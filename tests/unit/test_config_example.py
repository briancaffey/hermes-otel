"""``config.yaml.example`` is the file users copy; keep it honest (#103).

Every key it mentions must be a real config field (or a real top-level
block), every config field must be mentioned, and the file must load without
a single warning. The keys are commented out in the example, so this reads
them from the ``# key:`` lines rather than parsing YAML.
"""

import dataclasses
import logging
import re
from pathlib import Path

from hermes_otel.plugin_config import HermesOtelConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "config.yaml.example"

# Top-level keys that are not dataclass fields but are real config.
_TOP_LEVEL_BLOCKS = set()


def _example_top_level_keys() -> set:
    text = EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^#? ?([a-z_]+):", text, flags=re.M))


def _fields() -> set:
    return {f.name for f in dataclasses.fields(HermesOtelConfig)}


def test_every_example_key_is_a_real_field():
    unknown = sorted(_example_top_level_keys() - _fields() - _TOP_LEVEL_BLOCKS)
    assert unknown == [], f"config.yaml.example mentions keys the loader ignores: {unknown}"


def test_every_field_is_documented_in_the_example():
    missing = sorted(_fields() - _example_top_level_keys())
    assert missing == [], f"add these knobs to config.yaml.example: {missing}"


def test_example_loads_without_warnings(caplog):
    with caplog.at_level(logging.WARNING, logger="hermes_otel"):
        cfg = load_config(path=EXAMPLE)
    assert cfg.enabled is True
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
