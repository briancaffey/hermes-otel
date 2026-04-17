# SigNoz (self-hosted) for hermes-otel

[SigNoz](https://signoz.io) is an open-source observability backend that speaks
plain OTLP and stores traces, metrics, and logs in ClickHouse. This directory
contains a self-contained copy of the upstream Docker Compose stack, re-packaged
so it can run from the plugin repo without cloning `SigNoz/signoz`.

## Layout

```
docker-compose/signoz/
├── docker-compose.yaml               # service definitions (ports remapped — see below)
├── otel-collector-config.yaml        # required: otel-collector pipeline
├── common/                           # files copied from SigNoz repo's deploy/common/
│   ├── clickhouse/
│   │   ├── config.xml
│   │   ├── users.xml
│   │   ├── cluster.xml
│   │   ├── custom-function.xml
│   │   └── user_scripts/histogramQuantile
│   └── signoz/
│       └── otel-collector-opamp-config.yaml
└── README.md
```

The upstream stack references `../common/`; this repo inlines those files as
`./common/` so the compose project is self-contained. If you want to rebase on
a newer SigNoz release, copy the matching `deploy/common/` tree from
[SigNoz/signoz](https://github.com/SigNoz/signoz) on top of `./common/`.

## Ports — upstream vs. this repo

Upstream SigNoz binds a few ports that commonly collide with other dev
services. This compose file remaps them by default (override with env vars):

| Service              | Upstream | This repo (default) | Env var                   |
|----------------------|----------|---------------------|---------------------------|
| SigNoz UI / API      | `8080`   | `3301`              | `SIGNOZ_UI_PORT`          |
| OTLP gRPC receiver   | `4317`   | `4327`              | `SIGNOZ_OTLP_GRPC_PORT`   |
| OTLP HTTP receiver   | `4318`   | `4328`              | `SIGNOZ_OTLP_HTTP_PORT`   |

### Why the remap

- **`8080`** is the single most-contested port on a developer laptop (Java dev
  servers, Spring Boot, Tomcat, Jenkins, old proxies, etc.). Moved to `3301`,
  which is SigNoz's historical UI port and unlikely to conflict.
- **`4317` / `4318`** are the OpenTelemetry OTLP standard ports, so **any other
  OTel collector on the host will conflict**, including the Phoenix container
  in `docker-compose/phoenix.yaml` (it binds `4317`). Moving SigNoz's OTLP
  receivers up by ten lets both stacks run side by side, which is useful when
  comparing backends.

### Overriding

Set any of the env vars above in the shell or in a `.env` file next to
`docker-compose.yaml` before `docker compose up`:

```bash
SIGNOZ_UI_PORT=8080 docker compose -f docker-compose/signoz/docker-compose.yaml up -d
```

## Running the stack

From the plugin root:

```bash
# Start
docker compose -f docker-compose/signoz/docker-compose.yaml up -d

# Wait ~30–60 s for ClickHouse migrations + SigNoz bootstrap
docker compose -f docker-compose/signoz/docker-compose.yaml ps

# UI: http://localhost:3301
# OTLP HTTP (point hermes-otel here): http://localhost:4328

# Stop + remove volumes
docker compose -f docker-compose/signoz/docker-compose.yaml down -v
```

First-run startup downloads the ClickHouse, ZooKeeper, SigNoz, and
signoz-otel-collector images (~2 GB) and runs an `init-clickhouse` job that
fetches the `histogram-quantile` binary.

## First-run setup — **required before any OTLP data is accepted**

This one bit us, so it's worth calling out explicitly.

SigNoz ships with opamp-managed config for the otel-collector. On a fresh
volume, `signoz` refuses to push a real pipeline to `signoz-otel-collector`
until an admin account exists — instead it pushes a pipeline where every
exporter/receiver is `nop`. Symptom: all container healthchecks look green,
but OTLP clients see `Connection reset by peer` on `:4328` / `:4327`, the
collector logs spam `Server returned an error response` from opamp every
30 s, and `/var/tmp/collector-config.yaml` inside the collector container
shows `receivers: [nop]` / `exporters: [nop]` on every pipeline.

Fix: complete the first-run setup **once**, and SigNoz will push the real
config (clickhousetraces, signozclickhousemetrics, clickhouselogsexporter,
etc.) to the collector, which then binds the OTLP ports.

### Check whether setup is done

```bash
curl -s http://localhost:3301/api/v1/version
# {"version":"v0.119.0","ee":"Y","setupCompleted":false}   <-- needs setup
# {"version":"v0.119.0","ee":"Y","setupCompleted":true}    <-- good to go
```

### Option A — browser (recommended)

Open <http://localhost:3301>, pick any email/name, and set a password. That's
it; SigNoz registers it as the root admin.

### Option B — headless curl (useful for fresh machines / CI)

Passwords must be ≥12 chars with upper / lower / digit / symbol, or the API
rejects them.

```bash
curl -sS -X POST http://localhost:3301/api/v1/register \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "admin",
    "email": "admin@signoz.local",
    "password": "HermesOtel#local-2026",
    "orgName": "hermes-otel",
    "isAnonymous": false,
    "hasOptedUpdates": false
  }'
```

A successful response looks like `{"status":"success","data":{...,"isRoot":true}}`.

### Login credentials for the UI

Whatever you register in Option A/B is your login:

| Field    | Example (from Option B)     |
|----------|------------------------------|
| URL      | `http://localhost:3301`     |
| Email    | `admin@signoz.local`        |
| Password | `HermesOtel#local-2026`     |

There is no default admin — if you forget the password on a local-only stack,
the fastest reset is `docker compose down -v` (wipes the `signoz-sqlite`
volume where the user table lives) and re-register.

### Confirm the collector flipped out of no-op mode

After setup, give opamp ~15 s, then check:

```bash
docker exec signoz-otel-collector \
  awk '/traces:/,/^[^ ]/' /var/tmp/collector-config.yaml | head
# Expect: exporters: [clickhousetraces, ...]   (NOT [nop])
```

## Wiring hermes-otel → this stack

Once setup is complete, put this in `~/.hermes/.env` (the file hermes-agent
loads at startup):

```bash
# SigNoz self-hosted (this docker-compose stack)
OTEL_SIGNOZ_ENDPOINT=http://localhost:4328/v1/traces
OTEL_PROJECT_NAME=hermes-agent
```

Make sure none of the higher-priority backends are also configured in the
same file — the plugin's detection order is LangSmith > Langfuse > SigNoz >
Phoenix, so comment out `LANGSMITH_*`, `OTEL_LANGFUSE_*`, and `LANGFUSE_*`
vars if you want hermes-otel to pick SigNoz.

Restart `hermes gateway` and confirm:

```
[hermes-otel] ✓ SigNoz at http://localhost:4328/v1/traces
```

The plugin sends both traces and metrics to the SigNoz otel-collector over
OTLP/HTTP. No ingestion key is required for the self-hosted stack.

### SigNoz Cloud

For **SigNoz Cloud** (no local compose needed), use the regional ingest URL
plus the ingestion key from the cloud UI:

```bash
OTEL_SIGNOZ_ENDPOINT=https://ingest.us.signoz.cloud:443/v1/traces
OTEL_SIGNOZ_INGESTION_KEY=sz-...
OTEL_PROJECT_NAME=hermes-agent
```

The plugin sets the `signoz-ingestion-key` header automatically on both the
trace and metric exporters.

## Notes

- The stack includes a one-shot `signoz-telemetrystore-migrator` container that
  bootstraps ClickHouse schemas. It is expected to exit after migrations
  complete — don't treat its `Exited` status as an error.
- SigNoz provisions a default admin on first boot via the UI setup flow at
  `http://localhost:3301`. The JWT secret is pinned to `secret` in the compose
  file for local use — do not expose this stack to the public internet.
- `VERSION` and `OTELCOL_TAG` env vars pin the SigNoz image and collector
  versions (`v0.119.0` / `v0.144.2` at time of import).
