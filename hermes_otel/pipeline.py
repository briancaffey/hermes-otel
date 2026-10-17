"""The export pipeline: the Resource, one ``BatchSpanProcessor`` per backend
behind the live-store and MCP-ping-filter processors, one metric reader per
backend that takes metrics, and the logs pipeline.

Split out of ``tracer.py`` in #405. The functions take the plugin instance;
``HermesOTelPlugin`` keeps thin methods that delegate here, so the plugin's
public surface and the test seams (``plugin._init_logs_pipeline(...)``,
``plugin._build_resource(...)``) are unchanged. The OTLP exporter classes are
imported here: tests that stub them patch ``hermes_otel.pipeline``.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from .backends import _ResolvedBackend
from .debug_utils import debug_log, logger
from .helpers import DEFAULT_SERVICE_NAME, named_profile, package_version
from .instruments import (
    _INSTRUMENTS,
    metric_preferred_aggregation,
    metric_temporality_map,
    metric_views,
)
from .pipeline_env import (
    host_arch,
    is_usage_key,
    os_type,
    without_otlp_header_env,
    without_usage_keys,
)
from .plugin_config import content_mode, effective_export_batch_size

try:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

    _OTEL_AVAILABLE = True
except ImportError as e:  # pragma: no cover — plugin is unusable without OTel
    _OTEL_AVAILABLE = False
    logger.warning(
        f"[hermes-otel] OpenTelemetry import error: {e}. Run: pip install "
        "opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http"
    )


# One id per process, generated at import: two Hermes processes exporting to
# the same backend get distinct Resources and therefore distinct series.
_SERVICE_INSTANCE_ID = str(uuid.uuid4())

# Env vars the Hermes kanban dispatcher sets on a worker -> resource attribute.
# Only these three: the dispatcher also exports the DB and workspace paths and
# the claim lock, which are a private layout and a token, not identity.
_KANBAN_ENV_ATTRS = (
    ("HERMES_KANBAN_TASK", "hermes.kanban.task_id"),
    ("HERMES_KANBAN_BOARD", "hermes.kanban.board"),
    ("HERMES_KANBAN_RUN_ID", "hermes.kanban.run_id"),
)


def _set_global_once(api_module: Any, kind: str, provider: Any) -> bool:
    """Install ``provider`` as the OTel global iff no real provider is set yet.

    ``opentelemetry.trace.set_tracer_provider`` / ``metrics.set_meter_provider``
    accept exactly one call per process (a ``Once``); a second call logs an
    SDK warning and keeps the first provider. Returns True when installed.
    """
    try:
        getter = getattr(api_module, f"get_{kind}_provider")
        setter = getattr(api_module, f"set_{kind}_provider")
        current = getter()
        # Unset globals are proxy objects (``ProxyTracerProvider`` in trace,
        # ``_ProxyMeterProvider`` in metrics); anything else is a real provider.
        if not type(current).__name__.lstrip("_").startswith("Proxy"):
            debug_log(f"global {kind} provider already set; keeping ours private")
            return False
        setter(provider)
        return True
    except Exception as e:  # pragma: no cover — never block init on this
        debug_log(f"could not set global {kind} provider: {e}")
        return False


def _attr_value(v: Any) -> Any:
    """Make a span attribute value JSON-friendly for the live store."""
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return v


def _serialize_span(span: Any) -> Dict[str, Any]:
    """Compact dict for the live dashboard — the trace tree + attributes."""
    ctx = span.get_span_context()
    parent = getattr(span, "parent", None)
    start = span.start_time or 0
    end = span.end_time or 0
    try:
        status = span.status.status_code.name  # OK / ERROR / UNSET
    except Exception:
        status = "UNSET"
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent is not None else None,
        "name": span.name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "duration_ms": round((end - start) / 1e6, 3) if (end and start) else None,
        "status": status,
        "attributes": {k: _attr_value(v) for k, v in (span.attributes or {}).items()},
    }


def _is_usage_rollup_span(span: Any) -> bool:
    attrs = getattr(span, "attributes", None) or {}
    return attrs.get("openinference.span.kind") == "AGENT" and any(is_usage_key(k) for k in attrs)


def strip_root_usage(spans: Any) -> Any:
    """``spans`` with every AGENT-kind span rebuilt without its usage attributes.

    Spans that carry no roll-up are passed through as the same objects; the
    sequence keeps its order. Pure, so the per-backend wrapper and its tests
    share it.
    """
    if not _OTEL_AVAILABLE or not any(_is_usage_rollup_span(sp) for sp in spans):
        return spans
    out = []
    for sp in spans:
        if not _is_usage_rollup_span(sp):
            out.append(sp)
            continue
        kept = without_usage_keys(sp.attributes or {})
        out.append(
            ReadableSpan(
                name=sp.name,
                context=sp.get_span_context(),
                parent=sp.parent,
                resource=sp.resource,
                attributes=kept,
                events=sp.events,
                links=sp.links,
                kind=sp.kind,
                status=sp.status,
                start_time=sp.start_time,
                end_time=sp.end_time,
                instrumentation_scope=sp.instrumentation_scope,
            )
        )
    return out


def is_mcp_keepalive_ping(span: Any) -> bool:
    """True for a finished MCP ``ping`` request span that did not fail.

    Matches on the SDK's ``mcp.method.name`` attribute, with the span name as
    a fallback, so it is robust to either changing on its own.
    """
    attrs = getattr(span, "attributes", None) or {}
    if attrs.get("mcp.method.name") != "ping" and getattr(span, "name", None) != "MCP send ping":
        return False
    code = getattr(getattr(span, "status", None), "status_code", None)
    return getattr(code, "name", None) != "ERROR"


class _DelegatingExporter:
    """Base for the per-backend exporter wrappers: ``shutdown`` and
    ``force_flush`` go to the wrapped exporter, subclasses own ``export``."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        flush = getattr(self._inner, "force_flush", None)
        return flush(timeout_millis) if flush else True


