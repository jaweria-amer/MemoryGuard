#!/usr/bin/env python3
"""Small cgroup v2 file-backed reclaim calibration in isolated Ubuntu VM ONLY.

Not a repeat of MemoryGuard's full experiment, not an eBPF test, and not a
measurement of AI throughput or the 250 ms security-detection outcome.
"""
import argparse
import json
import os
import platform
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SLICE = 'mgphase2.slice'  # independent root slice, NOT child of memoryguard.slice
MONITOR_MIB = 128
PRESSURE_MIB = 512
PARENT_HIGH_MIB = 470
PROTECT_MIB = 192


def cmd(*args, check=True):
    return subprocess.run(list(map(str, args)), check=check,
                          text=True, capture_output=True)


def read_pairs(path: Path):
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            chunks = line.split()
            if len(chunks) == 2 and chunks[1].isdigit():
                result[chunks[0]] = int(chunks[1])
    return result


def read_psi(path: Path):
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if parts and parts[0] in ('some', 'full'):
                result[parts[0]] = next(
                    (int(x.split('=')[1]) for x in parts[1:] if x.startswith('total=')), 0)
    return result


def snapshot(cg: Path):
    def number(f):
        p = cg / f
        return p.read_text().strip() if p.exists() else None
    return {
        'cgroup': str(cg),
        'memory_current_bytes': number('memory.current'),
        'memory_low_bytes': number('memory.low'),
        'memory_high_bytes': number('memory.high'),
        'memory_stat': read_pairs(cg / 'memory.stat'),
        'memory_events': read_pairs(cg / 'memory.events'),
        'memory_psi_us': read_psi(cg / 'memory.pressure'),
        'cpu_psi_us': read_psi(cg / 'cpu.pressure'),
    }


def diff(a, b, group, metric):
    return b.get(group, {}).get(metric, 0) - a.get(group, {}).get(metric, 0)


