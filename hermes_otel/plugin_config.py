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
    or Langfuse / LangSmith credentials. When ``backends:`` is empty, env-var
    detection is used and at most one backend is selected: LangSmith first,
    then the order of ``backend_specs.ENV_PRIORITY``.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .backend_specs import SPECS as _SPECS
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

# The two file locations, derived from the home in force *at call time* so
# each profile of a multiplexed gateway reads its own (#405). ``None`` means
# "derive"; a test (or an embedder) may pin either to a path.
#
# Durable: outside the plugin directory, so it survives
# `hermes plugins install --force` (which replaces the plugin directory
# wholesale, see issue #55).
DURABLE_CONFIG_PATH: Optional[Path] = None
# Legacy: inside the plugin directory. Still read, so existing installs keep
# working, but it is wiped by a reinstall.
DEFAULT_CONFIG_PATH: Optional[Path] = None


def durable_config_path() -> Path:
    """``$HERMES_HOME/hermes_otel.yaml`` for the home in force now."""
    return DURABLE_CONFIG_PATH or hermes_home() / "hermes_otel.yaml"


def legacy_config_path() -> Path:
    """``$HERMES_HOME/plugins/hermes_otel/config.yaml`` for the home in force now."""
    return DEFAULT_CONFIG_PATH or hermes_home() / "plugins" / "hermes_otel" / "config.yaml"


def resolve_config_path_with_reason() -> Tuple[Optional[Path], str]:
    """The config file to read and the rule that picked it.

    Order: ``HERMES_OTEL_CONFIG`` (``env``; even if missing, since an explicit
    path that does not exist is worth surfacing), then the durable location
    (``durable``), then the legacy plugin-directory copy (``legacy``);
    ``(None, "none")`` when there is no file.
    """
    override = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser(), "env"

    durable, legacy = durable_config_path(), legacy_config_path()
    if durable.exists():
        if legacy.exists():
            logger.warning(
                f"[hermes-otel] using {durable}; ignoring the copy in the plugin "
                f"directory ({legacy}). Delete the latter to silence this."
            )
        return durable, "durable"
    if legacy.exists():
        return legacy, "legacy"
    return None, "none"


def resolve_config_path() -> Optional[Path]:
    """Return the config file to read, or ``None`` when there is none
    (see :func:`resolve_config_path_with_reason`)."""
    return resolve_config_path_with_reason()[0]


ENV_PREFIX = "HERMES_OTEL_"
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}


# Logger prefixes the logs pipeline never forwards: the OTel SDK and the HTTP
# client stack (an export that logs itself would feed the next export), plus
# the third-party loggers Hermes itself pins at WARNING (hermes_logging._NOISY_LOGGERS).
DEFAULT_LOG_EXCLUDE_LOGGERS: Tuple[str, ...] = (
    "opentelemetry",
    "urllib3",
    "httpx",
    "httpcore",
    "requests",
    "openai",
    "asyncio",
    "hpack",
    "grpc",
    "websockets",
    "charset_normalizer",
    "markdown_it",
)

LOG_EVENTS_CONTENT_MODES = ("inherit", "full", "preview", "off")


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
    # Export the turn's token roll-up (``gen_ai.usage.*`` / ``llm.token_count.*``
    # on the ``agent`` / ``cron`` root) to this backend. None = type preset:
    # off for backends that sum usage over every span (opik, langwatch, #327).
    root_usage: Optional[bool] = None
    logs: Optional[bool] = (
        None  # None = auto (on for signoz/otlp/lgtm/uptrace/openobserve/parseable/honeycomb)
    )
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
    # victoria: the metrics and logs stores (VictoriaMetrics, VictoriaLogs); traces go to `endpoint`
    metrics_endpoint: Optional[str] = None
    logs_endpoint: Optional[str] = None
    # Dashboard-only keys the entry carried (``query_port``, ``prometheus_url``,
    # ...: what the type's adapter reads, per the manifest), kept so the
    # loader never silently drops a query-side setting (#402). Pairs, so the
    # frozen dataclass stays hashable.
    extra: Tuple[Tuple[str, Any], ...] = ()


