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
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple, Union

from .debug_utils import logger

if TYPE_CHECKING:  # the ``backends:`` list has its own loader (backend_config.py)
    from .backend_config import BackendConfig

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


# The (durable, legacy) pairs already warned about: the dashboard resolves the
# path on every poll and must not repeat the warning (#519).
_WARNED_SHADOWED: set = set()


def resolve_config_path_with_reason() -> Tuple[Optional[Path], str]:
    """The config file to read and the rule that picked it.

    Order: ``HERMES_OTEL_CONFIG`` (``env``; even if missing, since an explicit
    path that does not exist is worth surfacing), then the durable location
    (``durable``), then the legacy plugin-directory copy (``legacy``);
    ``(None, "none")`` when there is no file. Both files present is warned
    about once per pair.
    """
    override = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser(), "env"

    durable, legacy = durable_config_path(), legacy_config_path()
    if durable.exists():
        if legacy.exists() and (str(durable), str(legacy)) not in _WARNED_SHADOWED:
            _WARNED_SHADOWED.add((str(durable), str(legacy)))
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
CONTENT_CAPTURE_MODES = ("off", "preview", "full")
GPU_VENDORS = ("auto", "amd", "nvidia", "off")
VALID_TEMPORALITIES = ("cumulative", "delta")
VALID_HISTOGRAMS = ("explicit", "exponential")
VALID_METRIC_NAME_COMPAT = ("off", "claude_code", "both")


def _lower(text: str) -> str:
    return text.strip().lower()


def _upper(text: str) -> str:
    return text.strip().upper()


def _field(default: Any, doc: str, group: str, **rule: Any) -> Any:
    """One config knob: its default, its one-line reference text, the
    Settings-tab group it belongs to, and how a yaml or env value is coerced.

    ``rule`` keys: ``bounds=(lo, hi)`` for numbers (checked in
    ``__post_init__``; ``hi=None`` is open), and for :func:`_parse_scalar`
    ``choices`` (the accepted values), ``normalize`` (applied to the parsed
    value first), ``aliases`` (spellings mapped onto a choice) and
    ``bool_as=(when_false, when_true)`` (what a YAML 1.1 bare ``off`` / ``on``,
    read as a bool, stands for). The reference tables, the Settings tab and
    the loader all read this metadata, so a knob cannot be undocumented.
    """
    return field(default=default, metadata=dict(doc=doc, group=group, **rule))


# ``max_export_batch_size`` may not exceed ``max_queue_size`` (the SDK raises):
# (batch field, queue field).
_BATCH_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("span_batch_max_export_batch_size", "span_batch_max_queue_size"),
    ("log_batch_max_export_batch_size", "log_batch_max_queue_size"),
)


