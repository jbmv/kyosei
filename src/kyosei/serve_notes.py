"""``kyo serve``'s note routes: file, find, tag and send notes (KYO-706, chunk E5).

Under ``/v1/projects/{project}/``, each carrying a device token holding the ``notes``
permission (:mod:`kyosei.device_tokens`):

- ``POST notes`` -- ``{"text", "anchors", "tags", "entered"}`` files a note;
- ``GET notes?tag=&on=&anchored-to=&session=&person=`` -- the notes matching all the filters;
- ``GET notes/{id}`` -- one note;
- ``POST notes/{id}/tags`` -- ``{"add": [...], "remove": [...]}`` moves its tags;
- ``POST sessions/{id}/send-notes`` -- ``{"notes": [ids]}`` sends them as one message, which
  also needs the ``send`` permission and ``serve_send: true``.

The note's device is the name its token was issued to, and a body cannot name another: a
note is evidence of who wrote it, and the token is what authenticates that.

HOW A WRITE REACHES THE STORE. Every other served command runs on a store opened read-only,
under the server's one lock, and that stays true. Each note write here opens its own writable
connection for that one request and closes it, and goes through :func:`kyosei.cmd_notes.file_note`
or :func:`kyosei.cmd_notes.change_tags`, which ``kyo notes`` also calls. So there is one lock
against the CLI and no second one: SQLite's write lock (``BEGIN IMMEDIATE``, taken by
:meth:`kyosei.store.Store.transaction`), and the export directory's ``flock`` for the ledger
lines. A writer holding either makes this one wait as it would make the CLI wait, up to
``BUSY_TIMEOUT_MS``, and a write that gives up is refused by name (``store-locked``).

The server's ``lock`` is not taken: it guards the streams :func:`kyosei.cli.run` redirects and
the prose collector, neither of which these routes touch, and holding it through a wait for
the store would stall every read. A connection is never shared between threads.

THE MESSAGE. :func:`compose` writes a fixed plain-text form, stated in the operating reference
(``docs/reference/topics/serve-and-desktop.md``) so that the orchestrator skill can teach it:

    [kyo-message, 2 notes]

    Note 1 (KYO-N41)
    on your reply 8c1f...e2 "the transcript does not stream; the..."
    why don't we just reformat it ourselves?

    Note 2 (KYO-N42)
    on KYO-12.g1
    this looks right on the iPad, approved

A span's words are the first :data:`ANCHOR_WORDS` words and no more, read from the transcript
when the message is made, because no transcript text is stored and the agent cannot see
transcript ids. An agent that needs the exact span looks it up by id. The person's words are
not re-wrapped or prefixed, so that a transcript check still finds them.

THE HELD PATH. A set of notes is sent only through :meth:`kyosei.serve_held.HeldMessages.hold`,
so a message during a turn waits for the turn, and every check a send makes applies. The notes
get the :data:`SENT_TAG` tag when the message is delivered and not before, so a refused or
retracted message leaves them as they were.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from typing import Any

from . import cmd_notes, commands, device_tokens, serve_send
from .errors import KyoseiError, NotFound, UsageError
from .export import person_note_document
from .serve import Refusal
from .store import is_lock_contention, lock_contention_message

#: How many words of a span a message carries.
ANCHOR_WORDS = 6

#: A span with one very long word stays short all the same.
ANCHOR_CHARS = 120

#: The tag a note gets when the message holding it has been delivered.
SENT_TAG = "sent"

#: What a served write records as its actor, before the device's name.
ACTOR_PREFIX = "kyo-serve:"

_NOTE_KEYS = frozenset({"text", "anchors", "tags", "entered"})
_TAG_KEYS = frozenset({"add", "remove"})
_SEND_KEYS = frozenset({"notes"})

#: The filters of ``GET notes``. Only ``tag`` may repeat.
_FILTERS = ("tag", "on", "anchored-to", "session", "person")


# ------------------------------------------------------------------------------ plumbing


@contextlib.contextmanager
def _refusing() -> Iterator[None]:
    """What the store and the note path raise, as refusals."""
    try:
        yield
    except NotFound as exc:
        raise Refusal(404, str(exc), reason="not-found") from exc
    except UsageError as exc:
        raise serve_send.bad_request(str(exc)) from exc
    except KyoseiError as exc:
        raise Refusal(422, str(exc), reason="store-refused") from exc
    except sqlite3.OperationalError as exc:
        if is_lock_contention(exc):
            raise Refusal(
                503, lock_contention_message(None), reason="store-locked"
            ) from exc
        raise


@contextlib.contextmanager
def _context(
    server: Any, root: str, actor: str | None, *, read_only: bool
) -> Iterator[commands.Context]:
    """One request's own view of the project's store, closed when the request is done."""
    with _refusing():
        ctx = commands.build_context(
            root,
            actor,
            server.environ,
            ["serve", "notes"],
            project=commands.PROJECT_REQUIRED,
            read_only=read_only,
        )
        try:
            yield ctx
        finally:
            ctx.close()


def _actor(device: device_tokens.Device) -> str:
    return f"{ACTOR_PREFIX}{device.name}"


def _device(server: Any, authorization: str | None) -> device_tokens.Device:
    return serve_send.require_device(
        server.environ, authorization, device_tokens.PERMISSION_NOTES
    )


def _authorized_body(
    server: Any, authorization: str | None, read_body: Callable[[], bytes], keys: frozenset[str]
) -> tuple[device_tokens.Device, dict]:
    """The device and the body, past the token. A body is parsed only for a token accepted."""
    body = serve_send.Body(read_body)
    try:
        device = _device(server, authorization)
        document = serve_send.json_object(body.read())
    except Refusal:
        body.drain()
        raise
    extra = sorted(set(document) - keys)
    if extra:
        raise serve_send.bad_request(
            f"this route does not take {', '.join(repr(key) for key in extra)}; it takes "
            f"{', '.join(sorted(keys))}. A note's device is the name its token was issued to"
        )
    return device, document


def _strings(document: dict, key: str) -> list[str]:
    value = document.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise serve_send.bad_request(f'"{key}" must be a list of strings')
    return value


# ------------------------------------------------------------------------------- filing


def create(
    server: Any,
    root_of: Callable[[], str],
    authorization: str | None,
    read_body: Callable[[], bytes],
) -> dict[str, Any]:
    """File one note."""
    device, document = _authorized_body(server, authorization, read_body, _NOTE_KEYS)
    text = document.get("text", "")
    if not isinstance(text, str):
        raise serve_send.bad_request('"text" must be a string')
    entered = document.get("entered")
    if entered not in cmd_notes.ENTERED:
        raise serve_send.bad_request(
            f'"entered" must be one of {", ".join(cmd_notes.ENTERED)}: how the text reached '
            "kyo is a fact about the note that only the client knows"
        )
    anchors = document.get("anchors", [])
    if not isinstance(anchors, list):
        raise serve_send.bad_request('"anchors" must be a list of anchors')
    with _refusing():
        tags = sorted({cmd_notes.normalise_tag(tag) for tag in _strings(document, "tags")})
    if not text.strip() and not tags:
        raise serve_send.bad_request('a note needs "text" or at least one tag in "tags"')
    with _context(server, root_of(), _actor(device), read_only=False) as ctx:
        note = cmd_notes.file_note(
            ctx,
            text=text,
            tags=tags,
            anchors_of=lambda store: [
                cmd_notes.validate_anchor(ctx, store, raw, f"anchors[{index}]")
                for index, raw in enumerate(anchors)
            ],
            device=device.name,
            entered=entered,
        )
    return {"ok": True, "note": note}


# ------------------------------------------------------------------------------ reading


def find(
    server: Any,
    root_of: Callable[[], str],
    authorization: str | None,
    query: list[tuple[str, str, bool]],
) -> dict[str, Any]:
    """Every note matching all the query's filters, oldest first."""
    _device(server, authorization)
    given: dict[str, list[str]] = {}
    for key, value, _bare in query:
        if key not in _FILTERS:
            raise serve_send.bad_request(
                f"?{key} is not a filter of this route; it takes {', '.join(_FILTERS)}"
            )
        given.setdefault(key, []).append(value)
    for key, values in given.items():
        if key != "tag" and len(values) > 1:
            raise serve_send.bad_request(f"?{key} may be given once")
    single = {key: values[0] for key, values in given.items() if key != "tag"}
    anchored_to = single.get("anchored-to")
    if anchored_to is not None and anchored_to not in cmd_notes.ANCHORED_TO:
        raise serve_send.bad_request(
            f"?anchored-to must be one of {', '.join(sorted(cmd_notes.ANCHORED_TO))}"
        )
    with _context(server, root_of(), None, read_only=True) as ctx:
        store = ctx.store()
        note_ids = cmd_notes.find_note_ids(
            store,
            tags=[cmd_notes.normalise_tag(tag) for tag in given.get("tag", [])],
            target=single.get("on"),
            anchored_to=anchored_to,
            person=single.get("person"),
            session=single.get("session"),
        )
        notes = [person_note_document(store, note_id) for note_id in note_ids]
    return {"ok": True, "count": len(notes), "notes": notes}


