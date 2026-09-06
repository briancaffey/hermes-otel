## hermes-otel installed

One more step: the OpenTelemetry packages must be importable from the
**virtualenv that runs `hermes`** — the plugin is imported into that process,
not into a venv of its own.

```bash
/path/to/hermes-agent/venv/bin/pip install -r \
  ~/.hermes/plugins/hermes_otel/requirements.txt
```

Optional: `langsmith` (time-ordered uuid7 run IDs, LangSmith backend) and
`pyyaml` (enables `config.yaml`; env vars and defaults work without it).
For `host_metrics: true` (CPU/GPU metrics + per-tool utilization) the sampler
uses `psutil`, which hermes-agent already installs; GPU readings additionally
need `pynvml` (NVIDIA) or `amdsmi` matching your ROCm stack (AMD).

### Point it at a backend

Pick one and export it before starting Hermes:

| Backend | Environment |
|---|---|
| Phoenix | `OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces` |
| Grafana LGTM | `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces` |
| Langfuse | `OTEL_LANGFUSE_ENDPOINT` + `OTEL_LANGFUSE_PUBLIC_API_KEY` + `OTEL_LANGFUSE_SECRET_API_KEY` |
| LangSmith | `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` |

Telemetry shaping (sampling, preview sizes, resource attributes) is optional and
lives in `config.yaml`. Keep it at `$HERMES_HOME/hermes_otel.yaml` rather than in
this directory — reinstalling the plugin replaces this directory wholesale.

### Verify

Start Hermes and look for the banner:

```text
[hermes-otel] Phoenix connected - endpoint=http://localhost:6006/v1/traces
[hermes-otel] Registered 13 hooks
```

Nothing showing up? `export HERMES_OTEL_DEBUG=true` writes a per-span log to
`debug.log` in this directory.

Docs: https://briancaffey.github.io/hermes-otel/
