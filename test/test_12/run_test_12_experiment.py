"""Flash all three Test 12 servers, exercise BLE, then restore normal firmware.

Intended for this bench's verified COM3/4/5 ESP32 devices. Runs physical tests.
python test/test_12/run_test_12_experiment.py --duration-seconds 120 --single-seconds 35
"""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"COM3": "68:EE:8F:4B:5B:4C", "COM4": "68:EE:8F:46:33:44", "COM5": "68:EE:8F:4B:5B:18"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=120)
    parser.add_argument("--single-seconds", type=float, default=35)
    parser.add_argument("--pio", type=Path, default=Path("C:/Users/leungp/.platformio/penv/Scripts/platformio.exe"))
    args = parser.parse_args()
    if any(not math.isfinite(n) or n <= 0 for n in (args.duration_seconds, args.single_seconds)):
        parser.error("durations must be positive and finite")
    ports = {port.device: port for port in list_ports.comports()}
    for name, serial_number in EXPECTED.items():
        port = ports.get(name)
        if port is None or port.vid != 0x303a or port.pid != 0x1001 or port.serial_number != serial_number:
            raise SystemExit(f"Unexpected device on {name}; no firmware changes made")
    if not args.pio.is_file():
        raise SystemExit("PlatformIO executable not found; no firmware changes made")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = ROOT / "test/test_12/results/runs" / ("experiment_" + stamp)
    output.mkdir(parents=True)
    state = {"started_utc": datetime.now(timezone.utc).isoformat(), "test12_upload_attempts": [],
             "normal_profiles_restored": [], "runs": [], "errors": [], "status": "preparing"}
    state_path = output / "firmware_state.json"
    process_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    def checkpoint():
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def upload(car, test12):
        env = f"ble_test12_car_{car}" if test12 else f"ble_server_car_{car}"
        print(f"Uploading {env} to COM{car+2}", flush=True)
        with (output / (env + "_upload.log")).open("w", encoding="utf-8") as stream:
            result = subprocess.run([str(args.pio), "run", "-e", env, "-t", "upload",
                                     "--upload-port", f"COM{car+2}"], cwd=ROOT,
                                    stdout=stream, stderr=subprocess.STDOUT, env=process_env, timeout=240)
        if result.returncode:
            raise RuntimeError(f"{env} upload failed; see upload log")
        print(f"Uploaded {env}", flush=True)

    checkpoint()
    print(f"Experiment: {output}", flush=True)
    try:
        for car in (1, 2, 3):
            state["test12_upload_attempts"].append(car) # Restore even a partial flash.
            checkpoint()
            upload(car, True)
        time.sleep(2)
        state["status"] = "testing"
        checkpoint()
        runs = [("concurrent", [1, 2, 3], args.duration_seconds),
                ("sequential", [1, 2, 3], args.duration_seconds),
                ("sequential", [3, 2, 1], args.duration_seconds)]
        runs += [("sequential", [car], args.single_seconds) for car in (1, 2, 3)]
        for index, (mode, cars, duration) in enumerate(runs, 1):
            label = f"{index}_{mode}_{''.join(map(str, cars))}"
            print(f"Running {label}, target traffic duration {duration:g}s", flush=True)
            before = set(output.glob("*.summary.json"))
            with (output / (label + "_console.log")).open("w", encoding="utf-8") as stream:
                result = subprocess.run([sys.executable, "-u", str(ROOT / "test/test_12/test_12_ble_pc.py"),
                                         "--setup", mode, "--cars", *map(str, cars),
                                         "--duration-seconds", str(duration), "--ports", "COM3", "COM4", "COM5",
                                         "--output-dir", str(output)], cwd=ROOT, stdout=stream,
                                        stderr=subprocess.STDOUT, env=process_env, timeout=duration+160)
            new_summaries = sorted(set(output.glob("*.summary.json")) - before)
            run = {"label": label, "exit_code": result.returncode,
                   "summaries": [str(path) for path in new_summaries]}
            state["runs"].append(run)
            for path in new_summaries:
                summary = json.loads(path.read_text(encoding="utf-8"))
                print(f"Finished {label}: {summary['status']}; connections={list(summary['connections'])}", flush=True)
            checkpoint()
    except Exception as exc:
        state["errors"].append(f"{type(exc).__name__}: {exc}")
        print(str(exc), flush=True)
    finally:
        state["status"] = "restoring_normal_firmware"
        checkpoint()
        for car in state["test12_upload_attempts"]:
            try:
                upload(car, False)
                state["normal_profiles_restored"].append(car)
            except Exception as exc:
                state["errors"].append(str(exc))
                print(str(exc), flush=True)
            checkpoint()
        state["status"] = "restored" if state["normal_profiles_restored"] == state["test12_upload_attempts"] else "restoration_failed"
        state["finished_utc"] = datetime.now(timezone.utc).isoformat()
        checkpoint()
    print(f"Finished: {state['status']}; {state_path}", flush=True)
    return 0 if state["status"] == "restored" and not state["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
