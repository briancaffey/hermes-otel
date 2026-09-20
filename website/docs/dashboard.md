---
sidebar_position: 4
title: "Dashboard tab"
description: "The OTel tab in the Hermes web dashboard: live traces, metrics and logs from the in-process store with no backend, plus trace search on any configured backend that has an adapter."
---

# Dashboard tab

Installing the plugin adds an **OTel** tab to the Hermes web dashboard (`hermes dashboard`). It has two data sources:

| Source | What it shows | Needs |
|---|---|---|
| **Live** (in-process store) | The most recent turns, spans, metrics and logs the plugin recorded on this machine, within seconds of happening | nothing: on by default |
| **Backend** | Historical trace search and trace detail queried from one of your configured backends | a backend whose type has a dashboard adapter |

The plugin's own dashboard code lives in `hermes_otel/dashboard/` (a FastAPI router mounted at `/api/plugins/hermes_otel/` and a pre-built bundle registered as the tab). It is installed with the plugin; there is nothing extra to enable.

## The live store

The gateway process writes every finished span, every metric point and (when `capture_logs` is on) every log line into a small SQLite file, `$HERMES_HOME/hermes_otel_live.db`. The dashboard process, which is separate, reads the same file. The store is a **bounded buffer of recent activity**, not a record:

| Key | Default | Meaning |
|---|---|---|
| `dashboard_live` | `true` | Write to the live store at all. Set `false` to disable the Live source. |
| `dashboard_live_max_spans` | `1000` | Rows kept per kind (spans, metrics, logs); the oldest are dropped. |
| `dashboard_live_retention_hours` | `168` | Rows older than this are dropped as well. `0` keeps rows until the row cap evicts them. |
| `HERMES_OTEL_LIVE_DB` (env) | `$HERMES_HOME/hermes_otel_live.db` | Where the file lives. Both processes must resolve the same path. |

Every key also has a `HERMES_OTEL_*` environment variable, see [Environment variables](/reference/env-vars). For history beyond the buffer, configure a backend.

Rows carry indexed columns (trace id, session id, span name, status, start and end time, log level, logger, metric name) so the tab filters, groups and buckets in SQLite and the browser receives one page of results. The file's schema is versioned; a file written by a plugin release before 1.9 is recreated on first open.

## Tabs

- **Live**: cost, tokens, turns, spans and errors for the buffered activity, an activity sparkline, and one card per turn. Tokens and cost are counted once per turn (the root span's totals). Opening a card shows the span waterfall.
- **Traces**: the same cards with a filter box for the Live source, or the backend search form (native query, service, lookback) for the Backend source. Backend cards show the whole-trace span count when the adapter knows it.
- **Metrics**: totals, tokens and cost over time, tokens by type, calls by model, tool durations and approvals, from the live store.
- **Logs**: the in-process log tail with level and text filters. A log line carries a trace id and session id when the plugin can attribute it: the span on the logging thread's context, or, failing that, the one session with a turn in flight. With several sessions active at once the line stays unattributed rather than guessed.

Successful MCP keepalive pings are hidden by default in every list (a checkbox shows them).

## Which backends can the tab query?

| Backend type | Search | Detail | Notes |
|---|---|---|---|
| `phoenix` | yes | yes | GraphQL on the Phoenix port; honours `project_name` and never substitutes another project |
| `openobserve` | yes | yes | SQL over the traces stream; needs `user` and `password` |
| `langfuse` | yes | yes | Public API; a synthetic root marked `synthetic: true` holds the observations together |
| `signoz`, `uptrace`, `jaeger`, `tempo` / `lgtm` | yes | yes | Native query APIs |
| any other type | no | no | Shown as read-only in the status bar; use the Live source |

With several backends configured, `query_backend: <name or type>` in `hermes_otel.yaml` chooses which one the tab queries; otherwise the first one with an adapter is used. The status bar lists every configured backend.

## API

All routes are under `/api/plugins/hermes_otel/`. The streaming views poll the cursor endpoints; the query endpoints do the filtering server-side.

| Route | Purpose |
|---|---|
| `GET /live/status` | Whether the store is active and how full it is |
| `GET /live/spans`, `/live/metrics`, `/live/logs` (`since`, `limit`) | Raw rows after a cursor, for streaming |
| `GET /live/traces` (`lookback_hours`, `session`, `status`, `name`, `kind`, `text`, `trace_id`, `limit`, `offset`) | One row per trace, newest first, with totals counted once |
| `GET /live/traces/{trace_id}` | The trace's spans |
| `GET /live/sessions` (`lookback_hours`, `limit`) | One row per session: turns, spans, errors, tokens, cost, tool calls |
| `GET /live/metrics/names` | Every instrument in the store with its point count |
| `GET /live/metrics/query` (`name`, `group_by`, `agg`, `lookback_hours`, `bucket_s`) | Time buckets for one instrument, optionally split by an attribute |
| `GET /live/logs/search` (`trace_id`, `session`, `min_level`, `logger`, `text`, `lookback_hours`, `limit`) | Filtered log lines, newest first |
| `GET /live/loggers` | Logger names with counts |
| `GET /status` | The active query backend and every configured one |
| `GET /traces/search` (`q`, `service`, `lookback_hours`, `roots_only`, …) | Backend trace search |
| `GET /traces/{trace_id}` | Backend trace detail as OTLP JSON |

## Troubleshooting

**The Live tab says "Live mode is off".** `dashboard_live` is `false`, or the gateway and the dashboard resolve different `HERMES_HOME` values and therefore different files. `GET /live/status` reports the store's fill.

**Backend source says "Not configured".** No entry under `backends:` has a type with an adapter. The message lists the types that do.

**Numbers differ between Live and Backend for the same turn.** They should not since 1.8.8. Open an issue with the trace id and both screenshots.

## Developing the tab

Front-end sources are in `dashboard-ui/src` (TypeScript, built with esbuild into `hermes_otel/dashboard/dist/`, which is committed). `npm run build` rebuilds the bundle and CI fails if it is stale; `npm test` runs the vitest suite over the pure helpers. The tab must not rely on Tailwind classes from the host dashboard: every class it uses is either in `dashboard-ui/host-classes.txt` (regenerated from the targeted Hermes release with `scripts/host_css_classes.py`) or defined in `dist/style.css`, and a unit test enforces that. Python routes are tested with a FastAPI test client in `tests/unit/test_dashboard_api.py`.
