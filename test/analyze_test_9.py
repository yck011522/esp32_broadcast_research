"""Analyze Test 9 CSV and save PNG plots, JSON metrics, and a Markdown report.

Usage: python test/analyze_test_9.py [CSV]; defaults to latest ble_test_9_*.csv.
Requires matplotlib. No measurements are interpolated across missing reports.
"""

import argparse
import csv
import json
from pathlib import Path
import statistics


def distribution(values):
    values = sorted(values)
    def percentile(fraction):
        position = (len(values) - 1) * fraction
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)
    return {"min": min(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "p95": percentile(.95),
            "p99": percentile(.99), "max": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path)
    args = parser.parse_args()
    paths = list((Path(__file__).parent / "run_logs").glob("ble_test_9_*.csv"))
    path = args.csv or (max(paths, key=lambda p: p.stat().st_mtime) if paths else None)
    if path is None:
        parser.error("no Test 9 CSV found")
    with path.open(newline="", encoding="utf-8") as source:
        all_rows = list(csv.DictReader(source))
    rows = [row for row in all_rows if row["record_type"] == "ble_stats"]
    if not rows:
        parser.error("no combined BLE_STATS rows found")
    rows.sort(key=lambda row: float(row["elapsed_s"]))
    def values(key):
        return [float(row[key]) for row in rows if row.get(key)]
    times = values("elapsed_s")
    windows = values("window_s")
    rtt_rows = [row for row in rows if row.get("rtt_avg_ms") and int(row["rtt_samples"]) > 0]
    samples = sum(int(row["rtt_samples"]) for row in rtt_rows)
    received = sum(int(row["received"]) for row in rows)
    lost = sum(int(row["seq_lost"]) for row in rows)
    report_intervals = [b - a for a, b in zip(times, times[1:])]
    missing = [(a["elapsed_s"], b["elapsed_s"]) for a, b in zip(rows, rows[1:])
               if a["connection"] != b["connection"] or
               float(b["elapsed_s"]) - float(a["elapsed_s"]) > float(b["window_s"]) * 1.5]
    summary_path = path.with_suffix(".summary.json")
    run = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    metrics = {
        "source": str(path), "logger_summary": run, "reports": len(rows),
        "reported_window_seconds": sum(windows), "received": received,
        "seq_lost": lost, "sequence_loss_percent": 100 * lost / (received + lost) if received + lost else None,
        "weighted_receive_rate_hz": received / sum(windows),
        "report_interval_s": distribution(report_intervals) if report_intervals else None,
        "missing_report_or_connection_boundaries": missing,
        "rtt_samples": samples,
        "weighted_rtt_estimate_ms": sum(float(row["rtt_avg_ms"]) * int(row["rtt_samples"])
                                         for row in rtt_rows) / samples if samples else None,
        "windows_gap_at_least_200ms": sum(float(row["max_gap_s"]) >= .2 for row in rows),
        "windows_gap_above_200ms": sum(float(row["max_gap_s"]) > .2 for row in rows),
        "worst_gap_windows": [{key: row[key] for key in
                                ["timestamp_utc", "elapsed_s", "max_gap_s", "rtt_max_ms", "received"]}
                              for row in sorted(rows, key=lambda row: float(row["max_gap_s"]), reverse=True)[:10]],
    }
    for key in ["rate_hz", "max_gap_s", "silence_s", "rtt_avg_ms", "rtt_max_ms"]:
        data = values(key)
        metrics[key] = distribution(data) if data else None
    path.with_suffix(".analysis.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    x = np.array(times) / 60
    def series(key, scale=1):
        y = np.array([float(row[key]) * scale if row.get(key) else np.nan for row in rows])
        # Explicitly break connecting lines at missing reports/session boundaries.
        for i in range(1, len(rows)):
            if rows[i]["connection"] != rows[i-1]["connection"] or times[i]-times[i-1] > windows[i]*1.5:
                y[i] = np.nan
        return y
    fig, axes = plt.subplots(4, 1, figsize=(13, 11), sharex=True)
    axes[0].plot(x, series("rate_hz"), color="#2463a5", linewidth=.7)
    axes[0].axhline(20, color="black", linestyle="--", linewidth=.8, label="20 Hz target")
    axes[0].set_ylabel("Receive rate (Hz)")
    axes[0].legend(loc="upper right")
    axes[1].plot(x, series("seq_lost"), color="#b44839", linewidth=.9)
    axes[1].set_ylabel("Sequence loss\nper window")
    axes[1].set_ylim(-.1, max(1, max(values("seq_lost")) * 1.1))
    axes[2].plot(x, series("max_gap_s", 1000), color="#b44839", linewidth=.7, label="Completed receive gap: window maximum")
    axes[2].plot(x, series("silence_s", 1000), color="#278777", linewidth=.6, alpha=.7, label="Silence at report time")
    axes[2].axhline(200, color="black", linestyle="--", linewidth=.8, label="200 ms reference")
    axes[2].set_ylabel("Receive timing (ms)")
    axes[2].legend(loc="upper right", fontsize=8)
    axes[3].plot(x, series("rtt_max_ms"), color="#a89ac2", linewidth=.7, label="RTT estimate: window maximum")
    axes[3].plot(x, series("rtt_avg_ms"), color="#624291", linewidth=1, label="RTT estimate: window average")
    axes[3].set_ylabel("Estimated RTT (ms)")
    axes[3].set_xlabel("Logger elapsed time (minutes)")
    axes[3].legend(loc="upper right", fontsize=8)
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle(f"Test 9 BLE: {len(rows):,} reports, {received:,} notifications, {lost} detected sequence losses\n{path.stem}")
    fig.text(.5, .015, "Each point is a five-second report. RTT assumes steady 50 Hz writes, has 20 ms resolution, and includes server echo timing.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .04, 1, .94))
    plot_path = path.with_suffix(".timeseries.png")
    fig.savefig(plot_path, dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for ax, key, scale, title, label in [
        (axes[0], "max_gap_s", 1000, "Five-second maximum receive gaps", "Maximum gap (ms)"),
        (axes[1], "rtt_avg_ms", 1, "Five-second average RTT estimates", "Average RTT estimate (ms)"),
        (axes[2], "rtt_max_ms", 1, "Five-second maximum RTT estimates", "Maximum RTT estimate (ms)"),
    ]:
        ax.hist([v * scale for v in values(key)], bins=25, color="#2463a5", edgecolor="white")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(label)
        ax.set_ylabel("Reporting windows")
        ax.grid(axis="y", alpha=.2)
    fig.suptitle("Window summary distributions — not individual packet latency distributions")
    fig.tight_layout()
    hist_path = path.with_suffix(".distributions.png")
    fig.savefig(hist_path, dpi=160)
    plt.close(fig)

    report = f"""# Test 9 BLE analysis

Source: `{path.name}`. Logger status: {run.get('status', 'unknown')}.
Run start: {run.get('started_utc', 'unknown')} (UTC).

- Reports: **{len(rows):,}**; reported windows total **{sum(windows)/3600:.3f} hours**.
- Received: **{received:,}**; inferred notification sequence loss: **{lost}**.
- Weighted receive rate: **{metrics['weighted_receive_rate_hz']:.3f} Hz**.
- Serial connections: **{run.get('connections', 'unknown')}**; serial errors: **{run.get('serial_errors', 'unknown')}**.
- Missing report/session boundaries detected: **{len(missing)}**.
- Receive gap: largest completed gap **{metrics['max_gap_s']['max']*1000:.0f} ms**.
- Windows with a gap >=200 ms: **{metrics['windows_gap_at_least_200ms']} / {len(rows)}**;
  strictly >200 ms: **{metrics['windows_gap_above_200ms']} / {len(rows)}**.
- Silence at report time: maximum **{metrics['silence_s']['max']*1000:.0f} ms**.
"""
    if samples:
        report += f"""- Sample-weighted RTT estimate: **{metrics['weighted_rtt_estimate_ms']:.2f} ms**.
- Largest reported RTT estimate: **{metrics['rtt_max_ms']['max']:.0f} ms**.
- 95th percentile of window-average RTT: **{metrics['rtt_avg_ms']['p95']:.1f} ms**.
- 95th percentile of window-maximum RTT: **{metrics['rtt_max_ms']['p95']:.0f} ms**.
"""
    report += """
The notification stream sustains its target average rate, but average throughput
does not guarantee bounded delivery timing. Sequence loss counts missing
notification attempts, not ungenerated send slots, radio retries, or delay.
Completed gaps can cross reporting windows; threshold counts are windows, not
individual gap events. Silence is sampled only when a report is generated.

RTT is an estimate of echoed command age based on 20 ms sequence intervals.
It can underestimate age by almost 20 ms under steady sending, includes server
echo sampling/delivery timing, and is distorted by sender stalls or stale echoes.
RTT percentiles above describe window summaries, not individual packets.

This client log does not contain the server's world-command loss statistics;
it cannot prove zero world-command loss or assess actual motor response.
The first firmware report can cover time before the logger opened its port;
reported-window duration and host recording coverage are different quantities.

Plots: [Time series](""" + plot_path.name + ") and [distributions](" + hist_path.name + ").\n"
    report_path = path.with_suffix(".analysis.md")
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Saved {plot_path}\nSaved {hist_path}\nSaved {report_path}")


if __name__ == "__main__":
    main()
