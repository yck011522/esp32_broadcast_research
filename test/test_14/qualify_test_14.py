"""Run controlled Test 14 cases, restore normal modem, then optional stability.

python -u test/test_14/qualify_test_14.py --stability-hours 1
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DURATIONS = {"reconnect": 160, "double": 60, "idle": 70, "expiry": 60,
             "mailbox": 35, "car-watchdog": 65, "missing-start": 60, "modem-watchdog": 90}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=list(DURATIONS), default=list(DURATIONS))
    parser.add_argument("--stability-hours", type=float, default=0)
    args = parser.parse_args(argv)
    if not 0 <= args.stability_hours <= 8:
        parser.error("stability hours must be 0..8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    folder = Path(__file__).parent/"results/runs"/("qualification_"+stamp)
    folder.mkdir(parents=True)
    record = {"started_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "cases": [], "operations": [], "errors": []}
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    old_sleep = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001) if os.name == "nt" else None
    watchdog_installed = False
    def checkpoint():
        (folder/"qualification.json").write_text(json.dumps(record, indent=2)+"\n", encoding="utf-8")
    def command(arguments, label, timeout):
        print(f"Starting {label}", flush=True)
        started = time.perf_counter()
        with (folder/(label+".log")).open("w", encoding="utf-8") as output:
            result = subprocess.run([sys.executable, "-u", *arguments], cwd=ROOT, env=env,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        record["operations"].append({"label": label, "exit_code": result.returncode,
                                     "elapsed_s": time.perf_counter()-started})
        checkpoint()
        return result.returncode
    checkpoint()
    print(f"Qualification folder: {folder}", flush=True)
    try:
        for case in args.cases:
            if case == "modem-watchdog":
                # Restore in finally even when the upload or fault test fails.
                watchdog_installed = True
                if command(["-m", "test.test_14.flash_test_14", "--modem-only", "--watchdog-test"], "flash_watchdog", 480):
                    raise RuntimeError("Watchdog test firmware upload failed")
            directory = folder/case
            code = command(["-m", "test.test_14.test_14_ble_modem", "--scenario", case,
                            "--duration-seconds", str(DURATIONS[case]), "--label", case,
                            "--output-dir", str(directory), "--modem-profile",
                            "ble14_modem_watchdog_test" if case == "modem-watchdog" else "ble14_modem"],
                           case, DURATIONS[case]+120)
            summaries = list(directory.glob("*.summary.json"))
            summary = json.loads(summaries[0].read_text()) if len(summaries) == 1 else {}
            record["cases"].append({"case": case, "exit_code": code, "status": summary.get("status", "missing_summary"),
                                    "summary": str(summaries[0].relative_to(folder)) if summaries else None,
                                    "validation_failures": summary.get("validation_failures", []),
                                    "errors": summary.get("errors", [])})
            checkpoint()
            print(f"Finished {case}: {summary.get('status', 'missing_summary')}", flush=True)
            if code:
                raise RuntimeError(f"Qualification case failed: {case}")
            if case == "modem-watchdog":
                if command(["-m", "test.test_14.flash_test_14", "--modem-only"], "restore_normal", 480):
                    raise RuntimeError("Normal modem restore failed")
                watchdog_installed = False
        if args.stability_hours:
            seconds = args.stability_hours*3600
            directory = folder/"stability"
            code = command(["-m", "test.test_14.test_14_ble_modem", "--duration-seconds", str(seconds),
                            "--label", f"stability_{args.stability_hours:g}h", "--output-dir", str(directory)],
                           "stability", seconds+120)
            summaries = list(directory.glob("*.summary.json"))
            summary = json.loads(summaries[0].read_text()) if len(summaries) == 1 else {}
            record["cases"].append({"case": "stability", "exit_code": code, "status": summary.get("status"),
                                    "summary": str(summaries[0].relative_to(folder)) if summaries else None,
                                    "validation_failures": summary.get("validation_failures", [])})
            if code:
                raise RuntimeError("Stability run failed")
        record["status"] = "passed"
    except Exception as exc:
        record["status"] = "failed"
        record["errors"].append(f"{type(exc).__name__}: {exc}")
        print(str(exc), flush=True)
    finally:
        if watchdog_installed:
            try:
                code = command(["-m", "test.test_14.flash_test_14", "--modem-only"], "restore_normal_finally", 480)
                record["normal_restore_exit_code"] = code
                if code:
                    record["status"] = "failed"
            except Exception as exc:
                record["status"] = "failed"
                record["errors"].append(f"Normal restore: {exc}")
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        checkpoint()
        if old_sleep:
            ctypes.windll.kernel32.SetThreadExecutionState(old_sleep)
    print(f"Qualification: {record['status']}; {folder/'qualification.json'}", flush=True)
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
