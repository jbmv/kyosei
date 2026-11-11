"""``kyo serve``'s saved composer drafts (KYO-703, KYO-763).

Under ``/v1/projects/{project}/sessions/{id}/draft``: ``GET`` reads a session's draft of
composer boxes, ``PUT`` replaces it. A draft is held by the server so that it survives closing
the app and moving between devices. ``/v1/projects/{project}/draft`` is the project's own
draft, read and replaced the same way: the boxes written while no session of the project is
live.

WHERE. ``$KYOSEI_HOME/composer/drafts/<session id>.json`` and
``$KYOSEI_HOME/composer/project-drafts/<project>.json``, readable by this account alone and
through :mod:`kyosei.serve_files`, which refuses the write if the path would lie inside a git
repository. Unsent text is as sensitive as a transcript. A project's own directory is not
used, because under the split layout that directory is the store's repository.

A STALE WRITER. The draft's ETag is a hash of its stored bytes, which include the time of the
write, so a ``PUT`` carrying ``If-Match: <etag>`` is refused with 412 and the current etag once
another device has saved since. A first draft is created with ``If-None-Match: *``. This is the
convention of the idea routes (:func:`kyosei.serve_files.put_file`), and the same function
applies it.

WHO. A device token holding the ``send`` permission, and no further permission: a device that
may compose may send, and a drafts-only device has no use. The machine's ``serve_send`` setting
is not consulted, because a draft does not touch a session.

THE SHAPE. ``{"boxes": [{"text": ...}, ...]}``: an ordered list of objects, each with a
``text`` string and an ``id``. A box's other keys are kept as written, for the composer's own
use (its marks: anchors, tags and attachments); the server does not interpret them.

A BOX'S ID (KYO-798) is what every device and the server know one box by, so that a device can
tell a box edited elsewhere from one added, and one sent or deleted elsewhere from one it has
just written. The device that makes a box names it, and the id is kept through every save and
every move between drafts. A box saved without one is given one here, and so is a box read
from a draft stored before boxes had them: that id is derived from the box's text and its
place among boxes of the same text, so every device reading the stored draft is given the
same one without a write. An ``id`` must be a string that is not empty; one repeated within a
draft is replaced on the later box.

A CHANGE IS ANNOUNCED (KYO-798) on a stream an open app already holds, so that a draft saved
on one device is read by the others without their being reopened. A session's draft is
announced on the session's event stream as ``draft``, carrying the session and the new etag,
and the stream's ``ready`` states the etag as ``draft``; :func:`session_etag` is what the
sampler asks on each tick. The project's own draft is announced on the project's event stream
as ``changed`` with ``kind`` ``draft``, from :func:`project_stamp`. Neither carries a box:
both streams are open to a device that may not read a draft, which is read with the ``send``
permission by the ``GET`` above. A device compares the etag with the one its own last save or
read returned, so its own save is not read back.

BETWEEN SESSIONS (KYO-763). Boxes move between a session's draft and the project's at two
moments, and the server makes both moves, from the copies it stores. A device is not asked to,
because the device that notices may hold an older copy than another device saved, or no app
may be open at all.

- A session that has ended leaves its boxes to the project. The server is not told when a
  session ends, so the move is made when the project's draft is next read: by its ``GET``, or
  by the taking described next. The boxes of each ended session are appended to the project's
  draft, the session that ended first going first, a box already there word for word is not
  added again, and the session's draft is left empty.
- A live session with no draft of its own takes the project's. The first ``GET`` of such a
  session's draft writes the project's boxes as the session's draft, empties the project's,
  and answers with them. A second device asking a moment later reads the session's draft, so
  the boxes are handed over once however many devices are open. An ended session takes
  nothing.

Both moves run under one lock in this process and write the destination before they empty the
source, so a failure between the two writes repeats boxes and loses none. A session counts as
ended when ``sessions.json`` says so, or when it is recorded as running and no process runs
it; one whose state cannot be read is left alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import clock, serve_files, serve_send
from .cmd_sessions import STATE_ENDED, read_sessions
from .errors import KyoseiError
from .serve import Refusal
from .serve_sessions import ProjectSessions

#: One move between drafts at a time; a device's own ``PUT`` is held off by the etag instead.
_MOVE_LOCK = threading.Lock()


def _boxes(raw: bytes) -> list[dict]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal(400, f"the body is not JSON: {exc}", reason="bad-request") from exc
    boxes = document.get("boxes") if isinstance(document, dict) else None
    if not isinstance(boxes, list):
        raise Refusal(
            400,
            'the body needs "boxes": a list of objects, each with a "text" string; an empty '
            "list is an empty draft",
            reason="bad-request",
        )
    for index, box in enumerate(boxes):
        if not isinstance(box, dict) or not isinstance(box.get("text"), str):
            raise Refusal(
                400, f'boxes[{index}] needs a "text" string', reason="bad-request"
            )
        if "id" in box and not (isinstance(box["id"], str) and box["id"]):
            raise Refusal(
                400,
                f'boxes[{index}] has an "id" that is not a string of at least one character; '
                "leave the key out and the server names the box",
                reason="bad-request",
            )
    return boxes


def _identified(boxes: list[dict]) -> list[dict]:
    """The boxes, each with an id no other box of the list has (KYO-798).

    A box with none is given one derived from its text and from how many unnamed boxes of
    that text came before it, so the same stored draft reads with the same ids every time.
    """
    seen: set[str] = set()
    unnamed: dict[str, int] = {}
    named: list[dict] = []
    for box in boxes:
        held = box.get("id")
        if not (isinstance(held, str) and held) or held in seen:
            text = box.get("text") if isinstance(box.get("text"), str) else ""
            while True:
                nth = unnamed.get(text, 0)
                unnamed[text] = nth + 1
                held = "b" + hashlib.sha256(f"{nth}\0{text}".encode()).hexdigest()[:20]
                if held not in seen:
                    break
            box = {**box, "id": held}
        seen.add(held)
        named.append(box)
    return named


def _stored(target: Path, shown: str) -> tuple[dict, str] | None:
    """The stored draft and its etag; None when there is none."""
    try:
        found = serve_files.read_file(target, shown)
    except Refusal as refusal:
        if refusal.status == 404:
            return None
        raise
    try:
        document = json.loads(found.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal(
            500,
            f"the draft file {target} cannot be read ({exc}); remove it and save the draft "
            "again",
            reason="composer-state-unusable",
        ) from exc
    return document, found.etag


def _held(found: tuple[dict, str] | None) -> list[dict]:
    """The boxes of a stored draft; none for a draft that is absent or holds no list."""
    boxes = found[0].get("boxes") if found is not None else None
    if not isinstance(boxes, list):
        return []
    return _identified([box for box in boxes if isinstance(box, dict)])


def _unnamed(box: dict) -> dict:
    return {key: value for key, value in box.items() if key != "id"}


def session_etag(environ: dict[str, str], session_id: str) -> str | None:
    """The etag of a session's stored draft; None when it has none or it cannot be read.

    What the session's event stream announces (KYO-798): the etag and no box.
    """
    try:
        path = serve_files.composer_path(environ, serve_files.DRAFTS, session_id)
        return serve_files.etag_of(path.read_bytes())
    except (Refusal, OSError):
        return None


def project_stamp(environ: dict[str, str], name: str) -> str:
    """A stamp of the file holding a project's own draft, which differs after each save.

    What the project's event stream compares (KYO-798). The inode is part of it because a
    draft is written by rename: two saves of one length inside one tick of the file's clock
    are still two files.
    """
    try:
        info = os.stat(serve_files.project_draft_path(environ, name))
    except (Refusal, OSError):
        return "gone"
    return f"{info.st_mtime_ns}:{info.st_size}:{info.st_ino}"


class _Drafts:
    """One request's view of a project's drafts: where each is, and how it is written."""

    def __init__(
        self, environ: dict[str, str], project: ProjectSessions, name: str, device: Any
    ) -> None:
        self.environ = environ
        self.project = project
        self.name = name
        self.device = device

    def shown(self, session_id: str | None) -> str:
        if session_id is None:
            return f"the draft of project {self.name}"
        return f"the draft of session {session_id}"

    def path(self, session_id: str | None, *, create: bool = False) -> Path:
        if session_id is None:
            return serve_files.project_draft_path(self.environ, self.name, create=create)
        if create:
            return serve_files.composer_target(self.environ, serve_files.DRAFTS, session_id)
        return serve_files.composer_path(self.environ, serve_files.DRAFTS, session_id)

    def read(self, session_id: str | None) -> tuple[dict, str] | None:
        return _stored(self.path(session_id), self.shown(session_id))

    def write(
        self,
        session_id: str | None,
        boxes: list[dict],
        *,
        if_match: str | None,
        if_none_match: str | None,
    ) -> tuple[int, dict, str]:
        """Replace one draft under its precondition; the status, the document, the etag."""
        target = self.path(session_id, create=True)
        document = {
            "boxes": _identified(boxes),
            "updated_at": clock.now(),
            "device": {"id": self.device.id, "name": self.device.name},
        }
        data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        status, written = serve_files.put_file(
            target,
            self.shown(session_id),
            data,
            if_match=if_match,
            if_none_match=if_none_match,
            private=True,
        )
        return status, document, written["etag"]

    def move(self, session_id: str | None, boxes: list[dict], etag: str | None) -> tuple[dict, str]:
        """Write a draft as one step of a move, over the copy whose etag was just read."""
        _, document, written = self.write(
            session_id, boxes, if_match=etag, if_none_match="*" if etag is None else None
        )
        return document, written

    def empty(self, session_id: str | None, etag: str) -> None:
        """Empty the draft a move was made from, unless a device has saved to it since.

        The newer save is then kept as it is. Its boxes may repeat ones already moved, which
        is the outcome that loses nothing.
        """
        try:
            self.move(session_id, [], etag)
        except Refusal as refusal:
            if refusal.status != 412:
                raise

    def running(self, entry: dict) -> bool | None:
        """Whether a process runs a recorded session; None when that cannot be read."""
        if entry.get("state") == STATE_ENDED:
            return False
        try:
            return bool(self.project.open_live(str(entry.get("session_id"))).process_running)
        except (Refusal, KyoseiError, OSError, subprocess.SubprocessError):
            return None

    def entries(self) -> list[dict]:
        return read_sessions(self.project.paths.sessions).entries

    def gather(self) -> tuple[dict, str] | None:
        """The project's draft, after each ended session's boxes have been moved into it."""
        stored = self.read(None)
        left: list[tuple[str, str, list[dict], str]] = []
        for entry in self.entries():
            session_id = entry.get("session_id")
            if not isinstance(session_id, str):
                continue
            try:
                found = self.read(session_id)
            except Refusal:
                continue  # a draft that cannot be read is reported when it is asked for
            boxes = _held(found)
            if found is None or not boxes or self.running(entry) is not False:
                continue
            ended = str(entry.get("ended_at") or entry.get("last_seen") or "")
            left.append((ended, session_id, boxes, found[1]))
        if not left:
            return stored
        left.sort(key=lambda row: row[:2])
        boxes = _held(stored)
        for _, _, theirs, _ in left:
            # A box is the one already there when it has that box's id, as it has when the
            # move was made before and the emptying after it failed.
            # One that differs only in its id is the same words written twice, and is not
            # added again either.
            have = {box["id"] for box in boxes}
            words = [_unnamed(box) for box in boxes]
            boxes += [
                box for box in theirs if box["id"] not in have and _unnamed(box) not in words
            ]
        moved = self.move(None, boxes, stored[1] if stored is not None else None)
        for _, session_id, _, etag in left:
            self.empty(session_id, etag)
        return moved

    def take(self, session_id: str) -> tuple[dict, str] | None:
        """A live session with no draft takes the project's; None when nothing was taken."""
        if (own := self.read(session_id)) is not None:
            return own  # another device asked first, and this one reads what it was given
        entry = next((e for e in self.entries() if e.get("session_id") == session_id), None)
        # The cheap questions first: most sessions are asked when there is nothing to take.
        gathered = self.gather()
        boxes = _held(gathered)
        if gathered is None or not boxes or entry is None or self.running(entry) is not True:
            return None
        taken = self.move(session_id, boxes, None)
        self.empty(None, gathered[1])
        return taken


