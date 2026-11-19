"""Bounded, separate integrated Phase 3 PILOT (not a full study).

A real bpftrace file-open event passes through a deterministic synthetic,
file-backed 128 MiB rule cache before the alert is emitted. A separate
cgroup is subject to local reclaim under memory.high while a CPU-only Ollama
service is separately toggled for a matched CPU-competition control.

Safety: Ubuntu 24.04 disposable lab VM marker required; original 100-trial
batch and original memoryguard.slice are untouched.
"""
import argparse
import csv
import json
import os
import platform
import re
import shutil
import socket
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from .common import cmd, json_write, wait_status, snap, delta, cgroup_for_unit, unit_journal
from .analysis import summarise, read_jsonl

ROOT = Path(__file__).resolve().parent.parent
SLICE = 'mgphase3.slice'
AI_SLICE = 'mgphase3ai.slice'
MON_MIB = 128
PRESSURE_MIB = 512
PARENT_HIGH_MIB = 640
PROTECT_MIB = 192
PROBE_PAGES = 512
DEADLINE_MS = 250
BATCH1 = Path('/home/ubuntu/MemoryGuard_RunKit/results/batch_20261103T052931Z')
MODEL_DIR = Path('/var/lib/memoryguard/models')


def preflight(destination, model):
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise RuntimeError('Refusing to run outside root Ubuntu lab VM')
    if not Path('/etc/memoryguard-allowed-vm').is_file():
        raise RuntimeError('Refusing unmarked VM')
    os_release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', os_release, flags=re.M) or not re.search(r'^VERSION_ID="?24\.04"?$', os_release, flags=re.M):
        raise RuntimeError('Requires Ubuntu 24.04')
    if not BATCH1.is_dir() or not (BATCH1 / 'qc.json').is_file():
        raise RuntimeError('Original 100-trial batch not found on VM; refused')
    qc = json.loads((BATCH1 / 'qc.json').read_text())
    if not qc.get('passed') or qc.get('valid_trials') != 100:
        raise RuntimeError('Original Batch 1 QC did not pass; refused')
    memory_kib = int(re.search(r'^MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M).group(1))
    if memory_kib < 7 * 1024**2:
        raise RuntimeError('VM needs at least 7 GiB RAM')
    if shutil.disk_usage(destination).free < 10 * 1024**3:
        raise RuntimeError('VM needs at least 10 GiB free storage')
    swaps = Path('/proc/swaps').read_text().splitlines()
    if len(swaps) != 1:
        raise RuntimeError('Swap settings changed since Phase 2 calibration; re-evaluate before running')
    for binary in ('systemctl', 'systemd-run', 'bpftrace', 'ollama'):
        if not shutil.which(binary):
            raise RuntimeError(f'Missing required Linux tool: {binary}')
    if 'memory' not in Path('/sys/fs/cgroup/cgroup.controllers').read_text().split():
        raise RuntimeError('cgroup v2 memory controller unavailable')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(('127.0.0.1', 11436)) == 0:
            raise RuntimeError('Ollama test port 11436 is already occupied; inspect before running')
    if not MODEL_DIR.is_dir():
        raise RuntimeError(f'Prepared model directory missing: {MODEL_DIR}')
    for running in ('mg-ai-*.service', 'mg-monitor-*.service', 'mg3-*.service', 'mg3m-*.service', 'mg3p-*.service'):
        result = cmd('systemctl', 'list-units', '--state=running', '--plain', '--no-legend', running, check=False).stdout.strip()
        if result:
            raise RuntimeError(f'Conflicting experiment already running: {result}')
    return {'os': 'Ubuntu 24.04', 'kernel': platform.release(), 'ram_kib': memory_kib,
            'swap': 0, 'batch1_preserved': str(BATCH1), 'model': model,
            'monitor_cache_mib': MON_MIB, 'competitor_cache_mib': PRESSURE_MIB,
            'parent_memory_high_mib': PARENT_HIGH_MIB,
            'protected_memory_low_mib': PROTECT_MIB,
            'probe_pages_per_event': PROBE_PAGES,
            'causal_note': 'memory.high forces local reclaim; concurrent Ollama is not itself proven to cause that reclaim.'}


def start_unit(unit, slice_name, module, out, low_mib=0, extra=()):
    command = ['systemd-run', '--unit=' + unit, '--slice=' + slice_name, '--collect',
               '--property=MemoryAccounting=yes', f'--property=MemoryLow={low_mib}M',
               sys.executable, '-m', 'mg3.' + module, '--out', str(out), *map(str, extra)]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    # Explicit systemd property to carry module search path to the service.
    command.insert(4, '--setenv=PYTHONPATH=' + str(ROOT))
    p = subprocess.run(command, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f'Could not start {unit}: {p.stderr}\n{p.stdout}')


def set_slice(low, high='infinity'):
    cmd('systemctl', 'set-property', '--runtime', SLICE,
        f'MemoryLow={low}M', f'MemoryHigh={high}', 'MemoryAccounting=yes')


def ai_metrics(path, start_ns, finish_ns):
    rows = read_jsonl(path)
    return [x['tokens_per_second'] for x in rows
            if start_ns <= int(x['finish_ns']) <= finish_ns and x.get('tokens_per_second') is not None]


def read_alerts_safely(path):
    if not path.exists():
        return []
    return read_jsonl(path)


def wait_for_alerts(path, target_count, status_path, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        alerts = read_alerts_safely(path)
        if len({x['id'] for x in alerts}) >= target_count:
            return alerts
        if status_path.exists():
            status = json.loads(status_path.read_text())
            if status.get('state') == 'error':
                raise RuntimeError(f'Monitor failed while detecting events: {status}')
        time.sleep(0.15)
    return read_alerts_safely(path)


def run_trial(out, low, ai_enabled, ai_unit, ai_folder, attack_count, interval):
    out.mkdir(parents=True, exist_ok=False)
    stem = re.sub('[^a-z0-9-]', '-', out.name.lower())[:40]
    unit_m = f'mg3m-{os.getpid()}-{stem}.service'
    unit_p = f'mg3p-{os.getpid()}-{stem}.service'
    active = []
    try:
        start_unit(unit_m, SLICE, 'monitor', out, low,
                   ('--cache-mib', MON_MIB, '--probe-pages', PROBE_PAGES))
        active.append(unit_m)
        wait_status(out / 'monitor_status.json', timeout=100)
        cg_monitor = cgroup_for_unit(unit_m)
        cg_parent = cgroup_for_unit(SLICE)
        set_slice(low, 'infinity')
        pre = {'monitor': snap(cg_monitor), 'parent': snap(cg_parent)}
        if int(pre['monitor']['memory_low']) != low * 2**20 or int(pre['parent']['memory_low']) != low * 2**20:
            raise RuntimeError('Effective monitor/parent memory.low configuration mismatch')
        set_slice(low, str(PARENT_HIGH_MIB) + 'M')
        start_unit(unit_p, SLICE, 'pressure', out, 0, ('--mib', PRESSURE_MIB))
        active.append(unit_p)
        wait_status(out / 'pressure_status.json', timeout=110)
        time.sleep(2)
        pressure_cg = cgroup_for_unit(unit_p)
        before = {'monitor': snap(cg_monitor), 'parent': snap(cg_parent), 'pressure': snap(pressure_cg)}
        if ai_enabled:
            before['ai'] = snap(cgroup_for_unit(ai_unit))
        if int(before['parent']['memory_high']) != PARENT_HIGH_MIB * 2**20:
            raise RuntimeError('Parent memory.high incorrect')
        trial_start = time.monotonic_ns()
        if ai_enabled:
            wait_status(ai_folder / 'ai_status.json', timeout=20)
        # Original, harmless Linux-comm-filtered attack generator; read under no special comm.
        trial_id = stem.replace('-', '_')
        attacker = cmd(sys.executable, '-m', 'mg3.attack', '--trial', trial_id,
                       '--output', str(out), '--count', attack_count,
                       '--interval', interval, check=False)
        (out / 'attack_stdout.log').write_text(attacker.stdout + attacker.stderr, encoding='utf8')
        if attacker.returncode:
            raise RuntimeError(f'Attack event generator failed ({attacker.returncode})')
        events = read_jsonl(out / 'attacks.jsonl')
        alerts = wait_for_alerts(out / 'alerts.jsonl', len(events), out / 'monitor_status.json')
        trial_end = time.monotonic_ns()
        after = {'monitor': snap(cg_monitor), 'parent': snap(cg_parent), 'pressure': snap(pressure_cg)}
        if ai_enabled:
            after['ai'] = snap(cgroup_for_unit(ai_unit))
        if ai_enabled:
            status = json.loads((ai_folder / 'ai_status.json').read_text())
            if status.get('state') != 'ready':
                raise RuntimeError(f'Ollama exited during trial: {status}')
            speeds = ai_metrics(ai_folder / 'ai_metrics.jsonl', trial_start, trial_end)
            if not speeds:
                raise RuntimeError('No completed live Ollama request during AI-on trial')
        else:
            speeds = []
        stats = summarise(events, alerts, DEADLINE_MS)
        expected_ids = {y['id'] for y in events}
        earliest = {}
        for alert in alerts:
            event_id = alert['id']
            if event_id in expected_ids and (event_id not in earliest or
                int(alert['alert_monotonic_ns']) < int(earliest[event_id]['alert_monotonic_ns'])):
                earliest[event_id] = alert
        detected = sorted(earliest.values(), key=lambda x: x['alert_monotonic_ns'])
        fault_events = sum(int(x.get('lookup_major_faults', 0)) for x in detected)
        first = None
        if detected:
            first = next((a for a in detected if a['id'] == events[0]['id']), None)
        oom = delta(pre['parent'], after['parent'], 'memory_events', 'oom_kill')
        if ai_enabled:
            oom += delta(before['ai'], after['ai'], 'memory_events', 'oom_kill')
        if oom:
            raise RuntimeError(f'OOM kills detected in test cgroup: {oom}')
        if len(detected) != len(events):
            raise RuntimeError(f'Only {len(detected)} of {len(events)} eBPF alerts received; preserve diagnostics')
        current_monitor_status = json.loads((out / 'monitor_status.json').read_text())
        if current_monitor_status.get('state') != 'ready':
            raise RuntimeError(f'Monitor not running after trial: {current_monitor_status}')
        result = {
            'label': out.name, 'low_mib': low, 'ai_enabled': ai_enabled,
            'attack_count': attack_count, 'completed_requests': len(speeds),
            'ai_tokens_per_sec_median': statistics.median(speeds) if speeds else None,
            'alerts': {'on_time': stats['on_time'], 'late': stats['late'],
                       'missed': stats['missed'], 'on_time_pct': stats['on_time_pct'],
                       'p99_delay_ms': stats['p99_observed_delay_ms'],
                       'median_delay_ms': stats['median_observed_delay_ms']},
            'first_alert': ({'total_delay_ms': (first['alert_monotonic_ns'] - events[0]['trigger_monotonic_ns']) / 1e6,
                             'rule_lookup_ms': first['lookup_ms'],
                             'lookup_major_faults': first['lookup_major_faults']}
                            if first is not None else None),
            'total_lookup_major_faults': fault_events,
            'monitor_refault_during_attack': delta(before['monitor'], after['monitor'], 'memory_stat', 'workingset_refault_file'),
            'monitor_pgmajfault_during_attack': delta(before['monitor'], after['monitor'], 'memory_stat', 'pgmajfault'),
            'parent_high_events_from_pressure': delta(pre['parent'], before['parent'], 'memory_events', 'high'),
            'monitor_pgscan_from_pressure': delta(pre['monitor'], before['monitor'], 'memory_stat', 'pgscan'),
            'memory_psi_monitor_during_attack_us': delta(before['monitor'], after['monitor'], 'memory_psi_us', 'some'),
            'cpu_psi_monitor_during_attack_us': delta(before['monitor'], after['monitor'], 'cpu_psi_us', 'some'),
            'oom_kills': oom,
            'warning': 'Synthetic rule-cache processing was added to the real eBPF alert path. This is a targeted pilot under local memory.high, not the original lightweight monitor or an AI-driven global memory crisis.',
        }
        json_write(out / 'result.json', result)
        json_write(out / 'cgroup_snapshots.json', {'pre': pre, 'before_attacks': before, 'after_attacks': after})
        with (out / 'events.csv').open('w', newline='', encoding='utf8') as stream:
            writer = csv.DictWriter(stream, ['id', 'trigger_ns', 'alert_ns', 'total_delay_ms', 'rule_lookup_ms', 'lookup_major_faults', 'status'])
            writer.writeheader()
            match = {x['id']: x for x in detected}
            for src in events:
                e = match.get(src['id'])
                delay = (e['alert_monotonic_ns']-src['trigger_monotonic_ns'])/1e6 if e else None
                writer.writerow({'id': src['id'], 'trigger_ns': src['trigger_monotonic_ns'],
                                 'alert_ns': e['alert_monotonic_ns'] if e else '',
                                 'total_delay_ms': delay if delay is not None else '',
                                 'rule_lookup_ms': e['lookup_ms'] if e else '',
                                 'lookup_major_faults': e['lookup_major_faults'] if e else '',
                                 'status': ('missed' if delay is None else 'on_time' if delay <= DEADLINE_MS else 'late')})
        return result
    except BaseException:
        for unit in active:
            unit_journal(out, unit)
        raise
    finally:
        # Remove bounds before stopping units, then delete transient large files.
        if active:
            try:
                set_slice(0, 'infinity')
            except Exception as exc:
                print('WARNING: failed to relax Phase 3 slice:', exc, file=sys.stderr)
        for unit in active[::-1]:
            cmd('systemctl', 'stop', unit, check=False)
        for filename in ('synthetic_rule_cache.bin', 'pressure_pages.bin'):
            try:
                (out / filename).unlink()
            except FileNotFoundError:
                pass
        time.sleep(1)


def start_ai(out, model):
    unit = f'mg3-ai-{os.getpid()}.service'
    start_unit(unit, AI_SLICE, 'ai', out, 0,
               ('--model-dir', MODEL_DIR, '--model', model))
    try:
        wait_status(out / 'ai_status.json', timeout=220)
    except BaseException:
        unit_journal(out, unit)
        cmd('systemctl', 'stop', unit, check=False)
        raise
    return unit


def plan(repeats):
    for n in range(1, repeats + 1):
        # Counterbalanced low setting within each block and AI block order across repeats.
        if n % 2:
            blocks = ((False, (0, PROTECT_MIB)), (True, (PROTECT_MIB, 0)))
        else:
            blocks = ((True, (0, PROTECT_MIB)), (False, (PROTECT_MIB, 0)))
        for ai_on, levels in blocks:
            for level in levels:
                yield (f'r{n}_{"ai" if ai_on else "noai"}_low{level}', level, ai_on)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--attacks', type=int, default=8)
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--model', default='qwen2.5:1.5b')
    a = parser.parse_args()
    if not (1 <= a.repeats <= 3) or not (4 <= a.attacks <= 12) or not (0.5 <= a.interval <= 3):
        parser.error('Safety caps: 1-3 repeats; 4-12 attacks; 0.5-3 sec interval')
    out = a.output.resolve()
    if out.exists():
        raise SystemExit('Output already exists; cannot overwrite: ' + str(out))
    out.mkdir(parents=True, exist_ok=False)
    interrupted = False
    def handle(*_):
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt('Phase 3 pilot interrupted')
    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    ai_unit = None
    ai_dir = out / 'ai_service'
    results = []
    try:
        env = preflight(out, a.model)
        json_write(out / 'environment.json', env)
        config = {'repeats': a.repeats, 'attacks_per_trial': a.attacks,
                  'interval_s': a.interval, 'conditions': list(plan(a.repeats)),
                  'started_utc': datetime.now(timezone.utc).isoformat()}
        json_write(out / 'plan.json', config)
        for trial_name, low, ai_on in plan(a.repeats):
            if ai_on and not ai_unit:
                print('Starting CPU-only Ollama control workload', flush=True)
                ai_dir.mkdir(parents=True, exist_ok=False)
                ai_unit = start_ai(ai_dir, a.model)
            if not ai_on and ai_unit:
                cmd('systemctl', 'stop', ai_unit, check=False)
                ai_unit = None
            print(f'PILOT {trial_name}: protection={low} MiB, AI={ai_on}', flush=True)
            result = run_trial(out / trial_name, low, ai_on, ai_unit, ai_dir, a.attacks, a.interval)
            results.append(result)
            print(f"{trial_name}: ontime={result['alerts']['on_time']}/{a.attacks}, "
                  f"first={result['first_alert']['total_delay_ms'] if result['first_alert'] else 'MISSING'} ms, "
                  f"rule major faults={result['total_lookup_major_faults']}", flush=True)
        # A PASSED pilot means integrated mechanism ran; it does not prove improvement.
        needs_baseline_reclaim = any(
            r['low_mib'] == 0 and r['monitor_pgscan_from_pressure'] > 0 and
            (r['monitor_pgmajfault_during_attack'] > 0 or r['monitor_refault_during_attack'] > 0)
            for r in results)
        event_coverage = all(r['alerts']['missed'] == 0 and
                             r['alerts']['on_time'] + r['alerts']['late'] == a.attacks
                             for r in results)
        real_pressure = all(r['parent_high_events_from_pressure'] > 0 for r in results)
        enough_ai = all(r['completed_requests'] > 0 for r in results if r['ai_enabled'])
        findings = {
            'finished': True, 'expected_trials': len(config['conditions']),
            'valid_trials': len(results), 'total_events': a.attacks * len(results),
            'baseline_reclaim_observed': needs_baseline_reclaim,
            'actual_local_pressure_observed_in_every_trial': real_pressure,
            'complete_event_detection': event_coverage,
            'ai_concurrently_active': enough_ai,
            'oom_kills': sum(r['oom_kills'] for r in results),
            'results': results,
            'mechanism_pilot_passed': needs_baseline_reclaim and enough_ai and event_coverage and real_pressure,
            'limitations': [
                'memory.high causes local reclaim; Ollama generates CPU load in a separate cgroup.',
                'The monitor uses synthetic file-backed rule lookup that is absent from Batch 1.',
                'Four to twelve trials are pilot data, not hypothesis confirmation.',
                'No attempt is made to force or assume a 250 ms deadline failure.',
                'The original 100-trial batch remains an independent negative result.'
            ]}
        json_write(out / 'pilot_summary.json', findings)
        print('PHASE 3 PILOT COMPLETE. Inspect pilot_summary.json; not a finished causal study.', flush=True)
        if not needs_baseline_reclaim:
            print('NOTE: baseline reclaim was not observed; do NOT infer memory.low benefit.', flush=True)
    except BaseException as exc:
        json_write(out / 'failure.json', {'error': str(exc), 'finished': False,
                                          'valid_completed_trials': len(results),
                                          'completed_trial_labels': [r['label'] for r in results]})
        raise
    finally:
        if ai_unit:
            unit_journal(ai_dir, ai_unit)
            cmd('systemctl', 'stop', ai_unit, check=False)
        try:
            set_slice(0, 'infinity')
        except Exception:
            pass

if __name__ == '__main__':
    main()
