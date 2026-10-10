#!/usr/bin/env python3
"""Drop SOPS-encrypted references from kustomization files, in place.

flux-local renders without the cluster's age key, so a kustomization.yaml
that lists a *.sops.yaml patch, resource or patchesStrategicMerge entry
cannot build. This applies the same stripping as validate.sh (which is not
changed here). Run it on a checkout you are free to modify, never on a
branch you intend to commit. Encrypted secretGenerator inputs (*.sops.env)
stay in place: they are read as opaque bytes and the generated Secrets are
dropped by --skip-secrets.

usage: flux-local-sanitize.py ROOT
"""
import os
import sys

import yaml

KEYS = ("patches", "resources", "patchesStrategicMerge")


def is_sops(entry):
    path = entry.get("path") if isinstance(entry, dict) else entry
    return isinstance(path, str) and path.endswith((".sops.yaml", ".sops.yml"))


def strip(doc):
    changed = False
    for key in KEYS:
        entries = doc.get(key)
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not is_sops(e)]
        if len(kept) != len(entries):
            doc[key] = kept
            changed = True
    return changed


def main(root):
    stripped = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        name = next((n for n in ("kustomization.yaml", "kustomization.yml") if n in files), None)
        if name is None:
            continue
        full = os.path.join(dirpath, name)
        with open(full) as fh:
            doc = yaml.safe_load(fh)
        if not isinstance(doc, dict) or not strip(doc):
            continue
        with open(full, "w") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False)
        stripped += 1
        print(f"sanitized {os.path.relpath(full, root)}")
    print(f"stripped sops references from {stripped} kustomization file(s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
