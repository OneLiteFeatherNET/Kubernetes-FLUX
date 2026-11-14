"""Unit tests for sbom_sync. Run from this directory: python3 -m unittest discover -v"""

import base64
import dataclasses
import datetime as dt
import json
import re
import unittest
import urllib.parse
from collections import Counter

import sbom_sync as sync

NOW = 1_800_000_000.0
DAY = 86400
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DT_KEY = "odt_TESTKEY1_" + "k" * 32
HARBOR_USER = "robot$sbom-sync"
HARBOR_PASSWORD = "robot-password-value-42"
K8S_TOKEN = "k8s-service-account-token-value"
DT_HOST = "dt.test"
HARBOR_HOST = "harbor.test"
K8S_HOST = "k8s.test"
GROUP = sync.GROUP


def make_config(**overrides) -> sync.Config:
    values = dict(
        dt_url=f"http://{DT_HOST}",
        dt_api_key=DT_KEY,
        harbor_url=f"https://{HARBOR_HOST}",
        harbor_user=HARBOR_USER,
        harbor_password=HARBOR_PASSWORD,
        k8s_url=f"https://{K8S_HOST}",
        k8s_token=K8S_TOKEN,
        k8s_ca_file="/unused/ca.crt",
    )
    values.update(overrides)
    return sync.Config(**values)


def run(world: "World", **overrides) -> Counter:
    return sync.run_sync(make_config(**overrides), world, world, lambda: NOW)


def bom(*names: str) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "components": [{"type": "library", "name": name, "version": "1.0.0"} for name in names],
    }


def report(repository="onelitefeather/otis", server="ghcr.io", tag="1.4.0", digest=DIGEST_A,
           created="2027-01-14T10:00:00Z", components=("lib-a",)) -> dict:
    name = f"{repository.replace('/', '-')}-{tag or 'untagged'}-{digest[7:15]}"
    return {
        "metadata": {"name": name, "namespace": "monitoring", "creationTimestamp": created},
        "report": {
            "artifact": {"repository": repository, "tag": tag, "digest": digest},
            "registry": {"server": server},
            "components": bom(*components),
        },
    }


def harbor_artifact(digest=DIGEST_B, tags=("2.0.0",), status="Success",
                    push_time="2027-01-14T09:00:00.123456789Z") -> dict:
    artifact = {"digest": digest, "tags": [{"name": tag} for tag in tags], "push_time": push_time}
    if status is not None:
        artifact["sbom_overview"] = {"scan_status": status}
    return artifact


