"""Eight-hour ESP-NOW loss test using the existing master and slave firmware.

Default: COM4, two slaves, 100 Hz, eight wall-clock hours. Each measurement lasts
up to 60 s. Before it, repeat W,-1 until both slaves confirm zeroed counters or
the bounded 2 s reset window expires. Missing confirmations never stop the run.

Confirmed minutes give a valid per-minute world_packets_lost and max_world_gap.
For an unconfirmed slave, only the change after its first telemetry observation
is reported; its per-minute maximum gap remains unknown. Missing telemetry gets
an explicit row. The script does not infer individual gap-event frequencies.

Usage:
  python -u test/test_5_loss_run.py
  python -u test/test_5_loss_run.py --duration-seconds 125 --slaves 1 2
"""

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
import time

import serial

from test_4_long_run import WindowsSession, parse_telemetry


FIELDS = [
    "timestamp", "elapsed_s", "minute", "measurement_s", "car_id", "status",
    "reset_confirmed", "reset_attempts", "tx_attempted", "tx_written",
    "tx_skipped", "tx_rate_hz", "host_serial_disconnects", "last_world_seq",
    "world_packets_lost", "world_loss_percent", "max_world_gap",
    "telemetry_observed", "telemetry_age_s", "max_telemetry_silence_s",
    "counter_rebases",
]


class SerialLink:
    """One owner for serial reads and writes; reconnect after port errors."""

    def __init__(self, port, baud):
        self.port, self.baud = port, baud
        self.ser = None
        self.next_retry = 0.0
        self.pending = bytearray()
        self.disconnects = 0
        self.last_error = None

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except (OSError, serial.SerialException):
                pass
        self.ser = None
        self.pending.clear()

    def disconnect(self, exc):
        self.last_error = str(exc)
        self.disconnects += 1
        self.close()
        self.next_retry = time.perf_counter() + 5

    def connect(self, now):
        if self.ser is not None or now < self.next_retry:
            return
        try:
            candidate = serial.Serial(port=None, baudrate=self.baud, timeout=0,
                                      write_timeout=0.2, rtscts=False, dsrdtr=False)
            candidate.port = self.port
            candidate.rts = candidate.dtr = False
            candidate.open()
            candidate.reset_input_buffer()
            self.ser = candidate
            self.pending.clear()
            print(f"Serial connected: {self.port}", flush=True)
        except (OSError, serial.SerialException) as exc:
            if "candidate" in locals():
                candidate.close()
            self.last_error = str(exc)
            self.next_retry = time.perf_counter() + 5

    def send(self, sequence):
        if self.ser is None:
            return False
        message = f"W,{sequence}\n".encode("ascii")
        try:
            if self.ser.write(message) != len(message):
                raise serial.SerialException("short serial write")
            return True
        except (OSError, serial.SerialException, serial.SerialTimeoutException) as exc:
            self.disconnect(exc)
            return False

    def read(self):
        if self.ser is None:
            return []
        try:
            available = self.ser.in_waiting
            if available:
                self.pending.extend(self.ser.read(min(available, 4096)))
            if len(self.pending) > 4096 and b"\n" not in self.pending:
                self.pending.clear()  # Drop an invalid oversized line.
            records = []
            while b"\n" in self.pending:
                line, _, rest = self.pending.partition(b"\n")
                self.pending = bytearray(rest)
                values = parse_telemetry(line)
                if values is not None:
                    records.append((time.perf_counter(), values))
            return records
        except (OSError, serial.SerialException) as exc:
            self.disconnect(exc)
            return []


class SlaveMinute:
    def __init__(self, confirmed, start):
        self.confirmed = confirmed
        self.start = start
        self.first = None
        self.last = None
        self.previous_rx = None
        self.count = 0
        self.max_silence = 0.0
        self.loss_added = 0
        self.rebases = 0

    def receive(self, now, values):
        _, _, world, lost, events, maximum = values
        if self.previous_rx is None:
            self.max_silence = max(0.0, now - self.start)
            # The confirmed reset is a known zero baseline. Count loss that
            # happened before the first periodic telemetry report as well.
            self.loss_added = lost if self.confirmed else 0
        else:
            self.max_silence = max(self.max_silence, now - self.previous_rx)
            if lost < self.last[3] or events < self.last[4] or world < self.last[2]:
                self.rebases += 1
                # Keep the original reset result, but mark this minute partial.
            else:
                self.loss_added += lost - self.last[3]
        if self.first is None:
            self.first = values
        self.last = values
        self.previous_rx = now
        self.count += 1

    def row(self, now, sent):
        if self.last is None:
            return dict(status="NO_TELEMETRY", reset_confirmed=int(self.confirmed),
                        telemetry_observed=0)
        age = max(0.0, now - self.previous_rx)
        silence = max(self.max_silence, age)
        if self.rebases:
            status = "COUNTER_REBASE"
        elif not self.confirmed:
            status = "RESET_UNCONFIRMED"
        elif age > 2:
            status = "STALE"
        else:
            status = "OK"
        return dict(
            status=status, reset_confirmed=int(self.confirmed),
            last_world_seq=self.last[2],
            world_packets_lost=self.loss_added,
            world_loss_percent=round(100 * self.loss_added / sent, 4) if sent else "",
            max_world_gap=self.last[5] if self.confirmed and not self.rebases else "",
            telemetry_observed=self.count,
            telemetry_age_s=round(age, 3),
            max_telemetry_silence_s=round(silence, 3),
            counter_rebases=self.rebases,
        )


