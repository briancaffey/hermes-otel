# otel_plan.md — adoption plan for ideas from PR #9596

Companion to `overview.md`. This file is an ordered, opinionated plan for
what to add to the `hermes_otel` plugin, in what order, and why. Every item
names the files it touches, the compatibility story, and the minimum viable
slice.

**Starting point** — the plugin today has multi-backend OTLP fan-out,
config yaml + env vars, rich usage attributes (OpenInference + GenAI), orphan
span sweep, BatchSpanProcessor per backend, force-flush on session end, and
docker-compose stacks for Phoenix / Langfuse / Jaeger / SigNoz. It does
**not** have: logs, an LGTM stack, a pre-built Grafana dashboard, state
tracking, stall detection, or delegate/subagent spans.

**Guiding principles** (copied from the way the plugin is already organised):

- Keep the plugin-hook integration. Don't patch hermes-agent core.
- Config belongs in `plugin_config.py` with yaml + env var precedence — no
  new env-var-only features.
- Privacy: everything that captures content must honour `capture_previews`.
- Never let instrumentation crash the host. Wrap every OTel call.
- Avoid vendor lock-in. An LGTM backend should be one of N backends, not
  *the* backend.

---

## Phase 1 — LGTM stack as a supported backend (small, high value)

**Goal:** `docker compose -p lgtm -f docker-compose/lgtm.yaml up -d` gives
the user Grafana + Tempo + Prometheus + Loki + a collector configured for
spanmetrics, and the plugin exports to it out of the box.

### 1.1 Add `docker-compose/lgtm.yaml`

One service: `grafana/otel-lgtm:latest` with host networking (or mapped ports
3000, 3100, 3200, 4317, 4318, 9090) and a volume mount for the collector
config.

```yaml
# docker-compose/lgtm.yaml
services:
  otel-lgtm:
    image: grafana/otel-lgtm:latest
    container_name: hermes-otel-lgtm
    restart: unless-stopped
    ports:
      - "3000:3000"   # Grafana UI (admin/admin)
      - "3100:3100"   # Loki
      - "3200:3200"   # Tempo UI
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
      - "9090:9090"   # Prometheus
    volumes:
      - ./docker-compose/lgtm/otelcol-config.yaml:/otel-lgtm/otelcol-config.yaml:ro
      - ./docker-compose/lgtm/grafana-dashboards:/otel-lgtm/grafana/conf/provisioning/dashboards:ro
```

### 1.2 Add `docker-compose/lgtm/otelcol-config.yaml`

Borrow the PR's collector config almost verbatim — it is genuinely good and
not novel enough to rewrite. Keep:

- `receivers: otlp` (gRPC 4317, HTTP 4318) + `prometheus/collector` scraping `:8888`
- `connectors: spanmetrics` with dimensions that match plugin conventions —
  use **OpenInference dimensions** instead of the PR's `user_id` / `platform`
  / `profile`. Candidates: `llm.provider`, `llm.model_name`, `service.name`,
  `openinference.span.kind`, plus whatever `config.global_tags` the user sets.
- `exporters: otlp_http/traces` → Tempo, `otlp_http/metrics` → Prometheus,
  `otlp_http/logs` → Loki.
- Pipelines: `traces` → Tempo + spanmetrics; `metrics/spanmetrics` → Prometheus;
  `metrics` → Prometheus; `logs` → Loki.

Diverge from the PR by **not** hardcoding `user_id` / `platform` / `profile`
— those are NousResearch-specific. Use attribute names the plugin actually
emits today.

### 1.3 Add LGTM to `all.sh`

Append `"lgtm|docker-compose/lgtm.yaml"` to the STACKS array so
`docker-compose/all.sh up` brings it up alongside phoenix/langfuse/jaeger/signoz.

