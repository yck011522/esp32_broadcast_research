# Test 14: car-watchdog

Result: **passed**. Traffic duration: 65.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 829 | 0 | 1 | 23.639 | 38331.7 |
| 2 | 1,300 | 0 | 0 | 0.079 | 55.2 |
| 3 | 1,301 | 0 | 0 | 0.081 | 60.9 |
| 4 | 1,300 | 0 | 0 | 0.080 | 64.2 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_163934_991619.csv), [summary](ble_test_14_20261012_163934_991619.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
