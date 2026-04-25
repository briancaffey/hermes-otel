"""Declarative configuration for hermes-otel.

Loader precedence per field: env var > ~/.hermes/plugins/hermes_otel/config.yaml > default.

Two ways to pick backends:
  * **Multi-backend** (preferred): set ``backends:`` in config.yaml. Every entry
    fans out via its own ``BatchSpanProcessor`` so traces land in all
    configured collectors in parallel without blocking the agent thread.
  * **Single-backend (legacy)**: set one of the ``OTEL_*_ENDPOINT`` env vars
    or LangSmith/Langfuse credentials. When ``backends:`` is empty, env-var
    detection is used and at most one backend is selected (priority is
    LangSmith > Langfuse > SigNoz > Jaeger > Tempo > Phoenix).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .debug_utils import logger

Scalar = Union[str, int, float, bool]

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "plugins" / "hermes_otel" / "config.yaml"

_ENV_PREFIX = "HERMES_OTEL_"
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class BackendConfig:
    """One collector destination declared in ``config.yaml``.

    Secrets (api keys, ingestion keys, langfuse credentials) should normally
    live in env vars rather than yaml — use the ``*_env`` fields to point at
    the env var name. Inline fields are accepted for convenience but are
    discouraged because the file is plaintext.
    """

    type: str  # phoenix | langfuse | signoz | jaeger | tempo | otlp
    name: Optional[str] = None  # display name (defaults to type)
    endpoint: Optional[str] = None  # OTLP HTTP traces URL
    headers: Optional[Dict[str, str]] = None  # extra/override HTTP headers
    metrics: Optional[bool] = None  # None = auto (off for langfuse/jaeger/tempo)
    logs: Optional[bool] = None  # None = auto (on for signoz/otlp/lgtm/uptrace/openobserve)
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
    # OpenObserve Basic-auth credentials + optional stream name
    user: Optional[str] = None
    user_env: Optional[str] = None
    password: Optional[str] = None
    password_env: Optional[str] = None
    stream_name: Optional[str] = None


@dataclass(frozen=True)
class HermesOtelConfig:
    """Frozen configuration object passed through the plugin."""

    enabled: bool = True
    sample_rate: Optional[float] = None  # None = AlwaysOn. 0..1 = ratio.
    root_span_ttl_ms: int = 600_000  # 10 min orphan sweep threshold
    flush_interval_ms: int = 60_000  # metrics export interval
    preview_max_chars: int = 1200  # clip_preview truncation
    capture_previews: bool = True  # global privacy kill switch
    headers: Optional[Dict[str, str]] = None  # extra OTLP headers (all backends)
    global_tags: Optional[Dict[str, Scalar]] = None
    resource_attributes: Optional[Dict[str, Scalar]] = None
    project_name: Optional[str] = None  # supersedes OTEL_PROJECT_NAME
    # ── BatchSpanProcessor tunables (Phase 2: non-blocking export) ──────
    span_batch_max_queue_size: int = 2048  # spans buffered before drops
    span_batch_schedule_delay_ms: int = 1000  # worker wake-up cadence
    span_batch_max_export_batch_size: int = 512  # spans per HTTP POST
    span_batch_export_timeout_ms: int = 30_000  # per-export HTTP timeout
    force_flush_on_session_end: bool = True  # flush so UI sees traces promptly
    # ── LLM span input fidelity ─────────────────────────────────────────
    # Opt-in: serialise the full conversation_history onto the llm span's
    # input.value so the UI shows every message instead of just the last
    # user turn. The api.* spans don't carry message-level detail, so
    # flipping this on is the easiest way to see what the model actually saw.
    capture_conversation_history: bool = False
    conversation_history_max_chars: int = 20_000
    # ── Full-fidelity api.* span capture (opt-in, unredacted) ───────────
    # Writes the *entire* prompt/system prompt and/or response onto each
    # ``api.{model}`` span, bypassing ``preview_max_chars``. Off by default
    # because payloads can be large (multi-MB conversations) and contain
    # sensitive data. Prefer ``capture_conversation_history`` for the
    # summary-level LLM span; these flags target the per-request span.
    capture_full_prompts: bool = False
    capture_full_responses: bool = False
    # Opt-in: platform user identifier from Hermes gateway sessions. Hermes
    # currently exposes this as ``sender_id`` only on pre_llm_call.
    capture_sender_id: bool = False
    # ── OTel logs signal ────────────────────────────────────────────────
    # Opt-in: when enabled, attach an OTel ``LoggingHandler`` to Python's
    # logging so stdlib ``logger.info(...)`` calls ship to any log-capable
    # backend (SigNoz, OTLP → Loki, LGTM). Correlates each log record
    # with the active span's ``trace_id`` / ``span_id`` automatically.
    # Off by default because attaching to the root logger is invasive —
    # third-party libraries' logs are also exported.
    capture_logs: bool = False
    log_level: str = "INFO"  # handler level: DEBUG, INFO, WARNING, ERROR
    # None = attach to the root logger (captures all hermes-agent + plugin
    # logs). Set to e.g. "hermes_otel" to scope capture to plugin logs only.
    log_attach_logger: Optional[str] = None
    # ── Multi-backend fan-out ───────────────────────────────────────────
    backends: Optional[Tuple[BackendConfig, ...]] = None


# ── Env-var parsers ────────────────────────────────────────────────────────


def _parse_bool(value: str) -> Optional[bool]:
    v = value.strip().lower()
    if v in _TRUE_STRINGS:
        return True
    if v in _FALSE_STRINGS:
        return False
    return None


def _parse_float(value: str) -> Optional[float]:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return None


# ── YAML loader ────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load config.yaml if present and pyyaml is available.

    Missing file or missing pyyaml → empty dict (silent).
    Malformed yaml → warn + empty dict (explicit, not silent).
    """
    if not path.exists():
        return {}

    try:
        import yaml  # type: ignore
    except ImportError:
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"[hermes-otel] config.yaml malformed, using defaults: {e}")
        return {}

    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.warning(
            f"[hermes-otel] config.yaml root must be a mapping, got {type(data).__name__}; using defaults"
        )
        return {}
    return data


