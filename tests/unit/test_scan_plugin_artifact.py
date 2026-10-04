"""Tests for the pinned Hermes scanner download helper."""

from __future__ import annotations

import importlib.util
import urllib.error
from email.message import Message
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "scan_plugin_artifact.py"


def _load_scan_script():
    spec = importlib.util.spec_from_file_location("scan_plugin_artifact", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    status = 200

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def _http_error(code: int, retry_after: str | None = None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://example.invalid/tool.py", code, "rate limited", headers, None
    )


def test_fetch_retries_rate_limited_scanner_download(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    attempts = []
    sleeps = []

    def fake_urlopen(url, timeout):
        attempts.append((url, timeout))
        if len(attempts) == 1:
            raise _http_error(429, retry_after="0")
        return _Response(b"scanner")

    monkeypatch.setattr(scanner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(scanner.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert scanner._fetch("abc123", "tools/plugin_guard.py", tmp_path) == b"scanner"
    assert len(attempts) == 2
    assert sleeps == [0.0]


def test_fetch_uses_ref_keyed_cache_without_network(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    cached = tmp_path / "abc123" / "tools" / "plugin_guard.py"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached scanner")

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("network should not be used when cache is warm")

    monkeypatch.setattr(scanner.urllib.request, "urlopen", fail_urlopen)

    assert scanner._fetch("abc123", "tools/plugin_guard.py", tmp_path) == b"cached scanner"
