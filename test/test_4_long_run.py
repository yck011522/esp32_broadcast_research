"""Long ESP-NOW test: Test 3's drained writes and sleeping TX loop, bounded stats.

Default: COM4, 100 Hz, four wall-clock hours, slave reset every measured minute.
Each interval has a 0.1 s receive tail and about 0.5 s of reset overhead.
Use --reset-interval 0 for continuous operation. Example short check:
  python -u test/test_4_long_run.py --duration-seconds 180 --log-interval 30

CSV and a final JSON run summary are saved under test/run_logs.
Slave IDs are discovered automatically. Only slaves with telemetry during the
measurement (including its receive tail) get a row. Reset-only sightings do not.
FAIL_TO_RESET rows contain no untrusted loss statistics. No firmware changes needed.
World loss is the slave's reported internal sequence gaps (same as Test 3),
not proof of every packet delivered: the slave does not count initial/trailing
loss. Sequence lag and telemetry age are logged separately to expose outages.

PER-SLAVE CSV STATUS
-------------------
OK
    Reset confirmed; valid telemetry updated within the last 2 seconds.
    Statistics are included.
STALE
    Reset confirmed, but no valid telemetry update for over 2 seconds.
    The latest valid statistics are retained and may be incomplete. Check
    telemetry_age_s and world_sequence_lag when interpreting these results.
FAIL_TO_RESET
    Telemetry appeared during measurement without a reset confirmation observed
    before measurement began. Loss and telemetry sequence statistics are blank
    for this slave for the entire interval; the other slaves continue normally.
    This means confirmation was missing, not proof that the device failed to reset.
COUNTER_RESET
    A telemetry sequence, world sequence, or cumulative loss counter moved
    backwards during measurement. Statistics from before the inconsistency are
    retained; subsequent updates from this slave are ignored for this interval.
    Other slaves continue normally.

A slave never observed during measurement has no CSV row, rather than a
NO_TELEMETRY status. Reset-phase sightings alone do not create a row.
Statuses are reassessed independently after each interval's reset. None of
these per-slave statuses aborts the whole test.

RUN SUMMARY STATUS
------------------
The companion .summary.json describes the entire run:
    COMPLETE: The configured run finished without a run-level error. This does
              not imply every slave was present or every interval was valid.
    FAILED:   A run-level error or manual interruption stopped the test; the
              error field records the reason.
"""

import argparse
import csv
import ctypes
import json
from datetime import datetime
from pathlib import Path
import sys
import threading
import time

import serial

PORT = "COM4"
BAUD = 115200
SEND_FREQUENCY_HZ = 100
SEND_DURATION_S = 4 * 60 * 60
TAIL_OBSERVATION_TIME_S = 1.0
LOG_INTERVAL_S = 60.0


