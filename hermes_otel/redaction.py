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
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Callable, MutableMapping, Optional

_MASK = "***"
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


def redact_attributes(attrs: Optional[MutableMapping[str, Any]]) -> None:
    """Redact every string value in *attrs* in place; other value types are left alone."""
    if not attrs:
        return
    try:
        for key in list(attrs.keys()):
            value = attrs[key]
            if isinstance(value, str) and value:
                redacted = redact_text(value)
                if redacted != value:
                    attrs[key] = redacted
    except Exception:
        pass


def redaction_source() -> str:
    """``"hermes"`` when Hermes's redactor is in use, ``"builtin"`` for the fallback, ``"off"`` when disabled."""
    if _resolve_hermes_redactor() is not None:
        return "hermes"
    return "builtin" if _fallback_enabled() else "off"
