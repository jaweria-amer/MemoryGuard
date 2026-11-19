#!/usr/bin/env bash
# Run only INSIDE the disposable MemoryGuard Ubuntu 24.04 VM.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"
PRESSURES="${MG_PRESSURES:-0,2048,4096,5120}"
MODEL="${MG_MODEL:-qwen2.5:1.5b}"
cd "$ROOT"
if [[ "$MODE" != all && "$MODE" != pilot && "$MODE" != resume ]]; then
  echo 'Usage: sudo bash scripts/one_go_vm.sh [pilot|all|resume]' >&2; exit 2
fi
if [[ "$(id -u)" != 0 ]]; then
  echo 'Run using sudo inside the Ubuntu lab VM.' >&2; exit 2
fi
if [[ ! -f /etc/memoryguard-allowed-vm ]]; then
  echo 'REFUSED: this machine has not been explicitly marked as a disposable MemoryGuard VM.' >&2
  echo 'Use scripts/run_mac.sh to create and mark the VM; do not run this on your Mac, a workstation, or any server.' >&2
  exit 2
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 ]]; then
  echo "REFUSED: expected Ubuntu 24.04 VM; found ${PRETTY_NAME:-unknown}." >&2; exit 2
fi
if (( $(awk '/MemTotal/ {print $2}' /proc/meminfo) < 7*1024*1024 )); then
  echo 'REFUSED: less than 7 GiB RAM was presented to the VM.' >&2; exit 2
fi
if (( $(nproc) < 4 )); then
  echo 'REFUSED: fewer than four virtual CPUs were allocated.' >&2; exit 2
