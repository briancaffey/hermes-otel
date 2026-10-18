---
sidebar_position: 2
title: "Testing"
description: "Five-tier test suite: unit, integration, the credential-free e2e tier (the real hermes binary in CI), E2E against real Phoenix/Langfuse, smoke against a full Hermes gateway."
---

# Testing

The suite is organised into five tiers, from fastest/simplest to slowest/most comprehensive. Start with the fastest tier that covers your change; add higher tiers if they're warranted.

## Tier summary

| Tier | Marker | Count | What it tests | Requirements |
|---|---|---|---|---|
| **Unit** | (default) | 109 | Helper functions, `SpanTracker`, tracer init, hook callbacks | None |
| **Integration** | (default) | 19 | Full span pipeline with `InMemorySpanExporter`, hierarchy, token roll-up, metrics | None |
| **E2E (CI)** | `-m e2e_ci` | 3 | The real `hermes` binary through the plugin: one-shot, tool-call and resumed turns against a scripted provider and an in-process OTLP receiver | `hermes` on PATH |
| **E2E** | `-m e2e` | 6 | OTLP export to real Phoenix/Langfuse, queried via GraphQL/REST | Docker |
| **Smoke** | `-m smoke` | 6 | Full pipeline — hermes API → plugin → Langfuse | Hermes gateway + Langfuse |

Default `pytest` runs unit + integration (no Docker, < 1 second). The other tiers are opt-in.

For an end-to-end pass against real backends with predictable turns (traces, metrics and logs read back per source), see the [scenario harness](/development/scenarios).

## Running

```bash
# Unit + integration (default)
uv run --extra dev pytest

# All E2E
uv run --extra dev --extra e2e pytest -m e2e

# Single backend
uv run --extra dev --extra e2e pytest -m phoenix
uv run --extra dev --extra e2e pytest -m langfuse

# Smoke tests (full pipeline)
uv run --extra dev --extra e2e pytest -m smoke

# Coverage
uv run --extra dev pytest --cov=hermes_otel --cov-report=term-missing
```

CI fails if coverage falls below 85%.

## Unit tests (`tests/unit/`)

Cover:

- Pure functions: the text helpers (`helpers.py`), scalar coercion (`coerce.py`), and each hook family's classifiers (`detect_session_kind`, tool identity, skill and outcome classification) in its own module
- `SpanTracker` class: span lifecycle, parent stack, `end_all`
- `HermesOTelPlugin.init()`: environment detection, backend priority
- `NoopSpan` graceful degradation when OTel is unavailable
- All 8 hook callbacks with mocked tracer (span names, attributes, metric recording, module-state management)

Fast, no external deps, deterministic. Run them on every save.

## Integration tests (`tests/integration/`)

Use a real OTel SDK with `InMemorySpanExporter` — no network:

- Individual hook pairs produce correctly-attributed spans
- Parent-child nesting: Session → LLM → API → Tool (via span context)
- Full session lifecycle with token aggregation and session I/O roll-up
- Metric counters and histograms via `InMemoryMetricReader`

Useful for regression-testing attribute conventions — if you change a span attribute name, an integration test will catch it.

## Credential-free end-to-end tests (`tests/e2e_ci/`)

The one tier that runs the **real `hermes` binary**, in CI, with no secret. Three
tests, each a separate `hermes chat -Q --yolo -q "<prompt>"` process in a throwaway
`HERMES_HOME` whose plugin is this checkout's `hermes_otel/`:

