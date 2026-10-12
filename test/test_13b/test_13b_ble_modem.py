"""Test 13b: shared ESP32 BLE modem, four cars, separate run results."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path
from test.test_13.test_13_ble_modem import main as run_shared

CARS = (2, 3, 1, 4)
SERVER_PORTS = ("COM4", "COM5", "COM7", "COM8")
PROFILES = ("esp_esp_ble_modem_13b", "esp_esp_ble_car_2", "esp_esp_ble_car_3",
            "esp_esp_ble_car_1", "esp_esp_ble_car_4")


def main(argv=None):
    return run_shared(argv, experiment="13b", default_cars=CARS,
                      default_server_ports=list(SERVER_PORTS),
                      result_root=Path(__file__).parent / "results/runs",
                      firmware_profiles=PROFILES, extra_sources=(Path(__file__),), description=__doc__,
                      default_max_echo_age_ms=5000)


if __name__ == "__main__":
    raise SystemExit(main())
