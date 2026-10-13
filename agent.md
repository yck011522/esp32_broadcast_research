# Microcontroller development and experiment workflow

Reusable guidance for developing microcontroller projects and testing with PlatformIO in VS Code and Python. User instructions and the current experiment's requirements take precedence.

## 1. Repository structure and reproducibility

Keep firmware, PC tools and results grouped by experiment number:

```text
repository/
├── platformio.ini                   # Explicit build/upload environments
├── agent.md                         # This development workflow
├── docs/
│   ├── payload.md                   # Specification Files as required
├── src/
│   ├── README.md                    # Firmware/profile index
│   └── test_14/
│       ├── include/                 # Shared protocol and experiment config
│       ├── modem/main.cpp           # Build files for device X etc
│       └── car/main.cpp             # Build files for device Y etc
└── test/
    ├── README.md                    # Experiment index
    ├── common/                      # Stable, genuinely shared utilities
    └── test_14/
        ├── README.md                # Goals, conditions, procedure, criteria
        ├── include/                 #
        │   └── protocol.py
        ├── run_test_14_ble_modem.py # Runner and logger
        ├── analyze_test_14.py       # Matplotlib plots and analysis
        ├── check_test_14.py         # Offline checks for the test
        └── results/
            ├── test_14_result.md    # Experiment-level findings
            ├── firmware_build.log
            ├── offline_checks.log
            └── runs/<timestamp>/
                ├── manifest.json   # Record run set up parameters
                ├── firmware/       # Source snapshots used by this run
                ├── *.csv
                ├── *.summary.json
                ├── *.jsonl         # Optional large raw capture; local only
                ├── <serial logs>
                ├── audit.json
                ├── analysis.md
                └── plots/overview.png
```

The tree illustrates roles, not mandatory filenames for every experiment. Qualification groups may contain a separate run directory for each scenario. Include the actual logger/analysis version or source hash in the manifest as well as firmware sources.

Use local timestamps for run directories and record the timezone.

Preserve completed experiment firmware when starting a new development line. Use a new numbered source folder for substantial changes. Variants such as Test 13b can share Test 13 firmware if that sharing is explicit in their README and build profiles, while keeping their results separate. Do not recreate missing historical firmware or imply that current sources reproduce an unknown old revision.

Keep CSVs, summaries, manifests, small diagnostic/build logs, Markdown reports and plots in Git. Large packet-level JSONL captures stay local and ignored. Analysis should work from the retained CSVs and summaries; document any analysis that additionally requires raw captures. Never discard the only evidence behind a conclusion.

## 2. Generic PlatformIO and VS Code development

Use one root `platformio.ini` with named environments for each firmware role and hardware variant. Select source directories explicitly using `build_src_filter`;
If firmware will upload to multiple devices, write their firmware to support build_flags to indicate device IDs and conditional compilation. However, do not use build_flags for substantially different code paths; use separate source directories instead.
Pin the platform/framework and library versions used by a qualified experiment. Changes to pins create a new validation obligation.

Inspect the working tree and existing documentation before editing. Preserve unrelated work. Update the experiment README, repository indexes and payload specification when behavior or paths change. Keep implemented behavior distinct from future design proposals and synthetic test data.



Build the intended environment explicitly; do not rely on a default environment that may belong to an older experiment. In VS Code, select that same environment for build/upload tasks. From the repository root, equivalent commands are:

```powershell
pio run -e ble14_modem
pio run -e ble14_car_1
python -m unittest discover -s test -t . -p '*checks.py'
```

Use the project's `.venv` for Python tools and record the interpreter and package versions. If the venv is not present, ask the user if they want to create it.

PlatformIO maybe installed as a VS Code extension or as a standalone executable. If `pio` is not on PATH, use the VS Code extension installation. The typical path is `C:\Users\<user>\.platformio\penv\Scripts\platformio.exe`. Compile all affected role/ID profiles for a test, including meaningful conditional variants; do not assume one successful build covers different preprocessor branches.

Before upload, identify the physical board by USB identity/serial number, then resolve its current port. COM numbers can change. Close competing serial monitors, configure reset lines deliberately, and verify every target before a multi-board flash. Record successful and failed uploads individually; a partial flash is not a successful deployment. On Windows, configure Python subprocess text handling for UTF-8 so upload progress output cannot break orchestration.

Build success establishes compilation only. Hardware behavior requires a measured run. Use offline checks for wire layouts, sequence handling, parsing and important orchestration decisions rather than tests that merely repeat the implementation.

## 3. Automatic development and hardware testing

Within the authorized hardware and test scope, iterate through a staged sequence: implement, compile, short bench run, controlled recovery cases, then endurance. Fix failures and repeat the affected stages; broaden testing when a change affects shared behavior. Avoid launching a long run before basic throughput and recovery work.

Automate flashing, capture, scenario timing, clean shutdown and report generation where practical. Keep timestamps and structured scenario events so planned outages can be distinguished from unexpected faults. Continue streaming data to disk during a long run rather than holding it all in memory. Handle interruption cleanly and write a final summary identifying completion, failure or interruption.

The Test 14 scenario set is a reusable starting point:

| Scenario | Evidence to seek |
| --- | --- |
| Short bench | Target rates and bounded command age for every peer |
| One missing/rejoining peer | Automatic return while healthy peers continue |
| Multiple peers missing | Recovery without permanent slot starvation |
| Idle connected period | Connection retained with no application traffic |
| Expired command | Old pending state is not replayed after return |
| Submission burst | Older snapshots are superseded rather than replayed |
| Car loop hang | Watchdog reset followed by automatic rejoin |
| Missing at startup | Available peers operate before the missing peer returns |
| Modem worker hang | Recovery even when the main loop still runs |
| Endurance | Sustained performance, resource stability and recovery observations |

