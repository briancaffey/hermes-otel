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
process, tracked on the :class:`~hermes_otel.tracer.HermesOTelPlugin`
singleton — so test fixtures that reset the tracer transitively reset the
log pipeline too. The only thing that persists across tracer rebuilds is
Python's global logger hierarchy, which :func:`install_handler` guards
against double-attachment by removing prior instances keyed on a marker
attribute before adding a new one.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backends import _ResolvedBackend
from .debug_utils import debug_log, logger
from .helpers import derive_signal_endpoint
from .plugin_config import DEFAULT_LOG_EXCLUDE_LOGGERS, HermesOtelConfig
from .redaction import redact_attributes, redact_text

try:
    # opentelemetry-sdk emits a DeprecationWarning on LoggingHandler import
    # pointing users at opentelemetry-instrumentation-logging. That package
    # is a higher-level auto-instrumentation layer; we use the SDK handler
    # directly because it gives explicit control over which logger the
    # handler attaches to and lets us fan out to multiple providers. Revisit
    # if the SDK handler is actually removed (not just deprecated).
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, LogRecordProcessor
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExporter, LogExportResult
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import set_span_in_context

    _LOGS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK missing
    _LOGS_AVAILABLE = False
    OTLPLogExporter = None  # type: ignore[assignment]
    LoggerProvider = None  # type: ignore[assignment]
    LoggingHandler = None  # type: ignore[assignment]
    BatchLogRecordProcessor = None  # type: ignore[assignment]
    LogExporter = object  # type: ignore[assignment,misc]
    LogExportResult = None  # type: ignore[assignment]
    LogRecordProcessor = object  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment]
    set_span_in_context = None  # type: ignore[assignment]


# Marker attribute stamped on handlers we install so idempotent reinstalls
# can locate and remove prior copies without affecting unrelated handlers.
_HANDLER_MARKER = "_hermes_otel_log_handler"
# Logger level before install_handler lowered it, stamped on the handler.
_PREVIOUS_LEVEL_ATTR = "_hermes_otel_previous_level"

# Loggers whose records we never forward, by default: the OTel SDK (an export
# failure logged and re-exported would feed the next export), the HTTP client
# stack the exporter uses (every outbound export would log a DEBUG line that
# gets captured, batched and exported, producing the next one), and the
# third-party loggers Hermes itself pins at WARNING. ``logs.exclude_loggers``
# replaces this list (#266); the defaults live in plugin_config so the schema
# docs show them.
_EXCLUDED_LOGGER_PREFIXES = DEFAULT_LOG_EXCLUDE_LOGGERS

_SEVERITY_FLOOR = {  # Python level -> lowest OTel severity number of that band
    logging.CRITICAL: 21,
    logging.ERROR: 17,
    logging.WARNING: 13,
    logging.INFO: 9,
    logging.DEBUG: 5,
}


def _python_to_severity(level: int) -> int:
    """Python level number -> the lowest OTel severity number of that band."""
    for py_level, sev in _SEVERITY_FLOOR.items():
        if level >= py_level:
            return sev
    return 1


def _level_to_severity(name: Any, default: int) -> int:
    """``"WARN"`` / ``"WARNING"`` / ``30`` -> OTel severity number; *default* when unknown."""
    if name is None:
        return default
    text = str(name).strip().upper()
    if not text:
        return default
    text = {"WARN": "WARNING", "FATAL": "CRITICAL", "TRACE": "DEBUG"}.get(text, text)
    level = logging.getLevelName(text) if not text.isdigit() else int(text)
    if not isinstance(level, int):
        return default
    return _python_to_severity(level)


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

    def accepts(self, logger_name: Optional[str], severity: Optional[int], attrs: Any) -> bool:
        name = logger_name or ""
        if any(
            name == p or name.startswith(p + ".") or name.startswith(p)
            for p in self.exclude_prefixes
        ):
            return False
        sev = severity if isinstance(severity, int) else 9
        floor = self.min_severity
        for prefix, level_sev in self.logger_levels:
            if name == prefix or name.startswith(prefix + "."):
                floor = max(floor, level_sev)
                break
        if sev < floor:
            return False
        if self.only_in_turn:
            try:
                if not (attrs or {}).get(SESSION_ID_ATTRIBUTE):
                    return False
            except Exception:
                return False
        return True


