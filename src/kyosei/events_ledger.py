"""The events ledger: what an event line carries, where the shards live, and how
lines are appended, read back and migrated.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from .errors import KyoseiError
from .export import _digest, export_dir, iter_ledger_lines, ledger_lock, stamp_ledger_row
from .store import Store

#: The events ledger's canonical NAME. Until schema_version 20 it was also the whole of
#: the ledger: one file, in the export root. From 20 on the bytes live in
#: :data:`EVENTS_DIRNAME` and this names the pre-migration file, which
#: :func:`events_files_in` still reads and `kyo export --migrate-events` removes. It is
#: also the key the ledger is registered under in :data:`LEDGER_READERS` — see the note
#: there on why the registry stays keyed on a filename.
EVENTS_FILENAME = "_events.jsonl"
#: The directory the events ledger is written to, one bounded shard at a time. A
#: DIRECTORY rather than a file because git stores a changed tracked file as a whole new
#: blob, so a single append-only ledger costs its whole size on every store commit. Under
#: a directory of shards a mutation rewrites exactly one shard, and every closed shard is
#: a blob git already has. See docs/design/export.md#kyo-268.
EVENTS_DIRNAME = "_events"
#: Deliberately the same extension the monolithic ledger had: a shard is the same format,
#: one JSON line per event, and a reader that learned to read ``_events.jsonl`` reads a
#: shard unchanged.
EVENTS_SHARD_SUFFIX = ".jsonl"
#: A shard is closed once appending the next line would take it past this.
#:
#: The number is a trade between the two costs the shard layout sits between. TOO LARGE
#: and every commit that touches the store re-stores the whole open shard, so the open
#: shard's size IS the per-commit cost. TOO SMALL and the ledger becomes tens of thousands
#: of files that `kyo rebuild` opens on every run. It must also stay under git's
#: ``core.bigFileThreshold`` (and any lower value a store sets), so a shard is still
#: delta-compressed against its own earlier versions. Why 4 MiB in particular:
#: docs/design/export.md#kyo-268.
EVENTS_SHARD_MAX_BYTES = 4 * 1024 * 1024


# ------------------------------------------------------------------- event ledger

# Path-only helpers (``events_path_in`` / ``allocations_path_in``) take a bare export
# directory rather than a ``Store`` so ``kyo rebuild`` can read them *before* a database
# — and therefore a ``Store`` — exists. The ``Store``-taking wrappers below are what every
# other caller uses.


#: What an event's ``before``/``after`` keeps of an evidence artifact — its id and the
#: sha256 of its body, and nothing else.
#:
#: A record document embeds every evidence artifact's ``text`` verbatim, so a snapshot
#: that kept it would copy every artifact into the ledger on every mutation of its record.
#: The bodies already live, once, on the record's own export document, with the sha that
#: identifies them; the ledger carrying them again would be a second copy of a thing the
#: design stores once (law 3). What that cost: docs/design/export.md#kyo-268.
#:
#: ``sha256`` is kept rather than dropped with the rest because it is what makes the
#: reference *checkable*: a snapshot that named only an id would say an artifact was
#: attached, not which bytes it was. It is also what ``kyo redact``'s ledger scrub
#: rewrites — the digest of a redacted artifact is recomputed over the marker precisely
#: so the original is not republished (docs/reference/topics/store.md, "The store"), and a
#: snapshot that kept the
#: pre-redaction digest would be the republication the scrub exists to prevent.
#:
#: Everything else an artifact carries (``seq``, ``supports_id``, ``author``,
#: ``created_at``, ``redacted_at``, ``redacted_why``) is dropped, because evidence is
#: append-only and never deleted: each of those values is still readable, unchanged, on
#: the record's own export document, which the id in the snapshot is the key into.
EVENT_SNAPSHOT_EVIDENCE_KEYS = ("id", "sha256")

#: The document key those references live under — the same list
#: :func:`record_document` writes and :func:`_scrub_events` scrubs, named once so the
#: stripper and the scrubber cannot drift to two spellings of it (law 3).
EVIDENCE_COLLECTION = "evidence_artifacts"

#: The other two bodies a record document embeds, and what a snapshot keeps of each —
#: ``(collection, body key, digest key)``.
#:
#: A dispatch carries its **frozen brief** verbatim (``dispatches[].brief``) and a report
#: carries the worker's **ingested report** verbatim (``reports[].body``), so a snapshot
#: of the whole document would copy both into the ledger on every mutation. Same
#: mechanism as evidence, so it is the same stripper: this table and
#: :data:`EVENT_SNAPSHOT_EVIDENCE_KEYS` are both read by :func:`event_snapshot` and by
#: nothing else, which is what keeps the three treatments from drifting apart. See
#: docs/design/export.md#kyo-274.
#:
#: A digest and not a bare id, for the reason evidence keeps ``sha256``: a snapshot that
#: named only the dispatch would say a brief existed, not which bytes it was. Unlike
#: evidence the digest is COMPUTED here rather than read from a stored column — neither
#: ``dispatches`` nor ``reports`` has ever had a sha column — so it is a digest of the
#: body as the record document held it at that instant, recomputable by any reader that
#: hashes the body on the current document (:func:`body_digest`).
#:
#: Only the body is dropped, where an evidence artifact is reduced to two keys, and the
#: difference is that an evidence artifact is immutable and a dispatch row is not. A
#: dispatch moves ``running`` -> ``reported``/``terminated-no-report``/``withdrawn``,
#: gains a ``report_id`` and a ``closed_at``; the record's own export document shows only
#: where it ended up, so a snapshot reduced to ``{id, sha}`` would delete the only record
#: of the transition — which is the question the ledger exists to answer (law 6). Those
#: fields are ids, enums, shas and timestamps; the bytes are all in the body, and the
#: body is what leaves. (KYO-345 added the third of those destinations and needed no
#: change here, which is the property keeping the non-body fields bought: a snapshot that
#: carries `state` whole carries a value this build's enum does not have to know.)
EVENT_SNAPSHOT_BODIES = (
    ("dispatches", "brief", "brief_sha256"),
    ("reports", "body", "body_sha256"),
)


def body_digest(body: object) -> str | None:
    """The sha256 a snapshot names an embedded body by (KYO-274.c1).

    A frozen brief is a string; an ingested report is the parsed JSON object
    ``_report`` puts in the document. The object is hashed over its canonical
    serialisation — the same ``sort_keys``/``ensure_ascii=False`` form :func:`serialise`
    writes the export in — so the digest is a property of the document a reader holds,
    not of whatever byte order a particular json dump happened to choose.
    """
    if body is None:
        return None
    text = body if isinstance(body, str) else json.dumps(body, sort_keys=True, ensure_ascii=False)
    return _digest(text)


def _named_not_carried(row: dict, body_key: str, digest_key: str) -> dict:
    """One row of an :data:`EVENT_SNAPSHOT_BODIES` collection with its body replaced by
    that body's digest. A row that has no body key is copied unchanged — an event
    snapshot taken from a partially built document is still a snapshot."""
    if body_key not in row:
        return dict(row)
    reduced = {key: value for key, value in row.items() if key != body_key}
    reduced[digest_key] = body_digest(row[body_key])
    return reduced


def event_snapshot(document: dict | None) -> dict | None:
    """The form of a record document that goes into an event's ``before``/``after``.

    KYO-268.c1, widened to briefs and reports by KYO-274.c1. Returns a NEW dict — never
    mutates its argument — because the caller (``commands._commit_and_export``) hands the
    same post-mutation document to :meth:`kyosei.store.Store.record_event` and to the
    command's own JSON payload, and the payload is the full document.

    Applied at :meth:`kyosei.store.Store.record_event`, the one door every event goes
    through, rather than at the seven call sites that reach it: a snapshot that kept the
    bodies would be indistinguishable from one that did not until the ledger was measured
    in gigabytes. Payloads that are not record documents (``kyo redact``'s
    ``{"redacted": ...}``, a stop audit's ``{"stop_audit": ...}``, ``None``) pass through
    untouched — there is no body-carrying list in them to strip.
    """
    if not isinstance(document, dict):
        return document
    stripped = dict(document)
    artifacts = stripped.get(EVIDENCE_COLLECTION)
    if isinstance(artifacts, list):
        stripped[EVIDENCE_COLLECTION] = [
            {key: item.get(key) for key in EVENT_SNAPSHOT_EVIDENCE_KEYS}
            if isinstance(item, dict)
            else item
            for item in artifacts
        ]
    for collection, body_key, digest_key in EVENT_SNAPSHOT_BODIES:
        rows = stripped.get(collection)
        if not isinstance(rows, list):
            continue
        stripped[collection] = [
            _named_not_carried(row, body_key, digest_key) if isinstance(row, dict) else row
            for row in rows
        ]
    return stripped


# ------------------------------------------ KYO-418: an event records what CHANGED
#
# Before schema_version 25 an event carried the whole record document twice, as
# ``before`` and ``after`` (docs/design/export.md#kyo-418). From 25 on an event
# carries ``after`` whole and ``changed`` -- the PRIOR value of each top-level key whose
# value differs -- and no ``before``. ``after`` stays whole because it is what the
# readers of a snapshot's own state read (``kyo redact``'s scrubs, the stop-audit
# reader, the changelog's status test), and ``changed`` is all the before/after readers
# ever used: which keys moved, and from what.
#
# **Keyed on content, never on a version.** No existing line is rewritten (charter,
# Layer 2), so both formats sit in one ledger forever: a line that has ``before`` is the
# old format, a line that has ``changed`` is the new one, and a line with neither had no
# prior document (a creation, or a payload that is not a record's). Every reader goes
# through :func:`event_prior` or :func:`event_before_document`, which answer the same
# question from either shape.
#
# **Absent and null are the same value here**, and that is the reading every existing
# reader already had: ``store.description_edited_at`` compared ``json_extract`` of both
# sides (SQL NULL for either), and the Python readers compared ``dict.get``. So a key
# absent from the prior document and present in ``after`` records a prior of ``null``,
# and a key present before and absent after records its prior value. Nothing is lost
# that any reader could see.

#: The key a KYO-418 event line carries the prior values under.
EVENT_CHANGED_KEY = "changed"

#: Every snapshot side an event line can carry, paired with the ``events`` column that
#: stores it. ``before`` is only ever on a line written before schema_version 25;
#: ``changed`` only on one written from 25 on; ``after`` on both.
EVENT_SNAPSHOT_SIDES: tuple[tuple[str, str], ...] = (
    ("before", "before_json"),
    (EVENT_CHANGED_KEY, "changed_json"),
    ("after", "after_json"),
)


def event_diff(before: dict, after: dict | None) -> dict:
    """The prior value of every top-level key whose value differs between ``before``
    and ``after`` (KYO-418) — ``changed`` as a line stores it. An ``after`` of ``None``
    (a payload that no longer exists) differs in every key ``before`` held a value in."""
    current = after if isinstance(after, dict) else {}
    return {
        key: before.get(key)
        for key in sorted(set(before) | set(current))
        if before.get(key) != current.get(key)
    }


def event_prior(event: dict) -> dict | None:
    """The prior value of each top-level key this event changed, whichever format the
    event was written in (KYO-418.c3). ``None`` when the event has no prior document —
    a creation, or a payload that is not a record's.

    An old line's full ``before`` is reduced by :func:`event_diff` against its
    ``after``; a new line's ``changed`` IS that reduction, stored. The two answers are
    identical for the same mutation, which is what lets a reader ask this and never ask
    which format it is holding.
    """
    before = event.get("before")
    if isinstance(before, dict):
        after = event.get("after")
        return event_diff(before, after if isinstance(after, dict) else None)
    changed = event.get(EVENT_CHANGED_KEY)
    return dict(changed) if isinstance(changed, dict) else None


def event_before_document(event: dict) -> dict | None:
    """The whole prior document, whichever format the event was written in: an old
    line's ``before`` as stored, a new line's ``after`` with ``changed`` laid over it.
    ``None`` when there was no prior document. Equal to the old ``before`` under the
    absent-is-null reading every reader of it already used (see the note above)."""
    before = event.get("before")
    if isinstance(before, dict):
        return before
    changed = event.get(EVENT_CHANGED_KEY)
    if not isinstance(changed, dict):
        return None
    after = event.get("after")
    return {**(after if isinstance(after, dict) else {}), **changed}


def event_from_columns(
    before_json: str | None, after_json: str | None, changed_json: str | None = None
) -> dict:
    """An ``events`` row's snapshot columns as the sides a ledger line carries —
    ``changed`` only when the row has one, so an old row reads as an old line."""
    event: dict = {
        "before": None if before_json is None else json.loads(before_json),
        "after": None if after_json is None else json.loads(after_json),
    }
    if changed_json is not None:
        event[EVENT_CHANGED_KEY] = json.loads(changed_json)
    return event


def _row_changed_json(row: sqlite3.Row) -> str | None:
    """``changed_json`` of an ``events`` row, or ``None`` for a row selected without it."""
    return row["changed_json"] if "changed_json" in row.keys() else None  # noqa: SIM118


def _event_column_values(event: dict) -> tuple[str | None, str | None, str | None]:
    """``(before_json, changed_json, after_json)`` for writing an event's sides back."""
    before, changed, after = (
        None if event.get(side) is None else json.dumps(event[side], sort_keys=True)
        for side, _column in EVENT_SNAPSHOT_SIDES
    )
    return before, changed, after


def events_path_in(directory: Path) -> Path:
    """The MONOLITHIC events ledger — the single ``_events.jsonl`` every export was
    written as before schema_version 20.

    Still named, and still read (:func:`events_files_in`), because an export that has not
    been migrated yet is an ordinary state and not an error; nothing appends to it any
    more. :func:`events_dir_in` is where new lines go, and `kyo export --migrate-events`
    is what converts one to the other.
    """
    return directory / EVENTS_FILENAME


def events_path(store: Store) -> Path:
    return events_path_in(export_dir(store))


def events_dir_in(directory: Path) -> Path:
    return directory / EVENTS_DIRNAME


def events_dir(store: Store) -> Path:
    return events_dir_in(export_dir(store))


def event_shard_name(index: int) -> str:
    """The filename of shard ``index``, zero-padded so lexical order IS append order.

    Six digits because sort order is the only ordering this ledger has: a reader lists
    the directory and concatenates, so ``10.jsonl`` sorting before ``9.jsonl`` would
    silently reorder history. Six is enough for a hundred thousand shards of
    :data:`EVENTS_SHARD_MAX_BYTES` each — about 100 GB of ledger — and a store that
    somehow passes it still sorts correctly, because the padding only ever grows.
    """
    return f"{index:06d}{EVENTS_SHARD_SUFFIX}"


def event_shard_paths_in(directory: Path) -> list[Path]:
    """Every shard of the sharded ledger, in append order."""
    shards = events_dir_in(directory)
    if not shards.is_dir():
        return []
    return sorted(
        p for p in shards.iterdir() if p.is_file() and p.name.endswith(EVENTS_SHARD_SUFFIX)
    )


def events_files_in(directory: Path) -> list[Path]:
    """Every file the events ledger is made of, oldest first.

    KYO-268.c2/c3. The monolithic ``_events.jsonl`` comes first and the shards follow,
    which is the only order the two can be in: nothing has appended to the monolithic
    file since this build, so every line in it predates every line in a shard. That is
    what lets an un-migrated export keep working while new events land in shards — the
    transition is a state the reader handles, not a step the reader requires.
    """
    monolithic = events_path_in(directory)
    files = [monolithic] if monolithic.is_file() else []
    return files + event_shard_paths_in(directory)


def events_files(store: Store) -> list[Path]:
    return events_files_in(export_dir(store))


def event_line(event: dict) -> str:
    return json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"


def shard_to_append_to(directory: Path, incoming_bytes: int) -> Path:
    """Which shard the next line goes in — the shard rule, stated once (KYO-268.c2).

    Append to the newest shard until adding this line would take it past
    :data:`EVENTS_SHARD_MAX_BYTES`, then open the next one. A line is never split, so a
    single event larger than the limit gets a shard to itself rather than an infinite
    loop: the size test only fires against a shard that already has something in it.

    This is the whole of the rule, deliberately. Closing on a line COUNT would bound the
    number of events in a shard while leaving its size free to follow the widest event in
    it, and the cost sharding exists to bound is measured in bytes, not rows
    (docs/design/export.md#kyo-268).
    """
    shards = event_shard_paths_in(directory)
    if not shards:
        return events_dir_in(directory) / event_shard_name(1)
    newest = shards[-1]
    size = newest.stat().st_size
    if size and size + incoming_bytes > EVENTS_SHARD_MAX_BYTES:
        # Numbered from the newest shard's own name rather than from how many shards
        # there are: the two agree in the ordinary case and disagree exactly when one has
        # been removed by hand, where counting would hand back a name that is already
        # taken and append this line into the middle of history.
        return events_dir_in(directory) / event_shard_name(_shard_index(newest) + 1)
    return newest


def _shard_index(path: Path) -> int:
    """The number in a shard's filename, or 0 for a name that does not carry one."""
    stem = path.name[: -len(EVENTS_SHARD_SUFFIX)]
    return int(stem) if stem.isdigit() else 0


def append_events(store: Store, events: list[dict]) -> None:
    """Append one JSON line per event, to the newest shard (charter, Layer 2:
    append-only, never rewritten).

    KYO-181: each line is stamped with this build's ``schema_version`` on its way out.
    Stamped here rather than in :func:`event_line`, because that function is also how
    ``kyo redact`` re-serialises lines that already exist — see
    :func:`stamp_ledger_row` for why re-stamping those would be a lie about history.

    KYO-268.c2: the destination is a shard under ``_events/`` rather than one
    ``_events.jsonl``, so what a commit re-stores is bounded by
    :data:`EVENTS_SHARD_MAX_BYTES` instead of by the whole history. Appending to a shard
    is the same physical operation appending to the file was; what changed is only which
    file, and :func:`shard_to_append_to` is the one place that decides.
    """
    if not events:
        return
    directory = export_dir(store)
    # KYO-407.c7: under the ledger lock, and the shard chosen inside it, so an append
    # that waited on a running `kyo export --migrate-events` lands in the shards that
    # migration produced rather than in the directory it renamed aside and deleted.
    with ledger_lock(directory):
        events_dir_in(directory).mkdir(parents=True, exist_ok=True)
        for event in events:
            line = event_line(stamp_ledger_row(event))
            path = shard_to_append_to(directory, len(line.encode("utf-8")))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def read_events_in(directory: Path) -> list[dict]:
    """Read every event ever appended, in order. Used by ``kyo rebuild``.

    KYO-268.c2: "in order" now spans files — the monolithic ``_events.jsonl`` if the
    export still has one, then every shard in :data:`EVENTS_DIRNAME`, in name order
    (:func:`events_files_in`). Still the single reader registered in
    :data:`LEDGER_READERS`, so rebuild's version guard covers a shard by the act of
    reading it and nothing had to learn a second path.
    """
    events: list[dict] = []
    for path in events_files_in(directory):
        # KYO-407.c3: a line that does not parse names its file, line and remedy.
        events.extend(row for _, _, row in iter_ledger_lines(path))
    return events


def count_event_lines_in(directory: Path) -> int:
    """How many events the ledger holds, across every file it is made of.

    KYO-268.c2: `kyo check-push` counts what a push would publish, and after sharding
    that is no longer one file's line count (``cmd_publish.SURFACE_COUNTERS``).
    """
    total = 0
    for path in events_files_in(directory):
        with path.open(encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def migrate_events_to_shards(directory: Path) -> dict:
    """Rewrite a monolithic ``_events.jsonl`` as bounded shards, in one run (KYO-268.c3).

    Returns ``{"events": n, "shards": [names], "migrated": bool, "bytes": n}``.

    **Lossless by construction, and that is the whole design.** Every line is moved
    VERBATIM — read as text, written as text, never parsed and re-serialised. A line
    keeps the ``schema_version`` it was written at (``stamp_ledger_row`` would overwrite
    it, which KYO-181 calls a lie about history), keeps any key a later build wrote that
    this one does not read, and keeps its byte-for-byte form, so a rebuild before the
    migration and a rebuild after it read the same characters.

    **Everything is rewritten, not just the monolithic file.** A build at
    schema_version 20 appends to shards whether or not the old file has been migrated, so
    an export can legitimately hold both — and the monolithic lines are all older than
    every shard line. Packing them in order therefore means re-laying every shard, which
    is why this reads the whole ledger through :func:`events_files_in` and writes it out
    from empty. It happens once per store, deliberately, at a command the maintainer
    runs.

    **Streamed, one line at a time, and that is not an optimisation.** The ledgers this
    command exists for are hundreds of megabytes, on machines short of memory already;
    holding the whole file doubles the peak resident set (measured:
    docs/design/export.md#kyo-268). Nothing bigger than one line is held, and
    ``tests/test_export.py`` asserts that against this function's AST rather than by
    measuring, because a measurement on a small fixture passes whatever the code does.

    **The swap, and which half-finished state it leaves.** Shards are written into
    ``_events.migrating/``, the existing shard directory is renamed aside, the new one is
    renamed into place, and the monolithic file is unlinked last. Every step after the
    write is a rename or an unlink, so the window in which this can be interrupted is one
    syscall wide, and the state it leaves is both layouts present — the shards holding
    everything and the monolithic file holding its own lines again. That state is
    duplicated rather than missing, which is the direction to fail in: a duplicate event id
    stops ``kyo rebuild`` on its primary key, loudly, at the next run, where a gap would
    load clean and be a hole in the audit log nobody could see.

    **Re-running from an interrupted state (KYO-407.c5/c6).** Each interrupted state is
    recognised on entry, by :func:`_recover_interrupted_migration`, before anything is
    written (docs/design/export.md#kyo-407):

    * killed between the two renames, ``_events/`` is absent and ``_events.superseded/``
      holds the only copy of every event the shards had. It is renamed BACK, never
      deleted.
    * killed after the new shards were renamed into place but before the monolithic
      file was unlinked, the shards already begin with the monolithic file's first line
      (packing writes the monolithic lines first, and nothing else can put that line
      there). Re-packing would have laid the monolithic lines down a second time, so the
      pack is skipped and only the unlink and the cleanup are finished.

    **Held under :func:`ledger_lock` for the whole run (KYO-407.c7)**, the lock
    :func:`append_events` also takes, so a hook appending while this runs waits and then
    lands in the migrated shards instead of in a directory about to be deleted.
    """
    with ledger_lock(directory):
        monolithic = events_path_in(directory)
        already_packed = _recover_interrupted_migration(directory)
        existing = event_shard_paths_in(directory)
        if not monolithic.is_file() or already_packed:
            if already_packed:
                # c5: the shards hold everything; finish the step the interruption cut off.
                monolithic.unlink()
            return {
                "events": count_event_lines_in(directory),
                "shards": [p.name for p in existing],
                "migrated": already_packed,
                "bytes": sum(p.stat().st_size for p in existing),
            }

        staging = directory / f"{EVENTS_DIRNAME}.migrating"
        superseded = directory / f"{EVENTS_DIRNAME}.superseded"
        if superseded.exists():
            raise KyoseiError(
                f"{superseded} and {events_dir_in(directory)} both exist, and the shards do not "
                f"begin with {monolithic}'s first line, so this is not a state an interrupted "
                "events migration leaves. Nothing was changed; compare the two "
                "directories and remove the one that is not the ledger before running it again."
            )
        staging.mkdir(parents=True)

        total = 0
        index = 1
        written = 0
        handle = None
        try:
            for path in events_files_in(directory):
                with path.open(encoding="utf-8") as source:
                    for raw in source:
                        line = raw.strip()
                        if not line:
                            continue
                        payload = (line + "\n").encode("utf-8")
                        if handle is None:
                            handle = (staging / event_shard_name(index)).open("w", encoding="utf-8")
                        elif written + len(payload) > EVENTS_SHARD_MAX_BYTES:
                            handle.close()
                            index += 1
                            written = 0
                            handle = (staging / event_shard_name(index)).open("w", encoding="utf-8")
                        handle.write(line + "\n")
                        written += len(payload)
                        total += 1
        finally:
            if handle is not None:
                handle.close()

        # An empty ledger migrates to no shards at all rather than to one empty file: a
        # zero-line shard is a file git would track saying nothing, and the next append
        # creates the shard it needs anyway.
        shards = events_dir_in(directory)
        if shards.exists():
            shards.rename(superseded)
        staging.rename(shards)
        monolithic.unlink()
        shutil.rmtree(superseded, ignore_errors=True)

        produced = event_shard_paths_in(directory)
        return {
            "events": total,
            "shards": [p.name for p in produced],
            "migrated": True,
            "bytes": sum(p.stat().st_size for p in produced),
        }


def _first_ledger_line(path: Path) -> str | None:
    """The first non-blank line of a ledger file, stripped; None for an empty file."""
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            if raw.strip():
                return raw.strip()
    return None


def _recover_interrupted_migration(directory: Path) -> bool:
    """Put an interrupted migration back into a state a fresh run handles; returns
    whether the shards ALREADY hold the monolithic file's lines (KYO-407.c5/c6)."""
    monolithic = events_path_in(directory)
    shards = events_dir_in(directory)
    staging = directory / f"{EVENTS_DIRNAME}.migrating"
    superseded = directory / f"{EVENTS_DIRNAME}.superseded"
    # Staging is never live: it only becomes the ledger by being renamed, so a staging
    # directory that is still here was never renamed and holds nothing only it has.
    shutil.rmtree(staging, ignore_errors=True)
    if superseded.exists() and not shards.exists():
        # c6: killed between `shards.rename(superseded)` and `staging.rename(shards)`.
        superseded.rename(shards)
    packed = False
    if monolithic.is_file():
        first = _first_ledger_line(monolithic)
        existing = event_shard_paths_in(directory)
        packed = first is not None and bool(existing) and _first_ledger_line(existing[0]) == first
    if superseded.exists() and (packed or not monolithic.is_file()):
        # Both directories and the shards already complete: the superseded one is the
        # pre-migration copy of lines the new shards carry, and only the cleanup was
        # left undone. With the pack NOT complete, it is left where it is.
        shutil.rmtree(superseded, ignore_errors=True)
    return packed


def read_events(store: Store) -> list[dict]:
    return read_events_in(export_dir(store))
