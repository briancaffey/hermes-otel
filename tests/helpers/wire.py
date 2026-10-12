"""A scripted HTTP server at the socket boundary: ``urllib.request.urlopen``
replaced by a route table.

``FakeHttp`` (helpers.dashboard) replaces the adapters' ``http_get_json`` /
``http_post_json``; three transports bypass those (ClickHouse SQL, GreptimeDB
SQL and VictoriaLogs LogsQL post raw bodies through ``urlopen`` directly), so
the goldens answer one level lower, where every adapter meets the network. The
error taxonomy in ``base._execute`` then runs for real on a scripted 4xx/5xx.

A rule is a dict: matchers ``method``, ``path`` (exact), ``path_prefix``,
``path_suffix``, ``query_contains``, ``body_contains`` (a string or a list that
must all appear in the decoded request body, URL-decoded too); answer ``json``
(an object) or ``text`` (a raw body), ``status`` (default 200; 400 and up
raises ``HTTPError`` carrying the body), or ``raise``: ``"unreachable"`` /
``"timeout"``. The first matching rule wins.
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional
from urllib import error as _urlerror
from urllib import parse as _urlparse
from urllib import request as _urlrequest


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _decode_body(data: Optional[bytes]) -> Any:
    """The request body as the golden records it: JSON when it parses, a
    form as a dict, anything else as text."""
    if not data:
        return None
    text = data.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        pass
    if "=" in text and "\n" not in text and " " not in text.split("=", 1)[0]:
        form = _urlparse.parse_qs(text, keep_blank_values=True)
        if form:
            return {k: (v[0] if len(v) == 1 else v) for k, v in form.items()}
    return text


class FakeWire:
    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rules: List[Dict[str, Any]] = list(rules or [])
        self.calls: List[Dict[str, Any]] = []

    def install(self, monkeypatch) -> "FakeWire":
        monkeypatch.setattr(_urlrequest, "urlopen", self)
        return self

    @staticmethod
    def _matches(rule: Dict[str, Any], method: str, url: str, body_text: str) -> bool:
        parsed = _urlparse.urlsplit(url)
        if rule.get("method") and rule["method"].upper() != method:
            return False
        if "path" in rule and parsed.path != rule["path"]:
            return False
        if "path_prefix" in rule and not parsed.path.startswith(rule["path_prefix"]):
            return False
        if "path_suffix" in rule and not parsed.path.endswith(rule["path_suffix"]):
            return False
        if "query_contains" in rule and rule["query_contains"] not in _urlparse.unquote_plus(
            parsed.query
        ):
            return False
        needles = rule.get("body_contains")
        if needles:
            if isinstance(needles, str):
                needles = [needles]
            unquoted = _urlparse.unquote_plus(body_text)
            for n in needles:
                if n not in body_text and n not in unquoted:
                    return False
        return True

    def __call__(self, req: Any, timeout: Any = None) -> _Response:
        method = req.get_method()
        url = req.full_url
        data = req.data if isinstance(req.data, bytes) else None
        body_text = data.decode("utf-8", errors="replace") if data else ""
        self.calls.append({"method": method, "url": url, "body": _decode_body(data)})
        for rule in self.rules:
            if self._matches(rule, method, url, body_text):
                break
        else:
            raise AssertionError(f"no route for {method} {url} body={body_text[:200]!r}")
        if rule.get("raise") == "unreachable":
            raise _urlerror.URLError(ConnectionRefusedError(61, "Connection refused"))
        if rule.get("raise") == "timeout":
            raise TimeoutError("timed out")
        if "json" in rule:
            body = json.dumps(rule["json"]).encode("utf-8")
        else:
            body = str(rule.get("text", "")).encode("utf-8")
        status = int(rule.get("status", 200))
        if status >= 400:
            raise _urlerror.HTTPError(url, status, "err", {}, io.BytesIO(body))
        return _Response(body)
