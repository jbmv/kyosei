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
  ``notes`` permission as well when a mark holds an anchor or a tag.
- ``GET sessions/{id}/marks`` -- every sent note with a turn anchor in that session, its
  anchors given as a client draws the turns (KYO-710, below), and every note sent to it that
  is anchored to a record or a file (KYO-737, below);
- ``GET tags`` -- each tag now on a note, with how many notes carry it (KYO-710).

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
    on record KYO-12.g1 "the iPad build is checked by..."
    this looks right on the iPad, approved

A span's words are the first :data:`ANCHOR_WORDS` words and no more, read from the transcript
when the message is made, because no transcript text is stored and the agent cannot see
transcript ids. An agent that needs the exact span looks it up by id.

A SHORT SELECTION (KYO-733). A selection of fewer than :data:`ANCHOR_WORDS` words says little
about where it lies, and the uuid does not take an agent to the place, since an agent cannot
see the ids of its own replies. For a turn anchor the line then quotes the sentence that holds
the selection, from that sentence's start and up to :data:`SENTENCE_WORDS` words, and names
the selection after it, unless the selection is the whole sentence:

    on your reply 8c1f...e2 "The count was blank because this repository's..." at "blank"

The sentence ends as a right-click anchor's does (:func:`sentence_at`): at a full stop,
question mark or exclamation mark followed by white space or the end, at a line break, or at
the end of the block. A selection of :data:`ANCHOR_WORDS` words or more is quoted as it
always was. When the block is a heading, a list item, a table cell or a code block, the
quote is followed by ``(heading)``, ``(list item)``, ``(table cell)`` or ``(code block)``
(:func:`block_of`), and a heading's line means the section under it. The stored anchor, and
what reads it back, are unchanged: only this line is longer. The person's words are
not re-wrapped or prefixed, so that a transcript check still finds them.

A RECORD AND A FILE (KYO-737). A paragraph may also be anchored to a record or one of its
elements, and to a span of a file the file routes serve (:mod:`kyosei.serve_tree`). The agent
reads the line and cannot open the client, so each line carries what finds the place:

    on record KYO-717.c4 "A record's page shows its criteria with..."
    on file src/kyosei/serve_notes.py lines 333-345 "def find( server: Any, root_of:..."
    on file docs/plan.md in the store repository lines 12-14 "The app is built in small..."
    on file notes/plan.md in the scratchpad line 3 "The second chunk waits on..." at "waits"

A record's line is its id, which ``kyo show`` reads, and the first words the element holds now
(:func:`kyosei.cmd_notes.element_words`); a dispatch has no words and is its id alone. A
file's line is its path relative to its root, the root when it is not the code repository
(``in the store repository``, ``in the scratchpad``, and ``in the scratchpad of session
<id>`` when the scratchpad is another session's), its lines counted from 1 (``line 3`` for one,
``lines 12-14`` for a span), and its words as a turn's are quoted: a selection of fewer than
:data:`ANCHOR_WORDS` words as its sentence and ``at`` the selection, and in a markdown file the
block's label after it. Lines are the source's lines whatever a client drew from them.

A client gives such a file as ``{"kind": "file", "root", "path", "lines": [first, last]}``,
with ``"chars": [start, end]`` for a selection inside those lines, counted in code points of
the file's text, and ``"session"`` for the ``scratch`` root. The server reads the file as the
blob route would serve it now, which needs the ``files`` permission as that route does, and
stores an anchor of an existing kind: ``document`` for a markdown file and ``file`` for any
other, the path as its target, the lines, the selection, a digest of the lines' text
(:func:`kyosei.cmd_notes.lines_digest`) and the commit that is the head of the root's
repository. That commit says which of the two repositories the path is in, so no root is
stored. A scratchpad has no commit, and its anchor names the session instead. A file with
uncommitted changes is anchored as it is served, and the commit then says only what the
changes were made over, which the digest does not depend on.

