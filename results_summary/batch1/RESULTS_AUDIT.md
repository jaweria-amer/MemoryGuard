# MemoryGuard - First-Batch Research Audit (3 November 2026)

**Status:** experimental data collection complete; hypothesis **not established**. This is an internal research-analysis document, **not** the final university report. Do not claim that memory.low improved monitor alert timing based on this batch.

## 1. Provenance and reproducibility

- Original archive: `memoryguard_outputs.tar(2).gz` (uploaded full batch)
- SHA-256 of the exact uploaded archive: `b94f1eb5e19d7eff0e4527881ef69d47e39b0ebbe15da0937f055df03823cb01`
- Batch directory inside archive: `results/batch_20261103T052931Z/`
- System: Ubuntu 24.04.5 LTS; Linux kernel `6.8.0-147-generic` on `aarch64`; 4 vCPUs; approximately 8 GiB RAM.
- Tools used: `bpftrace` v0.20.2, Ollama (client v0.40.13), `stress-ng` v0.17.06; model `qwen2.5:1.5b` on CPU.
- Full design: 5 conditions (baseline, higher CPU priority, memory.low 64/128/256 MiB), four `stress-ng` memory sizes (0/2048/4096/5120 MiB), five repeats, 30 harmless events per trial, and a 250 ms detection deadline.
- One earlier interrupted AI trial was archived under `results/_failed_attempts/` and successfully rerun. This failure is part of operational reliability documentation, not an additional valid trial.

## 2. Directly observed results

| Measure | Observation |
| --- | --- |
| Valid trials | 100 of 100 (all configuration cells: 5 repeats each) |
| Harmless trigger events | 3,000 |
| On-time alerts / late / missing | 2,999 / 1 / 0 |
| Overall on-time rate | 99.9667% |
| Unprotected baseline | 600/600 on time (100%) |
| CPU-priority condition | 600/600 on time (100%) |
| memory.low = 64 MiB | 600/600 on time (100%) |
| memory.low = 128 MiB | 599/600 on time (99.8333%) |
| memory.low = 256 MiB | 600/600 on time (100%) |
| Median event alert latency (all observed events) | 0.1736 ms |
| Pooled event p99 observed latency | 3.1501 ms |
| Longest measured latency | 586.8817 ms (128 MiB protection; 2048 MiB load; repeat 3) |
| Linux cgroup OOM kills in valid trials | 0 |

The sole late alert is not evidence of a consistent protective or harmful effect: all other events at that condition and all baseline events were on time. It is an outlier. The corresponding **trial-level p99** is approximately 419.27 ms even though the **pooled overall p99** is 3.15 ms; these are different quantities. A median across five trial-level p99s can hide this one outlier, so both the single worst-case delay and the number of deadline violations must be reported.

## 3. Were the workloads creating the intended memory problem?

**Not convincingly for the monitor.** Across the 100 trial measurement windows:

- The monitor cgroup recorded **zero** major page faults, **zero** anonymous refaults and **zero** file refaults. Monitor memory PSI stall time also remained zero.
- Its measured cgroup memory usage was about **49-53 MiB** at the beginning and end of trials. All three nonzero `memory.low` settings (64/128/256 MiB) exceeded the monitor's typical measured usage.
- The parent cgroup recorded only **6,386 microseconds (6.386 milliseconds) of memory PSI 'some' stalls across the entire batch**, concentrated in 10 of the 25 trials at 5120 MiB. The lower three pressure levels recorded none in their trial measurement windows.
- The AI cgroup accounted for nearly all observed memory stalls. This does **not** demonstrate the monitor itself encountered reclaim-induced blocking.
- The test did verify that `memory.low` was set on the monitor cgroup and its parent; this verifies *configuration*, not an improvement in alert latency.
- The monitor cgroup's `memory.events:low` counter increased in five high-allocation trials (12 events in total). This deserves discussion, but it was not accompanied by measured monitor major faults or refaults.

Critically, the archive does **not** record the VM's active swap configuration. Verify swap before trying to induce reclamation of anonymous process pages. File-backed working sets can also be reclaimed, but they need to be deliberately represented and measured.

## 4. AI throughput and CPU contention

The table reports median trial-level median model generation rate across all 25 trials at each stress-ng allocation.

| stress-ng allocation | Median AI generation speed | Median parent-cgroup CPU PSI 'some' stall time per trial | Memory PSI observed at parent |
| --- | ---: | ---: | --- |
| 0 MiB | 89.45 tokens/s | 0.58 s | No |
| 2048 MiB | 17.99 tokens/s | 15.38 s | No |
| 4096 MiB | 18.41 tokens/s | 15.40 s | No |
| 5120 MiB | 17.92 tokens/s | 15.43 s | Yes, small amounts in 10 trials |

