"""Recovery behavior without hardware: outages, reboot sequences and peer isolation."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test.test_12.test_12_ble_pc import ROBOT_FORMAT, TelemetryWindow
from test.test_13.test_13_ble_modem import Bench, HexLines, Recovery, verify_modem_stopped


def packet(car, sequence, world):
    return ROBOT_FORMAT.pack(car, sequence, world, 0, b"ABCDEF", 1, 2, -4, 5, 12000)


class MemoryLog:
    start = 0
    def __init__(self):
        self.events, self.rows, self.summary = [], [], {}

    def event(self, kind, console=False, **details):
        row = {"elapsed_s": self.now, "record_type": kind, **details}
        self.events.append(row)
        return row

    def report(self, states, partial=False, recovery=None, now=None):
        for car, state in states.items():
            self.rows.append({"car": car, **recovery.columns(car), **state.snapshot(now, partial)})


class Port:
    def __init__(self, data):
        self.data = data
    @property
    def in_waiting(self):
        return len(self.data)
    def read(self, size):
        result, self.data = self.data[:size], self.data[size:]
        return result


class RecoveryChecks(unittest.TestCase):
    def setUp(self):
        self.bench = Bench.__new__(Bench)
        self.bench.args = SimpleNamespace(cars=[1, 2], max_echo_age_ms=1000, telemetry_timeout_s=3)
        self.bench.log = MemoryLog()
        self.bench.log.now = 0
        self.bench.states = {car: TelemetryWindow(car, 0) for car in (1, 2)}
        self.bench.recovery = Recovery(self.bench, 0)
        self.recovery = self.bench.recovery
        self.bench.measuring = True
        self.bench.decoder = HexLines()
        self.bench.server_handles = []
        self.bench.links = {}
        self.bench.framing_errors = self.bench.unexpected_cars = 0

    def time(self, now):
        self.bench.log.now = now

    def test_reboot_baseline_resets_only_affected_car_and_preserves_counts(self):
        old = self.bench.states[1]
        healthy = self.bench.states[2]
        for state in self.bench.states.values():
            state.attempt(100, .02)
        self.assertEqual(old.notify(packet(1, 30000, 100), .05), "accepted")
        healthy.notify(packet(2, 10000, 100), .05)
        self.time(1)
        self.recovery.link(1, False, 1, 520)
        old.attempt(101, 1.02)
        self.time(5)
        self.recovery.link(1, True, 5)
        fresh = self.bench.states[1]
        self.assertIsNot(fresh, old)
        self.assertIs(self.bench.states[2], healthy)
        self.assertEqual(self.bench.log.rows[0]["world_attempted"], 2)
        self.assertEqual(self.bench.log.rows[0]["window_s"], 5)
        self.assertEqual(fresh.notify(packet(1, 0, 0), 5.01), "accepted")
        fresh.attempt(102, 5.02)
        self.assertEqual(fresh.notify(packet(1, 1, 102), 5.05), "accepted")
        self.time(5.05)
        self.recovery.packet(1, 5.05)
        self.recovery.watch(5.05)
        self.recovery.finish(5.1)
        result = self.bench.log.summary["recovery"]["cars"]["1"]
        self.assertEqual(result["reconnections"], 1)
        self.assertEqual(result["recovered_outages"], 1)
        self.assertAlmostEqual(result["outages"][0]["outage_s"], 4.05)
        self.assertEqual(fresh.lost, 0)
        self.assertEqual(fresh.ignored, 0)

    def test_ready_is_not_recovery_until_a_new_command_echo_arrives(self):
        state = self.bench.states[1]
        state.attempt(90, .1)
        state.notify(packet(1, 100, 90), .2)
        self.time(1)
        self.recovery.outage(1, 1, "telemetry_silence")
        self.recovery.packet(1, 1.1)
        self.assertIsNotNone(self.recovery.peers[1]["outage"])
        state.attempt(91, 1.2)
        state.notify(packet(1, 101, 91), 1.3)
        self.time(1.3)
        self.recovery.packet(1, 1.3)
        self.assertIsNone(self.recovery.peers[1]["outage"])

    def test_duplicate_link_events_do_not_reset_session_twice(self):
        self.time(1)
        self.recovery.link(1, False, 1, 520)
        self.recovery.link(1, False, 1.1)
        self.time(2)
        self.recovery.link(1, True, 2)
        fresh = self.bench.states[1]
        self.recovery.link(1, True, 2.1)
        self.assertIs(self.bench.states[1], fresh)
        self.assertEqual(self.recovery.peers[1]["session"], 2)
        self.assertEqual(len(self.bench.log.rows), 1)

    def test_command_outage_duration_is_not_a_telemetry_gap(self):
        state = self.bench.states[1]
        state.attempt(50, .1)
        state.notify(packet(1, 1, 50), .15)
        self.recovery.packet(1, .15)
        self.time(.16)
        self.recovery.outage(1, .16, "stale_command_echo")
        for sequence in range(2, 11):
            now = .1+sequence*.05
            state.notify(packet(1, sequence, 50), now)
            self.recovery.packet(1, now)
        state.attempt(51, .62)
        state.notify(packet(1, 11, 51), .65)
        self.time(.65)
        self.recovery.packet(1, .65)
        episode = self.recovery.peers[1]["episodes"][0]
        self.assertAlmostEqual(episode["max_telemetry_gap_s"], .05)
        self.assertAlmostEqual(episode["outage_s"], .49)

    def test_warning_survives_window_rollover_and_clears_on_fresh_echo(self):
        state = self.bench.states[1]
        state.attempt(50, .1)
        state.notify(packet(1, 1, 50), .15)
        self.time(2)
        self.recovery.watch(2)
        state.snapshot(2)
        self.recovery.watch(2.1)
        warnings = [e for e in self.bench.log.events if e.get("event") == "warning"
                    and e.get("car") == 1]
        self.assertEqual(len(warnings), 1)
        state.attempt(51, 2.2)
        state.notify(packet(1, 2, 51), 2.25)
        self.time(2.25)
        self.recovery.packet(1, 2.25)
        self.recovery.watch(2.25)
        self.assertEqual(self.recovery.peers[1]["warnings"], set())

    def test_unrecovered_outage_remains_visible_in_summary(self):
        self.time(1)
        self.recovery.link(1, False, 1, 520)
        self.recovery.watch(10)
        self.recovery.finish(10)
        result = self.bench.log.summary["recovery"]
        self.assertFalse(result["all_outages_recovered"])
        self.assertFalse(result["all_healthy_at_end"])
        self.assertEqual(result["cars"]["1"]["outages"][0]["outage_s"], 9)

    def test_timestamp_eviction_does_not_clear_stale_echo_warning(self):
        state = self.bench.states[1]
        state.attempt(50, .1)
        state.notify(packet(1, 1, 50), .15)
        self.time(2)
        self.recovery.watch(2)
        state.sent_times.clear()
        self.time(50)
        self.recovery.watch(50)
        self.assertIn("stale_command_echo", self.recovery.peers[1]["warnings"])
        self.assertFalse(any(e.get("event") == "warning_cleared" and e.get("car") == 1
                             for e in self.bench.log.events))

    def test_cleanup_requires_fresh_stop_acknowledgement(self):
        verify_modem_stopped({"enabled": 0, "links": 0}, 11, 10)
        for health, observed in (({"enabled": 0, "links": 0}, 9),
                                 ({"enabled": 1, "links": 3}, 11)):
            with self.assertRaisesRegex(RuntimeError, "current state is unconfirmed"):
                verify_modem_stopped(health, observed, 10)

    def test_pump_keeps_processing_other_car_after_disconnect(self):
        healthy = self.bench.states[2]
        healthy.attempt(100, .1)
        self.bench.modem = Port(b"#EVENT car=1 event=disconnected reason=520\n"
                               +packet(2, 1, 100).hex().encode()+b"\n")
        self.time(.2)
        with patch("test.test_13.test_13_ble_modem.time.perf_counter", return_value=.2):
            self.bench.pump()
        self.assertEqual(healthy.received, 1)
        self.assertFalse(self.recovery.peers[1]["ready"])
        self.assertTrue(self.bench.measuring)

    def test_strict_mode_still_aborts_disconnect(self):
        self.bench.recovery = None
        self.bench.modem = Port(b"#EVENT car=1 event=disconnected reason=520\n")
        with self.assertRaisesRegex(RuntimeError, "disconnected during traffic"):
            self.bench.pump()


if __name__ == "__main__":
    unittest.main()
