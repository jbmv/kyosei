"""``kyo serve``'s session routes: the read half of chunk E4 (KYO-689).

Under ``/v1/projects/{project}/sessions``:

- ``GET sessions`` -- the project's recent sessions, each with whether it has a pane and
  whether work is running;
- ``GET sessions/{id}/turns[?cursor=]`` -- the session's turns, replies, tool calls,
  subagents, slash-command output and messages delivered by anything other than the person,
  its facts as of the latest reply (KYO-691), and a cursor to ask for what is new;
- ``GET sessions/{id}/images/{turn}/{index}`` -- one image the person sent (KYO-692), whose
  bytes are carried by no other route;
- ``GET sessions/{id}/subagents/{agent}`` -- one subagent's tool calls, from its own
  transcript (KYO-693), and for a dispatched worker its dispatch and branch lines (KYO-695);
- ``GET sessions/{id}/changes`` -- the lines the session's working directory holds that no
  commit does (KYO-693);
- ``GET sessions/{id}/events[?cursor=]`` -- Server-Sent Events carrying new transcript
  objects, the reply in progress and changes to the screen and the terminal's text;
- ``GET sessions/{id}/screen`` -- what the pane shows now, as the session layer classifies it,
  and its text.

The shapes are in ``docs/reference/topics/serve-and-desktop.md``.

THROUGH THE SESSION LAYER ONLY. This module reads no transcript row and no pane screen; it
speaks to :mod:`kyosei.session_layer` and turns that layer's objects into JSON field by
field, so a field added to an object is exposed only by adding it here, and a client never
parses a Claude Code transcript row.

READ ONLY, AND NEVER THE SENDING HALF. These routes change no session. The layer's adapter
holds the functions that send keys; no ``kyosei.serve*`` module may reach them
(``tests/test_kyo_serve_sessions.py`` fails if one does), and sending arrives with the later
half of E4. The one thing these routes can cause to be written is the layer's own log of an
uncatalogued screen under ``$KYOSEI_HOME/pane-unrecognised``, outside any repository.

OPT-IN (KYO-656). Every route refuses, naming ``read_transcripts``, in a project that has not
opted in. The check is made first, before ``sessions.json`` is read. A session is also
served only by the project whose ``sessions.json`` records it, so an opted-in project cannot
be used to read another project's transcript by id: the layer's own lookup is by id alone.

THE CURSOR. A client passes back the ``cursor`` of the last response. It is counts, one per
list of objects, of how many objects the transcript has yielded in order, and a digest of
those objects' ids. A transcript read from its start yields the same objects in the same
order however the read is divided, so a cursor taken from an incremental read matches a
later read from the start and needs no state on the server. The reader needs
every earlier row, because a tool result is paired with a call made earlier, which is why
this is not a byte offset. A rewritten transcript fails the digest and the whole session
is sent with ``reset: true``.

ONE SAMPLER PER SESSION. Reading a pane costs a subprocess, unlike the ``stat`` the project's
``/events`` route runs on (:mod:`kyosei.serve_events`), so nothing samples a pane while no
client is subscribed. The first subscriber to a session starts a :class:`_Sampler`, later
ones join it, and it stops when the last one leaves. A subscriber is noticed leaving
within :data:`_CLIENT_CHECK_SECONDS`, because a stream's client sends nothing further and the
socket says when it has closed.
"""

from __future__ import annotations

import hashlib
import queue
import re
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from . import session_layer
from .cmd_sessions import STATE_ENDED, read_sessions
from .config import READ_TRANSCRIPTS_KEY, Config, load_config, transcripts_opted_in
from .errors import KyoseiError, NotFound
from .serve import API_PREFIX, Refusal
from .serve_events import KEEPALIVE, frame
from .serve_files import ProjectPaths, project_paths
from .store import open_store

#: How often a subscribed session is sampled. One capture measured about 4.4 ms.
SAMPLE_SECONDS = 0.5

