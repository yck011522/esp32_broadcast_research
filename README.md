The purpose of this repository is to test communication between a master radio and multiple slave radios for real time control of up to four RC cars.

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

`src/ble_server/main.cpp` represents the car (BLE peripheral/server).
`src/ble_client/main.cpp` represents the computer side, using an ESP32-S3
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

The server advertises `ble_server` and service UUID
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
python -u test/test_9_ble_test.py
```

Defaults are COM4, 115200 baud, and two hours. To try a short run or another port:

```powershell
python -u test/test_9_ble_test.py --duration-seconds 30
python -u test/test_9_ble_test.py --port COM13 --hours 2
```

Files are saved under `test/run_logs/` with a unique UTC timestamp:

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
python test/analyze_test_9.py
```

An optional CSV path selects a specific run. Plots distinguish window-average
RTT from window-maximum RTT; these are not individual-packet percentiles.
