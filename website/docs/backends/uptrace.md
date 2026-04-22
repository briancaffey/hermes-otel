---
sidebar_position: 9
title: "Uptrace"
description: "Uptrace — OSS all-in-one traces + metrics + logs backend on ClickHouse + Postgres, with DSN-based per-project auth."
---

# Uptrace

[Uptrace](https://uptrace.dev) is an open-source OpenTelemetry backend that stores traces, metrics, and logs in ClickHouse and uses Postgres for metadata (users, projects, alerts). It has a polished UI, PromQL support on metrics, and per-project DSN ingestion tokens so you can multi-tenant one deployment across several apps.

**Signals:** traces + metrics + logs. **Deployment:** local (docker compose) or self-hosted. **Cost:** OSS (premium features require a license key).

## One-command quickstart

The plugin ships a trimmed compose that boots just what hermes-otel needs (ClickHouse + Postgres + Redis + Uptrace — no collector, no Grafana, no Mailpit):

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -p uptrace -f docker-compose/uptrace.yaml up -d
```

Wait ~20s for ClickHouse to finish applying migrations, then open the UI at http://localhost:14318 and log in with the seeded admin:

- **Email:** `admin@uptrace.local`
- **Password:** `admin`

Point the plugin at it — minimal `config.yaml`:

```yaml
backends:
  - type: uptrace
    endpoint: http://localhost:14318/v1/traces
    dsn: http://project1_secret@localhost:14318?grpc=14317

capture_logs: true
```

The `dsn` value is the ingestion DSN for the seeded project (`hermes-agent` under org `hermes-otel`, token `project1_secret`). The plugin sends it as the `uptrace-dsn` header on every OTLP request; Uptrace uses it to route data to the right project.

## Why a dedicated `type: uptrace`

Uptrace authenticates with a custom `uptrace-dsn` header that `type: otlp` can't synthesise for you. Declaring `type: uptrace` asks the plugin to:

1. Read `dsn:` (or `dsn_env:` → env var → `UPTRACE_DSN` / `OTEL_UPTRACE_DSN`).
2. Set the `uptrace-dsn` request header automatically.
3. Display `✓ Uptrace at <endpoint>` in startup logs.
4. Enable all three signals (`supports_metrics=True`, `supports_logs=True`).

Keep secrets out of `config.yaml` — use `dsn_env` and set the variable in your shell:

```yaml
backends:
  - type: uptrace
    endpoint: http://localhost:14318/v1/traces
    dsn_env: UPTRACE_DSN
```

```bash
export UPTRACE_DSN='http://project1_secret@localhost:14318?grpc=14317'
```

## Ports

| Port | Service | Purpose |
|---|---|---|
| 5432 | Postgres | Uptrace metadata store |
| 8123 | ClickHouse | HTTP query API |
| 9000 | ClickHouse | Native protocol |
| 14317 | Uptrace | OTLP/gRPC receiver |
| 14318 | Uptrace | **UI + OTLP/HTTP receiver — the plugin exports here** |

Port 14318 was picked specifically to avoid the standard 4318 used by LGTM / Jaeger / SigNoz, so Uptrace can run alongside them. The only likely conflict is port 5432 with a host-installed PostgreSQL — stop it or remap in `docker-compose/uptrace.yaml`.

## What you'll see in the UI

**Traces:**
Navigation → Traces. Every attribute the plugin emits is searchable (OpenInference + GenAI + `hermes.*`). Group by `llm.model_name`, `openinference.span.kind`, `hermes.session.kind`, etc. Click a trace to see the full waterfall with the same attributes Phoenix and Grafana show.

**Metrics:**
Navigation → Metrics. Query with PromQL over any `hermes_*` metric the plugin emits:

- `hermes_session_count_total` — sessions started
- `hermes_token_usage_total` — prompt / completion tokens by provider / model
- `hermes_tool_duration_bucket` — tool execution histogram

**Logs:**
Navigation → Logs. When `capture_logs: true` is on, every `logger.info(...)` from hermes or the plugin lands here with the active span's `trace_id` and `span_id` attached — click a log record's `trace_id` to jump into the corresponding trace. See [OTel logs](/configuration/logs) for the full story.

## Rotating the DSN

The seeded token `project1_secret` is fine for local dev; change it before exposing the stack anywhere:

1. Edit `docker-compose/uptrace/uptrace.yml` and change `project_tokens[0].token`.
2. Restart the stack — `seed_data.update: true` in the config applies changes on boot.
3. Update the `dsn` (or `UPTRACE_DSN` env var) in your hermes-otel config to match.

Or, do it from the UI: Settings → Projects → Edit → rotate token, then update the plugin config.

## Multi-backend fan-out

Uptrace plays nicely alongside other backends because its ports don't collide:

```yaml
backends:
  - type: uptrace
    endpoint: http://localhost:14318/v1/traces
    dsn_env: UPTRACE_DSN

  - type: phoenix
    endpoint: http://localhost:6006/v1/traces

capture_logs: true
```

Traces fan out to both, metrics to both, logs only to Uptrace (Phoenix doesn't accept OTLP logs — the plugin skips it automatically).

## Troubleshooting

**"Uptrace is up but I see `unauthorized` in the agent logs"**

The `uptrace-dsn` header is missing or its token doesn't match the seed config. Confirm the DSN matches `project_tokens[0].token` in `docker-compose/uptrace/uptrace.yml`.

**"I see `✓ Uptrace at ...` but no traces appear"**

Uptrace batches writes to ClickHouse — first traces may take 10-30s to show up. If they still don't appear, check `docker logs hermes-otel-uptrace` for ClickHouse connection errors.

**"ClickHouse container is unhealthy"**

Usually a port bind conflict on 8123 or 9000. Stop any other ClickHouse running on the host (a SigNoz stack, for example) before bringing Uptrace up.

**"Postgres container is unhealthy"**

Host-installed PostgreSQL is almost certainly bound to 5432 already. Either stop it or remap the compose port: `ports: ["5433:5432"]` and adjust your firewall rules.

## Production usage

The shipped compose uses `seed_data` for local bootstrap — fine for dev, not for prod. For a production deployment you should:

- Disable `seed_data` after initial bootstrap (or set it to a non-admin user / restricted org).
- Change `service.secret` to a real secret (used for cryptographic operations).
- Point `ch_cluster.shards[].replicas[].addr` at a real ClickHouse cluster (replicated or distributed), not the single-node container.
- Put a reverse proxy (nginx, Caddy) in front and enable TLS.
- Replace the seeded users / tokens with real ones via the UI.

See [Uptrace's production docs](https://uptrace.dev/get/hosted/production) for the full checklist.

## See also

- [OTel logs](/configuration/logs) — the log pipeline that makes trace-id correlation work.
- [Multi-backend fan-out](/backends/multi-backend) — adding Uptrace alongside other backends.
- [docker-compose/uptrace/README.md](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/uptrace/README.md) — the on-disk README with the same setup walkthrough.
