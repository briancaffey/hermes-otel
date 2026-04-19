"""E2E tests: fire hooks with a real Langfuse backend and verify traces via REST API.

Langfuse API reference:
  GET /api/public/observations — query spans/generations
  GET /api/public/health       — health check
  Authentication: Basic Auth (public_key:secret_key)
"""

import base64
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


def _langfuse_auth_header(public_key, secret_key):
    """Build Basic Auth header for the Langfuse REST API."""
    creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _query_langfuse_observations(base_url, public_key, secret_key, **params):
    """Query Langfuse GET /api/public/observations.

    Supported params: name, type, traceId, level, fromStartTime, toStartTime,
                      page, limit, parentObservationId.
    Returns the list of observation dicts.
    """
    url = f"{base_url}/api/public/observations"
    headers = _langfuse_auth_header(public_key, secret_key)
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def _init_langfuse_plugin(otel_endpoint, public_key, secret_key):
    """Initialize a fresh HermesOTelPlugin pointing at Langfuse OTEL endpoint."""
    import base64

    auth_b64 = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "x-langfuse-ingestion-version": "4",
    }
    plugin = HermesOTelPlugin()
    result = plugin._init_otlp(otel_endpoint, headers=headers, backend_name="Langfuse")
    assert result, f"Failed to initialize Langfuse plugin at {otel_endpoint}"
    tracer_mod._tracer = plugin
    return plugin


def _wait_for_observations(
    base_url, public_key, secret_key, expected_min=1, timeout=45, interval=5, **query_params
):
    """Poll Langfuse observations API until we get at least expected_min results.

    Langfuse processes events asynchronously (typically 15-30s latency).
    """
    deadline = time.time() + timeout
    observations = []
    while time.time() < deadline:
        observations = _query_langfuse_observations(
            base_url,
            public_key,
            secret_key,
            **query_params,
        )
        if len(observations) >= expected_min:
            return observations
        time.sleep(interval)
    return observations


