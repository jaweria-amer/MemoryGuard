"""Real eBPF file-open event -> synthetic rule-cache lookup -> timestamped alert.

This is explicitly an *augmented synthetic* rule path, not the original
lightweight Python rule checker. Each event probes 512 mapped rule pages.
"""
import argparse
import json
import os
import re
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path
from .cache import check_rules, rule_offsets, warm_file
from .common import json_write

TRACE = r'''
BEGIN { printf("MG_READY\n"); }
tracepoint:syscalls:sys_enter_openat
/comm == "mg_attack"/
{
    printf("MG_OPEN %s\n", str(args.filename));
}
'''
EVENT = re.compile(r'^mgsecret_([A-Za-z0-9_-]+)\.txt$')


def event_id_from_line(line):
    if not line.startswith('MG_OPEN '):
        return None
    match = EVENT.fullmatch(Path(line[len('MG_OPEN '):].strip()).name)
    return match.group(1) if match else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cache-mib', type=int, default=128)
    p.add_argument('--probe-pages', type=int, default=512)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    state = a.out / 'monitor_status.json'
    json_write(state, {'state': 'starting'})
    proc = None
    stopping = False
    def handle(*_):
        nonlocal stopping
        stopping = True
        if proc is not None and proc.poll() is None:
            proc.terminate()
    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    view = None
    try:
        view = warm_file(a.out / 'synthetic_rule_cache.bin', a.cache_mib)
        offsets = rule_offsets(len(view), a.probe_pages)
        env = os.environ.copy()
        env['BPFTRACE_MAX_STRLEN'] = '192'
        with (a.out / 'bpftrace_stderr.log').open('w', encoding='utf8') as err, \
                (a.out / 'alerts.jsonl').open('w', encoding='utf8', buffering=1) as log:
            proc = subprocess.Popen(['bpftrace', '-q', '-e', TRACE],
                                    env=env, stdout=subprocess.PIPE,
                                    stderr=err, bufsize=1, text=True)
            if proc.stdout is None:
                raise RuntimeError('bpftrace stdout unavailable')
            for raw in proc.stdout:
                receipt_ns = time.monotonic_ns()
                line = raw.strip()
                if line == 'MG_READY':
                    json_write(state, {'state': 'ready', 'cache_mib': a.cache_mib,
                                       'probe_pages': a.probe_pages})
                    continue
                event_id = event_id_from_line(line)
                if event_id is None:
                    continue
                before_usage = resource.getrusage(resource.RUSAGE_SELF)
                start = time.monotonic_ns()
                permitted = check_rules(view, offsets)
                end = time.monotonic_ns()
                after_usage = resource.getrusage(resource.RUSAGE_SELF)
                if not permitted:
                    raise RuntimeError('Synthetic rule-cache checksum mismatch')
                log.write(json.dumps({
                    'id': event_id, 'receipt_monotonic_ns': receipt_ns,
                    'alert_monotonic_ns': end,
                    'lookup_ms': (end - start) / 1e6,
                    'lookup_major_faults': after_usage.ru_majflt - before_usage.ru_majflt,
                    'lookup_minor_faults': after_usage.ru_minflt - before_usage.ru_minflt,
                    'simulated_rule_cache': True,
                }, sort_keys=True) + '\n')
                log.flush()
                if stopping:
                    break
            code = proc.wait(timeout=8)
            if not stopping and code != 0:
                raise RuntimeError(f'bpftrace exited {code}; see stderr log')
        json_write(state, {'state': 'stopped' if stopping else 'exited'})
    except BaseException as exc:
        json_write(state, {'state': 'error', 'reason': str(exc)})
        print('Monitor error:', exc, file=sys.stderr, flush=True)
        raise
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if view is not None:
            view.close()

if __name__ == '__main__':
    main()