| test | the turn | what the receiver must show |
|---|---|---|
| `test_one_turn_exports_spans_metrics_and_events` | one scripted answer | the `agent` root (`hermes.turn.number` 1, `hermes.turn.trigger` oneshot, usage 12/7), `llm.scripted-model` under it, `api.scripted-model` under that; `hermes.token.usage` sums by token type; `hermes.turn.start` / `hermes.turn.end` events |
| `test_tool_call_turn_exports_a_tool_span` | a scripted `terminal` call (`echo e2e-marker`), then an answer | `tool.terminal` in the root's trace (`completed`), the tool output sent back to the model, `hermes.tool.call` event, `hermes.tool.duration` histogram, the root's roll-up (24 input tokens, `hermes.turn.tool_count` 1) |
| `test_resumed_turn_is_turn_two` | `hermes chat -r <session>` in a new process | `hermes.turn.number` 2 on the new trace, the replayed conversation in the model request, `hermes.session.turns` 2 ([#466](https://github.com/briancaffey/hermes-otel/issues/466) regression) |

Two loopback servers stand in for the world (`tests/e2e_ci/_servers.py`):

- **`ScriptedProvider`**, an OpenAI-compatible endpoint (`/v1/models`,
  `/v1/chat/completions`, streaming and not) that Hermes uses as
  `provider: custom`. A test loads the responses the agent loop gets, in order: a
  plain completion, or a tool call followed by the final answer. Hermes's own
  title-generation request (it carries `response_format`) is answered with a JSON
  object and never consumes a scripted step. Every request is recorded, so a test
  can also assert what Hermes sent.
- **`OtlpReceiver`**, an OTLP/HTTP endpoint for `/v1/traces`, `/v1/metrics` and
  `/v1/logs` that decodes the protobuf payloads (`opentelemetry-proto`, a dependency
  of the exporter the plugin already needs) into spans, metric points and log
  records. The plugin reaches it through a `type: otlp` backend entry.

The `HERMES_HOME` is built with the scenario harness's
`scripts/scenarios/setup_profile.py` helpers (same Hermes config shape, same plugin
copy) and the agent runs with a clean environment: no `*_API_KEY`, `OTEL_*` or
`HERMES_*` variable from your shell reaches it.

```bash
make ci-e2e                       # the whole tier; ~1-2 min the first time, ~30 s after
uv run --extra dev pytest tests/e2e_ci -m e2e_ci   # the same, with a temp home removed afterwards
```

| variable | effect |
|---|---|
| `HERMES_E2E_HOME` | the Hermes home to build and keep. `make ci-e2e` sets `~/.cache/hermes-otel/e2e-home`, so Hermes's runtime venv staging (about a minute, ~800 MB) happens once; delete the directory to start cold. Unset, a temp home is used and removed. |
| `HERMES_BIN` | the binary to run instead of the `hermes` on `PATH`. Without either the tier skips. |
| `HERMES_E2E_TURN_TIMEOUT` | seconds per `hermes chat` process (default 300). With the provider unreachable Hermes retries for minutes; the cap turns that into a failure with the process output and the plugin's `debug.log` attached. |

In CI, `.github/workflows/test.yml` (`e2e-hermes`) installs **a pinned Hermes**
(`HERMES_VERSION`, the version the maintainer runs; bump it with `hermes --version`)
together with the plugin's `hermes_otel/requirements.txt` (a pip-installed Hermes manages no
plugin venv, so the dependencies go into the same interpreter) and runs `make ci-e2e` on
every pull request. `.github/workflows/e2e-hermes-main.yml`
runs the same tier weekly against **hermes-agent main** and is allowed to fail: a
failure opens one issue (labels `bug`, `blocked: upstream`) or comments on the open
one with the run link and the log tail, and the next green run closes it. That is
where a Hermes change to a hook payload or to session resumption surfaces first.

## E2E tests (`tests/e2e/`)

Invoke hooks directly against **real backends**:

- **Phoenix** — fires hooks, queries Phoenix GraphQL API at `/graphql` to verify spans
- **Langfuse** — fires hooks, queries Langfuse REST API at `GET /api/public/observations`

Docker fixtures start/stop the containers automatically. If the container is already running on the expected port, it's reused.

```bash
# Start containers manually if preferred
docker compose -f docker-compose/phoenix/docker-compose.yaml up -d
docker compose -f docker-compose/langfuse/docker-compose.yaml up -d
```

Pre-seeded Langfuse credentials:

- Public key: `lf_pk_hermes_dev`
- Secret key: `lf_sk_hermes_dev`
- OTEL endpoint: `http://localhost:3000/api/public/otel`

## Smoke tests (`tests/smoke/`)

Exercise the complete production pipeline:

```text
OpenAI SDK  -->  hermes API server  -->  LLM  -->  OTEL plugin  -->  Langfuse
                 (port 8642)                        (hooks/)       (port 3000)
     \                                                                   /
      `--- pytest sends chat here                 pytest queries here ---`
```

Requirements:

1. **Hermes Agent API server** running with the OTEL plugin loaded. Add to `~/.hermes/.env`:
   ```
   API_SERVER_ENABLED=true
   ```
   Start the gateway:
   ```bash
   hermes gateway
   ```

2. **Langfuse** running with credentials configured in `~/.hermes/.env` (`OTEL_LANGFUSE_*` variables).

Tests auto-skip with a helpful message if either service isn't reachable. The smoke tests poll the Langfuse observations API (up to 60–90s) to account for async trace ingestion.

## Adding tests for a new backend

For a new OTLP-compatible backend:

1. **Unit:** resolver function — env var precedence, header construction, URL normalization. Mock the env, assert on the resulting exporter config.
2. **Integration:** attribute snapshot via `InMemorySpanExporter` — spin up the plugin with the backend selected, fire a hook sequence, assert on the exported spans.
3. **E2E (optional):** if the backend has backend-specific attributes or a custom auth mechanism worth verifying against a real instance.

See `tests/unit/test_backends.py` and `tests/integration/test_fanout.py` for examples.

## CI behavior

GitHub Actions runs unit + integration + ruff + black on every push to `main` and every PR, plus the credential-free e2e tier against a pinned Hermes (`e2e-hermes`). Coverage is posted as a workflow artifact. The Docker E2E and smoke tiers aren't in CI — they need Docker and maintainers run them locally before releases.

## Hook drift against Hermes

`scripts/hook_audit/hook_audit.py --hermes-agent ~/git/hermes-agent` reads Hermes's `VALID_HOOKS` straight from the checkout's source, without importing it, and compares it with `provides_hooks` in `hermes_otel/plugin.yaml`. Every hook Hermes offers must be registered or listed in the script's `NOT_REGISTERED`, with the reason. It exits 1 on:

- a hook the plugin registers that Hermes renamed or removed
- a new hook nobody has decided about
- a skip entry that has gone stale

`tests/unit/test_hook_audit.py` runs it against `$HERMES_AGENT_DIR` (default `~/git/hermes-agent`) when that checkout exists. Re-run it after every `hermes update`.

