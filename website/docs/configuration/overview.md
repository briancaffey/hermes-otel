---
sidebar_position: 1
title: "Overview"
description: "How to configure hermes-otel: env vars for backends, config.yaml for shaping, precedence rules."
---

# Configuration overview

hermes-otel has two configuration surfaces:

1. **Environment variables** — for backend selection + secrets.
2. **`config.yaml`** — for telemetry shaping (sampling, preview size, resource attributes, batch tuning, multi-backend fan-out).

Both are optional. The plugin ships with sensible defaults and will run with no config at all if you set a single backend env var (e.g. `OTEL_PHOENIX_ENDPOINT`).

## Precedence

Per-field precedence, highest → lowest:

1. **`HERMES_OTEL_*` env var**
2. **`config.yaml` value**
3. **Built-in default**

So a value set in the env var always wins over `config.yaml`, which always wins over the default.

## Where does `config.yaml` live?

```
~/.hermes/plugins/hermes_otel/config.yaml
```

A fully-annotated template is at `config.yaml.example` in the same directory — copy and edit:

```bash
cp ~/.hermes/plugins/hermes_otel/config.yaml.example \
   ~/.hermes/plugins/hermes_otel/config.yaml
```

`config.yaml` is gitignored in the plugin repo (so local endpoints and any inline secrets don't get committed). Only `config.yaml.example` is tracked.

## Requires `pyyaml`

YAML parsing is optional. If `pyyaml` isn't installed in the Hermes venv, the plugin silently skips the YAML file and falls back to env vars + defaults. Install it via the `yaml` extra:

```bash
~/git/hermes-agent/venv/bin/pip install pyyaml
```

Malformed YAML logs a single warning at startup and falls back to defaults — it won't crash Hermes.

## The two modes

**Single-backend (env-var-driven):**
Set exactly one of the backend env vars. First match wins. No `config.yaml` needed for the backend; optional `config.yaml` shapes telemetry.

**Multi-backend (YAML-driven):**
Set `backends:` in `config.yaml` with one or more entries. Env-var detection is **skipped**. See [Multi-backend fan-out](/backends/multi-backend).

## Quick config tour

| I want to… | See |
|---|---|
| Pick a backend | [Backends overview](/backends/overview) |
| Send to several backends at once | [Multi-backend](/backends/multi-backend) |
| Sample traces (e.g. 10%) | [Sampling](/configuration/sampling) |
| Suppress message content | [Privacy mode](/configuration/privacy) |
| Capture full conversation history | [Conversation capture](/configuration/conversation-capture) |
| Ship Python logs to Loki with trace-id correlation | [OTel logs](/configuration/logs) |
| Tune batch export for high-throughput | [Batch export tuning](/configuration/batch-export) |
| See every knob | [Full config schema](/reference/config-schema) |
| Use env vars instead of YAML | [Env vars reference](/reference/env-vars) |

## Disabling the plugin without uninstalling

Two options:

```bash
export HERMES_OTEL_ENABLED=false     # Env-var kill switch
```

Or in `config.yaml`:

```yaml
enabled: false
```

Either way, `register()` returns early after the kill-switch check. No spans are created, no hooks are attached, no OTel SDK is loaded. Flip it back on to restart.
