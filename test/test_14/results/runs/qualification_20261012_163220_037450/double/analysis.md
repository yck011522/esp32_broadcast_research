# Test 14: double

Result: **passed**. Traffic duration: 60.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 1,200 | 0 | 0 | 0.099 | 74.0 |
| 2 | 940 | 0 | 0 | 13.039 | 27717.9 |
| 3 | 924 | 0 | 0 | 13.859 | 62.4 |
| 4 | 1,200 | 0 | 0 | 0.081 | 47.5 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_163510_019946.csv), [summary](ble_test_14_20261012_163510_019946.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
