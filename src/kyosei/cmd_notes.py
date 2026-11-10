"""``kyo notes``: what a person wrote, on the places it addresses (KYO-705, KYO-681 D2).

A NOTE is the person's text, its anchors, its tags, the device, the time and how it was
entered. It is a row of its own (``person_notes``) and not a kind in the registry, because a
record carries a title, a status and a place on the board, and a person may write dozens of
notes in one session. Its text and anchors are fixed when it is filed, and a person who wants
to add to it writes another note, anchored to it.

ITS TAGS CAN CHANGE (KYO-705.c5). ``kyo notes tag`` adds and removes them, one row of
``person_note_tag_changes`` and one ledger line for each tag that moved, naming who moved it
and when. The note's own ledger line stays as it was filed, and finding notes by tag reads
the current set.

AN ANCHOR addresses a place and holds no words from it:

=========  ==================================================================
turn       a transcript record (session, uuid), a character range, a digest
record     a record, criterion or gate id
document   a path at a commit, with a line range or a character range
file       a path at a commit, with a line range
note       another note's id, so a reply can be threaded
=========  ==================================================================

THE DIGEST OF A TURN ANCHOR (:func:`span_digest`) is HMAC-SHA256 of the span's text, keyed
by the SHA-256 of the whole record's text. Someone holding the transcript can recompute it
and confirm the span is unchanged. Someone holding only the export cannot test a guess
at a short span without first reproducing the whole record. KYO-663's per-line commitments
are a different object, made at send time for prompts and final replies, per line and
under a key: they cannot address a span inside a line or a record they never saw, so an
anchor does not use them. A one-word record is still guessable from its digest; KYO-663's
key is what would close that, under a new scheme name.

WHO COMPUTES IT. kyo does, wherever the project has opted in to reading transcripts
(KYO-656) and the transcript is there: it reads the record, takes the role from it, checks
the range, and refuses a supplied digest that disagrees (``digest_check: computed``). In any
other case the caller supplies digest and role, and kyo records that it took them on trust
(``digest_check: given``). A span's characters are code points of the record's text, the
``text`` blocks joined by one newline, so a client measuring UTF-16 units must convert.

A TURN ANCHOR BY MESSAGE (KYO-709). A reply the session API returns joins the text blocks of
every record of one message, while a turn anchor lies within one record. A client that knows
only a reply's text gives ``{"kind": "turn", "session", "message", "start", "end"}``, with
offsets into that joined text, and kyo stores one ordinary turn anchor for each record the
span covers, each within its own record. The joining is the transcript reader's own
(:func:`kyosei.harness.claude_code_transcript.reply_layout`), so an offset the client measured
lands on the record it measured. It needs the transcript, as a computed anchor does.

THE SENT STATE (KYO-709) is a field, ``sent``: the time the message holding the note was
delivered, the session and the held message. It is set once (:func:`mark_sent`, or
:func:`file_note` for a note sent as it is filed) and nothing changes or clears it. Until
schema 31 it was a ``sent`` tag; a rebuild migrates that tag to the field.

TWO WRITERS, ONE WRITE PATH (KYO-706). ``kyo notes`` and ``kyo serve``'s note routes
(:mod:`kyosei.serve_notes`) file a note with :func:`file_note` and change its tags with
:func:`change_tags`, so the transaction, the event and the ledger lines cannot differ.

CREDITS. A credit cites words checked against a transcript (:mod:`kyosei.credits`). A note is
the person's own words at their source, so the credit source kind that reads one (a later
chunk) will name the note by id and take ``person``, ``device`` and ``entered`` from it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from . import events_ledger, export, git_queries, ids, ledgers
from .cmd_sessions import recorded_transcript_path
from .commands import Context, Output
from .config import transcripts_opted_in
from .credits import _archive, _person
from .errors import NotFound, UsageError
from .harness import claude_code_transcript as transcript
from .prose import read_prose, single_line
from .render import echo_block
from .schema import NOTE_SENT_COMMAND
from .store import Store

#: How a note reached the store. What the composer observes (a paste, say) is written here.
ENTERED = ("typed", "pasted", "dictated", "drawn")

ANCHOR_TURN = "turn"
ANCHOR_RECORD = "record"
ANCHOR_DOCUMENT = "document"
ANCHOR_FILE = "file"
ANCHOR_NOTE = "note"

ROLE_PERSON = "person"
ROLE_AGENT = "agent"

#: ``digest_check``: kyo read the transcript, or took the caller's word for it.
DIGEST_COMPUTED = "computed"
DIGEST_GIVEN = "given"

#: The prefix of every digest, so a later scheme can sit beside this one in the same column.
DIGEST_SCHEME = "span-hmac-sha256"
DIGEST_RE = re.compile(rf"^{DIGEST_SCHEME}:[0-9a-f]{{64}}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
TAG_RE = re.compile(r"^[^\s,]{1,64}$")

#: The keys each anchor kind accepts in ``--anchors-file``. An anchor is an address: there
#: is no key for the words at that address, and any other key is refused.
ANCHOR_KEYS: dict[str, frozenset[str]] = {
    ANCHOR_TURN: frozenset({"kind", "session", "uuid", "start", "end", "digest", "role"}),
    ANCHOR_RECORD: frozenset({"kind", "id"}),
    ANCHOR_DOCUMENT: frozenset({"kind", "path", "commit", "lines", "chars"}),
    ANCHOR_FILE: frozenset({"kind", "path", "commit", "lines"}),
    ANCHOR_NOTE: frozenset({"kind", "id"}),
}

#: KYO-709: the keys of a turn anchor given by message, a span of a reply as the session API
#: joins it. Stored as one ``turn`` anchor per record the span covers.
TURN_MESSAGE_KEYS = frozenset({"kind", "session", "message", "start", "end"})

#: ``--anchored-to``: what a note is on, in the words a person would use. A prompt and a
#: reply are both turns, told apart by role.
ANCHORED_TO: dict[str, tuple[str, str | None]] = {
    "prompt": (ANCHOR_TURN, ROLE_PERSON),
    "output": (ANCHOR_TURN, ROLE_AGENT),
    "turn": (ANCHOR_TURN, None),
    "record": (ANCHOR_RECORD, None),
    "document": (ANCHOR_DOCUMENT, None),
    "file": (ANCHOR_FILE, None),
    "note": (ANCHOR_NOTE, None),
}


def span_digest(record_text: str, start: int, end: int) -> str:
    """The digest of ``record_text[start:end]``, keyed by the whole record (module docstring)."""
    key = hashlib.sha256(record_text.encode("utf-8")).digest()
    span = record_text[start:end].encode("utf-8")
    return f"{DIGEST_SCHEME}:{hmac.new(key, span, hashlib.sha256).hexdigest()}"


def record_text(record: dict) -> str | None:
    """A transcript record's text: a string, or its ``text`` blocks joined by one newline.

    Wider than the credit check's reader, which refuses a record with any other block: an
    agent's reply beside a tool call is still text the person can mark. None when the record
    holds no text at all.
    """
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = [block["text"] for block in content if transcript.is_text_block(block)]
    return "\n".join(parts) if parts else None


def _transcript_paths(ctx: Context, session: str) -> list[Path]:
    """The session's transcript files, or none when this project may not read them."""
    if not transcripts_opted_in(ctx.store().config):
        return []
    try:
        return list(
            transcript.candidate_transcripts(
                session,
                config=transcript.config_dir(ctx.environ),
                recorded=recorded_transcript_path(ctx.root, session),
                archive=_archive(ctx),
            )
        )
    except ValueError as exc:
        raise UsageError(f"{exc}; a turn anchor's session is the session's UUID") from exc


