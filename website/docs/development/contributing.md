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

# Unit + integration (no Docker, no network)
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

## Repository layout

Runtime code lives in `hermes_otel/`; everything else is development material.

```text
hermes_otel/        ← the install artifact: modules, plugin.yaml, skill, dashboard
tests/              unit / integration / e2e_ci / e2e / smoke, plus golden/ and helpers/
website/            Docusaurus docs site
docker-compose/     example backend stacks
scripts/            dev + CI utilities
```

`hermes plugins install briancaffey/hermes-otel/hermes_otel` installs **only** `hermes_otel/`. Two consequences worth internalizing:

- **Runtime code belongs in `hermes_otel/`.** A module at the repo root imports fine in the test suite and is missing on a user's machine. `tests/unit/test_install_artifact.py` fails the build if that happens.
- **Hermes ≥ v0.20 security-scans a plugin's file tree before installing it**, and any `critical` finding is a hard block that `--force` cannot override ([#53](https://github.com/briancaffey/hermes-otel/issues/53)). Docs and test fixtures are graded like executable code, which is the other reason they stay out of the artifact. Run the same scanner locally before pushing:

```bash
python scripts/scan_plugin_artifact.py
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

- `make ci` passes locally. It runs what GitHub Actions runs (lockfile check, ruff, black, plugin scan, telemetry vocabulary check, tests with the 85% coverage gate and the per-module floors, dashboard bundle, docs build, wheel). `make ci-fast` skips the two npm builds when you did not touch `dashboard-ui/` or `website/`.
- Added / updated tests that prove the behavior change.
- Updated `README.md` / `website/docs/` if user-visible behavior changed.
- Commit message follows Conventional Commits.
- No secrets in the diff — `config.yaml` is gitignored; use `config.yaml.example` for documentation.

GitHub Actions runs those jobs on every PR, plus the OpenTelemetry-floor job and the credential-free `e2e_ci` tier against a pinned Hermes; [Testing](/development/testing#ci-behavior) lists every job. The Docker `e2e` and `smoke` tiers are **not** run in CI (they need containers or a running Hermes); maintainers run them locally before releases.

## Adding a new backend

The full checklist, including the optional dashboard adapter, is in [CONTRIBUTING.md](https://github.com/briancaffey/hermes-otel/blob/main/CONTRIBUTING.md#adding-a-new-backend). In short:

1. Add a `BackendSpec` in `hermes_otel/backend_specs.py`; a plain OTLP type needs no code beyond it. Add a `_resolve_<type>(bc, spec)` in `hermes_otel/backends.py` only for bespoke URL or header rules.
2. Run `uv run --extra dev python scripts/gen_config_docs.py --write` to regenerate the type list and the env-var reference.
3. Add a unit test covering the resolver (`tests/unit/test_backends_<type>.py`: env-var precedence, header construction) and re-record the resolution snapshot (`UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_golden_resolution.py`).
4. Add a `docker-compose/<type>/` folder (compose file + README, see `docker-compose/README.md`) if the backend runs locally.
5. Add a `website/docs/backends/<type>.md` page (follow the structure of `phoenix.md` / `signoz.md`: setup, multi-backend config, what you'll see, attribute convention, metrics note, troubleshooting) and list it in `website/sidebars.ts`.
6. Add an entry under `backends:` in `config.yaml.example`.
7. Update the backend table in `website/docs/backends/overview.md` and the backend list in `README.md`.

## Reporting bugs

Please include:

- Python version + OS.
- `hermes-otel` version (the `version:` line of `~/.hermes/plugins/hermes_otel/plugin.yaml`) and the commit SHA if installed from source.
- Backend(s) configured.
- A minimal repro — either a unit test or a snippet that calls the affected hook directly.
- Output with `HERMES_OTEL_DEBUG=true` if the bug is about missing / wrong spans.

Repo path for issues: [github.com/briancaffey/hermes-otel/issues](https://github.com/briancaffey/hermes-otel/issues).

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0. See `LICENSE`.
