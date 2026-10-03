"""Test 7: ten 60-second ESP-NOW intervals with an OTA-enabled slave.

The master is on COM4 and relays W,<sequence> at 100 Hz. The --mode argument
labels the CSV; it does not reconfigure the master firmware. Before each interval,
send W,-1 up to 20 times (100 ms apart) until Slave 2 reports reset counters.
The slave firmware remains connected to Wi-Fi and services ArduinoOTA.handle().
This script does not start an OTA upload or change either firmware.

One CSV row is written per interval, including intervals with missing telemetry.
Telemetry packet loss is inferred from gaps in telemetry_seq between observations;
loss before the first observed sequence or after the last cannot be known without
a confirmed sequence baseline. A maximum world gap is trusted only when reset was
confirmed and the slave counters stayed monotonic. The first world packet after
reset establishes the slave's sequence baseline and is not counted as lost.

Run: python -u test/test_7_ota_unicast.py
Short check: python -u test/test_7_ota_unicast.py --minutes 1 --interval-seconds 10
"""

import argparse
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path

from test_4_long_run import WindowsSession
from test_5_loss_run import SerialLink

FIELDS = [
    "timestamp",
    "interval",
    "mode",
    "car_id",
    "status",
    "reset_confirmed",
    "reset_attempts",
    "measurement_s",
    "world_packets_attempted",
    "world_packets_written",
    "world_packets_skipped",
    "world_tx_rate_hz",
    "last_world_seq",
    "world_packets_lost",
    "world_loss_percent",
    "max_world_gap",
    "world_gap_events",
    "telemetry_received",
    "telemetry_packets_lost",
    "telemetry_loss_percent",
    "telemetry_first_seq",
    "telemetry_last_seq",
    "telemetry_age_s",
    "max_telemetry_silence_s",
    "counter_rebases",
    "serial_disconnects",
]


def reset_slave(link, car_id, attempts, spacing):
    """Return (confirmed, attempts used); failure never stops the run."""
    confirmed = False
    attempts_used = 0
    for _ in range(attempts):
        attempts_used += 1
        link.connect(time.perf_counter())
        link.send(-1)
        deadline = time.perf_counter() + spacing
        while time.perf_counter() < deadline:
            for _, values in link.read():
                ident, _, world, lost, events, gap = values
                if ident == car_id and world == -1 and lost == events == gap == 0:
                    confirmed = True
            if confirmed:
                break
            time.sleep(min(0.005, max(0, deadline - time.perf_counter())))
        if confirmed:
            break
    # Drain the last acknowledgment before measurement, without sending more resets.
    settle_end = time.perf_counter() + 0.15
    while time.perf_counter() < settle_end:
        link.read()
        time.sleep(0.005)
    return confirmed, attempts_used


class IntervalState:
    def __init__(self, car_id, reset_confirmed, start):
        self.car_id = car_id
        self.reset_confirmed = reset_confirmed
        self.start = start
        self.first_seq = None
        self.last_seq = None
        self.last_world = None
        self.last_lost = None
        self.last_events = None
        self.last_gap = None
        self.last_rx = None
        self.received = 0
        self.telemetry_lost = 0
        self.world_lost = 0
        self.max_silence = 0.0
        self.rebases = 0

    def receive(self, now, values):
        car, seq, world, lost, events, gap = values
        if car != self.car_id or world == -1:
            return
        if self.last_seq is not None and seq == self.last_seq:
            return
        if self.last_rx is None:
            self.max_silence = max(0, now - self.start)
            self.first_seq = seq
            self.world_lost = lost if self.reset_confirmed else 0
        else:
            self.max_silence = max(self.max_silence, now - self.last_rx)
            if (
                seq < self.last_seq
                or world < self.last_world
                or lost < self.last_lost
                or events < self.last_events
                or gap < self.last_gap
            ):
                self.rebases += 1
            else:
                self.telemetry_lost += max(0, seq - self.last_seq - 1)
                self.world_lost += lost - self.last_lost
        self.received += 1
        self.last_seq, self.last_world = seq, world
        self.last_lost, self.last_events, self.last_gap = lost, events, gap
        self.last_rx = now

    def row(self, finish, sent):
        if self.received == 0:
            return dict(
                status="NO_TELEMETRY",
                reset_confirmed=int(self.reset_confirmed),
                telemetry_received=0,
                telemetry_packets_lost="",
            )
        age = max(0, finish - self.last_rx)
        if self.rebases:
            status = "COUNTER_REBASE"
        elif not self.reset_confirmed:
            status = "RESET_UNCONFIRMED"
        elif age > 2:
            status = "STALE"
        else:
            status = "OK"
        expected_telemetry = self.received + self.telemetry_lost
        return dict(
            status=status,
            reset_confirmed=int(self.reset_confirmed),
            last_world_seq=self.last_world,
            world_packets_lost=self.world_lost,
            world_loss_percent=round(100 * self.world_lost / sent, 4) if sent else "",
            max_world_gap=(
                self.last_gap if self.reset_confirmed and not self.rebases else ""
            ),
            world_gap_events=(
                self.last_events if self.reset_confirmed and not self.rebases else ""
            ),
            telemetry_received=self.received,
            telemetry_packets_lost=self.telemetry_lost,
            telemetry_loss_percent=round(
                100 * self.telemetry_lost / expected_telemetry, 4
            ),
            telemetry_first_seq=self.first_seq,
            telemetry_last_seq=self.last_seq,
            telemetry_age_s=round(age, 3),
            max_telemetry_silence_s=round(max(self.max_silence, age), 3),
            counter_rebases=self.rebases,
        )


