# Four-car power-cycle recovery observation

**Car 4 was power-cycled. Recovery was unsuccessful.** The user confirmed that
only one car was unplugged, all four cars subsequently had power, and COM3
remained connected. No firmware was changed or reset during this observation.

Elapsed times below start at Python launch, including the initial setup.

| Elapsed time | Observation |
|---|---|
| 6.1 s | All four subscriptions ready, 15 ms intervals. |
| 11–56 s | Approximately 50 Hz PC commands and 20 Hz telemetry per car, zero observed telemetry sequence gaps. |
| 57.773 s | Last Car 4 telemetry. Car 4 address is `68:ee:8f:4b:4f:35`, formerly COM8. |
| 59–61.9 s | Other three cars continued sending telemetry but echoed old commands. Fresh echoes returned around 61.9 s. |
| 61.810 s | Car 4 disconnect reported, reason 520. The modem began retrying without Python stopping the other links. |
| 66.9–134.0 s | Car 4 connection attempts repeatedly failed with logged error 13, roughly every 6.1 s. No subsequent Car 4 `ready` event appeared. |
| 62–135 s | Other cars continued near 20 Hz with mean echo ages approximately 30–38 ms. |
| 132.29 s | Last modem status snapshot; three links ready. |
| 135.071–135.078 s | Last telemetry from Cars 2, 3 and 1. |
| 135.105 s | Last modem diagnostic: another Car 4 connection attempt. |
| 204.8 s | Observation stopped cleanly on the Python side using its stop file; CSV and summary saved. No modem stop acknowledgement arrived. |

The PC submitted 9,935 commands over 198.698 seconds of traffic. Its continued
50 Hz serial writes after the stall prove submission to the Windows serial
interface, not processing by the modem or receipt by a car. Last-known modem
counters are stale and cannot be treated as final receive totals.

A separate three-second `#STATUS` probe after closing the test returned zero
bytes from COM3. This supports investigating the modem firmware/BLE host or
USB output path. The precise cause is not established by these logs; the last
connection attempt alone does not prove a particular deadlock.

The experiment demonstrates that Python recovery mode allows modem retries
and keeps other traffic running through the initial car outage. It does not
demonstrate successful reconnection or isolation from a later modem-wide stall.
Firmware diagnostics and recovery changes should be developed on COM3 before
repeating this controlled power cycle. Car firmware changes are not yet shown
to be necessary.

## Capture interpretation

The original capture is preserved. Its status `observation_completed` means
Python finished recording; `performance_passed=false` and
`recovery.all_outages_recovered=false` report the failure. `ready_at_end=true`
for three peers is their last observed link state, not a fresh health check.

Two logger issues exposed by this run have been fixed for subsequent runs:
bounded timestamp eviction no longer clears an unresolved stale-echo warning,
and missing fresh shutdown acknowledgement now makes the run fail explicitly.
The original summary's `telemetry_gap_s` on recovered stale-command episodes
measures time since the episode's pre-outage notification, not an actual
notification gap: the other cars were still notifying. New runs record the
maximum actual telemetry gap observed during each episode instead.

[CSV](ble_test_13b_20261012_135434_260822.csv),
[summary](ble_test_13b_20261012_135434_260822.summary.json),
[manifest](manifest.json), [console](console.log).
