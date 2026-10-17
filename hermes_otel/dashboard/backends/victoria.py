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
  instant queries, so the ``PrometheusMetrics`` mixin is used as-is. It expects
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

import json
import re
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse
from urllib import request as _urlrequest

from hermes_otel.severity import otel_severity

from . import register
from ._prometheus import PrometheusMetrics
from .base import (
    BackendError,
    ConfigError,
    LogColumns,
    LogFilter,
    _execute,
    basic_auth,
    bearer,
    log_row,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)
from .jaeger import JaegerAdapter
from .paging import LOG_PAGE_SLACK, log_end_ns, strictly_older
from .shapes import coerce_tag
from .timeconv import ns_to_rfc3339, rfc3339_to_ns

_DEFAULT_TRACES_PORT = 10428
# ``/api/traces?limit=N``: whether VictoriaTraces keeps the newest N is not
# documented; fetch wide and cut the page here, as for GreptimeDB.
_DEFAULT_SEARCH_FETCH = 500

# VictoriaLogs' columns; its own ``_``-prefixed fields (``_msg``, ``_time``,
# ``_stream``) are filtered out before the row is built.
_LOG_COLUMNS = LogColumns(
    level="severity_text",
    logger="scope.name",
    session_id="hermes.session_id",
    event_name="event.name",
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


def logsql_str(value: Any) -> str:
    """A LogsQL quoted string (double quotes, backslash escapes)."""
    return json.dumps(str(value))


@register
class VictoriaAdapter(PrometheusMetrics, JaegerAdapter):
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
        self._use_prometheus(
            _origin(cfg.get("prometheus_url")) or _origin(cfg.get("metrics_endpoint")), cfg
        )
        self.logs_url = _origin(cfg.get("logs_url")) or _origin(cfg.get("logs_endpoint"))
        self.supports_logs = self.logs_url is not None

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
            parts.append(f"severity_number:>={otel_severity(f.min_level)}")
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
            # Every field is a string; give the attributes their types back.
            raw = {k: coerce_tag(k, v) for k, v in row.items() if not k.startswith("_")}
            raw["body"] = row.get("_msg")
            records.append(log_row(raw, _LOG_COLUMNS, time_ns=rfc3339_to_ns(row.get("_time"))))
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
