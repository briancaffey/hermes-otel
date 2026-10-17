"""Host metrics (CPU / GPU) wired into the plugin: the sampler's lifecycle,
the mirror into the live store and the observable instruments.

Split out of ``tracer.py`` in #405; ``HermesOTelPlugin`` keeps thin methods
that delegate here. The instrument names are ``instruments.HOST_METRIC_NAMES``.
"""

from __future__ import annotations

from typing import Any, Optional

from .debug_utils import logger


def ensure_host_sampler(plugin) -> Optional[Any]:
    """Create (but do not start) the sampler when ``config.host_metrics`` is on."""
    if not plugin.config.host_metrics:
        return None
    if plugin._host_metrics is None:
        from .host_metrics import HostMetricsSampler

        plugin._host_metrics = HostMetricsSampler(
            interval_ms=plugin.config.host_metrics_interval_ms,
            gpu_vendor=plugin.config.host_metrics_gpu,
            on_sample=plugin._mirror_host_sample,
        )
    return plugin._host_metrics


def start_host_metrics(plugin) -> None:
    sampler = plugin._ensure_host_sampler()
    if sampler is None:
        return
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
            f"(every {plugin.config.host_metrics_interval_ms} ms, gpu={gpu})"
        )


def stop_host_metrics(plugin) -> None:
    """Stop the sampler thread and release any GPU SDK (idempotent)."""
    sampler = plugin._host_metrics
    if sampler is not None:
        try:
            sampler.stop()
        except Exception:  # pragma: no cover — shutdown must never raise
            pass


def mirror_host_sample(plugin, sample: Any) -> None:
    """Push each reading into the live store so the built-in dashboard sees it."""
    if not plugin._live_active:
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


def create_host_metric_instruments(plugin) -> None:
    """Register the host/GPU observable instruments (semantic-convention names).

    Each callback reads the sampler's latest reading; the SDK invokes them
    on every collection, so the export cadence is ``flush_interval_ms``
    while the sampler keeps its own finer cadence for the per-tool window
    attributes.
    """
    sampler = plugin._ensure_host_sampler()
    if sampler is None or plugin._meter is None:
        return
    try:
        from opentelemetry.metrics import Observation
    except ImportError:  # pragma: no cover
        return

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

    meter = plugin._meter
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
