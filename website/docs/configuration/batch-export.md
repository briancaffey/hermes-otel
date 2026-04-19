---
sidebar_position: 7
title: "Batch export tuning"
description: "Tune the BatchSpanProcessor — queue size, flush cadence, batch size, export timeout — for high-throughput deployments."
---

# Batch export tuning

hermes-otel uses OpenTelemetry's `BatchSpanProcessor` so `span.end()` is a non-blocking queue push. Each backend gets its own processor with its own worker thread and its own queue. The defaults are tuned for typical single-user agent traffic and rarely need tweaking — but for high-throughput deployments (cron jobs, many concurrent sessions, multi-tenant gateways), here are the knobs.

## The defaults

| Setting | Default | Env var |
|---|---|---|
| `span_batch_max_queue_size` | 2048 | `HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE` |
| `span_batch_schedule_delay_ms` | 1000 | `HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS` |
| `span_batch_max_export_batch_size` | 512 | `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` |
| `span_batch_export_timeout_ms` | 30000 | `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` |

## Mental model

Every second (`schedule_delay_ms`), the worker drains up to `max_export_batch_size` spans from the queue and POSTs them to the backend. If the queue has more than `max_export_batch_size` spans, the rest wait for the next tick. If the queue has more than `max_queue_size`, the oldest spans get dropped (you'll see a warning in debug logs).

```text
span.end() → [queue, up to 2048 spans] → worker (wakes every 1s) → batch (up to 512) → POST
```

## When to tune

### "I'm losing spans"

Symptoms: backend shows fewer traces than you expect, debug log shows `BatchSpanProcessor dropped N spans`.

Causes, in order of likelihood:

1. **Backend is slower than you're producing spans.** Worker can't keep up. Either reduce span volume (sampling) or bump the queue size so the buffer can absorb bursts:
   ```yaml
   span_batch_max_queue_size: 8192
   ```
2. **Schedule delay is too long.** Spans pile up between ticks. Reduce:
   ```yaml
   span_batch_schedule_delay_ms: 250
   ```
3. **Batch size is too small for the volume.** Every tick drains less than arrives. Increase:
   ```yaml
   span_batch_max_export_batch_size: 1024
   ```

### "Traces appear in the backend UI with a delay"

Default delay ceiling is `schedule_delay_ms + export_timeout_ms` + network. To see traces faster:

```yaml
span_batch_schedule_delay_ms: 250   # wake 4× per second
force_flush_on_session_end: true     # already default — flushes at end-of-turn
```

The `force_flush_on_session_end` path is the most impactful for UI latency: it synchronously flushes every backend's queue at `on_session_end`, so the full trace appears in the backend the instant the turn finishes.

### "Hermes shutdown takes forever"

On graceful exit, the plugin registers an `atexit` handler that flushes each queue with the configured export timeout. If one backend is unreachable, Python waits up to 30s per queue before giving up.

Reduce the timeout if you'd rather lose some buffered spans than wait:

```yaml
span_batch_export_timeout_ms: 5000
```

Note that a hard crash (SIGKILL, OOM) doesn't run `atexit` handlers — up to `schedule_delay_ms` of spans can be lost then. This is the standard OTel trade-off and mirrors every production tracing stack.

## Multi-backend implications

These settings are **shared** across all backends — every `BatchSpanProcessor` uses the same values. The isolation is in the queues and workers, not the tuning.

If you have one fast backend and one slow backend in the same fan-out config, the slow one's queue will fill up first and start dropping, which is the intended behavior — the fast one is unaffected.

## Verifying

Enable debug logging:

```bash
export HERMES_OTEL_DEBUG=true
```

Each export attempt logs:

```text
[hermes-otel] phoenix export: batch=487 spans, duration=42ms, status=200
[hermes-otel] langfuse export: batch=487 spans, duration=1284ms, status=200
```

A dropped-span warning looks like:

```text
[hermes-otel] ▲ phoenix queue full — dropped 12 spans
```

## Don't tune what you don't need to

The defaults are deliberately conservative and work for 99% of deployments. Start tuning only after debug logs show actual drops or UI latency becomes a concrete complaint.
