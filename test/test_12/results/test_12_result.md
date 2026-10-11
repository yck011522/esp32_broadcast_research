# Test 12 — separate NimBLE-Arduino experiment

Date: 11 October 2026 (Hong Kong). The separate NimBLE-Arduino firmware works individually on the cars, but did not resolve the BARROT multiple-connection failure. Both concurrent and sequential setup allowed only one car to connect; the others failed before application traffic began.

## Implementation and corrections to the supplied advice

- Separate source: [src/test_12/car/main.cpp](../../../src/test_12/car/main.cpp), with explicit `ble_test12_car_1/2/3` PlatformIO profiles. NimBLE-Arduino is pinned to 2.5.1 and uses the 2.x callback API. Existing Test 10/11 firmware remains separate.
- New Python runner: [test_12_ble_pc.py](../test_12_ble_pc.py). The three servers share the supplied service UUID and advertise `ESP32_Robot_1/2/3`. Discovered BLEDevice objects are passed to Bleak, avoiding implicit scans during connection setup. Connections and serial ports are closed in `finally`.
- Commands retain the earlier **20-byte** requirement: the sample's seven fields occupy seven bytes, followed by 13 random padding bytes. Movement bytes are neutral, and the firmware implements no motor outputs.
- The sample's packed telemetry structure occupies **21 bytes**. Its Python format omitted the follow-state byte, producing a 20-byte decoder and the wrong field indexing. Test 12 uses `<BHHB6sHBhhH` and verifies an ATT MTU of at least 24; every successful physical connection negotiated 64.
- Command and telemetry sequence checks use unsigned modulo-65536 differences, including wraparound, duplicate and old-packet handling. Shared command generation awaits concurrent writes without response and uses deadlines with worker-thread sleep on Windows; actual rates are measured rather than assumed.
- Five-second measurements include notification rates/gaps, write attempts/errors, sequence-based approximate RTT and timestamped echoed-command age. The sequence estimate compares the last sequence actually attempted, avoiding the example's next-unsent-sequence offset. Echo age includes Windows queuing, BLE transport, server processing and notification timing; it is not pure radio RTT.
- The experimental command-disconnect watchdog is absent so setup can be truly idle. No notification subscription or command traffic starts until every requested car has connected and completed the ten-second idle observation.

The previous firmware already used Arduino's BLE API backed by NimBLE: installed ESP32-S3 SDK configuration sets `CONFIG_BT_NIMBLE_ENABLED=1`, and the normal firmware map links `BLEDevice.cpp` to `nimble_port_init`. This experiment therefore compares a separate C++ library/application implementation rather than switching an existing Bluedroid firmware to NimBLE. The quoted RAM/throughput claims cannot be assumed for this comparison.