class _LoggingExporter(_DelegatingExporter):
    """Delegating exporter that records every batch's outcome in the debug log.

    The batch processors swallow the exporter's result; the SDK only logs
    failures to its own logger. One line per batch (``export Phoenix: 12
    span(s) -> SUCCESS``) makes "did my spans leave the process, and did
    the backend take them?" answerable from ``debug.log`` alone (#167).

    ``label`` names the backend (``"SigNoz logs"`` for its log exporter),
    ``unit`` what a batch holds (``span`` / ``record``), ``failure`` the
    result returned when the wrapped exporter raises instead of returning.
    """

    def __init__(self, inner: Any, label: str, *, unit: str, failure: Any) -> None:
        super().__init__(inner)
        self._label = label
        self._unit = unit
        self._failure = failure

    def export(self, batch: Any) -> Any:
        count = len(batch) if hasattr(batch, "__len__") else "?"
        try:
            result = self._inner.export(batch)
        except Exception as e:  # pragma: no cover — exporter raised instead of returning
            debug_log(
                f"export {self._label}: {count} {self._unit}(s) -> FAILURE "
                f"({type(e).__name__}: {e})"
            )
            return self._failure
        debug_log(
            f"export {self._label}: {count} {self._unit}(s) -> {getattr(result, 'name', result)}"
        )
        return result


