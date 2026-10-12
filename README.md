The purpose of this repository is to test communication between a master radio and multiple slave radios for real time control of up to four RC cars.

Experiment scripts, plotting tools and results are grouped under numbered
folders in [test/README.md](test/README.md). Available firmware mappings
are listed in [src/README.md](src/README.md). The current ESP32 modem
experiments are [Test 13](test/test_13/README.md) with two cars and
[Test 13b](test/test_13b/README.md) with four cars, sharing firmware sources.

## Goal

Control signals sent from computer to all four cars with low latency (trusting ESP-NOW) and high reliability (loss of control gap < 200ms).

An absent car must not disrupt healthy cars.

## Hardware System

- Windows Computer connected to Master Radio via USB.
- 1 Master Radio (ESP32-S3)
- 4 Slave Radios (ESP32-S3)

## Test Summary

- OTA upload listener can be used on the slave devices without affecting the low latency ESP-NOW communication.
  - WiFi.setSleep(false); // To keep the radio awake for low latency ESP-NOW reception.
- Unicast Master is not recommended because missing targets can cause huge world loss for healthy slaves.
  - Use a broadcast master to send World State to all slaves.


## Test Results

Slaves with OTA listener needs to turn off power save mode to avoid long silence periods.

| Phase              | World packets sent | Telemetry received | Telemetry loss | World loss (slave-reported) | Max silence |
|--------------------|--------------------|--------------------|----------------|-----------------------------|-------------|
| OTA listener (v1)  | 1500 @ 50 Hz       | 604                | 0.000 %        | 705 (47.0 %)                | 1.34 s      |
| OTA listener       | 1500 @ 50 Hz       | 568                | 0.000 %        | 512 (34.1 %)                | 3.07 s      |
| OTA no Power Save  | 1500 @ 50 Hz       |                    | 0.000 %        | 304 (20%)                   | 0.12 s      |

**Unicast Master Test**

Using a Unicast Master to send World State:

| Configuration               | Rate  | World loss | Worst consecutive loss | Telemetry lost |
| --------------------------- | ----- | ---------- | ---------------------- | -------------- |
| Four addresses, two absent  | 25 Hz | 6.01%      | 5                      | 0              |
| Four addresses, two absent  | 50 Hz | 42.87%     | 57                     | 220            |
| Two addresses commented out | 50 Hz | 12.15%     | 17                     | 1              |

Observation is that the presence of missing unicast targets creates a huge world loss for the healthy slaves.
Interpretation: The ESP NOW unicast automatic retry consumes too much time. Since only one slave device can be served at one single time, the master radio is blocked from sending to the healthy slaves.

Recommendation: Use a broadcast master to send World State to all slaves.


## Test Results in LAB environment - Broadcast Master , 2 slave radios, Wifi AP nearby
[2026-10-08T12:29:36] 0.017 h | interval 1 | reset confirmed [1, 2] (11 attempts)
Slave 1: OK | world loss 86.2122% | max gap 208 | telemetry lost 472 | max telemetry silence 2.113s
Slave 2: OK | world loss 86.7052% | max gap 161 | telemetry lost 681 | max telemetry silence 1.647s

[2026-10-08T12:32:43] 0.017 h | interval 1 | reset confirmed [1, 2] (18 attempts)
Slave 1: OK | world loss 87.6118% | max gap 208 | telemetry lost 426 | max telemetry silence 2.063s
Slave 2: OK | world loss 84.4976% | max gap 161 | telemetry lost 668 | max telemetry silence 1.623s

## BLE bidirectional car/computer test

`src/test_11/ble_server/main.cpp` represents the car (BLE peripheral/server).
`src/test_09/ble_client/main.cpp` represents the computer side, using an ESP32-S3
connected to the computer over USB (BLE central/client). The client sends dummy
world-state commands at 50 Hz, while the server sends notifications at 20 Hz
when subscribed. No dummy command is applied to a motor.

### Build and flash

Server upload port: COM11. Client upload port: COM13 (`platformio.ini`).

```powershell
pio run -e ble_server -e ble_client
pio run -e ble_server -t upload
pio run -e ble_client -t upload
```