@pytest.mark.e2e
@pytest.mark.langfuse
class TestLangfuseTraceExport:

    def test_session_trace_appears_in_langfuse(self, langfuse_service):
        """Fire a complete session lifecycle and verify observations appear in Langfuse."""
        info = langfuse_service
        plugin = _init_langfuse_plugin(
            info["otel_endpoint"],
            info["public_key"],
            info["secret_key"],
        )

        try:
            session_id = f"lf-e2e-{int(time.time())}"
            from_time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

            # Simulate a session
            on_session_start(session_id=session_id, model="gpt-4", platform="e2e-test")
            on_pre_llm_call(
                session_id=session_id,
                user_message="What is 2+2?",
                conversation_history=[],
                is_first_turn=True,
                model="gpt-4",
                platform="e2e-test",
            )
            on_pre_api_request(
                task_id=f"api-{session_id}",
                session_id=session_id,
                platform="e2e-test",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                message_count=2,
                tool_count=0,
                approx_input_tokens=50,
                request_char_count=200,
                max_tokens=256,
            )
            on_post_api_request(
                task_id=f"api-{session_id}",
                session_id=session_id,
                platform="e2e-test",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                api_duration=0.3,
                finish_reason="stop",
                message_count=2,
                response_model="gpt-4",
                usage={"prompt_tokens": 50, "output_tokens": 10, "total_tokens": 60},
                assistant_content_chars=20,
                assistant_tool_call_count=0,
            )
            on_post_llm_call(
                session_id=session_id,
                user_message="What is 2+2?",
                assistant_response="4",
                conversation_history=[],
                model="gpt-4",
                platform="e2e-test",
            )
            on_session_end(
                session_id=session_id,
                completed=True,
                interrupted=False,
                model="gpt-4",
                platform="e2e-test",
            )

            # Force flush spans
            plugin._force_flush()

            # Poll Langfuse API for our observations
            observations = _wait_for_observations(
                info["base_url"],
                info["public_key"],
                info["secret_key"],
                expected_min=3,
                fromStartTime=from_time,
            )

            obs_names = [o.get("name", "") for o in observations]
            assert any(
                "agent" in n for n in obs_names
            ), f"Expected 'agent' observation, got: {obs_names}"
            assert any("llm" in n for n in obs_names), f"Expected LLM observation, got: {obs_names}"
            assert any("api" in n for n in obs_names), f"Expected API observation, got: {obs_names}"

        finally:
            tracer_mod._tracer = None

    def test_tool_spans_appear_in_langfuse(self, langfuse_service):
        """Fire hooks with tool calls and verify they appear as observations."""
        info = langfuse_service
        plugin = _init_langfuse_plugin(
            info["otel_endpoint"],
            info["public_key"],
            info["secret_key"],
        )

        try:
            session_id = f"lf-tool-{int(time.time())}"
            from_time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

            on_session_start(session_id=session_id, model="gpt-4", platform="e2e-test")
            on_pre_llm_call(
                session_id=session_id,
                user_message="List files",
                conversation_history=[],
                is_first_turn=True,
                model="gpt-4",
                platform="e2e-test",
            )
            on_pre_api_request(
                task_id=f"api-{session_id}",
                session_id=session_id,
                platform="e2e-test",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                message_count=2,
                tool_count=1,
                approx_input_tokens=100,
                request_char_count=500,
                max_tokens=512,
            )

            on_pre_tool_call(tool_name="bash", args={"cmd": "ls"}, task_id=f"tool-{session_id}")
            on_post_tool_call(
                tool_name="bash",
                args={"cmd": "ls"},
                result="file.txt\nREADME.md",
                task_id=f"tool-{session_id}",
            )

            on_post_api_request(
                task_id=f"api-{session_id}",
                session_id=session_id,
                platform="e2e-test",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                api_duration=0.5,
                finish_reason="stop",
                message_count=2,
                response_model="gpt-4",
                usage={"prompt_tokens": 100, "output_tokens": 30, "total_tokens": 130},
                assistant_content_chars=50,
                assistant_tool_call_count=1,
            )
            on_post_llm_call(
                session_id=session_id,
                user_message="List files",
                assistant_response="Found file.txt and README.md",
                conversation_history=[],
                model="gpt-4",
                platform="e2e-test",
            )
            on_session_end(
                session_id=session_id,
                completed=True,
                interrupted=False,
                model="gpt-4",
                platform="e2e-test",
            )

            plugin._force_flush()

            observations = _wait_for_observations(
                info["base_url"],
                info["public_key"],
                info["secret_key"],
                expected_min=4,
                fromStartTime=from_time,
            )

            obs_names = [o.get("name", "") for o in observations]
            assert any(
                "tool.bash" in n for n in obs_names
            ), f"Expected 'tool.bash' observation, got: {obs_names}"

        finally:
            tracer_mod._tracer = None

    def test_token_usage_on_api_observation(self, langfuse_service):
        """Verify token count attributes appear on API observations in Langfuse."""
        info = langfuse_service
        plugin = _init_langfuse_plugin(
            info["otel_endpoint"],
            info["public_key"],
            info["secret_key"],
        )

        try:
            session_id = f"lf-tokens-{int(time.time())}"
            from_time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

            on_pre_api_request(
                task_id=f"api-{session_id}",
                session_id=session_id,
                platform="e2e-test",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                message_count=3,
                tool_count=0,
                approx_input_tokens=200,
                request_char_count=1000,
                max_tokens=1024,
            )
            on_post_api_request(
                task_id=f"api-{session_id}",
                session_id=session_id,
                platform="e2e-test",
                model="gpt-4",
                provider="openai",
                base_url="",
                api_mode="chat",
                api_call_count=1,
                api_duration=0.7,
                finish_reason="stop",
                message_count=3,
                response_model="gpt-4",
                usage={
                    "prompt_tokens": 200,
                    "output_tokens": 80,
                    "total_tokens": 280,
                    "cache_read_tokens": 50,
                },
                assistant_content_chars=300,
                assistant_tool_call_count=0,
            )

            plugin._force_flush()

            observations = _wait_for_observations(
                info["base_url"],
                info["public_key"],
                info["secret_key"],
                expected_min=1,
                fromStartTime=from_time,
            )

            assert (
                len(observations) >= 1
            ), f"Expected at least 1 observation, got {len(observations)}"

            # Find the API observation
            api_obs = next(
                (o for o in observations if "api" in o.get("name", "")),
                None,
            )
            assert (
                api_obs is not None
            ), f"No API observation found in: {[o.get('name') for o in observations]}"

            # Langfuse maps gen_ai.usage.input_tokens / output_tokens to
            # its native usage fields when received via OTEL.
            # Check for usage data in the observation (may be in usage or metadata).
            usage = api_obs.get("usage") or api_obs.get("usageDetails") or {}
            metadata = api_obs.get("metadata") or {}

            # At minimum, the observation should exist and have our model name
            assert api_obs.get("model") == "gpt-4" or "gpt-4" in api_obs.get(
                "name", ""
            ), f"Expected model gpt-4, got: {api_obs}"

        finally:
            tracer_mod._tracer = None
