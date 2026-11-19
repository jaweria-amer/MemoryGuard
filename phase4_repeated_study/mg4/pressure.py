"""Idle file-backed competitor. Creates reclaim pressure without busy-spinning."""
import argparse
import signal
import time
from pathlib import Path
from .cache import warm_file
from .common import json_write


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--mib', type=int, default=512)
    a = p.parse_args()
    stopped = False
    def handler(*_args):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    json_write(a.out / 'pressure_status.json', {'state': 'warming'})
    view = warm_file(a.out / 'pressure_pages.bin', a.mib)
    json_write(a.out / 'pressure_status.json', {'state': 'ready', 'mib': a.mib})
    try:
        while not stopped:
            time.sleep(0.1)
    finally:
        view.close()
        json_write(a.out / 'pressure_status.json', {'state': 'stopped'})

if __name__ == '__main__':
    main()
