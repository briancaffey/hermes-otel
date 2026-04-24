"""Phoenix (Arize) adapter — GraphQL at ``/graphql``.

Phoenix stores spans per-project. The adapter honours ``project_name``
from the backend cfg (or the top-level plugin config) and falls back
to the first project that has traces. Attributes come back as a
nested JSON string; we parse + flatten to dot-notation keys
(``llm.model_name``) so the frontend renderer can use them unchanged.

Native query language is Phoenix's ``filterCondition`` expression
syntax — same one the Phoenix UI uses in its filter bar.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_post_json,
    otlp_attr,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)

_DEFAULT_PHOENIX_PORT = 6006


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Phoenix's ``attributes`` JSON is nested by dot groupings
    (``{"llm": {"model_name": ...}}``). Re-emit as a flat dict
    (``{"llm.model_name": ...}``) to match Tempo/OTLP attribute keys.
    """
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(_flatten(v, key))
            else:
                out[key] = v
    else:
        if prefix:
            out[prefix] = obj
    return out


def _iso_to_ns(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return None


def _ns_to_iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()


def _span_kind_to_otlp(kind: Optional[str]) -> int:
    """Phoenix's SpanKind enum → OTLP span kind int.

    OTLP span kinds: 0=UNSPECIFIED, 1=INTERNAL, 2=SERVER, 3=CLIENT,
    4=PRODUCER, 5=CONSUMER. Phoenix's categories don't map 1:1; we
    pick INTERNAL as the most sensible default for everything.
    """
    return 1  # INTERNAL


_CARD_ATTR_KEYS = frozenset(
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
        "status",
        "name",
    }
)


