"""bpftrace -> Python rule checker -> timestamped alert log.

This program is meant for a disposable Ubuntu VM where bpftrace can attach
kernel tracepoints. It must be started before the harmless attack generator.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# Restrict tracing to the specifically named harmless lab process.
# bpftrace is quiet except for its own output; no system-wide file log.
TRACE_PROGRAM = r'''
BEGIN { printf("MG_READY\n"); }
tracepoint:syscalls:sys_enter_openat
/comm == "mg_attack"/
{
    printf("MG_OPEN %s\n", str(args.filename));
}
'''
EVENT_RE = re.compile(r"^mgsecret_([A-Za-z0-9_-]+)\.txt$")


def extract_event_id(line: str) -> str | None:
    if not line.startswith("MG_OPEN "):
        return None
    path = line[len("MG_OPEN "):].strip()
    m = EVENT_RE.fullmatch(Path(path).name)
    return m.group(1) if m else None


def write_status(path: Path, state: str, reason: str = "") -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"state": state, "reason": reason,
                                "monotonic_ns": time.monotonic_ns()}), encoding="utf-8")
    temp.replace(path)


def run_monitor(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    alert_log = output / "alerts.jsonl"
    status_path = output / "monitor_status.json"
    stderr_path = output / "bpftrace_stderr.log"
    write_status(status_path, "starting")
    process = None
    stop = False

    def stop_handler(_signum, _frame):
        nonlocal stop
        stop = True
        if process is not None and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        with stderr_path.open("w", encoding="utf-8") as stderr, alert_log.open(
                "w", encoding="utf-8", buffering=1) as alerts:
            env = os.environ.copy()
            # Ubuntu 24.04 ships an older bpftrace with 64-byte strings by default.
            # Increase the limit, or long absolute paths lose the event ID.
            env["BPFTRACE_MAX_STRLEN"] = "192"
            process = subprocess.Popen(
                ["bpftrace", "-q", "-e", TRACE_PROGRAM], env=env,
                stdout=subprocess.PIPE, stderr=stderr,
                text=True, bufsize=1)
            if process.stdout is None:
                raise RuntimeError("Could not read bpftrace output")
            for raw_line in process.stdout:
                receipt_ns = time.monotonic_ns()
                line = raw_line.strip()
                if line == "MG_READY":
                    write_status(status_path, "ready")
                    continue
                event_id = extract_event_id(line)
                if event_id is None:
                    continue
                alerts.write(json.dumps({"id": event_id,
                                         "alert_monotonic_ns": receipt_ns},
                                        sort_keys=True) + "\n")
                alerts.flush()
                if stop:
                    break
            code = process.wait(timeout=8)
            if not stop and code != 0:
                raise RuntimeError(f"bpftrace exited with code {code}; see {stderr_path}")
        if stop:
            write_status(status_path, "stopped")
        else:
            write_status(status_path, "exited", "bpftrace stopped unexpectedly")
    except Exception as exc:
        write_status(status_path, "error", str(exc))
        print(f"Monitor error: {exc}", file=sys.stderr, flush=True)
        raise
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    run_monitor(args.output)


if __name__ == "__main__":
    main()
