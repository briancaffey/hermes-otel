"""Langfuse adapter — public REST API at ``/api/public/``.

Auth is HTTP Basic with ``public_key`` + ``secret_key`` (generate both
in the Langfuse UI under Settings → API Keys). Both are required;
there's no unauthenticated path for self-hosted Langfuse.

Langfuse models trace observations differently from OTel: a ``Trace``
has many ``Observation`` items of type ``SPAN``, ``GENERATION``,
``EVENT``, etc. We map ``GENERATION`` → the LLM call span shape the
rest of the plugin expects (model, provider, usage tokens) and every
other type to a generic internal span.

Search enrichment: the Langfuse list endpoint doesn't include
per-trace observations, so the trace list shows trace-level metadata
only (name, timestamp, latency, user/session). Full observation
attributes appear in the detail view.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import parse as _urlparse

from fastapi import HTTPException

from . import register
from .base import (
    BackendAdapter,
    StructuredFilter,
    http_get_json,
    otlp_attrs_from_dict,
    otlp_status,
    resolve_env_or_literal,
    rewrite_host_for_docker,
)

_DEFAULT_LANGFUSE_PORT = 3000


def _iso_utc(ts_s: int) -> str:
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


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
def _obs_kind_to_otlp(obs_type: Optional[str]) -> int:
    return 1


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
    import json

    inp = obs.get("input")
    if inp is not None:
        out["input.value"] = inp if isinstance(inp, str) else json.dumps(inp)
    outp = obs.get("output")
    if outp is not None:
        out["output.value"] = outp if isinstance(outp, str) else json.dumps(outp)

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

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["query_url"] = self.query_url
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
            raise HTTPException(
                status_code=502,
                detail=(
                    "Langfuse requires public_key + secret_key. Set them "
                    "(or *_env variants) on the langfuse backend entry."
                ),
            )
        token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode("utf-8")).decode(
            "ascii"
        )
        return {"Authorization": f"Basic {token}"}

    # ── Filter translation ───────────────────────────────────────────

    def _raw_params(self, raw: Optional[str]) -> Dict[str, str]:
        if not raw:
            return {}
        out: Dict[str, str] = {}
        for token in raw.split():
            if "=" in token:
                k, v = token.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip().strip("\"'")
        return out

    def _list_params(
        self, f: StructuredFilter, start_s: int, end_s: int, limit: int
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "fromTimestamp": _iso_utc(start_s),
            "toTimestamp": _iso_utc(end_s),
            "limit": int(limit),
        }
        # Langfuse supports these first-class: name, userId, sessionId,
        # release, version, tags (latter multi-value).
        for k in ("name", "userId", "sessionId", "release", "version"):
            v = f.attr_equals.get(k)
            if v:
                params[k] = v
        if f.free_text and "name" not in params:
            # Best-effort — Langfuse filters name with exact match only.
            params["name"] = f.free_text
        params.update(self._raw_params(f.raw))
        return params

    # ── Public API ───────────────────────────────────────────────────

    def search(self, f: StructuredFilter, start_s: int, end_s: int, limit: int) -> Dict[str, Any]:
        params = self._list_params(f, start_s, end_s, limit)
        url = f"{self.query_url}/api/public/traces?" + _urlparse.urlencode(params, doseq=True)
        data = http_get_json(url, headers=self._headers(), timeout=15.0)
        raw_list = data.get("data") if isinstance(data, dict) else None
        items = raw_list if isinstance(raw_list, list) else []

        traces: List[Dict[str, Any]] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            trace_id = t.get("id")
            if not trace_id:
                continue
            start_ns = _iso_to_ns(t.get("timestamp"))
            # Langfuse latency is in seconds (float) or ms depending on
            # version; treat anything < 10000 as seconds.
            latency = t.get("latency")
            duration_ms = 0
            if isinstance(latency, (int, float)):
                duration_ms = int(latency * 1000) if latency < 10000 else int(latency)
            if f.min_duration_ms and duration_ms < int(f.min_duration_ms):
                continue

            attrs: Dict[str, Any] = {"name": t.get("name") or ""}
            for k in ("userId", "sessionId", "release", "version"):
                if t.get(k):
                    attrs[f"langfuse.{k}"] = t[k]
            if t.get("tags"):
                attrs["langfuse.tags"] = t["tags"]

            traces.append(
                {
                    "traceID": trace_id,
                    "rootServiceName": "langfuse",
                    "rootTraceName": t.get("name") or "",
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "durationMs": duration_ms,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": trace_id,
                                    "name": t.get("name") or "",
                                    "attributes": otlp_attrs_from_dict(attrs),
                                }
                            ],
                            "matched": 1,
                        }
                    ],
                }
            )
        traces.sort(
            key=lambda t: int(t.get("startTimeUnixNano") or 0),
            reverse=True,
        )
        return {"traces": traces}

    def get_trace(self, trace_id: str) -> Dict[str, Any]:
        url = f"{self.query_url}/api/public/traces/{trace_id}"
        data = http_get_json(url, headers=self._headers(), timeout=20.0)
        if not isinstance(data, dict):
            return {"batches": []}

        observations = data.get("observations") or []
        spans_otlp: List[Dict[str, Any]] = []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
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
                    "name": obs.get("name") or obs.get("type") or "",
                    "kind": _obs_kind_to_otlp(obs.get("type")),
                    "startTimeUnixNano": str(start_ns) if start_ns else "0",
                    "endTimeUnixNano": str(end_ns) if end_ns else "0",
                    "attributes": otlp_attrs_from_dict(attrs),
                    "status": otlp_status(
                        "error" if obs.get("level") == "ERROR" else "ok",
                        obs.get("statusMessage") or "",
                    ),
                }
            )

        # Synthesise the root span from the trace itself so the tree has
        # a top-level node covering the whole turn.
        trace_start = _iso_to_ns(data.get("timestamp"))
        trace_end = max((_iso_to_ns(o.get("endTime")) or 0 for o in observations), default=0)
        root_attrs: Dict[str, Any] = {"langfuse.trace_id": trace_id}
        for k in ("userId", "sessionId", "release", "version"):
            if data.get(k):
                root_attrs[f"langfuse.{k}"] = data[k]
        if data.get("input") is not None:
            import json

            root_attrs["input.value"] = (
                data["input"] if isinstance(data["input"], str) else json.dumps(data["input"])
            )
        if data.get("output") is not None:
            import json

            root_attrs["output.value"] = (
                data["output"] if isinstance(data["output"], str) else json.dumps(data["output"])
            )
        spans_otlp.insert(
            0,
            {
                "traceId": trace_id,
                "spanId": trace_id[:16] if trace_id else "root",
                "parentSpanId": None,
                "name": data.get("name") or "trace",
                "kind": 1,
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
        synthetic_root_id = spans_otlp[0]["spanId"]
        span_id_set = {sp["spanId"] for sp in spans_otlp}
        for sp in spans_otlp[1:]:
            if not sp.get("parentSpanId") or sp["parentSpanId"] not in span_id_set:
                sp["parentSpanId"] = synthetic_root_id

        resource_attrs = otlp_attrs_from_dict({"service.name": "langfuse"})
        return {
            "batches": [
                {
                    "resource": {"attributes": resource_attrs},
                    "scopeSpans": [{"spans": spans_otlp}],
                }
            ]
        }
