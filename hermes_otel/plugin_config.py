"""Declarative configuration for hermes-otel.

Loader precedence per field: env var > config.yaml > default.

The config file itself is resolved by :func:`resolve_config_path`:
``HERMES_OTEL_CONFIG`` > ``$HERMES_HOME/hermes_otel.yaml`` >
``$HERMES_HOME/plugins/hermes_otel/config.yaml``. Prefer one of the first
two: the plugin directory is replaced wholesale on reinstall, so a config
kept there does not survive an upgrade.

Two ways to pick backends:
  * **Multi-backend** (preferred): set ``backends:`` in config.yaml. Every entry
    fans out via its own ``BatchSpanProcessor`` so traces land in all
    configured collectors in parallel without blocking the agent thread.
  * **Single-backend (legacy)**: set one of the ``OTEL_*_ENDPOINT`` env vars
    or LangSmith/Langfuse credentials. When ``backends:`` is empty, env-var
    detection is used and at most one backend is selected: LangSmith first,
    then the order of ``backends._ENV_PRIORITY``.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .debug_utils import logger

Scalar = Union[str, int, float, bool]


def hermes_home() -> Path:
    """The Hermes home this plugin instance belongs to.

    Hermes's scope-aware resolver when Hermes is importable (so each profile
    of a multiplexed gateway gets its own home, #70), else ``$HERMES_HOME``
    or ``~/.hermes``. See :mod:`hermes_otel.hermes_home`.
    """
    from .hermes_home import resolve_hermes_home

    return resolve_hermes_home()


# Environment variable holding an explicit config file path.
CONFIG_PATH_ENV = "HERMES_OTEL_CONFIG"

# Outside the plugin directory, so it survives `hermes plugins install --force`
# (which replaces the plugin directory wholesale — see issue #55).
DURABLE_CONFIG_PATH = hermes_home() / "hermes_otel.yaml"

# The historical location: inside the plugin directory. Still read, so existing
# installs keep working, but it is wiped by a reinstall.
DEFAULT_CONFIG_PATH = hermes_home() / "plugins" / "hermes_otel" / "config.yaml"


def resolve_config_path() -> Optional[Path]:
    """Return the config file to read, or ``None`` when there is none.

    Order: ``HERMES_OTEL_CONFIG`` (even if missing — an explicit path that does
    not exist is worth surfacing), then the durable location, then the legacy
    plugin-directory copy. Module-level path constants are read at call time so
    tests can redirect them.
    """
    override = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()

    durable, legacy = DURABLE_CONFIG_PATH, DEFAULT_CONFIG_PATH
    if durable.exists():
        if legacy.exists():
            logger.warning(
                f"[hermes-otel] using {durable}; ignoring the copy in the plugin "
                f"directory ({legacy}). Delete the latter to silence this."
            )
        return durable
    if legacy.exists():
        return legacy
    return None


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

    type: str  # phoenix | langfuse | signoz | jaeger | tempo | otlp | parseable | honeycomb | weave
    name: Optional[str] = None  # display name (defaults to type)
    endpoint: Optional[str] = None  # OTLP HTTP traces URL
    # Where this backend's web UI lives, for the dashboard's Settings tab to
    # link to. Optional: when unset the tab derives it from ``endpoint`` for
    # the types whose UI shares the OTLP origin (Phoenix, Langfuse, ...).
    ui_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None  # extra/override HTTP headers
    traces: Optional[bool] = None  # None = on. False = dashboard/query-only, no trace export.
    metrics: Optional[bool] = None  # None = auto (off for langfuse/jaeger/tempo)
    metrics_temporality: Optional[str] = None  # cumulative | delta; None = type preset / top-level
    logs: Optional[bool] = (
        None  # None = auto (on for signoz/otlp/lgtm/uptrace/openobserve/parseable/honeycomb)
    )
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
    # W&B Weave routing. Weave authenticates with ``api_key`` and routes spans
    # by OTel Resource attributes. These fields are copied to ``wandb.entity``
    # / ``wandb.project``; users can alternatively set them in top-level
    # ``resource_attributes``.
    entity: Optional[str] = None
    entity_env: Optional[str] = None
    project: Optional[str] = None
    project_env: Optional[str] = None


@dataclass(frozen=True)
class HermesOtelConfig:
    """Frozen configuration object passed through the plugin."""

    enabled: bool = True
    sample_rate: Optional[float] = None  # None = AlwaysOn. 0..1 = ratio.
    root_span_ttl_ms: int = 600_000  # 10 min orphan sweep threshold
    flush_interval_ms: int = 60_000  # metrics export interval
    preview_max_chars: int = 1200  # global clip_preview truncation fallback
    capture_previews: bool = True  # global privacy kill switch
    # Per-category overrides — when set, each takes precedence over preview_max_chars
    # for its specific span type. None = fall back to preview_max_chars.
    tool_input_preview_max_chars: Optional[int] = None
    tool_output_preview_max_chars: Optional[int] = None
    llm_input_preview_max_chars: Optional[int] = None
    llm_output_preview_max_chars: Optional[int] = None
    headers: Optional[Dict[str, str]] = None  # extra OTLP headers (all backends)
    global_tags: Optional[Dict[str, Scalar]] = None
    resource_attributes: Optional[Dict[str, Scalar]] = None
    project_name: Optional[str] = None  # supersedes OTEL_PROJECT_NAME
    # ── BatchSpanProcessor tunables (Phase 2: non-blocking export) ──────
    span_batch_max_queue_size: int = 2048  # spans buffered before drops
    span_batch_schedule_delay_ms: int = 1000  # worker wake-up cadence
    # None = auto: 512, or 64 when content_capture is "full" (large spans
    # would otherwise make a single OTLP POST bigger than most receivers
    # accept). See effective_export_batch_size().
    span_batch_max_export_batch_size: Optional[int] = None
    span_batch_export_timeout_ms: int = 30_000  # per-export HTTP timeout
    force_flush_on_session_end: bool = True  # flush so UI sees traces promptly
    # How long the hook thread waits for that background flush to finish
    # before the turn returns to Hermes, in ms. Bounded and coalesced: a
    # healthy backend receives the turn in well under it, a stuck one costs at
    # most this much per turn. 0 = do not wait. A one-shot ``hermes -z`` exits
    # right after the turn, so the wait is what gets its spans out.
    # 1500 since #233: the flush now includes the metric and log providers,
    # and a one-shot `hermes -z` exits the moment this wait returns.
    force_flush_wait_ms: int = 1500
    # ── LLM span input fidelity ─────────────────────────────────────────
    # Opt-in: serialise the full conversation_history onto the llm span's
    # input.value so the UI shows every message instead of just the last
    # user turn. The api.* spans don't carry message-level detail, so
    # flipping this on is the easiest way to see what the model actually saw.
    capture_conversation_history: bool = False
    conversation_history_max_chars: int = 20_000
    # ── Content capture ─────────────────────────────────────────────────
    # What of the conversation content is recorded on spans:
    #   "full"     the complete prompt (system prompt + every message) and
    #              the complete response on every api.* span, unclipped;
    #              previews elsewhere. The default: this is a debugging tool
    #              and the data goes only to backends you configure.
    #   "preview"  clipped previews only (``preview_max_chars``).
    #   "off"      no prompt, tool or response content at all; metadata only.
    # ``capture_previews`` / ``capture_full_prompts`` / ``capture_full_responses``
    # are the pre-1.11 spellings; load_config keeps them consistent with
    # ``content_capture`` (see _reconcile_content_capture) and hook code reads
    # the booleans. ``capture_previews: false`` still means "off".
    content_capture: str = "full"
    capture_full_prompts: bool = True
    capture_full_responses: bool = True
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
    # ── OTel GenAI semantic-convention metrics ──────────────────────────
    # Emit spec-named instruments (gen_ai.client.*, gen_ai.agent.*) in
    # addition to the custom hermes.* metrics, so generic OTel-GenAI
    # dashboards/alerts work out of the box. Set false for hermes.* only.
    emit_genai_metrics: bool = True
    # Metric aggregation temporality sent to OTLP backends (#233). ``None`` =
    # auto: the OTel SDK default (``OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE``
    # when set, else cumulative). ``delta`` for Datadog-style backends. A
    # backend entry's own ``metrics_temporality`` wins over this default, and
    # backend types with a known preference (SigNoz, Uptrace) default to it.
    metrics_temporality: Optional[str] = None
    # Histogram aggregation: ``explicit`` (spec bucket boundaries per instrument)
    # or ``exponential`` (base-2 exponential histograms; needs a backend that
    # accepts them, e.g. Prometheus native histograms, Datadog, New Relic).
    metrics_histogram: str = "explicit"
    # Distinct values a label such as ``model`` or ``tool_name`` may take per
    # process before further values are folded into ``other`` (the Python SDK
    # has no cardinality limit of its own). ``0`` disables the cap.
    metrics_label_limit: int = 100
    # ── Skill execution-window spans ────────────────────────────────────
    # Emit a skill:<name> span when the agent loads a skill (via skill_view
    # or a /skills/ path), spanning from load to the turn boundary. Skills
    # overlap freely. Set false to keep only the hermes.skill.* attribute and
    # the skill_inferred counter.
    skill_spans: bool = True
    # ── Telemetry discovery prompt (opt-in) ─────────────────────────────
    # Register a short system-prompt section (via Hermes'
    # register_system_prompt_section) telling the model that telemetry is
    # available and to load the bundled hermes_otel:observability skill for
    # questions about past behaviour. It changes what the model sees on every
    # turn (~80 tokens), so it is off unless you opt in.
    discovery_prompt: bool = False
    # ── Live dashboard (local store) ────────────────────────────────────
    # Keep a bounded window of recent spans + metrics + logs in a small SQLite
    # file ($HERMES_HOME/hermes_otel_live.db, or HERMES_OTEL_LIVE_DB) so the
    # built-in dashboard's "Live" mode works with NO external backend and the
    # gateway and dashboard processes see the same data. Each kind keeps its
    # last dashboard_live_max_spans rows. Set false to disable it entirely.
    dashboard_live: bool = True
    dashboard_live_max_spans: int = 1000
    # Rows older than this are dropped from the live store as well (0 = keep
    # until the row cap evicts them). The store is a recent-activity buffer for
    # the dashboard, not a record: configure a backend for history (#184).
    dashboard_live_retention_hours: float = 168.0
    # ── Host metrics (CPU / GPU) ────────────────────────────────────────
    # Sample the Hermes process tree, the whole host, and any AMD/NVIDIA GPU
    # on a fixed interval and export the readings as OTel metrics
    # (process.cpu.utilization, system.cpu.utilization, hw.gpu.*, hw.power) on
    # every backend that receives metrics, plus per-tool CPU/GPU utilization
    # attributes on tool spans. Off by default. Needs psutil (ships with
    # hermes-agent); GPU readings additionally need pynvml or amdsmi.
    host_metrics: bool = False
    host_metrics_gpu: str = "auto"  # auto | amd | nvidia | off
    host_metrics_interval_ms: int = 1000
    # ── MCP keepalive noise ─────────────────────────────────────────────
    # MCP Python SDK 2.x (Hermes v0.21.0+) emits a CLIENT span for every
    # JSON-RPC request, including the periodic keepalive ``ping`` Hermes sends
    # per MCP connection. Each surfaces as a standalone one-span
    # "MCP send ping" trace. True (default) drops the successful ones before
    # they reach any exporter or the live store; failed pings are always kept.
    suppress_mcp_ping_spans: bool = True
    # ── Multi-backend fan-out ───────────────────────────────────────────
    backends: Optional[Tuple[BackendConfig, ...]] = None


# ── Env-var parsers ────────────────────────────────────────────────────────


# ── Field kinds, derived from the dataclass ─────────────────────────────
# One source of truth for "how do I parse this field": both the yaml loader
# and the HERMES_OTEL_* env loader dispatch on it, and the docs generator
# (scripts/gen_config_docs.py) renders the reference tables from it. A new
# field is therefore yaml-loadable, env-overridable and documented (or the
# tests fail) without touching three hand-maintained lists (#93).
_SCALAR_KINDS = ("bool", "int", "float", "str")


def _field_kind(field: "dataclasses.Field") -> str:
    ann = (
        field.type
        if isinstance(field.type, str)
        else getattr(field.type, "__name__", str(field.type))
    )
    ann = ann.replace("typing.", "")
    if field.name == "backends":
        return "backends"
    if ann.startswith("Optional[Dict") or ann.startswith("Dict"):
        return "map"
    for kind in ("bool", "int", "float", "str"):
        if ann == kind or ann == f"Optional[{kind}]":
            return kind
    raise TypeError(f"unsupported config field annotation {field.name}: {ann}")


def field_kinds() -> Dict[str, str]:
    return {f.name: _field_kind(f) for f in dataclasses.fields(HermesOtelConfig)}


# Fields whose string value gets extra normalisation.
_STR_NORMALISERS = {
    "log_level": lambda v: str(v).upper(),
}

CONTENT_CAPTURE_MODES = ("off", "preview", "full")


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

    Missing file → empty dict (silent). Missing pyyaml → warn + empty dict.
    Malformed yaml → warn + empty dict (explicit, not silent).
    """
    if not path.exists():
        return {}

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning(
            f"[hermes-otel] {path} exists but PyYAML is not installed in the Hermes venv; "
            "the file is ignored (pip install pyyaml)"
        )
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


