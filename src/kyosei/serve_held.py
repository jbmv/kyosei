"""``kyo serve``'s held messages: a message sent during a turn, kept until the turn ends (KYO-703).

Under ``/v1/projects/{project}/sessions/{id}/held``:

- ``POST`` -- ``{"segments": [...]}`` as for the send route; the message is kept instead of sent;
- ``GET`` -- the held message, with its text, or ``null``;
- ``DELETE`` with ``If-Match: <its id>`` -- retract it: its segments come back and it is not
  delivered;
- ``POST`` to ``held-now`` with ``If-Match: <its id>`` -- send it now (KYO-722), to a working
  screen as well as an idle one. Claude Code queues what is typed during a turn and gives it
  to the agent at its next tool call, so the turn is not interrupted. The log row of that
  delivery has the reason ``during-turn``. Every other refusal of a delivery stands.

One message per session, and it is delivered when the session's screen is idle.

WHERE. ``$KYOSEI_HOME/composer/held/<session id>.json``, private to this account, durable before
the POST answers, and refused inside a git repository (:mod:`kyosei.serve_files`). Such a file
is what makes a message held, so the server reads it again when it starts.

THE WATCHING. The sampler (:mod:`kyosei.serve_sessions`) stops with its last client, so delivery
has its own thread. It is started when a message is held or found at start, looks at each held
session every :data:`HELD_POLL_SECONDS`, and ends when nothing that can be delivered is left, so
nothing reads a pane while nothing is held. One look costs what a sampler's tick does for the
pane: the pane lookup and one ``capture-pane``.

A SET OF NOTES (KYO-706) is held like any other message, with the ids of its notes in ``notes``,
and A PROMPT WITH MARKS (KYO-709) with its own segments, its marks and what each marked line
files in ``prompt``. What either files -- the notes' sent state, the prompt's notes -- is
filed when it is delivered (:func:`kyosei.serve_notes.file_delivered`), and not when it is
held, refused or retracted, because only then has the agent received it.

IF THAT FILING FAILS the message stays delivered: the agent has it, so it is not held again or
sent twice. It is kept in ``$KYOSEI_HOME/composer/unfiled/<message id>.json``, the log names
it, and the filing is tried again each time the server resumes its held messages, at start
(:meth:`HeldMessages.resume`), until it succeeds and the file is removed.

DELIVERY goes through :func:`kyosei.serve_send.deliver_held`, because that module is the only one
that can reach the sending half. A message that cannot go yet stays held with a reason: a screen
showing a prompt or a dialog, an input box with text in it, no pane, sending switched off, a
device since revoked. Each is looked at again on every poll. ``send-failed`` is the exception:
keys may have reached the pane, so it is not tried again and the person retracts it. So is a
message found marked as being delivered when the server starts, which it died delivering.

RETRACT AGAINST DELIVERY. One lock per session is held across an attempt and across a
retraction, so a retraction that arrives during an attempt waits for it, then either returns
the text or finds the message gone. Exactly one of the two happens. ``If-Match`` names the
message, so that a device retracting what it saw cannot take a newer message held by another.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import clock, serve_files, serve_notes, serve_send
from .serve import Refusal
from .serve_events import frame
from .serve_sessions import ProjectSessions

#: How often a held message's screen is looked at. Delivery waits two looks once the turn ends.
HELD_POLL_SECONDS = 1.0

_WATCH_THREAD = "kyo-serve-held-watcher"

#: The reason on a message found marked as being delivered when the server starts.
_INTERRUPTED = "delivery-interrupted"


def _unreadable(path: Path, why: str) -> Refusal:
    return Refusal(
        500,
        f"the held message {path} cannot be read ({why}); remove the file, and the message "
        "with it, to hold another",
        reason="composer-state-unusable",
    )


def _public(message: dict, *, text: bool) -> dict:
    """The message as a response or a stream states it; only a token's holder sees text."""
    segments = (
        [{"kind": s["kind"], "text": s["text"]} for s in message["segments"]]
        if text
        else [{"kind": s["kind"], "length": len(s["text"])} for s in message["segments"]]
    )
    return {
        "id": message["id"],
        "at": message["at"],
        "device": message["device"],
        "automatic": message["automatic"],
        "segments": segments,
        "state": "delivering" if message["delivering"] else "held",
        "reason": message["reason"],
        "detail": message["detail"],
        "retrying": message["retrying"],
        "notes": message.get("notes", []),
        "prompt": _prompt(message, text=text),
    }


