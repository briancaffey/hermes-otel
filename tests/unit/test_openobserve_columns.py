"""OpenObserve column names map back to the plugin's real attribute names (#158)."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent.parent
_DASHBOARD = _HERE / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))
if "fastapi" not in sys.modules:
    _stub = types.ModuleType("fastapi")

    class _StubHTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    _stub.HTTPException = _StubHTTPException  # type: ignore[attr-defined]
    sys.modules["fastapi"] = _stub

from backends import openobserve as oo  # noqa: E402

DOC = _HERE / "website" / "docs" / "reference" / "span-attributes.md"
_NAME = r"(?:gen_ai|llm|hermes|openinference|session|user|error|input|output|tool|correlation|http|service|process|wandb|weave|exception|host)\.[a-z_][a-z0-9_.]*"


def _documented() -> set:
    names = set(re.findall(r"`(" + _NAME + r")`", DOC.read_text(encoding="utf-8")))
    return {n for n in names if not n.endswith(".*")}


class TestColumnTable:
    def test_every_documented_attribute_round_trips(self):
        missing = sorted(_documented() - set(oo._KNOWN_ATTRIBUTES))
        assert missing == [], f"add to _KNOWN_ATTRIBUTES in openobserve.py: {missing}"
        wrong = {}
        for attr in _documented():
            back = oo._dotted(oo._oo_col(attr))
            # Aliases that share a column resolve to the current spelling.
            if back != attr and oo._oo_col(back) != oo._oo_col(attr):
                wrong[attr] = back
        assert wrong == {}

    def test_real_columns_get_their_real_names(self):
        for col, attr in {
            "llm_model_name": "llm.model_name",
            "gen_ai_usage_input_tokens": "gen_ai.usage.input_tokens",
            "hermes_session_id": "hermes.session_id",
            "gen_ai_tool_call_id": "gen_ai.tool.call.id",
            "llm_token_count_prompt": "llm.token_count.prompt",
            "hermes_turn_final_status": "hermes.turn.final_status",
            "openinference_span_kind": "openinference.span.kind",
        }.items():
            assert oo._dotted(col) == attr, col

    def test_shared_columns_prefer_the_current_spelling(self):
        assert (
            oo._dotted("gen_ai_usage_cache_read_input_tokens")
            == "gen_ai.usage.cache_read.input_tokens"
        )
        assert oo._dotted("session_id") == "session.id"

    def test_unknown_column_is_not_invented(self):
        assert oo._dotted("some_backend_column") == "some_backend_column"

    def test_card_keys_match_after_round_trip(self):
        row = {
            "trace_id": "t",
            "span_id": "s",
            "operation_name": "api.m",
            "start_time": 1,
            "end_time": 2,
            "llm_model_name": "gpt-4",
            "gen_ai_usage_input_tokens": "13283",
            "hermes_tool_outcome": "completed",
        }
        otlp = oo._rows_to_otlp([row])
        span = otlp["batches"][0]["scopeSpans"][0]["spans"][0]
        keys = {a["key"] for a in span["attributes"]}
        assert {"llm.model_name", "gen_ai.usage.input_tokens", "hermes.tool.outcome"} <= keys
        assert not any("model.name" in k or "usage.input.tokens" in k for k in keys)
