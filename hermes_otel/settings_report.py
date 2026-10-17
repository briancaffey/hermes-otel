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
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import backends as _backends
from . import plugin_config as pc
from .backend_config import BackendConfig
from .backend_specs import BY_TYPE, SPECS, spec_for
from .plugin_config import (
    FIELD_DOCS,
    FIELD_GROUPS,
    HermesOtelConfig,
    content_mode,
    field_kinds,
    hermes_home,
)
from .redaction import REPORT_MASK as MASK
from .redaction import (
    is_header_secret_name,
    is_secret_name,
    is_secret_value,
    mask_env_value,
)

# Config keys that are not HermesOtelConfig fields but are read by another
# part of the plugin from the same file; listed so they are explained rather
# than flagged as typos.
KNOWN_EXTRA_KEYS: Dict[str, str] = {
    "query_backend": "Dashboard: the backend (by `name` or `type`) the tab queries by default",
}


def _mask(value: Any) -> Any:
    if value is None or value == "":
        return value
    return MASK


def _display_map(
    value: Optional[Dict[str, Any]], reveal: bool, headers: bool = False
) -> Optional[Dict[str, Any]]:
    """A mapping with its secret values masked; ``headers`` widens the match to
    header names (``x-honeycomb-team``) and masks per pair inside list values."""
    if value is None:
        return None
    if reveal:
        return dict(value)
    secret_name = is_header_secret_name if headers else is_secret_name
    return {
        k: (_mask(v) if secret_name(str(k)) or is_secret_value(v) else v) for k, v in value.items()
    }


# ── Backends ──────────────────────────────────────────────────────────────

# Credential fields per backend type, with the env vars each resolver falls
# back to when neither the inline value nor ``<field>_env`` is set
# (backends.py). Types absent here (phoenix, jaeger, tempo, lgtm, otlp) take
# credentials only through ``headers``.
_TYPE_CREDENTIALS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    s.type: {c.field: c.env for c in s.credentials} for s in SPECS if s.credentials
}
# Every credential field any resolver reads, and every other scalar an entry
# may carry, both derived (#501): a hand list here drifted twice (mlflow's
# ``experiment_id`` and opik's ``workspace`` were never reported; victoria's
# ``metrics_endpoint`` / ``logs_endpoint`` were dropped from the effective
# YAML, so pasting it back lost two of the three stores).
_ALL_CREDENTIAL_FIELDS: Tuple[str, ...] = tuple(
    f.name
    for f in dataclasses.fields(BackendConfig)
    if f.name in {c.field for s in SPECS for c in s.credentials}
)
# Fields the card reports under their own key rather than as a plain scalar.
_STRUCTURED_BACKEND_FIELDS = frozenset(
    {
        "type",
        "name",
        "ui_url",
        "headers",
        "traces",
        "metrics",
        "logs",
        "metrics_temporality",
        "root_usage",
        "log_overrides",
        "extra",
    }
)
_PLAIN_BACKEND_FIELDS: Tuple[str, ...] = tuple(
    f.name
    for f in dataclasses.fields(BackendConfig)
    if f.name not in _STRUCTURED_BACKEND_FIELDS
    and f.name not in _ALL_CREDENTIAL_FIELDS
    and not f.name.endswith("_env")
)


def _credential_report(
    bc: BackendConfig, field: str, raw: Dict[str, Any], reveal: bool, fallbacks: Tuple[str, ...]
) -> Dict[str, Any]:
    """How one credential field of a backend is supplied, without its value
    (the resolvers' own precedence, ``backends.choose_secret``)."""
    env_name = getattr(bc, f"{field}_env", None)
    choice = _backends.choose_secret(getattr(bc, field, None), env_name, fallbacks)
    out: Dict[str, Any] = {"field": field, "set": choice.origin is not None, "source": None}
    if choice.origin == "inline":
        raw_inline = raw.get(field)
        if isinstance(raw_inline, str) and "${" in raw_inline:
            out["source"] = f"file, expanded from {raw_inline.strip()}"
        else:
            out["source"] = "file (inline)"
    elif choice.origin is not None:
        out["source"] = f"env {choice.env_name}"
    elif env_name:
        out["source"] = f"env {env_name} (not set)"
    if choice.value is not None:
        out["value"] = choice.value if (reveal or not is_secret_name(field)) else MASK
    return out


