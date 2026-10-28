"""The two loopback servers the credential-free e2e tier runs Hermes against (#482).

* :class:`ScriptedProvider` — an OpenAI-compatible endpoint (``/v1/models``,
  ``/v1/chat/completions``, streaming and not) that answers from a scripted
  sequence: a plain completion, or a tool call followed by a final answer.
  Every request is recorded for assertions. No model, no key, no network.
* :class:`OtlpReceiver` — an OTLP/HTTP endpoint (``/v1/traces``,
  ``/v1/metrics``, ``/v1/logs``) that decodes the protobuf payloads the
  plugin's exporters send into plain dicts (``opentelemetry-proto`` ships
  with the exporter package the plugin depends on).

Both bind ``127.0.0.1`` on an ephemeral port and run on a daemon thread.
Standard library plus the OTel proto package only, so the tier needs nothing
beyond the plugin's own dependencies and a ``hermes`` on ``PATH``.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Union

MODEL_ID = "scripted-model"
USAGE = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}


# ── Scripted OpenAI-compatible provider ───────────────────────────────────


def text(content: str) -> Dict[str, Any]:
    """A scripted response: the assistant answers ``content``."""
    return {"content": content}


def tool_call(name: str, arguments: Dict[str, Any], call_id: str = "call_e2e_1") -> Dict[str, Any]:
    """A scripted response: the assistant calls tool ``name`` with ``arguments``."""
    return {"tool_call": {"id": call_id, "name": name, "arguments": arguments}}


@dataclass
class Request:
    """One request the provider received, for assertions."""

    method: str
    path: str
    body: Dict[str, Any]


class _Script:
    """The scripted responses still to serve, consumed in request order.

    A request carrying ``response_format`` (Hermes's title generation, a
    structured auxiliary call) never consumes a scripted step: it is answered
    with a JSON object that satisfies any ``{"title": ...}`` schema, so the
    agent loop's own requests see exactly the sequence a test scripted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._steps: List[Dict[str, Any]] = []

    def load(self, steps: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._steps = list(steps)

    def next(self) -> Dict[str, Any]:
        with self._lock:
            if self._steps:
                return self._steps.pop(0)
        return text("No scripted response left; the e2e script was shorter than the turn.")

    def remaining(self) -> int:
        with self._lock:
            return len(self._steps)


class ScriptedProvider:
    """``http://127.0.0.1:<port>/v1`` for Hermes's ``provider: custom``."""

    def __init__(self) -> None:
        self.requests: List[Request] = []
        self._script = _Script()
        self._lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def do_GET(self) -> None:
                provider._record("GET", self.path, {})
                path = self.path.split("?", 1)[0].rstrip("/")
                if path.endswith("/v1/models"):
                    _json(self, 200, {"object": "list", "data": [_model_object()]})
                elif path.endswith(f"/v1/models/{MODEL_ID}"):
                    _json(self, 200, _model_object())
                else:
                    # Hermes also probes LM Studio / Ollama shapes (/api/v1/models,
                    # /api/show); a 404 tells it this is a plain OpenAI endpoint.
                    _json(self, 404, {"error": {"message": f"no route {self.path}"}})

            def do_POST(self) -> None:
                body = _read_json(self)
                provider._record("POST", self.path, body)
                if not self.path.rstrip("/").endswith("/v1/chat/completions"):
                    _json(self, 404, {"error": {"message": f"no route {self.path}"}})
                    return
                if body.get("response_format"):
                    step = text(json.dumps({"title": "e2e scripted turn"}))
                else:
                    step = provider._script.next()
                if body.get("stream"):
                    _stream_completion(self, step)
                else:
                    _json(self, 200, _completion(step))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="e2e-provider", daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def script(self, *steps: Dict[str, Any]) -> None:
        """Load the responses the next agent-loop requests get, in order."""
        self._script.load(list(steps))

    def remaining(self) -> int:
        return self._script.remaining()

    def chat_requests(self) -> List[Request]:
        """The ``/v1/chat/completions`` requests received so far."""
        return [r for r in self.requests if r.path.rstrip("/").endswith("/v1/chat/completions")]

    def clear(self) -> None:
        with self._lock:
            self.requests.clear()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _record(self, method: str, path: str, body: Dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(Request(method, path, body))


def _model_object() -> Dict[str, Any]:
    return {"id": MODEL_ID, "object": "model", "created": 1, "owned_by": "hermes-otel-e2e"}


def _message(step: Dict[str, Any]) -> Dict[str, Any]:
    if "tool_call" in step:
        call = step["tool_call"]
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                }
            ],
        }
    return {"role": "assistant", "content": step["content"]}


