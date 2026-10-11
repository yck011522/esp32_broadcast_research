"""Flash the verified COM3/4/5 bench into the Test 13 modem/two-car setup.

Leaves Test 13 installed on success. Restores previous normal server profiles
if an upload fails partway through. Does not start the Python traffic test.
"""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from serial.tools import list_ports


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pio", type=Path, default=Path("C:/Users/leungp/.platformio/penv/Scripts/platformio.exe"))
    parser.add_argument("--output-dir", type=Path, default=root / "test/test_13/results/runs")
    args = parser.parse_args()
    expected = {"COM3": "68:EE:8F:4B:5B:4C", "COM4": "68:EE:8F:46:33:44", "COM5": "68:EE:8F:4B:5B:18"}
    ports = {port.device: port for port in list_ports.comports()}
    for name, serial_number in expected.items():
        port = ports.get(name)
        if port is None or port.vid != 0x303a or port.pid != 0x1001 or port.serial_number != serial_number:
            raise SystemExit(f"Unexpected USB identity on {name}; no changes made")
    if not args.pio.is_file():
        raise SystemExit("PlatformIO executable not found; no changes made")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = args.output_dir / ("flash_"+stamp)
    output.mkdir(parents=True)
    state = {"status": "flashing", "attempted": [], "uploaded": [], "restored": [], "errors": []}
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    def checkpoint():
        (output / "firmware_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def upload(profile, port):
        print(f"Uploading {profile} to {port}", flush=True)
        with (output / (profile+".log")).open("w", encoding="utf-8") as stream:
            result = subprocess.run([str(args.pio), "run", "-e", profile, "-t", "upload", "--upload-port", port],
                                    cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
        if result.returncode:
            raise RuntimeError(f"Upload failed: {profile}; see {output}")
        print(f"Uploaded {profile}", flush=True)

    checkpoint()
    successful = False
    try:
        for profile, port, old_profile in (
            ("esp_esp_ble_car_2", "COM4", "ble_server_car_2"),
            ("esp_esp_ble_car_3", "COM5", "ble_server_car_3"),
            ("esp_esp_ble_modem", "COM3", "ble_server_car_1"),
        ):
            state["attempted"].append({"profile": profile, "port": port, "previous": old_profile})
            checkpoint()
            upload(profile, port)
            state["uploaded"].append(profile)
            checkpoint()
        successful = True
        state["status"] = "test13_installed"
    except Exception as exc:
        state["errors"].append(str(exc))
        print(str(exc), flush=True)
    finally:
        if not successful:
            state["status"] = "restoring"
            checkpoint()
            for item in state["attempted"]:
                try:
                    upload(item["previous"], item["port"])
                    state["restored"].append(item["port"])
                except Exception as exc:
                    state["errors"].append(str(exc))
            state["status"] = "restored_after_failure" if len(state["restored"]) == len(state["attempted"]) else "restoration_failed"
        checkpoint()
    print(f"Finished: {state['status']}; {output}", flush=True)
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