def read_reply_blocks(ctx: Context, session: str, message_id: str) -> list[tuple[str, str]]:
    """One reply's text blocks with their records' uuids, from the first of the session's
    files that holds the reply (:func:`kyosei.harness.claude_code_transcript.reply_blocks`).

    Empty when this project may not read the transcript, it is gone, or no reply has that id.
    """
    for path in _transcript_paths(ctx, session):
        blocks = transcript.reply_blocks(transcript._parse_lines(path), message_id)
        if blocks:
            return blocks
    return []


def read_records(ctx: Context, session: str, uuids: set[str]) -> dict[str, dict]:
    """The transcript records with these uuids, read in one pass over the session's files.

    Empty when this project may not read the transcript or it is gone.
    """
    paths = _transcript_paths(ctx, session)
    found: dict[str, dict] = {}
    for path in paths:
        for record in transcript._parse_lines(path):
            uuid = record.get("uuid")
            if uuid in uuids and uuid not in found:
                found[uuid] = record
    return found


def _read_record(ctx: Context, session: str, uuid: str) -> dict | None:
    """The transcript record, or None when this project may not read it or it is gone."""
    return read_records(ctx, session, {uuid}).get(uuid)


def _int(raw: dict, key: str, where: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise UsageError(f"{where}: {key!r} must be an integer")
    return value


def _range(raw: dict, key: str, where: str) -> tuple[int, int]:
    value = raw.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(n, bool) or not isinstance(n, int) for n in value)
    ):
        raise UsageError(f"{where}: {key!r} must be a list of two integers, first and last")
    return value[0], value[1]


def _turn_anchor(ctx: Context, raw: dict, where: str) -> dict:
    session, uuid = raw.get("session"), raw.get("uuid")
    if not isinstance(session, str) or not isinstance(uuid, str) or not session or not uuid:
        raise UsageError(f"{where}: a turn anchor needs 'session' and 'uuid'")
    start, end = _int(raw, "start", where), _int(raw, "end", where)
    if not 0 <= start < end:
        raise UsageError(f"{where}: the range {start}-{end} is empty or negative")
    given = raw.get("digest")
    if given is not None and (not isinstance(given, str) or not DIGEST_RE.match(given)):
        raise UsageError(f"{where}: 'digest' must be {DIGEST_SCHEME}:<64 hex digits>")
    role = raw.get("role")
    if role is not None and role not in (ROLE_PERSON, ROLE_AGENT):
        raise UsageError(f"{where}: 'role' must be {ROLE_PERSON} or {ROLE_AGENT}")
    fields = {
        "kind": ANCHOR_TURN,
        "target": uuid,
        "session": session,
        "char_start": start,
        "char_end": end,
    }
    record = _read_record(ctx, session, uuid)
    if record is None:
        if given is None or role is None:
            raise UsageError(
                f"{where}: this project may not read that transcript or it is not on this "
                "machine, so kyo cannot compute the anchor. Give 'digest' and 'role' in the "
                "anchor, or set read_transcripts: true in kyosei/config.yml if the "
                "transcript is here: nothing was written"
            )
        return {**fields, "role": role, "digest": given, "digest_check": DIGEST_GIVEN}
    text = record_text(record)
    if text is None:
        raise UsageError(f"{where}: transcript record {uuid} holds no text to anchor to")
    if end > len(text):
        raise UsageError(
            f"{where}: the range ends at {end}, past the record's {len(text)} characters"
        )
    computed = span_digest(text, start, end)
    if given is not None and given != computed:
        raise UsageError(
            f"{where}: the digest does not match the span the transcript holds at "
            f"{start}-{end}, so the span moved or the anchor was measured against other "
            "text: nothing was written"
        )
    read_role = ROLE_AGENT if record.get("type") == "assistant" else ROLE_PERSON
    return {**fields, "role": read_role, "digest": computed, "digest_check": DIGEST_COMPUTED}


