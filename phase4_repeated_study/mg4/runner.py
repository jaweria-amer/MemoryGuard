"""Randomized, balanced Phase 4 integrated REPEATED study (separate follow-up).

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
from .study import balanced_plan, code_hash, verify_trial, summarize_study

ROOT = Path(__file__).resolve().parent.parent
SLICE = 'mgphase4.slice'
AI_SLICE = 'mgphase4ai.slice'
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
        if probe.connect_ex(('127.0.0.1', 11437)) == 0:
            raise RuntimeError('Ollama test port 11437 is already occupied; inspect before running')
    if not MODEL_DIR.is_dir():
        raise RuntimeError(f'Prepared model directory missing: {MODEL_DIR}')
    for running in ('mg-ai-*.service', 'mg-monitor-*.service', 'mg3*.service', 'mg4m-*.service', 'mg4p-*.service', 'mg4-ai-*.service'):
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
               sys.executable, '-m', 'mg4.' + module, '--out', str(out), *map(str, extra)]
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
    unit_m = f'mg4m-{os.getpid()}-{stem}.service'
    unit_p = f'mg4p-{os.getpid()}-{stem}.service'
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
        attacker = cmd(sys.executable, '-m', 'mg4.attack', '--trial', trial_id,
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
            'trial_start_monotonic_ns': trial_start,
            'trial_end_monotonic_ns': trial_end,
            'ai_session': ai_folder.name if ai_enabled else None,
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
            'warning': 'Synthetic rule-cache processing was added to the real eBPF alert path. This is a repeated follow-up under local memory.high, not the original lightweight monitor or an AI-driven global memory crisis.',
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
                print('WARNING: failed to relax Phase 4 slice:', exc, file=sys.stderr)
        for unit in active[::-1]:
            cmd('systemctl', 'stop', unit, check=False)
        for filename in ('synthetic_rule_cache.bin', 'pressure_pages.bin'):
            try:
                (out / filename).unlink()
            except FileNotFoundError:
                pass
        time.sleep(1)


def start_ai(out, model, session_number):
    unit = f'mg4-ai-{os.getpid()}-s{session_number}.service'
    start_unit(unit, AI_SLICE, 'ai', out, 0,
               ('--model-dir', MODEL_DIR, '--model', model))
    try:
        wait_status(out / 'ai_status.json', timeout=220)
    except BaseException:
        unit_journal(out, unit)
        cmd('systemctl', 'stop', unit, check=False)
        raise
    return unit


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--blocks', type=int, default=8)
    parser.add_argument('--attacks', type=int, default=8)
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--seed', type=int, default=20261103)
    parser.add_argument('--model', default='qwen2.5:1.5b')
    a = parser.parse_args()
    if a.blocks not in (4, 8, 12) or not (6 <= a.attacks <= 12) or not (0.8 <= a.interval <= 2.0):
        parser.error('Safety caps: blocks 4/8/12; attacks 6-12; interval 0.8-2 sec')
    out = a.output.resolve()
    if a.resume and not out.is_dir():
        raise SystemExit('Resume output missing; refusing to create another batch: ' + str(out))
    if not a.resume:
        if out.exists():
            raise SystemExit('New output already exists; refusing overwrite: ' + str(out))
        out.mkdir(parents=True, exist_ok=False)
    interrupted = False
    def handle(*_):
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt('Phase 4 study interrupted')
    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    ai_unit = None
    ai_folder = None
    session_number = 0
    entries = []
    valid_count = 0
    try:
        env = preflight(out, a.model)
        if a.resume:
            plan_data = _read_json(out / 'study_plan.json')
            saved_env = _read_json(out / 'environment.json')
            if (saved_env.get('kernel') != env.get('kernel') or
                saved_env.get('ram_kib') != env.get('ram_kib') or
                saved_env.get('swap') != env.get('swap')):
                raise RuntimeError('VM/kernel/memory environment changed; refusing to mix study runs')
            if plan_data.get('code_sha256') != code_hash(ROOT):
                raise RuntimeError('Source code changed since original batch; refusing to mix versions')
            if plan_data['model'] != a.model:
                raise RuntimeError('Resume model differs from recorded design')
            entries = plan_data['conditions']
            if entries != balanced_plan(plan_data['blocks'], plan_data['seed']):
                raise RuntimeError('Saved randomized plan is inconsistent; refusing resume')
            if plan_data['blocks'] not in (4,8,12):
                raise RuntimeError('Unexpected block count in resume plan')
            attacks, interval = plan_data['attacks_per_trial'], plan_data['interval_s']
            print('RESUMING IMMUTABLE RANDOMIZED STUDY:', out, flush=True)
            print('Existing valid trials will be checked from raw data and SKIPPED.', flush=True)
        else:
            entries = balanced_plan(a.blocks, a.seed)
            attacks, interval = a.attacks, a.interval
            plan_data = {'version':'phase4-v1', 'blocks':a.blocks, 'seed':a.seed,
                         'attacks_per_trial':attacks, 'interval_s':interval,
                         'model':a.model,'code_sha256':code_hash(ROOT),
                         'conditions':entries,
                         'started_utc':datetime.now(timezone.utc).isoformat(),
                         'primary_latency_outcome':'first cold-cache eBPF alert delay in milliseconds',
                         'primary_deadline_outcome':'proportion of eBPF alerts within the unchanged 250ms deadline',
                         'design_note':'Eight 4-condition blocks, Latin-square-position balanced and seed-shuffled. Paired first-alert differences per block and AI setting.'}
            json_write(out / 'study_plan.json', plan_data)
            json_write(out / 'environment.json', env)
            print('NEW BALANCED STUDY:', out, flush=True)
        session_folders = sorted(out.glob('ai_session_*'))
        if session_folders:
            session_number = max(int(p.name.rsplit('_', 1)[-1]) for p in session_folders)
        for entry in entries:
            label, low, ai_on = entry['label'], entry['low_mib'], entry['ai_enabled']
            trial_dir = out / label
            if trial_dir.exists():
                if (trial_dir / 'result.json').exists():
                    verify_trial(trial_dir, entry, attacks)
                    valid_count += 1
                    if not ai_on and ai_unit is not None:
                        unit_journal(ai_folder, ai_unit)
                        cmd('systemctl', 'stop', ai_unit, check=False)
                        ai_unit = None
                        ai_folder = None
                    print(f'KEEP verified original trial: {label}', flush=True)
                    continue
                # This attempt never completed. Preserve all raw evidence.
                archive = out / '_failed_attempts'
                archive.mkdir(exist_ok=True)
                when = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                moved_to = archive / (label + '.prior_' + when)
                trial_dir.rename(moved_to)
                print(f'ARCHIVED incomplete attempt: {moved_to}', flush=True)
                if len(list(archive.glob(label + '.prior_*'))) > 2:
                    raise RuntimeError('Too many interrupted attempts for this condition; inspect before retry')
            if ai_on and ai_unit is None:
                session_number += 1
                ai_folder = out / f'ai_session_{session_number:02d}'
                ai_folder.mkdir(parents=True, exist_ok=False)
                print(f'Starting CPU-only Ollama session {session_number}', flush=True)
                ai_unit = start_ai(ai_folder, a.model, session_number)
            elif not ai_on and ai_unit is not None:
                unit_journal(ai_folder, ai_unit)
                cmd('systemctl', 'stop', ai_unit, check=False)
                ai_unit = None
                ai_folder = None
            print(f"TRIAL {label}: block={entry['block']}, position={entry['position']}, "
                  f'protection={low} MiB, AI={ai_on}', flush=True)
            result = run_trial(trial_dir, low, ai_on, ai_unit, ai_folder, attacks, interval)
            verify_trial(trial_dir, entry, attacks)
            valid_count += 1
            print(f"{label}: first={result['first_alert']['total_delay_ms']:.3f} ms, "
                  f"major faults={result['total_lookup_major_faults']}, "
                  f"ontime={result['alerts']['on_time']}/{attacks}", flush=True)
        qc = summarize_study(out, plan_data)
        print('PHASE 4 COMPLETE. Integrity QC:', json.dumps({
            'integrity_passed':qc['integrity_passed'],
            'trials_verified':qc['trials_verified'],
            'events_verified':qc['events_verified'],
            'late_250ms':qc['late_250ms'], 'oom_kills':qc['oom_kills']},
            sort_keys=True), flush=True)
        print('The study does NOT assume that memory.low improves deadline compliance.', flush=True)
    except BaseException as exc:
        json_write(out / 'study_failure.json', {
            'error':str(exc), 'finished':False,
            'completed_or_preserved_valid_trials':valid_count,
            'diagnostic_note':'Preserve the original trial folders; upload phase4 archive for debugging.',
            'failed_utc': datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        if ai_unit:
            unit_journal(ai_folder, ai_unit)
            cmd('systemctl', 'stop', ai_unit, check=False)
        try:
            set_slice(0, 'infinity')
        except Exception:
            pass

if __name__ == '__main__':
    main()