#: How often a session with no pane is checked for having moved to a new transcript. That
#: check reads the head of every sibling transcript, so it runs far less often than the
#: reading of new rows; a session in a pane is followed by its process record instead.
PANELESS_FOLLOW_SECONDS = 30.0

#: How many events may wait for a slow client before its stream is ended; it reconnects
#: with its last cursor and misses nothing.
QUEUE_LIMIT = 1024

#: How often a stream with nothing to send checks that its client is still there.
_CLIENT_CHECK_SECONDS = 0.25

OPENING = b"retry: 1000\n: kyo serve session events\n\n"

_SAMPLER_THREAD = "kyo-serve-session-sampler"

_CURSOR = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)\.([0-9a-f]{12})$")

#: The lists of a delta, in the order a cursor counts them.
_LISTS = ("turns", "replies", "tool_calls", "subagents", "command_outputs", "deliveries")


# ------------------------------------------------------------------- the objects as JSON


def _turn_json(turn: Any) -> dict:
    return {
        "id": turn.id,
        "at": turn.timestamp,
        "form": turn.form,
        "text": turn.full_text,
        "typed": list(turn.segments),
        "paste_known": turn.paste_known,
        "prompt_id": turn.prompt_id,
        "images": [
            {"index": image.index, "media_type": image.media_type, "size": image.size}
            for image in turn.images
        ],
    }


def _usage_json(usage: Any) -> dict | None:
    if usage is None:
        return None
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
    }


def _reply_json(reply: Any) -> dict:
    return {
        "message_id": reply.message_id,
        "uuid": reply.uuid,
        "at": reply.at,
        "text": reply.text,
        "thinking": reply.thinking,
        "thinking_omitted": reply.thinking_omitted,
        "model": reply.model,
        "usage": _usage_json(reply.usage),
        "record_uuids": list(reply.record_uuids),
    }


def _result_json(result: Any) -> dict | None:
    if result is None:
        return None
    return {
        "uuid": result.uuid,
        "at": result.at,
        "text": result.text,
        "is_error": result.is_error,
    }


def _tool_call_json(call: Any) -> dict:
    return {
        "call_id": call.call_id,
        "uuid": call.uuid,
        "at": call.at,
        "name": call.name,
        "input": dict(call.input),
        "result": _result_json(call.result),
    }


def _subagent_json(ref: Any) -> dict:
    return {
        "agent_id": ref.agent_id,
        "tool_call_uuid": ref.tool_call_uuid,
        "description": ref.description,
        "agent_type": ref.agent_type,
    }


def _changes_json(changes: Any) -> dict:
    return {
        "cwd": changes.cwd,
        "root": changes.root,
        "why": changes.why,
        "added": changes.added,
        "removed": changes.removed,
        "files": changes.files,
        "untracked": {
            "files": changes.untracked.files,
            "lines": changes.untracked.lines,
            "uncounted": changes.untracked.uncounted,
        },
    }


def _command_output_json(output: Any) -> dict:
    return {
        "uuid": output.uuid,
        "at": output.at,
        "command": output.command,
        "args": output.args,
        "text": output.text,
    }


def _delivery_json(delivery: Any) -> dict:
    return {
        "uuid": delivery.uuid,
        "at": delivery.at,
        "kind": delivery.kind,
        "text": delivery.text,
        "sender": delivery.sender,
        "sender_id": delivery.sender_id,
        "handback": delivery.handback,
        "task_id": delivery.task_id,
        "call_id": delivery.call_id,
        "status": delivery.status,
        "summary": delivery.summary,
        "usage": _task_usage_json(delivery.usage),
    }


def _task_usage_json(usage: Any) -> dict | None:
    if usage is None:
        return None
    return {
        "total_tokens": usage.total_tokens,
        "tool_uses": usage.tool_uses,
        "duration_ms": usage.duration_ms,
    }


