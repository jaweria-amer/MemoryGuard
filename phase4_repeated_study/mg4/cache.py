"""File-backed, deterministic synthetic rule cache. All data is fake."""
import mmap
import os
import random
from pathlib import Path

PAGE = mmap.PAGESIZE


def warm_file(path, size_mib):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    length = size_mib * 1024**2
    if not 1 <= size_mib <= 512:
        raise ValueError('File-backed worker must be <=512 MiB')
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        block = b'\x01' * (1024**2)
        for _ in range(size_mib):
            os.write(fd, block)
        os.fsync(fd)
        view = mmap.mmap(fd, length, access=mmap.ACCESS_READ)
    finally:
        os.close(fd)
    # Populate the page cache before competitor pressure starts.
    total = sum(view[i] for i in range(0, length, PAGE))
    if total != length // PAGE:
        raise RuntimeError('Synthetic cache checksum failed during warmup')
    return view


def rule_offsets(size, probes, seed=20261103):
    page_count = size // PAGE
    if probes <= 0 or probes > page_count:
        raise ValueError('Invalid number of rule pages')
    return [i * PAGE for i in random.Random(seed).sample(range(page_count), probes)]


def check_rules(view, offsets):
    """All fake policy pages contain 1; their sum is the deterministic pass condition."""
    return sum(view[offset] for offset in offsets) == len(offsets)