def _note(ctx: commands.Context, note_id: str) -> dict:
    store = ctx.store()
    if store.person_note(note_id) is None:
        raise NotFound(f"no note {note_id!r}: GET the notes route to find a note's id")
    return person_note_document(store, note_id)


def read(
    server: Any, root_of: Callable[[], str], authorization: str | None, note_id: str
) -> dict[str, Any]:
    """One note."""
    _device(server, authorization)
    with _context(server, root_of(), None, read_only=True) as ctx:
        return {"ok": True, "note": _note(ctx, note_id)}


# ------------------------------------------------------------------------------ tagging


def tag(
    server: Any,
    root_of: Callable[[], str],
    authorization: str | None,
    note_id: str,
    read_body: Callable[[], bytes],
) -> dict[str, Any]:
    """Add and remove a note's tags. Its text and anchors are fixed once filed."""
    device, document = _authorized_body(server, authorization, read_body, _TAG_KEYS)
    with _refusing():
        add = {cmd_notes.normalise_tag(item) for item in _strings(document, "add")}
        remove = {cmd_notes.normalise_tag(item) for item in _strings(document, "remove")}
    if not add and not remove:
        raise serve_send.bad_request('the body needs a tag in "add" or "remove"')
    both = sorted(add & remove)
    if both:
        raise serve_send.bad_request(
            f'"add" and "remove" both name {", ".join(both)}; name each tag in one of them'
        )
    with _context(server, root_of(), _actor(device), read_only=False) as ctx:
        note, changes, unchanged = cmd_notes.change_tags(ctx, note_id, add=add, remove=remove)
    return {"ok": True, "note": note, "changes": changes, "unchanged": unchanged}


