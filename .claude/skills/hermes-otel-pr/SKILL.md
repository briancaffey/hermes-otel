---
name: hermes-otel-pr
description: >-
  End-to-end workflow for shipping a feature, bug fix, or new hook in the
  hermes-otel plugin as a green, well-documented pull request. Use whenever you
  are implementing a GitHub issue or any code change destined for a PR in this
  repo — it covers branching, the plugin's hook/span/metric conventions, the
  test fixtures, running the EXACT CI checks locally (ruff + black + coverage
  gate), the docs that count as acceptance criteria, optional before/after
  verification against a real OTLP backend, and opening the PR linked to its
  issue.
---

# Shipping a hermes-otel PR

hermes-otel is an OpenTelemetry plugin for the Hermes Agent. It subscribes to
Hermes lifecycle hooks and exports spans/metrics/logs to OTLP backends
(Phoenix, Langfuse, SigNoz, etc.). This skill is the repeatable recipe for
turning an issue into a merged PR without surprises. Follow it top to bottom;
skip steps only when they genuinely don't apply (and say so).

## 0. Orient

The plugin is a **flat package**: modules live at the repo root, which *is* the
`hermes_otel` package (required by Hermes auto-discovery — don't move them into
a subdir). Key files:

| File | Role |
|---|---|
| `plugin.yaml` | Manifest. `provides_hooks:` lists every hook the plugin subscribes to. |
| `__init__.py` | `register(ctx)` — inits the tracer and registers hook callbacks. |
| `hooks.py` | Hook callbacks (`on_*`). Stateless; routes through the tracer singleton. |
| `tracer.py` | `HermesOTelPlugin` singleton: backend init, `start_span`/`end_span`, `record_metric`, metric instruments, orphan sweep, flush. |
| `span_tracker.py` | Active-span registry + per-session parent stacks (survive cross-thread hook dispatch). |
| `session_state.py` | Per-session aggregators (`PerSession`, `TurnSummary`). |
| `helpers.py` | Pure functions (no OTel import) — unit-testable. |
| `plugin_config.py` | `HermesOtelConfig` frozen dataclass + yaml/env loader. |
| `backends.py` | Resolves config/env into backend endpoints+headers. |

**Hook contract:** the authoritative list of every hook Hermes fires, with
payload fields and which are observer-only vs behavior-changing, is in the
hermes-agent repo at `docs/observability/README.md`. Read the relevant hook's
entry there before implementing. Dispatch sites live under the hermes-agent
package (`grep -rn "invoke_hook(" ` in that repo).

## 1. Branch

Never commit features to `main`. Branch from up-to-date main:

```bash
git checkout main && git pull --ff-only
git checkout -b feat/<short-slug>     # or fix/<slug>, docs/<slug>
```

## 2. Implement — follow the existing conventions

- **New hook?** Add its name to `provides_hooks` in `plugin.yaml`, and register
  it in `__init__.py` inside the forward-compatible `try/except` loop (older
  Hermes builds may not expose newer hooks — registration must degrade
  gracefully, never raise). If a hook only exists on some Hermes versions, gate
  it on `hermes_cli.plugins.VALID_HOOKS` like `mcp_request_headers` does.
- **Handlers** in `hooks.py`: signature uses explicit kwargs you need plus
  `**kwargs` (payloads are additive — always accept unknown fields). First line
  after logging: `tracer = get_tracer(); if not tracer.is_enabled: return`.
  Everything must **fail open** — guard `.get(...)`, never let a telemetry hook
  raise into the agent loop.
- **Spans:** `tracer.start_span(name, key, kind, attributes, session_id, parent=, links=)`
  and `tracer.end_span(key, attributes, status, error_message)`. `kind` is one
  of `_KIND_MAP` (`tool`/`llm`/`general`/`agent`). Pass `session_id` so the
  orphan sweep can finalize the span. Use `parent=`/`links=` only for
  cross-context nesting (e.g. sub-agent rejoin).
- **Attributes:** emit **dual convention** where applicable — OpenInference
  (`llm.*`, `openinference.*`, `input.value`/`output.value`) for Phoenix *and*
  `gen_ai.*` for Langfuse/OTel GenAI. Clip strings with `truncate_string` /
  `clip_preview`; gate previews through `_preview` (respects `capture_previews`
  and the per-category `*_preview_max_chars` config).
- **Metrics:** add the instrument in `tracer._create_metric_instruments`, a
  branch in `tracer.record_metric`, then call `tracer.record_metric(name, value, attrs)`.
  Keep label cardinality low (model, provider, role, status — never raw ids).
- **Pure logic → `helpers.py`** so it can be unit-tested without importing OTel.
- **Status policy:** real failures map to `ERROR`; deliberately-benign outcomes
  (tool timeout/blocked, unknown/missing status) stay `OK` so they don't inflate
  error rates. Match the surrounding code's existing choice.

## 3. Tests — required for every behavior change

Tiers: `tests/unit/` (pure + single-hook), `tests/integration/` (full export
pipeline), `tests/smoke/` and `tests/e2e/` (live backends). Most work lands in
unit + integration.

Fixtures (in `tests/conftest.py`):
- `inmemory_otel_setup` → `(exporter, plugin)`; assert on `exporter.get_finished_spans()`.
- `two_exporter_pipeline` → `(exporter_a, exporter_b, plugin)`; verify fan-out.
- `inmemory_otel_with_metrics` → `(exporter, metric_reader, plugin)`; read
  `metric_reader.get_metrics_data()`.

