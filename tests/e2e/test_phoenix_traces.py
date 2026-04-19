"""E2E tests: fire hooks with a real Phoenix backend and verify traces via GraphQL."""

import json
import os
import time

import pytest

requests = pytest.importorskip("requests")

import hermes_otel.tracer as tracer_mod
from hermes_otel.hooks import (
    on_post_api_request,
    on_post_llm_call,
    on_post_tool_call,
    on_pre_api_request,
    on_pre_llm_call,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
)
from hermes_otel.tracer import HermesOTelPlugin


def _query_phoenix_spans(base_url, project_name=None, limit=50):
    """Query Phoenix GraphQL API for recent spans."""
    # Phoenix GraphQL endpoint
    graphql_url = f"{base_url}/graphql"

    query = """
    query GetSpans($first: Int!) {
      spans(first: $first, sort: { col: startTime, dir: desc }) {
        edges {
          node {
            name
            statusCode
            parentId
            context {
              traceId
              spanId
            }
            attributes
          }
        }
      }
    }
    """

    resp = requests.post(
        graphql_url,
        json={"query": query, "variables": {"first": limit}},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")

    edges = data.get("data", {}).get("spans", {}).get("edges", [])
    return [edge["node"] for edge in edges]


def _init_phoenix_plugin(phoenix_url, project_name="hermes-otel-e2e-test"):
    """Initialize a fresh HermesOTelPlugin pointing at Phoenix."""
    endpoint = f"{phoenix_url}/v1/traces"

    plugin = HermesOTelPlugin()
    os.environ["OTEL_PROJECT_NAME"] = project_name

    result = plugin._init_otlp(endpoint, backend_name="Phoenix")
    assert result, f"Failed to initialize Phoenix plugin at {endpoint}"

    tracer_mod._tracer = plugin
    return plugin


@pytest.mark.e2e
@pytest.mark.phoenix
class TestPhoenixTraceExport:

    def test_session_trace_appears_in_phoenix(self, phoenix_service):
        """Fire a complete session lifecycle and verify the trace appears in Phoenix."""
        plugin = _init_phoenix_plugin(phoenix_service)

        try:
            session_id = f"e2e-test-{int(time.time())}"

            # Simulate a session
            on_session_start(session_id=session_id, model="gpt-4", platform="e2e-test")
            on_pre_llm_call(
                session_id=session_id, user_message="What is 2+2?",
                conversation_history=[], is_first_turn=True,
                model="gpt-4", platform="e2e-test",
            )
            on_pre_api_request(
                task_id=f"api-{session_id}", session_id=session_id,
                platform="e2e-test", model="gpt-4",
                provider="openai", base_url="", api_mode="chat",
                api_call_count=1, message_count=2, tool_count=0,
                approx_input_tokens=50, request_char_count=200, max_tokens=256,
            )
            on_post_api_request(
                task_id=f"api-{session_id}", session_id=session_id,
                platform="e2e-test", model="gpt-4",
                provider="openai", base_url="", api_mode="chat",
                api_call_count=1, api_duration=0.3, finish_reason="stop",
                message_count=2, response_model="gpt-4",
                usage={"prompt_tokens": 50, "output_tokens": 10, "total_tokens": 60},
                assistant_content_chars=20, assistant_tool_call_count=0,
            )
            on_post_llm_call(
                session_id=session_id, user_message="What is 2+2?",
                assistant_response="4",
                conversation_history=[], model="gpt-4", platform="e2e-test",
            )
            on_session_end(
                session_id=session_id, completed=True, interrupted=False,
                model="gpt-4", platform="e2e-test",
            )

            # Force flush
            plugin._force_flush()

            # Wait for Phoenix to ingest
            time.sleep(3)

            # Query Phoenix
            spans = _query_phoenix_spans(phoenix_service)

            # Find our spans by session_id in attributes
            our_spans = []
            for span in spans:
                attrs_str = span.get("attributes", "")
                if session_id in attrs_str:
                    our_spans.append(span)

            span_names = [s["name"] for s in our_spans]
            assert "agent" in span_names, f"Expected 'agent' span, got: {span_names}"
            assert any("llm" in n for n in span_names), f"Expected LLM span, got: {span_names}"
            assert any("api" in n for n in span_names), f"Expected API span, got: {span_names}"

        finally:
            tracer_mod._tracer = None
            os.environ.pop("OTEL_PROJECT_NAME", None)

    def test_tool_spans_with_correct_hierarchy(self, phoenix_service):
        """Fire hooks with tool calls and verify parent-child relationships."""
        plugin = _init_phoenix_plugin(phoenix_service)

        try:
            session_id = f"e2e-tool-{int(time.time())}"

            on_session_start(session_id=session_id, model="gpt-4", platform="e2e-test")
            on_pre_llm_call(
                session_id=session_id, user_message="List files",
                conversation_history=[], is_first_turn=True,
                model="gpt-4", platform="e2e-test",
            )
            on_pre_api_request(
                task_id=f"api-{session_id}", session_id=session_id,
                platform="e2e-test", model="gpt-4",
                provider="openai", base_url="", api_mode="chat",
                api_call_count=1, message_count=2, tool_count=1,
                approx_input_tokens=100, request_char_count=500, max_tokens=512,
            )

            # Tool call
            on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id=f"tool-{session_id}")
            on_post_tool_call(
                tool_name="bash", args={"cmd": "ls"},
                result="file.txt\nREADME.md", task_id=f"tool-{session_id}",
            )

            on_post_api_request(
                task_id=f"api-{session_id}", session_id=session_id,
                platform="e2e-test", model="gpt-4",
                provider="openai", base_url="", api_mode="chat",
                api_call_count=1, api_duration=0.5, finish_reason="stop",
                message_count=2, response_model="gpt-4",
                usage={"prompt_tokens": 100, "output_tokens": 30, "total_tokens": 130},
                assistant_content_chars=50, assistant_tool_call_count=1,
            )
            on_post_llm_call(
                session_id=session_id, user_message="List files",
                assistant_response="Found file.txt and README.md",
                conversation_history=[], model="gpt-4", platform="e2e-test",
            )
            on_session_end(
                session_id=session_id, completed=True, interrupted=False,
                model="gpt-4", platform="e2e-test",
            )

            plugin._force_flush()
            time.sleep(3)

            spans = _query_phoenix_spans(phoenix_service)
            our_spans = [s for s in spans if session_id in s.get("attributes", "")]

            span_names = [s["name"] for s in our_spans]
            assert "tool.bash" in span_names, f"Expected tool span, got: {span_names}"

            # Verify tool span has a parent
            tool_span = next(s for s in our_spans if s["name"] == "tool.bash")
            assert tool_span["parentId"] is not None, "Tool span should have a parent"

        finally:
            tracer_mod._tracer = None
            os.environ.pop("OTEL_PROJECT_NAME", None)

    def test_token_attributes_on_api_span(self, phoenix_service):
        """Verify token count attributes appear correctly on API spans."""
        plugin = _init_phoenix_plugin(phoenix_service)

        try:
            session_id = f"e2e-tokens-{int(time.time())}"

            on_pre_api_request(
                task_id=f"api-{session_id}", session_id=session_id,
                platform="e2e-test", model="gpt-4",
                provider="openai", base_url="", api_mode="chat",
                api_call_count=1, message_count=3, tool_count=0,
                approx_input_tokens=200, request_char_count=1000, max_tokens=1024,
            )
            on_post_api_request(
                task_id=f"api-{session_id}", session_id=session_id,
                platform="e2e-test", model="gpt-4",
                provider="openai", base_url="", api_mode="chat",
                api_call_count=1, api_duration=0.7, finish_reason="stop",
                message_count=3, response_model="gpt-4",
                usage={
                    "prompt_tokens": 200, "output_tokens": 80, "total_tokens": 280,
                    "cache_read_tokens": 50,
                },
                assistant_content_chars=300, assistant_tool_call_count=0,
            )

            plugin._force_flush()
            time.sleep(3)

            spans = _query_phoenix_spans(phoenix_service)
            our_spans = [s for s in spans if session_id in s.get("attributes", "")]
            assert len(our_spans) >= 1, f"Expected at least 1 span, got {len(our_spans)}"

            api_span = next(s for s in our_spans if "api" in s["name"])
            attrs_str = api_span["attributes"]

            # Token attributes should be present (as stringified JSON in Phoenix)
            assert "llm.token_count.prompt" in attrs_str
            assert "llm.token_count.completion" in attrs_str

        finally:
            tracer_mod._tracer = None
            os.environ.pop("OTEL_PROJECT_NAME", None)
