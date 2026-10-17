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

Adding a backend: one :class:`~hermes_otel.backend_specs.BackendSpec` in
``backend_specs.py`` (display name, signals, env opt-in, credentials, ...),
then a ``_resolve_<name>(bc)`` function below registered in ``_RESOLVERS``.
Every per-type table this module used to keep (signals, display names, env
priority, opt-in, vendor credentials, temporality and root-usage presets) is
a view over the manifest (#402).
"""

from __future__ import annotations

import base64
import dataclasses
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .backend_specs import ENV_PRIORITY, SPECS, spec_for
from .backend_specs import KNOWN_TYPES as SPEC_KNOWN_TYPES
from .plugin_config import BackendConfig, normalize_temporality

# Every per-type fact lives in the manifest (#402); these are the views the
# rest of this module (and the tests) read.
_TRACES_ONLY = frozenset(s.type for s in SPECS if s.traces_only)
_LOGS_CAPABLE = frozenset(s.type for s in SPECS if s.logs)
# Backends that take traces and logs but silently drop metrics (Laminar's
# /v1/metrics answers 200 and stores nothing); metrics default off for them.
_NO_METRICS = frozenset(s.type for s in SPECS if not s.metrics and s.logs)
_DISPLAY_NAMES: Dict[str, str] = {s.type: s.display_name for s in SPECS}

# Honeycomb OTLP/HTTP base endpoints by region (the SDK-style ``/v1/traces``
# suffix is appended by the resolver; ``tracer.py`` / ``log_handler.py`` derive
# the ``/v1/metrics`` and ``/v1/logs`` variants from it).
_HONEYCOMB_ENDPOINTS = {
    "us": "https://api.honeycomb.io",
    "eu": "https://api.eu1.honeycomb.io",
}

_WEAVE_DEFAULT_ENDPOINT = "https://trace.wandb.ai/otel/v1/traces"

# Priority for env-var-driven single-backend detection. First backend whose
# required env vars are fully set wins.
_ENV_PRIORITY: List[str] = list(ENV_PRIORITY)

# Env-var mode must not switch on export to a vendor just because that vendor's
# SDK-standard credentials are in the environment (#255): these types need at
# least one plugin-namespaced variable as the explicit opt-in.
_ENV_OPT_IN: Dict[str, Tuple[str, ...]] = {s.type: s.env_opt_in for s in SPECS if s.env_opt_in}

# The vendor-variable sets that used to select each type on their own; each
# inner tuple is one alternative spelling. Used only to tell the user why
# env-var mode did not export (#259).
_VENDOR_CREDENTIALS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    s.type: s.vendor_credentials for s in SPECS if s.vendor_credentials
}


def traces_url(endpoint: str) -> str:
    """``<base>/v1/traces`` from an OTLP base, a per-signal URL or a slash-terminated
    spelling: the one normalisation every plain OTLP resolver applies."""
    base = re.sub(r"/v1/(traces|metrics|logs)$", "", endpoint.strip().rstrip("/")).rstrip("/")
    return f"{base}/v1/traces"


def _set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def vendor_credentials_without_opt_in() -> List[Tuple[str, List[str], Tuple[str, ...]]]:
    """Types whose vendor credentials are in the environment but whose
    ``OTEL_*`` opt-in is not: ``[(type, present_vendor_vars, opt_in_vars)]``.

    Env-var mode deliberately ignores these (see ``_ENV_OPT_IN``); the caller
    turns the list into a one-line notice so the silence is explained.
    """
    out: List[Tuple[str, List[str], Tuple[str, ...]]] = []
    for backend_type, opt_in in _ENV_OPT_IN.items():
        if any(_set(name) for name in opt_in):
            continue
        present: List[str] = []
        for group in _VENDOR_CREDENTIALS[backend_type]:
            hit = next((name for name in group if _set(name)), None)
            if hit is None:
                present = []
                break
            present.append(hit)
        if present:
            out.append((backend_type, present, opt_in))
    return out


def env_opt_in_hints() -> List[str]:
    """Human-readable version of :func:`vendor_credentials_without_opt_in`."""
    hints: List[str] = []
    for backend_type, present, opt_in in vendor_credentials_without_opt_in():
        label = _DISPLAY_NAMES.get(backend_type, backend_type)
        hints.append(
            f"{' and '.join(present)} {'is' if len(present) == 1 else 'are'} set, but env-var mode "
            f"only exports to {label} when one of {', '.join(opt_in)} is also set "
            f"(or {backend_type} is listed under backends:); not exporting."
        )
    return hints


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
    metrics_headers: Optional[Dict[str, str]] = None
    logs_headers: Optional[Dict[str, str]] = None
    # Full per-signal URLs when a backend keeps its signals on different
    # hosts or paths (VictoriaMetrics' three stores, #366); ``None`` means
    # derive ``/v1/metrics`` / ``/v1/logs`` from the traces URL.
    metrics_endpoint: Optional[str] = None
    logs_endpoint: Optional[str] = None
    supports_traces: bool = True
    supports_metrics: bool = True
    supports_logs: bool = False
    resource_attributes: Optional[Dict[str, str]] = None
    # Per-backend log settings from the entry's ``logs:`` mapping (#266).
    log_overrides: Optional[Dict[str, Any]] = None
    # ``cumulative`` / ``delta`` for this backend's metric reader; ``None`` =
    # the top-level ``metrics_temporality`` or the SDK default (#233).
    metrics_temporality: Optional[str] = None
    # Whether the ``agent`` / ``cron`` root keeps its token roll-up on the way
    # to this backend; ``False`` strips it for this exporter only (#327).
    root_usage: bool = True
    protocol: str = "http/protobuf"
    insecure: Optional[bool] = None
    certificate_file: Optional[str] = None
    client_key_file: Optional[str] = None
    client_certificate_file: Optional[str] = None


# ── Shared helpers ─────────────────────────────────────────────────────────


def _metrics_for(backend_type: str, override: Optional[bool]) -> bool:
    if override is not None:
        return override
    spec = spec_for(backend_type)
    return spec.metrics if spec is not None else True


def _logs_for(backend_type: str, override: Optional[bool]) -> bool:
    if override is not None:
        return override
    spec = spec_for(backend_type)
    return spec.logs if spec is not None else False


def _traces_for(override: Optional[bool]) -> bool:
    # Traces are the primary signal for every existing backend. ``False`` is
    # opt-in for query-only/dashboard-only entries that should not receive span
    # exports (for example, querying Tempo directly while exporting through an
    # OTel Collector).
    return True if override is None else override


@dataclass(frozen=True)
class SecretChoice:
    """Which source supplied a credential: ``inline`` (the entry), ``env``
    (the entry's ``<field>_env``), ``fallback`` (a vendor env var) or ``None``
    (nothing set); ``env_name`` is the variable read for the last two."""

    value: Optional[str] = None
    origin: Optional[str] = None
    env_name: Optional[str] = None


def choose_secret(
    inline: Optional[str],
    env_name: Optional[str],
    fallback_envs: Sequence[str],
) -> SecretChoice:
    """The first available secret: inline > named env > fallback envs. The
    one precedence rule the resolvers and the Settings tab share."""
    if inline:
        v = inline.strip()
        if v:
            return SecretChoice(v, "inline")
    if env_name:
        v = os.getenv(env_name, "").strip()
        if v:
            return SecretChoice(v, "env", env_name)
    for name in fallback_envs:
        v = os.getenv(name, "").strip()
        if v:
            return SecretChoice(v, "fallback", name)
    return SecretChoice()


def _resolve_secret(
    inline: Optional[str],
    env_name: Optional[str],
    fallback_envs: List[str],
) -> Optional[str]:
    """Pick the first available secret value. Inline > named env > fallback envs."""
    return choose_secret(inline, env_name, fallback_envs).value


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
    )


_LANGFUSE_OTEL_PATH = "/api/public/otel"


def langfuse_traces_url(endpoint: str) -> str:
    """Normalise a Langfuse ``endpoint`` to the full OTLP traces URL.

    The OTLP/HTTP span exporter posts to exactly the URL it is given, so a
    Langfuse endpoint must end in ``/api/public/otel/v1/traces``. Accept the
    three forms people actually write and complete them:

    * ``https://host`` / ``https://host/`` → ``https://host/api/public/otel/v1/traces``
    * ``https://host/api/public/otel``     → ``…/api/public/otel/v1/traces``
    * ``https://host/api/public/otel/v1/traces`` → unchanged

    Before this, a root URL or the ``/api/public/otel`` form (the one the docs
    used to show) posted to the wrong path and every export died with a 405
    that only the ``opentelemetry`` logger saw.
    """
    ep = endpoint.strip().rstrip("/")
    if ep.endswith("/v1/traces"):
        return ep
    if ep.endswith(_LANGFUSE_OTEL_PATH):
        return ep + "/v1/traces"
    parsed = urllib.parse.urlsplit(ep)
    if parsed.path in ("", "/"):
        return ep + _LANGFUSE_OTEL_PATH + "/v1/traces"
    # Some other path (a reverse proxy prefix): trust it but complete the
    # standard suffix if it is missing.
    return ep + "/v1/traces"


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
    ep = langfuse_traces_url(ep)
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
    )


def _resolve_zipkin(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a Zipkin backend (traces only, OTLP/HTTP through the zipkin-otel
    contrib module).

    The stock ``openzipkin/zipkin`` image has no OTLP endpoint; the
    ``ghcr.io/openzipkin-contrib/zipkin-otel`` image serves ``POST /v1/traces``
    on the query port (9411) next to the UI and the v2 API. ``endpoint`` is
    required (no default host); a bare origin gets the ``/v1/traces`` suffix
    and a ``/v1/metrics`` or ``/v1/logs`` URL is rewritten to it.

    Auth: none upstream; anything in ``headers`` is passed through for a proxy.

    Signals: traces only. The module answers ``/v1/metrics`` with 404 and keeps
    a ``/v1/logs`` record only as an annotation on a span it can name, which
    the plugin's log records never do, so both default off (``_TRACES_ONLY``).
    """
    ep = (bc.endpoint or os.getenv("OTEL_ZIPKIN_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("zipkin requires endpoint (or set OTEL_ZIPKIN_ENDPOINT)")
    ep = traces_url(ep)
    extra = dict(bc.headers or {})
    return _ResolvedBackend(
        type="zipkin",
        endpoint=ep,
        display_name=_display(bc, "zipkin"),
        headers=extra or None,
    )


# GreptimeDB's OTLP prefix and the pipeline its trace ingest requires.
_GREPTIME_OTLP_PREFIX = "/v1/otlp"
_GREPTIME_TRACE_PIPELINE = "greptime_trace_v1"


def _resolve_greptime(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a GreptimeDB backend (standalone or cluster, OTLP/HTTP, all three
    signals).

    GreptimeDB ingests OTLP under ``/v1/otlp``: ``/v1/otlp/v1/traces``,
    ``/v1/otlp/v1/metrics`` and ``/v1/otlp/v1/logs`` on its HTTP port (4000
    upstream, 4398 in the bundled stack). ``endpoint`` is required (no default
    host); a bare origin, the ``/v1/otlp`` prefix or any of the three full URLs
    all resolve to the traces URL, and ``tracer.py`` / ``log_handler.py``
    derive the other two from it.

    Headers: traces **must** carry ``X-Greptime-Pipeline-Name:
    greptime_trace_v1`` (the only pipeline the trace model accepts; without it
    the batch is rejected). Metrics and logs do not take it (a pipeline name on
    the logs route would select a log pipeline of that name), so they get their
    own header sets. ``db`` / ``db_env`` (fallback ``OTEL_GREPTIME_DB``) sets
    ``X-Greptime-DB-Name``; unset means the server default, ``public``. Basic
    auth from ``user`` + ``password`` (fallbacks ``OTEL_GREPTIME_USER`` /
    ``OTEL_GREPTIME_PASSWORD``) when the server runs with a user provider;
    omitted entirely when neither resolves, which is what a local stack wants.

    Signals: traces, metrics and logs all on by default. GreptimeDB stores
    cumulative and delta points as sent (no preset) and rejects exponential
    histograms, which the plugin does not emit by default.
    """
    ep = (bc.endpoint or os.getenv("OTEL_GREPTIME_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("greptime requires endpoint (or set OTEL_GREPTIME_ENDPOINT)")
    ep = ep.rstrip("/")
    ep = re.sub(r"/v1/(traces|metrics|logs)$", "", ep).rstrip("/")
    if ep.endswith(_GREPTIME_OTLP_PREFIX):
        ep = ep[: -len(_GREPTIME_OTLP_PREFIX)].rstrip("/")
    ep = f"{ep}{_GREPTIME_OTLP_PREFIX}/v1/traces"

    common: Dict[str, str] = {}
    db = _resolve_secret(bc.db, bc.db_env, ["OTEL_GREPTIME_DB"])
    if db:
        common["X-Greptime-DB-Name"] = db
    user = _resolve_secret(bc.user, bc.user_env, ["OTEL_GREPTIME_USER"])
    pw = _resolve_secret(bc.password, bc.password_env, ["OTEL_GREPTIME_PASSWORD"])
    if user and pw:
        common["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    common.update(bc.headers or {})
    trace_headers = {"X-Greptime-Pipeline-Name": _GREPTIME_TRACE_PIPELINE, **common}
    # A user-supplied pipeline header (another trace model, e.g. v2) wins.
    if bc.headers and "X-Greptime-Pipeline-Name" in bc.headers:
        trace_headers["X-Greptime-Pipeline-Name"] = bc.headers["X-Greptime-Pipeline-Name"]

    return _ResolvedBackend(
        type="greptime",
        endpoint=ep,
        display_name=_display(bc, "greptime"),
        headers=trace_headers,
        metrics_headers=dict(common),
        logs_headers=dict(common),
    )


def _resolve_clickstack(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a ClickStack / HyperDX backend (all-in-one or split stack,
    OTLP/HTTP, all three signals).

    ClickStack's bundled OpenTelemetry Collector listens on the standard
    ``/v1/traces``, ``/v1/metrics`` and ``/v1/logs`` paths (port 4318 in the
    container, 4408 in the bundled stack) and **requires the team's ingestion
    API key** on every request, as a bare ``authorization: <key>`` header (no
    ``Bearer``). ``endpoint`` is required (no default host); a bare base URL
    gets the ``/v1/traces`` suffix and ``tracer.py`` / ``log_handler.py``
    derive the other two from it.

    The key comes from ``api_key`` / ``api_key_env``, else
    ``OTEL_CLICKSTACK_API_KEY`` / ``HYPERDX_API_KEY`` (the vendor SDK's own
    variable). Without one the Collector answers 401 for every batch, so a
    missing key skips the entry at startup instead of filling the debug log
    with failures; the key is minted in the UI (Team Settings → API Keys) or
    read from the all-in-one's MongoDB (docker-compose/clickstack/README.md).

    Signals: traces, metrics and logs all on; the ClickHouse exporter stores
    every metric type as sent, so no temporality preset.
    """
    ep = (bc.endpoint or os.getenv("OTEL_CLICKSTACK_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("clickstack requires endpoint (or set OTEL_CLICKSTACK_ENDPOINT)")
    ep = traces_url(ep)

    key = _resolve_secret(
        bc.api_key, bc.api_key_env, ["OTEL_CLICKSTACK_API_KEY", "HYPERDX_API_KEY"]
    )
    if not key:
        raise ValueError(
            "clickstack requires api_key / api_key_env (the team's ingestion API key; "
            "or set OTEL_CLICKSTACK_API_KEY)"
        )
    headers: Dict[str, str] = {"authorization": key}
    headers.update(bc.headers or {})

    return _ResolvedBackend(
        type="clickstack",
        endpoint=ep,
        display_name=_display(bc, "clickstack"),
        headers=headers,
    )


_VICTORIA_TRACES_PATH = "/insert/opentelemetry/v1/traces"
_VICTORIA_METRICS_PATH = "/opentelemetry/v1/metrics"
_VICTORIA_LOGS_PATH = "/insert/opentelemetry/v1/logs"


def _victoria_url(raw: str, path: str) -> str:
    """An origin, or the full ingest URL, of one Victoria store → that store's
    OTLP URL. Any ``/insert/opentelemetry/v1/<signal>``, ``/opentelemetry/v1/<signal>``
    or ``/v1/<signal>`` tail is replaced by the store's own path."""
    base = re.sub(r"(/insert)?(/opentelemetry)?/v1/(traces|metrics|logs)$", "", raw.rstrip("/"))
    return f"{base.rstrip('/')}{path}"


def _resolve_victoria(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve the VictoriaMetrics family: VictoriaTraces, VictoriaMetrics and
    VictoriaLogs, three single-binary stores with one OTLP/HTTP route each.

    ``endpoint`` is VictoriaTraces (``:10428``, traces land on
    ``/insert/opentelemetry/v1/traces``); ``metrics_endpoint`` is
    VictoriaMetrics (``:8428``, ``/opentelemetry/v1/metrics``) and
    ``logs_endpoint`` VictoriaLogs (``:9428``, ``/insert/opentelemetry/v1/logs``).
    Each takes an origin or the full URL, also via ``OTEL_VICTORIA_ENDPOINT``,
    ``OTEL_VICTORIA_METRICS_ENDPOINT`` and ``OTEL_VICTORIA_LOGS_ENDPOINT``. The
    three stores are separate processes, so a signal whose store is not named
    is simply off: there is no host to derive it on.

    Headers: none upstream. Basic auth from ``user`` + ``password`` (fallbacks
    ``OTEL_VICTORIA_USER`` / ``OTEL_VICTORIA_PASSWORD``) when the stores run
    with ``-httpAuth.*``. Logs carry ``VL-Stream-Fields: service.name`` so
    VictoriaLogs keys its streams on the service, not on every resource
    attribute (``service.instance.id`` changes with every Hermes process);
    ``headers`` can override it.

    Signals: traces on; metrics and logs on when their store is named.
    VictoriaMetrics stores cumulative points natively (no preset); exponential
    histograms become its ``vmrange`` histograms.
    """
    ep = (bc.endpoint or os.getenv("OTEL_VICTORIA_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError(
            "victoria requires endpoint (VictoriaTraces; or set OTEL_VICTORIA_ENDPOINT)"
        )
    traces_url = _victoria_url(ep, _VICTORIA_TRACES_PATH)
    m_ep = (bc.metrics_endpoint or os.getenv("OTEL_VICTORIA_METRICS_ENDPOINT", "")).strip()
    l_ep = (bc.logs_endpoint or os.getenv("OTEL_VICTORIA_LOGS_ENDPOINT", "")).strip()
    metrics_url = _victoria_url(m_ep, _VICTORIA_METRICS_PATH) if m_ep else None
    logs_url = _victoria_url(l_ep, _VICTORIA_LOGS_PATH) if l_ep else None

    if bc.metrics is True and not metrics_url:
        raise ValueError("victoria metrics need metrics_endpoint (VictoriaMetrics)")
    if bc.logs is True and not logs_url:
        raise ValueError("victoria logs need logs_endpoint (VictoriaLogs)")

    common: Dict[str, str] = {}
    user = _resolve_secret(bc.user, bc.user_env, ["OTEL_VICTORIA_USER"])
    pw = _resolve_secret(bc.password, bc.password_env, ["OTEL_VICTORIA_PASSWORD"])
    if user and pw:
        common["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    common.update(bc.headers or {})
    log_headers = {"VL-Stream-Fields": "service.name", **common}

    return _ResolvedBackend(
        type="victoria",
        endpoint=traces_url,
        display_name=_display(bc, "victoria"),
        headers=dict(common),
        metrics_headers=dict(common),
        logs_headers=log_headers,
        metrics_endpoint=metrics_url,
        logs_endpoint=logs_url,
        supports_traces=_traces_for(bc.traces),
        supports_metrics=_metrics_for("victoria", bc.metrics) and metrics_url is not None,
        supports_logs=_logs_for("victoria", bc.logs) and logs_url is not None,
    )


def _resolve_aspire(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a standalone .NET Aspire Dashboard (OTLP/HTTP, all three signals).

    The dashboard takes OTLP on the standard ``/v1/traces``, ``/v1/metrics``
    and ``/v1/logs`` paths of its OTLP/HTTP port (18890 upstream, 4418 in the
    bundled stack); the UI and the read API live on another port (18888).
    ``endpoint`` is required (no default host); a bare base URL gets the
    ``/v1/traces`` suffix and the other two are derived from it.

    Auth: none in anonymous mode (the bundled stack). With
    ``DASHBOARD__OTLP__AUTHMODE=ApiKey`` the dashboard wants the key in an
    ``x-otlp-api-key`` header: ``api_key`` / ``api_key_env`` (fallback
    ``OTEL_ASPIRE_API_KEY``) fills it; omitted entirely when none resolves.

    Signals: traces, metrics and logs all on. The dashboard stores Gauge, Sum
    and Histogram points and **rejects exponential histograms** ("is not
    supported"), so the plugin's default explicit-bucket histograms are what
    it wants; no temporality preset.
    """
    ep = (bc.endpoint or os.getenv("OTEL_ASPIRE_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("aspire requires endpoint (or set OTEL_ASPIRE_ENDPOINT)")
    ep = traces_url(ep)
    key = _resolve_secret(bc.api_key, bc.api_key_env, ["OTEL_ASPIRE_API_KEY"])
    headers: Dict[str, str] = {}
    if key:
        headers["x-otlp-api-key"] = key
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="aspire",
        endpoint=ep,
        display_name=_display(bc, "aspire"),
        headers=headers,
    )


def _resolve_gigapipe(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a Gigapipe (formerly qryn) backend: OTLP/HTTP, all three signals,
    one port.

    Gigapipe takes ``/v1/traces``, ``/v1/metrics`` and ``/v1/logs`` on the
    same port that serves its Tempo, Prometheus and Loki query APIs (3100
    upstream, 3110 in the bundled stack). ``endpoint`` is required (no
    default host); a bare base URL gets the ``/v1/traces`` suffix and the
    other two are derived from it. Traces and logs are protobuf only, which is
    what the plugin sends.

    Auth: none unless the server runs with ``GIGAPIPE_LOGIN`` /
    ``GIGAPIPE_PASSWORD``; then Basic from ``user`` + ``password`` (fallbacks
    ``OTEL_GIGAPIPE_USER`` / ``OTEL_GIGAPIPE_PASSWORD``), omitted otherwise.

    Signals: all on. Gigapipe stores cumulative points and **rejects delta
    points** one by one (reported in ``partial_success``), so the SDK's
    cumulative default is the right one; no preset.
    """
    ep = (bc.endpoint or os.getenv("OTEL_GIGAPIPE_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("gigapipe requires endpoint (or set OTEL_GIGAPIPE_ENDPOINT)")
    ep = traces_url(ep)
    headers: Dict[str, str] = {}
    user = _resolve_secret(bc.user, bc.user_env, ["OTEL_GIGAPIPE_USER"])
    pw = _resolve_secret(bc.password, bc.password_env, ["OTEL_GIGAPIPE_PASSWORD"])
    if user and pw:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="gigapipe",
        endpoint=ep,
        display_name=_display(bc, "gigapipe"),
        headers=headers or None,
    )


_AGENTA_CLOUD = {"us": "https://us.cloud.agenta.ai", "eu": "https://eu.cloud.agenta.ai"}
_AGENTA_OTLP_PATH = "/api/otlp/v1/traces"


def _resolve_agenta(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve an Agenta backend (self-hosted or Agenta Cloud, traces only).

    Agenta ingests OTLP traces at ``/api/otlp/v1/traces`` (protobuf only) and
    nothing else: no metrics or logs routes. ``endpoint`` is the origin of a
    self-hosted stack (``http://localhost:3060`` for the bundled one), or the
    full ingest URL; without one the cloud host for ``region`` (``us`` default,
    ``eu``) is used, so a cloud key alone is enough.

    Auth: ``Authorization: ApiKey <project key>`` from ``api_key`` /
    ``api_key_env`` (fallbacks ``OTEL_AGENTA_API_KEY``, ``AGENTA_API_KEY``, the
    SDK's variable). The key is scoped to one project and routes the spans
    there; a missing key skips the entry at startup instead of a 401 per batch.

    Signals: traces only (``_TRACES_ONLY``).
    """
    region = (bc.region or "us").strip().lower()
    ep = (
        bc.endpoint
        or os.getenv("OTEL_AGENTA_ENDPOINT", "")
        or os.getenv("AGENTA_HOST", "")
        or _AGENTA_CLOUD.get(region, _AGENTA_CLOUD["us"])
    ).strip()
    ep = re.sub(r"(/api)?(/otlp)?(/v1/traces)?/?$", "", ep.rstrip("/"))
    ep = f"{ep}{_AGENTA_OTLP_PATH}"
    key = _resolve_secret(bc.api_key, bc.api_key_env, ["OTEL_AGENTA_API_KEY", "AGENTA_API_KEY"])
    if not key:
        raise ValueError(
            "agenta requires api_key / api_key_env (a project API key; or set OTEL_AGENTA_API_KEY)"
        )
    headers: Dict[str, str] = {"Authorization": f"ApiKey {key}"}
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="agenta",
        endpoint=ep,
        display_name=_display(bc, "agenta"),
        headers=headers,
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
    )


def _resolve_lgtm(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve the Grafana LGTM stack (Grafana + Loki + Tempo + Mimir + collector).

    Functionally identical to :func:`_resolve_otlp` — the LGTM container
    exposes a standard OTLP HTTP receiver on the collector at :4318. We
    keep this as a distinct type purely so users running the shipped
    ``docker-compose/lgtm/docker-compose.yaml`` can declare ``type: lgtm`` in config.yaml
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
    )


def _resolve_uptrace(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve Uptrace (all-in-one traces/metrics/logs backend).

    Auth model: per-project DSN sent in the ``uptrace-dsn`` request header,
    e.g. ``http://project1_secret@localhost:14318?grpc=14317``. The DSN
    carries the ingestion token; the endpoint URL is where OTLP payloads
    land. We don't try to parse the DSN — Uptrace does that server-side.
    """
    ep = (bc.endpoint or os.getenv("OTEL_UPTRACE_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("uptrace requires endpoint")
    dsn = _resolve_secret(
        bc.dsn,
        bc.dsn_env,
        ["OTEL_UPTRACE_DSN", "UPTRACE_DSN"],
    )
    if not dsn:
        raise ValueError("uptrace requires dsn (e.g. http://<project_token>@host:14318?grpc=14317)")
    headers: Dict[str, str] = {"uptrace-dsn": dsn}
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="uptrace",
        endpoint=ep,
        display_name=_display(bc, "uptrace"),
        headers=headers,
    )


def _resolve_openobserve(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve OpenObserve (all-in-one traces/metrics/logs backend).

    Auth model: HTTP Basic using the admin email + password (or any user
    created in the UI), plus an optional ``stream-name`` header that
    routes ingested data into a named stream (defaults to ``default``).
    The endpoint URL embeds the org in its path, e.g.
    ``http://localhost:5080/api/default/v1/traces``.
    """
    ep = (bc.endpoint or os.getenv("OTEL_OPENOBSERVE_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("openobserve requires endpoint")
    user = _resolve_secret(
        bc.user,
        bc.user_env,
        ["OTEL_OPENOBSERVE_USER", "OPENOBSERVE_USER"],
    )
    pw = _resolve_secret(
        bc.password,
        bc.password_env,
        ["OTEL_OPENOBSERVE_PASSWORD", "OPENOBSERVE_PASSWORD"],
    )
    if not (user and pw):
        raise ValueError("openobserve requires user and password")
    stream = (bc.stream_name or os.getenv("OTEL_OPENOBSERVE_STREAM", "") or "default").strip()
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    headers: Dict[str, str] = {
        "Authorization": f"Basic {auth}",
        "stream-name": stream,
    }
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="openobserve",
        endpoint=ep,
        display_name=_display(bc, "openobserve"),
        headers=headers,
    )


def _resolve_parseable(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve Parseable direct OTLP/HTTP ingest for all three signals."""
    ep = (bc.endpoint or os.getenv("OTEL_PARSEABLE_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("parseable requires endpoint")
    key = _resolve_secret(
        bc.api_key,
        bc.api_key_env,
        ["OTEL_PARSEABLE_API_KEY", "PARSEABLE_API_KEY"],
    )
    if not key:
        raise ValueError("parseable requires api_key")

    traces_dataset = (
        bc.traces_dataset or os.getenv("PARSEABLE_TRACES_DATASET", "") or "hermes-traces"
    ).strip()
    metrics_dataset = (
        bc.metrics_dataset or os.getenv("PARSEABLE_METRICS_DATASET", "") or "hermes-metrics"
    ).strip()
    logs_dataset = (
        bc.logs_dataset or os.getenv("PARSEABLE_LOGS_DATASET", "") or "hermes-logs"
    ).strip()

    common = {"X-API-Key": key}
    trace_headers = {**common, "X-P-Stream": traces_dataset, "X-P-Log-Source": "otel-traces"}
    metric_headers = {**common, "X-P-Stream": metrics_dataset, "X-P-Log-Source": "otel-metrics"}
    log_headers = {**common, "X-P-Stream": logs_dataset, "X-P-Log-Source": "otel-logs"}
    for signal_headers in (trace_headers, metric_headers, log_headers):
        signal_headers.update(bc.headers or {})

    return _ResolvedBackend(
        type="parseable",
        endpoint=ep,
        display_name=_display(bc, "parseable"),
        headers=trace_headers,
        metrics_headers=metric_headers,
        logs_headers=log_headers,
    )


def _resolve_honeycomb(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve Honeycomb (SaaS, OTLP/HTTP — traces + metrics + logs).

    Auth model: the API key travels in the ``x-honeycomb-team`` header. An
    optional ``dataset`` is sent as ``x-honeycomb-dataset``; ``region``
    (``us``|``eu``) selects the base endpoint when one isn't given explicitly.

    Endpoint: defaults to the US ingest host (or EU for ``region: eu``) with the
    ``/v1/traces`` suffix the OTLP pipeline expects. ``tracer.py`` and
    ``log_handler.py`` rewrite that suffix to ``/v1/metrics`` and ``/v1/logs``,
    which matches Honeycomb's per-signal path scheme exactly.

    Dataset routing: ``x-honeycomb-dataset`` is honored only by Honeycomb
    Classic keys (where it's required for every signal). Modern "Environments"
    keys ignore it — traces route by ``service.name`` and metrics go to the
    environment's default ``Metrics`` dataset (verified live; see
    ``HONEYCOMB.md``). The plugin sends ``dataset`` on all three exporters via
    one merged header set, which is correct for Classic and a harmless no-op for
    modern keys; leave ``dataset`` unset on a modern key.
    """
    key = _resolve_secret(
        bc.api_key,
        bc.api_key_env,
        ["OTEL_HONEYCOMB_API_KEY", "HONEYCOMB_API_KEY"],
    )
    if not key:
        raise ValueError(
            "honeycomb requires api_key (or set OTEL_HONEYCOMB_API_KEY / HONEYCOMB_API_KEY)"
        )

    ep = (bc.endpoint or os.getenv("OTEL_HONEYCOMB_ENDPOINT", "")).strip()
    if not ep:
        region = (bc.region or "us").strip().lower()
        base = _HONEYCOMB_ENDPOINTS.get(region)
        if base is None:
            raise ValueError(
                f"honeycomb region must be one of {sorted(_HONEYCOMB_ENDPOINTS)}, got {bc.region!r}"
            )
        ep = f"{base}/v1/traces"

    headers: Dict[str, str] = {"x-honeycomb-team": key}
    dataset = (bc.dataset or "").strip()
    if dataset:
        headers["x-honeycomb-dataset"] = dataset
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="honeycomb",
        endpoint=ep,
        display_name=_display(bc, "honeycomb"),
        headers=headers,
    )


# LangSmith's OTLP ingest base per region; the plugin appends ``/otel/v1/traces``.
_LANGSMITH_ENDPOINTS = {
    "us": "https://api.smith.langchain.com",
    "eu": "https://eu.api.smith.langchain.com",
}


def _langsmith_traces_url(ep: str) -> str:
    """``.../otel/v1/traces`` from an API base, an ``/otel`` base or a full URL.

    ``LANGSMITH_ENDPOINT`` is the SDK's API base (``https://api.smith.langchain.com``);
    the OTLP routes live under ``/otel`` on the same host."""
    base = re.sub(r"/v1/(traces|metrics|logs)$", "", ep.strip().rstrip("/")).rstrip("/")
    if not base.endswith("/otel"):
        base = f"{base}/otel"
    return f"{base}/v1/traces"


def _resolve_langsmith(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve LangSmith (SaaS, OTLP/HTTP — traces only).

    LangSmith accepts OTLP traces at ``<api base>/otel/v1/traces`` with the
    API key in ``x-api-key`` and the target project in ``Langsmith-Project``.
    Until 1.26 the plugin drove LangSmith's run-tree HTTP API instead, selected
    by ``LANGSMITH_TRACING=true`` and outside the ``backends:`` fan-out; that
    path is gone (#415) and ``LANGSMITH_TRACING`` selects nothing. The vendor
    variables (``LANGSMITH_API_KEY``, ``LANGSMITH_PROJECT``,
    ``LANGSMITH_ENDPOINT``) still fill in values once the entry or the
    ``OTEL_LANGSMITH_*`` opt-in names the backend (#255).
    """
    key = _resolve_secret(
        bc.api_key, bc.api_key_env, ["OTEL_LANGSMITH_API_KEY", "LANGSMITH_API_KEY"]
    )
    if not key:
        raise ValueError(
            "langsmith requires api_key (or set OTEL_LANGSMITH_API_KEY / LANGSMITH_API_KEY)"
        )
    ep = (
        bc.endpoint
        or os.getenv("OTEL_LANGSMITH_ENDPOINT", "")
        or os.getenv("LANGSMITH_ENDPOINT", "")
    ).strip()
    if not ep:
        region = (bc.region or "us").strip().lower()
        base = _LANGSMITH_ENDPOINTS.get(region)
        if base is None:
            raise ValueError(
                f"langsmith region must be one of {sorted(_LANGSMITH_ENDPOINTS)}, got {bc.region!r}"
            )
        ep = base
    ep = _langsmith_traces_url(ep)
    headers: Dict[str, str] = {"x-api-key": key}
    project = _resolve_secret(
        bc.project, bc.project_env, ["OTEL_LANGSMITH_PROJECT", "LANGSMITH_PROJECT"]
    )
    if project:
        headers["Langsmith-Project"] = project
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="langsmith",
        endpoint=ep,
        display_name=_display(bc, "langsmith"),
        headers=headers,
    )


# Valid data-stream name component (Elastic's own rule for
# ``data_stream.dataset`` / ``data_stream.namespace``): lowercase letters,
# digits, '_' and '.', at most 100 characters. '-' is NOT allowed — a full
# stream name is ``<type>-<dataset>-<namespace>`` and is split on '-', so a
# '-' inside a component would make the name unparseable; EDOT silently
# rewrites it to '_'. Dots are fine and Elastic uses them itself
# (``generic.otel``); the collector's ``otel`` mapping mode appends ``.otel``
# to whatever dataset is sent, so ``dataset: hermes_otel`` lands in
# ``traces-hermes_otel.otel-<namespace>``.
_ELASTIC_DS_COMPONENT = re.compile(r"[a-z0-9_.]{1,100}")


def _resolve_elastic(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve an Elastic OTLP backend (Elastic Cloud mOTLP or self-hosted EDOT).

    Auth model: Elastic's managed OTLP endpoint (mOTLP) authenticates with an
    API key in the standard ``Authorization`` header, but with the ``ApiKey``
    scheme — ``Authorization: ApiKey <key>`` — not ``Bearer``. A key is
    optional: a self-hosted EDOT Collector on your own network typically runs
    without auth, so when no key resolves the header is omitted entirely.

    Endpoint: required (no default) — Elastic's is either the cloud project's
    mOTLP URL (``https://<hash>.apm.<region>.gcp.elastic-cloud.com:443``, one
    endpoint for all signals) or your EDOT Collector's OTLP/HTTP address. A
    bare base URL is given the ``/v1/traces`` suffix the pipeline expects;
    ``tracer.py`` / ``log_handler.py`` derive the ``/v1/metrics`` and
    ``/v1/logs`` variants from it.

    Data streams: optional ``dataset`` / ``namespace`` fields are copied to
    the ``data_stream.dataset`` / ``data_stream.namespace`` Resource
    attributes, which Elasticsearch data-stream routing keys off. When unset,
    Elastic defaults the dataset per signal (``traces``, ``metrics``,
    ``logs``). Both attributes land on the plugin's single shared Resource,
    so every configured backend receives them; two elastic entries with
    different values conflict at init.
    """
    ep = (bc.endpoint or os.getenv("OTEL_ELASTIC_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("elastic requires endpoint (or set OTEL_ELASTIC_ENDPOINT)")
    # Normalise to the traces URL the pipeline keys off: a bare base gets the
    # suffix, and a per-signal URL for another signal is rewritten so that the
    # derived /v1/metrics and /v1/logs variants stay correct.
    ep = traces_url(ep)

    key = _resolve_secret(bc.api_key, bc.api_key_env, ["OTEL_ELASTIC_API_KEY", "ELASTIC_API_KEY"])
    headers: Dict[str, str] = {}
    if key:
        headers["Authorization"] = f"ApiKey {key}"
    headers.update(bc.headers or {})

    resource_attrs: Dict[str, str] = {}
    for field, attr in (("dataset", "data_stream.dataset"), ("namespace", "data_stream.namespace")):
        value = getattr(bc, field)
        if not value:
            continue
        if not _ELASTIC_DS_COMPONENT.fullmatch(value):
            raise ValueError(
                f"elastic {field} {value!r} is not a valid data-stream component "
                "(lowercase letters, digits, '_' and '.' only, no '-'; max 100 chars)"
            )
        resource_attrs[attr] = value

    return _ResolvedBackend(
        type="elastic",
        endpoint=ep,
        display_name=_display(bc, "elastic"),
        headers=headers,
        resource_attributes=resource_attrs or None,
    )


def _resolve_openlit(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve an OpenLIT backend (self-hosted, OTLP/HTTP, all three signals).

    OpenLIT runs an OTLP receiver next to its UI: ``/v1/traces``, ``/v1/metrics``
    and ``/v1/logs`` on port 4318 of the app container (the UI port does not
    proxy them on the 2.1.0 image). ``endpoint`` is required (no default host); a bare base
    URL gets the ``/v1/traces`` suffix and ``tracer.py`` / ``log_handler.py``
    derive the other two from it.

    Auth: optional. An OpenLIT API key scopes ingest to an organisation,
    project and environment and travels as ``Authorization: Bearer <key>``;
    without one the data lands in the deployment's ``INIT_DB_*`` defaults,
    which is what a local compose stack wants. When no key resolves the header
    is omitted entirely.

    Signals: traces, metrics and logs all on by default; OpenLIT stores every
    OTel metric type with cumulative temporality, so no preset is needed.
    """
    ep = (bc.endpoint or os.getenv("OTEL_OPENLIT_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("openlit requires endpoint (or set OTEL_OPENLIT_ENDPOINT)")
    ep = traces_url(ep)

    key = _resolve_secret(bc.api_key, bc.api_key_env, ["OTEL_OPENLIT_API_KEY", "OPENLIT_API_KEY"])
    headers: Dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(bc.headers or {})

    return _ResolvedBackend(
        type="openlit",
        endpoint=ep,
        display_name=_display(bc, "openlit"),
        headers=headers,
    )


def _resolve_mlflow(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve an MLflow tracking server (OTLP/HTTP traces only, MLflow >= 3.6).

    MLflow ingests spans at ``/v1/traces`` and routes them by the mandatory
    ``x-mlflow-experiment-id`` header (400 without it). ``experiment_id``
    defaults to ``"0"``, the built-in Default experiment, and can come from
    ``experiment_id_env`` / ``OTEL_MLFLOW_EXPERIMENT_ID`` /
    ``MLFLOW_EXPERIMENT_ID``. ``workspace`` adds ``X-MLFLOW-WORKSPACE``.

    Auth: optional. A tracking token from ``api_key`` / ``api_key_env`` /
    ``OTEL_MLFLOW_API_KEY`` / ``MLFLOW_TRACKING_TOKEN`` travels as
    ``Authorization: Bearer <token>``; a plain local server needs none.

    Signals: traces only. The server has no ``/v1/metrics`` or ``/v1/logs``
    router (404), so both default off; MLflow derives token usage and cost
    from the span attributes itself.
    """
    ep = (bc.endpoint or os.getenv("OTEL_MLFLOW_ENDPOINT", "")).strip()
    if not ep:
        raise ValueError("mlflow requires endpoint (or set OTEL_MLFLOW_ENDPOINT)")
    ep = traces_url(ep)

    experiment = _resolve_secret(
        bc.experiment_id,
        bc.experiment_id_env,
        ["OTEL_MLFLOW_EXPERIMENT_ID", "MLFLOW_EXPERIMENT_ID"],
    )
    headers: Dict[str, str] = {"x-mlflow-experiment-id": str(experiment or "0").strip()}
    workspace = (bc.workspace or "").strip()
    if workspace:
        headers["X-MLFLOW-WORKSPACE"] = workspace
    token = _resolve_secret(
        bc.api_key, bc.api_key_env, ["OTEL_MLFLOW_API_KEY", "MLFLOW_TRACKING_TOKEN"]
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(bc.headers or {})

    return _ResolvedBackend(
        type="mlflow",
        endpoint=ep,
        display_name=_display(bc, "mlflow"),
        headers=headers,
    )


_OPIK_CLOUD = "https://www.comet.com/opik"
_OPIK_OTEL_PATH = "/api/v1/private/otel"


def _opik_traces_url(endpoint: str) -> str:
    """Normalise an Opik ``endpoint`` to the full OTLP traces URL.

    Opik ingests on a vendor path, ``<base>/api/v1/private/otel/v1/traces``.
    Accept the forms people write (the UI base, the SDK's ``…/api`` URL
    override, the ``…/api/v1/private/otel`` prefix, or the full traces URL)
    and complete them.
    """
    ep = endpoint.strip().rstrip("/")
    ep = re.sub(r"/v1/(traces|metrics|logs)$", "", ep).rstrip("/")
    if ep.endswith(_OPIK_OTEL_PATH):
        return ep + "/v1/traces"
    if ep.endswith("/api"):
        return ep + "/v1/private/otel/v1/traces"
    return ep + _OPIK_OTEL_PATH + "/v1/traces"


def _resolve_opik(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve Comet Opik (cloud or self-hosted, OTLP/HTTP traces only).

    Endpoint: the Opik base URL (self-host ``http://localhost:5173``, cloud
    ``https://www.comet.com/opik``) or any of the forms ``_opik_traces_url``
    accepts; defaults to the cloud host when unset. Also via
    ``OTEL_OPIK_ENDPOINT`` or the SDK's ``OPIK_URL_OVERRIDE``.

    Headers: cloud needs ``Authorization: <api key>`` (the bare key, no
    ``Bearer``) and ``Comet-Workspace``; ``projectName`` picks the project on
    both editions and falls back to Opik's "Default Project" when absent. A
    self-hosted deployment works without any of them, so every header is
    optional and omitted when nothing resolves. Values come from ``api_key`` /
    ``api_key_env`` / ``OTEL_OPIK_API_KEY`` / ``OPIK_API_KEY``, ``workspace`` /
    ``OPIK_WORKSPACE`` and ``project`` / ``project_env`` / ``OPIK_PROJECT_NAME``.

    Signals: traces only. The backend resource declares ``/traces`` alone;
    ``/metrics`` and ``/logs`` under the same prefix answer 404.
    """
    ep = (
        bc.endpoint
        or os.getenv("OTEL_OPIK_ENDPOINT", "")
        or os.getenv("OPIK_URL_OVERRIDE", "")
        or _OPIK_CLOUD
    ).strip()
    ep = _opik_traces_url(ep)

    headers: Dict[str, str] = {}
    key = _resolve_secret(bc.api_key, bc.api_key_env, ["OTEL_OPIK_API_KEY", "OPIK_API_KEY"])
    if key:
        headers["Authorization"] = key
    workspace = (bc.workspace or os.getenv("OPIK_WORKSPACE", "")).strip()
    if workspace:
        headers["Comet-Workspace"] = workspace
    project = _resolve_secret(bc.project, bc.project_env, ["OPIK_PROJECT_NAME"])
    if project:
        headers["projectName"] = project
    headers.update(bc.headers or {})

    return _ResolvedBackend(
        type="opik",
        endpoint=ep,
        display_name=_display(bc, "opik"),
        headers=headers,
    )


_LAMINAR_CLOUD = "https://api.lmnr.ai"


def _resolve_laminar(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve Laminar (cloud or self-hosted app-server, OTLP/HTTP).

    Endpoint: the app-server base, ``http://localhost:8100`` for the bundled
    stack (upstream publishes 8000; the frontend on 5667 does not ingest), or
    the cloud ``https://api.lmnr.ai``, which is the default. Also via
    ``OTEL_LAMINAR_ENDPOINT`` or the SDK's ``LMNR_BASE_URL``. A bare base gets
    ``/v1/traces``; ``/v1/logs`` is derived from it.

    Auth: a project API key is required on both editions (the key is what
    names the project) and travels as ``Authorization: Bearer <key>``, from
    ``api_key`` / ``api_key_env`` / ``OTEL_LAMINAR_API_KEY`` /
    ``LMNR_PROJECT_API_KEY``.

    Signals: traces and logs on (verified: log records are stored); metrics
    OFF by default because ``/v1/metrics`` answers 200 and drops the payload.
    """
    key = _resolve_secret(
        bc.api_key, bc.api_key_env, ["OTEL_LAMINAR_API_KEY", "LMNR_PROJECT_API_KEY"]
    )
    if not key:
        raise ValueError(
            "laminar requires api_key (or set OTEL_LAMINAR_API_KEY / LMNR_PROJECT_API_KEY)"
        )
    ep = (
        bc.endpoint
        or os.getenv("OTEL_LAMINAR_ENDPOINT", "")
        or os.getenv("LMNR_BASE_URL", "")
        or _LAMINAR_CLOUD
    ).strip()
    ep = traces_url(ep)

    headers: Dict[str, str] = {"Authorization": f"Bearer {key}"}
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="laminar",
        endpoint=ep,
        display_name=_display(bc, "laminar"),
        headers=headers,
    )


_LANGWATCH_CLOUD = "https://app.langwatch.ai"
_LANGWATCH_OTEL_PATH = "/api/otel"


def _langwatch_traces_url(endpoint: str) -> str:
    """Normalise a LangWatch ``endpoint`` to the full OTLP traces URL.

    LangWatch serves OTLP under ``/api/otel``: accept the base URL, the
    ``/api/otel`` prefix, or a full per-signal URL, and complete to
    ``<base>/api/otel/v1/traces`` (metrics and logs are derived from it).
    """
    ep = endpoint.strip().rstrip("/")
    ep = re.sub(r"/v1/(traces|metrics|logs)$", "", ep).rstrip("/")
    if not ep.endswith(_LANGWATCH_OTEL_PATH):
        ep = ep + _LANGWATCH_OTEL_PATH
    return ep + "/v1/traces"


def _resolve_langwatch(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve LangWatch (cloud or self-hosted, OTLP/HTTP, all three signals).

    Endpoint: the LangWatch base (self-host ``http://localhost:5560``, cloud
    ``https://app.langwatch.ai``, the default) or any form
    ``_langwatch_traces_url`` accepts. Also via ``OTEL_LANGWATCH_ENDPOINT`` or
    the SDK's ``LANGWATCH_ENDPOINT``.

    Auth: a project API key (``sk-lw-…``) is required and travels as
    ``Authorization: Bearer <key>`` (``api_key`` / ``api_key_env`` /
    ``OTEL_LANGWATCH_API_KEY`` / ``LANGWATCH_API_KEY``). A service key needs
    ``X-Project-Id`` as well: ``project`` / ``project_env`` /
    ``LANGWATCH_PROJECT_ID``.

    Signals: traces, metrics and logs all on (``/api/otel/v1/{traces,metrics,
    logs}`` exist and store; verified against the compose stack).
    """
    key = _resolve_secret(
        bc.api_key, bc.api_key_env, ["OTEL_LANGWATCH_API_KEY", "LANGWATCH_API_KEY"]
    )
    if not key:
        raise ValueError(
            "langwatch requires api_key (or set OTEL_LANGWATCH_API_KEY / LANGWATCH_API_KEY)"
        )
    ep = (
        bc.endpoint
        or os.getenv("OTEL_LANGWATCH_ENDPOINT", "")
        or os.getenv("LANGWATCH_ENDPOINT", "")
        or _LANGWATCH_CLOUD
    ).strip()
    ep = _langwatch_traces_url(ep)

    headers: Dict[str, str] = {"Authorization": f"Bearer {key}"}
    project = _resolve_secret(bc.project, bc.project_env, ["LANGWATCH_PROJECT_ID"])
    if project:
        headers["X-Project-Id"] = project
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="langwatch",
        endpoint=ep,
        display_name=_display(bc, "langwatch"),
        headers=headers,
    )


_LATITUDE_CLOUD = "https://ingest.latitude.so"


def _resolve_latitude(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve Latitude (cloud ingest or self-hosted ingest service, traces only).

    Endpoint: the ingest service base, ``http://localhost:3002`` for the
    bundled stack, or the cloud ``https://ingest.latitude.so`` (the default).
    Also via ``OTEL_LATITUDE_ENDPOINT`` / ``LATITUDE_INGEST_URL``. A bare base
    gets ``/v1/traces``.

    Auth and routing: ``Authorization: Bearer <api key>`` (``api_key`` /
    ``api_key_env`` / ``OTEL_LATITUDE_API_KEY`` / ``LATITUDE_API_KEY``) and the
    mandatory ``X-Latitude-Project`` header from ``project`` / ``project_env``
    / ``LATITUDE_PROJECT``: spans without a project are rejected, so both are
    required here and a missing one skips the entry at startup with a clear
    message.

    Signals: traces only (the ingest service has /v1/traces and health
    routes only).
    """
    key = _resolve_secret(bc.api_key, bc.api_key_env, ["OTEL_LATITUDE_API_KEY", "LATITUDE_API_KEY"])
    if not key:
        raise ValueError(
            "latitude requires api_key (or set OTEL_LATITUDE_API_KEY / LATITUDE_API_KEY)"
        )
    project = _resolve_secret(bc.project, bc.project_env, ["LATITUDE_PROJECT"])
    if not project:
        raise ValueError(
            "latitude requires project (the project slug for X-Latitude-Project; "
            "or set LATITUDE_PROJECT)"
        )
    ep = (
        bc.endpoint
        or os.getenv("OTEL_LATITUDE_ENDPOINT", "")
        or os.getenv("LATITUDE_INGEST_URL", "")
        or _LATITUDE_CLOUD
    ).strip()
    ep = traces_url(ep)

    headers: Dict[str, str] = {
        "Authorization": f"Bearer {key}",
        "X-Latitude-Project": project,
    }
    headers.update(bc.headers or {})
    return _ResolvedBackend(
        type="latitude",
        endpoint=ep,
        display_name=_display(bc, "latitude"),
        headers=headers,
    )


def _weave_endpoint_from_base(base_url: Optional[str]) -> str:
    """Build Weave's OTLP traces endpoint from a W&B base URL."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return _WEAVE_DEFAULT_ENDPOINT
    if base.endswith("/v1/traces"):
        return base
    if base.endswith("/otel") or base.endswith("/traces/otel"):
        return f"{base}/v1/traces"
    if "trace.wandb.ai" in base:
        return f"{base}/otel/v1/traces"
    # Dedicated Cloud / Self-Managed use the org host plus the /traces prefix.
    return f"{base}/traces/otel/v1/traces"


def _resolve_weave(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve W&B Weave's dedicated OTLP trace ingest endpoint.

    Weave authenticates trace ingest with the ``wandb-api-key`` header and
    routes spans by OTel Resource attributes: ``wandb.entity`` and
    ``wandb.project``. The latter can be supplied either on this backend entry
    (``entity`` / ``project``) or globally via ``resource_attributes``.
    """
    key = _resolve_secret(
        bc.api_key,
        bc.api_key_env,
        ["OTEL_WEAVE_API_KEY", "WANDB_API_KEY"],
    )
    if not key:
        raise ValueError("weave requires api_key (or set OTEL_WEAVE_API_KEY / WANDB_API_KEY)")

    ep = (
        bc.endpoint or os.getenv("OTEL_WEAVE_ENDPOINT", "") or os.getenv("WANDB_OTLP_ENDPOINT", "")
    ).strip()
    if not ep:
        ep = _weave_endpoint_from_base(
            bc.base_url or os.getenv("OTEL_WEAVE_BASE_URL", "") or os.getenv("WANDB_BASE_URL", "")
        )

    headers: Dict[str, str] = {"wandb-api-key": key}
    headers.update(bc.headers or {})

    resource_attrs: Dict[str, str] = {}
    entity = _resolve_secret(
        bc.entity,
        bc.entity_env,
        ["WANDB_ENTITY", "DEFAULT_WANDB_ENTITY"],
    )
    project = _resolve_secret(
        bc.project,
        bc.project_env,
        ["WANDB_PROJECT", "DEFAULT_WANDB_PROJECT"],
    )
    if entity:
        resource_attrs["wandb.entity"] = entity
    if project:
        resource_attrs["wandb.project"] = project

    return _ResolvedBackend(
        type="weave",
        endpoint=ep,
        display_name=_display(bc, "weave"),
        headers=headers,
        resource_attributes=resource_attrs or None,
    )


_RESOLVERS: Dict[str, Callable[[BackendConfig], _ResolvedBackend]] = {
    "langsmith": _resolve_langsmith,
    "phoenix": _resolve_phoenix,
    "langfuse": _resolve_langfuse,
    "signoz": _resolve_signoz,
    "jaeger": _resolve_jaeger,
    "zipkin": _resolve_zipkin,
    "greptime": _resolve_greptime,
    "clickstack": _resolve_clickstack,
    "victoria": _resolve_victoria,
    "aspire": _resolve_aspire,
    "gigapipe": _resolve_gigapipe,
    "agenta": _resolve_agenta,
    "tempo": _resolve_tempo,
    "otlp": _resolve_otlp,
    "lgtm": _resolve_lgtm,
    "uptrace": _resolve_uptrace,
    "openobserve": _resolve_openobserve,
    "parseable": _resolve_parseable,
    "honeycomb": _resolve_honeycomb,
    "weave": _resolve_weave,
    "elastic": _resolve_elastic,
    "openlit": _resolve_openlit,
    "mlflow": _resolve_mlflow,
    "opik": _resolve_opik,
    "laminar": _resolve_laminar,
    "langwatch": _resolve_langwatch,
    "latitude": _resolve_latitude,
}


# ── Public API ─────────────────────────────────────────────────────────────


#: Canonical types and the dashboard-only aliases (``hyperdx`` → ``clickstack``).
KNOWN_TYPES = SPEC_KNOWN_TYPES
assert KNOWN_TYPES >= frozenset(_RESOLVERS), sorted(frozenset(_RESOLVERS) - KNOWN_TYPES)


def display_name(backend_type: str) -> str:
    """The human name of a backend type (``signoz`` → ``SigNoz``); an alias names
    its canonical type (``hyperdx`` → ``ClickStack``)."""
    spec = spec_for(backend_type)
    if spec is not None:
        return spec.display_name
    t = (backend_type or "").strip().lower()
    return t.capitalize() or "OTLP"


def signal_support(backend_type: str) -> Dict[str, bool]:
    """Which OTLP signals a backend type accepts when the entry sets no override.

    Mirrors :func:`_traces_for`, :func:`_metrics_for` and :func:`_logs_for`:
    every type takes traces; the ``_TRACES_ONLY`` and ``_NO_METRICS`` types
    refuse metrics; only the ``_LOGS_CAPABLE`` types take logs. An explicit ``traces`` / ``metrics`` /
    ``logs`` on the entry still wins over this table, which is what the
    Settings tab uses to tell "off because unsupported" from "off by choice".
    """
    spec = spec_for(backend_type)
    if spec is None:
        return {"traces": True, "metrics": True, "logs": False}
    return {"traces": True, "metrics": spec.metrics, "logs": spec.logs}


def preset_temporality(backend_type: str) -> Optional[str]:
    """The metric temporality a type's docs ask for, if any (the spec's preset)."""
    spec = spec_for(backend_type)
    return spec.temporality if spec is not None else None


def signals_for(bc: BackendConfig) -> Dict[str, bool]:
    """Which signals this entry exports: the entry's ``traces`` / ``metrics`` /
    ``logs`` when set, else the type's support. A type whose resolver decides
    (Victoria: a signal is on only when its store is named) is asked; if that
    entry cannot resolve, the plain rule answers."""
    spec = spec_for(bc.type)
    t = spec.type if spec is not None else (bc.type or "").strip().lower()
    if spec is not None and spec.signals_from_resolver:
        try:
            rb = _RESOLVERS[t](bc)
            return {
                "traces": rb.supports_traces,
                "metrics": rb.supports_metrics,
                "logs": rb.supports_logs,
            }
        except ValueError:
            pass
    return {
        "traces": _traces_for(bc.traces),
        "metrics": _metrics_for(t, bc.metrics),
        "logs": _logs_for(t, bc.logs),
    }


_SDK_TEMPORALITY_ENV = "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"


def _entry_temporality(bc: BackendConfig, *, warn: bool = True) -> Tuple[Optional[str], str]:
    """The entry's own ``metrics_temporality`` (validated), else the type's preset."""
    t = (bc.type or "").strip().lower()
    entry = normalize_temporality(
        bc.metrics_temporality, where=f"backends[{bc.name or t}].metrics_temporality", warn=warn
    )
    if entry:
        return entry, "entry"
    preset = preset_temporality(t)
    if preset:
        return preset, "type preset"
    return None, ""


def temporality_for(bc: BackendConfig, top_level: Optional[str]) -> Tuple[str, str]:
    """``(temporality, which rule set it)`` for this backend's metric reader:
    the entry, the type preset, the top-level ``metrics_temporality``, the
    SDK's own env var, or the SDK default (``cumulative``)."""
    value, source = _entry_temporality(bc, warn=False)
    if value:
        return value, source
    if top_level:
        return str(top_level).strip().lower(), "top-level metrics_temporality"
    # With no preference passed, the OTLP exporter reads the SDK's own env var.
    env_pref = os.getenv(_SDK_TEMPORALITY_ENV, "").strip().lower()
    if env_pref:
        return env_pref, _SDK_TEMPORALITY_ENV
    return "cumulative", "SDK default"


def resolve(bc: BackendConfig) -> _ResolvedBackend:
    """Resolve a declared ``BackendConfig`` into a ready-to-wire backend.

    The resolver builds the endpoint and headers; the signals, temporality
    and root-usage presets come from the type's :class:`BackendSpec` (an
    explicit field on the entry wins). An alias (``hyperdx``) resolves as its
    canonical type. Raises :class:`ValueError` if required fields are missing
    or the backend type is unknown.
    """
    spec = spec_for(bc.type)
    if spec is None:
        raise ValueError(f"unknown backend type {bc.type!r}")
    t = spec.type
    if bc.protocol not in ("http/protobuf", "grpc"):
        raise ValueError(f"unsupported OTLP protocol {bc.protocol!r}; use http/protobuf or grpc")
    if bc.protocol == "grpc" and t != "otlp":
        raise ValueError("protocol: grpc requires a generic type: otlp backend")
    resolver = _RESOLVERS[t]
    rb = resolver(bc)
    if not spec.signals_from_resolver:
        signals = signals_for(bc)
        rb = dataclasses.replace(
            rb,
            supports_traces=signals["traces"],
            supports_metrics=signals["metrics"],
            supports_logs=signals["logs"],
        )
    temporality, _ = _entry_temporality(bc)
    if temporality and rb.metrics_temporality != temporality:
        rb = dataclasses.replace(rb, metrics_temporality=temporality)
    if bc.log_overrides:
        rb = dataclasses.replace(rb, log_overrides=dict(bc.log_overrides))
    root_usage = bc.root_usage if bc.root_usage is not None else spec.root_usage
    if rb.root_usage != root_usage:
        rb = dataclasses.replace(rb, root_usage=root_usage)
    return dataclasses.replace(
        rb,
        protocol=bc.protocol,
        insecure=bc.insecure,
        certificate_file=bc.certificate_file,
        client_key_file=bc.client_key_file,
        client_certificate_file=bc.client_certificate_file,
    )


# Views over the manifest, kept under their old names for the tests and the
# settings report: the backends that sum usage over every span (#327) and the
# ones whose docs ask for delta temporality (#233).
_NO_ROOT_USAGE_PRESETS = frozenset(s.type for s in SPECS if not s.root_usage)
_TEMPORALITY_PRESETS: Dict[str, str] = {s.type: s.temporality for s in SPECS if s.temporality}


def resolve_from_env() -> Optional[_ResolvedBackend]:
    """Try each backend in priority order; return the first one whose
    required env vars are fully satisfied. Returns ``None`` when no
    backend qualifies — the caller should then log a helpful message.
    """
    for backend_type in _ENV_PRIORITY:
        opt_in = _ENV_OPT_IN.get(backend_type)
        if opt_in and not any(os.getenv(name, "").strip() for name in opt_in):
            continue
        try:
            rb = resolve(BackendConfig(type=backend_type))
            if backend_type == "weave":
                attrs = rb.resource_attributes or {}
                if not (attrs.get("wandb.entity") and attrs.get("wandb.project")):
                    continue
            return rb
        except ValueError:
            continue
    return None
