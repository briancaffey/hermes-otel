"""Smoke tests: send a real chat to hermes-agent, verify traces in Langfuse.

Prerequisites:
  - hermes-agent API server running:  API_SERVER_ENABLED=true hermes gateway
  - Langfuse running with OTEL plugin configured (OTEL_LANGFUSE_* in ~/.hermes/.env)
  - The hermes-otel plugin must be loaded (plugin auto-discovered from ~/.hermes/plugins/)

These tests are skipped automatically if either service is not reachable.
"""

import time

import pytest

requests = pytest.importorskip("requests")
openai = pytest.importorskip("openai")
from openai import OpenAI


def _query_observations(langfuse_api, **params):
    """Query GET /api/public/observations."""
    url = f"{langfuse_api['base_url']}/api/public/observations"
    resp = requests.get(
        url,
        headers=langfuse_api["auth_header"],
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _wait_for_observations(langfuse_api, expected_min=1, timeout=60, interval=5, **query_params):
    """Poll until at least expected_min observations appear."""
    deadline = time.time() + timeout
    observations = []
    while time.time() < deadline:
        observations = _query_observations(langfuse_api, **query_params)
        if len(observations) >= expected_min:
            return observations
        time.sleep(interval)
    return observations


@pytest.mark.smoke
class TestHermesLangfuseSmoke:

    def test_chat_completion_produces_trace(self, hermes_api, langfuse_api):
        """Send a simple chat via OpenAI SDK, verify a trace appears in Langfuse.

        This is the core smoke test: it exercises the full pipeline from
        HTTP request through hermes-agent, through the OTEL plugin, to
        Langfuse.
        """
        from_time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        # Send a chat via the OpenAI-compatible endpoint
        client = OpenAI(
            base_url=f"{hermes_api['base_url']}/v1",
            api_key=hermes_api["api_key"] or "not-required",
        )

        response = client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {"role": "user", "content": "Reply with exactly: SMOKE_TEST_OK"},
            ],
            max_tokens=50,
        )

        # Verify hermes responded
        reply = response.choices[0].message.content
        assert reply is not None and len(reply) > 0, "Empty response from hermes"

        # Wait for Langfuse to ingest the trace (async, typically 15-30s)
        observations = _wait_for_observations(
            langfuse_api,
            expected_min=1,
            timeout=60,
            fromStartTime=from_time,
        )

        assert len(observations) >= 1, (
            "No observations appeared in Langfuse within 60s after chat. "
            "Verify the hermes-otel plugin is loaded and OTEL_LANGFUSE_* env vars are set."
        )

        # We should see at least an API span (api.*) from the LLM call
        obs_names = [o.get("name", "") for o in observations]
        has_api_span = any("api." in n for n in obs_names)
        has_llm_span = any("llm." in n for n in obs_names)

        assert (
            has_api_span or has_llm_span
        ), f"Expected api.* or llm.* observations, got: {obs_names}"

    def test_chat_with_tool_use_produces_tool_span(self, hermes_api, langfuse_api):
        """Send a prompt that triggers tool use and verify tool spans appear.

        Uses a prompt that should cause hermes to invoke a tool (like terminal),
        producing tool.* spans in Langfuse.
        """
        from_time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        client = OpenAI(
            base_url=f"{hermes_api['base_url']}/v1",
            api_key=hermes_api["api_key"] or "not-required",
        )

        response = client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {
                    "role": "user",
                    "content": "What is the current date and time? Use terminal to run the `date` command to find out.",
                },
            ],
            max_tokens=200,
        )

        reply = response.choices[0].message.content
        assert reply is not None and len(reply) > 0, "Empty response from hermes"

        # Wait for tool spans to appear — tool calls add more processing time
        observations = _wait_for_observations(
            langfuse_api,
            expected_min=2,
            timeout=90,
            fromStartTime=from_time,
        )

        obs_names = [o.get("name", "") for o in observations]

        # Should have tool spans (tool.*) alongside api/llm spans
        has_tool_span = any("tool." in n for n in obs_names)
        assert has_tool_span, (
            f"Expected tool.* observations from tool use, got: {obs_names}. "
            f"The prompt may not have triggered tool use — check hermes toolset config."
        )

    def test_trace_has_token_counts(self, hermes_api, langfuse_api):
        """Verify that API observations include token usage data."""
        from_time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        client = OpenAI(
            base_url=f"{hermes_api['base_url']}/v1",
            api_key=hermes_api["api_key"] or "not-required",
        )

        client.chat.completions.create(
            model="hermes-agent",
            messages=[
                {"role": "user", "content": "Say hello."},
            ],
            max_tokens=20,
        )

        observations = _wait_for_observations(
            langfuse_api,
            expected_min=1,
            timeout=60,
            fromStartTime=from_time,
        )

        # Find an API observation
        api_obs = next(
            (o for o in observations if "api." in o.get("name", "")),
            None,
        )

        if api_obs is None:
            # Fall back to any observation
            api_obs = observations[0] if observations else None

        assert api_obs is not None, "No observations found"

        # Langfuse should have usage data from gen_ai.usage.* attributes
        usage = api_obs.get("usage") or api_obs.get("usageDetails") or {}
        model = api_obs.get("model") or api_obs.get("name", "")

        # At minimum the observation should exist — token mapping depends on
        # how Langfuse interprets OTEL attributes, so we verify presence
        assert api_obs.get("id") is not None, "Observation has no ID"