Such an anchor is ``stale`` when the file as served now does not hold the same text at those
lines, by the digest. A commit to the file that leaves those lines as they were changes
nothing. An anchor filed with a commit and no digest (``kyo notes add --anchors-file``) is
compared with the file's text at its commit in the same way, and keeps the line it always had,
``on file <path> at <commit> lines <first>-<last> "words..."``.

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

ATTACHMENTS (KYO-715). A mark may also carry ``"attachments": [ids]``, each the id of a file
saved by this session's attachments route (:mod:`kyosei.serve_attachments`), checked to exist
when the message is held. Each becomes one typed line ``attached <absolute path>`` directly
above the marked line, after that mark's ``on`` lines:

    on note KYO-N41
    attached ~/kyosei/composer/attachments/proj/<session>/<sha256>.png
    this is what the screen shows

A mark holding attachments and no anchor or tag files no note, and needs no ``notes``
permission. A note filed for a mark does not record its attachments.

Its anchors are validated when it is held, so a bad one is refused and nothing is held. Its
notes are filed only when it is delivered, one for each marked line, with the line's text,
anchors and tags, the token's device, ``entered`` as ``pasted`` for a line wholly inside a
pasted segment and ``typed`` otherwise, and the sent state from the start. A retracted
prompt files nothing, and the held message keeps the prompt's own segments and marks
(``prompt``) so that a composer can restore them.

A HELD PROMPT'S LINE (KYO-730). A prompt sent during a turn is held and typed when the turn
ends, so the agent receives it after replies its writer had not read. :func:`held_line` writes
one line that :mod:`kyosei.serve_held` types above such a prompt at delivery, shown here on
two:

    held 14 minutes: written before your reply 8c1f...e2 "The install is done and a..."
    and 2 later replies (replies are your text messages; tool calls are not counted)

The uuid is the record of the first text reply written since the prompt was held, and its
words are quoted as an ``on`` line quotes them. A prompt held across no text reply, and one
delivered by Send now, carries no line. The line is part of the prompt the transcript records,
as the ``on`` and ``attached`` lines are, so an anchor on that prompt counts its characters
from the line's first, on the server and in a client alike. A prompt's own marks are cut from
its segments when it is held, before the line exists, and are not moved by it.

A SESSION'S MARKS (KYO-710). After a prompt is sent, a chat still shows what each of its
lines was anchored to. A client draws a reply as the text the session routes return for a
``message_id``, which may join several transcript records, while a stored turn anchor lies
within one. :func:`kyosei.cmd_notes.session_marks` gives each anchor back in the client's
terms, by the same table that cut it (:func:`kyosei.cmd_notes.record_layout`), and joins the
anchors one span was stored as back into one. Both read routes open the store read-only.
A record anchor comes back as ``{"kind": "record", "id"}`` and a file's as ``{"kind", "root",
"path", "lines"}`` with its ``commit`` or ``session`` and its ``chars`` when it has them,
each with ``"stale": true`` when the element is gone from the store or the lines no longer
hold the words (KYO-737). Those entries carry a ``kind``; a turn's carry a ``role``.

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
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from . import (
    cmd_notes,
    commands,
    device_tokens,
    git_queries,
    serve_attachments,
    serve_files,
    serve_send,
    serve_tree,
    session_layer,
)
from .config import READ_TRANSCRIPTS_KEY, transcripts_opted_in
from .errors import KyoseiError, NotFound, UsageError
from .export import person_note_document
from .serve import Refusal
from .store import is_lock_contention, lock_contention_message

#: How many words of a span a message carries.
ANCHOR_WORDS = 6
#: How many words of the sentence holding a short selection a message carries (KYO-733).
SENTENCE_WORDS = 10

#: A span with one very long word stays short all the same.
ANCHOR_CHARS = 120

#: What a served write records as its actor, before the device's name.
ACTOR_PREFIX = "kyo-serve:"

