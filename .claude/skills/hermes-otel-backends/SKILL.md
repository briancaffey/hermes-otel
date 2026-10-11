---
name: hermes-otel-backends
description: >-
  Spin up, query, and tear down a local OTLP backend (Phoenix, Grafana LGTM,
  OpenObserve, SigNoz, Jaeger, Tempo, Uptrace, Elastic, and the otlp-typed
  candidates) for hermes-otel development and demos. Use when you need to SEE traces/metrics/logs in a real UI, stand up a
  backend for before/after validation, or debug "why don't I see my telemetry".
  Covers the one-folder-per-backend layout, the port-conflict map, which
  backend supports which signal, the UI URLs + logins, and the query gotchas
  that bite everyone (Prometheus histogram _sum/_count, the 5-minute staleness
  window, OpenObserve PromQL, Phoenix GraphQL for spans).
---

# Running a hermes-otel backend locally

hermes-otel fans telemetry out to any OTLP/HTTP backend. For development you
usually want ONE running locally so you can look at what the plugin emits. This
skill removes every sharp edge between "I made a change" and "I see it in a UI".

One folder per backend under `docker-compose/`: `<name>/docker-compose.yaml`
plus a `README.md` with the login, the `backends:` snippet, a verify query and
the caveats. `docker-compose/README.md` is the manual (comparison table, port
map, disk/memory budget, the test loop).

## 1. Pick a backend

