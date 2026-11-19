"""Offline checks: preserve complete trials and archive incomplete attempts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memoryguard import runner


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = self.root / 'results' / 'batch_20261103T052931Z'
        self.batch.mkdir(parents=True)
        self.condition = runner.CONDITIONS[3]
        self.pressure = 5120
        self.repeat = 3
        self.out = self.batch / 'r3_p5120_low_128m'
        self.model = 'qwen2.5:1.5b'

    def _valid(self):
        self.out.mkdir(exist_ok=True)
        meta = {'trial_id':'mgtest1', 'experimental_valid': True,
                'condition': self.condition.name,
                'memory_low_mb': self.condition.low_mb,
                'cpu_weight': self.condition.cpu_weight, 'nice': self.condition.nice,
                'pressure_mb': self.pressure, 'repeat': self.repeat,
                'model': self.model, 'attack_count': 30, 'deadline_ms': 250}
        (self.out / 'trial_metadata.json').write_text(json.dumps(meta))
        summary = {'trial':meta, 'total_attacks': 30, 'on_time':29,
                   'late': 1, 'missed': 0, 'observed_alerts': 30,
                   'ai_completed_requests': 10,
                   'cgroup_changes':{'monitor':{
                       'memory_low_bytes':128*1024*1024}}}
        (self.out / 'summary.json').write_text(json.dumps(summary))
        for name in ('attacks.jsonl','alerts.jsonl','ai_metrics.jsonl','cgroup_snapshots.json','matched.csv'):
            (self.out / name).write_text('original evidence')
        return meta

    def test_valid_trial_is_preserved_and_skipped(self):
        self._valid()
        self.assertTrue(runner.verify_finished_trial(self.out, self.condition, self.pressure,
                                                      self.repeat, 30, self.model))
        self.assertEqual((self.out / 'matched.csv').read_text(), 'original evidence')

    def test_incomplete_trial_archived_outside_original_batch(self):
        self.out.mkdir()
        (self.out / 'ai_status.json').write_text('{"state":"error"}')
        self.assertFalse(runner.verify_finished_trial(self.out, self.condition, self.pressure,
                                                       self.repeat, 30, self.model))
        archived=runner.archive_incomplete_attempt(self.out, self.batch)
        self.assertFalse(self.out.exists())
        self.assertTrue((archived / 'ai_status.json').exists())
        self.assertIn('_failed_attempts',str(archived))

    def test_mismatch_in_claimed_valid_trial_is_never_overwritten(self):
        self._valid()
        with self.assertRaisesRegex(RuntimeError,'mismatch'):
            runner.verify_finished_trial(self.out,self.condition,4096,self.repeat,30,self.model)
        self.assertTrue((self.out / 'matched.csv').exists())

    def test_resume_checks_original_configuration(self):
        env={'experiment_type':'full','pressures_mib':[0,2048,4096,5120],
             'repeats':5,'attacks_per_trial':30,
             'condition_config':[vars(c) for c in runner.CONDITIONS],
             'kernel':'test-kernel','architecture':'test-machine'}
        (self.batch / 'environment.json').write_text(json.dumps(env))
        with patch.object(runner,'ROOT',self.root), patch.object(runner.platform,'release',return_value='test-kernel'),patch.object(runner.platform,'machine',return_value='test-machine'):
            returned=runner.verify_resume_environment(self.batch,[0,2048,4096,5120],5,30,self.model)
            self.assertEqual(returned,env)
            with self.assertRaisesRegex(RuntimeError,'differs'):
                runner.verify_resume_environment(self.batch,[0,2048,4096,5121],5,30,self.model)
            with self.assertRaisesRegex(ValueError,'Resume path'):
                runner.verify_resume_environment(self.root/'outside',[0,2048,4096,5120],5,30,self.model)


if __name__ == '__main__':
    unittest.main()
