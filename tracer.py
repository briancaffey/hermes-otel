"""OpenTelemetry tracer for Hermes plugin.

Provides a singleton tracer manager that fans out spans/metrics to one or more
collector backends (Phoenix, Langfuse, SigNoz, Jaeger, Tempo, generic OTLP).
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
import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .debug_utils import debug_log
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


# Backend types whose collectors do not accept OTLP metrics. Pure trace-only.
_TRACES_ONLY = {"langfuse", "jaeger", "tempo"}


@dataclass
class _ResolvedBackend:
    """A backend ready to wire into the OTLP pipeline.

    Headers may already include backend-specific auth (e.g. Langfuse Basic
    Auth, SigNoz ingestion key); the pipeline merges the global
    ``config.headers`` on top before constructing the exporter.
    """

    type: str
    endpoint: str
    display_name: str = "OTLP"
    headers: Optional[Dict[str, str]] = None
    supports_metrics: bool = True


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
        self._span_processor = None
        self._metric_reader = None
        self._backend_summaries: List[str] = []
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
            print("[hermes-otel] ✗ OpenTelemetry packages not available")
            return False

        if not self.config.enabled:
            print("[hermes-otel] ✗ Disabled via config (enabled=false)")
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
                    print(f"[hermes-otel] ✗ backend {bc.type!r} skipped: {e}")
                    continue
                if rb is not None:
                    backends.append(rb)
            if not backends:
                print("[hermes-otel] ✗ config.backends had no valid entries")
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

        Each branch routes through ``_init_otlp`` so the existing unit-test
        suite (which patches ``_init_otlp``) keeps verifying the routing.
        """
        # Langfuse
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
                root = base_url if base_url else "https://cloud.langfuse.com"
                langfuse_endpoint = f"{root}/api/public/otel/v1/traces"
            auth_b64 = base64.b64encode(f"{langfuse_pub}:{langfuse_sec}".encode()).decode()
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "x-langfuse-ingestion-version": "4",
            }
            return self._init_otlp(langfuse_endpoint, headers=headers, backend_name="Langfuse")

        # SigNoz
        signoz_endpoint = os.getenv("OTEL_SIGNOZ_ENDPOINT", "").strip()
        if signoz_endpoint:
            signoz_key = os.getenv("OTEL_SIGNOZ_INGESTION_KEY", "").strip()
            headers = {"signoz-ingestion-key": signoz_key} if signoz_key else None
            return self._init_otlp(signoz_endpoint, headers=headers, backend_name="SigNoz")

        # Jaeger
        jaeger_endpoint = os.getenv("OTEL_JAEGER_ENDPOINT", "").strip()
        if jaeger_endpoint:
            return self._init_otlp(jaeger_endpoint, backend_name="Jaeger")

        # Tempo
        tempo_endpoint = os.getenv("OTEL_TEMPO_ENDPOINT", "").strip()
        if tempo_endpoint:
            return self._init_otlp(tempo_endpoint, backend_name="Tempo")

        # Phoenix
        phoenix_endpoint = os.getenv("OTEL_PHOENIX_ENDPOINT", "").strip()
        if phoenix_endpoint:
            return self._init_otlp(phoenix_endpoint, backend_name="Phoenix")

        print(
            "[hermes-otel] ✗ No backend configured "
            "(set OTEL_PHOENIX_ENDPOINT, OTEL_SIGNOZ_ENDPOINT, "
            "OTEL_JAEGER_ENDPOINT, OTEL_TEMPO_ENDPOINT, "
            "Langfuse credentials, or LANGSMITH_TRACING; or define "
            "'backends:' in config.yaml)"
        )
        return False

    def _resolve_backend_config(self, bc: BackendConfig) -> Optional[_ResolvedBackend]:
        """Turn a yaml ``BackendConfig`` into a ready-to-wire backend."""
        t = (bc.type or "").strip().lower()
        display = bc.name or t.capitalize() or "OTLP"
        extra_headers = dict(bc.headers or {})

        if t == "phoenix":
            ep = (bc.endpoint or os.getenv("OTEL_PHOENIX_ENDPOINT", "")).strip()
            if not ep:
                raise ValueError("phoenix requires endpoint")
            return _ResolvedBackend(
                type="phoenix",
                endpoint=ep,
                display_name=display,
                headers=extra_headers or None,
                supports_metrics=self._metrics_for(t, bc.metrics),
            )

        if t == "langfuse":
            pub = self._resolve_secret(
                bc.public_key,
                bc.public_key_env,
                ["OTEL_LANGFUSE_PUBLIC_API_KEY", "LANGFUSE_PUBLIC_KEY"],
            )
            sec = self._resolve_secret(
                bc.secret_key,
                bc.secret_key_env,
                ["OTEL_LANGFUSE_SECRET_API_KEY", "LANGFUSE_SECRET_KEY"],
            )
            if not (pub and sec):
                raise ValueError("langfuse requires public_key and secret_key")
            ep = (bc.endpoint or os.getenv("OTEL_LANGFUSE_ENDPOINT", "")).strip()
            if not ep:
                base = (bc.base_url or os.getenv("LANGFUSE_BASE_URL", "")).strip().rstrip("/")
                root = base if base else "https://cloud.langfuse.com"
                ep = f"{root}/api/public/otel/v1/traces"
            auth = base64.b64encode(f"{pub}:{sec}".encode()).decode()
            headers = {
                "Authorization": f"Basic {auth}",
                "x-langfuse-ingestion-version": "4",
            }
            headers.update(extra_headers)
            return _ResolvedBackend(
                type="langfuse",
                endpoint=ep,
                display_name=display,
                headers=headers,
                supports_metrics=self._metrics_for(t, bc.metrics),
            )

        if t == "signoz":
            ep = (bc.endpoint or os.getenv("OTEL_SIGNOZ_ENDPOINT", "")).strip()
            if not ep:
                raise ValueError("signoz requires endpoint")
            key = self._resolve_secret(
                bc.ingestion_key,
                bc.ingestion_key_env,
                ["OTEL_SIGNOZ_INGESTION_KEY"],
            )
            headers: Dict[str, str] = {}
            if key:
                headers["signoz-ingestion-key"] = key
            headers.update(extra_headers)
            return _ResolvedBackend(
                type="signoz",
                endpoint=ep,
                display_name=display,
                headers=headers or None,
                supports_metrics=self._metrics_for(t, bc.metrics),
            )

        if t == "jaeger":
            ep = (bc.endpoint or os.getenv("OTEL_JAEGER_ENDPOINT", "")).strip()
            if not ep:
                raise ValueError("jaeger requires endpoint")
            return _ResolvedBackend(
                type="jaeger",
                endpoint=ep,
                display_name=display,
                headers=extra_headers or None,
                supports_metrics=self._metrics_for(t, bc.metrics),
            )

        if t == "tempo":
            ep = (bc.endpoint or os.getenv("OTEL_TEMPO_ENDPOINT", "")).strip()
            if not ep:
                raise ValueError("tempo requires endpoint")
            return _ResolvedBackend(
                type="tempo",
                endpoint=ep,
                display_name=display,
                headers=extra_headers or None,
                supports_metrics=self._metrics_for(t, bc.metrics),
            )

        if t in ("otlp", "generic"):
            ep = (bc.endpoint or "").strip()
            if not ep:
                raise ValueError("otlp requires endpoint")
            return _ResolvedBackend(
                type="otlp",
                endpoint=ep,
                display_name=bc.name or "OTLP",
                headers=extra_headers or None,
                supports_metrics=self._metrics_for(t, bc.metrics),
            )

        raise ValueError(f"unknown backend type {bc.type!r}")

    @staticmethod
    def _metrics_for(backend_type: str, override: Optional[bool]) -> bool:
        if override is not None:
            return override
        return backend_type not in _TRACES_ONLY

    @staticmethod
    def _resolve_secret(
        inline: Optional[str],
        env_name: Optional[str],
        fallback_envs: List[str],
    ) -> Optional[str]:
        """Pick the first available secret value. Inline > named env > fallback envs."""
        if inline:
            v = inline.strip()
            if v:
                return v
        if env_name:
            v = os.getenv(env_name, "").strip()
            if v:
                return v
        for name in fallback_envs:
            v = os.getenv(name, "").strip()
            if v:
                return v
        return None

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
            print(f"[hermes-otel] ✓ LangSmith at {backend.endpoint}")
            return True
        except Exception as e:
            print(f"[hermes-otel] ✗ LangSmith init failed: {e}")
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
                    print(f"[hermes-otel] ✗ {b.display_name} traces init failed: {e}")
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
                print(
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

            self._initialized = True
            self._register_atexit_flush()

            if not self.config.capture_previews:
                print("[hermes-otel] ⚠ capture_previews=false — input/output values suppressed")
            if len(self._span_processors) > 1:
                print(
                    f"[hermes-otel] ✓ Multi-backend fan-out active "
                    f"({len(self._span_processors)} collectors, "
                    f"{len(self._metric_readers)} with metrics)"
                )
            return True
        except Exception as e:
            print(f"[hermes-otel] ✗ pipeline init failed: {e}")
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
