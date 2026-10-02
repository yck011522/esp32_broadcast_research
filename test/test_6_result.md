# Test 6 Result — OTA Firmware Update Over the Offline Lab Router

Date: 2026-10-02
Status: **PASS**

## Purpose

Verify that the slave ESP32 firmware can be updated Over-The-Air (OTA)
through the offline lab router, while the master ESP32 stays connected
to the PC by USB for serial monitoring and Python logging — with no USB
connection to the slave.

## Test Setup

```
Internet Wi-Fi ── PC ── USB (COM9) ── MASTER ESP32 (master_radio_ota, channel 6)
                  │
                  └─ LAN cable ── ASUS Router (offline) ))(( SLAVE ESP32
                                 "M7026 Lab's ASUS Router"      (slave_radio_ota)
                                 2.4 GHz, fixed channel 6

MASTER )))──── ESP-NOW broadcast / unicast, channel 6 ────((( SLAVE
```

- Router: `M7026 Lab's ASUS Router`, 2.4 GHz fixed to channel 6, no internet
- Slave: static IP `192.168.50.200` (no DHCP), gateway `192.168.50.1`
- OTA: hostname `esp32-car2`, password-protected, PlatformIO `espota` protocol
- Slave firmware identification: `FW: v1` (before) → `FW: v2` (uploaded by OTA)

## Firmware Environments (platformio.ini)

| Environment | Upload method | Use |
|---|---|---|
| `master_radio_ota` | USB serial | Master, ESP-NOW fixed to channel 6 |
| `slave_radio_ota` | OTA (`espota`, 192.168.50.200) | Slave updates over Wi-Fi |
| `slave_radio_ota_usb` | USB serial | First slave flash / recovery only |

## Procedure

1. Flashed both boards once by USB (`master_radio_ota`, `slave_radio_ota_usb`).
2. Slave powered externally; confirmed on the router (`ping 192.168.50.200` OK,
   boot status: `WiFi: connected | IP: 192.168.50.200 | Channel: 6 | RSSI: -31 dBm`).
3. Ran the automated test:

   ```
   python -u test/test_6_ota.py --port COM9
   ```

   Phases: BASELINE (30 s radio check) → OTA upload (pio subprocess) →
   REBOOT (20 s wait) → VERIFY (30 s radio check).
4. Visual verification: slave USB serial monitor afterwards showed
   `STATUS | FW: v2 | WiFi: connected | IP: 192.168.50.200 | Channel: 6` —
   the v2 firmware arrived over the air.

## Results

| Phase | World packets sent | Telemetry received | Telemetry loss | World loss (slave-reported) | Max silence |
|---|---|---|---|---|---|
| BASELINE (FW v1) | 1500 @ 50 Hz | 604 | 0.000 % | 705 (47.0 %) | 1.34 s |
| OTA upload | — | — | — | — | — |
| VERIFY (FW v2) | 1500 @ 50 Hz | 568 | 0.000 % | 512 (34.1 %) | 3.07 s |

OTA upload details:

- Command: `pio run -e slave_radio_ota -t upload`
- Duration: **10.1 s** (firmware image ≈ 735 KB)
- Exit code: 0 (SUCCESS)
- Slave rebooted automatically and rejoined the router; telemetry
  recovered with 0.000 % loss.

## Conclusions

1. **OTA works end-to-end.** Slave firmware can be updated through the
   offline router with no USB connection to the slave. The slave falls
   back safely if an upload fails (dual OTA app partitions).
2. **ESP-NOW and router Wi-Fi coexist on channel 6.** Fixing the master
   to the router's channel (`esp_wifi_set_channel(6)`) is required;
   without it the two radios cannot hear each other.
3. **Slave→master telemetry is unaffected** by the router connection
   (0.000 % loss, unicast with ACK).
4. **Master→slave broadcast world packets degrade noticeably**
   (34–47 % loss) compared to earlier tests without the router
   (Test 3: 0.2–1.1 %). The slave's radio shares channel 6 between
   Wi-Fi station traffic and ESP-NOW reception; broadcast packets have
   no ACK/retransmission and are lost first. This coexistence effect is
   a candidate for a future dedicated test (Test 7).

## Data Files

- `test/run_logs/radio_test_6_20261002_171016.csv`
- `test/run_logs/radio_test_6_20261002_171016.summary.json`
- Test script: `test/test_6_ota.py`
- Firmware: `src/master_radio_ota/main.cpp`, `src/slave_radio_ota/main.cpp`
