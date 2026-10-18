---
sidebar_position: 32
title: "otel-desktop-viewer and otel-tui"
description: "Two single-binary OTLP viewers for looking at a Hermes turn with nothing to set up: otel-desktop-viewer (browser UI, DuckDB, JSON-RPC) and otel-tui (terminal UI). Generic type: otlp."
---

# otel-desktop-viewer and otel-tui

Two single-binary OpenTelemetry viewers for the "see one turn, right now" tier,
next to [Phoenix](/backends/phoenix) and the [Aspire Dashboard](/backends/aspire).
Both are stock OTLP/HTTP receivers with no login and no auth, so hermes-otel
talks to them with the generic [`otlp`](/backends/otlp) type: nothing to
translate, all three signals on. Neither has a dashboard adapter (otel-tui has
no read API; the viewer's JSON-RPC is a follow-up if anyone asks). Issue #367.

| | [otel-desktop-viewer](https://github.com/CtrlSpice/otel-desktop-viewer) | [otel-tui](https://github.com/ymtdzzz/otel-tui) |
|---|---|---|
| What | browser UI on an embedded DuckDB; JSON-RPC 2.0 read API at `POST /rpc` | terminal UI, in memory, no read API |
| Run it | `docker-compose/otel-desktop-viewer/` (one container) or `brew install --cask otel-desktop-viewer` | `docker run -d -i -t … ymtdzzz/otel-tui` + `docker attach`, or `brew install ymtdzzz/tap/otel-tui` |
| Ports in the repo | UI **3070** (upstream 8000), OTLP/HTTP **4428** (upstream 4318) | OTLP/HTTP **4438** (upstream 4318) |
| Signals | traces, metrics, logs | traces, metrics (no exponential histograms), logs |
| Storage | DuckDB; the compose file passes `--db` so a named volume keeps it | memory; fixed caps (1000 spans per service, 1000 logs, 3000 metrics) |
| Verified | v0.5.0 (2026-08-24), 263 MB image, ~105 MB resident | 0.7.5 (2026-09-12), 144 MB image, ~30 MB resident |
| Licence | Apache-2.0 | Apache-2.0 |

## otel-desktop-viewer

```bash
docker compose -f docker-compose/otel-desktop-viewer/docker-compose.yaml up -d
# UI http://localhost:3070 — ready about a second after start
```

```yaml
backends:
  - type: otlp
    name: otel-desktop-viewer
    endpoint: http://localhost:4428/v1/traces   # /v1/metrics and /v1/logs are derived
    metrics: true
    logs: true
logs:
  capture: true   # send Python log lines as OTel logs too
```

One real `hermes` turn (2026-10-11, hermes-otel 1.24.0 branch) stored one trace
of 5 spans (`agent` root, `llm.*`, two `api.*`, `tool.terminal`), 27 log
records and 13 metric names; a later turn whose `tool.read_file` failed showed
`errorCount: 2` on its trace summary. Read it back without the UI:

```bash
NOW=$(python3 -c 'import time; print(int(time.time()*1e9))')     # times are Unix nanoseconds
curl -s -X POST http://localhost:3070/rpc -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"searchTraces\",\"params\":{\"startTime\":0,\"endTime\":$NOW}}"
# → [{"traceID", "rootSpan": {"serviceName", "name"}, "startTime", "durationNs", "spanCount", "errorCount"}, …]
curl -s -X POST http://localhost:3070/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"searchSpans","params":{"traceID":"<trace id>"}}'
```

What to know (details and the full method list in the folder's
[README](https://github.com/briancaffey/hermes-otel/blob/main/docker-compose/otel-desktop-viewer/README.md)):

- **The `query` parameter is the UI's parsed tree, not a string.** `name = agent`
  as a string answers `Invalid query`. Filter in the UI, or filter the
  `searchTraces` result client-side.
- **No OTLP JSON export and no SQL in v0.5.0.** Upstream `main` documents
  `GET /export/traces/{id}` and a read-only `query` method; the v0.5.0 image has
  neither (unknown `GET` paths return the app's HTML with `200`). Pin
  `ODV_VERSION` when they ship.
- Logs from outside a turn (plugin start-up, shutdown) carry no trace id.
- v0.5.0 changed the on-disk schema upstream: `down -v` when changing versions.

## otel-tui

A terminal program: in Docker it needs a TTY and an open stdin, and you look
at it with `docker attach`, which is why it has no compose file of its own.
The image has no `ENTRYPOINT`; the binary is `/otel-tui` and the defaults are
`--host 0.0.0.0 --http 4318 --grpc 4317`.

```bash
docker run -d -i -t --name hermes-otel-otel-tui -p 4438:4318 ymtdzzz/otel-tui:latest
docker attach --sig-proxy=false hermes-otel-otel-tui          # detach with Ctrl-p Ctrl-q
# or natively (binds 4317 / 4318 — stop Jaeger, LGTM, … first; --http 4438 moves it):
brew install ymtdzzz/tap/otel-tui && otel-tui
```

```yaml
backends:
  - type: otlp
    name: otel-tui
    endpoint: http://localhost:4438/v1/traces
    metrics: true
    logs: true
```

The Traces tab lists one row per trace with the root span's name; Enter opens
the timeline with every span and the selected span's attributes (`gen_ai.*`,
`hermes.*`) in the Details pane. Captured from the attached terminal after one
turn (0.7.5, 2026-10-11):

```
││▼agent 2.933087s              │▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒││
││└▼llm.nvidia/nemotron-3-super-│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ ││
││ └ api.nvidia/nemotron-3-super│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒                              ││
││ └ tool.terminal 880.474ms    │                        ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒             ││
││ └ api.nvidia/nemotron-3-super│                                         ▒▒▒▒▒▒▒▒▒▒▒▒ ││
```

What to know:

- Memory only with fixed caps; everything is gone when the process stops.
- The Metrics tab shows **one row per metric per export batch**, not one per
  series: the plugin's 13 metric names appeared three times after one turn.
- Log records from outside a turn show `N/A` as the trace id; a trace with an
  error span was marked `[!]` in the list.
- Optional bearer auth via the `AUTH_TOKEN` environment variable; mirror it
  under `headers: {Authorization: Bearer …}` on the plugin entry.
- With otel-tui stopped, the plugin's export to it fails with a
  `ConnectionError` in `debug.log` and fan-out to the other backends continues.

## Which one

otel-tui when you are already in a terminal and want to watch spans arrive
while a turn runs. otel-desktop-viewer when you want the data to survive the
session, a browser timeline, or a scriptable check through `/rpc`. For a UI
that understands LLM spans, [Phoenix](/backends/phoenix); for all three
signals with dashboards, the [Aspire Dashboard](/backends/aspire) or
[LGTM](/backends/lgtm).
