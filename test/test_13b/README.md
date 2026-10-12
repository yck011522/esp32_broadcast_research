# Test 13b: one ESP32 modem with four BLE cars

Firmware and measurement logic are shared with Test 13. Results are separate
under `test/test_13b/results/runs/`, with `ble_test_13b_` filenames and manifests.
Existing two-car results remain under Test 13.

| Port | Role / car ID | Public BLE address | Firmware profile |
|---|---|---|---|
| COM3 | Master modem | — | `esp_esp_ble_modem_13b` |
| COM4 | Car 2 | `68:EE:8F:46:33:45` | `esp_esp_ble_car_2` |
| COM5 | Car 3 | `68:EE:8F:4B:5B:19` | `esp_esp_ble_car_3` |
| COM7 | Car 1 | `1C:DB:D4:43:73:ED` | `esp_esp_ble_car_1` |
| COM8 | Car 4 | `68:EE:8F:4B:4F:35` | `esp_esp_ble_car_4` |

The original Car 2/3 identities are retained. The modem uses the common service
UUID and matches each configured MAC; notifications retain their car ID.
The four-car modem profile sets `BLE_MODEM_FOUR_CARS=1`, selecting four entries
in the same source file. Car firmware is selected by `BLE_CAR_ID`. Both Test 13
and 13b use NimBLE-Arduino 2.5.1 with four modem host slots. World commands are
20 bytes at 50 Hz per car; telemetry is 21 bytes at 20 Hz per car. USB uses hex
plus newline. No motor output is implemented in these test profiles.

From the repository root with `.venv` activated, close other serial monitors:

```powershell
python -u test/test_13b/flash_test_13b.py
python -u test/test_13b/test_13b_ble_modem.py --duration-seconds 120 --label bench
```

The flash helper verifies all five USB identities first. It leaves Test 13b
installed on success and records any partial upload failure. The test waits
for all four subscriptions and monitors COM4/5/7/8 by default. A passing run
requires 50±1 Hz commands and 20±0.5 Hz telemetry for **every** car, matching
Python/modem/car command totals, no sequence gaps or queue/write errors, and
clean shutdown. The car command-disconnect watchdog remains disabled.

## Planned distance runs

After moving the cars, run these separately. Keep the master on COM3:

```powershell
python -u test/test_13b/test_13b_ble_modem.py --duration-seconds 10 --label far_10s
python -u test/test_13b/test_13b_ble_modem.py --hours 1 --label far_1h
python -u test/test_13b/test_13b_ble_modem.py --hours 8 --label far_8h
```

If the cars are powered remotely and no longer connected to the computer by
USB, add `--no-server-monitor` to each command. This still checks all four
telemetry streams, echoed-command ages, and modem write totals. Exact command
receipt then has no independent car serial verification; summaries record
`server_monitoring=false`. Do not invent receive totals from submitted writes.
If Windows changes port numbers, use `--server-ports` in car order **2 3 1 4**.
MAC addresses and application IDs are independent of COM port numbering.

Test 13b allows echoed-command age up to **5,000 ms** by default (the original
Test 13 default remains 1,000 ms). `--max-echo-age-ms` overrides it; use 0 to
disable the age stop condition. The separate telemetry-silence limit defaults
to 3 seconds and can be changed with `--telemetry-timeout-s`, or disabled with
0. Initial command echo and BLE disconnect checks also remain active. Limits
are saved in the run manifest and summary; late packets keep their actual ages
in the statistics. Raising a stop limit does not remove delays or change the
rate/loss acceptance checks. For example:

```powershell
python -u test/test_13b/test_13b_ble_modem.py --hours 1 --label far_1h --no-server-monitor --max-echo-age-ms 5000 --telemetry-timeout-s 10
```

Each run stops command forwarding, disconnects the links, and closes all
opened ports at completion or interruption. Python prevents Windows idle
sleep while running. Raw JSONL stays local and is ignored by Git; CSVs,
summaries, manifests, analysis and plots are retained.

Analyze the latest completed run with shared Matplotlib tools:

```powershell
python test/test_13b/analyze_test_13b.py
```

Echo age includes USB, modem scheduling, BLE and notification timing; it is
not pure radio RTT. Four-car analysis requires the local raw JSONL for packet
replay. CSVs and the saved summary preserve window and final results.

## Bench result

[The two-minute four-car bench run passed](results/test_13b_result.md): every
car received 6,000 commands and returned 2,400 notifications, with zero observed
sequence gaps or unexpected disconnects. Subsequent distance and recovery runs
exposed failures; see the [consolidated findings](results/findings.md). The same
firmware remains installed. After the recovery stall, COM3 needs a reset before
further testing; Python has closed the serial port.

## Recovery observation

The existing modem firmware retries missing connections. Python can now leave
that retry running while it continues the same 50 Hz command stream:

```powershell
python -u test/test_13b/test_13b_ble_modem.py --recovery --no-server-monitor --duration-seconds 900 --label recovery_powercycle
```

No firmware reflash is needed. All four cars must be ready initially. During
traffic, disconnects and the configured echo-age/silence limits become logged
warnings. Serial transport errors and missing fresh shutdown acknowledgement
still fail the run. Reconnection creates a
new measurement session for that car only, so a rebooted telemetry sequence
does not appear as a huge packet loss. Previous windows and outage durations
are retained; zero sequence gaps within a session does not imply zero downtime.
TX statistics count commands submitted to the modem, including while a car is
offline. They do not claim that the offline car received commands.

For a controlled test, first confirm all cars return about 20 Hz with fresh
echoes. Remove power from Car 1 (formerly COM7), leave it off for 30 seconds,
then restore power. Leave COM3 and the other three cars powered. Observe both
`link_ready` and `traffic_restored`: subscription alone is not proof that fresh
commands are reaching the car. Record any effect on the other cars.

Recovery CSVs include `session`, `link_ready`, `recovery_pending` and
`health_warnings`. The summary records every outage, reconnection, command
recovery and unresolved outage. A completed recovery run has status
`observation_completed`, separately from `performance_passed` and the strict
validation failures. An intentional outage will usually fail uninterrupted
delivery acceptance; it is not converted to a passing performance result.

Optionally add `--stop-file <new-path>`. Creating that file ends traffic cleanly
and saves CSV/summary results, useful for a background observation. The file
must not exist when starting the run. Normal completion and Ctrl+C also save
results and request that the modem stop. A stalled modem may not acknowledge
that request; the current runner reports this as failure.
