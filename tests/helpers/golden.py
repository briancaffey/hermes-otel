"""Golden-fixture plumbing (#407): a three-signal in-memory plugin and the
normaliser that turns what it exported into a deterministic JSON document.

Everything observed stays literal except what cannot be stable between two
runs or two machines: ids, times, durations, the process identity.
"""

from __future__ import annotations

import contextlib
import dataclasses
import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import patch

from hermes_otel import log_handler as lh
from hermes_otel import plugin_config as pc
from hermes_otel import redaction
from hermes_otel import tracer as tracer_mod
from hermes_otel.plugin_config import HermesOtelConfig
from hermes_otel.tracer import HermesOTelPlugin, metric_views

GOLDEN_ROOT = Path(__file__).resolve().parent.parent / "golden"

# Attribute keys whose value is a process identity, never stable.
IDENTITY_KEYS = frozenset(
    {
        "service.instance.id",
        "process.pid",
        "host.name",
        "service.version",
        "hermes.profile",
        "hermes.plugin.version",
        "weave.agent.version",  # the package version: changes every release
    }
)
# The SDK's self-instrumentation (``otel.sdk.*``) is not the plugin's telemetry
# and names the reader object, so it is left out of every recording.
_SDK_OWN_METRIC = "otel.sdk."
# A measured duration, a clock reading, or a count of real time.
_TIME_KEY = re.compile(r"(duration|_at$|timestamp|\.time$|\.started$|\.ended$)")
_OVER_SECONDS = re.compile(r"over \d+(\.\d+)?s")


def config_for(mode: str, **over: Any) -> HermesOtelConfig:
    """A config the way the loader would build it for ``content_capture: mode``
    (the hooks read the derived booleans, so they must agree with the mode)."""
    values: Dict[str, Any] = {"content_capture": mode}
    pc._reconcile_content_capture(values)
    values.update(
        emit_genai_metrics=True,
        dashboard_live=False,
        force_flush_on_session_end=False,
        host_metrics=False,
        capture_logs=False,
    )
    values.update(over)
    return HermesOtelConfig(**values)


@dataclasses.dataclass
class Pipeline:
    plugin: HermesOTelPlugin
    spans: Any  # InMemorySpanExporter
    metrics: Any  # InMemoryMetricReader
    logs: Any  # InMemoryLogExporter


@contextlib.contextmanager
def three_signal_plugin(config: HermesOtelConfig) -> Iterator[Pipeline]:
    """A ``HermesOTelPlugin`` exporting spans, metrics and logs in memory,
    installed as the hooks' singleton for the duration of the block."""
    from opentelemetry.sdk._logs import export as _log_export
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    resource = Resource.create({"service.name": "hermes-agent"})
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    plugin = HermesOTelPlugin(config=config)
    plugin.profile_name = "golden"
    plugin.tracer = provider.get_tracer("hermes-otel-plugin")
    plugin._tracer_provider = provider
    plugin._initialized = True

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        views=metric_views(config.metrics_histogram),
    )
    plugin._meter_provider = meter_provider
    plugin._meter = meter_provider.get_meter("hermes-otel-plugin")
    plugin._create_metric_instruments()

    # The 1.35 floor spells it InMemoryLogExporter; newer SDKs deprecate that name.
    exporter_cls = getattr(_log_export, "InMemoryLogRecordExporter", None) or getattr(
        _log_export, "InMemoryLogExporter"
    )
    log_exporter = exporter_cls()
    resolved, redactor = redaction._hermes_resolved, redaction._hermes_redactor
    redaction._hermes_resolved, redaction._hermes_redactor = True, None
    with patch.object(
        lh,
        "build_log_processors",
        return_value=[(SimpleLogRecordProcessor(log_exporter), object())],
    ):
        plugin._init_logs_pipeline(resource, [])

    previous = tracer_mod._tracer
    tracer_mod._tracer = plugin
    tracer_mod._PARENT_STACK.set(None)
    try:
        yield Pipeline(plugin, span_exporter, metric_reader, log_exporter)
    finally:
        try:
            plugin.spans.end_all()
        except Exception:
            pass
        tracer_mod._tracer = previous
        tracer_mod._PARENT_STACK.set(None)
        try:
            lh.uninstall_handler(None)
        except Exception:
            pass
        meter_provider.shutdown()
        provider.shutdown()
        redaction._hermes_resolved, redaction._hermes_redactor = resolved, redactor