# Keys the dashboard's query adapters read from a backend entry that are not
# ``BackendConfig`` fields (the loader drops them; the adapters read the raw
# yaml). Shown on the Settings card so a ``query_port`` is not invisible.
# The dashboard-only keys an entry may carry (what each adapter reads, from the
# manifest), shown on the card so a query-side setting is never invisible.
_QUERY_FIELDS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        ("query_port", "project_name", "service_name", "project_id", "org", "logs_stream")
        + tuple(
            k
            for s in SPECS
            for k in s.query_keys
            # a key that is also an exporter field (project, ui_url) is shown as such
            if k not in {f.name for f in dataclasses.fields(BackendConfig)}
        )
    )
)


def _origin(url: Optional[str]) -> Optional[str]:
    """``scheme://host[:port]`` of a URL, or ``None`` when it has no host."""
    if not url or not isinstance(url, str):
        return None
    parts = urllib.parse.urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _ui_link(bc: BackendConfig, raw: Dict[str, Any]) -> Dict[str, Any]:
    """The backend's web UI URL for the card, with where it came from.

    ``{"url": str|None, "source": "file"|"derived"|None, "note": str}``; the note
    explains the derivation (or why there is no link) and is shown as the
    link's tooltip. Never a guess: when the UI's port cannot be known from the
    endpoint the link is omitted and the note says to set ``ui_url``. The
    per-type facts are the spec's ``ui_*`` fields (``BackendSpec.ui_rule``
    documents the rules); an alias entry (``hyperdx``) derives nothing.
    """
    t = (bc.type or "").strip().lower()
    human = _backends.display_name(t)
    if bc.ui_url and bc.ui_url.strip():
        return {"url": bc.ui_url.strip(), "source": "file", "note": "ui_url from the config file"}
    hint = "set ui_url on this entry to link the card to the UI"
    spec = BY_TYPE.get(t)
    rule = spec.ui_rule if spec is not None else None

    def derived(url: str, note: str) -> Dict[str, Any]:
        return {"url": url, "source": "derived", "note": note}

    def none(note: str) -> Dict[str, Any]:
        return {"url": None, "source": None, "note": f"{note}; {hint}"}

    if rule == "origin":
        src = bc.endpoint or bc.base_url
        origin = _origin(src) or (spec.ui_url if not src else None)
        if origin:
            return derived(
                origin, f"derived from the endpoint: {human} serves its UI on the OTLP origin"
            )
        return none("no endpoint to derive a UI link from")
    if rule == "proxied":
        parts = urllib.parse.urlsplit((bc.endpoint or "").strip())
        query_port = raw.get("query_port") if spec.ui_on_query_port else None
        if parts.scheme and parts.hostname and query_port:
            return derived(
                f"{parts.scheme}://{parts.hostname}:{query_port}",
                f"derived from query_port: the {human} UI is served on its query port",
            )
        if parts.scheme and parts.netloc and parts.port is None:
            return derived(
                f"{parts.scheme}://{parts.netloc}",
                "derived from the endpoint host (no port, so a reverse proxy is assumed "
                "to serve the UI there)",
            )
        return none(f"the {human} UI is on a different port than OTLP ingest")
    if rule == "fixed":
        if spec.ui_by_region:
            region = (bc.region or "us").strip().lower()
            url = spec.ui_by_region.get(region, spec.ui_by_region["us"])
            return derived(url, f"the {human} UI for region {region}")
        base = _origin(bc.base_url) or spec.ui_url
        page = spec.ui_project_page
        entity, project = getattr(bc, "entity", None) or "", getattr(bc, "project", None) or ""
        if page is not None and entity and project:
            return derived(base + page.path.format(entity=entity, project=project), page.note)
        return derived(base, page.home_note if page is not None else f"the {human} UI")
    return none("a generic OTLP collector has no UI")