Start the server before the client. Serial monitors use 115200 baud. The server
resumes advertising after disconnect; the client scans, reconnects, discovers
both characteristics, and subscribes again. Pairing is not required.

### GATT interface

The Car 1 server advertises `ble_car_1` and service UUID
`7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001`. The client discovers the characteristics
inside that service after connecting.

| Characteristic | UUID | Direction | Rate |
| --- | --- | --- | --- |
| Telemetry notification | `7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001` | Server to subscribed client | 20 Hz |
| World state, write without response | `7a1e0003-4b6d-4f7a-9c2e-6d3b1a5f0001` | Client to server | 50 Hz |

World state is exactly 20 binary bytes. The server ignores commands of other
lengths in its processing and statistics.

| Bytes | World-state contents |
| --- | --- |
| 0-1 | `world_seq_n`: unsigned 16-bit sequence, little-endian |
| 2-19 | 18 random dummy bytes |

Each notification is exactly four binary bytes:

| Bytes | Notification contents |
| --- | --- |
| 0-1 | `server_seq_n`: unsigned 16-bit notification sequence, little-endian |
| 2-3 | Latest accepted `world_seq_n`, little-endian |

Both sequences wrap from 65535 to zero. The server sequence starts at zero on
boot and advances on each notification attempt. The client world sequence starts
at zero per connection and advances on each write attempt, including local
write failures. The echoed world sequence is zero until the first valid command;
zero by itself cannot distinguish that condition from a received sequence zero.

### Five-second statistics

Monitor COM11 for the server's three world-state measurements:

```text
world stats: rate=50.00 Hz seq_lost=0 silence=0.012 s
```

`rate` is accepted forward commands divided by the actual reporting duration.
`seq_lost` counts missing sequence values inferred between accepted commands.
`silence` is the time since the latest accepted command, or since connection
start if none has arrived. Sequence baselines and world reporting counters reset
on connection. Window counters reset every five seconds, but the last sequence
and receive timestamp persist across windows. Duplicates, backward sequences,
and malformed commands are ignored.

Monitor COM13 for notification receive rate, sequence loss, completed receive
gaps, current silence, and the latest echoed world sequence. Expect about 100
notifications and 250 world commands per five seconds on a steady healthy link.
Neither sequence loss metric counts send slots missed because a sending loop
paused. Missing packets after the last received packet are inferred only when
a later sequence arrives. The 16-bit forward-gap calculation assumes fewer than
32768 sequence increments between valid received packets; longer gaps are
ambiguous. The echo samples world reception at 20 Hz, so skipped echo values do
not indicate world loss when commands arrive at 50 Hz.

The server interval is `PACKET_INTERVAL_MS = 50`; the client world interval is
`WORLD_INTERVAL_MS = 20`. Scheduling can vary actual timings. Both loops avoid
catch-up bursts. USB transmit timeouts are short and periodic reports are skipped
when the USB transmit buffer lacks room, to avoid diagnostic logging stalls.

### Rough round-trip estimate

The client samples RTT when a notification arrives, using
`uint16_t(latest_attempted_world_seq - echoed_world_seq) * 20 ms`.
It reports the average, maximum, and sample count for each five-second window.
The subtraction handles sequence wrap; ambiguous differences of 32768 or more
are excluded. Estimation starts after a nonzero world echo, since the server's
initial zero does not confirm receipt of a command. State resets on reconnect.

This measures the approximate age of the echoed command at the client, with
20 ms resolution, assuming steady 50 Hz generation. It can underestimate age
by nearly one send interval; zero means less than one interval, not zero delay.
It includes any server wait before the echo is sampled into a notification.
Sender stalls or old echoes caused by failed writes can distort this estimate.
No server changes or packet-format changes are needed.

### Test 9: two-hour BLE USB log

Close any serial monitor using COM4, then run:

```powershell
python -m pip install pyserial
python -u test/test_09/test_9_ble_test.py
```

Defaults are COM4, 115200 baud, and two hours. To try a short run or another port:

```powershell
python -u test/test_09/test_9_ble_test.py --duration-seconds 30
python -u test/test_09/test_9_ble_test.py --port COM13 --hours 2
```

Files are saved under each numbered test’s `results/runs/` folder with a unique UTC timestamp:

