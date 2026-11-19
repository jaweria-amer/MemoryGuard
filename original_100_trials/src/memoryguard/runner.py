"""Start isolated monitor/AI services and collect real trial measurements.

WARNING: Ubuntu VM only. This uses root privileges and can intentionally
create heavy memory pressure. Never run it on the macOS host or a work server.
"""
import argparse
import traceback
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .analysis import write_trial_report
from .attack import generate

ROOT = Path(__file__).resolve().parents[2]
SLICE = "memoryguard.slice"


@dataclass(frozen=True)
class Condition:
    name: str
    low_mb: int = 0
    cpu_weight: int = 100
    nice: int = 0


CONDITIONS = [
    Condition("baseline", 0),
    Condition("cpu_priority", 0, 1000, -10),
    Condition("low_64m", 64),
    Condition("low_128m", 128),
    Condition("low_256m", 256),
]


def run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def check_environment(require_ai: bool = True) -> None:
    if platform.system() != "Linux":
        raise RuntimeError("This experiment must run inside an Ubuntu Linux VM")
    if os.geteuid() != 0:
        raise RuntimeError("Run this script with sudo inside the test VM")
    needed = ("systemctl", "systemd-run", "bpftrace")
    if require_ai:
        needed += ("stress-ng", "ollama")
    for executable in needed:
        if not shutil.which(executable):
            raise RuntimeError(f"Missing program: {executable}")
    controllers = Path("/sys/fs/cgroup/cgroup.controllers")
    if not controllers.exists() or "memory" not in controllers.read_text():
        raise RuntimeError("cgroup v2 memory controller is unavailable")
    if run_cmd(["systemctl", "is-system-running"], check=False).returncode not in (0, 1):
        raise RuntimeError("Systemd is not available as the VM's service manager")
    if not Path("/sys/kernel/tracing/events/syscalls/sys_enter_openat").exists() and not Path(
            "/sys/kernel/debug/tracing/events/syscalls/sys_enter_openat").exists():
        print("Warning: openat tracepoint path not visible. Verify with bpftrace -l.", file=sys.stderr)


def wait_for_status(path: Path, timeout: float, service: str) -> dict:
    expires = time.monotonic() + timeout
    while time.monotonic() < expires:
        if path.exists():
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                time.sleep(0.2)
                continue
            if status.get("state") == "ready":
                return status
            if status.get("state") in ("error", "exited", "stopped"):
                raise RuntimeError(f"{service} did not become ready: {status}")
        active = run_cmd(["systemctl", "is-active", service], check=False).stdout.strip()
        if active in ("failed", "inactive"):
            raise RuntimeError(f"{service} unexpectedly stopped; use journalctl -u {service}")
        time.sleep(0.25)
    raise TimeoutError(f"No ready status from {service} within {timeout} seconds")


def unit_cgroup(unit: str) -> Path:
    r = run_cmd(["systemctl", "show", "-p", "ControlGroup", "--value", unit])
    value = r.stdout.strip()
    if not value.startswith("/") or value == "/":
        raise RuntimeError(f"Missing cgroup path for unit {unit}: {value!r}")
    return Path("/sys/fs/cgroup") / value.lstrip("/")


def memory_snapshot(unit: str) -> dict:
    cg = unit_cgroup(unit)
    data = {"control_group": str(cg), "files": {}}
    for name in ("memory.low", "memory.current", "memory.stat", "memory.events",
                 "memory.pressure", "cpu.pressure", "cpu.stat"):
        file = cg / name
        data["files"][name] = file.read_text(encoding="utf-8") if file.exists() else None
    return data


