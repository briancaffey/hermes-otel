"""span-attributes.md must list every attribute the code sets, and nothing it does not (#96)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE = [REPO_ROOT / "hermes_otel" / "hooks.py", REPO_ROOT / "hermes_otel" / "tracer.py"]
DOC = REPO_ROOT / "website" / "docs" / "reference" / "span-attributes.md"

_NAME = r"(?:gen_ai|llm|hermes|openinference|session|user|error|input|output|tool|correlation|http|service|process|wandb|weave|exception|host)\.[a-z_][a-z0-9_.]*"
_QUOTED = re.compile(r'"(' + _NAME + r')"')
_TICKED = re.compile(r"`(" + _NAME + r")`")
# Documented in prose (wildcards), not as literals.
_PROSE_ONLY = {"telemetry.sdk.*", "resource_attributes.*", "global_tags.*"}


# Metric-only literals: instrument names created in tracer.py (documented in
# metrics.md), the one metric-only label, and the host-metric names the live
# store mirrors. ``hermes.retry.count`` is both an instrument and a span
# attribute, so it stays.
_DUAL_USE = {"hermes.retry.count"}


def _metric_names() -> set:
    src = (REPO_ROOT / "hermes_otel" / "tracer.py").read_text(encoding="utf-8")
    body = src[src.index("def _create_metric_instruments") :]
    body = body[: body.index("\n    def ", 10)]
    instruments = set(
        re.findall(r'create_(?:counter|histogram|observable_[a-z_]+)\(\s*"([a-z_.]+)"', body)
    )
    return (instruments - _DUAL_USE) | {
        "gen_ai.token.type",
        "process.cpu.utilization",
        "system.cpu.utilization",
    }


def _code_attributes() -> set:
    names = set()
    for path in CODE:
        names |= set(_QUOTED.findall(path.read_text(encoding="utf-8")))
    return names - _metric_names()


def _doc_attributes() -> set:
    text = DOC.read_text(encoding="utf-8")
    return set(_TICKED.findall(text))


def test_every_emitted_attribute_is_documented():
    missing = sorted(_code_attributes() - _doc_attributes())
    assert missing == [], f"add to span-attributes.md: {missing}"


def test_every_documented_attribute_is_emitted():
    phantom = sorted(_doc_attributes() - _code_attributes() - _PROSE_ONLY)
    assert phantom == [], f"span-attributes.md lists attributes the code never sets: {phantom}"
