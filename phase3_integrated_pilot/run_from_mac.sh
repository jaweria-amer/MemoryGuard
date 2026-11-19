#!/usr/bin/env bash
# Run only inside the already marked Multipass lab; never stress the macOS host.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="$(basename "$ROOT")"
VM="memoryguard-lab"
[[ "$(uname -s)" == Darwin ]] || { echo 'Refused: Mac host wrapper only' >&2; exit 2; }
command -v multipass >/dev/null || { echo 'Multipass not installed' >&2; exit 2; }
if ! multipass info "$VM" >/dev/null; then
  echo 'Existing memoryguard-lab VM is missing/unavailable. No VM created.' >&2; exit 2
fi
multipass start "$VM" >/dev/null 2>&1 || true
multipass exec "$VM" -- test -f /etc/memoryguard-allowed-vm || {
  echo 'Refused: VM ownership marker missing' >&2; exit 2;
}
multipass exec "$VM" -- test -f /home/ubuntu/MemoryGuard_RunKit/results/batch_20261103T052931Z/qc.json || {
  echo 'Refused: preserved original Batch 1 QC missing' >&2; exit 2;
}
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
ARCHIVE="$(mktemp -t mg_phase3_src)"
trap 'rm -f "$ARCHIVE"' EXIT
COPYFILE_DISABLE=1 tar -czf "$ARCHIVE" -C "$(dirname "$ROOT")" \
  --exclude="$NAME/phase3_results" --exclude="$NAME/__pycache__" \
  --exclude="$NAME/mg3/__pycache__" --exclude="$NAME/tests/__pycache__" \
  --exclude="$NAME/mg_phase3_results.tar.gz" "$NAME"
multipass transfer "$ARCHIVE" "$VM:/home/ubuntu/mg_phase3_source.tar.gz"
multipass exec "$VM" -- bash -lc 'cd /home/ubuntu && tar -xzf mg_phase3_source.tar.gz'
echo 'Starting FOUR-TRIAL end-to-end calibration in the separate mgphase3.slice.'
echo 'Original Batch 1, Phase 2 calibration, VM swap, and macOS memory are unchanged.'
set +e
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i multipass exec "$VM" -- bash -lc \
    "cd /home/ubuntu/$NAME && sudo python3 -m mg3.runner --output /home/ubuntu/$NAME/phase3_results/pilot_$STAMP --repeats 1 --attacks 8 --interval 1.0"
  STATUS=$?
else
  multipass exec "$VM" -- bash -lc \
    "cd /home/ubuntu/$NAME && sudo python3 -m mg3.runner --output /home/ubuntu/$NAME/phase3_results/pilot_$STAMP --repeats 1 --attacks 8 --interval 1.0"
  STATUS=$?
fi
set -e
echo 'Copying trial results and error logs back to your Mac.'
if multipass exec "$VM" -- bash -lc "cd /home/ubuntu/$NAME && tar -czf /home/ubuntu/mg_phase3_results.tar.gz phase3_results"; then
  multipass transfer "$VM:/home/ubuntu/mg_phase3_results.tar.gz" "$ROOT/mg_phase3_results.tar.gz"
  echo "Results archive ready for review: $ROOT/mg_phase3_results.tar.gz"
else
  echo 'Transfer failed; original VM evidence stays in phase3_results.' >&2
  exit 2
fi
if (( STATUS != 0 )); then
  echo 'Pilot stopped. Upload archived diagnostics. Do not rerun before reviewing errors.' >&2
  exit "$STATUS"
fi
echo 'Integrated PILOT finished. Completion does not prove a memory.low benefit.'
