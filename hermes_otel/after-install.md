## hermes-otel installed

Hermes 0.21+ has already installed the OpenTelemetry packages this plugin
imports into its own virtualenv and will re-apply them after `hermes update`.
Enable the plugin if you have not (`hermes plugins enable hermes_otel`) and
restart the gateway if one is running.

Fallback, only if you installed with `--no-deps`, disabled lazy installs, or
run a Hermes older than 0.21:

```bash
/path/to/hermes-agent/venv/bin/pip install -r ~/.hermes/plugins/hermes_otel/requirements.txt
```

Optional: `langsmith` (LangSmith backend, uuid7 run IDs) and `pyyaml`
(enables `hermes_otel.yaml`; env vars and defaults work without it).
`host_metrics: true` uses `psutil` (ships with hermes-agent); GPU readings
additionally need `pynvml` (NVIDIA) or `amdsmi` matching your ROCm stack.

### What is captured

By default (`content_capture: full`) every model call's complete prompt and
response go to your backends, unclipped. `content_capture: preview` keeps
only 1200-character previews; `off` records no content at all. Set it in
`$HERMES_HOME/hermes_otel.yaml` or with `HERMES_OTEL_CONTENT_CAPTURE`. The
dashboard's OTel → Settings tab shows what is in force.

### Point it at a backend

Pick one and export it before starting Hermes:

| Backend | Environment |
|---|---|
| Phoenix | `OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces` |
| Grafana LGTM / any OTLP collector | no env-var mode: add `backends: [{type: lgtm, endpoint: http://localhost:4318/v1/traces, metrics: true}]` to `$HERMES_HOME/hermes_otel.yaml` |
| Langfuse | `OTEL_LANGFUSE_ENDPOINT` + `OTEL_LANGFUSE_PUBLIC_API_KEY` + `OTEL_LANGFUSE_SECRET_API_KEY` |
| LangSmith | `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` |

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
