# hermes-otel — Refactor & Open-Source Readiness Plan

Phased plan to take hermes-otel from "polished internal plugin" to a
credible open-source project. Each phase is **independently shippable**
— do not bundle them into one PR.

Legend: `[ ]` todo · `[x]` done · `[~]` in progress · `[-]` skipped

---

## Phase 0 — OSS essentials (≈1–2 hrs, zero code risk)

Unblocks external contributions. Do these before touching any Python.

- [x] **Add `LICENSE`** (Apache-2.0).
- [x] **Add `CONTRIBUTING.md`**: local setup with `uv`, test tier explanation, PR expectations, Conventional Commits.
- [x] **Fill `pyproject.toml` metadata**: `[project.urls]`, `license = {text = "Apache-2.0"}`, `authors`, `classifiers`, `keywords`.
- [-] **Add `CHANGELOG.md`** — deferred to a later phase (will land with release-please).
- [x] **Fix stale README** (`README.md:498-505`): removed the "SimpleSpanProcessor" limitation bullet.
- [x] **CI on pull requests** (`.github/workflows/test.yml`): now runs on `push: main` and `pull_request`.
- [x] **Lint in CI**: ruff job added. `[tool.ruff]` block in `pyproject.toml` (conservative ruleset: E/F/W/I). Codebase fixed to pass cleanly.
- [x] **Coverage in CI**: `pytest-cov` added; CI runs with `--cov=hermes_otel --cov-fail-under=85` (baseline is 88 %).
- [x] **Cache `uv` in CI** via `astral-sh/setup-uv` `enable-cache: true`.
- [ ] **Optional**: `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1), `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md`.

---

## Phase 1 — Split the README (no code, 1–2 hrs)

Turns docs from a 500-line wall of text into a scannable surface.

- [ ] **Slim `README.md`** to: project summary, 60-second quickstart (one backend, docker-compose one-liner, three env vars), badge row (CI, PyPI, License), links into `docs/`.
- [ ] **`docs/configuration.md`**: `config.yaml` reference, env-var table, precedence rules. Move "Shaping knobs" section here.
- [ ] **`docs/backends/<name>.md`**, one per backend (phoenix, langfuse, signoz, jaeger, tempo, langsmith). Each has: install, env vars, docker-compose snippet, screenshots/links.
- [ ] **`docs/attributes.md`**: dual-convention token table, `hermes.turn.*`, `hermes.tool.*`, `hermes.skill.*`.
- [ ] **`docs/architecture.md`**: span hierarchy diagram, threading model, BatchSpanProcessor fan-out, **parent-stack rationale** (the ContextVar + session-keyed-dict "why" from `tracer.py:96-186` deserves first-class treatment — it's your most interesting engineering).
- [ ] **`docs/development.md`**: test tiers, `docker-compose/all.sh`, smoke-test setup, how to add a backend.
- [ ] **`docs/roadmap.md`**: the backend-support table currently in `README.md`.
- [ ] **Move `docs/PRD_*.md`** → `docs/internal/` (or delete if shipped).
- [ ] **Rename `DESIGN.md`** → `video/BRAND.md` (it's a visual-identity doc for the launch video, not architecture).
- [ ] **Document `scripts/verify_multi_backend.py`** in `docs/development.md`.

---

## Phase 2 — Extract state out of module globals (moderate code risk)

**Goal**: remove `tests/conftest.py:_reset_otel_state`'s need to poke
module internals. When this lands, most of the test-coupling problems in
Phase 5 disappear naturally.

- [x] **New class `SessionState`** (`session_state.py`) wrapping the four former module dicts. `PerSession` holds usage/io/turn_summary; tool timings live in a flat task-scoped registry.
- [x] **Attach `SessionState` to `HermesOTelPlugin`** via `self.sessions = SessionState()` in `__init__`.
- [x] **Rewrite hooks** to go through `tracer.sessions.*` — all four module globals and `_get_or_create_summary` deleted.
- [x] **Rewrite `conftest.py`** — reset is now just `tracer_mod._tracer = None` + `_PARENT_STACK.set(None)`; no more reaching into `hooks_mod`.
- [x] **Collapse `_safe_str` and `clip_preview`** — `_safe_str` moved to `helpers.py` as `truncate_string` with a clear docstring; all call sites updated; test imports updated.

---

## Phase 3 — Simplify tracer init (high-clarity win)

`tracer.py` is 930 lines doing 5 jobs. Split it.

- [x] **Merge the two backend resolvers** — both env (`_init_otlp_from_env`) and yaml (`_resolve_backend_config`) paths now delegate to `backends.resolve(BackendConfig)`. Env path walks a priority list via `backends.resolve_from_env()`. Langfuse basic-auth / SigNoz header / endpoint-default logic lives in one place.
- [-] **Delete `_init_otlp`** — deferred. The shim stays as the test seam (`patch.object(plugin, "_init_otlp")` is used in ~16 tests). Phase 5 will migrate tests to a different seam.
- [x] **Registry dispatch** for backend types — `backends._RESOLVERS: Dict[str, Callable[[BackendConfig], _ResolvedBackend]]`.
- [-] **Remove back-compat aliases** `_span_processor` / `_metric_reader` — deferred to Phase 5 (tests still assert on them).
- [x] **Split `HermesOTelPlugin` into focused modules**:
      - `backends.py` — `_ResolvedBackend`, `_TRACES_ONLY`, per-type resolvers, registry, `resolve` + `resolve_from_env`.
      - `span_tracker.py` — `SpanTracker` + `_PARENT_STACK`.
      - `session_state.py` — already landed in Phase 2 (`SessionState`, `PerSession`, `TurnSummary`).
      - `tracer.py` — shrank from 930 LOC to 619 LOC. Further extraction (orphan sweep → `orphan_sweep.py`, pipeline → `pipeline.py`) deferred; they remain tightly coupled to plugin state and the current split already hits "each file ≤ ~300 LOC" for the new files.
- [x] **Replace `NoopSpan`** — now uses OTel's `INVALID_SPAN` (NonRecordingSpan singleton). Custom class deleted.
- [-] **Drop the `endpoint=` explicit-arg init path** — deferred. One test uses it; removal is low-priority.

---

## Phase 4 — Hook layer cleanup (low risk)

- [x] **Extract `_normalize_usage` / `_usage_attributes` / `_record_usage_metrics`** from `on_post_api_request`. Dual-convention attribute logic is now defined once and reused in both `on_post_api_request` (parses raw hermes usage) and `on_session_end` (emits pre-normalized PerSession.usage).
- [x] **Replace `print("[hermes-otel] ...")`** with `logger = logging.getLogger("hermes_otel")` in `debug_utils.py`. `NullHandler` attached at import; `configure_default_handler()` adds a stderr handler with INFO level when `register()` runs and nothing else has claimed the logger. Every existing startup line still prints; consumers can override by installing their own handler. tracer.py + plugin_config.py + __init__.py all migrated; 5 tests moved from `capsys` to `caplog`.
- [x] **`HookContext` TypedDict** added at the top of `hooks.py` documenting the 8 optional `**kwargs` fields Hermes may pass (session_id + the 7 session-kind classifiers).
- [x] **Audit `debug_utils.mask_secret`** — confirmed unused via grep; removed.

---

## Phase 5 — Test hygiene (can run in parallel with 2–4)

Depends on Phase 2 for the biggest wins.

- [x] **Parameterize the inmemory fixture** — `_build_inmemory_plugin(n_exporters)` helper in `conftest.py` now drives `inmemory_otel_setup`, `inmemory_otel_with_metrics`, and `two_exporter_pipeline` (which moved from `test_multi_backend.py` into `conftest.py`). Metric-reader path now uses `plugin._create_metric_instruments()` so the fixture doesn't hand-roll every counter. `batch_pipeline` in `test_batch_processor.py` stays separate — it uses a custom `_RecordingExporter` for batch-timing assertions that InMemorySpanExporter can't express.
- [x] **Replace `_SESSION_USAGE["s1"]` assertions** — done in Phase 2 (tests now inspect `mock_tracer.sessions.peek("s1").usage`, which is the public SessionState API).
- [x] **Replace `plugin._turn_started_at[...]` writes** — new public `plugin.register_turn(session_id, started_at=None)` seam on `HermesOTelPlugin`. Orphan-sweep tests migrated to it; the `assert "sid" not in plugin._turn_started_at` assertions became `assert plugin.sweep_expired_turns() == []` (testing observable behavior).
- [-] **Replace `len(plugin._span_processors)` assertions** — left as-is. `_span_processors` (plural) is the public shape of a multi-backend plugin; downstream consumers could legitimately introspect it. The `test_span_lands_in_every_exporter` / `test_session_trace_complete_in_both_exporters` tests already verify *behavioral* fan-out via real in-memory exporters.
- [x] **Add a LangSmith integration test** — `tests/integration/test_langsmith_integration.py`. Drives the full hook chain (session_start → llm → api → tool → ... → session_end) through a mocked `urlopen` and asserts on the resulting POST `/runs` / PATCH `/runs/{id}` payloads, including `parent_run_id` chaining and `usage_metadata` on the api span. No extra deps (uses `unittest.mock`, same pattern as existing unit tests).
- [x] **Add a partial-failure test** — `TestPartialBackendFailure` in `test_multi_backend.py`. Configures 3 backends, makes `OTLPSpanExporter` raise for the middle one, asserts the other two still wire up. Plus a test for "only backend fails → init() returns False".
- [-] **Cardinality guard** — out of scope for OSS v1; would require either a sampling-on-write label validator or an upstream OTel view. Documented as a follow-up (see README "Known limitations" once it's added).
- [x] **`pytest-cov` floor in CI** — landed in Phase 0 (`--cov-fail-under=85`).

---

## Phase 6 — v1.0 release

- [ ] Tag `v1.0.0`.
- [ ] Publish to PyPI as `hermes-otel`.
- [ ] GitHub release notes from `CHANGELOG.md`.
- [ ] Announce (short post + launch video + Phoenix/Langfuse screenshots).

---

## Quick wins (if you only have 3 hours)

These alone take the project from "polished internal plugin" to
"credible open-source project" without touching any Python:

1. `LICENSE` + `pyproject.toml` metadata + fix stale README bullet.
2. CI runs on PRs, add ruff.
3. Split README into `README.md` (quickstart) + `docs/configuration.md` + `docs/architecture.md`.

Everything in phases 2–5 is craftsmanship — nicer to contribute to but
no behavior change. Pace those to taste.

---

## Appendix — Non-goals / deliberate omissions

- **gRPC exporter**: HTTP is fine for the target audience. Don't expand scope.
- **Dropping Hermes-specific hooks**: `hermes.*` attributes are value-add, not lock-in.
- **Rewriting LangSmith as OTLP**: LangSmith's API is stable and the current path works. Not worth the churn.
- **Per-type `BackendConfig` subclasses**: frozen dataclass with optionals is fine at this scale; generics / ADTs would be over-engineering.