def record_layout(blocks: list[tuple[str, str]]) -> list[tuple[str, int, int, int]]:
    """Where each block of one reply lies, in its record and in the reply (KYO-709, KYO-710).

    ``blocks`` is :func:`read_reply_blocks`'s. Each entry is the block's record uuid, where the
    block begins in that record's :func:`record_text`, and where it begins and ends in the
    reply's text (:func:`kyosei.harness.claude_code_transcript.reply_layout`). A record's text
    joins its own blocks with one newline, so a block's place in its record is counted
    afresh. Both directions read this one table: a span given by message is cut into record
    anchors with it, and a stored anchor is given back in reply offsets with it.
    """
    layout: list[tuple[str, int, int, int]] = []
    offset_in_record: dict[str, int] = {}
    parts = [text for _, text in blocks]
    for (uuid, text), (block_start, block_end) in zip(
        blocks, transcript.reply_layout(parts), strict=True
    ):
        before = offset_in_record.get(uuid)
        first = 0 if before is None else before + 1  # the newline record_text joins with
        offset_in_record[uuid] = first + len(text)
        layout.append((uuid, first, block_start, block_end))
    return layout


def _message_anchors(ctx: Context, raw: dict, where: str) -> list[dict]:
    """A span of one reply, as the session API joins it, as one anchor per record covered.

    A record's text joins its own blocks with one newline (:func:`record_text`) and the
    reply joins every block with :data:`~kyosei.harness.claude_code_transcript
    .REPLY_BLOCK_SEPARATOR`, so a block's place in its record is counted afresh here. A span
    covering two blocks of one record becomes one anchor over both, separator included.
    """
    extra = sorted(set(raw) - TURN_MESSAGE_KEYS)
    if extra:
        raise UsageError(
            f"{where}: a turn anchor given by 'message' takes no "
            f"{', '.join(repr(key) for key in extra)}; it takes "
            f"{', '.join(sorted(TURN_MESSAGE_KEYS))}, and kyo reads the role and computes "
            "the digest of each record it covers"
        )
    session, message = raw.get("session"), raw.get("message")
    if not isinstance(session, str) or not isinstance(message, str) or not session or not message:
        raise UsageError(f"{where}: a turn anchor by message needs 'session' and 'message'")
    start, end = _int(raw, "start", where), _int(raw, "end", where)
    if not 0 <= start < end:
        raise UsageError(f"{where}: the range {start}-{end} is empty or negative")
    blocks = read_reply_blocks(ctx, session, message)
    if not blocks:
        raise UsageError(
            f"{where}: no reply {message} with text was found in the transcript of session "
            f"{session}, or this project may not read it. A span given by message is "
            "measured against that reply's text, so it needs the transcript; give the "
            "anchor by 'uuid' with 'digest' and 'role' instead: nothing was written"
        )
    length = len(transcript.reply_text([text for _, text in blocks]))
    if end > length:
        raise UsageError(
            f"{where}: the range ends at {end}, past the reply's {length} characters"
        )
    spans: dict[str, list[int]] = {}
    for uuid, first, block_start, block_end in record_layout(blocks):
        low, high = max(start, block_start), min(end, block_end)
        if low >= high:
            continue
        span = spans.setdefault(uuid, [first + low - block_start, first + high - block_start])
        span[1] = first + high - block_start
    if not spans:
        raise UsageError(
            f"{where}: the range {start}-{end} covers only the space between two of the "
            "reply's blocks, which no record holds"
        )
    return [
        _turn_anchor(
            ctx,
            {"kind": ANCHOR_TURN, "session": session, "uuid": uuid, "start": low, "end": high},
            where,
        )
        for uuid, (low, high) in spans.items()
    ]


def _id_anchor(store: Store, kind: str, target: object, where: str) -> dict:
    if not isinstance(target, str) or not target:
        raise UsageError(f"{where}: a {kind} anchor needs 'id'")
    if kind == ANCHOR_NOTE:
        if not ids.PERSON_NOTE_RE.match(target):
            raise UsageError(f"{where}: {target!r} is not a note id (PREFIX-NN)")
        if store.person_note(target) is None:
            raise NotFound(f"{where}: no note {target!r}: nothing was written")
        return {"kind": ANCHOR_NOTE, "target": target}
    if ids.is_record_id(target):
        if not store.record_exists(target):
            raise NotFound(f"{where}: no record {target!r}: nothing was written")
    elif ids.child_kind(target) == "criterion":
        store.criterion(target)
    elif ids.child_kind(target) == "gate":
        store.gate(target)
    else:
        raise UsageError(
            f"{where}: {target!r} is not a record, criterion or gate id. A record anchor "
            "takes KYO-12, KYO-12.c3 or KYO-12.g1"
        )
    return {"kind": ANCHOR_RECORD, "target": target}


