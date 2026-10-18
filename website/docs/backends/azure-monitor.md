---
sidebar_position: 34
title: "Azure Monitor (via Collector)"
description: "Send Hermes traces, metrics and logs to Azure Monitor / Application Insights through the repo's OpenTelemetry Collector and its azuremonitor target. Generic type: otlp; one hop removed; documented, not verified."
---

# Azure Monitor / Application Insights (via an OpenTelemetry Collector)

Azure Monitor ingests all three OTLP signals and its **Agents (Preview)** view is
built on the OpenTelemetry GenAI semantic conventions hermes-otel emits. What it
does not offer is an OTLP/HTTP endpoint that takes a static header: every
documented route goes through the Azure Monitor OpenTelemetry Distro, the Azure
Monitor Agent, the AKS add-on, or an OpenTelemetry Collector. So there is no
`type: azure`; hermes-otel reaches Azure through the
[OpenTelemetry Collector](/backends/collector) the repo ships, with the generic
[`otlp`](/backends/otlp) type ([#228](https://github.com/briancaffey/hermes-otel/issues/228)).

**Signals:** traces + metrics + logs (through the Collector). **Deployment:**
Azure subscription plus one local container. **Status:** documented, not
verified; this page was written against Microsoft's and the exporter's docs on
2026-10-11 without an Application Insights resource to send to.

## Quick start

1. Create an Application Insights resource and copy its **connection string**
   from the Overview page (Essentials → Connection String). It looks like
   `InstrumentationKey=…;IngestionEndpoint=https://<region>.in.applicationinsights.azure.com/;…`
   and is a secret: anyone holding it can write to the resource.

2. Start the Collector with the `azuremonitor` target:

   ```bash
   COLLECTOR_TARGET=azuremonitor \
   APPLICATIONINSIGHTS_CONNECTION_STRING='InstrumentationKey=…;IngestionEndpoint=https://…' \
     docker compose -f docker-compose/collector/docker-compose.yaml up -d
   curl -s -o /dev/null -w '%{http_code}\n' http://localhost:13133/   # 200
   ```

3. Point the plugin at the Collector:

   ```yaml
   backends:
     - type: otlp
       name: azure-monitor
       endpoint: http://localhost:4448/v1/traces   # /v1/metrics and /v1/logs are derived
       metrics: true                               # the otlp default, spelled out
       logs: true                                  # the otlp default, spelled out
   capture_logs: true                              # log export is off until this is set
   ```

4. Run a Hermes turn, then check in the portal under **Logs**. The exporter
   writes log records to the `traces` table and metrics to `customMetrics`;
   Microsoft's own verification queries read spans from `dependencies`, with
   `cloud_RoleName` derived from the resource's `service.name` (`hermes-agent`
   unless you set `resource_attributes`):

   ```kusto
   union isfuzzy=true dependencies, traces, customMetrics
   | where timestamp > ago(1h)
   | where cloud_RoleName == "hermes-agent"
   | take 50
   ```

## What the target does

`docker-compose/collector/targets/azuremonitor.yaml` replaces the exporter of
all three pipelines with the contrib Collector's `azure_monitor` exporter
(beta for traces, metrics and logs in 0.162.0, the pinned image) and adds the
`cumulative_to_delta` processor to the metrics pipeline:

```yaml
processors:
  cumulative_to_delta:
exporters:
  azure_monitor:
    connection_string: ${env:APPLICATIONINSIGHTS_CONNECTION_STRING}
service:
  pipelines:
    traces:
      exporters: [azure_monitor]
    metrics:
      processors: [memory_limiter, cumulative_to_delta, batch]
      exporters: [azure_monitor]
    logs:
      exporters: [azure_monitor]
```

- The exporter's `connection_string` is the recommended credential; the older
  `instrumentation_key` setting is documented as slated for deprecation. The
  exporter also reads `APPLICATIONINSIGHTS_CONNECTION_STRING` from its own
  environment and gives that variable precedence, so the compose file passes
  it through and the target references it explicitly.
- Microsoft's [coding-agents guide](https://learn.microsoft.com/en-us/azure/managed-grafana/grafana-opentelemetry-app-insights)
  spells the exporter `azuremonitor` and runs `otel/opentelemetry-collector-contrib:latest`;
  the component is `azure_monitor` in the 0.162.0 image this folder pins, which
  is the spelling the image's `validate` command accepts.

## Metrics: delta temporality and exponential histograms

Microsoft's [OTLP ingestion page](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/opentelemetry-protocol-ingestion)
states that "Application Insights experiences, including prebuilt dashboards
and queries, expect and require OTLP metrics with delta temporality and
exponential histogram aggregation", and recommends the `cumulativetodelta`
processor when a Collector receives cumulative metrics. hermes-otel's readers
are cumulative with explicit-bucket histograms by default, so:

- **Temporality** is handled in the Collector by the target's
  `cumulative_to_delta` processor. The alternative is to have the plugin emit
  delta for this entry only, `metrics_temporality: delta` on the `backends:`
  entry, and drop the processor from a copy of the target. Both are untested
  against Azure.
- **Exponential histograms** are not converted by the target. The plugin can
  emit them with the top-level `metrics_histogram: exponential`, but that
  setting applies to every configured backend and several reject exponential
  histograms (Aspire, GreptimeDB; see the
  [metrics table](/backends/overview#metrics-and-your-backend)). Leave it off
  unless Azure is the only metrics backend.
- The statement is made for the native OTLP ingestion route. The
  `azure_monitor` exporter's README documents neither temporality nor histogram
  handling for the `customMetrics` table it writes, so whether explicit-bucket
  histograms render in the prebuilt experiences is unknown.

## The native OTLP route (not used here)

Azure Monitor also has plain OTLP/HTTP endpoints on a Data Collection
Endpoint, one per signal:

```
https://<logs-dce-domain>/dataCollectionRules/<dcr-immutable-id>/streams/Microsoft-OTLP-Traces/otlp/v1/traces
https://<logs-dce-domain>/dataCollectionRules/<dcr-immutable-id>/streams/Microsoft-OTLP-Logs/otlp/v1/logs
https://<metrics-dce-domain>/dataCollectionRules/<dcr-immutable-id>/streams/Custom-Metrics-Otel/otlp/v1/metrics
```

They authenticate with a Microsoft Entra bearer token that the Collector's
`azure_auth` extension (contrib 0.148.0 or later for the current syntax)
obtains from a managed identity or a service principal with the Monitoring
Metrics Publisher role, and the Collector's `otlphttp` exporter attaches it.
hermes-otel cannot mint that token itself, which is why there is no direct
type. If you prefer this route, copy `targets/otlphttp.yaml`, add the
`azure_auth` extension and the three `*_endpoint` settings from Microsoft's
sample, and keep `cumulative_to_delta` on the metrics pipeline. Microsoft
notes the two routes land in the same Application Insights / Log Analytics
tables. Should Microsoft document a static-key OTLP endpoint, this page
becomes a real `type: azure`.

## What Azure makes of Hermes data

- **Agents (Preview)** in Application Insights is "based on OpenTelemetry
  Generative AI Semantics": agent runs, tool calls and models, token usage and
  costs, with a simple end-to-end view. hermes-otel emits `gen_ai.operation.name`
  (`invoke_agent` on the turn, `chat` on LLM and API calls, `execute_tool` on
  tools), `gen_ai.request.model`, `gen_ai.usage.*` and `gen_ai.conversation.id`
  on those spans ([attributes](/architecture/attributes)), which is the input
  that view reads.
  Whether it recognises Hermes spans as agent runs has not been checked.
- **Coding-agent dashboards** (Claude Code, Codex, Copilot, …) in Azure Managed
  Grafana filter on each product's own metric names and `cloud_RoleName`
  (`name startswith "claude_code"`, `cloud_RoleName == "opencode"`), so they
  show nothing for Hermes. Build on the GenAI-based "Agent Framework"
  dashboards or write KQL against `dependencies` / `customMetrics` instead.
- **Two places to look.** A 200 from the Collector is all the plugin ever
  sees (`export azure-monitor: N span(s) -> SUCCESS` in `debug.log`). A wrong
  connection string, blocked egress to `*.in.applicationinsights.azure.com`
  or a throttled ingestion endpoint shows up only in
  `docker logs hermes-otel-collector`, where the exporter retries and
  eventually drops the batch.
- **Cost.** An Azure subscription; the first 5 GB per month of Analytics Logs
  are free on the default plan.

## See also

- [OpenTelemetry Collector](/backends/collector): the folder, its other
  targets (debug, otlphttp, AWS, Google Cloud) and the validate command.
- [Generic OTLP](/backends/otlp) for the `type: otlp` entry.
- [Metrics and your backend](/backends/overview#metrics-and-your-backend) for
  `metrics_temporality` and `metrics_histogram`.
- [Backend quirks](/backends/quirks) for the one-line summary of every backend.
