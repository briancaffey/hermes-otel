"""OpenTelemetry tracer for Hermes plugin.

Provides singleton tracer manager with span tracking between
pre/post hook calls.
"""

from __future__ import annotations

import atexit
import base64
import contextvars
import os
import time
from typing import Any, Dict, Optional

from .debug_utils import debug_log
from .plugin_config import HermesOtelConfig, load_config


# Per-context parent span stack.  Using ContextVar (not threading.local)
# ensures isolation across both threads AND asyncio coroutines: each
# async task and each thread gets its own independent stack because
# contextvars copy-on-write at task/thread boundaries.
#
# Default is None (not []) to avoid sharing a single list across contexts.
_PARENT_STACK: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "hermes_otel_parent_stack", default=None
)

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, set_span_in_context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    _OTEL_AVAILABLE = True
    _METRICS_AVAILABLE = True
except ImportError as e:
    _OTEL_AVAILABLE = False
    _METRICS_AVAILABLE = False
    print(f"[hermes-otel] OpenTelemetry import error: {e}. Run: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http")


class NoopSpan:
    """No-op span fallback."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status_code, description: str = "") -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def end(self) -> None:
        pass


class SpanTracker:
    """Track active spans between pre/post hook calls.

    Since hooks fire independently, we need a registry to look up
    the active span when the post_* hook fires. Also tracks the
    current "parent" span so child spans nest correctly.

    The parent stack is stored in a ``ContextVar`` so it is isolated
    per task/thread: each async task, each thread-pool worker, and
    each thread gets its own independent stack. This prevents
    concurrent sessions (e.g. a cron job and a chat running at the
    same time) from corrupting each other's span hierarchy.
    """

    def __init__(self):
        # key = f"{tool_name}:{task_id}" or f"llm:{session_id}"
        self._active_spans: Dict[str, Any] = {}

    def _parent_stack(self) -> list:
        """Return this context's parent span stack, creating it if needed."""
        stack = _PARENT_STACK.get()
        if stack is None:
            stack = []
            _PARENT_STACK.set(stack)
        return stack

    def start_span(self, key: str, span, parent=None) -> None:
        """Store an active span by key."""
        self._active_spans[key] = span
        if parent:
            span._otel_parent = parent

    def push_parent(self, span) -> None:
        """Mark a span as the current parent on this thread."""
        self._parent_stack().append(span)

    def pop_parent(self) -> None:
        """Remove the current parent span on this thread."""
        stack = self._parent_stack()
        if stack:
            stack.pop()

    def get_current_parent(self):
        """Get the active parent span on this thread, or None."""
        stack = self._parent_stack()
        return stack[-1] if stack else None

    def end_span(self, key: str, attributes: dict = None, status: str = None, error_message: str = None) -> None:
        """End and remove a tracked span.

        Args:
            key: The tracking key for the span
            attributes: Final attributes to set before ending
            status: "ok" or "error" (defaults to "ok" if None)
            error_message: Error description if status is "error"
        """
        span = self._active_spans.pop(key, None)
        if span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)

            # Set status
            if status == "error":
                span.set_status(Status(status_code=StatusCode.ERROR, description=error_message or ""))
            elif status == "ok":
                # Set an explicit empty description so backends don't render "None".
                span.set_status(Status(status_code=StatusCode.OK, description=""))

            span.end()

    def get_span(self, key):
        """Get an active span by key."""
        return self._active_spans.get(key)

    def end_all(self) -> None:
        """End all remaining spans (cleanup).

        Only clears this context's parent stack — other tasks/threads
        are untouched.
        """
        for key in list(self._active_spans.keys()):
            self.end_span(key)
        self._active_spans.clear()
        stack = _PARENT_STACK.get()
        if stack is not None:
            stack.clear()


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
        self._initialized = False
        self._span_processor = None
        # LangSmith backend (None when using OTLP)
        self._langsmith = None
        # Metrics
        self._meter = None
        self._meter_provider = None
        self._metric_reader = None
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

    def init(self, endpoint: str = None) -> bool:
        """Initialize the tracer from environment variables.

        Detection order (first match wins):
          1. LangSmith  — LANGSMITH_TRACING=true + LANGSMITH_API_KEY
          2. Langfuse   — OTEL_LANGFUSE_* or standard LANGFUSE_* credentials
          3. SigNoz     — OTEL_SIGNOZ_ENDPOINT (+ optional OTEL_SIGNOZ_INGESTION_KEY)
          4. Jaeger     — OTEL_JAEGER_ENDPOINT (traces only, no metrics)
          5. Tempo      — OTEL_TEMPO_ENDPOINT (traces only, no metrics)
          6. Phoenix    — OTEL_PHOENIX_ENDPOINT (or explicit *endpoint* arg)

        Returns True if a backend was initialized.
        """
        if not _OTEL_AVAILABLE:
            print("[hermes-otel] ✗ OpenTelemetry packages not available")
            return False

        if not self.config.enabled:
            print("[hermes-otel] ✗ Disabled via config (enabled=false)")
            return False

        # ── 1. LangSmith (HTTP API, not OTLP) ───────────────────────────
        langsmith_tracing = os.getenv("LANGSMITH_TRACING", "").strip().lower()
        langsmith_api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
        if langsmith_tracing == "true" and langsmith_api_key:
            return self._init_langsmith()

        # ── 2. Langfuse (OTLP + Basic Auth) ──────────────────────────────
        langfuse_pub = (
            os.getenv("OTEL_LANGFUSE_PUBLIC_API_KEY", "").strip()
            or os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        )
        langfuse_sec = (
            os.getenv("OTEL_LANGFUSE_SECRET_API_KEY", "").strip()
            or os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        )
        if langfuse_pub and langfuse_sec:
            langfuse_endpoint = os.getenv("OTEL_LANGFUSE_ENDPOINT", "").strip()
            if not langfuse_endpoint:
                base_url = os.getenv("LANGFUSE_BASE_URL", "").strip().rstrip("/")
                langfuse_endpoint = (
                    f"{base_url}/api/public/otel" if base_url
                    else "https://cloud.langfuse.com/api/public/otel"
                )
            auth_b64 = base64.b64encode(
                f"{langfuse_pub}:{langfuse_sec}".encode()
            ).decode()
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "x-langfuse-ingestion-version": "4",
            }
            return self._init_otlp(langfuse_endpoint, headers=headers,
                                   backend_name="Langfuse")

        # ── 3. SigNoz (OTLP, optional ingestion-key header) ─────────────
        signoz_endpoint = os.getenv("OTEL_SIGNOZ_ENDPOINT", "").strip()
        if signoz_endpoint:
            signoz_key = os.getenv("OTEL_SIGNOZ_INGESTION_KEY", "").strip()
            # signoz-ingestion-key is required for SigNoz Cloud and ignored
            # by self-hosted collectors, so it's safe to omit for localhost.
            headers = {"signoz-ingestion-key": signoz_key} if signoz_key else None
            return self._init_otlp(signoz_endpoint, headers=headers,
                                   backend_name="SigNoz")

        # ── 4. Jaeger (OTLP HTTP, traces only — no auth) ────────────────
        jaeger_endpoint = os.getenv("OTEL_JAEGER_ENDPOINT", "").strip()
        if jaeger_endpoint:
            return self._init_otlp(jaeger_endpoint, backend_name="Jaeger")

        # ── 5. Tempo (OTLP HTTP, traces only — no auth) ─────────────────
        tempo_endpoint = os.getenv("OTEL_TEMPO_ENDPOINT", "").strip()
        if tempo_endpoint:
            return self._init_otlp(tempo_endpoint, backend_name="Tempo")

        # ── 6. Phoenix / generic OTLP (no auth) ─────────────────────────
        endpoint = endpoint or os.getenv("OTEL_PHOENIX_ENDPOINT", "").strip()
        if endpoint:
            return self._init_otlp(endpoint, backend_name="Phoenix")

        print("[hermes-otel] ✗ No backend configured "
              "(set OTEL_PHOENIX_ENDPOINT, OTEL_SIGNOZ_ENDPOINT, "
              "OTEL_JAEGER_ENDPOINT, OTEL_TEMPO_ENDPOINT, "
              "Langfuse credentials, or LANGSMITH_TRACING)")
        return False

    # ── Backend initializers ─────────────────────────────────────────────

    def _init_langsmith(self) -> bool:
        """Initialize LangSmith backend from environment variables."""
        from .langsmith_backend import LangSmithBackend

        try:
            backend = LangSmithBackend.from_env()
            if backend is None:
                return False
            self._langsmith = backend
            self._initialized = True
            print(f"[hermes-otel] ✓ LangSmith at {backend.endpoint}")
            return True
        except Exception as e:
            print(f"[hermes-otel] ✗ LangSmith init failed: {e}")
            return False

    def _init_otlp(self, endpoint: str, headers: dict = None,
                   backend_name: str = "OTLP") -> bool:
        """Initialize an OTLP tracer (shared by Phoenix, Langfuse, SigNoz).

        Args:
            endpoint:     OTLP HTTP trace endpoint URL.
            headers:      Optional HTTP headers (e.g. Langfuse Basic Auth,
                          SigNoz ingestion key).
            backend_name: Display name for log messages.
        """
        try:
            resource_attrs: Dict[str, Any] = {"service.name": "hermes-agent"}
            if self.config.global_tags:
                resource_attrs.update(self.config.global_tags)
            if self.config.resource_attributes:
                resource_attrs.update(self.config.resource_attributes)
            project_name = (
                self.config.project_name
                or os.getenv("OTEL_PROJECT_NAME", "").strip()
            )
            if project_name:
                resource_attrs["openinference.project.name"] = project_name

            resource = Resource.create(resource_attrs)

            provider_kwargs: Dict[str, Any] = {"resource": resource}
            if self.config.sample_rate is not None:
                from opentelemetry.sdk.trace.sampling import (
                    ParentBased,
                    TraceIdRatioBased,
                )
                provider_kwargs["sampler"] = ParentBased(
                    TraceIdRatioBased(self.config.sample_rate)
                )
            provider = TracerProvider(**provider_kwargs)

            merged_headers = dict(headers or {})
            if self.config.headers:
                merged_headers.update(self.config.headers)
            exporter_headers = merged_headers or None

            exporter = OTLPSpanExporter(endpoint=endpoint, headers=exporter_headers)
            # BatchSpanProcessor: runs a background worker that drains the
            # queue in batches. span.end() becomes a non-blocking enqueue,
            # so tool-call / api-request hooks don't stall on slow backends.
            # Trade-off: up to `schedule_delay_millis` of spans stay in
            # memory until exported; we flush explicitly on session end
            # and via atexit so users don't lose data on graceful shutdown.
            processor = BatchSpanProcessor(
                exporter,
                max_queue_size=self.config.span_batch_max_queue_size,
                schedule_delay_millis=self.config.span_batch_schedule_delay_ms,
                max_export_batch_size=self.config.span_batch_max_export_batch_size,
                export_timeout_millis=self.config.span_batch_export_timeout_ms,
            )
            provider.add_span_processor(processor)
            self._span_processor = processor

            trace.set_tracer_provider(provider)
            self.tracer = trace.get_tracer("hermes-otel-plugin")

            self._init_metrics(endpoint, resource, backend_name, headers=exporter_headers)
            self._initialized = True
            self._register_atexit_flush()

            if not self.config.capture_previews:
                print("[hermes-otel] ⚠ capture_previews=false — input/output values suppressed")
            print(f"[hermes-otel] ✓ {backend_name} at {endpoint}")
            return True
        except Exception as e:
            print(f"[hermes-otel] ✗ {backend_name} init failed: {e}")
            return False

    def _init_metrics(self, traces_endpoint: str, resource: Resource,
                      backend_name: str = "Phoenix",
                      headers: dict = None) -> bool:
        """Initialize metrics (MeterProvider) alongside tracer.

        Langfuse does not support OTLP metrics ingestion, so metrics are
        skipped for that backend.  For Phoenix and SigNoz, the metrics
        endpoint is derived from the traces endpoint (e.g. /v1/traces ->
        /v1/metrics).  Headers (e.g. SigNoz ingestion key) are reused.
        """
        if not _METRICS_AVAILABLE:
            return True

        if backend_name in ("Langfuse", "Jaeger", "Tempo"):
            debug_log(f"Metrics skipped ({backend_name} does not support OTLP metrics)")
            return True

        # Derive the metrics endpoint from the traces endpoint.
        # e.g. http://localhost:6006/v1/traces -> http://localhost:6006/v1/metrics
        if traces_endpoint.endswith("/v1/traces"):
            metrics_endpoint = traces_endpoint[:-len("/v1/traces")] + "/v1/metrics"
        else:
            metrics_endpoint = traces_endpoint

        try:
            exporter = OTLPMetricExporter(endpoint=metrics_endpoint, headers=headers)
            self._metric_reader = PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=self.config.flush_interval_ms,
            )
            self._meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[self._metric_reader],
            )
            metrics.set_meter_provider(self._meter_provider)
            self._meter = metrics.get_meter("hermes-otel-plugin")

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

            debug_log("Metrics initialized")
            return True

        except Exception as e:
            debug_log(f"Metrics init failed: {e}")
            return False

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

    def start_span(self, name: str, key: str, kind: str = "general",
                   attributes: dict = None, session_id: Optional[str] = None):
        """Create and track a new span.

        Args:
            session_id: When provided, the span key is linked to this session
                        so the orphan sweep can finalize it if the session
                        exceeds root_span_ttl_ms.
        """
        if not self._initialized:
            return NoopSpan()

        if session_id:
            self._session_keys.setdefault(session_id, set()).add(key)

        # LangSmith mode — HTTP only
        if self._langsmith:
            parent_run = self.spans.get_current_parent()
            run_obj = self._langsmith.start_span(
                name, key, kind, attributes, parent_run=parent_run,
            )
            if run_obj is None:
                return NoopSpan()
            self.spans.start_span(key, run_obj)
            return run_obj

        # OTLP mode (Phoenix/Langfuse)
        if not self.tracer:
            return NoopSpan()

        try:
            attrs = dict(attributes or {})

            # OpenInference semantic conventions — Phoenix recognizes these
            kind_value = self._KIND_MAP.get(kind, "GENERAL")
            attrs["traceloop.span.kind"] = kind_value
            attrs["openinference.span.kind"] = kind_value

            # Check for active parent — enables nesting
            parent = self.spans.get_current_parent()
            span_ctx = None
            if parent is not None and hasattr(parent, "get_span_context"):
                span_ctx = set_span_in_context(parent)

            span = self.tracer.start_span(name, attributes=attrs, context=span_ctx)
            self.spans.start_span(key, span)
            debug_log(f"start_span: {name} (key={key}, kind={kind_value})")
            return span
        except Exception as e:
            debug_log(f"Error starting span '{name}': {e}")
            return NoopSpan()

    def end_span(self, key: str, attributes: dict = None, status: str = None, error_message: str = None):
        """End a tracked span by its key."""
        try:
            # LangSmith mode — HTTP
            if self._langsmith:
                run = self.spans.get_span(key)
                if run:
                    self._langsmith.end_span(run, attributes=attributes,
                                             status=status, error_message=error_message)
                    # Remove from active spans directly (bypass SpanTracker.end_span
                    # which tries to call .end() — but LangSmith runs are dicts)
                    self.spans._active_spans.pop(key, None)
            else:
                # OTLP mode — just enqueue. BatchSpanProcessor handles
                # export asynchronously. on_session_end / atexit flush
                # when data actually needs to be visible.
                self.spans.end_span(key, attributes=attributes, status=status, error_message=error_message)
            # Remove this key from any session's active-key set.
            for sid, keys in list(self._session_keys.items()):
                keys.discard(key)
                if not keys:
                    self._session_keys.pop(sid, None)
            debug_log(f"end_span: key={key}")
        except Exception as e:
            debug_log(f"Error ending span (key={key}): {e}")

    # ── Turn registry (orphan sweep) ─────────────────────────────────────

    def register_turn(self, session_id: str) -> None:
        """Record the start time of a session for TTL tracking."""
        if not session_id:
            return
        self._turn_started_at[session_id] = time.perf_counter()

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
            sid for sid, started_at in self._turn_started_at.items()
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
        stack = _PARENT_STACK.get()
        if stack is not None:
            stack.clear()

    def _force_flush(self):
        """Force export of all buffered spans and metrics.

        Called:
          - at the end of each session (so UI sees traces promptly)
          - on process shutdown via atexit (so graceful exit loses nothing)
        Per-span flushing is deliberately NOT done — it would defeat the
        whole purpose of the BatchSpanProcessor queue.
        """
        if self._span_processor:
            try:
                self._span_processor.force_flush(timeout_millis=2000)
            except Exception:
                pass
        if self._meter_provider:
            try:
                self._meter_provider.force_flush(timeout_millis=2000)
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
