# Firmware by experiment

| Source | Profiles / use |
|---|---|
| [Test 9 BLE client](test_09/ble_client/main.cpp) | `ble_client` |
| [Test 11 BLE server](test_11/ble_server/main.cpp) | `ble_server`, `ble_server_car_1/2/3`; shared by Tests 9–11 |
| [Test 12 car](test_12/car/main.cpp) | `ble_test12_car_1/2/3` |
| [Test 13 modem](test_13/esp_esp_ble_modem/main.cpp) | `esp_esp_ble_modem` |
| [Test 13 car](test_13/esp_esp_ble_car/main.cpp) | `esp_esp_ble_car`, `esp_esp_ble_car_2/3` |
| [Retained ESP-NOW](legacy/espnow/README.md) | Current sources, historical revisions uncertain |

One root `platformio.ini` selects the sources and pins the platform/library
versions. Completed experiment firmware should remain stable; place new
experimental changes under a new test number. Missing historical firmware
folders remain absent.