- `.csv`: one `ble_stats` row per five-second report, containing notification
  and RTT measurements together, UTC timestamps, monotonic elapsed seconds,
  and serial connection numbers. Empty RTT values mean unavailable measurements,
  not zero latency.
- `.jsonl`: every serial line, including startup/reconnection messages, plus
  logger connection/error events and any incomplete trailing line.
- `.summary.json`: run settings, progress, report counts, and completion status.

Reports are flushed to disk as they arrive. Ctrl+C saves a partial run. Serial
errors trigger a reconnect attempt every five seconds without extending the
requested duration. Windows automatic idle sleep is prevented during the run;
keep the laptop lid open and powered. The script only reads USB output and does
not send commands. It also accepts the older two-line output, preserving those
legacy notification and RTT rows separately.

The client firmware emits only one application statistics line per window:

```text
BLE_STATS window_s=5.000 received=100 seq_lost=0 loss_percent=0.00 rate_hz=20.00 max_gap_s=0.199 silence_s=0.051 have_packet=1 world_seq=3243 rtt_avg_ms=84.0 rtt_max_ms=180 rtt_samples=100
```

Keys include units, and values contain no unit suffixes. Unavailable RTT is
`rtt_avg_ms=NA rtt_max_ms=NA rtt_samples=0`. Client startup, scan, connection,
and error prints have been removed; board boot output may still appear and is
preserved in the raw log. Incomplete statistics lines are retained in the raw
log but excluded from numeric CSV rows. Generated filenames start with
`ble_test_9_`. Reflash the client before running this test.

To analyze the newest Test 9 run and save plots plus a Markdown/JSON report:

```powershell
python -m pip install matplotlib
python test/test_09/analyze_test_9.py
```

An optional CSV path selects a specific run. Plots distinguish window-average
RTT from window-maximum RTT; these are not individual-packet percentiles.

### Test 10: computer Bluetooth adapter as the client

Keep the existing car `ble_server` firmware. Power off the ESP32 client board,
enable Bluetooth on the computer, and run from a terminal:

```powershell
python -m pip install bleak
python -u test/test_10/test_10_ble_pc.py --duration-seconds 30
python -u test/test_10/test_10_ble_pc.py --hours 2
```

Python 3.11 or newer is required. The default duration is two hours. This test uses the computer's BLE adapter
directly, with no COM port. It scans for the advertised service, validates both
characteristics, subscribes to 20 Hz telemetry, and writes exactly 20 bytes at
50 Hz using write-without-response. Multiple matching cars require selecting
one with `--address XX:XX:XX:XX:XX:XX`. BLE failures trigger scan/reconnect
attempts within the original run deadline. Ctrl+C preserves partial results.
Windows idle sleep is prevented while the script runs.

Tests 10 and 11 use the adapter's default connection parameters unless
`--connection-mode throughput` is supplied. Default mode sends no Windows
throughput request and imposes no connection-interval check, so an adapter that
negotiates 30 ms can still be measured at the same 50 Hz write target.
The selected mode is recorded in each summary JSON. No firmware change is required.

On Windows 11, `--connection-mode throughput` uses the public WinRT
`ThroughputOptimized` preference alongside Bleak, verifies the negotiated
interval, and holds the preference request for the session. On the original Intel
adapter the preset requests 15 ms with zero peripheral latency. Failure to obtain
that preset is logged as an error and triggers reconnection in this opt-in mode.

Output files under each numbered test’s `results/runs/` folder start with `ble_test_10_`: numeric CSV,
raw notification/event JSONL, and checkpointed summary JSON. Five-second rows
use the Test 9 notification fields plus actual world write rate, local write
failures, maximum send gap, and maximum write API duration. Reconnection and
final partial windows are marked with `partial_window=1`; session sequence
baselines reset on reconnect. Notification gap and silence use Python callback
arrival times, including Windows/Python scheduling, rather than ESP32 timestamps.

