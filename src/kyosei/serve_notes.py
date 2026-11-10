"""``kyo serve``'s note routes: file, find, tag and send notes, and send a marked prompt
(KYO-706, chunk E5; KYO-709).

Under ``/v1/projects/{project}/``, each carrying a device token holding the ``notes``
permission (:mod:`kyosei.device_tokens`):

- ``POST notes`` -- ``{"text", "anchors", "tags", "entered"}`` files a note;
- ``GET notes?tag=&on=&anchored-to=&session=&person=&sent=`` -- the notes matching all the
  filters, ``sent`` being ``true`` or ``false``;
- ``GET notes/{id}`` -- one note;
- ``POST notes/{id}/tags`` -- ``{"add": [...], "remove": [...]}`` moves its tags;
- ``POST sessions/{id}/send-notes`` -- ``{"notes": [ids]}`` sends them as one message, which
  also needs the ``send`` permission and ``serve_send: true``;
- ``POST sessions/{id}/send-prompt`` -- ``{"segments": [...], "marks": [...]}`` sends one
  composer prompt with its marks in place (below). It needs the ``send`` permission, and the
  ``notes`` permission as well when ``marks`` is not empty.

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

A PROMPT WITH MARKS (KYO-709, decided by KYO-708). One composer box is one prompt, and a mark
beside one of its lines holds that line's anchors and tags. ``segments`` is what ``POST held``
takes, and each mark is ``{"line": n, "anchors": [...], "tags": [...]}``, ``line`` counting
the lines of the segments' joined text from 0, one mark to a line. The message is that text
unchanged, with the ``on`` lines of a marked line (the form above) typed directly above it:

    the transcript does not stream
    on your reply 8c1f...e2 "the transcript does not stream; the..."
    why don't we just reformat it ourselves?

A marked line with no text is sent as its ``on`` lines alone. There is no ``[kyo-message]``
header and no ``Note N`` heading, and a prompt without marks is the typed text exactly.

Its anchors are validated when it is held, so a bad one is refused and nothing is held. Its
notes are filed only when it is delivered, one for each marked line, with the line's text,
anchors and tags, the token's device, ``entered`` as ``pasted`` for a line wholly inside a
pasted segment and ``typed`` otherwise, and the sent state from the start. A retracted
prompt files nothing, and the held message keeps the prompt's own segments and marks
(``prompt``) so that a composer can restore them.

THE HELD PATH. A set of notes, or a prompt, is sent only through
:meth:`kyosei.serve_held.HeldMessages.hold`, so a message during a turn waits for the turn,
and every check a send makes applies. What a delivered message files -- a set of notes'
sent state, a prompt's notes -- is written by :func:`file_delivered` once the keys have been
sent and not before, so a refused or retracted message files nothing. A failure there leaves
the message delivered and its filing kept to be tried again (:mod:`kyosei.serve_held`).
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

#: What a served write records as its actor, before the device's name.
ACTOR_PREFIX = "kyo-serve:"

_NOTE_KEYS = frozenset({"text", "anchors", "tags", "entered"})
_TAG_KEYS = frozenset({"add", "remove"})
_SEND_KEYS = frozenset({"notes"})
_PROMPT_KEYS = frozenset({"segments", "marks"})
_MARK_KEYS = frozenset({"line", "anchors", "tags"})

#: The filters of ``GET notes``. Only ``tag`` may repeat.
_FILTERS = ("tag", "on", "anchored-to", "session", "person", "sent")
_SENT_VALUES = {"true": True, "false": False}


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
            anchors_of=lambda store: _validated(ctx, store, anchors, "anchors"),
            device=device.name,
            entered=entered,
        )
    return {"ok": True, "note": note}


def _validated(ctx: commands.Context, store: Any, anchors: list, where: str) -> list[dict]:
    """The anchors as rows to store; a turn by message may become several (KYO-709)."""
    rows: list[dict] = []
    for index, raw in enumerate(anchors):
        rows += cmd_notes.validate_anchors(ctx, store, raw, f"{where}[{index}]")
    return rows


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
    sent = single.get("sent")
    if sent is not None and sent not in _SENT_VALUES:
        raise serve_send.bad_request("?sent must be true or false")
    with _context(server, root_of(), None, read_only=True) as ctx:
        store = ctx.store()
        note_ids = cmd_notes.find_note_ids(
            store,
            tags=[cmd_notes.normalise_tag(tag) for tag in given.get("tag", [])],
            target=single.get("on"),
            anchored_to=anchored_to,
            person=single.get("person"),
            session=single.get("session"),
            sent=None if sent is None else _SENT_VALUES[sent],
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


def _records_of(ctx: commands.Context, anchors: list[dict]) -> dict[str, dict]:
    """The transcript records these anchors' turns address, read once per session."""
    wanted: dict[str, set[str]] = {}
    for anchor in anchors:
        if anchor["kind"] == cmd_notes.ANCHOR_TURN:
            wanted.setdefault(anchor["session"], set()).add(anchor["target"])
    records: dict[str, dict] = {}
    for session, uuids in wanted.items():
        records.update(cmd_notes.read_records(ctx, session, uuids))
    return records