def _facts_json(facts: Any) -> dict | None:
    if facts is None:
        return None
    return {
        "at": facts.at,
        "model": facts.model,
        "effort": facts.effort,
        "git_branch": facts.git_branch,
        "permission_mode": facts.permission_mode,
        "context": {
            "tokens": facts.context.tokens,
            "window": facts.context.window,
            "percent": facts.context.percent,
        },
    }


def _background_json(work: Any) -> dict | None:
    if work is None:
        return None
    return {
        "running": work.running,
        "shells": work.shells,
        "agents": [{"type": kind, "description": what} for kind, what in work.agents],
    }


def _terminal_json(words: Any) -> dict | None:
    """The pane's words, carried apart from the classification, which changes less often."""
    if words is None:
        return None
    return {"text": words.text, "working_line": words.working_line}


def _screen_json(screen: Any) -> dict | None:
    """The layer's classification of a screen. Its text is :func:`_terminal_json`'s."""
    if screen is None:
        return None
    return {
        "state": screen.state,
        "question": screen.question,
        "options": [
            {
                "number": option.number,
                "label": option.label,
                "selected": option.selected,
                "description": option.description,
            }
            for option in screen.options
        ],
        "input_text": screen.input_text,
        "background": _background_json(screen.background),
    }


def _pane_json(lookup: Any) -> dict:
    ref = lookup.pane
    return {
        "present": ref is not None,
        "why": lookup.why,
        "pane_id": None if ref is None else ref.pane_id,
        "tmux_session": None if ref is None else ref.session_name,
    }


# ----------------------------------------------------------------------------- the cursor


def _lists(delta: Any) -> tuple[list, list, list, list, list, list]:
    return (
        delta.turns,
        delta.replies,
        delta.tool_calls,
        delta.subagents,
        delta.command_outputs,
        delta.deliveries,
    )


def _identity(index: int, item: Any) -> str:
    """What the digest covers for one object of list ``index``, as yielded at that moment."""
    if index == 0:
        return item.id
    if index == 1:
        return item.record_uuids[-1]
    if index == 2:
        return f"{item.uuid}|{item.result.uuid if item.result else ''}"
    if index == 3:
        return item.agent_id
    return item.uuid


class _Position:
    """How many objects of each list a reader has been given, and a digest of them."""

    def __init__(self) -> None:
        self.counts = [0] * len(_LISTS)
        self._hashes = [hashlib.sha256() for _ in _LISTS]

    def feed(self, lists: Iterable[list]) -> None:
        for index, items in enumerate(lists):
            for item in items:
                self.counts[index] += 1
                self._hashes[index].update(_identity(index, item).encode("utf-8") + b"\0")

    @property
    def digest(self) -> str:
        whole = hashlib.sha256()
        for part in self._hashes:
            whole.update(part.digest())
        return whole.hexdigest()[:12]

    @property
    def cursor(self) -> str:
        return ".".join(str(count) for count in self.counts) + "." + self.digest


@dataclass(frozen=True)
class Cursor:
    counts: tuple[int, ...]
    digest: str


def parse_cursor(text: str | None, *, refuse: bool = True) -> Cursor | None:
    """A cursor from a request; an unusable one is refused, or ignored when ``refuse`` is off.

    ``Last-Event-ID`` is read with ``refuse`` off, since a browser sends whatever it was given.
    """
    if text is None or text == "":
        return None
    match = _CURSOR.match(text)
    if match is None:
        if not refuse:
            return None
        raise Refusal(
            400,
            f"{text!r} is not a cursor this server issued; pass the `cursor` of an earlier "
            "response, or drop ?cursor to read the session from its start",
        )
    *counts, digest = match.groups()
    return Cursor(tuple(int(count) for count in counts), digest)


