# Grafana Tempo

[Grafana Tempo](https://grafana.com/oss/tempo/) (AGPL-3.0) as a standalone trace
store, with Grafana for the UI. Trimmed from Tempo's upstream
`example/docker-compose/single-binary` to two containers: Tempo 3.1 with
local-disk storage and Grafana 13 with the Tempo datasource provisioned.
Explicit plugin type: `tempo`. For traces + metrics + logs in Grafana use
[`../lgtm`](../lgtm) instead.

## Why pick it
- Exactly what `type: tempo` targets, without the LGTM extras.
- TraceQL search in Grafana Explore; the span tree renders with the full
  attribute set.
- Light: about 260 MB resident.

## Start / stop
```bash
docker compose -f docker-compose/tempo/docker-compose.yaml up -d
docker compose -f docker-compose/tempo/docker-compose.yaml down -v
```
Grafana: http://localhost:3020 (anonymous admin, no login) → Explore → Tempo.
Tempo API: http://localhost:3210.

## Point hermes-otel at it
```yaml
backends:
  - type: tempo
    endpoint: http://localhost:4358/v1/traces
```

## Verify
```bash
curl -s 'http://localhost:3210/api/search?q=%7Bresource.service.name%3D%22hermes-agent%22%7D&limit=5'
curl -s http://localhost:3210/api/v2/traces/<trace id>
```

## Caveats
- Traces only. Tempo's metrics-generator was removed from the config because it
  needs a Prometheus to remote-write to.
- Search catches up **20-30 s** after export; an immediate query returns `[]`.
- Tempo 3 truncates attribute values at **2048 bytes** by default, which cut
  `gen_ai.input.messages` under `content_capture: full` (the log says
  `attributes truncated`). `tempo.yaml` here sets `distributor.max_attribute_bytes:
  131072`; do the same on any Tempo you run yourself.
- Host ports are moved (3020 / 3210 / 4358) so the stack can run next to LGTM,
  which owns 3000 / 3200 / 4318.