if _OTEL_AVAILABLE:

    class _RootUsageStrippingExporter(_DelegatingExporter, SpanExporter):
        """Delegating exporter that drops the token roll-up from agent-kind spans.

        The plugin puts the turn's usage total on the ``agent`` / ``cron``
        root as well as on each ``api.*`` span. Backends that compute
        trace-level usage by summing every span (Opik, LangWatch) then show
        twice the real tokens, and Opik doubles its estimated cost the same
        way. Bound to one backend's exporter, this wrapper rebuilds each
        AGENT-kind span without ``gen_ai.usage.*`` / ``llm.token_count.*``
        (``hermes.cost.*`` and everything else stay) so only that backend's
        copy changes; every other exporter and the live store still get the
        roll-up (#327).
        """

        def export(self, spans: Any) -> Any:
            return self._inner.export(strip_root_usage(spans))

    class _LiveSpanProcessor(SpanProcessor):
        """SpanProcessor that mirrors finished spans into the in-process LiveStore."""

        def __init__(self, store: Any) -> None:
            self._store = store

        def on_start(self, span: Any, parent_context: Any = None) -> None:  # noqa: D401
            pass

        def on_end(self, span: Any) -> None:
            try:
                self._store.add_span(_serialize_span(span))
                if getattr(span, "parent", None) is None:
                    # A root span ending is the end of a turn. Commit now rather
                    # than on the writer thread's next tick: a one-shot process
                    # (`hermes -z`) exits without running atexit, and the turn's
                    # last spans would otherwise never reach the file (#215).
                    self._store.flush()
            except Exception:  # pragma: no cover — never break the SDK export path
                pass

        def shutdown(self) -> None:
            try:
                self._store.flush()
            except Exception:  # pragma: no cover
                pass

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            try:
                self._store.flush()
            except Exception:  # pragma: no cover
                pass
            return True

    class _MCPPingFilterProcessor(SpanProcessor):
        """Delegating SpanProcessor that drops successful MCP keepalive ``ping`` spans.

        MCP Python SDK 2.x (Hermes v0.21.0+) instruments every outbound JSON-RPC
        request with a CLIENT span on the *global* TracerProvider — the one this
        plugin installs. Hermes sends ``ping`` on every idle keepalive interval,
        per MCP connection, so each one surfaces as a standalone single-span
        ``MCP send ping`` trace that carries no agent activity (#62). Skipping
        them at ``on_end`` keeps them out of the OTLP exporters *and* the live
        dashboard store alike. Pings that failed (``StatusCode.ERROR``) are
        passed through — that is the one case where the span is diagnostic.
        """

        def __init__(self, inner: SpanProcessor) -> None:
            self._inner = inner

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            self._inner.on_start(span, parent_context=parent_context)

        def on_end(self, span: Any) -> None:
            if is_mcp_keepalive_ping(span):
                return
            self._inner.on_end(span)

        def shutdown(self) -> None:
            self._inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return self._inner.force_flush(timeout_millis)

else:  # pragma: no cover — plugin is unusable without OTel
    _LiveSpanProcessor = None  # type: ignore[assignment, misc]
    _MCPPingFilterProcessor = None  # type: ignore[assignment, misc]


# ── the Resource ───────────────────────────────────────────────────────


def build_resource(plugin, backends: Optional[List[_ResolvedBackend]] = None) -> "Resource":
    # Process identity (OTel semconv). ``service.instance.id`` is what keeps
    # two Hermes processes on one host (gateway + dashboard, two profiles)
    # from writing the same metric series — without it their independent
    # cumulative counters interleave into one sawtooth (#79). Users can
    # still override any of these via resource_attributes.
    config = plugin.config
    # With ``service_name_from_profile`` a named profile is its own service
    # in the ``hermes-agent`` namespace, so a fleet shows one service per
    # profile (#395). The namespace is only set then: OTLP-to-Prometheus
    # folds it into ``job`` (``hermes-agent/hermes-agent``), which would
    # rename every metric series of a plain install. ``resource_attributes``
    # below still override both.
    profile_service = named_profile(plugin.profile_name) if config.service_name_from_profile else ""
    attrs: Dict[str, Any] = {
        "service.name": profile_service or DEFAULT_SERVICE_NAME,
        "service.instance.id": _SERVICE_INSTANCE_ID,
        "process.pid": os.getpid(),
    }
    # The platform, so a fleet can split by OS and CPU architecture (#476).
    for key, value in (("os.type", os_type()), ("host.arch", host_arch())):
        if value:
            attrs[key] = value
    if profile_service:
        attrs["service.namespace"] = DEFAULT_SERVICE_NAME
    pkg_version = package_version()
    if pkg_version:
        attrs["service.version"] = pkg_version
    # Which Hermes profile produced this telemetry (#70). A resource
    # attribute so backends can split traces and metrics per agent even
    # where span attributes are not filterable; also on the root span.
    attrs["hermes.profile"] = plugin.profile_name
    # Kanban worker identity: the dispatcher exports these into a worker's
    # environment, so everything a card's run emits can be joined back to
    # the card and to the attempt. Keyed on the task: Hermes pins the
    # current board into HERMES_KANBAN_BOARD at the start of EVERY chat
    # (hermes-agent #20074), so the board alone does not mark a worker.
    if os.getenv("HERMES_KANBAN_TASK", "").strip():
        for env_name, attr_name in _KANBAN_ENV_ATTRS:
            value = os.getenv(env_name, "").strip()
            if value:
                attrs[attr_name] = value
    if config.resource_attributes:
        attrs.update(config.resource_attributes)
    for b in backends or []:
        for key, value in (b.resource_attributes or {}).items():
            existing = attrs.get(key)
            if existing is not None and str(existing) != str(value):
                raise ValueError(
                    f"{b.display_name} resource attribute {key!r} conflicts with "
                    f"configured value {existing!r}"
                )
            attrs[key] = value
    for b in backends or []:
        if b.type == "weave":
            missing = [key for key in ("wandb.entity", "wandb.project") if not attrs.get(key)]
            if missing:
                joined = ", ".join(missing)
                raise ValueError(
                    f"Weave requires {joined} as resource attributes "
                    "(set them under resource_attributes or on the weave backend entry)"
                )
    project_name = config.project_name or os.getenv("OTEL_PROJECT_NAME", "").strip()
    if project_name:
        attrs["openinference.project.name"] = project_name
    # host.name lets the host/GPU series join the traces they were sampled
    # alongside (and a Collector's hostmetrics series, if one runs too).
    if config.host_metrics and not attrs.get("host.name"):
        try:
            import socket

            attrs["host.name"] = socket.gethostname()
        except Exception:  # pragma: no cover — never block init on this
            pass
    return Resource.create(attrs)


