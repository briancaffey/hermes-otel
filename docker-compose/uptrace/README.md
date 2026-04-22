# Uptrace backend — ClickHouse + Postgres + Redis + Uptrace

A self-hosted open-source observability stack that covers all three OTel
signals in one UI.

| Signal | Storage | UI |
|---|---|---|
| Traces | ClickHouse | Uptrace UI (`/traces`) |
| Metrics | ClickHouse | Uptrace UI (`/metrics`) |
| Logs | ClickHouse | Uptrace UI (`/logs`) |

Uptrace ingests OTLP/HTTP and OTLP/gRPC directly — the plugin exports to
it with no intermediate collector. Authentication is per-project via a
DSN token the plugin sends as the `uptrace-dsn` header.

## Quick start

```bash
# From the plugin root:
docker compose -p uptrace -f docker-compose/uptrace.yaml up -d

# First boot bootstraps Postgres + ClickHouse schemas (~20s). Open
# http://localhost:14318 and log in:
#   admin@uptrace.local / admin
```

Point the plugin at it:

```yaml
backends:
  - type: uptrace
    endpoint: http://localhost:14318/v1/traces
    dsn: http://project1_secret@localhost:14318?grpc=14317

capture_logs: true
```

The `dsn` above matches the seed data in `uptrace.yml` (one project named
`hermes-agent` under org `hermes-otel`, token `project1_secret`). Rotate
by editing `docker-compose/uptrace/uptrace.yml` and restarting the stack;
don't forget to update `config.yaml` to match.

Restart hermes-agent; `[hermes-otel] ✓ Uptrace at http://localhost:14318/v1/traces`
should appear in startup logs.

## What you get in the UI

- **Traces** — the "Spans" and "Traces" pages. Every attribute the plugin
  emits is searchable (OpenInference + GenAI + `hermes.*`). Group by
  `llm.model_name`, `openinference.span.kind`, etc.
- **Metrics** — Prometheus-style PromQL over the metrics the plugin
  emits (`hermes_session_count_total`, `hermes_token_usage_total`, the
  tool-duration histogram).
- **Logs** — structured logs with automatic `trace_id` / `span_id`
  correlation when `capture_logs` is on. Clicking a log record's
  `trace_id` jumps to the full trace.

## Ports

| Port | Service | Purpose |
|---|---|---|
| 5432 | Postgres | Uptrace metadata |
| 8123 | ClickHouse | HTTP query API |
| 9000 | ClickHouse | Native protocol |
| 14317 | Uptrace | OTLP/gRPC receiver |
| 14318 | Uptrace | UI + OTLP/HTTP receiver ← **plugin exports here** |

**Conflicts to watch for:**

- **5432** collides with a host-installed PostgreSQL. Either stop the
  host one or remap: `ports: ["5433:5432"]`.
- **8123** / **9000** collide with any other ClickHouse instance (e.g.
  a SigNoz stack that exposes its own ClickHouse).
- Port 14318 was picked to NOT collide with the standard OTLP 4318 the
  LGTM / jaeger / signoz stacks use, so Uptrace can run alongside them.

## Tearing down

```bash
# Stop containers, keep volumes (Postgres + ClickHouse data persists):
docker compose -p uptrace -f docker-compose/uptrace.yaml down

# Stop + drop volumes (full reset — re-applies seed_data on next up):
docker compose -p uptrace -f docker-compose/uptrace.yaml down -v
```

## Signals supported

- **Traces:** yes — primary use case.
- **Metrics:** yes — OTLP metrics are stored in ClickHouse and queryable
  via the Uptrace UI. (Note: Uptrace is Prometheus-compatible via a
  separate endpoint; the plugin uses the OTLP path.)
- **Logs:** yes — stored alongside traces with trace-ID correlation.

Set `metrics: false` / `logs: false` per-backend in `config.yaml` if you
want traces only.

## Why not just run the upstream compose?

The upstream `example/docker/docker-compose.yml` bundles eight extra
services (Grafana, Prometheus, Mailpit, Vector, otelcol, node_exporter,
TLS certs) that hermes-otel doesn't use. The trimmed compose here boots
in ~20s instead of ~60s and drops dependency on files we'd otherwise have
to vendor from the upstream repo (Grafana datasources, Vector config,
TLS keys). If you want the full experience, clone
github.com/uptrace/uptrace and run their example directly.
