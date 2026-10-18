---
sidebar_position: 13
title: "Agenta"
description: "Send Hermes traces to Agenta — an open-source LLMOps platform with native OTLP ingest, both attribute conventions understood, and a spans query API the plugin dashboard reads."
---

# Agenta

[Agenta](https://github.com/Agenta-AI/agenta) (MIT outside `ee/`) is an open-source LLMOps platform with native OTLP trace ingest. Its server-side adapters read the OpenInference, OpenLLMetry and OTel GenAI conventions, so the plugin's spans arrive with span types, token roll-ups and sessions already filled in, and its `POST /api/spans/query` API lets the plugin dashboard read them back.

**Signals:** traces only. **Deployment:** local (docker compose, eleven containers in the trimmed stack) · Agenta Cloud (US / EU). **Cost:** open source · free cloud tier.

:::tip Verified live
One real `hermes` turn through `type: agenta` into the bundled compose stack (api image v0.122.x, 2026-10-11) stored 7 spans with Agenta's `ag.metrics.tokens` roll-ups, and the dashboard-verify harness read them back through the adapter. See [Verified behavior](#verified-behavior).
:::

## Quick start

```bash
docker compose -f docker-compose/agenta/docker-compose.yaml up -d      # ~1-2 min
export OTEL_AGENTA_API_KEY="$(docker-compose/agenta/mint-api-key.sh)"
```

The script creates a local account (sign-ups are open on a fresh stack), signs in through SuperTokens and mints a project API key with `POST /api/keys`. Then declare the backend:

```yaml
backends:
  - type: agenta
    endpoint: http://localhost:3060          # the type appends /api/otlp/v1/traces
    api_key_env: OTEL_AGENTA_API_KEY
```

The startup banner reads `✓ Agenta at http://localhost:3060/api/otlp/v1/traces (traces only)`. Open http://localhost:3060 (log in as `hermes@example.com` / `HermesOtel-local-1` by default) and the turn is under Observability in the default project.

For Agenta Cloud, leave `endpoint` out and set `region: us` (default) or `eu`; the key comes from the project's settings.

## Why a dedicated `type: agenta`

Export also works through the [generic OTLP](/backends/otlp) type with a hand-written `Authorization` header and `metrics: false`, `logs: false`. Declaring `type: agenta` instead asks the plugin to:

1. Complete `endpoint` to `<origin>/api/otlp/v1/traces` (an origin, `/api`, `/api/otlp` or the full URL all work), or use the cloud host for `region` when there is none.
2. Read the project key from `api_key` / `api_key_env` (falling back to `OTEL_AGENTA_API_KEY`, then the SDK's `AGENTA_API_KEY`) and send it as `Authorization: ApiKey <key>`; skip the entry at startup when none resolves.
3. Keep metrics and logs **off**: Agenta mounts only the traces route.
4. Enable the [dashboard adapter](/dashboard) for the entry.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | Agenta's origin for a self-hosted stack, e.g. `http://localhost:3060`. Also via `OTEL_AGENTA_ENDPOINT` or the SDK's `AGENTA_HOST`. Unset means Agenta Cloud. |
| `region` | `us` (default) or `eu`, for the cloud host when `endpoint` is unset. |
| `api_key` / `api_key_env` | **Required.** A project API key → `Authorization: ApiKey <key>` (fallbacks `OTEL_AGENTA_API_KEY`, `AGENTA_API_KEY`). Prefer `api_key_env`. |
| `metrics` / `logs` / `traces` | Per-signal toggles; metrics and logs default **off** (no such routes). |
| `headers` | Extra headers, merged on top of the generated ones. |
| `ui_url` | Dashboard read path: the project's observability page, `http://<host>/w/<workspace>/p/<project>/observability`, for an "open in" link. |
| `service_name` | Dashboard read path: the service shown on cards and in the detail's resource (Agenta does not store resource attributes). |

No metric-temporality preset (no metrics). The `agent` root keeps its token roll-up: Agenta's cumulative total on the root equalled the sum of the `api.*` spans in the verified turn, so nothing is double-counted.

## Single backend (env vars)

```bash
export OTEL_AGENTA_API_KEY=...                       # alone: Agenta Cloud (US)
export OTEL_AGENTA_ENDPOINT=http://localhost:3060    # self-hosted
export OTEL_PROJECT_NAME=hermes-otel-agenta
```

`AGENTA_API_KEY` alone never switches export on (it is the SDK's variable and often set for other tools); one `OTEL_AGENTA_*` variable is the opt-in.

## What you'll see

Agenta types every span (`agent` for the root, `chat` for `llm.*` and `api.*`, `tool` for `tool.*`), groups the turn under the session the plugin sets (`session.id` → `ag.session.id`), and computes `ag.metrics.tokens` (`cumulative` down the tree, `incremental` per span), `ag.metrics.duration` and, for priced models, `ag.metrics.costs`. The original attributes stay next to Agenta's own under their namespaces (`gen_ai`, `llm`, `hermes`, `openinference`, `input`, `output`).

## Dashboard adapter

The [Hermes dashboard's OTel tab](/dashboard) reads Agenta through `POST /api/spans/query` with the same project key:

- **Search**: a `TracingQuery` with `windowing` (`oldest` / `newest` from the window and the cursor, `order: descending`) and `filtering` conditions: `parent_id is null` for roots-only, `span_name is` / `startswith` for the name and kind filters, `attributes` with a dotted `key` (`hermes.session_id`, `llm.model_name` or `gen_ai.request.model`, and `k=v` pairs from the raw box) with `eq`, the tool filter as `span_name is tool.<name>`, `status_code is` / `is_not STATUS_CODE_ERROR`, and free text as `contains` on `input.value`, `output.value`, `ag.data.outputs` and `ag.unsupported.data.inputs`; each "or" is one query per key merged client-side, because the API accepts a nested `or` group but drops it (v0.122: a text search returned every root). The grammar has no duration field, so the minimum duration is applied to the fetched rows. Span counts come from one more query over the page's trace ids.
- **Detail**: the trace's spans, flattened from Agenta's nested attribute objects to dotted keys, with Agenta's derived `ag.data.*` and `ag.meta.*` copies of the inputs and outputs dropped (the plugin's own `input.value` and `gen_ai.*.messages` already carry them) and `ag.metrics.*` / `ag.type.*` kept. Resource attributes are not stored, so the resource is the entry's `service_name`.
- Current Agenta (`agenta-api:latest`, 2026-10-18) no longer stores `llm.model_name`, `tool.name` or a root's `input.value` / `output.value` as sent: it keeps `gen_ai.request.model`, `gen_ai.tool.name` and the span name, and moves the text to `ag.data.outputs` / `ag.unsupported.data.inputs`. The search asks both the old and the new keys, and a card or detail span missing one of those four keys shows the moved value under it.
- `service` is not a filter (the key is the project), and no "open in" link is offered unless `ui_url` names the observability page.

## Verified behavior

| Check | Result |
|---|---|
| Ingest | `POST /api/otlp/v1/traces` with `ApiKey` → 200 (`GET` → `{"status":"ready"}`); without a key → 401; a JSON body → 400 |
| Spans | 7 in one trace: `agent` (type `agent`, no parent), `llm.nvidia/…` and three `api.nvidia/…` (type `chat`), `tool.terminal`, `tool.read_file` (type `tool`), all `STATUS_CODE_OK` |
| Roll-ups | root `ag.metrics.tokens.cumulative.total` = sum of the three `api.*` spans (23 653 in the verified turn): no double count |
| Query grammar | `parent_id is null` → the root; `span_name startswith "tool."` → 2; `attributes key hermes.session_id eq` → 1 root; `status_code is_not STATUS_CODE_ERROR` → 7; `attributes key input.value contains` → 6; `oldest` / `newest` honoured; `not_exists` and `eq` on `parent_id` / `status_code` are rejected ("only supports comparison and list operators") |
| Dashboard verify harness | fresh four-turn batch: **PASS 35 · SKIP 13** (the skips: no UI deep link ×4 and the nine metrics / logs checks Agenta does not serve); root, status, model, session, tokens, start and duration of every turn match the oracle, the free-text marker search returns exactly one trace each, the `kind=tool` filter names the three tool-using turns, and the cards and detail render in the browser |
| Resident memory | about 2 GB across eleven containers (api ~900 MB) |

## Caveats

- **Protobuf only** on the OTLP route (the plugin's default); batches over 10 MB are 413.
- **Asynchronous ingest**: spans pass through a Redis stream and appear a few seconds after the export; a 200 means accepted, not stored.
- **Heavy**: the trimmed stack is eleven containers and about 4 GB of images; the upstream stack adds a sandbox runner (needs `SYS_ADMIN` + `/dev/fuse`), playground services, a mobile UI and Traefik.
- **Resource attributes are dropped** on ingest; the dashboard shows the entry's service name.
- Sign-ups are open on a fresh stack; the compose header lists the local secrets to change before exposing the port.

## Troubleshooting

- **`agenta requires api_key`** — no key resolved; run `mint-api-key.sh` or copy one from the project settings.
- **401 on every export** — the key belongs to another stack or was deleted (`GET /api/keys` lists the live prefixes).
- **Spans missing right after a turn** — the ingest is asynchronous; wait a few seconds.
- **Dashboard says `Agenta needs api_key`** — the adapter reads the same `api_key_env` as the exporter; set it on the entry.
