"""Loading the dashboard API must not leave the plugins directory on ``sys.path`` (#638).

The Hermes dashboard imports ``dashboard/plugin_api.py`` by path into its
long-lived process. With ``<plugins>`` left at the front of ``sys.path``, every
sibling plugin directory shadowed the installed package of the same name: the
honcho memory plugin lives in ``<plugins>/honcho/`` and its own
``from honcho import Honcho`` then imported itself instead of the ``honcho-ai``
SDK. Each case runs in a fresh interpreter, since this test process already has
``hermes_otel`` imported.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "hermes_otel"

# Loads plugin_api the way hermes_cli/web_server_dashboard.py does (a top-level
# module from its file path), then imports what a sibling plugin would.
LOADER = textwrap.dedent("""
    import importlib.util, json, sys
    plugins, site = sys.argv[1], sys.argv[2]
    sys.path.insert(0, site)
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_hermes_otel", f"{plugins}/hermes_otel/dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    import honcho
    import hermes_otel
    from hermes_otel.dashboard import route_helpers
    print(json.dumps({
        "plugins_on_path": plugins in sys.path,
        "honcho": honcho.__file__,
        "hermes_otel": hermes_otel.__file__,
        "router": hasattr(api, "router"),
        "late_import": route_helpers.plugin_module("debug_utils") is not None,
    }))
""")


def _plugins_tree(tmp_path: Path) -> tuple[Path, Path]:
    """``<plugins>/hermes_otel`` (a copy, so ``__file__`` resolves inside it), a
    sibling ``<plugins>/honcho`` plugin, and a site dir holding the real SDK."""
    plugins = tmp_path / "plugins"
    shutil.copytree(
        PKG, plugins / "hermes_otel", ignore=shutil.ignore_patterns("__pycache__", "*.log")
    )
    (plugins / "honcho").mkdir()
    (plugins / "honcho" / "__init__.py").write_text("# the honcho memory plugin\n")
    site = tmp_path / "site"
    (site / "honcho").mkdir(parents=True)
    (site / "honcho" / "__init__.py").write_text("class Honcho:\n    pass\n")
    return plugins, site


def test_sibling_plugin_does_not_shadow_installed_package(tmp_path):
    plugins, site = _plugins_tree(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c", LOADER, str(plugins), str(site)],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    result = json.loads(out.stdout)
    assert result == {
        "plugins_on_path": False,
        "honcho": str(site / "honcho" / "__init__.py"),
        "hermes_otel": str(plugins / "hermes_otel" / "__init__.py"),
        "router": True,
        "late_import": True,
    }
