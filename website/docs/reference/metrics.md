---
sidebar_position: 4
title: "Metrics"
description: "Every metric the plugin emits — name, kind, unit, labels, and which hook records it."
---

# Metrics reference

Every instrument the plugin creates, exactly as named in `tracer._create_metric_instruments`. Metrics reach every backend with `metrics: true` through one shared `MeterProvider` (`flush_interval_ms` controls the export cadence). Backends that speak only traces (Phoenix, Jaeger, Tempo, Langfuse, Weave) ignore them.

**Buckets.** Every histogram has explicit bucket boundaries (a `View` per instrument, since 1.15): the OTel GenAI lists for seconds (`0.01 … 81.92`) and tokens (`1 … 67,108,864`), the spec's `invoke_workflow` list (`1 s … 2 h`) for sessions, approvals and sub-agent runs, and millisecond lists for the deprecated `ms` histograms. Before 1.15 the SDK default (`0 … 10000`, a millisecond scale) applied to everything, which put every LLM call in the first two buckets of a seconds histogram and every request over 10,000 tokens into `+Inf`. Set `metrics_histogram: exponential` to send base-2 exponential histograms instead (Prometheus 3.8+ native histograms, Datadog, New Relic, Uptrace); Mimir and SigNoz Cloud do not accept them by default.

