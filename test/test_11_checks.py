"""Offline checks for three-car discovery and concurrent sessions."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import sys
import struct
import time
import unittest
from unittest.mock import patch

import test_11_ble_pc as test11


class ThreeCarChecks(unittest.IsolatedAsyncioTestCase):
    async def test_overload_and_timeout_both_reconnect(self):
        for fault in (test11.BacklogError, test11.NotificationTimeout):
            device = SimpleNamespace(name="ble_car_1", address="68:EE:8F:4B:5B:4D")
            class Scanner:
                @staticmethod
                async def discover(**_):
                    return {device.address: (device, SimpleNamespace(
                        service_uuids=[test11.SERVICE_UUIDS[1]]))}

            class Client:
                def __init__(self, discovered, **_):
                    self.address = discovered.address
                    self.is_connected = False

                async def connect(self):
                    self.is_connected = True

                async def disconnect(self):
                    self.is_connected = False

            @asynccontextmanager
            async def preference(address, report):
                yield {"interval_ms": 15}

            attempts = 0
            async def session(client, log, deadline, max_echo_age_ms,
                              notification_timeout_s):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise fault("simulated interruption")
                await asyncio.sleep(max(0, deadline - time.perf_counter()))

            class Log:
                car_id = 1
                summary = {"connections": 0, "ble_errors": 0, "ble_disconnects": 0,
                           "overload_events": 0, "notification_timeouts": 0}
                def __init__(self):
                    self.summary = dict(self.summary)
                    self.events = []
                def event(self, kind, message):
                    self.events.append(kind)
                def checkpoint(self):
                    pass

            log = Log()
            with patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=Client, BleakScanner=Scanner)}), \
                    patch.object(test11, "session", session), patch.object(test11, "throughput_connection", preference):
                await test11.run_car(1, log, time.perf_counter() + .12, asyncio.Lock(),
                                     first_device=device, overload_cooldown_s=.01,
                                     retry_delay_s=.01)
            self.assertEqual(attempts, 2)
            self.assertEqual(log.summary["connections"], 2)
            self.assertIn("scanning", log.events)
            self.assertEqual(log.summary["overload_events"], int(fault is test11.BacklogError))
            self.assertEqual(log.summary["notification_timeouts"], int(fault is test11.NotificationTimeout))

    async def test_stale_echo_stops_sender(self):
        class Client:
            is_connected = True
            def __init__(self):
                self.services = self
                self.writes = 0

            def get_characteristic(self, uuid):
                properties = ["notify"] if uuid == test11.NOTIFY_UUID else ["write-without-response"]
                return SimpleNamespace(properties=properties, max_write_without_response_size=20)

            async def start_notify(self, characteristic, callback):
                self.callback = callback

            async def write_gatt_char(self, characteristic, packet, response):
                self.writes += 1
                if self.writes >= 3:
                    self.callback(None, struct.pack("<HH", self.writes, 1))

        class Log:
            car_id = 1
            def raw_event(self, *_args, **_kwargs):
                pass
            def report(self, state, partial=False):
                state.snapshot(time.perf_counter(), partial)

        client = Client()
        with self.assertRaises(test11.BacklogError):
            await test11.session(client, Log(), time.perf_counter() + .5, max_echo_age_ms=1)
        self.assertLess(client.writes, 20)

    async def test_notification_watchdog_detects_unplugged_car(self):
        class Client:
            is_connected = True
            def __init__(self):
                self.services = self

            def get_characteristic(self, uuid):
                properties = ["notify"] if uuid == test11.NOTIFY_UUID else ["write-without-response"]
                return SimpleNamespace(properties=properties, max_write_without_response_size=20)

            async def start_notify(self, characteristic, callback):
                pass

            async def write_gatt_char(self, characteristic, packet, response):
                pass

        class Log:
            car_id = 2
            def report(self, state, partial=False):
                state.snapshot(time.perf_counter(), partial)

        with self.assertRaises(test11.NotificationTimeout):
            await test11.session(Client(), Log(), time.perf_counter() + .5,
                                 notification_timeout_s=.05)

    async def test_distinct_services_run_concurrently_with_separate_logs(self):
        addresses = {car_id: f"68:EE:8F:4B:5B:{car_id:02X}" for car_id in (1, 2, 3)}
        devices = {car_id: SimpleNamespace(name=f"ble_car_{car_id}", address=addresses[car_id])
                   for car_id in addresses}
        advertisements = {address: (devices[car_id], SimpleNamespace(
            service_uuids=[test11.SERVICE_UUIDS[car_id]]))
                          for car_id, address in addresses.items()}
        seen = set()
        active = set()
        peak = 0

        class Scanner:
            @staticmethod
            async def discover(**_):
                return advertisements

        class Client:
            def __init__(self, device, **_):
                self.address = device.address
                self.car_id = next(car_id for car_id, address in addresses.items() if address == self.address)
                self.is_connected = False

            async def connect(self):
                self.is_connected = True

            async def disconnect(self):
                self.is_connected = False

        @asynccontextmanager
        async def preference(address, report):
            yield {"interval_ms": 15}

        async def session(client, log, deadline, max_echo_age_ms, notification_timeout_s):
            nonlocal peak
            self.assertEqual(client.car_id, log.car_id)
            seen.add(log.car_id)
            active.add(log.car_id)
            peak = max(peak, len(active))
            await asyncio.sleep(max(0, deadline - time.perf_counter()))
            active.remove(log.car_id)

        class Log:
            def __init__(self, car_id, start):
                self.car_id = car_id
                self.start = start
                self.summary = {"connections": 0, "ble_errors": 0, "ble_disconnects": 0}
                self.events = []

            def event(self, kind, message):
                self.events.append((kind, message))

            def checkpoint(self):
                pass

        start = time.perf_counter()
        logs = {car_id: Log(car_id, start) for car_id in addresses}
        with patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=Client, BleakScanner=Scanner)}), \
                patch.object(test11, "session", session), patch.object(test11, "throughput_connection", preference):
            await test11.run(logs, .12)
        self.assertEqual(seen, {1, 2, 3})
        self.assertEqual(peak, 3)
        self.assertTrue(all(log.summary["connections"] == 1 and log.summary["ble_errors"] == 0
                            for log in logs.values()))


if __name__ == "__main__":
    unittest.main()
