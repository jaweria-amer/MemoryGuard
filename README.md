# MemoryGuard — Reproducible OS memory-protection research

**Student:** Jaweria Amer Chaudhry (`jc09631`)  
**Course:** Operating Systems; final research project  
**Development environment:** Ubuntu 24.04 (arm64) inside an 8 GiB / 4-vCPU Multipass VM  
**Stage:** experimental data collection complete; report and formal submission subject to instructor review.

## Research question

When a CPU-only local AI model coexists with an eBPF file-open monitoring program, can Linux cgroup-v2 best-effort memory protection (`memory.low`) reduce alert delay under memory pressure, and at what cost to AI inference?

**Important distinction:** The original lightweight-monitor experiment did **not** establish a benefit for a 250 ms detection threshold. The later controlled cache-augmented study did show lower **first, cold-cache** eBPF-triggered alert latency with 192 MiB protection under experimentally imposed **local** cgroup reclaim. It did **not** show improved deadline compliance or AI throughput, and did **not** show that Ollama caused the reclaim pressure. These are separate workloads and must not be pooled as if they were identical implementations.

## Source tree

| Directory | Purpose | Status |
| --- | --- | --- |
| `original_100_trials/` | Original bpftrace/Python monitor, 5 treatment arms × 4 pressures × 5 repeats, 30 harmless file opens per trial | 100/100 experimental trials completed; see archived dataset |
| `phase2_calibration/` | Four local file-backed reclaim calibration runs without eBPF and without AI | Completed; mechanism check only |
| `phase3_integrated_pilot/` | Four eBPF + synthetic-rule-cache runs with/without concurrent Ollama | Completed; exploratory pilot |
| `phase4_repeated_study/` | Eight balanced blocks × 4 conditions; 256 eBPF-triggered alerts | 32/32 trials completed; randomized follow-up |
| `analysis/` | Independent Phase 4 audit script | Re-run against bundled Phase 4 evidence |
| `original_evidence/` | Unmodified Phase 4 raw archive | SHA-256 verified |
| `results_summary/` | Figures, tables and audit interpretation | Derived from actual measured data |

Subproject README files are preserved from the version used at development time and sometimes refer to experiments as *not yet run*. The **completed statuses above** are based on the subsequently collected, independently audited raw data, not on stale forward-looking text in those historical READMEs.

## Setup prerequisites

1. **Do not run memory pressure on the macOS host or on shared/production machines.** Use an isolated Ubuntu 24.04 VM with cgroup v2 and systemd; the scripts may require `sudo` inside the VM.
2. Install Linux packages: `python3`, `bpftrace`, `stress-ng`, `strace`, `curl`; model runtime Ollama; optionally `python3-matplotlib`. Python standard library is used for most analyses.
3. Ensure sufficient disk space for the local `qwen2.5:1.5b` model. The model is prepared **before** timed runs. It is not redistributed in this repository.
4. The **actual completed experiment** used Ubuntu kernel `6.8.0-147-generic`, aarch64/arm64, 4 vCPU, ~8 GiB RAM and **no swap**.

## Inspect code and run safe, offline tests first

```bash
cd original_100_trials
PYTHONPATH=src python3 -m memoryguard.runner --plan
# Linux-specific attack tests require Linux; they fail on macOS because macOS has no prctl().
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Check Phase 4 structure offline:

```bash
cd phase4_repeated_study
python3 -m unittest discover -s tests -v
```

## Reproducing the VM experiments (do not overwrite existing data)

**Original Batch 1:** On macOS, with the explicitly marked disposable VM expected by the original wrapper, inspect `original_100_trials/START_HERE.md` and `scripts/run_mac.sh`. Use `bash scripts/run_mac.sh all` only when starting a **new** VM/test collection and when you accept the extra hardware/time cost. A prior 100-trial batch should be preserved: `bash scripts/run_mac.sh resume` uses the protected resume workflow rather than recreating it.

**Phase 2:** `phase2_calibration/run_from_mac.sh`, as documented in its README; separate four-run calibration.

**Phase 3:** `phase3_integrated_pilot/run_from_mac.sh`, as documented in its README; separate four-condition pilot.

**Phase 4:** `cd phase4_repeated_study && bash run_from_mac.sh new` is for a **new** 32-trial VM experiment. Use `resume`, not `new`, for an interrupted pre-existing Phase 4 study. Never mix or overwrite the historical data archived with this project. The Phase 4 run requires preconditions established by earlier stages; inspect the safety checks in scripts before running.

All file-open 'attacks' operate on harmless fake test paths. The system does not detect general malware or prevent data theft.

## Reproduce reported results without re-stressing hardware

The original Phase 4 evidence archive and its SHA-256 provenance record are bundled in `original_evidence/` and `provenance.json`. To recheck the published results using Python 3.12+ without running memory stress, use:

```bash
python3 analysis/audit_phase4.py
```

The audit independently verifies and summarizes the 32-trial dataset; open `analysis/phase4_provenance.json` for the exact SHA-256 of the historically recorded study.

The `results_summary/` directory includes published aggregate tables and figures for the initial and repeated studies. The full original Batch 1, Phase 2 and Phase 3 archives are retained separately in the accompanying research-evidence folder. The Phase 4 raw archive is bundled so the independent audit can be run offline.

## Reported findings and boundaries

- **Original study:** 100 trials, 3,000 fake-open events, 2,999 within 250 ms; all 600 baseline events were on time. The original monitor suffered no observed major page faults/refaults, so the memory-protection hypothesis was not supported under those test conditions.
- **Augmented repeated study:** 32 trials, 256 events, all 256 on time. Mean first-alert delays in ms (unprotected → 192 MiB protected): no-AI 57.093 → 0.687; Ollama 59.214 → 0.645. Paired block-averaged difference 57.487 ms (95% t CI 45.077–69.897). Reclaim was imposed through a local `memory.high=640 MiB` parent; a synthetic 128 MiB file-backed cache was added to the monitor's rule-check path.
- **AI speed in repeated study:** per-trial median averages 72.257 vs 72.412 tokens/s (unprotected vs protected); difference 0.155 tokens/s, 95% CI −2.467 to 2.777; no demonstrated inference-speed benefit.
- Results concern one VM, one synthetic harmless test pattern and one short experimental session. No generalized malware-detection or deadline-compliance improvement was established.

## Research documentation

Methodology, limitations and independent calculations are provided in the research report, audit notes and results summary. The file-backed rule-cache benchmark is an intentionally augmented experimental workload, not a general-purpose malware detector.

### Primary technical documentation

- Linux kernel, cgroup v2: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- Linux kernel, PSI: https://www.kernel.org/doc/html/latest/accounting/psi.html
- bpftrace file-open tracing: https://github.com/bpftrace/bpftrace/blob/master/docs/tutorial_one_liners.md
- Ollama Linux documentation: https://docs.ollama.com/linux
