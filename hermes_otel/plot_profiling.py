"""Render the profiling CSVs for a session into PNG charts (offline).

This is a standalone post-processing tool: it reads whichever CSVs a session
produced and writes one chart per signal plus a combined CPU/GPU timeline. It is
run manually after a session and imports nothing from the hermes plugin, so it
has no effect on a profiled run — all rendering happens out of process, once
sampling is already finished.

Usage:
    python plot_profiling.py <session_dir> [--out <dir>]

    <session_dir>  the per-session output directory, i.e.
                   <HERMES_PROFILING_OUTPUT_DIR>/<session_id>
    --out          where to write the PNGs (default: <session_dir>/plots)

Inputs (any subset may be present):
    cpu_hermes_trace.csv, cpu_system_wide.csv, gpu_system_wide.csv,
    tool_execution.csv

Outputs (only for the inputs that exist):
    cpu_hermes_trace.png, cpu_system_wide.png, gpu_system_wide.png,
    combined_timeline.png

tool_execution.csv has no chart of its own; it is used only to shade each tool's
execution window on combined_timeline.png.
"""

import argparse
import csv
import os
import sys

try:
    import matplotlib

    matplotlib.use("Agg")  # headless: render to files, never open a window
    import matplotlib.pyplot as plt
except ImportError:
    sys.stderr.write("plot_profiling.py requires matplotlib (pip install matplotlib)\n")
    raise


def _read_csv(path):
    """Return the rows of a CSV as a list of dicts (empty list if missing/empty)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, "r", newline="") as f:
        return list(csv.DictReader(f))


def _f(value):
    """Parse a CSV cell as float, treating blanks/garbage as 0.0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _elapsed(rows, t0):
    """Return per-row elapsed seconds relative to ``t0`` (epoch seconds), read
    from the shared start_time_unix_nano column the timelines all carry."""
    return [_f(r.get("start_time_unix_nano")) / 1e9 - t0 for r in rows]


def _t0_of(rows):
    """Earliest start_time_unix_nano in a timeline, as epoch seconds (0 if none)."""
    vals = [_f(r.get("start_time_unix_nano")) for r in rows if r.get("start_time_unix_nano")]
    return min(vals) / 1e9 if vals else 0.0


# A gap between consecutive samples is treated as a real break in data
# collection - e.g. the poller was not running between two turns of a resumed
# session - once it is many times larger than the series' own typical spacing.
# Comparing against the series' own median (rather than a fixed number of
# seconds) means this works regardless of HERMES_POLL_INTERVAL. The floor
# guards short or sparse series, where a couple of points could otherwise
# produce a tiny, unrepresentative median.
_GAP_FACTOR = 5.0
_GAP_FLOOR_S = 1.0


def _gap_indices(xs, factor=_GAP_FACTOR, floor_s=_GAP_FLOOR_S):
    """Return the indices i where the span from xs[i] to xs[i+1] is an outlier
    large gap rather than normal sample-to-sample spacing."""
    if len(xs) < 3:
        return []
    diffs = sorted(xs[i + 1] - xs[i] for i in range(len(xs) - 1))
    median = diffs[len(diffs) // 2]
    threshold = max(median * factor, floor_s)
    return [i for i in range(len(xs) - 1) if xs[i + 1] - xs[i] > threshold]


def _break_gaps(xs, *y_series, factor=_GAP_FACTOR, floor_s=_GAP_FLOOR_S):
    """Insert a NaN at each detected gap so matplotlib draws a break there
    instead of a straight line connecting the sample before an idle period to
    the sample after it. ``y_series`` may hold several arrays sharing ``xs``
    (e.g. the GPU chart's busy/power/VRAM columns), so every series gets the
    break at the same point and stays aligned with the shared x-axis.

    Returns (xs, *y_series) with the same shape as the input, one NaN wider per
    gap found.
    """
    gaps = _gap_indices(xs, factor=factor, floor_s=floor_s)
    if not gaps:
        return (xs, *y_series)
    gap_set = set(gaps)
    new_xs = []
    new_series = [[] for _ in y_series]
    for i, x in enumerate(xs):
        new_xs.append(x)
        for new_col, col in zip(new_series, y_series):
            new_col.append(col[i])
        if i in gap_set:
            new_xs.append((xs[i] + xs[i + 1]) / 2.0)
            for new_col in new_series:
                new_col.append(float("nan"))
    return (new_xs, *new_series)


def _save(fig, out_dir, name):
    path = os.path.join(out_dir, name)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _plot_cpu(rows, out_dir, name, title):
    if not rows:
        return
    t0 = _t0_of(rows)
    xs = _elapsed(rows, t0)
    ys = [_f(r.get("cpu_pct")) for r in rows]
    xs, ys = _break_gaps(xs, ys)
    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.plot(xs, ys, linewidth=0.9, color="#1f77b4")
    ax.fill_between(xs, ys, alpha=0.15, color="#1f77b4")
    ax.set_title(title)
    ax.set_xlabel("elapsed seconds")
    ax.set_ylabel("CPU %")
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    _save(fig, out_dir, name)


def _plot_gpu(rows, out_dir):
    if not rows:
        return
    t0 = _t0_of(rows)
    xs = _elapsed(rows, t0)
    series = [
        ("gfx_busy_pct", "GPU busy %", "#d62728"),
        ("power_w", "Power (W)", "#2ca02c"),
        ("vram_mb", "VRAM (MB)", "#9467bd"),
    ]
    # All three columns share one x-axis (sampled on the same poller tick), so
    # break them together to keep the gap aligned across every panel.
    gapped_xs, *gapped_cols = _break_gaps(
        xs, *([_f(r.get(col)) for r in rows] for col, _, _ in series)
    )
    fig, axes = plt.subplots(len(series), 1, figsize=(11, 7), sharex=True)
    for ax, (col, label, color), ys in zip(axes, series, gapped_cols):
        ax.plot(gapped_xs, ys, linewidth=0.9, color=color)
        ax.fill_between(gapped_xs, ys, alpha=0.15, color=color)
        ax.set_ylabel(label)
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.3)
    axes[0].set_title("System-wide GPU")
    axes[-1].set_xlabel("elapsed seconds")
    _save(fig, out_dir, "gpu_system_wide.png")


