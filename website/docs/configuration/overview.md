---
sidebar_position: 1
title: "Overview"
description: "How to configure hermes-otel: env vars for backends, config.yaml for shaping, precedence rules."
---

# Configuration overview

hermes-otel has two configuration surfaces:

1. **Environment variables**: pick a single backend, hold secrets, and override any scalar setting (`HERMES_OTEL_*`).
2. **`config.yaml`** (the file is `hermes_otel.yaml`): shape the telemetry (content capture, sampling, resource attributes, batch tuning) and list several backends.

Both are optional. With nothing set, the plugin runs with its defaults and keeps telemetry in the local live store for the dashboard. One backend variable (for example `OTEL_PHOENIX_ENDPOINT`) is enough to export.

## Precedence

Per-field precedence, highest → lowest:

1. **`HERMES_OTEL_*` env var**
2. **`config.yaml` value**
3. **Built-in default**

So a value set in the env var always wins over `config.yaml`, which always wins over the default. An env value that does not parse is ignored with a warning. Maps and the `backends:` list have no env var; see [Environment variables](/configuration/environment-variables).

## Where does `config.yaml` live?

Three locations, checked in this order:

| Order | Location | Notes |
|---|---|---|
| 1 | `$HERMES_OTEL_CONFIG` | An explicit path. Used even if the file is missing, so a typo surfaces instead of silently falling back. |
| 2 | `$HERMES_HOME/hermes_otel.yaml` | **Recommended.** Outside the plugin directory, so it survives an upgrade. `$HERMES_HOME` defaults to `~/.hermes`. |
| 3 | `$HERMES_HOME/plugins/hermes_otel/config.yaml` | The historical location. Still read, but **replaced on every reinstall** — see [Upgrading](/getting-started/installation#upgrading). |

If both 2 and 3 exist, 2 wins and the plugin logs a warning naming the shadowed copy.

A fully-annotated template ships in the repository as `config.yaml.example`:

```bash
curl -o ~/.hermes/hermes_otel.yaml \
  https://raw.githubusercontent.com/briancaffey/hermes-otel/main/config.yaml.example
```

Already have one in the plugin directory? Move it somewhere durable:

```bash
mv ~/.hermes/plugins/hermes_otel/config.yaml ~/.hermes/hermes_otel.yaml
```

In string values, `${VAR}` is replaced with the environment variable's value when the file loads. An unset variable stays as the literal text and logs one warning.

`config.yaml` is gitignored in the plugin repo (so local endpoints and any inline secrets don't get committed). Only `config.yaml.example` is tracked.

## Requires `PyYAML`

The plugin declares `PyYAML` in its manifest's `python_dependencies`, so Hermes installs it with the OpenTelemetry packages when the plugin is enabled. Hermes itself uses `ruamel.yaml`, so its runtime does not have PyYAML otherwise. If PyYAML is missing (the plugin was copied in by hand, or installed with dependency preparation skipped), the plugin logs one warning naming the file and falls back to env vars + defaults; `hermes plugins enable hermes_otel` prepares the declared dependencies.

Malformed YAML logs a single warning at startup and falls back to defaults — it won't crash Hermes.

## The two modes

**Single-backend (env-var-driven):**
Set one backend's env vars. If several are set, the first match in a fixed order wins (LangSmith first, Phoenix last; the order is `ENV_PRIORITY` in `hermes_otel/backend_specs.py`). No `config.yaml` needed for the backend; an optional `config.yaml` shapes telemetry.

**Multi-backend (YAML-driven):**
Set `backends:` in `config.yaml` with one or more entries. Env-var detection is **skipped**. An invalid entry is skipped with a warning; when every entry is invalid the plugin runs live-only (the in-process dashboard store, as with no backend configured) unless `dashboard_live` is off. See [Multi-backend fan-out](/backends/multi-backend).

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

## When a change takes effect

The plugin reads its configuration when Hermes loads it: at the start of every `hermes` process, and again when Hermes reloads its plugins (a profile switch, `hermes update`). A gateway started before an edit keeps its old values until it restarts or reloads.

Inside one process, the plugin decides what a re-initialisation means by **fingerprint**: a SHA-256 over the resolved configuration (file, `HERMES_OTEL_*` overrides and defaults, the `backends:` list included), normalised so that key order and tuple-versus-list spellings do not count, with every secret value replaced by a stable hash of itself. The same fingerprint as the live pipeline makes the re-initialisation a no-op: the exporters keep running and nothing buffered is touched. A different fingerprint (a knob changed, a backend added, a project renamed, a key rotated) flushes the live pipeline, shuts it down and builds a new one from the new configuration; the transition is logged with both short fingerprints (`config changed (0a1b2c3d4e5f → 9f8e7d6c5b4a)`). Spans still open at that moment are ended with `hermes.span.ended_by=sweep` and exported by the old pipeline before it stops.

The **Settings** tab of the dashboard shows the short fingerprint of the configuration it resolves next to the plugin version, so you can tell whether the gateway runs the configuration you are looking at. Credentials named by a `<field>_env` variable are read when the pipeline is built and are not part of the fingerprint; the variable's *name* is.

## Disabling the plugin without uninstalling

Two options:

```bash
export HERMES_OTEL_ENABLED=false     # Env-var kill switch
```

Or in `config.yaml`:

```yaml
enabled: false
```

Either way, the plugin logs `Disabled via config (enabled=false)` and stops: no pipeline or exporters are built, no hooks are registered, no spans are created. The plugin module still imports the OTel packages. Set it back to `true` and restart Hermes to turn it on.
