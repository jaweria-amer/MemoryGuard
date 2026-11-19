import json
import tempfile
import unittest
from pathlib import Path

from memoryguard.qc import inspect_batch


class QualityControlTests(unittest.TestCase):
    def test_smoke_flags_unverified_pressure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'environment.json').write_text(json.dumps({
                'pressures_mib': [0, 4096], 'repeats': 1, 'attacks_per_trial': 10,
                'condition_config': [{'name': 'baseline'}, {'name': 'low_128m'}]
            }))
            for pressure in (0, 4096):
                for cond in ('baseline', 'low_128m'):
                    x = root / f'r1_p{pressure}_{cond}'
                    x.mkdir()
                    (x / 'summary.json').write_text(json.dumps({
                        'trial': {'experimental_valid': True},
                        'total_attacks': 10, 'on_time': 10, 'late': 0, 'missed': 0,
                        'on_time_pct': 100, 'ai_completed_requests': 1,
                        'p99_observed_delay_ms': 5.0,
                        'ai_tokens_per_second_median': 16.0,
                        'cgroup_changes': {'ai': {'memory.pressure_some_stall_us_delta': 0,
                                                  'memory.events_oom_kill_delta': 0}}
                    }))
            report = inspect_batch(root, 'smoke')
            self.assertFalse(report['passed'])
            self.assertEqual(report['valid_trials'], 4)
            self.assertFalse(report['high_pressure_psi_observed'])
            x = root / 'r1_p4096_low_128m' / 'summary.json'
            data = json.loads(x.read_text())
            data['cgroup_changes']['ai']['memory.pressure_some_stall_us_delta'] = 300
            x.write_text(json.dumps(data))
            report = inspect_batch(root, 'smoke')
            self.assertTrue(report['passed'])
            self.assertTrue((root / 'trial_table.csv').is_file())
            self.assertTrue((root / 'condition_table.csv').is_file())


if __name__ == '__main__':
    unittest.main()
