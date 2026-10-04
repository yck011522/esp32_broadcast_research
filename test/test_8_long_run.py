"""Test 8: multi-slave ESP-NOW long run, using Test 7's fixed world payload.

Default: COM4, slaves 1 and 2, 50 Hz, four wall-clock hours, measurements up
to 60 seconds followed by a reset before the next measurement. Reset retries
and observation tails count towards the duration. No radio firmware is changed.
--mode labels the results only; it does not change radio transmission mode.

Each interval writes one CSV row per requested slave, including absent slaves.
Missing resets, missing telemetry and serial errors do not abort the run.
Summary JSON is checkpointed after every interval and finalized on interruption.

World loss includes states replaced by the master before radio transmission.
Confirmed, monotonic counters provide measured loss; unconfirmed counters provide
partial deltas only. Leading/trailing world losses are not observable. Telemetry
loss counts sequence gaps between received reports, excluding reset periods.
Telemetry silence is measured on the PC, including interval boundaries, and is
NOT the car's world-reception silence. No-data intervals report their full silence.
World gap duration is an estimate: (max_world_gap + 1) / Hz between receptions.
Per-minute maxima cannot determine the number of individual long-gap events.

Run: python -u test/test_8_long_run.py --slaves 1 2 --hours 4 --mode broadcast
Check: python -u test/test_8_long_run.py --duration-seconds 15 --interval-seconds 5
"""

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import time

from test_4_long_run import WindowsSession
from test_5_loss_run import SerialLink
from test_7_ota_unicast import FIELDS as TEST7_FIELDS, IntervalState, send_world_state

FIELDS = TEST7_FIELDS + [
    "elapsed_s",
    "minute",
    "configured_hz",
    "reset_duration_s",
    "world_loss_complete",
    "world_gap_estimate_ms",
    "telemetry_observed",
    "telemetry_rate_hz",
    "first_telemetry_delay_s",
    "host_serial_disconnects",
    "max_tx_lateness_ms",
    "max_serial_write_ms",
    "max_tx_interval_ms",
    "run_world_packets_written",
    # Aliases used by the existing long-run analysis/plotting script.
    "world_packets_sent",
    "total_world_packets_sent",
    "telemetry_lost",
    "interval_elapsed_s",
    "schedule_rebases",
    "observed_telemetry",
]


def reset_slaves(link, slaves, attempts, spacing, deadline):
    """Confirm each requested slave independently within a bounded reset window."""
    confirmed = set()
    used = 0
    start = time.perf_counter()
    link.connect(start)
    link.read()  # Discard reports queued before this reset phase.
    for _ in range(attempts):
        if time.perf_counter() >= deadline:
            break
        used += 1
        link.connect(time.perf_counter())
        link.send(-1)
        end = min(deadline, time.perf_counter() + spacing)
        while time.perf_counter() < end:
            for _, values in link.read():
                car, _, world, lost, events, gap = values
                if car in slaves and world == -1 and lost == events == gap == 0:
                    confirmed.add(car)
            if confirmed.issuperset(slaves):
                break
            time.sleep(min(0.005, max(0, end - time.perf_counter())))
        if confirmed.issuperset(slaves):
            break
    # Drain replies already in flight without issuing additional resets.
    end = min(deadline, time.perf_counter() + 0.15)
    while time.perf_counter() < end:
        for _, values in link.read():
            car, _, world, lost, events, gap = values
            if car in slaves and world == -1 and lost == events == gap == 0:
                confirmed.add(car)
        time.sleep(min(0.005, max(0, end - time.perf_counter())))
    return confirmed, used, time.perf_counter() - start


