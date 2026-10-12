"""Write ``config.json`` and ``routes.json`` for every adapter (#408).

The response bodies are the shapes the per-adapter unit tests recorded
against real backends (each test module's docstring names the image and
date), re-based onto one shared turn so every adapter answers the same
scenario list: a root ``agent`` span at ``T0`` with one ``tool.terminal``
child and one failed ``tool.read_file`` child, plus an older turn ten
minutes before it. Live captures through ``HERMES_OTEL_RECORD_HTTP`` can
replace a body here without changing the scenario list.

Run: ``uv run python tests/golden/adapters/seed_responses.py`` then record
the answers with ``UPDATE_GOLDEN=1 uv run --extra dev pytest
tests/unit/test_dashboard_goldens.py``.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent

NS = 1_000_000_000
T0 = 1_791_750_000  # 2026-10-11T20:20:00Z
TRACE = "ab43116e1d6d4a1a449db547c36ca808"
OLDER = "5e0ef1f1f6ab4b5eb8c5c7e6a0d3c2b1"
MISSING = "0" * 32
SESSION = "20261011_201958_a1b2c3"
MODEL = "nvidia/nemotron-3-super-120b-a12b"
SERVICE = "hermes-agent"
INSTANCE = "f26a6d1e-4c7b-4d9a-9c3e-1b2a3c4d5e6f"

ROOT_ID = "65a98a08103c9da7"
TOOL_ID = "aaaa000000000001"
FAIL_ID = "aaaa000000000002"
OLD_ROOT_ID = "bbbb000000000001"

ROOT_DUR_NS = 4_029_000_000
TOOL_START_NS = T0 * NS + 1_500_000_000
TOOL_DUR_NS = 1_120_770_000
FAIL_START_NS = T0 * NS + 3_000_000_000
FAIL_DUR_NS = 3_595_000
OLD_T = T0 - 600


def root_status(trace: str) -> int:
    """The older turn ended in error, so a roots-only ``status=error`` search
    has exactly one root to find; the main turn fails only in a child."""
    return 2 if trace == OLDER else 1


ROOT_ATTRS = {
    "hermes.session_id": SESSION,
    "session.id": SESSION,
    "gen_ai.usage.input_tokens": 23504,
    "gen_ai.usage.output_tokens": 186,
    "gen_ai.usage.total_tokens": 23690,
    "llm.model_name": MODEL,
    "hermes.turn.number": 1,
    "hermes.session.completed": True,
    "input.value": "read the file hermes_otel.yaml and say hello",
    "output.value": "Here is the file.",
}
TOOL_ATTRS = {"tool.name": "terminal", "openinference.span.kind": "TOOL"}
FAIL_ATTRS = {"tool.name": "read_file", "openinference.span.kind": "TOOL"}


def iso(ns: int, digits: int = 3, suffix: str = "Z") -> str:
    d = _dt.datetime.fromtimestamp(ns // NS, _dt.timezone.utc)
    frac = ns % NS
    base = d.strftime("%Y-%m-%dT%H:%M:%S")
    if digits:
        base += "." + f"{frac:09d}"[:digits]
    return base + suffix


def write(backend_type: str, config: Dict[str, Any], routes: List[Dict[str, Any]]) -> None:
    d = ROOT / backend_type
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(config, indent=1, sort_keys=True) + "\n")
    (d / "routes.json").write_text(json.dumps(routes, indent=1) + "\n")
    print(f"{backend_type}: {len(routes)} routes")


def base_config(entry: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    cfg = {
        "entry": entry,
        "t0": T0,
        "trace_id": TRACE,
        "session_id": SESSION,
        "model": MODEL,
        "counter": "hermes_token_usage_total",
        "gauge": "system_cpu_utilization",
        "group_by": "model",
    }
    cfg.update(extra)
    return cfg


# ── OTLP JSON (Tempo detail, Aspire, Jaeger v3) ───────────────────────


def otlp_attr(k: str, v: Any) -> Dict[str, Any]:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)}}


def otlp_span(
    trace: str, sid: str, name: str, start_ns: int, dur_ns: int, parent=None, status=1, attrs=None
) -> Dict[str, Any]:
    sp = {
        "traceId": trace,
        "spanId": sid,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + dur_ns),
        "attributes": [otlp_attr(k, v) for k, v in (attrs or {}).items()],
        "status": {"message": "No such file" if status == 2 else "", "code": status},
    }
    if parent:
        sp["parentSpanId"] = parent
    return sp


def otlp_resource() -> Dict[str, Any]:
    return {
        "attributes": [
            otlp_attr("service.name", SERVICE),
            otlp_attr("service.instance.id", INSTANCE),
            otlp_attr("hermes.profile", "default"),
        ]
    }


def turn_otlp(trace: str = TRACE, root_id: str = ROOT_ID, t: int = T0) -> List[Dict[str, Any]]:
    """The three spans of the main turn in OTLP JSON."""
    shift = (t - T0) * NS
    return [
        otlp_span(
            trace,
            root_id,
            "agent",
            T0 * NS + shift,
            ROOT_DUR_NS,
            None,
            root_status(trace),
            ROOT_ATTRS,
        ),
        otlp_span(
            trace,
            TOOL_ID,
            "tool.terminal",
            TOOL_START_NS + shift,
            TOOL_DUR_NS,
            root_id,
            1,
            TOOL_ATTRS,
        ),
        otlp_span(
            trace,
            FAIL_ID,
            "tool.read_file",
            FAIL_START_NS + shift,
            FAIL_DUR_NS,
            root_id,
            2,
            FAIL_ATTRS,
        ),
    ]


def otlp_batches(spans: List[Dict[str, Any]], key: str = "scopeSpans") -> Dict[str, Any]:
    return {
        "batches": [
            {
                "resource": otlp_resource(),
                key: [{"scope": {"name": "hermes-otel-plugin"}, "spans": spans}],
            }
        ]
    }


# ── Prometheus / Loki (LGTM, Gigapipe, Victoria, GreptimeDB names) ────


def prom_names() -> Dict[str, Any]:
    return {
        "status": "success",
        "data": [
            "hermes_token_usage_total",
            "hermes_message_count_total",
            "hermes_tool_duration_milliseconds_bucket",
            "hermes_tool_duration_milliseconds_sum",
            "hermes_tool_duration_milliseconds_count",
            "system_cpu_utilization",
            "up",
        ],
    }


def prom_counter_matrix(label: str = "model", inst_label: str = "service_instance_id"):
    """Raw cumulative samples, one series per process: a gateway that grows
    by 5 inside the window, and a one-shot run born inside it."""
    s = T0 - 3600
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "hermes_token_usage_total",
                        label: "nvidia/nemotron-3-nano-omni",
                        inst_label: "gw",
                    },
                    "values": [[s - 60, "100"], [s + 30, "100"], [s + 90, "105"]],
                },
                {
                    "metric": {
                        "__name__": "hermes_token_usage_total",
                        label: MODEL,
                        inst_label: "one-shot",
                    },
                    "values": [[T0 - 300, "23690"], [T0 - 240, "23690"]],
                },
            ],
        },
    }


def prom_gauge_matrix() -> Dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": "system_cpu_utilization", "cpu_mode": "user"},
                    "values": [[T0 - 3000, "0.5"], [T0 - 600, "0.25"], [T0, "0.3"]],
                }
            ],
        },
    }


def loki_streams() -> Dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {
                        "scope_name": "hermes_otel",
                        "severity_text": "INFO",
                        "severity_number": "9",
                        "hermes_session_id": SESSION,
                        "service_name": SERVICE,
                        "trace_id": TRACE,
                        "span_id": ROOT_ID,
                    },
                    "values": [
                        [str(T0 * NS + 4_100_000_000), f"[hermes-otel] session {SESSION} finalized"]
                    ],
                },
                {
                    "stream": {
                        "scope_name": "cli",
                        "detected_level": "warn",
                        "severity_text": "WARN",
                        "severity_number": "13",
                        "trace_id": TRACE,
                    },
                    "values": [
                        [str(T0 * NS + 2_000_000_000), "careful: the output was truncated"],
                        [str(T0 * NS - 300 * NS), "older line"],
                    ],
                },
            ],
        },
    }


def loki_loggers() -> Dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"scope_name": "cli"}, "value": [T0, "2"]},
                {"metric": {"scope_name": "hermes_otel"}, "value": [T0, "6"]},
                {"metric": {}, "value": [T0, "9"]},
            ],
        },
    }


# ── Jaeger v1 (Jaeger, GreptimeDB, Victoria) ──────────────────────────


def jaeger_tag(k: str, v: Any) -> Dict[str, Any]:
    if isinstance(v, bool):
        return {"key": k, "type": "bool", "value": v}
    if isinstance(v, int):
        return {"key": k, "type": "int64", "value": v}
    return {"key": k, "type": "string", "value": str(v)}


def jaeger_doc(trace: str, root_id: str, t: int = T0, stringy: bool = False) -> Dict[str, Any]:
    def tag(k, v):
        return jaeger_tag(
            k, str(v).lower() if stringy and isinstance(v, bool) else (str(v) if stringy else v)
        )

    shift_us = (t - T0) * 1_000_000

    def span(sid, name, start_ns, dur_ns, parent, attrs, error=False):
        tags = [tag(k, v) for k, v in attrs.items()]
        tags.append(tag("span.kind", "internal"))
        if error:
            tags += [
                tag("otel.status_code", "ERROR"),
                tag("otel.status_description", "No such file"),
            ]
            tags.append(tag("error", "true" if stringy else True))
        else:
            tags.append(tag("error", "false" if stringy else False))
        return {
            "traceID": trace,
            "spanID": sid,
            "operationName": name,
            "startTime": start_ns // 1000 + shift_us,
            "duration": dur_ns // 1000,
            "references": (
                [{"refType": "CHILD_OF", "traceID": trace, "spanID": parent}] if parent else []
            ),
            "tags": tags,
            "logs": [],
            "processID": "p1",
        }

    return {
        "traceID": trace,
        "spans": [
            span(TOOL_ID, "tool.terminal", TOOL_START_NS, TOOL_DUR_NS, root_id, TOOL_ATTRS),
            span(FAIL_ID, "tool.read_file", FAIL_START_NS, FAIL_DUR_NS, root_id, FAIL_ATTRS, True),
            span(root_id, "agent", T0 * NS, ROOT_DUR_NS, None, ROOT_ATTRS, root_status(trace) == 2),
        ],
        "processes": {
            "p1": {"serviceName": SERVICE, "tags": [jaeger_tag("hermes.profile", "default")]}
        },
        "warnings": None,
    }


def jaeger_routes(prefix: str, stringy: bool = False, probe: bool = True) -> List[Dict[str, Any]]:
    routes: List[Dict[str, Any]] = []
    if probe:
        routes.append(
            {
                "method": "GET",
                "path": f"{prefix}/api/services",
                "json": {"data": [SERVICE, "jaeger-all-in-one"]},
            }
        )
    routes += [
        {
            "method": "GET",
            "path": f"{prefix}/api/traces/{MISSING}",
            "json": {"data": [], "errors": None},
        },
        {
            "method": "GET",
            "path": f"{prefix}/api/traces/{TRACE}",
            "json": {"data": [jaeger_doc(TRACE, ROOT_ID, stringy=stringy)], "errors": None},
        },
        {
            "method": "GET",
            "path": f"{prefix}/api/traces/{OLDER}",
            "json": {"data": [jaeger_doc(OLDER, OLD_ROOT_ID, OLD_T, stringy)], "errors": None},
        },
        {
            "method": "GET",
            "path": f"{prefix}/api/traces",
            "json": {
                "data": [
                    jaeger_doc(OLDER, OLD_ROOT_ID, OLD_T, stringy),
                    jaeger_doc(TRACE, ROOT_ID, stringy=stringy),
                ],
                "errors": None,
                "limit": 0,
                "offset": 0,
                "total": 2,
            },
        },
    ]
    return routes


# ── the adapters ──────────────────────────────────────────────────────


def seed_jaeger() -> None:
    write(
        "jaeger",
        base_config(
            {"type": "jaeger", "endpoint": "http://localhost:16686"}, metrics=False, logs=False
        ),
        jaeger_routes(""),
    )


def seed_zipkin() -> None:
    def span(trace, sid, name, start_ns, dur_ns, parent, tags, error=False, kind=None):
        sp = {
            "traceId": trace,
            "id": sid,
            "name": name,
            "timestamp": start_ns // 1000,
            "duration": dur_ns // 1000,
            "localEndpoint": {"serviceName": SERVICE},
            "tags": {
                "otel.status_code": "ERROR" if error else "OK",
                "otel.scope.name": "hermes-otel-plugin",
                **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in tags.items()},
            },
        }
        if error:
            sp["tags"]["error"] = "No such file: nope.txt"
        if parent:
            sp["parentId"] = parent
        if kind:
            sp["kind"] = kind
        return sp

    def turn(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return [
            span(
                trace,
                TOOL_ID,
                "tool.terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                TOOL_ATTRS,
                kind="CLIENT",
            ),
            span(
                trace,
                FAIL_ID,
                "tool.read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                FAIL_ATTRS,
                True,
            ),
            span(
                trace,
                root_id,
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                None,
                ROOT_ATTRS,
                root_status(trace) == 2,
            ),
        ]

    write(
        "zipkin",
        base_config(
            {"type": "zipkin", "endpoint": "http://localhost:9411"}, metrics=False, logs=False
        ),
        [
            {"method": "GET", "path": f"/api/v2/trace/{MISSING}", "status": 404, "text": ""},
            {"method": "GET", "path": f"/api/v2/trace/{TRACE}", "json": turn(TRACE, ROOT_ID)},
            {
                "method": "GET",
                "path": "/api/v2/traces",
                "json": [turn(TRACE, ROOT_ID), turn(OLDER, OLD_ROOT_ID, OLD_T)],
            },
        ],
    )


def tempo_search_row(trace, root_id, t=T0, attrs=None, name="agent"):
    shift = (t - T0) * NS
    return {
        "traceID": trace.lstrip("0") or "0",
        "rootServiceName": SERVICE,
        "rootTraceName": "agent",
        "startTimeUnixNano": str(T0 * NS + shift),
        "durationMs": ROOT_DUR_NS // 1_000_000,
        "serviceStats": {SERVICE: {"spanCount": 3, "errorCount": 1}},
        "spanSets": [
            {
                "spans": [
                    {
                        "spanID": root_id,
                        "name": name,
                        "startTimeUnixNano": str(T0 * NS + shift),
                        "durationNanos": str(ROOT_DUR_NS),
                        "attributes": [
                            otlp_attr(k, v)
                            for k, v in (attrs if attrs is not None else ROOT_ATTRS).items()
                        ],
                    }
                ],
                "matched": 1,
            }
        ],
    }


def seed_tempo() -> None:
    write(
        "lgtm",
        base_config({"type": "lgtm", "endpoint": "http://localhost:4318/v1/traces"}),
        [
            {
                "method": "GET",
                "path": f"/api/traces/{MISSING}",
                "status": 404,
                "text": "trace not found",
            },
            {"method": "GET", "path": f"/api/traces/{TRACE}", "json": otlp_batches(turn_otlp())},
            {
                "method": "GET",
                "path": "/api/search",
                "query_contains": "tool.",
                "json": {
                    "traces": [
                        tempo_search_row(
                            TRACE,
                            TOOL_ID,
                            attrs={**TOOL_ATTRS, "name": "tool.terminal"},
                            name="tool.terminal",
                        ),
                        tempo_search_row(
                            OLDER,
                            TOOL_ID,
                            OLD_T,
                            attrs={**TOOL_ATTRS, "name": "tool.terminal"},
                            name="tool.terminal",
                        ),
                    ],
                    "metrics": {"inspectedTraces": 2, "inspectedBytes": "120394"},
                },
            },
            {
                "method": "GET",
                "path": "/api/search",
                "json": {
                    "traces": [
                        tempo_search_row(TRACE, ROOT_ID),
                        tempo_search_row(OLDER, OLD_ROOT_ID, OLD_T),
                    ],
                    "metrics": {"inspectedTraces": 2, "inspectedBytes": "120394"},
                },
            },
            {"method": "GET", "path": "/api/v1/label/__name__/values", "json": prom_names()},
            {"method": "GET", "path": "/api/v1/query", "json": prom_counter_matrix()},
            {"method": "GET", "path": "/api/v1/query_range", "json": prom_gauge_matrix()},
            {"method": "GET", "path": "/loki/api/v1/query_range", "json": loki_streams()},
            {"method": "GET", "path": "/loki/api/v1/query", "json": loki_loggers()},
        ],
    )


def seed_gigapipe() -> None:
    def raw_span(trace, sid, name, start_ns, dur_ns, parent=None, status=1, attrs=None):
        sp = {
            "traceID": trace.upper(),
            "traceId": trace,
            "spanID": sid.upper(),
            "spanId": sid,
            "name": name,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(start_ns + dur_ns),
            "serviceName": SERVICE,
            "attributes": [
                {"key": k, "value": {"stringValue": str(v)}} for k, v in (attrs or {}).items()
            ],
            "events": [],
            "status": {"code": status},
        }
        if parent:
            sp["parentSpanId"] = parent
        return sp

    def detail(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": SERVICE}}]
                    },
                    "instrumentationLibrarySpans": [
                        {
                            "spans": [
                                raw_span(
                                    trace,
                                    TOOL_ID,
                                    "tool.terminal",
                                    TOOL_START_NS + shift,
                                    TOOL_DUR_NS,
                                    root_id,
                                    1,
                                    TOOL_ATTRS,
                                ),
                                raw_span(
                                    trace,
                                    FAIL_ID,
                                    "tool.read_file",
                                    FAIL_START_NS + shift,
                                    FAIL_DUR_NS,
                                    root_id,
                                    2,
                                    FAIL_ATTRS,
                                ),
                                raw_span(
                                    trace,
                                    root_id,
                                    "agent",
                                    T0 * NS + shift,
                                    ROOT_DUR_NS,
                                    None,
                                    root_status(trace),
                                    ROOT_ATTRS,
                                ),
                            ]
                        }
                    ],
                }
            ]
        }

    def search_row(trace, root_id, t=T0):
        shift = (t - T0) * NS
        span = {
            "spanID": root_id,
            "startTimeUnixNano": str(T0 * NS + shift),
            "durationNanos": str(ROOT_DUR_NS),
            "attributes": [],
        }
        return {
            "traceID": trace,
            "rootServiceName": SERVICE,
            "rootTraceName": "agent",
            "startTimeUnixNano": str(T0 * NS + shift),
            "durationMs": ROOT_DUR_NS // 1_000_000,
            "spanSet": {"spans": [span], "matched": 1},
            "spanSets": [{"spans": [span], "matched": 1}],
        }

    streams = loki_streams()
    for s in streams["data"]["result"]:
        s["stream"] = {
            "service_name": SERVICE,
            "level": s["stream"].get("severity_text", "INFO"),
            "trace_id": TRACE,
            "span_id": ROOT_ID,
            "hermes_session_id": SESSION,
            "hermes_log_attribution": "single_session",
        }
    write(
        "gigapipe",
        base_config({"type": "gigapipe", "endpoint": "http://localhost:3110"}),
        [
            {"method": "GET", "path": f"/api/traces/{MISSING}", "status": 404, "text": "not found"},
            {"method": "GET", "path": f"/api/traces/{TRACE}", "json": detail(TRACE, ROOT_ID)},
            {
                "method": "GET",
                "path": f"/api/traces/{OLDER}",
                "json": detail(OLDER, OLD_ROOT_ID, OLD_T),
            },
            {
                "method": "GET",
                "path": "/api/search",
                "json": {
                    "traces": [search_row(TRACE, ROOT_ID), search_row(OLDER, OLD_ROOT_ID, OLD_T)]
                },
            },
            {"method": "GET", "path": "/api/v1/label/__name__/values", "json": prom_names()},
            {
                "method": "GET",
                "path": "/api/v1/query_range",
                "query_contains": "hermes_token",
                "json": prom_counter_matrix(inst_label="instance"),
            },
            {"method": "GET", "path": "/api/v1/query_range", "json": prom_gauge_matrix()},
            {"method": "GET", "path": "/loki/api/v1/query_range", "json": streams},
            {"method": "GET", "path": "/loki/api/v1/query", "json": loki_loggers()},
        ],
    )


def seed_phoenix() -> None:
    def node(trace, sid, name, start_ns, dur_ns, parent, status, attrs):
        return {
            "spanId": sid,
            "name": name,
            "latencyMs": dur_ns / 1_000_000,
            "statusCode": "ERROR" if status == 2 else "OK",
            "statusMessage": "No such file" if status == 2 else "",
            "startTime": iso(start_ns, 6, "+00:00"),
            "endTime": iso(start_ns + dur_ns, 6, "+00:00"),
            "parentId": parent,
            "spanKind": "tool" if name.startswith("tool.") else "agent",
            "attributes": json.dumps(nest(attrs)),
            "context": {"traceId": trace, "spanId": sid},
            "trace": {"numSpans": 3},
            "input": {"value": attrs.get("input.value"), "mimeType": "text/plain"},
            "output": {"value": attrs.get("output.value"), "mimeType": "text/plain"},
            "tokenCountTotal": attrs.get("gen_ai.usage.total_tokens"),
            "tokenCountPrompt": attrs.get("gen_ai.usage.input_tokens"),
            "tokenCountCompletion": attrs.get("gen_ai.usage.output_tokens"),
        }

    def nest(flat):
        out: Dict[str, Any] = {}
        for k, v in flat.items():
            cur = out
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v
        return out

    def turn(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return [
            node(
                trace,
                root_id,
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                None,
                root_status(trace),
                ROOT_ATTRS,
            ),
            node(
                trace,
                TOOL_ID,
                "tool.terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                1,
                TOOL_ATTRS,
            ),
            node(
                trace,
                FAIL_ID,
                "tool.read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                2,
                FAIL_ATTRS,
            ),
        ]

    def edges(nodes):
        return {"edges": [{"node": n} for n in nodes], "pageInfo": {"hasNextPage": False}}

    roots = [turn(TRACE, ROOT_ID)[0], turn(OLDER, OLD_ROOT_ID, OLD_T)[0]]
    tools = [turn(TRACE, ROOT_ID)[1], turn(TRACE, ROOT_ID)[2], turn(OLDER, OLD_ROOT_ID, OLD_T)[1]]
    write(
        "phoenix",
        base_config(
            {"type": "phoenix", "endpoint": "http://localhost:6006/v1/traces"},
            metrics=False,
            logs=False,
        ),
        [
            {
                "method": "POST",
                "path": "/graphql",
                "body_contains": "projects(",
                "json": {
                    "data": {
                        "projects": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "UHJvamVjdDox",
                                        "name": "default",
                                        "hasTraces": False,
                                    }
                                },
                                {
                                    "node": {
                                        "id": "UHJvamVjdDoy",
                                        "name": "hermes-minimal",
                                        "hasTraces": True,
                                    }
                                },
                            ]
                        }
                    }
                },
            },
            {
                "method": "POST",
                "path": "/graphql",
                "body_contains": "__type",
                "json": {
                    "data": {
                        "__type": {
                            "fields": [
                                {
                                    "name": "spans",
                                    "args": [
                                        {"name": a}
                                        for a in (
                                            "first",
                                            "timeRange",
                                            "filterCondition",
                                            "sort",
                                            "traceFilterCondition",
                                        )
                                    ],
                                }
                            ]
                        }
                    }
                },
            },
            {
                "method": "POST",
                "path": "/graphql",
                "body_contains": ["trace(traceId", MISSING],
                "json": {"data": {"node": {"name": "hermes-minimal", "trace": None}}},
            },
            {
                "method": "POST",
                "path": "/graphql",
                "body_contains": "trace(traceId",
                "json": {
                    "data": {
                        "node": {
                            "name": "hermes-minimal",
                            "trace": {
                                "traceId": TRACE,
                                "numSpans": 3,
                                "spans": edges(turn(TRACE, ROOT_ID)),
                            },
                        }
                    }
                },
            },
            {
                "method": "POST",
                "path": "/graphql",
                "body_contains": "'tool.' in name",
                "json": {"data": {"node": {"name": "hermes-minimal", "spans": edges(tools)}}},
            },
            {
                "method": "POST",
                "path": "/graphql",
                "body_contains": "spans(",
                "json": {"data": {"node": {"name": "hermes-minimal", "spans": edges(roots)}}},
            },
        ],
    )


def seed_langfuse() -> None:
    def item(trace, t=T0):
        shift = (t - T0) * NS
        return {
            "id": trace,
            "name": "agent",
            "timestamp": iso(T0 * NS + shift),
            "latency": ROOT_DUR_NS / NS,
            "totalCost": 0.0,
            "observations": [ROOT_ID, TOOL_ID, FAIL_ID],
            "input": ROOT_ATTRS["input.value"],
            "output": ROOT_ATTRS["output.value"],
            "sessionId": SESSION,
            "tags": [],
            "metadata": {
                "attributes": {
                    k: str(v)
                    for k, v in ROOT_ATTRS.items()
                    if k not in ("input.value", "output.value")
                }
            },
        }

    def obs(trace, oid, typ, name, start_ns, dur_ns, parent=None, level="DEFAULT", attrs=None):
        return {
            "id": oid,
            "traceId": trace,
            "type": typ,
            "name": name,
            "startTime": iso(start_ns),
            "endTime": iso(start_ns + dur_ns),
            "latency": dur_ns / NS,
            "level": level,
            "statusMessage": "No such file" if level == "ERROR" else None,
            "parentObservationId": parent,
            "input": {"command": "ls"} if typ == "TOOL" else None,
            "output": "hi" if typ == "TOOL" else None,
            "metadata": {"attributes": {k: str(v) for k, v in (attrs or {}).items()}},
        }

    def detail(trace, root_id, t=T0):
        shift = (t - T0) * NS
        d = item(trace, t)
        d["observations"] = [
            obs(
                trace,
                root_id,
                "AGENT",
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                level="ERROR" if root_status(trace) == 2 else "DEFAULT",
                attrs=ROOT_ATTRS,
            ),
            obs(
                trace,
                TOOL_ID,
                "TOOL",
                "terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                attrs=TOOL_ATTRS,
            ),
            obs(
                trace,
                FAIL_ID,
                "TOOL",
                "read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                "ERROR",
                FAIL_ATTRS,
            ),
        ]
        return d

    write(
        "langfuse",
        base_config(
            {
                "type": "langfuse",
                "endpoint": "http://localhost:3000",
                "public_key": "pk-test",
                "secret_key": "sk-test",
            },
            metrics=False,
            logs=False,
        ),
        [
            {
                "method": "GET",
                "path": f"/api/public/traces/{MISSING}",
                "status": 404,
                "text": '{"message":"Trace not found"}',
            },
            {
                "method": "GET",
                "path": f"/api/public/traces/{TRACE}",
                "json": detail(TRACE, ROOT_ID),
            },
            {
                "method": "GET",
                "path": "/api/public/traces",
                "json": {
                    "data": [item(TRACE), item(OLDER, OLD_T)],
                    "meta": {"page": 1, "limit": 100, "totalItems": 2, "totalPages": 1},
                },
            },
            {
                "method": "GET",
                "path": "/api/public/observations",
                "json": {
                    "data": [
                        obs(
                            TRACE,
                            TOOL_ID,
                            "TOOL",
                            "terminal",
                            TOOL_START_NS,
                            TOOL_DUR_NS,
                            ROOT_ID,
                            attrs=TOOL_ATTRS,
                        ),
                        obs(
                            TRACE,
                            FAIL_ID,
                            "TOOL",
                            "read_file",
                            FAIL_START_NS,
                            FAIL_DUR_NS,
                            ROOT_ID,
                            "ERROR",
                            FAIL_ATTRS,
                        ),
                        obs(
                            OLDER,
                            TOOL_ID,
                            "TOOL",
                            "terminal",
                            TOOL_START_NS - 600 * NS,
                            TOOL_DUR_NS,
                            OLD_ROOT_ID,
                            attrs=TOOL_ATTRS,
                        ),
                    ],
                    "meta": {"page": 1, "limit": 100, "totalItems": 3, "totalPages": 1},
                },
            },
            {
                "method": "GET",
                "path": "/api/public/projects",
                "json": {"data": [{"id": "cmgk1test0000proj", "name": "hermes"}]},
            },
        ],
    )


def seed_signoz() -> None:
    def list_row(trace, root_id, t=T0, name="agent", sid=None, error=False, attrs=None):
        shift = (t - T0) * NS
        return {
            "traceID": trace,
            "spanID": sid or root_id,
            "name": name,
            "serviceName": SERVICE,
            "durationNano": ROOT_DUR_NS if name == "agent" else TOOL_DUR_NS,
            "timestamp": iso(T0 * NS + shift, 0),
            "hasError": error or (name == "agent" and root_status(trace) == 2),
            **{k: v for k, v in (attrs if attrs is not None else ROOT_ATTRS).items()},
        }

    cols = [
        "__time",
        "SpanId",
        "TraceId",
        "ServiceName",
        "Name",
        "Kind",
        "DurationNano",
        "TagsKeys",
        "TagsValues",
        "References",
        "HasError",
        "StatusMessage",
        "StatusCodeString",
        "SpanKind",
    ]

    def event(sid, name, start_ns, dur_ns, parent, attrs, error=False):
        refs = [f"{{TraceId={TRACE}, SpanId={parent}, RefType=CHILD_OF}}"] if parent else []
        return [
            start_ns // 1_000_000,
            sid,
            TRACE,
            SERVICE,
            name,
            1,
            dur_ns,
            list(attrs.keys()),
            [str(v) for v in attrs.values()],
            refs,
            error,
            "No such file" if error else "",
            "Error" if error else "Ok",
            "Internal",
        ]

    detail = [
        {
            "columns": cols,
            "events": [
                event(ROOT_ID, "agent", T0 * NS, ROOT_DUR_NS, None, ROOT_ATTRS),
                event(TOOL_ID, "tool.terminal", TOOL_START_NS, TOOL_DUR_NS, ROOT_ID, TOOL_ATTRS),
                event(
                    FAIL_ID, "tool.read_file", FAIL_START_NS, FAIL_DUR_NS, ROOT_ID, FAIL_ATTRS, True
                ),
            ],
        }
    ]
    catalog = {
        "status": "success",
        "data": {
            "attributeKeys": [
                {
                    "key": "hermes.token.usage",
                    "dataType": "float64",
                    "type": "Sum",
                    "isColumn": True,
                },
                {
                    "key": "hermes.message.count",
                    "dataType": "float64",
                    "type": "Sum",
                    "isColumn": True,
                },
                {"key": "hermes.tool.duration.bucket", "dataType": "float64", "type": "Histogram"},
                {"key": "hermes.tool.duration.sum", "dataType": "float64", "type": "Sum"},
                {"key": "hermes.tool.duration.count", "dataType": "float64", "type": "Sum"},
                {"key": "system.cpu.utilization", "dataType": "float64", "type": "Gauge"},
            ]
        },
    }
    graph = {
        "status": "success",
        "data": {
            "resultType": "",
            "result": [
                {
                    "queryName": "A",
                    "series": [
                        {
                            "labels": {"model": "nvidia/nemotron-3-nano-omni"},
                            "labelsArray": [{"model": "nvidia/nemotron-3-nano-omni"}],
                            "values": [{"timestamp": (T0 - 3600) * 1000, "value": "53739"}],
                        },
                        {
                            "labels": {"model": MODEL},
                            "labelsArray": [{"model": MODEL}],
                            "values": [
                                {"timestamp": (T0 - 3600) * 1000, "value": "79423"},
                                {"timestamp": (T0 - 300) * 1000, "value": "100"},
                            ],
                        },
                    ],
                }
            ],
        },
    }
    log_entry = lambda ts_ns, body, sev, num, scope, attrs: {  # noqa: E731
        "timestamp": iso(ts_ns),
        "data": {
            "attributes_bool": {},
            "attributes_number": {"code.line.number": 173},
            "attributes_string": {"code.function.name": "finalize", **attrs},
            "body": body,
            "id": "0lvp5V2AsfACTj1VXzRqsSwZLbk",
            "resources_string": {"service.name": SERVICE},
            "scope_name": scope,
            "scope_string": {},
            "severity_number": num,
            "severity_text": sev,
            "span_id": ROOT_ID,
            "trace_id": TRACE,
            "trace_flags": 0,
        },
    }
    logs = {
        "status": "success",
        "data": {
            "result": [
                {
                    "queryName": "A",
                    "list": [
                        log_entry(
                            T0 * NS + 4_100_000_000,
                            f"[hermes-otel] session {SESSION} finalized",
                            "INFO",
                            9,
                            "hermes_otel",
                            {"hermes.session_id": SESSION},
                        ),
                        log_entry(
                            T0 * NS + 2_000_000_000,
                            "careful: the output was truncated",
                            "WARN",
                            13,
                            "cli",
                            {},
                        ),
                    ],
                }
            ]
        },
    }
    loggers = {
        "status": "success",
        "data": {
            "result": [
                {
                    "queryName": "A",
                    "series": [
                        {
                            "labels": {"scope_name": "gateway.run"},
                            "values": [{"timestamp": 0, "value": "38"}],
                        },
                        {
                            "labels": {"scope_name": "hermes_otel"},
                            "values": [{"timestamp": 0, "value": "73"}],
                        },
                        {"labels": {}, "values": [{"timestamp": 0, "value": "5"}]},
                    ],
                }
            ]
        },
    }
    write(
        "signoz",
        base_config(
            {"type": "signoz", "endpoint": "http://localhost:3301", "api_key": "signoz-test-key"},
            counter="hermes.token.usage",
            gauge="system.cpu.utilization",
        ),
        [
            {
                "method": "GET",
                "path": f"/api/v1/traces/{MISSING}",
                "status": 404,
                "text": '{"error":"trace not found"}',
            },
            {"method": "GET", "path": f"/api/v1/traces/{TRACE}", "json": detail},
            {
                "method": "GET",
                "path_prefix": "/api/v3/autocomplete/aggregate_attributes",
                "json": catalog,
            },
            {
                "method": "POST",
                "path": "/api/v4/query_range",
                "body_contains": '"dataSource": "metrics"',
                "json": graph,
            },
            {
                "method": "POST",
                "path": "/api/v4/query_range",
                "body_contains": ['"dataSource": "logs"', '"panelType": "list"'],
                "json": logs,
            },
            {
                "method": "POST",
                "path": "/api/v4/query_range",
                "body_contains": '"dataSource": "logs"',
                "json": loggers,
            },
            {
                "method": "POST",
                "path": "/api/v4/query_range",
                "body_contains": '"value": "^tool\\\\."',
                "json": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "queryName": "A",
                                "list": [
                                    {
                                        "data": list_row(
                                            TRACE,
                                            ROOT_ID,
                                            name="tool.terminal",
                                            sid=TOOL_ID,
                                            attrs=TOOL_ATTRS,
                                        )
                                    },
                                    {
                                        "data": list_row(
                                            TRACE,
                                            ROOT_ID,
                                            name="tool.read_file",
                                            sid=FAIL_ID,
                                            error=True,
                                            attrs=FAIL_ATTRS,
                                        )
                                    },
                                    {
                                        "data": list_row(
                                            OLDER,
                                            OLD_ROOT_ID,
                                            OLD_T,
                                            name="tool.terminal",
                                            sid=TOOL_ID,
                                            attrs=TOOL_ATTRS,
                                        )
                                    },
                                ],
                            }
                        ]
                    },
                },
            },
            {
                "method": "POST",
                "path": "/api/v4/query_range",
                "json": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "queryName": "A",
                                "list": [
                                    {"data": list_row(TRACE, ROOT_ID)},
                                    {"data": list_row(OLDER, OLD_ROOT_ID, OLD_T)},
                                ],
                            }
                        ]
                    },
                },
            },
        ],
    )


def seed_openobserve() -> None:
    def row(trace, sid, name, start_ns, dur_ns, parent, status, attrs):
        r = {
            "_timestamp": start_ns // 1000,
            "trace_id": trace,
            "span_id": sid,
            "operation_name": name,
            "start_time": start_ns,
            "end_time": start_ns + dur_ns,
            "duration": dur_ns // 1000,
            "service_name": SERVICE,
            "span_kind": 1,
            "span_status": "ERROR" if status == 2 else "OK",
            "status_code": status,
            "status_message": "No such file" if status == 2 else "",
            "flags": 1,
            "reference_parent_span_id": parent or "",
            "reference_parent_trace_id": trace if parent else "",
            "reference_ref_type": "CHILD_OF" if parent else "",
        }
        for k, v in attrs.items():
            r[k.replace(".", "_")] = str(v).lower() if isinstance(v, bool) else v
        return r

    def turn(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return [
            row(
                trace,
                root_id,
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                None,
                root_status(trace),
                ROOT_ATTRS,
            ),
            row(
                trace,
                TOOL_ID,
                "tool.terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                1,
                TOOL_ATTRS,
            ),
            row(
                trace,
                FAIL_ID,
                "tool.read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                2,
                FAIL_ATTRS,
            ),
        ]

    def metric_rows(name, values, cumulative=True):
        return [
            {
                "_timestamp": (T0 - 3600 + i * 600) * 1_000_000,
                "value": v,
                "__name__": name,
                "aggregation_temporality": (
                    "AGGREGATION_TEMPORALITY_CUMULATIVE" if cumulative else ""
                ),
                "is_monotonic": "true" if cumulative else "false",
                "start_time": (T0 - 7200) * NS,
                "model": MODEL if i % 2 else "nvidia/nemotron-3-nano-omni",
                "service_instance_id": INSTANCE if i % 2 else "gw",
                "telemetry_sdk_name": "opentelemetry",
            }
            for i, v in enumerate(values)
        ]

    def log_row(ts_ns, body, sev, num, scope, extra):
        return {
            "_timestamp": ts_ns // 1000,
            "body": body,
            "severity": sev,
            "severity_number": num,
            "instrumentation_library_name": scope,
            "trace_id": TRACE,
            "span_id": ROOT_ID,
            "service_name": SERVICE,
            "code_line_number": 173,
            **extra,
        }

    hits_main = {
        "hits": turn(TRACE, ROOT_ID)[:1] + turn(OLDER, OLD_ROOT_ID, OLD_T)[:1],
        "total": 2,
        "took": 12,
    }
    write(
        "openobserve",
        base_config(
            {
                "type": "openobserve",
                "endpoint": "http://localhost:5080/api/default/v1/traces",
                "user": "root@example.com",
                "password": "oo-test-password",
            },
            counter="hermes_token_usage",
            gauge="system_cpu_utilization",
        ),
        [
            {
                "method": "GET",
                "path": "/api/default/streams/default/schema",
                "json": {
                    "name": "default",
                    "schema": [
                        {"name": "input_value", "type": "Utf8"},
                        {"name": "output_value", "type": "Utf8"},
                        {"name": "gen_ai_input_messages", "type": "Utf8"},
                    ],
                },
            },
            {
                "method": "GET",
                "path": "/api/default/streams",
                "query_contains": "type=metrics",
                "json": {
                    "list": [
                        {"name": "hermes_token_usage", "stats": {"doc_num": 9}},
                        {
                            "name": "hermes_tool_duration_milliseconds_bucket",
                            "stats": {"doc_num": 40},
                        },
                        {"name": "hermes_tool_duration_milliseconds_sum", "stats": {"doc_num": 4}},
                        {"name": "system_cpu_utilization", "stats": {"doc_num": 120}},
                        {"name": "otel_sdk_exporter_span_exported", "stats": {"doc_num": 3}},
                    ]
                },
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "query_contains": "type=metrics",
                "body_contains": "hermes_token_usage",
                "json": {
                    "hits": metric_rows(
                        "hermes_token_usage", [100, 100, 105, 23690, 23690, 23795, 23795]
                    )
                },
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "query_contains": "type=metrics",
                "json": {
                    "hits": metric_rows(
                        "system_cpu_utilization", [0.5, 0.25, 0.3], cumulative=False
                    )
                },
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "query_contains": "type=logs",
                "body_contains": "GROUP BY instrumentation_library_name",
                "json": {
                    "hits": [
                        {"logger": "hermes_otel", "n": 6},
                        {"logger": "cli", "n": 2},
                        {"logger": "", "n": 1},
                    ]
                },
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "query_contains": "type=logs",
                "json": {
                    "hits": [
                        log_row(
                            T0 * NS + 4_100_000_000,
                            f"[hermes-otel] session {SESSION} finalized",
                            "INFO",
                            9,
                            "hermes_otel",
                            {"hermes_session_id": SESSION, "hermes_log_attribution": "context"},
                        ),
                        log_row(
                            T0 * NS + 2_000_000_000,
                            "careful: the output was truncated",
                            "WARN",
                            13,
                            "cli",
                            {"event_name": "hermes.turn.end"},
                        ),
                    ]
                },
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "body_contains": "GROUP BY trace_id",
                "json": {"hits": [{"trace_id": TRACE, "n": 3}, {"trace_id": OLDER, "n": 3}]},
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "body_contains": f"trace_id = '{MISSING}'",
                "json": {"hits": [], "total": 0},
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "body_contains": f"trace_id = '{TRACE}'",
                "json": {"hits": turn(TRACE, ROOT_ID), "total": 3},
            },
            {
                "method": "POST",
                "path": "/api/default/_search",
                "body_contains": "operation_name LIKE 'tool.%'",
                "json": {
                    "hits": turn(TRACE, ROOT_ID)[1:] + turn(OLDER, OLD_ROOT_ID, OLD_T)[1:2],
                    "total": 3,
                },
            },
            {"method": "POST", "path": "/api/default/_search", "json": hits_main},
        ],
    )


def seed_uptrace() -> None:
    def span(sid, name, start_ns, dur_ns, parent, status, attrs, trace=TRACE, system="funcs"):
        a = {"service_name::str": SERVICE}
        for k, v in attrs.items():
            key = k.replace(".", "_")
            if isinstance(v, bool):
                a[f"{key}::bool"] = v
            elif isinstance(v, int):
                a[f"{key}::int"] = v
            else:
                a[f"{key}::str"] = str(v)
        sp = {
            "id": sid,
            "traceId": trace,
            "projectId": 1,
            "type": "",
            "system": system,
            "kind": "internal",
            "name": name,
            "displayName": name,
            "time": start_ns / 1_000_000,
            "duration": dur_ns / 1_000_000,
            "statusCode": "error" if status == 2 else "ok",
            "statusMessage": "No such file" if status == 2 else "",
            "attrs": a,
            "events": [],
            "logs": [],
            "links": [],
        }
        if parent:
            sp["parentId"] = parent
        return sp

    def turn(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return [
            span(
                root_id,
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                None,
                root_status(trace),
                ROOT_ATTRS,
                trace,
            ),
            span(
                TOOL_ID,
                "tool.terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                1,
                TOOL_ATTRS,
                trace,
            ),
            span(
                FAIL_ID,
                "tool.read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                2,
                FAIL_ATTRS,
                trace,
            ),
        ]

    def log(sid, ts_ns, body, sev, scope, extra):
        return {
            "id": sid,
            "traceId": TRACE,
            "parentId": ROOT_ID,
            "projectId": 1,
            "groupId": "1",
            "type": "log",
            "system": f"log:{sev.lower()}",
            "kind": "internal",
            "name": "",
            "eventName": "log",
            "displayName": body,
            "time": ts_ns / 1_000_000,
            "duration": 0,
            "statusCode": "unset",
            "attrs": {
                "log_severity::str": sev,
                "otel_library_name::str": scope,
                "hermes_session_id::str": SESSION,
                "hermes_log_attribution::str": "context",
                "service_name::str": SERVICE,
                **extra,
            },
        }

    write(
        "uptrace",
        base_config(
            {
                "type": "uptrace",
                "endpoint": "http://localhost:14318",
                "user_token": "uptrace-test-token",
            },
            counter="hermes_token_usage",
            gauge="system_cpu_utilization",
        ),
        [
            {
                "method": "GET",
                "path": "/internal/v1/logs/1/systems",
                "json": {
                    "systems": [
                        {"groupCount": 1, "system": "log:info"},
                        {"groupCount": 1, "system": "log:warn"},
                    ]
                },
            },
            {
                "method": "GET",
                "path": f"/internal/v1/traces/1/{MISSING}",
                "status": 404,
                "text": '{"message":"trace not found"}',
            },
            {
                "method": "GET",
                "path": f"/internal/v1/traces/1/{TRACE}",
                "json": {"id": TRACE, "spans": turn(TRACE, ROOT_ID), "logs": []},
            },
            {
                "method": "GET",
                "path": "/internal/v1/spans/1",
                "query_contains": "tool.%",
                "json": {
                    "count": 3,
                    "spans": turn(TRACE, ROOT_ID)[1:] + turn(OLDER, OLD_ROOT_ID, OLD_T)[1:2],
                },
            },
            {
                "method": "GET",
                "path": "/internal/v1/spans/1",
                "json": {
                    "count": 2,
                    "spans": [turn(TRACE, ROOT_ID)[0], turn(OLDER, OLD_ROOT_ID, OLD_T)[0]],
                },
            },
            {
                "method": "GET",
                "path": "/internal/v1/logs/1",
                "json": {
                    "count": 2,
                    "spans": [
                        log(
                            "7541fb626d6c0bf8",
                            T0 * NS + 4_100_000_000,
                            f"[hermes-otel] session {SESSION} finalized",
                            "INFO",
                            "hermes_otel",
                            {},
                        ),
                        log(
                            "49584777a3766d16",
                            T0 * NS + 2_000_000_000,
                            "careful: the output was truncated",
                            "WARN",
                            "cli",
                            {"event_name::str": "hermes.turn.end"},
                        ),
                    ],
                },
            },
            {
                "method": "GET",
                "path": "/internal/v1/tracing/1/attributes/otel_library_name::str",
                "json": {
                    "hasMore": False,
                    "items": [
                        {"value": "hermes_otel", "count": 18},
                        {"value": "cli", "count": 14},
                        {"value": "", "count": 3},
                    ],
                },
            },
            {
                "method": "GET",
                "path": "/internal/v1/metrics/1/timeseries",
                "json": {
                    "columns": [],
                    "hasMore": False,
                    "query": [
                        {
                            "disabled": False,
                            "error": "",
                            "id": "1",
                            "query": "$m group by model::str",
                            "type": "selector",
                        }
                    ],
                    "timeseries": [
                        {
                            "name": "x+nvidia/nemotron-3-nano-omni",
                            "metric": "$m group by model",
                            "unit": "{token}",
                            "attrs": {"model::str": "nvidia/nemotron-3-nano-omni"},
                            "time": [(T0 - 3600) * 1000, (T0 - 300) * 1000],
                            "value": [26748, None],
                        },
                        {
                            "name": "y+" + MODEL,
                            "metric": "$m group by model",
                            "unit": "{token}",
                            "attrs": {"model::str": MODEL},
                            "time": [(T0 - 3600) * 1000, (T0 - 300) * 1000],
                            "value": [42342, 100],
                        },
                    ],
                },
            },
            {
                "method": "GET",
                "path": "/internal/v1/metrics/1",
                "json": {
                    "metrics": [
                        {"name": "hermes_token_usage", "instrument": "counter", "unit": "{token}"},
                        {"name": "hermes_tool_duration", "instrument": "histogram", "unit": "ms"},
                        {"name": "system_cpu_utilization", "instrument": "gauge", "unit": "1"},
                    ]
                },
            },
        ],
    )


def seed_clickstack() -> None:
    def ch(rows):
        return {"meta": [], "data": rows, "rows": len(rows)}

    def span_row(trace, sid, name, ts_ns, dur_ns, parent, status, attrs):
        return {
            "TraceId": trace,
            "SpanId": sid,
            "ParentSpanId": parent or "",
            "SpanName": name,
            "SpanKind": "Internal",
            "ServiceName": SERVICE,
            "StatusCode": "Error" if status == 2 else "Ok",
            "StatusMessage": "No such file" if status == 2 else "",
            "Duration": dur_ns,
            "SpanAttributes": {
                k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in attrs.items()
            },
            "ResourceAttributes": {"service.instance.id": INSTANCE, "hermes.profile": "default"},
            "ts_ns": ts_ns,
            "event_ts": [],
            "event_names": [],
            "event_attrs": [],
        }

    def turn(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return [
            span_row(
                trace,
                root_id,
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                None,
                root_status(trace),
                ROOT_ATTRS,
            ),
            span_row(
                trace,
                TOOL_ID,
                "tool.terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                1,
                TOOL_ATTRS,
            ),
            span_row(
                trace,
                FAIL_ID,
                "tool.read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                2,
                FAIL_ATTRS,
            ),
        ]

    s = T0 - 3600
    counter_rows = [
        {
            "ts": s - 120,
            "v": 100,
            "Attributes": {"model": "nvidia/nemotron-3-nano-omni"},
            "inst": "gw",
        },
        {
            "ts": s + 30,
            "v": 100,
            "Attributes": {"model": "nvidia/nemotron-3-nano-omni"},
            "inst": "gw",
        },
        {
            "ts": s + 90,
            "v": 105,
            "Attributes": {"model": "nvidia/nemotron-3-nano-omni"},
            "inst": "gw",
        },
        {"ts": T0 - 300, "v": 23690, "Attributes": {"model": MODEL}, "inst": "one"},
        {"ts": T0 - 240, "v": 23690, "Attributes": {"model": MODEL}, "inst": "one"},
    ]
    gauge_rows = [
        {"ts": T0 - 3000, "v": 0.5, "Attributes": {}, "inst": "i"},
        {"ts": T0 - 600, "v": 0.25, "Attributes": {}, "inst": "i"},
    ]
    log_rows = [
        {
            "ts_ns": T0 * NS + 4_100_000_000,
            "SeverityText": "info",
            "SeverityNumber": 9,
            "ScopeName": "hermes_otel",
            "Body": f"[hermes-otel] session {SESSION} finalized",
            "TraceId": TRACE,
            "SpanId": ROOT_ID,
            "LogAttributes": {"hermes.session_id": SESSION, "code.line.number": "173"},
        },
        {
            "ts_ns": T0 * NS + 2_000_000_000,
            "SeverityText": "warn",
            "SeverityNumber": 13,
            "ScopeName": "cli",
            "Body": "careful: the output was truncated",
            "TraceId": TRACE,
            "SpanId": "",
            "LogAttributes": {"event.name": "hermes.turn.end"},
        },
    ]
    write(
        "clickstack",
        base_config(
            {
                "type": "clickstack",
                "endpoint": "http://localhost:4408",
                "query_port": 8124,
                "user": "api",
                "password": "api",
            },
            counter="hermes.token.usage",
            gauge="system.cpu.utilization",
        ),
        [
            {
                "method": "POST",
                "path": "/",
                "body_contains": "GROUP BY TraceId",
                "json": ch([{"TraceId": TRACE, "n": 3}, {"TraceId": OLDER, "n": 3}]),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": f"WHERE TraceId = '{MISSING}'",
                "json": ch([]),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": f"WHERE TraceId = '{TRACE}'",
                "json": ch(turn(TRACE, ROOT_ID)),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": "UNION ALL",
                "json": ch(
                    [
                        {"kind": "sum", "MetricName": "hermes.token.usage"},
                        {"kind": "gauge", "MetricName": "system.cpu.utilization"},
                        {"kind": "histogram", "MetricName": "hermes.tool.duration"},
                    ]
                ),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": ["otel_metrics_sum", "system.cpu"],
                "json": ch([]),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": "otel_metrics_sum",
                "json": ch(counter_rows),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": "otel_metrics_gauge",
                "json": ch(gauge_rows),
            },
            {
                "method": "POST",
                "path": "/",
                "body_contains": "GROUP BY ScopeName",
                "json": ch(
                    [
                        {"ScopeName": "hermes_otel", "n": 6},
                        {"ScopeName": "cli", "n": 2},
                        {"ScopeName": "", "n": 9},
                    ]
                ),
            },
            {"method": "POST", "path": "/", "body_contains": "otel_logs", "json": ch(log_rows)},
            {
                "method": "POST",
                "path": "/",
                "body_contains": "startsWith(SpanName, 'tool.')",
                "json": ch(turn(TRACE, ROOT_ID)[1:] + turn(OLDER, OLD_ROOT_ID, OLD_T)[1:2]),
            },
            {
                "method": "POST",
                "path": "/",
                "json": ch([turn(TRACE, ROOT_ID)[0], turn(OLDER, OLD_ROOT_ID, OLD_T)[0]]),
            },
        ],
    )


def seed_aspire() -> None:
    def doc(spans):
        return {
            "data": {
                "resourceSpans": [
                    {
                        "resource": otlp_resource(),
                        "scopeSpans": [{"scope": {"name": "hermes-otel-plugin"}, "spans": spans}],
                    }
                ]
            },
            "totalCount": len(spans),
            "returnedCount": len(spans),
        }

    def log(ts_ns, body, num, text, scope, attrs, trace=True):
        rec = {
            "timeUnixNano": str(ts_ns),
            "severityNumber": num,
            "severityText": text,
            "body": {"stringValue": body},
            "attributes": [
                otlp_attr("aspire.log_id", 42),
                *(otlp_attr(k, v) for k, v in attrs.items()),
            ],
        }
        if trace:
            rec["traceId"] = TRACE
            rec["spanId"] = ROOT_ID
        return scope, rec

    def logs_doc(*scoped):
        by_scope: Dict[str, List[Dict[str, Any]]] = {}
        for scope, rec in scoped:
            by_scope.setdefault(scope, []).append(rec)
        return {
            "data": {
                "resourceLogs": [
                    {
                        "resource": otlp_resource(),
                        "scopeLogs": [
                            {"scope": {"name": s}, "logRecords": recs}
                            for s, recs in by_scope.items()
                        ],
                    }
                ]
            },
            "totalCount": len(scoped),
            "returnedCount": len(scoped),
        }

    write(
        "aspire",
        base_config(
            {"type": "aspire", "endpoint": "http://localhost:4418", "query_port": 18888},
            metrics=False,
        ),
        [
            {
                "method": "GET",
                "path": "/api/telemetry/resources",
                "json": [
                    {"name": SERVICE, "displayName": f"{SERVICE}-f26a", "hasTraces": True},
                    {"name": SERVICE, "displayName": f"{SERVICE}-ab54", "hasTraces": True},
                    {"name": "other", "displayName": "other-1", "hasTraces": True},
                ],
            },
            {
                "method": "GET",
                "path": f"/api/telemetry/traces/{MISSING}",
                "status": 404,
                "text": "",
            },
            {"method": "GET", "path": f"/api/telemetry/traces/{TRACE}", "json": doc(turn_otlp())},
            {
                "method": "GET",
                "path": "/api/telemetry/traces",
                "json": doc(
                    turn_otlp()[1:] + turn_otlp()[:1] + turn_otlp(OLDER, OLD_ROOT_ID, OLD_T)
                ),
            },
            {
                "method": "GET",
                "path": "/api/telemetry/logs",
                "json": logs_doc(
                    log(
                        T0 * NS + 4_100_000_000,
                        f"[hermes-otel] session {SESSION} finalized",
                        9,
                        "Information",
                        "hermes_otel",
                        {"hermes.session_id": SESSION, "code.line.number": 173},
                    ),
                    log(
                        T0 * NS + 2_000_000_000,
                        "careful: the output was truncated",
                        13,
                        "Warning",
                        "cli",
                        {"event.name": "hermes.turn.end"},
                        trace=False,
                    ),
                ),
            },
        ],
    )


def seed_greptime() -> None:
    def gt(cols, rows):
        return {
            "output": [
                {
                    "records": {
                        "schema": {
                            "column_schemas": [{"name": c, "data_type": "String"} for c in cols]
                        },
                        "rows": rows,
                    }
                }
            ],
            "execution_time_ms": 3,
        }

    counter_cols = ["greptime_timestamp", "greptime_value", "model", "service_instance_id"]
    s = T0 - 3600
    counter_rows = [
        [(s - 120) * 1000, 100, "nvidia/nemotron-3-nano-omni", "gw"],
        [(s + 30) * 1000, 100, "nvidia/nemotron-3-nano-omni", "gw"],
        [(s + 90) * 1000, 105, "nvidia/nemotron-3-nano-omni", "gw"],
        [(T0 - 300) * 1000, 23690, MODEL, "one"],
        [(T0 - 240) * 1000, 23690, MODEL, "one"],
    ]
    gauge_cols = ["greptime_timestamp", "greptime_value", "cpu_mode"]
    gauge_rows = [[(T0 - 3000) * 1000, 0.5, "user"], [(T0 - 600) * 1000, 0.25, "user"]]
    log_cols = [
        "timestamp",
        "severity_text",
        "severity_number",
        "scope_name",
        "body",
        "trace_id",
        "span_id",
        "log_attributes",
    ]
    log_rows = [
        [
            T0 * NS + 4_100_000_000,
            "INFO",
            9,
            "hermes_otel",
            f"[hermes-otel] session {SESSION} finalized",
            TRACE,
            ROOT_ID,
            {"code.file.path": "/x.py", "hermes.session_id": SESSION},
        ],
        [
            T0 * NS + 2_000_000_000,
            "WARN",
            13,
            "cli",
            "careful: the output was truncated",
            TRACE,
            "",
            {"event.name": "hermes.turn.end"},
        ],
    ]
    write(
        "greptime",
        base_config({"type": "greptime", "endpoint": "http://localhost:4398"}),
        jaeger_routes("/v1/jaeger", probe=False)
        + [
            {
                "method": "GET",
                "path": "/v1/prometheus/api/v1/label/__name__/values",
                "json": prom_names(),
            },
            {
                "method": "POST",
                "path": "/v1/sql",
                "body_contains": ["information_schema", "hermes_token_usage_total"],
                "json": gt(["column_name"], [[c] for c in counter_cols]),
            },
            {
                "method": "POST",
                "path": "/v1/sql",
                "body_contains": ["information_schema", "system_cpu_utilization"],
                "json": gt(["column_name"], [[c] for c in gauge_cols]),
            },
            {
                "method": "POST",
                "path": "/v1/sql",
                "body_contains": 'FROM "hermes_token_usage_total"',
                "json": gt(counter_cols, counter_rows),
            },
            {
                "method": "POST",
                "path": "/v1/sql",
                "body_contains": 'FROM "system_cpu_utilization"',
                "json": gt(gauge_cols, gauge_rows),
            },
            {
                "method": "POST",
                "path": "/v1/sql",
                "body_contains": ['"opentelemetry_logs"', "GROUP BY"],
                "json": gt(["scope_name", "n"], [["hermes_otel", 6], ["cli", 2], [None, 9]]),
            },
            {
                "method": "POST",
                "path": "/v1/sql",
                "body_contains": '"opentelemetry_logs"',
                "json": gt(log_cols, log_rows),
            },
        ],
    )


def seed_victoria() -> None:
    def ndjson(rows):
        return "\n".join(json.dumps(r) for r in rows) + "\n"

    log_rows = [
        {
            "_msg": f"[hermes-otel] session {SESSION} finalized",
            "_stream": f'{{service.name="{SERVICE}"}}',
            "_stream_id": "0000000000000000f1c3d08708ccc0e6c532624dc5cdc71c",
            "_time": iso(T0 * NS + 4_100_000_000, 9),
            "code.line.number": "173",
            "hermes.session_id": SESSION,
            "scope.name": "hermes_otel",
            "service.name": SERVICE,
            "severity_number": "9",
            "severity_text": "INFO",
            "span_id": ROOT_ID,
            "trace_id": TRACE,
        },
        {
            "_msg": "careful: the output was truncated",
            "_stream": f'{{service.name="{SERVICE}"}}',
            "_stream_id": "0000000000000000f1c3d08708ccc0e6c532624dc5cdc71c",
            "_time": iso(T0 * NS + 2_000_000_000, 9),
            "event.name": "hermes.turn.end",
            "scope.name": "cli",
            "service.name": SERVICE,
            "severity_number": "13",
            "severity_text": "WARN",
            "span_id": "",
            "trace_id": "",
        },
    ]
    write(
        "victoria",
        base_config(
            {
                "type": "victoria",
                "endpoint": "http://localhost:10428",
                "metrics_endpoint": "http://localhost:8428",
                "logs_endpoint": "http://localhost:9428",
            }
        ),
        jaeger_routes("/select/jaeger", stringy=True, probe=False)
        + [
            {"method": "GET", "path": "/api/v1/label/__name__/values", "json": prom_names()},
            {"method": "GET", "path": "/api/v1/query", "json": prom_counter_matrix()},
            {"method": "GET", "path": "/api/v1/query_range", "json": prom_gauge_matrix()},
            {
                "method": "POST",
                "path": "/select/logsql/query",
                "body_contains": "stats by",
                "text": ndjson(
                    [
                        {"scope.name": "hermes_otel", "n": "6"},
                        {"scope.name": "cli", "n": "2"},
                        {"scope.name": "", "n": "9"},
                    ]
                ),
            },
            {"method": "POST", "path": "/select/logsql/query", "text": ndjson(log_rows)},
        ],
    )


def seed_agenta() -> None:
    def span(trace, sid, name, start_ns, dur_ns, parent, span_type, status, attrs):
        nested: Dict[str, Any] = {
            "ag": {
                "type": {"span": span_type, "trace": "invocation"},
                "metrics": {
                    "tokens": {"cumulative": {"total": ROOT_ATTRS["gen_ai.usage.total_tokens"]}}
                },
                "data": {"inputs": {"prompt": "big"}},
                "meta": {"tool": {"name": "terminal"}},
                "session": {"id": SESSION},
            }
        }
        for k, v in attrs.items():
            cur = nested
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v
        return {
            "trace_id": trace,
            "span_id": sid,
            "parent_id": parent,
            "trace_type": "invocation",
            "span_type": span_type,
            "span_kind": "SPAN_KIND_INTERNAL",
            "span_name": name,
            "start_time": iso(start_ns, 6),
            "end_time": iso(start_ns + dur_ns, 6),
            "status_code": "STATUS_CODE_ERROR" if status == 2 else "STATUS_CODE_OK",
            "status_message": "No such file" if status == 2 else None,
            "attributes": nested,
        }

    def turn(trace, root_id, t=T0):
        shift = (t - T0) * NS
        return [
            span(
                trace,
                root_id,
                "agent",
                T0 * NS + shift,
                ROOT_DUR_NS,
                None,
                "agent",
                root_status(trace),
                ROOT_ATTRS,
            ),
            span(
                trace,
                TOOL_ID,
                "tool.terminal",
                TOOL_START_NS + shift,
                TOOL_DUR_NS,
                root_id,
                "tool",
                1,
                TOOL_ATTRS,
            ),
            span(
                trace,
                FAIL_ID,
                "tool.read_file",
                FAIL_START_NS + shift,
                FAIL_DUR_NS,
                root_id,
                "tool",
                2,
                FAIL_ATTRS,
            ),
        ]

    write(
        "agenta",
        base_config(
            {"type": "agenta", "endpoint": "http://localhost:3060", "api_key": "agenta-test-key"},
            metrics=False,
            logs=False,
        ),
        [
            {
                "method": "POST",
                "path": "/api/spans/query",
                "body_contains": ['"field": "trace_id"', '"operator": "in"'],
                "json": {
                    "count": 6,
                    "spans": turn(TRACE, ROOT_ID) + turn(OLDER, OLD_ROOT_ID, OLD_T),
                },
            },
            {
                "method": "POST",
                "path": "/api/spans/query",
                "body_contains": ['"field": "trace_id"', MISSING],
                "json": {"count": 0, "spans": []},
            },
            {
                "method": "POST",
                "path": "/api/spans/query",
                "body_contains": ['"field": "trace_id"', '"operator": "is"'],
                "json": {"count": 3, "spans": turn(TRACE, ROOT_ID)},
            },
            {
                "method": "POST",
                "path": "/api/spans/query",
                "body_contains": '"startswith", "value": "tool."',
                "json": {
                    "count": 3,
                    "spans": turn(TRACE, ROOT_ID)[1:] + turn(OLDER, OLD_ROOT_ID, OLD_T)[1:2],
                },
            },
            {
                "method": "POST",
                "path": "/api/spans/query",
                "json": {
                    "count": 2,
                    "spans": [turn(TRACE, ROOT_ID)[0], turn(OLDER, OLD_ROOT_ID, OLD_T)[0]],
                },
            },
        ],
    )


def main() -> None:
    for fn in (
        seed_jaeger,
        seed_zipkin,
        seed_tempo,
        seed_gigapipe,
        seed_phoenix,
        seed_langfuse,
        seed_signoz,
        seed_openobserve,
        seed_uptrace,
        seed_clickstack,
        seed_aspire,
        seed_greptime,
        seed_victoria,
        seed_agenta,
    ):
        fn()


if __name__ == "__main__":
    main()
