#!/usr/bin/env python3
"""Sync SBOMs into Dependency-Track 5 from Trivy-Operator SbomReports and Harbor artifacts.

Standard library only. Rules: openspec/changes/add-dependency-track-sbom-sync.
"""

import argparse
import base64
import dataclasses
import datetime as dt
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from typing import Any

log = logging.getLogger("sbom-sync")

GROUP = "sbom-sync"
DIGEST = "digest"
LAST_SEEN = "last-seen"
TAG_CLUSTER = "sbom-sync-cluster"
TAG_HARBOR = "sbom-sync-harbor"
MAX_BOM_BYTES = 100 * 1024 * 1024  # Dependency-Track dt.bom-upload.max-size-bytes
IN_PROGRESS = frozenset({"queued", "pending", "scheduled", "running"})
NO_ASSERTION = frozenset({"", "NOASSERTION", "NONE"})
BOUNDARY = b"sbom-sync-boundary-7f3c2a9e"
JSON = "application/json"
K8S_SBOMREPORTS = "/apis/aquasecurity.github.io/v1alpha1/sbomreports"
CYCLONEDX_SCHEMA = "https://cyclonedx.org/schema/bom-1.7.schema.json"
SPDX_EXPRESSION = re.compile(r"[()]|\s(?:AND|OR|WITH)\s")
SPDX_ID = re.compile(r"[A-Za-z0-9.+-]+")
FRACTION = re.compile(r"(\.\d{6})\d+")
MAX_PAGES = 10_000
TRUTHY = frozenset({"1", "true", "yes", "on"})

Clock = Callable[[], float]


class ConfigError(Exception):
    """Bad configuration. The message names a variable or file, never a value."""


class ApiError(Exception):
    """A request failed. The message holds method, path and a redacted body excerpt."""


class ItemError(Exception):
    """One item failed. It is recorded and the run continues."""


class Skip(Exception):
    """One item needs no action in this run."""


@dataclasses.dataclass
class Response:
    status: int
    body: bytes


class UrllibTransport:
    def __init__(self, ssl_context: ssl.SSLContext | None = None, timeout: float = 120.0):
        self.ssl_context = ssl_context
        self.timeout = timeout

    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> Response:
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.ssl_context) as reply:
                return Response(reply.status, reply.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read())


class Redactor:
    def __init__(self, *values: str):
        self.values = [value for value in values if value]

    def add(self, value: str) -> None:
        if value:
            self.values.append(value)

    def __call__(self, text: str) -> str:
        for value in self.values:
            text = text.replace(value, "***")
        return text