# ------------------------------------------------------------------------------ sending


def _words(span: str) -> str:
    """The first words of a span, quoted: control characters would act as keys in a pane."""
    words = "".join(
        char for char in span if char.isspace() or unicodedata.category(char) != "Cc"
    ).split()
    shown = " ".join(words[:ANCHOR_WORDS])
    cut = len(words) > ANCHOR_WORDS or len(shown) > ANCHOR_CHARS
    return f'"{shown[:ANCHOR_CHARS]}{"..." if cut else ""}"'


def _anchor_line(
    ctx: commands.Context, anchor: dict, records: dict[str, dict], session_id: str
) -> str:
    """One anchor as one line: its id, and the first words of its span if it has one."""
    kind, target = anchor["kind"], anchor["target"]
    if kind == cmd_notes.ANCHOR_RECORD:
        return f"on {target}"
    if kind == cmd_notes.ANCHOR_NOTE:
        return f"on note {target}"
    if kind == cmd_notes.ANCHOR_TURN:
        who = "your reply" if anchor["role"] == cmd_notes.ROLE_AGENT else "my prompt"
        where = "" if anchor["session"] == session_id else f" in session {anchor['session']}"
        line = f"on {who} {target}{where}"
    else:
        span = (
            f"lines {anchor['line_start']}-{anchor['line_end']}"
            if "line_start" in anchor
            else f"chars {anchor['char_start']}-{anchor['char_end']}"
        )
        line = f"on {kind} {target} at {anchor['commit_sha']} {span}"
    words = cmd_notes.span_text(ctx, anchor, records)
    return f"{line} {_words(words) if words else '(words unavailable)'}"