_NOTE_KEYS = frozenset({"text", "anchors", "tags", "entered"})
_TAG_KEYS = frozenset({"add", "remove"})
_SEND_KEYS = frozenset({"notes"})
_PROMPT_KEYS = frozenset({"segments", "marks"})
_MARK_KEYS = frozenset({"line", "anchors", "tags", "attachments"})

#: KYO-737: the keys of an anchor to a span of a served file, as a client gives it.
SERVED_FILE_KEYS = frozenset({"kind", "root", "path", "lines", "chars", "session"})
_PATH_KINDS = (cmd_notes.ANCHOR_DOCUMENT, cmd_notes.ANCHOR_FILE)
#: What a file's line says of a root that is not the code repository.
_ROOT_WORDS = {
    serve_tree.ROOT_CODE: "",
    serve_tree.ROOT_STORE: " in the store repository",
    serve_tree.ROOT_SCRATCH: " in the scratchpad",
}

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
    root = root_of()
    files = _Files(server, root, authorization)
    with _context(server, root, _actor(device), read_only=False) as ctx:
        note = cmd_notes.file_note(
            ctx,
            text=text,
            tags=tags,
            anchors_of=lambda store: _validated(ctx, store, anchors, "anchors", files),
            device=device.name,
            entered=entered,
        )
    return {"ok": True, "note": note}


def _validated(
    ctx: commands.Context, store: Any, anchors: list, where: str, files: _Files
) -> list[dict]:
    """The anchors as rows to store; a turn by message may become several (KYO-709), and a
    served file is read here (KYO-737)."""
    rows: list[dict] = []
    for index, raw in enumerate(anchors):
        if _is_served_file(raw):
            rows.append(_file_anchor(files, raw, f"{where}[{index}]"))
        else:
            rows += cmd_notes.validate_anchors(ctx, store, raw, f"{where}[{index}]")
    return rows


# ------------------------------------------------------- a served file's anchor (KYO-737)


class _Files:
    """One request's reading of served files for its anchors (module docstring).

    The ``files`` permission is checked when the first such anchor is met, so a request with
    none needs none. A file is read once however many anchors name it, and each read is the
    file routes' own, with their checks.
    """

    def __init__(self, server: Any, root: str, authorization: str | None) -> None:
        self._environ = server.environ
        self._authorization = authorization
        self._allowed = False
        self.paths = serve_files.project_paths(root, server.environ)
        self._texts: dict[tuple[str, str | None, str], str | None] = {}
        self._commits: dict[str, str | None] = {}

    def allow(self) -> None:
        """Refuse a token without the ``files`` permission: the file's words go in a message."""
        if not self._allowed:
            serve_send.require_device(
                self._environ, self._authorization, device_tokens.PERMISSION_FILES
            )
            self._allowed = True

    def root(self, name: str, session: str | None) -> serve_tree.Root:
        if name == serve_tree.ROOT_SCRATCH and session:
            return serve_tree._scratch_root(self.paths, session)
        return serve_tree._root(self.paths, name)

    def text(self, name: str, session: str | None, relpath: str) -> str:
        """The file's text as the blob route serves it now, or that route's refusal."""
        kind = serve_tree.media_type(relpath.rsplit("/", 1)[-1])
        if not serve_tree.is_text(kind):
            raise Refusal(
                415,
                f"{relpath} is served as {kind}, which has no lines to anchor to; an anchor "
                "to a file names lines of a text file",
                reason="not-text",
                root=name,
                path=relpath,
            )
        _, data, _, _ = serve_tree.blob(self.root(name, session), relpath, None)
        return data.decode("utf-8", errors="replace")

    def text_now(self, name: str, session: str | None, relpath: str) -> str | None:
        """The same, kept for the request, and None for a file that is not served now."""
        key = (name, session, relpath)
        if key not in self._texts:
            try:
                self._texts[key] = self.text(name, session, relpath)
            except Refusal:
                self._texts[key] = None
        return self._texts[key]

    def root_of(self, anchor: dict) -> str:
        """The root a stored anchor's path is in: the scratchpad of its session, or the
        repository that holds its commit, and failing that the first root serving the path."""
        if anchor.get("session"):
            return serve_tree.ROOT_SCRATCH
        roots = serve_tree.served_roots(self.paths)
        commit = anchor.get("commit_sha")
        if commit:
            if commit not in self._commits:
                self._commits[commit] = next(
                    (root.name for root in roots if _holds_commit(root.path, commit)), None
                )
            if self._commits[commit] is not None:
                return self._commits[commit]
        for root in roots:
            if self.text_now(root.name, None, anchor["target"]) is not None:
                return root.name
        return roots[0].name