def _merged(pieces: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Pieces as send segments, neighbours of one kind joined and empty pieces dropped."""
    merged: list[dict[str, str]] = []
    for kind, text in pieces:
        if not text:
            continue
        if merged and merged[-1]["kind"] == kind:
            merged[-1]["text"] += text
        else:
            merged.append({"kind": kind, "text": text})
    return merged


def compose(
    ctx: commands.Context, notes: list[dict], session_id: str
) -> list[dict[str, str]]:
    """The message for these notes, as send segments (module docstring)."""
    records = _records_of(ctx, [anchor for note in notes for anchor in note["anchors"]])
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
    return _merged(pieces)


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


# ------------------------------------------------------------------ sending a prompt


def _lines_of(segments: list[dict[str, str]]) -> list[tuple[int, int, str]]:
    """Each line of the segments' joined text as (start, end, how it was entered)."""
    spans: list[tuple[int, int]] = []
    position = 0
    for segment in segments:
        end = position + len(segment["text"])
        if segment["kind"] == serve_send.PASTED:
            spans.append((position, end))
        position = end
    text = "".join(segment["text"] for segment in segments)
    lines: list[tuple[int, int, str]] = []
    start = 0
    for line in text.split("\n"):
        end = start + len(line)
        inside = any(low <= start and end <= high and start < high for low, high in spans)
        lines.append((start, end, serve_send.PASTED if inside else serve_send.TYPED))
        start = end + 1
    return lines


def _marks(document: dict, line_count: int) -> list[dict]:
    """The body's marks, checked, each refusal naming the mark it is about."""
    raw = document.get("marks", [])
    if not isinstance(raw, list):
        raise serve_send.bad_request('"marks" must be a list of {"line", "anchors", "tags"}')
    marks: list[dict] = []
    seen: dict[int, int] = {}
    for index, mark in enumerate(raw):
        where = f"marks[{index}]"
        if not isinstance(mark, dict):
            raise serve_send.bad_request(f'{where} must be {{"line", "anchors", "tags"}}')
        extra = sorted(set(mark) - _MARK_KEYS)
        if extra:
            raise serve_send.bad_request(
                f"{where} takes no {', '.join(repr(key) for key in extra)}; a mark takes "
                f"{', '.join(sorted(_MARK_KEYS))}"
            )
        line = mark.get("line")
        if isinstance(line, bool) or not isinstance(line, int):
            raise serve_send.bad_request(f'{where}: "line" must be an integer')
        if not 0 <= line < line_count:
            raise serve_send.bad_request(
                f"{where}: line {line} is outside the text, whose lines are 0 to "
                f"{line_count - 1}, counted from 0"
            )
        if line in seen:
            raise serve_send.bad_request(
                f"{where} marks line {line}, which marks[{seen[line]}] already marks; a line "
                "has one mark, holding all its anchors and tags"
            )
        seen[line] = index
        anchors = mark.get("anchors", [])
        if not isinstance(anchors, list):
            raise serve_send.bad_request(f'{where}: "anchors" must be a list of anchors')
        tags = mark.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise serve_send.bad_request(f'{where}: "tags" must be a list of strings')
        try:
            normalised = sorted({cmd_notes.normalise_tag(tag) for tag in tags})
        except UsageError as exc:
            raise serve_send.bad_request(f"{where}: {exc}") from exc
        if not anchors and not normalised:
            raise serve_send.bad_request(
                f"{where} holds no anchor and no tag, so there is nothing to mark; leave it out"
            )
        marks.append({"line": line, "anchors": anchors, "tags": normalised, "given": mark})
    return marks


def _with_marks(
    segments: list[dict[str, str]], insertions: dict[int, str]
) -> list[dict[str, str]]:
    """The segments with typed text inserted at these offsets of their joined text."""
    pieces: list[tuple[str, str]] = []
    position = 0
    pending = sorted(insertions.items())
    for segment in segments:
        text, kind = segment["text"], segment["kind"]
        end = position + len(text)
        cut = 0
        while pending and pending[0][0] < end:
            offset, typed = pending.pop(0)
            pieces.append((kind, text[cut : offset - position]))
            pieces.append((serve_send.TYPED, typed))
            cut = offset - position
        pieces.append((kind, text[cut:]))
        position = end
    pieces += [(serve_send.TYPED, typed) for _, typed in pending]
    return _merged(pieces)


def _prompt_message(
    ctx: commands.Context,
    segments: list[dict[str, str]],
    marks: list[dict],
    session_id: str,
) -> tuple[list[dict[str, str]], list[dict]]:
    """The message a prompt sends, and what each marked line files once it is delivered."""
    lines = _lines_of(segments)
    text = "".join(segment["text"] for segment in segments)
    records = _records_of(ctx, [anchor for mark in marks for anchor in mark["rows"]])
    insertions: dict[int, str] = {}
    filing: list[dict] = []
    for mark in sorted(marks, key=lambda m: m["line"]):
        start, end, entered = lines[mark["line"]]
        on = [_anchor_line(ctx, anchor, records, session_id) for anchor in mark["rows"]]
        if on:
            # An empty line's own line break follows, so it becomes its `on` lines alone.
            insertions[start] = "\n".join(on) + ("\n" if end > start else "")
        filing.append(
            {
                "line": mark["line"],
                "text": text[start:end],
                "anchors": mark["rows"],
                "tags": mark["tags"],
                "entered": entered,
            }
        )
    return (_with_marks(segments, insertions) if insertions else segments), filing


def send_prompt(
    server: Any,
    project_name: str,
    root_of: Callable[[], str],
    session_id: str,
    authorization: str | None,
    read_body: Callable[[], bytes],
) -> dict[str, Any]:
    """Send one composer prompt with its marks in place, through the held path."""
    body = serve_send.Body(read_body)
    try:
        serve_send.require_device(server.environ, authorization, device_tokens.PERMISSION_SEND)
        document = serve_send.json_object(body.read())
    except Refusal:
        body.drain()
        raise
    extra = sorted(set(document) - _PROMPT_KEYS)
    if extra:
        raise serve_send.bad_request(
            f"this route does not take {', '.join(repr(key) for key in extra)}; it takes "
            f"{', '.join(sorted(_PROMPT_KEYS))}"
        )
    segments = serve_send.message_segments(document)
    marks = _marks(document, len(_lines_of(segments)))
    filing: list[dict] = []
    if marks:
        # A mark becomes a note, so marking needs what filing a note needs.
        _device(server, authorization)
        with _context(server, root_of(), None, read_only=True) as ctx:
            store = ctx.store()
            for index, mark in enumerate(marks):
                try:
                    mark["rows"] = _validated(
                        ctx, store, mark["anchors"], f"marks[{index}].anchors"
                    )
                except (UsageError, NotFound) as exc:
                    # A mark is part of the body, so an anchor that names nothing is a
                    # malformed body here rather than a missing note or record.
                    raise serve_send.bad_request(str(exc)) from exc
            segments_sent, filing = _prompt_message(ctx, segments, marks, session_id)
    else:
        segments_sent = segments
    prompt = {
        "segments": segments,
        "marks": [mark["given"] for mark in marks],
        "filing": filing,
    }
    answer = server.held_messages.hold(
        project_name,
        root_of,
        session_id,
        authorization,
        lambda: json.dumps({"segments": segments_sent}).encode("utf-8"),
        prompt=prompt,
    )
    return {**answer, "message": "".join(segment["text"] for segment in segments_sent)}


# ------------------------------------------------------------------- after delivery


def _filed_already(ctx: commands.Context, message_id: str) -> bool:
    """Whether a prompt's notes are in the store: they carry its id as their sent message."""
    row = ctx.store().conn.execute(
        "SELECT 1 FROM person_notes WHERE sent_message = ? LIMIT 1", (message_id,)
    ).fetchone()
    return row is not None


def file_delivered(server: Any, message: dict[str, Any]) -> str | None:
    """File what a delivered message files; None when it is done, or why it is not.

    Called once the keys have been sent, and again by :meth:`kyosei.serve_held.HeldMessages
    .resume` for a message whose filing failed. A set of notes gets its sent state, and a
    prompt its notes, sent from the start. Either can be run again: a note already sent
    keeps its state, and a prompt whose notes are filed is not filed twice.
    """
    note_ids = message.get("notes") or []
    filing = (message.get("prompt") or {}).get("filing") or []
    if not note_ids and not filing:
        return None
    sent = {
        "at": message.get("delivered_at") or message["at"],
        "session": message["session_id"],
        "message": message["id"],
    }
    device = message["device"]["name"]
    try:
        root = server.project(message["project"]).root
        with _context(server, root, f"{ACTOR_PREFIX}{device}", read_only=False) as ctx:
            if note_ids:
                cmd_notes.mark_sent(ctx, note_ids, **sent)
            if filing and not _filed_already(ctx, message["id"]):
                cmd_notes.file_notes(
                    ctx,
                    [
                        {
                            "text": item["text"],
                            "tags": item["tags"],
                            "anchors_of": lambda _store, rows=item["anchors"]: rows,
                            "device": device,
                            "entered": item["entered"],
                            "sent": sent,
                        }
                        for item in filing
                    ],
                )
    except (Refusal, KyoseiError, OSError, sqlite3.Error) as exc:
        return str(exc)
    return None
