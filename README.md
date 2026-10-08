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

## BLE slave firmware

The `ble_slave` firmware makes the ESP32-S3 a Bluetooth Low Energy (BLE)
peripheral. A computer (the BLE central) connects to it and subscribes to a
GATT notification characteristic. While connected, the board sends one
notification every 100 ms (10 Hz). It does not use Wi-Fi or ESP-NOW.

### Build and flash

Connect the XIAO ESP32-S3 to the computer by USB, then run these commands from
the repository root:

```sh
pio run -e ble_slave
pio run -e ble_slave -t upload
pio device monitor -b 115200
```

If more than one serial device is connected, select the correct USB serial port
in PlatformIO (`upload_port` for flashing and `monitor_port` for the monitor).
USB is used for flashing and diagnostic messages; the packet stream itself is
sent over BLE.

### Connect from the computer

Use a BLE client/scanner on the computer to find and connect to
`ble_slave_1`. Open service
`7a1e0001-4b6d-4f7a-9c2e-6d3b1a5f0001` and enable notifications on
characteristic `7a1e0002-4b6d-4f7a-9c2e-6d3b1a5f0001`. The client must subscribe
to that characteristic to receive packets; a BLE connection by itself is not
enough.

### Packet and settings

Each notification is exactly five binary bytes:

| Byte(s) | Meaning |
| ------- | ------- |
| 0       | Device ID, unsigned 8-bit integer |
| 1-4     | Sequence number, unsigned 32-bit integer, little-endian |

The first sequence number is 0. It increments once for each notification sent
while a client is connected and wraps back to 0 after `4,294,967,295`. BLE
notifications are unacknowledged, so a sequence gap at the computer means one
or more notifications may have been missed. The payload contains no text,
timestamp, or framing bytes.

Settings you may need to change:

- **Device ID:** change `-D BLE_DEVICE_ID=1` in `[env:ble_slave]` in
  `platformio.ini`. It must be from 0 to 255. The advertised name is generated
  as `ble_slave_<ID>`, so rebuild and reflash after changing it.
- **Packet rate:** `PACKET_INTERVAL_MS` in `src/ble_slave/main.cpp` is 100 ms.
  Change it only if you want a rate other than 10 Hz.
- **BLE service and characteristic UUIDs:** leave these as-is unless you also
  update the computer-side client to use the replacement UUIDs.
- **Bluetooth on the computer:** the computer needs a BLE-capable adapter and
  client software. Wi-Fi credentials, Wi-Fi channel, and the ESP-NOW channel
  are not used by this firmware.
- **USB upload/monitor port:** select the ESP32-S3's serial port in PlatformIO
  if it cannot identify the board automatically. Monitor speed is 115200 baud.

## BLE board-to-board loss test

When the computer does not have a BLE adapter, two ESP32-S3 boards can test the
link directly. The server is the BLE peripheral; the client scans for it and
writes incrementing 32-bit sequence numbers at 100 Hz. The server estimates
missing packets from sequence-number gaps and prints statistics to USB serial
every five seconds. Loss is calculated as `lost / (received + lost)`. The
maximum gap is the largest observed time between two received packets in that
reporting window. Notifications/writes that are lost after the last received
packet in a window cannot be inferred until a later sequence arrives.

Connect the server board to COM11 and the client board to COM13. From the
repository root, build and upload each firmware:

```powershell
pio run -e ble_server
pio run -e ble_server -t upload
pio run -e ble_client
pio run -e ble_client -t upload
```

The upload ports are set in `platformio.ini`. Open a serial monitor on COM11 at
115200 baud to read the server's loss and gap statistics; COM13 reports the
client's attempted writes and local write failures. Start the server before the
client. Pairing is not required. The server restarts advertising after a client
disconnects, and the client scans and reconnects automatically.

The packet payload is four bytes: an unsigned 32-bit sequence number in
little-endian order. At 100 Hz, the nominal interval is 10 ms; the client
interval is configured by `PACKET_INTERVAL_US` in
`src/ble_client/main.cpp`. BLE connection interval and radio scheduling can
cause actual send and receive times to vary.