# ── traces + metrics ───────────────────────────────────────────────────


def _build_exporter(signal: str, b: _ResolvedBackend, config: Any) -> Any:
    """``b``'s exporter for ``signal``, wrapped the way the pipeline attaches
    it: a ``BatchSpanProcessor`` for traces, a ``PeriodicExportingMetricReader``
    for metrics. Endpoint and headers come from :meth:`_ResolvedBackend.target`.

    Logs are built by :func:`log_handler.build_log_processors` from the same
    ``target`` rule, on the ``LoggerProvider`` :func:`init_logs_pipeline` makes.
    """
    target = b.target(signal, config.headers)
    if signal == "traces":
        with without_otlp_header_env():
            inner: Any = OTLPSpanExporter(endpoint=target.endpoint, headers=target.headers)
        if not b.root_usage:
            # This backend sums usage over every span (#327).
            inner = _RootUsageStrippingExporter(inner)
            debug_log(f"root usage stripped for {b.display_name}")
        return BatchSpanProcessor(
            _LoggingExporter(inner, b.display_name, unit="span", failure=SpanExportResult.FAILURE),
            max_queue_size=config.span_batch_max_queue_size,
            schedule_delay_millis=config.span_batch_schedule_delay_ms,
            max_export_batch_size=effective_export_batch_size(config),
            export_timeout_millis=config.span_batch_export_timeout_ms,
        )
    temporality = b.metrics_temporality or config.metrics_temporality
    with without_otlp_header_env():
        exporter = OTLPMetricExporter(
            endpoint=target.endpoint,
            headers=target.headers,
            preferred_temporality=metric_temporality_map(temporality),
            preferred_aggregation=metric_preferred_aggregation(config.metrics_histogram),
        )
    if temporality:
        debug_log(f"metrics temporality for {b.display_name}: {temporality}")
    return PeriodicExportingMetricReader(exporter, export_interval_millis=config.flush_interval_ms)


