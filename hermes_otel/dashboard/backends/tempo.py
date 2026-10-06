"""Tempo / LGTM adapter.

Traces come from Tempo. For ``type: lgtm`` (and any Tempo entry that names
them) metrics come from the stack's Prometheus and logs from its Loki, via
the ``_prometheus`` / ``_loki`` helpers (#194): ``prometheus_url`` and
``loki_url`` default to ports 9090 and 3100 on the Tempo host, which is the
``grafana/otel-lgtm`` layout.

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

import re
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import _loki, _prometheus, register
from .base import (
    BackendAdapter,
    BackendError,
    ConfigError,
    HTTPException,
    LogFilter,
    StructuredFilter,
    http_get_json,
    rewrite_host_for_docker,
    strictly_older_traces,
    trace_page,
)

__all__ = ["TempoAdapter", "HTTPException"]

_SELECT_PIPELINE = re.compile(r"\|\s*select\s*\(")

# Default Tempo HTTP API port in the otel-lgtm and standalone tempo
# images. Override per deployment by adding ``query_port`` to the
# backend entry in config.yaml.
_DEFAULT_TEMPO_QUERY_PORT = 3200
# The otel-lgtm image's Prometheus and Loki ports, used when the entry does
# not name ``prometheus_url`` / ``loki_url`` explicitly.
_DEFAULT_PROMETHEUS_PORT = 9090
_DEFAULT_LOKI_PORT = 3100

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
    ".hermes.session_id",
    ".hermes.turn.number",
    "status",
    "name",
)


def _dedupe_spans(data: Any) -> Any:
    """Drop repeated spans (same span id) from an OTLP-JSON trace.

    Two entries that both end in one Tempo (a ``tempo`` entry next to an
    ``lgtm`` gateway that forwards to the same Tempo) store every span twice
    until compaction merges them, and the waterfall would show each span
    doubled. The first copy wins.
    """
    if not isinstance(data, dict):
        return data
    seen: set = set()
    for batch in data.get("batches") or []:
        for scope in batch.get("scopeSpans") or []:
            kept = []
            for sp in scope.get("spans") or []:
                sid = sp.get("spanId")
                if sid and sid in seen:
                    continue
                if sid:
                    seen.add(sid)
                kept.append(sp)
            scope["spans"] = kept
    return data


def _re_esc(text: str) -> str:
    """Escape ``text`` for a TraceQL regex inside a double-quoted string."""
    out = []
    for ch in text:
        if ch in ".^$*+?()[]{}|\\":
            out.append("\\\\" + ch)
        elif ch == '"':
            out.append('\\"')
        else:
            out.append(ch)
    return "".join(out)


def _esc(s: str) -> str:
    """Quote a string for safe inclusion in a TraceQL string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


_DISABLED = ("off", "none", "disabled", "false")


def _sibling_url(explicit: Any, is_stack: bool, scheme: str, host: str, port: int) -> Optional[str]:
    """An explicit URL wins, ``off`` disables the signal, and an ``lgtm`` entry
    otherwise defaults to the stack port on the Tempo host."""
    if explicit is False or (isinstance(explicit, str) and explicit.strip().lower() in _DISABLED):
        return None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().rstrip("/")
    if is_stack:
        return f"{scheme}://{host}:{port}"
    return None


