"""Summarize a Test 4 CSV. Defaults to the newest radio_test_*.csv in run_logs.

Examples:
    python test/analyze_radio_test.py
    python test/analyze_radio_test.py path/to/radio_test.csv --save
    python test/analyze_radio_test.py --plot

Only OK rows enter aggregate loss rates. STALE/COUNTER_RESET rows retain a
trustworthy prefix, but their counters are incomplete for the full interval.
FAIL_TO_RESET rows contain no loss counts. Missing device rows are reported
relative to IDs seen somewhere in this run, not treated as zero loss.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


REQUIRED = {
    "interval", "car_id", "status", "world_packets_sent", "world_packets_lost",
    "max_world_gap", "telemetry_received", "telemetry_lost", "interval_elapsed_s",
    "elapsed_s", "schedule_rebases", "max_tx_lateness_ms", "max_tx_interval_ms",
}


def number(row, field, default=None):
    value = row.get(field, "")
    return default if value is None or value == "" else float(value)


def read_run(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError(f"Not a Test 4 CSV; missing: {sorted(REQUIRED - set(reader.fieldnames or []))}")
        rows = list(reader)
    if not rows:
        raise ValueError("CSV contains no device rows")
    for row in rows:
        row["interval"] = int(row["interval"])
        row["car_id"] = int(row["car_id"])
    summary_path = path.with_suffix(".summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
    return rows, summary


def percent(numerator, denominator):
    return 100 * numerator / denominator if denominator else None


def fmt(value, digits=3):
    return "n/a" if value is None else f"{value:,.{digits}f}"


def report(path, rows, summary, top=5, expected_hz=100):
    intervals = defaultdict(dict)
    for row in rows:
        key = row["interval"]
        car = row["car_id"]
        if car in intervals[key]:
            raise ValueError(f"Duplicate CSV row: interval {key}, slave {car}")
        intervals[key][car] = row

    ids = sorted({row["car_id"] for row in rows})
    complete = summary is not None and summary.get("status") == "COMPLETE"
    final_interval = max(intervals)
    final_elapsed = max(number(row, "elapsed_s", 0) for row in rows)
    final_total = max(number(row, "total_world_packets_sent", 0) for row in rows)
    lines = [f"Radio test: {path.name}"]
    if summary:
        lines.append(f"Run status: {summary.get('status', 'UNKNOWN')}" +
                     (f" ({summary['error']})" if summary.get("error") else ""))
    else:
        lines.append("Run status: unknown (summary JSON unavailable)")
    lines += [f"Elapsed: {final_elapsed / 3600:.3f} h; intervals logged: {len(intervals)}"
              f" (last interval #{final_interval})",
              f"World packets sent: {int(final_total):,}",
              "Aggregates below use OK rows only; unobserved trailing losses are not measurable."]

    # Host diagnostics are duplicated on one row per slave; count each interval once.
    host = [next(iter(group.values())) for _, group in sorted(intervals.items())]
    sent = sum(number(row, "world_packets_sent", 0) for row in host)
    measured = sum(number(row, "interval_elapsed_s", 0) for row in host)
    rebases = sum(number(row, "schedule_rebases", 0) for row in host)
    rates = [number(row, "world_packets_sent", 0) /
             number(row, "interval_elapsed_s") for row in host
             if number(row, "interval_elapsed_s", 0) > 0]
    lines += ["", "Host transmission:",
              f"  Rate during logged intervals: {fmt(sent / measured if measured else None)} packets/s "
              f"(median interval {fmt(median(rates))} packets/s)" if rates else "  Rate: n/a",
              f"  Expected rate for comparison: {fmt(expected_hz)} packets/s; "
              f"sent by host: {fmt(percent(sent, measured * expected_hz))}%"
              if measured else "  Expected rate comparison: n/a",
              f"  Schedule rebases: {int(rebases):,}",
              f"  Worst scheduling lateness: {fmt(max(number(r, 'max_tx_lateness_ms', 0) for r in host))} ms",
              f"  Longest TX interval: {fmt(max(number(r, 'max_tx_interval_ms', 0) for r in host))} ms"]

    for car in ids:
        device = [group[car] for _, group in sorted(intervals.items()) if car in group]
        statuses = Counter(row["status"] for row in device)
        absent = len(intervals) - len(device)
        good = [row for row in device if row["status"] == "OK"]
        world_sent = sum(number(r, "world_packets_sent", 0) for r in good)
        world_lost = sum(number(r, "world_packets_lost", 0) for r in good)
        tel_recv = sum(number(r, "telemetry_received", 0) for r in good)
        tel_lost = sum(number(r, "telemetry_lost", 0) for r in good)
        gaps = [number(r, "max_world_gap", 0) for r in good]
        worst = sorted(good, key=lambda r: percent(number(r, "world_packets_lost", 0),
                                                     number(r, "world_packets_sent", 0)) or 0,
                       reverse=True)[:top]
        lines += ["", f"Slave {car}:",
                  "  Intervals: " + ", ".join(f"{key} {statuses[key]}" for key in
                                              ("OK", "STALE", "COUNTER_RESET", "FAIL_TO_RESET")
                                              if statuses[key]) + f", {absent} absent",
                  f"  Valid world loss: {int(world_lost):,} / {int(world_sent):,} = "
                  f"{fmt(percent(world_lost, world_sent))}%",
                  f"  Valid telemetry loss: {int(tel_lost):,} / {int(tel_recv + tel_lost):,} = "
                  f"{fmt(percent(tel_lost, tel_recv + tel_lost))}%",
                  f"  Largest valid consecutive world gap: {int(max(gaps)) if gaps else 'n/a'}"]
        midpoint = final_elapsed / 2
        for label, subset in (("First half", [r for r in good if number(r, "elapsed_s", 0) <= midpoint]),
                              ("Second half", [r for r in good if number(r, "elapsed_s", 0) > midpoint])):
            subset_sent = sum(number(r, "world_packets_sent", 0) for r in subset)
            subset_lost = sum(number(r, "world_packets_lost", 0) for r in subset)
            lines.append(f"  {label} valid world loss: {fmt(percent(subset_lost, subset_sent))}% "
                         f"across {len(subset)} OK intervals")
        if worst:
            lines.append("  Worst valid intervals (world loss):")
            for row in worst:
                lost = number(row, "world_packets_lost", 0)
                count = number(row, "world_packets_sent", 0)
                lines.append(f"    #{row['interval']}: {fmt(percent(lost, count))}% "
                             f"({int(lost)}/{int(count)}), max gap {row['max_world_gap']}")
        problems = [row for row in device if row["status"] != "OK"]
        if problems:
            recent = sum(number(row, "telemetry_age_s", float("inf")) <= 2 for row in problems)
            lines.append(f"  Non-OK intervals with telemetry observed within 2 s of the end: "
                         f"{recent}/{len(problems)}")
            observed = [row for row in device if number(row, "observed_telemetry") is not None]
            if observed:
                longest = max(number(row, "max_observed_silence_s", 0) for row in observed)
                lines.append(f"  Longest logged within-interval telemetry silence: {fmt(longest)} s")
            lines.append("  Non-OK intervals: " + ", ".join(
                f"#{row['interval']} {row['status']}" for row in problems[:15]) +
                (f" ... ({len(problems)} total)" if len(problems) > 15 else ""))
        if absent:
            missing = [i for i in sorted(intervals) if car not in intervals[i]]
            lines.append("  Absent intervals: " + ", ".join(f"#{i}" for i in missing[:15]) +
                         (f" ... ({absent} total)" if absent > 15 else ""))

    if not complete:
        lines += ["", "This run did not have a COMPLETE summary. Its final interval may be partial."]
    else:
        last = host[-1]
        if number(last, "interval_elapsed_s", 0) < 50:
            lines += ["", "The final interval is shorter than one minute and is included where status is OK."]
    return "\n".join(lines) + "\n"


def plot_run(path, rows, expected_hz=100):
    """Save four aligned panels, leaving invalid device intervals as gaps."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    intervals = defaultdict(dict)
    for row in rows:
        intervals[row["interval"]][row["car_id"]] = row
    ordered = sorted(intervals)
    elapsed = [max(number(row, "elapsed_s", 0) for row in intervals[i].values()) / 3600
               for i in ordered]
    host = [next(iter(intervals[i].values())) for i in ordered]
    rate = [number(row, "world_packets_sent", 0) /
            number(row, "interval_elapsed_s") if number(row, "interval_elapsed_s", 0) > 0
            else float("nan") for row in host]
    ids = sorted({row["car_id"] for row in rows})
    fig, axes = plt.subplots(5, 1, figsize=(14, 12), sharex=True,
                             gridspec_kw={"height_ratios": [2.2, 1.6, 1.3, 1.2, 1]})
    palette = plt.get_cmap("tab10")
    for index, car in enumerate(ids):
        color = palette(index % 10)
        device = [intervals[i].get(car) for i in ordered]
        valid = [row if row is not None and row["status"] == "OK" else None
                 for row in device]
        world = [percent(number(row, "world_packets_lost", 0),
                         number(row, "world_packets_sent", 0)) if row else float("nan")
                 for row in valid]
        telemetry = [percent(number(row, "telemetry_lost", 0),
                             number(row, "telemetry_received", 0) +
                             number(row, "telemetry_lost", 0)) if row else float("nan")
                     for row in valid]
        axes[0].plot(elapsed, world, color=color, linewidth=1.15, label=f"Slave {car}")
        axes[1].plot(elapsed, telemetry, color=color, linewidth=1.15,
                     label=f"Slave {car}")
        age = [number(row, "telemetry_age_s") if row is not None else None for row in device]
        axes[3].plot(elapsed, [value if value is not None else float("nan") for value in age],
                     color=color, linewidth=1, label=f"Slave {car}")
        observed_silence = [number(row, "max_observed_silence_s") if row is not None else None
                            for row in device]
        if any(value is not None for value in observed_silence):
            axes[3].plot(elapsed,
                         [value if value is not None else float("nan")
                          for value in observed_silence],
                         color=color, linewidth=0.9, linestyle="--", alpha=0.65)
        for status, marker in (("STALE", "^"), ("COUNTER_RESET", "D"),
                               ("FAIL_TO_RESET", "x")):
            xs = [elapsed[j] for j, row in enumerate(device)
                  if row is not None and row["status"] == status]
            if xs:
                axes[4].scatter(xs, [index] * len(xs), c=[color], marker=marker,
                                s=22, linewidths=1.1)
        absent = [elapsed[j] for j, row in enumerate(device) if row is None]
        if absent:
            axes[4].scatter(absent, [index] * len(absent), c=[color], marker="|",
                            s=45)
    axes[2].plot(elapsed, rate, color="#303846", linewidth=1)
    axes[2].axhline(expected_hz, color="#bd3d3a", linestyle="--", linewidth=1,
                    label=f"Expected {expected_hz:g} Hz")
    axes[3].axhline(2, color="#bd3d3a", linestyle=":", linewidth=1,
                    label="2 s stale threshold")
    axes[0].set_ylabel("World loss (%)")
    axes[1].set_ylabel("Telemetry loss (%)")
    axes[2].set_ylabel("World TX rate (packets/s)")
    axes[3].set_ylabel("Age / silence (s)")
    axes[4].set_ylabel("Device status")
    axes[4].set_yticks(range(len(ids)), [f"Slave {car}" for car in ids])
    axes[4].set_ylim(-0.6, len(ids) - 0.4)
    axes[4].set_xlabel("Elapsed time (hours)")
    for ax in axes:
        ax.grid(alpha=0.22)
    axes[0].legend(loc="upper left", ncol=min(len(ids), 4), fontsize=8)
    axes[2].legend(loc="upper left", fontsize=8)
    axes[3].legend(loc="upper left", ncol=min(len(ids) + 1, 4), fontsize=8)
    status_legend = [Line2D([], [], color="#555555", marker=marker, linestyle="None",
                            label=status, markersize=6) for status, marker in
                     (("STALE", "^"), ("COUNTER_RESET", "D"),
                      ("FAIL_TO_RESET", "x"), ("ABSENT", "|"))]
    axes[4].legend(handles=status_legend, loc="upper left", ncol=4, fontsize=8)
    fig.suptitle(f"Radio test over time: {path.stem}", fontsize=14)
    fig.text(0.5, 0.015,
             "Loss plots use OK rows only. Age is time since latest telemetry at interval end; "
             "dashed age lines (new logs) show maximum observed silence. "
             "Status: triangle=stale, diamond=counter reset, x=failed reset, bar=absent.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.035, 1, 0.96))
    output = path.with_suffix(".analysis.png")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output


def plot_gap_distribution(path, rows, expected_hz=100):
    """Plot occurrences of per-minute maxima, with nominal gap duration."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    by_car = defaultdict(list)
    for row in rows:
        if (row["status"] == "OK" and
                number(row, "interval_elapsed_s", 0) >= 55 and
                number(row, "max_world_gap") is not None):
            by_car[row["car_id"]].append(int(number(row, "max_world_gap")))
    if not by_car:
        raise ValueError("No complete, reset-confirmed minutes have max_world_gap data")

    maximum = max(max(values) for values in by_car.values())
    lengths = list(range(maximum + 1))
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                             gridspec_kw={"height_ratios": [1.3, 1]})
    palette = plt.get_cmap("tab10")
    cars = sorted(by_car)
    width = min(0.8 / len(cars), 0.35)
    for index, car in enumerate(cars):
        values = by_car[car]
        counts = Counter(values)
        color = palette(index % 10)
        offset = (index - (len(cars) - 1) / 2) * width
        axes[0].bar([length + offset for length in lengths],
                    [counts.get(length, 0) for length in lengths],
                    width=width, color=color, label=f"Slave {car} ({len(values)} minutes)")
        tail = [sum(value >= length for value in values) for length in lengths]
        axes[1].step(lengths, tail, where="mid", color=color, linewidth=1.8,
                     label=f"Slave {car}: {sum(v >= 10 for v in values)} minutes with gap ≥10")
    integer_ticks = [0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
    for ax in axes:
        ax.set_yscale("symlog", linthresh=1)
        ax.set_yticks(integer_ticks)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{int(value):,}"))
    axes[0].set_ylabel("Number of occurrences")
    axes[1].set_ylabel("Cumulative occurrences")
    axes[1].set_xlabel("Maximum consecutive world packets lost in a minute")
    axes[1].axvline(10, color="#a23a3a", linestyle="--", linewidth=1,
                    label="10-packet threshold")
    packet_ticks = list(range(0, maximum + 1, 5 if maximum > 20 else 2))
    axes[1].set_xticks(packet_ticks)
    axes[1].set_xlim(-0.7, maximum + 0.7)
    duration_axis = axes[0].secondary_xaxis(
        "top", functions=(lambda packets: packets * 1000 / expected_hz,
                          lambda milliseconds: milliseconds * expected_hz / 1000)
    )
    duration_axis.set_xticks([packets * 1000 / expected_hz for packets in packet_ticks])
    duration_axis.set_xlabel(f"Nominal gap duration at {expected_hz:g} Hz (ms)")
    for ax in axes:
        ax.grid(axis="y", alpha=0.25)
        ax.legend(loc="upper right", fontsize=9)
    fig.suptitle(f"Per-minute maximum world-packet loss: {path.stem}", fontsize=14, y=0.99)
    fig.text(0.5, 0.02,
             "One occurrence = one valid full minute, not one loss event. "
             "Duration axis assumes the requested rate; actual send timing may differ. "
             "Invalid and partial intervals are excluded.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.045, 1, 0.91))
    output = path.with_suffix(".max_gap_distribution.png")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output, {car: (len(values), sum(value >= 10 for value in values))
                    for car, values in by_car.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path, help="Test 4 CSV; defaults to latest by filename")
    parser.add_argument("--save", action="store_true", help="Write a .analysis.txt beside the CSV")
    parser.add_argument("--plot", action="store_true",
                        help="Write time-series and maximum-gap distribution PNGs beside the CSV")
    parser.add_argument("--top", type=int, default=5, help="Worst valid intervals to list per slave")
    parser.add_argument("--expected-hz", type=float, default=100,
                        help="Planned world send rate for comparison (default: 100)")
    args = parser.parse_args()
    if args.top < 0:
        parser.error("--top must be nonnegative")
    if args.expected_hz <= 0:
        parser.error("--expected-hz must be positive")
    path = args.csv
    if path is None:
        candidates = list((Path(__file__).resolve().parent / "run_logs").glob("radio_test_*.csv"))
        if not candidates:
            parser.error("no radio_test_*.csv files found in test/run_logs")
        path = max(candidates, key=lambda item: item.name)
    rows, summary = read_run(path)
    result = report(path, rows, summary, top=args.top, expected_hz=args.expected_hz)
    print(result, end="")
    if args.save:
        output = path.with_suffix(".analysis.txt")
        output.write_text(result, encoding="utf-8")
        print(f"Saved: {output.resolve()}")
    if args.plot:
        output = plot_run(path, rows, expected_hz=args.expected_hz)
        print(f"Saved graph: {output.resolve()}")
        distribution, counts = plot_gap_distribution(path, rows, expected_hz=args.expected_hz)
        print(f"Saved maximum-gap distribution: {distribution.resolve()}")
        for car, (minutes, at_least_ten) in counts.items():
            print(f"Slave {car}: {at_least_ten}/{minutes} valid full minutes had max gap >=10")


if __name__ == "__main__":
    main()
