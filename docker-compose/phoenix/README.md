# Phoenix

[Arize Phoenix](https://github.com/Arize-ai/phoenix) (Elastic License 2.0) is the
quickest way to look at a Hermes trace: one container, no login, and a UI built
around OpenInference spans (`llm`, `tool`, `agent` kinds), which the plugin emits
alongside the GenAI semconv attributes. Explicit plugin type: `phoenix`.

## Why pick it
- One container, starts in seconds, nothing to configure.
- LLM-centric trace view: prompts/completions, token counts and tool calls are
  rendered as panels, not as raw attribute lists.
- Projects: the plugin's `project_name` becomes a Phoenix project.

## Start / stop
```bash
docker compose -f docker-compose/phoenix/docker-compose.yaml up -d
docker compose -f docker-compose/phoenix/docker-compose.yaml down        # keep data
docker compose -f docker-compose/phoenix/docker-compose.yaml down -v     # delete data
```
UI: http://localhost:6006 (no login). Port 6006 is also the OTLP/HTTP endpoint.

## Point hermes-otel at it
```yaml
backends:
  - type: phoenix
    endpoint: http://localhost:6006/v1/traces
```

## Verify
Open the project in the UI, or query GraphQL:
```bash
curl -s http://localhost:6006/graphql -H 'content-type: application/json' \
  -d '{"query":"{ projects(first:10){ edges { node { name spans(first:5){ edges { node { name spanKind } } } } } } }"}'
```

## Caveats
- Traces only. `/v1/metrics` and `/v1/logs` both answer 405 (#160); the
  `phoenix` type turns both signals off for you.
- `arizephoenix/phoenix:latest` is unpinned; the UI changes often.
- Data lives in the container's SQLite unless you add a volume; `down -v` or
  recreating the container loses it.