The model's speed fell approximately **80% whenever the stress process was active**. This is *not* a measured 80% cost of `memory.low`. The workload used `stress-ng --vm 1 --vm-keep`, which rewrites the allocated memory continuously and can consume CPU in addition to RAM. Strong CPU PSI was recorded in all the active-stress groups. Since AI throughput was similarly low at 2048, 4096 and 5120 MiB even though memory PSI was absent at the first two settings, the change is consistent with substantial CPU contention. The experiment does not isolate memory-induced slowing from CPU-induced slowing.

Do not report a numeric "AI cost of memory protection" without comparing **within the same memory-load level** and carefully describing the CPU confound. No consistent reduction in AI speed attributable to larger memory.low settings is established in this batch.

## 5. What the first batch does and does not answer

**Established:** A small bpftrace-based monitor can detect a harmless file-open trigger within 250 ms reliably while a CPU-only local model and stress-ng workload run in this Ubuntu VM. A reproducible 100-trial collection and raw log/graph workflow was demonstrated.

**Not established:** A reclaim/refault mechanism causing monitor alert delay; a meaningful minimum nonzero `memory.low` needed to meet the deadline; or a reliable causal benefit of protected memory over the unprotected baseline. Under these particular tested conditions, **0 MiB was adequate for the 250 ms deadline**, but this is not a universal result and does not show what would happen under stronger, cleaner memory reclaim.

The original study's hypothesis should be recorded as **not supported by Batch 1**, not confirmed or refuted in all conditions.

## 6. Methodological notes for final report

- The primary outcome is an end-to-end user-space receipt delay from the attack generator's monotonic timestamp (just before `open`) to the Python parser's receipt of bpftrace output. It is **not** a pure kernel tracepoint latency.
- Experimental units are **trials**, with 30 repeated triggers nested within each trial; avoid assuming 3,000 independent randomized experiments for statistical tests.
- A single very long delay (586.9 ms) shows a rare tail event and must not be hidden behind medians alone.
- Some final `ai_status.json` files show `error: Remote end closed connection without response` after the experiment stops services. All 100 trials nonetheless passed the runner's live readiness check and completed AI throughput samples. Treat this as a cleanup/status-reporting issue to fix in the future source code, **not** as proof all completed trials failed. Do not rewrite historic raw files.
- No final report format has been supplied yet; the course requires matching its `Project - Part 4 (sample)` template when available.

## 7. Recommended targeted second experiment (conditional)

**Do not just rerun the same 100 trials with a higher stress-ng allocation.** First execute the read-only VM preflight included in this audit to confirm swap, free disk and cgroup support. Then design a small pilot with the following changes, documented before data collection:

1. **Remove CPU-stress confounding.** Replace the continuous `--vm-keep` rewrite workload with a resident memory allocator that touches its pages once and sleeps (in the AI cgroup). Keep inference running with the same fixed prompt/model. Measure CPU PSI to verify memory load does not itself create substantial CPU competition.
2. **Create a documented reclaimable working set.** If feasible, use a realistically motivated, file-backed synthetic rule/history dataset in the monitor cgroup which is loaded before a quiet period and accessed only when a test alert must be checked. This tests cold rule pages, rather than relying on a tiny hot Python/bpftrace process that never faults. Do not introduce this silently; clearly label it as a revised workload.
3. **Test swap/pressure safely.** Only if swap and disk resources make this safe in the **disposable VM**; never apply memory stress or swap changes on the macOS host. Use short pilots and guards for OOM and VM responsiveness.
4. **Pre-register the success check.** Compare an unprotected baseline to increasing `memory.low` values only after showing measurable monitor reclaim/faults or sustained monitor memory PSI under the unprotected condition. Keep the full timeline, latency, monitor faults, CPU PSI, AI throughput and OOM events; otherwise stop and conclude the monitor was unaffected in the tested environment.
5. **Find a meaningful threshold.** If a real baseline latency cliff exists, explore 0/16/32/64/128/256 MiB near the measured monitor working set. Run repeated controls with identical pressure and CPU load. Report uncertainty and the actual cost to inference; do not select only the trials that support the hypothesis.

**Alternative if the second experiment cannot produce measurable reclaim:** Finish a rigorous *negative-results* report rather than artificially manufacture slow alerts. Explain that the tested monitor's working set remained resident under the tested conditions and propose realistic future work.

## 8. Technical references

- Linux kernel cgroup v2 memory.low semantics: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- Linux kernel PSI definitions: https://www.kernel.org/doc/html/latest/accounting/psi.html
- stress-ng `--vm-keep` and `--vm-hang` manual: https://man.archlinux.org/man/stress-ng.1

*This audit only contains findings from the uploaded real data and openly identified inferences/next-test proposals. It is not a university report and must not be substituted for actual validation and analysis.*
