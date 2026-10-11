---
sidebar_position: 1
title: "Overview"
description: "Comparison of every supported backend — signals, deployment, cost, and when to pick each one."
---

# Backends overview

hermes-otel speaks plain **OTLP/HTTP**, so any OTLP-compatible backend should work — but these are the ones that ship with first-class support, docker-compose files, and (where relevant) smoke-test coverage.

## Supported today

| Backend | Signals | Deployment | Account / cost |
|---|---|---|---|
| **[Phoenix](/backends/phoenix)** | Traces only | Local (single container) · Arize AX cloud | OSS, no account · commercial cloud |
| **[Langfuse](/backends/langfuse)** | Traces | Local (docker compose) · Cloud | OSS, no account · free tier + paid |
| **[LangSmith](/backends/langsmith)** | Traces | Cloud only (self-host = enterprise) | Free personal tier · paid tiers |
| **[SigNoz](/backends/signoz)** | Traces + metrics + logs | Local (docker compose) · Cloud | OSS, no account · free tier + paid cloud |
| **[Jaeger](/backends/jaeger)** | Traces | Local (single container) | OSS, no account needed |
| **[Zipkin](/backends/zipkin)** | Traces | Local (single container, the `zipkin-otel` image) | OSS (Apache-2.0), no account |
| **[GreptimeDB](/backends/greptime)** | Traces + metrics + logs | Local (single container) · Helm · GreptimeCloud | OSS (Apache-2.0), no account |
| **[ClickStack](/backends/clickstack)** (HyperDX) | Traces + metrics + logs | Local (one all-in-one container) · upstream compose · Helm · ClickHouse Cloud | OSS (MIT), local account on first run |
| **[VictoriaMetrics family](/backends/victoria)** | Traces + metrics + logs | Local (three single-binary containers) · Helm · VictoriaMetrics Cloud | OSS (Apache-2.0), no account |
| **[Aspire Dashboard](/backends/aspire)** | Traces + metrics + logs | Local (single container) | OSS (MIT), no account |
| **[Gigapipe](/backends/gigapipe)** (qryn) | Traces + metrics + logs | Local (Gigapipe + ClickHouse) · Gigapipe Cloud | OSS (AGPL-3.0), no account |
| **[Agenta](/backends/agenta)** | Traces | Local (docker compose, eleven containers) · Agenta Cloud (US / EU) | OSS (MIT), local account · free cloud tier |
| **[Grafana Tempo](/backends/tempo)** | Traces | Local (docker compose) · Grafana Cloud | OSS, no account · free tier + paid cloud |
| **[Grafana LGTM](/backends/lgtm)** | Traces + metrics + logs | Local (single container) | OSS, no account |
| **[Uptrace](/backends/uptrace)** | Traces + metrics + logs | Local (docker compose) · Self-hosted | OSS · premium license for HA features |
| **[OpenObserve](/backends/openobserve)** | Traces + metrics + logs | Local (single container) · Self-hosted HA | OSS, no account |
| **[Parseable](/backends/parseable)** | Traces + metrics + logs | Self-hosted · Cloud | OSS · paid cloud |
| **[Honeycomb](/backends/honeycomb)** | Traces + metrics + logs | Cloud (US / EU) | Generous free tier · paid plans |
| **[W&B Weave](/backends/weave)** | Traces | W&B Cloud · Dedicated Cloud · Self-Managed | W&B account |
| **[Elastic](/backends/elastic)** | Traces + metrics + logs | Elastic Cloud (mOTLP) · self-hosted EDOT Collector | Elastic license / cloud plan |
| **[OpenLIT](/backends/openlit)** | Traces + metrics + logs | Local (docker compose, 2 containers) · Helm | OSS (Apache-2.0), no account |
| **[MLflow](/backends/mlflow)** | Traces | Local (docker compose, 1 container) · Helm · Databricks | OSS (Apache-2.0), no account |
| **[Comet Opik](/backends/opik)** | Traces | Local (docker compose, 7 containers) · Helm · Comet cloud | OSS (Apache-2.0) · free cloud tier |
| **[Laminar](/backends/laminar)** | Traces + logs | Local (docker compose, 5 containers) · Helm · Laminar cloud | OSS (Apache-2.0) · free cloud tier |
| **[LangWatch](/backends/langwatch)** | Traces + metrics + logs | Local (docker compose, 5 containers) · Helm · LangWatch cloud | OSS (Apache-2.0) · free cloud tier |
| **[Latitude](/backends/latitude)** | Traces | Local (docker compose, 13 containers) · Helm · Latitude cloud | OSS (MIT) · free cloud tier |
| **[telemetry.dev](/backends/telemetry)** (via generic `otlp`) | Traces + metrics + logs | Cloud | Free tier + paid plans |
| **[Generic OTLP](/backends/otlp)** | Depends on collector | Anywhere | — |

