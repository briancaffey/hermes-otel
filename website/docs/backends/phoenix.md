---
sidebar_position: 2
title: "Phoenix"
description: "Run Phoenix locally in Docker or send to Arize AX cloud — LLM-native tracing UI with OpenInference attribute support."
---

# Phoenix

[Arize Phoenix](https://github.com/Arize-ai/phoenix) is an open-source LLM observability platform with a tracing UI built around the [OpenInference](https://arize.com/docs/phoenix/reference/openinference) span convention. It runs as a single container, takes ~5 seconds to start, and pretty-prints JSON previews.

**Signals:** traces + metrics. **Deployment:** local (single container) or Arize AX cloud. **Cost:** OSS (self-host) / commercial (cloud).

## Local (Docker)

A ready-to-use compose file ships with the plugin:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -f docker-compose/phoenix.yaml up -d
```

Then:

```bash
export OTEL_PHOENIX_ENDPOINT="http://localhost:6006/v1/traces"
export OTEL_PROJECT_NAME="hermes-agent"
```

Open the UI at http://localhost:6006. Traces show up in the `hermes-agent` project.

## Arize AX Cloud

Phoenix also runs as a hosted service. Point at your cloud endpoint and attach the ingest key:

```bash
export OTEL_PHOENIX_ENDPOINT="https://app.phoenix.arize.com/v1/traces"
# Headers can be passed via config.yaml:
```

```yaml
# ~/.hermes/plugins/hermes_otel/config.yaml
backends:
  - type: phoenix
    endpoint: https://app.phoenix.arize.com/v1/traces
    headers:
      api_key: ${PHOENIX_API_KEY}
```

## What you'll see

Phoenix is built around LLM-specific spans, so the UI understands the plugin's span types natively:

- **`session.*` / `cron`** spans appear as top-level traces with the turn summary on them (tool count, skills, final status).
- **`llm.*`** spans show the user message in the Input panel and the assistant response in the Output panel (pretty-printed JSON when [conversation capture](/configuration/conversation-capture) is on).
- **`api.*`** spans carry the token counts (`llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`), the `finish_reason`, and the HTTP duration.
- **`tool.*`** spans show the arguments (Input) and the result (Output). Errors map to `StatusCode.ERROR` so the Phoenix error filter works.

## Attribute convention

Phoenix uses [OpenInference](https://arize.com/docs/phoenix/reference/openinference). hermes-otel emits that convention on every span (`llm.token_count.*`, `input.value`, `output.value`) alongside the `gen_ai.*` convention for other backends.

See [Attribute conventions](/architecture/attributes) for the full dual-convention table.

## Metrics

Phoenix accepts OTLP metrics in addition to traces. The plugin's token/tool/cost metrics flow automatically when Phoenix is selected — no extra config.

## Troubleshooting

**"No traces show up in Phoenix"**

- Check the endpoint includes `/v1/traces` — Phoenix doesn't redirect from the collector root.
- Confirm the container is listening: `curl -I http://localhost:6006` should return `200`.
- Turn on debug logging: `export HERMES_OTEL_DEBUG=true`, run a Hermes turn, check `~/.hermes/plugins/hermes_otel/debug.log` for the OTLP POST response.

**"Spans are missing input/output previews"**

- If `capture_previews: false` or `HERMES_OTEL_CAPTURE_PREVIEWS=false` is set, previews are intentionally suppressed. Remove the setting, restart Hermes.

**"Tokens show as zero"**

- Phoenix keys off `llm.token_count.*` — the plugin emits these on `api.*` spans (not `llm.*`). Check the child `api.*` span, not the parent.
