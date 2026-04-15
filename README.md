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

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Then place this repo in `~/.hermes/plugins/hermes_otel/`. Hermes auto-discovers it via `plugin.yaml`.

Or install via pip:
```bash
pip install -e /path/to/hermes_otel   # dev mode
# or
pip install hermes-otel               # from PyPI (when published)
```

## Configuration

**Pick one backend:**

### Phoenix
```bash
export OTEL_ENDPOINT="http://localhost:6006/v1/traces"
export OTEL_PROJECT_NAME=hermes-agent
```

### Langfuse
```bash
# these keys are scoped to a project, no need to specify project name
export OTEL_LANGFUSE_PUBLIC_API_KEY="pk-lf-..."
export OTEL_LANGFUSE_SECRET_API_KEY="sk-lf-..."
# Optional — defaults to EU cloud endpoint
export OTEL_LANGFUSE_ENDPOINT="https://cloud.langfuse.com/api/public/otel"
# For US region:
# export OTEL_LANGFUSE_ENDPOINT="https://us.cloud.langfuse.com/api/public/otel"
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

**Priority order:** LangSmith (if `LANGSMITH_TRACING=true`) > Langfuse (if credentials set) > Phoenix (`OTEL_ENDPOINT`).

## How it works

Hermes fires lifecycle hooks. This plugin maps them to OTel spans:

```
Turn 1:
  LLM span (root)
  └── API span (first call → stop or tool_calls)
      └── Tool span(s) (if tools called)
  └── API span (second call → final response)
```

### Span hierarchy

| Span | Kind | Contains |
|------|------|----------|
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
| `__init__.py` | Entry point — initializes tracer, registers 6 hook callbacks |
| `tracer.py` | OTel TracerProvider setup, span lifecycle management, parent/child tracking |
| `hooks.py` | Hook implementations — maps Hermes events to OTel spans with attributes |

## Current limitations

- **No full prompt capture** — Hermes hooks don't expose the fully-formed prompt (system message + conversation history + tool results) to plugins. API spans only receive metadata (token counts, model, duration). The raw user message and assistant response appear on the parent LLM span.
- **Langfuse auth** — Requires both public and secret keys; Basic Auth is constructed automatically. If only one key is set, Langfuse mode won't activate.
- **No gRPC** — Only OTLP over HTTP/JSON is used. gRPC exporters are not included.
- **Sync export** — Uses `SimpleSpanProcessor` (synchronous export on span end) rather than `BatchSpanProcessor` for reliability, which adds a small latency per span.
- **Single session per run** — Span tracking is in-memory; if Hermes restarts mid-session, active spans are lost.
