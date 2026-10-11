"""Diagnose multiple BLE links: one scan, sequential setup, then staged traffic.

python test/test_11/test_11_connection_probe.py --order 1 2 3
python test/test_11/test_11_connection_probe.py --order 3 2 1
No firmware changes. 2 Hz dummy writes keep the server command watchdog alive.
"""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import csv
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import struct
import time

from test.test_11.test_11_ble_pc import SERVICE_UUIDS, WORLD_UUID, NOTIFY_UUID, Window, precise_loop
from test.test_11.test_11_monitor_servers import open_port


class ProbeWindow(Window):
    def notify(self, data, now):
        result = super().notify(data, now)
        # Rates change between phases: sequence-based 50 Hz RTT is inapplicable.
        self.rtts.clear()
        return result


class Recorder:
    def __init__(self, directory, order, mode):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.path = directory / f"ble_connection_probe_{''.join(map(str, order))}_{mode}_{stamp}.jsonl"
        self.raw = self.path.open("w", encoding="utf-8")
        self.start = time.perf_counter()
        self.phase = "discovery"
        self.summary = {"started_utc": datetime.now(timezone.utc).isoformat(), "order": order,
                        "connection_mode": mode, "keepalive_hz": 2, "connections": {},
                        "errors": [], "statistics": [], "status": "running", "serial_logs": {}}
        self.summary["server_watchdogs"] = {}
        print(f"Probe: {self.path}", flush=True)

    def event(self, kind, car=None, console=True, **details):
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "elapsed_s": time.perf_counter() - self.start, "phase": self.phase,
               "record_type": kind, "car": car, **details}
        self.raw.write(json.dumps(row) + "\n")
        self.raw.flush()
        if console:
            print(f"[{row['elapsed_s']:.1f}s] {self.phase} car={car} {kind}: {details}", flush=True)
        return row

    def report(self, car, link, partial=False):
        row = self.event("ble_stats", car, console=False,
                         **{k: v for k, v in link.state.snapshot(time.perf_counter(), partial).items()
                            if k != "record_type"}, target_tx_hz=link.rate)
        self.summary["statistics"].append(row)
        age = f"{row['echo_age_avg_ms']:.1f}" if row["echo_age_samples"] else "n/a"
        print(f"[{row['elapsed_s']:.1f}s] {self.phase} car={car} "
              f"TX={row['world_tx_rate_hz']:.2f} RX={row['rate_hz']:.2f} "
              f"lost={row['seq_lost']} echo_age={age} ms", flush=True)

    def close(self):
        self.summary.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                            elapsed_s=time.perf_counter() - self.start)
        self.path.with_suffix(".summary.json").write_text(json.dumps(self.summary, indent=2), encoding="utf-8")
        rows = self.summary["statistics"]
        if rows:
            with self.path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        self.raw.close()

    def change_phase(self, name, links):
        for car, link in links.items():
            self.report(car, link, partial=True)
        self.phase = name


@asynccontextmanager
async def power_preference(client, record, car):
    from winrt.windows.devices.bluetooth import (
        BluetoothLEDevice, BluetoothLEPreferredConnectionParameters,
        BluetoothLEPreferredConnectionParametersRequestStatus,
    )
    device = await BluetoothLEDevice.from_bluetooth_address_async(int(client.address.replace(":", ""), 16))
    if device is None:
        raise RuntimeError("Windows could not open BLE device for power preference")
    request = None
    try:
        request = device.request_preferred_connection_parameters(
            BluetoothLEPreferredConnectionParameters.power_optimized)
        if request.status != BluetoothLEPreferredConnectionParametersRequestStatus.SUCCESS:
            raise RuntimeError(f"Power preference failed: {request.status.name}")
        record.event("power_preference_accepted", car)
        yield
    finally:
        if request is not None:
            request.close()
        device.close()


