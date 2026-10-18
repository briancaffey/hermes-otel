# OpenTelemetry Collector (generic, in front of the plugin)

One `otel/opentelemetry-collector-contrib` container (0.162.0, 482 MB image,
~40 MB resident) that receives the plugin's OTLP/HTTP and sends it on. It is
for two things the plugin alone cannot do (#368):

- **Backends with signed or exchanged auth.** Azure Monitor wants a Microsoft
  Entra bearer token, AWS wants SigV4, Google Cloud wants OAuth; a static-header
  OTLP exporter cannot produce any of them. The Collector's exporters can.
- **Seeing exactly what the plugin sends** with nothing else running: the
  default target prints every span, metric and log record to `docker logs`.

Parseable OSS already uses the same pattern (`parseable/`), because it refuses
protobuf; this folder makes "Collector in front" a supported, tested path.
Only the OTLP/HTTP receiver is published; the plugin's `protocol: grpc` has no
port to reach here.

## Start / stop
```bash
docker compose -f docker-compose/collector/docker-compose.yaml up -d      # COLLECTOR_TARGET=debug
docker logs -f hermes-otel-collector
docker compose -f docker-compose/collector/docker-compose.yaml down
```
Health: `curl -s -o /dev/null -w '%{http_code}' http://localhost:13133/` → `200`
(the image is distroless, so there is no in-container healthcheck). Ready
about a second after start (`Everything is ready` in the logs).

Ports (host side): **4448** OTLP/HTTP (`COLLECTOR_OTLP_PORT`, upstream 4318),
**13133** health (`COLLECTOR_HEALTH_PORT`).

## Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: collector
    endpoint: http://localhost:4448/v1/traces   # /v1/metrics and /v1/logs are derived
    metrics: true
    logs: true
capture_logs: true
```
The plugin is now one hop removed from the backend: its `debug.log` shows
`export collector: N span(s) -> SUCCESS` for the Collector's 200, whatever
the target does afterwards. Target-side failures show up in
`docker logs hermes-otel-collector` only.

## Targets

`COLLECTOR_TARGET` picks one file under `targets/`, merged on top of
`otelcol-config.yaml` as a second `--config` flag (later files replace each
pipeline's exporter list). All five validate with the image's
`validate` command; the first two are verified end to end, the three cloud
ones are documented and need an account.

| `COLLECTOR_TARGET` | Exporters (stability in 0.162.0) | Credentials from the environment | Status |
|---|---|---|---|
| `debug` (default) | `debug` for all three signals; `COLLECTOR_DEBUG_VERBOSITY` = `basic` / `normal` / `detailed` | none | verified: one turn printed `Traces` batches of 4 + 1 spans, a `Metrics` batch of 24 metrics and `Logs` batches with the resource, scope and body of every record |
| `otlphttp` | `otlp_http` to `OTLP_TARGET_ENDPOINT` (base URL; `/v1/<signal>` appended) | none; copy the file to add a header (`headers: {Authorization: "Bearer ${env:MY_TOKEN}"}`) | verified into `otel-desktop-viewer/` at `http://host.docker.internal:4428`: the viewer gained 1 trace / 5 spans, 26 log records and 74 metric data points from one turn, with 12 `SUCCESS` export lines and no errors on either side |
| `azuremonitor` (#228, [docs page](https://briancaffey.github.io/hermes-otel/backends/azure-monitor)) | `azure_monitor` (beta: traces, metrics, logs) with `cumulative_to_delta` on the metrics pipeline | `APPLICATIONINSIGHTS_CONNECTION_STRING` | documented, not verified (needs an Azure subscription) |
| `aws` | `awsxray` (beta, traces), `awsemf` (beta, metrics as Embedded Metric Format), `awscloudwatchlogs` (alpha, logs) | `AWS_REGION`, `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`, `AWS_LOG_GROUP` (default `hermes-otel`) | documented, not verified (needs an AWS account) |
| `googlecloud` | `googlecloud` (beta: traces, metrics, logs) | `GOOGLE_CLOUD_PROJECT`, `GCP_CREDENTIALS_FILE` (a service-account JSON mounted into the container) | documented, not verified (needs a GCP project) |

```bash
# forward to another stack in this directory (the viewer here)
COLLECTOR_TARGET=otlphttp OTLP_TARGET_ENDPOINT=http://host.docker.internal:4428 \
  docker compose -f docker-compose/collector/docker-compose.yaml up -d

# Azure Monitor / Application Insights
COLLECTOR_TARGET=azuremonitor APPLICATIONINSIGHTS_CONNECTION_STRING='InstrumentationKey=…;IngestionEndpoint=https://…' \
  docker compose -f docker-compose/collector/docker-compose.yaml up -d

# check a target's syntax without starting it
docker run --rm -v "$PWD/docker-compose/collector/otelcol-config.yaml:/etc/otelcol/config.yaml:ro" \
  -v "$PWD/docker-compose/collector/targets:/etc/otelcol/targets:ro" \
  -e OTEL_BIND_HOST=0.0.0.0 -e COLLECTOR_DEBUG_VERBOSITY=normal -e AWS_REGION=us-east-1 -e AWS_LOG_GROUP=hermes-otel \
  otel/opentelemetry-collector-contrib:0.162.0 validate --config=/etc/otelcol/config.yaml --config=/etc/otelcol/targets/aws.yaml
```

## Caveats
- **Temporality.** The plugin exports cumulative metrics. Application Insights
  expects delta, so the `azuremonitor` target converts with
  `cumulative_to_delta`; the `aws` and `googlecloud` targets take cumulative
  as the exporters document. Datadog has the same requirement if you add a
  target for it.
- **Two places to look.** A 200 from the Collector is all the plugin ever
  sees. Exporter errors (bad connection string, expired AWS credentials,
  a target that is down) are in the container's logs, where the exporter
  retries with backoff and eventually drops the batch.
- **Env substitution is for values.** `${env:X}` works in a header's value, not
  in its name; a target that needs a custom header name is a copied file.
- `host.docker.internal` is how the container reaches the other stacks on this
  machine (Docker Desktop); on Linux add `extra_hosts: ["host.docker.internal:host-gateway"]`.
- `service.telemetry.metrics.level: none` keeps the Collector's own metrics
  off; `memory_limiter` is set to 256 MB, generous for one agent.
- Ports 4448 and 13133 collide with nothing else in this directory.
