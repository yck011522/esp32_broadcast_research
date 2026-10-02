"""Test 6: OTA firmware update of the slave ESP32 over the offline lab router.

SETUP
-----
Master ESP32: connected to this PC by USB, running master_radio_ota
              (ESP-NOW fixed to Wi-Fi channel 6).
Slave ESP32:  externally powered, running slave_radio_ota, connected to
              the offline router "M7026 Lab's ASUS Router" with static
              IP 192.168.50.200 (channel 6).
PC:           LAN cable to the offline router; internet Wi-Fi as usual.

PHASES
------
1. BASELINE  Reset the slave (W,-1), send world packets, and confirm
             telemetry arrives through the master. This proves the
             channel-6 ESP-NOW link works while the slave is on the
             router's Wi-Fi.
2. OTA       Upload firmware over the air by running
             "pio run -e slave_radio_ota -t upload" as a subprocess.
             Upload duration and result are recorded.
3. REBOOT    Fixed wait while the slave flashes and reboots.
4. VERIFY    Same radio check as BASELINE. Telemetry flowing again
             proves the OTA firmware booted and the link recovered.

RESULT
------
Per-phase rows are appended to a CSV under test/run_logs, and a final
JSON summary records the run status:

    PASS:     Baseline OK, OTA upload succeeded, verification OK.
    FAILED:   Any phase failed; the error field records the reason.

Quick check without touching the slave firmware (radio link only):

    python -u test/test_6_ota.py --skip-ota

Typical full run:

    python -u test/test_6_ota.py --port COM4
"""

import argparse
import csv
import ctypes
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial

PORT = "COM9"
BAUD = 115200
SEND_FREQUENCY_HZ = 50
BASELINE_DURATION_S = 30
VERIFY_DURATION_S = 30
TAIL_OBSERVATION_TIME_S = 1.0
REBOOT_WAIT_S = 20
OTA_TIMEOUT_S = 300

PIO = r"C:\Users\Zeyang Wu\.platformio\penv\Scripts\pio.exe"
OTA_ENV = "slave_radio_ota"


class WindowsSession:
    """Scoped timer resolution for Python 3.10 and idle-sleep prevention."""

    def __enter__(self):
        self.timer = False
        self.awake = False
        if sys.platform == "win32":
            self.timer = ctypes.windll.winmm.timeBeginPeriod(1) == 0
            self.awake = bool(
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            )
        return self

    def __exit__(self, *args):
        if self.timer:
            ctypes.windll.winmm.timeEndPeriod(1)
        if self.awake:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


def parse_telemetry(line):
    parts = line.strip().split(b",")
    if len(parts) != 7 or parts[0] != b"T":
        return None
    try:
        values = tuple(int(v) for v in parts[1:])
    except ValueError:
        return None
    if min(values[0], values[1], values[3], values[4], values[5]) < 0:
        return None
    if values[2] < -1:
        return None
    return values


