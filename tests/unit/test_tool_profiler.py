"""Tests for per-tool CPU/GPU attribution (tool_profiler.py).

tool_profiler.py keeps module-level mutable dicts to track sessions, tool
starts, and turn counters across calls. An autouse fixture resets that state
between tests so results never bleed across test cases.
"""

import csv
import os
import sys
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from hermes_otel import tool_profiler as tp


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    tp._session_pollers.clear()
    tp._tool_starts.clear()
    tp._turn_counters.clear()
    tp._turn_starts.clear()
    tp._new_turn_pending.clear()
    monkeypatch.setattr(tp, "_atexit_registered", False)
    for name in (
        "HERMES_CPU_TRACE",
        "HERMES_CPU_SYSTEM_WIDE",
        "HERMES_GPU_SYSTEM_WIDE",
        "HERMES_TOOL_TRACE",
        "HERMES_PLOT_PROFILING",
        "HERMES_GPU_VENDOR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_PROFILING_OUTPUT_DIR", str(tmp_path))
    yield
    tp._session_pollers.clear()
    tp._tool_starts.clear()
    tp._turn_counters.clear()
    tp._turn_starts.clear()
    tp._new_turn_pending.clear()


def _fmt_ts(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _write_timeline_csv(path, value_col, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", value_col])
        for ts, val in rows:
            w.writerow([_fmt_ts(ts), val])


# --------------------------------------------------------------------------- #
# _ensure_session
# --------------------------------------------------------------------------- #
class TestEnsureSession:
    def test_nothing_enabled_returns_none(self):
        assert tp._ensure_session("s1") is None

    def test_no_session_id_returns_none(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        assert tp._ensure_session("") is None

    def test_creates_out_dir_and_file_paths(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        state = tp._ensure_session("s1")
        assert state is not None
        assert state["out_dir"] == str(tmp_path / "s1")
        assert os.path.isdir(state["out_dir"])
        assert state["cpu_csv"].endswith("cpu_hermes_trace.csv")
        assert state["gpu_csv"].endswith("gpu_system_wide.csv")
        assert state["breakdown"].endswith("tool_execution.csv")
        # Only tool tracing is on -> no out-of-process poller needed.
        assert state["proc"] is None

    def test_breakdown_header_written_only_when_tool_trace_on(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        state = tp._ensure_session("s1")
        with open(state["breakdown"]) as f:
            header = f.readline().strip().split(",")
        assert header == tp._BREAKDOWN_HEADER

    def test_no_breakdown_header_when_tool_trace_off(self, monkeypatch):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        monkeypatch.setattr(tp, "_maybe_start_poller", MagicMock())
        state = tp._ensure_session("s1")
        assert not os.path.exists(state["breakdown"])

    def test_idempotent_returns_same_state(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        first = tp._ensure_session("s1")
        second = tp._ensure_session("s1")
        assert first is second

    def test_makedirs_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")

        def _raise(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(tp.os, "makedirs", _raise)
        assert tp._ensure_session("s1") is None


# --------------------------------------------------------------------------- #
# _maybe_start_poller
# --------------------------------------------------------------------------- #
class TestMaybeStartPoller:
    def test_noop_when_hardware_trace_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")  # tool trace alone
        popen_mock = MagicMock()
        monkeypatch.setattr(tp.subprocess, "Popen", popen_mock)
        state = {"proc": None, "out_dir": str(tmp_path)}
        tp._maybe_start_poller("s1", state)
        popen_mock.assert_not_called()

    def test_launches_poller_with_expected_argv(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        fake_proc = MagicMock(pid=4242)
        popen_mock = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(tp.subprocess, "Popen", popen_mock)
        state = {"proc": None, "out_dir": str(tmp_path)}

        tp._maybe_start_poller("s1", state)

        assert popen_mock.call_count == 1
        argv = popen_mock.call_args[0][0]
        assert argv[0] == sys.executable
        assert argv[1].endswith("metrics_poller.py")
        assert argv[2] == str(tmp_path)
        assert argv[3] == "0.1"  # HERMES_POLL_INTERVAL default
        assert argv[4] == str(os.getpid())
        assert state["proc"] is fake_proc

    def test_does_not_relaunch_when_already_running(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        running_proc = MagicMock()
        running_proc.poll.return_value = None  # still alive
        popen_mock = MagicMock()
        monkeypatch.setattr(tp.subprocess, "Popen", popen_mock)
        state = {"proc": running_proc, "out_dir": str(tmp_path)}

        tp._maybe_start_poller("s1", state)
        popen_mock.assert_not_called()

    def test_relaunches_when_previous_process_died(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")
        dead_proc = MagicMock()
        dead_proc.poll.return_value = 1  # exited
        popen_mock = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(tp.subprocess, "Popen", popen_mock)
        state = {"proc": dead_proc, "out_dir": str(tmp_path)}

        tp._maybe_start_poller("s1", state)
        popen_mock.assert_called_once()

    def test_popen_exception_is_swallowed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_CPU_SYSTEM_WIDE", "true")

        def _raise(*a, **k):
            raise OSError("cannot spawn")

        monkeypatch.setattr(tp.subprocess, "Popen", _raise)
        state = {"proc": None, "out_dir": str(tmp_path)}
        tp._maybe_start_poller("s1", state)  # must not raise


# --------------------------------------------------------------------------- #
# start_session_poller / note_turn / end_turn
# --------------------------------------------------------------------------- #
class TestTurnLifecycle:
    def test_start_session_poller_calls_ensure_session(self, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(tp, "_ensure_session", mock)
        tp.start_session_poller("s1")
        mock.assert_called_once_with("s1")

    def test_note_turn_noop_when_nothing_enabled(self):
        tp.note_turn("s1")
        assert "s1" not in tp._turn_counters

    def test_first_call_increments_to_one(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.note_turn("s1")
        assert tp._turn_counters["s1"] == 1
        assert "s1" in tp._turn_starts

    def test_intra_turn_calls_do_not_reincrement(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.note_turn("s1")
        tp.note_turn("s1")
        tp.note_turn("s1")
        assert tp._turn_counters["s1"] == 1

    def test_end_turn_arms_next_increment(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.note_turn("s1")
        tp.end_turn("s1")
        tp.note_turn("s1")
        assert tp._turn_counters["s1"] == 2

    def test_end_turn_noop_when_nothing_enabled(self):
        tp.end_turn("s1")
        assert "s1" not in tp._new_turn_pending

    def test_resume_reseeds_counter_from_persisted_file(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.note_turn("s1")
        assert tp._turn_counters["s1"] == 1

        # Simulate a fresh process (e.g. `hermes -r`) that has lost in-memory
        # state but the persisted .turn_count file survives.
        tp._turn_counters.pop("s1")
        tp._new_turn_pending.pop("s1", None)

        tp.note_turn("s1")
        assert tp._turn_counters["s1"] == 2


# --------------------------------------------------------------------------- #
# _read_turn_state / _write_turn_state / current_turn
# --------------------------------------------------------------------------- #
class TestTurnStatePersistence:
    def test_round_trip(self, tmp_path):
        (tmp_path / "s1").mkdir()
        tp._write_turn_state("s1", 7)
        assert tp._read_turn_state("s1") == 7

    def test_read_missing_file_returns_zero(self):
        assert tp._read_turn_state("nope") == 0

    def test_write_failure_is_swallowed(self, tmp_path):
        # Directory for "s1" was never created, so open() fails with
        # FileNotFoundError; this must be caught, not raised.
        tp._write_turn_state("s1", 3)

    def test_current_turn_prefers_in_memory(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.note_turn("s1")
        assert tp.current_turn("s1") == 1

    def test_current_turn_falls_back_to_persisted_file(self, tmp_path):
        (tmp_path / "s1").mkdir()
        tp._write_turn_state("s1", 9)
        assert tp.current_turn("s1") == 9

    def test_current_turn_zero_for_unknown_session(self):
        assert tp.current_turn("nope") == 0

    def test_current_turn_zero_for_empty_session_id(self):
        assert tp.current_turn("") == 0


# --------------------------------------------------------------------------- #
# _stop_one / _stop_all_pollers / _render_plots
# --------------------------------------------------------------------------- #
class TestStopPollers:
    def test_stop_one_terminates_running_process(self):
        proc = MagicMock()
        proc.poll.return_value = None
        state = {"proc": proc}
        tp._stop_one(state)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=10)

    def test_stop_one_kills_when_terminate_fails(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("no such process")
        state = {"proc": proc}
        tp._stop_one(state)
        proc.kill.assert_called_once()

    def test_stop_one_skips_already_exited_process(self):
        proc = MagicMock()
        proc.poll.return_value = 0  # already exited
        state = {"proc": proc}
        tp._stop_one(state)
        proc.terminate.assert_not_called()

    def test_stop_one_closes_devnull(self):
        devnull = MagicMock()
        state = {"proc": None, "_devnull": devnull}
        tp._stop_one(state)
        devnull.close.assert_called_once()

    def test_stop_all_pollers_clears_sessions(self):
        tp._session_pollers["s1"] = {"proc": None, "out_dir": "/tmp/s1"}
        tp._session_pollers["s2"] = {"proc": None, "out_dir": "/tmp/s2"}
        tp._stop_all_pollers()
        assert tp._session_pollers == {}

    def test_stop_all_pollers_skips_plotting_when_disabled(self, monkeypatch):
        render_mock = MagicMock()
        monkeypatch.setattr(tp, "_render_plots", render_mock)
        tp._session_pollers["s1"] = {"proc": None, "out_dir": "/tmp/s1"}
        tp._stop_all_pollers()
        render_mock.assert_not_called()

    def test_stop_all_pollers_renders_plots_when_enabled(self, monkeypatch):
        monkeypatch.setenv("HERMES_PLOT_PROFILING", "true")
        render_mock = MagicMock()
        monkeypatch.setattr(tp, "_render_plots", render_mock)
        tp._session_pollers["s1"] = {"proc": None, "out_dir": "/tmp/s1"}
        tp._session_pollers["s2"] = {"proc": None, "out_dir": "/tmp/s2"}
        tp._stop_all_pollers()
        assert render_mock.call_count == 2

    def test_render_plots_invokes_plot_script(self, monkeypatch, tmp_path):
        run_mock = MagicMock()
        monkeypatch.setattr(tp.subprocess, "run", run_mock)
        tp._render_plots(str(tmp_path))
        args, kwargs = run_mock.call_args
        argv = args[0]
        assert argv[0] == sys.executable
        assert argv[1].endswith("plot_profiling.py")
        assert argv[2] == str(tmp_path)
        assert kwargs["timeout"] == 120

    def test_render_plots_exception_is_swallowed(self, monkeypatch, tmp_path):
        def _raise(*a, **k):
            raise OSError("boom")

        monkeypatch.setattr(tp.subprocess, "run", _raise)
        tp._render_plots(str(tmp_path))  # must not raise

    def test_stop_session_poller_is_a_noop(self):
        assert tp.stop_session_poller("s1") is None


# --------------------------------------------------------------------------- #
# _slice_column
# --------------------------------------------------------------------------- #
class TestSliceColumn:
    def test_missing_file_returns_empty_lists(self):
        out = tp._slice_column("/no/such/file.csv", ["cpu_pct"], 0.0, 100.0)
        assert out == {"cpu_pct": []}

    def test_filters_by_wall_clock_window(self, tmp_path):
        path = tmp_path / "cpu.csv"
        t0 = 1_700_000_000.0
        _write_timeline_csv(
            path, "cpu_pct", [(t0 - 10, 1.0), (t0 + 1, 2.0), (t0 + 2, 3.0), (t0 + 100, 4.0)]
        )
        out = tp._slice_column(str(path), ["cpu_pct"], t0, t0 + 5)
        assert out["cpu_pct"] == [2.0, 3.0]

    def test_malformed_timestamp_row_is_skipped(self, tmp_path):
        path = tmp_path / "cpu.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "cpu_pct"])
            w.writerow(["not-a-timestamp", "5.0"])
        out = tp._slice_column(str(path), ["cpu_pct"], 0.0, 1e15)
        assert out["cpu_pct"] == []

    def test_unparsable_numeric_cell_is_skipped(self, tmp_path):
        path = tmp_path / "cpu.csv"
        t0 = 1_700_000_000.0
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "cpu_pct"])
            w.writerow([_fmt_ts(t0), "not-a-number"])
        out = tp._slice_column(str(path), ["cpu_pct"], t0 - 1, t0 + 1)
        assert out["cpu_pct"] == []


# --------------------------------------------------------------------------- #
# record_tool_start / record_tool_end
# --------------------------------------------------------------------------- #
class TestRecordTool:
    def test_start_noop_when_tool_trace_disabled(self):
        tp.record_tool_start("k1", session_id="s1")
        assert "k1" not in tp._tool_starts

    def test_start_records_wall_time(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.record_tool_start("k1", session_id="s1")
        assert tp._tool_starts["k1"]["session_id"] == "s1"

    def test_end_noop_when_tool_trace_disabled(self):
        tp.record_tool_end("k1", "read")  # must not raise

    def test_end_noop_when_no_matching_start(self, monkeypatch):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        tp.record_tool_end("missing-key", "read")  # must not raise

    def test_end_writes_breakdown_row_with_cpu_gpu_averages(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        session_id = "s1"
        state = tp._ensure_session(session_id)

        t0 = 1_700_000_000.0
        duration = 2.0
        _write_timeline_csv(
            state["cpu_csv"],
            "cpu_pct",
            [(t0 + 0.0, 10.0), (t0 + 0.5, 20.0), (t0 + 1.0, 30.0), (t0 + 1.5, 40.0)],
        )
        _write_timeline_csv(
            state["gpu_csv"],
            "gfx_busy_pct",
            [(t0 + 0.0, 5.0), (t0 + 0.5, 15.0), (t0 + 1.0, 25.0), (t0 + 1.5, 35.0)],
        )

        tp._tool_starts["k1"] = {"wall_start": t0, "session_id": session_id}
        tp._turn_counters[session_id] = 3
        tp._turn_starts[session_id] = t0 - 1.0
        monkeypatch.setattr(tp.time, "time", lambda: t0 + duration)

        tp.record_tool_end(
            "k1", "read_file", session_id=session_id, args={"path": "/tmp/x"}, result={"ok": True}
        )

        with open(state["breakdown"]) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        row = rows[0]
        assert row["turn"] == "3"
        assert row["tool_name"] == "read_file"
        assert row["input"] == '{"path": "/tmp/x"}'
        assert row["output"] == '{"ok": true}'
        assert float(row["cpu_avg_pct"]) == 25.0
        assert float(row["cpu_peak_pct"]) == 40.0
        assert float(row["gpu_avg_pct"]) == 20.0
        assert float(row["gpu_peak_pct"]) == 35.0
        assert float(row["duration_s"]) == pytest.approx(2.0, abs=0.01)
        assert float(row["elapsed_s"]) == pytest.approx(1.0, abs=0.01)
        # The key is popped once consumed, so a second call is a no-op.
        assert "k1" not in tp._tool_starts

    def test_end_handles_string_and_none_result(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        state = tp._ensure_session("s1")
        tp._tool_starts["k1"] = {"wall_start": time.time(), "session_id": "s1"}

        tp.record_tool_end("k1", "echo", session_id="s1", args=None, result="plain text")

        with open(state["breakdown"]) as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["input"] == ""
        assert rows[0]["output"] == "plain text"

    def test_end_truncates_long_input(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_TOOL_TRACE", "true")
        state = tp._ensure_session("s1")
        tp._tool_starts["k1"] = {"wall_start": time.time(), "session_id": "s1"}

        long_arg = {"data": "x" * 5000}
        tp.record_tool_end("k1", "echo", session_id="s1", args=long_arg, result=None)

        with open(state["breakdown"]) as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["input"].endswith("...")
        assert len(rows[0]["input"]) == tp._PREVIEW_MAX_CHARS + len("...")
        assert rows[0]["output"] == ""
