#!/usr/bin/env python3
"""Upload CycloneDX SBOMs to Dependency-Track, one project per image.

Trivy writes CycloneDX 1.7. Dependency-Track 4.13 ships cyclonedx-core-java
11.x, which validates against 1.6 and rejects 1.7 outright, so every BOM is
rewritten before upload:

  1. specVersion and $schema are set to 1.6
  2. license IDs the 1.6 SPDX enum does not know (SMAIL-GPL and friends) move
     from license.id to license.name, which is where non-SPDX licenses belong

Both steps are lossless for what Dependency-Track actually analyses. Once the
server runs 4.14 or newer the rewrite becomes a no-op that costs nothing, so it
stays unconditional rather than sniffing the server version.

Project name is the image repository, project version its tag - that is what
makes Dependency-Track compare v2.15.0 against v2.15.2 instead of treating them
as unrelated projects.

  DT_URL=https://dependency-track.example DT_API_KEY=odt_... \
    scripts/upload-sbom.py sbom/*.cdx.json
"""
import argparse
import base64
import glob
import json
import os
import sys
import urllib.error
import urllib.request

SPDX_1_6 = "https://raw.githubusercontent.com/CycloneDX/specification/1.6/schema/spdx.schema.json"


def spdx_ids():
    """The license IDs CycloneDX 1.6 accepts. Empty set means: rewrite nothing."""
    try:
        with urllib.request.urlopen(SPDX_1_6, timeout=30) as response:
            return set(json.load(response).get("enum") or [])
    except Exception as exc:
        print(f"note: SPDX 1.6 enum unavailable ({exc}); leaving license IDs alone", file=sys.stderr)
        return set()


def to_1_6(bom, allowed):
    bom["specVersion"] = "1.6"
    bom["$schema"] = "http://cyclonedx.org/schema/bom-1.6.schema.json"
    if not allowed:
        return bom, 0

    moved = 0

    def walk(node):
        nonlocal moved
        if isinstance(node, dict):
            for entry in node.get("licenses") or []:
                license_ = entry.get("license") if isinstance(entry, dict) else None
                if isinstance(license_, dict) and license_.get("id") not in allowed and "id" in license_:
                    license_["name"] = license_.pop("id")
                    moved += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(bom)
    return bom, moved


def image_of(bom, fallback):
    """Trivy records the scanned image as the BOM's own metadata component,
    and puts the tag in the name rather than in version: goharbor/x:v2.15.0."""
    component = (bom.get("metadata") or {}).get("component") or {}
    name = component.get("name") or fallback
    version = component.get("version") or ""

    repository, colon, tag = name.rpartition(":")
    if colon and "/" not in tag:  # a tag never contains a slash, a registry port does
        name, version = repository, version or tag
    return name, version or "unknown"


def upload(url, key, project, version, bom, dry_run):
    payload = json.dumps({
        "project": "",
        "projectName": project,
        "projectVersion": version,
        "projectTags": ["source:trivy-ci"],
        "autoCreate": True,
        "bom": base64.b64encode(json.dumps(bom).encode()).decode(),
    }).encode()

    if dry_run:
        return "dry-run"

    request = urllib.request.Request(
        f"{url}/api/v1/bom", data=payload, method="PUT",
        headers={"Content-Type": "application/json", "X-Api-Key": key},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return str(response.status)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("boms", nargs="+", help="CycloneDX files, globs allowed")
    parser.add_argument("--dry-run", action="store_true", help="convert and report, upload nothing")
    args = parser.parse_args()

    url = os.environ.get("DT_URL", "").rstrip("/")
    key = os.environ.get("DT_API_KEY", "")
    if not args.dry_run and not (url and key):
        sys.exit("DT_URL and DT_API_KEY must be set")

    paths = sorted({p for pattern in args.boms for p in glob.glob(pattern)})
    if not paths:
        sys.exit("no SBOM files matched")

    allowed = spdx_ids()
    failures = 0
    moved_total = 0
    empty = 0

    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                bom = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL  {path}: unreadable ({exc})")
            failures += 1
            continue

        bom, moved = to_1_6(bom, allowed)
        moved_total += moved
        project, version = image_of(bom, os.path.basename(path))
        components = len(bom.get("components") or [])

        # Distroless and busybox-style images carry no package database. Uploading
        # those would leave empty projects that read as "nothing to fix here".
        if not components:
            print(f"empty   {project}:{version}  (no package database)")
            empty += 1
            continue

        try:
            status = upload(url, key, project, version, bom, args.dry_run)
            print(f"{status:8}{project}:{version}  ({components} components)")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:160]
            print(f"FAIL {exc.code}  {project}:{version}  {body}")
            failures += 1
        except Exception as exc:
            print(f"FAIL     {project}:{version}  {exc}")
            failures += 1

    uploaded = len(paths) - failures - empty
    print(f"\n{uploaded}/{len(paths)} uploaded, {empty} empty, {moved_total} license IDs rewritten")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
