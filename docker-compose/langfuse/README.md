# Langfuse

[Langfuse](https://langfuse.com) (MIT core, some EE folders) is the most
polished LLM tracing UI of the self-hostable set: sessions, generations, cost
attribution, prompt management. This stack is upstream's self-host compose
(web, worker, Postgres, Redis, ClickHouse, MinIO from Chainguard's image, since MinIO no longer publishes to Docker Hub or quay.io) with headless init that seeds
an org, a project and API keys. Explicit plugin type: `langfuse`.

## Why pick it
- Generation/observation view with token and cost columns per `api.*` span.
- Pre-seeded keys, so no clicking before the first trace.
- Same file runs **v3 or v4** (`LANGFUSE_VERSION`), which matters for the
  dashboard adapter (#246).

## Start / stop
```bash
docker compose -f docker-compose/langfuse/docker-compose.yaml up -d          # v3 (default)
LANGFUSE_VERSION=4 docker compose -f docker-compose/langfuse/docker-compose.yaml up -d
docker compose -f docker-compose/langfuse/docker-compose.yaml down -v
```
Healthy about 20-60 s after `up`. UI: http://localhost:3000 — `test@test.com` /
`testpassword123`. Use `down -v` when switching between 3 and 4; they do not
share data.

## Point hermes-otel at it
```yaml
backends:
  - type: langfuse
    public_key: lf_pk_hermes_dev
    secret_key: lf_sk_hermes_dev
    endpoint: http://localhost:3000/api/public/otel/v1/traces
```

## Verify
```bash
# v3
curl -s -u lf_pk_hermes_dev:lf_sk_hermes_dev 'http://localhost:3000/api/public/traces?limit=2'
# v4 (events_only mode)
curl -s -u lf_pk_hermes_dev:lf_sk_hermes_dev 'http://localhost:3000/api/public/v2/observations?limit=3'
```

## Caveats
- Traces only; the `langfuse` type turns metrics and logs off.
- **v4** removes `/api/public/traces`, `/api/public/observations` and
  `/api/public/sessions` (404 "not available … in Langfuse v4 events_only
  mode"); `/api/public/v2/observations` works. The dashboard's Langfuse adapter
  probes for this and reads v4 through `/api/public/v2/observations` plus
  `/api/public/v2/metrics` (`query_api: v3|v4` pins it, #246).
- Port 3000 collides with Grafana (LGTM stack) and Latitude's web UI.
- Heavy: about 3 GB resident for the six containers (web ≈ 1.1 GB, worker
  ≈ 1.2 GB, ClickHouse ≈ 0.6 GB) and 3.6 GB of images for v4.
- Traces appear in the API 10-20 s after export (async ingestion through S3/
  MinIO and the worker).