@register
class TempoAdapter(BackendAdapter):
    handles = frozenset({"lgtm", "tempo"})
    query_lang_label = "TraceQL"
    raw_placeholder = '{ .llm.provider = "openai" }'
    filter_support = {
        "service": "server",
        "name": "server",
        "model": "server",
        "session": "server",
        "tool": "server",
        "min_duration": "server",
        "status_error": "server",
        "status_ok": "server",
        "free_text": "server",
        "raw": "server",
        "roots_only": "client",
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        endpoint = cfg.get("endpoint") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = cfg.get("query_port") or _DEFAULT_TEMPO_QUERY_PORT
        host = rewrite_host_for_docker(host)
        self.query_url = f"{scheme}://{host}:{port}"
        # Metrics and logs: on for ``lgtm`` (the stack ships Prometheus + Loki
        # next to Tempo) and for any entry that points at them explicitly.
        is_stack = cfg.get("type") == "lgtm"
        self.prometheus_url = _sibling_url(
            cfg.get("prometheus_url"), is_stack, scheme, host, _DEFAULT_PROMETHEUS_PORT
        )
        self.loki_url = _sibling_url(
            cfg.get("loki_url"), is_stack, scheme, host, _DEFAULT_LOKI_PORT
        )
        self.supports_metrics = self.prometheus_url is not None
        self.supports_logs = self.loki_url is not None
        self.metrics_match = cfg.get("metrics_match") or None
        self.loki_selector = cfg.get("loki_selector") or _loki.DEFAULT_SELECTOR

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        if self.prometheus_url:
            base["prometheus_url"] = self.prometheus_url
        if self.loki_url:
            base["loki_url"] = self.loki_url
        return base

    # ── metrics via Prometheus (#194) ─────────────────────────────────

    def metric_names(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return _prometheus.metric_names(self._prometheus(), start_s, end_s, self.metrics_match)

    def metrics_query(
        self,
        name: str,
        start_s: int,
        end_s: int,
        bucket_s: int,
        group_by: Optional[str] = None,
        agg: str = "sum",
    ) -> Dict[str, Any]:
        return _prometheus.metrics_query(
            self._prometheus(), name, start_s, end_s, bucket_s, group_by=group_by, agg=agg
        )

    # ── logs via Loki (#194) ──────────────────────────────────────────

    def logs_search(
        self, f: LogFilter, start_s: int, end_s: int, limit: int
    ) -> List[Dict[str, Any]]:
        return _loki.logs_search(self._loki(), f, start_s, end_s, limit, self.loki_selector)

    def loggers(self, start_s: int, end_s: int) -> List[Dict[str, Any]]:
        return _loki.loggers(self._loki(), start_s, end_s, self.loki_selector)

    def _prometheus(self) -> str:
        if not self.prometheus_url:
            raise ConfigError("This backend has no prometheus_url configured")
        return self.prometheus_url

    def _loki(self) -> str:
        if not self.loki_url:
            raise ConfigError("This backend has no loki_url configured")
        return self.loki_url

    def _predicates(self, f: StructuredFilter) -> List[str]:
        predicates: List[str] = []
        if f.service:
            predicates.append(f'resource.service.name = "{_esc(f.service)}"')
        if f.name_prefix:
            predicates.append(f'name =~ "^{_re_esc(f.name_prefix)}"')
        if f.name_regex:
            predicates.append(f'name =~ "{_esc(f.name_regex)}"')
        if f.status == "error":
            predicates.append("status = error")
        elif f.status == "ok":
            predicates.append("status = ok")
        if f.min_duration_ms and f.min_duration_ms > 0:
            # In the query itself: ``minDuration`` is the legacy tag search's
            # parameter and is not documented next to a TraceQL ``q``.
            predicates.append(f"duration >= {int(f.min_duration_ms)}ms")
        for k, v in f.attr_equals.items():
            predicates.append(f'.{k} = "{_esc(str(v))}"')
        if f.free_text:
            # Any span of the trace whose captured input or output
            # mentions the text; regex-escaped so a marker with dots or
            # brackets matches literally.
            pat = ".*" + _re_esc(f.free_text) + ".*"
            predicates.append(f'(span.input.value =~ "{pat}" || span.output.value =~ "{pat}")')
        return predicates

    def _build_traceql(self, f: StructuredFilter, with_select: bool = True) -> str:
        """Compose the effective TraceQL query.

        Structured filter predicates are AND'd into a ``{}``-style
        expression when no raw query is supplied. When a raw query *is*
        supplied we honour it verbatim and only decorate it with
        ``| select()`` (``with_select=False`` leaves that off: the retry
        for Tempo builds that reject the pipeline keeps every predicate).
        """
        user_q = (f.raw or "").strip()
        if user_q:
            base = user_q
        else:
            predicates = self._predicates(f)
            base = "{ " + " && ".join(predicates) + " }" if predicates else "{}"

        if not with_select or _SELECT_PIPELINE.search(base):
            return base
        return base + " | select(" + ", ".join(_CARD_SELECT_ATTRS) + ")"

    def _search_params(self, f: StructuredFilter, start_s: int, end_s: int, limit: int, q: str):
        end = int(end_s)
        if f.before_ns:
            # Tempo bounds the search in whole seconds; the cursor rounds up
            # so the row it was taken from is still inside the window and
            # strictly_older_traces() drops it exactly.
            end = min(end, int(f.before_ns) // 1_000_000_000 + 1)
        params: Dict[str, Any] = {
            "limit": int(limit) + 1,
            "start": int(start_s),
            "end": end,
            "q": q,
        }
        if f.raw and f.raw.strip() and f.min_duration_ms and f.min_duration_ms > 0:
            params["minDuration"] = f"{f.min_duration_ms}ms"
        return params

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        params = self._search_params(f, start_s, end_s, limit, self._build_traceql(f))
        url = f"{self.query_url}/api/search?{_urlparse.urlencode(params)}"
        try:
            data = http_get_json(url)
        except BackendError as exc:
            # Older Tempo builds reject the ``| select()`` pipeline with a 4xx;
            # retry the same predicates without it. Anything else (unreachable,
            # auth, 5xx) is reported as is: a second request would only hit a
            # dead backend twice and could not improve the answer (#296).
            if exc.kind != "backend" or "Backend returned 4" not in str(exc.detail):
                raise
            params = self._search_params(
                f, start_s, end_s, limit, self._build_traceql(f, with_select=False)
            )
            url = f"{self.query_url}/api/search?{_urlparse.urlencode(params)}"
            data = http_get_json(url)

        result: Dict[str, Any] = data if isinstance(data, dict) else {"traces": []}
        traces = [t for t in (result.get("traces") or []) if isinstance(t, dict)]

        # Client-side root filter: TraceQL predicates match at the span
        # level, so a ``name =~ "api.*"`` query can return a cron trace
        # whose *child* is an api span. When the user asked for roots
        # only, drop traces where none of the matched spans is the
        # trace root (Tempo's span sets carry no parent id, so the root is
        # recognised by its name; a child named like the root passes).
        # A free-text search is a content search: the text usually sits on an
        # api/llm span, not the root, so the trace is kept whenever any span
        # matched.
        if f.roots_only and not f.free_text:
            filtered = []
            for t in traces:
                root_name = (t.get("rootTraceName") or "").strip()
                if not root_name:
                    filtered.append(t)
                    continue
                if f.name_prefix and not root_name.startswith(f.name_prefix):
                    continue
                span_sets = t.get("spanSets") or ([t["spanSet"]] if t.get("spanSet") else [])
                matched_root = any(
                    (sp.get("name") or "").strip() == root_name
                    for ss in span_sets
                    for sp in ss.get("spans") or []
                )
                if matched_root:
                    filtered.append(t)
            traces = filtered

        for t in traces:
            # Tempo's per-service stats carry the whole-trace span count.
            stats = t.get("serviceStats")
            if isinstance(stats, dict) and "spanCount" not in t:
                total = sum(
                    int((s or {}).get("spanCount") or 0)
                    for s in stats.values()
                    if isinstance(s, dict)
                )
                if total:
                    t["spanCount"] = total

        page = trace_page(strictly_older_traces(traces, f), limit)
        # Tempo's own extras (``metrics``) stay next to the page.
        for k, v in result.items():
            if k != "traces":
                page.setdefault(k, v)
        return page

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/traces/{trace_id}"
        data = _dedupe_spans(http_get_json(url, timeout=20.0))
        if not isinstance(data, dict):
            raise BackendError(404, f"Trace {trace_id} not found in Tempo", "not_found")
        n = sum(
            len(scope.get("spans") or [])
            for batch in data.get("batches") or []
            for scope in batch.get("scopeSpans") or []
        )
        if n == 0:
            raise BackendError(404, f"Trace {trace_id} not found in Tempo", "not_found")
        data["span_count"] = n
        data.setdefault("truncated", False)
        return data
