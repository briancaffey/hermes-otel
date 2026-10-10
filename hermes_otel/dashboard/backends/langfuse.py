"""Langfuse adapter — public REST API at ``/api/public/``.

Auth is HTTP Basic with ``public_key`` + ``secret_key`` (generate both
in the Langfuse UI under Settings → API Keys). Both are required;
there's no unauthenticated path for self-hosted Langfuse.

Langfuse models trace observations differently from OTel: a ``Trace``
has many ``Observation`` items of type ``SPAN``, ``GENERATION``,
``EVENT``, etc. We map ``GENERATION`` → the LLM call span shape the
rest of the plugin expects (model, provider, usage tokens) and every
other type to a generic internal span.

The trace list endpoint filters on ``name``, ``userId``, ``sessionId``,
``release``, ``version``, ``tags`` and a time window, and pages with
``page=``. Nothing else of the search bar reaches it: the model, tool and
status fields and the free text are declared unsupported in
``filter_support`` rather than silently dropped (#294); the minimum
duration is applied to the rows the list returned, fetching further pages
until the page is full or ``_MAX_PAGES`` is reached.

Search enrichment: the Langfuse list endpoint doesn't include
per-trace observations, so the trace list shows trace-level metadata
only (name, timestamp, latency, user/session). Full observation
attributes appear in the detail view.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from . import register
from .base import (
    BackendAdapter,
    BackendError,
    ConfigError,
    StructuredFilter,
    http_get_json,
    otlp_attrs_from_dict,
    otlp_status,
    parse_kv_tokens,
    resolve_env_or_literal,
    rewrite_host_for_docker,
    strictly_older_traces,
    trace_page,
)

_DEFAULT_LANGFUSE_PORT = 3000
# How far the client-side duration filter walks through the list.
_MAX_PAGES = 5
# The public API refuses ``limit`` above 100.
_MAX_PAGE_SIZE = 100
# Root-span attributes the card shows, read from the list item's metadata.
_CARD_ATTRIBUTE_KEYS = (
    "llm.model_name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "llm.provider",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "hermes.session_id",
    "hermes.turn.number",
    "tool.name",
)
# OpenInference span kinds the exporter sets, as Langfuse types them.
_OBSERVATION_TYPES = {"tool": "TOOL", "api": "GENERATION", "llm": "GENERATION", "agent": "AGENT"}

# Langfuse v4 in its default ``events_only`` mode has no ``/api/public/traces``
# (404 with a sentence about the mode, #246). What it keeps: the v2
# observations list (``/api/public/v2/observations``: one row per observation
# with ``traceId``, ``parentObservationId``, ``isRootObservation``, ``type``,
# ``name``, ``startTime``/``endTime``, ``latency`` in seconds, ``level``,
# ``sessionId``; no input/output/usage/metadata) and the v2 metrics API, which
# answers token totals, cost, observation count and the model per trace. The
# API is probed once per query URL; ``query_api: v3|v4`` on the entry pins it.
_API_CACHE: Dict[str, str] = {}
_V2_OBSERVATIONS = "/api/public/v2/observations"
_V2_METRICS = "/api/public/v2/metrics"
_V4_FILTER_SUPPORT = {
    "service": "none",
    "name": "client",  # ``name=`` is exact; a prefix is checked on the rows
    "model": "none",
    "session": "server",  # ``sessionId=``
    "tool": "none",
    "min_duration": "client",  # ``latency`` on the rows
    "status_error": "server",  # ``level=ERROR`` on the listed observation
    "status_ok": "none",
    "free_text": "none",
    "raw": "server",  # ``k=v`` query parameters of the v2 list
    "roots_only": "server",  # the root is the AGENT observation (``isRootObservation``)
}


def _iso_utc(ts_s: int) -> str:
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _span_name(obs: Dict[str, Any]) -> str:
    name = str(obs.get("name") or obs.get("type") or "")
    if obs.get("type") == "TOOL" and name and not name.startswith("tool."):
        return f"tool.{name}"
    return name


def _iso_to_ns(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return None


# Langfuse ObservationType → OTel span kind int. Everything maps to
# INTERNAL (1) — Langfuse's types are semantic rather than transport.
_OTLP_INTERNAL = 1


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


# ``GENERATION`` carries LLM-specific fields we want to surface as
# Otel-style attributes on the normalized shape.
def _obs_to_card_attrs(obs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": obs.get("name") or ""}
    t = obs.get("type")
    if isinstance(t, str):
        out["langfuse.type"] = t
    if obs.get("model"):
        out["llm.model_name"] = obs["model"]
    # Langfuse doesn't model "provider" as a distinct field — fall
    # through to modelParameters / metadata.
    params = obs.get("modelParameters")
    if isinstance(params, dict):
        for k, v in params.items():
            out[f"llm.parameters.{k}"] = v

    usage = obs.get("usage") or {}
    if isinstance(usage, dict):
        if usage.get("input") is not None:
            out["gen_ai.usage.input_tokens"] = usage["input"]
        if usage.get("output") is not None:
            out["gen_ai.usage.output_tokens"] = usage["output"]
        if usage.get("total") is not None:
            out["gen_ai.usage.total_tokens"] = usage["total"]

    # input / output come through as Langfuse JSON objects — serialize
    # as strings to line up with the way other backends render them.
    inp = obs.get("input")
    if inp is not None:
        out["input.value"] = _as_text(inp)
    outp = obs.get("output")
    if outp is not None:
        out["output.value"] = _as_text(outp)

    level = obs.get("level")
    if level:
        # Langfuse levels: DEFAULT, DEBUG, WARNING, ERROR. Map ERROR
        # into the shared ``status`` attribute used by the card.
        out["status"] = "error" if level == "ERROR" else "ok"
    return out


@register
class LangfuseAdapter(BackendAdapter):
    handles = frozenset({"langfuse"})
    query_lang_label = "Langfuse params (k=v k=v)"
    raw_placeholder = "userId=user_123 name=chat"
    filter_support = {
        "service": "none",
        "name": "client",  # the list has an exact ``name=``; a prefix is checked on the rows
        "model": "none",
        "session": "server",
        "tool": "none",
        "min_duration": "client",
        "status_error": "none",
        "status_ok": "none",
        "free_text": "none",
        "raw": "server",
        "roots_only": "none",  # the list is already one row per trace
    }

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__(cfg)
        # Langfuse self-hosted typically lives on :3000 (Next.js).
        endpoint = cfg.get("endpoint") or cfg.get("base_url") or ""
        parsed = _urlparse.urlparse(endpoint)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        port = parsed.port or cfg.get("query_port") or _DEFAULT_LANGFUSE_PORT
        self.query_url = f"{scheme}://{rewrite_host_for_docker(host)}:{port}"
        self.public_key = resolve_env_or_literal(cfg, "public_key", "public_key_env")
        self.secret_key = resolve_env_or_literal(cfg, "secret_key", "secret_key_env")
        self._project_id_cache: Optional[str] = None
        api = str(cfg.get("query_api") or "auto").strip().lower()
        self.query_api = api if api in ("v3", "v4") else "auto"

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
        base["query_api"] = (
            self.query_api if self.query_api != "auto" else _API_CACHE.get(self.query_url, "auto")
        )
        base["auth_required"] = not (self.public_key and self.secret_key)
        if base["auth_required"]:
            base["auth_hint"] = (
                "Langfuse requires public_key + secret_key on the "
                "langfuse backend entry. Generate both in the Langfuse "
                "UI under Settings → API Keys."
            )
        return base

    def _headers(self) -> Dict[str, str]:
        if not (self.public_key and self.secret_key):
            raise ConfigError(
                "Langfuse requires public_key + secret_key. Set them "
                "(or *_env variants) on the langfuse backend entry."
            )
        token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode("utf-8")).decode(
            "ascii"
        )
        return {"Authorization": f"Basic {token}"}

    # ── Filter translation ───────────────────────────────────────────

    def _raw_params(self, raw: Optional[str]) -> Dict[str, str]:
        return parse_kv_tokens(raw)

    def _api(self) -> str:
        """``v3`` (the trace endpoints answer) or ``v4`` (``events_only``: the
        trace list is a 404 naming the mode). Probed once per query URL; an
        unreachable or unauthenticated server is reported, not guessed."""
        if self.query_api != "auto":
            return self.query_api
        cached = _API_CACHE.get(self.query_url)
        if cached:
            return cached
        api = "v3"
        try:
            http_get_json(
                f"{self.query_url}/api/public/traces?limit=1", headers=self._headers(), timeout=10.0
            )
        except BackendError as exc:
            if exc.kind == "not_found" and "events_only" in str(exc.detail):
                api = "v4"
            elif exc.kind != "not_found":
                raise
        _API_CACHE[self.query_url] = api
        return api

    def filter_support_for(self, f: Optional[StructuredFilter] = None) -> Dict[str, str]:
        """The v2 observations list honours more of the search bar than the
        v3 trace list (session, roots, an error level) and less of the rest."""
        return dict(_V4_FILTER_SUPPORT) if self._api() == "v4" else {}

    def _v4_list(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = f"{self.query_url}{_V2_OBSERVATIONS}?" + _urlparse.urlencode(params, doseq=True)
        data = http_get_json(url, headers=self._headers(), timeout=15.0)
        items = data.get("data") if isinstance(data, dict) else None
        return [o for o in (items if isinstance(items, list) else []) if isinstance(o, dict)]

    def _v4_trace_facts(
        self, trace_ids: List[str], start_s: int, end_s: int
    ) -> Dict[str, Dict[str, Any]]:
        """Per trace, from the v2 metrics API: token totals, cost, observation
        count and the model (``providedModelName``), which the observation rows
        do not carry. One request per page; a failure of the metrics route
        leaves the cards without those numbers rather than failing the list."""
        if not trace_ids:
            return {}
        query = {
            "view": "observations",
            # children start after their root; give the window a margin
            "fromTimestamp": _iso_utc(max(0, int(start_s) - 60)),
            "toTimestamp": _iso_utc(int(end_s) + 3600),
            "metrics": [
                {"measure": "totalTokens", "aggregation": "sum"},
                {"measure": "inputTokens", "aggregation": "sum"},
                {"measure": "outputTokens", "aggregation": "sum"},
                {"measure": "totalCost", "aggregation": "sum"},
                {"measure": "count", "aggregation": "count"},
            ],
            "dimensions": [{"field": "traceId"}, {"field": "providedModelName"}],
            "filters": [
                {
                    "column": "traceId",
                    "operator": "any of",
                    "value": list(trace_ids),
                    "type": "stringOptions",
                }
            ],
            "orderBy": [{"field": "sum_totalTokens", "direction": "desc"}],
            "config": {"row_limit": max(100, len(trace_ids) * 8)},
        }
        url = f"{self.query_url}{_V2_METRICS}?query=" + _urlparse.quote(json.dumps(query))
        try:
            data = http_get_json(url, headers=self._headers(), timeout=20.0)
        except BackendError as exc:
            if exc.kind in ("auth", "config"):
                raise
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        rows = data.get("data") if isinstance(data, dict) else None
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not r.get("traceId"):
                continue
            facts = out.setdefault(
                str(r["traceId"]), {"tokens": 0, "input": 0, "output": 0, "cost": 0.0, "count": 0}
            )
            for key, field in (
                ("tokens", "sum_totalTokens"),
                ("input", "sum_inputTokens"),
                ("output", "sum_outputTokens"),
                ("count", "count_count"),
            ):
                v = r.get(field)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    facts[key] += int(v)
            cost = r.get("sum_totalCost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                facts["cost"] += float(cost)
            if r.get("providedModelName") and not facts.get("model"):
                facts["model"] = str(r["providedModelName"])
        return out

    @staticmethod
    def _facts_to_attrs(facts: Dict[str, Any]) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {}
        if facts.get("tokens"):
            attrs["gen_ai.usage.total_tokens"] = facts["tokens"]
            attrs["gen_ai.usage.input_tokens"] = facts.get("input", 0)
            attrs["gen_ai.usage.output_tokens"] = facts.get("output", 0)
        if facts.get("cost"):
            attrs["hermes.cost.usage"] = float(facts["cost"])
        if facts.get("model"):
            attrs["llm.model_name"] = facts["model"]
        return attrs

    def _search_v4(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        """The trace list from the v2 observations: root observations (the
        AGENT type, one per Hermes turn) in the window, or, widened by the kind
        filter, the typed observations with one card per trace. Token totals,
        cost, model and the span count join from the metrics API."""
        prefix = f.name_prefix or ""
        widened = bool(not f.roots_only and prefix)
        obs_type = _OBSERVATION_TYPES.get(prefix.rstrip(".")) if widened else "AGENT"
        want = int(limit) + 1
        end_s_eff = int(end_s)
        if f.before_ns:
            end_s_eff = min(end_s_eff, int(f.before_ns) // 1_000_000_000 + 1)
        session = f.attr_equals.get("hermes.session_id") or f.attr_equals.get("sessionId")
        rows: List[Dict[str, Any]] = []
        card_attrs: Dict[str, Dict[str, Any]] = {}
        seen: set = set()
        for page in range(1, _MAX_PAGES + 1):
            params: Dict[str, Any] = {
                "page": page,
                "limit": _MAX_PAGE_SIZE,
                "fromStartTime": _iso_utc(start_s),
                "toStartTime": _iso_utc(end_s_eff),
            }
            if obs_type:
                params["type"] = obs_type
            if f.name_regex:
                params["name"] = f.name_regex
            if session:
                params["sessionId"] = session
            if f.status == "error":
                params["level"] = "ERROR"
            raw = self._raw_params(f.raw)
            raw.pop("page", None)
            raw.pop("limit", None)
            params.update(raw)
            items = self._v4_list(params)
            for o in items:
                if not widened and o.get("isRootObservation") is False:
                    continue  # a nested AGENT (a sub-agent) is not a turn
                name = str(o.get("name") or "")
                card_name = f"tool.{name}" if o.get("type") == "TOOL" else name
                if prefix and not card_name.startswith(prefix):
                    continue
                trace_id = o.get("traceId")
                if not trace_id or trace_id in seen:
                    continue
                row = self._observation_row(o, trace_id, card_name, f)
                if row is None:
                    continue
                attrs: Dict[str, Any] = {"name": card_name}
                if o.get("sessionId"):
                    attrs["hermes.session_id"] = o["sessionId"]
                if o.get("type") == "TOOL":
                    attrs["tool.name"] = name
                if str(o.get("level") or "").upper() == "ERROR":
                    attrs["status"] = "error"
                seen.add(trace_id)
                rows.append(row)
                card_attrs[trace_id] = attrs
            if len(rows) >= want or len(items) < _MAX_PAGE_SIZE:
                break
        page_out = trace_page(strictly_older_traces(rows, f), limit)
        facts = self._v4_trace_facts(
            [t["traceID"] for t in page_out["traces"]], int(start_s), end_s_eff
        )
        for t in page_out["traces"]:
            attrs = dict(card_attrs.get(t["traceID"], {}))
            tf = facts.get(t["traceID"]) or {}
            attrs.update(self._facts_to_attrs(tf))
            if tf.get("count"):
                t["spanCount"] = int(tf["count"])
            t["spanSets"][0]["spans"][0]["attributes"] = otlp_attrs_from_dict(attrs)
        return page_out

    def _get_trace_v4(self, trace_id: str) -> Dict[str, Any]:
        """The trace's observations as spans. The v2 rows carry no input,
        output or per-observation usage; the trace's totals from the metrics
        API go on the root so the header can show them."""
        observations: List[Dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            items = self._v4_list({"traceId": trace_id, "limit": _MAX_PAGE_SIZE, "page": page})
            observations.extend(items)
            if len(items) < _MAX_PAGE_SIZE:
                break
        if not observations:
            raise BackendError(404, f"Trace {trace_id} not found in Langfuse", "not_found")
        spans_otlp: List[Dict[str, Any]] = []
        for obs in observations:
            attrs: Dict[str, Any] = {"name": _span_name(obs)}
            if isinstance(obs.get("type"), str):
                attrs["langfuse.type"] = obs["type"]
            if obs.get("type") == "TOOL" and obs.get("name"):
                attrs["tool.name"] = obs["name"]
            for key in ("sessionId", "userId", "environment", "version"):
                if obs.get(key):
                    attrs[f"langfuse.{key}"] = obs[key]
            if obs.get("sessionId"):
                attrs["hermes.session_id"] = obs["sessionId"]
            start_ns = _iso_to_ns(obs.get("startTime"))
            end_ns = _iso_to_ns(obs.get("endTime"))
            spans_otlp.append(
                {
                    "traceId": trace_id,
                    "spanId": obs.get("id"),
                    "parentSpanId": obs.get("parentObservationId") or None,
                    "name": _span_name(obs),
                    "kind": _OTLP_INTERNAL,
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "endTimeUnixNano": str(end_ns) if end_ns else "0",
                    "attributes": otlp_attrs_from_dict(attrs),
                    "status": otlp_status(
                        "error" if obs.get("level") == "ERROR" else "ok",
                        obs.get("statusMessage") or "",
                    ),
                }
            )
        span_ids = {sp["spanId"] for sp in spans_otlp}
        roots = [
            sp
            for sp in spans_otlp
            if not sp.get("parentSpanId") or sp["parentSpanId"] not in span_ids
        ]
        starts = [
            int(sp["startTimeUnixNano"]) for sp in spans_otlp if sp["startTimeUnixNano"] != "0"
        ]
        ends = [int(sp["endTimeUnixNano"]) for sp in spans_otlp if sp["endTimeUnixNano"] != "0"]
        if len(roots) == 1:
            root = roots[0]
            root["parentSpanId"] = None
            if starts and ends:
                facts = self._v4_trace_facts(
                    [trace_id], min(starts) // 1_000_000_000, max(ends) // 1_000_000_000
                )
                have = {a["key"] for a in root["attributes"]}
                extra = {
                    k: v
                    for k, v in self._facts_to_attrs(facts.get(trace_id) or {}).items()
                    if k not in have
                }
                root["attributes"].extend(otlp_attrs_from_dict(extra))
        else:
            synthetic_id = f"synthetic-{trace_id[:16]}"
            spans_otlp.insert(
                0,
                {
                    "traceId": trace_id,
                    "spanId": synthetic_id,
                    "parentSpanId": None,
                    "name": "trace",
                    "kind": _OTLP_INTERNAL,
                    "startTimeUnixNano": str(min(starts)) if starts else "0",
                    "endTimeUnixNano": str(max(ends)) if ends else "0",
                    "attributes": otlp_attrs_from_dict(
                        {
                            "langfuse.trace_id": trace_id,
                            "synthetic": True,
                            "synthetic.reason": (
                                "Langfuse holds no single root observation for this trace"
                            ),
                        }
                    ),
                    "status": otlp_status("ok"),
                },
            )
            for sp in spans_otlp[1:]:
                if not sp.get("parentSpanId") or sp["parentSpanId"] not in span_ids:
                    sp["parentSpanId"] = synthetic_id
        return {
            "batches": [
                {
                    "resource": {"attributes": otlp_attrs_from_dict({"service.name": "langfuse"})},
                    "scopeSpans": [{"spans": spans_otlp}],
                }
            ],
            "span_count": len(observations),
            "truncated": False,
        }

    def _list_params(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int, page: int = 1
    ) -> Dict[str, Any]:
        end_ns = int(end_s) * 1_000_000_000
        if f.before_ns:
            end_ns = min(end_ns, int(f.before_ns))
        params: Dict[str, Any] = {
            "fromTimestamp": _iso_utc(start_s),
            # Round up so the row the cursor was taken from is inside the
            # window; strictly_older_traces() then cuts exactly.
            "toTimestamp": _iso_utc(-(-end_ns // 1_000_000_000)),
            "limit": int(limit),
            "page": int(page),
        }
        # Langfuse supports these first-class: name, userId, sessionId,
        # release, version, tags (latter multi-value).
        for k in ("name", "userId", "sessionId", "release", "version"):
            v = f.attr_equals.get(k)
            if v:
                params[k] = v
        if f.attr_equals.get("hermes.session_id") and "sessionId" not in params:
            params["sessionId"] = f.attr_equals["hermes.session_id"]
        if f.name_regex and "name" not in params:
            # The native query field: Langfuse filters name by exact match.
            params["name"] = f.name_regex
        # The raw k=v pairs are the native escape hatch, but the page and the
        # page size stay the route's: ``page=`` would otherwise silently
        # replace the cursor.
        raw = self._raw_params(f.raw)
        raw.pop("page", None)
        raw.pop("limit", None)
        params.update(raw)
        return params

    # ── Public API ───────────────────────────────────────────────────

    def _list_rows(self, f: StructuredFilter, start_s: int, end_s: int, limit: int, page: int):
        params = self._list_params(f, start_s, end_s, limit, page)
        url = f"{self.query_url}/api/public/traces?" + _urlparse.urlencode(params, doseq=True)
        data = http_get_json(url, headers=self._headers(), timeout=15.0)
        raw_list = data.get("data") if isinstance(data, dict) else None
        return [t for t in (raw_list if isinstance(raw_list, list) else []) if isinstance(t, dict)]

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        if self._api() == "v4":
            return self._search_v4(f, start_s, end_s, limit)
        if not f.roots_only and f.name_prefix and not (f.raw or "").strip():
            return self._search_observations(f, start_s, end_s, limit)
        want = int(limit) + 1
        traces: List[Dict[str, Any]] = []
        # One page beyond the limit is enough for has_more unless rows are
        # filtered out here (duration, name prefix) or the page is capped
        # (the public API refuses more than 100); then walk further pages.
        page_size = min(want, _MAX_PAGE_SIZE)
        for page in range(1, _MAX_PAGES + 1):
            try:
                items = self._list_rows(f, start_s, end_s, page_size, page)
            except BackendError as exc:
                # Langfuse v4 in events_only mode answers 404 with a sentence
                # about the mode: that is a deployment choice, not an outage
                # (#246), so it is reported as configuration.
                if "events_only" in str(exc.detail):
                    raise ConfigError(
                        "This Langfuse runs v4 in events_only mode, which has no "
                        "/api/public/traces for the dashboard to query (#246). Use the "
                        "Live source, or a Langfuse with the tracing API enabled."
                    )
                raise
            for t in items:
                row = self._trace_row(t, f)
                if row is not None:
                    traces.append(row)
            if len(traces) >= want or len(items) < page_size:
                break
            if not (f.min_duration_ms or f.name_prefix) and page_size >= want:
                break
        return trace_page(strictly_older_traces(traces, f), limit)

    # The kind filter widens the search to every span: Langfuse keeps those as
    # observations, typed by the exporter's OpenInference kind. A ``tool.``
    # prefix is the TOOL type (Langfuse drops the prefix from the name),
    # ``api.`` / ``llm.`` are GENERATIONs with the name kept, ``agent`` is the
    # AGENT type; any other prefix is matched on the name alone (#246).
    def _search_observations(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        prefix = f.name_prefix or ""
        obs_type = _OBSERVATION_TYPES.get(prefix.rstrip(".")) if prefix else None
        want = int(limit) + 1
        rows: List[Dict[str, Any]] = []
        seen: set = set()
        end_s_eff = int(end_s)
        if f.before_ns:
            end_s_eff = min(end_s_eff, int(f.before_ns) // 1_000_000_000 + 1)
        for page in range(1, _MAX_PAGES + 1):
            params: Dict[str, Any] = {
                "page": page,
                "limit": _MAX_PAGE_SIZE,
                "fromStartTime": _iso_utc(start_s),
                "toStartTime": _iso_utc(end_s_eff),
            }
            if obs_type:
                params["type"] = obs_type
            url = f"{self.query_url}/api/public/observations?" + _urlparse.urlencode(params)
            data = http_get_json(url, headers=self._headers(), timeout=15.0)
            items = data.get("data") if isinstance(data, dict) else None
            items = [o for o in (items if isinstance(items, list) else []) if isinstance(o, dict)]
            for o in items:
                name = str(o.get("name") or "")
                card_name = f"tool.{name}" if o.get("type") == "TOOL" else name
                if not card_name.startswith(prefix):
                    continue
                trace_id = o.get("traceId")
                if not trace_id or trace_id in seen:
                    continue
                row = self._observation_row(o, trace_id, card_name, f)
                if row is None:
                    continue
                seen.add(trace_id)
                rows.append(row)
            if len(rows) >= want or len(items) < _MAX_PAGE_SIZE:
                break
        return trace_page(strictly_older_traces(rows, f), limit)

    def _observation_row(
        self, o: Dict[str, Any], trace_id: str, card_name: str, f: StructuredFilter
    ) -> Optional[Dict[str, Any]]:
        start_ns = _iso_to_ns(o.get("startTime"))
        latency = o.get("latency")
        duration_ms = (
            int(latency * 1000)
            if isinstance(latency, (int, float)) and not isinstance(latency, bool)
            else 0
        )
        if f.min_duration_ms and duration_ms < int(f.min_duration_ms):
            return None
        attrs: Dict[str, Any] = {"name": card_name}
        meta = o.get("metadata") if isinstance(o.get("metadata"), dict) else {}
        span_attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
        for k in _CARD_ATTRIBUTE_KEYS:
            if span_attrs.get(k) not in (None, ""):
                attrs[k] = span_attrs[k]
        if o.get("type") == "TOOL":
            attrs.setdefault("tool.name", o.get("name") or "")
        if o.get("model"):
            attrs.setdefault("llm.model_name", o["model"])
        for key, field_name in (("input.value", "input"), ("output.value", "output")):
            v = o.get(field_name)
            if v not in (None, "") and key not in attrs:
                attrs[key] = v if isinstance(v, str) else json.dumps(v)
        if str(o.get("level") or "").upper() == "ERROR":
            attrs["status"] = "error"
        return {
            "traceID": trace_id,
            "rootServiceName": "langfuse",
            "rootTraceName": card_name,
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "durationMs": duration_ms,
            "spanSets": [
                {
                    "spans": [
                        {
                            "spanID": o.get("id"),
                            "name": card_name,
                            "attributes": otlp_attrs_from_dict(attrs),
                        }
                    ],
                    "matched": 1,
                }
            ],
        }

    def _trace_row(self, t: Dict[str, Any], f: StructuredFilter) -> Optional[Dict[str, Any]]:
        trace_id = t.get("id")
        if not trace_id:
            return None
        name = t.get("name") or ""
        if f.name_prefix and not name.startswith(f.name_prefix):
            return None
        start_ns = _iso_to_ns(t.get("timestamp"))
        # The public API documents ``latency`` in seconds.
        latency = t.get("latency")
        duration_ms = 0
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            duration_ms = int(latency * 1000)
        if f.min_duration_ms and duration_ms < int(f.min_duration_ms):
            return None

        attrs: Dict[str, Any] = {"name": name}
        # The list item's ``metadata.attributes`` are the root span's
        # attributes (verified on Langfuse 3 against the 2026-10 image): the
        # model, the turn's token totals and the session id come from there,
        # so the card reads like the other backends' (#246).
        meta = t.get("metadata") if isinstance(t.get("metadata"), dict) else {}
        root_attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
        for k in _CARD_ATTRIBUTE_KEYS:
            if root_attrs.get(k) not in (None, ""):
                attrs[k] = root_attrs[k]
        for key, field_name in (("input.value", "input"), ("output.value", "output")):
            v = t.get(field_name)
            if v not in (None, "") and key not in attrs:
                attrs[key] = v if isinstance(v, str) else json.dumps(v)
        # The trace list carries the trace's total cost; the token counts of
        # the observations are summed on the root (above).
        total_cost = t.get("totalCost")
        if (
            isinstance(total_cost, (int, float))
            and not isinstance(total_cost, bool)
            and total_cost > 0
        ):
            attrs["hermes.cost.usage"] = float(total_cost)
        observations = t.get("observations")
        span_count = len(observations) if isinstance(observations, list) else None
        for k in ("userId", "sessionId", "release", "version"):
            if t.get(k):
                attrs[f"langfuse.{k}"] = t[k]
        if t.get("tags"):
            attrs["langfuse.tags"] = t["tags"]

        return {
            "traceID": trace_id,
            **({"spanCount": span_count} if span_count is not None else {}),
            "rootServiceName": "langfuse",
            "rootTraceName": name,
            "startTimeUnixNano": str(start_ns) if start_ns else "0",
            "durationMs": duration_ms,
            "spanSets": [
                {
                    "spans": [
                        {
                            # The list carries no observation ids; the trace id
                            # stands in (see the docs' shape notes).
                            "spanID": trace_id,
                            "name": name,
                            "attributes": otlp_attrs_from_dict(attrs),
                        }
                    ],
                    "matched": 1,
                }
            ],
        }

    def _project_id(self) -> Optional[str]:
        if self._project_id_cache:
            return self._project_id_cache
        try:
            data = http_get_json(
                f"{self.query_url}/api/public/projects", headers=self._headers(), timeout=10.0
            )
        except BackendError as exc:
            if exc.kind in ("auth", "config"):
                raise  # a wrong key is an error, not "no link"
            return None
        except Exception:
            return None
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict) and items[0].get("id"):
            self._project_id_cache = str(items[0]["id"])
        return self._project_id_cache

    def trace_url(self, trace_id: str) -> Optional[str]:
        pid = self._project_id()
        return f"{self.query_url}/project/{pid}/traces/{trace_id}" if pid else None

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        if self._api() == "v4":
            return self._get_trace_v4(trace_id)
        url = f"{self.query_url}/api/public/traces/{trace_id}"
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        if not isinstance(data, dict) or not data.get("id"):
            raise BackendError(404, f"Trace {trace_id} not found in Langfuse", "not_found")

        observations = [o for o in (data.get("observations") or []) if isinstance(o, dict)]
        spans_otlp: List[Dict[str, Any]] = []
        for obs in observations:
            attrs = _obs_to_card_attrs(obs)
            # Carry through any free-form metadata so it's still visible
            # in the attribute table.
            metadata = obs.get("metadata") or {}
            if isinstance(metadata, dict):
                for k, v in metadata.items():
                    attrs[f"metadata.{k}"] = v

            start_ns = _iso_to_ns(obs.get("startTime"))
            end_ns = _iso_to_ns(obs.get("endTime"))
            spans_otlp.append(
                {
                    "traceId": trace_id,
                    "spanId": obs.get("id"),
                    "parentSpanId": obs.get("parentObservationId") or None,
                    # Langfuse drops the exporter's ``tool.`` prefix from a
                    # TOOL observation's name; the span tree uses the name
                    # the exporter gave the span, as every other backend does.
                    "name": _span_name(obs),
                    "kind": _OTLP_INTERNAL,
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "endTimeUnixNano": str(end_ns) if end_ns else "0",
                    "attributes": otlp_attrs_from_dict(attrs),
                    "status": otlp_status(
                        "error" if obs.get("level") == "ERROR" else "ok",
                        obs.get("statusMessage") or "",
                    ),
                }
            )

        trace_attrs: Dict[str, Any] = {"langfuse.trace_id": trace_id}
        for k in ("userId", "sessionId", "release", "version"):
            if data.get(k):
                trace_attrs[f"langfuse.{k}"] = data[k]
        if data.get("input") is not None:
            trace_attrs["input.value"] = _as_text(data["input"])
        if data.get("output") is not None:
            trace_attrs["output.value"] = _as_text(data["output"])

        span_ids = {sp["spanId"] for sp in spans_otlp}
        roots = [
            sp
            for sp in spans_otlp
            if not sp.get("parentSpanId") or sp["parentSpanId"] not in span_ids
        ]
        if len(roots) == 1:
            # An OTLP-ingested trace keeps its real root observation (the
            # plugin's ``agent`` span): no synthetic node, the trace record's
            # facts join the root's attributes where the root has none.
            root = roots[0]
            have = {a["key"] for a in root["attributes"]}
            root["attributes"].extend(
                otlp_attrs_from_dict({k: v for k, v in trace_attrs.items() if k not in have})
            )
            root["parentSpanId"] = None
        else:
            # Several parentless observations (or none): a top-level node built
            # from the trace record holds the tree together. It is marked as
            # synthetic so the UI can label it and nobody mistakes it for a
            # span the agent emitted (#159); the plugin marks its own lazily
            # created roots the same way (``hermes.session.synthesized``).
            trace_start = _iso_to_ns(data.get("timestamp"))
            trace_end = max((_iso_to_ns(o.get("endTime")) or 0 for o in observations), default=0)
            root_attrs: Dict[str, Any] = {
                **trace_attrs,
                "synthetic": True,
                "synthetic.reason": (
                    "Langfuse holds no single root observation for this trace; "
                    "built from the trace record"
                ),
            }
            synthetic_id = f"synthetic-{trace_id[:16]}" if trace_id else "synthetic-root"
            spans_otlp.insert(
                0,
                {
                    "traceId": trace_id,
                    "spanId": synthetic_id,
                    "parentSpanId": None,
                    "name": data.get("name") or "trace",
                    "kind": _OTLP_INTERNAL,
                    "startTimeUnixNano": str(trace_start) if trace_start else "0",
                    "endTimeUnixNano": (
                        str(trace_end) if trace_end else (str(trace_start) if trace_start else "0")
                    ),
                    "attributes": otlp_attrs_from_dict(root_attrs),
                    "status": otlp_status("ok"),
                },
            )
            # Back-link orphan observations to the synthetic root so the
            # tree doesn't fall apart.
            span_id_set = {sp["spanId"] for sp in spans_otlp}
            for sp in spans_otlp[1:]:
                if not sp.get("parentSpanId") or sp["parentSpanId"] not in span_id_set:
                    sp["parentSpanId"] = synthetic_id

        resource_attrs = otlp_attrs_from_dict({"service.name": "langfuse"})
        return {
            "batches": [
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [{"spans": spans_otlp}],
                }
            ],
            "span_count": len(observations),
            "truncated": False,
        }
