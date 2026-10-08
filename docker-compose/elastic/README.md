# Elastic smoke stack

Single-node Elasticsearch + Kibana + EDOT Collector (Elastic Distribution of
the OpenTelemetry Collector), all 9.5.5, every port bound to 127.0.0.1 and
chosen not to collide with the other stacks in this directory.

| Service | Host port | Purpose |
|---|---|---|
| EDOT Collector | 14319 | OTLP/HTTP ingest: `http://127.0.0.1:14319` is the plugin's `endpoint` |
| Elasticsearch | 19201 | REST API, security off (`curl :19201/_cluster/health`) |
| Kibana | 15602 | UI, no login |

```bash
docker compose -p elastic -f docker-compose/elastic/docker-compose.yml up -d
uv run --extra dev python scripts/verify_elastic.py      # export + verify per signal
./scripts/import_elastic_dashboard.sh                    # optional hermes-otel dashboard
docker compose -p elastic -f docker-compose/elastic/docker-compose.yml down -v
```

Plugin entry for it (`$HERMES_HOME/hermes_otel.yaml`):

```yaml
backends:
  - type: elastic
    endpoint: http://127.0.0.1:14319
    dataset: hermes_otel        # -> traces-hermes_otel.otel-<namespace> etc.
    namespace: default
```

Notes:

- Wait for `curl :19201/_cluster/health` to report `yellow` or `green` before
  the first export. The first bulk request creates the index templates and
  data streams; `otel.yaml` gives the exporter a 90 s timeout, retries and a
  queue so a slow start does not drop the batch.
- The compose file disables Elasticsearch's disk watermark: a Docker VM above
  90 % full would otherwise leave every shard unassigned (`red`, nothing
  indexes) with no error on the plugin side.
- Images total about 5.8 GB (Elasticsearch 2.0, Kibana 2.6, collector 1.2).
- `dashboards.ndjson` is a Kibana saved-objects export (two data views plus a
  five-panel dashboard); `dashboard.png` is what it renders.
