"""Per-tool CPU/GPU attribution for hermes-otel.

Answers "which tool used how much CPU and GPU" by measuring each tool call
between the pre_tool_call and post_tool_call hooks.

Design constraints (from the deployment requirements):
  * NO extra threads inside the hermes process. CPU is measured with cumulative
    kernel counters (two reads, no sampler). GPU is sampled by a SEPARATE OS
    process (metrics_poller.py) launched per session, then sliced per tool.
  * An external inference server running in its own process or container (e.g.
    a model server hermes talks to over HTTP) is not a child of the hermes
    process, so its CPU is naturally excluded by construction.

CPU accuracy note: the delta of cpu_times() over the hermes process plus its
live children captures all in-process tool work. A grandchild that both spawns
and fully exits inside a single tool window contributes only the portion the
parent accounted for; hermes tools are in-process Python, so this is accurate in
practice.

Everything here is opt-in via the HERMES_* feature flags (see profiling_env.py).
When nothing is enabled every public function is a no-op with zero overhead.

Responsibilities split by flag:
  * the out-of-process poller starts when any hardware trace is on
    (HERMES_CPU_TRACE / HERMES_CPU_SYSTEM_WIDE / HERMES_GPU_SYSTEM_WIDE);
  * the per-tool breakdown (tool_execution.csv) is written only under
    HERMES_TOOL_TRACE;
  * the shared per-turn counter advances whenever ANY profiling feature is on,
    because it is the join key the tool CSV shares with the trace spans
    (the ``hermes.turn.number`` attribute).

Configuration (env vars): see profiling_env.py for the authoritative list.
  HERMES_GPU_VENDOR            amd / nvidia (default: auto-detect)
  HERMES_PROFILING_OUTPUT_DIR  base output dir (default "./outputs")
  HERMES_POLL_INTERVAL         poll interval seconds (default "0.1")
"""

import os
import csv
import sys
import json
import time
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

from . import profiling_env as _env
from .helpers import truncate_string


def _debug(msg: str):
    try:
        from .debug_utils import debug_log
        debug_log(f"[tool_profiler] {msg}")
    except Exception:
        if os.environ.get("HERMES_PROFILING_DEBUG"):
            sys.stderr.write(f"[tool_profiler] {msg}\n")


_BREAKDOWN_HEADER = [
    "turn", "tool_name", "input", "output", "timestamp", "start_time_unix_nano",
    "elapsed_s", "duration_s", "cpu_avg_pct", "cpu_peak_pct",
    "gpu_avg_pct", "gpu_peak_pct",
]

# Max length of the JSON-serialized tool args/result stored in the "input"/
# "output" columns. Matches plugin_config.py's PluginConfig.preview_max_chars
# default, so the CSV keeps roughly as much as the "input.value"/"output.value"
# attributes the trace's backend (e.g. Phoenix) already shows for the same call.
_PREVIEW_MAX_CHARS = 1200

# session_id -> {"proc": Popen|None, "out_dir": path, "cpu_csv": path,
#                 "gpu_csv": path, "breakdown": path, "_devnull": fh}
# "proc" is None when only tool tracing is on (no hardware poller needed).
_session_pollers: Dict[str, dict] = {}
# key (f"{tool_name}:{task_id}") -> {"wall_start": float, "session_id": str}
_tool_starts: Dict[str, dict] = {}
# session_id -> current turn number (one prompt = one turn)
_turn_counters: Dict[str, int] = {}
# session_id -> turn start timestamp (wall clock) - reset at each turn
_turn_starts: Dict[str, float] = {}
# session_id -> True when the next note_turn should start a NEW user turn.
# on_pre_llm_call fires once per agent-loop step (many per user prompt), but a
# turn must advance only ONCE per user prompt. on_session_end (end_turn) arms
# this; the first note_turn afterwards consumes it and increments the counter.
# Intra-turn calls (flag False) only refresh the poller, never the counter.
_new_turn_pending: Dict[str, bool] = {}
# Ensure pollers are stopped exactly once at process exit (atexit registered lazily)
_atexit_registered = False


