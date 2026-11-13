"""``kyo serve``'s route for a session's task list (KYO-770).

``/v1/projects/{project}/sessions/{id}/agenda``: ``GET`` reads the list, ``PUT`` saves it as
the person. ``/v1/projects/{project}/agenda`` (KYO-901) is the same two methods on the list
of the project's most recent session (:func:`kyosei.cmd_agenda.latest_session`), live or
ended, so a device shows and edits the list while no session runs, and the next session's
first brief carries what the person left unchecked. Its response names the session it
resolved to. A project that has had no session answers a ``GET`` with no items and
``session_id: null``, and refuses a ``PUT`` with 409. The list, its file and who may change
which item are :mod:`kyosei.cmd_agenda`'s; this module maps HTTP onto it.

WHO. A device token holding the ``send`` permission, as for the session's draft, and the
machine's ``serve_send`` setting is not consulted, because a list does not touch a session.

THE BODY of a ``PUT`` is ``{"items": [...]}``: the whole list in the order the person sees
it. Each item has a ``text`` and may have a ``checked``; an item the list already holds
carries the ``id`` a ``GET`` gave it, and one without an ``id`` is added as the person's. A
device cannot state an item's author. An item of the orchestrator's must be sent as it was
read: a ``PUT`` that edits, checks, unchecks or leaves out one is refused with 403 and
changes nothing.

A STALE WRITER. The draft's rule: ``If-Match: <etag>`` replaces the list a ``GET`` returned
and is refused with 412 and the current etag once anything has been saved since, by a device
or by the orchestrator's ``kyo agenda``; ``If-None-Match: *`` creates a session's first list.
The precondition is judged before the items are, so a device holding an old copy is told to
read again and not that its change is forbidden.

A CHANGE IS ANNOUNCED on the session's event stream as ``agenda``, carrying the new etag and
no item (:mod:`kyosei.serve_sessions`), because that stream is open to a device token of any
permission (KYO-791) and the list is read with one holding ``send``. The project's event
stream says ``changed`` with kind ``agenda`` when any list was saved (KYO-901), which is what
a device with no live session to follow hears.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from . import cmd_agenda, serve_files, serve_send
from .errors import KyoseiError
from .serve import Refusal
from .serve_sessions import ProjectSessions


def _response(session_id: str | None, document: dict | None, etag: str | None) -> dict[str, Any]:
    held = document or {}
    return {
        "ok": True,
        "session_id": session_id,
        "items": [cmd_agenda.public_item(item) for item in held.get("items", [])],
        "updated_at": held.get("updated_at"),
        "etag": etag,
    }


def _sent(raw: bytes) -> object:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal(400, f"the body is not JSON: {exc}", reason="bad-request") from exc
    return document.get("items") if isinstance(document, dict) else None


def perform(
    environ: dict[str, str],
    root_of: Callable[[], str],
    session_id: str | None,
    method: str,
    authorization: str | None,
    *,
    if_match: str | None,
    if_none_match: str | None,
    read_body: Callable[[], bytes],
) -> tuple[int, dict[str, Any]]:
    """Carry out one request for a session's list; the status and the response document.

    ``session_id`` None asks for the project's list, that of its most recent session.
    """
    body = serve_send.Body(read_body)
    try:
        serve_send.require_device(environ, authorization)
        project = ProjectSessions(root_of(), environ)
        if session_id is None:
            session_id = cmd_agenda.latest_session(project.paths.db)
            if session_id is None:
                if method == "GET":
                    return 200, _response(None, None, None)
                raise Refusal(
                    409,
                    "this project has had no session, so it has no list to add to; the "
                    "list begins with the project's first session",
                    reason="no-session",
                )
        else:
            project.require(session_id)
        # The same refusal a draft gives for an id that cannot name a file.
        serve_files.composer_path(environ, cmd_agenda.AGENDAS, session_id)
        try:
            if method == "GET":
                raw = cmd_agenda.read_raw(environ, session_id)
                document = cmd_agenda.parse(raw, "of this session")
                etag = None if raw is None else serve_files.etag_of(raw)
                return 200, _response(session_id, document, etag)
            sent = _sent(body.read())
            status, document, etag = cmd_agenda.write(
                environ,
                session_id,
                lambda stored, at: cmd_agenda.merge_from_device(stored, sent, at),
                headers=(if_match, if_none_match),
            )
        except cmd_agenda.BadItems as exc:
            raise Refusal(400, str(exc), reason="bad-request") from exc
        except cmd_agenda.Forbidden as exc:
            raise Refusal(403, str(exc), reason="orchestrator-item") from exc
        except KyoseiError as exc:
            raise Refusal(500, str(exc), reason="composer-state-unusable") from exc
    except Refusal:
        body.drain()
        raise
    return status or 200, _response(session_id, document, etag)