class WindowsSession:
    """Scoped timer resolution for Python 3.10 and idle-sleep prevention.

    Both requests are released when the process exits; no power plan is edited.
    """

    def __enter__(self):
        self.timer = False
        self.awake = False
        if sys.platform == "win32":
            self.timer = ctypes.windll.winmm.timeBeginPeriod(1) == 0
            self.awake = bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000001))
            if not self.timer or not self.awake:
                print("WARNING: could not enable timer precision or prevent idle sleep.")
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
    car_id, seq, world, lost, events, gap = values
    if min(car_id, seq, lost, events, gap) < 0 or world < -1:
        return None
    return values


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.logger_stop = threading.Event()
        self.error = None
        self.collect = False
        self.reset_seen = set()
        self.stats = {}
        self.sent = 0
        self.max_lateness_ms = 0.0
        self.max_write_ms = 0.0
        self.max_tx_interval_ms = 0.0
        self.schedule_rebases = 0
        self.start = None
        self.run_start = None
        self.interval = 0
        self.completed_packets = 0

    def prepare_interval(self):
        with self.lock:
            self.collect = False
            self.reset_seen.clear()
            self.stats.clear()
            self.sent = 0
            self.max_lateness_ms = 0.0
            self.max_write_ms = 0.0
            self.max_tx_interval_ms = 0.0
            self.schedule_rebases = 0
            self.start = None
            self.interval += 1

    def fail(self, message):
        with self.lock:
            self.error = message
        self.stop.set()

    def receive(self, values, now):
        car_id, seq, world, lost, events, gap = values
        with self.lock:
            if not self.collect:
                if world == -1 and lost == events == gap == 0:
                    self.reset_seen.add(car_id)
                return
            if car_id not in self.reset_seen:
                # Presence is useful even when this interval's counters cannot
                # be trusted. Quarantine this device until the next interval.
                self.stats[car_id] = dict(status="FAIL_TO_RESET", last_rx=now)
                return
            car = self.stats.get(car_id)
            if car is None:
                car = self.stats[car_id] = dict(
                    status="OK",
                    telemetry_received=0, telemetry_lost=0,
                    telemetry_duplicates=0, telemetry_first_seq=seq,
                    telemetry_last_seq=seq, last_world_seq=world,
                    world_packets_lost=lost, world_gap_events=events,
                    max_world_gap=gap, last_rx=now,
                )
            elif car["status"] == "COUNTER_RESET":
                # Preserve the trusted prefix rather than mix counter epochs.
                return
            elif (seq < car["telemetry_last_seq"] or world < car["last_world_seq"]
                  or lost < car["world_packets_lost"] or events < car["world_gap_events"]
                  or gap < car["max_world_gap"]):
                car["status"] = "COUNTER_RESET"
                return
            elif seq == car["telemetry_last_seq"]:
                car["telemetry_duplicates"] += 1
                return
            else:
                car["telemetry_lost"] += seq - car["telemetry_last_seq"] - 1
            car.update(last_world_seq=world, world_packets_lost=lost,
                       world_gap_events=events, max_world_gap=gap,
                       telemetry_last_seq=seq, last_rx=now)
            car["telemetry_received"] += 1

    def snapshot(self):
        with self.lock:
            return ({key: value.copy() for key, value in self.stats.items()},
                    dict(world_packets_sent=self.sent,
                         max_tx_lateness_ms=self.max_lateness_ms,
                         max_write_ms=self.max_write_ms,
                         max_tx_interval_ms=self.max_tx_interval_ms,
                         schedule_rebases=self.schedule_rebases))


def receive_loop(ser, state):
    # Keep partial lines across read timeouts; readline() can return fragments.
    pending = bytearray()
    try:
        while not state.stop.is_set():
            pending.extend(ser.read(min(ser.in_waiting or 1, 4096)))
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                values = parse_telemetry(line)
                if values is not None:
                    state.receive(values, time.perf_counter())
            if len(pending) > 4096:
                raise RuntimeError("Serial input exceeded 4096 bytes without a newline")
    except Exception as exc:
        state.fail(f"Serial receiver failed: {exc}")


def send_world(ser, sequence):
    message = f"W,{sequence}\n".encode("ascii")
    if ser.write(message) != len(message):
        raise RuntimeError("Incomplete serial write")
    # Match the known-good Test 3. This waits for the driver output to drain;
    # it is not an ESP-NOW delivery acknowledgement.
    ser.flush()


def next_deadline(previous_target, actual_send, period):
    # Keep phase for small jitter, but never replay a backlog as a packet burst.
    if actual_send - previous_target >= period:
        return actual_send + period, True
    return previous_target + period, False


def transmit_loop(ser, state, duration, frequency, tail=TAIL_OBSERVATION_TIME_S):
    period = 1.0 / frequency
    target = state.start
    end = state.start + duration
    previous_actual = None
    for sequence in range(round(duration * frequency)):
        while not state.stop.is_set():
            remaining = target - time.perf_counter()
            if remaining <= 0:
                break
            # Same sleeping strategy as Test 3; no sleep(0) polling loop.
            time.sleep(min(remaining, 0.05))
        actual = time.perf_counter()
        if state.stop.is_set() or actual >= end:
            break
        send_world(ser, sequence)
        finished = time.perf_counter()
        following, rebased = next_deadline(target, actual, period)
        with state.lock:
            state.sent += 1
            state.max_lateness_ms = max(state.max_lateness_ms, (actual - target) * 1000)
            state.max_write_ms = max(state.max_write_ms, (finished - actual) * 1000)
            if previous_actual is not None:
                state.max_tx_interval_ms = max(state.max_tx_interval_ms,
                                               (actual - previous_actual) * 1000)
            state.schedule_rebases += int(rebased)
        previous_actual = actual
        target = following
    # Include a full nominal sending duration even if the final packet is early.
    if not state.stop.is_set():
        state.stop.wait(max(0, end - time.perf_counter()) + tail)


