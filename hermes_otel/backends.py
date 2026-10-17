"""Backend resolution for hermes-otel.

Converts declarative :class:`~hermes_otel.backend_config.BackendConfig`
objects (from ``config.yaml``) **or** environment variables into
ready-to-wire :class:`_ResolvedBackend` instances. Each
``_ResolvedBackend`` carries exactly what the OTLP pipeline needs:
endpoint URL, ready-to-send headers (with auth already baked in), and
the display name used in startup logs.

The module is intentionally stateless — no per-call cache, no mutation
of the plugin, no OTel SDK imports. Tracer wiring happens in
``tracer.py``; this module decides *what* to wire.

Every per-type fact (display name, signals, env opt-in, credentials and
their fallback variables, endpoint variable and default host, auth header,
temporality and root-usage presets) is the type's
:class:`~hermes_otel.backend_specs.BackendSpec`. A resolver,
``_resolve_<type>(bc, spec)``, decides only what the spec cannot say: the
URL(s) and headers (a :class:`_Wire`); :func:`resolve` builds the
``_ResolvedBackend`` from the spec and that. Plain OTLP types (an endpoint,
at most one auth header) share :func:`_resolve_plain`; ``_RESOLVERS`` holds
the ones with bespoke URL or header rules.
"""

from __future__ import annotations

