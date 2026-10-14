"""What ``kyo redact`` does to the ledgers: scrubbing redacted text out of event lines,
shift notes, the events table and every embedded copy, and naming what is left.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import ids
from .contracts import EMBEDDED_PROSE, REDACTION_MARKER
from .events_ledger import (
    EVENT_CHANGED_KEY,
    EVENT_SNAPSHOT_SIDES,
    EVIDENCE_COLLECTION,
    _event_column_values,
    _row_changed_json,
    event_from_columns,
    event_line,
    events_files,
)
from .export import (
    _digest,
    atomic_write_text,
    export_dir,
    iter_ledger_lines,
    ledger_lock,
    parse_ledger_line,
)
from .ledgers import _read_ledger_lines, shift_notes_path
from .prose import replace_phrase
from .store import Store


def scrub_redacted_from_ledgers(store: Store, target_id: str, kind: str, marker: str) -> int:
    """Rewrite the git-canonical ledgers so one redacted item's text is gone.
    Returns how many lines changed (KYO-25, generalising KYO-22).

    Four prose stores, four places their text is copied to, one mechanism —
    because the argument for scrubbing evidence is the argument for all of them,
    and a redaction command that covers one of four is a redaction command whose
    users will believe they are covered:

    ============  ==================================  ==========================
    kind          text lives at                       copied into
    ============  ==================================  ==========================
    evidence      ``evidence_artifacts.text``         record documents
    claim         ``claims.statement``                record documents
    note          ``notes.text``                      record documents
    shift-note    ``shift_notes.text``                ``_shift_notes.jsonl``
                                                      *and* its own event's
                                                      ``after.text``
    ============  ==================================  ==========================

    A record document goes into ``_events.jsonl`` on every mutation of that
    record, which is why one item's text ends up in many rows. The shift-note case
    is the one a consumer named specifically: the shift log is "precisely where a
    controller quotes a log line while thinking out loud", and its text is written
    into the event payload directly rather than through a record document.

    See :func:`kyosei.cmd_redact.cmd_redact` for why rewriting an append-only ledger
    is the deliberate trade it is.
    """
    changed = 0
    changed += _scrub_events(store, target_id, kind, marker)
    if kind == "shift-note":
        changed += _scrub_shift_notes(store, target_id, marker)
    return changed


#: Which record-document list and field each redactable kind lives in.
_DOCUMENT_FIELD = {
    "evidence": (EVIDENCE_COLLECTION, "text"),
    "claim": ("claims", "statement"),
    "note": ("notes", "text"),
}


def _scrub_events(store: Store, target_id: str, kind: str, marker: str) -> int:
    """KYO-268.c2: every file the ledger is made of, not one — a store mid-transition
    has both a monolithic ``_events.jsonl`` and shards, and a scrub that reached only one
    of them would report a number while leaving the text in the export."""
    changed = 0
    with ledger_lock(export_dir(store)):
        for path in events_files(store):
            changed += _scrub_one_event_file(path, target_id, kind, marker)
    return changed


def _scrub_one_event_file(path: Path, target_id: str, kind: str, marker: str) -> int:
    """Scrub one events file, rewriting it only if a line in it changed (KYO-407.c2).

    A line the scrub does not change keeps its bytes exactly, and a file with no such
    line is not written at all: rewriting every one of a ledger's shards on every
    redaction widened the window a torn write could land in to the whole ledger, and
    left every shard dirty in git for a change that was in one of them.
    """
    changed = 0
    lines: list[str] = []
    for _, raw, event in iter_ledger_lines(path):
        if _scrub_event(event, target_id, kind, marker):
            changed += 1
            lines.append(event_line(event))
        else:
            lines.append(raw if raw.endswith("\n") else raw + "\n")
    if changed:
        atomic_write_text(path, "".join(lines))
    return changed


def _scrub_event(event: dict, target_id: str, kind: str, marker: str) -> bool:
    """Scrub one redacted item's text out of one event, in place; True if it changed.

    Factored out of :func:`_scrub_one_event_file` by KYO-404 so the ledger repair
    (:func:`repair_ledgers`) scrubs a re-appended event by the same definition the
    redaction scrubbed the lines already in the file — one rule, two callers (law 3).
    """
    touched = False
    if kind == "shift-note":
        after = event.get("after")
        if (
            isinstance(after, dict)
            and after.get("shift_note_id") == target_id
            and after.get("text") != marker
        ):
            after["text"] = marker
            touched = True
        return touched
    collection, field = _DOCUMENT_FIELD[kind]
    # KYO-418.c4: `changed` too. It holds the PRIOR value of every key a mutation
    # moved -- a record document's lists included -- so it is the one place a
    # post-25 line still carries an item as it was before its redaction.
    for side, _column in EVENT_SNAPSHOT_SIDES:
        document = event.get(side)
        if not isinstance(document, dict):
            continue
        for item in document.get(collection, []) or []:
            if not isinstance(item, dict) or item.get("id") != target_id:
                continue
            # KYO-268.c1: a snapshot written from schema_version 19 on
            # carries an evidence artifact by id and sha only, so there is
            # no `text` key to scrub and the digest is the whole of the
            # copy. Keyed on whether the FIELD is present rather than on a
            # version number, for the same reason the ledger defaults
            # tables are: the two formats are mixed inside one ledger by
            # construction, and an older line is still a line with a body
            # in it. Writing the field back into a bodiless snapshot would
            # re-add a key this format does not have.
            if field in item and item[field] != marker:
                item[field] = marker
                touched = True
            redacted_digest = _digest(marker)
            if item.get("sha256") not in (None, redacted_digest):
                item["sha256"] = redacted_digest
                touched = True
    return touched


def _residue_texts(event: dict, target_id: str, kind: str, marker: str) -> set[str]:
    """The redacted item's own text wherever this event still carries it (KYO-405).

    The read-only twin of :func:`_scrub_event`, over the same fields: what that function
    would overwrite, this one returns. It is how a retried redaction recovers the text it
    has to match against once the item's own row already holds the marker.
    """
    found: set[str] = set()
    if kind == "shift-note":
        after = event.get("after")
        if isinstance(after, dict) and after.get("shift_note_id") == target_id:
            text = after.get("text")
            if isinstance(text, str) and text != marker:
                found.add(text)
        return found
    collection, field = _DOCUMENT_FIELD[kind]
    for side, _column in EVENT_SNAPSHOT_SIDES:
        document = event.get(side)
        if not isinstance(document, dict):
            continue
        for item in document.get(collection, []) or []:
            if isinstance(item, dict) and item.get("id") == target_id:
                text = item.get(field)
                if isinstance(text, str) and text != marker:
                    found.add(text)
    return found


def _event_rows_naming(store: Store, target_id: str) -> list[sqlite3.Row]:
    """The db ``events`` rows whose snapshots mention ``target_id`` at all — a textual
    prefilter (``EXP-1.e1`` also matches ``EXP-1.e10``); :func:`_scrub_event` decides."""
    return store.conn.execute(
        "SELECT id, before_json, after_json, changed_json FROM events"
        " WHERE instr(coalesce(before_json, ''), ?) > 0"
        " OR instr(coalesce(after_json, ''), ?) > 0"
        " OR instr(coalesce(changed_json, ''), ?) > 0",
        (target_id, target_id, target_id),
    ).fetchall()


def _scrub_event_row(
    store: Store,
    row: sqlite3.Row,
    texts: list[str],
    replacement: str,
    needle: str,
    rule: Callable[[dict], bool],
) -> bool:
    """One ``events`` row through both redaction rules, written back if either changed
    it; True if it was. The whole-blob match runs on each snapshot column's text, then
    ``rule`` (an id-keyed scrub) on the decoded sides when ``needle`` occurs in any.
    Shared by the two table scrubs so a third snapshot column (KYO-418's
    ``changed_json``) is one entry in :data:`EVENT_SNAPSHOT_SIDES` rather than a
    change in each of them."""
    original = {column: row[column] for column in _SNAPSHOT_COLUMNS if column in row.keys()}  # noqa: SIM118
    values = dict(original)
    for text in texts:
        for column, value in values.items():
            if value is not None:
                values[column], _hits = _scrub_every_form(value, text, replacement)
    if any(needle in (value or "") for value in values.values()):
        event = event_from_columns(
            values.get("before_json"), values.get("after_json"), values.get("changed_json")
        )
        if "seq" in row.keys():  # noqa: SIM118
            event["seq"] = row["seq"]
        if rule(event):
            before, changed, after = _event_column_values(event)
            values.update(before_json=before, changed_json=changed, after_json=after)
            values = {column: values[column] for column in original}
    if values == original:
        return False
    assignments = ", ".join(f"{column} = ?" for column in values)
    store.conn.execute(
        f"UPDATE events SET {assignments} WHERE id = ?",  # noqa: S608 - fixed column names
        (*values.values(), row["id"]),
    )
    return True


#: The ``events`` columns a snapshot is stored in, in :data:`EVENT_SNAPSHOT_SIDES` order.
_SNAPSHOT_COLUMNS = tuple(column for _side, column in EVENT_SNAPSHOT_SIDES)


def scrub_redacted_from_events_table(
    store: Store, target_id: str, kind: str, marker: str, originals: tuple[str, ...] = ()
) -> int:
    """Scrub one redacted item out of the DATABASE's ``events`` snapshots; returns how
    many rows changed (KYO-405).

    The table the ledger's lines were written from carries the same snapshots, so every
    copy of ``kyosei.db`` (backups included) and every reader of the events table would
    otherwise still hold the value the redaction reported gone
    (docs/design/export.md#kyo-405). The same two rules run here, so the db and the
    ledger cannot disagree about what was removed: the id-keyed :func:`_scrub_event`,
    and — for each of ``originals`` at least :data:`MIN_GLOBAL_SCRUB_LENGTH` long — the
    whole-blob, wrap- and escape-tolerant match :func:`scrub_text_from_ledgers` applies to
    the files. Called inside the redaction's own transaction, so a failure here rolls the
    redaction back rather than half-applying it.
    """
    texts = [t for t in originals if isinstance(t, str) and len(t) >= MIN_GLOBAL_SCRUB_LENGTH]
    replacement = json.dumps(marker)[1:-1]  # the marker as it appears inside JSON
    rows = (
        store.conn.execute(
            "SELECT id, before_json, after_json, changed_json FROM events"
        ).fetchall()
        if texts
        else _event_rows_naming(store, target_id)
    )
    return sum(
        _scrub_event_row(
            store,
            row,
            texts,
            replacement,
            target_id,
            lambda event: _scrub_event(event, target_id, kind, marker),
        )
        for row in rows
    )


@dataclass(frozen=True)
class RedactionResidue:
    """Where one redacted item's text or original digest still survives, by its id.

    KYO-405: the database row can hold the marker while the ledger does not — the
    row commits first and the ledger is scrubbed after COMMIT — so a check that read only
    the row passed a half-applied redaction. ``texts`` is the item's own text as those
    surviving copies still hold it, which is what a retried redaction matches on.
    """

    ledger_lines: int
    shift_note_lines: int
    event_rows: int
    texts: tuple[str, ...]

    @property
    def total(self) -> int:
        return self.ledger_lines + self.shift_note_lines + self.event_rows


def redaction_residue(
    store: Store,
    target_id: str,
    kind: str,
    marker: str = REDACTION_MARKER,
    paths: Iterable[Path] | None = None,
    event_rows: Iterable[sqlite3.Row] | None = None,
) -> RedactionResidue:
    """Count every ledger line, shift-note ledger line and db ``events`` row that still
    carries a redacted item's text or a non-marker digest (KYO-405).

    Read-only, and keyed on the id by the same :func:`_scrub_event` the scrub uses, so
    "residue" means exactly "what a rerun of the scrub would change". Each events file is
    read whole and skipped unless the id occurs in it anywhere, and only a line that
    names the id is parsed — this runs from ``kyo doctor``, and the ledger it reads is
    the largest thing in the export. ``paths`` narrows the files read to those a
    caller already knows mention the id (:func:`shards_naming`), so a caller checking
    many ids reads the ledger once rather than once per id. ``event_rows`` does the same
    for the database's ``events`` table (:func:`event_rows_naming`, KYO-426.c4): without
    it each id is its own scan of the table.
    """
    needle = target_id.encode("utf-8")
    texts: set[str] = set()
    ledger_lines = 0
    for path in events_files(store) if paths is None else paths:
        data = path.read_bytes()
        if needle not in data:
            continue
        for number, line in enumerate(data.splitlines(), start=1):
            if needle not in line:
                continue
            event = parse_ledger_line(line, path, number)
            texts |= _residue_texts(event, target_id, kind, marker)
            if _scrub_event(event, target_id, kind, marker):
                ledger_lines += 1
    shift_note_lines = 0
    if kind == "shift-note":
        for row in _read_ledger_lines(shift_notes_path(store)):
            if row.get("id") == target_id and row.get("text") != marker:
                shift_note_lines += 1
                if isinstance(row.get("text"), str):
                    texts.add(row["text"])
    rows_changed = 0
    rows = _event_rows_naming(store, target_id) if event_rows is None else event_rows
    for row in rows:
        event = event_from_columns(row["before_json"], row["after_json"], _row_changed_json(row))
        texts |= _residue_texts(event, target_id, kind, marker)
        if _scrub_event(event, target_id, kind, marker):
            rows_changed += 1
    return RedactionResidue(ledger_lines, shift_note_lines, rows_changed, tuple(sorted(texts)))


def shards_naming(store: Store, ids: Iterable[str]) -> dict[str, list[Path]]:
    """For each id, the events files whose bytes contain it — every file read once.

    :func:`redaction_residue` reads the whole ledger per id, and ``kyo doctor`` asks it
    of every redacted row on every ``Stop`` (cost: docs/design/export.md#kyo-405). One
    pass here, then each id's residue reads only its files.
    """
    needles = {item_id: item_id.encode("utf-8") for item_id in ids}
    found: dict[str, list[Path]] = {item_id: [] for item_id in needles}
    if not needles:
        return found
    for path in events_files(store):
        data = path.read_bytes()
        for item_id, needle in needles.items():
            if needle in data:
                found[item_id].append(path)
    return found


#: KYO-426.c4: every id a snapshot carries AS an id -- a record document's own ``id``,
#: each child item's ``id`` inside it, a shift note event's ``shift_note_id``. These are
#: the only positions :func:`_scrub_event` and :func:`_scrub_field_event` key on, so a
#: row this finds nothing in is a row neither rule would change. Whitespace after the
#: colon is optional so a snapshot serialised with other separators is still read.
_SNAPSHOT_ID_RE = re.compile(r'"(?:shift_note_)?id":\s*"([^"\\]+)"')


def event_rows_naming(store: Store, needles: Iterable[str]) -> dict[str, list[sqlite3.Row]]:
    """For each id, the db ``events`` rows whose snapshots carry it as an id — one scan
    of the table for all of them, whatever their number (KYO-426.c4).

    The table-side twin of :func:`shards_naming`: one scan of the ``events`` table for
    every id, where a query per id costs a scan per id in every ``Stop`` hook. A
    substring test per id in one scan would still cost a pass over every snapshot per
    id, so each row's ids are extracted once (:data:`_SNAPSHOT_ID_RE`) and intersected
    with the wanted set instead (measurements: docs/design/export.md#kyo-426). Exact,
    not a prefix match — ``EXP-1.e1`` does not drag in ``EXP-1.e10``'s rows — and the
    scrub rule each caller applies still decides.
    """
    wanted = set(needles)
    found: dict[str, list[sqlite3.Row]] = {needle: [] for needle in wanted}
    if not wanted:
        return found
    for row in store.conn.execute(
        "SELECT id, seq, before_json, after_json, changed_json FROM events"
    ):
        named: set[str] = set()
        for column in (row["before_json"], row["after_json"], row["changed_json"]):
            if column:
                named.update(_SNAPSHOT_ID_RE.findall(column))
        for needle in named & wanted:
            found[needle].append(row)
    return found


# --- KYO-426: a record's own fields as redaction targets --------------------------------
#
# Beyond the four prose stores in :data:`REDACTABLE`, a record's title, description and
# criteria are redaction targets (docs/design/export.md#kyo-426). These three are the
# fields a record document carries as prose of its
# own. They have no ``redacted_at`` column to carry the redaction (adding one is a schema
# change for a fact the event ledger already holds), so the redaction event IS the record:
# :func:`field_redactions` reads it back, and ``kyo doctor`` checks against it.

#: ``RECORD.title`` / ``RECORD.description`` — a field target's id. A criterion is named
#: by its own child id (``RECORD.cN``).
FIELD_TARGET_RE = re.compile(r"^([A-Z][A-Z0-9]{1,15}-[1-9][0-9]*)\.(title|description)$")

#: Every field-target kind, and the db column its current value lives in.
FIELD_REDACTABLE: dict[str, tuple[str, str]] = {
    "title": ("records", "title"),
    "description": ("records", "description"),
    "criterion": ("criteria", "text"),
}

#: The command a redaction event is written with, and the pattern that reads its target
#: back — one spelling, used by the writer (`kyo redact`) and the reader
#: (:func:`field_redactions`), so the two cannot drift apart.
REDACTION_COMMAND = "redact {target} because: {reason}"
_REDACTION_COMMAND_RE = re.compile(r"^redact (\S+) because: (.*)$", re.DOTALL)


def field_target(target_id: str) -> tuple[str, str] | None:
    """``(kind, record id)`` when ``target_id`` names a record field or a criterion."""
    match = FIELD_TARGET_RE.match(target_id)
    if match:
        return match.group(2), match.group(1)
    if ids.child_kind(target_id) == "criterion":
        return "criterion", ids.record_of_child(target_id) or ""
    return None


@dataclass(frozen=True)
class FieldRedaction:
    """One field target's latest redaction, as its event records it."""

    target_id: str
    kind: str
    record_id: str
    seq: int
    why: str


def field_redactions(store: Store) -> list[FieldRedaction]:
    """The latest redaction event of every field target, oldest target first.

    Only the latest matters: every snapshot up to it is in scope for the scrub, so an
    earlier redaction of the same target is covered by a later one.
    """
    latest: dict[str, FieldRedaction] = {}
    for row in store.conn.execute(
        "SELECT seq, command FROM events WHERE command LIKE 'redact %' ORDER BY seq"
    ):
        match = _REDACTION_COMMAND_RE.match(row["command"])
        target = field_target(match.group(1)) if match else None
        if match is None or target is None:
            continue
        kind, record_id = target
        latest[match.group(1)] = FieldRedaction(
            match.group(1), kind, record_id, row["seq"], match.group(2)
        )
    return sorted(latest.values(), key=lambda item: item.seq)


def _field_values(event: dict, target_id: str, kind: str, record_id: str) -> list[tuple[dict, str]]:
    """``(container, key)`` for every place this event's snapshots hold the target field.

    A record document is the only snapshot that carries a record's title, description or
    criteria; any other payload (a shift note's, a redaction's ``{"redacted": ...}``)
    has no ``id`` equal to the record and is passed over.
    """
    places: list[tuple[dict, str]] = []
    after = event.get("after")
    for side, _column in EVENT_SNAPSHOT_SIDES:
        document = event.get(side)
        if not isinstance(document, dict):
            continue
        # KYO-418: a `changed` map carries only the keys that moved, so it has no `id`
        # of its own unless the id moved; it belongs to the record its `after` names.
        owner = (
            after.get("id") if side == EVENT_CHANGED_KEY and isinstance(after, dict) else None
        )
        if document.get("id", owner) != record_id:
            continue
        if kind == "criterion":
            for item in document.get("criteria", []) or []:
                if isinstance(item, dict) and item.get("id") == target_id and "text" in item:
                    places.append((item, "text"))
        elif kind in document:
            places.append((document, kind))
    return places


def _in_scope(event: dict, upto_seq: int | None) -> bool:
    """Whether an event precedes (or is) the redaction: a later edit's value is new text,
    not the redacted one. A line with no seq is treated as in scope — scrubbing too much
    of an unordered line is the safe error for a redaction."""
    seq = event.get("seq")
    return upto_seq is None or not isinstance(seq, int) or seq <= upto_seq


def _scrub_field_event(
    event: dict, target_id: str, kind: str, record_id: str, marker: str, upto_seq: int | None
) -> bool:
    """The field twin of :func:`_scrub_event`: every copy of the target field in a snapshot
    up to the redaction becomes the marker. True if anything changed."""
    if not _in_scope(event, upto_seq):
        return False
    touched = False
    for container, key in _field_values(event, target_id, kind, record_id):
        value = container[key]
        if isinstance(value, str) and value and value != marker:
            container[key] = marker
            touched = True
    return touched


def _field_residue_texts(
    event: dict, target_id: str, kind: str, record_id: str, marker: str, upto_seq: int | None
) -> set[str]:
    """The read-only twin of :func:`_scrub_field_event`: what it would overwrite."""
    if not _in_scope(event, upto_seq):
        return set()
    return {
        container[key]
        for container, key in _field_values(event, target_id, kind, record_id)
        if isinstance(container[key], str) and container[key] and container[key] != marker
    }


def _with_seq(row: sqlite3.Row) -> dict:
    event = event_from_columns(row["before_json"], row["after_json"], _row_changed_json(row))
    event["seq"] = row["seq"]
    return event


def field_redaction_residue(
    store: Store,
    target_id: str,
    kind: str,
    record_id: str,
    upto_seq: int | None,
    marker: str = REDACTION_MARKER,
    paths: Iterable[Path] | None = None,
    event_rows: Iterable[sqlite3.Row] | None = None,
) -> RedactionResidue:
    """Count every ledger line and db ``events`` row whose snapshot, up to the
    redaction's own event, still holds the target field's text (KYO-426.c3).

    The field twin of :func:`redaction_residue`, keyed by the same rule the scrub uses
    (:func:`_scrub_field_event`), so a finding is exactly what a rerun would change.
    ``paths``/``event_rows`` narrow the reads to what :func:`shards_naming` and
    :func:`event_rows_naming` found for the record's id in their single passes;
    ``upto_seq=None`` counts every snapshot, which is what a redaction about to record
    its event recovers the text from.
    """
    needle = record_id.encode("utf-8")
    texts: set[str] = set()
    ledger_lines = 0
    for path in events_files(store) if paths is None else paths:
        data = path.read_bytes()
        if needle not in data:
            continue
        for number, line in enumerate(data.splitlines(), start=1):
            if needle not in line:
                continue
            event = parse_ledger_line(line, path, number)
            texts |= _field_residue_texts(event, target_id, kind, record_id, marker, upto_seq)
            if _scrub_field_event(event, target_id, kind, record_id, marker, upto_seq):
                ledger_lines += 1
    rows_changed = 0
    if event_rows is None:
        event_rows = event_rows_naming(store, [record_id])[record_id]
    for row in event_rows:
        event = _with_seq(row)
        texts |= _field_residue_texts(event, target_id, kind, record_id, marker, upto_seq)
        if _scrub_field_event(event, target_id, kind, record_id, marker, upto_seq):
            rows_changed += 1
    return RedactionResidue(ledger_lines, 0, rows_changed, tuple(sorted(texts)))


def scrub_field_from_events_table(
    store: Store,
    target_id: str,
    kind: str,
    record_id: str,
    upto_seq: int,
    marker: str,
    originals: tuple[str, ...] = (),
) -> int:
    """Scrub a redacted field out of the DATABASE's ``events`` snapshots; returns how many
    rows changed (KYO-426.c1). The field twin of
    :func:`scrub_redacted_from_events_table`: the id-keyed rule over every snapshot up to
    the redaction, then, for each of ``originals`` at least
    :data:`MIN_GLOBAL_SCRUB_LENGTH` long, the whole-blob match over every row — the same
    two rules the ledger gets, so the db and the ledger cannot disagree."""
    texts = [t for t in originals if isinstance(t, str) and len(t) >= MIN_GLOBAL_SCRUB_LENGTH]
    replacement = json.dumps(marker)[1:-1]
    rows = (
        store.conn.execute(
            "SELECT id, seq, before_json, after_json, changed_json FROM events"
        ).fetchall()
        if texts
        else event_rows_naming(store, [record_id])[record_id]
    )
    return sum(
        _scrub_event_row(
            store,
            row,
            texts,
            replacement,
            record_id,
            lambda event: _scrub_field_event(
                event, target_id, kind, record_id, marker, upto_seq
            ),
        )
        for row in rows
    )


def scrub_field_from_ledgers(
    store: Store, target_id: str, kind: str, record_id: str, upto_seq: int, marker: str
) -> int:
    """Rewrite the events ledger so no snapshot up to the redaction holds the redacted
    field; returns how many lines changed (KYO-426.c1). Only a changed file is written,
    and only a changed line is re-serialised (KYO-407.c2, as :func:`_scrub_one_event_file`).
    """
    changed = 0
    needle = record_id
    with ledger_lock(export_dir(store)):
        for path in events_files(store):
            lines: list[str] = []
            file_changed = 0
            for _, raw, event in iter_ledger_lines(path):
                if needle in raw and _scrub_field_event(
                    event, target_id, kind, record_id, marker, upto_seq
                ):
                    file_changed += 1
                    lines.append(event_line(event))
                else:
                    lines.append(raw if raw.endswith("\n") else raw + "\n")
            if file_changed:
                atomic_write_text(path, "".join(lines))
            changed += file_changed
    return changed


#: KYO-28: below this length, a redacted string is too short to replace globally
#: without risking unrelated text -- replacing "42" everywhere would corrupt the store
#: far worse than leaving it (docs/design/export.md#kyo-28). Above it, an exact
#: whole-blob match is distinctive enough to remove safely.
MIN_GLOBAL_SCRUB_LENGTH = 12


def scrub_embedded_copies(store: Store, original: str, marker: str) -> tuple[int, bool]:
    """Remove verbatim copies of a redacted blob that were *embedded* elsewhere.
    Returns ``(places changed, whether the pass ran)`` (KYO-28).

    The id-based scrub reaches every place the store keeps an item *as* that item.
    It does not reach the places that copied its **text** into a larger string:

    * ``dispatches.brief`` — brief generation renders the record's description,
      criteria and **claim statements** into the frozen brief, so redacting a claim
      left the statement sitting verbatim in every dispatch that had cited it, in
      the export, in git.
    * ``reports.body_json`` — a worker's structured report, ingested verbatim.

    Both are inside the store, so both are this tool's problem
    (docs/design/export.md#kyo-28).

    The match is on the **whole** original blob, never a fragment, and is skipped
    entirely below :data:`MIN_GLOBAL_SCRUB_LENGTH`. A short value cannot be removed
    this way without hitting unrelated text, and a redaction that corrupts the store
    is not an improvement — when the pass is skipped the caller says so rather than
    implying the value is gone.

    KYO-66: "verbatim" is not *byte-identical*. A redacted blob is prose, the surfaces
    it was copied into re-wrap and re-encode it (``reports.body_json`` holds it
    JSON-escaped), and a scrub that silently finds nothing is indistinguishable from a
    scrub that had nothing to find. The match is wrap- and escape-tolerant
    (:func:`kyosei.prose.replace_phrase`); the exact form is tried first, so nothing a
    byte-identical match would scrub is missed (docs/design/export.md#kyo-66).
    """
    if len(original) < MIN_GLOBAL_SCRUB_LENGTH:
        return 0, False

    changed = 0
    for table, column in EMBEDDED_PROSE:
        rows = store.conn.execute(
            f"SELECT rowid AS row_id, {column} AS value FROM {table}"  # noqa: S608
        ).fetchall()
        for row in rows:
            text = row["value"]
            if not isinstance(text, str) or not text:
                continue
            scrubbed, _hits = _scrub_every_form(text, original, marker)
            if scrubbed != text:
                store.conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",  # noqa: S608
                    (scrubbed, row["row_id"]),
                )
                changed += 1
    return changed, True


def _scrub_every_form(text: str, original: str, marker: str) -> tuple[str, int]:
    """Replace ``original`` in ``text`` however it was re-wrapped or re-encoded on the
    way in. Returns ``(new text, occurrences replaced)`` (KYO-66).

    Three passes, narrowest first: the blob exactly as stored; the blob as JSON writes
    it (``reports.body_json`` and the JSONL ledgers hold prose escaped, and that alone
    hid every multi-line blob from the old fixed-string scrub); then the wrap-tolerant
    pattern, which catches a copy whose line breaks fall somewhere else than the
    original's. The first two are redundant with the third for ordinary prose and kept
    anyway, because they are not redundant for a blob containing a quote or a backslash
    — JSON escapes those *inside* a word, where a whitespace-tolerant pattern cannot
    reach. That residue is real and named rather than papered over: a redacted blob
    containing a ``"`` is still only removed from JSON-encoded columns when its wrapping
    is unchanged.
    """
    hits = 0
    encoded = json.dumps(original)[1:-1]
    for literal in (original, encoded):
        if literal and literal in text:
            hits += text.count(literal)
            text = text.replace(literal, marker)
    text, wrapped_hits = replace_phrase(text, original, marker)
    return text, hits + wrapped_hits


def scrub_text_from_ledgers(store: Store, original: str, marker: str) -> int:
    """Replace a redacted blob wherever it appears verbatim in the JSONL ledgers.

    Companion to :func:`scrub_embedded_copies` for the same reason: a frozen brief
    that quoted the text is itself carried inside record documents in
    ``_events.jsonl``, so fixing the database alone would leave the value in the
    file git actually tracks. Whole-blob match only, same length guard applied by
    the caller, and the same wrap- and escape-tolerant match as
    :func:`scrub_embedded_copies` (KYO-66) — one implementation, so the database copy
    and the ledger copy of a blob cannot be scrubbed by two different definitions of
    "verbatim" and disagree about whether the value is gone.

    KYO-274.c1 took the brief and the report body OUT of new snapshots, and this pass is
    why that changes nothing here rather than needing a version check. It matches TEXT in
    the file, so a line written before the widening still has the brief in it and is
    still scrubbed, and a line written after it has nothing of the brief to find — the
    two formats are mixed inside one append-only ledger by construction, and a text match
    is indifferent to which one a line is (the same argument ``_scrub_events`` makes for
    keying on an absent field instead of a version number).

    **Named residue, KYO-274.** A post-widening snapshot keeps ``brief_sha256`` /
    ``body_sha256``, and this pass does not recompute either: after an embedded copy is
    scrubbed out of ``dispatches.brief``, a snapshot's digest is of the pre-scrub brief.
    That is deliberate, and it is not the republication ``_scrub_events`` recomputes an
    evidence digest to avoid. There the digest is OF the redacted blob, so a guess at the
    blob is confirmable by hashing it; here it is of a whole multi-kilobyte brief that was
    never the redaction target, and confirming a guess would mean already holding every
    other byte of that brief. What the stale digest says is true — these are the bytes
    the snapshot saw — and a historical snapshot naming historical bytes is the ledger
    working, not leaking.
    """
    changed = 0
    replacement = json.dumps(marker)[1:-1]  # the marker as it appears inside JSON
    # KYO-268.c2: every events file, not one — see `_scrub_events`.
    with ledger_lock(export_dir(store)):
        for path in [*events_files(store), shift_notes_path(store)]:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            scrubbed, hits = _scrub_every_form(text, original, replacement)
            if scrubbed != text:
                changed += hits
                atomic_write_text(path, scrubbed)
    return changed


def _scrub_shift_notes(store: Store, target_id: str, marker: str) -> int:
    path = shift_notes_path(store)
    if not path.is_file():
        return 0
    changed = 0
    lines: list[str] = []
    with ledger_lock(path.parent):
        # KYO-407.c2: only a changed line is re-serialised, and only a changed file is
        # written -- see `_scrub_one_event_file`.
        for _, raw, row in iter_ledger_lines(path):
            if row.get("id") == target_id and row.get("text") != marker:
                row["text"] = marker
                changed += 1
                lines.append(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            else:
                lines.append(raw if raw.endswith("\n") else raw + "\n")
        if changed:
            atomic_write_text(path, "".join(lines))
    return changed
