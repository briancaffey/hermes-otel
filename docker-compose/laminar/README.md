# Laminar

[Laminar](https://github.com/lmnr-ai/lmnr) (Apache-2.0) is an agent-focused
observability platform. This is upstream's "lite" compose (Postgres, ClickHouse,
Quickwit, app-server, frontend) with its `.env` inlined, plus a `quickwit-init`
job that upstream's published images are missing. Plugin type: `otlp` until
`type: laminar` exists (#223).

## Why pick it
- Spans get Laminar's span types (LLM for `api.*`/`llm.*`, TOOL for `tool.*`),
  token counts, cost and model columns.
- Stores logs as well as traces (verified: 40 log records from one turn), more
  than the docs promise.
- Light for a full platform: about 600 MB resident across five containers.

## Start / stop
```bash
docker compose -f docker-compose/laminar/docker-compose.yaml up -d
docker compose -f docker-compose/laminar/docker-compose.yaml down -v
```
UI: http://localhost:5667 — enter any email; the self-hosted local sign-in has no
password.

## Get a project API key without the UI
```bash
export LMNR_PROJECT_API_KEY=$(docker-compose/laminar/mint-api-key.sh)
```
The script signs in through `/api/auth/sign-in/local-email`, creates a
workspace + project when the user has none, and calls `POST /api/cli/api-key`.

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: laminar
    endpoint: http://localhost:8100/v1/traces
    headers:
      Authorization: "Bearer ${LMNR_PROJECT_API_KEY}"
    metrics: false      # answers 200 and drops the payload
capture_logs: true
```

## Verify
```bash
docker exec hermes-otel-laminar-clickhouse clickhouse-client --user ch_user --password ch_passwd \
  -q "SELECT name, span_type, input_tokens, output_tokens, total_cost FROM spans ORDER BY start_time DESC LIMIT 8; SELECT count() FROM logs"
```

## Caveats
- `/v1/metrics` returns 200 and stores nothing (`metrics.rs` is a placeholder),
  so keep `metrics: false` or you will wonder where they went.
- **Published images skip Quickwit index creation**: the frontend logs
  "Quickwit indexes directory not found, skipping index initialization" and
  the app-server then fails every span indexing job with "index `spans_v2` not
  found". `quickwit-init` creates the three indexes from upstream's
  `frontend/lib/quickwit/indexes/`. Trace views read ClickHouse and worked
  either way; the index feeds full-text span search.
- app-server is published on **8100/8101** instead of upstream's 8000/8001
  (8000 is the most contested dev port).
- `LAMINAR_TELEMETRY_DISABLED=true` is set; upstream sends anonymised usage
  telemetry otherwise.
- Images are `latest` (no release tags published for the containers).