class Link:
    def __init__(self, client, car, record):
        self.client, self.car, self.record = client, car, record
        self.state = ProbeWindow(time.perf_counter())
        self.rate = 2.0
        self.sequence = 0
        self.notifying = False
        self.notify_started = None
        self.world = client.services.get_characteristic(WORLD_UUID)
        self.notify_char = client.services.get_characteristic(NOTIFY_UUID)
        if self.world is None or "write-without-response" not in self.world.properties:
            raise RuntimeError("Missing world characteristic")
        if self.world.max_write_without_response_size < 20:
            raise RuntimeError("World characteristic cannot accept 20 bytes")
        if self.notify_char is None or "notify" not in self.notify_char.properties:
            raise RuntimeError("Missing notification characteristic")

    def notification(self, _, payload):
        now = time.perf_counter()
        previous = len(self.state.ages)
        result = self.state.notify(payload, now)
        self.record.event("notification", self.car, console=False,
                          data_hex=bytes(payload).hex(), accepted=result,
                          echo_age_ms=self.state.ages[-1] if len(self.state.ages) > previous else None)

    async def sender(self):
        next_send = time.perf_counter()
        while self.client.is_connected:
            delay = max(0, next_send - time.perf_counter())
            if os.name == "nt":
                await asyncio.to_thread(time.sleep, delay)
            else:
                await asyncio.sleep(delay)
            if not self.client.is_connected:
                break
            now = time.perf_counter()
            packet = struct.pack("<H", self.sequence) + os.urandom(18)
            self.state.attempt(self.sequence, now)
            self.sequence = (self.sequence + 1) & 0xffff
            try:
                await asyncio.wait_for(self.client.write_gatt_char(self.world, packet, response=False), 2)
            except Exception:
                self.state.failures += 1
                raise
            finally:
                self.state.max_write = max(self.state.max_write, time.perf_counter() - now)
            next_send += 1 / self.rate
            if next_send <= time.perf_counter():
                next_send = time.perf_counter() + 1 / self.rate


async def serial_capture(record, names, stop):
    ports = []
    try:
        for car, name in enumerate(names, 1):
            port = open_port(name)
            path = record.path.with_name(record.path.stem + f"_car_{car}_{name}.log")
            output = path.open("w", encoding="utf-8")
            ports.append((port, output, bytearray()))
            record.summary["serial_logs"][str(car)] = str(path)
            record.event("serial_opened", car, port=name)
        while not stop.is_set():
            for car, (port, output, buffer) in enumerate(ports, 1):
                if port.in_waiting:
                    buffer.extend(port.read(min(port.in_waiting, 4096)))
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer[:] = rest
                    message = line.decode("utf-8", errors="replace").rstrip("\r")
                    output.write(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                                 f"[{time.perf_counter()-record.start:.3f}s] {message}\n")
                    output.flush()
                    watchdog = re.search(r"command_watchdog=(\d+)", message)
                    if watchdog:
                        record.summary["server_watchdogs"][str(car)] = int(watchdog[1])
                    if "ble event:" in message or "ble recovery:" in message:
                        record.event("server_event", car, message=message)
            await asyncio.sleep(.02)
    finally:
        for port, output, _ in ports:
            port.close()
            output.close()


async def run(args, record):
    from bleak import BleakClient, BleakScanner
    stop = asyncio.Event()
    capture = asyncio.create_task(serial_capture(record, args.ports, stop))
    clients, links, senders = {}, {}, {}
    record.summary["keepalive_hz"] = args.keepalive_hz
    try:
        await asyncio.sleep(.1)
        if capture.done():
            await capture
        devices = {}
        # Preflight repeats are allowed only while no links are active.
        for attempt in range(3):
            record.event("scan", attempt=attempt + 1)
            found = await BleakScanner.discover(timeout=8, return_adv=True)
            for car in args.order:
                matches = [device for device, adv in found.values()
                           if SERVICE_UUIDS[car] in [s.lower() for s in adv.service_uuids]]
                if len(matches) == 1:
                    devices[car] = matches[0]
            if all(car in devices for car in args.order):
                break
        record.event("discovered", devices={str(c): d.address for c, d in devices.items()})
        if len(devices) != len(args.order):
            record.summary["status"] = "discovery_incomplete"
            return
        if args.keepalive_hz == 0 and any(record.summary["server_watchdogs"].get(str(car)) != 0
                                        for car in args.order):
            raise RuntimeError("Pure-idle setup requires serial confirmation of command_watchdog=0 on every car")
        record.phase = f"sequential_setup_{args.keepalive_hz:g}Hz_no_notifications"
        async with AsyncExitStack() as preferences:
            for car in args.order:
                client = BleakClient(devices[car], timeout=12)
                clients[car] = client
                record.event("connect_attempt", car, address=client.address)
                try:
                    await asyncio.wait_for(client.connect(), 12)
                    link = Link(client, car, record)
                    link.rate = args.keepalive_hz
                    links[car] = link
                    if link.rate > 0:
                        senders[car] = asyncio.create_task(link.sender())
                    record.summary["connections"][str(car)] = client.address
                    record.event("connected", car, address=client.address)
                    if args.connection_mode == "power":
                        await preferences.enter_async_context(power_preference(client, record, car))
                    # Give the first link a measured, low-load baseline.
                    await observe(links, senders, record, 5)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    record.summary["errors"].append({"car": car, "phase": record.phase, "message": message})
                    record.event("connect_failed", car, message=message)
                    if car not in links:
                        await asyncio.wait_for(client.disconnect(), 5)
            await observe(links, senders, record, 10)
            if len(links) != len(args.order) or any(not l.client.is_connected for l in links.values()):
                record.summary["status"] = "multiple_link_setup_failed"
                return
            record.change_phase("notifications_20Hz_writes_2Hz", links)
            for car, link in links.items():
                link.rate = 2
                if car not in senders:
                    senders[car] = asyncio.create_task(link.sender())
                await asyncio.wait_for(link.client.start_notify(link.notify_char, link.notification), 8)
                link.notifying = True
                link.notify_started = time.perf_counter()
            await observe(links, senders, record, 15)
            for rate in (5, 20, 50):
                record.change_phase(f"notifications_20Hz_writes_{rate}Hz", links)
                for link in links.values():
                    link.rate = rate
                await observe(links, senders, record, 40 if rate == 50 else 20)
            record.summary["status"] = "all_stages_passed"
    finally:
        for task in senders.values():
            task.cancel()
        await asyncio.gather(*senders.values(), return_exceptions=True)
        for car, client in clients.items():
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except Exception as exc:
                record.event("disconnect_error", car, message=str(exc))
        record.phase = "post_disconnect"
        await asyncio.sleep(6)
        stop.set()
        await capture