def _holds_commit(repository: Path, commit: str) -> bool:
    try:
        done = git_queries.run_git(repository, "cat-file", "-e", f"{commit}^{{commit}}")
    except OSError:
        return False
    return done.returncode == 0


def _is_served_file(raw: object) -> bool:
    """Whether a given anchor is one to a served file: a path anchor that names a root."""
    return isinstance(raw, dict) and raw.get("kind") in _PATH_KINDS and "root" in raw


def _has_lines_digest(anchor: dict) -> bool:
    """Whether a stored anchor was read from a served file, and so has its lines' digest."""
    return anchor["kind"] in _PATH_KINDS and str(anchor.get("digest") or "").startswith(
        f"{cmd_notes.LINES_DIGEST_SCHEME}:"
    )


def file_lines(text: str) -> list[str]:
    """A file's lines as a client counts them: split at each newline, and the newline that
    ends the file begins no line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _line_span(lines: list[str], first: int, last: int) -> tuple[int, int]:
    """Where lines ``first`` to ``last``, counted from 1, lie in the file's text."""
    start = sum(len(line) + 1 for line in lines[: first - 1])
    return start, start + len("\n".join(lines[first - 1 : last]))


def _file_anchor(files: _Files, raw: dict, where: str) -> dict:
    """A span of a served file as the row to store, read from the file as served now."""
    extra = sorted(set(raw) - SERVED_FILE_KEYS)
    if extra:
        raise UsageError(
            f"{where}: an anchor to a served file takes no "
            f"{', '.join(repr(key) for key in extra)}; it takes "
            f"{', '.join(sorted(SERVED_FILE_KEYS))}, and kyo reads the commit and computes "
            "the digest of the lines"
        )
    name, path, session = raw.get("root"), raw.get("path"), raw.get("session")
    if name not in serve_tree.ROOTS:
        raise UsageError(
            f"{where}: 'root' must be one of {', '.join(serve_tree.ROOTS)}, the roots the "
            "file routes serve"
        )
    if not isinstance(path, str) or not path:
        raise UsageError(f"{where}: 'path' must be the file's path relative to its root")
    if name == serve_tree.ROOT_SCRATCH:
        if not isinstance(session, str) or not session:
            raise UsageError(
                f"{where}: a file in a scratchpad needs 'session', the session whose "
                "scratchpad it is"
            )
    elif session is not None:
        raise UsageError(
            f"{where}: 'session' names the session of a scratchpad, and the {name} root has "
            "none; leave it out"
        )
    first, last = cmd_notes._range(raw, "lines", where)
    files.allow()
    try:
        text = files.text(name, session, path)
    except Refusal as exc:
        raise Refusal(exc.status, f"{where}: {exc}", headers=exc.headers, **exc.extra) from exc
    lines = file_lines(text)
    if not 1 <= first <= last <= len(lines):
        raise UsageError(
            f"{where}: lines {first}-{last} do not fall within the {len(lines)} lines "
            f"{path} has now, counted from 1; read the file again and anchor to what it holds"
        )
    markdown = serve_tree.media_type(path.rsplit("/", 1)[-1]) == "text/markdown"
    row: dict = {
        "kind": cmd_notes.ANCHOR_DOCUMENT if markdown else cmd_notes.ANCHOR_FILE,
        "target": path,
        "line_start": first,
        "line_end": last,
        "digest": cmd_notes.lines_digest(lines[first - 1 : last]),
        "digest_check": cmd_notes.DIGEST_COMPUTED,
    }
    if "chars" in raw:
        start, end = cmd_notes._range(raw, "chars", where)
        low, high = _line_span(lines, first, last)
        if not low <= start < end <= high:
            raise UsageError(
                f"{where}: characters {start}-{end} are not a selection inside lines "
                f"{first}-{last}, which are characters {low}-{high} of the file, counted in "
                "code points from 0"
            )
        row |= {"char_start": start, "char_end": end}
    if name == serve_tree.ROOT_SCRATCH:
        return row | {"session": session}
    commit = git_queries._git_head_or_none(files.root(name, None).path)
    if commit is None:
        raise UsageError(
            f"{where}: the {name} repository has no commit yet, and an anchor to a file "
            "records the commit it was read at; make a first commit there with `git commit` "
            "and anchor again"
        )
    return row | {"commit_sha": commit}


