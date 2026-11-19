"""Read-only audit of an extracted Phase 3 result archive; no VM needed."""
import csv
import json
import statistics
import sys
from pathlib import Path


def read_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def verify(base):
    base = Path(base)
    summary = json.loads((base / 'pilot_summary.json').read_text())
    plan = json.loads((base / 'plan.json').read_text())
    results = []
    for label, low, ai in plan['conditions']:
        folder = base / label
        r = json.loads((folder / 'result.json').read_text())
        events = read_jsonl(folder / 'attacks.jsonl')
        alerts = read_jsonl(folder / 'alerts.jsonl')
        assert len(events) == len(alerts) == 8
        ids = {x['id'] for x in events}
        assert len(ids) == 8 and {x['id'] for x in alerts} == ids
        lookup = {x['id']:x for x in alerts}
        delays=[(lookup[e['id']]['alert_monotonic_ns'] - e['trigger_monotonic_ns'])/1e6 for e in events]
        assert min(delays)>=0
        assert sum(d<=250 for d in delays)==8
        snaps=json.loads((folder/'cgroup_snapshots.json').read_text())
        assert int(snaps['pre']['monitor']['memory_low']) == int(snaps['pre']['parent']['memory_low']) == low * 2**20
        assert int(snaps['before_attacks']['parent']['memory_high']) == 640 * 2**20
        assert r['low_mib'] == low and r['ai_enabled'] == ai
        assert r['oom_kills'] == 0 and r['parent_high_events_from_pressure']>0
        assert abs(r['first_alert']['total_delay_ms']-delays[0]) < 1e-8
        assert (r['completed_requests']>0)==ai
        results.append({'label':label, 'low_mib':low,'ai_enabled':ai,
                        'first_alert_ms':delays[0],
                        'median_all_eight_ms':statistics.median(delays),
                        'min_alert_ms':min(delays), 'max_alert_ms':max(delays),
                        'faults':r['total_lookup_major_faults'],
                        'refaults':r['monitor_refault_during_attack'],
                        'reclaim_scans':r['monitor_pgscan_from_pressure'],
                        'high_events':r['parent_high_events_from_pressure'],
                        'inference_requests_during_trial':r['completed_requests'],
                        'ai_tokens_per_second_median':r['ai_tokens_per_sec_median']})
    ai_root=base/'ai_service'
    ai_logs=read_jsonl(ai_root/'ai_metrics.jsonl')
    for item in results:
        if item['ai_enabled']:
            folder=base/item['label']
            e=read_jsonl(folder/'attacks.jsonl')
            # Valid completed Ollama request spans need nonzero overlap with file-open sequence.
            t0=min(x['trigger_monotonic_ns'] for x in e)
            t1=max(x['trigger_monotonic_ns'] for x in e)
            assert any(t0<=x['finish_ns']<=t1 for x in ai_logs)
    assert len(results)==4 and summary['mechanism_pilot_passed']
    return results


if __name__=='__main__':
    data=verify(sys.argv[1])
    print(json.dumps(data, indent=2))
