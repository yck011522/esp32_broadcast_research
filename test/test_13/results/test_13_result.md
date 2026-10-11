# Test 13: ESP32 modem with two simultaneous BLE car links

The subsequent **two-hour endurance run passed**. Both cars received all
360,000 commands at 50 Hz and returned 144,003 notifications each at roughly
20 Hz. Mean echoed-command ages were 28.83 and 29.29 ms, maximum ages were
below 68 ms, and maximum telemetry gaps were below 90 ms. No command or
notification sequence gaps, queue drops or unexpected disconnects were observed.
See the [full analysis and Matplotlib plots](runs/endurance_20261011_160139_879273/analysis.md).

Both car links met the 50 Hz command / 20 Hz telemetry target in three runs
lasting 120, 300 and 120 seconds. Independent car serial counters confirmed every command
arrived. Notification sequence gaps, command sequence gaps, BLE write errors,
queue drops and unexpected disconnections were all zero during normal traffic.

## Hardware and firmware

| USB port | Role | Application ID | Public BLE address |
|---|---|---:|---|
| COM3 | `esp_esp_ble_modem`, ESP32 central | — | — |
| COM4 | `esp_esp_ble_car`, peripheral | 2 | `68:EE:8F:46:33:45` |
| COM5 | `esp_esp_ble_car`, peripheral | 3 | `68:EE:8F:4B:5B:19` |

All boards are Seeed XIAO ESP32-S3. The firmware uses NimBLE-Arduino 2.5.1,
Arduino-ESP32 3.3.12 and the existing pioarduino PlatformIO platform. Python
3.12.10 communicates over USB serial only. The Windows Bluetooth adapter and
Bleak transport are outside this experiment.

Both cars publish the same service UUID
`12345678-1234-5678-1234-567812345678`; world writes use `...5679` and telemetry
notifications use `...567a`. The modem filters discovery by service and the
two configured public addresses. Each connected link negotiated **15 ms
connection interval, latency 0, ATT MTU 64**. MTU 24 or greater is required for
the 21-byte notification.

The modem's NimBLE host is compiled with **four connection slots**, verified
by its runtime `host_limit=4` diagnostic. The controller configuration permits
six BLE activities. Only two physical car peripherals are available and
configured, so four simultaneous car links remain untested.

## Protocol and timing

Python writes one 20-byte world packet every 20 ms, encoded as 40 hexadecimal
characters and newline. The modem decodes it and writes the identical binary
packet without response to each ready car. Cars send telemetry every 50 ms;
the modem returns each unchanged 21-byte packet as 42 hex characters and
newline. The telemetry's first byte is its car ID, followed by the uint16
telemetry and echoed-world sequence numbers. All multibyte fields are little
endian. Full layouts are in `src/test_13/include/esp_esp_ble_protocol.h`.

USB reads can split or combine lines. Both directions accumulate and parse
whole lines, accept uppercase hex and CRLF, and recover at the newline after
an oversized or invalid frame. `#` lines carry controls and diagnostics.
The modem starts idle; `#START` connects, `#STATUS` reports state, and `#STOP`
disconnects both cars. Connection management, command forwarding and serial
handling run separately; BLE callbacks enqueue telemetry rather than printing.

Python waits for both subscribed links and fresh car serial baselines before
measuring. Its default start sequence is 65520, so uint16 wraparound is tested
immediately. All three serial ports are monitored. Acceptance compares Python's
send count, modem serial and per-link write counts, and each car's lifetime
receive counters. Sequence continuity is checked independently in both
directions. The end of each run drains the last command, sends `#STOP`, captures
final counters and closes every port. The shutdown disconnects are intentional
and are excluded from unexpected-disconnection counts.

## Measured normal traffic

| Run | Car | Commands sent / received | Notifications | TX Hz | RX Hz | Mean echo age | Maximum echo age | Maximum telemetry gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 120 s initial | 2 | 6,000 / 6,000 | 2,400 | 50.000 | 20.000 | 28.08 ms | 49.15 ms | 74.91 ms |
| 120 s initial | 3 | 6,000 / 6,000 | 2,401 | 50.000 | 20.008 | 28.29 ms | 41.89 ms | 64.13 ms |
| 300 s | 2 | 15,000 / 15,000 | 6,000 | 50.000 | 20.000 | 28.27 ms | 59.27 ms | 73.91 ms |
| 300 s | 3 | 15,000 / 15,000 | 6,000 | 50.000 | 20.000 | 28.65 ms | 41.74 ms | 75.57 ms |
| 120 s final firmware | 2 | 6,000 / 6,000 | 2,400 | 50.000 | 20.000 | 29.87 ms | 52.82 ms | 75.36 ms |
| 120 s final firmware | 3 | 6,000 / 6,000 | 2,400 | 50.000 | 20.000 | 27.22 ms | 50.19 ms | 74.81 ms |

