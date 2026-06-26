---
name: hermes-otel-backends
description: >-
  Spin up, query, and tear down a local OTLP backend (Phoenix, Grafana LGTM,
  OpenObserve, SigNoz, Jaeger, Tempo, Uptrace) for hermes-otel development and
  demos. Use when you need to SEE traces/metrics/logs in a real UI, stand up a
  backend for before/after validation, or debug "why don't I see my telemetry".
  Covers the docker-compose project-name trap, the port-conflict map, which
  backend supports which signal, the UI URLs + logins, and the query gotchas
  that bite everyone (Prometheus histogram _sum/_count, the 5-minute staleness
  window, OpenObserve PromQL, Phoenix GraphQL for spans).
---

# Running a hermes-otel backend locally

hermes-otel fans telemetry out to any OTLP/HTTP backend. For development you
usually want ONE running locally so you can look at what the plugin emits. This
skill removes every sharp edge between "I made a change" and "I see it in a UI".

Compose files live in `docker-compose/` at the repo root. READMEs with extra
detail sit under `docker-compose/<backend>/`.

## 1. Pick a backend

| Backend | Signals | UI | Best for |
|---|---|---|---|
| **OpenObserve** | traces + metrics + logs | http://localhost:5080 | one container, no conflicts — the safe default |
| **Grafana LGTM** | traces + metrics + logs | http://localhost:3000 | the nicest graphs (Tempo + Mimir + Loki in one image) |
| **Phoenix** | traces only | http://localhost:6006 | LLM-span inspection, OpenInference panels |
| **SigNoz** | traces + metrics + logs | http://localhost:3301 | full APM, but a heavy multi-container stack |
| **Jaeger / Tempo** | traces only | :16686 / :3200 | trace-only quick looks |

For metrics work use **OpenObserve** or **LGTM** (Phoenix rejects `/v1/metrics`
with 405). For a first look at LLM spans, **Phoenix** is the friendliest.

## 2. Bring it up

> ⚠️ **The project-name trap.** For `lgtm`, `openobserve`, and `uptrace` you
> MUST pass `-p <name>` explicitly. `docker compose -f <file>` alone silently
> no-ops on these — nothing starts and you get no error.

```bash
# OpenObserve (clean, single container)
docker compose -p openobserve -f docker-compose/openobserve.yaml up -d

# Grafana LGTM (single container, all three signals)
docker compose -p lgtm -f docker-compose/lgtm.yaml up -d   # wait ~30s

# Phoenix (traces only)
docker compose -f docker-compose/phoenix.yaml up -d
```

> ⚠️ **Port-conflict map.** Check these are free first (`lsof -i :PORT`):
> 3000 Grafana · 4317/4318 OTLP gRPC/HTTP · 9090 Prometheus · 3100 Loki ·
> 3200 Tempo · 5080 OpenObserve · 6006 Phoenix.
> LGTM wants 4318 **and** 3100 — 3100 commonly collides with other dev
> frontends. If so, copy `lgtm.yaml`, remap the host side (`3110:3100`), make
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
docker compose -p openobserve -f docker-compose/openobserve.yaml down       # keep data
docker compose -p openobserve -f docker-compose/openobserve.yaml down -v     # nuke data
docker rm -f hermes-otel-openobserve hermes-otel-lgtm                        # blunt instrument
```

## Per-backend cheat sheet

| Backend | `-p` needed | OTLP endpoint | metrics | logs | notes |
|---|---|---|---|---|---|
| openobserve | **yes** | `:5080/api/default/v1/traces` | ✅ | ✅ | needs `user`/`password`; healthcheck false-negative |
| lgtm | **yes** | `:4318/v1/traces` | ✅ | ✅ | Loki 3100 conflicts; ~30s to ready |
| uptrace | **yes** | per `dsn:` | ✅ | ✅ | takes a `dsn:` for the `uptrace-dsn` header |
| phoenix | no | `:6006/v1/traces` | ❌ 405 | ❌ | set `metrics: false`; spans via GraphQL |
| signoz | no | `:4328/v1/traces` | ✅ | ✅ | OTLP remapped to 4328 to dodge 4318 |
| jaeger / tempo | no | `:4318/v1/traces` | ❌ | ❌ | traces only |
