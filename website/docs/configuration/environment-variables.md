---
sidebar_position: 3
title: "Environment variables"
description: "Where to set environment variables and how they combine with the yaml config."
---

# Environment variables

Most shaping vars are `HERMES_OTEL_*`-prefixed and override the corresponding field in `~/.hermes/hermes_otel.yaml`; backend vars (`OTEL_PHOENIX_ENDPOINT`, `LANGSMITH_TRACING`, …) select a single backend when no `backends:` list is configured.

## The complete list

Every variable, grouped by purpose, is in the [env var reference](/reference/env-vars) — backend selection, the `HERMES_OTEL_*` overrides (generated from the config dataclass), paths and debug. This page only covers where to set them and how they combine with the yaml file.

## Precedence

For every field: `HERMES_OTEL_<FIELD>` env var → value in `~/.hermes/hermes_otel.yaml` → built-in default. Maps (`headers`, `global_tags`, `resource_attributes`) and `backends` are yaml-only. Booleans accept `true`/`1`/`yes`/`on` and `false`/`0`/`no`/`off` (case-insensitive); anything else logs a warning and is ignored.

## Where to set them

The easiest place is `~/.hermes/.env`, which Hermes auto-loads on startup:

```
OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
OTEL_PROJECT_NAME=hermes-agent
HERMES_OTEL_CAPTURE_PREVIEWS=true
HERMES_OTEL_SAMPLE_RATE=0.25
```

Or export them in your shell profile (`~/.bashrc`, `~/.zshrc`) for a global default.

:::tip
Prefer `~/.hermes/.env` for per-machine config. Per-shell exports are fine for experimentation but drift from what you've committed to `config.yaml`.
:::
