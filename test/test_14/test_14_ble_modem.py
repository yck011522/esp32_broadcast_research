"""50 Hz seven-byte world / 20 Hz existing telemetry; automatically recovers USB.

python -u test/test_14/test_14_ble_modem.py --duration-seconds 120 --label bench
python -u test/test_14/test_14_ble_modem.py --hours 1 --label stability
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from collections import OrderedDict
import copy
import csv
import ctypes
from datetime import datetime, timezone
from importlib.metadata import version
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import time

import serial
from serial.tools import list_ports
from test.test_11.test_11_monitor_servers import open_port
from test.test_14.protocol import CAR_PORTS, USB_IDENTITIES, Lines, decode_robot, diagnostic_fields, sequence_delta, world_line

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ("bench", "reconnect", "double", "idle", "expiry", "mailbox", "car-watchdog", "modem-watchdog", "missing-start")


def baseline_ready(required, seen, health, monitor):
    return (required.issubset(seen) and (not monitor or all(
        health.get(car, {}).get("connected") == 1 and health.get(car, {}).get("subscribed") == 1
        for car in required)))


def scenario_plan(name):
    actions, pauses, expected = [], [], {}
    def disconnect(at, cars, seconds=10):
        actions.append((at, cars, f"DISCONNECT {seconds}"))
        for car in cars:
            expected.setdefault(car, []).append((at, at+seconds+12))
    if name == "reconnect":
        for index, car in enumerate((1, 2, 3, 4)):
            disconnect(15+index*35, [car])
    elif name == "double":
        disconnect(15, [2, 3])
    elif name == "idle":
        actions.append((15, [1, 2, 3, 4], "SILENCE 30"))
        pauses.append((15, 45))
        expected = {car: [(15, 48)] for car in CAR_PORTS}
    elif name == "expiry":
        disconnect(15, [1])
        pauses.append((20, 40))
    elif name == "car-watchdog":
        actions.append((15, [1], "HANG"))
        expected[1] = [(15, 48)]
    elif name == "modem-watchdog":
        expected = {car: [(25, 80)] for car in CAR_PORTS}
    elif name == "missing-start":
        expected[1] = [(0, 42)]
    elif name == "mailbox":
        actions.append((15, [], "BURST"))
    return actions, pauses, expected


class PeerWindow:
    def __init__(self, now):
        self.previous = self.last = self.world = None
        self.total_received = self.total_lost = self.total_changes = self.total_duplicates = 0
        self.total_gaps = []
        self.last_echo_sent = None
        self.last_echo_age = None
        self.max_observed_echo_age = 0
        self.await_nonzero_echo = True
        self.reset(now)

    def reset(self, now):
        self.start = now
        self.received = self.lost = self.changes = self.duplicates = 0
        self.max_gap = 0
        self.ages = []
        self.ambiguous_zero_echoes = 0

    def notify(self, packet, now, sent):
        gap = now-self.last if self.last is not None else 0
        lost, changed, duplicate = sequence_delta(self.previous, packet["telemetry_seq"], gap)
        if duplicate:
            self.duplicates += 1
            self.total_duplicates += 1
            return {"duplicate": True}
        self.lost += lost
        self.changes += changed
        self.total_lost += lost
        self.total_changes += changed
        if self.last is not None:
            self.max_gap = max(self.max_gap, gap)
            self.total_gaps.append(gap)
        self.previous = packet["telemetry_seq"]
        self.last = now
        self.world = packet["world_seq"]
        self.received += 1
        self.total_received += 1
        if changed or gap > 1:
            self.await_nonzero_echo = True
        if self.world != 0:
            self.await_nonzero_echo = False
        ambiguous_zero = self.world == 0 and self.await_nonzero_echo
        if ambiguous_zero:
            self.ambiguous_zero_echoes += 1
        generated = sent.get(self.world) if not ambiguous_zero else None
        self.last_echo_age = (now-generated)*1000 if generated is not None else None
        self.last_echo_sent = generated
        if self.last_echo_age is not None and 0 <= self.last_echo_age <= 40000:
            self.ages.append(self.last_echo_age)
            self.max_observed_echo_age = max(self.max_observed_echo_age, self.last_echo_age)
        return {"inferred_lost": lost, "sequence_discontinuity": changed, "gap_s": gap,
                "echo_age_ms": self.last_echo_age, "ambiguous_zero_echo": ambiguous_zero}

    def snapshot(self, now):
        elapsed = now-self.start
        row = {"window_s": elapsed, "received": self.received, "rx_hz": self.received/elapsed if elapsed else 0,
               "inferred_seq_lost": self.lost, "sequence_discontinuities": self.changes, "duplicates": self.duplicates,
               "max_gap_s": self.max_gap, "silence_s": now-self.last if self.last is not None else now-self.start,
               "world_seq": self.world if self.world is not None else "", "echo_samples": len(self.ages),
               "ambiguous_zero_echoes": self.ambiguous_zero_echoes,
               "echo_age_avg_ms": sum(self.ages)/len(self.ages) if self.ages else "",
               "echo_age_max_ms": max(self.ages) if self.ages else ""}
        self.reset(now)
        return row


def capture(directory, args):
    paths = [ROOT/"platformio.ini", ROOT/"payload.md"]
    paths += [p for p in (ROOT/"src/test_14").rglob("*") if p.suffix in (".cpp", ".h")]
    paths += [p for p in (ROOT/"test/test_14").glob("*.py")]
    sources = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    builds = {}
    for profile in (args.modem_profile, *(f"ble14_car_{car}" for car in CAR_PORTS)):
        binary = ROOT/".pio/build"/profile/"firmware.bin"
        if binary.exists():
            target = directory/"firmware"/(profile+".bin")
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(binary, target)
            builds[profile] = {"file": target.relative_to(directory).as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    settings = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    metadata = {"python": platform.python_version(), "platform": platform.platform(),
                "packages": {name: version(name) for name in ("pyserial", "matplotlib")},
                "settings": settings, "source_sha256": sources, "available_builds": builds,
                "build_note": "Available builds copied locally, not firmware read back from devices."}
    (directory/"manifest.json").write_text(json.dumps(metadata, indent=2)+"\n", encoding="utf-8")


class Run:
    def __init__(self, args):
        self.args = args
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.directory = args.output_dir or Path(__file__).parent/"results/runs"/(stamp+"_"+args.label)
        self.directory.mkdir(parents=True, exist_ok=True)
        capture(self.directory, args)
        self.base = self.directory/("ble_test_14_"+stamp)
        self.raw = self.base.with_suffix(".jsonl").open("w", encoding="utf-8")
        self.csv = self.base.with_suffix(".csv").open("w", newline="", encoding="utf-8")
        self.writer = None
        self.created = time.perf_counter()
        self.start = None
        self.handles, self.reopen_at, self.decoders, self.car_logs = {}, {}, {}, {}
        self.health, self.health_history, self.seen = {}, [], set()
        self.peers = {}
        self.sent = OrderedDict()
        self.sequence = args.start_sequence
        self.submitted = self.window_submitted = self.generated = self.overruns = 0
        self.rows, self.events = [], []
        self.phase = "setup"
        self.summary = {"experiment": "14", "scenario": args.scenario, "label": args.label,
                        "started_utc": datetime.now(timezone.utc).isoformat(), "planned_duration_s": args.duration_seconds,
                        "status": "running", "errors": [], "server_monitoring": not args.no_server_monitor,
                        "measurement_note": "PC submits are not car receipts. Sequence changes cannot uniquely identify reconnect/reboot without session metadata."}
        self.actions, self.pauses, self.expected = scenario_plan(args.scenario)
        self.port_names = {"modem": args.port}
        if not args.no_server_monitor:
            self.port_names.update(CAR_PORTS)
            for car in CAR_PORTS:
                self.car_logs[car] = (self.directory/f"car_{car}_{CAR_PORTS[car]}.log").open("w", encoding="utf-8")
        self.summary["port_names"] = self.port_names
        self.summary.update(command_pauses=self.pauses, planned_outages=self.expected)
        print(f"Test 14: {self.base.with_suffix('.csv')}", flush=True)

    def event(self, kind, console=False, **details):
        row = {"timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "elapsed_s": time.perf_counter()-self.created, "phase": self.phase,
               "record_type": kind, **details}
        self.raw.write(json.dumps(row)+"\n")
        if kind not in ("telemetry", "world_sent", "car_serial", "stats"):
            self.events.append(row)
        if console:
            print(f"[{row['elapsed_s']:.1f}s] {kind}: {details}", flush=True)
        return row

    def open_missing(self, now):
        for role, configured in self.port_names.items():
            if role in self.handles or now < self.reopen_at.get(role, 0):
                continue
            self.reopen_at[role] = now+1
            try:
                # Verify USB identity each time, including watchdog re-enumeration.
                identity = USB_IDENTITIES[configured]
                matches = [p for p in list_ports.comports() if p.serial_number == identity]
                if len(matches) != 1:
                    continue
                port = open_port(matches[0].device)
                port.write_timeout = .5
                self.handles[role] = port
                self.decoders[role] = Lines()
                self.event("serial_opened", console=True, role=role, port=port.port)
            except (serial.SerialException, OSError) as exc:
                self.event("serial_open_failed", role=role, error=str(exc))

    def lost_port(self, role, exc):
        handle = self.handles.pop(role, None)
        if handle:
            handle.close()
        self.reopen_at[role] = time.perf_counter()+.5
        self.event("serial_interrupted", console=True, role=role, error=str(exc))

    def pump(self):
        now = time.perf_counter()
        self.open_missing(now)
        for role, port in list(self.handles.items()):
            try:
                if not port.in_waiting:
                    continue
                chunk = port.read(min(port.in_waiting, 8192))
            except (serial.SerialException, OSError) as exc:
                self.lost_port(role, exc)
                continue
            for line in self.decoders[role].feed(chunk):
                at = time.perf_counter()
                if role != "modem":
                    message = line.decode(errors="replace") if line is not None else "oversized_line"
                    self.car_logs[role].write(f"[{at-self.created:.3f}s] {message}\n")
                    self.car_logs[role].flush()
                    self.event("car_serial", car=role, message=message)
                    if message.startswith("car14:"):
                        fields = diagnostic_fields(message)
                        if fields.get("car") != role:
                            raise RuntimeError(f"Unexpected car identity on {port.port}")
                        previous = self.health.get(role)
                        if previous and fields["uptime_ms"] < previous["uptime_ms"]:
                            self.event("car_uptime_reset", console=True, car=role)
                        self.health[role] = fields
                        self.health_history.append({"elapsed_s": at-self.created, **fields})
                    continue
                if not line:
                    continue
                try:
                    packet = decode_robot(line)
                except ValueError:
                    self.event("non_packet_modem_line", line_hex=line.hex() if line is not None else None)
                    continue
                car = packet["car"]
                self.seen.add(car)
                result = self.peers[car].notify(packet, at, self.sent) if self.peers and self.phase == "traffic" else {}
                if self.peers and self.phase != "traffic":
                    self.peers[car].last = at
                self.event("telemetry", car=car, data_hex=line.decode(), **result)
                if result.get("sequence_discontinuity"):
                    self.event("sequence_discontinuity", console=True, car=car, sequence=packet["telemetry_seq"])
                if result.get("gap_s", 0) > 1:
                    self.event("traffic_resumed", console=True, car=car, gap_s=result["gap_s"])

    def control_car(self, car, command):
        port = self.handles.get(car)
        if port is None:
            raise RuntimeError(f"Car {car} serial port unavailable for controlled test")
        data = (command+"\n").encode("ascii")
        if port.write(data) != len(data):
            raise RuntimeError("Partial car test command")
        self.event("car_test_command", console=True, car=car, command=command)

    def prepare(self):
        deadline = time.perf_counter()+65
        while "modem" not in self.handles or (not self.args.no_server_monitor and len(self.handles) != 5):
            self.pump()
            if time.perf_counter() > deadline:
                raise RuntimeError("Serial ports did not become available")
            time.sleep(.01)
        required = {1, 2, 3, 4}
        if self.args.scenario == "missing-start":
            self.control_car(1, "DISCONNECT 30")
            required.remove(1)
            self.seen.clear()
        while not baseline_ready(required, self.seen, self.health, not self.args.no_server_monitor):
            self.pump()
            if time.perf_counter() > deadline:
                raise RuntimeError(f"Initial telemetry missing: {sorted(required-self.seen)}")
            time.sleep(.001)
        self.summary["car_baselines"] = copy.deepcopy(self.health)
        self.event("baseline_ready", console=True, cars=sorted(required))

    def expected_outage(self, car, elapsed):
        return any(a <= elapsed <= b for a, b in self.expected.get(car, []))

    def world_active(self, elapsed):
        return not any(a <= elapsed < b for a, b in self.pauses)

    def check_echo_freshness(self, elapsed):
        return (elapsed > 1 and self.world_active(elapsed)
                and not any(b <= elapsed < b+.25 for _, b in self.pauses))

    def submit(self, now):
        sequence = self.sequence
        self.sequence = (sequence+1)&0xffff
        self.generated += 1
        port = self.handles.get("modem")
        if port is None:
            return
        line = world_line(sequence)
        try:
            if port.write(line) != len(line):
                raise serial.SerialException("Partial WorldState serial submission")
        except (serial.SerialException, OSError) as exc:
            self.lost_port("modem", exc)
            return
        self.submitted += 1
        self.window_submitted += 1
        self.sent[sequence] = now
        if len(self.sent) > 2048:
            self.sent.popitem(last=False)
        self.event("world_sent", sequence=sequence, data_hex=line[1:-1].decode())

    def report(self, now, partial=False):
        elapsed = now-self.start
        for car, peer in self.peers.items():
            row = {"elapsed_s": now-self.created, "traffic_s": elapsed, "car": car,
                   "world_active": int(self.world_active(elapsed)), "expected_outage": int(self.expected_outage(car, elapsed)),
                   **peer.snapshot(now), "partial": int(partial)}
            row["pc_submit_hz"] = self.window_submitted/row["window_s"] if row["window_s"] else 0
            if self.writer is None:
                self.writer = csv.DictWriter(self.csv, fieldnames=list(row))
                self.writer.writeheader()
            self.writer.writerow(row)
            self.rows.append(row)
            self.event("stats", **row)
            age = f"{row['echo_age_avg_ms']:.1f}" if row["echo_samples"] else "n/a"
            print(f"[{elapsed:.1f}s] car={car} PC_TX={row['pc_submit_hz']:.2f} Hz RX={row['rx_hz']:.2f} Hz "
                  f"gaps={row['inferred_seq_lost']} changes={row['sequence_discontinuities']} echo_age={age} ms", flush=True)
        self.window_submitted = 0
        self.csv.flush()
        self.raw.flush()

    def traffic(self):
        self.phase = "traffic"
        self.start = time.perf_counter()
        self.peers = {car: PeerWindow(self.start) for car in CAR_PORTS}
        deadline = self.start+self.args.duration_seconds
        next_send = self.start
        next_report = self.start+5
        actions = list(self.actions)
        warnings = set()
        try:
            while time.perf_counter() < deadline:
                self.pump()
                now = time.perf_counter()
                elapsed = now-self.start
                if self.args.stop_file and self.args.stop_file.exists():
                    self.summary["stop_reason"] = "stop_file"
                    break
                while actions and elapsed >= actions[0][0]:
                    _, cars, command = actions.pop(0)
                    if command == "BURST":
                        for _ in range(200):
                            self.submit(time.perf_counter())
                        self.event("mailbox_burst", console=True, final_sequence=(self.sequence-1)&65535)
                    else:
                        for car in cars:
                            self.control_car(car, command)
                if now >= next_send:
                    if self.world_active(elapsed):
                        self.submit(now)
                    next_send += .020
                    if next_send <= time.perf_counter():
                        self.overruns += 1
                        next_send = time.perf_counter()+.020
                for car, peer in self.peers.items():
                    silence = now-(peer.last if peer.last is not None else self.start)
                    stale = (now-peer.last_echo_sent)*1000 if peer.last_echo_sent is not None else None
                    bad = silence > 1 or (self.check_echo_freshness(elapsed) and stale is not None and stale > 1000)
                    if bad and car not in warnings:
                        warnings.add(car)
                        self.event("traffic_warning", console=True, car=car, silence_s=silence,
                                   echo_age_ms=stale, expected=self.expected_outage(car, elapsed))
                    elif not bad and car in warnings:
                        warnings.remove(car)
                        self.event("traffic_warning_cleared", console=True, car=car)
                if now >= next_report:
                    self.report(now)
                    next_report = now+5
                time.sleep(max(0, min(.001, next_send-time.perf_counter())))
        finally:
            self.report(time.perf_counter(), partial=True)
            self.summary.update(active_s=time.perf_counter()-self.start, pc_submitted=self.submitted,
                                world_generated=self.generated, pacing_overruns=self.overruns)
            self.summary["traffic_final_health"] = {str(car): {"silence_s": time.perf_counter()-(peer.last or self.start),
                  "echo_age_ms": peer.last_echo_age} for car, peer in self.peers.items()}

    def drain(self):
        self.phase = "drain"
        # Stop generating worlds; the modem and BLE links keep running.
        deadline = time.perf_counter()+5.2
        while time.perf_counter() < deadline:
            self.pump()
            time.sleep(.001)
        self.summary["car_finals"] = copy.deepcopy(self.health)

    def assess(self):
        failures = []
        now = time.perf_counter()
        for car, peer in self.peers.items():
            if self.summary["traffic_final_health"][str(car)]["silence_s"] > .5:
                failures.append(f"Car {car}: traffic ended without fresh telemetry")
            if peer.last is None or now-peer.last > .5:
                failures.append(f"Car {car}: telemetry did not finish healthy")
            rows = [r for r in self.rows if r["car"] == car and not r["partial"]]
            for row in rows:
                begin, end = row["traffic_s"]-row["window_s"], row["traffic_s"]
                crosses_outage = any(begin < b and end > a for a, b in self.expected.get(car, []))
                if not crosses_outage and not 19.5 <= row["rx_hz"] <= 20.5:
                    failures.append(f"Car {car}: healthy telemetry window outside 20±0.5 Hz")
            if self.args.scenario == "bench":
                seconds = sum(r["window_s"] for r in rows)
                rate = sum(r["received"] for r in rows)/seconds if seconds else 0
                if not 19.5 <= rate <= 20.5:
                    failures.append(f"Car {car}: telemetry rate {rate:.3f} Hz")
                if peer.total_lost or peer.total_changes or peer.total_duplicates:
                    failures.append(f"Car {car}: sequence continuity anomaly")
            before = self.summary.get("car_baselines", {}).get(car, {})
            after = self.health.get(car, {})
            if not self.args.no_server_monitor:
                if after.get("connected") != 1 or after.get("subscribed") != 1:
                    failures.append(f"Car {car}: missing connected/subscribed serial observation")
                if self.args.scenario in ("bench", "idle", "mailbox") and after.get("connects") != before.get("connects"):
                    failures.append(f"Car {car}: unexpected reconnect")
                if self.args.scenario == "bench" and after.get("total_rx", 0)-before.get("total_rx", 0) != self.submitted:
                    failures.append(f"Car {car}: car receipt count differs from PC submits")
                if self.args.scenario in ("reconnect", "double", "expiry", "missing-start") and car in self.expected:
                    if after.get("connects", 0) <= before.get("connects", 0):
                        failures.append(f"Car {car}: subscription recovery was not independently observed")
                for sample in self.health_history:
                    if sample["car"] != car:
                        continue
                    end = sample["elapsed_s"]-(self.start-self.created)
                    begin = end-5
                    excluded = self.expected.get(car, [])+self.pauses
                    if self.args.scenario == "mailbox":
                        excluded += [(14, 21)]
                    if begin >= .25 and end <= self.summary["active_s"] and not any(begin < b and end > a for a, b in excluded):
                        if not 49 <= sample["rx_hz"] <= 51:
                            failures.append(f"Car {car}: healthy serial-observed command window outside 50±1 Hz")
            if not any(r["echo_samples"] for r in rows):
                failures.append(f"Car {car}: no matched world echoes")
        if self.args.scenario == "bench" and not 49 <= self.submitted/self.summary["active_s"] <= 51:
            failures.append("PC submit rate outside 50±1 Hz")
        for event in self.events:
            if event["record_type"] == "traffic_warning" and not event.get("expected"):
                failures.append(f"Unexpected traffic warning for Car {event['car']}")
        # Scripted outages require positive evidence of the requested transition,
        # not merely absence of a fatal exception.
        for car, periods in self.expected.items():
            if self.args.scenario in ("reconnect", "double", "missing-start", "expiry"):
                history = [r for r in self.health_history if r["car"] == car]
                if not any(r.get("connected") == 0 for r in history):
                    failures.append(f"Car {car}: controlled outage was not observed")
        if self.args.scenario == "idle":
            if not all(any(r["car"] == car and r.get("connected") == 1 and r.get("rx_hz") == 0 and r.get("led") == 0
                           and 20 <= r["elapsed_s"]-(self.start-self.created) <= 40 for r in self.health_history)
                       for car in CAR_PORTS):
                failures.append("Idle connected state with LEDs off was not observed for every car")
        if self.args.scenario == "car-watchdog" and not any(e["record_type"] == "car_uptime_reset" and e.get("car") == 1 for e in self.events):
            failures.append("Car watchdog reset was not independently observed")
        if self.args.scenario == "modem-watchdog":
            for car in CAR_PORTS:
                before = self.summary["car_baselines"].get(car, {})
                if self.health.get(car, {}).get("connects", 0) <= before.get("connects", 0):
                    failures.append(f"Car {car}: modem watchdog reconnect was not observed")
        self.summary["validation_failures"] = sorted(set(failures))
        self.summary["measurements"] = {str(car): {"notifications": peer.total_received,
            "inferred_seq_lost": peer.total_lost, "sequence_discontinuities": peer.total_changes,
            "duplicates": peer.total_duplicates, "max_arrival_gap_s": max(peer.total_gaps, default=0),
            "max_matched_echo_age_ms": peer.max_observed_echo_age} for car, peer in self.peers.items()}
        return not failures

    def close(self):
        for port in self.handles.values():
            port.close()
        for stream in self.car_logs.values():
            stream.close()
        self.summary.update(finished_utc=datetime.now(timezone.utc).isoformat(), statistics=self.rows,
                            events=self.events, car_health_history=self.health_history)
        self.base.with_suffix(".summary.json").write_text(json.dumps(self.summary, indent=2)+"\n", encoding="utf-8")
        self.csv.close()
        self.raw.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3", choices=["COM3"])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--duration-seconds", type=float)
    group.add_argument("--hours", type=float)
    parser.add_argument("--label", default="bench")
    parser.add_argument("--scenario", choices=SCENARIOS, default="bench")
    parser.add_argument("--no-server-monitor", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--start-sequence", type=int, default=65520)
    parser.add_argument("--modem-profile", default="ble14_modem", choices=("ble14_modem", "ble14_modem_watchdog_test"))
    args = parser.parse_args(argv)
    args.duration_seconds = args.hours*3600 if args.hours is not None else (
        args.duration_seconds if args.duration_seconds is not None else 120)
    if not math.isfinite(args.duration_seconds) or args.duration_seconds < 5:
        parser.error("duration must be finite and at least 5 seconds")
    if args.scenario != "bench" and args.no_server_monitor:
        parser.error("controlled scenarios require car serial ports")
    if args.stop_file and args.stop_file.exists():
        parser.error("stop file already exists")
    if not 0 <= args.start_sequence <= 65535:
        parser.error("start sequence must be uint16")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.label):
        parser.error("label must contain 1..80 letters, digits, underscores or hyphens")
    run = Run(args)
    old_sleep, timer = None, False
    try:
        if os.name == "nt":
            old_sleep = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            timer = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        run.prepare()
        run.traffic()
        run.drain()
        run.summary["status"] = "passed" if run.assess() else "failed"
    except KeyboardInterrupt:
        run.summary["status"] = "interrupted"
    except Exception as exc:
        run.summary["status"] = "failed"
        run.summary["errors"].append(f"{type(exc).__name__}: {exc}")
        run.event("fatal", console=True, message=str(exc))
    finally:
        run.close()
        if old_sleep:
            ctypes.windll.kernel32.SetThreadExecutionState(old_sleep)
        if timer:
            ctypes.windll.winmm.timeEndPeriod(1)
    print(f"Finished: {run.summary['status']}; {run.base.with_suffix('.summary.json')}", flush=True)
    if run.summary.get("validation_failures"):
        print("Validation: "+"; ".join(run.summary["validation_failures"]), flush=True)
    return 0 if run.summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