@register
class PhoenixAdapter(BackendAdapter):
    handles = frozenset({"phoenix"})
    query_lang_label = "Phoenix filter"
    raw_placeholder = "llm.model_name == 'gpt-4'"

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_PHOENIX_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        self.graphql_url = f"{self.query_url}/graphql"
        # Precedence: backend-entry override > top-level project_name.
        # The top-level key matches the key the plugin uses elsewhere for
        # its OTel resource ``service.name``, so Phoenix's "project"
        # lines up with the rest of the stack.
        self.project_name = cfg.get("project_name") or cfg.get("project")
        if not self.project_name:
            from . import top_level_config

            self.project_name = top_level_config().get("project_name")
        # Optional bearer token for self-hosted Phoenix with auth.
        self.api_key = resolve_env_or_literal(cfg, "api_key", "api_key_env")
        self._project_id_cache: Optional[str] = None

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        return base

    # ── GraphQL helpers ───────────────────────────────────────────────

    def _gql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"query": query}
        if variables is not None:
            body["variables"] = variables
        resp = http_post_json(self.graphql_url, body, headers=headers, timeout=15.0)
        if not isinstance(resp, dict):
            from fastapi import HTTPException

            raise HTTPException(status_code=502, detail="Phoenix returned non-object")
        if resp.get("errors"):
            from fastapi import HTTPException

            msg = "; ".join(e.get("message", "?") for e in resp["errors"])
            raise HTTPException(status_code=502, detail=f"Phoenix GraphQL error: {msg}")
        return resp.get("data") or {}

    def _resolve_project_id(self) -> Optional[str]:
        if self._project_id_cache:
            return self._project_id_cache
        data = self._gql(
            "{ projects(first: 50) { edges { node { id name hasTraces } } } }"
        )
        edges = ((data or {}).get("projects") or {}).get("edges") or []
        projects = [e["node"] for e in edges if e.get("node")]
        if not projects:
            return None

        if self.project_name:
            for p in projects:
                if p.get("name") == self.project_name:
                    self._project_id_cache = p["id"]
                    return self._project_id_cache

        # Fallback: first project that has traces; else first project.
        for p in projects:
            if p.get("hasTraces"):
                self._project_id_cache = p["id"]
                return self._project_id_cache
        self._project_id_cache = projects[0]["id"]
        return self._project_id_cache

    # ── Filter translation ───────────────────────────────────────────

    def _build_filter_condition(self, f: StructuredFilter) -> Optional[str]:
        """Compose Phoenix ``filterCondition`` string from structured input."""
        user_raw = (f.raw or "").strip()
        parts: List[str] = []
        if user_raw:
            parts.append(f"({user_raw})")
        if f.name_regex:
            parts.append(f"name == '{_esc(f.name_regex)}'")  # no regex in UI expr
        if f.min_duration_ms:
            parts.append(f"latency_ms >= {int(f.min_duration_ms)}")
        if f.status == "error":
            parts.append("span.status_code == 'ERROR'")
        elif f.status == "ok":
            parts.append("span.status_code == 'OK'")
        for k, v in f.attr_equals.items():
            parts.append(f"{k} == '{_esc(str(v))}'")
        if f.free_text:
            # Phoenix supports substring with 'in' — best-effort over input.
            parts.append(f"'{_esc(f.free_text)}' in input.value")
        return " and ".join(parts) if parts else None

    # ── Normalized shape builders ────────────────────────────────────

    def _span_to_card_attrs(self, span: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the card-relevant attributes from a Phoenix span node."""
        attrs: Dict[str, Any] = {}
        raw = span.get("attributes")
        if isinstance(raw, str) and raw:
            try:
                flat = _flatten(json.loads(raw))
            except Exception:
                flat = {}
            for k, v in flat.items():
                if k in _CARD_ATTR_KEYS:
                    attrs[k] = v
        # First-class input/output take precedence.
        inp = (span.get("input") or {}).get("value")
        if inp:
            attrs["input.value"] = inp
        out = (span.get("output") or {}).get("value")
        if out:
            attrs["output.value"] = out
        # Token counts from dedicated fields (fall back when attrs missing).
        if span.get("tokenCountTotal") is not None:
            attrs.setdefault("gen_ai.usage.total_tokens", span["tokenCountTotal"])
        if span.get("tokenCountPrompt") is not None:
            attrs.setdefault("gen_ai.usage.input_tokens", span["tokenCountPrompt"])
        if span.get("tokenCountCompletion") is not None:
            attrs.setdefault("gen_ai.usage.output_tokens", span["tokenCountCompletion"])
        status_code = span.get("statusCode")
        if status_code:
            attrs.setdefault("status", status_code.lower() if isinstance(status_code, str) else status_code)
        if span.get("name"):
            attrs.setdefault("name", span["name"])
        return attrs

    def _span_node_to_otlp(self, span: Dict[str, Any]) -> Dict[str, Any]:
        """Build an OTLP span dict from a Phoenix span node."""
        raw_attrs: Dict[str, Any] = {}
        raw = span.get("attributes")
        if isinstance(raw, str) and raw:
            try:
                raw_attrs = _flatten(json.loads(raw))
            except Exception:
                raw_attrs = {}

        inp = (span.get("input") or {}).get("value")
        if inp is not None:
            raw_attrs.setdefault("input.value", inp)
        out = (span.get("output") or {}).get("value")
        if out is not None:
            raw_attrs.setdefault("output.value", out)
        if span.get("tokenCountTotal") is not None:
            raw_attrs.setdefault("gen_ai.usage.total_tokens", span["tokenCountTotal"])
        if span.get("tokenCountPrompt") is not None:
            raw_attrs.setdefault("gen_ai.usage.input_tokens", span["tokenCountPrompt"])
        if span.get("tokenCountCompletion") is not None:
            raw_attrs.setdefault("gen_ai.usage.output_tokens", span["tokenCountCompletion"])

        start_ns = _iso_to_ns(span.get("startTime"))
        end_ns = _iso_to_ns(span.get("endTime"))
        ctx = span.get("context") or {}

        otlp_span: Dict[str, Any] = {
            "traceId": ctx.get("traceId"),
            "spanId": span.get("spanId") or ctx.get("spanId"),
            "parentSpanId": span.get("parentId") or None,
            "name": span.get("name") or "",
            "kind": _span_kind_to_otlp(span.get("spanKind")),
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(raw_attrs),
            "status": otlp_status(span.get("statusCode"), span.get("statusMessage") or ""),
        }
        return otlp_span

    # ── Public API ───────────────────────────────────────────────────

    def search(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        project_id = self._resolve_project_id()
        if not project_id:
            return {"traces": []}

        fc = self._build_filter_condition(f)
        time_range = {"start": _ns_to_iso(start_s * 1_000_000_000),
                      "end": _ns_to_iso(end_s * 1_000_000_000)}

        # Phoenix's ``rootSpansOnly`` honours the companion
        # ``orphanSpanAsRootSpan`` flag, which defaults to True and
        # causes spans whose parent isn't in the current result set to
        # be treated as "root". That leaks non-root spans
        # (``api.*`` / ``tool.*``) into the roots-only view when their
        # actual parent is paginated out. Force it off for "root only".
        query = """
        query SearchSpans(
          $projectId: ID!,
          $first: Int!,
          $timeRange: TimeRange!,
          $filterCondition: String,
          $rootsOnly: Boolean!,
          $orphanAsRoot: Boolean!,
          $sort: SpanSort
        ) {
          node(id: $projectId) {
            ... on Project {
              name
              spans(
                first: $first,
                rootSpansOnly: $rootsOnly,
                orphanSpanAsRootSpan: $orphanAsRoot,
                timeRange: $timeRange,
                filterCondition: $filterCondition,
                sort: $sort
              ) {
                edges { node {
                  spanId name latencyMs statusCode startTime endTime
                  parentId spanKind attributes
                  context { traceId spanId }
                  input { value mimeType }
                  output { value mimeType }
                  tokenCountTotal tokenCountPrompt tokenCountCompletion
                  numChildSpans
                  trace { numSpans }
                } }
              }
            }
          }
        }
        """
        data = self._gql(
            query,
            {
                "projectId": project_id,
                "first": int(limit),
                "timeRange": time_range,
                "filterCondition": fc,
                "rootsOnly": bool(f.roots_only),
                # ``orphan-as-root`` is on when the user wants "any
                # span" (so orphans aren't dropped), off when they want
                # strict roots.
                "orphanAsRoot": not bool(f.roots_only),
                # Newest first — matches what the trace list UI wants
                # (latest activity at top) and lines up with the other
                # adapters.
                "sort": {"col": "startTime", "dir": "desc"},
            },
        )
        project = data.get("node") or {}
        project_name = project.get("name") or (self.project_name or "phoenix")
        edges = ((project.get("spans") or {}).get("edges")) or []

        traces: List[Dict[str, Any]] = []
        for e in edges:
            span = e.get("node") or {}
            trace_id = (span.get("context") or {}).get("traceId")
            if not trace_id:
                continue
            # Defensive: even with orphanSpanAsRootSpan=false, drop
            # anything that still has a parentId when the user asked
            # for root spans only.
            if f.roots_only and span.get("parentId"):
                continue
            start_ns = _iso_to_ns(span.get("startTime"))
            card_attrs = self._span_to_card_attrs(span)
            # ``Trace.numSpans`` is the authoritative total — count
            # spans across the whole trace, not just direct children of
            # the root (which is what ``numChildSpans`` gives).
            trace_span_count = (span.get("trace") or {}).get("numSpans")
            if not trace_span_count:
                trace_span_count = 1 + int(span.get("numChildSpans") or 0)
            traces.append(
                {
                    "traceID": trace_id,
                    "rootServiceName": project_name,
                    "rootTraceName": span.get("name") or "",
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "durationMs": int(span.get("latencyMs") or 0),
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": span.get("spanId"),
                                    "name": span.get("name") or "",
                                    "attributes": otlp_attrs_from_dict(card_attrs),
                                }
                            ],
                            "matched": int(trace_span_count),
                        }
                    ],
                }
            )
        return {"traces": traces}

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        project_id = self._resolve_project_id()
        if not project_id:
            return {"batches": []}

        query = """
        query GetTrace($projectId: ID!, $traceId: ID!) {
          node(id: $projectId) {
            ... on Project {
              name
              trace(traceId: $traceId) {
                traceId
                spans(first: 500) {
                  edges { node {
                    spanId name statusCode statusMessage startTime endTime
                    parentId spanKind attributes
                    context { traceId spanId }
                    input { value mimeType }
                    output { value mimeType }
                    tokenCountTotal tokenCountPrompt tokenCountCompletion
                  } }
                }
              }
            }
          }
        }
        """
        data = self._gql(query, {"projectId": project_id, "traceId": trace_id})
        project = data.get("node") or {}
        project_name = project.get("name") or "phoenix"
        trace = project.get("trace") or {}
        edges = ((trace.get("spans") or {}).get("edges")) or []

        otlp_spans = [self._span_node_to_otlp(e["node"]) for e in edges if e.get("node")]

        resource_attrs = otlp_attrs_from_dict({"service.name": project_name})
        return {
            "batches": [
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [{"spans": otlp_spans}],
                }
            ]
        }


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")
