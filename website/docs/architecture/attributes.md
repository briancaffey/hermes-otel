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
| Total tokens | — | `llm.token_count.total` |
| Cache read | `gen_ai.usage.cache_read_input_tokens` | `llm.token_count.cache_read` |
| Cache write | `gen_ai.usage.cache_creation_input_tokens` | `llm.token_count.cache_write` |

Phoenix adds a `total` variant that's the sum; gen_ai doesn't. Cache read/write are only populated when the provider reports them (Anthropic's prompt caching, OpenAI's — both surface them in their API responses).

## Message content (on `llm.*` spans)

| | Langfuse / gen_ai | Phoenix / OpenInference |
|---|---|---|
| User message | `gen_ai.content.prompt` | `input.value` |
| Assistant response | `gen_ai.content.completion` | `output.value` |
| Content type | *(not set)* | `input.mime_type`, `output.mime_type` |

When [conversation capture](/configuration/conversation-capture) is on, `input.value` becomes JSON of the full message list, `input.mime_type` becomes `application/json`, and `hermes.conversation.message_count` records how many messages were passed.

## Model / request metadata

| | Langfuse / gen_ai | Phoenix / OpenInference |
|---|---|---|
| Model name | `gen_ai.request.model` | `llm.model_name` |
| Provider | `gen_ai.system` | `llm.provider` |
| Invocation params | — | `llm.invocation_parameters` (JSON) |
| Finish reason | `gen_ai.response.finish_reason` | *(same)* |

`llm.invocation_parameters` is a JSON blob with the request params (temperature, max_tokens, tool schemas, etc.) that Phoenix pretty-prints in the UI.

## Tool spans

Tool-span attributes are largely OpenInference-native (the `gen_ai.*` convention doesn't have tool-specific names yet). Both backends index on them:

| Attribute | Meaning |
|---|---|
| `tool.name` | Tool name |
| `input.value` | Tool args (JSON string) |
| `output.value` | Tool result (string) |
| `hermes.tool.target` | Inferred file / URL (plugin-specific) |
| `hermes.tool.command` | Inferred shell command (plugin-specific) |
| `hermes.tool.outcome` | `completed` / `error` / `timeout` / `blocked` (plugin-specific) |
| `hermes.skill.name` | Inferred skill name (plugin-specific, optional) |

See [Tool identity](/architecture/tool-identity).

## Session / turn metadata (on `session.*`)

All plugin-specific:

| Attribute | Meaning |
|---|---|
| `openinference.project.name` | Project name from `OTEL_PROJECT_NAME` |
| `hermes.session.kind` | `cli` / `telegram` / `discord` / `cron` / ... |
| `hermes.session.id` | Hermes session ID |
| `session.id` | Standard OTel alias of the above |
| `user.id` | Hermes user ID |
| `hermes.turn.*` | Turn summary (see [Turn summary](/architecture/turn-summary)) |

## Resource-level attributes

Set on the OTel `Resource` and therefore stamped on **every** span:

| Attribute | Source |
|---|---|
| `service.name` | `OTEL_PROJECT_NAME` (falls back to `"hermes-agent"`) |
| `service.version` | `hermes-otel` plugin version |
| `otel.scope.name` | `hermes-otel` |
| `openinference.project.name` | Same as `service.name` |
| *plus* any `resource_attributes:` / `global_tags:` from `config.yaml` |

## Why dual-convention rather than pick one?

Every backend supports a different set. Emitting both is cheap (same span, a few extra key-value pairs) and saves every user from writing their own mapping adapter. When the GenAI spec stabilises and Phoenix/Langfuse converge, this will simplify.

## Roadmap

- [OpenInference Tool span kind is now stable](https://arize.com/docs/phoenix/reference/openinference) — already emitted.
- `gen_ai.tool.*` convention is evolving; we'll add it once the spec is stable.
- `session.id` is the standard OTel name; the plugin emits both `hermes.session.id` (for compatibility with older backends that key on it) and `session.id`. The former may be dropped in a future major version — watch the changelog.
