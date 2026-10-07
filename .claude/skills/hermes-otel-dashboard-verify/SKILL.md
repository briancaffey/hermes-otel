---
name: hermes-otel-dashboard-verify
description: >-
  Prove that the hermes-otel dashboard reads one backend correctly for every
  signal it exports: run a fixed, marked workload through Hermes, use the
  plugin's live store as the oracle, check the backend adapter through the
  plugin API, then drive the real dashboard tab in a browser (Playwright) and
  compare what the page shows with what the API answered. One command per
  backend, a pass/fail report with screenshots, and a fix loop. Use it after
  touching an adapter, the dashboard pages, the exporter, or when bringing up a
  new backend. Pairs with hermes-otel-backends (stand the backend up) and
  hermes-otel-validate (prove a single span/metric landed).
---

# Verifying the dashboard against a backend

The dashboard tab has two halves that can each be wrong on their own: the
**adapter** (`hermes_otel/dashboard/backends/<type>.py`, served through
`/api/plugins/hermes_otel/*`) and the **pages** (`dashboard-ui/`, bundled into
`hermes_otel/dashboard/dist/index.js`). Unit tests fake both the backend and
the browser. This skill tests neither with a fake: a real Hermes run, a real
backend, the real plugin API and a real browser, and one script that scores all
of it.

**The oracle is the live store.** Every turn the workload runs is recorded by
the plugin's own SQLite live store (`$HERMES_HOME/hermes_otel_live.db`) in the
same process that exported it. Whatever a backend answers for a turn must agree
with the live store for that turn: same spans, same status, same tokens, same
log lines, same metric totals. "Standard and reliable" means: *the backend
shows exactly what the plugin emitted, through the same page, with the same
numbers.*

## 1. The one command

```bash
# backend running (see hermes-otel-backends), dashboard running on :9119,
# the source listed in the profile's hermes_otel.yaml
uv run --with playwright --extra dev python scripts/dashboard_verify/verify_dashboard.py \
    --source lgtm-local --profile minimal --run-batch

# re-score an earlier batch (no new turns), several sources at once
uv run --with playwright --extra dev python scripts/dashboard_verify/verify_dashboard.py \
    --source lgtm-local --source openobserve-local --profile minimal --batch B261006-120000
```

Output: `scripts/dashboard_verify/out/<token>/<source>/report.md` (+ `report.json`,
`*.png` per page). Exit code 1 when any check failed. Read `report.md` top to
bottom; every row is `PASS`, `FAIL`, `WARN` or `SKIP` with the two numbers
that were compared.

Install Playwright's browser once: `uv run --with playwright playwright install chromium`.

> The connected Chrome (Claude in Chrome) is a remote Windows device and cannot
> reach this Mac's localhost. In-browser verification runs headless Chromium
> through Playwright on this machine, and the screenshots are the evidence.

## 2. What runs

### The workload (fixed, marked, small)

Four prompts from `scripts/turn_batch/prompts.yaml`, run in series through
`hermes -p <profile> chat -Q --yolo -q` (never `-z`: one-shot runs export no
logs). Each carries a unique marker `<token>-<id>` in its text, so every
source can be asked for exactly that turn:

| id | exercises | expect |
|---|---|---|
| `shell-echo` | agent → llm → api → one `tool.terminal` span | status ok, 1 tool, logs ≥ 3 |
| `no-tools` | agent → llm → api, no tool span | 0 tools |
| `missing-file` | a tool that fails inside a turn that succeeds | tool error text, root ok |
| `multi-tool` | three tool calls, several api round-trips | ≥ 2 api spans, ≥ 3 tools |

Roughly 20–40 s per turn with the minimal profile's model. Keep other Hermes
traffic (gateway chats, cron) quiet during the batch: metric totals are
compared over the batch's time window, and anything else exporting to the same
backend in that window shows up as a mismatch.

### The checks

Every check has an id; the report lists them in this order. "Live" means the
live store of the same profile, read through `/live/*`.

**S — status and contract**
- `S1` the source is in `/status.available` with `supported: true`; its
  `metrics`/`logs` flags match the backend table below.
- `S2` `filters` declares all 11 keys with `server|client|none`.
- `S3` error taxonomy: unknown trace id → 404 `kind=not_found`; unknown
  backend → 400 `kind=request`; `bucket_s=1` over 24 h → 422 `kind=request`
  (5,000-bucket cap); a source without metrics → 503 `kind=config`.

**T — traces, per marked turn**
- `T1` the turn is found by its marker (`free_text` when the adapter declares
  it, otherwise the window is scanned and the input preview matched) and found
  exactly once.
- `T2` the list row agrees with Live: root name, status, model, session id,
  total tokens, start time (±5 ms) and duration (±5 ms or 1 %). Trace ids
  compare as 32 hex digits (Tempo drops leading zeros).
- `T3` the detail agrees with Live: same span count, same multiset of span
  names, the root has no parent, every child's parent is in the trace, the
  same spans are `ERROR`.
