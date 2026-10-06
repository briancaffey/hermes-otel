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

The gateway process writes every finished span, every metric point and (when `logs.capture` or `logs.events.enabled` is on) every log record and event into a small SQLite file, `$HERMES_HOME/hermes_otel_live.db`. Rows are buffered and committed in one transaction by a background thread every 250 ms or 64 rows, so a hook never waits on the disk; the dashboard process, which is separate, reads the same file and sees a row within that interval. A commit that fails (the file locked by another process, a full disk) does not lose the batch: the rows are held, up to 5,000 of them, and committed on the next attempt, with one line in the plugin debug log. The file is created owner-only (`0600`, and an older `0644` file is tightened on open; the `-wal` / `-shm` sidecars inherit the mode) because it holds full prompts, responses and tool I/O. The store is a **bounded buffer of recent activity**, not a record:

| Key | Default | Meaning |
|---|---|---|
| `dashboard_live` | `true` | Write to the live store at all. Set `false` to disable the Live source. |
| `dashboard_live_max_spans` | `1000` | Rows kept per kind (spans, metrics, logs); the oldest are dropped. Host samples (`process.cpu.*`, `system.cpu.*`, `hw.*`, recorded several times a second when `host_metrics` is on) have their own cap of 200 rows so they can never evict the turn's token and cost points. |
| `dashboard_live_retention_hours` | `168` | Rows older than this are dropped as well. `0` keeps rows until the row cap evicts them. |
| `HERMES_OTEL_LIVE_DB` (env) | `$HERMES_HOME/hermes_otel_live.db` | Where the file lives. Both processes must resolve the same path. |

The caps are enforced every 64 writes by the gateway and once more whenever either process opens the file, with the values of that home's config, so a store fed only by one-shot `hermes -z` runs (fewer than 64 rows each) stays bounded too. With the default cap, 1,000 spans is roughly thirty turns of ten tool calls each; raise `dashboard_live_max_spans` for a busy gateway, or configure a backend for history.

Every key also has a `HERMES_OTEL_*` environment variable, see [Environment variables](/reference/env-vars). For history beyond the buffer, configure a backend.

Metrics in the live store carry the same names as the OTLP instruments (`hermes.token.usage`, `gen_ai.client.token.usage`, …), so a name in the Metrics tab's explorer means the same thing for the Live source and for a backend; a Prometheus-style backend shows them as `hermes_token_usage`.

The same store is what the bundled skill's terminal tool reads, so an agent in the chat can list turns, draw a trace tree or total up cost without the dashboard: see [The observability skill](/skill).

Rows carry indexed columns (trace id, session id, span name, status, start and end time, log level, logger, metric name) so the tab filters, groups and buckets in SQLite and the browser receives one page of results. The file's schema is versioned (`PRAGMA user_version`): a file written by a plugin release before 1.9 (v1, a JSON blob only) is recreated on first open; a file from 1.9 to 1.18 (v2) has the same layout as the current v3 and is kept and stamped in place, so a dashboard on the current release reads a file an older gateway is still writing; a file stamped by a newer release than the one reading it is opened as is and never re-stamped. A file that cannot be opened at all (corrupt, wrong permissions) is reported by `GET /live/status` as `live: false` with the error, not as an empty store.

A trace whose root span has not finished yet (a turn in flight) is listed as **partial**: the row describes its earliest stored span, the totals are the `api.*` subtotal so far, and `partial: true` is on the row; it becomes a normal row when the root lands. A turn is `status=ok` only when none of its spans failed. The model filter matches the model attribute of a span (a model name quoted inside a prompt is not a match), and `%` / `_` in the name and text filters are literal.

## Tabs