def measure(link, slaves, confirmed, seconds, hz, deadline):
    start = time.perf_counter()
    end = min(start + seconds, deadline)
    states = {car: IntervalState(car, car in confirmed, start) for car in slaves}
    first_rx = {}
    unexpected = set()
    period = 1 / hz
    next_slot = attempted = written = skipped = 0
    max_lateness = max_write = max_interval = 0.0
    previous_write = None
    disconnects_before = link.disconnects

    def receive():
        for received, values in link.read():
            car = values[0]
            if car not in states:
                unexpected.add(car)
                continue
            if values[2] != -1:
                first_rx.setdefault(car, received)
            states[car].receive(received, values)

    while time.perf_counter() < end:
        link.connect(time.perf_counter())
        receive()
        now = time.perf_counter()
        if now >= end:
            break
        slot = int((now - start) / period)
        if slot >= next_slot:
            skipped += slot - next_slot
            next_slot = slot + 1
            attempted += 1
            max_lateness = max(max_lateness, now - (start + slot * period))
            before = time.perf_counter()
            success = send_world_state(link, slot)
            max_write = max(max_write, time.perf_counter() - before)
            if success:
                written += 1
                if previous_write is not None:
                    max_interval = max(max_interval, before - previous_write)
                previous_write = before
        else:
            remaining = start + next_slot * period - now
            if remaining > 0.003:
                time.sleep(min(remaining - 0.002, 0.005))
    duration = max(0, end - start)
    skipped += max(0, math.ceil(duration * hz - 1e-9) - next_slot)
    tail_end = min(deadline, time.perf_counter() + 0.1)
    while time.perf_counter() < tail_end:
        receive()
        time.sleep(min(0.005, max(0, tail_end - time.perf_counter())))
    finish = time.perf_counter()
    common = dict(
        measurement_s=round(duration, 6),
        world_packets_attempted=attempted,
        world_packets_written=written,
        world_packets_skipped=skipped,
        world_tx_rate_hz=round(written / duration, 3) if duration else 0,
        serial_disconnects=link.disconnects - disconnects_before,
        host_serial_disconnects=link.disconnects - disconnects_before,
        max_tx_lateness_ms=round(max_lateness * 1000, 3),
        max_serial_write_ms=round(max_write * 1000, 3),
        max_tx_interval_ms=round(max_interval * 1000, 3),
    )
    rows = []
    for car, state in states.items():
        row = dict(common, car_id=car, **state.row(finish, written))
        complete = state.received > 0 and state.reset_confirmed and not state.rebases
        row["world_loss_complete"] = int(complete)
        row["telemetry_observed"] = state.received
        row["telemetry_rate_hz"] = (
            round(state.received / duration, 3) if duration else 0
        )
        row["first_telemetry_delay_s"] = (
            round(first_rx[car] - start, 3) if car in first_rx else ""
        )
        if state.received == 0:
            row["telemetry_age_s"] = round(finish - start, 3)
            row["max_telemetry_silence_s"] = round(finish - start, 3)
        gap = row.get("max_world_gap", "")
        row["world_gap_estimate_ms"] = (
            round((gap + 1) * 1000 / hz, 3) if gap != "" else ""
        )
        rows.append(row)
    return rows, common, unexpected


