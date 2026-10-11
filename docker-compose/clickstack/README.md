# ClickStack (HyperDX)

[ClickStack](https://clickhouse.com/docs/use-cases/observability/clickstack)
(MIT) is ClickHouse's open-source observability stack: ClickHouse, an
OpenTelemetry Collector, MongoDB and the HyperDX UI, here as the one
`clickhouse/clickstack-all-in-one` image (2.40.0 on 2026-10-11). It stores
**all three signals** in the standard OTel ClickHouse tables (`otel_traces`,
`otel_logs`, `otel_metrics_*`). Explicit plugin type: `clickstack` (#365).

## Why pick it
- The most prominent open-source ClickHouse stack, one container, about
  1.1 GB resident.
- Traces, metrics and logs with a Lucene-style search UI, and the dashboard
  adapter reads all three back straight from ClickHouse.
- Standard tables: anything written for the OTel ClickHouse exporter (OpenLIT,
  SigNoz-style SQL) works here too.

## Start / stop
```bash
docker compose -f docker-compose/clickstack/docker-compose.yaml up -d      # healthy in ~30 s
export OTEL_CLICKSTACK_API_KEY="$(docker-compose/clickstack/mint-api-key.sh)"
docker compose -f docker-compose/clickstack/docker-compose.yaml down -v
```
UI: http://localhost:3050. There is **no default login**: the first visit (or
`mint-api-key.sh`) creates the account. The script registers
`hermes@example.com` / `HermesOtel-local-1` (override with `CLICKSTACK_EMAIL`
/ `CLICKSTACK_PASSWORD`; the password must mix upper and lower case), reads
the team's ingestion key from the all-in-one's MongoDB, and waits until the
Collector's OTLP receivers are up. Idempotent: with a team already there it
just prints the key. In the UI the same key is under Team Settings → API Keys.

## Point hermes-otel at it
```yaml
backends:
  - type: clickstack
    endpoint: http://localhost:4408            # the Collector's OTLP/HTTP (4318 in the container)
    api_key_env: OTEL_CLICKSTACK_API_KEY       # bare `authorization: <key>` header
    # dashboard read path: ClickHouse over HTTP, the bundled api/api user
    query_port: 8124
    user: api
    password: api
    ui_url: http://localhost:3050              # "open in" link to HyperDX search
capture_logs: true
```
The banner reads `✓ ClickStack at http://localhost:4408/v1/traces`. Without a
key the entry is skipped at startup (every batch would be a 401).

## Verify
```bash
curl -s -u api:api 'http://localhost:8124/?default_format=TSV' --data-binary \
  "SELECT SpanName, ParentSpanId, Duration FROM default.otel_traces ORDER BY Timestamp"
curl -s -u api:api 'http://localhost:8124/' --data-binary "SELECT count(), countIf(TraceId != '') FROM default.otel_logs"
curl -s -u api:api 'http://localhost:8124/' --data-binary "SELECT MetricName, count() FROM default.otel_metrics_sum GROUP BY MetricName"
```
Verified 2026-10-11 (all-in-one 2.40.0, ClickHouse 26.8, hermes-otel 1.24.0
branch): one Hermes turn stored 7 spans (`agent` root with an empty
`ParentSpanId`, `llm.*`, three `api.*`, `tool.terminal`, `tool.read_file`),
28 log rows (12 with a `TraceId`), 6 sum metrics (`hermes.token.usage`,
`hermes.model.usage`, …) and 7 histograms (`hermes.tool.duration`,
`gen_ai.client.token.usage`, …) under their dotted OTel names. The
dashboard-verify harness passed every check (see the docs page).

## Caveats
- **Ingest needs the team's API key**, sent as a bare `authorization: <key>`
  header (a `Bearer` prefix is accepted too); without it the Collector answers
  401, with a wrong key `provided authorization does not match expected scheme
  or token`. The receivers on 4317/4318 only start once a team exists: the
  Collector gets its config from the API over OpAMP.
- **The bundled `default` ClickHouse user only answers from inside the
  container** (`users.d/default-user.xml` pins it to localhost). From the host
  use `api` / `api` (or `worker` / `worker`), which the image defines with
  open networks. Local defaults; change them before exposing the port.
- Upstream keeps ClickHouse unpublished and the UI on 8080, OTLP on 4317/4318;
  this folder remaps to 3050 / 4407 / 4408 / 8124 so the stack coexists with
  LGTM, Jaeger and the Uptrace ClickHouse.
- Every attribute value is a **string** in the `Map(String, String)` columns;
  the adapter coerces numbers and booleans back on read.
- Metrics keep their **dotted** OTel names (`hermes.token.usage`, not
  `hermes_token_usage_total`); histograms are one table with `Count` and `Sum`.
  The Collector's own scrape metrics (`otelcol_*`, `up`, `scrape_*`) land in
  `otel_metrics_gauge` next to yours; the adapter lists only the plugin's
  namespaces by default (`metrics_match`).
- The Collector lower-cases `SeverityText` (`info`, `warn`); the adapter
  upper-cases it again.
- Retention: 3 days by default (`CLICKSTACK_TABLES_TTL`). Four processes in one
  container, which upstream calls fine for a laptop and wrong for production.
- Image size 2.6 GB.
