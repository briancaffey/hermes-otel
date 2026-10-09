# Sigiro

[Sigiro](https://sigiro.com) is a single-binary OTLP store (DuckLake catalog +
Parquet) with a read-only SQL query API, aimed at agents rather than dashboards.
One container. **Not open source**: the image `ghcr.io/sigiroai/sigiro` is
public, but no source repository or licence was found (2026-10-05). Issue #71.
Plugin type: `otlp`.

## Why pick it
- All three signals, 76 MB resident, nothing to configure.
- SQL over spans, logs and metrics from `curl`; handy for scripted checks.

## Start / stop
```bash
docker compose -f docker-compose/sigiro/docker-compose.yaml up -d
docker compose -f docker-compose/sigiro/docker-compose.yaml down -v
```
No UI. Query API: http://localhost:9999/v1/query.

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: sigiro
    endpoint: http://localhost:4378/v1/traces
capture_logs: true
```

## Verify
```bash
curl -s -X POST http://localhost:9999/v1/query --data-binary \
  "SELECT span_name, count(*) AS n FROM sigiro_spans WHERE service_name = 'hermes-agent' GROUP BY 1 ORDER BY 1"
curl -s -X POST http://localhost:9999/v1/query --data-binary "SELECT metric_name, count(*) FROM sigiro_metrics_histogram GROUP BY 1"
```

## Caveats
- The query API accepts exactly one `SELECT` over an allow-list of tables
  (`sigiro_spans`, `sigiro_logs`, `sigiro_metrics_{gauge,sum,histogram,exp_histogram}`,
  `sigiro_profiles`, `sigiro_anomalies`); no `SHOW TABLES`, no CTEs.
- Unpinned `latest` image, no release tags, no licence. Treat as a free binary.
- OTLP is published on 4377/4378 (not 4317/4318) so it can run next to LGTM.
