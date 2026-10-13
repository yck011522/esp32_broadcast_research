"""Protocol framing, conservative sequence interpretation and scenario checks."""
import unittest
import copy
from types import SimpleNamespace
from test.test_14.protocol import WORLD, ROBOT, Lines, decode_robot, sequence_delta, world_line, USB_IDENTITIES
from test.test_14.test_14_ble_modem import PeerWindow, scenario_plan, baseline_ready
from test.test_14.flash_test_14 import verify
from test.test_14.audit_test_14 import audit, reassess_stale_startup


class Test14Checks(unittest.TestCase):
    def test_world_header_is_usb_only_and_has_no_padding(self):
        line = world_line(65535)
        self.assertEqual(line, b"Wffff0000000000\n")
        payload = bytes.fromhex(line[1:-1].decode())
        self.assertEqual(len(payload), 7)
        self.assertEqual(WORLD.unpack(payload), (65535, 0, 0, 0, 0, 0))
        self.assertEqual(len(line), 16)

    def test_existing_robot_format_signed_speeds_and_id(self):
        data = ROBOT.pack(4, 65535, 0, 17, b"ABCDEF", 1, 2, -150, 150, 12420)
        self.assertEqual(len(data), 21)
        packet = decode_robot(data.hex().encode())
        self.assertEqual(packet["left_speed"], -150)
        self.assertEqual(packet["world_packets_lost_5s"], 17)
        self.assertEqual(packet["car"], 4)

    def test_fragmentation_oversized_line_recovery_and_crlf(self):
        decoder = Lines(maximum=42)
        packet = ROBOT.pack(1, 0, 0, 0, bytes(6), 0, 0, 0, 0, 0).hex().encode()
        data = b"x"*60+b"\n"+packet+b"\r\n"+packet+b"\n"
        rows = []
        for offset in range(0, len(data), 7):
            rows.extend(decoder.feed(data[offset:offset+7]))
        # A CRLF needs a spare byte in a strict maximum-42 test decoder.
        self.assertEqual(rows, [None, None, packet])
        self.assertEqual(Lines().feed(packet+b"\r\n"), [packet])

    def test_invalid_wire_shapes_and_unknown_id_are_rejected(self):
        for value in (b"x"*42, b"0"*40, b"0"*44, None):
            with self.assertRaises(ValueError):
                decode_robot(value)
        data = ROBOT.pack(5, 0, 0, 0, bytes(6), 0, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            decode_robot(data.hex().encode())

    def test_wrap_missing_duplicate_and_probable_reset(self):
        self.assertEqual(sequence_delta(65535, 0, .05), (0, False, False))
        self.assertEqual(sequence_delta(10, 13, .15), (2, False, False))
        self.assertEqual(sequence_delta(10, 10, .05), (0, False, True))
        self.assertEqual(sequence_delta(10, 0, .05), (0, True, False))
        self.assertEqual(sequence_delta(40000, 0, .05), (0, True, False))
        # No session marker can distinguish every plausible reset from a wrap.
        self.assertEqual(sequence_delta(65530, 0, .3), (5, False, False))

    def test_discontinuity_is_accepted_without_manufacturing_huge_loss(self):
        peer = PeerWindow(0)
        first = {"telemetry_seq": 40000, "world_seq": 100}
        peer.notify(first, .05, {100: .02})
        result = peer.notify({"telemetry_seq": 0, "world_seq": 101}, .1, {101: .08})
        self.assertTrue(result["sequence_discontinuity"])
        self.assertEqual(peer.total_lost, 0)
        self.assertEqual(peer.total_received, 2)
        row = peer.snapshot(5)
        self.assertEqual(row["sequence_discontinuities"], 1)
        self.assertAlmostEqual(row["echo_age_avg_ms"], 25)

    def test_no_match_zero_is_not_fabricated_as_rtt(self):
        peer = PeerWindow(0)
        peer.notify({"telemetry_seq": 0, "world_seq": 0}, .05, {})
        self.assertIsNone(peer.last_echo_age)
        self.assertEqual(peer.snapshot(5)["echo_samples"], 0)

    def test_zero_after_outage_does_not_match_old_zero_timestamp(self):
        peer = PeerWindow(0)
        peer.notify({"telemetry_seq": 1, "world_seq": 20}, .1, {20: .08, 0: 0})
        result = peer.notify({"telemetry_seq": 2, "world_seq": 0}, 10, {0: 0})
        self.assertTrue(result["ambiguous_zero_echo"])
        self.assertIsNone(peer.last_echo_age)
        peer.notify({"telemetry_seq": 3, "world_seq": 65535}, 10.05, {65535: 10.02})
        result = peer.notify({"telemetry_seq": 4, "world_seq": 0}, 10.1, {0: 10.08})
        self.assertFalse(result["ambiguous_zero_echo"])
        self.assertAlmostEqual(peer.last_echo_age, 20)

    def test_all_four_car_power_cycles_are_scheduled(self):
        actions, pauses, expected = scenario_plan("reconnect")
        self.assertEqual([cars for _, cars, _ in actions], [[1], [2], [3], [4]])
        self.assertEqual(len(expected), 4)
        self.assertEqual(pauses, [])

    def test_idle_and_expiry_preserve_the_transport(self):
        actions, pauses, expected = scenario_plan("idle")
        self.assertEqual(actions, [(15, [1, 2, 3, 4], "SILENCE 30")])
        self.assertEqual(pauses, [(15, 45)])
        self.assertEqual(set(expected), {1, 2, 3, 4})
        _, pauses, _ = scenario_plan("expiry")
        self.assertEqual(pauses, [(20, 40)])

    def test_upload_identity_verification_precedes_mutation(self):
        ports = {p: SimpleNamespace(vid=0x303a, pid=0x1001, serial_number=s) for p, s in USB_IDENTITIES.items()}
        boards = [("COM3", "ble14_modem"), ("COM7", "ble14_car_1")]
        verify(boards, ports)
        ports["COM7"].serial_number = "wrong"
        with self.assertRaisesRegex(RuntimeError, "no uploads"):
            verify(boards, ports)

    def test_mailbox_audit_rejects_fifo_delivery_of_every_burst_packet(self):
        summary = {"scenario": "mailbox", "status": "passed", "server_monitoring": False,
                   "statistics": [], "pc_submitted": 1200,
                   "car_baselines": {str(c): {"total_rx": 0} for c in range(1, 5)},
                   "car_finals": {str(c): {"total_rx": 1000} for c in range(1, 5)},
                   "events": [{"record_type": "mailbox_burst"}]}
        self.assertTrue(audit(summary)["passed"])
        summary["car_finals"]["4"]["total_rx"] = 1200
        self.assertFalse(audit(summary)["passed"])

    def test_expiry_audit_requires_positive_waiting_state(self):
        summary = {"scenario": "expiry", "status": "passed", "statistics": [{"elapsed_s": 10, "traffic_s": 5}],
                   "car_health_history": [{"car": 1, "elapsed_s": 35, "connected": 1, "rx_hz": 0, "last_world": 0}]}
        self.assertTrue(audit(summary)["passed"])
        summary["car_health_history"][0]["last_world"] = 999
        self.assertFalse(audit(summary)["passed"])

    def test_baseline_waits_for_connected_subscribed_serial_observation(self):
        required = {1, 2}
        health = {1: {"connected": 0, "subscribed": 0}, 2: {"connected": 1, "subscribed": 1}}
        self.assertFalse(baseline_ready(required, required, health, True))
        self.assertTrue(baseline_ready(required, required, health, False))
        health[1] = {"connected": 1, "subscribed": 1}
        self.assertTrue(baseline_ready(required, required, health, True))
        self.assertFalse(baseline_ready(required, {2}, health, True))

    def test_stale_snapshot_reassessment_cannot_waive_real_disconnect(self):
        before = {str(car): {"connected": 1, "subscribed": 1, "connects": 1, "disconnects": 0,
                            "total_rx": 0, "total_lost": 0, "notify_errors": 0} for car in range(1, 5)}
        after = copy.deepcopy(before)
        for value in after.values():
            value["total_rx"] = 250
        before["1"]["connected"] = before["1"]["subscribed"] = 0
        after["1"]["connects"] = 2
        summary = {"scenario": "bench", "server_monitoring": True, "status": "failed", "errors": [],
                   "active_s": 5, "pc_submitted": 250,
                   "statistics": [{"elapsed_s": 6, "traffic_s": 5}],
                   "car_baselines": before, "car_finals": after,
                   "car_health_history": [{"car": car, "elapsed_s": 5, "connects": after[str(car)]["connects"]} for car in range(1, 5)],
                   "measurements": {str(car): {"notifications": 100, "inferred_seq_lost": 0,
                       "sequence_discontinuities": 0, "duplicates": 0} for car in range(1, 5)},
                   "traffic_final_health": {str(car): {"silence_s": .02} for car in range(1, 5)},
                   "validation_failures": ["Car 1: unexpected reconnect"], "events": []}
        self.assertTrue(reassess_stale_startup(summary)["passed"])
        summary["car_finals"]["1"]["disconnects"] = 1
        self.assertFalse(reassess_stale_startup(summary)["passed"])
        summary["car_finals"]["1"]["disconnects"] = 0
        summary["validation_failures"].append("Car 2: telemetry continuity anomaly")
        self.assertFalse(reassess_stale_startup(summary)["passed"])


if __name__ == "__main__":
    unittest.main()