def objects_json(lists: Iterable[list]) -> dict[str, list]:
    """The lists as JSON, each object once at its latest version and in first-seen order."""
    turns, replies, calls, subagents, outputs, deliveries = lists
    return {
        "turns": [_turn_json(item) for item in _latest(turns, lambda t: t.id)],
        "replies": [_reply_json(item) for item in _latest(replies, lambda r: r.message_id)],
        "tool_calls": [_tool_call_json(item) for item in _latest(calls, lambda c: c.call_id)],
        "subagents": [_subagent_json(item) for item in _latest(subagents, lambda s: s.agent_id)],
        "command_outputs": [_command_output_json(item) for item in outputs],
        "deliveries": [_delivery_json(item) for item in sorted(deliveries, key=lambda d: d.at)],
    }


def _latest(items: list, key: Callable[[Any], str]) -> list:
    by_key: dict[str, Any] = {}
    for item in items:
        by_key[key(item)] = item
    return list(by_key.values())


def past_cursor(delta: Any, given: Cursor | None) -> tuple[tuple[list, ...], str, bool]:
    """What ``delta``, a whole read, holds past ``given``: its lists, the new cursor, reset."""
    lists = _lists(delta)
    whole = _Position()
    whole.feed(lists)
    if given is None:
        return lists, whole.cursor, False
    prefix = _Position()
    if all(count <= len(items) for count, items in zip(given.counts, lists, strict=True)):
        prefix.feed(items[:count] for count, items in zip(given.counts, lists, strict=True))
    if prefix.digest != given.digest or tuple(prefix.counts) != given.counts:
        return lists, whole.cursor, True
    return (
        tuple(items[count:] for count, items in zip(given.counts, lists, strict=True)),
        whole.cursor,
        False,
    )


# ---------------------------------------------------------------------- one project's reads


