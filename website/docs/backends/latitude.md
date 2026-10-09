---
sidebar_position: 19
title: "Latitude"
description: "Latitude as a Hermes trace backend — OTLP/HTTP traces into a self-hosted Latitude ingest service or Latitude cloud, following the OpenTelemetry GenAI semantic conventions."
---

# Latitude

[Latitude](https://github.com/latitude-dev/latitude-llm) (MIT) is a prompt-engineering and agent-observability platform that states it follows the OpenTelemetry Semantic Conventions for Generative AI. Its ingest service takes OTLP traces with a bearer key and a mandatory project header, and stores each span with operation, provider, model, token counts, cost and tool fields derived from the `gen_ai.*` attributes the plugin emits.

**Signals:** traces only. **Deployment:** self-hosted (upstream's single-host stack: twelve containers plus Mailpit for the magic-link emails, about 18 GB of images), the in-repo Helm chart, or Latitude cloud. **Cost:** open source; the cloud has a free tier.

:::tip Verified live
One real `hermes` turn through `type: latitude` into the bundled compose stack (2026-10-09): two span batches `SUCCESS`, and Latitude's ClickHouse then held 5 spans, 1 trace and 1 session for the project named in the header. See [Verified behavior](#verified-behavior).
:::

## Quick start

### Self-hosted

```bash
docker compose -f docker-compose/latitude/docker-compose.yaml up -d    # pulls ≈ 18 GB; first start ≈ 2 min
```

Open `http://localhost:3000`, enter any email, and open the magic link from Mailpit at `http://localhost:8025`. The profile step creates an organisation and a first project (its slug is in the URL, for example `hermes-otel-s-project`). The API key is under Settings → Organization → Keys; clicking the masked key copies it.

```yaml
backends:
  - type: latitude
    endpoint: http://localhost:3002           # the ingest service, not the UI on 3000
    api_key_env: LATITUDE_API_KEY
    project: hermes-otel-s-project            # the project slug
```

The startup banner reads `✓ Latitude at http://localhost:3002/v1/traces (traces only)`.

### Latitude cloud

```bash
export LATITUDE_API_KEY="..."
export LATITUDE_PROJECT="my-project"
```

```yaml
backends:
  - type: latitude                            # endpoint defaults to https://ingest.latitude.so
    api_key_env: LATITUDE_API_KEY
    project_env: LATITUDE_PROJECT
```

## Why a dedicated `type: latitude`

Latitude rejects spans that name no project, needs a bearer key, and ingests on a service separate from its UI. The explicit type:

1. Requires both the key (`api_key` / `api_key_env` / `OTEL_LATITUDE_API_KEY` / `LATITUDE_API_KEY`) and the project slug (`project` / `project_env` / `LATITUDE_PROJECT`), sends `Authorization: Bearer <key>` and `X-Latitude-Project: <slug>`, and skips the entry with a clear message when either is missing.
2. Defaults `endpoint` to the cloud ingest and normalises a self-hosted base to `/v1/traces`.
3. Defaults metrics and logs **off**: the ingest service has only `/v1/traces` and health routes.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | Ingest base: `http://localhost:3002` for the bundled stack. Default `https://ingest.latitude.so`. Also via `OTEL_LATITUDE_ENDPOINT` / `LATITUDE_INGEST_URL`. |
| `api_key` / `api_key_env` | **Required.** API key → `Authorization: Bearer`. Prefer `api_key_env`. |
| `project` / `project_env` | **Required.** Project slug → `X-Latitude-Project`. Falls back to `LATITUDE_PROJECT`. |
| `metrics` / `logs` | Default **off**. |
| `headers` | Extra headers, merged on top (a user `X-Latitude-Project` wins). |

## Single backend (env vars)

```bash
export OTEL_LATITUDE_API_KEY="..."           # enables Latitude together with the project
export LATITUDE_PROJECT="my-project"
# export OTEL_LATITUDE_ENDPOINT=http://localhost:3002
export OTEL_PROJECT_NAME=hermes-otel-latitude
```

`LATITUDE_API_KEY`, `LATITUDE_PROJECT` and `LATITUDE_INGEST_URL` fill in values but never switch export on by themselves; a key without a project is skipped.

## Verified behavior

Read back from the stack's ClickHouse after one turn:

```bash
docker exec latitude-clickhouse-1 clickhouse-client -q \
  "SELECT table, sum(rows) FROM system.parts WHERE active AND database = 'latitude' GROUP BY table"
```

| | Observed |
|---|---|
| Plugin | `✓ Latitude at http://localhost:3002/v1/traces (traces only)`; `export Latitude: 4 span(s) -> SUCCESS`, `1 span(s) -> SUCCESS`; no metrics reader; logs to the live store only |
| ClickHouse | `latitude.spans` 5, `latitude.traces` 1, `latitude.sessions` 1 |

Latitude's `spans` table carries `operation`, `provider`, `model`, `tokens_input` / `tokens_output` / `tokens_total`, `cost_*`, `tool_name` / `tool_input` / `tool_output` and the `input_messages` / `output_messages` columns, all populated from the `gen_ai.*` attributes; the per-span values were not inspected in this pass.

## Caveats

- **Traces only.** Token, tool and cost **metrics** need a second backend ([multi-backend](/backends/multi-backend)); logs stay in the live store.
- **The heaviest stack in the repo.** About 18 GB of images and twelve containers; the bundled compose keeps upstream's example secrets in `latitude.env`, so regenerate `LAT_MASTER_ENCRYPTION_KEY` and `LAT_BETTER_AUTH_SECRET` before exposing it. Ports 3000–3002 are upstream's and 3000 collides with Langfuse and Grafana.
- **Latitude ships its own Hermes plugin** (`latitude-telemetry-hermes`); running both double-traces every turn.
- **The API key is encrypted at rest**, so unlike the other stacks it cannot be read from Postgres; copy it from the Keys page.
- The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `latitude`; use Latitude's UI.

## Troubleshooting

- **`latitude requires project`** — set `project` to the slug shown in the project's URL (`/projects/<slug>/…`).
- **`401`** — wrong key, or an organisation key used against another organisation's project.
- **`4xx` with "project"** in the ingest log — the slug does not exist in the organisation the key belongs to.