_CONTENT_RANK = {"off": 0, "preview": 1, "full": 2}


def rules_from_config(cfg: HermesOtelConfig, *, min_level: Optional[int] = None) -> LogRules:
    """The global rules from the ``logs:`` block (*min_level* overrides ``log_level``)."""
    level = min_level if min_level is not None else resolve_level(cfg.log_level)
    return LogRules(
        min_severity=_python_to_severity(level),
        exclude_prefixes=tuple(cfg.log_exclude_loggers or ()),
        logger_levels=tuple(
            (str(k), _level_to_severity(v, 1)) for k, v in (cfg.log_logger_levels or {}).items()
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
        sev = _level_to_severity(level, base.min_severity)
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
            sev = _level_to_severity(value, 1)
            merged[name] = max(sev, merged.get(name, 1))
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
        if _CONTENT_RANK.get(want, 2) > _CONTENT_RANK.get(base_mode, 2):
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
        rules = self._rules
        name = record.name or ""
        if any(
            name == p or name.startswith(p + ".") or name.startswith(p)
            for p in rules.exclude_prefixes
        ):
            return False
        for prefix, level_sev in rules.logger_levels:
            if name == prefix or name.startswith(prefix + "."):
                return _python_to_severity(record.levelno) >= level_sev
        return True


class _ExcludeOTelInternal(_LoggerRulesFilter):
    """The default exclusions only (kept for callers that used this name)."""

    def __init__(self) -> None:
        super().__init__(LogRules(exclude_prefixes=_EXCLUDED_LOGGER_PREFIXES))


class BackendLogFilter(LogRecordProcessor):  # type: ignore[misc]
    """A backend's own rules in front of its batch processor (#266).

    Records are shared objects on the provider, so this filter only decides
    whether the inner processor sees a record; it never mutates one. (Per-
    backend content narrowing for events, #267, copies the record first.)
    """

    def __init__(self, inner: Any, rules: LogRules, backend_name: str = "") -> None:
        self._inner = inner
        self._rules = rules
        self._name = backend_name

    @property
    def rules(self) -> LogRules:
        return self._rules

    @property
    def inner(self) -> Any:
        return self._inner

    def on_emit(self, log_record: Any) -> None:
        try:
            rec = getattr(log_record, "log_record", log_record)
            scope = getattr(log_record, "instrumentation_scope", None)
            severity = getattr(rec, "severity_number", None)
            severity_value = getattr(severity, "value", severity)
            if not self._rules.accepts(
                getattr(scope, "name", None), severity_value, getattr(rec, "attributes", None)
            ):
                return
        except Exception:  # pragma: no cover — never block a record on a filter bug
            pass
        self._inner.on_emit(log_record)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


# ── Endpoint derivation ─────────────────────────────────────────────────────


def _derive_logs_endpoint(traces_endpoint: str) -> str:
    """Rewrite ``.../v1/traces`` to ``.../v1/logs``.

    Thin wrapper over :func:`helpers.derive_signal_endpoint` (kept as the
    name tests and the tracer's metrics twin refer to).
    """
    return derive_signal_endpoint(traces_endpoint, "logs")


# ── Processor construction ──────────────────────────────────────────────────


class _LoggingLogExporter(LogExporter):  # type: ignore[misc]
    """Delegating log exporter that records each batch's outcome in the debug log (#167)."""

    def __init__(self, inner: Any, backend_name: str) -> None:
        self._inner = inner
        self._name = backend_name

    def export(self, batch: Any) -> Any:
        count = len(batch) if hasattr(batch, "__len__") else "?"
        try:
            result = self._inner.export(batch)
        except Exception as e:  # pragma: no cover — exporter raised instead of returning
            debug_log(
                f"export {self._name} logs: {count} record(s) -> FAILURE ({type(e).__name__}: {e})"
            )
            return LogExportResult.FAILURE
        debug_log(
            f"export {self._name} logs: {count} record(s) -> {getattr(result, 'name', result)}"
        )
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        flush = getattr(self._inner, "force_flush", None)
        return flush(timeout_millis) if flush else True


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


def _span_context(span: Any):
    try:
        ctx = span.get_span_context() if span is not None else None
    except Exception:
        return None
    if ctx is None or not getattr(ctx, "trace_id", 0):
        return None
    return ctx


class HermesLogProcessor(LogRecordProcessor):  # type: ignore[misc]
    """Make every exported record truthful and safe, once, for every sink.

    Registered first on the provider, ahead of the live sink and the
    exporters, so what the dashboard shows and what a backend stores are the
    same record. In ``on_emit`` it:

    1. **Attributes the line to its turn, never by guessing.** Three exact
       tiers, tried in order; the one that applied is recorded in
       ``hermes.log.attribution``:

       * ``context`` — the record already carries a trace id because a span
         was current on the logging thread. Left as is.
       * ``session_tag`` — Hermes stamps the session id of the thread that
         logged the line onto the record (``session_tag``); the tracker's
         innermost open span for that session gives the ids. Exact under
         any number of concurrent sessions.
       * ``single_session`` — exactly one session has a turn in flight, so
         the line can only be that session's (#186 policy). With several
         sessions active an unattributable line stays unattributed.
    2. **Adds the session attributes**: ``hermes.session_id`` and
       ``gen_ai.conversation.id`` (the session), and ``hermes.platform``
       from the session's root span when it is known.
    3. **Drops host-internal attributes** (``hermes_home``, ``session_tag``
       and a small deny-list of path-like keys) before export.
    4. **Redacts secrets** from the body and every string attribute with
       Hermes's own redactor when the plugin runs inside Hermes, else the
       plugin's built-in set (:mod:`hermes_otel.redaction`), so the plugin is
       never less careful than ``agent.log``.

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

    @staticmethod
    def _stamp(rec: Any, span: Any) -> bool:
        ctx = _span_context(span)
        if ctx is None:
            return False
        rec.trace_id = ctx.trace_id
        rec.span_id = ctx.span_id
        rec.trace_flags = ctx.trace_flags
        if set_span_in_context is not None:
            rec.context = set_span_in_context(span)
        return True

    # ── processor API ──────────────────────────────────────────────────────
    def on_emit(self, log_record: Any) -> None:  # ReadWriteLogRecord
        try:
            rec = getattr(log_record, "log_record", log_record)
            attrs = getattr(rec, "attributes", None)
            if attrs is None:
                attrs = {}
                try:
                    rec.attributes = attrs
                except Exception:
                    attrs = None

            # 3. host-internal attributes: read what we need, then drop them.
            tag = None
            if attrs is not None:
                tag = attrs.get(_HERMES_SESSION_TAG)
                for key in list(attrs.keys()):
                    if key in _DENY_ATTRIBUTES or key.endswith(_DENY_SUFFIXES):
                        try:
                            del attrs[key]
                        except Exception:
                            pass
            tagged_session = _session_id_from_tag(tag)

            # 1. attribution tiers.
            tier: Optional[str] = None
            session_id: Optional[str] = None
            if getattr(rec, "trace_id", 0):
                tier = ATTRIBUTION_CONTEXT
                session_id = tagged_session
                if attrs is not None and attrs.get(SESSION_ID_ATTRIBUTE):
                    session_id = str(attrs[SESSION_ID_ATTRIBUTE])
            else:
                if tagged_session:
                    span = self._span_for_session(tagged_session)
                    if span is not None and self._stamp(rec, span):
                        tier = ATTRIBUTION_SESSION_TAG
                        session_id = tagged_session
                if tier is None and self._resolve is not None:
                    found = self._resolve()
                    if found:
                        found_session, span = found
                        if tagged_session and str(found_session) != tagged_session:
                            found = None  # the record names another session: do not guess
                        elif self._stamp(rec, span):
                            tier = ATTRIBUTION_SINGLE_SESSION
                            session_id = str(found_session)
                if tier is None and tagged_session:
                    # Hermes knows the session even when no span is open for
                    # it (between turns): keep the session, leave trace ids empty.
                    tier = ATTRIBUTION_SESSION_TAG
                    session_id = tagged_session

            # 2. session attributes.
            if attrs is not None:
                if session_id:
                    attrs.setdefault(SESSION_ID_ATTRIBUTE, session_id)
                    attrs.setdefault(CONVERSATION_ID_ATTRIBUTE, session_id)
                    platform = self._platform_for_session(session_id)
                    if platform:
                        attrs.setdefault(PLATFORM_ATTRIBUTE, platform)
                if tier:
                    attrs[ATTRIBUTION_ATTRIBUTE] = tier

            # 4. redaction, after everything else so added attributes are covered.
            if self._redact:
                body = getattr(rec, "body", None)
                if isinstance(body, str) and body:
                    redacted = redact_text(body)
                    if redacted != body:
                        rec.body = redacted
                redact_attributes(attrs)
            # 5. attribute length cap (``logs.max_attribute_length``), after redaction.
            if self._max_len and attrs is not None:
                for key in list(attrs.keys()):
                    value = attrs[key]
                    if isinstance(value, str) and len(value) > self._max_len:
                        attrs[key] = value[: self._max_len] + "…"
        except Exception:  # pragma: no cover — logging must never raise
            pass

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# Kept for callers that used the previous name (#247); same processor.
SpanContextStamper = HermesLogProcessor


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


class _LiveLogNoiseFilter(logging.Filter):
    """Drop Hermes's chattiest housekeeping lines (kept as a stdlib filter for callers)."""

    _NOISY_LOGGERS = _LIVE_NOISY_LOGGERS
    _NOISY_SUBSTRINGS = _LIVE_NOISY_SUBSTRINGS

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name in self._NOISY_LOGGERS:
            return False
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        return not any(s in msg for s in self._NOISY_SUBSTRINGS)


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
            rec = getattr(log_record, "log_record", log_record)
            severity = getattr(rec, "severity_number", None)
            severity_value = getattr(severity, "value", severity)
            scope = getattr(log_record, "instrumentation_scope", None)
            logger_name = getattr(scope, "name", None)
            body = getattr(rec, "body", None)
            attributes = getattr(rec, "attributes", None)
            if not self._rules.accepts(logger_name, severity_value, attributes):
                return
            if _is_live_noise(logger_name, body):
                return
            attrs = dict(attributes or {})
            severity_text = str(getattr(rec, "severity_text", None) or "INFO")
            trace_id = getattr(rec, "trace_id", 0) or 0
            span_id = getattr(rec, "span_id", 0) or 0
            self._store.add_log(
                {
                    "level": severity_text,  # OTel spelling (WARN, FATAL) since store v3
                    "severity_number": severity_value if isinstance(severity_value, int) else None,
                    "logger": logger_name,
                    "scope": logger_name,
                    "body": body if isinstance(body, str) else str(body),
                    "time_unix_nano": int(getattr(rec, "timestamp", None) or 0) or None,
                    "observed_time_unix_nano": int(getattr(rec, "observed_timestamp", None) or 0)
                    or None,
                    "trace_id": format(trace_id, "032x") if trace_id else None,
                    "span_id": format(span_id, "016x") if span_id else None,
                    "session_id": attrs.get(SESSION_ID_ATTRIBUTE),
                    "event_name": getattr(rec, "event_name", None) or None,
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
) -> List[Tuple[Any, _ResolvedBackend]]:
    """Build one :class:`BatchLogRecordProcessor` per log-capable backend.

    Returns ``[(processor, backend), ...]``. Backends that don't support
    logs are silently skipped. Any per-backend exporter init failure is
    logged and the other backends still proceed — matches the fan-out
    semantics of :meth:`HermesOTelPlugin._init_otlp_pipeline`.

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
        # Global ``headers:`` first, the backend's own on top: per-backend wins
        # on conflict, which keeps resolver-built auth headers intact. Mirrors
        # HermesOTelPlugin._merge_headers and the documented precedence.
        merged: Dict[str, str] = dict(extra_headers or {})
        merged.update(b.logs_headers or b.headers or {})
        endpoint = _derive_logs_endpoint(b.endpoint)
        try:
            exporter = _LoggingLogExporter(
                OTLPLogExporter(endpoint=endpoint, headers=merged or None), b.display_name
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
                processor = BackendLogFilter(processor, backend_rules, b.display_name)
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
                base, min_severity=max(base.min_severity, _python_to_severity(live_min_level))
            )
        provider.add_log_record_processor(LiveLogProcessor(live_store, rules=live_rules))
    for proc, _backend in processors:
        provider.add_log_record_processor(proc)

    handler = LoggingHandler(level=level, logger_provider=provider)
    handler.addFilter(_LoggerRulesFilter(rules))
    setattr(handler, _HANDLER_MARKER, True)

    target = logging.getLogger(attach_logger) if attach_logger else logging.getLogger()
    _remove_prior_handlers(target)
    target.addHandler(handler)
    # Ensure records actually reach the handler — Python filters at the
    # logger level before dispatching to handlers, so a root logger left
    # at WARNING would silently drop INFO/DEBUG even with a DEBUG handler.
    # The previous level is remembered on the handler so ``uninstall_handler``
    # can put it back (lowering the root logger is a process-wide side effect).
    setattr(handler, _PREVIOUS_LEVEL_ATTR, target.level)
    if target.level == logging.NOTSET or target.level > level:
        target.setLevel(level)

    debug_log(
        f"log_handler installed: target={attach_logger or 'root'} "
        f"level={logging.getLevelName(level)} backends={len(processors)} "
        f"live={'yes' if live_store is not None else 'no'}"
    )
    return provider


def _remove_prior_handlers(target: logging.Logger) -> None:
    """Strip any marker-tagged handlers we installed previously.

    Only touches handlers we own — leaves the consumer's own handlers
    (stderr, file, syslog, ...) alone. Restores the logger level the
    handler recorded at install time.
    """
    for h in list(target.handlers):
        if getattr(h, _HANDLER_MARKER, False):
            target.removeHandler(h)
            previous = getattr(h, _PREVIOUS_LEVEL_ATTR, None)
            if previous is not None:
                target.setLevel(previous)


def uninstall_handler(attach_logger: Optional[str] = None) -> None:
    """Remove the handler :func:`install_handler` attached (tracer shutdown)."""
    target = logging.getLogger(attach_logger) if attach_logger else logging.getLogger()
    _remove_prior_handlers(target)


def resolve_level(name: Optional[str], default: int = logging.INFO) -> int:
    """Parse a level string like ``"INFO"`` to an :mod:`logging` integer level.

    Unknown or empty values fall back to ``default``. Accepts bare digits
    (``"20"``) as a convenience for users mirroring the stdlib numeric API.
    """
    if name is None:
        return default
    text = str(name).strip()
    if not text:
        return default
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return default
    resolved = logging.getLevelName(text.upper())
    if isinstance(resolved, int):
        return resolved
    return default
