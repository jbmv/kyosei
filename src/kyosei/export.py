"""Deterministic per-record JSON export.

The database is authoritative; the export exists so every change stays a reviewable
git diff — the one property of plain-files-in-git that demonstrably worked and must
not be lost (charter, Layer 2).

Determinism is the whole contract: the document is a pure function of the database,
key order is fixed, and ``kyo doctor`` re-derives every file and fails on any
byte-level mismatch. Nothing is ever hand-edited here.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from . import ids
from .errors import KyoseiError
from .schema import SCHEMA_VERSION
from .store import Store

try:  # pragma: no cover - the platform branch is not the thing under test
    import fcntl
except ImportError:  # pragma: no cover - no POSIX advisory locks (Windows)
    fcntl = None  # type: ignore[assignment]

EXPORT_SUFFIX = ".json"

#: The key :func:`record_document` stamps into every per-record document. Named once,
#: here, beside the code that writes it — a reader that spelled it separately would be
#: the second copy of the format's own name (law 3).
DOCUMENT_VERSION_KEY = "schema_version"

#: What a document carrying no :data:`DOCUMENT_VERSION_KEY` at all was written at. The
#: stamp predates the version discipline KYO-3 introduced but not the format itself, so
#: an absent key means version 1 — the same reading
#: :func:`kyosei.schema.apply_export_defaults` gives it, and deliberately the same
#: number: these two are the only readers of this key, and if they ever disagreed about
#: what its absence means, the defaults table and the guard below would be compensating
#: for different formats on the same file.
PRE_VERSIONED_EXPORT_SCHEMA_VERSION = 1

#: KYO-181: the key every LEDGER line carries from schema_version 17 on. Deliberately
#: the same spelling as :data:`DOCUMENT_VERSION_KEY`, and deliberately an alias of it
#: rather than a second string literal: there is one export format and it has one
#: version, so the two carriers of its stamp must not be able to drift to two different
#: key names (law 3). Two names exist at all only because the two carriers have
#: different pre-versioned readings — see :data:`PRE_STAMPED_LEDGER_SCHEMA_VERSION`.
LEDGER_VERSION_KEY = DOCUMENT_VERSION_KEY

#: What a ledger line carrying no :data:`LEDGER_VERSION_KEY` was written at.
#:
#: KYO-181. Unlike a record document — where an absent stamp means version 1, because
#: documents have carried one since the format's second version — a ledger line has
#: NEVER carried a stamp until this bump, so an absent key means "written by some build
#: at 16 or earlier" and 16 is the newest thing it can be. That is the only number this
#: constant may be: the guard's whole job is to refuse a line from a LATER build, so
#: reading an absence as the highest version it could possibly be is the direction that
#: cannot manufacture a refusal. Nothing else reads it — the pre-10 and pre-12 ledger
#: tolerances (``schema.SWEEP_RUN_FIELD_DEFAULTS``, ``schema.BRIEF_RUN_FIELD_DEFAULTS``)
#: are keyed on the absence of the FIELD, not on this number, and deliberately stayed
#: that way so an unstamped line is tolerated by exactly the machinery that always
#: tolerated it.
PRE_STAMPED_LEDGER_SCHEMA_VERSION = 16


# ------------------------------------------ KYO-407: atomic writes, one lock, one loader

#: KYO-407: the suffix every temporary file :func:`atomic_write_text` creates carries.
#: Named once because it is also a gitignore pattern (``scaffold.store_gitignore_text``
#: derives ``*`` + this): a writer killed between creating its temporary file and
#: renaming it over the target leaves the temporary behind, and a leftover nobody
#: ignored is committed by the controller's next ``git add kyosei/``. Distinct from every
#: suffix a reader of this directory lists by (``.json``, ``.jsonl``), so a leftover is
#: never mistaken for a record document or a shard.
ATOMIC_TEMP_SUFFIX = ".kyosei-tmp"


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` so no reader can ever see a partial file (KYO-407).

    ``Path.write_text`` truncates and then writes, so for the length of the write the
    file on disk is a prefix of the new content (docs/design/export.md#kyo-407) -- and a writer
    killed mid-write (a hook at its timeout, a Ctrl-C) leaves that prefix behind for
    every later command to crash on. Here the new bytes go to a temporary file in the
    SAME directory (so the rename below never crosses a filesystem), are fsynced, and
    are then ``os.replace``-d over the target, which POSIX makes atomic: a reader opens
    either the whole old file or the whole new one.

    The temporary is created with ``mkstemp``'s private mode and given the mode the
    target already has (or the process umask's default for a new file) before the
    rename, so an atomic write does not quietly tighten a tracked file's permissions.
    Any failure removes the temporary and re-raises; the target is untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=ATOMIC_TEMP_SUFFIX
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            mode = path.stat().st_mode & 0o7777
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


_LOCK_DEPTH = threading.local()


@contextlib.contextmanager
def ledger_lock(directory: Path) -> Iterator[None]:
    """Hold the export directory's ledger lock (KYO-407.c7).

    Taken by every writer that appends to or rewrites a ledger file --
    :func:`append_events`, :func:`_append_ledger_line`, the redact scrubs -- and by
    :func:`migrate_events_to_shards` for its whole run. Without it, a hook appending to
    ``_events/`` during the migration's copy wrote into the directory that is then
    renamed aside and deleted, and a scrub that read a shard before an append and
    replaced it after dropped the appended line.

    An advisory ``flock`` on the export DIRECTORY itself rather than on a lock file:
    a lock file would be one more untracked name in a git-canonical directory, and a
    directory always exists by the time anything writes into it. Re-entrant within a
    thread (a scrub that appends, or a caller already holding it, does not deadlock on
    its own lock: ``flock`` locks belong to an open file description, so a second open
    in the same process would wait on the first forever). A no-op where ``fcntl`` does
    not exist.
    """
    directory.mkdir(parents=True, exist_ok=True)
    key = str(directory.resolve())
    depths: dict[str, int] = getattr(_LOCK_DEPTH, "depths", None) or {}
    _LOCK_DEPTH.depths = depths
    if depths.get(key) or fcntl is None:
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        depths[key] = 1
        try:
            yield
        finally:
            depths[key] = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class ExportFileUnreadable(KyoseiError, ValueError):
    """An export file that does not parse (KYO-407.c3).

    A :class:`KyoseiError`, so the CLI reports it as a message rather than a traceback;
    ALSO a ``ValueError`` -- which ``json.JSONDecodeError`` is -- so every caller that
    already tolerated a bad line by catching ``ValueError`` (doctor's ledger-gap check
    reports it as a finding instead of crashing the run) keeps doing exactly that.
    """


def _restore_command(path: Path) -> str:
    """``git -C <dir> checkout -- <name>``: runnable from any working directory.

    A path relative to the process's cwd would be right only when run from the one
    directory the message was printed in, and the paths involved contain spaces often
    enough (this repository's own worktrees do) that every part is shell-quoted."""
    return f"git -C {shlex.quote(str(path.parent))} checkout -- {shlex.quote(path.name)}"


def export_file_unreadable(path: Path, line: int, why: str) -> ExportFileUnreadable:
    """The one message for a torn or corrupt export file: the file, the line, the fix."""
    return ExportFileUnreadable(
        f"{path} line {line} is not valid JSON ({why}). This export file was cut short or "
        "corrupted, most likely by a write that was interrupted before it finished. It is "
        "git-tracked, so restore its last committed copy with "
        f"`{_restore_command(path)}` and run this command again."
    )


def load_json_file(path: Path) -> object:
    """Parse a whole-file JSON export document, or raise :class:`ExportFileUnreadable`
    naming it (KYO-407.c3). The one loader for ``_allocations.json`` and every record
    document (``rebuild.read_record_documents``)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise export_file_unreadable(path, 1, f"not UTF-8: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise export_file_unreadable(path, exc.lineno, f"{exc.msg}, column {exc.colno}") from exc


def parse_ledger_line(raw: str | bytes, path: Path, line: int) -> dict:
    """Parse one JSONL ledger line, or raise :class:`ExportFileUnreadable` naming the
    file and the line (KYO-407.c3). A line that parses to something other than an
    object -- a torn line can end on a bare number -- is refused the same way."""
    try:
        row = json.loads(raw)
    except UnicodeDecodeError as exc:
        raise export_file_unreadable(path, line, f"not UTF-8: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise export_file_unreadable(path, line, f"{exc.msg}, column {exc.colno}") from exc
    if not isinstance(row, dict):
        raise export_file_unreadable(path, line, "the line is not a JSON object")
    return row


def iter_ledger_lines(path: Path) -> Iterator[tuple[int, str, dict]]:
    """``(line number, raw line, parsed row)`` for each non-blank line of a JSONL
    ledger, streamed. Line numbers are physical, so they match an editor's."""
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            if raw.strip():
                yield number, raw, parse_ledger_line(raw, path, number)


def export_dir(store: Store) -> Path:
    return store.config.export_path


def export_path(store: Store, record_id: str) -> Path:
    return export_dir(store) / f"{record_id}{EXPORT_SUFFIX}"


def _criterion(store: Store, row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "text": row["text"],
        "state": row["state"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "verifications": [_verification(v) for v in store.verifications_for_criterion(row["id"])],
    }


def _verification(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "verdict": row["verdict"],
        "method": row["method"],
        "reason": row["reason"],
        "author": row["author"],
        "created_at": row["created_at"],
        "evidence_id": row["evidence_id"],
    }


def _claim(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "statement": row["statement"],
        "level": row["level"],
        "method": row["method"],
        "author": row["author"],
        "created_at": row["created_at"],
        "supersedes": row["supersedes"],
        "superseded_by": row["superseded_by"],
        "superseded_at": row["superseded_at"],
        "redacted_at": row["redacted_at"],
        "redacted_why": row["redacted_why"],
    }


def _digest(text: str) -> str:
    """The sha256 `kyo attach`/`kyo verify` store for an evidence blob."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "text": row["text"],
        "sha256": row["sha256"],
        "supports_id": row["supports_id"],
        "author": row["author"],
        "created_at": row["created_at"],
        "redacted_at": row["redacted_at"],
        "redacted_why": row["redacted_why"],
    }


def _note(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "text": row["text"],
        "created_at": row["created_at"],
        "superseded_by": row["superseded_by"],
        "superseded_at": row["superseded_at"],
        "redacted_at": row["redacted_at"],
        "redacted_why": row["redacted_why"],
    }


def _touch(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "path": row["path"],
        "symbol": row["symbol"],
        "created_at": row["created_at"],
    }


def _doc_ref(row: sqlite3.Row) -> dict:
    """KYO-137.c1. Shaped exactly like :func:`_touch`, because it is the same shape: a
    root-relative path with an optional narrowing. The narrowing is a section heading
    rather than a symbol, so it keeps the column name ``symbol`` in the database (where
    the shape is what matters) and takes the honest one here, where the format is what a
    reader of the git-canonical export sees."""
    return {
        "id": row["id"],
        "seq": row["seq"],
        "path": row["path"],
        "section": row["symbol"],
        "created_at": row["created_at"],
    }


def _docs_obligation(row: sqlite3.Row) -> dict:
    """KYO-137.c2-c5. The five settle fields are exported whether or not they are set:
    an open obligation is one whose ``verdict`` is null, and a reader has to be able to
    see that null rather than infer it from a key that is not there (law 6)."""
    return {
        "id": row["id"],
        "seq": row["seq"],
        "dispatch_id": row["dispatch_id"],
        "doc_path": row["doc_path"],
        "section": row["section"],
        "cause": row["cause"],
        "detail": row["detail"],
        "created_at": row["created_at"],
        "verdict": row["verdict"],
        "method": row["method"],
        "author": row["author"],
        "settled_at": row["settled_at"],
        "settled_why": row["settled_why"],
    }


def _gate(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "type": row["gate_type"],
        "do": row["detail"],
        # KYO-102: the gate's tags, in the order they were declared. An empty list for
        # a gate carrying none — exported explicitly rather than omitted, so "this gate
        # was never classified" survives the round trip as a stated fact rather than as
        # a missing key, exactly as `branch` and `worker_model` do on a dispatch.
        "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
        "created_at": row["created_at"],
        "cleared_at": row["cleared_at"],
        "cleared_why": row["cleared_why"],
    }


def _dispatch(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "state": row["state"],
        "brief": row["brief"],
        "worked_from_sha": row["worked_from_sha"],
        "worktree": row["worktree"],
        # KYO-32: NULL when the controller declared no branch. Exported as an
        # explicit null rather than omitted, so "not declared" survives the
        # export/rebuild round trip as a stated fact rather than an absence.
        "branch": row["branch"],
        # KYO-61: NULL when nothing declared a model. Exported as an explicit null
        # for the same reason `branch` is — "no model was ever chosen" is a fact
        # about the conditions of this work, and it has to survive the round trip
        # as a stated one rather than as a missing key.
        "worker_model": row["worker_model"],
        "touch_override": (
            json.loads(row["touch_override_json"]) if row["touch_override_json"] else None
        ),
        "covers": json.loads(row["covers_json"]) if row["covers_json"] else [],
        "report_id": row["report_id"],
        "terminated_why": row["terminated_why"],
        "closed_at": row["closed_at"],
        # KYO-375: the per-dispatch tick counter a not-a-worker tick increments in
        # place instead of writing an events-ledger row -- {"count", "first_seen",
        # "last_seen"}, or None for a dispatch no tick has ever named.
        "tick_audit": (
            json.loads(row["tick_audit_json"]) if row["tick_audit_json"] else None
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _flag(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "flag_type": row["flag_type"],
        "detail": row["detail"],
        "other_record_id": row["other_record_id"],
        "other_criterion_id": row["other_criterion_id"],
        "sweep_run_id": row["sweep_run_id"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "resolved_by": row["resolved_by"],
        "resolved_why": row["resolved_why"],
    }


def _report(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "dispatch_id": row["dispatch_id"],
        "worked_from_sha": row["worked_from_sha"],
        "body": json.loads(row["body_json"]),
        "author": row["author"],
        "created_at": row["created_at"],
    }


def record_document(store: Store, record_id: str) -> dict:
    """Build the canonical document for one record.

    Incoming links are included, which is why writing a link re-exports *both*
    endpoints: a reader of ``KYO-2.json`` must see that ``KYO-1`` blocks it. Every
    link carries its own ``id``/``seq``/``created_at`` (not just the outgoing side's
    reader) so ``kyo rebuild`` can reconstruct the ``links`` table from export alone,
    without guessing a timestamp or an allocation-order seq (milestone 2 export
    completeness).
    """
    row = store.record(record_id)
    links = [
        {
            "id": link["id"],
            "seq": link["seq"],
            "direction": "outgoing",
            "type": link["edge_type"],
            "other": link["dst_id"],
            "created_at": link["created_at"],
        }
        for link in store.links_from(record_id)
    ] + [
        {
            "id": link["id"],
            "seq": link["seq"],
            "direction": "incoming",
            "type": link["edge_type"],
            "other": link["src_id"],
            "created_at": link["created_at"],
        }
        for link in store.links_to(record_id)
    ]
    return {
        DOCUMENT_VERSION_KEY: SCHEMA_VERSION,
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "status": row["status"],
        "plan": row["plan_id"],
        # KYO-100. Exported as ``null`` when nothing declared one, never omitted: an
        # absent key is what a pre-13 document has, and `kyo rebuild` reads that absence
        # as the version signal (schema.EXPORT_FIELD_DEFAULTS[13]).
        "scope": row["scope"],
        # KYO-498: the shift note that first recorded a carried item, ``null`` when none
        # was named -- never omitted, so a pre-26 document's absent key stays the version
        # signal (schema.EXPORT_FIELD_DEFAULTS[26]). Only the id; the time is the note's.
        "since_note": row["since_note"],
        "description": row["description"],
        "wont_do_reason": row["wont_do_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "criteria": [_criterion(store, c) for c in store.criteria(record_id)],
        "notes": [_note(n) for n in store.notes(record_id)],
        "touches": [_touch(t) for t in store.touches(record_id)],
        "doc_refs": [_doc_ref(r) for r in store.doc_refs(record_id)],
        "docs_obligations": [
            _docs_obligation(o) for o in store.docs_obligations(record_id)
        ],
        "gates": [_gate(g) for g in store.gates(record_id)],
        "links": links,
        "claims": [_claim(c) for c in store.claims(record_id)],
        "evidence_artifacts": [_evidence(e) for e in store.evidence_artifacts(record_id)],
        "dispatches": [_dispatch(d) for d in store.dispatches(record_id)],
        "reports": [_report(r) for r in store.reports(record_id)],
        "flags": [_flag(f) for f in store.flags(record_id)],
    }


def serialise(document: dict) -> str:
    """Render a document to its one canonical byte sequence."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_export(store: Store, record_id: str) -> Path:
    path = export_path(store, record_id)
    atomic_write_text(path, serialise(record_document(store, record_id)))
    return path


def write_exports(store: Store, record_ids: list[str]) -> list[Path]:
    """Regenerate exports for every record a mutation touched."""
    seen: list[str] = []
    for rid in record_ids:
        if rid not in seen:
            seen.append(rid)
    return [write_export(store, rid) for rid in seen]


def refresh_exports(store: Store) -> list[str]:
    """Rewrite *every* record's export document from the db (KYO-6).

    ``write_exports`` above only regenerates the records a mutation just touched —
    fine for keeping exports current in the normal course of business, but it
    leaves every *other* record's export at whatever format wrote it last. After a
    format-version bump, that is every record whose mutation predates the bump, and
    ``kyo doctor``'s ``check_exports`` flags all of them; this is their repair path
    (docs/design/export.md#kyo-6).

    Returns the ids of records whose on-disk export differed from the database
    *before* this call rewrote it, so the caller can report what was actually
    drifted rather than just "N records processed". Deliberately does not touch
    ``_events.jsonl`` (append-only history, not a derived-from-current-state
    artifact — there is nothing to "refresh") or delete any file with no matching
    record (a different failure mode than drift, out of KYO-6's scope, and law 4:
    supersede, never delete).
    """
    drifted: list[str] = []
    for record_id in store.all_record_ids():
        path = export_path(store, record_id)
        expected = serialise(record_document(store, record_id))
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != expected:
            drifted.append(record_id)
            atomic_write_text(path, expected)
    return drifted


def expected_exports(store: Store) -> dict[str, str]:
    """Every export file that *should* exist, mapped to its expected content."""
    return {rid: serialise(record_document(store, rid)) for rid in store.all_record_ids()}


def record_export_files_in(directory: Path) -> list[Path]:
    """Per-record export files only — never ``_events.jsonl`` / ``_allocations.json``."""
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.suffix == EXPORT_SUFFIX and p.is_file() and ids.is_record_id(p.stem)
    )


def existing_export_files(store: Store) -> list[Path]:
    return record_export_files_in(export_dir(store))


# ------------------------------------------------- KYO-179: reading the stamp back


def document_schema_version(document: dict, *, where: str) -> int:
    """The schema version an export document stamps in itself.

    KYO-179. ``kyo rebuild``'s compat machinery infers the source format from the
    *absence* of a field (:data:`kyosei.schema.EXPORT_FIELD_DEFAULTS`), which is sound
    for a document older than this build and structurally incapable of noticing one that
    is newer — a newer document is missing nothing, it merely has keys this build does
    not read. The stamp is the only thing in the file that can say so
    (docs/design/export.md#kyo-179).

    ``where`` is the file this document came from, quoted in the refusal below: a
    half-written export is a real state (nothing writes ``kyosei/export/``
    transactionally), so "which document" is a question the message has to answer.

    Refuses a stamp that is not an integer rather than coercing one. ``int("16")`` and
    ``int(16.5)`` both succeed and the second is silently wrong, which is the failure
    shape this whole record is about; a value that is not a version number means the
    document cannot say what format it is in, and that is not a fact to guess at.
    """
    raw = document.get(DOCUMENT_VERSION_KEY, PRE_VERSIONED_EXPORT_SCHEMA_VERSION)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise KyoseiError(
            f"{where} stamps a {DOCUMENT_VERSION_KEY} of {raw!r}, which is not a version "
            "number, so nothing can tell which export format this document is in. "
            f"Correct that one value in {where} by hand, or restore the file from the git "
            "history of the store repository — kyosei/export/ is the git-canonical "
            "serialization, so the version this document was written at is recorded there "
            "even though it is no longer in the file (KYO-179)"
        )
    return raw


# ---------------------------------------------- KYO-181: the ledgers carry one too


def stamp_ledger_row(row: dict) -> dict:
    """Return ``row`` with this build's version stamped into it, for appending.

    KYO-181. Without a stamp on the ledgers, a schema bump that touches only a ledger
    produces an export in which no record document changes and nothing carries a stamp
    the rebuild guard can read, so a later build's extra keys are dropped in silence
    (docs/design/export.md#kyo-181).

    **The form: a key on every line, not a header line at the top of the file.** Both
    version correctly; they trade differently against the property the event ledger was
    chosen for in the first place — append-only, merges cleanly (charter, Layer 2) — and
    that property decides it:

    * A **header line** has to be *rewritten* the moment the version moves, because a
      header written at 16 over lines appended at 17 states the wrong version for those
      lines and the guard reads right past them. So the file stops being append-only at
      exactly the moment the version changes, which is the only moment the stamp matters.
      And the rewritten byte is line 1 of a file two branches are both appending to —
      the one line a three-way merge cannot resolve positionally.
    * A **per-line key** is written once, with the line, and never touched again. Two
      branches appending different lines still conflict on nothing, because neither
      changes a byte the other wrote. It costs about twenty bytes a line, which is the
      whole of the trade.

    The deciding argument is not the byte count either way: it is that a per-line stamp
    is **bound to the bytes it versions**, which is the same reason
    :func:`document_schema_version` is trusted at this boundary and ``kyosei/config.yml``
    is not (see the long note in ``rebuild.py``). A ledger line is an immutable historical
    row; the version that wrote it is a fact about that row, not about the file it
    happens to sit in. A header would be a second config.yml — one number, standing for
    data it is not attached to, going stale on its own.

    Called at APPEND time only. Deliberately not inside :func:`event_line` or the
    ``json.dumps`` in :func:`_scrub_shift_notes`: those re-serialise lines that already
    exist (``kyo redact`` rewrites the ledgers, the one place the append-only rule is
    knowingly broken), and re-stamping there would relabel a historical row with the
    version of the build that happened to redact it — the row would then claim a format
    it was not written in, which is the exact failure this record exists to prevent,
    inverted.
    """
    return {**row, LEDGER_VERSION_KEY: SCHEMA_VERSION}


def ledger_schema_version(row: dict, *, where: str) -> int:
    """The schema version one ledger line stamps in itself (KYO-181).

    The ledger counterpart of :func:`document_schema_version`, and the same two rules for
    the same two reasons: an absent stamp is read as
    :data:`PRE_STAMPED_LEDGER_SCHEMA_VERSION` rather than as an error (every line written
    before this bump has none, and law 6 forbids an unknown reading as either answer),
    and a stamp that is not an integer is refused rather than coerced (``int("17")``
    succeeds and ``int(17.5)`` succeeds and is silently wrong).

    ``where`` is the ledger file the line came from, quoted in the refusal.
    """
    raw = row.get(LEDGER_VERSION_KEY, PRE_STAMPED_LEDGER_SCHEMA_VERSION)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise KyoseiError(
            f"{where} holds a line stamping a {LEDGER_VERSION_KEY} of {raw!r}, which is "
            "not a version number, so nothing can tell which export format that line is "
            f"in. Correct that one value in {where} by hand, or restore the file from the "
            "git history of the store repository — kyosei/export/ is the git-canonical "
            "serialization, so the version that line was written at is recorded there "
            "even though it is no longer in the file (KYO-181)"
        )
    return raw
