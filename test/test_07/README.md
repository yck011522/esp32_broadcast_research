# Test 7: ESP-NOW unicast comparison

The Python scripts and available results are retained. Exact historical firmware
is not reconstructed. Current ESP-NOW sources are under
[src/legacy/espnow](../../src/legacy/espnow/README.md), with provenance notes.
Test 6 references historical OTA profiles that are no longer available.

## Scripts

- [test_7_checks.py](test_7_checks.py)
- [test_7_ota_unicast.py](test_7_ota_unicast.py)

## Results

No standalone result report is currently retained.

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_07 -t . -p '*checks.py'
```
