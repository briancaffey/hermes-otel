"""Single source of truth for the advanced-profiling feature flags.

Every toggle shares the ``HERMES_`` prefix so the whole surface is easy to grep
and document, and every one is opt-in (off unless explicitly set to a truthy
value). ``metrics_poller.py`` runs as a standalone subprocess and cannot import
this module, so it reads the same variable *names* directly — the names here are
the contract, not this parser.

Feature flags (each writes one CSV under <output_dir>/<session_id>/):

  HERMES_CPU_TRACE           hermes process-tree CPU%      -> cpu_hermes_trace.csv
  HERMES_CPU_SYSTEM_WIDE     whole-host CPU% (context)     -> cpu_system_wide.csv
  HERMES_GPU_SYSTEM_WIDE     system-wide GPU (vendor SDK)   -> gpu_system_wide.csv
  HERMES_TOOL_TRACE          per-tool execution breakdown   -> tool_execution.csv

Supporting values (with defaults):

  HERMES_GPU_VENDOR            amd / nvidia                (auto-detect)
  HERMES_POLL_INTERVAL         sampling interval seconds  (0.1)
  HERMES_PROFILING_OUTPUT_DIR  base output directory      (./outputs)

Behaviour:

  HERMES_PLOT_PROFILING        render the CSVs to PNGs at process exit (off)

Attribution note: ``cpu_hermes_trace`` is the hermes process plus its children,
so it is *attributable* to hermes/tool work and is what the per-tool breakdown
slices. ``cpu_system_wide`` is the whole host (the OS and every other process
running on it, including the poller itself) — useful as a total-load backdrop
but NOT attributable per tool, which is why it lives in its own file and is
never used for tool slicing.
"""

import os

_TRUE = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def cpu_trace() -> bool:
    return _flag("HERMES_CPU_TRACE")


def cpu_system_wide() -> bool:
    return _flag("HERMES_CPU_SYSTEM_WIDE")


def gpu_system_wide() -> bool:
    return _flag("HERMES_GPU_SYSTEM_WIDE")


def tool_trace() -> bool:
    return _flag("HERMES_TOOL_TRACE")


def plot_profiling() -> bool:
    """When set, the CSVs are rendered to PNGs at process exit by a separate
    plot_profiling subprocess. It runs only after the poller has stopped, so it
    never adds samples and cannot affect any timing or CPU/GPU measurement.
    Requires matplotlib in the environment that runs hermes."""
    return _flag("HERMES_PLOT_PROFILING")


def gpu_vendor() -> str:
    """Force a specific GPU vendor ("amd" or "nvidia") instead of auto-detecting.
    Defaults to "auto", which tries AMD, then NVIDIA, in that order (see
    metrics_poller.py)."""
    return os.environ.get("HERMES_GPU_VENDOR", "auto").strip().lower()


def hardware_trace_enabled() -> bool:
    """True when any signal that needs the out-of-process poller is on."""
    return cpu_trace() or cpu_system_wide() or gpu_system_wide()


def any_enabled() -> bool:
    """True when any profiling feature is enabled. Gates the shared per-turn
    counter and the per-session output setup so neither is started for a session
    that has not enabled any feature."""
    return hardware_trace_enabled() or tool_trace()
