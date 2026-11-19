#!/usr/bin/env bash
# Copy a separate, bounded OS mechanism calibration into the marked lab VM.
# DO NOT run the first-batch script again. Does not modify MemoryGuard_RunKit.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM="memoryguard-lab"
[[ "$(uname -s)" == Darwin ]] || { echo 'Mac host only' >&2; exit 2; }
command -v multipass >/dev/null || { echo 'Multipass is not installed' >&2; exit 2; }
multipass info "$VM" >/dev/null || { echo 'MemoryGuard lab VM is missing' >&2; exit 2; }
multipass start "$VM" >/dev/null 2>&1 || true
multipass exec "$VM" -- test -f /etc/memoryguard-allowed-vm || {
  echo 'Refused: existing VM has no approved MemoryGuard marker' >&2; exit 2;
}
multipass exec "$VM" -- test -f /home/ubuntu/MemoryGuard_RunKit/results/batch_20261103T052931Z/environment.json || {
  echo 'Refused: complete original Batch 1 not found in this lab VM' >&2; exit 2;
}
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
PACKAGE="$(mktemp -t mg_phase2_source)"
trap 'rm -f "$PACKAGE"' EXIT
PARENT="$(dirname "$ROOT")"
NAME="$(basename "$ROOT")"
COPYFILE_DISABLE=1 tar -czf "$PACKAGE" -C "$PARENT" --exclude="$NAME/phase2_results" "$NAME"
multipass transfer "$PACKAGE" "$VM:/home/ubuntu/mg_phase2_source.tar.gz"
multipass exec "$VM" -- bash -lc 'cd /home/ubuntu && tar -xzf mg_phase2_source.tar.gz'
echo "Running separate no-swap cgroup reclaim calibration in $VM only."
echo 'No changes to the first 100 research trials, and no Mac memory stress.'
set +e
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i multipass exec "$VM" -- sudo python3 \
    "/home/ubuntu/$NAME/calibrate.py" --output "/home/ubuntu/$NAME/phase2_results/calibration_$STAMP"
  STATUS=$?
else
  multipass exec "$VM" -- sudo python3 \
    "/home/ubuntu/$NAME/calibrate.py" --output "/home/ubuntu/$NAME/phase2_results/calibration_$STAMP"
  STATUS=$?
fi
set -e
echo 'Copying calibration evidence (even if the calibration stopped early).'
if multipass exec "$VM" -- bash -lc "cd /home/ubuntu/$NAME && tar -czf /home/ubuntu/mg_phase2_results.tar.gz phase2_results"; then
  multipass transfer "$VM:/home/ubuntu/mg_phase2_results.tar.gz" "$ROOT/mg_phase2_results.tar.gz"
  tar -xzf "$ROOT/mg_phase2_results.tar.gz" -C "$ROOT"
  echo "Results archive ready for review: $ROOT/mg_phase2_results.tar.gz"
else
  echo 'Results transfer failed. VM evidence is still under phase2_results/.' >&2
  exit 2
fi
if (( STATUS != 0 )); then
  echo 'Calibration stopped without producing a finished result. Upload archive for diagnosis.' >&2
  exit "$STATUS"
fi
echo 'Mechanism calibration finished. This is NOT a completed AI/eBPF second experiment.'