class ProjectSessions:
    """One served project's sessions: the opt-in, the ownership check and the reads."""

    def __init__(self, root: str, environ: dict[str, str]) -> None:
        self.root = root
        self.environ = environ
        self.paths: ProjectPaths = project_paths(root, environ)
        self.config: Config = _config_of(self.paths)
        if not transcripts_opted_in(self.config):
            raise Refusal(403, session_layer.NOT_OPTED_IN, setting=READ_TRANSCRIPTS_KEY)

    def require(self, session_id: str) -> None:
        """Refuse a session this project's ``sessions.json`` does not record."""
        recorded = read_sessions(self.paths.sessions).entries
        if not any(entry.get("session_id") == session_id for entry in recorded):
            raise Refusal(
                404,
                f"session {session_id} is not recorded for this project in "
                f"{self.paths.sessions}; GET {API_PREFIX}/projects/{{project}}/sessions lists "
                "the sessions it serves",
            )

    def open_live(self, session_id: str) -> session_layer.LiveSession:
        """The layer's view of a session this project records, or a refusal."""
        try:
            return session_layer.open_session(
                self.config, self.paths.store_root, session_id, environ=self.environ
            )
        except session_layer.SessionNotFound as exc:
            raise Refusal(404, str(exc)) from exc
        except KyoseiError as exc:
            raise Refusal(400, str(exc)) from exc

    # ------------------------------------------------------------------------- the routes

    def list_sessions(self) -> dict:
        file = read_sessions(self.paths.sessions)
        rows = [self._row(entry) for entry in file.entries]
        rows.sort(key=lambda row: not row["live"])  # stable: newest first within each
        return {"ok": True, "sessions": rows, "problem": file.problem}

    def _row(self, entry: dict) -> dict:
        row = {
            "session_id": entry.get("session_id"),
            "title": self._title(entry),
            "state": entry.get("state"),
            "live": False,
            "first_seen": entry.get("first_seen"),
            "last_seen": entry.get("last_seen"),
            "ended_at": entry.get("ended_at"),
            "cwd": entry.get("cwd"),
            "remote_control_url": entry.get("url"),
            "has_pane": False,
            "pane_why": "this session has ended",
            "work_running": False,
            "screen_state": None,
            "background": None,
        }
        if entry.get("state") == STATE_ENDED:
            return row
        row["work_running"] = None
        try:
            live = self.open_live(str(entry.get("session_id")))
            row["live"] = live.process_running
            row["has_pane"] = live.pane is not None
            row["pane_why"] = live.pane_lookup.why
            screen = live.screen()
        except (Refusal, KyoseiError, OSError, subprocess.SubprocessError) as exc:
            row["pane_why"] = str(exc)
            return row
        row["has_pane"] = live.pane is not None
        if screen is not None:
            row["work_running"] = session_layer.is_working(screen)
            row["screen_state"] = screen.state
            row["background"] = _background_json(screen.background)
        return row

    def _title(self, entry: dict) -> str | None:
        """The transcript's title for a row; None when it holds none or cannot be read."""
        try:
            return session_layer.session_title(
                self.config,
                self.paths.store_root,
                str(entry.get("session_id")),
                environ=self.environ,
            )
        except (KyoseiError, OSError):
            return None

    def turns(self, session_id: str, given: Cursor | None) -> dict:
        self.require(session_id)
        live = self.open_live(session_id)
        try:
            delta = live.transcript.read()
        except OSError as exc:
            raise _unreadable(session_id, exc) from exc
        lists, cursor, reset = past_cursor(delta, given)
        return {
            "ok": True,
            "session_id": session_id,
            "cursor": cursor,
            "reset": reset,
            "readable": live.transcript.readable,
            "why": live.transcript.why,
            "facts": _facts_json(live.facts()),
            **objects_json(lists),
        }

    def image(self, session_id: str, turn_id: str, index: int) -> tuple[str, bytes]:
        """The media type and bytes of one image the person sent."""
        self.require(session_id)
        live = self.open_live(session_id)
        try:
            found = live.image(turn_id, index)
        except OSError as exc:
            raise _unreadable(session_id, exc) from exc
        if found is None:
            raise Refusal(
                404,
                f"session {session_id} holds no image {index} on a turn {turn_id}; the `images` "
                f"of a turn from GET {API_PREFIX}/projects/{{project}}/sessions/{session_id}/turns "
                "name those it holds",
            )
        return found.media_type, found.data

    def subagent(self, session_id: str, agent_id: str) -> dict:
        """One subagent's tool calls, read from its own transcript, and its dispatch's lines.

        The lines are here rather than on the ``subagents`` of ``/turns`` because they cost a
        store read and git commands per subagent. That list is read for every subagent of a
        session at once, and carried by a cursor that re-sends an object only when the
        transcript changes it, which a branch moving does not.
        """
        self.require(session_id)
        live = self.open_live(session_id)
        try:
            reader = live.subagent(agent_id)
            named = None if reader is None else live.subagent_dispatch(agent_id)
        except OSError as exc:
            raise _unreadable(session_id, exc) from exc
        if reader is None:
            raise Refusal(
                404,
                f"session {session_id} holds no subagent {agent_id}; the `subagents` of "
                f"GET {API_PREFIX}/projects/{{project}}/sessions/{session_id}/turns name those "
                "it holds",
            )
        dispatch, dispatch_why = self._dispatch_lines(named)
        return {
            "ok": True,
            "session_id": session_id,
            "agent_id": agent_id,
            "readable": reader.readable,
            "why": reader.why,
            "dispatch": dispatch,
            "dispatch_why": dispatch_why,
            "tool_calls": [_tool_call_json(call) for call in reader.tool_calls],
        }

    def _dispatch_lines(self, dispatch_id: str | None) -> tuple[dict | None, str | None]:
        """The dispatch's record, branch and line counts, or None and why there are none.

        A store or a repository that cannot be read leaves the subagent's tool calls
        readable, so it is stated here rather than refusing the route.
        """
        if dispatch_id is None:
            return None, "no dispatch names this subagent"
        try:
            store = open_store(
                self.paths.store_root, code_root=self.paths.code_root, read_only=True
            )
            try:
                row = store.dispatch(dispatch_id)
            finally:
                store.close()
            branch = row["branch"]
            lines = (
                None
                if not branch
                else session_layer.branch_lines(
                    self.paths.code_root, row["worked_from_sha"], branch, environ=self.environ
                )
            )
        except NotFound:
            return None, f"this project's store holds no dispatch {dispatch_id}"
        except (KyoseiError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
            return None, f"dispatch {dispatch_id} could not be read: {exc}"
        if lines is None:
            return None, f"dispatch {dispatch_id}'s branch or base commit is not in the repository"
        added, removed = lines
        return {
            "id": dispatch_id,
            "record": row["record_id"],
            "branch": branch,
            "added": added,
            "removed": removed,
        }, None

    def changes(self, session_id: str) -> dict:
        """What the session's working directory holds that no commit does."""
        self.require(session_id)
        entries = read_sessions(self.paths.sessions).entries
        cwd = next(e.get("cwd") for e in entries if e.get("session_id") == session_id)
        try:
            found = session_layer.uncommitted_changes(
                cwd if isinstance(cwd, str) else None, environ=self.environ
            )
        except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
            raise Refusal(
                502, f"the changes of session {session_id} could not be read: {exc}"
            ) from exc
        return {"ok": True, "session_id": session_id, **_changes_json(found)}

    def screen(self, session_id: str) -> dict:
        self.require(session_id)
        live = self.open_live(session_id)
        try:
            view = live.view()
        except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
            raise Refusal(
                502, f"the pane of session {session_id} could not be read: {exc}"
            ) from exc
        screen = None if view is None else view.screen
        return {
            "ok": True,
            "session_id": session_id,
            "pane": _pane_json(live.pane_lookup),
            "screen": _screen_json(screen),
            "terminal": _terminal_json(None if view is None else view.terminal),
            "work_running": None if screen is None else session_layer.is_working(screen),
        }


def _config_of(paths: ProjectPaths) -> Config:
    try:
        return load_config(paths.store_root)
    except KyoseiError as exc:
        raise Refusal(500, f"this project's configuration could not be read: {exc}") from exc


def _unreadable(session_id: str, exc: OSError) -> Refusal:
    return Refusal(500, f"the transcript of session {session_id} could not be read: {exc}")


# ------------------------------------------------------------------------ the shared sampler


class Subscriber:
    """One open stream's queue of events."""

    def __init__(self) -> None:
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=QUEUE_LIMIT)
        self.overflowed = False
        #: Set once the ready event is queued; nothing is sent to this subscriber before that.
        self.joined = False
        self.sampler: _Sampler | None = None

    def put(self, chunk: bytes) -> None:
        try:
            self.queue.put_nowait(chunk)
        except queue.Full:
            self.overflowed = True


