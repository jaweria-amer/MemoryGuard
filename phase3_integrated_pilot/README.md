> **Completed-study note (2026-11-03):** Phase 3 was subsequently executed in four integrated pilot trials, producing 32 matched alerts. This README retains its original pre-run instructions. Results are exploratory.

# MemoryGuard Phase 3 — isolated integrated pilot (VM only)

**Status: locally syntax/unit checked; not yet executed on your VM.** This is
**not** the original proposed 100-trial experiment, not final proof of the
research hypothesis, and not a replacement for your existing Batch 1 results.

## Evidence motivating this pilot

Phase 2, uploaded real archive `mg_phase2_results.tar.gz`, showed that under an
independent 470 MiB memory.high ceiling, a synthetic 128 MiB file-backed
monitor cache was reclaimed without protection (around 420 major faults and
9,600 file refaults during first lookup), while a 192 MiB-protected cache
stayed resident (zero such faults). Two repetitions per setting is far too
small for a statistical claim. There was no AI or eBPF in Phase 2.

## What this independent test measures

1. The real Ubuntu bpftrace hook watches the original **harmless fake-file
   `openat` events** from the `mg_attack` test process, with no systemwide
   sensitive-file logging.
2. A Python alert processor holds a *synthetic*, file-backed, 128 MiB rule
   cache. For each matched file event it looks up a deterministic set of 512
   pages **before** it timestamps the alert. All pages contain mock rule data.
   The alert time therefore includes kernel event receipt, Python processing,
   and an actual file-backed rule lookup.
3. A competing 512 MiB file-backed cache occupies a sibling service in the
   **new** `mgphase3.slice`, bounded by `memory.high=640 MiB`. The 640 MiB
   ceiling is deliberately less aggressive than the Phase 2 pilot's 470 MiB,
   to allow overhead for the actual bpftrace subprocess. This ceiling creates
   **cgroup-local pressure**, not systemwide RAM exhaustion.
4. Compare the synthetic monitor with `memory.low=0` vs `192 MiB`, also
   applying the chosen value to its parent slice (as verified in Phase 2).
5. Repeat the pair with **CPU-only Qwen 2.5 1.5B Ollama** concurrently
   generating responses **in another cgroup** on local port `11436`.
   The comparison with and without Ollama is a CPU-contention control.
6. Each of the **4 pilot trials** generates 8 benign file-open events and
   records complete time measurements (250 ms deadline), major faults,
   workingset refaults, PSI, `memory.events`, OOM kills, and inference rates.

**Important causal boundary:** This pilot does **not** test whether Ollama
itself creates RAM-reclaim pressure. Reclaim is imposed separately using
`memory.high`; the AI load is a concurrent co-workload. Any benefit would
apply to an *augmented synthetic rule-cache monitor under imposed reclaim*,
not automatically to the lightweight original monitor.

## Mac instructions

1. Keep `~/Downloads/MemoryGuard_RunKit/memoryguard_outputs.tar.gz` and
   `~/Downloads/MemoryGuard_Phase2_Calibrator/mg_phase2_results.tar.gz` safe.
2. Download this separate ZIP to **Downloads**, double-click to extract
   `MemoryGuard_Phase3_IntegratedPilot`.
3. Open Terminal:

   ```bash
   cd ~/Downloads/MemoryGuard_Phase3_IntegratedPilot
   bash run_from_mac.sh
   ```

4. Let the **foreground** script run, Mac plugged in and lid open. It reuses
   the marked `memoryguard-lab` Ubuntu 24.04 VM; it refuses another VM or a
   missing original valid batch. It never stress-tests macOS.
5. Upload `~/Downloads/MemoryGuard_Phase3_IntegratedPilot/mg_phase3_results.tar.gz`
   here whether the pilot succeeds or stops.

The script copies back evidence even on error. It never deletes or writes to
`MemoryGuard_RunKit/results`, never installs packages, never configures swap,
never changes the original `memoryguard.slice`, and uses new service names.

## Interpretation after the run

- Check all four `result.json` and the `pilot_summary.json`, plus actual
  cgroup snapshots, before making any comparative claim.
- A passing readiness/quality gate proves only that the intended test path
  actually ran, not that `memory.low` helps meet the 250 ms deadline.
- First alert delay may be larger than subsequent alerts because bringing
  reclaimed pages back into cache warms the synthetic working set.
- With one repeat per setting, treat the numbers as feasibility evidence,
  never as p-values or a reliably estimated causal effect.
- If the unprotected monitor experiences no reclaim (changed load geometry,
  bpftrace memory cost), report **pilot inconclusive** and recalibrate; do
  not increase host RAM pressure or fabricate a result.
- Only after reviewing this pilot should we plan a preregistered larger
  study with balanced repeated conditions.

## Troubleshooting

If the pilot stops, upload `mg_phase3_results.tar.gz`. Inspect `failure.json`,
`*.journal.txt`, `bpftrace_stderr.log`, and `ollama_stderr.log`. The service
script restores the experiment-only memory limits and stops workers in
`finally`; if a VM interruption prevents this, inspect `systemctl
status mgphase3.slice` before another run.
