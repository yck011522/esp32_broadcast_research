# Test 14: reconnect

Result: **passed**. Traffic duration: 160.001 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 2,805 | 0 | 0 | 19.799 | 34493.2 |
| 2 | 2,951 | 0 | 0 | 12.499 | 57.5 |
| 3 | 2,915 | 0 | 0 | 14.360 | 78.2 |
| 4 | 2,812 | 0 | 0 | 19.459 | 85.3 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_163220_181194.csv), [summary](ble_test_14_20261012_163220_181194.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
