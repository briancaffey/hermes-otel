---
sidebar_position: 16
title: "Comet Opik"
description: "Comet Opik as a Hermes trace backend — OTLP/HTTP traces into a self-hosted Opik or Comet cloud, with threads per session, span types, model, usage and cost per call."
---

# Comet Opik

[Opik](https://github.com/comet-ml/opik) (Apache-2.0) is Comet's LLM evaluation and tracing platform. It ingests OTLP traces on a vendor path and maps both conventions the plugin emits (`gen_ai.*` and OpenInference `llm.*`) onto its own model: a thread per Hermes session, a type per span (`llm`, `tool`, `general`), model, provider, usage and estimated cost on every API call, and trace-level input and output.

**Signals:** traces only. **Deployment:** self-hosted (docker compose, seven containers, about 2.1 GB resident), the official Helm chart, or Comet cloud. **Cost:** open source; the cloud has a free tier.

:::tip Verified live
One real `hermes` turn through `type: opik` into the bundled compose stack (Opik 2.2.90, 2026-10-08) exported 3 spans in one `SUCCESS` batch; Opik's API returned the trace in the project named by the `projectName` header, `thread_id` = the Hermes session id, three spans typed `general` / `llm` / `llm` with model, provider and token usage. See [Verified behavior](#verified-behavior).
:::

## Quick start

### Self-hosted

```bash
docker compose -f docker-compose/opik/docker-compose.yaml up -d    # first start ≈ 1 min (migrations)
```

```yaml
backends:
  - type: opik
    endpoint: http://localhost:5173
    project: hermes-agent          # optional; Opik's "Default Project" otherwise
```

The startup banner reads `✓ Comet Opik at http://localhost:5173/api/v1/private/otel/v1/traces (traces only)`. Open `http://localhost:5173` (no login) → Projects → hermes-agent → Traces.

### Comet cloud

```bash
export OPIK_API_KEY="..."          # Comet API key
export OPIK_WORKSPACE="my-team"
```

```yaml
backends:
  - type: opik                      # endpoint defaults to https://www.comet.com/opik
    api_key_env: OPIK_API_KEY
    workspace: my-team
    project: hermes-agent
```

## Why a dedicated `type: opik`

The traces URL is a vendor path (`/api/v1/private/otel/v1/traces`), the cloud needs three headers, two of which name a workspace and a project rather than a secret, and the API key travels as a **bare** `Authorization` value, not `Bearer`. The explicit type:

1. Completes the URL from the base (`http://localhost:5173`, the SDK's `…/api` form, or the full traces URL are all accepted) and defaults it to Comet cloud.
2. Sends `Authorization: <key>` from `api_key` / `api_key_env` / `OTEL_OPIK_API_KEY` / `OPIK_API_KEY`, `Comet-Workspace` from `workspace` / `OPIK_WORKSPACE`, `projectName` from `project` / `project_env` / `OPIK_PROJECT_NAME`; each is omitted when nothing resolves, which is what a self-hosted stack wants.
3. Defaults metrics and logs **off**: only `/traces` exists under the OTLP prefix.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | Opik base URL (`http://localhost:5173`), the SDK's `…/api` form, or a full traces URL. Default `https://www.comet.com/opik`. Also via `OTEL_OPIK_ENDPOINT` / `OPIK_URL_OVERRIDE`. |
| `api_key` / `api_key_env` | Comet API key → bare `Authorization` header. Cloud only. Prefer `api_key_env`. |
| `workspace` | Comet workspace → `Comet-Workspace` header. Cloud only. Falls back to `OPIK_WORKSPACE`. |
| `project` / `project_env` | Opik project → `projectName` header. Falls back to `OPIK_PROJECT_NAME`; "Default Project" when unset. |
| `metrics` / `logs` | Default **off**. |
| `headers` | Extra headers, merged on top (a user `Authorization` wins). |

## Single backend (env vars)

```bash
export OTEL_OPIK_API_KEY="..."     # cloud: enables Opik, default host
export OPIK_WORKSPACE="my-team"
# or, self-hosted:
export OTEL_OPIK_ENDPOINT=http://localhost:5173
export OTEL_PROJECT_NAME=hermes-otel-opik
```

The Opik SDK's own variables (`OPIK_API_KEY`, `OPIK_URL_OVERRIDE`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME`) fill in values but never switch export on by themselves.

## Verified behavior

Read back from the self-hosted stack after one turn:

```bash
curl -s 'http://localhost:5173/api/v1/private/traces?project_name=hermes-agent&size=5'
curl -s 'http://localhost:5173/api/v1/private/spans?project_name=hermes-agent&size=20'
```

| | Observed |
|---|---|
| Plugin | `export Comet Opik: 3 span(s) -> SUCCESS`; no metrics reader (`(traces only)`); logs to the live store only |
| Trace | one, `thread_id` = the Hermes session id, `span_count: 3` |
| Spans | `agent` → `type: general`; `api.<model>` and `llm.<model>` → `type: llm`; `model` and `provider` filled from `gen_ai.request.model` / `gen_ai.provider.name`; `prompt_tokens` / `completion_tokens` on the `agent` and `api.*` spans |
| Cost | `null` for this run: the model (`nvidia/nemotron-3-nano-omni`) is not in Opik's price table; OpenAI and Anthropic models get `total_estimated_cost` |

## Caveats

- **The root's token roll-up is not sent to Opik.** Opik sums usage over every span, and the plugin's `agent` span carries the turn's roll-up, so before 1.24 the trace showed twice the prompt tokens of its API calls (28 560 for a turn whose single API call used 14 280) and `total_estimated_cost` doubled the same way ([#327](https://github.com/briancaffey/hermes-otel/issues/327)). The `opik` type now defaults to `root_usage: false`: the exporter bound to this entry rebuilds the `agent` / `cron` span without `gen_ai.usage.*` / `llm.token_count.*` (`hermes.cost.*` and everything else stay), so Opik's trace totals equal the sum of the `api.*` spans. Every other backend in the same config still receives the roll-up. Set `root_usage: true` on the entry to send it anyway.
- **Traces only.** `/metrics` and `/logs` under the OTLP prefix answer 404; token, tool and cost **metrics** need a second backend ([multi-backend](/backends/multi-backend)).
- The bundled stack drops Opik's Python evaluator backend, guardrails and demo data; online evaluation rules that run Python need the upstream `python-backend`.
- The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `opik`; use Opik's UI or its REST API.

## Troubleshooting

- **`401`** — cloud without a key or workspace; set `api_key_env` and `workspace`.
- **Traces land in "Default Project"** — set `project` (or `OPIK_PROJECT_NAME`); Opik creates the project on first use.
- **`404` on every export** — the base URL is wrong (the plugin needs the Opik base or its `/api` form, not the frontend's `/projects` page), or the backend is still running migrations (`docker compose … ps` shows `hermes-otel-opik-backend` healthy after about a minute).
