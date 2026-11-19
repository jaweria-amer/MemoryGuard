#!/usr/bin/env bash
# Host-side launcher: creates/runs an isolated 8 GB Ubuntu 24.04 Multipass VM.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"
INSTANCE="memoryguard-lab"
if [[ "$MODE" != all && "$MODE" != pilot && "$MODE" != resume ]]; then
  echo 'Usage: bash scripts/run_mac.sh [pilot|all|resume]' >&2; exit 2
fi
if [[ $(uname -s) != Darwin ]]; then
  echo 'This host launcher is for macOS. For another host, create an Ubuntu 24.04 VM manually.' >&2; exit 2
fi
MAC_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if (( MAC_MAJOR < 13 )); then
  echo 'This version of Multipass requires macOS 13 or newer. Use a compatible Mac.' >&2; exit 2
fi
HOST_RAM="$(sysctl -n hw.memsize)"
if (( HOST_RAM < 16*1024*1024*1024 )); then
  echo 'Less than 16 GiB Mac RAM. Do not allocate an 8 GiB stress-test VM on this host.' >&2; exit 2
fi
if ! command -v multipass >/dev/null 2>&1; then
  echo 'Install Multipass first from https://canonical.com/multipass/install or: brew install --cask multipass' >&2
  exit 2
fi
if [[ ! -e "$ROOT/src/memoryguard/runner.py" ]]; then
  echo "Missing source files in $ROOT" >&2; exit 2
fi
echo 'Using only a disposable VM. Nothing will be stress-tested on the macOS host.'
if ! multipass info "$INSTANCE" >/dev/null 2>&1; then
  if [[ "$MODE" == resume ]]; then
    echo "REFUSED: original lab VM $INSTANCE is missing. Cannot resume safely." >&2
    exit 2
  fi
  multipass launch 24.04 --name "$INSTANCE" --cpus 4 --memory 8G --disk 40G
  multipass exec "$INSTANCE" -- sudo touch /etc/memoryguard-allowed-vm
else
  echo "Found existing $INSTANCE. Checking ownership marker before reusing it."
  multipass start "$INSTANCE" >/dev/null 2>&1 || true
  if ! multipass exec "$INSTANCE" -- test -f /etc/memoryguard-allowed-vm; then
    echo "An existing VM has this name but is not marked as our disposable lab. No changes made." >&2; exit 2
  fi
fi
# Avoid transferring any raw data BACK into the VM when relaunching the kit.
TEMP_ARCHIVE="$(mktemp -t memoryguard_run_kit)"
trap 'rm -f "$TEMP_ARCHIVE"' EXIT
PARENT="$(dirname "$ROOT")"
NAME="$(basename "$ROOT")"
tar -czf "$TEMP_ARCHIVE" \
  --exclude="$NAME/results" --exclude="$NAME/figures" \
  --exclude="$NAME/run_logs" --exclude="$NAME/.pytest_cache" \
  --exclude="$NAME/memoryguard_outputs.tar.gz" \
  --exclude='__pycache__' --exclude='*.pyc' -C "$PARENT" "$NAME"
multipass transfer "$TEMP_ARCHIVE" "$INSTANCE:/home/ubuntu/memoryguard_code.tar.gz"
multipass exec "$INSTANCE" -- bash -lc 'cd /home/ubuntu && tar -xzf memoryguard_code.tar.gz'
set +e
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i multipass exec "$INSTANCE" -- sudo env \
    "MG_PRESSURES=${MG_PRESSURES:-0,2048,4096,5120}" \
    "MG_MODEL=${MG_MODEL:-qwen2.5:1.5b}" \
    "MG_RESUME_BATCH=${MG_RESUME_BATCH:-}" \
    bash /home/ubuntu/"$NAME"/scripts/one_go_vm.sh "$MODE"
  STATUS=$?
else
  multipass exec "$INSTANCE" -- sudo env \
    "MG_PRESSURES=${MG_PRESSURES:-0,2048,4096,5120}" \
    "MG_MODEL=${MG_MODEL:-qwen2.5:1.5b}" \
    "MG_RESUME_BATCH=${MG_RESUME_BATCH:-}" \
    bash /home/ubuntu/"$NAME"/scripts/one_go_vm.sh "$MODE"
  STATUS=$?
fi
set -e
# Preserve trial evidence even if a guard check or pilot failed.
echo 'Copying available raw results, figures and logs back to the Mac.'
multipass exec "$INSTANCE" -- bash -lc \
  "cd /home/ubuntu/$NAME && mkdir -p results figures run_logs && tar -czf /home/ubuntu/memoryguard_outputs.tar.gz results figures run_logs"
multipass transfer "$INSTANCE:/home/ubuntu/memoryguard_outputs.tar.gz" "$ROOT/memoryguard_outputs.tar.gz"
tar -xzf "$ROOT/memoryguard_outputs.tar.gz" -C "$ROOT"
echo "Results and logs are under: $ROOT"
if [[ $STATUS -ne 0 ]]; then
  echo 'THE WORKFLOW STOPPED for a safety/validity/runtime reason. Do not treat this as finished research.' >&2
  echo "See $ROOT/run_logs and $ROOT/results for troubleshooting evidence." >&2
  exit "$STATUS"
fi
echo 'VM workflow finished without a detected guard failure.'