Document port allocations in the README so nothing collides with the
existing phoenix stack (phoenix also wants 3000 — users running both will
need to remap LGTM's Grafana to a different port).

### 1.4 Update the README table of backends

Add a row: **LGTM** (Grafana + Tempo + Prometheus + Loki + collector) with
endpoint `http://localhost:4318` and one-line docker command. Note it's the
best choice if you want "all three signals (traces, metrics, logs) in one
UI" locally.

### 1.5 Validate the existing Tempo backend path

The plugin already supports a Tempo backend. Confirm it points at the LGTM
container's 4318. No code change should be needed — this is just end-to-end
verification with a real trace + metric.

**Files touched:** `docker-compose/lgtm.yaml` (new),
`docker-compose/lgtm/otelcol-config.yaml` (new), `docker-compose/all.sh`,
`README.md`.

**Effort:** half a day. No Python changes.

---

## Phase 2 — Pre-built Grafana dashboard

**Goal:** a JSON file that Grafana auto-provisions when the LGTM container
starts, showing queries/sec, token usage, latency percentiles, error rates,
trace lists, and logs. The user runs `up -d` and opens Grafana to a working
dashboard.

### 2.1 Base the dashboard on the PR's JSON, but rewrite the queries

The PR's metric names (`hermes_queries_total`, `hermes_tool_calls_total`,
`hermes_query_latency_seconds`, `hermes_llm_latency_seconds`,
`hermes_errors_total`, `hermes_active_conversations`) don't exist in the
plugin — the plugin emits `hermes.session.count`, `hermes.token.usage`,
`hermes.cost.usage`, `hermes.tool.duration`, `hermes.message.count`,
`hermes.model.usage`, `hermes.skill.inferred`.

Map PR-name → plugin-name for each panel, then write new PromQL:

| PR panel | Plugin equivalent query |
|---|---|
| Queries/sec | `rate(hermes_session_count_total[$interval])` |
| Tool Calls/sec | derive from spanmetrics: `rate(traces_spanmetrics_calls_total{openinference_span_kind="TOOL"}[$interval])` |
| Total Queries by Type | `sum by (session_kind) (hermes_session_count_total)` (the plugin records session kind = agent/cron) |
| Active Conversations | no equivalent today — see Phase 4 |
| Query Latency p50/95/99 | `histogram_quantile(..., rate(traces_spanmetrics_latency_bucket{span_name=~"agent\|cron"}[...]))` — uses spanmetrics |
| Tool Latency p50/95/99 | `histogram_quantile(..., rate(hermes_tool_duration_bucket[...]))` |
| LLM Latency p50/95/99 | `histogram_quantile(..., rate(traces_spanmetrics_latency_bucket{span_name=~"api\\..*"}[...]))` |
| Errors/sec | spanmetrics: `rate(traces_spanmetrics_calls_total{status_code="STATUS_CODE_ERROR"}[$interval])` |
| Token Usage | `rate(hermes_token_usage_total{token_type="input"}[...])` etc — already emitted |
| Cost | `rate(hermes_cost_usage_total[...])` — already emitted |
| Trace lists | same TraceQL patterns — `{span:name =~ "agent.*"}` etc |
| Logs | `{service_name="hermes-agent"}` once logs are shipped (Phase 3) |

### 2.2 Keep it dashboard-as-code, not dashboard-as-binary

2,788 lines of Grafana JSON is unreviewable. Two options:

- **Option A (simpler):** check in the JSON, but generate it from a short
  Jsonnet/Python script in `scripts/build_dashboard.py` so the source of
  truth is the script, not the exported blob.
- **Option B (longer but better):** use [grafanalib] or Grafana's SDK to
  generate JSON at build time from a ~200-line Python file. CI can diff the
  generated JSON against the checked-in file to catch drift.

Pick A for now. Keep a `TODO` to switch to B if the dashboard grows.

### 2.3 Auto-provision in the LGTM container

The `grafana/otel-lgtm` image reads dashboards from
`/otel-lgtm/grafana/conf/provisioning/dashboards/`. Mount
`docker-compose/lgtm/grafana-dashboards/` there (already in 1.1), drop the
JSON inside, and Grafana loads it on startup.

**Files touched:** `docker-compose/lgtm/grafana-dashboards/hermes.json` (new),
`scripts/build_dashboard.py` (new, optional).

**Effort:** 1–2 days. Most of the time is validating queries against a live
stack.

---

## Phase 3 — Logs (the part I haven't done yet)

The PR ships logs via Promtail — no Python change, logs never touch the
OTel SDK, **no trace-log correlation**. That's the easy path but it leaves
money on the table. My take: do both, but start with the OTel log handler
because it gives trace correlation for free.

### 3.1 Add an OTel logging handler (app-side, correlated)

OpenTelemetry Python has [opentelemetry-sdk] `LoggingHandler` +
[opentelemetry-exporter-otlp-proto-http] `OTLPLogExporter`. Plug it into
Python's root logger:

```python
# new: hermes_otel/log_handler.py
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

def install_log_handler(resource, endpoint, headers=None, level=logging.INFO):
    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + "/v1/logs",
                                                 headers=headers))
    )
    set_logger_provider(lp)
    handler = LoggingHandler(level=level, logger_provider=lp)
    logging.getLogger().addHandler(handler)
    return lp
```