## Quick picks

**"I just want to see a trace, right now, on my laptop"**
→ [Phoenix](/backends/phoenix) — one container, open the UI on port 6006, done. Or the [Aspire Dashboard](/backends/aspire) — one container, all three signals, a GenAI visualizer, and the plugin dashboard reads traces and logs back.

**"I want pretty LLM-specific UI and I'm fine running a stack"**
→ [Langfuse](/backends/langfuse) — polished UI for LLM traces, free cloud tier, robust self-host.

**"I want traces *and* the token/tool/cost metrics dashboard"**
→ [SigNoz](/backends/signoz), [LGTM](/backends/lgtm) or [OpenObserve](/backends/openobserve) — they accept OTLP metrics as well as traces (Phoenix does not).

**"I want the Grafana APIs without running Grafana's stores"**
→ [Gigapipe](/backends/gigapipe) — OTLP in, the Tempo, Prometheus and Loki APIs out of one port on ClickHouse; the plugin reads it with its LGTM adapters.

**"I run VictoriaMetrics and want traces and logs in the same family"**
→ [VictoriaTraces + VictoriaMetrics + VictoriaLogs](/backends/victoria) — one entry, three stores; the dashboard reads them through the Jaeger, Prometheus and LogsQL APIs.

**"I want ClickHouse's own observability stack with a search UI"**
→ [ClickStack](/backends/clickstack) — HyperDX on ClickHouse in one container, traces + metrics + logs in the standard OTel tables; the dashboard adapter reads them straight from ClickHouse.

**"I want all three signals in one open-source database I can query with SQL"**
→ [GreptimeDB](/backends/greptime) — one container, OTLP in under `/v1/otlp`, SQL, PromQL and a Jaeger-compatible API out; the dashboard adapter reads all three signals.

**"I want all three signals — traces, metrics, AND logs — in Grafana, in one container"**
→ [Grafana LGTM](/backends/lgtm) — `grafana/otel-lgtm` bundles Grafana + Tempo + Loki + Mimir + a collector. Pair with `capture_logs: true` and you get [trace-id-correlated logs](/configuration/logs) out of the box.

**"I'm already on LangChain / LangSmith"**
→ [LangSmith](/backends/langsmith) — free personal tier, zero extra infra.

**"Standard distributed tracing stack, no LLM-specific UI needed"**
→ [Jaeger](/backends/jaeger), [Zipkin](/backends/zipkin) or [Grafana Tempo](/backends/tempo) — all three are traces-only; pair with Prometheus if you need metrics. Zipkin is the lightest read API of the three and the dashboard adapter reads it.

**"I want a cloud backend with a generous free tier and all three signals"**
→ [Honeycomb](/backends/honeycomb) — OTLP-native; set `OTEL_HONEYCOMB_API_KEY` and a region.

**"I want W&B's Agents and Traces UI for Hermes runs"**
→ [W&B Weave](/backends/weave) — direct OTLP trace ingest with `OTEL_WEAVE_API_KEY`, `wandb.entity`, and `wandb.project`.

