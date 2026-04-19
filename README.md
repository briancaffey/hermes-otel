# hermes-otel

OpenTelemetry plugin for [Hermes Agent](https://github.com/nousresearch/hermes-agent). Automatically exports LLM tool calls, model invocations, and API requests as OTel spans to any OTLP-compatible backend.

## Backends

Tested with:
- **[Phoenix](https://github.com/Arize-ai/phoenix)** (local or cloud)
- **[Langfuse](https://langfuse.com/docs)** (cloud or self-hosted)
- **[LangSmith](https://smith.langchain.com/)** (LangChain's tracing platform)
- **[SigNoz](https://signoz.io)** (cloud or self-hosted)
- **[Jaeger](https://www.jaegertracing.io)** (local)
- **[Grafana Tempo](https://grafana.com/oss/tempo/)** (local or Grafana Cloud)

Any OTLP HTTP endpoint should work.

- For Phoenix see [docker-compose/phoenix.yaml](docker-compose/phoenix.yaml)
- For Langfuse see [https://langfuse.com/self-hosting/deployment/docker-compose](https://langfuse.com/self-hosting/deployment/docker-compose)
- For Langsmith see [https://smith.langchain.com/](https://smith.langchain.com/)
- For SigNoz see [docker-compose/signoz/](docker-compose/signoz/) (includes the upstream stack + port-remap notes)

## Installation

```
hermes plugins install briancaffey/hermes-otel
```

The plugin lives in `~/.hermes/plugins/hermes_otel/` and Hermes auto-discovers it via `plugin.yaml`. However, the OTel dependencies must be installed into the **hermes-agent virtual environment** (where `hermes` itself runs):

```bash
# Install OTel runtime dependencies into the hermes-agent venv
~/git/hermes-agent/venv/bin/pip install \
  opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http

# Optional: for LangSmith time-ordered run IDs
~/git/hermes-agent/venv/bin/pip install langsmith
```

You can also install the plugin package itself in editable mode (this pulls in the same OTel deps automatically):

```bash
~/git/hermes-agent/venv/bin/pip install -e ~/.hermes/plugins/hermes_otel
```

### Running tests

The test suite uses its own isolated environment via `uv` and does **not** require the hermes-agent venv:

```bash
cd ~/.hermes/plugins/hermes_otel

# Unit + integration tests (no Docker needed, <1s)
uv run --extra dev pytest

# All E2E tests (requires Docker)
uv run --extra dev --extra e2e pytest -m e2e

# Phoenix E2E only (starts a single container)
uv run --extra dev --extra e2e pytest -m phoenix

# Langfuse E2E only (starts full stack via docker compose)
uv run --extra dev --extra e2e pytest -m langfuse

# Smoke tests — full pipeline: hermes API server -> plugin -> Langfuse
uv run --extra dev --extra e2e pytest -m smoke
```

The default `pytest` run excludes E2E and smoke tests and completes in under a second.

#### Test tiers

The test suite is organized into four tiers, from fastest/simplest to slowest/most comprehensive:

| Tier | Marker | Tests | What it tests | Requirements |
|------|--------|-------|---------------|--------------|
| Unit | (default) | 109 | Hook logic, tracer init, helpers, SpanTracker | None |
| Integration | (default) | 19 | Full span export pipeline with InMemorySpanExporter, parent-child hierarchy, token roll-up, metrics | None |
| E2E | `-m e2e` | 6 | OTLP export to real Phoenix/Langfuse, queried via GraphQL/REST API | Docker |
| Smoke | `-m smoke` | 6 | Send real chats to hermes via OpenAI SDK, verify traces in Langfuse | hermes gateway + Langfuse |

**Unit tests** (`tests/unit/`) cover:
- `_safe_str`, `_to_int`, `_detect_session_kind` helper functions
- `SpanTracker` class: span lifecycle, parent stack, end_all
- `HermesOTelPlugin.init()` environment detection (Phoenix vs Langfuse vs LangSmith priority)
- `NoopSpan` graceful degradation when OTel is unavailable
- All 8 hook callbacks with mocked tracer (span names, attributes, metric recording, module-state management)

**Integration tests** (`tests/integration/`) use a real OTel SDK with `InMemorySpanExporter` — no network needed:
- Individual hook pairs produce correctly attributed spans
- Parent-child nesting: Session > LLM > API > Tool (verified via span context)
- Full session lifecycle with token aggregation and session I/O roll-up
- Metric counters and histograms via `InMemoryMetricReader`

**E2E tests** (`tests/e2e/`) invoke hooks directly against real backends and query their APIs:
- **Phoenix**: fires hooks, queries Phoenix GraphQL API at `/graphql` to verify spans
- **Langfuse**: fires hooks, queries Langfuse REST API at `GET /api/public/observations` to verify observations

**Smoke tests** (`tests/smoke/`) exercise the complete production pipeline:
- **test_hermes_api**: verifies the hermes API server is functional (health, models, chat completion)
- **test_hermes_langfuse**: sends real chats via OpenAI SDK to hermes, then queries Langfuse to confirm traces arrived with correct span names, tool spans, and token data

#### E2E backends

**Phoenix** — single container, starts in seconds:
```bash
docker compose -f docker-compose/phoenix.yaml up -d
# or let the test fixture start it automatically
```

**Langfuse** — full stack (Langfuse + Postgres + Redis + ClickHouse + MinIO), starts in ~60s:
```bash
docker compose -f docker-compose/langfuse.yaml up -d
# Pre-seeded API keys: lf_pk_test_hermes_otel / lf_sk_test_hermes_otel
# UI at http://localhost:3000, OTEL endpoint at http://localhost:3000/api/public/otel
```

The E2E fixtures will start/stop Docker services automatically if they aren't already running. If a service is already running on the expected port, it is reused.

#### Smoke tests

Smoke tests exercise the full pipeline end-to-end:

```
OpenAI SDK  -->  hermes API server  -->  LLM  -->  OTEL plugin  -->  Langfuse
                 (port 8642)                       (hooks.py)        (port 3000)
     \                                                                   /
      `--- pytest sends chat here                 pytest queries here ---`
```

They require:

1. **hermes-agent API server** running with the OTEL plugin loaded. Add to `~/.hermes/.env`:
   ```
   API_SERVER_ENABLED=true
   ```
   Then start the gateway:
   ```bash
   hermes gateway
   ```
2. **Langfuse** running with credentials configured in `~/.hermes/.env` (`OTEL_LANGFUSE_*` variables)

Tests skip automatically with a helpful message if either service is not reachable. The smoke tests poll the Langfuse observations API (up to 60-90s) to account for async trace ingestion.

## Configuration

You can either pick **one** backend via environment variables (legacy mode,
shown below), or fan **multiple** backends out in parallel via
`config.yaml`. The two are mutually exclusive — when `backends:` is set in
the yaml file, env-var detection is skipped.

### Multi-backend (`config.yaml`)

A fully annotated template lives at [`config.yaml.example`](config.yaml.example)
in the plugin root. Copy it to `config.yaml` and edit in place:

```bash
cp ~/.hermes/plugins/hermes_otel/config.yaml.example \
   ~/.hermes/plugins/hermes_otel/config.yaml
```

`config.yaml` is gitignored so local endpoints and (avoidable) secrets
never get committed. Only `config.yaml.example` is tracked. A minimal
multi-backend config looks like:

```yaml
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: jaeger
    endpoint: http://localhost:4318/v1/traces
  - type: tempo
    endpoint: http://localhost:3200/v1/traces
  - type: signoz
    endpoint: http://localhost:4328/v1/traces
    ingestion_key_env: OTEL_SIGNOZ_INGESTION_KEY   # secret from env
  - type: langfuse
    public_key_env: LANGFUSE_PUBLIC_KEY
    secret_key_env: LANGFUSE_SECRET_KEY
    base_url: https://cloud.langfuse.com
  - type: otlp                                     # any other OTLP/HTTP collector
    name: my-collector
    endpoint: http://collector:4318/v1/traces
    headers:
      X-Auth: secret
```

Every entry gets its own `BatchSpanProcessor` and (where supported) its own
`PeriodicExportingMetricReader`. Each processor owns a background worker
thread, so a slow or unreachable collector cannot block the agent's hot
path or starve the others — span end is just a non-blocking enqueue. Both
trace and metrics export run in parallel across all configured backends.

Supported `type` values: `phoenix`, `langfuse`, `signoz`, `jaeger`, `tempo`,
`otlp` (generic). Backends marked traces-only (`langfuse`, `jaeger`,
`tempo`) are auto-detected and skip the metrics reader. Override with
`metrics: true|false` per entry if needed.

### Full-conversation capture

By default the `llm.*` span's `input.value` is just the latest user turn.
The underlying `api.*` spans don't expose per-message detail. To see the
entire message list the model actually saw, flip on
`capture_conversation_history`:

```yaml
capture_conversation_history: true
conversation_history_max_chars: 40000   # safety cap; JSON is clipped with "..."
```

Or via env: `HERMES_OTEL_CAPTURE_CONVERSATION_HISTORY=true`. When enabled
the LLM span gets `input.value` = JSON-serialized history, `input.mime_type
= application/json`, and `hermes.conversation.message_count`. Phoenix
pretty-prints the JSON in its Input panel; Langfuse / Jaeger / SigNoz show
it as a large string. Respects the global `capture_previews` kill switch.

Secrets should live in env vars (use the `*_env:` keys to reference them
by name) rather than inline in yaml. LangSmith remains an env-var-only
single-backend path; setting `LANGSMITH_TRACING=true` short-circuits the
yaml backend list.

### Single backend (env vars)

**Pick one backend:**

### Phoenix
```bash
export OTEL_PHOENIX_ENDPOINT="http://localhost:6006/v1/traces"
export OTEL_PROJECT_NAME=hermes-agent
```

### Langfuse
```bash
# Option A (plugin-specific vars):
export OTEL_LANGFUSE_PUBLIC_API_KEY="pk-lf-..."
export OTEL_LANGFUSE_SECRET_API_KEY="sk-lf-..."
# Optional — defaults to EU cloud endpoint
export OTEL_LANGFUSE_ENDPOINT="https://cloud.langfuse.com/api/public/otel"
# For US region:
# export OTEL_LANGFUSE_ENDPOINT="https://us.cloud.langfuse.com/api/public/otel"

# Option B (Langfuse-standard vars from docs):
# export LANGFUSE_PUBLIC_KEY="pk-lf-..."
# export LANGFUSE_SECRET_KEY="sk-lf-..."
# export LANGFUSE_BASE_URL="https://cloud.langfuse.com"  # or us.cloud/langfuse/self-hosted base URL
```

### LangSmith
```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY="lsv2_..."
# Optional — defaults to LangChain Cloud
export LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
# Optional — project name for organizing traces
export LANGSMITH_PROJECT="hermes-langsmith-otel"
```

> **Note:** Install `langsmith` for better time-ordered run IDs: `pip install langsmith`. The plugin uses `langsmith.uuid7()` for run IDs when available, otherwise falls back to `uuid.uuid4()`.

### SigNoz
```bash
# Self-hosted (see docker-compose/signoz/ — OTLP HTTP is remapped to 4328
# to avoid colliding with Phoenix on 4318)
export OTEL_SIGNOZ_ENDPOINT="http://localhost:4328/v1/traces"
export OTEL_PROJECT_NAME=hermes-agent

# SigNoz Cloud — use the regional ingest URL + your ingestion key
# export OTEL_SIGNOZ_ENDPOINT="https://ingest.us.signoz.cloud:443/v1/traces"
# export OTEL_SIGNOZ_INGESTION_KEY="sz-..."
```

The plugin sends both traces and metrics over OTLP/HTTP. When
`OTEL_SIGNOZ_INGESTION_KEY` is set, the `signoz-ingestion-key` header is
attached to both exporters.

### Jaeger
```bash
# Jaeger ≥ 1.35 accepts OTLP/HTTP natively on port 4318
export OTEL_JAEGER_ENDPOINT="http://localhost:4318/v1/traces"
export OTEL_PROJECT_NAME=hermes-otel-jaeger
```

Jaeger is **traces-only** — the plugin skips metric export when this backend is selected. If you need token/tool/cost metrics alongside Jaeger traces, pair it with a Prometheus-compatible metrics sink or use a unified backend (Phoenix, SigNoz).

### Grafana Tempo
```bash
# Tempo accepts OTLP/HTTP natively on port 4318
export OTEL_TEMPO_ENDPOINT="http://localhost:4318/v1/traces"
export OTEL_PROJECT_NAME=hermes-otel-tempo
```

Run the upstream single-binary example (Tempo + MinIO + Grafana + Prometheus):

```bash
cd ~/git/grafana/tempo/example/docker-compose/single-binary
docker compose up -d
# UI:   http://localhost:3000   (Grafana, anonymous admin)
# OTLP: http://localhost:4318   (HTTP)  /  localhost:4317 (gRPC)
```

Tempo is **traces-only** — the plugin skips metric export when this backend is selected. The upstream example already bundles Prometheus + Grafana, so token/tool/cost metrics can be routed there via a separate Prometheus remote-write or OTel collector if needed.

### Optional
```bash
export OTEL_PROJECT_NAME="hermes-agent"   # Shown in Phoenix
export HERMES_OTEL_DEBUG=true             # Enable debug logging (see below)
```

### Debug logging

The plugin prints only essential startup messages (backend connected/failed, hook count) to stdout. For detailed per-span logging (span start/end, parent nesting, token counts, HTTP payloads), enable debug mode:

```bash
export HERMES_OTEL_DEBUG=true
```

Debug output is written to `~/.hermes/plugins/hermes_otel/debug.log` and does not clutter hermes stdout.

**Priority order:** LangSmith (if `LANGSMITH_TRACING=true`) > Langfuse (if credentials set) > SigNoz (`OTEL_SIGNOZ_ENDPOINT`) > Jaeger (`OTEL_JAEGER_ENDPOINT`) > Tempo (`OTEL_TEMPO_ENDPOINT`) > Phoenix (`OTEL_PHOENIX_ENDPOINT`).

### Shaping knobs — `config.yaml` and `HERMES_OTEL_*` env vars

Backend selection stays env-var-driven (above). For telemetry **shaping** — sampling, preview size, resource attributes, TTL, extra headers — you can also use a YAML file at `~/.hermes/plugins/hermes_otel/config.yaml`.

**Precedence (per-field):** `HERMES_OTEL_*` env var > `config.yaml` value > default.

Example `config.yaml`:

```yaml
enabled: true
sample_rate: 0.25               # ParentBased(TraceIdRatioBased) — null/omit = sample everything
root_span_ttl_ms: 600000        # orphan-sweep threshold (10 min default)
flush_interval_ms: 60000        # metrics export cadence
preview_max_chars: 1200         # clip_preview truncation limit
capture_previews: true          # false = suppress all input.value / output.value
project_name: hermes-prod       # supersedes OTEL_PROJECT_NAME
global_tags:
  team: platform
resource_attributes:            # merged into Resource; overrides global_tags on key conflict
  env: prod
  region: us-east-1
headers:                        # merged onto outgoing OTLP requests
  X-Scope-OrgID: tenant-a
```

Every field can be overridden by env var with prefix `HERMES_OTEL_` (scalars only):

| Field | Env var |
|---|---|
| `enabled` | `HERMES_OTEL_ENABLED` (`true`/`false`) |
| `sample_rate` | `HERMES_OTEL_SAMPLE_RATE` (float 0..1, or `0` to disable) |
| `root_span_ttl_ms` | `HERMES_OTEL_ROOT_SPAN_TTL_MS` |
| `flush_interval_ms` | `HERMES_OTEL_FLUSH_INTERVAL_MS` |
| `preview_max_chars` | `HERMES_OTEL_PREVIEW_MAX_CHARS` |
| `capture_previews` | `HERMES_OTEL_CAPTURE_PREVIEWS` |
| `project_name` | `HERMES_OTEL_PROJECT_NAME` |
| `span_batch_max_queue_size` | `HERMES_OTEL_SPAN_BATCH_MAX_QUEUE_SIZE` |
| `span_batch_schedule_delay_ms` | `HERMES_OTEL_SPAN_BATCH_SCHEDULE_DELAY_MS` |
| `span_batch_max_export_batch_size` | `HERMES_OTEL_SPAN_BATCH_MAX_EXPORT_BATCH_SIZE` |
| `span_batch_export_timeout_ms` | `HERMES_OTEL_SPAN_BATCH_EXPORT_TIMEOUT_MS` |
| `force_flush_on_session_end` | `HERMES_OTEL_FORCE_FLUSH_ON_SESSION_END` |

`pyyaml` is optional — if not installed, the YAML file is silently skipped and only env vars + defaults apply. Malformed YAML logs a single warning and falls back to defaults.

#### Privacy mode

Set `capture_previews: false` (or `HERMES_OTEL_CAPTURE_PREVIEWS=false`) to suppress every `input.value` / `output.value` attribute. Useful for shared deployments where message content can't leave the process. A one-line startup banner confirms the mode is active.

### Per-turn summary attributes

On `on_session_end`, the root session/agent span is enriched with a summary of what happened in the turn — so dashboards don't need to JOIN across spans.

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.turn.tool_count` | int | distinct tool names invoked |
| `hermes.turn.tools` | string | sorted CSV of distinct tool names (≤500 chars) |
| `hermes.turn.tool_targets` | string | `\|`-joined distinct file paths / URLs |
| `hermes.turn.tool_commands` | string | `\|`-joined distinct shell commands |
| `hermes.turn.tool_outcomes` | string | sorted CSV of distinct outcome statuses |
| `hermes.turn.skill_count` | int | distinct skill names inferred |
| `hermes.turn.skills` | string | sorted CSV of distinct skill names |
| `hermes.turn.api_call_count` | int | `pre_api_request` hook invocations |
| `hermes.turn.final_status` | string | `completed` \| `interrupted` \| `incomplete` \| `timed_out` |

Zero/empty aggregators are omitted rather than emitted as empty strings.

### Tool identity, outcome, skill inference

Each `tool.*` span now also carries:

- `hermes.tool.target` — first non-empty value under args.`path` / `file_path` / `target` / `url` / `uri`.
- `hermes.tool.command` — first non-empty value under args.`command` / `cmd`.
- `hermes.tool.outcome` — one of `completed` · `error` · `timeout` · `blocked` · (explicit `status` field from the result, lowercased). Only `error` maps the span `StatusCode` to `ERROR`; timeouts/blocked stay `OK` so dashboards don't count them as failures.
- `hermes.skill.name` — inferred from args paths matching `/skills/<name>/`. Does **not** match `/optional-skills/<name>/references/`. Also increments a `hermes.skill.inferred{skill_name, source}` counter so ops can audit hit rates.

### Orphan-span sweep

If a session never fires `on_session_end` (e.g. host crash mid-turn), it would otherwise leak active-span state. A TTL-based sweeper (default 10 min, configurable via `root_span_ttl_ms`) runs at the top of every `pre_*` hook; sessions older than the TTL are finalized with `hermes.turn.final_status=timed_out` and span status `OK` (not `ERROR` — timeouts should not pollute error rates).

### Non-blocking span export

Spans are exported via OpenTelemetry's `BatchSpanProcessor`: `span.end()` enqueues the span to a bounded in-memory queue, and a background worker drains that queue in batches on a timer. This means a slow or unreachable OTLP backend no longer adds latency to every tool call / API request.

**Export cadence:**
- Background worker flushes every `span_batch_schedule_delay_ms` (default 1s).
- At the end of each session (`on_session_end`), the plugin force-flushes so traces appear in the UI immediately rather than after the worker's next cycle. Disable with `force_flush_on_session_end: false` if you prefer to let the worker handle it.
- On graceful process shutdown, an `atexit` handler flushes the queue once so nothing is lost.

**Backpressure:** the queue is bounded by `span_batch_max_queue_size` (default 2048). If the agent outruns the exporter, the oldest enqueued spans are dropped — hermes keeps running rather than stalling.

**Crash vs. graceful exit:** up to `schedule_delay_millis` worth of spans may be lost on a hard crash (SIGKILL, OOM). This is the standard OTel trade-off and mirrors every production tracing stack. Graceful shutdown (`hermes gateway stop`, SIGTERM) triggers the atexit flush.

## How it works

Hermes fires lifecycle hooks. This plugin maps them to OTel spans:

```
Turn 1:
  session.{platform} / cron (root, when session hooks are available)
  └── LLM span
      └── API span (first call → stop or tool_calls)
          └── Tool span(s) (if tools called)
      └── API span (second call → final response)
```

### Span hierarchy

| Span | Kind | Contains |
|------|------|----------|
| `session.{platform}` / `cron` | GENERAL | Session metadata, completion/interruption status |
| `llm.{model}` | LLM | Model name, provider, user message (input), assistant response (output) |
| `api.{model}` | LLM | Token counts (prompt + completion), duration, finish reason, cache tokens |
| `tool.{name}` | TOOL | Tool name, arguments (input), result (output), error status |

### Attribute conventions

The plugin emits **dual-convention** attributes so both backends work:

| Metric | Langfuse (gen_ai) | Phoenix (OpenInference) |
|--------|-------------------|------------------------|
| Prompt tokens | `gen_ai.usage.input_tokens` | `llm.token_count.prompt` |
| Completion tokens | `gen_ai.usage.output_tokens` | `llm.token_count.completion` |
| Total tokens | — | `llm.token_count.total` |
| Cache read | `gen_ai.usage.cache_read_input_tokens` | `llm.token_count.cache_read` |
| Cache write | `gen_ai.usage.cache_creation_input_tokens` | `llm.token_count.cache_write` |

Langfuse uses `gen_ai.content.prompt` and `gen_ai.content.completion` for text. Phoenix uses `input.value` and `output.value`. Both are set on LLM spans.

## File structure

| File | Role |
|------|------|
| `plugin.yaml` | Plugin manifest — declares hooks to Hermes |
| `__init__.py` | Entry point — initializes tracer, registers core hooks (+ session hooks when supported) |
| `tracer.py` | OTel TracerProvider setup, span lifecycle management, parent/child tracking |
| `hooks.py` | Hook implementations — maps Hermes events to OTel spans with attributes |
| `debug_utils.py` | Optional debug logging and secret masking |
| `docker-compose/` | Docker Compose files for Phoenix and Langfuse backends |
| `tests/unit/` | Unit tests — helpers, SpanTracker, tracer init, hook callbacks |
| `tests/integration/` | Integration tests — InMemorySpanExporter, span hierarchy, metrics |
| `tests/e2e/` | E2E tests — real Phoenix/Langfuse via Docker |
| `tests/smoke/` | Smoke tests — full pipeline through hermes API server to Langfuse |

## Roadmap: additional backends

This plugin speaks plain OTLP/HTTP, so any OTLP-compatible backend should work today with no code changes — just point `OTEL_EXPORTER_OTLP_ENDPOINT` at it. The list below tracks backends I plan to formally test, add a `docker-compose/` file for, and (where applicable) cover with a smoke test.

**Status legend:** ✅ supported & tested · 🟡 should work, not yet tested/documented · 🔲 planned

| Backend | Signals | Deployment | Account / cost | Status |
|---------|---------|------------|----------------|--------|
| [Phoenix](https://github.com/Arize-ai/phoenix) | traces | Local (docker) · Arize AX cloud | OSS, no account · commercial cloud | ✅ |
| [Langfuse](https://langfuse.com) | traces | Local (docker compose) · Cloud | OSS, no account · free tier + paid | ✅ |
| [LangSmith](https://smith.langchain.com) | traces | Cloud only (self-host = enterprise) | Free personal tier · paid tiers | ✅ |
| [Jaeger](https://www.jaegertracing.io) | traces | Local (single container) | OSS, no account needed | ✅ |
| [SigNoz](https://signoz.io) | traces + metrics + logs | Local (docker compose) · Cloud | OSS, no account · free tier + paid cloud | ✅ |
| [Grafana Tempo](https://grafana.com/oss/tempo/) | traces | Local (docker compose) · Grafana Cloud | OSS, no account · free tier + paid cloud | ✅ |
| [OpenObserve](https://openobserve.ai) | traces + metrics + logs | Local (single binary / docker) · Cloud | OSS, no account · free tier + paid cloud | 🔲 |
| [Uptrace](https://uptrace.dev) | traces + metrics + logs | Local (docker compose) · Cloud | OSS, no account · free tier + paid cloud | 🔲 |
| [Honeycomb](https://www.honeycomb.io) | traces + metrics | Cloud only | Free tier + paid | 🔲 |
| [New Relic](https://newrelic.com) | traces + metrics + logs | Cloud only | Free tier (100 GB/mo) + paid | 🔲 |
| [Elastic APM](https://www.elastic.co/observability/application-performance-monitoring) | traces + metrics + logs | Local (docker) · Elastic Cloud | OSS self-host · trial + paid cloud | 🔲 |
| [Datadog](https://www.datadoghq.com) | traces + metrics + logs | Cloud only | Trial only, paid thereafter | 🔲 |

### Quick picks

- **Fully offline / no account ever:** Phoenix, Langfuse (self-hosted), Jaeger, SigNoz, Grafana Tempo+Mimir, OpenObserve, Uptrace, Elastic APM self-host. All runnable via `docker compose up`.
- **Free SaaS (personal / hobby tier, no credit card):** Langfuse Cloud, LangSmith, SigNoz Cloud, Grafana Cloud, Honeycomb, New Relic. Best if you don't want to run infrastructure.
- **Paid only (credit card required after trial):** Datadog, Dynatrace, LangSmith self-hosted (enterprise plan).

> Free-tier limits change frequently — check each vendor's pricing page before committing. The table reflects what's advertised as of this writing.

### Signals note

Jaeger and Tempo are both **traces only**. If you want both spans and the token/tool/cost metrics this plugin emits (via `PeriodicExportingMetricReader`), pair them with Prometheus, or pick one of the traces+metrics backends above.

## Current limitations

- **No full prompt capture** — Hermes hooks don't expose the fully-formed prompt (system message + conversation history + tool results) to plugins. API spans only receive metadata (token counts, model, duration). The raw user message and assistant response appear on the parent LLM span.
- **Langfuse auth** — Requires both public and secret keys; Basic Auth is constructed automatically. If only one key is set, Langfuse mode won't activate.
- **No gRPC** — Only OTLP over HTTP/JSON is used. gRPC exporters are not included.
- **Single session per run** — Span tracking is in-memory; if Hermes restarts mid-session, active spans are lost. A TTL-based sweeper finalizes abandoned sessions (see "Orphan-span sweep" above), but the orphaned process's buffered spans still need a graceful `atexit` to flush.
