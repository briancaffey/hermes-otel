"""Unit tests for scripts/import_elastic_dashboard.sh auth handling.

The script is exercised against a local HTTP server that records the request
headers it received. Three paths are covered: api key, basic auth, and no auth.
Credentials must never appear in the script's stdout/stderr.
"""

from __future__ import annotations

import http.server
import json
import os
import stat
import subprocess
import threading

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "import_elastic_dashboard.sh")


class _Recorder:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requests = 0
        self.status = 200

    def serve(self) -> tuple[str, threading.Thread]:
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                rec.requests += 1
                rec.headers = {k.lower(): v for k, v in self.headers.items()}
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(rec.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]

        def run() -> None:
            server.serve_forever(poll_interval=0.05)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._server = server
        return f"http://127.0.0.1:{port}", t

    def stop(self) -> None:
        self._server.shutdown()


@pytest.fixture()
def recorder():
    rec = _Recorder()
    url, thread = rec.serve()
    try:
        yield rec, url
    finally:
        rec.stop()
        thread.join(timeout=5)


def _run(url: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KIBANA_")}
    env.update(env_extra)
    return subprocess.run(
        ["/bin/bash", SCRIPT, url],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class TestAuth:
    def test_api_key_path(self, recorder):
        rec, url = recorder
        result = _run(url, {"KIBANA_API_KEY": "sekret-key"})
        assert result.returncode == 0, result.stderr
        assert "imported: HTTP 200" in result.stdout
        assert rec.headers.get("authorization") == "ApiKey sekret-key"
        assert "kbn-xsrf" in rec.headers
        # credential never echoed
        assert "sekret-key" not in result.stdout + result.stderr

    def test_basic_path(self, recorder):
        rec, url = recorder
        result = _run(url, {"KIBANA_USERNAME": "elastic", "KIBANA_PASSWORD": "hunter2"})
        assert result.returncode == 0, result.stderr
        expected = "Basic " + _b64("elastic:hunter2")
        assert rec.headers.get("authorization") == expected
        assert "hunter2" not in result.stdout + result.stderr

    def test_api_key_takes_precedence(self, recorder):
        rec, url = recorder
        result = _run(
            url,
            {"KIBANA_API_KEY": "sekret-key", "KIBANA_USERNAME": "elastic", "KIBANA_PASSWORD": "hunter2"},
        )
        assert result.returncode == 0, result.stderr
        assert rec.headers.get("authorization") == "ApiKey sekret-key"

    def test_no_auth_omits_header(self, recorder):
        rec, url = recorder
        result = _run(url, {})
        assert result.returncode == 0, result.stderr
        assert "authorization" not in rec.headers

    def test_username_without_password_fails(self, recorder):
        _, url = recorder
        result = _run(url, {"KIBANA_USERNAME": "elastic"})
        assert result.returncode != 0
        assert "KIBANA_PASSWORD" in result.stderr

    def test_401_fails_with_hint(self, recorder):
        rec, url = recorder
        rec.status = 401
        result = _run(url, {"KIBANA_USERNAME": "elastic", "KIBANA_PASSWORD": "wrong"})
        assert result.returncode != 0
        assert "HTTP 401" in result.stderr
        assert "KIBANA_API_KEY" in result.stderr
        assert "wrong" not in result.stdout + result.stderr


def _b64(s: str) -> str:
    import base64

    return base64.b64encode(s.encode()).decode()


class TestArtifact:
    def test_script_is_executable(self):
        mode = stat.S_IMODE(os.stat(SCRIPT).st_mode)
        assert mode & stat.S_IXUSR

    def test_dashboard_sums_usage_counters(self):
        """Usage panels aggregate with sum, not median (median of a counter answers nothing)."""
        path = os.path.join(REPO, "docker-compose", "elastic", "dashboards.ndjson")
        panels: dict[str, dict] = {}
        for line in open(path):
            obj = json.loads(line)
            if obj.get("type") != "dashboard":
                continue
            for pn in json.loads(obj["attributes"]["panelsJSON"]):
                at = pn.get("embeddableConfig", {}).get("attributes", {})
                panels[at.get("title", "")] = at
        token = panels["Token usage by operation"]
        cols = token["state"]["datasourceStates"]["formBased"]["layers"]
        ops = {
            (col["operationType"], col["sourceField"])
            for layer in cols.values()
            for col in layer["columns"].values()
        }
        assert ("sum", "hermes.token.usage") in ops
        assert ("median", "hermes.token.usage") not in ops

        model = panels["Messages by model"]
        cols = model["state"]["datasourceStates"]["formBased"]["layers"]
        ops = {
            (col["operationType"], col["sourceField"])
            for layer in cols.values()
            for col in layer["columns"].values()
        }
        assert ("sum", "hermes.model.usage") in ops
        assert ("median", "hermes.model.usage") not in ops
        # hermes.model.usage is a message counter ({message}), not cost (USD) —
        # hermes.cost.usage is the cost metric. No panel may claim "(cost)".
        assert all("(cost)" not in t for t in panels)
