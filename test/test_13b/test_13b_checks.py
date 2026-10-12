"""Four-peer configuration, result isolation and delivery acceptance checks."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test.test_13.test_13_checks import valid_summary
from test.test_13.test_13_ble_modem import Log, assess, enforce_traffic_limits
from test.test_13b import test_13b_ble_modem as test13b
from test.test_13b.flash_test_13b import BOARDS, verify_boards


class Test13bChecks(unittest.TestCase):
    def test_wrapper_uses_four_cars_and_separate_results(self):
        with patch.object(test13b, "run_shared", return_value=0) as run:
            self.assertEqual(test13b.main(["--duration-seconds", "10"]), 0)
        settings = run.call_args.kwargs
        self.assertEqual(settings["default_cars"], (2, 3, 1, 4))
        self.assertEqual(settings["default_server_ports"], ["COM4", "COM5", "COM7", "COM8"])
        self.assertEqual(settings["experiment"], "13b")
        self.assertEqual(settings["result_root"].parents[1].name, "test_13b")
        self.assertIn("esp_esp_ble_modem_13b", settings["firmware_profiles"])
        self.assertEqual(settings["default_max_echo_age_ms"], 5000)

    def test_age_spikes_and_configurable_limit_boundaries(self):
        state = SimpleNamespace(last_receive=10, confirmed_echo=True, ages=[1200])
        enforce_traffic_limits(2, state, 10, 0, 5000, 3)
        with self.assertRaisesRegex(RuntimeError, "exceeded 1000 ms"):
            enforce_traffic_limits(2, state, 10, 0, 1000, 3)
        state.ages = [5000]
        enforce_traffic_limits(2, state, 10, 0, 5000, 3)
        state.ages = [5000.1]
        with self.assertRaisesRegex(RuntimeError, "exceeded 5000 ms"):
            enforce_traffic_limits(2, state, 10, 0, 5000, 3)
        enforce_traffic_limits(2, state, 10, 0, 0, 3)

    def test_silence_is_independent_of_echo_age_limit(self):
        state = SimpleNamespace(last_receive=6, confirmed_echo=True, ages=[1200])
        with self.assertRaisesRegex(RuntimeError, "telemetry silence"):
            enforce_traffic_limits(1, state, 10, 0, 5000, 3)
        enforce_traffic_limits(1, state, 10, 0, 5000, 10)
        enforce_traffic_limits(1, state, 100, 0, 0, 0)

    def test_every_board_identity_is_checked_before_flash(self):
        ports = {name: SimpleNamespace(vid=0x303a, pid=0x1001, serial_number=serial)
                 for name, serial, _ in BOARDS}
        verify_boards(ports)
        ports["COM8"].serial_number = "incorrect"
        with self.assertRaises(RuntimeError):
            verify_boards(ports)

    def test_all_four_server_totals_are_required(self):
        summary = valid_summary()
        summary["cars"] = [2, 3, 1, 4]
        for car in (1, 4):
            for phase in ("modem_baseline", "modem_final"):
                summary[phase]["links"][car] = copy.deepcopy(summary[phase]["links"][3])
            for phase in ("server_baselines", "server_finals"):
                summary[phase][car] = copy.deepcopy(summary[phase][3])
            row = copy.deepcopy(summary["statistics"][1])
            row["car"] = car
            summary["statistics"].append(row)
        self.assertTrue(assess(summary, True))
        summary["server_finals"][4]["total_received"] -= 1
        self.assertFalse(assess(summary, True))

    def test_log_names_and_metadata_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(output_dir=Path(folder), port="COM3", cars=[2, 3, 1, 4],
                                   server_ports=["COM4", "COM5", "COM7", "COM8"],
                                   duration_seconds=10, experiment="13b", label="far_10s")
            with patch("test.test_13.test_13_ble_modem.capture_test13"):
                log = Log(args)
                try:
                    self.assertTrue(log.path.name.startswith("ble_test_13b_"))
                    self.assertEqual(log.summary["experiment"], "13b")
                    self.assertTrue(log.summary["server_monitoring"])
                finally:
                    log.close()


if __name__ == "__main__":
    unittest.main()
