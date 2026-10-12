"""In-process host metrics sampler (CPU / GPU) for hermes-otel.

Answers "what was the machine doing while the agent ran?" the OpenTelemetry way:
a single sampler per Hermes process takes one reading per ``interval_ms`` of

* the Hermes process tree's CPU utilization (the agent plus every child it
  spawned — tool subprocesses, MCP servers it launched — but *not* an external
  inference server, which is not a child and is therefore excluded by
  construction);
* the whole host's CPU utilization, as context;
* every AMD/NVIDIA GPU's busy ratio, memory in use, and power draw, through
  :mod:`hermes_otel.gpu_probe`.

Readings land in a bounded ring buffer. The tracer exposes the latest reading
through OTel observable instruments (``process.cpu.utilization``,
``system.cpu.utilization``, ``hw.gpu.utilization``, ``hw.gpu.memory.usage``,
``hw.power``), so they reach every backend that receives metrics, and the tool
hooks slice the ring by a tool call's window to stamp average / peak utilization
on the tool span. Nothing is written to disk.

Design notes
------------
* Utilization is computed the way ``top`` / ``psutil`` do it: the OS exposes a
  cumulative per-process CPU-time counter, so the CPU time spent in a tick is
  the difference of that counter between two samples divided by the measured
  wall-clock elapsed time. Dividing by the logical core count keeps every value
  on the same 0..1 scale (1.0 = every core fully busy), which is what the
  semantic conventions specify for ``*.cpu.utilization`` and what makes CPU
  directly comparable with GPU busy ratio.
* Reaped-child time (``children_user`` / ``children_system``) is deliberately
  excluded: those counters credit a child's entire lifetime to the parent at
  reap time and would show up as a spurious spike. Live descendants are
  measured directly, tick by tick.
* The sampler is a daemon thread, started lazily by the tracer and stopped at
  tracer shutdown. The plugin already runs the SDK's exporter threads, so this
  adds no new kind of resource; there is one sampler per process, never per
  session, so a long-lived gateway pays a fixed cost.
* ``psutil`` is imported lazily and the sampler degrades to "unavailable"
  without it; the GPU SDKs are optional in the same way (see gpu_probe).

Timestamps use ``time.perf_counter()`` so windows line up with the tool
timings the hooks already keep on ``SessionState`` (same clock).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from .debug_utils import debug_log
from .gpu_probe import GpuProbe

try:  # optional at runtime; ships with hermes-agent
    import psutil
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True)
class GpuReading:
    """One device's reading in semantic-convention units (``None`` = unknown)."""

    index: int
    utilization: Optional[float]  # 0..1
    memory_bytes: Optional[float]
    power_w: Optional[float]