def _git_text(ctx: Context, commit: str, path: str) -> str | None:
    """The file at that commit, from the code repository or else the store repository."""
    for root in dict.fromkeys([Path(ctx.code_root), Path(ctx.root)]):
        text = git_queries._git_file_at(root, commit, path)
        if text is not None:
            return text
    return None


def _path_anchor(ctx: Context, kind: str, raw: dict, where: str) -> dict:
    path, commit = raw.get("path"), raw.get("commit")
    if (
        not isinstance(path, str)
        or not path
        or PurePosixPath(path).is_absolute()
        or ".." in PurePosixPath(path).parts
    ):
        raise UsageError(f"{where}: 'path' must be a relative path inside the repository")
    if not isinstance(commit, str) or not COMMIT_RE.match(commit):
        raise UsageError(f"{where}: 'commit' must be a git sha (7 to 40 hex digits)")
    if ("lines" in raw) == ("chars" in raw):
        raise UsageError(f"{where}: a {kind} anchor takes one of 'lines' or 'chars'")
    text = _git_text(ctx, commit, path)
    if text is None:
        raise UsageError(
            f"{where}: {path} at {commit} is in neither the code repository nor the store "
            "repository: nothing was written"
        )
    fields: dict = {"kind": kind, "target": path, "commit_sha": commit}
    if "lines" in raw:
        first, last = _range(raw, "lines", where)
        total = len(text.splitlines())
        if not 1 <= first <= last <= total:
            raise UsageError(
                f"{where}: lines {first}-{last} do not fall within the file's {total} lines"
            )
        return {**fields, "line_start": first, "line_end": last}
    first, last = _range(raw, "chars", where)
    if not 0 <= first <= last <= len(text):
        raise UsageError(
            f"{where}: characters {first}-{last} do not fall within the file's "
            f"{len(text)} characters"
        )
    return {**fields, "char_start": first, "char_end": last}


def validate_anchors(ctx: Context, store: Store, raw: object, where: str) -> list[dict]:
    """One given anchor as the rows to store: one, or for a turn given by message, one per
    record the span covers (module docstring). A refusal names what is wrong."""
    if isinstance(raw, dict) and raw.get("kind") == ANCHOR_TURN and "message" in raw:
        return _message_anchors(ctx, raw, where)
    return [validate_anchor(ctx, store, raw, where)]


def validate_anchor(ctx: Context, store: Store, raw: object, where: str) -> dict:
    """One ``--anchors-file`` entry as the row to store, or a refusal naming what is wrong."""
    if not isinstance(raw, dict):
        raise UsageError(f"{where}: an anchor is a JSON object")
    kind = raw.get("kind")
    if kind not in ANCHOR_KEYS:
        raise UsageError(f"{where}: 'kind' must be one of {', '.join(sorted(ANCHOR_KEYS))}")
    extra = sorted(set(raw) - ANCHOR_KEYS[kind])
    if extra:
        raise UsageError(
            f"{where}: {kind} anchors take no {', '.join(repr(key) for key in extra)}. An "
            "anchor holds addresses and a digest, never the text at the address, so that "
            "transcript text does not enter the store"
        )
    if kind == ANCHOR_TURN:
        return _turn_anchor(ctx, raw, where)
    if kind in (ANCHOR_RECORD, ANCHOR_NOTE):
        return _id_anchor(store, kind, raw.get("id"), where)
    return _path_anchor(ctx, kind, raw, where)


def _anchors(ctx: Context, store: Store, args: Namespace) -> list[dict]:
    anchors: list[dict] = []
    for target in args.on or []:
        kind = ANCHOR_NOTE if ids.PERSON_NOTE_RE.match(target) else ANCHOR_RECORD
        anchors.append(_id_anchor(store, kind, target, f"--on {target}"))
    if args.anchors_file:
        raw = read_prose(args.anchors_file, what="--anchors-file")
        try:
            listed = json.loads(raw)
        except ValueError as exc:
            raise UsageError(f"--anchors-file is not valid JSON: {exc}") from exc
        if not isinstance(listed, list):
            raise UsageError("--anchors-file must hold a JSON array of anchors")
        for index, item in enumerate(listed, start=1):
            anchors.extend(validate_anchors(ctx, store, item, f"anchor {index}"))
    return anchors


def normalise_tag(raw: str) -> str:
    """A tag as stored: without a leading ``#``, lower-cased."""
    tag = raw.strip().lstrip("#").casefold()
    if not TAG_RE.match(tag):
        raise UsageError(f"tag {raw!r} must be 1 to 64 characters with no space or comma")
    return tag


def _short(digest: str) -> str:
    return f"{digest.partition(':')[2][:12]}"


