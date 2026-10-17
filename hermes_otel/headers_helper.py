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

One helper serves the entry's traces, metrics and logs exporters, each on its
own thread with its own session (#584). Every successful run bumps the helper's
generation; a session remembers the generation it sends with, picks a newer
set up before its next send, and on a 401/403 asks for a set newer than the
one it was refused with: when another exporter already re-ran the helper past
that point, the session takes that set and retries without a second run. So a
rotation costs one helper run per backend, and the signals that send after the
refresh never see the 401 at all.

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
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

from .debug_utils import debug_log
from .redaction import REPORT_MASK, is_header_secret_name, is_secret_value, redact_text

#: Milliseconds the helper may take before the run counts as failed.
DEFAULT_TIMEOUT_MS = 5000
#: Statuses that mean "the credential was refused": re-mint once, retry once.
AUTH_STATUSES = (401, 403)
_STDERR_PREVIEW = 300


class Minted(NamedTuple):
    """One answer of :meth:`HeadersHelper.refresh_after`: the ``generation``
    and ``headers`` now current, and whether this call ``ran`` the helper (or
    took the set another caller had already minted)."""

    generation: int
    headers: Dict[str, str]
    ran: bool


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

    Thread-safe: runs are serialised by a lock, and the current set is one
    ``(generation, headers)`` tuple swapped whole, so :meth:`current` reads it
    without waiting on a run in progress.
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
        # (generation, headers), replaced whole after each successful run;
        # generation 1 is the first set. Never mutated in place.
        self._state: Optional[Tuple[int, Dict[str, str]]] = None

    def snapshot(self) -> Tuple[int, Dict[str, str]]:
        """``(generation, headers)``; the helper runs on the first call only."""
        with self._lock:
            if self._state is None:
                self._state = (1, self._run())
            generation, headers = self._state
            return generation, dict(headers)

    def headers(self) -> Dict[str, str]:
        """The current header set; the helper runs on the first call only."""
        return self.snapshot()[1]

    def current(self) -> Optional[Tuple[int, Dict[str, str]]]:
        """``(generation, headers)`` as last minted, ``None`` before the first
        run. Never runs the helper and never waits on a run in progress: the
        cheap check a session makes before every send."""
        state = self._state
        if state is None:
            return None
        return state[0], dict(state[1])

    def refresh(self) -> Dict[str, str]:
        """Run the helper again and replace the cached set. Raises
        :class:`HeadersHelperError` and keeps the old set when the run fails."""
        with self._lock:
            return self._rerun()[1]

    def refresh_after(self, generation: int) -> Minted:
        """A set newer than ``generation``, the one a request was refused with.

        When another caller already minted past it (the traces exporter
        refreshed while the metrics exporter's request was in flight), that
        set is returned and the helper does not run again; otherwise the
        helper runs. Concurrent callers refused with the same generation
        queue on the lock, so one rotation is one run. Raises
        :class:`HeadersHelperError` and keeps the old set when the run fails.
        """
        with self._lock:
            state = self._state
            if state is not None and state[0] != generation:
                return Minted(state[0], dict(state[1]), False)
            new_generation, headers = self._rerun()
            return Minted(new_generation, headers, True)

    def _rerun(self) -> Tuple[int, Dict[str, str]]:
        # The caller holds the lock.
        headers = self._run()
        generation = (self._state[0] if self._state is not None else 0) + 1
        self._state = (generation, headers)
        return generation, dict(headers)

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

        One per exporter (the SDK closes it on shutdown); the entry's other
        exporters hold sessions on the same helper. ``refreshes`` counts the
        401/403s answered with a newer set, ``retries`` the batches sent again,
        ``adopted`` the sets another exporter minted that this session picked
        up before a send. For the tests and the debug log.
        """

        def __init__(self, helper: HeadersHelper, label: str = "") -> None:
            super().__init__()
            self._helper = helper
            self._label = label or helper.label
            # The set the exporter was built with (cached: no second run here),
            # so a header the helper stops printing is removed, not kept stale;
            # and its generation, to tell a newer set from this one.
            self._generation, self._minted = helper.snapshot()
            self._sync = threading.Lock()
            self.refreshes = 0
            self.retries = 0
            self.adopted = 0

        def _apply(self, generation: int, minted: Dict[str, str], request: Any) -> None:
            """Put ``minted`` on the session and on ``request`` (prepared
            already, so it carries the old set), dropping the names the old
            set had and ``minted`` does not."""
            with self._sync:
                stale = self._minted
                if generation > self._generation:
                    self._generation, self._minted = generation, minted
                for headers in (self.headers, request.headers):
                    for name in stale:
                        if name not in minted:
                            headers.pop(name, None)
                    headers.update(minted)

        def send(self, request: Any, **kwargs: Any) -> Any:
            latest = self._helper.current()
            if latest is not None and latest[0] > self._generation:
                # Another exporter of this backend re-minted after a 401/403:
                # send with that set instead of collecting our own refusal.
                self._apply(latest[0], latest[1], request)
                self.adopted += 1
            sent_with = self._generation
            response = super().send(request, **kwargs)
            if response.status_code not in AUTH_STATUSES:
                return response
            status = response.status_code
            try:
                generation, minted, ran = self._helper.refresh_after(sent_with)
            except HeadersHelperError as e:
                debug_log(f"headers_helper {self._label}: {status} from the backend; {e}; dropping")
                return response
            self.refreshes += 1
            self._apply(generation, minted, request)
            how = "re-ran the helper" if ran else "another exporter already re-ran the helper"
            debug_log(
                f"headers_helper {self._label}: {status} from the backend; {how} "
                f"and retrying the batch once with {describe_headers(minted)}"
            )
            self.retries += 1
            return super().send(request, **kwargs)

else:  # pragma: no cover
    RefreshingSession = None  # type: ignore[assignment, misc]
