---
slug: /
sidebar_position: 0
title: "hermes-otel"
description: "OpenTelemetry plugin for Hermes Agent — automatically export LLM traces, tool calls, token metrics and logs to Phoenix, Langfuse, LangSmith, SigNoz, Jaeger, Grafana Tempo and LGTM, Uptrace, OpenObserve, Parseable, Honeycomb, W&B Weave, Elastic, OpenLIT, MLflow, Comet Opik, Laminar, LangWatch, Latitude, or any OTLP collector."
hide_table_of_contents: true
displayed_sidebar: docs
---

import Link from '@docusaurus/Link';

<div className="hero--otel">
  <div className="container">
    <h1 className="hero__title--otel">OpenTelemetry for Hermes Agent</h1>
    <p className="hero__subtitle--otel">
      Fan LLM traces, tool calls, API requests, and token metrics out to any OTLP-compatible observability backend — <strong>Phoenix</strong>, <strong>Langfuse</strong>, <strong>LangSmith</strong>, <strong>SigNoz</strong>, <strong>Jaeger</strong>, <strong>Grafana Tempo</strong> and <strong>LGTM</strong>, <strong>Uptrace</strong>, <strong>OpenObserve</strong>, <strong>Parseable</strong>, <strong>Honeycomb</strong>, <strong>W&amp;B Weave</strong>, <strong>Elastic</strong>, <strong>OpenLIT</strong>, <strong>MLflow</strong>, <strong>Comet Opik</strong>, <strong>Laminar</strong>, <strong>LangWatch</strong>, <strong>Latitude</strong>, or your own collector. One plugin, parallel fan-out, zero hot-path blocking.
    </p>
    <div className="hero__ctas">
      <Link className="hero__cta hero__cta--primary" to="/getting-started/quickstart">Quickstart →</Link>
      <Link className="hero__cta hero__cta--secondary" to="/backends/overview">Browse backends</Link>
    </div>
    <div className="hero__code-block">
      <span className="prompt">$ </span><span className="cmd">hermes plugins install briancaffey/hermes-otel/hermes_otel</span>
    </div>
  </div>
</div>

## Why hermes-otel?

Hermes Agent is a production agent loop — tools, skills, memory, a gateway, messaging platforms. The moment you ship it, you need to see *what it's actually doing*: which tools fired, how many tokens the model burned, which turns stalled, which users hit errors.

hermes-otel turns every Hermes lifecycle hook into a properly-nested **OpenTelemetry span** with the right attribute conventions for the backend you're sending to — no adapter code per vendor. Drop it in, point it at an OTLP endpoint, and the traces show up.

<div className="feature-grid">
  <div className="feature-card">
    <h3>Dual-convention attributes</h3>
    <p>Emits both <code>{'gen_ai.*'}</code> (Langfuse / SigNoz) and <code>{'llm.token_count.*'}</code> (Phoenix / OpenInference) so the UI in your chosen backend just <em>works</em>.</p>
  </div>
  <div className="feature-card">
    <h3>Multi-backend fan-out</h3>
    <p>Send the same span to Phoenix + Langfuse + Jaeger in parallel, each on its own non-blocking worker. One slow collector can't stall the others — or the agent.</p>
  </div>
  <div className="feature-card">
    <h3>Per-turn summary</h3>
    <p>Root session span gets tool count, tool names, skills used, API-call count, and final status. Dashboards don't need to JOIN across spans to answer "what happened in this turn?"</p>
  </div>
  <div className="feature-card">
    <h3>Non-blocking export</h3>
    <p><code>BatchSpanProcessor</code> under the hood: <code>span.end()</code> is a queue push. A slow backend adds zero latency to tool calls or API requests on the hot path.</p>
  </div>
  <div className="feature-card">
    <h3>Privacy mode</h3>
    <p>Flip <code>capture_previews: false</code> to strip every input/output preview at the source. Metadata (tool names, durations, tokens) still flows.</p>
  </div>
  <div className="feature-card">
    <h3>Orphan-span sweep</h3>
    <p>Long-abandoned sessions don't leak state: a TTL sweeper finalizes stale root spans with <code>final_status=timed_out</code> so your UI stays clean.</p>
  </div>
</div>

## Supported backends

