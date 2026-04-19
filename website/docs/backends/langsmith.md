---
sidebar_position: 4
title: "LangSmith"
description: "Send Hermes traces to LangSmith — LangChain's tracing platform — with time-ordered run IDs."
---

# LangSmith

[LangSmith](https://smith.langchain.com) is LangChain's tracing platform. It has a free personal tier, excellent LLM-specific UI, and the same Run-ID model used throughout the LangChain ecosystem. Unlike the other backends on this page, LangSmith does **not** speak OTLP — it has its own HTTP Run API — so it's an env-var-only path inside the plugin.

**Signals:** traces only. **Deployment:** cloud (self-host is enterprise-only). **Cost:** free personal tier + paid.

## Configuration

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY="lsv2_..."
# Optional — defaults to LangChain Cloud:
# export LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
# Optional — project name for organizing traces:
# export LANGSMITH_PROJECT="hermes-langsmith-otel"
```

Setting `LANGSMITH_TRACING=true` short-circuits the rest of the backend detection — LangSmith takes priority even if `OTEL_PHOENIX_ENDPOINT` or a `backends:` list is also set.

## Optional: `langsmith` extra for uuid7 run IDs

Install the `langsmith` SDK to use time-ordered [uuid7](https://datatracker.ietf.org/doc/html/draft-ietf-uuidrev-rfc4122bis) run IDs:

```bash
~/git/hermes-agent/venv/bin/pip install langsmith
```

The plugin uses `langsmith.uuid7()` if the package is importable, falling back to `uuid.uuid4()` if it isn't. uuid7 IDs sort lexicographically by creation time, which makes LangSmith's run tree easier to follow.

## What you'll see

- Each Hermes turn creates a run tree: `session.*` is the parent run, with `llm.*`, `api.*`, and `tool.*` as child runs.
- LangSmith's `Inputs` and `Outputs` tabs read from `gen_ai.content.prompt` / `gen_ai.content.completion`.
- Token counts show up on the `api.*` runs via `gen_ai.usage.*`.
- Tool call args + results appear as their own runs under the parent LLM run.

## Why no OTLP path?

LangSmith exposes a REST API (`POST /runs`) rather than an OTLP ingest. The plugin translates each span into a LangSmith run at export time. Because the mapping is vendor-specific rather than OTLP-standard, LangSmith doesn't work inside the `backends:` fan-out list in `config.yaml` — setting `LANGSMITH_TRACING=true` is the only way to enable it.

If you need LangSmith **and** a vendor-neutral OTLP target at the same time, you can still do that: LangSmith via env vars, the other backend(s) via the `backends:` list. Setting `LANGSMITH_TRACING=true` short-circuits the `backends:` list, so you'd need to pick — this is a known limitation. Open an issue if it bites you.

## Troubleshooting

**"401 Unauthorized from LangSmith"**

- `LANGSMITH_API_KEY` must start with `lsv2_`. Legacy `ls__*` keys work too but are being phased out.

**"Runs show up under the wrong project"**

- `LANGSMITH_PROJECT` controls the LangSmith project name. If unset, LangSmith's default project is used. Set it explicitly so Hermes traces are isolated from other apps.

**"Run IDs are random / unsortable"**

- Install the `langsmith` package so the plugin can use `uuid7`. See the optional extra above.
