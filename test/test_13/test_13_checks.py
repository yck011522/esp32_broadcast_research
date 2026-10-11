"""Offline transport and acceptance checks for the serial/BLE modem experiment."""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import copy
import unittest

from test.test_12.test_12_ble_pc import ROBOT_FORMAT
from test.test_13.test_13_ble_modem import HexLines, assess, fields, world_payload


def wire(car=2):
    data = ROBOT_FORMAT.pack(car, 65535, 0, 0, b"A\n\0XYZ", 1, 2, -4, 5, 12000)
    return data.hex().encode()+b"\n"


def valid_summary():
    return {"cars": [2, 3], "commands_sent": 250, "framing_errors": 0, "unexpected_cars": 0,
            "modem_baseline": {"io": {"received": 0}, "links": {2: {"tx": 0, "disconnects": 0}, 3: {"tx": 0, "disconnects": 0}}},
            "modem_final": {"health": {"enabled": 0, "links": 0}, "io": {"received": 250},
                            "links": {2: {"tx": 250, "disconnects": 1}, 3: {"tx": 250, "disconnects": 1}}},
            "server_baselines": {2: {"total_received": 10}, 3: {"total_received": 20}},
            "server_finals": {2: {"total_received": 260}, 3: {"total_received": 270}},
            "statistics": [{"car": car, "window_s": 5, "received": 100, "world_attempted": 250,
                            "seq_lost": 0, "malformed": 0, "ignored": 0, "echo_age_samples": 100,
                            "echo_age_avg_ms": 30, "echo_age_max_ms": 55, "max_gap_s": .06}
                           for car in (2, 3)]}


class Test13Checks(unittest.TestCase):
    def test_fragmented_and_combined_reads_preserve_packets(self):
        decoder = HexLines()
        data = b"#MODEM ready=2\n"+wire(2)+wire(3)
        frames = []
        for start in range(0, len(data), 7):
            frames.extend(decoder.feed(data[start:start+7]))
        self.assertEqual([kind for kind, value in frames], ["diagnostic", "telemetry", "telemetry"])
        self.assertEqual(frames[1][1][0], 2)
        self.assertEqual(frames[2][1][0], 3)
        self.assertEqual(len(frames[1][1]), 21)

    def test_invalid_and_oversized_lines_recover_at_next_newline(self):
        decoder = HexLines(maximum=48)
        frames = decoder.feed(b"g"*42+b"\n"+b"f"*100+b"\n"+wire())
        self.assertEqual([kind for kind, value in frames], ["invalid", "invalid", "telemetry"])

    def test_uppercase_crlf_and_no_whitespace_in_payload(self):
        decoder = HexLines()
        self.assertEqual(decoder.feed(wire().strip().upper()+b"\r\n")[0][0], "telemetry")
        self.assertEqual(decoder.feed(b" "+wire())[0][0], "invalid")

    def test_command_hex_is_one_line_even_with_binary_newlines(self):
        packet = world_payload(65535)
        self.assertEqual(len(packet), 20)
        line = packet.hex().encode()+b"\n"
        self.assertEqual(len(line), 41)
        self.assertEqual(line.count(b"\n"), 1)
        self.assertEqual(bytes.fromhex(line.decode()), packet)

    def test_diagnostic_numbers_and_address(self):
        self.assertEqual(fields("#LINK car=2 mtu=64 interval_ms=15.00 mac=68:ee:8f:46:33:45"),
                         {"car": 2, "mtu": 64, "interval_ms": 15., "mac": "68:ee:8f:46:33:45"})

    def test_both_links_and_server_totals_are_required_to_pass(self):
        summary = valid_summary()
        self.assertTrue(assess(summary, True))
        self.assertEqual(summary["measurements"]["3"]["server_received"], 250)
        for mutate in (
            lambda s: s["statistics"][1].update(received=0),
            lambda s: s["server_finals"][3].update(total_received=269),
            lambda s: s["modem_final"]["io"].update(command_drops=1),
            lambda s: s["modem_final"]["io"].update(seq_errors=1),
            lambda s: s["modem_final"]["links"][2].update(disconnects=2),
            lambda s: s.update(framing_errors=1),
        ):
            bad = copy.deepcopy(valid_summary())
            mutate(bad)
            self.assertFalse(assess(bad, True))


if __name__ == "__main__":
    unittest.main()