- `T4` `trace_url` (the "open in" link) is present for backends with a UI and
  contains the trace id.

**T — traces, filters and paging (over the batch window)**
- `T5` `status=ok` / `status=error` against the batch: every backend filters
  the root span (all batch roots are ok), the live store means "any span"
  (so `missing-file`, whose tool span errored, is not ok there); each
  SKIPped when the adapter declares `none`.
- `T6` `tool=terminal` returns the shell turns and not `no-tools`;
  `kind=tool` (`name_prefix=tool.`, roots widened) returns traces whose matched
  spans are tool spans; `session=<id>` returns exactly that turn;
  `model=<name>` returns every batch turn; `min_duration_ms=10^8` returns none.
- `T7` keyset paging: `limit=2` → `has_more`, `next_before_ns`; the next page
  is strictly older, pages do not overlap, their union is the batch, newest
  first.

**M — metrics (sources with `metrics: true`)**
- `M1` `/metrics/names` carries `otlp_name` for `hermes.token.usage`,
  `hermes.model.usage` and `hermes.tool.duration` (sum/count parts allowed).
- `M2` parity over the batch window: token usage per `token_type` equals Live;
  model calls (`hermes.model.usage`) equal Live's count; tool calls
  (`hermes.tool.duration` count) equal Live's tool-span count. Checked once
  the window has closed and retried within the settle budget (metrics land
  after spans).

**L — logs (sources with `logs: true`)**
- `L1` per turn: `trace_id=<id>` returns as many lines as Live, ≥ the prompt's
  `logs_min`, every line carrying that trace id.
- `L2` `min_level=30` returns a subset whose every line is WARN or above;
  `logger=<one seen>` returns only that logger.
- `L3` keyset paging on one turn's lines (`limit=3`): strictly older, no
  overlap, union is the whole set.
- `L4` `/loggers` is non-empty and includes a logger seen in `L1`.
- `L5` `events_only=true` returns only rows with an `event_name` (or none).

**B — browser (the dashboard tab, headless Chromium)**
- `B1` Traces: with the marker in the text box (or the session filter), one
  card per turn; the card shows the root name, model and token count Live has.
- `B2` Detail: the spans line equals Live's span count; the "open in" link's
  `href` is `T4`'s URL; the Logs sub-tab line count equals `L1`.
- `B3` Filters by URL: `status=error` hides the batch; `kind=tool` shows tool
  cards on a backend (the matched span is the card) and turn cards on Live;
  an `ignored:` chip appears only for filters the adapter declares `none`.
- `B4` Metrics: the TOKENS, MODEL CALLS and TOOL CALLS tiles equal the API's
  totals for the same range; no tile reads "not recorded" when `M1` found the
  instrument.
- `B5` Logs: the line count equals the API's count for the same lookback and
  page size; `level=30` reduces it the same way as `L2`; "Older" pages when
  the API says `has_more`.
- `B6` Sessions view lists the batch sessions. Settings tab lists the source
  and never prints a secret in clear.
- `B7` zero console errors and zero failed `/api/plugins/hermes_otel` requests
  on any page (deliberate error probes excluded). A screenshot of every page
  is saved for a human look.

### What is deliberately not here

No load or soak testing, no cross-profile tests, no sub-agent / cron / gateway
workloads, no theme or layout checks, no backend UI driving. The live store's
own correctness is covered by unit tests; here it is trusted as the oracle.

## 3. The verification profile

The turns run through the `minimal` Hermes profile
(`~/.hermes/profiles/minimal`), set up on 2026-10-06 for exactly this:

