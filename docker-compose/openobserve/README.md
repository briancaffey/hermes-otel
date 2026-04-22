# OpenObserve backend — single-container OTel stack

An OpenTelemetry-native observability platform (written in Rust) that
ingests traces, metrics, and logs on one port and stores them on disk
(or S3 in production). No ClickHouse / Postgres / Kafka dependencies —
the default compose is one container.

| Signal | Ingest path | UI |
|---|---|---|
| Traces | `/api/<org>/v1/traces` | Traces tab |
| Metrics | `/api/<org>/v1/metrics` | Metrics tab |
| Logs | `/api/<org>/v1/logs` | Logs tab |

Authentication is HTTP Basic: `Authorization: Basic base64(email:password)`
using the root admin credentials (or any user you create in the UI).

## Quick start

```bash
# From the plugin root:
docker compose -p openobserve -f docker-compose/openobserve.yaml up -d

# Open http://localhost:5080 and log in with the seeded admin:
#   root@example.com / Complexpass#123
```

Point the plugin at it:

```yaml
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user: root@example.com
    password_env: OPENOBSERVE_PASSWORD
    # stream_name: default          # optional; defaults to "default"

capture_logs: true
```

Then:

```bash
export OPENOBSERVE_PASSWORD='Complexpass#123'
```

The plugin encodes a `Basic` auth header from `user` + `password`, and
sends a `stream-name: default` header. Restart hermes-agent;
`[hermes-otel] ✓ OpenObserve at http://localhost:5080/api/default/v1/traces`
should appear in startup logs.

## What you get in the UI

- **Traces** — "Traces" tab. Search by service, span name, attributes.
  The plugin emits OpenInference + GenAI attributes that OpenObserve
  indexes automatically.
- **Metrics** — "Metrics" tab. Query `hermes_*` metrics the plugin
  emits (sessions, tokens, tool durations).
- **Logs** — "Logs" tab. When `capture_logs` is on, every Python
  `logger.info(...)` record lands here with the active span's
  `trace_id` / `span_id` attached; click to jump to the related trace.

## URL path conventions

OpenObserve's OTLP endpoint is **per-organization, per-signal**:

| Signal | Endpoint |
|---|---|
| Traces | `http://host:5080/api/<org>/v1/traces` |
| Logs | `http://host:5080/api/<org>/v1/logs` |
| Metrics | `http://host:5080/api/<org>/v1/metrics` |

The plugin's `endpoint:` field points at the traces URL; the metrics
and logs URLs are derived by swapping the last path segment. The
default organization is `default` and that's what this compose uses;
if you create a different org in the UI, put its name in the URL.

## Ports

| Port | Service | Purpose |
|---|---|---|
| 5080 | OpenObserve | UI + OTLP/HTTP ingestion ← **plugin exports here** |
| 5081 | OpenObserve | OTLP/gRPC ingestion (unused by plugin) |

No collisions with any other hermes-otel stack.

## Tearing down

```bash
docker compose -p openobserve -f docker-compose/openobserve.yaml down
docker compose -p openobserve -f docker-compose/openobserve.yaml down -v   # also drops the volume
```

## Signals supported

- **Traces:** yes — primary use case.
- **Metrics:** yes — both gauges and counters, plus histograms.
- **Logs:** yes — with automatic trace/span correlation.

Set `metrics: false` or `logs: false` per-backend in `config.yaml` if
you want a narrower fan-out.

## Disk vs production mode

This compose runs OpenObserve in "standalone disk" mode — everything
stored in the `oo_data` named volume. Fine for local/CI; for production
switch to the HA mode with S3 storage + etcd + Postgres. See the
upstream docs at https://openobserve.ai/docs/ha_and_production/.
