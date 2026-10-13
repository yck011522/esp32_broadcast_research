# Test 14: reconnect

Result: **failed**. Traffic duration: 160.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 2,947 | 0 | 0 | 12.704 | 27384.0 |
| 2 | 2,938 | 0 | 0 | 13.138 | 66.8 |
| 3 | 2,451 | 0 | 0 | 37.499 | 65.4 |
| 4 | 2,401 | 0 | 0 | 0.077 | 70.5 |

Validation failures: Car 3: healthy serial-observed command window outside 50±1 Hz; Car 3: healthy telemetry window outside 20±0.5 Hz; Car 4: healthy serial-observed command window outside 50±1 Hz; Car 4: healthy telemetry window outside 20±0.5 Hz; Car 4: missing connected/subscribed serial observation; Car 4: subscription recovery was not independently observed; Car 4: telemetry did not finish healthy; Car 4: traffic ended without fresh telemetry.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_162634_157943.csv), [summary](ble_test_14_20261012_162634_157943.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
