"""Independent checks on retained Test 14 summaries, without raw packet logs."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse
import json
import hashlib
from pathlib import Path


def audit(summary):
    failures, proof = [], {}
    scenario = summary["scenario"]
    rows = summary.get("statistics", [])
    offset = rows[0]["elapsed_s"]-rows[0]["traffic_s"] if rows else 0
    before, after = summary.get("car_baselines", {}), summary.get("car_finals", {})
    before = {str(k): v for k, v in before.items()}
    after = {str(k): v for k, v in after.items()}
    history = summary.get("car_health_history", [])
    if summary["status"] != "passed":
        failures.append("Runner did not pass")
    if summary.get("server_monitoring"):
        for car in ("1", "2", "3", "4"):
            if after.get(car, {}).get("connected") != 1 or after.get(car, {}).get("subscribed") != 1:
                failures.append(f"Car {car}: final development serial snapshot not connected/subscribed")
    if scenario == "bench" and summary.get("server_monitoring"):
        receipts = {car: after[car]["total_rx"]-before[car]["total_rx"] for car in before}
        proof["car_receipt_counts"] = receipts
        if any(count != summary["pc_submitted"] for count in receipts.values()):
            failures.append("Independent car receipt totals differ from PC submissions")
    if scenario == "mailbox":
        receipts = {car: after[car]["total_rx"]-before[car]["total_rx"] for car in before}
        proof.update(pc_submitted=summary["pc_submitted"], car_receipts=receipts,
                     burst_observed=any(e["record_type"] == "mailbox_burst" for e in summary["events"]))
        if not proof["burst_observed"] or len(receipts) != 4 or not all(0 < count < summary["pc_submitted"] for count in receipts.values()):
            failures.append("Burst snapshot supersession was not demonstrated")
    if scenario == "expiry":
        samples = [r for r in history if r["car"] == 1 and 25 <= r["elapsed_s"]-offset < 40
                   and r.get("connected") == 1 and r.get("rx_hz") == 0 and r.get("last_world") == 0]
        proof["returned_car_without_expired_world_samples"] = [round(r["elapsed_s"]-offset, 3) for r in samples]
        if not samples:
            failures.append("Returning car was not observed waiting without an expired WorldState")
    if scenario == "idle":
        changes = {car: after[car]["connects"]-before[car]["connects"] for car in before}
        proof["connection_count_changes"] = changes
        proof["idle_connected_led_off"] = {str(car): any(r["car"] == car and 20 <= r["elapsed_s"]-offset <= 40
            and r.get("connected") == 1 and r.get("subscribed") == 1 and r.get("rx_hz") == 0 and r.get("led") == 0
            for r in history) for car in (1, 2, 3, 4)}
        if len(changes) != 4 or any(changes.values()) or not all(proof["idle_connected_led_off"].values()):
            failures.append("Idle connections with receive LEDs off were not preserved")
    if scenario == "car-watchdog":
        proof["car_1_uptime_reset_observed"] = any(e["record_type"] == "car_uptime_reset" and e.get("car") == 1 for e in summary["events"])
        if not proof["car_1_uptime_reset_observed"]:
            failures.append("Car watchdog reset was not observed")
    if scenario == "modem-watchdog":
        changes = {car: after[car]["connects"]-before[car]["connects"] for car in before}
        proof["car_connection_count_changes"] = changes
        if len(changes) != 4 or not all(change > 0 for change in changes.values()):
            failures.append("Every car rejoining after modem worker stall was not demonstrated")
    if scenario in ("reconnect", "double"):
        required = (1, 2, 3, 4) if scenario == "reconnect" else (2, 3)
        resumed = {str(car): [e["gap_s"] for e in summary["events"]
                              if e["record_type"] == "traffic_resumed" and e.get("car") == car] for car in required}
        proof["observed_telemetry_outages_s"] = resumed
        if not all(gaps and all(9 <= gap <= 22 for gap in gaps) for gaps in resumed.values()):
            failures.append("Controlled ten-second outages did not all recover within the planned allowance")
    if scenario == "missing-start":
        proof["car_1_missing_then_connected"] = any(r["car"] == 1 and r.get("connected") == 0 for r in history) and after.get("1", {}).get("connected") == 1
        if not proof["car_1_missing_then_connected"]:
            failures.append("Missing-at-startup recovery was not observed")
    return {"passed": not failures, "failures": failures, "proof": proof}


def reassess_stale_startup(summary):
    """Narrow reassessment of the old disconnected-startup-snapshot check.

    Original results remain immutable. All communication and continuity criteria
    are rechecked; a real disconnect or another original failure cannot be waived.
    """
    failures, stale = [], []
    if summary.get("scenario") != "bench" or not summary.get("server_monitoring"):
        return {"passed": False, "failures": ["Requires a monitored bench/stability capture"]}
    if summary.get("errors"):
        failures.append("Original runtime errors remain")
    rows = summary["statistics"]
    offset = rows[0]["elapsed_s"]-rows[0]["traffic_s"]
    end = offset+summary["active_s"]
    history = summary["car_health_history"]
    commands = summary["pc_submitted"]
    proof = {}
    for car in ("1", "2", "3", "4"):
        before, after = summary["car_baselines"][car], summary["car_finals"][car]
        samples = [r for r in history if str(r["car"]) == car and offset <= r["elapsed_s"] <= end]
        connection_counts = sorted(set(r["connects"] for r in samples))
        receipt_count = after["total_rx"]-before["total_rx"]
        telemetry = summary["measurements"][car]
        proof[car] = {"car_received": receipt_count, "connection_counts_during_traffic": connection_counts,
                      "disconnect_delta": after["disconnects"]-before["disconnects"],
                      "baseline_connected": before["connected"], "baseline_subscribed": before["subscribed"]}
        if receipt_count != commands or after["disconnects"] != before["disconnects"]:
            failures.append(f"Car {car}: receipt mismatch or actual disconnect")
        if after["total_lost"] != before["total_lost"] or after["notify_errors"] != before["notify_errors"]:
            failures.append(f"Car {car}: car loss/notification error counter increased")
        if len(connection_counts) != 1 or connection_counts[0] != after["connects"]:
            failures.append(f"Car {car}: connection changes during traffic")
        if after["connected"] != 1 or after["subscribed"] != 1:
            failures.append(f"Car {car}: unhealthy final link observation")
        if telemetry["inferred_seq_lost"] or telemetry["sequence_discontinuities"] or telemetry["duplicates"]:
            failures.append(f"Car {car}: telemetry continuity anomaly")
        if not 19.5 <= telemetry["notifications"]/summary["active_s"] <= 20.5:
            failures.append(f"Car {car}: telemetry rate outside target")
        if summary["traffic_final_health"][car]["silence_s"] > .5:
            failures.append(f"Car {car}: traffic ended without fresh telemetry")
        if after["connects"] != before["connects"]:
            if before["connected"] == 0 and before["subscribed"] == 0 and after["connects"] == before["connects"]+1:
                stale.append(car)
            else:
                failures.append(f"Car {car}: baseline discrepancy is not the known stale snapshot")
    allowed = {f"Car {car}: unexpected reconnect" for car in stale}
    for failure in summary.get("validation_failures", []):
        if failure not in allowed:
            failures.append("Original failure not explained by stale startup snapshot: "+failure)
    if not 49 <= commands/summary["active_s"] <= 51:
        failures.append("PC submission rate outside target")
    if any(e["record_type"] == "traffic_warning" and not e.get("expected") for e in summary["events"]):
        failures.append("Unexpected traffic warning")
    if any(e["record_type"] == "car_uptime_reset" and offset <= e["elapsed_s"] <= end for e in summary["events"]):
        failures.append("Car reset during traffic")
    return {"passed": not failures, "original_status": summary["status"], "failures": sorted(set(failures)),
            "stale_startup_snapshot_cars": stale, "proof": proof,
            "note": "Reassessment of communication evidence; original CSV and summary remain unchanged."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--reassess-stale-startup", action="store_true")
    args = parser.parse_args(argv)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    result = reassess_stale_startup(summary) if args.reassess_stale_startup else audit(summary)
    result["source_summary_sha256"] = hashlib.sha256(args.summary.read_bytes()).hexdigest()
    name = "startup_reassessment.json" if args.reassess_stale_startup else "audit.json"
    (args.summary.parent/name).write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return not result["passed"]


if __name__ == "__main__":
    raise SystemExit(main())
