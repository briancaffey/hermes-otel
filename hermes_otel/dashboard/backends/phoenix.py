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
from typing import Any, Dict, List, Optional, Tuple

from . import register
from .base import (
    BackendAdapter,
    BackendError,
    StructuredFilter,
    bearer,
    http_post_json,
    not_found,
    query_origin,
    register_cache,
    resolve_env_or_literal,
)
from .paging import strictly_older_traces, trace_end_bound, trace_page, widened_fetch
from .shapes import (
    CARD_ATTRIBUTE_SET,
    SpanRow,
    card_row,
    flatten_dotted,
    otlp_batches,
    otlp_span,
    otlp_status,
    recard_roots,
)
from .timeconv import iso_to_ns

_DEFAULT_PHOENIX_PORT = 6006
# Phoenix pages spans; the detail view asks for this many and says so when
# the trace has more.
_DETAIL_SPAN_CAP = 500
# Projects are listed once per adapter instance (instances are cached while
# the config file is unchanged, #290).
_PROJECT_LIST_SIZE = 200


def _attributes_of(span: Dict[str, Any]) -> Dict[str, Any]:
    """Phoenix's ``attributes`` is a JSON string nested by dot groupings
    (``{"llm": {"model_name": ...}}``); flattened to the OTLP keys
    (``llm.model_name``) the rest of the dashboard uses."""
    raw = span.get("attributes")
    if not (isinstance(raw, str) and raw):
        return {}
    try:
        return flatten_dotted(json.loads(raw))
    except Exception:
        return {}


def _ns_to_iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()


# Whether ``Project.spans`` still takes ``rootSpansOnly`` /
# ``orphanSpanAsRootSpan``: Phoenix 20.x dropped both (``parent_id is None``
# in the filter condition is the replacement, verified on 20.20.0,
# 2026-10-09); older builds still have them. Probed once per query URL.
_ROOT_ARGS_CACHE: Dict[str, bool] = register_cache({})

_SPANS_ARGS_QUERY = '{ __type(name: "Project") { fields { name args { name } } } }'

