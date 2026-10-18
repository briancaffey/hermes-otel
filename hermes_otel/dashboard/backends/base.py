"""Core abstractions for dashboard backend adapters.

Each adapter is a subclass of :class:`BackendAdapter`, lists the backend
``type:`` values it handles in ``handles``, and registers itself with the
package's ``register`` decorator.

Adapters return data in two normalized shapes:

* ``search()`` → Tempo-style ``{traces: [{traceID, rootServiceName,
  rootTraceName, startTimeUnixNano, durationMs, spanSets: [...]}],
  has_more, next_before_ns}``
  where ``spanSets[0].spans[].attributes`` carries card-relevant
  attributes (model, provider, tokens, input/output previews, status).

* ``get_trace()`` → OTLP JSON ``{batches: [{resource, scopeSpans:
  [{spans: [...]}]}]}`` — the shape the frontend's ``buildSpanTree``
  already consumes.

This module holds the adapter ABC, the structured and log filters, the
error taxonomy and the HTTP core. The rest is split by concern (#403):
``shapes`` (card rows, OTLP spans and batches), ``paging`` (trace and log
pages, cursor bounds), ``metrics_math`` (name mapping, counter increases,
bucketing) and ``timeconv`` (timestamps to ns); the level tables are the
package's :mod:`hermes_otel.severity`.
No side effects at import time.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union
from urllib import error as _urlerror
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from hermes_otel.backend_specs import spec_for
from hermes_otel.backends import choose_secret
from hermes_otel.severity import severity_number

from .shapes import to_int

try:  # the dashboard process has FastAPI; the skill's terminal tool (#215) may not
    from fastapi import HTTPException
except ImportError:  # pragma: no cover - exercised in test_query_cli via sys.modules

    class HTTPException(Exception):  # type: ignore[no-redef]
        """Stdlib stand-in with FastAPI's ``status_code`` / ``detail`` fields."""

        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

        def __str__(self) -> str:
            return f"{self.status_code}: {self.detail}"


# ── One error taxonomy for every adapter (#290) ─────────────────────────
#
# ``kind`` tells the route (and the UI) what went wrong without parsing the
# message: ``not_found`` (the backend has no such trace / route: 404),
# ``auth`` (401/403: the key is wrong or missing scope), ``config`` (the entry
# lacks a credential or a URL; nothing was sent), ``request`` (the dashboard
# asked for something invalid), ``backend`` (unreachable, timeout, 5xx,
# non-JSON, an unparseable answer). The route turns the kind into the HTTP
# status the browser sees: 404, 503, 503, 400, 502.

ERROR_KINDS = ("not_found", "auth", "config", "request", "backend")


