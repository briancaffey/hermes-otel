"""Plugin-catalog readiness (#128, #130, #133).

The Hermes plugin catalog renders each entry from the pinned commit's
``plugin.yaml`` and fails admission when the declared hooks differ from what
``register()`` registers. These tests pin the manifest v2 metadata, keep the
registered hook set equal to the declared one whatever the host exposes, and
check the rendered catalog entry against the catalog's schema.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

import hermes_otel

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "hermes_otel" / "plugin.yaml"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


# ── #128: manifest v2 metadata ───────────────────────────────────────────────


class TestManifestV2:
    def test_v2_metadata_declared(self):
        m = _manifest()
        assert m["manifest_version"] == 2
        assert m["name"] == "hermes_otel"
        assert m["author"] and m["license"] == "Apache-2.0"
        assert m["homepage"].startswith("https://briancaffey.github.io/hermes-otel")
        assert "observability" in m["tags"] and "opentelemetry" in m["tags"]

    def test_requires_hermes_is_a_strict_version_spec(self):
        # `hermes plugins validate` parses this strictly; a typo fails admission.
        spec = _manifest()["requires_hermes"]
        assert re.fullmatch(r"(>=|==|~=|>|<|<=|!=)\s*\d+(\.\d+)*", spec), spec
        assert spec == ">=0.21"

    def test_description_fits_a_catalog_card(self):
        desc = " ".join(_manifest()["description"].split())
        assert len(desc) <= 200, len(desc)
        assert desc.startswith("OpenTelemetry for Hermes Agent")

    def test_mcp_request_headers_not_declared(self):
        # Not a hook in any Hermes release; declaring it would make the
        # validator report a declared-but-unregistered hook (#130).
        assert "mcp_request_headers" not in _manifest()["provides_hooks"]

    def test_python_dependencies_stay_a_contiguous_block(self):
        # test_install_artifact reads this block with a regex, not a yaml lib.
        text = MANIFEST.read_text(encoding="utf-8")
        block = re.search(r"^python_dependencies:\n((?:  - .*\n)+)", text, re.M)
        assert block and len(block.group(1).splitlines()) == len(_manifest()["python_dependencies"])


# ── #130: registered hooks == declared hooks, fail closed ────────────────────


class _Ctx:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, callback):
        self.hooks.append(name)

    def register_skill(self, name, path, description=""):
        pass


@pytest.fixture
def enabled_tracer(monkeypatch):
    class _Cfg:
        discovery_prompt = False

    class _T:
        is_enabled = True
        config = _Cfg()

        def init(self):
            return True

    monkeypatch.setattr("hermes_otel.tracer.get_tracer", lambda: _T())


def _fake_hermes_cli(monkeypatch, valid_hooks):
    """Make ``from hermes_cli.plugins import VALID_HOOKS`` yield *valid_hooks*."""
    import types

    pkg = types.ModuleType("hermes_cli")
    mod = types.ModuleType("hermes_cli.plugins")
    mod.VALID_HOOKS = valid_hooks
    pkg.plugins = mod
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", mod)


class TestRegisteredHooksMatchManifest:
    def test_registry_not_importable_registers_exactly_the_declared_hooks(
        self, monkeypatch, enabled_tracer
    ):
        # The catalog capability probe runs register() in a child process; if
        # hermes_cli is ever unimportable there, an unconditional registration
        # would surface as an undeclared hook and fail admission.
        monkeypatch.setitem(sys.modules, "hermes_cli", None)
        monkeypatch.setitem(sys.modules, "hermes_cli.plugins", None)
        ctx = _Ctx()
        hermes_otel.register(ctx)
        assert sorted(ctx.hooks) == sorted(_manifest()["provides_hooks"])

    def test_host_without_the_hook_registers_exactly_the_declared_hooks(
        self, monkeypatch, enabled_tracer
    ):
        _fake_hermes_cli(monkeypatch, frozenset(_manifest()["provides_hooks"]))
        ctx = _Ctx()
        hermes_otel.register(ctx)
        assert sorted(ctx.hooks) == sorted(_manifest()["provides_hooks"])

    def test_host_advertising_the_hook_registers_it(self, monkeypatch, enabled_tracer):
        declared = _manifest()["provides_hooks"]
        _fake_hermes_cli(monkeypatch, frozenset(declared) | {"mcp_request_headers"})
        ctx = _Ctx()
        hermes_otel.register(ctx)
        assert sorted(ctx.hooks) == sorted(declared + ["mcp_request_headers"])

    def test_registering_the_hook_can_fail_without_aborting(self, monkeypatch, enabled_tracer):
        _fake_hermes_cli(monkeypatch, frozenset({"mcp_request_headers", "pre_tool_call"}))

        class _Picky(_Ctx):
            def register_hook(self, name, callback):
                if name == "mcp_request_headers":
                    raise ValueError("unknown hook")
                super().register_hook(name, callback)

        ctx = _Picky()
        hermes_otel.register(ctx)
        assert "mcp_request_headers" not in ctx.hooks
        assert "pre_tool_call" in ctx.hooks


# ── #133: rendered catalog entry ─────────────────────────────────────────────


def _render_module():
    path = REPO_ROOT / "scripts" / "render_catalog_entry.py"
    spec = importlib.util.spec_from_file_location("render_catalog_entry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SHA = "0123456789abcdef0123456789abcdef01234567"

# Mirror of KNOWN_KEYS / REQUIRED_KEYS in hermes-agent's
# scripts/validate_plugin_catalog.py (unknown keys only warn there; required
# ones fail). CI runs the real validator; this keeps the unit suite honest.
KNOWN_KEYS = {
    "name",
    "repo",
    "sha",
    "subdir",
    "description",
    "maintainer",
    "tier",
    "category",
    "requires_hermes",
    "docs_url",
    "version",
    "image",
    "platforms",
    "capabilities",
}
REQUIRED_KEYS = {"name", "repo", "sha", "description", "maintainer"}


class TestCatalogEntry:
    def test_entry_matches_manifest(self):
        mod = _render_module()
        entry = yaml.safe_load(mod.render(mod.build_entry(_manifest(), sha=SHA)))
        m = _manifest()
        assert entry["name"] == "hermes-otel" and entry["subdir"] == "hermes_otel"
        assert entry["sha"] == SHA and entry["version"] == str(m["version"])
        assert entry["requires_hermes"] == m["requires_hermes"]
        assert entry["description"] == " ".join(m["description"].split())
        caps = entry["capabilities"]
        assert caps["provides_hooks"] == sorted(m["provides_hooks"])
        assert caps["provides_tools"] == [] and caps["provides_middleware"] == []
        assert caps["requires_env"] == []
        assert entry["image"] == (
            f"https://raw.githubusercontent.com/briancaffey/hermes-otel/{SHA}/docs/catalog-banner.png"
        )
        assert (REPO_ROOT / "docs" / "catalog-banner.png").is_file()

    def test_entry_keys_are_the_catalog_schema(self):
        mod = _render_module()
        entry = yaml.safe_load(mod.render(mod.build_entry(_manifest(), sha=SHA)))
        assert REQUIRED_KEYS <= set(entry) <= KNOWN_KEYS
        assert entry["tier"] == "community" and entry["category"] == "general"
        assert re.fullmatch(r"[0-9a-f]{40}", entry["sha"])

    def test_sha_and_version_are_quoted_and_overridable(self):
        mod = _render_module()
        text = mod.render(mod.build_entry(_manifest(), sha=SHA, version="9.9.9", image=False))
        assert f'sha: "{SHA}"' in text and 'version: "9.9.9"' in text
        assert "image:" not in text

    def test_rejects_short_or_symbolic_sha(self):
        mod = _render_module()
        for bad in ("main", SHA[:7], "hermes-otel-v1.7.0"):
            with pytest.raises(ValueError):
                mod.build_entry(_manifest(), sha=bad)

    def test_cli_writes_the_file(self, tmp_path):
        mod = _render_module()
        out = tmp_path / "plugin-catalog" / "hermes-otel.yaml"
        assert mod.main(["--sha", SHA, "-o", str(out)]) == 0
        assert yaml.safe_load(out.read_text())["name"] == "hermes-otel"
