"""Smoke test: a real Hermes turn's log records land in OpenObserve correlated with its spans (#265).

Prerequisites:
  - hermes-agent API server running:  API_SERVER_ENABLED=true hermes gateway
  - OpenObserve running (docker compose -f docker-compose/openobserve/docker-compose.yaml up -d)
  - the plugin configured with an openobserve backend and capture_logs: true
  - OPENOBSERVE_URL / OPENOBSERVE_USER / OPENOBSERVE_PASSWORD (defaults: the compose stack)

Skipped automatically when either service is not reachable.
"""

from __future__ import annotations

import base64
import os
import re
import time

import pytest

requests = pytest.importorskip("requests")
openai = pytest.importorskip("openai")
from openai import OpenAI  # noqa: E402

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(scope="session")
def openobserve():
    base = os.environ.get("OPENOBSERVE_URL", "http://localhost:5080").rstrip("/")
    user = os.environ.get("OPENOBSERVE_USER", "root@example.com")
    password = os.environ.get("OPENOBSERVE_PASSWORD", "Complexpass#123")
    org = os.environ.get("OPENOBSERVE_ORG", "default")
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    try:
        r = requests.get(f"{base}/healthz", timeout=5)
        r.raise_for_status()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"OpenObserve not reachable at {base}: {exc}")
    return {"base": base, "org": org, "headers": headers}


def _search(oo, stream, sql, start_s, end_s):
    r = requests.post(
        f"{oo['base']}/api/{oo['org']}/_search?type={stream}",
        json={
            "query": {
                "sql": sql,
                "start_time": int(start_s * 1_000_000),
                "end_time": int(end_s * 1_000_000),
                "size": 200,
            }
        },
        headers=oo["headers"],
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("hits", [])


def _wait(fn, deadline_s, every=3.0):
    while True:
        rows = fn()
        if rows or time.time() > deadline_s:
            return rows
        time.sleep(every)


def test_turn_logs_carry_the_turn_trace_id(hermes_api, openobserve):
    marker = f"smoke-logs-{int(time.time())}"
    start = time.time() - 30
    client = OpenAI(base_url=f"{hermes_api['base_url']}/v1", api_key=hermes_api["api_key"] or "x")
    resp = client.chat.completions.create(
        model="hermes",
        messages=[
            {"role": "user", "content": f"Reply with exactly the word {marker} and nothing else."}
        ],
    )
    assert resp.choices
    end = time.time() + 120

    # Any log record attributed to a turn in this window: it must carry a trace id
    # that matches a span in the traces stream.
    def logs():
        return _search(
            openobserve,
            "logs",
            "SELECT trace_id, span_id, hermes_session_id, hermes_log_attribution, body "
            "FROM \"default\" WHERE hermes_log_attribution IS NOT NULL AND trace_id != ''",
            start,
            time.time(),
        )

    rows = _wait(logs, end)
    assert rows, "no attributed log record reached OpenObserve within 2 minutes"
    trace_ids = {r["trace_id"] for r in rows}
    assert all(_HEX32.match(t) for t in trace_ids), trace_ids
    assert {r["hermes_log_attribution"] for r in rows} <= {
        "context",
        "session_tag",
        "single_session",
    }
    assert all(r.get("hermes_session_id") for r in rows)
    assert not any("hermes_home" in r for r in rows)

    def spans():
        tid = next(iter(trace_ids))
        return _search(
            openobserve,
            "traces",
            f"SELECT trace_id FROM \"default\" WHERE trace_id = '{tid}'",
            start,
            time.time(),
        )

    assert _wait(spans, end), "the log's trace_id matches no span in the traces stream"
