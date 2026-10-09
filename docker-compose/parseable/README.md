# Parseable OSS (behind a collector)

[Parseable](https://www.parseable.com) OSS (AGPL-3.0) stores logs, traces and
metrics as Parquet with a SQL query API and an Agents view. Two containers:
Parseable in standalone `local-store` mode and an OpenTelemetry Collector that
converts the plugin's OTLP/protobuf to OTLP/JSON, adds HTTP Basic auth and the
`X-P-Stream` / `X-P-Log-Source` dataset headers. The collector is **not
optional** (see caveats). Plugin type: `otlp`, pointed at the collector.

## Why pick it
- All three signals in one light store (about 190 MB resident for both
  containers), SQL over everything, datasets created on first ingest.
- Shows what the plugin's `type: parseable` would deliver on Parseable Cloud,
  without an account.

## Start / stop
```bash
docker compose -f docker-compose/parseable/docker-compose.yaml up -d
docker compose -f docker-compose/parseable/docker-compose.yaml down -v
```
UI: http://localhost:8010 — `admin` / `admin`.

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: parseable
    endpoint: http://localhost:4348/v1/traces     # the collector, not Parseable
capture_logs: true
```

## Verify
```bash
for s in hermes-traces hermes-metrics hermes-logs; do
  curl -s -u admin:admin http://localhost:8010/api/v1/logstream/$s/stats | jq .ingestion.count
done
```
Then UI → Datasets → `hermes-traces` (Explore), or the Traces / Agents views.

## Caveats
- **Do not use `type: parseable` against OSS.** OSS has no API keys (any
  `X-API-Key` header is rejected with 401) and answers `400 Protobuf ingestion is
  not supported in Parseable OSS`; the plugin's exporters are protobuf-only.
  `type: parseable` is for Parseable Cloud/Enterprise (#238).
- Credentials are duplicated: `P_USERNAME`/`P_PASSWORD` on the server and
  `PARSEABLE_BASIC_AUTH` (base64 `user:password`) on the collector; change both.
- Image lives on `quay.io/parseablehq/parseable` (the Docker Hub repo stopped at
  v2.8), pinned to v3.2.4.