# The search document; ``root_vars`` / ``root_args`` are the roots clause of
# the schema in use (see :func:`_root_clause`), empty on current Phoenix.
_SEARCH_QUERY = """
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


def _root_clause(legacy: bool, roots_only: bool) -> Tuple[str, str, Dict[str, Any], Optional[str]]:
    """How the search asks for roots, per schema: the declarations and
    ``spans(...)`` arguments spliced into :data:`_SEARCH_QUERY`, the variables
    they take, and the predicate the filter condition starts with.

    Legacy ``rootSpansOnly`` honours the companion ``orphanSpanAsRootSpan``
    flag (default True), which treats a span whose parent is not in the result
    set as a root and so leaks ``api.*`` / ``tool.*`` spans into the roots-only
    view when their parent is paginated out: on only when the user wants any
    span (orphans kept), off for strict roots. Current Phoenix has neither
    argument: roots are the ``parent_id is None`` predicate.
    """
    if legacy:
        return (
            "$rootsOnly: Boolean!, $orphanAsRoot: Boolean!,",
            "rootSpansOnly: $rootsOnly, orphanSpanAsRootSpan: $orphanAsRoot,",
            {"rootsOnly": roots_only, "orphanAsRoot": not roots_only},
            None,
        )
    return "", "", {}, ("parent_id is None" if roots_only else None)


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
        self.query_url = query_origin(cfg, _DEFAULT_PHOENIX_PORT)
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
        base["project_name"] = self.project_name
        base["project_resolved"] = self.resolved_project_name
        base["project_fallback"] = self.project_fallback
        return base

    # ── GraphQL helpers ───────────────────────────────────────────────

    def _gql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = {"query": query}
        if variables is not None:
            body["variables"] = variables
        resp = http_post_json(self.graphql_url, body, headers=bearer(self.api_key), timeout=15.0)
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
        """The card's attributes from a Phoenix span node: the known keys of
        the span's attributes, then Phoenix's first-class fields where the
        attributes lack them."""
        attrs = {
            k: v
            for k, v in _attributes_of(span).items()
            if k in CARD_ATTRIBUTE_SET or k in ("status", "name")
        }
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
        raw_attrs = _attributes_of(span)
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

        ctx = span.get("context") or {}
        # Phoenix's SpanKind (LLM, CHAIN, TOOL, …) is OpenInference's semantic
        # kind, not OTLP's transport kind: every span is INTERNAL (the default).
        return otlp_span(
            ctx.get("traceId"),
            span.get("spanId") or ctx.get("spanId"),
            span.get("parentId"),
            span.get("name"),
            start_ns=iso_to_ns(span.get("startTime")) or 0,
            end_ns=iso_to_ns(span.get("endTime")) or 0,
            attrs=raw_attrs,
            status=otlp_status(span.get("statusCode"), span.get("statusMessage") or ""),
        )

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
            return trace_page([], limit)

        fc = self._build_filter_condition(f)
        root_vars, root_args, root_variables, root_predicate = _root_clause(
            self._supports_root_args(), bool(f.roots_only)
        )
        if root_predicate:
            fc = root_predicate + (f" and {fc}" if fc else "")
        time_range = {
            "start": _ns_to_iso(start_s * 1_000_000_000),
            "end": _ns_to_iso(trace_end_bound(f, end_s, "ns")),
        }
        # Widened (the kind filter) the rows are spans and several of one
        # trace share a page, so ask for more and dedupe below.
        fetch = widened_fetch(limit, f.roots_only)
        data = self._gql(
            _SEARCH_QUERY.format(root_vars=root_vars, root_args=root_args),
            {
                "projectId": project_id,
                "first": fetch,
                "timeRange": time_range,
                "filterCondition": fc,
                **root_variables,
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
            row_start = iso_to_ns(span.get("startTime"))
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
            # ``Trace.numSpans`` is the authoritative total — count spans
            # across the whole trace, not just direct children of the root
            # (which is what ``numChildSpans`` gives), so the card never shows
            # the matched-span count as if it were the trace size (#179).
            trace_span_count = (span.get("trace") or {}).get("numSpans")
            if not trace_span_count:
                trace_span_count = 1 + int(span.get("numChildSpans") or 0)
            traces.append(
                card_row(
                    trace_id,
                    span_id=span.get("spanId"),
                    name=span.get("name"),
                    attrs=self._span_to_card_attrs(span),
                    start_ns=iso_to_ns(span.get("startTime")) or 0,
                    duration_ms=int(span.get("latencyMs") or 0),
                    # A Phoenix project is the service (see ``filter_support``).
                    service=project_name,
                    span_count=int(trace_span_count),
                )
            )
        page = trace_page(strictly_older_traces(traces, f), limit)
        if not f.roots_only and not page["has_more"] and len(edges) >= fetch and traces:
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
        if not f.roots_only and not f.name_prefix:
            # A tool filter widens to any span, but the row stands for the
            # trace: its root, the matched span its span set (#558).
            ids = [t["traceID"] for t in page["traces"]]
            recard_roots(page, self._roots(project_id, project_name, ids, time_range))
        return page

    def _roots(
        self,
        project_id: str,
        project_name: str,
        trace_ids: List[str],
        time_range: Dict[str, str],
    ) -> Dict[str, SpanRow]:
        """The root spans of these traces, by trace id: one span query, the
        roots-only clause plus ``trace_id in [...]``. A root outside the
        window, or a failed query, leaves its trace's matched card."""
        if not trace_ids:
            return {}
        root_vars, root_args, root_variables, root_predicate = _root_clause(
            self._supports_root_args(), True
        )
        ids = ", ".join(f"'{_esc(t)}'" for t in trace_ids)
        fc = f"trace_id in [{ids}]"
        if root_predicate:
            fc = f"{root_predicate} and {fc}"
        try:
            data = self._gql(
                _SEARCH_QUERY.format(root_vars=root_vars, root_args=root_args),
                {
                    "projectId": project_id,
                    "first": len(trace_ids),
                    "timeRange": time_range,
                    "filterCondition": fc,
                    **root_variables,
                    "sort": {"col": "startTime", "dir": "desc"},
                },
            )
        except BackendError:
            return {}
        edges = (((data.get("node") or {}).get("spans") or {}).get("edges")) or []
        out: Dict[str, SpanRow] = {}
        for e in edges:
            span = e.get("node") or {}
            tid = (span.get("context") or {}).get("traceId")
            if not tid or span.get("parentId"):
                continue
            start = iso_to_ns(span.get("startTime")) or 0
            out.setdefault(
                tid,
                SpanRow(
                    tid,
                    span.get("spanId"),
                    None,
                    str(span.get("name") or ""),
                    start_ns=start,
                    end_ns=start + int(span.get("latencyMs") or 0) * 1_000_000,
                    attrs={},
                    service=project_name,
                ),
            )
        return out

    def trace_url(self, trace_id: str) -> Optional[str]:
        """The trace's page in the project, once a search or detail has
        resolved the project; ``None`` before that. ``trace_url`` is a pure
        read like ``status()``: it never queries the server (#403)."""
        project_id = self._project_id_cache
        if not project_id:
            return None
        return f"{self.query_url}/projects/{project_id}/traces/{trace_id}"

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        project_id = self._resolve_project_id()
        if not project_id:
            raise not_found(
                trace_id,
                "Phoenix",
                f"Trace {trace_id} not found in Phoenix (no project has traces)",
            )

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
        missing = f"Trace {trace_id} not found in Phoenix project {project_name!r}"
        if not trace:
            raise not_found(trace_id, "Phoenix", missing)
        spans_conn = trace.get("spans") or {}
        edges = spans_conn.get("edges") or []

        otlp_spans = [self._span_node_to_otlp(e["node"]) for e in edges if e.get("node")]
        if not otlp_spans:
            raise not_found(trace_id, "Phoenix", missing)
        truncated = bool((spans_conn.get("pageInfo") or {}).get("hasNextPage")) or (
            len(otlp_spans) >= _DETAIL_SPAN_CAP
        )
        span_count = trace.get("numSpans")
        return {
            "batches": otlp_batches({project_name: otlp_spans}),
            "truncated": truncated,
            "span_count": int(span_count) if isinstance(span_count, int) else len(otlp_spans),
        }


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")
