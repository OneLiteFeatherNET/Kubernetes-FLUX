#!/usr/bin/env python3
"""Collect every container image this repository pins, for SBOM scanning.

Only what the repo pins is visible here; whatever a chart resolves on its own
is not. Use --stats to see how far that reaches.

  scripts/collect-images.py            one image reference per line
  scripts/collect-images.py --json     same, as a JSON array
  scripts/collect-images.py --stats    counts and the sources they came from
"""
import argparse
import json
import os
import sys

import yaml

ROOTS = ("apps", "infrastructure", "helm", "clusters")
SKIP_DIRS = {".git", "node_modules", ".claude", "graphify-out", ".serena", ".venv"}
# whole-file encrypted; parsing them yields nothing but noise
SKIP_SUFFIX = (".sops.yaml", ".sops.env", ".sops.json", ".sops.crt", ".sops.key", ".sops.conf")


def is_image_string(value):
    """A tag or digest separates an image ref from an arbitrary string."""
    if not isinstance(value, str) or not value or value.startswith(("$", "{")):
        return False
    if "@sha256:" in value:
        return True
    tail = value.rsplit("/", 1)[-1]
    return ":" in tail and not tail.endswith(":")


def join(registry, repository, tag):
    if not repository or not isinstance(repository, str):
        return None
    if registry and isinstance(registry, str):
        repository = f"{registry.rstrip('/')}/{repository}"
    if tag and isinstance(tag, (str, int, float)):
        return f"{repository}:{tag}"
    return None


def walk(node, out, where):
    if isinstance(node, dict):
        img = node.get("image")
        if is_image_string(img):
            out.setdefault(img, set()).add(where)
        elif isinstance(img, dict):
            ref = join(img.get("registry"), img.get("repository"), img.get("tag"))
            if ref:
                out.setdefault(ref, set()).add(where)

        # a HelmRelease splits registry/repository/tag across one level
        if "repository" in node and "tag" in node and "image" not in node:
            ref = join(node.get("registry"), node.get("repository"), node.get("tag"))
            if ref:
                out.setdefault(ref, set()).add(where)

        for key, value in node.items():
            if key == "images" and isinstance(value, list):
                for entry in value:
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("newName") or entry.get("name")
                    ref = join(None, name, entry.get("newTag"))
                    if ref:
                        out.setdefault(ref, set()).add(where)
            walk(value, out, where)

    elif isinstance(node, list):
        for value in node:
            walk(value, out, where)


def collect(repo_root):
    out = {}
    for root in ROOTS:
        base = os.path.join(repo_root, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if not name.endswith((".yaml", ".yml")) or name.endswith(SKIP_SUFFIX):
                    continue
                path = os.path.relpath(os.path.join(dirpath, name), repo_root)
                try:
                    with open(os.path.join(repo_root, path), encoding="utf-8") as fh:
                        docs = list(yaml.safe_load_all(fh))
                except (yaml.YAMLError, UnicodeDecodeError):
                    continue  # templates and encrypted files
                for doc in docs:
                    walk(doc, out, path)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = collect(repo_root)
    # a floating tag says nothing about what was scanned
    images = sorted(i for i in found if not i.endswith((":latest", ":stable", ":main")))

    if args.stats:
        print(f"{len(found)} image references, {len(images)} scannable\n")
        for image in images:
            print(f"  {image}")
            for source in sorted(found[image]):
                print(f"      {source}")
        skipped = sorted(set(found) - set(images))
        if skipped:
            print(f"\nskipped, floating tag ({len(skipped)}):")
            for image in skipped:
                print(f"  {image}")
    elif args.json:
        json.dump(images, sys.stdout)
    else:
        print("\n".join(images))


if __name__ == "__main__":
    main()