def anchor_label(anchor: dict) -> str:
    """One anchor as one line of text."""
    kind = anchor["kind"]
    if kind == ANCHOR_TURN:
        return (
            f"{anchor['role']} turn {anchor['target']} in {anchor['session']} "
            f"[{anchor['char_start']}-{anchor['char_end']}] "
            f"digest {_short(anchor['digest'])} ({anchor['digest_check']})"
        )
    if kind in (ANCHOR_DOCUMENT, ANCHOR_FILE):
        span = (
            f"lines {anchor['line_start']}-{anchor['line_end']}"
            if "line_start" in anchor
            else f"chars {anchor['char_start']}-{anchor['char_end']}"
        )
        return f"{kind} {anchor['target']} at {anchor['commit_sha']} {span}"
    return f"{kind} {anchor['target']}"


def note_lines(note: dict) -> list[str]:
    """A note as ``kyo notes`` prints it: the person's text, then what it is on."""
    who = note["person"] or note["author"]
    lines = [
        f"{note['id']}  {note['created_at']}  {who}, {note['entered']} on {note['device']}"
        f"  tags: {', '.join(note['tags']) or '(none)'}"
    ]
    if note["redacted_at"]:
        lines.append(f"  (text removed by `kyo redact`: {note['redacted_why']})")
    else:
        lines.extend(f"  | {line}" for line in note["text"].split("\n"))
    lines.extend(f"  on {anchor_label(anchor)}" for anchor in note["anchors"])
    sent = note["sent"]
    if sent is None:
        lines.append("  not sent")
    else:
        where = (
            f" to session {sent['session']} in message {sent['message']}"
            if sent["session"]
            else " (recorded by a sent tag, before the session was kept)"
        )
        lines.append(f"  sent at {sent['at']}{where}")
    for change in note["tag_changes"]:
        sign = "+" if change["action"] == "add" else "-"
        who = change["person"] or change["author"]
        lines.append(f"  tag {sign}{change['tag']} by {who} at {change['changed_at']}")
    return lines


def find_note_ids(
    store: Store,
    *,
    tags: list[str],
    target: str | None,
    anchored_to: str | None,
    person: str | None,
    session: str | None = None,
    sent: bool | None = None,
) -> list[str]:
    """Every note matching all the filters, oldest first.

    ``target`` matches an anchor's target or its session, and a record id also matches the
    criteria and gates of that record. ``session`` matches a turn anchor's session alone.
    ``target``, ``session`` and ``anchored_to`` must hold of one anchor together. ``sent``
    True or False keeps the notes that were or were never sent.
    """
    sql = "SELECT n.id FROM person_notes n WHERE 1=1"
    params: list[object] = []
    if sent is not None:
        sql += " AND n.sent_at IS NOT NULL" if sent else " AND n.sent_at IS NULL"
    for tag in tags:
        sql += (
            " AND EXISTS (SELECT 1 FROM person_note_tags t"
            " WHERE t.note_id = n.id AND t.tag = ?)"
        )
        params.append(tag)
    if person is not None:
        sql += " AND n.person = ?"
        params.append(person)
    conditions: list[str] = []
    if target is not None:
        if ids.is_record_id(target):
            conditions.append("(a.target = ? OR a.session = ? OR a.target LIKE ?)")
            params += [target, target, f"{target}.%"]
        else:
            conditions.append("(a.target = ? OR a.session = ?)")
            params += [target, target]
    if session is not None:
        conditions.append("a.session = ?")
        params.append(session)
    if anchored_to is not None:
        kind, role = ANCHORED_TO[anchored_to]
        conditions.append("a.kind = ?")
        params.append(kind)
        if role is not None:
            conditions.append("a.role = ?")
            params.append(role)
    if conditions:
        sql += (
            " AND EXISTS (SELECT 1 FROM person_note_anchors a"
            " WHERE a.note_id = n.id AND " + " AND ".join(conditions) + ")"
        )
    return [row["id"] for row in store.conn.execute(sql + " ORDER BY n.seq", params)]


def span_text(ctx: Context, anchor: dict, records: dict[str, dict]) -> str | None:
    """The words an anchor addresses, read from where they live, or None.

    ``anchor`` is as :func:`kyosei.export.person_note_document` gives it and ``records`` is
    what :func:`read_records` found. None for an anchor with no span, a transcript or commit
    that is gone, or a turn span whose digest no longer matches.
    """
    kind = anchor["kind"]
    if kind == ANCHOR_TURN:
        record = records.get(anchor["target"])
        text = None if record is None else record_text(record)
        start, end = anchor["char_start"], anchor["char_end"]
        if text is None or end > len(text) or span_digest(text, start, end) != anchor["digest"]:
            return None
        return text[start:end]
    if kind in (ANCHOR_DOCUMENT, ANCHOR_FILE):
        text = _git_text(ctx, anchor["commit_sha"], anchor["target"])
        if text is None:
            return None
        if "line_start" in anchor:
            return "\n".join(text.splitlines()[anchor["line_start"] - 1 : anchor["line_end"]])
        return text[anchor["char_start"] : anchor["char_end"]]
    return None


def _replies_of(
    ctx: Context, session: str, message_ids: set[str]
) -> dict[str, list[tuple[str, str]]]:
    """Each reply's blocks, as :func:`read_reply_blocks` gives them, read in one pass per file.

    A reply is taken from the first of the session's files that holds it, as there.
    """
    found: dict[str, list[tuple[str, str]]] = {}
    for path in _transcript_paths(ctx, session):
        wanted = message_ids - set(found)
        if not wanted:
            break
        rows = [
            record
            for record in transcript._parse_lines(path)
            if isinstance(record.get("message"), dict) and record["message"].get("id") in wanted
        ]
        for message_id in wanted:
            blocks = transcript.reply_blocks(rows, message_id)
            if blocks:
                found[message_id] = blocks
    return found


