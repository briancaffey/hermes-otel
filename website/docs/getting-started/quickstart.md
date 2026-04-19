---
sidebar_position: 1
title: "Quickstart"
description: "Install hermes-otel, start a local Phoenix container, and see your first Hermes trace in under 5 minutes."
---

# Quickstart

This walks you end-to-end: install the plugin, start a local [Phoenix](https://github.com/Arize-ai/phoenix) container, and watch a real Hermes turn show up as a span tree.

## 1. Install the plugin

hermes-otel is a [Hermes Agent](https://github.com/nousresearch/hermes-agent) plugin. Install it like any other:

```bash
hermes plugins install briancaffey/hermes-otel
```

This drops the plugin at `~/.hermes/plugins/hermes_otel/` and Hermes auto-discovers it via `plugin.yaml`. The OTel runtime still needs to be installed into the Hermes venv itself — that part is a one-liner:

```bash
~/git/hermes-agent/venv/bin/pip install -e ~/.hermes/plugins/hermes_otel
```

Installing the plugin package in editable mode pulls in `opentelemetry-api`, `opentelemetry-sdk`, and `opentelemetry-exporter-otlp-proto-http` as real dependencies.

:::tip
Prefer the explicit path? The requirements are:

```bash
~/git/hermes-agent/venv/bin/pip install \
  opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http
```
:::

## 2. Start a local Phoenix

Phoenix is the fastest backend to spin up — a single container.

```bash
cd ~/.hermes/plugins/hermes_otel
docker compose -f docker-compose/phoenix.yaml up -d
```

Phoenix is now listening at:

- UI: http://localhost:6006
- OTLP/HTTP: http://localhost:6006/v1/traces

## 3. Point the plugin at Phoenix

Set the env var in your shell (or add it to `~/.hermes/.env`):

```bash
export OTEL_PHOENIX_ENDPOINT="http://localhost:6006/v1/traces"
export OTEL_PROJECT_NAME="hermes-agent"
```

That's the entire configuration. No YAML needed for the single-backend case.

## 4. Run Hermes

Start Hermes and send it a message that uses at least one tool — e.g. "list the files in my home directory":

```bash
hermes
```

The plugin prints a connection banner on startup:

```text
[hermes-otel] ✓ Phoenix connected · endpoint=http://localhost:6006/v1/traces
[hermes-otel] Registered 8 hooks
```

## 5. See the trace

Open http://localhost:6006 in a browser. Pick the `hermes-agent` project and you'll see a full span tree:

```text
session.cli
└── llm.claude-sonnet-4-6
    ├── api.claude-sonnet-4-6    prompt_tokens=312  completion_tokens=84
    │   └── tool.bash            args.command="ls -la ~"   outcome=completed
    └── api.claude-sonnet-4-6    prompt_tokens=518  completion_tokens=42
```

Each span carries:

- User message on `llm.*` as `input.value`
- Assistant response on `llm.*` as `output.value`
- Tool arguments + result on `tool.*`
- Token counts on `api.*`
- Per-turn summary (tool count, tool names, final status) on `session.*`

## What's next?

- **Pick a different backend?** → [Backends overview](/backends/overview)
- **Send to several backends at once?** → [Multi-backend fan-out](/backends/multi-backend)
- **Control sampling, previews, privacy?** → [Configuration](/configuration/overview)
- **Understand what each span means?** → [Architecture](/architecture/overview)

:::info Something not showing up?
Enable debug logging — `export HERMES_OTEL_DEBUG=true` — and check `~/.hermes/plugins/hermes_otel/debug.log`. Per-span start/end, parent nesting, token counts, and HTTP payloads all land there.
:::
