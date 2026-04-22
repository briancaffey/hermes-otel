"""Backend resolution for hermes-otel.

Converts declarative :class:`~hermes_otel.plugin_config.BackendConfig`
objects (from ``config.yaml``) **or** environment variables into
ready-to-wire :class:`_ResolvedBackend` instances. Each
``_ResolvedBackend`` carries exactly what the OTLP pipeline needs:
endpoint URL, ready-to-send headers (with auth already baked in), and
the display name used in startup logs.

The module is intentionally stateless — no per-call cache, no mutation
of the plugin, no OTel SDK imports. Tracer wiring happens in
``tracer.py``; this module decides *what* to wire.

Adding a backend: write a ``_resolve_<name>(bc)`` function below, add
it to ``_RESOLVERS``, and add a display-name entry to ``_DISPLAY_NAMES``.
If you want the env path (single-backend detection when no
``config.yaml`` is present) to pick it up automatically, also add the
type to ``_ENV_PRIORITY``.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .plugin_config import BackendConfig

# Backend types whose collectors do not accept OTLP metrics. Pure traces.
_TRACES_ONLY = {"langfuse", "jaeger", "tempo"}

# Backend types whose collectors accept OTLP logs. Everything else defaults
# to "logs off" — Phoenix/Langfuse/Jaeger/Tempo don't implement /v1/logs, and
# we'd rather drop logs on the floor than spray 4xx errors at them. Users
# can override per-backend via the ``logs:`` field in config.yaml.
_LOGS_CAPABLE = {"signoz", "otlp", "generic", "lgtm"}

# Display names used in logs. Preferred over ``type.capitalize()`` because
# some backends use camelCase ("SigNoz") that simple title-case gets wrong.
_DISPLAY_NAMES = {
    "phoenix": "Phoenix",
    "langfuse": "Langfuse",
    "signoz": "SigNoz",
    "jaeger": "Jaeger",
    "tempo": "Tempo",
    "otlp": "OTLP",
    "generic": "OTLP",
    "lgtm": "LGTM",
}

# Priority for env-var-driven single-backend detection. First backend whose
# required env vars are fully set wins.
_ENV_PRIORITY = ["langfuse", "signoz", "jaeger", "tempo", "phoenix"]


@dataclass
class _ResolvedBackend:
    """A backend ready to wire into the OTLP pipeline.

    ``headers`` may already include backend-specific auth (e.g. Langfuse
    Basic Auth, SigNoz ingestion key); the pipeline merges the global
    ``config.headers`` on top before constructing the exporter.
    """

    type: str
    endpoint: str
    display_name: str = "OTLP"
    headers: Optional[Dict[str, str]] = None
    supports_metrics: bool = True
    supports_logs: bool = False


# ── Shared helpers ─────────────────────────────────────────────────────────


def _metrics_for(backend_type: str, override: Optional[bool]) -> bool:
    if override is not None:
        return override
    return backend_type not in _TRACES_ONLY


def _logs_for(backend_type: str, override: Optional[bool]) -> bool:
    if override is not None:
        return override
    return backend_type in _LOGS_CAPABLE


def _resolve_secret(
    inline: Optional[str],
    env_name: Optional[str],
    fallback_envs: List[str],
) -> Optional[str]:
    """Pick the first available secret value. Inline > named env > fallback envs."""
    if inline:
        v = inline.strip()
        if v:
            return v
    if env_name:
        v = os.getenv(env_name, "").strip()
        if v:
            return v
    for name in fallback_envs:
        v = os.getenv(name, "").strip()
        if v:
            return v
    return None


def _display(bc: BackendConfig, t: str) -> str:
    return bc.name or _DISPLAY_NAMES.get(t, t.capitalize()) or "OTLP"


# ── Per-backend resolvers ──────────────────────────────────────────────────


def _resolve_phoenix(bc: BackendConfig) -> _ResolvedBackend:
    ep = (bc.endpoint or os.getenv("OTEL_PHOENIX_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("phoenix requires endpoint")
    extra = dict(bc.headers or {})
    return _ResolvedBackend(
        type="phoenix",
        endpoint=ep,
        display_name=_display(bc, "phoenix"),
        headers=extra or None,
        supports_metrics=_metrics_for("phoenix", bc.metrics),
        supports_logs=_logs_for("phoenix", bc.logs),
    )


def _resolve_langfuse(bc: BackendConfig) -> _ResolvedBackend:
    pub = _resolve_secret(
        bc.public_key,
        bc.public_key_env,
        ["OTEL_LANGFUSE_PUBLIC_API_KEY", "LANGFUSE_PUBLIC_KEY"],
    )
    sec = _resolve_secret(
        bc.secret_key,
        bc.secret_key_env,
        ["OTEL_LANGFUSE_SECRET_API_KEY", "LANGFUSE_SECRET_KEY"],
    )
    if not (pub and sec):
        raise ValueError("langfuse requires public_key and secret_key")
    ep = (bc.endpoint or os.getenv("OTEL_LANGFUSE_ENDPOINT", "")).strip()
    if not ep:
        base = (bc.base_url or os.getenv("LANGFUSE_BASE_URL", "")).strip().rstrip("/")
        root = base if base else "https://cloud.langfuse.com"
        ep = f"{root}/api/public/otel/v1/traces"
    auth = base64.b64encode(f"{pub}:{sec}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "x-langfuse-ingestion-version": "4",
    }
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="langfuse",
        endpoint=ep,
        display_name=_display(bc, "langfuse"),
        headers=headers,
        supports_metrics=_metrics_for("langfuse", bc.metrics),
        supports_logs=_logs_for("langfuse", bc.logs),
    )


def _resolve_signoz(bc: BackendConfig) -> _ResolvedBackend:
    ep = (bc.endpoint or os.getenv("OTEL_SIGNOZ_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("signoz requires endpoint")
    key = _resolve_secret(
        bc.ingestion_key,
        bc.ingestion_key_env,
        ["OTEL_SIGNOZ_INGESTION_KEY"],
    )
    headers: Dict[str, str] = {}
    if key:
        headers["signoz-ingestion-key"] = key
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="signoz",
        endpoint=ep,
        display_name=_display(bc, "signoz"),
        headers=headers or None,
        supports_metrics=_metrics_for("signoz", bc.metrics),
        supports_logs=_logs_for("signoz", bc.logs),
    )


def _resolve_jaeger(bc: BackendConfig) -> _ResolvedBackend:
    ep = (bc.endpoint or os.getenv("OTEL_JAEGER_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("jaeger requires endpoint")
    extra = dict(bc.headers or {})
    return _ResolvedBackend(
        type="jaeger",
        endpoint=ep,
        display_name=_display(bc, "jaeger"),
        headers=extra or None,
        supports_metrics=_metrics_for("jaeger", bc.metrics),
        supports_logs=_logs_for("jaeger", bc.logs),
    )


def _resolve_tempo(bc: BackendConfig) -> _ResolvedBackend:
    ep = (bc.endpoint or os.getenv("OTEL_TEMPO_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("tempo requires endpoint")
    extra = dict(bc.headers or {})
    return _ResolvedBackend(
        type="tempo",
        endpoint=ep,
        display_name=_display(bc, "tempo"),
        headers=extra or None,
        supports_metrics=_metrics_for("tempo", bc.metrics),
        supports_logs=_logs_for("tempo", bc.logs),
    )


def _resolve_otlp(bc: BackendConfig) -> _ResolvedBackend:
    # No conventional env var for the generic OTLP type — callers provide
    # the endpoint via config.yaml. env-var fallback is intentionally absent.
    ep = (bc.endpoint or "").strip()
    if not ep:
        raise ValueError("otlp requires endpoint")
    extra = dict(bc.headers or {})
    return _ResolvedBackend(
        type="otlp",
        endpoint=ep,
        display_name=bc.name or "OTLP",
        headers=extra or None,
        supports_metrics=_metrics_for("otlp", bc.metrics),
        supports_logs=_logs_for("otlp", bc.logs),
    )


def _resolve_lgtm(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve the Grafana LGTM stack (Grafana + Loki + Tempo + Mimir + collector).

    Functionally identical to :func:`_resolve_otlp` — the LGTM container
    exposes a standard OTLP HTTP receiver on the collector at :4318. We
    keep this as a distinct type purely so users running the shipped
    ``docker-compose/lgtm.yaml`` can declare ``type: lgtm`` in config.yaml
    and self-document the intent, instead of ``type: otlp name: lgtm``.
    The display name defaults to ``LGTM`` so startup logs say what they
    actually are.
    """
    ep = (bc.endpoint or "").strip()
    if not ep:
        raise ValueError("lgtm requires endpoint")
    extra = dict(bc.headers or {})
    return _ResolvedBackend(
        type="lgtm",
        endpoint=ep,
        display_name=_display(bc, "lgtm"),
        headers=extra or None,
        supports_metrics=_metrics_for("lgtm", bc.metrics),
        supports_logs=_logs_for("lgtm", bc.logs),
    )


_RESOLVERS: Dict[str, Callable[[BackendConfig], _ResolvedBackend]] = {
    "phoenix": _resolve_phoenix,
    "langfuse": _resolve_langfuse,
    "signoz": _resolve_signoz,
    "jaeger": _resolve_jaeger,
    "tempo": _resolve_tempo,
    "otlp": _resolve_otlp,
    "generic": _resolve_otlp,
    "lgtm": _resolve_lgtm,
}


# ── Public API ─────────────────────────────────────────────────────────────


def resolve(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a declared ``BackendConfig`` into a ready-to-wire backend.

    Raises :class:`ValueError` if required fields are missing or the
    backend type is unknown.
    """
    t = (bc.type or "").strip().lower()
    resolver = _RESOLVERS.get(t)
    if resolver is None:
        raise ValueError(f"unknown backend type {bc.type!r}")
    return resolver(bc)


def resolve_from_env() -> Optional[_ResolvedBackend]:
    """Try each backend in priority order; return the first one whose
    required env vars are fully satisfied. Returns ``None`` when no
    backend qualifies — the caller should then log a helpful message.
    """
    for backend_type in _ENV_PRIORITY:
        try:
            return resolve(BackendConfig(type=backend_type))
        except ValueError:
            continue
    return None