def init_otlp_pipeline(plugin, backends: List[_ResolvedBackend]) -> bool:
    """Wire one TracerProvider to all backends + a shared MeterProvider.

    Each backend gets its own ``BatchSpanProcessor`` (independent worker
    thread, independent queue) so a slow or unreachable collector cannot
    delay span enqueue or starve the others. Metrics fan out the same
    way: one ``PeriodicExportingMetricReader`` per backend that supports
    metrics, attached to a single shared ``MeterProvider``. One backend
    whose exporter fails to build is logged and skipped; the others proceed.
    """
    config = plugin.config
    try:
        resource = plugin._build_resource(backends)
    except ValueError as e:
        # Conflicting or missing resource attributes (build_resource says
        # which): a configuration error the pipeline cannot start without.
        logger.error(f"[hermes-otel] ✗ pipeline init failed: {e}")
        return False

    provider_kwargs: Dict[str, Any] = {"resource": resource}
    if config.sample_rate is not None:
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        provider_kwargs["sampler"] = ParentBased(TraceIdRatioBased(config.sample_rate))
    provider = TracerProvider(**provider_kwargs)

    def _attach(processor: Any) -> None:
        # Every processor sits behind the same MCP keepalive filter so
        # the OTLP backends and the live dashboard agree on what a
        # trace list looks like. See _MCPPingFilterProcessor.
        if config.suppress_mcp_ping_spans:
            processor = _MCPPingFilterProcessor(processor)
        provider.add_span_processor(processor)

    # In-process live store for the zero-config dashboard. Added FIRST so
    # the dashboard renders the agent's activity even with no OTLP
    # backend configured — install the plugin, open the tab, see traces.
    plugin._live_active = False
    if config.dashboard_live and _LiveSpanProcessor is not None:
        from .live_store import get_live_store

        store = get_live_store(
            create=True,
            max_rows=config.dashboard_live_max_spans,
            retention_hours=config.dashboard_live_retention_hours,
        )
        if store is not None:
            _attach(_LiveSpanProcessor(store))
            plugin._live_active = True
            # The live Logs tab is wired in init_logs_pipeline (same provider
            # as the OTLP exporters, #265).

    for b in backends:
        for signal in b.enabled_signals():
            if signal == "logs":
                continue  # built on the LoggerProvider in init_logs_pipeline
            try:
                built = _build_exporter(signal, b, config)
            except Exception as e:
                # One backend failing must not stop the others.
                logger.error(f"[hermes-otel] ✗ {b.display_name} {signal} init failed: {e}")
                if signal == "traces":
                    break  # its metrics reader and its ✓ line go with it
                continue
            if signal == "traces":
                _attach(built)
                plugin._span_processors.append(built)
            else:
                plugin._metric_readers.append(built)
        else:
            logger.info(f"[hermes-otel] ✓ {b.display_name} at {b.endpoint}{b.describe_signals()}")

    # Proceed when *either* an OTLP backend or the live store is active.
    # (Live-only = the zero-config dashboard with no external collector.)
    if not plugin._span_processors and not plugin._live_active:
        return False

    # The OTel globals can be set once per process: on a re-init the
    # SDK keeps the old (now shut down) provider and logs "Overriding of
    # current TracerProvider is not allowed". So the tracer / meter we
    # use always come from OUR provider objects; the globals are set
    # only while still unset, for third-party code that instruments
    # through them.
    plugin._tracer_provider = provider
    _set_global_once(trace, "tracer", provider)
    plugin.tracer = provider.get_tracer("hermes-otel-plugin")

    if plugin._metric_readers:
        plugin._meter_provider = MeterProvider(
            resource=resource,
            metric_readers=plugin._metric_readers,
            views=metric_views(config.metrics_histogram),
        )
        _set_global_once(metrics, "meter", plugin._meter_provider)
        plugin._meter = plugin._meter_provider.get_meter("hermes-otel-plugin")
        plugin._create_metric_instruments()
        debug_log(f"Metrics initialized for {len(plugin._metric_readers)} backend(s)")

    plugin._init_logs_pipeline(resource, backends)

    plugin._initialized = True
    plugin._register_atexit_flush()

    mode = content_mode(config)
    if mode == "off":
        logger.warning(
            "[hermes-otel] ⚠ content_capture=off — prompts, tool I/O and responses are not recorded"
        )
    elif mode == "full":
        logger.info(
            "[hermes-otel] ✓ content_capture=full — complete prompts and responses on every "
            "api.* span (set content_capture: preview or off to reduce)"
        )
    if len(plugin._span_processors) > 1:
        logger.info(
            f"[hermes-otel] ✓ Multi-backend fan-out active "
            f"({len(plugin._span_processors)} collectors, "
            f"{len(plugin._metric_readers)} with metrics)"
        )
    if plugin._live_active:
        logger.info(
            "[hermes-otel] ✓ Live dashboard store active"
            + ("" if plugin._span_processors else " (no OTLP backend — live-only)")
        )
    if config.probe_backends and backends:
        # One TCP connect per endpoint on a background thread (#481): a
        # dead backend is said so at startup instead of 30 s later inside
        # the batch worker. Never gates an export.
        from .probe import start_probe

        def _report(results):
            plugin.probe_results = results
            for r in results:
                (logger.info if r.ok else logger.warning)(r.line())
                debug_log(r.line())

        plugin._probe_thread = start_probe(backends, _report)
    return True


