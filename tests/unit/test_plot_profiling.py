"""Tests for the standalone matplotlib chart renderer (plot_profiling.py).

plot_profiling.py sets matplotlib.use("Agg") at import time, so rendering real
PNGs to a tmp_path is safe in a headless CI environment. Assertions check that
the expected file exists with nonzero size rather than pixel content.
"""

import csv
import os
import sys

import pytest

from hermes_otel import plot_profiling as pp


# --------------------------------------------------------------------------- #
# _f / _elapsed / _t0_of
# --------------------------------------------------------------------------- #
class TestValueHelpers:
    def test_f_parses_float(self):
        assert pp._f("12.5") == 12.5

    @pytest.mark.parametrize("value", [None, "", "not-a-number"])
    def test_f_blank_or_garbage_is_zero(self, value):
        assert pp._f(value) == 0.0

    def test_t0_of_returns_earliest(self):
        rows = [{"start_time_unix_nano": "3000000000"}, {"start_time_unix_nano": "1000000000"}]
        assert pp._t0_of(rows) == 1.0

    def test_t0_of_empty_rows_is_zero(self):
        assert pp._t0_of([]) == 0.0

    def test_t0_of_ignores_blank_column(self):
        rows = [{"start_time_unix_nano": ""}, {"start_time_unix_nano": "2000000000"}]
        assert pp._t0_of(rows) == 2.0

    def test_elapsed_relative_to_t0(self):
        rows = [{"start_time_unix_nano": "1000000000"}, {"start_time_unix_nano": "3000000000"}]
        assert pp._elapsed(rows, t0=1.0) == [0.0, 2.0]


