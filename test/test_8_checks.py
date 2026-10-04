"""Offline checks for Test 8: loss coverage, missing slaves, and reset handling."""
import argparse
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_8_long_run as test8


class FakeLink:
    def __init__(self, *args):
        self.disconnects = 0
        self.last_error = None
        self.records = []

    def connect(self, now):
        pass

    def read(self):
        records, self.records = self.records, []
        return records

    def send(self, sequence):
        # Only slave 1 responds; slave 2 is absent.
        self.records.append((test8.time.perf_counter(), (1, 0, sequence, 0, 0, 0)))
        return True

    def close(self):
        pass


class Test8Checks(unittest.TestCase):
    def test_reset_waits_for_both_and_tolerates_absent_slave(self):
        confirmed, attempts, _ = test8.reset_slaves(
            FakeLink(), [1, 2], 2, 0.001, test8.time.perf_counter() + 1)
        self.assertEqual(confirmed, {1})
        self.assertEqual(attempts, 2)

    def test_pending_reset_observation_does_not_count_as_measurement(self):
        state = test8.IntervalState(1, True, 0)
        state.receive(0.01, (1, 0, -1, 0, 0, 0))
        state.receive(0.1, (1, 2, 4, 2, 1, 2))
        state.receive(0.2, (1, 4, 9, 3, 2, 2))
        row = state.row(0.3, 10)
        self.assertEqual(row['world_packets_lost'], 3)
        self.assertEqual(row['telemetry_packets_lost'], 1)

    def test_partial_intervals_excluded_from_average(self):
        base = dict(status='OK', world_loss_complete=1, world_packets_written=100,
                    world_packets_lost=10, world_loss_percent=10, max_world_gap=2,
                    world_gap_estimate_ms=60, measurement_s=60, telemetry_received=10,
                    telemetry_packets_lost=1, max_telemetry_silence_s=0.3,
                    reset_confirmed=1, counter_rebases=0)
        partial = dict(base, status='RESET_UNCONFIRMED', world_loss_complete=0,
                       world_loss_percent=0, reset_confirmed=0)
        result = test8.slave_summary([base, partial])
        self.assertEqual(result['avg_world_loss_percent'], 10)
        self.assertEqual(result['weighted_world_loss_percent'], 10)
        self.assertEqual(result['partial_or_missing_intervals'], 1)
        self.assertEqual(result['telemetry_packets_lost'], 2)

    def test_long_run_writes_absent_rows_and_checkpoint(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(duration_seconds=0.45, hours=4, interval_seconds=0.08,
                                      hz=50, reset_spacing=0.001, reset_attempts=2,
                                      slaves=[1, 2], log_dir=directory, port='FAKE',
                                      baud=115200, mode='unicast')
            with patch.object(test8, 'SerialLink', FakeLink), \
                 patch.object(test8, 'WindowsSession', nullcontext), \
                 patch.object(test8, 'send_world_state', lambda link, seq: link.send(seq)):
                self.assertEqual(test8.run(args), 0)
            path = next(Path(directory).glob('*.csv'))
            with path.open(newline='') as handle:
                rows = list(csv.DictReader(handle))
            missing = [r for r in rows if r['car_id'] == '2']
            self.assertTrue(missing)
            self.assertTrue(all(r['status'] == 'NO_TELEMETRY' for r in missing))
            self.assertTrue(all(float(r['max_telemetry_silence_s']) > 0 for r in missing))
            summary = json.loads(path.with_suffix('.summary.json').read_text())
            from analyze_radio_test import read_run
            plotted_rows, _ = read_run(path)
            self.assertEqual(len(plotted_rows), len(rows))
            self.assertEqual(summary['status'], 'COMPLETE')
            self.assertIsNone(summary['slaves']['2']['avg_world_loss_percent'])
            # Shared PC transmissions must not be multiplied by the slave count.
            host_rows = [r for r in rows if r['car_id'] == '1']
            self.assertEqual(summary['world_packets_written'], sum(int(r['world_packets_written']) for r in host_rows))


if __name__ == '__main__':
    unittest.main()