# ``${VAR_NAME}`` references inside config.yaml string values (headers, api
# keys, endpoints, …) are replaced with the environment variable's value at
# load time (#92). An unset variable is left as the literal ``${VAR_NAME}`` and
# warned about once, so a typo fails loudly at the backend instead of
# silently sending nothing.
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_REF_WARNED: set = set()


def expand_env_refs(value: Any, *, where: str = "config") -> Any:
    """Expand ``${VAR}`` references in a string; non-strings pass through."""
    if not isinstance(value, str) or "${" not in value:
        return value

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(0)[2:-1]
        env_value = os.environ.get(name)
        if env_value is None:
            if name not in _ENV_REF_WARNED:
                _ENV_REF_WARNED.add(name)
                logger.warning(
                    f"[hermes-otel] {where}: environment variable {name!r} referenced as "
                    f"${{{name}}} is not set; leaving the literal value in place"
                )
            return match.group(0)
        return env_value

    return _ENV_REF.sub(_sub, value)


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
            if k == "trace":
                # Friendly alias for users who naturally mirror the singular
                # signal name in prose. ``traces`` wins when both are present.
                if "traces" in raw:
                    continue
                k = "traces"
            if k not in _BACKEND_ALLOWED_KEYS:
                continue
            if k == "headers":
                if isinstance(v, dict):
                    kwargs[k] = {
                        str(kk): expand_env_refs(str(vv), where=f"backends[{idx}].headers")
                        for kk, vv in v.items()
                    }
                continue
            if k in ("traces", "metrics", "logs"):
                if isinstance(v, bool):
                    kwargs[k] = v
                elif isinstance(v, str):
                    parsed = _parse_bool(v)
                    if parsed is not None:
                        kwargs[k] = parsed
                continue
            if v is None:
                continue
            kwargs[k] = expand_env_refs(
                str(v) if not isinstance(v, str) else v, where=f"backends[{idx}].{k}"
            )
        try:
            out.append(BackendConfig(**kwargs))
        except TypeError as e:
            logger.warning(f"[hermes-otel] config.yaml backends[{idx}] invalid: {e}; skipping")

    return tuple(out) if out else None


