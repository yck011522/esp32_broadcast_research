"""Record all three cars' serial world-receive statistics during Test 11."""

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


def open_port(name):
    port = serial.Serial(port=None, baudrate=115200, timeout=0)
    port.dtr = False
    port.rts = False
    port.port = name
    port.open()
    return port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=7260)
    parser.add_argument("--ports", nargs=3, default=["COM3", "COM4", "COM5"],
                        metavar="COM_PORT", help="Car 1, Car 2, and Car 3 serial ports")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "results/runs")
    args = parser.parse_args()
    if args.seconds <= 0 or len(set(port.upper() for port in args.ports)) != 3:
        parser.error("seconds must be positive and the three ports must be distinct")
    directory = args.output_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ports, files, buffers = [], [], []
    retry_at = [0.0] * 3
    try:
        for car_id, name in enumerate(args.ports, 1):
            ports.append(None)
            path = directory / f"ble_test_11_car_{car_id}_server_{stamp}.log"
            files.append(path.open("w", encoding="utf-8"))
            buffers.append(bytearray())
            print(f"Car {car_id} {name}: {path}", flush=True)
        started = time.perf_counter()
        while time.perf_counter() - started < args.seconds:
            for car_id, (port, output, buffer) in enumerate(zip(ports, files, buffers), 1):
                index = car_id - 1
                if port is None and time.perf_counter() < retry_at[index]:
                    continue
                try:
                    if port is None:
                        port = ports[index] = open_port(args.ports[index])
                        buffer.clear()
                        buffer.extend(b"serial monitor: port opened\n")
                    available = port.in_waiting
                    if available:
                        buffer.extend(port.read(min(available, 4096)))
                except (serial.SerialException, OSError) as exc:
                    if port is not None:
                        port.close()
                    ports[index] = None
                    retry_at[index] = time.perf_counter() + 1
                    # Keep the other cars' capture running while USB re-enumerates.
                    buffer.clear()
                    buffer.extend(f"serial monitor: {exc}; retrying\n".encode())
                while b"\n" in buffer:
                    line, _, remainder = buffer.partition(b"\n")
                    buffer[:] = remainder
                    message = line.decode("utf-8", errors="replace").rstrip("\r")
                    record = (f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                              f"[{time.perf_counter()-started:.3f}s] {message}")
                    output.write(record + "\n")
                    output.flush()
                    if message.startswith(("world stats:", "ble ", "serial monitor:")):
                        print(f"Car {car_id}: {record}", flush=True)
            time.sleep(.02)
    except KeyboardInterrupt:
        pass
    finally:
        for port in ports:
            if port is not None:
                port.close()
        for output in files:
            output.close()


if __name__ == "__main__":
    main()