`rtt_avg_ms`/`rtt_max_ms` reproduce the Test 9 sequence-based estimate.
`echo_age_avg_ms`/`echo_age_max_ms` use the echoed world's recorded monotonic
timestamp immediately before its Python write call. This includes OS queuing,
BLE transport, and server echo timing; it is not a pure radio RTT. Initial zero
echoes are excluded until a nonzero command echo confirms reception. The raw log
preserves each notification payload and its RTT/age sample for later analysis.
Writes are paced without recovery bursts; inspect `world_tx_rate_hz` and
`max_tx_gap_s` to see whether the computer maintained 50 Hz.
Windows pacing uses Python's high-resolution sleep in a worker thread so the
event loop can continue receiving notifications. Timer precision and idle-sleep
settings are restored when the run exits.

Plot the newest Test 10 log with the shared BLE analysis:

```powershell
python -m pip install matplotlib
python test/test_10/analyze_test_10.py
```

The plots show notification rate/loss, receive timing, and sequence-based RTT.
Server world-state loss still requires its serial statistics; sampled echoed
world sequences do not establish that all world commands were received.

### Optional Test 10 server log and comparison with Test 9

To record the car's command receive rate, sequence losses, and silence, close
other COM3 monitors and start this in a separate terminal before the BLE runner:

```powershell
python -m pip install pyserial
python -u test/test_10/test_10_monitor_server.py --port COM3
```

It saves `ble_test_10_server_*.log` and defaults to two hours plus one minute
for setup. Use `--seconds` to change this, or Ctrl+C to stop.

For the two-hour experiment, run `python -u test/test_10/test_10_ble_pc.py --hours 2`.
Afterward run `python test/test_10/analyze_test_10.py` to plot the newest Test 10 CSV.
The original Test 9 logs and analysis remain available via
`python test/test_09/analyze_test_9.py`. Both analyses use the same notification rate,
sequence-loss, gap, silence, and sequence-based RTT fields. Compare runs of
similar duration under the same conditions. PC callback timing and ESP32 timing
are different measurement points; timestamp-based echo age is Test 10 only.
The client TX rate counts write attempts; use the server log for delivered
command rate/loss. The two-hour duration includes scanning and reconnect time.

The successful short validation and its logs are retained in
`test/test_10/results/test_10_throughput_result.md`. Development experiments and failed Test 10
logs have been removed. Measurement and connection-lifetime regression checks
remain in `test/test_10/test_10_checks.py`.

### Test 11: three cars on the computer BLE adapter

Test 11 runs the same 50 Hz world writes and 20 Hz notification measurements
for three cars at once. Each firmware profile builds the same
`src/test_11/ble_server/main.cpp`; `BLE_CAR_ID` selects its advertised name and service
UUID. The characteristic UUIDs and payloads remain the same on all cars.

| Profile | Advertised name | Service UUID |
| --- | --- | --- |
| `ble_server_car_1` | `ble_car_1` | `7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001` |
| `ble_server_car_2` | `ble_car_2` | `7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0002` |
| `ble_server_car_3` | `ble_car_3` | `7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0003` |

Flash each profile to its corresponding board, substituting its actual COM
port. The legacy `ble_server` profile also uses Car 1's service UUID.

```powershell
pio run -e ble_server_car_1 -t upload --upload-port COM3
pio run -e ble_server_car_2 -t upload --upload-port COMx
pio run -e ble_server_car_3 -t upload --upload-port COMy
```

With all three cars powered on and the ESP32 BLE client powered off, run:

```powershell
python -u test/test_11/test_11_monitor_servers.py --seconds 150
```

In another terminal, choose a short or long run:

```powershell
python -u test/test_11/test_11_ble_pc.py --duration-seconds 120
# Or: python -u test/test_11/test_11_ble_pc.py --hours 2
# To compare with the Windows throughput preset, add: --connection-mode throughput
```

After the run:

```powershell
python test/test_11/analyze_test_11.py
```

Start the optional serial monitor in a separate terminal. For a two-hour run,
omit `--seconds`; its default adds one minute for startup. Close other serial
monitors first. Each server log records the car's actual command rate and loss.

The PC matches the advertised service UUID to a car number, then records its
Bluetooth address in that car's connection event. The name is only a readable
label. Test 11 keeps one independent connection, 16-bit command sequence,
notification sequence baseline, and CSV/JSONL/summary set per car under
`test/run_logs/ble_test_11_car_N_*`. If a car is absent or disconnects, its
log records retries while the other cars continue. Each car runs at 50 Hz
world writes and 20 Hz notifications, so the total target is 150 writes and
60 notifications per second over one PC radio. Actual three-car capacity must
be measured on the hardware; a successful single-car Test 10 does not establish
three-car throughput.

