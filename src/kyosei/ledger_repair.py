"""The ledgers against the database: the rows a ledger is missing, and the repair
that re-appends them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .contracts import REDACTABLE, REDACTION_MARKER
from .events_ledger import (
    EVENT_SNAPSHOT_SIDES,
    EVENTS_DIRNAME,
    EVENTS_FILENAME,
    append_events,
    events_files_in,
)
from .export import export_dir, parse_ledger_line
from .ledger_scrub import _scrub_event
from .ledgers import (
    BRIEFS_FILENAME,
    SHIFT_NOTES_FILENAME,
    SWEEPS_FILENAME,
    _append_ledger_line,
    _read_ledger_lines,
    _row_to_dict,
)
from .store import Store

# ------------------------------------------- KYO-404: the ledgers against the database
#
# Every ledger above is appended AFTER its transaction commits, so a kill, a SIGINT, a
# hook timeout or an exception in between leaves a row in ``kyosei.db`` that no ledger
# carries — and ``kyo rebuild`` reads only the ledgers, so the row is gone on the next
# rebuild with nothing to say it ever existed. What follows compares the two sides by
# id, and repairs by re-appending what is missing (docs/design/export.md#kyo-404).

#: Every append-only ledger paired with the table it is the export of. The keys are the
#: :data:`LEDGER_READERS` keys, so a fifth ledger added there and not here is visible
#: side by side (``tests/test_ledger_gaps.py`` asserts the two agree).
LEDGER_TABLES: dict[str, str] = {
    EVENTS_FILENAME: "events",
    SWEEPS_FILENAME: "sweep_runs",
    BRIEFS_FILENAME: "brief_runs",
    SHIFT_NOTES_FILENAME: "shift_notes",
}

#: The last keys of an event line as :func:`event_line` writes it (``sort_keys=True``):
#: ``id``, ``record_id``, the stamp when there is one, and ``seq`` — every other key
#: sorts before ``id``. Matched against the TAIL of the line only, so the id set of the
#: events ledger is read without decoding a single snapshot: that ledger is hundreds of
#: megabytes on a long-lived store, and ``kyo doctor`` runs from the session hooks. A
#: line whose tail does not match — a
#: hand-edited one, a future key order — falls back to a full parse rather than being
#: skipped, so the fast path can be wrong only by being slow.
_EVENT_TAIL_RE = re.compile(
    rb'"id": ("[^"\\]*"), "record_id": (?:null|"(?:[^"\\]|\\.)*"), '
    rb'(?:"schema_version": -?\d+, )?"seq": -?\d+\}\s*$'
)
#: How many bytes from the end of an event line the tail match looks at. The tail is
#: under a hundred bytes; the margin is for a long record id.
_EVENT_TAIL_BYTES = 512


def _event_ids_in(directory: Path) -> set[str]:
    found: set[str] = set()
    for path in events_files_in(directory):
        with path.open("rb") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                match = _EVENT_TAIL_RE.search(line, max(0, len(line) - _EVENT_TAIL_BYTES))
                if match:
                    found.add(json.loads(match.group(1)))
                else:
                    found.add(parse_ledger_line(line, path, number)["id"])
    return found


def ledger_ids_in(directory: Path, ledger: str) -> set[str]:
    """The ids ``ledger`` (a :data:`LEDGER_TABLES` key) holds under ``directory``."""
    if ledger == EVENTS_FILENAME:
        return _event_ids_in(directory)
    return {row["id"] for row in _read_ledger_lines(directory / ledger)}


_TRAILING_NUMBER_RE = re.compile(r"(\d+)$")


def _by_seq(item_id: str) -> tuple[int, str]:
    """Sort key for an id this module mints: its trailing number, which is its seq."""
    match = _TRAILING_NUMBER_RE.search(item_id)
    return (int(match.group(1)) if match else -1, item_id)


@dataclass(frozen=True)
class LedgerGap:
    """One ledger whose ids and its table's ids disagree, both ways, in seq order."""

    ledger: str
    table: str
    only_in_db: tuple[str, ...]
    only_in_ledger: tuple[str, ...]