def _file_state(ctx: commands.Context, files: _Files, anchor: dict) -> tuple[str, str | None]:
    """The root a stored path anchor is in, and the file's text now when its span is fresh.

    Fresh means the file as served now holds at the anchor's lines the text it was anchored
    on: by the digest, or for an anchor filed with a commit alone by the file at that commit
    (module docstring). The text is None for a stale anchor.
    """
    name = files.root_of(anchor)
    text = files.text_now(name, anchor.get("session"), anchor["target"])
    if text is None:
        return name, None
    if "line_start" in anchor:
        first, last = anchor["line_start"], anchor["line_end"]
        lines = file_lines(text)
        if not 1 <= first <= last <= len(lines):
            return name, None
        now = "\n".join(lines[first - 1 : last])
        if _has_lines_digest(anchor):
            fresh = cmd_notes.lines_digest(lines[first - 1 : last]) == anchor["digest"]
        else:
            then = cmd_notes._git_text(ctx, anchor.get("commit_sha") or "", anchor["target"])
            fresh = then is not None and "\n".join(file_lines(then)[first - 1 : last]) == now
    else:
        start, end = anchor["char_start"], anchor["char_end"]
        then = cmd_notes._git_text(ctx, anchor.get("commit_sha") or "", anchor["target"])
        fresh = then is not None and end <= len(text) and then[start:end] == text[start:end]
    return name, text if fresh else None


def _file_place(ctx: commands.Context, files: _Files, anchor: dict) -> dict:
    """A stored path anchor as the marks route gives it: its address, its root, and whether
    it is stale."""
    name, text = _file_state(ctx, files, anchor)
    place = cmd_notes.path_place(anchor) | {"root": name}
    return place if text is not None else place | {"stale": True}


def _file_line(
    ctx: commands.Context, files: _Files, anchor: dict, session_id: str
) -> str:
    """The line for an anchor read from a served file (module docstring)."""
    first, last = anchor["line_start"], anchor["line_end"]
    name, text = _file_state(ctx, files, anchor)
    where = _ROOT_WORDS[name]
    if name == serve_tree.ROOT_SCRATCH and anchor["session"] != session_id:
        where += f" of session {anchor['session']}"
    span = f"line {first}" if first == last else f"lines {first}-{last}"
    line = f"on file {anchor['target']}{where} {span}"
    if text is None:
        return f"{line} (words unavailable)"
    markdown = anchor["kind"] == cmd_notes.ANCHOR_DOCUMENT
    low, high = _line_span(file_lines(text), first, last)
    if "char_start" not in anchor:
        # The lines are the place: a block's own words, without the mark that begins it.
        label, start, _ = block_of(text, low) if markdown else ("", low, high)
        quote = _words(text[max(start, low) : high])
        return f"{line} {quote}{f' ({label})' if label else ''}"
    selected = text[anchor["char_start"] : anchor["char_end"]]
    quote, label = _place(text, anchor["char_start"], markdown)
    if len(_clean_words(selected)) >= ANCHOR_WORDS or "\n" in selected:
        # A selection over several lines is in no one sentence, and is quoted itself.
        quote = _words(selected)
    elif _words(selected) != quote:
        quote = f"{quote} at {_words(selected)}"
    return f"{line} {quote}{f' ({label})' if label else ''}"


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


