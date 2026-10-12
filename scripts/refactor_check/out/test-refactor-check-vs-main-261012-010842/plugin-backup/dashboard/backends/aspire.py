""".NET Aspire Dashboard adapter — the standalone dashboard's read API (#369).

Since 13.6 the dashboard serves ``GET /api/telemetry/{resources,traces,spans,
logs}`` and ``/api/telemetry/traces/{traceId}`` on its UI port, answering
**OTLP JSON** (``data.resourceSpans`` / ``data.resourceLogs``) with
``totalCount`` and ``returnedCount``. In anonymous mode (the bundled stack) no
key is needed; with ``Dashboard:Api:AuthMode=ApiKey`` the key goes in an
``x-api-key`` header (``api_key`` / ``api_key_env``).

The documented filters are ``resource`` (a resource name, repeatable) and
``follow``; on 13.6.1 ``limit`` / ``offset`` and, for logs, ``traceId`` and
``search`` work as well, and nothing else does (no time window, no
attribute, severity or scope filters, verified 2026-10-11). A resource is one
``service.name`` + ``service.instance.id``, i.e. one Hermes process, and the
bare service name is ambiguous once a second process has reported, so the
adapter lists ``/api/telemetry/resources`` and names every instance of the
service. The adapter
therefore fetches a bounded page (``search_fetch`` traces, ``logs_fetch``
records) and applies the window, the structured filters, roots-only and the
cursor itself. Metrics have no read route (the UI alone shows them), so the
adapter serves traces and logs only.

Severities arrive in .NET spelling (``Information``, ``Warning``,
``Critical``); the OTel level comes from ``severityNumber``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import default_service_name, register
from .base import (
    LOG_PAGE_SLACK,
    BackendAdapter,
    BackendError,
    LogFilter,
    StructuredFilter,
    finish_log_row,
    http_get_json,
    log_end_ns,
    otlp_attrs_from_dict,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older,
    strictly_older_traces,
    trace_page,
)

_DEFAULT_UI_PORT = 18888
# The API has no time window, so a search reads this many traces (newest or
# not, the order is not documented) and cuts the window and the page here.
_DEFAULT_SEARCH_FETCH = 500
_DEFAULT_LOGS_FETCH = 5000

_CARD_KEYS = frozenset(
    {
        "llm.model_name",
        "llm.provider",
        "llm.api_mode",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "llm.response.finish_reason",
        "llm.response.tool_calls",
        "tool.name",
        "input.value",
        "output.value",
        "llm.output.content",
        "hermes.session_id",
        "hermes.turn.number",
    }
)

# OTel severity numbers → the level spelling the dashboard shows.
_LEVEL_BANDS = ((21, "FATAL"), (17, "ERROR"), (13, "WARN"), (9, "INFO"), (5, "DEBUG"), (1, "TRACE"))
_DOTNET_LEVELS = {
    "trace": "TRACE",
    "debug": "DEBUG",
    "information": "INFO",
    "info": "INFO",
    "warning": "WARN",
    "warn": "WARN",
    "error": "ERROR",
    "critical": "FATAL",
    "fatal": "FATAL",
}


def otlp_value(value: Any) -> Any:
    """Unwrap an OTLP JSON ``AnyValue``."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "arrayValue" in value:
        return [otlp_value(v) for v in (value["arrayValue"] or {}).get("values") or []]
    if "kvlistValue" in value:
        return {
            kv.get("key"): otlp_value(kv.get("value"))
            for kv in (value["kvlistValue"] or {}).get("values") or []
        }
    return value


def attrs_dict(items: Any) -> Dict[str, Any]:
    return {
        a["key"]: otlp_value(a.get("value"))
        for a in (items or [])
        if isinstance(a, dict) and a.get("key")
    }


def level_for(severity_number: Any, severity_text: Any) -> str:
    try:
        n = int(severity_number or 0)
    except (TypeError, ValueError):
        n = 0
    for floor, name in _LEVEL_BANDS:
        if n >= floor:
            return name
    return _DOTNET_LEVELS.get(str(severity_text or "").strip().lower(), "INFO")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


