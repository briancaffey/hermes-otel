"""OTel log pipeline for hermes-otel.

Bridges Python's :mod:`logging` to the OTel logs signal. When
:attr:`~hermes_otel.plugin_config.HermesOtelConfig.capture_logs` is true the
tracer wires one :class:`BatchLogRecordProcessor` per log-capable backend and
attaches a :class:`LoggingHandler` to Python's root logger (or a scoped
logger, controlled by ``log_attach_logger``).

One :class:`LoggerProvider` carries every sink. :class:`HermesLogProcessor`
runs first and makes each record truthful and safe: it stamps the trace and
span ids of the turn the line belongs to (three exact attribution tiers, each
named in ``hermes.log.attribution``), adds the session attributes, drops the
host-internal attributes Hermes puts on every record, and redacts secrets
with Hermes's own redactor. :class:`LiveLogProcessor` then mirrors the
enriched record into the live store for the dashboard, and one
:class:`BatchLogRecordProcessor` per log-capable backend exports it. The
live store and every backend therefore see the same record.

Module-level state is deliberately minimal — a single installed handler per
process, whose provider the plugin's :class:`~hermes_otel.pipeline.Pipeline`
holds — so test fixtures that reset the tracer transitively reset the log
pipeline too. The only thing that persists across tracer rebuilds is
Python's global logger hierarchy, which :func:`install_handler` guards
against double-attachment by removing prior instances keyed on a marker
attribute before adding a new one.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import log_events as _ev
from .backends import _ResolvedBackend
from .debug_utils import debug_log, logger
from .pipeline import _LoggingExporter
from .pipeline_env import without_otlp_header_env
from .plugin_config import DEFAULT_LOG_EXCLUDE_LOGGERS, HermesOtelConfig
from .redaction import redact_attributes, redact_text
from .severity import otel_severity, python_level, severity_number

try:
    # opentelemetry-sdk emits a DeprecationWarning on LoggingHandler import
    # pointing users at opentelemetry-instrumentation-logging. That package
    # is a higher-level auto-instrumentation layer; we use the SDK handler
    # directly because it gives explicit control over which logger the
    # handler attaches to and lets us fan out to multiple providers. Revisit
    # if the SDK handler is actually removed (not just deprecated).
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, LogRecordProcessor
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExportResult
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import set_span_in_context

    _LOGS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK missing
    _LOGS_AVAILABLE = False
    OTLPLogExporter = None  # type: ignore[assignment]
    LoggerProvider = None  # type: ignore[assignment]
    LoggingHandler = None  # type: ignore[assignment]
    BatchLogRecordProcessor = None  # type: ignore[assignment]
    LogExportResult = None  # type: ignore[assignment]
    LogRecordProcessor = object  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment]
    set_span_in_context = None  # type: ignore[assignment]


# Marker attribute stamped on handlers we install so idempotent reinstalls
# can locate and remove prior copies without affecting unrelated handlers.
_HANDLER_MARKER = "_hermes_otel_log_handler"
# Logger level before install_handler lowered it, stamped on the handler.
_PREVIOUS_LEVEL_ATTR = "_hermes_otel_previous_level"
# ``(previous, lifted)`` ``logging.disable`` thresholds, stamped when install lifted one.
_PREVIOUS_DISABLE_ATTR = "_hermes_otel_previous_disable"

# Loggers whose records we never forward, by default: the OTel SDK (an export
# failure logged and re-exported would feed the next export), the HTTP client
# stack the exporter uses (every outbound export would log a DEBUG line that
# gets captured, batched and exported, producing the next one), and the
# third-party loggers Hermes itself pins at WARNING. ``logs.exclude_loggers``
# replaces this list (#266); the defaults live in plugin_config so the schema
# docs show them.
_EXCLUDED_LOGGER_PREFIXES = DEFAULT_LOG_EXCLUDE_LOGGERS


@dataclasses.dataclass(frozen=True)
class LogRules:
    """What one sink accepts. Built from the ``logs:`` block, then narrowed per backend.

    ``min_severity`` is an OTel severity number (INFO = 9); ``logger_levels``
    maps a logger name (exact or prefix) to the severity it must reach;
    ``exclude_prefixes`` are logger-name prefixes dropped outright;
    ``only_in_turn`` drops records the enricher could not attribute to a
    session. ``events_enabled`` / ``events_content`` are carried for the
    events pipeline (#267) and validated here so a weaker per-backend value
    is refused at load time.
    """

    min_severity: int = 1
    exclude_prefixes: Tuple[str, ...] = ()
    logger_levels: Tuple[Tuple[str, int], ...] = ()
    only_in_turn: bool = False
    events_enabled: bool = False
    events_content: str = "inherit"

    def accepts_logger(self, logger_name: Optional[str], severity: Optional[int]) -> bool:
        """Not excluded, and at or above the floor for that logger (unknown severity reads as INFO)."""
        name = logger_name or ""
        if any(name.startswith(p) for p in self.exclude_prefixes):
            return False
        floor = self.min_severity
        for prefix, level_sev in self.logger_levels:
            if name == prefix or name.startswith(prefix + "."):
                floor = max(floor, level_sev)
                break
        return (severity if isinstance(severity, int) else 9) >= floor

    def accepts(self, logger_name: Optional[str], severity: Optional[int], attrs: Any) -> bool:
        """:meth:`accepts_logger`, plus the record must name a session when ``only_in_turn``."""
        if not self.accepts_logger(logger_name, severity):
            return False
        if self.only_in_turn:
            try:
                return bool((attrs or {}).get(SESSION_ID_ATTRIBUTE))
            except Exception:
                return False
        return True


def rules_from_config(cfg: HermesOtelConfig, *, min_level: Optional[int] = None) -> LogRules:
    """The global rules from the ``logs:`` block (*min_level* overrides ``log_level``)."""
    level = min_level if min_level is not None else python_level(cfg.log_level)
    return LogRules(
        min_severity=otel_severity(level),
        exclude_prefixes=tuple(cfg.log_exclude_loggers or ()),
        logger_levels=tuple(
            (str(k), severity_number(v, 1)) for k, v in (cfg.log_logger_levels or {}).items()
        ),
        only_in_turn=bool(cfg.log_only_in_turn),
        events_enabled=bool(cfg.log_events),
        events_content=str(cfg.log_events_content or "inherit"),
    )


def narrow_rules(
    base: LogRules, overrides: Optional[Dict[str, Any]], *, where: str, content_mode: str
) -> LogRules:
    """Apply a backend's ``logs:`` mapping to *base*: stricter only, weaker warns and is refused.

    *content_mode* is the effective ``content_capture`` so ``inherit`` can be
    compared; a backend may narrow content (``full`` -> ``preview`` -> ``off``)
    but never widen it.
    """
    if not overrides:
        return base
    changes: Dict[str, Any] = {}
    level = overrides.get("level")
    if level is not None:
        sev = severity_number(level, base.min_severity)
        if sev < base.min_severity:
            logger.warning(
                f"[hermes-otel] {where}.logs.level={level} is below the global level; "
                "per-backend settings can only be stricter, keeping the global level"
            )
        else:
            changes["min_severity"] = sev
    extra = overrides.get("exclude_loggers")
    if extra:
        changes["exclude_prefixes"] = tuple(base.exclude_prefixes) + tuple(
            p for p in extra if p not in base.exclude_prefixes
        )
    levels = overrides.get("logger_levels")
    if levels:
        merged = dict(base.logger_levels)
        for name, value in levels.items():
            merged[name] = max(severity_number(value, 1), merged.get(name, 1))
        changes["logger_levels"] = tuple(merged.items())
    if overrides.get("only_in_turn") is True:
        changes["only_in_turn"] = True
    elif overrides.get("only_in_turn") is False and base.only_in_turn:
        logger.warning(
            f"[hermes-otel] {where}.logs.only_in_turn=false is weaker than the global setting; ignoring"
        )
    events = overrides.get("events") or {}
    if events.get("enabled") is False:
        changes["events_enabled"] = False
    elif events.get("enabled") is True and not base.events_enabled:
        logger.warning(
            f"[hermes-otel] {where}.logs.events.enabled=true needs logs.events.enabled: true globally; ignoring"
        )
    content = events.get("content")
    if content:
        base_mode = content_mode if base.events_content == "inherit" else base.events_content
        want = content_mode if content == "inherit" else content
        if _ev.narrower(want, base_mode) != want:
            logger.warning(
                f"[hermes-otel] {where}.logs.events.content={content} is wider than the global "
                f"{base_mode}; per-backend content can only be narrower, keeping {base_mode}"
            )
        else:
            changes["events_content"] = content
    return dataclasses.replace(base, **changes) if changes else base


class _LoggerRulesFilter(logging.Filter):
    """Handler-side filter: drop excluded loggers and below-level records before translation."""

    def __init__(self, rules: Optional[LogRules] = None) -> None:
        super().__init__()
        self._rules = rules or LogRules(exclude_prefixes=_EXCLUDED_LOGGER_PREFIXES)

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        return self._rules.accepts_logger(record.name, otel_severity(record.levelno))


@dataclasses.dataclass(frozen=True)
class _RecordView:
    """What the processors read from an SDK record, whatever the SDK wraps it in.

    ``rec`` is the mutable ``LogRecord`` itself (the ``log_record`` of a
    ``ReadWriteLogRecord`` wrapper, else the object given); ``severity`` is
    the plain OTel number or None.
    """

    rec: Any
    logger_name: Optional[str]
    severity: Optional[int]
    attrs: Any
    body: Any
    event_name: Optional[str]

    @classmethod
    def from_sdk(cls, log_record: Any) -> "_RecordView":
        rec = getattr(log_record, "log_record", log_record)
        scope = getattr(log_record, "instrumentation_scope", None)
        severity = getattr(rec, "severity_number", None)
        severity = getattr(severity, "value", severity)
        return cls(
            rec=rec,
            logger_name=getattr(scope, "name", None),
            severity=severity if isinstance(severity, int) else None,
            attrs=getattr(rec, "attributes", None),
            body=getattr(rec, "body", None),
            event_name=getattr(rec, "event_name", None) or None,
        )


class BackendLogFilter(LogRecordProcessor):  # type: ignore[misc]
    """A backend's own rules in front of its batch processor (#266).

    Records are shared objects on the provider, so this filter only decides
    whether the inner processor sees a record; it never mutates one. (Per-
    backend content narrowing for events, #267, copies the record first.)
    """

    def __init__(
        self,
        inner: Any,
        rules: LogRules,
        backend_name: str = "",
        *,
        global_events_content: str = "inherit",
        span_content_mode: str = "full",
        preview_chars: int = 1200,
    ) -> None:
        self._inner = inner
        self._rules = rules
        self._name = backend_name
        resolved_global = _ev.resolve_content_mode(global_events_content, span_content_mode)
        resolved_mine = _ev.resolve_content_mode(rules.events_content, span_content_mode)
        # Only a narrower per-backend mode changes anything; equal or wider = pass through.
        self._event_content = (
            resolved_mine
            if _ev.narrower(resolved_mine, resolved_global) != resolved_global
            else None
        )
        self._preview_chars = int(preview_chars)

    @property
    def rules(self) -> LogRules:
        return self._rules

    @property
    def inner(self) -> Any:
        return self._inner

    def on_emit(self, log_record: Any) -> None:
        try:
            view = _RecordView.from_sdk(log_record)
            if not self._rules.accepts(view.logger_name, view.severity, view.attrs):
                return
            if view.event_name:
                if not self._rules.events_enabled:
                    return  # this backend opted out of events
                if self._event_content is not None:
                    log_record = self._narrow_event(log_record, view.rec)
        except Exception:  # pragma: no cover — never block a record on a filter bug
            pass
        self._inner.on_emit(log_record)

    def _narrow_event(self, log_record: Any, rec: Any) -> Any:
        """A copy of the record with content attributes gated for this backend only.

        Records are shared between the provider's processors, so the original
        is left untouched for the backends that may keep full content.
        """
        import copy

        attrs = getattr(rec, "attributes", None) or {}
        if not _ev.has_content(attrs):
            return log_record
        new_rec = copy.copy(rec)
        new_rec.attributes = _ev.apply_content_mode(
            dict(attrs), self._event_content, self._preview_chars
        )
        if log_record is rec:
            return new_rec
        wrapper = copy.copy(log_record)
        wrapper.log_record = new_rec
        return wrapper

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


# ── Processor construction ──────────────────────────────────────────────────


# A callable the tracer supplies: ``(session_id, span)`` of the one session
# with a turn in flight and its innermost open span, or ``None``.
ContextResolver = Callable[[], Optional[Tuple[str, Any]]]

SESSION_ID_ATTRIBUTE = "hermes.session_id"
CONVERSATION_ID_ATTRIBUTE = "gen_ai.conversation.id"
PLATFORM_ATTRIBUTE = "hermes.platform"
ATTRIBUTION_ATTRIBUTE = "hermes.log.attribution"

# How a record came to carry its trace and session ids. Low cardinality by
# construction; a dashboard can show it as a confidence hint (#265).
ATTRIBUTION_CONTEXT = "context"  # a span was current on the logging thread
ATTRIBUTION_SESSION_TAG = "session_tag"  # Hermes's own per-thread session id on the record
ATTRIBUTION_SINGLE_SESSION = "single_session"  # exactly one session had a turn in flight

# Hermes's record factory puts these on EVERY LogRecord, and the SDK handler
# copies every non-reserved record attribute onto the exported record. Neither
# belongs in a backend: ``hermes_home`` is the user's home directory and
# ``session_tag`` is a display string the plugin turns into ``hermes.session_id``.
_HERMES_SESSION_TAG = "session_tag"
_HERMES_HOME = "hermes_home"
_DENY_ATTRIBUTES = frozenset({_HERMES_SESSION_TAG, _HERMES_HOME})
_DENY_SUFFIXES = (".raw_home", "_home_path")


def _session_id_from_tag(tag: Any) -> Optional[str]:
    """``" [abc123]"`` (Hermes's ``%(session_tag)s``) -> ``"abc123"``; anything else -> None."""
    if not isinstance(tag, str):
        return None
    text = tag.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text or None


def _stamp(rec: Any, span: Any) -> bool:
    """Copy *span*'s ids onto *rec*; False when there is no span or it has no trace id."""
    try:
        ctx = span.get_span_context() if span is not None else None
    except Exception:
        return False
    if ctx is None or not getattr(ctx, "trace_id", 0):
        return False
    rec.trace_id = ctx.trace_id
    rec.span_id = ctx.span_id
    rec.trace_flags = ctx.trace_flags
    if set_span_in_context is not None:
        rec.context = set_span_in_context(span)
    return True


def _strip_host_attrs(attrs: Any) -> Optional[str]:
    """Drop the host-internal attributes in place; the session the ``session_tag`` named, if any."""
    tag = attrs.get(_HERMES_SESSION_TAG)
    for key in list(attrs.keys()):
        if key in _DENY_ATTRIBUTES or key.endswith(_DENY_SUFFIXES):
            del attrs[key]
    return _session_id_from_tag(tag)


def _attribute(
    rec: Any,
    tagged_session: Optional[str],
    named_session: Optional[str],
    resolve: Optional[ContextResolver],
    span_for_session: Callable[[str], Any],
) -> Tuple[Optional[str], Optional[str]]:
    """``(tier, session_id)`` for *rec*, stamping trace ids when a tier finds a span.

    *tagged_session* is what Hermes's ``session_tag`` named, *named_session*
    what the record's own ``hermes.session_id`` says, *resolve* the tracer's
    single-active-session lookup and *span_for_session* the tracker's
    innermost open span for a session. The tiers, in order:

    * ``context``: the record already carries a trace id (a span was current
      on the logging thread); the session is the one it names, else the tag's.
    * ``session_tag``: the tracker has an open span for the tagged session.
    * ``single_session``: exactly one session has a turn in flight and the
      record does not name another one (#186 policy).
    * ``session_tag`` again, ids left empty: Hermes knows the session even
      between turns, when no span is open for it.
    """
    if getattr(rec, "trace_id", 0):
        return ATTRIBUTION_CONTEXT, named_session or tagged_session
    if tagged_session and _stamp(rec, span_for_session(tagged_session)):
        return ATTRIBUTION_SESSION_TAG, tagged_session
    found = resolve() if resolve is not None else None
    if found:
        session, span = found
        if (not tagged_session or str(session) == tagged_session) and _stamp(rec, span):
            return ATTRIBUTION_SINGLE_SESSION, str(session)
    if tagged_session:
        return ATTRIBUTION_SESSION_TAG, tagged_session
    return None, None


class HermesLogProcessor(LogRecordProcessor):  # type: ignore[misc]
    """Make every exported record truthful and safe, once, for every sink.

    Registered first on the provider, ahead of the live sink and the
    exporters, so what the dashboard shows and what a backend stores are the
    same record. ``on_emit`` is a fixed sequence, and the order is the
    behaviour:

    1. **Drops host-internal attributes** (``hermes_home``, ``session_tag``
       and a small deny-list of path-like keys), reading the session tag on
       the way out, so nothing host-side can leak through a later step.
    2. **Attributes the line to its turn, never by guessing**: the three
       exact tiers of :func:`_attribute`, the one that applied recorded in
       ``hermes.log.attribution``.
    3. **Adds the session attributes**: ``hermes.session_id`` and
       ``gen_ai.conversation.id`` (the session), and ``hermes.platform``
       from the session's root span when it is known.
    4. **Redacts secrets** from the body and every string attribute, after
       enrichment so the added attributes are covered, with Hermes's own
       redactor when the plugin runs inside Hermes, else the plugin's
       built-in set (:mod:`hermes_otel.redaction`), so the plugin is never
       less careful than ``agent.log``.
    5. **Caps string attributes** at ``logs.max_attribute_length``, after
       redaction so a cut never exposes part of a secret.

    Everything is wrapped so that logging can never raise into the agent.
    """

    def __init__(
        self,
        resolve: Optional[ContextResolver] = None,
        tracker: Any = None,
        redact: bool = True,
        max_attribute_length: Optional[int] = None,
    ) -> None:
        self._resolve = resolve
        self._tracker = tracker
        self._redact = redact
        self._max_len = int(max_attribute_length) if max_attribute_length else 0

    # ── helpers ────────────────────────────────────────────────────────────
    def _span_for_session(self, session_id: str):
        tracker = self._tracker
        if tracker is None:
            return None
        try:
            return tracker.get_current_parent(session_id) or tracker.get_session_root(session_id)
        except Exception:
            return None

    def _platform_for_session(self, session_id: Optional[str]) -> Optional[str]:
        if not session_id or self._tracker is None:
            return None
        try:
            root = self._tracker.get_session_root(session_id)
            attrs = getattr(root, "attributes", None) or {}
            value = attrs.get(PLATFORM_ATTRIBUTE)
            return str(value) if value else None
        except Exception:
            return None

    # ── processor API ──────────────────────────────────────────────────────
    def on_emit(self, log_record: Any) -> None:  # ReadWriteLogRecord
        try:
            view = _RecordView.from_sdk(log_record)
            rec, attrs = view.rec, view.attrs
            if attrs is None:
                attrs = rec.attributes = {}
            # 1. deny-list, before anything reads or adds attributes.
            tagged_session = _strip_host_attrs(attrs)
            # 2. attribution.
            named = attrs.get(SESSION_ID_ATTRIBUTE)
            tier, session_id = _attribute(
                rec,
                tagged_session,
                str(named) if named else None,
                self._resolve,
                self._span_for_session,
            )
            # 3. session attributes.
            if session_id:
                attrs.setdefault(SESSION_ID_ATTRIBUTE, session_id)
                attrs.setdefault(CONVERSATION_ID_ATTRIBUTE, session_id)
                platform = self._platform_for_session(session_id)
                if platform:
                    attrs.setdefault(PLATFORM_ATTRIBUTE, platform)
            if tier:
                attrs[ATTRIBUTION_ATTRIBUTE] = tier
            # 4. redaction, after enrichment so the added attributes are covered.
            if self._redact:
                if isinstance(view.body, str) and view.body:
                    rec.body = redact_text(view.body)
                redact_attributes(attrs)
            # 5. the length cap (``logs.max_attribute_length``), after redaction.
            if self._max_len:
                for key, value in list(attrs.items()):
                    if isinstance(value, str) and len(value) > self._max_len:
                        attrs[key] = value[: self._max_len] + "…"
        except Exception:  # pragma: no cover — logging must never raise
            pass

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# ── Live sink ───────────────────────────────────────────────────────────────

# Hermes's chattiest housekeeping lines, dropped from the live tail regardless
# of level: they fire on every gateway boot/refresh and drown the agent's own
# activity in the dashboard. Loggers and levels are configurable through the
# ``logs:`` block (#266); these substrings stay built in.
_LIVE_NOISY_LOGGERS = ("gateway.config",)
_LIVE_NOISY_SUBSTRINGS = (
    "is_connected returned False",
    "available but not configured",
    "has no subscriptions",
)
_INFO_SEVERITY_NUMBER = 9  # OTel INFO = 9..12


def _is_live_noise(logger_name: Optional[str], body: Any) -> bool:
    if logger_name in _LIVE_NOISY_LOGGERS:
        return True
    if isinstance(body, str):
        return any(s in body for s in _LIVE_NOISY_SUBSTRINGS)
    return False


class LiveLogProcessor(LogRecordProcessor):  # type: ignore[misc]
    """Mirror enriched records into the in-process live store (dashboard Logs tab).

    Sits on the same provider as the exporters, after
    :class:`HermesLogProcessor`, so the row the dashboard shows is the record
    the backend stores: same ids, same attributes, same redaction. The tail is
    floored at INFO (Hermes's DEBUG firehose would evict useful lines from the
    bounded buffer) and skips the housekeeping noise above.
    """

    def __init__(
        self,
        store: Any,
        min_severity_number: int = _INFO_SEVERITY_NUMBER,
        rules: Optional[LogRules] = None,
    ) -> None:
        self._store = store
        self._rules = (
            rules if rules is not None else LogRules(min_severity=int(min_severity_number))
        )

    def on_emit(self, log_record: Any) -> None:
        try:
            view = _RecordView.from_sdk(log_record)
            if not self._rules.accepts(view.logger_name, view.severity, view.attrs):
                return
            if _is_live_noise(view.logger_name, view.body):
                return
            rec, body = view.rec, view.body
            attrs = dict(view.attrs or {})
            trace_id = getattr(rec, "trace_id", 0) or 0
            span_id = getattr(rec, "span_id", 0) or 0
            self._store.add_log(
                {
                    # OTel spelling (WARN, FATAL) since store v3
                    "level": str(getattr(rec, "severity_text", None) or "INFO"),
                    "severity_number": view.severity,
                    "logger": view.logger_name,
                    "scope": view.logger_name,
                    "body": body if isinstance(body, str) else str(body),
                    "time_unix_nano": int(getattr(rec, "timestamp", None) or 0) or None,
                    "observed_time_unix_nano": int(getattr(rec, "observed_timestamp", None) or 0)
                    or None,
                    "trace_id": format(trace_id, "032x") if trace_id else None,
                    "span_id": format(span_id, "016x") if span_id else None,
                    "session_id": attrs.get(SESSION_ID_ATTRIBUTE),
                    "event_name": view.event_name,
                    "attributes": attrs,
                }
            )
        except Exception:  # pragma: no cover — logging must never raise
            pass

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def build_log_processors(
    backends: List[_ResolvedBackend],
    extra_headers: Optional[Dict[str, str]] = None,
    *,
    batch: Optional[Dict[str, int]] = None,
    rules: Optional[LogRules] = None,
    content_mode: str = "full",
    preview_chars: int = 1200,
) -> List[Tuple[Any, _ResolvedBackend]]:
    """Build one :class:`BatchLogRecordProcessor` per log-capable backend.

    Returns ``[(processor, backend), ...]``. Backends that don't support
    logs are silently skipped. Any per-backend exporter init failure is
    logged and the other backends still proceed — matches the fan-out
    semantics of :func:`pipeline.init_otlp_pipeline`, and the endpoint and
    headers come from the same :meth:`_ResolvedBackend.target` rule.

    *batch* carries the ``logs.batch.*`` knobs; *rules* the global
    :class:`LogRules`, which each backend's own ``logs:`` mapping may narrow
    (the processor is then wrapped in a :class:`BackendLogFilter`).
    """
    if not _LOGS_AVAILABLE:
        return []
    batch = batch or {}

    processors: List[Tuple[Any, _ResolvedBackend]] = []
    for b in backends:
        if not b.supports_logs:
            continue
        target = b.target("logs", extra_headers)
        try:
            with without_otlp_header_env():
                log_exporter = OTLPLogExporter(endpoint=target.endpoint, headers=target.headers)
            # One debug.log line per batch, as for spans (#167).
            exporter = _LoggingExporter(
                log_exporter,
                f"{b.display_name} logs",
                unit="record",
                failure=LogExportResult.FAILURE,
            )
            processor: Any = BatchLogRecordProcessor(
                exporter,
                schedule_delay_millis=batch.get("schedule_delay_ms"),
                max_queue_size=batch.get("max_queue_size"),
                max_export_batch_size=batch.get("max_export_batch_size"),
                export_timeout_millis=batch.get("export_timeout_ms"),
            )
            if rules is not None:
                backend_rules = narrow_rules(
                    rules,
                    b.log_overrides,
                    where=f"backends[{b.display_name}]",
                    content_mode=content_mode,
                )
                processor = BackendLogFilter(
                    processor,
                    backend_rules,
                    b.display_name,
                    global_events_content=rules.events_content,
                    span_content_mode=content_mode,
                    preview_chars=preview_chars,
                )
            processors.append((processor, b))
        except Exception as e:
            logger.error(f"[hermes-otel] ✗ {b.display_name} logs init failed: {e}")
    return processors


# ── Handler install / uninstall ─────────────────────────────────────────────


def install_handler(
    resource: "Resource",
    processors: List[Tuple[Any, "_ResolvedBackend"]],
    level: int,
    attach_logger: Optional[str] = None,
    context_resolver: Optional[ContextResolver] = None,
    tracker: Any = None,
    live_store: Any = None,
    live_min_level: int = logging.INFO,
    redact: bool = True,
    rules: Optional[LogRules] = None,
    live_rules: Optional[LogRules] = None,
    max_attribute_length: Optional[int] = None,
    attach_handler: bool = True,
) -> Optional["LoggerProvider"]:
    """Wire a :class:`LoggerProvider` + :class:`LoggingHandler` onto Python logging.

    Idempotent — any handler previously installed by this function on the
    same target logger is removed first, so repeated calls (plugin reload,
    tests) don't stack handlers.

    Args:
        resource: OTel ``Resource`` shared with traces/metrics so every
            signal carries the same ``service.name`` etc.
        processors: Output of :func:`build_log_processors`. If empty the
            handler is not attached and ``None`` is returned.
        level: Python logging level (``logging.INFO`` etc) the handler
            will accept.
        attach_logger: Logger name to attach the handler to. ``None``
            means the root logger (captures everything). Pass e.g.
            ``"hermes_otel"`` to scope capture to plugin logs only.
        context_resolver: The single-active-session resolver for the
            ``single_session`` attribution tier (see :class:`HermesLogProcessor`).
        tracker: The span tracker, for the ``session_tag`` tier.
        live_store: When given, a :class:`LiveLogProcessor` mirrors every
            enriched record into it (the dashboard Logs tab), floored at
            ``live_min_level``.
        redact: Redact secrets from bodies and attributes (default on).
        rules: Global :class:`LogRules` (excluded loggers, per-logger levels)
            applied at the handler, before translation. Defaults to the
            built-in exclusions.
        live_rules: Rules for the live sink; defaults to *rules* floored at
            ``live_min_level``.
        max_attribute_length: Cap on exported string attributes.
        attach_handler: Attach the stdlib ``LoggingHandler`` (``logs.capture``).
            ``False`` builds the provider for the plugin's own events only
            (``logs.events.enabled`` with ``logs.capture: false``, #267).

    Returns the ``LoggerProvider`` so the tracer can keep a reference for
    ``force_flush`` / ``shutdown``. Returns ``None`` when logs are
    unavailable or there is neither a log-capable backend nor a live store.
    """
    if not _LOGS_AVAILABLE or (not processors and live_store is None):
        return None

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        HermesLogProcessor(
            resolve=context_resolver,
            tracker=tracker,
            redact=redact,
            max_attribute_length=max_attribute_length,
        )
    )
    if live_store is not None:
        if live_rules is None:
            base = rules or LogRules(exclude_prefixes=_EXCLUDED_LOGGER_PREFIXES)
            live_rules = dataclasses.replace(
                base, min_severity=max(base.min_severity, otel_severity(live_min_level))
            )
        provider.add_log_record_processor(LiveLogProcessor(live_store, rules=live_rules))
    for proc, _backend in processors:
        provider.add_log_record_processor(proc)

    target = logging.getLogger(attach_logger) if attach_logger else logging.getLogger()
    _remove_prior_handlers(target)
    if not attach_handler:
        debug_log(f"log_handler: provider only (events), backends={len(processors)}")
        return provider

    handler = LoggingHandler(level=level, logger_provider=provider)
    handler.addFilter(_LoggerRulesFilter(rules))
    setattr(handler, _HANDLER_MARKER, True)
    target.addHandler(handler)
    # Ensure records actually reach the handler — Python filters at the
    # logger level before dispatching to handlers, so a root logger left
    # at WARNING would silently drop INFO/DEBUG even with a DEBUG handler.
    # The previous level is remembered on the handler so ``uninstall_handler``
    # can put it back (lowering the root logger is a process-wide side effect).
    setattr(handler, _PREVIOUS_LEVEL_ATTR, target.level)
    if target.level == logging.NOTSET or target.level > level:
        target.setLevel(level)
    _lift_global_disable(handler, level)

    debug_log(
        f"log_handler installed: target={attach_logger or 'root'} "
        f"level={logging.getLevelName(level)} backends={len(processors)} "
        f"live={'yes' if live_store is not None else 'no'}"
    )
    return provider


def _lift_global_disable(handler: logging.Handler, level: int) -> None:
    """Undo a process-wide ``logging.disable()`` that would starve *handler*.

    ``logging.disable(n)`` short-circuits ``Logger.isEnabledFor`` for every
    logger, so records never reach any handler. ``hermes -z`` (one-shot) calls
    ``logging.disable(CRITICAL)`` to keep stderr clean, which made
    ``logs.capture`` export zero records from every one-shot run. Lower the
    threshold just far enough that records at or above *level* flow again;
    anything below stays suppressed. The prior value is stamped on the handler
    so uninstall can restore it.
    """
    manager = logging.root.manager
    previous = manager.disable
    if previous < level:
        return
    lifted = max(level - 1, logging.NOTSET)
    logging.disable(lifted)
    setattr(handler, _PREVIOUS_DISABLE_ATTR, (previous, lifted))
    debug_log(
        f"log_handler: lifted logging.disable({previous}) to {lifted} so records "
        f"at {logging.getLevelName(level)}+ reach the handler"
    )


def _remove_prior_handlers(target: logging.Logger) -> None:
    """Strip any marker-tagged handlers we installed previously.

    Only touches handlers we own — leaves the consumer's own handlers
    (stderr, file, syslog, ...) alone. Restores the logger level (and any
    ``logging.disable`` threshold) the handler recorded at install time.
    """
    for h in list(target.handlers):
        if getattr(h, _HANDLER_MARKER, False):
            target.removeHandler(h)
            previous = getattr(h, _PREVIOUS_LEVEL_ATTR, None)
            if previous is not None:
                target.setLevel(previous)
            disable = getattr(h, _PREVIOUS_DISABLE_ATTR, None)
            # Only restore if nobody changed the threshold since we lifted it.
            if disable is not None and logging.root.manager.disable == disable[1]:
                logging.disable(disable[0])


def uninstall_handler(attach_logger: Optional[str] = None) -> None:
    """Remove the handler :func:`install_handler` attached (tracer shutdown)."""
    target = logging.getLogger(attach_logger) if attach_logger else logging.getLogger()
    _remove_prior_handlers(target)