class Receiver:
    """Counts telemetry per car between begin() and the end of a phase."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error = None
        self.collect = False
        self.cars = {}

    def begin(self):
        with self.lock:
            self.collect = True
            self.cars = {}

    def finish(self):
        with self.lock:
            self.collect = False
            return {car_id: car.copy() for car_id, car in self.cars.items()}

    def receive(self, values, now):
        car_id, seq, world, lost, events, gap = values
        with self.lock:
            if not self.collect or world == -1:
                return
            car = self.cars.get(car_id)
            if car is None:
                self.cars[car_id] = dict(
                    telemetry_received=1,
                    telemetry_lost=0,
                    telemetry_first_seq=seq,
                    telemetry_last_seq=seq,
                    last_rx=now,
                    max_silence_s=0.0,
                    world_packets_lost=lost,
                    world_gap_events=events,
                    max_world_gap=gap,
                    last_world_seq=world,
                )
                return
            if seq > car["telemetry_last_seq"]:
                car["telemetry_lost"] += seq - car["telemetry_last_seq"] - 1
                car["telemetry_last_seq"] = seq
                car["telemetry_received"] += 1
            car["max_silence_s"] = max(
                car["max_silence_s"], now - car["last_rx"]
            )
            car["last_rx"] = now
            car.update(
                world_packets_lost=lost,
                world_gap_events=events,
                max_world_gap=gap,
                last_world_seq=world,
            )

    def fail(self, message):
        with self.lock:
            self.error = message
        self.stop.set()


def receive_loop(ser, rx):
    pending = bytearray()
    try:
        while not rx.stop.is_set():
            pending.extend(ser.read(min(ser.in_waiting or 1, 4096)))
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                values = parse_telemetry(line)
                if values is not None:
                    rx.receive(values, time.perf_counter())
            if len(pending) > 4096:
                raise RuntimeError("Serial input exceeded 4096 bytes without a newline")
    except Exception as exc:
        rx.fail(f"Serial receiver failed: {exc}")


def send_world(ser, sequence):
    message = f"W,{sequence}\n".encode("ascii")
    if ser.write(message) != len(message):
        raise RuntimeError("Incomplete serial write")
    ser.flush()


def radio_phase(ser, rx, duration, frequency, label):
    """Reset the slave, send world packets, collect telemetry statistics."""
    print(f"\n--- {label}: resetting slave ---", flush=True)
    for _ in range(5):
        send_world(ser, -1)
        time.sleep(0.1)
    time.sleep(0.3)
    rx.begin()
    print(
        f"--- {label}: sending {round(duration * frequency)} world packets "
        f"at {frequency:g} Hz ---",
        flush=True,
    )
    period = 1.0 / frequency
    start = time.perf_counter()
    target = start
    sent = 0
    for sequence in range(round(duration * frequency)):
        if rx.stop.is_set():
            break
        remaining = target - time.perf_counter()
        if remaining > 0:
            time.sleep(min(remaining, 0.05))
        actual = time.perf_counter()
        send_world(ser, sequence)
        sent += 1
        target = max(target + period, actual + period)
    rx.stop.wait(TAIL_OBSERVATION_TIME_S)
    cars = rx.finish()
    now = time.perf_counter()
    for car_id in sorted(cars):
        car = cars[car_id]
        total = car["telemetry_received"] + car["telemetry_lost"]
        car["telemetry_loss_percent"] = round(
            100 * car["telemetry_lost"] / max(total, 1), 4
        )
        car["telemetry_age_s"] = round(now - car["last_rx"], 3)
        car["max_silence_s"] = round(
            max(car["max_silence_s"], now - car["last_rx"]), 3
        )
        print(
            f"  Slave {car_id}: {car['telemetry_received']} telemetry received, "
            f"loss {car['telemetry_loss_percent']:.3f}% | "
            f"world lost {car['world_packets_lost']}/{sent} | "
            f"max silence {car['max_silence_s']:.2f}s",
            flush=True,
        )
    if not cars:
        print("  No slaves observed during this phase.", flush=True)
    return dict(world_packets_sent=sent, cars=cars)


def ota_phase(pio, env):
    """Upload firmware over the air; record duration and outcome."""
    command = [pio, "run", "-e", env, "-t", "upload"]
    print(f"\n--- OTA: {' '.join(command)} ---", flush=True)
    start = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=OTA_TIMEOUT_S,
            cwd=Path(__file__).resolve().parent.parent,
        )
        duration = time.perf_counter() - start
        tail = "\n".join(result.stdout.strip().splitlines()[-5:])
        print(f"  pio exit code {result.returncode} after {duration:.1f}s", flush=True)
        print(f"  output tail:\n{tail}", flush=True)
        return dict(
            success=result.returncode == 0,
            duration_s=round(duration, 3),
            exit_code=result.returncode,
            output_tail=tail + ("\n" + result.stderr.strip() if result.stderr else ""),
        )
    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - start
        print(f"  OTA timed out after {duration:.1f}s", flush=True)
        return dict(
            success=False,
            duration_s=round(duration, 3),
            exit_code=None,
            output_tail=f"Timed out after {OTA_TIMEOUT_S}s",
        )


FIELDS = [
    "timestamp",
    "phase",
    "world_packets_sent",
    "car_id",
    "telemetry_received",
    "telemetry_lost",
    "telemetry_loss_percent",
    "telemetry_first_seq",
    "telemetry_last_seq",
    "telemetry_age_s",
    "max_silence_s",
    "world_packets_lost",
    "world_gap_events",
    "max_world_gap",
    "last_world_seq",
]


def write_rows(writer, log_file, phase, result):
    timestamp = datetime.now().isoformat(timespec="seconds")
    for car_id in sorted(result["cars"]):
        car = result["cars"][car_id]
        row = {field: car.get(field) for field in FIELDS}
        row.update(
            timestamp=timestamp,
            phase=phase,
            world_packets_sent=result["world_packets_sent"],
            car_id=car_id,
        )
        writer.writerow(row)
    log_file.flush()


def run(args):
    folder = Path(args.log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("radio_test_6_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    summary = dict(csv=str(path.resolve()), phases={})
    error = None
    print(
        f"TEST 6: OTA FIRMWARE UPDATE OVER LAB ROUTER\n"
        f"Port: {args.port}\nFrequency: {args.frequency:g} Hz\n"
        f"Baseline: {args.baseline:g}s | Verify: {args.verify:g}s | "
        f"Reboot wait: {args.reboot_wait:g}s\n"
        f"OTA: {'SKIP' if args.skip_ota else f'{PIO} run -e {OTA_ENV} -t upload'}\n"
        f"CSV: {path.resolve()}",
        flush=True,
    )
    rx = Receiver()
    ser = serial.Serial(
        port=None,
        baudrate=BAUD,
        timeout=0.05,
        write_timeout=2,
        rtscts=False,
        dsrdtr=False,
    )
    ser.port = args.port
    ser.rts = ser.dtr = False
    receiver = None
    with WindowsSession(), open(path, "w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=FIELDS)
        writer.writeheader()
        log_file.flush()
        try:
            ser.open()
            time.sleep(0.2)
            ser.reset_input_buffer()
            receiver = threading.Thread(
                target=receive_loop, args=(ser, rx), daemon=True
            )
            receiver.start()

            baseline = radio_phase(
                ser, rx, args.baseline, args.frequency, "BASELINE"
            )
            summary["phases"]["baseline"] = dict(
                world_packets_sent=baseline["world_packets_sent"],
                slaves_observed=sorted(baseline["cars"]),
            )
            write_rows(writer, log_file, "baseline", baseline)
            if not baseline["cars"]:
                raise RuntimeError(
                    "BASELINE failed: no telemetry from any slave. "
                    "Check master firmware (master_radio_ota), the COM port, "
                    "and that master and slave are on the same Wi-Fi channel."
                )

            if not args.skip_ota:
                ota = ota_phase(args.pio, OTA_ENV)
                summary["phases"]["ota"] = ota
                if not ota["success"]:
                    raise RuntimeError(
                        f"OTA upload failed (exit code {ota['exit_code']}). "
                        "Check that the slave is powered and ping 192.168.50.200 works."
                    )

                print(
                    f"\n--- REBOOT: waiting {args.reboot_wait:g}s for the slave "
                    f"to flash and reboot ---",
                    flush=True,
                )
                rx.stop.wait(args.reboot_wait)

                verify = radio_phase(
                    ser, rx, args.verify, args.frequency, "VERIFY"
                )
                summary["phases"]["verify"] = dict(
                    world_packets_sent=verify["world_packets_sent"],
                    slaves_observed=sorted(verify["cars"]),
                )
                write_rows(writer, log_file, "verify", verify)
                if not verify["cars"]:
                    raise RuntimeError(
                        "VERIFY failed: no telemetry after OTA. The slave may "
                        "not have rebooted correctly; check its USB serial "
                        "output or re-flash with the slave_radio_ota_usb env."
                    )
        except KeyboardInterrupt:
            error = "Stopped manually"
        except Exception as exc:
            error = str(exc)
        finally:
            rx.stop.set()
            if receiver:
                receiver.join(timeout=3)
            ser.close()
            if rx.error and not error:
                error = rx.error
            summary.update(
                status="FAILED" if error else "PASS",
                error=error,
                finished_local=datetime.now().isoformat(timespec="seconds"),
            )
            path.with_suffix(".summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
    if error:
        print(f"\nTEST 6 FAILED: {error}", flush=True)
        return 1
    print(
        f"\nTEST 6 PASS: OTA upload succeeded and telemetry recovered. "
        f"CSV: {path.resolve()}",
        flush=True,
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=PORT)
    parser.add_argument("--frequency", type=float, default=SEND_FREQUENCY_HZ)
    parser.add_argument("--baseline", type=float, default=BASELINE_DURATION_S)
    parser.add_argument("--verify", type=float, default=VERIFY_DURATION_S)
    parser.add_argument("--reboot-wait", type=float, default=REBOOT_WAIT_S)
    parser.add_argument("--pio", default=PIO)
    parser.add_argument(
        "--skip-ota",
        action="store_true",
        help="Radio link check only; no firmware upload",
    )
    parser.add_argument(
        "--log-dir", default=str(Path(__file__).resolve().parent / "run_logs")
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
