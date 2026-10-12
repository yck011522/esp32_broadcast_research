# Test 13b findings: four-car BLE modem

Four connected cars sustain 50 Hz commands and 20 Hz telemetry in short tests.
The current firmware has not demonstrated reliable operation at distance or
successful automatic recovery from a car power cycle.

All runs below use the shared Test 13 firmware, the four-peer modem profile,
and Cars 2, 3, 1 and 4. Car receive totals were independently monitored only
in the bench run. Remote runs use COM3 telemetry and modem counters.

| Run | Measured traffic duration | Result |
|---|---:|---|
| [Bench](runs/20261012_103357_239160_bench/analysis.md) | 120.001 s | Passed: each car received 6,000 commands and returned 2,400 notifications. |
| [Distance, 10 s](runs/20261012_104044_595409_far_10s/ble_test_13b_20261012_104044_595409.summary.json) | 10.001 s | Passed: all four streams near 50/20 Hz. |
| [Distance, first planned hour](runs/20261012_104324_990989_far_1h/ble_test_13b_20261012_104324_990989.summary.json) | 1,221.270 s | Failed: Car 2 command echo exceeded the then-current 1 s limit; Car 1 had stopped notifying. |
| [Distance, planned hour retry](runs/20261012_111738_858586_far_1h_retry/ble_test_13b_20261012_111738_858586.summary.json) | 3,524.745 s | Failed: Car 1 disconnected; strict Python mode stopped all links. |
| [Power-cycle recovery](runs/20261012_135434_008559_recovery_powercycle/recovery_findings.md) | 198.698 s | Failed recovery: Car 4 disappeared, connection retries continued, then all modem output stopped. |

## Distance failure evidence

During the hour retry, Python submitted 176,238 commands and the modem's serial
receive counter increased by the same amount. Serial sequence, framing and
queue-drop counters did not increase. Per-car BLE write errors increased by
186–187, while the other cars continued notifying with increasingly old
command echoes. This places the observed command failure after the PC-to-modem
serial receive path. Successful serial submission is not proof of car receipt.

Car 1 then disconnected with logged reason 520. The modem started reconnecting
and its connection count increased, but the Python test sent `#STOP` before a
new ready/subscribed state was demonstrated. That run therefore cannot establish
whether a complete reconnect would have succeeded. Raising the age limit to
5 seconds allowed longer observation, but did not remove the underlying delay.

Shared BLE transmit-resource pressure is a possible explanation for several
cars' command delays during one weak link. It remains a hypothesis; the current
logs do not prove a particular buffer exhaustion or deadlock.

## Recovery observation

Python recovery mode keeps transmitting, records warnings/outages, and resets
only a reconnected car's measurement session. Strict mode remains available.
The controlled observation identified Car 4 as the unplugged car. The user
confirmed all cars were powered again and the master had remained connected.

The other three cars continued notifying during an initial command echo delay
of about 3.8 seconds, then returned to fresh echoes. Car 4 retries failed
repeatedly; no ready event followed. Around 135 seconds after launch, telemetry
from all other cars and modem diagnostics also stopped. A later independent
`#STATUS` check on COM3 received no bytes. The exact cause of this modem-wide
stall is unconfirmed. Details and capture interpretation are in the
[recovery findings](runs/20261012_135434_008559_recovery_powercycle/recovery_findings.md).

The old capture's `observation_completed` means recording finished, not that
recovery passed. Its `performance_passed` and `all_outages_recovered` are false.
The current runner additionally requires a fresh modem shutdown acknowledgement
and retains stale-echo warnings after its bounded timestamp lookup expires.

## Reproducibility and current state

CSVs, original summaries/manifests, short console/car logs, plots and available
firmware snapshots are preserved. High-volume JSONL packet captures remain local
and are excluded from Git; do not infer missing car receipt counts from PC TX.
The bench report records the successful short experiment, not endurance proof.
An eight-hour run has not been attempted.

No reconnect firmware changes or reflashes were made during the Python recovery
observation. The serial port was closed after saving results, but the modem did
not acknowledge shutdown and requires a reset before further hardware tests.
