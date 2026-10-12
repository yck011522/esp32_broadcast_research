"""Matplotlib analysis for Test 13b; reuses the Test 13 analyzer."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path
from test.test_13.analyze_test_13 import main

if __name__ == "__main__":
    raise SystemExit(main(result_root=Path(__file__).parent / "results/runs"))
