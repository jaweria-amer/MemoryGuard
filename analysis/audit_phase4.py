#!/usr/bin/env python3
"""Re-audit the bundled original Phase 4 evidence with Python stdlib only.

No source files are changed. Checks every raw event against its alert timestamp,
confirms cgroup memory.low/high and OOM metadata, then prints paired contrasts.
"""
import csv, hashlib, json, statistics, tarfile
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

HERE=Path(__file__).resolve().parent.parent
archive=HERE/'original_evidence/mg_phase4_results.tar.gz'
expected=json.loads((HERE/'provenance.json').read_text())
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected['original_archive_sha256']

def jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

with TemporaryDirectory() as tmp:
    with tarfile.open(archive,'r:gz') as t:
        for m in t.getmembers():
            if m.name.startswith('/') or '..' in Path(m.name).parts or m.issym() or m.islnk() or m.isdev():
                raise AssertionError('Unsafe archive member')
        t.extractall(tmp,filter='data')
    studies=list(Path(tmp).glob('phase4_results/study_*'))
    assert len(studies)==1
    study=studies[0]
    plan=json.loads((study/'study_plan.json').read_text())
    assert plan['code_sha256']==expected['phase4_code_sha256_verified']
    effects={}
    arm_vals={}
    counts=Counter();ai_overlaps=0;alerts=0
    for c in plan['conditions']:
        folder=study/c['label']
        result=json.loads((folder/'result.json').read_text())
        attacks=jsonl(folder/'attacks.jsonl')
        observed=jsonl(folder/'alerts.jsonl')
        att={x['id']:x for x in attacks}; obs={x['id']:x for x in observed}
        assert len(att)==len(obs)==len(attacks)==len(observed)==8 and set(att)==set(obs)
        assert result['oom_kills']==result['alerts']['missed']==result['alerts']['late']==0
        snap=json.loads((folder/'cgroup_snapshots.json').read_text())
        for group in ('parent','monitor'):
            assert int(snap['pre'][group]['memory_low'])==c['low_mib']*1048576
        assert int(snap['before_attacks']['parent']['memory_high'])==640*1048576
        assert snap['before_attacks']['parent']['memory_events']['high'] > snap['pre']['parent']['memory_events']['high']
        if c['ai_enabled']:
            reqs=jsonl(study/result['ai_session']/'ai_metrics.jsonl')
            done=[q for q in reqs if result['trial_start_monotonic_ns']<=q['finish_ns']<=result['trial_end_monotonic_ns']]
            assert len(done)==result['completed_requests']>0
            assert all(any(q['start_ns']<=a['trigger_monotonic_ns']<=q['finish_ns'] for q in reqs) for a in attacks)
            ai_overlaps+=8
        for a in attacks:
            b=obs[a['id']]
            delay=(b['alert_monotonic_ns']-a['trigger_monotonic_ns'])/1e6
            assert 0<=delay<=250
            alerts+=1
        first=(obs[attacks[0]['id']]['alert_monotonic_ns']-attacks[0]['trigger_monotonic_ns'])/1e6
        assert abs(first-result['first_alert']['total_delay_ms'])<1e-5
        key=(c['block'],c['ai_enabled'],c['low_mib'])
        effects[key]=first
        arm_vals.setdefault(c['arm'],[]).append(first)
        counts[c['arm']]+=1
    assert len(plan['conditions'])==32 and set(counts.values())=={8}
    assert alerts==256 and ai_overlaps==128
    print('VERIFIED: 32 trials, 256 alerts, 128 concurrent AI-on events, 0 late/missing, 0 OOM kills')
    for arm in sorted(arm_vals):
        print(f'{arm}: first-alert mean {statistics.mean(arm_vals[arm]):.3f} ms (n=8)')
    for ai in (False,True):
        diff=[effects[(b,ai,0)]-effects[(b,ai,192)] for b in range(1,9)]
        assert all(x>0 for x in diff)
        print(f'AI={ai}: paired mean difference {statistics.mean(diff):.3f} ms, positive in 8/8 blocks')
