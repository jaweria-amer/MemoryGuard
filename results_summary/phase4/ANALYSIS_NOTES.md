# MemoryGuard Phase 4 — Independent Raw-Data Audit and Research Results

**Study:** `study_20261103T221415Z`  
**Source archive SHA-256:** `8e1b7a3fbb754e872bf35f469999f8eac652699a2c738c5062c2ba8e55ba6bba`  
**Study code SHA-256:** `67a6c9228c6f5327c5078580add26164705e4fa0a37e1e7705350e3c8c756c1d` (matched to the original Phase 4 package)  
**Status:** Technical integrity verified, interpretive limitations remain.

## Executive result

The source archive contains **32 complete trials**, organized into **eight randomized, position-counterbalanced four-condition blocks**. Every trial generated eight harmless simulated secret-file opens, and all **256 events produced matching eBPF-triggered user-space alerts**. No alert exceeded **250 ms**, and no OOM kill was reported. Every paired block showed lower *first-alert* latency with `memory.low = 192 MiB` than without it, both with and without concurrent Ollama.

The results support a **large, repeatable reduction in cold-cache first-alert latency under forced local cgroup reclaim** for this **augmented synthetic rule-cache monitor**. They do **not** show a change in on-time detection rate, performance on real malicious activity, or a benefit for the original lightweight monitor without the synthetic rule cache.

## Experimental configuration

- Ubuntu 24.04, Linux kernel 6.8.0-147-generic, 7.73 GiB allocated RAM, **no swap**.
- Monitor synthetic file-backed rule cache: **128 MiB**; each alert checks **512** mapped pages; competing cache: **512 MiB**.
- Separate parent cgroup `memory.high = 640 MiB`; protection setting `memory.low = 0 or 192 MiB` on monitor and parent cgroups.
- CPU-only Ollama `qwen2.5:1.5b`, enabled or disabled as a distinct factorial condition.
- Eight blocks, each with four conditions once; order seed **20261103** and counterbalanced treatment positions. Each trial contained eight harmless file-open probes at **1.0 s** intervals.
- Outcome timing: monotonic clock from attack trigger to completion of eBPF-triggered Python rule checking and alert timestamp. The event stream is a genuine `bpftrace` file-open trace; the expensive rule-cache lookup is a **synthetic augmentation**, not a real-world threat classification.

## Verified first-alert findings

| Condition | Trials | Mean first-alert (ms) | Median (ms) | Range (ms) | First-event major faults |
|---|---:|---:|---:|---:|---:|
| No AI, unprotected | 8 | 57.093 | 45.824 | 42.799–81.480 | 231–238 |
| No AI, protected 192 MiB | 8 | 0.687 | 0.596 | 0.410–1.441 | 0 |
| Ollama, unprotected | 8 | 59.214 | 60.143 | 30.306–89.242 | 231–240 |
| Ollama, protected 192 MiB | 8 | 0.645 | 0.313 | 0.280–1.399 | 0 |

Averages are at the **trial level**, not a pooled average of 256 non-independent alerts.

### Paired protection effects

The pre-specified latency endpoint for this follow-up is first-alert delay in milliseconds. Difference is **unprotected minus protected**; positive is a lower observed latency with protection.

| Contrast | Blocks | Mean difference | 95% t interval (df = 7) | Directional consistency | Exact 2-sided sign-test p |
|---|---:|---:|---:|---:|---:|
| No AI | 8 | 56.405 ms | [41.956, 70.854] ms | 8/8 | 0.00781 |
| Ollama running | 8 | 58.569 ms | [37.744, 79.394] ms | 8/8 | 0.00781 |
| Mean of both within each block | 8 | 57.487 ms | [45.077, 69.897] ms | 8/8 | 0.00781 |

These intervals use eight block-level paired differences (Student t, n=8); they should be interpreted cautiously because all blocks ran in **one VM session** and the sample is modest. Exact sign tests treat the eight blocks as independent directional units and are descriptive, not a guarantee of replication in another host. The two strata are **not** blindly counted as 16 independent experimental blocks.

Ratios of group mean first-alert delays: approximately **83.1×** lower without AI and **91.8×** lower with AI. These ratios describe this controlled workload only; they are not a general system speed-up.

## Mechanism: observed reclaim and page faults

| Condition | First-event major faults, sum over 8 trials | File refaults during events | Monitor memory PSI stall, sum | Parent `memory.high` events before attacks |
|---|---:|---:|---:|---:|
| No AI, unprotected | 1,859 | 56,356 | 160,038 μs | 16,192 |
| No AI, protected | 0 | 0 | 0 μs | 16,441 |
| Ollama, unprotected | 1,860 | 56,378 | 130,420 μs | 16,426 |
| Ollama, protected | 0 | 0 | 0 μs | 16,394 |

Parent `memory.high` events occurred in **every condition**, demonstrating the imposed local memory-pressure mechanism. The protected monitor had **zero major page faults, zero measured file refaults, and zero accumulated memory PSI stall** during attack windows in these trials. The unprotected monitor faulted during first lookup, matching its long first-alert times.

The source of reclaim here is **intentionally imposed cgroup `memory.high`**, *not* demonstrated to be Ollama or global system pressure. This is why this synthetic mechanism study must not be equated with the original 100-trial natural-load study.

## 250 ms deadline outcome and warm-cache behavior

All **256 of 256** alerts met the 250 ms deadline; there were no missed alerts, no late alerts, and no OOM kills. Therefore, **the data provide no measured improvement in deadline compliance**, even though first-alert latency improved substantially.

