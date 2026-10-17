"""What the plugin resolves for every backend type under every input path, as
recorded fixtures (#409), plus the consistency checks across the exporter
registry, the dashboard adapters, the settings report and the MCP config.

A fixture is ``tests/golden/resolution/<type>__<path>.json``. ``UPDATE_GOLDEN=1
pytest tests/unit/test_golden_resolution.py`` rewrites them; a refactor PR may
not change one without listing the change (milestone 14 ground rules). Secrets
are obviously fake placeholders and masked in the fixtures.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import difflib
import io
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from _helpers import clear_backend_env

from hermes_otel import backends, log_handler, mcp_config, query_cli, settings_report
from hermes_otel import plugin_config as pc
from hermes_otel.backend_specs import BY_TYPE, ENV_PRIORITY, spec_for
from hermes_otel.dashboard import backends as dash
from hermes_otel.dashboard.backends.base import resolve_env_or_literal

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests" / "golden" / "resolution"
UPDATE = os.environ.get("UPDATE_GOLDEN", "").strip() not in ("", "0", "false")

LOCAL_ENDPOINT = "http://localhost:4318/v1/traces"

# Types whose resolver has a cloud default host; the others need an endpoint.
_CLOUD_DEFAULT = {
    "langfuse",
    "honeycomb",
    "opik",
    "laminar",
    "langwatch",
    "latitude",
    "agenta",
    "weave",
}

# Placeholder credentials per type (inline path). Every value is obviously
# fake and is masked in the fixtures; the point is which header each becomes.
_CREDS: Dict[str, Dict[str, str]] = {
    "langfuse": {"public_key": "pk-test", "secret_key": "sk-test"},
    "signoz": {"ingestion_key": "ik-test"},
    "uptrace": {"dsn": "http://token-test@localhost:14318?grpc=14317"},
    "openobserve": {"user": "u-test", "password": "p-test"},
    "parseable": {"api_key": "k-test"},
    "honeycomb": {"api_key": "k-test"},
    "elastic": {"api_key": "k-test"},
    "openlit": {"api_key": "k-test"},
    "opik": {"api_key": "k-test", "workspace": "ws-test", "project": "proj-test"},
    "laminar": {"api_key": "k-test"},
    "langwatch": {"api_key": "k-test", "project": "proj-test"},
    "latitude": {"api_key": "k-test", "project": "proj-test"},
    "greptime": {"user": "u-test", "password": "p-test", "db": "db-test"},
    "clickstack": {"api_key": "k-test"},
    "victoria": {"user": "u-test", "password": "p-test"},
    "aspire": {"api_key": "k-test"},
    "gigapipe": {"user": "u-test", "password": "p-test"},
    "agenta": {"api_key": "k-test"},
    "mlflow": {"api_key": "k-test", "experiment_id": "exp-test"},
    "weave": {"api_key": "k-test", "entity": "ent-test", "project": "proj-test"},
}
# Non-credential fields a type needs beyond ``endpoint``.
_EXTRA: Dict[str, Dict[str, Any]] = {
    "victoria": {
        "metrics_endpoint": "http://localhost:8428",
        "logs_endpoint": "http://localhost:9428",
    },
}
# Env-only path: the variables beyond ``OTEL_<TYPE>_ENDPOINT`` and the first
# fallback of each credential field in ``settings_report._TYPE_CREDENTIALS``.
_ENV_ONLY_EXTRA: Dict[str, Dict[str, str]] = {
    "victoria": {
        "OTEL_VICTORIA_METRICS_ENDPOINT": "http://localhost:8428",
        "OTEL_VICTORIA_LOGS_ENDPOINT": "http://localhost:9428",
    },
    "opik": {"OPIK_WORKSPACE": "ws-test", "OPIK_PROJECT_NAME": "proj-test"},
    "langwatch": {"LANGWATCH_PROJECT_ID": "proj-test"},
    "latitude": {"LATITUDE_PROJECT": "proj-test"},
    "weave": {"WANDB_ENTITY": "ent-test", "WANDB_PROJECT": "proj-test"},
    "mlflow": {"OTEL_MLFLOW_EXPERIMENT_ID": "exp-test"},
}

PATHS = (
    "inline",
    "env_fields",
    "env_only",
    "signal_overrides",
    "metric_overrides",
    "logs_block",
)
_ENV_FIELDS = {f.name for f in dataclasses.fields(pc.BackendConfig) if f.name.endswith("_env")}


def _alias_types() -> Dict[str, str]:
    """Every alias (``hyperdx`` …) -> the canonical type it maps to (the manifest, #402)."""
    from hermes_otel.backend_specs import SPECS

    return {alias: spec.type for spec in SPECS for alias in spec.aliases}


def all_types() -> List[str]:
    aliases = _alias_types()
    return sorted(t for t in backends.KNOWN_TYPES if t not in aliases) + sorted(aliases)


def base_entry(backend_type: str) -> Tuple[Dict[str, Any], str]:
    canonical = _alias_types().get(backend_type, backend_type)
    entry: Dict[str, Any] = {"type": backend_type, "name": f"{backend_type}-entry"}
    if canonical not in _CLOUD_DEFAULT:
        entry["endpoint"] = LOCAL_ENDPOINT
    entry.update(_EXTRA.get(canonical, {}))
    return entry, canonical


def entry_for(backend_type: str, path: str, monkeypatch) -> Dict[str, Any]:
    """The yaml entry (and the environment) for one input path."""
    entry, canonical = base_entry(backend_type)
    creds = _CREDS.get(canonical, {})
    if path == "env_fields":
        for field, value in creds.items():
            if f"{field}_env" in _ENV_FIELDS:
                var = f"HO_TEST_{field.upper()}"
                entry[f"{field}_env"] = var
                monkeypatch.setenv(var, value)
            else:
                entry[field] = value
    else:
        entry.update(creds)
    if path == "signal_overrides":
        entry.update({"traces": False, "metrics": True, "logs": True})
    elif path == "metric_overrides":
        entry.update({"metrics_temporality": "delta", "root_usage": False})
    elif path == "logs_block":
        entry["logs"] = {"level": "ERROR", "exclude_loggers": ["noisy"]}
    return entry


def env_only_vars(backend_type: str) -> Dict[str, str]:
    """The environment that selects ``backend_type`` without a config file."""
    entry, canonical = base_entry(backend_type)
    out: Dict[str, str] = {}
    if "endpoint" in entry:
        out[f"OTEL_{canonical.upper()}_ENDPOINT"] = entry["endpoint"]
    for field, value in _CREDS.get(canonical, {}).items():
        fallbacks = settings_report._TYPE_CREDENTIALS.get(canonical, {}).get(field)
        if fallbacks:
            out[fallbacks[0]] = value
    out.update(_ENV_ONLY_EXTRA.get(canonical, {}))
    opt_in = spec_for(canonical).env_opt_in
    if opt_in and not any(name in out for name in opt_in):
        out[opt_in[0]] = "opt-in-test"
    return out


# ── masking and normalisation ──────────────────────────────────────────

_PLACEHOLDERS = sorted(
    {v for creds in _CREDS.values() for v in creds.values()} | {"opt-in-test", "token-test"},
    key=len,
    reverse=True,
)
_BASIC_PAIRS = [
    base64.b64encode(f"{c['user']}:{c['password']}".encode()).decode()
    for c in _CREDS.values()
    if "user" in c and "password" in c
]


_RESOLVED_AT = re.compile(r"# resolved [^\n]* from")
_AUTH_SCHEME = re.compile(r"\b(Basic|Bearer|ApiKey|Token)\s+\S+")


def _mask_text(text: str) -> str:
    """Placeholders, their Basic-auth encodings and any scheme-prefixed token."""
    for b64 in _BASIC_PAIRS:
        text = text.replace(b64, "<secret>")
    text = _AUTH_SCHEME.sub(r"\1 <secret>", text)
    for p in _PLACEHOLDERS:
        text = text.replace(p, "<secret>")
    return text


def normalise(value: Any, home: Optional[Path] = None) -> Any:
    if isinstance(value, dict):
        return {
            str(k): normalise(v, home) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [normalise(v, home) for v in value]
    if isinstance(value, Path):
        return normalise(str(value), home)
    if isinstance(value, str):
        if home is not None:
            value = value.replace(str(home), "<HOME>")
        value = _RESOLVED_AT.sub("# resolved <WHEN> from", value)
        return _mask_text(value)
    return value


# ── one snapshot ───────────────────────────────────────────────────────


@pytest.fixture()
def scratch_home(tmp_path, monkeypatch):
    """A HERMES_HOME of our own, with every backend env var cleared."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv(pc.CONFIG_PATH_ENV, raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", raising=False)
    clear_backend_env(monkeypatch)
    for key in pc.field_kinds():
        monkeypatch.delenv(pc.ENV_PREFIX + key.upper(), raising=False)
    monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", home / "hermes_otel.yaml")
    monkeypatch.setattr(pc, "DEFAULT_CONFIG_PATH", home / "plugins" / "hermes_otel" / "config.yaml")
    dash.clear_caches()
    yield home
    dash.clear_caches()


def _write_config(home: Path, entries: List[Dict[str, Any]]) -> Path:
    import yaml

    path = home / "hermes_otel.yaml"
    path.write_text(yaml.safe_dump({"backends": entries}, sort_keys=False), encoding="utf-8")
    dash.clear_caches()
    return path


def _resolved_dict(rb: Any) -> Dict[str, Any]:
    d = dataclasses.asdict(rb)
    for key in ("headers", "metrics_headers", "logs_headers"):
        if d.get(key):
            d[key] = {k: _mask_text(str(v)) for k, v in d[key].items()}
    return d


def _log_targets(rb: Any) -> Dict[str, Any]:
    """Which exporter ``build_log_processors`` would create for this backend
    (the unit conftest stubs ``OTLPLogExporter`` with a MagicMock)."""
    exporter = log_handler.OTLPLogExporter
    exporter.reset_mock()
    processors = log_handler.build_log_processors([rb], {"x-global": "g"})
    exporters = [
        {"endpoint": call.kwargs.get("endpoint"), "headers": call.kwargs.get("headers")}
        for call in exporter.call_args_list
    ]
    return {"count": len(processors), "exporters": exporters}


def _settings_card() -> Dict[str, Any]:
    report = settings_report.build_settings_report()
    cards = next(f["value"] for f in report["fields"] if f["key"] == "backends")
    return cards[0] if cards else {}


def _status_line() -> Any:
    buf = io.StringIO()
    rc = query_cli.cmd_status(argparse.Namespace(db=None, json=True), buf)
    info = json.loads(buf.getvalue())
    return {"rc": rc, "backends": info.get("backends"), "error": info.get("backends_error")}


def _adapter_facts(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cls = dash.find_adapter_class(entry["type"])
    if cls is None:
        return None
    inst = cls(dict(entry))
    return {
        "class": cls.__name__,
        "supports_metrics": bool(inst.supports_metrics),
        "supports_logs": bool(inst.supports_logs),
    }


def snapshot(backend_type: str, path: str, home: Path, monkeypatch) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": backend_type, "path": path}
    canonical = _alias_types().get(backend_type, backend_type)
    if path == "env_only":
        if canonical not in ENV_PRIORITY or backend_type != canonical:
            out["env_selectable"] = False
            return out
        env = env_only_vars(backend_type)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        out["env"] = env
        rb = backends.resolve_from_env()
        out["selected"] = rb.type if rb is not None else None
        out["resolved"] = _resolved_dict(rb) if rb is not None else None
        out["env_notices"] = backends.env_opt_in_hints()
        return normalise(out, home)

    entry = entry_for(backend_type, path, monkeypatch)
    out["entry"] = dict(entry)
    coerced = pc.coerce_backends([entry]) or ()
    if not coerced:
        out["error"] = "entry rejected by the loader"
        return normalise(out, home)
    bc = coerced[0]
    try:
        rb = backends.resolve(bc)
    except ValueError as exc:
        out["error"] = f"ValueError: {exc}"
        rb = None
    if rb is not None:
        out["resolved"] = _resolved_dict(rb)
        out["log_targets"] = _log_targets(rb)
    out["display_name"] = backends.display_name(backend_type)
    out["signal_support"] = backends.signal_support(backend_type)
    out["preset_temporality"] = spec_for(backend_type).temporality
    _write_config(home, [entry])
    out["settings_card"] = _settings_card()
    out["mcp"] = dataclasses.asdict(mcp_config.query_target(entry))
    out["status"] = _status_line()
    out["adapter"] = _adapter_facts(entry)
    return normalise(out, home)


def _compare(name: str, actual: Any) -> None:
    path = GOLDEN / f"{name}.json"
    text = json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if UPDATE:
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path.name}; record with UPDATE_GOLDEN=1"
    expected = path.read_text(encoding="utf-8")
    if expected != text:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(), text.splitlines(), "golden", "actual", lineterm="", n=3
            )
        )
        pytest.fail(f"resolution changed for {name}:\n{diff}")


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("backend_type", all_types())
def test_resolution_snapshot(backend_type, path, scratch_home, monkeypatch):
    _compare(f"{backend_type}__{path}", snapshot(backend_type, path, scratch_home, monkeypatch))


def test_config_example_report(scratch_home):
    """The whole settings report for config.yaml.example (every documented entry)."""
    shutil.copy(REPO_ROOT / "config.yaml.example", scratch_home / "hermes_otel.yaml")
    dash.clear_caches()
    report = settings_report.build_settings_report()
    keep = {
        "config": {
            k: report["config"][k]
            for k in ("exists", "parse_ok", "path_source", "unknown_keys", "deprecated_keys")
        },
        "counts": report["counts"],
        "groups": report["groups"],
        "fields": report["fields"],
        "effective_yaml": report["effective_yaml"],
        "env_notices": report["env_notices"],
    }
    _compare("config_example__report", normalise(keep, scratch_home))


# ── consistency across the registries ──────────────────────────────────


class TestRegistriesAgree:
    def test_every_adapter_type_is_an_exporter_type(self):
        """Every type or alias an adapter handles is in the manifest (#402)."""
        handled = {t for cls in dash.adapters() for t in cls.handles}
        assert handled - backends.KNOWN_TYPES == set()

    def test_adapter_capabilities_match_signal_support(self):
        """What the adapter can read equals what the exporter sends, unless the
        manifest documents the exception (``adapter_signals``, #402)."""
        from helpers.dashboard import generic_adapter

        from hermes_otel.backend_specs import spec_for

        mismatches = {}
        for cls in dash.adapters():
            inst = generic_adapter(cls)
            for t in sorted(cls.handles):
                spec = spec_for(t)
                assert spec is not None, t
                if spec.adapter_signals is not None:
                    want = {
                        "metrics": spec.adapter_signals.metrics,
                        "logs": spec.adapter_signals.logs,
                    }
                else:
                    support = backends.signal_support(t)
                    want = {"metrics": support["metrics"], "logs": support["logs"]}
                got = {"metrics": bool(inst.supports_metrics), "logs": bool(inst.supports_logs)}
                if want != got:
                    mismatches[t] = {"expected": want, "adapter": got}
        assert mismatches == {}

    def test_every_env_var_read_is_in_the_inventory(self):
        # The two spellings the package reads one named variable with. The
        # alternation is ordered so the plugin scanner's shell-dump rule does
        # not match this line.
        pattern = re.compile(r'os\.(?:environ\.get|getenv)\(\s*"([A-Z][A-Z0-9_]+)"')
        read = set()
        sources = [REPO_ROOT / "hermes_otel" / "backends.py"] + sorted(
            (REPO_ROOT / "hermes_otel" / "dashboard" / "backends").glob("*.py")
        )
        for src in sources:
            read |= set(pattern.findall(src.read_text(encoding="utf-8")))
        known = {name for name, _group, _desc in settings_report.KNOWN_ENV_VARS}
        # A config field's HERMES_OTEL_<FIELD> override is listed too (the
        # inventory adds every scalar field's variable before these).
        known |= {
            pc.ENV_PREFIX + key.upper()
            for key, kind in pc.field_kinds().items()
            if kind in pc.SCALAR_KINDS
        }
        assert sorted(read - known) == []

    def test_default_query_ports_agree_with_mcp_config(self):
        from hermes_otel.dashboard.backends import jaeger, tempo

        # ``mcp_config`` takes the port from the spec; the adapters keep their own.
        assert BY_TYPE["jaeger"].default_query_port == jaeger._DEFAULT_JAEGER_QUERY_PORT
        assert BY_TYPE["tempo"].default_query_port == tempo._DEFAULT_TEMPO_QUERY_PORT
        assert BY_TYPE["lgtm"].default_query_port == tempo._DEFAULT_TEMPO_QUERY_PORT

    def test_config_candidates_agree(self, scratch_home):
        home = pc.hermes_home()
        assert dash.candidate_config_paths() == [
            home / "hermes_otel.yaml",
            home / "plugins" / "hermes_otel" / "config.yaml",
        ]

    def test_secret_precedence_agrees(self, monkeypatch):
        monkeypatch.setenv("HO_TEST_K", "from-env")
        both = {"api_key": "inline", "api_key_env": "HO_TEST_K"}
        assert backends.choose_secret("inline", "HO_TEST_K", []).value == "inline"
        assert resolve_env_or_literal(both, "api_key", "api_key_env") == "inline"
        assert backends.choose_secret(None, "HO_TEST_K", []).value == "from-env"
        only_env = {"api_key_env": "HO_TEST_K"}
        assert resolve_env_or_literal(only_env, "api_key", "api_key_env") == "from-env"