# ── Loader ─────────────────────────────────────────────────────────────────


_ALLOWED_KEYS = {f.name for f in fields(HermesOtelConfig)}
_BACKEND_ALLOWED_KEYS = {f.name for f in fields(BackendConfig)}


def _coerce_backends(value: Any) -> Optional[Tuple[BackendConfig, ...]]:
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
        for k, v in raw.items():
            if k not in _BACKEND_ALLOWED_KEYS:
                continue
            if k == "headers":
                if isinstance(v, dict):
                    kwargs[k] = {str(kk): str(vv) for kk, vv in v.items()}
                continue
            if k in ("metrics", "logs"):
                if isinstance(v, bool):
                    kwargs[k] = v
                elif isinstance(v, str):
                    parsed = _parse_bool(v)
                    if parsed is not None:
                        kwargs[k] = parsed
                continue
            if v is None:
                continue
            kwargs[k] = str(v) if not isinstance(v, str) else v
        try:
            out.append(BackendConfig(**kwargs))
        except TypeError as e:
            logger.warning(f"[hermes-otel] config.yaml backends[{idx}] invalid: {e}; skipping")

    return tuple(out) if out else None


def _coerce_from_yaml(key: str, value: Any) -> Any:
    """Normalize yaml scalar types into the dataclass field types.

    yaml.safe_load already returns native python types; we only coerce
    obvious cases (e.g., stringified int) and pass-through dicts.
    """
    if value is None:
        return None
    if key == "backends":
        return _coerce_backends(value)
    if key in (
        "enabled",
        "capture_previews",
        "force_flush_on_session_end",
        "capture_conversation_history",
        "capture_logs",
        "capture_full_prompts",
        "capture_full_responses",
        "capture_sender_id",
    ):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            parsed = _parse_bool(value)
            if parsed is not None:
                return parsed
        return bool(value)
    if key == "sample_rate":
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return _parse_float(value)
        return None
    if key in (
        "root_span_ttl_ms",
        "flush_interval_ms",
        "preview_max_chars",
        "span_batch_max_queue_size",
        "span_batch_schedule_delay_ms",
        "span_batch_max_export_batch_size",
        "span_batch_export_timeout_ms",
        "conversation_history_max_chars",
    ):
        if isinstance(value, bool):
            return None  # bools are ints in python; reject explicitly
        if isinstance(value, int):
            return value
        if isinstance(value, (float, str)):
            return _parse_int(str(value))
        return None
    if key in ("headers", "global_tags", "resource_attributes"):
        if isinstance(value, dict):
            return {str(k): v for k, v in value.items()}
        return None
    if key == "project_name":
        return None if value is None else str(value)
    if key == "log_level":
        return None if value is None else str(value).upper()
    if key == "log_attach_logger":
        return None if value is None else str(value)
    return value


def _load_env_overrides() -> Dict[str, Any]:
    """Extract per-field overrides from environment variables."""
    out: Dict[str, Any] = {}

    def take(key: str, parser):
        raw = os.getenv(_ENV_PREFIX + key.upper(), "").strip()
        if not raw:
            return
        parsed = parser(raw)
        if parsed is not None:
            out[key] = parsed

    take("enabled", _parse_bool)
    take("sample_rate", _parse_float)
    take("root_span_ttl_ms", _parse_int)
    take("flush_interval_ms", _parse_int)
    take("preview_max_chars", _parse_int)
    take("capture_previews", _parse_bool)
    take("span_batch_max_queue_size", _parse_int)
    take("span_batch_schedule_delay_ms", _parse_int)
    take("span_batch_max_export_batch_size", _parse_int)
    take("span_batch_export_timeout_ms", _parse_int)
    take("force_flush_on_session_end", _parse_bool)
    take("capture_conversation_history", _parse_bool)
    take("conversation_history_max_chars", _parse_int)
    take("capture_logs", _parse_bool)
    take("capture_full_prompts", _parse_bool)
    take("capture_full_responses", _parse_bool)
    take("capture_sender_id", _parse_bool)

    proj = os.getenv(_ENV_PREFIX + "PROJECT_NAME", "").strip()
    if proj:
        out["project_name"] = proj

    level = os.getenv(_ENV_PREFIX + "LOG_LEVEL", "").strip()
    if level:
        out["log_level"] = level.upper()

    attach = os.getenv(_ENV_PREFIX + "LOG_ATTACH_LOGGER", "").strip()
    if attach:
        out["log_attach_logger"] = attach

    return out


def load_config(
    path: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> HermesOtelConfig:
    """Build a HermesOtelConfig from yaml + env, per-field precedence.

    Args:
        path: Override config.yaml location (tests).
        env:  Reserved for future use; env is read via os.getenv directly so
              existing monkeypatch-based tests keep working.
    """
    yaml_path = path if path is not None else DEFAULT_CONFIG_PATH
    yaml_data = _load_yaml(yaml_path)

    values: Dict[str, Any] = {}
    for key, raw in yaml_data.items():
        if key not in _ALLOWED_KEYS:
            continue
        coerced = _coerce_from_yaml(key, raw)
        if coerced is not None:
            values[key] = coerced

    values.update(_load_env_overrides())

    # Build config with whatever we have; unset fields fall back to dataclass defaults.
    return replace(HermesOtelConfig(), **values)