The normal runs total nine minutes of traffic and 27,000 commands received
per car. They include repeated stop/start connection cycles. All three
summaries report `passed` with empty validation-failure lists.

One extra notification near a measurement boundary is expected. The maximum
gap records delivery jitter even when sequences are contiguous. Echo age is
measured from Python sending a world packet to Python receiving the telemetry
that echoes that packet; it includes both USB paths, modem scheduling, BLE
transport and car notification timing. It is not pure radio RTT. The separate
sequence-based RTT estimate has 20 ms resolution.

The five-minute run reconnected the already-flashed boards: both BLE links
were ready in under one second, without rescanning their cached addresses.
Car free heap remained stable in that run (252,072 and 252,044 bytes).

Evidence:

- Initial run: [summary](runs/ble_test_13_20261011_152825_897806.summary.json),
  [CSV](runs/ble_test_13_20261011_152825_897806.csv),
  raw JSONL (local-only `ble_test_13_20261011_152825_897806.jsonl`).
- Five-minute run: [summary](runs/ble_test_13_20261011_153114_127204.summary.json),
  [CSV](runs/ble_test_13_20261011_153114_127204.csv),
  raw JSONL (local-only `ble_test_13_20261011_153114_127204.jsonl`),
  [COM4](runs/ble_test_13_20261011_153114_127204_car_2_COM4.log),
  [COM5](runs/ble_test_13_20261011_153114_127204_car_3_COM5.log).
- Initial flash record: [firmware state](runs/flash_20261011_152709/firmware_state.json).
- Final modem flash: [upload log](runs/flash_20261011_152709/esp_esp_ble_modem_final.log),
  [source and firmware hashes](runs/flash_20261011_152709/final_source_sha256.json).
- Final firmware rate run: [summary](runs/ble_test_13_20261011_154012_771483.summary.json),
  [CSV](runs/ble_test_13_20261011_154012_771483.csv),
  raw JSONL (local-only `ble_test_13_20261011_154012_771483.jsonl`),
  [COM4](runs/ble_test_13_20261011_154012_771483_car_2_COM4.log),
  [COM5](runs/ble_test_13_20261011_154012_771483_car_3_COM5.log).

## Serial framing probe

After the normal runs, the final modem build was flashed with an adjustment:
the sequence observer records repeats without suppressing their forwarding.
Every correctly framed payload must be transported; the car application
owns sequence acceptance.

The physical probe sent two malformed lines, then three valid packets with
sequences 65535, 65535, 0. It fragmented the first valid line and used uppercase
hex with CRLF for the second. The payload included binary NUL and newline.
The modem rejected exactly the two malformed lines and forwarded all three
valid packets to both cars, recording one sequence repeat. Each car accepted
two new packets and recorded one duplicate, with zero sequence gaps. Return
framing and queues remained error-free, and `#STOP` disconnected cleanly.

The final two-minute rate run followed this probe and passed on the same
firmware. Its lifetime modem counters retain `seq_errors=1` and `malformed=2`
from the intentional probe; neither increased during normal traffic. The
runner validates counter differences from the beginning of each experiment.

Probe [summary](runs/ble_test_13_20261011_153940_262744.summary.json),
raw capture (local-only `ble_test_13_20261011_153940_262744.jsonl`),
[COM4](runs/ble_test_13_20261011_153940_262744_car_2_COM4.log),
[COM5](runs/ble_test_13_20261011_153940_262744_car_3_COM5.log).

## Repeating the experiment

The new firmware remains installed: COM3 is the modem, COM4/COM5 are cars.
These are dummy car profiles with synthetic telemetry and no motor outputs;
their command-disconnect watchdog is disabled for the experiment. The modem
is stopped between runs and cars resume advertising after disconnection.
The initial results below describe short runs; the linked endurance analysis
also validates a two-hour observation with two cars. Four-car capacity remains
untested.

Close any other serial monitors, activate `.venv`, and run:

```powershell
python -u test/test_13/test_13_ble_modem.py --hours 2 --server-ports COM4 COM5
```

Use `--duration-seconds 120` for a shorter repeat. Captures are stored under
`test/test_13/results/runs`. The final summary says `passed` only if both car rates,
sequence checks, independent command totals, queue/error counters and clean
shutdown satisfy the acceptance checks. Optional server serial monitoring can
be omitted, but then exact delivery is not independently verified at the cars.

Both car profiles and the modem built and flashed successfully. All **54
offline checks** passed, including six Test 13 stream-framing and acceptance
checks. The hardware framing probe also passed.