def _coerce_from_yaml(key: str, value: Any) -> Any:
    """Coerce one config.yaml value to its field type (None = ignore).

    yaml.safe_load already returns native python types; this only normalises
    the few ambiguous cases (bool-vs-int, numeric strings) and rejects — with
    a warning — values that cannot be the field's type.
    """
    if value is None:
        return None
    kind = field_kinds().get(key)
    if kind == "backends":
        return _coerce_backends(value)
    if kind == "map":
        if isinstance(value, dict):
            return {str(k): expand_env_refs(v, where=key) for k, v in value.items()}
        logger.warning(f"[hermes-otel] config.yaml {key!r} must be a mapping; ignoring")
        return None
    parsed = _parse_scalar(kind, key, value)
    if parsed is None:
        logger.warning(
            f"[hermes-otel] config.yaml {key!r}: cannot use {value!r} as {kind}; "
            "keeping the default"
        )
    return parsed


def _parse_scalar(kind: Optional[str], key: str, value: Any) -> Any:
    """Parse a scalar (from yaml or an env string) to ``kind``; None if invalid."""
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return _parse_bool(str(value))
    if kind == "int":
        if isinstance(value, bool):
            return None  # bools are ints in python; reject explicitly
        if isinstance(value, int):
            return value
        return _parse_int(str(value))
    if kind == "float":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return _parse_float(str(value))
    if kind == "str":
        if key == "content_capture" and isinstance(value, bool):
            # YAML 1.1 reads a bare ``off`` as false and ``on`` as true.
            return "off" if not value else "full"
        text = str(value)
        if key == "host_metrics_gpu":
            return _parse_gpu_vendor(text)
        if key == "content_capture":
            mode = text.strip().lower()
            return mode if mode in CONTENT_CAPTURE_MODES else None
        norm = _STR_NORMALISERS.get(key)
        return norm(text) if norm else text
    return None