def _ensure_session(session_id: str):
    """Ensure the per-session output dir, file paths and (if needed) the poller
    exist. Returns the session state dict, or None when nothing is enabled.

    Idempotent and safe across turns AND resume: hermes fires on_session_start
    only on the first turn (and a fresh process on `hermes -r`), while tools run
    on every turn. All CSVs are opened in append mode, so a resumed session
    accumulates onto the same files. The breakdown header is written only when
    the file is new/empty and only when tool tracing is on.
    """
    if not _env.any_enabled() or not session_id:
        return None

    existing = _session_pollers.get(session_id)
    if existing is not None:
        _maybe_start_poller(session_id, existing)  # relaunch if it died
        return existing

    base = os.environ.get("HERMES_PROFILING_OUTPUT_DIR", "./outputs")
    out_dir = os.path.join(base, session_id)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        _debug(f"could not create output dir {out_dir}: {e}")
        return None

    state = {
        "proc": None,
        "out_dir": out_dir,
        # The per-tool breakdown slices the ATTRIBUTABLE hermes-tree CPU and the
        # system-wide GPU; system-wide CPU is deliberately not used for slicing.
        "cpu_csv": os.path.join(out_dir, "cpu_hermes_trace.csv"),
        "gpu_csv": os.path.join(out_dir, "gpu_system_wide.csv"),
        "breakdown": os.path.join(out_dir, "tool_execution.csv"),
    }

    # Write the breakdown header only when tool tracing is on and the file is new.
    if _env.tool_trace():
        try:
            bd = state["breakdown"]
            if not os.path.exists(bd) or os.path.getsize(bd) == 0:
                with open(bd, "a", newline="") as f:
                    csv.writer(f).writerow(_BREAKDOWN_HEADER)
        except Exception as e:
            _debug(f"could not init tool_execution.csv: {e}")

    _session_pollers[session_id] = state
    _maybe_start_poller(session_id, state)

    global _atexit_registered
    if not _atexit_registered:
        import atexit
        atexit.register(_stop_all_pollers)
        _atexit_registered = True

    return state


def _maybe_start_poller(session_id: str, state: dict):
    """Launch the out-of-process CPU/GPU poller if a hardware trace is enabled
    and it isn't already running. No-op when only tool tracing is on."""
    if not _env.hardware_trace_enabled():
        return
    proc = state.get("proc")
    if proc is not None and proc.poll() is None:
        return  # already running in this process

    interval = os.environ.get("HERMES_POLL_INTERVAL", "0.1")
    poller_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics_poller.py")
    try:
        devnull = open(os.devnull, "w")
        state["_devnull"] = devnull
        # The poller reads the HERMES_* flags (including HERMES_GPU_VENDOR) from
        # the inherited environment to decide which per-signal CSVs to write in
        # out_dir and which GPU vendor SDK to query.
        state["proc"] = subprocess.Popen(
            [sys.executable, poller_script, state["out_dir"], interval, str(os.getpid())],
            stdout=devnull, stderr=devnull,
        )
        _debug(f"metrics poller started (pid={state['proc'].pid}) -> {state['out_dir']}")
    except Exception as e:
        _debug(f"failed to start poller: {e}")


def start_session_poller(session_id: str, **kwargs):
    """Hook entry: start profiling at session start (first turn)."""
    _ensure_session(session_id)


def note_turn(session_id: str, is_first_turn: bool = False, **kwargs):
    """Advance the per-session turn counter and ensure the poller is running.

    Called from on_pre_llm_call, which fires once per prompt. This is also the
    safety net for continuation turns and `hermes -r` resume, where
    on_session_start does not fire in this process.
    """
    if not _env.any_enabled() or not session_id:
        return

    _ensure_session(session_id)
    # On the first turn seen in THIS process, seed the counter from the persisted
    # turn-count file. `hermes -r` resumes the same session_id in a fresh process,
    # so the in-memory counter would otherwise restart at 1. We persist the count
    # itself (not infer it from the CSV) so it stays correct even for turns that
    # ran no tools and therefore wrote no CSV row.
    if session_id not in _turn_counters:
        _turn_counters[session_id] = _read_turn_state(session_id)
        # First note_turn of this process begins a user turn.
        _new_turn_pending.setdefault(session_id, True)

    # Advance the counter only ONCE per user prompt. on_pre_llm_call fires once
    # per agent-loop reasoning step (many per prompt); only the first call after
    # an on_session_end (which armed the flag) starts a new turn. Intra-turn
    # calls fall through and just keep the poller alive.
    if not _new_turn_pending.get(session_id, True):
        return
    _new_turn_pending[session_id] = False

    _turn_counters[session_id] += 1
    _write_turn_state(session_id, _turn_counters[session_id])

    # Capture the turn start time for per-turn elapsed_s calculations
    _turn_starts[session_id] = time.time()


