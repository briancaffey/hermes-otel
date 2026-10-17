"""Host metrics (CPU / GPU) wired into the plugin: :class:`HostMetrics` owns
the sampler's lifecycle, the mirror into the live store and the observable
instruments on the meter. The plugin holds one when ``config.host_metrics``
is on (#506). The instrument names are ``instruments.HOST_METRIC_NAMES``.
"""

from __future__ import annotations

from typing import Any, Optional

from .debug_utils import logger


class HostMetrics:
    """The sampler and its wiring, created (not started) from the config.

    The sampler's readings are mirrored into the live store when
    ``live_active`` (so the built-in dashboard sees them) and read by the
    observable instruments registered on ``meter`` (none without a meter:
    the live-only pipeline). ``start()`` runs the sampler thread after init,
    ``stop()`` ends it at shutdown.
    """

    def __init__(self, config: Any, *, meter: Any = None, live_active: bool = False) -> None:
        from .host_metrics import HostMetricsSampler

        self.config = config
        self.live_active = live_active
        self.sampler = HostMetricsSampler(
            interval_ms=config.host_metrics_interval_ms,
            gpu_vendor=config.host_metrics_gpu,
            on_sample=self.mirror_sample,
        )
        if meter is not None:
            self._create_instruments(meter)

    @property
    def running(self) -> bool:
        return self.sampler.running

    def start(self) -> None:
        sampler = self.sampler
        if not sampler.available:
            logger.warning(
                "[hermes-otel] ⚠ host_metrics=true but psutil is not importable; "
                "host metrics disabled"
            )
            return
        if sampler.start():
            gpu = sampler.gpu_vendor_detected or "none"
            logger.info(
                f"[hermes-otel] ✓ Host metrics sampler on "
                f"(every {self.config.host_metrics_interval_ms} ms, gpu={gpu})"
            )

    def stop(self) -> None:
        """Stop the sampler thread and release any GPU SDK (idempotent)."""
        try:
            self.sampler.stop()
        except Exception:  # pragma: no cover — shutdown must never raise
            pass

    def window(self, started_at: float, ended_at: float) -> Optional[Any]:
        """The sampler's utilization stats over a ``perf_counter`` window, or
        ``None`` while the sampler is not running (the hooks' per-tool attributes)."""
        if not self.sampler.running:
            return None
        return self.sampler.window(started_at, ended_at)

    def mirror_sample(self, sample: Any) -> None:
        """Push each reading into the live store so the built-in dashboard sees it."""
        if not self.live_active:
            return
        try:
            from .live_store_registry import get_live_store

            store = get_live_store()
            if store is None:
                return
            ts = sample.wall_ns
            store.add_metric("process.cpu.utilization", sample.process_cpu_total, {}, ts)
            store.add_metric("system.cpu.utilization", sample.system_cpu_total, {}, ts)
            gpu = sample.gpu_utilization
            if gpu is not None:
                store.add_metric("hw.gpu.utilization", gpu, {}, ts)
        except Exception:  # pragma: no cover — never break the sampler
            pass

    def _create_instruments(self, meter: Any) -> None:
        """Register the host/GPU observable instruments (semantic-convention names).

        Each callback reads the sampler's latest reading; the SDK invokes them
        on every collection, so the export cadence is ``flush_interval_ms``
        while the sampler keeps its own finer cadence for the per-tool window
        attributes.
        """
        try:
            from opentelemetry.metrics import Observation
        except ImportError:  # pragma: no cover
            return
        sampler = self.sampler

        def _cpu(getter):
            def callback(_options):
                latest = sampler.latest()
                if latest is None:
                    return []
                modes = getter(latest)
                return [Observation(v, {"cpu.mode": mode}) for mode, v in modes.items()]

            return callback

        def _gpu(field, extra=None):
            def callback(_options):
                latest = sampler.latest()
                if latest is None:
                    return []
                vendor = sampler.gpu_vendor_detected or "unknown"
                out = []
                for g in latest.gpus:
                    value = getattr(g, field)
                    if value is None:
                        continue
                    attrs = {"hw.id": f"gpu{g.index}", "hw.vendor": vendor}
                    if extra:
                        attrs.update(extra)
                    out.append(Observation(value, attrs))
                return out

            return callback

        meter.create_observable_gauge(
            "process.cpu.utilization",
            callbacks=[_cpu(lambda s: s.process_cpu)],
            unit="1",
            description="CPU utilization of the Hermes process tree, by mode (1 = all cores busy)",
        )
        meter.create_observable_gauge(
            "system.cpu.utilization",
            callbacks=[_cpu(lambda s: s.system_cpu)],
            unit="1",
            description="Whole-host CPU utilization, by mode",
        )
        meter.create_observable_gauge(
            "hw.gpu.utilization",
            callbacks=[_gpu("utilization")],
            unit="1",
            description="GPU busy ratio per device",
        )
        meter.create_observable_up_down_counter(
            "hw.gpu.memory.usage",
            callbacks=[_gpu("memory_bytes")],
            unit="By",
            description="GPU memory in use per device",
        )
        meter.create_observable_gauge(
            "hw.power",
            callbacks=[_gpu("power_w", {"hw.type": "gpu"})],
            unit="W",
            description="GPU power draw per device",
        )
