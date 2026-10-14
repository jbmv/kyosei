"""The allocation snapshot and the three project-wide append-only ledgers (sweeps,
briefs, shift notes), and the registry ``kyo rebuild`` reads every ledger through.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .events_ledger import EVENTS_FILENAME, read_events_in
from .export import (
    atomic_write_text,
    export_dir,
    export_file_unreadable,
    iter_ledger_lines,
    ledger_lock,
    load_json_file,
    stamp_ledger_row,
)
from .store import Store

ALLOCATIONS_FILENAME = "_allocations.json"
#: Milestone 6 (KYO-8): the three project-wide (not per-record) append-only
#: ledgers, same pattern as ``_events.jsonl`` — one JSON line per row, never
#: rewritten, read back by ``kyo rebuild``.
SWEEPS_FILENAME = "_sweeps.jsonl"
BRIEFS_FILENAME = "_briefs.jsonl"
SHIFT_NOTES_FILENAME = "_shift_notes.jsonl"


# --------------------------------------------------------------- allocation snapshot


def allocations_path_in(directory: Path) -> Path:
    return directory / ALLOCATIONS_FILENAME


def allocations_path(store: Store) -> Path:
    return allocations_path_in(export_dir(store))


def expected_allocations(store: Store) -> dict[str, int]:
    return {
        row["scope"]: int(row["next_value"])
        for row in store.conn.execute("SELECT scope, next_value FROM allocations ORDER BY scope")
    }


def serialise_allocations(allocations: dict[str, int]) -> str:
    return json.dumps(allocations, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_allocations(store: Store) -> Path:
    """Overwrite the counter snapshot. Maintained on every allocation (charter, Layer 2):

    every mutating command allocates at least one id inside its transaction, and every
    mutating command calls this once, post-commit, alongside its own export write.

    KYO-188: "alongside its own export write" is the sentence that decides this file's
    version question, and the answer is that it needs no ``schema_version`` of its own.
    Every caller of this function writes a stamped artifact in the same command, so a
    later build's counters never travel unaccompanied by a later stamp. The full
    reasoning, and the two tests that keep it true, are on
    :data:`LEDGER_READERS` below — which is where a reader working out the export
    format's versioning rules arrives.

    KYO-414: the read of the counters and the write of the file
    happen under the database's write lock. This runs after its command's commit, so
    without the lock two overlapping commands could interleave as A-commits, A-reads,
    B-commits, B-reads-and-writes, A-writes -- and A's older snapshot, written last, left
    the git-canonical file BELOW an id the ledger already held. Holding ``BEGIN
    IMMEDIATE`` across read-and-write orders every writer's snapshot after every commit
    it could have missed: a commit that lands after this read cannot land before this
    write. Nothing is written to the database, so the transaction is rolled back. Called
    with a transaction already open (none of today's callers do), it reads inside that
    one instead. Rebuild's ``max(snapshot, loaded)`` seeding is the other half: it keeps
    law 4 whatever a snapshot says, including one written before this lock existed.
    """
    path = allocations_path(store)
    own_lock = not store.conn.in_transaction
    if own_lock:
        store.conn.execute("BEGIN IMMEDIATE")
    try:
        # KYO-407.c1: replaced atomically, so a concurrent reader -- every `kyo list`
        # reads this file -- never sees half of it, and a writer killed mid-write
        # leaves the previous snapshot rather than a torn one.
        atomic_write_text(path, serialise_allocations(expected_allocations(store)))
    finally:
        if own_lock:
            store.conn.execute("ROLLBACK")
    return path


def read_allocations_in(directory: Path) -> dict[str, int]:
    """Read the counter snapshot. ``kyo rebuild`` seeds ``allocations`` from this, never
    from ``max(seq)`` alone (law 4: a rebuilt db must not be able to reissue an id whose
    highest-numbered sibling was later voided or deleted by hand) -- and, since KYO-414,
    never below ``max(seq) + 1`` either, so a snapshot left below the ledger cannot
    reissue an id the export already carries.
    """
    path = allocations_path_in(directory)
    if not path.is_file():
        return {}
    # KYO-407.c3: through the one loader, so a torn snapshot is named with its remedy.
    allocations = load_json_file(path)
    if not isinstance(allocations, dict):
        raise export_file_unreadable(path, 1, "the file is not a JSON object")
    return allocations


# ------------------------------------------------------------- M6 global ledgers

# Milestone 6 (KYO-8): sweep_runs / brief_runs / shift_notes are project-wide,
# not per-record, so (like _events.jsonl/_allocations.json) they get their own
# append-only export artifacts rather than living inside any one record's
# document. Same shape, same rationale, three times over — a helper factors
# the read/append pattern so it is written once (law 3).


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _append_ledger_line(path: Path, row: dict) -> None:
    """KYO-181: one place stamps all three of these ledgers, for the same reason one
    helper appends to them — a fourth ledger added later gets the version by using the
    mechanism, not by somebody remembering."""
    with ledger_lock(path.parent), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stamp_ledger_row(row), sort_keys=True, ensure_ascii=False) + "\n")


def _read_ledger_lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    # KYO-407.c3: a line that does not parse names its file, line and remedy.
    return [row for _, _, row in iter_ledger_lines(path)]


def sweeps_path_in(directory: Path) -> Path:
    return directory / SWEEPS_FILENAME


def sweeps_path(store: Store) -> Path:
    return sweeps_path_in(export_dir(store))


def append_sweep_run(store: Store, row: sqlite3.Row) -> None:
    _append_ledger_line(sweeps_path(store), _row_to_dict(row))


def read_sweep_runs_in(directory: Path) -> list[dict]:
    return _read_ledger_lines(sweeps_path_in(directory))


def briefs_path_in(directory: Path) -> Path:
    return directory / BRIEFS_FILENAME


def briefs_path(store: Store) -> Path:
    return briefs_path_in(export_dir(store))


def append_brief_run(store: Store, row: sqlite3.Row) -> None:
    _append_ledger_line(briefs_path(store), _row_to_dict(row))


def read_brief_runs_in(directory: Path) -> list[dict]:
    return _read_ledger_lines(briefs_path_in(directory))


def shift_notes_path_in(directory: Path) -> Path:
    return directory / SHIFT_NOTES_FILENAME


def shift_notes_path(store: Store) -> Path:
    return shift_notes_path_in(export_dir(store))


def append_shift_note(store: Store, row: sqlite3.Row) -> None:
    _append_ledger_line(shift_notes_path(store), _row_to_dict(row))


def read_shift_notes_in(directory: Path) -> list[dict]:
    return _read_ledger_lines(shift_notes_path_in(directory))


#: Every append-only JSONL ledger in the export, mapped to the function that reads it
#: back. KYO-181: declared as a table so ``kyo rebuild``'s version guard iterates the
#: ledgers rather than naming four of them, which is what makes a FIFTH ledger guarded
#: by the act of existing instead of by somebody remembering to extend a list. The
#: registry is what ``tests/test_schema_compat.py`` checks the ledger filenames against,
#: so a new ledger that is not in here fails the suite rather than shipping unversioned —
#: the same shape of mechanism as ``schema.CONFIG_CONSUMERS`` (law 2), one boundary over.
#:
#: ``_allocations.json`` is deliberately absent, and KYO-188 is where that stopped being a
#: residue and became a decision. **It carries no ``schema_version`` of its own and is not
#: going to.** The reason is not that it is un-versionable — it is that a stamp there would
#: version nothing that is not already versioned, because THIS FILE CANNOT BE WRITTEN
#: ALONE:
#:
#: Every one of the seven functions in this package that calls :func:`write_allocations`
#: also, in the same function and the same command, writes an artifact that carries this
#: build's ``schema_version`` — an event or ledger line (``append_events``,
#: ``append_sweep_run``, ``append_brief_run``, ``append_shift_note``) or a record document
#: (``write_exports``, ``refresh_exports``). That is structural rather than incidental: the
#: snapshot exists to record that an id was handed out, and handing one out is a mutation,
#: and a mutation writes an event. So an export whose counters were written by a later
#: build ALWAYS holds a later-stamped line or document beside them, and ``kyo rebuild``
#: refuses the whole export on that signal (``rebuild.read_ledger_rows`` /
#: ``read_record_documents``) before any counter is read. A stamp here would be a second
#: witness to a fact the first witness already reports.
#:
#: Against that, a stamp would put a metadata key into a mapping every consumer reads as
#: scope name -> counter (docs/design/export.md#kyo-188 names them and the cost).
#:
#: ``tests/test_schema_compat.py`` pins BOTH halves, because the argument above is only
#: sound while its premise holds: ``test_the_ledgers_and_the_record_documents_both_carry_a
#: _schema_version`` asserts the absence, and ``test_nothing_writes_the_allocation_snapshot
#: _without_writing_a_stamped_artifact_beside_it`` asserts the co-write that makes the
#: absence safe. Add an eighth writer that touches this file alone and the second one fails,
#: which is the moment this decision would have to be reopened.
#: KYO-268.c2: the events entry stays keyed on ``_events.jsonl`` after that file stopped
#: being where the bytes are, and the key is now the ledger's NAME rather than its path.
#: Two reasons, and the second is the load-bearing one. The registry's keys are what
#: ``test_every_jsonl_ledger_the_export_declares_is_read_through_the_registry`` compares
#: against every ``*_FILENAME`` constant in this module, so a fifth ledger is still
#: guarded by existing; and a shard IS a ``_events.jsonl``-format file — same one line per
#: event, same stamp, same reader — so the format the key names is exactly the format
#: behind it. What the key no longer promises is that a single file of that name exists;
#: :func:`events_files_in` is the authority on which files the ledger is made of.
LEDGER_READERS = {
    EVENTS_FILENAME: read_events_in,
    SWEEPS_FILENAME: read_sweep_runs_in,
    BRIEFS_FILENAME: read_brief_runs_in,
    SHIFT_NOTES_FILENAME: read_shift_notes_in,
}