def _validate_numeric_bounds(cfg: Any) -> None:
    """Warn about and reset every numeric field outside its ``bounds`` (#487).

    Out-of-range values (a ``0`` queue size, a negative timeout, a sample rate
    above 1) used to reach the SDK, which raises inside the pipeline init:
    every backend then logged "traces init failed" and the run exported
    nothing. Runs from ``__post_init__`` so yaml, env and direct construction
    all pass through it. Strings that are not numbers never get here: the
    loader rejected them already. A bool is not a number for this purpose;
    ``None`` stays ``None`` for the Optional fields.
    """
    for f in dataclasses.fields(cfg):
        bounds = f.metadata.get("bounds")
        value = getattr(cfg, f.name)
        if bounds is None or value is None:
            continue
        lo, hi = bounds
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok and (value < lo or (hi is not None and value > hi)):
            ok = False
        if ok:
            continue
        want = f"at least {lo}" if hi is None else f"between {lo} and {hi}"
        logger.warning(
            f"[hermes-otel] {f.name}={value!r} must be {want}; using "
            + ("null" if f.default is None else repr(f.default))
        )
        object.__setattr__(cfg, f.name, f.default)
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
    """Frozen configuration object passed through the plugin.

    Every field is declared with :func:`_field`: default, reference text,
    Settings-tab group and coercion rule in one place. ``FIELD_DOCS``,
    ``NUMERIC_BOUNDS``, ``FIELD_GROUPS`` and ``FIELD_RULES`` below are views
    over that metadata.
    """

    enabled: bool = _field(True, "Master kill switch; `false` unloads every hook", "General")
    sample_rate: Optional[float] = _field(
        None,
        "Parent-based trace-ID ratio 0.0–1.0; `null` = AlwaysOn (no sampling)",
        "General",
        bounds=(0.0, 1.0),
    )
    root_span_ttl_ms: int = _field(
        600_000,
        "Orphan-sweep TTL: a turn root older than this with no end hook is closed",
        "Export and batching",
        bounds=(1, None),
    )
    # A delegation (``subagent.*``) span outlives its parent's turn: Hermes
    # runs top-level delegations in the background (#392). Hermes sets no
    # child timeout by default, hence a day.
    subagent_span_ttl_ms: int = _field(
        86_400_000,
        "Delegation-span TTL: a `subagent.*` span whose `subagent_stop` never arrives is closed after this (it outlives the parent's turn by design)",
        "Export and batching",
        bounds=(1, None),
    )
    flush_interval_ms: int = _field(
        60_000,
        "Metrics export cadence (PeriodicExportingMetricReader)",
        "Export and batching",
        bounds=(1, None),
    )
    preview_max_chars: int = _field(
        1200,
        "Cap on preview strings (tool args/results, user message, assistant response)",
        "Content capture",
        bounds=(1, None),
    )
    capture_previews: bool = _field(
        True,
        "Deprecated spelling of `content_capture: off` (when `false`); kept consistent with `content_capture`",
        "Content capture",
    )
    tool_input_preview_max_chars: Optional[int] = _field(
        None,
        "Per-category cap for tool args previews; `null` = `preview_max_chars`",
        "Content capture",
        bounds=(1, None),
    )
    tool_output_preview_max_chars: Optional[int] = _field(
        None,
        "Per-category cap for tool result previews; `null` = `preview_max_chars`",
        "Content capture",
        bounds=(1, None),
    )
    llm_input_preview_max_chars: Optional[int] = _field(
        None,
        "Per-category cap for LLM input previews; `null` = `preview_max_chars`",
        "Content capture",
        bounds=(1, None),
    )
    llm_output_preview_max_chars: Optional[int] = _field(
        None,
        "Per-category cap for LLM output previews; `null` = `preview_max_chars`",
        "Content capture",
        bounds=(1, None),
    )
    headers: Optional[Dict[str, str]] = _field(
        None,
        "Extra HTTP headers on every OTLP request; per-backend `headers:` are merged onto these",
        "General",
    )
    global_tags: Optional[Dict[str, Scalar]] = _field(
        None,
        "Deprecated: merged into `resource_attributes` at load (which wins on a key conflict)",
        "General",
    )
    resource_attributes: Optional[Dict[str, Scalar]] = _field(
        None,
        "Merged into the Resource on top of the defaults `service.name=hermes-agent`, `service.instance.id` (per-process UUID), `service.version`, `process.pid`",
        "General",
    )
    project_name: Optional[str] = _field(
        None,
        "`openinference.project.name` on the Resource (Phoenix project); overrides `OTEL_PROJECT_NAME`",
        "General",
    )
    service_name_from_profile: bool = _field(
        False,
        "A named profile exports as `service.name=<profile>` with `service.namespace=hermes-agent`; the default profile stays `hermes-agent`. `resource_attributes.service.name` still wins",
        "General",
    )
    # ── BatchSpanProcessor tunables (non-blocking export) ───────────────
    span_batch_max_queue_size: int = _field(
        2048, "Max buffered spans per backend before drops", "Export and batching", bounds=(1, None)
    )
    span_batch_schedule_delay_ms: int = _field(
        1000, "BatchSpanProcessor worker wake-up cadence", "Export and batching", bounds=(1, None)
    )
    # None = auto: 512, or 64 when content_capture is "full" (large spans
    # would otherwise make a single OTLP POST bigger than most receivers
    # accept). See effective_export_batch_size().
    span_batch_max_export_batch_size: Optional[int] = _field(
        None,
        "Max spans per OTLP POST; `null` = 512, or 64 when `content_capture` is `full`",
        "Export and batching",
        bounds=(1, None),
    )
    span_batch_export_timeout_ms: int = _field(
        30_000, "Per-export HTTP timeout", "Export and batching", bounds=(1, None)
    )
    force_flush_on_session_end: bool = _field(
        True,
        "Flush every backend's span queue at the end of each turn, from a background thread (500 ms per backend, coalesced)",
        "Export and batching",
    )
    # Bounded and coalesced: a healthy backend receives the turn in well
    # under it, a stuck one costs at most this much per turn. 1500 since
    # #233: the flush includes the metric and log providers.
    force_flush_wait_ms: int = _field(
        1500,
        "How long the turn waits for that background flush (spans, then metrics and logs on a second thread) before returning to Hermes; a one-shot run exits right after, so this bounds what it exports; `0` = do not wait",
        "Export and batching",
        bounds=(0, None),
    )
    capture_conversation_history: bool = _field(
        False, "Attach the full message JSON to `llm.*` spans", "Content capture"
    )
    conversation_history_max_chars: int = _field(
        20_000, "JSON cap when conversation capture is on", "Content capture", bounds=(1, None)
    )
    # ── Content capture ─────────────────────────────────────────────────
    # ``capture_previews`` / ``capture_full_prompts`` / ``capture_full_responses``
    # are the pre-1.11 spellings; load_config keeps them consistent with
    # ``content_capture`` (see _reconcile_content_capture) and hook code asks
    # ``content_mode()`` / ``captures_full_prompts()`` etc. below.
    content_capture: str = _field(
        "full",
        "`full` (default): complete prompt and response on every `api.*` span · `preview`: clipped previews only · `off`: no content, metadata only; see [Conversation capture](/configuration/conversation-capture)",
        "Content capture",
        choices=CONTENT_CAPTURE_MODES,
        normalize=_lower,
        bool_as=("off", "full"),
    )
    capture_full_prompts: bool = _field(
        True,
        "Deprecated: derived from `content_capture`; `false` keeps prompts as previews in `full` mode",
        "Content capture",
    )
    capture_full_responses: bool = _field(
        True,
        "Deprecated: derived from `content_capture`; `false` keeps responses as previews in `full` mode",
        "Content capture",
    )
    capture_sender_id: bool = _field(
        False,
        "Gateway sessions add `hermes.sender.id` and `user.id` (`platform:sender`)",
        "Content capture",
    )
    # ── OTel logs signal ────────────────────────────────────────────────
    # Off by default because attaching to the root logger is invasive:
    # third-party libraries' logs are also exported.
    capture_logs: bool = _field(
        False,
        "Attach an OTel LoggingHandler to Python logging; see [OTel logs](/configuration/logs)",
        "Logs",
    )
    log_level: str = _field(
        "INFO",
        "Handler level: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`",
        "Logs",
        normalize=_upper,
    )
    log_attach_logger: Optional[str] = _field(
        None, "Logger to attach to; `null` = root, `hermes_otel` = the plugin only", "Logs"
    )
    log_exclude_loggers: Tuple[str, ...] = _field(
        DEFAULT_LOG_EXCLUDE_LOGGERS,
        "Logger-name prefixes never forwarded (loop guard + Hermes' noisy third-party loggers); `logs.exclude_loggers`",
        "Logs",
    )
    log_logger_levels: Optional[Dict[str, str]] = _field(
        None,
        "Per-logger minimum level, e.g. `{gateway.config: WARNING}`; `logs.logger_levels`",
        "Logs",
    )
    log_only_in_turn: bool = _field(
        False, "Drop records the plugin cannot attribute to a session; `logs.only_in_turn`", "Logs"
    )
    log_max_attribute_length: int = _field(
        4096,
        "Longest exported string attribute on a log record (characters); `logs.max_attribute_length`",
        "Logs",
        bounds=(1, None),
    )
    log_batch_schedule_delay_ms: int = _field(
        1000,
        "Log batch processor: export every N ms; `logs.batch.schedule_delay_ms`",
        "Logs",
        bounds=(1, None),
    )
    log_batch_max_queue_size: int = _field(
        2048,
        "Log batch processor: queued records before drops; `logs.batch.max_queue_size`",
        "Logs",
        bounds=(1, None),
    )
    log_batch_max_export_batch_size: int = _field(
        512,
        "Log batch processor: records per export request; `logs.batch.max_export_batch_size`",
        "Logs",
        bounds=(1, None),
    )
    log_batch_export_timeout_ms: int = _field(
        30_000,
        "Log batch processor: per-export timeout; `logs.batch.export_timeout_ms`",
        "Logs",
        bounds=(1, None),
    )
    log_live_min_level: str = _field(
        "INFO",
        "Floor for the dashboard's live Logs tab (`INFO` default; the OTLP path keeps `log_level`); `logs.live_min_level`",
        "Logs",
        normalize=_upper,
    )
    log_events: bool = _field(
        False,
        "Emit structured `hermes.*` / GenAI events from the hooks (Phase 2); `logs.events.enabled`",
        "Logs",
    )
    log_events_content: str = _field(
        "inherit",
        "Content on events: `inherit` (follow `content_capture`) / `full` / `preview` / `off`; `logs.events.content`",
        "Logs",
        choices=LOG_EVENTS_CONTENT_MODES,
        normalize=_lower,
        bool_as=("off", "full"),
    )
    # ── OTel GenAI semantic-convention metrics ──────────────────────────
    emit_genai_metrics: bool = _field(
        True,
        "Also emit the OTel GenAI spec metrics (`gen_ai.client.*`, `gen_ai.agent.*`)",
        "Spans and metrics",
    )
    # None = the OTel SDK default (OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE
    # when set, else cumulative); backend types with a known preference
    # (SigNoz, Uptrace) default to it (#233).
    metrics_temporality: Optional[str] = _field(
        None,
        "Metric temporality for OTLP export: `cumulative` (Prometheus family) or `delta` (Datadog, New Relic, Logfire); unset = SDK default; a backend entry's own value wins",
        "Spans and metrics",
        choices=VALID_TEMPORALITIES,
        normalize=_lower,
    )
    metrics_histogram: str = _field(
        "explicit",
        "Histogram aggregation: `explicit` (spec bucket boundaries) or `exponential` (base-2, for backends that accept it)",
        "Spans and metrics",
        choices=VALID_HISTOGRAMS,
        normalize=_lower,
    )
    metrics_label_limit: int = _field(
        100,
        "Distinct values a metric label such as `model` may take per process before the rest fold into `other` (0 = no cap)",
        "Spans and metrics",
        bounds=(0, None),
        normalize=lambda n: max(0, n),
    )
    metric_name_compat: str = _field(
        "off",
        "`off` · `claude_code` · `both` — export session count, token usage, cost and active time under Claude Code's metric names, units and labels (`claude_code.*`) instead of (`claude_code`) or beside (`both`) the `hermes.*` twins, so dashboards built for Claude Code read Hermes; see [Claude Code dashboards](/guides/claude-code-dashboards)",
        "Spans and metrics",
        choices=VALID_METRIC_NAME_COMPAT,
        normalize=_lower,
        aliases={"false": "off", "none": "off", "no": "off", "": "off"},
    )
    metric_name_compat_session_id: bool = _field(
        False,
        "Also put `session.id` on the `claude_code.*` instruments, as Claude Code does by default (per-session panels need it; one series per session)",
        "Spans and metrics",
    )
    skill_spans: bool = _field(
        True,
        "Open a `skill.<name>` span on each successful skill load, closed at turn end",
        "Spans and metrics",
    )
    remote_parent: bool = _field(
        True,
        "Parent the turn root under the span named by `HERMES_OTEL_TRACEPARENT` (+ `HERMES_OTEL_TRACESTATE`) when an outer process exported them; `false` ignores the variables. The kanban worker hand-off (`TRACEPARENT`) is separate",
        "Spans and metrics",
    )
    probe_backends: bool = _field(
        True,
        "At init, open one TCP connection to each backend endpoint on a background thread and log reachable / unreachable per backend; once, never periodic, no bytes sent, exports never wait for it",
        "Spans and metrics",
    )
    discovery_prompt: bool = _field(
        False,
        "Register a system-prompt section advertising `hermes_otel:observability` (changes what the model sees every turn; opt-in)",
        "Spans and metrics",
    )
    # ── Live dashboard (local store) ────────────────────────────────────
    # $HERMES_HOME/hermes_otel_live.db (or HERMES_OTEL_LIVE_DB): the gateway
    # and dashboard processes see the same data, with NO external backend.
    dashboard_live: bool = _field(
        True,
        "Keep recent spans/metrics/logs in `$HERMES_HOME/hermes_otel_live.db` for the dashboard's Live mode",
        "Dashboard",
    )
    dashboard_live_max_spans: int = _field(
        1000,
        "Rows kept per kind (spans, metrics, logs) in the live store",
        "Dashboard",
        bounds=(1, None),
    )
    dashboard_live_retention_hours: float = _field(
        168.0,
        "Rows older than this are dropped from the live store (`0` = only the row cap applies)",
        "Dashboard",
        bounds=(0.0, None),
    )
    # ── Host metrics (CPU / GPU) ────────────────────────────────────────
    # Needs psutil (ships with hermes-agent); GPU readings additionally need
    # pynvml or amdsmi.
    host_metrics: bool = _field(
        False,
        "Sample CPU/GPU and emit `process.*` / `system.*` / `hw.*` metrics; see [Host & GPU metrics](/configuration/host-metrics)",
        "Host metrics",
    )
    host_metrics_gpu: str = _field(
        "auto",
        "`auto` · `amd` · `nvidia` · `off` — which GPU SDK to probe",
        "Host metrics",
        choices=GPU_VENDORS,
        normalize=_lower,
    )
    host_metrics_interval_ms: int = _field(
        1000, "Host sampling cadence (floor 50 ms)", "Host metrics", bounds=(1, None)
    )
    # ── MCP keepalive noise ─────────────────────────────────────────────
    # MCP Python SDK 2.x (Hermes v0.21.0+) emits a CLIENT span for every
    # JSON-RPC request, the periodic keepalive ``ping`` included; failed
    # pings are always kept.
    suppress_mcp_ping_spans: bool = _field(
        True, "Drop successful MCP keepalive `ping` spans before export", "Spans and metrics"
    )
    # ── Multi-backend fan-out ───────────────────────────────────────────
    backends: Optional[Tuple[BackendConfig, ...]] = _field(
        None, "Multi-backend fan-out list; see the `backends[]` section", "Backends"
    )

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