The difference is concentrated in the first, cold-cache event. Later events re-use pages brought into memory by the first lookup. The median of events 2–8 per arm was **0.977 ms** (no AI/unprotected), **0.986 ms** (no AI/protected), **0.317 ms** (Ollama/unprotected), and **0.328 ms** (Ollama/protected).

**Outlier disclosure:** During trial `b08_ai_low192`, event 3 took **47.806 ms** despite **zero major faults**; its synthetic rule lookup itself took **17.194 ms**. Thus, memory protection does **not** eliminate all other scheduling, processing, or buffering delays. The cause of this isolated latency spike was not independently established.

## Concurrent Ollama evidence

The 16 AI-on trials had **164 inference completions** counted within trial windows. Using the source API request timestamps, an Ollama inference request was active at the trigger of **all 128/128 AI-enabled attack events**, including every first event. Therefore, AI concurrency is verified, not merely an enabled flag.

The mean *per-trial median* generation rate was **72.257 tokens/s** for unprotected AI trials and **72.412 tokens/s** for protected AI trials. The paired AI-throughput difference (protected minus unprotected) was **0.155 tokens/s**, 95% t interval **[-2.467, 2.777]**. This study does **not** demonstrate a change in Ollama throughput caused by protecting the monitor.

The difference in the estimated first-alert protection effect between Ollama on versus off was **2.164 ms**, 95% paired-block t interval **[-23.700, 28.027]**. That wide interval is consistent with meaningful uncertainty about any AI-specific interaction; do not claim that Ollama amplifies or has no effect on the protection benefit.

## Integrity checks performed

1. Loaded archive and checked 32 unique condition labels, eight complete blocks, the four treatments in every block, and balance of eight trials per treatment.
2. Recomputed all 256 alert delays from independent `attacks.jsonl` and `alerts.jsonl` monotonic timestamp differences and compared them with per-trial `events.csv` and study-level `event_metrics.csv`.
3. Verified one-to-one attack/alert IDs, deadline labels, first-alert selection, per-event major-fault counts, reported trial outcome counts, and absence of OOM kills.
4. Compared raw `cgroup_snapshots.json` across phases to verify configured `memory.low`, local `memory.high`, reclaim scans, fault counts, refaults, memory PSI, and `memory.high` event deltas.
5. Recomputed per-trial Ollama request counts and median tokens/s from AI-session logs. Verified actual inference overlap with each of the 128 AI-on attack triggers.
6. Matched the original Phase 4 Python source code fingerprint to `study_plan.json` (SHA-256 `67a6c9228c6f5327c5078580add26164705e4fa0a37e1e7705350e3c8c756c1d`).
7. Calculated paired mean differences, small-sample confidence intervals, exact sign tests, and AI interaction at the **block** level.

## Limits on causal and external claims

- The workload uses a **purpose-built 128 MiB file-backed rule cache** and probes 512 mapped pages per event. The initial 100-trial lightweight monitor did **not** have this cache, and its baseline already met nearly all 250 ms deadlines.
- Reclaim is introduced with **local `memory.high`** in a separate test cgroup. This demonstrates a Linux reclaim-protection mechanism under these conditions but not a natural production workload or an Ollama-generated global memory crisis.
- The fake secret-file opens are harmless synthetic activity; detection rate here cannot be generalized to malicious attacks, exfiltration, malware detection, or practical operational security.
- Eight blocks were counterbalanced to reduce order bias, but all were conducted on one machine/VM session. Long-term, cross-kernel and cross-hardware reproducibility remain untested.
- The measured benefit is predominantly in a cold-cache first alert, not in warmed later alerts. There is an unexplained 47.806 ms protected warm-event outlier.
- All 256 alerts met the 250 ms threshold, so the deadline outcome cannot distinguish the treatments.
- `memory.low` is best-effort reclaim protection; its behavior under other cgroup hierarchies or severe global pressure is not established by this one study.

## Defensible research conclusion

> Under deliberately induced cgroup-local reclaim, protecting a synthetic, file-backed security-monitor rule cache using `memory.low = 192 MiB` was associated with consistently shorter **first** eBPF-triggered alert latency in eight counterbalanced blocks, both with and without concurrent CPU-only Ollama inference. The observed reduction was accompanied by the disappearance of monitor page faults and file refaults. All alerts remained within the 250 ms deadline, so improved deadline compliance was not demonstrated. Findings are specific to this augmented cache-heavy monitor and do not establish a benefit for the initial lightweight monitor or general production systems.

## Generated figures and tables

- `figures/01_first_alert_distribution.png` — trial-level first-alert distribution by arm.
- `figures/02_paired_block_effect.png` — observed paired protection advantage by experimental block.
- `figures/03_cold_vs_warm_medians.png` — difference between first and later alerts.
- `figures/04_ollama_throughput.png` — observed AI generation speed by protection state.
- `tables/trial_verified.csv` — each trial and corroborated cgroup evidence.
- `tables/event_verified.csv` — all 256 individually matched and timed alerts.
- `tables/paired_blocks.csv` — per-block paired first-alert comparisons.
- `tables/arm_summary.csv` — descriptive results for each of four arms.
- `tables/effect_estimates.csv` — paired mean contrasts, confidence intervals and exact sign tests.

The accompanying `original_evidence/mg_phase4_results.tar.gz` contains the user-provided source data unmodified. The `reproducibility/audit_phase4.py` script can recompute the main analysis without installing the original research application.