def slave_summary(rows):
    """Exclude unavailable/partial world-loss estimates from valid averages."""
    complete = [r for r in rows if r["world_loss_complete"]]
    loss = [r["world_loss_percent"] for r in complete if r["world_loss_percent"] != ""]
    sent = sum(r["world_packets_written"] for r in complete)
    lost = sum(r["world_packets_lost"] for r in complete)
    received = sum(r["telemetry_received"] for r in rows)
    tel_lost = sum(r.get("telemetry_packets_lost") or 0 for r in rows)
    gaps = [r["max_world_gap"] for r in complete]
    estimates = [r["world_gap_estimate_ms"] for r in complete]
    return dict(
        intervals=len(rows),
        status_counts=dict(Counter(r["status"] for r in rows)),
        complete_world_loss_intervals=len(complete),
        partial_or_missing_intervals=len(rows) - len(complete),
        measured_seconds=sum(r["measurement_s"] for r in rows),
        complete_world_loss_seconds=sum(r["measurement_s"] for r in complete),
        avg_world_loss_percent=statistics.mean(loss) if loss else None,
        weighted_world_loss_percent=100 * lost / sent if sent else None,
        max_interval_world_loss_percent=max(loss, default=None),
        world_packets_lost_complete_intervals=lost,
        world_packets_written_complete_intervals=sent,
        max_world_gap=max(gaps, default=None),
        max_world_gap_estimate_ms=max(estimates, default=None),
        intervals_world_gap_estimate_over_200ms=sum(g > 200 for g in estimates),
        interval_max_world_gap_distribution=dict(sorted(Counter(gaps).items())),
        max_telemetry_silence_s=max(
            (r["max_telemetry_silence_s"] for r in rows), default=None
        ),
        intervals_telemetry_silence_over_200ms=sum(
            r["max_telemetry_silence_s"] > 0.2 for r in rows
        ),
        telemetry_received=received,
        telemetry_packets_lost=tel_lost,
        telemetry_loss_percent=(
            100 * tel_lost / (received + tel_lost) if received + tel_lost else None
        ),
        reset_unconfirmed_intervals=sum(not r["reset_confirmed"] for r in rows),
        counter_rebases=sum(r.get("counter_rebases", 0) for r in rows),
    )