def wait_json(path: Path, state: str, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (ValueError, OSError):
                data = {}
            if data.get('state') == state:
                return data
            if data.get('state') == 'error':
                raise RuntimeError(f'Worker error: {data}')
        time.sleep(0.25)
    raise TimeoutError(f'Timed out waiting for {state}: {path}')


def cg_for_unit(name: str) -> Path:
    val = cmd('systemctl', 'show', '-p', 'ControlGroup', '--value', name).stdout.strip()
    if not val.startswith('/') or val == '/':
        raise RuntimeError(f'Invalid service ControlGroup for {name}: {val!r}')
    result = Path('/sys/fs/cgroup') / val.lstrip('/')
    if not result.is_dir():
        raise RuntimeError(f'Cgroup not found: {result}')
    return result


def start_worker(unit: str, role: str, out: Path, mb: int, low_mib: int = 0):
    command = ['systemd-run', '--unit=' + unit, '--slice=' + SLICE,
               '--collect', '--property=MemoryAccounting=yes',
               f'--property=MemoryLow={low_mib}M', sys.executable,
               str(ROOT / 'worker.py'), '--role', role,
               '--out', str(out), '--size-mb', str(mb)]
    cmd(*command)


def set_slice(low_mib: int, high='infinity'):
    cmd('systemctl', 'set-property', '--runtime', SLICE,
        f'MemoryLow={low_mib}M', f'MemoryHigh={high}', 'MemoryAccounting=yes')


def preflight(out_base: Path):
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise RuntimeError('Refused: must run as root INSIDE Ubuntu')
    if not Path('/etc/memoryguard-allowed-vm').is_file():
        raise RuntimeError('Refused: existing disposable MemoryGuard VM marker not present')
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, val = line.split('=', 1)
            release[key] = val.strip('"')
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04':
        raise RuntimeError('Refused: expected Ubuntu 24.04')
    if 'memory' not in Path('/sys/fs/cgroup/cgroup.controllers').read_text().split():
        raise RuntimeError('Refused: cgroup v2 memory controller unavailable')
    mem_kib = int(re.search(r'^MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M).group(1))
    if mem_kib < 7 * 1024 * 1024:
        raise RuntimeError('Refused: needs 7 GiB VM memory')
    if shutil.disk_usage(out_base).free < 10 * 1024**3:
        raise RuntimeError('Refused: less than 10 GiB free in VM')
    if not shutil.which('systemd-run'):
        raise RuntimeError('Refused: no systemd-run')
    # Protect other studies: do not run while any original monitor or AI is live.
    active = cmd('systemctl', 'list-units', '--state=running', '--plain', '--no-legend',
                 'mg-ai-*.service', 'mg-monitor-*.service', check=False).stdout
    if 'mg-ai-' in active or 'mg-monitor-' in active:
        raise RuntimeError('Refused: original MemoryGuard experiment services are active')
    # Do not override an older/current calibration's resource limits.
    if cmd('systemctl', 'is-active', '--quiet', SLICE, check=False).returncode == 0:
        raise RuntimeError('Refused: calibration slice already active; inspect before retry')
    return {'vm_marker': True, 'os': release.get('PRETTY_NAME'), 'kernel': platform.release(),
            'ram_kib': mem_kib, 'disk_free_bytes': shutil.disk_usage(out_base).free,
            'swap_file_count': max(0, len(Path('/proc/swaps').read_text().splitlines()) - 1),
            'monitor_mib': MONITOR_MIB, 'pressure_mib': PRESSURE_MIB,
            'parent_memory_high_mib': PARENT_HIGH_MIB,
            'memory_low_conditions_mib': [0, PROTECT_MIB]}


def run_trial(out: Path, low_mib: int):
    out.mkdir(parents=True, exist_ok=False)
    suffix = out.name.replace('_', '-')
    monitor = f'mg2-monitor-{suffix}.service'
    pressure = f'mg2-pressure-{suffix}.service'
    active = []
    current_low = 0
    try:
        start_worker(monitor, 'monitor', out, MONITOR_MIB, low_mib)
        active.append(monitor)
        wait_json(out / 'monitor_status.json', 'ready')
        # The slice is created with the first transient service; set low on its parent.
        set_slice(low_mib, 'infinity')
        current_low = low_mib
        monitor_cg = cg_for_unit(monitor)
        parent_cg = cg_for_unit(SLICE)
        pre = {'monitor': snapshot(monitor_cg), 'parent': snapshot(parent_cg)}
        if int(pre['monitor']['memory_low_bytes']) != low_mib * 1024 * 1024:
            raise RuntimeError('Monitor MemoryLow mismatch')
        if int(pre['parent']['memory_low_bytes']) < low_mib * 1024 * 1024:
            raise RuntimeError('Parent MemoryLow mismatch')
        # The limit is on a NEW experiment-only slice, NOT the first-batch slice.
        set_slice(low_mib, f'{PARENT_HIGH_MIB}M')
        start_worker(pressure, 'pressure', out, PRESSURE_MIB, 0)
        active.append(pressure)
        wait_json(out / 'pressure_status.json', 'ready')
        time.sleep(2)
        before = {'monitor': snapshot(monitor_cg), 'parent': snapshot(parent_cg),
                  'pressure': snapshot(cg_for_unit(pressure))}
        cmd('systemctl', 'kill', '--signal=USR1', '--kill-whom=main', monitor)
        lookup = wait_file_json(out / 'lookup.json', 30)
        after = {'monitor': snapshot(monitor_cg), 'parent': snapshot(parent_cg),
                 'pressure': snapshot(cg_for_unit(pressure))}
        events = after['parent']['memory_events'].get('oom_kill', 0) - pre['parent']['memory_events'].get('oom_kill', 0)
        if events:
            raise RuntimeError(f'Unexpected cgroup OOM kills: {events}')
        result = {
            'condition': f'low_{low_mib}m', 'low_mib': low_mib,
            'lookup': lookup, 'snapshots': {'pre': pre, 'before': before, 'after': after},
            'parent_high_events_after_pressure': diff(pre['parent'], before['parent'], 'memory_events', 'high'),
            'monitor_refault_file_during_lookup': diff(before['monitor'], after['monitor'], 'memory_stat', 'workingset_refault_file'),
            'monitor_majfault_during_lookup': diff(before['monitor'], after['monitor'], 'memory_stat', 'pgmajfault'),
            'monitor_memory_pressure_us_during_lookup': diff(before['monitor'], after['monitor'], 'memory_psi_us', 'some'),
            'monitor_cpu_pressure_us_during_lookup': diff(before['monitor'], after['monitor'], 'cpu_psi_us', 'some'),
            'oom_kills': events,
        }
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    except BaseException:
        for unit in active:
            journal = cmd('journalctl', '-u', unit, '--no-pager', '-n', '60', check=False)
            (out / f'{unit}.journal.txt').write_text(journal.stdout + '\n' + journal.stderr)
        raise
    finally:
        # Relax limits FIRST to allow normal cleanup without reclaim throttling.
        if active:
            try:
                set_slice(0, 'infinity')
            except Exception as exc:
                print(f'Warning: could not reset temporary slice: {exc}', file=sys.stderr)
        for unit in reversed(active):
            cmd('systemctl', 'stop', unit, check=False)
        for f in (out / 'monitor_pages.bin', out / 'pressure_pages.bin'):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        time.sleep(1)


def wait_file_json(path: Path, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (ValueError, OSError):
                pass
        time.sleep(0.1)
    raise TimeoutError(f'Lookup result never appeared: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt('Calibration interrupted; services will be stopped')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        info = preflight(out)
        (out / 'environment.json').write_text(json.dumps(info, indent=2) + '\n')
        # Four small calibrations; counterbalance low setting ordering.
        results = []
        for label, protection in [('r1_baseline', 0), ('r1_low192', 192),
                                  ('r2_low192', 192), ('r2_baseline', 0)]:
            print('CALIBRATION', label, flush=True)
            r = run_trial(out / label, protection)
            results.append({'name': label, **{k: v for k, v in r.items() if k != 'snapshots'}})
            print(f"{label}: lookup={r['lookup']['elapsed_ms']:.3f} ms; "
                  f"majfault={r['monitor_majfault_during_lookup']}; "
                  f"refault={r['monitor_refault_file_during_lookup']}", flush=True)
        result = {'finished': True, 'results': results,
                  'limits': ['No AI or eBPF in this calibration',
                             'Memory.high triggers cgroup-local cache reclaim, not global memory shortage',
                             'Only four mechanism probes; do not use as statistical evidence of a benefit'],
                  'next_step': 'Use measured reclaim evidence to design a new end-to-end AI/eBPF experiment, or retain Batch 1 as a negative result.'}
        (out / 'calibration_summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print('CALIBRATION FINISHED (NOT an AI/eBPF research batch).')
    except BaseException as exc:
        (out / 'calibration_error.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        raise


if __name__ == '__main__':
    main()
