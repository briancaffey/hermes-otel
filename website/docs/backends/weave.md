---
sidebar_position: 12
title: "W&B Weave"
description: "W&B Weave trace ingest for Hermes agent runs, using OTLP/HTTP and GenAI agent attributes."
---

# W&B Weave

[W&B Weave](https://docs.wandb.ai/weave/) is W&B's tracing and evaluation product for LLM and agent applications. hermes-otel exports directly to Weave's OTLP trace endpoint, so Hermes runs show up in Weave without adding the Weave SDK as a second tracing layer.

**Signals:** traces. **Deployment:** W&B Cloud, Dedicated Cloud, Self-Managed. **Cost:** W&B account required.

## Quick start

Create a W&B API key, then set it in your shell:

```bash
export WANDB_API_KEY="..."
```

Declare the backend in `config.yaml`:

```yaml
backends:
  - type: weave
    api_key_env: WANDB_API_KEY
    entity: my-team
    project: hermes-agent
```

The plugin fills in:

- Endpoint: `https://trace.wandb.ai/otel/v1/traces`
- Header: `wandb-api-key: <WANDB_API_KEY>`
- Resource attributes: `wandb.entity` and `wandb.project`

You should see `✓ W&B Weave at https://trace.wandb.ai/otel/v1/traces` in startup logs.

## Why a dedicated `type: weave`

Trace export can be hand-written with `type: otlp`, but Weave needs a specific authentication and routing shape. Declaring `type: weave` asks the plugin to:

1. Read the W&B API key from `api_key`, `api_key_env`, or `WANDB_API_KEY`.
2. Set the `wandb-api-key` request header.
3. Route spans with `wandb.entity` and `wandb.project` resource attributes.
4. Default the endpoint for W&B Cloud, while supporting Dedicated Cloud and Self-Managed base URLs.
5. Disable metrics and logs by default because W&B documents this endpoint for OTLP traces.

Keep the key out of `config.yaml`; use `api_key_env`.

## Configuration reference

| Field | Meaning |
|---|---|
| `api_key` / `api_key_env` | W&B API key. Prefer `api_key_env`; falls back to `WANDB_API_KEY`. |
| `entity` / `entity_env` | W&B team or user name. Falls back to `WANDB_ENTITY` / `DEFAULT_WANDB_ENTITY`. |
| `project` / `project_env` | W&B project name. Falls back to `WANDB_PROJECT` / `DEFAULT_WANDB_PROJECT`. |
| `base_url` | W&B base URL. `https://trace.wandb.ai` becomes `/otel/v1/traces`; Dedicated Cloud such as `https://acme.wandb.io` becomes `/traces/otel/v1/traces`. |
| `endpoint` | Optional full OTLP traces endpoint; overrides `base_url`. Also settable via `OTEL_WEAVE_ENDPOINT` / `WANDB_OTLP_ENDPOINT`. |
| `metrics` / `logs` / `traces` | Per-signal toggles. Traces default on; metrics and logs default off. |
| `headers` | Extra headers, merged on top of the generated `wandb-api-key` header. |

You can also put routing attributes at the top level:

```yaml
resource_attributes:
  wandb.entity: my-team
  wandb.project: hermes-agent

backends:
  - type: weave
    api_key_env: WANDB_API_KEY
```

If both places set `wandb.entity` or `wandb.project`, the values must match.

## Single backend (env vars)

Without a `config.yaml` `backends:` list, env-var mode selects Weave when all routing values are present:

```bash
export WANDB_API_KEY="..."
export WANDB_ENTITY="my-team"
export WANDB_PROJECT="hermes-agent"
```

Optional endpoint overrides:

```bash
export OTEL_WEAVE_ENDPOINT="https://trace.wandb.ai/otel/v1/traces"
# or
export OTEL_WEAVE_BASE_URL="https://acme.wandb.io"
```

## Agent trace shape

The plugin emits Weave-friendly GenAI attributes while preserving Phoenix/OpenInference attributes:

- Root `agent` / `cron` span: `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name=hermes-agent`, `gen_ai.conversation.id=<session_id>`, `wandb.thread_id=<session_id>`, `wandb.is_turn=true`.
- LLM/API spans: `gen_ai.operation.name=chat`, model/provider attributes, usage attributes, and privacy-gated `gen_ai.input.messages` / `gen_ai.output.messages` when content capture is enabled.
- Tool spans: `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, and privacy-gated tool arguments/results.
- Sub-agent spans: `gen_ai.operation.name=invoke_agent` and `gen_ai.agent.name=<role>`.

`capture_previews: false` suppresses message bodies, tool arguments, and tool results, but keeps structural attributes so traces remain useful.

## Multi-backend fan-out

Weave is a good cloud companion to a local backend:

```yaml
resource_attributes:
  wandb.entity: my-team
  wandb.project: hermes-agent

backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: weave
    api_key_env: WANDB_API_KEY
```

Each backend gets its own `BatchSpanProcessor`, so W&B network latency does not block local Phoenix export.

## Limitations

- **Trace ingest only by default.** W&B's documented OTLP endpoint is `/otel/v1/traces`; the plugin disables metrics and logs for `type: weave` unless you explicitly override them.
- **No bundled-dashboard query support.** Use the Weave UI for W&B traces. The hermes-otel bundled dashboard should query a local backend such as Phoenix.
- **Global W&B routing.** `wandb.entity` and `wandb.project` are OTel Resource attributes on the shared `TracerProvider`, so all spans in the process route to one Weave project.

## See also

- [Send OpenTelemetry Traces to Weave](https://docs.wandb.ai/weave/guides/tracking/otel)
- [Trace your agents](https://docs.wandb.ai/weave/guides/tracking/trace-agents)
- [Multi-backend fan-out](/backends/multi-backend)
