# Backend quirks: what each one does differently, and what hermes-otel does about it

OTLP fixes the wire format. It does not fix the URL a backend listens on, the
header it wants your key in, which of the three signals it stores, whether it
wants cumulative or delta counters, how it adds up tokens over a trace, how
long an attribute may be, or which API gives the data back. Every backend in
this directory answers those questions differently, and the plugin carries one
answer per `type:` so that a `backends:` entry is one line of intent rather
than a page of vendor notes.

This file is the catalog of those differences. Part 1 is one matrix per
category. Part 2 lists each backend's quirks with the exact treatment the
plugin gives it, and the issue where that was settled. Part 3 is the short
list of what the plugin normalizes. Every statement comes from
`hermes_otel/backends.py`, a dashboard adapter under
`hermes_otel/dashboard/backends/`, a folder README here, a page under
`website/docs/backends/`, or a closed issue; nothing here is a guess. Where a
figure is quoted it carries the date and version it was measured against.

Companion files: [`README.md`](README.md) is the manual (which backend, the
test loop, ports, disk), each `<backend>/README.md` has the start/stop/verify
recipe, and the docs site has the same matrices at `/backends/quirks`.

## Part 1: the matrices

Legend: **std** means the standard OTLP/HTTP paths `/v1/traces`,
`/v1/metrics`, `/v1/logs`. "the plugin completes it" means you can give the
base URL (or the vendor prefix, or the full traces URL) and the resolver
produces the full per-signal URLs. "derived" means `/v1/metrics` and
`/v1/logs` are produced from the traces URL by swapping the last path segment
(`helpers.derive_signal_endpoint`).

### A. Where OTLP lands

| Backend | `type:` | Ingest path | Port notes | What the plugin does |
|---|---|---|---|---|
| Phoenix | `phoenix` | std, traces only | ingest and UI share :6006; no redirect from the root, the URL must end in `/v1/traces` | endpoint used as given |
| Langfuse | `langfuse` | vendor prefix `/api/public/otel/v1/traces` | ingest and UI share :3000 | completes root URL, `/api/public/otel`, or full URL; a root URL used to be posted as-is and died with 405 |
| LangSmith | (env only) | not OTLP: `POST /runs`, `PATCH /runs/{id}` | cloud only | separate code path (`langsmith_backend.py`), not a `backends:` entry |
| SigNoz | `signoz` | std | collector :4328 here (upstream 4318); UI/API :3301 | derived |
| Jaeger v1 / v2 | `jaeger` | std | v1 ingest :4318, UI :16686; v2 ingest :4368, UI :16696 | endpoint used as given |
| Tempo | `tempo` | std | ingest :4358 here, API :3210, Grafana :3020 | endpoint used as given |
| Grafana LGTM | `lgtm` | std | collector :4318 (gRPC :4317), Grafana :3000, Loki :3100, Tempo :3200, Prometheus :9090 | derived; `lgtm` is a self-documenting alias of `otlp` |
| Uptrace | `uptrace` | std | ingest and UI share :14318; gRPC :14317 | derived |
| OpenObserve | `openobserve` | org-prefixed `/api/<org>/v1/traces` | ingest and UI share :5080; gRPC :5081 | derived (the org stays in the path); a wrong path 404s silently |
| Parseable Cloud / Enterprise | `parseable` | std, at the ingestor URL | query/UI endpoint may refuse ingest in distributed setups | derived, one dataset header per signal |
| Parseable OSS | `otlp` via collector | std on the bundled collector :4348 | UI :8010 | collector re-encodes to OTLP/JSON because OSS refuses protobuf |
| Honeycomb | `honeycomb` | std on `api.honeycomb.io` or `api.eu1.honeycomb.io` | SaaS | base chosen by `region: us\|eu`, derived |
| W&B Weave | `weave` | vendor path `/otel/v1/traces` (SaaS) or `/traces/otel/v1/traces` (dedicated / self-managed) | SaaS | completes from `base_url` |
| Elastic | `elastic` | std, one endpoint for all signals | EDOT Collector :14319 here; ES :19201; Kibana :15602 | strips any `/v1/<signal>` and re-appends `/v1/traces`, derived; no default host |
| OpenLIT | `openlit` | std on the collector :4338 (4318 in-container) | UI :3010 does **not** proxy `/v1/*` on 2.1.0 | completes base URL, derived; no default host |
| MLflow | `mlflow` | std, traces only, on the tracking server :5001 (5000 in-container) | one port for UI and ingest | completes base URL |
| Comet Opik | `opik` | vendor prefix `/api/v1/private/otel/v1/traces` | ingest and UI share :5173 (nginx) | completes base, `…/api`, or prefix form; cloud default host |
| Laminar | `laminar` | std on the **app-server** :8100 (upstream 8000) | UI :5667 does not ingest; gRPC :8101 | completes base URL; cloud default `api.lmnr.ai` |
| LangWatch | `langwatch` | vendor prefix `/api/otel/v1/traces` | ingest, API and UI share :5560 | completes base or prefix form, derived; cloud default host |
| Latitude | `latitude` | std, traces only, on the **ingest service** :3002 | web :3000, API :3001, Mailpit :8025 | completes base URL; cloud default `ingest.latitude.so` |
| telemetry.dev | `otlp` | std on `ingest.telemetry.dev` | SaaS | generic; derived |
| Langtrace | `otlp` | custom `/api/trace` (no `/v1/`) | ingest and UI share :3040 | generic, URL used verbatim; derivation does not apply |
| Sigiro | `otlp` | std :4378 (gRPC :4377) | SQL API :9999, no UI | generic; derived |
| Maple Local | `otlp` | std :4388 | ingest and UI share :4388 | generic; derived |

Transport everywhere is OTLP/HTTP with protobuf bodies
(`opentelemetry-exporter-otlp-proto-http`); gRPC is never used. Parseable OSS
is the one backend that refuses protobuf, hence its collector.

### B. Who you are: authentication

