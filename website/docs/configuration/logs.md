---
sidebar_position: 8
title: "OTel logs"
description: "Ship Python logger.info(...) calls to Loki or any OTLP logs receiver with automatic trace-id correlation — the third OTel signal."
---

# OTel logs

Opt-in pipeline that captures Python `logging` records and ships them to any log-capable backend (Loki via the [LGTM stack](/backends/lgtm), SigNoz, or any OTLP collector) as the OTel logs signal. Each record is automatically stamped with the active span's `trace_id` and `span_id`, which is what makes the "jump from this log line to the span that emitted it" workflow in Grafana / SigNoz work.

**Off by default.** Attaching a handler to Python's root logger is invasive — it exports records from every library hermes-agent imports, not just the plugin. Turn it on deliberately.

## The switch

```yaml
# config.yaml
capture_logs: true
log_level: INFO
log_attach_logger: null   # null = root logger (default); set to scope capture
```

Or via env vars:

```bash
export HERMES_OTEL_CAPTURE_LOGS=true
export HERMES_OTEL_LOG_LEVEL=INFO
export HERMES_OTEL_LOG_ATTACH_LOGGER=hermes_otel   # optional scope
```

The plugin will attach an OTel `LoggingHandler` to the target logger and fan records out to every backend whose `supports_logs` is true (see [Which backends accept logs](#which-backends-accept-logs)).

## What correlation looks like

When `capture_logs` is on and hermes-agent code calls:

```python
logger.info("tool complete tool=%s outcome=%s", tool_name, outcome)
```

...inside an active span, the resulting Loki record carries:

```
{
  body: "tool complete tool=Bash outcome=completed",
  severity_text: "INFO",
  trace_id: "4bf92f3577b34da6...",   # ← the active span's trace_id
  span_id:  "00f067aa0ba902b7",       # ← the active span's span_id
  resource: { "service.name": "hermes-agent", ... }
}
```

In Grafana, Loki's built-in derived field picks the `trace_id` up automatically — clicking it opens the Tempo trace view. Conversely, opening a span in Tempo and clicking "Logs for this span" runs `{trace_id="<id>"}` against Loki and surfaces exactly the logs emitted during that span.

No app-side context plumbing required. The stdlib `logging` module is the integration point.

## Fields

### `capture_logs`

Master switch. `false` → pipeline disabled, no handler installed, no change to Python logging. `true` → handler attached, records flow to every log-capable backend.

### `log_level`

Minimum severity the handler accepts — `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Case-insensitive. Numeric values (e.g. `"20"`) also accepted. Defaults to `INFO`.

Records below this level never reach the OTel pipeline. Python's logger-level filtering still applies first (if the root logger is at WARNING, DEBUG records are never even created); the handler level is an additional cap on top.

### `log_attach_logger`

Which Python logger to attach the handler to.

| Value | Scope |
|---|---|
| `null` (default) | **Root logger** — captures hermes-agent + plugin + every imported library |
| `"hermes"` | Only hermes-agent's own logs (and child loggers) |
| `"hermes_otel"` | Only the plugin's logs |
| any other name | That logger's subtree only |

Start broad (root), narrow if the signal-to-noise ratio gets bad.

## Which backends accept logs?

Set per-backend via `supports_logs` (auto-derived from `type`, overrideable via `logs: true|false` in `config.yaml`):

| Backend | Logs |
|---|---|
| [Phoenix](/backends/phoenix) | ❌ (traces-only) |
| [Langfuse](/backends/langfuse) | ❌ |
| [LangSmith](/backends/langsmith) | ❌ (non-OTLP) |
| [SigNoz](/backends/signoz) | ✅ |
| [Jaeger](/backends/jaeger) | ❌ |
| [Grafana Tempo](/backends/tempo) | ❌ (traces-only; use [LGTM](/backends/lgtm) for all three signals) |
| [Generic OTLP](/backends/otlp) | ✅ (default on; collector must accept `/v1/logs`) |
| [LGTM](/backends/lgtm) | ✅ |

If `capture_logs` is on but **no** configured backend accepts logs, the plugin logs a single warning at startup and leaves Python logging alone.

## The loop-avoidance filter

The OTel HTTP exporter uses `urllib3` (via `requests`) to POST log batches to the collector. If the root logger is at DEBUG, `urllib3.connectionpool` emits a DEBUG line like `http://localhost:4318 "POST /v1/logs HTTP/1.1" 200 2` for every export — which would then get captured, batched, exported, producing another line.

The plugin installs a `logging.Filter` on its handler that **drops records from these logger prefixes**:

- `opentelemetry.*` — the SDK's own export-failure warnings
- `urllib3.*`, `httpx`, `httpcore`, `requests` — HTTP client libraries that log outbound calls

The loop isn't infinite (the `BatchLogRecordProcessor` queue is bounded) but it spams real application logs out of Loki. The filter makes the problem go away.

If you need to debug the OTel exporter itself, scope capture to a specific logger instead (`log_attach_logger: hermes_otel`) so the full firehose is out of scope.

## Startup banner

When logs are on, the plugin prints a banner so it's never silent:

```text
[hermes-otel] ✓ Logs → 2 backend(s) (attached to root, level=INFO)
```

If the banner says "attached to `hermes_otel`" you're in scoped mode — hermes-agent's own logs won't flow.

If the banner is **absent** after setting `capture_logs: true`, check:

1. `opentelemetry-sdk` is recent enough to have `LoggingHandler` in `opentelemetry.sdk._logs` (the plugin warns if the import fails).
2. At least one configured backend has `supports_logs=True`.
3. The config file is actually being read (plugin installs default handler when `pyyaml` is missing).

## Not suppressed by privacy mode

[Privacy mode](/configuration/privacy) (`capture_previews: false`) suppresses **span** previews (user messages, tool args/results) but does **not** touch logs. The log body is whatever the application passed to `logger.info(...)` — if that includes sensitive content, it flows unless the application redacts it first.

This is deliberate: privacy mode reasons about plugin-captured attributes, not about what host-app code chooses to log. If your app logs user messages at INFO and you also want those suppressed, either scope capture with `log_attach_logger` to exclude the chatty logger, or filter at the application's logging layer.

## Interaction with other signals

- **Shared resource.** Logs, traces, and metrics all inherit the same `Resource` (`service.name`, `service.version`, `global_tags`, `resource_attributes`, `project_name`). Set attributes once on the plugin's resource and they appear on every signal.
- **Per-backend fan-out.** Just like traces and metrics, each log-capable backend gets its own `BatchLogRecordProcessor` with an independent queue and worker thread. A slow Loki can't block a fast SigNoz.
- **Flushed on shutdown.** The logger provider's `force_flush` is called from the same atexit hook that flushes spans and metrics, so process exit doesn't drop buffered records.

## Verifying

Easiest smoke test: run a Hermes turn with `capture_logs: true`, open Loki, query `{service_name="hermes-agent"}`, and look for a log whose `trace_id` is non-zero. Click the `trace_id` link — it should open the Tempo trace that was active when that log fired.

For a programmatic check:

```bash
curl -s 'http://localhost:3100/loki/api/v1/query?query={service_name="hermes-agent"}' \
  | jq '.data.result[0].values[0]'
```

If the returned JSON includes a `traceID` / `trace_id` field with a 32-char hex value, correlation is working.

## See also

- [LGTM stack](/backends/lgtm) — the recommended all-signals local stack for logs.
- [SigNoz](/backends/signoz) — the other OSS backend with native logs support.
- [Generic OTLP](/backends/otlp) — pointing at any log-capable OTLP collector.
- [Env vars reference](/reference/env-vars#logs) — the three env-var overrides.
- [Config schema — top level](/reference/config-schema) — full field reference.