- **Model:** `nvidia/nemotron-3-super-120b-a12b` through Hermes's built-in
  `nvidia` provider (`model: {default: ..., provider: nvidia}` in the
  profile's `config.yaml`). The provider reads `NVIDIA_API_KEY` from the
  profile's `.env`; never put the key in a config file or in chat. It answers
  tool calls in 1–2 s, so a turn takes about 10 s. The 30B
  `nvidia/nemotron-3.5-lightning-30b-a3b` also works (about 4 s per call).
- **Backends: local docker-compose stacks only.** The profile's
  `hermes_otel.yaml` lists one named entry per running stack
  (`<type>-local`). Keep it that way: an entry for a stack that is not
  running makes every turn wait on a dead collector at shutdown (a minute per
  turn with four dead k3s entries, against ten seconds without).
- `query_backend: openobserve-local`, `capture_logs: true`,
  `content_capture: full`, `force_flush_wait_ms: 8000`.
- Dev loop: the dashboard on :9119 serves `~/.hermes/plugins/hermes_otel`, not
  the repo. After an adapter or page fix, rebuild the bundle
  (`cd dashboard-ui && npm run build`), copy the tree
  (`rsync -a --delete --exclude __pycache__ hermes_otel/ ~/.hermes/plugins/hermes_otel/`,
  same for the profile's `plugins/`), and restart the dashboard
  (`hermes dashboard --stop; cd ~ && nohup hermes dashboard --port 9119 --no-open > ~/.hermes/logs/dashboard.out 2>&1 &`).

## 4. The loop per backend

1. **Bring it up** with `hermes-otel-backends` (one heavy stack at a time; the
   Docker VM has little headroom — `docker system df`).
2. **Add a named entry** to the verification profile's `hermes_otel.yaml`
   (back it up to the scratchpad first). Name it `<type>-local`; the name is
   the `source`.
3. **Confirm the plugin exports to it**: `hermes -p minimal chat -Q --yolo -q hi`
   and look for `✓ <name>` in the banner.
4. **Run the command** in §1 with `--run-batch`.
5. **Read `report.md`.** For every FAIL decide: adapter bug, page bug, backend
   quirk the plan must document, or harness bug. Fix in that order of
   likelihood; re-score the same batch with `--batch <token>` (no new turns)
   until clean, then run one fresh batch to confirm.
6. **Record** the outcome in the matrix below and in the backend's issue.

## 5. Backend matrix

What each backend is expected to serve, how to start it, and what the plan
knows about it. Keep this table true; the harness reads its own copy in
`scripts/dashboard_verify/backends.yaml`.

| type | traces | metrics | logs | start | source used | notes |
|---|---|---|---|---|---|---|
| lgtm | ✅ | ✅ Prometheus | ✅ Loki | `docker compose -p lgtm -f docker-compose/lgtm.yaml up -d` (the running local copy maps Grafana to :3001) | `lgtm-local` | counters via raw `query_range` with first-sample counting; roots via `nestedSetParent < 0`; Tempo anchors regexes, so prefixes carry `.*` |
| openobserve | ✅ | ✅ | ✅ | `docker compose -p openobserve -f docker-compose/openobserve.yaml up -d` | `openobserve-local` | healthcheck false-negative; process identity from `start_time`; links to `/web/traces/trace-details` |
| signoz | ✅ | ✅ | ✅ | `docker compose -f docker-compose/signoz/docker-compose.yaml up -d` (heavy) | `signoz-local` | 30-min JWT; detail via GET `/api/v1/traces/{id}` |
| uptrace | ✅ | ✅ | ✅ | `docker compose -p uptrace -f <copy of docker-compose/uptrace.yaml with 8124/9009/5433 remaps> up -d` (UI + OTLP on 14318) | `uptrace-local` | needs `endpoint` + `dsn` + `user_token` (compose defaults `project1_secret` / `user1_secret`); cumulative `$m` points |
| jaeger | ✅ | — | — | `docker compose -p jaeger -f <copy of docker-compose/jaeger.yaml with OTLP on 4320> up -d` (4318 is LGTM's) | `jaeger-local` | `status_ok` and `free_text` are `none`, roots client-side |
| tempo | ✅ | — | — | part of lgtm (query_port 3200) | `tempo-local` | TraceQL |
| phoenix | ✅ | — | — | `docker compose -f docker-compose/phoenix.yaml up -d` | `phoenix-local` | `status=error` means the root errored, not any span |
| langfuse | ✅ | — | — | `docker compose -p langfuse -f <copy of docker-compose/langfuse.yaml with web on 3002> up -d` (heavy; keys `lf_pk_hermes_dev` / `lf_sk_hermes_dev`) | `langfuse-local` | v3 works; v4 events-only deployments answer 404 on `/api/public/traces` (#246): expect a `kind=config` error, not rows. Trace timestamp ≈ root start (±100 ms tolerated) |

## 6. Results (2026-10-06, hermes-otel working tree after this round)

Batches of four turns on the minimal profile; every source scored clean
(`FAIL 0`). SKIPs are checks a traces-only source cannot take.

| source | result | fixed on the way |
|---|---|---|
| lgtm-local | PASS 51 | Tempo regex anchoring, zero-padded ids, root-first free-text search, widened card name, Prometheus raw samples, Grafana Explore link |
| openobserve-local | PASS 51 | trace-details link |
| uptrace-local | PASS 51 | server-side roots (2.1), per-process counter increases, histogram count = sum/avg |
| signoz-local | PASS 51 | numeric list columns, row start time, per-process latest + adapter increases |
| jaeger-local | PASS 38 · SKIP 10 | widened card = matched span |
| phoenix-local | PASS 39 · SKIP 9 | — |
| langfuse-local | PASS 35 · SKIP 13 | page cap, card attrs from metadata, widened search over observations, `tool.` prefix restored |
| live | PASS 37 · SKIP 14 | — (oracle) |

Page-side: the trace card takes turn totals from the root span when several
spans are matched. Harness-side learnings are in the check descriptions
above and in `backends.yaml`.

## 7. Definition of done (per backend)

- [ ] `report.md` has no FAIL; every WARN is explained in the report's notes.
- [ ] Screenshots looked at once by a human (they are the presentation check
      a script cannot make).
- [ ] Adapter and page fixes landed with unit tests; this SKILL and
      `backends.yaml` updated when a quirk was learned.
- [ ] The backend's issue (#293–#299) carries the report summary.