_GPU_VENDORS = ("auto", "amd", "nvidia", "off")


def _parse_gpu_vendor(value: str) -> Optional[str]:
    v = (value or "").strip().lower()
    return v if v in _GPU_VENDORS else None


def _load_env_overrides() -> Dict[str, Any]:
    """``HERMES_OTEL_<FIELD>`` for every scalar field (maps and ``backends``
    are yaml-only). Invalid values warn and are ignored."""
    out: Dict[str, Any] = {}
    for key, kind in field_kinds().items():
        if kind not in _SCALAR_KINDS:
            continue
        var = _ENV_PREFIX + key.upper()
        raw = os.getenv(var, "").strip()
        if not raw:
            continue
        parsed = _parse_scalar(kind, key, raw)
        if parsed is None:
            logger.warning(f"[hermes-otel] {var}={raw!r} is not a valid {kind}; ignoring it")
            continue
        out[key] = parsed
    return out


def load_config(path: Optional[Path] = None) -> HermesOtelConfig:
    """Build a HermesOtelConfig from yaml + env, per-field precedence.

    Args:
        path: Explicit config.yaml location. When omitted the file is resolved
              by :func:`resolve_config_path`.
    """
    yaml_path = path if path is not None else resolve_config_path()
    yaml_data = _load_yaml(yaml_path) if yaml_path is not None else {}

    values: Dict[str, Any] = {}
    for key, raw in yaml_data.items():
        if key not in _ALLOWED_KEYS:
            continue
        coerced = _coerce_from_yaml(key, raw)
        if coerced is not None:
            values[key] = coerced

    values.update(_load_env_overrides())
    _reconcile_content_capture(values)
    _reconcile_metrics_settings(values)

    # Build config with whatever we have; unset fields fall back to dataclass defaults.
    return replace(HermesOtelConfig(), **values)