# ── Numeric bounds (#487) ───────────────────────────────────────────────────
# Every numeric knob and the smallest value it may take. Out-of-range values
# (a ``0`` queue size, a negative timeout, a sample rate above 1) used to
# reach the SDK, which raises inside the pipeline init: every backend then
# logged "traces init failed" and the run exported nothing. Now the field
# warns at load and keeps its default, the way every other bad value in this
# file is handled. ``None`` stays ``None`` for the Optional fields.
# The config reference renders these bounds from this table.
NUMERIC_BOUNDS: Dict[str, Tuple[float, Optional[float]]] = {
    "sample_rate": (0.0, 1.0),
    "root_span_ttl_ms": (1, None),
    "subagent_span_ttl_ms": (1, None),
    "flush_interval_ms": (1, None),
    "preview_max_chars": (1, None),
    "tool_input_preview_max_chars": (1, None),
    "tool_output_preview_max_chars": (1, None),
    "llm_input_preview_max_chars": (1, None),
    "llm_output_preview_max_chars": (1, None),
    "span_batch_max_queue_size": (1, None),
    "span_batch_schedule_delay_ms": (1, None),
    "span_batch_max_export_batch_size": (1, None),
    "span_batch_export_timeout_ms": (1, None),
    "force_flush_wait_ms": (0, None),
    "conversation_history_max_chars": (1, None),
    "metrics_label_limit": (0, None),
    "log_max_attribute_length": (1, None),
    "log_batch_schedule_delay_ms": (1, None),
    "log_batch_max_queue_size": (1, None),
    "log_batch_max_export_batch_size": (1, None),
    "log_batch_export_timeout_ms": (1, None),
    "dashboard_live_max_spans": (1, None),
    "dashboard_live_retention_hours": (0.0, None),
    "host_metrics_interval_ms": (1, None),
}

# ``max_export_batch_size`` may not exceed ``max_queue_size`` (the SDK raises):
# (batch field, queue field).
_BATCH_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("span_batch_max_export_batch_size", "span_batch_max_queue_size"),
    ("log_batch_max_export_batch_size", "log_batch_max_queue_size"),
)


def _validate_numeric_bounds(cfg: Any) -> None:
    """Warn about and reset every numeric field outside :data:`NUMERIC_BOUNDS`.

    Runs from ``__post_init__`` so yaml, env and direct construction all pass
    through it. Strings that are not numbers never get here: the loader
    rejected them already. A bool is not a number for this purpose.
    """
    defaults = {f.name: f.default for f in dataclasses.fields(cfg)}
    for name, (lo, hi) in NUMERIC_BOUNDS.items():
        value = getattr(cfg, name)
        if value is None:
            continue
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok and (value < lo or (hi is not None and value > hi)):
            ok = False
        if ok:
            continue
        default = defaults[name]
        want = f"at least {lo}" if hi is None else f"between {lo} and {hi}"
        logger.warning(
            f"[hermes-otel] {name}={value!r} must be {want}; using "
            + ("null" if default is None else repr(default))
        )
        object.__setattr__(cfg, name, default)
    for batch_name, queue_name in _BATCH_PAIRS:
        batch, queue = getattr(cfg, batch_name), getattr(cfg, queue_name)
        if batch is not None and batch > queue:
            logger.warning(
                f"[hermes-otel] {batch_name}={batch} exceeds {queue_name}={queue}; "
                f"using {queue}"
            )
            object.__setattr__(cfg, batch_name, queue)