import base64
import dataclasses
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .backend_config import BackendConfig
from .backend_specs import BY_TYPE, ENV_PRIORITY, SPECS, BackendSpec, spec_for
from .backend_specs import KNOWN_TYPES as SPEC_KNOWN_TYPES
from .headers_helper import HeadersHelper, headers_helper_for
from .pipeline_env import derive_signal_endpoint
from .plugin_config import normalize_temporality


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

    Env-var mode deliberately ignores these (``BackendSpec.env_opt_in``, #255);
    the caller turns the list into a one-line notice so the silence is
    explained (#259).
    """
    out: List[Tuple[str, List[str], Tuple[str, ...]]] = []
    for spec in SPECS:
        if not spec.env_opt_in or any(_set(name) for name in spec.env_opt_in):
            continue
        present: List[str] = []
        for group in spec.vendor_credentials:
            hit = next((name for name in group if _set(name)), None)
            if hit is None:
                present = []
                break
            present.append(hit)
        if present:
            out.append((spec.type, present, spec.env_opt_in))
    return out


def env_opt_in_hints() -> List[str]:
    """Human-readable version of :func:`vendor_credentials_without_opt_in`."""
    hints: List[str] = []
    for backend_type, present, opt_in in vendor_credentials_without_opt_in():
        label = BY_TYPE[backend_type].display_name
        hints.append(
            f"{' and '.join(present)} {'is' if len(present) == 1 else 'are'} set, but env-var mode "
            f"only exports to {label} when one of {', '.join(opt_in)} is also set "
            f"(or {backend_type} is listed under backends:); not exporting."
        )
    return hints


class SignalTarget(NamedTuple):
    """Where one signal's exporter sends and what it sends it with. ``headers``
    already include what ``headers_helper`` minted; the helper itself rides
    along so the exporter's session can re-mint them on a 401/403 (#483)."""

    endpoint: str
    headers: Optional[Dict[str, str]]
    headers_helper: Optional[HeadersHelper] = None


# The OTLP signals in pipeline order; ``supports_<signal>`` /
# ``<signal>_endpoint`` / ``<signal>_headers`` on :class:`_ResolvedBackend`.
SIGNALS = ("traces", "metrics", "logs")


@dataclass
class _ResolvedBackend:
    """A backend ready to wire into the OTLP pipeline.

    ``headers`` may already include backend-specific auth (e.g. Langfuse
    Basic Auth, SigNoz ingestion key); :meth:`target` merges them over the
    global ``config.headers`` when the pipeline constructs each exporter.
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
    # The entry's ``headers_helper`` (#483), shared by the three signals'
    # exporters so one process mints for all and one cache serves them.
    headers_helper: Optional[HeadersHelper] = None

    def target(self, signal: str, global_headers: Optional[Dict[str, str]] = None) -> SignalTarget:
        """The endpoint and headers to build ``signal``'s exporter with.

        Headers: the global ``headers:`` first, this backend's own on top, so
        on a conflict the backend wins (its headers carry resolver-built auth
        such as Langfuse's ``Authorization`` or ``x-honeycomb-team``, which a
        global header must never clobber), then what ``headers_helper``
        minted on top of both: the helper is the live credential (#483); its
        first run happens here, so a helper that fails skips the entry at
        init. ``metrics_headers`` / ``logs_headers`` set, even empty, replace
        ``headers`` for that signal: GreptimeDB's trace pipeline header must
        not reach the other signals (#363). ``None`` when nothing is
        configured; the pipeline then builds the exporter inside
        :func:`pipeline_env.without_otlp_header_env` so the SDK cannot fill
        the gap from the shell (#472).

        Endpoint: the per-signal URL when the backend names one (#366), else
        ``/v1/<signal>`` next to the traces URL.
        """
        own = self.headers
        endpoint = self.endpoint
        if signal != "traces":
            per_signal = getattr(self, f"{signal}_headers")
            own = per_signal if per_signal is not None else own
            endpoint = getattr(self, f"{signal}_endpoint") or derive_signal_endpoint(
                self.endpoint, signal
            )
        merged: Dict[str, str] = dict(global_headers or {})
        merged.update(own or {})
        if self.headers_helper is not None:
            merged.update(self.headers_helper.headers())
        return SignalTarget(endpoint, merged or None, self.headers_helper)

    def enabled_signals(self) -> Tuple[str, ...]:
        """The signals this backend receives, in :data:`SIGNALS` order."""
        return tuple(s for s in SIGNALS if getattr(self, f"supports_{s}"))

    def describe_signals(self) -> str:
        """The startup banner's suffix, leading space included: ``" (query
        only)"`` for a backend that exports nothing, ``" (traces only)"`` /
        ``" (traces + logs)"`` when metrics are off, ``""`` when they are on."""
        if not self.supports_traces:
            return " (query only)"
        if self.supports_metrics:
            return ""
        return " (traces + logs)" if self.supports_logs else " (traces only)"


@dataclass
class _Wire:
    """What a resolver decides for one entry: where each signal goes and the
    headers it carries. :func:`resolve` adds everything the manifest knows."""

    endpoint: str
    headers: Optional[Dict[str, str]] = None
    metrics_headers: Optional[Dict[str, str]] = None
    logs_headers: Optional[Dict[str, str]] = None
    metrics_endpoint: Optional[str] = None
    logs_endpoint: Optional[str] = None
    resource_attributes: Optional[Dict[str, str]] = None


# ── Shared helpers ─────────────────────────────────────────────────────────


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


def _fallbacks(spec: BackendSpec, field: str) -> Tuple[str, ...]:
    return next((c.env for c in spec.credentials if c.field == field), ())


def _secret(bc: BackendConfig, spec: BackendSpec, field: str) -> Optional[str]:
    """``bc.<field>``, else the variable ``bc.<field>_env`` names, else the
    spec's fallback variables for the field (``BackendSpec.credentials``)."""
    return choose_secret(
        getattr(bc, field), getattr(bc, f"{field}_env", None), _fallbacks(spec, field)
    ).value


def _basic_auth(bc: BackendConfig, spec: BackendSpec) -> Optional[str]:
    """``Basic <base64 user:password>`` when both resolve, else ``None``."""
    user = _secret(bc, spec, "user")
    pw = _secret(bc, spec, "password")
    if user and pw:
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    return None


def _endpoint(bc: BackendConfig, spec: BackendSpec) -> str:
    """The entry's ``endpoint``, else ``spec.endpoint_env`` and the vendor
    fallbacks, else the host for ``region`` when the spec has several, else
    ``spec.default_endpoint``; ``""`` when nothing names one. An unknown
    region is an error unless the spec has a default host for it."""
    ep = bc.endpoint or ""
    names = ((spec.endpoint_env,) if spec.endpoint_env else ()) + spec.endpoint_env_fallbacks
    for name in names:
        ep = ep or os.getenv(name, "")
    if not ep and spec.region_endpoints:
        region = (bc.region or "us").strip().lower()
        ep = spec.region_endpoints.get(region) or spec.default_endpoint or ""
        if not ep:
            raise ValueError(
                f"{spec.type} region must be one of {sorted(spec.region_endpoints)}, "
                f"got {bc.region!r}"
            )
    return (ep or spec.default_endpoint or "").strip()


def _required_endpoint(bc: BackendConfig, spec: BackendSpec) -> str:
    ep = _endpoint(bc, spec)
    if not ep:
        hint = f" (or set {spec.endpoint_env})" if spec.endpoint_env else ""
        raise ValueError(f"{spec.type} requires endpoint{hint}")
    return ep


def _auth_header(bc: BackendConfig, spec: BackendSpec) -> Dict[str, str]:
    """The one header ``spec.auth`` describes, or ``{}`` when it is optional
    and nothing resolves."""
    auth = spec.auth
    if auth is None:
        return {}
    if auth.scheme == "Basic":
        value = _basic_auth(bc, spec)
    else:
        value = _secret(bc, spec, auth.field)
        if value and auth.scheme:
            value = f"{auth.scheme} {value}"
    if value:
        return {auth.name: value}
    if auth.required:
        names = " / ".join(_fallbacks(spec, auth.field))
        raise ValueError(
            f"{spec.type} requires {auth.field} / {auth.field}_env"
            + (f" (or set {names})" if names else "")
        )
    return {}


# ── Per-backend resolvers ──────────────────────────────────────────────────


def _resolve_plain(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """A plain OTLP/HTTP type: the endpoint (required unless the spec has a
    default host), normalised to ``/v1/traces`` when the spec says so, and the
    one auth header the spec declares; the entry's ``headers`` win."""
    ep = _required_endpoint(bc, spec)
    if spec.normalize_endpoint:
        ep = traces_url(ep)
    headers = _auth_header(bc, spec)
    headers.update(bc.headers or {})
    return _Wire(ep, headers or None)


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


def _resolve_langfuse(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    pub = _secret(bc, spec, "public_key")
    sec = _secret(bc, spec, "secret_key")
    if not (pub and sec):
        raise ValueError("langfuse requires public_key and secret_key")
    ep = _endpoint(bc, spec)
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
    return _Wire(ep, headers)


# GreptimeDB's OTLP prefix and the pipeline its trace ingest requires.
_GREPTIME_OTLP_PREFIX = "/v1/otlp"
_GREPTIME_TRACE_PIPELINE = "greptime_trace_v1"


def _resolve_greptime(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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
    ep = _required_endpoint(bc, spec).rstrip("/")
    ep = re.sub(r"/v1/(traces|metrics|logs)$", "", ep).rstrip("/")
    if ep.endswith(_GREPTIME_OTLP_PREFIX):
        ep = ep[: -len(_GREPTIME_OTLP_PREFIX)].rstrip("/")
    ep = f"{ep}{_GREPTIME_OTLP_PREFIX}/v1/traces"

    common: Dict[str, str] = {}
    db = _secret(bc, spec, "db")
    if db:
        common["X-Greptime-DB-Name"] = db
    basic = _basic_auth(bc, spec)
    if basic:
        common["Authorization"] = basic
    common.update(bc.headers or {})
    trace_headers = {"X-Greptime-Pipeline-Name": _GREPTIME_TRACE_PIPELINE, **common}
    # A user-supplied pipeline header (another trace model, e.g. v2) wins.
    if bc.headers and "X-Greptime-Pipeline-Name" in bc.headers:
        trace_headers["X-Greptime-Pipeline-Name"] = bc.headers["X-Greptime-Pipeline-Name"]
    return _Wire(ep, trace_headers, metrics_headers=dict(common), logs_headers=dict(common))


_VICTORIA_TRACES_PATH = "/insert/opentelemetry/v1/traces"
_VICTORIA_METRICS_PATH = "/opentelemetry/v1/metrics"
_VICTORIA_LOGS_PATH = "/insert/opentelemetry/v1/logs"


def _victoria_url(raw: str, path: str) -> str:
    """An origin, or the full ingest URL, of one Victoria store → that store's
    OTLP URL. Any ``/insert/opentelemetry/v1/<signal>``, ``/opentelemetry/v1/<signal>``
    or ``/v1/<signal>`` tail is replaced by the store's own path."""
    base = re.sub(r"(/insert)?(/opentelemetry)?/v1/(traces|metrics|logs)$", "", raw.rstrip("/"))
    return f"{base.rstrip('/')}{path}"


def _resolve_victoria(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """Resolve the VictoriaMetrics family: VictoriaTraces, VictoriaMetrics and
    VictoriaLogs, three single-binary stores with one OTLP/HTTP route each.

    ``endpoint`` is VictoriaTraces (``:10428``, traces land on
    ``/insert/opentelemetry/v1/traces``); ``metrics_endpoint`` is
    VictoriaMetrics (``:8428``, ``/opentelemetry/v1/metrics``) and
    ``logs_endpoint`` VictoriaLogs (``:9428``, ``/insert/opentelemetry/v1/logs``).
    Each takes an origin or the full URL, also via ``OTEL_VICTORIA_ENDPOINT``,
    ``OTEL_VICTORIA_METRICS_ENDPOINT`` and ``OTEL_VICTORIA_LOGS_ENDPOINT``. The
    three stores are separate processes, so a signal whose store is not named
    is simply off: there is no host to derive it on (``resolve()`` reads that
    from the per-signal endpoints, ``signals_from_resolver``).

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
    ep = _endpoint(bc, spec)
    if not ep:
        raise ValueError(
            "victoria requires endpoint (VictoriaTraces; or set OTEL_VICTORIA_ENDPOINT)"
        )
    traces = _victoria_url(ep, _VICTORIA_TRACES_PATH)
    m_ep = (bc.metrics_endpoint or os.getenv(spec.metrics_endpoint_env or "", "")).strip()
    l_ep = (bc.logs_endpoint or os.getenv(spec.logs_endpoint_env or "", "")).strip()
    metrics_url = _victoria_url(m_ep, _VICTORIA_METRICS_PATH) if m_ep else None
    logs_url = _victoria_url(l_ep, _VICTORIA_LOGS_PATH) if l_ep else None

    if bc.metrics is True and not metrics_url:
        raise ValueError("victoria metrics need metrics_endpoint (VictoriaMetrics)")
    if bc.logs is True and not logs_url:
        raise ValueError("victoria logs need logs_endpoint (VictoriaLogs)")

    common: Dict[str, str] = {}
    basic = _basic_auth(bc, spec)
    if basic:
        common["Authorization"] = basic
    common.update(bc.headers or {})
    log_headers = {"VL-Stream-Fields": "service.name", **common}
    return _Wire(
        traces,
        dict(common),
        metrics_headers=dict(common),
        logs_headers=log_headers,
        metrics_endpoint=metrics_url,
        logs_endpoint=logs_url,
    )


_AGENTA_OTLP_PATH = "/api/otlp/v1/traces"


def _resolve_agenta(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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

    Signals: traces only.
    """
    ep = re.sub(r"(/api)?(/otlp)?(/v1/traces)?/?$", "", _endpoint(bc, spec).rstrip("/"))
    ep = f"{ep}{_AGENTA_OTLP_PATH}"
    key = _secret(bc, spec, "api_key")
    if not key:
        raise ValueError(
            "agenta requires api_key / api_key_env (a project API key; or set OTEL_AGENTA_API_KEY)"
        )
    headers: Dict[str, str] = {"Authorization": f"ApiKey {key}"}
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


def openobserve_traces_url(endpoint: str, org: str) -> str:
    """The OpenObserve traces URL for ``endpoint``: a bare origin (empty or
    ``/`` path) becomes ``<origin>/api/<org>/v1/traces``, since the exporter
    posts to exactly the URL it is given and the origin answers 405 (#582);
    any URL with a path is kept as given."""
    ep = endpoint.strip()
    parts = urllib.parse.urlsplit(ep)
    if parts.path.strip("/"):
        return ep
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, f"/api/{org}/v1/traces", parts.query, "")
    )


def _resolve_openobserve(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """Resolve OpenObserve (all-in-one traces/metrics/logs backend).

    Auth model: HTTP Basic using the admin email + password (or any user
    created in the UI), plus an optional ``stream-name`` header that
    routes ingested data into a named stream (defaults to ``default``).
    The endpoint URL embeds the org in its path, e.g.
    ``http://localhost:5080/api/default/v1/traces``; a bare origin is
    completed with ``org`` (``OTEL_OPENOBSERVE_ORG``, default ``default``).
    """
    org = (bc.org or os.getenv("OTEL_OPENOBSERVE_ORG", "") or "default").strip()
    ep = openobserve_traces_url(_required_endpoint(bc, spec), org)
    basic = _basic_auth(bc, spec)
    if not basic:
        raise ValueError("openobserve requires user and password")
    stream = (bc.stream_name or os.getenv("OTEL_OPENOBSERVE_STREAM", "") or "default").strip()
    headers: Dict[str, str] = {"Authorization": basic, "stream-name": stream}
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


def _resolve_parseable(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """Resolve Parseable direct OTLP/HTTP ingest for all three signals."""
    ep = _required_endpoint(bc, spec)
    key = _secret(bc, spec, "api_key")
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
    return _Wire(ep, trace_headers, metrics_headers=metric_headers, logs_headers=log_headers)


def _resolve_honeycomb(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """Resolve Honeycomb (SaaS, OTLP/HTTP — traces + metrics + logs).

    Auth model: the API key travels in the ``x-honeycomb-team`` header. An
    optional ``dataset`` is sent as ``x-honeycomb-dataset``; ``region``
    (``us``|``eu``) selects the base endpoint when one isn't given explicitly.

    Endpoint: defaults to the US ingest host (or EU for ``region: eu``) with the
    ``/v1/traces`` suffix the OTLP pipeline expects (``spec.region_endpoints``).
    ``tracer.py`` and ``log_handler.py`` rewrite that suffix to ``/v1/metrics``
    and ``/v1/logs``, which matches Honeycomb's per-signal path scheme exactly.

    Dataset routing: ``x-honeycomb-dataset`` is honored only by Honeycomb
    Classic keys (where it's required for every signal). Modern "Environments"
    keys ignore it — traces route by ``service.name`` and metrics go to the
    environment's default ``Metrics`` dataset (verified live; see
    ``HONEYCOMB.md``). The plugin sends ``dataset`` on all three exporters via
    one merged header set, which is correct for Classic and a harmless no-op for
    modern keys; leave ``dataset`` unset on a modern key.
    """
    key = _secret(bc, spec, "api_key")
    if not key:
        raise ValueError(
            "honeycomb requires api_key (or set OTEL_HONEYCOMB_API_KEY / HONEYCOMB_API_KEY)"
        )
    ep = _endpoint(bc, spec)
    headers: Dict[str, str] = {"x-honeycomb-team": key}
    dataset = (bc.dataset or "").strip()
    if dataset:
        headers["x-honeycomb-dataset"] = dataset
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


def _langsmith_traces_url(ep: str) -> str:
    """``.../otel/v1/traces`` from an API base, an ``/otel`` base or a full URL.

    ``LANGSMITH_ENDPOINT`` is the SDK's API base (``https://api.smith.langchain.com``);
    the OTLP routes live under ``/otel`` on the same host."""
    base = re.sub(r"/v1/(traces|metrics|logs)$", "", ep.strip().rstrip("/")).rstrip("/")
    if not base.endswith("/otel"):
        base = f"{base}/otel"
    return f"{base}/v1/traces"


def _resolve_langsmith(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """Resolve LangSmith (SaaS, OTLP/HTTP — traces only).

    LangSmith accepts OTLP traces at ``<api base>/otel/v1/traces`` with the
    API key in ``x-api-key`` and the target project in ``Langsmith-Project``.
    Until 1.26 the plugin drove LangSmith's run-tree HTTP API instead, selected
    by ``LANGSMITH_TRACING=true`` and outside the ``backends:`` fan-out; that
    path is gone (#415) and ``LANGSMITH_TRACING`` selects nothing. The vendor
    variables (``LANGSMITH_API_KEY``, ``LANGSMITH_PROJECT``,
    ``LANGSMITH_ENDPOINT``) still fill in values once the entry or the
    ``OTEL_LANGSMITH_*`` opt-in names the backend (#255). ``region`` (``us``
    default, ``eu``) picks the host when no endpoint is given.
    """
    key = _secret(bc, spec, "api_key")
    if not key:
        raise ValueError(
            "langsmith requires api_key (or set OTEL_LANGSMITH_API_KEY / LANGSMITH_API_KEY)"
        )
    ep = _langsmith_traces_url(_endpoint(bc, spec))
    headers: Dict[str, str] = {"x-api-key": key}
    project = _secret(bc, spec, "project")
    if project:
        headers["Langsmith-Project"] = project
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


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


def _resolve_elastic(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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
    ``/v1/logs`` variants from it. Endpoint and header are the plain rules
    (``spec.normalize_endpoint`` / ``spec.auth``).

    Data streams: optional ``dataset`` / ``namespace`` fields are copied to
    the ``data_stream.dataset`` / ``data_stream.namespace`` Resource
    attributes, which Elasticsearch data-stream routing keys off. When unset,
    Elastic defaults the dataset per signal (``traces``, ``metrics``,
    ``logs``). Both attributes land on the plugin's single shared Resource,
    so every configured backend receives them; two elastic entries with
    different values conflict at init.
    """
    wire = _resolve_plain(bc, spec)
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
    return dataclasses.replace(wire, resource_attributes=resource_attrs or None)


def _resolve_mlflow(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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
    ep = traces_url(_required_endpoint(bc, spec))
    experiment = _secret(bc, spec, "experiment_id")
    headers: Dict[str, str] = {"x-mlflow-experiment-id": str(experiment or "0").strip()}
    workspace = (bc.workspace or "").strip()
    if workspace:
        headers["X-MLFLOW-WORKSPACE"] = workspace
    token = _secret(bc, spec, "api_key")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


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


def _resolve_opik(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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
    ep = _opik_traces_url(_endpoint(bc, spec))
    headers: Dict[str, str] = {}
    key = _secret(bc, spec, "api_key")
    if key:
        headers["Authorization"] = key
    workspace = _secret(bc, spec, "workspace")
    if workspace:
        headers["Comet-Workspace"] = workspace
    project = _secret(bc, spec, "project")
    if project:
        headers["projectName"] = project
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


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


def _resolve_langwatch(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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
    key = _secret(bc, spec, "api_key")
    if not key:
        raise ValueError(
            "langwatch requires api_key (or set OTEL_LANGWATCH_API_KEY / LANGWATCH_API_KEY)"
        )
    ep = _langwatch_traces_url(_endpoint(bc, spec))
    headers: Dict[str, str] = {"Authorization": f"Bearer {key}"}
    project = _secret(bc, spec, "project")
    if project:
        headers["X-Project-Id"] = project
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


def _resolve_latitude(bc: BackendConfig, spec: BackendSpec) -> _Wire:
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
    key = _secret(bc, spec, "api_key")
    if not key:
        raise ValueError(
            "latitude requires api_key (or set OTEL_LATITUDE_API_KEY / LATITUDE_API_KEY)"
        )
    project = _secret(bc, spec, "project")
    if not project:
        raise ValueError(
            "latitude requires project (the project slug for X-Latitude-Project; "
            "or set LATITUDE_PROJECT)"
        )
    ep = traces_url(_endpoint(bc, spec))
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {key}",
        "X-Latitude-Project": project,
    }
    headers.update(bc.headers or {})
    return _Wire(ep, headers)


_WEAVE_DEFAULT_ENDPOINT = "https://trace.wandb.ai/otel/v1/traces"


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


def _resolve_weave(bc: BackendConfig, spec: BackendSpec) -> _Wire:
    """Resolve W&B Weave's dedicated OTLP trace ingest endpoint.

    Weave authenticates trace ingest with the ``wandb-api-key`` header and
    routes spans by OTel Resource attributes: ``wandb.entity`` and
    ``wandb.project``. The latter can be supplied either on this backend entry
    (``entity`` / ``project``) or globally via ``resource_attributes``.
    """
    key = _secret(bc, spec, "api_key")
    if not key:
        raise ValueError("weave requires api_key (or set OTEL_WEAVE_API_KEY / WANDB_API_KEY)")
    ep = _endpoint(bc, spec)
    if not ep:
        ep = _weave_endpoint_from_base(
            bc.base_url or os.getenv("OTEL_WEAVE_BASE_URL", "") or os.getenv("WANDB_BASE_URL", "")
        )
    headers: Dict[str, str] = {"wandb-api-key": key}
    headers.update(bc.headers or {})

    resource_attrs: Dict[str, str] = {}
    entity = _secret(bc, spec, "entity")
    project = _secret(bc, spec, "project")
    if entity:
        resource_attrs["wandb.entity"] = entity
    if project:
        resource_attrs["wandb.project"] = project
    return _Wire(ep, headers, resource_attributes=resource_attrs or None)


#: Types with bespoke URL or header rules; every other type is ``_resolve_plain``.
_RESOLVERS: Dict[str, Callable[[BackendConfig, BackendSpec], _Wire]] = {
    "langsmith": _resolve_langsmith,
    "langfuse": _resolve_langfuse,
    "greptime": _resolve_greptime,
    "victoria": _resolve_victoria,
    "agenta": _resolve_agenta,
    "openobserve": _resolve_openobserve,
    "parseable": _resolve_parseable,
    "honeycomb": _resolve_honeycomb,
    "weave": _resolve_weave,
    "elastic": _resolve_elastic,
    "mlflow": _resolve_mlflow,
    "opik": _resolve_opik,
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
    """Which OTLP signals a backend type accepts when the entry sets no override:
    every type takes traces; ``metrics`` and ``logs`` are the spec's. An explicit
    ``traces`` / ``metrics`` / ``logs`` on the entry still wins over this table,
    which is what the Settings tab uses to tell "off because unsupported" from
    "off by choice".
    """
    spec = spec_for(backend_type)
    if spec is None:
        return {"traces": True, "metrics": True, "logs": False}
    return {"traces": True, "metrics": spec.metrics, "logs": spec.logs}


def _signals(
    bc: BackendConfig, spec: Optional[BackendSpec], wire: Optional[_Wire]
) -> Dict[str, bool]:
    """The entry's ``traces`` / ``metrics`` / ``logs`` when set, else the type's
    support. For a ``signals_from_resolver`` type a signal is also off when the
    resolver named no endpoint for it (Victoria: the store is not configured)."""
    support = signal_support(spec.type if spec is not None else bc.type or "")
    metrics = support["metrics"] if bc.metrics is None else bc.metrics
    logs = support["logs"] if bc.logs is None else bc.logs
    if wire is not None and spec is not None and spec.signals_from_resolver:
        metrics = metrics and wire.metrics_endpoint is not None
        logs = logs and wire.logs_endpoint is not None
    # Traces are the primary signal for every backend. ``False`` is opt-in for
    # query-only/dashboard-only entries that should not receive span exports
    # (for example, querying Tempo directly while exporting through a Collector).
    return {"traces": True if bc.traces is None else bc.traces, "metrics": metrics, "logs": logs}


def signals_for(bc: BackendConfig) -> Dict[str, bool]:
    """Which signals this entry exports: the entry's ``traces`` / ``metrics`` /
    ``logs`` when set, else the type's support. A type whose resolver decides
    (Victoria: a signal is on only when its store is named) is asked; if that
    entry cannot resolve, the plain rule answers."""
    spec = spec_for(bc.type)
    wire = None
    if spec is not None and spec.signals_from_resolver:
        try:
            wire = _RESOLVERS.get(spec.type, _resolve_plain)(bc, spec)
        except ValueError:
            pass
    return _signals(bc, spec, wire)


_SDK_TEMPORALITY_ENV = "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"


def _entry_temporality(bc: BackendConfig, *, warn: bool = True) -> Tuple[Optional[str], str]:
    """The entry's own ``metrics_temporality`` (validated), else the type's preset."""
    t = (bc.type or "").strip().lower()
    entry = normalize_temporality(
        bc.metrics_temporality, where=f"backends[{bc.name or t}].metrics_temporality", warn=warn
    )
    if entry:
        return entry, "entry"
    spec = spec_for(t)
    if spec is not None and spec.temporality:
        return spec.temporality, "type preset"
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

    The resolver builds the endpoint and headers; the display name, signals,
    temporality and root-usage presets come from the type's
    :class:`BackendSpec` (an explicit field on the entry wins). An alias
    (``hyperdx``) resolves as its canonical type. Raises :class:`ValueError`
    if required fields are missing or the backend type is unknown.
    """
    spec = spec_for(bc.type)
    if spec is None:
        raise ValueError(f"unknown backend type {bc.type!r}")
    wire = _RESOLVERS.get(spec.type, _resolve_plain)(bc, spec)
    signals = _signals(bc, spec, wire)
    return _ResolvedBackend(
        type=spec.type,
        endpoint=wire.endpoint,
        display_name=bc.name or spec.display_name,
        headers=wire.headers,
        metrics_headers=wire.metrics_headers,
        logs_headers=wire.logs_headers,
        metrics_endpoint=wire.metrics_endpoint,
        logs_endpoint=wire.logs_endpoint,
        supports_traces=signals["traces"],
        supports_metrics=signals["metrics"],
        supports_logs=signals["logs"],
        resource_attributes=wire.resource_attributes,
        log_overrides=dict(bc.log_overrides) if bc.log_overrides else None,
        metrics_temporality=_entry_temporality(bc)[0],
        root_usage=bc.root_usage if bc.root_usage is not None else spec.root_usage,
        headers_helper=headers_helper_for(bc),
    )


def resolve_from_env() -> Optional[_ResolvedBackend]:
    """Try each backend in ``ENV_PRIORITY`` order; return the first one whose
    required env vars are fully satisfied (a type with an ``env_opt_in`` needs
    one of those variables set first, #255). Returns ``None`` when no backend
    qualifies — the caller should then log a helpful message.
    """
    for backend_type in ENV_PRIORITY:
        spec = BY_TYPE[backend_type]
        if spec.env_opt_in and not any(_set(name) for name in spec.env_opt_in):
            continue
        try:
            rb = resolve(BackendConfig(type=backend_type))
        except ValueError:
            continue
        # No ``resource_attributes`` overlay in env-var mode: a type that routes
        # by resource attributes must find them in the environment too.
        if not spec.missing_resource_attributes(rb.resource_attributes or {}):
            return rb
    return None
