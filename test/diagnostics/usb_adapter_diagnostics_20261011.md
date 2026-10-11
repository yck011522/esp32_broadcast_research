# BARROT multiple-connection diagnostics — 11 October 2026

Only the first requested car connected in all four physical experiments. Reversing the order changed which car succeeded. The second and third connections failed during GATT service discovery with `BleakError: Could not get GATT services: Unreachable`, before the planned three-car traffic test could begin.

The failure also occurred with **zero application writes, no notification subscriptions, and the command disconnect watchdog disabled on all three servers**. Disabling that watchdog was useful for this idle experiment, but did not resolve the connection failure.

## Setup and results

Windows 11 Education 25H2, build 26200.9457; Python 3.12.10; Bleak 3.0.2. Active adapter: BARROT Bluetooth 5.4 Adapter, USB VID_33FA/PID_0010, driver 17.55.6.566 dated 26 July 2024. Intel Bluetooth remained disabled. COM3, COM4 and COM5 correspond to Cars 1, 2 and 3.

The diagnostic scans before connecting, caches discovered device objects, connects sequentially, and performs no scanning while a connection is active. During setup, notifications are not subscribed. The 2 Hz writes keep the normal command watchdog satisfied.

| Experiment | Connection order | Writes per connected car | Preference | Command watchdog | Connected | Others |
| --- | --- | ---: | --- | --- | --- | --- |
| Low traffic | 1 → 2 → 3 | 2 Hz | Default | Enabled | Car 1 | GATT Unreachable |
| Reverse order | 3 → 2 → 1 | 2 Hz | Default | Enabled | Car 3 | GATT Unreachable |
| Power preference | 1 → 2 → 3 | 2 Hz | PowerOptimized | Enabled | Car 1 | GATT Unreachable |
| Pure idle | 1 → 2 → 3 | 0 Hz | Default | Disabled | Car 1 | GATT Unreachable |

Windows accepted the power preference, but the successful server link still reported a 30 ms interval and zero peripheral latency. No experiment established all three connections, so the planned 20 Hz notification / 2, 5, 20 and 50 Hz command sweep was not run. These results do not constitute a three-car throughput measurement.

In the pure-idle run, serial health verified `command_watchdog=0` on all three boards. The first car stayed connected for roughly 35 seconds without commands or notifications. The other two remained advertising. Server logs showed no firmware restarts or command-timeout disconnects during the setup failure.

Earlier single-car tests succeeded for every car, including approximately 50 Hz commands and 20 Hz notifications; see [the baseline report](usb_adapter_result_20261011.md). Taken together, the observations point toward adapter/controller/driver or Windows handling of multiple BLE links. They do **not** prove a hard one-link hardware limit or identify the exact component at fault.

## Firmware and cleanup

Added the default-enabled `BLE_COMMAND_WATCHDOG_ENABLED` build flag and three explicitly named `ble_server_car_N_idle_probe` profiles. Setting the flag to zero disables only the command-timeout disconnect; the task watchdog and advertising recovery remain enabled. Serial health now reports the flag.

All three diagnostic profiles built and uploaded successfully. After the idle experiment, all three normal profiles were built and uploaded successfully. A separate nine-second serial capture verified `command_watchdog=1`, `advertising=1`, `connected=0`, zero restarts and zero notification errors on every board. All BLE clients and serial monitors were closed.

The Intel comparison was deferred at the user's request. Windows had refused the attempted adapter switch with a pending-reboot error. A subsequent independent device check confirmed BARROT status OK and Intel still disabled. No driver was installed or updated, and no restart was performed.

Validation: 42 offline repository checks passed, including four checks for the diagnostic runner. Both temporary and normal firmware builds passed for each of the three boards.

## Evidence and reproduction

Runner: [test_11_connection_probe.py](../test_11/test_11_connection_probe.py). Firmware instructions are in [README.md](../../README.md).

- [Low-traffic summary](results/usb_diagnostics_20261011/ble_connection_probe_123_default_20261011_133339_727923.summary.json)
- [Reverse-order summary](results/usb_diagnostics_20261011/ble_connection_probe_321_default_20261011_133451_510180.summary.json)
- [Power-preference summary](results/usb_diagnostics_20261011/ble_connection_probe_123_power_20261011_133604_995842.summary.json)
- [Pure-idle summary](results/usb_diagnostics_20261011/ble_connection_probe_123_default_20261011_134956_306697.summary.json)
- [Firmware restoration checkpoint](results/usb_diagnostics_20261011/idle_firmware_state.json)
- Restored serial captures: [Car 1](results/usb_diagnostics_20261011/ble_test_11_car_1_server_20261011_135408.log), [Car 2](results/usb_diagnostics_20261011/ble_test_11_car_2_server_20261011_135408.log), [Car 3](results/usb_diagnostics_20261011/ble_test_11_car_3_server_20261011_135408.log).

Each diagnostic summary links its simultaneous server serial captures; the matching JSONL file contains discovery, connection, phase and cleanup events. Numeric CSV files are retained, but notification rates of zero during setup are intentional and cannot be interpreted as packet loss.

```powershell
.venv/Scripts/python.exe -u test/test_11/test_11_connection_probe.py --order 1 2 3
.venv/Scripts/python.exe -u test/test_11/test_11_connection_probe.py --order 3 2 1
.venv/Scripts/python.exe -u test/test_11/test_11_connection_probe.py --connection-mode power
```

For a pure-idle reproduction, explicitly flash all three idle-probe profiles first, then use `--keepalive-hz 0`. The runner requires serial confirmation of the disabled command watchdog before connecting. Restore the normal firmware profiles after the experiment.
