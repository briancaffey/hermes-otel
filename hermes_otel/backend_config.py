"""The ``backends:`` list of config.yaml: one :class:`BackendConfig` per
collector destination, and the loader that turns the yaml list into them.

Resolving an entry into exporter-ready endpoints and headers is
:mod:`hermes_otel.backends`; the scalar coercion rules are
:mod:`hermes_otel.plugin_config`'s, applied here with the top-level field's
rule for the per-backend ``logs:`` overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Tuple

from .backend_specs import SPECS as _SPECS
from .debug_utils import logger
from .plugin_config import FIELD_RULES, _parse_scalar, expand_env_refs


@dataclass(frozen=True)
class BackendConfig:
    """One collector destination declared in ``config.yaml``.

    Secrets (api keys, ingestion keys, langfuse credentials) should normally
    live in env vars rather than yaml — use the ``*_env`` fields to point at
    the env var name. Inline fields are accepted for convenience but are
    discouraged because the file is plaintext.
    """

    type: str  # a type from the manifest (backend_specs.SPECS), e.g. phoenix | langfuse | otlp
    name: Optional[str] = None  # display name (defaults to type)
    endpoint: Optional[str] = None  # HTTP traces URL, or gRPC host:port / origin
    # Where this backend's web UI lives, for the dashboard's Settings tab to
    # link to. Optional: when unset the tab derives it from ``endpoint`` for
    # the types whose UI shares the OTLP origin (Phoenix, Langfuse, ...).
    ui_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None  # extra/override HTTP headers
    # A command whose stdout mints headers (a JSON object or ``Name=value``
    # lines) for every signal's exporter, merged over ``headers`` and the
    # resolver's own; re-run once, and the batch retried once, when the
    # backend answers 401/403 (#483). An argv string split with shlex, no
    # shell. ``None`` timeout = 5000 ms.
    headers_helper: Optional[str] = None
    headers_helper_timeout_ms: Optional[int] = None
    traces: Optional[bool] = None  # None = on. False = dashboard/query-only, no trace export.
    metrics: Optional[bool] = None  # None = the type's preset (BackendSpec.metrics)
    metrics_temporality: Optional[str] = None  # cumulative | delta; None = type preset / top-level
    # Export the turn's token roll-up (``gen_ai.usage.*`` / ``llm.token_count.*``
    # on the ``agent`` / ``cron`` root) to this backend. None = type preset:
    # off for backends that sum usage over every span (opik, langwatch, #327).
    root_usage: Optional[bool] = None
    logs: Optional[bool] = None  # None = the type's preset (BackendSpec.logs)
    # Per-backend log settings (#266): ``logs:`` given as a mapping in yaml
    # turns the signal on and keeps the mapping here. Keys mirror the
    # top-level ``logs:`` block (``level``, ``exclude_loggers``,
    # ``logger_levels``, ``only_in_turn``, ``events: {enabled, content}``)
    # and may only be stricter than the global values.
    log_overrides: Optional[Dict[str, Any]] = None
    # Langfuse credentials
    public_key: Optional[str] = None
    secret_key: Optional[str] = None
    public_key_env: Optional[str] = None
    secret_key_env: Optional[str] = None
    base_url: Optional[str] = None  # langfuse alt to endpoint
    # SigNoz cloud credential
    ingestion_key: Optional[str] = None
    ingestion_key_env: Optional[str] = None
    # Uptrace DSN (sent as the ``uptrace-dsn`` header on every OTLP export)
    dsn: Optional[str] = None
    dsn_env: Optional[str] = None
    # OpenObserve Basic-auth credentials + optional stream name and org (the org
    # completes an endpoint given as a bare origin; default ``default``)
    user: Optional[str] = None
    user_env: Optional[str] = None
    password: Optional[str] = None
    password_env: Optional[str] = None
    stream_name: Optional[str] = None
    org: Optional[str] = None
    # Parseable dataset routing (one dataset per OTLP signal)
    traces_dataset: Optional[str] = None
    metrics_dataset: Optional[str] = None
    logs_dataset: Optional[str] = None
    # Honeycomb: API key (sent as the ``x-honeycomb-team`` header), optional
    # dataset (``x-honeycomb-dataset``), and ``region`` (``us``|``eu``) used to
    # default the endpoint when one isn't given explicitly.
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    dataset: Optional[str] = None
    region: Optional[str] = None
    # Elastic data-stream routing: copied to ``data_stream.dataset`` /
    # ``data_stream.namespace`` Resource attributes.
    namespace: Optional[str] = None
    # MLflow routing: ``experiment_id`` → the mandatory ``x-mlflow-experiment-id``
    # header (default ``"0"``, the built-in Default experiment); ``workspace``
    # → the optional ``X-MLFLOW-WORKSPACE`` header. Opik reuses ``workspace``
    # for ``Comet-Workspace`` and ``project`` for ``projectName``.
    experiment_id: Optional[str] = None
    experiment_id_env: Optional[str] = None
    workspace: Optional[str] = None
    # W&B Weave routing. Weave authenticates with ``api_key`` and routes spans
    # by OTel Resource attributes. These fields are copied to ``wandb.entity``
    # / ``wandb.project``; users can alternatively set them in top-level
    # ``resource_attributes``.
    entity: Optional[str] = None
    entity_env: Optional[str] = None
    project: Optional[str] = None
    project_env: Optional[str] = None
    # greptime: the database the signals land in (X-Greptime-DB-Name; default public)
    db: Optional[str] = None
    db_env: Optional[str] = None
    # Per-signal URLs (any type; victoria: the VictoriaMetrics / VictoriaLogs stores); traces go to `endpoint`
    metrics_endpoint: Optional[str] = None
    logs_endpoint: Optional[str] = None
    # Dashboard-only keys the entry carried (``query_port``, ``prometheus_url``,
    # ...: what the type's adapter reads, per the manifest), kept so the
    # loader never silently drops a query-side setting (#402). Pairs, so the
    # frozen dataclass stays hashable.
    extra: Tuple[Tuple[str, Any], ...] = ()
    protocol: str = "http/protobuf"  # http/protobuf | grpc (generic otlp backend)
    insecure: Optional[bool] = None  # gRPC plaintext; unset = infer from endpoint
    certificate_file: Optional[str] = None  # PEM CA bundle; unset = system trust
    client_key_file: Optional[str] = None  # PEM client private key for mTLS
    client_certificate_file: Optional[str] = None  # PEM client certificate chain for mTLS


_BACKEND_ALLOWED_KEYS = {f.name for f in fields(BackendConfig)} - {"extra"}
# Dashboard-only keys (what the adapters read, from the manifest) plus the
# generic query-side spellings; anything else unknown is warned about once.
_QUERY_KEYS = frozenset(
    {"query_port", "ui_url", "query_api", "query_path", "prometheus_url", "loki_url"}
    | {k for s in _SPECS for k in s.query_keys}
)
_UNKNOWN_KEY_WARNED: set = set()


def coerce_backends(value: Any) -> Optional[Tuple[BackendConfig, ...]]:
    """Coerce a yaml ``backends:`` list into a tuple of BackendConfig."""
    if value is None:
        return None
    if not isinstance(value, list):
        logger.warning(
            f"[hermes-otel] config.yaml 'backends' must be a list, got {type(value).__name__}; ignoring"
        )
        return None

    out: List[BackendConfig] = []
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            logger.warning(f"[hermes-otel] config.yaml backends[{idx}] must be a mapping; skipping")
            continue
        if "type" not in raw or not isinstance(raw["type"], str) or not raw["type"].strip():
            logger.warning(f"[hermes-otel] config.yaml backends[{idx}] missing 'type'; skipping")
            continue
        kwargs: Dict[str, Any] = {}
        extra: List[Tuple[str, Any]] = []
        invalid = False
        for k, v in raw.items():
            if k == "trace":
                # Friendly alias for users who naturally mirror the singular
                # signal name in prose. ``traces`` wins when both are present.
                if "traces" in raw:
                    continue
                k = "traces"
            if k not in _BACKEND_ALLOWED_KEYS:
                if k in _QUERY_KEYS:
                    extra.append((str(k), v))
                elif k not in _UNKNOWN_KEY_WARNED:
                    _UNKNOWN_KEY_WARNED.add(k)
                    logger.warning(
                        f"[hermes-otel] config.yaml backends[{idx}]: unknown key {k!r} ignored"
                    )
                continue
            if k == "headers":
                if isinstance(v, dict):
                    kwargs[k] = {
                        str(kk): expand_env_refs(str(vv), where=f"backends[{idx}].headers")
                        for kk, vv in v.items()
                    }
                continue
            if k == "logs" and isinstance(v, dict):
                # ``logs: {level: WARN, ...}`` = the signal on, with per-backend
                # settings (#266). Validated against the global values when the
                # pipeline is built, where both are known.
                kwargs["logs"] = True
                overrides = _coerce_backend_log_overrides(v, idx)
                if overrides:
                    kwargs["log_overrides"] = overrides
                continue
            if k == "headers_helper_timeout_ms":
                parsed = _parse_scalar("int", v)
                if parsed is None or parsed <= 0:
                    logger.warning(
                        f"[hermes-otel] config.yaml backends[{idx}].{k}: {v!r} is not a positive "
                        "integer; using the default (5000)"
                    )
                else:
                    kwargs[k] = parsed
                continue
            if k in ("traces", "metrics", "logs", "root_usage", "insecure"):
                parsed = _parse_scalar("bool", v) if isinstance(v, (bool, str)) else None
                if parsed is not None:
                    kwargs[k] = parsed
                elif k == "insecure" and v is not None:
                    # A typo must not fall back to inferring plaintext from
                    # the endpoint: the entry is skipped instead.
                    logger.warning(
                        f"[hermes-otel] config.yaml backends[{idx}]: insecure must be a boolean; skipping"
                    )
                    invalid = True
                    break
                continue
            if v is None:
                continue
            kwargs[k] = expand_env_refs(
                str(v) if not isinstance(v, str) else v, where=f"backends[{idx}].{k}"
            )
        if invalid:
            continue
        try:
            out.append(BackendConfig(**kwargs, extra=tuple(extra)))
        except TypeError as e:
            logger.warning(f"[hermes-otel] config.yaml backends[{idx}] invalid: {e}; skipping")

    return tuple(out) if out else None


_BACKEND_LOG_OVERRIDE_KEYS = ("level", "exclude_loggers", "logger_levels", "only_in_turn", "events")


def _coerce_backend_log_overrides(raw: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Validate a per-backend ``logs:`` mapping; unknown keys and bad values warn and are dropped.

    Each key is coerced with the rule of the top-level field it narrows
    (``level`` as ``log_level``, ``events.content`` as ``log_events_content``).
    """
    out: Dict[str, Any] = {}
    where = f"backends[{idx}].logs"
    for k, v in raw.items():
        if k not in _BACKEND_LOG_OVERRIDE_KEYS:
            logger.warning(f"[hermes-otel] config.yaml {where}.{k}: unknown key; ignoring")
            continue
        if k == "level":
            out[k] = None if v is None else _parse_scalar("str", v, **FIELD_RULES["log_level"])
        elif k == "exclude_loggers":
            parsed = _parse_scalar("list", v)
            if parsed is None:
                logger.warning(f"[hermes-otel] config.yaml {where}.{k} must be a list; ignoring")
            else:
                out[k] = parsed
        elif k == "logger_levels":
            if isinstance(v, dict):
                out[k] = {str(kk): str(vv).upper() for kk, vv in v.items()}
            else:
                logger.warning(f"[hermes-otel] config.yaml {where}.{k} must be a mapping; ignoring")
        elif k == "only_in_turn":
            parsed = _parse_scalar("bool", v)
            if parsed is None:
                logger.warning(f"[hermes-otel] config.yaml {where}.{k} must be a bool; ignoring")
            else:
                out[k] = parsed
        elif k == "events":
            if not isinstance(v, dict):
                logger.warning(
                    f"[hermes-otel] config.yaml {where}.events must be a mapping; ignoring"
                )
                continue
            events: Dict[str, Any] = {}
            for ek, ev in v.items():
                if ek == "enabled":
                    parsed = _parse_scalar("bool", ev)
                    if parsed is not None:
                        events["enabled"] = parsed
                elif ek == "content":
                    rule = FIELD_RULES["log_events_content"]
                    parsed = _parse_scalar("str", ev, **rule)
                    if parsed is None:
                        logger.warning(
                            f"[hermes-otel] config.yaml {where}.events.content: {ev!r} is not one of "
                            f"{', '.join(rule['choices'])}; ignoring"
                        )
                    else:
                        events["content"] = parsed
                else:
                    logger.warning(
                        f"[hermes-otel] config.yaml {where}.events.{ek}: unknown key; ignoring"
                    )
            if events:
                out["events"] = events
    return out
