"""Hardware framing probe; intentionally sends malformed lines and a duplicate.

Flash Test 13 first, close serial monitors, then run this script. It exercises
the modem's opaque forwarding contract, not the normal rate acceptance test.
"""

# Support direct script execution as well as python -m.
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path
import struct
from types import SimpleNamespace

from test.test_13.test_13_ble_modem import Bench, Log, fields


def main():
    args = SimpleNamespace(port="COM3", cars=[2, 3], server_ports=["COM4", "COM5"],
                           duration_seconds=0, output_dir=None)
    log = Log(args)
    log.summary["experiment"] = "transport_probe"
    bench = None
    completed = False
    try:
        bench = Bench(args, log)
        bench.prepare()
        log.phase = "transport_probe"
        for malformed in (b"zz" + b"0"*38 + b"\n", b"f"*100 + b"\n"):
            bench.modem.write(malformed)
            log.event("probe_invalid_line", data_hex=malformed.hex())
        for index, sequence in enumerate((65535, 65535, 0)):
            # Includes binary NUL and newline; neither is a serial delimiter
            # after hex encoding. The repeated sequence must still be forwarded.
            packet = struct.pack("<H", sequence) + bytes(range(18))
            line = packet.hex().encode() + b"\n"
            if index == 0:
                bench.modem.write(line[:17])
                bench.wait(.01)
                bench.modem.write(line[17:])
            else:
                bench.modem.write(line.upper().rstrip(b"\n") + b"\r\n" if index == 1 else line)
            log.event("probe_world_sent", sequence=sequence, data_hex=packet.hex())
            bench.wait(.03)
        bench.wait(5.2, request_status=True)
        completed = True
    except Exception as exc:
        log.summary["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if bench:
            try:
                bench.shutdown()
            except Exception as exc:
                log.summary["errors"].append(f"Cleanup: {type(exc).__name__}: {exc}")
            finally:
                bench.close()
        failures = list(log.summary["errors"])
        if not completed:
            failures.append("Probe did not finish")
        if completed and not failures:
            before = log.summary["modem_baseline"]
            after = log.summary["modem_final"]
            actual = {key: after["io"].get(key, 0)-before["io"].get(key, 0)
                      for key in ("received", "seq_lost", "seq_errors", "malformed",
                                  "command_drops", "telemetry_drops", "malformed_telemetry", "partial_writes")}
            log.summary["probe_io_deltas"] = actual
            expected = {key: 0 for key in actual}
            expected.update(received=3, seq_errors=1, malformed=2)
            if actual != expected:
                failures.append(f"Modem counters: {actual}, expected {expected}")
            for car in args.cars:
                writes = after["links"][car]["tx"]-before["links"][car]["tx"]
                accepted = log.summary["server_finals"][car]["total_received"]-log.summary["server_baselines"][car]["total_received"]
                messages = Path(log.summary["serial_logs"][str(car)]).read_text().splitlines()
                duplicates = sum(fields(line).get("duplicates", 0) for line in messages if "car world:" in line)
                if (writes, accepted, duplicates) != (3, 2, 1):
                    failures.append(f"Car {car}: writes/accepted/duplicates={(writes, accepted, duplicates)}")
                if log.summary["server_finals"][car]["total_lost"] != log.summary["server_baselines"][car]["total_lost"]:
                    failures.append(f"Car {car}: sequence gaps")
            if after["health"]["enabled"] or after["health"]["links"] or log.summary["framing_errors"]:
                failures.append("Modem shutdown or return framing failed")
        log.summary["validation_failures"] = failures
        log.summary["status"] = "passed" if not failures else "failed"
        log.close()
    print(f"Transport probe: {log.summary['status']}; {log.path.with_suffix('.summary.json')}")
    if failures:
        print("; ".join(failures))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
