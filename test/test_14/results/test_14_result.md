# Test 14: four-car communication and recovery results

**Communication evidence passed independent reassessment over one hour. All eight controlled recovery cases passed.** The original one-hour runner result is preserved as failed because it misclassified a stale, disconnected startup serial snapshot as an unexpected reconnect. The current logger waits for connected/subscribed baselines, and the separate reassessment verifies there were no actual traffic-time disconnects.

One-hour capture: 2026-10-13 00:45:08 to 2026-10-13 01:45:21 Hong Kong time, including setup and the final diagnostic drain. Traffic duration: **3600.001 seconds**.

| Car / port | Independently received commands | Telemetry received | Telemetry rate (Hz) | Mean echo age (ms) | Maximum echo age (ms) | Largest arrival gap (ms) |
|---|---:|---:|---:|---:|---:|---:|
| 1 / COM7 | 180,000 | 72,001 | 20.00027 | 36.20 | 130.36 | 159.70 |
| 2 / COM4 | 180,000 | 72,002 | 20.00055 | 34.15 | 65.02 | 100.96 |
| 3 / COM5 | 180,000 | 72,002 | 20.00055 | 34.65 | 111.22 | 120.20 |
| 4 / COM8 | 180,000 | 72,002 | 20.00055 | 34.64 | 95.79 | 120.93 |

Python submitted **180,000** worlds at 49.99999 Hz, with 0 pacing overruns. Every car received the same count. There were zero inferred telemetry sequence gaps, ambiguous sequence discontinuities or duplicate telemetry records. Car disconnect, world-loss and notification-error counters did not increase during this observation.

## Controlled cases

| Case | Result | Positive evidence |
|---|---|---|
| [reconnect](runs/qualification_20261012_163220_037450/reconnect/analysis.md) | passed; independent audit passed | All four cars individually returned after ten-second artificial outages; healthy streams retained their target rates. |
| [double](runs/qualification_20261012_163220_037450/double/analysis.md) | passed; independent audit passed | Cars 2 and 3 disappeared together and both returned. |
| [idle](runs/qualification_20261012_163220_037450/idle/analysis.md) | passed; independent audit passed | Thirty seconds without worlds or notifications; all four stayed connected/subscribed, with activity LEDs off and unchanged connect counts. |
| [expiry](runs/qualification_20261012_163220_037450/expiry/analysis.md) | passed; independent audit passed | Car 1 returned while worlds were paused. At roughly 30 and 35 seconds it was connected but had received no new world; the expired snapshot was not replayed. |
| [mailbox](runs/qualification_20261012_163220_037450/mailbox/analysis.md) | passed; independent audit passed | 1,949 serial submissions including a 200-frame burst produced only 1,752?1,753 receipts per car, demonstrating snapshot supersession rather than FIFO replay. |
| [car-watchdog](runs/qualification_20261012_163220_037450/car-watchdog/analysis.md) | passed; independent audit passed | A deliberate Car 1 main-loop hang produced an independently observed uptime reset and automatic recovery. |
| [missing-start](runs/qualification_20261012_163220_037450/missing-start/analysis.md) | passed; independent audit passed | Car 1 was absent initially; the other three operated, and Car 1 subsequently joined. |
| [modem-watchdog](runs/qualification_20261012_163220_037450/modem-watchdog/analysis.md) | passed; independent audit passed | The write worker stalled while the main loop continued. The watchdog reset the modem and every car rejoined without a PC handshake. |

## Implementation and development findings

USB WorldState framing is literal `W` plus 14 hex characters and newline. The car characteristic receives seven unchanged binary bytes, without header or padding. RobotState remains 21 bytes, including its original five-second world-loss field; no wire session token or receive counter was added.

The modem starts automatically and never uses application silence to disconnect. One pending shared WorldState expires after 1,000 ms. Logical IDs are discovered through common-service advertising data, with no configured BLE MACs. Fixed-period scans use short receive windows; asynchronous connection attempts and GATT setup have cancellation/disconnect deadlines, and critical tasks independently feed a hardware-backed watchdog.

An early 15 ms interval iteration maintained throughput but rediscovery could stall with three existing peers. A returning car sometimes appeared only after another peer disconnected. The qualified configuration requests 20 ms intervals and scans for two seconds every five seconds while a car is missing, using 5 ms receive windows every 30 ms. All subsequent controlled cases passed with these settings. This is an observation on the tested hardware/SDK, not a universal BLE scheduling rule.

A bounded eight-entry window of submitted sequence numbers uses existing echoes to limit pressure from one weak peer. It stores no queued WorldState payloads. If feedback stops during active command generation, submissions to that peer pause when credits fill, while BLE remains connected. Expiry applies to the modem mailbox; packets already submitted to the BLE controller cannot be recalled.

The car activity LED is serviced without delays or prints in receive callbacks. It blinks at 5 Hz during world reception and goes off within about 100 ms of inactivity. Car development serial output is guarded by available-for-write checks. The normal modem application emits only telemetry packets; debug and worker-stall builds are separate optional profiles. Normal firmware was restored before stability.

The original hour capture used a disconnected Car 1 snapshot taken before traffic. Every traffic-time Car 1 snapshot had the same connection count, its disconnect count never changed, and all 180,000 commands arrived. The narrow reassessment checks those facts and all remaining data/continuity criteria without modifying the original summary. Large initial zero-echo ages in the earlier expiry capture are unconfirmed matches to an old sequence zero; the current logger excludes ambiguous initial zeros after possible resets/outages.

## Limits and retained evidence

This is a local bench observation. Different-MAC backup takeover, distance operation and eight-hour endurance have not been validated. No motor outputs are implemented; NFC/speed/battery fields are synthetic. Sequence observations cannot identify every reboot/replacement/wrap case or isolate radio loss from intentional supersession. Echo age includes USB, scheduling, BLE and notification timing; it is not pure radio RTT.

[One-hour original summary](runs/qualification_20261012_163220_037450/stability/ble_test_14_20261012_164508_507394.summary.json), [CSV](runs/qualification_20261012_163220_037450/stability/ble_test_14_20261012_164508_507394.csv), [independent reassessment](runs/qualification_20261012_163220_037450/stability/startup_reassessment.json), [plots and detailed analysis](runs/qualification_20261012_163220_037450/stability/analysis.md), [original qualification record](runs/qualification_20261012_163220_037450/qualification.json).

Original captures and source/build manifests are preserved. Raw JSONL remains local and excluded from Git. Analysis uses retained CSVs and summaries. Serial ports are closed at completion; the transparent modem and BLE links remain running.

## Corrected logger validation

The corrected logger passed a fresh [two-minute bench run](runs/20261012_175124_605150_logger_fix_validation/analysis.md), starting from connected/subscribed serial snapshots for every car. A fresh [expiry run](runs/20261012_175431_396318_expiry_logger_fix/analysis.md) also passed, with initial zero echoes classified as unconfirmed rather than matched to an old sequence-zero timestamp. Both runs passed independent audits.

All five normal firmware profiles compiled in the final check, and **88 offline checks** passed. [Offline check output](offline_checks.log) and [build output](firmware_build.log) are retained. The normal four-car firmware remains installed; no debug or automatic fault-injection profile is left running.
