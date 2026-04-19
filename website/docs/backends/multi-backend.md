---
sidebar_position: 9
title: "Multi-backend fan-out"
description: "Fan the same Hermes spans out to several observability backends in parallel — each on its own non-blocking worker."
---

# Multi-backend fan-out

hermes-otel can send the same span tree to several backends in parallel. Every backend entry gets its own `BatchSpanProcessor` (independent worker thread, independent queue), so a slow or unreachable collector can't block the agent or starve the others.

## Why fan out?

- **Side-by-side evaluation** — compare Phoenix and Langfuse on the same real traffic.
- **Operational vs. LLM-specific UIs** — ops team watches Jaeger / Grafana Tempo; product team watches Langfuse.
- **Local + cloud** — mirror everything to a local Phoenix for development and a Langfuse Cloud for retention.
- **Tenant / project isolation** — same traces, different ingest paths with different auth headers.

## Configuration

Multi-backend is YAML-only. When `backends:` is set and non-empty, single-backend env-var detection is **skipped entirely** — the YAML takes full control.

```yaml
# ~/.hermes/plugins/hermes_otel/config.yaml
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces

  - type: langfuse
    public_key_env: LANGFUSE_PUBLIC_KEY
    secret_key_env: LANGFUSE_SECRET_KEY
    base_url: https://cloud.langfuse.com

  - type: signoz
    endpoint: http://localhost:4328/v1/traces
    ingestion_key_env: OTEL_SIGNOZ_INGESTION_KEY

  - type: jaeger
    endpoint: http://localhost:4318/v1/traces

  - type: tempo
    endpoint: http://localhost:3200/v1/traces

  - type: otlp
    name: honeycomb
    endpoint: https://api.honeycomb.io/v1/traces
    headers:
      x-honeycomb-team: ${HONEYCOMB_API_KEY}
```

Startup banner prints one line per backend:

```text
[hermes-otel] ✓ phoenix connected · endpoint=http://localhost:6006/v1/traces
[hermes-otel] ✓ langfuse connected · endpoint=https://cloud.langfuse.com/api/public/otel
[hermes-otel] ✓ signoz connected · endpoint=http://localhost:4328/v1/traces
[hermes-otel] ✓ jaeger connected · endpoint=http://localhost:4318/v1/traces
[hermes-otel] ✓ tempo connected · endpoint=http://localhost:3200/v1/traces
[hermes-otel] ✓ honeycomb connected · endpoint=https://api.honeycomb.io/v1/traces
[hermes-otel] Registered 8 hooks
```

## Isolation guarantees

Each backend runs in **its own worker thread with its own bounded queue**:

- A single slow collector backs up **only its own queue** — the others keep draining.
- If one backend is completely unreachable, the plugin logs the failed POST attempts and drops the oldest spans in that queue when the buffer fills. The others continue.
- No backend's latency is on Hermes' hot path. `span.end()` is still a queue push.

## Per-backend metrics override

By default, backends that don't accept OTLP metrics (`langfuse`, `jaeger`, `tempo`) get metrics-export auto-disabled. You can force the opposite with `metrics: true|false`:

```yaml
backends:
  - type: jaeger
    endpoint: http://localhost:4318/v1/traces
    metrics: true   # Force-attempt metrics export (will probably 404; useful for testing collectors-that-happen-to-be-jaeger)

  - type: otlp
    name: traces-only-sink
    endpoint: http://sink:4318/v1/traces
    metrics: false  # Explicit opt-out
```

## Secrets

Prefer env-var references over inline values. Two forms:

1. **`*_env:` keys** — for first-class backend types:
   ```yaml
   - type: langfuse
     public_key_env: LANGFUSE_PUBLIC_KEY
     secret_key_env: LANGFUSE_SECRET_KEY
   ```

2. **`${VAR}` interpolation** — inside `headers:`:
   ```yaml
   - type: otlp
     endpoint: https://api.honeycomb.io/v1/traces
     headers:
       x-honeycomb-team: ${HONEYCOMB_API_KEY}
   ```

`config.yaml` is gitignored in the plugin repo; secrets still shouldn't sit there in plaintext because any editor plugin, pair-programming session, or backup system will see them. Env vars are the right place.

## LangSmith and fan-out

LangSmith is the one exception — it uses its own HTTP Run API rather than OTLP, so it's **not** a valid `backends:` entry. Setting `LANGSMITH_TRACING=true` short-circuits the `backends:` list entirely, so you can have either fan-out **or** LangSmith, not both.

If you need LangSmith alongside another backend, open an issue — we might add a second-class fan-out path for it if there's demand.

## Debugging fan-out

Enable debug logging:

```bash
export HERMES_OTEL_DEBUG=true
```

Per-backend export attempts, queue depths, and retry counts show up in `~/.hermes/plugins/hermes_otel/debug.log`. See [Debug logging](/development/debug-logging).