@dataclass(frozen=True)
class HermesOtelConfig:
    """Frozen configuration object passed through the plugin."""

    enabled: bool = True
    sample_rate: Optional[float] = None  # None = AlwaysOn. 0..1 = ratio.
    root_span_ttl_ms: int = 600_000  # 10 min orphan sweep threshold
    # A delegation (``subagent.*``) span outlives its parent's turn: Hermes
    # runs top-level delegations in the background and fires subagent_stop
    # turns later (#392). The turn sweep leaves it alone; this is its own
    # safety net for a child whose stop never arrives. Hermes sets no child
    # timeout by default, hence a day.
    subagent_span_ttl_ms: int = 86_400_000
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
    # Opt-in: a named profile reports itself as its own service
    # (``service.name=<profile>``, ``service.namespace=hermes-agent``) so a
    # fleet shows up as one service per profile in service pickers and maps
    # (#395). Off: every profile is ``service.name=hermes-agent``.
    service_name_from_profile: bool = False
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
    # ``content_capture`` (see _reconcile_content_capture) and hook code asks
    # ``content_mode()`` / ``captures_full_prompts()`` etc. below.
    # ``capture_previews: false`` still means "off".
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
    # Logger-name prefixes never forwarded (loop guard + Hermes' own noisy
    # list). Replaces the hardcoded filters; also drives the live tail.
    log_exclude_loggers: Tuple[str, ...] = DEFAULT_LOG_EXCLUDE_LOGGERS
    # Per-logger minimum level, e.g. {gateway.config: WARNING}.
    log_logger_levels: Optional[Dict[str, str]] = None
    # Drop records the plugin cannot attribute to a session (gateways that
    # only want turn logs shipped).
    log_only_in_turn: bool = False
    # Longest string attribute value exported on a log record (characters).
    log_max_attribute_length: int = 4096
    # BatchLogRecordProcessor knobs, mirroring the span batch settings.
    log_batch_schedule_delay_ms: int = 1000
    log_batch_max_queue_size: int = 2048
    log_batch_max_export_batch_size: int = 512
    log_batch_export_timeout_ms: int = 30000
    # Floor for the live store's Logs tab (the OTLP path keeps log_level).
    log_live_min_level: str = "INFO"
    # Structured hermes.* / GenAI events from the hooks (Phase 2, #267).
    # Off by default; ``content`` inherits content_capture unless narrowed.
    log_events: bool = False
    log_events_content: str = "inherit"  # inherit | full | preview | off
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
    # Metric-name compatibility (#485). ``claude_code`` exports the four
    # instruments Claude Code's telemetry also has under Claude Code's names,
    # units and labels (``claude_code.session.count``, ``claude_code.token.usage``,
    # ``claude_code.cost.usage``, ``claude_code.active_time.total``) *instead of*
    # their ``hermes.*`` twins, so a dashboard built for Claude Code reads
    # Hermes unchanged; ``both`` keeps the ``hermes.*`` twins as well (two
    # series per point for those four); ``off`` is the default. Every other
    # ``hermes.*`` and ``gen_ai.*`` instrument is untouched either way.
    metric_name_compat: str = "off"
    # Claude Code puts ``session.id`` on every metric by default and its
    # dashboards count sessions with it. The plugin never puts an id on a
    # metric; this opt-in adds ``session.id`` to the compat instruments only.
    metric_name_compat_session_id: bool = False
    # ── Skill execution-window spans ────────────────────────────────────
    # Emit a skill:<name> span when the agent loads a skill (via skill_view
    # or a /skills/ path), spanning from load to the turn boundary. Skills
    # overlap freely. Set false to keep only the hermes.skill.* attribute and
    # the skill_inferred counter.
    skill_spans: bool = True
    # ── Remote parent (#474) ─────────────────────────────────────────────
    # An outer process that launches Hermes can export HERMES_OTEL_TRACEPARENT
    # (and HERMES_OTEL_TRACESTATE) to make the turn a child of its own span.
    # false ignores the variables (a kanban worker's TRACEPARENT hand-off is
    # separate and unaffected).
    remote_parent: bool = True
    # ── Reachability probe (#481) ────────────────────────────────────────
    # At init, one TCP connect per backend endpoint on a background thread,
    # logged as reachable / unreachable. Once, never periodic; sends no bytes.
    probe_backends: bool = True
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

    def __post_init__(self) -> None:
        # ``global_tags`` is the pre-``resource_attributes`` spelling: fold it
        # in once, here, so the resource builder reads one mapping.
        # ``resource_attributes`` wins on a key conflict, as it always has.
        if self.global_tags:
            merged = {**self.global_tags, **(self.resource_attributes or {})}
            object.__setattr__(self, "resource_attributes", merged)
            object.__setattr__(self, "global_tags", None)
        _validate_numeric_bounds(self)

    # ── What content the hooks record (``content_capture``) ──────────────
    # The hooks ask these instead of reading the three pre-1.11 booleans.
    # The booleans stay as input spellings: in ``full`` mode they can still
    # keep one side as previews (``capture_full_responses: false``).

    def content_mode(self) -> str:
        """``off`` / ``preview`` / ``full``: the mode the hooks apply."""
        if not self.capture_previews:
            return "off"
        if self.capture_full_prompts or self.capture_full_responses:
            return "full"
        return "preview"

    def captures_content(self) -> bool:
        """Whether any prompt, tool or response content is recorded (mode is not ``off``)."""
        return self.content_mode() != "off"

    def captures_full_prompts(self) -> bool:
        """Whether api spans carry the complete prompt instead of a preview."""
        return self.captures_content() and self.capture_full_prompts

    def captures_full_responses(self) -> bool:
        """Whether api spans carry the complete response instead of a preview."""
        return self.captures_content() and self.capture_full_responses


# ── Env-var parsers ────────────────────────────────────────────────────────


# ── Field kinds, derived from the dataclass ─────────────────────────────
# One source of truth for "how do I parse this field": both the yaml loader
# and the HERMES_OTEL_* env loader dispatch on it, and the docs generator
# (scripts/gen_config_docs.py) renders the reference tables from it. A new
# field is therefore yaml-loadable, env-overridable and documented (or the
# tests fail) without touching three hand-maintained lists (#93).
SCALAR_KINDS = ("bool", "int", "float", "str", "list")  # env-overridable kinds


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
    if ann.startswith("Optional[Tuple[str") or ann.startswith("Tuple[str"):
        return "list"
    for kind in ("bool", "int", "float", "str"):
        if ann == kind or ann == f"Optional[{kind}]":
            return kind
    raise TypeError(f"unsupported config field annotation {field.name}: {ann}")


