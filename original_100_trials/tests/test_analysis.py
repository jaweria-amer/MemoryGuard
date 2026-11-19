import json
import tempfile
import unittest
from pathlib import Path

from memoryguard.analysis import (percentile, summarise, write_trial_report,
                                  cgroup_deltas, pressure_totals)
from memoryguard.monitor import extract_event_id


class AnalysisTests(unittest.TestCase):
    def test_counts_late_missed_and_ontime(self):
        t = [
            {"id": "a", "trigger_monotonic_ns": 1_000_000_000},
            {"id": "b", "trigger_monotonic_ns": 2_000_000_000},
            {"id": "c", "trigger_monotonic_ns": 3_000_000_000},
        ]
        a = [
            {"id": "a", "alert_monotonic_ns": 1_100_000_000},
            {"id": "a", "alert_monotonic_ns": 1_200_000_000},
            {"id": "b", "alert_monotonic_ns": 2_400_000_000},
            {"id": "extraneous", "alert_monotonic_ns": 5_000_000_000},
        ]
        result = summarise(t, a)
        self.assertEqual((result["on_time"], result["late"], result["missed"]), (1, 1, 1))
        self.assertAlmostEqual(result["on_time_pct"], 100/3)
        self.assertEqual(result["median_observed_delay_ms"], 250.0)
        self.assertEqual([x["status"] for x in result["matched"]],
                         ["on_time", "late", "missed"])

    def test_deadline_is_inclusive(self):
        t = [{"id": "one", "trigger_monotonic_ns": 100_000_000}]
        a = [{"id": "one", "alert_monotonic_ns": 350_000_000}]
        self.assertEqual(summarise(t, a)["on_time"], 1)

    def test_missing_alert_has_no_invented_latency(self):
        r = summarise([{"id": "x", "trigger_monotonic_ns": 10}], [])
        self.assertEqual(r["on_time_pct"], 0)
        self.assertIsNone(r["p99_observed_delay_ms"])
        self.assertIsNone(r["matched"][0]["delay_ms"])

    def test_negative_delay_fails(self):
        with self.assertRaises(ValueError):
            summarise([{"id": "x", "trigger_monotonic_ns": 10}],
                      [{"id": "x", "alert_monotonic_ns": 9}])

    def test_parser_matches_only_fake_secret_filename(self):
        self.assertEqual(extract_event_id("MG_OPEN /tmp/fake/mgsecret_test_0001.txt"), "test_0001")
        self.assertIsNone(extract_event_id("MG_OPEN /etc/shadow"))
        self.assertIsNone(extract_event_id("something else"))

    def test_writes_summary_from_synthetic_fixture(self):
        with tempfile.TemporaryDirectory() as base:
            folder = Path(base)
            (folder / "attacks.jsonl").write_text(json.dumps({
                "id": "z", "trigger_monotonic_ns": 1_000_000_000}) + "\n")
            (folder / "alerts.jsonl").write_text(json.dumps({
                "id": "z", "alert_monotonic_ns": 1_200_000_000}) + "\n")
            result = write_trial_report(folder)
            self.assertEqual(result["on_time"], 1)
            self.assertTrue((folder / "matched.csv").exists())
            self.assertTrue((folder / "summary.json").exists())

    def test_cgroup_metrics_are_differences_not_levels(self):
        def record(major, stall, rss):
            return {"files": {
                "memory.low": "67108864\n", "memory.current": str(rss),
                "memory.stat": f"pgmajfault {major}\npgfault 21\n",
                "memory.events": "oom 0\noom_kill 0\n",
                "cpu.stat": "usage_usec 8\n",
                "memory.pressure": f"some avg10=0.1 total={stall}\nfull avg10=0.0 total=0\n",
                "cpu.pressure": "some avg10=0.0 total=7\n",
            }}
        info = {"monitor_start": record(20, 100, 500),
                "monitor_end": record(25, 410, 750)}
        out = cgroup_deltas(info)["monitor"]
        self.assertEqual(out["memory.stat_pgmajfault_delta"], 5)
        self.assertEqual(out["memory.pressure_some_stall_us_delta"], 310)
        self.assertEqual(out["memory_current_end_bytes"], 750)
        self.assertEqual(out["memory_low_bytes"], 67108864)

    def test_percentile_handles_empty_single_and_interpolation(self):
        self.assertIsNone(percentile([], 99))
        self.assertEqual(percentile([4.0], 99), 4.0)
        self.assertEqual(percentile([1, 3], 50), 2)


if __name__ == "__main__":
    unittest.main()