# ── Views over the field metadata ───────────────────────────────────────
# What the docs generator (scripts/gen_config_docs.py), the Settings tab
# (settings_report.py) and the loader read. Groups are listed in display
# order; within a group the fields keep their dataclass order.
FIELD_DOCS: Dict[str, str] = {f.name: f.metadata["doc"] for f in fields(HermesOtelConfig)}
NUMERIC_BOUNDS: Dict[str, Tuple[float, Optional[float]]] = {
    f.name: f.metadata["bounds"] for f in fields(HermesOtelConfig) if "bounds" in f.metadata
}
GROUP_ORDER: Tuple[str, ...] = (
    "General",
    "Content capture",
    "Export and batching",
    "Spans and metrics",
    "Logs",
    "Dashboard",
    "Host metrics",
    "Backends",
)
FIELD_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = tuple(
    (group, tuple(f.name for f in fields(HermesOtelConfig) if f.metadata["group"] == group))
    for group in GROUP_ORDER
)
_ungrouped = {f.metadata["group"] for f in fields(HermesOtelConfig)} - set(GROUP_ORDER)
if _ungrouped:  # a misspelt group would silently drop its fields from the Settings tab
    raise ValueError(f"HermesOtelConfig groups missing from GROUP_ORDER: {sorted(_ungrouped)}")

