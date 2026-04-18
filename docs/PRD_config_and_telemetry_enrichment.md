# PRD: Configurability, resilience, and richer telemetry

## Context

`hermes-otel` is currently env-var-only, OpenInference/GenAI-attributed, with a clean `ContextVar`-based parent stack and four working backends. Two gaps surfaced in a comparison with `hermes-otel-plugin` (a parallel implementation with different priorities):

1. **Configuration is shallow.** Every option is an env var; there's no way to set resource attributes, global tags, sampling, headers, or TTLs from a config file. Operators running a shared collector need declarative config.
2. **Telemetry is per-event, not per-turn.** Each span carries its own attributes but the root span doesn't summarize what happened in the turn (which tools ran, which targets, what statuses). Dashboards have to JOIN across spans to answer "how many turns called bash?".
3. **Missing resilience knobs.** No sampling, no orphan-span sweep, no preview sanitization beyond truncation. A long-lived agent process that drops `on_session_end` for any reason will silently leak active-span state.

`hermes-otel-plugin` makes opposite trade-offs (config-rich, summary-heavy, custom attribute schema) and the parts that aren't tied to its custom schema are worth absorbing.

This PRD covers what to bring over while preserving what makes `hermes-otel` good — backend portability, ContextVar concurrency, OpenInference/GenAI compatibility, the four-tier test pyramid.

## Goals

- **G1**: Operators can configure resource attrs, sampling, TTLs, and headers without setting env vars.
- **G2**: Root session/agent span carries a per-turn summary (tools used, skill names if inferable, outcome counts) without changing the span hierarchy.
- **G3**: Orphaned spans are reaped after a configurable TTL.
- **G4**: Tool spans carry richer identity (target file, command, outcome status) and a richer outcome taxonomy.
- **G5**: All previews are ANSI-stripped and whitespace-normalized before export.
- **G6**: Existing backends (Phoenix, Langfuse, LangSmith, SigNoz) keep working unchanged. No attribute renames; everything is additive.

## Non-goals

- Replacing OpenInference/GenAI conventions with a custom Hermes schema.
- Dropping any currently-supported backend.
- Skill **span synthesis** (theirs creates `skill:<name>` spans by inference). This PRD only attaches inferred *attributes* to existing tool spans — the skill graph is too speculative to fabricate spans for. Revisit when Hermes exposes a real skill activation hook.
- Replacing the parent-stack model with a per-turn graph.
- Single global lock around all state (we keep ContextVar).

## Design

### Part 1 — `config.yaml` with env-var override

Add `plugin_config.py` exposing a frozen dataclass:

```python
@dataclass(frozen=True)
class HermesOtelConfig:
    enabled: bool = True
    sample_rate: Optional[float] = None       # ParentBased(TraceIdRatioBased)
    root_span_ttl_ms: int = 600_000           # 10 min
    flush_interval_ms: int = 60_000           # metrics
    preview_max_chars: int = 1200             # for clip_preview
    capture_previews: bool = True             # global kill switch
    headers: Optional[Dict[str, str]] = None  # extra OTLP headers
    global_tags: Optional[Dict[str, Scalar]] = None
    resource_attributes: Optional[Dict[str, Scalar]] = None
    project_name: Optional[str] = None        # supersedes OTEL_PROJECT_NAME
```

Loader precedence: **env var > config.yaml > default**. Source the file from `~/.hermes/plugins/hermes_otel/config.yaml`. Tolerate missing `pyyaml` (skip the file silently); tolerate malformed YAML by **logging a warning** and falling back to defaults — explicitly *not* the silent `return {}` we'd be copying from `hermes-otel-plugin`.

Backend selection (Phoenix vs Langfuse vs LangSmith vs SigNoz) stays env-var-driven — those env vars are already documented and switching them to YAML breaks every existing user. Config file only controls **shaping** (sampling, previews, resource attrs, TTL, headers).

### Part 2 — Per-turn summary attributes on the session/agent span

Today the session span carries only `session.id`, `llm.model_name`, completion flag, and aggregated token counts. Add:

| Attribute | Type | Source |
|---|---|---|
| `hermes.turn.tool_count` | int | distinct tool names invoked in turn |
| `hermes.turn.tools` | string (CSV) | sorted distinct tool names |
| `hermes.turn.tool_targets` | string (`\|`-joined) | distinct file paths / URLs the tool acted on |
| `hermes.turn.tool_commands` | string (`\|`-joined) | distinct shell commands invoked |
| `hermes.turn.tool_outcomes` | string (CSV) | distinct outcome statuses observed |
| `hermes.turn.skill_count` | int | distinct skill names inferred |
| `hermes.turn.skills` | string (CSV) | sorted distinct skill names |
| `hermes.turn.api_call_count` | int | number of `pre_api_request`s in turn |
| `hermes.turn.final_status` | string | `completed` / `interrupted` / `incomplete` / `timed_out` |

Implementation: extend `_SESSION_USAGE` with a parallel `_SESSION_TURN_SUMMARY: dict[str, TurnSummary]` aggregator. Update on every `pre_tool_call`, `post_tool_call`, `pre_api_request`. Flush onto the session span in `on_session_end` (same place token totals flush today). Apply previously to the parent LLM span if the session hook isn't available.

Use the `hermes.turn.*` namespace so it doesn't collide with OpenInference (`tool.*`, `llm.*`) or GenAI (`gen_ai.*`).

### Part 3 — Orphan turn sweep (`root_span_ttl_ms`)

Add a `TurnRegistry` keyed by `session_id` recording `started_at` (perf_counter). On every `pre_*` hook, call `_sweep_expired()`:

```python
def _sweep_expired(self):
    now = time.perf_counter()
    threshold = self.config.root_span_ttl_ms / 1000.0
    expired = [
        sid for sid, started_at in self._turn_started_at.items()
        if now - started_at > threshold
    ]
    for sid in expired:
        self._finalize_orphan(sid, final_status="timed_out")
```

