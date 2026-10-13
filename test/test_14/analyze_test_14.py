"""Plot Test 14 CSV and retained summary; raw JSONL is not required."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse
import csv
import json
import hashlib
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def analyze(path):
    summary = json.loads(path.read_text(encoding="utf-8"))
    reassessment = None
    reassessment_path = path.parent/"startup_reassessment.json"
    if reassessment_path.exists():
        candidate = json.loads(reassessment_path.read_text(encoding="utf-8"))
        if candidate.get("source_summary_sha256") == hashlib.sha256(path.read_bytes()).hexdigest():
            reassessment = candidate
    display_status = summary["status"]
    if reassessment and reassessment["passed"]:
        display_status += "; communication reassessment passed"
    csv_path = path.with_name(path.name.replace(".summary.json", ".csv"))
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    folder = path.parent/"plots"
    folder.mkdir(exist_ok=True)
    fig, axes = plt.subplots(4, 4, figsize=(20, 12), squeeze=False, sharex=True, layout="constrained")
    colors = ("#2463a5", "#d47721", "#448844", "#994488")
    for column, car in enumerate((1, 2, 3, 4)):
        selected = [r for r in rows if int(r["car"]) == car]
        minutes = [float(r["traffic_s"])/60 for r in selected]
        for index, field, label, target in ((0, "pc_submit_hz", "PC submissions (Hz)", 50),
                                            (1, "rx_hz", "Telemetry arrivals (Hz)", 20)):
            values = [float(r[field]) for r in selected]
            axes[index, column].plot(minutes, values, color=colors[column], linewidth=.9)
            axes[index, column].axhline(target, color="#888", linestyle="--", linewidth=.8)
            axes[index, column].set_ylabel(label)
            if summary["scenario"] != "bench":
                axes[index, column].set_ylim(bottom=0, top=max([target*1.1, *values])*1.05)
        for field, style, label in (("echo_age_avg_ms", "-", "Mean"), ("echo_age_max_ms", ":", "Maximum")):
            values = [float(r[field]) if r[field] else np.nan for r in selected]
            axes[2, column].plot(minutes, values, color=colors[column], linestyle=style, linewidth=.9, label=label)
        axes[2, column].set_ylabel("Echo age (ms)")
        axes[2, column].legend(fontsize=8)
        axes[3, column].plot(minutes, [float(r["silence_s"]) for r in selected], color=colors[column], linewidth=.9)
        axes[3, column].set_ylabel("Sampled telemetry silence (s)")
        axes[3, column].set_xlabel("Traffic elapsed (minutes)")
        axes[0, column].set_title(f"Car {car}")
        for ax in axes[:, column]:
            for begin, end in summary.get("planned_outages", {}).get(str(car), []):
                ax.axvspan(begin/60, end/60, color="#d47721", alpha=.12)
            for begin, end in summary.get("command_pauses", []):
                ax.axvspan(begin/60, end/60, color="#2463a5", alpha=.08)
            ax.grid(alpha=.2)
    fig.suptitle(f"Test 14: {summary['label']} / {summary['scenario']} — {display_status}\nOrange: planned disruption window. Blue: PC world generation paused.")
    fig.savefig(folder/"overview.png", dpi=150)
    plt.close(fig)
    text = f"# Test 14: {summary['label']}\n\nResult: **{summary['status']}**. Traffic duration: {summary.get('active_s', 0):.3f} seconds.\n\n"
    text += "| Car | Notifications | Inferred telemetry gaps | Ambiguous sequence changes | Largest arrival gap (s) | Largest matched echo age (ms) |\n|---|---:|---:|---:|---:|---:|\n"
    for car, item in summary.get("measurements", {}).items():
        text += f"| {car} | {item['notifications']:,} | {item['inferred_seq_lost']} | {item['sequence_discontinuities']} | {item['max_arrival_gap_s']:.3f} | {item['max_matched_echo_age_ms']:.1f} |\n"
    failures = summary.get("errors", [])+summary.get("validation_failures", [])
    text += "\nValidation failures: "+("; ".join(failures) if failures else "none")+".\n\n"
    if reassessment:
        text += (f"Independent startup reassessment: **{'passed' if reassessment['passed'] else 'failed'}**. "
                 "The original result remains unchanged. The old runner used a disconnected pre-traffic car serial snapshot as a connection-count baseline, incorrectly calling completion of startup an unexpected reconnect. Retained receive totals, disconnect counts and all traffic-time connection observations are checked separately. "
                 "See [reassessment](startup_reassessment.json). The current runner waits for connected/subscribed car serial baselines before sending worlds.\n\n")
    text += ("PC submissions count successful serial writes, not car receipts. Attached car serial counters independently check receipts during bench tests.\n\n"
             "Sequence gaps are application observations, including intentional snapshot supersession. Without session metadata, some reset/replacement/wrap cases remain ambiguous.\n\n"
             "Echo age includes USB, modem scheduling, BLE and notification timing. It is not pure radio RTT. During intentional idle periods the retained world naturally becomes old; this does not imply a connection failure.\n\n")
    if summary["scenario"] == "expiry":
        text += ("A returning car reports zero before its first WorldState. In older logger captures, that zero may match the real sequence zero sent near startup and produce misleading 30–40 second ages. Such initial zero echoes are unconfirmed, not measured transaction times. The current logger suppresses them after a possible restart/outage until a nonzero echo arrives. The car serial audit independently confirms that no expired world was applied.\n\n")
    text += f"[CSV]({csv_path.name}), [summary]({path.name}), [manifest](manifest.json).\n\n![Rates and timing](plots/overview.png)\n"
    (path.parent/"analysis.md").write_text(text, encoding="utf-8")
    print(f"Report: {path.parent/'analysis.md'}")
    return summary["status"] != "passed" and not (reassessment and reassessment["passed"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, nargs="?")
    args = parser.parse_args(argv)
    path = args.summary
    if path is None:
        paths = sorted((Path(__file__).parent/"results/runs").rglob("*.summary.json"), key=lambda p: p.stat().st_mtime)
        if not paths:
            parser.error("No completed Test 14 summary")
        path = paths[-1]
    return analyze(path)


if __name__ == "__main__":
    raise SystemExit(main())
