"""``kyo statusline``: Claude Code's usage limits, saved for `kyo serve` (KYO-714).

Claude Code runs a status line command after each reply and passes it a JSON document
on stdin (https://code.claude.com/docs/en/statusline). For a subscriber that document
carries ``rate_limits.five_hour`` and ``rate_limits.seven_day``, each with
``used_percentage`` (0 to 100) and ``resets_at`` (Unix epoch seconds). The object is
absent before a session's first reply and for a non-subscriber, either window can be
absent on its own, and a window stops being sent once its ``resets_at`` has passed.

The limits are the account's, not a session's, so one file per machine holds the latest
figures: ``$KYOSEI_HOME/usage-limits.json``, beside ``device-tokens.json``, replaced
whole on each write. Input without ``rate_limits`` leaves it as it is, so a session
that has not replied yet does not erase what another session saved.

THE PROMPT CACHE (KYO-715). After the main conversation's first response the document
also carries ``prompt_cache``, whose ``warm`` (a boolean), ``expires_at`` (epoch seconds,
or null when the last response reported no cache tokens) and ``recache_tokens_if_cold``
(a token count, or null after a compaction) are saved for the document's ``session_id``
in ``$KYOSEI_HOME/prompt-cache.json``, beside the limits. Unlike the limits these are a
session's, so the file holds one entry per session, each with its ``read_at``, and keeps
the :data:`CACHE_SESSIONS_KEPT` most recently read; an older session's entry is dropped
when a newer one is saved. Each write replaces the file whole under an ``flock`` on a
lock file beside it, so two sessions replying at once do not drop each other's entry.

A status line command runs often and its failure shows in the person's terminal, so the
plain form exits 0 and prints nothing on any error: unreadable input, a malformed
document, an unwritable home. ``--install``, ``--uninstall`` and ``--print`` manage the
``statusLine`` entry in Claude Code's user settings and refuse as any command does.

:func:`read_limits` and :func:`read_cache` are the reading half, which
:mod:`kyosei.session_layer` calls each time it builds a session's facts, so a new figure
reaches a client without a restart.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import sys
import tempfile
from argparse import Namespace
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .commands import Context, Output
from .config import store_home
from .doctor_install import claude_config_dir
from .errors import KyoseiError

#: The file's name under ``$KYOSEI_HOME``.
LIMITS_FILE = "usage-limits.json"

#: KYO-715: the prompt cache's file under ``$KYOSEI_HOME``, one entry per session.
CACHE_FILE = "prompt-cache.json"

#: How many sessions' cache entries the file keeps, the most recently read.
CACHE_SESSIONS_KEPT = 50

#: The two windows Claude Code reports, in the order the status row shows them, with
#: the word each is shown by.
WINDOWS: tuple[tuple[str, str], ...] = (("five_hour", "5-hour"), ("seven_day", "weekly"))

#: The ``statusLine`` entry ``--install`` writes and ``--uninstall`` removes.
STATUS_LINE = {"type": "command", "command": "kyo statusline"}


def limits_path(environ: Mapping[str, str] | None = None) -> Path:
    return store_home(dict(environ) if environ is not None else None) / LIMITS_FILE


def cache_path(environ: Mapping[str, str] | None = None) -> Path:
    return store_home(dict(environ) if environ is not None else None) / CACHE_FILE


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _number(value: object) -> float | None:
    """A finite JSON number, never a bool (``True`` is an ``int`` in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def windows_from(document: object) -> dict[str, dict]:
    """The well-formed windows of a status line payload, keyed as Claude Code keys them.

    A window missing either field, or carrying one that is not a number, is left out
    rather than saved half-known.
    """
    if not isinstance(document, dict):
        return {}
    limits = document.get("rate_limits")
    if not isinstance(limits, dict):
        return {}
    found: dict[str, dict] = {}
    for key, _word in WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        used = _number(window.get("used_percentage"))
        resets = _number(window.get("resets_at"))
        if used is None or resets is None:
            continue
        found[key] = {"used_percentage": used, "resets_at": int(resets)}
    return found


def _percent(used: float) -> int:
    # Half up: round() would send an exact half to the even neighbour.
    return int(math.floor(used + 0.5))


def status_row(windows: Mapping[str, dict]) -> str:
    """``5-hour NN% · weekly NN%``, naming only the windows given; empty for none."""
    return " · ".join(
        f"{word} {_percent(windows[key]['used_percentage'])}%"
        for key, word in WINDOWS
        if key in windows
    )


def save_limits(path: Path, windows: Mapping[str, dict], read_at: datetime) -> None:
    """Replace the file whole, so a reader never sees half of a write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _replace_json(path, {"read_at": _iso(read_at), **windows})


def read_limits(
    environ: Mapping[str, str] | None = None, now: datetime | None = None
) -> dict | None:
    """The saved limits as the session facts carry them, or None.

    ``resets_at`` is an ISO time. A window whose reset time has passed is left out: the
    figure describes a window that has ended. None when there is no file, when it cannot
    be read, or when no window is left. Read from disk on every call.
    """
    path = limits_path(environ)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    moment = (now or _now()).timestamp()
    shown: dict[str, object] = {}
    for key, window in windows_from({"rate_limits": document}).items():
        if window["resets_at"] <= moment:
            continue
        shown[key] = {
            "used_percentage": window["used_percentage"],
            "resets_at": _iso(datetime.fromtimestamp(window["resets_at"], UTC)),
        }
    if not shown:
        return None
    read_at = document.get("read_at")
    shown["read_at"] = read_at if isinstance(read_at, str) else None
    return shown


# ------------------------------------------------------- the prompt cache (KYO-715)


def _nullable_number(value: object) -> tuple[bool, int | None]:
    """Whether ``value`` is a number or null, and the number as an int."""
    if value is None:
        return True, None
    number = _number(value)
    return (False, None) if number is None else (True, int(number))


def cache_from(document: object) -> tuple[str, dict] | None:
    """The session id and its prompt cache's three fields, or None.

    None unless ``session_id`` is a non-empty string and ``prompt_cache`` carries a
    boolean ``warm`` and an ``expires_at`` and ``recache_tokens_if_cold`` that are each a
    number or null. A missing field counts as null, as Claude Code sends null for one
    it does not know.
    """
    if not isinstance(document, dict):
        return None
    session_id = document.get("session_id")
    cache = document.get("prompt_cache")
    if not isinstance(session_id, str) or not session_id or not isinstance(cache, dict):
        return None
    warm = cache.get("warm")
    if not isinstance(warm, bool):
        return None
    expires_ok, expires_at = _nullable_number(cache.get("expires_at"))
    recache_ok, recache = _nullable_number(cache.get("recache_tokens_if_cold"))
    if not (expires_ok and recache_ok):
        return None
    return session_id, {
        "warm": warm,
        "expires_at": expires_at,
        "recache_tokens_if_cold": recache,
    }


def _saved_sessions(path: Path) -> dict[str, dict]:
    """The file's entries, or none when it is absent or unreadable."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    sessions = document.get("sessions") if isinstance(document, dict) else None
    if not isinstance(sessions, dict):
        return {}
    return {key: value for key, value in sessions.items() if isinstance(value, dict)}


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """An exclusive ``flock`` on ``<file>.lock`` for one read-modify-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(f".{path.name}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _weekly_figure(value: object) -> dict | None:
    """A saved first weekly figure when it is well formed, else None."""
    if not isinstance(value, dict):
        return None
    used, resets = _number(value.get("used_percentage")), _number(value.get("resets_at"))
    if used is None or resets is None:
        return None
    return {"used_percentage": used, "resets_at": int(resets)}


def save_cache(
    path: Path,
    session_id: str,
    cache: Mapping | None,
    read_at: datetime,
    weekly: Mapping | None = None,
) -> None:
    """Save one session's entry, keeping the most recently read entries only.

    ``weekly`` is the weekly window read with this write. The first one saved for the
    session is kept as ``weekly_at_start`` and carried unchanged through every later write
    of the entry (KYO-717), and so are the cache fields when a write carries none.
    """
    with _locked(path):
        sessions = _saved_sessions(path)
        entry = dict(sessions.get(session_id) or {})
        if cache is not None:
            entry.update(cache)
        first = _weekly_figure(entry.get("weekly_at_start")) or _weekly_figure(weekly)
        entry.pop("weekly_at_start", None)
        if first is not None:
            entry["weekly_at_start"] = first
        entry["read_at"] = _iso(read_at)
        sessions[session_id] = entry
        recent = sorted(
            sessions.items(),
            key=lambda item: str(item[1].get("read_at") or ""),
            reverse=True,
        )[:CACHE_SESSIONS_KEPT]
        _replace_json(path, {"sessions": dict(recent)})


def read_weekly_at_start(
    session_id: str, environ: Mapping[str, str] | None = None
) -> dict | None:
    """The weekly figure first read for the session, or None when none was saved (KYO-717).

    ``resets_at`` is an ISO time, as the facts' ``limits`` state it, so the two compare.
    """
    entry = _saved_sessions(cache_path(environ)).get(session_id)
    figure = None if entry is None else _weekly_figure(entry.get("weekly_at_start"))
    if figure is None:
        return None
    return {
        "used_percentage": figure["used_percentage"],
        "resets_at": _iso(datetime.fromtimestamp(figure["resets_at"], UTC)),
    }


def read_cache(session_id: str, environ: Mapping[str, str] | None = None) -> dict | None:
    """One session's saved prompt cache as the session facts carry it, or None.

    ``expires_at`` is an ISO time (or null, as saved). Whether the cache is cold is not
    decided here: a client compares ``expires_at`` with its own clock. Read from disk on
    every call.
    """
    entry = _saved_sessions(cache_path(environ)).get(session_id)
    if entry is None:
        return None
    parsed = cache_from({"session_id": session_id, "prompt_cache": entry})
    if parsed is None:
        return None
    _, cache = parsed
    expires_at = cache["expires_at"]
    read_at = entry.get("read_at")
    return {
        "warm": cache["warm"],
        "expires_at": None
        if expires_at is None
        else _iso(datetime.fromtimestamp(expires_at, UTC)),
        "recache_tokens_if_cold": cache["recache_tokens_if_cold"],
        "read_at": read_at if isinstance(read_at, str) else None,
    }


def _replace_json(path: Path, document: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def record(raw: str, environ: Mapping[str, str] | None = None) -> str:
    """Save what ``raw`` carries and return the status row; never raises.

    The limits and a session's prompt cache are saved apart: input with no usable window
    leaves the limits file untouched and returns an empty row, and input with no usable
    ``prompt_cache`` leaves the cache file untouched.
    """
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError):
        return ""
    now = _now()
    cache = cache_from(document)
    windows = windows_from(document)
    weekly = windows.get("seven_day")
    session_id = cache[0] if cache is not None else (
        document.get("session_id") if isinstance(document, dict) else None
    )
    if isinstance(session_id, str) and session_id and (cache is not None or weekly):
        with contextlib.suppress(Exception):  # a status line never fails visibly (KYO-714)
            save_cache(
                cache_path(environ), session_id, cache[1] if cache else None, now, weekly
            )
    if not windows:
        return ""
    try:
        save_limits(limits_path(environ), windows, now)
    except Exception:  # noqa: BLE001 - a status line never fails visibly (KYO-714)
        return ""
    return status_row(windows)


# ------------------------------------------------------------- the settings entry


def settings_path(environ: Mapping[str, str]) -> Path:
    return claude_config_dir(environ) / "settings.json"


def _read_settings(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise KyoseiError(f"kyo statusline: {path} could not be read ({exc})") from exc
    try:
        document = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise KyoseiError(
            f"kyo statusline: {path} is not JSON ({exc}); nothing was written. Correct the "
            "file and run the command again"
        ) from exc
    if not isinstance(document, dict):
        raise KyoseiError(
            f"kyo statusline: {path} is not a JSON object; nothing was written. Correct the "
            "file and run the command again"
        )
    return document


def _write_settings(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _is_ours(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("type") == STATUS_LINE["type"]
        and entry.get("command") == STATUS_LINE["command"]
    )


def _manage(ctx: Context, args: Namespace) -> Output:
    path = settings_path(ctx.environ)
    document = _read_settings(path)
    current = document.get("statusLine")
    action = "uninstall" if args.uninstall else "install"
    preview = bool(args.print)
    if action == "install":
        if _is_ours(current):
            change, after = "already set", document
        elif current is not None:
            raise KyoseiError(
                f"kyo statusline --install: {path} already sets a different status line, "
                f"{json.dumps(current)}; nothing was written. A status line runs one "
                "command, so remove that `statusLine` entry from the file (or point its "
                "command at `kyo statusline` yourself) and run `kyo statusline --install` "
                "again"
            )
        else:
            change, after = "set", {**document, "statusLine": dict(STATUS_LINE)}
    elif _is_ours(current):
        change, after = "removed", {k: v for k, v in document.items() if k != "statusLine"}
    else:
        what = "no status line" if current is None else f"a different one, {json.dumps(current)}"
        change, after = "left alone", document
        lines = [f"{path}: sets {what}; kyo statusline --uninstall removes only its own"]
        return Output(lines, _payload(action, path, change, preview, after))
    if change in ("set", "removed") and not preview:
        _write_settings(path, after)
    verb = {"set": "statusLine set to", "removed": "statusLine removed:"}.get(change)
    entry = json.dumps(STATUS_LINE)
    if verb is None:
        line = f"{path}: statusLine is already {entry}"
    elif preview:
        line = f"{path}: would have {verb} {entry} (--print wrote nothing)"
    else:
        line = f"{path}: {verb} {entry}"
    return Output([line], _payload(action, path, change, preview, after))


def _payload(action: str, path: Path, change: str, preview: bool, after: dict) -> dict:
    return {
        "ok": True,
        "command": "statusline",
        "action": action,
        "file": str(path),
        "change": change,
        "written": change in ("set", "removed") and not preview,
        "status_line": after.get("statusLine"),
    }


def cmd_statusline(ctx: Context, args: Namespace) -> Output:
    """``kyo statusline`` (stdin from Claude Code), or ``--install``/``--uninstall``."""
    if args.install or args.uninstall:
        return _manage(ctx, args)
    if args.print:
        raise KyoseiError(
            "kyo statusline --print previews a settings change; run it with --install or "
            "--uninstall (`kyo statusline --install --print`)"
        )
    try:
        raw = "" if sys.stdin is None or sys.stdin.closed else sys.stdin.read()
    except Exception:  # noqa: BLE001 - a status line never fails visibly (KYO-714)
        raw = ""
    row = record(raw, ctx.environ)
    return Output([row] if row else [], {"ok": True, "command": "statusline", "row": row})
