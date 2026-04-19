---
sidebar_position: 3
title: "Langfuse"
description: "Send Hermes traces to Langfuse Cloud (free tier) or self-hosted Langfuse, with Basic Auth constructed automatically from your public/secret keys."
---

# Langfuse

[Langfuse](https://langfuse.com) is an open-source LLM engineering platform with a polished tracing UI, session grouping, and cost attribution. It has a generous free cloud tier and a self-host option via docker-compose.

**Signals:** traces only. **Deployment:** local (docker compose) or cloud. **Cost:** OSS (self-host) / free tier + paid (cloud).

## Cloud (fastest)

Sign up at [cloud.langfuse.com](https://cloud.langfuse.com), create a project, and grab the public + secret keys from Settings → API Keys.

**Option A — plugin-specific env vars:**

```bash
export OTEL_LANGFUSE_PUBLIC_API_KEY="pk-lf-..."
export OTEL_LANGFUSE_SECRET_API_KEY="sk-lf-..."
# Optional — defaults to EU cloud
export OTEL_LANGFUSE_ENDPOINT="https://cloud.langfuse.com/api/public/otel"
# US region:
# export OTEL_LANGFUSE_ENDPOINT="https://us.cloud.langfuse.com/api/public/otel"
```

**Option B — Langfuse-standard env vars from their docs:**

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
```

Both forms work. The plugin automatically constructs the `Authorization: Basic ...` header from the two keys — you don't need to base64-encode anything yourself.

## Self-hosted

Langfuse self-host is a full stack (Langfuse + Postgres + Redis + ClickHouse + MinIO). The plugin ships with a ready-to-go compose file:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -f docker-compose/langfuse.yaml up -d
# Wait ~60s for ClickHouse to start
```

Pre-seeded test keys:

```bash
export OTEL_LANGFUSE_PUBLIC_API_KEY="lf_pk_test_hermes_otel"
export OTEL_LANGFUSE_SECRET_API_KEY="lf_sk_test_hermes_otel"
export OTEL_LANGFUSE_ENDPOINT="http://localhost:3000/api/public/otel"
```

UI at http://localhost:3000.

## Multi-backend config

```yaml
# ~/.hermes/plugins/hermes_otel/config.yaml
backends:
  - type: langfuse
    public_key_env: LANGFUSE_PUBLIC_KEY
    secret_key_env: LANGFUSE_SECRET_KEY
    base_url: https://cloud.langfuse.com
    # Or override the full OTLP path:
    # endpoint: https://cloud.langfuse.com/api/public/otel/v1/traces
```

Secrets should live in env vars (`*_env:` keys). Plaintext `public_key:` / `secret_key:` also work but are discouraged.

## What you'll see

Langfuse groups traces into sessions automatically. hermes-otel's `session.*` root spans show up as top-level traces; nested `llm.*` / `api.*` / `tool.*` appear as observations within.

- **User message** lands on `gen_ai.content.prompt` / `input.value` on the `llm.*` span.
- **Assistant response** lands on `gen_ai.content.completion` / `output.value`.
- **Token counts** use `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` on `api.*` spans.
- **Tool calls** appear as child spans with inputs/outputs.

## Attribute convention

Langfuse keys off `gen_ai.*`. The plugin emits that alongside the OpenInference convention so the same span serves both UIs — see [Attribute conventions](/architecture/attributes).

## Metrics

Langfuse doesn't accept OTLP metrics — it's trace-only. The plugin auto-skips the metrics exporter when Langfuse is the sole backend. If you want token/tool/cost metrics too, fan out to a metrics-capable backend in parallel; see [Multi-backend](/backends/multi-backend).

## Troubleshooting

**"Auth failed / 401 from Langfuse"**

- You need *both* keys (public + secret). Langfuse won't authenticate with only one.
- If using `LANGFUSE_BASE_URL`, don't append `/api/public/otel` yourself — the plugin does it. If you want full control, use `OTEL_LANGFUSE_ENDPOINT` instead.

**"Spans show up but without message content"**

- Check `capture_previews` — if it's false, the plugin is suppressing `input.value` / `output.value` at the source.
- Remember: by default `input.value` is just the latest user turn. To capture the full conversation history, enable [conversation capture](/configuration/conversation-capture).

**"Self-hosted Langfuse won't start"**

- ClickHouse needs ~60 seconds to come up. The plugin will show connection refused errors until it's ready. Wait and retry.
