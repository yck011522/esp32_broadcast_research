"""Plot Test 8 loss over time and the distribution of full-minute maximum gaps.

Usage: python test/plot_test_8.py [path/to/radio_test_8.csv]
Defaults to the newest Test 8 CSV by modification time. Reads Hz from the CSV.
"""
import argparse
from pathlib import Path

from analyze_radio_test import number, read_run, plot_gap_distribution


def plot_time_series(path, rows, hz):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    cars = sorted({r['car_id'] for r in rows})
    elapsed = max(number(r, 'elapsed_s', 0) for r in rows)
    scale, unit = (3600, 'hours') if elapsed >= 3600 else (60, 'minutes')
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    for index, car in enumerate(cars):
        device = sorted((r for r in rows if r['car_id'] == car), key=lambda r: r['interval'])
        x = [number(r, 'elapsed_s') / scale for r in device]
        color = plt.get_cmap('tab10')(index % 10)
        losses = [number(r, 'world_loss_percent', float('nan')) if r['status'] == 'OK'
                  else float('nan') for r in device]
        gaps = [number(r, 'max_world_gap', float('nan')) if r['status'] == 'OK'
                else float('nan') for r in device]
        axes[0].plot(x, losses, marker='o', markersize=4, color=color, label=f'Slave {car}')
        axes[1].plot(x, gaps, marker='o', markersize=4, color=color, label=f'Slave {car}')
        partial = [r for r in device if r['status'] != 'OK' and number(r, 'world_loss_percent') is not None]
        if partial:
            axes[0].scatter([number(r, 'elapsed_s')/scale for r in partial],
                            [number(r, 'world_loss_percent') for r in partial],
                            marker='x', color=color, label=f'Slave {car} partial')
    host = {r['interval']: r for r in rows}
    host = [host[k] for k in sorted(host)]
    axes[2].plot([number(r, 'elapsed_s')/scale for r in host],
                 [number(r, 'world_tx_rate_hz') for r in host],
                 color='#333333', marker='o', markersize=4, label='PC to master')
    axes[2].axhline(hz, color='#888888', linestyle='--', label=f'Target {hz:g} Hz')
    axes[0].set_ylabel('World loss (%)')
    axes[1].set_ylabel('Max consecutive loss')
    axes[1].yaxis.set_major_locator(MaxNLocator(integer=True))
    axes[2].set_ylabel('Actual TX rate (Hz)')
    axes[2].set_xlabel(f'Elapsed time ({unit})')
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.legend(loc='best')
    mode = rows[0].get('mode', '')
    fig.suptitle(f'Test 8: {mode} at {hz:g} Hz\n{path.stem}')
    fig.text(0.5, 0.015, 'Each point represents one measurement interval. Missing data remain blank; crosses indicate partial loss estimates.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, 0.035, 1, 0.94))
    output = path.with_suffix('.png')
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', nargs='?', type=Path)
    args = parser.parse_args()
    path = args.csv
    if path is None:
        candidates = list((Path(__file__).resolve().parent / 'run_logs').glob('radio_test_8_*.csv'))
        if not candidates:
            parser.error('No Test 8 CSV files found')
        path = max(candidates, key=lambda p: p.stat().st_mtime)
    rows, _ = read_run(path)
    rates = {number(r, 'configured_hz') for r in rows}
    if len(rates) != 1 or None in rates:
        parser.error('CSV must contain one configured_hz value')
    hz = rates.pop()
    print(f'Time series: {plot_time_series(path, rows, hz)}')
    output, counts = plot_gap_distribution(path, rows, expected_hz=hz)
    print(f'Maximum-gap distribution: {output}')
    for car, (minutes, _) in counts.items():
        print(f'Slave {car}: {minutes} valid full-minute maxima')


if __name__ == '__main__':
    main()