def start_service(unit: str, script: str, output: Path,
                  condition: Condition | None, model: str, model_dir: Path,
                  pressure_mb: int) -> None:
    cmd = ["systemd-run", f"--unit={unit}", f"--slice={SLICE}", "--collect",
           "--property=MemoryAccounting=yes"]
    if script == "monitor":
        assert condition is not None
        cmd += [f"--property=MemoryLow={condition.low_mb}M",
                f"--property=CPUWeight={condition.cpu_weight}",
                f"--property=Nice={condition.nice}",
                sys.executable, "-u", str(ROOT / "src/memoryguard/monitor.py"),
                "--output", str(output)]
    else:
        cmd += [sys.executable, "-u", str(ROOT / "src/memoryguard/ai_load.py"),
                "--output", str(output), "--model", model,
                "--model-dir", str(model_dir), "--pressure-mb", str(pressure_mb)]
    run_cmd(cmd)


def read_version(program: str, arg: str = "--version") -> str:
    try:
        result = run_cmd([program, arg], check=False)
        return (result.stdout or result.stderr).strip()[:300]
    except OSError:
        return "unavailable"


def run_trial(out: Path, condition: Condition, pressure_mb: int, repeat: int,
              model: str, model_dir: Path, attacks: int, interval: float,
              warmup_seconds: float, monitor_only: bool = False) -> dict:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Trial directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    unique = hashlib.sha256(str(out.resolve()).encode("utf-8")).hexdigest()[:12]
    mon = f"mg-monitor-{unique}.service"
    ai = f"mg-ai-{unique}.service"
    running = []
    start = time.time()
    trial_id = f"mg{unique}"
    metadata = {"trial_id": trial_id, "condition": condition.name,
                "memory_low_mb": condition.low_mb, "cpu_weight": condition.cpu_weight,
                "nice": condition.nice, "pressure_mb": pressure_mb, "repeat": repeat,
                "model": None if monitor_only else model, "attack_count": attacks,
                "deadline_ms": 250, "kernel": platform.release(),
                "architecture": platform.machine(),
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "experimental_valid": False}
    (out / "trial_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    try:
        # Ensure parent memory.low is not smaller than monitor memory.low.
        run_cmd(["systemctl", "set-property", "--runtime", SLICE,
                 f"MemoryLow={condition.low_mb}M", "MemoryAccounting=yes"])
        start_service(mon, "monitor", out, condition, model, model_dir, pressure_mb)
        running.append(mon)
        wait_for_status(out / "monitor_status.json", 45, mon)
        if not monitor_only:
            start_service(ai, "ai", out, None, model, model_dir, pressure_mb)
            running.append(ai)
            wait_for_status(out / "ai_status.json", 180, ai)
            time.sleep(warmup_seconds)
        snaps = {"monitor_start": memory_snapshot(mon),
                 "parent_start": memory_snapshot(SLICE)}
        if not monitor_only:
            snaps["ai_start"] = memory_snapshot(ai)
        actual_low = snaps["monitor_start"]["files"]["memory.low"]
        if actual_low is None or int(actual_low.strip()) != condition.low_mb * 1024 * 1024:
            raise RuntimeError(f"Monitor memory.low mismatch: expected {condition.low_mb} MiB, actual {actual_low!r}")
        # The parent slice must also have at least the expected protection.
        parent_low = snaps["parent_start"]["files"]["memory.low"]
        if parent_low is None or int(parent_low.strip()) < condition.low_mb * 1024 * 1024:
            raise RuntimeError(f"Parent memory.low is too small: {parent_low!r}")
        generate(trial_id, out, attacks, interval)
        time.sleep(3.0)  # Let queued monitor events reach the rule checker.
        if not monitor_only:
            ai_status = json.loads((out / "ai_status.json").read_text(encoding="utf-8"))
            if ai_status.get("state") != "ready":
                raise RuntimeError(f"AI workload ended before trial finished: {ai_status}")
        mon_status = json.loads((out / "monitor_status.json").read_text(encoding="utf-8"))
        if mon_status.get("state") != "ready":
            raise RuntimeError(f"Monitor ended before trial finished: {mon_status}")
        snaps["monitor_end"] = memory_snapshot(mon)
        snaps["parent_end"] = memory_snapshot(SLICE)
        if not monitor_only:
            snaps["ai_end"] = memory_snapshot(ai)
        (out / "cgroup_snapshots.json").write_text(json.dumps(snaps, indent=2), encoding="utf-8")
        # Stop services before parsing. Files are line-flushed per event.
    except Exception as exc:
        (out / "trial_error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n\n" + traceback.format_exc(), encoding="utf-8")
        for unit in running:
            journal = run_cmd(["journalctl", "--no-pager", "-u", unit, "-n", "90"], check=False)
            (out / f"{unit}.journal.log").write_text(
                journal.stdout + "\n" + journal.stderr, encoding="utf-8")
        raise
    finally:
        for unit in reversed(running):
            run_cmd(["systemctl", "stop", unit], check=False)
        run_cmd(["systemctl", "set-property", "--runtime", SLICE,
                 "MemoryLow=0", "MemoryAccounting=yes"], check=False)
    result = write_trial_report(out)
    # If no events arrived in a zero-pressure pilot, tracing is not functioning.
    if result["observed_alerts"] == 0:
        raise RuntimeError(f"No alerts observed. Do not use {out} as a valid result.")
    if not monitor_only and result.get("ai_completed_requests", 0) == 0:
        raise RuntimeError(f"No AI throughput metrics collected. Do not use {out} as a valid result.")
    metadata["duration_seconds"] = round(time.time() - start, 2)
    metadata["experimental_valid"] = True
    (out / "trial_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    result = write_trial_report(out)
    print(f"{out.name}: on-time {result['on_time_pct']:.1f}%, "
          f"late={result['late']} missed={result['missed']}", flush=True)
    return result



def read_saved_json(path: Path) -> dict:
    """Read validated metadata, never infer success from a folder name."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def verify_resume_environment(batch: Path, levels: list[int],
                              repeats: int, attacks: int, model: str) -> dict:
    """Guard against combining runs made with different configurations."""
    root_results = (ROOT / "results").resolve()
    resolved = batch.resolve()
    if resolved.parent != root_results or not resolved.name.startswith("batch_"):
        raise ValueError("Resume path must be one existing results/batch_* directory in this VM")
    if not resolved.is_dir():
        raise FileNotFoundError(f"Original batch directory not found: {resolved}")
    env = read_saved_json(resolved / "environment.json")
    expected = {
        "experiment_type": "full", "pressures_mib": levels,
        "repeats": repeats, "attacks_per_trial": attacks,
        "condition_config": [vars(x) for x in CONDITIONS],
        "architecture": platform.machine(), "kernel": platform.release(),
    }
    for key, value in expected.items():
        if env.get(key) != value:
            raise RuntimeError(f"Resume refused: environment.json {key} differs: "
                               f"saved={env.get(key)!r}; requested={value!r}")
    # v0.2 batches lack a 'model' field; check it in every valid trial instead.
    if "model" in env and env["model"] != model:
        raise RuntimeError("Resume refused: different AI model")
    return env


def verify_finished_trial(out: Path, cond: Condition, pressure: int,
                          repeat: int, attacks: int, model: str) -> bool:
    """Accept a prior result only if its evidence and configuration agree.

    Returns False for an incomplete attempt; conflicting claimed-valid data
    cause an error instead of being silently overwritten.
    """
    if not out.exists():
        return False
    if not out.is_dir():
        raise RuntimeError(f"Existing trial path is not a directory: {out}")
    metadata_path = out / "trial_metadata.json"
    if not metadata_path.is_file():
        return False
    try:
        metadata = read_saved_json(metadata_path)
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"Unparseable existing trial metadata at {out}: {exc}") from exc
    if not metadata.get("experimental_valid", False):
        return False
    require = ("summary.json", "attacks.jsonl", "alerts.jsonl", "ai_metrics.jsonl",
               "cgroup_snapshots.json", "matched.csv")
    for filename in require:
        if not (out / filename).is_file():
            raise RuntimeError(f"Claimed-valid trial missing {filename}: {out}")
    summary = read_saved_json(out / "summary.json")
    expected = {"condition": cond.name, "memory_low_mb": cond.low_mb,
                "cpu_weight": cond.cpu_weight, "nice": cond.nice,
                "pressure_mb": pressure, "repeat": repeat, "model": model,
                "attack_count": attacks, "deadline_ms": 250}
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise RuntimeError(f"Claimed-valid trial mismatch {key}: {out} "
                               f"{metadata.get(key)!r} != {value!r}")
    summary_trial = summary.get("trial", {})
    if summary_trial.get("trial_id") != metadata.get("trial_id") or not summary_trial.get("experimental_valid"):
        raise RuntimeError(f"Invalid summary identity for claimed-valid trial: {out}")
    if summary.get("total_attacks") != attacks or sum(
            int(summary.get(key, 0)) for key in ("on_time", "late", "missed")) != attacks:
        raise RuntimeError(f"Invalid event counts for claimed-valid trial: {out}")
    if (int(summary.get("observed_alerts", 0)) < 1 or
            int(summary.get("ai_completed_requests", 0)) < 1):
        raise RuntimeError(f"No valid alert or AI throughput evidence: {out}")
    monitor = summary.get("cgroup_changes", {}).get("monitor", {})
    if monitor.get("memory_low_bytes") != cond.low_mb * 1024 * 1024:
        raise RuntimeError(f"Monitor protection mismatch in saved trial: {out}")
    return True


def archive_incomplete_attempt(out: Path, batch: Path) -> Path:
    """Move incomplete evidence aside, never delete it before retrying."""
    target_dir = batch.parent / "_failed_attempts" / batch.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (out.name + ".prior_attempt")
    suffix = 1
    while target.exists():
        target = target_dir / (out.name + f".prior_attempt_{suffix}")
        suffix += 1
    out.rename(target)
    return target


def record_resume_event(batch: Path, event: dict) -> None:
    event = dict(event)
    event["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    with (batch / "resume_history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, default=ROOT / "results")
    p.add_argument("--model", default="qwen2.5:1.5b")
    p.add_argument("--model-dir", type=Path, default=Path("/var/lib/memoryguard/models"))
    p.add_argument("--plan", action="store_true", help="Print planned conditions without running anything")
    p.add_argument("--pilot-monitor", action="store_true", help="5 fake opens, eBPF only; no AI")
    p.add_argument("--pilot-full", action="store_true", help="5 fake opens with Ollama, no stress")
    p.add_argument("--smoke", action="store_true", help="Four short end-to-end trials at low/high pressure")
    p.add_argument("--all", action="store_true", help="Run the planned repeated comparisons")
    p.add_argument("--resume-batch", type=Path, help="Continue a stopped full batch without rerunning valid trials")
    p.add_argument("--pressures", default="0,1024,2048,3072", help="4 calibrated MiB levels")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--attacks", type=int, default=30)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--warmup", type=float, default=10.0)
    args = p.parse_args()
    chosen = sum([args.plan, args.pilot_monitor, args.pilot_full, args.smoke, args.all, bool(args.resume_batch)])
    if chosen != 1:
        p.error("Choose exactly one of --plan, --pilot-monitor, --pilot-full, --smoke, --all or --resume-batch")
    if args.plan:
        levels = [int(x.strip()) for x in args.pressures.split(",")]
        if len(levels) != 4 or len(set(levels)) != 4 or args.repeats < 1:
            p.error("Plan requires exactly four distinct pressure levels and positive repeats")
        matrix = [{"repeat": r, "pressure_mb": pressure, "condition": vars(condition)}
                  for r in range(1, args.repeats + 1)
                  for pressure in levels for condition in CONDITIONS]
        print(json.dumps({"total_planned_trials": len(matrix), "trials": matrix}, indent=2))
        return
    check_environment(require_ai=not args.pilot_monitor)
    args.results.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.pilot_monitor or args.pilot_full:
        condition = CONDITIONS[0]
        out = args.results / ("pilot_monitor_" if args.pilot_monitor else "pilot_full_") / name
        run_trial(out, condition, 0, 1, args.model, args.model_dir,
                  5 if args.pilot_monitor else 10,
                  1 if args.pilot_monitor else 2,
                  0 if args.pilot_monitor else 5,
                  monitor_only=args.pilot_monitor)
        return
    pressures = [int(x.strip()) for x in args.pressures.split(",")]
    if len(pressures) != 4 or len(set(pressures)) != 4 or min(pressures) < 0:
        p.error("Provide exactly four distinct nonnegative pressure levels in MiB")
    if args.repeats < 1 or args.attacks < 1:
        p.error("--repeats and --attacks must be positive")
    smoke = args.smoke
    resuming = args.resume_batch is not None
    chosen_pressures = [min(pressures), max(pressures)] if smoke else pressures
    chosen_conditions = [CONDITIONS[0], CONDITIONS[3]] if smoke else CONDITIONS
    chosen_repeats = 1 if smoke else args.repeats
    chosen_attacks = min(args.attacks, 20) if smoke else args.attacks
    chosen_interval = args.interval
    if resuming:
        batch = args.resume_batch.resolve()
        verify_resume_environment(batch, chosen_pressures, chosen_repeats,
                                  chosen_attacks, args.model)
        print(f"RESUMING EXISTING BATCH (do not remove): {batch}", flush=True)
        record_resume_event(batch, {"action": "resume_start", "model": args.model})
    else:
        batch = args.results / f"{'smoke' if smoke else 'batch'}_{name}"
        batch.mkdir()
        (batch / "environment.json").write_text(json.dumps({
            "kernel": platform.release(), "architecture": platform.machine(),
            "bpftrace": read_version("bpftrace", "-V"),
            "ollama": read_version("ollama"),
            "stress_ng": read_version("stress-ng"),
            "pressures_mib": chosen_pressures, "repeats": chosen_repeats,
            "attacks_per_trial": chosen_attacks,
            "model": args.model,
            "experiment_type": "smoke" if smoke else "full",
            "condition_config": [vars(c) for c in chosen_conditions],
        }, indent=2), encoding="utf-8")
    # Interleave conditions to reduce changes in temperature and cache state
    # from being entirely confounded with a single condition.
    try:
        for repeat in range(1, chosen_repeats + 1):
            for pressure_mb in chosen_pressures:
                for cond in chosen_conditions:
                    out = batch / f"r{repeat}_p{pressure_mb}_{cond.name}"
                    if resuming and verify_finished_trial(out, cond, pressure_mb,
                                                          repeat, chosen_attacks, args.model):
                        print(f"KEEP verified existing trial: {out.name}", flush=True)
                        continue
                    if resuming and out.exists():
                        archived = archive_incomplete_attempt(out, batch)
                        record_resume_event(batch, {"action": "archived_failed_attempt",
                                                    "trial": out.name, "location": str(archived)})
                        print(f"ARCHIVED incomplete attempt at {archived}", flush=True)
                    if resuming:
                        record_resume_event(batch, {"action": "retry_trial", "trial": out.name})
                    run_trial(out, cond, pressure_mb, repeat,
                              args.model, args.model_dir, chosen_attacks,
                              chosen_interval, args.warmup)
                    if resuming:
                        record_resume_event(batch, {"action": "completed_trial", "trial": out.name})
                    time.sleep(4)  # Short recovery between trials
    except Exception:
        print("Batch stopped after error. Earlier real trial logs were preserved.", file=sys.stderr)
        raise
    print(f"Batch complete: {batch}")


if __name__ == "__main__":
    main()