| Backend | Header and scheme | Required? | Where the local key comes from |
|---|---|---|---|
| Phoenix | none locally; Arize cloud takes `api_key: <key>` as an extra header | no | none |
| Langfuse | `Authorization: Basic base64(public:secret)` plus `x-langfuse-ingestion-version: 4` | yes, both keys | pre-seeded by headless init (`lf_pk_hermes_dev` / `lf_sk_hermes_dev`) |
| LangSmith | `x-api-key` | yes | cloud account |
| SigNoz | `signoz-ingestion-key` (cloud); nothing self-hosted | cloud only | cloud UI |
| Jaeger, Tempo, LGTM | none (Grafana Cloud: `Authorization: Basic <token>` via `headers`) | no | none |
| Uptrace | `uptrace-dsn: http://<project_token>@host:14318?grpc=14317` | yes | seeded in `uptrace.yml` (`project_tokens[0].token`) |
| OpenObserve | `Authorization: Basic base64(email:password)` | yes | seeded root user (`root@example.com` / `Complexpass#123`) |
| Parseable (type) | `X-API-Key` | yes | Cloud / Enterprise UI. OSS has no API keys: any `X-API-Key` is a 401, so the collector adds Basic auth instead |
| Honeycomb | `x-honeycomb-team` | yes | cloud UI; keys are region-specific (a US key is a 401 on EU) |
| W&B Weave | `wandb-api-key` | yes | W&B account |
| Elastic | `Authorization: ApiKey <key>` (not Bearer); omitted when no key resolves | cloud yes, local EDOT no | cloud UI; needs the `apm` app's `event:write` privilege |
| OpenLIT | `Authorization: Bearer <key>`; omitted when none | no (`OTLP_REQUIRE_API_KEY=true` makes it mandatory) | UI; without a key data lands in the `INIT_DB_*` defaults |
| MLflow | `Authorization: Bearer <tracking token>`; omitted when none | no | none locally |
| Comet Opik | `Authorization: <key>` (**bare**, no Bearer) plus `Comet-Workspace` | cloud yes, self-hosted no | cloud UI |
| Laminar | `Authorization: Bearer <project key>` | yes; a missing key skips the entry at startup | `laminar/mint-api-key.sh` (signs in, then `POST /api/cli/api-key`) |
| LangWatch | `Authorization: Bearer sk-lw-…` | yes; missing key skips the entry | UI onboarding wizard once, `langwatch/mint-api-key.py` (Playwright), or `select "apiKey" from mydb."Project"` in Postgres |
| Latitude | `Authorization: Bearer <key>` | yes; missing key skips the entry | UI only (encrypted at rest, cannot be read from Postgres) |
| telemetry.dev | `Authorization: Bearer td_live_…` via `headers` | yes | cloud UI |
| Langtrace | `x-api-key` via `headers` | yes | UI, or the NextAuth curl recipe in its README |
| Sigiro, Maple | none | no | none |

Five different spellings of "here is my key" on the `Authorization` header
alone: Basic (Langfuse, OpenObserve), Bearer (OpenLIT, MLflow, Laminar,
LangWatch, Latitude, telemetry.dev), `ApiKey` (Elastic), the bare key (Opik),
and vendor headers for the rest. The per-type resolver builds the right one
from `api_key` / `api_key_env` / the vendor's own env var, and the global
`headers:` block is merged **under** the resolver's headers so it can never
clobber the auth it built.

