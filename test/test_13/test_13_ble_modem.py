"""Test 13: COM3 ESP32 BLE modem -> two cars, 50 Hz world / 20 Hz telemetry.

python test/test_13/test_13_ble_modem.py --duration-seconds 120 --server-ports COM4 COM5
python test/test_13/test_13_ble_modem.py --hours 2 --server-ports COM4 COM5
Flash esp_esp_ble_modem and esp_esp_ble_car_2/3 first. No PC Bluetooth is used.
"""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import copy
import csv
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time

from test.test_12.test_12_ble_pc import ROBOT_FORMAT, TelemetryWindow, world_payload
from test.test_11.test_11_monitor_servers import open_port
from test.common.provenance import capture_test13


class HexLines:
    """USB reads are chunks, not packets; recover oversized lines at newline."""
    def __init__(self, maximum=512):
        self.maximum = maximum
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data):
        result = []
        for byte in data:
            if byte == 10:
                if self.discarding:
                    result.append(("invalid", "oversized_line"))
                else:
                    line = bytes(self.buffer).removesuffix(b"\r")
                    if line.startswith(b"#"):
                        result.append(("diagnostic", line.decode("utf-8", errors="replace")))
                    elif line:
                        if re.fullmatch(rb"[0-9a-fA-F]{42}", line):
                            result.append(("telemetry", bytes.fromhex(line.decode("ascii"))))
                        else:
                            result.append(("invalid", line.hex()))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                if len(self.buffer) < self.maximum:
                    self.buffer.append(byte)
                else:
                    self.buffer.clear()
                    self.discarding = True
        return result


def fields(line):
    result = {}
    for key, value in re.findall(r"(\w+)=([^\s]+)", line):
        try:
            result[key] = float(value) if "." in value and ":" not in value else int(value)
        except ValueError:
            result[key] = value
    return result


class Log:
    def __init__(self, args):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        directory = args.output_dir or Path(__file__).parent / "results/runs" / stamp
        directory.mkdir(parents=True, exist_ok=True)
        capture_test13(directory, {"port": args.port, "cars": args.cars,
                                  "server_ports": args.server_ports,
                                  "duration_seconds": args.duration_seconds,
                                  "start_sequence": getattr(args, "start_sequence", None)})
        self.path = directory / f"ble_test_13_{stamp}.jsonl"
        self.raw = self.path.open("w", encoding="utf-8")
        self.start = time.perf_counter()
        self.phase = "setup"
        self.summary = {"started_utc": datetime.now(timezone.utc).isoformat(), "port": args.port,
                        "cars": args.cars, "planned_duration_s": args.duration_seconds,
                        "status": "running", "errors": [], "statistics": [], "serial_logs": {}}
        print(f"Test 13: {self.path}", flush=True)

    def event(self, kind, console=False, **details):
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "elapsed_s": time.perf_counter()-self.start, "phase": self.phase,
               "record_type": kind, **details}
        self.raw.write(json.dumps(row) + "\n")
        if console:
            print(f"[{row['elapsed_s']:.1f}s] {kind}: {details}", flush=True)
        return row

    def report(self, states, partial=False):
        now = time.perf_counter()
        for car, state in states.items():
            row = self.event("ble_stats", car=car,
                             **{k: v for k, v in state.snapshot(now, partial).items() if k != "record_type"})
            self.summary["statistics"].append(row)
            age = f"{row['echo_age_avg_ms']:.1f}" if row["echo_age_samples"] else "n/a"
            print(f"[{row['elapsed_s']:.1f}s] car={car} TX={row['world_tx_rate_hz']:.2f} Hz "
                  f"RX={row['rate_hz']:.2f} Hz lost={row['seq_lost']} echo_age={age} ms", flush=True)
        self.raw.flush()

    def close(self):
        self.summary.update(finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_s=time.perf_counter()-self.start)
        self.path.with_suffix(".summary.json").write_text(json.dumps(self.summary, indent=2), encoding="utf-8")
        rows = self.summary["statistics"]
        if rows:
            with self.path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        self.raw.close()


