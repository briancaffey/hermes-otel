## hermes-otel installed

Hermes 0.21+ has already installed the OpenTelemetry packages this plugin
imports (`opentelemetry-api`, `-sdk` and `-exporter-otlp-proto-http`, 1.35 or
newer, below 2) into its own virtualenv and will re-apply them after
`hermes update`.
Enable the plugin if you have not (`hermes plugins enable hermes_otel`) and
restart the gateway if one is running.

Fallback, only if you installed with `--no-deps`, disabled lazy installs, or
run a Hermes older than 0.21:

```bash
/path/to/hermes-agent/venv/bin/pip install -r ~/.hermes/plugins/hermes_otel/requirements.txt
```

Optional: `langsmith` (LangSmith backend, uuid7 run IDs). `PyYAML`, which
reads `hermes_otel.yaml`, is a declared dependency and is installed with the
OpenTelemetry packages.
`host_metrics: true` uses `psutil` (ships with hermes-agent); GPU readings
additionally need `pynvml` (NVIDIA) or `amdsmi` matching your ROCm stack.

### What is captured

By default (`content_capture: full`) every model call's complete prompt and
response go to your backends, unclipped. `content_capture: preview` keeps
only 1200-character previews; `off` records no content at all. Set it in
`$HERMES_HOME/hermes_otel.yaml` or with `HERMES_OTEL_CONTENT_CAPTURE`. The
dashboard's OTel → Settings tab shows what is in force. Structured `hermes.*` /
GenAI log events from the hooks are off by default (`logs.events.enabled: true`
turns them on; `logs.events.content` gates prompt content on them). The local live store
(`$HERMES_HOME/hermes_otel_live.db`) and `debug.log` hold the same content and
are created owner-only (`0600`).

### Point it at a backend

Pick one and export it before starting Hermes:

| Backend | Environment |
|---|---|
| Phoenix | `OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces` |
| Grafana LGTM / any OTLP collector | no env-var mode: add `backends: [{type: lgtm, endpoint: http://localhost:4318/v1/traces, metrics: true}]` to `$HERMES_HOME/hermes_otel.yaml` |
| Langfuse | `OTEL_LANGFUSE_ENDPOINT` + `OTEL_LANGFUSE_PUBLIC_API_KEY` + `OTEL_LANGFUSE_SECRET_API_KEY` |
| LangSmith | `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` |
| Honeycomb | `OTEL_HONEYCOMB_API_KEY` |
| W&B Weave | `OTEL_WEAVE_API_KEY` + `WANDB_ENTITY` + `WANDB_PROJECT` |
| Elastic | `OTEL_ELASTIC_ENDPOINT` (+ `OTEL_ELASTIC_API_KEY` for Elastic Cloud mOTLP; omit for a local EDOT Collector) |
| OpenLIT | `OTEL_OPENLIT_ENDPOINT` (+ `OTEL_OPENLIT_API_KEY` to scope ingest to an org/project; omit for a local stack) |
| MLflow | `OTEL_MLFLOW_ENDPOINT` (+ `MLFLOW_EXPERIMENT_ID`, default `0`; traces only) |

Vendor SDK variables already in your environment (`LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY`, `HONEYCOMB_API_KEY`, `WANDB_API_KEY`) are used as credential
fallbacks but never switch export on by themselves: with no `OTEL_*` variable and
no `backends:` list, nothing leaves the machine.

Telemetry shaping (sampling, preview sizes, resource attributes) is optional and
lives in `$HERMES_HOME/hermes_otel.yaml`. Keep it there rather than in this
directory: reinstalling or updating the plugin replaces this directory wholesale.

### Verify

Start Hermes and look for the banner:

```text
[hermes-otel] ✓ Phoenix at http://localhost:6006/v1/traces (traces only)
[hermes-otel] Registered 15 hooks
```

Nothing showing up? `export HERMES_OTEL_DEBUG=true` writes a per-span log to
`debug.log` in this directory.

Docs: https://briancaffey.github.io/hermes-otel/
