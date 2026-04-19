---
sidebar_position: 1
title: "Contributing"
description: "How to clone, set up, run tests, and open a PR against hermes-otel."
---

# Contributing

Contributions are welcome. hermes-otel is a small codebase with a layered test suite — most changes are easy to add and easy to verify locally.

## Ground rules

- **Be kind.** Assume good faith, ask clarifying questions before pushing back hard.
- **Small, focused PRs.** One change per PR; easier to review and revert.
- **Tests required** for new behavior, bug fixes, and new backends.
- **Docs follow code** — if you add a config knob, update the docs in the same PR.

## Local setup

The project uses [`uv`](https://github.com/astral-sh/uv) for dependency management. Tests run in their own isolated environment — you do **not** need a Hermes install to hack on the plugin.

```bash
git clone git@github.com:briancaffey/hermes-otel.git
cd hermes-otel

# Unit + integration (fast, no Docker needed, < 1s)
uv run --extra dev pytest

# Lint
uv run --extra dev ruff check .

# Format check
uv run --extra dev black --check .

# Format apply
uv run --extra dev black .

# Coverage
uv run --extra dev pytest --cov=hermes_otel --cov-report=term-missing
```

If you want to actually run the plugin inside Hermes (rare for contributors), install it in editable mode:

```bash
~/git/hermes-agent/venv/bin/pip install -e .
```

## Code style

- **Formatting:** [black](https://black.readthedocs.io) with `line-length = 100`. Configured in `pyproject.toml`. CI fails on unformatted diffs.
- **Linting:** [ruff](https://docs.astral.sh/ruff/) with a conservative ruleset (`E`, `F`, `W`, `I`). Don't disable rules in-line unless you open an issue first.
- **Type hints** are appreciated but not yet enforced. There's no `mypy` in CI.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/). `release-please` derives versions and changelogs from the commit log, so please stick to the format:

```text
feat(backends): add honeycomb as a first-class backend type
fix(hooks): avoid duplicate span on synthesized continuation turn
docs(config): document HERMES_OTEL_SAMPLE_RATE env var
refactor(tracer): extract SpanTracker into its own module
test(integration): cover partial multi-backend failure
chore(ci): cache uv across matrix jobs
```

Common scopes: `tracer`, `hooks`, `backends`, `config`, `helpers`, `tests`, `ci`, `docs`, `deps`.

Breaking changes get an exclamation mark and a `BREAKING CHANGE:` footer:

```text
feat(config)!: rename root_span_ttl_ms to session_ttl_ms

BREAKING CHANGE: root_span_ttl_ms is now session_ttl_ms. Existing configs
will emit a warning and fall back to the default until the rename is applied.
```

## Pull-request checklist

Before opening a PR:

- `uv run --extra dev pytest` passes locally.
- `uv run --extra dev ruff check .` passes.
- `uv run --extra dev black --check .` passes.
- Added / updated tests that prove the behavior change.
- Updated README.md / docs/ if user-visible behavior changed.
- Commit message follows Conventional Commits.
- No secrets in the diff — `config.yaml` is gitignored; use `config.yaml.example` for documentation.

GitHub Actions runs the unit + integration suite + ruff + black on every PR. E2E and smoke tiers are intentionally **not** run in CI (they need Docker / a real Hermes install); maintainers run those locally before releases.

## Adding a new backend

1. Add the resolver in `backends.py`.
2. Add a `docker-compose/<backend>.yaml` file if the backend runs locally.
3. Add a `docs/backends/<name>.md` page — follow the structure of `phoenix.md` / `signoz.md` (setup, multi-backend config, what you'll see, attribute convention, metrics note, troubleshooting).
4. Add an entry under `backends:` in `config.yaml.example`.
5. Add a unit test covering the resolver (env-var precedence, header construction).
6. Integration tests via `InMemorySpanExporter` are encouraged; E2E tests are optional unless the backend has backend-specific attributes.
7. Update the "Supported backends" table in `docs/backends/overview.md` and the README.

## Reporting bugs

Please include:

- Python version + OS.
- `hermes-otel` version (`pip show hermes-otel`) and commit SHA if installed from source.
- Backend(s) configured.
- A minimal repro — either a unit test or a snippet that calls the affected hook directly.
- Output with `HERMES_OTEL_DEBUG=true` if the bug is about missing / wrong spans.

Repo path for issues: [github.com/briancaffey/hermes-otel/issues](https://github.com/briancaffey/hermes-otel/issues).

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0. See `LICENSE`.
