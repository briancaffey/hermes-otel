# MLflow

[MLflow](https://mlflow.org) (Apache-2.0) ingests OTLP traces on its tracking
server since 3.6 and renders them in its GenAI Traces UI; MLflow's own docs
already show Hermes traced through this plugin. One container with SQLite,
trimmed from upstream's compose (which adds Postgres and an S3 store). Plugin
type: `mlflow` (since hermes-otel 1.23, #221).

## Why pick it
- MLflow computes its own trace-level token usage and **cost**
  (`mlflow.trace.tokenUsage`, `mlflow.trace.cost`) and groups by
  `mlflow.trace.session` = the Hermes session id.
- Request/response previews on the trace list; attribute translators for GenAI
  semconv, OpenInference and OpenLLMetry.
- If you already use MLflow for experiments, traces land next to them.

## Start / stop
```bash
docker compose -f docker-compose/mlflow/docker-compose.yaml up -d
docker compose -f docker-compose/mlflow/docker-compose.yaml down -v
```
UI: http://localhost:5001 (no login) → Experiments → Default → Traces.

## Point hermes-otel at it
```yaml
backends:
  - type: mlflow
    endpoint: http://localhost:5001
    # experiment_id: "0"     # default; the built-in Default experiment
    # metrics and logs are off by default for this type (no such routes)
```

## Verify
```bash
curl -s -X POST http://localhost:5001/api/3.0/mlflow/traces/search -H 'content-type: application/json' \
  -d '{"locations":[{"type":"MLFLOW_EXPERIMENT","mlflow_experiment":{"experiment_id":"0"}}],"max_results":5}' | head -c 600
```

## Caveats
- Traces only: `/v1/metrics` and `/v1/logs` are 404, so set both signals off.
- The `x-mlflow-experiment-id` header is mandatory (400 without it).
- Host port is **5001**: macOS AirPlay Receiver owns 5000.
- About 1.2 GB resident at idle (MLflow's default four uvicorn workers); the
  image is 1.3 GB.
- SQLite is fine for traces; MLflow refuses OTLP on the plain file store.
