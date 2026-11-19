import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibrate import diff, read_pairs, read_psi
from worker import create_and_warm


class CalibrationUnitTests(unittest.TestCase):
    def test_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'stat'
            f.write_text('pgmajfault 3\nworkingset_refault_file 10\nother max\n')
            self.assertEqual(read_pairs(f), {'pgmajfault': 3, 'workingset_refault_file': 10})

    def test_psi_totals(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'pressure'
            f.write_text('some avg10=0.00 avg60=0.00 total=312\nfull avg10=0.00 total=20\n')
            self.assertEqual(read_psi(f), {'some': 312, 'full': 20})

    def test_deltas(self):
        a = {'monitor': {'memory_stat': {'pgmajfault': 3}}}
        b = {'monitor': {'memory_stat': {'pgmajfault': 7}}}
        self.assertEqual(diff(a['monitor'], b['monitor'], 'memory_stat', 'pgmajfault'), 4)

    def test_file_warm(self):
        with tempfile.TemporaryDirectory() as d:
            mm = create_and_warm(Path(d) / 'test.bin', 1)
            self.assertEqual(len(mm), 1024 * 1024)
            self.assertEqual(mm[4096], 1)
            mm.close()


if __name__ == '__main__':
    unittest.main()
