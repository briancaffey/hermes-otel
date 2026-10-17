"""Headers minted by an executable, refreshed when the backend says 401/403 (#483).

Static headers (``headers:``, the ``*_env`` credential fields) cannot follow a
token that expires: a Google Cloud access token, an OIDC token from a vault, an
API key a script rotates. A backend entry's ``headers_helper:`` names a command
instead. :class:`HeadersHelper` runs it (``argv`` split with :mod:`shlex`, no
shell, the plugin's own environment, ``headers_helper_timeout_ms`` to answer)
and parses its stdout as a JSON object or ``Name=value`` lines; the result is
merged over the entry's static headers on every signal's exporter and cached.

:class:`RefreshingSession` is the ``requests.Session`` the pipeline hands the
OTLP/HTTP exporters when an entry has a helper. The exporters post through
``self._session`` on every release from the 1.35 floor to today and treat a 401
or 403 as final (``_is_retryable`` is 408 and 5xx), so the session is the one
place that sees the status before the batch is dropped: on a 401/403 it runs
the helper once more, puts the new headers on the session and on the very same
prepared request, and sends that request once more. A second 401/403, or a
helper that fails, is handed back to the exporter, which logs it and drops the
batch as it does any failed export.

Debug lines name the headers the helper printed and never their values: a
header whose name says credential (``Authorization``, ``x-api-key``) or whose
value is a bearer/basic string is masked whole; anything else goes through
:func:`hermes_otel.redaction.redact_text`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional

from .debug_utils import debug_log
from .redaction import REPORT_MASK, is_header_secret_name, is_secret_value, redact_text

#: Milliseconds the helper may take before the run counts as failed.
DEFAULT_TIMEOUT_MS = 5000
#: Statuses that mean "the credential was refused": re-mint once, retry once.
AUTH_STATUSES = (401, 403)
_STDERR_PREVIEW = 300


class HeadersHelperError(ValueError):
    """The helper could not be run or its output is not a header set.

    A ``ValueError`` so a helper that fails at startup skips its backend entry
    with a message, the way any other invalid entry does."""


def parse_headers(text: str) -> Dict[str, str]:
    """The header set a helper printed: a JSON object, or ``Name=value`` lines.

    Blank lines and ``#`` comments are skipped. A JSON object's values are
    taken as strings (a number is formatted, nothing else is accepted). Empty
    output (``{}`` and comment-only included), a JSON value that is not an
    object, or a line without ``=`` is a
    :class:`HeadersHelperError`: a helper that prints nothing is broken, and
    guessing would send the backend a request it cannot authenticate.
    """
    body = (text or "").strip()
    if not body:
        raise HeadersHelperError("the helper printed no headers")
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except ValueError as e:
            raise HeadersHelperError(f"the helper printed invalid JSON: {e}") from None
        if not isinstance(data, dict):
            raise HeadersHelperError(
                "the helper's JSON must be an object of header names to values"
            )
        out: Dict[str, str] = {}
        for name, value in data.items():
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise HeadersHelperError(f"header {name!r}: value must be a string")
            out[str(name).strip()] = str(value).strip()
        return _checked(out)
    out = {}
    for lineno, raw in enumerate(body.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not name.strip():
            raise HeadersHelperError(f"line {lineno} is not Name=value")
        out[name.strip()] = value.strip()
    return _checked(out)


def _checked(headers: Dict[str, str]) -> Dict[str, str]:
    if not headers:
        raise HeadersHelperError("the helper printed no headers")
    for name in headers:
        if not name or any(c.isspace() for c in name) or ":" in name:
            raise HeadersHelperError(f"{name!r} is not a header name")
    return headers


def describe_headers(headers: Dict[str, str]) -> str:
    """``Authorization=••••••, stream-name=default``: the set, values redacted."""
    parts = []
    for name, value in sorted(headers.items()):
        if is_header_secret_name(name) or is_secret_value(value):
            parts.append(f"{name}={REPORT_MASK}")
        else:
            parts.append(f"{name}={redact_text(value)}")
    return ", ".join(parts)


class HeadersHelper:
    """One backend entry's ``headers_helper``: the command, its cached output,
    and :meth:`refresh` to run it again.

    ``command`` is split with :mod:`shlex` (POSIX rules, so a quoted argument
    stays one argument) and run with ``shell=False``; an empty or unbalanced
    command is refused here, at config time. ``runner`` is ``subprocess.run``
    unless a test substitutes one.
    """

    def __init__(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        *,
        label: str = "",
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        try:
            argv = shlex.split(command or "")
        except ValueError as e:
            raise HeadersHelperError(f"headers_helper {command!r}: {e}") from None
        if not argv:
            raise HeadersHelperError("headers_helper is empty")
        self.command = command
        self.argv = argv
        self.timeout_ms = int(timeout_ms) if timeout_ms else DEFAULT_TIMEOUT_MS
        self.label = label or argv[0]
        self.runs = 0
        self._runner = runner
        self._lock = threading.Lock()
        self._headers: Optional[Dict[str, str]] = None

    def headers(self) -> Dict[str, str]:
        """The current header set; the helper runs on the first call only."""
        with self._lock:
            if self._headers is None:
                self._headers = self._run()
            return dict(self._headers)

    def refresh(self) -> Dict[str, str]:
        """Run the helper again and replace the cached set. Raises
        :class:`HeadersHelperError` and keeps the old set when the run fails."""
        with self._lock:
            self._headers = self._run()
            return dict(self._headers)

    def _run(self) -> Dict[str, str]:
        started = time.monotonic()
        try:
            proc = self._runner(
                self.argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_ms / 1000.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise HeadersHelperError(
                f"headers_helper {self.argv[0]} did not finish within {self.timeout_ms} ms"
            ) from None
        except OSError as e:
            raise HeadersHelperError(
                f"headers_helper {self.argv[0]} could not be run: {e}"
            ) from None
        self.runs += 1
        if proc.returncode != 0:
            stderr = redact_text((proc.stderr or "").strip())[:_STDERR_PREVIEW]
            raise HeadersHelperError(
                f"headers_helper {self.argv[0]} exited {proc.returncode}"
                + (f": {stderr}" if stderr else "")
            )
        try:
            headers = parse_headers(proc.stdout)
        except HeadersHelperError as e:
            raise HeadersHelperError(f"headers_helper {self.argv[0]}: {e}") from None
        elapsed_ms = int((time.monotonic() - started) * 1000)
        debug_log(
            f"headers_helper {self.label}: run {self.runs} ({elapsed_ms} ms) -> "
            f"{describe_headers(headers)}"
        )
        return headers


def headers_helper_for(bc: Any) -> Optional[HeadersHelper]:
    """The entry's :class:`HeadersHelper`, or ``None`` when it names none."""
    command = (getattr(bc, "headers_helper", None) or "").strip()
    if not command:
        return None
    return HeadersHelper(
        command,
        getattr(bc, "headers_helper_timeout_ms", None),
        label=getattr(bc, "name", None) or getattr(bc, "type", None) or "",
    )


