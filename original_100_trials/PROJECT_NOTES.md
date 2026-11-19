# MemoryGuard - project decisions and open points

Source documents reviewed: "MemoryGuard - Proposal Defense 3" (20 pages)
and "Project Details" (2 pages).

## Decisions kept from the approved-direction proposal

- Single-student OS project focused on virtual memory behavior.
- Ubuntu 24.04 VM with 8 GB RAM and 4 virtual CPUs.
- Local CPU-only Ollama model; use Qwen2.5 1.5B as the concrete initial choice.
- eBPF event capture using bpftrace, with a Python rule checker.
- Harmless fake-secret file opens rather than live malware.
- Main metric: proportion of alerts received within 250 ms.
- Comparisons: no protection, higher CPU priority, several memory.low levels.
- At least five repeats; collect AI tokens per second and cgroup stats.

## Important methodological improvements

1. An assigned memory.low value is not sufficient proof of effective protection.
   Check both the monitor cgroup and ancestor slice values at run time.
2. Keep the Ollama server/runner AND stress-ng in the AI cgroup. If Ollama stays
   in its ordinary systemd service, the AI working set is not in the test group.
3. The stress-ng allocation level is not necessarily the same as actual memory
   pressure. Use the memory.pressure file and memory.stat changes to interpret it.
4. Event-to-alert timing uses CLOCK_MONOTONIC in both test processes. No clocks
   across different machines are involved.
5. A missed event affects the on-time rate but is never assigned a made-up delay.
6. Only observed measurements go into the final figures. The proposal's graph
   is a sketch of expected results, not evidence.
7. Any run where the tracer is broken must be labeled invalid rather than being
   counted as evidence that the monitor was slow.

## Items Jaweria/faculty still need to supply or confirm

- Evidence that this research question was approved and is unique in class.
- The exact "Project - Part 4 (sample)" report template.
- A real Ubuntu VM run and logs. This starter was NOT run in the requested VM.
- Pressure levels calibrated to the VM (defaults are merely trial settings).
- If faculty wants a numerical on-time target besides the 250 ms deadline.
- A short sample of Jaweria's previous academic writing, if style matching is
  important beyond the wording of the existing proposal.

## Deadlines in the provided handouts

- Topic/group registration: November 2, 2026.
- Project submission: November 26, 2026, 11:59 PM.
- Proposal checkpoints: November 9, 16 and 22 before final submission.

## What the starter already covers

- Attack event generator and ID-based matching (code completed).
- bpftrace watcher and simple Python rule checker (written; VM validation pending).
- Cgroup-aware Ollama/stress-ng workload and systemd trial runner (written;
  end-to-end VM validation pending).
- Measured throughput, events and cgroup snapshots (written; VM data pending).
- Offline unit tests for matching and metrics (pass locally).
- Graph creation from collected measurements only (no generated sample results).

## What the starter does not yet prove

- That the Ubuntu VM has sufficient pressure for lateness to appear.
- That memory.low improves timeliness on the actual hardware.
- That the final experiment reaches a particular success threshold.
- That all 100 real trials have executed successfully.

## RunKit v0.2 engineering changes (NOT laboratory results)

- Corrected a trial-contamination bug: `mg_attack` Linux task name now returns
  to its original value after an attack batch; otherwise files created for the
  *next* trial could be observed before their trigger timestamps.
- Added `runner --smoke` for four short low/high-memory-pressure checks.
- Added `qc.py` to require valid trial records, observed PSI stalls at highest
  pressure, and zero OOM kills before declaring a set valid for further work.
- Added `scripts/run_mac.sh` and `scripts/one_go_vm.sh`: disposable VM bootstrap,
  model preparation, pilots, smoke, gated 100-run batch, graphs, result transfer.
- These scripts passed shell syntax checks and Python unit tests in a Linux
  container. No real Linux VM eBPF test has been performed by this development
  environment; VM compatibility is not guaranteed.


## November 3 real-run evidence and resume implementation (v0.4)

The first full batch `batch_20261103T052931Z` produced 59 claimed-valid, independently
rechecked trials before Ollama returned an HTTP 500 / connection closure in the
60th attempt, `r3_p5120_low_256m`. The failed attempt's metadata is marked
`experimental_valid=false`, and its logs are preserved. Cause is **not proven**
by that error message alone; do not label it an OOM kill.

In the 59 valid trials there were 1,770 test file opens, one late alert and
zero missed alerts. The monitored process had zero recorded major faults and
file refaults across these trials. No OOM kills were reported in the valid
trial cgroup deltas. Some memory stall time occurred at the highest stress-ng
setting, but simply allocating 5,120 MiB is not equivalent to proving
reclaim of the monitor's working set. The observed slowdown of Ollama under
stress-ng can also be affected by CPU contention. These are preliminary
limitations, not a claim that `memory.low` makes no difference in all systems.

The `resume` mode verifies the original batch configuration and per-trial
metadata before deciding which 59 to keep. Incomplete attempts are moved into
`results/_failed_attempts/<batch>/` with a history log and then rerun; they
are not destroyed or silently counted as valid. On a new failure, the runner
stops again and captures systemd journals without changing completed data.

**Never run `all` to resume.** Use `bash scripts/run_mac.sh resume` on the
original Mac with the original `memoryguard-lab` VM intact. Keep the Mac
connected to power with the lid open throughout the experiment.