def _json(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def paginate(fetch: Callable[[int], list], page_size: int) -> list:
    """Page numbers start at 1 and stop at the first page shorter than page_size."""
    items: list = []
    for page in range(1, MAX_PAGES + 1):
        batch = fetch(page)
        items.extend(batch)
        if len(batch) < page_size:
            return items
    raise ApiError("pagination did not end")


class Api:
    def __init__(self, http, base_url: str, headers: dict[str, str], redact: Redactor):
        self.http = http
        self.base = base_url.rstrip("/")
        self.headers = {"Accept": JSON, **headers}
        self.redact = redact

    def send(self, method, path, query=None, body=None, content_type=None, ok=(200,)) -> Response:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = dict(self.headers)
        if content_type:
            headers["Content-Type"] = content_type
        reply = self.http.request(method, url, headers, body)
        if reply.status not in ok:
            excerpt = self.redact(reply.body[:200].decode("utf-8", "replace"))
            raise ApiError(f"{method} {path} returned HTTP {reply.status}: {excerpt}")
        return reply

    def get_json(self, path, query=None) -> Any:
        return json.loads(self.send("GET", path, query=query).body or b"null")


def multipart(fields: dict[str, str], field: str, payload: bytes) -> bytes:
    sep = b"--" + BOUNDARY + b"\r\n"
    body = b""
    for key, value in fields.items():
        body += sep + f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode() + value.encode("utf-8") + b"\r\n"
    body += (sep + f'Content-Disposition: form-data; name="{field}"; filename="bom.json"\r\n'.encode()
             + b"Content-Type: application/json\r\n\r\n" + payload + b"\r\n")
    return body + b"--" + BOUNDARY + b"--\r\n"


class DependencyTrack(Api):
    def __init__(self, http, base_url: str, api_key: str, page_size: int, redact: Redactor):
        super().__init__(http, base_url, {"X-Api-Key": api_key}, redact)
        self.page_size = page_size

    def lookup(self, name: str, version: str) -> dict | None:
        reply = self.send("GET", "/api/v1/project/lookup", query={"name": name, "version": version}, ok=(200, 404))
        return json.loads(reply.body) if reply.status == 200 else None

    def properties(self, uuid: str) -> dict[tuple[str, str], str]:
        items = self.get_json(f"/api/v1/project/{uuid}/property")
        return {(p["groupName"], p["propertyName"]): p.get("propertyValue") or "" for p in items}

    def put_property(self, uuid: str, name: str, value: str, exists: bool) -> None:
        body = {"groupName": GROUP, "propertyName": name, "propertyValue": value}
        path = f"/api/v1/project/{uuid}/property"
        if exists:
            self.send("POST", path, body=_json(body), content_type=JSON, ok=(200,))
        else:
            self.send("PUT", path, body=_json({**body, "propertyType": "STRING"}), content_type=JSON, ok=(201,))

    def tagged(self, tag: str) -> list[dict]:
        path = f"/api/v1/tag/{_quote(tag)}/project"
        return paginate(
            lambda page: self.get_json(path, query={"pageNumber": page, "pageSize": self.page_size}),
            self.page_size,
        )

    def upload(self, name: str, version: str, tag: str, is_latest: bool, bom: bytes) -> None:
        if BOUNDARY in bom:
            raise ItemError("BOM contains the multipart boundary")
        fields = {
            "autoCreate": "true",
            "projectName": name,
            "projectVersion": version,
            "isLatest": "true" if is_latest else "false",
            "projectTags": tag,
        }
        self.send(
            "POST", "/api/v1/bom", body=multipart(fields, "bom", bom),
            content_type=f"multipart/form-data; boundary={BOUNDARY.decode()}", ok=(200,),
        )

    def set_latest(self, uuid: str) -> None:
        self.send("PATCH", f"/api/v1/project/{uuid}", body=_json({"isLatest": True}), content_type=JSON, ok=(200,))

    def delete(self, uuid: str) -> None:
        self.send("DELETE", f"/api/v1/project/{uuid}", ok=(200, 204))


def _project_path(project: str) -> str:
    return f"/api/v2.0/projects/{_quote(project)}"


def _repo_path(project: str, repo: str) -> str:
    # Harbor wants the repository part of the path encoded twice.
    return f"{_project_path(project)}/repositories/{_quote(_quote(repo))}"


class Harbor(Api):
    def __init__(self, http, base_url: str, user: str, password: str, page_size: int, redact: Redactor):
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        redact.add(token)
        super().__init__(http, base_url, {"Authorization": f"Basic {token}"}, redact)
        self.page_size = page_size

    def projects(self) -> list[str]:
        items = paginate(
            lambda page: self.get_json("/api/v2.0/projects", query={"page": page, "page_size": self.page_size}),
            self.page_size,
        )
        return [item["name"] for item in items]

    def repositories(self, project: str) -> list[str]:
        items = paginate(
            lambda page: self.get_json(
                f"{_project_path(project)}/repositories", query={"page": page, "page_size": self.page_size}
            ),
            self.page_size,
        )
        prefix = project + "/"
        return [item["name"][len(prefix):] for item in items if item["name"].startswith(prefix)]

    def artifacts(self, project: str, repo: str) -> list[dict]:
        path = f"{_repo_path(project, repo)}/artifacts"
        return paginate(
            lambda page: self.get_json(
                path, query={"page": page, "page_size": self.page_size, "with_sbom_overview": "true"}
            ),
            self.page_size,
        )

    def sbom(self, project: str, repo: str, digest: str) -> Any:
        doc = self.get_json(f"{_repo_path(project, repo)}/artifacts/{_quote(digest)}/additions/sbom")
        if isinstance(doc, dict) and isinstance(doc.get("sbom"), dict):
            return doc["sbom"]
        return doc

    def request_sbom(self, project: str, repo: str, digest: str) -> int:
        path = f"{_repo_path(project, repo)}/artifacts/{_quote(digest)}/scan"
        reply = self.send("POST", path, body=_json({"scan_type": "sbom"}), content_type=JSON, ok=(202, 422))
        return reply.status


class Kubernetes(Api):
    def __init__(self, http, base_url: str, token: str, page_size: int, redact: Redactor):
        super().__init__(http, base_url, {"Authorization": f"Bearer {token}"}, redact)
        self.page_size = page_size

    def sbom_report_pages(self) -> Iterator[list[dict]]:
        """Yields one page at a time so full BOMs are never held for the whole listing."""
        token = None
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            query: dict[str, Any] = {"limit": self.page_size}
            if token:
                query["continue"] = token
            doc = self.get_json(K8S_SBOMREPORTS, query=query)
            yield doc.get("items") or []
            token = (doc.get("metadata") or {}).get("continue")
            if not token:
                return
            if token in seen:
                raise ApiError("sbomreports listing repeated a continue token")
            seen.add(token)
        raise ApiError("sbomreports listing did not end")

    def sbom_report(self, namespace: str, name: str) -> dict:
        return self.get_json(f"/apis/aquasecurity.github.io/v1alpha1/namespaces/{_quote(namespace)}/sbomreports/{_quote(name)}")


@dataclasses.dataclass
class Entry:
    name: str
    version: str
    digest: str
    created: dt.datetime | None
    load_bom: Callable[[], dict] | None = None  # None: no SBOM available this run
    scan: tuple[str, str, str] | None = None  # Harbor (project, repository, digest) to generate an SBOM for


@dataclasses.dataclass
class Source:
    tag: str
    entries: list[Entry] = dataclasses.field(default_factory=list)
    present: set[tuple[str, str]] = dataclasses.field(default_factory=set)
    complete: bool = False
    failed: int = 0
    request_scan: Callable[[str, str, str], int] | None = None


def parse_time(value: Any) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = FRACTION.sub(r"\1", value.strip())
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _epoch(created: dt.datetime | None) -> float:
    return created.timestamp() if created else float("-inf")


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _license(pkg: dict) -> list[dict]:
    value = ""
    for key in ("licenseDeclared", "licenseConcluded"):
        value = str(pkg.get(key) or "").strip()
        if value not in NO_ASSERTION:
            break
    else:
        return []
    if SPDX_EXPRESSION.search(value):
        return [{"expression": value}]
    if SPDX_ID.fullmatch(value) and not value.startswith("LicenseRef-"):
        return [{"license": {"id": value}}]
    return [{"license": {"name": value}}]


def _spdx_component(pkg: dict, spdx_id: str) -> dict:
    component: dict[str, Any] = {"type": "library", "bom-ref": spdx_id, "name": str(pkg.get("name") or spdx_id)}
    version = pkg.get("versionInfo")
    if version and version not in NO_ASSERTION:
        component["version"] = str(version)
    for ref in pkg.get("externalRefs") or []:
        if isinstance(ref, dict) and ref.get("referenceType") == "purl" and ref.get("referenceLocator"):
            component["purl"] = ref["referenceLocator"]
            break
    licenses = _license(pkg)
    if licenses:
        component["licenses"] = licenses
    return component


def spdx_to_cyclonedx(doc: Any) -> dict:
    """Convert an SPDX 2.x JSON document to CycloneDX 1.7 (components, licenses, dependencies)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("packages"), list):
        raise ItemError("SBOM is not an SPDX document with packages")
    relationships = [rel for rel in doc.get("relationships") or [] if isinstance(rel, dict)]
    described = set(doc.get("documentDescribes") or []) | {
        rel.get("relatedSpdxElement")
        for rel in relationships
        if rel.get("relationshipType") == "DESCRIBES" and rel.get("spdxElementId") == "SPDXRef-DOCUMENT"
    }
    components: list[dict] = []
    main: dict | None = None
    for pkg in doc["packages"]:
        spdx_id = str(pkg.get("SPDXID") or "")
        if not spdx_id:
            raise ItemError("SPDX package without SPDXID")
        component = _spdx_component(pkg, spdx_id)
        if main is None and spdx_id in described:
            main = {**component, "type": "container"}
        else:
            components.append(component)
    refs = {c["bom-ref"] for c in components} | ({main["bom-ref"]} if main else set())
    depends: dict[str, list[str]] = {}
    for rel in relationships:
        source, target = rel.get("spdxElementId"), rel.get("relatedSpdxElement")
        if rel.get("relationshipType") == "DEPENDS_ON" and source in refs and target in refs:
            targets = depends.setdefault(source, [])
            if target not in targets:
                targets.append(target)
    bom: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "$schema": CYCLONEDX_SCHEMA,
        "version": 1,
        "components": components,
        "dependencies": [{"ref": ref, "dependsOn": targets} for ref, targets in depends.items()],
    }
    if main:
        bom["metadata"] = {"component": main}
    return bom


def encode_bom(bom: dict, limit: int) -> bytes:
    if not bom.get("components"):
        raise Skip("SBOM lists no components")
    raw = json.dumps(bom, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > limit:
        raise ItemError(f"BOM is {len(raw)} bytes, above the {limit}-byte upload limit; not truncated")
    return raw


def cluster_entry(report: dict, fetch: Callable[[str, str], dict]) -> Entry:
    meta = report["metadata"]
    body = report["report"]
    artifact = body["artifact"]
    server = str(body["registry"]["server"] or "")
    repository = str(artifact["repository"] or "")
    digest = str(artifact["digest"] or "")
    if not (server and repository and digest.startswith("sha256:")):
        raise ValueError("incomplete artifact identity")
    tag = str(artifact.get("tag") or "")
    bom = body.get("components")
    has_sbom = isinstance(bom, dict) and bool(bom.get("components"))
    namespace, name = str(meta["namespace"]), str(meta["name"])

    def load() -> dict:
        fresh = fetch(namespace, name)["report"]
        if fresh["artifact"]["digest"] != digest:
            raise ItemError("SbomReport changed since it was listed; the next run picks it up")
        return fresh["components"]

    return Entry(
        name=f"{server}/{repository}",
        version=tag or digest,
        digest=digest,
        created=parse_time(meta.get("creationTimestamp")),
        load_bom=load if has_sbom else None,
    )


def harbor_entries(host: str, project: str, repo: str, artifact: dict, fetch_sbom: Callable) -> list[Entry]:
    digest = str(artifact["digest"])
    overview = artifact.get("sbom_overview") or {}
    status = str(overview.get("scan_status") or "").lower()
    available = status == "success"
    scan = None if available or status in IN_PROGRESS else (project, repo, digest)
    name = f"{host}/{project}/{repo}"
    versions = sorted(tag["name"] for tag in artifact.get("tags") or []) or [digest]
    created = parse_time(artifact.get("push_time"))
    entries = []
    for version in versions:
        load = None
        if available:
            load = lambda p=project, r=repo, d=digest: spdx_to_cyclonedx(fetch_sbom(p, r, d))
        entries.append(Entry(name, version, digest, created, load, scan))
    return entries


def _log_failure(label: str, exc: Exception, redact: Redactor) -> None:
    log.error("failed %s: %s", label, redact(str(exc) or type(exc).__name__))


def load_cluster(k8s: Kubernetes, redact: Redactor) -> Source:
    source = Source(TAG_CLUSTER)
    best: dict[tuple[str, str], tuple[tuple, Entry]] = {}
    for page in k8s.sbom_report_pages():
        for report in page:
            try:
                entry = cluster_entry(report, k8s.sbom_report)
            except (KeyError, TypeError, ValueError, AttributeError):
                source.failed += 1
                log.error("failed SbomReport: identity missing or malformed")
                continue
            key = (entry.name, entry.version)
            rank = (_epoch(entry.created), entry.digest)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, entry)
    source.entries = [entry for _, entry in best.values()]
    source.present = {(entry.name, entry.version) for entry in source.entries}
    source.complete = True
    return source


def load_harbor(harbor: Harbor, host: str, redact: Redactor) -> Source:
    source = Source(TAG_HARBOR, request_scan=harbor.request_sbom)
    complete = True
    try:
        projects = harbor.projects()
    except (ApiError, OSError, ValueError) as exc:
        _log_failure("harbor project listing", exc, redact)
        source.failed += 1
        return source
    for project in projects:
        try:
            repos = harbor.repositories(project)
        except (ApiError, OSError, ValueError) as exc:
            _log_failure(f"harbor repositories of {project}", exc, redact)
            source.failed += 1
            complete = False
            continue
        for repo in repos:
            try:
                artifacts = harbor.artifacts(project, repo)
            except (ApiError, OSError, ValueError) as exc:
                _log_failure(f"harbor artifacts of {project}/{repo}", exc, redact)
                source.failed += 1
                complete = False
                continue
            for artifact in artifacts:
                try:
                    entries = harbor_entries(host, project, repo, artifact, harbor.sbom)
                except (KeyError, TypeError, ValueError, AttributeError):
                    source.failed += 1
                    complete = False
                    log.error("failed harbor artifact in %s/%s: malformed listing entry", project, repo)
                    continue
                for entry in entries:
                    source.entries.append(entry)
                    source.present.add((entry.name, entry.version))
    source.complete = complete
    return source


def latest_winners(entries: list[Entry]) -> set[tuple[str, str]]:
    """Per project name, the version with the newest creation time among those with an SBOM."""
    best: dict[str, tuple[tuple, tuple[str, str]]] = {}
    for entry in entries:
        if entry.load_bom is None:
            continue
        rank = (_epoch(entry.created), entry.version)
        if entry.name not in best or rank > best[entry.name][0]:
            best[entry.name] = (rank, (entry.name, entry.version))
    return {key for _, key in best.values()}


class Syncer:
    def __init__(self, dt_api: DependencyTrack, config: "Config", clock: Clock, redact: Redactor, counts: Counter):
        self.dt = dt_api
        self.config = config
        self.clock = clock
        self.redact = redact
        self.counts = counts
        self.budget = config.max_uploads

    def handle(self, source: Source, entry: Entry, is_winner: bool) -> None:
        label = f"{entry.name}@{entry.version}"
        try:
            outcome = self.process(source, entry, is_winner)
        except Skip as exc:
            log.info("skipped %s: %s", label, exc)
            outcome = "skipped"
        except Exception as exc:  # one item must not stop the run
            _log_failure(label, exc, self.redact)
            outcome = "failed"
        self.counts[outcome] += 1

    def process(self, source: Source, entry: Entry, is_winner: bool) -> str:
        label = f"{entry.name}@{entry.version}"
        project = self.dt.lookup(entry.name, entry.version)
        if entry.load_bom is None:
            if project is not None:
                self.touch(project, self.dt.properties(project["uuid"]))
                return "skipped"
            return self.request_scan(source, entry, label)
        props = self.dt.properties(project["uuid"]) if project else {}
        if project is not None and props.get((GROUP, DIGEST)) == entry.digest:
            self.touch(project, props)
            self.ensure_latest(project, is_winner)
            return "skipped"
        if self.budget <= 0:
            if project is not None:
                self.touch(project, props)
                self.ensure_latest(project, is_winner)
            return "deferred"
        self.budget -= 1
        raw = encode_bom(entry.load_bom(), self.config.max_bom_bytes)
        if self.config.dry_run:
            log.info("dry-run: would upload %s (%d bytes)", label, len(raw))
            return "uploaded"
        self.dt.upload(entry.name, entry.version, source.tag, is_winner, raw)
        project = self.dt.lookup(entry.name, entry.version)
        if project is None:
            raise ItemError("project missing after upload")
        self.dt.put_property(project["uuid"], DIGEST, entry.digest, exists=(GROUP, DIGEST) in props)
        self.touch(project, props)
        self.ensure_latest(project, is_winner)
        log.info("uploaded %s", label)
        return "uploaded"

    def request_scan(self, source: Source, entry: Entry, label: str) -> str:
        if entry.scan is None:
            return "skipped"
        if self.budget <= 0:
            return "deferred"
        self.budget -= 1
        if self.config.dry_run:
            log.info("dry-run: would request SBOM generation for %s", label)
            return "skipped"
        status = source.request_scan(*entry.scan)
        if status == 422:
            log.info("not scannable, skipped: %s", label)
        else:
            log.info("requested SBOM generation for %s", label)
        return "skipped"

    def touch(self, project: dict, props: dict) -> None:
        value = str(int(self.clock()))
        if self.config.dry_run:
            log.debug("dry-run: would set last-seen=%s on %s@%s", value, project["name"], project["version"])
            return
        self.dt.put_property(project["uuid"], LAST_SEEN, value, exists=(GROUP, LAST_SEEN) in props)

    def ensure_latest(self, project: dict, is_winner: bool) -> None:
        if not is_winner or project.get("isLatest"):
            return
        if self.config.dry_run:
            log.info("dry-run: would mark %s@%s as latest", project["name"], project["version"])
            return
        self.dt.set_latest(project["uuid"])

    def retain(self, source: Source) -> None:
        if not source.complete:
            log.warning("%s listing incomplete: no deletions this run", source.tag)
            return
        grace = self.config.retention_days * 86400
        for project in self.dt.tagged(source.tag):
            if (project["name"], project["version"]) in source.present:
                continue
            label = f"{project['name']}@{project['version']}"
            try:
                props = self.dt.properties(project["uuid"])
                last = _to_int(props.get((GROUP, LAST_SEEN)))
                if last is None:
                    log.warning("kept %s: no last-seen property", label)
                    continue
                if self.clock() - last < grace:
                    continue
                if self.config.dry_run:
                    log.info("dry-run: would delete %s", label)
                else:
                    self.dt.delete(project["uuid"])
                    log.info("deleted %s", label)
                self.counts["deleted"] += 1
            except Exception as exc:  # one project must not stop the run
                _log_failure(label, exc, self.redact)
                self.counts["failed"] += 1


@dataclasses.dataclass(frozen=True)
class Config:
    dt_url: str
    dt_api_key: str
    harbor_url: str
    harbor_user: str
    harbor_password: str
    k8s_url: str
    k8s_token: str
    k8s_ca_file: str
    retention_days: int = 30
    page_size_dt: int = 100
    page_size_harbor: int = 100
    page_size_k8s: int = 100
    max_uploads: int = 200
    max_bom_bytes: int = MAX_BOM_BYTES
    dry_run: bool = False
    # Public registry host for project names when HARBOR_URL is an internal address.
    harbor_host: str = ""


def run_sync(config: Config, http, k8s_http, clock: Clock) -> Counter:
    redact = Redactor(config.dt_api_key, config.harbor_password, config.k8s_token)
    dt_api = DependencyTrack(http, config.dt_url, config.dt_api_key, config.page_size_dt, redact)
    harbor = Harbor(http, config.harbor_url, config.harbor_user, config.harbor_password, config.page_size_harbor, redact)
    k8s = Kubernetes(k8s_http, config.k8s_url, config.k8s_token, config.page_size_k8s, redact)
    counts: Counter = Counter()
    log.info("sbom-sync starting dry_run=%s retention_days=%d max_uploads=%d",
             str(config.dry_run).lower(), config.retention_days, config.max_uploads)

    try:
        cluster = load_cluster(k8s, redact)
    except (ApiError, OSError, ValueError) as exc:
        _log_failure("sbomreports listing", exc, redact)
        counts["failed"] += 1
        cluster = Source(TAG_CLUSTER)
    harbor_host = config.harbor_host or urllib.parse.urlsplit(config.harbor_url).netloc
    harbor_src = load_harbor(harbor, harbor_host, redact)
    counts["failed"] += cluster.failed + harbor_src.failed

    # The cluster copy is CycloneDX from the operator; it wins a (name, version) collision.
    cluster_keys = {(entry.name, entry.version) for entry in cluster.entries}
    kept = [entry for entry in harbor_src.entries if (entry.name, entry.version) not in cluster_keys]
    counts["skipped"] += len(harbor_src.entries) - len(kept)
    harbor_src.entries = kept

    winners = latest_winners(cluster.entries + kept)
    syncer = Syncer(dt_api, config, clock, redact, counts)
    for source in (cluster, harbor_src):
        for entry in source.entries:
            syncer.handle(source, entry, (entry.name, entry.version) in winners)
        try:
            syncer.retain(source)
        except (ApiError, OSError, ValueError) as exc:
            _log_failure(f"{source.tag} retention", exc, redact)
            counts["failed"] += 1

    log.info(
        "sbom-sync-summary uploaded=%d skipped=%d deleted=%d deferred=%d failed=%d dry_run=%s",
        counts["uploaded"], counts["skipped"], counts["deleted"], counts["deferred"], counts["failed"],
        str(config.dry_run).lower(),
    )
    return counts


def read_secret(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError as exc:
        raise ConfigError(f"cannot read secret file {path}: {exc.strerror or 'unreadable'}") from None
    if not value:
        raise ConfigError(f"secret file {path} is empty")
    return value


def load_config(environ: Mapping[str, str], read_file: Callable[[str], str], dry_run: bool = False) -> Config:
    def text(name: str, default: str | None = None) -> str:
        value = environ.get(name, default)
        if not value or not value.strip():
            raise ConfigError(f"{name} must be set")
        return value.strip()

    def number(name: str, default: int) -> int:
        try:
            value = int(environ.get(name, str(default)).strip())
        except ValueError:
            raise ConfigError(f"{name} must be an integer") from None
        if value < 1:
            raise ConfigError(f"{name} must be at least 1")
        return value

    return Config(
        dt_url=text("DT_URL"),
        dt_api_key=read_file(text("DT_API_KEY_FILE", "/etc/sbom-sync/DT_API_KEY")),
        harbor_url=text("HARBOR_URL"),
        harbor_user=read_file(text("HARBOR_USERNAME_FILE", "/etc/sbom-sync/HARBOR_USERNAME")),
        harbor_password=read_file(text("HARBOR_PASSWORD_FILE", "/etc/sbom-sync/HARBOR_PASSWORD")),
        k8s_url=text("K8S_API_URL", "https://kubernetes.default.svc"),
        k8s_token=read_file(text("K8S_TOKEN_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/token")),
        k8s_ca_file=text("K8S_CA_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
        retention_days=number("RETENTION_DAYS", 30),
        page_size_dt=number("DT_PAGE_SIZE", 100),
        page_size_harbor=number("HARBOR_PAGE_SIZE", 100),
        page_size_k8s=number("K8S_PAGE_SIZE", 100),
        max_uploads=number("MAX_UPLOADS_PER_RUN", 200),
        dry_run=dry_run or environ.get("DRY_RUN", "").strip().lower() in TRUTHY,
        harbor_host=environ.get("HARBOR_HOST", "").strip(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync SBOMs into Dependency-Track 5.")
    parser.add_argument("--dry-run", action="store_true", help="log planned writes, send none")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    try:
        config = load_config(os.environ, read_secret, dry_run=args.dry_run)
        cluster_ca = ssl.create_default_context(cafile=config.k8s_ca_file)
    except (ConfigError, OSError, ssl.SSLError) as exc:
        log.error("configuration: %s", exc)
        return 2
    counts = run_sync(config, UrllibTransport(), UrllibTransport(cluster_ca), time.time)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
