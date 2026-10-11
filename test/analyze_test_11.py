"""Analyze the newest Test 11 CSV for each car with the shared BLE plots."""

import csv
import sys
from pathlib import Path

from analyze_test_9 import main


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python test/analyze_test_11.py")
    directory = Path(__file__).parent / "run_logs"
    for car_id in (1, 2, 3):
        paths = list(directory.glob(f"ble_test_11_car_{car_id}_*.csv"))
        if not paths:
            print(f"Car {car_id}: no Test 11 CSV yet", flush=True)
            continue
        path = max(paths, key=lambda path: path.stat().st_mtime)
        with path.open(newline="", encoding="utf-8") as source:
            if not any(row["record_type"] == "ble_stats" for row in csv.DictReader(source)):
                print(f"Car {car_id}: no notification statistics in {path.name}", flush=True)
                continue
        sys.argv = [sys.argv[0], str(path)]
        print(f"Car {car_id}:", flush=True)
        main()
