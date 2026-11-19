"""Verify recorded trial coverage and evidence before trusting final plots.

The checks do not fabricate or modify any observations. Pressure is measured
from cgroup PSI stall-time deltas, NOT from the stress-ng allocation setting.
"""
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


def inspect_batch(batch: Path, mode: str) -> dict:
    env_path = batch / "environment.json"
    if not env_path.is_file():
        raise FileNotFoundError(f"No environment.json in {batch}")
    env = json.loads(env_path.read_text(encoding="utf-8"))
    pressures = list(env["pressures_mib"])
    conditions = [c["name"] for c in env["condition_config"]]
    repeats = int(env["repeats"])
    expected = len(pressures) * len(conditions) * repeats
    rows, problems = [], []
    high_psi_found = False
    oom_events = 0
    for repeat in range(1, repeats + 1):
        for pressure in pressures:
            for condition in conditions:
                trial_path = batch / f"r{repeat}_p{pressure}_{condition}"
                file = trial_path / "summary.json"
                if not file.is_file():
                    problems.append(f"Missing: {file}")
                    continue
                data = json.loads(file.read_text(encoding="utf-8"))
                trial = data.get("trial", {})
                if not trial.get("experimental_valid", False):
                    problems.append(f"Unvalidated trial: {trial_path}")
                    continue
                if data.get("total_attacks") != env["attacks_per_trial"]:
                    problems.append(f"Wrong attack count: {trial_path}")
                if data.get("on_time", 0) + data.get("late", 0) + data.get("missed", 0) != data.get("total_attacks"):
                    problems.append(f"Event accounting failure: {trial_path}")
                if data.get("ai_completed_requests", 0) < 1:
                    problems.append(f"No measured AI speed: {trial_path}")
                changes = data.get("cgroup_changes", {})
                # Verify measured pressure in the AI group or the study's parent.
                stall = max(
                    int(changes.get(group, {}).get("memory.pressure_some_stall_us_delta", 0))
                    for group in ("ai", "parent")
                )
                if pressure == max(pressures) and stall > 0:
                    high_psi_found = True
                oom = sum(max(0, int(changes.get(group, {}).get("memory.events_oom_kill_delta", 0)))
                          for group in ("ai", "monitor"))
                oom_events += oom
                rows.append({
                    "repeat": repeat, "pressure_mib": pressure, "condition": condition,
                    "attacks": data.get("total_attacks"), "on_time": data.get("on_time"),
                    "late": data.get("late"), "missed": data.get("missed"),
                    "on_time_pct": data.get("on_time_pct"),
                    "observed_p99_ms": data.get("p99_observed_delay_ms"),
                    "ai_tokens_sec": data.get("ai_tokens_per_second_median"),
                    "measured_psi_stall_us": stall,
                    "oom_kills": oom,
                })
    if len(rows) != expected:
        problems.append(f"Expected {expected} valid trials, found {len(rows)}")
    if not high_psi_found:
        problems.append("No recorded memory PSI stalls at highest pressure. The intended pressure is unverified; recalibrate before drawing conclusions.")
    if oom_events:
        problems.append(f"{oom_events} OOM kill(s) in AI/monitor groups; lower pressure or document exclusion/re-run.")
    if mode == "full" and expected != 100:
        problems.append(f"Expected a 100-trial final design, got {expected} trials")
    if mode == "smoke" and expected != 4:
        problems.append(f"Expected a 4-trial smoke design, got {expected} trials")

    batch.mkdir(exist_ok=True, parents=True)
    if rows:
        with (batch / "trial_table.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        group = defaultdict(list)
        for row in rows:
            group[(row["condition"], row["pressure_mib"])].append(row)
        with (batch / "condition_table.csv").open("w", newline="", encoding="utf-8") as f:
            cols = ["condition", "pressure_mib", "trials", "total_events", "on_time_events",
                    "on_time_pct_pooled", "median_trial_on_time_pct", "median_observed_p99_ms",
                    "median_ai_tokens_sec", "median_psi_stall_us"]
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            for (cond, pressure), r in sorted(group.items()):
                def med(key):
                    vals = [x[key] for x in r if x[key] is not None]
                    return statistics.median(vals) if vals else None
                total = sum(x["attacks"] for x in r)
                writer.writerow({"condition": cond, "pressure_mib": pressure, "trials": len(r),
                                 "total_events": total, "on_time_events": sum(x["on_time"] for x in r),
                                 "on_time_pct_pooled": 100*sum(x["on_time"] for x in r)/total,
                                 "median_trial_on_time_pct": med("on_time_pct"),
                                 "median_observed_p99_ms": med("observed_p99_ms"),
                                 "median_ai_tokens_sec": med("ai_tokens_sec"),
                                 "median_psi_stall_us": med("measured_psi_stall_us")})
    report = {"mode": mode, "batch": str(batch), "expected_trials": expected,
              "valid_trials": len(rows), "high_pressure_psi_observed": high_psi_found,
              "oom_kills": oom_events, "passed": not problems, "problems": problems,
              "interpretation_warning": "A passing QC check does not prove that memory.low improves latency. Analyze effects and uncertainty without assuming the hypothesis."}
    (batch / "qc.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("batch", type=Path)
    p.add_argument("--mode", choices=("smoke", "full"), required=True)
    a = p.parse_args()
    result = inspect_batch(a.batch, a.mode)
    print(json.dumps(result, indent=2), flush=True)
    if not result["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