def create_metric_instruments(plugin) -> None:
    """Create every instrument in ``_INSTRUMENTS`` on ``plugin._meter``.

    One that fails to create is left out rather than aborting the rest;
    ``record_metric`` skips keys without an instrument.
    """
    if plugin._meter is None:
        return
    compat_mode = plugin.config.metric_name_compat
    for key, spec in _INSTRUMENTS.items():
        if spec.compat and compat_mode not in (spec.compat, "both"):
            continue  # a compatibility family that is switched off (#485)
        try:
            factory = (
                plugin._meter.create_histogram
                if spec.kind == "histogram"
                else plugin._meter.create_counter
            )
            plugin._instruments[key] = factory(
                spec.name, unit=spec.unit, description=spec.description
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(f"[hermes-otel] could not create metric {spec.name}: {e}")
    plugin._create_host_metric_instruments()


# ── logs ───────────────────────────────────────────────────────────────


def init_logs_pipeline(plugin, resource: "Resource", backends: List[_ResolvedBackend]) -> None:
    """Wire a :class:`LoggerProvider` + handler when ``capture_logs`` is on.

    One provider carries the enrichment processor, the live store sink
    (when ``dashboard_live`` is on) and one exporter per log-capable
    backend (#265). Skipped silently when ``capture_logs=false`` or the
    SDK logs module is unavailable; with no log-capable backend the live
    store alone still gets the records. Failures in individual backend
    exporters are logged but do not block pipeline init.
    """
    config = plugin.config
    capture = bool(config.capture_logs)
    events = bool(config.log_events)
    if not (capture or events):
        return

    from . import log_handler

    if not log_handler._LOGS_AVAILABLE:
        logger.warning(
            "[hermes-otel] ⚠ logs.capture / logs.events are on but opentelemetry.sdk._logs "
            "is unavailable; upgrade opentelemetry-sdk to enable logs"
        )
        return

    rules = log_handler.rules_from_config(config)
    batch = {
        "schedule_delay_ms": config.log_batch_schedule_delay_ms,
        "max_queue_size": config.log_batch_max_queue_size,
        "max_export_batch_size": config.log_batch_max_export_batch_size,
        "export_timeout_ms": config.log_batch_export_timeout_ms,
    }
    processors = log_handler.build_log_processors(
        backends,
        config.headers,
        batch=batch,
        rules=rules,
        content_mode=content_mode(config),
        preview_chars=config.preview_max_chars,
    )
    live_store = None
    if plugin._live_active:
        try:
            from .live_store import get_live_store

            live_store = get_live_store(create=False)
        except Exception:  # pragma: no cover
            live_store = None
    if not processors:
        backend_types = ", ".join(b.type for b in backends) or "none"
        what = "logs.capture" if capture else "logs.events"
        if live_store is None:
            logger.warning(
                f"[hermes-otel] ⚠ {what} is on but no configured backend "
                f"accepts OTLP logs ({backend_types}); add a signoz/otlp backend "
                f"or set logs: true on an existing entry"
            )
            return
        logger.info(
            f"[hermes-otel] Logs → live store only (no configured backend accepts "
            f"OTLP logs: {backend_types})"
        )

    level = log_handler.resolve_level(config.log_level)
    # The live tail has its own floor (``logs.live_min_level``, INFO by
    # default) so Hermes's DEBUG firehose cannot evict useful lines from
    # the bounded buffer; OTLP keeps ``log_level``.
    live_level = max(level, log_handler.resolve_level(config.log_live_min_level))
    plugin._logger_provider = log_handler.install_handler(
        resource=resource,
        processors=processors,
        level=level,
        attach_logger=config.log_attach_logger,
        context_resolver=plugin.active_span_for_logs,
        tracker=plugin.spans,
        live_store=live_store,
        live_min_level=live_level,
        rules=rules,
        max_attribute_length=config.log_max_attribute_length,
        attach_handler=capture,
    )
    if plugin._logger_provider is None:
        return

    plugin._log_processors = [p for p, _b in processors]
    if processors:
        sinks = f"{len(processors)} backend(s){', live store' if live_store is not None else ''}"
        if capture:
            target = config.log_attach_logger or "root"
            logger.info(
                f"[hermes-otel] ✓ Logs → {sinks} "
                f"(attached to {target}, level={config.log_level.upper()}"
                f"{', events on' if events else ''})"
            )
        else:
            logger.info(f"[hermes-otel] ✓ Log events → {sinks} (stdlib logs not captured)")