def _refuse_unsendable(note: dict) -> None:
    if note["redacted_at"]:
        raise Refusal(
            409,
            f"note {note['id']} had its text removed by `kyo redact`, so it cannot be sent; "
            "leave it out of the set",
            reason="note-redacted",
            note=note["id"],
        )
    for character in note["text"]:
        if character not in "\n\t" and unicodedata.category(character) == "Cc":
            raise Refusal(
                409,
                f"note {note['id']} holds the control character U+{ord(character):04X}, which "
                "would act as a key in the pane, and a filed note's text is fixed; write "
                "another note without it and send that one",
                reason="note-unsendable",
                note=note["id"],
            )


def compose(
    ctx: commands.Context, notes: list[dict], session_id: str
) -> list[dict[str, str]]:
    """The message for these notes, as send segments (module docstring)."""
    wanted: dict[str, set[str]] = {}
    for note in notes:
        for anchor in note["anchors"]:
            if anchor["kind"] == cmd_notes.ANCHOR_TURN:
                wanted.setdefault(anchor["session"], set()).add(anchor["target"])
    records: dict[str, dict] = {}
    for session, uuids in wanted.items():
        records.update(cmd_notes.read_records(ctx, session, uuids))
    plural = "note" if len(notes) == 1 else "notes"
    pieces = [(serve_send.TYPED, f"[kyo-message, {len(notes)} {plural}]")]
    for index, note in enumerate(notes, start=1):
        lines = [
            f"\n\nNote {index} ({note['id']})",
            *(_anchor_line(ctx, a, records, session_id) for a in note["anchors"]),
        ]
        body = note["text"]
        pieces.append((serve_send.TYPED, "\n".join(lines) + ("\n" if body else "")))
        if body:
            # A typed tab would act as a key, so a body holding one goes as a paste.
            pasted = note["entered"] == "pasted" or "\t" in body
            pieces.append((serve_send.PASTED if pasted else serve_send.TYPED, body))
    merged: list[dict[str, str]] = []
    for kind, text in pieces:
        if merged and merged[-1]["kind"] == kind:
            merged[-1]["text"] += text
        else:
            merged.append({"kind": kind, "text": text})
    return merged


def send(
    server: Any,
    project_name: str,
    root_of: Callable[[], str],
    session_id: str,
    authorization: str | None,
    read_body: Callable[[], bytes],
) -> dict[str, Any]:
    """Send a set of notes as one message, through the held path."""
    _, document = _authorized_body(server, authorization, read_body, _SEND_KEYS)
    note_ids = _strings(document, "notes")
    if not note_ids or len(set(note_ids)) != len(note_ids):
        raise serve_send.bad_request(
            '"notes" must list one or more note ids, each once, in the order they are to be read'
        )
    with _context(server, root_of(), None, read_only=True) as ctx:
        notes = [_note(ctx, note_id) for note_id in note_ids]
        for note in notes:
            _refuse_unsendable(note)
        segments = compose(ctx, notes, session_id)
    answer = server.held_messages.hold(
        project_name,
        root_of,
        session_id,
        authorization,
        lambda: json.dumps({"segments": segments}).encode("utf-8"),
        notes=note_ids,
    )
    return {
        **answer,
        "notes": note_ids,
        "message": "".join(segment["text"] for segment in segments),
    }


def file_as_sent(server: Any, message: dict[str, Any]) -> None:
    """Tag the notes of a delivered message as sent; called once the keys have been sent.

    The agent has the message by now, so a failure here is reported and not raised, and the
    tag can be added by hand.
    """
    note_ids = message.get("notes") or []
    if not note_ids:
        return
    actor = f"{ACTOR_PREFIX}{message['device']['name']}"
    try:
        root = server.project(message["project"]).root
        with _context(server, root, actor, read_only=False) as ctx:
            for note_id in note_ids:
                cmd_notes.change_tags(ctx, note_id, add={SENT_TAG}, remove=set())
    except (Refusal, KyoseiError, OSError, sqlite3.Error) as exc:
        remedy = "; ".join(f"`kyo notes tag {note_id} --add {SENT_TAG}`" for note_id in note_ids)
        server.log_stream.write(
            f"kyo serve: message {message['id']} was delivered, but its notes could not be "
            f"tagged as sent ({exc}); run {remedy}\n"
        )
