"""``headers_helper:`` (#483): parsing, the subprocess contract, redaction, the
cache and ``refresh()``, and how the entry reaches the exporters through
``BackendConfig`` → ``resolve()`` → ``target()`` and the settings card.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import hermes_otel.debug_utils as du
from hermes_otel import headers_helper as hh
from hermes_otel.backend_config import BackendConfig, coerce_backends
from hermes_otel.backends import _ResolvedBackend, resolve
from hermes_otel.headers_helper import (
    HeadersHelper,
    HeadersHelperError,
    describe_headers,
    headers_helper_for,
    parse_headers,
)

# ── helper scripts, run through the real subprocess module ─────────────


def script(tmp_path: Path, body: str, name: str = "helper.py") -> str:
    """A python script as the ``headers_helper`` command string (argv, quoted)."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"


PRINTS_KV = 'print("Authorization=Bearer first-token-value-0001")\nprint("x-tenant=acme")\n'
PRINTS_JSON = 'import json\nprint(json.dumps({"Authorization": "Bearer t-1", "x-n": 7}))\n'
# A real interpreter start can take seconds on a loaded runner; the default
# 5000 ms would turn a test that spawns one into a timeout failure.
SLOW_START_MS = 60_000


class TestParse:
    def test_kv_lines_with_blanks_and_comments(self):
        text = "# minted\n\nAuthorization = Bearer abc\nx-tenant=acme\n"
        assert parse_headers(text) == {"Authorization": "Bearer abc", "x-tenant": "acme"}

    def test_value_may_contain_equals(self):
        assert parse_headers("Authorization=Basic dXNlcjpwYXNz==") == {
            "Authorization": "Basic dXNlcjpwYXNz=="
        }

    def test_json_object(self):
        assert parse_headers('{"Authorization": "Bearer abc", "x-n": 7}') == {
            "Authorization": "Bearer abc",
            "x-n": "7",
        }

    @pytest.mark.parametrize("text", ["", "   \n\n", "# only a comment\n"])
    def test_empty_output_is_an_error(self, text):
        with pytest.raises(HeadersHelperError, match="printed no headers"):
            parse_headers(text)

    @pytest.mark.parametrize(
        "text, match",
        [
            ("Authorization Bearer abc", "line 1 is not Name=value"),
            ("ok=1\n=novalue", "line 2 is not Name=value"),
            ("{not json", "invalid JSON"),
            ('["a", "b"]', "printed no headers|not Name=value"),
            ('{"a": [1]}', "must be a string"),
            ('{"a": true}', "must be a string"),
            ('{"bad name": "x"}', "not a header name"),
            ("bad:name=x", "not a header name"),
        ],
    )
    def test_malformed_output_is_an_error(self, text, match):
        with pytest.raises(HeadersHelperError, match=match):
            parse_headers(text)

    def test_json_array_is_an_error(self):
        with pytest.raises(HeadersHelperError):
            parse_headers("[1, 2]")


