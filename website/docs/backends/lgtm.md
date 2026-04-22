---
sidebar_position: 8
title: "Grafana LGTM"
description: "Grafana's Loki + Grafana + Tempo + Mimir stack in one container — all three OTel signals (traces, metrics, logs) with a single docker compose command."
---

# Grafana LGTM

[Grafana LGTM](https://github.com/grafana/docker-otel-lgtm) is the "all signals in one container" distribution — Grafana + Loki (logs) + Tempo (traces) + Mimir (Prometheus-compatible metrics) + an OTel Collector, bundled into `grafana/otel-lgtm`. It's the only first-class backend that covers **all three OTel signals** from a single image, which is why it's the recommended stack for local development and quickstarts.

**Signals:** traces + metrics + logs. **Deployment:** local (single container) or you assemble the pieces in production. **Cost:** OSS.

## One-command quickstart

A ready-to-use compose file ships with the plugin:

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -p lgtm -f docker-compose/lgtm.yaml up -d
```

Wait ~30s for everything to come up, then open Grafana at http://localhost:3000 (admin / admin). Three datasources are pre-provisioned: Tempo, Prometheus (pointed at Mimir), and Loki.

Point the plugin at it — minimal `config.yaml`:

```yaml
backends:
  - type: lgtm
    endpoint: http://localhost:4318/v1/traces

capture_logs: true   # optional — ship Python logs to Loki with trace-id correlation
log_level: INFO
```

Restart hermes-agent and you'll see all three signals flow in.

## Why a dedicated `type: lgtm`

Functionally `type: lgtm` is an alias over `type: otlp` — both point at a collector that accepts OTLP on 4318. The dedicated type exists for two reasons:

1. **Self-documenting config.** `type: lgtm` tells the next reader exactly what stack this entry targets, instead of a bare `type: otlp`.
2. **Startup logs say "LGTM".** The fan-out status line reads `✓ LGTM at http://localhost:4318/v1/traces` instead of `✓ OTLP at ...`.

Defaults (both on): `supports_metrics=True`, `supports_logs=True`. Override with `metrics: false` or `logs: false` per-backend if needed.

:::caution
**Don't use `type: tempo` for this stack.** That type is for *standalone* Tempo (traces only) and will refuse to fan out logs or metrics even when pointed at the LGTM collector's endpoint. Use `type: lgtm` (or `type: otlp`) to unlock all three signals.
:::

## Ports

| Port | Service | Purpose |
|---|---|---|
| 3000 | Grafana | UI (admin / admin) |
| 3100 | Loki | OTLP/HTTP log writes from the internal collector |
| 3200 | Tempo | Tempo's HTTP query API (Grafana talks here) |
| 4317 | Collector | OTLP gRPC receiver |
| 4318 | Collector | **OTLP HTTP receiver — the plugin exports here** |
| 9090 | Mimir | Prometheus-compatible UI + OTLP write endpoint |

Port 3000 collides with Phoenix and self-hosted Langfuse. Port 4318 collides with Jaeger and SigNoz's HTTP receiver. Bring the LGTM container up on its own or remap the conflicts — the plugin's `docker-compose/all.sh` intentionally **skips** LGTM for the "bring up everything" flow because of these conflicts.

## Spanmetrics — RED metrics for free

The bundled collector config at `docker-compose/lgtm/otelcol-config.yaml` enables the **`spanmetrics` connector**. The collector derives Prometheus histograms (`traces_spanmetrics_calls_total`, `traces_spanmetrics_latency_bucket`) from every incoming trace — you get rate/errors/duration metrics on Mimir without the plugin having to emit them from Python.

Dimensions are picked from attributes the plugin actually emits:

- `llm.provider` — split RED by model provider
- `llm.model_name` — split by specific model
- `openinference.span.kind` — split by LLM / TOOL / AGENT
- `hermes.session.kind` — split by session vs cron
- `hermes.tool.outcome` — error-rate panels

Add more in the YAML under `connectors.spanmetrics.dimensions` if you want to slice on additional attributes the plugin sets. Histogram buckets default to `[5ms, 25ms, 100ms, 250ms, 1s, 2.5s, 5s, 10s, 30s, 60s]` — tuned for LLM workloads.

## What you'll see in Grafana

**Traces (Tempo):**
Explore → Tempo. Search by `service.name=hermes-agent` or span name (`agent`, `tool.*`, `api.*`). Every span carries the OpenInference + GenAI attribute set and the plugin's `hermes.*` extensions.

**Metrics (Prometheus):**
Explore → Prometheus. Query any `hermes_*` metric (`hermes_session_count_total`, `hermes_token_usage_total`, `hermes_tool_duration_bucket`, ...) or any `traces_spanmetrics_*` derived metric.

**Logs (Loki):**
Explore → Loki. Query `{service_name="hermes-agent"}`. Every `logger.info(...)` call from hermes or the plugin lands here with the active span's `trace_id` / `span_id` automatically attached — clicking a `trace_id` in a log line jumps straight into the Tempo trace. See [OTel logs](/configuration/logs) for the full story.

## Multi-backend config

Fan out to LGTM alongside other backends:

```yaml
backends:
  - type: lgtm
    endpoint: http://localhost:4318/v1/traces

  - type: phoenix
    endpoint: http://localhost:6006/v1/traces

capture_logs: true
```

With the above, traces go to both LGTM and Phoenix; metrics go to both (both accept OTLP metrics); logs go only to LGTM (Phoenix doesn't accept OTLP logs, so the plugin skips it automatically).

## Production usage

The `grafana/otel-lgtm` image is for **local development and quickstarts**. For production, Grafana recommends deploying the components individually:

- Tempo as its own service (or use Grafana Cloud Traces)
- Mimir or vanilla Prometheus
- Loki as its own service (or use Grafana Cloud Logs)
- A properly-sized OTel Collector in front

Once you've split them, point the plugin at your production collector and set `type: otlp` (or keep `type: lgtm` for clarity — the semantics are identical).

## Troubleshooting

**"Container is `unhealthy`"**

The bundled healthcheck probes `/tmp/ready` — the image writes that file when every sub-service is up. If the container stays `(health: starting)` forever, check the logs: `docker logs hermes-otel-lgtm`. Usually a port bind conflict.

**"Grafana is up but the Tempo datasource says 'connection refused'"**

The image needs ~30s for Tempo to finish starting — Grafana is up well before Tempo. Wait for `docker ps` to show `(healthy)` and retry.

**"I see `[hermes-otel] ✓ LGTM at ...` but no traces appear"**

From inside a Docker-networked hermes-agent, `localhost:4318` means the agent's **own** container, not your host. Use `host.docker.internal:4318` on Docker Desktop (macOS/Windows) or `172.17.0.1:4318` on Linux to reach the LGTM container running on the host. Alternatively, put both containers on the same docker network.

**"Metrics in Mimir look scaled"**

Default metric buckets are tuned for LLM latency (millisecond → minute range). For very fast tools the default buckets may bucket everything into the lowest two. Extend the `histogram.explicit.buckets` list in `otelcol-config.yaml`.

## See also

- [OTel logs](/configuration/logs) — the log pipeline that makes trace-id correlation work.
- [Multi-backend fan-out](/backends/multi-backend) — adding LGTM alongside other backends.
- [Grafana Tempo](/backends/tempo) — the traces-only standalone option (different type, different use case).
- [docker-compose/lgtm/README.md](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/lgtm/README.md) — the on-disk README with the same setup walkthrough.
