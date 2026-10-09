# Local backends for hermes-otel — user manual

This directory lets you stand up any OTLP backend the plugin can talk to, send a
real Hermes turn into it, look at the result, and tear it down again. One folder
per backend, each self-contained:

```
docker-compose/
├── README.md                 ← this manual
├── all.sh                    ← up / down / nuke / status / list across folders
└── <backend>/
    ├── docker-compose.yaml   ← the stack; header comment = usage + ports + plugin snippet
    ├── README.md             ← why pick it, start/stop, login, verify, caveats
    └── …                     ← vendored upstream config files, unchanged
```

Compose names the project after the folder, so **no `-p` flag is needed**:

```bash
docker compose -f docker-compose/<backend>/docker-compose.yaml up -d
docker compose -f docker-compose/<backend>/docker-compose.yaml down        # stop, keep data
docker compose -f docker-compose/<backend>/docker-compose.yaml down -v     # stop, delete data
docker-compose/all.sh up phoenix openobserve        # several at once
docker-compose/all.sh status                        # what is running
```

## Which backend?

Signals are what the backend **stored** from one real Hermes turn through the
plugin (hermes-otel 1.19.0, 2026-10-05), not what its docs claim. `type:` is
the plugin backend type; `otlp` means the backend has no explicit type yet and
is driven through the generic one with the headers shown in its folder.

