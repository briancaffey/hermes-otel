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
    BackendError,
    StructuredFilter,
    http_post_json,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older_traces,
    trace_page,
)

_DEFAULT_PHOENIX_PORT = 6006
# Phoenix pages spans; the detail view asks for this many and says so when
# the trace has more.
_DETAIL_SPAN_CAP = 500
# Projects are listed once per adapter instance (instances are cached while
# the config file is unchanged, #290).
_PROJECT_LIST_SIZE = 200


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


# Phoenix's SpanKind (LLM, CHAIN, TOOL, …) is OpenInference's semantic kind,
# not OTLP's transport kind, so every span is INTERNAL (1) in the OTLP shape.
_OTLP_INTERNAL = 1


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
        "hermes.session_id",
        "hermes.turn.number",
    }
)


# Whether ``Project.spans`` still takes ``rootSpansOnly`` /
# ``orphanSpanAsRootSpan``: Phoenix 20.x dropped both (``parent_id is None``
# in the filter condition is the replacement, verified on 20.20.0,
# 2026-10-09); older builds still have them. Probed once per query URL.
_ROOT_ARGS_CACHE: Dict[str, bool] = {}

_SPANS_ARGS_QUERY = '{ __type(name: "Project") { fields { name args { name } } } }'


@register
class PhoenixAdapter(BackendAdapter):
    handles = frozenset({"phoenix"})
    query_lang_label = "Phoenix filter"
    raw_placeholder = "llm.model_name == 'gpt-4'"
    filter_support = {
        "service": "none",  # a Phoenix project IS the service
        "name": "client",  # a substring pre-filter, the prefix checked on the rows
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "server",
        "status_error": "server",
        "status_ok": "server",
        "free_text": "server",
        "raw": "server",
        "roots_only": "server",
    }

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
        # The project actually being shown, and whether it was chosen by
        # fallback (no project configured) rather than by name (#159).
        self.resolved_project_name: Optional[str] = None
        self.project_fallback: bool = False

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["project_name"] = self.project_name
        base["project_resolved"] = self.resolved_project_name
        base["project_fallback"] = self.project_fallback
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
            raise BackendError(502, "Phoenix returned non-object")
        if resp.get("errors"):
            msg = "; ".join(e.get("message", "?") for e in resp["errors"])
            raise BackendError(502, f"Phoenix GraphQL error: {msg}")
        return resp.get("data") or {}

    def _resolve_project_id(self) -> Optional[str]:
        if self._project_id_cache:
            return self._project_id_cache
        data = self._gql(
            f"{{ projects(first: {_PROJECT_LIST_SIZE}) {{ edges {{ node {{ id name hasTraces }} }} }} }}"
        )
        edges = ((data or {}).get("projects") or {}).get("edges") or []
        projects = [e["node"] for e in edges if e.get("node")]
        if not projects:
            return None

        if self.project_name:
            for p in projects:
                if p.get("name") == self.project_name:
                    self._project_id_cache = p["id"]
                    self.resolved_project_name = p.get("name")
                    self.project_fallback = False
                    return self._project_id_cache
            # A configured project that does not exist is an error, not an
            # invitation to show some other project's traces under its name
            # (#159). Say what exists so the typo is easy to spot.
            available = ", ".join(sorted(str(p.get("name")) for p in projects)) or "none"
            raise BackendError(
                404,
                f"Phoenix project {self.project_name!r} not found at {self.query_url}; "
                f"available: {available}",
                "not_found",
            )

        # No project configured: show the first project that has traces (else
        # the first project) and report which one in status() so the choice
        # is visible.
        chosen = next((p for p in projects if p.get("hasTraces")), projects[0])
        self._project_id_cache = chosen["id"]
        self.resolved_project_name = chosen.get("name")
        self.project_fallback = True
        return self._project_id_cache

    # ── Filter translation ───────────────────────────────────────────

    def _build_filter_condition(self, f: StructuredFilter) -> Optional[str]:
        """Compose Phoenix ``filterCondition`` string from structured input."""
        user_raw = (f.raw or "").strip()
        parts: List[str] = []
        if user_raw:
            parts.append(f"({user_raw})")
        if f.name_prefix:
            # The filter DSL has substring membership but no startswith; the
            # prefix is enforced on the returned rows (filter_support: client).
            parts.append(f"'{_esc(f.name_prefix)}' in name")
        if f.name_regex:
            parts.append(f"name == '{_esc(f.name_regex)}'")  # no regex in UI expr
        if f.min_duration_ms:
            parts.append(f"latency_ms >= {int(f.min_duration_ms)}")
        # ``status_code`` is the documented filter field (the bare name, as the
        # Phoenix UI's filter bar accepts it).
        if f.status == "error":
            parts.append("status_code == 'ERROR'")
        elif f.status == "ok":
            parts.append("status_code == 'OK'")
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
            attrs.setdefault(
                "status", status_code.lower() if isinstance(status_code, str) else status_code
            )
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
            "kind": _OTLP_INTERNAL,
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "endTimeUnixNano": str(end_ns) if end_ns else "0",
            "attributes": otlp_attrs_from_dict(raw_attrs),
            "status": otlp_status(span.get("statusCode"), span.get("statusMessage") or ""),
        }
        return otlp_span

    # ── Public API ───────────────────────────────────────────────────

    def _supports_root_args(self) -> bool:
        """True when ``Project.spans`` takes ``rootSpansOnly`` (Phoenix < 20);
        introspected once per query URL. An answer that does not describe
        the field at all (a proxy, a fake) keeps the legacy arguments."""
        cached = _ROOT_ARGS_CACHE.get(self.query_url)
        if cached is not None:
            return cached
        supported = True
        try:
            data = self._gql(_SPANS_ARGS_QUERY, {})
            fields = ((data.get("__type") or {}).get("fields")) if isinstance(data, dict) else None
            spans = next(
                (x for x in (fields or []) if isinstance(x, dict) and x.get("name") == "spans"),
                None,
            )
            if spans is not None:
                names = {a.get("name") for a in (spans.get("args") or []) if isinstance(a, dict)}
                supported = "rootSpansOnly" in names
        except BackendError:
            raise
        except Exception:
            supported = True
        _ROOT_ARGS_CACHE[self.query_url] = supported
        return supported

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        project_id = self._resolve_project_id()
        if not project_id:
            return {"traces": []}

        fc = self._build_filter_condition(f)
        legacy_root_args = self._supports_root_args()
        if f.roots_only and not legacy_root_args:
            # Current Phoenix: roots are a filter-condition predicate.
            fc = "parent_id is None" + (f" and {fc}" if fc else "")
        end_ns = end_s * 1_000_000_000
        if f.before_ns:
            end_ns = min(end_ns, int(f.before_ns))
        time_range = {
            "start": _ns_to_iso(start_s * 1_000_000_000),
            "end": _ns_to_iso(end_ns),
        }

        # Phoenix's ``rootSpansOnly`` honours the companion
        # ``orphanSpanAsRootSpan`` flag, which defaults to True and
        # causes spans whose parent isn't in the current result set to
        # be treated as "root". That leaks non-root spans
        # (``api.*`` / ``tool.*``) into the roots-only view when their
        # actual parent is paginated out. Force it off for "root only".
        root_vars = "$rootsOnly: Boolean!, $orphanAsRoot: Boolean!," if legacy_root_args else ""
        root_args = (
            "rootSpansOnly: $rootsOnly, orphanSpanAsRootSpan: $orphanAsRoot,"
            if legacy_root_args
            else ""
        )
        query = f"""
        query SearchSpans(
          $projectId: ID!,
          $first: Int!,
          $timeRange: TimeRange!,
          $filterCondition: String,
          {root_vars}
          $sort: SpanSort
        ) {{
          node(id: $projectId) {{
            ... on Project {{
              name
              spans(
                first: $first,
                {root_args}
                timeRange: $timeRange,
                filterCondition: $filterCondition,
                sort: $sort
              ) {{
                edges {{ node {{
                  spanId name latencyMs statusCode startTime endTime
                  parentId spanKind attributes
                  context {{ traceId spanId }}
                  input {{ value mimeType }}
                  output {{ value mimeType }}
                  tokenCountTotal tokenCountPrompt tokenCountCompletion
                  numChildSpans
                  trace {{ numSpans }}
                }} }}
              }}
            }}
          }}
        }}
        """
        data = self._gql(
            query,
            {
                "projectId": project_id,
                # One beyond the page so has_more is exact (trace_page). Widened
                # (the kind filter) the rows are spans and several of one trace
                # share a page, so ask for more and dedupe below.
                "first": int(limit) + 1 if f.roots_only else int(limit) * 4 + 1,
                "timeRange": time_range,
                "filterCondition": fc,
                **(
                    {
                        "rootsOnly": bool(f.roots_only),
                        # ``orphan-as-root`` is on when the user wants "any
                        # span" (so orphans aren't dropped), off when they
                        # want strict roots.
                        "orphanAsRoot": not bool(f.roots_only),
                    }
                    if legacy_root_args
                    else {}
                ),
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
        seen_traces: set = set()
        oldest_fetched: Dict[str, int] = {}
        for e in edges:
            span = e.get("node") or {}
            trace_id = (span.get("context") or {}).get("traceId")
            if not trace_id:
                continue
            row_start = _iso_to_ns(span.get("startTime"))
            if row_start:
                oldest_fetched[trace_id] = min(oldest_fetched.get(trace_id, row_start), row_start)
            if not f.roots_only and trace_id in seen_traces:
                # One card per trace, named after its newest matching span,
                # as the other adapters show a widened search.
                continue
            # Defensive: even with orphanSpanAsRootSpan=false, drop
            # anything that still has a parentId when the user asked
            # for root spans only.
            if f.roots_only and span.get("parentId"):
                continue
            if f.name_prefix and not str(span.get("name") or "").startswith(f.name_prefix):
                continue
            seen_traces.add(trace_id)
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
                    # Whole-trace span count, so the card never shows the
                    # matched-span count as if it were the trace size (#179).
                    "spanCount": int(trace_span_count),
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
        page = trace_page(strictly_older_traces(traces, f), limit)
        if not f.roots_only and not page["has_more"] and len(edges) > int(limit) * 4 and traces:
            # The fetch came back full: older spans exist even though they
            # collapsed into fewer traces than the page holds.
            page["has_more"] = True
            page["next_before_ns"] = min(
                int(t.get("startTimeUnixNano") or 0) for t in page["traces"]
            )
        if not f.roots_only and page["has_more"] and page["traces"]:
            # Widened rows are spans: move the cursor below the oldest
            # fetched span of every trace on this page, so a trace's other
            # matching spans do not bring it back on the next page.
            shown = [
                oldest_fetched[t["traceID"]]
                for t in page["traces"]
                if t["traceID"] in oldest_fetched
            ]
            if shown:
                page["next_before_ns"] = min(int(page["next_before_ns"] or 0) or min(shown), *shown)
        return page

    def trace_url(self, trace_id: str) -> Optional[str]:
        project_id = self._resolve_project_id()
        if not project_id:
            return None
        return f"{self.query_url}/projects/{project_id}/traces/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        project_id = self._resolve_project_id()
        if not project_id:
            return {"batches": []}

        query = """
        query GetTrace($projectId: ID!, $traceId: ID!, $first: Int!) {
          node(id: $projectId) {
            ... on Project {
              name
              trace(traceId: $traceId) {
                traceId
                numSpans
                spans(first: $first) {
                  pageInfo { hasNextPage }
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
        data = self._gql(
            query, {"projectId": project_id, "traceId": trace_id, "first": _DETAIL_SPAN_CAP}
        )
        project = data.get("node") or {}
        project_name = project.get("name") or "phoenix"
        trace = project.get("trace") or {}
        if not trace:
            raise BackendError(
                404, f"Trace {trace_id} not found in Phoenix project {project_name!r}", "not_found"
            )
        spans_conn = trace.get("spans") or {}
        edges = spans_conn.get("edges") or []

        otlp_spans = [self._span_node_to_otlp(e["node"]) for e in edges if e.get("node")]
        if not otlp_spans:
            raise BackendError(
                404, f"Trace {trace_id} not found in Phoenix project {project_name!r}", "not_found"
            )
        truncated = bool((spans_conn.get("pageInfo") or {}).get("hasNextPage")) or (
            len(otlp_spans) >= _DETAIL_SPAN_CAP
        )
        span_count = trace.get("numSpans")

        resource_attrs = otlp_attrs_from_dict({"service.name": project_name})
        return {
            "batches": [
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [{"spans": otlp_spans}],
                }
            ],
            "truncated": truncated,
            "span_count": int(span_count) if isinstance(span_count, int) else len(otlp_spans),
        }


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")
