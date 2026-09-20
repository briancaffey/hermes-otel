"""Effective-settings report for the dashboard's Settings tab.

Resolves the configuration exactly as :func:`plugin_config.load_config` does
(env var > config file > dataclass default) but keeps, for every field, where
its value came from and what the other sources said. Also inventories the
environment variables the plugin honours, redacts anything that looks like a
credential, and renders the raw config file plus an "effective" YAML with a
source comment per key.

Pure functions over the environment and the file system; no OTel imports, so
the dashboard process can build the report even when telemetry is disabled.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import plugin_config as pc
from .plugin_config import (
    FIELD_DOCS,
    FIELD_GROUPS,
    BackendConfig,
    HermesOtelConfig,
    content_mode,
    field_kinds,
    hermes_home,
)

MASK = "••••••"

# Config keys that are not HermesOtelConfig fields but are read by another
# part of the plugin from the same file; listed so they are explained rather
# than flagged as typos.
KNOWN_EXTRA_KEYS: Dict[str, str] = {
    "query_backend": "Dashboard: the backend (by `name` or `type`) the tab queries by default",
}

_SECRET_NAME = re.compile(
    r"(key|secret|token|password|passwd|dsn|authorization|credential|cookie)", re.I
)


def is_secret_name(name: str) -> bool:
    """Whether a key, field or env var name looks like it holds a credential."""
    return bool(_SECRET_NAME.search(name or ""))


def is_secret_value(value: Any) -> bool:
    """Bearer/Basic auth strings are secrets whatever their key is called."""
    return isinstance(value, str) and value.strip().lower().startswith(("bearer ", "basic "))


def _mask(value: Any) -> Any:
    if value is None or value == "":
        return value
    return MASK


def _display_map(value: Optional[Dict[str, Any]], reveal: bool) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if reveal:
        return dict(value)
    return {
        k: (_mask(v) if is_secret_name(str(k)) or is_secret_value(v) else v)
        for k, v in value.items()
    }


# ── Backends ──────────────────────────────────────────────────────────────

# Credential fields per backend type, with the env vars each resolver falls
# back to when neither the inline value nor ``<field>_env`` is set
# (backends.py). Types absent here (phoenix, jaeger, tempo, lgtm, otlp) take
# credentials only through ``headers``.
_TYPE_CREDENTIALS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "langfuse": {
        "public_key": ("OTEL_LANGFUSE_PUBLIC_API_KEY", "LANGFUSE_PUBLIC_KEY"),
        "secret_key": ("OTEL_LANGFUSE_SECRET_API_KEY", "LANGFUSE_SECRET_KEY"),
    },
    "signoz": {"ingestion_key": ("OTEL_SIGNOZ_INGESTION_KEY",)},
    "uptrace": {"dsn": ("OTEL_UPTRACE_DSN", "UPTRACE_DSN")},
    "openobserve": {
        "user": ("OTEL_OPENOBSERVE_USER", "OPENOBSERVE_USER"),
        "password": ("OTEL_OPENOBSERVE_PASSWORD", "OPENOBSERVE_PASSWORD"),
    },
    "parseable": {"api_key": ("OTEL_PARSEABLE_API_KEY", "PARSEABLE_API_KEY")},
    "honeycomb": {"api_key": ("OTEL_HONEYCOMB_API_KEY", "HONEYCOMB_API_KEY")},
    "weave": {
        "api_key": ("WANDB_API_KEY",),
        "entity": ("WANDB_ENTITY", "DEFAULT_WANDB_ENTITY"),
        "project": ("WANDB_PROJECT", "DEFAULT_WANDB_PROJECT"),
    },
}
_ALL_CREDENTIAL_FIELDS = (
    "public_key",
    "secret_key",
    "ingestion_key",
    "dsn",
    "user",
    "password",
    "api_key",
    "entity",
    "project",
)

_PLAIN_BACKEND_FIELDS = (
    "endpoint",
    "base_url",
    "stream_name",
    "traces_dataset",
    "metrics_dataset",
    "logs_dataset",
    "dataset",
    "region",
)


def _credential_report(
    bc: BackendConfig, field: str, raw: Dict[str, Any], reveal: bool, fallbacks: Tuple[str, ...]
) -> Dict[str, Any]:
    """How one credential field of a backend is supplied, without its value."""
    inline = getattr(bc, field, None)
    raw_inline = raw.get(field)
    env_name = getattr(bc, f"{field}_env", None)
    out: Dict[str, Any] = {"field": field, "set": False, "source": None}
    secret = is_secret_name(field)
    if inline:
        out["set"] = True
        if isinstance(raw_inline, str) and "${" in raw_inline:
            out["source"] = f"file, expanded from {raw_inline.strip()}"
        else:
            out["source"] = "file (inline)"
        out["value"] = inline if (reveal or not secret) else MASK
        return out
    if env_name:
        present = bool(os.getenv(env_name, "").strip())
        out["set"] = present
        out["source"] = f"env {env_name}" + ("" if present else " (not set)")
        if present:
            out["value"] = os.getenv(env_name) if (reveal or not secret) else MASK
        return out
    for name in fallbacks:
        if os.getenv(name, "").strip():
            out["set"] = True
            out["source"] = f"env {name}"
            out["value"] = os.getenv(name) if (reveal or not secret) else MASK
            return out
    return out


def _backend_summary(bc: BackendConfig, raw: Dict[str, Any], reveal: bool) -> Dict[str, Any]:
    signals = {
        s: ("on" if v is True else "off" if v is False else "auto")
        for s, v in (("traces", bc.traces), ("metrics", bc.metrics), ("logs", bc.logs))
    }
    fields = {f: getattr(bc, f) for f in _PLAIN_BACKEND_FIELDS if getattr(bc, f) is not None}
    type_fallbacks = _TYPE_CREDENTIALS.get((bc.type or "").lower(), {})
    credentials = []
    for f in _ALL_CREDENTIAL_FIELDS:
        fallbacks = type_fallbacks.get(f, ())
        if getattr(bc, f, None) or getattr(bc, f"{f}_env", None) or _any_env_set(fallbacks):
            credentials.append(_credential_report(bc, f, raw, reveal, fallbacks))
    return {
        "type": bc.type,
        "name": bc.name or bc.type,
        "signals": signals,
        "fields": fields,
        "headers": _display_map(bc.headers, reveal),
        "credentials": credentials,
    }


def _any_env_set(names: Tuple[str, ...]) -> bool:
    return any(os.getenv(n, "").strip() for n in names)


# ── Fields ────────────────────────────────────────────────────────────────


def _display_value(kind: str, key: str, value: Any, reveal: bool, raw_backends: Any) -> Any:
    if value is None:
        return None
    if kind == "backends":
        raws = raw_backends if isinstance(raw_backends, list) else []
        return [
            _backend_summary(
                bc, raws[i] if i < len(raws) and isinstance(raws[i], dict) else {}, reveal
            )
            for i, bc in enumerate(value)
        ]
    if kind == "map":
        return _display_map(value, reveal)
    if is_secret_name(key) and not reveal:
        return _mask(value)
    return value


def _config_path_source(path: Optional[Path]) -> str:
    """Which rule of :func:`resolve_config_path` picked ``path``."""
    if path is None:
        return "none"
    if os.environ.get(pc.CONFIG_PATH_ENV, "").strip():
        return "env"
    if path == pc.DURABLE_CONFIG_PATH:
        return "durable"
    if path == pc.DEFAULT_CONFIG_PATH:
        return "legacy"
    return "explicit"


def _plugin_version() -> Optional[str]:
    try:
        text = (Path(__file__).resolve().parent / "plugin.yaml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^version:\s*[\"']?([^\"'\n]+)", text, re.M)
    return m.group(1).strip() if m else None


def field_reports(
    yaml_data: Dict[str, Any], reveal: bool = False
) -> Tuple[List[Dict[str, Any]], HermesOtelConfig]:
    """One entry per HermesOtelConfig field with value, default and source.

    Mirrors ``load_config``: a file value that fails coercion is ignored (and
    flagged), an env value that fails parsing is ignored (and flagged), and
    the env var wins over the file when both are valid.
    """
    kinds = field_kinds()
    defaults = HermesOtelConfig()
    group_of = {k: g for g, keys in FIELD_GROUPS for k in keys}
    values: Dict[str, Any] = {}
    reports: List[Dict[str, Any]] = []
    raw_backends = yaml_data.get("backends")

    for f in dataclasses.fields(HermesOtelConfig):
        key, kind = f.name, kinds[f.name]
        default = getattr(defaults, key)
        entry: Dict[str, Any] = {
            "key": key,
            "group": group_of.get(key, "Other"),
            "kind": kind,
            "description": FIELD_DOCS.get(key, ""),
            "env_var": pc._ENV_PREFIX + key.upper() if kind in pc._SCALAR_KINDS else None,
            "source": "default",
            "default": _display_value(kind, key, default, reveal, None),
            "file_value": None,
            "file_invalid": False,
            "env_raw": None,
            "env_invalid": False,
        }
        value = default

        if key in yaml_data and yaml_data[key] is not None:
            coerced = pc._coerce_from_yaml(key, yaml_data[key])
            if coerced is None:
                entry["file_invalid"] = True
                entry["file_value"] = str(yaml_data[key])
            else:
                value = coerced
                entry["source"] = "file"
                entry["file_value"] = _display_value(kind, key, coerced, reveal, raw_backends)

        if entry["env_var"]:
            raw_env = os.getenv(entry["env_var"], "").strip()
            if raw_env:
                parsed = pc._parse_scalar(kind, key, raw_env)
                secret = is_secret_name(key)
                entry["env_raw"] = raw_env if (reveal or not secret) else MASK
                if parsed is None:
                    entry["env_invalid"] = True
                else:
                    value = parsed
                    entry["source"] = "env"

        values[key] = value
        entry["derived_from"] = None
        reports.append(entry)

    # content_capture and the legacy booleans are kept consistent by the
    # loader; mirror that so the tab shows what the hooks apply, and say
    # which key decided it.
    explicit = {r["key"]: values[r["key"]] for r in reports if r["source"] != "default"}
    reconciled = dict(explicit)
    pc._reconcile_content_capture(reconciled)
    by_key = {r["key"]: r for r in reports}
    if "content_capture" in explicit:
        # content_capture wins: every legacy flag is derived from it, even one
        # that is also written in the file or the environment.
        driver = ["content_capture"]
        derived_keys = list(pc._LEGACY_CONTENT_KEYS)
    else:
        driver = [k for k in pc._LEGACY_CONTENT_KEYS if k in explicit]
        derived_keys = [
            k for k in ("content_capture",) + pc._LEGACY_CONTENT_KEYS if k not in explicit
        ]
    for key in ("content_capture",) + pc._LEGACY_CONTENT_KEYS:
        if key in reconciled:
            values[key] = reconciled[key]
    if driver:
        for key in derived_keys:
            if key in reconciled:
                by_key[key]["source"] = by_key[driver[0]]["source"]
                by_key[key]["derived_from"] = driver

    for entry in reports:
        key, kind = entry["key"], entry["kind"]
        entry["value"] = _display_value(kind, key, values[key], reveal, raw_backends)
        entry["changed"] = values[key] != getattr(defaults, key)

    return reports, dataclasses.replace(defaults, **values)


# ── Environment inventory ─────────────────────────────────────────────────

# (name, group, description). Groups: plugin, hermes, backend, langsmith, otel.
KNOWN_ENV_VARS: Tuple[Tuple[str, str, str], ...] = (
    (
        "HERMES_HOME",
        "plugin",
        "Hermes home; the config file, live store and debug log live under it",
    ),
    (
        "HERMES_OTEL_CONFIG",
        "plugin",
        "Explicit config file path; wins over `$HERMES_HOME/hermes_otel.yaml`",
    ),
    (
        "HERMES_OTEL_DEBUG",
        "plugin",
        "Write the plugin's hook-by-hook debug log to `$HERMES_HOME/plugins/hermes_otel/debug.log`",
    ),
    (
        "HERMES_OTEL_LIVE_DB",
        "plugin",
        "Path of the live store SQLite file (default `$HERMES_HOME/hermes_otel_live.db`)",
    ),
    ("OTEL_PROJECT_NAME", "plugin", "Phoenix project name when `project_name` is not set"),
    (
        "HERMES_PLUGIN_PAYLOAD_MAX_CHARS",
        "hermes",
        'Hermes-side cap on the sanitised hook payload (`request["body"]`), default 50000; the plugin reads the raw `request_messages` so full capture is unaffected',
    ),
    ("OTEL_PHOENIX_ENDPOINT", "backend", "Single-backend mode: Phoenix OTLP traces URL"),
    ("OTEL_LANGFUSE_ENDPOINT", "backend", "Single-backend mode: Langfuse OTLP traces URL"),
    ("LANGFUSE_BASE_URL", "backend", "Langfuse base URL (alternative to the endpoint)"),
    ("LANGFUSE_PUBLIC_KEY", "backend", "Langfuse public key fallback"),
    ("LANGFUSE_SECRET_KEY", "backend", "Langfuse secret key fallback"),
    ("OTEL_LANGFUSE_PUBLIC_API_KEY", "backend", "Langfuse public key (preferred name)"),
    ("OTEL_LANGFUSE_SECRET_API_KEY", "backend", "Langfuse secret key (preferred name)"),
    ("OTEL_SIGNOZ_ENDPOINT", "backend", "Single-backend mode: SigNoz OTLP URL"),
    ("OTEL_SIGNOZ_INGESTION_KEY", "backend", "SigNoz Cloud ingestion key"),
    ("OTEL_JAEGER_ENDPOINT", "backend", "Single-backend mode: Jaeger OTLP URL"),
    ("OTEL_TEMPO_ENDPOINT", "backend", "Single-backend mode: Tempo / LGTM OTLP URL"),
    ("OTEL_UPTRACE_ENDPOINT", "backend", "Single-backend mode: Uptrace OTLP URL"),
    ("OTEL_UPTRACE_DSN", "backend", "Uptrace DSN (preferred name)"),
    ("UPTRACE_DSN", "backend", "Uptrace DSN fallback"),
    ("OTEL_OPENOBSERVE_ENDPOINT", "backend", "Single-backend mode: OpenObserve OTLP URL"),
    ("OTEL_OPENOBSERVE_STREAM", "backend", "OpenObserve stream name (default `default`)"),
    ("OTEL_OPENOBSERVE_USER", "backend", "OpenObserve user (preferred name)"),
    ("OTEL_OPENOBSERVE_PASSWORD", "backend", "OpenObserve password (preferred name)"),
    ("OPENOBSERVE_USER", "backend", "OpenObserve user fallback"),
    ("OPENOBSERVE_PASSWORD", "backend", "OpenObserve password fallback"),
    ("OTEL_PARSEABLE_ENDPOINT", "backend", "Single-backend mode: Parseable OTLP URL"),
    ("OTEL_PARSEABLE_API_KEY", "backend", "Parseable API key (preferred name)"),
    ("PARSEABLE_API_KEY", "backend", "Parseable API key fallback"),
    (
        "PARSEABLE_TRACES_DATASET",
        "backend",
        "Parseable dataset for traces (default `hermes-traces`)",
    ),
    (
        "PARSEABLE_METRICS_DATASET",
        "backend",
        "Parseable dataset for metrics (default `hermes-metrics`)",
    ),
    ("PARSEABLE_LOGS_DATASET", "backend", "Parseable dataset for logs (default `hermes-logs`)"),
    ("OTEL_HONEYCOMB_ENDPOINT", "backend", "Single-backend mode: Honeycomb OTLP URL"),
    ("OTEL_HONEYCOMB_API_KEY", "backend", "Honeycomb API key (preferred name)"),
    ("HONEYCOMB_API_KEY", "backend", "Honeycomb API key fallback"),
    ("OTEL_WEAVE_ENDPOINT", "backend", "Single-backend mode: W&B Weave OTLP URL"),
    ("WANDB_OTLP_ENDPOINT", "backend", "Weave OTLP URL fallback"),
    ("OTEL_WEAVE_BASE_URL", "backend", "Weave base URL"),
    ("WANDB_BASE_URL", "backend", "Weave base URL fallback"),
    ("WANDB_API_KEY", "backend", "Weave API key"),
    ("WANDB_ENTITY", "backend", "Weave entity"),
    ("WANDB_PROJECT", "backend", "Weave project"),
    ("DEFAULT_WANDB_ENTITY", "backend", "Weave entity fallback"),
    ("DEFAULT_WANDB_PROJECT", "backend", "Weave project fallback"),
    ("LANGSMITH_TRACING", "langsmith", "`true` enables the LangSmith backend (with an API key)"),
    ("LANGSMITH_API_KEY", "langsmith", "LangSmith API key"),
    (
        "LANGSMITH_ENDPOINT",
        "langsmith",
        "LangSmith API URL (default `https://api.smith.langchain.com`)",
    ),
    ("LANGSMITH_PROJECT", "langsmith", "LangSmith project (default `hermes-langsmith-otel`)"),
    ("LANGSMITH_WORKSPACE_ID", "langsmith", "LangSmith workspace id"),
    (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "otel",
        "OTel SDK: default OTLP endpoint for exporters created without one",
    ),
    ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "otel", "OTel SDK: traces endpoint override"),
    ("OTEL_EXPORTER_OTLP_HEADERS", "otel", "OTel SDK: headers added to every OTLP request"),
    ("OTEL_SERVICE_NAME", "otel", "OTel SDK: `service.name` resource attribute"),
    ("OTEL_RESOURCE_ATTRIBUTES", "otel", "OTel SDK: extra resource attributes, `k=v,k2=v2`"),
    ("OTEL_SDK_DISABLED", "otel", "OTel SDK: `true` turns every exporter into a no-op"),
)


def env_inventory(reveal: bool = False) -> List[Dict[str, Any]]:
    """Every environment variable the plugin honours, set or not.

    ``HERMES_OTEL_<FIELD>`` overrides come first (one per scalar field), then
    the known variables, then any other set ``HERMES_OTEL_*`` / ``OTEL_*``
    variable so nothing that is set is hidden.
    """
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(name: str, group: str, description: str, maps_to: Optional[str] = None) -> None:
        if name in seen:
            return
        seen.add(name)
        raw = os.environ.get(name)
        present = raw is not None and raw.strip() != ""
        value = None
        if present:
            value = MASK if (is_secret_name(name) or is_secret_value(raw)) and not reveal else raw
        out.append(
            {
                "name": name,
                "group": group,
                "description": description,
                "set": present,
                "value": value,
                "maps_to": maps_to,
            }
        )

    for key, kind in field_kinds().items():
        if kind in pc._SCALAR_KINDS:
            add(pc._ENV_PREFIX + key.upper(), "override", FIELD_DOCS.get(key, ""), maps_to=key)
    for name, group, description in KNOWN_ENV_VARS:
        add(name, group, description)
    for name in sorted(os.environ):
        if name.startswith(("HERMES_OTEL_", "OTEL_")) and name not in seen:
            add(name, "other", "Set in the environment but not read by this plugin version")
    return out


# ── Raw and effective YAML ────────────────────────────────────────────────

_YAML_SECRET_LINE = re.compile(
    r"^(\s*)([A-Za-z0-9_\-]*(?:key|secret|token|password|passwd|dsn|authorization|credential|cookie)[A-Za-z0-9_\-]*)(\s*:\s*)(\S.*)$",
    re.I,
)
_YAML_BEARER = re.compile(r"((?:bearer|basic)\s+)\S+", re.I)


def redact_yaml_text(text: str) -> str:
    """Mask the value of every key that looks like a credential, keep the rest verbatim.

    A ``<field>_env`` key names an environment variable, not a secret, and a
    ``${VAR}`` reference is left as written; both stay readable.
    """
    out = []
    for line in text.splitlines():
        m = _YAML_SECRET_LINE.match(line)
        value = m.group(4) if m else ""
        if (
            m
            and not m.group(2).lower().endswith("_env")
            and not value.lstrip().startswith("${")
            and not value.startswith("#")
        ):
            tail = re.search(r"\s+#.*$", value)
            comment = tail.group(0) if tail else ""
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{comment}"
        else:
            line = _YAML_BEARER.sub(lambda mm: mm.group(1) + MASK, line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _yaml_dump(value: Any) -> str:
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(value, sort_keys=False, default_flow_style=False, allow_unicode=True)
    except Exception:
        import json

        return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def effective_yaml(reports: List[Dict[str, Any]], config_path: Optional[Path]) -> str:
    """The resolved configuration as YAML, one comment per key naming its source.

    Backends are written back in the file's shape (credentials masked unless
    the report was built with ``reveal``), so the text can be pasted into
    ``hermes_otel.yaml`` as a starting point.
    """
    doc: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for r in reports:
        key, value = r["key"], r["value"]
        if r["kind"] == "backends":
            if value:
                doc[key] = [_backend_as_yaml(b) for b in value]
        else:
            doc[key] = value
        if r["source"] == "env":
            sources[key] = f"env {r['env_var']}"
        elif r["source"] == "file":
            sources[key] = "file"
        else:
            sources[key] = "default"
    body = _yaml_dump(doc)
    lines = []
    for line in body.splitlines():
        m = re.match(r"^([a-z_]+):", line)
        if m and m.group(1) in sources:
            line = f"{line}  # {sources[m.group(1)]}"
        lines.append(line)
    header = [
        "# hermes-otel effective configuration",
        f"# resolved {time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        + (f" from {config_path}" if config_path else " with no config file"),
        "# each top-level key notes where its value came from: env, file or default",
        "",
    ]
    return "\n".join(header + lines) + "\n"


def _backend_as_yaml(b: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": b["type"]}
    if b.get("name") and b["name"] != b["type"]:
        out["name"] = b["name"]
    out.update(b.get("fields") or {})
    for signal, state in (b.get("signals") or {}).items():
        if state != "auto":
            out[signal] = state == "on"
    if b.get("headers"):
        out["headers"] = b["headers"]
    for c in b.get("credentials") or []:
        if not c.get("set"):
            continue
        src = c.get("source") or ""
        if src.startswith("env "):
            out[f"{c['field']}_env"] = src.split()[1]
        elif "value" in c:
            out[c["field"]] = c["value"]
    return out


# ── The report ────────────────────────────────────────────────────────────


def build_settings_report(reveal: bool = False) -> Dict[str, Any]:
    """Everything the Settings tab shows, as one JSON-serialisable dict."""
    path = pc.resolve_config_path()
    exists = bool(path and path.exists())
    yaml_data = pc._load_yaml(path) if path is not None else {}
    reports, effective = field_reports(yaml_data, reveal=reveal)

    raw_text: Optional[str] = None
    raw_error: Optional[str] = None
    mtime: Optional[float] = None
    if exists:
        try:
            raw_text = path.read_text(encoding="utf-8")  # type: ignore[union-attr]
            mtime = path.stat().st_mtime  # type: ignore[union-attr]
        except OSError as e:
            raw_error = str(e)
        if raw_text is not None and not reveal:
            raw_text = redact_yaml_text(raw_text)
    parse_ok = not exists or bool(yaml_data) or (raw_text or "").strip() == ""

    unknown = [
        {"key": k, "note": KNOWN_EXTRA_KEYS.get(k)} for k in yaml_data if k not in pc._ALLOWED_KEYS
    ]
    # Counts are of values written somewhere; a field derived from another
    # (content_capture and its legacy flags) is not counted twice.
    written = [r for r in reports if not r.get("derived_from")]
    counts = {
        "env": sum(1 for r in written if r["source"] == "env"),
        "file": sum(1 for r in written if r["source"] == "file"),
        "default": sum(1 for r in reports if r["source"] == "default"),
        "changed": sum(1 for r in reports if r["changed"]),
    }
    return {
        "resolved_at": time.time(),
        "reveal": reveal,
        "config": {
            "path": str(path) if path else None,
            "path_source": _config_path_source(path),
            "exists": exists,
            "parse_ok": parse_ok,
            "mtime": mtime,
            "raw": raw_text,
            "raw_error": raw_error,
            "durable_path": str(pc.DURABLE_CONFIG_PATH),
            "legacy_path": str(pc.DEFAULT_CONFIG_PATH),
            "unknown_keys": unknown,
        },
        "counts": counts,
        "groups": [g for g, _ in FIELD_GROUPS],
        "fields": reports,
        "effective_yaml": effective_yaml(reports, path if exists else None),
        "env": env_inventory(reveal=reveal),
        "process": {
            "role": "dashboard",
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "plugin_version": _plugin_version(),
            "hermes_home": str(hermes_home()),
            "note": (
                "Resolved by the dashboard process from the same file and environment "
                "the gateway and CLI read at their start. A gateway started before an "
                "edit keeps its old values until it restarts."
            ),
        },
        "capture_summary": capture_summary(effective),
    }


def capture_summary(cfg: HermesOtelConfig) -> Dict[str, Any]:
    """One line for the tab header: what leaves the machine, content-wise."""
    mode = content_mode(cfg)
    if mode == "off":
        detail = "no prompt, tool or response content is recorded"
    elif mode == "full":
        parts = []
        if cfg.capture_full_prompts:
            parts.append("full prompts")
        if cfg.capture_full_responses:
            parts.append("full responses")
        detail = " and ".join(parts) + " on every api.* span, unclipped"
    else:
        detail = f"previews clipped at {cfg.preview_max_chars} characters"
    return {
        "mode": mode,
        "detail": detail,
        "conversation_history": cfg.capture_conversation_history,
        "logs": cfg.capture_logs,
        "sender_id": cfg.capture_sender_id,
    }
