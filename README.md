# hermes-otel

OpenTelemetry plugin for [Hermes Agent](https://github.com/nousresearch/hermes-agent). Automatically exports LLM tool calls, model invocations, and API requests as OTel spans to any OTLP-compatible backend.

## Backends

Tested with:
- **[Phoenix](https://github.com/Arize-ai/phoenix)** (local or cloud)
- **[Langfuse](https://langfuse.com/docs)** (cloud or self-hosted)
- **[LangSmith](https://smith.langchain.com/)** (LangChain's tracing platform)

Any OTLP HTTP endpoint should work.

- For Pheonix see [docker-compose/pheonix.yaml](docker-compose/pheonix.yaml)
- For Langfuse see [https://langfuse.com/self-hosting/deployment/docker-compose](https://langfuse.com/self-hosting/deployment/docker-compose)
- For Langsmith see [https://smith.langchain.com/](https://smith.langchain.com/)

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
docker compose -f docker-compose/pheonix.yaml up -d
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

### Optional
```bash
export OTEL_PROJECT_NAME="hermes-agent"   # Shown in Phoenix
export HERMES_OTEL_DEBUG=true             # Optional local debug log
```

**Priority order:** LangSmith (if `LANGSMITH_TRACING=true`) > Langfuse (if credentials set) > Phoenix (`OTEL_PHOENIX_ENDPOINT`).

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

## Current limitations

- **No full prompt capture** — Hermes hooks don't expose the fully-formed prompt (system message + conversation history + tool results) to plugins. API spans only receive metadata (token counts, model, duration). The raw user message and assistant response appear on the parent LLM span.
- **Langfuse auth** — Requires both public and secret keys; Basic Auth is constructed automatically. If only one key is set, Langfuse mode won't activate.
- **No gRPC** — Only OTLP over HTTP/JSON is used. gRPC exporters are not included.
- **Sync export** — Uses `SimpleSpanProcessor` (synchronous export on span end) rather than `BatchSpanProcessor` for reliability, which adds a small latency per span.
- **Single session per run** — Span tracking is in-memory; if Hermes restarts mid-session, active spans are lost.
