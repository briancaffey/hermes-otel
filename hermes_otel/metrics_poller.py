"""Standalone CPU / GPU metrics poller (runs as a separate OS process).

Launched by tool_profiler via subprocess.Popen so that all sampling happens
OUTSIDE the hermes interpreter (no extra threads inside hermes). It samples on a
fixed interval and appends timestamped rows to one CSV per ENABLED signal:

  * cpu_hermes_trace.csv  — hermes process + children CPU%   [HERMES_CPU_TRACE]
  * cpu_system_wide.csv   — whole-host CPU%                  [HERMES_CPU_SYSTEM_WIDE]
  * gpu_system_wide.csv   — busy / power / vram, aggregated    [HERMES_GPU_SYSTEM_WIDE]
                            across every GPU on the detected vendor SDK
                            (AMD via amdsmi, NVIDIA via pynvml)

Which files are written is decided entirely by the HERMES_* env flags, which the
subprocess inherits from the launching hermes process (see profiling_env.py for
the authoritative list). Disabled signals are never sampled. cpu_hermes_trace is
attributable to hermes; the system-wide CPU includes the OS and every other
process on the host, and is provided as context only.

GPU vendor selection: HERMES_GPU_VENDOR forces "amd" or "nvidia"; left unset (or
"auto"), the vendor is auto-detected in that order — the first one that finds a
working GPU handle is used for the rest of the process. When multiple GPUs are
present, busy% is averaged across them (a ratio) and power/VRAM are summed
(physical totals), matching how a multi-GPU host's real load and memory
pressure are commonly reported.

Usage:
    python metrics_poller.py <out_dir> <interval> <hermes_pid>

Runs until SIGTERM/SIGINT, flushing each row so the CSVs can be read while still
being written. Imports nothing from the hermes plugin.
"""

import os
import re
import csv
import sys
import time
import signal
import subprocess
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None


