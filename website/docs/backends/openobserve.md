---
sidebar_position: 10
title: "OpenObserve"
description: "OpenObserve — OSS Rust-based all-in-one traces + metrics + logs backend, single container, HTTP Basic auth."
---

# OpenObserve

[OpenObserve](https://openobserve.ai) is an OpenTelemetry-native observability platform written in Rust. It ingests all three signals on one port, stores them on local disk (or S3 for production), and has no external dependencies — the default deployment is literally one container.

**Signals:** traces + metrics + logs. **Deployment:** local (single container) or self-hosted HA with S3 + etcd. **Cost:** OSS.

## One-command quickstart

A compose file ships with the plugin:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -p openobserve -f docker-compose/openobserve.yaml up -d
```

Open http://localhost:5080 and log in with the default admin:

- **Email:** `root@example.com`
- **Password:** `Complexpass#123`

Point the plugin at it — minimal `config.yaml`:

```yaml
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user: root@example.com
    password_env: OPENOBSERVE_PASSWORD
    # stream_name: default          # optional; defaults to "default"

capture_logs: true
```

```bash
export OPENOBSERVE_PASSWORD='Complexpass#123'
```

The plugin encodes `Basic base64(user:password)` into an `Authorization` header and adds a `stream-name` header automatically. Restart hermes-agent; you should see `[hermes-otel] ✓ OpenObserve at http://localhost:5080/api/default/v1/traces` in the startup logs.

## Endpoint URL structure

OpenObserve's OTLP endpoint is **per-organization, per-signal**:

| Signal | URL template |
|---|---|
| Traces | `http://HOST:5080/api/<org>/v1/traces` |
| Logs | `http://HOST:5080/api/<org>/v1/logs` |
| Metrics | `http://HOST:5080/api/<org>/v1/metrics` |

Configure `endpoint:` with the **traces** URL; the plugin derives the other two by swapping the final path segment. The default organization is `default` — if you create additional orgs in the UI, use their name in the URL.

## Why a dedicated `type: openobserve`

OpenObserve uses HTTP Basic auth plus a `stream-name` request header that `type: otlp` can't produce for you. Declaring `type: openobserve` asks the plugin to:

1. Read `user`/`password` (with `user_env`/`password_env` for env-driven secrets, plus fallbacks to `OPENOBSERVE_USER` / `OPENOBSERVE_PASSWORD`).
2. Build `Authorization: Basic base64(user:password)` automatically.
3. Add `stream-name: <stream_name>` (default `default`).
4. Display `✓ OpenObserve at <endpoint>` in startup logs.
5. Enable all three signals (`supports_metrics=True`, `supports_logs=True`).

## Ports

| Port | Service | Purpose |
|---|---|---|
| 5080 | OpenObserve | **UI + OTLP/HTTP ingestion — the plugin exports here** |
| 5081 | OpenObserve | OTLP/gRPC ingestion (unused by the plugin) |

No collisions with any other hermes-otel stack — OpenObserve can run concurrently with everything else.

## What you'll see in the UI

**Traces:**
Traces tab. Every span the plugin emits is indexed with its OpenInference / GenAI / `hermes.*` attributes. Filter by attribute, pivot to the waterfall, click into spans. OpenObserve's search syntax is SQL-flavoured (VRL under the hood).

**Metrics:**
Metrics tab. The `hermes_*` metrics (session counts, token usage, tool-duration histograms) show up as time series with their resource attributes intact.

**Logs:**
Logs tab. With `capture_logs: true`, every Python `logger.info(...)` record lands here stamped with the active span's `trace_id` and `span_id` — click a `trace_id` to jump to the corresponding trace. See [OTel logs](/configuration/logs) for the full pipeline.

## Streams

OpenObserve partitions incoming data into named **streams**. Without a `stream-name` header everything lands in `default`; setting a different name routes telemetry to its own stream (useful for separating envs: `hermes-prod`, `hermes-staging`). Set it per-backend:

```yaml
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user_env: OPENOBSERVE_USER
    password_env: OPENOBSERVE_PASSWORD
    stream_name: hermes-prod
```

Streams and their retention / index settings are managed in the OpenObserve UI under Settings → Streams.

## Organizations and users

The docker quickstart seeds one organization (`default`) and one root user. To split telemetry across environments or teams:

1. In the UI, go to Settings → Organizations → create new.
2. Put the new org's name in the endpoint path: `http://HOST:5080/api/<new-org>/v1/traces`.
3. Create additional users under Settings → Users → Invite (OpenObserve sends an email; for local dev the invite token shows up in the container logs).

## Multi-backend fan-out

OpenObserve's port doesn't collide with anything, so it drops cleanly into a fan-out:

```yaml
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user_env: OPENOBSERVE_USER
    password_env: OPENOBSERVE_PASSWORD

  - type: phoenix
    endpoint: http://localhost:6006/v1/traces

capture_logs: true
```

Traces fan out to both, metrics to both, logs only to OpenObserve (Phoenix doesn't accept OTLP logs).

## Troubleshooting

**"401 Unauthorized in agent logs"**

Basic-auth credentials don't match the root account. Confirm `ZO_ROOT_USER_EMAIL` / `ZO_ROOT_USER_PASSWORD` in `docker-compose/openobserve.yaml` match what you're passing to the plugin.

**"I see `✓ OpenObserve at ...` but no traces appear"**

Check that the URL includes the org segment (`/api/default/v1/traces`, not `/v1/traces`). OpenObserve silently returns 404 on unmatched paths — the plugin has no way to surface that distinction from a regular ingestion failure.

**"Container starts, UI is up, but POSTing traces returns 400"**

Usually the `stream-name` header is missing or malformed. Check with `docker logs hermes-otel-openobserve` — bad stream names produce validation errors there.

**"Data disappears after I recreate the container"**

By default the compose uses a named volume `oo_data`. `docker compose down` preserves it; `docker compose down -v` drops it.

## Production usage

This compose runs OpenObserve in standalone disk mode — everything in the `oo_data` named volume. For production you'd want:

- **Object storage** (S3, GCS, MinIO) for cold tier — configure via `ZO_S3_*` env vars.
- **etcd** for metadata replication across multiple OpenObserve nodes.
- **Postgres** for metadata (optional; alternative to etcd).
- **A reverse proxy** with TLS termination in front of port 5080.

See [OpenObserve's HA + production docs](https://openobserve.ai/docs/ha_and_production/) for the full setup.

## See also

- [OTel logs](/configuration/logs) — the log pipeline that makes trace-id correlation work.
- [Multi-backend fan-out](/backends/multi-backend) — adding OpenObserve alongside other backends.
- [docker-compose/openobserve/README.md](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/openobserve/README.md) — the on-disk README with the same setup walkthrough.