def _in_reply(
    layout: list[tuple[str, int, int, int]], uuid: str, start: int, end: int
) -> tuple[int, int, int, int] | None:
    """A span of one record as offsets into the reply's text, with the indexes of the blocks
    it starts and ends in; None when the record is not one of the reply's."""

    def at(offset: int) -> tuple[int, int] | None:
        # A record's blocks occupy [first, first + length], one newline apart, so an offset
        # falls in one block's range at most.
        for index, (owner, first, block_start, block_end) in enumerate(layout):
            if owner == uuid and first <= offset <= first + block_end - block_start:
                return index, block_start + offset - first
        return None

    low, high = at(start), at(end)
    if low is None or high is None:
        return None
    return low[1], high[1], low[0], high[0]


def session_marks(ctx: Context, session: str) -> list[dict]:
    """KYO-710: every sent note with a turn anchor in this session, oldest first, each anchor
    given as a client draws the turn: a reply by message with offsets into the reply's text,
    a prompt by uuid with offsets into its record.

    The reverse of :func:`_message_anchors`, by the same table (:func:`record_layout`): the
    anchors one span was stored as, one per record, are joined back into one where one ends
    at a block's end and the next starts at the following block's start in another record.
    An anchor whose record is gone, whose digest no longer matches, or whose record is not
    in its reply any longer is given ``stale`` and no offsets. A redacted note's text is null.
    """
    store = ctx.store()
    note_ids = find_note_ids(
        store,
        tags=[],
        target=None,
        anchored_to="turn",
        person=None,
        session=session,
        sent=True,
    )
    notes = [export.person_note_document(store, note_id) for note_id in note_ids]
    turns = {
        note["id"]: [
            anchor
            for anchor in note["anchors"]
            if anchor["kind"] == ANCHOR_TURN and anchor["session"] == session
        ]
        for note in notes
    }
    records = read_records(
        ctx, session, {anchor["target"] for anchors in turns.values() for anchor in anchors}
    )

    def message_of(anchor: dict) -> str | None:
        if anchor["role"] != ROLE_AGENT or span_text(ctx, anchor, records) is None:
            return None
        message = records[anchor["target"]].get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        return message_id if isinstance(message_id, str) else None

    message_ids = {
        message_id
        for anchors in turns.values()
        for anchor in anchors
        if (message_id := message_of(anchor)) is not None
    }
    layouts = {
        message_id: record_layout(blocks)
        for message_id, blocks in _replies_of(ctx, session, message_ids).items()
    }
    marks: list[dict] = []
    for note in notes:
        given: list[dict] = []
        # The last anchor given back on a reply, and the block it ends in, while it may join.
        joinable: tuple[dict, int] | None = None
        for anchor in turns[note["id"]]:
            role, uuid = anchor["role"], anchor["target"]
            stale = {"role": role, "uuid": uuid, "stale": True}
            if role != ROLE_AGENT:
                joinable = None
                fresh = span_text(ctx, anchor, records) is not None
                given.append(
                    {
                        "role": role,
                        "uuid": uuid,
                        "start": anchor["char_start"],
                        "end": anchor["char_end"],
                    }
                    if fresh
                    else stale
                )
                continue
            message_id = message_of(anchor)
            layout = layouts.get(message_id) if message_id is not None else None
            place = (
                None
                if layout is None
                else _in_reply(layout, uuid, anchor["char_start"], anchor["char_end"])
            )
            if place is None:
                joinable = None
                given.append(stale)
                continue
            start, end, first_block, last_block = place
            assert layout is not None
            if joinable is not None:
                previous, previous_block = joinable
                if (
                    previous["message"] == message_id
                    and first_block == previous_block + 1
                    and previous["end"] == layout[previous_block][3]
                    and start == layout[first_block][2]
                    and layout[previous_block][0] != uuid
                ):
                    previous["end"] = end
                    joinable = (previous, last_block)
                    continue
            entry = {"role": role, "message": message_id, "start": start, "end": end}
            given.append(entry)
            joinable = (entry, last_block)
        marks.append(
            {
                "note": note["id"],
                "text": None if note["redacted_at"] else note["text"],
                "tags": note["tags"],
                "sent": note["sent"],
                "anchors": given,
            }
        )
    return marks


def tag_counts(store: Store) -> list[dict]:
    """KYO-710: each tag now on at least one note, with how many carry it, most used first
    and then by name."""
    return [
        {"tag": row["tag"], "count": row["count"]} for row in store.person_note_tag_counts()
    ]


def file_note(
    ctx: Context,
    *,
    text: str,
    tags: list[str],
    anchors_of: Callable[[Store], list[dict]],
    device: str,
    entered: str,
    person: str | None = None,
    sent: dict[str, str] | None = None,
) -> dict:
    """File one note and return its document: the one write path (module docstring).

    ``anchors_of`` is called inside the transaction, so what an anchor names is checked
    under the store's write lock. ``sent`` (``at``, ``session``, ``message``) files a note
    already sent, as ``send-prompt`` does once its message is delivered.
    """
    return file_notes(
        ctx,
        [
            {
                "text": text,
                "tags": tags,
                "anchors_of": anchors_of,
                "device": device,
                "entered": entered,
                "person": person,
                "sent": sent,
            }
        ],
    )[0]