| Backend | Containers | UI | Traces | Metrics | Logs | `type:` | Pick it when… |
|---|---|---|---|---|---|---|---|
| [Phoenix](phoenix/) | 1 | :6006 | ✅ | ❌ | ❌ | `phoenix` | you want to see an LLM trace in 10 seconds |
| [OpenObserve](openobserve/) | 1 | :5080 | ✅ | ✅ | ✅ | `openobserve` | all three signals, one container, no port conflicts — the safe default |
| [Grafana LGTM](lgtm/) | 1 | :3000 | ✅ | ✅ | ✅ | `lgtm` | you want Grafana graphs (Tempo + Mimir + Loki in one image) |
| [Jaeger v1](jaeger/) | 1 | :16686 | ✅ | ❌ | ❌ | `jaeger` | plain span tree; the dashboard adapter reads it |
| [Jaeger v2](jaeger-v2/) | 1 | :16696 | ✅ | ❌ | ❌ | `jaeger` | reproducing #245 (v2 has only `/api/v3`) |
| [Tempo](tempo/) | 2 | :3020 | ✅ | ❌ | ❌ | `tempo` | standalone Tempo + Grafana, what `type: tempo` targets |
| [Langfuse](langfuse/) 3 / 4 | 6 | :3000 | ✅ | ❌ | ❌ | `langfuse` | the polished LLM UI; `LANGFUSE_VERSION=4` for #246 |
| [SigNoz](signoz/) | 5 | :3301 | ✅ | ✅ | ✅ | `signoz` | full APM with ClickHouse; heavy |
| [Uptrace](uptrace/) | 4 | :14318 | ✅ | ✅ | ✅ | `uptrace` | all three signals with a DSN-style setup |
| [Elastic](elastic/) | 3 | :15602 | ✅ | ✅ | ✅ | `elastic` | you already run Elastic: EDOT Collector in front of Elasticsearch + Kibana (verified 2026-10-07, 1.21.0) |
| [Parseable OSS](parseable/) | 2 | :8010 | ✅ | ✅ | ✅ | `otlp` | SQL over Parquet; shows the collector-in-front pattern |
| [OpenLIT](openlit/) | 2 | :3010 | ✅ | ✅ | ✅ | `openlit` | OTel-native LLM UI with all three signals, light |
| [MLflow](mlflow/) | 1 | :5001 | ✅ | ❌ | ❌ | `mlflow` | you already use MLflow; it prices traces itself |
| [Comet Opik](opik/) | 7 | :5173 | ✅ | ❌ | ❌ | `opik` | the best LLM-trace fit of the new batch (threads, span types, cost) |
| [Laminar](laminar/) | 5 | :5667 | ✅ | ❌ | ✅ | `laminar` | agent-focused UI; scripted API key |
| [LangWatch](langwatch/) | 5 | :5560 | ✅ | ✅ | ✅ | `langwatch` | all three signals in an LLM platform; slow first start |
| [Langtrace](langtrace/) | 3 | :3040 | partial | ❌ | ❌ | `otlp` | evaluating #224 only (AGPL, stale, drops bool/double attrs) |
| [Sigiro](sigiro/) | 1 | SQL :9999 | ✅ | ✅ | ✅ | `otlp` | scripted SQL checks; closed-source binary |
| [Maple Local](maple/) | 1 | :4388 | ✅ | ✅ | ✅ | `otlp` | single binary with UI + SQL; FSL licence |
| [Latitude](latitude/) | 13 | :3000 | ✅ | ❌ | ❌ | `latitude` | you have ~15 GB of disk and a reason (#230) |

Not here, and why: **AgentOps** builds from source, needs the Supabase CLI and
a JWT exchange the plugin cannot do (#231); **Monocle** is an SDK, not a
server (#14); **Helicone / Lunary** have no OTLP ingest (#232); Logfire, New
Relic, Datadog, Azure Monitor, Honeycomb, W&B Weave, LangSmith and telemetry.dev
are SaaS.

## The loop: start, send a turn, look, tear down

1. **Start** the stack and wait for its health. The folder README says how long
   (10 s for Phoenix, about 1 min for Opik, about 5 min for LangWatch).
2. **Point the plugin at it.** Copy the `backends:` snippet from the folder
   README (or the compose header) into `$HERMES_HOME/hermes_otel.yaml` (or the
   plugin's `config.yaml`). Use a distinct `project_name` per experiment so you
   can find it in the UI. Several backends can be listed at once; each gets its
   own export queue.
3. **Run a turn.** `hermes chat -Q --yolo -q "list the files here, then read README.md"`
   exercises an `agent` span, an `llm.*` span, two `api.*` spans and two
   `tool.*` spans. Prefer a fast, cheap model in the scratch home. `hermes -z`
   also works for traces and metrics but **exports no logs** (one-shot mode
   disables logging).
4. **Read the plugin's debug log first.** With `HERMES_OTEL_DEBUG=true` in the
   home's `.env`, `$HERMES_HOME/plugins/hermes_otel/debug.log` has one line per
   export: `export <name>: 3 span(s) -> SUCCESS` / `-> FAILURE (…)`. This tells
   you in seconds whether the backend accepted the batch, before you touch its
   UI. Metric export failures show up as `[sdk] … Exception while exporting
   metrics`.
5. **Query the backend** with the `Verify` command in its README. Mind the
   lags: Tempo search 20-30 s, Langfuse 10-20 s, Prometheus-style stores go
   stale 5 min after the process exits.
6. **Tear down** with `down -v`, and `docker rmi` the images if you are done
   with that backend for a while (see *Disk and memory*).

A scratch `HERMES_HOME` keeps this away from your real setup: copy your `.env`,
symlink `plugins/hermes_otel` to the repo checkout, write a minimal
`config.yaml` with a fast model, and run `hermes plugins enable hermes_otel`
once. The first turn in a fresh home builds its own venv (1-2 min).

## Ports

Every published host port, so a new stack can pick a free one. Rules: OTLP/HTTP
on `43x8` (gRPC on `43x7` when published), UIs that upstream puts on 3000 move
to `30x0`, everything else keeps upstream's port unless it collides with another
folder. The three stacks that keep 4318 or 3000 cannot run together.

| Port | Folder | What |
|---|---|---|
| 3000 | langfuse, lgtm, latitude | Langfuse UI / Grafana / Latitude web — one at a time |
| 3001, 3002 | latitude | API, OTLP ingest |
| 3010 | openlit | UI |
| 3020 | tempo | Grafana |
| 3040 | langtrace | UI + `/api/trace` ingest |
| 3100, 3200 | lgtm | Loki, Tempo |
| 3210 | tempo | Tempo API |
| 3301 | signoz | UI |
| 4317, 4318 | lgtm, jaeger | OTLP gRPC / HTTP — one at a time |
| 4327, 4328 | signoz | OTLP gRPC / HTTP |
| 4337, 4338 | openlit | OTLP gRPC / HTTP |
| 4348 | parseable | collector OTLP/HTTP |
| 4358 | tempo | OTLP/HTTP |
| 4368 | jaeger-v2 | OTLP/HTTP |
| 4377, 4378 | sigiro | OTLP gRPC / HTTP |
| 4388 | maple | UI + OTLP/HTTP |
| 5001 | mlflow | UI + OTLP/HTTP |
| 5080, 5081 | openobserve | UI + OTLP/HTTP, OTLP/gRPC |
| 5173 | opik | UI + API |
| 5432 | uptrace | Postgres |
| 5560 | langwatch | UI + API |
| 5667 | laminar | UI |
| 6006 | phoenix | UI + OTLP/HTTP |
| 8010 | parseable | UI + API |
| 8025 | latitude | Mailpit |
| 8100, 8101 | laminar | app-server HTTP / gRPC |
| 8123, 9000 | uptrace | ClickHouse |
| 9090 | lgtm | Prometheus |
| 9999 | sigiro | SQL query API |
| 14317, 14318 | uptrace | OTLP gRPC / UI + OTLP HTTP |
| 14319 | elastic | EDOT Collector OTLP/HTTP |
| 15602 | elastic | Kibana |
| 16686, 16696 | jaeger, jaeger-v2 | UI |
| 19201 | elastic | Elasticsearch API |

Check with `lsof -nP -iTCP:<port> -sTCP:LISTEN` when a stack refuses to bind;
dev servers love 3000 and 8000.

## Disk and memory

- **Images are big.** Langtrace's app image is 4.5 GB, Langfuse v4 3.6 GB,
  LangWatch 2.8 GB, Elastic 5.8 GB across its three images, Latitude about
  3 GB *per service*. Docker Desktop's VM disk
  fills up silently and the symptom is a Postgres that dies with "No space left
  on device" or a pull that fails mid-layer. Check with
  `docker run --rm alpine df -h /` and `docker rmi` what you are done with.
- **Memory.** Idle footprints measured after one turn: Phoenix / Jaeger /
  Sigiro < 100 MB; Tempo+Grafana 260 MB; Maple 340 MB; OpenLIT 560 MB; Laminar
  600 MB; Langtrace 780 MB; MLflow 1.2 GB; Elastic 1.6 GB (Elasticsearch
  900 MB + Kibana 630 MB); LangWatch 1.9 GB; Opik 2.1 GB; Langfuse 3 GB. On a 6 GB Docker Desktop run one of the heavy ones at a time.
- **CPU.** ClickHouse-based stacks (SigNoz, Uptrace, Langfuse, Opik, LangWatch,
  Laminar, Langtrace, OpenLIT) and Elasticsearch are the ones that make a laptop
  fan spin; a single Elasticsearch node that is starved goes `red` and indexes
  nothing while the collector drops batches (see `elastic/README.md`).

## Caveats you will hit

- **Signals a backend silently drops.** Laminar answers 200 on `/v1/metrics`
  and stores nothing; set `metrics: false`. MLflow, Opik and Latitude have no
  metrics/logs routes at all (404) — the generic `otlp` type defaults both on,
  so turn them off per entry or the debug log fills with failures.
- **Trace-level token totals double in Opik and LangWatch.** Both sum usage over
  every span and the plugin's `agent` span carries the turn's roll-up, so a
  turn with 24 299 prompt tokens shows 48 598 at trace level. Per-span numbers
  are right. Worth settling before `type: opik` / `type: langwatch` exist.
- **Attribute truncation.** Tempo cuts attribute values at 2048 bytes by
  default (`tempo/tempo.yaml` raises it). Langtrace drops bool and double
  attributes outright.
- **API keys.** Laminar, LangWatch, Langtrace and Latitude refuse spans
  without a project key. Laminar: `laminar/mint-api-key.sh`. Langtrace: curl
  recipe in its README. LangWatch: UI wizard once, then read the key from
  Postgres. Latitude: UI.
- **Read APIs differ from write APIs.** Jaeger v2 serves `/api/v3` only;
  Langfuse v4 removed `/api/public/traces` (use `/api/public/v2/observations`);
  Parseable OSS needs the collector because it refuses protobuf and has no API
  keys. The plugin dashboard adapters for Jaeger and Langfuse are affected
  (#245, #246).
- **Released images that need help.** OpenLIT 2.1.0 needs the Collector config
  mounted or nothing listens on 4318; Laminar's published frontend skips
  creating its Quickwit indexes; Langtrace's upstream ClickHouse healthcheck
  never passes (`localhost` → `::1`). Each folder carries the fix.
- **Anonymous telemetry** is switched off in every file where upstream has a
  knob (OpenLIT, Opik, Laminar, Langtrace, Tempo, Grafana, Parseable).
- **Secrets are local defaults** (admin/admin, example keys). Fine on a laptop;
  change them before anything else can reach the ports.

## Adding a backend

1. Create `docker-compose/<name>/` with `docker-compose.yaml`, a `README.md`
   with the same sections as the others (why pick it, start/stop, login,
   plugin snippet, verify, caveats), and any upstream config files **vendored
   unchanged**, saying which tag they came from.
2. Trim upstream's compose to what trace/metric/log ingest and the UI need, and
   list what you dropped in the header comment. Inline upstream's `.env`
   values rather than shipping dotfiles.
3. Container names `hermes-otel-<name>[-<service>]`, named volumes
   `<name>_<service>`, pinned image tags where the project publishes them, host
   ports from the map above, telemetry knobs off.
4. Header comment: what it is, upstream source, usage, ports with their
   override env vars, conflicts, the `backends:` snippet, a pointer to the
   README.
5. Run one real Hermes turn through it, read the debug log, query the store,
   and write down exactly what landed (spans, logs, metric rows) and what did
   not. Record resident memory and time-to-healthy.
6. Add the row to the table above and the ports to the map; mention the folder
   on the backend's page under `website/docs/backends/`.
