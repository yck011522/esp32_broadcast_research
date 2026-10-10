"""Offline checks: python -m unittest discover -s test -p test_9_checks.py."""

import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import test_9_ble_test as logger


LINE = (
    "BLE_STATS window_s=5.000 received=100 seq_lost=0 loss_percent=0.00 "
    "rate_hz=20.00 max_gap_s=0.199 silence_s=0.051 have_packet=1 world_seq=3243 "
    "rtt_avg_ms=84.0 rtt_max_ms=180 rtt_samples=100"
)


class LoggerChecks(unittest.TestCase):
    def test_measurements_and_unavailable_rtt(self):
        row = logger.parse_report(LINE)
        self.assertEqual(row["received"], 100)
        self.assertEqual(row["rtt_avg_ms"], 84.0)
        self.assertEqual(row["world_seq"], 3243)
        self.assertEqual(row["max_gap_s"], 0.199)
        unavailable = LINE.replace("rtt_avg_ms=84.0 rtt_max_ms=180 rtt_samples=100",
                                   "rtt_avg_ms=NA rtt_max_ms=NA rtt_samples=0")
        row = logger.parse_report(unavailable)
        self.assertNotIn("rtt_avg_ms", row)
        self.assertEqual(row["rtt_samples"], 0)
        self.assertIsNone(logger.parse_report("BLE_STATS window_s=5.000 received=100"))
        self.assertIsNone(logger.parse_report("boot message"))

    def test_chunked_serial_to_files(self):
        import serial

        class FakeSerial:
            def __init__(self, **kwargs):
                self.chunks = [b"boot message\r\nBLE_STA", LINE[7:].encode() + b"\r",
                               b"\ntrailing partial"]
                self.closed = False

            def open(self):
                pass

            @property
            def in_waiting(self):
                return 4096

            def read(self, size):
                time.sleep(0.001)
                return self.chunks.pop(0) if self.chunks else b""

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            link = FakeSerial()
            args = ["test_9_ble_test.py", "--duration-seconds", "0.05",
                    "--output-dir", directory]
            with patch("sys.argv", args), patch.object(serial, "Serial", return_value=link), \
                    contextlib.redirect_stdout(io.StringIO()):
                logger.main()
            root = Path(directory)
            with next(root.glob("*.csv")).open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            stats = [row for row in rows if row["record_type"] == "ble_stats"]
            self.assertEqual(len(stats), 1)
            self.assertEqual(stats[0]["rtt_avg_ms"], "84.0")
            raw = [json.loads(line) for line in next(root.glob("*.jsonl")).read_text().splitlines()]
            self.assertTrue(any(row["message"] == "boot message" for row in raw))
            self.assertTrue(any(row["record_type"] == "partial_line" for row in raw))
            summary = json.loads(next(root.glob("*.summary.json")).read_text())
            self.assertEqual(summary["ble_stats_reports"], 1)
            self.assertEqual(summary["status"], "completed")
            self.assertTrue(link.closed)


if __name__ == "__main__":
    unittest.main()
