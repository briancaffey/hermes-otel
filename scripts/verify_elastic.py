"""Smoke check for the `elastic` backend against the local compose stack.

Bring the stack up first:

    docker compose -f docker-compose/elastic/docker-compose.yml up -d
    uv run --extra dev python scripts/verify_elastic.py

The script resolves the backend exactly like the tracer would, emits one
session's worth of OTLP signals (spans, delta metrics, a log record) through
the EDOT Collector, force-flushes, then queries Elasticsearch `_count` and
`_search` to prove the data landed — plus a negative control (a time range
before the export must return zero hits).

Uses only stdlib + the OTel SDK the dev extra already installs; no network
access beyond 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EDOT = os.getenv("HERMES_OTEL_ELASTIC_EDOT", "http://127.0.0.1:14319")
ES = os.getenv("HERMES_OTEL_ELASTIC_ES", "http://127.0.0.1:19201")
SERVICE = f"hermes-otel-smoke-{int(time.time())}"

from hermes_otel import backends  # noqa: E402


def _req(url: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode()
    return json.loads(text) if text else {}


def _count(query: dict, index: str = "_all") -> int:
    return _req(f"{ES}/{index}/_count", payload=query)["count"]


def main() -> int:
    rb = backends.resolve(
        backends.BackendConfig(
            type="elastic",
            endpoint=EDOT,
            dataset="hermes_smoke",
            namespace="hermes",
        )
    )
    print(f"resolved: {rb.type} at {rb.endpoint}, temporality={rb.metrics_temporality}")
    assert "Authorization" not in rb.headers, "local EDOT collector must not get an auth header"

    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    res = Resource.create({"service.name": SERVICE, **(rb.resource_attributes or {})})
    headers = dict(rb.headers or {})
    tp = TracerProvider(resource=res)
    tp.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{EDOT}/v1/traces", headers=headers))
    )
    tracer = tp.get_tracer("smoke")

    mp = MeterProvider(
        resource=res,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{EDOT}/v1/metrics", headers=headers)
            )
        ],
    )
    meter = mp.get_meter("smoke")
    counter = meter.create_counter("hermes.smoke.spans")

    lp = LoggerProvider(resource=res)
    lp.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{EDOT}/v1/logs", headers=headers))
    )

    import logging

    logger = logging.getLogger("hermes_smoke")
    logger.setLevel(logging.INFO)
    logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))

    with tracer.start_as_current_span("hermes-session") as s:
        s.set_attribute("hermes.session_id", "smoke-1")
        counter.add(1)
        with tracer.start_as_current_span("tool-call"):
            logger.info("hermes-otel elastic smoke log record")

    tp.force_flush()
    mp.force_flush()
    lp.force_flush()
    mp.shutdown()
    lp.shutdown()
    tp.shutdown()
    print("export flushed")

    # Per-signal verification: count docs in each data stream
    # (`<type>-<dataset>-<namespace>`) filtered by this run's unique
    # service.name. Each signal must land in its own stream.
    svc_filter = {"term": {"service.name": SERVICE}}
    signals = {}
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(3)
        for sig in ("traces", "metrics", "logs"):
            try:
                signals[sig] = _count(
                    {"query": {"bool": {"filter": [svc_filter]}}}, index=f"{sig}-hermes_smoke*"
                )
            except urllib.error.HTTPError:
                signals[sig] = 0  # stream not created yet
        if all(signals.get(s, 0) > 0 for s in ("traces", "metrics", "logs")):
            break
    print("per-signal counts:", signals)
    missing = [s for s in ("traces", "metrics", "logs") if signals.get(s, 0) == 0]
    assert not missing, f"signals {missing} never landed in their data streams: {signals}"

    # Content check: the exported span must be in the traces stream.
    sample = _req(
        f"{ES}/traces-hermes_smoke*/_search",
        payload={"query": {"bool": {"filter": [svc_filter]}}, "size": 1},
    )
    hit = sample["hits"]["hits"][0]
    print("sample span index:", hit["_index"])
    assert hit["_index"].startswith(".ds-traces-hermes_smoke."), hit["_index"]

    # Negative control: the same query filtered on a service name that was
    # never exported must return zero — proves the term filter is actually
    # filtering and the counts above are not wildcard artifacts.
    absent = _count(
        {"query": {"bool": {"filter": [{"term": {"service.name": SERVICE + "-absent"}}]}}},
        index="*",
    )
    assert absent == 0, f"negative control failed: {absent} docs for a service never exported"
    print("negative control: 0 docs for absent service OK")
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
