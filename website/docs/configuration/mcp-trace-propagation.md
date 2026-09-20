---
sidebar_position: 9
title: "MCP trace propagation"
description: "Status of linking MCP-server spans into the agent's trace: implemented on the plugin side, waiting on Hermes."
---

# MCP trace propagation

When the agent calls a tool on a remote [MCP](https://modelcontextprotocol.io/)
server, that server's spans land in a **separate, unlinked trace**, so debugging
a slow or failing MCP call means correlating timestamps by hand. Linking the two
needs the agent's W3C [trace context](https://www.w3.org/TR/trace-context/) to
travel with each outbound MCP request.

:::caution Not available on any Hermes release
This page describes a feature that is **implemented in hermes-otel and waiting on
Hermes**. No released Hermes fires a hook the plugin could use to attach
headers to MCP requests, so today MCP-server spans are not linked. Nothing is
configurable and nothing breaks: the plugin's handler is dormant.
:::

## Where things stand upstream

- The plugin side is done: `hermes_otel.hooks.on_mcp_request_headers` returns
  `{"traceparent": ...}` for the session's active span, and
  `get_current_traceparent()` is public for other integrations.
- A generic `mcp_request_headers` plugin hook was proposed in
  [hermes-agent#52211](https://github.com/NousResearch/hermes-agent/issues/52211)
  (with implementation attempts in
  [#55536](https://github.com/NousResearch/hermes-agent/pull/55536) and
  [#78965](https://github.com/NousResearch/hermes-agent/pull/78965)). All three
  were closed in September 2026: the MCP Python SDK 2.x that Hermes 0.21 pins
  already carries trace context in-protocol (the request's `_meta`, per
  [SEP-414](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/414)),
  so header injection was judged redundant.
- The remaining gap, per the closing note, is that Hermes issues the MCP RPC on
  its dedicated MCP loop thread, where the agent's span is not the current
  OpenTelemetry context, so the SDK's in-protocol propagation has no parent to
  carry. That is an upstream change; hermes-otel is the concrete consumer for it.

The hook is therefore **not** declared in `plugin.yaml` and not part of the
"15 hooks" the startup banner reports on Hermes 0.21. The plugin registers it
only when the running Hermes lists `mcp_request_headers` in its hook registry;
when the registry cannot be inspected at all it stays unregistered, so the
plugin catalog's declared-vs-registered check can never see an undeclared hook.

## What will happen once Hermes supports it

1. hermes-otel registers `mcp_request_headers` and the banner shows one more hook.
2. On each outbound MCP call over HTTP/StreamableHTTP, Hermes merges the
   returned `traceparent` onto the request (stdio servers have no headers).
3. An OpenTelemetry-instrumented MCP server extracts it and its spans join the
   agent's trace: user message → LLM call → tool dispatch → MCP transport →
   the server's own work, all under one `trace_id`.

The span registry is process-global and keyed by `session_id` (not a context
variable), so the lookup works even though MCP requests run on a separate
event loop from the agent task.

## Public API

```python
from hermes_plugins.hermes_otel.hooks import get_current_traceparent

tp = get_current_traceparent(session_id)   # "00-<trace>-<span>-01" or None
```

Returns the W3C `traceparent` for the active span of the given session
(falling back to the current context-var parent, then to any single active
session), or `None` when tracing is disabled or no span is active. Safe to call
from any thread or event loop.

## See also

- [Hooks reference](/reference/hooks): which hooks are registered on which Hermes version.
- [Span hierarchy](/architecture/span-hierarchy): how the agent's spans nest.