def _response(
    session_id: str | None, name: str, document: dict | None, etag: str | None
) -> dict[str, Any]:
    held = document or {}
    whose = {"project": name} if session_id is None else {"session_id": session_id}
    return {
        "ok": True,
        **whose,
        "boxes": _held((held, "")) if document is not None else [],
        "updated_at": held.get("updated_at"),
        "device": held.get("device"),
        "etag": etag,
    }


def perform(
    environ: dict[str, str],
    name: str,
    root_of: Callable[[], str],
    session_id: str | None,
    method: str,
    authorization: str | None,
    *,
    if_match: str | None,
    if_none_match: str | None,
    read_body: Callable[[], bytes],
) -> tuple[int, dict[str, Any]]:
    """Carry out one draft route; the status and response document, or a refusal.

    ``session_id`` is None for the project's own draft.
    """
    body = serve_send.Body(read_body)
    try:
        device = serve_send.require_device(environ, authorization)
        project = ProjectSessions(root_of(), environ)
        if session_id is not None:
            project.require(session_id)
        drafts = _Drafts(environ, project, name, device)
        if method == "GET":
            found = drafts.read(session_id) if session_id is not None else None
            if found is None:
                with _MOVE_LOCK:
                    found = drafts.gather() if session_id is None else drafts.take(session_id)
            document, etag = found if found is not None else (None, None)
            return 200, _response(session_id, name, document, etag)
        status, document, etag = drafts.write(
            session_id, _boxes(body.read()), if_match=if_match, if_none_match=if_none_match
        )
    except Refusal:
        body.drain()
        raise
    return status, _response(session_id, name, document, etag)