def marks(
    server: Any, root_of: Callable[[], str], authorization: str | None, session_id: str
) -> dict[str, Any]:
    """Every sent note with a turn anchor in this session, and every note sent to it that is
    on a record or a file, oldest first (module docstring)."""
    _device(server, authorization)
    root = root_of()
    with _context(server, root, None, read_only=True) as ctx:
        if not transcripts_opted_in(ctx.store().config):
            # The offsets are read from the transcript, as the session routes read it.
            raise Refusal(403, session_layer.NOT_OPTED_IN, setting=READ_TRANSCRIPTS_KEY)
        files = _Files(server, root, authorization)
        found = cmd_notes.session_marks(
            ctx, session_id, lambda anchor: _file_place(ctx, files, anchor)
        )
    return {"ok": True, "session": session_id, "count": len(found), "marks": found}


def tags(server: Any, root_of: Callable[[], str], authorization: str | None) -> dict[str, Any]:
    """Each tag now on a note with its count, most used first and then by name."""
    _device(server, authorization)
    with _context(server, root_of(), None, read_only=True) as ctx:
        counts = cmd_notes.tag_counts(ctx.store())
    return {"ok": True, "count": len(counts), "tags": counts}


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


def _clean_words(span: str) -> list[str]:
    """A span's words, control characters dropped: they would act as keys in a pane."""
    return "".join(
        char for char in span if char.isspace() or unicodedata.category(char) != "Cc"
    ).split()


def _words(span: str, limit: int = ANCHOR_WORDS) -> str:
    """The first ``limit`` words of a span, quoted."""
    words = _clean_words(span)
    shown = " ".join(words[:limit])
    cut = len(words) > limit or len(shown) > ANCHOR_CHARS
    return f'"{shown[:ANCHOR_CHARS]}{"..." if cut else ""}"'


_SENTENCE_END = re.compile(r"[.!?]+[\"')\]\u201d\u2019]*(?=\s|$)")
_LIST_ITEM = re.compile(r"\s*(?:[-*]|\d+\.)\s+")
_HEADING = re.compile(r"\s*#+\s")


def sentence_at(line: str, at: int) -> tuple[int, int]:
    """The sentence of ``line`` that offset ``at`` is in, as ``(from, to)`` (KYO-733).

    It ends at a full stop, question mark or exclamation mark that white space or the end
    follows, and has no white space at either end: the bounds a right-click anchor in the Mac
    app takes (``sentenceAt`` in ``apps/apple/web/src/marks.ts``), within one line.
    """
    start, end = 0, len(line)
    for match in _SENTENCE_END.finditer(line):
        if at < match.end():
            end = match.end()
            break
        start = match.end()
    while start < end and line[start].isspace():
        start += 1
    while end > start and line[end - 1].isspace():
        end -= 1
    return start, end


def block_of(text: str, at: int) -> tuple[str, int, int]:
    """The kind of block offset ``at`` of ``text`` is in, and the span of its words.

    Returns ``(label, from, to)``: the label is ``heading``, ``list item``, ``table cell``,
    ``code block`` or ``""`` for a paragraph, and ``from`` to ``to`` the part of the line the
    block's own words occupy, without its marker or the pipes around a cell (KYO-733). A line
    is read as markdown source: ``#``, ``-``/``*``/``1.`` and ``|`` begin a heading, an item
    and a cell, and a line between two fences is code.
    """
    offset = 0
    fenced = False
    for line in text.split("\n"):
        end = offset + len(line)
        stripped = line.lstrip()
        if at <= end:
            if stripped.startswith(("```", "~~~")):
                return "", offset, end
            if fenced:
                return "code block", offset, end
            if _HEADING.match(line):
                return "heading", offset + _HEADING.match(line).end(), end
            if _LIST_ITEM.match(line):
                return "list item", offset + _LIST_ITEM.match(line).end(), end
            if stripped.startswith("|"):
                bar_start = text.rfind("|", offset, at) if at > offset else -1
                start = max(bar_start, offset + len(line) - len(stripped)) + 1
                bar_end = text.find("|", max(at, start), end)
                return "table cell", start, end if bar_end < 0 else bar_end
            return "", offset, end
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        offset = end + 1
    return "", 0, len(text)


