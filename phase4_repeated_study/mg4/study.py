"""Deterministic counterbalanced plan, immutable trial integrity, and analysis.

The experiment is an *augmented synthetic rule-cache monitor*, not the
original lightweight Batch 1 detector. First-alert delay is the prospective
primary latency outcome; 250 ms deadline adherence remains the original
on-time detection outcome.
"""
import csv
import hashlib
import json
import random
import statistics
from collections import Counter
from pathlib import Path

from .analysis import read_jsonl
from .common import json_write

ARMS = (
    ('noai_low0', 0, False),
    ('noai_low192', 192, False),
    ('ai_low0', 0, True),
    ('ai_low192', 192, True),
)
# Each treatment occurs in every position once per four blocks. This order
# balances both positions and first-order preceding treatments.
LATIN_ROWS = (
    (0, 1, 3, 2),
    (1, 2, 0, 3),
    (2, 3, 1, 0),
    (3, 0, 2, 1),
)
DEADLINE_MS = 250.0


def balanced_plan(blocks=8, seed=20261103):
    if blocks not in (4, 8, 12):
        raise ValueError('Use exactly 4, 8, or 12 blocks (balanced design)')
    order = list(range(4)) * (blocks // 4)
    random.Random(seed).shuffle(order)
    plan = []
    for block, row_index in enumerate(order, 1):
        for position, arm_idx in enumerate(LATIN_ROWS[row_index], 1):
            arm, low, ai = ARMS[arm_idx]
            plan.append({'label': f'b{block:02d}_{arm}', 'block': block,
                         'position': position, 'arm': arm, 'low_mib': low,
                         'ai_enabled': ai})
    return plan


def code_hash(root):
    h = hashlib.sha256()
    for file in sorted((Path(root) / 'mg4').glob('*.py')):
        h.update(file.name.encode('utf-8') + b'\0')
        h.update(file.read_bytes())
    return h.hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verify_trial(folder, entry, attacks=8):
    """Return a valid result or raise; never silently mark a bad run valid."""
    folder = Path(folder)
    required = ('result.json', 'events.csv', 'attacks.jsonl', 'alerts.jsonl',
                'cgroup_snapshots.json', 'monitor_status.json', 'pressure_status.json')
    for name in required:
        if not (folder / name).is_file():
            raise ValueError(f'{entry["label"]}: missing {name}')
    rec = _read_json(folder / 'result.json')
    expected = (entry['label'], entry['low_mib'], entry['ai_enabled'], attacks)
    actual = (rec.get('label'), rec.get('low_mib'), rec.get('ai_enabled'), rec.get('attack_count'))
    if expected != actual:
        raise ValueError(f'{entry["label"]}: wrong trial identity/config: {actual}')
    if rec.get('oom_kills') != 0:
        raise ValueError(f'{entry["label"]}: OOM kills reported')
    if rec.get('parent_high_events_from_pressure', 0) <= 0:
        raise ValueError(f'{entry["label"]}: local reclaim pressure not verified')
    if entry['ai_enabled']:
        if rec.get('completed_requests', 0) <= 0 or not rec.get('ai_tokens_per_sec_median'):
            raise ValueError(f'{entry["label"]}: no concurrent AI inference')
        session = rec.get('ai_session')
        if not isinstance(session, str) or not session.startswith('ai_session_') or '/' in session:
            raise ValueError(f'{entry["label"]}: missing/invalid AI session provenance')
        ai_log = folder.parent / session / 'ai_metrics.jsonl'
        if not ai_log.is_file():
            raise ValueError(f'{entry["label"]}: missing source AI inference log')
        inference = read_jsonl(ai_log)
        start, end = rec.get('trial_start_monotonic_ns'), rec.get('trial_end_monotonic_ns')
        if not isinstance(start,int) or not isinstance(end,int) or end <= start:
            raise ValueError(f'{entry["label"]}: invalid AI trial time window')
        live = [x['tokens_per_second'] for x in inference
                if start <= int(x['finish_ns']) <= end and x.get('tokens_per_second') is not None]
        if len(live) != rec['completed_requests'] or not live:
            raise ValueError(f'{entry["label"]}: concurrent inference count inconsistent with source log')
        if abs(statistics.median(live) - rec['ai_tokens_per_sec_median']) > 0.001:
            raise ValueError(f'{entry["label"]}: inference rate inconsistent with source log')
    elif rec.get('completed_requests') != 0 or rec.get('ai_session') is not None:
        raise ValueError(f'{entry["label"]}: unexpected AI metric in AI-off control')
    snaps = _read_json(folder / 'cgroup_snapshots.json')
    before = snaps['before_attacks']
    pre = snaps['pre']
    expected_low_bytes = entry['low_mib'] * (2 ** 20)
    for group in ('monitor', 'parent'):
        if int(pre[group]['memory_low']) != expected_low_bytes:
            raise ValueError(f'{entry["label"]}: unexpected {group} memory.low')
    if int(before['parent']['memory_high']) != 640 * (2 ** 20):
        raise ValueError(f'{entry["label"]}: unexpected local memory.high')
    attacks_raw = read_jsonl(folder / 'attacks.jsonl')
    alerts_raw = read_jsonl(folder / 'alerts.jsonl')
    if len(attacks_raw) != attacks:
        raise ValueError(f'{entry["label"]}: wrong number of attacks')
    ids = [x['id'] for x in attacks_raw]
    if len(set(ids)) != attacks:
        raise ValueError(f'{entry["label"]}: duplicate attack IDs')
    alert_ids = [x['id'] for x in alerts_raw]
    if len(alert_ids) != attacks or set(alert_ids) != set(ids) or len(set(alert_ids)) != attacks:
        raise ValueError(f'{entry["label"]}: missing, duplicate or unexpected alerts')
    lookup = {x['id']: x for x in alerts_raw}
    with (folder / 'events.csv').open(newline='', encoding='utf-8') as file:
        rows = list(csv.DictReader(file))
    if len(rows) != attacks or {x['id'] for x in rows} != set(ids):
        raise ValueError(f'{entry["label"]}: mismatched event CSV')
    csv_lookup = {x['id']: x for x in rows}
    ontime = late = 0
    for attack in attacks_raw:
        alert = lookup[attack['id']]
        delay_ms = (int(alert['alert_monotonic_ns']) - int(attack['trigger_monotonic_ns'])) / 1e6
        if not (0 <= delay_ms < 1_000_000):
            raise ValueError(f'{entry["label"]}: invalid event timing')
        row = csv_lookup[attack['id']]
        if abs(float(row['total_delay_ms']) - delay_ms) > 0.00001:
            raise ValueError(f'{entry["label"]}: event CSV delay inconsistent')
        outcome = 'on_time' if delay_ms <= DEADLINE_MS else 'late'
        if row['status'] != outcome:
            raise ValueError(f'{entry["label"]}: event CSV deadline label inconsistent')
        ontime += outcome == 'on_time'
        late += outcome == 'late'
    if (rec['alerts']['on_time'], rec['alerts']['late'], rec['alerts']['missed']) != (ontime, late, 0):
        raise ValueError(f'{entry["label"]}: result.json deadline count inconsistent')
    if int(rec['total_lookup_major_faults']) != sum(int(x['lookup_major_faults']) for x in alerts_raw):
        raise ValueError(f'{entry["label"]}: major-fault total inconsistent')
    expected_first = next(x for x in attacks_raw if x['id'] == ids[0])
    first = lookup[expected_first['id']]
    first_delay = (int(first['alert_monotonic_ns']) - int(expected_first['trigger_monotonic_ns'])) / 1e6
    if abs(rec['first_alert']['total_delay_ms'] - first_delay) > 0.00001:
        raise ValueError(f'{entry["label"]}: first alert not correctly measured')
    return rec


def collect_results(folder, study_plan, attacks):
    result = []
    for entry in study_plan:
        result.append(verify_trial(Path(folder) / entry['label'], entry, attacks))
    return result


def summarize_study(folder, plan_data):
    entries = plan_data['conditions']
    attack_count = plan_data['attacks_per_trial']
    results = collect_results(folder, entries, attack_count)
    match = {r['label']: r for r in results}
    trial_rows = []
    event_rows = []
    for entry in entries:
        rec = match[entry['label']]
        first = rec['first_alert']
        trial_rows.append({**entry,
            'first_alert_ms': first['total_delay_ms'],
            'first_lookup_ms': first['rule_lookup_ms'],
            'first_faults': first['lookup_major_faults'],
            'major_faults': rec['total_lookup_major_faults'],
            'monitor_pgscan': rec['monitor_pgscan_from_pressure'],
            'monitor_refault': rec['monitor_refault_during_attack'],
            'high_events': rec['parent_high_events_from_pressure'],
            'monitor_memory_psi_us': rec['memory_psi_monitor_during_attack_us'],
            'on_time': rec['alerts']['on_time'], 'late': rec['alerts']['late'],
            'missed': rec['alerts']['missed'],
            'ai_tokens_per_sec': rec['ai_tokens_per_sec_median'],
            'ai_completed_requests': rec['completed_requests'],
            'oom_kills': rec['oom_kills']})
        with (Path(folder) / entry['label'] / 'events.csv').open(newline='', encoding='utf-8') as f:
            for i, event in enumerate(csv.DictReader(f)):
                event_rows.append({**{k:entry[k] for k in ('label', 'block', 'arm', 'ai_enabled', 'low_mib')},
                                   'event_number': i + 1, **event})
    paired_rows = []
    by_arm = {(r['block'], r['arm']): r for r in trial_rows}
    for block in sorted({e['block'] for e in entries}):
        for ai_on, prefix in ((False, 'noai'), (True, 'ai')):
            un = by_arm[(block, prefix + '_low0')]
            pr = by_arm[(block, prefix + '_low192')]
            paired_rows.append({'block':block, 'ai_enabled':ai_on,
                                'unprotected_first_ms':un['first_alert_ms'],
                                'protected_first_ms':pr['first_alert_ms'],
                                'unprotected_minus_protected_ms': un['first_alert_ms'] - pr['first_alert_ms'],
                                'unprotected_first_major_faults':un['first_faults'],
                                'protected_first_major_faults':pr['first_faults']})
    arm_results = {}
    for arm, _, _ in ARMS:
        rs = [r for r in trial_rows if r['arm'] == arm]
        arm_results[arm] = {
            'trials': len(rs), 'first_delay_median_ms':statistics.median(r['first_alert_ms'] for r in rs),
            'first_delay_range_ms':[min(r['first_alert_ms'] for r in rs), max(r['first_alert_ms'] for r in rs)],
            'late_alerts':sum(r['late'] for r in rs),
            'on_time_alerts':sum(r['on_time'] for r in rs),
            'trials_with_first_major_faults':sum(r['first_faults'] > 0 for r in rs),
            'trials_with_reclaim_scan':sum(r['monitor_pgscan'] > 0 for r in rs),
        }
    paired_stats={}
    for ai_on in (False,True):
        ds=[x['unprotected_minus_protected_ms'] for x in paired_rows if x['ai_enabled'] is ai_on]
        paired_stats['ai_on' if ai_on else 'ai_off']={
            'blocks':len(ds), 'median_first_delay_difference_ms':statistics.median(ds),
            'range_ms':[min(ds),max(ds)], 'blocks_where_protection_faster':sum(d>0 for d in ds),
        }
    qc = {'integrity_passed':True,'trials_verified':len(results),
          'expected_trials':len(entries), 'events_verified':len(event_rows),
          'late_250ms':sum(r['late'] for r in trial_rows),
          'missed_250ms':sum(r['missed'] for r in trial_rows),
          'oom_kills':sum(r['oom_kills'] for r in trial_rows),
          'all_trials_had_local_pressure':all(r['high_events'] > 0 for r in trial_rows),
          'arm_summaries':arm_results,'paired_block_summaries':paired_stats,
          'interpretation':'First-alert delay is primary cold-cache latency outcome; 250 ms on-time is separate. No benefits presumed. Reclaim is imposed by memory.high, not by Ollama.',
          'limitations':['Augmented synthetic rule-cache monitor; not original lightweight monitor.',
                         'memory.high imposed inside separate local test slice.',
                         'Ollama concurrent but not causal source of the reclaim workload.',
                         'Trial/block is the repeated measurement unit; event counts must not be used as independent replicates.',
                         'Balanced randomized order reduces but does not remove time and carryover confounding.',
                         'Failed trial attempts are preserved, and reruns must be disclosed.']}
    json_write(Path(folder) / 'study_qc.json', qc)
    for filename, rows in [('trial_metrics.csv', trial_rows),
                           ('event_metrics.csv', event_rows),
                           ('paired_metrics.csv', paired_rows)]:
        with (Path(folder) / filename).open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
    return qc
