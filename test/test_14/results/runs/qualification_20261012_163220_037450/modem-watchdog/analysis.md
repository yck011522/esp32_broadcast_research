# Test 14: modem-watchdog

Result: **passed**. Traffic duration: 90.000 seconds.

| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |
|---|---:|---:|---:|---:|---:|
| 1 | 1,640 | 81 | 0 | 8.063 | 11966.0 |
| 2 | 1,628 | 80 | 0 | 8.665 | 12009.4 |
| 3 | 1,615 | 81 | 0 | 9.285 | 11993.2 |
| 4 | 1,449 | 81 | 0 | 17.565 | 11976.5 |

Validation failures: none.

PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.

Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.

Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.

[CSV](ble_test_14_20261012_164307_417566.csv), [summary](ble_test_14_20261012_164307_417566.summary.json), [manifest](manifest.json).

![Rates and timing](plots/overview.png)
