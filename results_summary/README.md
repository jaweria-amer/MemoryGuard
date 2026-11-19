# Published results summary

These files are derived from the recorded MemoryGuard research measurements, not synthetic illustrative data.

- `batch1/`: original 100-trial lightweight monitor (3,000 harmless events; 2,999 within 250 ms). No demonstrated deadline benefit from `memory.low`; memory reclaim was not observed in the monitor's working set.
- `phase4/`: later 32-trial integrated follow-up with a deliberately added synthetic file-backed rule cache, eight counterbalanced blocks, 256 alerts (all within 250 ms). The observed improvement concerns **first cold-cache alert latency**, not deadline compliance or general malware detection.

These studies do not use identical monitor implementations and must not be pooled as if they did. The Phase 4 original raw tarball is in `../original_evidence/`; the audit verifies its checksum and all 256 alert timestamps. Phase 2 and Phase 3 are calibration/pilot studies, and their complete evidence is archived separately.