class Bench:
    def __init__(self, args, log):
        self.args, self.log = args, log
        self.modem = open_port(args.port)
        self.modem.write_timeout = .5
        self.modem.reset_input_buffer()
        self.decoder = HexLines()
        self.links, self.health, self.io = {}, {}, {}
        self.server_world, self.server_health = {}, {}
        self.server_handles = []
        self.states = {}
        self.framing_errors = self.unexpected_cars = 0
        self.commands_sent = 0
        self.last_request = 0
        self.measuring = False
        try:
            for car, name in zip(args.cars, args.server_ports or []):
                port = open_port(name)
                path = log.path.with_name(log.path.stem + f"_car_{car}_{name}.log")
                output = path.open("w", encoding="utf-8")
                self.server_handles.append((car, port, output, bytearray()))
                log.summary["serial_logs"][str(car)] = str(path)
        except BaseException:
            self.close()
            raise

    def control(self, text):
        payload = (text + "\n").encode("ascii")
        if self.modem.write(payload) != len(payload):
            raise RuntimeError("Partial modem control write")
        self.log.event("control", command=text)

    def pump(self):
        if self.modem.in_waiting:
            chunk = self.modem.read(min(self.modem.in_waiting, 8192))
            for kind, data in self.decoder.feed(chunk):
                if kind == "diagnostic":
                    values = fields(data)
                    self.log.event("modem_diagnostic", message=data)
                    if data.startswith("#MODEM "):
                        self.health = values
                    elif data.startswith("#IO "):
                        self.io = values
                    elif data.startswith("#LINK "):
                        self.links[values["car"]] = values
                    elif data.startswith("#EVENT "):
                        self.log.event("modem_event", console=True, **values)
                        if values.get("event") == "disconnected" and self.measuring:
                            raise RuntimeError(f"Car {values.get('car')} disconnected during traffic")
                elif kind == "telemetry":
                    car = data[0]
                    if car not in self.args.cars:
                        self.unexpected_cars += 1
                    state = self.states.get(car)
                    now = time.perf_counter()
                    accepted = state.notify(data, now) if state and self.measuring else "outside_measurement"
                    self.log.event("telemetry", car=car, data_hex=data.hex(), accepted=accepted,
                                   decoded=state.telemetry if state and accepted == "accepted" else None)
                else:
                    self.framing_errors += 1
                    self.log.event("framing_error", data=data)
        for car, port, output, buffer in self.server_handles:
            if port.in_waiting:
                buffer.extend(port.read(min(port.in_waiting, 8192)))
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer[:] = rest
                message = line.decode("utf-8", errors="replace").rstrip("\r")
                output.write(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                             f"[{time.perf_counter()-self.log.start:.3f}s] {message}\n")
                output.flush()
                if message.startswith("car world:"):
                    self.server_world[car] = fields(message)
                elif message.startswith("car health:"):
                    self.server_health[car] = fields(message)
                elif message.startswith("car event:"):
                    self.log.event("car_event", car=car, message=message)

    def wait(self, seconds, request_status=False):
        deadline = time.perf_counter()+seconds
        while time.perf_counter() < deadline:
            self.pump()
            if request_status and time.perf_counter()-self.last_request >= 1:
                self.control("#STATUS")
                self.last_request = time.perf_counter()
            time.sleep(.001)

    def prepare(self):
        self.control("#STOP")
        deadline = time.perf_counter()+12
        while not (self.health.get("enabled") == 0 and self.health.get("links") == 0):
            if time.perf_counter() >= deadline:
                raise RuntimeError("Modem did not acknowledge stopped/disconnected state")
            self.wait(.1, request_status=True)
        self.control("#START")
        deadline = time.perf_counter()+45
        while True:
            self.wait(.1, request_status=True)
            ready = (self.health.get("ready") == len(self.args.cars)
                     and self.health.get("targets") == len(self.args.cars)
                     and all(self.links.get(car, {}).get("ready") == 1 for car in self.args.cars))
            serial_ready = (not self.args.server_ports or all(
                car in self.server_world and self.server_health.get(car, {}).get("connected") == 1
                for car in self.args.cars))
            if ready and serial_ready and self.io:
                break
            if time.perf_counter() >= deadline:
                raise RuntimeError("Timed out establishing every requested modem/car connection")
        if self.health.get("host_limit", 0) < 4:
            raise RuntimeError("Modem host is not configured for four links")
        self.log.summary["modem_baseline"] = {"health": copy.deepcopy(self.health),
            "io": copy.deepcopy(self.io), "links": copy.deepcopy(self.links)}
        self.log.summary["server_baselines"] = copy.deepcopy(self.server_world)
        self.log.summary["server_health_baselines"] = copy.deepcopy(self.server_health)
        self.log.summary["framing_baseline"] = self.framing_errors
        self.log.summary["unexpected_car_baseline"] = self.unexpected_cars
        self.log.event("all_cars_ready", console=True, cars=self.args.cars)

    def traffic(self):
        self.log.phase = "traffic"
        start = time.perf_counter()
        self.states = {car: TelemetryWindow(car, start) for car in self.args.cars}
        self.measuring = True
        sequence = self.args.start_sequence
        next_send = start
        deadline = start+self.args.duration_seconds
        try:
            while time.perf_counter() < deadline:
                self.pump()
                now = time.perf_counter()
                if now >= next_send:
                    payload = world_payload(sequence)
                    for state in self.states.values():
                        state.attempt(sequence, now)
                    line = payload.hex().encode("ascii") + b"\n"
                    written = self.modem.write(line)
                    ended = time.perf_counter()
                    for state in self.states.values():
                        state.max_write = max(state.max_write, ended-now)
                    if written != len(line):
                        raise RuntimeError("Partial serial world write")
                    self.commands_sent += 1
                    self.log.event("world_sent", sequence=sequence, data_hex=payload.hex())
                    sequence = (sequence+1) & 0xffff
                    next_send += .020
                    if next_send <= ended:
                        self.log.event("tx_overrun", overdue_ms=(ended-next_send)*1000)
                        next_send = ended+.020
                for car, state in self.states.items():
                    if now-(state.last_receive if state.last_receive is not None else start) > 3:
                        raise RuntimeError(f"Car {car} telemetry silence exceeded three seconds")
                    if now-start > 3 and not state.confirmed_echo:
                        raise RuntimeError(f"Car {car} never echoed a command")
                    if state.ages and state.ages[-1] > 1000:
                        raise RuntimeError(f"Car {car} echoed-command age exceeded one second")
                if now-next(iter(self.states.values())).start >= 5:
                    self.log.report(self.states)
                time.sleep(max(0, min(.001, next_send-time.perf_counter())))
        finally:
            self.log.report(self.states, partial=True)
            self.measuring = False
            self.log.summary["commands_sent"] = self.commands_sent
            self.log.summary["active_s"] = time.perf_counter()-start

    def shutdown(self):
        self.log.phase = "drain"
        self.wait(.25) # Let the final command reach each server before stopping.
        self.control("#STOP")
        self.log.phase = "post_stop"
        self.wait(6, request_status=True)
        self.log.summary["modem_final"] = {"health": copy.deepcopy(self.health),
            "io": copy.deepcopy(self.io), "links": copy.deepcopy(self.links)}
        self.log.summary["server_finals"] = copy.deepcopy(self.server_world)
        self.log.summary["server_final_health"] = copy.deepcopy(self.server_health)
        self.log.summary["framing_errors"] = self.framing_errors-self.log.summary.get("framing_baseline", 0)
        self.log.summary["unexpected_cars"] = self.unexpected_cars-self.log.summary.get("unexpected_car_baseline", 0)

    def close(self):
        for _, port, output, _ in self.server_handles:
            port.close()
            output.close()
        self.modem.close()


