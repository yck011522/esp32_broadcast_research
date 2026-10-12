"""Verify all five USB identities, then flash the shared four-car BLE setup."""
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

ROOT = Path(__file__).resolve().parents[2]
BOARDS = (("COM4", "68:EE:8F:46:33:44", "esp_esp_ble_car_2"),
          ("COM5", "68:EE:8F:4B:5B:18", "esp_esp_ble_car_3"),
          ("COM7", "1C:DB:D4:43:73:EC", "esp_esp_ble_car_1"),
          ("COM8", "68:EE:8F:4B:4F:34", "esp_esp_ble_car_4"),
          ("COM3", "68:EE:8F:4B:5B:4C", "esp_esp_ble_modem_13b"))


def verify_boards(ports):
    for name, identity, _ in BOARDS:
        port = ports.get(name)
        if port is None or (port.vid, port.pid, port.serial_number) != (0x303a, 0x1001, identity):
            raise RuntimeError(f"Unexpected USB identity on {name}; no firmware changes made")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pio", type=Path, default=Path("C:/Users/leungp/.platformio/penv/Scripts/platformio.exe"))
    args = parser.parse_args()
    verify_boards({port.device: port for port in list_ports.comports()})
    if not args.pio.is_file():
        parser.error("PlatformIO executable not found")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder = Path(__file__).parent / "results/runs" / ("flash_"+stamp)
    folder.mkdir(parents=True)
    state = {"status": "flashing", "attempted": [], "uploaded": [], "errors": [],
             "note": "Leaves Test 13b installed; partial upload failures are recorded and not claimed as success."}
    def checkpoint():
        (folder / "firmware_state.json").write_text(json.dumps(state, indent=2)+"\n")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    checkpoint()
    try:
        for port, identity, profile in BOARDS:
            print(f"Uploading {profile} to {port}", flush=True)
            state["attempted"].append({"port": port, "usb_serial": identity, "profile": profile})
            checkpoint()
            with (folder / (profile+".log")).open("w", encoding="utf-8") as stream:
                result = subprocess.run([str(args.pio), "run", "-e", profile, "-t", "upload", "--upload-port", port],
                                        cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
            if result.returncode:
                raise RuntimeError(f"Upload failed: {profile}; see {folder}")
            state["uploaded"].append(profile)
            checkpoint()
        state["status"] = "test13b_installed"
    except Exception as exc:
        state["status"] = "failed_partial_upload"
        state["errors"].append(str(exc))
    finally:
        checkpoint()
    print(f"Finished: {state['status']}; {folder}", flush=True)
    return 0 if state["status"] == "test13b_installed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