def wait_and_poll(link, seconds, consumer):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        now = time.perf_counter()
        link.connect(now)
        for received, values in link.read():
            consumer(received, values)
        time.sleep(min(0.005, max(0, end - now)))


def run_reset(link, expected, deadline):
    confirmed = set()
    attempts = 0

    def check(_, values):
        car, _, world, lost, events, gap = values
        if world == -1 and lost == events == gap == 0:
            confirmed.add(car)

    while time.perf_counter() < deadline and not expected.issubset(confirmed):
        link.connect(time.perf_counter())
        attempts += int(link.send(-1))
        wait_and_poll(link, min(0.1, max(0, deadline - time.perf_counter())), check)
    # Let USB/ESP-NOW replies already in flight arrive before measurement begins.
    wait_and_poll(link, min(0.15, max(0, deadline - time.perf_counter())), check)
    return confirmed, attempts


def measure(link, expected, confirmed, start, end, hz):
    trackers = {car: SlaveMinute(car in confirmed, start) for car in expected}
    period = 1.0 / hz
    next_slot = 0
    attempted = written = skipped = 0
    disconnects_before = link.disconnects

    def accept(now, values):
        car = values[0]
        if car not in trackers:
            trackers[car] = SlaveMinute(car in confirmed, start)
        trackers[car].receive(now, values)

    while time.perf_counter() < end:
        now = time.perf_counter()
        link.connect(now)
        for received, values in link.read():
            accept(received, values)
        due_slot = int((now - start) / period)
        if due_slot >= next_slot:
            skipped += due_slot - next_slot
            next_slot = due_slot + 1
            attempted += 1
            written += int(link.send(due_slot))
        else:
            remaining = start + next_slot * period - now
            if remaining > 0.003:
                time.sleep(min(remaining - 0.002, 0.005))
    wait_and_poll(link, 0.1, accept)
    finish = time.perf_counter()
    duration = end - start
    expected_slots = math.ceil(duration * hz - 1e-9)
    skipped += max(0, expected_slots - next_slot)
    return trackers, dict(
        measurement_s=round(duration, 3), tx_attempted=attempted,
        tx_written=written, tx_skipped=skipped,
        tx_rate_hz=round(written / duration, 3),
        host_serial_disconnects=link.disconnects - disconnects_before,
    ), finish


