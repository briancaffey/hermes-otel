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

The gateway process writes every finished span, every metric point and (when `capture_logs` is on) every log line into a small SQLite file, `$HERMES_HOME/hermes_otel_live.db`. Rows are buffered and committed in one transaction by a background thread every 250 ms or 64 rows, so a hook never waits on the disk; the dashboard process, which is separate, reads the same file and sees a row within that interval. The store is a **bounded buffer of recent activity**, not a record:

| Key | Default | Meaning |
|---|---|---|
| `dashboard_live` | `true` | Write to the live store at all. Set `false` to disable the Live source. |
| `dashboard_live_max_spans` | `1000` | Rows kept per kind (spans, metrics, logs); the oldest are dropped. |
| `dashboard_live_retention_hours` | `168` | Rows older than this are dropped as well. `0` keeps rows until the row cap evicts them. |
| `HERMES_OTEL_LIVE_DB` (env) | `$HERMES_HOME/hermes_otel_live.db` | Where the file lives. Both processes must resolve the same path. |

Every key also has a `HERMES_OTEL_*` environment variable, see [Environment variables](/reference/env-vars). For history beyond the buffer, configure a backend.

Metrics in the live store carry the same names as the OTLP instruments (`hermes.token.usage`, `gen_ai.client.token.usage`, …), so a name in the Metrics tab's explorer means the same thing for the Live source and for a backend; a Prometheus-style backend shows them as `hermes_token_usage`.

The same store is what the bundled skill's terminal tool reads, so an agent in the chat can list turns, draw a trace tree or total up cost without the dashboard: see [The observability skill](/skill).

Rows carry indexed columns (trace id, session id, span name, status, start and end time, log level, logger, metric name) so the tab filters, groups and buckets in SQLite and the browser receives one page of results. The file's schema is versioned; a file written by a plugin release before 1.9 is recreated on first open.

## Tabs

