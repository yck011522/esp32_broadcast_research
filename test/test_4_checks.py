"""Offline regression checks; no serial hardware is opened."""
import csv
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch, MagicMock

from test_4_long_run import State, Reporter, FIELDS, next_deadline, receive_loop, send_world, run


class LongRunChecks(unittest.TestCase):
    def ready(self):
        state = State()
        state.receive((1, 0, -1, 0, 0, 0), 0)
        state.collect = True
        return state

    def test_sequence_gaps_duplicates_and_reset(self):
        state = self.ready()
        state.receive((1, 2, 0, 0, 0, 0), 1)
        state.receive((1, 5, 15, 2, 1, 2), 2)
        state.receive((1, 5, 15, 2, 1, 2), 3)
        car = state.snapshot()[0][1]
        self.assertEqual((car['telemetry_received'], car['telemetry_lost'],
                          car['telemetry_duplicates']), (2, 2, 1))
        self.assertEqual(car['world_packets_lost'], 2)
        state.receive((1, 0, -1, 0, 0, 0), 4)
        self.assertFalse(state.stop.is_set())
        self.assertIsNone(state.error)
        self.assertEqual(state.stats[1]['status'], 'COUNTER_RESET')
        self.assertEqual(state.stats[1]['world_packets_lost'], 2)

    def test_partial_lines_survive_timeouts(self):
        state = self.ready()

        class FakeSerial:
            chunks = iter([b'T,1,2,', b'', b'10,0,0,0\nT,1,3,11,0,0,0\n'])
            in_waiting = 1

            def read(self, size):
                try:
                    return next(self.chunks)
                except StopIteration:
                    state.stop.set()
                    return b''

        receive_loop(FakeSerial(), state)
        self.assertIsNone(state.error)
        self.assertEqual(state.stats[1]['telemetry_received'], 2)

    def test_late_sender_does_not_replay_backlog(self):
        target, rebased = next_deadline(10, 11.2, .01)
        self.assertTrue(rebased)
        self.assertAlmostEqual(target, 11.21)
        target, rebased = next_deadline(10, 10.001, .01)
        self.assertFalse(rebased)
        self.assertAlmostEqual(target, 10.01)

    def test_missing_and_stale_slaves_are_explicit(self):
        state = self.ready()
        state.start = time.perf_counter() - 10
        state.sent = 1000
        state.receive((1, 2, 990, 10, 5, 2), time.perf_counter() - 3)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        with patch('builtins.print'):
            Reporter(state, writer, output).write()
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(rows[0]['status'], 'STALE')
        self.assertEqual(rows[0]['world_loss_percent'], '1.0')
        self.assertEqual(len(rows), 1)

    def test_write_drains_and_rejects_short_write(self):
        class FakeSerial:
            def write(self, message):
                self.message = message
                return len(message)

            def flush(self):
                self.drained = True

        ser = FakeSerial()
        send_world(ser, 42)
        self.assertEqual(ser.message, b'W,42\n')
        self.assertTrue(ser.drained)
        ser.write = lambda message: 0
        with self.assertRaises(RuntimeError):
            send_world(ser, 43)

    def test_unconfirmed_slave_is_not_silently_counted(self):
        state = self.ready()
        state.receive((2, 1000, 20000, 500, 50, 121), 1)
        self.assertFalse(state.stop.is_set())
        self.assertIsNone(state.error)
        self.assertEqual(state.stats[2]['status'], 'FAIL_TO_RESET')
        state.receive((1, 2, 5, 0, 0, 0), time.perf_counter())
        rows = self.rows(state)
        self.assertEqual(rows[0]['status'], 'OK')
        self.assertEqual(rows[1]['status'], 'FAIL_TO_RESET')
        for field in ('world_packets_lost', 'world_loss_percent', 'max_world_gap',
                      'telemetry_received', 'telemetry_lost', 'last_world_seq'):
            self.assertEqual(rows[1][field], '')

    def rows(self, state):
        state.start = time.perf_counter() - 10
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        with patch('builtins.print'):
            Reporter(state, writer, output).write()
        return list(csv.DictReader(io.StringIO(output.getvalue())))

    def test_arbitrary_ids_and_absent_next_interval(self):
        state = State()
        for car_id in (3, 17, 42, 255):
            state.receive((car_id, 0, -1, 0, 0, 0), 0)
        state.collect = True
        for car_id in (3, 17, 42):
            state.receive((car_id, 2, 50, 1, 1, 1), time.perf_counter())
        self.assertEqual([int(row['car_id']) for row in self.rows(state)], [3, 17, 42])
        state.prepare_interval()
        state.collect = True
        self.assertEqual(self.rows(state), [])

    def test_failed_slave_can_recover_next_interval(self):
        state = self.ready()
        state.receive((7, 50, 100, 3, 2, 2), 1)
        state.prepare_interval()
        state.receive((7, 0, -1, 0, 0, 0), 2)
        state.collect = True
        state.receive((7, 2, 10, 1, 1, 1), time.perf_counter())
        self.assertEqual(self.rows(state)[0]['status'], 'OK')

    def test_disappeared_slave_retains_data_and_reappearance_counts_gaps(self):
        state = self.ready()
        now = time.perf_counter()
        state.receive((1, 2, 50, 1, 1, 1), now - 3)
        row = self.rows(state)[0]
        self.assertEqual(row['status'], 'STALE')
        self.assertEqual(row['world_packets_lost'], '1')
        state.receive((1, 5, 100, 4, 3, 2), now)
        row = self.rows(state)[0]
        self.assertEqual(row['status'], 'OK')
        self.assertEqual(row['telemetry_lost'], '2')

    def test_run_continues_when_no_reset_is_confirmed(self):
        for observed in (False, True):
            with self.subTest(observed=observed), tempfile.TemporaryDirectory() as folder:
                args = SimpleNamespace(duration_seconds=1, hours=4, frequency=100,
                                       log_interval=60, reset_interval=60, log_dir=folder, port='FAKE')

                def transmit(ser, state, duration, frequency, tail):
                    state.sent = 10
                    if observed:
                        state.receive((99, 999, 999, 50, 10, 10), time.perf_counter())
                    state.run_start -= 2  # Finish the simulated wall-clock budget.

                with patch('test_4_long_run.serial.Serial', return_value=MagicMock()), \
                     patch('test_4_long_run.WindowsSession', return_value=nullcontext()), \
                     patch('test_4_long_run.time.sleep'), \
                     patch('test_4_long_run.receive_loop'), \
                     patch('test_4_long_run.send_world'), \
                     patch('test_4_long_run.transmit_loop', side_effect=transmit), \
                     patch('builtins.print'):
                    self.assertEqual(run(args), 0)
                summary = json.loads(next(Path(folder).glob('*.summary.json')).read_text())
                self.assertEqual(summary['status'], 'COMPLETE')
                self.assertEqual(summary['total_world_packets_sent'], 10)
                with next(Path(folder).glob('*.csv')).open(newline='') as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), int(observed))
                if observed:
                    self.assertEqual(rows[0]['status'], 'FAIL_TO_RESET')

    def test_interval_reset_preserves_run_total_but_clears_maximum(self):
        state = self.ready()
        state.receive((1, 2, 100, 12, 2, 10), 1)
        state.sent = 101
        state.completed_packets = 101
        state.prepare_interval()
        self.assertEqual(state.completed_packets, 101)
        self.assertEqual(state.stats, {})
        self.assertFalse(state.collect)
        self.assertEqual(state.reset_seen, set())
        state.receive((1, 0, -1, 0, 0, 0), 2)
        state.collect = True
        state.receive((1, 2, 10, 1, 1, 1), 3)
        self.assertEqual(state.stats[1]['max_world_gap'], 1)
        self.assertFalse(state.stop.is_set())


if __name__ == '__main__':
    unittest.main()
