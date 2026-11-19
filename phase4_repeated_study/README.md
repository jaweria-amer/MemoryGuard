> **Completed-study note (2026-11-03):** Phase 4 was subsequently completed (32 trials, 256 alerts) and independently audited. The source code remains unchanged; the historical status text below was written before the VM run. See the root README and results_summary for measured findings.

# MemoryGuard — Phase 4 randomized integrated follow-up

**Stage:** runnable code, locally checked only. **No Phase 4 VM experiments have been run yet.**

This follow-up repeats Phase 3's **augmented file-backed rule-cache eBPF monitor** under local reclaim pressure, with and without separately running CPU-only Ollama. It is **not** the original 100-trial lightweight-monitor experiment. The original Batch 1, Phase 2 and Phase 3 output folders are never modified.

## What has already been verified

The uploaded Phase 3 real-data archive contained four complete runs, 32 matched eBPF alerts, zero misses, zero OOM kills, `memory.high` events in every run and real completed inference requests in both AI-on conditions. First-alert latency for unprotected/protected was 56.746/0.342 ms (without AI) and 37.023/0.395 ms (with AI). All alerts still met 250 ms. These are one-run-per-condition **pilot** results only. See `PHASE3_AUDIT.md` and `PHASE3_VERIFIED.json`.

## Experimental design — separate Phase 4 dataset

- **32 trials:** eight four-condition blocks, randomized in a fixed-seed, position- and sequence-balanced order.
- Four conditions per block: AI off × `memory.low` 0/192 MiB; AI on × `memory.low` 0/192 MiB.
- **256 harmless fake-file opens:** eight 1-second-spaced events per trial; no real secret files.
- Actual bpftrace `openat` events filtered to the test process, with a deterministic lookup of 512 pages in a 128 MiB synthetic rule cache **before** the alert timestamp.
- Independent, idle, 512 MiB file-backed competitor and a **local, reversible** `memory.high=640 MiB` limit in `mgphase4.slice`. This deliberately induces local reclaim; Ollama is a separate concurrent load, not the presumed source of reclaim.
- Primary prospective Phase 4 latency outcome: the **first cold-cache alert delay** in milliseconds. The **unchanged original 250 ms alert deadline** is reported separately. The per-trial median and AI speed are secondary.
- Pair first alerts within the same block and AI condition, compare unprotected minus protected. The **block/trial**, not each event, is the repeated-measurement unit.
- Record cgroup snapshots, monitor faults, refaults, pressure events, PSI, per-event delays, and actual AI requests. A data integrity check will refuse incomplete, duplicated, or misconfigured trials.

## Exact steps on the Mac

1. Keep the already completed original archives. Do **not** delete `memoryguard-lab`.
2. Download `MemoryGuard_Phase4_RepeatedStudy_v1.zip` to `~/Downloads`, then double-click to extract. The new folder must be exactly `MemoryGuard_Phase4_RepeatedStudy`.
3. Open Terminal and paste:

   ```bash
   cd ~/Downloads/MemoryGuard_Phase4_RepeatedStudy && bash run_from_mac.sh new
   ```

4. Leave the laptop plugged in with its lid open, and keep Terminal active. **The workflow may take significant time**: 32 new Linux trials and occasional Ollama restarts. On error it stops and retains evidence; **do not start another `new` run**.
5. When Terminal finishes, upload the generated file:

   `~/Downloads/MemoryGuard_Phase4_RepeatedStudy/mg_phase4_results.tar.gz`

6. If the run fails, upload the archive **before** considering a retry. If a retry is appropriate, use `bash run_from_mac.sh resume` — not `new`. This verifies and skips valid earlier trials and preserves incomplete attempts separately. It refuses different source code or changed VM/kernel/RAM settings in an existing dataset.

## What a successful run produces

Inside `phase4_results/study_<UTC timestamp>/`:

- `study_plan.json`: saved randomized schedule, source-code hash, outcomes, parameters.
- `environment.json`: VM/kernel/RAM settings.
- Individual trial directories: `attacks.jsonl`, `alerts.jsonl`, `events.csv`, `result.json`, `cgroup_snapshots.json`, monitor/stress statuses and errors.
- `ai_session_XX/`: actual generated-token measurements and Ollama diagnostic logs.
- `study_qc.json`, `trial_metrics.csv`, `paired_metrics.csv`, `event_metrics.csv`: created **only when all 32 trials validate**.
- `_failed_attempts/` and `study_failure.json`, if the run was interrupted.

The Mac wrapper copies the working data back **even on a failed run**. Files stay on the user's Mac/VM; none are auto-uploaded elsewhere.

## What Phase 4 cannot establish

- The original 100-trial lightweight monitor already met 250 ms almost always; Phase 4's modified rule path is not equivalent.
- `memory.high` is an experimenter-imposed bound, **not memory pressure produced by Ollama alone**. The AI-on versus AI-off comparison is a co-workload control, not a demonstration of AI-driven reclaim.
- The first-alert slowdown can be dominated by cold-cache faults, so pooling all events hides the effect. A large first-alert difference does not show improved 250 ms success if both conditions still meet it.
- A balanced schedule reduces ordering effects but cannot eliminate all system noise or blinding/carryover limitations. No result is guaranteed. Report failed attempts and exploratory status.
- Do not substitute these findings for a claimed causal effect in an unmodified eBPF production security monitor.

## Safety boundaries

Requires existing, explicitly marked 8 GiB Ubuntu 24.04 `memoryguard-lab` Multipass VM with the original passed Batch 1 QC file; no swap; at least 10 GiB free VM disk. This package never sets host memory pressure, never creates a new VM, never touches production data, and never modifies the previous projects' source or output folders. It imposes `memory.high` only within its separate `mgphase4.slice` and reverts it after each trial. It uses a distinct Ollama port (`11437`) and separate service names. Any failure aborts instead of silently fabricating/accepting results.
