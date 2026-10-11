# Test 11: PC Bluetooth multi-car experiment

The available BLE server source used by Tests 9–11 is
[src/test_11/ble_server](../../src/test_11/ble_server/main.cpp),
built with `ble_server` or `ble_server_car_1/2/3`. Test 9 also uses
[src/test_09/ble_client](../../src/test_09/ble_client/main.cpp), profile
`ble_client`. These are the retained revisions, not recreated historical versions.

## Scripts

- [analyze_test_11.py](analyze_test_11.py)
- [test_11_ble_pc.py](test_11_ble_pc.py)
- [test_11_checks.py](test_11_checks.py)
- [test_11_connection_probe.py](test_11_connection_probe.py)
- [test_11_monitor_servers.py](test_11_monitor_servers.py)
- [test_11_probe_checks.py](test_11_probe_checks.py)

## Results

- [test_11_result.md](results/test_11_result.md)

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_11 -t . -p '*checks.py'
```