Primary references: [NimBLE-Arduino 2.x migration guide](https://h2zero.github.io/NimBLE-Arduino/md_1_8x__to2_8x__migration__guide.html), [2.5.1 release](https://github.com/h2zero/NimBLE-Arduino/releases/tag/2.5.1), and [Bleak connection guidance](https://bleak.readthedocs.io/en/latest/api/client.html). Bleak documents adapter-dependent concurrent connection support and problems caused by implicit discovery when raw addresses are used.

## Physical setup

Windows 11 Education 25H2, build 26200.9457; Python 3.12.10; Bleak 3.0.2; pioarduino 55.03.312-1 / Arduino ESP32 3.3.12. BARROT Bluetooth 5.4 Adapter, VID_33FA/PID_0010, driver 17.55.6.566 was active. Intel remained disabled as requested; no driver updates or restarts were performed. Car 1/2/3 were verified on COM3/4/5 by USB serial identity before flashing.

All three Test 12 profiles compiled and uploaded successfully. Each image used 32,928 bytes of reported static RAM and 524,630 bytes of reported flash. These sizes are for the entire different application and do not isolate library resource use.

## Multiple-connection result

| Setup | Successful connection | Failed connections |
| --- | --- | --- |
| Concurrent, cars 1/2/3 | Car 3 | Cars 1 and 2 |
| Sequential 1 → 2 → 3 | Car 1 | Cars 2 and 3 |
| Sequential 3 → 2 → 1 | Car 3 | Cars 2 and 1 |

Every failed connection reported `BleakError: Could not get GATT services: Unreachable`. The connected server remained idle without commands or notifications, while the other servers stayed advertising. Serial logs confirmed NimBLE-Arduino 2.5.1 and `command_watchdog=0`. No three-car traffic phase ran, so the planned 120-second three-car observation was not achieved and its throughput cannot be inferred from these runs.

This reproduces the earlier idle experiment with different firmware and Python connection setup. It points toward the PC adapter/controller/driver or Windows multiple-link handling, but does not identify the exact fault or prove a hard hardware connection limit.

Each ESP32 needs only one connection to the PC. The PC central must maintain the three separate links. A server library's support for multiple peers therefore does not guarantee that this Windows adapter can connect to all three cars.

## Individual traffic measurements

Each individual run targeted 35 seconds of traffic, following scan, connection and idle setup. The aggregate rates include initial subscription/setup timing and the final partial window.

| Car | Measured seconds | Writes | Notifications | PC TX Hz | PC RX Hz | Mean echo age ms | Maximum echo age ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 35.13 | 1,753 | 702 | 49.90 | 19.98 | 53.27 | 81.91 |
| 2 | 35.09 | 1,753 | 702 | 49.96 | 20.01 | 57.72 | 108.67 |
| 3 | 35.07 | 1,752 | 701 | 49.96 | 19.99 | 53.98 | 81.88 |

All individual runs completed with zero notification sequence gaps, zero server command sequence gaps, zero malformed telemetry, zero write failures and zero reported server notification errors. Estimated server receive totals, calculated from rounded five-second `rate × window` measurements, matched the respective PC write totals exactly. Maximum notification gaps were approximately 91, 90 and 94 ms. Thus the mean notification rate was 20 Hz with some arrival-time jitter; it was not perfectly spaced at 50 ms.

Server captures reached zero command rate after shutdown. The last positive five-second report includes commands from before shutdown; it is not evidence of a queued backlog. These short individual runs validate operation and packet decoding, not two-hour endurance or three-car capacity.

## Validation and cleanup

Six new offline checks cover the binary sizes/field mapping, timestamped echo and sequence wrap, wrong-car/malformed packets, shared payload fan-out, propagation of write failures, and insufficient MTU rejection. All 48 repository checks passed after correcting an existing Test 10 check that assumed worker startup and two writes would fit into 110 ms. That fake session now ends after three observed writes with a generous safety timeout; production Test 10 behavior was unchanged.

All three normal `ble_server_car_N` profiles were rebuilt and uploaded successfully after the experiment. An independent nine-second serial capture verified `command_watchdog=1`, `advertising=1`, `connected=0`, zero restarts and zero notification errors on each board. All BLE clients, serial monitors and the experiment wrapper have exited. The final build removed the sample's deprecated `NimBLEService::start()` call, which is a no-op in 2.5.1; advertising already starts the GATT server.

## Evidence

All raw logs, server captures, upload logs and summaries are retained under [run_logs/test12/experiment_20261011_140436](runs/experiment_20261011_140436).

- [Concurrent summary](runs/experiment_20261011_140436/ble_test_12_concurrent_123_20261011_140536_411093.summary.json)
- [Sequential 1/2/3 summary](runs/experiment_20261011_140436/ble_test_12_sequential_123_20261011_140611_167209.summary.json)
- [Sequential 3/2/1 summary](runs/experiment_20261011_140436/ble_test_12_sequential_321_20261011_140658_114046.summary.json)
- Individual traffic summaries: [Car 1](runs/experiment_20261011_140436/ble_test_12_sequential_1_20261011_140744_654578.summary.json), [Car 2](runs/experiment_20261011_140436/ble_test_12_sequential_2_20261011_140846_493896.summary.json), [Car 3](runs/experiment_20261011_140436/ble_test_12_sequential_3_20261011_140948_043700.summary.json).
- [Firmware restoration checkpoint](runs/experiment_20261011_140436/firmware_state.json)
- [Aggregated measurements](runs/experiment_20261011_140436/measurements.json)
- Independent normal-firmware serial verification: [Car 1](runs/experiment_20261011_140436/ble_test_11_car_1_server_20261011_141405.log), [Car 2](runs/experiment_20261011_140436/ble_test_11_car_2_server_20261011_141405.log), [Car 3](runs/experiment_20261011_140436/ble_test_11_car_3_server_20261011_141405.log).

Reproduction instructions are in [README.md](../../../README.md). [run_test_12_experiment.py](../run_test_12_experiment.py) verifies this bench's USB identities, runs the six physical scenarios and restores normal firmware afterward. The individual Python runner does not flash firmware.
