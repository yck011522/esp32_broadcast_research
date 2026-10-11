# Experiments

Each numbered experiment has its Python scripts, analysis/plotting tools and
results together. Run commands from the repository root with `.venv` activated.

| Test | Experiment |
|---|---|
| [2](test_02/README.md) | Basic ESP-NOW test |
| [3](test_03/README.md) | Master radio passthrough |
| [4](test_04/README.md) | ESP-NOW long run and diagnosis |
| [5](test_05/README.md) | ESP-NOW loss experiment |
| [6](test_06/README.md) | OTA experiment |
| [7](test_07/README.md) | ESP-NOW unicast comparison |
| [8](test_08/README.md) | ESP-NOW multi-car long run |
| [9](test_09/README.md) | ESP32 BLE client serial logging |
| [10](test_10/README.md) | PC Bluetooth single-car experiment |
| [11](test_11/README.md) | PC Bluetooth multi-car experiment |
| [12](test_12/README.md) | NimBLE car firmware with PC Bluetooth |
| [13](test_13/README.md) | ESP32 serial/BLE modem with two cars |

Firmware folders exist for Tests 9, 11, 12 and 13. Tests 9–11 share the
retained Test 11 BLE server source. Older ESP-NOW sources are retained
under `src/legacy/espnow`; missing historical sources are not recreated.
PlatformIO profile names remain unchanged; their source paths were updated.

`diagnostics/` holds investigations spanning experiments. `common/` holds
stable utilities. Some experiments intentionally import retained modules
from earlier numbered packages instead of copying their measurement code.

Existing capture bytes and filenames were preserved. Embedded absolute
paths in old JSON/logs remain historical metadata.
[migration_map.json](migration_map.json) maps every old path to its new
location and records its original SHA-256. Result Markdown links were updated.

Raw `.jsonl` packet logs are excluded from Git because long runs produce very
large files. They remain available locally. CSVs, summary/analysis JSON, reports,
plots and small serial/build logs are retained in the repository. Archived
packet-level replay requires the corresponding local raw log; the committed
CSV and summaries retain window statistics and final results. The migration
map records historical locations, including files subsequently excluded from Git.

Full offline check suite:

```powershell
python -m unittest discover -s test -t . -p '*checks.py'
```
