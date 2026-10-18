---
sidebar_position: 3
title: "Environment variables"
description: "Where to set environment variables and how they combine with the yaml config."
---

# Environment variables

Two kinds of variables matter:

- **`HERMES_OTEL_<FIELD>`** overrides one field of `~/.hermes/hermes_otel.yaml` (`HERMES_OTEL_SAMPLE_RATE` overrides `sample_rate`).
- **Backend variables** (`OTEL_PHOENIX_ENDPOINT`, `OTEL_LANGSMITH_API_KEY`, …) select a single backend, and only when the file has no `backends:` list.

## The complete list

Every variable, grouped by purpose, is in the [env var reference](/reference/env-vars) — backend selection, the `HERMES_OTEL_*` overrides (generated from the config dataclass), paths and debug. This page only covers where to set them and how they combine with the yaml file.

## Precedence

For every field: `HERMES_OTEL_<FIELD>` env var → value in `~/.hermes/hermes_otel.yaml` → built-in default. Maps (`headers`, `global_tags`, `resource_attributes`, `common_attributes`, `log_logger_levels`) and `backends` can only be set in the file. List fields (`disable_metrics`, `disable_spans`, `log_exclude_loggers`) take a comma-separated value: `HERMES_OTEL_DISABLE_SPANS=approval,skill`. Booleans accept `true`/`1`/`yes`/`on` and `false`/`0`/`no`/`off` (case-insensitive). A value that does not parse logs a warning and is ignored, so the file value or the default applies.

## Where to set them

The easiest place is `~/.hermes/.env` (or `<profile home>/.env` for a named profile), which Hermes loads on startup:

```
OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
OTEL_PROJECT_NAME=hermes-agent
HERMES_OTEL_CONTENT_CAPTURE=preview
HERMES_OTEL_SAMPLE_RATE=0.25
```

Hermes loads that file **over** the shell: a variable set in both takes the `.env` value. To try a value from the shell, first remove the line from `.env`.

You can also export variables in your shell profile (`~/.bashrc`, `~/.zshrc`) for any variable `.env` does not set.

:::tip
Prefer `~/.hermes/.env` for per-machine config. Per-shell exports are fine for experimentation but drift from what you've committed to `config.yaml`.
:::
