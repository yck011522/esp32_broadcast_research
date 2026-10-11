# USB Bluetooth adapter validation - 11 October 2026

Adapter: BARROT Bluetooth 5.4, USB VID_33FA/PID_0010, driver 17.55.6.566. Intel Bluetooth disabled.
All runs use default connection parameters, with no throughput request or interval gate. The target is 50 Hz world writes and 20 Hz notifications per car.

## Findings

Test 10 passed for Car 1. Test 11 maintained traffic only for Car 1; Cars 2 and 3 repeatedly failed with GATT `Unreachable` errors and never reported a connected event in their serial logs. Both remained advertising with no firmware restarts or notification errors. Each then passed an individual 40-second test, with shorter active time after discovery and setup.

All successful connections negotiated 30 ms with zero peripheral latency, verified in the server serial logs. A 30 ms interval therefore supported 50 Hz command delivery in these single-car runs. The first reported 7.5 ms interval was subsequently updated to 30 ms.

Across each successful run, estimated server receive totals from rounded five-second rates match the PC write-call totals, and both notification and world-command sequence-gap counters were zero. There was no sustained command drain after shutdown: the following reports reached zero rate, with silence showing that the last command arrived near the end of the corresponding test. The final report with a positive rate includes commands received before shutdown.

The failure reproduced during simultaneous operation, while all three cars worked individually. This points toward PC/adapter handling of multiple links or connection scheduling. It does not establish a hard one-link hardware limit or identify the precise driver/controller fault. A useful next diagnostic is to establish several idle BLE connections before starting traffic, and compare the outcome with serialized connection setup.

These are short validation runs, not an endurance result. COM3, COM4 and COM5 were monitored continuously for 390 seconds, with DTR and RTS deasserted. All runners and serial monitors finished and closed their connections.

## Measurements

| Run | Car | Status | Active seconds | PC TX Hz | PC RX Hz | Mean RTT estimate ms | Mean timestamped echo age ms | Notification gaps | Server command gaps |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Test 10 (120 seconds) | 1 | completed | 110.16 | 49.98 | 20.00 | 46.53 | 57.50 | 0 | 0 |
| Test 11 (120 seconds) | 1 | completed | 109.91 | 49.99 | 20.00 | 44.18 | 55.73 | 0 | 0 |
| Test 11 (120 seconds) | 2 | no_connection | 0.00 | n/a | n/a | n/a | n/a | 0 | 0 |
| Test 11 (120 seconds) | 3 | no_connection | 0.00 | n/a | n/a | n/a | n/a | 0 | 0 |
| Car 2 alone (40 seconds) | 2 | completed | 30.15 | 49.98 | 19.97 | 43.43 | 52.51 | 0 | 0 |
| Car 3 alone (40 seconds) | 3 | completed | 19.80 | 49.84 | 20.00 | 50.63 | 60.02 | 0 | 0 |

Rates are weighted by measured traffic-window duration. A run includes scan/setup time in addition to active time. Zero counters for a run that never connected do not establish successful delivery.
RTT is the existing sequence-based estimate at 20 ms resolution. Timestamped echo age measures elapsed host time from the corresponding write attempt to its notification echo. Both include server echo sampling and return delivery.

## Test 10 (120 seconds) - Car 1

Sources: [PC CSV](../test_10/results/runs/ble_test_10_20261011_130830_837302.csv), [PC summary](../test_10/results/runs/ble_test_10_20261011_130830_837302.summary.json), [serial log](../test_11/results/runs/ble_test_11_car_1_server_20261011_130827.log).

Connections: 1; BLE errors: 0; write failures: 0.
Maximum notification gap: 119.95 ms. Maximum timestamped echo age: 115.39 ms.
Complete overlapping server windows: 21; mean command rate 50.00 Hz; range [49.6, 50.4] Hz.
Estimated server commands from rounded five-second reports, including startup/shutdown windows: 5506. PC write calls: 5506.

Server events:

```text
2026-10-11T13:08:39.301+00:00 [12.250s] ble event: uptime_ms=3470318 event=connected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:08:39.997+00:00 [12.946s] ble event: uptime_ms=3471008 event=connection_parameters status=0 interval=7.50 ms latency=0 timeout=9600 ms
2026-10-11T13:08:41.800+00:00 [14.749s] ble event: uptime_ms=3472808 event=connection_parameters status=0 interval=30.00 ms latency=0 timeout=9600 ms
2026-10-11T13:10:33.977+00:00 [126.925s] ble event: uptime_ms=3584982 event=gap_disconnect status=531 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:10:33.977+00:00 [126.926s] ble event: uptime_ms=3584982 event=disconnected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:10:34.467+00:00 [127.416s] ble event: uptime_ms=3585483 event=advertising_restart status=0 interval=0.00 ms latency=0 timeout=0 ms
```

## Test 11 (120 seconds) - Car 1

Sources: [PC CSV](../test_11/results/runs/ble_test_11_car_1_20261011_131039_664766.csv), [PC summary](../test_11/results/runs/ble_test_11_car_1_20261011_131039_664766.summary.json), [serial log](../test_11/results/runs/ble_test_11_car_1_server_20261011_130827.log).

Connections: 1; BLE errors: 0; write failures: 0.
Maximum notification gap: 150.23 ms. Maximum timestamped echo age: 136.82 ms.
Complete overlapping server windows: 21; mean command rate 50.00 Hz; range [49.8, 50.4] Hz.
Estimated server commands from rounded five-second reports, including startup/shutdown windows: 5494. PC write calls: 5494.

Server events:

