---
sidebar_position: 9
title: "MCP trace propagation"
description: "Link MCP-server spans into the agent's trace by forwarding a W3C traceparent on outbound MCP requests."
---

# MCP trace propagation

When the agent calls a tool on a remote [MCP](https://modelcontextprotocol.io/)
server, that server's spans normally land in a **separate, unlinked trace** —
so debugging a slow or failing MCP call means correlating timestamps by hand.

This plugin can forward the active span's context as a W3C
[`traceparent`](https://www.w3.org/TR/trace-context/) header on every outbound
MCP HTTP request, so the MCP server's spans join the **same trace** as the
agent. One query in your backend then shows the whole lifecycle: user message →
LLM call → tool dispatch → MCP transport → the MCP server's own work.

## How it works

Two pieces cooperate:

1. **hermes-otel** exposes the active trace context and registers an
   `mcp_request_headers` hook that returns `{"traceparent": "..."}`.
2. **hermes-agent** invokes that hook on the MCP transport's HTTP client and
   merges the returned headers onto each outbound request.

The span registry is process-global and keyed by `session_id` (not bound to a
context var), so the lookup works even though MCP requests run on a separate
background event loop from the agent task.

:::note Requires hermes-agent support
The header injection needs the `mcp_request_headers` hook in hermes-agent.
hermes-otel registers the hook **only when the host advertises it**, so on
older builds this feature is a silent no-op — nothing to configure, nothing to
break. Trace context is propagated over **HTTP/StreamableHTTP** MCP transports
(stdio servers have no request headers).
:::

## Enabling it

Nothing to configure. When both sides support it, the hook is registered
automatically and you'll see one extra hook in the startup banner:

```
[hermes-otel] Registered 9 hooks
```

Then point the agent at an MCP server over HTTP (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  my-server:
    url: "https://my-mcp-server.example.com/mcp"
```

For the MCP-server spans to actually appear linked, the **MCP server must be
OpenTelemetry-instrumented** (extract the incoming `traceparent` and emit spans
to a backend). Most OTel ASGI/HTTP middlewares do this out of the box.

## Public API

If you're building your own integration, call the public helper directly
instead of reaching into span internals:

```python
from hermes_plugins.hermes_otel.hooks import get_current_traceparent

tp = get_current_traceparent(session_id)   # "00-<trace>-<span>-01" or None
```

It returns the W3C `traceparent` for the active span of the given session
(falling back to the current context-var parent, then to any single active
session), or `None` when tracing is disabled or no span is active. Safe to call
from any thread or event loop.

## Verifying

With an OTel-instrumented MCP server, after a tool call your backend should show
a single trace whose spans span both services — the agent's `agent` / `tool.*`
spans **and** the MCP server's request/handler spans — all sharing one
`trace_id`. If the MCP server logs request headers, you'll also see the
`traceparent` arrive on the `tools/call` request.

## See also

- [Span hierarchy](/architecture/span-hierarchy) — how the agent's spans nest.
- [Backends overview](/backends/overview) — where these traces land.