async def observe(links, senders, record, seconds):
    deadline = time.perf_counter() + seconds
    for car, link in links.items():
        record.report(car, link, partial=True)
    while time.perf_counter() < deadline:
        for car, task in senders.items():
            if task.done():
                await task
                raise RuntimeError(f"Car {car} disconnected during observation")
        for car, link in links.items():
            if not link.client.is_connected:
                raise RuntimeError(f"Car {car} disconnected during observation")
            if link.notifying:
                last = link.state.last_receive if link.state.last_receive is not None else link.notify_started
                if time.perf_counter() - last > 3:
                    raise RuntimeError(f"Car {car} notifications stopped")
                if link.state.ages and link.state.ages[-1] > 1000:
                    raise RuntimeError(f"Car {car} echoed command age exceeded one second")
            if time.perf_counter() - link.state.start >= 5:
                record.report(car, link)
        await asyncio.sleep(min(.05, max(0, deadline - time.perf_counter())))
    for car, link in links.items():
        record.report(car, link, partial=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order", nargs="+", type=int, choices=(1, 2, 3), default=[1, 2, 3])
    parser.add_argument("--connection-mode", choices=("default", "power"), default="default")
    parser.add_argument("--keepalive-hz", type=float, default=2,
                        help="setup write rate; zero requires command-watchdog-disabled diagnostic firmware")
    parser.add_argument("--ports", nargs=3, default=["COM3", "COM4", "COM5"])
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).parent / "results/runs/connection_probes")
    args = parser.parse_args()
    if len(args.order) != len(set(args.order)):
        parser.error("each car must appear only once in --order")
    if not math.isfinite(args.keepalive_hz) or args.keepalive_hz < 0:
        parser.error("--keepalive-hz must be finite and nonnegative")
    if args.connection_mode == "power" and os.name != "nt":
        parser.error("power preference requires Windows 11")
    record = Recorder(args.output_dir, args.order, args.connection_mode)
    old_sleep = None
    timer_changed = False
    try:
        if os.name == "nt":
            old_sleep = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            timer_changed = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        with asyncio.Runner(loop_factory=precise_loop) as runner:
            runner.run(run(args, record))
    except KeyboardInterrupt:
        record.summary["status"] = "interrupted"
    except Exception as exc:
        record.summary["status"] = "failed"
        record.summary["errors"].append({"message": f"{type(exc).__name__}: {exc}"})
        record.event("fatal", message=str(exc))
    finally:
        if old_sleep:
            ctypes.windll.kernel32.SetThreadExecutionState(old_sleep)
        if timer_changed:
            ctypes.windll.winmm.timeEndPeriod(1)
        record.close()
    print(f"Finished: {record.summary['status']}", flush=True)


if __name__ == "__main__":
    main()
