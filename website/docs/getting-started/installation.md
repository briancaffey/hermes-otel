---
sidebar_position: 2
title: "Installation"
description: "All the ways to install hermes-otel — via hermes plugins, editable pip, or a manual dependency install."
---

# Installation

hermes-otel has two moving parts:

1. **The plugin files** — Python source at `~/.hermes/plugins/hermes_otel/`. Hermes discovers these automatically via `plugin.yaml`.
2. **The OpenTelemetry runtime** — the `opentelemetry-*` packages, which must be importable from the hermes-agent venv (the same interpreter that runs `hermes`).

:::info Why two installs?
Plugins live in `~/.hermes/plugins/` so they can be swapped without reinstalling Hermes, but they run inside Hermes' own Python process — so their runtime dependencies need to sit in the venv that launches `hermes`.
:::

## Recommended: `hermes plugins install`

```bash
hermes plugins install briancaffey/hermes-otel
```

This clones the repo into `~/.hermes/plugins/hermes_otel/`. Then install the OTel runtime into the hermes-agent venv:

```bash
~/git/hermes-agent/venv/bin/pip install -e ~/.hermes/plugins/hermes_otel
```

Editable mode is the cleanest option because:

- It pulls `opentelemetry-api`, `opentelemetry-sdk`, and `opentelemetry-exporter-otlp-proto-http` as declared dependencies.
- `pip show hermes-otel` reports a real version, which debug logs reference.
- Updating is a single `git pull` in the plugin directory.

## Manual dependency install

If you'd rather not install the plugin package into the venv, the three runtime dependencies are enough:

```bash
~/git/hermes-agent/venv/bin/pip install \
  opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http
```

## Optional extras

| Extra | Install | What it does |
|---|---|---|
| `langsmith` | `pip install langsmith` | Enables [LangSmith](/backends/langsmith) as a backend and gives you time-ordered `uuid7` run IDs. |
| `yaml` | `pip install pyyaml` | Enables [`config.yaml`](/configuration/yaml) parsing. Without it, only env vars + defaults apply. |

## Requirements

- **Python ≥ 3.9** (the plugin tests against 3.11 and 3.13 in CI).
- **Hermes Agent** with plugin support — modern versions auto-register plugins found under `~/.hermes/plugins/`.
- **One OTLP-compatible backend** — local via Docker Compose, or a cloud endpoint. See [Backends overview](/backends/overview).

## Verifying the install

When Hermes starts up, the plugin prints a startup banner:

```text
[hermes-otel] ✓ Phoenix connected · endpoint=http://localhost:6006/v1/traces
[hermes-otel] Registered 8 hooks
```

If you see `Registered 0 hooks` or no banner at all:

- Check `~/.hermes/plugins/hermes_otel/plugin.yaml` is intact.
- Confirm the OTel packages import from your hermes venv — `~/git/hermes-agent/venv/bin/python -c "import opentelemetry"`.
- Turn on debug logging: `export HERMES_OTEL_DEBUG=true` and re-run — see [Debug logging](/development/debug-logging).

## Uninstalling

```bash
# Remove the plugin
rm -rf ~/.hermes/plugins/hermes_otel

# Optionally remove OTel deps (if nothing else uses them)
~/git/hermes-agent/venv/bin/pip uninstall \
  opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Or leave the plugin in place and disable it with `HERMES_OTEL_ENABLED=false` — no uninstall required.
