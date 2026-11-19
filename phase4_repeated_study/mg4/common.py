"""Common cgroup/sandbox utilities for the independent Phase 4 repeated integrated study."""
import json
import os
import subprocess
import time
from pathlib import Path


def cmd(*args, check=True):
    return subprocess.run([str(x) for x in args], capture_output=True, text=True, check=check)


def json_write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf8')
    os.replace(tmp, path)


def wait_status(path, state='ready', timeout=120):
    end = time.monotonic() + timeout
    path = Path(path)
    previous = None
    while time.monotonic() < end:
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding='utf8'))
            except (ValueError, OSError):
                previous = None
            if previous and previous.get('state') == state:
                return previous
            if previous and previous.get('state') in ('error', 'stopped'):
                raise RuntimeError(f'Service did not become {state}: {previous}')
        time.sleep(0.20)
    raise TimeoutError(f'Service timeout waiting for {state}: {path}, last={previous}')


def read_stat_pairs(path):
    result = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                try:
                    result[parts[0]] = int(parts[1])
                except ValueError:
                    pass
    return result


def read_psi(path):
    result = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            parts = line.split()
            if parts and parts[0] in ('some', 'full'):
                for part in parts[1:]:
                    if part.startswith('total='):
                        result[parts[0]] = int(part.split('=', 1)[1])
    return result


def snap(cgroup):
    cgroup = Path(cgroup)
    def val(name):
        f = cgroup / name
        return f.read_text().strip() if f.exists() else None
    return {
        'path': str(cgroup), 'memory_current': val('memory.current'),
        'memory_low': val('memory.low'), 'memory_high': val('memory.high'),
        'memory_stat': read_stat_pairs(cgroup / 'memory.stat'),
        'memory_events': read_stat_pairs(cgroup / 'memory.events'),
        'memory_psi_us': read_psi(cgroup / 'memory.pressure'),
        'cpu_psi_us': read_psi(cgroup / 'cpu.pressure'),
    }


def delta(before, after, group, key):
    return after.get(group, {}).get(key, 0) - before.get(group, {}).get(key, 0)


def cgroup_for_unit(unit):
    value = cmd('systemctl', 'show', '-p', 'ControlGroup', '--value', unit).stdout.strip()
    if not value.startswith('/') or value == '/':
        raise RuntimeError(f'Invalid ControlGroup for {unit}: {value!r}')
    path = Path('/sys/fs/cgroup') / value.lstrip('/')
    if not path.is_dir():
        raise RuntimeError(f'Unit cgroup missing: {path}')
    return path


def unit_journal(out, unit):
    p = cmd('journalctl', '-u', unit, '--no-pager', '-n', '80', check=False)
    (Path(out) / f'{unit}.journal.txt').write_text(p.stdout + '\n' + p.stderr, encoding='utf8')
