---
sidebar_position: 4
title: "Debug logging"
description: "Turn on HERMES_OTEL_DEBUG=true for per-span start/end, parent nesting, token counts, and HTTP payloads in a dedicated log file."
---

# Debug logging

The plugin prints only essential startup messages (backend connected / failed, hook count) to stdout. Everything else goes to a debug log file that's off by default.

## Enabling

```bash
export HERMES_OTEL_DEBUG=true
```

Then restart Hermes. The log file is:

```
~/.hermes/plugins/hermes_otel/debug.log
```

It's append-only — old entries stick around until you delete the file. No rotation; if you use debug mode for long periods, `rm debug.log` occasionally or pipe through `logrotate`.

## What gets logged

With debug enabled, every hook logs:

```text
[2026-04-19 14:12:33.104] pre_tool_call tool=bash session_id=abc123 parent=api.claude-sonnet-4-6 args={"command": "ls -la"}
[2026-04-19 14:12:33.812] post_tool_call tool=bash duration_ms=708 outcome=completed result_len=1284
```

Span lifecycle events:

```text
[2026-04-19 14:12:33.104] span.start name=tool.bash span_id=0x3a7b parent_span_id=0xff12 trace_id=0x0001...
[2026-04-19 14:12:33.812] span.end name=tool.bash duration_ms=708 attr_count=18
```

OTLP export attempts:

```text
[2026-04-19 14:12:34.001] export backend=phoenix batch=12 spans duration=42ms status=200
[2026-04-19 14:12:34.245] export backend=langfuse batch=12 spans duration=1284ms status=200
```

Queue warnings:

```text
[2026-04-19 14:12:40.123] ▲ phoenix queue full — dropped 5 spans (queue_size=2048)
```

And the OTLP request bodies, redacted for secrets:

```text
[2026-04-19 14:12:34.001] POST http://localhost:6006/v1/traces body={"resourceSpans": [...]} headers={"Authorization": "Bearer ***REDACTED***"}
```

## Secret masking

The debug logger passes any header value matching a known secret-carrying name (`Authorization`, `api_key`, `x-honeycomb-team`, `signoz-ingestion-key`, etc.) through a masker. Only the first 4 and last 4 characters of the value are logged; the middle is replaced with `***`.

If a secret is logged unredacted, that's a bug — open an issue.

## Typical workflows

### "Spans aren't showing up in the backend"

```bash
export HERMES_OTEL_DEBUG=true
# Run one Hermes turn
tail -f ~/.hermes/plugins/hermes_otel/debug.log
```

Look for:

- `export backend=... status=...` — is the export succeeding?
- `▲ queue full` — the queue is overwhelmed
- `span.end` count ≈ what you expect from the turn's tool calls
- No `span.start` / `span.end` at all? Check `pre_*` hook lines — the hooks might not be firing

### "Wrong parent/child nesting"

Look for `span.start` lines; verify `parent_span_id` matches the expected parent's `span_id` from a previous `span.start`. Misnesting usually means the `SpanTracker` parent stack is confused — often because of an error path that skipped a `post_*` hook.

### "Token counts are zero"

Find the `post_api_request` log line; check the `usage=` field. If it's missing or `{}`, the provider didn't return usage data (some streaming responses don't). Not a plugin bug.

## Performance impact

The debug log writes synchronously via Python's `logging` module with a `FileHandler`. Write latency is a few hundred microseconds per call — cheap, but not free. On very high-throughput deployments, debug logging can add 1-5% overhead. Turn it off when you're done debugging.

## Disabling

```bash
unset HERMES_OTEL_DEBUG
# or
export HERMES_OTEL_DEBUG=false
```

Restart Hermes. The file isn't deleted on disable; `rm ~/.hermes/plugins/hermes_otel/debug.log` to clean up.
