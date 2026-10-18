---
sidebar_position: 7
title: "Batch export tuning"
description: "Tune the BatchSpanProcessor — queue size, flush cadence, batch size, export timeout — for high-throughput deployments."
---

# Batch export tuning

hermes-otel uses OpenTelemetry's `BatchSpanProcessor`, so ending a span only puts it on a queue; nothing waits on the network. Each backend gets its own processor with its own worker thread and its own queue. The defaults suit typical single-user agent traffic. For high-throughput deployments (cron jobs, many concurrent sessions, multi-tenant gateways), these are the knobs.

## The defaults

| Setting | Default | Env var |
|---|---|---|
| `span_batch_max_queue_size` | 2048 | `HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE` |
| `span_batch_schedule_delay_ms` | 1000 | `HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS` |
| `span_batch_max_export_batch_size` | `null`: 512, or 64 when `content_capture` is `full` (the default) | `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` |
| `span_batch_export_timeout_ms` | 30000 | `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` |
| `force_flush_on_session_end` | `true` | `HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END` |
| `force_flush_wait_ms` | 1500 | `HERMES_OTEL_FORCE_FLUSH_WAIT_MS` |

A batch size above the queue size is lowered to the queue size with a warning, and a value out of range (a `0` queue, a negative timeout) falls back to the default with a warning.

## Mental model

Every second (`schedule_delay_ms`), the worker takes up to `max_export_batch_size` spans from the queue and sends them to the backend. The worker also wakes early once a full batch is waiting. When the queue is full, each new span pushes out the oldest one, and the SDK logs a warning (see [Verifying](#verifying)).

```text
span.end() → [queue, up to 2048 spans] → worker (wakes every 1s) → batch (up to 64 or 512) → OTLP request
```

## When to tune

### "I'm losing spans"

Symptoms: the backend shows fewer traces than you expect, and `debug.log` shows the SDK warning `Queue full, dropping …`.

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

`force_flush_on_session_end` matters most for UI latency. At `on_session_end` it flushes every backend's span queue, and then the metric and log providers, so the full trace appears in the backend a moment after the turn finishes. The flush runs on a background thread with a 500 ms timeout per backend. Requests coalesce: a turn that ends while a flush is still queued does not queue another. The turn waits at most `force_flush_wait_ms` (1500 ms by default) for the flush before returning to Hermes, so an unreachable collector costs a turn no more than that. A one-shot `hermes -z` run exits right after its turn and skips `atexit`, so this turn-end flush is the only export it gets; `0` stops the wait and can lose that run's telemetry.

### "Hermes shutdown takes forever"

On a graceful exit, the plugin's `atexit` handler ends any open spans, flushes each backend (2 s per span processor), then shuts the processors down. An export already in flight to an unreachable backend can still hold the exit for up to `span_batch_export_timeout_ms` (30 s by default).

Reduce the timeout if you'd rather lose some buffered spans than wait:

```yaml
span_batch_export_timeout_ms: 5000
```

A hard crash (SIGKILL, OOM) doesn't run `atexit` handlers, so spans still in the queue are lost. This is the standard OpenTelemetry trade-off.

## Logs

Log records and events have their own `BatchLogRecordProcessor` per log-capable backend, tuned under the `logs:` block rather than the span keys:

```yaml
logs:
  batch:
    schedule_delay_ms: 1000        # export tick
    max_queue_size: 2048           # queued records before the oldest are dropped
    max_export_batch_size: 512     # records per POST
    export_timeout_ms: 30000       # per-export timeout
  max_attribute_length: 4096       # longest exported string attribute
```

The same model applies. Each backend's log filter runs before its batch processor, so a record that backend does not accept never takes a place in its queue. The live store is not batched by the SDK; it buffers rows itself and commits every 250 ms or every 64 rows. The logger provider is flushed from the same turn-end and `atexit` paths as spans. In `debug.log` each log export writes `export <backend> logs: N record(s) -> SUCCESS|FAILURE`.

## Multi-backend implications

These settings are **shared** across all backends — every `BatchSpanProcessor` uses the same values. The isolation is in the queues and workers, not the tuning.

If you have one fast backend and one slow backend in the same fan-out config, the slow one's queue will fill up first and start dropping, which is the intended behavior — the fast one is unaffected.

## Verifying

Enable debug logging:

```bash
export HERMES_OTEL_DEBUG=true
```

With `HERMES_OTEL_DEBUG=true`, each batch a backend's exporter sends writes one line to `$HERMES_HOME/plugins/hermes_otel/debug.log`:

```text
export Phoenix: 487 span(s) -> SUCCESS
export Langfuse: 487 span(s) -> FAILURE
```

When the queue is full the SDK drops the oldest span and logs a warning, which the debug log copies as:

```text
[sdk] opentelemetry.sdk._shared_internal WARNING: Queue full, dropping …
```

Nothing is printed to stdout for either; see [Debug logging](/development/debug-logging).

## Don't tune what you don't need to

The defaults are conservative and work for most deployments. Start tuning only after the debug log shows real drops, or when UI latency is a concrete complaint.
