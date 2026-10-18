"""``otel.py mcp-config`` (#304): the backend → opentelemetry-mcp mapping and the apply path."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from hermes_otel import mcp_config as mc
from hermes_otel import query_cli, query_sources

J = {"type": "jaeger", "name": "jaeger-local", "endpoint": "http://localhost:4318/v1/traces"}
T = {"type": "tempo", "endpoint": "http://tempo.lan:4358/v1/traces", "query_port": 3210}
L = {"type": "lgtm", "endpoint": "https://grafana.example.com:4318/v1/traces"}


class TestQueryTarget:
    def test_jaeger_defaults_to_the_ui_port(self):
        t = mc.query_target(J)
        assert isinstance(t, mc.QueryTarget)
        assert t.env() == {"BACKEND_TYPE": "jaeger", "BACKEND_URL": "http://localhost:16686"}
        assert t.name == "jaeger-local"

    def test_tempo_honours_query_port_and_host(self):
        t = mc.query_target(T)
        assert t.env() == {"BACKEND_TYPE": "tempo", "BACKEND_URL": "http://tempo.lan:3210"}

    def test_lgtm_is_tempo_on_3200_keeping_the_scheme(self):
        t = mc.query_target(L)
        assert t.server_backend_type == "tempo"
        assert t.backend_url == "https://grafana.example.com:3200"

    def test_bad_query_port_falls_back(self):
        t = mc.query_target({**J, "query_port": "ui"})
        assert t.backend_url == "http://localhost:16686"

    def test_no_endpoint_means_localhost(self):
        assert mc.query_target({"type": "tempo"}).backend_url == "http://localhost:3200"

    def test_jaeger_v2_is_refused(self):
        r = mc.query_target({**J, "query_api": "v3"})
        assert isinstance(r, mc.NotQueryable) and "/api/v3" in r.reason

    @pytest.mark.parametrize("btype", ["gigapipe", "victoria", "greptime"])
    def test_compatible_apis_are_listed_as_unverified(self, btype):
        r = mc.query_target({"type": btype, "endpoint": "http://h:1/v1/traces"})
        assert isinstance(r, mc.NotQueryable) and "not verified" in r.reason

    def test_other_types_are_not_queryable(self):
        r = mc.query_target({"type": "phoenix", "endpoint": "http://localhost:6006/v1/traces"})
        assert isinstance(r, mc.NotQueryable)
        assert r.name == "phoenix" and "Jaeger or Tempo" in r.reason

    def test_never_copies_credentials(self):
        t = mc.query_target({**J, "api_key": "sekrit", "headers": {"Authorization": "x"}})
        assert "sekrit" not in json.dumps(t.env()) and set(t.env()) == {
            "BACKEND_TYPE",
            "BACKEND_URL",
        }


class TestPick:
    def test_source_then_pin_then_first(self):
        results = mc.classify([{"type": "phoenix"}, J, T])
        assert mc.pick_target(results).name == "jaeger-local"
        assert mc.pick_target(results, pin="tempo").backend_type == "tempo"
        assert mc.pick_target(results, wanted="tempo", pin="jaeger-local").backend_type == "tempo"

    def test_pin_that_is_not_queryable_falls_through(self):
        results = mc.classify([{"type": "phoenix"}, J])
        assert mc.pick_target(results, pin="phoenix").name == "jaeger-local"

    def test_wanted_that_is_not_queryable_is_none(self):
        results = mc.classify([{"type": "phoenix"}, J])
        assert mc.pick_target(results, wanted="phoenix") is None
        assert mc.pick_target([], wanted=None) is None


class TestRender:
    def test_yaml_block_and_commands_pin_the_version(self):
        t = mc.query_target(J)
        y = mc.render_yaml(t, "otel")
        assert "mcp_servers:\n  otel:\n    command: uvx" in y
        assert f"- {mc.MCP_PACKAGE}=={mc.MCP_PACKAGE_VERSION}" in y
        assert "BACKEND_URL: http://localhost:16686" in y and "connect_timeout: 60.0" in y
        argv = mc.hermes_add_argv(t, "otel")
        assert argv[:4] == ["hermes", "mcp", "add", "otel"]
        assert argv[-2:] == ["--args", mc.MCP_PACKAGE_SPEC], "--args must stay last"
        assert "BACKEND_TYPE=jaeger" in argv and "BACKEND_URL=http://localhost:16686" in argv
        assert mc.render_claude_code_add(t).startswith("claude mcp add otel -e BACKEND_TYPE=jaeger")
        js = mc.render_mcp_json(t)
        assert js["mcpServers"]["otel"]["args"] == [mc.MCP_PACKAGE_SPEC]
        assert js["mcpServers"]["otel"]["env"]["BACKEND_URL"] == "http://localhost:16686"


class TestApply:
    def _home(self, tmp_path: Path, servers=None) -> Path:
        body = "model: {default: x}\n"
        if servers:
            body += "mcp_servers:\n" + "".join(f"  {s}:\n    command: uvx\n" for s in servers)
        (tmp_path / "config.yaml").write_text(body)
        return tmp_path

    def test_existing_names(self, tmp_path):
        assert mc.existing_server_names(self._home(tmp_path, ["otel", "fs"])) == ["fs", "otel"]
        assert mc.existing_server_names(self._home(tmp_path)) == []
        assert mc.existing_server_names(tmp_path / "missing") == []

    def test_adds_when_absent(self, tmp_path):
        calls = []
        ran = mc.apply(
            mc.query_target(J), self._home(tmp_path), runner=lambda a, **k: calls.append((a, k))
        )
        assert ran == [mc.hermes_add_argv(mc.query_target(J))]
        assert calls[0][1]["input"] == "Y\n" and calls[0][1]["check"] is True

    def test_refuses_to_overwrite_without_force(self, tmp_path):
        with pytest.raises(mc.ApplyRefused):
            mc.apply(
                mc.query_target(J), self._home(tmp_path, ["otel"]), runner=lambda *a, **k: None
            )

    def test_force_removes_then_adds(self, tmp_path):
        calls = []
        ran = mc.apply(
            mc.query_target(J),
            self._home(tmp_path, ["otel"]),
            force=True,
            runner=lambda a, **k: calls.append(a),
        )
        assert ran[0] == ["hermes", "mcp", "remove", "otel"] and ran[1][:3] == [
            "hermes",
            "mcp",
            "add",
        ]
        assert calls == ran


def _fake_backends(entries, pin=None):
    class FakeBackends:
        @staticmethod
        def load_config():
            return (Path("/cfg.yaml"), entries, pin)

    return FakeBackends


def _run(monkeypatch, tmp_path, *argv, entries, pin=None):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(query_sources, "_import_backends", lambda: _fake_backends(entries, pin))
    out, err = io.StringIO(), io.StringIO()
    code = query_cli.main(["mcp-config", *argv], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TestCli:
    def test_text_output_lists_every_entry_and_renders_the_pin(self, monkeypatch, tmp_path):
        code, out, _ = _run(monkeypatch, tmp_path, entries=[{"type": "phoenix"}, J, T], pin="tempo")
        assert code == 0
        assert "phoenix        phoenix      not queryable over MCP" in out
        assert "jaeger-local   jaeger       queryable over MCP: BACKEND_TYPE=jaeger" in out
        assert "selected      tempo (tempo)" in out
        assert "BACKEND_URL: http://tempo.lan:3210" in out
        assert "hermes mcp add otel --command uvx" in out and "claude mcp add otel" in out
        assert "mcpServers" in out and "#caveats" in out

    def test_json_output(self, monkeypatch, tmp_path):
        code, out, _ = _run(monkeypatch, tmp_path, "--json", "--name", "traces", entries=[J])
        assert code == 0
        d = json.loads(out)
        assert d["server_name"] == "traces" and d["selected"] == "jaeger-local"
        assert d["backends"][0]["env"]["BACKEND_URL"] == "http://localhost:16686"
        assert (
            d["hermes_mcp_add"][3] == "traces"
            and "mcp_servers:\n  traces:" in d["mcp_servers_yaml"]
        )
        assert d["existing_mcp_servers"] == []

    def test_nothing_queryable_is_not_an_error(self, monkeypatch, tmp_path):
        code, out, _ = _run(monkeypatch, tmp_path, entries=[{"type": "phoenix"}])
        assert code == 0 and "No entry the server can read" in out

    def test_no_backends(self, monkeypatch, tmp_path):
        code, out, _ = _run(monkeypatch, tmp_path, entries=[])
        assert code == 0 and "none configured" in out

    def test_bad_source_is_an_error(self, monkeypatch, tmp_path):
        code, _, err = _run(
            monkeypatch, tmp_path, "--source", "phoenix", entries=[{"type": "phoenix"}, J]
        )
        assert code == 1 and "names no configured backend" in err

    def test_apply_refusal_surfaces_as_cli_error(self, monkeypatch, tmp_path):
        (tmp_path / "config.yaml").write_text("mcp_servers:\n  otel:\n    command: uvx\n")
        code, out, err = _run(monkeypatch, tmp_path, "--apply", entries=[J])
        assert code == 1 and "already exists" in err and "--force" in err

    def test_apply_runs_hermes(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(
            mc, "subprocess", type("S", (), {"run": staticmethod(lambda a, **k: calls.append(a))})
        )
        # ``apply``'s default runner was bound at import; patch the function's default instead.
        monkeypatch.setattr(
            mc.apply,
            "__defaults__",
            (mc.DEFAULT_SERVER_NAME, False, lambda a, **k: calls.append(a)),
        )
        code, out, _ = _run(monkeypatch, tmp_path, "--apply", entries=[J])
        assert code == 0 and calls and calls[0][:3] == ["hermes", "mcp", "add"]
        assert "applied:" in out and "/reload-mcp" in out

    def test_apply_with_nothing_queryable(self, monkeypatch, tmp_path):
        code, _, err = _run(monkeypatch, tmp_path, "--apply", entries=[{"type": "phoenix"}])
        assert code == 1 and "nothing to apply" in err
