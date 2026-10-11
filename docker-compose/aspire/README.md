# .NET Aspire Dashboard (standalone)

The [Aspire Dashboard](https://aspire.dev/dashboard/standalone/) (MIT, part of
[microsoft/aspire](https://github.com/microsoft/aspire)) is one container that
takes OTLP for **traces, metrics and logs** and shows them within seconds: no
account, no database to run, a GenAI visualizer for `gen_ai.*` spans, and since
13.6 a read API the plugin dashboard uses. Pinned 13.6.1 (2026-10-07). Explicit
plugin type: `aspire` (#369).

## Why pick it
- The fastest "see a Hermes turn" tool there is: `up -d`, export, look. About
  75 MB resident.
- All three signals in one UI, with a GenAI visualizer for chat-completion spans.
- A read API (`/api/telemetry/*`) that returns OTLP JSON, so the plugin
  dashboard reads traces and logs back without a translation layer.

## Start / stop
```bash
docker compose -f docker-compose/aspire/docker-compose.yaml up -d
docker compose -f docker-compose/aspire/docker-compose.yaml down -v
```
UI: http://localhost:18888 (no login: `ASPIRE_DASHBOARD_UNSECURED_ALLOW_ANONYMOUS=true`).
Healthy within ~10 s (`curl -s http://localhost:18888/health` → `Healthy`); the
image has no shell or curl, so Compose cannot health-check it from inside.

## Point hermes-otel at it
```yaml
backends:
  - type: aspire
    endpoint: http://localhost:4418        # OTLP/HTTP (18890 in the container)
    query_port: 18888                      # dashboard read path: the UI port
capture_logs: true
```
The banner reads `✓ Aspire Dashboard at http://localhost:4418/v1/traces`.

## Verify
```bash
curl -s http://localhost:18888/api/telemetry/resources
curl -s 'http://localhost:18888/api/telemetry/traces?resource=hermes-agent&limit=5' | head -c 400
curl -s 'http://localhost:18888/api/telemetry/logs?resource=hermes-agent&limit=3' | head -c 400
```
Verified 2026-10-11 (13.6.1, hermes-otel 1.24.0 branch): one Hermes turn stored
one trace of 7 spans (`agent` root, `llm.*`, three `api.*`, `tool.terminal`,
`tool.read_file`), 28 log records (12 with a `traceId`) and the resource
reported `hasMetrics: true`; every export in the plugin's debug log was a
SUCCESS.

## Caveats
- **Data lives in a SQLite file under the `app` user's home**; the volume is
  mounted on `/home/app` so Docker seeds it with that user's ownership (a
  volume on a fresh path is root-owned and the dashboard dies with `Access to
  the path '/data/resumes' is denied`). `ASPIRE_DASHBOARD_PERSISTENCE_MODE=Resume`
  keeps the data across restarts; the upstream default `None` deletes it on a
  clean shutdown.
- **Exponential histograms are rejected** ("is not supported", counted as
  failures); the plugin's default explicit-bucket histograms are fine. Keep
  `metrics_histogram: explicit`.
- **A resource is one Hermes process** (`service.name` + `service.instance.id`,
  shown as `hermes-agent-<uuid>`); `?resource=hermes-agent` works while one
  process has reported and fails once there are two, so the plugin dashboard
  lists `/api/telemetry/resources` and names every instance.
- **The read API has no metrics route** and no time, attribute, severity or
  scope filters: `resource`, `limit`, `offset` and (on logs) `traceId` and
  `search` are what works on 13.6.1. The plugin dashboard fetches a bounded
  page and applies the window and the filters itself; its Metrics tab says
  "not served" for this source (the Aspire UI shows the metrics).
- Severities arrive in .NET spelling (`Information`, `Warning`, `Critical`);
  the adapter maps them from `severityNumber`.
- Anonymous mode also disables the OTLP and API keys. With
  `DASHBOARD__OTLP__AUTHMODE=ApiKey` set `api_key_env` on the entry
  (`x-otlp-api-key`); with `DASHBOARD__API__AUTHMODE=ApiKey` the adapter's
  `api_key` goes in `x-api-key`.
- Limits (`DASHBOARD__TELEMETRYLIMITS__*`): 100 000 traces and logs, 50 000
  metric points per dimension, 128 attributes per item; the oldest are evicted.
- Upstream calls the dashboard a development tool, not a durable telemetry
  backend. OTLP/gRPC (18889) and the MCP endpoint (18891) are not published.
