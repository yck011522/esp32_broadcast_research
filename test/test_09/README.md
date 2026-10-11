# Test 9: ESP32 BLE client serial logging

The available BLE server source used by Tests 9–11 is
[src/test_11/ble_server](../../src/test_11/ble_server/main.cpp),
built with `ble_server` or `ble_server_car_1/2/3`. Test 9 also uses
[src/test_09/ble_client](../../src/test_09/ble_client/main.cpp), profile
`ble_client`. These are the retained revisions, not recreated historical versions.

## Scripts

- [analyze_test_9.py](analyze_test_9.py)
- [test_9_ble_test.py](test_9_ble_test.py)
- [test_9_checks.py](test_9_checks.py)

## Results

No standalone result report is currently retained.

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_09 -t . -p '*checks.py'
```
