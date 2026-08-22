"""Tempo / LGTM adapter.

Tempo is the reference backend — the card + detail response shapes the
rest of the plugin consumes were copied from Tempo's search + trace
JSON. Everything else translates *to* these shapes.

Query language is TraceQL. The adapter composes a ``| select()``
pipeline so each search hit comes back with the attributes the card
renderer needs (model, provider, tokens, input/output, tool name,
status). If the user supplies their own query the raw text is passed
through; ``select()`` is appended only when they haven't added one.
"""

from __future__ import annotations

from typing import Any, Dict
from urllib import parse as _urlparse

from fastapi import HTTPException

from . import register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_get_json,
    rewrite_host_for_docker,
)

# Default Tempo HTTP API port in the otel-lgtm and standalone tempo
# images. Override per deployment by adding ``query_port`` to the
# backend entry in config.yaml.
_DEFAULT_TEMPO_QUERY_PORT = 3200

_CARD_SELECT_ATTRS = (
    ".llm.model_name",
    ".llm.provider",
    ".llm.api_mode",
    ".gen_ai.usage.input_tokens",
    ".gen_ai.usage.output_tokens",
    ".gen_ai.usage.total_tokens",
    ".llm.response.finish_reason",
    ".llm.response.tool_calls",
    ".tool.name",
    ".input.value",
    ".output.value",
    ".llm.output.content",
    "status",
    "name",
)


def _esc(s: str) -> str:
    """Quote a string for safe inclusion in a TraceQL string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


@register
class TempoAdapter(BackendAdapter):
    handles = frozenset({"lgtm", "tempo"})
    query_lang_label = "TraceQL"
    raw_placeholder = '{ .llm.provider = "openai" }'

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_TEMPO_QUERY_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        return base

    def _build_traceql(self, f: StructuredFilter) -> str:
        """Compose the effective TraceQL query.

        Structured filter predicates are AND'd into a ``{}``-style
        expression when no raw query is supplied. When a raw query *is*
        supplied we honour it verbatim and only decorate it with
        ``| select()``.
        """
        user_q = (f.raw or "").strip()
        if user_q:
            base = user_q
        else:
            predicates = []
            if f.service:
                predicates.append(f'resource.service.name = "{_esc(f.service)}"')
            if f.name_regex:
                predicates.append(f'name =~ "{_esc(f.name_regex)}"')
            if f.status == "error":
                predicates.append("status = error")
            elif f.status == "ok":
                predicates.append("status = ok")
            for k, v in f.attr_equals.items():
                predicates.append(f'.{k} = "{_esc(str(v))}"')
            base = "{ " + " && ".join(predicates) + " }" if predicates else "{}"

        if "select(" in base:
            return base
        return base + " | select(" + ", ".join(_CARD_SELECT_ATTRS) + ")"

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "start": start_s,
            "end": end_s,
            "q": self._build_traceql(f),
        }
        if f.min_duration_ms and f.min_duration_ms > 0:
            params["minDuration"] = f"{f.min_duration_ms}ms"

        url = f"{self.query_url}/api/search?{_urlparse.urlencode(params)}"
        try:
            data = http_get_json(url)
        except HTTPException:
            # Older Tempo builds reject the enriched TraceQL; retry with
            # the bare parameters (raw query only, no select pipeline).
            fallback: Dict[str, Any] = {"limit": limit, "start": start_s, "end": end_s}
            if f.raw and f.raw.strip():
                fallback["q"] = f.raw.strip()
            if f.min_duration_ms and f.min_duration_ms > 0:
                fallback["minDuration"] = f"{f.min_duration_ms}ms"
            url = f"{self.query_url}/api/search?{_urlparse.urlencode(fallback)}"
            data = http_get_json(url)

        result = data if isinstance(data, dict) else {"traces": [], "raw": data}

        # Client-side root filter: TraceQL predicates match at the span
        # level, so a ``name =~ "api.*"`` query can return a cron trace
        # whose *child* is an api span. When the user asked for roots
        # only, drop traces where none of the matched spans is the
        # trace root.
        if f.roots_only and isinstance(result, dict):
            filtered = []
            for t in result.get("traces") or []:
                root_name = (t.get("rootTraceName") or "").strip()
                if not root_name:
                    filtered.append(t)
                    continue
                span_sets = t.get("spanSets") or ([t["spanSet"]] if t.get("spanSet") else [])
                matched_root = False
                for ss in span_sets:
                    for sp in ss.get("spans") or []:
                        if (sp.get("name") or "").strip() == root_name:
                            matched_root = True
                            break
                    if matched_root:
                        break
                if matched_root:
                    filtered.append(t)
            result["traces"] = filtered

        # Newest first. Tempo usually returns in start-time order, but
        # it's not guaranteed across storage blocks; sort explicitly.
        if isinstance(result, dict):
            traces = result.get("traces") or []
            try:
                traces.sort(
                    key=lambda t: int(t.get("startTimeUnixNano") or 0),
                    reverse=True,
                )
            except (TypeError, ValueError):
                pass
            result["traces"] = traces

        return result

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/traces/{trace_id}"
        return http_get_json(url, timeout=20.0)