- **Live**: cost, tokens, turns, spans and errors for the buffered activity, an activity sparkline, and one card per turn. Tokens and cost are counted once per turn (the root span's totals). Opening a card shows the trace detail.
- **Traces**: one search bar for every source: status, kind, tool, model, session id, minimum duration, free text, trace id and lookback, plus the backend's native query and service as advanced fields. Fields a backend cannot honour are ignored by its adapter. The **Turns** view lists one card per turn; the **Sessions** view groups turns by session id with per-session turns, spans, errors, tokens, cost and tool calls (from the live store's `/live/sessions`, or grouped client-side from a backend's results). Backend cards show the whole-trace span count when the adapter knows it.
- **Trace detail** (both sources): a header with the requested model (and the served model when it differs), tokens in/out/reasoning/cache-read, cost or "no pricing data", tools used, outcome, a session link and a copy button for the trace id, plus an "open in …" link to the trace in Phoenix, Langfuse or Jaeger. Under it, **Spans** (the waterfall; each span opens to a summary for its kind: prompt and response as a conversation for LLM spans, command, arguments and result for tools, the decision for approvals, with every attribute grouped by prefix below), **Logs** (the log lines that carry this trace id) and **Raw** (the spans as JSON).
- **Metrics**: for the chosen source and range, tiles (tokens, cost, model calls, tool calls, cache-read share), tokens and cost over time, tokens by type, calls by model, tool durations, approvals, CPU and GPU utilisation when recorded, and an **explorer** that charts any instrument the source holds, grouped by any attribute, with sum/count/avg/max/last. Buckets are computed server-side; the browser never sees raw points. A missing cost series reads "no pricing data", never `$0`.
- **Settings**: every plugin setting with its effective value, where it came from (`env` for a `HERMES_OTEL_*` variable, `file` for the config file, `default`) and a one-line description, grouped by topic and searchable, with values changed from the default marked. Invalid values in the file or the environment are shown as ignored rather than silently dropped. Backends appear as cards. Each card opens the backend's web UI in a new window: from `ui_url` on the entry when set, otherwise derived from `endpoint` where the UI shares the OTLP origin (the tooltip says which; a port is never guessed, so a Jaeger or SigNoz entry on `:4318` needs `ui_url` or `query_port`). The signal pills separate what the backend type accepts from what the entry exports: `on` (accepted and exported), `off` (accepted, switched off in the entry), `n/a` (the type does not accept the signal) and `forced` (exported although the type does not accept it). Below them: whether this dashboard can query the backend and for which signals, the metric temporality its reader uses and which rule set it (entry, type preset, top-level, SDK default), query-only keys such as `query_port` or `project_name`, per-backend headers, a link to the type's docs page, and how each credential is supplied (inline, `${VAR}` reference, `<field>_env`, or a fallback variable), never the credential itself. A **Raw YAML** view shows the config file as written and an **Effective config** rendering of every setting with a source comment per key, ready to paste into `hermes_otel.yaml`; an **Environment** view lists every variable the plugin reads, set or not. Credential values are masked until "show secrets" is ticked. The tab is read-only: edit the file, then reload. Values are resolved by the dashboard process; a gateway started before an edit keeps its old values until it restarts.
- **Logs**: server-side search over the chosen source with level, logger (picked from the loggers seen), session id, trace id, text and lookback; relative or absolute times; "load more" paging; a trace id opens that trace in the Traces tab. A log line carries a trace id and session id when the plugin can attribute it: the span on the logging thread's context, or, failing that, the one session with a turn in flight. With several sessions active at once the line stays unattributed rather than guessed.

The tab keeps its state in the URL (`/otel?tab=traces&source=live&view=turns&trace=<id>`), so a refresh, the back button or a pasted link lands on the same view.

Successful MCP keepalive pings are hidden by default in every list (a checkbox shows them).

## Choosing the source

Every tab has a **source** selector: `Live (in-process)` plus one entry per configured backend. Entries whose type has no adapter, or that cannot serve what the tab shows (metrics, logs), are listed but disabled with the reason. The choice is remembered per browser. The API takes the same choice as a `backend=<name or type>` query parameter on `/status`, `/traces/search`, `/traces/{id}`, `/metrics/*` and `/logs/*`; an unknown name is a `400` listing the configured names, and a backend without the capability is a `503`.

Without a selection (or a parameter), `query_backend: <name or type>` in `hermes_otel.yaml` chooses the default backend, else the first configured one with an adapter.

## Which backends can the tab query?

| Backend type | Trace search and detail | Metrics | Logs | Notes |
|---|---|---|---|---|
| `phoenix` | yes | no | no | GraphQL on the Phoenix port; honours `project_name` and never substitutes another project |
| `openobserve` | yes | yes | yes | SQL over the traces, metrics and logs streams; needs `user` and `password`. Counters arrive cumulative and are shown as increases per bucket |
| `langfuse` | yes | no | no | Public API; a synthetic root marked `synthetic: true` holds the observations together |
| `signoz` | yes | yes | yes | Query-builder API (`/api/v4/query_range`) for all three signals; needs `api_key`. Counters are shown as the increase per bucket; logs filter on `trace_id`, `hermes.session_id`, the logger (`scope_name`), severity and body text |
| `uptrace` | yes | yes | yes | Uptrace 2.x `/internal/v1` API; needs a **user** token (`user_token_env`), not the DSN's project token. Metrics via MQL (`$m`, `sum($m)`, `avg($m)`…) with `group by`; logs are the span store's `log:*` systems, filtered on `_trace_id`, `hermes_session_id`, `otel_library_name`, level and text |
| `lgtm` | yes | yes | yes | Tempo for traces, the stack's Prometheus (`prometheus_url`, default `:9090`) for metrics and Loki (`loki_url`, default `:3100`, `off` to disable) for logs. Counters are shown as `increase()` per bucket; logs use LogQL label-filter stages on `trace_id`, `hermes_session_id`, `scope_name`, `severity_number` and body text |
| `tempo` | yes | optional | optional | Traces from Tempo; add `prometheus_url` / `loki_url` to a Tempo entry to get the same metrics and logs as `lgtm` |
| `jaeger` | yes | no | no | Jaeger stores traces only |
| any other type | no | no | no | Shown as unavailable in the selector; use the Live source |

## API

All routes are under `/api/plugins/hermes_otel/`. The streaming views poll the cursor endpoints; the query endpoints do the filtering server-side.

| Route | Purpose |
|---|---|
| `GET /live/status` | Whether the store is active and how full it is |
| `GET /live/spans`, `/live/metrics`, `/live/logs` (`since`, `limit`) | Raw rows after a cursor, for streaming |
| `GET /live/traces` (`lookback_hours`, `session`, `status`, `name`, `kind`, `text`, `trace_id`, `model`, `tool`, `min_duration_ms`, `limit`, `offset`) | One row per trace, newest first, with totals counted once |
| `GET /live/traces/{trace_id}` | The trace's spans |
| `GET /live/sessions` (`lookback_hours`, `limit`) | One row per session: turns, spans, errors, tokens, cost, tool calls |
| `GET /live/metrics/names` | Every instrument in the store with its point count |
| `GET /live/metrics/query` (`name`, `group_by`, `agg`, `lookback_hours`, `bucket_s`) | Time buckets for one instrument, optionally split by an attribute |
| `GET /live/logs/search` (`trace_id`, `session`, `min_level`, `logger`, `text`, `lookback_hours`, `limit`) | Filtered log lines, newest first |
| `GET /live/loggers` | Logger names with counts |
| `GET /settings` (`reveal`) | Every setting with value, default, source and description; the config file raw and as an effective YAML; the environment variables the plugin reads. Credentials are masked unless `reveal=true` |
| `GET /status` (`backend`) | The chosen or default backend, and every configured one with its capabilities (`available[].supported/metrics/logs`) |
| `GET /traces/search` (`backend`, `q`, `service`, `lookback_hours`, `roots_only`, `status`, `min_duration_ms`, `free_text`, `name_regex`, `model`, `session`, `tool`) | Backend trace search; `model`/`session`/`tool` become attribute equalities each adapter translates |
| `GET /traces/{trace_id}` (`backend`) | Backend trace detail as OTLP JSON |
| `GET /metrics/names`, `/metrics/query` (`backend`, same parameters as the live ones) | Metrics from a backend that serves them, in the live endpoints' shape |
| `GET /logs/search`, `/loggers` (`backend`, same parameters as the live ones) | Logs from a backend that serves them, in the live record shape |

## Troubleshooting

**The Live tab says "Live mode is off".** `dashboard_live` is `false`, or the gateway and the dashboard resolve different `HERMES_HOME` values and therefore different files. `GET /live/status` reports the store's fill.

**Backend source says "Not configured".** No entry under `backends:` has a type with an adapter. The message lists the types that do.

**Numbers differ between Live and Backend for the same turn.** They should not since 1.8.8. Open an issue with the trace id and both screenshots.

## Developing the tab

Front-end sources are in `dashboard-ui/src` (TypeScript, built with esbuild into `hermes_otel/dashboard/dist/`, which is committed). `npm run build` rebuilds the bundle and CI fails if it is stale; `npm test` runs the vitest suite over the pure helpers. The tab must not rely on Tailwind classes from the host dashboard: every class it uses is either in `dashboard-ui/host-classes.txt` (regenerated from the targeted Hermes release with `scripts/host_css_classes.py`) or defined in `dist/style.css`, and a unit test enforces that. Python routes are tested with a FastAPI test client in `tests/unit/test_dashboard_api.py`.
