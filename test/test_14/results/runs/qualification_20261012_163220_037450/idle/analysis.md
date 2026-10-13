# Test 14: idle

Result: **passed**. Traffic duration: 70.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 800 | 0 | 0 | 30.040 | 91.8 |
| 2 | 800 | 0 | 0 | 30.038 | 55.8 |
| 3 | 801 | 0 | 0 | 30.059 | 60.7 |
| 4 | 800 | 0 | 0 | 30.060 | 64.3 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_163619_996745.csv), [summary](ble_test_14_20261012_163619_996745.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
