---
sidebar_position: 2
title: "Config schema"
description: "Complete config.yaml schema, field by field."
---

# Config schema

Complete schema for `~/.hermes/plugins/hermes_otel/config.yaml`. See [`config.yaml`](/configuration/yaml) for the narrative version.

## Top level

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Master kill switch |
| `project_name` | string | *(unset)* | Overrides `OTEL_PROJECT_NAME` |
| `sample_rate` | float \| null | `null` | Parent-based trace ID ratio (0.0–1.0); null = AlwaysOn |
| `preview_max_chars` | int | `1200` | Cap on preview strings before truncation |
| `capture_previews` | bool | `true` | false = suppress all input/output previews |
| `capture_conversation_history` | bool | `false` | Attach full message JSON to llm.* spans |
| `conversation_history_max_chars` | int | `20000` | JSON cap when conversation capture is on |
| `capture_logs` | bool | `false` | Attach OTel LoggingHandler to Python logging; see [OTel logs](/configuration/logs) |
| `log_level` | string | `"INFO"` | Handler level: DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `log_attach_logger` | string \| null | `null` | Logger to attach to; null = root, "hermes_otel" = scope to plugin |
| `root_span_ttl_ms` | int | `600000` | Orphan-sweep TTL in ms |
| `flush_interval_ms` | int | `60000` | Metrics export cadence |
| `force_flush_on_session_end` | bool | `true` | Sync flush every backend at end-of-turn |
| `span_batch_max_queue_size` | int | `2048` | Max buffered spans per backend |
| `span_batch_schedule_delay_ms` | int | `1000` | Worker wake-up cadence |
| `span_batch_max_export_batch_size` | int | `512` | Max spans per OTLP POST |
| `span_batch_export_timeout_ms` | int | `30000` | Per-export HTTP timeout |
| `global_tags` | map | `{}` | Merged into Resource; overridden by `resource_attributes` on key conflict |
| `resource_attributes` | map | `{}` | Merged into Resource |
| `headers` | map | `{}` | Extra HTTP headers on every OTLP request |
| `backends` | list | *(unset)* | Multi-backend fan-out; see below |

When `backends:` is present and non-empty, single-backend env-var detection is skipped.

## `backends[]` entries

Shared fields (all optional unless noted):

| Field | Type | Description |
|---|---|---|
| `type` | string | **Required.** One of: `phoenix`, `langfuse`, `langsmith`, `signoz`, `jaeger`, `tempo`, `otlp`, `lgtm`, `uptrace`, `openobserve` |
| `name` | string | Friendly name shown in logs (default: `type`) |
| `endpoint` | string | Full OTLP endpoint URL (backend-specific defaults — see below) |
| `metrics` | bool | Override metrics-export default for this backend |
| `logs` | bool | Override logs-export default (on for `signoz`, `otlp`, `lgtm`, `uptrace`, `openobserve`; off elsewhere) |
| `headers` | map | Per-backend HTTP headers (merged onto top-level `headers`) |

### Type-specific fields

#### `phoenix`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | Default: `http://localhost:6006/v1/traces` |

#### `langfuse`

| Field | Type | Description |
|---|---|---|
| `public_key` | string | Langfuse public key (inline; discouraged) |
| `public_key_env` | string | Env var name holding the public key |
| `secret_key` | string | Langfuse secret key (inline; discouraged) |
| `secret_key_env` | string | Env var name holding the secret key |
| `base_url` | string | Langfuse base URL (e.g. `https://cloud.langfuse.com`); the plugin appends `/api/public/otel/v1/traces` |
| `endpoint` | string | Override; skips `base_url` construction |

Basic Auth header is constructed automatically from public + secret keys.

#### `signoz`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | OTLP endpoint (self-host: `http://localhost:4328/v1/traces`; cloud: `https://ingest.<region>.signoz.cloud:443/v1/traces`) |
| `ingestion_key` | string | SigNoz Cloud ingestion key (inline; discouraged) |
| `ingestion_key_env` | string | Env var name holding the ingestion key |

When an ingestion key is set, the plugin adds the `signoz-ingestion-key` header.

#### `jaeger`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | OTLP endpoint (default: `http://localhost:4318/v1/traces`). Auto-disables metrics. |

#### `tempo`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | OTLP endpoint. Auto-disables metrics. |

#### `otlp`

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** OTLP traces endpoint |
| `metrics` | bool | Default: `true`; set false for traces-only collectors |
| `logs` | bool | Default: `true`; set false if the collector doesn't accept `/v1/logs` |

Use `headers:` for auth (`${VAR}` interpolation supported).

#### `lgtm`

Alias over `otlp` with a dedicated display name and all signals on by default. See [Grafana LGTM](/backends/lgtm).

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | **Required.** OTLP traces endpoint — `http://localhost:4318/v1/traces` for the bundled `docker-compose/lgtm.yaml` |
| `metrics` | bool | Default: `true` |
| `logs` | bool | Default: `true` |

Use `type: lgtm` (not `type: tempo`) when pointing at the `grafana/otel-lgtm` container — `tempo` is traces-only and would disable the logs/metrics fan-out.

## Env var interpolation in `headers:`

Inside any `headers:` value, `${VAR_NAME}` is replaced with the env var's value at startup:

```yaml
headers:
  Authorization: "Bearer ${OTEL_AUTH_TOKEN}"
  x-honeycomb-team: ${HONEYCOMB_API_KEY}
```

Missing env vars result in a startup warning and the literal `${VAR}` being sent (which will fail auth, but visibly so).

## Precedence

For every field, precedence (highest → lowest) is:

1. `HERMES_OTEL_*` env var (if applicable — see [Env var reference](/reference/env-vars))
2. `config.yaml` value
3. Built-in default

## Validation

On startup the plugin validates the config and:

- Logs a warning and falls back to the default for an invalid value
- Logs a single warning and uses empty config if YAML parsing fails
- Silently skips the YAML file if `pyyaml` isn't installed

The plugin never crashes Hermes because of config — at worst, it disables itself with a clear log line.