@dataclass(frozen=True)
class Sample:
    at: float  # time.perf_counter() seconds
    wall_ns: int  # epoch nanoseconds (for the live store)
    process_cpu: Dict[str, float]  # {"user": 0..1, "system": 0..1}
    system_cpu: Dict[str, float]  # {"user": 0..1, "system": 0..1}
    gpus: List[GpuReading] = field(default_factory=list)

    @property
    def process_cpu_total(self) -> float:
        return self.process_cpu.get("user", 0.0) + self.process_cpu.get("system", 0.0)

    @property
    def system_cpu_total(self) -> float:
        return self.system_cpu.get("user", 0.0) + self.system_cpu.get("system", 0.0)

    @property
    def gpu_utilization(self) -> Optional[float]:
        """Mean busy ratio across devices that reported one (``None`` if none did)."""
        vals = [g.utilization for g in self.gpus if g.utilization is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass(frozen=True)
class WindowStats:
    """Average / peak utilization over the samples inside a time window."""

    samples: int
    cpu_avg: float
    cpu_peak: float
    gpu_avg: Optional[float]
    gpu_peak: Optional[float]


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _gpu_reading(index: int, raw: Dict[str, Any]) -> GpuReading:
    """Convert a gpu_probe per-device dict (vendor units) to semconv units."""
    busy = raw.get("busy_pct")
    vram_mb = raw.get("vram_used_mb")
    power = raw.get("power_w")
    return GpuReading(
        index=index,
        utilization=_clamp(float(busy) / 100.0) if busy is not None else None,
        memory_bytes=float(vram_mb) * 1024 * 1024 if vram_mb is not None else None,
        power_w=float(power) if power is not None else None,
    )


class HostMetricsSampler:
    """Periodic CPU/GPU sampler with a bounded in-memory ring of readings."""

    def __init__(
        self,
        interval_ms: int = 1000,
        gpu_vendor: str = "auto",
        pid: Optional[int] = None,
        on_sample: Optional[Callable[[Sample], None]] = None,
        max_samples: Optional[int] = None,
    ) -> None:
        self.interval_s = max(int(interval_ms), 50) / 1000.0
        self.gpu_vendor = gpu_vendor
        self._pid = pid or os.getpid()
        self._on_sample = on_sample
        # Keep at least ten minutes of readings, capped so a very fast interval
        # can't grow the ring without bound (36 000 samples ≈ 1 h at 100 ms).
        if max_samples is None:
            max_samples = min(max(int(600 / self.interval_s), 600), 36_000)
        self._ring: Deque[Sample] = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._probe = GpuProbe(gpu_vendor)
        self._root = None
        self._prev_proc: Optional[Dict[int, Any]] = None
        self._prev_at: Optional[float] = None
        self._ncpu = 1

    # ── Availability / lifecycle ─────────────────────────────────────────

    @property
    def available(self) -> bool:
        return psutil is not None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def gpu_vendor_detected(self) -> Optional[str]:
        return self._probe.vendor

    def start(self) -> bool:
        """Start the sampler thread (idempotent). Returns False when psutil is missing."""
        if not self.available:
            debug_log("host metrics: psutil unavailable; sampler not started")
            return False
        if self.running:
            return True
        self._open()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="hermes-otel-host-metrics", daemon=True
        )
        self._thread.start()
        debug_log(
            f"host metrics: sampler started (interval={self.interval_s}s, "
            f"gpu={self._probe.vendor or 'none'})"
        )
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the sampler thread and release the GPU SDK (idempotent)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None
        self._probe.close()

    def _open(self) -> None:
        try:
            self._root = psutil.Process(self._pid)
            self._ncpu = psutil.cpu_count(logical=True) or 1
        except Exception as e:  # pragma: no cover — defensive
            debug_log(f"host metrics: cannot attach to pid {self._pid}: {e}")
            self._root = None
        try:
            self._probe.open()
        except Exception as e:  # pragma: no cover — probe already swallows
            debug_log(f"host metrics: gpu probe failed: {e}")
        # Prime the delta baselines so the first real sample covers a real interval.
        self._process_cpu()
        self._system_cpu()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as e:  # never let the sampler die on a bad tick
                debug_log(f"host metrics: sample failed: {e}")
            self._stop.wait(self.interval_s)

    # ── Sampling ─────────────────────────────────────────────────────────

    def sample_once(self) -> Sample:
        """Take one reading, append it to the ring, notify ``on_sample``."""
        sample = Sample(
            at=time.perf_counter(),
            wall_ns=time.time_ns(),
            process_cpu=self._process_cpu(),
            system_cpu=self._system_cpu(),
            gpus=[_gpu_reading(i, raw) for i, raw in enumerate(self._probe.read())],
        )
        with self._lock:
            self._ring.append(sample)
        if self._on_sample is not None:
            try:
                self._on_sample(sample)
            except Exception:  # pragma: no cover — mirror must never break sampling
                pass
        return sample

    def _process_cpu(self) -> Dict[str, float]:
        """Process-tree CPU utilization split by mode, as fractions of all cores."""
        if psutil is None or self._root is None:
            return {"user": 0.0, "system": 0.0}
        now = time.perf_counter()
        snapshot: Dict[int, Any] = {}
        try:
            procs = [self._root] + list(self._root.children(recursive=True))
        except psutil.NoSuchProcess:
            return {"user": 0.0, "system": 0.0}
        for proc in procs:
            try:
                t = proc.cpu_times()
                snapshot[proc.pid] = (t.user, t.system)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        prev, prev_at = self._prev_proc, self._prev_at
        self._prev_proc, self._prev_at = snapshot, now
        if prev is None or prev_at is None:
            return {"user": 0.0, "system": 0.0}
        elapsed = max(now - prev_at, 1e-6)
        user = system = 0.0
        for pid, (u, s) in snapshot.items():
            if pid not in prev:
                continue  # newly seen this tick; no prior counter to difference
            pu, ps = prev[pid]
            user += max(u - pu, 0.0)
            system += max(s - ps, 0.0)
        scale = elapsed * self._ncpu
        return {"user": _clamp(user / scale), "system": _clamp(system / scale)}

    def _system_cpu(self) -> Dict[str, float]:
        """Whole-host CPU utilization split by mode (psutil keeps the baseline)."""
        if psutil is None:
            return {"user": 0.0, "system": 0.0}
        try:
            pct = psutil.cpu_times_percent(interval=None)
            return {
                "user": _clamp(float(getattr(pct, "user", 0.0)) / 100.0),
                "system": _clamp(float(getattr(pct, "system", 0.0)) / 100.0),
            }
        except Exception:
            return {"user": 0.0, "system": 0.0}

    # ── Reads ────────────────────────────────────────────────────────────

    def latest(self) -> Optional[Sample]:
        with self._lock:
            return self._ring[-1] if self._ring else None

    def window(self, start_at: float, end_at: float) -> Optional[WindowStats]:
        """Average / peak utilization over samples with ``start_at <= at <= end_at``.

        Walks the ring from the newest reading backwards and stops at the first
        one older than ``start_at``, so the cost is proportional to the window,
        not to the ring. Returns ``None`` when no sample fell inside the window
        (a tool shorter than the sampling interval).
        """
        with self._lock:
            picked: List[Sample] = []
            for s in reversed(self._ring):
                if s.at > end_at:
                    continue
                if s.at < start_at:
                    break
                picked.append(s)
        if not picked:
            return None
        cpu = [s.process_cpu_total for s in picked]
        gpu = [s.gpu_utilization for s in picked if s.gpu_utilization is not None]
        return WindowStats(
            samples=len(picked),
            cpu_avg=sum(cpu) / len(cpu),
            cpu_peak=max(cpu),
            gpu_avg=sum(gpu) / len(gpu) if gpu else None,
            gpu_peak=max(gpu) if gpu else None,
        )
