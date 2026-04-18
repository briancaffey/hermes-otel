"""Declarative configuration for hermes-otel.

Loader precedence per field: env var > ~/.hermes/plugins/hermes_otel/config.yaml > default.

Backend selection (Phoenix / Langfuse / LangSmith / SigNoz) stays env-var-driven
to preserve existing deployments. This config only controls telemetry shaping:
sampling, previews, resource attributes, TTL, headers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Dict, Optional, Union

Scalar = Union[str, int, float, bool]

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "plugins" / "hermes_otel" / "config.yaml"

_ENV_PREFIX = "HERMES_OTEL_"
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class HermesOtelConfig:
    """Frozen configuration object passed through the plugin."""

    enabled: bool = True
    sample_rate: Optional[float] = None        # None = AlwaysOn. 0..1 = ratio.
    root_span_ttl_ms: int = 600_000            # 10 min orphan sweep threshold
    flush_interval_ms: int = 60_000            # metrics export interval
    preview_max_chars: int = 1200              # clip_preview truncation
    capture_previews: bool = True              # global privacy kill switch
    headers: Optional[Dict[str, str]] = None   # extra OTLP headers
    global_tags: Optional[Dict[str, Scalar]] = None
    resource_attributes: Optional[Dict[str, Scalar]] = None
    project_name: Optional[str] = None         # supersedes OTEL_PROJECT_NAME
    # ── BatchSpanProcessor tunables (Phase 2: non-blocking export) ──────
    span_batch_max_queue_size: int = 2048      # spans buffered before drops
    span_batch_schedule_delay_ms: int = 1000   # worker wake-up cadence
    span_batch_max_export_batch_size: int = 512  # spans per HTTP POST
    span_batch_export_timeout_ms: int = 30_000 # per-export HTTP timeout
    force_flush_on_session_end: bool = True    # flush so UI sees traces promptly


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
        print(f"[hermes-otel] config.yaml malformed, using defaults: {e}")
        return {}

    if data is None:
        return {}
    if not isinstance(data, dict):
        print(f"[hermes-otel] config.yaml root must be a mapping, got {type(data).__name__}; using defaults")
        return {}
    return data


# ── Loader ─────────────────────────────────────────────────────────────────


_ALLOWED_KEYS = {f.name for f in fields(HermesOtelConfig)}


def _coerce_from_yaml(key: str, value: Any) -> Any:
    """Normalize yaml scalar types into the dataclass field types.

    yaml.safe_load already returns native python types; we only coerce
    obvious cases (e.g., stringified int) and pass-through dicts.
    """
    if value is None:
        return None
    if key in ("enabled", "capture_previews", "force_flush_on_session_end"):
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

    proj = os.getenv(_ENV_PREFIX + "PROJECT_NAME", "").strip()
    if proj:
        out["project_name"] = proj

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
