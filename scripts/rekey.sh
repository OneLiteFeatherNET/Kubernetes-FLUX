#!/usr/bin/env bash
# Re-encrypt every SOPS-encrypted file in this repo to the recipient set
# currently listed in .sops.yaml. Run from anywhere; it cd's to the repo root.
#
#   ./scripts/rekey.sh          re-key every encrypted file
#   ./scripts/rekey.sh --list   print the file list and exit (used by verification)
#
# Run this after ANY edit to .sops.yaml — adding a recipient, removing one,
# or rotating a key. `sops updatekeys` is a no-op on a file whose recipients
# already match, so re-running is always safe.
#
# The suffix list below mirrors the path_regex in .sops.yaml. If you add a
# suffix there, add it here. `.sops.yaml` itself is excluded: it is config,
# not ciphertext, and `sops updatekeys` on it errors.
set -euo pipefail

cd "$(dirname "$0")/.."

mapfile -t FILES < <(
  find apps infrastructure clusters -type f \
    \( -name '*.sops.env' \
    -o -name '*.sops.yaml' -o -name '*.sops.yml' \
    -o -name '*.sops.json' \
    -o -name '*.sops.conf' \
    -o -name '*.sops.crt' \
    -o -name '*.sops.key' \
    -o -name '*.dockerconfigjson' \
    -o -name '*.s3.conf' \
    -o -name '*.env' \) \
    ! -name '.sops.*' \
    | sort
)

if [[ "${#FILES[@]}" -eq 0 ]]; then
  echo "error: no encrypted files found — wrong directory?" >&2
  exit 1
fi

if [[ "${1:-}" == "--list" ]]; then
  printf '%s\n' "${FILES[@]}"
  exit 0
fi

echo "re-keying ${#FILES[@]} file(s) against $(pwd)/.sops.yaml"
for f in "${FILES[@]}"; do
  printf '  %s\n' "$f"
  sops updatekeys -y "$f"
done
echo
echo "done: ${#FILES[@]} file(s) re-keyed"
echo "now verify every file carries every recipient:"
echo "  ./scripts/rekey.sh --list | xargs grep -L <RECIPIENT>   # must print nothing"
