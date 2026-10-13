# Test 14: bench_retry

Result: **passed**. Traffic duration: 120.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 2,401 | 0 | 0 | 0.077 | 67.7 |
| 2 | 2,400 | 0 | 0 | 0.066 | 51.4 |
| 3 | 2,400 | 0 | 0 | 0.075 | 68.7 |
| 4 | 2,400 | 0 | 0 | 0.091 | 58.8 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_162304_046234.csv), [summary](ble_test_14_20261012_162304_046234.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
