"""``HERMES_OTEL_RECORD_HTTP=<dir>``: the dev-only switch that writes every
exchange ``base._execute`` makes to ``<dir>/exchanges.jsonl`` with its
credentials masked, the raw material for an adapter golden (#408)."""

from __future__ import annotations

import io
import json
from urllib import error as _urlerror
from urllib import request as _urlrequest

import pytest

from hermes_otel.dashboard.backends import base
from hermes_otel.dashboard.backends.base import BackendError


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _entries(path):
    return [json.loads(line) for line in (path / "exchanges.jsonl").read_text().splitlines()]


def test_off_by_default_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_OTEL_RECORD_HTTP", raising=False)
    monkeypatch.setattr(_urlrequest, "urlopen", lambda req, timeout=0: _Resp(b'{"ok": 1}'))
    assert base.http_get_json("http://x/api?token=secret") == {"ok": 1}
    assert not (tmp_path / "exchanges.jsonl").exists()


def test_records_request_and_answer_with_credentials_masked(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_OTEL_RECORD_HTTP", str(tmp_path / "rec"))
    seen = {}

    def fake(req, timeout=0):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(b'{"data": [1, 2]}')

    monkeypatch.setattr(_urlrequest, "urlopen", fake)
    out = base.http_post_json(
        "http://localhost:3000/api/public/traces?limit=1&api_key=key-test&page=2",
        {"q": 1},
        headers={"Authorization": "Basic cGs6c2s=", "X-Api-Key": "k", "Accept": "*/*"},
    )
    assert out == {"data": [1, 2]} and seen["auth"] == "Basic cGs6c2s="  # the wire is untouched
    (entry,) = _entries(tmp_path / "rec")
    assert entry["method"] == "POST" and entry["status"] == 200
    assert entry["url"] == "http://localhost:3000/api/public/traces?limit=1&api_key=***&page=2"
    assert entry["headers"]["Authorization"] == "***" and entry["headers"]["X-api-key"] == "***"
    assert entry["headers"]["Accept"] == "*/*"
    assert json.loads(entry["request"]) == {"q": 1} and entry["response"] == {"data": [1, 2]}


def test_records_an_http_error_with_its_status_and_body(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_OTEL_RECORD_HTTP", str(tmp_path))

    def bad(req, timeout=0):
        raise _urlerror.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"no such trace"))

    monkeypatch.setattr(_urlrequest, "urlopen", bad)
    with pytest.raises(BackendError) as ei:
        base.http_get_json("http://localhost:16686/api/traces/abc")
    assert ei.value.kind == "not_found"
    (entry,) = _entries(tmp_path)
    assert entry["status"] == 404 and entry["response"] == "no such trace"


def test_a_recording_failure_never_reaches_the_caller(monkeypatch, tmp_path):
    target = tmp_path / "file-not-dir"
    target.write_text("x")
    monkeypatch.setenv("HERMES_OTEL_RECORD_HTTP", str(target))
    monkeypatch.setattr(_urlrequest, "urlopen", lambda req, timeout=0: _Resp(b"[]"))
    assert base.http_get_json("http://x/") == []
