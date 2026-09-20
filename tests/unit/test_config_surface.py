"""The config surface is one thing (#93): every field is loadable from yaml,
every scalar field is overridable from ``HERMES_OTEL_<FIELD>``, invalid values
warn instead of vanishing, and the reference docs are generated from the same
dataclass — so a new knob cannot be half-wired or undocumented.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_otel.plugin_config import HermesOtelConfig, field_kinds, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE = {"bool": ("false", False), "int": ("42", 42), "float": ("0.5", 0.5), "str": ("x", "x")}


def _scalar_fields():
    return [(n, k) for n, k in field_kinds().items() if k in _SAMPLE]


class TestEnvOverrides:
    @pytest.mark.parametrize("name,kind", _scalar_fields())
    def test_every_scalar_field_is_env_overridable(self, name, kind, monkeypatch, tmp_path):
        raw, expected = _SAMPLE[kind]
        if name == "host_metrics_gpu":
            raw, expected = "nvidia", "nvidia"
        if name == "log_level":
            raw, expected = "debug", "DEBUG"
        if name == "content_capture":
            raw, expected = "preview", "preview"
        monkeypatch.setenv(f"HERMES_OTEL_{name.upper()}", raw)
        cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert getattr(cfg, name) == expected, name

    def test_maps_and_backends_are_yaml_only(self):
        assert {n for n, k in field_kinds().items() if k not in _SAMPLE} == {
            "headers",
            "global_tags",
            "resource_attributes",
            "backends",
        }

    def test_invalid_env_value_warns_and_is_ignored(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("HERMES_OTEL_FLUSH_INTERVAL_MS", "soon")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(path=tmp_path / "nonexistent.yaml")
        assert cfg.flush_interval_ms == HermesOtelConfig().flush_interval_ms
        assert any("HERMES_OTEL_FLUSH_INTERVAL_MS" in r.getMessage() for r in caplog.records)


class TestYamlCoercion:
    def test_invalid_yaml_value_warns_and_keeps_default(self, tmp_path, caplog):
        pytest.importorskip("yaml")
        path = tmp_path / "hermes_otel.yaml"
        path.write_text("preview_max_chars: lots\nheaders: not-a-map\n")
        with caplog.at_level(logging.WARNING, logger="hermes_otel"):
            cfg = load_config(path=path)
        assert cfg.preview_max_chars == 1200 and cfg.headers is None
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "preview_max_chars" in msgs and "headers" in msgs

    @pytest.mark.parametrize("name,kind", _scalar_fields())
    def test_every_scalar_field_loads_from_yaml(self, name, kind, tmp_path):
        pytest.importorskip("yaml")
        raw, expected = _SAMPLE[kind]
        if name == "host_metrics_gpu":
            raw, expected = "amd", "amd"
        if name == "log_level":
            raw, expected = "warning", "WARNING"
        if name == "content_capture":
            raw, expected = "preview", "preview"
        path = tmp_path / "hermes_otel.yaml"
        path.write_text(f"{name}: {raw}\n")
        assert getattr(load_config(path=path), name) == expected


class TestGeneratedDocs:
    def test_reference_tables_match_the_dataclass(self):
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "gen_config_docs.py")],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert proc.returncode == 0, (
            "config-schema.md / env-vars.md drifted from HermesOtelConfig — run\n"
            "  uv run --extra dev python scripts/gen_config_docs.py --write\n" + proc.stdout
        )

    def test_every_field_has_a_description(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from gen_config_docs import FIELD_DOCS

        assert set(FIELD_DOCS) == {f.name for f in dataclasses.fields(HermesOtelConfig)}

    def test_every_backend_env_var_is_in_the_reference(self):
        src = "".join(
            (REPO_ROOT / "hermes_otel" / f).read_text(encoding="utf-8")
            for f in ("backends.py", "langsmith_backend.py", "live_store.py", "plugin_config.py")
        )
        names = set(re.findall(r'"([A-Z][A-Z0-9_]{5,})"', src))
        names |= {"HERMES_OTEL_CONFIG", "HERMES_HOME", "HERMES_OTEL_DEBUG"}
        names -= {
            n for n in names if n.startswith("HERMES_OTEL_") and n[12:].lower() in field_kinds()
        }
        names -= {"UTF_8"}  # not an env var
        ref = (REPO_ROOT / "website" / "docs" / "reference" / "env-vars.md").read_text(
            encoding="utf-8"
        )
        missing = sorted(
            n for n in names if f"`{n}`" not in ref and f"`{n}` " not in ref and n not in ref
        )
        assert missing == [], f"document in env-vars.md: {missing}"
