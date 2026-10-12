---
sidebar_position: 10
title: "Query your traces over MCP"
description: "Let Hermes, Claude Code or Cursor search hermes-otel traces through Traceloop's opentelemetry-mcp server: which backends qualify, how to register it, what a Hermes turn looks like through its tools, and the caveats of v0.2.2."
---

# Query your traces over MCP

[traceloop/opentelemetry-mcp-server](https://github.com/traceloop/opentelemetry-mcp-server)
(`opentelemetry-mcp`, Apache-2.0, v0.2.2) is a [FastMCP](https://gofastmcp.com) server that
lets any MCP client search traces and aggregate GenAI token usage from a tracing backend.
It reads **Jaeger**, **Grafana Tempo** and Traceloop's cloud, and it understands the
attributes hermes-otel already emits: it classifies model spans by `gen_ai.system`, reads
`gen_ai.request.model` and the `gen_ai.usage.*` keys, finds tools through
`traceloop.span.kind` and groups by `service.name` (`hermes-agent`). No plugin change is
needed; point it at the same Jaeger or Tempo the plugin exports to and the tools work.

Everything on this page was observed on 2026-10-11 with `opentelemetry-mcp==0.2.2`,
Hermes v0.21.6 and the plugin at 1.24.0, against the repo's `docker-compose/jaeger` and
`docker-compose/tempo` stacks. The caveats at the end matter; read them before trusting
a request count.

## Where it fits

The bundled [observability skill](/skill) and its `otel.py` read the **live store** and
every configured backend, for **traces, metrics and logs**. The MCP server is
**traces only**, over Jaeger and Tempo, but it is a standard MCP surface: Claude Code,
Cursor, Hermes itself or any other client can use it without knowing anything about the
plugin. Use the skill for metrics, logs, SQL and the live store; use the MCP server when
an outside client should read the traces.

## Backends that qualify

| Backend | `BACKEND_TYPE` | `BACKEND_URL` for the repo's stack | Status |
|---|---|---|---|
| [Jaeger](/backends/jaeger) (v1, `docker-compose/jaeger`) | `jaeger` | `http://localhost:16686` | verified, all eleven tools answer |
| [Grafana Tempo](/backends/tempo) (`docker-compose/tempo`) | `tempo` | `http://localhost:3210` | verified, all eleven tools answer |
| Jaeger **v2** (`docker-compose/jaeger-v2`) | `jaeger` | `http://localhost:16696` | **does not work**: every call returns `404 Not Found` for `/api/services` and `/api/traces`; v2 serves only `/api/v3/...` (same break the dashboard adapter fixed in [#245](https://github.com/briancaffey/hermes-otel/issues/245)) |
| [Grafana LGTM](/backends/lgtm), [Gigapipe](/backends/gigapipe) (Tempo API), [VictoriaTraces](/backends/victoria), [GreptimeDB](/backends/greptime) (Jaeger-compatible APIs) | `tempo` / `jaeger` | the stack's query port | the APIs exist; **not verified** with this server |
| Traceloop cloud | `traceloop` | `https://api.traceloop.com` + `BACKEND_API_KEY` | not used here |

Point the server at the **query** port (Jaeger UI 16686, Tempo HTTP 3200), not at the OTLP
ingest port the plugin exports to.

## Register it in Hermes

Hermes auto-registers MCP servers only from portable plugin packages, not from native
plugins like hermes-otel, so the server is added once. The plugin's query tool derives
the registration from the backends you already configured, so you never look a query
port up:

```bash
python3 ~/.hermes/plugins/hermes_otel/skills/observability/scripts/otel.py mcp-config
```

It lists every configured entry (Jaeger and Tempo entries are readable; LGTM counts as
Tempo on port 3200; everything else says why it is not), picks the `query_backend` pin or
the first readable one (`--source <name>` to choose), and prints the `mcp_servers:` block,
the `hermes mcp add` command, the Claude Code command and the `mcp.json` shape below, all
with the version pinned. Nothing secret is copied: the server's environment gets
`BACKEND_TYPE` and `BACKEND_URL` only. The query port is the entry's `query_port`, the
same key the dashboard adapter reads, or the stack default; the repo's Tempo compose
stack remaps 3200 to 3210, so its entry carries `query_port: 3210`. `--apply` runs the
add for you and refuses to replace an existing server of that name unless you pass
`--force`; `--json` returns the same for scripts. Or by hand:

```bash
hermes mcp add otel --command uvx --connect-timeout 60 \
  --env BACKEND_TYPE=jaeger BACKEND_URL=http://localhost:16686 \
  --args opentelemetry-mcp==0.2.2
```

`--args` must be the last option. Hermes lists the eleven tools and asks
`Enable all 11 tools? [Y/n/select]`; answer `Y`. The result in `config.yaml`:

```yaml
mcp_servers:
  otel:
    command: uvx
    args:
      - opentelemetry-mcp==0.2.2
    env:
      BACKEND_TYPE: jaeger
      BACKEND_URL: http://localhost:16686
    connect_timeout: 60.0
    enabled: true
```

For Tempo use `BACKEND_TYPE=tempo BACKEND_URL=http://localhost:3210`. `hermes mcp list`
shows the server; a new session picks it up (`/reload-mcp` in an open one). The first
`uvx` start downloads the package, which is why the connect timeout is raised to 60 s;
later starts take a few seconds.

Pin the version. `uvx opentelemetry-mcp` without `==0.2.2` resolves to whatever is newest
and the caveats below are version-specific.

## Claude Code and Cursor

Claude Code:

```bash
claude mcp add otel -e BACKEND_TYPE=jaeger -e BACKEND_URL=http://localhost:16686 \
  -- uvx opentelemetry-mcp==0.2.2
```

Cursor (`.cursor/mcp.json`, or the global `~/.cursor/mcp.json`), and the same shape for
Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "otel": {
      "command": "uvx",
      "args": ["opentelemetry-mcp==0.2.2"],
      "env": {
        "BACKEND_TYPE": "jaeger",
        "BACKEND_URL": "http://localhost:16686"
      }
    }
  }
}
```

## The tools, on a Hermes workload

The workload: six one-shot turns through a scratch `HERMES_HOME` (a shell command, a
no-tool reply, a file write and read, a skill load, a failing `ls`, and a warm-up), exported
to Jaeger and Tempo at once. The plugin's live store, which is the reference, held
**6 traces, 29 spans, 11 model calls, 89,743 tokens, 1 error** (a `tool.terminal` span).
The server exposes eleven tools; the README lists ten and adds `list_llm_tools_tool` in code.

| Tool | What it returned for the workload (identical on Jaeger and Tempo unless noted) |
|---|---|
| `list_services` | `["hermes-agent", "jaeger-all-in-one"]` on Jaeger, `["hermes-agent"]` on Tempo |
| `search_traces` (`service_name=hermes-agent`, `start_time`, `limit`) | one row per turn: `trace_id`, `operation_name: "agent"`, `duration_ms`, `status`, `span_count`, `llm_span_count`, `total_tokens`, `has_errors`. Filters `has_error: true` and `gen_ai_request_model` work |
| `get_trace` | the span tree with attributes; `llm_summary` with `llm_span_count`, `total_tokens`, `models_used`. **Short by the `api.*` spans** (see caveat 1): 3 of 5 spans for the shell turn, 4 of 7 for the file turn |
| `search_spans_tool` (`operation_name: "tool.terminal"`) | the two terminal spans, one `OK` and one `ERROR`, with `parent_span_id`. The `filters` list wants `{"field", "operator": "equals", "value", "value_type": "string"}`; `exists` must omit `value` |
| `find_errors` | the failing turn, with `error_spans: [{operation_name: "tool.terminal", status: "ERROR", error_message: "Unknown error"}]` (the server reads exception events, which the plugin does not record for tool failures) |
| `get_llm_usage` | `total_prompt_tokens 88423`, `total_completion_tokens 1320`, `total_tokens 89743`, by model and by service. Tokens match the live store; **`total_requests: 12`** does not (11 real calls, caveat 1) |
| `list_llm_models` | `nvidia/nemotron-3-super-120b-a12b`, `provider: nvidia`, `request_count: 12`, first and last seen |
| `get_llm_model_stats` | request/success/error counts, `duration_ms` and token percentiles (p50, p95, p99); these describe **turns**, not API calls (caveat 1) |
| `get_llm_expensive_traces` / `get_llm_slow_traces` | the top turns by `tokens.total` / `duration_ms`, each with `models` and `status` |
| `list_llm_tools_tool` | **fails on every call** in 0.2.2: `TraceQuery limit: Input should be less than or equal to 1000 [input_value=2000]` (caveat 3) |

## Hermes reading its own traces

With the server registered, one turn:

> Use the otel MCP tools: call list_services, then call get_llm_usage with service_name hermes-agent and start_time 2026-10-11T00:00:00Z. Reply in two sentences naming the services and the total_tokens figure.

answered correctly ("The services are hermes-agent and jaeger-all-in-one. The total_tokens
for hermes-agent since 2026-10-11T00:00:00Z is 89743.") and its own trace, in Jaeger and
in the live store, shows the MCP calls as ordinary tool spans under the API call that
issued them:

```
agent
└── llm.nvidia/nemotron-3-super-120b-a12b
    ├── api.nvidia/nemotron-3-super-120b-a12b
    │   └── tool.tool_describe
    ├── api.nvidia/nemotron-3-super-120b-a12b
    │   ├── tool.mcp__otel__list_services
    │   └── tool.mcp__otel__get_llm_usage
    └── api.nvidia/nemotron-3-super-120b-a12b
```

Hermes's MCP client also emits **one single-span trace per MCP request** (`MCP send
initialize`, `MCP send tools/list`, `MCP send tools/call <tool>`, attribute
`mcp.method.name`). They reach the backends and the live store as separate traces, not
linked to the turn; that is the gap described in [MCP trace propagation](/configuration/mcp-trace-propagation).

## Caveats in v0.2.2 {#caveats}

1. **Every `api.*` span is dropped, on both backends.** The plugin sets
   `gen_ai.response.finish_reasons` as an array, which is what the GenAI conventions
   specify. The server's `SpanAttributes` model stringifies the value (`'["tool_calls"]'`
   from Jaeger, `"{'values': [{'stringValue': 'stop'}]}"` from Tempo), then rejects it
   as "Input should be a valid list", and the whole span is discarded with
   `Error parsing span` in the server log. Consequences: `get_trace` is short by one span
   per model round-trip; `request_count` counts the `agent` and `llm.*` spans, which carry
   `gen_ai.system`, so it reports **2 per turn** instead of the real number of API calls
   (12 instead of 11 here); latency and token percentiles describe turns. Token **totals
   are right** because the `agent` root carries the turn roll-up
   ([`root_usage`](/backends/quirks), on for Jaeger and Tempo).
2. **Jaeger v2 returns 404.** The server calls the classic `/api/traces`, `/api/services`
   routes; v2 serves only `/api/v3/...`. Use `docker-compose/jaeger`, not `jaeger-v2`.
3. **`list_llm_tools_tool` never works**: it asks its own query layer for 2000 spans and
   the layer caps at 1000. Ask for tool spans with `search_spans_tool` and
   `operation_name: "tool.<name>"` instead, or filter on `traceloop.span.kind == "tool"`:
   since hermes-otel 1.26.0 that attribute carries Traceloop's lowercase vocabulary
   (`agent`, `tool`, `task`, `unknown`). Spans exported by earlier releases hold the
   OpenInference spelling (`TOOL`) there and do not match
   ([#382](https://github.com/briancaffey/hermes-otel/issues/382)).
4. **Tempo ids.** `search_spans_tool` returns trace and span ids base64-encoded
   (`Koy2F0Bucnz3poNZUWbI8A==`) while `search_traces` returns hex, and a hex trace id with
   a leading zero loses it (`02f734c9…` becomes `2f734c9…`). Copy ids from `search_traces`
   and pad to 32 hex characters before `get_trace`.

The upstream fixes are tracked in [#306](https://github.com/briancaffey/hermes-otel/issues/306).

## Privacy

The server returns whatever the backend stores. With the default
`content_capture: full` that is every prompt and response, delivered into the tool result
of whichever client asked, and when the client is Hermes those prompts come back into the
model's context and cost tokens. When an agent will read its own traces, set
`content_capture: preview` (clipped to 1200 characters) or `off`; see
[Conversation capture](/configuration/conversation-capture). Local Jaeger and Tempo have
no authentication, so anyone who can reach the query port can run these tools.

## See also

- [The observability skill](/skill): the plugin's own query path, with metrics, logs and the live store.
- [MCP trace propagation](/configuration/mcp-trace-propagation): why the MCP client's spans are separate traces.
- [Jaeger](/backends/jaeger) and [Grafana Tempo](/backends/tempo): the two verified backends.
