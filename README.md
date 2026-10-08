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
