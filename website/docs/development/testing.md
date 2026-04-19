---
sidebar_position: 2
title: "Testing"
description: "Four-tier test suite: unit, integration, E2E against real Phoenix/Langfuse, smoke against a full Hermes gateway."
---

# Testing

The suite is organised into four tiers, from fastest/simplest to slowest/most comprehensive. Start with the fastest tier that covers your change; add higher tiers if they're warranted.

## Tier summary

| Tier | Marker | Count | What it tests | Requirements |
|---|---|---|---|---|
| **Unit** | (default) | 109 | Helper functions, `SpanTracker`, tracer init, hook callbacks | None |
| **Integration** | (default) | 19 | Full span pipeline with `InMemorySpanExporter`, hierarchy, token roll-up, metrics | None |
| **E2E** | `-m e2e` | 6 | OTLP export to real Phoenix/Langfuse, queried via GraphQL/REST | Docker |
| **Smoke** | `-m smoke` | 6 | Full pipeline — hermes API → plugin → Langfuse | Hermes gateway + Langfuse |

Default `pytest` runs unit + integration (no Docker, < 1 second). E2E and smoke are opt-in.

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

- Helper functions: `_safe_str`, `_to_int`, `_detect_session_kind`
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

## E2E tests (`tests/e2e/`)

Invoke hooks directly against **real backends**:

- **Phoenix** — fires hooks, queries Phoenix GraphQL API at `/graphql` to verify spans
- **Langfuse** — fires hooks, queries Langfuse REST API at `GET /api/public/observations`

Docker fixtures start/stop the containers automatically. If the container is already running on the expected port, it's reused.

```bash
# Start containers manually if preferred
docker compose -f docker-compose/phoenix.yaml up -d
docker compose -f docker-compose/langfuse.yaml up -d
```

Pre-seeded Langfuse credentials:

- Public key: `lf_pk_test_hermes_otel`
- Secret key: `lf_sk_test_hermes_otel`
- OTEL endpoint: `http://localhost:3000/api/public/otel`

## Smoke tests (`tests/smoke/`)

Exercise the complete production pipeline:

```text
OpenAI SDK  -->  hermes API server  -->  LLM  -->  OTEL plugin  -->  Langfuse
                 (port 8642)                        (hooks.py)       (port 3000)
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

GitHub Actions runs unit + integration + ruff + black on every push to `main` and every PR. Coverage is posted as a workflow artifact. E2E and smoke tiers aren't in CI — they need Docker and maintainers run them locally before releases.
