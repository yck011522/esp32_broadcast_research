# Test 10 Windows throughput preference validation - 2026-10-10

Hardware: existing car firmware at `68:EE:8F:4B:5B:4D`, computer Bluetooth
adapter, Python 3.12.10, server serial COM3. No firmware changes.

`test_10_ble_pc.py` uses Windows' public WinRT
`RequestPreferredConnectionParameters(ThroughputOptimized)` API alongside
Bleak. It verifies the negotiated parameters, keeps the returned request alive
throughout the session, and closes the request/device handles on exit.
The full logger automatically uses this mode on Windows.

The successful run verified a 15 ms interval, zero peripheral latency, and
9.6 s supervision timeout. The interval was already 15 ms at the initial
pre-request snapshot; no within-connection 60-to-15 ms transition was observed.

## Full logger test

Command: `python test/test_10_ble_pc.py --duration-seconds 120`

- Completed with one connection, no BLE errors/disconnects/write failures.
- Active measurement time: 109.646 s after scanning/connection establishment.
- 5480 world writes: 49.979 Hz. Server counts also total 5480, zero sequence gaps.
- Server full active five-second windows: 49.40-51.00 Hz.
- 2193 notifications: 20.001 Hz, zero sequence gaps.
- Sequence RTT estimate: 34.82 ms average, 180 ms maximum.
- Timestamp-based echo age: 45.47 ms average, 195.13 ms maximum.
- Five-second mean RTT stayed around 23-46 ms; no growing seconds-long backlog.
- After shutdown the server returned to zero receive rate and increasing silence.
- [CSV](run_logs/ble_test_10_20261010_065013_471033.csv)
- [Summary](run_logs/ble_test_10_20261010_065013_471033.summary.json)
- [Server log](run_logs/ble_test_10_server_20261010_064958.log)

The duration includes scanning/connection establishment. RTT is the existing
sequence-age estimate and includes the server's notification timing; it is not
pure radio RTT. These are short validation runs, not a two-hour endurance result.

Offline checks: four unit tests pass, including sequence wrap/loss handling,
session behavior, and preference lifetime/cleanup on normal and exceptional exit.

API references:

- [Windows request and lifetime](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothledevice.requestpreferredconnectionparameters)
- [ThroughputOptimized preset](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.bluetoothlepreferredconnectionparameters.throughputoptimized)