The handler automatically attaches `trace_id` + `span_id` from the current
OTel context to each log record. In Grafana, clicking a span's logs button
filters Loki to that trace_id — this is the story Promtail can't tell.

Wire it into the existing `_init_otlp_pipeline` in `tracer.py`. Controlled
by a new config field `capture_logs: bool = False` (default off — opt-in
because it changes the global logging behaviour) and a collector capability
flag on each backend (`supports_logs: Optional[bool]`).

Per-backend fan-out: one `BatchLogRecordProcessor` per log-capable backend,
same as the existing span/metric fan-out.

### 3.2 Add an (optional) Promtail config template

For deployments where Python can't reach the collector (e.g. sandboxed
workers writing to files) or where users want to tail existing
`~/.hermes/logs/*.log` files that the plugin can't patch, keep a Promtail
option. Borrow the PR's `promtail-config.yaml` almost verbatim.

Ship it as `docker-compose/lgtm/promtail-config.yaml.example` with a
comment saying "optional — only needed if you want to tail logs that
aren't going through the OTel log handler." Do not install a systemd unit
— that's hermes-agent core territory, not the plugin's.

### 3.3 Correlation test

Write one integration test: start a session, log something inside a tool
span, verify the emitted `LogRecord` carries the span's `trace_id` in its
resource / attributes.

**Files touched:** `log_handler.py` (new), `tracer.py` (wire up),
`plugin_config.py` (new `capture_logs` field, `supports_logs` on
BackendConfig), `backends.py` (set `supports_logs` per type), docs,
`docker-compose/lgtm/promtail-config.yaml.example` (new).

**Effort:** 2 days including tests.

---

## Phase 4 — State machine + active conversations gauge

This is the PR's most original feature. Adopt it, but map to hermes-agent's
actual plugin-hook events — which don't expose quite the same state
transitions as `AIAgent` callbacks do.

### 4.1 States that map cleanly

| PR state | Triggered on | Plugin-hook equivalent |
|---|---|---|
| `idle` (0) | start of session, end of session | between `on_session_start` and first hook; after `on_session_end` |
| `thinking` (1) | LLM API in progress | between `on_pre_llm_call` / `on_pre_api_request` and the matching `post_*` |
| `tool_executing` (2) | `tool_start_callback` | between `on_pre_tool_call` and `on_post_tool_call` |
| `waiting_for_user` (3) | `clarify_callback` | **no plugin hook exists for this today.** Skip it or add a hermes-agent hook. |

`clarify` has no plugin-hook equivalent — the AIAgent callback is not
surfaced to plugins. Two options: (a) live without state 3, or (b) propose
a new `on_clarify` hook upstream. For phase 4, ship without state 3 and
note the gap.

### 4.2 Instruments

Add to `HermesOTelPlugin._create_metric_instruments`:

```python
self._conversation_state = self._meter.create_observable_gauge(
    "hermes.conversation.state",
    description="Current state per session: 0=idle, 1=thinking, 2=tool_executing",
    callbacks=[self._gather_state],
)
self._state_duration = self._meter.create_histogram(
    "hermes.conversation.state_duration_seconds",
    unit="s",
    description="Time spent in each conversation state",
)
```

### 4.3 Per-session state tracking

Add a `state: int` + `state_start: float` to `SessionState.PerSession`.
Update via a small helper:

```python
def _set_state(self, session_id, new):
    ps = self.sessions.get_or_create(session_id)
    old, old_start = ps.state, ps.state_start
    if old == new:
        return
    elapsed = time.perf_counter() - old_start
    self.record_metric("state_duration", elapsed,
                       {"conversation.state": _STATE_NAMES[old],
                        "session_id": session_id})
    ps.state = new
    ps.state_start = time.perf_counter()
```

Call it from `on_pre_llm_call` (→1), `on_post_llm_call` (→0 or keep
`tool_executing` if a tool is active), `on_pre_tool_call` (→2),
`on_post_tool_call` (→1 if still inside an llm span, else 0).

### 4.4 Observable gauge callback

Have the gauge callback iterate `tracer.sessions.all()` and yield one
Observation per active session with the current state integer + session_id
attribute. **Unlike the PR, do it through the existing `SessionState`
object, not a module-level global list** — that avoids the leak.

### 4.5 Active conversations counter

Add a module-level `_active_sessions: Set[str]` (or track count on
`SessionState`) and export as an UpDownCounter:

```python
self._active_conversations = self._meter.create_up_down_counter(
    "hermes.conversation.active",
    description="Live sessions",
)
```

