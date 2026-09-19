# hermes-otel

OpenTelemetry plugin for [Hermes Agent](https://github.com/nousresearch/hermes-agent). Every Hermes turn — model calls, API requests, tool calls, skill loads, approvals, delegated sub-agents — becomes one OTel trace, with token / cost / latency metrics and (optionally) logs, exported to any OTLP/HTTP backend.

**Docs:** [briancaffey.github.io/hermes-otel](https://briancaffey.github.io/hermes-otel/) · [Quickstart](https://briancaffey.github.io/hermes-otel/getting-started/quickstart) · [Backends](https://briancaffey.github.io/hermes-otel/backends/overview) · [Configuration](https://briancaffey.github.io/hermes-otel/configuration/overview) · [Reference](https://briancaffey.github.io/hermes-otel/reference/span-attributes)

## What a turn looks like

```text
agent / cron                          ← the turn (root); turn summary at the end
├── skill.{name}                      ← a loaded skill; spans load → turn end
└── llm.{model}                       ← one run_conversation
    ├── api.{model}                   ← one HTTP round-trip; tokens, cost, finish reason
    │   ├── tool.{name}               ← each tool call; args, result, outcome
    │   └── approval.{pattern}        ← human / smart-guardian approval wait
    ├── subagent.{role}               ← delegate_task; the child's own trace nests here
    │   └── agent → llm → api → tool …
    └── api.{model}                   ← final response
```

| Span | Kind | Carries |
|---|---|---|
| `agent` / `cron` | AGENT | Session kind and id, completion / interruption status, the per-turn summary (tools, targets, commands, outcomes, skills, API call count, final status) |
| `skill.{name}` | CHAIN | A skill that loaded successfully — name, source (`skill_view` / path read), path; open until the turn ends |
| `llm.{model}` | LLM | Model, provider, user message (input), assistant response (output) |
| `api.{model}` | LLM | Token counts in both conventions (incl. cache and reasoning buckets), duration, finish reason, request parameters. On failure: `ERROR` + exception + retry metadata |
| `tool.{name}` | TOOL | Args, result, `hermes.tool.outcome` (`completed` / `error` / `timeout` / `blocked` / `cancelled`), inferred target / command, CPU/GPU utilization with host metrics |
| `approval.{pattern}` | CHAIN | Decision wait time, choice (`once` / `session` / `always` / `deny` / `timeout` / `smart_approve` / `smart_deny`), who decided |
| `subagent.{role}` | AGENT | Role, goal, status, duration, summary; the child run is nested so a multi-agent run is one connected trace |

Attributes are emitted in **both** conventions — OpenInference (`llm.*`, `input.value`) for Phoenix and OTel GenAI (`gen_ai.*`) for Langfuse, Weave and generic dashboards — so no backend needs custom mapping. Full lists: [span attributes](https://briancaffey.github.io/hermes-otel/reference/span-attributes) · [metrics](https://briancaffey.github.io/hermes-otel/reference/metrics) · [hooks](https://briancaffey.github.io/hermes-otel/reference/hooks).

## Backends

Tested with: [Phoenix](https://briancaffey.github.io/hermes-otel/backends/phoenix) · [Langfuse](https://briancaffey.github.io/hermes-otel/backends/langfuse) · [LangSmith](https://briancaffey.github.io/hermes-otel/backends/langsmith) · [SigNoz](https://briancaffey.github.io/hermes-otel/backends/signoz) · [Jaeger](https://briancaffey.github.io/hermes-otel/backends/jaeger) · [Grafana Tempo](https://briancaffey.github.io/hermes-otel/backends/tempo) · [Grafana LGTM](https://briancaffey.github.io/hermes-otel/backends/lgtm) · [Uptrace](https://briancaffey.github.io/hermes-otel/backends/uptrace) · [OpenObserve](https://briancaffey.github.io/hermes-otel/backends/openobserve) · [Parseable](https://briancaffey.github.io/hermes-otel/backends/parseable) · [Honeycomb](https://briancaffey.github.io/hermes-otel/backends/honeycomb) · [W&B Weave](https://briancaffey.github.io/hermes-otel/backends/weave).

Any OTLP/HTTP endpoint works as `type: otlp`. Several can be fed at once, each with its own export queue. Which backend carries which signal (Phoenix, Jaeger, Tempo, Langfuse and Weave are traces-only) and the ready-made Compose stacks under [`docker-compose/`](docker-compose/) are in the [backends overview](https://briancaffey.github.io/hermes-otel/backends/overview).

## Install

```bash
hermes plugins install briancaffey/hermes-otel/hermes_otel
~/git/hermes-agent/venv/bin/pip install -r ~/.hermes/plugins/hermes_otel/requirements.txt
```

The trailing `/hermes_otel` is the plugin package inside this repo; Hermes installs just that directory (about 40 files) to `~/.hermes/plugins/hermes_otel/`. The OTel packages must live in the virtualenv that runs `hermes` — Hermes never installs plugin dependencies for you. Details, upgrades and troubleshooting: [Installation](https://briancaffey.github.io/hermes-otel/getting-started/installation).

## Configure

One backend needs nothing but an environment variable, e.g. `OTEL_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces`. For anything more, create `~/.hermes/hermes_otel.yaml`:

```yaml
project_name: hermes-agent
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces          # traces
  - type: lgtm
    endpoint: http://localhost:4318/v1/traces          # traces + metrics + logs
    metrics: true
  - type: honeycomb
    api_key: ${HONEYCOMB_API_KEY}                      # ${VAR} is expanded at load
capture_logs: true
```

[`config.yaml.example`](config.yaml.example) in this repository documents every knob; every scalar knob is also a `HERMES_OTEL_*` environment variable. See the [config schema](https://briancaffey.github.io/hermes-otel/reference/config-schema), the [env var reference](https://briancaffey.github.io/hermes-otel/reference/env-vars), and the guides on [privacy](https://briancaffey.github.io/hermes-otel/configuration/privacy), [sampling](https://briancaffey.github.io/hermes-otel/configuration/sampling), [logs](https://briancaffey.github.io/hermes-otel/configuration/logs) and [host & GPU metrics](https://briancaffey.github.io/hermes-otel/configuration/host-metrics).

Not seeing data? `HERMES_OTEL_DEBUG=true` writes a per-span log — see [debug logging](https://briancaffey.github.io/hermes-otel/development/debug-logging). The plugin also ships a Hermes skill, `hermes_otel:observability`, that walks the agent itself through setup and querying.

## How it works

Hermes fires lifecycle hooks; the plugin maps them onto spans, metrics and logs through one `TracerProvider` fanned out to every configured backend, and never blocks the agent: span end is a non-blocking enqueue, exporters run on their own threads, hooks fail open. The turn summary, tool identity inference, orphan sweep and batch export are described under [Architecture](https://briancaffey.github.io/hermes-otel/architecture/overview); the W3C `traceparent` forwarded to MCP servers under [MCP trace propagation](https://briancaffey.github.io/hermes-otel/configuration/mcp-trace-propagation); known gaps under [Limitations](https://briancaffey.github.io/hermes-otel/reference/limitations).

## Repository layout

| Path | Role |
|---|---|
| `hermes_otel/` | The plugin — the only directory Hermes installs (runtime modules, `plugin.yaml`, the bundled skill, the dashboard bundle) |
| `website/` | The Docusaurus docs site |
| `tests/` | Unit and integration tiers run in CI; `e2e/` and `smoke/` need real backends |
| `docker-compose/` | Backend stacks for local development |
| `dashboard-ui/` | TSX sources for the dashboard tab; `npm run build` writes into `hermes_otel/dashboard/dist/` |
| `scripts/` | The Hermes plugin security scanner and the docs generators CI runs |
| `marketing/`, `archive/` | Launch video and article; historical design notes |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — the exact CI gate to run locally, the hook / span conventions, and how docs count as acceptance criteria. Releases are cut by release-please from conventional commits ([releasing](https://briancaffey.github.io/hermes-otel/development/releasing)).

## License

Apache-2.0 — see [LICENSE](LICENSE).
