# Phase 4 design and analysis protocol (written before Phase 4 VM measurements)

## Research question answered by this follow-up

When a test security monitor carries a preloaded, synthetic 128 MiB file-backed rule cache under **local** cgroup reclaim pressure, does applying `memory.low=192 MiB` lower the first eBPF-file-open alert latency, and how does concurrent CPU-only Ollama influence the outcome?

This is intentionally **narrower** than the user's original proposal about global AI-driven RAM exhaustion. Batch 1 is a separate, already complete negative/near-ceiling result for the original lightweight eBPF monitor.

## Treatments and assignment

Four arms (AI off/on × memory.low 0/192 MiB). Eight balanced blocks; each contains every arm exactly once. A fixed seed (`20261103`) shuffles the order of eight rows from a 4×4 balanced Latin design repeated twice. Every arm appears in each of positions 1–4 twice. Each ordered pair of different arms occurs twice as consecutive within-block conditions. The full sequence is locked into `study_plan.json` before testing.

## Planned measurements

- **Primary Phase 4 latency outcome:** first alert `alert_monotonic_ns - trigger_monotonic_ns` in milliseconds, per trial. Calculate within-block unprotected minus protected, separately for AI off and AI on; present all eight paired differences, median and range. Do not pool 256 event latencies as if they were independent trials.
- **Original primary security criterion retained:** 250 ms deadline, count on-time, late, missed alerts per trial and aggregated by arm. Even if first-alert latency improves, do not report deadline improvement unless the observed rates differ and evidence supports an inference.
- Secondary: event-specific latencies (not just first event), major faults, cache refaults, pgscan, monitor memory-PSI, cgroup high/OOM events, Ollama completions/tokens per second. Compare within arms descriptively.
- Validity gate: 8 matched unique events per trial, 32 trial identities, correct protection limits, positive local `memory.high` event counts, real concurrent inference for AI-on trials, zero OOM kills, and consistency of raw timestamps, CSV, and JSON. Incomplete runs preserved, not silently discarded.

## Interpretation boundaries

A positive paired first-alert difference with faults in unprotected runs would support a cold-cache mechanism under imposed reclaim. It **cannot by itself** show improved 250 ms compliance or prove that Ollama caused reclaim. Mixed or negative effects are valid results. Record all study deviations and failed attempts; no ad hoc adjustment to workload or deadline within the same batch.
