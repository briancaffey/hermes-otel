# Agenta (OSS)

[Agenta](https://github.com/Agenta-AI/agenta) (MIT outside `ee/`, v0.122.x on
2026-10-11) is an open-source LLMOps platform with native OTLP trace ingest and
server-side adapters for the OpenInference, OpenLLMetry and OTel GenAI
conventions. It is the one OSS LLM platform with OTLP ingest that the #232
sweep did not cover. Traces only, like Opik, MLflow and Latitude. Explicit
plugin type: `agenta` (#371).

## Why pick it
- An LLM platform view (sessions, span types, token roll-ups, cost when the
  model is priced) of Hermes turns, open source.
- Both conventions the plugin emits are understood without mapping.
- A query API the plugin dashboard reads back (traces and filters).

## Start / stop
```bash
docker compose -f docker-compose/agenta/docker-compose.yaml up -d     # healthy in ~1-2 min
export OTEL_AGENTA_API_KEY="$(docker-compose/agenta/mint-api-key.sh)"
docker compose -f docker-compose/agenta/docker-compose.yaml down -v
```
UI: http://localhost:3060. Sign-ups are open; `mint-api-key.sh` registers
`hermes@example.com` / `HermesOtel-local-1` (override with `AGENTA_EMAIL` /
`AGENTA_PASSWORD`), takes the SuperTokens session from the response headers,
mints a key for the default project with `POST /api/keys` and prints it. Run
it again to sign in and mint another key. Eleven containers, about 2 GB
resident (the API alone is ~900 MB).

## Point hermes-otel at it
```yaml
backends:
  - type: agenta
    endpoint: http://localhost:3060            # the type appends /api/otlp/v1/traces
    api_key_env: OTEL_AGENTA_API_KEY           # Authorization: ApiKey <key>
```
The banner reads `✓ Agenta at http://localhost:3060/api/otlp/v1/traces (traces only)`.
Without a key the entry is skipped at startup (every batch would be a 401).

## Verify
```bash
curl -s -X POST http://localhost:3060/api/spans/query -H "Authorization: ApiKey $OTEL_AGENTA_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"formatting":{"focus":"span","format":"agenta"},"windowing":{"limit":20,"order":"descending"}}' | head -c 600
```
Verified 2026-10-11 (api image `latest` = v0.122.x, hermes-otel 1.24.0 branch):
one Hermes turn stored 7 spans (`agent` typed `agent`, `llm.*` and `api.*`
typed `chat`, `tool.*` typed `tool`), each with Agenta's `ag.metrics.tokens`
roll-up; the root's cumulative total equalled the sum of the `api.*` spans,
so the plugin's `agent` roll-up is not double-counted here.

## Caveats
- **Protobuf only**: `POST /api/otlp/v1/traces` refuses OTLP/JSON (400); the
  plugin sends protobuf. Batches over 10 MB are 413.
- **Async ingest**: spans go through a Redis stream (`worker-streams`); they
  show up in the UI and the query API a few seconds after the export, so the
  200 the plugin gets means "accepted", not "stored".
- **Trimmed stack**: runner (sandboxes, needs `SYS_ADMIN` + `/dev/fuse`),
  playground services, cron, the mobile UI, Composio and Traefik are dropped;
  nginx is the front door. The playground and agent-run features of the UI
  will not work here; tracing does.
- **Local secrets**: `AGENTA_AUTH_KEY`, `AGENTA_CRYPT_KEY`, the store keys and
  the Postgres `username` / `password` are laptop defaults (override with the
  env vars in the compose header). PostHog telemetry is switched off.
- **Resource attributes are not stored**; `service.name` does not survive, so
  the plugin dashboard labels traces with the entry's `service_name`.
- The query API has no duration field; the dashboard applies the minimum
  duration to the rows it fetched. The observability page is
  `/w/<workspace>/p/<project>/observability`, ids the plugin cannot know, so
  there is no "open in" link unless `ui_url` names that page.
- Port 3060 (upstream 80). Images: api 740 MB, web 900 MB, plus Postgres 17,
  two Redis 8, SeaweedFS, SuperTokens and nginx (~4 GB in all).
