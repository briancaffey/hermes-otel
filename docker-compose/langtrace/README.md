# Langtrace

[Langtrace](https://github.com/Scale3-Labs/langtrace) (**AGPL-3.0**; last
release 4.0.11 on 2025-04-17, last push 2025-11) ingests OpenTelemetry traces on
a custom path, `/api/trace`. Three containers: the Next.js app, Postgres and
ClickHouse. Issue #224 tracks whether hermes-otel should support it at all;
this folder exists so that decision can be made against a running instance.

## Why pick it
- Simple LLM trace UI with its own `gen_ai.*`-based schema.
- Admin login is enabled, so no sign-up flow.

## Start / stop
```bash
docker compose -f docker-compose/langtrace/docker-compose.yaml up -d
docker compose -f docker-compose/langtrace/docker-compose.yaml down -v
```
UI: http://localhost:3040 — `admin@langtrace.ai` / `langtraceadminpw`.

## Get a project API key
In the UI: create a project, then "Generate API Key". Or with curl and `jq`
(NextAuth credentials login, then the project and key endpoints):
```bash
B=http://localhost:3040; J=$(mktemp)
CSRF=$(curl -s -c $J $B/api/auth/csrf | jq -r .csrfToken)
curl -s -b $J -c $J -o /dev/null -X POST $B/api/auth/callback/credentials \
  --data-urlencode csrfToken=$CSRF --data-urlencode username=admin@langtrace.ai --data-urlencode password=langtraceadminpw --data-urlencode json=true
TEAM=$(curl -s -b $J "$B/api/user?email=admin@langtrace.ai" | jq -r .data.teamId)
PID=$(curl -s -b $J -X POST $B/api/project -H 'content-type: application/json' -d "{\"name\":\"hermes-otel\",\"description\":\"\",\"teamId\":\"$TEAM\",\"type\":\"default\"}" | jq -r .data.id)
curl -s -b $J -X POST "$B/api/api-key?project_id=$PID" | jq -r .data.apiKey
```

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: langtrace
    endpoint: http://localhost:3040/api/trace      # used verbatim; it is not /v1/traces
    headers:
      x-api-key: ${LANGTRACE_API_KEY}
    metrics: false
    logs: false
```

## Verify
```bash
docker exec hermes-otel-langtrace-clickhouse clickhouse-client --user lt_clickhouse_user --password clickhousepw -d langtrace_traces \
  -q "SHOW TABLES"            # one table per project id
```

## Caveats
- **Partial attribute fidelity**: the ingest keeps string and int attribute
  values only. Verified: 40 keys on the `agent` span, token counts intact, no
  bool or double attribute present (consistent with the source reading in
  #224). Only `resourceSpans[0]` of a batch is read.
- The docs say JSON only; the protobuf the plugin sends was accepted (4.0.11).
- The app image is **4.45 GB**; ClickHouse adds 0.5 GB resident.
- Upstream ClickHouse healthcheck used `localhost`, which resolved to `::1`
  inside the container and never passed; this file probes `127.0.0.1`.
- `TELEMETRY_ENABLED=false` is set (upstream ships PostHog analytics on).
