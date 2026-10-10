# Elastic smoke stack

Single-node Elasticsearch + Kibana + EDOT Collector (Elastic Distribution of
the OpenTelemetry Collector), all 9.5.5, every port bound to 127.0.0.1 and
chosen not to collide with the other stacks in this directory. Compose names
the project after the folder (`elastic`), so no `-p` flag is needed; the
`backends:` type is `elastic` (see the [Elastic page](https://briancaffey.github.io/hermes-otel/backends/elastic)).

| Service | Host port | Purpose |
|---|---|---|
| EDOT Collector | 14319 | OTLP/HTTP ingest: `http://127.0.0.1:14319` is the plugin's `endpoint` |
| Elasticsearch | 19201 | REST API, security off (`curl :19201/_cluster/health`) |
| Kibana | 15602 | UI, no login |

```bash
docker compose -f docker-compose/elastic/docker-compose.yaml up -d
uv run --extra dev python scripts/verify_elastic.py      # export + verify per signal
./scripts/import_elastic_dashboard.sh                    # optional hermes-otel dashboard
docker compose -f docker-compose/elastic/docker-compose.yaml down -v
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

## Quirks
- `Authorization: ApiKey <key>`, not Bearer, and only on Elastic Cloud; the
  local EDOT Collector takes none, so the plugin omits the header when no key
  resolves and a key alone never opts in (there is no default host).
- Routing is by **resource attribute** (`data_stream.dataset` /
  `data_stream.namespace`, `[a-z0-9_.]` only, `.otel` appended by the
  collector), so it reaches every configured backend and two `elastic` entries
  with different values conflict at init.
- Metrics are sent as **delta** (type preset): Elasticsearch does not handle
  cumulative histograms.
- The one silent failure in this directory: the exporter reports `SUCCESS`
  while a red cluster indexes nothing. Watch `_cluster/health` and the
  collector's `bulk indexer flush error` lines.
- No dashboard adapter; Kibana is the UI.

The cross-backend comparison is in [`../QUIRKS.md`](../QUIRKS.md).