class _Sampler:
    """The one reader of a subscribed session, in a thread of its own."""

    def __init__(self, hub: SessionHub, project: ProjectSessions, session_id: str) -> None:
        self.hub = hub
        self.project = project
        self.session_id = session_id
        self.keys = {(project.root, session_id)}
        self.subs: set[Subscriber] = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.ticks = 0
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failure: tuple[int, str] | None = None
        self._live: session_layer.LiveSession | None = None
        self._position = _Position()
        self._screen: dict | None = None
        self._terminal: dict | None = None
        self._reply: str | None = None
        self._facts: dict | None = None
        self._next_follow = 0.0
        self._last_error: str | None = None

    def start_once(self) -> None:
        """Read the baseline and start sampling; a later subscriber waits for the first."""
        with self._start_lock:
            if self._failure is not None:
                raise Refusal(*self._failure)
            if self._thread is not None:
                return
            try:
                self._live = self.project.open_live(self.session_id)
                update = self._live.refresh()
            except Refusal as refusal:
                self._failure = (refusal.status, str(refusal))
                raise
            except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
                self._failure = (500, f"session {self.session_id} could not be read: {exc}")
                raise Refusal(*self._failure) from exc
            self.session_id = update.session_id
            self._position.feed(_lists(update.delta))
            self._screen = _screen_json(update.screen)
            self._terminal = _terminal_json(update.terminal)
            self._reply = update.reply_in_progress
            self._facts = _facts_json(self._live.facts())
            self._next_follow = time.monotonic() + PANELESS_FOLLOW_SECONDS
            self._thread = threading.Thread(target=self._run, name=_SAMPLER_THREAD, daemon=True)
            self._thread.start()

    def join(self, sub: Subscriber) -> None:
        """Add ``sub`` with the session's current state as its first event."""
        with self.lock:
            sub.put(self._ready())
            sub.joined = True

    def _ready(self) -> bytes:
        live = self._live
        assert live is not None
        return frame(
            "ready",
            {
                "session_id": self.session_id,
                "cursor": self._position.cursor,
                "pane": _pane_json(live.pane_lookup),
                "screen": self._screen,
                "terminal": self._terminal,
                "reply_in_progress": self._reply,
                "facts": self._facts,
            },
            event_id=self._position.cursor,
        )

    def _broadcast(self, chunk: bytes) -> None:
        with self.lock:
            for sub in self.subs:
                if sub.joined:
                    sub.put(chunk)

    def _run(self) -> None:
        while not self.stop.wait(self.hub.sample_seconds()):
            if self.hub.stopping.is_set():
                return
            self.ticks += 1
            try:
                self._tick()
                self._last_error = None
            except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
                message = f"session {self.session_id} could not be sampled: {exc}"
                if message != self._last_error:
                    self._last_error = message
                    self._broadcast(frame("error", {"error": message}))

    def _tick(self) -> None:
        live = self._live
        assert live is not None
        previous_id, previous_offset = self.session_id, live.transcript.offset
        if live.pane is not None or time.monotonic() >= self._next_follow:
            self._next_follow = time.monotonic() + PANELESS_FOLLOW_SECONDS
            update = live.refresh()
            lists, screen, words, reply, followed = (
                _lists(update.delta),
                update.screen,
                update.terminal,
                update.reply_in_progress,
                update.followed_clear,
            )
        else:
            lists, screen, words, reply, followed = (
                _lists(live.transcript.read()),
                None,
                None,
                None,
                False,
            )
        self.session_id = live.session_id
        if followed:
            self._position = _Position()
            self.hub.alias(self, self.session_id)
            self._broadcast(
                frame(
                    "session",
                    {
                        "session_id": self.session_id,
                        "previous_session_id": previous_id,
                        "followed_clear": True,
                    },
                )
            )
        elif live.transcript.offset < previous_offset:
            self._position = _Position()
            self._broadcast(frame("reset", {"session_id": self.session_id}))
        self._position.feed(lists)
        if any(lists):
            cursor = self._position.cursor
            self._broadcast(
                frame(
                    "transcript",
                    {"session_id": self.session_id, "cursor": cursor, **objects_json(lists)},
                    event_id=cursor,
                )
            )
        # A permission-mode row is none of the lists, and a change of mode has to reach the strip.
        held_mode = (self._facts or {}).get("permission_mode")
        if any(lists) or followed or live.transcript.latest_permission_mode != held_mode:
            now_facts = _facts_json(live.facts())
            if now_facts != self._facts:
                self._facts = now_facts
                self._broadcast(frame("facts", {"session_id": self.session_id, "facts": now_facts}))
        now_screen = _screen_json(screen)
        if now_screen != self._screen:
            self._screen = now_screen
            self._broadcast(frame("screen", {"session_id": self.session_id, "screen": now_screen}))
        now_terminal = _terminal_json(words)
        if now_terminal != self._terminal:
            self._terminal = now_terminal
            self._broadcast(
                frame("terminal", {"session_id": self.session_id, "terminal": now_terminal})
            )
        if reply != self._reply:
            self._reply = reply
            self._broadcast(frame("reply", {"session_id": self.session_id, "text": reply}))


