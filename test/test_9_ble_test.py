"""Log BLE client USB reports: python test/test_9_ble_test.py (COM4, two hours).

Requires pyserial. Each BLE_STATS line becomes one CSV row with notification
and RTT measurements for the same reporting window.
All serial lines and connection events are also preserved in a JSONL file.
"""

import argparse
import csv
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time


FIELDS = [
    "timestamp_utc", "elapsed_s", "record_type", "connection", "window_s",
    "received", "seq_lost", "loss_percent", "rate_hz", "max_gap_s",
    "silence_s", "have_packet", "world_seq", "rtt_avg_ms", "rtt_max_ms",
    "rtt_samples", "message",
]
KEYS = {
    "window": "window_s", "received": "received", "seq_lost": "seq_lost",
    "loss": "loss_percent", "rate": "rate_hz", "max_gap": "max_gap_s",
    "silence": "silence_s", "have_packet": "have_packet", "world_seq": "world_seq",
}


def parse_report(line):
    """Parse numeric fields; leave unavailable RTT values empty, never zero."""
    if line.startswith("BLE_STATS "):
        row = {"record_type": "ble_stats"}
        mapping = {field: field for field in FIELDS}
    elif line.startswith("computer stats:"):
        row = {"record_type": "notification"}
        mapping = KEYS
    elif line.startswith("RTT estimate:"):
        row = {"record_type": "rtt"}
        mapping = {"avg": "rtt_avg_ms", "max": "rtt_max_ms", "samples": "rtt_samples"}
    else:
        return None
    for key, value in re.findall(r"([a-z_]+)=([0-9]+(?:\.[0-9]+)?)", line):
        if key in mapping:
            row[mapping[key]] = float(value) if "." in value else int(value)
    if row["record_type"] == "ble_stats":
        # Incomplete lines remain in the raw log, rather than becoming fake data.
        required = set(KEYS.values()) | {"rtt_samples"}
        if not required.issubset(row):
            return None
        if row["rtt_samples"] > 0 and not {"rtt_avg_ms", "rtt_max_ms"}.issubset(row):
            return None
    return row


def positive_number(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--hours", type=positive_number, default=2)
    parser.add_argument("--duration-seconds", type=positive_number,
                        help="override --hours for a short trial")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parent / "run_logs")
    args = parser.parse_args()
    try:
        import serial
    except ImportError:
        parser.error("pyserial is required: python -m pip install pyserial")

    duration = args.duration_seconds if args.duration_seconds is not None else args.hours * 3600
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "ble_test_9_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    csv_path = args.output_dir / (stem + ".csv")
    raw_path = args.output_dir / (stem + ".jsonl")
    summary_path = args.output_dir / (stem + ".summary.json")
    summary = {"port": args.port, "baud": args.baud, "planned_duration_s": duration,
               "started_utc": datetime.now(timezone.utc).isoformat(),
               "connections": 0, "serial_errors": 0, "serial_lines": 0,
               "notification_reports": 0, "rtt_reports": 0, "ble_stats_reports": 0, "status": "running",
               "csv": str(csv_path), "raw_log": str(raw_path)}
    start = time.monotonic()
    deadline = start + duration
    link = None
    buffer = bytearray()
    next_connect = start
    sleep_state = None
    if os.name == "nt":
        # Keep Windows awake, while allowing the display to turn off.
        sleep_state = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    print(f"Logging {args.port} for {duration / 3600:.2f} hours", flush=True)
    print(f"CSV: {csv_path}\nRaw log: {raw_path}", flush=True)

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file, \
            raw_path.open("w", encoding="utf-8") as raw_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()

        def checkpoint():
            summary["elapsed_s"] = round(time.monotonic() - start, 3)
            temporary = summary_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            temporary.replace(summary_path)

        def record(kind, message):
            timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            elapsed = round(time.monotonic() - start, 3)
            common = {"timestamp_utc": timestamp, "elapsed_s": elapsed,
                      "connection": summary["connections"], "message": message}
            raw_file.write(json.dumps({**common, "record_type": kind}) + "\n")
            raw_file.flush()
            parsed = parse_report(message) if kind == "serial" else None
            if parsed:
                writer.writerow({**common, **parsed})
                summary[parsed["record_type"] + "_reports"] += 1
                print(f"[{elapsed:8.1f}s] {message}", flush=True)
            elif kind != "serial":
                writer.writerow({**common, "record_type": kind})
                print(f"[{elapsed:8.1f}s] {kind}: {message}", flush=True)
            csv_file.flush()

        try:
            checkpoint()
            while time.monotonic() < deadline:
                if link is None:
                    if time.monotonic() < next_connect:
                        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
                        continue
                    try:
                        # Set modem lines before opening; avoid an intentional reset.
                        link = serial.Serial(port=None, baudrate=args.baud, timeout=0.2)
                        link.dtr = False
                        link.rts = False
                        link.port = args.port
                        link.open()
                        summary["connections"] += 1
                        record("connected", args.port)
                        checkpoint()
                    except (serial.SerialException, OSError) as exc:
                        if link is not None:
                            link.close()
                        link = None
                        summary["serial_errors"] += 1
                        record("serial_error", str(exc))
                        checkpoint()
                        next_connect = time.monotonic() + 5
                        continue
                try:
                    chunk = link.read(min(max(link.in_waiting, 1), 4096))
                except (serial.SerialException, OSError) as exc:
                    summary["serial_errors"] += 1
                    record("serial_error", str(exc))
                    if buffer:
                        record("partial_line", buffer.decode("utf-8", errors="replace"))
                    buffer.clear()
                    link.close()
                    link = None
                    checkpoint()
                    next_connect = time.monotonic() + 5
                    continue
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, remainder = buffer.partition(b"\n")
                    buffer[:] = remainder
                    summary["serial_lines"] += 1
                    record("serial", line.decode("utf-8", errors="replace").rstrip("\r"))
                    checkpoint()
                if len(buffer) > 65536:
                    record("partial_line", buffer.decode("utf-8", errors="replace"))
                    buffer.clear()
            summary["status"] = "completed"
        except KeyboardInterrupt:
            summary["status"] = "interrupted"
        except Exception:
            summary["status"] = "failed"
            raise
        finally:
            if buffer:
                record("partial_line", buffer.decode("utf-8", errors="replace"))
            if link is not None:
                link.close()
            if os.name == "nt" and sleep_state:
                ctypes.windll.kernel32.SetThreadExecutionState(sleep_state)
            summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
            checkpoint()
    print(f"Finished: {summary['status']}. Summary: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
