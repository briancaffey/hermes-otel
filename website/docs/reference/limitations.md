---
sidebar_position: 5
title: "Limitations"
description: "What the plugin doesn't do, why, and what we might fix."
---

# Limitations

Things to be aware of. Some are upstream constraints; some are plugin-specific trade-offs.

## No full prompt capture (structural)

Hermes hooks don't currently expose the **fully-formed prompt** to plugins — the concatenated system message + conversation history + tool results. The `api.*` spans only receive metadata (model, token counts, duration). The raw user message and final assistant response appear on the parent `llm.*` span.

As a partial workaround, enable [conversation capture](/configuration/conversation-capture) — that attaches the message list the model was handed to the `llm.*` span's `input.value` as JSON. That covers ~95% of "what did the model see?" investigations.

If you need the fully-rendered prompt string (after Hermes' own prompt templating), that needs a Hermes change to expose it on the hook payload. File an issue upstream.

## Langfuse auth requires both keys

Langfuse's Basic Auth is constructed from the public + secret keys. If only one is set, Langfuse mode won't activate (the plugin logs a warning and falls back to the next backend in priority order).

This is a deliberate check rather than a bug — Langfuse will `401` regardless, and clearer logs beat opaque ones.

## No gRPC

Only OTLP over HTTP/JSON is used. `opentelemetry-exporter-otlp-proto-grpc` is not a dependency.

Why: HTTP is simpler to debug (curl works), has fewer moving parts (no protobuf compilation needed), and every collector accepts it. The performance difference vs. gRPC doesn't matter at the span volumes a single Hermes process produces.

If you have a backend that requires gRPC specifically, open an issue — we can add the option with a per-backend switch.

## Single session tracked in memory

The `SpanTracker`'s parent-stack state is in-memory. If Hermes restarts mid-session, any currently-open spans are lost — not exported, not resumable.

The [orphan-span sweep](/architecture/orphan-sweep) is the partial fix for this: when a new turn fires a hook after a crash, the sweep finalizes stale roots with `hermes.turn.final_status=timed_out`. But:

- Buffered-but-not-yet-exported spans in the `BatchSpanProcessor` queue of the old process are lost (hard crash: `atexit` doesn't run).
- Spans that *were* opened but never ended (and not yet swept) from the old process are just gone — the new process can't resurrect them.

Live with this. Production tracing stacks have the same trade-off; the alternative is persisting span state to disk, which has its own failure modes.

## Sub-agent linking is link-only across processes

Delegated child agents (`delegate_task`) are modeled as `subagent.*` spans, and the child's own root span rejoins the parent trace so a multi-agent run is one connected tree. This relies on the delegation span being reachable from the child's `on_session_start` — which works because Hermes runs delegated children **in the same process** (on background threads), where the plugin's in-memory sub-agent registry holds the live span.

If a future Hermes version runs delegated children **in a separate process**, that registry won't hold the live span. The plugin then degrades gracefully: the child root attaches an OTel **span link** to the delegation span's `SpanContext` (when available) and is tagged with `hermes.subagent.parent_session_id` for correlation — but the child becomes its own trace rather than nesting in the parent. True cross-process trace-context *propagation* (injecting the parent context into the child process) is out of scope for now; open an issue if you need it.

## API error capture depends on the `api_request_error` hook

Failed provider API requests (rate limits, timeouts, 5xx, network errors) are
now captured: the `api.{model}` span ends `ERROR` with a recorded exception and
retry metadata, via the `api_request_error` hook. On older Hermes builds that
don't fire that hook, a failed request's span is instead finalized by the
[orphan sweep](/architecture/orphan-sweep) on the next turn and ends `OK` — so
on those builds failures remain invisible until you upgrade Hermes.

A hard crash *before* the error hook fires (or before the next sweep) can still
lose the in-flight span, the same buffered-span trade-off described above.

Note the deliberate asymmetry: only API-level failures map to `ERROR`. Tool
`timeout`/`blocked` outcomes stay `OK` so they don't inflate error rates.

## Sampling is head-based only

`ParentBased(TraceIdRatioBased(rate))` makes the sampling decision at the **root**. There's no tail-based sampler that boosts on error or keeps all traces above a duration threshold.

Why: tail-based sampling requires buffering spans until the trace is complete, which roughly doubles memory use and adds a full trace of latency to every export.

The standard answer is to run an [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) in the middle with a `tail_sampling` processor. The Collector can fan-out to all your backends and apply tail-based policy centrally.

## LangSmith not in fan-out

Setting `LANGSMITH_TRACING=true` short-circuits the `backends:` list entirely. You can have LangSmith **or** a multi-backend fan-out, not both.

Why: LangSmith uses its own HTTP Run API rather than OTLP; its transport doesn't fit into the OTel `BatchSpanProcessor` shape.

Workaround if you need both: run the OTel Collector and have it route traces to LangSmith's OTLP-compatible beta ingest, if/when LangSmith ships one.

## Weave is trace-ingest only

W&B Weave's documented OTLP endpoint is `/otel/v1/traces`, authenticated with
the `wandb-api-key` header and routed by `wandb.entity` / `wandb.project`
Resource attributes. The dedicated `type: weave` backend therefore disables
OTLP metrics and logs by default.

If you need `hermes.*` metrics or OTel logs next to Weave traces, fan out to
Weave plus a metrics/logs-capable backend such as Phoenix, SigNoz, LGTM, or
OpenObserve. The bundled dashboard also does not query Weave; use Weave's UI
for W&B traces and point the dashboard at a local backend.

Because `wandb.entity` and `wandb.project` are Resource attributes on the
shared `TracerProvider`, one Hermes process can route to one Weave project at a
time. Multiple configured Weave entries must agree on those values.

## Debug log has no rotation

Enabling `HERMES_OTEL_DEBUG=true` appends to `~/.hermes/plugins/hermes_otel/debug.log` forever. No rotation, no size cap.

Deliberate: the debug log is meant for troubleshooting, not routine operation. If you want persistent debug logs, pipe through `logrotate` or rm the file weekly.

## Metrics labels are curated

The plugin deliberately does not put user IDs, session IDs, tool arg values, or similar high-cardinality values on metrics. Labels are restricted to low-cardinality values (model, provider, tool name, outcome, finish reason) to keep Prometheus-style TSDBs from blowing up.

If you need high-cardinality breakdowns, use traces (where every attribute is fine), not metrics.

## No cost accounting

The plugin emits token counts; it doesn't convert them to dollars. Cost is model-and-tier specific (prompt tokens vs. completion tokens, cached vs. uncached, provider pricing tiers), and baking a price table into a plugin that has to track every new model release is a losing battle.

Phoenix and Langfuse both do cost accounting server-side from the token counts. SigNoz can too with a derived metric. For plain Jaeger/Tempo, build the conversion in a dashboard query.

## Python ≥ 3.9

The plugin uses `from __future__ import annotations` and some 3.9-only stdlib features. 3.8 is EOL and not supported.