class TestRun:
    def test_kv_script(self, tmp_path):
        helper = HeadersHelper(script(tmp_path, PRINTS_KV), timeout_ms=SLOW_START_MS)
        assert helper.headers() == {
            "Authorization": "Bearer first-token-value-0001",
            "x-tenant": "acme",
        }
        assert helper.runs == 1

    def test_json_script(self, tmp_path):
        helper = HeadersHelper(script(tmp_path, PRINTS_JSON), timeout_ms=SLOW_START_MS)
        assert helper.headers() == {"Authorization": "Bearer t-1", "x-n": "7"}

    def test_argv_is_split_with_shlex_not_a_shell(self, tmp_path):
        # A quoted argument with a space and a shell metacharacter stays one
        # argument and is never interpreted: no shell is involved.
        body = "import sys\nprint('x-arg=' + sys.argv[1])\n"
        cmd = script(tmp_path, body) + " 'two words; echo pwned'"
        helper = HeadersHelper(cmd, timeout_ms=SLOW_START_MS)
        assert helper.argv[-1] == "two words; echo pwned"
        assert helper.headers() == {"x-arg": "two words; echo pwned"}

    def test_empty_or_unbalanced_command_is_refused_at_config_time(self):
        with pytest.raises(HeadersHelperError, match="empty"):
            HeadersHelper("   ")
        with pytest.raises(HeadersHelperError, match="quotation"):
            HeadersHelper("helper 'unbalanced")

    def test_non_zero_exit_names_the_code_and_redacts_stderr(self, tmp_path):
        body = (
            "import sys\n"
            "sys.stderr.write('refresh failed: token=sk-ant-abcdefghijklmnopqrstuvwxyz\\n')\n"
            "sys.exit(3)\n"
        )
        helper = HeadersHelper(script(tmp_path, body), timeout_ms=SLOW_START_MS)
        with pytest.raises(HeadersHelperError) as exc:
            helper.headers()
        msg = str(exc.value)
        assert "exited 3" in msg
        assert "refresh failed" in msg
        assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in msg
        assert helper.runs == 1  # it ran; the failure is the exit code

    def test_timeout(self, tmp_path):
        body = "import time\ntime.sleep(5)\nprint('a=b')\n"
        helper = HeadersHelper(script(tmp_path, body), timeout_ms=200)
        with pytest.raises(HeadersHelperError, match="did not finish within 200 ms"):
            helper.headers()

    def test_missing_executable(self, tmp_path):
        helper = HeadersHelper(str(tmp_path / "no-such-helper"))
        with pytest.raises(HeadersHelperError, match="could not be run"):
            helper.headers()

    def test_malformed_output_names_the_helper(self, tmp_path):
        helper = HeadersHelper(
            script(tmp_path, "print('no equals sign here')\n"), timeout_ms=SLOW_START_MS
        )
        with pytest.raises(HeadersHelperError, match="line 1 is not Name=value"):
            helper.headers()

    def test_runs_with_the_plugin_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HO_TEST_TOKEN", "from-env")
        body = "import os\nprint('x-token=' + os.environ['HO_TEST_TOKEN'])\n"
        helper = HeadersHelper(script(tmp_path, body), timeout_ms=SLOW_START_MS)
        assert helper.headers() == {"x-token": "from-env"}

    def test_default_timeout(self):
        assert HeadersHelper("helper").timeout_ms == hh.DEFAULT_TIMEOUT_MS
        assert HeadersHelper("helper", timeout_ms=None).timeout_ms == hh.DEFAULT_TIMEOUT_MS
        assert HeadersHelper("helper", timeout_ms=250).timeout_ms == 250

    def test_subprocess_is_called_without_a_shell(self):
        calls = []

        def runner(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, stdout="a=b\n", stderr="")

        HeadersHelper("my-helper --flag value", timeout_ms=1234, runner=runner).headers()
        argv, kw = calls[0]
        assert argv == ["my-helper", "--flag", "value"]
        assert kw["shell"] is False
        assert kw["timeout"] == pytest.approx(1.234)
        assert kw["capture_output"] is True and kw["text"] is True


class TestCacheAndRefresh:
    def _counting_script(self, tmp_path: Path) -> str:
        # Each run prints a token read from a file the test rewrites, and a
        # counter so the test can tell runs apart.
        (tmp_path / "token").write_text("first", encoding="utf-8")
        body = (
            "import pathlib\n"
            f"token = pathlib.Path({str(tmp_path / 'token')!r}).read_text().strip()\n"
            "print('Authorization=Bearer ' + token)\n"
        )
        return script(tmp_path, body)

    def _helper(self, tmp_path: Path) -> HeadersHelper:
        return HeadersHelper(self._counting_script(tmp_path), timeout_ms=SLOW_START_MS)

    def test_headers_runs_once_and_caches(self, tmp_path):
        helper = self._helper(tmp_path)
        assert helper.headers()["Authorization"] == "Bearer first"
        (tmp_path / "token").write_text("second", encoding="utf-8")
        assert helper.headers()["Authorization"] == "Bearer first"  # cached
        assert helper.runs == 1

    def test_refresh_after_reruns_and_replaces(self, tmp_path):
        helper = self._helper(tmp_path)
        helper.headers()
        (tmp_path / "token").write_text("second", encoding="utf-8")
        minted = helper.refresh_after(1)
        assert minted.headers == {"Authorization": "Bearer second"} and minted.ran
        assert helper.headers() == {"Authorization": "Bearer second"}
        assert helper.runs == 2

    def test_failed_refresh_keeps_the_old_set(self, tmp_path):
        helper = self._helper(tmp_path)
        helper.headers()
        # Injected failure: a real failing interpreter is timing-dependent.
        helper._runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 9, "", "")
        with pytest.raises(HeadersHelperError, match="exited 9"):
            helper.refresh_after(1)
        assert helper.headers() == {"Authorization": "Bearer first"}

    def test_returns_copies(self, tmp_path):
        helper = self._helper(tmp_path)
        first = helper.headers()
        first["Authorization"] = "tampered"
        assert helper.headers()["Authorization"] == "Bearer first"