The first three-car hardware run showed 50 Hz client write calls but an early
server receive rate near 30 Hz per car and a growing command age. A later
120-second run delivered all writes at about 50 Hz per car with low latency.
The startup behavior was inconsistent, so Test 11 closes and reconnects a
car when echoed command age exceeds one second, waiting five seconds before
trying again. It also reconnects when notifications stop for two seconds,
even if Windows has not reported a disconnection. Each new connection resets
that car's command sequence and measurement baseline. The per-car summary
counts overload events, notification timeouts, and disconnects. It marks a
run `ended_disconnected` when retries never reconnect before the deadline,
and `completed_with_reconnects` only after a later connection succeeds. Set
`--max-echo-age-ms` or `--notification-timeout-seconds` to adjust the guards. See
`test/test_11/results/test_11_result.md` for both measured runs; a longer guarded run is
needed to assess endurance.

For connection diagnostics, `test/test_11/test_11_connection_probe.py` scans while
disconnected, connects cars sequentially, and initially sends only 2 Hz dummy
writes without notifications. If all requested cars connect, it enables
notifications and tests 5, 20, and 50 Hz writes. It captures COM3/COM4/COM5
and records timestamped echo age; it suppresses the sequence-based RTT estimate
because the write rate changes between stages.

```powershell
python -u test/test_11/test_11_connection_probe.py --order 1 2 3
python -u test/test_11/test_11_connection_probe.py --order 3 2 1
# Optional Windows scheduling comparison:
python -u test/test_11/test_11_connection_probe.py --order 1 2 3 --connection-mode power
```

The normal server profiles keep the command watchdog enabled. Temporary
`ble_server_car_N_idle_probe` profiles set `BLE_COMMAND_WATCHDOG_ENABLED=0`
to disable only the command-timeout disconnect for pure-idle diagnostics.
Task watchdog and advertising recovery remain enabled. After flashing these
profiles to the corresponding boards, `--keepalive-hz 0` makes setup truly idle.
The runner requires all selected boards to report `command_watchdog=0` over
serial before proceeding. Restore the normal `ble_server_car_N` profiles
afterward; their health logs report `command_watchdog=1`.

The server also checks for a stalled command stream. After five seconds with
no new valid world sequence while connected, it requests a disconnect (the
first command gets a ten-second connection-setup grace period). If
that request remains stuck for another 15 seconds, it restarts the ESP32 as a
last resort. While disconnected, it retries inactive advertising once per
second after a short handshake grace period; 15 seconds of failed advertising
recovery also triggers a restart. A powered car waiting normally for a PC
does not restart. The platform's five-second task watchdog also monitors the
main loop for blocked BLE calls. These are communication recovery limits for the test, not
a motor-control safety timeout.

Server serial output includes `ble event:` records for connection changes and
`ble health:` records for connection/subscription/advertising state, free heap,
notification errors and recovery restarts. `ble buffers:` reports BLE packet
buffer availability and skipped telemetry samples. When buffers are nearly
exhausted, the server skips samples to leave capacity for command/control
traffic; an allocation failure also causes a 200 ms notification backoff.
Skipped samples still advance the notification sequence and are visible as
sequence gaps if the same connection resumes. `ble boot:` records include the
hardware reset reason and last recovery reason (1: disconnect stuck, 2:
advertising stuck). The serial monitor retries USB ports that disappear during
a reset, while continuing to capture the other cars. Both the monitor and PC
test accept `--output-dir` to group an experiment's files.

### Test 12: separate NimBLE-Arduino experiment

`src/test_12/car/main.cpp` uses the separate `h2zero/NimBLE-Arduino` library,
pinned to 2.5.1. The existing Test 10/11 firmware uses Arduino's `BLEDevice.h`
API; the installed ESP32 core already backs that API with NimBLE. Test 12
compares the library implementations and a simpler application, rather than
assuming Test 11 was using Bluedroid. Existing firmware sources are unchanged.

