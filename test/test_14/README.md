# Test 14: transparent four-car ESP32 BLE modem

This experiment has separate firmware under `src/test_14` and results under
`test/test_14/results/runs`. Test 13/13b sources and captures are retained.
NimBLE-Arduino 2.5.1 and the existing pinned Arduino/PlatformIO platform are used.

WorldState USB framing is **literal `W` + 14 hexadecimal characters + newline**.
The modem strips `W` and writes exactly **seven bytes**, without padding, to
each car. All integer fields are little-endian. The existing **21-byte
RobotState** is forwarded as 42 hex characters plus newline. There are no extra
telemetry headers, receive counters, or wire session tokens. See
[payload.md](../../payload.md). `C` is reserved; configuration/acknowledgements
are not implemented in this experiment.

| Port | Device | Build profile |
|---|---|---|
| COM3 | Modem | `ble14_modem` |
| COM7 | Car 1 | `ble14_car_1` |
| COM4 | Car 2 | `ble14_car_2` |
| COM5 | Car 3 | `ble14_car_3` |
| COM8 | Car 4 | `ble14_car_4` |

The modem discovers logical IDs 1–4 through common-service advertising data:
protocol version 14 followed by the car ID. Physical MACs are learned at scan
time, not configured. One healthy connection per logical ID is retained; a
missing ID can bind to another physical device. Duplicate-ID selection is
first valid discovery, with no preference or authentication. A notification
with the wrong ID is discarded. Backup hardware takeover is not yet validated.

Missing cars trigger a two-second passive scan every five seconds when connection
setup is idle. Receive windows are 5 ms every 30 ms to fit around existing
20 ms connection events. Repeated advertisement callbacks are enabled; slots deduplicate
logical IDs. Connections are initiated asynchronously, one at a time, with
3 s controller timeout. Stuck connection/setup operations have cancellation
or disconnect deadlines and a hardware-backed task watchdog. Scanning shares
radio resources with existing links, so its effect must be measured.

One shared WorldState mailbox replaces older unsent snapshots. It expires
1,000 ms after USB reception. Per-peer delivery generations avoid repeating
the state during idle periods. Only sequence numbers of already-submitted
writes are retained in a bounded eight-entry credit window; existing echoed
world sequences release credits. This limits pressure from one weak link on
shared BLE buffers. There is no queue of eight WorldState payloads. If a car
stops returning telemetry during active command generation, delivery to that
peer pauses when its credits fill; BLE remains connected. Resumed echoes
release credits and allow the latest fresh snapshot through.

Application silence never disconnects BLE. The modem starts automatically,
has no `#START`/`#STOP`/`#STATUS` interface, and normally emits only telemetry
packets. Completing a Python run stops command generation and closes USB;
the modem and links remain running. Bounded feedback buffering and
`availableForWrite()` prevent an undrained USB port from blocking callbacks.
Runtime/framework fault text, if emitted, is classified separately by Python.

Each critical modem task and the car control loop must independently feed the
12 s hardware-backed task watchdog. A stuck worker cannot be masked by a live
main loop. A reset recovers automatically without a PC handshake. Python
reopens USB by verified device identity if Windows re-enumerates the device.
No traffic-based watchdog is used. A watchdog does not prove recovery from
every possible silent controller fault where application tasks still progress.

Cars blink their active-low built-in LED at 5 Hz while receiving worlds. The
callback only records activity; the loop changes GPIO at LED edges. The LED
turns off within about 100 ms after receipt stops. Occasional development
serial statistics are guarded by `availableForWrite()`. There are no motor
outputs; speed/battery/NFC fields remain synthetic test values.

Car-side USB commands used by controlled tests:

- `DISCONNECT 10`: disconnect and suppress advertising for ten seconds.
- `SILENCE 30`: pause notifications for thirty seconds while the loop and BLE
  connection continue running.
- `HANG`: deliberately stop feeding the car loop watchdog, which resets it.

From the repository root, close serial monitors before flashing/running:

```powershell
python -u test/test_14/flash_test_14.py
python -u test/test_14/test_14_ble_modem.py --duration-seconds 120 --label bench
python -u test/test_14/test_14_ble_modem.py --duration-seconds 160 --scenario reconnect --label reconnect_each_car
python -u test/test_14/test_14_ble_modem.py --duration-seconds 70 --scenario idle --label idle
python -u test/test_14/test_14_ble_modem.py --hours 1 --label stability_1h
python test/test_14/analyze_test_14.py
```

Other scenarios are `double`, `expiry`, `mailbox`, `car-watchdog`,
`missing-start`, and `modem-watchdog`. The last requires temporarily flashing
`ble14_modem_watchdog_test`, a separate profile that stalls the write worker
once after 45 seconds. Restore `ble14_modem` afterwards. No fault trigger is
enabled in the normal profile. Flash records capture partial failures and do
not claim restoration of unknown prior firmware.

Python measures telemetry rates, silence, inferred gaps, ambiguous sequence
changes and timestamped world echo age. Reset/replacement cannot always be
distinguished from sequence wrap or loss without metadata; ambiguous jumps
are accepted and reported instead of fabricated large loss totals.

Initial zero echoes after a possible reset/outage are treated as unconfirmed
until a nonzero echo arrives: zero before the first command cannot be uniquely
distinguished from a valid wrapped sequence zero. Normal continuous wraps
remain measured. No field is added to the packets.

The existing `world_packets_lost_5s` is saturated and counts plausible forward
sequence gaps, including superseded snapshots. Large timing-inconsistent jumps
are reported as discontinuities on development serial rather than huge loss.
Car serial counters provide development-only independent receive-rate checks.

Each run saves a streaming CSV, summary, source/dependency/build manifest and
car logs. Raw JSONL stays local and is ignored by Git. Plotting uses the retained
CSV and summary, so raw captures are not required. `--no-server-monitor` supports
remotely powered bench/stability observations. `--stop-file <new-path>` ends a
run cleanly; the file must not exist initially. Windows idle sleep is prevented
while the test runs.

`qualify_test_14.py --stability-hours 1` runs the controlled cases, restores
normal modem firmware after the fault profile, and starts stability only if
every earlier case passed. `audit_test_14.py <summary>` independently checks
the retained car observations for expiry, coalescing, idle connection retention
and watchdog recovery. Each completed case can be plotted separately.

Monitored runs wait for connected/subscribed car serial snapshots before
capturing baselines. Older captures that used stale disconnected snapshots
remain unchanged. `audit_test_14.py <summary> --reassess-stale-startup` can
independently recheck that narrow case; actual disconnects, resets, missing
receipts and other original failures cannot be waived. A separate reassessment
file records the source summary hash and the evidence, and plots display both
the original result and any verified reassessment.