class SessionHub:
    """The server's samplers: one per subscribed session, however many clients subscribe."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.stopping: threading.Event = server.stopping
        self._lock = threading.Lock()
        self._samplers: dict[tuple[str, str], _Sampler] = {}

    def sample_seconds(self) -> float:
        return self.server.session_sample_seconds

    def subscribe(self, project: ProjectSessions, session_id: str) -> Subscriber:
        """A subscriber to ``session_id``, or the refusal that session deserves.

        Must be matched by :meth:`unsubscribe`, which stops the sampler on the last one.
        """
        project.require(session_id)
        sub = Subscriber()
        with self._lock:
            key = (project.root, session_id)
            sampler = self._samplers.get(key)
            if sampler is None:
                sampler = _Sampler(self, project, session_id)
                self._samplers[key] = sampler
            sub.sampler = sampler
            with sampler.lock:
                sampler.subs.add(sub)  # held so the sampler is not stopped while it starts
        try:
            sampler.start_once()
        except Refusal:
            self.unsubscribe(sub)
            raise
        sampler.join(sub)
        return sub

    def alias(self, sampler: _Sampler, session_id: str) -> None:
        """A session that moved on ``/clear``: a subscriber to its new id joins this sampler."""
        with self._lock:
            key = (sampler.project.root, session_id)
            self._samplers[key] = sampler
            sampler.keys.add(key)

    def unsubscribe(self, sub: Subscriber) -> None:
        sampler = sub.sampler
        if sampler is None:
            return
        with self._lock:
            with sampler.lock:
                sampler.subs.discard(sub)
                last = not sampler.subs
            if last:
                for key in sampler.keys:
                    if self._samplers.get(key) is sampler:
                        del self._samplers[key]
                sampler.stop.set()

    def samplers(self) -> list[_Sampler]:
        """The running samplers, each once (for ``kyo serve``'s own tests)."""
        with self._lock:
            return list({id(s): s for s in self._samplers.values()}.values())


def stream(
    project: ProjectSessions,
    session_id: str,
    sub: Subscriber,
    given: Cursor | None,
    write: Callable[[bytes], None],
    stopping: threading.Event,
    client_gone: Callable[[], bool],
    *,
    keepalive_seconds: float,
) -> None:
    """Write ``sub``'s events to ``write`` until the client leaves or the server stops.

    A cursor is honoured from a fresh read of the transcript, since the sampler keeps only
    the session's position. Objects read there can also arrive from the sampler, and a client
    keys every object by its id, so a repeat replaces an object with itself.
    """
    idle = 0.0
    try:
        write(OPENING)
        if given is not None:
            _catch_up(project, session_id, given, write)
        while not stopping.is_set() and not sub.overflowed and not client_gone():
            try:
                chunk = sub.queue.get(timeout=_CLIENT_CHECK_SECONDS)
            except queue.Empty:
                idle += _CLIENT_CHECK_SECONDS
                if idle >= keepalive_seconds:
                    write(KEEPALIVE)
                    idle = 0.0
                continue
            write(chunk)
            idle = 0.0
    except OSError:  # the client went away: a broken pipe or a reset
        return


def _catch_up(
    project: ProjectSessions, session_id: str, given: Cursor, write: Callable[[bytes], None]
) -> None:
    """Send what the transcript holds past ``given``."""
    try:
        document = project.turns(session_id, given)
    except Refusal as refusal:
        write(frame("error", {"error": str(refusal)}))
        return
    write(frame("transcript", document, event_id=document["cursor"]))