The three `ble_test12_car_N` profiles advertise `ESP32_Robot_N`, share the
service UUID `12345678-1234-5678-1234-567812345678`, and implement no motor
outputs. The command-disconnect watchdog is absent so connection setup can be
tested with no application traffic. Notification subscription is tracked,
advertising resumes after disconnect, and serial output is bounded and emitted
outside BLE callbacks.

Commands remain **20 bytes**: little-endian uint16 sequence, uint8 topology,
four dummy movement bytes, and 13 random padding bytes. Telemetry follows the
expanded example's **21-byte** structure, decoded with `<BHHB6sHBhhH`:
car ID, telemetry sequence, echoed world sequence, saturated previous-window
command loss, six NFC bytes, NFC age, follow state, two signed speeds and
battery millivolts. The sample advice omitted the follow-state byte in Python;
the corrected format agrees with the packed C++ structure. An ATT MTU of at
least 24 is required; the server requests a local maximum of 64 and Python
checks the negotiated value. Sensor fields are synthetic.

Build and flash these explicit profiles to the matching boards:

```powershell
pio run -e ble_test12_car_1 -t upload --upload-port COM3
pio run -e ble_test12_car_2 -t upload --upload-port COM4
pio run -e ble_test12_car_3 -t upload --upload-port COM5
python -u test/test_12/test_12_ble_pc.py --duration-seconds 120 --ports COM3 COM4 COM5
python -u test/test_12/test_12_ble_pc.py --setup sequential --cars 3 2 1 --ports COM3 COM4 COM5
python -u test/test_12/test_12_ble_pc.py --cars 1 --duration-seconds 35
```

The new Python runner scans before connecting and passes discovered BLEDevice
objects to Bleak. Concurrent setup is the default; sequential order is an
explicit alternative. It waits ten seconds with no commands or subscriptions,
then starts traffic only if every requested car connected. It sends one shared
world sequence at 50 Hz using concurrent writes without response and receives
20 Hz notifications. It uses deadline pacing with worker-thread sleep on
Windows, records actual rates and write errors, and avoids catch-up bursts.
The approximate RTT compares the last sequence actually attempted to the
echoed sequence; timestamped echo age measures each echoed command separately.
Both include server processing and notification timing, and are not pure
radio latency. Five-second statistics, raw telemetry and optional server serial
logs are saved under `test/test_12/results/runs`. All clients and serial ports are
closed on exit. `traffic_completed` means the observation finished; measured
rates and gaps determine whether the performance target was achieved.

For this bench's known USB identities, the following wrapper flashes all three
Test 12 profiles, tries concurrent and two sequential setups, tests each car
individually, and restores normal firmware in `finally`. It records uploads and
restoration in `firmware_state.json`:

```powershell
python -u test/test_12/run_test_12_experiment.py --duration-seconds 120 --single-seconds 35
```

After manual tests, restore `ble_server_car_1`, `ble_server_car_2` and
`ble_server_car_3` to COM3, COM4 and COM5 respectively. The normal health logs
should report `command_watchdog=1`.

The measured BARROT comparison and firmware restoration evidence are in
[`test/test_12/results/test_12_result.md`](test/test_12/results/test_12_result.md).

### Test 13: ESP32 serial/BLE modem with two car links

This setup uses the ESP32 on **COM3** as the PC's BLE central radio. Python
communicates only over USB serial; it does not use the Windows Bluetooth
adapter. **COM4 is Car 2**, BLE address `68:EE:8F:46:33:45`, and **COM5 is
Car 3**, BLE address `68:EE:8F:4B:5B:19`. Both use the same service and
characteristic UUIDs as Test 12. Their fixed car IDs remain in telemetry.

Firmware is separate from all earlier tests:

- `src/test_13/esp_esp_ble_modem/main.cpp`: serial modem and BLE central.
- `src/test_13/esp_esp_ble_car/main.cpp`: car peripheral, synthetic telemetry and no
  motor outputs. `BLE_CAR_ID` selects the application ID.
- `src/test_13/include/esp_esp_ble_protocol.h`: shared 20-byte command / 21-byte telemetry
  formats and hex framing helpers.

