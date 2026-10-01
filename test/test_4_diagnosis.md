# Test 4 investigation — 2026-09-30

## Current behavior — updated October 1

The overnight run stopped because Slave 1 appeared after its reset confirmation
was missed. Per the user's updated instructions, that condition no longer aborts
the run. The original investigation below describes the earlier implementation.

- Slave IDs are discovered automatically from telemetry; there is no configured
  slave count or `--slaves` argument. Each physical slave needs a distinct ID.
- Each interval independently checks reset confirmation. A slave observed during
  measurement without confirmation gets a `FAIL_TO_RESET` row. Its loss counters,
  percentages, and telemetry sequence statistics remain blank rather than mixing
  untrusted counters into the results. It can recover after the next interval's reset.
- A slave with no telemetry during measurement (including the receive tail) has
  no data row. Reset-phase sightings alone do not create measurement rows.
- A slave that disappears retains its latest valid counters; its row becomes
  `STALE` after two seconds without valid updates. Its age and sequence lag remain
  visible. If it returns with consistent counters, statistics resume, including
  observed telemetry sequence gaps.
- A backwards counter invalidates further updates for that slave in the current
  interval (`COUNTER_RESET`); the trusted prefix is preserved and other slaves
  continue. This condition no longer aborts the entire run either.
- Even if no slaves confirm reset, the transmit schedule continues. Only actual
  serial/host/logging failures or manual interruption stop the run early.
- A companion `.summary.json` records completion status, error reason, packet
  count, and interval count. Capture stdout separately if a full console history
  is desired.

For `STALE` and `COUNTER_RESET`, reported loss is based only on the last trustworthy
telemetry. Missing trailing world packets or telemetry cannot be measured exactly
from the existing slave protocol. Do not interpret a frozen loss percentage as
proof that a disappeared device continued receiving.

## Original investigation

The original Test 4 differed from Test 3 in its host transmission path:
it omitted `ser.flush()` and used a `sleep(0)` polling loop near each deadline.
The project virtual environment runs Python 3.10.13 on Windows. A fresh
60-second original-script run measured 15.813 ms maximum scheduling lateness,
0.5333% world loss, maximum gap 3, and no telemetry gaps for Slave 1.
Slave 2 was unplugged at that point, as subsequently confirmed by the user.

The historical 19% loss / gap 121 was not reproduced in that baseline.
It is therefore not established that a single Python bug caused all of the
historical loss. Both slaves' shared gaps suggest a shared path interruption,
but the old timing metric measured only time before `write()`, not write delay,
actual inter-write intervals, USB delivery, or radio delivery. The master also
does not report the return value of `esp_now_send()`. Those logs cannot locate
the interruption conclusively.

## Changes

- Restore Test 3's serial write followed by drain, and its sleeping main-thread
  transmitter; keep receive and logging work in separate threads.
- Request 1 ms Windows timer resolution for Python 3.10. Release it on exit.
- Rebase the schedule after a delay of at least one packet period rather than
  replaying overdue packets in a burst. Keep sequences contiguous, count rebases,
  and report actual packets sent. A delayed run can send fewer than its target
  packet count; it still stops at its configured duration plus a one-second tail.
- Record maximum write time and inter-write interval alongside scheduling
  lateness. These are host measurements, not radio acknowledgements.
- Preserve partial serial lines across timeouts. Detect duplicate telemetry and
  counter resets; stop with an error if counters become untrustworthy.
- Verify zeroed telemetry during reset. Log missing expected slaves explicitly;
  stop if a slave appears later without having confirmed reset.
- Log cumulative and interval world loss, sequence lag, and telemetry age.
  Mark telemetry stale after two seconds rather than presenting it as healthy.
- Default to four wall-clock hours on COM4 at 100 Hz. Following the user's
  request, save each 60-second measurement interval and then reset both slaves.
  Include a 0.1-second receive tail before saving and approximately 0.5 seconds
  for the five reset messages. Reset overhead is included in the four-hour
  budget, so fewer than 1,440,000 packets are sent. The last interval may be short.
- In the default mode, every CSV row's gap and loss counters belong to that
  measurement interval. `elapsed_s` spans the entire run, `interval_elapsed_s`
  spans the interval including its tail, and `total_world_packets_sent` preserves
  the total across resets. `max_world_gap` is now a true since-reset interval
  maximum, not an inferred change in a cumulative maximum.
- `--reset-interval 0` retains continuous mode, with periodic cumulative logging.
- Request prevention of automatic idle sleep while running. This does not prevent
  manual sleep, shutdown, or closing the laptop lid; no power-plan setting changes.

The world-loss numerator remains the slave's own internal gap count, matching
Test 3. Its firmware does not count loss before its first numbered packet or
after its last received packet. Telemetry loss is measured between observed
telemetry sequence numbers. Neither percentage alone captures a silent outage;
use the age, status, and sequence-lag columns too.

## Commands

Continuous-mode validation completed at 23:53:28 local time on September 30,
before the user requested periodic resets:

| Metric | Slave 1 | Slave 2 |
| --- | ---: | ---: |
| World packets lost / 18,000 sent | 83 | 78 |
| World loss | 0.4611% | 0.4333% |
| Maximum consecutive gap | 2 | 2 |
| Telemetry loss | 0% | 0% |

All 18,000 scheduled packets were sent in the three-minute validation.
Maximum scheduling lateness was 6.036 ms, maximum write/drain time 4.664 ms,
and maximum inter-write interval 15.125 ms. No schedule rebases were needed.
Both slaves confirmed reset. Six initial offline regression checks passed;
a seventh check for interval reset accounting was added and passed afterward.
Validation data: `run_logs/radio_test_20260930_235026.csv`.
This is a successful short validation, not a guarantee of future radio conditions.

Periodic-reset validation then completed at 23:57:35. Two full 60-second intervals
sent 6,000 packets each, followed by a shortened final interval to meet the
125-second wall-clock limit. Both slaves confirmed all three resets.

| Measured interval | Slave 1 loss | Slave 2 loss | Maximum gap, both slaves | Telemetry loss, both slaves |
| --- | ---: | ---: | ---: | ---: |
| First minute | 0.267% | 0.167% | 1 | 0% |
| Second minute | 0.200% | 0.133% | 1 | 0% |

Periodic-reset validation data: `run_logs/radio_test_20260930_235529.csv`.
The first detached continuous run was stopped before its first one-minute log
to apply the user's reset request. Its launch metadata is retained as
`run_logs/overnight_20260930_235350.superseded.json`.

The detached overnight launch metadata is saved in `run_logs/overnight_latest.json`,
including the PID, console log paths, command, and estimated completion time.

```powershell
# Offline regression checks (no hardware)
& .\.venv\Scripts\python.exe test\test_4_checks.py

# Three-minute validation
& .\.venv\Scripts\python.exe -u test\test_4_long_run.py --duration-seconds 180 --log-interval 30

# Four-hour run, resetting and logging after each measured minute
& .\.venv\Scripts\python.exe -u test\test_4_long_run.py --port COM4 --hours 4 --reset-interval 60
```

Generated CSV files and detached-run console logs are in `test/run_logs/`.
The original script is preserved there as `test_4_before_fix.py.txt`.
No master or slave firmware was modified or flashed during this investigation.

## References

- [Python sleep behavior and the Windows timer change in Python 3.11](https://docs.python.org/3.11/library/time.html#time.sleep)
- [pySerial write, flush, and timeout behavior](https://pyserial.readthedocs.io/en/latest/pyserial_api.html)
- [Windows scoped idle-sleep prevention](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)
