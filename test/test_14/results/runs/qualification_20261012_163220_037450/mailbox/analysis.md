# Test 14: mailbox

Result: **passed**. Traffic duration: 35.002 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 700 | 0 | 0 | 0.100 | 72.2 |
| 2 | 700 | 0 | 0 | 0.062 | 41.1 |
| 3 | 701 | 0 | 0 | 0.081 | 60.1 |
| 4 | 700 | 0 | 0 | 0.100 | 81.5 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_163849_996372.csv), [summary](ble_test_14_20261012_163849_996372.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