try:
    import requests
except ImportError:  # pragma: no cover — requests ships with the OTLP HTTP exporter
    requests = None  # type: ignore[assignment]


if requests is not None:

    class RefreshingSession(requests.Session):
        """The exporter's HTTP session when its headers come from a helper.

        One per exporter (the SDK closes it on shutdown). ``refreshes`` and
        ``retries`` count what happened, for the tests and the debug log.
        """

        def __init__(self, helper: HeadersHelper, label: str = "") -> None:
            super().__init__()
            self._helper = helper
            self._label = label or helper.label
            # The set the exporter was built with (cached: no second run here),
            # so a header the helper stops printing is removed, not kept stale.
            self._minted = helper.headers()
            self.refreshes = 0
            self.retries = 0

        def send(self, request: Any, **kwargs: Any) -> Any:
            response = super().send(request, **kwargs)
            if response.status_code not in AUTH_STATUSES:
                return response
            status = response.status_code
            try:
                minted = self._helper.refresh()
            except HeadersHelperError as e:
                debug_log(f"headers_helper {self._label}: {status} from the backend; {e}; dropping")
                return response
            self.refreshes += 1
            for headers in (self.headers, request.headers):
                for name in self._minted:
                    if name not in minted:
                        headers.pop(name, None)
                headers.update(minted)
            self._minted = minted
            debug_log(
                f"headers_helper {self._label}: {status} from the backend; re-ran the helper "
                f"and retrying the batch once with {describe_headers(minted)}"
            )
            self.retries += 1
            return super().send(request, **kwargs)

else:  # pragma: no cover
    RefreshingSession = None  # type: ignore[assignment, misc]