The modem accepts **40 hex characters followed by newline** for each binary
20-byte command and sends it once, using write without response, to every
ready car. Telemetry is returned as **42 hex characters followed by newline**,
unchanged from the car's 21-byte BLE payload. No MAC or extra routing header
is added to telemetry. Lines beginning with `#` are modem diagnostics or
controls and cannot be mistaken for telemetry. CRLF and uppercase hex are
accepted; wrong lengths, invalid hex and oversized lines are rejected.
The modem forwards repeated or out-of-order sequence numbers too: sequence
counters observe the stream without changing it. Each car decides whether a
command is new, duplicated or old.

The modem starts idle. Python sends `#START` to scan by shared service UUID
and match the configured public BLE addresses, then connect and subscribe.
`#STATUS` requests counters and link state. `#STOP` stops command forwarding
and disconnects the links; another `#START` reconnects them. Commands are
generated only by Python, at 50 Hz. Each car independently generates 20 Hz
telemetry. The modem uses bounded queues and separates serial handling,
connection management and command writes; BLE callbacks never write serial
output. Queue drops, malformed frames, BLE write failures and disconnects
are exposed in diagnostics.

`src/test_13/include/esp_esp_ble_modem_config.h` is force-included only in the modem
profile to build NimBLE-Arduino's host with **four connection slots**. The
installed ESP32-S3 controller allows six activities, including scanning and
advertising capacity. Only two peers are configured in the modem's `targets`
table; add known ID/address entries there to use additional cars. Configuring
four slots is not a physical four-car validation.

Flash these explicit profiles:

```powershell
pio run -e esp_esp_ble_car_2 -t upload --upload-port COM4
pio run -e esp_esp_ble_car_3 -t upload --upload-port COM5
pio run -e esp_esp_ble_modem -t upload --upload-port COM3
```

`esp_esp_ble_car` is the base Car 2 profile. For this bench, an optional
`python -u test/test_13/flash_test_13.py` verifies the three USB serial identities
before uploading. It leaves the new modem/car firmware installed on success
and falls back to the normal server profiles if an upload fails.

Close other monitors on COM3/4/5, then run:

```powershell
python -u test/test_13/test_13_ble_modem.py --duration-seconds 120 --server-ports COM4 COM5
python -u test/test_13/test_13_ble_modem.py --duration-seconds 300 --server-ports COM4 COM5
python -u test/test_13/test_13_ble_modem.py --hours 2 --server-ports COM4 COM5
```

The default modem port is COM3 and the expected car IDs are 2 and 3. Car USB
monitoring is optional, but `--server-ports` enables exact receive-total and
sequence-loss validation against each car. The runner waits for both links
and fresh server measurements before starting traffic. The initial world
sequence defaults to 65520 to exercise uint16 wraparound within the first
second. USB input is parsed as a stream and supports fragmented or combined
reads. Telemetry ID selects each car's independent statistics and timestamps.

Logs are saved under `test/test_13/results/runs`: raw JSONL (including every command,
telemetry and modem diagnostic), per-car server serial logs, five-second CSV
statistics and a final summary JSON. A `passed` result requires each car to
average 50±1 Hz commands and 20±0.5 Hz telemetry, with no observed sequence
gaps, format errors, modem queue drops or BLE write errors. Modem command and
per-link write totals must equal Python's send total; monitored car receive
totals must also match exactly. Unexpected reconnects fail validation. These
are acceptance tolerances; measured rates and gaps are retained in the logs.

Echo age includes both USB paths, modem scheduling, BLE transport and the
car's telemetry timing. The sequence-based RTT remains a rough estimate at
20 ms per world sequence. Neither is pure radio latency. On exit the runner
stops traffic, allows the last command to drain, sends `#STOP`, captures final
server counters and closes all serial ports. The firmware remains installed
for the next run; these dummy car profiles have no command-disconnect watchdog.

Measured results and links to the raw captures are in
[`test/test_13/results/test_13_result.md`](test/test_13/results/test_13_result.md). For a separate hardware
framing check, `python test/test_13/test_13_transport_probe.py` deliberately sends two
malformed lines and a repeated sequence, then verifies recovery and forwarding
on both cars. These intentional errors are separated from the rate tests by
counter baselines.