Env-var mode has one more rule: a vendor's own credential variable
(`LANGFUSE_PUBLIC_KEY`, `WANDB_API_KEY`, `HONEYCOMB_API_KEY`,
`ELASTIC_API_KEY`, `OPENLIT_API_KEY`, `MLFLOW_TRACKING_TOKEN`, `OPIK_API_KEY`,
`LMNR_PROJECT_API_KEY`, `LANGWATCH_API_KEY`, `LATITUDE_API_KEY`) never switches
export on by itself, because those are often set for other tools and Hermes
loads `$HERMES_HOME/.env` into the process. One plugin-namespaced `OTEL_*`
variable is the opt-in (#259).

### C. Where it files your data: routing

| Backend | Routing mechanism | Plugin field |
|---|---|---|
| Phoenix | project = resource attribute `openinference.project.name` | top-level `project_name` |
| Langfuse | project = the key pair | none |
| SigNoz | none | none |
| Jaeger / Tempo / LGTM | `service.name` | `project_name` / `resource_attributes` |
| Uptrace | project = the DSN | `dsn` |
| OpenObserve | org in the URL path, stream in the `stream-name` header (default `default`; a bad name is a 400) | `stream_name` |
| Parseable | one dataset per signal: `X-P-Stream: hermes-{traces,metrics,logs}` plus `X-P-Log-Source: otel-{traces,metrics,logs}` | `traces_dataset`, `metrics_dataset`, `logs_dataset` |
| Honeycomb | `x-honeycomb-dataset` is honoured only by Classic keys (required there for every signal); Environments keys route traces by `service.name` and metrics to the `Metrics` dataset | `dataset` (leave unset on a modern key) |
| W&B Weave | **resource attributes** `wandb.entity` and `wandb.project`; they live on the one shared Resource, so one Weave project per process | `entity`, `project` |
| Elastic | **resource attributes** `data_stream.dataset` / `data_stream.namespace`; the collector appends `.otel`, so `dataset: hermes_otel` lands in `traces-hermes_otel.otel-<namespace>`. Components allow `[a-z0-9_.]` only, no `-`; an invalid value skips the entry; two elastic entries with different values conflict | `dataset`, `namespace` |
| OpenLIT | org / project / environment = the key | `api_key` |
| MLflow | **mandatory** `x-mlflow-experiment-id` (400 without it; default `"0"`, the built-in Default experiment); optional `X-MLFLOW-WORKSPACE` | `experiment_id`, `workspace` |
| Comet Opik | `projectName` header (auto-created, default "Default Project"); `Comet-Workspace` on cloud | `project`, `workspace` |
| Laminar | project = the key | `api_key` |
| LangWatch | `X-Project-Id`, needed only with a service key | `project` |
| Latitude | **mandatory** `X-Latitude-Project: <slug>`; spans without it are rejected, so a missing slug skips the entry | `project` |
| Langtrace | project = the key; one ClickHouse table per project id | `headers` |

Two backends route by **resource attribute** instead of header (Weave,
Elastic). Because the plugin has one TracerProvider and therefore one
Resource, those attributes reach every configured backend, and two entries of
the same type with different values are a configuration error at init.

### D. Which signals it stores, and what happens to the others

"Stored" is what landed from a real Hermes turn, not what a vendor's docs
claim (verified dates are on each folder README). The failure mode matters
because the plugin's debug log only sees the HTTP status.

| Backend | Traces | Metrics | Logs | Failure mode for the missing ones | Plugin default |
|---|---|---|---|---|---|
| Phoenix | yes | no | no | 405 on `/v1/metrics` and `/v1/logs` (#160) | `_TRACES_ONLY`: no metrics or logs exporter is created |
| Langfuse | yes | no | no | no such routes | `_TRACES_ONLY` |
| LangSmith | yes | no | no | not OTLP | n/a |
| SigNoz | yes | yes | yes | before first-run setup the collector runs a `nop` pipeline and resets the connection | all on |
| Jaeger | yes | no | no | not served (a forced `metrics: true` is expected to 404) | `_TRACES_ONLY` |
| Tempo | yes | no | no | not served | `_TRACES_ONLY` (even behind a collector that would accept them; set `metrics: true` explicitly) |
| Grafana LGTM | yes | yes | yes | — | all on |
| Uptrace | yes | yes | yes | — | all on |
| OpenObserve | yes | yes | yes | — | all on |
| Parseable | yes | yes | yes | OSS: `400 Protobuf ingestion is not supported` without the collector | all on |
| Honeycomb | yes | yes | yes | — | all on |
| W&B Weave | yes | no | no | W&B documents the endpoint for traces only | `_TRACES_ONLY` |
| Elastic | yes | yes | yes | **silent**: exporter says SUCCESS, the collector's bulk indexer fails if the cluster is red | all on, delta preset |
| OpenLIT | yes | yes | yes | — | all on |
| MLflow | yes | no | no | 404 on `/v1/metrics` and `/v1/logs` | `_TRACES_ONLY` |
| Comet Opik | yes | no | no | 404 under the vendor prefix | `_TRACES_ONLY` |
| Laminar | yes | **no** | yes | `/v1/metrics` answers **200 and stores nothing** (a placeholder handler) | `_NO_METRICS`: metrics off, logs on |
| LangWatch | yes | yes | yes | — | all on |
| Latitude | yes | no | no | the ingest service has only `/v1/traces` and health routes | `_TRACES_ONLY` |
| telemetry.dev | yes | yes | yes | — | generic `otlp`: all on |
| Langtrace | partial | no | no | not served | generic `otlp`: set `metrics: false`, `logs: false` on the entry |
| Sigiro, Maple | yes | yes | yes | — | generic `otlp`: all on |

Three failure modes, one of them invisible: a 404/405 you can see in the
debug log, a connection reset you can see, and Laminar's 200-with-nothing
and Elastic's red-cluster drop that you cannot. The per-type defaults
(`_TRACES_ONLY`, `_LOGS_CAPABLE`, `_NO_METRICS` in `backends.py`) exist so a
user never has to discover the invisible ones. The generic `otlp` type cannot
know, so it defaults every signal on and you turn them off per entry.

`hermes -z` (one-shot) exports traces and metrics but **no logs**: one-shot
mode disables Python logging before the handler sees a record.

### E. Metrics: temporality, histograms, names, staleness

| Backend | Temporality it wants | Plugin preset | Exponential histograms | How the stored name looks |
|---|---|---|---|---|
| Grafana LGTM (Mimir), Tempo-side Prometheus | cumulative; delta is dropped unless `otlp-deltatocumulative` is on | cumulative (SDK default) | Prometheus 3.8+ native histograms; Mimir only with native-histogram ingestion on | `hermes_token_usage_total`, histograms grow `_sum` / `_count` / `_bucket`, units appended (`_seconds`, `_milliseconds`) |
| OpenObserve | cumulative works; delta undocumented | cumulative | undocumented | one **stream per metric**, `_total` dropped (`hermes_token_usage`), histograms split into `_sum` / `_count` / `_bucket` streams |
| SigNoz | recommends delta; exponential histograms delta-only and self-hosted-only | **delta** | self-hosted only | `hermes_token_usage_total`, `.sum` / `.count` / `.bucket` views |
| Uptrace | prefers delta, converts cumulative | **delta** | recommended | dotted names kept; MQL `$m` |
| Elastic | **delta required**: ES does not handle cumulative histograms and converts cumulative counters lossily | **delta** | accepted | ES data streams |
| Honeycomb | either | cumulative | not verified | — |
| OpenLIT | cumulative stored as-is (verified) | cumulative | stored (exponential histogram type) | ClickHouse `otel_metrics_*` tables |
| Parseable, generic `otlp`, Sigiro, Maple | whatever the collector / store does | cumulative | Sigiro has an `exp_histogram` table | — |
| Datadog, New Relic, Logfire (generic `otlp`) | **delta required** (Datadog rejects cumulative sums; Logfire dashboards stay empty; New Relic prefers delta) | none yet (#232): set `metrics_temporality: delta` on the entry | Datadog and New Relic accept them | — |
| LangWatch | stored (73 metric points from one turn, 2026-10) | cumulative | — | — |

Presets live in `_TEMPORALITY_PRESETS` (`signoz`, `uptrace`, `elastic` →
`delta`, #233). An explicit `metrics_temporality` on the entry wins; the
top-level key sets every backend. `metrics_histogram: exponential` is global,
so use it only when every configured backend accepts base-2 histograms.

Two cross-backend facts about **short-lived runs**: a Prometheus-family store
treats a series as stale five minutes after its last sample, so a one-shot
turn's single cumulative point disappears from "now" queries (widen the
range); and Datadog / New Relic treat the first cumulative point as a
baseline, so the same turn counts as zero there but in full as delta. Long
running gateways are unaffected either way.

### F. Semantics: what the UI understands, and how it adds up a trace

| Backend | Attribute namespace it reads | Trace-level token total | Prices calls itself? | Session / thread key |
|---|---|---|---|---|
| Phoenix | OpenInference (`llm.token_count.*`, `input.value`, `openinference.span.kind`) | reads the root's own roll-up | yes, from token counts | `session.id` |
| Langfuse | `gen_ai.*` (including legacy `gen_ai.system`) | reads the root; zero-fills `usage` on non-GENERATION observations | yes | `session.id` (automatic grouping) |
| LangSmith | mapped by the plugin to Run API fields plus `usage_metadata` | per run | — | session id → thread |
| SigNoz | `gen_ai.*` in its LLM views; otherwise raw tags | n/a (span table) | via a derived metric | any attribute |
| Jaeger, Tempo, LGTM | none; raw tags | n/a | no | any attribute (Tempo: TraceQL) |
| Uptrace, OpenObserve | all attributes searchable, keys **flattened with underscores** | n/a | no | `hermes_session_id` |
| Honeycomb | columns auto-created from whatever arrives (`gen_ai.*`, `llm.*`) | n/a | no | any column |
| W&B Weave | `gen_ai.*`; `wandb.thread_id`, `wandb.is_turn=true` on the root | per call | — | `gen_ai.conversation.id` / `wandb.thread_id` |
| Elastic | raw fields; Kibana dashboard uses `hermes.token.usage`, `hermes.model.usage`, tool durations | n/a | no | any field |
| OpenLIT | `gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.tool.*` | n/a | no | `hermes.session_id` on logs |
| MLflow | translators for GenAI semconv, OpenInference and OpenLLMetry | **computes itself** (`mlflow.trace.tokenUsage`), reads the root | yes (`mlflow.trace.cost`, priced models only) | `mlflow.trace.session` = Hermes session id |
| Comet Opik | GenAI semconv and OpenInference; `gen_ai.request.model`, `gen_ai.provider.name` | **sums every span** → would double | yes (`total_estimated_cost`, null for unknown models; the doubling also doubled the cost) | `thread_id` = Hermes session |
| Laminar | `gen_ai.usage.*`; span types LLM (`api.*`, `llm.*`) and TOOL | reads the root | yes (0 for unknown models) | — |
| LangWatch | `gen_ai.*` | **sums every span** → would double | — | `thread_id` = Hermes session |
| Latitude | GenAI semconv, explicitly | per span columns (`cost_*`) | yes | session column |
| telemetry.dev | `gen_ai.*` only; OpenInference attributes ignored | — | — | `gen_ai.conversation.id` on the root |
| Langtrace | its own `gen_ai.*`-based schema | — | — | — |
| Maple | AI trace index (model, tokens, cost, tool); pickup unverified | — | — | — |

Three things the plugin does here:

1. It emits **both** conventions on every span: OTel GenAI `gen_ai.*` and
   OpenInference `llm.*`, so Phoenix and Langfuse and OpenLIT each find the
   spelling they index on without a mapping adapter.
2. It puts the turn's token total on the `agent` / `cron` root **and** on each
   `api.*` span. Backends that total a trace by summing every span (Opik,
   LangWatch) would then show twice the tokens, so for those two types the
   exporter bound to that entry rebuilds the root without
   `gen_ai.usage.*` / `llm.token_count.*` (`root_usage: false`, the
   `_NO_ROOT_USAGE_PRESETS` preset, #327; PR #353, open on 2026-10-10, so on
   1.23 the totals still double). Phoenix, Langfuse, MLflow and
   Laminar read the root's own numbers and keep it. Only that backend's copy
   changes; every other exporter and the live store still get the roll-up.
3. It sets `session.id`, `gen_ai.conversation.id` and the legacy
   `hermes.session_id`, plus `wandb.thread_id` for Weave, so each backend's
   notion of "conversation" lines up with a Hermes session.

### G. Attribute limits

| Backend | Limit | Treatment |
|---|---|---|
| Tempo | truncates attribute values at **2048 bytes** by default; cut `gen_ai.input.messages` under `content_capture: full` | `tempo/tempo.yaml` sets `distributor.max_attribute_bytes: 131072` |
| Langtrace | keeps **string and int** attributes only; **drops bool and double**; reads only `resourceSpans[0]` of a batch | documented; evaluation-only backend (#224) |
| Langfuse | drops the exporter's `tool.` prefix on TOOL observations | the dashboard adapter restores it |
| OpenObserve, Uptrace, Loki | dots in attribute names become underscores on storage (`llm.model_name` → `llm_model_name`), irreversibly | the adapters carry a `KNOWN_ATTRIBUTES` table to map them back (#158); collisions resolve to the name with more dots |
| Uptrace 2.1 | attribute keys carry a type suffix on the read side (`hermes_session_id::str`) | stripped by the adapter |
| LangWatch, Latitude | request caps of 10 MiB and 32 MiB | the span batch is auto-lowered to 64 when `content_capture: full`, because a 512-span POST of full prompts exceeds receiver limits (#199) |
| New Relic (generic `otlp`) | payloads over 1 MB rejected; attribute values at most 4095 chars; at most 128 span attributes and 64 resource attributes | use `content_capture: preview` or a smaller `max_export_batch_size` (#227) |
| All (plugin side) | log attribute values are capped by `max_attribute_length` (default 4096, applied after redaction) | configurable |

### H. Reading it back: query API, auth, version splits

The dashboard tab and the `observability` skill read eight types back
through an adapter. Each one speaks a different API and, in four cases, two
versions of it.

| Backend | Read API | Read-side auth vs write-side | Version split | How the adapter finds a turn's root |
|---|---|---|---|---|
| Phoenix | **GraphQL** only, `POST /graphql` | optional Bearer (write: none) | Phoenix 20 removed `rootSpansOnly` / `orphanSpanAsRootSpan`; the adapter introspects the schema once per URL and sends `parent_id is None` instead | `parent_id is None`; the project is never substituted (#159) |
| Langfuse | v3: `/api/public/traces`, `/api/public/traces/{id}`, `/api/public/observations`; v4: `/api/public/v2/observations` plus `/api/public/v2/metrics` | same Basic pair | **v3 vs v4**: v4 `events_only` removed `/api/public/traces`, `/observations`, `/sessions` (404 naming `events_only`); probed once per URL, `query_api: v3\|v4` pins (#246); cloud retires v3 routes 2026-11-16 | v3: list is per trace; detail root = observation with no parent, else a synthetic root (#159). v4: AGENT observations with `isRootObservation` |
| SigNoz | `POST /api/v4/query_range` (query builder) for all three signals; `GET /api/v1/traces/{id}` | **always required**, even on localhost: sent as both `SIGNOZ-API-KEY` (PAT) and `Authorization: Bearer` (session JWT; the OSS build has no PAT endpoint, so the key is a 30-minute JWT) | — | `parentSpanID = ""` |
| Jaeger | v1: `/api/traces`; v2: **`/api/v3/...`** (OTLP-JSON, the only API v2 serves) | optional Bearer (write: none) | **v1 vs v2** (#245): probed once per URL, classic first because Jaeger 1.x also exposes an `/api/v3` gateway with snake_case params; `query_api: v1\|v3` pins. v2 answers an empty search with 404 "No traces found" | client-side: the span with no `CHILD_OF` reference; every search must name a service |
| Tempo / LGTM | Tempo `/api/search` (TraceQL) + `/api/traces/{id}`; Prometheus `/api/v1/query` (range vectors) and `/api/v1/query_range`; Loki `/loki/api/v1/query_range` | none | — | TraceQL `nestedSetParent < 0` with `spss=1`; trace ids re-padded to 32 hex digits because Tempo drops leading zeros |
| Uptrace | `/internal/v1` in **two dialects** (2.1: `/spans/{p}`, `/logs/{p}`, `/metrics/{p}`, ms times, `::str` keys; 2.0: everything under `/tracing/{p}`); MQL for metrics | a **user** token (`user_token_env`), not the DSN's project token | **2.0 vs 2.1**, probed once per process via `/internal/v1/logs/{p}/systems` (2.0 answers with the SPA's HTML) (#243, #298) | 2.1: `_parent_id = ""` server-side; 2.0: client-side from `parentId` |
| OpenObserve | `POST /api/{org}/_search?type=traces\|metrics\|logs` with SQL; stream schema endpoint for columns | same Basic pair | the parent column is named `reference_parent_span_id`, `reference` (a JSON string) or `parent_span_id` depending on the build; tried in order (#299) | that column `IS NULL OR = ''` |
| Honeycomb, Weave, Elastic, Parseable, OpenLIT, MLflow, Opik, Laminar, LangWatch, Latitude, `otlp` | **no adapter**: the tab shows them as unavailable and the Live source or the backend's own UI is the answer | — | Honeycomb's Query Data API is Enterprise-gated, asynchronous and limited to 7 days | — |

Metric reads have their own quirks, all about **one-shot processes**:
PromQL `increase()` never counts a series' first sample, so the Prometheus
adapter reads raw range-vector samples and counts a first sample when the
series started inside the window (`counter_increases`, #296); SigNoz's own
`increase` over-counts turns that share a step and stamps each step at its
epoch-aligned start, so the adapter reads `latest` per process and takes the
differences itself (#297); Uptrace forward-fills a counter's cumulative value
and its histogram `count($m)` is the number of samples, not observations, so
the adapter computes `sum($m)/avg($m)` (#298); OpenObserve returns numbers as
digit strings and lists metric streams without a time bound (#299).

Log reads differ in where the event name lives: Loki and OpenObserve have an
`event_name` column (OpenObserve errors on it until the first event is
ingested; the adapter answers empty instead, #268), SigNoz keeps no column
for it and uses the `event.name` attribute, Uptrace keeps it as the
`event_name` attribute while its own `eventName` is the row kind.

### I. Operations: footprint, first run, and upstream bits each folder patches

Resident memory is after one turn (`README.md`, Disk and memory); time to
healthy is from the folder READMEs.

| Backend | Containers | Resident | Ready in | First-run step | Upstream breakage this folder works around | Licence |
|---|---|---|---|---|---|---|
| Phoenix | 1 | under 100 MB | ~10 s | none | image is `latest`, unpinned; SQLite dies with the container unless you add a volume | Elastic License 2.0 |
| Langfuse | 6 | ~3 GB | 20-60 s; traces appear 10-20 s after export (async via MinIO) | none (headless init seeds user and keys) | `LANGFUSE_VERSION=4` is a separate data set: `down -v` when switching | MIT core + EE folders |
| SigNoz | 5 | — | 30-60 s | **mandatory** admin registration (`POST /api/v1/register`, 12+ char mixed password) or the collector stays `nop` and resets connections | — | OSS |
| Jaeger v1 / v2 | 1 | ~25 MB | seconds | none | v1 line ended at 1.76.0 (Dec 2025); v2 pinned 2.21.0 | Apache-2.0 |
| Tempo | 2 | ~260 MB | search lags 20-30 s | none | metrics-generator removed from config; attribute limit raised (G) | AGPL-3.0 |
| Grafana LGTM | 1 | — | ~30 s | Grafana admin/admin | `spanmetrics` connector dimensions set to the plugin's attribute names; skipped by `all.sh` because of port 3000 / 4318 | AGPL-3.0 |
| Uptrace | 4 | — | ~20 s (upstream's compose: ~60 s) | none (DSN seeded) | trimmed from upstream's 12 services; pinned 2.1.0-beta.5 | OSS + premium features |
| OpenObserve | 1 | — | seconds | none | healthcheck is a false negative | OSS |
| Parseable OSS | 2 | ~190 MB | seconds | none | collector in front: protobuf refused, no API keys | AGPL-3.0 |
| Elastic | 3 | ~1.6 GB | wait for cluster `yellow`/`green` | none | disk watermark disabled (a Docker VM above 90 % makes every shard unassigned with no plugin-side error); 90 s exporter timeout + queue in `otel.yaml` | Elastic |
| OpenLIT | 2 | ~560 MB | ~30 s | login `user@openlit.io` | **2.1.0 image needs the collector config mounted** or nothing listens on 4318 (upstream PR 1588); "Unknown table otel_traces" until the first span | Apache-2.0 |
| MLflow | 1 | ~1.2 GB | ~20 s | none | needs MLflow ≥ 3.6 **and a SQL store** (the file store refuses OTLP); host port 5001 because macOS AirPlay owns 5000 | Apache-2.0 |
| Comet Opik | 7 + a bucket job | ~2.1 GB | ~1-3 min | none | python-backend dropped; `OPIK_USAGE_REPORT_ENABLED=false` | Apache-2.0 |
| Laminar | 5 + init job | ~600 MB | ~1 min | mint a key (B) | published frontend skips creating the Quickwit `spans_v2` index: a `quickwit-init` job creates it or span search stays empty | Apache-2.0 |
| LangWatch | 5 | ~1.9 GB | **~5 min** of ClickHouse migrations (`/api/health` → 204) | onboarding wizard mints the key | NLP and evaluator services dropped; `latest` tag | Apache-2.0 |
| Latitude | 13 | — | ~2 min after an ~18 GB pull | magic-link sign-in via Mailpit; create project; copy key | ships its **own** Hermes plugin: running both double-traces every turn | MIT |
| Langtrace | 3 | ~780 MB | — | login `admin@langtrace.ai` | upstream ClickHouse healthcheck on `localhost` resolves to `::1` and never passes; 4.45 GB image; last release 2025-04 | AGPL-3.0 |
| Sigiro | 1 | 76 MB | seconds | none | closed source, `latest` only | proprietary |
| Maple Local | 1 | ~340 MB | seconds (`up -d --build`) | none | built from a release tarball; needs `libchdb.so` beside the binary | FSL-1.1 (source-available) |

Anonymous usage telemetry is switched off in every file where upstream has a
knob (OpenLIT, Opik, Laminar, Langtrace, Tempo, Grafana, Parseable).

### J. Not in this directory, and why

| Candidate | Reason | Issue |
|---|---|---|
| AgentOps | builds from source, needs the Supabase CLI and a JWT exchange the plugin cannot do | #231 |
| Monocle | an SDK, not a server | #14 |
| Helicone, Lunary | no OTLP ingest | #232 |
| Pydantic Logfire | all three signals at `logfire-{us,eu}.pydantic.dev/v1/*`; the header is a **bare write token**; dashboards stay empty on cumulative metrics, so `delta` is needed; self-host is enterprise-only; unbuilt until there is an account to verify against | #225 |
| Datadog | agentless intake `otlp.<site>/v1/*`, `dd-api-key` on every signal and **`dd-otlp-source: llmobs`** on traces to land in Agent Observability; metrics intake accepts **delta only**; payload caps 512 KiB / 5.1 MiB / 15 MiB; 3-5 min delay | #226 |
| New Relic | `otlp.nr-data.net/v1/*`, `api-key: <license key>`; **1 MB payload cap**, 4095-char values, 128 span attributes; prefers delta | #227 |
| Azure Monitor | no static-key OTLP endpoint: needs a Microsoft Entra bearer from the Collector's `azure_auth` extension; requires delta and exponential histograms; proposed as docs-only through a contrib Collector | #228 |
| (all four) | work today through generic `otlp` with `metrics_temporality: delta`; explicit types tracked | #232 |
| Honeycomb, W&B Weave, LangSmith, telemetry.dev | SaaS only; documented on the site, nothing to compose | — |

## Part 2: each backend's quirks and the treatment

Format: **quirk** → treatment (issue).

### Phoenix (`phoenix`)
- Ingest and UI on one port, no redirect from the root → the endpoint must end in `/v1/traces`; the resolver uses it as given.
- 405 on `/v1/metrics` and `/v1/logs` → in `_TRACES_ONLY`; no metrics or logs exporter is created; banner says "(traces only)" (#160). A collector in front can still take them: `metrics: true` on the entry overrides.
- Reads OpenInference, not GenAI → the plugin emits both; tokens are on the `api.*` spans and Phoenix totals them itself.
- No default endpoint, despite what early docs promised (#93) → `requires endpoint`.
- `status=error` on Phoenix means the **root** span's status, while the Live source means any span errored (#293); documented as a semantic difference.
- GraphQL is the only read API; Phoenix 20 dropped the `rootSpansOnly` argument (every search became a 502 on 20.20.0, #293) → the adapter introspects the `Project` type once per URL and switches to `parent_id is None`. Legacy builds default `orphanSpanAsRootSpan` on, which leaks `api.*` spans whose parent was paged out → forced off.
- A project is the service → the `service` filter is `none`; a missing `project_name` is an error that lists the projects, never a silent fallback (#159).
- `attributes` comes back as one nested JSON string → flattened to dotted keys by the adapter.

### Langfuse (`langfuse`, v3 and v4)
- Vendor ingest prefix `/api/public/otel` → `_langfuse_traces_url` completes a root URL, the prefix, or the full URL (a root URL used to be posted as-is and every export died with a 405 that only the `opentelemetry` logger saw; PR #169, and the reason #167 added the per-batch `SUCCESS|FAILURE` lines to debug.log).
- Basic auth from two keys, plus `x-langfuse-ingestion-version: 4` → built by the resolver; both keys required.
- Vendor `LANGFUSE_*` variables are often set for other tools, and used to switch on export to Langfuse Cloud with full content capture → they fill credentials only; `OTEL_LANGFUSE_*` is the opt-in (#258, #259, PRs #255, #261, #263).
- `docker.io/minio/minio` left Docker Hub → the compose uses `cgr.dev/chainguard/minio`, as upstream does (PR #340).
- Traces only → `_TRACES_ONLY`.
- **v4 `events_only` removed the v3 trace endpoints** (`/api/public/traces`, `/observations`, `/sessions`) → the adapter probes `GET /api/public/traces?limit=1`, treats a 404 naming `events_only` as v4, and reads `/api/public/v2/observations` plus `/api/public/v2/metrics` instead; `query_api: v3|v4` pins (#246). v4 observations carry no input, output or per-span usage; trace totals come from the metrics query.
- OTLP-ingested observations keep the span attributes verbatim under `metadata.attributes`; Langfuse's own `usage` is zero-filled on non-GENERATION types → the adapter reads the attributes first and skips `usage` on OTLP observations, which fixed the header showing 0 tokens (#346).
- Langfuse drops the `tool.` prefix on TOOL observations → restored by the adapter.
- Ingest is asynchronous through MinIO → traces appear 10-20 s after a SUCCESS.
- v3 and v4 do not share data → `down -v` when switching `LANGFUSE_VERSION`. Port 3000 collides with Grafana (LGTM) and Latitude.

### LangSmith (env only)
- Not OTLP: `POST /runs` / `PATCH /runs/{id}` with `x-api-key` → its own backend module with one daemon worker over a bounded queue (#91); not a valid `backends:` entry, and `LANGSMITH_TRACING=true` short-circuits the whole `backends:` list, so it is fan-out **or** LangSmith.
- Run ids are uuid7 when the `langsmith` package is installed, else uuid4.

### SigNoz (`signoz`)
- Fresh volume: OpAMP pushes a `nop` pipeline to the collector until an admin exists, so every healthcheck is green and every OTLP POST gets a connection reset (#239) → the README's headless `POST /api/v1/register` recipe; check `GET /api/v1/version` → `setupCompleted`.
- Cloud key travels as `signoz-ingestion-key`, not `Authorization` → the resolver sets it on the trace, metric and log exporters; self-hosted needs none.
- Recommends delta; exponential histograms delta-only and self-hosted-only → `_TEMPORALITY_PRESETS["signoz"] = "delta"` (#233); SigNoz Cloud does not accept exponential histograms by default.
- The query API sits on the UI port and **requires auth even on localhost** → `query_port: 3301` plus `api_key_env`; the adapter sends the key as both `SIGNOZ-API-KEY` and `Authorization: Bearer`, because the community build has no PAT endpoint (`/api/v1/pats` answers the SPA page) and the only credential is the 30-minute session JWT from `POST /api/v2/sessions/email_password` (#297, #302).
- Trace detail `POST /api/v1/traces/{id}` answers the SPA page on v0.119 → `GET` first, POST only after a 4xx (#297). Histogram parts are **dotted** (`hermes.tool.duration.sum` / `.count`) → the pair is folded into one histogram.
- Its `increase` over-counts one-shot turns that share a step, and steps are stamped at an epoch-aligned start → the adapter reads `timeAggregation=latest` grouped by `service.instance.id`, opens the window on the step grid and computes increases itself (#297).
- No column for the OTLP `event_name` field → events filter on the `event.name` attribute (#268). A list panel returns only the columns asked for, and numeric attributes come back empty unless requested as `float64` (#297).
- The autocomplete catalogue is one bounded page → one search per metric namespace.
- Ports remapped (4318 → 4328, 4317 → 4327, 8080 → 3301) so it coexists with LGTM and Jaeger.

### Jaeger (`jaeger`, v1 and v2)
- Traces only → `_TRACES_ONLY`; a forced `metrics: true` is expected to 404.
- Not LLM-aware → tokens and messages are plain tags; fine for a span-tree look.
- **v2 serves only `/api/v3`** (OTLP-JSON) and 404s the classic routes; Jaeger 1.x also exposes an `/api/v3` gateway with snake_case parameters → the adapter probes classic first, then v3; `query_api: v1|v3` pins (#245, PR #338). v3 silently ignores `attributes[k]=v`, so the tag map goes JSON-encoded in `query.attributes`; an empty search is a 404 "No traces found".
- `tags=` and `operation=` match any span of a trace → with roots-only the adapter re-checks the root itself (#295); the root is the span with no `CHILD_OF` reference.
- No default endpoint (#93).
- Every search must name a service → `service_name`, else `resource_attributes.service.name`, else `hermes-agent`.
- The query port is the UI port, not the OTLP port → set `query_port` (or `ui_url`) when the entry's endpoint is `:4318`.
- Host port 4318 collides with LGTM.

### Grafana Tempo (`tempo`)
- Traces only, and the type refuses metrics and logs even behind a collector that would take them → `_TRACES_ONLY`; set `metrics: true` / `logs: true` explicitly, or use `lgtm`.
- **Attribute values truncated at 2048 bytes** by default, which cut `gen_ai.input.messages` → `tempo.yaml` raises `distributor.max_attribute_bytes` to 131072.
- Search lags 20-30 s behind ingest.
- Search returns the first `limit` matches in block order, not the newest (verified on 3.0.3: `limit=3` over four traces skipped a newer one about half the time) → the adapter over-fetches `search_fetch` (default 500) and sorts.
- `minDuration` is ignored next to `q` (verified otel-lgtm 0.34) → `duration >= Nms` goes into the TraceQL, or client-side with a raw query.
- `grafana/otel-lgtm:latest` on 2026-09-20 listened on the collector's exporter ports (4417 / 4418 / 9099) instead of the mapped ones, and Tempo took 541 s to start → the image is pinned to 0.34.0 (#194).
- Span sets have no `name` field and are capped at three spans → roots via `nestedSetParent < 0` with `spss=1`; free text as a second span set ANDed at trace level (#296).
- Trace ids lose leading zeros → re-padded to 32 hex digits to match Loki and the live store.
- Older builds reject `| select(...)` with a 4xx → retried once without it.
- No UI of its own → `ui_url` pointing at Grafana gives an Explore link (`grafana_datasource_uid`, default `tempo`).

### Grafana LGTM (`lgtm`)
- Functionally the generic `otlp` type → kept as a distinct type so the config says what it is and the banner reads `LGTM`.
- Prometheus wants cumulative; delta is dropped unless `otlp-deltatocumulative` is on → SDK default; native histograms need Prometheus 3.8+ / Mimir ingestion flag.
- Two processes with byte-identical Resources interleave into one Prometheus series (a sawtooth; `rate()` reported calls on an idle model, #79) → `service.instance.id`, `service.version` and `process.pid` on every Resource (PR #115).
- `increase()` never counts a series' first sample, so a one-shot run reads 0; `query_range` can miss a sample between evaluation instants → the adapter reads raw range-vector samples with a 300 s look-behind and counts a first sample when the series is new (#296).
- Names are mangled (`hermes.token.usage` → `hermes_token_usage_total`, units appended, `{token}` units dropped) → `otlp_metric_name` reverses it for display (#284).
- Loki stores resource and record attributes as labels or structured metadata depending on `otlp_config`, underscored → every filter is a pipeline stage; windows clamped to 30 days (`max_query_length`).
- A `tempo` entry and an `lgtm` entry feeding the same Tempo return duplicate span ids until compaction → de-duplicated in the detail.
- The bundled collector's `spanmetrics` connector uses the plugin's attribute names (`llm.provider`, `llm.model_name`, `openinference.span.kind`, `hermes.session.kind`, `hermes.tool.outcome`) → RED metrics without Python emitting them.
- Keeps 3000 and 4318, so it cannot run beside Langfuse / Latitude or Jaeger; `all.sh` skips it.

### Uptrace (`uptrace`)
- Auth is a **DSN** in the `uptrace-dsn` header, not a key → `dsn` / `dsn_env` / `UPTRACE_DSN`; the resolver never parses it. The exporter still needs `endpoint` next to `dsn` (#298).
- Prefers delta and converts cumulative → preset `delta` (#233); exponential histograms recommended.
- The read side needs a **user** token, not the project token → `user_token_env` (or `UPTRACE_USER_TOKEN`).
- A 401 on the dialect probe used to cache "2.1" for the process lifetime → auth errors cache nothing (#298, PR #309).
- **Two API dialects**: 2.1 (`/spans/{p}`, ms times, `::str` key suffixes) and 2.0 (`/tracing/{p}/...`, bare keys); 2.0 answers the probe with the SPA's HTML → probed once per process, HTML counts as 2.0 (#243, #298).
- Uptrace writes its own spans (service `serve`) and log lines into the same project → trace and log queries are pinned to the agent's service name; `service_name: off` removes the pin.
- A counter's `$m` is forward-filled at each interval end and `delta($m)` loses the first interval; histogram `count($m)` is the number of samples, not observations → the adapter computes increases itself per `service_instance_id` and derives the count as `sum($m)/avg($m)` (#298).
- `_span_id` is an "unsupported attr" for per-span logs → `_parent_id` is used; the end bound is floored to the second.
- Trimmed from upstream's 12-service compose to 4 containers (20 s instead of 60 s). Host ports 5432, 8123, 9000 collide with other Postgres / ClickHouse stacks.

### OpenObserve (`openobserve`)
- Org in the URL path (`/api/<org>/v1/traces`) and a `stream-name` header → the resolver builds Basic auth and the header; a wrong path 404s silently, a bad stream name is a 400.
- Attribute names are **flattened with underscores** on storage, irreversibly; the first adapter reversed that with `replace("_", ".")` and invented `llm.model.name` → `_attrs.KNOWN_ATTRIBUTES`, a table of the documented names, drift-tested against `span-attributes.md` (#158, PR #165).
- The parent-span column is named differently across builds (`reference_parent_span_id`, `reference` as a JSON string, `parent_span_id`) → tried in order; only a 4xx moves to the next (#299).
- A query naming a column the stream has never seen is a 400 (`event_name` before the first event) → the adapter answers empty (#268, #299); free text ORs only over columns the schema has.
- Each OTLP metric is its own stream; `_total` dropped; histograms split into `_sum` / `_count` / `_bucket` streams; the stream listing has no time bound → `count_scope: stream`.
- Numbers sometimes come back as digit strings → converted.
- A trace detail needs a window → `trace_window_days` (default 90).
- One container, no port collisions: the safe default.

### Parseable (`parseable` for Cloud / Enterprise, `otlp` + collector for OSS)
- **OSS refuses protobuf** (`400 Protobuf ingestion is not supported in Parseable OSS`) and has **no API keys** (any `X-API-Key` is a 401) → the folder runs an OTel Collector in front that re-encodes to OTLP/JSON and adds Basic auth; the plugin uses `type: otlp` against the collector (#238). Never `type: parseable` against OSS.
- Cloud / Enterprise want `X-API-Key` plus one dataset per signal (`X-P-Stream`, `X-P-Log-Source`) → `type: parseable` carries three header sets, one per exporter (#57, PR #58); the ingestor URL, not the query URL. Env-var mode once sent the traces stream header to the metrics endpoint, so metrics landed in the wrong dataset (#90, PR #110).
- Tag `hermes-traces` with `agent-observability` to get the Agents view.

### Honeycomb (`honeycomb`)
- Key in `x-honeycomb-team` (#20, PR #21); keys are region-specific → `region: us|eu` picks the host; a US key on EU is a 401.
- `x-honeycomb-dataset` is honoured only by Classic keys and ignored by Environments keys → `dataset` sent on all three exporters (correct for Classic, harmless no-op for modern); leave it unset on a modern key.
- Query Data API is Enterprise-only, asynchronous and 7-day limited → no adapter.
- Docs once showed `x-honeycomb-team: ${HONEYCOMB_API_KEY}` with no implementation, so users sent the literal string and got 401 → `${VAR}` expansion in header values (#92, PR #112). A global `headers:` block used to override the resolver's auth → per-backend headers win (#105, PR #145).

### W&B Weave (`weave`)
- Had no plugin-namespaced key, so `WANDB_API_KEY` + entity + project alone used to switch export on → `OTEL_WEAVE_API_KEY` / `_ENDPOINT` / `_BASE_URL` opt in (PR #255).
- Routes by **resource attributes** `wandb.entity` / `wandb.project` → copied from the entry (or `resource_attributes`); both must be present for env-var mode to select it; one Weave project per process.
- Dedicated Cloud uses a different path prefix (`/traces/otel/v1/traces`) than SaaS (`/otel/v1/traces`) → `_weave_endpoint_from_base`.
- W&B documents the endpoint for traces → `_TRACES_ONLY`.
- Expects `wandb.thread_id` and `wandb.is_turn=true` on the root → set by the plugin.

### Elastic (`elastic`)
- `Authorization: ApiKey`, not Bearer; a self-hosted EDOT needs none → header omitted when no key; a key alone never opts in because there is no default host.
- Routes by `data_stream.dataset` / `data_stream.namespace` resource attributes with a strict character set and an appended `.otel` → validated (`[a-z0-9_.]`, no `-`); invalid value skips the entry; conflicting entries fail at init.
- ES does not handle cumulative histograms and converts cumulative counters lossily → preset `delta`.
- **Silent drop**: the exporter reports SUCCESS while the collector's bulk indexer fails on a red cluster; a Docker VM above 90 % full leaves every shard unassigned → the compose disables the disk watermark, `otel.yaml` adds a 90 s timeout, retries and a queue; wait for `yellow`/`green` before the first export.
- `hermes.tool.duration` is mapped as `histogram` through EDOT but as `exponential_histogram` on Elastic Cloud managed OTLP, so no Lens median works on both → the Kibana panel is ES|QL over the tool spans' `duration` (#333, PR #345).
- No adapter; Kibana is the UI (`dashboards.ndjson` ships a five-panel dashboard). Elastic Cloud managed OTLP was verified by the contributor (#333), not re-run by the maintainer; both the `.ingest.` and the classic `.apm.` hosts accept the data (#318, PRs #319, #321).

### OpenLIT (`openlit`)
- The 2.1.0 image does not listen on 4318 unless the collector config is mounted → `otel-collector-config.yaml` vendored (upstream PR 1588 will replace it); the UI port does **not** proxy `/v1/*`.
- Optional Bearer key scopes to org / project / environment → omitted when none; data lands in the `INIT_DB_*` defaults.
- Stores cumulative as-is, including exponential histograms → no preset.
- Harmless "Unknown table otel_traces" errors until the first span.

### MLflow (`mlflow`)
- **Mandatory** `x-mlflow-experiment-id` (400 without it) → default `"0"`; `experiment_id` / `OTEL_MLFLOW_EXPERIMENT_ID` / `MLFLOW_EXPERIMENT_ID`.
- Needs MLflow ≥ 3.6 and a SQL tracking store; the file store refuses OTLP → the compose uses SQLite.
- 404 on `/v1/metrics` and `/v1/logs` → `_TRACES_ONLY`.
- Computes trace token totals and cost itself (`mlflow.trace.tokenUsage`, `mlflow.trace.cost`) from the span attributes; reads the root → keeps the roll-up.
- macOS AirPlay owns port 5000 → host port 5001.

### Comet Opik (`opik`)
- Vendor prefix `/api/v1/private/otel` → `_opik_traces_url` completes the UI base, the SDK's `…/api` override, the prefix, or the full URL; cloud default host.
- Cloud auth is the **bare key** in `Authorization` (no Bearer) plus `Comet-Workspace`; self-hosted needs nothing → every header optional and omitted when unresolved.
- `/metrics` and `/logs` under the prefix 404 → `_TRACES_ONLY`.
- **Sums `gen_ai.usage.*` over every span**, so the root's roll-up doubled trace tokens and the estimated cost (28 560 shown for 14 280 real, #327) → `root_usage: false` preset; after it, the trace total equals the `api.*` spans (PR #353, open on 2026-10-10). Opik maps `agent` → `general`, `api.*` / `llm.*` → `llm`; HTTP only, gRPC errors.
- First start takes one to three minutes while ClickHouse and MySQL migrate.

### Laminar (`laminar`)
- Ingest is on the **app-server** (:8100 here, upstream 8000), not the frontend → endpoint is the app-server base; cloud default `api.lmnr.ai`.
- A project key is required on both editions → resolver skips the entry with a clear message instead of a stream of 401s; `mint-api-key.sh` scripts the key.
- **`/v1/metrics` answers 200 and stores nothing** → `_NO_METRICS`: metrics off by default, traces and logs on (verified: log records stored).
- The published frontend skips creating its Quickwit `spans_v2` index → a `quickwit-init` job creates it, or span search stays empty forever.

### LangWatch (`langwatch`)
- Vendor prefix `/api/otel` for all three signals → `_langwatch_traces_url` completes the base or the prefix; derived metrics and logs URLs.
- Project key required; a service key additionally needs `X-Project-Id` → `project` / `LANGWATCH_PROJECT_ID`.
- **Sums `gen_ai.usage.*` over every span** (48 598 shown for a turn whose calls total 24 299, #327) → `root_usage: false` preset (PR #353, open on 2026-10-10); not re-run after the fix. Common wrong paths (`/v1/traces`, `/api/v1/traces`) are rewritten server-side; 10 MiB per request.
- About five minutes of ClickHouse migrations before `/api/health` answers 204; the key exists only after the onboarding wizard → `mint-api-key.py` or the Postgres read.

### Latitude (`latitude`)
- Ingest is a separate **ingest service** (:3002), not the web or API ports → endpoint is its base; cloud default `ingest.latitude.so`.
- Both the Bearer key **and** `X-Latitude-Project: <slug>` are mandatory; spans without a project are rejected → both required by the resolver; a missing one skips the entry.
- Only `/v1/traces` and health routes exist → `_TRACES_ONLY`. Ingest answers 202; 32 MiB per request by default. Per-span column values were not inspected (the stack was torn down to reclaim disk, PR #331).
- Latitude ships its own Hermes plugin → running both double-traces every turn.
- 13 containers, about 18 GB of images; the key is encrypted at rest and cannot be read back from Postgres.

### telemetry.dev (generic `otlp`)
- Single `Authorization: Bearer` header, one key per project and environment → `headers` with `${VAR}`.
- Reads `gen_ai.*` and ignores OpenInference; sessions from `gen_ai.conversation.id` on the root → the plugin sets it.

### Langtrace (generic `otlp`, evaluation only)
- Custom path `/api/trace`, `x-api-key` → `type: otlp` with the full URL and `headers`; URL derivation does not apply, so `metrics: false`, `logs: false`.
- **Drops bool and double attributes**, reads only `resourceSpans[0]` of a batch → documented as "partial"; not promoted to a type (#224). AGPL-3.0, stale (last release 2025-04), 4.45 GB image.
- Upstream ClickHouse healthcheck on `localhost` resolves to `::1` and never passes → `127.0.0.1` in the compose.

### Sigiro and Maple Local (generic `otlp`)
- Standard paths, no auth, all three signals, SQL query endpoints (`POST :9999/v1/query`; `POST :4388/local/query`) → generic `otlp`; no adapter uses the SQL.
- Sigiro is a closed-source binary; Maple is FSL-1.1 source-available and needs `libchdb.so` beside the binary.

## Part 3: what the plugin normalizes

The short version, for the overview page and the video:

1. **One URL field.** Give the base URL; the type knows the vendor prefix
   (`/api/public/otel`, `/api/v1/private/otel`, `/api/otel`, `/api/<org>`,
   `/otel`) and derives the metrics and logs URLs.
2. **One key field.** `api_key` / `api_key_env` becomes Basic, Bearer,
   `ApiKey`, a bare key, a DSN, `x-honeycomb-team`, `wandb-api-key`,
   `signoz-ingestion-key` or `X-API-Key`, whichever the backend reads, and the
   global `headers:` can never clobber it.
3. **Routing without reading vendor docs.** `project`, `workspace`,
   `experiment_id`, `dataset`, `namespace`, `stream_name`, `entity` land in
   the right header or resource attribute per type; mandatory ones are
   checked at startup and a missing one skips the entry with one message.
4. **Only the signals the backend stores.** Traces-only types get no metrics
   or logs exporter; Laminar's 200-and-drop gets metrics off; everything is
   overridable per entry. No 4xx spray in the debug log, no empty dashboards
   you cannot explain.
5. **The temporality the backend wants.** Delta for SigNoz, Uptrace and
   Elastic; cumulative for the Prometheus family; per entry or global
   override.
6. **Token totals that are right in every UI.** Both attribute conventions on
   every span; the root roll-up withheld from the two backends that sum every
   span.
7. **Isolation.** One `BatchSpanProcessor`, one metric reader and one log
   processor per backend, each on its own queue, so a slow or dead backend
   never blocks the agent or the other backends.
8. **Headers that cannot misfire.** `${VAR}` expands in header values (#92); the per-backend headers win over the global block (#105); a vendor credential alone never switches export on (#259).
9. **Reading it back the same way.** Eight read APIs (GraphQL, TraceQL +
   PromQL + LogQL, SQL, a query builder, two REST dialects, two Jaeger
   generations, two Langfuse generations) behind one search bar, one trace
   detail shape and one metric panel, with the one-shot-process counter
   problem solved per backend.

## Open as of 2026-10-10

- `root_usage` (#327) is PR #353, not yet on `main`; LangWatch was not re-run after it.
- Elastic Cloud managed OTLP: contributor-verified only (#333).
- Latitude per-span columns never inspected (PR #331); Uptrace 2.0 dialect not re-run since 2.0.2 (PR #277).
- Logfire, Datadog, New Relic, Azure Monitor need accounts (#225-#228); AgentOps needs a token-exchange design (#231).

## Keeping this file honest

When a backend changes, re-run one turn through it (`README.md`, "The
loop"), read the debug log, query the store with the folder's Verify
command, and update the row here with the date and version. If a statement
here disagrees with `backends.py` or an adapter, the code is right and this
file is wrong: fix it in the same PR.
