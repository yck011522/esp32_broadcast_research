"""Flash only verified local boards; records partial failures, never claims rollback."""
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
from test.test_14.protocol import CAR_PORTS, USB_IDENTITIES

ROOT = Path(__file__).resolve().parents[2]


def verify(boards, available):
    for port, _ in boards:
        found = available.get(port)
        if found is None or (found.vid, found.pid, found.serial_number) != (0x303a, 0x1001, USB_IDENTITIES[port]):
            raise RuntimeError(f"USB identity mismatch on {port}; no uploads performed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modem-only", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watchdog-test", action="store_true")
    mode.add_argument("--debug", action="store_true", help="temporary guarded modem state output")
    parser.add_argument("--pio", type=Path, default=Path("C:/Users/leungp/.platformio/penv/Scripts/platformio.exe"))
    args = parser.parse_args(argv)
    modem_profile = ("ble14_modem_watchdog_test" if args.watchdog_test else
                     "ble14_modem_debug" if args.debug else "ble14_modem")
    boards = [] if args.modem_only else [(port, f"ble14_car_{car}") for car, port in CAR_PORTS.items()]
    boards.append(("COM3", modem_profile))
    verify(boards, {p.device: p for p in list_ports.comports()})
    if not args.pio.is_file():
        parser.error("PlatformIO executable unavailable")
    folder = Path(__file__).parent/"results/runs"/("flash_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
    folder.mkdir(parents=True)
    state = {"status": "flashing", "uploaded": [], "attempted": [], "errors": []}
    def checkpoint():
        (folder/"firmware_state.json").write_text(json.dumps(state, indent=2)+"\n", encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    checkpoint()
    try:
        for port, profile in boards:
            state["attempted"].append({"port": port, "profile": profile, "usb_serial": USB_IDENTITIES[port]})
            checkpoint()
            print(f"Uploading {profile} on {port}", flush=True)
            with (folder/(profile+".log")).open("w", encoding="utf-8") as stream:
                result = subprocess.run([str(args.pio), "run", "-e", profile, "-t", "upload", "--upload-port", port],
                                        cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=420)
            if result.returncode:
                raise RuntimeError(f"Upload failed: {profile}")
            state["uploaded"].append(profile)
            checkpoint()
        state["status"] = "test14_installed"
    except Exception as exc:
        state["status"] = "failed_partial_upload"
        state["errors"].append(str(exc))
    finally:
        checkpoint()
    print(f"Finished: {state['status']}; {folder}", flush=True)
    return 0 if state["status"] == "test14_installed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