- **Live**: cost, tokens, turns, spans and errors for the buffered activity, an activity sparkline, and one card per turn. Tokens and cost are counted once per turn (the root span's totals). Opening a card shows the trace detail.
- **Traces**: one search bar for every source: status, kind, tool, model, session id, minimum duration, free text, trace id and lookback, plus the backend's native query and service as advanced fields. Fields a backend cannot honour are ignored by its adapter. The **Turns** view lists one card per turn; the **Sessions** view groups turns by session id with per-session turns, spans, errors, tokens, cost and tool calls (from the live store's `/live/sessions`, or grouped client-side from a backend's results). Backend cards show the whole-trace span count when the adapter knows it. Opening a span shows a summary built for its kind: a turn's user message, final response as markdown, and its tool calls with outcomes and commands; an LLM or API call's prompt as a conversation with a badge per role (system prompts collapsed), tool calls as cards and the response as markdown; a tool's command, arguments and result as key/value rows with the output text in full; skills, approvals, sub-agents and cron runs with their own facts. Every rich value has a **structured / raw** switch (JSON, message lists and markdown; one setting for the page, remembered per browser), and the full attribute table below formats counts, durations, booleans, lists and links by key, with long content collapsed behind its size.
- **Trace detail** (both sources): a header with the requested model (and the served model when it differs), tokens in/out/reasoning/cache-read, cost or "no pricing data", tools used, outcome, a session link and a copy button for the trace id, plus an "open in …" link to the trace in Phoenix, Langfuse or Jaeger. Under it, **Spans** (the waterfall; each span opens to a summary for its kind: prompt and response as a conversation for LLM spans, command, arguments and result for tools, the decision for approvals, with every attribute grouped by prefix below), **Logs** (the log lines that carry this trace id) and **Raw** (the spans as JSON).
- **Metrics**: for the chosen source and range, tiles (tokens, cost, model calls, tool calls, cache-read share), tokens and cost over time, tokens by type, calls by model, tool durations, approvals, CPU and GPU utilisation when recorded, and an **explorer** that charts any instrument the source holds, grouped by any attribute, with sum/count/avg/max/last. Buckets are computed server-side; the browser never sees raw points. A missing cost series reads "no pricing data", never `$0`.
- **Settings**: every plugin setting with its effective value, where it came from (`env` for a `HERMES_OTEL_*` variable, `file` for the config file, `default`) and a one-line description, grouped by topic and searchable, with values changed from the default marked. Invalid values in the file or the environment are shown as ignored rather than silently dropped. Backends appear as cards. Each card opens the backend's web UI in a new window: from `ui_url` on the entry when set, otherwise derived from `endpoint` where the UI shares the OTLP origin (the tooltip says which; a port is never guessed, so a Jaeger or SigNoz entry on `:4318` needs `ui_url` or `query_port`). The signal pills separate what the backend type accepts from what the entry exports: `on` (accepted and exported), `off` (accepted, switched off in the entry), `n/a` (the type does not accept the signal) and `forced` (exported although the type does not accept it). Below them: whether this dashboard can query the backend and for which signals, the metric temporality its reader uses and which rule set it (entry, type preset, top-level, SDK default), query-only keys such as `query_port` or `project_name`, per-backend headers, a link to the type's docs page, and how each credential is supplied (inline, `${VAR}` reference, `<field>_env`, or a fallback variable), never the credential itself. A **Raw YAML** view shows the config file as written and an **Effective config** rendering of every setting with a source comment per key, ready to paste into `hermes_otel.yaml`; an **Environment** view lists every variable the plugin reads, set or not. Credential values are masked until "show secrets" is ticked. The tab is read-only: edit the file, then reload. Values are resolved by the dashboard process; a gateway started before an edit keeps its old values until it restarts.
- **Logs**: server-side search over the chosen source with level, logger (picked from the loggers seen), session id, trace id, text, lookback, an **events only** toggle and an **event name** box that keep just the plugin's structured events (the name is a badge on the line, and clicking a badge filters to that event; see the [log events reference](/reference/log-events)); relative or absolute times; a trace id opens that trace in the Traces tab. Above the list a severity summary counts the page by level, with a sparkline of lines per time bucket (errors and warnings in colour). Every line expands in place: the record's time, logger and severity number, links to its session and trace, its span id, a **±30 s around this line** link that reloads the tab as a context window around that instant, **copy JSON** for the whole record, the body as it was logged, and the attributes grouped by prefix (Event, Hermes, GenAI, Exception, Code location, Other) with a stacktrace as a block. **follow** turns the page the other way up (oldest first, newest at the bottom) and keeps the end in view as lines arrive; **wrap** toggles long lines; **copy link** copies the page's URL, which carries the source and every filter. A session card in the Traces tab links to that session's logs, and a trace's **Logs** sub-tab shows the same expandable rows with an "open in Logs tab" link. Pages are **keyset-paged, newest first**: the next page is "the N rows older than the oldest row shown" (`before_ns`), so lines arriving while you read never shift an older page and every backend answers it with its native time bound. The newest page follows new lines every few seconds; an older page is a fixed window until you go back to the newest. Page size is 100–1000. Every filter, the page size and the cursor are in the URL (`?tab=logs&source=<backend>&level=30&logger=…&session=…&trace=…&text=…&lookback=24&events=1&event=hermes.tool.call&center=<unix ns>&win=30&size=500&before=<unix ns>`; a pasted link's `source` wins over the browser's remembered one), so a refresh, the back button or a pasted link lands on the same page with the same filters. A log line carries a trace id and session id when the plugin can attribute it exactly: a span current on the logging thread, Hermes' own session tag on the record, or the one session with a turn in flight; `hermes.log.attribution` on the record says which. With several sessions active at once an unattributable line stays unattributed rather than guessed ([details](/configuration/logs#where-the-ids-come-from)). Rows use the OTel severity spelling (`WARN`, `FATAL`).

The tab keeps its state in the URL (`/otel?tab=traces&source=live&view=turns&trace=<id>`), so a refresh, the back button or a pasted link lands on the same view.

Successful MCP keepalive pings are hidden by default in every list (a checkbox shows them).

## Choosing the source

Every tab has a **source** selector: `Live (in-process)` plus one entry per configured backend. Entries whose type has no adapter, or that cannot serve what the tab shows (metrics, logs), are listed but disabled with the reason. The choice is remembered per browser. The API takes the same choice as a `backend=<name or type>` query parameter on `/status`, `/traces/search`, `/traces/{id}`, `/metrics/*` and `/logs/*`; an unknown name is a `400` listing the configured names, and a backend without the capability is a `503`.

Without a selection (or a parameter), `query_backend: <name or type>` in `hermes_otel.yaml` chooses the default backend, else the first configured one with an adapter.

A dashboard that serves several profiles (`?profile=<name>`) reads each profile's own config and live store: the Live source, the backend list and the Settings tab all follow the profile of the page.

## Which backends can the tab query?

| Backend type | Trace search and detail | Metrics | Logs | Notes |
|---|---|---|---|---|
| `phoenix` | yes | no | no | GraphQL on the Phoenix port; honours `project_name` and never substitutes another project |
| `openobserve` | yes | yes | yes | SQL over the traces, metrics and logs streams; needs `user` and `password`. Counters arrive cumulative and are shown as increases per bucket; events filter on the `event_name` column, and flattened attribute names (`hermes_log_attribution`) come back dotted |
| `langfuse` | yes | no | no | Public API; a synthetic root marked `synthetic: true` holds the observations together |
| `signoz` | yes | yes | yes | Query-builder API (`/api/v4/query_range`) for all three signals; needs `api_key`. Counters are shown as the increase per bucket; logs filter on `trace_id`, `hermes.session_id`, the logger (`scope_name`), severity and body text; events on the `event.name` attribute |
| `uptrace` | yes | yes | yes | Uptrace 2.x `/internal/v1` API in both of its spellings (2.1's one route per signal, 2.0's `/tracing/` routes; probed once per process); needs a **user** token (`user_token_env`), not the DSN's project token. Metrics via MQL (`$m`, `sum($m)`, `avg($m)`…) with `group by`; logs are the span store's `log:*` systems, filtered on `_trace_id`, `hermes_session_id`, `otel_library_name`, `event_name`, level and text |
| `lgtm` | yes | yes | yes | Tempo for traces, the stack's Prometheus (`prometheus_url`, default `:9090`) for metrics and Loki (`loki_url`, default `:3100`, `off` to disable) for logs. Counters are shown as `increase()` per bucket; logs use LogQL label-filter stages on `trace_id`, `hermes_session_id`, `scope_name`, `severity_number`, `event_name` and body text, and the underscored labels come back as dotted attribute names |
| `tempo` | yes | optional | optional | Traces from Tempo; add `prometheus_url` / `loki_url` to a Tempo entry to get the same metrics and logs as `lgtm` |

The logs column, including the event filters and the expanded record view, was checked against live stacks on 2026-10-04: `grafana/otel-lgtm` 0.34 (Loki), OpenObserve, SigNoz v0.119 and Uptrace 2.1.0-beta.5, each fed by a real Hermes turn. The Uptrace 2.0 spelling keeps the request and row shapes recorded against 2.0.2 and was not re-run.
| `jaeger` | yes | no | no | Jaeger stores traces only |
| any other type | no | no | no | Shown as unavailable in the selector; use the Live source |

## API

All routes are under `/api/plugins/hermes_otel/`. The streaming views poll the cursor endpoints; the query endpoints do the filtering server-side.

| Route | Purpose |
|---|---|
| `GET /live/status` | Whether the store can be read (`live`, with a `reason` and the file `path` when not: the module failed to import, or the file could not be opened), how full it is, and `write_error` / `pending` while a commit is being retried |
| `GET /live/spans`, `/live/metrics`, `/live/logs` (`since`, `limit`) | Raw rows after a cursor, for streaming |
| `GET /live/traces` (`lookback_hours` or `start_s`/`end_s`, `session`, `status`, `name`, `kind`, `text`, `trace_id`, `model`, `tool`, `min_duration_ms`, `limit`, `offset`) | One row per trace, newest first, with totals counted once: `{traces, total, has_more}`; a row carries `partial: true` while its root span is missing. `kind` accepts every kind the tab shows, including `other`. A window with `start_s` after `end_s` is a `422` |
| `GET /live/traces/{trace_id}` | The trace's spans and its summary row (`partial` included) |
| `GET /live/sessions` (`lookback_hours`, `limit`) | One row per session: turns, spans, errors, tokens, cost, tool calls, newest first; `has_more` when more sessions than `limit` exist in the window |
| `GET /live/metrics/names` | Every instrument in the store with its point count (the whole buffer, not a window) |
| `GET /live/metrics/query` (`name`, `group_by`, `agg`, `lookback_hours` or `start_s`/`end_s`, `bucket_s`) | Time buckets for one instrument, optionally split by an attribute. More than 5,000 buckets (window ÷ `bucket_s`) is a `422` |
| `GET /live/logs/search` (`trace_id`, `span_id`, `session`, `min_level` on the Python scale: 10 debug, 20 info, 30 warn, 40 error, `logger`, `text`, `event_name`, `events_only`, `lookback_hours` or an absolute `start_s`/`end_s` window, `limit`, `before_ns`) | One page of filtered log lines, newest first: `{logs, next_before_ns, has_more}`. Pass `next_before_ns` back as `before_ns` for the next (older) page. Rows carry the OTel `severity_number` next to the level |
| `GET /live/loggers` | Logger names with counts |
| `GET /settings` (`reveal`) | Every setting with value, default, source and description; the config file raw and as an effective YAML; the environment variables the plugin reads. Credentials are masked unless `reveal=true` |
| `GET /status` (`backend`) | The chosen or default backend, and every configured one with its capabilities (`available[].supported/metrics/logs`) |
| `GET /traces/search` (`backend`, `q`, `service`, `lookback_hours`, `roots_only`, `status`, `min_duration_ms`, `free_text`, `name_regex`, `model`, `session`, `tool`) | Backend trace search; `model`/`session`/`tool` become attribute equalities each adapter translates |
| `GET /traces/{trace_id}` (`backend`) | Backend trace detail as OTLP JSON |
| `GET /metrics/names`, `/metrics/query` (`backend`, same parameters as the live ones) | Metrics from a backend that serves them, in the live endpoints' shape |
| `GET /logs/search`, `/loggers` (`backend`, same parameters as the live ones) | Logs from a backend that serves them, in the live record shape |

## Troubleshooting

**The Live tab says "Live mode is off".** The dashboard could not import the plugin's `live_store` module, or the file could not be opened (corrupt, or not readable by the dashboard's user); `GET /live/status` names the reason and the path. An empty Live tab with `live: true` and zero rows means nothing is being written: `dashboard_live` is `false`, or the gateway and the dashboard resolve different `HERMES_HOME` values and therefore different files (compare `path` with the gateway's home).

**Backend source says "Not configured".** No entry under `backends:` has a type with an adapter. The message lists the types that do.

**Numbers differ between Live and Backend for the same turn.** They should not since 1.8.8. Open an issue with the trace id and both screenshots.

## Developing the tab

Front-end sources are in `dashboard-ui/src` (TypeScript, built with esbuild into `hermes_otel/dashboard/dist/`, which is committed). `npm run build` rebuilds the bundle and CI fails if it is stale; `npm test` runs the vitest suite over the pure helpers. The tab must not rely on Tailwind classes from the host dashboard: every class it uses is either in `dashboard-ui/host-classes.txt` (regenerated from the targeted Hermes release with `scripts/host_css_classes.py`) or defined in `dist/style.css`, and a unit test enforces that. Python routes are tested with a FastAPI test client in `tests/unit/test_dashboard_api.py`.
