"""Unit tests for scripts/import_elastic_dashboard.sh auth handling.

The script is exercised against a local HTTP server that records the request
headers it received. Three paths are covered: api key, basic auth, and no auth.
Credentials must never appear in the script's stdout/stderr.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import stat
import subprocess
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "import_elastic_dashboard.sh")


class _Recorder:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.requests = 0
        self.status = 200
        self.body = b""
        self.delay = 0.0

    def serve(self) -> tuple[str, threading.Thread]:
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                rec.requests += 1
                rec.headers = {k.lower(): v for k, v in self.headers.items()}
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                if rec.delay:
                    time.sleep(rec.delay)
                self.send_response(rec.status)
                self.send_header("Content-Length", str(len(rec.body)))
                self.end_headers()
                self.wfile.write(rec.body)

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
            {
                "KIBANA_API_KEY": "sekret-key",
                "KIBANA_USERNAME": "elastic",
                "KIBANA_PASSWORD": "hunter2",
            },
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

    @pytest.mark.parametrize(
        "env_extra",
        [
            {"KIBANA_USERNAME": "eve\nil", "KIBANA_PASSWORD": "pw-secret"},
            {"KIBANA_USERNAME": "elastic", "KIBANA_PASSWORD": "pw\nsecret"},
            {"KIBANA_USERNAME": "elastic\r", "KIBANA_PASSWORD": "pw-secret"},
            {"KIBANA_API_KEY": "key\nwith-newline"},
            {"KIBANA_API_KEY": "key\rwith-cr"},
        ],
        ids=[
            "username_newline",
            "password_newline",
            "username_carriage_return",
            "api_key_newline",
            "api_key_carriage_return",
        ],
    )
    def test_newline_in_credentials_rejected_before_request(self, recorder, env_extra):
        rec, url = recorder
        result = _run(url, env_extra)
        assert result.returncode == 1
        assert "must not contain newlines" in result.stderr
        assert rec.requests == 0, "no request may reach the server"

    def test_401_fails_with_hint(self, recorder):
        rec, url = recorder
        rec.status = 401
        result = _run(url, {"KIBANA_USERNAME": "elastic", "KIBANA_PASSWORD": "wrong"})
        assert result.returncode != 0
        assert "HTTP 401" in result.stderr
        assert "KIBANA_API_KEY" in result.stderr
        assert "wrong" not in result.stdout + result.stderr


def _free_port() -> int:
    """A localhost port with nothing listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestCredentialsNotInArgv:
    """Secrets must reach curl on stdin, never on its command line (`ps`).

    A curl shim earlier on PATH records the argv it was exec'd with (exactly what
    `ps` would show) and then hands over to the real curl. A `ps` snapshot is not
    used because macOS hides argv of system binaries, which would pass vacuously.
    """

    @staticmethod
    def _run_with_curl_shim(
        url: str, env_extra: dict[str, str], tmp_path
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        real_curl = shutil.which("curl")
        assert real_curl, "curl is required"
        argv_log = tmp_path / "curl-argv.log"
        shim_dir = tmp_path / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "curl"
        shim.write_text(
            "#!/bin/bash\n" f'printf "%s\\n" "$@" >> "{argv_log}"\n' f'exec "{real_curl}" "$@"\n'
        )
        shim.chmod(0o755)
        result = _run(url, {**env_extra, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"})
        return result, argv_log.read_text() if argv_log.exists() else ""

    @pytest.mark.parametrize(
        "env_extra, secrets",
        [
            ({"KIBANA_API_KEY": "argv-sekret-key-123"}, ["argv-sekret-key-123"]),
            (
                {"KIBANA_USERNAME": "argv-user-xyz", "KIBANA_PASSWORD": "argv-hunter-789"},
                ["argv-user-xyz", "argv-hunter-789"],
            ),
        ],
        ids=["api_key", "basic"],
    )
    def test_secret_absent_from_curl_argv(self, recorder, tmp_path, env_extra, secrets):
        rec, url = recorder
        result, argv = self._run_with_curl_shim(url, env_extra, tmp_path)
        assert result.returncode == 0, result.stderr
        # Guard against a vacuous pass: the shim must really have wrapped curl.
        assert url in argv
        for secret in secrets:
            assert secret not in argv
        # ...and the credentials still reached the server.
        assert rec.headers.get("authorization")

    def test_special_chars_survive_config_escaping(self, recorder):
        rec, url = recorder
        key = 'a"b\\c d'
        result = _run(url, {"KIBANA_API_KEY": key})
        assert result.returncode == 0, result.stderr
        assert rec.headers.get("authorization") == f"ApiKey {key}"


class TestTransportFailure:
    def test_unreachable_port_reports_clearly(self):
        url = f"http://127.0.0.1:{_free_port()}"
        result = _run(url, {})
        assert result.returncode == 1
        assert f"could not reach Kibana at {url}" in result.stderr

    def test_unreachable_does_not_leak_credentials(self):
        url = f"http://127.0.0.1:{_free_port()}"
        result = _run(url, {"KIBANA_API_KEY": "sekret-key"})
        assert result.returncode == 1
        assert "sekret-key" not in result.stdout + result.stderr


class TestKibanaUrl:
    def test_trailing_slash_is_tolerated(self, recorder):
        rec, url = recorder
        result = _run(url + "/", {})
        assert result.returncode == 0, result.stderr
        assert rec.requests == 1
        assert f"Open {url}/app/dashboards" in result.stdout  # no doubled slash


class TestImportBody:
    def test_success_reports_count(self, recorder):
        rec, url = recorder
        rec.body = b'{"success":true,"successCount":8,"successResults":[],"warnings":[]}'
        result = _run(url, {})
        assert result.returncode == 0, result.stderr
        assert "imported: HTTP 200, successCount=8" in result.stdout

    def test_http_200_with_success_false_fails_and_lists_ids(self, recorder):
        rec, url = recorder
        rec.body = json.dumps(
            {
                "success": False,
                "successCount": 6,
                "errors": [
                    {
                        "id": "hermes-otel-metrics",
                        "type": "index-pattern",
                        "title": "metrics-*",
                        "meta": {"title": "metrics-*"},
                        "error": {"type": "conflict"},
                    },
                    {"id": "panel-two", "type": "lens", "error": {"type": "missing_references"}},
                ],
            },
            indent=2,
        ).encode()
        result = _run(url, {})
        assert result.returncode == 1
        assert "imported" not in result.stdout
        assert "hermes-otel-metrics" in result.stderr
        assert "panel-two" in result.stderr


def _b64(s: str) -> str:
    import base64

    return base64.b64encode(s.encode()).decode()


class TestArtifact:
    def test_script_is_executable(self):
        mode = stat.S_IMODE(os.stat(SCRIPT).st_mode)
        assert mode & stat.S_IXUSR

    def test_tool_duration_panel_is_esql(self):
        """The tool-duration panel must read from traces via ES|QL.

        hermes.tool.duration maps as `histogram` on the EDOT->ES stack but
        `exponential_histogram` on managed-OTLP serverless; no single Lens
        agg works on both. The span `duration` field (plain long) does, so
        the panel runs ES|QL over traces-* instead of the metrics index."""
        path = os.path.join(REPO, "docker-compose", "elastic", "dashboards.ndjson")
        objs = [json.loads(line) for line in open(path)]
        dash = next(o for o in objs if o["type"] == "dashboard")
        panel = next(
            pn
            for pn in json.loads(dash["attributes"]["panelsJSON"])
            if pn["embeddableConfig"]["attributes"]["title"] == "Tool call duration by tool"
        )
        state = panel["embeddableConfig"]["attributes"]["state"]
        layers = state["datasourceStates"]["textBased"]["layers"]
        (query,) = [layer["query"]["esql"] for layer in layers.values()]
        assert "FROM traces-*" in query
        assert "MEDIAN(" in query
        # must not aggregate the metrics histogram field at all
        assert "hermes.tool.duration" not in query

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

    def test_histogram_metric_not_in_datatable(self):
        """A Lens datatable probes every column for click-to-filter on load. The
        `hermes.tool.duration` ES histogram field is not filterable, so a datatable
        column over it throws a console TypeError (`field can not be used for
        filtering`). It must be read through ES|QL or an XY chart instead, never a
        form-based datatable column; every histogram field the stack maps gets the
        same treatment (`hermes.session.duration` is one too on EDOT 9.5.5)."""
        path = os.path.join(REPO, "docker-compose", "elastic", "dashboards.ndjson")
        objs = [json.loads(line) for line in open(path)]
        metrics = next(o for o in objs if o["id"] == "hermes-otel-metrics")
        fields = {f["name"]: f for f in json.loads(metrics["attributes"]["fields"])}
        histogram_fields = {n for n, f in fields.items() if f["type"] == "histogram"}
        assert {"hermes.tool.duration", "hermes.session.duration"} <= histogram_fields
        for name in histogram_fields:
            assert fields[name]["esTypes"] == ["histogram"], name
            assert not fields[name]["searchable"], name

        dash = next(o for o in objs if o["type"] == "dashboard")
        offenders = []
        for pn in json.loads(dash["attributes"]["panelsJSON"]):
            at = pn["embeddableConfig"]["attributes"]
            if at["visualizationType"] != "lnsDatatable":
                continue
            # ES|QL (textBased) panels never carry formBased layers; the
            # histogram-metric rule only applies to form-based datatables.
            layers = at["state"]["datasourceStates"].get("formBased", {}).get("layers", {})
            for layer in layers.values():
                for col in layer["columns"].values():
                    if col.get("sourceField") in histogram_fields:
                        offenders.append((at["title"], col["sourceField"]))
        assert offenders == []