def field_kinds() -> Dict[str, str]:
    return {f.name: _field_kind(f) for f in dataclasses.fields(HermesOtelConfig)}


# Fields whose string value gets extra normalisation.
_STR_NORMALISERS = {
    "log_level": lambda v: str(v).upper(),
    "log_live_min_level": lambda v: str(v).upper(),
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


def load_yaml(path: Path) -> Dict[str, Any]:
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


ALLOWED_KEYS = {f.name for f in fields(HermesOtelConfig)}
TOP_LEVEL_BLOCKS = ("logs",)  # yaml keys that are blocks, not fields
_BACKEND_ALLOWED_KEYS = {f.name for f in fields(BackendConfig)} - {"extra"}
# Dashboard-only keys (what the adapters read, from the manifest) plus the
# generic query-side spellings; anything else unknown is warned about once.
_QUERY_KEYS = frozenset(
    {"query_port", "ui_url", "query_api", "query_path", "prometheus_url", "loki_url"}
    | {k for s in _SPECS for k in s.query_keys}
)
_UNKNOWN_KEY_WARNED: set = set()


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
            if k in ("traces", "metrics", "logs", "root_usage"):
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
            out.append(BackendConfig(**kwargs, extra=tuple(extra)))
        except TypeError as e:
            logger.warning(f"[hermes-otel] config.yaml backends[{idx}] invalid: {e}; skipping")

    return tuple(out) if out else None


_BACKEND_LOG_OVERRIDE_KEYS = ("level", "exclude_loggers", "logger_levels", "only_in_turn", "events")


def _coerce_backend_log_overrides(raw: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Validate a per-backend ``logs:`` mapping; unknown keys and bad values warn and are dropped."""
    out: Dict[str, Any] = {}
    where = f"backends[{idx}].logs"
    for k, v in raw.items():
        if k not in _BACKEND_LOG_OVERRIDE_KEYS:
            logger.warning(f"[hermes-otel] config.yaml {where}.{k}: unknown key; ignoring")
            continue
        if k == "level":
            out[k] = str(v).strip().upper() if v is not None else None
        elif k == "exclude_loggers":
            parsed = _parse_scalar("list", k, v)
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
            parsed = _parse_scalar("bool", k, v)
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
                    parsed = _parse_scalar("bool", ek, ev)
                    if parsed is not None:
                        events["enabled"] = parsed
                elif ek == "content":
                    parsed = _parse_scalar("str", "log_events_content", ev)
                    if parsed is None:
                        logger.warning(
                            f"[hermes-otel] config.yaml {where}.events.content: {ev!r} is not one of "
                            f"{', '.join(LOG_EVENTS_CONTENT_MODES)}; ignoring"
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


# ── The ``logs:`` block (#266) ──────────────────────────────────────────────
# The flat ``capture_logs`` / ``log_*`` fields are the dataclass's source of
# truth (one field, one env var, one schema row). The block is the preferred
# yaml spelling and is flattened onto them at load time; the flat spellings
# keep working as aliases.
LOGS_BLOCK_KEY = "logs"
_LOGS_BLOCK: Dict[str, Any] = {
    "capture": "capture_logs",
    "level": "log_level",
    "attach_logger": "log_attach_logger",
    "exclude_loggers": "log_exclude_loggers",
    "logger_levels": "log_logger_levels",
    "only_in_turn": "log_only_in_turn",
    "max_attribute_length": "log_max_attribute_length",
    "live_min_level": "log_live_min_level",
    "batch": {
        "schedule_delay_ms": "log_batch_schedule_delay_ms",
        "max_queue_size": "log_batch_max_queue_size",
        "max_export_batch_size": "log_batch_max_export_batch_size",
        "export_timeout_ms": "log_batch_export_timeout_ms",
    },
    "events": {"enabled": "log_events", "content": "log_events_content"},
}
# flat field -> ("block", "path") for rendering the effective yaml as a block
LOGS_FLAT_TO_BLOCK: Dict[str, Tuple[str, ...]] = {}
for _k, _v in _LOGS_BLOCK.items():
    if isinstance(_v, dict):
        for _kk, _vv in _v.items():
            LOGS_FLAT_TO_BLOCK[_vv] = (_k, _kk)
    else:
        LOGS_FLAT_TO_BLOCK[_v] = (_k,)
LOG_FIELDS: Tuple[str, ...] = tuple(LOGS_FLAT_TO_BLOCK)


def _flatten_logs_block(block: Any, flat: Dict[str, Any]) -> Dict[str, Any]:
    """Turn ``logs: {...}`` into flat keys; the block wins over a flat alias and says so."""
    out: Dict[str, Any] = {}
    if not isinstance(block, dict):
        logger.warning("[hermes-otel] config.yaml 'logs' must be a mapping; ignoring")
        return out

    def _walk(mapping: Dict[str, Any], spec: Dict[str, Any], prefix: str) -> None:
        for k, v in mapping.items():
            target = spec.get(k)
            if target is None:
                logger.warning(f"[hermes-otel] config.yaml {prefix}{k}: unknown key; ignoring")
                continue
            if isinstance(target, dict):
                if isinstance(v, dict):
                    _walk(v, target, f"{prefix}{k}.")
                else:
                    logger.warning(
                        f"[hermes-otel] config.yaml {prefix}{k} must be a mapping; ignoring"
                    )
                continue
            if target in flat and flat[target] != v:
                logger.warning(
                    f"[hermes-otel] config.yaml {target}={flat[target]!r} conflicts with "
                    f"{prefix}{k}={v!r}; the logs: block wins"
                )
            out[target] = v

    _walk(block, _LOGS_BLOCK, f"{LOGS_BLOCK_KEY}.")
    return out


def normalize_yaml_data(yaml_data: Dict[str, Any], *, notice: bool = True) -> Dict[str, Any]:
    """Flatten the ``logs:`` block onto the flat fields (block wins; flat spellings noticed once).

    Shared by :func:`load_config` and the settings report so both see the
    same flat view of the file.
    """
    data = dict(yaml_data or {})
    flat_log_keys = sorted(k for k in data if k in LOG_FIELDS)
    if LOGS_BLOCK_KEY in data:
        data.update(_flatten_logs_block(data.pop(LOGS_BLOCK_KEY), data))
    if flat_log_keys and notice:
        logger.info(
            "[hermes-otel] %s %s the flat spelling; the logs: block is preferred "
            "(see /configuration/logs#configuration)",
            ", ".join(flat_log_keys),
            "uses" if len(flat_log_keys) == 1 else "use",
        )
    return data


def coerce_from_yaml(key: str, value: Any, *, warn: bool = True) -> Any:
    """Coerce one config.yaml value to its field type (None = ignore).

    yaml.safe_load already returns native python types; this only normalises
    the few ambiguous cases (bool-vs-int, numeric strings) and rejects — with
    a warning — values that cannot be the field's type.
    """
    if value is None:
        return None
    kind = field_kinds().get(key)
    if kind == "backends":
        return coerce_backends(value)
    if kind == "map":
        if isinstance(value, dict):
            return {str(k): expand_env_refs(v, where=key) for k, v in value.items()}
        if warn:
            logger.warning(f"[hermes-otel] config.yaml {key!r} must be a mapping; ignoring")
        return None
    parsed = _parse_scalar(kind, key, value)
    if parsed is None and warn:
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
        if key in ("content_capture", "log_events_content") and isinstance(value, bool):
            # YAML 1.1 reads a bare ``off`` as false and ``on`` as true.
            return "off" if not value else "full"
        text = str(value)
        if key == "host_metrics_gpu":
            return _parse_gpu_vendor(text)
        if key == "content_capture":
            mode = text.strip().lower()
            return mode if mode in CONTENT_CAPTURE_MODES else None
        if key == "log_events_content":
            mode = text.strip().lower()
            return mode if mode in LOG_EVENTS_CONTENT_MODES else None
        norm = _STR_NORMALISERS.get(key)
        return norm(text) if norm else text
    if kind == "list":
        # yaml list, or a comma-separated string (the env-var spelling).
        if isinstance(value, (list, tuple)):
            items = [str(v).strip() for v in value if v is not None and str(v).strip()]
            return tuple(items)
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return None
    return None


_GPU_VENDORS = ("auto", "amd", "nvidia", "off")


def _parse_gpu_vendor(value: str) -> Optional[str]:
    v = (value or "").strip().lower()
    return v if v in _GPU_VENDORS else None


@dataclass(frozen=True)
class FieldSource:
    """Where one config field's value came from, as the loader decided it.

    ``source`` is ``default``, ``file`` or ``env``. ``file_value`` is the
    coerced file value (``None`` when absent or ``file_invalid``) and
    ``file_raw`` the value as written; ``env_var`` is the override variable
    for scalar fields, ``env_raw`` its text when set. A value that was given
    but not usable is flagged ``*_invalid`` and the field keeps its default.
    ``derived_from`` names the keys a derived field follows (``content_capture``
    and its legacy flags).
    """

    source: str = "default"
    file_raw: Any = None
    file_value: Any = None
    file_invalid: bool = False
    env_var: Optional[str] = None
    env_raw: Optional[str] = None
    env_invalid: bool = False
    derived_from: Optional[Tuple[str, ...]] = None


def resolve_fields(
    yaml_data: Dict[str, Any], *, warn: bool = True
) -> Tuple[HermesOtelConfig, Dict[str, FieldSource]]:
    """Normalised yaml + the environment → the config and, per field, where its
    value came from. The one implementation of the precedence rules: a file
    value that fails coercion is ignored, an env value that fails parsing is
    ignored, and a valid env value wins over the file. ``warn=False`` keeps a
    report (the Settings tab) from logging the loader's warnings again.
    """
    kinds = field_kinds()
    defaults = HermesOtelConfig()
    values: Dict[str, Any] = {}
    sources: Dict[str, Dict[str, Any]] = {}
    for f in fields(HermesOtelConfig):
        key, kind = f.name, kinds[f.name]
        src: Dict[str, Any] = {
            "env_var": ENV_PREFIX + key.upper() if kind in SCALAR_KINDS else None
        }
        sources[key] = src
        raw = yaml_data.get(key)
        if raw is not None:
            src["file_raw"] = raw
            coerced = coerce_from_yaml(key, raw, warn=warn)
            if coerced is None:
                src["file_invalid"] = True
            else:
                values[key] = coerced
                src["file_value"] = coerced
                src["source"] = "file"
        if src["env_var"]:
            raw_env = os.getenv(src["env_var"], "").strip()
            if raw_env:
                src["env_raw"] = raw_env
                parsed = _parse_scalar(kind, key, raw_env)
                if parsed is None:
                    src["env_invalid"] = True
                    if warn:
                        logger.warning(
                            f"[hermes-otel] {src['env_var']}={raw_env!r} is not a valid {kind}; ignoring it"
                        )
                else:
                    values[key] = parsed
                    src["source"] = "env"

    # An unknown histogram or temporality is dropped (the default applies);
    # flag it the way an uncoercible value is flagged (#413).
    for key, valid in (
        ("metrics_histogram", VALID_HISTOGRAMS),
        ("metrics_temporality", VALID_TEMPORALITIES),
        ("metric_name_compat", VALID_METRIC_NAME_COMPAT),
    ):
        src = sources[key]
        if src.get("source", "default") == "default" or values.get(key) is None:
            continue
        if str(values[key]).strip().lower() not in valid:
            src["env_invalid" if src["source"] == "env" else "file_invalid"] = True
            src["source"] = "default"

    if warn and values.get("global_tags"):
        logger.warning(
            "[hermes-otel] global_tags is deprecated; it is merged into resource_attributes "
            "(which wins on a key conflict). Move the keys there."
        )

    # content_capture and the legacy booleans are kept consistent; record
    # which key decided the others.
    explicit = set(values)
    _reconcile_content_capture(values, warn=warn)
    if "content_capture" in explicit:
        driver: Tuple[str, ...] = ("content_capture",)
        derived = LEGACY_CONTENT_KEYS
    else:
        driver = tuple(k for k in LEGACY_CONTENT_KEYS if k in explicit)
        derived = tuple(k for k in ("content_capture",) + LEGACY_CONTENT_KEYS if k not in explicit)
    if driver:
        for key in derived:
            if key in values:
                sources[key]["source"] = sources[driver[0]].get("source", "default")
                sources[key]["derived_from"] = driver

    reconcile_metrics_settings(values, warn=warn)

    cfg = replace(defaults, **values)
    return cfg, {k: FieldSource(**v) for k, v in sources.items()}


def load_config_with_sources(
    path: Optional[Path] = None, *, warn: bool = True
) -> Tuple[HermesOtelConfig, Dict[str, FieldSource]]:
    """:func:`load_config` plus where each field's value came from."""
    yaml_path = path if path is not None else resolve_config_path()
    yaml_data = load_yaml(yaml_path) if yaml_path is not None else {}
    return resolve_fields(normalize_yaml_data(yaml_data, notice=warn), warn=warn)


def load_config(path: Optional[Path] = None) -> HermesOtelConfig:
    """Build a HermesOtelConfig from yaml + env, per-field precedence.

    Args:
        path: Explicit config.yaml location. When omitted the file is resolved
              by :func:`resolve_config_path`.
    """
    return load_config_with_sources(path)[0]


LEGACY_CONTENT_KEYS = ("capture_previews", "capture_full_prompts", "capture_full_responses")


VALID_TEMPORALITIES = ("cumulative", "delta")
VALID_HISTOGRAMS = ("explicit", "exponential")
VALID_METRIC_NAME_COMPAT = ("off", "claude_code", "both")


def normalize_temporality(
    value: Any, where: str = "metrics_temporality", *, warn: bool = True
) -> Optional[str]:
    """``cumulative`` / ``delta`` (case-insensitive) or ``None``; anything else warns and is dropped."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in VALID_TEMPORALITIES:
        return text
    if not warn:
        return None
    logger.warning(
        f"[hermes-otel] {where}={value!r} is not one of {'/'.join(VALID_TEMPORALITIES)}; ignoring"
    )
    return None


def reconcile_metrics_settings(values: Dict[str, Any], *, warn: bool = True) -> None:
    if "metrics_temporality" in values:
        values["metrics_temporality"] = normalize_temporality(
            values["metrics_temporality"], warn=warn
        )
    hist = str(values.get("metrics_histogram", "explicit") or "explicit").strip().lower()
    if hist not in VALID_HISTOGRAMS and warn:
        logger.warning(
            f"[hermes-otel] metrics_histogram={values.get('metrics_histogram')!r} is not one of "
            f"{'/'.join(VALID_HISTOGRAMS)}; using explicit"
        )
    if hist not in VALID_HISTOGRAMS:
        hist = "explicit"
    values["metrics_histogram"] = hist
    if "metric_name_compat" in values:
        compat = str(values.get("metric_name_compat") or "off").strip().lower()
        if compat in ("false", "none", "no", ""):
            compat = "off"
        if compat not in VALID_METRIC_NAME_COMPAT:
            if warn:
                logger.warning(
                    f"[hermes-otel] metric_name_compat={values.get('metric_name_compat')!r} is not "
                    f"one of {'/'.join(VALID_METRIC_NAME_COMPAT)}; using off"
                )
            compat = "off"
        values["metric_name_compat"] = compat
    try:
        values["metrics_label_limit"] = max(0, int(values.get("metrics_label_limit", 100)))
    except (TypeError, ValueError):
        values["metrics_label_limit"] = 100


def _reconcile_content_capture(values: Dict[str, Any], *, warn: bool = True) -> None:
    """Keep ``content_capture`` and the three legacy booleans consistent.

    ``content_capture`` (yaml or env) wins; the legacy keys are then derived
    from it and a conflicting legacy value is warned about and dropped.
    Without ``content_capture``, legacy keys set the mode the way they did
    before 1.11: ``capture_previews: false`` is ``off``, either full flag
    ``true`` is ``full``, a full flag explicitly ``false`` (previews on) is
    ``preview``. Nothing set means the default, ``full``.
    """
    legacy = {k: values[k] for k in LEGACY_CONTENT_KEYS if k in values}
    mode = values.get("content_capture")
    if mode is None and legacy:
        if legacy.get("capture_previews") is False:
            mode = "off"
        elif legacy.get("capture_full_prompts") or legacy.get("capture_full_responses"):
            mode = "full"
        else:
            mode = "preview"
    if mode is not None and legacy and "content_capture" not in values and warn:
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
        for key, want in zip(LEGACY_CONTENT_KEYS, derived):
            if key in legacy and legacy[key] != want and warn:
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
    """The mode the hooks actually apply (see :meth:`HermesOtelConfig.content_mode`)."""
    return cfg.content_mode()


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
    "subagent_span_ttl_ms": "Delegation-span TTL: a `subagent.*` span whose `subagent_stop` never arrives is closed after this (it outlives the parent's turn by design)",
    "flush_interval_ms": "Metrics export cadence (PeriodicExportingMetricReader)",
    "preview_max_chars": "Cap on preview strings (tool args/results, user message, assistant response)",
    "capture_previews": "Deprecated spelling of `content_capture: off` (when `false`); kept consistent with `content_capture`",
    "tool_input_preview_max_chars": "Per-category cap for tool args previews; `null` = `preview_max_chars`",
    "tool_output_preview_max_chars": "Per-category cap for tool result previews; `null` = `preview_max_chars`",
    "llm_input_preview_max_chars": "Per-category cap for LLM input previews; `null` = `preview_max_chars`",
    "llm_output_preview_max_chars": "Per-category cap for LLM output previews; `null` = `preview_max_chars`",
    "headers": "Extra HTTP headers on every OTLP request; per-backend `headers:` are merged onto these",
    "global_tags": "Deprecated: merged into `resource_attributes` at load (which wins on a key conflict)",
    "resource_attributes": "Merged into the Resource on top of the defaults `service.name=hermes-agent`, `service.instance.id` (per-process UUID), `service.version`, `process.pid`",
    "project_name": "`openinference.project.name` on the Resource (Phoenix project); overrides `OTEL_PROJECT_NAME`",
    "service_name_from_profile": "A named profile exports as `service.name=<profile>` with `service.namespace=hermes-agent`; the default profile stays `hermes-agent`. `resource_attributes.service.name` still wins",
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
    "log_exclude_loggers": "Logger-name prefixes never forwarded (loop guard + Hermes' noisy third-party loggers); `logs.exclude_loggers`",
    "log_logger_levels": "Per-logger minimum level, e.g. `{gateway.config: WARNING}`; `logs.logger_levels`",
    "log_only_in_turn": "Drop records the plugin cannot attribute to a session; `logs.only_in_turn`",
    "log_max_attribute_length": "Longest exported string attribute on a log record (characters); `logs.max_attribute_length`",
    "log_batch_schedule_delay_ms": "Log batch processor: export every N ms; `logs.batch.schedule_delay_ms`",
    "log_batch_max_queue_size": "Log batch processor: queued records before drops; `logs.batch.max_queue_size`",
    "log_batch_max_export_batch_size": "Log batch processor: records per export request; `logs.batch.max_export_batch_size`",
    "log_batch_export_timeout_ms": "Log batch processor: per-export timeout; `logs.batch.export_timeout_ms`",
    "log_live_min_level": "Floor for the dashboard's live Logs tab (`INFO` default; the OTLP path keeps `log_level`); `logs.live_min_level`",
    "log_events": "Emit structured `hermes.*` / GenAI events from the hooks (Phase 2); `logs.events.enabled`",
    "log_events_content": "Content on events: `inherit` (follow `content_capture`) / `full` / `preview` / `off`; `logs.events.content`",
    "emit_genai_metrics": "Also emit the OTel GenAI spec metrics (`gen_ai.client.*`, `gen_ai.agent.*`)",
    "metrics_temporality": "Metric temporality for OTLP export: `cumulative` (Prometheus family) or `delta` (Datadog, New Relic, Logfire); unset = SDK default; a backend entry's own value wins",
    "metrics_histogram": "Histogram aggregation: `explicit` (spec bucket boundaries) or `exponential` (base-2, for backends that accept it)",
    "metrics_label_limit": "Distinct values a metric label such as `model` may take per process before the rest fold into `other` (0 = no cap)",
    "metric_name_compat": "`off` · `claude_code` · `both` — export session count, token usage, cost and active time under Claude Code's metric names, units and labels (`claude_code.*`) instead of (`claude_code`) or beside (`both`) the `hermes.*` twins, so dashboards built for Claude Code read Hermes; see [Claude Code dashboards](/guides/claude-code-dashboards)",
    "metric_name_compat_session_id": "Also put `session.id` on the `claude_code.*` instruments, as Claude Code does by default (per-session panels need it; one series per session)",
    "skill_spans": "Open a `skill.<name>` span on each successful skill load, closed at turn end",
    "probe_backends": "At init, open one TCP connection to each backend endpoint on a background thread and log reachable / unreachable per backend; once, never periodic, no bytes sent, exports never wait for it",
    "remote_parent": "Parent the turn root under the span named by `HERMES_OTEL_TRACEPARENT` (+ `HERMES_OTEL_TRACESTATE`) when an outer process exported them; `false` ignores the variables. The kanban worker hand-off (`TRACEPARENT`) is separate",
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
        (
            "enabled",
            "sample_rate",
            "project_name",
            "service_name_from_profile",
            "global_tags",
            "resource_attributes",
            "headers",
        ),
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
            "subagent_span_ttl_ms",
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
            "metric_name_compat",
            "metric_name_compat_session_id",
            "skill_spans",
            "remote_parent",
            "probe_backends",
            "suppress_mcp_ping_spans",
            "discovery_prompt",
        ),
    ),
    (
        "Logs",
        (
            "capture_logs",
            "log_level",
            "log_attach_logger",
            "log_exclude_loggers",
            "log_logger_levels",
            "log_only_in_turn",
            "log_max_attribute_length",
            "log_batch_schedule_delay_ms",
            "log_batch_max_queue_size",
            "log_batch_max_export_batch_size",
            "log_batch_export_timeout_ms",
            "log_live_min_level",
            "log_events",
            "log_events_content",
        ),
    ),
    ("Dashboard", ("dashboard_live", "dashboard_live_max_spans", "dashboard_live_retention_hours")),
    ("Host metrics", ("host_metrics", "host_metrics_gpu", "host_metrics_interval_ms")),
    ("Backends", ("backends",)),
)
