"""Pure, cross-platform checks; do not require a running BPF kernel."""
import copy
import csv
import json
import statistics
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from mg4.ai import rate
from mg4.cache import rule_offsets, check_rules
from mg4.monitor import event_id_from_line
from mg4.study import balanced_plan, verify_trial, code_hash


class Phase4Tests(unittest.TestCase):
    def test_32_unique_trials(self):
        rows = balanced_plan()
        self.assertEqual(len(rows), 32)
        self.assertEqual(len(set(r['label'] for r in rows)), 32)
    def test_positions_counterbalanced(self):
        rows = balanced_plan()
        count = Counter((r['arm'], r['position']) for r in rows)
        self.assertEqual(len(count), 16)
        self.assertTrue(all(x == 2 for x in count.values()))
    def test_each_block_has_four_treatments(self):
        rows = balanced_plan()
        for b in range(1,9):
            self.assertEqual(len({r['arm'] for r in rows if r['block'] == b}),4)
    def test_sequential_pairs_counterbalanced(self):
        rows=balanced_plan()
        trans=Counter()
        for b in range(1,9):
            arm_ids=[r['arm'] for r in rows if r['block']==b]
            trans.update(zip(arm_ids[:-1],arm_ids[1:]))
        self.assertEqual(len(trans), 12)
        self.assertTrue(all(v==2 for v in trans.values()))
    def test_plan_reproducible(self):
        self.assertEqual(balanced_plan(8, 20261103),balanced_plan(8,20261103))
        self.assertNotEqual(balanced_plan(8,1),balanced_plan(8,20261103))
    def test_disallow_unbalanced(self):
        for blocks in (0,1,2,6,13):
            with self.assertRaises(ValueError):
                balanced_plan(blocks)
    def test_deterministic_fake_rule_cache(self):
        offsets = rule_offsets(128 * 1024**2, 512)
        self.assertEqual(offsets,rule_offsets(128 * 1024**2,512))
        self.assertEqual(len(set(offsets)),512)
        buffer=bytearray(b'\x01'*8192)
        self.assertTrue(check_rules(buffer,[0,4096]))
        buffer[4096]=0
        self.assertFalse(check_rules(buffer,[0,4096]))
    def test_eBPF_event_filter_fake_files_only(self):
        self.assertEqual(event_id_from_line('MG_OPEN /tmp/mgsecret_b01_noai_low0_0000.txt'),'b01_noai_low0_0000')
        self.assertIsNone(event_id_from_line('MG_OPEN /etc/shadow'))
    def test_ai_rate(self):
        self.assertEqual(rate({'eval_count':48,'eval_duration':600_000_000}),80)
        self.assertIsNone(rate({'eval_count':0,'eval_duration':100_000_000}))
    def test_code_fingerprint(self):
        self.assertEqual(code_hash(Path(__file__).parents[1]),code_hash(Path(__file__).parents[1]))

    def _fixture(self, root, ai=False):
        entry={'label':'b01_ai_low192' if ai else 'b01_noai_low192',
               'low_mib':192,'ai_enabled':ai}
        folder=Path(root)/entry['label']; folder.mkdir()
        events=[{'id':f'case_{i}', 'trigger_monotonic_ns':100_000_000+i*1_000_000_000} for i in range(8)]
        alerts=[{'id':e['id'], 'alert_monotonic_ns':e['trigger_monotonic_ns']+300_000,
                 'lookup_ms':0.15,'lookup_major_faults':0} for e in events]
        with (folder/'attacks.jsonl').open('w') as f:
            for e in events:f.write(json.dumps(e)+'\n')
        with (folder/'alerts.jsonl').open('w') as f:
            for e in alerts:f.write(json.dumps(e)+'\n')
        with (folder/'events.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,['id','trigger_ns','alert_ns','total_delay_ms','rule_lookup_ms','lookup_major_faults','status'])
            writer.writeheader()
            for ev,al in zip(events,alerts):
                writer.writerow({'id':ev['id'],'trigger_ns':ev['trigger_monotonic_ns'],
                                 'alert_ns':al['alert_monotonic_ns'],'total_delay_ms':0.3,
                                 'rule_lookup_ms':0.15,'lookup_major_faults':0,'status':'on_time'})
        snap={'memory_low':str(192*2**20),'memory_high':str(640*2**20)}
        cgroups={'pre':{'monitor':snap,'parent':snap},
                 'before_attacks':{'parent':snap}}
        (folder/'cgroup_snapshots.json').write_text(json.dumps(cgroups))
        (folder/'monitor_status.json').write_text('{}')
        (folder/'pressure_status.json').write_text('{}')
        result={'label':entry['label'],'low_mib':192,'ai_enabled':ai,
                'attack_count':8,'oom_kills':0,'parent_high_events_from_pressure':40,
                'total_lookup_major_faults':0,'first_alert':{'total_delay_ms':0.3},
                'alerts':{'on_time':8,'late':0,'missed':0},'completed_requests':0,
                'ai_session':None,'ai_tokens_per_sec_median':None}
        if ai:
            session=Path(root)/'ai_session_01';session.mkdir()
            measurements=[{'start_ns':1, 'finish_ns':10,'tokens_per_second':55.0},
                          {'start_ns':11,'finish_ns':20,'tokens_per_second':65.0}]
            (session/'ai_metrics.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in measurements))
            result.update({'completed_requests':2,'ai_session':'ai_session_01',
                           'ai_tokens_per_sec_median':60.0,
                           'trial_start_monotonic_ns':1,'trial_end_monotonic_ns':21})
        (folder/'result.json').write_text(json.dumps(result))
        return folder,entry,result

    def test_accepts_complete_non_ai_trial(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d)
            self.assertEqual(verify_trial(folder,entry)['attack_count'],8)
    def test_accepts_ai_with_actual_inference_log(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d,ai=True)
            self.assertEqual(verify_trial(folder,entry)['completed_requests'],2)
    def test_rejects_incomplete_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d)
            (folder/'events.csv').unlink()
            with self.assertRaisesRegex(ValueError,'missing events.csv'):
                verify_trial(folder,entry)
    def test_rejects_wrong_cgroup_protection(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d)
            snap=json.loads((folder/'cgroup_snapshots.json').read_text())
            snap['pre']['monitor']['memory_low']='0'
            (folder/'cgroup_snapshots.json').write_text(json.dumps(snap))
            with self.assertRaisesRegex(ValueError,'memory.low'):
                verify_trial(folder,entry)
    def test_rejects_claimed_ai_without_request_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d,ai=True)
            (Path(d)/'ai_session_01'/'ai_metrics.jsonl').write_text('')
            with self.assertRaisesRegex(ValueError,'concurrent inference'):
                verify_trial(folder,entry)
    def test_rejects_oom(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,rec=self._fixture(d)
            rec['oom_kills']=1
            (folder/'result.json').write_text(json.dumps(rec))
            with self.assertRaisesRegex(ValueError,'OOM'):
                verify_trial(folder,entry)
    def test_rejects_incorrect_deadline_label(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d)
            p=folder/'events.csv'
            p.write_text(p.read_text().replace('on_time','late'))
            with self.assertRaisesRegex(ValueError,'deadline label'):
                verify_trial(folder,entry)
    def test_rejects_duplicate_alerts(self):
        with tempfile.TemporaryDirectory() as d:
            folder,entry,_=self._fixture(d)
            p=folder/'alerts.jsonl'
            rows=p.read_text().splitlines()
            p.write_text('\n'.join(rows[:-1]+[rows[0]])+'\n')
            with self.assertRaisesRegex(ValueError,'duplicate'):
                verify_trial(folder,entry)

if __name__=='__main__':
    unittest.main()
