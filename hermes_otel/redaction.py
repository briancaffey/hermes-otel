"""Secret redaction for telemetry that leaves the machine.

Hermes formats every file log through ``agent.redact.RedactingFormatter``, so
a credential that appears in a log line never reaches ``agent.log``. The
plugin's exported records and live-store rows must be at least as careful,
so :func:`redact_text` applies Hermes's own redactor when the plugin runs
inside Hermes (lazy import; it honours ``security.redact_secrets`` /
``HERMES_REDACT_SECRETS``, including per-profile overrides) and falls back to
a small built-in pattern set otherwise (tests, a copy of the plugin loaded
outside Hermes). The fallback covers the shapes that matter most for an
agent's logs: provider key prefixes, bearer and basic auth, ``Authorization``
and ``x-api-key`` style headers, ``key=value`` credential assignments and URL
userinfo. It is a floor, not a replacement: Hermes's redactor is far more
thorough.

Masking mirrors Hermes: a short token is replaced entirely, a long one keeps
its first six and last four characters so a leak can still be identified.

The second half of the module is the name-based engine the settings report
uses: a value is a secret because of the key, header or env var that holds it
(``is_secret_name``, ``is_header_secret_name``, ``mask_header_pairs``), and is
shown as :data:`REPORT_MASK` whole. Both engines live here so there is one
place that decides what counts as a credential.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Callable, MutableMapping, Optional

_MASK = "***"
# What the settings report shows in place of a secret value (whole, never partial).
REPORT_MASK = "••••••"
_SHORT = 18  # tokens shorter than this are masked whole, like Hermes

_hermes_lock = threading.Lock()
_hermes_resolved = False
_hermes_redactor: Optional[Callable[[str], str]] = None


def _resolve_hermes_redactor() -> Optional[Callable[[str], str]]:
    """``agent.redact.redact_sensitive_text`` when Hermes is importable, else None (cached)."""
    global _hermes_resolved, _hermes_redactor
    if _hermes_resolved:
        return _hermes_redactor
    with _hermes_lock:
        if _hermes_resolved:
            return _hermes_redactor
        try:
            from agent.redact import redact_sensitive_text  # type: ignore[import-not-found]

            _hermes_redactor = redact_sensitive_text
        except Exception:
            _hermes_redactor = None
        _hermes_resolved = True
    return _hermes_redactor


def _reset_cache() -> None:
    """Forget the resolved redactor (tests only)."""
    global _hermes_resolved, _hermes_redactor
    with _hermes_lock:
        _hermes_resolved = False
        _hermes_redactor = None


def _fallback_enabled() -> bool:
    """The built-in set follows the same switch Hermes reads at launch."""
    return os.getenv("HERMES_REDACT_SECRETS", "true").strip().lower() in {"1", "true", "yes", "on"}


def mask(token: str) -> str:
    """Hermes-style mask: short tokens vanish, long ones keep a head and tail."""
    if len(token) < _SHORT:
        return _MASK
    return f"{token[:6]}{_MASK}{token[-4:]}"


# Credential shapes with a fixed prefix. Every pattern starts with a literal so the
# scan is cheap and never catastrophic.
_PREFIXED = re.compile(
    r"(?<![A-Za-z0-9_-])("
    r"sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|sk-(?:proj-|or-v1-|lf-)?[A-Za-z0-9_\-]{16,}"
    r"|pk-lf-[A-Za-z0-9_\-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_\-]{10,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{30,}"
    r"|hcaik_[A-Za-z0-9]{10,}"
    r"|lsv2_[a-z]{2}_[A-Za-z0-9_]{20,}"
    r")(?![A-Za-z0-9_-])"
)
# ``Bearer <token>`` / ``Basic <b64>``: keep the scheme, mask the credential.
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=\-]{8,})")
# ``Authorization: …``, ``x-api-key: …``, ``api_key=…``, ``"password": "…"`` and friends.
_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|x-api-key|x-honeycomb-team|api[_\-]?key|secret[_\-]?key|"
    r"access[_\-]?token|refresh[_\-]?token|auth[_\-]?token|token|secret|password|passwd)"
    r"(\"?'?\s*[:=]\s*[\"']?)(?!(?:bearer|basic)\b)([^\s\"',;&]{4,})"
)
# ``scheme://user:password@host`` — mask the password only.
_URL_USERINFO = re.compile(r"(://[^/\s:@]+:)([^@/\s]+)(@)")


def _fallback(text: str) -> str:
    if not _fallback_enabled():
        return text
    out = _PREFIXED.sub(lambda m: mask(m.group(1)), text)
    out = _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} {mask(m.group(2))}", out)
    out = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{mask(m.group(3))}", out)
    out = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{_MASK}{m.group(3)}", out)
    return out


def redact_text(text: Any) -> Any:
    """Redact secrets from *text*; non-strings and empty strings pass through unchanged.

    Never raises: a redactor failure returns the original text, the same
    choice Hermes makes for its own log formatter.
    """
    if not isinstance(text, str) or not text:
        return text
    redactor = _resolve_hermes_redactor()
    try:
        if redactor is not None:
            return redactor(text)
        return _fallback(text)
    except Exception:
        return text


# JSON keys whose value is a secret because of the key (``{"password": "…"}``).
# Only a candidate filter: the redactor still decides, with the key in view.
_JSON_SECRET_KEY = re.compile(
    r"(key|secret|token|pass|pwd|\bpw\b|auth|credential|cookie|dsn|signature|session)", re.I
)
_NOT_JSON = object()


def _redact_json_leaf(key: Optional[str], value: str) -> str:
    """One string of a parsed JSON document, redacted.

    A value under a credential-shaped key is redacted as a one-entry object so
    key-based rules see the key; if that object no longer parses, or the key is
    an ordinary one, the bare value is redacted.
    """
    if key is not None and _JSON_SECRET_KEY.search(key):
        try:
            # A one-entry object stays one entry; a quoted value stays a string.
            (out,) = json.loads(redact_text(json.dumps({key: value}, ensure_ascii=False))).values()
            return out
        except ValueError:
            pass
    return redact_text(value)


def _redact_json_tree(node: Any, key: Optional[str], changed: list) -> Any:
    if isinstance(node, str):
        out = _redact_json_leaf(key, node)
        if out != node:
            changed[0] = True
        return out
    if isinstance(node, list):
        return [_redact_json_tree(item, key, changed) for item in node]
    if isinstance(node, dict):
        return {k: _redact_json_tree(v, k, changed) for k, v in node.items()}
    return node


def redact_content(text: Any) -> Any:
    """:func:`redact_text` that keeps a JSON document valid JSON.

    Content attributes are often JSON (``input.value`` with
    ``application/json``, ``gen_ai.*.messages``, a tool result). Redacting the
    serialised text can break it: Hermes's ``KEY=\\S+`` rule runs through an
    escaped newline and the closing quote. A JSON object or array is therefore
    parsed and redacted string by string, then serialised again, and only when
    a string changed, so a document with nothing to mask comes back as the
    same text. Values under credential-shaped keys keep their key in view.
    Anything else is redacted as text. Never raises.
    """
    if not isinstance(text, str) or not text:
        return text
    if text.lstrip()[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = _NOT_JSON
        if parsed is not _NOT_JSON:
            changed = [False]
            out = _redact_json_tree(parsed, None, changed)
            return json.dumps(out, ensure_ascii=False) if changed[0] else text
    return redact_text(text)


def redact_value(value: Any) -> Any:
    """:func:`redact_content` for an OTel attribute value: a string or a sequence of them."""
    if isinstance(value, str):
        return redact_content(value)
    if isinstance(value, (list, tuple)) and any(isinstance(v, str) for v in value):
        out = [redact_content(v) if isinstance(v, str) else v for v in value]
        if out != list(value):
            return tuple(out) if isinstance(value, tuple) else out
    return value


def redact_attributes(attrs: Optional[MutableMapping[str, Any]]) -> None:
    """Redact every string value in *attrs* in place; other value types are left alone."""
    if not attrs:
        return
    try:
        for key in list(attrs.keys()):
            value = attrs[key]
            if isinstance(value, str) and value:
                redacted = redact_content(value)
                if redacted != value:
                    attrs[key] = redacted
    except Exception:
        pass


def redaction_source() -> str:
    """``"hermes"`` when Hermes's redactor is in use, ``"builtin"`` for the fallback, ``"off"`` when disabled."""
    if _resolve_hermes_redactor() is not None:
        return "hermes"
    return "builtin" if _fallback_enabled() else "off"


