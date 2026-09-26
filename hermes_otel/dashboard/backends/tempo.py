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

from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import _loki, _prometheus, register
from .base import (
    BackendAdapter,
    HTTPException,
    LogFilter,
    StructuredFilter,
    http_get_json,
    rewrite_host_for_docker,
)

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
            raise HTTPException(
                status_code=503, detail="This backend has no prometheus_url configured"
            )
        return self.prometheus_url

    def _loki(self) -> str:
        if not self.loki_url:
            raise HTTPException(status_code=503, detail="This backend has no loki_url configured")
        return self.loki_url

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
