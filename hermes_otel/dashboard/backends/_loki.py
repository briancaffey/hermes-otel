"""Loki HTTP API helpers for the logs half of Grafana-stack adapters (#194).

Loki 3 ingests OTLP logs natively (``/otlp/v1/logs``); resource and record
attributes become stream labels or structured metadata depending on its
``otlp_config``. LogQL label-filter stages (``| key="value"``) match both,
so every filter here is written as a pipeline stage on a broad stream
selector rather than baked into the selector.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from .base import LogFilter, http_get_json, log_end_ns, strictly_older

DEFAULT_SELECTOR = '{service_name=~".+"}'

# Python logging levels → the smallest OTel severity number of that band
# (the exporter maps DEBUG→5, INFO→9, WARNING→13, ERROR→17, CRITICAL→21).
_OTEL_SEVERITY_FLOOR = {10: 5, 20: 9, 30: 13, 40: 17, 50: 21}


def otel_severity_for(min_level: int) -> int:
    band = max((lvl for lvl in _OTEL_SEVERITY_FLOOR if lvl <= int(min_level)), default=0)
    return _OTEL_SEVERITY_FLOOR.get(band, 1)


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def logql_for(f: LogFilter, selector: str = DEFAULT_SELECTOR) -> str:
    """Selector plus one pipeline stage per honoured filter field."""
    stages: List[str] = []
    if f.trace_id:
        stages.append(f"| trace_id={_quote(f.trace_id)}")
    if f.session:
        stages.append(f"| hermes_session_id={_quote(f.session)}")
    if f.logger:
        stages.append(f"| scope_name={_quote(f.logger)}")
    if f.min_level:
        stages.append(f"| severity_number >= {otel_severity_for(f.min_level)}")
    if f.text:
        stages.append(f"|= {_quote(f.text)}")
    return " ".join([selector, *stages])


def _record(labels: Dict[str, Any], ts_ns: Any, line: str) -> Dict[str, Any]:
    trace_id = labels.get("trace_id") or None
    return {
        "level": str(labels.get("severity_text") or labels.get("detected_level") or "INFO").upper(),
        "logger": labels.get("scope_name") or "",
        "body": line or "",
        "time_unix_nano": int(ts_ns or 0),
        "trace_id": trace_id,
        "session_id": labels.get("hermes_session_id") or None,
    }


def logs_search(
    base: str,
    f: LogFilter,
    start_s: int,
    end_s: int,
    limit: int,
    selector: str = DEFAULT_SELECTOR,
    headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    params = {
        "query": logql_for(f, selector),
        "start": int(start_s) * 1_000_000_000,
        # Loki's end is exclusive and nanosecond-precise, so the cursor maps
        # straight onto it.
        "end": log_end_ns(end_s, f),
        "limit": int(limit),
        "direction": "backward",
    }
    url = f"{base}/loki/api/v1/query_range?{_urlparse.urlencode(params)}"
    data = http_get_json(url, headers=headers, timeout=30.0)
    out: List[Dict[str, Any]] = []
    for stream in ((data.get("data") or {}).get("result")) if isinstance(data, dict) else []:
        labels = stream.get("stream") or {}
        for ts_ns, line in stream.get("values") or []:
            out.append(_record(labels, ts_ns, line))
    out.sort(key=lambda r: r["time_unix_nano"], reverse=True)
    return strictly_older(out, f)[: int(limit)]


def loggers(
    base: str,
    start_s: int,
    end_s: int,
    selector: str = DEFAULT_SELECTOR,
    headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Record counts per ``scope_name`` (the Python logger) over the window."""
    window = max(1, int(end_s) - int(start_s))
    params = {
        "query": f"sum by (scope_name) (count_over_time({selector} [{window}s]))",
        "time": int(end_s),
    }
    url = f"{base}/loki/api/v1/query?{_urlparse.urlencode(params)}"
    data = http_get_json(url, headers=headers, timeout=30.0)
    out = []
    for series in ((data.get("data") or {}).get("result")) if isinstance(data, dict) else []:
        logger = (series.get("metric") or {}).get("scope_name")
        value = series.get("value") or [None, "0"]
        if logger:
            try:
                out.append({"logger": logger, "count": int(float(value[1]))})
            except (TypeError, ValueError):
                continue
    return sorted(out, key=lambda r: -r["count"])
