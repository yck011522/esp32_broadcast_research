# Test 14: logger_fix_validation

Result: **passed**. Traffic duration: 120.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 2,400 | 0 | 0 | 0.101 | 74.4 |
| 2 | 2,400 | 0 | 0 | 0.063 | 52.7 |
| 3 | 2,400 | 0 | 0 | 0.100 | 67.6 |
| 4 | 2,400 | 0 | 0 | 0.100 | 70.3 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_175124_605150.csv), [summary](ble_test_14_20261012_175124_605150.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