def _place(text: str, start: int, markdown: bool = True) -> tuple[str, str]:
    """The sentence holding offset ``start`` of ``text``, quoted, and its block's label.

    Text that is not markdown, as a source file is not, has no blocks: the sentence is
    looked for in the line that holds the offset, and nothing is labelled.
    """
    if markdown:
        label, first, last = block_of(text, start)
    else:
        newline = text.find("\n", start)
        label, first = "", text.rfind("\n", 0, start) + 1
        last = len(text) if newline < 0 else newline
    block = text[first:last]
    # A selection that starts before the block's words (on a marker or a pipe) is at its start.
    sentence_from, sentence_to = sentence_at(block, max(start - first, 0))
    sentence = block[sentence_from:sentence_to]
    if label == "code block":
        sentence = block.strip()
    return _words(sentence, SENTENCE_WORDS), label


#: What ends a held prompt's line, so that a later session reads what a reply is here.
HELD_LINE_COUNTS = "(replies are your text messages; tool calls are not counted)"


def held_line(seconds: float, replies: list[tuple[str, str]]) -> str | None:
    """The line above a held prompt at delivery, or None when no reply was written (KYO-730).

    ``seconds`` is how long the prompt was held, and ``replies`` the agent's text replies
    written in that time, oldest first, each as its record's uuid and its text. The time is
    in whole minutes, or in seconds under one minute.
    """
    if not replies:
        return None
    whole = max(int(seconds), 0)
    count, unit = (whole, "second") if whole < 60 else (whole // 60, "minute")
    uuid, text = replies[0]
    later = len(replies) - 1
    more = f" and {later} later {'reply' if later == 1 else 'replies'}" if later else ""
    return (
        f"held {count} {unit}{'' if count == 1 else 's'}: written before your reply {uuid} "
        f"{_words(text)}{more} {HELD_LINE_COUNTS}"
    )


def _anchor_line(
    ctx: commands.Context,
    anchor: dict,
    records: dict[str, dict],
    session_id: str,
    files: _Files | None = None,
) -> str:
    """One anchor as one line: its id, and the first words of its span if it has one."""
    kind, target = anchor["kind"], anchor["target"]
    if kind == cmd_notes.ANCHOR_RECORD:
        # KYO-737: the id is the address, and the words say which element it is.
        held = cmd_notes.element_words(ctx.store(), target)
        if held is None:
            return f"on record {target} (not in the store)"
        return f"on record {target}{f' {_words(held)}' if _clean_words(held) else ''}"
    if files is not None and _has_lines_digest(anchor):
        return _file_line(ctx, files, anchor, session_id)
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
    if not words:
        return f"{line} (words unavailable)"
    if kind != cmd_notes.ANCHOR_TURN:
        return f"{line} {_words(words)}"
    # A turn's span is read from its record, which span_text has found and checked.
    text = cmd_notes.record_text(records[target])
    quote, label = _place(text, anchor["char_start"])
    if len(_clean_words(words)) < ANCHOR_WORDS:
        # A selection that is the whole sentence is the quote already.
        if _words(words) != quote:
            quote = f"{quote} at {_words(words)}"
    else:
        quote = _words(words)
    return f"{line} {quote}{f' ({label})' if label else ''}"


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
    ctx: commands.Context, notes: list[dict], session_id: str, files: _Files | None = None
) -> list[dict[str, str]]:
    """The message for these notes, as send segments (module docstring)."""
    records = _records_of(ctx, [anchor for note in notes for anchor in note["anchors"]])
    plural = "note" if len(notes) == 1 else "notes"
    pieces = [(serve_send.TYPED, f"[kyo-message, {len(notes)} {plural}]")]
    for index, note in enumerate(notes, start=1):
        lines = [
            f"\n\nNote {index} ({note['id']})",
            *(_anchor_line(ctx, a, records, session_id, files) for a in note["anchors"]),
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
    root = root_of()
    with _context(server, root, None, read_only=True) as ctx:
        notes = [_note(ctx, note_id) for note_id in note_ids]
        for note in notes:
            _refuse_unsendable(note)
        files = _Files(server, root, authorization)
        if any(_has_lines_digest(anchor) for note in notes for anchor in note["anchors"]):
            # The words of a served file go into the message, as the blob route would give them.
            files.allow()
        segments = compose(ctx, notes, session_id, files)
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
        attachments = mark.get("attachments", [])
        if not isinstance(attachments, list) or not all(
            isinstance(item, str) for item in attachments
        ):
            raise serve_send.bad_request(
                f'{where}: "attachments" must be a list of the ids the attachments route gave'
            )
        if len(set(attachments)) != len(attachments):
            raise serve_send.bad_request(f'{where}: "attachments" names an attachment twice')
        if not anchors and not normalised and not attachments:
            raise serve_send.bad_request(
                f"{where} holds no anchor, no tag and no attachment, so there is nothing to "
                "mark; leave it out"
            )
        marks.append(
            {
                "line": line,
                "anchors": anchors,
                "tags": normalised,
                "attachments": attachments,
                "given": mark,
            }
        )
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


def _files_a_note(mark: dict) -> bool:
    """Whether a mark becomes a note: one holding attachments alone does not (KYO-715)."""
    return bool(mark["anchors"] or mark["tags"])


def _prompt_message(
    ctx: commands.Context | None,
    segments: list[dict[str, str]],
    marks: list[dict],
    session_id: str,
    files: _Files | None = None,
) -> tuple[list[dict[str, str]], list[dict]]:
    """The message a prompt sends, and what each marked line files once it is delivered."""
    lines = _lines_of(segments)
    text = "".join(segment["text"] for segment in segments)
    records = _records_of(ctx, [anchor for mark in marks for anchor in mark["rows"]])
    insertions: dict[int, str] = {}
    filing: list[dict] = []
    for mark in sorted(marks, key=lambda m: m["line"]):
        start, end, entered = lines[mark["line"]]
        on = [_anchor_line(ctx, anchor, records, session_id, files) for anchor in mark["rows"]]
        on += [f"attached {attachment['path']}" for attachment in mark["attached"]]
        if on:
            # An empty line's own line break follows, so it becomes its `on` lines alone.
            insertions[start] = "\n".join(on) + ("\n" if end > start else "")
        if not _files_a_note(mark):
            continue
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
    for index, mark in enumerate(marks):
        mark["attached"] = serve_attachments.resolve(
            server.environ,
            project_name,
            session_id,
            mark["attachments"],
            f"marks[{index}].attachments",
        )
        mark["rows"] = []
    filing: list[dict] = []
    if any(_files_a_note(mark) for mark in marks):
        # A mark becomes a note, so marking needs what filing a note needs.
        _device(server, authorization)
        root = root_of()
        files = _Files(server, root, authorization)
        with _context(server, root, None, read_only=True) as ctx:
            store = ctx.store()
            for index, mark in enumerate(marks):
                try:
                    mark["rows"] = _validated(
                        ctx, store, mark["anchors"], f"marks[{index}].anchors", files
                    )
                except (UsageError, NotFound) as exc:
                    # A mark is part of the body, so an anchor that names nothing is a
                    # malformed body here rather than a missing note or record.
                    raise serve_send.bad_request(str(exc)) from exc
            segments_sent, filing = _prompt_message(ctx, segments, marks, session_id, files)
    elif marks:
        # Attachments alone: no anchor to read, so no store is opened.
        segments_sent, filing = _prompt_message(None, segments, marks, session_id)
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