<div className="backend-grid">
  <Link className="backend-card" to="/backends/phoenix">
    <div className="backend-card__name">Phoenix</div>
    <div className="backend-card__desc">Arize's OSS LLM observability platform. Local docker or Arize AX cloud. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/langfuse">
    <div className="backend-card__name">Langfuse</div>
    <div className="backend-card__desc">OSS LLM engineering platform. Self-host or cloud. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/langsmith">
    <div className="backend-card__name">LangSmith</div>
    <div className="backend-card__desc">LangChain's tracing platform. Cloud with a free tier. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/signoz">
    <div className="backend-card__name">SigNoz</div>
    <div className="backend-card__desc">OSS observability platform. Local docker or cloud. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/jaeger">
    <div className="backend-card__name">Jaeger</div>
    <div className="backend-card__desc">The classic distributed-trace UI. Single-container local. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/tempo">
    <div className="backend-card__name">Grafana Tempo</div>
    <div className="backend-card__desc">Tempo + Grafana stack, OSS or Grafana Cloud. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/lgtm">
    <div className="backend-card__name">Grafana LGTM</div>
    <div className="backend-card__desc">Tempo + Mimir + Loki + Grafana in one image. Local docker. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/uptrace">
    <div className="backend-card__name">Uptrace</div>
    <div className="backend-card__desc">OSS APM on ClickHouse, DSN-style setup. Local docker or cloud. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/openobserve">
    <div className="backend-card__name">OpenObserve</div>
    <div className="backend-card__desc">Single-container observability store with SQL. Local docker or cloud. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/parseable">
    <div className="backend-card__name">Parseable</div>
    <div className="backend-card__desc">SQL over Parquet with a Traces view. Self-hosted or cloud. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/honeycomb">
    <div className="backend-card__name">Honeycomb</div>
    <div className="backend-card__desc">Cloud (US / EU) with a generous free tier. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/weave">
    <div className="backend-card__name">W&amp;B Weave</div>
    <div className="backend-card__desc">Weights &amp; Biases' LLM tracing. Cloud, Dedicated Cloud or self-managed. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/elastic">
    <div className="backend-card__name">Elastic</div>
    <div className="backend-card__desc">Elastic Cloud managed OTLP or a self-hosted EDOT Collector into Elasticsearch + Kibana. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/openlit">
    <div className="backend-card__name">OpenLIT</div>
    <div className="backend-card__desc">OTel-native LLM observability, two containers, Apache-2.0. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/mlflow">
    <div className="backend-card__name">MLflow</div>
    <div className="backend-card__desc">MLflow 3.6+ tracking server with its GenAI Traces UI, token and cost roll-ups. Self-hosted. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/opik">
    <div className="backend-card__name">Comet Opik</div>
    <div className="backend-card__desc">LLM evaluation and tracing platform; threads, span types, usage and cost per span. Self-hosted or Comet cloud. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/laminar">
    <div className="backend-card__name">Laminar</div>
    <div className="backend-card__desc">Agent-focused tracing with span types, tokens and cost. Self-hosted or Laminar cloud. Traces + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/langwatch">
    <div className="backend-card__name">LangWatch</div>
    <div className="backend-card__desc">LLM observability and evaluation platform. Self-hosted or cloud. Traces + metrics + logs.</div>
  </Link>
  <Link className="backend-card" to="/backends/latitude">
    <div className="backend-card__name">Latitude</div>
    <div className="backend-card__desc">Prompt engineering and agent observability on the GenAI semconv. Self-hosted or Latitude cloud. Traces only.</div>
  </Link>
  <Link className="backend-card" to="/backends/otlp">
    <div className="backend-card__name">Generic OTLP</div>
    <div className="backend-card__desc">Any OTLP/HTTP collector. Drop in an endpoint and go.</div>
  </Link>
  <Link className="backend-card" to="/backends/multi-backend">
    <div className="backend-card__name">Multi-backend</div>
    <div className="backend-card__desc">Fan the same spans out to several backends in parallel from one <code>config.yaml</code>.</div>
  </Link>
</div>

## How this relates to Hermes' built-in telemetry

Hermes core ships content-free [gateway monitoring](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-monitoring) over OTLP (gateway and cron health, no prompts, tool calls or per-run traces) and a bundled Langfuse-only observability plugin. hermes-otel is the run-level plane, and coexists with both:

| | Hermes gateway monitoring (core) | Bundled Langfuse plugin | hermes-otel |
|---|---|---|---|
| Scope | Gateway/cron health, content-free | Per-run traces | Per-run traces + metrics + logs |
| Backends | Any OTLP receiver | Langfuse only | 19 backend types plus generic OTLP, fan-out |
| Coexists with hermes-otel | Yes | Yes | |

## The span hierarchy

```text
agent / cron                              [root, AGENT]
└── llm.{model}                           [LLM — input, output, total tokens]
    ├── api.{model}                       [LLM — prompt/completion tokens, duration]
    │   └── tool.{name}                   [TOOL — args, result, outcome]
    └── api.{model}                       [LLM — second round-trip, final response]
```

Each span carries the attributes both Langfuse (`gen_ai.usage.input_tokens`, `gen_ai.content.prompt`) and Phoenix (`llm.token_count.prompt`, `input.value`) expect — see [Attribute conventions](/architecture/attributes).

## Where to go next

| | |
|---|---|
| 🚀 **[Quickstart](/getting-started/quickstart)** | Install + Phoenix in a local Docker container, first trace in under 5 minutes |
| 📦 **[Installation](/getting-started/installation)** | Install into Hermes Agent's venv, optional `langsmith` extra |
| 🧩 **[Concepts](/getting-started/concepts)** | Hooks, spans, fan-out, how the plugin wires into Hermes |
| 🎯 **[Pick a backend](/backends/overview)** | Comparison table, quick picks, decision flowchart |
| ⚙️ **[Configuration](/configuration/overview)** | `config.yaml`, env vars, sampling, privacy, batch tuning |
| 🏗️ **[Architecture](/architecture/overview)** | Span hierarchy, attribute conventions, turn summaries, orphan sweep |
| 🛠️ **[Contributing](/development/contributing)** | Run the test suite, add a backend, open a PR |
| 📑 **[Reference](/reference/env-vars)** | Every env var, every config key, every span attribute |
