# Contributing to hermes-otel

Thanks for your interest in contributing. This project is an
OpenTelemetry plugin for [Hermes Agent](https://github.com/nousresearch/hermes-agent)
that exports LLM traces to a number of OTLP-compatible backends.

## Ground rules

- **Be kind.** Assume good faith, ask clarifying questions before
  pushing back hard.
- **Small, focused PRs.** One change per PR; easier to review and easier
  to revert.
- **Tests required** for new behavior, bug fixes, and new backends.
- **Docs follow code** — if you add a config knob, update
  `README.md` / `docs/` in the same PR.

## Local setup

The project uses [`uv`](https://github.com/astral-sh/uv) for dependency
management. Tests run in their own isolated environment — you do **not**
need a Hermes Agent install to hack on the plugin or run the test suite.

```bash
git clone git@github.com:briancaffey/hermes-otel.git
cd hermes-otel

# Unit + integration (fast, no Docker needed)
uv run --extra dev pytest

# Lint
uv run --with ruff ruff check

# Coverage
uv run --extra dev pytest --cov=hermes_otel --cov-report=term-missing
```

If you want to actually run the plugin inside Hermes (rare for
contributors), install it in editable mode into the hermes-agent venv:

```bash
~/git/hermes-agent/venv/bin/pip install -e .
```

## Test tiers

The suite is layered. Start with the fastest tier that covers your
change and add higher tiers if they're warranted.

| Tier | Marker | Needs | Typical use |
|------|--------|-------|-------------|
| Unit | (default) | nothing | helper logic, single-hook behavior, mocked tracer |
| Integration | (default) | nothing | real OTel SDK + `InMemorySpanExporter`, span hierarchy, metrics |
| E2E | `-m e2e` | Docker | exports to a real Phoenix / Langfuse container |
| Smoke | `-m smoke` | hermes gateway + backend running | full pipeline |

```bash
uv run --extra dev pytest                               # unit + integration (default)
uv run --extra dev --extra e2e pytest -m e2e            # all E2E tests
uv run --extra dev --extra e2e pytest -m phoenix        # Phoenix only
uv run --extra dev --extra e2e pytest -m langfuse       # Langfuse only
uv run --extra dev --extra e2e pytest -m smoke          # smoke tests
```

Docker services are started/stopped automatically by the E2E fixtures.
See `docker-compose/` and `docker-compose/all.sh`.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/). The
release-please tooling (added in a later phase) will derive versions and
a changelog from them.

```
feat(backends): add honeycomb as a first-class backend type
fix(hooks): avoid duplicate span on synthesized continuation turn
docs(config): document HERMES_OTEL_SAMPLE_RATE env var
refactor(tracer): extract SpanTracker into its own module
test(integration): cover partial multi-backend failure
chore(ci): cache uv across matrix jobs
```

Common scopes: `tracer`, `hooks`, `backends`, `config`, `helpers`,
`tests`, `ci`, `docs`, `deps`.

Breaking changes get an exclamation mark and a `BREAKING CHANGE:`
footer: `feat(config)!: rename root_span_ttl_ms to session_ttl_ms`.

## Pull-request checklist

Before opening a PR:

- [ ] `uv run --extra dev pytest` passes locally.
- [ ] `uv run --with ruff ruff check` passes (CI will fail otherwise).
- [ ] Added / updated tests that prove the behavior change.
- [ ] Updated `README.md` or `docs/` if user-visible behavior changed.
- [ ] Commit message follows Conventional Commits.
- [ ] No secrets in the diff — `config.yaml` is gitignored; use
      `config.yaml.example` for documentation.

GitHub Actions runs the unit/integration suite + ruff on every PR. E2E
and smoke tiers are intentionally not run in CI (they need Docker /
a real Hermes install); maintainers run those locally before releases.

## Adding a new backend

1. Add the resolver in `tracer.py` (for now; this will move to
   `backends.py` in a later phase).
2. Add a `docker-compose/<backend>.yaml` and a one-line entry in
   `docker-compose/all.sh` if the backend runs locally.
3. Add a `docs/backends/<name>.md` (will exist after Phase 1) or a
   section in `README.md` with: required env vars, docker-compose
   snippet, link to the project's docs.
4. Add an entry under `backends:` in `config.yaml.example`.
5. Add a unit test covering the resolver (env-var precedence, header
   construction). Integration tests via `InMemorySpanExporter` are
   encouraged; E2E tests are optional unless the backend has
   backend-specific attributes.
6. Update the "Supported backends" table in `README.md`.

## Reporting bugs

Please include:

- Python version + OS.
- `hermes-otel` version (`pip show hermes-otel`) and commit SHA if
  installed from source.
- Backend(s) configured (Phoenix / Langfuse / LangSmith / ...).
- A minimal repro — either a unit test or a snippet that calls the
  affected hook directly.
- Output with `HERMES_OTEL_DEBUG=true` if the bug is about missing /
  wrong spans.

## License

By contributing, you agree that your contributions will be licensed
under the Apache License 2.0. See `LICENSE`.
