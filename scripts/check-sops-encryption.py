#!/usr/bin/env python3
"""Assert every git-tracked file matched by a whole-file .sops.yaml creation_rule is encrypted.

The file list is derived from .sops.yaml at runtime — never a hardcoded glob — so
it cannot drift from the rules. Only rules WITHOUT an `encrypted_regex` are
considered: those encrypt the whole file. A field-level rule legitimately matches
plaintext manifests that happen to contain no encryptable field, so it is out of
scope here. (This repo currently has no field-level rule; it was removed in
eb4ffd9 because it emitted cleartext under a valid-looking sops block.)
"""
import re
import subprocess
import sys

import yaml

# .sops.yaml self-matches the `sops\.ya?ml$` alternative in the whole-file rule
# but is configuration, not a secret. `.sops.pub.asc` is a tracked public key and
# matches no rule, so it needs no entry.
ALLOWLIST = {".sops.yaml"}

rules = yaml.safe_load(open(".sops.yaml"))["creation_rules"]
patterns = [
    re.compile(r["path_regex"])
    for r in rules
    if r.get("path_regex") and not r.get("encrypted_regex")
]
if not patterns:
    sys.exit("::error::no whole-file creation_rules found in .sops.yaml")

files = subprocess.run(
    ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
).stdout.split("\0")

bad, checked = [], 0
for f in files:
    if not f or f in ALLOWLIST or not any(p.search(f) for p in patterns):
        continue
    checked += 1
    blob = open(f, "rb").read()
    if b"ENC[AES256_GCM" not in blob and b"sops_version" not in blob and b"sops:" not in blob:
        bad.append(f)

print(f"sops-encryption: checked {checked} matched file(s)", file=sys.stderr)
for f in bad:
    print(f"::error file={f}::matched a .sops.yaml creation_rule but contains no sops metadata")
sys.exit(1 if bad else 0)
