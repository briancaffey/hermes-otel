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
    from .tracer import get_tracer

    tracer = get_tracer()
    tracer.init()                 # Reads config, builds exporters, attaches processors
    if not tracer.is_enabled:
        return                     # HERMES_OTEL_ENABLED=false → bail out silently

    ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.on_post_tool_call)
    ctx.register_hook("pre_llm_call", hooks.on_pre_llm_call)
    ctx.register_hook("post_llm_call", hooks.on_post_llm_call)
    ctx.register_hook("pre_api_request", hooks.on_pre_api_request)
    ctx.register_hook("post_api_request", hooks.on_post_api_request)
    ctx.register_hook("on_session_start", hooks.on_session_start)  # If available
    ctx.register_hook("on_session_end", hooks.on_session_end)       # If available
```

Session hooks are wrapped in try/except because older Hermes versions don't expose them. The plugin gracefully degrades to 6 hooks instead of 8.

## 2. Tracer init

`tracer.init()` reads [`config.yaml`](/configuration/yaml) + env vars, resolves the backend list (single-backend env-var detection, or multi-backend from YAML), and sets up the OTel SDK:

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
| `on_session_end` | Compute turn summary, set on `session.*`, `tracker.end`, force-flush |

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

- **Graceful** — `on_session_end` runs, `force_flush_on_session_end: true` synchronously drains each queue. Plus an `atexit` handler on process exit drains whatever's left.
- **Hard crash** — no atexit, no force-flush. Up to `schedule_delay_ms` of spans can be lost (default: 1 second's worth).

This is the standard OTel trade-off and matches every production tracing stack — the alternative is blocking on every span end, which defeats the purpose.

## The file map

| File | Role |
|---|---|
| `__init__.py` | Plugin entry point — wires `register(ctx)` |
| `plugin.yaml` | Hermes manifest — declares provided hooks |
| `tracer.py` | TracerProvider setup, exporter + processor wiring, backend resolution |
| `backends.py` | Per-backend URL / header builders |
| `hooks.py` | Hook callbacks — each maps a Hermes event to a span operation |
| `span_tracker.py` | Per-session parent stack, orphan sweep, end_all |
| `session_state.py` | Per-session aggregation for turn summary |
| `plugin_config.py` | Config file + env-var parsing with precedence |
| `helpers.py` | `_safe_str`, `_to_int`, `_detect_session_kind`, preview clipping |
| `debug_utils.py` | Optional debug log to `~/.hermes/plugins/hermes_otel/debug.log` |
| `langsmith_backend.py` | LangSmith-specific translation (not OTLP) |

## Next

- **[Span hierarchy](/architecture/span-hierarchy)** — what each span carries
- **[Attribute conventions](/architecture/attributes)** — the dual-convention mapping
- **[Turn summary](/architecture/turn-summary)** — the rolled-up attributes on the session root
- **[Tool identity](/architecture/tool-identity)** — how `hermes.tool.command` / `target` / `outcome` / `skill` get inferred
- **[Orphan-span sweep](/architecture/orphan-sweep)** — how crashed sessions get cleaned up