# ── one helper, several exporters' sessions (#584) ──────────────────────


class FakeRunner:
    """A ``subprocess.run`` stand-in: run N prints ``Bearer t<N>`` (and, while
    ``extra`` is set, that header too); ``hold`` keeps a run in progress."""

    def __init__(self) -> None:
        self.calls = 0
        self.extra: dict = {"x-old": "1"}
        self.hold = None  # threading.Event to wait on inside a run
        self.entered = threading.Event()

    def __call__(self, argv, **kwargs):
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(5)
        self.calls += 1
        lines = [f"Authorization=Bearer t{self.calls}"]
        lines += [f"{k}={v}" for k, v in self.extra.items()]
        return subprocess.CompletedProcess(argv, 0, stdout="\n".join(lines), stderr="")


def fake_helper() -> tuple:
    runner = FakeRunner()
    return HeadersHelper("mint", runner=runner), runner


class TestGenerations:
    def test_current_never_runs_and_snapshot_counts_from_one(self):
        helper, runner = fake_helper()
        assert helper.current() is None and runner.calls == 0
        assert helper.snapshot()[0] == 1
        assert helper.current()[0] == 1
        helper.refresh_after(1)
        assert helper.current() == (2, {"Authorization": "Bearer t2", "x-old": "1"})

    def test_refresh_after_a_stale_generation_takes_the_newer_set(self):
        helper, runner = fake_helper()
        helper.headers()
        first = helper.refresh_after(1)
        assert (first.generation, first.ran) == (2, True)
        # Refused with generation 1 too, but 2 already exists: no second run.
        again = helper.refresh_after(1)
        assert (again.generation, again.ran) == (2, False)
        assert again.headers == first.headers
        assert runner.calls == helper.runs == 2

    def test_failed_refresh_after_keeps_the_generation(self):
        helper, runner = fake_helper()
        helper.headers()
        helper._runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 3, "", "")
        with pytest.raises(HeadersHelperError, match="exited 3"):
            helper.refresh_after(1)
        assert helper.current()[0] == 1

    def test_concurrent_refusals_with_one_generation_run_once(self):

        helper, runner = fake_helper()
        helper.headers()
        runner.hold = threading.Event()
        start = threading.Barrier(8)
        answers = []

        def refused() -> None:
            start.wait(5)
            answers.append(helper.refresh_after(1))

        threads = [threading.Thread(target=refused) for _ in range(8)]
        for t in threads:
            t.start()
        runner.hold.set()
        for t in threads:
            t.join(5)
        assert runner.calls == 2  # the first mint + exactly one refresh
        assert sorted(a.ran for a in answers) == [False] * 7 + [True]
        assert {a.generation for a in answers} == {2}

    def test_current_does_not_wait_on_a_run_in_progress(self):

        helper, runner = fake_helper()
        helper.headers()
        runner.hold = threading.Event()
        runner.entered.clear()
        t = threading.Thread(target=helper.refresh_after, args=(1,))
        t.start()
        try:
            assert runner.entered.wait(5)
            assert helper.current()[0] == 1  # the run holds the lock; this returns
        finally:
            runner.hold.set()
            t.join(5)
        assert helper.current()[0] == 2


requests = pytest.importorskip("requests")


class FakeBackend(requests.adapters.BaseAdapter):
    """Answers 200 to the accepted bearer token, 401 otherwise, and records
    ``(session label, Authorization, x-old)`` per request. ``refusal_barrier``
    holds every refused request until the other session is refused too."""

    def __init__(self, accepted: str) -> None:
        super().__init__()
        self.accepted = accepted
        self.seen: list = []
        self.refusal_barrier = None

    def send(self, request, **kwargs):
        auth = request.headers.get("Authorization")
        self.seen.append((request.headers.get("x-label"), auth, request.headers.get("x-old")))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response._content = b""
        response.status_code = 200 if auth == f"Bearer {self.accepted}" else 401
        if response.status_code == 401 and self.refusal_barrier is not None:
            self.refusal_barrier.wait(5)
        return response

    def close(self) -> None:
        pass