@register
class AspireAdapter(BackendAdapter):
    handles = frozenset({"aspire"})
    query_lang_label = "no native query (filters are applied to the fetched rows)"
    raw_placeholder = ""
    supports_metrics = False
    supports_logs = True
    filter_support = {
        "service": "server",  # ``resource=`` names the service
        "name": "client",
        "model": "client",
        "session": "client",
        "tool": "client",
        "min_duration": "client",
        "status_error": "client",
        "status_ok": "client",
        "free_text": "client",
        "raw": "none",
        "roots_only": "client",
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = rewrite_host_for_docker(parsed.hostname or "localhost")
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_UI_PORT
        self.query_url = f"{scheme}://{host}:{port}"
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        self.default_service = default_service_name(cfg)
        self.ui_url = (cfg.get("ui_url") or "").strip().rstrip("/") or self.query_url
        try:
            self.search_fetch = max(1, int(cfg.get("search_fetch") or _DEFAULT_SEARCH_FETCH))
        except (TypeError, ValueError):
            self.search_fetch = _DEFAULT_SEARCH_FETCH
        try:
            self.logs_fetch = max(1, int(cfg.get("logs_fetch") or _DEFAULT_LOGS_FETCH))
        except (TypeError, ValueError):
            self.logs_fetch = _DEFAULT_LOGS_FETCH

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["default_service"] = self.default_service
        return base

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.api_key} if self.api_key else {}

    def _get(self, path: str, params: List[tuple], timeout: float = 20.0) -> Any:
        url = f"{self.query_url}{path}"
        if params:
            url += "?" + _urlparse.urlencode(params)
        try:
            return http_get_json(url, headers=self._headers(), timeout=timeout)
        except BackendError as exc:
            # ``resource=<unknown>`` is a 404: nothing for that service, not a failure.
            if exc.kind == "not_found" and any(k == "resource" for k, _ in params):
                return {}
            raise

    def _resource_params(self, service: str) -> Optional[List[tuple]]:
        """``resource=`` parameters for every Aspire resource of one service.

        Aspire keys a resource on ``service.name`` + ``service.instance.id``, so
        each Hermes process is its own resource (``hermes-agent-<uuid>``). The
        bare service name only works while a single instance exists; with
        several it is ambiguous and the API fails (verified on 13.6.1), so the
        instances are listed first and all of them are named, the parameter
        being repeatable. ``None`` means the service has no resource yet.
        """
        data = self._get("/api/telemetry/resources", [])
        names: List[str] = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("name") == service:
                label = item.get("displayName") or item.get("name")
                if label:
                    names.append(str(label))
        if not names:
            return None
        return [("resource", n) for n in sorted(set(names))]

    # ── traces ────────────────────────────────────────────────────────

    @staticmethod
    def _spans_of(data: Any) -> List[Dict[str, Any]]:
        """Flatten OTLP JSON into spans, each carrying its resource attributes."""
        out: List[Dict[str, Any]] = []
        payload = data.get("data") if isinstance(data, dict) else None
        for rs in (payload or {}).get("resourceSpans") or []:
            if not isinstance(rs, dict):
                continue
            res = attrs_dict((rs.get("resource") or {}).get("attributes"))
            for ss in rs.get("scopeSpans") or []:
                for sp in (ss or {}).get("spans") or []:
                    if isinstance(sp, dict) and sp.get("traceId"):
                        sp = dict(sp)
                        sp["_resource"] = res
                        out.append(sp)
        return out

    @staticmethod
    def _status_code(span: Dict[str, Any]) -> int:
        code = (span.get("status") or {}).get("code")
        if code in (2, "STATUS_CODE_ERROR", "Error"):
            return 2
        if code in (1, "STATUS_CODE_OK", "Ok"):
            return 1
        return 0

    def _matches(self, span: Dict[str, Any], attrs: Dict[str, Any], f: StructuredFilter) -> bool:
        name = str(span.get("name") or "")
        if f.name_prefix and not name.startswith(f.name_prefix):
            return False
        if f.name_regex and name != f.name_regex:
            return False
        for k, v in f.attr_equals.items():
            have = attrs.get(k)
            if have is None or str(have).lower() != str(v).lower():
                return False
        if f.min_duration_ms:
            dur_ns = _int(span.get("endTimeUnixNano")) - _int(span.get("startTimeUnixNano"))
            if dur_ns < int(f.min_duration_ms) * 1_000_000:
                return False
        code = self._status_code(span)
        if f.status == "error" and code != 2:
            return False
        if f.status == "ok" and code == 2:
            return False
        if f.free_text:
            needle = f.free_text.lower()
            hay = f"{attrs.get('input.value', '')}\n{attrs.get('output.value', '')}".lower()
            if needle not in hay:
                return False
        return True

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        resources = self._resource_params(f.service or self.default_service)
        if resources is None:
            return trace_page([], limit)
        params: List[tuple] = [*resources, ("limit", str(self.search_fetch))]
        spans = self._spans_of(self._get("/api/telemetry/traces", params))
        by_trace: Dict[str, List[Dict[str, Any]]] = {}
        for sp in spans:
            by_trace.setdefault(str(sp["traceId"]), []).append(sp)
        start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
        rows: List[Dict[str, Any]] = []
        for tid, tspans in by_trace.items():
            ids = {sp.get("spanId") for sp in tspans}
            tspans.sort(key=lambda sp: _int(sp.get("startTimeUnixNano")))
            root = next(
                (
                    sp
                    for sp in tspans
                    if not sp.get("parentSpanId") or sp.get("parentSpanId") not in ids
                ),
                tspans[0],
            )
            if f.roots_only:
                if root.get("parentSpanId"):
                    continue  # an orphan: its real root is outside the fetch
                card = root
                attrs = attrs_dict(card.get("attributes"))
                if not self._matches(card, attrs, f):
                    continue
            else:
                card = next(
                    (sp for sp in tspans if self._matches(sp, attrs_dict(sp.get("attributes")), f)),
                    None,
                )
                if card is None:
                    continue
                attrs = attrs_dict(card.get("attributes"))
            card_start = _int(card.get("startTimeUnixNano"))
            if not (start_ns <= card_start < end_ns):
                continue
            keep = {k: v for k, v in attrs.items() if k in _CARD_KEYS}
            keep["name"] = card.get("name") or ""
            if self._status_code(attrs and card) == 2:
                keep["status"] = "error"
            dur_ns = max(0, _int(card.get("endTimeUnixNano")) - card_start)
            rows.append(
                {
                    "traceID": tid,
                    "spanCount": len(tspans),
                    "rootServiceName": str((card.get("_resource") or {}).get("service.name") or ""),
                    "rootTraceName": card.get("name") or "",
                    "startTimeUnixNano": str(card_start) if card_start else "0",
                    "durationMs": dur_ns // 1_000_000,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": card.get("spanId"),
                                    "name": card.get("name") or "",
                                    "attributes": otlp_attrs_from_dict(keep),
                                }
                            ],
                            "matched": len(tspans),
                        }
                    ],
                }
            )
        return trace_page(strictly_older_traces(rows, f), limit)

    def trace_url(self, trace_id: str) -> Optional[str]:
        return f"{self.ui_url}/traces/detail/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        try:
            data = self._get(f"/api/telemetry/traces/{_urlparse.quote(trace_id)}", [])
        except BackendError as exc:
            if exc.kind == "not_found":
                raise BackendError(404, f"Trace {trace_id} not found in Aspire", "not_found")
            raise
        payload = data.get("data") if isinstance(data, dict) else None
        batches = [b for b in ((payload or {}).get("resourceSpans") or []) if isinstance(b, dict)]
        n = sum(
            len((ss or {}).get("spans") or []) for b in batches for ss in b.get("scopeSpans") or []
        )
        if n == 0:
            raise BackendError(404, f"Trace {trace_id} not found in Aspire", "not_found")
        return {"batches": batches, "span_count": n, "truncated": False}

    # ── logs ──────────────────────────────────────────────────────────

    def _records(self, f: LogFilter, service: Optional[str]) -> List[Dict[str, Any]]:
        params: List[tuple] = [("limit", str(self.logs_fetch))]
        if service:
            resources = self._resource_params(service)
            if resources is None:
                return []
            params.extend(resources)
        if f.trace_id:
            params.append(("traceId", f.trace_id))
        if f.text:
            params.append(("search", f.text))
        data = self._get("/api/telemetry/logs", params, timeout=30.0)
        payload = data.get("data") if isinstance(data, dict) else None
        out: List[Dict[str, Any]] = []
        for rl in (payload or {}).get("resourceLogs") or []:
            if not isinstance(rl, dict):
                continue
            for sl in rl.get("scopeLogs") or []:
                scope = ((sl or {}).get("scope") or {}).get("name") or ""
                for rec in (sl or {}).get("logRecords") or []:
                    if isinstance(rec, dict):
                        rec = dict(rec)
                        rec["_scope"] = scope
                        out.append(rec)
        return out

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        start_ns, end_ns = int(start_s) * 1_000_000_000, log_end_ns(end_s, f)
        floor = 0
        if f.min_level:
            from ._loki import otel_severity_for

            floor = otel_severity_for(f.min_level)
        records: List[Dict[str, Any]] = []
        for rec in self._records(f, self.default_service):
            ts = _int(rec.get("timeUnixNano"))
            if not (start_ns <= ts < end_ns):
                continue
            if f.span_id and (rec.get("spanId") or "") != f.span_id:
                continue
            if f.logger and rec.get("_scope") != f.logger:
                continue
            severity = _int(rec.get("severityNumber"))
            if floor and severity < floor:
                continue
            attrs = attrs_dict(rec.get("attributes"))
            attrs.pop("aspire.log_id", None)
            session = attrs.pop("hermes.session_id", None)
            event_name = attrs.pop("event.name", None)
            if f.session and str(session) != f.session:
                continue
            if f.event_name and event_name != f.event_name:
                continue
            if f.events_only and not event_name:
                continue
            record = {
                "level": level_for(rec.get("severityNumber"), rec.get("severityText")),
                "severity_number": severity or None,
                "logger": rec.get("_scope") or "",
                "body": otlp_value(rec.get("body")) if rec.get("body") is not None else "",
                "time_unix_nano": ts,
                "trace_id": rec.get("traceId") or None,
                "span_id": rec.get("spanId") or None,
                "session_id": session,
                "event_name": event_name,
            }
            records.append(finish_log_row(record, attrs))
        records.sort(key=lambda r: int(r.get("time_unix_nano") or 0), reverse=True)
        return strictly_older(records, f)[: int(limit) + LOG_PAGE_SLACK]

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        start_ns, end_ns = int(start_s) * 1_000_000_000, int(end_s) * 1_000_000_000
        counts: Dict[str, int] = {}
        for rec in self._records(LogFilter(), self.default_service):
            ts = _int(rec.get("timeUnixNano"))
            if start_ns <= ts < end_ns and rec.get("_scope"):
                counts[rec["_scope"]] = counts.get(rec["_scope"], 0) + 1
        return [{"logger": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
