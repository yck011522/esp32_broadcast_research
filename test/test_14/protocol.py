"""Wire formats from payload.md; the ASCII W is USB-only."""
import re
import struct

WORLD = struct.Struct("<HB4B")
ROBOT = struct.Struct("<BHHB6sHBhhH")
CAR_PORTS = {1: "COM7", 2: "COM4", 3: "COM5", 4: "COM8"}
USB_IDENTITIES = {"COM3": "68:EE:8F:4B:5B:4C", "COM4": "68:EE:8F:46:33:44",
                  "COM5": "68:EE:8F:4B:5B:18", "COM7": "1C:DB:D4:43:73:EC", "COM8": "68:EE:8F:4B:4F:34"}


def world_line(sequence):
    # Dummy intent keeps all movement flags STOP; no motor outputs in firmware.
    return b"W"+WORLD.pack(sequence, 0, 0, 0, 0, 0).hex().encode("ascii")+b"\n"


class Lines:
    def __init__(self, maximum=512):
        self.maximum = maximum
        self.buffer = bytearray()
        self.overflow = False

    def feed(self, chunk):
        rows = []
        for byte in chunk:
            if byte == 10:
                rows.append(None if self.overflow else bytes(self.buffer).removesuffix(b"\r"))
                self.buffer.clear()
                self.overflow = False
            elif not self.overflow:
                if len(self.buffer) < self.maximum:
                    self.buffer.append(byte)
                else:
                    self.overflow = True
        return rows


def decode_robot(line):
    if line is None or not re.fullmatch(rb"[a-fA-F0-9]{42}", line):
        raise ValueError("RobotState must be 42 hexadecimal characters")
    values = ROBOT.unpack(bytes.fromhex(line.decode("ascii")))
    if values[0] not in CAR_PORTS:
        raise ValueError("Unknown car ID")
    return dict(zip(("car", "telemetry_seq", "world_seq", "world_packets_lost_5s", "nfc",
                     "nfc_age_ms", "follow_state", "left_speed", "right_speed", "battery_mv"), values))


def diagnostic_fields(line):
    result = {}
    for key, value in re.findall(r"(\w+)=([^\s]+)", line):
        try:
            result[key] = float(value) if "." in value else int(value)
        except ValueError:
            result[key] = value
    return result


def sequence_delta(previous, current, elapsed):
    """Infer continuity conservatively. No wire session/reset identity exists."""
    if previous is None:
        return 0, False, False
    delta = (current-previous) & 0xffff
    if delta == 0:
        return 0, False, True
    plausible = max(20, int(elapsed*20)+20)
    if delta >= 0x8000 or delta > plausible:
        return 0, True, False
    return delta-1, False, False