def _signal_report(bc: BackendConfig) -> Dict[str, Dict[str, Any]]:
    """Per signal: whether the type accepts it, what the entry says, and what
    the exporter does with it (``backends.signals_for``)."""
    support = _backends.signal_support(bc.type or "")
    exported = _backends.signals_for(bc)
    out: Dict[str, Dict[str, Any]] = {}
    for signal, override in (("traces", bc.traces), ("metrics", bc.metrics), ("logs", bc.logs)):
        out[signal] = {
            "supported": support[signal],
            "configured": "auto" if override is None else ("on" if override else "off"),
            "exported": exported[signal],
        }
    return out


def _temporality_report(bc: BackendConfig, top_level: Optional[str]) -> Dict[str, Any]:
    """The temporality this backend's metric reader gets, and which rule set it."""
    value, source = _backends.temporality_for(bc, top_level)
    return {"value": value, "source": source}


def _backend_summary(
    bc: BackendConfig, raw: Dict[str, Any], reveal: bool, top_temporality: Optional[str] = None
) -> Dict[str, Any]:
    t = (bc.type or "").strip().lower()
    spec = spec_for(t)
    fields = {f: getattr(bc, f) for f in _PLAIN_BACKEND_FIELDS if getattr(bc, f) is not None}
    type_fallbacks = _TYPE_CREDENTIALS.get(spec.type if spec is not None else t, {})
    credentials = []
    for f in _ALL_CREDENTIAL_FIELDS:
        fallbacks = type_fallbacks.get(f, ())
        if getattr(bc, f, None) or getattr(bc, f"{f}_env", None) or _any_env_set(fallbacks):
            credentials.append(_credential_report(bc, f, raw, reveal, fallbacks))
    query_fields = {k: raw[k] for k in _QUERY_FIELDS if raw.get(k) not in (None, "")}
    return {
        "type": bc.type,
        "name": bc.name or bc.type,
        "display_type": _backends.display_name(t),
        "known_type": spec is not None,
        "docs_path": spec.docs_path if spec is not None else None,
        "ui": _ui_link(bc, raw),
        "signals": _signal_report(bc),
        "metrics_temporality": _temporality_report(bc, top_temporality),
        "fields": fields,
        "query_fields": query_fields,
        "headers": _display_map(bc.headers, reveal, headers=True),
        "credentials": credentials,
        # Per-backend log settings from the entry's ``logs:`` mapping (#266).
        "log_overrides": _overrides_as_yaml(bc.log_overrides),
    }