def _flag(name: str) -> bool:
    """Standalone copy of profiling_env._flag (this process can't import the
    plugin). The variable NAMES are the contract shared with profiling_env.py."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_RUNNING = True


def _stop(signum, frame):
    global _RUNNING
    _RUNNING = False


# --------------------------------------------------------------------------- #
# GPU vendor backends. Each returns a list of one dict per GPU:
#   {"busy_pct": float|None, "power_w": float|None, "vram_used_mb": float|None}
# A None field means that reading could not be obtained for that GPU on this
# tick; _aggregate_gpu excludes it from the corresponding aggregate rather than
# treating it as zero.
# --------------------------------------------------------------------------- #

def _amd_init():
    """Initialize AMD SMI and return its GPU handles, or None if amdsmi is not
    installed or no AMD GPU is present."""
    try:
        import amdsmi

        amdsmi.amdsmi_init()
        handles = amdsmi.amdsmi_get_processor_handles()
        if not handles:
            amdsmi.amdsmi_shut_down()
            return None
        return handles
    except Exception:
        return None


def _amd_shutdown(handles):
    try:
        import amdsmi

        amdsmi.amdsmi_shut_down()
    except Exception:
        pass


def _amd_cli_snapshot():
    """Fallback reader for hosts where the amdsmi Python API is blocked for
    busy%, power, and/or VRAM (observed on SR-IOV VF GPU partitions, where
    amdsmi_get_gpu_activity, amdsmi_get_gpu_metrics_info and
    amdsmi_get_gpu_vram_usage can all raise AmdSmiLibraryException). Runs
    `rocm-smi` ONCE, covering every GPU on the host in a single subprocess
    call, rather than once per GPU — the caller is responsible for calling
    this at most once per tick (see _amd_gpu_stats).

    Returns {gpu_index: {"busy_pct": float|None, "power_w": float|None,
    "vram_used_mb": float|None}}, keyed by the index rocm-smi reports (e.g.
    "GPU[0]"), which matches the position of each handle in
    amdsmi_get_processor_handles()'s device-ordered list. Empty on any failure
    (rocm-smi missing, timeout, unparsable output).

    rocm-smi reports VRAM in raw bytes here, unlike the Python API's MB.
    """
    try:
        res = subprocess.run(
            ["rocm-smi", "--showuse", "--showmeminfo", "vram", "--showpower"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return {}

    snapshot = {}
    for line in res.stdout.splitlines():
        m = re.match(r"GPU\[(\d+)\]\s*:\s*(.+)", line.strip())
        if not m:
            continue
        idx, rest = int(m.group(1)), m.group(2)
        entry = snapshot.setdefault(
            idx, {"busy_pct": None, "power_w": None, "vram_used_mb": None}
        )
        if "GPU use (%)" in rest:
            try:
                entry["busy_pct"] = float(rest.split(":")[-1].strip())
            except ValueError:
                pass
        elif "VRAM Total Used Memory" in rest:
            digits = "".join(filter(str.isdigit, rest))
            if digits:
                entry["vram_used_mb"] = float(digits) / (1024 ** 2)
        elif "Power (W)" in rest:
            try:
                entry["power_w"] = float(rest.split(":")[-1].strip())
            except ValueError:
                pass
    return snapshot


def _amd_gpu_stats(handles):
    """Return one stats dict per AMD GPU via amdsmi, falling back to a single
    `rocm-smi` CLI call (see _amd_cli_snapshot) for busy%/power/VRAM on hosts
    where the corresponding Python API call is blocked.

    amdsmi_get_gpu_vram_usage()'s vram_used is already in MB, not bytes — this
    was confirmed by cross-checking against a reference script whose own sample
    output (a physically-impossible sub-1-MB total VRAM) was only explicable by
    an accidental double byte-to-MB conversion. No division is applied here.

    The CLI fallback is lazy and shared across every GPU in this call: it only
    runs if at least one Python API field came back missing, and at most once
    per call regardless of GPU count, so a working host never pays for it and
    a multi-GPU host pays for it once, not once per GPU.
    """
    import amdsmi

    stats = []
    cli_snapshot = None

    for idx, handle in enumerate(handles):
        busy_pct = power_w = vram_used_mb = None

        try:
            activity = amdsmi.amdsmi_get_gpu_activity(handle)
            raw = (
                activity.get("gfx_activity") if isinstance(activity, dict)
                else getattr(activity, "gfx_activity", None)
            )
            if raw is not None:
                busy_pct = float(raw)
        except Exception:
            pass

        try:
            vram_info = amdsmi.amdsmi_get_gpu_vram_usage(handle)
            raw = (
                vram_info.get("vram_used") if isinstance(vram_info, dict)
                else getattr(vram_info, "vram_used", None)
            )
            if raw is not None:
                vram_used_mb = float(raw)
        except Exception:
            pass

        try:
            metrics = amdsmi.amdsmi_get_gpu_metrics_info(handle)
            raw = (
                metrics.get("current_socket_power") if isinstance(metrics, dict)
                else getattr(metrics, "current_socket_power", None)
            )
            if raw not in (None, "N/A"):
                power_w = float(raw)
        except Exception:
            pass

        if busy_pct is None or power_w is None or vram_used_mb is None:
            if cli_snapshot is None:
                cli_snapshot = _amd_cli_snapshot()
            cli_entry = cli_snapshot.get(idx)
            if cli_entry:
                if busy_pct is None:
                    busy_pct = cli_entry.get("busy_pct")
                if power_w is None:
                    power_w = cli_entry.get("power_w")
                if vram_used_mb is None:
                    vram_used_mb = cli_entry.get("vram_used_mb")

        stats.append({"busy_pct": busy_pct, "power_w": power_w, "vram_used_mb": vram_used_mb})
    return stats


def _nvidia_init():
    """Initialize NVML and return its GPU handles, or None if pynvml is not
    installed or no NVIDIA GPU is present."""
    try:
        import pynvml

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count <= 0:
            pynvml.nvmlShutdown()
            return None
        return [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
    except Exception:
        return None


def _nvidia_shutdown(handles):
    try:
        import pynvml

        pynvml.nvmlShutdown()
    except Exception:
        pass


def _nvidia_gpu_stats(handles):
    """Return one stats dict per NVIDIA GPU via NVML.

    Unlike amdsmi, NVML's units are unambiguous and well documented: memory in
    bytes, power in milliwatts — both converted here to the MB/W the CSV uses.
    """
    import pynvml

    stats = []
    for handle in handles:
        busy_pct = power_w = vram_used_mb = None

        try:
            busy_pct = float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        except Exception:
            pass

        try:
            vram_used_mb = pynvml.nvmlDeviceGetMemoryInfo(handle).used / (1024 ** 2)
        except Exception:
            pass

        try:
            power_w = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
        except Exception:
            pass

        stats.append({"busy_pct": busy_pct, "power_w": power_w, "vram_used_mb": vram_used_mb})
    return stats


def _detect_gpu_vendor():
    """Return (vendor, handles) for the GPU backend this process will use.

    Honors HERMES_GPU_VENDOR ("amd"/"nvidia") when set to one of those values;
    otherwise auto-detects in the order AMD -> NVIDIA (the same relative order
    gpu_monitor.py's create_monitor() uses), returning the first vendor that
    yields a working handle. (None, None) means no GPU signal is available for
    this host, and gpu_system_wide.csv rows will read 0.0.
    """
    forced = os.environ.get("HERMES_GPU_VENDOR", "auto").strip().lower()
    if forced == "amd":
        handles = _amd_init()
        return ("amd", handles) if handles else (None, None)
    if forced == "nvidia":
        handles = _nvidia_init()
        return ("nvidia", handles) if handles else (None, None)

    handles = _amd_init()
    if handles:
        return "amd", handles
    handles = _nvidia_init()
    if handles:
        return "nvidia", handles
    return None, None


def _query_gpus(vendor, handles):
    """Dispatch to the per-GPU stats function for the detected vendor. Returns
    an empty list when no vendor was detected."""
    if vendor == "amd":
        return _amd_gpu_stats(handles)
    if vendor == "nvidia":
        return _nvidia_gpu_stats(handles)
    return []


def _shutdown_gpu_vendor(vendor, handles):
    """Release the detected vendor's SDK handles, if any. A vendor with no
    persistent handle to release is a no-op."""
    if vendor == "amd":
        _amd_shutdown(handles)
    elif vendor == "nvidia":
        _nvidia_shutdown(handles)


def _aggregate_gpu(per_gpu):
    """Combine every GPU's reading into the one row gpu_system_wide.csv carries.

    busy_pct is a utilization ratio, so multiple GPUs are averaged. power_w and
    vram_used_mb are physical quantities (total draw, total memory in use), so
    they are summed — the host's real load and memory pressure, not a per-GPU
    average of them. Returns (gfx, power, vram): the same tuple shape the CSV
    writer has always expected, so no downstream reader needs to change.
    Per-GPU None readings are excluded from their aggregate; if every GPU is
    missing a metric, that aggregate is 0.0.
    """
    if not per_gpu:
        return 0.0, 0.0, 0.0
    busy_vals = [g["busy_pct"] for g in per_gpu if g.get("busy_pct") is not None]
    power_vals = [g["power_w"] for g in per_gpu if g.get("power_w") is not None]
    vram_vals = [g["vram_used_mb"] for g in per_gpu if g.get("vram_used_mb") is not None]
    gfx = sum(busy_vals) / len(busy_vals) if busy_vals else 0.0
    power = sum(power_vals) if power_vals else 0.0
    vram = sum(vram_vals) if vram_vals else 0.0
    return gfx, power, vram


def _cpu_seconds(proc) -> float:
    """Return the cumulative CPU seconds (user + system) consumed by a process.

    Reaped-child time (``children_user`` / ``children_system``) is intentionally
    excluded: those counters credit a child's entire lifetime to the parent at
    reap time, dumping it into a single sample window and producing spurious
    spikes. Live descendants are instead measured directly, tick by tick.
    """
    t = proc.cpu_times()
    return t.user + t.system


def _clamp_pct(value: float) -> float:
    """Clamp a computed percentage to the documented 0..100 range.

    sample_cpu's per-process counters are read sequentially, one process after
    another, following a single wall-clock timestamp taken before that loop
    starts. Under load with several live descendants, the small delay between
    the timestamp and a given process's read can let it accumulate slightly
    more CPU time than the nominal elapsed window accounts for, which can push
    the raw ratio a fraction of a percent past 100. This keeps the value inside
    the range every caller (and the chart that fixes its y-axis to 0-100)
    assumes.
    """
    return min(max(value, 0.0), 100.0)


def sample_cpu(state: dict, root, contributors=None):
    """Return the machine-wide CPU% of ``root`` plus its live descendants.

    The value is normalized against total machine capacity, so 100% means every
    logical core is fully utilized by the measured process tree and a single
    fully-busy core reads as ``100 / ncpu`` percent. This keeps CPU on the same
    0..100 scale as GPU busy%, so the two signals are directly comparable.

    Utilization is computed the same way ``top``, ``htop`` and ``psutil`` do it:
    the OS exposes only a cumulative per-process CPU-time counter, so the CPU
    time spent during a tick is obtained by differencing that counter between
    two samples and dividing by the elapsed wall-clock time. The elapsed time is
    measured, not assumed from the poll interval, because the true spacing
    between samples includes scrape, write and scheduling latency and therefore
    drifts above the nominal interval. Dividing the summed CPU-time delta by
    ``ncpu * elapsed`` yields a fraction bounded to 0..100.

    Both the wall clock and every process counter are read once per tick, in a
    single pass captured immediately after the timestamp, and the same snapshot
    is carried over as the next tick's baseline. Reading the timestamp before
    that pass keeps the CPU-time window and the elapsed window closely aligned
    across every process, so the result stays close to 0..100 in practice; the
    returned value is still clamped (see _clamp_pct) since the per-process reads
    are sequential and a fraction-of-a-percent overshoot is possible under load.

    Args:
        state: Cross-tick cache holding ``"prev"`` (a ``pid -> cpu_seconds``
            snapshot from the previous tick) and ``"wall"`` (its timestamp).
            The first call only records the baseline and returns ``0.0``.
        root: The root ``psutil.Process`` (the hermes process).
        contributors: Optional list; when provided, receives a
            ``(pid, cpu_pct, name, cmdline)`` tuple for each process that
            consumed CPU during the interval (used by the debug mode).

    Returns:
        CPU utilization as a percentage in the range 0..100, or ``0.0`` when
        psutil is unavailable, the root has exited, or on the priming call.
    """
    if psutil is None or root is None:
        return 0.0

    # Enumerate the process tree. The poller runs as a child of hermes (spawned
    # via Popen) and therefore appears in children(recursive=True); exclude its
    # own PID so the poller's sampling overhead is not attributed to hermes.
    self_pid = os.getpid()
    try:
        current = {root.pid: root}
        for child in root.children(recursive=True):
            if child.pid == self_pid:
                continue
            current[child.pid] = child
    except psutil.NoSuchProcess:
        return 0.0

    # Take a single snapshot per tick: the wall clock followed immediately by
    # every live process's cumulative CPU-time counter. Keeping both reads in one
    # consistent pass is what keeps the elapsed and CPU-time windows aligned.
    now = time.time()
    snapshot = {}
    for pid, proc in current.items():
        try:
            snapshot[pid] = _cpu_seconds(proc)
        except psutil.NoSuchProcess:
            pass

    prev = state.get("prev")
    last_wall = state.get("wall")
    state["prev"] = snapshot
    state["wall"] = now

    # First call establishes the baseline; a delta needs two snapshots.
    if prev is None or last_wall is None:
        return 0.0

    elapsed = max(now - last_wall, 1e-6)
    ncpu = psutil.cpu_count(logical=True) or 1

    total_delta = 0.0
    for pid, cur in snapshot.items():
        if pid not in prev:
            continue  # newly seen this tick; no prior counter to difference
        delta = cur - prev[pid]
        if delta <= 0:
            continue
        total_delta += delta
        if contributors is not None:
            name = cmd = ""
            try:
                proc = current[pid]
                name = proc.name()
                cmd = " ".join(proc.cmdline())
            except Exception:
                pass
            contributors.append(
                (pid, round(_clamp_pct(delta / elapsed / ncpu * 100), 1), name, cmd)
            )

    return round(_clamp_pct(total_delta / elapsed / ncpu * 100), 2)


def sample_system_cpu() -> float:
    """Return whole-host CPU% since the previous call (0..100, already
    normalized so 100 = every logical core fully busy). Prime once before the
    loop so the first real reading covers a real interval rather than 0. Unlike
    ``sample_cpu`` this counts EVERYTHING on the box — the OS and every other
    process running on it, including the poller itself — so it is a total-load
    backdrop, not per-tool attributable."""
    if psutil is None:
        return 0.0
    try:
        return psutil.cpu_percent(interval=None)
    except Exception:
        return 0.0


def _open_append(path: str, header):
    """Open ``path`` in append mode (so ``hermes -r`` accumulates rather than
    overwriting) and write ``header`` only when the file is new/empty."""
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    fh = open(path, "a", newline="")
    writer = csv.writer(fh)
    if is_new:
        writer.writerow(header)
        fh.flush()
    return fh, writer


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("usage: metrics_poller.py <out_dir> <interval> <hermes_pid>\n")
        return 1

    out_dir = sys.argv[1]
    try:
        interval = float(sys.argv[2])
    except ValueError:
        interval = 0.1
    try:
        hermes_pid = int(sys.argv[3])
    except ValueError:
        hermes_pid = None

    # Each signal is sampled only when its flag is set (inherited from hermes).
    want_cpu = _flag("HERMES_CPU_TRACE")
    want_cpu_system = _flag("HERMES_CPU_SYSTEM_WIDE")
    want_gpu = _flag("HERMES_GPU_SYSTEM_WIDE")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    root = None
    if psutil is not None and hermes_pid is not None:
        try:
            root = psutil.Process(hermes_pid)
        except Exception:
            root = None
    cpu_state = {}

    gpu_vendor = gpu_handles = None
    if want_gpu:
        gpu_vendor, gpu_handles = _detect_gpu_vendor()

    # Open one writer per enabled signal. If nothing is enabled there is nothing
    # to do — exit cleanly (tool_profiler shouldn't have launched us, but guard).
    handles = []
    cpu_writer = cpu_system_writer = gpu_writer = None
    if want_cpu:
        fh, cpu_writer = _open_append(
            os.path.join(out_dir, "cpu_hermes_trace.csv"),
            ["timestamp", "start_time_unix_nano", "cpu_pct"],
        )
        handles.append(fh)
    if want_cpu_system:
        fh, cpu_system_writer = _open_append(
            os.path.join(out_dir, "cpu_system_wide.csv"),
            ["timestamp", "start_time_unix_nano", "cpu_pct"],
        )
        handles.append(fh)
    if want_gpu:
        fh, gpu_writer = _open_append(
            os.path.join(out_dir, "gpu_system_wide.csv"),
            ["timestamp", "start_time_unix_nano", "gfx_busy_pct", "power_w", "vram_mb"],
        )
        handles.append(fh)

    if not handles:
        return 0

    # Optional per-PID attribution of non-zero hermes-tree CPU readings. Set
    # HERMES_CPU_DEBUG=true to record which process/command accounts for each reading.
    debug_cpu = want_cpu and os.environ.get("HERMES_CPU_DEBUG", "").lower() in ("1", "true", "yes")
    debug_file = None
    debug_writer = None
    if debug_cpu:
        debug_path = os.path.join(out_dir, "cpu_debug.csv")
        debug_file, debug_writer = _open_append(
            debug_path, ["timestamp", "pid", "cpu_pct", "name", "cmdline"]
        )

    # Prime the CPU delta baselines before the first real sample.
    if want_cpu:
        sample_cpu(cpu_state, root)
    if want_cpu_system:
        sample_system_cpu()

    try:
        while _RUNNING:
            wall = time.time()
            ts = datetime.fromtimestamp(wall).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            # Absolute epoch nanoseconds, so the timelines and the trace
            # waterfall can be plotted on one shared wall-clock axis regardless
            # of the host's local time zone.
            ts_ns = int(wall * 1_000_000_000)

            if want_cpu:
                contributors = [] if debug_cpu else None
                cpu = sample_cpu(cpu_state, root, contributors)
                cpu_writer.writerow([ts, ts_ns, cpu])
                if debug_writer is not None and contributors:
                    for pid, c, name, cmd in contributors:
                        debug_writer.writerow([ts, pid, c, name, cmd])
                    debug_file.flush()

            if want_cpu_system:
                cpu_system_writer.writerow([ts, ts_ns, sample_system_cpu()])

            if want_gpu:
                gfx, power, vram = _aggregate_gpu(_query_gpus(gpu_vendor, gpu_handles))
                gpu_writer.writerow([ts, ts_ns, gfx, power, vram])

            for fh in handles:
                fh.flush()

            time.sleep(interval)
    finally:
        if want_gpu:
            _shutdown_gpu_vendor(gpu_vendor, gpu_handles)
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass
        if debug_file is not None:
            debug_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
