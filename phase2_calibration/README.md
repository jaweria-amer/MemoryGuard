> **Completed-study note (2026-11-03):** Phase 2 was subsequently executed in four controlled calibration runs; this is the archived pre-run guide. Phase 2 is a file-backed cache mechanism test, not an eBPF detection outcome.

# MemoryGuard Phase 2: controlled file-backed reclaim calibration (VM only)

**Status: pilot code, not experimental evidence.** The script has been syntax/unit
checked but has **not been run against your Ubuntu VM**. Stop and send logs if
it fails. Do not present its measurements as proof of the original hypothesis.

## Why this exists

Batch 1 completed 100 valid AI/eBPF trials, 2,999/3,000 on-time events, and
zero observed monitor major faults / file refaults. The VM was confirmed to
have ~7.5 GiB available RAM, **0 swap**, ~32 GiB disk free, and no ongoing PSI
stall pressure when idle. The `stress-ng --vm-keep` load generated substantial
CPU competition but little observed monitor reclaim.

Without swap, anonymous process memory is not normally swappable, but
**file-backed cache can still be reclaimed**. This small OS-mechanism pilot
tries to validate reclaim with a synthetic *file-backed rule/history cache*,
not by increasing global RAM stress to unsafe levels.

## What the pilot does

- Runs **only inside the existing marked `memoryguard-lab` Multipass VM**, with
  8 GiB RAM; refuses any unmarked machine.
- Creates a separate `mgphase2.slice`. **Does not touch the original
  `memoryguard.slice`, VM settings, Batch 1 files, or Mac system RAM.**
- Creates a synthetic 128 MiB file-backed monitor cache and a 512 MiB file-
  backed *competitor* cache. Both workers fill their own files and then go idle.
- Sets **only the new slice's** `memory.high` to 470 MiB, which causes bounded,
  cgroup-local reclaim instead of exhausting the VM's 8 GiB physical RAM.
- Tests 0 vs. 192 MiB monitor `memory.low` in alternating order, twice each.
- Captures per-cgroup `memory.stat`, `memory.events`, PSI totals, and a timing
  probe that reads random cached pages *after* the competing cache is loaded.
- Restores `memory.high=max` / `memory.low=0` and stops both worker services;
  saves measurements, journals on errors, and removes large test files.
- **Does not enable swap**, run Ollama, attach eBPF, generate fake attack events,
  or measure real 250 ms alert detection. It is a **mechanism calibration only**.

## Steps on Mac

1. **Preserve** `~/Downloads/MemoryGuard_RunKit/memoryguard_outputs.tar.gz`
   and the existing Ubuntu VM; do not rerun the 100-trial batch.
2. Download this ZIP into `~/Downloads` and double-click to extract
   `MemoryGuard_Phase2_Calibrator`.
3. Open Terminal:

   ```bash
   cd ~/Downloads/MemoryGuard_Phase2_Calibrator
   bash run_from_mac.sh
   ```

4. Upload `~/Downloads/MemoryGuard_Phase2_Calibrator/mg_phase2_results.tar.gz`
   here for review.

## Interpretation checks

A useful calibration requires the slice's `memory.events:high` counter to
increase *and* the monitor to show reclaim-related faults/refaults or measurable
lookup stalls under the unprotected setting. A noticeable effect in four runs
would justify a new **separately labeled, end-to-end AI + eBPF + rule-cache
batch**. This mechanism pilot by itself cannot show that `memory.low` improves
security detection. If no monitor reclaim is observed, report the first batch
as a negative result instead of trying to force positive findings.

`memory.low` is best-effort and ancestor-dependent; no specific improvement
is guaranteed: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

The script uses only file-backed cache to avoid danger from VM-wide anonymous
memory overcommit with no swap. Even bounded `memory.high` can slow the test
services; stop the experiment if the VM becomes unresponsive. The Mac host
never runs these workloads.
