"""Test 12: NimBLE-Arduino servers, shared 50 Hz commands, 20 Hz telemetry.

Flash ble_test12_car_1/2/3 first. No motor control is implemented.
python test/test_12/test_12_ble_pc.py --duration-seconds 120
python test/test_12/test_12_ble_pc.py --setup sequential --cars 3 2 1
python test/test_12/test_12_ble_pc.py --cars 1 --duration-seconds 40
"""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import asyncio
import csv
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import struct
import time

from test.test_11.test_11_ble_pc import Window, precise_loop
from test.test_11.test_11_monitor_servers import open_port

SERVICE_UUID = "12345678-1234-5678-1234-567812345678"
WORLD_UUID = "12345678-1234-5678-1234-567812345679"
ROBOT_UUID = "12345678-1234-5678-1234-56781234567a"
WORLD_FORMAT = struct.Struct("<HBBBBB13s")
ROBOT_FORMAT = struct.Struct("<BHHB6sHBhhH")
WORLD_INTERVAL = .020


def world_payload(sequence):
    # Neutral dummy movement bytes; Test 12 firmware never drives hardware.
    return WORLD_FORMAT.pack(sequence, 1, 0, 0, 0, 0, os.urandom(13))


class TelemetryWindow(Window):
    def __init__(self, car, now):
        super().__init__(now)
        self.car = car
        self.telemetry = None

    def notify(self, data, now):
        if len(data) != ROBOT_FORMAT.size:
            self.malformed += 1
            return "malformed"
        car, sequence, world, loss, nfc, age, follow, left, right, battery = ROBOT_FORMAT.unpack(data)
        if car != self.car:
            self.malformed += 1
            return "wrong_car"
        result = super().notify(struct.pack("<HH", sequence, world), now)
        if result == "accepted":
            self.telemetry = {"car": car, "telemetry_seq": sequence, "world_seq": world,
                              "server_lost_previous_5s": loss, "nfc": nfc.hex(),
                              "nfc_age_ms": age, "follow_state": follow,
                              "left_mm_s": left, "right_mm_s": right, "battery_mv": battery}
        return result


class Log:
    def __init__(self, args):
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.path = args.output_dir / f"ble_test_12_{args.setup}_{''.join(map(str, args.cars))}_{stamp}.jsonl"
        self.raw = self.path.open("w", encoding="utf-8")
        self.start = time.perf_counter()
        self.phase = "discovery"
        self.summary = {"started_utc": datetime.now(timezone.utc).isoformat(),
                        "setup": args.setup, "cars": args.cars, "duration_s": args.duration_seconds,
                        "world_bytes": WORLD_FORMAT.size, "telemetry_bytes": ROBOT_FORMAT.size,
                        "connections": {}, "errors": [], "statistics": [], "serial_logs": {},
                        "status": "running"}
        print(f"Test 12: {self.path}", flush=True)

    def event(self, kind, car=None, console=True, **details):
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "elapsed_s": time.perf_counter() - self.start, "phase": self.phase,
               "record_type": kind, "car": car, **details}
        self.raw.write(json.dumps(row) + "\n")
        self.raw.flush()
        if console:
            print(f"[{row['elapsed_s']:.1f}s] {kind} car={car}: {details}", flush=True)
        return row

    def report(self, links, partial=False):
        now = time.perf_counter()
        for car, link in links.items():
            row = self.event("ble_stats", car, console=False,
                             **{k: v for k, v in link.state.snapshot(now, partial).items()
                                if k != "record_type"})
            self.summary["statistics"].append(row)
            age = f"{row['echo_age_avg_ms']:.1f}" if row["echo_age_samples"] else "n/a"
            print(f"[{row['elapsed_s']:.1f}s] car={car} TX={row['world_tx_rate_hz']:.2f} Hz "
                  f"RX={row['rate_hz']:.2f} Hz lost={row['seq_lost']} echo_age={age} ms", flush=True)

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


