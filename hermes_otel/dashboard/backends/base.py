"""Core abstractions for dashboard backend adapters.

Each adapter is a subclass of :class:`BackendAdapter` and uses
:func:`register` to advertise which ``type:`` values in
``config.yaml`` it handles.

Adapters return data in two normalized shapes:

* ``search()`` → Tempo-style ``{traces: [{traceID, rootServiceName,
  rootTraceName, startTimeUnixNano, durationMs, spanSets: [...]}]}``
  where ``spanSets[0].spans[].attributes`` carries card-relevant
  attributes (model, provider, tokens, input/output previews, status).

* ``get_trace()`` → OTLP JSON ``{batches: [{resource, scopeSpans:
  [{spans: [...]}]}]}`` — the shape the frontend's ``buildSpanTree``
  already consumes.

HTTP + OTLP helpers live here to keep adapters short. No side effects
at import time.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib import error as _urlerror
from urllib import request as _urlrequest

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
    """An adapter could not get an answer; ``kind`` says why."""

    def __init__(self, status_code: int, detail: str, kind: str = "backend") -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.kind = kind if kind in ERROR_KINDS else "backend"


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
    """Return ``cfg[env_key]``-resolved env value, else ``cfg[literal_key]``.

    Adapters prefer the ``*_env`` variants so secrets stay out of yaml;
    inline fields remain supported for convenience.
    """
    env_name = cfg.get(env_key)
    if isinstance(env_name, str) and env_name.strip():
        v = os.environ.get(env_name.strip(), "").strip()
        if v:
            return v
    lit = cfg.get(literal_key)
    if isinstance(lit, str) and lit.strip():
        return lit.strip()
    return None


# ── Structured filter the UI sends to every adapter ────────────────────


@dataclass
class StructuredFilter:
    """Portable filter spec. Adapters translate fields they can honour
    and silently drop ones they can't; ``raw`` is always the native
    escape hatch and is passed through verbatim.
    """

    service: Optional[str] = None
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


def split_applied_filters(adapter: Any, f: StructuredFilter) -> Tuple[List[str], List[str]]:
    """``(applied, ignored)``: which of the filter's set fields the adapter honours
    (server- or client-side) and which it drops."""
    support = filter_support_of(adapter)
    used = f.set_fields()
    applied = [k for k in used if support.get(k) != "none"]
    ignored = [k for k in used if support.get(k) == "none"]
    return applied, ignored


def strictly_older_traces(
    traces: List[Dict[str, Any]], f: StructuredFilter
) -> List[Dict[str, Any]]:
    """Drop trace rows that started at or after the cursor (backends bound time
    at their own precision; this keeps the page exact)."""
    if not f.before_ns:
        return traces
    cut = int(f.before_ns)
    return [t for t in traces if int(t.get("startTimeUnixNano") or 0) < cut]


def trace_page(traces: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    """The envelope a trace search returns: newest-first rows, the start of the
    oldest row as the next cursor, and whether asking is worthwhile. ``traces``
    should carry at least one row beyond ``limit`` so ``has_more`` is exact."""
    rows = sorted(traces, key=lambda t: int(t.get("startTimeUnixNano") or 0), reverse=True)
    n = int(limit)
    page, rest = rows[:n], rows[n:]
    starts = [int(t.get("startTimeUnixNano") or 0) for t in page if t.get("startTimeUnixNano")]
    has_more = bool(rest)
    return {
        "traces": page,
        "next_before_ns": min(starts) if has_more and starts else None,
        "has_more": has_more,
    }


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


# Units the OTLP-to-Prometheus translation appends to a name; ``{token}``
# style units are dropped by it, so no ``_tokens`` here.
_PROM_UNIT_SUFFIXES = (
    "_milliseconds",
    "_seconds",
    "_bytes",
    "_ratio",
    "_percent",
    "_watts",
)


def otlp_metric_name(native: str) -> str:
    """The plugin's dotted instrument name behind a backend's native spelling,
    so the UI can match its curated panels on every source (#284).

    ``hermes_token_usage_total`` → ``hermes.token.usage``;
    ``hermes_tool_duration_milliseconds_sum`` → ``hermes.tool.duration``;
    a dotted name passes through. Best effort: the Prometheus translation
    appends ``_total`` to counters and the unit to everything with one.
    """
    n = str(native or "")
    if "." in n:
        base = n
        for suffix in (".sum", ".count", ".bucket", ".min", ".max"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base
    base = n
    for suffix in ("_total", "_sum", "_count", "_bucket"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    for suffix in _PROM_UNIT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    # Name segments that carry an underscore of their own (``gen_ai``,
    # ``prompt_cache``) are kept whole; every other underscore was a dot.
    toks = base.split("_")
    out: List[str] = []
    i = 0
    while i < len(toks):
        pair = f"{toks[i]}_{toks[i + 1]}" if i + 1 < len(toks) else None
        if pair in _UNDERSCORE_SEGMENTS:
            out.append(pair)
            i += 2
        else:
            out.append(toks[i])
            i += 1
    return ".".join(out)


_UNDERSCORE_SEGMENTS = ("gen_ai", "prompt_cache")


# ── Adapter base ───────────────────────────────────────────────────────


class BackendAdapter:
    """Abstract base for a trace-query backend.

    Subclasses set ``handles`` (frozenset of ``type`` values from
    ``config.yaml``) and implement :meth:`search` / :meth:`get_trace`.
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
    # show the active backend URL. Subclasses typically add ``query_url``.
    def status(self) -> Dict[str, Any]:
        return {
            "type": self.cfg.get("type"),
            "name": self.cfg.get("name") or self.cfg.get("type"),
            "query_lang_label": self.query_lang_label,
            "raw_placeholder": self.raw_placeholder,
            "metrics": self.supports_metrics,
            "logs": self.supports_logs,
            "filters": filter_support_of(self),
        }

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

_SEVERITY_NUMBERS = {
    "TRACE": 1,
    "DEBUG": 5,
    "INFO": 9,
    "WARN": 13,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
    "FATAL": 21,
}


def severity_number_for(level: Any) -> Optional[int]:
    """OTel severity number for a level spelling (Python or OTel); None when unknown."""
    if level is None:
        return None
    text = str(level).strip().upper()
    if text.isdigit():
        n = int(text)
        return n if n <= 24 else {10: 5, 20: 9, 30: 13, 40: 17, 50: 21}.get(n)
    return _SEVERITY_NUMBERS.get(text)


def finish_log_row(row: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalise one adapter row: OTel level spelling, severity number, the
    event name and the leftover fields under ``attributes`` (#268)."""
    level = str(row.get("level") or "INFO").upper()
    level = {"WARNING": "WARN", "CRITICAL": "FATAL"}.get(level, level)
    row["level"] = level
    if row.get("severity_number") is None:
        row["severity_number"] = severity_number_for(level)
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


def log_end_ns(end_s: int, f: "LogFilter") -> int:
    """The exclusive upper bound of a logs query in ns: the window end, tightened
    by the paging cursor when there is one."""
    end_ns = int(end_s) * 1_000_000_000
    return min(end_ns, int(f.before_ns)) if f.before_ns else end_ns


def strictly_older(records: List[Dict[str, Any]], f: "LogFilter") -> List[Dict[str, Any]]:
    """Drop rows at or after the cursor. Backends bound time at their own
    precision (ms, µs); this keeps the page exact at the nanosecond so a
    cursor never repeats the row it was taken from."""
    if not f.before_ns:
        return records
    cut = int(f.before_ns)
    return [r for r in records if int(r.get("time_unix_nano") or 0) < cut]


# Adapters fetch this many rows beyond the page so log_page() can see whether
# the last row's timestamp continues past the page boundary.
LOG_PAGE_SLACK = 50


def log_page(records: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    """The envelope every logs route returns: the rows plus where the next
    page starts and whether asking is worthwhile.

    The cursor is a timestamp, and timestamps are not unique (a burst can log
    several lines in one nanosecond). A page therefore keeps every row that
    shares its last row's timestamp, so the next page — "strictly older than
    that timestamp" — neither repeats nor loses a tied row. ``records`` must be
    newest-first and should carry ``LOG_PAGE_SLACK`` rows beyond ``limit``.
    """
    rows = list(records)
    n = int(limit)
    if len(rows) > n:
        boundary = int(rows[n - 1].get("time_unix_nano") or 0)
        while n < len(rows) and int(rows[n].get("time_unix_nano") or 0) == boundary:
            n += 1
    page, rest = rows[:n], rows[n:]
    times = [int(r.get("time_unix_nano") or 0) for r in page if r.get("time_unix_nano")]
    has_more = bool(rest)
    return {
        "logs": page,
        "next_before_ns": min(times) if has_more and times else None,
        "has_more": has_more,
    }


def bucketize(
    points: Iterable[Tuple[int, float, str]],
    start_ns: int,
    end_ns: int,
    bucket_s: int,
    agg: str = "sum",
) -> Dict[str, Any]:
    """Fold ``(ts_ns, value, label)`` points into the live store's bucket shape.

    Shared by the backend adapters so every source renders identically.
    """
    bucket_ns = max(1, int(bucket_s)) * 1_000_000_000
    start_ns = int(start_ns) - int(start_ns) % bucket_ns
    n = max(1, int((int(end_ns) - start_ns) // bucket_ns) + 1)
    series: Dict[str, List[List[float]]] = {}
    count = 0
    for ts, value, label in points:
        count += 1
        idx = int((int(ts) - start_ns) // bucket_ns)
        if idx < 0 or idx >= n:
            continue
        series.setdefault(label or "_", [[] for _ in range(n)])[idx].append(float(value))

    def reduce(vals: List[float]) -> Optional[float]:
        if not vals:
            return None
        if agg == "count":
            return float(len(vals))
        if agg == "avg":
            return sum(vals) / len(vals)
        if agg == "max":
            return max(vals)
        if agg == "last":
            return vals[-1]
        return sum(vals)

    return {
        "agg": agg,
        "bucketS": int(bucket_s),
        "buckets": [start_ns + i * bucket_ns for i in range(n)],
        "series": {label: [reduce(v) for v in vals] for label, vals in series.items()},
        "points": count,
    }


def counter_increases(
    samples: Iterable[Tuple[int, float, str]],
    *,
    started_in_window: Optional[Iterable[str]] = None,
    window_start_ns: Optional[int] = None,
) -> List[Tuple[int, float, str]]:
    """Turn cumulative counter samples into per-sample increases.

    OTLP counters arrive cumulative (the exporter's temporality); a bucketed
    "tokens per 15 s" chart needs the increase between consecutive samples of
    the same series. A drop (process restart) counts the new value in full.

    A series' first sample is an increase too when the series started inside
    the window: every ``hermes -z`` run is a new process whose counters start
    at zero, so its one or two cumulative samples carry the whole turn.
    Callers say which series those are, either by name (``started_in_window``,
    for stores that record the counter's start time) or by giving
    ``window_start_ns`` and passing samples from before the window as well: a
    series whose first sample lies at or after the window start is new, one
    with an earlier sample is not (and its earlier value is the baseline).
    Without either, the first sample is a baseline only (the old behaviour).
    """
    new = set(started_in_window or ())
    last: Dict[str, float] = {}
    out: List[Tuple[int, float, str]] = []
    for ts, value, label in sorted(samples, key=lambda p: (p[2], p[0])):
        prev = last.get(label)
        if prev is None:
            if label in new or (window_start_ns is not None and int(ts) >= int(window_start_ns)):
                out.append((ts, value, label))
        else:
            out.append((ts, value if value < prev else value - prev, label))
        last[label] = value
    return out


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


def _execute(req: _urlrequest.Request, timeout: float) -> Any:
    """Send one request; every failure is a :class:`BackendError` with a kind.

    404 → ``not_found``, 401/403 → ``auth``, any other HTTP error, a network
    error, a timeout or a non-JSON body → ``backend``.
    """
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except _urlerror.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        excerpt = _error_body(detail)
        if e.code == 404:
            raise BackendError(404, f"Backend returned 404: {excerpt}", "not_found")
        if e.code in (401, 403):
            raise BackendError(
                503, f"Backend rejected the credentials ({e.code}): {excerpt}", "auth"
            )
        raise BackendError(502, f"Backend returned {e.code}: {excerpt}")
    except _urlerror.URLError as e:
        raise BackendError(502, f"Backend unreachable: {e.reason}")
    except Exception as e:
        raise BackendError(502, f"Backend query failed: {e}")
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        raise BackendError(502, f"Backend returned non-JSON: {e}")


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


# ── OTLP shape helpers ────────────────────────────────────────────────
#
# Building OTLP JSON attributes by hand is tedious and every non-Tempo
# adapter needs it. These helpers are intentionally permissive — they
# accept any Python value and stringify-as-JSON for unknown types so an
# unexpected dict attribute doesn't crash the response.


def otlp_attr(key: str, value: Any) -> Dict[str, Any]:
    """Wrap ``(key, value)`` into an OTLP attribute entry."""
    if value is None:
        return {"key": key, "value": {}}
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, str):
        return {"key": key, "value": {"stringValue": value}}
    # dict / list / anything else: stringify as JSON so the UI can still
    # render it. Preserves structure in a predictable form.
    try:
        return {"key": key, "value": {"stringValue": json.dumps(value, default=str)}}
    except Exception:
        return {"key": key, "value": {"stringValue": str(value)}}


def otlp_attrs_from_dict(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Render a flat dict as a list of OTLP attribute entries."""
    return [otlp_attr(k, v) for k, v in d.items() if v is not None]


def otlp_status(code: Union[str, int, None], message: str = "") -> Dict[str, Any]:
    """Normalize various status flavours into an OTLP ``Status`` message."""
    if code is None:
        return {}
    if isinstance(code, int):
        return {"code": code, "message": message or ""}
    norm = str(code).lower()
    if norm in ("error", "status_code_error", "err", "2"):
        return {"code": 2, "message": message or "error"}
    if norm in ("ok", "status_code_ok", "success", "1"):
        return {"code": 1, "message": message or ""}
    return {"code": 0, "message": message or ""}


def ns_from_any(value: Any) -> Optional[int]:
    """Best-effort parse of a time value into unix-nanoseconds.

    Accepts int/str nanoseconds, ISO-8601 strings, and float seconds.
    Returns None if nothing works so adapters can skip the field.
    """
    if value is None:
        return None
    # Raw int or numeric string — treat as ns (Tempo/Jaeger) or ms (some
    # backends) based on magnitude.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _scale_to_ns(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric string?
        try:
            return _scale_to_ns(float(s))
        except ValueError:
            pass
        # ISO-8601.
        try:
            from datetime import datetime

            s_iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s_iso)
            return int(dt.timestamp() * 1_000_000_000)
        except Exception:
            return None
    return None


def ns_from_magnitude(n: float) -> int:
    """Public spelling of :func:`_scale_to_ns` for adapters whose timestamps
    come as ms or ns depending on the version (SigNoz)."""
    return _scale_to_ns(n)


def _scale_to_ns(n: float) -> int:
    """Infer time unit from magnitude. Handy when backends differ."""
    # Anything larger than ~1e18 is already ns.
    # 1e15–1e18 → microseconds (Jaeger)
    # 1e12–1e15 → milliseconds
    # below that → seconds
    abs_n = abs(n)
    if abs_n >= 1e18:
        return int(n)
    if abs_n >= 1e15:
        return int(n * 1_000)
    if abs_n >= 1e12:
        return int(n * 1_000_000)
    return int(n * 1_000_000_000)
