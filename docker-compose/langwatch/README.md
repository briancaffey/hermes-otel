# LangWatch

[LangWatch](https://github.com/langwatch/langwatch) (Apache-2.0) is an LLM
observability and evaluation platform whose OTLP routes cover **traces, metrics
and logs** under `/api/otel`. Trimmed from upstream's `infra/compose.yml` to the
app, the workers, Postgres, Redis and upstream's ClickHouse image; the NLP and
evaluator services are left out. Plugin type: `langwatch` (since hermes-otel 1.23,
#229).

## Why pick it
- All three signals stored (verified in ClickHouse: `stored_spans`,
  `log_records`, `metric_data_points`).
- Trace view with input, output, thread id (= Hermes session) and token totals;
  an onboarding option specifically for "AI coding agents".
- Tracing works without the NLP/evaluator containers upstream runs.

## Start / stop
```bash
docker compose -f docker-compose/langwatch/docker-compose.yaml up -d     # ≈ 5 min to healthy
docker compose -f docker-compose/langwatch/docker-compose.yaml down -v
```
UI: http://localhost:5560. Health: `curl -i localhost:5560/api/health` returns
**204** once the ClickHouse migrations are done.

## Get a project API key
Sign up (any email + password), finish the onboarding wizard (organisation →
starting point → it creates the first project), then Settings → API keys. Or
let Playwright do the wizard and print the key:
```bash
export LANGWATCH_API_KEY=$(uv run --with playwright python docker-compose/langwatch/mint-api-key.py)
```
(`uv run --with playwright python -m playwright install chromium` once.) The
same key is readable from Postgres once the project exists:
```bash
docker exec hermes-otel-langwatch-postgres psql -U prisma -d mydb -At -c 'select "apiKey" from mydb."Project"'
```

## Point hermes-otel at it
```yaml
backends:
  - type: langwatch
    endpoint: http://localhost:5560
    api_key_env: LANGWATCH_API_KEY      # sk-lw-…
capture_logs: true
```

## Verify
```bash
NOW=$(date +%s000); AUTH="X-Auth-Token: $LANGWATCH_API_KEY"
curl -s -H "$AUTH" -X POST http://localhost:5560/api/traces/search \
  -H 'content-type: application/json' -d "{\"pageSize\":3,\"startDate\":$((NOW-3600000)),\"endDate\":$NOW}" | head -c 800
docker exec hermes-otel-langwatch-clickhouse clickhouse-client --password langwatch -d langwatch \
  -q "select table, sum(rows) from system.parts where database='langwatch' and active group by table"
```

## Caveats
- **Slow first start**: about five minutes of ClickHouse migrations before the
  app answers; `docker compose ps` shows it `Up` the whole time.
- The root's token roll-up is not sent to LangWatch (`root_usage: false` is
  the preset for this type since 1.24, #327): LangWatch sums every span, same
  as Opik, so trace-level `prompt_tokens` used to double (48 598 for a turn
  whose API calls total 24 299).
- Heavy: about 1.9 GB resident (app ≈ 0.75 GB, workers ≈ 0.65 GB, ClickHouse
  ≈ 0.5 GB, capped at 2 GB) and a 2.8 GB app image; upstream states 4 CPU /
  8 GB for the full stack.
- Evaluations, topic clustering and the optimisation studio need the dropped
  `langwatch_nlp` / `langevals` services.
- `latest` image tag (upstream publishes digests only).
