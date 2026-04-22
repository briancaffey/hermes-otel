"""OTel log pipeline for hermes-otel.

Bridges Python's :mod:`logging` to the OTel logs signal. When
:attr:`~hermes_otel.plugin_config.HermesOtelConfig.capture_logs` is true the
tracer wires one :class:`BatchLogRecordProcessor` per log-capable backend and
attaches a :class:`LoggingHandler` to Python's root logger (or a scoped
logger, controlled by ``log_attach_logger``).

The OTel ``LoggingHandler`` stamps every emitted record with the current
span's ``trace_id`` and ``span_id`` automatically, which is what makes the
"jump from a Loki log line to the Tempo span that emitted it" workflow in
Grafana function without any app-side context plumbing.

Module-level state is deliberately minimal — a single installed handler per
process, tracked on the :class:`~hermes_otel.tracer.HermesOTelPlugin`
singleton — so test fixtures that reset the tracer transitively reset the
log pipeline too. The only thing that persists across tracer rebuilds is
Python's global logger hierarchy, which :func:`install_handler` guards
against double-attachment by removing prior instances keyed on a marker
attribute before adding a new one.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .backends import _ResolvedBackend
from .debug_utils import debug_log, logger

try:
    # opentelemetry-sdk emits a DeprecationWarning on LoggingHandler import
    # pointing users at opentelemetry-instrumentation-logging. That package
    # is a higher-level auto-instrumentation layer; we use the SDK handler
    # directly because it gives explicit control over which logger the
    # handler attaches to and lets us fan out to multiple providers. Revisit
    # if the SDK handler is actually removed (not just deprecated).
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    _LOGS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK missing
    _LOGS_AVAILABLE = False
    OTLPLogExporter = None  # type: ignore[assignment]
    LoggerProvider = None  # type: ignore[assignment]
    LoggingHandler = None  # type: ignore[assignment]
    BatchLogRecordProcessor = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]


# Marker attribute stamped on handlers we install so idempotent reinstalls
# can locate and remove prior copies without affecting unrelated handlers.
_HANDLER_MARKER = "_hermes_otel_log_handler"

# Loggers whose records we refuse to forward to the OTLP logs pipeline.
# Two reasons each of these is on the list:
#
#   opentelemetry.*  — The SDK emits warnings via stdlib logging on export
#                      failure. Forwarding those back through the exporter
#                      would fill the queue with more "export failed"
#                      records, fail again, etc.
#
#   urllib3.*, httpx, httpcore, requests — The OTLP HTTP exporter uses
#                      these libraries internally. When an app has HTTP
#                      client debug logging turned on, EVERY outbound log
#                      export also produces a DEBUG line like
#                      ``http://localhost:4318 "POST /v1/logs HTTP/1.1" 200``
#                      which then gets captured, batched, and exported —
#                      producing the next DEBUG line, and so on. The loop
#                      is async-bounded (no infinite recursion) but it
#                      crowds out real application logs in Loki.
#
# If you need to debug the OTel exporter itself, temporarily scope the log
# handler to a single logger via ``log_attach_logger: hermes_otel`` so the
# full root-logger firehose is out of scope.
_EXCLUDED_LOGGER_PREFIXES = (
    "opentelemetry",
    "urllib3",
    "httpx",
    "httpcore",
    "requests",
)


class _ExcludeOTelInternal(logging.Filter):
    """Drop records emitted by OTel SDK internals.

    Prevents export-failure warnings from the logs pipeline from re-entering
    that same pipeline. Users who want to see SDK internal warnings can
    still get them via the plugin's stderr handler (``hermes_otel`` logger)
    or stdout.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        name = record.name or ""
        return not any(name.startswith(p) for p in _EXCLUDED_LOGGER_PREFIXES)


# ── Endpoint derivation ─────────────────────────────────────────────────────


def _derive_logs_endpoint(traces_endpoint: str) -> str:
    """Rewrite ``.../v1/traces`` to ``.../v1/logs``.

    Mirrors :meth:`HermesOTelPlugin._derive_metrics_endpoint`. Collectors
    that follow OTLP/HTTP convention expose the three signals on the same
    host under different path suffixes; most of the time swapping the
    suffix is all we need.
    """
    if traces_endpoint.endswith("/v1/traces"):
        return traces_endpoint[: -len("/v1/traces")] + "/v1/logs"
    return traces_endpoint


# ── Processor construction ──────────────────────────────────────────────────


def build_log_processors(
    backends: List[_ResolvedBackend],
    extra_headers: Optional[Dict[str, str]] = None,
) -> List[Tuple[Any, _ResolvedBackend]]:
    """Build one :class:`BatchLogRecordProcessor` per log-capable backend.

    Returns ``[(processor, backend), ...]``. Backends that don't support
    logs are silently skipped. Any per-backend exporter init failure is
    logged and the other backends still proceed — matches the fan-out
    semantics of :meth:`HermesOTelPlugin._init_otlp_pipeline`.
    """
    if not _LOGS_AVAILABLE:
        return []

    processors: List[Tuple[Any, _ResolvedBackend]] = []
    for b in backends:
        if not b.supports_logs:
            continue
        merged: Dict[str, str] = dict(b.headers or {})
        if extra_headers:
            merged.update(extra_headers)
        endpoint = _derive_logs_endpoint(b.endpoint)
        try:
            exporter = OTLPLogExporter(endpoint=endpoint, headers=merged or None)
            processors.append((BatchLogRecordProcessor(exporter), b))
        except Exception as e:
            logger.error(f"[hermes-otel] ✗ {b.display_name} logs init failed: {e}")
    return processors


# ── Handler install / uninstall ─────────────────────────────────────────────


def install_handler(
    resource: "Resource",
    processors: List[Tuple[Any, "_ResolvedBackend"]],
    level: int,
    attach_logger: Optional[str] = None,
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

    Returns the ``LoggerProvider`` so the tracer can keep a reference for
    ``force_flush`` / ``shutdown``. Returns ``None`` when logs are disabled,
    unavailable, or no log-capable backend was provided.
    """
    if not _LOGS_AVAILABLE or not processors:
        return None

    provider = LoggerProvider(resource=resource)
    for proc, _backend in processors:
        provider.add_log_record_processor(proc)

    handler = LoggingHandler(level=level, logger_provider=provider)
    handler.addFilter(_ExcludeOTelInternal())
    setattr(handler, _HANDLER_MARKER, True)

    target = logging.getLogger(attach_logger) if attach_logger else logging.getLogger()
    _remove_prior_handlers(target)
    target.addHandler(handler)
    # Ensure records actually reach the handler — Python filters at the
    # logger level before dispatching to handlers, so a root logger left
    # at WARNING would silently drop INFO/DEBUG even with a DEBUG handler.
    if target.level == logging.NOTSET or target.level > level:
        target.setLevel(level)

    debug_log(
        f"log_handler installed: target={attach_logger or 'root'} "
        f"level={logging.getLevelName(level)} backends={len(processors)}"
    )
    return provider


def _remove_prior_handlers(target: logging.Logger) -> None:
    """Strip any marker-tagged handlers we installed previously.

    Only touches handlers we own — leaves the consumer's own handlers
    (stderr, file, syslog, ...) alone.
    """
    for h in list(target.handlers):
        if getattr(h, _HANDLER_MARKER, False):
            target.removeHandler(h)


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