def plot_results(csv_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    ids = sorted({int(row["car_id"]) for row in rows})
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
    for car in ids:
        device = [row for row in rows if int(row["car_id"]) == car]
        x = [float(row["elapsed_s"]) / 3600 for row in device]
        trusted_loss = [float(row["world_loss_percent"])
                        if row["status"] == "OK" else math.nan for row in device]
        partial_x = [hour for hour, row in zip(x, device)
                     if row["status"] in ("RESET_UNCONFIRMED", "COUNTER_REBASE", "STALE")
                     and row["world_loss_percent"]]
        partial_loss = [float(row["world_loss_percent"]) for row in device
                        if row["status"] in ("RESET_UNCONFIRMED", "COUNTER_REBASE", "STALE")
                        and row["world_loss_percent"]]
        gaps = [float(row["max_world_gap"]) if row["status"] == "OK"
                and row["max_world_gap"] else math.nan for row in device]
        line, = axes[0].plot(x, trusted_loss, linewidth=1, label=f"Slave {car}")
        axes[0].scatter(partial_x, partial_loss, marker="x", s=12,
                        color=line.get_color(), alpha=0.7)
        axes[1].plot(x, gaps, linewidth=1, color=line.get_color(), label=f"Slave {car}")
    host = [row for row in rows if int(row["car_id"]) == ids[0]]
    axes[2].plot([float(row["elapsed_s"]) / 3600 for row in host],
                 [float(row["tx_rate_hz"]) for row in host], color="#333333", linewidth=1)
    axes[0].set_ylabel("World loss (%)")
    axes[1].set_ylabel("Max consecutive loss")
    axes[2].set_ylabel("Actual TX rate (Hz)")
    axes[2].set_xlabel("Elapsed time (hours)")
    for ax in axes:
        ax.grid(alpha=0.25)
    axes[0].legend()
    axes[1].legend()
    fig.suptitle("Test 5: loss by measurement minute (× = partial estimate; gaps = unavailable)")
    fig.tight_layout()
    output = csv_path.with_suffix(".png")
    fig.savefig(output, dpi=150)
    plt.close(fig)
    return output


def plot_gap_distribution(csv_path, hz=100):
    """Plot valid full-minute maxima, including an occurrence and tail view."""
    from analyze_radio_test import plot_gap_distribution as draw_distribution

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["interval_elapsed_s"] = row["measurement_s"]
    return draw_distribution(csv_path, rows, expected_hz=hz)


def run(args):
    duration = args.duration_seconds if args.duration_seconds is not None else args.hours * 3600
    if (duration <= 0 or args.hz <= 0 or args.reset_seconds < 0
            or args.minute_seconds <= 0 or not args.slaves
            or len(args.slaves) != len(set(args.slaves))):
        raise ValueError("duration, frequency, and measurement length must be positive")
    folder = Path(args.log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stem = "radio_test_5_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = folder / (stem + ".csv")
    link = SerialLink(args.port, args.baud)
    expected = set(args.slaves)
    start = time.perf_counter()
    end = start + duration
    minute = 0
    all_rows = 0
    with WindowsSession(), csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()
        print(f"Test 5: {duration / 3600:.3f} h, {args.hz:g} Hz, "
              f"slaves {sorted(expected)}, CSV {csv_path.resolve()}", flush=True)
        try:
            while time.perf_counter() < end:
                minute += 1
                reset_deadline = min(end, time.perf_counter() + args.reset_seconds)
                confirmed, attempts = run_reset(link, expected, reset_deadline)
                measurement_start = time.perf_counter()
                if measurement_start >= end:
                    break
                measurement_end = min(end, measurement_start + args.minute_seconds)
                trackers, host, finish = measure(
                    link, expected, confirmed, measurement_start, measurement_end, args.hz
                )
                timestamp = datetime.now().isoformat(timespec="seconds")
                for car in sorted(trackers):
                    row = dict(timestamp=timestamp, elapsed_s=round(finish - start, 3),
                               minute=minute, car_id=car, reset_attempts=attempts,
                               **host, **trackers[car].row(finish, host["tx_written"]))
                    writer.writerow(row)
                    all_rows += 1
                    print(f"  minute {minute} slave {car}: {row['status']}, "
                          f"lost {row.get('world_packets_lost', '')}, "
                          f"max gap {row.get('max_world_gap', '')}, "
                          f"telemetry {row['telemetry_observed']}")
                handle.flush()
                print(f"  TX: {host['tx_written']} written, {host['tx_skipped']} skipped, "
                      f"{host['tx_rate_hz']:.1f} Hz; reset confirmed {sorted(confirmed)}",
                      flush=True)
        except KeyboardInterrupt:
            print("Stopped manually; partial results retained.", flush=True)
        finally:
            link.close()
            status = "COMPLETE" if time.perf_counter() >= end else "INTERRUPTED"
            summary = dict(status=status, csv=str(csv_path.resolve()),
                           duration_requested_s=duration, elapsed_s=round(time.perf_counter()-start, 3),
                           minutes=minute, rows=all_rows, serial_disconnects=link.disconnects,
                           last_serial_error=link.last_error)
            csv_path.with_suffix(".summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
    try:
        plot = plot_results(csv_path)
        print(f"Plot: {plot.resolve()}", flush=True)
        distribution, counts = plot_gap_distribution(csv_path, hz=args.hz)
        print(f"Max-gap distribution: {distribution.resolve()}", flush=True)
        for car, (minutes, at_least_ten) in counts.items():
            print(f"  slave {car}: {at_least_ten}/{minutes} full minutes had gap >=10",
                  flush=True)
    except (ImportError, ValueError) as exc:
        print(f"Plot unavailable: {exc}", flush=True)
    print(f"Result: {csv_path.resolve()}", flush=True)
    return 0 if status == "COMPLETE" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--hours", type=float, default=8)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--hz", type=float, default=100)
    parser.add_argument("--slaves", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--reset-seconds", type=float, default=2)
    parser.add_argument("--minute-seconds", type=float, default=60)
    parser.add_argument("--log-dir", default=str(Path(__file__).resolve().parent / "run_logs"))
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
