"""OpenObserve adapter — SQL over ``/api/{org}/_search?type=traces``.

OpenObserve stores traces as rows in a stream (default ``default``).
Attributes are flattened into columns with dots replaced by
underscores (``llm.model_name`` → ``llm_model_name``); the adapter
translates them back so the UI sees the same keys it does on Tempo.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_post_json,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)

_DEFAULT_OO_PORT = 5080

# Columns the card renderer cares about. Expressed in Otel dot form;
# ``_oo_col`` translates to OpenObserve's underscore form on the wire.
_CARD_ATTR_KEYS = (
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
)


def _oo_col(dotted: str) -> str:
    return dotted.replace(".", "_")


def _dotted(underscored: str) -> str:
    # Best-effort reverse — safe because all OTel-standard attrs use
    # dots that never appear between consecutive digits, and ``_`` is
    # the escape. Exact fidelity isn't strictly required since the
    # frontend treats keys as opaque strings, but dotted keys are
    # expected by the card's ``_CARD_ATTR_KEYS`` match.
    return underscored.replace("_", ".")


def _sql_escape(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace("'", "''")


@register
class OpenObserveAdapter(BackendAdapter):
    handles = frozenset({"openobserve", "openobserver"})
    query_lang_label = "SQL WHERE"
    raw_placeholder = "llm_model_name = 'gpt-4'"

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_OO_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        # Org is the first path segment in the endpoint, e.g.
        # ``/api/default/v1/traces`` → ``default``.
        self.org = cfg.get("org") or _extract_org(endpoint) or "default"
        self.stream = cfg.get("stream_name") or cfg.get("stream") or "default"
        self.user = resolve_env_or_literal(cfg, "user", "user_env")
        self.password = resolve_env_or_literal(cfg, "password", "password_env")

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["org"] = self.org
        base["stream"] = self.stream
        base["auth_required"] = not (self.user and self.password)
        return base

    def _headers(self) -> Dict[str, str]:
        if not (self.user and self.password):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=502,
                detail=(
                    "OpenObserve requires basic auth credentials. Set "
                    "user + password (or *_env) on the openobserve "
                    "backend entry in config.yaml."
                ),
            )
        token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    # ── Query construction ───────────────────────────────────────────

    def _build_where(self, f: StructuredFilter) -> str:
        clauses: List[str] = []
        if f.service:
            clauses.append(f"service_name = '{_sql_escape(f.service)}'")
        if f.name_regex:
            # OpenObserve SQL supports LIKE; wrap user text in %%.
            pat = f.name_regex.replace("%", "\\%")
            clauses.append(f"operation_name LIKE '%{_sql_escape(pat)}%'")
        if f.status == "error":
            clauses.append("status_code = 2")
        elif f.status == "ok":
            clauses.append("status_code = 1")
        if f.min_duration_ms:
            # duration stored in microseconds in OpenObserve traces stream.
            clauses.append(f"duration >= {int(f.min_duration_ms) * 1000}")
        for k, v in f.attr_equals.items():
            clauses.append(f"{_oo_col(k)} = '{_sql_escape(str(v))}'")
        if f.free_text:
            clauses.append(f"llm_input LIKE '%{_sql_escape(f.free_text)}%'")

        raw = (f.raw or "").strip()
        if raw:
            clauses.append(f"({raw})")

        return " AND ".join(clauses) if clauses else "1=1"

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        where = self._build_where(f)
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"

        # When roots_only is False we want ALL matched spans (still
        # deduped to one per trace client-side); when it's True we try
        # version-specific root-span filters first and fall back to
        # client-side dedupe.
        if f.roots_only:
            attempts = [
                f"SELECT * FROM {self.stream} WHERE {where} AND "
                f"(reference_parent_span_id IS NULL OR reference_parent_span_id = '') "
                f"ORDER BY _timestamp DESC LIMIT {int(limit)}",
                f"SELECT * FROM {self.stream} WHERE {where} AND "
                f"(reference IS NULL OR reference = '') "
                f"ORDER BY _timestamp DESC LIMIT {int(limit)}",
                f"SELECT * FROM {self.stream} WHERE {where} "
                f"ORDER BY _timestamp DESC LIMIT {int(limit) * 4}",
            ]
        else:
            attempts = [
                f"SELECT * FROM {self.stream} WHERE {where} "
                f"ORDER BY _timestamp DESC LIMIT {int(limit) * 4}",
            ]

        rows: List[Dict[str, Any]] = []
        last_err: Optional[Exception] = None
        for sql in attempts:
            body = {
                "query": {
                    "sql": sql,
                    "start_time": int(start_s) * 1_000_000,  # µs
                    "end_time": int(end_s) * 1_000_000,
                    "size": int(limit) * 4,
                }
            }
            try:
                data = http_post_json(url, body, headers=self._headers(), timeout=15.0)
            except Exception as e:  # HTTPException or network
                last_err = e
                continue
            hits = data.get("hits") if isinstance(data, dict) else None
            if isinstance(hits, list) and hits:
                rows = hits
                break

        if not rows and last_err:
            raise last_err

        traces: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            trace_id = row.get("trace_id") or row.get("traceId")
            if not trace_id or trace_id in traces:
                continue
            start_ns = int(row.get("start_time") or 0)
            duration_us = int(row.get("duration") or 0)
            duration_ms = duration_us // 1000 if duration_us else 0

            attrs_map = _row_to_card_attrs(row)
            traces[trace_id] = {
                "traceID": trace_id,
                "rootServiceName": row.get("service_name") or "",
                "rootTraceName": row.get("operation_name") or row.get("name") or "",
                "startTimeUnixNano": str(start_ns) if start_ns else "0",
                "durationMs": duration_ms,
                "spanSets": [
                    {
                        "spans": [
                            {
                                "spanID": row.get("span_id"),
                                "name": row.get("operation_name") or row.get("name") or "",
                                "attributes": otlp_attrs_from_dict(attrs_map),
                            }
                        ],
                        "matched": 1,
                    }
                ],
            }
        return {"traces": list(traces.values())}

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        where = f"trace_id = '{_sql_escape(trace_id)}'"
        sql = f"SELECT * FROM {self.stream} WHERE {where} ORDER BY start_time ASC LIMIT 500"
        # 24h window either side of the trace — trace_id is unique so
        # we don't need a tight window but OpenObserve requires one.
        import time

        now = int(time.time())
        body = {
            "query": {
                "sql": sql,
                "start_time": (now - 7 * 86400) * 1_000_000,
                "end_time": (now + 3600) * 1_000_000,
                "size": 500,
            }
        }
        url = f"{self.query_url}/api/{self.org}/_search?type=traces"
        data = http_post_json(url, body, headers=self._headers(), timeout=20.0)
        hits = data.get("hits") if isinstance(data, dict) else None
        rows = hits if isinstance(hits, list) else []
        return _rows_to_otlp(rows)


def _extract_org(endpoint: str) -> Optional[str]:
    try:
        parsed = _urlparse.urlparse(endpoint)
        # Path looks like ``/api/default/v1/traces``.
        parts = [p for p in (parsed.path or "").split("/") if p]
        if len(parts) >= 2 and parts[0] == "api":
            return parts[1]
    except Exception:
        pass
    return None


_CARD_ATTR_OO_COLS = tuple(_oo_col(k) for k in _CARD_ATTR_KEYS)


def _row_to_card_attrs(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": row.get("operation_name") or row.get("name") or ""}
    status_code = row.get("status_code")
    if status_code is not None:
        out["status"] = "error" if status_code == 2 else "ok"
    for dotted, col in zip(_CARD_ATTR_KEYS, _CARD_ATTR_OO_COLS):
        v = row.get(col)
        if v is not None and v != "":
            out[dotted] = _maybe_num(v)
    return out


def _maybe_num(v: Any) -> Any:
    """OpenObserve sometimes returns numeric columns as strings
    (``"13283"``). Pull those back to ints for the card renderer's
    token-count formatting."""
    if isinstance(v, str) and v.isdigit():
        try:
            return int(v)
        except Exception:
            return v
    return v


_SKIP_OTLP_COLS = frozenset(
    {
        "_timestamp",
        "trace_id",
        "span_id",
        "parent_span_id",
        "reference",
        "reference_parent_span_id",
        "reference_parent_trace_id",
        "reference_ref_type",
        "start_time",
        "end_time",
        "duration",
        "operation_name",
        "name",
        "service_name",
        "span_kind",
        "status_code",
        "status_message",
        "flags",
        "events",
        "links",
        "input_mime_type",
        "output_mime_type",
    }
)


def _rows_to_otlp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_service: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        start_ns = int(row.get("start_time") or 0)
        end_ns = int(row.get("end_time") or 0)
        attrs: Dict[str, Any] = {}
        for k, v in row.items():
            if k in _SKIP_OTLP_COLS or v is None or v == "":
                continue
            attrs[_dotted(k)] = _maybe_num(v)

        parent = (
            row.get("reference_parent_span_id")
            or _extract_parent_from_reference(row.get("reference"))
            or row.get("parent_span_id")
        )
        otlp_span = {
            "traceId": row.get("trace_id"),
            "spanId": row.get("span_id"),
            "parentSpanId": parent or None,
            "name": row.get("operation_name") or row.get("name") or "",
            "kind": int(row.get("span_kind") or 1),
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(attrs),
            "status": otlp_status(row.get("status_code"), row.get("status_message") or ""),
        }
        service = row.get("service_name") or ""
        by_service.setdefault(service, []).append(otlp_span)

    batches = []
    for service, spans in by_service.items():
        resource_attrs = otlp_attrs_from_dict({"service.name": service} if service else {})
        batches.append(
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [{"spans": spans}],
            }
        )
    return {"batches": batches}


def _extract_parent_from_reference(ref: Any) -> Optional[str]:
    """OpenObserve encodes parent-links as a JSON string in ``reference``.

    Example: ``[{"refType":"CHILD_OF","traceId":"…","spanId":"…"}]``.
    We return the first ``spanId`` we find.
    """
    if not ref:
        return None
    if isinstance(ref, list):
        for r in ref:
            if isinstance(r, dict) and r.get("spanId"):
                return r["spanId"]
    if isinstance(ref, str):
        try:
            import json

            parsed = json.loads(ref)
            if isinstance(parsed, list):
                for r in parsed:
                    if isinstance(r, dict) and r.get("spanId"):
                        return r["spanId"]
        except Exception:
            pass
    return None
