"""OpenTelemetry tracer for Hermes plugin.

Provides a singleton tracer manager that fans out spans/metrics to one or more
collector backends (Phoenix, Langfuse, SigNoz, Jaeger, Tempo, LGTM, Uptrace,
OpenObserve, or any other OTLP-compatible collector via the ``otlp`` type).
LangSmith remains a separate, env-var-only single-backend path because it uses
its own HTTP API rather than OTLP.

When multiple backends are configured the SDK's TracerProvider holds one
``BatchSpanProcessor`` per backend — each processor owns its own background
worker thread, so no single slow collector can block the agent's hot path or
delay export to the others. A shared ``MeterProvider`` similarly fans metrics
out via one ``PeriodicExportingMetricReader`` per backend that supports them.
"""

from __future__ import annotations

import atexit
import os
import time
from typing import Any, Dict, List, Optional

from . import backends as _backends
from .backends import _TRACES_ONLY, _ResolvedBackend
from .debug_utils import debug_log, logger
from .plugin_config import BackendConfig, HermesOtelConfig, load_config
from .session_state import SessionState

# Re-exported for tests (conftest resets _PARENT_STACK between runs).
# Canonical definition lives in span_tracker so the module is self-contained.
from .span_tracker import _PARENT_STACK, SpanTracker  # noqa: F401 (re-export)

try:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import INVALID_SPAN, set_span_in_context

    _OTEL_AVAILABLE = True
    _METRICS_AVAILABLE = True
except ImportError as e:
    _OTEL_AVAILABLE = False
    _METRICS_AVAILABLE = False
    # If OTel itself is missing we short-circuit via is_enabled=False before
    # ever returning this; the None is just to keep module-level references valid.
    INVALID_SPAN = None  # type: ignore[assignment]
    print(
        f"[hermes-otel] OpenTelemetry import error: {e}. Run: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http"
    )


