import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memoryguard.attack import generate, get_process_name


class AttackTests(unittest.TestCase):
    def test_attack_writes_distinct_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("memoryguard.attack.set_process_name"):
                events = generate("trialA", root, 3, 0)
            self.assertEqual(len(events), 3)
            self.assertEqual(len({x["id"] for x in events}), 3)
            self.assertEqual(len((root / "attacks.jsonl").read_text().splitlines()), 3)
            for event in events:
                self.assertTrue(Path(event["path"]).exists())
                self.assertIn("trigger_monotonic_ns", event)

    def test_process_comm_restored_after_generating_events(self):
        with tempfile.TemporaryDirectory() as directory:
            original = get_process_name()
            generate("trialOne", Path(directory), 2, 0)
            self.assertEqual(get_process_name(), original)
            generate("trialTwo", Path(directory), 2, 0)
            self.assertEqual(get_process_name(), original)

    def test_invalid_trial_name_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                generate("bad/id", Path(directory), 2, 0)


if __name__ == "__main__":
    unittest.main()
