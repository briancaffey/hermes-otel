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
hermes plugins install briancaffey/hermes-otel/hermes_otel
```

Note the trailing `/hermes_otel`: that is the plugin package inside the repo, and Hermes installs
just that subdirectory. It still lands at `~/.hermes/plugins/hermes_otel/` — the destination comes
from `plugin.yaml`, not from the path you typed.

Then install the OTel runtime into the hermes-agent venv, using the requirements file that ships
alongside the plugin:

```bash
~/git/hermes-agent/venv/bin/pip install -r ~/.hermes/plugins/hermes_otel/requirements.txt
```

Hermes deliberately never installs plugin dependencies for you; it prints them at install time and
leaves the venv to you.

:::note What actually gets installed
About 40 files / 500 KB: the Python modules, `plugin.yaml`, the bundled skill and the dashboard tab.
The docs site, test suite and example Compose stacks stay in the repository — they are development
material, and shipping them would put several megabytes of unused files into every Hermes install.

This also matters for Hermes ≥ v0.20, which security-scans a plugin's whole file tree before
installing it and hard-blocks on any `critical` finding. Documentation and test fixtures are graded
by the same rules as executable code, so keeping them out of the artifact is what keeps installs
working ([issue #53](https://github.com/briancaffey/hermes-otel/issues/53)).
:::

### Upgrading from an older install

Installs made before v0.12 used `briancaffey/hermes-otel` (no subdirectory) and recorded that as
the plugin's source, so `hermes plugins update` still points at the repository root. Re-install
once to move to the new artifact:

```bash
hermes plugins remove hermes_otel
hermes plugins install briancaffey/hermes-otel/hermes_otel
```

Your `config.yaml` lives in the plugin directory, so copy it aside first if you have one.

### Installing from a clone

Contributors can install the package into the hermes venv in editable mode, which pulls the same
dependencies and makes `pip show hermes-otel` report a real version (debug logs reference it):

```bash
git clone https://github.com/briancaffey/hermes-otel.git ~/git/hermes-otel
~/git/hermes-agent/venv/bin/pip install -e ~/git/hermes-otel
```

To have Hermes load your working copy, point the plugin directory at the package inside the clone:

```bash
ln -s ~/git/hermes-otel/hermes_otel ~/.hermes/plugins/hermes_otel
```

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