def run(args):
    duration = (
        args.duration_seconds
        if args.duration_seconds is not None
        else args.hours * 3600
    )
    if (
        any(
            not math.isfinite(v) or v <= 0
            for v in (duration, args.interval_seconds, args.hz, args.reset_spacing)
        )
        or args.reset_attempts <= 0
    ):
        raise ValueError(
            "Duration, interval, frequency and reset settings must be positive and finite"
        )
    if math.ceil(args.interval_seconds * args.hz) > 1_000_000:
        raise ValueError("Interval exceeds the six-digit world sequence range")
    slaves = sorted(set(args.slaves))
    if any(car < 0 or car > 255 for car in slaves):
        raise ValueError("Slave IDs must fit in an unsigned byte")
    folder = Path(args.log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (
        "radio_test_8_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
    )
    link = SerialLink(args.port, args.baud)
    rows_by_slave = {car: [] for car in slaves}
    intervals = []
    unexpected = set()
    started_at = datetime.now().isoformat(timespec="seconds")
    start = time.perf_counter()
    deadline = start + duration
    status = "RUNNING"
    error = None

    def checkpoint():
        summary = dict(
            status=status,
            error=error,
            csv=str(path.resolve()),
            started_at=started_at,
            updated_at=datetime.now().isoformat(timespec="seconds"),
            mode=args.mode,
            slave_ids=slaves,
            unexpected_slave_ids=sorted(unexpected),
            configured_hz=args.hz,
            duration_requested_s=duration,
            elapsed_s=round(time.perf_counter() - start, 3),
            interval_seconds=args.interval_seconds,
            intervals_completed=len(intervals),
            world_packets_written=sum(r["world_packets_written"] for r in intervals),
            world_packets_skipped=sum(r["world_packets_skipped"] for r in intervals),
            measurement_seconds=sum(r["measurement_s"] for r in intervals),
            reset_seconds=sum(r["reset_duration_s"] for r in intervals),
            max_tx_lateness_ms=max(
                (r["max_tx_lateness_ms"] for r in intervals), default=None
            ),
            max_serial_write_ms=max(
                (r["max_serial_write_ms"] for r in intervals), default=None
            ),
            max_tx_interval_ms=max(
                (r["max_tx_interval_ms"] for r in intervals), default=None
            ),
            serial_disconnects=link.disconnects,
            last_serial_error=link.last_error,
            slaves={
                str(car): slave_summary(rows) for car, rows in rows_by_slave.items()
            },
            notes=[
                "World loss averages exclude unconfirmed resets and counter rebases; remaining loss can omit leading/trailing losses.",
                "Telemetry silence is PC-observed, within measurement intervals; intentional resets are excluded.",
                "Gap durations are sequence-based estimates, not measured receiver timings.",
                "Long-gap interval counts count affected intervals, not individual outage events.",
                "Host totals count each transmission once, not once per slave CSV row.",
            ],
        )
        target = path.with_suffix(".summary.json")
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
        )
        temporary.replace(target)

    print(
        f"Test 8: {duration/3600:g} hours, {args.interval_seconds:g}s intervals, {args.hz:g} Hz\nSlaves {slaves}, {args.mode}, {args.port}\nCSV: {path.resolve()}",
        flush=True,
    )
    with WindowsSession(), path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()
        checkpoint()
        try:
            while time.perf_counter() < deadline:
                confirmed, attempts, reset_seconds = reset_slaves(
                    link, slaves, args.reset_attempts, args.reset_spacing, deadline
                )
                if time.perf_counter() >= deadline:
                    break
                rows, common, seen = measure(
                    link, slaves, confirmed, args.interval_seconds, args.hz, deadline
                )
                unexpected.update(seen)
                common["reset_duration_s"] = reset_seconds
                intervals.append(common)
                total_written = sum(r["world_packets_written"] for r in intervals)
                elapsed = time.perf_counter() - start
                print(
                    f'\n[{datetime.now().isoformat(timespec="seconds")}] {elapsed/3600:.3f} h | interval {len(intervals)} | reset confirmed {sorted(confirmed)} ({attempts} attempts)',
                    flush=True,
                )
                for row in rows:
                    row.update(
                        timestamp=datetime.now().isoformat(timespec="seconds"),
                        interval=len(intervals),
                        minute=len(intervals),
                        mode=args.mode,
                        reset_attempts=attempts,
                        reset_duration_s=round(reset_seconds, 3),
                        elapsed_s=round(elapsed, 3),
                        configured_hz=args.hz,
                        run_world_packets_written=total_written,
                    )
                    row.update(
                        world_packets_sent=row["world_packets_written"],
                        total_world_packets_sent=total_written,
                        telemetry_lost=row.get("telemetry_packets_lost", ""),
                        interval_elapsed_s=row["measurement_s"],
                        schedule_rebases=0,
                        observed_telemetry=row["telemetry_received"],
                    )
                    writer.writerow(row)
                    rows_by_slave[row["car_id"]].append(row)
                    print(
                        f"Slave {row['car_id']}: {row['status']} | world loss {row.get('world_loss_percent', '')}% | max gap {row.get('max_world_gap', '')} | telemetry lost {row.get('telemetry_packets_lost', '')} | max telemetry silence {row['max_telemetry_silence_s']:.3f}s",
                        flush=True,
                    )
                handle.flush()
                checkpoint()
            status = "COMPLETE"
        except KeyboardInterrupt:
            status = "INTERRUPTED"
            print("Interrupted; completed interval rows retained.", flush=True)
        except Exception as exc:
            status, error = "FAILED", str(exc)
            raise
        finally:
            link.close()
            checkpoint()
    print(
        f'Result: {path.resolve()}\nSummary: {path.with_suffix(".summary.json").resolve()}',
        flush=True,
    )
    return 0 if status == "COMPLETE" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--slaves", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--hours", type=float, default=8)
    parser.add_argument(
        "--duration-seconds", type=float, help="Override --hours for a short check"
    )
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--hz", type=float, default=100)
    parser.add_argument(
        "--mode",
        choices=("unicast", "broadcast"),
        default="broadcast",
        help="Result label only; match the flashed master firmware",
    )
    parser.add_argument("--reset-attempts", type=int, default=40)
    parser.add_argument("--reset-spacing", type=float, default=0.1)
    parser.add_argument(
        "--log-dir", default=str(Path(__file__).resolve().parent / "run_logs")
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