`on_session_start` adds 1, `on_session_end` / orphan sweep subtracts 1.

**Files touched:** `session_state.py` (add state/state_start fields + count),
`tracer.py` (new instruments + callback), `hooks.py` (call `_set_state`),
tests.

**Effort:** 1.5 days.

---

## Phase 5 — Tool stall detection

**Goal:** emit `tool.heartbeat` events every 30s while a tool span is open,
and a `tool.stalled` event + counter after 5 minutes. Same mechanism the PR
uses, cleaner implementation.

### 5.1 Don't spawn one thread per tool call

A single background thread per process sweeping `tracer.sessions.tool_starts`
every 30s is simpler and cheaper than the PR's per-call thread model. On
each sweep:

- For every open tool start, compute elapsed.
- If elapsed > heartbeat interval since last heartbeat → emit
  `tool.heartbeat` event on the span.
- If elapsed > stall threshold and span not yet marked stalled → emit
  `tool.stalled` event, set `hermes.tool.stalled=true` attribute,
  increment `hermes.tool.stalled.total` counter.

Implementation: `threading.Thread(daemon=True)` started in `init()`,
stopped in `_force_flush` at atexit.

### 5.2 Configurable thresholds

Add to `HermesOtelConfig`:

```python
tool_heartbeat_interval_ms: int = 30_000
tool_stall_threshold_ms: int = 300_000
tool_stall_sweep_enabled: bool = True  # kill switch
```

### 5.3 Metric

New counter `hermes.tool.stalled` with dimension `tool_name`.

**Files touched:** `tracer.py` (sweeper thread), `plugin_config.py` (config
fields), `hooks.py` (track last_heartbeat on the tool start record), tests.

**Effort:** 1 day.

---

## Phase 6 — Delegate / subagent spans

This one depends on whether hermes-agent has delegate/subagent tools that
fire plugin hooks. Two sub-cases:

### 6a — If delegate_task already fires `pre_tool_call` / `post_tool_call`

Then we get one `tool.delegate_task` span per call already. We just need to
**enrich** it: recognise `tool_name == "delegate_task"` in `on_pre_tool_call`
and pull richer attributes from `args` (`goal`, `model`, `toolsets`, `task_index`).

Add a span-event pattern: when the sub-agent's own session runs under the
same process (hermes uses nested AIAgent), the sub-session's spans should
nest under the delegate span's context. Check whether hermes preserves
OTel context across the nested agent — if it doesn't, we can't improve
without core changes.

### 6b — If it doesn't, we need a hermes-agent hook

Propose `pre_subagent_start` / `post_subagent_end` hooks upstream, then
implement once merged. Out of scope for this plan.

Start with 6a. If hermes fires `pre_tool_call` for delegates, great. If not,
skip this phase until a hermes-agent hook lands.

**Files touched:** `hooks.py` (special-case `delegate_task` name),
`helpers.py` (subagent attribute extractor).

**Effort:** 0.5 day for 6a.

---

## Phase 7 — Gateway lifecycle events

This is the **one real thing a plugin can't do** that core-code integration
can. The PR's `gateway.shutdown.*` and `gateway.agent.*` events fire from
`gateway/run.py`, which doesn't run plugin hooks.

### Option A — punt

Accept that plugin-hook integration doesn't see gateway lifecycle. Document
it as a known limitation.

### Option B — push hermes-agent to add hooks

Propose upstream: `on_gateway_shutdown_start`, `on_gateway_drain_timeout`,
`on_gateway_interrupt_incomplete`, `on_agent_inactivity_warning`,
`on_agent_inactivity_timeout`. Plugin attaches these as span events on
whatever root spans are currently open per session.

### Option C — parallel "gateway shim" module

Ship a tiny `hermes_otel.gateway` module that, if imported from gateway
code, registers a handful of callbacks. Needs a one-line import in
`gateway/run.py` but no ongoing maintenance burden (all the logic is in the
plugin).

Recommendation: **A for now, B if there's a need.** Gateway lifecycle
visibility is valuable but not enough to justify either an upstream
proposal or a tight coupling. Revisit when a user actually reports "why did
my agent get interrupted?"

**Files touched:** none initially. Issue upstream if pursuing B.

**Effort:** 0 to 2 days depending on path.

---

## Phase 8 — Polish borrowed from the PR

Small, cheap adoptions:

### 8.1 Secret redaction in tool args

The plugin's existing `clip_preview` doesn't redact. Add a regex-based
redactor to `helpers.py`:

