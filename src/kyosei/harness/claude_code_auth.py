"""Whether this machine's Claude Code login stands, as ``claude auth status`` reports it (KYO-974).

A session whose login has ended still draws its input box and accepts a prompt; the request
is then refused and the transcript gains a reply Claude Code wrote itself (see
:attr:`kyosei.harness.claude_code_transcript.SessionTranscript.latest_login_refusal`). The
transcript can say so only after a request has failed. This module asks Claude Code before
any request is made.

WHAT ``claude auth status --json`` PRINTS (Claude Code 2.1.329, measured). A logged-in
configuration prints ``loggedIn: true``, ``authMethod: "claude.ai"``, ``apiProvider:
"firstParty"``, ``configDirectory`` and ``loginExpiresAt``, and exits 0, in about 0.1 s. A
configuration directory holding no login prints ``loggedIn: false``, ``authMethod: "none"``
and ``loginExpiresAt: null``, and exits 1. ``loginExpiresAt`` is the stored refresh token's
expiry. The release's own warnings say that a sign-in "can end at any time" in the three days
before that time and that renewing a token does not move it; only a new login does. So:

- ``loggedIn: false``, or a ``loginExpiresAt`` that has passed, is a login that has ended;
- a ``loginExpiresAt`` more than three days ahead is a login that stands;
- anything else (the last three days, no expiry stated, another provider, another
  configuration directory, a ``claude`` that is absent or prints something else) is not
  known, and :func:`judge` says :data:`UNKNOWN`.

The answer describes the login of the configuration directory that ``claude`` finds in the
environment it is given, which is shared by every session of that directory. It is read at
most once per :data:`CACHE_SECONDS` for each binary and directory.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: How long ``claude auth status`` may take before it is given up as not answering.
STATUS_SECONDS = 5.0

#: How long one answer is used before ``claude`` is asked again.
CACHE_SECONDS = 60.0

#: The span before ``loginExpiresAt`` in which 2.1.329 says a sign-in can end at any time.
FINAL_SPAN = timedelta(days=3)

LOGGED_OUT = "logged-out"
LOGGED_IN = "logged-in"
UNKNOWN = "unknown"

#: :attr:`Judgement.reason` for a configuration with no login, and for one whose time is up.
NOT_LOGGED_IN = "not-logged-in"
SIGN_IN_ENDED = "sign-in-ended"


@dataclass(frozen=True)
class AuthStatus:
    """The fields of ``claude auth status --json`` that :func:`judge` reads."""

    logged_in: bool | None
    auth_method: str | None
    api_provider: str | None
    config_directory: str | None
    login_expires_at: datetime | None


@dataclass(frozen=True)
class Judgement:
    """:data:`LOGGED_OUT`, :data:`LOGGED_IN` or :data:`UNKNOWN`, and why it is logged out."""

    state: str
    reason: str | None = None


def find_claude(environ: Mapping[str, str]) -> str | None:
    """The ``claude`` on the ``PATH`` of ``environ``, or None."""
    return shutil.which("claude", path=environ.get("PATH", ""))


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _text(document: dict, key: str) -> str | None:
    value = document.get(key)
    return value if isinstance(value, str) and value else None


def parse(stdout: str) -> AuthStatus | None:
    """The status in ``claude auth status --json``'s output, or None when it is not that."""
    try:
        document = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    logged_in = document.get("loggedIn")
    return AuthStatus(
        logged_in if isinstance(logged_in, bool) else None,
        _text(document, "authMethod"),
        _text(document, "apiProvider"),
        _text(document, "configDirectory"),
        _moment(document.get("loginExpiresAt")),
    )


_cache: dict[tuple[str, str], tuple[float, AuthStatus | None]] = {}
_cache_lock = threading.Lock()


def read_status(environ: Mapping[str, str]) -> AuthStatus | None:
    """What ``claude auth status --json`` says in ``environ``; None when it cannot be read.

    Exit status 1 is what a configuration with no login exits with, and its output is read;
    an output that is not the document is None.
    """
    binary = find_claude(environ)
    if binary is None:
        return None
    key = (binary, environ.get("CLAUDE_CONFIG_DIR", ""))
    with _cache_lock:
        held = _cache.get(key)
        if held is not None and time.monotonic() - held[0] < CACHE_SECONDS:
            return held[1]
        try:
            done = subprocess.run(
                [binary, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=STATUS_SECONDS,
                env=dict(environ),
                check=False,
            )
            status = parse(done.stdout) if done.returncode in (0, 1) else None
        except (OSError, subprocess.SubprocessError):
            status = None
        _cache[key] = (time.monotonic(), status)
        return status


def forget() -> None:
    """Drop every held answer, so that the next read asks ``claude`` again."""
    with _cache_lock:
        _cache.clear()


def _same_directory(stated: str | None, config_dir: Path) -> bool:
    if stated is None:
        return False
    try:
        return Path(stated).resolve() == config_dir.resolve()
    except OSError:
        return False


def judge(status: AuthStatus | None, config_dir: Path, now: datetime) -> Judgement:
    """Whether the login of a session under ``config_dir`` has ended, stands, or is not known.

    Only what the module's docstring lists as an ended login is :data:`LOGGED_OUT`, so that a
    reading that could be wrong never stops a send (KYO-974.c5).
    """
    if status is None or not _same_directory(status.config_directory, config_dir):
        return Judgement(UNKNOWN)
    if status.api_provider != "firstParty":
        return Judgement(UNKNOWN)
    if status.logged_in is False:
        return Judgement(LOGGED_OUT, NOT_LOGGED_IN)
    if status.logged_in is not True or status.login_expires_at is None:
        return Judgement(UNKNOWN)
    if status.login_expires_at <= now:
        return Judgement(LOGGED_OUT, SIGN_IN_ENDED)
    if status.login_expires_at - FINAL_SPAN > now:
        return Judgement(LOGGED_IN)
    return Judgement(UNKNOWN)
