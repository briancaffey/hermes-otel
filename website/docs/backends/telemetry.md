---
sidebar_position: 13
title: "telemetry.dev"
description: "Send Hermes traces, metrics, and logs to telemetry.dev — hosted LLM/agent observability built on the gen_ai.* semantic conventions, using the generic otlp type."
---

# telemetry.dev

[telemetry.dev](https://telemetry.dev) is a hosted observability platform purpose-built for LLM and agent telemetry. Its ingest speaks plain **OTLP/HTTP** (protobuf or JSON) and indexes the `gen_ai.*` semantic conventions hermes-otel already emits — so Hermes traces, GenAI metrics, and logs land there with no adapter code, via the [generic `otlp` type](/backends/otlp).

**Signals:** traces + metrics + logs. **Deployment:** cloud. **Cost:** free tier + paid plans.

## Quick start

Create a project at [telemetry.dev](https://telemetry.dev) and copy an ingest API key (`td_live_...`) from the project settings, then set it in your shell:

```bash
export TELEMETRY_DEV_API_KEY="td_live_..."
```

Declare the backend in `config.yaml`:

```yaml
backends:
  - type: otlp
    name: telemetry-dev
    endpoint: https://ingest.telemetry.dev/v1/traces
    headers:
      Authorization: Bearer ${TELEMETRY_DEV_API_KEY}
```

You should see `✓ telemetry-dev connected` in startup logs. Run a Hermes turn, open your project dashboard at [telemetry.dev](https://telemetry.dev), and the session trace is there — no local UI to run.

## Endpoints and auth

| Signal | Endpoint | Notes |
|---|---|---|
| Traces | `https://ingest.telemetry.dev/v1/traces` | The one you configure. |
| Metrics | `https://ingest.telemetry.dev/v1/metrics` | Derived automatically by replacing `/v1/traces` — matches telemetry.dev's path scheme, nothing to configure. |
| Logs | `https://ingest.telemetry.dev/v1/logs` | Accepted at ingest; ships only when [`capture_logs: true`](/configuration/logs) is set. |

- Auth is a single header: `Authorization: Bearer td_live_...`. Keys are created per project (and per environment), so one Hermes deployment maps cleanly to one project.
- Both OTLP/HTTP encodings are accepted (`application/x-protobuf` and `application/json`); the plugin's exporters send protobuf.

## Attribute fit

telemetry.dev normalizes the OpenTelemetry **GenAI semantic conventions**, which is one of the two conventions hermes-otel [emits on every span](/architecture/attributes):

- Token usage — `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`
- Model + provider — `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.provider.name`
- Operations — `gen_ai.operation.name` (`invoke_agent` / `chat` / `execute_tool`), `gen_ai.tool.name`
- Session grouping — `gen_ai.conversation.id` on the root session span

The OpenInference (`llm.*`, `input.value` / `output.value`) attributes ride along and are ignored — no config needed on either side.

## Privacy

Two layers compose:

- **At the source:** `capture_previews: false` strips every input/output preview before export, as with any backend — see [Privacy](/configuration/privacy).
- **At ingest:** telemetry.dev projects support server-side redaction patterns applied before storage, useful when you want previews for debugging but specific tokens/PII scrubbed.

## Troubleshooting

**`401` in debug logs (`HERMES_OTEL_DEBUG=true`)**

The `Authorization` header is missing or the key is malformed — it must be `Bearer td_live_...`. Check that `TELEMETRY_DEV_API_KEY` is exported in the environment Hermes actually runs in (gateway service files often don't inherit your shell).

**Traces show up; logs don't**

Logs are opt-in on the plugin side: set `capture_logs: true`. See [OTel logs](/configuration/logs).

## See also

- [telemetry.dev](https://telemetry.dev)
- [Generic OTLP](/backends/otlp) — the backend type this page uses
- [Multi-backend fan-out](/backends/multi-backend)
