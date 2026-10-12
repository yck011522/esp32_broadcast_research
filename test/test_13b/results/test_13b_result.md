# Test 13b: four-car bench result

**Passed:** the COM3 ESP32 modem maintained four simultaneous BLE links for
120 seconds, sending 50 Hz world commands to each car and receiving 20 Hz
telemetry per car. All five boards were flashed with the shared Test 13 sources.
All car ports were monitored independently: COM4, COM5, COM7 and COM8.

| Port / car | Commands received | Notifications received | TX / RX rate | Mean echo age | Maximum echo age | Maximum telemetry gap |
|---|---:|---:|---|---:|---:|---:|
| COM4 / Car 2 | 6,000 | 2,400 | 50.00 / 20.00 Hz | 28.45 ms | 53.66 ms | 64.57 ms |
| COM5 / Car 3 | 6,000 | 2,400 | 50.00 / 20.00 Hz | 29.01 ms | 66.60 ms | 75.63 ms |
| COM7 / Car 1 | 6,000 | 2,400 | 50.00 / 20.00 Hz | 34.07 ms | 64.65 ms | 76.13 ms |
| COM8 / Car 4 | 6,000 | 2,400 | 50.00 / 20.00 Hz | 29.23 ms | 53.39 ms | 62.99 ms |

Each link negotiated 15 ms connection interval and MTU 64. All four were
subscribed within 4.4 seconds of starting; measurement began after fresh
car serial baselines were available. The original two car IDs were retained,
and the new IDs/MACs were confirmed directly in firmware health diagnostics.

There were zero command/notification sequence gaps, malformed frames, modem
queue drops, BLE write errors or car notification errors. Python's 6,000
world packets matched 6,000 modem writes and 6,000 independently counted car
receipts on every link: 24,000 total car deliveries. Raw packet replay and
CSV/summary/car counter reconciliation also passed with no check failures.

At the end, `#STOP` deliberately disconnected all four peers, the modem
reported disabled with zero links, all cars resumed advertising, and all
serial ports were closed. The four-car modem and car firmware remain installed.
Mean and maximum echo ages include USB, modem scheduling, BLE and the car's
notification timing; they are not pure radio RTT.

## Evidence

- [Run summary](runs/20261012_103357_239160_bench/ble_test_13b_20261012_103357_239160.summary.json)
- [CSV](runs/20261012_103357_239160_bench/ble_test_13b_20261012_103357_239160.csv)
- [Independent analysis and Matplotlib plots](runs/20261012_103357_239160_bench/analysis.md)
- [Source/dependency/build manifest](runs/20261012_103357_239160_bench/manifest.json)
- [Five-board flash record](runs/flash_20261012_102703/firmware_state.json)
- Car serial logs: [COM4](runs/20261012_103357_239160_bench/ble_test_13b_20261012_103357_239160_car_2_COM4.log),
  [COM5](runs/20261012_103357_239160_bench/ble_test_13b_20261012_103357_239160_car_3_COM5.log),
  [COM7](runs/20261012_103357_239160_bench/ble_test_13b_20261012_103357_239160_car_1_COM7.log),
  [COM8](runs/20261012_103357_239160_bench/ble_test_13b_20261012_103357_239160_car_4_COM8.log).

Raw JSONL is retained locally and excluded from Git. Five firmware builds
and **61 offline checks** passed. An older Test 9 logger check now uses a
simulated clock instead of a 50 ms real-time deadline; this removes a compile-
load-dependent test failure without changing its production logger.

## Subsequent tests

The distant 10-second run passed. Two planned one-hour runs failed, and a
controlled car power cycle exposed a modem-wide stall. The eight-hour test has
not been run. See [consolidated findings](findings.md) for evidence and the limits
of the short bench result. Remotely powered runs did not monitor car serial
ports independently. Test 13's previous two-car results remain separate.