def _prompt(message: dict, *, text: bool) -> dict | None:
    """A prompt's own segments and marks, which restore a composer; without text, a count."""
    prompt = message.get("prompt")
    if prompt is None:
        return None
    if not text:
        return {"marks": len(prompt["marks"])}
    return {"segments": prompt["segments"], "marks": prompt["marks"]}


class HeldMessages:
    """The server's held messages: the files, the per-session locks and the watching thread."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.environ: dict[str, str] = server.environ
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        #: Sessions whose last look found an idle screen; memory only, so a restart looks twice.
        self._idle_once: set[str] = set()

    # ------------------------------------------------------------------------ the files

    def _lock_of(self, session_id: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(session_id, threading.Lock())

    def _path(self, session_id: str) -> Path:
        return serve_files.composer_path(self.environ, serve_files.HELD, session_id)

    @staticmethod
    def _load(path: Path) -> dict | None:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _unreadable(path, str(exc)) from exc
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _unreadable(path, str(exc)) from exc
        return document if isinstance(document, dict) else None

    def _save(self, path: Path, message: dict) -> None:
        serve_files.write_private(path, (json.dumps(message, ensure_ascii=False) + "\n").encode())

    def _held_paths(self) -> list[Path]:
        directory = serve_files.composer_kind_directory(self.environ, serve_files.HELD)
        if not directory.is_dir():  # read, not created: a server may never hold a message
            return []
        return sorted(p for p in directory.glob("*.json") if p.is_file())

    # ------------------------------------------------------------------------- the routes

    def hold(
        self,
        project_name: str,
        root_of: Callable[[], str],
        session_id: str,
        authorization: str | None,
        read_body: Callable[[], bytes],
        *,
        notes: list[str] | None = None,
        prompt: dict | None = None,
    ) -> dict[str, Any]:
        """Keep a message for ``session_id``, past every check a send makes.

        ``notes`` names the notes the message was made from, if it was, and ``prompt`` is a
        marked prompt's segments, marks and filing (:func:`kyosei.serve_notes.send_prompt`).
        """

        def store(device: dict, automatic: bool, segments: list[dict]) -> dict:
            with self._lock_of(session_id):
                path = serve_files.composer_target(self.environ, serve_files.HELD, session_id)
                if (existing := self._load(path)) is not None:
                    raise Refusal(
                        409,
                        f"session {session_id} already holds message {existing['id']}, and a "
                        "session holds one at a time; DELETE this route with If-Match: "
                        f"{existing['id']} to take it back, or wait for it to be delivered",
                        reason="already-held",
                        held_id=existing["id"],
                    )
                message = {
                    "id": uuid.uuid4().hex,
                    "project": project_name,
                    "session_id": session_id,
                    "at": clock.now(),
                    "device": device,
                    "automatic": automatic,
                    "segments": segments,
                    "reason": None,
                    "detail": None,
                    "retrying": True,
                    "delivering": False,
                    "notes": notes or [],
                    "prompt": prompt,
                }
                self._save(path, message)
            self._announce(session_id, "held", message)
            self._ensure_watching()
            return _public(message, text=False)

        return serve_send.perform(
            self.environ,
            project_name,
            root_of,
            session_id,
            serve_send.HOLD,
            authorization,
            read_body,
            hold=store,
        )

    def _authorized(
        self, root_of: Callable[[], str], session_id: str, authorization: str | None
    ) -> Any:
        device = serve_send.require_device(self.environ, authorization)
        ProjectSessions(root_of(), self.environ).require(session_id)
        return device

    def read(
        self, root_of: Callable[[], str], session_id: str, authorization: str | None
    ) -> dict[str, Any]:
        self._authorized(root_of, session_id, authorization)
        message = self._load(self._path(session_id))
        return {
            "ok": True,
            "session_id": session_id,
            "held": None if message is None else _public(message, text=True),
        }

    def retract(
        self,
        root_of: Callable[[], str],
        session_id: str,
        authorization: str | None,
        if_match: str | None,
    ) -> dict[str, Any]:
        """Take a held message back. Waits for any attempt in flight (see the module's note)."""
        device = self._authorized(root_of, session_id, authorization)
        with self._lock_of(session_id):
            path = self._path(session_id)
            message = self._load(path)
            if message is None:
                raise Refusal(
                    404,
                    f"session {session_id} holds no message: it has been delivered or taken "
                    "back. GET its turns route to see whether it was delivered",
                    reason="not-held",
                )
            named = (if_match or "").strip().strip('"')
            if named != message["id"]:
                raise Refusal(
                    428 if not named else 412,
                    f"retracting needs If-Match: {message['id']}, the id of the message held "
                    "now, so that a newer message is never taken unseen",
                    reason="wrong-held-id",
                    held_id=message["id"],
                )
            serve_files.remove_private(path)
            self._idle_once.discard(session_id)
        serve_send.log_held(
            self.environ,
            message,
            serve_send.RETRACT,
            {"id": device.id, "name": device.name},
        )
        self._announce(session_id, "retracted", None, message_id=message["id"])
        return {
            "ok": True,
            "session_id": session_id,
            "id": message["id"],
            "automatic": message["automatic"],
            "segments": _public(message, text=True)["segments"],
            "notes": message.get("notes", []),
            "prompt": _prompt(message, text=True),
        }

    # ------------------------------------------------------------------- what a stream says

    def announced(self, session_id: str) -> dict | None:
        """The held message for a stream's ``ready`` event: its state, never its text."""
        try:
            message = self._load(self._path(session_id))
        except Refusal:
            return None
        return None if message is None else _public(message, text=False)

    def _announce(
        self,
        session_id: str,
        outcome: str,
        message: dict | None,
        *,
        message_id: str | None = None,
    ) -> None:
        identifier = message_id or (None if message is None else message["id"])
        document = {
            "session_id": session_id,
            "outcome": outcome,
            "id": identifier,
            "held": None if message is None else _public(message, text=False),
        }
        self.server.session_hub.announce(session_id, frame("held", document))

    # ----------------------------------------------------------------------- the watching

    def resume(self) -> None:
        """At start: pick up what a previous server held, and file what it could not."""
        self._file_unfiled()
        try:
            paths = self._held_paths()
        except (Refusal, OSError):
            return  # refused again, by name, when a route first needs the directory
        for path in paths:
            try:
                message = self._load(path)
            except Refusal:
                continue
            if message is None or not message.get("delivering"):
                continue
            message.update(
                delivering=False,
                retrying=False,
                reason=_INTERRUPTED,
                detail=(
                    "the server stopped while this message was being delivered, and keys may "
                    "have reached the pane, so it is not tried again; retract it and send it "
                    "again if the transcript does not show it"
                ),
            )
            self._save(path, message)
        self._ensure_watching()

    # ---------------------------------------------------------- filing after delivery

    def _file(self, message: dict) -> None:
        """File what a delivered message files, or keep it to be filed at the next start."""
        problem = serve_notes.file_delivered(self.server, message)
        if problem is None:
            return
        try:
            path = serve_files.composer_target(self.environ, serve_files.UNFILED, message["id"])
            self._save(path, message)
            kept = f"it is kept in {path} and filed when kyo serve next starts"
        except (Refusal, OSError) as exc:
            kept = f"and it could not be kept to be filed later ({exc})"
        self.server.log_stream.write(
            f"kyo serve: message {message['id']} was delivered to session "
            f"{message['session_id']}, but what it files could not be filed ({problem}); "
            f"{kept}\n"
        )

    def _file_unfiled(self) -> None:
        """Try again to file each delivered message whose filing failed."""
        directory = serve_files.composer_kind_directory(self.environ, serve_files.UNFILED)
        if not directory.is_dir():
            return
        for path in sorted(p for p in directory.glob("*.json") if p.is_file()):
            try:
                message = self._load(path)
            except Refusal:
                continue
            if message is None:
                continue
            problem = serve_notes.file_delivered(self.server, message)
            if problem is None:
                serve_files.remove_private(path)
                continue
            self.server.log_stream.write(
                f"kyo serve: message {message['id']}, delivered to session "
                f"{message['session_id']}, still could not be filed ({problem}); it stays in "
                f"{path} and is tried again when kyo serve next starts\n"
            )

    def _ensure_watching(self) -> None:
        with self._lock:
            if self._thread is None and not self.server.stopping.is_set():
                self._thread = threading.Thread(
                    target=self._watch, name=_WATCH_THREAD, daemon=True
                )
                self._thread.start()

    def _waiting_on(self) -> list[str]:
        """The sessions whose held message can still be delivered."""
        sessions = []
        for path in self._held_paths():
            try:
                message = self._load(path)
            except Refusal:
                continue
            if message is not None and message.get("retrying"):
                sessions.append(message["session_id"])
        return sessions

    def _watch(self) -> None:
        stopping: threading.Event = self.server.stopping
        while not stopping.is_set():
            try:
                sessions = self._waiting_on()
            except (Refusal, OSError):
                sessions = []
            if not sessions:
                with self._lock:
                    # Under the lock a hold's _ensure_watching waits for, and a hold saves its
                    # file first, so a message saved after this empty look starts a new thread.
                    try:
                        if not self._waiting_on():
                            self._thread = None
                            return
                    except (Refusal, OSError):
                        self._thread = None
                        return
                continue
            for session_id in sessions:
                try:
                    self.attend(session_id)
                except Exception as exc:  # noqa: BLE001 - one message must not end the watching
                    self.server.log_stream.write(
                        f"kyo serve: could not look at the held message of {session_id}: {exc}\n"
                    )
            stopping.wait(self.server.held_poll_seconds)

    def send_now(
        self,
        root_of: Callable[[], str],
        session_id: str,
        authorization: str | None,
        if_match: str | None,
    ) -> dict[str, Any]:
        """Deliver the held message at once, during a turn; refuse, naming why, if it cannot."""
        self._authorized(root_of, session_id, authorization)
        result = self.attend(session_id, during_turn=True, if_match=if_match or "")
        assert result is not None
        if result.outcome == "delivered":
            return {"ok": True, "session_id": session_id, "outcome": "delivered"}
        if result.outcome == "failed":
            raise Refusal(502, result.detail or "delivery failed", reason=result.reason)
        raise Refusal(
            409,
            result.detail
            or f"session {session_id} cannot take the message now; it stays held",
            reason=result.reason or "not-deliverable",
        )

    def attend(
        self, session_id: str, *, during_turn: bool = False, if_match: str | None = None
    ) -> Any:
        """One look at a session's held message, delivering it if its screen allows.

        With ``during_turn`` (Send now) the message named by ``if_match`` is delivered whatever
        the screen's state allows, and the look's outcome is returned.
        """
        with self._lock_of(session_id):
            try:
                path = self._path(session_id)
                message = self._load(path)
            except Refusal:
                if during_turn:
                    raise
                return None
            if during_turn:
                if message is None:
                    raise Refusal(
                        404,
                        f"session {session_id} holds no message: it has been delivered or "
                        "taken back. GET its turns route to see whether it was delivered",
                        reason="not-held",
                    )
                named = (if_match or "").strip().strip('"')
                if named != message["id"]:
                    raise Refusal(
                        428 if not named else 412,
                        f"sending now needs If-Match: {message['id']}, the id of the message "
                        "held now, so that a newer message is never sent unseen",
                        reason="wrong-held-id",
                        held_id=message["id"],
                    )
                if not message["retrying"]:
                    raise Refusal(
                        409,
                        f"message {message['id']} is not tried again ({message['reason']}): "
                        "keys may have reached the pane; retract it and send it again if the "
                        "transcript does not show it",
                        reason=message["reason"] or "not-retrying",
                    )
            elif message is None or not message["retrying"]:
                return None

            def mark_delivering() -> None:
                message["delivering"] = True
                self._save(path, message)

            settled = session_id in self._idle_once
            result = serve_send.deliver_held(
                self.environ,
                message,
                lambda: self.server.project(message["project"]).root,
                settled=settled,
                mark_delivering=mark_delivering,
                during_turn=during_turn,
            )
            changed = False
            if result.outcome == "delivered":
                message["delivered_at"] = clock.now()
                serve_files.remove_private(path)
                self._idle_once.discard(session_id)
                if result.log_problem:
                    self.server.log_stream.write(
                        f"kyo serve: held message delivered, not logged: {result.log_problem}\n"
                    )
            else:
                if result.idle:
                    self._idle_once.add(session_id)
                else:
                    self._idle_once.discard(session_id)
                retrying = result.outcome == "waiting"
                changed = (
                    message["delivering"]
                    or message["reason"] != result.reason
                    or message["detail"] != result.detail
                    or message["retrying"] != retrying
                )
                if changed:
                    message.update(
                        delivering=False,
                        retrying=retrying,
                        reason=result.reason,
                        detail=result.detail,
                    )
                    self._save(path, message)
        if result.outcome == "delivered":
            self._file(message)
            self._announce(session_id, "delivered", None, message_id=message["id"])
        elif changed:
            self._announce(session_id, "held", message)
        return result