fi
IFS=',' read -r -a level_array <<< "$PRESSURES"
if [[ ${#level_array[@]} -ne 4 ]]; then
  echo 'MG_PRESSURES must contain exactly four comma-separated MiB allocations.' >&2; exit 2
fi
for p in "${level_array[@]}"; do
  if [[ ! "$p" =~ ^[0-9]+$ ]] || (( p > 6144 )); then
    echo 'Pressure allocations must be nonnegative integers <= 6144 MiB.' >&2; exit 2
  fi
done
mkdir -p results figures run_logs
LOG="run_logs/$(date -u +%Y%m%dT%H%M%SZ)_${MODE}.log"
exec > >(tee -a "$LOG") 2>&1
trap 'echo "Run stopped at line $LINENO; earlier logs and results have been preserved."' ERR
printf '\n=== MemoryGuard controlled VM experiment: %s ===\n' "$MODE"
echo "OS: $PRETTY_NAME; kernel: $(uname -r); CPU: $(nproc); RAM KiB: $(awk '/MemTotal/ {print $2}' /proc/meminfo)"
echo "Pressures: $PRESSURES; model: $MODEL"

echo '=== Installing/verifying laboratory dependencies ==='
bash scripts/bootstrap_ubuntu.sh
if ! command -v ollama >/dev/null 2>&1; then
  echo 'Downloading official Ollama Linux installer to a local file (then executing only inside the disposable VM).'
  curl -fsSL https://ollama.com/install.sh -o /tmp/memoryguard_ollama_install.sh
  sha256sum /tmp/memoryguard_ollama_install.sh
  sh /tmp/memoryguard_ollama_install.sh
fi
command -v ollama
# Never leave the model daemon outside the experimental AI cgroup.
systemctl stop ollama.service 2>/dev/null || true
systemctl disable ollama.service 2>/dev/null || true
export PYTHONPATH="$ROOT/src"

echo '=== Python unit tests ==='
python3 -m unittest discover -s tests -v

echo '=== Preparing fixed model BEFORE the timed tests ==='
python3 -m memoryguard.prepare_model --model "$MODEL"

if [[ "$MODE" == resume ]]; then
  echo '=== Resume ONLY the incomplete trials from the original batch ==='
  if [[ -n "${MG_RESUME_BATCH:-}" ]]; then
    BATCH_NAME="$MG_RESUME_BATCH"
  else
    BATCH_DIRS=()
    for path in results/batch_*; do
      [[ -d "$path" ]] && BATCH_DIRS+=("$path")
    done
    if [[ ${#BATCH_DIRS[@]} -ne 1 ]]; then
      echo "Cannot safely choose a source batch: found ${#BATCH_DIRS[@]}." >&2
      echo 'Set MG_RESUME_BATCH=batch_YYYYMMDDTHHMMSSZ to select the original batch.' >&2
      exit 2
    fi
    BATCH_NAME="$(basename "${BATCH_DIRS[0]}")"
  fi
  if [[ ! "$BATCH_NAME" =~ ^batch_[0-9]{8}T[0-9]{6}Z$ ]]; then
    echo "Invalid batch name: $BATCH_NAME" >&2; exit 2
  fi
  BATCH_DIR="results/$BATCH_NAME"
  if [[ ! -f "$BATCH_DIR/environment.json" ]]; then
    echo "Original batch not found: $BATCH_DIR; no data will be modified." >&2; exit 2
  fi
  echo "Source batch: $BATCH_DIR"
  echo 'Existing valid trials will be checked and SKIPPED, not recalculated.'
  echo 'Incomplete attempts will be archived, then retried once. Their evidence will not be erased.'
  python3 -m memoryguard.runner --resume-batch "$BATCH_DIR" --model "$MODEL" \
    --pressures "$PRESSURES" --repeats 5 --attacks 30 --interval 2 --warmup 10
  python3 -m memoryguard.qc "$BATCH_DIR" --mode full
  python3 -m memoryguard.plots "$BATCH_DIR" --output figures
  echo "RESUMED DATA COLLECTION COMPLETE: $BATCH_DIR"
  echo 'Passing technical QC does not establish the memory.low hypothesis.'
  exit 0
fi

echo '=== eBPF monitor-only pilot ==='
python3 -m memoryguard.runner --pilot-monitor

echo '=== eBPF + CPU-only local Ollama pilot ==='
python3 -m memoryguard.runner --pilot-full --model "$MODEL"

echo '=== Four-trial low/high-pressure smoke test ==='
python3 -m memoryguard.runner --smoke --model "$MODEL" \
  --pressures "$PRESSURES" --attacks 20 --interval 2 --warmup 6
SMOKE_DIR="$(find results -maxdepth 1 -type d -name 'smoke_*' -print | LC_ALL=C sort | tail -1)"
if [[ -z "$SMOKE_DIR" ]]; then echo 'No smoke data directory.' >&2; exit 2; fi
python3 -m memoryguard.qc "$SMOKE_DIR" --mode smoke || {
  echo 'STOP: smoke-test evidence is incomplete, no real memory stalls occurred, or an OOM kill occurred.'
  echo 'Read results/smoke_*/qc.json and recent run_logs. Adjust MG_PRESSURES conservatively, then rerun.'
  exit 2
}
if [[ "$MODE" == pilot ]]; then
  echo 'Pilot/pressure checks passed. No 100-trial batch has been started.'
  echo 'To complete the 100 trials, rerun: bash scripts/run_mac.sh all (on your Mac).'
  exit 0
fi

echo '=== Full design: 5 conditions x 4 pressures x 5 repeats = 100 trials ==='
python3 -m memoryguard.runner --all --model "$MODEL" --pressures "$PRESSURES" \
  --repeats 5 --attacks 30 --interval 2 --warmup 10
BATCH_DIR="$(find results -maxdepth 1 -type d -name 'batch_*' -print | LC_ALL=C sort | tail -1)"
if [[ -z "$BATCH_DIR" ]]; then echo 'No full batch data directory.' >&2; exit 2; fi
python3 -m memoryguard.qc "$BATCH_DIR" --mode full
python3 -m memoryguard.plots "$BATCH_DIR" --output figures
printf '\nDATA COLLECTION COMPLETE. Raw evidence: %s; graphs: figures/\n' "$BATCH_DIR"
echo 'This does not automatically complete the academic report, defense, or GitHub publication.'
