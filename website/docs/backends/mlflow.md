---
sidebar_position: 15
title: "MLflow"
description: "MLflow 3.6+ as a Hermes trace backend — OTLP/HTTP traces into an MLflow experiment, with MLflow's GenAI Traces UI, token and cost roll-ups."
---

# MLflow

[MLflow](https://mlflow.org) (Apache-2.0) ingests OpenTelemetry traces on its tracking server since 3.6 and shows them in the GenAI **Traces** view of an experiment, with request and response previews, its own token-usage and cost roll-ups, and sessions grouped by the Hermes session id. MLflow's docs already describe tracing Hermes Agent with this plugin; `type: mlflow` makes that a two-line entry.

**Signals:** traces only. **Deployment:** self-hosted (one container with SQLite is enough), the official Helm chart, or Databricks-managed MLflow. **Cost:** open source, no account.

:::tip Verified live
One real `hermes` turn through `type: mlflow` into the bundled compose stack (MLflow 3.16.1, 2026-10-08) exported 5 spans in two batches, both `SUCCESS`; MLflow's search API returned the trace with `state: OK`, `mlflow.trace.session` set to the Hermes session id and `mlflow.trace.tokenUsage` computed from the span attributes (28 673 input, 125 output tokens). See [Verified behavior](#verified-behavior).
:::

## Quick start

Bring up the bundled stack (host port 5001, since macOS AirPlay Receiver owns 5000):

```bash
docker compose -f docker-compose/mlflow/docker-compose.yaml up -d
```

Then declare the backend:

```yaml
backends:
  - type: mlflow
    endpoint: http://localhost:5001
    # experiment_id: "0"        # default: the built-in Default experiment
```

The startup banner reads `✓ MLflow at http://localhost:5001/v1/traces (traces only)`. Open `http://localhost:5001`, pick the experiment and its **Traces** tab.

For a tracking server elsewhere, use its base URL (`http://<host>:5000`). To land traces in a specific experiment, create it in the UI and set `experiment_id` to its id.

## Why a dedicated `type: mlflow`

MLflow rejects any OTLP request without an `x-mlflow-experiment-id` header (400), and it has no metrics or logs routes. The explicit type:

1. Sends `x-mlflow-experiment-id` from `experiment_id` (default `"0"`), or from `experiment_id_env` / `OTEL_MLFLOW_EXPERIMENT_ID` / `MLFLOW_EXPERIMENT_ID`.
2. Adds `X-MLFLOW-WORKSPACE` when `workspace` is set.
3. Sends a tracking token as `Authorization: Bearer <token>` when one resolves from `api_key` / `api_key_env` / `OTEL_MLFLOW_API_KEY` / `MLFLOW_TRACKING_TOKEN`; omits the header otherwise.
4. Normalises `endpoint` to `/v1/traces` and **defaults metrics and logs off**, so nothing is sprayed at routes that answer 404.

Before this, the same setup needed `type: otlp` with a hand-written header and two signal toggles.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** Tracking server base URL (`http://localhost:5001` for the bundled stack). Also via `OTEL_MLFLOW_ENDPOINT`. |
| `experiment_id` / `experiment_id_env` | Experiment that receives the traces, sent as `x-mlflow-experiment-id`. Default `"0"`. |
| `workspace` | Optional, sent as `X-MLFLOW-WORKSPACE`. |
| `api_key` / `api_key_env` | Optional tracking token → `Authorization: Bearer`. Prefer `api_key_env`. |
| `metrics` / `logs` | Default **off**. Set `true` only if a collector in front of MLflow takes those signals. |
| `headers` | Extra headers, merged on top of the generated ones (a user `x-mlflow-experiment-id` wins). |

Requirements on the MLflow side: version 3.6 or newer, and a **SQL backend store** (SQLite qualifies); the plain file store refuses OTLP.

## Single backend (env vars)

```bash
export OTEL_MLFLOW_ENDPOINT=http://localhost:5001
# export MLFLOW_EXPERIMENT_ID=3            # optional, default 0
# export MLFLOW_TRACKING_TOKEN=...          # optional
export OTEL_PROJECT_NAME=hermes-otel-mlflow
```

`OTEL_MLFLOW_ENDPOINT` alone opts in; the experiment id and token never do on their own.

## Verified behavior

Read back with MLflow's search API after one turn:

```bash
curl -s -X POST http://localhost:5001/api/3.0/mlflow/traces/search -H 'content-type: application/json' \
  -d '{"locations":[{"type":"MLFLOW_EXPERIMENT","mlflow_experiment":{"experiment_id":"0"}}],"max_results":5}'
```

| | Observed |
|---|---|
| Plugin exports | `export MLflow: 4 span(s) -> SUCCESS`, `export MLflow: 1 span(s) -> SUCCESS`; no metrics reader created (`(traces only)` in the banner), logs went to the live store only |
| Trace | one, `state: OK`, `mlflow.trace.session` = the Hermes session id |
| Token usage | `mlflow.trace.tokenUsage` = `{"input_tokens": 28673, "output_tokens": 125, …}`, computed by MLflow from the `gen_ai.usage.*` span attributes |
| Cost | empty for this run: the model (`nvidia/nemotron-3-nano-omni`) is not in MLflow's price table; OpenAI and Anthropic models get `mlflow.trace.cost` |

## Caveats

- **Traces only.** `/v1/metrics` and `/v1/logs` answer 404; keep the defaults. Token, tool and cost **metrics** need a second backend ([multi-backend](/backends/multi-backend)).
- **The experiment must exist.** An unknown `experiment_id` is rejected by the server; `0` always exists.
- **Memory.** The default four uvicorn workers idle at about 1.2 GB; the image is 1.3 GB.
- The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `mlflow`; use MLflow's UI or its search API.

## Troubleshooting

- **`400 Bad Request` on every export** — the experiment id is missing or wrong. The plugin always sends the header; check the value exists in the UI.
- **`404` on `/v1/traces`** — MLflow older than 3.6, or a file-store server; upgrade and use `--backend-store-uri sqlite:///…` (or Postgres).
- **`mlflow requires endpoint`** — no `endpoint:` and no `OTEL_MLFLOW_ENDPOINT`.
