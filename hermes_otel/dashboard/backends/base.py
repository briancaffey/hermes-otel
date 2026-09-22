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
        }

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        raise NotImplementedError

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
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
    # ── time_unix_nano, trace_id, session_id) ────────────────────────────
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
) -> List[Tuple[int, float, str]]:
    """Turn cumulative counter samples into per-sample increases.

    OTLP counters arrive cumulative (the exporter's temporality); a bucketed
    "tokens per 15 s" chart needs the increase between consecutive samples of
    the same series. A drop (process restart) counts the new value in full.
    """
    last: Dict[str, float] = {}
    out: List[Tuple[int, float, str]] = []
    for ts, value, label in sorted(samples, key=lambda p: (p[2], p[0])):
        prev = last.get(label)
        inc = value if prev is None or value < prev else value - prev
        if prev is not None:
            out.append((ts, inc, label))
        last[label] = value
    return out


# ── HTTP helpers (stdlib only to avoid extra deps in the dashboard venv) ──


def _execute(req: _urlrequest.Request, timeout: float) -> Any:
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except _urlerror.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=502, detail=f"Backend returned {e.code}: {detail[:500]}")
    except _urlerror.URLError as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e.reason}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Backend query failed: {e}")
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Backend returned non-JSON: {e}")


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
