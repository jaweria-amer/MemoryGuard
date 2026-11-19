# Phase 3 raw archive audit (completed, separate pilot)

The user-supplied `mg_phase3_results.tar.gz` was opened and independently checked against the original raw files. The archive contains a single completed run, `pilot_20261103T214147Z` (UTC).

## Reverified raw observations

| Phase 3 condition | First alert delay (ms) | Median of 8 delays (ms) | Total rule-lookup major faults | Monitor refaults during attack | AI requests completed during trial | Within 250 ms |
|---|---:|---:|---:|---:|---:|---:|
| No AI, low=0 | 56.746351 | 0.8354635 | 233 | 7055 | 0 | 8/8 |
| No AI, low=192 MiB | 0.341502 | 0.8747555 | 0 | 0 | 0 | 8/8 |
| AI on, low=192 MiB | 0.394545 | 0.340523 | 0 | 0 | 11 | 8/8 |
| AI on, low=0 | 37.022768 | 0.346294 | 232 | 7034 | 10 | 8/8 |

- 32 attack IDs match 32 unique bpftrace alert IDs. Individual alert timestamps are later than their triggers; the per-trial CSVs and `result.json` agree.
- The monitor and parent memory.low values in `cgroup_snapshots.json` matched the assigned protected or unprotected condition. Parent memory.high was 640 MiB. Positive parent `high` event deltas occurred in all four trials.
- No OOM kills or missed eBPF events were reported. Both AI-on trials have completed inference measurements in the concurrent event windows (the source Ollama log includes requests completing between the first and last attack events).
- The source files explicitly identify the rule cache as synthetic, and the reclaim pressure was imposed by a separate file-backed competitor under `memory.high`. This is **not** AI-caused global RAM exhaustion.
- The large differences mainly concern the **first** post-pressure alert. Subsequent lookups are warm; Phase 3 does not provide repeated estimates, statistical uncertainty, or evidence of any missed 250 ms deadline.

**Interpretation:** Strong feasibility evidence for a causal mechanism *within the augmented synthetic experimental construction*, but one trial per setting and fixed order do not identify a repeatable general effect. Phase 4 was developed **after observing this pilot**. Its outcomes are prospectively specified for the new runs, but it is not preregistered independent discovery.
