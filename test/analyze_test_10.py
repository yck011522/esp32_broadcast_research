"""Plot Test 10 using the shared BLE analysis; default to newest Test 10 CSV."""

import sys
from pathlib import Path

from analyze_test_9 import main


if __name__ == "__main__":
    if len(sys.argv) == 1:
        paths = list((Path(__file__).parent / "run_logs").glob("ble_test_10_*.csv"))
        if not paths:
            raise SystemExit("No Test 10 CSV found; run test_10_ble_pc.py first.")
        sys.argv.append(str(max(paths, key=lambda path: path.stat().st_mtime)))
    main()
