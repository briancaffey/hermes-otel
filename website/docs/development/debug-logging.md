---
sidebar_position: 4
title: "Debug logging"
description: "Turn on HERMES_OTEL_DEBUG=true for hook firings, span start/end, token counts, export results and the SDK's export errors in a dedicated log file."
---

# Debug logging

The plugin prints only essential startup messages (backend connected / failed, hook count) to stdout. Everything else goes to a debug log file that's off by default.

The path follows `HERMES_HOME` (default `~/.hermes`). The file is opened once per process, line-buffered, and closed when the tracer shuts down.

## Enabling

```bash
export HERMES_OTEL_DEBUG=true
```

Then restart Hermes. The log file is:

```
$HERMES_HOME/plugins/hermes_otel/debug.log     # ~/.hermes/plugins/hermes_otel/debug.log by default
```

It's append-only — old entries stick around until you delete the file. No rotation; if you use debug mode for long periods, `rm debug.log` occasionally or pipe through `logrotate`.

## What gets logged

Lines have no timestamps and no log levels; they are written in the order things happen. Four kinds:

**Hook firings and span lifecycle**, one block per hook (indented lines belong to the hook above them):

```text
on_session_start fired: session=20260919_214809_50196a, platform=cli
start_span: agent (key=session:20260919_214809_50196a, kind=AGENT)
  session span started: key=session:20260919_214809_50196a, name=agent, synthesized=False
pre_api_request fired: model=anthropic/claude-sonnet-4.5, provider=openrouter, session=20260919_214809_50196a
start_span: api.anthropic/claude-sonnet-4.5 (key=api:186e0321-5a60-48b2-9160-5d5bf213e9f9, kind=LLM)
post_tool_call fired: tool=terminal
  span ended: status=ok, outcome=completed
post_api_request fired: model=anthropic/claude-sonnet-4.5, finish=tool_calls
  API span ended: status=ok, tokens=14691
  session span ended: key=session:20260919_214809_50196a, status=ok
```

**Export results**, one line per batch each OTLP span or log exporter sends, with the backend's display name, the batch size and the SDK's result:

```text
export Phoenix: 12 span(s) -> SUCCESS
export Langfuse: 1 span(s) -> FAILURE
export SigNoz logs: 4 record(s) -> SUCCESS
```

**The SDK's own warnings and errors**, copied from the `opentelemetry` Python loggers (they are otherwise not shown anywhere). This is where the *reason* for a `FAILURE` is:

```text
[sdk] opentelemetry.exporter.otlp.proto.http.trace_exporter ERROR: Failed to export span batch code: 404, reason: Not Found
```

Metric batches have no `export …` line of their own; a failing metrics endpoint shows up here as `Failed to export metrics batch …`.

**Fail-open reports**: a hook that raised is caught, and the line `<hook> failed open: <exception>` records it (the same event is logged once per hook and exception type at WARNING on the `hermes_otel` logger).

## What is not in the file

- **No HTTP bodies or headers.** The plugin never writes the OTLP payload, the endpoint's response body, or any header. There is therefore nothing to redact: API keys and tokens cannot appear in this file.
- **No prompt, tool-argument or tool-result text.** Hook lines carry tool names, model names, ids, statuses and token counts (`usage={...}` on `post_api_request` is the token dict). The one way user text can enter the file is inside an exception's message when a hook fails open.
- **No queue depths.** The batch queue is internal to the SDK's `BatchSpanProcessor`; when it overflows, the SDK logs a warning, which arrives as an `[sdk] … WARNING` line.

## Typical workflows

### "Spans aren't showing up in the backend"

```bash
export HERMES_OTEL_DEBUG=true
# Run one Hermes turn
tail -f ~/.hermes/plugins/hermes_otel/debug.log
```

Look for, in this order:

- `<hook> fired:` lines — are the hooks firing at all? None means the plugin is not registered (check `hermes plugins list`).
- `start_span:` / `span ended:` lines — are spans being created and closed?
- `export <backend>: … -> SUCCESS` — did the batch leave the process and did the backend accept it? A `FAILURE` is always followed or preceded by an `[sdk] … ERROR` line with the HTTP status and reason (`405 Method Not Allowed` and `404 Not Found` mean the endpoint path is wrong; `401` means the credentials are).
- No `export` line at all after the turn ended — the batch has not been sent yet (the processor flushes on `on_session_end` when `force_flush_on_session_end` is on, else every `span_batch_schedule_delay_ms`).

### "Wrong parent/child nesting"

Follow the `start_span:` lines and the `<kind> span started: key=…` lines beneath them; the key names the session, LLM, API or tool span, and the order shows which span was open when the next one started. Misnesting usually means an error path skipped a `post_*` hook, which shows as a missing `span ended:` line.

### "Token counts are zero"

Find the `ending span: key=api:…, usage={...}` line under `post_api_request`. If `usage` is `{}` or missing the provider did not return usage data (some streaming responses don't). Not a plugin bug.

## Performance impact

`debug_log` is a plain buffered write to one file handle opened on first use (no `logging` module in the path). Measured with `timeit` over 20 000 lines: about 2.4 µs per line with debug on, and about 40 ns per call with it off (one boolean check). A busy turn writes a few hundred lines. Turn it off when you're done debugging.

## Disabling

```bash
unset HERMES_OTEL_DEBUG
# or
export HERMES_OTEL_DEBUG=false
```

Restart Hermes. The file isn't deleted on disable; `rm ~/.hermes/plugins/hermes_otel/debug.log` to clean up.
