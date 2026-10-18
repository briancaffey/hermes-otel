---
sidebar_position: 2
title: "Installation"
description: "Install hermes-otel from the Hermes plugin catalog or from this repository, enable it, keep it updated, and verify it loaded."
---

# Installation

hermes-otel has two moving parts:

1. **The plugin files**, Python source at `~/.hermes/plugins/hermes_otel/`. Hermes discovers them via `plugin.yaml`.
2. **The Python dependencies**: the `opentelemetry-*` packages and PyYAML. They must be importable from the hermes-agent venv (the virtual environment whose interpreter runs `hermes`).

Since Hermes 0.21 the second part is automatic. `hermes plugins install` and `hermes plugins enable` read `python_dependencies` from `plugin.yaml`, install them into Hermes' venv (pinned against Hermes' own lock file), and install them again after every `hermes update`. Hermes' lock already pins the OTel SDK that its optional `otlp` extra uses, so the plugin's `>=1.35,<2` requirements resolve against it.

## What leaves your machine

Span data, including prompts, tool arguments and tool output, goes only to the OTLP backends you configure. By default the content is complete and unclipped (`content_capture: full`); `content_capture: preview` clips it to 1200 characters and `content_capture: "off"` sends none, keeping only the structure ([Privacy mode](/configuration/privacy)). With no backend configured, the plugin keeps a local SQLite store under `$HERMES_HOME` for the dashboard and sends nothing. The catalog entry's description says the same. The only packages installed into the Hermes venv are `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-exporter-otlp-proto-grpc` and PyYAML.

## From the plugin catalog

```bash
hermes plugins install hermes_otel
hermes plugins enable hermes_otel
```

:::note Two names
The catalog entry and the manifest share the name `hermes_otel`; it is also the install directory and the id you `enable`. Only the GitHub repository is spelled `hermes-otel`. `hermes plugins list` shows a catalog install as `catalog:community@<sha>` with the pinned version, which lags the newest release until a sha-bump PR lands upstream.
:::

## From this repository

To run the newest `main` before it reaches the catalog pin:

```bash
hermes plugins install briancaffey/hermes-otel/hermes_otel --enable
```

Note the trailing `/hermes_otel`: that is the plugin package inside the repo, and Hermes installs
just that subdirectory. It still lands at `~/.hermes/plugins/hermes_otel/`; the destination comes
from `plugin.yaml`, not from the path you typed. The Python dependencies are installed automatically here too.

:::note What actually gets installed
About 90 files, roughly 1.6 MB: the Python modules, `plugin.yaml`, the bundled skill and the dashboard tab.
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

**Catalog installs** are pinned to a reviewed commit. `hermes plugins update hermes_otel` compares the installed commit with the catalog pin and reinstalls at the new one when they differ; nothing moves until a sha-bump lands in the catalog.

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

PyYAML, which reads [`hermes_otel.yaml`](/configuration/yaml), is a declared dependency and is installed with the plugin. Without it only env vars and defaults apply.

| Extra | Install | What it does |
|---|---|---|
| `pynvml` / `amdsmi` | `pip install pynvml` (NVIDIA), or the `amdsmi` that matches your ROCm | GPU readings for [host metrics](/configuration/host-metrics). CPU readings need only `psutil`, which ships with hermes-agent. |

## Requirements

- **Python ≥ 3.9** for the plugin itself (CI tests 3.9, 3.11 and 3.13); Hermes' own venv is 3.11+.
- **Hermes Agent ≥ 0.21**, declared as `requires_hermes` in the manifest. Older builds still load the plugin (newer hooks register defensively) but are untested and need the manual dependency install.
- **One OTLP-compatible backend**, local via Docker Compose or a cloud endpoint. See [Backends overview](/backends/overview). Without one the plugin runs in live-only mode (in-process dashboard store).

## Verifying the install

When Hermes starts up, the plugin prints a startup banner:

```text
[hermes-otel] ✓ Phoenix at http://localhost:6006/v1/traces (traces only)
[hermes-otel] ✓ content_capture=full — complete prompts and responses on every api.* span (set content_capture: preview or off to reduce)
[hermes-otel] ✓ Live dashboard store active
[hermes-otel] ✓ Phoenix reachable at localhost:6006 (<n> ms)
[hermes-otel] Registered 34 hooks
```

The reachability line comes from a background thread, so it can appear later. 34 is every hook `plugin.yaml` declares; a Hermes build that rejects a hook name it does not know registers fewer.

If you see `Registered 0 hooks` or no banner at all:

- Check `hermes plugins list` shows `hermes_otel` as enabled.
- Confirm the OTel SDK imports from your hermes venv: `~/git/hermes-agent/venv/bin/python -c "import opentelemetry.sdk"`. A missing SDK logs `✗ OpenTelemetry packages not available`.
- Turn on debug logging: `export HERMES_OTEL_DEBUG=true` and re-run; see [Debug logging](/development/debug-logging).

## Uninstalling

```bash
hermes plugins remove hermes_otel

# Optionally remove the OTel packages (if nothing else uses them)
~/git/hermes-agent/venv/bin/pip uninstall \
  opentelemetry-api opentelemetry-sdk \
  opentelemetry-exporter-otlp-proto-http opentelemetry-exporter-otlp-proto-grpc
```

Or leave the plugin in place and disable it with `hermes plugins disable hermes_otel` or `HERMES_OTEL_ENABLED=false`.
