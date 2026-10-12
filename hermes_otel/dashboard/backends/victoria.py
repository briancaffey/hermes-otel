"""VictoriaMetrics-family adapter — VictoriaTraces through its Jaeger-compatible
API, VictoriaMetrics through the Prometheus API, VictoriaLogs through LogsQL
(#366).

Three stores, three read paths, one entry:

* Traces: VictoriaTraces serves the Jaeger v1 JSON API under ``/select/jaeger``
  (``/api/services``, ``/api/traces?service=…``, ``/api/traces/{id}``) with the
  v1 grammar (``start`` / ``end`` in µs, ``tags`` as JSON, ``operation`` exact,
  ``minDuration`` with a unit), so this is the Jaeger adapter mounted on a
  prefix. Every tag comes back typed ``string`` (``"23571"``, ``"false"``), the
  OTLP status is an ``error`` tag (``"true"`` / ``"false"``) and the kind a
  ``span.kind`` tag; values are coerced back so token counts render as numbers
  and ``error="false"`` is not an error. ``limit`` is over-fetched and the page
  cut here, as for GreptimeDB.
* Metrics: VictoriaMetrics speaks the Prometheus API, including range-vector
  instant queries, so the shared Prometheus reader is used as-is. It expects
  the Prometheus spelling the bundled stack produces with
  ``-opentelemetry.usePrometheusNaming`` (``hermes_token_usage_total``).
* Logs: VictoriaLogs' ``/select/logsql/query`` answers NDJSON rows with
  ``_msg``, ``_time`` (RFC 3339, ns), ``trace_id``, ``span_id``,
  ``severity_number``, ``severity_text``, ``scope.name`` and every log and
  resource attribute as a flat field (dotted keys kept). There is no Loki
  query API, so the Loki helper does not apply.

The entry's ``metrics_endpoint`` / ``logs_endpoint`` (the stores the plugin
writes to) name the read side too; ``prometheus_url`` / ``logs_url`` override.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from . import register
from ._prometheus import metric_names as _prom_metric_names
from ._prometheus import metrics_query as _prom_metrics_query
from .base import (
    BackendError,
    ConfigError,
    LogFilter,
    _execute,
    basic_auth,
    bearer,
    finish_log_row,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)
from .jaeger import JaegerAdapter
from .paging import LOG_PAGE_SLACK, log_end_ns, strictly_older
from .severity import otel_severity_for
from .zipkin import coerce_tag

_DEFAULT_TRACES_PORT = 10428
# ``/api/traces?limit=N``: whether VictoriaTraces keeps the newest N is not
# documented; fetch wide and cut the page here, as for GreptimeDB.
_DEFAULT_SEARCH_FETCH = 500

# VictoriaLogs fields that are the row, not attributes.
_LOG_ROW_FIELDS = frozenset(
    {
        "_msg",
        "_time",
        "_stream",
        "_stream_id",
        "trace_id",
        "span_id",
        "severity_number",
        "severity_text",
        "scope.name",
        "hermes.session_id",
        "event.name",
    }
)


def _origin(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    parsed = _urlparse.urlparse(url.strip())
    if not parsed.scheme or not parsed.hostname:
        return None
    host = rewrite_host_for_docker(parsed.hostname)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def rfc3339_to_ns(text: Any) -> int:
    """``2026-10-11T20:27:50.202258944Z`` → unix ns (fraction kept to 9 digits)."""
    s = str(text or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$", s)
    if not m:
        return 0
    base, frac, tz = m.groups()
    dt = _dt.datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz))
    secs = int(dt.timestamp())
    nanos = int((frac or "0")[:9].ljust(9, "0"))
    return secs * 1_000_000_000 + nanos


def ns_to_rfc3339(ns: int) -> str:
    secs, rem = divmod(int(ns), 1_000_000_000)
    base = _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{rem:09d}Z"


def logsql_str(value: Any) -> str:
    """A LogsQL quoted string (double quotes, backslash escapes)."""
    return json.dumps(str(value))


@register
class VictoriaAdapter(JaegerAdapter):
    handles = frozenset({"victoria", "victoriatraces"})
    query_lang_label = "Jaeger tags (key=value)"
    raw_placeholder = "tool.name=terminal error=true"
    default_query_port = _DEFAULT_TRACES_PORT
    default_query_path = "/select/jaeger"
    default_search_fetch = _DEFAULT_SEARCH_FETCH

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        self.query_api = "v1"  # the Jaeger-compatible API has no v3
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")
        self.prometheus_url = _origin(cfg.get("prometheus_url")) or _origin(
            cfg.get("metrics_endpoint")
        )
        self.logs_url = _origin(cfg.get("logs_url")) or _origin(cfg.get("logs_endpoint"))
        self.supports_metrics = self.prometheus_url is not None
        self.supports_logs = self.logs_url is not None
        self.metrics_match = cfg.get("metrics_match") or None

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_api"] = "jaeger-compatible"
        if self.prometheus_url:
            base["prometheus_url"] = self.prometheus_url
        if self.logs_url:
            base["logs_url"] = self.logs_url
        return base

    def _headers(self) -> Dict[str, str]:
        if self.user and self.password:
            return basic_auth(self.user, self.password)
        return bearer(self.api_key)

    def trace_url(self, trace_id: str) -> Optional[str]:
        # vmui's Trace Explorer has a TraceID mode; the route is not documented
        # as a deep link, so none is offered unless ``ui_url`` is set.
        return None

    # ── traces: typed-string tags ─────────────────────────────────────

    def _span_tags_as_dict(self, span: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for tag in span.get("tags") or []:
            if isinstance(tag, dict) and tag.get("key"):
                out[tag["key"]] = coerce_tag(str(tag["key"]), tag.get("value"))
        return out

    # ── metrics via the Prometheus reader ─────────────────────────────

    def _prometheus(self) -> str:
        if not self.prometheus_url:
            raise ConfigError("This backend has no metrics_endpoint / prometheus_url configured")
        return self.prometheus_url

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return _prom_metric_names(
            self._prometheus(), start_s, end_s, self.metrics_match, headers=self._headers()
        )

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        return _prom_metrics_query(
            self._prometheus(),
            name,
            start_s,
            end_s,
            bucket_s,
            group_by=group_by,
            agg=agg,
            headers=self._headers(),
        )

    # ── logs via LogsQL ───────────────────────────────────────────────

    def _logs(self) -> str:
        if not self.logs_url:
            raise ConfigError("This backend has no logs_endpoint / logs_url configured")
        return self.logs_url

    def _logsql(
        self, query: str, limit: Optional[int] = None, timeout: float = 30.0
    ) -> List[Dict[str, Any]]:
        """``POST /select/logsql/query`` → the NDJSON rows as dicts."""
        form = {"query": query}
        if limit is not None:
            form["limit"] = str(int(limit))
        body = _urlparse.urlencode(form).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded", **self._headers()}
        req = _urlrequest.Request(
            f"{self._logs()}/select/logsql/query", data=body, headers=headers, method="POST"
        )
        # A LogsQL 404 is a bad query, never a missing trace; the body is NDJSON.
        text = _execute(
            req, timeout, raw=True, label="VictoriaLogs", not_found_is_backend=True
        ).decode("utf-8", errors="replace")
        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                raise BackendError(502, f"VictoriaLogs returned a non-JSON line: {line[:120]}")
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    @staticmethod
    def _time_filter(start_ns: int, end_ns: int) -> str:
        return f"_time:[{ns_to_rfc3339(start_ns)}, {ns_to_rfc3339(end_ns)})"

    def _log_filters(self, f: LogFilter, start_s: int, end_s: int) -> List[str]:
        parts = [self._time_filter(int(start_s) * 1_000_000_000, log_end_ns(end_s, f))]
        if f.trace_id:
            parts.append(f"trace_id:{logsql_str(f.trace_id)}")
        if f.span_id:
            parts.append(f"span_id:{logsql_str(f.span_id)}")
        if f.session:
            parts.append(f"hermes.session_id:{logsql_str(f.session)}")
        if f.logger:
            parts.append(f"scope.name:{logsql_str(f.logger)}")
        if f.min_level:
            parts.append(f"severity_number:>={otel_severity_for(f.min_level)}")
        if f.event_name:
            parts.append(f"event.name:{logsql_str(f.event_name)}")
        elif f.events_only:
            parts.append("event.name:*")
        if f.text:
            parts.append(f"_msg:~{logsql_str(re.escape(f.text))}")
        return parts

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        n = int(limit) + LOG_PAGE_SLACK
        query = (
            " ".join(self._log_filters(f, start_s, end_s)) + f" | sort by (_time desc) | limit {n}"
        )
        records: List[Dict[str, Any]] = []
        for row in self._logsql(query, limit=n):
            attrs = {
                k: coerce_tag(k, v)
                for k, v in row.items()
                if k not in _LOG_ROW_FIELDS and not k.startswith("_")
            }
            try:
                severity = int(row.get("severity_number") or 0) or None
            except (TypeError, ValueError):
                severity = None
            record = {
                "level": str(row.get("severity_text") or "INFO").upper(),
                "severity_number": severity,
                "logger": row.get("scope.name") or "",
                "body": row.get("_msg") or "",
                "time_unix_nano": rfc3339_to_ns(row.get("_time")),
                "trace_id": row.get("trace_id") or None,
                "span_id": row.get("span_id") or None,
                "session_id": row.get("hermes.session_id") or None,
                "event_name": row.get("event.name") or None,
            }
            records.append(finish_log_row(record, attrs))
        return strictly_older(records, f)

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        query = (
            self._time_filter(int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000)
            + " | stats by (scope.name) count() n | sort by (n desc) | limit 200"
        )
        out = []
        for row in self._logsql(query):
            logger = row.get("scope.name")
            if not logger:
                continue
            try:
                out.append({"logger": str(logger), "count": int(row.get("n") or 0)})
            except (TypeError, ValueError):
                continue
        return out
