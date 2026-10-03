"""Offline checks for Test 7's interval statistics."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_7_ota_unicast import IntervalState  # noqa: E402


def report(seq, world, lost, gap=0, events=0):
    return (2, seq, world, lost, events, gap)


class IntervalTests(unittest.TestCase):
    def test_confirmed_reset_counts_initial_world_loss_and_telemetry_gaps(self):
        state = IntervalState(2, True, 0)
        state.receive(0.1, report(1, 9, 2, 2, 1))
        state.receive(0.25, report(4, 24, 4, 2, 3))
        row = state.row(0.3, 25)
        self.assertEqual(row["status"], "OK")
        self.assertEqual(row["world_packets_lost"], 4)
        self.assertEqual(row["max_world_gap"], 2)
        self.assertEqual(row["telemetry_packets_lost"], 2)
        self.assertEqual(row["telemetry_loss_percent"], 50)

    def test_unconfirmed_reset_uses_first_counter_as_baseline(self):
        state = IntervalState(2, False, 0)
        state.receive(0.1, report(50, 200, 30, 10, 9))
        state.receive(0.2, report(52, 220, 32, 10, 10))
        row = state.row(0.3, 100)
        self.assertEqual(row["status"], "RESET_UNCONFIRMED")
        self.assertEqual(row["world_packets_lost"], 2)
        self.assertEqual(row["telemetry_packets_lost"], 1)
        self.assertEqual(row["max_world_gap"], "")

    def test_rebase_invalidates_max_gap(self):
        state = IntervalState(2, True, 0)
        state.receive(0.1, report(1, 10, 2, 2, 1))
        state.receive(0.2, report(0, 1, 0))
        state.receive(0.3, report(1, 10, 1, 1, 1))
        row = state.row(0.4, 30)
        self.assertEqual(row["status"], "COUNTER_REBASE")
        self.assertEqual(row["world_packets_lost"], 3)
        self.assertEqual(row["max_world_gap"], "")

    def test_missing_telemetry_has_no_loss_estimate(self):
        row = IntervalState(2, False, 0).row(60, 6000)
        self.assertEqual(row["status"], "NO_TELEMETRY")
        self.assertEqual(row["telemetry_received"], 0)
        self.assertNotIn("world_packets_lost", row)

    def test_duplicate_report_does_not_inflate_received_count(self):
        state = IntervalState(2, True, 0)
        state.receive(0.1, report(1, 10, 0))
        state.receive(0.2, report(1, 10, 0))
        self.assertEqual(state.row(0.3, 20)["telemetry_received"], 1)


if __name__ == "__main__":
    unittest.main()
