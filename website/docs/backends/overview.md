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
| **[Phoenix](/backends/phoenix)** | Traces + metrics | Local (single container) · Arize AX cloud | OSS, no account · commercial cloud |
| **[Langfuse](/backends/langfuse)** | Traces | Local (docker compose) · Cloud | OSS, no account · free tier + paid |
| **[LangSmith](/backends/langsmith)** | Traces | Cloud only (self-host = enterprise) | Free personal tier · paid tiers |
| **[SigNoz](/backends/signoz)** | Traces + metrics + logs | Local (docker compose) · Cloud | OSS, no account · free tier + paid cloud |
| **[Jaeger](/backends/jaeger)** | Traces | Local (single container) | OSS, no account needed |
| **[Grafana Tempo](/backends/tempo)** | Traces | Local (docker compose) · Grafana Cloud | OSS, no account · free tier + paid cloud |
| **[Grafana LGTM](/backends/lgtm)** | Traces + metrics + logs | Local (single container) | OSS, no account |
| **[Generic OTLP](/backends/otlp)** | Depends on collector | Anywhere | — |

## Quick picks

**"I just want to see a trace, right now, on my laptop"**
→ [Phoenix](/backends/phoenix) — one container, open the UI on port 6006, done.

**"I want pretty LLM-specific UI and I'm fine running a stack"**
→ [Langfuse](/backends/langfuse) — polished UI for LLM traces, free cloud tier, robust self-host.

**"I want traces *and* the token/tool/cost metrics dashboard"**
→ [Phoenix](/backends/phoenix) or [SigNoz](/backends/signoz) — both accept OTLP metrics as well as traces.

**"I want all three signals — traces, metrics, AND logs — in Grafana, in one container"**
→ [Grafana LGTM](/backends/lgtm) — `grafana/otel-lgtm` bundles Grafana + Tempo + Loki + Mimir + a collector. Pair with `capture_logs: true` and you get [trace-id-correlated logs](/configuration/logs) out of the box.

**"I'm already on LangChain / LangSmith"**
→ [LangSmith](/backends/langsmith) — free personal tier, zero extra infra.

**"Standard distributed tracing stack, no LLM-specific UI needed"**
→ [Jaeger](/backends/jaeger) or [Grafana Tempo](/backends/tempo) — both are traces-only; pair with Prometheus if you need metrics.

**"My company already has an OTel collector / Honeycomb / New Relic / Datadog"**
→ [Generic OTLP](/backends/otlp) — point at its ingest endpoint and it just works.

**"I want several of the above simultaneously"**
→ [Multi-backend fan-out](/backends/multi-backend) — same spans, parallel, non-blocking.

## Signal support

Backends differ in which OTel signals they accept. The plugin auto-skips signals a backend can't take — you don't need to configure anything.

| Backend | Traces | Metrics | Logs |
|---|---|---|---|
| Phoenix | ✅ | ✅ | ❌ |
| Langfuse | ✅ | ❌ | ❌ |
| LangSmith | ✅ (via HTTP Run API, not OTLP) | ❌ | ❌ |
| SigNoz | ✅ | ✅ | ✅ |
| Jaeger | ✅ | ❌ | ❌ |
| Grafana Tempo | ✅ | ❌ | ❌ |
| Grafana LGTM | ✅ | ✅ | ✅ |
| Generic OTLP | ✅ | depends on collector | depends on collector |

If you care about token / tool / cost metrics on a traces-only backend, pair it with a Prometheus-compatible sink or fan out to Phoenix / SigNoz / LGTM alongside. See [OTel logs](/configuration/logs) for the logs pipeline.

## Selecting a single backend

Single-backend selection is env-var-driven. First match wins:

1. `LANGSMITH_TRACING=true` → LangSmith
2. `OTEL_LANGFUSE_PUBLIC_API_KEY` + `OTEL_LANGFUSE_SECRET_API_KEY` set → Langfuse
3. `OTEL_SIGNOZ_ENDPOINT` set → SigNoz
4. `OTEL_JAEGER_ENDPOINT` set → Jaeger
5. `OTEL_TEMPO_ENDPOINT` set → Tempo
6. `OTEL_PHOENIX_ENDPOINT` set → Phoenix

Setting `backends:` in `config.yaml` overrides the env-var flow entirely — see [Multi-backend fan-out](/backends/multi-backend).

## Planned

These are OTLP-compatible and should work today with the generic OTLP backend — first-class docs, docker-compose files, and smoke tests are on the roadmap:

- [OpenObserve](https://openobserve.ai) — OSS, single binary, traces + metrics + logs
- [Uptrace](https://uptrace.dev) — OSS, docker compose, full stack
- [Honeycomb](https://www.honeycomb.io) — cloud, generous free tier
- [New Relic](https://newrelic.com) — cloud, 100 GB/mo free tier
- [Elastic APM](https://www.elastic.co/observability/application-performance-monitoring) — self-host or Elastic Cloud
- [Datadog](https://www.datadoghq.com) — cloud, trial only

File an issue if you've tried one of these and hit friction — we'll prioritise.
