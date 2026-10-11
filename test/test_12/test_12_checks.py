"""Protocol and failure checks for Test 12; no BLE hardware needed."""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test.test_12 import test_12_ble_pc as test12
def telemetry(car, seq, world):
    return test12.ROBOT_FORMAT.pack(car, seq, world, 7, b"ABCDEF", 456, 2, -150, 151, 12420)


class Test12Checks(unittest.IsolatedAsyncioTestCase):
    def test_payload_sizes_and_field_mapping(self):
        self.assertEqual(len(test12.world_payload(65535)), 20)
        self.assertEqual(struct.unpack_from("<H", test12.world_payload(65535))[0], 65535)
        self.assertEqual(test12.ROBOT_FORMAT.size, 21)
        state = test12.TelemetryWindow(2, 0)
        self.assertEqual(state.notify(telemetry(2, 3, 8), .1), "accepted")
        self.assertEqual(state.telemetry["follow_state"], 2)
        self.assertEqual(state.telemetry["left_mm_s"], -150)
        self.assertEqual(state.telemetry["right_mm_s"], 151)
        self.assertEqual(state.telemetry["battery_mv"], 12420)

    def test_notification_wrap_and_timestamp_echo(self):
        state = test12.TelemetryWindow(1, 0)
        state.attempt(65535, .01)
        state.notify(telemetry(1, 65535, 65535), .06)
        state.attempt(0, .07)
        state.notify(telemetry(1, 0, 0), .12)
        row = state.snapshot(.2)
        self.assertEqual(row["received"], 2)
        self.assertEqual(row["seq_lost"], 0)
        self.assertEqual(row["echo_age_samples"], 2)
        self.assertAlmostEqual(row["echo_age_avg_ms"], 50)
        self.assertEqual(row["rtt_avg_ms"], 0)

    def test_wrong_length_or_car_does_not_change_sequence(self):
        state = test12.TelemetryWindow(1, 0)
        self.assertEqual(state.notify(b"\0"*20, .1), "malformed")
        self.assertEqual(state.notify(telemetry(2, 100, 2), .2), "wrong_car")
        self.assertIsNone(state.last_sequence)
        self.assertEqual(state.malformed, 2)

    async def test_shared_payload_is_written_once_to_every_car(self):
        writers = {car: SimpleNamespace(write=AsyncMock()) for car in (1, 2, 3)}
        log = SimpleNamespace(event=lambda *a, **k: None)
        with patch.object(test12.os, "name", "nt"), patch.object(
                test12.asyncio, "to_thread", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await test12.broadcast(writers, log)
        calls = [link.write.call_args.args for link in writers.values()]
        self.assertTrue(all(call == calls[0] for call in calls))
        self.assertEqual(calls[0][1], 1)
        self.assertEqual(len(calls[0][0]), 20)

    async def test_write_failure_is_not_hidden_by_gather(self):
        links = {1: SimpleNamespace(write=AsyncMock(side_effect=RuntimeError("write failed"))),
                 2: SimpleNamespace(write=AsyncMock())}
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            await test12.broadcast(links, SimpleNamespace(event=lambda *a, **k: None))
        links[2].write.assert_awaited_once()

    def test_21_byte_telemetry_requires_sufficient_mtu(self):
        characteristic = SimpleNamespace(properties=["notify", "write-without-response"],
                                         max_write_without_response_size=20)
        client = SimpleNamespace(mtu_size=23, services=SimpleNamespace(
            get_characteristic=lambda _: characteristic))
        with self.assertRaisesRegex(RuntimeError, "too small"):
            test12.Link(client, 1, None)


if __name__ == "__main__":
    unittest.main()
