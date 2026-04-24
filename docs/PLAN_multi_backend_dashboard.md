# Multi-backend trace querying in the hermes-otel dashboard tab

## Goal

Let the OTel Traces tab query any configured `backends:` entry — not
just LGTM/Tempo. Keep the list/detail/search UX unchanged; swap the
data source underneath.

## Locked design decisions

1. **No cross-syntax translation.** Each backend implementation owns
   its own native query path (GraphQL for Phoenix, REST for Tempo /
   Jaeger / Langfuse, SQL for OpenObserve, UQL for Uptrace, composite
   JSON for SigNoz). The raw query field is passed through verbatim
   and labelled dynamically per backend. The dashboard never tries to
   transpile between query languages.
2. **Silent fallback** when `query_backend:` pins a backend that isn't
   present — fall through to the first queryable entry.
3. **Localhost-first auth posture.** Each adapter attempts unauth'd
   against localhost; if the backend cfg has credentials
   (`api_key`/`api_key_env`, `public_key`+`secret_key`,
   `user`+`password`, `ingestion_key`, `dsn` with secret), it uses
   them. Every adapter must *support* auth; none requires it.
4. **One status bar for the active backend.** The other configured
   backends still appear as chips underneath (already implemented) so
   the user can see the full picture. No per-backend health checks
   beyond the active one.
5. **Langfuse + Jaeger get first-class adapters.** Not running locally
   right now, but the code ships so they work the day you deploy
   them. They're the last phases — implemented but only smoke-tested
   if/when a container is up.
6. **No CI integration.** Tests are local-only; integration tests
   gated by an opt-in marker and a running container.

---

## Context (what's in place today)

- `dashboard/plugin_api.py` has `_resolve_backend()` that picks the
  first backend in `config.yaml` whose `type` is in `{lgtm, tempo}`.
- Three FastAPI routes (`/status`, `/traces/search`, `/traces/{id}`)
  proxy to Tempo's HTTP query API.
- The frontend parses two shapes:
  - **Search response**: `{traces: [{traceID, rootServiceName,
    rootTraceName, startTimeUnixNano, durationMs, spanSets: [...]}]}`
    where `spanSets[0].spans[].attributes` carries the TraceQL
    `select()` enrichment.
  - **Detail response**: OTLP JSON (`{batches: [{resource,
    scopeSpans: [{spans}]}]}`).
- The TraceQL input field in `FiltersForm` feeds the `q=` query
  parameter verbatim.
- The CSS scoping (`.hermes-otel-tab`) and card rendering (model,
  provider, tokens, input/output previews, chevron) are all shape-
  agnostic — they only need `spanSets` with the named attributes.

Everything below preserves those two shapes. The frontend does not
change except for (1) a dynamic raw-syntax label on the filter form
and (2) two new structured-filter inputs.

---

## Architecture

### Directory layout

```
dashboard/
  plugin_api.py              # thin FastAPI routes, delegates to adapters
  backends/
    __init__.py              # registry + resolve_backend()
    base.py                  # BackendAdapter ABC + StructuredFilter
    tempo.py                 # existing Tempo/LGTM code, extracted
    phoenix.py               # phase 1
    signoz.py                # phase 2
    uptrace.py               # phase 3
    openobserve.py           # phase 4
```

`dashboard/` stays flat (no `__init__.py`) because the Hermes plugin
loader uses `importlib.util.spec_from_file_location` on
`plugin_api.py`. To let that file import from `backends/` cleanly,
`plugin_api.py` adds its own directory to `sys.path` once at import
time — a 4-line shim. This is the only non-obvious piece; encapsulate
in `plugin_api.py`.

### Base interface (`backends/base.py`)

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class StructuredFilter:
    service: str | None = None
    name_regex: str | None = None
    attr_equals: dict[str, str] = field(default_factory=dict)
    min_duration_ms: int | None = None
    status: Literal["ok", "error"] | None = None
    free_text: str | None = None          # full-text over prompt/response
    raw: str | None = None                # backend-native pass-through

class BackendAdapter:
    handles: frozenset[str] = frozenset()  # which cfg `type` values
    query_lang_label: str = "query"        # shown on the raw input

    def __init__(self, cfg: dict, in_docker: bool):
        self.cfg = cfg
        self.in_docker = in_docker

    # Must return {healthy: bool, query_url: str|None, ...}
    def status(self) -> dict: ...

    # Must return the Tempo-shaped {traces: [...], metrics: {...}}
    def search(self, f: StructuredFilter, start: int,
               end: int, limit: int) -> dict: ...

    # Must return OTLP JSON {batches: [{resource, scopeSpans: [...]}]}
    def get_trace(self, trace_id: str) -> dict: ...
