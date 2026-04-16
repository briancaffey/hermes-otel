"""OpenTelemetry tracer for Hermes plugin.

Provides singleton tracer manager with span tracking between
pre/post hook calls.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

from .debug_utils import debug_log

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, set_span_in_context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
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
    """

    def __init__(self):
        # key = f"{tool_name}:{task_id}" or f"llm:{session_id}"
        self._active_spans: Dict[str, Any] = {}
        # Stack of parent span contexts (LLM spans push here)
        self._parent_stack: list = []

    def start_span(self, key: str, span, parent=None) -> None:
        """Store an active span by key."""
        self._active_spans[key] = span
        if parent:
            span._otel_parent = parent

    def push_parent(self, span) -> None:
        """Mark a span as the current parent (e.g., LLM call)."""
        self._parent_stack.append(span)

    def pop_parent(self) -> None:
        """Remove the current parent span."""
        if self._parent_stack:
            self._parent_stack.pop()

    def get_current_parent(self):
        """Get the active parent span, or None."""
        return self._parent_stack[-1] if self._parent_stack else None

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
        """End all remaining spans (cleanup)."""
        for key in list(self._active_spans.keys()):
            self.end_span(key)
        self._active_spans.clear()
        self._parent_stack.clear()


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

    def __init__(self):
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

    def init(self, endpoint: str = None) -> bool:
        """Initialize the tracer from environment variables.

        Detection order (first match wins):
          1. LangSmith  — LANGSMITH_TRACING=true + LANGSMITH_API_KEY
          2. Langfuse   — OTEL_LANGFUSE_* or standard LANGFUSE_* credentials
          3. Phoenix    — OTEL_PHOENIX_ENDPOINT (or explicit *endpoint* arg)

        Returns True if a backend was initialized.
        """
        if not _OTEL_AVAILABLE:
            print("[hermes-otel] ✗ OpenTelemetry packages not available")
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

        # ── 3. Phoenix / generic OTLP (no auth) ─────────────────────────
        endpoint = endpoint or os.getenv("OTEL_PHOENIX_ENDPOINT", "").strip()
        if endpoint:
            return self._init_otlp(endpoint, backend_name="Phoenix")

        print("[hermes-otel] ✗ No backend configured "
              "(set OTEL_PHOENIX_ENDPOINT, Langfuse credentials, or LANGSMITH_TRACING)")
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
        """Initialize an OTLP tracer (shared by Phoenix and Langfuse).

        Args:
            endpoint:     OTLP HTTP trace endpoint URL.
            headers:      Optional HTTP headers (e.g. Langfuse Basic Auth).
            backend_name: Display name for log messages.
        """
        try:
            resource_attrs = {"service.name": "hermes-agent"}
            project_name = os.getenv("OTEL_PROJECT_NAME", "").strip()
            if project_name:
                resource_attrs["openinference.project.name"] = project_name

            resource = Resource.create(resource_attrs)
            provider = TracerProvider(resource=resource)

            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
            processor = SimpleSpanProcessor(exporter)
            provider.add_span_processor(processor)
            self._span_processor = processor

            trace.set_tracer_provider(provider)
            self.tracer = trace.get_tracer("hermes-otel-plugin")

            self._init_metrics(endpoint, resource, backend_name)
            self._initialized = True

            print(f"[hermes-otel] ✓ {backend_name} at {endpoint}")
            return True
        except Exception as e:
            print(f"[hermes-otel] ✗ {backend_name} init failed: {e}")
            return False

    def _init_metrics(self, traces_endpoint: str, resource: Resource,
                      backend_name: str = "Phoenix") -> bool:
        """Initialize metrics (MeterProvider) alongside tracer.

        Langfuse does not support OTLP metrics ingestion, so metrics are
        skipped for that backend.  For Phoenix, the metrics endpoint is
        derived from the traces endpoint (e.g. /v1/traces -> /v1/metrics).
        """
        if not _METRICS_AVAILABLE:
            return True

        if backend_name == "Langfuse":
            debug_log("Metrics skipped (Langfuse does not support OTLP metrics)")
            return True

        # Derive the metrics endpoint from the traces endpoint.
        # e.g. http://localhost:6006/v1/traces -> http://localhost:6006/v1/metrics
        if traces_endpoint.endswith("/v1/traces"):
            metrics_endpoint = traces_endpoint[:-len("/v1/traces")] + "/v1/metrics"
        else:
            metrics_endpoint = traces_endpoint

        try:
            exporter = OTLPMetricExporter(endpoint=metrics_endpoint)
            self._metric_reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60000)
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

    def start_span(self, name: str, key: str, kind: str = "general", attributes: dict = None):
        """Create and track a new span."""
        if not self._initialized:
            return NoopSpan()

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
                # OTLP mode
                self.spans.end_span(key, attributes=attributes, status=status, error_message=error_message)
                self._force_flush()
            debug_log(f"end_span: key={key}")
        except Exception as e:
            debug_log(f"Error ending span (key={key}): {e}")

    def _force_flush(self):
        """Force export of all buffered spans and metrics."""
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
