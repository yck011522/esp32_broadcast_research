# Test 12: NimBLE car firmware with PC Bluetooth

Firmware: [src/test_12/car](../../src/test_12/car/main.cpp).
Profiles: `ble_test12_car_1`, `ble_test12_car_2`, `ble_test12_car_3`.
The experiment wrapper flashes COM3/4/5 and restores normal BLE servers;
running the Python client alone does not flash firmware.

## Scripts

- [run_test_12_experiment.py](run_test_12_experiment.py)
- [test_12_ble_pc.py](test_12_ble_pc.py)
- [test_12_checks.py](test_12_checks.py)

## Results

- [test_12_result.md](results/test_12_result.md)

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_12 -t . -p '*checks.py'
```
