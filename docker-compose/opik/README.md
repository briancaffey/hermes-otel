# Comet Opik

[Opik](https://github.com/comet-ml/opik) (Apache-2.0) is one of the most-starred
LLM observability platforms and accepts OTLP/HTTP traces on a vendor path.
Trimmed from upstream's compose (2.2.90) to MySQL, Redis, ZooKeeper, ClickHouse,
MinIO (+ a one-shot bucket job), the Java backend and the nginx frontend.
Dropped: python-backend (code evaluators), guardrails, demo data, and the
self-tracing collector. Plugin type: `opik` (since hermes-otel 1.23, #220).

## Why pick it
- The best fit of the #232 batch for Hermes traces: `thread_id` = session id,
  per-span `type` (`llm` / `tool` / `general`), model, provider, usage and
  `total_estimated_cost` on every `api.*` span, trace input/output.
- Explicit mapping rules for both GenAI semconv and OpenInference, so the
  plugin's dual-convention spans map without changes.
- No login locally; the `projectName` header picks the project.

## Start / stop
```bash
docker compose -f docker-compose/opik/docker-compose.yaml up -d     # first start ≈ 1 min (migrations)
docker compose -f docker-compose/opik/docker-compose.yaml down -v
```
UI: http://localhost:5173 (no login).

## Point hermes-otel at it
```yaml
backends:
  - type: opik
    endpoint: http://localhost:5173
    project: hermes-agent            # optional; default is "Default Project"
    # metrics and logs are off by default for this type (no such routes)
```

## Verify
```bash
curl -s 'http://localhost:5173/api/v1/private/traces?project_name=hermes-agent&size=3' | head -c 800
curl -s 'http://localhost:5173/api/v1/private/spans?project_name=hermes-agent&size=10' | head -c 800
```

## Caveats
- Traces only: the OTLP resource declares `/traces` only; `/metrics` and
  `/logs` under the same prefix are 404.
- **Trace-level usage double-counts.** Opik sums usage over every span, and the
  plugin's `agent` span carries the turn's roll-up, so a turn whose two API
  calls total 24 299 prompt tokens shows 48 598 at trace level (cost doubles
  the same way). Per-span numbers are right.
- Online evaluation rules that run Python need the dropped `python-backend`.
- About 2.1 GB resident (ClickHouse ≈ 0.9 GB, MySQL ≈ 0.5 GB, backend ≈ 0.5 GB).
- `OPIK_USAGE_REPORT_ENABLED` is set to `false` here (upstream defaults on).
