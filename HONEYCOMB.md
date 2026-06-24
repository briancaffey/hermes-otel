# Honeycomb support

[Honeycomb](https://www.honeycomb.io/) is supported as a first-class OTLP/HTTP
backend: **traces, metrics, and logs**. This page is the usage guide plus a note
on what's intentionally deferred.

> **Tested:** verified end-to-end against a real Honeycomb free-tier account
> (US, a modern "Environments" key). A real `hermes` turn fanned spans out to
> Honeycomb alongside Phoenix; traces, metrics, and logs all registered, the
> OTLP export returned success, and Honeycomb auto-created the datasets. The
> resolver is also covered by unit tests (`tests/unit/test_backends_honeycomb.py`,
> no network). See [Verified behavior](#verified-behavior) for the routing
> details, which differ from older docs. (See
> [#20](https://github.com/briancaffey/hermes-otel/issues/20).)

---

## Quick start

Put your key in an env var and add a backend entry to the plugin's `config.yaml`:

```bash
export HONEYCOMB_API_KEY="hcaik_..."
```

```yaml
backends:
  - type: honeycomb
    region: us                 # us (default) or eu
    api_key_env: HONEYCOMB_API_KEY
    # dataset: hermes          # optional — read "Datasets" below first
```

That's it. The plugin fills in the endpoint and the `x-honeycomb-team` auth
header, and derives the per-signal paths automatically.

Single-backend env-var mode also works with no `config.yaml`: set
`HONEYCOMB_API_KEY` (US endpoint) and, optionally,
`OTEL_HONEYCOMB_ENDPOINT` to override the URL (e.g. the EU host).

> Honeycomb is **export-only** in the bundled dashboard — see
> [Deferred](#deferred--known-limitations). Point `query_backend` at a local
> backend (Phoenix/Tempo) and explore Honeycomb data in Honeycomb's own UI.

---

## Configuration reference

| Field | Meaning |
|-------|---------|
| `region` | `us` (default) → `https://api.honeycomb.io`, `eu` → `https://api.eu1.honeycomb.io`. Ignored if `endpoint` is set. |
| `api_key` / `api_key_env` | Honeycomb ingest key → `x-honeycomb-team` header. Prefer `api_key_env`. Falls back to `HONEYCOMB_API_KEY` / `OTEL_HONEYCOMB_API_KEY`. |
| `dataset` | Optional → `x-honeycomb-dataset` header. See "Datasets". |
| `endpoint` | Optional full `/v1/traces` URL; overrides `region`. Also via `OTEL_HONEYCOMB_ENDPOINT`. |
| `metrics` / `logs` / `traces` | Per-signal toggles. All default on for Honeycomb. |
| `headers` | Extra headers, merged on top of the generated ones. |

The plugin uses **OTLP/HTTP** (not gRPC). Metrics and logs endpoints are derived
from the traces endpoint by rewriting the `/v1/traces` suffix to `/v1/metrics`
and `/v1/logs` (`_derive_metrics_endpoint` in `tracer.py`,
`_derive_logs_endpoint` in `log_handler.py`) — which matches Honeycomb's
per-signal path scheme.

---

## Datasets (read this before enabling metrics)

Honeycomb routes data into *datasets*, and the rules differ by signal and key
type:

**Modern "Environments" keys (what most accounts use today):**

- **Traces** route by the `service.name` resource attribute → a dataset of that
  name is auto-created. The `x-honeycomb-dataset` header is **ignored**.
- **Metrics** route to the environment's default metrics dataset (named
  `Metrics`). The header is **ignored** here too.
- **Logs** are ingested as events.
- ⇒ With a modern key, **`dataset` has no observable effect** — you can leave it
  unset. Control your trace dataset name via `service.name` (the plugin sets it
  from the OTel resource / project name). This was confirmed by live testing
  (see [Verified behavior](#verified-behavior)).

**Honeycomb Classic keys (legacy 32-char):**

- `x-honeycomb-dataset` is honored and **required for every signal**. Set
  `dataset` so data isn't rejected / mis-routed.

Because the plugin applies one merged header set to the trace, metric, and log
exporters, a `dataset` you set is sent on all three. That's correct for Classic
keys; for modern keys it's simply ignored. If a future use case needs distinct
per-signal datasets on Classic keys, a `metrics_dataset` field could be added —
see [#20](https://github.com/briancaffey/hermes-otel/issues/20).

---

## Multiple backends

Honeycomb runs happily alongside a local backend — each gets its own batch
processor and worker thread, so they export in parallel and a slow one can't
block the others. A common setup is local Phoenix for the bundled dashboard plus
Honeycomb for durable storage:

```yaml
query_backend: phoenix
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
  - type: honeycomb
    region: us
    api_key_env: HONEYCOMB_API_KEY
```

---

## Verified behavior

Confirmed against a real Honeycomb free-tier account (US region, modern
Environments key) by running a real `hermes` turn with Honeycomb configured
alongside Phoenix:

- **Startup:** `✓ Honeycomb at https://api.honeycomb.io/v1/traces`, logs and
  metrics fan-out both reported Honeycomb as a target (Phoenix was traces-only).
- **Export:** a span pushed through the plugin's resolved exporter returned
  `SpanExportResult.SUCCESS`; the full `hermes` turn produced no export errors.
- **Ingestion:** Honeycomb auto-created datasets — a **trace** dataset named
  after `service.name` (`hermes-agent`, ~16 columns from the gen_ai/`llm.*`
  attributes) and a default **`Metrics`** dataset. The `x-honeycomb-dataset`
  header set in config did **not** override either, confirming it's ignored for
  modern keys.
- **Couldn't verify:** individual column names / span contents — the test key's
  scope was `events + createDatasets + markers` but **not** `columns` or
  `queries`, so the management API can't enumerate columns or run queries. The
  dataset column counts are strong evidence the attributes landed; full
  attribute-level confirmation needs a key with `columns`/`queries` access or a
  look in the Honeycomb UI.

## Deferred / known limitations

- **No dashboard query adapter (export-only).** The bundled dashboard queries a
  backend to render traces (e.g. Phoenix via GraphQL). Honeycomb's
  [Query Data API](https://api-docs.honeycomb.io/api/query-data) is
  Enterprise-plan gated, asynchronous (create query → run → poll), oriented
  toward aggregated results rather than fetching raw spans by trace ID, and
  bounded to a 7-day window. That's a poor fit for the dashboard's needs, so
  there is no `dashboard/backends/honeycomb.py`. Use a local backend for
  `query_backend` and Honeycomb's UI for exploration.
- **No automated live verification in CI.** Live ingestion was confirmed
  manually (see [Verified behavior](#verified-behavior)); CI relies on the
  no-network unit tests. `scripts/verify_multi_backend.py` does not query
  Honeycomb.
- **Dataset header ignored on modern keys** — see "Datasets" above. Control the
  trace dataset via `service.name`.

---

## Sources

- [Send Data to Honeycomb with OpenTelemetry — Honeycomb Docs](https://docs.honeycomb.io/send-data/opentelemetry)
- [Query Data API — Honeycomb API](https://api-docs.honeycomb.io/api/query-data)
- [Announcing GA of the Honeycomb Query Data API](https://www.honeycomb.io/blog/query-data-api-generally-available)
- [OTLP Exporter Configuration — OpenTelemetry](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)