# ── normalisation ──────────────────────────────────────────────────────


class _Ids:
    def __init__(self) -> None:
        self.traces: Dict[int, str] = {}
        self.spans: Dict[int, str] = {}

    def trace(self, trace_id: Optional[int]) -> Optional[str]:
        if not trace_id:
            return None
        return self.traces.setdefault(trace_id, f"T{len(self.traces) + 1}")

    def span(self, span_id: Optional[int]) -> Optional[str]:
        if not span_id:
            return None
        return self.spans.setdefault(span_id, f"S{len(self.spans) + 1}")


def _value(key: str, v: Any) -> Any:
    if key in IDENTITY_KEYS:
        return f"<{key}>"
    if isinstance(v, (int, float)) and not isinstance(v, bool) and _TIME_KEY.search(key):
        return "<num>"
    if isinstance(v, (list, tuple)):
        return [x if isinstance(x, (int, float, bool)) else str(x) for x in v]
    return v


def _attrs(attributes: Any) -> Dict[str, Any]:
    return {k: _value(k, v) for k, v in sorted(dict(attributes or {}).items())}


def _status(span: Any) -> Dict[str, Any]:
    try:
        code = span.status.status_code.name
    except Exception:
        code = "UNSET"
    out: Dict[str, Any] = {"code": code}
    desc = getattr(span.status, "description", None)
    if desc:
        out["description"] = desc
    return out


def normalize(spans: List[Any], metrics_data: Any, logs: List[Any]) -> Dict[str, Any]:
    """The exported telemetry as a JSON document with stable ids and times."""
    ids = _Ids()
    ordered = sorted(spans, key=lambda s: (s.start_time or 0, s.name))
    out_spans = []
    for s in ordered:
        ctx = s.get_span_context()
        parent = getattr(s, "parent", None)
        out_spans.append(
            {
                "name": s.name,
                "trace": ids.trace(ctx.trace_id),
                "span": ids.span(ctx.span_id),
                "parent": ids.span(parent.span_id) if parent is not None else None,
                "kind": getattr(s.kind, "name", str(s.kind)),
                "status": _status(s),
                "attributes": _attrs(s.attributes),
                "events": [
                    {"name": e.name, "attributes": _attrs(e.attributes)} for e in (s.events or [])
                ],
                "links": [
                    {
                        "trace": ids.trace(link.context.trace_id),
                        "span": ids.span(link.context.span_id),
                        "attributes": _attrs(link.attributes),
                    }
                    for link in (s.links or [])
                ],
            }
        )

    points: List[Dict[str, Any]] = []
    if metrics_data is not None:
        for rm in metrics_data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    if metric.name.startswith(_SDK_OWN_METRIC):
                        continue
                    for dp in metric.data.data_points:
                        row: Dict[str, Any] = {
                            "name": metric.name,
                            "unit": metric.unit or "",
                            "attributes": _attrs(dp.attributes),
                        }
                        if hasattr(dp, "bucket_counts"):
                            row["count"] = int(dp.count)
                            if _TIME_KEY.search(metric.name):
                                # Measured time: the sum and which bucket it fell in
                                # depend on the machine; the point count does not.
                                row["sum"] = "<num>"
                                row["bucket_counts"] = "<measured>"
                            else:
                                row["sum"] = dp.sum
                                row["bucket_counts"] = [int(c) for c in dp.bucket_counts]
                        else:
                            row["value"] = dp.value
                        points.append(row)
    points.sort(key=lambda r: (r["name"], json.dumps(r["attributes"], sort_keys=True)))

    out_logs = []
    for rec in logs:
        r = rec.log_record
        attrs = _attrs(r.attributes)
        body = r.body if isinstance(r.body, str) else json.dumps(r.body, sort_keys=True)
        out_logs.append(
            {
                "event_name": getattr(r, "event_name", None) or attrs.get("event.name"),
                "severity": r.severity_text,
                "body": _OVER_SECONDS.sub("over <s>", body),
                "attributes": attrs,
                "trace": ids.trace(r.trace_id),
                "span": ids.span(r.span_id),
            }
        )
    out_logs.sort(
        key=lambda r: (r["trace"] or "", r["span"] or "", r["event_name"] or "", r["body"])
    )
    return {"spans": out_spans, "metrics": points, "logs": out_logs}


def dumps(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def diff(expected: str, actual: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"{name} (golden)",
            tofile=f"{name} (this run)",
            n=3,
        )
    )