def ledger_gaps(store: Store) -> list[LedgerGap]:
    """Every ledger whose id set differs from its table's; empty when they agree.

    Id sets only, on both sides — ``SELECT id`` and :func:`ledger_ids_in` — never a
    body, which is what keeps it affordable inside ``doctor --hook``.
    """
    directory = export_dir(store)
    gaps: list[LedgerGap] = []
    for ledger, table in LEDGER_TABLES.items():
        in_db = {
            row[0]
            for row in store.conn.execute(f"SELECT id FROM {table}")  # noqa: S608 - fixed table
        }
        in_ledger = ledger_ids_in(directory, ledger)
        if in_db == in_ledger:
            continue
        gaps.append(
            LedgerGap(
                ledger=ledger,
                table=table,
                only_in_db=tuple(sorted(in_db - in_ledger, key=_by_seq)),
                only_in_ledger=tuple(sorted(in_ledger - in_db, key=_by_seq)),
            )
        )
    return gaps


def ledger_location(ledger: str) -> str:
    """How a finding names a ledger: the events ledger is a directory of shards."""
    if ledger == EVENTS_FILENAME:
        return f"kyosei/export/{EVENTS_DIRNAME}/"
    return f"kyosei/export/{ledger}"


def _event_from_row(row: sqlite3.Row) -> dict:
    """The dict :meth:`Store.record_event` returned for this row when it was written —
    the same eight keys, the snapshots decoded back to objects — so the line
    :func:`append_events` writes from it is the line it would have written then."""
    return {
        "id": row["id"],
        "seq": row["seq"],
        "at": row["at"],
        "actor": row["actor"],
        "command": row["command"],
        "record_id": row["record_id"],
        # KYO-418: only the sides the row holds, so a row written from schema_version
        # 25 on re-appends as the `after` + `changed` line it was, not with a `before`
        # key the new format does not carry.
        **{
            side: json.loads(row[column])
            for side, column in EVENT_SNAPSHOT_SIDES
            if side != "after" and row[column] is not None
        },
        "after": None if row["after_json"] is None else json.loads(row["after_json"]),
    }


def _redacted_items(store: Store) -> list[tuple[str, str]]:
    """``(id, kind)`` for every item ``kyo redact`` has removed the text of."""
    items: list[tuple[str, str]] = []
    for kind, (table, _text, _record) in REDACTABLE.items():
        items += [
            (row[0], kind)
            for row in store.conn.execute(
                f"SELECT id FROM {table} WHERE redacted_at IS NOT NULL"  # noqa: S608
            )
        ]
    return items


_REPAIR_BATCH = 500


def repair_ledgers(store: Store) -> dict[str, int]:
    """Re-append every row the database holds and its ledger lacks; ``{table: count}``.

    KYO-404. Rows go on in seq order, each through the appender its command uses —
    :func:`append_events` for events, :func:`_append_ledger_line` for the other three —
    so each line is stamped and shaped exactly as the original append would have
    written it. A ledger that is complete appends nothing, which is what makes this
    safe to run on every ``kyo export --refresh``.

    What it never does is remove a line: an id only the LEDGER holds is the export being
    ahead of the database (a pull), which ``kyo rebuild`` answers, not this.

    **Redaction.** ``kyo redact`` rewrites the item and scrubs the ledgers, but the
    database's own ``events`` snapshots keep what they were written with. An event that
    missed the ledger and is re-appended after a redaction would carry the removed text
    back into git, so each re-appended event is scrubbed by :func:`_scrub_event` against
    every redacted item first — the same rule the redaction applied to the lines that
    were already there. A shift note's row is re-appended from the table, which already
    holds the marker.
    """
    appended: dict[str, int] = dict.fromkeys(LEDGER_TABLES.values(), 0)
    redacted: list[tuple[str, str]] | None = None
    for gap in ledger_gaps(store):
        if not gap.only_in_db:
            continue
        rows: list[sqlite3.Row] = []
        missing = list(gap.only_in_db)
        for start in range(0, len(missing), _REPAIR_BATCH):
            batch = missing[start : start + _REPAIR_BATCH]
            placeholders = ", ".join("?" for _ in batch)
            rows += store.conn.execute(
                f"SELECT * FROM {gap.table} WHERE id IN ({placeholders})",  # noqa: S608
                batch,
            ).fetchall()
        rows.sort(key=lambda row: row["seq"])
        if gap.ledger == EVENTS_FILENAME:
            if redacted is None:
                redacted = _redacted_items(store)
            events = [_event_from_row(row) for row in rows]
            for event in events:
                for target_id, kind in redacted:
                    _scrub_event(event, target_id, kind, REDACTION_MARKER)
            append_events(store, events)
        else:
            path = export_dir(store) / gap.ledger
            for row in rows:
                _append_ledger_line(path, _row_to_dict(row))
        appended[gap.table] = len(rows)
    return appended
