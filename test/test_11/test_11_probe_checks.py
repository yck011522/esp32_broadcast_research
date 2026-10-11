"""Offline checks for the staged connection diagnostic."""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test.test_11 import test_11_connection_probe as probe
class ProbeChecks(unittest.IsolatedAsyncioTestCase):
    def test_variable_rate_echo_uses_timestamp_not_50Hz_sequence(self):
        state = probe.ProbeWindow(0)
        state.attempt(1, .5)
        state.attempt(2, 1)
        state.notify(struct.pack("<HH", 1, 1), 1.1)
        row = state.snapshot(1.2)
        self.assertEqual(row["rtt_samples"], 0)
        self.assertAlmostEqual(row["echo_age_avg_ms"], 600)

    async def test_discovery_precedes_connections_and_notifications_wait_for_all(self):
        trace = []
        connected = set()
        original_sleep = asyncio.sleep
        devices = {c: SimpleNamespace(address=f"00:00:00:00:00:{c:02X}") for c in (1, 2, 3)}
        async def discover(**_):
            self.assertFalse(connected)
            trace.append("scan")
            return {d.address: (d, SimpleNamespace(service_uuids=[probe.SERVICE_UUIDS[c]]))
                    for c, d in devices.items()}

        class Client:
            def __init__(self, device, **_):
                self.car = next(c for c, d in devices.items() if d is device)
                self.address = device.address
                self.is_connected = False
                self.services = self
            async def connect(self):
                trace.append(self.car)
                self.is_connected = True
                connected.add(self.car)
            async def disconnect(self):
                self.is_connected = False
                connected.discard(self.car)
            def get_characteristic(self, uuid):
                return SimpleNamespace(properties=["notify"] if uuid == probe.NOTIFY_UUID else
                                       ["write-without-response"], max_write_without_response_size=20)
            async def start_notify(self, *_):
                self_test.assertEqual(connected, {1, 2, 3})
                trace.append(f"notify{self.car}")

        self_test = self
        phases = []
        async def observe(links, senders, record, seconds):
            phases.append((record.phase, {c: l.rate for c, l in links.items()}))
            await original_sleep(0)
        async def capture(record, names, stop):
            await stop.wait()
        async def sender(_):
            await asyncio.Event().wait()
        async def fast_sleep(_):
            await original_sleep(0)
        with tempfile.TemporaryDirectory() as directory:
            record = probe.Recorder(Path(directory), [3, 2, 1], "default")
            args = SimpleNamespace(order=[3, 2, 1], ports=["COM3", "COM4", "COM5"], connection_mode="default",
                                   keepalive_hz=2)
            with patch.dict("sys.modules", {"bleak": SimpleNamespace(
                    BleakClient=Client, BleakScanner=SimpleNamespace(discover=discover))}), \
                    patch.object(probe, "serial_capture", capture), patch.object(probe, "observe", observe), \
                    patch.object(probe.Link, "sender", sender), patch.object(probe.asyncio, "sleep", fast_sleep):
                await probe.run(args, record)
            record.close()
        self.assertEqual(trace[:4], ["scan", 3, 2, 1])
        self.assertEqual(set(trace[4:]), {"notify1", "notify2", "notify3"})
        self.assertEqual(phases[-1][1], {3: 50, 2: 50, 1: 50})
        self.assertEqual(record.summary["status"], "all_stages_passed")
        self.assertFalse(connected)

    async def test_no_notifications_after_subscription_is_failure(self):
        link = SimpleNamespace(client=SimpleNamespace(is_connected=True),
                               notifying=True, notify_started=probe.time.perf_counter() - 4,
                               state=SimpleNamespace(last_receive=None, ages=[]))
        record = SimpleNamespace(report=lambda *_args, **_kwargs: None)
        with self.assertRaisesRegex(RuntimeError, "notifications stopped"):
            await probe.observe({1: link}, {}, record, .01)

    async def test_idle_mode_refuses_unverified_server_watchdog(self):
        device = SimpleNamespace(address="00:00:00:00:00:01")
        scanner = SimpleNamespace(discover=AsyncMock(return_value={
            device.address: (device, SimpleNamespace(service_uuids=[probe.SERVICE_UUIDS[1]]))}))
        client_factory = AsyncMock()
        original_sleep = asyncio.sleep
        async def fast_sleep(_):
            await original_sleep(0)
        async def capture(record, names, stop):
            await stop.wait()
        with tempfile.TemporaryDirectory() as directory:
            record = probe.Recorder(Path(directory), [1], "default")
            args = SimpleNamespace(order=[1], ports=["COM3", "COM4", "COM5"],
                                   connection_mode="default", keepalive_hz=0)
            with patch.dict("sys.modules", {"bleak": SimpleNamespace(
                    BleakClient=client_factory, BleakScanner=scanner)}), \
                    patch.object(probe, "serial_capture", capture), patch.object(probe.asyncio, "sleep", fast_sleep):
                with self.assertRaisesRegex(RuntimeError, "command_watchdog=0"):
                    await probe.run(args, record)
            record.close()
        client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