# --------------------------------------------------------------------------- #
# _gap_indices / _break_gaps
# --------------------------------------------------------------------------- #
class TestGapDetection:
    def test_short_series_has_no_gaps(self):
        assert pp._gap_indices([0.0, 1.0]) == []

    def test_evenly_spaced_series_has_no_gaps(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        assert pp._gap_indices(xs) == []

    def test_detects_one_large_gap(self):
        # median spacing is 1.0; a 20s jump is a clear outlier.
        xs = [0.0, 1.0, 2.0, 22.0, 23.0, 24.0]
        assert pp._gap_indices(xs) == [2]

    def test_floor_guards_tiny_median(self):
        # Sub-second spacing with one ~0.9s jump should NOT trip the floor.
        xs = [0.0, 0.05, 0.1, 0.15, 0.2]
        assert pp._gap_indices(xs) == []

    def test_break_gaps_inserts_nan_at_gap(self):
        xs = [0.0, 1.0, 2.0, 22.0, 23.0]
        ys = [10.0, 11.0, 12.0, 13.0, 14.0]
        new_xs, new_ys = pp._break_gaps(xs, ys)
        assert len(new_xs) == len(xs) + 1
        assert len(new_ys) == len(ys) + 1
        nan_idx = new_ys.index([v for v in new_ys if v != v][0])  # v != v => NaN
        assert new_xs[nan_idx] == pytest.approx((2.0 + 22.0) / 2.0)

    def test_break_gaps_noop_when_no_gap(self):
        xs = [0.0, 1.0, 2.0]
        ys = [1.0, 2.0, 3.0]
        assert pp._break_gaps(xs, ys) == (xs, ys)

    def test_break_gaps_keeps_multiple_series_aligned(self):
        xs = [0.0, 1.0, 2.0, 22.0, 23.0]
        ys1 = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys2 = [10.0, 20.0, 30.0, 40.0, 50.0]
        new_xs, new_ys1, new_ys2 = pp._break_gaps(xs, ys1, ys2)
        assert len(new_xs) == len(new_ys1) == len(new_ys2) == len(xs) + 1


# --------------------------------------------------------------------------- #
# _read_csv
# --------------------------------------------------------------------------- #
class TestReadCsv:
    def test_missing_file_returns_empty(self, tmp_path):
        assert pp._read_csv(str(tmp_path / "missing.csv")) == []

    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("")
        assert pp._read_csv(str(path)) == []

    def test_reads_rows_as_dicts(self, tmp_path):
        path = tmp_path / "data.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["a", "b"])
            w.writerow(["1", "2"])
        rows = pp._read_csv(str(path))
        assert rows == [{"a": "1", "b": "2"}]


# --------------------------------------------------------------------------- #
# Chart renderers — assert PNGs are produced (not pixel content)
# --------------------------------------------------------------------------- #
def _cpu_rows(n=5, start_ns=1_000_000_000_000, step_ns=100_000_000, pct=10.0):
    return [
        {"start_time_unix_nano": str(start_ns + i * step_ns), "cpu_pct": str(pct + i)}
        for i in range(n)
    ]


def _gpu_rows(n=5, start_ns=1_000_000_000_000, step_ns=100_000_000):
    return [
        {
            "start_time_unix_nano": str(start_ns + i * step_ns),
            "gfx_busy_pct": str(20.0 + i),
            "power_w": str(150.0 + i),
            "vram_mb": str(1000.0 + i),
        }
        for i in range(n)
    ]


class TestPlotCpu:
    def test_writes_png(self, tmp_path):
        pp._plot_cpu(
            _cpu_rows(), str(tmp_path), "cpu_hermes_trace.png", "Hermes process-tree CPU %"
        )
        path = tmp_path / "cpu_hermes_trace.png"
        assert path.exists()
        assert path.stat().st_size > 0

    def test_empty_rows_writes_nothing(self, tmp_path):
        pp._plot_cpu([], str(tmp_path), "cpu_hermes_trace.png", "title")
        assert not (tmp_path / "cpu_hermes_trace.png").exists()


class TestPlotGpu:
    def test_writes_png(self, tmp_path):
        pp._plot_gpu(_gpu_rows(), str(tmp_path))
        path = tmp_path / "gpu_system_wide.png"
        assert path.exists()
        assert path.stat().st_size > 0

    def test_empty_rows_writes_nothing(self, tmp_path):
        pp._plot_gpu([], str(tmp_path))
        assert not (tmp_path / "gpu_system_wide.png").exists()


class TestPlotCombined:
    def test_writes_png_with_all_series(self, tmp_path):
        pp._plot_combined(_cpu_rows(), _cpu_rows(), _gpu_rows(), [], str(tmp_path))
        path = tmp_path / "combined_timeline.png"
        assert path.exists()
        assert path.stat().st_size > 0

    def test_writes_png_with_tool_windows_shaded(self, tmp_path):
        tools = [
            {"start_time_unix_nano": "1000000000000", "duration_s": "0.5", "tool_name": "read"}
        ]
        pp._plot_combined(_cpu_rows(), [], [], tools, str(tmp_path))
        assert (tmp_path / "combined_timeline.png").exists()

    def test_all_series_empty_writes_nothing(self, tmp_path):
        pp._plot_combined([], [], [], [], str(tmp_path))
        assert not (tmp_path / "combined_timeline.png").exists()


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #
def _write_csv(path, rows, columns):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for row in rows:
            w.writerow(row)


class TestMain:
    def test_not_a_directory_returns_1(self, monkeypatch, tmp_path, capsys):
        not_a_dir = tmp_path / "does-not-exist"
        monkeypatch.setattr(sys, "argv", ["plot_profiling.py", str(not_a_dir)])
        assert pp.main() == 1
        assert "not a directory" in capsys.readouterr().err

    def test_no_chartable_csvs_returns_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(sys, "argv", ["plot_profiling.py", str(tmp_path)])
        assert pp.main() == 1
        assert "no profiling CSVs found" in capsys.readouterr().err

    def test_renders_all_four_charts(self, monkeypatch, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        _write_csv(
            session_dir / "cpu_hermes_trace.csv",
            _cpu_rows(),
            ["start_time_unix_nano", "cpu_pct"],
        )
        _write_csv(
            session_dir / "cpu_system_wide.csv",
            _cpu_rows(),
            ["start_time_unix_nano", "cpu_pct"],
        )
        _write_csv(
            session_dir / "gpu_system_wide.csv",
            _gpu_rows(),
            ["start_time_unix_nano", "gfx_busy_pct", "power_w", "vram_mb"],
        )
        _write_csv(
            session_dir / "tool_execution.csv",
            [{"start_time_unix_nano": "1000000000000", "duration_s": "0.5", "tool_name": "read"}],
            ["start_time_unix_nano", "duration_s", "tool_name"],
        )

        monkeypatch.setattr(sys, "argv", ["plot_profiling.py", str(session_dir)])
        assert pp.main() == 0

        plots_dir = session_dir / "plots"
        for name in (
            "cpu_hermes_trace.png",
            "cpu_system_wide.png",
            "gpu_system_wide.png",
            "combined_timeline.png",
        ):
            path = plots_dir / name
            assert path.exists(), f"{name} was not rendered"
            assert path.stat().st_size > 0

    def test_custom_out_dir(self, monkeypatch, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        out_dir = tmp_path / "custom-out"
        _write_csv(
            session_dir / "cpu_hermes_trace.csv",
            _cpu_rows(),
            ["start_time_unix_nano", "cpu_pct"],
        )

        monkeypatch.setattr(
            sys, "argv", ["plot_profiling.py", str(session_dir), "--out", str(out_dir)]
        )
        assert pp.main() == 0
        assert (out_dir / "cpu_hermes_trace.png").exists()
