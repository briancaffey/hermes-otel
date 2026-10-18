---
sidebar_position: 1
title: "Quickstart"
description: "Install hermes-otel, start a local Phoenix container, and see your first Hermes trace in under 5 minutes."
---

# Quickstart

This walks you end-to-end: install the plugin, start a local [Phoenix](https://github.com/Arize-ai/phoenix) container, and watch a real Hermes turn show up as a span tree.

## 1. Install the plugin

hermes-otel is a [Hermes Agent](https://github.com/nousresearch/hermes-agent) plugin. From the plugin catalog:

```bash
hermes plugins install hermes_otel
hermes plugins enable hermes_otel
```

Or, for the newest `main` ahead of the catalog pin, from this repository:

```bash
hermes plugins install briancaffey/hermes-otel/hermes_otel --enable
```

Either way the plugin lands at `~/.hermes/plugins/hermes_otel/`, Hermes auto-discovers it via `plugin.yaml`, and Hermes 0.21+ installs the `opentelemetry-*` packages into its own venv for you. (On older Hermes builds, or after `--no-deps`, run `<hermes venv>/bin/pip install -r ~/.hermes/plugins/hermes_otel/requirements.txt` yourself; see [Installation](/getting-started/installation).)

## 2. Start a local Phoenix

Phoenix is the fastest backend to start: one container, no login.

The Compose file lives in the repository, not in the installed plugin, so fetch it first:

```bash
mkdir -p ~/hermes-otel-backends && cd ~/hermes-otel-backends
curl -fsSLO https://raw.githubusercontent.com/briancaffey/hermes-otel/main/docker-compose/phoenix/docker-compose.yaml
docker compose -f docker-compose.yaml up -d
```

Phoenix is now listening at:

- UI: http://localhost:6006
- OTLP/HTTP: http://localhost:6006/v1/traces (Phoenix takes traces only)

## 3. Point the plugin at Phoenix

Set the env var in your shell (or add it to `~/.hermes/.env`):

```bash
export OTEL_PHOENIX_ENDPOINT="http://localhost:6006/v1/traces"
export OTEL_PROJECT_NAME="hermes-agent"
```

That's the entire configuration: one endpoint variable selects one backend, no YAML needed. `OTEL_PROJECT_NAME` names the Phoenix project. To send to several backends, use a `backends:` list in `~/.hermes/hermes_otel.yaml` instead ([Multi-backend fan-out](/backends/multi-backend)).

## 4. Run Hermes

Start Hermes and send it a message that uses at least one tool — e.g. "list the files in my home directory":

```bash
hermes
```

The plugin prints a connection banner on startup:

```text
[hermes-otel] ✓ Phoenix at http://localhost:6006/v1/traces (traces only)
[hermes-otel] ✓ content_capture=full — complete prompts and responses on every api.* span (set content_capture: preview or off to reduce)
[hermes-otel] ✓ Live dashboard store active
[hermes-otel] ✓ Phoenix reachable at localhost:6006 (<n> ms)
[hermes-otel] Registered 34 hooks
```

If the third-from-last line says `⚠ Phoenix unreachable`, the container is not up yet. The hook count depends on the Hermes version; see [Installation](/getting-started/installation#verifying-the-install).

## 5. See the trace

Open http://localhost:6006 in a browser. Pick the `hermes-agent` project and you'll see a full span tree:

```text
agent
└── llm.claude-sonnet-4-6
    ├── api.claude-sonnet-4-6    prompt_tokens=312  completion_tokens=84
    │   └── tool.bash            args.command="ls -la ~"   outcome=completed
    └── api.claude-sonnet-4-6    prompt_tokens=518  completion_tokens=42
```

Each span carries:

- User message on `llm.*` as `input.value`
- Assistant response on `llm.*` as `output.value`
- The complete request and response on each `api.*` span (the default `content_capture: full`; see [Conversation capture](/configuration/conversation-capture))
- Tool arguments + result previews on `tool.*`
- Token counts on `api.*`
- Per-turn summary (tool count, tool names, final status) on the `agent` root

## What's next?

- **Pick a different backend?** → [Backends overview](/backends/overview)
- **Send to several backends at once?** → [Multi-backend fan-out](/backends/multi-backend)
- **Control sampling, previews, privacy?** → [Configuration](/configuration/overview)
- **Understand what each span means?** → [Architecture](/architecture/overview)

:::info Something not showing up?
Enable debug logging (`HERMES_OTEL_DEBUG=true`) and check `~/.hermes/plugins/hermes_otel/debug.log`. If `~/.hermes/.env` already sets `HERMES_OTEL_DEBUG`, change it there: Hermes loads that file over the shell's values. Hook firings, span start/end, token counts, one `export <backend>: … -> SUCCESS|FAILURE` line per batch and the SDK's export errors (`[sdk] …`) all land there.
:::