def file_notes(ctx: Context, notes: list[dict]) -> list[dict]:
    """File several notes in one transaction, all or none, and return their documents.

    Each entry holds :func:`file_note`'s keyword arguments. ``send-prompt`` files a
    delivered prompt's notes this way, so a failure leaves none of them filed and the whole
    set can be filed again (KYO-709).
    """
    store = ctx.store()
    documents: list[dict] = []
    lines: list[dict] = []
    events: list[dict] = []
    with store.transaction():
        for note in notes:
            who = _person(store, note.get("person"))
            anchors = note["anchors_of"](store)
            note_id = store.add_person_note(
                text=note["text"],
                person=who,
                author=ctx.actor,
                device=note["device"],
                entered=note["entered"],
                anchors=anchors,
                tags=note["tags"],
                sent=note.get("sent"),
            )
            documents.append(export.person_note_document(store, note_id))
            lines.append(export.person_note_line(store, note_id))
            # Names the note and carries no text: its words live on its own ledger line
            # alone, which is the one place `kyo redact` then has to reach.
            events.append(
                store.record_event(
                    actor=ctx.actor,
                    command="notes add",
                    record_id=None,
                    before=None,
                    after={"person_note_id": note_id},
                )
            )
    events_ledger.append_events(store, events)
    for line in lines:
        ledgers.append_person_note(store, line)
    ledgers.write_allocations(store)
    return documents


def mark_sent(
    ctx: Context, note_ids: list[str], *, at: str, session: str, message: str
) -> list[str]:
    """Set the sent state of filed notes, once each, in one transaction (KYO-709).

    Returns the notes it was set on. A note already sent keeps its state, so a note
    delivered twice records the first delivery. Each note set gets a ``notes sent`` event,
    which is where a rebuild finds the state, because the note's own line is not rewritten.
    """
    store = ctx.store()
    for note_id in note_ids:
        if store.person_note(note_id) is None:
            raise NotFound(f"no note {note_id!r}: ids are printed when a note is added and listed")
    events: list[dict] = []
    marked: list[str] = []
    with store.transaction():
        for note_id in note_ids:
            if not store.mark_person_note_sent(note_id, at=at, session=session, message=message):
                continue
            marked.append(note_id)
            events.append(
                store.record_event(
                    actor=ctx.actor,
                    command=NOTE_SENT_COMMAND,
                    record_id=None,
                    before=None,
                    after={
                        "person_note_id": note_id,
                        "sent": {"at": at, "session": session, "message": message},
                    },
                )
            )
    if events:
        events_ledger.append_events(store, events)
        ledgers.write_allocations(store)
    return marked


def _add(ctx: Context, args: Namespace) -> Output:
    if not args.entered:
        raise UsageError(
            f"kyo notes add needs --entered ({', '.join(ENTERED)}): how the text reached "
            "kyo is a fact about the note that only the caller knows"
        )
    text = read_prose(args.file, what="--file") if args.file else ""
    tags = sorted({normalise_tag(tag) for tag in args.tag or []})
    if not text.strip() and not tags:
        raise UsageError(
            "a note needs text (--file FILE, or '-' for stdin, law 5) or at least one --tag"
        )
    device = (
        single_line(args.device, what="--device")
        if args.device is not None
        else socket.gethostname()
    )
    document = file_note(
        ctx,
        text=text,
        tags=tags,
        anchors_of=lambda opened: _anchors(ctx, opened, args),
        device=device,
        entered=args.entered,
        person=args.person,
    )
    note_id = document["id"]
    lines = [f"wrote note {note_id}"]
    if text:
        lines += echo_block("note", text)
    lines += [f"  on {anchor_label(anchor)}" for anchor in document["anchors"]]
    lines.append(f"  tags: {', '.join(document['tags']) or '(none)'}")
    return Output(lines, {"ok": True, "command": "notes add", "note": document})


def _sent_filter(args: Namespace) -> bool | None:
    if getattr(args, "sent", False):
        return True
    if getattr(args, "unsent", False):
        return False
    return None


def _list(ctx: Context, args: Namespace) -> Output:
    if args.on and len(args.on) > 1:
        raise UsageError("kyo notes list takes one --on TARGET")
    store = ctx.store()
    note_ids = find_note_ids(
        store,
        tags=[normalise_tag(tag) for tag in args.tag or []],
        target=args.on[0] if args.on else None,
        anchored_to=args.anchored_to,
        person=args.person,
        sent=_sent_filter(args),
    )
    notes = [export.person_note_document(store, note_id) for note_id in note_ids]
    lines: list[str] = []
    for note in notes:
        lines += note_lines(note)
    if not notes:
        lines.append("no notes match")
    return Output(lines, {"ok": True, "command": "notes list", "count": len(notes), "notes": notes})


def _show(ctx: Context, args: Namespace) -> Output:
    if not args.id:
        raise UsageError("kyo notes show needs a note id (PREFIX-NN)")
    store = ctx.store()
    if store.person_note(args.id) is None:
        raise NotFound(f"no note {args.id!r}: ids are printed when a note is added and listed")
    note = export.person_note_document(store, args.id)
    return Output(note_lines(note), {"ok": True, "command": "notes show", "note": note})


