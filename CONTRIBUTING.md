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

## Workflow

The full issue-to-merged-PR recipe (branching, hook/span/metric conventions,
running the exact CI checks locally, docs as acceptance criteria, and
before/after backend verification) lives in
[`.claude/skills/hermes-otel-pr/SKILL.md`](.claude/skills/hermes-otel-pr/SKILL.md)
and is summarized in the docs under
[Development → PR workflow](https://briancaffey.github.io/hermes-otel/development/pr-workflow).
If you use Claude Code, that skill loads automatically in this repo, so the
agent follows the same workflow you do.

## Reporting a security problem

Privately, through GitHub's vulnerability reporting; see [SECURITY.md](SECURITY.md).
Not as a public issue.

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
uv run --extra dev ruff check .

# Format (check only)
uv run --extra dev black --check .

# Format (apply)
uv run --extra dev black .

# Coverage
uv run --extra dev pytest --cov=hermes_otel --cov-report=term-missing
```

If you want to actually run the plugin inside Hermes (rare for
contributors), install it in editable mode into the hermes-agent venv and
point the plugin directory at your working copy:

```bash
~/git/hermes-agent/venv/bin/pip install -e .
ln -s "$PWD/hermes_otel" ~/.hermes/plugins/hermes_otel
```

## Repository layout

Runtime code lives in `hermes_otel/`; everything else is development material.

```text
hermes_otel/        ← the install artifact: modules, plugin.yaml, skill, dashboard
tests/              unit / integration / e2e / smoke
website/            Docusaurus docs site
docker-compose/     example backend stacks
scripts/            dev + CI utilities
```

`hermes plugins install briancaffey/hermes-otel/hermes_otel` installs **only**
`hermes_otel/`. Two consequences worth internalizing:

- **Runtime code belongs in `hermes_otel/`.** A module at the repo root imports
  fine in the test suite and is missing on a user's machine.
  `tests/unit/test_install_artifact.py` fails the build if that happens.
- **Hermes ≥ v0.20 security-scans a plugin's file tree before installing it**,
  and any `critical` finding is a hard block that `--force` cannot override
  ([#53](https://github.com/briancaffey/hermes-otel/issues/53)). Docs and test
  fixtures are graded like executable code, which is the other reason they stay
  out of the artifact. Run the same scanner locally before pushing:

```bash
python scripts/scan_plugin_artifact.py
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

## Code style

- **Formatting** is handled by [black](https://black.readthedocs.io)
  with `line-length = 100`. Run `uv run --extra dev black .` before
  committing (or configure your editor to format on save). CI fails on
  unformatted diffs.
- **Linting** is handled by [ruff](https://docs.astral.sh/ruff/). The
  ruleset is intentionally conservative (`E`, `F`, `W`, `I`); tighten
  as the codebase matures.
- Both configs live in `pyproject.toml` under `[tool.black]` and
  `[tool.ruff]`. Don't disable rules in-line unless you have a concrete
  reason — open an issue first if a rule fights the codebase.

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

- [ ] `make ci` passes locally. It runs what GitHub Actions runs, job for
      job (lockfile check, ruff, black, plugin scan, tests with the 85%
      coverage gate and the per-module floors in `coverage-floors.json`,
      dashboard bundle up to date + vitest, docs build, wheel build), so a
      green `make ci` is a green pipeline, with three exceptions it names in
      its header: the Python version matrix, `make ci-floor` (the suite on
      the OpenTelemetry 1.35 floor) and the catalog-admission workflow.
      A module may not drop more than one point under its floor; after a
      change that deliberately adds or removes coverage, re-record with
      `python scripts/coverage_floor.py --update` and commit the file; a new
      module gets its floor from `--add-missing`, which leaves the others alone.
      The pull request template carries this list. `make ci-fast` skips the
      two npm builds when you did not touch `dashboard-ui/` or `website/`.
- [ ] If `black --check` complains, apply with `uv run --extra dev black .`.
- [ ] Added / updated tests that prove the behavior change.
- [ ] Updated `README.md` or `docs/` if user-visible behavior changed.
- [ ] Commit message follows Conventional Commits.
- [ ] No secrets in the diff — `config.yaml` is gitignored; use
      `config.yaml.example` for documentation.

GitHub Actions runs the same jobs on every PR and on main. E2E and smoke
tiers are intentionally not run in CI (they need Docker / a real Hermes
install); maintainers run those locally before releases.

Release PRs are opened by release-please. Its version bump also updates
`uv.lock` (an `extra-files` entry in `release-please-config.json`), so the
release branch passes the lockfile check on its first run; a superseded run
on any branch is cancelled rather than left red.

## Test scaffolding

`tests/helpers/` holds what more than one test file needs (#406); nothing in
it is a test. Import builders where used; the fixtures are registered by the
tier `conftest.py` files.

| Module | What it gives |
|---|---|
| `helpers/dashboard.py` | `isolated_backends` (autouse for every `test_dashboard_*` / `test_openobserve_*` module: no developer config, caches and probe results cleared), `FakeHttp` / `fake_http(monkeypatch, routes)` (a route table replacing the adapters' HTTP functions, calls recorded), `adapter(type, **cfg)`, the contract app's `client` fixture, `down`, `row`, `http_error` |
| `helpers/live.py` | `span(...)` / `metric(...)` / `log(...)` rows in the live store's shape, the `store` fixture, the shared `NOW` |
| `helpers/turns.py` | `run_turn(session_id, ...)`, `api_call(...)`, `tool_call(...)` and the payload builders: one fake Hermes turn, every documented field, overridable |
| `helpers/tracer.py` | `mock_tracer` / `disabled_tracer` for single-hook unit tests |
| `tests/_helpers.py` | span/metric lookups and `clear_backend_env` (older, kept) |

Import the dashboard package as `hermes_otel.dashboard.backends`, never by
putting `hermes_otel/dashboard` on `sys.path`: the latter creates a second copy
of every adapter module, and a monkeypatch on one copy does not reach the other.
The contract test (`test_dashboard_adapter_contract.py`) parametrizes its
invariants over `backends.adapters()`, so a new adapter is covered by it the
moment it registers; its own response parsing goes in `test_dashboard_<type>.py`.

### Golden fixtures

`tests/golden/hooks/<scenario>__<config>.json` records the complete telemetry
(spans, metric points, events) of twelve fixed hook sequences under four
configs (`full`, `preview`, `off`, `events`). `tests/unit/test_golden_hooks.py`
re-runs every one and fails on any difference, with a unified diff naming the
attribute. Ids, timestamps, durations and the process identity are normalised
(`T1`/`S1`, `<num>`, `<service.instance.id>`); everything else is literal.

- Record or refresh: `UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_golden_hooks.py`.
- Add a scenario in `tests/golden/record_hooks.py` (`SCENARIOS`), record, commit the files.
- **A refactor PR may not change a golden file.** If a change is deliberate (a bug
  found on the way), re-record and list every changed line in the PR description.

### Resolution snapshot (#409)

`tests/unit/test_golden_resolution.py` records, for every backend type and
every adapter alias under six input paths (inline credentials, `*_env`
credentials, env-only selection, signal overrides, temporality + root_usage
overrides, a per-backend `logs:` block), what the exporter resolves, which log
exporter it gets, its Settings card, its MCP query target, its `status` line
and its dashboard adapter, into `tests/golden/resolution/<type>__<path>.json`,
plus the whole settings report for `config.yaml.example`. Secrets are
placeholders and masked. `UPDATE_GOLDEN=1 pytest tests/unit/test_golden_resolution.py`
re-records; a refactor PR that changes a fixture lists every changed line. The
same module holds the cross-registry consistency checks (exporter types vs
adapter `handles`, `signal_support()` vs adapter capabilities, env vars read vs
`KNOWN_ENV_VARS`), marked `xfail(strict=True)` while #402 is open.

### Adapter goldens

`tests/golden/adapters/<type>/` holds, per dashboard adapter, the backend
entry it is built from (`config.json`), a scripted server (`routes.json`,
answered at the `urlopen` boundary by `helpers.wire.FakeWire`, so the raw SQL
and LogsQL transports are covered too) and the recorded answers
(`expected/<scenario>.json`: what the adapter returned *and* the requests it
made). `tests/unit/test_dashboard_goldens.py` runs one fixed scenario list
(status, the search variants, the detail, metrics and logs where declared,
the error taxonomy) over `backends.adapters()`; a new adapter fails until its
fixtures exist, and a change in an answer or a request fails with a diff.

- Record or refresh: `UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_dashboard_goldens.py`.
- The canned bodies come from `seed_responses.py` (the shapes the adapter
  tests recorded against real backends, re-based onto one shared turn). To
  capture a real backend instead, run the dashboard with
  `HERMES_OTEL_RECORD_HTTP=<dir>` and read `<dir>/exchanges.jsonl`
  (credentials are masked; ClickHouse and VictoriaLogs post through their
  own transports and are not captured).
- A refactor PR never changes an `expected/` file silently: when it must,
  the PR description lists every changed fixture and why.

### The test seam (`hermes_otel.testing`)

Tests never reach into the package's private names. `hermes_otel.testing`
ships with the plugin and is the supported surface: `reset_state()` forgets
the singleton, the parent-stack ContextVar, the live-store singleton and the
redaction cache (the root `conftest.py` calls it around every test);
`install_singleton(plugin)` makes the hooks use a plugin and returns the
previous one; `build_inmemory_plugin(exporters=, metrics=, logs=)` wires a
plugin to in-memory sinks on providers of its own. Pure helpers a test needs
are public in their home module (`hooks.usage.normalize_usage`,
`plugin_config.coerce_from_yaml`, ...); the underscored spellings are aliases
kept for one release. `hooks/__init__.py` exports only the `on_*` callbacks,
`get_tracer` and `get_current_traceparent`.

### The telemetry vocabulary (`hermes_otel/semconv.py`)

Every attribute name the plugin emits, the fallback chains readers use
(`SESSION_KEYS`, `MODEL_KEYS`, `TOTAL_TOKEN_KEYS`, ...), the span-kind table
and the event names live in `hermes_otel/semconv.py` (#404). The live store,
the query CLI, the OpenObserve column table and `dashboard-ui/src/lib.ts`
read it; `scripts/gen_semconv.py` writes `dashboard-ui/src/semconv.generated.ts`
and the tables of `website/docs/reference/span-attributes.md` (from the
registry's `ROWS`, between marker comments; the page's prose is hand-written),
and `--check` (run by `make ci-semconv` and CI) fails when either is stale. To
add an attribute: emit it in a hook, add it to a `Row` in the registry, run the
generator, commit the regenerated TypeScript and page, rebuild the dashboard
bundle. `tests/unit/test_semconv.py` fails on a hook literal the registry does
not know.

## Live before/after check (`make refactor-check`)

Golden fixtures prove the Python output is unchanged; `make refactor-check`
proves a real Hermes turn still lands in a real backend. It installs the
base ref's `hermes_otel/` into the profile's plugin directory, runs the
scenario harness's cli turns, scores every source with `scripts/scenarios/check.py`, dumps the
live store, then does the same with the working tree and diffs the *shape*
of the telemetry (span names, parentage, status, attribute keys, the
run-stable values) plus the check matrices. Model output, durations, tokens
and cost are never compared; `scripts/refactor_check/allowlist.yaml` lists
the keys that legitimately come and go, each with a reason. One difference
is recognised rather than allowlisted: the auxiliary API call at the end of
a cli turn races `on_session_end` (#552), so when a prompt's only difference
is that auxiliary `api.*` span swept on one side and answered on the other
(plus the root's `hermes.turn.auxiliary.*` and `hermes.span.swept.count`),
the verdict reports an "auxiliary race" line and the run does not fail on
it. The report lands
under `scripts/refactor_check/out/<branch>-vs-<base>-<stamp>/report.md`; the
profile's plugin directory is restored afterwards.

Required for any PR in the refactor milestone (#414) that touches `hooks/`,
`tracer.py`, `live_store.py`, `log_handler.py`, `dashboard/backends/` or
`backends.py`; attach the report to the PR. `BASE=origin/main` and
`PROFILE=<name>` override the defaults; `--base-run <side.json>` reuses an
earlier base run.

## Adding a new backend

A backend has two halves: the **exporter** side (where the plugin sends
OTLP) and, optionally, the **dashboard** side (an adapter that reads
traces, metrics and logs back for the Hermes dashboard tab). Most of the
per-type facts are declared once and derived everywhere else.

**Exporter side**

1. Add one `BackendSpec` to `SPECS` in `hermes_otel/backend_specs.py`:
   display name, the OTLP signals it accepts, temporality and root-usage
   presets, how env-var mode selects it, its credential fields and the env
   vars each falls back to, its endpoint variable and default host, and the
   one auth header it sends (`auth=AuthHeader(...)`). The exporter, the
   Settings tab, the MCP config and the dashboard registry all read their
   tables from here. A plain OTLP/HTTP type (an endpoint, at most one auth
   header, `/v1/traces` normalisation) is complete at this step:
   `backends._resolve_plain` serves it from the spec.
2. Only when the type has bespoke URL or header rules (a vendor ingest path,
   per-signal header sets, Resource attributes): add
   `_resolve_<type>(bc, spec)` in `hermes_otel/backends.py` and register it
   in `_RESOLVERS`. It returns a `_Wire` (endpoint and headers); `resolve()`
   adds everything the spec knows. Use `_endpoint(bc, spec)` and
   `_secret(bc, spec, "<field>")`, so env fallbacks come from the spec and
   precedence matches every other type.
3. Run `uv run --extra dev python scripts/gen_config_docs.py --write` to
   regenerate the type list and the env-var reference.
4. Tests: `tests/unit/test_backends_<type>.py` for env-var precedence and
   header construction. Re-record the resolution snapshot with
   `UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_golden_resolution.py`
   and check the diff is only your new type. `tests/unit/test_backend_specs.py`
   fails if the spec, the resolver and the registry disagree.

**Dashboard side (optional)**

5. Add `hermes_otel/dashboard/backends/<type>.py`: subclass `BackendAdapter`
   from `base.py`, decorate it with `@register` (from the package
   `__init__`), and name the module in the spec's `adapter_module`.
   Build on the shared pieces instead of re-deriving them:
   - `shapes`: card rows, OTLP spans and batches.
   - `base`: the search template (`root_of`, `span_matches`),
     `query_origin`, `basic_auth`/`bearer`/`require_credential`, the HTTP
     core and `register_cache` for any probe cache.
   - `paging`: trace and log pages, cursor bounds.
   - `metrics_math` and `timeconv`, and the package's `hermes_otel/severity.py`
     for level spellings.
   Declare `filter_support` honestly: `server`, `client` or `none` per key.
6. Tests: `tests/unit/test_dashboard_<type>.py`, plus adapter goldens.
   Add `tests/golden/adapters/<type>/config.json` and `routes.json`, then
   `UPDATE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_dashboard_goldens.py`
   (see [Adapter goldens](#adapter-goldens)). The parametrized contract test
   picks the adapter up automatically.
7. Verify it live with `scripts/dashboard_verify` against the local stack.

**Around it**

8. If the backend runs locally, add `docker-compose/<type>/docker-compose.yaml`
   and a `README.md` (see `docker-compose/README.md` for the folder
   convention), and note anything surprising in `docker-compose/QUIRKS.md`.
9. Add `website/docs/backends/<type>.md` (required env vars, a compose
   snippet, a link to the project's docs) and list it in `website/sidebars.ts`.
10. Add a commented entry under `backends:` in `config.yaml.example`, and a
    link in the "Backends" list of `README.md`.

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
