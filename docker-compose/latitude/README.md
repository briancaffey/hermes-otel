# Latitude

[Latitude](https://github.com/latitude-dev/latitude-llm) (MIT) ingests OTLP
traces with a mandatory project header and follows the GenAI semconv
explicitly. This folder wraps upstream's single-host stack (`docker-stack.yml`,
vendored with one edit: `env_file: .env.production` → `latitude.env`) and adds
Mailpit to catch the magic-link sign-in emails. Twelve upstream containers:
web, api, ingest, workers, workflows, a one-shot migrations job, pgvector
Postgres, ClickHouse, two Redis, Temporal, SeaweedFS. Issue #230. Plugin type:
`otlp`.

## Status: not yet run end to end
`docker compose config` resolves all 13 services, but on 2026-10-05 the pull did
not fit the Docker disk available: the application images are about **3 GB
each** (web 3.17 GB, ingest 2.68 GB, api 2.71 GB, plus workers, workflows and
migrations ≈ 15 GB). Budget that, plus several GB of RAM, before starting it.

## Start / stop
```bash
docker compose -f docker-compose/latitude/docker-compose.yaml up -d     # first start: several minutes
docker compose -f docker-compose/latitude/docker-compose.yaml down -v
```
UI: http://localhost:3000 — register with any email, then open the magic link in
Mailpit at http://localhost:8025. API http://localhost:3001, ingest
http://localhost:3002.

## Point hermes-otel at it
Create a project in the UI, then Settings → API keys.
```yaml
backends:
  - type: otlp
    name: latitude
    endpoint: http://localhost:3002/v1/traces
    headers:
      Authorization: "Bearer ${LATITUDE_API_KEY}"
      X-Latitude-Project: <project slug>      # mandatory; spans without a project are rejected
    metrics: false
    logs: false
```

## Caveats
- Traces only (the ingest service has `/v1/traces` and health routes only).
- Ports 3000-3002 are upstream's and 3000 collides with Langfuse and Grafana.
- Secrets in `latitude.env` are upstream's example values; regenerate
  `LAT_MASTER_ENCRYPTION_KEY` and `LAT_BETTER_AUTH_SECRET` (`openssl rand -hex 32`)
  before exposing the stack to anyone.
- Latitude ships its own Hermes plugin (`latitude-telemetry-hermes`); running
  both double-traces every turn.
- Pin `LAT_IMAGE_TAG` in `latitude.env` for anything long-lived.