_RULE_KEYS = ("choices", "normalize", "aliases", "bool_as")
# Per field, the keyword rule :func:`_parse_scalar` applies to its yaml or env value.
FIELD_RULES: Dict[str, Dict[str, Any]] = {
    f.name: {k: f.metadata[k] for k in _RULE_KEYS if k in f.metadata}
    for f in fields(HermesOtelConfig)
}


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


def _parse_scalar(
    kind: Optional[str],
    value: Any,
    *,
    choices: Optional[Tuple[str, ...]] = None,
    normalize: Optional[Callable[[Any], Any]] = None,
    aliases: Optional[Dict[str, str]] = None,
    bool_as: Optional[Tuple[str, str]] = None,
) -> Any:
    """Parse one scalar (a yaml value or an env string) to ``kind``; ``None`` = invalid.

    The keyword rule is the field's (``FIELD_RULES``): ``normalize`` runs on
    the parsed value, ``aliases`` then map a spelling onto a choice, and a
    value outside ``choices`` is invalid. A bool where a string is expected is
    YAML 1.1 reading a bare ``off`` / ``on``: ``bool_as`` says what it stands
    for; without it the text is ``False`` / ``True``.
    """
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return True if text in _TRUE_STRINGS else False if text in _FALSE_STRINGS else None
    if kind in ("int", "float"):
        if isinstance(value, bool):
            return None  # bools are ints in python; reject explicitly
        try:
            number = value if isinstance(value, (int, float)) else float(str(value).strip())
            number = int(number) if kind == "int" else float(number)
        except (ValueError, OverflowError):
            return None
        return normalize(number) if normalize else number
    if kind == "str":
        if isinstance(value, bool) and bool_as:
            text = bool_as[1] if value else bool_as[0]
        else:
            text = str(value)
        if normalize:
            text = normalize(text)
        if aliases:
            text = aliases.get(text, text)
        return text if choices is None or text in choices else None
    if kind == "list":
        # yaml list, or a comma-separated string (the env-var spelling).
        if isinstance(value, (list, tuple)):
            return tuple(str(v).strip() for v in value if v is not None and str(v).strip())
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return None
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
        from .backend_config import coerce_backends  # its loader builds on this module

        return coerce_backends(value)
    if kind == "map":
        if isinstance(value, dict):
            return {str(k): expand_env_refs(v, where=key) for k, v in value.items()}
        if warn:
            logger.warning(f"[hermes-otel] config.yaml {key!r} must be a mapping; ignoring")
        return None
    parsed = _parse_scalar(kind, value, **FIELD_RULES.get(key, {}))
    if parsed is None and warn:
        logger.warning(
            f"[hermes-otel] config.yaml {key!r}: cannot use {value!r} as {kind}; "
            "keeping the default"
        )
    return parsed


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
                parsed = _parse_scalar(kind, raw_env, **FIELD_RULES[key])
                if parsed is None:
                    src["env_invalid"] = True
                    if warn:
                        logger.warning(
                            f"[hermes-otel] {src['env_var']}={raw_env!r} is not a valid {kind}; ignoring it"
                        )
                else:
                    values[key] = parsed
                    src["source"] = "env"

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


def normalize_temporality(
    value: Any, where: str = "metrics_temporality", *, warn: bool = True
) -> Optional[str]:
    """``cumulative`` / ``delta`` (case-insensitive) or ``None``; anything else warns and is dropped.

    The top-level field goes through :func:`_parse_scalar` with this same
    rule; this is for a backend entry's own ``metrics_temporality``, whose
    warning names the entry.
    """
    if value is None or not str(value).strip():
        return None
    parsed = _parse_scalar("str", value, **FIELD_RULES["metrics_temporality"])
    if parsed is None and warn:
        logger.warning(
            f"[hermes-otel] {where}={value!r} is not one of {'/'.join(VALID_TEMPORALITIES)}; ignoring"
        )
    return parsed


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
