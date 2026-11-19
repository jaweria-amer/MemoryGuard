"""Match triggers with received alerts and report observed performance.

No sample results are shipped. Unobserved alerts count as failures for the
on-time rate but have no numeric latency (we do not invent a delay).
"""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path

DEADLINE_MS = 250.0


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    result = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Malformed JSON in {path}, line {n}") from exc
    return result


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * p / 100
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (position - low)


def summarise(triggers: list[dict], alerts: list[dict], deadline_ms: float = DEADLINE_MS) -> dict:
    if deadline_ms <= 0:
        raise ValueError("Deadline must be positive")
    if not triggers:
        raise ValueError("At least one attack trigger is required")
    trigger_ids = [e["id"] for e in triggers]
    if len(set(trigger_ids)) != len(trigger_ids):
        raise ValueError("Trigger IDs must be unique")
    earliest = {}
    for alert in alerts:
        k = alert["id"]
        n = int(alert["alert_monotonic_ns"])
        if k not in earliest or n < earliest[k]:
            earliest[k] = n
    matched = []
    on_time = 0
    late = 0
    missing = 0
    observed_ms = []
    for trigger in triggers:
        id_ = trigger["id"]
        start = int(trigger["trigger_monotonic_ns"])
        end = earliest.get(id_)
        if end is None:
            missing += 1
            delay = None
            status = "missed"
        else:
            delay = (end - start) / 1_000_000.0
            if delay < 0:
                raise ValueError(f"Alert for {id_} predates trigger; check clocks or matching")
            observed_ms.append(delay)
            if delay <= deadline_ms:
                on_time += 1
                status = "on_time"
            else:
                late += 1
                status = "late"
        matched.append({"id": id_, "delay_ms": delay, "status": status})
    return {
        "total_attacks": len(triggers),
        "on_time": on_time,
        "late": late,
        "missed": missing,
        "on_time_pct": 100.0 * on_time / len(triggers),
        "deadline_ms": deadline_ms,
        "median_observed_delay_ms": statistics.median(observed_ms) if observed_ms else None,
        "p99_observed_delay_ms": percentile(observed_ms, 99),
        "observed_alerts": len(observed_ms),
        "matched": matched,
    }



def numeric_lines(raw: str | None) -> dict[str, int]:
    """Parse kernel's key-number counters from memory.stat/events files."""
    result = {}
    if not raw:
        return result
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return result


def pressure_totals(raw: str | None) -> dict[str, int]:
    """PSI 'total' fields count stalled time in microseconds."""
    result = {}
    if not raw:
        return result
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        for fragment in parts[1:]:
            if fragment.startswith("total="):
                result[parts[0]] = int(fragment.split("=", 1)[1])
    return result


def cgroup_deltas(data: dict) -> dict:
    """Show changes in the same cgroup during an observed trial."""
    summary = {}
    for group in ("monitor", "ai", "parent"):
        begin = data.get(f"{group}_start")
        finish = data.get(f"{group}_end")
        if not begin or not finish:
            continue
        first, last = begin["files"], finish["files"]
        record = {
            "memory_low_bytes": int(first["memory.low"] or "0"),
            "memory_current_start_bytes": int(first["memory.current"] or "0"),
            "memory_current_end_bytes": int(last["memory.current"] or "0"),
        }
        for file, names in (
            ("memory.stat", ("pgmajfault", "pgfault", "workingset_refault_file",
                             "workingset_refault_anon")),
            ("memory.events", ("low", "high", "oom", "oom_kill")),
            ("cpu.stat", ("usage_usec",)),
        ):
            a, b = numeric_lines(first.get(file)), numeric_lines(last.get(file))
            for name in names:
                if name in a and name in b:
                    record[f"{file}_{name}_delta"] = b[name] - a[name]
        for file in ("memory.pressure", "cpu.pressure"):
            a, b = pressure_totals(first.get(file)), pressure_totals(last.get(file))
            for category in ("some", "full"):
                if category in a and category in b:
                    record[f"{file}_{category}_stall_us_delta"] = b[category] - a[category]
        summary[group] = record
    return summary


def analyse_trial(folder: Path, deadline_ms: float = DEADLINE_MS) -> dict:
    result = summarise(read_jsonl(folder / "attacks.jsonl"),
                       read_jsonl(folder / "alerts.jsonl"), deadline_ms)
    info_path = folder / "trial_metadata.json"
    if info_path.exists():
        result["trial"] = json.loads(info_path.read_text(encoding="utf-8"))
    cg_log = folder / "cgroup_snapshots.json"
    if cg_log.exists():
        result["cgroup_changes"] = cgroup_deltas(json.loads(cg_log.read_text(encoding="utf-8")))
    ai_log = folder / "ai_metrics.jsonl"
    if ai_log.exists():
        ai = read_jsonl(ai_log)
        speeds = [float(x["tokens_per_second"]) for x in ai
                  if x.get("tokens_per_second") is not None]
        result["ai_tokens_per_second_median"] = statistics.median(speeds) if speeds else None
        result["ai_completed_requests"] = len(speeds)
    return result


def write_trial_report(folder: Path, deadline_ms: float = DEADLINE_MS) -> dict:
    result = analyse_trial(folder, deadline_ms)
    (folder / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (folder / "matched.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "delay_ms", "status"])
        writer.writeheader()
        writer.writerows(result["matched"])
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", type=Path)
    p.add_argument("--deadline-ms", type=float, default=DEADLINE_MS)
    args = p.parse_args()
    result = write_trial_report(args.folder, args.deadline_ms)
    print(json.dumps({k: v for k, v in result.items() if k != "matched"}, indent=2))


if __name__ == "__main__":
    main()
