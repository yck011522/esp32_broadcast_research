"""Offline Test 10 checks; no Bluetooth connection required."""

import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_10_ble_pc import throughput_connection

from test_10_ble_pc import Window, session, NOTIFY_UUID, WORLD_UUID


class MeasurementChecks(unittest.TestCase):
    def test_wrap_loss_echo_and_window_baselines(self):
        state = Window(0)
        state.attempt(65535, .02)
        state.notify(struct.pack("<HH", 65535, 65535), .03)
        state.attempt(0, .04)
        state.attempt(1, .06)
        state.notify(struct.pack("<HH", 1, 65535), .07)
        self.assertEqual(state.lost, 1)  # Missing server sequence zero.
        self.assertEqual(state.rtts, [0, 40])
        self.assertAlmostEqual(state.ages[-1], 50)
        row = state.snapshot(.1)
        self.assertEqual(row["received"], 2)
        self.assertEqual(row["rtt_avg_ms"], 20)
        self.assertAlmostEqual(row["silence_s"], .03)
        self.assertEqual(state.notify(struct.pack("<HH", 1, 65535), .11), "ignored")
        state.notify(struct.pack("<HH", 2, 0), .12)
        self.assertEqual(state.lost, 0)
        self.assertEqual(state.rtts, [20])

    def test_startup_malformed_and_reconnect(self):
        state = Window(0)
        state.attempt(0, .02)
        state.notify(struct.pack("<HH", 0, 0), .03)
        self.assertEqual(state.rtts, [])  # Initial echo zero is ambiguous.
        self.assertEqual(state.notify(b"bad", .04), "malformed")
        row = state.snapshot(.1)
        self.assertEqual(row["rtt_avg_ms"], "")
        self.assertEqual(row["malformed"], 1)
        fresh = Window(.1)
        fresh.attempt(1, .12)
        fresh.notify(struct.pack("<HH", 2, 1), .14)
        self.assertEqual(fresh.lost, 0)
        self.assertAlmostEqual(fresh.ages[0], 20)


class ConnectionChecks(unittest.IsolatedAsyncioTestCase):
    async def test_preference_lifetime_and_cleanup(self):
        for fail_session in (False, True):
            request = SimpleNamespace(status=1, close=Mock())
            actual = SimpleNamespace(connection_interval=12, connection_latency=0, link_timeout=960)
            device = SimpleNamespace(get_connection_parameters=Mock(return_value=actual),
                                     request_preferred_connection_parameters=Mock(return_value=request), close=Mock())
            module = SimpleNamespace(
                BluetoothLEDevice=SimpleNamespace(from_bluetooth_address_async=AsyncMock(return_value=device)),
                BluetoothLEPreferredConnectionParameters=SimpleNamespace(throughput_optimized=SimpleNamespace(
                    min_connection_interval=12, max_connection_interval=12, connection_latency=0)),
                BluetoothLEPreferredConnectionParametersRequestStatus=SimpleNamespace(SUCCESS=1))
            with patch.dict("sys.modules", {"winrt.windows.devices.bluetooth": module}), patch("test_10_ble_pc.os.name", "nt"):
                try:
                    async with throughput_connection("68:EE:8F:4B:5B:4D", report=lambda _: None) as parameters:
                        self.assertEqual(parameters["interval_ms"], 15)
                        request.close.assert_not_called()
                        if fail_session:
                            raise ValueError("session failed")
                except ValueError:
                    if not fail_session:
                        raise
            request.close.assert_called_once()
            device.close.assert_called_once()


class SessionChecks(unittest.IsolatedAsyncioTestCase):
    async def test_writes_notifications_and_final_report(self):
        import time

        class Characteristic:
            max_write_without_response_size = 20
            def __init__(self, properties):
                self.properties = properties

        class Client:
            is_connected = True
            def __init__(self):
                self.services = self
                self.writes = []
                self.sequence = 0
            def get_characteristic(self, uuid):
                return Characteristic(["notify"] if uuid == NOTIFY_UUID else ["write-without-response"])
            async def start_notify(self, characteristic, callback):
                self.callback = callback
            async def write_gatt_char(self, characteristic, packet, response):
                self.writes.append((packet, response))
                self.callback(None, struct.pack("<HH", self.sequence, struct.unpack("<H", packet[:2])[0]))
                self.sequence += 1

        class Log:
            def __init__(self):
                self.rows = []
                self.events = []
            def raw_event(self, kind, **details):
                self.events.append((kind, details))
            def report(self, state, partial=False):
                self.rows.append(state.snapshot(time.perf_counter(), partial))

        client, log = Client(), Log()
        await session(client, log, time.perf_counter() + .11)
        self.assertGreaterEqual(len(client.writes), 2)
        self.assertTrue(all(len(packet) == 20 and response is False for packet, response in client.writes))
        self.assertEqual([struct.unpack("<H", packet[:2])[0] for packet, _ in client.writes],
                         list(range(len(client.writes))))
        self.assertEqual(log.rows[0]["received"], len(client.writes))
        self.assertEqual(log.rows[0]["partial_window"], 1)
        self.assertIsNone(log.events[0][1]["rtt_ms"])
        self.assertEqual(log.events[1][1]["rtt_ms"], 0)


if __name__ == "__main__":
    unittest.main()
