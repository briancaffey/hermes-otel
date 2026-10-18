---
sidebar_position: 3
title: "Attribute conventions"
description: "Dual-convention emitted on every span — gen_ai.* (Langfuse, SigNoz) and OpenInference llm.* (Phoenix)."
---

# Attribute conventions

The observability ecosystem hasn't agreed on attribute names for LLM telemetry yet. Langfuse and SigNoz use the (pre-standard) `gen_ai.*` convention from [OTel's GenAI SIG](https://github.com/open-telemetry/semantic-conventions/tree/main/docs/gen-ai); Phoenix uses [Arize's OpenInference](https://arize.com/docs/phoenix/reference/openinference) with `llm.token_count.*` and `input.value` / `output.value`.

hermes-otel emits **both** conventions on the same span so whichever backend reads from it picks up the data it's expecting. No vendor-specific adapter code per backend.

## Token counts (on `api.*` spans)

| Metric | Langfuse / gen_ai | Phoenix / OpenInference |
|---|---|---|
| Prompt tokens | `gen_ai.usage.input_tokens` | `llm.token_count.prompt` |
| Completion tokens | `gen_ai.usage.output_tokens` | `llm.token_count.completion` |
| Total tokens | `gen_ai.usage.total_tokens` | `llm.token_count.total` |
| Cache read | `gen_ai.usage.cache_read_input_tokens` | `llm.token_count.prompt_details.cache_read` |
| Cache write | `gen_ai.usage.cache_creation_input_tokens` | `llm.token_count.prompt_details.cache_write` |
| Reasoning | `gen_ai.usage.reasoning.output_tokens` | `llm.token_count.completion_details.reasoning` |

Cache read/write are only populated when the provider reports them (Anthropic's prompt caching, OpenAI's — both surface them in their API responses). Reasoning tokens are emitted only by reasoning-capable models and are a **subset of completion/output tokens** (already counted in the completion and total figures), surfaced separately for visibility — don't add them on top of the total.

## Message content (on `llm.*` spans)

| | Langfuse / gen_ai | Phoenix / OpenInference |
|---|---|---|
| User message | `gen_ai.input.messages` (message JSON) | `input.value` |
| Assistant response | `gen_ai.output.messages` (message JSON) | `output.value` |
| Content type | *(not set)* | `input.mime_type`, `output.mime_type` |

When [conversation capture](/configuration/conversation-capture) is on, `input.value` and `gen_ai.input.messages` become JSON of the full message list, `input.mime_type` becomes `application/json`, and `hermes.conversation.message_count` records how many messages were passed.

## Model / request metadata

| | Langfuse / gen_ai | Phoenix / OpenInference |
|---|---|---|
| Model name | `gen_ai.request.model` | `llm.model_name` |
| Provider | `gen_ai.provider.name` and the older `gen_ai.system` | `llm.provider` |
| Request parameters | `gen_ai.request.temperature`, `gen_ai.request.max_tokens`, `gen_ai.request.top_p`, … (on `api.*`) | `llm.request.max_tokens` |
| Finish reason | `gen_ai.response.finish_reasons` (an array) | `llm.response.finish_reason` |

Request parameters are set only when Hermes passes them to the hook.

## Tool spans

Tool spans carry both conventions too:

| Attribute | Meaning |
|---|---|
| `tool.name` / `gen_ai.tool.name` | Tool name |
| `gen_ai.tool.call.id` | The model's tool-call id (also on the `approval.*` span that gated it) |
| `input.value` / `gen_ai.tool.call.arguments` | Tool args (JSON string) |
| `output.value` / `gen_ai.tool.call.result` | Tool result (string) |
| `hermes.tool.target` | Inferred file / URL (plugin-specific) |
| `hermes.tool.command` | Inferred shell command (plugin-specific) |
| `hermes.tool.outcome` | `completed` / `error` / `timeout` / `blocked` / `cancelled` (plugin-specific) |
| `hermes.skill.name` | Inferred skill name (plugin-specific, optional) |

See [Tool identity](/architecture/tool-identity).

## Session / turn metadata (on the `agent` / `cron` root)

| Attribute | Meaning |
|---|---|
| `hermes.session.kind` | `cli` / `telegram` / `discord` / `cron` / ... |
| `hermes.session.id` | Hermes session ID |
| `session.id` | Standard OTel alias of the above (`gen_ai.conversation.id` is the GenAI one) |
| `user.id` | `platform:sender` on gateway sessions, only with `capture_sender_id: true` |
| `hermes.turn.*` | Turn summary (see [Turn summary](/architecture/turn-summary)) |

## Resource-level attributes

Set on the OTel `Resource` and therefore stamped on **every** span:

| Attribute | Source |
|---|---|
| `service.name` | `hermes-agent`, or the profile name with `service_name_from_profile` (override via `resource_attributes:`; `OTEL_SERVICE_NAME` has no effect) |
| `service.instance.id` | A UUID per process, so two Hermes processes never share a metric series |
| `service.version` | `hermes-otel` plugin version |
| `process.pid` | Process id |
| `hermes.profile` | The Hermes profile this process serves (also on the root span) |
| `hermes.kanban.task_id`, `hermes.kanban.board`, `hermes.kanban.run_id` | A dispatcher-spawned kanban worker's card, board and attempt; absent elsewhere |
| `openinference.project.name` | `project_name` / `OTEL_PROJECT_NAME` (the Phoenix project) |
| `host.name` | Only with `host_metrics: true` |
| *plus* any `resource_attributes:` (and the deprecated `global_tags:`) from `hermes_otel.yaml` |

The full list with every source is in the [span attributes reference](/reference/span-attributes#resource-on-every-span).

`common_attributes:` is the per-signal counterpart: its keys are set on every span, every log record and every metric point as plain attributes (never on the Resource), for backends that do not index resource attributes per signal. See [Which attributes reach which signal](/configuration/yaml#which-attributes-reach-which-signal).

## Why dual-convention rather than pick one?

Every backend supports a different set. Emitting both is cheap (same span, a few extra key-value pairs) and saves every user from writing their own mapping adapter. When the GenAI spec stabilises and Phoenix/Langfuse converge, this will simplify.

## Roadmap

- [OpenInference Tool span kind is now stable](https://arize.com/docs/phoenix/reference/openinference) — already emitted.
- The `gen_ai.tool.*` names are emitted already; they will follow the GenAI spec as it changes.
- `session.id` is the standard OTel name; the plugin emits both `hermes.session.id` (for compatibility with older backends that key on it) and `session.id`. The former may be dropped in a future major version — watch the changelog.
