---
sidebar_position: 2
title: "Installation"
description: "Install hermes-otel from the Hermes plugin catalog or from this repository, enable it, keep it updated, and verify it loaded."
---

# Installation

hermes-otel has two moving parts:

1. **The plugin files**, Python source at `~/.hermes/plugins/hermes_otel/`. Hermes discovers them via `plugin.yaml`.
2. **The OpenTelemetry runtime**, the `opentelemetry-*` packages, which must be importable from the hermes-agent venv (the interpreter that runs `hermes`).

Since Hermes 0.21 the second part is automatic: `hermes plugins install` and `hermes plugins enable` read `python_dependencies` from `plugin.yaml`, install them into Hermes' venv under a constraints file built from Hermes' own lock, and re-apply them after every `hermes update`. Hermes' lock already pins the OTel SDK its optional `otlp` extra uses, so the plugin's `<2` requirements resolve against it.

## From the plugin catalog

```bash
hermes plugins install hermes-otel
hermes plugins enable hermes_otel
```

:::note Two names
The catalog key is `hermes-otel`; the manifest name, and therefore the install directory and the id you `enable`, is `hermes_otel`. `hermes plugins install hermes-otel` prints the installed name. `hermes plugins list` shows the source as `catalog:community@<sha>`.
:::

:::caution Listing in progress
The catalog entry is tracked in [issue #134](https://github.com/briancaffey/hermes-otel/issues/134). Until it is merged upstream, use the repository install below; it is the same plugin, just not yet catalog-reviewed.
:::

## From this repository

```bash
hermes plugins install briancaffey/hermes-otel/hermes_otel --enable
```

Note the trailing `/hermes_otel`: that is the plugin package inside the repo, and Hermes installs
just that subdirectory. It still lands at `~/.hermes/plugins/hermes_otel/`; the destination comes
from `plugin.yaml`, not from the path you typed. The Python dependencies are installed automatically here too.

:::note What actually gets installed
About 40 files / 500 KB: the Python modules, `plugin.yaml`, the bundled skill and the dashboard tab.
The docs site, test suite and example Compose stacks stay in the repository; they are development
material, and shipping them would put several megabytes of unused files into every Hermes install.

This also matters for Hermes ≥ v0.20, which security-scans a plugin's whole file tree before
installing it and hard-blocks on any `critical` finding. Documentation and test fixtures are graded
by the same rules as executable code, so keeping them out of the artifact is what keeps installs
working ([issue #53](https://github.com/briancaffey/hermes-otel/issues/53)).
:::

## Installed is not enabled

Installing puts the files on disk. The plugin only loads once it is enabled (`--enable` at install time, or `hermes plugins enable hermes_otel` later), and a running gateway picks it up on restart.

## Manual dependency install (fallback)

Hermes leaves the venv alone when you install with `--no-deps`, when `security.allow_lazy_installs: false` is set in its config, or on releases older than 0.21. In those cases install the runtime yourself, using the requirements file that ships with the plugin:

```bash
~/git/hermes-agent/venv/bin/pip install -r ~/.hermes/plugins/hermes_otel/requirements.txt
```

## Upgrading {#upgrading}

**Catalog installs** are pinned to a reviewed commit. `hermes plugins update hermes-otel` compares the installed commit with the catalog pin and reinstalls at the new one when they differ; nothing moves until a sha-bump lands in the catalog.

**Repository installs** are a plain copy with no `.git`, so `hermes plugins update` refuses with *"was not installed from git"*. Upgrade by reinstalling:

```bash
hermes plugins install briancaffey/hermes-otel/hermes_otel --force
hermes plugins enable hermes_otel     # --force reinstall leaves it disabled
```

:::warning A reinstall replaces the plugin directory
Both paths swap the whole directory, so **anything you keep inside it is deleted**. Since 1.6.1 the
plugin keeps nothing there by itself: the durable config is `~/.hermes/hermes_otel.yaml` and the live
dashboard store is `~/.hermes/hermes_otel_live.db` (override with `HERMES_OTEL_LIVE_DB`). If you
still have a legacy `config.yaml` inside the plugin directory, move it out:

```bash
mv ~/.hermes/plugins/hermes_otel/config.yaml ~/.hermes/hermes_otel.yaml
```

`$HERMES_HOME/hermes_otel.yaml` is read automatically; see
[Where does `config.yaml` live?](/configuration/overview).
:::

### Coming from an install made before v0.12

Those used `briancaffey/hermes-otel` with no subdirectory and recorded the repository root as the
source, so neither `update` nor a plain `--force` reinstall moves them. Point the install at the
new subdirectory once:

```bash
cp ~/.hermes/plugins/hermes_otel/config.yaml ~/.hermes/hermes_otel.yaml   # if you have one
hermes plugins remove hermes_otel
hermes plugins install briancaffey/hermes-otel/hermes_otel --enable
```

## Installing from a clone

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

## Optional extras

| Extra | Install | What it does |
|---|---|---|
| `langsmith` | `pip install langsmith` | Enables [LangSmith](/backends/langsmith) as a backend and gives you time-ordered `uuid7` run IDs. |
| `yaml` | `pip install pyyaml` | Enables [`hermes_otel.yaml`](/configuration/yaml) parsing. Without it, only env vars + defaults apply. |

## Requirements

- **Python ≥ 3.9** for the plugin itself (CI tests 3.9, 3.11 and 3.13); Hermes' own venv is 3.11+.
- **Hermes Agent ≥ 0.21**, declared as `requires_hermes` in the manifest. Older builds still load the plugin (newer hooks register defensively) but are untested and need the manual dependency install.
- **One OTLP-compatible backend**, local via Docker Compose or a cloud endpoint. See [Backends overview](/backends/overview). Without one the plugin runs in live-only mode (in-process dashboard store).

## Verifying the install

When Hermes starts up, the plugin prints a startup banner:

```text
[hermes-otel] ✓ Phoenix at http://localhost:6006/v1/traces (traces only)
[hermes-otel] ✓ Live dashboard store active
[hermes-otel] Registered 13 hooks
```

(The hook count depends on the Hermes version; 13 on Hermes 0.21.)

If you see `Registered 0 hooks` or no banner at all:

- Check `hermes plugins list` shows `hermes_otel` as enabled.
- Confirm the OTel packages import from your hermes venv: `~/git/hermes-agent/venv/bin/python -c "import opentelemetry"`.
- Turn on debug logging: `export HERMES_OTEL_DEBUG=true` and re-run; see [Debug logging](/development/debug-logging).

## Uninstalling

```bash
hermes plugins remove hermes_otel

# Optionally remove the OTel packages (if nothing else uses them)
~/git/hermes-agent/venv/bin/pip uninstall \
  opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

Or leave the plugin in place and disable it with `hermes plugins disable hermes_otel` or `HERMES_OTEL_ENABLED=false`.