FIELDS = ["timestamp", "elapsed_s", "interval", "interval_elapsed_s",
          "total_world_packets_sent", "car_id", "status", "world_packets_sent",
          "last_world_seq", "world_packets_lost", "world_loss_percent",
          "interval_world_loss_percent", "world_gap_events", "max_world_gap",
          "telemetry_received", "telemetry_lost", "telemetry_last_seq",
          "telemetry_loss_percent", "telemetry_duplicates", "telemetry_age_s",
          "world_sequence_lag", "max_tx_lateness_ms", "max_write_ms",
          "max_tx_interval_ms", "schedule_rebases"]


class Reporter:
    def __init__(self, state, writer, log_file):
        self.state, self.writer, self.log_file = state, writer, log_file
        self.previous = {}

    def write(self):
        snapshot, tx = self.state.snapshot()
        now = time.perf_counter()
        timestamp = datetime.now().isoformat(timespec="seconds")
        interval_elapsed = now - self.state.start
        elapsed = now - (self.state.run_start or self.state.start)
        print(f"\n[{timestamp}] {elapsed / 3600:.3f} h elapsed | interval {self.state.interval}")
        if not snapshot:
            print("  No slaves observed during this measurement; no device rows written.")
        for car_id in sorted(snapshot):
            row = dict(timestamp=timestamp, elapsed_s=round(elapsed, 3),
                       interval=self.state.interval, interval_elapsed_s=round(interval_elapsed, 3),
                       total_world_packets_sent=self.state.completed_packets + tx['world_packets_sent'],
                       car_id=car_id, **tx)
            car = snapshot[car_id]
            if car["status"] == "FAIL_TO_RESET":
                row.update(status="FAIL_TO_RESET", telemetry_age_s=round(now - car["last_rx"], 3))
                print(f"  Slave {car_id}: FAIL_TO_RESET (excluded from interval statistics)")
            else:
                age = now - car["last_rx"]
                loss = 100 * car["world_packets_lost"] / max(tx["world_packets_sent"], 1)
                total = car["telemetry_received"] + car["telemetry_lost"]
                telemetry_loss = 100 * car["telemetry_lost"] / max(total, 1)
                old_sent, old_lost = self.previous.get(car_id, (0, 0))
                interval_loss = 100 * (car["world_packets_lost"] - old_lost) / max(
                    tx["world_packets_sent"] - old_sent, 1)
                self.previous[car_id] = tx["world_packets_sent"], car["world_packets_lost"]
                row.update({k: v for k, v in car.items() if k in FIELDS})
                row.update(status=car["status"] if car["status"] != "OK" else ("STALE" if age > 2 else "OK"),
                           world_loss_percent=round(loss, 4),
                           interval_world_loss_percent=round(interval_loss, 4),
                           telemetry_loss_percent=round(telemetry_loss, 4),
                           telemetry_age_s=round(age, 3),
                           world_sequence_lag=tx["world_packets_sent"] - 1 - car["last_world_seq"])
                print(f"  Slave {car_id}: {row['status']} | world loss {loss:.3f}% "
                      f"(since last log {interval_loss:.3f}%) | max gap {car['max_world_gap']} | "
                      f"telemetry loss {telemetry_loss:.3f}% | age {age:.2f}s")
            self.writer.writerow(row)
        self.log_file.flush()
        print(f"  Interval TX packets: {tx['world_packets_sent']:,} | "
              f"run total: {self.state.completed_packets + tx['world_packets_sent']:,} | "
              f"max lateness: {tx['max_tx_lateness_ms']:.3f} ms | "
              f"max write: {tx['max_write_ms']:.3f} ms | "
              f"max TX interval: {tx['max_tx_interval_ms']:.3f} ms | "
              f"schedule rebases: {tx['schedule_rebases']}", flush=True)

    def loop(self, interval):
        try:
            while not self.state.logger_stop.wait(interval):
                self.write()
        except Exception as exc:
            self.state.fail(f"Logger failed: {exc}")


