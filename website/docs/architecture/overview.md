---
sidebar_position: 1
title: "Overview"
description: "How the plugin is wired up end to end — hook registration, tracer init, span lifecycle, span export."
---

# Architecture overview

A tour of what happens from the moment Hermes starts to the moment a span lands in your backend UI.

## 1. Plugin registration

When Hermes starts, it walks `~/.hermes/plugins/` and loads each plugin's `__init__.py`. For hermes-otel, that calls `register(ctx)`:

```python
# hermes_otel/__init__.py (simplified)
def register(ctx):
    from . import hooks
    from .debug_utils import debug_log
    from .tracer import get_tracer

    tracer = get_tracer()
    tracer.init()                 # Reads config, resolves backends, builds the pipeline
    if not tracer.is_enabled:
        return                     # HERMES_OTEL_ENABLED=false → bail out silently

    for hook_name, callback in hooks.HOOKS:   # the one (hook name, callback) table
        try:
            ctx.register_hook(hook_name, callback)
        except Exception:
            debug_log(f"{hook_name} hook unavailable")
```

`hooks.HOOKS` lists every hook `plugin.yaml` declares (session, tool, LLM and API-request hooks, approvals, sub-agents, auxiliary calls, kanban, the `transform_*` observers and the smaller observer hooks). Each registration is wrapped in try/except so a Hermes release that lacks a hook name does not break the rest; the plugin degrades to the hooks that host exposes.

## 2. Tracer init

`tracer.init()` reads [`config.yaml`](/configuration/yaml) + env vars, resolves the backend list (single-backend env-var detection, or multi-backend from YAML, in `backends.py`), and hands the resolved backends to `pipeline.build_pipeline()`, which sets up the OTel SDK:

```text
Backend resolver
  ↓
For each backend:
  SpanExporter (OTLP/HTTP)
  ↓
  BatchSpanProcessor (bounded queue + worker thread)
  ↓
Attach to single TracerProvider (all processors)
```

Each backend gets its own processor — that's the non-blocking-per-backend isolation that makes multi-backend safe.

## 3. Hooks → spans

Hermes fires lifecycle events; the plugin translates each into a span operation:

| Hook | What happens |
|---|---|
| `on_session_start` | `tracker.start("session.{platform}", parent=None)`; stash root |
| `pre_llm_call` | `tracker.start("llm.{model}", parent=session_root)` |
| `pre_api_request` | `tracker.start("api.{model}", parent=current_llm)` |
| `post_api_request` | Set token counts on the `api.*` span, `tracker.end` it |
| `pre_tool_call` | `tracker.start("tool.{name}", parent=current_api or current_llm)` |
| `post_tool_call` | Set result / outcome on `tool.*` span, `tracker.end` it |
| `post_llm_call` | Set output on `llm.*`, `tracker.end` it |
| `on_session_end` | Compute turn summary, set on the `agent` / `cron` root, `tracker.end`, force-flush |

All handled by `SpanTracker` (in `span_tracker.py`), which keeps a per-session parent stack so parent/child relationships are correct even when hooks interleave.

## 4. Span export

`span.end()` doesn't POST anything — it enqueues the finished span on every attached processor's queue. Each processor's worker thread wakes on its schedule (1 second by default) and POSTs a batch to its OTLP endpoint.

```text
span.end()
  ↓
[processor 1 queue] → worker 1 → POST https://phoenix.internal/v1/traces
[processor 2 queue] → worker 2 → POST https://cloud.langfuse.com/api/public/otel/v1/traces
[processor 3 queue] → worker 3 → POST http://jaeger:4318/v1/traces
```

A slow backend backs up its own queue only. The agent's hot path is unaffected.

## 5. Shutdown

Two shutdown paths:

- **Graceful** — `on_session_end` runs, `force_flush_on_session_end: true` drains each span queue from a background thread (500 ms per backend, coalesced). Plus an `atexit` handler on process exit synchronously drains whatever's left, metrics and logs included.
- **Hard crash** — no atexit, no force-flush. Up to `schedule_delay_ms` of spans can be lost (default: 1 second's worth).

This is the standard OTel trade-off and matches every production tracing stack — the alternative is blocking on every span end, which defeats the purpose.

## The file map

Every path below is relative to `hermes_otel/` — the package directory that `hermes plugins install briancaffey/hermes-otel/hermes_otel` unpacks into `~/.hermes/plugins/hermes_otel/`.

Each line is the module's own docstring in one sentence.

**Entry point and pipeline**

| File | Role |
|---|---|
| `__init__.py` | Plugin entry point: `register(ctx)` initialises the tracer and loops over `hooks.HOOKS` |
| `plugin.yaml` | Hermes manifest — declares provided hooks |
| `tracer.py` | `HermesOTelPlugin`, the singleton tracer manager: `init()` reads the config, resolves the backends and holds the one `Pipeline`; `record_metric`; `get_tracer()` |
| `pipeline.py` | The export pipeline: `build_pipeline()` wires the Resource, one `BatchSpanProcessor` per backend behind the live-store and MCP-ping-filter processors, one metric reader per metrics backend and the logs pipeline into one `Pipeline` with `force_flush()` and `shutdown()` |
| `pipeline_env.py` | Leaf module: the OTLP endpoint and header environment the exporters see, the host platform the Resource describes, and the token-usage attribute filter the root roll-up applies |
| `flush.py` | `Flusher`: the coalesced background turn-end flush |
| `probe.py` | Backend reachability probe: one TCP connect per distinct endpoint at init, on a background thread, one log line per backend |

**Configuration and backends**

| File | Role |
|---|---|
| `plugin_config.py` | Declarative configuration: every field with env var > `config.yaml` > default precedence and its metadata (doc, group, bounds, choices); `resolve_config_path` |
| `backend_config.py` | The `backends:` list of `config.yaml`: one `BackendConfig` per destination and the loader that builds them |
| `backend_specs.py` | The backend manifest: one `BackendSpec` per type (signals, presets, env-var opt-in, credential fallbacks, Settings-tab facts, adapter module); `SPECS`, `BY_TYPE`, `spec_for` |
| `backends.py` | Backend resolution: `BackendConfig` objects or environment variables → `_ResolvedBackend` (endpoint URLs, headers with auth baked in, display name); stateless, no SDK imports |
| `settings_report.py` | Effective-settings report for the Settings tab: every field with its value, source and what the other sources said, the env-var inventory, credential masking, the effective YAML |
| `mcp_config.py` | Derives an `opentelemetry-mcp` server configuration from the configured backends (`otel.py mcp-config`) |

**Hooks, spans and sessions**

| File | Role |
|---|---|
| `hooks/` | Hook callbacks, one module per hook family (`session`, `tools`, `llm`, `api_call`, `auxiliary`, `approval`, `subagent`, `kanban`, `transforms`, `observers`, `propagation`); `attributes.py` and `usage.py` hold the shared attribute and token-usage builders, `skills.py` the skill inference, `_common.py` the tracer lookup, fail-open wrapper and preview policy; `__init__.py` re-exports every callback and holds `HOOKS` |
| `span_tracker.py` | `SpanTracker`: active-span registry, per-session parent stack, the pending-start table, orphan sweep, `end_all` |
| `session_state.py` | Per-session aggregation (token totals, first input / last output, turn summary) flushed onto the root span at session end |
| `semconv.py` | The telemetry vocabulary: every attribute name, the fallback chains for both conventions, span-kind classification and event names; pure data, and the source of the reference tables |
| `redaction.py` | `redact_text`: Hermes' own redactor when the plugin runs inside Hermes, a built-in pattern set otherwise |

**Metrics**

| File | Role |
|---|---|
| `instruments.py` | The metric instruments: one `InstrumentSpec` table drives creation and `record_metric`, plus the Views, temporality and aggregation helpers |
| `host_bridge.py` | `HostMetrics`: the host sampler's lifecycle, its mirror into the live store and the observable instruments on the meter |
| `host_metrics.py` | In-process host sampler: process-tree and whole-host CPU plus GPU readings, one reading per `interval_ms` into a bounded ring |
| `gpu_probe.py` | GPU readings (AMD via amdsmi, NVIDIA via pynvml); both SDKs imported lazily |

**Logs**

| File | Role |
|---|---|
| `log_handler.py` | The OTel logs pipeline: `logging` → `LoggingHandler`, `HermesLogProcessor` (trace and span attribution, session attributes, redaction), one `BatchLogRecordProcessor` per log-capable backend |
| `log_events.py` | The structured events the hooks emit as OTel log records with `event_name`, on the plugin's own scope |
| `severity.py` | The one log-severity vocabulary: Python levels, OTel severity numbers and level names translated in one place; the dashboard adapters import it too |

**Live store and query CLI**

| File | Role |
|---|---|
| `live_store.py` | `LiveStore`: the bounded SQLite (WAL) file the gateway writes and the dashboard reads; one `events` table with indexed columns extracted at insert time |
| `live_store_registry.py` | Which `LiveStore` a process uses: `get_live_store` (the gateway's one writer) and `get_live_store_for_home` (one reader per profile in the dashboard) |
| `live_summary.py` | Pure trace arithmetic over stored span dicts: `summarize_trace` with its root, totals and delegation roll-ups, shared by the live store, the dashboard API and the query CLI |
| `buckets.py` | Time-bucket aggregation shared by the live store and `dashboard/backends/metrics_math`; a leaf module |
| `query_cli.py` | The terminal query tool behind the `observability` skill: recent turns, one turn as a span tree, sessions, totals, metrics, logs, read-only SQL |
| `query_sources.py` | Where the CLI reads from: `LiveSource` (the SQLite live store) and `BackendSource` (a dashboard adapter) behind the same six methods |
| `query_render.py` | Terminal rendering for the CLI: formatters, tables, the span tree and `span_summary`, driven by a per-kind field table |
| `skills/observability/` | The `observability` skill: `SKILL.md` and `scripts/otel.py`, a launcher for `query_cli.main` |

**Dashboard**

| File | Role |
|---|---|
| `dashboard/` | The tab's Python side: `plugin_api.py` (FastAPI routes at `/api/plugins/hermes_otel/*`), `live_api.py` (the `/live/*` routes over the live store), `route_helpers.py` (plugin-module import, query windows, the bucket-count guard); `dist/` holds the built front-end bundle |
| `dashboard/backends/` | The adapter registry (`__init__.py`), `base.py` (`BackendAdapter`, the search template, the HTTP core), one adapter module per backend type, and the shared pieces: `shapes` (card rows, OTLP spans, the decoders, `coerce_tag`), `paging`, `metrics_math`, `timeconv`, the `_prometheus` / `_loki` mixins and `_attrs` |

**Utilities**

| File | Role |
|---|---|
| `helpers.py` | Text helpers, no OTel import: clipping, serialising, the plugin version, the fleet profile name and W3C `traceparent` parsing |
| `coerce.py` | Scalar coercion (`to_int`, `to_optional_int`, `optional_number`, `coerce_bool`) shared by the hooks, the live store and the query CLI; a leaf module |
| `hermes_home.py` | Which Hermes home and profile this plugin instance belongs to, through the context-local override a multiplexed gateway sets, not `HERMES_HOME` |
| `debug_utils.py` | Optional debug log to `$HERMES_HOME/plugins/hermes_otel/debug.log`, with the SDK's warnings forwarded into it |
| `testing.py` | The supported test seam: `reset_state`, `install_singleton`, `attach_pipeline`, `attach_logs`, `build_inmemory_plugin` |

## Next

- **[Span hierarchy](/architecture/span-hierarchy)** — what each span carries
- **[Attribute conventions](/architecture/attributes)** — the dual-convention mapping
- **[Turn summary](/architecture/turn-summary)** — the rolled-up attributes on the session root
- **[Tool identity](/architecture/tool-identity)** — how `hermes.tool.command` / `target` / `outcome` / `skill` get inferred
- **[Orphan-span sweep](/architecture/orphan-sweep)** — how crashed sessions get cleaned up
