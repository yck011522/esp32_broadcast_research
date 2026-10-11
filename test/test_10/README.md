# Test 10: PC Bluetooth single-car experiment

The available BLE server source used by Tests 9–11 is
[src/test_11/ble_server](../../src/test_11/ble_server/main.cpp),
built with `ble_server` or `ble_server_car_1/2/3`. Test 9 also uses
[src/test_09/ble_client](../../src/test_09/ble_client/main.cpp), profile
`ble_client`. These are the retained revisions, not recreated historical versions.

## Scripts

- [analyze_test_10.py](analyze_test_10.py)
- [test_10_ble_pc.py](test_10_ble_pc.py)
- [test_10_checks.py](test_10_checks.py)
- [test_10_monitor_server.py](test_10_monitor_server.py)

## Results

- [test_10_throughput_result.md](results/test_10_throughput_result.md)

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_10 -t . -p '*checks.py'
```