```text
2026-10-11T13:10:48.070+00:00 [141.019s] ble event: uptime_ms=3599082 event=connected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:10:48.972+00:00 [141.921s] ble event: uptime_ms=3599982 event=connection_parameters status=0 interval=7.50 ms latency=0 timeout=9600 ms
2026-10-11T13:10:50.920+00:00 [143.868s] ble event: uptime_ms=3601932 event=connection_parameters status=0 interval=30.00 ms latency=0 timeout=9600 ms
2026-10-11T13:12:42.815+00:00 [255.764s] ble event: uptime_ms=3713836 event=gap_disconnect status=531 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:12:42.815+00:00 [255.764s] ble event: uptime_ms=3713836 event=disconnected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:12:43.329+00:00 [256.278s] ble event: uptime_ms=3714337 event=advertising_restart status=0 interval=0.00 ms latency=0 timeout=0 ms
```

## Test 11 (120 seconds) - Car 2

Sources: [PC CSV](../test_11/results/runs/ble_test_11_car_2_20261011_131039_666271.csv), [PC summary](../test_11/results/runs/ble_test_11_car_2_20261011_131039_666271.summary.json), [serial log](../test_11/results/runs/ble_test_11_car_2_server_20261011_130827.log).

Connections: 0; BLE errors: 7; write failures: 0.
Maximum notification gap: n/a ms. Maximum timestamped echo age: n/a ms.
Estimated server commands from rounded five-second reports, including startup/shutdown windows: 0. PC write calls: 0.

Errors:
- `BleakError: Could not get GATT services: Unreachable`
- `RuntimeError: Car 2: found 0 matching servers`
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `RuntimeError: Car 2: found 0 matching servers`

Server events:

```text
```

## Test 11 (120 seconds) - Car 3

Sources: [PC CSV](../test_11/results/runs/ble_test_11_car_3_20261011_131039_669319.csv), [PC summary](../test_11/results/runs/ble_test_11_car_3_20261011_131039_669319.summary.json), [serial log](../test_11/results/runs/ble_test_11_car_3_server_20261011_130827.log).

Connections: 0; BLE errors: 6; write failures: 0.
Maximum notification gap: n/a ms. Maximum timestamped echo age: n/a ms.
Estimated server commands from rounded five-second reports, including startup/shutdown windows: 0. PC write calls: 0.

Errors:
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `BleakError: Could not get GATT services: Unreachable`
- `TimeoutError: `

Server events:

```text
```

## Car 2 alone (40 seconds) - Car 2

Sources: [PC CSV](../test_10/results/runs/ble_test_10_20261011_131312_153512.csv), [PC summary](../test_10/results/runs/ble_test_10_20261011_131312_153512.summary.json), [serial log](../test_11/results/runs/ble_test_11_car_2_server_20261011_130827.log).

Connections: 1; BLE errors: 0; write failures: 0.
Maximum notification gap: 119.84 ms. Maximum timestamped echo age: 123.61 ms.
Complete overlapping server windows: 5; mean command rate 50.00 Hz; range [49.8, 50.4] Hz.
Estimated server commands from rounded five-second reports, including startup/shutdown windows: 1507. PC write calls: 1507.

Server events:

```text
2026-10-11T13:13:20.585+00:00 [293.533s] ble event: uptime_ms=1181840 event=connected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:13:21.281+00:00 [294.229s] ble event: uptime_ms=1182537 event=connection_parameters status=0 interval=7.50 ms latency=0 timeout=9600 ms
2026-10-11T13:13:23.088+00:00 [296.037s] ble event: uptime_ms=1184330 event=connection_parameters status=0 interval=30.00 ms latency=0 timeout=9600 ms
2026-10-11T13:13:55.298+00:00 [328.247s] ble event: uptime_ms=1216551 event=gap_disconnect status=531 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:13:55.298+00:00 [328.247s] ble event: uptime_ms=1216552 event=disconnected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:13:55.809+00:00 [328.758s] ble event: uptime_ms=1217053 event=advertising_restart status=0 interval=0.00 ms latency=0 timeout=0 ms
```

## Car 3 alone (40 seconds) - Car 3

Sources: [PC CSV](../test_10/results/runs/ble_test_10_20261011_131400_060557.csv), [PC summary](../test_10/results/runs/ble_test_10_20261011_131400_060557.summary.json), [serial log](../test_11/results/runs/ble_test_11_car_3_server_20261011_130827.log).

Connections: 1; BLE errors: 1; write failures: 0.
Maximum notification gap: 90.37 ms. Maximum timestamped echo age: 111.45 ms.
Complete overlapping server windows: 3; mean command rate 49.93 Hz; range [49.6, 50.2] Hz.
Estimated server commands from rounded five-second reports, including startup/shutdown windows: 987. PC write calls: 987.

Errors:
- `RuntimeError: Found 0 matching servers; power on the car or select --address`

Server events:

```text
2026-10-11T13:14:18.480+00:00 [351.429s] ble event: uptime_ms=1248647 event=connected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:14:19.454+00:00 [352.403s] ble event: uptime_ms=1249607 event=connection_parameters status=0 interval=7.50 ms latency=0 timeout=9600 ms
2026-10-11T13:14:21.406+00:00 [354.355s] ble event: uptime_ms=1251557 event=connection_parameters status=0 interval=30.00 ms latency=0 timeout=9600 ms
2026-10-11T13:14:43.215+00:00 [376.164s] ble event: uptime_ms=1273369 event=gap_disconnect status=531 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:14:43.215+00:00 [376.164s] ble event: uptime_ms=1273369 event=disconnected status=0 interval=0.00 ms latency=0 timeout=0 ms
2026-10-11T13:14:43.707+00:00 [376.655s] ble event: uptime_ms=1273870 event=advertising_restart status=0 interval=0.00 ms latency=0 timeout=0 ms
```
