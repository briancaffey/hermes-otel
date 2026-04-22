# Overview — NousResearch/hermes-agent PR #9596

**PR title:** feat(agent): OTel observability integration
**Author:** karthikkrishnaswamysr
**State:** Open
**Size:** +5,325 / −1, 17 files
**Head commit:** `eebadc25`

This note summarizes what that PR does, how it structures OTel inside hermes-agent,
and a blunt quality assessment so the reader can decide what to borrow. It is
written for someone (me, the maintainer of `hermes_otel`) who needs to decide
which ideas are worth adopting and which are not.

## What the PR is

It adds OpenTelemetry instrumentation **as core code inside hermes-agent**, not
as a plugin. Instead of using the hermes-agent plugin-hook system, it wraps
`AIAgent`'s callback slots (`tool_start_callback`, `tool_complete_callback`,
`thinking_callback`, `step_callback`, `status_callback`, `clarify_callback`,
`stream_delta_callback`, `pre_api_request_callback`, `stream_complete_callback`)
with a single class — `OtelShim` — and then patches the gateway, CLI, and
`run_agent.py` to instantiate and drive that shim around every conversation.

In short: one 1,165-line file (`agent/otel_shim.py`) plus ~250 lines of
integration patches in `gateway/run.py`, `cli.py`, `run_agent.py`, and
`tools/delegate_tool.py`, plus an LGTM collector config, a promtail config, a
setup-hermes.sh stanza to install promtail on Linux, and a big 2,788-line
Grafana dashboard JSON.

## Files

| File | +lines | Purpose |
|---|---|---|
| `agent/otel_shim.py` | 1,165 | The `OtelShim` class, module-level SDK init, metrics, classifiers, helpers |
| `gateway/run.py` | 165 | Wrap `run_conversation()`, emit shutdown/drain/inactivity events |
| `cli.py` | 50 | Create the shim when `OTEL_ENABLED=true`, start/end trace around TUI runs |
| `run_agent.py` | 34 | Call `_on_pre_api_request` / `_on_stream_complete` at LLM call boundaries |
| `tools/delegate_tool.py` | 61 | Open a child `subagent.run` span per delegated task |
| `otel/grafana/hermes_observability_dashboard.json` | 2,788 | Pre-built Grafana dashboard |
| `otel/otel-lgtm/otelcol-config.yaml` | 66 | Collector: OTLP → Tempo + Prometheus + Loki, plus spanmetrics |
| `otel/promtail/promtail-config.yaml` | 42 | Tail `~/.hermes/logs/*.log` + `~/.hermes/sessions/*.jsonl` → Loki |
| `otel/promtail/promtail-hermes.service` | 15 | systemd unit for Promtail |
| `setup-hermes.sh` | 130 | Installs Promtail v3.2.1, writes config, enables the unit |
| `README.md` | 109 | Docker one-liner for LGTM, env var setup, metric table |
| `tests/**` | 699 | Unit tests for shim, cli, gateway, run_agent paths |

## Architecture

### Single backend, env-var config

There is no multi-backend fan-out, no yaml config, no sampling knob.
Everything is driven by six env vars read once at module import:

```python
OTEL_ENABLED                 = "true"/"false"
OTEL_EXPORTER_OTLP_ENDPOINT  = "http://localhost:4318"
OTEL_EXPORTER_OTLP_HEADERS   = "k=v,k=v"
OTEL_SERVICE_NAME            = "hermes-agent"
OTEL_SERVICE_VERSION         = "0.8.0"
OTEL_ENVIRONMENT             = "development"
OTEL_PROFILE                 = ""            # free-form tenant/profile tag
OTEL_METRICS_INTERVAL_SECS   = 10
OTEL_TOOL_HEARTBEAT_INTERVAL_SECS = 30
OTEL_TOOL_STALL_THRESHOLD_SECS    = 300
```

A single `BatchSpanProcessor` and a single `PeriodicExportingMetricReader` are
created at first shim construction. `_init_otel()` guards with an
`_otel_initialised` flag so multiple shims share one SDK.

### The OtelShim class

One shim wraps one `AIAgent`. Its `.callbacks` property returns the nine
callbacks hermes-agent expects, so integration is:

```python
shim   = OtelShim(agent, conversation_id=session_id, extra_attributes={...})
merged = merge_callbacks(shim.callbacks, existing_tui_callbacks)
for k, v in merged.items():
    setattr(agent, k, v)

shim.start_trace(user_message)
try:
    result = agent.run_conversation(user_message, ...)
    shim.end_trace(result["final_response"], success=True)
except Exception as e:
    shim.record_error(str(e), type(e).__name__)
    shim.end_trace(str(e), success=False)
    raise
```

`merge_callbacks` fuses multiple callback dicts so the shim's callbacks don't
clobber the TUI's pre-existing tool-start/tool-complete hooks — both fire.

### Span hierarchy

```
agent.query <conv-id suffix>           ← opened by start_trace()
├── llm.<api_mode> <N>/<model>          ← opened by _on_pre_api_request,
│                                         closed by _on_stream_complete
├── tool.<tool_name>                    ← opened by _on_tool_start,
│                                         closed by _on_tool_complete
│   ├── event: tool.started
│   ├── event: tool.heartbeat  (every 30s)
│   └── event: tool.stalled    (once, after 5min)
└── subagent.run                        ← opened by delegate_tool.py
    ├── event: subagent.started
    ├── event: subagent.heartbeat
    └── event: subagent.stalled
```

Several spans' parent context is explicitly set to the root conversation span
via `trace.set_span_in_context(self._span)` — LLM, tool, and subagent spans
are **all children of `agent.query`**. They are not nested under LLM.

That is a **different topology** from `hermes_otel`, which nests
`tool.*` under `llm.*`/`api.*` so the model's own invocation of a tool is
visible as a child of the call that produced it. The PR's flatter layout is
easier to reason about but loses "which LLM turn issued this tool call" at
the trace level — you have to use span attributes to reconstruct that.

### Base labels

Every span and metric carries three dimensional labels:

- `user.id` — source user identity (from gateway's `source.user_id`)
- `platform` — source platform (`source.platform.value`, e.g. "cli", "slack")
- `profile` — active hermes profile name

These are the primary faceting keys in the Grafana dashboard. Both
dot-notation (`user.id`) and underscore-notation (`user_id`) are attached to
spans so the spanmetrics connector's Prometheus export sees underscore
versions while Tempo queries see dot versions.

## Instrumentation surface

### Metrics (all emitted via OTLP)

| Metric | Type | Dimensions |
|---|---|---|
| `hermes.queries.total` | Counter | query.type, + base labels |
| `hermes.tool_calls.total` | Counter | tool.name, tool.type |
| `hermes.errors.total` | Counter | error.type |
| `hermes.query.latency_seconds` | Histogram | conversation.id |
| `hermes.tool.latency_seconds` | Histogram | tool.name |
| `hermes.llm.latency_seconds` | Histogram | llm.model, llm.provider, llm.finish_reason |
| `hermes.tool.stalled.total` | Counter | tool.name |
| `hermes.active_conversations` | UpDownCounter | conversation.state |
| `hermes.llm.errors.total` | Counter | llm.model, llm.provider |
| `hermes.conversation.state` | ObservableGauge | conversation.id, conversation.state |
| `hermes.conversation.state_duration_seconds` | Histogram | conversation.state, conversation.id |
| `hermes.subagent.duration_seconds` | Histogram | subagent.outcome |
| `hermes.subagent.outcome.total` | Counter | subagent.outcome |
| `hermes.subagent.stalled.total` | Counter | subagent.current_tool |

Token usage is NOT exported as a metric — it's only a span attribute.

### Traces

Root `agent.query` with attributes:
- `conversation.id`, `user.query` (truncated to 500), `user.query.length`,
  `agent.model`, `conversation.success`, `response.length`, plus base labels
  in dual dot/underscore form.

LLM spans carry `llm.model`, `llm.provider`, `llm.api_mode`,
`llm.call_number`, `llm.message_count`, `llm.tool_count`,
`llm.input_tokens_approx`, `llm.output_tokens`, `llm.finish_reason`.

Tool spans carry `tool.name`, `tool.call_id`, `tool.args` (JSON, secret-redacted,
truncated 600 chars), `tool.result_length`, `tool.error`, `tool.stalled`.

Subagent spans carry `subagent.task_index`, `subagent.goal.preview`,
`subagent.goal.length`, `subagent.model`, `subagent.toolsets`,
`subagent.outcome`, `subagent.api_calls`, `subagent.duration_seconds`,
`subagent.error`.

### Span events (attached to root span)

- `model.thinking`       — thinking content preview + length
- `agent.step`           — each agent iteration
- `status.<type>`        — status/warning/error callback messages
- `agent.clarify`        — agent paused for user clarification
- `stream.token`         — per-delta stream token length
- `state.<name>.duration` — emitted on every state transition
- `gateway.shutdown.interrupt_sent`, `gateway.shutdown.drain_timeout`,
  `gateway.shutdown.interrupt_incomplete`
- `gateway.agent.still_working`, `gateway.agent.possible_stuck`
- `gateway.agent.inactivity_warning`, `gateway.agent.inactivity_timeout`
- `tool.heartbeat`, `tool.stalled`
- `subagent.started`, `subagent.heartbeat`, `subagent.stalled`

## State machine — the headline feature

This is the most distinctive piece. A per-shim integer `_state` tracks which
of four lifecycle phases a conversation is in:

```
0 = idle
1 = thinking          (LLM API call in progress)
2 = tool_executing    (inside _on_tool_start .. _on_tool_complete)
3 = waiting_for_user  (clarify_callback fired)
```

`_set_state(new)` is called at every transition. On transition, it:

1. Records the duration of the previous state as a histogram observation
   on `hermes.conversation.state_duration_seconds{conversation.state=<old>}`
2. Adds a `state.<old>.duration` span event with `state.from`, `state.to`,
   `state.duration_ms`

Separately, all live shims register themselves in a module-level `_state_shims`
list. An **observable gauge** (`hermes.conversation.state`) is registered
with a callback that, on each metrics scrape, yields one Observation per
active shim with the current integer state. Result: you get a continuous
gauge per-conversation that you can graph as a stacked time series "how many
conversations are idle vs thinking vs in a tool call vs paused for input."

The dashboard's "Conversation State" panel queries this gauge directly, and
the "Active Conversations by State" bargauge sums it.

## Stall detection

On `_on_tool_start`, a daemon thread is spawned that wakes every
`OTEL_TOOL_HEARTBEAT_INTERVAL_SECS` (default 30s):

- Emits a `tool.heartbeat` span event with elapsed seconds.
- After `OTEL_TOOL_STALL_THRESHOLD_SECS` (default 300s / 5min), it emits a
  one-shot `tool.stalled` event and increments `hermes.tool.stalled.total`.

The `tool.stalled` attribute is also recorded on the span when it finally
ends, so you can answer "what percentage of tool calls stalled before
returning?" in Prometheus.

Same pattern exists for subagents in `tools/delegate_tool.py` (10-minute
threshold).

## Gateway shutdown & inactivity visibility

The gateway patches emit span events on the root conversation span during
shutdown and monitoring:

- `gateway.shutdown.interrupt_sent` when the gateway hits an interrupt during stop
- `gateway.shutdown.drain_timeout` when drain phase times out
- `gateway.shutdown.interrupt_incomplete` when interrupt didn't finish
- `gateway.agent.still_working` every N seconds during long runs with current
  tool, api_call_count, seconds_since_activity
- `gateway.agent.possible_stuck` when the still-working signature (tool name,
  last activity desc, API call count) repeats across notifications
- `gateway.agent.inactivity_warning` at warning threshold
- `gateway.agent.inactivity_timeout` at final timeout

This is how you debug "why did my long-running agent die?" with no other
signal than Tempo. Very useful, and not something a plugin-hook integration
can easily do because these events fire in gateway code paths that don't run
plugin hooks.

## Logs — the Promtail path

The PR ships Python logs to Loki **out of process** via Promtail, not via an
OTel log handler. Four scrape targets:

```
~/.hermes/logs/gateway.log    → labels {app=hermes-agent, component=gateway}
~/.hermes/logs/agent.log      → labels {app=hermes-agent, component=agent}
~/.hermes/logs/errors.log     → labels {app=hermes-agent, component=errors}
~/.hermes/sessions/*.jsonl    → labels {app=hermes-agent, component=sessions}
```

Pushed to `http://localhost:3100/loki/api/v1/push`. A systemd unit is
installed by `setup-hermes.sh` which downloads Promtail v3.2.1 for the host
arch and writes the config.

There's no trace-to-log correlation here — logs don't carry `trace_id` /
`span_id` because Python logging isn't OTel-instrumented. The dashboard has
a "Logs of Hermes" panel that filters by `{app="hermes-agent"}` in Loki but
doesn't link from a span to the log line.

The OTel collector config does contain an `otlp` → `otlp_http/logs` pipeline
(`endpoint: http://127.0.0.1:3100/otlp`), so if the app ever started
exporting logs via OTLP they'd flow through the collector too. It's unused
by the current shim code — nothing in Python calls the logs SDK.

## The LGTM stack config

`otel/otel-lgtm/otelcol-config.yaml` is a collector config intended to run
inside `grafana/otel-lgtm:latest` (an all-in-one Docker image that bundles
Grafana + Loki + Tempo + Mimir/Prometheus + the collector). Highlights:

- Receivers: `otlp` (gRPC 4317, HTTP 4318) and `prometheus/collector` scraping
  the collector's own `:8888` internal metrics.
- **`spanmetrics` connector** — synthesizes Prometheus histograms
  (`traces_spanmetrics_calls_total`, `traces_spanmetrics_latency_bucket`)
  from incoming spans with dimensions `user_id`, `platform`, `profile`. This
  gives you RED metrics for free without client-side instrumentation.
- Exporters: Prometheus OTLP at `:9090/api/v1/otlp`, Tempo OTLP at `:4418`,
  Loki OTLP at `:3100/otlp`.
- Pipelines:
  - `traces` → Tempo + spanmetrics
  - `metrics/spanmetrics` → Prometheus
  - `metrics` → Prometheus (receives from `otlp` + `prometheus/collector`)
  - `logs` → Loki

The Grafana dashboard queries Prometheus for most panels, Tempo for trace
list panels (`{ status=ok && span:name =~ "agent.*"}`), and Loki for the logs
panel.

## Grafana dashboard

`otel/grafana/hermes_observability_dashboard.json` — tagged `["hermes",
"opentelemetry"]`, 10s refresh. Variables are `profiles`, `user_id`,
`platforms`, `interval`.

Panel inventory:

- **Top row (per-profile):** Queries/sec, Tool Calls/sec, Total Queries by
  Type (bargauge), Total Tool Calls by Tool (bargauge), Total Queries
  (stat), Active Conversations (gauge).
- **Trace list — LLM Duration:** Tempo query `{ span:name =~ "llm.chat.*"}`.
- **Query Latency p50/p95/p99** (histogram_quantile over
  `hermes_query_latency_seconds_bucket`).
- **Tool Latency p50/p95/p99**, **LLM Latency p50/p95/p99**.
- **Errors/sec** (both `hermes_errors_total` and `hermes_llm_errors_total`).
- **OTel Collector throughput** (self-observability:
  `otelcol_receiver_accepted_spans_total`,
  `otelcol_receiver_accepted_metric_points_total`).
- **Trace Calls/sec + Trace Duration** — spanmetrics-driven (RED metrics
  derived by the collector, not the app).
- **Conversation State** — time series of the observable gauge, one line per
  conversation.
- **Active Conversations by State** — bargauge stacked by `conversation_state`.
- **State Duration by State p50/p95/p99**.
- **Traces row:** four Tempo trace list panels —
  agent.query success, agent.query errored, non-agent.query success, all errored.
- **Logs row:** Loki panel filtering by `{app="hermes-agent"}`.
- **Subagent row:** outcome counts, average duration by outcome, stalled count,
  tool stalled count.

## Small details worth noticing

- **Secret redaction** — `_sanitise_args` runs a regex over JSON-encoded
  tool args that redacts the *value* of any key matching
  `api_key|token|secret|password|auth|credential|key`. Truncates to 600
  chars. Simple but effective.
- **Query classification** — `_classify_query` does string matching to
  bucket the user's query into `cob|status|action|report|general`. The
  `cob` (close-of-business) label gives away this was written for a specific
  internal use case.
- **Tool classification** — `_classify_tool` buckets tool names by prefix
  into `shell|file|web|delegate|browser|mcp|other`.
- **Error heuristic** — `_is_error_result` looks for strings like `error`,
  `exception`, `failed`, `denied`, `timeout`, `not found`, `command failed`
  in the tool result. False positives are guaranteed (e.g. `grep error
  logs/foo.log` is not an error) but it's cheap and good enough for a
  coarse error rate metric.
- **Span entry/exit is manual:** spans are opened with
  `_tracer.start_span(...)` then `span.__enter__()` and later
  `span.__exit__(None, None, None)` rather than wrapped in `with`. Works but
  will leak a span if an exception escapes between the two.
- **Idempotency** — `_otel_initialised` guard prevents re-init, but the
  `_state_shims` module list is append-only (shims are never removed when
  their conversation ends — minor memory leak over many sessions).

## Quality assessment

Mixed. Some things are well thought out, some are clearly hand-written for
one deployment's needs, and a few details are sloppy:

**Good:**
- State machine as observable gauge is a genuinely original idea.
- Tool/subagent stall detection via heartbeat threads is practical.
- Gateway lifecycle events (shutdown, drain, inactivity) capture signals
  that plugin-hook-based OTel *cannot* see — this is a real advantage of
  core-code integration.
- Spanmetrics connector in the collector is the right way to get RED
  metrics without instrumenting every caller.
- LGTM single-container stack is the right default for "show me observability
  in one docker command."
- Pre-built dashboard with per-profile/per-user/per-platform variables.

**Questionable:**
- Env-var-only config (no yaml, no sampling, no backend fan-out) keeps the
  code simple but locks you to one collector.
- `OTEL_PROFILE` conflates "tenant identifier" with "Hermes profile name"
  depending on how you read the code.
- The `_state_shims` list leaks.
- `_classify_query` "cob" bucket is hardcoded business vocabulary.
- Gateway patches are 165 lines of inline code interleaved with business
  logic, many copies of `_otel_shim = getattr(agent, "_otel_shim", None)`
  followed by `if _otel_shim and getattr(_otel_shim, "_otel_enabled", ...)`.
  This will rot — a cleaner decorator or middleware would be easier to
  maintain.
- Manual `span.__enter__()` / `__exit__()` pattern instead of a context
  manager is fragile.
- No trace-log correlation despite shipping logs. Adding `trace_id` to the
  log format would cost ~5 lines.
- The PR description checkboxes admit "I've run `pytest tests/ -q`" is
  **unchecked** and "I've added tests for my changes" is **unchecked** —
  though the PR does ship ~700 lines of tests. So the checkboxes are stale.
- README has typos: duplicated step "6.", double-slash in docker volume
  path (`./otel//otel-lgtm/...`), references `grafana-dashboards/` but the
  file lives at `otel/grafana/`.
- `#import fs` dead line at top of `otel_shim.py`.

**Looks AI-ish:**
- Heavy comment density explaining every line.
- Long defensive `try/except: pass` blocks wrapping every OTel call. This
  is actually the right pattern for observability code (don't let
  instrumentation crash the app), but the sheer volume reads as generated.
- Helper functions that duplicate stdlib/SDK behavior (`_parse_headers`
  reimplements `OTEL_EXPORTER_OTLP_HEADERS` parsing that the SDK already
  does).
- Attribute names sometimes repeated for no reason (e.g. `"user.id"` and
  `"user_id"` set to the same value on every span — done deliberately for
  spanmetrics, but the explanation is buried).

**Bottom line:** not slop, not pristine either. The architectural ideas are
good and worth borrowing. The implementation details often aren't; reach
for the pattern, not the code.

## What to take from it (short version)

1. **LGTM-in-a-box** docker-compose + collector config (traces, metrics,
   logs, spanmetrics RED, all in one container).
2. **Pre-built Grafana dashboard** targeted at Tempo + Prometheus + Loki
   with profile/platform/user variables.
3. **State machine** — idle/thinking/tool/waiting as an observable gauge +
   state-duration histogram.
4. **Stall detection** — heartbeat thread that emits `heartbeat` events and
   a one-shot `stalled` event after threshold, with a counter metric.
5. **Subagent spans** — treat delegate_task as a first-class nested span
   with its own duration and outcome metric.
6. **Logs pipeline** — either via Promtail (no app change) or via the
   Python OTel log handler (so logs carry `trace_id` / `span_id`).
7. **spanmetrics connector** — cheap RED metrics without client-side
   instrumentation.

Adoption plan for each of these is in `otel_plan.md`.