def measure(link, car_id, confirmed, seconds, hz):
    start = time.perf_counter()
    end = start + seconds
    state = IntervalState(car_id, confirmed, start)
    period = 1 / hz
    next_slot = attempted = written = skipped = 0
    disconnects_before = link.disconnects
    while time.perf_counter() < end:
        now = time.perf_counter()
        link.connect(now)
        for received, values in link.read():
            state.receive(received, values)
        slot = int((now - start) / period)
        if slot >= next_slot:
            skipped += slot - next_slot
            next_slot = slot + 1
            attempted += 1
            written += int(link.send(slot))
        else:
            remaining = start + next_slot * period - now
            if remaining > 0.003:
                time.sleep(min(remaining - 0.002, 0.005))
    skipped += max(0, math.ceil(seconds * hz - 1e-9) - next_slot)
    tail_end = time.perf_counter() + 0.1
    while time.perf_counter() < tail_end:
        for received, values in link.read():
            state.receive(received, values)
        time.sleep(0.005)
    finish = time.perf_counter()
    return dict(
        measurement_s=round(seconds, 3),
        world_packets_attempted=attempted,
        world_packets_written=written,
        world_packets_skipped=skipped,
        world_tx_rate_hz=round(written / seconds, 3),
        serial_disconnects=link.disconnects - disconnects_before,
        **state.row(finish, written),
    )


def run(args):
    if (
        args.minutes <= 0
        or args.interval_seconds <= 0
        or args.hz <= 0
        or args.reset_attempts <= 0
        or args.reset_spacing <= 0
    ):
        raise ValueError(
            "minutes, interval, frequency, and reset settings must be positive"
        )
    folder = Path(args.log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (
        "radio_test_7_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
    )
    link = SerialLink(args.port, args.baud)
    completed = 0
    print(
        f"Test 7: {args.minutes} x {args.interval_seconds:g}s at {args.hz:g} Hz, "
        f"Slave {args.slave}, {args.mode}, port {args.port}\nCSV: {path.resolve()}",
        flush=True,
    )
    with WindowsSession(), path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()
        try:
            for interval in range(1, args.minutes + 1):
                confirmed, attempts = reset_slave(
                    link, args.slave, args.reset_attempts, args.reset_spacing
                )
                row = measure(
                    link, args.slave, confirmed, args.interval_seconds, args.hz
                )
                row.update(
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                    interval=interval,
                    mode=args.mode,
                    car_id=args.slave,
                    reset_attempts=attempts,
                )
                writer.writerow(row)
                handle.flush()
                completed += 1
                print(
                    f"{interval}/{args.minutes}: {row['status']} | "
                    f"world lost {row.get('world_packets_lost', '')}/"
                    f"{row['world_packets_written']} | max gap "
                    f"{row.get('max_world_gap', '')} | telemetry lost "
                    f"{row.get('telemetry_packets_lost', '')} | "
                    f"reset attempts {attempts}",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("Interrupted; completed rows retained.", flush=True)
        finally:
            link.close()
            summary = dict(
                status="COMPLETE" if completed == args.minutes else "INTERRUPTED",
                csv=str(path.resolve()),
                mode=args.mode,
                car_id=args.slave,
                intervals_requested=args.minutes,
                intervals_completed=completed,
                serial_disconnects=link.disconnects,
                last_serial_error=link.last_error,
            )
            path.with_suffix(".summary.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
    print(f"Result: {path.resolve()}", flush=True)
    return 0 if completed == args.minutes else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--slave", type=int, default=2)
    parser.add_argument(
        "--mode",
        choices=("unicast", "broadcast"),
        default="unicast",
        help="CSV label for the master firmware currently flashed",
    )
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--hz", type=float, default=100)
    parser.add_argument("--reset-attempts", type=int, default=40)
    parser.add_argument("--reset-spacing", type=float, default=0.1)
    parser.add_argument(
        "--log-dir", default=str(Path(__file__).resolve().parent / "run_logs")
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
