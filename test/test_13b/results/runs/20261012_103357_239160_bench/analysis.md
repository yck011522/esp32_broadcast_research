# Test 13b analysis

Run: 2026-10-12 18:33:57 to 2026-10-12 18:36:11 Hong Kong time.

Traffic duration: **120.001 seconds**. Runner result: **passed**.

| Metric | Car 2 | Car 3 | Car 1 | Car 4 |
|---|---:|---:|---:|---:|
| Commands received by car | 6,000 | 6,000 | 6,000 | 6,000 |
| Notifications received | 2,400 | 2,400 | 2,400 | 2,400 |
| Command rate (Hz) | 49.999846 | 49.999846 | 49.999846 | 49.999846 |
| Telemetry rate (Hz) | 19.999939 | 19.999939 | 19.999939 | 19.999939 |
| Command sequence gaps | 0 | 0 | 0 | 0 |
| Telemetry sequence gaps | 0 | 0 | 0 | 0 |
| Mean echoed-command age (ms) | 28.45 | 29.01 | 34.07 | 29.23 |
| Maximum echoed-command age (ms) | 53.66 | 66.60 | 64.65 | 53.39 |
| Maximum telemetry gap (ms) | 64.58 | 75.64 | 76.13 | 62.98 |

## Delivery and stability

Python's 6,000 world records reconcile with the CSV and modem
serial/per-link counters. Car receive totals are independently checked when
car serial logs are available; unmonitored receive totals remain unavailable.
Notification counts and sequence continuity were rechecked directly from raw
21-byte packets. Sequence loss counts application gaps, not radio retransmissions.

Raw command sequence wraparounds: 1. Command pacing overruns: 0.

Modem gap/error/drop counters did not increase. All links had no reconnects
during traffic and one intentional disconnect each at shutdown.

- Car 2: 0 notification sequence wraparounds;
  gap median / p95 / p99 = 45.39 / 60.60 / 61.51 ms.
  Gaps >=100 ms: 0; >=200 ms: 0.
  Logged echo-age median / p95 / p99 = 28.64 / 36.85 / 38.04 ms.
  Car heap range: 252,072–252,072 bytes; observed uptime resets: 0.
  Quarter-run mean echo ages: 29.50 ms, 28.87 ms, 28.25 ms, 27.50 ms.
- Car 3: 0 notification sequence wraparounds;
  gap median / p95 / p99 = 45.38 / 60.53 / 61.41 ms.
  Gaps >=100 ms: 0; >=200 ms: 0.
  Logged echo-age median / p95 / p99 = 29.54 / 42.60 / 51.87 ms.
  Car heap range: 252,044–252,044 bytes; observed uptime resets: 0.
  Quarter-run mean echo ages: 35.13 ms, 28.58 ms, 27.09 ms, 26.65 ms.
- Car 1: 0 notification sequence wraparounds;
  gap median / p95 / p99 = 45.40 / 60.70 / 61.42 ms.
  Gaps >=100 ms: 0; >=200 ms: 0.
  Logged echo-age median / p95 / p99 = 34.21 / 49.95 / 51.23 ms.
  Car heap range: 252,380–252,380 bytes; observed uptime resets: 0.
  Quarter-run mean echo ages: 34.15 ms, 33.36 ms, 33.59 ms, 35.03 ms.
- Car 4: 0 notification sequence wraparounds;
  gap median / p95 / p99 = 45.61 / 60.83 / 61.50 ms.
  Gaps >=100 ms: 0; >=200 ms: 0.
  Logged echo-age median / p95 / p99 = 29.18 / 38.26 / 39.48 ms.
  Car heap range: 252,192–252,192 bytes; observed uptime resets: 0.
  Quarter-run mean echo ages: 28.16 ms, 29.15 ms, 29.96 ms, 29.45 ms.

Independent check failures: none.

## Interpretation and measurement limits

The 4-link setup sustained the target throughput over this observation.
Quarter-run mean echo ages range from 26.65 to 35.13 ms.
Their chronological values above show whether delay accumulated.
The timing plots show individual arrivals rather than assuming a perfectly
uniform nominal interval. Counts such as 99/100/101 in five-second windows
can be timing/boundary variation when sequences remain contiguous.

The mean/maximum echo ages in the table are the runner's timestamped
measurements from before serial writes. Packet percentiles are reconstructed
from raw post-write send and receive log timestamps, so they differ slightly
and must be treated as approximations. Both include USB transport, modem
scheduling, BLE and car notification timing. Neither measures pure RF RTT.

Server silence is sampled at five-second report times, not the worst gap
between every world command. Exact matching final command totals strengthen
the receive-rate result. Synthetic traffic does not validate motor response
or disconnect safety behavior.

Intentional final `#STOP` disconnects are outside traffic. The recorded final
modem state is disabled with zero links, and both cars resume advertising.

## Sources and plots

[Summary](ble_test_13b_20261012_103357_239160.summary.json), [CSV](ble_test_13b_20261012_103357_239160.csv), [raw JSONL](ble_test_13b_20261012_103357_239160.jsonl), [manifest](manifest.json).

![Rates and timing over the run](plots/overview.png)

![Individual packet timing](plots/packet_timing.png)
