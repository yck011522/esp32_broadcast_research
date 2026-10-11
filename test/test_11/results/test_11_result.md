# Test 11 three-car hardware runs - 2026-10-10

All three boards were flashed and verified: Car 1 on COM3, Car 2 on COM4,
Car 3 on COM5. Each advertises its own service UUID and name. The computer
identified and connected to all three using one Bluetooth adapter, and Windows
reported a 15 ms interval with zero peripheral latency for every connection.

## First 120-second run: backlog

The PC submitted about 50 world writes per second per car and received about
20 notifications per second per car. The car serial logs initially showed
roughly 30-34 received writes per second per car. Echoed command age grew to
around 30 seconds. Later, the server receive rate rose above 60 Hz as it
drained the queued writes. Old commands continued arriving for about 66 seconds
after the PC runner exited. Zero sequence gaps did not mean fresh delivery.

The exact location of the queue is not established; the result demonstrates
that the original unguarded three-car run was unsuitable for timely control.

Test 11 now closes and retries a car's connection when its timestamped echo
age exceeds one second. It waits five seconds before rescanning. A separate
two-second notification watchdog also retries a silent connection.
`--max-echo-age-ms` and `--notification-timeout-seconds` adjust the limits.

## Repeat 120-second run: all three sustained the targets

A separate 30-second run held 50 Hz delivery with low echo age. A further
120-second run completed without triggering the one-second backlog guard.
The active measurement time was about 109-110 seconds after scanning and
connection establishment.

| Car | PC world writes | Estimated server receives | Notifications | Notification sequence gaps | Mean RTT estimate | Mean echo age |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 5,464 | 5,464 | 2,186 | 0 | 32.1 ms | 43.1 ms |
| 2 | 5,493 | 5,493 | 2,197 | 0 | 27.6 ms | 38.7 ms |
| 3 | 5,498 | 5,498 | 2,199 | 0 | 27.5 ms | 38.2 ms |

Server receives are estimated from the rounded five-second rates; their
rounded totals match the PC write counts in this run. Every server reported zero world
sequence gaps and settled to zero receive rate within the next five-second
window after the PC stopped. Full active server windows ranged from 49.2 to
50.8 Hz. The largest timestamped echo age in a five-second window was 274 ms.

The differing outcomes show that the setup can sustain three cars at the
target rates, but consistent startup behavior is not yet established. The
one-second guard makes overload visible and starts a fresh session.
A longer guarded run is still needed for an endurance claim.

The raw client and server logs from these earlier development runs were cleared
before the next Test 11 measurement. The measured findings remain recorded here.

## Reconnect validation

An 85-second run after the reconnect change started all three cars, then all
three exceeded the one-second echo-age limit at about 22 seconds. Each car
logged an overload, closed its connection, rescanned, connected a second time,
and resumed 50 Hz writes with 20 Hz notifications. All three summaries ended
`completed_with_recoveries` with two connections, one overload event, and no
BLE errors or notification sequence gaps.

The planned COM5 firmware reset could not be performed because Windows reported
the COM5 USB port unavailable at upload time. This run therefore validates
automatic recovery from overload on physical hardware; the unplugged-car path
is covered by offline watchdog/retry checks but was not physically reproduced.

The reconnect summary files referenced by the original report are no longer
retained: `ble_test_11_car_1_20261010_084317_607049.summary.json`,
`ble_test_11_car_2_20261010_084317_610050.summary.json`, and
`ble_test_11_car_3_20261010_084317_613050.summary.json`. They were already absent
before the folder migration.

## Latest two-hour run: common outage at 6.6 minutes

The run from 16:45:33 to 18:45:33 Hong Kong time connected all three cars once.
For about 6.4 minutes of active measurement per car, the PC submitted nearly
50 world writes per second and received nearly 20 notifications per second.
No notification sequence gaps or write failures were recorded before the
outage. The sample-weighted RTT estimates were 82.9, 84.6, and 91.0 ms for
Cars 1, 2, and 3 respectively.

The final notifications from all three cars arrived within 19 ms of each other
at approximately 16:52:07 Hong Kong time. Each notification watchdog fired
about two seconds later. The script kept rescanning until the two-hour deadline,
but did not establish another connection: each car has one connection and zero
successful reconnections. The summary statuses were corrected from the former
misleading `completed_with_recoveries` to `ended_disconnected`; the previous
status is retained in `status_before_correction`.

| Car | Notifications | World writes submitted | Scan/connect errors after outage |
| --- | ---: | ---: | ---: |
| 1 | 7,669 | 19,264 | 289 |
| 2 | 7,665 | 19,251 | 293 |
| 3 | 7,645 | 19,203 | 259 |

Most retry errors were "found 0 matching servers"; the others were scan
timeouts. A later PC scan found 22 other BLE advertisers but none of the three
car service UUIDs. COM4 and COM5 were absent from the current USB port list;
COM3 was present and its server firmware still printed zero-rate statistics.
The simultaneous outage points to a shared event, but the logs do not identify
whether it was power, USB, peripheral BLE state, or another cause. There are
no matching server serial logs for this two-hour run, so world-command
delivery/loss before the outage cannot be confirmed from the PC logs alone.

Latest analyses: [Car 1](runs/ble_test_11_car_1_20261010_084533_510849.analysis.md),
[Car 2](runs/ble_test_11_car_2_20261010_084533_513821.analysis.md),
[Car 3](runs/ble_test_11_car_3_20261010_084533_515819.analysis.md).
