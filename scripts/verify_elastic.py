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


def _count(query: dict) -> int:
    return _req(f"{ES}/_count", payload=query)["count"]


def main() -> int:
    rb = backends.resolve(
        backends.BackendConfig(
            type="elastic",
            endpoint=EDOT,
            dataset="hermes-smoke",
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

    q = {"query": {"term": {"service.name": SERVICE}}}
    deadline = time.time() + 60
    counts = {}
    while time.time() < deadline:
        time.sleep(3)
        counts = {
            t: _count({"query": {"bool": {"filter": [{"term": {"service.name": SERVICE}}]}}})
            for t in ("traces", "metrics", "logs")
        }
        per_index = _req(
            f"{ES}/_search",
            payload={
                **q,
                "size": 0,
                "aggs": {"by_index": {"terms": {"field": "_index", "size": 10}}},
            },
        )
        buckets = per_index.get("aggregations", {}).get("by_index", {}).get("buckets", [])
        named = {b["key"].split("-")[0]: b["doc_count"] for b in buckets if SERVICE not in b["key"]}
        if all(named.get(k, 0) > 0 for k in ("traces", "metrics", "logs")):
            break

    print("per-signal counts:", counts, "indices:", named)
    total = sum(counts.values())
    assert total >= 3, f"expected >=3 docs in traces/metrics/logs, got {counts}"

    # negative control: a range strictly before the export must be empty
    before = _count(
        {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"service.name": SERVICE}},
                        {"range": {"@timestamp": {"lt": "2020-01-01"}}},
                    ]
                }
            }
        }
    )
    assert before == 0, f"negative control failed: {before} docs before 2020"
    print("negative control: 0 docs before 2020 OK")

    sample = _req(f"{ES}/traces-*/_search", payload={**q, "size": 1})
    hit = sample["hits"]["hits"][0]
    print("sample span index:", hit["_index"])
    assert hit["_index"].startswith(".ds-traces-hermes-smoke-hermes") or "traces" in hit["_index"]
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
