"""Safe pure-function tests; do not require kernel BPF or VM root."""
import json
import tempfile
import unittest
from pathlib import Path
from mg3.cache import rule_offsets, check_rules
from mg3.common import delta, read_stat_pairs, read_psi
from mg3.monitor import event_id_from_line
from mg3.ai import rate
from mg3.runner import plan
from mg3.analysis import summarise

class PilotTests(unittest.TestCase):
    def test_order_is_counterbalanced(self):
        self.assertEqual([(x[1], x[2]) for x in plan(1)],
                         [(0, False), (192, False), (192, True), (0, True)])
        self.assertEqual([(x[1], x[2]) for x in plan(2)][4:],
                         [(0, True), (192, True), (192, False), (0, False)])
    def test_four_trial_pilot(self):
        self.assertEqual(len(list(plan(1))), 4)
    def test_offsets_are_deterministic_and_unique(self):
        x = rule_offsets(128 * 1024**2, 512)
        self.assertEqual(x, rule_offsets(128 * 1024**2, 512))
        self.assertEqual(len(set(x)), 512)
        self.assertTrue(all(0 <= a < 128 * 1024**2 for a in x))
    def test_rule_cache_passes(self):
        data = bytearray(b'\x01' * (3 * 4096))
        self.assertTrue(check_rules(data, [0, 4096, 8192]))
        data[4096] = 0
        self.assertFalse(check_rules(data, [0, 4096, 8192]))
    def test_only_fake_attack_lines(self):
        self.assertEqual(event_id_from_line('MG_OPEN /tmp/mgsecret_trial_0001.txt'), 'trial_0001')
        self.assertIsNone(event_id_from_line('MG_OPEN /etc/shadow'))
        self.assertIsNone(event_id_from_line('MG_OPEN /tmp/mgsecret_%%%_.txt'))
    def test_analytic_deadline_includes_lookup(self):
        events = [{'id':'x','trigger_monotonic_ns':10_000_000}]
        alerts = [{'id':'x','alert_monotonic_ns':265_000_000,'lookup_ms':254}]
        results = summarise(events, alerts)
        self.assertEqual(results['late'], 1)
    def test_ai_rate_guard(self):
        self.assertEqual(rate({'eval_count': 20, 'eval_duration': 500_000_000}), 40)
        self.assertIsNone(rate({'eval_count': 0, 'eval_duration': 500_000_000}))
    def test_delta_counters(self):
        x = {'memory_stat': {'pgmajfault':10}}; y={'memory_stat':{'pgmajfault':51}}
        self.assertEqual(delta(x,y,'memory_stat','pgmajfault'),41)
    def test_parse_psi(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'memory.pressure'
            path.write_text('some avg10=0.00 total=1234\nfull avg10=0.00 total=234\n')
            self.assertEqual(read_psi(path), {'some':1234,'full':234})
    def test_read_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'stat'; path.write_text('pgmajfault 4\nother 27\n')
            self.assertEqual(read_stat_pairs(path)['pgmajfault'],4)
    def test_invalid_probe_sizes(self):
        with self.assertRaises(ValueError):
            rule_offsets(4096*10, 11)
    def test_plan_names_unique(self):
        names=[x[0] for x in plan(3)]
        self.assertEqual(len(names),len(set(names)))

if __name__ == '__main__':
    unittest.main()