_LEGACY_CONTENT_KEYS = ("capture_previews", "capture_full_prompts", "capture_full_responses")


VALID_TEMPORALITIES = ("cumulative", "delta")
VALID_HISTOGRAMS = ("explicit", "exponential")


def normalize_temporality(value: Any, where: str = "metrics_temporality") -> Optional[str]:
    """``cumulative`` / ``delta`` (case-insensitive) or ``None``; anything else warns and is dropped."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in VALID_TEMPORALITIES:
        return text
    logger.warning(
        f"[hermes-otel] {where}={value!r} is not one of {'/'.join(VALID_TEMPORALITIES)}; ignoring"
    )
    return None


def _reconcile_metrics_settings(values: Dict[str, Any]) -> None:
    if "metrics_temporality" in values:
        values["metrics_temporality"] = normalize_temporality(values["metrics_temporality"])
    hist = str(values.get("metrics_histogram", "explicit") or "explicit").strip().lower()
    if hist not in VALID_HISTOGRAMS:
        logger.warning(
            f"[hermes-otel] metrics_histogram={values.get('metrics_histogram')!r} is not one of "
            f"{'/'.join(VALID_HISTOGRAMS)}; using explicit"
        )
        hist = "explicit"
    values["metrics_histogram"] = hist
    try:
        values["metrics_label_limit"] = max(0, int(values.get("metrics_label_limit", 100)))
    except (TypeError, ValueError):
        values["metrics_label_limit"] = 100


def _reconcile_content_capture(values: Dict[str, Any]) -> None:
    """Keep ``content_capture`` and the three legacy booleans consistent.

    ``content_capture`` (yaml or env) wins; the legacy keys are then derived
    from it and a conflicting legacy value is warned about and dropped.
    Without ``content_capture``, legacy keys set the mode the way they did
    before 1.11: ``capture_previews: false`` is ``off``, either full flag
    ``true`` is ``full``, a full flag explicitly ``false`` (previews on) is
    ``preview``. Nothing set means the default, ``full``.
    """
    legacy = {k: values[k] for k in _LEGACY_CONTENT_KEYS if k in values}
    mode = values.get("content_capture")
    if mode is None and legacy:
        if legacy.get("capture_previews") is False:
            mode = "off"
        elif legacy.get("capture_full_prompts") or legacy.get("capture_full_responses"):
            mode = "full"
        else:
            mode = "preview"
        logger.warning(
            "[hermes-otel] %s %s deprecated; use content_capture: %s "
            "(off | preview | full) instead",
            ", ".join(sorted(legacy)),
            "is" if len(legacy) == 1 else "are",
            mode,
        )
    if mode is None:
        return
    derived = {
        "off": (False, False, False),
        "preview": (True, False, False),
        "full": (True, True, True),
    }[mode]
    if "content_capture" in values:
        for key, want in zip(_LEGACY_CONTENT_KEYS, derived):
            if key in legacy and legacy[key] != want:
                logger.warning(
                    "[hermes-otel] %s=%r conflicts with content_capture: %s; "
                    "content_capture wins",
                    key,
                    legacy[key],
                    mode,
                )
        (
            values["capture_previews"],
            values["capture_full_prompts"],
            values["capture_full_responses"],
        ) = derived
    else:
        values["content_capture"] = mode
        values.setdefault("capture_previews", derived[0])
        if mode == "full":
            # Either flag on means full mode; an explicit false for the other
            # keeps that side as previews (prompts full, responses preview).
            values.setdefault("capture_full_prompts", derived[1])
            values.setdefault("capture_full_responses", derived[2])
        else:
            values["capture_full_prompts"], values["capture_full_responses"] = derived[1:]


def content_mode(cfg: HermesOtelConfig) -> str:
    """The mode the hooks actually apply, from the booleans they read."""
    if not cfg.capture_previews:
        return "off"
    if cfg.capture_full_prompts or cfg.capture_full_responses:
        return "full"
    return "preview"


DEFAULT_EXPORT_BATCH_SIZE = 512
FULL_CAPTURE_EXPORT_BATCH_SIZE = 64


def effective_export_batch_size(cfg: HermesOtelConfig) -> int:
    """Spans per OTLP POST: the configured value, else 512, else 64 in full mode.

    A full-capture span carries the whole prompt, so 512 of them in one POST
    can exceed what receivers accept (the OTel Collector's gRPC receiver
    defaults to 4 MiB). Smaller batches keep every export deliverable.
    """
    if cfg.span_batch_max_export_batch_size:
        return int(cfg.span_batch_max_export_batch_size)
    return (
        FULL_CAPTURE_EXPORT_BATCH_SIZE if content_mode(cfg) == "full" else DEFAULT_EXPORT_BATCH_SIZE
    )


# ── Field documentation and grouping ───────────────────────────────────
# One line per field, in the package so both the docs generator
# (scripts/gen_config_docs.py) and the dashboard's Settings tab read the same
# text. A field without an entry fails tests/unit/test_config_surface.py —
# that is the point: adding a knob means documenting it. Backticks mark code;
# a markdown link points into the docs site.
FIELD_DOCS = {
    "enabled": "Master kill switch; `false` unloads every hook",
    "sample_rate": "Parent-based trace-ID ratio 0.0–1.0; `null` = AlwaysOn (no sampling)",
    "root_span_ttl_ms": "Orphan-sweep TTL: a turn root older than this with no end hook is closed",
    "flush_interval_ms": "Metrics export cadence (PeriodicExportingMetricReader)",
    "preview_max_chars": "Cap on preview strings (tool args/results, user message, assistant response)",
    "capture_previews": "Deprecated spelling of `content_capture: off` (when `false`); kept consistent with `content_capture`",
    "tool_input_preview_max_chars": "Per-category cap for tool args previews; `null` = `preview_max_chars`",
    "tool_output_preview_max_chars": "Per-category cap for tool result previews; `null` = `preview_max_chars`",
    "llm_input_preview_max_chars": "Per-category cap for LLM input previews; `null` = `preview_max_chars`",
    "llm_output_preview_max_chars": "Per-category cap for LLM output previews; `null` = `preview_max_chars`",
    "headers": "Extra HTTP headers on every OTLP request; per-backend `headers:` are merged onto these",
    "global_tags": "Merged into the OTel Resource; overridden by `resource_attributes` on key conflict",
    "resource_attributes": "Merged into the Resource on top of the defaults `service.name=hermes-agent`, `service.instance.id` (per-process UUID), `service.version`, `process.pid`",
    "project_name": "`openinference.project.name` on the Resource (Phoenix project); overrides `OTEL_PROJECT_NAME`",
    "span_batch_max_queue_size": "Max buffered spans per backend before drops",
    "span_batch_schedule_delay_ms": "BatchSpanProcessor worker wake-up cadence",
    "span_batch_max_export_batch_size": "Max spans per OTLP POST; `null` = 512, or 64 when `content_capture` is `full`",
    "span_batch_export_timeout_ms": "Per-export HTTP timeout",
    "force_flush_on_session_end": "Flush every backend's span queue at the end of each turn, from a background thread (500 ms per backend, coalesced)",
    "force_flush_wait_ms": "How long the turn waits for that background flush (spans, then metrics and logs on a second thread) before returning to Hermes; a one-shot run exits right after, so this bounds what it exports; `0` = do not wait",
    "capture_conversation_history": "Attach the full message JSON to `llm.*` spans",
    "conversation_history_max_chars": "JSON cap when conversation capture is on",
    "content_capture": "`full` (default): complete prompt and response on every `api.*` span · `preview`: clipped previews only · `off`: no content, metadata only; see [Conversation capture](/configuration/conversation-capture)",
    "capture_full_prompts": "Deprecated: derived from `content_capture`; `false` keeps prompts as previews in `full` mode",
    "capture_full_responses": "Deprecated: derived from `content_capture`; `false` keeps responses as previews in `full` mode",
    "capture_sender_id": "Gateway sessions add `hermes.sender.id` and `user.id` (`platform:sender`)",
    "capture_logs": "Attach an OTel LoggingHandler to Python logging; see [OTel logs](/configuration/logs)",
    "log_level": "Handler level: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`",
    "log_attach_logger": "Logger to attach to; `null` = root, `hermes_otel` = the plugin only",
    "emit_genai_metrics": "Also emit the OTel GenAI spec metrics (`gen_ai.client.*`, `gen_ai.agent.*`)",
    "metrics_temporality": "Metric temporality for OTLP export: `cumulative` (Prometheus family) or `delta` (Datadog, New Relic, Logfire); unset = SDK default; a backend entry's own value wins",
    "metrics_histogram": "Histogram aggregation: `explicit` (spec bucket boundaries) or `exponential` (base-2, for backends that accept it)",
    "metrics_label_limit": "Distinct values a metric label such as `model` may take per process before the rest fold into `other` (0 = no cap)",
    "skill_spans": "Open a `skill.<name>` span on each successful skill load, closed at turn end",
    "discovery_prompt": "Register a system-prompt section advertising `hermes_otel:observability` (changes what the model sees every turn; opt-in)",
    "dashboard_live": "Keep recent spans/metrics/logs in `$HERMES_HOME/hermes_otel_live.db` for the dashboard's Live mode",
    "dashboard_live_max_spans": "Rows kept per kind (spans, metrics, logs) in the live store",
    "dashboard_live_retention_hours": "Rows older than this are dropped from the live store (`0` = only the row cap applies)",
    "host_metrics": "Sample CPU/GPU and emit `process.*` / `system.*` / `hw.*` metrics; see [Host & GPU metrics](/configuration/host-metrics)",
    "host_metrics_gpu": "`auto` · `amd` · `nvidia` · `off` — which GPU SDK to probe",
    "host_metrics_interval_ms": "Host sampling cadence (floor 50 ms)",
    "suppress_mcp_ping_spans": "Drop successful MCP keepalive `ping` spans before export",
    "backends": "Multi-backend fan-out list; see the `backends[]` section",
}

# How the Settings tab groups the fields. Every field appears exactly once
# (tests/unit/test_settings_report.py); order is display order.
FIELD_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "General",
        ("enabled", "sample_rate", "project_name", "global_tags", "resource_attributes", "headers"),
    ),
    (
        "Content capture",
        (
            "content_capture",
            "capture_previews",
            "preview_max_chars",
            "tool_input_preview_max_chars",
            "tool_output_preview_max_chars",
            "llm_input_preview_max_chars",
            "llm_output_preview_max_chars",
            "capture_conversation_history",
            "conversation_history_max_chars",
            "capture_full_prompts",
            "capture_full_responses",
            "capture_sender_id",
        ),
    ),
    (
        "Export and batching",
        (
            "force_flush_on_session_end",
            "force_flush_wait_ms",
            "flush_interval_ms",
            "root_span_ttl_ms",
            "span_batch_max_queue_size",
            "span_batch_schedule_delay_ms",
            "span_batch_max_export_batch_size",
            "span_batch_export_timeout_ms",
        ),
    ),
    (
        "Spans and metrics",
        (
            "emit_genai_metrics",
            "metrics_temporality",
            "metrics_histogram",
            "metrics_label_limit",
            "skill_spans",
            "suppress_mcp_ping_spans",
            "discovery_prompt",
        ),
    ),
    ("Logs", ("capture_logs", "log_level", "log_attach_logger")),
    ("Dashboard", ("dashboard_live", "dashboard_live_max_spans", "dashboard_live_retention_hours")),
    ("Host metrics", ("host_metrics", "host_metrics_gpu", "host_metrics_interval_ms")),
    ("Backends", ("backends",)),
)
