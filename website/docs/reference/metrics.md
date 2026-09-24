---
sidebar_position: 4
title: "Metrics"
description: "Every metric the plugin emits — name, kind, unit, labels, and which hook records it."
---

# Metrics reference

Every instrument the plugin creates, exactly as named in `tracer._create_metric_instruments`. Metrics reach every backend with `metrics: true` through one shared `MeterProvider` (`flush_interval_ms` controls the export cadence). Backends that speak only traces (Phoenix, Jaeger, Tempo, Langfuse, Weave) ignore them.

**Buckets.** Every histogram has explicit bucket boundaries (a `View` per instrument, since 1.15): the OTel GenAI lists for seconds (`0.01 … 81.92`) and tokens (`1 … 67,108,864`), the spec's `invoke_workflow` list (`1 s … 2 h`) for sessions, approvals and sub-agent runs, and millisecond lists for the deprecated `ms` histograms. Before 1.15 the SDK default (`0 … 10000`, a millisecond scale) applied to everything, which put every LLM call in the first two buckets of a seconds histogram and every request over 10,000 tokens into `+Inf`. Set `metrics_histogram: exponential` to send base-2 exponential histograms instead (Prometheus 3.8+ native histograms, Datadog, New Relic, Uptrace); Mimir and SigNoz Cloud do not accept them by default.

**Temporality.** Counters and histograms are exported **cumulative** by default (what Prometheus, Mimir, Grafana LGTM and OpenObserve expect). A backend entry's `metrics_temporality: delta` switches that backend's reader to delta (counters, histograms and observable counters delta; up-down counters and gauges stay cumulative), which Datadog requires, Logfire's dashboards need, and New Relic, SigNoz and Uptrace recommend; `signoz` and `uptrace` entries default to delta. The top-level `metrics_temporality` sets the default for every backend, and the SDK's `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` applies when neither is set. Metrics are exported by the periodic reader (`flush_interval_ms`, 60 s) and, since 1.15, by the turn-end flush as well, on the same worker thread as the span flush, so a one-shot `hermes -z` run exports its metrics once per turn instead of never (it exits without `atexit` and never reaches the periodic tick). The turn waits up to `force_flush_wait_ms` (1.5 s since 1.15) for that flush; a collector slower than that loses the turn's last points in one-shot runs. Mind short-lived runs on delta-native backends: such a run exports each turn's points once; a single cumulative point counts as zero on Datadog and New Relic and goes stale after five minutes on Prometheus, while the same run exported as delta counts in full. See [Metrics and your backend](/backends/overview#metrics-and-your-backend).

**Label policy.** Labels are bounded: model, provider, platform, profile (`profile`, the Hermes profile that ran the turn, on every metric; see [profiles](/configuration/profiles)), tool name, skill name, outcome, choice, error class. Never a session id, task id, tool-call id or free text — per-session analysis belongs on spans (`session.id`). Since 1.15 each instrument has an allow-list of labels (a `View` drops anything else), and open-ended labels (`model`, `tool_name`, `skill_name`, `reason`, `role`, `error_type` and their `gen_ai.*` twins) fold into `other` after `metrics_label_limit` distinct values per process (default 100), because the Python SDK has no cardinality limit of its own. Each process also carries its own `service.instance.id` resource attribute, so two Hermes processes exporting to one backend never write the same series (see [Config schema → `resource_attributes`](/reference/config-schema)).

## `hermes.*` metrics

| Metric | Kind | Unit | Labels | Recorded by |
|---|---|---|---|---|
| `hermes.session.count` | Counter | `{session}` | `platform` | `session_start` |
| `hermes.session.turns` | Histogram | `{turn}` | `platform`, `reason` | `on_session_finalize` / `on_session_reset`: turns the finished session had |
| `hermes.session.duration` | Histogram | `s` | `platform`, `reason` | `on_session_finalize` / `on_session_reset`: seconds from the session's first turn to its end |
| `hermes.message.count` | Counter | `{message}` | `model`, `provider` (the provider the turn's API calls reported; absent before any did) | `post_llm_call` (one per completed assistant message) |
| `hermes.model.usage` | Counter | `{message}` | `model`, `provider` | `post_api_request` (one per API call) |
| `hermes.token.usage` | Counter | `{token}` | `model`, `provider`, `token_type` = `input` · `output` · `cacheRead` · `cacheCreation` · `reasoning` | `post_api_request` |
| `hermes.prompt_cache.tokens` | Counter | `{token}` | `model`, `provider`, `api_mode`, `cache_result` = `hit` · `miss` | `post_api_request` (only when the provider reported cache accounting) |
| `hermes.prompt_cache.observations` | Counter | `{request}` | `model`, `provider`, `api_mode`, `cache_result` | `post_api_request` |
| `hermes.cost.usage` | Counter | `USD` | `model`, `provider` | `post_api_request` (when Hermes reports `usage.cost`) |
| `hermes.tool.duration` | Histogram | `ms` **(deprecated)** | `tool_name`, `gen_ai.tool.name` | `post_tool_call`; superseded by `gen_ai.execute_tool.duration` (seconds), removed in 2.0 |
| `hermes.skill.inferred` | Counter | `{hit}` | `skill_name`, `source` = `skill_view` · `path_match` | `pre_tool_call` |
| `hermes.approval.count` | Counter | `{prompt}` | `choice` | `post_approval_response` |
| `hermes.approval.duration` | Histogram | `ms` **(deprecated)** | `choice` | `post_approval_response`; superseded by `hermes.approval.wait.duration` (seconds), removed in 2.0 |
| `hermes.approval.wait.duration` | Histogram | `s` | `choice` | `post_approval_response` (human / guardian decision wait) |
| `hermes.api.error.count` | Counter | `{request}` | `error_type`, `status_class` = `2xx` … `5xx` · `network` · `other`, `retryable` = `true` · `false` · `unknown`, `model`, `provider` | `api_request_error` |
| `hermes.retry.count` | Counter | `{attempt}` | `model`, `provider` | `api_request_error` (once per *retryable* failure) |
| `hermes.subagent.count` | Counter | `{run}` | `role` (`unknown` when Hermes reported none), `status` = `ok` · `error`, `child_status` (Hermes' reported status, lower-cased; absent when not reported) | `subagent_stop` |
| `hermes.subagent.duration` | Histogram | `ms` **(deprecated)** | `role` | `subagent_stop`; superseded by `hermes.subagent.run.duration` (seconds), removed in 2.0 |
| `hermes.subagent.run.duration` | Histogram | `s` | `role` | `subagent_stop` |

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
| `gen_ai.execute_tool.duration` | Histogram | **`s`** | `gen_ai.tool.name`; `error.type` when the tool failed | `post_tool_call` (the OTel GenAI tool-execution histogram; buckets `0.01 … 81.92`) |
| `gen_ai.client.operation.duration` | Histogram | **`s`** | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`; `error.type` on failures | `post_api_request`, `api_request_error` |
| `gen_ai.agent.token.usage` | Histogram | `{token}` | `gen_ai.token.type`, `gen_ai.operation.name` = `invoke_agent`, `gen_ai.provider.name`, `gen_ai.request.model` | `session_end` (per-turn rollup) |

- Durations follow the spec unit (**seconds**). The three `hermes.*` `ms` histograms are deprecated in favour of their seconds successors above and go away in 2.0.
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