def end_turn(session_id: str, **kwargs):
    """Mark the end of a user turn so the next note_turn starts a fresh one.

    Called from on_session_end, which fires once per user prompt. Arming the
    flag here is what makes the counter advance exactly once per prompt instead
    of once per agent-loop LLM call.
    """
    if not _env.any_enabled() or not session_id:
        return
    _new_turn_pending[session_id] = True


def _turn_state_path(session_id: str) -> str:
    base = os.environ.get("HERMES_PROFILING_OUTPUT_DIR", "./outputs")
    return os.path.join(base, session_id, ".turn_count")


def _read_turn_state(session_id: str) -> int:
    """Read the last persisted turn number for this session (0 if none).

    Persisting the count directly means it survives `hermes -r` and counts every
    turn — including tool-less turns that leave no row in tool_execution.csv."""
    try:
        with open(_turn_state_path(session_id), "r") as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0
    except Exception as e:
        _debug(f"could not read turn state: {e}")
        return 0


def _write_turn_state(session_id: str, turn: int):
    try:
        with open(_turn_state_path(session_id), "w") as f:
            f.write(str(turn))
    except Exception as e:
        _debug(f"could not write turn state: {e}")


def current_turn(session_id: str) -> int:
    """Return the current turn number for a session — the single source of truth
    shared by the per-tool CSV and the ``hermes.turn.number`` span attribute so
    they always match. Falls back to the persisted file if this process has not
    yet seen the session in memory (e.g. resume)."""
    if not session_id:
        return 0
    if session_id in _turn_counters:
        return _turn_counters[session_id]
    return _read_turn_state(session_id)


def _stop_one(state: dict):
    proc = state.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        if state.get("_devnull"):
            state["_devnull"].close()
    except Exception:
        pass


def _stop_all_pollers():
    """Stop every running poller. Registered via atexit so it runs once when the
    hermes process exits — NOT per turn (on_session_end fires every turn)."""
    out_dirs = []
    for sid, state in list(_session_pollers.items()):
        _stop_one(state)
        if state.get("out_dir"):
            out_dirs.append(state["out_dir"])
        _session_pollers.pop(sid, None)
    _debug("all pollers stopped at process exit")

    # Optional plotting runs here — after every poller has stopped and the CSVs
    # are final — rather than as its own atexit handler. This guarantees the
    # render never overlaps sampling, so the LLM/tool timings and the per-tool
    # CPU/GPU figures (already computed during the run) cannot be affected by it.
    if _env.plot_profiling():
        for out_dir in out_dirs:
            _render_plots(out_dir)