def _plot_combined(cpu_hermes, cpu_system, gpu, tools, out_dir):
    """Overlay hermes-process-tree CPU %, system-wide CPU % and system-wide
    GPU % on one shared timeline.

    Every overlaid signal is already on the same 0-100 % scale, so they share a
    single y-axis fixed at 0-100 rather than one axis per source. That keeps the
    curves directly comparable and stops matplotlib from autoscaling the top,
    which would otherwise differ between runs and make an idle session look busy.
    Power and VRAM use other units and are deliberately not overlaid here — see
    the per-CSV GPU chart for those.
    """
    present = [r for r in (cpu_hermes, cpu_system, gpu) if r]
    if not present:
        return
    # Shared origin across every timeline so the curves line up in real time.
    t0 = min(_t0_of(r) for r in present)

    fig, ax = plt.subplots(figsize=(12, 4.5))
    # Each signal is its own CSV with its own sample times, so gaps (e.g. idle
    # time between turns where the poller was not running) are detected and
    # broken independently per series rather than on a shared axis.
    if cpu_hermes:
        xs, ys = _break_gaps(_elapsed(cpu_hermes, t0), [_f(r.get("cpu_pct")) for r in cpu_hermes])
        ax.plot(xs, ys, linewidth=0.9, label="CPU hermes-process-tree %", color="#1f77b4")
    if cpu_system:
        xs, ys = _break_gaps(_elapsed(cpu_system, t0), [_f(r.get("cpu_pct")) for r in cpu_system])
        ax.plot(xs, ys, linewidth=0.9, label="CPU system-wide %", color="#ff7f0e")
    if gpu:
        xs, ys = _break_gaps(_elapsed(gpu, t0), [_f(r.get("gfx_busy_pct")) for r in gpu])
        ax.plot(xs, ys, linewidth=0.9, label="GPU system-wide %", color="#d62728")

    # Shade each tool's execution window in a light blue that reads clearly on a
    # white background (skip labels when there are many).
    label_tools = len(tools) <= 20
    for r in tools:
        start = _f(r.get("start_time_unix_nano")) / 1e9 - t0
        end = start + _f(r.get("duration_s"))
        ax.axvspan(start, end, alpha=0.2, color="cornflowerblue")
        if label_tools:
            ax.text(start, 1, r.get("tool_name", ""), rotation=90, fontsize=6,
                    va="bottom", ha="left", alpha=0.6)

    ax.set_title("CPU / GPU utilization vs time (tool windows shaded)")
    ax.set_xlabel("elapsed seconds")
    ax.set_ylabel("utilization %")
    # One shared y-axis, locked to 0-100 % for every overlaid signal, with a
    # touch of headroom above 100 so a line sitting at the top isn't clipped
    # by the plot frame (matplotlib doesn't pad for line width). The extra
    # margin is visual only; ticks stay at the meaningful 0-100 marks.
    ax.set_ylim(0, 103)
    ax.set_yticks(range(0, 101, 20))
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    _save(fig, out_dir, "combined_timeline.png")


def main():
    parser = argparse.ArgumentParser(description="Plot the profiling CSVs for a session.")
    parser.add_argument("session_dir", help="per-session output directory containing the CSVs")
    parser.add_argument("--out", help="output dir for the PNGs (default: <session_dir>/plots)")
    args = parser.parse_args()

    session_dir = args.session_dir
    if not os.path.isdir(session_dir):
        sys.stderr.write(f"not a directory: {session_dir}\n")
        return 1

    out_dir = args.out or os.path.join(session_dir, "plots")
    os.makedirs(out_dir, exist_ok=True)

    def load(name):
        return _read_csv(os.path.join(session_dir, name))

    cpu_hermes = load("cpu_hermes_trace.csv")
    cpu_system = load("cpu_system_wide.csv")
    gpu = load("gpu_system_wide.csv")
    # tool_execution.csv is not charted on its own; it is read only to shade the
    # tool windows on the combined timeline.
    tools = load("tool_execution.csv")

    if not any((cpu_hermes, cpu_system, gpu)):
        sys.stderr.write(f"no profiling CSVs found in {session_dir}\n")
        return 1

    print(f"rendering plots into {out_dir}")
    _plot_cpu(cpu_hermes, out_dir, "cpu_hermes_trace.png", "Hermes process-tree CPU %")
    _plot_cpu(cpu_system, out_dir, "cpu_system_wide.png", "System-wide CPU %")
    _plot_gpu(gpu, out_dir)
    _plot_combined(cpu_hermes, cpu_system, gpu, tools, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
