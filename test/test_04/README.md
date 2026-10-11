# Test 4: ESP-NOW long run and diagnosis

The Python scripts and available results are retained. Exact historical firmware
is not reconstructed. Current ESP-NOW sources are under
[src/legacy/espnow](../../src/legacy/espnow/README.md), with provenance notes.
Test 6 references historical OTA profiles that are no longer available.

## Scripts

- [analyze_radio_test.py](analyze_radio_test.py)
- [test_4_checks.py](test_4_checks.py)
- [test_4_long_run.py](test_4_long_run.py)

## Results

- [test_4_diagnosis.md](results/test_4_diagnosis.md)

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_04 -t . -p '*checks.py'
```
