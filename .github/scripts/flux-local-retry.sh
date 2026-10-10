#!/usr/bin/env bash
# Run a flux-local command and retry only when it failed on a transient download error.
#
# usage: flux-local-retry.sh LOG -- COMMAND [ARGS...]
#
# Every attempt is shown on stdout and appended to LOG. A failed attempt is retried
# (max 3 attempts, waits from RETRY_BACKOFF, default "15 45") only if its output matches
# TRANSIENT_PATTERN; any other failure returns the command's exit code immediately.
set -uo pipefail

# Helm reports a failed chart download as "failed to fetch <url> : 503 Service Unavailable".
TRANSIENT_PATTERN='failed to fetch .*: 50[0-9] |Service Unavailable|Bad Gateway|Gateway Timeout|i/o timeout|context deadline exceeded|timed out|connection reset by peer|connection refused|TLS handshake timeout|unexpected EOF|no such host'
MAX_ATTEMPTS=3

if [ "$#" -lt 3 ] || [ "$2" != "--" ]; then
  echo "usage: $0 LOG -- COMMAND [ARGS...]" >&2
  exit 2
fi
log="$1"
shift 2

read -r -a backoff <<< "${RETRY_BACKOFF:-15 45}"

attempt=1
while :; do
  out="$(mktemp)"
  "$@" 2>&1 | tee -a "${log}" "${out}"
  rc="${PIPESTATUS[0]}"
  if [ "${rc}" -eq 0 ]; then
    rm -f "${out}"
    exit 0
  fi
  if [ "${attempt}" -ge "${MAX_ATTEMPTS}" ] || ! grep -qE -- "${TRANSIENT_PATTERN}" "${out}"; then
    rm -f "${out}"
    exit "${rc}"
  fi
  delay="${backoff[$((attempt - 1))]:-${backoff[-1]}}"
  rm -f "${out}"
  echo "::warning::attempt ${attempt}/${MAX_ATTEMPTS} hit a transient download error, retrying in ${delay}s"
  sleep "${delay}"
  attempt=$((attempt + 1))
done
