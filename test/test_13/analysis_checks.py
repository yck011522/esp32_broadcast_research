"""Checks for independent Test 13 analysis, including corrupted raw sequences."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from test.test_13.analyze_test_13 import distribution, read_raw, sequence_change, server_observations


class AnalysisChecks(unittest.TestCase):
    def test_wrap_duplicate_backward_and_percentiles(self):
        self.assertEqual(sequence_change(65535, 0), (0, False, True))
        self.assertEqual(sequence_change(2, 2), (0, True, False))
        self.assertEqual(sequence_change(2, 1), (0, True, False))
        self.assertEqual(sequence_change(1, 4), (2, False, False))
        self.assertEqual(distribution([1, 2, 3])["median"], 2)
        self.assertIsNone(distribution([]))

    def test_raw_gap_is_detected_even_when_logger_says_accepted(self):
        def packet(sequence, when):
            data = struct.pack("<BHH", 2, sequence, 0)+bytes(16)
            return {"phase": "traffic", "record_type": "telemetry", "elapsed_s": when,
                    "data_hex": data.hex(), "accepted": "accepted"}
        rows = [packet(65535, 1), packet(1, 1.05), packet(1, 1.06)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"raw.jsonl"
            path.write_text("".join(json.dumps(r)+"\n" for r in rows))
            _, states, _, failures = read_raw(path, [2])
        self.assertEqual(states[2]["sequence_gaps"], 1)
        self.assertEqual(states[2]["sequence_errors"], 1)
        self.assertEqual(states[2]["received"], 2)
        self.assertTrue(failures)

    def test_server_totals_include_shutdown_but_window_rates_exclude_it(self):
        def world(at, rate, total):
            return (f"[ {at}s] car world: window=5.000 rate={rate} seq_lost=0 silence=0.010 "
                    f"duplicates=0 old=0 malformed=0 total_received={total} total_lost=0\n").replace("[ ", "[")
        text = world(1, 0, 10)+world(6, 40, 210)+world(11, 50, 460)+world(16, 10, 510)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"server.log"
            path.write_text(text)
            result = server_observations(path, 2, 12)
        self.assertEqual(result["received_delta"], 500)
        self.assertEqual(result["full_traffic_windows"], 1)
        self.assertEqual(result["window_rate_hz"]["mean"], 50)


if __name__ == "__main__":
    unittest.main()
