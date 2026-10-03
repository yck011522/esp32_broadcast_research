The purpose of this repository is to test communication between a master radio and multiple slave radios for real time control of up to four RC cars.

Goal:
Control signals sent from computer to all cars with low latency (trusting ESP-NOW) and high reliability (loss of control gap < 200ms).


Hardware System:
- Windows Computer connected to Master Radio via USB.
- 1 Master Radio (ESP32-S3)
- 4 Slave Radios (ESP32-S3)