def _overrides_as_yaml(overrides: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not overrides:
        return None
    out: Dict[str, Any] = {}
    for k, v in overrides.items():
        out[k] = list(v) if isinstance(v, tuple) else v
    return out


def _any_env_set(names: Tuple[str, ...]) -> bool:
    return any(os.getenv(n, "").strip() for n in names)


# ── Fields ────────────────────────────────────────────────────────────────


def _display_value(
    kind: str,
    key: str,
    value: Any,
    reveal: bool,
    raw_backends: Any,
    top_temporality: Optional[str] = None,
) -> Any:
    if value is None:
        return None
    if kind == "backends":
        raws = raw_backends if isinstance(raw_backends, list) else []
        return [
            _backend_summary(
                bc,
                raws[i] if i < len(raws) and isinstance(raws[i], dict) else {},
                reveal,
                top_temporality,
            )
            for i, bc in enumerate(value)
        ]
    if kind == "map":
        return _display_map(value, reveal, headers=(key == "headers"))
    if is_secret_name(key) and not reveal:
        return _mask(value)
    return value


def config_path_source(path: Optional[Path]) -> str:
    """Which rule of :func:`plugin_config.resolve_config_path_with_reason`
    would have picked ``path`` (``explicit`` for any other path).

    Judged against the home in force for this report (a profile's own under
    a multiplexed gateway or a ``?profile=`` dashboard request, #286).
    """
    if path is None:
        return "none"
    if os.environ.get(pc.CONFIG_PATH_ENV, "").strip():
        return "env"
    if path == pc.durable_config_path():
        return "durable"
    if path == pc.legacy_config_path():
        return "legacy"
    return "explicit"


def _deprecated_keys(data: Any) -> List[Dict[str, Any]]:
    """Top-level keys the file spells the old, flat way (``capture_logs``,
    ``log_level`` …) when the ``logs:`` block is the preferred spelling (#266).
    They keep working; the Settings tab marks them so a user can migrate."""
    if not isinstance(data, dict):
        return []
    flat = set(pc.LOG_FIELDS)
    return [
        {"key": k, "note": "flat spelling; the logs: block is the preferred form"}
        for k in data
        if isinstance(k, str) and k in flat
    ]


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

    Formatting over :func:`plugin_config.resolve_fields`, the loader's own
    precedence rules: a file value that fails coercion is ignored (and
    flagged), an env value that fails parsing is ignored (and flagged), and
    the env var wins over the file when both are valid.
    """
    kinds = field_kinds()
    defaults = HermesOtelConfig()
    group_of = {k: g for g, keys in FIELD_GROUPS for k in keys}
    raw_backends = yaml_data.get("backends")
    effective, sources = pc.resolve_fields(yaml_data, warn=False)
    top_temporality = effective.metrics_temporality

    reports: List[Dict[str, Any]] = []
    for f in dataclasses.fields(HermesOtelConfig):
        key, kind = f.name, kinds[f.name]
        src = sources[key]
        default = getattr(defaults, key)
        value = getattr(effective, key)
        secret = is_secret_name(key)
        if src.file_invalid:
            file_value: Any = str(src.file_raw)
        elif src.file_value is not None:
            file_value = _display_value(kind, key, src.file_value, reveal, raw_backends)
        else:
            file_value = None
        reports.append(
            {
                "key": key,
                "group": group_of.get(key, "Other"),
                "kind": kind,
                "description": FIELD_DOCS.get(key, ""),
                "env_var": src.env_var,
                "source": src.source,
                "default": _display_value(kind, key, default, reveal, None),
                "file_value": file_value,
                "file_invalid": src.file_invalid,
                "env_raw": (
                    None
                    if src.env_raw is None
                    else (src.env_raw if (reveal or not secret) else MASK)
                ),
                "env_invalid": src.env_invalid,
                "derived_from": list(src.derived_from) if src.derived_from else None,
                "value": _display_value(kind, key, value, reveal, raw_backends, top_temporality),
                "changed": value != default,
            }
        )
    return reports, effective


# ── Environment inventory ─────────────────────────────────────────────────

# (name, group, description). Groups: plugin, hermes, backend, otel.
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
        "HERMES_OTEL_TRACEPARENT",
        "plugin",
        "W3C traceparent of an outer span to parent every turn root under (#474); ignored with `remote_parent: false`",
    ),
    (
        "HERMES_OTEL_TRACESTATE",
        "plugin",
        "W3C tracestate carried with `HERMES_OTEL_TRACEPARENT`",
    ),
    (
        "HERMES_OTEL_LIVE_DB",
        "plugin",
        "Path of the live store SQLite file (default `$HERMES_HOME/hermes_otel_live.db`)",
    ),
    ("OTEL_PROJECT_NAME", "plugin", "Phoenix project name when `project_name` is not set"),
    (
        "HERMES_OTEL_QUERY_HOST",
        "plugin",
        "Dashboard: host that replaces every adapter's query host (a Docker-side name, #413)",
    ),
    (
        "HERMES_PLUGIN_PAYLOAD_MAX_CHARS",
        "hermes",
        'Hermes-side cap on the sanitised hook payload (`request["body"]`), default 50000; the plugin reads the raw `request_messages` so full capture is unaffected',
    ),
    *((name, "backend", description) for s in SPECS for name, description in s.env_vars),
    (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "otel",
        "OTel SDK: default OTLP endpoint for exporters created without one",
    ),
    ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "otel", "OTel SDK: traces endpoint override"),
    (
        "OTEL_EXPORTER_OTLP_HEADERS",
        "otel",
        "Ignored (#472): headers come from the config file only, so a token set here never reaches a credential-less backend",
    ),
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
            value = raw if reveal else mask_env_value(name, raw or "")
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
        if kind in pc.SCALAR_KINDS:
            add(pc.ENV_PREFIX + key.upper(), "override", FIELD_DOCS.get(key, ""), maps_to=key)
    for name, group, description in KNOWN_ENV_VARS:
        add(name, group, description)
    for name in sorted(os.environ):
        if name.startswith(("HERMES_OTEL_", "OTEL_")) and name not in seen:
            add(name, "other", "Set in the environment but not read by this plugin version")
    return out


# ── Raw and effective YAML ────────────────────────────────────────────────

# A ``key: value`` line, including one that opens a list item (``- key: v``)
# and quoted keys; group 2 is the bare key, group 4 the value (``|``/``>`` for
# a block scalar).
_YAML_KV_LINE = re.compile(
    r"^(\s*(?:-\s+)?)(?P<q>[\"']?)([A-Za-z0-9_.\-]+)(?P=q)(\s*:\s*)(\S.*)?$",
)
_YAML_BEARER = re.compile(r"((?:bearer|basic)\s+)\S+", re.I)
_YAML_BLOCK = re.compile(r"^[|>][-+0-9]*(\s+#.*)?$")


def redact_yaml_text(text: str) -> str:
    """Mask the value of every key that looks like a credential, keep the rest verbatim.

    A ``<field>_env`` key names an environment variable, not a secret, and a
    ``${VAR}`` reference is left as written; both stay readable. A secret key
    on a list-item line (``- secret_key: …``), inside a ``headers:`` mapping
    (``x-honeycomb-team: …``), or holding a block scalar (``secret_key: |``
    followed by indented lines) is masked as well (#286).
    """
    out = []
    headers_indent: Optional[int] = None  # inside a headers: mapping deeper than this
    block_indent: Optional[int] = None  # masking the continuation of a block scalar
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if stripped and indent > block_indent:
                out.append(" " * indent + MASK)
                continue
            block_indent = None
        if headers_indent is not None and stripped and indent <= headers_indent:
            headers_indent = None
        m = _YAML_KV_LINE.match(line)
        if m and not stripped.startswith("#"):
            prefix, key, sep, value = m.group(1), m.group(3), m.group(4), m.group(5) or ""
            key_indent = len(prefix.rstrip("- ")) if "-" in prefix else len(prefix)
            in_headers = headers_indent is not None and indent > headers_indent
            secret = (
                is_header_secret_name(key) if in_headers else is_secret_name(key)
            ) and not key.lower().endswith("_env")
            if key.lower() == "headers" and not value:
                headers_indent = key_indent
            if (
                secret
                and value
                and not value.lstrip().startswith("${")
                and not value.startswith("#")
            ):
                if _YAML_BLOCK.match(value):
                    block_indent = key_indent
                    out.append(line)
                    continue
                tail = re.search(r"\s+#.*$", value)
                comment = tail.group(0) if tail else ""
                out.append(f"{prefix}{m.group(2)}{key}{m.group(2)}{sep}{MASK}{comment}")
                continue
        out.append(_YAML_BEARER.sub(lambda mm: mm.group(1) + MASK, line))
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
    logs_block: Dict[str, Any] = {}
    logs_sources: set = set()
    for r in reports:
        key, value = r["key"], r["value"]
        if r["source"] == "env":
            src = f"env {r['env_var']}"
        elif r["source"] == "file":
            src = "file"
        else:
            src = "default"
        if key in pc.LOGS_FLAT_TO_BLOCK:
            # The logs: block is the preferred spelling (#266): render the flat
            # fields under it so the text pastes back as the block.
            path = pc.LOGS_FLAT_TO_BLOCK[key]
            node = logs_block
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = list(value) if isinstance(value, tuple) else value
            logs_sources.add(src)
            continue
        if r["kind"] == "backends":
            if value:
                doc[key] = [_backend_as_yaml(b) for b in value]
        else:
            doc[key] = value
        sources[key] = src
    if logs_block:
        doc[pc.LOGS_BLOCK_KEY] = logs_block
        sources[pc.LOGS_BLOCK_KEY] = (
            next(iter(logs_sources)) if len(logs_sources) == 1 else "file, env and defaults"
        )
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
    if b.get("ui", {}).get("source") == "file":
        out["ui_url"] = b["ui"]["url"]
    for signal, state in (b.get("signals") or {}).items():
        configured = state.get("configured") if isinstance(state, dict) else state
        if configured != "auto":
            out[signal] = configured == "on"
    if b.get("log_overrides"):
        out["logs"] = b["log_overrides"]  # the mapping form implies the signal is on
    if b.get("metrics_temporality", {}).get("source") == "entry":
        out["metrics_temporality"] = b["metrics_temporality"]["value"]
    out.update(b.get("query_fields") or {})
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
    path, path_source = pc.resolve_config_path_with_reason()
    exists = bool(path and path.exists())
    raw_yaml = pc.load_yaml(path) if path is not None else {}
    yaml_data = pc.normalize_yaml_data(raw_yaml, notice=False) if path is not None else {}
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
        {"key": k, "note": KNOWN_EXTRA_KEYS.get(k)} for k in yaml_data if k not in pc.ALLOWED_KEYS
    ]
    # The flat spellings of the ``logs:`` block are read from the file as
    # written (normalisation folds the block onto them).
    deprecated = _deprecated_keys(raw_yaml)
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
            "path_source": path_source,
            "exists": exists,
            "parse_ok": parse_ok,
            "mtime": mtime,
            "raw": raw_text,
            "raw_error": raw_error,
            "durable_path": str(pc.durable_config_path()),
            "legacy_path": str(pc.legacy_config_path()),
            "unknown_keys": unknown,
            "deprecated_keys": deprecated,
            # The short fingerprint of the resolved configuration (#484): the
            # plugin logs the same value when it builds its pipeline, so a
            # match says this is the configuration the gateway runs.
            "fingerprint": effective.fingerprint()[: pc.SHORT_FINGERPRINT_CHARS],
        },
        "counts": counts,
        "groups": [g for g, _ in FIELD_GROUPS],
        "fields": reports,
        "effective_yaml": effective_yaml(reports, path if exists else None),
        "env": env_inventory(reveal=reveal),
        # Vendor credentials present without their OTEL_* opt-in; only relevant
        # in env-var mode (a backends: list bypasses that path entirely).
        "env_notices": [] if effective.backends else _backends.env_opt_in_hints(),
        "process": {
            "role": "dashboard",
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "plugin_version": _plugin_version(),
            "hermes_home": str(hermes_home()),
            "note": (
                "Resolved by the dashboard process from the same file and environment "
                "the gateway and CLI read at their start. A gateway started before an "
                "edit keeps its old values until it restarts, and with plugins.isolation: "
                "host the dashboard router runs in a plugin-host process whose environment "
                "can differ from the gateway's."
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
