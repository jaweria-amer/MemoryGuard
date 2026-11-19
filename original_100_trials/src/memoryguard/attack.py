"""Generate harmless, identifiable file-open events for the test monitor.

The files are fake lab objects. This program does not access real secrets,
modify sensitive files, or use a malicious payload.
"""
import argparse
import ctypes
import json
import os
import re
import time
from pathlib import Path


def get_process_name() -> bytes:
    """Read the original Linux comm, to avoid leaking the test identity."""
    libc = ctypes.CDLL(None, use_errno=True)
    buf = ctypes.create_string_buffer(16)
    result = libc.prctl(16, ctypes.byref(buf), 0, 0, 0)  # PR_GET_NAME
    if result != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_GET_NAME) failed")
    return buf.value


def set_process_name(name: bytes = b"mg_attack") -> None:
    """Set Linux task comm so bpftrace can filter only the test process."""
    if len(name) > 15:
        raise ValueError("Linux comm is limited to 15 bytes")
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.prctl(15, ctypes.c_char_p(name), 0, 0, 0)  # PR_SET_NAME
    if result != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NAME) failed")


def generate(trial_id: str, root: Path, count: int, interval: float) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", trial_id):
        raise ValueError("trial ID must contain only letters, digits, '_' or '-'")
    if count < 1 or interval < 0:
        raise ValueError("count must be positive and interval nonnegative")

    root.mkdir(parents=True, exist_ok=True)
    secret_dir = root / "fake_secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    trigger_log = root / "attacks.jsonl"
    files = []
    for i in range(count):
        event_id = f"{trial_id}_{i:04d}"
        path = secret_dir / f"mgsecret_{event_id}.txt"
        path.write_text("MemoryGuard harmless test file. No real secrets.\n", encoding="utf-8")
        if len(str(path).encode("utf-8")) >= 192:
            raise ValueError("Test pathname is too long for the configured bpftrace string limit")
        files.append((event_id, path))

    # File creation happened before setting comm. The monitor sees only test opens.
    original_name = get_process_name()
    set_process_name()
    events = []
    try:
        with trigger_log.open("w", encoding="utf-8", buffering=1) as out:
            for event_id, path in files:
                start_ns = time.monotonic_ns()
                fd = os.open(str(path), os.O_RDONLY)
                try:
                    os.read(fd, 16)
                finally:
                    os.close(fd)
                event = {"id": event_id, "trigger_monotonic_ns": start_ns, "path": str(path)}
                out.write(json.dumps(event, sort_keys=True) + "\n")
                out.flush()
                events.append(event)
                if interval and event_id != files[-1][0]:
                    time.sleep(interval)
    finally:
        # Critical for sequential trials: without this, the next trial's test
        # file CREATION is traced before its intended attack start timestamp.
        set_process_name(original_name)
    return events


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trial", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--interval", type=float, default=2.0)
    args = p.parse_args()
    events = generate(args.trial, args.output, args.count, args.interval)
    print(f"Generated {len(events)} harmless file-open events.")


if __name__ == "__main__":
    main()
