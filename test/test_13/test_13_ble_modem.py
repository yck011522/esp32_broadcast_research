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


def enforce_traffic_limits(car, state, now, start, max_echo_age_ms, telemetry_timeout_s):
    silence = now-(state.last_receive if state.last_receive is not None else start)
    if telemetry_timeout_s > 0 and silence > telemetry_timeout_s:
        raise RuntimeError(f"Car {car} telemetry silence {silence:.3f} s exceeded {telemetry_timeout_s:g} s")
    startup_grace = max(3, max_echo_age_ms/1000, telemetry_timeout_s)
    if now-start > startup_grace and not state.confirmed_echo:
        raise RuntimeError(f"Car {car} never echoed a command")
    if max_echo_age_ms > 0 and state.ages and state.ages[-1] > max_echo_age_ms:
        raise RuntimeError(f"Car {car} echoed-command age {state.ages[-1]:.1f} ms exceeded {max_echo_age_ms:g} ms")


def verify_modem_stopped(health, observed_at, requested_at):
    if observed_at < requested_at or health.get("enabled") != 0 or health.get("links") != 0:
        raise RuntimeError("Modem did not acknowledge a fresh stopped/disconnected state; its current state is unconfirmed")


class Log:
    def __init__(self, args):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        experiment = getattr(args, "experiment", "13")
        label = getattr(args, "label", "")
        result_root = getattr(args, "result_root", Path(__file__).parent / "results/runs")
        directory = args.output_dir or result_root / (stamp + ("_"+label if label else ""))
        directory.mkdir(parents=True, exist_ok=True)
        capture_test13(directory, {"port": args.port, "cars": args.cars,
                                  "server_ports": args.server_ports,
                                  "duration_seconds": args.duration_seconds,
                                  "start_sequence": getattr(args, "start_sequence", None),
                                  "experiment": experiment, "label": label,
                                  "recovery_mode": getattr(args, "recovery", False),
                                  "max_echo_age_ms": getattr(args, "max_echo_age_ms", 1000),
                                  "telemetry_timeout_s": getattr(args, "telemetry_timeout_s", 3)},
                       profiles=getattr(args, "firmware_profiles", None),
                       extra_sources=getattr(args, "extra_sources", ()))
        self.path = directory / f"ble_test_{experiment}_{stamp}.jsonl"
        self.raw = self.path.open("w", encoding="utf-8")
        self.start = time.perf_counter()
        self.phase = "setup"
        self.summary = {"started_utc": datetime.now(timezone.utc).isoformat(), "port": args.port,
                        "cars": args.cars, "planned_duration_s": args.duration_seconds,
                        "experiment": experiment, "label": label,
                        "server_monitoring": bool(args.server_ports),
                        "recovery_mode": getattr(args, "recovery", False),
                        "traffic_limits": {"max_echo_age_ms": getattr(args, "max_echo_age_ms", 1000),
                                           "telemetry_timeout_s": getattr(args, "telemetry_timeout_s", 3)},
                        "status": "running", "errors": [], "statistics": [], "serial_logs": {}}
        print(f"Test {experiment}: {self.path}", flush=True)

    def event(self, kind, console=False, **details):
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "elapsed_s": time.perf_counter()-self.start, "phase": self.phase,
               "record_type": kind, **details}
        self.raw.write(json.dumps(row) + "\n")
        if console:
            print(f"[{row['elapsed_s']:.1f}s] {kind}: {details}", flush=True)
        return row

    def report(self, states, partial=False, recovery=None, now=None):
        now = time.perf_counter() if now is None else now
        for car, state in states.items():
            row = self.event("ble_stats", car=car,
                             **(recovery.columns(car) if recovery else {}),
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


class Recovery:
    """Observe existing firmware retries; never initiate a global stop/restart.

    Link readiness and fresh command echoes are separate milestones. Sequence
    numbers are meaningful only within a car's subscription session.
    """
    def __init__(self, bench, start):
        self.bench = bench
        self.start = start
        self.peers = {car: {"session": 1, "session_start": start, "ready": True,
                            "echo_sent": None,
                            "last_telemetry": None,
                            "warnings": set(), "outage": None, "episodes": [],
                            "disconnects": 0, "reconnections": 0}
                      for car in bench.args.cars}

    def emit(self, car, event, **details):
        return self.bench.log.event("recovery_event", console=True, car=car,
                                    event=event, session=self.peers[car]["session"], **details)

    def outage(self, car, now, cause):
        peer = self.peers[car]
        if peer["outage"] is None:
            state = self.bench.states[car]
            row = self.emit(car, "outage_started", cause=cause)
            episode = {"started_elapsed_s": row["elapsed_s"], "cause": cause,
                       "last_telemetry_elapsed_s": (state.last_receive-self.bench.log.start
                                                   if state.last_receive is not None else None),
                       "max_telemetry_gap_s": 0,
                       "recovered": False}
            peer["episodes"].append(episode)
            peer["outage"] = (now, episode)

    def link(self, car, ready, now, reason=None):
        if car not in self.peers:
            return
        peer = self.peers[car]
        if ready == peer["ready"]:
            return  # Periodic #LINK and the corresponding #EVENT describe one transition.
        if not ready:
            self.outage(car, now, "disconnected")
            peer["disconnects"] += 1
            peer["ready"] = False
            self.emit(car, "link_down", reason=reason)
        else:
            # Preserve the previous window, including offline time and PC commands.
            self.bench.log.report({car: self.bench.states[car]}, partial=True,
                                  recovery=self, now=now)
            self.bench.states[car] = TelemetryWindow(car, now)
            peer["session"] += 1
            peer["session_start"] = now
            peer["echo_sent"] = None
            peer["ready"] = True
            peer["reconnections"] += 1
            row = self.emit(car, "link_ready")
            if peer["outage"]:
                peer["outage"][1]["link_ready_elapsed_s"] = row["elapsed_s"]

    def packet(self, car, now):
        peer = self.peers[car]
        state = self.bench.states[car]
        if peer["outage"] and peer["last_telemetry"] is not None:
            episode = peer["outage"][1]
            episode["max_telemetry_gap_s"] = max(episode["max_telemetry_gap_s"],
                                                 now-peer["last_telemetry"])
        peer["last_telemetry"] = now
        sent = state.sent_times.get(state.latest_world) if state.confirmed_echo else None
        if sent is not None:
            peer["echo_sent"] = sent
        if not peer["ready"] or not peer["outage"] or sent is None:
            return
        began, episode = peer["outage"]
        limit = self.bench.args.max_echo_age_ms
        if sent < began or (limit > 0 and (now-sent)*1000 > limit):
            return  # An old buffered notification is not proof of command recovery.
        row = self.emit(car, "traffic_restored", outage_s=now-began,
                        echo_age_ms=(now-sent)*1000)
        episode.update(recovered=True, restored_elapsed_s=row["elapsed_s"],
                       outage_s=now-began, echo_age_ms=(now-sent)*1000)
        peer["outage"] = None

    def watch(self, now):
        args = self.bench.args
        for car, peer in self.peers.items():
            state = self.bench.states[car]
            silence = now-(state.last_receive if state.last_receive is not None else peer["session_start"])
            warnings = set()
            if not peer["ready"]:
                warnings.add("link_down")
            if args.telemetry_timeout_s > 0 and silence > args.telemetry_timeout_s:
                warnings.add("telemetry_silence")
            sent = state.sent_times.get(state.latest_world) if state.confirmed_echo else None
            if sent is not None:
                peer["echo_sent"] = sent
            # Evicting a command from the bounded timestamp lookup does not
            # mean that the car has begun echoing fresh commands again.
            sent = peer["echo_sent"]
            age = (now-sent)*1000 if sent is not None else None
            if args.max_echo_age_ms > 0 and age is not None and age > args.max_echo_age_ms:
                warnings.add("stale_command_echo")
            grace = max(3, args.max_echo_age_ms/1000, args.telemetry_timeout_s)
            if now-peer["session_start"] > grace and sent is None and peer["ready"]:
                warnings.add("no_matched_echo")
            for warning in sorted(warnings-peer["warnings"]):
                self.outage(car, now, warning)
                self.emit(car, "warning", condition=warning, silence_s=silence, echo_age_ms=age)
            for warning in sorted(peer["warnings"]-warnings):
                self.emit(car, "warning_cleared", condition=warning)
            peer["warnings"] = warnings

    def columns(self, car):
        peer = self.peers[car]
        return {"session": peer["session"], "link_ready": int(peer["ready"]),
                "recovery_pending": int(peer["outage"] is not None),
                "health_warnings": ",".join(sorted(peer["warnings"]))}

    def finish(self, now):
        cars = {}
        for car, peer in self.peers.items():
            episodes = copy.deepcopy(peer["episodes"])
            if peer["outage"]:
                episodes[-1]["outage_s"] = now-peer["outage"][0]
            cars[str(car)] = {"sessions": peer["session"], "disconnects": peer["disconnects"],
                              "reconnections": peer["reconnections"], "ready_at_end": peer["ready"],
                              "warnings_at_end": sorted(peer["warnings"]), "outages": episodes,
                              "recovered_outages": sum(e["recovered"] for e in episodes),
                              "pending_outage": peer["outage"] is not None}
        self.bench.log.summary["recovery"] = {"cars": cars,
            "all_outages_recovered": all(not p["pending_outage"] for p in cars.values()),
            "all_healthy_at_end": all(p["ready_at_end"] and not p["warnings_at_end"]
                                      and not p["pending_outage"] for p in cars.values())}


class Bench:
    def __init__(self, args, log):
        self.args, self.log = args, log
        self.modem = open_port(args.port)
        self.modem.write_timeout = .5
        self.modem.reset_input_buffer()
        self.decoder = HexLines()
        self.links, self.health, self.io = {}, {}, {}
        self.health_observed_at = 0
        self.server_world, self.server_health = {}, {}
        self.server_handles = []
        self.states = {}
        self.framing_errors = self.unexpected_cars = 0
        self.commands_sent = 0
        self.last_request = 0
        self.measuring = False
        self.recovery = None
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
                        self.health_observed_at = time.perf_counter()
                    elif data.startswith("#IO "):
                        self.io = values
                    elif data.startswith("#LINK "):
                        self.links[values["car"]] = values
                        if self.measuring and self.recovery:
                            self.recovery.link(values["car"], bool(values.get("ready")), time.perf_counter())
                    elif data.startswith("#EVENT "):
                        self.log.event("modem_event", console=True, **values)
                        if self.measuring:
                            if self.recovery and values.get("event") in ("disconnected", "ready"):
                                self.recovery.link(values.get("car"), values["event"] == "ready",
                                                   time.perf_counter(), values.get("reason"))
                            elif values.get("event") == "disconnected":
                                raise RuntimeError(f"Car {values.get('car')} disconnected during traffic")
                elif kind == "telemetry":
                    car = data[0]
                    if car not in self.args.cars:
                        self.unexpected_cars += 1
                    state = self.states.get(car)
                    now = time.perf_counter()
                    if self.recovery and car in self.recovery.peers and not self.recovery.peers[car]["ready"]:
                        accepted = "link_not_ready"
                    else:
                        accepted = state.notify(data, now) if state and self.measuring else "outside_measurement"
                    self.log.event("telemetry", car=car, data_hex=data.hex(), accepted=accepted,
                                   **({"session": self.recovery.peers[car]["session"]}
                                      if self.recovery and car in self.recovery.peers else {}),
                                   decoded=state.telemetry if state and accepted == "accepted" else None)
                    if self.measuring and self.recovery and accepted == "accepted":
                        self.recovery.packet(car, now)
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
        self.recovery = Recovery(self, start) if self.args.recovery else None
        self.measuring = True
        sequence = self.args.start_sequence
        next_send = start
        deadline = start+self.args.duration_seconds
        next_report = start+5
        next_stop_check = start
        try:
            while time.perf_counter() < deadline:
                self.pump()
                now = time.perf_counter()
                if self.args.stop_file and now >= next_stop_check:
                    next_stop_check = now+.25
                    if self.args.stop_file.exists():
                        self.log.event("stop_requested", console=True, path=str(self.args.stop_file))
                        self.log.summary["stop_reason"] = "stop_file"
                        break
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
                if self.recovery:
                    self.recovery.watch(now)
                else:
                    for car, state in self.states.items():
                        enforce_traffic_limits(car, state, now, start,
                                               self.args.max_echo_age_ms, self.args.telemetry_timeout_s)
                if now >= next_report:
                    self.log.report(self.states, recovery=self.recovery)
                    next_report = now+5
                time.sleep(max(0, min(.001, next_send-time.perf_counter())))
        finally:
            if self.recovery:
                self.recovery.finish(time.perf_counter())
            self.log.report(self.states, partial=True, recovery=self.recovery)
            self.measuring = False
            self.log.summary["commands_sent"] = self.commands_sent
            self.log.summary["active_s"] = time.perf_counter()-start

    def shutdown(self):
        self.log.phase = "drain"
        self.wait(.25) # Let the final command reach each server before stopping.
        requested_at = time.perf_counter()
        self.control("#STOP")
        self.log.phase = "post_stop"
        self.wait(6, request_status=True)
        self.log.summary["modem_final"] = {"health": copy.deepcopy(self.health),
            "io": copy.deepcopy(self.io), "links": copy.deepcopy(self.links),
            "health_age_s": time.perf_counter()-self.health_observed_at,
            "fresh_stop_acknowledged": self.health_observed_at >= requested_at
                and self.health.get("enabled") == 0 and self.health.get("links") == 0}
        self.log.summary["server_finals"] = copy.deepcopy(self.server_world)
        self.log.summary["server_final_health"] = copy.deepcopy(self.server_health)
        self.log.summary["framing_errors"] = self.framing_errors-self.log.summary.get("framing_baseline", 0)
        self.log.summary["unexpected_cars"] = self.unexpected_cars-self.log.summary.get("unexpected_car_baseline", 0)
        verify_modem_stopped(self.health, self.health_observed_at, requested_at)

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


def main(argv=None, *, experiment="13", default_cars=(2, 3), default_server_ports=None,
         result_root=None, firmware_profiles=None, extra_sources=(), description=None,
         default_max_echo_age_ms=1000):
    parser = argparse.ArgumentParser(description=description or __doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--cars", type=int, nargs="+", choices=(1, 2, 3, 4), default=list(default_cars))
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--duration-seconds", type=float)
    duration.add_argument("--hours", type=float)
    monitors = parser.add_mutually_exclusive_group()
    monitors.add_argument("--server-ports", nargs="+", default=default_server_ports,
                          help="serial ports in --cars order; optional")
    monitors.add_argument("--no-server-monitor", action="store_true",
                          help="cars are remotely powered; monitor telemetry through COM3 only")
    parser.add_argument("--label", default="", help="run condition, e.g. bench or far_10s")
    parser.add_argument("--max-echo-age-ms", type=float, default=default_max_echo_age_ms,
                        help="stop above this echoed-command age; 0 disables the age limit")
    parser.add_argument("--telemetry-timeout-s", type=float, default=3,
                        help="stop after this telemetry silence; 0 disables the silence limit")
    parser.add_argument("--recovery", action="store_true",
                        help="observe firmware reconnects; disconnect/age/silence limits become logged warnings")
    parser.add_argument("--stop-file", type=Path,
                        help="finish and save results cleanly when this file appears; must not exist at startup")
    parser.add_argument("--start-sequence", type=int, default=65520, help="default crosses uint16 wrap at startup")
    parser.add_argument("--output-dir", type=Path,
                        help="exact run folder; default results/runs/<UTC timestamp>")
    args = parser.parse_args(argv)
    if args.stop_file and args.stop_file.exists():
        parser.error("stop file already exists; choose a new path")
    if args.no_server_monitor:
        args.server_ports = None
    if args.label and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.label):
        parser.error("label must contain 1..80 letters, digits, underscores or hyphens")
    args.experiment = experiment
    args.result_root = result_root or Path(__file__).parent / "results/runs"
    args.firmware_profiles, args.extra_sources = firmware_profiles, extra_sources
    if any(not math.isfinite(value) or value < 0
           for value in (args.max_echo_age_ms, args.telemetry_timeout_s)):
        parser.error("traffic limits must be finite and nonnegative; use 0 to disable")
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
                    log.summary["performance_passed"] = passed
                    log.summary["status"] = ("observation_completed" if args.recovery
                                             else "passed" if passed else "failed")
            except Exception as exc:
                log.summary["status"] = "failed"
                log.summary["errors"].append(f"Cleanup: {type(exc).__name__}: {exc}")
                if completed and "modem_final" in log.summary:
                    log.summary["performance_passed"] = assess(log.summary, bool(args.server_ports))
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
    return 0 if log.summary["status"] in ("passed", "observation_completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