```

Adapters raise `HTTPException(502, ...)` on backend failure. The
router layer doesn't wrap them.

### Registry (`backends/__init__.py`)

```python
_ADAPTERS: list[type[BackendAdapter]] = []

def register(cls):
    _ADAPTERS.append(cls)
    return cls

def resolve_adapter(cfg: dict, in_docker: bool) -> BackendAdapter | None:
    """Pick the adapter for the configured query backend.

    Precedence:
      1. cfg['query_backend'] pins a backend by name or type. If that
         entry is present AND has a registered adapter, use it.
      2. Otherwise (including when the pin misses) fall through to the
         first entry in cfg['backends'] whose type has an adapter.
      3. Return None only if no configured backend has an adapter.
    """
```

Silent fallback is intentional — the status bar reports the resolved
backend, so the user can still see what's active.

Adapters self-register at import time. `plugin_api.py` imports the
backends package once, which triggers registration.

### Shape normalisation

Every adapter's `search()` returns:

```json
{
  "traces": [{
    "traceID": "<hex>",
    "rootServiceName": "<service>",
    "rootTraceName": "<root span name>",
    "startTimeUnixNano": "<str-encoded-uint64>",
    "durationMs": 123,
    "spanSets": [{
      "spans": [{
        "name": "<span name>",
        "attributes": [{"key": "llm.model_name",
                        "value": {"stringValue": "gpt-4"}}, ...]
      }]
    }]
  }],
  "metrics": { "inspectedBytes": "...", ... }  // optional
}
```

Every adapter's `get_trace()` returns OTLP JSON:

```json
{
  "batches": [{
    "resource": {"attributes": [...]},
    "scopeSpans": [{"spans": [{
      "traceId": "...", "spanId": "...", "parentSpanId": "...",
      "name": "...", "kind": 1,
      "startTimeUnixNano": "...", "endTimeUnixNano": "...",
      "attributes": [...], "status": {"code": 1}
    }]}]
  }]
}
```

For Tempo this is a pass-through. For every other backend the
adapter rebuilds OTLP from the native response — that's the main
translation work.

---

## Config schema

Add one top-level optional key to `HermesOtelConfig` and
`config.yaml`:

```yaml
# Pin which backend the dashboard queries (by name or type).
# Default: first queryable entry in backends:.
query_backend: lgtm
```

`BackendConfig` entries don't change. No migration needed.

---

## Phase 0 — Refactor (foundation)

**No behavioural change.** Gate on: current tests green.

- [ ] Create `backends/base.py` with `BackendAdapter` + `StructuredFilter`.
- [ ] Create `backends/__init__.py` with registry + `resolve_adapter`.
- [ ] Extract Tempo code from `plugin_api.py` into `backends/tempo.py`.
  - The `_derive_query_base`, `_rewrite_host_for_docker`, and search
    / get_trace helpers move verbatim. `_CARD_SELECT_ATTRS` +
    `_build_traceql` live in `tempo.py`.
- [ ] Rewrite `plugin_api.py`:
  - sys.path shim + `from backends import resolve_adapter`.
  - Routes call `adapter.status() / search(filter, …) / get_trace(id)`.
  - Parse query params → `StructuredFilter`. TraceQL today comes in
    via `?q=`; map it to `filter.raw` with no semantic change.
- [ ] Extend `/status` response:
  - Add `query_lang_label` (string, e.g. `"TraceQL"`).
  - Add `raw_placeholder` (string, e.g. `'{ .llm.provider = "openai" }'`).
- [ ] Frontend (`dist/index.js`):
  - `FiltersForm` reads label + placeholder from status. If absent,
    defaults to `"TraceQL filter (optional)"` and the current
    placeholder — compatibility for the first deploy.
  - Submit remains `raw = form.q`. No other change yet.

Success criterion: `/traces/search?raw=...` returns the same shape as
before; dashboard behaviour is indistinguishable from today.

---

## Phase 1 — Phoenix adapter

**Why first**: running, LLM-native, clean GraphQL schema, best for
smoke-testing the adapter contract.

- [ ] `backends/phoenix.py`:
  - `handles = frozenset({"phoenix"})`, label `"GraphQL filter"`.
  - `search()`:
    - Compose GraphQL query: `{ projects(first: 1) { edges { node {
      spans(first: $limit, filter: $filter,
      timeRange: {start, end}) { edges { node { traceId name
      startTime endTime attributes } } } } } } }`.
    - Translate `StructuredFilter`:
      - `service` → `attributes['service.name'] == '...'`
      - `attr_equals` → `attributes['<k>'] == '<v>'` joined with `and`
      - `min_duration_ms` → `latencyMs >= N`
      - `status == error` → `statusCode == 'ERROR'`
      - `free_text` → `'<v>' in attributes['input.value']` (or
        `llm.prompt` depending on what Phoenix stores)
      - `raw` → pass through as additional filter expression
    - Group spans by `traceId` client-side. Root span = the one with
      no parent in the group (or earliest start if ambiguous).
    - Build `spanSets` by picking the set of attrs matching
      `_CARD_SELECT_ATTRS` from any span in the trace.
  - `get_trace()`:
    - GraphQL: query all spans for `traceId = $id`.
    - Translate Phoenix's attribute list (object form) → OTLP
      `{"key":..., "value": {...}}` shape.
    - Emit a single batch with all spans.
- [ ] Unit tests with mocked GraphQL responses:
  - Fixture: minimal 2-trace response covering an LLM call + tool call.
  - Assert normalised search shape.
  - Assert normalised detail OTLP shape.
- [ ] Smoke-test against running Phoenix at `:6006`.

Risks: Phoenix's GraphQL schema has changed between versions; pin the
query to fields introduced ≥4.0. Check live with an introspection
query before writing the real one — 5-minute task at start of phase.

---

## Phase 2 — SigNoz adapter

**Why second**: most complex API of the four, getting it right
validates the abstraction.

- [ ] `backends/signoz.py`:
  - `handles = frozenset({"signoz"})`, label `"SigNoz query-builder JSON"`.
  - Auth: honour `ingestion_key_env` / `ingestion_key` from the
    backend cfg; also accept `SIGNOZ_API_KEY` env var. No auth for
    localhost OSS; try unauth'd first, escalate on 401.
  - `search()`:
    - `POST /api/v4/query_range` with composite query (builder JSON
      for `traces` data source).
    - Translate structured filter → `filters.items[]` with correct
      `op` ("=", "like", ">=").
    - `raw` → appended as a SigNoz-formatted filter dict if parseable
      JSON, otherwise passed in `filters.rawExpr` (if supported) or
      stored as a free-text filter.
  - `get_trace()`:
    - `GET /api/v1/traces/{traceID}` returns a flat span list.
    - Translate to OTLP batches (one batch per service, scopeSpans
      grouped by instrumentation scope when present).
- [ ] Span shape translation is the meat of this phase. Write it
  once, reuse in `uptrace.py` / `openobserve.py` — factor into
  `backends/_otlp.py` helper.
- [ ] Unit + smoke tests.

Risks: SigNoz query-builder JSON is under-documented. Capture a
reference query from the SigNoz UI (dev tools → network) and use it
as the template.

---

## Phase 3 — Uptrace adapter

- [ ] `backends/uptrace.py`:
  - `handles = frozenset({"uptrace"})`, label `"UQL"`.
  - Auth: bearer token from `dsn` / `dsn_env` (extract secret).
  - `search()`: `POST /api/v1/tracing/{project_id}/search/spans` with
    UQL built from structured filter + raw.
  - `get_trace()`: `GET /api/v1/tracing/{project_id}/traces/{trace_id}`.
  - Uptrace's native shape is close to OTLP; translation is light.

---

## Phase 4 — OpenObserve adapter

- [ ] `backends/openobserve.py`:
  - `handles = frozenset({"openobserve"})`, label `"SQL WHERE"`.
  - Auth: HTTP Basic from `user` + `password` (or `_env` variants).
  - `search()`: `POST /api/{org}/_search` with SQL built from
    structured filter + raw WHERE fragment. Default org = `default`,
    override via `stream_name`/org cfg fields.
  - `get_trace()`: same endpoint with `WHERE trace_id = '...'`;
    group rows into OTLP batches.
  - Flattened attribute columns (`attributes_llm_model_name`) need a
    helper that unflattens back to `llm.model_name` in the OTLP shape.

---

## Phase 5 — Langfuse adapter

Not running locally today; the code ships for when it is. Langfuse is
LLM-native, so cards surface cleanly from its REST shape.

- [ ] `backends/langfuse.py`:
  - `handles = frozenset({"langfuse"})`, label `"Langfuse filter (JSON)"`.
  - Auth: HTTP Basic with `public_key` + `secret_key`
    (or `*_env` variants). Both required — no localhost-unauth path.
  - `search()`:
    - `GET /api/public/traces?fromTimestamp=&toTimestamp=&limit=&name=&userId=&sessionId=`.
    - Translate `StructuredFilter`:
      - `service` → ignored (Langfuse doesn't model services the same
        way; document this in the adapter docstring).
      - `attr_equals` with keys `userId`/`sessionId`/`name` → native
        query params. Other keys → client-side filter on the response.
      - `min_duration_ms` → client-side filter (Langfuse doesn't support
        server-side duration filter in the public API).
      - `raw` → parsed as JSON of extra query params, merged in.
    - Fetch each trace's observations via `GET /api/public/observations`
      in a follow-up call for the top N results (cap at `limit`, avoid
      N+1 blowup). Map observations → `spanSets[].spans[].attributes`.
  - `get_trace()`:
    - `GET /api/public/traces/{traceId}` for the trace envelope.
    - `GET /api/public/observations?traceId={traceId}` for spans.
    - Build OTLP batches. Langfuse observation types (`GENERATION`,
      `SPAN`, `EVENT`) map to span kind via a small lookup table.
- [ ] Unit tests with mocked REST responses.

---

## Phase 6 — Jaeger adapter

Not running locally today.

- [ ] `backends/jaeger.py`:
  - `handles = frozenset({"jaeger"})`, label `"tag=value (Jaeger tags)"`.
  - Auth: typically none for self-hosted; support optional `api_key`
    header for cloud-hosted Jaeger / Grafana Cloud.
  - `search()`:
    - `GET /api/traces?service=&operation=&tags=&limit=&start=&end=&minDuration=&maxDuration=`.
    - `start`/`end` are in microseconds — convert from the nanosecond
      convention used elsewhere in the plugin.
    - `tags` encoding: JSON-encoded object `{"llm.model_name":"gpt-4"}`.
    - Translate `StructuredFilter`:
      - `service` → `service=`
      - `attr_equals` → `tags=<json>`
      - `min_duration_ms` → `minDuration=<N>ms`
      - `status == error` → `tags={"error":"true"}`
      - `raw` → parsed as `k=v k=v` space-separated pairs and merged
        into `tags`.
  - `get_trace()`:
    - `GET /api/traces/{traceID}` — Jaeger's response is in its own
      "Jaeger JSON" shape (`{data: [{traceID, spans, processes}]}`).
    - Translate to OTLP batches via a small helper.
- [ ] Unit tests with mocked responses.

---

## Phase 7 — Frontend refinements

- [ ] `FiltersForm` gains two optional inputs alongside the raw field:
  - `service` text input.
  - `min_duration_ms` already exists — keep.
  - Everything else lives under an "Advanced" disclosure (collapsible,
    default closed): attribute equals rows (key + value pairs, add/
    remove), status select, free-text input.
- [ ] Raw-field label + placeholder come from `/status.query_lang_label`
  + `/status.raw_placeholder`.
- [ ] Status bar shows the resolved backend type chip with the
  `query_lang_label` so the user sees which DSL they're typing.
- [ ] `/traces/search` request moves to POST with a JSON body when
  the structured filter is non-trivial (attribute equals gets
  awkward in query strings). GET remains supported for the common
  case; frontend picks based on payload size. (Optional — can stay
  GET-only if we accept URL-encoded form.)

---

## Testing

### Unit tests (`tests/unit/test_backends_<name>.py`)
- Mock HTTP with `urllib` + `unittest.mock.patch`.
- Use a single fixture file per backend holding a captured real
  response; translate through the adapter and assert shape.
- Table-driven tests for `StructuredFilter → native query` mapping.

### Integration (`tests/integration/test_backend_live.py`)
- Guarded by marker `-m backend_live`. Opt-in, requires the
  respective docker compose up. **No CI integration** — local only.
- One test per running adapter: search with a known filter, assert
  non-empty + required fields present. Langfuse / Jaeger skipped
  until their containers are up.

### Frontend
- No frontend tests exist today; leave as-is. Manual smoke via
  hard-refresh + each filter permutation.

---

## Rough sizing

| Phase | Files touched | Smoke-testable locally? | Est. effort |
|---|---|---|---|
| 0 — Refactor | 3 py + 1 js | Yes (Tempo) | 1 h |
| 1 — Phoenix | 1 py + tests | Yes | 1.5 h |
| 2 — SigNoz | 2 py + tests | Yes | 3 h |
| 3 — Uptrace | 1 py + tests | Yes | 1.5 h |
| 4 — OpenObserve | 1 py + tests | Yes | 1.5 h |
| 5 — Langfuse | 1 py + tests | No (not deployed) | 1 h |
| 6 — Jaeger | 1 py + tests | No (not deployed) | 1 h |
| 7 — FE polish | 1 js + 1 css | Yes | 1 h |
| **Total** | | | **~11.5 h** |

Phases are independent after phase 0. Stop points between any two.
Phases 5 and 6 can be deferred without blocking the rest.
