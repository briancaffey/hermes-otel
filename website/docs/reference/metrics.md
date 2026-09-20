---
sidebar_position: 4
title: "Metrics"
description: "Every metric the plugin emits — name, kind, unit, labels, and which hook records it."
---

# Metrics reference

Every instrument the plugin creates, exactly as named in `tracer._create_metric_instruments`. Metrics reach every backend with `metrics: true` through one shared `MeterProvider` (`flush_interval_ms` controls the export cadence). Backends that speak only traces (Phoenix, Jaeger, Tempo, Langfuse, Weave) ignore them.

**Label policy.** Labels are bounded: model, provider, platform, tool name, skill name, outcome, choice, error class. Never a session id, task id, tool-call id or free text — per-session analysis belongs on spans (`session.id`). Each process also carries its own `service.instance.id` resource attribute, so two Hermes processes exporting to one backend never write the same series (see [Config schema → `resource_attributes`](/reference/config-schema)).

## `hermes.*` metrics

| Metric | Kind | Unit | Labels | Recorded by |
|---|---|---|---|---|
| `hermes.session.count` | Counter | — | `platform` | `session_start` |
| `hermes.message.count` | Counter | — | `model`, `provider` (the provider the turn's API calls reported; absent before any did) | `post_llm_call` (one per completed assistant message) |
| `hermes.model.usage` | Counter | — | `model`, `provider` | `post_api_request` (one per API call) |
| `hermes.token.usage` | Counter | — | `model`, `provider`, `token_type` = `input` · `output` · `cacheRead` · `cacheCreation` · `reasoning` | `post_api_request` |
| `hermes.prompt_cache.tokens` | Counter | `{token}` | `model`, `provider`, `api_mode`, `cache_result` = `hit` · `miss` | `post_api_request` (only when the provider reported cache accounting) |
| `hermes.prompt_cache.observations` | Counter | `{request}` | `model`, `provider`, `api_mode`, `cache_result` | `post_api_request` |
| `hermes.cost.usage` | Counter | USD | `model`, `provider` | `post_api_request` (when Hermes reports `usage.cost`) |
| `hermes.tool.duration` | Histogram | `ms` | `tool_name`, `gen_ai.tool.name` | `post_tool_call` |
| `hermes.skill.inferred` | Counter | — | `skill_name`, `source` = `skill_view` · `path_match` | `pre_tool_call` |
| `hermes.approval.count` | Counter | — | `choice` | `post_approval_response` |
| `hermes.approval.duration` | Histogram | `ms` | `choice` | `post_approval_response` (human / guardian decision wait) |
| `hermes.api.error.count` | Counter | — | `error_type`, `status_class` = `2xx` … `5xx` · `network` · `other`, `retryable` = `true` · `false` · `unknown`, `model`, `provider` | `api_request_error` |
| `hermes.retry.count` | Counter | — | `model`, `provider` | `api_request_error` (once per *retryable* failure) |
| `hermes.subagent.count` | Counter | — | `role` (`unknown` when Hermes reported none), `status` = `ok` · `error`, `child_status` (Hermes' reported status, lower-cased; absent when not reported) | `subagent_stop` |
| `hermes.subagent.duration` | Histogram | `ms` | `role` | `subagent_stop` |

`token_type` semantics: `input` is the **whole** prompt (uncached + cache reads + cache writes, Hermes' `prompt_tokens`); `cacheRead` / `cacheCreation` are subsets of it; `reasoning` is a subset of `output`. So the prompt-cache hit rate from this counter alone is `rate(cacheRead) / rate(input)`.

### Prompt-cache hit rate

`hermes.prompt_cache.tokens` splits the whole prompt into `hit` (cache reads) and `miss` (uncached input + cache writes), so `hit + miss` is the prompt:

```promql
sum(rate(hermes_prompt_cache_tokens_total{cache_result="hit"}[5m]))
/
sum(rate(hermes_prompt_cache_tokens_total[5m]))
```

Divide counter rates; never average per-request percentages. A request is counted only when the provider reported any cache accounting, so unknown support never shows up as a 0 % hit rate.

## OTel GenAI semantic-convention metrics

Emitted alongside the `hermes.*` instruments so generic GenAI dashboards work unchanged. Set `emit_genai_metrics: false` (`HERMES_OTEL_EMIT_GENAI_METRICS=false`) to turn them off.

| Metric | Kind | Unit | Labels | Recorded by |
|---|---|---|---|---|
| `gen_ai.client.token.usage` | Histogram | `{token}` | `gen_ai.token.type` = `input` · `output`, `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model` | `post_api_request` |
| `gen_ai.client.operation.duration` | Histogram | **`s`** | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`; `error.type` on failures | `post_api_request`, `api_request_error` |
| `gen_ai.agent.token.usage` | Histogram | `{token}` | `gen_ai.token.type`, `gen_ai.operation.name` = `invoke_agent`, `gen_ai.provider.name`, `gen_ai.request.model` | `session_end` (per-turn rollup) |

- Durations follow the spec unit (**seconds**); the `hermes.*` histograms stay in **ms**.
- `gen_ai.token.type` is limited to the spec's `input` / `output`; cache and reasoning breakdowns live on `hermes.token.usage`.
- `gen_ai.agent.request.duration` is deliberately not emitted yet — there is no reliable per-turn duration signal until true session-lifecycle timing lands.

## Host & GPU metrics (opt-in)

Only with `host_metrics: true`. Observable instruments read the in-process sampler's latest value on each collection; names follow the OTel system / hardware conventions. Details and PromQL: [Host & GPU metrics](/configuration/host-metrics).

| Metric | Kind | Unit | Labels |
|---|---|---|---|
| `process.cpu.utilization` | Gauge | `1` | `cpu.mode` = `user` · `system` |
| `system.cpu.utilization` | Gauge | `1` | `cpu.mode` |
| `hw.gpu.utilization` | Gauge | `1` | `hw.id`, `hw.vendor` |
| `hw.gpu.memory.usage` | UpDownCounter | `By` | `hw.id`, `hw.vendor` |
| `hw.power` | Gauge | `W` | `hw.id`, `hw.vendor`, `hw.type=gpu` |

## Names as your backend shows them

OTLP names use dots; Prometheus-style stores mangle them. Rules of thumb:

| OTLP name | Prometheus / LGTM / SigNoz | OpenObserve |
|---|---|---|
| `hermes.token.usage` (counter) | `hermes_token_usage_total` | `hermes_token_usage` |
| `hermes.tool.duration` (histogram, ms) | `hermes_tool_duration_milliseconds_bucket` / `_sum` / `_count` | `hermes_tool_duration_*` |
| `gen_ai.client.operation.duration` (histogram, s) | `gen_ai_client_operation_duration_seconds_*` | `gen_ai_client_operation_duration_*` |
| `hermes.prompt_cache.tokens` | `hermes_prompt_cache_tokens_total` | `hermes_prompt_cache_tokens` |

Query histograms by suffix (`_sum`, `_count`, `_bucket`); the bare name has no series. Prometheus instant queries go stale ~5 minutes after the producing process exits — widen the range.
