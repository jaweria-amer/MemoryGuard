#!/usr/bin/env bash
# Separate, bounded Phase 4 randomized follow-up. No macOS RAM stress.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="MemoryGuard_Phase4_RepeatedStudy"
VM="memoryguard-lab"
MODE="${1:-new}"
[[ "$MODE" == new || "$MODE" == resume ]] || { echo 'Usage: bash run_from_mac.sh [new|resume]' >&2; exit 2; }
[[ "$(basename "$ROOT")" == "$NAME" ]] || { echo "Expected extracted folder $NAME; found $ROOT" >&2; exit 2; }
[[ "$(uname -s)" == Darwin ]] || { echo 'Refused: Mac host wrapper only' >&2; exit 2; }
command -v multipass >/dev/null || { echo 'Multipass not installed' >&2; exit 2; }
multipass start "$VM" >/dev/null 2>&1 || true
multipass info "$VM" >/dev/null || { echo 'Existing lab VM missing/unavailable; no VM created' >&2; exit 2; }
multipass exec "$VM" -- test -f /etc/memoryguard-allowed-vm || { echo 'Refused: VM ownership marker missing' >&2; exit 2; }
multipass exec "$VM" -- test -f /home/ubuntu/MemoryGuard_RunKit/results/batch_20261103T052931Z/qc.json || { echo 'Original 100-trial batch QC missing. Stopped.' >&2; exit 2; }
ARCHIVE="$(mktemp -t mg_phase4_src)"
trap 'rm -f "$ARCHIVE"' EXIT
COPYFILE_DISABLE=1 tar -czf "$ARCHIVE" -C "$(dirname "$ROOT")" \
  --exclude="$NAME/phase4_results" --exclude="$NAME/__pycache__" \
  --exclude="$NAME/mg4/__pycache__" --exclude="$NAME/tests/__pycache__" \
  --exclude="$NAME/mg_phase4_results.tar.gz" "$NAME"
multipass transfer "$ARCHIVE" "$VM:/home/ubuntu/mg_phase4_source.tar.gz"
multipass exec "$VM" -- bash -lc 'cd /home/ubuntu && tar --warning=no-unknown-keyword -xzf mg_phase4_source.tar.gz'
if [[ "$MODE" == new ]]; then
  STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
  OUTPUT="/home/ubuntu/$NAME/phase4_results/study_$STAMP"
  echo 'Starting a NEW 32-trial randomized, counterbalanced Phase 4 experiment.'
  RUN_CMD="cd /home/ubuntu/$NAME && sudo python3 -m unittest discover -s tests -v && sudo python3 -m mg4.runner --output $OUTPUT --blocks 8 --seed 20261103 --attacks 8 --interval 1.0"
else
  OUTPUT="$(multipass exec "$VM" -- bash -lc "ls -1dt /home/ubuntu/$NAME/phase4_results/study_* 2>/dev/null | head -n 1" || true)"
  [[ -n "$OUTPUT" ]] || { echo 'No prior Phase 4 study found to resume; refused to create a new one.' >&2; exit 2; }
  echo "Resuming existing Phase 4 output: $OUTPUT"
  RUN_CMD="cd /home/ubuntu/$NAME && sudo python3 -m unittest discover -s tests -v && sudo python3 -m mg4.runner --output $OUTPUT --resume"
fi
echo 'Original 100-trial results, Phase 2 and Phase 3 outputs stay unchanged.'
set +e
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i multipass exec "$VM" -- bash -lc "$RUN_CMD"
  STATUS=$?
else
  multipass exec "$VM" -- bash -lc "$RUN_CMD"
  STATUS=$?
fi
set -e
echo 'Copying Phase 4 trial evidence to Mac (including partial work after errors).'
if multipass exec "$VM" -- bash -lc "cd /home/ubuntu/$NAME && tar --warning=no-unknown-keyword -czf /home/ubuntu/mg_phase4_results.tar.gz phase4_results"; then
  multipass transfer "$VM:/home/ubuntu/mg_phase4_results.tar.gz" "$ROOT/mg_phase4_results.tar.gz"
  echo "Results archive ready for review: $ROOT/mg_phase4_results.tar.gz"
else
  echo 'Copy failed; original Phase 4 files are retained inside VM.' >&2
  exit 2
fi
if (( STATUS != 0 )); then
  echo 'Experiment stopped; upload saved archive before attempting resume.' >&2
  exit "$STATUS"
fi
echo 'PHASE 4 FINISHED. Verify study_qc.json; completion alone does not prove hypothesis.'