def spdx(*names: str, license_declared: str = "MIT") -> dict:
    packages = [
        {
            "SPDXID": f"SPDXRef-Package-{index}",
            "name": name,
            "versionInfo": "1.0.0",
            "licenseDeclared": license_declared,
            "externalRefs": [
                {"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl",
                 "referenceLocator": f"pkg:golang/{name}@1.0.0"},
            ],
        }
        for index, name in enumerate(names)
    ]
    return {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT", "packages": packages, "relationships": []}


def parse_multipart(body: bytes) -> dict[str, bytes]:
    fields: dict[str, bytes] = {}
    for part in body.split(b"--" + sync.BOUNDARY)[1:]:
        if part.startswith(b"--"):
            break
        head, _, value = part.lstrip(b"\r\n").partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        fields[name] = value[:-2] if value.endswith(b"\r\n") else value
    return fields


def page_of(items: list, query: dict) -> list:
    number = int(query.get("page", query.get("pageNumber", 1)))
    size = int(query.get("page_size", query.get("pageSize", 10)))
    return items[(number - 1) * size: number * size]


def reply(status: int, payload=None) -> sync.Response:
    return sync.Response(status, b"" if payload is None else json.dumps(payload).encode("utf-8"))


@dataclasses.dataclass
class Hit:
    host: str
    method: str
    path: str
    query: dict
    body: bytes | None


@dataclasses.dataclass
class Upload:
    name: str
    version: str
    tag: str
    is_latest: bool
    bom: bytes


class World:
    """In-memory Dependency-Track, Harbor and Kubernetes. Every call goes through request()."""

    def __init__(self):
        self.reports: list[dict] = []
        self.fresh_reports: dict[str, dict] = {}  # served by the single-report GET instead of the listing
        self.projects: dict[str, dict] = {}
        self.uploads: list[Upload] = []
        self.hits: list[Hit] = []
        self.harbor: dict[str, dict[str, list[dict]]] = {}
        self.harbor_sboms: dict[str, dict] = {}
        self.harbor_repo_listing_fails: set[str] = set()
        self.scan_replies: dict[str, int] = {}
        self.scan_requests: list[tuple] = []
        self.upload_fails_for: set[str] = set()
        self._count = 0

    def add_project(self, name, version, tags=(), props=None, is_latest=False) -> str:
        self._count += 1
        uuid = f"uuid-{self._count}"
        self.projects[uuid] = {"uuid": uuid, "name": name, "version": version, "isLatest": is_latest,
                               "tags": list(tags), "props": dict(props or {})}
        return uuid

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [(hit.method, hit.path) for hit in self.hits if hit.method != "GET"]

    def request(self, method, url, headers, body=None) -> sync.Response:
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.hits.append(Hit(parts.netloc, method, parts.path, query, body))
        if parts.netloc == K8S_HOST:
            return self._k8s(method, parts.path, query)
        if parts.netloc == DT_HOST:
            return self._dt(method, parts.path, query, headers, body)
        if parts.netloc == HARBOR_HOST:
            return self._harbor(method, parts.path, query, headers, body)
        raise AssertionError(f"unexpected host {parts.netloc}")

    def _k8s(self, method, path, query):
        single = re.fullmatch(r"/apis/aquasecurity\.github\.io/v1alpha1/namespaces/[^/]+/sbomreports/([^/]+)", path)
        if method == "GET" and single:
            name = single.group(1)
            if name in self.fresh_reports:
                return reply(200, self.fresh_reports[name])
            for item in self.reports:
                if item["metadata"]["name"] == name:
                    return reply(200, item)
            return reply(404)
        if method != "GET" or path != sync.K8S_SBOMREPORTS:
            return reply(404)
        limit = int(query.get("limit", 100))
        start = int(query.get("continue", 0))
        more = start + limit < len(self.reports)
        meta = {"continue": str(start + limit)} if more else {}
        return reply(200, {"items": self.reports[start:start + limit], "metadata": meta})

    def _find(self, name, version):
        for project in self.projects.values():
            if project["name"] == name and project["version"] == version:
                return project
        return None

    @staticmethod
    def _public(project: dict) -> dict:
        return {key: project[key] for key in ("uuid", "name", "version", "isLatest")}

    def _clear_latest(self, name: str) -> None:
        for project in self.projects.values():
            if project["name"] == name:
                project["isLatest"] = False

    def _dt(self, method, path, query, headers, body):
        if path == "/api/v1/project/lookup" and method == "GET":
            found = self._find(query["name"], query["version"])
            return reply(200, self._public(found)) if found else reply(404, {"title": "not found"})
        if path == "/api/v1/bom" and method == "POST":
            return self._upload(headers, body)
        if re.fullmatch(r"/api/v1/tag/[^/]+/project", path) and method == "GET":
            tag = urllib.parse.unquote(path.split("/")[4])
            tagged = [self._public(p) for p in self.projects.values() if tag in p["tags"]]
            return reply(200, page_of(tagged, query))
        match = re.fullmatch(r"/api/v1/project/([^/]+)/property", path)
        if match:
            return self._property(match.group(1), method, body)
        match = re.fullmatch(r"/api/v1/project/([^/]+)", path)
        if match and match.group(1) in self.projects:
            project = self.projects[match.group(1)]
            if method == "PATCH":
                self._clear_latest(project["name"])
                project["isLatest"] = True
                return reply(200, self._public(project))
            if method == "DELETE":
                del self.projects[project["uuid"]]
                return reply(204)
        return reply(404)

    def _property(self, uuid, method, body):
        project = self.projects[uuid]
        if method == "GET":
            return reply(200, [{"groupName": g, "propertyName": n, "propertyValue": v}
                               for (g, n), v in project["props"].items()])
        payload = json.loads(body)
        key = (payload["groupName"], payload["propertyName"])
        if method == "PUT":
            if key in project["props"]:
                return reply(409)
            project["props"][key] = payload["propertyValue"]
            return reply(201, payload)
        if method == "POST":
            if key not in project["props"]:
                return reply(404)
            project["props"][key] = payload["propertyValue"]
            return reply(200, payload)
        return reply(405)

    def _upload(self, headers, body):
        fields = parse_multipart(body)
        name = fields["projectName"].decode()
        version = fields["projectVersion"].decode()
        tag = fields["projectTags"].decode()
        is_latest = fields["isLatest"] == b"true"
        if name in self.upload_fails_for:
            return reply(500, {"detail": f"upload rejected for key {headers['X-Api-Key']}"})
        self.uploads.append(Upload(name, version, tag, is_latest, fields["bom"]))
        if self._find(name, version) is None:
            if is_latest:
                self._clear_latest(name)
            self.add_project(name, version, tags=[tag], is_latest=is_latest)
        return reply(200, {"token": "accepted"})

    def _harbor(self, method, path, query, headers, body):
        if path == "/api/v2.0/projects" and method == "GET":
            return reply(200, [{"name": name} for name in page_of(sorted(self.harbor), query)])
        match = re.fullmatch(r"/api/v2\.0/projects/([^/]+)/repositories", path)
        if match and method == "GET":
            project = urllib.parse.unquote(match.group(1))
            if project in self.harbor_repo_listing_fails:
                return reply(500, {"detail": f"Authorization: {headers['Authorization']}"})
            names = [{"name": f"{project}/{repo}"} for repo in self.harbor.get(project, {})]
            return reply(200, page_of(names, query))
        match = re.fullmatch(
            r"/api/v2\.0/projects/([^/]+)/repositories/([^/]+)/artifacts(?:/([^/]+)/(additions/sbom|scan))?", path)
        if not match:
            return reply(404)
        project = urllib.parse.unquote(match.group(1))
        repo = urllib.parse.unquote(urllib.parse.unquote(match.group(2)))
        if match.group(3) is None and method == "GET":
            return reply(200, page_of(self.harbor.get(project, {}).get(repo, []), query))
        reference = urllib.parse.unquote(match.group(3) or "")
        if match.group(4) == "additions/sbom" and method == "GET":
            if reference in self.harbor_sboms:
                return reply(200, self.harbor_sboms[reference])
            return reply(404)
        if match.group(4) == "scan" and method == "POST":
            self.scan_requests.append((project, repo, reference, json.loads(body)))
            return reply(self.scan_replies.get(reference, 202))
        return reply(404)


class ConverterTests(unittest.TestCase):
    def test_spdx_packages_become_components_with_purl_version_and_license_id(self):
        cdx = sync.spdx_to_cyclonedx(spdx("left-pad"))

        self.assertEqual(("CycloneDX", "1.7"), (cdx["bomFormat"], cdx["specVersion"]))
        self.assertEqual(
            [{"type": "library", "bom-ref": "SPDXRef-Package-0", "name": "left-pad", "version": "1.0.0",
              "purl": "pkg:golang/left-pad@1.0.0", "licenses": [{"license": {"id": "MIT"}}]}],
            cdx["components"],
        )

    def test_spdx_license_expression_is_kept_as_expression(self):
        cdx = sync.spdx_to_cyclonedx(spdx("dual", license_declared="Apache-2.0 OR MIT"))

        self.assertEqual([{"expression": "Apache-2.0 OR MIT"}], cdx["components"][0]["licenses"])

    def test_spdx_no_assertion_license_is_omitted(self):
        cdx = sync.spdx_to_cyclonedx(spdx("unknown", license_declared="NOASSERTION"))

        self.assertNotIn("licenses", cdx["components"][0])

    def test_spdx_depends_on_relationship_becomes_dependency(self):
        doc = spdx("app", "lib")
        doc["relationships"] = [{"spdxElementId": "SPDXRef-Package-0", "relatedSpdxElement": "SPDXRef-Package-1",
                                 "relationshipType": "DEPENDS_ON"}]

        cdx = sync.spdx_to_cyclonedx(doc)

        self.assertEqual([{"ref": "SPDXRef-Package-0", "dependsOn": ["SPDXRef-Package-1"]}], cdx["dependencies"])

    def test_spdx_described_package_becomes_metadata_component(self):
        doc = spdx("image", "lib")
        doc["relationships"] = [{"spdxElementId": "SPDXRef-DOCUMENT", "relatedSpdxElement": "SPDXRef-Package-0",
                                 "relationshipType": "DESCRIBES"}]

        cdx = sync.spdx_to_cyclonedx(doc)

        self.assertEqual("image", cdx["metadata"]["component"]["name"])
        self.assertEqual(["lib"], [component["name"] for component in cdx["components"]])

    def test_spdx_document_without_packages_is_rejected(self):
        with self.assertRaises(sync.ItemError):
            sync.spdx_to_cyclonedx({"spdxVersion": "SPDX-2.3"})

    def test_harbor_push_time_with_nanoseconds_is_parsed(self):
        parsed = sync.parse_time("2027-01-14T09:00:00.123456789Z")

        self.assertEqual(dt.datetime(2027, 1, 14, 9, 0, 0, 123456, tzinfo=dt.timezone.utc), parsed)


class DedupeTests(unittest.TestCase):
    def test_three_replicas_of_one_image_upload_once(self):
        world = World()
        world.reports = [report(digest=DIGEST_A) for _ in range(3)]

        counts = run(world)

        self.assertEqual(1, len(world.uploads))
        self.assertEqual(("ghcr.io/onelitefeather/otis", "1.4.0"), (world.uploads[0].name, world.uploads[0].version))
        self.assertEqual(1, counts["uploaded"])

    def test_unchanged_digest_is_not_uploaded_again(self):
        world = World()
        world.reports = [report(digest=DIGEST_A)]
        uuid = world.add_project("ghcr.io/onelitefeather/otis", "1.4.0", tags=[sync.TAG_CLUSTER], is_latest=True,
                                 props={(GROUP, sync.DIGEST): DIGEST_A, (GROUP, sync.LAST_SEEN): "1"})

        counts = run(world)

        self.assertEqual([], world.uploads)
        self.assertEqual(1, counts["skipped"])
        self.assertEqual(str(int(NOW)), world.projects[uuid]["props"][(GROUP, sync.LAST_SEEN)])

    def test_changed_digest_under_same_tag_is_uploaded_and_recorded(self):
        world = World()
        world.reports = [report(digest=DIGEST_B)]
        uuid = world.add_project("ghcr.io/onelitefeather/otis", "1.4.0", tags=[sync.TAG_CLUSTER], is_latest=True,
                                 props={(GROUP, sync.DIGEST): DIGEST_A, (GROUP, sync.LAST_SEEN): "1"})

        run(world)

        self.assertEqual(1, len(world.uploads))
        self.assertEqual(DIGEST_B, world.projects[uuid]["props"][(GROUP, sync.DIGEST)])

    def test_untagged_report_uses_digest_as_version(self):
        world = World()
        world.reports = [report(tag="", digest=DIGEST_A)]

        run(world)

        self.assertEqual(DIGEST_A, world.uploads[0].version)

    def test_newest_report_wins_when_one_tag_points_at_two_digests(self):
        world = World()
        world.reports = [
            report(digest=DIGEST_A, created="2027-01-14T10:00:00Z", components=("old-lib",)),
            report(digest=DIGEST_B, created="2027-01-14T11:00:00Z", components=("new-lib",)),
        ]

        run(world)

        self.assertEqual(1, len(world.uploads))
        self.assertEqual(["new-lib"], [c["name"] for c in json.loads(world.uploads[0].bom)["components"]])


class PaginationTests(unittest.TestCase):
    def test_sbom_reports_follow_continue_token_until_it_is_empty(self):
        world = World()
        world.reports = [report(repository=f"team/app{index}", digest=DIGEST_A, tag="") for index in range(250)]

        run(world, page_size_k8s=100, max_uploads=1)

        listing = [hit.query for hit in world.hits if hit.path == sync.K8S_SBOMREPORTS]
        self.assertEqual([{"limit": "100"}, {"limit": "100", "continue": "100"}, {"limit": "100", "continue": "200"}],
                         listing)

    def test_tagged_projects_request_pages_until_a_short_page(self):
        world = World()
        for index in range(250):
            world.add_project(f"ghcr.io/x/app{index}", "1.0", tags=[sync.TAG_CLUSTER],
                              props={(GROUP, sync.LAST_SEEN): str(int(NOW))})

        run(world)

        pages = [hit.query for hit in world.hits if hit.path.endswith("/tag/sbom-sync-cluster/project")]
        self.assertEqual([{"pageNumber": "1", "pageSize": "100"}, {"pageNumber": "2", "pageSize": "100"},
                          {"pageNumber": "3", "pageSize": "100"}], pages)

    def test_harbor_artifacts_request_next_page_after_a_full_page(self):
        world = World()
        world.harbor = {"library": {"app": [harbor_artifact(digest=f"sha256:{i:064x}", status=None, tags=())
                                            for i in range(100)]}}

        run(world)

        pages = [hit.query for hit in world.hits if hit.path.endswith("/artifacts")]
        self.assertEqual(["1", "2"], [query["page"] for query in pages])
        self.assertEqual({"100"}, {query["page_size"] for query in pages})
        self.assertEqual({"true"}, {query["with_sbom_overview"] for query in pages})


class RetentionTests(unittest.TestCase):
    def test_owned_project_past_grace_is_deleted(self):
        world = World()
        uuid = world.add_project("ghcr.io/x/old", "1.3.0", tags=[sync.TAG_CLUSTER],
                                 props={(GROUP, sync.LAST_SEEN): str(int(NOW - 31 * DAY))})

        counts = run(world)

        self.assertIn(("DELETE", f"/api/v1/project/{uuid}"), world.writes)
        self.assertEqual(1, counts["deleted"])

    def test_owned_project_exactly_at_grace_is_deleted(self):
        world = World()
        uuid = world.add_project("ghcr.io/x/old", "1.3.0", tags=[sync.TAG_CLUSTER],
                                 props={(GROUP, sync.LAST_SEEN): str(int(NOW - 30 * DAY))})

        run(world)

        self.assertIn(("DELETE", f"/api/v1/project/{uuid}"), world.writes)

    def test_owned_project_within_grace_is_kept(self):
        world = World()
        uuid = world.add_project("ghcr.io/x/recent", "1.3.0", tags=[sync.TAG_CLUSTER],
                                 props={(GROUP, sync.LAST_SEEN): str(int(NOW - 29 * DAY))})

        counts = run(world)

        self.assertNotIn(("DELETE", f"/api/v1/project/{uuid}"), world.writes)
        self.assertEqual(0, counts["deleted"])

    def test_project_without_sync_tag_is_never_deleted(self):
        world = World()
        uuid = world.add_project("manual-tool", "0.1")

        run(world)

        self.assertFalse(any(method == "DELETE" for method, _ in world.writes))
        self.assertIn(uuid, world.projects)

    def test_owned_project_without_last_seen_is_kept(self):
        world = World()
        uuid = world.add_project("ghcr.io/x/unknown", "1.0", tags=[sync.TAG_CLUSTER])

        run(world)

        self.assertIn(uuid, world.projects)

    def test_incomplete_harbor_listing_blocks_only_harbor_deletion(self):
        world = World()
        world.harbor = {"library": {"app": []}}
        world.harbor_repo_listing_fails.add("library")
        harbor_stale = world.add_project(f"{HARBOR_HOST}/other/app", "1.0", tags=[sync.TAG_HARBOR],
                                         props={(GROUP, sync.LAST_SEEN): str(int(NOW - 60 * DAY))})
        cluster_stale = world.add_project("ghcr.io/x/old", "1.0", tags=[sync.TAG_CLUSTER],
                                          props={(GROUP, sync.LAST_SEEN): str(int(NOW - 60 * DAY))})

        counts = run(world)

        self.assertIn(harbor_stale, world.projects)
        self.assertNotIn(cluster_stale, world.projects)
        self.assertEqual(1, counts["failed"])


class HarborTests(unittest.TestCase):
    def test_artifact_with_sbom_is_uploaded_under_harbor_tag(self):
        world = World()
        world.harbor = {"library": {"app": [harbor_artifact(digest=DIGEST_B, tags=("2.0.0",))]}}
        world.harbor_sboms[DIGEST_B] = spdx("left-pad")

        run(world)

        upload = world.uploads[0]
        self.assertEqual((f"{HARBOR_HOST}/library/app", "2.0.0", sync.TAG_HARBOR),
                         (upload.name, upload.version, upload.tag))
        self.assertEqual(["left-pad"], [c["name"] for c in json.loads(upload.bom)["components"]])

    def test_artifact_without_sbom_requests_generation_and_uploads_nothing(self):
        world = World()
        world.harbor = {"library": {"app": [harbor_artifact(digest=DIGEST_B, tags=("2.0.1",), status=None)]}}

        counts = run(world)

        self.assertEqual([], world.uploads)
        self.assertEqual([("library", "app", DIGEST_B, {"scan_type": "sbom"})], world.scan_requests)
        self.assertEqual(1, counts["skipped"])

    def test_non_scannable_artifact_answering_422_is_skipped(self):
        world = World()
        world.harbor = {"library": {"index": [harbor_artifact(digest=DIGEST_B, tags=(), status=None)]}}
        world.scan_replies[DIGEST_B] = 422

        counts = run(world)

        self.assertEqual(1, counts["skipped"])
        self.assertEqual(0, counts["failed"])

    def test_artifact_with_sbom_in_progress_triggers_no_scan(self):
        world = World()
        world.harbor = {"library": {"app": [harbor_artifact(digest=DIGEST_B, tags=("2.0.2",), status="Running")]}}

        run(world)

        self.assertEqual([], world.scan_requests)
        self.assertEqual([], world.uploads)

    def test_cluster_entry_wins_over_harbor_entry_with_same_name_and_version(self):
        world = World()
        world.reports = [report(server=HARBOR_HOST, repository="library/app", tag="2.0.0", digest=DIGEST_A,
                                components=("cluster-lib",))]
        world.harbor = {"library": {"app": [harbor_artifact(digest=DIGEST_B, tags=("2.0.0",))]}}
        world.harbor_sboms[DIGEST_B] = spdx("harbor-lib")

        counts = run(world)

        self.assertEqual([sync.TAG_CLUSTER], [upload.tag for upload in world.uploads])
        self.assertEqual(1, counts["skipped"])


class UploadTests(unittest.TestCase):
    def test_oversize_bom_is_failed_and_not_uploaded(self):
        world = World()
        world.reports = [report(components=("a", "b", "c"))]

        counts = run(world, max_bom_bytes=50)

        self.assertEqual([], world.uploads)
        self.assertEqual(1, counts["failed"])

    def test_failed_upload_leaves_its_project_unchanged_and_other_items_processed(self):
        world = World()
        world.reports = [report(repository="x/bad", digest=DIGEST_B), report(repository="x/good", digest=DIGEST_B)]
        world.upload_fails_for.add("ghcr.io/x/bad")
        bad = world.add_project("ghcr.io/x/bad", "1.4.0", tags=[sync.TAG_CLUSTER],
                                props={(GROUP, sync.DIGEST): DIGEST_A, (GROUP, sync.LAST_SEEN): "1"})

        counts = run(world)

        self.assertEqual(["ghcr.io/x/good"], [upload.name for upload in world.uploads])
        self.assertEqual(DIGEST_A, world.projects[bad]["props"][(GROUP, sync.DIGEST)])
        self.assertEqual((1, 1), (counts["uploaded"], counts["failed"]))

    def test_multipart_upload_carries_project_fields_and_autocreate(self):
        world = World()
        world.reports = [report(tag="1.4.0", components=("lib-a",))]

        run(world)

        fields = parse_multipart(next(hit.body for hit in world.hits if hit.method == "POST"))
        self.assertEqual(b"true", fields["autoCreate"])
        self.assertEqual(b"true", fields["isLatest"])
        self.assertEqual(b"sbom-sync-cluster", fields["projectTags"])
        self.assertEqual(["lib-a"], [c["name"] for c in json.loads(fields["bom"])["components"]])

    def test_report_replaced_after_listing_is_failed_and_not_uploaded(self):
        world = World()
        listed = report(digest=DIGEST_A)
        world.reports = [listed]
        world.fresh_reports[listed["metadata"]["name"]] = report(digest=DIGEST_B)

        counts = run(world)

        self.assertEqual([], world.uploads)
        self.assertEqual(1, counts["failed"])

    def test_newest_existing_version_is_patched_to_latest(self):
        world = World()
        world.reports = [
            report(tag="1.4.0", digest=DIGEST_A, created="2027-01-14T10:00:00Z"),
            report(tag="1.5.0", digest=DIGEST_B, created="2027-01-14T11:00:00Z"),
        ]
        old = world.add_project("ghcr.io/onelitefeather/otis", "1.4.0", tags=[sync.TAG_CLUSTER], is_latest=True,
                                props={(GROUP, sync.DIGEST): DIGEST_A, (GROUP, sync.LAST_SEEN): "1"})
        new = world.add_project("ghcr.io/onelitefeather/otis", "1.5.0", tags=[sync.TAG_CLUSTER], is_latest=False,
                                props={(GROUP, sync.DIGEST): DIGEST_B, (GROUP, sync.LAST_SEEN): "1"})

        run(world)

        self.assertIn(("PATCH", f"/api/v1/project/{new}"), world.writes)
        self.assertNotIn(("PATCH", f"/api/v1/project/{old}"), world.writes)
        self.assertTrue(world.projects[new]["isLatest"])

    def test_upload_budget_defers_items_beyond_limit(self):
        world = World()
        world.reports = [report(repository=f"team/app{index}", digest=DIGEST_A) for index in range(3)]

        counts = run(world, max_uploads=2)

        self.assertEqual((2, 1), (counts["uploaded"], counts["deferred"]))


class SafetyTests(unittest.TestCase):
    def test_dry_run_sends_no_write_request(self):
        world = World()
        world.reports = [report(repository="x/new", digest=DIGEST_A), report(repository="x/known", digest=DIGEST_B)]
        world.add_project("ghcr.io/x/known", "1.4.0", tags=[sync.TAG_CLUSTER], is_latest=False,
                          props={(GROUP, sync.DIGEST): DIGEST_A, (GROUP, sync.LAST_SEEN): "1"})
        world.add_project("ghcr.io/x/stale", "0.9", tags=[sync.TAG_CLUSTER],
                          props={(GROUP, sync.LAST_SEEN): str(int(NOW - 90 * DAY))})
        world.harbor = {"library": {"app": [harbor_artifact(digest=DIGEST_B, tags=("2.0.0",)),
                                            harbor_artifact(digest="sha256:" + "d" * 64, tags=("3.0.0",),
                                                            status=None)]}}
        world.harbor_sboms[DIGEST_B] = spdx("left-pad")

        counts = run(world, dry_run=True)

        self.assertEqual([], world.writes)
        self.assertEqual([], world.scan_requests)
        self.assertEqual(1, counts["deleted"])

    def test_secrets_never_appear_in_log_output(self):
        world = World()
        world.reports = [report(digest=DIGEST_A)]
        world.upload_fails_for.add("ghcr.io/onelitefeather/otis")
        world.harbor_repo_listing_fails.add("library")
        basic = base64.b64encode(f"{HARBOR_USER}:{HARBOR_PASSWORD}".encode()).decode()

        with self.assertLogs("sbom-sync", level="INFO") as captured:
            run(world)

        output = "\n".join(captured.output)
        self.assertIn("failed", output)
        for secret in (DT_KEY, HARBOR_PASSWORD, basic):
            self.assertNotIn(secret, output)

    def test_summary_line_reports_counts_and_dry_run_flag(self):
        world = World()
        world.reports = [report(digest=DIGEST_A)]

        with self.assertLogs("sbom-sync", level="INFO") as captured:
            run(world)

        self.assertIn("sbom-sync-summary uploaded=1 skipped=0 deleted=0 deferred=0 failed=0 dry_run=false",
                      "\n".join(captured.output))


class ConfigTests(unittest.TestCase):
    ENV = {
        "DT_URL": f"http://{DT_HOST}",
        "DT_API_KEY_FILE": "/run/secrets/dt-key",
        "HARBOR_URL": f"https://{HARBOR_HOST}",
        "HARBOR_USERNAME_FILE": "/run/secrets/harbor-user",
        "HARBOR_PASSWORD_FILE": "/run/secrets/harbor-pass",
        "K8S_TOKEN_FILE": "/run/secrets/k8s-token",
    }
    FILES = {
        "/run/secrets/dt-key": DT_KEY,
        "/run/secrets/harbor-user": HARBOR_USER,
        "/run/secrets/harbor-pass": HARBOR_PASSWORD,
        "/run/secrets/k8s-token": K8S_TOKEN,
    }

    def test_api_key_is_read_from_the_file_named_by_dt_api_key_file(self):
        read: list[str] = []

        def read_file(path: str) -> str:
            read.append(path)
            return self.FILES[path]

        config = sync.load_config(self.ENV, read_file)

        self.assertEqual(DT_KEY, config.dt_api_key)
        self.assertIn("/run/secrets/dt-key", read)

    def test_dry_run_environment_flag_enables_dry_run(self):
        config = sync.load_config({**self.ENV, "DRY_RUN": "true"}, self.FILES.__getitem__)

        self.assertTrue(config.dry_run)

    def test_non_positive_page_size_is_a_configuration_error(self):
        with self.assertRaises(sync.ConfigError):
            sync.load_config({**self.ENV, "DT_PAGE_SIZE": "0"}, self.FILES.__getitem__)


if __name__ == "__main__":
    unittest.main()
