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
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts) if parts else None


def _read_record(ctx: Context, session: str, uuid: str) -> dict | None:
    """The transcript record, or None when this project may not read it or it is gone."""
    if not transcripts_opted_in(ctx.store().config):
        return None
    try:
        paths = transcript.candidate_transcripts(
            session,
            config=transcript.config_dir(ctx.environ),
            recorded=recorded_transcript_path(ctx.root, session),
            archive=_archive(ctx),
        )
    except ValueError as exc:
        raise UsageError(f"{exc}; a turn anchor's session is the session's UUID") from exc
    for path in paths:
        for record in transcript._parse_lines(path):
            if record.get("uuid") == uuid:
                return record
    return None


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
            anchors.append(validate_anchor(ctx, store, item, f"anchor {index}"))
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
) -> list[str]:
    """Every note matching all the filters, oldest first.

    ``target`` matches an anchor's target or its session, and a record id also matches the
    criteria and gates of that record. ``target`` and ``anchored_to`` must hold of one
    anchor together.
    """
    sql = "SELECT n.id FROM person_notes n WHERE 1=1"
    params: list[object] = []
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


def _add(ctx: Context, args: Namespace) -> Output:
    if not args.entered:
        raise UsageError(
            f"kyo notes add needs --entered ({', '.join(ENTERED)}): how the text reached "
            "kyo is a fact about the note that only the caller knows"
        )
    store = ctx.store()
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
    with store.transaction():
        person = _person(store, args.person)
        anchors = _anchors(ctx, store, args)
        note_id = store.add_person_note(
            text=text,
            person=person,
            author=ctx.actor,
            device=device,
            entered=args.entered,
            anchors=anchors,
            tags=tags,
        )
        document = export.person_note_document(store, note_id)
        line = export.person_note_line(store, note_id)
        # Names the note and carries no text: its words live on its own ledger line
        # alone, which is the one place `kyo redact` then has to reach.
        event = store.record_event(
            actor=ctx.actor,
            command="notes add",
            record_id=None,
            before=None,
            after={"person_note_id": note_id},
        )
    events_ledger.append_events(store, [event])
    ledgers.append_person_note(store, line)
    ledgers.write_allocations(store)
    lines = [f"wrote note {note_id}"]
    if text:
        lines += echo_block("note", text)
    lines += [f"  on {anchor_label(anchor)}" for anchor in document["anchors"]]
    lines.append(f"  tags: {', '.join(document['tags']) or '(none)'}")
    return Output(lines, {"ok": True, "command": "notes add", "note": document})


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
    store = ctx.store()
    if store.person_note(args.id) is None:
        raise NotFound(f"no note {args.id!r}: ids are printed when a note is added and listed")
    with store.transaction():
        person = _person(store, args.person)
        rows = store.change_person_note_tags(
            args.id, add=add, remove=remove, person=person, author=ctx.actor
        )
        document = export.person_note_document(store, args.id)
        # Written only when a tag moved: a repeated request leaves no trace to explain.
        event = (
            store.record_event(
                actor=ctx.actor,
                command="notes tag",
                record_id=None,
                before=None,
                after={
                    "person_note_id": args.id,
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
    unchanged = sorted((add | remove) - moved)
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
            "changes": [dict(row) for row in rows],
            "unchanged": unchanged,
        },
    )


def cmd_notes(ctx: Context, args: Namespace) -> Output:
    """``kyo notes add|list|show|tag`` (module docstring)."""
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
    return _list(ctx, args)
