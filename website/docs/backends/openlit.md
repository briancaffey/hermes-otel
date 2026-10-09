---
sidebar_position: 14
title: "OpenLIT"
description: "OpenLIT — self-hosted, OTel-native LLM observability for Hermes agent runs: traces, metrics and logs over OTLP/HTTP into ClickHouse, with GenAI-semconv views."
---

# OpenLIT

[OpenLIT](https://github.com/openlit/openlit) (Apache-2.0) is a self-hostable, OpenTelemetry-native LLM observability platform: one app container with the UI and an OTLP receiver, plus ClickHouse for storage. It accepts **all three signals** on the standard `/v1/traces`, `/v1/metrics` and `/v1/logs` paths and reads the `gen_ai.*` semantic conventions the plugin emits.

**Signals:** traces + metrics + logs. **Deployment:** local (docker compose, two containers, about 560 MB resident) or Kubernetes via the official Helm chart. **Cost:** open source, no account.

:::tip Verified live
One real `hermes` turn through `type: openlit` into the bundled compose stack (OpenLIT 2.1.0, hermes-otel 1.23 branch, 2026-10-08) stored 5 spans (`agent`, `llm.*`, two `api.*`, `tool.terminal`), 43 log records carrying the trace id, 4 sum and 7 histogram metric names, all read back from ClickHouse. See [Verified behavior](#verified-behavior).
:::

## Quick start

Bring up the bundled stack (host ports chosen so it coexists with the other stacks in the repo: UI on 3010, OTLP/HTTP on 4338):

```bash
docker compose -f docker-compose/openlit/docker-compose.yaml up -d
```

Then declare the backend:

```yaml
backends:
  - type: openlit
    endpoint: http://localhost:4338      # the OTLP/HTTP receiver (4318 inside the container)
capture_logs: true
```

The startup banner reads `✓ OpenLIT at http://localhost:4338/v1/traces`. Open `http://localhost:3010` (default login `user@openlit.io` / `openlituser`) and the turn appears under Traces, with token and cost metrics on the dashboard.

For an OpenLIT deployment elsewhere, point `endpoint` at its OTLP/HTTP receiver, usually `http://<host>:4318`.

## Why a dedicated `type: openlit`

Export also works through the [generic OTLP](/backends/otlp) type with a hand-written endpoint and header (the compose stack used that until 1.23). Declaring `type: openlit` instead asks the plugin to:

1. Normalise `endpoint` to the traces URL (a base URL gets `/v1/traces`; a `/v1/metrics` or `/v1/logs` URL is rewritten) and derive the metrics and logs URLs from it.
2. Read an optional OpenLIT API key from `api_key:` / `api_key_env:` (falling back to `OTEL_OPENLIT_API_KEY` / `OPENLIT_API_KEY`) and send it as `Authorization: Bearer <key>`; omit the header entirely when there is none.
3. Enable all three signals by default.
4. Display `✓ OpenLIT at <endpoint>` in startup logs and list the key in the settings report.

## Configuration reference

| Field | Meaning |
|---|---|
| `endpoint` | **Required.** OpenLIT's OTLP/HTTP base, e.g. `http://localhost:4338` for the bundled stack or `http://<host>:4318` upstream. Also via `OTEL_OPENLIT_ENDPOINT`. |
| `api_key` / `api_key_env` | Optional OpenLIT API key → `Authorization: Bearer <key>`. Scopes ingest to an organisation, project and environment; without it data lands in the deployment's `INIT_DB_*` defaults (what a local stack wants). Prefer `api_key_env`. |
| `metrics` / `logs` / `traces` | Per-signal toggles. All default **on**. |
| `headers` | Extra headers, merged on top of the generated ones. |

No metric-temporality preset: OpenLIT stored the plugin's cumulative sums and histograms as-is, so the global default applies.

## Single backend (env vars)

Without a `backends:` list, the env-var flow selects OpenLIT when `OTEL_OPENLIT_ENDPOINT` is set. `OTEL_OPENLIT_API_KEY` is optional, and a key alone never switches export on because there is no default host:

```bash
export OTEL_OPENLIT_ENDPOINT=http://localhost:4338
# export OTEL_OPENLIT_API_KEY=...     # optional
export OTEL_PROJECT_NAME=hermes-otel-openlit
```

## Verified behavior

Read back from the stack's ClickHouse after one turn (`project_name: openlit-222`):

```bash
docker exec hermes-otel-openlit-clickhouse clickhouse-client --user default --password OPENLIT -d openlit -q "
SELECT 'spans', count() FROM otel_traces
UNION ALL SELECT 'logs', count() FROM otel_logs
UNION ALL SELECT 'sum metrics', uniqExact(MetricName) FROM otel_metrics_sum
UNION ALL SELECT 'histogram metrics', uniqExact(MetricName) FROM otel_metrics_histogram"
```

| Signal | Stored |
|---|---|
| Spans | 5: `agent` (`gen_ai.operation.name=invoke_agent`), `llm.<model>` and two `api.<model>` (`chat`), `tool.terminal` (`execute_tool`) |
| Logs | 43 records, each with the turn's `TraceId` and the plugin's `hermes.session_id` attribute |
| Metrics | sums `gen_ai.agent.token.usage`, `gen_ai.client.token.usage`, `hermes.message.count`, `hermes.model.usage`, `hermes.session.count`, `hermes.session.turns`, `hermes.token.usage`; histograms `gen_ai.client.operation.duration`, `gen_ai.execute_tool.duration`, `hermes.session.duration`, `hermes.tool.duration` |

The plugin's debug log showed `export OpenLIT: N span(s) -> SUCCESS` and `export OpenLIT logs: N record(s) -> SUCCESS` for every batch, nothing dropped.

OpenLIT's GPU and coding-agent views were not checked in this pass; the plugin's `host_metrics` series and `gen_ai.*` attributes are what those views read, so they may light up with the relevant config.

## Caveats

- **The released image needs the Collector config mounted.** OpenLIT 2.1.0 (what `latest` resolved to on 2026-10-05) runs an embedded Collector under an OpAMP supervisor and reads `/etc/otel/otel-collector-config.yaml`; without the mount nothing listens on 4318 and exports fail with "connection reset by peer". The bundled compose file mounts it. Upstream main replaced the Collector with a first-party receiver ([openlit #1588](https://github.com/openlit/openlit/pull/1588)); once that ships in an image the mount is unused.
- **The UI port does not proxy OTLP on 2.1.0.** Issue notes suggested the UI on 3000 also serves `/v1/*`; on the released image a POST to the UI port's `/v1/traces` answers 404. Point `endpoint` at the OTLP port (4318 in the container, 4338 on the host with the bundled stack).
- **Ports.** Upstream publishes 3000, 4317 and 4318, which collide with Langfuse / Grafana and LGTM / Jaeger / SigNoz; the bundled stack remaps them to 3010 / 4337 / 4338 (`OPENLIT_UI_PORT`, `OPENLIT_OTLP_GRPC_PORT`, `OPENLIT_OTLP_HTTP_PORT`).
- The UI logs `Unknown table … otel_traces` errors until the first span creates the tables; harmless.
- The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `openlit`; use OpenLIT's own UI to read the data back.

## Troubleshooting

- **`openlit requires endpoint`** — no `endpoint:` and no `OTEL_OPENLIT_ENDPOINT`; there is no default host.
- **`connection reset by peer` on every export** — the Collector config is not mounted (see Caveats) or the stack is still starting; `docker compose … ps` should show `hermes-otel-openlit` healthy.
- **`401` / `403`** — `OTLP_REQUIRE_API_KEY=true` is set on the deployment; create an API key in the OpenLIT UI and set `api_key_env`.
