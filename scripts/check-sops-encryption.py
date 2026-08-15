#!/usr/bin/env python3
"""Assert every git-tracked file matched by a whole-file .sops.yaml creation_rule is
encrypted, and encrypted to every age recipient that rule lists.

The file list is derived from .sops.yaml at runtime — never a hardcoded glob — so
it cannot drift from the rules. Only rules WITHOUT an `encrypted_regex` are
considered: those encrypt the whole file. A field-level rule legitimately matches
plaintext manifests that happen to contain no encryptable field, so it is out of
scope here. (This repo currently has no field-level rule; it was removed in
eb4ffd9 because it emitted cleartext under a valid-looking sops block.)

The recipient assertion catches the one mistake that breaks the cluster: a file
committed after `.sops.yaml` changed but before `./scripts/rekey.sh` ran is still
validly encrypted, yet the cluster's key cannot read it and every Flux layer that
touches it fails to decrypt. An age public key is a public key, so this check
needs no private key — CI never gains the ability to read a secret.
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
# (compiled path_regex, age recipients) per whole-file rule, in file order —
# sops applies the FIRST rule a path matches, so the order matters here too.
matchers = [
    (re.compile(r["path_regex"]), [k.strip() for k in (r.get("age") or "").split(",") if k.strip()])
    for r in rules
    if r.get("path_regex") and not r.get("encrypted_regex")
]
if not matchers:
    sys.exit("::error::no whole-file creation_rules found in .sops.yaml")

files = subprocess.run(
    ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
).stdout.split("\0")

bad, checked = [], 0
for f in files:
    if not f or f in ALLOWLIST:
        continue
    rule = next((m for m in matchers if m[0].search(f)), None)
    if rule is None:
        continue
    checked += 1
    blob = open(f, "rb").read()
    if b"ENC[AES256_GCM" not in blob and b"sops_version" not in blob and b"sops:" not in blob:
        bad.append((f, "contains no sops metadata"))
        continue
    missing = [k for k in rule[1] if k.encode() not in blob]
    if missing:
        bad.append((f, f"not encrypted to {len(missing)} recipient(s): {', '.join(missing)}"))

print(f"sops-encryption: checked {checked} matched file(s)", file=sys.stderr)
for f, why in bad:
    print(f"::error file={f}::matched a .sops.yaml creation_rule but {why}")
if bad:
    print("::error::run ./scripts/rekey.sh and commit the result")
sys.exit(1 if bad else 0)
