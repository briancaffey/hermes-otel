"""The plugin reads YAML with PyYAML, or with the ruamel.yaml Hermes ships."""

import sys
from pathlib import Path

import pytest
import yaml

from hermes_otel import plugin_config, yaml_compat

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def without_pyyaml(monkeypatch):
    """PyYAML missing, as in the Hermes container image's base environment."""
    monkeypatch.setitem(sys.modules, "yaml", None)


def test_ruamel_reads_the_example_config_like_pyyaml(without_pyyaml):
    text = (REPO / "config.yaml.example").read_text(encoding="utf-8")
    assert yaml_compat.safe_load(text) == yaml.safe_load(text)


def test_ruamel_keeps_yaml_1_1_booleans(without_pyyaml):
    assert yaml_compat.safe_load("a: yes\nb: off\nc: 'off'\n") == {
        "a": True,
        "b": False,
        "c": "off",
    }


def test_ruamel_dump_is_block_style_in_the_given_order(without_pyyaml):
    value = {"zeta": 1, "alpha": {"nested": [1, "two"]}, "name": "café"}
    text = yaml_compat.safe_dump(value)
    assert not text.startswith("%YAML")
    assert text.index("zeta") < text.index("alpha") < text.index("name")
    assert "{" not in text and "café" in text
    assert yaml.safe_load(text) == value


def test_pyyaml_is_used_when_installed():
    assert yaml_compat.safe_load("a: 1\n") == {"a": 1}
    assert yaml_compat.safe_dump({"b": 1, "a": 2}) == "b: 1\na: 2\n"


def test_config_file_loads_without_pyyaml(without_pyyaml, tmp_path):
    path = tmp_path / "hermes_otel.yaml"
    path.write_text("capture_logs: yes\nbackends:\n  - type: otlp\n    protocol: grpc\n")
    assert plugin_config.load_yaml(path) == {
        "capture_logs": True,
        "backends": [{"type": "otlp", "protocol": "grpc"}],
    }


def test_no_yaml_library_at_all(monkeypatch, tmp_path, caplog):
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setitem(sys.modules, "ruamel.yaml", None)
    assert yaml_compat.available() is False
    with pytest.raises(ImportError):
        yaml_compat.safe_load("a: 1\n")
    with pytest.raises(ImportError):
        yaml_compat.safe_dump({"a": 1})
    path = tmp_path / "hermes_otel.yaml"
    path.write_text("capture_logs: true\n")
    with caplog.at_level("WARNING"):
        assert plugin_config.load_yaml(path) == {}
    assert "neither PyYAML nor ruamel.yaml" in caplog.text
