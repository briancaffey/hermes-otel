"""OpenTelemetry tracer for Hermes plugin.

Provides a singleton tracer manager that fans out spans/metrics to one or more
collector backends (Phoenix, Langfuse, SigNoz, Jaeger, Tempo, LGTM, Uptrace,
OpenObserve, Parseable, or any other OTLP-compatible collector via the ``otlp`` type).
When multiple backends are configured the SDK's TracerProvider holds one
``BatchSpanProcessor`` per backend — each processor owns its own background
worker thread, so no single slow collector can block the agent's hot path or
delay export to the others. A shared ``MeterProvider`` similarly fans metrics
out via one ``PeriodicExportingMetricReader`` per backend that supports them.

This module owns :class:`HermesOTelPlugin` (init / shutdown, span start / end,
``record_metric``, ``emit_event``, the session facade) and the singleton. The
pieces it used to carry live next door since #405 and are re-exported here:

- :mod:`.pipeline` — the Resource, the span processors and metric readers,
  the exporter wrappers, the logs pipeline;
- :mod:`.instruments` — the metric instrument table, Views and temporality;
- :mod:`.host_bridge` — the host-metrics sampler wiring;
- :mod:`.flush` — the shutdown flush and the background turn-end flush.
"""

from __future__ import annotations

import atexit
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import backends as _backends
from . import flush as _flush
from . import host_bridge as _host
from . import pipeline as _pipeline
from .backend_config import BackendConfig
from .backend_specs import ENV_PRIORITY
from .backends import _ResolvedBackend
from .debug_utils import (
    close_debug_log,
    debug_log,
    install_sdk_log_forwarding,
    logger,
    remove_sdk_log_forwarding,
)
from .hermes_home import resolve_hermes_home, resolve_profile_name
from .instruments import (  # noqa: F401 — re-exported
    _CAPPED_LABELS,
    _INSTRUMENTS,
    CLAUDE_CODE_REPLACES,
    COMPAT_SESSION_LABEL,
    HOST_METRIC_NAMES,
    InstrumentSpec,
    metric_otlp_name,
    metric_preferred_aggregation,
    metric_temporality_map,
    metric_views,
)
from .pipeline import (  # noqa: F401 — re-exported
    _OTEL_AVAILABLE,
    _SERVICE_INSTANCE_ID,
    _LiveSpanProcessor,
    _MCPPingFilterProcessor,
    _RootUsageStrippingExporter,
    is_mcp_keepalive_ping,
    strip_root_usage,
)
from .plugin_config import HermesOtelConfig, load_config
from .session_state import PerSession, SessionState
from .severity import severity_number
from .span_tracker import SpanTracker, sweep_attributes

try:
    from opentelemetry.trace import INVALID_SPAN, set_span_in_context
except ImportError:  # pragma: no cover — plugin is unusable without OTel
    # If OTel itself is missing we short-circuit via is_enabled=False before
    # ever returning this; the None is just to keep module-level references valid.
    INVALID_SPAN = None  # type: ignore[assignment]