def change_tags(
    ctx: Context,
    note_id: str,
    *,
    add: set[str],
    remove: set[str],
    person: str | None = None,
) -> tuple[dict, list[dict], list[str]]:
    """Move a filed note's tags: its document, the changes made, and the tags already so."""
    store = ctx.store()
    if store.person_note(note_id) is None:
        raise NotFound(f"no note {note_id!r}: ids are printed when a note is added and listed")
    with store.transaction():
        who = _person(store, person)
        rows = store.change_person_note_tags(
            note_id, add=add, remove=remove, person=who, author=ctx.actor
        )
        document = export.person_note_document(store, note_id)
        # Written only when a tag moved: a repeated request leaves no trace to explain.
        event = (
            store.record_event(
                actor=ctx.actor,
                command="notes tag",
                record_id=None,
                before=None,
                after={
                    "person_note_id": note_id,
                    "person_note_tag_changes": [row["id"] for row in rows],
                },
            )
            if rows
            else None
        )
    if event is not None:
        events_ledger.append_events(store, [event])
        for row in rows:
            ledgers.append_person_note_tag_change(store, row)
        ledgers.write_allocations(store)
    moved = {row["tag"] for row in rows}
    return document, [dict(row) for row in rows], sorted((add | remove) - moved)


def _tag(ctx: Context, args: Namespace) -> Output:
    if not args.id:
        raise UsageError("kyo notes tag needs a note id (PREFIX-NN)")
    fixed = [
        flag
        for flag, value in (
            ("--file", args.file),
            ("--on", args.on),
            ("--anchors-file", args.anchors_file),
            ("--tag", args.tag),
            ("--device", args.device),
            ("--entered", args.entered),
            ("--anchored-to", args.anchored_to),
            ("--sent", getattr(args, "sent", False)),
            ("--unsent", getattr(args, "unsent", False)),
        )
        if value
    ]
    if fixed:
        raise UsageError(
            f"kyo notes tag takes --add, --remove and --person, not {', '.join(fixed)}. A "
            f"filed note's text and anchors are fixed: to add to it, write another note on it "
            f"with `kyo notes add --on {args.id} --entered typed --file FILE`"
        )
    add = {normalise_tag(tag) for tag in args.add or []}
    remove = {normalise_tag(tag) for tag in args.remove or []}
    if not add and not remove:
        raise UsageError("kyo notes tag needs at least one --add TAG or --remove TAG")
    both = sorted(add & remove)
    if both:
        raise UsageError(f"--add and --remove both name {', '.join(both)}: nothing was written")
    document, rows, unchanged = change_tags(
        ctx, args.id, add=add, remove=remove, person=args.person
    )
    tags = ", ".join(document["tags"]) or "(none)"
    if rows:
        steps = ", ".join(("+" if r["action"] == "add" else "-") + r["tag"] for r in rows)
        lines = [f"changed tags on {args.id}: {steps}", f"  tags: {tags}"]
    else:
        lines = [f"no tag changed on {args.id}", f"  tags: {tags}"]
    if unchanged and rows:
        lines.append(f"  already so: {', '.join(unchanged)}")
    return Output(
        lines,
        {
            "ok": True,
            "command": "notes tag",
            "note": document,
            "changes": rows,
            "unchanged": unchanged,
        },
    )


def _tags(ctx: Context, args: Namespace) -> Output:
    given = [
        flag
        for flag, value in (
            ("a note id", args.id),
            ("--file", args.file),
            ("--on", args.on),
            ("--anchors-file", args.anchors_file),
            ("--tag", args.tag),
            ("--person", args.person),
            ("--device", args.device),
            ("--entered", args.entered),
            ("--anchored-to", args.anchored_to),
        )
        if value
    ]
    if given:
        raise UsageError(
            f"kyo notes tags lists every tag in use and takes no {', '.join(given)}; to find "
            "the notes carrying a tag, run `kyo notes list --tag TAG`"
        )
    counts = tag_counts(ctx.store())
    width = max((len(str(row["count"])) for row in counts), default=1)
    lines = [f"{row['count']:>{width}}  {row['tag']}" for row in counts] or ["no tags in use"]
    return Output(lines, {"ok": True, "command": "notes tags", "tags": counts})


def cmd_notes(ctx: Context, args: Namespace) -> Output:
    """``kyo notes add|list|show|tag|tags`` (module docstring)."""
    if args.action in ("add", "show", "tags") and (
        getattr(args, "sent", False) or getattr(args, "unsent", False)
    ):
        raise UsageError(
            "--sent and --unsent filter kyo notes list; a note's sent state is set when the "
            "message holding it is delivered, and no command sets it"
        )
    if args.action != "tag" and (args.add or args.remove):
        raise UsageError(
            "--add and --remove change a filed note's tags, so they belong to the tag "
            "action; filing a note takes --tag"
        )
    if args.action == "tag":
        return _tag(ctx, args)
    if args.action == "add":
        return _add(ctx, args)
    if args.action == "show":
        return _show(ctx, args)
    if args.action == "tags":
        return _tags(ctx, args)
    return _list(ctx, args)
