# Phase 2 uploaded-archive verification (November 3, 2026)

Input: `mg_phase2_results.tar.gz`, SHA-256
`a4a76f6553ed561dfd58328f0d8fc94b541fc51591bf35ae5a83f63a2dc8ecbf`.
Source files: `phase2_results/calibration_20261103T212631Z/environment.json`,
`calibration_summary.json`, and each `r*/result.json` (including pre/before/after
cgroup snapshots). Results below are from **real user-uploaded observations**.

## Actual observed results

| Trial | `memory.low` | Lookup time ms | Process major faults during lookup | Monitor cgroup file refaults during lookup | Monitor memory after competitor loaded | Parent `memory.high` events after competitor started | OOM kills |
|---|---:|---:|---:|---:|---:|---:|---:|
| r1_baseline | 0 MiB | 90.994656 | 420 | 9,573 | ~6 MiB | 2,203 | 0 |
| r1_low192 | 192 MiB | 0.435963 | 0 | 0 | ~138 MiB | 2,365 | 0 |
| r2_low192 | 192 MiB | 0.173793 | 0 | 0 | ~138 MiB | 2,367 | 0 |
| r2_baseline | 0 MiB | 74.638617 | 422 | 9,606 | ~6 MiB | 2,187 | 0 |

Two-run mean lookup time: 82.8166365 ms unprotected versus 0.304878 ms
protected, ~271.64x ratio. This is an observed effect size **for this
specific synthetic calibration** only, not an estimate from a large study.

## Verification of mechanisms

- Ubuntu 24.04.5, kernel `6.8.0-147-generic`, ~7.7 GiB usable RAM, zero swap.
- All four trials had a 128 MiB file-backed monitor cache and a 512 MiB
  competing file cache, governed by the same parent `memory.high=470 MiB`.
- Monitor and parent both recorded `memory.low` equal to the declared
  0 or 192 MiB before pressure was imposed.
- Baseline monitor memory shrank from ~138 MiB to ~6 MiB before the
  post-pressure lookup; protected monitor stayed around ~138 MiB.
- Baseline monitor recorded cgroup `pgscan` increments exceeding 65,500 pages
  during the pressure setup; protected monitor recorded zero.
- All checks recorded `oom_kill=0`; synthetic checksum was 512 for 512 probes.

## Interpretation boundary

This test demonstrates that the isolated local-reclaim workload can evict
synthetic file-backed rule/cache pages and that best-effort `memory.low`
prevents their eviction under the measured conditions. No security events,
eBPF monitor, or Ollama inference were involved. It does **not** establish
that the user's original lightweight eBPF monitor benefited, that Ollama
caused reclaim, or that any 250 ms deadline was at risk. Only two trials
per condition were run; the result is a mechanism-feasibility observation.

This motivates the separate Phase 3 integrated pilot. The original
100-trial AI/eBPF study remains preserved as Batch 1 and should be reported
without alteration, including its near-perfect unprotected on-time rate.