**"I want Hermes agent runs plus traces, metrics, and logs in Parseable"**
→ [Parseable](/backends/parseable) — direct OTLP ingest into three datasets, with Agent Observability and a ready-made dashboard.

**"I want a hosted backend purpose-built for LLM/agent telemetry, `gen_ai.*`-native"**
→ [telemetry.dev](/backends/telemetry) — OTLP ingest with a single `Authorization: Bearer` header, via the generic `otlp` type.

**"I want an OTel-native LLM UI with all three signals, self-hosted and light"**
→ [OpenLIT](/backends/openlit) — two containers, `gen_ai.*`-aware views, traces + metrics + logs on the standard `/v1/*` paths.

**"We already run MLflow for experiments"**
→ [MLflow](/backends/mlflow) — traces land in an experiment next to your runs, with MLflow's own token and cost roll-ups.

**"I want the richest LLM-trace view of the self-hosted batch: threads, span types, cost per call"**
→ [Comet Opik](/backends/opik) — maps both `gen_ai.*` and `llm.*` conventions; `thread_id` is the Hermes session.

**"An agent-focused trace UI that also keeps the agent's log lines"**
→ [Laminar](/backends/laminar) — span types, tokens and cost per call, plus log records next to the trace; metrics are off because Laminar drops them.

**"An open-source LLMOps platform that reads both conventions and queries back"**
→ [Agenta](/backends/agenta) — OTLP traces with a project key; span types, token roll-ups and sessions computed server-side; the dashboard adapter reads `POST /api/spans/query`.

**"An LLM platform with all three signals and an evaluation workflow"**
→ [LangWatch](/backends/langwatch) — traces, metrics and logs under one `/api/otel` prefix; thread id = the Hermes session.

**"Our observability is already Elastic (self-hosted or Elastic Cloud)"**
→ [Elastic](/backends/elastic) — one `type: elastic` entry covers the managed OTLP endpoint (`ApiKey` auth) and a self-hosted EDOT Collector (no key needed).

**"My company already has an OTel collector / New Relic / Datadog"**
→ [Generic OTLP](/backends/otlp) — point at its ingest endpoint and it just works.

**"I want several of the above simultaneously"**
→ [Multi-backend fan-out](/backends/multi-backend) — same spans, parallel, non-blocking.

## Signal support

Backends differ in which OTel signals they accept. The plugin auto-skips signals a backend can't take — you don't need to configure anything.

| Backend | Traces | Metrics | Logs |
|---|---|---|---|
| Phoenix | ✅ | ❌ (rejects `/v1/metrics`; the plugin defaults `metrics: false`) | ❌ |
| Langfuse | ✅ | ❌ | ❌ |
| LangSmith | ✅ (via HTTP Run API, not OTLP) | ❌ | ❌ |
| SigNoz | ✅ | ✅ | ✅ |
| Jaeger | ✅ | ❌ | ❌ |
| Zipkin | ✅ | ❌ (`/v1/metrics` is 404) | ❌ (`/v1/logs` answers 202 but stores a record only as a span annotation; off by default) |
| GreptimeDB | ✅ | ✅ (no exponential histograms) | ✅ |
| ClickStack | ✅ | ✅ | ✅ |
| VictoriaMetrics family | ✅ | ✅ (when `metrics_endpoint` names VictoriaMetrics) | ✅ (when `logs_endpoint` names VictoriaLogs) |
| Aspire Dashboard | ✅ | ✅ (no exponential histograms) | ✅ |
| Gigapipe | ✅ | ✅ (cumulative only) | ✅ |
| Agenta | ✅ | ❌ | ❌ |
| Grafana Tempo | ✅ | ❌ | ❌ |
| Grafana LGTM | ✅ | ✅ | ✅ |
| Uptrace | ✅ | ✅ | ✅ |
| OpenObserve | ✅ | ✅ | ✅ |
| Parseable | ✅ | ✅ | ✅ |
| Honeycomb | ✅ | ✅ | ✅ |
| W&B Weave | ✅ | ❌ | ❌ |
| Elastic | ✅ | ✅ | ✅ |
| OpenLIT | ✅ | ✅ | ✅ |
| MLflow | ✅ | ❌ | ❌ |
| Comet Opik | ✅ | ❌ | ❌ |
| Laminar | ✅ | ❌ (accepted and dropped; off by default) | ✅ |
| LangWatch | ✅ | ✅ | ✅ |
| Latitude | ✅ | ❌ | ❌ |
| telemetry.dev | ✅ | ✅ | ✅ |
| Generic OTLP | ✅ | depends on collector | depends on collector |