def _finish_reason(step: Dict[str, Any]) -> str:
    return "tool_calls" if "tool_call" in step else "stop"


def _completion(step: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": "chatcmpl-e2e",
        "object": "chat.completion",
        "created": 1,
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": _message(step), "finish_reason": _finish_reason(step)}],
        "usage": dict(USAGE),
    }


def _stream_completion(handler: BaseHTTPRequestHandler, step: Dict[str, Any]) -> None:
    """The same answer as ``_completion`` as SSE chunks: role, content or the
    tool call in one delta, then the finish chunk carrying ``usage``."""
    message = _message(step)
    first_delta: Dict[str, Any] = {"role": "assistant"}
    if message.get("tool_calls"):
        calls = []
        for i, call in enumerate(message["tool_calls"]):
            calls.append(dict(call, index=i))
        first_delta["tool_calls"] = calls
        first_delta["content"] = None
    else:
        first_delta["content"] = message["content"]
    chunks = [
        _chunk({"delta": first_delta, "finish_reason": None}),
        _chunk({"delta": {}, "finish_reason": _finish_reason(step)}, usage=dict(USAGE)),
    ]
    payload = b"".join(f"data: {json.dumps(c)}\n\n".encode("utf-8") for c in chunks)
    payload += b"data: [DONE]\n\n"
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
    handler.wfile.flush()


def _chunk(choice: Dict[str, Any], usage: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": "chatcmpl-e2e",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": MODEL_ID,
        "choices": [dict(choice, index=0)],
    }
    if usage is not None:
        out["usage"] = usage
    return out


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    raw = _read_body(handler)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    if handler.headers.get("Content-Encoding", "").lower() == "gzip":
        raw = gzip.decompress(raw)
    return raw


def _json(handler: BaseHTTPRequestHandler, code: int, body: Any) -> None:
    data = json.dumps(body).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


# ── OTLP/HTTP receiver ────────────────────────────────────────────────────


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    kind: str
    status: str
    attributes: Dict[str, Any]
    resource: Dict[str, Any]
    events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MetricPoint:
    name: str
    attributes: Dict[str, Any]
    value: Any  # a number for sums and gauges, {"count", "sum"} for histograms
    kind: str  # sum | gauge | histogram
    start_ns: int = 0  # startTimeUnixNano: one cumulative series per process and attribute set


@dataclass
class LogRecord:
    body: Any
    severity: str
    event_name: Optional[str]
    attributes: Dict[str, Any]
    trace_id: Optional[str]
    span_id: Optional[str]


_Scalar = Union[str, int, float, bool, None]


