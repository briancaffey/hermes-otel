"""The metrics reference page must list every instrument the tracer creates (#94)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACER = REPO_ROOT / "hermes_otel" / "tracer.py"
DOCS = REPO_ROOT / "website" / "docs"
METRICS_MD = DOCS / "reference" / "metrics.md"

# Names that were documented but never existed; they must not come back.
_PHANTOMS = ("hermes.tokens.", "hermes.api.duration", "hermes.sessions", "hermes.tool.calls")


def _instrument_names() -> set:
    src = TRACER.read_text(encoding="utf-8")
    body = src[src.index("def _create_metric_instruments") :]
    body = body[: body.index("\n    def ", 10)]
    return set(
        re.findall(r'create_(?:counter|histogram|observable_[a-z_]+)\(\s*"([a-z_.]+)"', body)
    )


def test_every_instrument_is_documented():
    names = _instrument_names()
    assert len(names) >= 15, names
    text = METRICS_MD.read_text(encoding="utf-8")
    missing = sorted(n for n in names if f"`{n}`" not in text)
    assert missing == [], f"add to website/docs/reference/metrics.md: {missing}"


def test_no_phantom_metric_names_in_docs():
    hits = []
    for md in DOCS.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        for p in _PHANTOMS:
            if p in text:
                hits.append(f"{md.relative_to(REPO_ROOT)}: {p}")
    assert hits == []
