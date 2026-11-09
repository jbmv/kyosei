"""``kyo serve``'s saved composer drafts (KYO-703).

Under ``/v1/projects/{project}/sessions/{id}/draft``: ``GET`` reads a session's draft of
composer boxes, ``PUT`` replaces it. A draft is held by the server so that it survives closing
the app and moving between devices.

WHERE. ``$KYOSEI_HOME/composer/drafts/<session id>.json``, readable by this account alone and
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
``text`` string. A box's other keys are kept as written, for the composer's own use (anchors
arrive with chunk U4); the server does not interpret them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import clock, serve_files, serve_send
from .serve import Refusal
from .serve_sessions import ProjectSessions


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
    return boxes


def _stored(target: Path, session_id: str) -> tuple[dict, str] | None:
    """The stored draft and its etag; None when the session has none."""
    try:
        found = serve_files.read_file(target, f"the draft of session {session_id}")
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


def _response(session_id: str, document: dict | None, etag: str | None) -> dict[str, Any]:
    held = document or {}
    return {
        "ok": True,
        "session_id": session_id,
        "boxes": held.get("boxes", []),
        "updated_at": held.get("updated_at"),
        "device": held.get("device"),
        "etag": etag,
    }


def perform(
    environ: dict[str, str],
    root_of: Callable[[], str],
    session_id: str,
    method: str,
    authorization: str | None,
    *,
    if_match: str | None,
    if_none_match: str | None,
    read_body: Callable[[], bytes],
) -> tuple[int, dict[str, Any]]:
    """Carry out one draft route; the status and response document, or a refusal."""
    body = serve_send.Body(read_body)
    try:
        device = serve_send.require_device(environ, authorization)
        project = ProjectSessions(root_of(), environ)
        project.require(session_id)
        if method == "GET":
            target = serve_files.composer_path(environ, serve_files.DRAFTS, session_id)
            found = _stored(target, session_id)
            document, etag = found if found is not None else (None, None)
            return 200, _response(session_id, document, etag)
        target = serve_files.composer_target(environ, serve_files.DRAFTS, session_id)
        document = {
            "boxes": _boxes(body.read()),
            "updated_at": clock.now(),
            "device": {"id": device.id, "name": device.name},
        }
        data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        status, written = serve_files.put_file(
            target,
            f"the draft of session {session_id}",
            data,
            if_match=if_match,
            if_none_match=if_none_match,
            private=True,
        )
    except Refusal:
        body.drain()
        raise
    return status, _response(session_id, document, written["etag"])
