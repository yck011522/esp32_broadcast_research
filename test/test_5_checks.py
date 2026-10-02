"""Offline checks for Test 5's minute accounting."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_5_loss_run import SlaveMinute  # noqa: E402


def telemetry(world, lost, events=0, maximum=0):
    return (1, 1, world, lost, events, maximum)


class SlaveMinuteTests(unittest.TestCase):
    def test_confirmed_reset_counts_loss_before_first_telemetry(self):
        minute = SlaveMinute(True, 0)
        minute.receive(0.05, telemetry(4, 2, 1, 2))
        minute.receive(1.0, telemetry(99, 5, 4, 2))
        row = minute.row(1.1, 100)
        self.assertEqual(row["status"], "OK")
        self.assertEqual(row["world_packets_lost"], 5)
        self.assertEqual(row["world_loss_percent"], 5)
        self.assertEqual(row["max_world_gap"], 2)

    def test_unconfirmed_reset_uses_first_telemetry_as_baseline(self):
        minute = SlaveMinute(False, 0)
        minute.receive(0.05, telemetry(104, 24, 9, 10))
        minute.receive(1.0, telemetry(199, 27, 11, 10))
        row = minute.row(1.1, 100)
        self.assertEqual(row["status"], "RESET_UNCONFIRMED")
        self.assertEqual(row["world_packets_lost"], 3)
        self.assertEqual(row["max_world_gap"], "")

    def test_unexpected_counter_reset_marks_partial(self):
        minute = SlaveMinute(True, 0)
        minute.receive(0.1, telemetry(10, 2, 2, 1))
        minute.receive(0.5, telemetry(50, 4, 4, 1))
        minute.receive(0.6, telemetry(1, 0, 0, 0))
        minute.receive(1.0, telemetry(40, 3, 2, 2))
        row = minute.row(1.1, 100)
        self.assertEqual(row["status"], "COUNTER_REBASE")
        self.assertEqual(row["reset_confirmed"], 1)
        self.assertEqual(row["world_packets_lost"], 7)
        self.assertEqual(row["max_world_gap"], "")

    def test_absent_slave_has_no_loss_estimate(self):
        row = SlaveMinute(False, 0).row(60, 6000)
        self.assertEqual(row["status"], "NO_TELEMETRY")
        self.assertNotIn("world_packets_lost", row)

    def test_stale_slave_retains_observed_count(self):
        minute = SlaveMinute(True, 0)
        minute.receive(1, telemetry(10, 1, 1, 1))
        row = minute.row(60, 6000)
        self.assertEqual(row["status"], "STALE")
        self.assertEqual(row["world_packets_lost"], 1)
        self.assertGreater(row["max_telemetry_silence_s"], 58)


if __name__ == "__main__":
    unittest.main()
