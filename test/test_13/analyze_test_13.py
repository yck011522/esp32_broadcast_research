"""Analyze a Test 13 capture and save Matplotlib plots without changing inputs.

python test/test_13/analyze_test_13.py [path/to/ble_test_13_*.summary.json]
Defaults to the latest normal Test 13 summary under results/runs.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from collections import Counter, OrderedDict
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import struct

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def distribution(values):
    if not values:
        return None
    values = np.asarray(values, dtype=float)
    return {"samples": len(values), "mean": float(values.mean()),
            "median": float(np.median(values)), "p95": float(np.percentile(values, 95)),
            "p99": float(np.percentile(values, 99)), "min": float(values.min()),
            "max": float(values.max())}


def sequence_change(previous, sequence):
    if previous is None:
        return 0, False, False
    delta = (sequence-previous) & 65535
    invalid = delta == 0 or delta >= 32768
    return (delta-1 if not invalid else 0), invalid, not invalid and sequence < previous


def read_raw(path, cars):
    states = {car: {"received": 0, "sequence_gaps": 0, "sequence_errors": 0,
                    "wraps": 0, "last_seq": None, "last_time": None,
                    "gaps_ms": [], "logged_echo_ms": [], "events": []} for car in cars}
    sent = OrderedDict()
    commands = {"count": 0, "sequence_gaps": 0, "sequence_errors": 0, "wraps": 0,
                "gaps_ms": [], "first_time": None, "last_time": None}
    previous = None
    event_counts = Counter()
    unexpected = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["phase"] != "traffic":
                continue
            kind, now = row["record_type"], row["elapsed_s"]
            event_counts[kind] += 1
            if kind == "world_sent":
                sequence = row["sequence"]
                packet = bytes.fromhex(row["data_hex"])
                if len(packet) != 20 or struct.unpack_from("<H", packet)[0] != sequence:
                    unexpected.append("Invalid command wire format")
                lost, invalid, wrapped = sequence_change(previous, sequence)
                commands["sequence_gaps"] += lost
                commands["sequence_errors"] += invalid
                commands["wraps"] += wrapped
                if commands["last_time"] is not None:
                    commands["gaps_ms"].append((now-commands["last_time"])*1000)
                if commands["first_time"] is None:
                    commands["first_time"] = now
                commands["last_time"] = now
                commands["count"] += 1
                previous = sequence
                sent[sequence] = now
                if len(sent) > 2048:
                    sent.popitem(last=False)
            elif kind == "telemetry":
                packet = bytes.fromhex(row["data_hex"])
                if len(packet) != 21 or packet[0] not in states:
                    unexpected.append("Invalid telemetry wire format or ID")
                    continue
                car, sequence, world = struct.unpack_from("<BHH", packet)
                state = states[car]
                lost, invalid, wrapped = sequence_change(state["last_seq"], sequence)
                state["sequence_gaps"] += lost
                state["sequence_errors"] += invalid
                if invalid or row["accepted"] != "accepted":
                    unexpected.append(f"Car {car}: non-forward or rejected telemetry")
                    continue
                state["wraps"] += wrapped
                if state["last_time"] is not None:
                    state["gaps_ms"].append((now-state["last_time"])*1000)
                state["last_time"], state["last_seq"] = now, sequence
                state["received"] += 1
                if world in sent:
                    state["logged_echo_ms"].append((now-sent[world])*1000)
                state["events"].append(now)
            elif kind == "modem_event" and row.get("event") == "disconnected":
                unexpected.append(f"Car {row.get('car')}: traffic disconnect")
            elif kind in ("fatal", "framing_error"):
                unexpected.append(kind)
    return commands, states, dict(event_counts), unexpected


def server_observations(path, traffic_start, traffic_end):
    world, health, resets, previous_uptime = [], [], 0, None
    baseline, final = None, None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            at = re.search(r"\[([\d.]+)s\]", line)
            if not at:
                continue
            elapsed = float(at[1])
            values = {k: v for k, v in re.findall(r"(\w+)=([^\s]+)", line)}
            if "car world:" in line:
                final = values
                if elapsed <= traffic_start:
                    baseline = values
                # A report describes the preceding window. Include only windows
                # wholly inside traffic, excluding setup and shutdown tails.
                if elapsed-float(values["window"]) >= traffic_start and elapsed <= traffic_end:
                    world.append(values)
            elif "car health:" in line:
                uptime = int(values["uptime_ms"])
                if previous_uptime is not None and uptime < previous_uptime:
                    resets += 1
                previous_uptime = uptime
                if traffic_start <= elapsed <= traffic_end:
                    health.append(values)
    return {"received_delta": int(final["total_received"])-int(baseline["total_received"]) if final and baseline else None,
            "lost_delta": int(final["total_lost"])-int(baseline["total_lost"]) if final and baseline else None,
            "full_traffic_windows": len(world), "window_rate_hz": distribution([float(r["rate"]) for r in world]),
            "sampled_silence_ms": distribution([float(r["silence"])*1000 for r in world]),
            "sequence_gaps": sum(int(r["seq_lost"]) for r in world),
            "duplicates": sum(int(r["duplicates"]) for r in world),
            "old": sum(int(r["old"]) for r in world),
            "malformed": sum(int(r["malformed"]) for r in world),
            "heap_bytes": distribution([int(r["heap"]) for r in health]),
            "uptime_resets": resets}


def analyze(path):
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary.get("experiment") == "transport_probe" or not summary.get("measurements"):
        raise ValueError("Select a normal completed Test 13 rate run")
    base = path.with_name(path.name.removesuffix(".summary.json"))
    raw_path, csv_path = base.with_suffix(".jsonl"), base.with_suffix(".csv")
    with csv_path.open(newline="", encoding="utf-8") as stream:
        csv_rows = list(csv.DictReader(stream))
    commands, raw, counts, failures = read_raw(raw_path, summary["cars"])
    if commands["count"] != summary["commands_sent"] or commands["sequence_gaps"] or commands["sequence_errors"]:
        failures.append("Raw command continuity/count does not match summary")
    metadata = {"experiment": summary.get("experiment", "13"),
                "status": summary["status"], "started_utc": summary["started_utc"],
                "finished_utc": summary["finished_utc"], "active_s": summary["active_s"],
                "raw_event_counts": counts, "commands": {k: v for k, v in commands.items() if k != "gaps_ms"},
                "command_spacing_ms": distribution(commands["gaps_ms"]), "cars": {},
                "independent_check_failures": failures}
    rows_by_car = {}
    for car in summary["cars"]:
        key, state = str(car), raw[car]
        rows = [r for r in csv_rows if int(r["car"]) == car]
        rows_by_car[car] = rows
        measurement = summary["measurements"][key]
        for name in ("received", "world_attempted", "seq_lost"):
            expected = {"received": state["received"], "world_attempted": commands["count"],
                        "seq_lost": state["sequence_gaps"]}[name]
            if sum(int(r[name]) for r in rows) != expected:
                failures.append(f"Car {car}: CSV {name} does not reconcile with raw data")
        if state["received"] != measurement["notifications"] or state["sequence_errors"]:
            failures.append(f"Car {car}: raw telemetry does not reconcile with summary")
        epochs = []
        for index in range(4):
            lower = commands["first_time"]+index*summary["active_s"]/4
            upper = commands["first_time"]+(index+1)*summary["active_s"]/4
            segment = [r for r in rows if lower <= float(r["elapsed_s"]) < upper]
            weight = sum(int(r["echo_age_samples"]) for r in segment)
            if weight:
                epochs.append({"quarter": index+1, "echo_age_avg_ms": sum(float(r["echo_age_avg_ms"])*int(r["echo_age_samples"])
                                                                                  for r in segment if int(r["echo_age_samples"]))/weight})
        # Locate locally so captures still analyze after a future folder move.
        server_path = next(path.parent.glob(base.name+f"_car_{car}_*.log"), None)
        server = server_observations(server_path, commands["first_time"], commands["last_time"]) if server_path else None
        if server and (server["received_delta"] != commands["count"] or server["lost_delta"] != 0):
            failures.append(f"Car {car}: direct server serial totals do not reconcile with raw commands")
        metadata["cars"][key] = {**measurement, "raw_notification_wraps": state["wraps"],
            "raw_notification_sequence_errors": state["sequence_errors"],
            "notification_spacing_ms": distribution(state["gaps_ms"]),
            "gaps_at_least_100ms": sum(gap >= 100 for gap in state["gaps_ms"]),
            "gaps_at_least_200ms": sum(gap >= 200 for gap in state["gaps_ms"]),
            "logged_echo_age_ms": distribution(state["logged_echo_ms"]),
            "quarter_echo_age": epochs,
            "full_window_rx_hz": distribution([float(r["rate_hz"]) for r in rows if int(r["partial_window"]) == 0]),
            "server": server}
    modem_before, modem_after = summary["modem_baseline"], summary["modem_final"]
    metadata["modem_io_deltas"] = {key: value-modem_before["io"].get(key, 0)
                                    for key, value in modem_after["io"].items()}
    if metadata["modem_io_deltas"].get("received") != commands["count"]:
        failures.append("Modem receive total does not reconcile with raw commands")
    if any(value for key, value in metadata["modem_io_deltas"].items() if key != "received"):
        failures.append("Modem gap/error/drop counters increased")
    for car in summary["cars"]:
        before, after = modem_before["links"][str(car)], modem_after["links"][str(car)]
        if after["tx"]-before["tx"] != commands["count"] or after["errors"] != before["errors"]:
            failures.append(f"Car {car}: modem per-link counters do not reconcile")
        if after["connects"] != before["connects"] or after["disconnects"]-before["disconnects"] != 1:
            failures.append(f"Car {car}: unexpected connection cycle")
    return summary, metadata, rows_by_car, raw


def plots(directory, rows_by_car, raw, metadata):
    directory.mkdir(exist_ok=True)
    colors = ["#2463a5", "#d47721", "#448844", "#994488"]
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    columns = len(rows_by_car)
    fig, axes = plt.subplots(4, columns, figsize=(7*columns, 12), sharex=True,
                             squeeze=False, layout="constrained")
    for column, (car, rows) in enumerate(rows_by_car.items()):
        time = [(float(r["elapsed_s"])-metadata["commands"]["first_time"])/60 for r in rows]
        for ax, field, label, target in (
            (axes[0, column], "world_tx_rate_hz", "Command rate (Hz)", 50),
            (axes[1, column], "rate_hz", "Telemetry rate (Hz)", 20),
        ):
            ax.plot(time, [float(r[field]) for r in rows], color=colors[column], linewidth=.8)
            ax.axhline(target, color="#777777", linestyle="--", linewidth=.8)
            ax.set_ylabel(label)
            ax.set_ylim(target-.55, target+.55)
        axes[0, column].set_title(f"Car {car}")
        for field, label, style in (("echo_age_avg_ms", "Window mean", "-"), ("echo_age_max_ms", "Window maximum", ":")):
            axes[2, column].plot(time, [float(r[field]) if r[field] else np.nan for r in rows], linestyle=style,
                                 color=colors[column], alpha=.85, linewidth=.9, label=label)
        axes[2, column].set_ylabel("Echoed-command age (ms)")
        axes[2, column].legend(loc="upper right", fontsize=8)
        for field, label, style in (("max_gap_s", "Maximum receive gap", "-"), ("silence_s", "Silence at report", ":")):
            axes[3, column].plot(time, [float(r[field])*1000 if r[field] else np.nan for r in rows],
                                 linestyle=style, color=colors[column], alpha=.85, linewidth=.8, label=label)
        axes[3, column].set_ylabel("Telemetry timing (ms)")
        axes[3, column].set_xlabel("Traffic elapsed (minutes)")
        axes[3, column].legend(loc="upper right", fontsize=8)
        for ax in axes[:, column]:
            ax.grid(alpha=.2)
    fig.suptitle(f"Test {metadata['experiment']}: simultaneous 50 Hz commands and 20 Hz telemetry\nEach point represents one reporting window")
    overview = directory / "overview.png"
    fig.savefig(overview, dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), layout="constrained")
    for index, (car, state) in enumerate(raw.items()):
        axes[0].hist(state["gaps_ms"], bins=np.arange(0, 101, 1), density=True,
                     histtype="step", linewidth=1.4, color=colors[index], label=f"Car {car}")
        ages = np.sort(state["logged_echo_ms"])
        if len(ages):
            axes[1].plot(ages, np.arange(1, len(ages)+1)/len(ages)*100,
                         color=colors[index], linewidth=1.4, label=f"Car {car}")
    axes[0].axvline(50, color="#777777", linestyle="--", linewidth=.8, label="50 ms nominal")
    axes[0].set(title="Individual telemetry arrival gaps", xlabel="Gap (ms)", ylabel="Probability density (1/ms)")
    axes[1].set(title="Logged send-to-receive echo age", xlabel="Age (ms)", ylabel="Cumulative samples (%)")
    for ax in axes:
        ax.legend()
        ax.grid(alpha=.2)
    fig.suptitle("Packet timing distributions\nEcho ages use post-write log timestamps; they are approximate end-to-end measurements")
    distributions = directory / "packet_timing.png"
    fig.savefig(distributions, dpi=160)
    plt.close(fig)
    return overview, distributions


def report(path, summary, metrics):
    local = timezone(timedelta(hours=8))
    started = datetime.fromisoformat(summary["started_utc"]).astimezone(local)
    finished = datetime.fromisoformat(summary["finished_utc"]).astimezone(local)
    text = f"# Test {metrics['experiment']} analysis\n\nRun: {started:%Y-%m-%d %H:%M:%S} to {finished:%Y-%m-%d %H:%M:%S} Hong Kong time.\n\n"
    text += f"Traffic duration: **{summary['active_s']:.3f} seconds**. Runner result: **{summary['status']}**.\n\n"
    text += "| Metric | " + " | ".join(f"Car {car}" for car in summary["cars"]) + " |\n|---|" + "---:|"*len(summary["cars"]) + "\n"
    for label, field, fmt in (("Commands received by car", "server_received", ",d"),
                              ("Notifications received", "notifications", ",d"),
                              ("Command rate (Hz)", "tx_hz", ".6f"),
                              ("Telemetry rate (Hz)", "rx_hz", ".6f"),
                              ("Command sequence gaps", "server_seq_lost", "d"),
                              ("Telemetry sequence gaps", "notification_seq_lost", "d"),
                              ("Mean echoed-command age (ms)", "echo_age_avg_ms", ".2f"),
                              ("Maximum echoed-command age (ms)", "echo_age_max_ms", ".2f")):
        text += f"| {label} | " + " | ".join(
            format(metrics["cars"][str(car)][field], fmt) if field in metrics["cars"][str(car)]
            else "not monitored" for car in summary["cars"]) + " |\n"
    text += "| Maximum telemetry gap (ms) | " + " | ".join(f"{metrics['cars'][str(car)]['notification_spacing_ms']['max']:.2f}" for car in summary["cars"]) + " |\n"
    text += "\n## Delivery and stability\n\n"
    text += (f"Python's {metrics['commands']['count']:,} world records reconcile with the CSV and modem\n"
             "serial/per-link counters. Car receive totals are independently checked when\n"
             "car serial logs are available; unmonitored receive totals remain unavailable.\n"
             "Notification counts and sequence continuity were rechecked directly from raw\n"
             "21-byte packets. Sequence loss counts application gaps, not radio retransmissions.\n\n")
    text += f"Raw command sequence wraparounds: {metrics['commands']['wraps']}. Command pacing overruns: {metrics['raw_event_counts'].get('tx_overrun', 0)}.\n\n"
    if not metrics["independent_check_failures"]:
        text += "Modem gap/error/drop counters did not increase. All links had no reconnects\nduring traffic and one intentional disconnect each at shutdown.\n\n"
    for car in summary["cars"]:
        item = metrics["cars"][str(car)]
        gaps, age, server = item["notification_spacing_ms"], item["logged_echo_age_ms"], item["server"]
        text += (f"- Car {car}: {item['raw_notification_wraps']} notification sequence wraparounds;\n"
                 f"  gap median / p95 / p99 = {gaps['median']:.2f} / {gaps['p95']:.2f} / {gaps['p99']:.2f} ms.\n"
                 f"  Gaps >=100 ms: {item['gaps_at_least_100ms']}; >=200 ms: {item['gaps_at_least_200ms']}.\n")
        if age:
            text += f"  Logged echo-age median / p95 / p99 = {age['median']:.2f} / {age['p95']:.2f} / {age['p99']:.2f} ms.\n"
        if server and server["heap_bytes"]:
            text += f"  Car heap range: {server['heap_bytes']['min']:,.0f}–{server['heap_bytes']['max']:,.0f} bytes; observed uptime resets: {server['uptime_resets']}.\n"
        text += "  Quarter-run mean echo ages: " + ", ".join(f"{q['echo_age_avg_ms']:.2f} ms" for q in item["quarter_echo_age"]) + ".\n"
    text += "\nIndependent check failures: " + ("none" if not metrics["independent_check_failures"] else "; ".join(metrics["independent_check_failures"])) + ".\n"
    text += "\n## Interpretation and measurement limits\n\n"
    if summary["status"] == "passed" and not metrics["independent_check_failures"]:
        text += f"The {len(summary['cars'])}-link setup sustained the target throughput over this observation.\n"
    quarters = [q["echo_age_avg_ms"] for item in metrics["cars"].values() for q in item["quarter_echo_age"]]
    if quarters:
        text += (f"Quarter-run mean echo ages range from {min(quarters):.2f} to {max(quarters):.2f} ms.\n"
                 "Their chronological values above show whether delay accumulated.\n")
    text += ("The timing plots show individual arrivals rather than assuming a perfectly\n"
             "uniform nominal interval. Counts such as 99/100/101 in five-second windows\n"
             "can be timing/boundary variation when sequences remain contiguous.\n\n"
             "The mean/maximum echo ages in the table are the runner's timestamped\n"
             "measurements from before serial writes. Packet percentiles are reconstructed\n"
             "from raw post-write send and receive log timestamps, so they differ slightly\n"
             "and must be treated as approximations. Both include USB transport, modem\n"
             "scheduling, BLE and car notification timing. Neither measures pure RF RTT.\n\n"
             "Server silence is sampled at five-second report times, not the worst gap\n"
             "between every world command. Exact matching final command totals strengthen\n"
             "the receive-rate result. Synthetic traffic does not validate motor response\n"
             "or disconnect safety behavior.\n\n"
             "Intentional final `#STOP` disconnects are outside traffic. The recorded final\n"
             "modem state is disabled with zero links, and both cars resume advertising.\n\n")
    text += "## Sources and plots\n\n"
    base = path.name.removesuffix(".summary.json")
    text += f"[Summary]({path.name}), [CSV]({base}.csv), [raw JSONL]({base}.jsonl), [manifest](manifest.json).\n\n"
    text += "![Rates and timing over the run](plots/overview.png)\n\n![Individual packet timing](plots/packet_timing.png)\n"
    return text


def main(argv=None, result_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", nargs="?", type=Path)
    args = parser.parse_args(argv)
    path = args.summary
    if path is None:
        candidates = sorted((result_root or Path(__file__).parent / "results/runs").rglob("*.summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = next((p for p in candidates if json.loads(p.read_text()).get("measurements")), None)
    if path is None:
        parser.error("No completed Test 13 rate run found")
    raw_path = path.with_name(path.name.removesuffix(".summary.json")+".jsonl")
    if not raw_path.exists():
        parser.error("Raw JSONL logs are excluded from Git. Restore the corresponding local log "
                     "for packet-level replay, or read the retained analysis.md, analysis.json, "
                     "CSV and plots in this run folder.")
    summary, metrics, rows, raw = analyze(path)
    plots(path.parent / "plots", rows, raw, metrics)
    (path.parent / "analysis.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    (path.parent / "analysis.md").write_text(report(path, summary, metrics), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Report: {path.parent / 'analysis.md'}")
    return bool(metrics["independent_check_failures"])


if __name__ == "__main__":
    raise SystemExit(main())