Drive tests by importing the hook functions from `hermes_otel.hooks` and calling
them with kwargs, exactly as Hermes would. Cover at least: fail-open (tracer
disabled, missing ids), `**kwargs` forward-compat, span parent/child shape,
status mapping, metrics, two-exporter fan-out, and orphan-sweep where relevant.

**Determinism (don't get burned by CI):**
- The orphan sweep compares `time.perf_counter()` values whose epoch is
  arbitrary. Never use `started_at=0.0`; set a small `root_span_ttl_ms` via
  `plugin.config = HermesOtelConfig(root_span_ttl_ms=1_000)` and back-date with
  `plugin.register_turn(sid, started_at=time.perf_counter() - 10.0)`.
- No wall-clock / random assumptions. Tests must pass on a freshly booted runner.

## 4. Run the EXACT CI checks locally — before every push

CI (`.github/workflows/test.yml`) runs three things. Run all three; do not push
until they pass. The canonical invocation uses `uv` (matches CI):

```bash
uv run --extra dev ruff check .
uv run --extra dev black --check .          # ← easy to forget; CI fails without it
uv run --extra dev pytest --cov=hermes_otel --cov-report=term --cov-fail-under=85
```

- If `black --check` complains, fix with `uv run --extra dev black .`.
- The coverage gate is **85%** — new code needs tests or coverage drops below it.
- Live-backend export errors during tests (connection-refused to a collector)
  are harmless noise from a backend pointed at a down endpoint; filter them when
  reading output (`grep -vE "Transient error|Failed to export|Max retries|Connection refused"`).
  They are NOT failures — look for the `passed`/`failed` summary line.

## 5. Docs are acceptance criteria — update in the same PR

The docs site is Docusaurus under `website/`. Touch the ones your change affects:

- `website/docs/reference/hooks.md` — add/adjust the hook entry + the hook→span map.
- `website/docs/reference/span-attributes.md` — every new attribute, with convention.
- `website/docs/architecture/span-hierarchy.md` — if the span tree/shape changes.
- `website/docs/reference/limitations.md` — add/remove caveats as they change.
- `README.md` — keep the span-hierarchy/features section in sync.
- New config knob → document it and, if it's a new page, add it to
  `website/sidebars.ts`.

Then build the site and confirm it's clean (exit 0, no broken links):

```bash
cd website && npm run build
```

Internal links are root-relative (`/reference/hooks`) with anchors (`#id`, or an
explicit `{#anchor}` on a heading).

## 6. (Recommended) Before/after verification against a real backend

For observability changes, prove the gap existed and your change closes it.

- The live plugin Hermes loads is the dir under `$HERMES_HOME/plugins/hermes_otel`
  (default `~/.hermes/plugins/hermes_otel`). When that dir is a git checkout,
  **switching branches swaps the live plugin** — handy for before/after.
- `config.yaml` is gitignored and persists across branch switches. Set a unique
  `project_name:` per phase to isolate runs into separate backend projects; back
  it up first and restore it after.
- Run Hermes non-interactively: `hermes -z "PROMPT"` (oneshot; auto-approves,
  prints only the final answer). Craft a prompt that reliably triggers your hook.
  Reasoning models are slow — run it in the background and poll.
- Capture a **baseline on `main`** (old code), then the **after** on your branch.
- Query the backend to compare. For Phoenix, GraphQL at `<endpoint>/graphql`:
  ```graphql
  { projects(first:50){ edges { node { name
    spans(first:500){ edges { node { name spanKind parentId spanId
      trace { traceId } attributes } } } } } } }
  ```
  Group spans by `trace.traceId`; inspect parent/child via `parentId`→`spanId`
  and check your new attributes. Restore `config.yaml` and `git checkout` back to
  your branch when done.

## 7. Commit, push, PR

- **Conventional commits** (`feat:`, `fix:`, `docs:`, `test:`, `chore:`) —
  release-please reads them to cut versions. `feat:` → minor bump.
- End each commit body with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Apostrophes in `-m "$(cat <<'EOF'...)"` heredocs break the shell. Write the
  message to a file and use `git commit -F <file>`.
- If `main` advanced, `git merge origin/main` and resolve. Additive hooks
  usually merge by keeping both blocks — re-check any renamed counters/vars.
- **PR body MUST start with `Closes #<issue>`** so the issue auto-closes.
  Include a short summary, the test/coverage result, and any before/after
  evidence.
- `gh pr edit` can fail on this repo (projects-classic GraphQL deprecation).
  Set the title/body via REST instead:
  ```bash
  gh api -X PATCH repos/<owner>/<repo>/pulls/<n> -f title="..." -F body=@body.md
  ```
- After pushing, confirm CI is green: `gh pr checks <n>`. Fix and repeat until
  lint + test (all Python versions) pass. Then hand off for review/merge.

## Definition of done (checklist)

- [ ] Branch off main; conventional-commit history.
- [ ] Hook registered forward-compatibly + listed in `plugin.yaml` (if a hook).
- [ ] Handler fails open, accepts `**kwargs`.
- [ ] Spans/attrs follow dual-convention; metrics low-cardinality.
- [ ] Unit + integration tests added; deterministic.
- [ ] `ruff`, `black --check`, and `pytest --cov-fail-under=85` all pass locally.
- [ ] Docs updated (hooks, span-attributes, hierarchy/limitations as needed) +
      README; `npm run build` clean.
- [ ] (If observability) before/after verified against a real backend.
- [ ] PR opened with `Closes #<issue>`; CI green.
