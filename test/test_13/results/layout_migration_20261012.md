# Folder migration and Test 13 verification

The repository now groups Python experiments under `test/test_02` through
`test/test_13`. Each folder contains its runner, available checks, analysis
or plotting scripts, instructions and `results/`. Historical capture filenames
and bytes were preserved: **359 capture files** passed SHA-256 checks before
and after the move. `test/migration_map.json` records old/new locations.
Embedded old absolute paths in archived logs and JSON remain historical data.

Available firmware is grouped under `src/test_09`, `src/test_11`,
`src/test_12` and `src/test_13`. The retained BLE server is explicitly shared
by Tests 9–11. Current ESP-NOW firmware is under `src/legacy/espnow`, with
historical-version limitations documented. Missing firmware was not recreated.
Existing PlatformIO environment names were preserved and source/include paths
updated. Imports and subprocess working directories were adjusted for the
additional folder level. Both direct script and package execution are supported.

Validation:

- All **54 offline checks** passed after migration.
- Package import and Python syntax checks passed.
- Active README/result links resolve. Three already-missing older Test 11
  reconnect captures are documented as unavailable instead of broken links.
- Test 8 and Test 9 plotting tools generated their PNGs using copied input
  captures, leaving historical files untouched.
- `esp_esp_ble_modem`, `esp_esp_ble_car_2`, and `esp_esp_ble_car_3` compiled
  successfully from their new locations, then were flashed to COM3/4/5.
- The hidden endurance launcher passed PowerShell syntax validation.

## Two-minute physical verification

Both cars stayed connected with 15 ms intervals and MTU 64. Each received
**6,000 commands** and Python received **2,400 telemetry packets per car**.
Command and notification sequence loss, write errors, queue drops and unexpected
disconnects were zero. Mean echoed-command age was 30.47 ms for Car 2 and
27.67 ms for Car 3. The summary reports `passed` with no validation failures.

[Summary](runs/20261011_155858_386934/ble_test_13_20261011_155858_386934.summary.json),
[CSV](runs/20261011_155858_386934/ble_test_13_20261011_155858_386934.csv),
[manifest](runs/20261011_155858_386934/manifest.json),
[flash state](runs/flash_20261011_155733/firmware_state.json).

## Two-hour run

Started in a hidden independent Python process at approximately **00:01:40
Hong Kong time on 12 October 2026**, PID 32260. It runs 7,200 seconds of traffic
after both links are ready, then drains, disconnects, captures final counters
and closes the ports. It finished at **02:01:48 Hong Kong time** with status
**passed**. Each car received all 360,000 commands and Python received 144,003
notifications per car, with zero observed sequence gaps.

[Matplotlib plots and independent analysis](runs/endurance_20261011_160139_879273/analysis.md).

[Run folder](runs/endurance_20261011_160139_879273),
[launch metadata](runs/endurance_20261011_160139_879273/launch.json),
[live console](runs/endurance_20261011_160139_879273/console.log).

New Test 13 run folders contain logs, a source/dependency manifest and copies
of available firmware binaries. Build copies are not readbacks from devices.