def decode_any_value(value: Dict[str, Any]) -> Any:
    """An OTLP ``AnyValue`` (as ``MessageToDict`` renders it) to a Python value.
    int64 fields arrive as strings; they come back as ints."""
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "arrayValue" in value:
        return [decode_any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return decode_attributes(value["kvlistValue"].get("values", []))
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def decode_attributes(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {kv["key"]: decode_any_value(kv.get("value", {})) for kv in items}


def _spans_of(request: Dict[str, Any]) -> List[Span]:
    out: List[Span] = []
    for rs in request.get("resourceSpans", []):
        resource = decode_attributes(rs.get("resource", {}).get("attributes", []))
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                status = sp.get("status", {}).get("code", "STATUS_CODE_UNSET")
                out.append(
                    Span(
                        name=sp.get("name", ""),
                        trace_id=sp.get("traceId", ""),
                        span_id=sp.get("spanId", ""),
                        parent_span_id=sp.get("parentSpanId") or None,
                        kind=sp.get("kind", "SPAN_KIND_UNSPECIFIED"),
                        status=status,
                        attributes=decode_attributes(sp.get("attributes", [])),
                        resource=resource,
                        events=[
                            {
                                "name": ev.get("name", ""),
                                "attributes": decode_attributes(ev.get("attributes", [])),
                            }
                            for ev in sp.get("events", [])
                        ],
                    )
                )
    return out


def _number(point: Dict[str, Any]) -> Any:
    if "asInt" in point:
        return int(point["asInt"])
    if "asDouble" in point:
        return float(point["asDouble"])
    return None


def _points_of(request: Dict[str, Any]) -> List[MetricPoint]:
    out: List[MetricPoint] = []
    for rm in request.get("resourceMetrics", []):
        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                name = metric.get("name", "")
                for kind in ("sum", "gauge", "histogram"):
                    if kind not in metric:
                        continue
                    for dp in metric[kind].get("dataPoints", []):
                        attrs = decode_attributes(dp.get("attributes", []))
                        if kind == "histogram":
                            value: Any = {
                                "count": int(dp.get("count", 0)),
                                "sum": float(dp.get("sum", 0.0)),
                            }
                        else:
                            value = _number(dp)
                        out.append(
                            MetricPoint(
                                name=name,
                                attributes=attrs,
                                value=value,
                                kind=kind,
                                start_ns=int(dp.get("startTimeUnixNano", 0)),
                            )
                        )
    return out


def _logs_of(request: Dict[str, Any]) -> List[LogRecord]:
    out: List[LogRecord] = []
    for rl in request.get("resourceLogs", []):
        for sl in rl.get("scopeLogs", []):
            for rec in sl.get("logRecords", []):
                attrs = decode_attributes(rec.get("attributes", []))
                body = decode_any_value(rec.get("body", {})) if rec.get("body") else None
                # The SDK writes the event name to the proto field on recent
                # releases and to the ``event.name`` attribute on older ones.
                event_name = rec.get("eventName") or attrs.get("event.name")
                out.append(
                    LogRecord(
                        body=body,
                        severity=rec.get("severityText", ""),
                        event_name=event_name,
                        attributes=attrs,
                        trace_id=rec.get("traceId") or None,
                        span_id=rec.get("spanId") or None,
                    )
                )
    return out


class OtlpReceiver:
    """``http://127.0.0.1:<port>`` for a ``type: otlp`` backend entry."""

    def __init__(self) -> None:
        from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
        from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

        self._messages = {
            "/v1/traces": trace_service_pb2.ExportTraceServiceRequest,
            "/v1/metrics": metrics_service_pb2.ExportMetricsServiceRequest,
            "/v1/logs": logs_service_pb2.ExportLogsServiceRequest,
        }
        self._lock = threading.Lock()
        self.spans: List[Span] = []
        self.points: List[MetricPoint] = []
        # Every point since the receiver started; ``clear`` keeps it, so
        # ``increase`` can find a series' previous point from an earlier turn.
        self.history: List[MetricPoint] = []
        self.logs: List[LogRecord] = []
        self.requests: List[str] = []
        self.bad_requests: List[str] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                raw = _read_body(self)
                path = self.path.split("?", 1)[0]
                try:
                    receiver._ingest(path, raw, self.headers.get("Content-Type", ""))
                except Exception as exc:  # a decode failure is a test failure, not a 500
                    with receiver._lock:
                        receiver.bad_requests.append(f"{path}: {exc!r}")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="e2e-otlp-receiver", daemon=True
        )
        self._thread.start()

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def _ingest(self, path: str, raw: bytes, content_type: str) -> None:
        from google.protobuf.json_format import MessageToDict

        message_cls = self._messages.get(path)
        if message_cls is None:
            raise ValueError(f"unexpected OTLP path {path}")
        if "json" in content_type:
            request = json.loads(raw.decode("utf-8"))
        else:
            msg = message_cls()
            msg.ParseFromString(raw)
            request = MessageToDict(msg, preserving_proto_field_name=False)
        with self._lock:
            self.requests.append(path)
            if path == "/v1/traces":
                self.spans.extend(_spans_of(request))
            elif path == "/v1/metrics":
                points = _points_of(request)
                self.points.extend(points)
                self.history.extend(points)
            else:
                self.logs.extend(_logs_of(request))

    def increase(self, point: MetricPoint) -> Any:
        """What a cumulative sum or histogram ``point`` added since the previous
        point of its series (same name, attributes and start time), or since
        the series started. A Hermes gateway serves several ``hermes chat``
        processes, so its series span sessions (#637)."""
        with self._lock:
            earlier = self.history[: next(i for i, p in enumerate(self.history) if p is point)]
        previous = next(
            (
                p
                for p in reversed(earlier)
                if (p.name, p.attributes, p.start_ns)
                == (point.name, point.attributes, point.start_ns)
            ),
            None,
        )
        if point.kind == "histogram":
            before = previous.value if previous else {"count": 0, "sum": 0.0}
            return {k: point.value[k] - before[k] for k in ("count", "sum")}
        return point.value - (previous.value if previous else 0)

    def clear(self) -> None:
        with self._lock:
            self.spans.clear()
            self.points.clear()
            self.logs.clear()
            self.requests.clear()
            self.bad_requests.clear()

    def wait_for(
        self, predicate: Callable[["OtlpReceiver"], bool], timeout_s: float = 10.0
    ) -> bool:
        """Poll until ``predicate(self)`` holds or ``timeout_s`` passes."""
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                if predicate(self):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def spans_for_session(self, session_id: str) -> List[Span]:
        with self._lock:
            return [s for s in self.spans if s.attributes.get("hermes.session_id") == session_id]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
