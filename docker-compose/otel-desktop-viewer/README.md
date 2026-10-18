# otel-desktop-viewer (and otel-tui)

Two single-binary OTLP viewers for the "look at a Hermes turn with nothing to
set up" tier, next to Phoenix and the Aspire Dashboard. Both are stock
OTLP/HTTP receivers with no auth, so the plugin talks to them with the generic
`type: otlp` and nothing to translate (#367).

| | [otel-desktop-viewer](https://github.com/CtrlSpice/otel-desktop-viewer) | [otel-tui](https://github.com/ymtdzzz/otel-tui) |
|---|---|---|
| What | a browser UI on an embedded DuckDB, with a JSON-RPC read API | a terminal UI, in memory, no read API |
| This folder | `docker-compose.yaml` (one container) | a `docker run` line below; it only works attached to a terminal |
| Version verified | v0.5.0 image (2026-08-24), 263 MB, ~105 MB resident | 0.7.5 image (2026-09-12), 144 MB, ~30 MB resident |
| Signals | traces, metrics, logs | traces, metrics, logs |
| Licence | Apache-2.0 | Apache-2.0 |

## otel-desktop-viewer

### Start / stop
```bash
docker compose -f docker-compose/otel-desktop-viewer/docker-compose.yaml up -d
docker compose -f docker-compose/otel-desktop-viewer/docker-compose.yaml down      # data stays in the volume
docker compose -f docker-compose/otel-desktop-viewer/docker-compose.yaml down -v   # and wipe it
```
UI: http://localhost:3070 (no login). Ready about a second after start; the
image has no shell or curl, so check from the host:
`curl -s -o /dev/null -w '%{http_code}' http://localhost:3070/` → `200`.

Ports (host side, `ODV_PORT` / `ODV_OTLP_PORT` to change): **3070** UI and
`POST /rpc` (upstream 8000), **4428** OTLP/HTTP `/v1/traces`, `/v1/metrics`,
`/v1/logs` (upstream 4318). The compose file passes `--db` so the DuckDB file
lives in a named volume and survives restarts (the image's own CMD is
in-memory); `--db-max-size` is the upstream default of 2 GB for a file store.

### Point hermes-otel at it
```yaml
backends:
  - type: otlp
    name: otel-desktop-viewer
    endpoint: http://localhost:4428/v1/traces
    metrics: true
    logs: true
logs:
  capture: true   # send Python log lines as OTel logs too
```
The startup banner reads `✓ otel-desktop-viewer at http://localhost:4428/v1/traces`; with
`HERMES_OTEL_DEBUG=true` the plugin's `debug.log` shows
`export otel-desktop-viewer: N span(s) -> SUCCESS` and the same for logs.

### Verify (JSON-RPC 2.0 at `POST /rpc`)
Parameters are named; times are Unix **nanoseconds** as numbers or decimal
strings. `getStats` takes none:
```bash
NOW=$(python3 -c 'import time; print(int(time.time()*1e9))')
curl -s -X POST http://localhost:3070/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"getStats","params":{}}'
# the newest traces in a window: traceID, rootSpan {serviceName, name}, startTime, durationNs, spanCount, errorCount
curl -s -X POST http://localhost:3070/rpc -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"searchTraces\",\"params\":{\"startTime\":0,\"endTime\":$NOW}}"
# every span of one trace, attributes typed ({key, value, type})
curl -s -X POST http://localhost:3070/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"searchSpans","params":{"traceID":"<trace id>"}}'
```
Other methods in v0.5.0: `getTraceSpanCount`, `searchLogs` (`startTime`,
`endTime`, `query`), `getLog`, `searchMetricSummaries`, `getMetric`,
`getMetricAggregate`, `getTraceAttributes` / `getLogAttributes` /
`getMetricAttributes`, `searchAttributes`, `getAttributesByTraceID`, and the
`clear*` / `delete*` methods. An unknown method answers
`JSON RPC method not found`; a wrong parameter name is spelled out in the error.

Verified 2026-10-11 (v0.5.0, hermes-otel 1.24.0 branch): one Hermes turn stored
one trace of 5 spans (`agent` root, `llm.*`, two `api.*`, `tool.terminal`), 27
log records and 13 metric names (74 data points); a later turn whose
`tool.read_file` failed showed `errorCount: 2` on its trace summary. The three
turns together: 3 traces, 26+ spans, 97 log records.

### Caveats
- **Generic type**: `type: otlp` with the full `/v1/traces` URL; the plugin
  derives `/v1/metrics` and `/v1/logs` from it. All three signals default on.
- **The `query` parameter is not a string.** `searchTraces`, `searchSpans` and
  `searchLogs` take the UI's *parsed* query tree (JSON); a query string such as
  `name = agent` answers `Invalid query`. The grammar the UI parses is
  `field operator value` with `and` / `or` / parentheses, operators `=`, `!=`,
  `<`, `>`, `=~`, `contains`, `in`, `regexp`. Search in the UI, or filter the
  `searchTraces` result client-side (`rootSpan.name`, `errorCount`).
- **No OTLP JSON export and no SQL in v0.5.0.** The current `main` documents
  `GET /export/traces/{id}` and a read-only `query` method; the v0.5.0 image
  has neither (an unknown `GET` path returns the app's HTML with `200`; `query`
  is `method not found`). Pin `ODV_VERSION` when they ship.
- Logs emitted outside a turn (plugin start-up, shutdown) carry no trace id.
- Storage is per container volume; `down -v` wipes it. The upstream release
  notes say v0.5.0 changed the on-disk schema, so wipe the volume when
  changing `ODV_VERSION`.
- Port 3070 and 4428 collide with nothing else in this directory.

## otel-tui (no compose file)

otel-tui is a terminal program: in Docker it needs a TTY **and** an open
stdin, and you look at it with `docker attach`. A compose service would need
`stdin_open: true`, `tty: true` and `docker compose attach` anyway, so this
folder gives the `docker run` line instead (the image has no ENTRYPOINT; the
binary is `/otel-tui`, default `--host 0.0.0.0 --http 4318 --grpc 4317`):
```bash
docker run -d -i -t --name hermes-otel-otel-tui -p 4438:4318 ymtdzzz/otel-tui:latest
docker attach --sig-proxy=false hermes-otel-otel-tui      # detach: Ctrl-p Ctrl-q
docker rm -f hermes-otel-otel-tui
```
Or natively: `brew install ymtdzzz/tap/otel-tui` then `otel-tui` (ports 4317 /
4318, so stop Jaeger, LGTM or anything else on 4318 first; `--http 4438` moves
it). Point the plugin at `http://localhost:4438/v1/traces` with `type: otlp`,
`metrics: true`, `logs: true` as above.

What one turn looked like (captured from the attached terminal, 2026-10-11,
0.7.5): the Traces tab lists one row per trace with the root span's name, and
Enter opens the timeline with all five spans and the selected span's
attributes (`gen_ai.*`, `hermes.*`) in the Details pane:
```
│┌──────────────────────────────┬──────────────────────────────────────────────────────┐│
││            Spans             │0──────586ms──────1s──────1s──────2s────────────────  ││
│├──────────────────────────────┼──────────────────────────────────────────────────────┤│
││▼agent 2.933087s              │▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒││
││└▼llm.nvidia/nemotron-3-super-│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ ││
││ └ api.nvidia/nemotron-3-super│▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒                              ││
││ └ tool.terminal 880.474ms    │                        ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒             ││
││ └ api.nvidia/nemotron-3-super│                                         ▒▒▒▒▒▒▒▒▒▒▒▒ ││
```

Caveats:
- Memory only, fixed caps (1000 spans per service, 1000 logs, 3000 metrics);
  everything is gone when the container stops.
- The Metrics tab shows **one row per metric per export batch**, not one per
  series: the plugin's 13 metric names appeared three times after a single
  turn (one row set per periodic export and flush).
- Log records from outside a turn show `N/A` as the trace id (the same records
  the viewer stores without one).
- A trace whose span ended with status error was marked `[!]` in the list
  (seen on a turn whose `tool.read_file` failed).
- Optional bearer auth via the `AUTH_TOKEN` environment variable; set the same
  value under `headers: {Authorization: Bearer …}` on the plugin entry.
- Only one process can own the pseudo-terminal: run it once, `attach` from any
  terminal. The plugin's export to a stopped otel-tui fails with
  `ConnectionError` in `debug.log` and nothing else breaks (fan-out keeps the
  other backends).
