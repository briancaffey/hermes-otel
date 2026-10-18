"""Loki HTTP API helpers for the logs half of Grafana-stack adapters (#194).

Loki 3 ingests OTLP logs natively (``/otlp/v1/logs``); resource and record
attributes become stream labels or structured metadata depending on its
``otlp_config``. LogQL label-filter stages (``| key="value"``) match both,
so every filter here is written as a pipeline stage on a broad stream
selector rather than baked into the selector.

:class:`LokiLogs` is the logs half of an adapter whose store speaks this API
(Tempo / LGTM, Gigapipe): the adapter sets the read URL once, the mixin
answers ``logs_search`` / ``loggers`` with the adapter's own ``_headers()``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from hermes_otel.severity import otel_severity

from ._attrs import dotted as _dotted
from .base import ConfigError, LogColumns, LogFilter, http_get_json, log_row
from .paging import log_end_ns, strictly_older

DEFAULT_SELECTOR = '{service_name=~".+"}'

# The otel-lgtm collector writes OTLP log attributes as Loki structured
# metadata with dots turned into underscores (``hermes_session_id``,
# ``hermes_log_attribution``); ``query_range`` returns them merged with the
# stream labels. Everything that is not a row column is exposed under
# ``attributes``, mapped back to its dotted name where the plugin documents
# one (``hermes.log.attribution``; see :func:`_dotted`) so the dashboard
# groups and reads them like the live store's rows (#268). Loki's own
# bookkeeping labels (``flags``, ``observed_timestamp``) are dropped.
LOG_COLUMNS = LogColumns(
    level=("severity_text", "detected_level"),
    logger="scope_name",
    session_id="hermes_session_id",
    drop=("service_name", "flags", "observed_timestamp"),
    attr_key=_dotted,
)

# Loki refuses a range longer than its ``max_query_length`` (30 days and one
# hour by default); every window here is clamped to that so a one-year
# lookback never turns into a 400 (#296).
MAX_RANGE_S = 30 * 86400


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def logql_for(f: LogFilter, selector: str = DEFAULT_SELECTOR) -> str:
    """Selector plus one pipeline stage per honoured filter field."""
    stages: List[str] = []
    if f.trace_id:
        stages.append(f"| trace_id={_quote(f.trace_id)}")
    if f.span_id:
        stages.append(f"| span_id={_quote(f.span_id)}")
    if f.event_name:
        stages.append(f"| event_name={_quote(f.event_name)}")
    elif f.events_only:
        stages.append('| event_name!=""')
    if f.session:
        stages.append(f"| hermes_session_id={_quote(f.session)}")
    if f.logger:
        stages.append(f"| scope_name={_quote(f.logger)}")
    if f.min_level:
        stages.append(f"| severity_number >= {otel_severity(f.min_level)}")
    if f.text:
        stages.append(f"|= {_quote(f.text)}")
    return " ".join([selector, *stages])


def _record(labels: Dict[str, Any], ts_ns: Any, line: str) -> Dict[str, Any]:
    """Stream labels plus structured metadata → the live store's row shape
    (see :data:`LOG_COLUMNS`); the line is the body."""
    return log_row({**labels, "body": line}, LOG_COLUMNS, time_ns=int(ts_ns or 0))


def logs_search(
    base: str,
    f: LogFilter,
    start_s: int,
    end_s: int,
    limit: int,
    selector: str = DEFAULT_SELECTOR,
    headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    end_ns = log_end_ns(end_s, f)
    start_ns = max(int(start_s) * 1_000_000_000, end_ns - MAX_RANGE_S * 1_000_000_000)
    params = {
        "query": logql_for(f, selector),
        "start": start_ns,
        # Loki's end is exclusive and nanosecond-precise, so the cursor maps
        # straight onto it.
        "end": end_ns,
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
    """Record counts per ``scope_name`` (the Python logger) over the window
    (at most the last ``MAX_RANGE_S`` of it)."""
    window = max(1, min(int(end_s) - int(start_s), MAX_RANGE_S))
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


class LokiLogs:
    """The logs half of an adapter whose store speaks the Loki API: the
    adapter calls :meth:`_use_loki` once it knows the read URL (``None`` when
    the entry has no Loki), the reads above then run with the entry's
    ``loki_selector`` and the adapter's own ``_headers()``, and ``/status``
    names the URL."""

    loki_url: Optional[str] = None
    loki_selector: str = DEFAULT_SELECTOR

    def _use_loki(self, url: Optional[str], cfg: Dict[str, Any]) -> None:
        self.loki_url = url
        self.loki_selector = cfg.get("loki_selector") or DEFAULT_SELECTOR
        self.supports_logs = url is not None

    def _loki(self) -> str:
        if not self.loki_url:
            raise ConfigError("This backend has no loki_url configured")
        return self.loki_url

    def status(self) -> Dict[str, Any]:
        out = super().status()
        if self.loki_url:
            out["loki_url"] = self.loki_url
        return out

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        return logs_search(
            self._loki(), f, start_s, end_s, limit, self.loki_selector, headers=self._headers()
        )

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return loggers(self._loki(), start_s, end_s, self.loki_selector, headers=self._headers())
