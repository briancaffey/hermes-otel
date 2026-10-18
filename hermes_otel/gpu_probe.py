"""GPU probing for the host-metrics sampler (AMD via amdsmi, NVIDIA via pynvml).

Contributed in PR #66 (originally the GPU half of a standalone ``metrics_poller``
subprocess); now called in-process by :mod:`hermes_otel.host_metrics` on each
sampling tick. Nothing here imports OpenTelemetry, and both vendor SDKs are
imported lazily so the plugin loads fine on hosts without a GPU or SDK.

Each per-GPU reading is a dict ``{"busy_pct", "power_w", "vram_used_mb"}`` where
a ``None`` field means that reading could not be obtained on this tick; the
sampler leaves ``None`` out (an unknown, never a zero). The units are the vendor
SDKs' natural ones; the sampler converts to OTel semantic-convention units
(utilization as a 0..1 ratio, memory in bytes, power in watts) at the metrics
boundary and reports every device as its own ``hw.id`` series.

Vendor auto-detection tries AMD first, then NVIDIA, and uses the first SDK that
returns a working handle. A ``rocm-smi`` CLI fallback covers SR-IOV VF GPU
partitions where the amdsmi Python API raises for busy%/power/VRAM.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional


class GpuProbe:
    """Stateful wrapper around the vendor functions: open once, read per tick.

    ``open()`` performs vendor detection and keeps the SDK handles; ``read()``
    returns one dict per GPU (empty when no vendor is available); ``close()``
    releases the SDK. All three are safe to call repeatedly.
    """

    def __init__(self, vendor: str = "auto") -> None:
        self.requested_vendor = vendor
        self.vendor: Optional[str] = None
        self._handles: Any = None
        self._opened = False

    def open(self) -> Optional[str]:
        if not self._opened:
            self.vendor, self._handles = _detect_gpu_vendor(self.requested_vendor)
            self._opened = True
        return self.vendor

    @property
    def available(self) -> bool:
        return self._opened and self.vendor is not None

    def read(self) -> List[Dict[str, Optional[float]]]:
        if not self.available:
            return []
        try:
            return _query_gpus(self.vendor, self._handles)
        except Exception:
            return []

    def close(self) -> None:
        if self.available:
            _shutdown_gpu_vendor(self.vendor, self._handles)
        self.vendor, self._handles, self._opened = None, None, False


# --------------------------------------------------------------------------- #
# GPU vendor backends. Each returns a list of one dict per GPU:
#   {"busy_pct": float|None, "power_w": float|None, "vram_used_mb": float|None}
# A None field means that reading could not be obtained for that GPU on this
# tick; the sampler leaves it out rather than treating it as zero.
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
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return {}

    snapshot = {}
    for line in res.stdout.splitlines():
        m = re.match(r"GPU\[(\d+)\]\s*:\s*(.+)", line.strip())
        if not m:
            continue
        idx, rest = int(m.group(1)), m.group(2)
        entry = snapshot.setdefault(idx, {"busy_pct": None, "power_w": None, "vram_used_mb": None})
        if "GPU use (%)" in rest:
            try:
                entry["busy_pct"] = float(rest.split(":")[-1].strip())
            except ValueError:
                pass
        elif "VRAM Total Used Memory" in rest:
            digits = "".join(filter(str.isdigit, rest))
            if digits:
                entry["vram_used_mb"] = float(digits) / (1024**2)
        elif "Power (W)" in rest:
            try:
                entry["power_w"] = float(rest.split(":")[-1].strip())
            except ValueError:
                pass
    return snapshot


def _field(obj, name):
    """One field of an amdsmi answer, which is a dict on some SDK versions and
    an object with attributes on others."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


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
            raw = _field(amdsmi.amdsmi_get_gpu_activity(handle), "gfx_activity")
            if raw is not None:
                busy_pct = float(raw)
        except Exception:
            pass

        try:
            raw = _field(amdsmi.amdsmi_get_gpu_vram_usage(handle), "vram_used")
            if raw is not None:
                vram_used_mb = float(raw)
        except Exception:
            pass

        try:
            raw = _field(amdsmi.amdsmi_get_gpu_metrics_info(handle), "current_socket_power")
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
    bytes, power in milliwatts — both converted here to the MB / W of the reading dict.
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
            vram_used_mb = pynvml.nvmlDeviceGetMemoryInfo(handle).used / (1024**2)
        except Exception:
            pass

        try:
            power_w = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
        except Exception:
            pass

        stats.append({"busy_pct": busy_pct, "power_w": power_w, "vram_used_mb": vram_used_mb})
    return stats


def _detect_gpu_vendor(forced: str = "auto"):
    """Return (vendor, handles) for the GPU backend this process will use.

    ``forced`` is the ``host_metrics_gpu`` config value: ``"amd"`` / ``"nvidia"``
    select that SDK only; ``"off"`` skips detection; anything else (``"auto"``)
    tries AMD then NVIDIA and returns the first vendor that yields a working
    handle. ``(None, None)`` means no GPU signal is available on this host.
    """
    forced = (forced or "auto").strip().lower()
    if forced == "off":
        return None, None
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
