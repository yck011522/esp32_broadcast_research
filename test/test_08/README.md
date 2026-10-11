# Test 8: ESP-NOW multi-car long run

The Python scripts and available results are retained. Exact historical firmware
is not reconstructed. Current ESP-NOW sources are under
[src/legacy/espnow](../../src/legacy/espnow/README.md), with provenance notes.
Test 6 references historical OTA profiles that are no longer available.

## Scripts

- [plot_test_8.py](plot_test_8.py)
- [test_8_checks.py](test_8_checks.py)
- [test_8_long_run.py](test_8_long_run.py)

## Results

No standalone result report is currently retained.

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_08 -t . -p '*checks.py'
```
