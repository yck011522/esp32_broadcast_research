"""Optional Test 10 server-side log: command receive rate, loss, and silence."""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
from datetime import datetime, timezone
from pathlib import Path
import time

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--seconds", type=float, default=7260,
                        help="monitor duration (default: two hours plus a minute for setup)")
    args = parser.parse_args()
    directory = Path(__file__).parent / "results/runs"
    directory.mkdir(exist_ok=True)
    path = directory / ("ble_test_10_server_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".log")
    port = serial.Serial(port=None, baudrate=115200, timeout=.2)
    port.dtr = False
    port.rts = False
    port.port = args.port
    port.open()
    start = time.perf_counter()
    buffer = bytearray()
    print(f"Monitoring {args.port}: {path}", flush=True)
    try:
        with path.open("w", encoding="utf-8") as output:
            while time.perf_counter() - start < args.seconds:
                buffer.extend(port.read(min(max(port.in_waiting, 1), 4096)))
                while b"\n" in buffer:
                    line, _, remainder = buffer.partition(b"\n")
                    buffer[:] = remainder
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    record = f"{stamp} [{time.perf_counter() - start:.3f}s] {text}"
                    output.write(record + "\n")
                    output.flush()
                    print(record, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()


if __name__ == "__main__":
    main()