def session_on(helper, backend, label: str):
    from hermes_otel.headers_helper import RefreshingSession

    session = RefreshingSession(helper, label)
    # What the exporter does with ``headers=`` at construction.
    session.headers.update(helper.headers())
    session.headers["x-label"] = label
    session.mount("http://", backend)
    return session


def post(session):
    req = requests.Request("POST", "http://backend.invalid/v1/x", data=b"batch")
    return session.send(session.prepare_request(req))


class TestSharedRefresh:
    def test_a_refresh_by_one_session_reaches_the_others_before_they_send(self):
        helper, runner = fake_helper()
        backend = FakeBackend("t1")
        traces, metrics, logs = (session_on(helper, backend, s) for s in ("t", "m", "l"))
        assert post(traces).status_code == 200

        backend.accepted = "t2"
        runner.extra = {}  # the rotated set no longer carries x-old
        assert post(traces).status_code == 200  # refused, re-minted, retried
        assert post(metrics).status_code == 200
        assert post(logs).status_code == 200

        assert backend.seen[1:] == [
            ("t", "Bearer t1", "1"),
            ("t", "Bearer t2", None),
            ("m", "Bearer t2", None),  # no 401: picked the new set up first
            ("l", "Bearer t2", None),
        ]
        assert runner.calls == 2
        assert (traces.refreshes, traces.adopted) == (1, 0)
        assert (metrics.refreshes, metrics.adopted, metrics.retries) == (0, 1, 0)
        assert "x-old" not in metrics.headers  # the session keeps the new set
        # Steady state: nothing more to adopt.
        assert post(metrics).status_code == 200
        assert metrics.adopted == 1 and runner.calls == 2
        for s in (traces, metrics, logs):
            s.close()

    def test_two_sessions_refused_together_share_one_run(self):

        helper, runner = fake_helper()
        backend = FakeBackend("t2")  # rotated before either sends
        backend.refusal_barrier = threading.Barrier(2)
        a, b = session_on(helper, backend, "a"), session_on(helper, backend, "b")
        results = {}

        def send(name, session) -> None:
            results[name] = post(session).status_code

        threads = [threading.Thread(target=send, args=(n, s)) for n, s in (("a", a), ("b", b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        assert results == {"a": 200, "b": 200}
        assert runner.calls == 2  # the first mint + one refresh for both
        assert a.refreshes == b.refreshes == a.retries == b.retries == 1
        refused = [s for s in backend.seen if s[1] == "Bearer t1"]
        assert sorted(s[0] for s in refused) == ["a", "b"]
        a.close()
        b.close()

    def test_debug_log_says_who_ran_the_helper(self, monkeypatch):

        lines = []
        monkeypatch.setattr(hh, "debug_log", lines.append)
        helper, runner = fake_helper()
        backend = FakeBackend("t2")
        backend.refusal_barrier = threading.Barrier(2)
        a, b = session_on(helper, backend, "a"), session_on(helper, backend, "b")
        threads = [threading.Thread(target=post, args=(s,)) for s in (a, b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        refreshed = [line for line in lines if "401 from the backend" in line]
        assert len(refreshed) == 2
        assert sum("; re-ran the helper" in line for line in refreshed) == 1
        assert sum("another exporter already re-ran the helper" in line for line in refreshed) == 1
        assert all("t2" not in line for line in lines)  # values stay masked
        a.close()
        b.close()


class TestRedaction:
    def test_describe_masks_credential_names_and_auth_values_only(self):
        text = describe_headers(
            {
                "Authorization": "Basic dXNlcjpwYXNzd29yZA==",
                "x-api-key": "k-123",
                "x-custom": "Bearer looks-like-a-token",
                "stream-name": "default",
            }
        )
        assert text == (
            "Authorization=••••••, stream-name=default, x-api-key=••••••, x-custom=••••••"
        )

    def test_describe_redacts_prefixed_keys_in_plain_values(self):
        # Not a credential header name, not a bearer/basic string: the text
        # redactor still catches a provider key inside the value.
        text = describe_headers({"x-note": "sk-ant-abcdefghijklmnopqrstuvwxyz"})
        assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in text
        assert text.startswith("x-note=")

    def test_debug_log_never_carries_the_minted_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        monkeypatch.setattr(du, "_DEBUG_ENABLED", True)
        du.close_debug_log()
        try:
            HeadersHelper(
                script(tmp_path, PRINTS_KV), label="oo", timeout_ms=SLOW_START_MS
            ).headers()
            du.close_debug_log()
            log = (tmp_path / "home" / "plugins" / "hermes_otel" / "debug.log").read_text()
        finally:
            du.close_debug_log()
        line = next(l for l in log.splitlines() if "headers_helper oo" in l)
        assert "run 1" in line
        assert "Authorization=••••••" in line and "x-tenant=acme" in line
        assert "first-token-value-0001" not in log


# ── the config → resolve → target path ──────────────────────────────────


class TestConfig:
    def test_coerce_accepts_the_fields(self):
        (bc,) = coerce_backends(
            [
                {
                    "type": "otlp",
                    "endpoint": "http://127.0.0.1:4318/v1/traces",
                    "headers_helper": "gcloud auth print-access-token --format=x",
                    "headers_helper_timeout_ms": "2500",
                }
            ]
        )
        assert bc.headers_helper == "gcloud auth print-access-token --format=x"
        assert bc.headers_helper_timeout_ms == 2500

    def test_env_refs_expand_in_the_command(self, monkeypatch):
        monkeypatch.setenv("HO_TEST_HELPER", "/opt/bin/mint")
        (bc,) = coerce_backends(
            [{"type": "otlp", "endpoint": "http://x", "headers_helper": "${HO_TEST_HELPER} --json"}]
        )
        assert bc.headers_helper == "/opt/bin/mint --json"

    @pytest.mark.parametrize("bad", ["soon", "-5", 0, True])
    def test_bad_timeout_warns_and_keeps_the_default(self, bad, caplog):
        with caplog.at_level("WARNING"):
            (bc,) = coerce_backends(
                [{"type": "otlp", "endpoint": "http://x", "headers_helper_timeout_ms": bad}]
            )
        assert bc.headers_helper_timeout_ms is None
        assert "headers_helper_timeout_ms" in caplog.text

    def test_defaults_are_unset(self):
        bc = BackendConfig(type="otlp", endpoint="http://x")
        assert bc.headers_helper is None and bc.headers_helper_timeout_ms is None
        assert headers_helper_for(bc) is None
        assert headers_helper_for(BackendConfig(type="otlp", headers_helper="  ")) is None

    def test_headers_helper_for_carries_timeout_and_label(self):
        bc = BackendConfig(
            type="otlp", name="vertex", headers_helper="mint --x", headers_helper_timeout_ms=900
        )
        helper = headers_helper_for(bc)
        assert helper.argv == ["mint", "--x"] and helper.timeout_ms == 900
        assert helper.label == "vertex"


class TestResolveAndTarget:
    def test_resolve_carries_the_helper_without_running_it(self):
        rb = resolve(
            BackendConfig(type="otlp", endpoint="http://x/v1/traces", headers_helper="no-such-cmd")
        )
        assert isinstance(rb.headers_helper, HeadersHelper)
        assert rb.headers_helper.runs == 0

    def test_resolve_refuses_an_unbalanced_command(self):
        with pytest.raises(ValueError, match="quotation"):
            resolve(BackendConfig(type="otlp", endpoint="http://x", headers_helper="a 'b"))

    def test_without_a_helper_nothing_changes(self):
        rb = resolve(BackendConfig(type="otlp", endpoint="http://x/v1/traces"))
        assert rb.headers_helper is None
        target = rb.target("traces", {"x-g": "1"})
        assert target.headers == {"x-g": "1"} and target.headers_helper is None

    def test_target_merges_minted_over_static_for_every_signal(self, tmp_path):
        helper = HeadersHelper(script(tmp_path, PRINTS_KV), label="t", timeout_ms=SLOW_START_MS)
        rb = _ResolvedBackend(
            type="otlp",
            endpoint="http://x/v1/traces",
            headers={"Authorization": "Basic static", "x-static": "s"},
            supports_logs=True,
            headers_helper=helper,
        )
        for signal in ("traces", "metrics", "logs"):
            target = rb.target(signal, {"x-g": "g", "x-tenant": "global"})
            assert target.headers == {
                "x-g": "g",
                "x-static": "s",
                "Authorization": "Bearer first-token-value-0001",  # the helper wins
                "x-tenant": "acme",
            }
            assert target.headers_helper is helper
        assert helper.runs == 1  # one process for the three signals

    def test_openobserve_entry_gets_the_helper_over_its_basic_auth(self, tmp_path):
        bc = BackendConfig(
            type="openobserve",
            endpoint="http://127.0.0.1:5080/api/default/v1/traces",
            user="root@example.com",
            password="initial",
            headers_helper=script(tmp_path, 'print("Authorization=Basic bmV3OnBhc3M=")\n'),
            headers_helper_timeout_ms=SLOW_START_MS,
        )
        rb = resolve(bc)
        target = rb.target("logs")
        assert target.headers["Authorization"] == "Basic bmV3OnBhc3M="
        assert target.headers["stream-name"] == "default"

    def test_failing_helper_skips_the_entry_at_pipeline_init(self, tmp_path, caplog):
        from hermes_otel.pipeline import build_pipeline
        from hermes_otel.plugin_config import HermesOtelConfig

        rb = resolve(
            BackendConfig(
                type="otlp",
                endpoint="http://127.0.0.1:4318/v1/traces",
                headers_helper=script(tmp_path, "import sys\nsys.exit(2)\n"),
                headers_helper_timeout_ms=SLOW_START_MS,
            )
        )
        cfg = HermesOtelConfig(dashboard_live=False, probe_backends=False)
        with caplog.at_level("ERROR"):
            pipeline = build_pipeline(cfg, [rb], "default")
        assert pipeline is None  # the only backend was skipped
        assert "traces init failed" in caplog.text and "exited 2" in caplog.text


# Each type that requires a static credential, the header that credential
# becomes, and the non-credential fields it needs beyond ``endpoint``.
_REQUIRED_AUTH = {
    "agenta": ("Authorization", {}),
    "clickstack": ("authorization", {}),
    "honeycomb": ("x-honeycomb-team", {}),
    "laminar": ("Authorization", {}),
    "langfuse": ("Authorization", {}),
    "langsmith": ("x-api-key", {}),
    "langwatch": ("Authorization", {}),
    "latitude": ("Authorization", {"project": "proj"}),
    "openobserve": ("Authorization", {}),
    "parseable": ("X-API-Key", {}),
    "uptrace": ("uptrace-dsn", {}),
    "weave": ("wandb-api-key", {}),
}


class TestHelperOnlyEntries:
    """A ``headers_helper`` stands in for the static credential (#583)."""

    @pytest.fixture(autouse=True)
    def _no_backend_env(self, monkeypatch):
        from _helpers import clear_backend_env

        clear_backend_env(monkeypatch)

    @staticmethod
    def _entry(backend_type, **extra):
        header, fields = _REQUIRED_AUTH[backend_type]
        return BackendConfig(
            type=backend_type, endpoint="http://127.0.0.1:4318/v1/traces", **fields, **extra
        )

    @pytest.mark.parametrize("backend_type", sorted(_REQUIRED_AUTH))
    def test_helper_alone_resolves_without_the_static_header(self, backend_type):
        rb = resolve(self._entry(backend_type, headers_helper="mint-headers"))
        header = _REQUIRED_AUTH[backend_type][0]
        for headers in (rb.headers, rb.metrics_headers, rb.logs_headers):
            assert header not in (headers or {})
        assert rb.headers_helper is not None and rb.headers_helper.runs == 0

    @pytest.mark.parametrize("backend_type", sorted(_REQUIRED_AUTH))
    def test_neither_credential_nor_helper_is_still_an_error(self, backend_type):
        with pytest.raises(ValueError, match="requires"):
            resolve(self._entry(backend_type))

    def test_blank_helper_does_not_count(self):
        with pytest.raises(ValueError, match="openobserve requires user and password"):
            resolve(self._entry("openobserve", headers_helper="  "))

    def test_helper_only_openobserve_sends_what_the_helper_mints(self, tmp_path):
        rb = resolve(
            self._entry(
                "openobserve",
                headers_helper=script(tmp_path, 'print("Authorization=Basic bWludDpwdw==")\n'),
                headers_helper_timeout_ms=SLOW_START_MS,
            )
        )
        target = rb.target("traces")
        assert target.headers == {"Authorization": "Basic bWludDpwdw==", "stream-name": "default"}

    def test_credentials_still_win_the_static_header_with_a_helper(self):
        rb = resolve(self._entry("honeycomb", api_key="k", headers_helper="mint-headers"))
        assert rb.headers["x-honeycomb-team"] == "k"


class TestExporterKwargs:
    def test_no_helper_means_the_two_classic_kwargs(self):
        from hermes_otel.backends import SignalTarget
        from hermes_otel.pipeline import exporter_kwargs

        assert exporter_kwargs(SignalTarget("http://x", {"a": "b"}), "L") == {
            "endpoint": "http://x",
            "headers": {"a": "b"},
        }

    def test_helper_adds_a_refreshing_session(self, tmp_path):
        from hermes_otel.backends import SignalTarget
        from hermes_otel.headers_helper import RefreshingSession
        from hermes_otel.pipeline import exporter_kwargs

        helper = HeadersHelper(script(tmp_path, PRINTS_KV), timeout_ms=SLOW_START_MS)
        kwargs = exporter_kwargs(SignalTarget("http://x", helper.headers(), helper), "L")
        session = kwargs["session"]
        assert isinstance(session, RefreshingSession)
        assert helper.runs == 1  # the session reads the cache, no second run
        session.close()

    def test_every_signal_is_built_through_it(self, tmp_path):
        """Spans, metrics and logs: each exporter gets the session (#483)."""
        from hermes_otel import log_handler, pipeline
        from hermes_otel.plugin_config import HermesOtelConfig

        helper_cmd = script(tmp_path, PRINTS_KV)
        rb = resolve(
            BackendConfig(
                type="otlp",
                endpoint="http://127.0.0.1:4318/v1/traces",
                headers_helper=helper_cmd,
                headers_helper_timeout_ms=SLOW_START_MS,
                metrics=True,
                logs=True,
            )
        )
        cfg = HermesOtelConfig(dashboard_live=False, probe_backends=False, capture_logs=True)
        built = pipeline.build_pipeline(cfg, [rb], "default")
        assert built is not None
        seen = []
        for mock in (pipeline.OTLPSpanExporter, pipeline.OTLPMetricExporter):
            kwargs = mock.call_args.kwargs
            assert kwargs["headers"]["Authorization"] == "Bearer first-token-value-0001"
            seen.append(kwargs["session"])
        log_kwargs = log_handler.OTLPLogExporter.call_args.kwargs
        assert log_kwargs["headers"]["x-tenant"] == "acme"
        seen.append(log_kwargs["session"])
        assert len({id(s) for s in seen}) == 3  # one session per exporter
        assert all(s._helper is rb.headers_helper for s in seen)  # one helper for all
        assert rb.headers_helper.runs == 1
        built.shutdown()
        for s in seen:
            s.close()


class TestSettingsCard:
    def test_card_shows_the_command_and_timeout_never_minted_values(self, tmp_path, monkeypatch):
        import yaml

        from hermes_otel import plugin_config as pc
        from hermes_otel import settings_report

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(pc, "DURABLE_CONFIG_PATH", home / "hermes_otel.yaml")
        monkeypatch.setattr(
            pc, "DEFAULT_CONFIG_PATH", home / "plugins" / "hermes_otel" / "config.yaml"
        )
        helper_cmd = script(tmp_path, PRINTS_KV)
        (home / "hermes_otel.yaml").write_text(
            yaml.safe_dump(
                {
                    "backends": [
                        {
                            "type": "otlp",
                            "endpoint": "http://127.0.0.1:4318/v1/traces",
                            "headers_helper": helper_cmd,
                            "headers_helper_timeout_ms": 1500,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        report = settings_report.build_settings_report()
        card = next(f["value"] for f in report["fields"] if f["key"] == "backends")[0]
        assert card["fields"]["headers_helper"] == helper_cmd
        assert card["fields"]["headers_helper_timeout_ms"] == 1500
        assert card["headers"] is None  # the minted set is never part of the card
        assert "first-token-value-0001" not in str(report)
        # The effective YAML round-trips the entry.
        assert "headers_helper:" in report["effective_yaml"]
        assert "headers_helper_timeout_ms: 1500" in report["effective_yaml"]


def test_module_has_no_shell_and_binds_nothing():
    """What the plugin scanner must keep seeing: argv list, ``shell=False``."""
    src = Path(hh.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in src
    assert "os.system" not in src
    assert os.path.basename(hh.__file__) == "headers_helper.py"