def _render_plots(out_dir: str):
    """Render the session CSVs to PNGs by running the standalone plot_profiling
    script in a separate process. Invoked only at process exit, after the poller
    has stopped, so it adds no samples and cannot perturb any measurement. A
    missing matplotlib (or any render error) is logged and otherwise ignored."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plot_profiling.py")
    try:
        subprocess.run([sys.executable, script, out_dir], timeout=120)
        _debug(f"profiling plots rendered -> {os.path.join(out_dir, 'plots')}")
    except Exception as e:
        _debug(f"plot rendering failed: {e}")


def stop_session_poller(session_id: str, **kwargs):
    """Retained for compatibility; intentionally a no-op.

    The poller must survive across turns (on_session_end fires per turn), so it
    is stopped at process exit via atexit (_stop_all_pollers), not here.
    """
    return


def record_tool_start(key: str, session_id: Optional[str] = None, **kwargs):
    """Record the wall-clock start of a tool call.

    Averages are computed at tool end by slicing the timeline CSVs (which the
    poller subprocess writes), so we only need the start timestamp here. The
    tool's input is captured later, in `record_tool_end`, since `on_post_tool_
    call` receives the same `args` and that's also where the CSV row is
    actually written.
    """
    if not _env.tool_trace():
        return
    _tool_starts[key] = {"wall_start": time.time(), "session_id": session_id}


def _slice_column(csv_path: str, value_cols, start_wall: float, end_wall: float):
    """Collect lists of values from `value_cols` for rows whose timestamp falls
    within [start_wall, end_wall]. Timeline CSVs use timestamp (human-readable),
    which we parse back to epoch seconds for comparison.

    Returns a dict {col: [values]}. Missing file/columns yield empty lists.
    """
    out = {c: [] for c in value_cols}
    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # Parse timestamp back to epoch seconds
                    ts_str = row.get("timestamp", "")
                    if not ts_str:
                        continue
                    ts_epoch = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f").timestamp()
                except (KeyError, ValueError):
                    continue
                if start_wall <= ts_epoch <= end_wall:
                    for c in value_cols:
                        try:
                            out[c].append(float(row.get(c, 0) or 0))
                        except ValueError:
                            pass
    except FileNotFoundError:
        pass
    except Exception as e:
        _debug(f"slice failed for {csv_path}: {e}")
    return out


def record_tool_end(key: str, tool_name: str, session_id: Optional[str] = None,
                     args: Optional[dict] = None, result: Optional[Any] = None, **kwargs):
    """Compute per-tool CPU/GPU averages from the timelines and write the CSV row.

    `args`/`result` (the same raw tool call arguments and result `on_post_tool_
    call` receives) are serialized here, right where the CSV row is written, so
    the "input"/"output" columns match exactly what hooks.py sends as the
    trace's "Inputs"/"Outputs" for the same tool call — regardless of which
    argument keys a given tool happens to use.
    """
    if not _env.tool_trace():
        return
    start = _tool_starts.pop(key, None)
    if not start:
        return

    sid = session_id or start.get("session_id")
    state = _session_pollers.get(sid) if sid else None
    try:
        tool_input = json.dumps(args) if args else ""
    except Exception:
        tool_input = str(args) if args else ""
    tool_input = truncate_string(tool_input, _PREVIEW_MAX_CHARS)

    try:
        if isinstance(result, (dict, list)):
            tool_output = json.dumps(result)
        else:
            tool_output = result if isinstance(result, str) else str(result) if result is not None else ""
    except Exception:
        tool_output = str(result) if result is not None else ""
    tool_output = truncate_string(tool_output, _PREVIEW_MAX_CHARS)

    try:
        wall_start = start["wall_start"]
        wall_end = time.time()
        duration = max(wall_end - wall_start, 1e-6)

        cpu_avg = cpu_peak = 0.0
        gpu_avg = gpu_peak = 0.0

        # Slice the timeline CSVs using wall-clock timestamps
        if state:
            cpu_data = _slice_column(
                state.get("cpu_csv", ""), ["cpu_pct"], wall_start, wall_end
            )
            cpu_vals = cpu_data["cpu_pct"]
            if cpu_vals:
                cpu_avg = round(sum(cpu_vals) / len(cpu_vals), 1)
                cpu_peak = round(max(cpu_vals), 1)

            gpu_data = _slice_column(
                state.get("gpu_csv", ""),
                ["gfx_busy_pct"], wall_start, wall_end,
            )
            gfx_vals = gpu_data["gfx_busy_pct"]
            if gfx_vals:
                gpu_avg = round(sum(gfx_vals) / len(gfx_vals), 2)
                gpu_peak = round(max(gfx_vals), 2)

        turn = _turn_counters.get(sid, 0) if sid else 0

        # Calculate elapsed time since TURN start (not session start)
        turn_start = _turn_starts.get(sid) if sid else None
        if turn_start is not None:
            # Per-turn elapsed: time from turn start to tool start
            turn_elapsed = wall_start - turn_start
        else:
            # Fallback: 0 if no turn start tracked
            turn_elapsed = 0.0

        start_ts = datetime.fromtimestamp(wall_start).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        # Absolute epoch nanoseconds, same convention as metrics_poller.py's
        # cpu_hermes_trace/gpu_system_wide CSVs and every span's
        # start_time_unix_nano attribute - lets the dashboard correlate a tool's
        # shaded window with the utilization curve on one shared clock instead
        # of falling back to this local-time string.
        start_ns = int(wall_start * 1_000_000_000)
        start_elapsed_str = f"{turn_elapsed:.3f}"

        # Append to the per-session breakdown CSV.
        # Column order: turn, tool_name, input, output, timestamp,
        # start_time_unix_nano, elapsed_s, duration_s, cpu_avg_pct, cpu_peak_pct,
        # gpu_avg_pct, gpu_peak_pct
        if state and state.get("breakdown"):
            try:
                with open(state["breakdown"], "a", newline="") as f:
                    csv.writer(f).writerow(
                        [turn, tool_name, tool_input, tool_output, start_ts, start_ns,
                         start_elapsed_str, f"{duration:.2f}", cpu_avg, cpu_peak,
                         gpu_avg, gpu_peak]
                    )
            except Exception as e:
                _debug(f"could not append breakdown row: {e}")

        _debug(
            f"{tool_name}: cpu_avg={cpu_avg}% gpu_avg={gpu_avg}% "
            f"peak={gpu_peak}% dur={duration:.2f}s"
        )
    except Exception as e:
        _debug(f"record_tool_end failed for {key}: {e}")