class BackendError(HTTPException):
    """An adapter could not get an answer; ``kind`` says why. ``http_status``
    is the status the backend itself answered with (``None`` when the failure
    was not an HTTP answer), so a caller can tell a refused request from an
    outage without parsing the message (see :func:`rejected_request`)."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        kind: str = "backend",
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.kind = kind if kind in ERROR_KINDS else "backend"
        self.http_status = http_status


def rejected_request(exc: BaseException) -> bool:
    """True when the backend answered a 4xx that was neither auth nor
    not-found: the request's shape was refused (an older Tempo rejecting a
    ``select()`` pipeline, OpenObserve refusing an unknown column, SigNoz
    refusing a GET), so a differently shaped retry may succeed. An outage, a
    timeout or a 5xx never is: a second request would only hit a dead backend
    twice (#296)."""
    status = getattr(exc, "http_status", None)
    if getattr(exc, "kind", None) != "backend" or not isinstance(status, int):
        return False
    return 400 <= status < 500


def first_accepted(*attempts: Callable[[], Any]) -> Any:
    """Run ``attempts`` in order and answer with the first that the backend
    accepts. A refused request shape (:func:`rejected_request`) moves on to
    the next spelling; any other failure (unreachable, auth, not found, 5xx)
    is raised at once, and when every spelling was refused the last refusal
    is (#519)."""
    last: Optional[BackendError] = None
    for attempt in attempts:
        try:
            return attempt()
        except BackendError as exc:
            if not rejected_request(exc):
                raise
            last = exc
    assert last is not None  # at least one attempt was given
    raise last


class ConfigError(BackendError):
    """The backend entry is incomplete (a missing credential, no URL): a 503 the
    user fixes in ``hermes_otel.yaml``, not a backend failure."""

    def __init__(self, detail: str) -> None:
        super().__init__(503, detail, "config")


def error_kind(exc: BaseException) -> str:
    """The ``kind`` of any exception an adapter call may raise."""
    kind = getattr(exc, "kind", None)
    if isinstance(kind, str) and kind in ERROR_KINDS:
        return kind
    status = getattr(exc, "status_code", None)
    if status in (400, 422):
        return "request"
    if status == 503:
        return "config"
    return "backend"


# ── Docker-host rewrite shim ────────────────────────────────────────────

_IN_DOCKER = Path("/.dockerenv").exists()
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def rewrite_host_for_docker(host: str) -> str:
    """Swap loopback for ``host.docker.internal`` when running in a container.

    Honours the ``HERMES_OTEL_QUERY_HOST`` override.
    """
    override = os.environ.get("HERMES_OTEL_QUERY_HOST", "").strip()
    if override:
        return override
    if _IN_DOCKER and host in _LOCAL_HOSTS:
        return "host.docker.internal"
    return host


# ── Config helpers ─────────────────────────────────────────────────────


def resolve_env_or_literal(cfg: Dict[str, Any], literal_key: str, env_key: str) -> Optional[str]:
    """``cfg[literal_key]`` when set, else the value of the env var ``cfg[env_key]``
    names, else the vendor variables the exporter falls back to for that field
    of the entry's type (``BackendSpec.fallback_envs``: ``OTEL_OPENOBSERVE_USER``,
    ``OTEL_GREPTIME_DB``, ...).

    The exporter's own rule (``backends.choose_secret``, inline > named env >
    fallbacks), so an entry queries with the credential it exports with
    (#413, #614). ``*_env`` keeps secrets out of yaml; inline stays for
    convenience. ``${VAR}`` in the entry is expanded before the adapter is
    built (``_instantiate``).
    """
    lit = cfg.get(literal_key)
    env_name = cfg.get(env_key)
    spec = spec_for(cfg.get("type") if isinstance(cfg.get("type"), str) else None)
    return choose_secret(
        lit if isinstance(lit, str) else None,
        env_name.strip() if isinstance(env_name, str) else None,
        spec.fallback_envs(literal_key) if spec is not None else (),
    ).value


def query_origin(
    cfg: Dict[str, Any], default_port: Optional[int], *, endpoint_port: str = "ignore"
) -> str:
    """``scheme://host[:port]`` of the backend's read API, from the entry.

    The host (loopback rewritten for a container) and the scheme come from the
    OTLP ``endpoint``; the port is ``query_port`` on the entry, else:

    * ``endpoint_port="ignore"`` — the type's default (the read API has a port
      of its own: Phoenix 6006, SigNoz 3301, the Aspire UI 18888, ...);
    * ``endpoint_port="fallback"`` — the endpoint's own port, then the default
      (stores whose read API shares the OTLP port: GreptimeDB's 4000,
      VictoriaTraces' 10428, Gigapipe's 3100, Zipkin's 9411);
    * ``endpoint_port="first"`` — the endpoint's port even over ``query_port``
      (Langfuse, whose base URL is the UI).

    With no default and no port anywhere the origin carries none (Agenta's
    cloud hosts).
    """
    parsed = _urlparse.urlparse(str(cfg.get("endpoint") or ""))
    host = rewrite_host_for_docker(parsed.hostname or "localhost")
    scheme = parsed.scheme or "http"
    if endpoint_port == "first":
        port = parsed.port or cfg.get("query_port") or default_port
    elif endpoint_port == "fallback":
        port = cfg.get("query_port") or parsed.port or default_port
    else:
        port = cfg.get("query_port") or default_port
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def basic_auth(user: Optional[str], password: Optional[str]) -> Dict[str, str]:
    """``Authorization: Basic`` for ``user:password`` (an empty password is fine)."""
    import base64

    token = base64.b64encode(f"{user}:{password or ''}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def bearer(token: Optional[str]) -> Dict[str, str]:
    """``Authorization: Bearer`` when there is a token, else no header."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def require_credential(value: Optional[str], detail: str) -> str:
    """``value``, or the ``config`` error ``detail`` (what is missing and how
    to set it) when the entry has none: nothing is sent to the backend."""
    if not value:
        raise ConfigError(detail)
    return value


# ── Probe caches (#403) ────────────────────────────────────────────────
#
# An adapter that learns something about a server once per process (which
# API version it speaks, which GraphQL arguments it takes, a table's columns)
# keeps it in a module-level dict and registers it here, so every cache is
# cleared together (``clear_probe_caches``) and none leaks across tests or a
# config reload.

_PROBE_CACHES: List[Dict[Any, Any]] = []


def register_cache(cache: Dict[Any, Any]) -> Dict[Any, Any]:
    _PROBE_CACHES.append(cache)
    return cache


def clear_probe_caches() -> None:
    for cache in _PROBE_CACHES:
        cache.clear()


# ── Structured filter the UI sends to every adapter ────────────────────


@dataclass
class StructuredFilter:
    """Portable filter spec. Adapters translate fields they can honour
    and silently drop ones they can't; ``raw`` is always the native
    escape hatch and is passed through verbatim.
    """

    service: Optional[str] = None
    # The search bar's span-name field. Despite the name it is an EXACT match
    # on most backends (Jaeger ``operation=``, Zipkin ``spanName=``, Phoenix
    # ``name ==``, ClickHouse ``SpanName =``, Langfuse ``name=`` ...) and a
    # regex only where the backend's query language has one (TraceQL, SigNoz).
    # Renaming the field is a UI change; until then this is the contract, and
    # ``span_matches`` re-checks it as an exact match.
    name_regex: Optional[str] = None
    # The kind filter (#292): a span-name prefix such as ``tool.`` or ``agent``.
    # Every backend can express a prefix (a regex anchor, ``like 'x%'``, a
    # substring plus a client-side check); a regex only some could.
    name_prefix: Optional[str] = None
    attr_equals: Dict[str, str] = field(default_factory=dict)
    min_duration_ms: Optional[int] = None
    status: Optional[str] = None  # "ok" | "error"
    free_text: Optional[str] = None
    raw: Optional[str] = None
    # When True, each adapter should restrict results to traces whose
    # root span matches (instead of any span in the trace). Defaults
    # to True since "one trace per match, from the top" is what the
    # list view usually wants; set False via the UI to widen.
    roots_only: bool = True
    # Keyset paging cursor (#292): only traces that STARTED strictly before
    # this instant (unix ns). Adapters pass it as the query's end bound and
    # :func:`strictly_older_traces` makes the cut exact; :func:`trace_page`
    # hands the next cursor back.
    before_ns: Optional[int] = None

    def set_fields(self) -> List[str]:
        """The ``filter_support`` keys this filter actually uses."""
        out: List[str] = []
        if self.service:
            out.append("service")
        if self.name_regex or self.name_prefix:
            out.append("name")
        if self.attr_equals.get("llm.model_name"):
            out.append("model")
        if self.attr_equals.get("hermes.session_id"):
            out.append("session")
        if self.attr_equals.get("tool.name"):
            out.append("tool")
        if self.min_duration_ms:
            out.append("min_duration")
        if self.status == "error":
            out.append("status_error")
        elif self.status == "ok":
            out.append("status_ok")
        if self.free_text:
            out.append("free_text")
        if self.raw and self.raw.strip():
            out.append("raw")
        if self.roots_only:
            out.append("roots_only")
        return out


# The search-bar fields an adapter declares support for (#292). Values:
# ``server`` (part of the backend query), ``client`` (applied to the rows the
# backend returned, so a page can come back short) or ``none`` (ignored).
FILTER_KEYS = (
    "service",
    "name",
    "model",
    "session",
    "tool",
    "min_duration",
    "status_error",
    "status_ok",
    "free_text",
    "raw",
    "roots_only",
)
FILTER_LEVELS = ("server", "client", "none")


def filter_support_of(adapter: Any) -> Dict[str, str]:
    """An adapter's declared support, with every key present and a valid level."""
    declared = getattr(adapter, "filter_support", None) or {}
    return {
        k: (declared.get(k) if declared.get(k) in FILTER_LEVELS else "none") for k in FILTER_KEYS
    }


def effective_filter_support(adapter: Any, f: StructuredFilter) -> Dict[str, str]:
    """The declaration, with an instance's per-request override on top: an
    adapter may honour fewer fields for this particular filter (a raw query
    that replaces the structured predicates, #296) or more for the server it
    talks to (Langfuse v4 vs v3). A class declares only; ``/status`` and the
    search agree because both read this (#519)."""
    support = filter_support_of(adapter)
    per_request = getattr(adapter, "filter_support_for", None)
    if not callable(per_request) or isinstance(adapter, type):
        return support
    override = per_request(f) or {}
    return {
        k: (override.get(k) if override.get(k) in FILTER_LEVELS else support[k]) for k in support
    }


def split_applied_filters(adapter: Any, f: StructuredFilter) -> Tuple[List[str], List[str]]:
    """``(applied, ignored)``: which of the filter's set fields the adapter honours
    (server- or client-side) and which it drops."""
    support = effective_filter_support(adapter, f)
    used = f.set_fields()
    applied = [k for k in used if support.get(k) != "none"]
    ignored = [k for k in used if support.get(k) == "none"]
    return applied, ignored


def parse_kv_tokens(raw: Optional[str]) -> Dict[str, str]:
    """``key=value key2=value2`` → a dict (the native-query grammar of the
    adapters whose backend takes query parameters or tags: Jaeger, Langfuse)."""
    if not raw:
        return {}
    out: Dict[str, str] = {}
    for tok in raw.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip().strip("\"'")
    return out


# ── The search template (#403): what every adapter's search() does ─────
#
# Find each trace's root and re-check the structured predicates on a span the
# backend could only match loosely. The window/cursor bounds and the
# over-fetch arithmetic are in ``paging``. Each piece used to be copied into
# ten adapters, each with its own spelling.


def root_of(
    spans: Sequence[Dict[str, Any]],
    span_id: Callable[[Dict[str, Any]], Any],
    parent_id: Callable[[Dict[str, Any]], Any],
) -> Optional[Dict[str, Any]]:
    """The trace's root among ``spans``: the first whose parent is absent or
    not among them (an orphan whose parent was cut counts; a caller that must
    tell a true root from an orphan checks ``parent_id`` again)."""
    if not spans:
        return None
    ids = {span_id(sp) for sp in spans}
    for sp in spans:
        parent = parent_id(sp)
        if not parent or parent not in ids:
            return sp
    return spans[0]


def span_matches(
    f: StructuredFilter,
    *,
    name: str,
    attrs: Dict[str, Any],
    duration_ns: Optional[int] = None,
    error: Optional[bool] = None,
    text: Optional[str] = None,
    equals: Optional[Dict[str, Any]] = None,
) -> bool:
    """Re-check the structured predicates on one span, for a backend whose
    query matched any span of the trace (Jaeger's ``tags=``, Zipkin's
    ``annotationQuery``) or none at all (Aspire). ``equals`` replaces
    ``f.attr_equals`` when the adapter folded its raw tags into the same
    dict; ``error`` is the span's status (``None`` skips the status check);
    ``text`` is what the free-text search reads (``None`` skips it). Names
    are exact (see ``StructuredFilter.name_regex``); attribute values compare
    case-insensitively as strings."""
    if f.name_prefix and not name.startswith(f.name_prefix):
        return False
    if f.name_regex and name != f.name_regex:
        return False
    for k, v in (f.attr_equals if equals is None else equals).items():
        have = attrs.get(k)
        if have is None or str(have).lower() != str(v).lower():
            return False
    if f.min_duration_ms and duration_ns is not None:
        if duration_ns < int(f.min_duration_ms) * 1_000_000:
            return False
    if error is not None:
        if f.status == "error" and not error:
            return False
        if f.status == "ok" and error:
            return False
    if f.free_text and text is not None and f.free_text.lower() not in text.lower():
        return False
    return True


def not_found(trace_id: str, backend: str, detail: Optional[str] = None) -> BackendError:
    """The ``not_found`` error every ``get_trace`` raises for an unknown id."""
    return BackendError(404, detail or f"Trace {trace_id} not found in {backend}", "not_found")


_METRIC_NAME_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:.]*$")
_LABEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def validate_metric_name(name: str) -> str:
    """A metric name safe to splice into PromQL / MQL / SQL; ``request`` error otherwise."""
    if not _METRIC_NAME_RE.match(name or ""):
        raise BackendError(400, f"Invalid metric name {name!r}", "request")
    return name


def validate_label_name(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    if not _LABEL_NAME_RE.match(label):
        raise BackendError(400, f"Invalid group_by attribute {label!r}", "request")
    return label


# ── Adapter base ───────────────────────────────────────────────────────


class BackendAdapter:
    """Abstract base for a trace-query backend.

    Subclasses set ``handles`` (frozenset of backend ``type`` values from
    the plugin config) and implement :meth:`search` / :meth:`get_trace`.
    """

    handles: "frozenset[str]" = frozenset()
    query_lang_label: str = "query"
    raw_placeholder: str = ""
    # Capabilities beyond traces (#182). An adapter that stores metrics or
    # logs sets these and implements the matching methods; the API advertises
    # them in /status so the UI offers only what works.
    supports_metrics: bool = False
    supports_logs: bool = False
    # Which search-bar fields reach the backend (``server``), are applied to
    # the returned rows (``client``) or are dropped (``none``); see
    # ``FILTER_KEYS``. ``/status`` publishes it so the UI can grey fields out.
    filter_support: Dict[str, str] = {}

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg

    # Reported in /status so the frontend can label the raw input and
    # show the active backend URL.
    # The read API's origin (see :func:`query_origin`); every adapter sets it
    # in ``__init__`` and ``status()`` reports it.
    query_url: str = ""

    def status(self) -> Dict[str, Any]:
        out = {
            "type": self.cfg.get("type"),
            "name": self.cfg.get("name") or self.cfg.get("type"),
            "query_lang_label": self.query_lang_label,
            "raw_placeholder": self.raw_placeholder,
            "metrics": self.supports_metrics,
            "logs": self.supports_logs,
            "filters": effective_filter_support(self, StructuredFilter()),
        }
        if self.query_url:
            out["query_url"] = self.query_url
        return out

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        """``{traces: [...], has_more, next_before_ns}`` (see :func:`trace_page`):
        at most ``limit`` trace rows, newest first, honouring ``f.before_ns``."""
        raise NotImplementedError

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """OTLP-JSON ``{batches: [...]}`` plus ``truncated: true`` when the
        backend's span cap was hit and ``span_count`` when known. Raises a
        ``not_found`` :class:`BackendError` for an unknown id."""
        raise NotImplementedError

    def trace_url(self, trace_id: str) -> Optional[str]:
        """Link to this trace in the backend's own UI, when the adapter knows
        the route (#185). ``None`` means no link is offered; never a guess."""
        return None

    def _headers(self) -> Dict[str, str]:
        """The HTTP headers every read carries (auth, mostly): none by default.
        An adapter with credentials overrides this; the shared readers
        (``_prometheus``, ``_loki``) call it."""
        return {}

    # ── metrics (same shapes as the live store's endpoints) ───────────
    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        """``[{name, count?, lastTs?}]`` for instruments with data in the window."""
        raise NotImplementedError

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        """``{name, agg, bucketS, buckets: [ns...], series: {label: [v|null...]}, points}``."""
        raise NotImplementedError

    # ── logs (same record shape as the live store: level, logger, body, ─
    # ── time_unix_nano, trace_id, session_id, plus since #268 span_id, ───
    # ── severity_number, event_name and attributes) ──────────────────────
    def logs_search(
        self, f: "LogFilter", start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        raise NotImplementedError


@dataclass
class LogFilter:
    """Portable log filter; adapters honour what they can."""

    trace_id: Optional[str] = None
    session: Optional[str] = None
    min_level: int = 0  # python logging numbers: 20 INFO, 30 WARNING, 40 ERROR
    logger: Optional[str] = None
    text: Optional[str] = None
    # Keyset paging cursor: only records strictly older than this instant
    # (unix ns). Pages are newest-first, so "the next page" is the rows older
    # than the oldest row shown; a cursor survives new lines arriving, which
    # an offset does not.
    before_ns: Optional[int] = None
    # Structured events (#267): one event name, or only rows that are events.
    event_name: Optional[str] = None
    events_only: bool = False
    # The lines written inside one span (#290).
    span_id: Optional[str] = None


# Row keys every adapter fills; anything else a backend returns goes under
# ``attributes`` so the dashboard can show it without knowing the backend.
LOG_ROW_KEYS = (
    "level",
    "severity_number",
    "logger",
    "body",
    "time_unix_nano",
    "trace_id",
    "span_id",
    "session_id",
    "event_name",
    "attributes",
)


def finish_log_row(row: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalise one adapter row: OTel level spelling, severity number, the
    event name and the leftover fields under ``attributes`` (#268)."""
    level = str(row.get("level") or "INFO").upper()
    level = {"WARNING": "WARN", "CRITICAL": "FATAL"}.get(level, level)
    row["level"] = level
    if row.get("severity_number") is None:
        row["severity_number"] = severity_number(level)
    attrs: Dict[str, Any] = dict(row.get("attributes") or {})
    for k, v in (extra or {}).items():
        if v is None or v == "" or k in LOG_ROW_KEYS:
            continue
        attrs.setdefault(str(k), v)
    event_name = (
        row.get("event_name") or attrs.pop("event_name", None) or attrs.pop("event.name", None)
    )
    row["event_name"] = str(event_name) if event_name else None
    row["attributes"] = attrs
    row.setdefault("span_id", None)
    return row


# One column name, or the names tried in order (Loki's ``severity_text``,
# then its ``detected_level``); ``""`` when the backend has no such column.
Column = Union[str, Tuple[str, ...]]


@dataclass(frozen=True)
class LogColumns:
    """Which columns of a backend's raw log row fill the record's fields (#514).

    A column defaults to the record key it fills: that is where a caller puts
    a value the backend hands over beside its row (Loki's line, Uptrace's
    ids). ``time`` only names the column (the caller converts it and passes
    ``time_ns``). The named columns plus ``drop`` (the backend's bookkeeping,
    ``flags``) are the row; the rest of a raw row lands under ``attributes``,
    each key through ``attr_key`` (``_attrs.dotted`` where names were
    flattened).
    """

    body: Column = "body"
    time: Column = ""
    level: Column = "level"
    severity_number: Column = "severity_number"
    logger: Column = "logger"
    trace_id: Column = "trace_id"
    span_id: Column = "span_id"
    session_id: Column = "session_id"
    event_name: Column = "event_name"
    drop: Tuple[str, ...] = ()
    attr_key: Callable[[str], str] = str
    known: FrozenSet[str] = field(init=False)

    def __post_init__(self) -> None:
        columns = [getattr(self, f.name) for f in fields(self) if f.type == "Column"]
        names = {n for c in columns for n in ((c,) if isinstance(c, str) else c) if n}
        object.__setattr__(self, "known", frozenset(names | set(self.drop)))


def log_row(raw: Dict[str, Any], cols: LogColumns, *, time_ns: int) -> Dict[str, Any]:
    """One raw backend log row → the live store's record shape (#514): the
    fields from the columns ``cols`` names (the first non-empty of a fallback
    tuple), ``time_ns`` as the time, and every other key of ``raw`` through
    :func:`finish_log_row`, which spells the level the OTel way, fills a
    missing severity number and keeps the leftovers under ``attributes``."""

    def col(column: Column) -> Any:
        for name in (column,) if isinstance(column, str) else column:
            value = raw.get(name) if name else None
            if value:
                return value
        return None

    row = {
        "level": col(cols.level),
        "severity_number": to_int(col(cols.severity_number)) or None,
        "logger": col(cols.logger) or "",
        "body": col(cols.body) or "",
        "time_unix_nano": time_ns,
        "trace_id": col(cols.trace_id) or None,
        "span_id": col(cols.span_id) or None,
        "session_id": col(cols.session_id) or None,
        "event_name": col(cols.event_name) or None,
    }
    return finish_log_row(row, {cols.attr_key(k): v for k, v in raw.items() if k not in cols.known})


# ── HTTP helpers (stdlib only to avoid extra deps in the dashboard venv) ──


_ERROR_BODY_CHARS = 200


def _error_body(text: str) -> str:
    """What of a backend's error body is worth echoing: a short excerpt, never
    an HTML page (an SPA answering a wrong route is pages of markup)."""
    body = (text or "").strip()
    if body[:1] == "<" or "<html" in body[:200].lower():
        return "(an HTML page, not an API answer)"
    body = " ".join(body.split())
    return body[:_ERROR_BODY_CHARS] + ("…" if len(body) > _ERROR_BODY_CHARS else "")


_RECORD_ENV = "HERMES_OTEL_RECORD_HTTP"
_SECRET_QUERY_KEYS = ("token", "key", "secret", "password", "api_key", "apikey")


def _record_exchange(req: Any, status: Optional[int], body: Optional[bytes]) -> None:
    """Dev-only: with ``HERMES_OTEL_RECORD_HTTP=<dir>`` every request this
    function sends is appended to ``<dir>/exchanges.jsonl`` with its answer,
    the raw material for an adapter golden (#408). Credentials never land in
    the file: ``Authorization`` / ``*-key`` headers and secret-looking query
    parameters are masked. Off unless the variable is set; never raises."""
    target = os.environ.get(_RECORD_ENV, "").strip()
    if not target:
        return
    try:
        import json as _json
        from pathlib import Path as _Path

        parsed = _urlparse.urlsplit(req.full_url)
        query = [
            (k, "***" if any(s in k.lower() for s in _SECRET_QUERY_KEYS) else v)
            for k, v in _urlparse.parse_qsl(parsed.query, keep_blank_values=True)
        ]
        url = _urlparse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, _urlparse.urlencode(query, safe="*"), "")
        )
        headers = {
            k: ("***" if k.lower() in ("authorization",) or k.lower().endswith("-key") else v)
            for k, v in req.header_items()
        }
        data = req.data if isinstance(req.data, bytes) else None
        text = body.decode("utf-8", errors="replace") if body is not None else None
        try:
            answer: Any = _json.loads(text) if text else text
        except ValueError:
            answer = text
        entry = {
            "method": req.get_method(),
            "url": url,
            "headers": headers,
            "request": data.decode("utf-8", errors="replace") if data else None,
            "status": status,
            "response": answer,
        }
        d = _Path(target).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "exchanges.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _execute(
    req: _urlrequest.Request,
    timeout: float,
    *,
    raw: bool = False,
    label: str = "Backend",
    auth_codes: Tuple[int, ...] = (401, 403),
    not_found_is_backend: bool = False,
) -> Any:
    """Send one request; every failure is a :class:`BackendError` with a kind.

    404 → ``not_found`` (or ``backend`` with ``not_found_is_backend``, for a
    query-language store whose 404 is a bad query, never a missing trace), the
    ``auth_codes`` → ``auth``, any other HTTP error, a network error, a timeout
    or a non-JSON body → ``backend``; an HTTP answer's status is on the error
    as ``http_status``. ``raw`` returns the body as bytes instead of parsed
    JSON (VictoriaLogs' NDJSON); ``label`` names the backend in the messages.
    """
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except _urlerror.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        _record_exchange(req, e.code, detail.encode("utf-8"))
        excerpt = _error_body(detail)
        if e.code == 404 and not not_found_is_backend:
            raise BackendError(404, f"{label} returned 404: {excerpt}", "not_found", e.code)
        if e.code in auth_codes:
            raise BackendError(
                503, f"{label} rejected the credentials ({e.code}): {excerpt}", "auth", e.code
            )
        raise BackendError(502, f"{label} returned {e.code}: {excerpt}", "backend", e.code)
    except _urlerror.URLError as e:
        raise BackendError(502, f"{label} unreachable: {e.reason}")
    except Exception as e:
        raise BackendError(502, f"{label} query failed: {e}")
    _record_exchange(req, 200, body)
    if raw:
        return body
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        raise BackendError(502, f"{label} returned non-JSON: {e}")


def http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> Any:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = _urlrequest.Request(url, headers=req_headers)
    return _execute(req, timeout)


def http_post_json(
    url: str,
    body: Any,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Any:
    data = json.dumps(body).encode("utf-8")
    req_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = _urlrequest.Request(url, data=data, headers=req_headers, method="POST")
    return _execute(req, timeout)
