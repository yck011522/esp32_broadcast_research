# Test 14: missing-start

Result: **passed**. Traffic duration: 60.002 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 522 | 0 | 0 | 0.082 | 33632.2 |
| 2 | 1,200 | 0 | 0 | 0.081 | 58.1 |
| 3 | 1,201 | 0 | 0 | 0.100 | 79.9 |
| 4 | 1,200 | 0 | 0 | 0.082 | 82.8 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_164049_995176.csv), [summary](ble_test_14_20261012_164049_995176.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