`_finalize_orphan` ends any spans still in `_active_spans` for that session, sets `hermes.turn.final_status="timed_out"`, sets status to `OK` (not ERROR — see "what NOT to copy" above; timeouts shouldn't pollute error rates).

### Part 4 — Richer tool identity & outcome

Port `resolve_tool_identity` and `extract_tool_result_status` from `hermes-otel-plugin/plugin_attrs.py`, but emit attributes under both conventions:

```python
# in on_pre_tool_call
target, command = resolve_tool_identity(args)
attributes["tool.parameters"] = json.dumps(args)[:500]      # OpenInference (existing)
attributes["hermes.tool.target"] = target
attributes["hermes.tool.command"] = command

# in on_post_tool_call
outcome = extract_tool_result_status(parsed_result) or "completed"
attributes["hermes.tool.outcome"] = outcome
status = "error" if outcome == "error" else "ok"
```

Outcome taxonomy: `completed` · `error` · `timeout` · `blocked` · plus whatever explicit `status` field comes back. Don't force into ok/error at the span level — let the consumer decide.

### Part 5 — Skill name inference (attribute-only)

Port `infer_skill_name` and `infer_skill_name_from_text`. **Do not** create skill spans. Instead, attach `hermes.skill.name` to the tool span when inference succeeds. Bump a counter:

```python
self._skill_inference_counter.add(1, {"skill_name": skill, "source": "path_match"})
```

Document the inference rules and limits in README (this is exactly what `hermes-otel-plugin` does badly — silently inferring without telling users when it does or doesn't fire).

### Part 6 — `clip_preview` with ANSI stripping

Replace `_safe_str` with `clip_preview` from `plugin_attrs.py`:

```python
_ANSI_RE = re.compile(r"\u001B(?:\][^\u0007]*(?:\u0007|\u001B\\)|\[[0-?]*[ -/]*[@-~]|[@-_])")

def clip_preview(text: Optional[str], max_chars: int) -> Optional[str]:
    if not text:
        return None
    text = _ANSI_RE.sub("", text).replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars - 3]}..."
```

Honor `config.capture_previews=false` to skip preview emission entirely (privacy mode for shared deployments).

### Part 7 — Configurable sampling

In `_init_otlp`, when `config.sample_rate is not None`:

```python
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
provider = TracerProvider(
    resource=resource,
    sampler=ParentBased(TraceIdRatioBased(config.sample_rate)),
)
```

Default unchanged (sample everything). Document that `sample_rate=0` effectively disables tracing while leaving metrics intact.

### Part 8 — Resource attributes from config

Today `_init_otlp` builds:

```python
resource_attrs = {"service.name": "hermes-agent"}
project_name = os.getenv("OTEL_PROJECT_NAME", "").strip()
if project_name:
    resource_attrs["openinference.project.name"] = project_name
```

After:

```python
resource_attrs = {
    "service.name": "hermes-agent",
    **(config.global_tags or {}),
    **(config.resource_attributes or {}),
}
project = config.project_name or os.getenv("OTEL_PROJECT_NAME", "").strip()
if project:
    resource_attrs["openinference.project.name"] = project
```

User-provided keys win. `service.name` stays defaulted; users can override via `resource_attributes.service.name`.

## Implementation plan

### Phase 1 — config + clip_preview (foundation)
Add `plugin_config.py`, `_load_config()`, `clip_preview()`, `capture_previews` switch. Plumb config through `HermesOTelPlugin.__init__`. Replace `_safe_str` callsites with `clip_preview`. Update tests.
**Files:** `tracer.py`, `hooks.py`, `plugin_config.py` (new), `tests/unit/test_config.py` (new), `tests/unit/test_hooks_helpers.py`.

### Phase 2 — orphan sweep + sampling + resource attrs
Add `TurnRegistry`, sweep on `pre_*` hooks, `_finalize_orphan`. Wire `sample_rate` and `resource_attributes` into `_init_otlp`.
**Files:** `tracer.py`, `hooks.py`, `tests/integration/test_orphan_sweep.py` (new), `tests/unit/test_tracer_init.py`.

### Phase 3 — per-turn summary attributes
`TurnSummary` dataclass on `_SESSION_TURN_SUMMARY`. Update `on_pre_tool_call` / `on_post_tool_call` / `on_pre_api_request` to record. Flush in `on_session_end` and (fallback) `on_post_llm_call` when no session hook is available.
**Files:** `hooks.py`, `tests/integration/test_turn_summary.py` (new).

### Phase 4 — tool identity, outcome, skill attribute inference
Port `resolve_tool_identity`, `extract_tool_result_status`, `infer_skill_name` from `plugin_attrs.py` into a new `helpers.py` module. Attach attributes in `on_pre_tool_call` / `on_post_tool_call`. Add `hermes.skill.inferred` counter.
**Files:** `helpers.py` (new), `hooks.py`, `tracer.py`, `tests/unit/test_helpers.py` (new), `tests/integration/test_tool_attributes.py` (new).

### Phase 5 — docs + smoke-test verification
Update README: new config section, list of `hermes.turn.*` attributes, skill inference rules, privacy-mode note. Re-run E2E + smoke tests against Phoenix and Langfuse to confirm no regression in existing dashboards.

## Acceptance criteria

Each criterion must be met and covered by a test. This is the definition of "done".

### AC1 — Config loader (`plugin_config.py`)
- `HermesOtelConfig` is a `@dataclass(frozen=True)` with exactly the fields listed in the Design section, and the defaults shown there.
- `load_config()` returns defaults when no file and no env vars are present.
- Precedence is **env var > `config.yaml` > default**, per-field (not whole-object).
- `config.yaml` at `~/.hermes/plugins/hermes_otel/config.yaml` is loaded when `pyyaml` is importable.
- Missing `pyyaml` does NOT raise — file-based config is skipped silently and defaults/env still apply.
- Malformed YAML logs a single warning via `print` (prefix `[hermes-otel]`) and falls back to defaults (does NOT silently return `{}`).
- Env vars recognized: `HERMES_OTEL_ENABLED`, `HERMES_OTEL_SAMPLE_RATE`, `HERMES_OTEL_ROOT_SPAN_TTL_MS`, `HERMES_OTEL_FLUSH_INTERVAL_MS`, `HERMES_OTEL_PREVIEW_MAX_CHARS`, `HERMES_OTEL_CAPTURE_PREVIEWS`, `HERMES_OTEL_PROJECT_NAME`.

### AC2 — `clip_preview` helper (`helpers.py`)
- ANSI escape sequences stripped (CSI `\x1b[...m`, OSC `\x1b]...\x07`, ESC single-byte).
- Newlines and tabs normalized to spaces; runs of whitespace collapsed to single space; leading/trailing stripped.
- Returns `None` for `None`, empty string, or whitespace-only/ANSI-only input.
- Strings ≤ `max_chars` returned verbatim; longer strings truncated with trailing `"..."` and total length exactly `max_chars`.
- `capture_previews=False` short-circuits `input.value` / `output.value` emission in hooks.

### AC3 — Sampling
- `config.sample_rate is None` → no sampler passed (default `AlwaysOn` behavior, unchanged).
- `config.sample_rate = X` (0.0 ≤ X ≤ 1.0) → `TracerProvider` constructed with `ParentBased(TraceIdRatioBased(X))`.
- Metrics unaffected by sampling (still exported).

### AC4 — Resource attributes
- `service.name` defaults to `hermes-agent` when not overridden.
- `config.global_tags` merged into resource; keys overridable by `config.resource_attributes`.
- `config.project_name` sets `openinference.project.name`; falls back to env `OTEL_PROJECT_NAME`.
- User-supplied `service.name` via `resource_attributes` wins over the default.

### AC5 — Orphan sweep
- `TurnRegistry.record_start(session_id)` stores `time.perf_counter()` value.
- `_sweep_expired()` is invoked at the top of each `pre_*` hook (pre_tool_call, pre_llm_call, pre_api_request).
- Sessions older than `config.root_span_ttl_ms / 1000` seconds are finalized:
  - all still-active spans for that session end cleanly
  - the session span (if present) gets `hermes.turn.final_status = "timed_out"`
  - span status is `OK` (NOT `ERROR`)
- Registry entries removed on `on_session_end` for the normal path.

### AC6 — Per-turn summary attributes
- `_SESSION_TURN_SUMMARY[session_id]` is a `TurnSummary` updated by each relevant hook.
- On `on_session_end`, the following attributes flush onto the session span when non-empty / non-zero:
  - `hermes.turn.tool_count` (int)
  - `hermes.turn.tools` (CSV, sorted, distinct, capped at 500 chars)
  - `hermes.turn.tool_targets` (pipe-joined, distinct, capped at 500 chars)
  - `hermes.turn.tool_commands` (pipe-joined, distinct, capped at 500 chars)
  - `hermes.turn.tool_outcomes` (CSV, sorted, distinct)
  - `hermes.turn.skill_count` (int)
  - `hermes.turn.skills` (CSV, sorted, distinct)
  - `hermes.turn.api_call_count` (int)
  - `hermes.turn.final_status` ∈ `{"completed", "interrupted", "incomplete", "timed_out"}`
- Attributes are skipped entirely (not set to empty string) when the aggregator is empty.

### AC7 — Tool identity, outcome, skill inference
- `hermes.tool.target`: set when args contain any of `path`, `file_path`, `target`, `url` (first match, path preferred).
- `hermes.tool.command`: set when args contain `command` or `cmd`.
- `hermes.tool.outcome`: emitted on every post_tool_call, value in `{"completed", "error", "timeout", "blocked", ...}`. Explicit `status` field in result wins; `error` field → `"error"`; otherwise `"completed"`.
- Span status is `"error"` iff outcome is `"error"`; other outcomes map to `"ok"` (per "what NOT to copy" — timeouts must not inflate error rates).
- `hermes.skill.name`: attached to tool span when `args.path` matches `/skills/<name>/`, NOT when it matches `/optional-skills/<name>/references/`.
- Metric `hermes.skill.inferred` incremented with `{skill_name, source}` labels on each successful inference.

### AC8 — Backward compatibility
- All 168 existing tests pass unchanged.
- No pre-existing attribute renamed or removed.
- Behavior with zero config (no yaml, no new env vars) is functionally equivalent to the pre-PRD behavior except for the additive attributes listed above.

## Verification

### Unit tests
- `test_config.py`: env-var override, malformed YAML logs warning, missing pyyaml falls back, frozen-dataclass equality.
- `test_helpers.py`: skill inference matches `/skills/<name>/` paths, doesn't match `/optional-skills/<name>/references/`, returns None on miss; tool identity picks `path` over `target` when both present; outcome extraction prefers explicit `status` over `error` over `ok`.
- `test_hooks_helpers.py`: `clip_preview` strips ANSI, collapses whitespace, returns None on empty.

### Integration tests
- `test_orphan_sweep.py`: start a session, simulate hooks-without-end, advance `time.perf_counter` past TTL, fire any `pre_*` hook, assert turn finalized with `final_status=timed_out` and status code OK.
- `test_turn_summary.py`: full hierarchy with two tool calls (bash + read), verify session span carries `hermes.turn.tool_count=2`, `hermes.turn.tools="bash,read"`, `hermes.turn.tool_outcomes="completed"`.
- `test_tool_attributes.py`: tool span has `hermes.tool.target` set when args contain `path=...`; carries `hermes.skill.name="monitor"` when args path matches `/skills/monitor/SKILL.md`.

### E2E tests
- Re-run existing Phoenix and Langfuse e2e tests unchanged. Add one assertion per backend: session span attributes include `hermes.turn.tool_count`.

### Smoke tests
- Re-run `tests/smoke/test_hermes_langfuse.py` against live hermes. No code changes; verify Langfuse generations still roll up correctly (tokens unchanged, status unchanged) and `hermes.turn.*` shows up in observation metadata.

## Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Adding YAML loader breaks env-only deploys | Existing users see no change to behavior | YAML is opt-in (file optional). Env vars still win. |
| `hermes.turn.*` attributes inflate Langfuse generation metadata | Slightly larger payload | Cap `tools` / `tool_targets` strings to 500 chars. Skip emission when zero. |
| TTL sweep races with in-flight tool calls | Tool span might be force-ended mid-execution | Default TTL 10 min — much longer than any real tool call. Document. Add a `min_active_age_ms` floor to be safe. |
| Skill inference produces false positives | Wrong `hermes.skill.name` on tool span | Counter `hermes.skill.inferred{source=...}` lets ops audit. Document rules in README. |
| `capture_previews=false` hides data users expect to see | UX regression | Default `true`. Surface a one-line startup banner when previews are disabled so it's visible. |
| Loading `pyyaml` adds a runtime dependency | Install size | Make it optional — `try: import yaml except ImportError: yaml = None`. Already the pattern in `hermes-otel-plugin`. |

## Out of scope (rejected ideas from `hermes-otel-plugin`)

For each, why we're *not* taking it:

- **Custom attribute schema (`session_id`, `tool_name`, `model_name`)** — breaks Phoenix/Langfuse/LangSmith UI auto-recognition. Our dual-convention emission is the value prop.
- **`openclaw.*` legacy mappings** — we have no legacy.
- **Single global `RLock`** — our `ContextVar` stack is strictly better.
- **`force_flush()` after every turn** — already handled correctly by PRD_concurrency_and_export.md (BatchSpanProcessor with shutdown flush).
- **Skill spans synthesized from tool args** — too speculative; we attach attributes only.
- **Marking `interrupted`/`superseded`/`timed_out` as `StatusCode.ERROR`** — pollutes error rates. We map only real errors.
- **`force_flush()` on unrecognized session ends** — wasted work. Only flush after a real turn finalizes.

## Files to add or modify

- `plugin_config.py` — **new**, dataclass + loader.
- `helpers.py` — **new**, `clip_preview`, `resolve_tool_identity`, `extract_tool_result_status`, `infer_skill_name`.
- `tracer.py` — wire config in, add sampler, add resource attrs from config, add `TurnRegistry`, add sweep call.
- `hooks.py` — replace `_safe_str` → `clip_preview`, add `TurnSummary` aggregator, attach tool identity / outcome / skill attrs, flush summary on session end.
- `__init__.py` — load config before `tracer.init()`.
- `README.md` — config section, `hermes.turn.*` attribute table, privacy mode, skill inference rules.
- `tests/unit/test_config.py` — **new**.
- `tests/unit/test_helpers.py` — **new**.
- `tests/integration/test_orphan_sweep.py` — **new**.
- `tests/integration/test_turn_summary.py` — **new**.
- `tests/integration/test_tool_attributes.py` — **new**.

## Estimated effort

| Phase | Days |
|---|---|
| Config + clip_preview | 1 |
| Orphan sweep + sampling + resource attrs | 1.5 |
| Per-turn summary attributes | 1.5 |
| Tool identity / outcome / skill inference | 2 |
| Docs + e2e/smoke verification | 1 |
| **Total** | **~7 working days** |
