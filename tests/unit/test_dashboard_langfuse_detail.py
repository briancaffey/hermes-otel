"""The Langfuse v3 trace detail carries the exported span attributes (#346).

Langfuse keeps an OTLP-ingested observation's span attributes under
``metadata.attributes``; the detail header reads the model and the turn's
token roll-up from the root span's attributes, so the adapter must lift them
first-class instead of leaving them inside one ``metadata.attributes`` blob.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

_DASHBOARD = Path(__file__).resolve().parents[2] / "hermes_otel" / "dashboard"
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from backends.langfuse import LangfuseAdapter, _obs_to_card_attrs  # noqa: E402

TRACE_ID = "bf309db7fb314cb987fdcc8e0d26a91e"


def _adapter() -> LangfuseAdapter:
    return LangfuseAdapter(
        {
            "type": "langfuse",
            "endpoint": "http://localhost:3002",
            "public_key": "pk",
            "secret_key": "sk",
        }
    )


def _attrs(span: Dict[str, Any]) -> Dict[str, Any]:
    """OTLP/JSON attribute values back to Python (ints travel as strings)."""
    out: Dict[str, Any] = {}
    for a in span["attributes"]:
        v = a["value"]
        if "intValue" in v:
            out[a["key"]] = int(v["intValue"])
        elif "doubleValue" in v:
            out[a["key"]] = float(v["doubleValue"])
        else:
            out[a["key"]] = next(iter(v.values()))
    return out


def _otlp_obs(oid: str, name: str, otype: str, attributes: Dict[str, Any], **extra: Any):
    """An observation the way Langfuse 3 returns one it ingested over OTLP:
    the span attributes verbatim under ``metadata.attributes``, ``usage``
    always present (zero-filled unless the type is GENERATION)."""
    obs: Dict[str, Any] = {
        "id": oid,
        "type": otype,
        "name": name,
        "startTime": "2026-10-10T10:00:00.100Z",
        "endTime": "2026-10-10T10:00:04.000Z",
        "parentObservationId": None,
        "model": None,
        "usage": {"input": 0, "output": 0, "total": 0, "unit": "TOKENS"},
        "metadata": {
            "attributes": attributes,
            "resourceAttributes": {"service.name": "hermes-agent"},
            "scope": {"name": "hermes-otel"},
        },
    }
    obs.update(extra)
    return obs


def _trace() -> Dict[str, Any]:
    root = _otlp_obs(
        "root0000000000aa",
        "agent",
        "SPAN",
        {
            "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
            "gen_ai.request.model": "nvidia/nemotron-3-super-120b-a12b",
            "gen_ai.usage.input_tokens": 50745,
            "gen_ai.usage.output_tokens": 1597,
            "gen_ai.usage.total_tokens": 52342,
            "hermes.session_id": "s-1",
            "hermes.turn.tools": ["terminal"],
            "hermes.cost.usage": 0.0123,
        },
    )
    api = _otlp_obs(
        "api00000000000bb",
        "api.nvidia/nemotron-3-super-120b-a12b",
        "GENERATION",
        {
            "llm.model_name": "nvidia/nemotron-3-super-120b-a12b",
            "gen_ai.usage.input_tokens": 7821,
            "gen_ai.usage.output_tokens": 81,
            "gen_ai.usage.total_tokens": 7902,
        },
        parentObservationId="root0000000000aa",
        model="nvidia/nemotron-3-super-120b-a12b",
        usage={"input": 7821, "output": 81, "total": 7902, "unit": "TOKENS"},
        input={"messages": [{"role": "user", "content": "hi"}]},
    )
    tool = _otlp_obs(
        "tool0000000000cc",
        "terminal",
        "TOOL",
        {"tool.name": "terminal", "input.value": "echo hi"},
        parentObservationId="root0000000000aa",
        level="ERROR",
        statusMessage="exit 1",
    )
    return {
        "id": TRACE_ID,
        "name": "agent",
        "timestamp": "2026-10-10T10:00:00.050Z",
        "sessionId": "s-1",
        "observations": [root, api, tool],
    }


def _detail(trace: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    with patch("backends.langfuse.http_get_json", return_value=trace):
        out = _adapter().get_trace(TRACE_ID)
    return {sp["name"]: sp for sp in out["batches"][0]["scopeSpans"][0]["spans"]}


class TestRootHeaderFacts:
    def test_root_carries_model_and_token_rollup_from_the_span_attributes(self):
        spans = _detail(_trace())
        root = spans["agent"]
        attrs = _attrs(root)
        assert root["parentSpanId"] is None
        assert attrs["llm.model_name"] == "nvidia/nemotron-3-super-120b-a12b"
        assert attrs["gen_ai.request.model"] == "nvidia/nemotron-3-super-120b-a12b"
        assert attrs["gen_ai.usage.input_tokens"] == 50745
        assert attrs["gen_ai.usage.output_tokens"] == 1597
        assert attrs["gen_ai.usage.total_tokens"] == 52342
        assert attrs["hermes.cost.usage"] == pytest.approx(0.0123)
        assert attrs["hermes.session_id"] == "s-1"
        # The trace record's facts still join the root where the span has none.
        assert attrs["langfuse.sessionId"] == "s-1"
        assert attrs["langfuse.trace_id"] == TRACE_ID

    def test_zero_filled_usage_never_reaches_an_otlp_observation(self):
        # Langfuse zero-fills ``usage`` on non-GENERATION observations; a zero
        # on the root used to be shown as "0 tokens" instead of letting the
        # header sum the api spans. The root here has no roll-up at all.
        trace = _trace()
        trace["observations"][0]["metadata"]["attributes"] = {"hermes.session_id": "s-1"}
        attrs = _attrs(_detail(trace)["agent"])
        assert "gen_ai.usage.total_tokens" not in attrs
        assert "gen_ai.usage.input_tokens" not in attrs
        assert "gen_ai.usage.output_tokens" not in attrs

    def test_span_attributes_are_not_repeated_as_a_metadata_blob(self):
        attrs = _attrs(_detail(_trace())["agent"])
        assert "metadata.attributes" not in attrs
        # Other metadata keys stay visible in the attribute table.
        assert attrs["metadata.resourceAttributes"] == json.dumps({"service.name": "hermes-agent"})
        assert attrs["metadata.scope"] == json.dumps({"name": "hermes-otel"})


class TestChildren:
    def test_generation_keeps_its_exported_attributes(self):
        attrs = _attrs(_detail(_trace())["api.nvidia/nemotron-3-super-120b-a12b"])
        assert attrs["gen_ai.usage.total_tokens"] == 7902
        assert attrs["gen_ai.usage.input_tokens"] == 7821
        assert attrs["llm.model_name"] == "nvidia/nemotron-3-super-120b-a12b"
        assert attrs["langfuse.type"] == "GENERATION"
        # Langfuse's derived input is the fallback when the span carries none.
        assert attrs["input.value"] == '{"messages": [{"role": "user", "content": "hi"}]}'

    def test_tool_span_keeps_name_prefix_attributes_and_error_status(self):
        spans = _detail(_trace())
        tool = spans["tool.terminal"]
        attrs = _attrs(tool)
        assert attrs["tool.name"] == "terminal"
        assert attrs["input.value"] == "echo hi"
        assert attrs["status"] == "error"
        assert tool["status"]["code"] == 2
        assert tool["parentSpanId"] == "root0000000000aa"


class TestSdkIngestedObservation:
    def test_langfuse_fields_remain_the_source_without_span_attributes(self):
        # An observation written through a Langfuse SDK has no
        # ``metadata.attributes``: model and usage come from Langfuse's fields.
        obs = {
            "id": "gen1",
            "type": "GENERATION",
            "name": "chat",
            "model": "gpt-4o-mini",
            "modelParameters": {"temperature": 0.2},
            "usage": {"input": 10, "output": 5, "total": 15},
            "input": "hi",
            "output": {"text": "hello"},
            "level": "DEFAULT",
            "metadata": {"team": "a"},
        }
        attrs = _obs_to_card_attrs(obs, {})
        assert attrs["llm.model_name"] == "gpt-4o-mini"
        assert attrs["llm.parameters.temperature"] == 0.2
        assert attrs["gen_ai.usage.input_tokens"] == 10
        assert attrs["gen_ai.usage.output_tokens"] == 5
        assert attrs["gen_ai.usage.total_tokens"] == 15
        assert attrs["input.value"] == "hi"
        assert attrs["output.value"] == '{"text": "hello"}'
        assert attrs["status"] == "ok"

    def test_exported_attributes_win_over_langfuse_derived_fields(self):
        obs = {
            "id": "gen1",
            "type": "GENERATION",
            "name": "api.m",
            "model": "m-as-langfuse-sees-it",
            "usage": {"input": 1, "output": 1, "total": 2},
        }
        attrs = _obs_to_card_attrs(
            obs, {"llm.model_name": "m", "gen_ai.usage.total_tokens": 2, "nullable": None}
        )
        assert attrs["llm.model_name"] == "m"
        assert attrs["gen_ai.usage.total_tokens"] == 2
        assert "nullable" not in attrs
        assert attrs["name"] == "api.m"
