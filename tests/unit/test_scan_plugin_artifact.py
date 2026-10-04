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


def test_fetch_does_not_retry_a_real_answer(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    attempts = []

    def fake_urlopen(url, timeout):
        attempts.append(url)
        raise _http_error(404)

    monkeypatch.setattr(scanner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        scanner.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("no sleep"))
    )
    try:
        scanner._fetch("abc123", "tools/plugin_guard.py", tmp_path)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:  # pragma: no cover
        raise AssertionError("a 404 must propagate")
    assert len(attempts) == 1


def test_fetch_gives_up_after_the_attempt_budget(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    attempts = []
    sleeps = []
    monkeypatch.setattr(
        scanner.urllib.request,
        "urlopen",
        lambda url, timeout: attempts.append(url) or (_ for _ in ()).throw(_http_error(503)),
    )
    monkeypatch.setattr(scanner.time, "sleep", lambda s: sleeps.append(s))
    try:
        scanner._fetch("abc123", "tools/plugin_guard.py", None)
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
    else:  # pragma: no cover
        raise AssertionError("exhaustion must re-raise the last error")
    assert len(attempts) == scanner.FETCH_ATTEMPTS
    assert sleeps == [2.0, 4.0, 8.0, 16.0]  # backoff between the five attempts, capped at 32 s


def test_fetch_retries_connection_errors_too(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    attempts = []
    sleeps = []
    failures = [urllib.error.URLError("connection reset"), TimeoutError("timed out")]

    def fake_urlopen(url, timeout):
        attempts.append(url)
        if failures:
            raise failures.pop(0)
        return _Response(b"scanner")

    monkeypatch.setattr(scanner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: sleeps.append(s))
    assert scanner._fetch("abc123", "tools/plugin_guard.py", None) == b"scanner"
    assert len(attempts) == 3 and sleeps == [2.0, 4.0]


def test_retry_delay_survives_missing_headers_and_http_dates():
    scanner = _load_scan_script()
    no_headers = urllib.error.HTTPError("https://example.invalid", 429, "m", None, None)
    assert scanner._retry_delay(no_headers, 1) == 2.0
    assert (
        scanner._retry_delay(_http_error(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"), 3)
        == 8.0
    )
    assert scanner._retry_delay(_http_error(429, retry_after="7"), 1) == 7.0
    assert scanner._retry_delay(_http_error(503), 9) == 32.0


def test_fetch_never_writes_the_cache_itself(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    monkeypatch.setattr(scanner.urllib.request, "urlopen", lambda url, timeout: _Response(b"fresh"))
    assert scanner._fetch("abc123", "tools/plugin_guard.py", tmp_path) == b"fresh"
    assert not (tmp_path / "abc123" / "tools" / "plugin_guard.py").exists()


def _lock_for(scanner, tmp_path, blobs):
    import hashlib
    import json

    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "ref": "abc123",
                "sha256": {rel: hashlib.sha256(b).hexdigest() for rel, b in blobs.items()},
            }
        )
    )
    return lock


def _fake_scanner_blobs(scanner):
    """Importable stand-ins: load_scanner imports two names from tools.plugin_guard."""
    return {
        "tools/skills_guard.py": b"# skills_guard stand-in\n",
        "tools/plugin_guard.py": (
            b"def scan_plugin(*a, **k):\n    return {}\n\n"
            b"def should_allow_plugin_install(*a, **k):\n    return True\n"
        ),
    }


def _forget_fetched_scanner(dest):
    import sys

    for name in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        sys.modules.pop(name, None)
    while str(dest) in sys.path:
        sys.path.remove(str(dest))


def test_load_scanner_caches_only_verified_bytes_and_heals_a_bad_entry(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    blobs = _fake_scanner_blobs(scanner)
    monkeypatch.setattr(scanner, "LOCK_PATH", _lock_for(scanner, tmp_path, blobs))
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv(scanner.SCANNER_CACHE_ENV, str(cache_dir))
    poisoned = cache_dir / "abc123" / "tools" / "skills_guard.py"
    poisoned.parent.mkdir(parents=True)
    poisoned.write_bytes(b"not the scanner")
    fetched = []

    def fake_urlopen(url, timeout):
        rel = url.split("/abc123/", 1)[1]
        fetched.append(rel)
        return _Response(blobs[rel])

    monkeypatch.setattr(scanner.urllib.request, "urlopen", fake_urlopen)
    dest, dest2 = tmp_path / "dest", tmp_path / "dest2"
    try:
        scanner.load_scanner(None, verify=True, update_lock=False, dest=dest)
        # the poisoned entry was evicted, refetched and replaced; the other was fetched once and cached
        assert sorted(fetched) == sorted(scanner.SCANNER_FILES)
        for rel, blob in blobs.items():
            assert (cache_dir / "abc123" / rel).read_bytes() == blob
            assert (dest / "tools" / rel.split("/")[-1]).read_bytes() == blob
        # second run: warm cache, no network
        monkeypatch.setattr(
            scanner.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")),
        )
        _forget_fetched_scanner(dest)
        scanner.load_scanner(None, verify=True, update_lock=False, dest=dest2)
    finally:
        _forget_fetched_scanner(dest)
        _forget_fetched_scanner(dest2)


def test_load_scanner_still_fails_when_upstream_changed(monkeypatch, tmp_path):
    scanner = _load_scan_script()
    blobs = _fake_scanner_blobs(scanner)
    monkeypatch.setattr(scanner, "LOCK_PATH", _lock_for(scanner, tmp_path, blobs))
    monkeypatch.setenv(scanner.SCANNER_CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setattr(
        scanner.urllib.request, "urlopen", lambda url, timeout: _Response(b"changed upstream")
    )
    try:
        scanner.load_scanner(None, verify=True, update_lock=False, dest=tmp_path / "dest")
    except SystemExit as exc:
        assert "checksum mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a mismatching download must fail the scan")
    assert not (tmp_path / "cache").exists()  # nothing unverified was cached