class HermesOTelPlugin:
    """OpenTelemetry tracer manager for the Hermes plugin.

    Uses a global TracerProvider set up once at plugin registration.
    All hooks share this instance via the module-level get_tracer().
    """

    _instance: Optional["HermesOTelPlugin"] = None

    # OpenInference semantic convention values — Phoenix recognizes these
    _KIND_MAP = {
        "tool": "TOOL",
        "llm": "LLM",
        "general": "GENERAL",
        "agent": "AGENT",
    }

    def __init__(self, config: Optional[HermesOtelConfig] = None):
        self.tracer = None
        self.spans = SpanTracker()
        # Per-session aggregators for hook callbacks (token totals, I/O,
        # turn summary, tool start times). Owned here so tests that
        # re-create the tracer singleton automatically get a fresh set.
        self.sessions = SessionState()
        self._initialized = False
        # OTLP fan-out: one BatchSpanProcessor + one PeriodicExportingMetricReader
        # per backend. The singular ``_span_processor`` / ``_metric_reader``
        # attributes are kept as aliases pointing at the first entry so legacy
        # tests and external callers that introspect them keep working.
        self._span_processors: List[Any] = []
        self._metric_readers: List[Any] = []
        self._log_processors: List[Any] = []
        self._span_processor = None
        self._metric_reader = None
        self._backend_summaries: List[str] = []
        # LoggerProvider created when capture_logs is on and at least one
        # log-capable backend is wired up. None otherwise.
        self._logger_provider: Optional[Any] = None
        # LangSmith backend (None when using OTLP). Set when LANGSMITH_TRACING=true.
        self._langsmith = None
        # Metrics
        self._meter = None
        self._meter_provider = None
        self._session_count = None
        self._token_usage = None
        self._cost_usage = None
        self._tool_duration = None
        self._message_count = None
        self._model_usage = None
        self._skill_inferred_counter = None
        # Config
        self.config: HermesOtelConfig = config if config is not None else load_config()
        # Turn registry for orphan sweep (session_id -> perf_counter start time)
        self._turn_started_at: Dict[str, float] = {}
        # Map session_id -> set of active span keys, so the orphan sweep
        # can finalize sub-spans (api:/tool:) whose keys don't embed session_id.
        self._session_keys: Dict[str, set] = {}
        # Guards against double-registering the atexit flush handler when
        # init() is called multiple times (e.g. in tests / plugin reload).
        self._atexit_registered: bool = False

    # ── Initialization entry point ───────────────────────────────────────

    def init(self, endpoint: str = None) -> bool:
        """Initialize one or more backends.

        Resolution order:
          1. ``LANGSMITH_TRACING=true`` → LangSmith (HTTP API, single backend).
          2. ``config.backends`` non-empty → fan out to every entry via
             ``_init_otlp_pipeline``.
          3. Explicit ``endpoint`` arg → single Phoenix backend (via
             ``_init_otlp`` for back-compat).
          4. Legacy env-var detection (single backend, first match wins):
             Langfuse → SigNoz → Jaeger → Tempo → Phoenix. Each branch calls
             ``_init_otlp`` so existing tests that mock it keep working.

        Returns True if at least one backend was initialized.
        """
        if not _OTEL_AVAILABLE:
            logger.error("[hermes-otel] ✗ OpenTelemetry packages not available")
            return False

        if not self.config.enabled:
            logger.warning("[hermes-otel] ✗ Disabled via config (enabled=false)")
            return False

        # 1. LangSmith short-circuit (legacy compat).
        if self._wants_langsmith():
            return self._init_langsmith()

        # 2. Multi-backend fan-out from yaml config.
        if self.config.backends:
            backends: List[_ResolvedBackend] = []
            for bc in self.config.backends:
                try:
                    rb = self._resolve_backend_config(bc)
                except Exception as e:
                    logger.warning(f"[hermes-otel] ✗ backend {bc.type!r} skipped: {e}")
                    continue
                if rb is not None:
                    backends.append(rb)
            if not backends:
                logger.warning("[hermes-otel] ✗ config.backends had no valid entries")
                return False
            return self._init_otlp_pipeline(backends)

        # 3. Explicit endpoint arg → single Phoenix backend.
        if endpoint:
            return self._init_otlp(endpoint, backend_name="Phoenix")

        # 4. Legacy env-var detection (single backend).
        return self._init_otlp_from_env()

    # ── Backend resolution ───────────────────────────────────────────────

    @staticmethod
    def _wants_langsmith() -> bool:
        return os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true" and bool(
            os.getenv("LANGSMITH_API_KEY", "").strip()
        )

    def _init_otlp_from_env(self) -> bool:
        """First-match-wins single-backend init from environment variables.

        Delegates to :mod:`backends` for resolution, then routes through
        ``_init_otlp`` so the existing unit-test suite (which patches
        ``_init_otlp``) keeps verifying the routing.
        """
        rb = _backends.resolve_from_env()
        if rb is None:
            logger.warning(
                "[hermes-otel] ✗ No backend configured "
                "(set OTEL_PHOENIX_ENDPOINT, OTEL_SIGNOZ_ENDPOINT, "
                "OTEL_JAEGER_ENDPOINT, OTEL_TEMPO_ENDPOINT, "
                "Langfuse credentials, or LANGSMITH_TRACING; or define "
                "'backends:' in config.yaml)"
            )
            return False
        return self._init_otlp(rb.endpoint, headers=rb.headers, backend_name=rb.display_name)

    def _resolve_backend_config(self, bc: BackendConfig) -> Optional[_ResolvedBackend]:
        """Turn a yaml ``BackendConfig`` into a ready-to-wire backend.

        Thin shim around :func:`backends.resolve` — kept as a method so
        subclasses / monkeypatch-based tests have a stable seam.
        """
        return _backends.resolve(bc)

    # ── Backend initializers ─────────────────────────────────────────────

    def _init_otlp(
        self, endpoint: str, headers: Optional[Dict[str, str]] = None, backend_name: str = "OTLP"
    ) -> bool:
        """Single-backend wrapper around ``_init_otlp_pipeline``.

        Preserves the original API for tests and external callers that
        bypass ``init()`` (e.g. e2e harnesses) and want to wire one
        backend directly. Internally it just calls the multi-backend
        pipeline with a list of one.
        """
        backend = _ResolvedBackend(
            type=backend_name.lower(),
            endpoint=endpoint,
            display_name=backend_name,
            headers=headers,
            supports_metrics=backend_name.lower() not in _TRACES_ONLY,
        )
        return self._init_otlp_pipeline([backend])

    def _init_langsmith(self) -> bool:
        """Initialize LangSmith backend from environment variables."""
        from .langsmith_backend import LangSmithBackend

        try:
            backend = LangSmithBackend.from_env()
            if backend is None:
                return False
            self._langsmith = backend
            self._initialized = True
            logger.info(f"[hermes-otel] ✓ LangSmith at {backend.endpoint}")
            return True
        except Exception as e:
            logger.error(f"[hermes-otel] ✗ LangSmith init failed: {e}")
            return False

    def _build_resource(self) -> "Resource":
        attrs: Dict[str, Any] = {"service.name": "hermes-agent"}
        if self.config.global_tags:
            attrs.update(self.config.global_tags)
        if self.config.resource_attributes:
            attrs.update(self.config.resource_attributes)
        project_name = self.config.project_name or os.getenv("OTEL_PROJECT_NAME", "").strip()
        if project_name:
            attrs["openinference.project.name"] = project_name
        return Resource.create(attrs)

    @staticmethod
    def _derive_metrics_endpoint(traces_endpoint: str) -> str:
        """Phoenix/SigNoz use /v1/traces and /v1/metrics on the same host."""
        if traces_endpoint.endswith("/v1/traces"):
            return traces_endpoint[: -len("/v1/traces")] + "/v1/metrics"
        return traces_endpoint

    def _merge_headers(self, backend_headers: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """Layer config.headers on top of per-backend headers."""
        merged: Dict[str, str] = dict(backend_headers or {})
        if self.config.headers:
            merged.update(self.config.headers)
        return merged or None

    def _init_otlp_pipeline(self, backends: List[_ResolvedBackend]) -> bool:
        """Wire one TracerProvider to all backends + a shared MeterProvider.

        Each backend gets its own ``BatchSpanProcessor`` (independent worker
        thread, independent queue) so a slow or unreachable collector cannot
        delay span enqueue or starve the others. Metrics fan out the same
        way: one ``PeriodicExportingMetricReader`` per backend that supports
        metrics, attached to a single shared ``MeterProvider``.
        """
        try:
            resource = self._build_resource()

            provider_kwargs: Dict[str, Any] = {"resource": resource}
            if self.config.sample_rate is not None:
                from opentelemetry.sdk.trace.sampling import (
                    ParentBased,
                    TraceIdRatioBased,
                )

                provider_kwargs["sampler"] = ParentBased(TraceIdRatioBased(self.config.sample_rate))
            provider = TracerProvider(**provider_kwargs)

            metric_readers: List[Any] = []

            for b in backends:
                hdrs = self._merge_headers(b.headers)
                try:
                    exporter = OTLPSpanExporter(endpoint=b.endpoint, headers=hdrs)
                    processor = BatchSpanProcessor(
                        exporter,
                        max_queue_size=self.config.span_batch_max_queue_size,
                        schedule_delay_millis=self.config.span_batch_schedule_delay_ms,
                        max_export_batch_size=self.config.span_batch_max_export_batch_size,
                        export_timeout_millis=self.config.span_batch_export_timeout_ms,
                    )
                    provider.add_span_processor(processor)
                    self._span_processors.append(processor)
                except Exception as e:
                    logger.error(f"[hermes-otel] ✗ {b.display_name} traces init failed: {e}")
                    continue

                if b.supports_metrics and _METRICS_AVAILABLE:
                    metrics_endpoint = self._derive_metrics_endpoint(b.endpoint)
                    try:
                        m_exporter = OTLPMetricExporter(endpoint=metrics_endpoint, headers=hdrs)
                        reader = PeriodicExportingMetricReader(
                            m_exporter,
                            export_interval_millis=self.config.flush_interval_ms,
                        )
                        metric_readers.append(reader)
                        self._metric_readers.append(reader)
                    except Exception as e:
                        debug_log(f"{b.display_name} metrics init failed: {e}")

                self._backend_summaries.append(f"{b.display_name} → {b.endpoint}")
                logger.info(
                    f"[hermes-otel] ✓ {b.display_name} at {b.endpoint}"
                    + (" (traces only)" if not b.supports_metrics else "")
                )

            if not self._span_processors:
                return False

            # Back-compat singular aliases — first entry wins.
            self._span_processor = self._span_processors[0]
            if self._metric_readers:
                self._metric_reader = self._metric_readers[0]

            trace.set_tracer_provider(provider)
            self.tracer = trace.get_tracer("hermes-otel-plugin")

            if metric_readers and _METRICS_AVAILABLE:
                self._meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=metric_readers,
                )
                metrics.set_meter_provider(self._meter_provider)
                self._meter = metrics.get_meter("hermes-otel-plugin")
                self._create_metric_instruments()
                debug_log(f"Metrics initialized for {len(metric_readers)} backend(s)")

            self._init_logs_pipeline(resource, backends)

            self._initialized = True
            self._register_atexit_flush()

            if not self.config.capture_previews:
                logger.warning(
                    "[hermes-otel] ⚠ capture_previews=false — input/output values suppressed"
                )
            if len(self._span_processors) > 1:
                logger.info(
                    f"[hermes-otel] ✓ Multi-backend fan-out active "
                    f"({len(self._span_processors)} collectors, "
                    f"{len(self._metric_readers)} with metrics)"
                )
            return True
        except Exception as e:
            logger.error(f"[hermes-otel] ✗ pipeline init failed: {e}")
            return False

    def _create_metric_instruments(self) -> None:
        """Create the shared metric instruments on ``self._meter``."""
        if self._meter is None:
            return
        self._session_count = self._meter.create_counter(
            "hermes.session.count",
            description="Sessions created",
        )
        self._token_usage = self._meter.create_counter(
            "hermes.token.usage",
            description="Tokens consumed by type",
        )
        self._cost_usage = self._meter.create_counter(
            "hermes.cost.usage",
            description="USD cost per message",
        )
        self._tool_duration = self._meter.create_histogram(
            "hermes.tool.duration",
            unit="ms",
            description="Tool execution time",
        )
        self._message_count = self._meter.create_counter(
            "hermes.message.count",
            description="Completed assistant messages",
        )
        self._model_usage = self._meter.create_counter(
            "hermes.model.usage",
            description="Messages per model and provider",
        )
        self._skill_inferred_counter = self._meter.create_counter(
            "hermes.skill.inferred",
            description="Skill-name inference hits on tool spans",
        )

    def _init_logs_pipeline(self, resource: "Resource", backends: List[_ResolvedBackend]) -> None:
        """Wire a :class:`LoggerProvider` + handler when ``capture_logs`` is on.

        Skipped silently when ``capture_logs=false``, the SDK logs module is
        unavailable, or no backend accepts OTLP logs. Failures in individual
        backend exporters are logged but do not block pipeline init.
        """
        if not self.config.capture_logs:
            return

        from . import log_handler

        if not log_handler._LOGS_AVAILABLE:
            logger.warning(
                "[hermes-otel] ⚠ capture_logs=true but opentelemetry.sdk._logs "
                "is unavailable; upgrade opentelemetry-sdk to enable logs"
            )
            return

        processors = log_handler.build_log_processors(backends, self.config.headers)
        if not processors:
            backend_types = ", ".join(b.type for b in backends) or "none"
            logger.warning(
                f"[hermes-otel] ⚠ capture_logs=true but no configured backend "
                f"accepts OTLP logs ({backend_types}); add a signoz/otlp backend "
                f"or set logs: true on an existing entry"
            )
            return

        level = log_handler.resolve_level(self.config.log_level)
        self._logger_provider = log_handler.install_handler(
            resource=resource,
            processors=processors,
            level=level,
            attach_logger=self.config.log_attach_logger,
        )
        if self._logger_provider is None:
            return

        self._log_processors = [p for p, _b in processors]
        target = self.config.log_attach_logger or "root"
        logger.info(
            f"[hermes-otel] ✓ Logs → {len(processors)} backend(s) "
            f"(attached to {target}, level={self.config.log_level.upper()})"
        )

    def record_metric(self, name: str, value: float, attributes: dict = None, bucket: str = None):
        """Record a metric value."""
        if not self._meter:
            return

        attrs = dict(attributes or {})

        if name == "session_count":
            self._session_count.add(1, attrs)
        elif name == "token_usage":
            self._token_usage.add(int(value), attrs)
        elif name == "cost_usage":
            self._cost_usage.add(value, attrs)
        elif name == "tool_duration":
            self._tool_duration.record(value, attrs)
        elif name == "message_count":
            self._message_count.add(1, attrs)
        elif name == "model_usage":
            self._model_usage.add(1, attrs)
        elif name == "skill_inferred":
            if self._skill_inferred_counter is not None:
                self._skill_inferred_counter.add(1, attrs)

    def start_span(
        self,
        name: str,
        key: str,
        kind: str = "general",
        attributes: dict = None,
        session_id: Optional[str] = None,
    ):
        """Create and track a new span.

        Args:
            session_id: When provided, the span key is linked to this session
                        so the orphan sweep can finalize it if the session
                        exceeds root_span_ttl_ms.
        """
        if not self._initialized:
            return INVALID_SPAN

        if session_id:
            self._session_keys.setdefault(session_id, set()).add(key)

        # LangSmith mode — HTTP only
        if self._langsmith:
            parent_run = self.spans.get_current_parent(session_id)
            run_obj = self._langsmith.start_span(
                name,
                key,
                kind,
                attributes,
                parent_run=parent_run,
            )
            if run_obj is None:
                return INVALID_SPAN
            self.spans.start_span(key, run_obj)
            return run_obj

        # OTLP mode (Phoenix/Langfuse/etc.)
        if not self.tracer:
            return INVALID_SPAN

        try:
            attrs = dict(attributes or {})

            # OpenInference semantic conventions — Phoenix recognizes these
            kind_value = self._KIND_MAP.get(kind, "GENERAL")
            attrs["traceloop.span.kind"] = kind_value
            attrs["openinference.span.kind"] = kind_value

            # Check for active parent — prefers the session-keyed stack so
            # nesting survives hermes' cross-thread hook dispatch.
            parent = self.spans.get_current_parent(session_id)
            span_ctx = None
            if parent is not None and hasattr(parent, "get_span_context"):
                span_ctx = set_span_in_context(parent)

            span = self.tracer.start_span(name, attributes=attrs, context=span_ctx)
            self.spans.start_span(key, span)
            debug_log(f"start_span: {name} (key={key}, kind={kind_value})")
            return span
        except Exception as e:
            debug_log(f"Error starting span '{name}': {e}")
            return INVALID_SPAN

    def end_span(
        self, key: str, attributes: dict = None, status: str = None, error_message: str = None
    ):
        """End a tracked span by its key."""
        try:
            # LangSmith mode — HTTP
            if self._langsmith:
                run = self.spans.get_span(key)
                if run:
                    self._langsmith.end_span(
                        run, attributes=attributes, status=status, error_message=error_message
                    )
                    # Remove from active spans directly (bypass SpanTracker.end_span
                    # which tries to call .end() — but LangSmith runs are dicts)
                    self.spans._active_spans.pop(key, None)
            else:
                # OTLP mode — just enqueue. BatchSpanProcessor handles
                # export asynchronously. on_session_end / atexit flush
                # when data actually needs to be visible.
                self.spans.end_span(
                    key, attributes=attributes, status=status, error_message=error_message
                )
            # Remove this key from any session's active-key set.
            for sid, keys in list(self._session_keys.items()):
                keys.discard(key)
                if not keys:
                    self._session_keys.pop(sid, None)
            debug_log(f"end_span: key={key}")
        except Exception as e:
            debug_log(f"Error ending span (key={key}): {e}")

    # ── Turn registry (orphan sweep) ─────────────────────────────────────

    def register_turn(self, session_id: str, started_at: Optional[float] = None) -> None:
        """Record the start time of a session for TTL tracking.

        ``started_at`` is a ``time.perf_counter()``-style monotonic value
        (seconds, arbitrary epoch). Defaults to ``now``; tests pass a
        back-dated value to simulate timeouts without monkeypatching
        ``time``.
        """
        if not session_id:
            return
        self._turn_started_at[session_id] = (
            started_at if started_at is not None else time.perf_counter()
        )

    def unregister_turn(self, session_id: str) -> None:
        """Remove a session from the turn registry (normal end)."""
        self._turn_started_at.pop(session_id, None)

    def sweep_expired_turns(self) -> list:
        """Finalize any sessions whose start time exceeds root_span_ttl_ms.

        Returns the list of finalized session_ids (useful for tests).
        """
        if not self._turn_started_at:
            return []
        threshold_seconds = self.config.root_span_ttl_ms / 1000.0
        now = time.perf_counter()
        expired = [
            sid
            for sid, started_at in self._turn_started_at.items()
            if now - started_at > threshold_seconds
        ]
        for sid in expired:
            self._finalize_orphan(sid)
        return expired

    def _finalize_orphan(self, session_id: str) -> None:
        """End any still-active spans for a timed-out session.

        Marks the session span (if present) with `hermes.turn.final_status=timed_out`
        and status OK (per PRD: timeouts must not inflate error rates).
        """
        self._turn_started_at.pop(session_id, None)

        # End non-session active spans first (api.*, tool.*, llm.*) so the
        # session span ends last and contains them in the hierarchy.
        session_key = f"session:{session_id}"
        keys = self._session_keys.pop(session_id, set())
        non_session_keys = [k for k in keys if k != session_key]
        for key in non_session_keys:
            self.end_span(key, status="ok")

        if session_key in self.spans._active_spans:
            self.end_span(
                session_key,
                attributes={"hermes.turn.final_status": "timed_out"},
                status="ok",
            )
        # Drop any parent stack references — the sweep is a safety net and
        # subsequent hooks will rebuild state correctly.
        self.spans._session_parent_stacks.pop(session_id, None)
        stack = _PARENT_STACK.get()
        if stack is not None:
            stack.clear()

    def _force_flush(self):
        """Force export of all buffered spans and metrics across every backend.

        Called:
          - at the end of each session (so UI sees traces promptly)
          - on process shutdown via atexit (so graceful exit loses nothing)
        Per-span flushing is deliberately NOT done — it would defeat the
        whole purpose of the BatchSpanProcessor queue.
        """
        # Iterate over the multi-backend list when present, otherwise fall
        # back to the singular alias (set up by test fixtures that bypass
        # ``_init_otlp_pipeline``).
        processors = self._span_processors or (
            [self._span_processor] if self._span_processor else []
        )
        for processor in processors:
            try:
                processor.force_flush(timeout_millis=2000)
            except Exception:
                pass
        if self._meter_provider:
            try:
                self._meter_provider.force_flush(timeout_millis=2000)
            except Exception:
                pass
        if self._logger_provider:
            try:
                self._logger_provider.force_flush(timeout_millis=2000)
            except Exception:
                pass

    def _register_atexit_flush(self) -> None:
        """Register a single atexit hook that flushes buffered spans/metrics.

        Idempotent across multiple init() calls (plugin reload, tests).
        """
        if self._atexit_registered:
            return
        atexit.register(self._force_flush)
        self._atexit_registered = True

    @property
    def is_enabled(self) -> bool:
        return self._initialized


# Module-level singleton
_tracer = None


def get_tracer() -> HermesOTelPlugin:
    """Get or create the singleton tracer instance."""
    global _tracer
    if _tracer is None:
        _tracer = HermesOTelPlugin()
    return _tracer