If you care about token / tool / cost metrics on a traces-only backend, pair it with a Prometheus-compatible sink or fan out to SigNoz / LGTM / OpenObserve alongside. See [OTel logs](/configuration/logs) for the logs pipeline.

## Metrics and your backend

Two metric settings depend on the backend (since 1.15; details on the [metrics reference](/reference/metrics)):

| Backend | Temporality it wants | Preset | Exponential histograms |
|---|---|---|---|
| Grafana LGTM, Tempo-side Prometheus, Mimir | cumulative (delta is dropped unless `otlp-deltatocumulative` is on) | cumulative (default) | Prometheus 3.8+ native histograms; Mimir only with native-histogram ingestion enabled |
| OpenObserve | cumulative works; delta handling undocumented | cumulative (default) | not documented |
| ClickStack | either: the ClickHouse exporter stores every point as sent | cumulative (default) | stored (`otel_metrics_exponential_histogram`) |
| VictoriaMetrics | cumulative natively; delta stored as-is since v1.132 (query with `sum_over_time`) | cumulative (default) | converted to VictoriaMetrics `vmrange` histograms |
| Gigapipe | **cumulative only**: delta points are rejected one by one (`partial_success`) | cumulative (default) | converted to `_bucket` / `_sum` / `_count` |
| Aspire Dashboard | either (stored as sent) | cumulative (default) | **rejected** ("is not supported"); keep `metrics_histogram: explicit` |
| GreptimeDB | either: stores cumulative and delta points as sent | cumulative (default) | **rejected** (`ExponentialHistogram is not supported yet`); keep `metrics_histogram: explicit` |
| SigNoz | recommends delta; exponential histograms are delta-only and self-hosted-only | `delta` | self-hosted only |
| Uptrace | prefers delta, converts cumulative | `delta` | recommended |
| Honeycomb | either | cumulative (default) | not verified |
| Parseable, generic `otlp` | depends on the collector behind it | cumulative (default) | depends |
| Elastic | delta required (ES does not handle cumulative histograms) | `delta` | accepted |
| OpenLIT | cumulative stored as-is (verified) | cumulative (default) | stored (exponential histogram type) |
| Datadog, New Relic, Logfire (via generic `otlp` today, explicit types tracked in #232) | delta required (Datadog rejects cumulative sums; Logfire dashboards stay empty on cumulative; New Relic prefers delta) | set `metrics_temporality: delta` on the entry | Datadog and New Relic accept them |

Override per entry with `metrics_temporality: cumulative | delta`, or for every backend with the top-level `metrics_temporality`. Backends that total a trace by summing every span's `gen_ai.usage.*` (Opik, LangWatch) are sent the `agent` root without its token roll-up (`root_usage: false`, the preset for those two types; `true` elsewhere), so their trace totals equal the sum of the `api.*` spans while Phoenix, Langfuse and the rest keep the roll-up on the root. `metrics_histogram: exponential` switches every backend to base-2 exponential histograms, so use it only when all of them accept those.

**Short-lived runs.** `hermes -z` exports metrics once, when it exits. A single cumulative point from a fresh process counts as zero on Datadog and New Relic (they treat the first point as a baseline) and goes stale after five minutes on Prometheus; the same run exported as delta counts in full on every delta-capable backend. Long-lived gateways are unaffected either way.

## Selecting a single backend

Single-backend selection is env-var-driven. First match wins:

1. `LANGSMITH_TRACING=true` → LangSmith
2. Langfuse credentials + at least one `OTEL_LANGFUSE_*` variable (`OTEL_LANGFUSE_PUBLIC_API_KEY`, `OTEL_LANGFUSE_SECRET_API_KEY` or `OTEL_LANGFUSE_ENDPOINT`) → Langfuse
3. `OTEL_SIGNOZ_ENDPOINT` set → SigNoz
4. `OTEL_UPTRACE_ENDPOINT` + DSN set → Uptrace
5. `OTEL_OPENOBSERVE_ENDPOINT` + credentials set → OpenObserve
6. `OTEL_PARSEABLE_ENDPOINT` + `PARSEABLE_API_KEY` set → Parseable
7. `OTEL_WEAVE_API_KEY` (or `OTEL_WEAVE_ENDPOINT` / `OTEL_WEAVE_BASE_URL`) + `WANDB_ENTITY` + `WANDB_PROJECT` set → W&B Weave
8. `OTEL_HONEYCOMB_API_KEY` (or `OTEL_HONEYCOMB_ENDPOINT`) set → Honeycomb
9. `OTEL_ELASTIC_ENDPOINT` set → Elastic (`OTEL_ELASTIC_API_KEY` optional: a local EDOT Collector needs none, and a key alone never opts in because Elastic has no default host)
10. `OTEL_OPENLIT_ENDPOINT` set → OpenLIT (`OTEL_OPENLIT_API_KEY` optional, same rule)
11. `OTEL_MLFLOW_ENDPOINT` set → MLflow (`MLFLOW_EXPERIMENT_ID` optional, default `0`)
12. `OTEL_OPIK_API_KEY` (cloud, with `OPIK_WORKSPACE`) or `OTEL_OPIK_ENDPOINT` set → Comet Opik
13. `OTEL_LAMINAR_API_KEY` set (cloud, or with `OTEL_LAMINAR_ENDPOINT`) → Laminar
14. `OTEL_LANGWATCH_API_KEY` set (cloud, or with `OTEL_LANGWATCH_ENDPOINT`) → LangWatch
15. `OTEL_LATITUDE_API_KEY` + `LATITUDE_PROJECT` set (cloud, or with `OTEL_LATITUDE_ENDPOINT`) → Latitude
16. `OTEL_AGENTA_API_KEY` set (cloud, or with `OTEL_AGENTA_ENDPOINT`) → Agenta
17. `OTEL_CLICKSTACK_ENDPOINT` + `OTEL_CLICKSTACK_API_KEY` (or `HYPERDX_API_KEY`) set → ClickStack
18. `OTEL_GREPTIME_ENDPOINT` set → GreptimeDB (`OTEL_GREPTIME_DB`, `OTEL_GREPTIME_USER` / `OTEL_GREPTIME_PASSWORD` optional)
19. `OTEL_VICTORIA_ENDPOINT` set → VictoriaTraces (`OTEL_VICTORIA_METRICS_ENDPOINT` / `OTEL_VICTORIA_LOGS_ENDPOINT` switch metrics and logs on)
20. `OTEL_ASPIRE_ENDPOINT` set → Aspire Dashboard (`OTEL_ASPIRE_API_KEY` optional)
21. `OTEL_GIGAPIPE_ENDPOINT` set → Gigapipe (`OTEL_GIGAPIPE_USER` / `OTEL_GIGAPIPE_PASSWORD` optional)
22. `OTEL_JAEGER_ENDPOINT` set → Jaeger
23. `OTEL_ZIPKIN_ENDPOINT` set → Zipkin
24. `OTEL_TEMPO_ENDPOINT` set → Tempo
25. `OTEL_PHOENIX_ENDPOINT` set → Phoenix

Vendor SDK variables (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`, `WANDB_API_KEY`, `HONEYCOMB_API_KEY`, `ELASTIC_API_KEY`, `OPENLIT_API_KEY`, `MLFLOW_TRACKING_TOKEN`, `OPIK_API_KEY`, `LMNR_PROJECT_API_KEY`, `LANGWATCH_API_KEY`, `LATITUDE_API_KEY`, `HYPERDX_API_KEY`, `AGENTA_API_KEY`) only fill in credentials. On their own they never switch export on, because they are often set for other tools and Hermes loads `$HERMES_HOME/.env` into the process; one plugin-namespaced `OTEL_*` variable is the explicit opt-in. When such credentials are present without the opt-in, the startup log says which `OTEL_*` variable would enable export, and the dashboard's OTel → Settings → Environment view shows the same notice.

Setting `backends:` in `config.yaml` overrides the env-var flow entirely — see [Multi-backend fan-out](/backends/multi-backend).

## What each backend does differently

Every one of these speaks OTLP, and every one of them differs in where it listens, which header carries the key, which signals it stores, which metric temporality it wants, how it totals tokens over a trace, what it truncates, and which API gives the data back. [Backend quirks](/backends/quirks) is the per-category comparison and lists, per backend, what the plugin does about each difference. In the repo the same catalog is [`docker-compose/QUIRKS.md`](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/QUIRKS.md).

## Running one locally

Every self-hostable backend above, plus the candidates tracked in [#232](https://github.com/briancaffey/hermes-otel/issues/232), has its own folder under [`docker-compose/`](https://github.com/briancaffey/hermes-otel/tree/main/docker-compose) in the repo: `docker compose -f docker-compose/<name>/docker-compose.yaml up -d`, and `down -v` to remove it with its data. Each folder has a README (why pick it, logins, the `backends:` snippet, verification query, caveats), and [`docker-compose/README.md`](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/README.md) is the manual: the comparison table, the port map, disk/memory budgets and the test loop.

Backends without an explicit `type:` yet are driven through the generic `otlp` type; each was verified end to end on 2026-10-05 with hermes-otel 1.19.0 (OpenLIT, MLflow, Opik, Laminar, LangWatch and Latitude have explicit types since 1.23):

| Backend | Compose file | Stored from a Hermes turn | Plugin config |
|---|---|---|---|
| [OpenLIT](https://github.com/openlit/openlit) (#222) | `openlit/` | traces + metrics + logs | `type: openlit`, `http://localhost:4338` (explicit type since 1.23) |
| [MLflow](https://mlflow.org) (#221) | `mlflow/` | traces (with MLflow's own token and cost roll-ups) | `type: mlflow`, `http://localhost:5001` (explicit type since 1.23) |
| [Comet Opik](https://github.com/comet-ml/opik) (#220) | `opik/` | traces (threads, span types, cost) | `type: opik`, `http://localhost:5173` (explicit type since 1.23) |
| [Laminar](https://github.com/lmnr-ai/lmnr) (#223) | `laminar/` | traces + logs; metrics accepted and dropped | `type: laminar`, `http://localhost:8100`, `api_key_env` (explicit type since 1.23) |
| [LangWatch](https://github.com/langwatch/langwatch) (#229) | `langwatch/` | traces + metrics + logs | `type: langwatch`, `http://localhost:5560`, `api_key_env` (explicit type since 1.23) |
| [Langtrace](https://github.com/Scale3-Labs/langtrace) (#224) | `langtrace/` | traces (string and int attributes only) | `type: otlp`, `http://localhost:3040/api/trace`, header `x-api-key`, `metrics: false`, `logs: false` |
| [Sigiro](https://sigiro.com) (#71) | `sigiro/` | traces + metrics + logs | `type: otlp`, `http://localhost:4378/v1/traces` |
| [OpenTelemetry Collector](/backends/collector) (#368) | `collector/` | traces + metrics + logs, printed (`debug`) or forwarded (`otlphttp`, verified into the viewer); Azure Monitor / AWS / Google Cloud targets documented | `type: otlp`, `http://localhost:4448/v1/traces`; `COLLECTOR_TARGET` picks the destination |
| [otel-desktop-viewer](https://github.com/CtrlSpice/otel-desktop-viewer) (#367) | `otel-desktop-viewer/` | traces + metrics + logs (5 spans, 27 log records, 13 metric names from one turn) | `type: otlp`, `http://localhost:4428/v1/traces`; UI :3070, JSON-RPC `POST /rpc`. [otel-tui](/backends/viewers) is the terminal sibling: a `docker run` line in the same README, `:4438` |
| [Maple Local](https://maple.dev/local/) (#49) | `maple/` | traces + metrics + logs | `type: otlp`, `http://localhost:4388/v1/traces` |
| [Parseable OSS](https://www.parseable.com) (#238) | `parseable/` | traces + metrics + logs, through the bundled collector | `type: otlp`, `http://localhost:4348/v1/traces` |
| [Zipkin](https://github.com/openzipkin-contrib/zipkin-otel) (#364) | `zipkin/` | traces (7 spans from one turn; every attribute a string tag) | `type: zipkin`, `http://localhost:9411` (the `zipkin-otel` image; stock Zipkin has no OTLP) |
| [Agenta](https://github.com/Agenta-AI/agenta) (#371) | `agenta/` | traces (7 spans with Agenta's token roll-ups from one turn) | `type: agenta`, `http://localhost:3060`, `api_key_env` (key from `agenta/mint-api-key.sh`) |
| [Gigapipe](https://github.com/metrico/gigapipe) (#370) | `gigapipe/` | traces + metrics + logs (7 spans, 28 log lines, 27 metric names from one turn) | `type: gigapipe`, `http://localhost:3110` |
| [Aspire Dashboard](https://aspire.dev/dashboard/standalone/) (#369) | `aspire/` | traces + metrics + logs (7 spans, 28 log rows from one turn; metrics in the UI only) | `type: aspire`, `http://localhost:4418`, `query_port: 18888` |
| [VictoriaTraces + VictoriaLogs + VictoriaMetrics](https://docs.victoriametrics.com) (#366) | `victoria/` | traces + metrics + logs (7 spans, 28 log rows, 27 metric names from one turn) | `type: victoria`, `endpoint` :10428 + `metrics_endpoint` :8428 + `logs_endpoint` :9428 |
| [ClickStack](https://github.com/hyperdxio/hyperdx) (#365) | `clickstack/` | traces + metrics + logs (7 spans, 28 log rows, 13 metric names from one turn) | `type: clickstack`, `http://localhost:4408`, `api_key_env` (key from `clickstack/mint-api-key.sh`) |
| [GreptimeDB](https://github.com/GreptimeTeam/greptimedb) (#363) | `greptime/` | traces + metrics + logs (7 spans, 28 log rows, 27 metric tables from one turn) | `type: greptime`, `http://localhost:4398` (the trace pipeline header on traces only) |
| [Latitude](https://github.com/latitude-dev/latitude-llm) (#230) | `latitude/` | traces (13 containers, ~15 GB of images) | `type: latitude`, `http://localhost:3002`, `api_key_env` + `project` (explicit type since 1.23) |

[Elastic](/backends/elastic) (`elastic/`: Elasticsearch + Kibana + EDOT Collector, `type: elastic`) was added on 2026-10-07 and verified with hermes-otel 1.21.0. Two of these are not open source: Sigiro publishes only a binary image, and Maple is source-available under FSL-1.1. [Jaeger v2](/backends/jaeger) (`jaeger-v2/`) and [Langfuse v4](/backends/langfuse) (`LANGFUSE_VERSION=4`) have their own stacks so the read-side issues #245 and #246 can be reproduced locally.

## Planned

These are OTLP-compatible and should work today with the generic OTLP backend — first-class docs, docker-compose files, and smoke tests are on the roadmap:

- [New Relic](https://newrelic.com) — cloud, 100 GB/mo free tier
- [Datadog](https://www.datadoghq.com) — cloud, trial only

File an issue if you've tried one of these and hit friction — we'll prioritise.