```python
_SECRET_RE = re.compile(
    r'("(?:api_key|apikey|token|secret|password|auth|credential|bearer|key)[^"]*"\s*:\s*")[^"]+(")',
    re.IGNORECASE,
)
def redact_secrets(json_str: str) -> str:
    return _SECRET_RE.sub(r'\1***\2', json_str)
```

Call from `_preview` when the preview looks like JSON. Gated by a new
config field `redact_secrets: bool = True` (default on — security feature,
on by default).

**Files touched:** `helpers.py`, `plugin_config.py`, `hooks.py` (one line),
tests.

**Effort:** 2 hours.

### 8.2 Error heuristic as an OPTIONAL enrichment

The plugin already extracts tool outcome via
`extract_tool_result_status`. The PR's string-match fallback is noisier but
catches cases where the tool result is a freeform string. Add it as a
fallback — *only* when `extract_tool_result_status` returns `None`. Keep
the result on `hermes.tool.outcome_heuristic` (separate attribute so we
don't conflate it with authoritative status).

**Files touched:** `helpers.py`, tests.

**Effort:** 2 hours.

### 8.3 Record tool.stalled attribute on end

Ties in with Phase 5. When a tool span ends, if it was marked stalled, set
`hermes.tool.stalled=true`. Enables "% of tool calls that stalled" queries
in Prometheus / TraceQL.

---

## Deliberately not adopting

- **Env-var-only config.** Plugin's yaml + env precedence is strictly
  better. Keep it.
- **Flat span topology (tool under agent, not under llm).** The plugin's
  nested topology (tool under api/llm span) is more informative for
  debugging "which model call decided to call this tool" and should stay.
- **Global `_state_shims` list.** Leak risk. Use `SessionState` instead.
- **Manual `span.__enter__/__exit__`.** Use `with tracer.start_as_current_span(...)`
  or the existing `start_span`/`end_span` pair.
- **Hardcoded query classifier buckets (`cob`, `status`, ...).** Too
  deployment-specific. If the user wants query classification, they can set
  `global_tags` or add their own span attributes.
- **130 lines of setup-hermes.sh for Promtail auto-install.** Out of scope
  for a Python plugin. Ship the config template only.

---

## Suggested execution order

1. **Phase 1** — LGTM stack (half day). Lowest risk, biggest visible win.
2. **Phase 2** — dashboard (1–2 days). Depends on phase 1 for a target.
3. **Phase 8** — polish (half day). Quick wins.
4. **Phase 4** — state machine + active conversations gauge (1.5 days).
   Most distinctive feature from the PR.
5. **Phase 5** — tool stall detection (1 day).
6. **Phase 3** — logs (2 days). Do after state+stalls so dashboards have
   something to log about.
7. **Phase 6** — subagent spans (0.5 day, only if delegate_task fires
   plugin hooks).
8. **Phase 7** — gateway events (defer indefinitely).

Total realistic effort: **~8 days** for phases 1–6. Phase 7 is open-ended.

---

## Decision points I'd like your input on

1. **Dashboard as JSON vs generated?** Check-in is easier; generated is
   maintainable. I lean toward generated via Python, but it's a judgment
   call.
2. **Phase 3 log handler opt-in vs opt-out?** Changing the root logger is
   invasive. Default opt-in feels too aggressive; I'd ship it opt-in with a
   strongly-worded "turn this on" in the README.
3. **State 3 (waiting_for_user) — worth proposing an upstream hook?** Only
   useful if we see real demand.
4. **How much of the PR's LGTM collector config to keep?** Dropping the
   `user_id` / `platform` / `profile` spanmetrics dimensions is the right
   call, but it changes what the dashboard can slice. We'll need to pick
   substitute dimensions that exist in plugin-emitted attributes.

---

## Summary — what the plugin gains

After phases 1–6, the plugin will have:

- A one-container local stack that shows **all three signals in Grafana**
  (currently: none — the existing phoenix/langfuse stacks cover traces
  but not logs, and only phoenix exposes metrics).
- A pre-built dashboard with the panel set the PR showcases, adapted to
  the plugin's actual metric names and multi-backend reality.
- **Logs with trace-id correlation**, something the PR shipped without.
- **Conversation state visibility** — the headline feature of the PR,
  implemented without its leak and without its env-var-only config.
- **Stall detection**, implemented with one sweeper thread instead of N.
- **Subagent visibility** if hermes-agent fires the right hooks.

And the plugin keeps everything it already does better than the PR:
multi-backend fan-out, yaml config, OpenInference + GenAI attribute
dual-convention, orphan sweep, per-backend BatchSpanProcessor, cache token
tracking, per-session aggregation without leaks.