class HermesOTelPlugin:
    """OpenTelemetry tracer manager for the Hermes plugin.

    Owns its ``TracerProvider`` / ``MeterProvider`` / ``LoggerProvider`` and
    installs them as the OTel globals the first time (the SDK allows setting
    each global exactly once per process). ``init()`` is idempotent: a second
    call shuts the previous pipeline down first, so a plugin reload or a
    reconfigure never leaves orphaned exporter threads behind. All hooks
    share this instance via the module-level ``get_tracer()``.
    """

    _instance: Optional["HermesOTelPlugin"] = None

    # OpenInference semantic convention values — Phoenix recognizes these
    _KIND_MAP = {
        "tool": "TOOL",
        "llm": "LLM",
        "general": "GENERAL",
        "agent": "AGENT",
    }
    # Traceloop's ``TraceloopSpanKindValues`` (openllmetry's
    # opentelemetry-semantic-conventions-ai): lowercase ``workflow`` / ``task``
    # / ``agent`` / ``tool`` / ``unknown``. It has no LLM kind — model spans are
    # recognised by their ``gen_ai.*`` attributes — so ``llm`` and ``general``
    # both map to ``task`` (#382).
    _TRACELOOP_KIND_MAP = {
        "tool": "tool",
        "llm": "task",
        "general": "task",
        "agent": "agent",
    }

    def __init__(self, config: Optional[HermesOtelConfig] = None):
        self.tracer = None
        # Span registry: active spans, the per-session parent stacks, the
        # skill / approval / sub-agent records and the turn registry the
        # orphan sweep reads.
        self.spans = SpanTracker()
        # Per-session aggregators for hook callbacks (token totals, I/O,
        # turn summary, tool start times). Owned here so tests that
        # re-create the tracer singleton automatically get a fresh set.
        self.sessions = SessionState()
        self._initialized = False
        # True when the in-process live store (zero-config dashboard) is wired.
        self._live_active = False
        # Our own provider objects (independent of the OTel globals, which can
        # only be set once per process — see pipeline.init_otlp_pipeline).
        self._tracer_provider: Optional[Any] = None
        # OTLP fan-out: one BatchSpanProcessor + one PeriodicExportingMetricReader
        # per backend.
        self._span_processors: List[Any] = []
        self._metric_readers: List[Any] = []
        self._log_processors: List[Any] = []
        # LoggerProvider created when capture_logs is on and at least one
        # log-capable backend is wired up. None otherwise.
        self._logger_provider: Optional[Any] = None
        # Metrics
        self._meter = None
        self._meter_provider = None
        self._instruments: Dict[str, Any] = {}
        # Host metrics sampler (CPU / GPU), created when config.host_metrics is
        # on. One per process; started after backend init, stopped at exit.
        self._host_metrics: Optional[Any] = None
        # Config
        self.config: HermesOtelConfig = config if config is not None else load_config()
        # The home and profile this instance serves. Captured here, while the
        # profile's scope is active (Hermes imports the plugin once per
        # profile, inside that profile's home override), so a multiplexed
        # gateway stamps every profile's spans and metrics with its own name
        # and background threads (which do not inherit the override) never
        # need to resolve it again (#70).
        self.hermes_home: Path = resolve_hermes_home()
        self._label_values: Dict[str, set] = {}
        self._label_cap_logged: set = set()
        self.profile_name: str = resolve_profile_name()
        # Guards against double-registering the atexit flush handler when
        # init() is called multiple times (e.g. in tests / plugin reload).
        self._atexit_registered: bool = False
        self._flush_executor: Any = None
        self._flush_pending = threading.Event()

    # ── Initialization entry point ───────────────────────────────────────

    def init(self, endpoint: str = None) -> bool:
        """Initialize the backends, then start the host-metrics sampler if enabled.

        Idempotent: when a pipeline is already up (plugin reload, reconfigure,
        tests) it is shut down and flushed first, so exporter threads and log
        handlers never stack up.
        """
        if self._initialized:
            self.shutdown()
        self._reset_pipeline_state()
        self._label_values = {}
        self._label_cap_logged = set()
        self.hermes_home = resolve_hermes_home()
        self.profile_name = resolve_profile_name()
        # Debug mode also captures what the SDK's exporters log (#167).
        install_sdk_log_forwarding()
        try:
            ok = self._init_backends(endpoint)
        except Exception:
            # The plugin fails open: a bug in pipeline init disables telemetry
            # for this process, with its traceback in the log, and Hermes keeps
            # running. Configuration errors are reported (and return False)
            # before this point; only unexpected exceptions land here.
            logger.error("[hermes-otel] ✗ pipeline init failed", exc_info=True)
            self._reset_pipeline_state()
            return False
        if ok:
            self._start_host_metrics()
        return ok

    def _reset_pipeline_state(self) -> None:
        """Forget every pipeline object from a previous init (not the config)."""
        # Reachability probe results (#481): filled by a background thread
        # shortly after init; [] until then or when the probe is off.
        self.probe_results = []
        self._probe_thread = None
        self.tracer = None
        self._tracer_provider = None
        self._span_processors = []
        self._metric_readers = []
        self._log_processors = []
        self._logger_provider = None
        self._meter = None
        self._meter_provider = None
        self._live_active = False

    def shutdown(self) -> None:
        """Flush and tear the pipeline down. Idempotent; never raises.

        Force-flushes every processor, shuts down the span processors, metric
        readers and logger provider (their worker threads exit), stops the
        host-metrics sampler, detaches both log handlers (restoring the logger
        level they lowered), ends any still-open spans, drops all per-session
        state and marks the plugin uninitialized. ``init()`` may be called
        again afterwards. Registered with ``atexit`` on first init.
        """
        # End whatever is still open first so it reaches the exporters, then
        # flush, then stop the workers.
        debug_log(f"shutdown: begin (flush pending={self._flush_pending.is_set()})")
        try:
            self.spans.end_all(attributes=sweep_attributes("shutdown"))
        except Exception:
            pass
        try:
            self.flush_wait(timeout_s=2.0)
            self._force_flush()
        except Exception:
            pass
        executor, self._flush_executor = self._flush_executor, None
        if executor is not None:
            try:
                executor.shutdown(wait=False)
            except Exception:
                pass
        self.stop_host_metrics()
        self._host_metrics = None
        # Shutting the provider down shuts every attached processor down (the
        # live store's, the MCP filter wrappers', the OTLP batchers').
        for obj in (self._tracer_provider, self._meter_provider, self._logger_provider):
            if obj is not None:
                try:
                    obj.shutdown()
                except Exception:
                    pass
        if self._tracer_provider is None:
            # Test fixtures wire processors without a provider of ours.
            for obj in list(self._span_processors) + list(self._metric_readers):
                try:
                    obj.shutdown()
                except Exception:
                    pass
        if self._logger_provider is not None:
            try:
                from . import log_handler

                log_handler.uninstall_handler(self.config.log_attach_logger)
            except Exception:
                pass
        self.spans.end_all()
        self.spans.clear_turns()
        self.sessions.clear()
        self._reset_pipeline_state()
        self._initialized = False
        remove_sdk_log_forwarding()
        close_debug_log()

    def _init_backends(self, endpoint: str = None) -> bool:
        """Initialize one or more backends.

        Resolution order:
          1. ``config.backends`` non-empty → fan out to every entry via
             ``_init_otlp_pipeline``.
          2. Explicit ``endpoint`` arg → single Phoenix backend.
          3. Legacy env-var detection (single backend, first match wins, in
             the order of ``backend_specs.ENV_PRIORITY``).

        Returns True if at least one backend was initialized.
        """
        if not _OTEL_AVAILABLE:
            logger.error("[hermes-otel] ✗ OpenTelemetry packages not available")
            return False

        if not self.config.enabled:
            logger.info("[hermes-otel] Disabled via config (enabled=false)")
            return False

        # 1. Multi-backend fan-out from yaml config.
        if self.config.backends:
            backends: List[_ResolvedBackend] = []
            for bc in self.config.backends:
                try:
                    rb = _backends.resolve(bc)
                except Exception as e:
                    logger.warning(f"[hermes-otel] ✗ backend {bc.type!r} skipped: {e}")
                    continue
                if rb is not None:
                    backends.append(rb)
            if not backends:
                logger.warning("[hermes-otel] ✗ config.backends had no valid entries")
                return False
            return self._init_otlp_pipeline(backends)

        # 2. Explicit endpoint arg → single Phoenix backend.
        if endpoint:
            backend = _backends.resolve(BackendConfig(type="phoenix", endpoint=endpoint))
            return self._init_otlp_pipeline([backend])

        # 3. Legacy env-var detection (single backend).
        return self._init_otlp_from_env()

    # ── Backend resolution ───────────────────────────────────────────────

    def _init_otlp_from_env(self) -> bool:
        """First-match-wins single-backend init from environment variables.

        Delegates to :mod:`backends` for resolution and hands the fully
        resolved backend to ``_init_otlp_pipeline`` intact, so env-configured
        backends keep their logs support, per-signal headers and resource
        attributes exactly like ``backends:`` entries do (#90).
        """
        rb = _backends.resolve_from_env()
        if rb is None:
            # Vendor SDK credentials that used to select a backend on their own
            # are ignored without an OTEL_* opt-in (#255); say so, once, so the
            # switch to live-only mode is not silent (#259).
            for hint in _backends.env_opt_in_hints():
                logger.info("[hermes-otel] %s", hint)
            # No external backend — but the in-process live store still gives a
            # working zero-config dashboard. Stand up a live-only pipeline.
            if self.config.dashboard_live:
                logger.info(
                    "[hermes-otel] No OTLP backend configured — running in "
                    "live-only mode (in-process dashboard store)."
                )
                return self._init_otlp_pipeline([])
            types = ", ".join(ENV_PRIORITY)
            logger.warning(
                "[hermes-otel] ✗ No backend configured (set OTEL_<TYPE>_ENDPOINT and the "
                f"type's credentials for one of: {types}; or define 'backends:' in "
                "hermes_otel.yaml)"
            )
            return False
        return self._init_otlp_pipeline([rb])

    # ── Pipeline (delegates to .pipeline) ────────────────────────────────

    def _build_resource(self, backends: Optional[List[_ResolvedBackend]] = None) -> Any:
        return _pipeline.build_resource(self, backends)

    def _init_otlp_pipeline(self, backends: List[_ResolvedBackend]) -> bool:
        return _pipeline.init_otlp_pipeline(self, backends)

    def _create_metric_instruments(self) -> None:
        _pipeline.create_metric_instruments(self)

    def _init_logs_pipeline(self, resource: Any, backends: List[_ResolvedBackend]) -> None:
        _pipeline.init_logs_pipeline(self, resource, backends)

    # ── Host metrics (delegates to .host_bridge) ─────────────────────────

    def _ensure_host_sampler(self) -> Optional[Any]:
        return _host.ensure_host_sampler(self)

    def _start_host_metrics(self) -> None:
        _host.start_host_metrics(self)

    def stop_host_metrics(self) -> None:
        _host.stop_host_metrics(self)

    def _mirror_host_sample(self, sample: Any) -> None:
        _host.mirror_host_sample(self, sample)

    def _create_host_metric_instruments(self) -> None:
        _host.create_host_metric_instruments(self)

    @property
    def host_metrics(self) -> Optional[Any]:
        """The running sampler, or None when host metrics are off / unavailable."""
        sampler = self._host_metrics
        return sampler if sampler is not None and sampler.running else None

    # ── Events ───────────────────────────────────────────────────────────

    def emit_event(
        self,
        name: str,
        body: str,
        attributes: Dict[str, Any],
        *,
        severity: str = "INFO",
        session_id: Optional[str] = None,
        span: Any = None,
    ) -> bool:
        """Emit one structured event (#267) on the plugin's own log scope.

        No-op unless ``logs.events.enabled`` is on and a log pipeline exists.
        The record carries the trace context of *span* (default: the session's
        innermost open span) so the enricher attributes it as ``context``,
        plus the session attributes; it is then redacted like any record.
        Never raises.
        """
        if not self.config.log_events or self._logger_provider is None:
            return False
        try:
            from . import log_events as _ev

            attrs = _ev.strip_none(attributes)
            # The OTLP ``event_name`` field is dropped by some backends
            # (OpenObserve keeps no column for it), so the name travels as an
            # attribute too; the adapters read either (#268).
            attrs.setdefault("event.name", name)
            if session_id:
                attrs.setdefault("hermes.session_id", str(session_id))
                attrs.setdefault("gen_ai.conversation.id", str(session_id))
                if span is None:
                    span = self.spans.get_current_parent(
                        str(session_id)
                    ) or self.spans.get_session_root(str(session_id))
            context = set_span_in_context(span) if span is not None else None
            otel_logger = self._logger_provider.get_logger(_ev.SCOPE_NAME, self.plugin_version)
            from opentelemetry._logs import LogRecord, SeverityNumber

            # ``Logger.emit(record)`` is the form every supported SDK has; the
            # keyword form (``emit(event_name=..., body=...)``) is newer than
            # the 1.35 floor. ``LogRecord(event_name=...)`` exists since 1.35.
            now = time.time_ns()
            record = LogRecord(
                timestamp=now,
                observed_timestamp=now,
                context=context,
                severity_text=severity,
                severity_number=SeverityNumber(severity_number(severity, 9)),
                body=body,
                attributes=attrs,
                event_name=name,
            )
            otel_logger.emit(record)
            return True
        except Exception as exc:  # telemetry must never raise into the agent
            debug_log(f"event {name} not emitted: {type(exc).__name__}: {exc}")
            return False

    @property
    def plugin_version(self) -> Optional[str]:
        """The plugin's own version (``plugin.yaml``), for the events scope."""
        cached = getattr(self, "_plugin_version_cache", None)
        if cached is not None:
            return cached or None
        try:
            import re as _re

            text = (Path(__file__).resolve().parent / "plugin.yaml").read_text(encoding="utf-8")
            m = _re.search(r"^version:\s*[\"']?([^\"'\n]+)", text, _re.M)
            self._plugin_version_cache = m.group(1).strip() if m else ""
        except Exception:
            self._plugin_version_cache = ""
        return self._plugin_version_cache or None

    def active_span_for_logs(self) -> Optional[Tuple[str, Any]]:
        """``(session_id, span)`` a log record written now should be correlated with.

        The innermost open span of the single session with a turn in flight
        (its root when nothing is nested), else ``None``: with several sessions
        active a line is left unattributed rather than attributed wrongly, the
        policy the live store's handler already follows (#186).
        """
        try:
            found = self.spans.single_active_session()
        except Exception:
            return None
        if not found:
            return None
        session_id, root = found
        span = self.spans.get_current_parent(str(session_id)) or root
        return str(session_id), span

    # ── Metrics ──────────────────────────────────────────────────────────

    def _bound_labels(self, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """Fold an open-ended label's values past ``metrics_label_limit`` into ``other``."""
        limit = int(getattr(self.config, "metrics_label_limit", 100) or 0)
        if limit <= 0:
            return attributes
        seen = self._label_values
        for key in _CAPPED_LABELS.intersection(attributes):
            value = str(attributes[key])
            known = seen.setdefault(key, set())
            if value in known:
                continue
            if len(known) < limit:
                known.add(value)
            else:
                attributes[key] = "other"
                if key not in self._label_cap_logged:
                    self._label_cap_logged.add(key)
                    debug_log(
                        f"metric label {key!r} reached metrics_label_limit={limit}; "
                        "further values fold into 'other'"
                    )
        return attributes

    def record_metric(self, name: str, value: float, attributes: dict = None):
        """Record one point for the hook-side key ``name`` (see ``_INSTRUMENTS``).

        The live store gets the point first, under the OTLP name, so the
        dashboard and the backends agree; it works with no MeterProvider at
        all (the zero-config path). GenAI-convention instruments are gated by
        ``emit_genai_metrics`` here, once, rather than at every call site.
        """
        spec = _INSTRUMENTS.get(name)
        if spec is not None and spec.genai and not self.config.emit_genai_metrics:
            return
        # Claude Code metric-name compatibility (#485): a compat instrument
        # is recorded only when its family is switched on; in the exclusive
        # mode the ``hermes.*`` twin it replaces is kept out of the OTLP
        # export (the live store still gets it, so the plugin's own dashboard
        # keeps working). Compat points never reach the live store.
        compat_mode = self.config.metric_name_compat
        is_compat = spec is not None and bool(spec.compat)
        if is_compat and compat_mode not in (spec.compat, "both"):
            return
        export = not (
            spec is not None and compat_mode == "claude_code" and name in CLAUDE_CODE_REPLACES
        )
        otlp_name = spec.name if spec is not None else name
        # ``profile`` is a bounded label (one value per plugin instance), so
        # a backend can split every series by agent (#70).
        attributes = {"profile": self.profile_name, **(attributes or {})}
        # A session id is Claude Code's standard metric attribute and nobody
        # else's: it survives only on a compat instrument, and only when the
        # user opted in.
        session_label = attributes.pop(COMPAT_SESSION_LABEL, None)
        if is_compat and session_label and self.config.metric_name_compat_session_id:
            attributes[COMPAT_SESSION_LABEL] = str(session_label)
        attributes = self._bound_labels(attributes)
        if self._live_active and not is_compat:
            try:
                from .live_store import get_live_store

                store = get_live_store()
                if store is not None:
                    store.add_metric(otlp_name, value, attributes or {}, time.time_ns())
            except Exception:  # pragma: no cover — never break the hot path
                pass

        instrument = self._instruments.get(name) if self._meter else None
        if instrument is None or spec is None or not export:
            return
        attrs = dict(attributes or {})
        try:
            if spec.value == "record":
                instrument.record(int(value) if spec.unit == "{token}" else value, attrs)
            elif spec.value == "one":
                instrument.add(1, attrs)
            elif spec.value == "int":
                instrument.add(int(value), attrs)
            else:
                instrument.add(value, attrs)
        except Exception:  # pragma: no cover — never break the hot path
            pass

    # ── Spans ────────────────────────────────────────────────────────────

    def start_span(
        self,
        name: str,
        key: str,
        kind: str = "general",
        attributes: dict = None,
        session_id: Optional[str] = None,
        parent: Any = None,
        links: Optional[list] = None,
        sweep_with_turn: bool = True,
    ):
        """Create and track a new span.

        Args:
            session_id: When provided, the span key is linked to this session
                        so the orphan sweep can finalize it if the session
                        exceeds root_span_ttl_ms. ``session_id`` still picks
                        the parent to nest under.
            sweep_with_turn: ``False`` keeps the span out of the session's
                        turn sweep: a delegation span outlives the turn that
                        opened it and has its own TTL (#392).
            parent: Explicit parent span to nest under, overriding the
                    session-keyed parent stack. Used for sub-agent delegation,
                    where a child's root span must nest under the delegation
                    span opened in the *parent* session (a different stack).
            links: Optional list of ``opentelemetry.trace.Link`` objects to
                   attach at span creation (links can't be added later). Used
                   as the cross-process fallback for sub-agent correlation when
                   a live parent span isn't available to nest under.
        """
        if not self._initialized:
            return INVALID_SPAN

        if session_id and sweep_with_turn:
            self.spans.link_key(session_id, key)

        if not self.tracer:
            return INVALID_SPAN

        try:
            attrs = dict(attributes or {})

            # Dual convention: OpenInference (Phoenix) and Traceloop (openllmetry,
            # opentelemetry-mcp) each get their own vocabulary (#382).
            attrs["openinference.span.kind"] = self._KIND_MAP.get(kind, "GENERAL")
            attrs["traceloop.span.kind"] = self._TRACELOOP_KIND_MAP.get(kind, "unknown")

            # Check for active parent — an explicit ``parent`` wins (sub-agent
            # delegation), otherwise prefer the session-keyed stack so nesting
            # survives hermes' cross-thread hook dispatch.
            effective_parent = (
                parent if parent is not None else self.spans.get_current_parent(session_id)
            )
            span_ctx = None
            if effective_parent is not None and hasattr(effective_parent, "get_span_context"):
                span_ctx = set_span_in_context(effective_parent)

            span = self.tracer.start_span(name, attributes=attrs, context=span_ctx, links=links)
            self.spans.start_span(key, span)
            debug_log(f"start_span: {name} (key={key}, kind={kind})")
            return span
        except Exception as e:
            debug_log(f"Error starting span '{name}': {e}")
            return INVALID_SPAN

    def end_span(
        self, key: str, attributes: dict = None, status: str = None, error_message: str = None
    ):
        """End a tracked span by its key."""
        try:
            # Just enqueue: the BatchSpanProcessor exports asynchronously;
            # on_session_end / atexit flush when data needs to be visible.
            self.spans.end_span(
                key, attributes=attributes, status=status, error_message=error_message
            )
            self.spans.unlink_key(key)
            debug_log(f"end_span: key={key}")
        except Exception as e:
            debug_log(f"Error ending span (key={key}): {e}")

    # ── Turn registry (orphan sweep) ─────────────────────────────────────

    def register_turn(self, session_id: str, started_at: Optional[float] = None) -> None:
        """Record the start time of a session for TTL tracking (see ``SpanTracker``)."""
        self.spans.register_turn(session_id, started_at)

    def unregister_turn(self, session_id: str) -> None:
        """Remove a session from the turn registry (normal end)."""
        self.spans.unregister_turn(session_id)

    def sweep_expired_turns(self) -> list:
        """Finalize any sessions whose start time exceeds root_span_ttl_ms.

        Returns the list of finalized session_ids (useful for tests).
        """
        expired = self.spans.expired_turns(self.config.root_span_ttl_ms / 1000.0)
        for sid in expired:
            self._finalize_orphan(sid)
        self.sweep_expired_delegations()
        return expired

    def sweep_expired_delegations(self) -> list:
        """Close delegation spans whose ``subagent_stop`` never arrived within
        ``subagent_span_ttl_ms``; returns their child session ids."""
        expired = self.spans.expired_delegations(self.config.subagent_span_ttl_ms / 1000.0)
        for child in expired:
            self.spans.pop_subagent(child)
            self.spans.end_delegation(child)
            self.sessions.pop_delegation(child)
            self.end_span(
                f"subagent:{child}", attributes={"hermes.subagent.swept": True}, status="ok"
            )
        return expired

    def close_failed_turn(self, session_id: str) -> bool:
        """Close a turn left open because every API attempt failed (#467).

        Hermes fires neither post_llm_call nor on_session_end on that path, so
        in a gateway the session's next message would otherwise land in the
        failed turn's trace. Called from pre_llm_call; returns True when a
        stale failed turn was closed (as failed, with its open children).
        """
        ps = self.sessions.peek(session_id)
        if ps is None or not ps.last_api_failed:
            return False
        if not self.spans.has_span(f"session:{session_id}"):
            return False
        self._finalize_orphan(session_id, reason="turn_failed")
        self.unregister_turn(session_id)
        return True

    def end_leftover_children(self, session_id: str, reason: str) -> int:
        """End every child span still open under ``session_id`` and say why (#477).

        A tool whose ``post_tool_call`` never fired, an ``api.*`` span cut by a
        stream abort or an approval that timed out used to stay open past
        the turn's end: never exported until shutdown, and exported with no
        trace of what happened. Each one is ended with status OK (a sweep is
        not an error of the operation) and ``hermes.span.ended_by=sweep`` +
        ``hermes.span.sweep_reason=<reason>``, and counted in
        ``hermes.span.swept.count``. Ends children before the root so the
        root still contains them. Returns how many were ended.
        """
        session_key = f"session:{session_id}"
        swept = 0
        for key in self.spans.pop_session_keys(session_id):
            if key == session_key or not self.spans.has_span(key):
                continue
            self.end_span(key, attributes=sweep_attributes(reason), status="ok")
            self.record_metric(
                "span_swept", 1, {"span_kind": key.split(":", 1)[0], "reason": reason}
            )
            swept += 1
        if swept:
            debug_log(f"swept {swept} leftover span(s) of session={session_id}: {reason}")
        return swept

    def end_skill_spans(self, session_id: str, result_status: str) -> None:
        """Close the ``skill.<name>`` windows opened this turn (``skill_spans``).

        Called before the root is ended so they close as its children;
        overlapping skills each get their own close. Status is OK: a skill
        being active is never itself an error; the turn's outcome rides on
        ``hermes.skill.result_status``.
        """
        if not self.config.skill_spans:
            return
        for _name, key in self.spans.pop_skill_spans(session_id).items():
            self.end_span(
                key, attributes={"hermes.skill.result_status": result_status}, status="ok"
            )

    def _finalize_orphan(self, session_id: str, reason: str = "ttl") -> None:
        """End any still-active spans for a timed-out session.

        Marks the session span (if present) with `hermes.turn.final_status=timed_out`
        and status OK (per PRD: timeouts must not inflate error rates).
        """
        # End non-session active spans first (api.*, tool.*, llm.*) so the
        # session span ends last and contains them in the hierarchy.
        session_key = f"session:{session_id}"
        self.end_leftover_children(session_id, reason)

        if self.spans.has_span(session_key):
            # A turn the gateway stopped (agent_loop_stopped) whose end never
            # arrived is interrupted, not timed out (#396); one whose every
            # API attempt failed and whose end never came is failed (#467).
            ps = self.sessions.peek(session_id) or PerSession()
            attributes, status, error_message = (
                ({"hermes.turn.final_status": "interrupted"}, "ok", None)
                if ps.stop_reason
                else ps.api_failure_close("timed_out")
            )
            self.end_span(
                session_key, attributes=attributes, status=status, error_message=error_message
            )
        # Drop the expired session's parent stack, skill spans, delegation
        # record and aggregator — the sweep is a safety net and later hooks
        # rebuild state. The ContextVar stack is deliberately untouched: it
        # belongs to whichever session runs in the *calling* context (#105).
        self.spans.drop_session(session_id)
        self.sessions.pop(session_id)

    # ── Flushing (delegates to .flush) ───────────────────────────────────

    def _force_flush(self, timeout_millis: int = 2000, providers: bool = True) -> None:
        _flush.force_flush(self, timeout_millis, providers)

    def _force_flush_providers(self, timeout_millis: int = 2000) -> None:
        _flush.force_flush_providers(self, timeout_millis)

    def flush_async(self, timeout_millis: int = 500) -> bool:
        """Queue a background flush of spans, metrics and logs (#91); see :mod:`.flush`."""
        return _flush.flush_async(self, timeout_millis)

    def flush_wait(self, timeout_s: float = 5.0) -> None:
        """Wait for a queued background flush to finish (tests, shutdown)."""
        _flush.flush_wait(self, timeout_s)

    def _register_atexit_flush(self) -> None:
        """Register a single atexit hook that flushes and shuts the pipeline down.

        Idempotent across multiple init() calls (plugin reload, tests). atexit
        runs LIFO, so this fires before the SDK's own per-processor hooks.
        """
        if self._atexit_registered:
            return
        atexit.register(self.shutdown)
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