# ── Name-based masking (the settings report) ──────────────────────────────

_SECRET_NAME = re.compile(
    r"(key|secret|token|password|passwd|dsn|authorization|credential|cookie)", re.I
)
# HTTP header names that carry a credential: the generic words above plus the
# vendor spellings (``x-honeycomb-team``, ``x-api-key``, ``api-key``) and any
# ``x-…-auth``; a header is almost always an auth header in this file.
_HEADER_SECRET_NAME = re.compile(
    r"(key|secret|token|password|passwd|dsn|authorization|credential|cookie|auth|signature"
    r"|^x-[a-z0-9]+-team$)",
    re.I,
)
# Env vars whose value is a ``k=v,k2=v2`` header list (OTel SDK spelling).
_HEADER_LIST_NAME = re.compile(r"_HEADERS$", re.I)


def is_secret_name(name: str) -> bool:
    """Whether a key, field or env var name looks like it holds a credential."""
    return bool(_SECRET_NAME.search(name or ""))


def is_header_secret_name(name: str) -> bool:
    """Whether an HTTP *header* name carries a credential (wider than :func:`is_secret_name`)."""
    return bool(_HEADER_SECRET_NAME.search(name or ""))


def is_secret_value(value: Any) -> bool:
    """Bearer/Basic auth strings are secrets whatever their key is called."""
    return isinstance(value, str) and value.strip().lower().startswith(("bearer ", "basic "))


def mask_header_pairs(raw: str) -> str:
    """Mask the secret values of a ``k=v,k2=v2`` header list, pair by pair.

    ``OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer abc,x-team=blue`` →
    ``Authorization=••••••,x-team=blue``. A pair whose header name is not an
    auth name but whose value is a bearer/basic string is masked too; a pair
    without ``=`` is left as written.
    """
    out = []
    for pair in raw.split(","):
        k, sep, v = pair.partition("=")
        if sep and v.strip() and (is_header_secret_name(k.strip()) or is_secret_value(v)):
            out.append(f"{k}={REPORT_MASK}")
        else:
            out.append(pair)
    return ",".join(out)


def mask_env_value(name: str, raw: str) -> str:
    """The value of one environment variable as the report may show it."""
    if _HEADER_LIST_NAME.search(name or ""):
        return mask_header_pairs(raw)
    if is_secret_name(name) or is_secret_value(raw):
        return REPORT_MASK
    return raw
