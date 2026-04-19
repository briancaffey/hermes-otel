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

- [ ] **Extract `build_usage_attributes(usage: dict) -> dict`** from `on_post_api_request` (`hooks.py:636-660`). The same dual-convention logic is duplicated in `on_session_end` (`hooks.py:302-314`). One function, two callers.
- [ ] **Extract `record_usage_metrics(tracer, usage, metric_attrs)`** from `on_post_api_request` (`hooks.py:681-701`).
- [ ] **Replace `print("[hermes-otel] ...")`** across `tracer.py` with `logging.getLogger("hermes_otel")` and a default `NullHandler`. Consumers control verbosity. Keep one startup banner for UX.
- [ ] **`HookContext` TypedDict** documenting what's actually in `**kwargs` for each hook. New contributors shouldn't have to read Hermes internals to know what they can `.get()`.
- [ ] **Audit `debug_utils.mask_secret`** — looks unused. `Grep` to confirm, then remove (or wire it into the LangSmith backend's startup log).

---

## Phase 5 — Test hygiene (can run in parallel with 2–4)

Depends on Phase 2 for the biggest wins.

- [ ] **Parameterize the inmemory fixture** so the duplicated wiring in `tests/integration/test_batch_processor.py:batch_pipeline` (~line 140) and `tests/integration/test_multi_backend.py:two_exporter_pipeline` (~line 26) collapses into one fixture in `conftest.py`.
- [ ] **Replace `_SESSION_USAGE["s1"]`-style assertions** in `tests/unit/test_hooks_callbacks.py` and `tests/integration/test_session_lifecycle.py:148-149` with assertions on **exported span attributes** (via `InMemorySpanExporter`). Testing behavior, not storage.
- [ ] **Replace `plugin._turn_started_at[session_id] = ...`** in `tests/integration/test_orphan_sweep.py:63, 120, 126` with a time-monkeypatch or a public `_register_turn_at(session_id, started_at)` helper.
- [ ] **Replace `len(plugin._span_processors)` assertions** (`tests/integration/test_multi_backend.py:56-57, 136-137`) with assertions that each backend's exporter actually receives spans.
- [ ] **Add a LangSmith integration test** using `pytest-httpserver` — no Docker needed, just fixtures asserting the right POST/PATCH payloads land. Parallel to `test_phoenix_traces.py`.
- [ ] **Add a partial-failure test**: configure 3 backends, make one throw in exporter construction (monkeypatch `OTLPSpanExporter.__init__`), assert the other two still export. Guards against silent degradation.
- [ ] **Add a cardinality-guard test** for metric attributes (tool-name label shouldn't accept unbounded values) — or decide this is out of scope and document it.
- [ ] **Add `pytest-cov` + floor** in CI (Phase 0 item, listed again here for completeness).

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
