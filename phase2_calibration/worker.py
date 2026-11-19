#!/usr/bin/env python3
"""Disposable-VM-only file-backed cache warmup and synthetic rule lookup worker.

No eBPF events or Ollama in this mechanism calibration. The measured lookup is
not a MemoryGuard detection alert; use only to decide whether a follow-up
end-to-end test has a realistic reclaimable rule working set.
"""
import argparse
import json
import mmap
import os
import random
import resource
import signal
import time
from pathlib import Path

PAGE = mmap.PAGESIZE


def atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def create_and_warm(path: Path, mb: int) -> mmap.mmap:
    # Give each trial a fresh file so a prior cgroup does not own its cache.
    size = mb * 1024 * 1024
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        block = b'\x01' * (1024 * 1024)
        for _ in range(mb):
            os.write(fd, block)
        os.fsync(fd)  # file-backed pages should be clean and reclaimable
        view = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
    finally:
        os.close(fd)
    checksum = 0
    for i in range(0, size, PAGE):
        checksum += view[i]
    return view


def run(role: str, out: Path, size_mb: int, pages_per_probe: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    status_file = out / f'{role}_status.json'
    signal_pending = False
    stopped = False

    def handler(signum, _):
        nonlocal signal_pending, stopped
        if signum == signal.SIGUSR1:
            signal_pending = True
        else:
            stopped = True

    signal.signal(signal.SIGUSR1, handler)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    atomic_json(status_file, {'state': 'warming', 'role': role})
    view = create_and_warm(out / f'{role}_pages.bin', size_mb)
    atomic_json(status_file, {'state': 'ready', 'role': role,
                              'size_mib': size_mb, 'pages_warmed': len(view) // PAGE})
    try:
        while not stopped:
            if role == 'monitor' and signal_pending:
                signal_pending = False
                rnd = random.Random(20261103)
                offsets = [rnd.randrange(len(view) // PAGE) * PAGE for _ in range(pages_per_probe)]
                before = resource.getrusage(resource.RUSAGE_SELF)
                start = time.monotonic_ns()
                total = 0
                for offset in offsets:
                    total += view[offset]
                finish = time.monotonic_ns()
                after = resource.getrusage(resource.RUSAGE_SELF)
                atomic_json(out / 'lookup.json', {
                    'probe_count': pages_per_probe,
                    'elapsed_ms': (finish - start) / 1_000_000,
                    'minor_faults': after.ru_minflt - before.ru_minflt,
                    'major_faults': after.ru_majflt - before.ru_majflt,
                    'checksum': total,
                    'triggered_at_monotonic_ns': start,
                })
            time.sleep(0.05)
    finally:
        view.close()
        atomic_json(status_file, {'state': 'stopped', 'role': role})


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--role', choices=('monitor', 'pressure'), required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--size-mb', type=int, required=True)
    p.add_argument('--pages-per-probe', type=int, default=512)
    a = p.parse_args()
    if not 1 <= a.size_mb <= 512:
        p.error('Each worker is limited to at most 512 MiB')
    run(a.role, a.out, a.size_mb, a.pages_per_probe)
