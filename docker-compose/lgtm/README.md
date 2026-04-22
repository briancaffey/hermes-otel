# LGTM backend — Grafana, Loki, Tempo, Mimir + Collector

A single-container local observability stack that covers all three OTel
signals:

| Signal | Storage | UI |
|---|---|---|
| Traces | Tempo | Grafana (Tempo datasource) |
| Metrics | Mimir (Prometheus-compatible) | Grafana (Prometheus datasource) |
| Logs | Loki | Grafana (Loki datasource) |

The bundled OTel Collector receives OTLP on `:4318` (HTTP) and `:4317`
(gRPC), fans traces out to Tempo + a `spanmetrics` connector, and ships
logs to Loki — so enabling `capture_logs` in the plugin gives you trace-id
correlated logs in Grafana with no extra configuration.

## Quick start

```bash
# From the plugin root:
docker compose -p lgtm -f docker-compose/lgtm.yaml up -d

# Wait ~30s for services to come up, then open Grafana at
#   http://localhost:3000     (admin / admin)
```

Point the plugin at it. Minimal `config.yaml`:

```yaml
backends:
  - type: lgtm
    endpoint: http://localhost:4318/v1/traces

capture_logs: true       # optional — ship Python logs to Loki
log_level: INFO
```

> **Don't use `type: tempo` for this stack.** That type is for a standalone
> Tempo (traces only) and will refuse to fan out logs or metrics even when
> pointed at the LGTM collector. `type: lgtm` is a thin alias over `otlp`
> that declares intent and makes startup logs say "LGTM" instead of "OTLP".

Restart hermes-agent; `[hermes-otel] ✓ OTLP at http://localhost:4318/v1/traces`
should appear in its startup logs. If `capture_logs` is on you'll also
see `[hermes-otel] ✓ Logs → 1 backend(s) (attached to root, level=INFO)`.

## What you get in Grafana

- **Traces** — Explore → Tempo → search by service name `hermes-agent`
  or span name (`agent`, `tool.*`, `api.*`). Each span shows the full
  OpenInference + GenAI attribute set the plugin emits.
- **Metrics** — Explore → Prometheus. Query any `hermes_*` metric the
  plugin emits (`hermes_session_count_total`, `hermes_token_usage_total`,
  `hermes_tool_duration_bucket`, ...). Also available: `traces_spanmetrics_*`
  derived by the collector — RED metrics per-service from your traces.
- **Logs** — Explore → Loki. Query `{service_name="hermes-agent"}`. When
  `capture_logs` is on, every Python `logger.info(...)` from hermes-agent
  or the plugin ships here with the active span's `trace_id` / `span_id`
  attached. Clicking a `trace_id` link jumps to the Tempo trace.

## Ports

| Port | Service | Purpose |
|---|---|---|
| 3000 | Grafana | UI (admin/admin) |
| 3100 | Loki | log writes from the in-container collector |
| 3200 | Tempo | Tempo's own query API (Grafana points here) |
| 4317 | Collector | OTLP gRPC receiver |
| 4318 | Collector | OTLP HTTP receiver ← **plugin exports here** |
| 9090 | Mimir / Prometheus | metrics UI and OTLP write endpoint |

**Conflicts to watch for:**

- **Port 3000** collides with the phoenix stack. Run one or the other,
  not both. If you want both, remap LGTM's Grafana:
  `ports: ["3001:3000", ...]`.
- **Port 4318** collides with jaeger and the HTTP side of signoz. Pick
  the one you want on 4318 or remap the others.

## Collector customisation

`otelcol-config.yaml` diverges from the image's default in one place: the
`spanmetrics` connector. It turns incoming traces into RED metrics on the
Prometheus side without the plugin having to emit them from Python.
Dimensions are set to attribute names the plugin actually emits:
`llm.provider`, `llm.model_name`, `openinference.span.kind`,
`hermes.session.kind`, `hermes.tool.outcome`. Adjust here if you change
what the plugin sets on its spans.

The `batch` processor timeout/size (`1s` / `512`) is tuned for typical LLM
agent workloads — lots of small spans, latency not critical. Bump both for
higher-throughput workloads.

Exporter endpoints (`127.0.0.1:4418`, `127.0.0.1:9090`, `127.0.0.1:3100`)
target services running inside the same LGTM container. Don't rewrite
them to `4317`/`4318` — those are the collector's own receiver ports.

## Tearing down

```bash
# Stop containers, keep no state (the image writes to an internal tmpfs by default):
docker compose -p lgtm -f docker-compose/lgtm.yaml down

# Same + remove volumes (harmless — image is ephemeral):
docker compose -p lgtm -f docker-compose/lgtm.yaml down -v
```

## Under the hood

`grafana/otel-lgtm` is a single Docker image that bundles five processes
supervised by a small init:

1. An OTel Collector (this file's config)
2. Tempo (trace storage)
3. Mimir (Prometheus-compatible metric storage)
4. Loki (log storage)
5. Grafana with the three datasources pre-provisioned

For production you'd split these across containers / hosts; for local
development the single-container form keeps the setup friction at "one
docker command."