class Link:
    def __init__(self, client, car, log):
        self.client, self.car, self.log = client, car, log
        self.state = TelemetryWindow(car, time.perf_counter())
        self.world = client.services.get_characteristic(WORLD_UUID)
        self.robot = client.services.get_characteristic(ROBOT_UUID)
        if self.world is None or "write-without-response" not in self.world.properties:
            raise RuntimeError("Test 12 world characteristic missing or wrong properties")
        if self.world.max_write_without_response_size < WORLD_FORMAT.size:
            raise RuntimeError("World characteristic cannot accept the 20-byte command")
        if self.robot is None or "notify" not in self.robot.properties:
            raise RuntimeError("Test 12 robot notification characteristic missing")
        if client.mtu_size < ROBOT_FORMAT.size + 3:
            raise RuntimeError(f"ATT MTU {client.mtu_size} is too small for 21-byte telemetry; need >=24")

    def notification(self, _, data):
        now = time.perf_counter()
        result = self.state.notify(data, now)
        self.log.event("telemetry", self.car, console=False, accepted=result,
                       data_hex=bytes(data).hex(), decoded=self.state.telemetry if result == "accepted" else None)

    async def write(self, payload, sequence):
        started = time.perf_counter()
        self.state.attempt(sequence, started)
        try:
            await asyncio.wait_for(self.client.write_gatt_char(self.world, payload, response=False), 2)
        except Exception as exc:
            self.state.failures += 1
            self.log.event("write_error", self.car, message=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self.state.max_write = max(self.state.max_write, time.perf_counter() - started)


async def broadcast(links, log):
    """One shared sequence; await one write per car, with no queued catch-up bursts."""
    sequence = 1
    deadline = time.perf_counter()
    while True:
        payload = world_payload(sequence)
        results = await asyncio.gather(*(link.write(payload, sequence) for link in links.values()),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        sequence = (sequence + 1) & 0xffff
        deadline += WORLD_INTERVAL
        now = time.perf_counter()
        if deadline <= now:
            log.event("tx_overrun", console=False, overdue_ms=(now-deadline)*1000)
            deadline = now + WORLD_INTERVAL
        delay = max(0, deadline-time.perf_counter())
        if os.name == "nt":
            await asyncio.to_thread(time.sleep, delay)
        else:
            await asyncio.sleep(delay)


async def capture_serial(log, ports, stop):
    handles = []
    try:
        for car, name in enumerate(ports, 1):
            port = open_port(name)
            path = log.path.with_name(log.path.stem + f"_car_{car}_{name}.log")
            output = path.open("w", encoding="utf-8")
            handles.append((car, port, output, bytearray()))
            log.summary["serial_logs"][str(car)] = str(path)
        while not stop.is_set():
            for car, port, output, buffer in handles:
                if port.in_waiting:
                    buffer.extend(port.read(min(port.in_waiting, 4096)))
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer[:] = rest
                    message = line.decode("utf-8", errors="replace").rstrip("\r")
                    output.write(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                                 f"[{time.perf_counter()-log.start:.3f}s] {message}\n")
                    output.flush()
                    if message.startswith("test12 event:"):
                        log.event("server_event", car, message=message)
            await asyncio.sleep(.02)
    finally:
        for _, port, output, _ in handles:
            port.close()
            output.close()


async def run(args, log):
    from bleak import BleakClient, BleakScanner
    stop = asyncio.Event()
    capture = asyncio.create_task(capture_serial(log, args.ports, stop)) if args.ports else None
    clients, links = {}, {}
    sender = None
    try:
        await asyncio.sleep(.1)
        if capture and capture.done():
            await capture
        devices = {}
        for attempt in range(3):
            log.event("scan", attempt=attempt+1)
            found = await BleakScanner.discover(timeout=8, return_adv=True)
            for car in args.cars:
                name = f"ESP32_Robot_{car}"
                matches = [device for device, adv in found.values()
                           if SERVICE_UUID in [uuid.lower() for uuid in adv.service_uuids]
                           and (adv.local_name == name or device.name == name)]
                if len(matches) > 1:
                    raise RuntimeError(f"Multiple devices advertise {name}")
                if matches:
                    devices[car] = matches[0]
            if len(devices) == len(args.cars):
                break
        log.event("discovered", addresses={str(car): dev.address for car, dev in devices.items()})
        if len(devices) != len(args.cars):
            log.summary["status"] = "discovery_incomplete"
            return
        log.phase = "setup_idle"

        async def connect(car):
            # BLEDevice avoids implicit rediscovery while other links are active.
            client = BleakClient(devices[car], timeout=15, winrt={"use_cached_services": False})
            clients[car] = client
            log.event("connect_attempt", car, address=client.address)
            try:
                await asyncio.wait_for(client.connect(), 15)
                links[car] = Link(client, car, log)
                log.summary["connections"][str(car)] = {"address": client.address, "mtu": client.mtu_size}
                log.event("connected", car, address=client.address, mtu=client.mtu_size)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                log.summary["errors"].append({"car": car, "phase": log.phase, "message": message})
                log.event("connect_failed", car, message=message)

        if args.setup == "concurrent":
            await asyncio.gather(*(connect(car) for car in args.cars))
        else:
            for car in args.cars:
                await connect(car)
        await asyncio.sleep(args.idle_seconds)
        if len(links) != len(args.cars) or any(not link.client.is_connected for link in links.values()):
            log.summary["status"] = "multiple_link_setup_failed"
            return
        log.phase = "traffic_50Hz_20Hz"
        # Fresh windows exclude scanning and idle setup from measured rates.
        for link in links.values():
            link.state = TelemetryWindow(link.car, time.perf_counter())
        sender = asyncio.create_task(broadcast(links, log))
        for link in links.values():
            await asyncio.wait_for(link.client.start_notify(link.robot, link.notification), 8)
        started = time.perf_counter()
        deadline = started + args.duration_seconds
        while time.perf_counter() < deadline:
            if sender.done():
                await sender
                raise RuntimeError("World sender stopped unexpectedly")
            if capture and capture.done():
                await capture
                raise RuntimeError("Serial capture stopped unexpectedly")
            for car, link in links.items():
                if not link.client.is_connected:
                    raise RuntimeError(f"Car {car} disconnected during traffic")
                last = link.state.last_receive or started
                if time.perf_counter() - last > 3:
                    raise RuntimeError(f"Car {car} telemetry silence exceeded three seconds")
                if link.state.ages and link.state.ages[-1] > 1000:
                    raise RuntimeError(f"Car {car} echoed-command age exceeded one second")
            if time.perf_counter() - next(iter(links.values())).state.start >= 5:
                log.report(links)
            await asyncio.sleep(.05)
        log.summary["status"] = "traffic_completed"
    finally:
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if links and log.phase == "traffic_50Hz_20Hz":
            log.report(links, partial=True)
        for car, client in clients.items():
            try:
                await asyncio.wait_for(client.disconnect(), 6)
            except Exception as exc:
                log.event("disconnect_error", car, message=str(exc))
                log.summary["errors"].append({"car": car, "phase": "cleanup", "message": str(exc)})
        log.phase = "post_disconnect"
        await asyncio.sleep(6)
        stop.set()
        if capture:
            await capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cars", nargs="+", type=int, choices=(1, 2, 3), default=[1, 2, 3])
    parser.add_argument("--setup", choices=("concurrent", "sequential"), default="concurrent")
    parser.add_argument("--duration-seconds", type=float, default=120)
    parser.add_argument("--idle-seconds", type=float, default=10)
    parser.add_argument("--ports", nargs=3, help="optional COM ports for Cars 1, 2, 3")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "results/runs")
    args = parser.parse_args()
    if len(args.cars) != len(set(args.cars)):
        parser.error("--cars must be distinct")
    if not math.isfinite(args.duration_seconds) or args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive and finite")
    if not math.isfinite(args.idle_seconds) or args.idle_seconds < 0:
        parser.error("--idle-seconds must be nonnegative and finite")
    if args.ports and len(set(name.upper() for name in args.ports)) != 3:
        parser.error("--ports must be distinct")
    log = Log(args)
    old_sleep, timer_changed = None, False
    try:
        if os.name == "nt":
            old_sleep = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            timer_changed = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        with asyncio.Runner(loop_factory=precise_loop) as runner:
            runner.run(run(args, log))
    except KeyboardInterrupt:
        log.summary["status"] = "interrupted"
    except Exception as exc:
        log.summary["status"] = "failed"
        log.summary["errors"].append({"message": f"{type(exc).__name__}: {exc}"})
        log.event("fatal", message=str(exc))
    finally:
        if old_sleep:
            ctypes.windll.kernel32.SetThreadExecutionState(old_sleep)
        if timer_changed:
            ctypes.windll.winmm.timeEndPeriod(1)
        log.close()
    print(f"Finished: {log.summary['status']}", flush=True)
    return 0 if log.summary["status"] == "traffic_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
