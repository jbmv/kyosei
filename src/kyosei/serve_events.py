"""``kyo serve``'s change events: Server-Sent Events from a stat fingerprint (KYO-456).

``GET /v1/projects/{project}/events`` is ``text/event-stream``. Each change is

    event: changed
    data: {"kind": "store"}

with ``kind`` one of ``store``, ``ideas``, ``sessions`` and ``scratchpad``, and a ``: keepalive``
comment goes out every :data:`KEEPALIVE_SECONDS` so a proxy in between (Tailscale
Serve, say) does not close an idle stream. It replaced the hand-framed WebSocket of
the server the desktop app once ran; ``EventSource`` is built into every browser.

THE FINGERPRINT is the desktop app's former watcher rule, polled once a second with ``stat``
only -- the standard library has no file watcher, and a second's ``stat`` of an idle
store costs nothing measurable:

- ``store``: the database's mtime and size, and the export directory's mtime. A
  read-only command opens the database without moving either (SQLite's journal
  moves ``kyosei/``'s mtime, which is why the directory itself is not read).
- ``sessions``: ``sessions.json``'s mtime and size.
- ``ideas``: the newest mtime and the entry count under ``ideas/`` -- a delete moves
  the count or its folder's mtime, an edit moves a file's mtime.
- ``scratchpad`` (KYO-575): the session scratchpad's mtime, size and inode. It lives in
  the per-project home, outside ``ideas/``, so no other kind sees it move -- and a
  window with the scratchpad open has to hear that another window or machine saved
  it, or it keeps showing (and later merges against) text that is no longer the file.
  The inode is in the stamp because kyo serve writes by atomic rename: two saves
  inside one mtime tick with the same length are still two files.

A path that is not there is part of the answer (``gone``), so a store whose database
appears later changes from ``gone`` to a stamp, which is an event.

Streams do not take the server's command lock: a fingerprint reads no store content.
A stream ends when its client goes away, or when the server stops.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from .serve_files import ProjectPaths

#: How often the fingerprint is taken (once a second).
POLL_SECONDS = 1.0

#: How often a ``: keepalive`` comment is sent on an idle stream.
KEEPALIVE_SECONDS = 15.0

#: The kinds a ``changed`` event names, in the order a simultaneous change sends them.
KINDS = ("store", "ideas", "sessions", "scratchpad")


def _stamp(path: Path) -> str:
    try:
        info = os.stat(path)
    except OSError:
        return "gone"
    return f"{info.st_mtime_ns}:{info.st_size}"


def _file_stamp(path: Path) -> str:
    """``_stamp`` plus the inode, for a file replaced by rename rather than rewritten."""
    try:
        info = os.stat(path)
    except OSError:
        return "gone"
    return f"{info.st_mtime_ns}:{info.st_size}:{info.st_ino}"


def _ideas_stamp(root: Path) -> str:
    """The newest mtime and the entry count under ``root``, without following links."""
    try:
        newest = os.stat(root).st_mtime_ns
    except OSError:
        return "gone"
    count = 0
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            count += 1
            try:
                mtime = os.lstat(os.path.join(directory, name)).st_mtime_ns
            except OSError:
                continue
            newest = max(newest, mtime)
    return f"{newest}:{count}"


def fingerprint(paths: ProjectPaths) -> dict[str, str]:
    """One stamp per kind; a kind whose stamp differs from the last one changed."""
    return {
        "store": f"{_stamp(paths.db)}|{_stamp(paths.export)}",
        "ideas": _ideas_stamp(paths.ideas),
        "sessions": _stamp(paths.sessions),
        "scratchpad": _file_stamp(paths.scratchpad),
    }


def changed_event(kind: str) -> bytes:
    return f"event: changed\ndata: {json.dumps({'kind': kind})}\n\n".encode()


KEEPALIVE = b": keepalive\n\n"

#: Sent once when the stream opens, so a client knows it is attached before the
#: first change, and told how soon to reconnect if it drops.
OPENING = b"retry: 1000\n: kyo serve events\n\n"


def stream(
    paths: ProjectPaths,
    write: Callable[[bytes], None],
    stopping: threading.Event,
    *,
    poll_seconds: float = POLL_SECONDS,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> None:
    """Write events to ``write`` until it raises (the client left) or ``stopping``.

    The first fingerprint is the baseline: a stream reports changes AFTER it opened,
    and a client reads current state with the ordinary routes when it attaches.
    """
    last = fingerprint(paths)
    since_keepalive = 0.0
    try:
        write(OPENING)
        while not stopping.wait(poll_seconds):
            now = fingerprint(paths)
            sent = False
            for kind in KINDS:
                if now[kind] != last[kind]:
                    write(changed_event(kind))
                    sent = True
            last = now
            since_keepalive = 0.0 if sent else since_keepalive + poll_seconds
            if since_keepalive >= keepalive_seconds:
                write(KEEPALIVE)
                since_keepalive = 0.0
    except OSError:  # the client went away: a broken pipe or a reset
        return