| Backend | Signals | UI | Best for |
|---|---|---|---|
| **OpenObserve** | traces + metrics + logs | http://localhost:5080 | one container, no conflicts — the safe default |
| **Grafana LGTM** | traces + metrics + logs | http://localhost:3000 | the nicest graphs (Tempo + Mimir + Loki in one image) |
| **Phoenix** | traces only | http://localhost:6006 | LLM-span inspection, OpenInference panels |
| **SigNoz** | traces + metrics + logs | http://localhost:3301 | full APM, but a heavy multi-container stack |
| **Jaeger / Tempo** | traces only | :16686 / :3020 (Grafana) | trace-only quick looks; `jaeger-v2/` for the v2 API (#245) |
| **Agenta** | traces | http://localhost:3060 (account from `agenta/mint-api-key.sh`: hermes@example.com / HermesOtel-local-1) | `type: agenta` + `api_key_env` (ApiKey header); 11 containers ~2 GB; async ingest; adapter reads `/api/spans/query` |
| **otel-desktop-viewer / otel-tui** | traces + metrics + logs | http://localhost:3070 (no login) / `docker attach hermes-otel-otel-tui` | generic `type: otlp` at `:4428` / `:4438`; the lightest viewers (~105 MB / ~30 MB resident); otel-tui is a `docker run -d -i -t` line in the viewer's README |
| **Gigapipe (qryn)** | traces + metrics + logs | http://localhost:3110 (qryn-view, no login) | `type: gigapipe`; one port for OTLP and the Tempo/Prometheus/Loki APIs; the LGTM adapter reads it; cumulative metrics only |
| **Aspire Dashboard** | traces + metrics + logs | http://localhost:18888 (no login) | `type: aspire`; read API `/api/telemetry/*` (OTLP JSON, no time filters) for traces + logs; metrics in the UI only; exp. histograms rejected |
| **VictoriaMetrics family** | traces + metrics + logs | vmui on :10428 /select/vmui, :9428 /select/vmui/, :8428 /vmui (no login) | `type: victoria` with `endpoint` + `metrics_endpoint` + `logs_endpoint`; Jaeger API `/select/jaeger`, Prometheus API, LogsQL `/select/logsql/query`; adapter reads all three; 30 s latency offset |
| **ClickStack** | traces + metrics + logs | http://localhost:3050 (account from `clickstack/mint-api-key.sh`: hermes@example.com / HermesOtel-local-1) | `type: clickstack`; key = `mint-api-key.sh` output (bare `authorization` header); ClickHouse at `:8124` with `api`/`api`; adapter reads all three |
| **GreptimeDB** | traces + metrics + logs | http://localhost:4398/dashboard (no login) | `type: greptime`; one container, SQL at `POST :4398/v1/sql?db=public` (`sql=` form field), PromQL under `/v1/prometheus`, Jaeger API under `/v1/jaeger`; adapter reads all three |
| **Zipkin** | traces only | http://localhost:9411/zipkin (no login) | `type: zipkin`, the `zipkin-otel` image (stock Zipkin has no OTLP); one port for ingest, API v2 and UI; adapter reads it |
| **OpenLIT, LangWatch, Sigiro, Maple, Parseable OSS** | traces + metrics + logs | :3010 / :5560 / SQL :9999 / :4388 / :8010 | the newer stacks; all via `type: otlp` |
| **OpenLIT** | traces + metrics + logs | http://localhost:3010 (user@openlit.io / openlituser) | `type: openlit`, OTel-native LLM UI, light |
| **MLflow** | traces | http://localhost:5001 (no login) | `type: mlflow`, experiment header, MLflow's own cost roll-up |
| **Comet Opik** | traces | http://localhost:5173 (no login) | `type: opik`, threads = sessions, per-span type/model/cost |
| **Laminar** | traces + logs | http://localhost:5667 (any email) | `type: laminar`, key from `laminar/mint-api-key.sh`, metrics dropped upstream |
| **LangWatch** | traces + metrics + logs | http://localhost:5560 (sign up) | `type: langwatch`, key from `langwatch/mint-api-key.py` (Playwright), ~5 min first start |
| **Latitude** | traces | http://localhost:3000 (magic link via Mailpit :8025) | `type: latitude`, key + project slug, 13 containers / 15 GB of images |
| **Langtrace** | traces (partial) | :3040 | generic `otlp` with caveats (#224) |
| **Elastic** | traces + metrics + logs | http://127.0.0.1:15602 (Kibana) | `type: elastic` against a local EDOT Collector; data streams, 5.8 GB of images |

Every stack, its ports, the `backends:` snippet and what it actually stored
from a Hermes turn: `docker-compose/README.md` (port map included). The
header comment of each compose file repeats the usage and the snippet.

For metrics work use **OpenObserve** or **LGTM** (Phoenix rejects `/v1/metrics`
with 405). For a first look at LLM spans, **Phoenix** is the friendliest.

## 2. Bring it up

> Since October 2026 every backend lives in its own folder
> (`docker-compose/<name>/docker-compose.yaml`), so Compose names the project
> after the folder and **no `-p` flag is needed**. (The old flat layout put every
> file in one directory, which made them all share the project name
> `docker-compose` and silently no-op; if you see `-p` in old notes, drop it.)

```bash
# OpenObserve (clean, single container)
docker compose -f docker-compose/openobserve/docker-compose.yaml up -d

# Grafana LGTM (single container, all three signals)
docker compose -f docker-compose/lgtm/docker-compose.yaml up -d   # wait ~30s

# Phoenix (traces only)
docker compose -f docker-compose/phoenix/docker-compose.yaml up -d
```

> ⚠️ **Port-conflict map.** Check these are free first (`lsof -i :PORT`):
> 3000 Grafana · 4317/4318 OTLP gRPC/HTTP · 9090 Prometheus · 3100 Loki ·
> 3200 Tempo · 5080 OpenObserve · 6006 Phoenix. The newer stacks use
> `43x8` for OTLP/HTTP and `30x0` for UIs so they never collide with these
> (Elastic: 14319 collector, 19201 Elasticsearch, 15602 Kibana); the full
> map is in `docker-compose/README.md`.
> LGTM wants 4318 **and** 3100 — 3100 commonly collides with other dev
> frontends. If so, copy `lgtm/docker-compose.yaml`, remap the host side (`3110:3100`), make
> the volume path absolute, and bring it up from the copy. 4318 is also claimed
> by Jaeger/SigNoz — run only one OTLP-HTTP backend at a time.

Health: `docker inspect --format '{{.State.Health.Status}}' hermes-otel-<backend>`.
OpenObserve may report `unhealthy` because its image lacks `wget` for the probe
— check `curl -s -o /dev/null -w '%{http_code}' http://localhost:5080/healthz`
returns 200 instead.

## 3. Point the plugin at it

In the plugin's `config.yaml` (gitignored — copy from `config.yaml.example`):

```yaml
project_name: hermes-dev
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user: root@example.com
    password: Complexpass#123
    metrics: true
  # - type: lgtm    {endpoint: http://localhost:4318/v1/traces, metrics: true}
  # - type: phoenix {endpoint: http://localhost:6006/v1/traces}   # traces only
```

On the next Hermes run the startup banner confirms it:
`✓ Multi-backend fan-out active (N collectors, M with metrics)`.

## 4. UI logins

| Backend | URL | Login |
|---|---|---|
| OpenObserve | http://localhost:5080 | `root@example.com` / `Complexpass#123` |
| Grafana (LGTM) | http://localhost:3000 | `admin` / `admin` (Skip the password change) |
| Prometheus (LGTM) | http://localhost:9090/query | none |
| Phoenix | http://localhost:6006 | none |

## 5. Query it (the gotchas that waste everyone's afternoon)

**Metrics are histograms → query a suffix, never the bare name.** The bare
`gen_ai_client_token_usage` has no series and returns "No Data". Use:
- `..._sum` (total), `..._count` (observations), `..._bucket` (distribution).

**Prometheus instant queries go stale after ~5 minutes.** If the producing
process exited, a query "at now" returns nothing even though the data is in the
TSDB — widen the time range (last 1–3h) or re-emit. (See `hermes-otel-validate`
for keeping data fresh.)

**OTel → Prometheus name mangling:** dots become underscores and the unit is
appended. `gen_ai.client.operation.duration` (seconds) →
`gen_ai_client_operation_duration_seconds_sum`.

Quick CLI checks:
```bash
# Prometheus / LGTM
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode 'query=gen_ai_client_token_usage_sum'

# OpenObserve (Prometheus-compatible API; same _sum rule)
curl -s -u 'root@example.com:Complexpass#123' \
  'http://localhost:5080/api/default/prometheus/api/v1/query' \
  --data-urlencode 'query=gen_ai_client_token_usage_sum'

# Phoenix spans — GraphQL, not PromQL
curl -s http://localhost:6006/graphql -H 'Content-Type: application/json' \
  -d '{"query":"{ projects(first:50){ edges { node { name spans(first:50){ edges { node { name spanKind attributes } } } } } } }"}'
```

In the **Grafana** UI: ☰ → Explore → datasource **Prometheus** → toggle the
query editor to **Code** → type `..._sum` → set range to last 30m → Run.
In **OpenObserve**: Metrics → set the PromQL box to `..._sum` → Run query.

## 6. Tear down

```bash
docker compose -f docker-compose/openobserve/docker-compose.yaml down       # keep data
docker compose -f docker-compose/openobserve/docker-compose.yaml down -v     # nuke data
docker rm -f hermes-otel-openobserve hermes-otel-lgtm                        # blunt instrument
```

## Per-backend cheat sheet

| Backend | `-p` needed (never, since the folder layout) | OTLP endpoint | metrics | logs | notes |
|---|---|---|---|---|---|
| openobserve | no | `:5080/api/default/v1/traces` | ✅ | ✅ | needs `user`/`password`; healthcheck false-negative |
| lgtm | no | `:4318/v1/traces` | ✅ | ✅ | Loki 3100 conflicts; ~30s to ready |
| uptrace | no | per `dsn:` | ✅ | ✅ | takes a `dsn:` for the `uptrace-dsn` header |
| phoenix | no | `:6006/v1/traces` | ❌ 405 | ❌ | set `metrics: false`; spans via GraphQL |
| signoz | no | `:4328/v1/traces` | ✅ | ✅ | OTLP remapped to 4328 to dodge 4318 |
| jaeger / tempo | no | `:4318/v1/traces` / `:4358/v1/traces` | ❌ | ❌ | traces only |
| jaeger-v2 | no | `:4368/v1/traces` | ❌ | ❌ | read API is `/api/v3/...` only |
| agenta | no | `:3060/api/otlp/v1/traces` (the type appends it) | ❌ 404 | ❌ 404 | `type: agenta`; protobuf only; key from `mint-api-key.sh`; spans appear a few seconds after export |
| otel-desktop-viewer | no | `:4428/v1/traces` | ✅ | ✅ | `type: otlp`; UI :3070; JSON-RPC `POST /rpc` with named params, times in ns; no `/export`, no SQL in v0.5.0 |
| otel-tui | n/a (`docker run -d -i -t … -p 4438:4318 ymtdzzz/otel-tui`) | `:4438/v1/traces` | ✅ | ✅ | `type: otlp`; TTY only (`docker attach`); memory only; metrics rows repeat per export batch |
| gigapipe | no | `:3110/v1/traces` | ✅ | ✅ | `type: gigapipe`; PromQL range-vector instant queries answer empty (adapter uses query_range); Loki labels only, no logger |
| aspire | no | `:4418/v1/traces` | ✅ | ✅ | `type: aspire`; `query_port: 18888`; volume on /home/app (app user); Resume persistence |
| victoria | no | `:10428` traces, `:8428` metrics, `:9428` logs (the type appends the paths) | ✅ | ✅ | `type: victoria`; Prometheus naming flag set in the compose file; VictoriaLogs stream = service.name |
| clickstack | no | `:4408/v1/traces` | ✅ | ✅ | `type: clickstack` + `api_key_env`; receivers appear only after the first account; ClickHouse `default` user is localhost-only, use `api`/`api` |
| greptime | no | `:4398` (the type appends `/v1/otlp/v1/traces`) | ✅ | ✅ | `type: greptime`; traces need the pipeline header the type sends; metric tables in Prometheus spelling; host port 4398 because 4000 is usually taken |
| zipkin | no | `:9411` (the type appends `/v1/traces`) | ❌ 404 | ❌ annotations only | `type: zipkin`; `ghcr.io/openzipkin-contrib/zipkin-otel`; API v2 on the same port |
| openlit | no | `:4338/v1/traces` | ✅ | ✅ | `type: otlp`; mounted collector config is required with the 2.1.0 image |
| mlflow | no | `:5001/v1/traces` | ❌ 404 | ❌ 404 | `type: otlp` + header `x-mlflow-experiment-id: "0"` |
| opik | no | `:5173/api/v1/private/otel/v1/traces` | ❌ 404 | ❌ 404 | `type: otlp`; first start ~1 min; trace usage double-counts the `agent` roll-up |
| laminar | no | `:8100/v1/traces` | ❌ dropped | ✅ | `type: otlp` + bearer key from `laminar/mint-api-key.sh` |
| langwatch | no | `:5560/api/otel/v1/traces` | ✅ | ✅ | `type: otlp` + bearer key; ~5 min to healthy; key needs the onboarding wizard |
| langtrace | no | `:3040/api/trace` | ❌ | ❌ | `type: otlp` + `x-api-key`; drops bool/double attributes; 4.5 GB image |
| sigiro | no | `:4378/v1/traces` | ✅ | ✅ | `type: otlp`; SQL at `POST :9999/v1/query` |
| maple | no | `:4388/v1/traces` | ✅ | ✅ | `type: otlp`; `up -d --build`; SQL at `POST :4388/local/query {"sql":…}` |
| parseable (OSS) | no | `:4348/v1/traces` (collector) | ✅ | ✅ | `type: otlp`, never `type: parseable` against OSS |
| latitude | no | `:3002/v1/traces` | ❌ | ❌ | 13 containers, ~15 GB of images; untested |
