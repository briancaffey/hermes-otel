# Honeycomb support

[Honeycomb](https://www.honeycomb.io/) is supported as a first-class OTLP/HTTP
backend: **traces, metrics, and logs**. This page is the usage guide plus a note
on what's intentionally deferred.

> **Tested how:** the resolver is covered by unit tests
> (`tests/unit/test_backends_honeycomb.py`, no network). Honeycomb is SaaS with
> no free tier available to the maintainers, so live export has **not** been run
> end-to-end against a real Honeycomb account by us — the trace path was
> previously exercised via the generic `otlp` type during the GenAI-semconv work
> (#17). If you have an account, confirmation in a real environment is very
> welcome (see [#20](https://github.com/briancaffey/hermes-otel/issues/20)).

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

- **Traces, modern "Environments" key** — dataset is derived server-side from
  the `service.name` resource attribute. **No `dataset` needed.**
- **Metrics** — a dataset is effectively **required**. Without one, metrics land
  in a dataset literally named `unknown_metrics`.
- **Logs** — routed by the dataset header too.
- **Honeycomb Classic keys** (legacy 32-char) — `dataset` is **required for
  every signal**, including traces.

**Important trade-off:** the plugin applies one merged header set to the trace,
metric, and log exporters, so setting `dataset` tags **all three signals** into
that dataset. For a modern key that means traces stop being split by
`service.name` and get forced into `dataset` too. So:

- Want traces split by service **and** metrics that don't go to
  `unknown_metrics`? You currently can't have both on one Honeycomb entry —
  either omit `dataset` (clean traces, `unknown_metrics` metrics) or set it
  (everything in one dataset). A per-signal `metrics_dataset` is a possible
  follow-up; see [#20](https://github.com/briancaffey/hermes-otel/issues/20).
- On a **Classic** key, just set `dataset` — it's required anyway.

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

## Deferred / known limitations

- **No dashboard query adapter (export-only).** The bundled dashboard queries a
  backend to render traces (e.g. Phoenix via GraphQL). Honeycomb's
  [Query Data API](https://api-docs.honeycomb.io/api/query-data) is
  Enterprise-plan gated, asynchronous (create query → run → poll), oriented
  toward aggregated results rather than fetching raw spans by trace ID, and
  bounded to a 7-day window. That's a poor fit for the dashboard's needs, so
  there is no `dashboard/backends/honeycomb.py`. Use a local backend for
  `query_backend` and Honeycomb's UI for exploration.
- **No automated live verification.** No free tier → verification is unit tests
  + manual confirmation in the Honeycomb UI. `scripts/verify_multi_backend.py`
  does not query Honeycomb.
- **Single dataset across signals** — see "Datasets" above.

---

## Sources

- [Send Data to Honeycomb with OpenTelemetry — Honeycomb Docs](https://docs.honeycomb.io/send-data/opentelemetry)
- [Query Data API — Honeycomb API](https://api-docs.honeycomb.io/api/query-data)
- [Announcing GA of the Honeycomb Query Data API](https://www.honeycomb.io/blog/query-data-api-generally-available)
- [OTLP Exporter Configuration — OpenTelemetry](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)