def assess(summary, server_required):
    failures, measurements = [], {}
    baseline, final = summary.get("modem_baseline", {}), summary.get("modem_final", {})
    writes = summary.get("commands_sent", 0)
    if writes == 0:
        failures.append("No commands sent")
    if summary.get("framing_errors", 0) or summary.get("unexpected_cars", 0):
        failures.append("Invalid serial framing or unexpected car identity")
    for key in ("seq_lost", "seq_errors", "malformed", "command_drops", "telemetry_drops", "malformed_telemetry", "partial_writes"):
        if final.get("io", {}).get(key, 0) != baseline.get("io", {}).get(key, 0):
            failures.append(f"Modem {key} increased")
    modem_received = final.get("io", {}).get("received", 0)-baseline.get("io", {}).get("received", 0)
    if modem_received != writes:
        failures.append(f"Modem received {modem_received} commands; Python sent {writes}")
    if final.get("health", {}).get("enabled") != 0 or final.get("health", {}).get("links") != 0:
        failures.append("Modem did not stop/disconnect cleanly")
    for car in summary["cars"]:
        rows = [row for row in summary["statistics"] if row["car"] == car]
        seconds = sum(row["window_s"] for row in rows)
        received = sum(row["received"] for row in rows)
        sent = sum(row["world_attempted"] for row in rows)
        samples = sum(row["echo_age_samples"] for row in rows)
        age_sum = sum(row["echo_age_avg_ms"]*row["echo_age_samples"] for row in rows if row["echo_age_samples"])
        lost = sum(row["seq_lost"] for row in rows)
        result = {"active_s": seconds, "commands": sent, "notifications": received,
                  "tx_hz": sent/seconds if seconds else 0, "rx_hz": received/seconds if seconds else 0,
                  "notification_seq_lost": lost, "echo_age_avg_ms": age_sum/samples if samples else None,
                  "echo_age_max_ms": max((row["echo_age_max_ms"] for row in rows if row["echo_age_samples"]), default=None),
                  "max_gap_s": max((row["max_gap_s"] for row in rows), default=None)}
        before = baseline.get("links", {}).get(str(car), baseline.get("links", {}).get(car, {}))
        after = final.get("links", {}).get(str(car), final.get("links", {}).get(car, {}))
        result["modem_ble_writes"] = after.get("tx", 0)-before.get("tx", 0)
        if result["modem_ble_writes"] != writes:
            failures.append(f"Car {car}: modem BLE write count differs from Python")
        if after.get("errors", 0) != before.get("errors", 0):
            failures.append(f"Car {car}: BLE write errors")
        if after.get("disconnects", 0)-before.get("disconnects", 0) != 1:
            failures.append(f"Car {car}: disconnect count differs from one intentional shutdown")
        if lost or any(row["malformed"] or row["ignored"] for row in rows):
            failures.append(f"Car {car}: telemetry sequence/format error")
        if not 49 <= result["tx_hz"] <= 51 or not 19.5 <= result["rx_hz"] <= 20.5:
            failures.append(f"Car {car}: rate outside 50±1 Hz TX / 20±0.5 Hz RX")
        if samples == 0:
            failures.append(f"Car {car}: no matched command echoes")
        if server_required:
            server_before = summary.get("server_baselines", {}).get(str(car), summary.get("server_baselines", {}).get(car, {}))
            server_after = summary.get("server_finals", {}).get(str(car), summary.get("server_finals", {}).get(car, {}))
            result["server_received"] = server_after.get("total_received", 0)-server_before.get("total_received", 0)
            result["server_seq_lost"] = server_after.get("total_lost", 0)-server_before.get("total_lost", 0)
            if result["server_received"] != writes or result["server_seq_lost"] != 0:
                failures.append(f"Car {car}: server receive count/loss differs from target")
            if not server_after:
                failures.append(f"Car {car}: missing server serial measurement")
            health_before = summary.get("server_health_baselines", {}).get(str(car), summary.get("server_health_baselines", {}).get(car, {}))
            health_after = summary.get("server_final_health", {}).get(str(car), summary.get("server_final_health", {}).get(car, {}))
            if health_after.get("notify_errors", 0) != health_before.get("notify_errors", 0):
                failures.append(f"Car {car}: server notification errors")
        measurements[str(car)] = result
    summary["measurements"] = measurements
    summary["validation_failures"] = failures
    return not failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--cars", type=int, nargs="+", choices=(1, 2, 3, 4), default=[2, 3])
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--duration-seconds", type=float)
    duration.add_argument("--hours", type=float)
    parser.add_argument("--server-ports", nargs="+", help="serial ports in --cars order; optional")
    parser.add_argument("--start-sequence", type=int, default=65520, help="default crosses uint16 wrap at startup")
    parser.add_argument("--output-dir", type=Path,
                        help="exact run folder; default results/runs/<UTC timestamp>")
    args = parser.parse_args()
    args.duration_seconds = args.hours*3600 if args.hours is not None else (
        args.duration_seconds if args.duration_seconds is not None else 120)
    if not math.isfinite(args.duration_seconds) or args.duration_seconds < 5:
        parser.error("duration must be finite and at least five seconds")
    if len(set(args.cars)) != len(args.cars) or not 0 <= args.start_sequence <= 65535:
        parser.error("cars must be distinct and sequence must be 0..65535")
    names = [args.port, *(args.server_ports or [])]
    if len(set(name.upper() for name in names)) != len(names):
        parser.error("all serial ports must be distinct")
    if args.server_ports and len(args.server_ports) != len(args.cars):
        parser.error("server port count must match --cars")
    log = Log(args)
    bench, old_sleep, timer_changed = None, None, False
    completed = False
    try:
        if os.name == "nt":
            old_sleep = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            timer_changed = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        bench = Bench(args, log)
        bench.prepare()
        bench.traffic()
        completed = True
    except KeyboardInterrupt:
        log.summary["status"] = "interrupted"
    except Exception as exc:
        log.summary["status"] = "failed"
        log.summary["errors"].append(f"{type(exc).__name__}: {exc}")
        log.event("fatal", console=True, message=str(exc))
    finally:
        if bench:
            try:
                bench.shutdown()
                passed = assess(log.summary, bool(args.server_ports)) if completed else False
                if completed:
                    log.summary["status"] = "passed" if passed else "failed"
            except Exception as exc:
                log.summary["status"] = "failed"
                log.summary["errors"].append(f"Cleanup: {type(exc).__name__}: {exc}")
            finally:
                bench.close()
        if old_sleep:
            ctypes.windll.kernel32.SetThreadExecutionState(old_sleep)
        if timer_changed:
            ctypes.windll.winmm.timeEndPeriod(1)
        log.close()
    print(f"Finished: {log.summary['status']}; {log.path.with_suffix('.summary.json')}", flush=True)
    if log.summary.get("validation_failures"):
        print("Validation: " + "; ".join(log.summary["validation_failures"]), flush=True)
    return 0 if log.summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