Fault injection belongs in explicit development commands or separate build profiles. Restore normal firmware afterwards, including on failure where possible. Record whether restoration succeeded and what remains installed. A passing fault-profile test does not substitute for a normal-firmware endurance run.

For unattended sessions, prevent host sleep, bound setup/connection waits, provide a clean stop mechanism, and track subprocesses so serial ports can be released. Give concise progress updates while developing. At completion, state what was tested, remaining limits, installed firmware, and whether any process is still running.

## 4. ESP32, USB serial and BLE practices

These practices reflect the tested XIAO ESP32-S3, Arduino framework and NimBLE setup. Recheck behavior for another board, USB mode or framework version.

Do not let development diagnostics stall timing-critical work when USB is absent or undrained. Format an occasional bounded line, check capacity, and skip that diagnostic if it cannot fit:

```cpp
const size_t length = strlen(line);
if (Serial.availableForWrite() >= int(length)) {
    Serial.write(reinterpret_cast<const uint8_t*>(line), length);
}
```

`Serial.write` is useful here because the exact byte count is known before submission. `print`/`println` are also suitable when capacity includes all formatted bytes and the newline. The method name alone does not guarantee nonblocking behavior. Capacity checks are a practical guard, use a single serial writer. Native USB CDC differs from a UART whose transmitter can drain without a connected receiver.

Keep serial output, waits, allocation-heavy processing and connection discovery out of typical control loops. Copy bounded data or enqueue without blocking, then process it in a loop/worker. Use bounded feedback buffers and an explicit overflow policy. Treat LED activity as a timestamp/flag in the callback; perform GPIO changes only at scheduled edges in the loop.

If using LED, check the board's LED pin and active polarity.

If projects requires developing communication flows, specify transport, byte order and encoding properties, packet lengths, endianness and minimum MTU etc in `payload.md`. Validate packed C++ sizes and match Python `struct` formats. Verify malformed framing, rollover and initial-value ambiguity with offline checks.

Use common service UUIDs and logical IDs when replacement devices should represent the same role. Keep discovery metadata separate from characteristic payloads. Scanning and connection events share radio resources; qualify rediscovery while other links carry traffic. Test 14's 20 ms interval and scan settings are measured settings for this setup, not universal optimum values.

## 5. Reliable firmware and recovery design

Give connection management explicit states, bounded attempts and cleanup paths. Missing peers must eventually become discoverable again. Reconnection should not require a PC handshake when the product is intended to start and operate autonomously. Measure how discovery and recovery affect healthy peers.

Keep state traffic fresh with a bounded latest-snapshot mailbox and a documented expiry origin. Separate reliable configuration transactions from replaceable state traffic. Commands already submitted to a controller cannot necessarily be recalled. Bound pressure from weak peers so one stalled link cannot consume all shared buffers.

Distinguish execution health, communications health and actuator safety. A hardware-backed task watchdog detects failure to make progress; absence of application packets need not mean a crashed task or require BLE disconnection. Motor-stop behavior on stale control input is a separate product requirement and must be specified before adding motion.

Each critical task must demonstrate its own progress to the watchdog. A live main loop must not hide a stuck communications worker. Test the reset and the subsequent return to useful service. Watchdogs do not cover every fault where code still runs but behavior is incorrect.

Prefer small, explicit recovery mechanisms with measured behavior. For Test 14, periodic scanning was sufficient; adaptive backoff, extra wire metadata and additional diagnostics should have a concrete requirement before being added.

## 6. Test plans, measurements and results

Before running, the experiment `README.md` should state:

- The goal, implemented scope and explicit exclusions.
- Hardware roles, verified identities/ports, power arrangement, distance and environment.
- Source/build profiles, dependency versions and protocol reference.
- Target rates, durations, scenario timings and pass/fail thresholds.
- Exact setup, flash, run, stop and analysis commands.
- What each metric means, its observation point and known ambiguity.
- Expected artifacts and normal firmware to leave installed.

Record actual conditions and arguments per run in its manifest; a README's default conditions do not prove how a particular capture was made. Use unique timestamped run directories and record timezone. Keep source/build hashes and Git revision plus working-tree state so uncommitted firmware can still be identified.

Use elapsed monotonic time for rates, gaps and command age. Require current connected/subscribed observations before capturing a healthy baseline. Separate setup, measured traffic and final drain. PC submissions are not car receptions; sequence gaps can include intentionally superseded snapshots and USB drops. Sequence numbers without session identity cannot uniquely distinguish every reset, replacement or wrap. State such limitations rather than inventing precise loss counts.

Write findings separately in `results/test_<number>_result.md`, supported by per-run `analysis.md`. Lead with the result, then conditions, measured evidence, development findings and remaining untested cases. Link to CSVs, original summaries, manifests and plots. Use Matplotlib for standalone figures with units, legible labels, per-device comparisons and annotations for planned faults/pauses. Include rates, gaps/silence and command age where relevant. Plot from saved evidence, not console screenshots.

Audit important claims independently of the runner's pass/fail decision using positive evidence: actual receipts, unchanged connection counters during idle, reset/rejoin observations, or absence of expired replay. Test 14 exposed a stale-startup snapshot that falsely failed an otherwise successful hour run. Preserve original captures and verdicts; document a measurement fix and save any justified reassessment separately, tied to the original file hash. Never silently rewrite a failed result as passed.

Finish with appropriate builds/checks, linked findings, installed-firmware state and remaining validation work. Commit or push only when requested; inspect file sizes before adding results and retain the agreed CSV evidence.