**Temporality.** Counters and histograms are exported **cumulative** by default (what Prometheus, Mimir, Grafana LGTM and OpenObserve expect). A backend entry's `metrics_temporality: delta` switches that backend's reader to delta (counters, histograms and observable counters delta; up-down counters and gauges stay cumulative), which Datadog requires, Logfire's dashboards need, and New Relic, SigNoz and Uptrace recommend; `signoz` and `uptrace` entries default to delta. The top-level `metrics_temporality` sets the default for every backend, and the SDK's `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` applies when neither is set. Metrics are exported by the periodic reader (`flush_interval_ms`, 60 s) and, since 1.15, by the turn-end flush as well, on the same worker thread as the span flush, so a one-shot `hermes -z` run exports its metrics once per turn instead of never (it exits without `atexit` and never reaches the periodic tick). The turn waits up to `force_flush_wait_ms` (1.5 s since 1.15) for that flush; a collector slower than that loses the turn's last points in one-shot runs. Mind short-lived runs on delta-native backends: such a run exports each turn's points once; a single cumulative point counts as zero on Datadog and New Relic and goes stale after five minutes on Prometheus, while the same run exported as delta counts in full. See [Metrics and your backend](/backends/overview#metrics-and-your-backend).

**Label policy.** The `provider` label is the same spelling as `gen_ai.provider.name` on the spans: the GenAI well-known value when the conventions have one for the Hermes provider id (`x_ai`, `aws.bedrock`, `gcp.vertex_ai`, `openai` …), else the raw id (`openrouter`, `nvidia` …) (#475). Labels are bounded: model, provider, platform, profile (`profile`, the Hermes profile that ran the turn, on every metric; see [profiles](/configuration/profiles)), tool name, skill name, outcome, choice, error class. Never a session id, task id, tool-call id or free text — per-session analysis belongs on spans (`session.id`). Since 1.15 each instrument has an allow-list of labels (a `View` drops anything else), and open-ended labels (`model`, `tool_name`, `skill_name`, `reason`, `role`, `error_type` and their `gen_ai.*` twins) fold into `other` after `metrics_label_limit` distinct values per process (default 100), because the Python SDK has no cardinality limit of its own. Each process also carries its own `service.instance.id` resource attribute, so two Hermes processes exporting to one backend never write the same series (see [Config schema → `resource_attributes`](/reference/config-schema)).

## `hermes.*` metrics

| Metric | Kind | Unit | Labels | Recorded by |
|---|---|---|---|---|
| `hermes.session.count` | Counter | `{session}` | `platform` | the first root span this process creates for the session: `session_start`, or the root synthesised on `pre_llm_call` when Hermes fires no `session_start` in this process (a resumed `hermes -r`, gateway and cron turns; #473). Continuation turns of a counted session add nothing |
| `hermes.span.swept.count` | Counter | `{span}` | `span_kind` (`tool` · `api` · `llm` · `approval` · `skill`), `reason` (`turn_end` · `session_end` · `turn_failed` · `ttl` · `shutdown`) | a child span still open when its turn or session ended, the orphan sweep ran or the process shut down was ended by the plugin and marked `hermes.span.ended_by=sweep` ([orphan sweep](/architecture/orphan-sweep), #477) |
| `hermes.session.turns` | Histogram | `{turn}` | `platform`, `reason` | `on_session_finalize` / `on_session_reset`: turns the finished session had |
| `hermes.session.duration` | Histogram | `s` | `platform`, `reason` | `on_session_finalize` / `on_session_reset`: seconds from the session's first turn to its end |
| `hermes.active_time.total` | Counter | `s` | `type` = `cli`, `platform` | `session_end`: the turn's wall-clock, from its first hook to its end, as seconds the agent loop was busy. The `type` values are Claude Code's (`user` = typing time, `cli` = the tool's own work); Hermes has no signal for a person's typing, so only `cli` is recorded (#485) |
| `hermes.message.count` | Counter | `{message}` | `model`, `provider` (the provider the turn's API calls reported; absent before any did) | `post_llm_call` (one per completed assistant message) |
| `hermes.model.usage` | Counter | `{message}` | `model`, `provider`, `query_source` | `post_api_request` / `post_auxiliary_call` (one per API call) |
| `hermes.token.usage` | Counter | `{token}` | `model`, `provider`, `token_type` = `input` · `output` · `cacheRead` · `cacheCreation` · `reasoning`, `query_source` = `main` · `subagent` · `auxiliary` | `post_api_request` / `post_auxiliary_call` |
| `hermes.prompt_cache.tokens` | Counter | `{token}` | `model`, `provider`, `api_mode`, `cache_result` = `hit` · `miss` | `post_api_request` (only when the provider reported cache accounting) |
| `hermes.prompt_cache.observations` | Counter | `{request}` | `model`, `provider`, `api_mode`, `cache_result` | `post_api_request` |
| `hermes.cost.usage` | Counter | `USD` | `model`, `provider`, `cost_status` = `actual` · `estimated`, `query_source` | `post_api_request` / `post_auxiliary_call`: Hermes's own price for the call (`agent.usage_pricing.estimate_usage_cost`). Calls with an unknown price or on a subscription-included route record nothing, so a missing price never reads as `$0` |
| `hermes.tool.duration` | Histogram | `ms` **(deprecated)** | `tool_name`, `gen_ai.tool.name` | `post_tool_call`; superseded by `gen_ai.execute_tool.duration` (seconds), removed in 2.0 |
| `hermes.skill.inferred` | Counter | `{hit}` | `skill_name`, `source` = `skill_view` · `path_match` | `pre_tool_call` |
| `hermes.api.time_to_first_chunk` | Histogram | `s` | `model`, `provider` | `post_api_request` of a streamed call: from Hermes's `started_at` (taken before any retry) to its `first_chunk_at`; also `hermes.api.ttfc_ms` on the `api.*` span |
| `hermes.command.count` | Counter | `{command}` | `command` (Hermes's canonical name), `surface` | `pre_command`, before a recognised slash command runs; never the arguments |
| `hermes.human_input.count` | Counter | `{request}` | `kind` (Hermes's: `approval`, `clarify`, a password prompt, …), `outcome` | `on_human_input_resolved`; every wait except an approval is also a `human_input.{kind}` span |
| `hermes.skill.lifecycle.count` | Counter | `{event}` | `action`, `provenance` (never the skill name) | `on_skill_lifecycle` |
| `hermes.transcription.count` | Counter | `{request}` | `provider`, `model`, `source` | `pre_transcription` (a decision hook; the observer returns None and never reads the file path or prompt) |
| `hermes.kanban.task.count` | Counter | `{transition}` | `outcome` = `claimed` · `spawned` · `worker_exited` · `stale_claim` · `completed` · `blocked`, `board` | the six kanban hooks ([hooks](/reference/hooks#kanban-board-transitions)); the dispatcher's and the worker's points come from different processes |
| `hermes.kanban.dispatch.count` | Counter | `{tick}` | `outcome` = `ok` · `idle` · `skipped_locked`, `board` | `on_kanban_dispatch_tick`, once per dispatcher pass after the dispatch lock is released; dry runs are not counted |
| `hermes.kanban.dispatch.result` | Counter | `{task}` | `kind` = `spawned` · `reclaimed` · `promoted` · `reconciled_orphans` · `reaped_terminal_workers` · `crashed` · `stale` · `timed_out` · `auto_blocked` · `rate_limited` · `auto_assigned_default` · `respawn_guarded` · `skipped_per_profile_capped` · `skipped_unassigned` · `skipped_nonspawnable` · `skipped_locked` · `memory_pressure`, `board` | the same hook: how many tasks each non-zero `DispatchResult` field covered. Counts only: the task ids, assignees and workspace paths in the result are never exported |
| `hermes.kanban.task.updated` | Counter | `{field}` | `field` (the changed field's name, never its value), `board` | `on_kanban_task_updated`, a task-row edit outside claim / complete / block |
| `hermes.approval.count` | Counter | `{prompt}` | `choice` | `post_approval_response` |
| `hermes.approval.duration` | Histogram | `ms` **(deprecated)** | `choice` | `post_approval_response`; superseded by `hermes.approval.wait.duration` (seconds), removed in 2.0 |
| `hermes.approval.wait.duration` | Histogram | `s` | `choice` | `post_approval_response` (human / guardian decision wait) |
| `hermes.api.error.count` | Counter | `{request}` | `error_type`, `status_class` = `2xx` … `5xx` · `network` · `other`, `retryable` = `true` · `false` · `unknown`, `model`, `provider` | `api_request_error` |
| `hermes.retry.count` | Counter | `{attempt}` | `model`, `provider` | `api_request_error` (once per *retryable* failure) |
| `hermes.subagent.count` | Counter | `{run}` | `role` (`unknown` when Hermes reported none), `status` = `ok` · `error`, `child_status` (Hermes' reported status, lower-cased; absent when not reported) | `subagent_stop` |
| `hermes.subagent.duration` | Histogram | `ms` **(deprecated)** | `role` | `subagent_stop`; superseded by `hermes.subagent.run.duration` (seconds), removed in 2.0 |
| `hermes.subagent.run.duration` | Histogram | `s` | `role` | `subagent_stop` |
| `hermes.auxiliary.count` | Counter | `{request}` | `aux_task` (`title_generation` · `compression` · `moa_reference` · `moa_aggregator` · `vision` · `approval` · `kanban_decomposer` · `triage_specifier` · `goal_judge` · … as Hermes names them; `unknown` when it sent none), `status` = `ok` · `error`, `model`, `provider` | `post_auxiliary_call`, once per physical provider attempt of an auxiliary LLM call ([hooks](/reference/hooks#pre_auxiliary_call--post_auxiliary_call)) |

`query_source` semantics: `main` is the agent loop's own API calls, `subagent` the same inside a delegated child's session, `auxiliary` the calls Hermes makes on the agent's behalf (titling, compression, MoA references and aggregator, vision, …). Sum all three for the process's whole spend; a Mixture-of-Agents turn reports the facade's usage under `main` **and** each reference/aggregator attempt under `auxiliary`, so do not add those two for one MoA turn.

`token_type` semantics: `input` is the **whole** prompt (uncached + cache reads + cache writes, Hermes' `prompt_tokens`); `cacheRead` / `cacheCreation` are subsets of it; `reasoning` is a subset of `output`. So the prompt-cache hit rate from this counter alone is `rate(cacheRead) / rate(input)`.

### Prompt-cache hit rate

`hermes.prompt_cache.tokens` splits the whole prompt into `hit` (cache reads) and `miss` (uncached input + cache writes), so `hit + miss` is the prompt:

```promql
sum(rate(hermes_prompt_cache_tokens_total{cache_result="hit"}[5m]))
/
sum(rate(hermes_prompt_cache_tokens_total[5m]))
```

Divide counter rates; never average per-request percentages. A request is counted only when the provider reported any cache accounting, so unknown support never shows up as a 0 % hit rate.

## Claude Code metric-name compatibility

Claude Code's own telemetry ([monitoring docs](https://code.claude.com/docs/en/monitoring-usage)) exports eight `claude_code.*` counters, and teams that run it have Grafana, Datadog or Honeycomb dashboards built on those names. Four of them measure the same thing a `hermes.*` instrument measures. `metric_name_compat: claude_code` (`HERMES_OTEL_METRIC_NAME_COMPAT=claude_code`) exports those four under Claude Code's names, units and label names **instead of** the `hermes.*` twins, so such a dashboard shows Hermes as one more agent; `metric_name_compat: both` exports both families (two series per point for these four, nothing else doubled). The default is `off`. Every other `hermes.*` and `gen_ai.*` instrument is exported unchanged in every mode, and the plugin's own dashboard keeps reading the `hermes.*` names from the live store whatever the mode. The walkthrough with the Grafana dashboard is [Claude Code dashboards](/guides/claude-code-dashboards).

| Hermes metric | Claude Code metric | Prometheus name | Labels (Claude Code's) | Same semantics? |
|---|---|---|---|---|
| `hermes.session.count` (`{session}`) | `claude_code.session.count` | `claude_code_session_count_total` | `start_type` = `fresh` · `resume` | Yes. `fresh` when Hermes fired `session_start` in this process, `resume` when the plugin had to synthesise the root (a resumed, gateway or cron turn). Claude Code's `continue` and `agents_view` values never occur |
| `hermes.token.usage` (`{token}`) | `claude_code.token.usage` (`tokens`) | `claude_code_token_usage_tokens_total` | `type` = `input` · `output` · `cacheRead` · `cacheCreation`, `model`, `query_source` = `main` · `subagent` · `auxiliary` | Yes, after two translations: Claude Code's `input` is the **uncached** prompt (the API's `input_tokens`), so the twin records `prompt − cacheRead − cacheCreation` where `hermes.token.usage{token_type="input"}` records the whole prompt; and the `reasoning` subset of the output is left out, so the four types sum to the request. `query_source` has the same name and values in both tools |
| `hermes.cost.usage` (`USD`) | `claude_code.cost.usage` (`USD`) | `claude_code_cost_usage_USD_total` | `model`, `query_source` | Yes: both are the client's own estimate of the call's price. Hermes's `cost_status` (`actual` / `estimated`) and `provider` labels are not carried; calls with an unknown price record nothing in both families |
| `hermes.active_time.total` (`s`) | `claude_code.active_time.total` (`s`) | `claude_code_active_time_seconds_total` | `type` = `cli` | Partly: `cli` (the agent's own work) is the turn's wall-clock; Claude Code's `user` (keyboard time) has no Hermes signal |
| — | `claude_code.lines_of_code.count`, `claude_code.commit.count`, `claude_code.pull_request.count`, `claude_code.code_edit_tool.decision` | | | Not emitted: Hermes exposes no diff, commit or edit-decision signal to the plugin today (`commit.count` is tracked as #478). Panels built on them stay empty |

Labels Claude Code puts on every metric and Hermes does not: `user.email`, `user.id`, `user.account_uuid`, `organization.id`, `terminal.type`, `app.version`. Panels that group by them show Hermes as one unlabelled series; template variables built on them (`label_values(..., user_email)`) list nothing for Hermes and match it through their *All* value. `session.id` is the exception: Claude Code sets it by default and its dashboards count sessions with it, so `metric_name_compat_session_id: true` adds it to the four `claude_code.*` instruments (one series per session; the plugin never puts it on a `hermes.*` metric). The `profile` label stays on the twins, as on every instrument. The `provider` label does not: Claude Code has one provider.

Prometheus names: Claude Code's units are literal (`tokens`, `USD`, `s`), and the OpenTelemetry→Prometheus translation appends them to the series name (`_tokens_total`, `_USD_total`, `_seconds_total`) while an existing `total` token is folded into the `_total` suffix, which is why `claude_code.active_time.total` becomes `claude_code_active_time_seconds_total`. The `{token}` unit of `hermes.token.usage` produces no suffix. Cumulative temporality (the plugin's default) is what every Prometheus-family dashboard for Claude Code expects.

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
