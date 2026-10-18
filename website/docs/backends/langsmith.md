---
sidebar_position: 4
title: "LangSmith"
description: "Send Hermes traces to LangSmith over OTLP: a regular backends: entry, in the fan-out like every other target."
---

# LangSmith

[LangSmith](https://smith.langchain.com) is LangChain's tracing platform. It accepts OpenTelemetry traces at `https://api.smith.langchain.com/otel` (EU: `https://eu.api.smith.langchain.com/otel`), so it is a regular backend entry: it fans out beside the others, the live store and the Settings card know about it, and `status` lists it. Until 1.26 the plugin drove LangSmith's run-tree HTTP API instead through a separate code path selected by `LANGSMITH_TRACING=true`; that path is gone (#415) and `LANGSMITH_TRACING` no longer selects anything. See [Migrating](#migrating-from-langsmith_tracing) below.

**Signals:** traces only (the OTLP ingest serves traces; the plugin defaults `metrics` and `logs` off). **Deployment:** cloud (self-host is enterprise-only). **Cost:** free personal tier + paid.

## Configuration

As a `backends:` entry in `~/.hermes/hermes_otel.yaml`:

```yaml
backends:
  - type: langsmith
    api_key_env: LANGSMITH_API_KEY   # or api_key: lsv2_... inline
    project: hermes-agent            # optional: the Langsmith-Project header
    # region: eu                     # optional: the EU host
    # endpoint: https://api.smith.langchain.com   # optional: the plugin appends /otel/v1/traces
```

Or from the environment, with no file:

```bash
export OTEL_LANGSMITH_API_KEY="lsv2_..."   # the opt-in: selects LangSmith in env-var mode
export OTEL_LANGSMITH_PROJECT="hermes-agent"   # optional
```

`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` and `LANGSMITH_ENDPOINT` (the SDK's API base) are honoured as fallback values, but on their own they select nothing: like every vendor's SDK variables they need the `OTEL_LANGSMITH_*` opt-in or a `backends:` entry ([why](/configuration/environment-variables)).

The plugin sends `x-api-key: <key>` and, when a project is set, `Langsmith-Project: <project>`; without a project LangSmith files the traces under your default project.

## What you'll see

Each Hermes turn is one trace rooted at the `agent` (or `cron`) span with `llm.*`, `api.*` and `tool.*` children, carrying the OTel GenAI attributes (`gen_ai.usage.*` token counts, `gen_ai.request.model`, the conversation id) that LangSmith's OpenTelemetry ingest maps onto its run fields. See LangSmith's OpenTelemetry documentation for the exact attribute mapping its ingest applies.

The [Hermes dashboard's OTel tab](/dashboard) has no query adapter for `langsmith`, so the entry exports only; read the traces in LangSmith's UI.

## Migrating from `LANGSMITH_TRACING`

| Before (run-tree client, up to 1.25) | Now (OTLP preset) |
|---|---|
| `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` | `OTEL_LANGSMITH_API_KEY=...` (or a `backends:` entry with `api_key_env: LANGSMITH_API_KEY`) |
| `LANGSMITH_PROJECT` | still read; or `OTEL_LANGSMITH_PROJECT` / `project:` on the entry |
| `LANGSMITH_ENDPOINT` (API base) | still read; the plugin appends `/otel/v1/traces` |
| `LANGSMITH_WORKSPACE_ID` | not sent (the OTLP ingest takes the workspace from the key) |
| LangSmith **or** fan-out | LangSmith **and** every other backend |
| `pip install langsmith` for uuid7 run ids | not needed; ids are OTel trace and span ids |

## Troubleshooting

**401 from LangSmith**

- The key must be a LangSmith API key (`lsv2_...`). Check the `x-api-key` header in the Settings tab's backend card (values are masked; the provenance says where it came from).

**Traces land in the wrong project**

- Set `project:` on the entry or `OTEL_LANGSMITH_PROJECT`; without one LangSmith uses the key's default project.

**Nothing exported in env-var mode**

- `LANGSMITH_API_KEY` alone is ignored by design; the plugin logs `LANGSMITH_API_KEY is set, but env-var mode only exports to LangSmith when one of OTEL_LANGSMITH_API_KEY, OTEL_LANGSMITH_ENDPOINT is also set`. Set `OTEL_LANGSMITH_API_KEY`.
