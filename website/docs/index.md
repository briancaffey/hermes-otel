---
slug: /
sidebar_position: 0
title: "hermes-otel"
description: "OpenTelemetry plugin for Hermes Agent — automatically export LLM traces, tool calls, and token metrics to Phoenix, Langfuse, LangSmith, SigNoz, Jaeger, and Grafana Tempo."
hide_table_of_contents: true
displayed_sidebar: docs
---

import Link from '@docusaurus/Link';

<div className="hero--otel">
  <div className="container">
    <h1 className="hero__title--otel">OpenTelemetry for Hermes Agent</h1>
    <p className="hero__subtitle--otel">
      Fan LLM traces, tool calls, API requests, and token metrics out to any OTLP-compatible observability backend — <strong>Phoenix</strong>, <strong>Langfuse</strong>, <strong>LangSmith</strong>, <strong>SigNoz</strong>, <strong>Jaeger</strong>, <strong>Grafana Tempo</strong>, or your own collector. One plugin, parallel fan-out, zero hot-path blocking.
    </p>
    <div className="hero__ctas">
      <Link className="hero__cta hero__cta--primary" to="/getting-started/quickstart">Quickstart →</Link>
      <Link className="hero__cta hero__cta--secondary" to="/backends/overview">Browse backends</Link>
    </div>
    <div className="hero__code-block">
      <span className="prompt">$ </span><span className="cmd">hermes plugins install briancaffey/hermes-otel</span>
    </div>
  </div>
</div>

## Why hermes-otel?

Hermes Agent is a production agent loop — tools, skills, memory, a gateway, messaging platforms. The moment you ship it, you need to see *what it's actually doing*: which tools fired, how many tokens the model burned, which turns stalled, which users hit errors.

hermes-otel turns every Hermes lifecycle hook into a properly-nested **OpenTelemetry span** with the right attribute conventions for the backend you're sending to — no adapter code per vendor. Drop it in, point it at an OTLP endpoint, and the traces show up.

<div className="feature-grid">
  <div className="feature-card">
    <h3>Dual-convention attributes</h3>
    <p>Emits both <code>gen_ai.*</code> (Langfuse / SigNoz) and <code>llm.token_count.*</code> (Phoenix / OpenInference) so the UI in your chosen backend just <em>works</em>.</p>
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
    <div className="backend-card__desc">Arize's OSS LLM observability platform. Local docker or Arize AX cloud. Traces + metrics.</div>
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
  <Link className="backend-card" to="/backends/otlp">
    <div className="backend-card__name">Generic OTLP</div>
    <div className="backend-card__desc">Any OTLP/HTTP collector. Drop in an endpoint and go.</div>
  </Link>
  <Link className="backend-card" to="/backends/multi-backend">
    <div className="backend-card__name">Multi-backend</div>
    <div className="backend-card__desc">Fan the same spans out to several backends in parallel from one <code>config.yaml</code>.</div>
  </Link>
</div>

## The span hierarchy

```text
session.{platform} / cron                 [root, GENERAL]
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
