---
sidebar_position: 33
title: "OpenTelemetry Collector"
description: "A generic contrib Collector in front of hermes-otel for backends with signed or exchanged auth (Azure Monitor, AWS, Google Cloud) and for printing exactly what the plugin sends. Generic type: otlp."
---

# OpenTelemetry Collector (in front of the plugin)

hermes-otel exports OTLP/HTTP with static headers. Some destinations cannot be
reached that way: Azure Monitor wants a Microsoft Entra bearer token, AWS wants
SigV4 signatures, Google Cloud wants OAuth. The answer is the same for all of
them: run an [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/)
next to Hermes, point the plugin at it with the generic [`otlp`](/backends/otlp)
type, and let the Collector's exporter do the auth. The repo ships one such
Collector as `docker-compose/collector/` with a target per destination, plus a
default target that prints every span, metric and log record to `docker logs`.
Issue #368.

```bash
docker compose -f docker-compose/collector/docker-compose.yaml up -d   # COLLECTOR_TARGET=debug
docker logs -f hermes-otel-collector
```

```yaml
backends:
  - type: otlp
    name: collector
    endpoint: http://localhost:4448/v1/traces   # /v1/metrics and /v1/logs are derived
    metrics: true
    logs: true
capture_logs: true
```

## Targets

`COLLECTOR_TARGET` picks a file under `docker-compose/collector/targets/`,
merged on top of the base config as a second `--config` flag. Credentials come
from the environment; the compose header lists every variable.

| Target | Where the signals go | Credentials | Status (2026-10-11, contrib 0.162.0) |
|---|---|---|---|
| `debug` (default) | the container's stdout, `COLLECTOR_DEBUG_VERBOSITY` = `basic` / `normal` / `detailed` | none | verified with one Hermes turn: `Traces` batches of 4 + 1 spans, a `Metrics` batch of 24 metrics, `Logs` batches with each record's resource, scope and body |
| `otlphttp` | any OTLP/HTTP backend at `OTLP_TARGET_ENDPOINT` (one of the repo's stacks via `host.docker.internal`, or a vendor) | none; copy the file to add a header | verified into [otel-desktop-viewer](/backends/viewers): one turn added 1 trace / 5 spans, 26 log records and 74 metric data points, no errors on either side |
| `azuremonitor` | [Azure Monitor / Application Insights](/backends/azure-monitor) via `azure_monitor` (beta: traces, metrics, logs), with `cumulative_to_delta` on metrics | `APPLICATIONINSIGHTS_CONNECTION_STRING` | documented, not verified (needs an Azure subscription; #228) |
| `aws` | X-Ray (`awsxray`, beta), CloudWatch metrics as EMF (`awsemf`, beta), CloudWatch Logs (`awscloudwatchlogs`, alpha) | `AWS_REGION`, the SDK's key variables, `AWS_LOG_GROUP` | documented, not verified (needs an AWS account) |
| `googlecloud` | Cloud Trace, Monitoring and Logging via `googlecloud` (beta) | `GOOGLE_CLOUD_PROJECT`, `GCP_CREDENTIALS_FILE` | documented, not verified (needs a GCP project) |

All five configurations pass the image's `validate` command; the folder's
[README](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/collector/README.md)
has the command.

## What to know

- **One hop removed.** The plugin's `debug.log` records the Collector's 200
  (`export collector: N span(s) -> SUCCESS`) whatever happens next. Exporter
  errors, retries and drops are in `docker logs hermes-otel-collector` only.
- **Temporality.** The plugin emits cumulative metrics. Application Insights
  expects delta, so the `azuremonitor` target converts; AWS and Google Cloud
  take cumulative. Datadog (#226) has the same requirement if you add a target.
- **Env substitution is for values.** `${env:X}` works in a header's value, not
  its name; a target that needs a custom header name is a copied file.
- Ports **4448** (OTLP/HTTP) and **13133** (health) collide with nothing else in
  the repo. The image is distroless: check health from the host.

## When not to use it

Every backend with its own page here takes the plugin's OTLP/HTTP directly, and
the explicit types ([overview](/backends/overview)) add the headers, paths and
signal defaults each one needs. A token that merely expires (a Google Cloud access
token, a vault-issued OIDC token, a rotated key) no longer needs it either: a
backend entry's [`headers_helper`](/configuration/short-lived-credentials) runs a
command that mints the headers and re-runs it on a 401/403. Reach for the
Collector when the destination needs a signature over the request (AWS SigV4),
when you need processing the plugin does not do
(sampling, redaction beyond `content_capture`, temporality conversion), or when
you want to see the raw export without a backend at all.
