# Test 13: ESP32 serial/BLE modem with two cars

Firmware: [modem](../../src/test_13/esp_esp_ble_modem/main.cpp),
[car](../../src/test_13/esp_esp_ble_car/main.cpp),
[protocol](../../src/test_13/include/esp_esp_ble_protocol.h).
NimBLE-Arduino 2.5.1; COM3 modem, COM4 Car 2, COM5 Car 3. Both
cars share UUIDs; the modem selects their fixed public BLE addresses.

From the repository root with `.venv` activated:

```powershell
python -u test/test_13/flash_test_13.py
python -u test/test_13/test_13_ble_modem.py --duration-seconds 120 --server-ports COM4 COM5
python -u test/test_13/test_13_ble_modem.py --hours 2 --server-ports COM4 COM5
```

To start the two-hour run in a hidden independent process:

```powershell
& ./test/test_13/start_test_13_endurance.ps1
```

It saves `launch.json` with the process ID, plus `console.log` and `stderr.log`,
in the same run folder. The final summary determines the experiment outcome;
starting the process alone does not mean the two-hour test passed.

The flash helper verifies USB identities and leaves Test 13 installed. Close
other serial monitors first. Cars generate synthetic telemetry and have no
motor outputs. The command-disconnect watchdog is disabled in these profiles.
The runner measures 50 Hz world commands and 20 Hz telemetry per car,
independently verifies car receive totals, then stops/disconnects and closes
all ports. `python -m test.test_13.test_13_ble_modem` also works.

New runs get `results/runs/<UTC timestamp>/`, containing raw JSONL, CSV,
final summary, per-car serial logs, a source/dependency manifest and copies
of available firmware builds. `--output-dir` overrides the entire run folder.
Firmware copies are build artifacts, not device readbacks. Historical
captures retain their existing names in `results/runs/`.

## Scripts

- [flash_test_13.py](flash_test_13.py)
- [start_test_13_endurance.ps1](start_test_13_endurance.ps1)
- [test_13_ble_modem.py](test_13_ble_modem.py)
- [test_13_checks.py](test_13_checks.py)
- [test_13_transport_probe.py](test_13_transport_probe.py)
- [analyze_test_13.py](analyze_test_13.py): Matplotlib plots and independent raw-record analysis.

Analyze the latest completed rate run:

```powershell
python test/test_13/analyze_test_13.py
```

An explicit `*.summary.json` path selects another run. Outputs are `analysis.md`,
`analysis.json` and PNGs under that run's `plots/` folder. Raw captures are unchanged.
Packet echo-age percentiles use post-write log timestamps and are approximate;
the report distinguishes those from the runner's pre-write timestamp measurements.

Raw JSONL logs are kept locally and ignored by Git. The committed CSV, summary,
analysis JSON, reports and PNG plots preserve results. Replaying packet-level
analysis after a fresh clone requires a copy of the original raw JSONL log.

## Results

- [test_13_result.md](results/test_13_result.md)
- [Folder migration verification and two-hour run](results/layout_migration_20261012.md)
- [Two-hour endurance analysis](results/runs/endurance_20261011_160139_879273/analysis.md)

Raw captures and generated plots are under `results/runs/`, when available.
Run offline checks from the root with:

```powershell
python -m unittest discover -s test/test_13 -t . -p '*checks.py'
```
