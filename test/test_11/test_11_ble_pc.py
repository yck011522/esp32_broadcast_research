"""Test 11: three simultaneous car BLE connections; 50 Hz writes and 20 Hz notifications per car.

python -m pip install bleak
python test/test_11/test_11_ble_pc.py --duration-seconds 120
python test/test_11/test_11_ble_pc.py --hours 2

Power off the ESP32 client so it does not compete for the car connection.
Flash each car with its matching ble_server_car_N profile. No COM port is needed on the PC.
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
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import struct
import time


SERVICE_UUIDS = {
    1: "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001",
    2: "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0002",
    3: "7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0003",
}
NOTIFY_UUID = "7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001"
WORLD_UUID = "7a1e0003-4b6d-4f7a-9c2e-6d3b1a5f0001"
WORLD_INTERVAL = .020
REPORT_INTERVAL = 5.0


class BacklogError(RuntimeError):
    """The server is echoing commands too old for this test to keep sending."""


class NotificationTimeout(RuntimeError):
    """No car telemetry arrived within the configured watchdog interval."""


FIELDS = ["timestamp_utc", "elapsed_s", "record_type", "connection", "window_s",
          "received", "seq_lost", "loss_percent", "rate_hz", "max_gap_s",
          "silence_s", "have_packet", "world_seq", "rtt_avg_ms", "rtt_max_ms",
          "rtt_samples", "echo_age_avg_ms", "echo_age_max_ms", "echo_age_samples",
          "world_attempted", "world_tx_rate_hz", "write_failures", "max_tx_gap_s",
          "max_write_ms", "malformed", "ignored", "partial_window", "message"]


@asynccontextmanager
async def throughput_connection(address, report=print):
    if os.name != "nt":
        raise RuntimeError("Throughput preference requires Windows 11")
    from winrt.windows.devices.bluetooth import (
        BluetoothLEDevice, BluetoothLEPreferredConnectionParameters,
        BluetoothLEPreferredConnectionParametersRequestStatus,
    )
    device = await BluetoothLEDevice.from_bluetooth_address_async(int(address.replace(":", ""), 16))
    if device is None:
        raise RuntimeError("Windows could not open the connected BLE device")
    request = None
    try:
        before = device.get_connection_parameters()
        report(f"Before request: interval={before.connection_interval * 1.25:g} ms "
               f"latency={before.connection_latency}")
        preferred = BluetoothLEPreferredConnectionParameters.throughput_optimized
        request = device.request_preferred_connection_parameters(preferred)
        if request.status != BluetoothLEPreferredConnectionParametersRequestStatus.SUCCESS:
            raise RuntimeError(f"Throughput request failed: {request.status.name}")
        deadline = time.perf_counter() + 5
        while True:
            actual = device.get_connection_parameters()
            if (preferred.min_connection_interval <= actual.connection_interval <= preferred.max_connection_interval
                    and actual.connection_latency == preferred.connection_latency):
                break
            if time.perf_counter() >= deadline:
                raise RuntimeError(f"Request accepted but negotiated interval is still "
                                   f"{actual.connection_interval * 1.25:g} ms, latency={actual.connection_latency}")
            await asyncio.sleep(.1)
        parameters = {"interval_ms": actual.connection_interval * 1.25,
                      "latency": actual.connection_latency,
                      "supervision_timeout_ms": actual.link_timeout * 10}
        report(f"Throughput request verified: {parameters}")
        yield parameters
    finally:
        # Keep the request alive during traffic; dispose it to release the preference.
        if request is not None:
            request.close()
        device.close()


class Window:
    """One BLE session. Callbacks and writes run on the same asyncio event loop."""
    def __init__(self, now):
        self.last_sequence = None
        self.last_receive = None
        self.latest_world = None
        self.last_attempt = None
        self.last_tx_time = None
        self.confirmed_echo = False
        self.sent_times = {}
        self.reset(now)

    def reset(self, now):
        self.start = now
        self.received = self.lost = self.attempted = self.failures = 0
        self.malformed = self.ignored = 0
        self.max_gap = self.max_tx_gap = self.max_write = 0.0
        self.rtts = []
        self.ages = []

    def attempt(self, sequence, now):
        if self.last_tx_time is not None:
            self.max_tx_gap = max(self.max_tx_gap, now - self.last_tx_time)
        self.last_tx_time = now
        self.last_attempt = sequence
        self.attempted += 1
        self.sent_times[sequence] = now
        # Keep at most ~40 seconds at target rate; prevents stale wrap matches.
        if len(self.sent_times) > 2048:
            del self.sent_times[next(iter(self.sent_times))]

    def notify(self, data, now):
        if len(data) != 4:
            self.malformed += 1
            return "malformed"
        sequence, world = struct.unpack("<HH", data)
        if self.last_sequence is not None:
            delta = (sequence - self.last_sequence) & 0xffff
            if delta == 0 or delta >= 0x8000:
                self.ignored += 1
                return "ignored"
            self.lost += delta - 1
        if self.last_receive is not None:
            self.max_gap = max(self.max_gap, now - self.last_receive)
        self.last_receive = now
        self.last_sequence = sequence
        self.latest_world = world
        self.received += 1
        if world != 0:
            self.confirmed_echo = True
        if self.last_attempt is not None and self.confirmed_echo:
            lag = (self.last_attempt - world) & 0xffff
            if lag < 0x8000:
                self.rtts.append(lag * WORLD_INTERVAL * 1000)
                sent = self.sent_times.get(world)
                if sent is not None and 0 <= now - sent <= 40:
                    self.ages.append((now - sent) * 1000)
        return "accepted"

    def snapshot(self, now, partial=False):
        elapsed = now - self.start
        total = self.received + self.lost
        row = {"record_type": "ble_stats", "window_s": round(elapsed, 6),
               "received": self.received, "seq_lost": self.lost,
               "loss_percent": 100 * self.lost / total if total else 0,
               "rate_hz": self.received / elapsed if elapsed else 0,
               "max_gap_s": self.max_gap,
               "silence_s": now - self.last_receive if self.last_receive is not None else "",
               "have_packet": int(self.last_receive is not None),
               "world_seq": self.latest_world if self.latest_world is not None else "",
               "rtt_avg_ms": sum(self.rtts) / len(self.rtts) if self.rtts else "",
               "rtt_max_ms": max(self.rtts) if self.rtts else "", "rtt_samples": len(self.rtts),
               "echo_age_avg_ms": sum(self.ages) / len(self.ages) if self.ages else "",
               "echo_age_max_ms": max(self.ages) if self.ages else "", "echo_age_samples": len(self.ages),
               "world_attempted": self.attempted,
               "world_tx_rate_hz": self.attempted / elapsed if elapsed else 0,
               "write_failures": self.failures, "max_tx_gap_s": self.max_tx_gap,
               "max_write_ms": self.max_write * 1000,
               "malformed": self.malformed, "ignored": self.ignored,
               "partial_window": int(partial)}
        self.reset(now)
        return row


class Log:
    def __init__(self, directory, duration, car_id):
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"ble_test_11_car_{car_id}_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.path = directory / (stem + ".csv")
        self.summary_path = directory / (stem + ".summary.json")
        self.csv_file = self.path.open("w", newline="", encoding="utf-8")
        self.raw = self.path.with_suffix(".jsonl").open("w", encoding="utf-8")
        self.writer = csv.DictWriter(self.csv_file, fieldnames=FIELDS)
        self.writer.writeheader()
        self.car_id = car_id
        self.start = time.perf_counter()
        self.summary = {"started_utc": datetime.now(timezone.utc).isoformat(),
                        "planned_duration_s": duration, "car_id": car_id, "service_uuid": SERVICE_UUIDS[car_id],
                        "status": "running", "connections": 0,
                        "ble_errors": 0, "ble_disconnects": 0, "overload_events": 0,
                        "notification_timeouts": 0, "connected_at_deadline": False,
                        "ble_stats_reports": 0, "received": 0,
                        "seq_lost": 0, "world_attempted": 0, "write_failures": 0}
        self.checkpoint()
        print(f"Car {self.car_id} CSV: {self.path}", flush=True)

    def raw_event(self, kind, **details):
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "elapsed_s": round(time.perf_counter() - self.start, 6),
               "connection": self.summary["connections"], "record_type": kind, **details}
        self.raw.write(json.dumps(row) + "\n")
        return row

    def event(self, kind, message):
        row = self.raw_event(kind, message=message)
        self.writer.writerow(row)
        self.flush()
        print(f"[car {self.car_id} {row['elapsed_s']:.1f}s] {kind}: {message}", flush=True)

    def report(self, state, partial=False):
        row = self.raw_event("ble_stats", **{k: v for k, v in state.snapshot(time.perf_counter(), partial).items()
                                             if k != "record_type"})
        self.writer.writerow(row)
        self.summary["ble_stats_reports"] += 1
        for key in ["received", "seq_lost", "world_attempted", "write_failures"]:
            self.summary[key] += row[key]
        self.flush()
        self.checkpoint()
        rtt = f"{row['rtt_avg_ms']:.1f}" if row["rtt_samples"] else "NA"
        print(f"[car {self.car_id} {row['elapsed_s']:.1f}s] RX={row['rate_hz']:.2f} Hz lost={row['seq_lost']} "
              f"TX={row['world_tx_rate_hz']:.2f} Hz RTT~{rtt} ms", flush=True)

    def flush(self):
        self.csv_file.flush()
        self.raw.flush()

    def checkpoint(self):
        self.summary["elapsed_s"] = time.perf_counter() - self.start
        temporary = self.summary_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.summary, indent=2), encoding="utf-8")
        temporary.replace(self.summary_path)

    def close(self):
        self.summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
        self.checkpoint()
        self.flush()
        self.csv_file.close()
        self.raw.close()


async def session(client, log, deadline, max_echo_age_ms=1000,
                  notification_timeout_s=2):
    notify = client.services.get_characteristic(NOTIFY_UUID)
    world = client.services.get_characteristic(WORLD_UUID)
    if notify is None or "notify" not in notify.properties:
        raise RuntimeError("Server notification characteristic missing")
    if world is None or "write-without-response" not in world.properties:
        raise RuntimeError("Server world-state write characteristic missing")
    if world.max_write_without_response_size < 20:
        raise RuntimeError("Adapter cannot write the required 20-byte world state")
    state = Window(time.perf_counter())
    active = True

    def notification(_, data):
        if not active:
            return
        now = time.perf_counter()
        prior_rtts = len(state.rtts)
        prior_ages = len(state.ages)
        result = state.notify(data, now)
        # Raw data allow individual gap/latency analysis later. Buffered until report.
        log.raw_event("notification", data_hex=bytes(data).hex(), accepted=result,
                      latest_attempted_world_seq=state.last_attempt,
                      rtt_ms=state.rtts[-1] if len(state.rtts) > prior_rtts else None,
                      echo_age_ms=state.ages[-1] if len(state.ages) > prior_ages else None)

    async def sender():
        sequence = 0
        next_send = time.perf_counter() + WORLD_INTERVAL
        while client.is_connected and time.perf_counter() < deadline:
            delay = max(0, min(next_send, deadline) - time.perf_counter())
            if os.name == "nt":
                # Python 3.11 sleep uses a Windows high-resolution waitable timer.
                # A worker keeps notification callbacks responsive during the wait.
                await asyncio.to_thread(time.sleep, delay)
            else:
                await asyncio.sleep(delay)
            now = time.perf_counter()
            if now >= deadline or not client.is_connected:
                return
            packet = struct.pack("<H", sequence) + os.urandom(18)
            if deadline - now < .01:
                return
            state.attempt(sequence, now)
            sequence = (sequence + 1) & 0xffff
            try:
                await asyncio.wait_for(client.write_gatt_char(world, packet, response=False),
                                       timeout=min(5, deadline - now))
            except asyncio.TimeoutError:
                if time.perf_counter() >= deadline:
                    return
                state.failures += 1
                raise
            except Exception:
                state.failures += 1
                raise
            finally:
                state.max_write = max(state.max_write, time.perf_counter() - now)
            next_send += WORLD_INTERVAL
            if next_send <= time.perf_counter():
                next_send = time.perf_counter() + WORLD_INTERVAL  # No catch-up bursts.

    async def reporter():
        while client.is_connected and time.perf_counter() < deadline:
            await asyncio.sleep(min(.1, max(0, deadline - time.perf_counter())))
            now = time.perf_counter()
            last_notification = state.last_receive if state.last_receive is not None else state.start
            if now - last_notification > notification_timeout_s:
                raise NotificationTimeout(f"Car {log.car_id} received no notification for "
                                          f"{now - last_notification:.1f} s")
            if state.ages and state.ages[-1] > max_echo_age_ms:
                raise BacklogError(f"Car {log.car_id} echo age {state.ages[-1]:.0f} ms "
                                   f"exceeds {max_echo_age_ms:g} ms")
            if time.perf_counter() - state.start >= REPORT_INTERVAL:
                log.report(state)

    tasks = []
    try:
        await asyncio.wait_for(client.start_notify(notify, notification),
                               timeout=max(.001, min(10, deadline - time.perf_counter())))
        state.start = time.perf_counter()
        tasks = [asyncio.create_task(sender()), asyncio.create_task(reporter())]
        await asyncio.gather(*tasks)
    finally:
        active = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if state.received or state.attempted or time.perf_counter() - state.start >= .1:
            log.report(state, partial=True)


def matching_devices(found, service_uuid):
    return [device for device, adv in found.values()
            if service_uuid in [uuid.lower() for uuid in adv.service_uuids]]


async def run_car(car_id, log, deadline, scan_lock, first_device=None,
                  max_echo_age_ms=1000, notification_timeout_s=2,
                  overload_cooldown_s=5, retry_delay_s=2, connection_mode="default"):
    from bleak import BleakClient, BleakScanner
    service_uuid = SERVICE_UUIDS[car_id]
    while time.perf_counter() < deadline:
        client = None
        retry_delay = retry_delay_s
        try:
            if first_device is not None:
                devices = [first_device]
                first_device = None
            else:
                log.event("scanning", service_uuid)
                # Serialize recovery scans on the shared Windows radio.
                async with scan_lock:
                    found = await BleakScanner.discover(
                        timeout=min(8, max(.01, deadline - time.perf_counter())), return_adv=True)
                devices = matching_devices(found, service_uuid)
            if len(devices) != 1:
                raise RuntimeError(f"Car {car_id}: found {len(devices)} matching servers")
            if time.perf_counter() >= deadline:
                break
            device = devices[0]
            client = BleakClient(device, timeout=min(15, deadline - time.perf_counter()))
            await asyncio.wait_for(client.connect(), timeout=max(.01, min(15, deadline-time.perf_counter())))
            log.summary["connections"] += 1
            log.event("connected", f"{device.name} {device.address}")
            async with AsyncExitStack() as cleanup:
                if connection_mode == "throughput":
                    parameters = await cleanup.enter_async_context(throughput_connection(
                        client.address, report=lambda message: log.event("connection_parameters", message)))
                    log.summary["connection_parameters"] = parameters
                else:
                    log.event("connection_parameters", "Using adapter defaults; throughput request and interval check disabled")
                await session(client, log, deadline, max_echo_age_ms,
                              notification_timeout_s)
            if time.perf_counter() >= deadline and client.is_connected:
                log.summary["connected_at_deadline"] = True
            if time.perf_counter() < deadline:
                log.summary["ble_disconnects"] += 1
                log.event("disconnected", "BLE connection ended; retrying")
        except asyncio.CancelledError:
            raise
        except BacklogError as exc:
            log.summary["overload_events"] += 1
            log.event("overloaded", str(exc))
            retry_delay = overload_cooldown_s
            log.event("reconnecting", f"cooling down for {retry_delay:g} s, then scanning again")
        except NotificationTimeout as exc:
            log.summary["notification_timeouts"] += 1
            log.event("notification_timeout", str(exc))
        except Exception as exc:
            log.summary["ble_errors"] += 1
            log.event("ble_error", f"{type(exc).__name__}: {exc}")
        finally:
            if client is not None:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=5)
                except Exception as exc:
                    log.event("cleanup_error", str(exc))
            log.checkpoint()
        if time.perf_counter() < deadline:
            await asyncio.sleep(min(retry_delay, deadline-time.perf_counter()))


async def run(logs, duration, max_echo_age_ms=1000,
              notification_timeout_s=2, overload_cooldown_s=5, connection_mode="default"):
    from bleak import BleakScanner
    deadline = min(log.start for log in logs.values()) + duration
    for log in logs.values():
        log.event("scanning", "all three car service UUIDs")
    found = await BleakScanner.discover(
        timeout=min(8, max(.01, deadline - time.perf_counter())), return_adv=True)
    first_devices = {}
    for car_id in logs:
        matches = matching_devices(found, SERVICE_UUIDS[car_id])
        if len(matches) == 1:
            first_devices[car_id] = matches[0]
        else:
            logs[car_id].event("discovery", f"found {len(matches)} matching servers; retrying")
    scan_lock = asyncio.Lock()
    await asyncio.gather(*(run_car(car_id, log, deadline, scan_lock, first_devices.get(car_id),
                                  max_echo_age_ms, notification_timeout_s,
                                  overload_cooldown_s, connection_mode=connection_mode)
                           for car_id, log in logs.items()))


def positive(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return result


def precise_loop():
    """Use a high-resolution clock instead of Windows' coarse monotonic ticks."""
    loop = asyncio.new_event_loop()
    loop.time = time.perf_counter
    # asyncio uses this tolerance to decide when a timer may fire early.
    loop._clock_resolution = time.get_clock_info("perf_counter").resolution
    return loop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=positive, default=2)
    parser.add_argument("--duration-seconds", type=positive)
    parser.add_argument("--connection-mode", choices=("default", "throughput"), default="default",
                        help="default leaves connection parameters alone; throughput requests and verifies the Windows preset")
    parser.add_argument("--max-echo-age-ms", type=positive, default=1000,
                        help="reconnect after a car's echoed command age exceeds this limit")
    parser.add_argument("--notification-timeout-seconds", type=positive, default=2,
                        help="reconnect if a connected car sends no notifications for this long")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "results/runs")
    args = parser.parse_args()
    if args.connection_mode == "throughput" and os.name != "nt":
        parser.error("--connection-mode throughput requires Windows 11")
    try:
        import bleak  # noqa: F401
    except ImportError:
        parser.error("Install dependency: python -m pip install bleak")
    duration = args.duration_seconds if args.duration_seconds is not None else args.hours * 3600
    logs = {car_id: Log(args.output_dir, duration, car_id) for car_id in SERVICE_UUIDS}
    for log in logs.values():
        log.summary["connection_mode"] = args.connection_mode
    old_sleep = None
    timer_changed = False
    try:
        if os.name == "nt":
            old_sleep = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            timer_changed = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        with asyncio.Runner(loop_factory=precise_loop) as runner:
            runner.run(run(logs, duration, args.max_echo_age_ms,
                           args.notification_timeout_seconds, connection_mode=args.connection_mode))
        for log in logs.values():
            log.summary["successful_reconnections"] = max(0, log.summary["connections"] - 1)
            if not log.summary["connections"]:
                log.summary["status"] = "no_connection"
            elif not log.summary["connected_at_deadline"]:
                log.summary["status"] = "ended_disconnected"
            elif log.summary["successful_reconnections"]:
                log.summary["status"] = "completed_with_reconnects"
            else:
                log.summary["status"] = "completed"
    except KeyboardInterrupt:
        for log in logs.values():
            log.summary["status"] = "interrupted"
    except Exception:
        for log in logs.values():
            log.summary["status"] = "failed"
        raise
    finally:
        if os.name == "nt" and old_sleep:
            ctypes.windll.kernel32.SetThreadExecutionState(old_sleep)
        if timer_changed:
            ctypes.windll.winmm.timeEndPeriod(1)
        for log in logs.values():
            log.close()
    for log in logs.values():
        print(f"Car {log.car_id} finished: {log.summary['status']}; {log.summary_path}", flush=True)


if __name__ == "__main__":
    main()