def run(args):
    duration = args.duration_seconds if args.duration_seconds is not None else args.hours * 3600
    if duration <= 0 or args.frequency <= 0 or args.log_interval <= 0 or args.reset_interval < 0:
        raise ValueError("Duration, frequency and log interval must be positive")
    folder = Path(args.log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("radio_test_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    state = State()
    print(f"ESP-NOW LONG-DURATION TEST\nPort: {args.port}\n"
          f"Frequency: {args.frequency:g} Hz\nDuration: {duration / 3600:.4f} hours\n"
          f"Packet upper bound (before reset overhead): {round(duration * args.frequency):,}\n"
          f"Reset interval: {args.reset_interval:g} measured seconds (0 = continuous)\n"
          f"CSV: {path.resolve()}\n"
          f"Python: {sys.version.split()[0]}", flush=True)
    ser = serial.Serial(port=None, baudrate=BAUD, timeout=0.05, write_timeout=2,
                        rtscts=False, dsrdtr=False)
    ser.port = args.port
    ser.rts = ser.dtr = False
    receiver = logger = None
    with WindowsSession(), open(path, "w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=FIELDS)
        writer.writeheader()
        log_file.flush()
        reporter = None
        interval_logged = True
        try:
            ser.open()
            time.sleep(0.2)
            ser.reset_input_buffer()
            receiver = threading.Thread(target=receive_loop, args=(ser, state), daemon=True)
            receiver.start()
            while not state.stop.is_set():
                # Avoid starting another reset when the four-hour budget is exhausted.
                if state.run_start is not None and time.perf_counter() >= state.run_start + duration - 0.7:
                    break
                state.prepare_interval()
                print(f"Resetting slaves for interval {state.interval}...", flush=True)
                for _ in range(5):
                    send_world(ser, -1)
                    time.sleep(0.1)
                with state.lock:
                    print(f"Reset confirmed by slaves: {sorted(state.reset_seen)}", flush=True)
                    if not state.reset_seen:
                        print("No reset confirmations; continuing. Any observed slave will be "
                              "marked FAIL_TO_RESET for this interval.", flush=True)
                    state.collect = True
                state.start = time.perf_counter()
                if state.run_start is None:
                    state.run_start = state.start
                remaining = state.run_start + duration - state.start
                tail = 0.1 if args.reset_interval else TAIL_OBSERVATION_TIME_S
                interval_duration = min(args.reset_interval, max(0, remaining - tail)) if args.reset_interval else duration
                reporter = Reporter(state, writer, log_file)
                interval_logged = False
                if not args.reset_interval:
                    logger = threading.Thread(target=reporter.loop, args=(args.log_interval,), daemon=True)
                    logger.start()
                # TX on the main thread, just as in Test 3. Errors propagate here.
                transmit_loop(ser, state, interval_duration, args.frequency, tail)
                if logger:
                    state.logger_stop.set()
                    logger.join()
                    logger = None
                with state.lock:
                    state.collect = False
                reporter.write()  # Save the interval's maximum BEFORE resetting it.
                interval_logged = True
                state.completed_packets += state.sent
                if not args.reset_interval:
                    break
        except KeyboardInterrupt:
            state.fail("Stopped manually")
        except Exception as exc:
            state.fail(str(exc))
        finally:
            state.stop.set()
            state.logger_stop.set()
            if logger:
                logger.join()
            if receiver:
                receiver.join(timeout=3)
            ser.close()
            if state.start is not None and not interval_logged:
                reporter.write()
            summary = dict(status="FAILED" if state.error else "COMPLETE",
                           error=state.error, csv=str(path.resolve()),
                           finished_local=datetime.now().isoformat(timespec="seconds"),
                           intervals_started=state.interval,
                           total_world_packets_sent=state.completed_packets +
                           (state.sent if not interval_logged else 0))
            path.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if state.error:
            print(f"TEST FAILED: {state.error}", flush=True)
            return 1
        print(f"TEST COMPLETE: {state.completed_packets:,} packets sent over "
              f"{state.interval} intervals. CSV: {path.resolve()}", flush=True)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=PORT)
    parser.add_argument("--hours", type=float, default=SEND_DURATION_S / 3600)
    parser.add_argument("--duration-seconds", type=float, help="Short validation; overrides --hours")
    parser.add_argument("--frequency", type=float, default=SEND_FREQUENCY_HZ)
    parser.add_argument("--log-interval", type=float, default=LOG_INTERVAL_S)
    parser.add_argument("--reset-interval", type=float, default=60,
                        help="Measured seconds per reset interval; 0 disables periodic resets")
    parser.add_argument("--log-dir", default=str(Path(__file__).resolve().parent / "run_logs"))
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
