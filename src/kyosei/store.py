"""The SQLite store: connection, transactions, id allocation, event log.

SQLite is authoritative (charter, Layer 2). Every mutation runs inside one
``BEGIN IMMEDIATE`` transaction, so "I may die mid-operation" leaves no half-writes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import ids
from .clock import now
from .config import Config, load_config, refuse_store_write
from .contracts import (
    installed_build_clause,
    stale_build_clause,
    upgrade_the_build_clause,
)
from .errors import (
    SCHEMA_BUILD_AHEAD_OF_DB,
    SCHEMA_CONFIG_DRIFT,
    SCHEMA_DB_AHEAD_OF_BUILD,
    KyoseiError,
    NotFound,
    SchemaMismatch,
)
from .schema import DDL, SCHEMA_VERSION

# Allocation scopes. Law 4: counters here only ever increase.
SCOPE_RECORD = "record"
SCOPE_LINK = "link"
SCOPE_EVENT = "event"
#: Milestone 6 (KYO-8): project-wide (not per-record) scopes for sweep/brief
#: bookkeeping rows and shift-log entries — same allocation discipline as
#: SCOPE_LINK/SCOPE_EVENT above, never per-record like SCOPE_RECORD's children.
SCOPE_SWEEP = "sweep"
SCOPE_BRIEF = "brief"
SCOPE_SHIFT_NOTE = "shift_note"


def scope_for_child(record_id: str, child_kind: str) -> str:
    return f"{child_kind}:{record_id}"


class Store:
    """A handle on one project's database."""

    def __init__(
        self, config: Config, conn: sqlite3.Connection, *, read_only: bool = False
    ) -> None:
        self.config = config
        self.conn = conn
        #: KYO-453: opened by `kyo serve`, which serves reads and never a write. The
        #: connection carries ``PRAGMA query_only`` as well; this flag is what lets
        #: :meth:`transaction` refuse in a sentence rather than a bare SQLite error.
        self.read_only = read_only

    # ------------------------------------------------------------------ lifecycle

    @property
    def prefix(self) -> str:
        return self.config.project_prefix

    def close(self) -> None:
        self.conn.close()

    # --------------------------------------------------------------- transactions

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. Rolls back on any exception.

        Refuses to open at all over a database that is behind its export
        (:meth:`_refuse_if_behind_export`, KYO-75), or over one that belongs to
        another working tree (:func:`kyosei.config.refuse_store_write`, KYO-342).

        Both guards sit here for the reason KYO-75 put the first one here: every
        mutating command allocates at least one id and therefore opens exactly one
        of these, so this is the seam a command written tomorrow inherits without
        its author doing anything. KYO-342's guard is placed ahead of KYO-75's
        because it is the cheaper question and the more fundamental one — a store
        this process may not write at all is not a store whose currency against its
        export is worth computing.
        """
        if self.read_only:
            raise KyoseiError(read_only_refusal("this command"))
        refuse_store_write(
            self.config.store_via_main_checkout,
            self.config.repo_root,
            "write to this store",
        )
        self._refuse_if_behind_export()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _refuse_if_behind_export(self) -> None:
        """Law 4's guard, placed once on the seam every writer already goes through.

        Every mutating command allocates at least one id from this database's
        counters — an ``events`` row at minimum — and then rewrites
        ``kyosei/export/_allocations.json`` from them (``commands._finish_mutation``).
        Over a database that is *behind* its export, which KYO-56 made an ordinary
        state for a second checkout after a pull, that does two things at once: it
        lowers the git-canonical counters, and it hands out an id the export has
        already issued. A reissued id from ``kyo new`` puts two different records
        under one identifier in a git-canonical file, which is the thing law 4 exists
        to make impossible.

        The condition is ``Currency.would_reissue_an_id`` — "the export holds a
        counter this database has not reached" — and not the ``db_is_behind`` the read
        surfaces use. They are different questions and they disagree in both
        directions; see that property for which cases and why. ``kyo brief`` asked the
        read one, on a copy of this guard it kept when KYO-75 moved the guard here, and
        so refused in a state this one permits (KYO-80). That copy is gone: the brief
        now asks each of the two questions once, in the place that answers it.
        Sharpening it this way is what "refuse only where a counter would actually be
        lowered" turns into once followed through: not a per-scope exemption
        (``ledgers.write_allocations`` rewrites the whole snapshot, so if one scope is
        behind, one is lowered) but a narrower *state*.

        Placement (KYO-75.c2). Here rather than per-command, so a mutating command
        written tomorrow inherits it without its author doing anything; ``kyo brief``
        was the fourth instance of one fix, and this is what stops there being a
        fifth. Here rather than :meth:`allocate`, which fires once per id and would
        raise from the middle of a half-built transaction rather than before it
        opens. And here rather than ``Context.store()``, which would catch the read
        commands too — KYO-74 deliberately made reads *warn and stay useful*, and a
        `kyo list` that refuses tells a consumer nothing about the board they just
        pulled.

        ``kyo rebuild`` is not caught, and cannot be: it is the remedy every message
        below names and it necessarily runs against a database that is behind its
        export. It builds its own connection over a fresh file (``rebuild._load``)
        and never holds a :class:`Store`, so it does not pass through here — the
        exemption is structural rather than a name this function has to know.

        Costs one small JSON read, one ``iterdir`` and two indexed selects per write
        transaction, which is the budget ``currency.db_export_currency`` was built to
        (the read surfaces run it inside session hooks). Uncached on purpose: a
        command may open more than one transaction, and a stale "was in step" is
        exactly the answer this guard exists to not give.
        """
        # Imported inside the function: `currency` reaches `export`, which imports
        # this module (same cycle `open_store` names below).
        from .currency import db_export_currency

        seam = db_export_currency(self)
        if not seam.would_reissue_an_id:
            return
        raise KyoseiError(
            f"refusing to write to this store: {seam.sentence()}. Every mutating "
            "command allocates at least one id from the database's counters and then "
            "rewrites kyosei/export/_allocations.json with them, which would lower the "
            "git-canonical counters and reissue an id the export has already handed "
            f"out (law 4). To make it current, {seam.remedy()}"
        )

    # ---------------------------------------------------------------- allocation

    def allocate(self, scope: str) -> int:
        """Hand out the next number in ``scope``. Numbers are never returned."""
        row = self.conn.execute(
            "SELECT next_value FROM allocations WHERE scope = ?", (scope,)
        ).fetchone()
        if row is None:
            value = 1
            self.conn.execute(
                "INSERT INTO allocations (scope, next_value) VALUES (?, ?)", (scope, 2)
            )
        else:
            value = int(row["next_value"])
            self.conn.execute(
                "UPDATE allocations SET next_value = ? WHERE scope = ?", (value + 1, scope)
            )
        return value

    def new_record_id(self) -> str:
        return ids.record_id(self.prefix, self.allocate(SCOPE_RECORD))

    def new_child_id(self, record_id: str, child_kind: str) -> tuple[str, int]:
        seq = self.allocate(scope_for_child(record_id, child_kind))
        return ids.child_id(record_id, child_kind, seq), seq

    def new_link_id(self) -> tuple[str, int]:
        seq = self.allocate(SCOPE_LINK)
        return ids.link_id(self.prefix, seq), seq

    def new_sweep_run_id(self) -> tuple[str, int]:
        seq = self.allocate(SCOPE_SWEEP)
        return ids.sweep_run_id(self.prefix, seq), seq

    def new_brief_run_id(self) -> tuple[str, int]:
        seq = self.allocate(SCOPE_BRIEF)
        return ids.brief_run_id(self.prefix, seq), seq

    def new_shift_note_id(self) -> tuple[str, int]:
        seq = self.allocate(SCOPE_SHIFT_NOTE)
        return ids.shift_note_id(self.prefix, seq), seq

    # -------------------------------------------------------------------- reads

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def record(self, record_id: str) -> sqlite3.Row:
        """Fetch a record or raise. Law 6: an id that does not exist is an error."""
        row = self.conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"no record {record_id!r} in {self.config.db_path}")
        return row

    def record_exists(self, record_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM records WHERE id = ?", (record_id,)).fetchone()
        return row is not None

    def criteria(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM criteria WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def criterion(self, criterion_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM criteria WHERE id = ?", (criterion_id,)).fetchone()
        if row is None:
            raise NotFound(f"no criterion {criterion_id!r}")
        return row

    def notes(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM notes WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def note(self, note_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise NotFound(f"no note {note_id!r}")
        return row

    def gates(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM gates WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def touches(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM touches WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    # ------------------------------------------------------- docs governance (KYO-137)

    def doc_refs(self, record_id: str) -> list[sqlite3.Row]:
        """The documents this record declares it affects (``doc-refs``)."""
        return list(
            self.conn.execute(
                "SELECT * FROM doc_refs WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def docs_obligations(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM docs_obligations WHERE record_id = ? ORDER BY seq",
                (record_id,),
            )
        )

    def docs_obligation(self, obligation_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM docs_obligations WHERE id = ?", (obligation_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"no docs obligation {obligation_id!r}")
        return row

    def open_docs_obligations(self, record_id: str) -> list[sqlite3.Row]:
        """This record's obligations nobody has recorded a verdict against yet.

        ``verdict IS NULL`` is the whole definition of open, and it is the same column
        the settle path writes. KYO-136 decision 3 is that a document counts as updated
        when a controller ASSERTS it did, so "open" has to be the absence of that
        assertion; it can never be a date comparison, and there is deliberately no
        column here a timestamp could close. This is what
        ``contracts.check_transition`` refuses a terminal move on (KYO-137.c5).
        """
        return list(
            self.conn.execute(
                "SELECT * FROM docs_obligations WHERE record_id = ? AND verdict IS NULL"
                " ORDER BY seq",
                (record_id,),
            )
        )

    def open_standing_obligations(self) -> list[sqlite3.Row]:
        """Every open STANDING obligation on the board (KYO-554), oldest first.

        A standing obligation is one a record raised when it was filed or edited, so it
        has no dispatch (``dispatch_id IS NULL``) and no branch whose report would surface
        it; `kyo brief` and `kyo doctor` are where it is seen. Each row carries its
        record's title beside the obligation's own columns.
        """
        return list(
            self.conn.execute(
                "SELECT o.*, r.title AS record_title FROM docs_obligations o"
                " JOIN records r ON r.id = o.record_id"
                " WHERE o.dispatch_id IS NULL AND o.verdict IS NULL"
                " ORDER BY o.created_at, o.record_id, o.seq"
            )
        )

    def description_edited_at(self, record_id: str) -> str:
        """When this record's description was last CHANGED (KYO-137.c7).

        Not ``records.updated_at``, which moves whenever anything on the record moves —
        a note appended, a criterion verified, a touch corrected. Against that column
        "the description predates this claim" would be answering a different question
        and would answer it wrong in the safe-looking direction, reporting a stale
        description as fresh because something unrelated was edited afterwards.

        The events ledger is the authority, and it can answer this because every
        mutating command stores what the record looked like before and after
        (:meth:`record_event`): the last event whose prior value of ``description``
        differs from its ``after`` is the last edit -- a whole ``before`` document on a
        row written before schema_version 25, a ``changed`` map since (KYO-418).
        Walked newest-first and stopped at the first hit, so the common case reads one row.

        Falls back to the record's ``created_at`` when no event shows a change, which is
        the truth for a record whose description has been what it was since it was
        filed — including a record created before this build, whose history holds no
        such event to find. It is a real answer, not a default: the description dates
        from the record's creation because nothing has touched it since.
        """
        row = self.record(record_id)
        # KYO-422: the comparison runs inside SQLite. Walking the rows in Python meant
        # `json.loads` on both whole snapshots of every event (up to 2.9 MB each) until
        # one differed -- all of them for a record never edited -- and `kyo review`
        # asks this of every swept record on every Stop: 0.39 s over this repository's
        # 426 records, against 0.11 s this way. `json_extract` of a missing key and of a
        # JSON null are both SQL NULL, and `IS NOT` treats two NULLs as equal, which is
        # what `dict.get` then `!=` did. A row missing either snapshot is skipped as it
        # was. A stored hash or a `description_edited_at` column would avoid even
        # this parse, and is a schema change deferred for now.
        #
        # KYO-418.c3: two formats, one question. A row written before schema_version 25
        # holds the whole prior document and is compared as before; a row written
        # since holds `changed_json`, which carries the key `description` exactly when
        # the description moved (`json_type` of a present key -- a JSON null included --
        # is never SQL NULL, and of an absent one always is). Keyed on which column the
        # row has, never on a version, because one ledger holds both.
        event = self.conn.execute(
            "SELECT at FROM events WHERE record_id = ? AND after_json IS NOT NULL AND ("
            " (before_json IS NOT NULL"
            "  AND json_extract(before_json, '$.description')"
            "  IS NOT json_extract(after_json, '$.description'))"
            " OR (changed_json IS NOT NULL"
            "  AND json_type(changed_json, '$.description') IS NOT NULL))"
            " ORDER BY seq DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        if event is not None:
            return str(event["at"])
        return str(row["created_at"])

    def live_claims(self, record_id: str) -> list[sqlite3.Row]:
        """This record's claims that nothing has superseded (KYO-137.c7).

        Claims are append-only and a superseded one is history: measuring a description
        against a claim that has itself been replaced would flag the record for being
        older than something that is no longer the answer.
        """
        return list(
            self.conn.execute(
                "SELECT * FROM claims WHERE record_id = ? AND superseded_by IS NULL"
                " ORDER BY seq",
                (record_id,),
            )
        )

    # ------------------------------------------------------------- provenance (M4)

    def claims(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM claims WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def claim(self, claim_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            raise NotFound(f"no claim {claim_id!r}")
        return row

    def verifications_for_criterion(self, criterion_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM verifications WHERE criterion_id = ? ORDER BY seq", (criterion_id,)
            )
        )

    def latest_verification(self, criterion_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM verifications WHERE criterion_id = ? ORDER BY seq DESC LIMIT 1",
            (criterion_id,),
        ).fetchone()

    def evidence_artifacts(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM evidence_artifacts WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def evidence_artifact(self, evidence_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM evidence_artifacts WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"no evidence artifact {evidence_id!r}")
        return row

    def gate(self, gate_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM gates WHERE id = ?", (gate_id,)).fetchone()
        if row is None:
            raise NotFound(f"no gate {gate_id!r}")
        return row

    def link(self, link_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        if row is None:
            raise NotFound(f"no link {link_id!r}")
        return row

    def links_from(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM links WHERE src_id = ? ORDER BY seq", (record_id,))
        )

    def links_to(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM links WHERE dst_id = ? ORDER BY seq", (record_id,))
        )

    def all_record_ids(self) -> list[str]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM records ORDER BY seq")]

    # -------------------------------------------------------------- dispatches (M5)

    def dispatches(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM dispatches WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def dispatch(self, dispatch_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM dispatches WHERE id = ?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"no dispatch {dispatch_id!r}")
        return row

    def running_dispatches(self) -> list[sqlite3.Row]:
        """The dispatches still in flight — a positive test on ``running`` (KYO-7).

        This is the query every CLAIM in this system is derived from: touch
        disjointness, the worktree-collision refusal and the brief's concurrency
        statement all read it (``cmd_dispatch``), so a dispatch leaves ``running``
        and its claims are released in the same instant, by one fact rather than by
        a release step somebody could forget. KYO-345 needed no line here for exactly
        that reason: ``withdrawn`` is not ``running``, so closing a mis-frozen brief
        frees its worktree and its touches for the next `kyo dispatch` without this
        method, or any caller of it, learning the new word (KYO-345.c2).
        """
        return list(
            self.conn.execute("SELECT * FROM dispatches WHERE state = 'running' ORDER BY seq")
        )

    def all_dispatches(self) -> list[sqlite3.Row]:
        """Every dispatch this project has ever created, oldest first (KYO-70).

        ``kyo worktrees`` needs the *finished* ones — the opposite of
        :meth:`running_dispatches` — and there is deliberately no "finished" query,
        because a dispatch can be finished in four different ways (``reported``,
        ``terminated-no-report``, ``withdrawn``, or merely carrying a ``closed_at``).
        Handing back everything and letting the one caller name its own predicate
        keeps that judgment in the command that has to explain it.
        """
        return list(self.conn.execute("SELECT * FROM dispatches ORDER BY seq"))

    def reports(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM reports WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def report(self, report_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise NotFound(f"no report {report_id!r}")
        return row

    # --------------------------------------------------------------- sweep/brief (M6)

    def flags(self, record_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM flags WHERE record_id = ? ORDER BY seq", (record_id,)
            )
        )

    def flag(self, flag_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM flags WHERE id = ?", (flag_id,)).fetchone()
        if row is None:
            raise NotFound(f"no flag {flag_id!r}")
        return row

    def unresolved_flags(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM flags WHERE resolved_at IS NULL ORDER BY record_id, seq"
            )
        )

    def latest_sweep_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sweep_runs ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    def latest_brief_run(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM brief_runs ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    def latest_session_brief_run(self) -> sqlite3.Row | None:
        """The most recent brief run that declared itself the start of a session.

        KYO-94: the store's entire session concept. ``session_start = 1`` is written
        only by ``kyo brief --session-start`` (what the packaged ``SessionStart`` hook
        runs), so this row is "where the reader with the current context started
        reading", and its ``last_shift_note_seq`` is the shift log as it stood at that
        moment — which is what makes "authored before this session began" a question
        the store can answer at all.

        ``= 1``, never truthiness: a row from before the column existed holds NULL,
        which means *never asked* and must not anchor a session (law 6).
        """
        return self.conn.execute(
            "SELECT * FROM brief_runs WHERE session_start = 1 ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    def shift_notes_since(self, seq_after: int) -> list[sqlite3.Row]:
        """Shift notes with ``seq`` strictly greater than ``seq_after`` — used by
        `kyo brief` to surface the log written since the previous brief."""
        return list(
            self.conn.execute(
                "SELECT * FROM shift_notes WHERE seq > ? ORDER BY seq", (seq_after,)
            )
        )

    def shift_note(self, note_id: str) -> sqlite3.Row | None:
        """One shift note by its ``PREFIX-SN`` id, or ``None``.

        KYO-94: the store could already find a note by id in order to *destroy* it
        (`kyo redact`) and had no way to find one in order to *show* it. This is the
        read half of that pair, and it is deliberately independent of every watermark:
        the brief's window decides what is surfaced unasked, never what remains
        readable.
        """
        return self.conn.execute(
            "SELECT * FROM shift_notes WHERE id = ?", (note_id,)
        ).fetchone()

    def latest_shift_note_upto(self, seq_at_most: int) -> sqlite3.Row | None:
        """The newest shift note with ``seq`` at or below ``seq_at_most``.

        KYO-94: with ``seq_at_most`` set to the shift-log watermark of the brief that
        opened this session, this is exactly "the most recent shift note authored
        before this session began".
        """
        return self.conn.execute(
            "SELECT * FROM shift_notes WHERE seq <= ? ORDER BY seq DESC LIMIT 1",
            (seq_at_most,),
        ).fetchone()

    def all_shift_notes(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM shift_notes ORDER BY seq"))

    # ------------------------------------------------------------------- events

    def record_event(
        self,
        *,
        actor: str,
        command: str,
        record_id: str | None,
        before: dict | None,
        after: dict | None,
    ) -> dict:
        """Append one row to the audit log. Called by every mutating command.

        Returns the event as a plain dict (id, seq, at, actor, command, record_id,
        before, after — the last two as plain objects, not double-encoded JSON
        strings) so the caller can also append it to the events export ledger without
        re-parsing what was just written.

        KYO-268.c1, widened by KYO-274.c1: both snapshots pass through
        :func:`kyosei.events_ledger.event_snapshot` first, which replaces every BODY a record
        document embeds — an evidence artifact's ``text``, a dispatch's frozen ``brief``,
        a report's ingested ``body`` — with a digest that names it, so none of the three
        is ever copied into a snapshot. Done here, at the one door every event goes
        through, rather than at the seven call sites that reach it — a call site that
        forgot would put the bodies back with nothing to say so. The stripped document
        is what goes into the db's ``before_json``/``after_json`` *and* what is returned
        for the ledger, so a rebuild from that ledger reproduces this row rather than a
        shorter version of it.

        ``export`` is imported inside the function because ``export`` imports this
        module at module level — the same deferred-import shape ``open_store`` below
        uses for ``currency``.
        """
        from . import events_ledger

        before = events_ledger.event_snapshot(before)
        after = events_ledger.event_snapshot(after)
        # KYO-418.c2: from schema_version 25 an event carries `after` whole and, in
        # place of the whole prior document, `changed` -- the prior value of each
        # top-level key that differs (events_ledger.event_diff). Both snapshots were full
        # documents, so a one-field edit stored the record twice. The diff is taken
        # between the two STRIPPED documents and the result is stripped again
        # (KYO-418.c4): `changed` holds whole top-level values -- a record's
        # `evidence_artifacts`, `dispatches` or `reports` list among them -- so it is
        # a partial record document and goes through the same door as the other two.
        changed: dict | None = None
        if isinstance(before, dict):
            changed = events_ledger.event_snapshot(events_ledger.event_diff(before, after))
            before = None
        seq = self.allocate(SCOPE_EVENT)
        event_id = ids.event_id(self.prefix, seq)
        at = now()
        self.conn.execute(
            "INSERT INTO events (id, seq, at, actor, command, record_id, before_json,"
            " after_json, changed_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                seq,
                at,
                actor,
                command,
                record_id,
                None if before is None else json.dumps(before, sort_keys=True),
                None if after is None else json.dumps(after, sort_keys=True),
                None if changed is None else json.dumps(changed, sort_keys=True),
            ),
        )
        event = {
            "id": event_id,
            "seq": seq,
            "at": at,
            "actor": actor,
            "command": command,
            "record_id": record_id,
            "after": after,
        }
        # Only the sides this event has: a line that carries `before` is the old
        # format by definition, and one with neither side had no prior document.
        if before is not None:
            event["before"] = before
        if changed is not None:
            event[events_ledger.EVENT_CHANGED_KEY] = changed
        return event


#: How long a connection from :func:`connect` waits for another process's write lock
#: before SQLite gives up with ``database is locked`` (KYO-421).
#:
#: Python's ``sqlite3.connect`` already installs a 5 s busy handler by default, so the
#: store always waited — but for a length nobody chose, on a line that did not say so.
#: The writers that collide are known: the Stop hook's `kyo doctor`/`kyo sweep`, the
#: SubagentStop stop-audit write, and the controller's own command, each of which
#: holds the lock only from ``BEGIN IMMEDIATE`` to ``COMMIT`` (exports are written
#: after the commit, :func:`kyosei.commands._finish_mutation`), so a healthy writer
#: releases it in well under a second. 15 s is three times the implicit default:
#: generous enough to outlast a writer slowed by a loaded machine or a large sweep,
#: and still far inside the 60 s a Claude Code command hook is allowed, so a hook that
#: waits and then fails still gets to print its own refusal instead of being killed.
#: Longer would only delay the refusal for a writer that is stuck, not slow.
#: A module attribute, read at connect time, so a test can shorten it.
BUSY_TIMEOUT_MS = 15_000


def is_lock_contention(exc: BaseException) -> bool:
    """Whether ``exc`` is SQLite giving up on another connection's lock."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)  # Python 3.11+
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return "database is locked" in str(exc) or "database table is locked" in str(exc)


def lock_contention_message(db_path: Path | None) -> str:
    """The refusal a write that lost a lock race prints (KYO-421.c5).

    It names the database and that another writer holds it, and says plainly that
    the writer is NOT named: SQLite reports that a lock is held, never by whom, and
    the only ways to learn it (``lsof`` on the file, a lock-owner table every writer
    would have to maintain) cost more than the question is worth on an error path.
    The usual writers are listed as what they are — the likely candidates — rather
    than presented as a diagnosis, and without backticks, because they are not
    commands to run: the remedy-coverage walk (``tests/test_kyo5_error_remedies.py``)
    reads a backticked `kyo` command as an offered remedy.
    """
    where = str(db_path) if db_path is not None else "this project's store database"
    seconds = BUSY_TIMEOUT_MS / 1000
    return (
        f"{where} is locked: another process is writing to it and still held the "
        f"write lock after this command waited {seconds:g}s for it. SQLite does not "
        "say which process holds a lock; on a project with Kyosei's session hooks "
        "the usual writers are the Stop hook's doctor and sweep, the SubagentStop "
        "hook's check-clean, or another kyo command run by hand or by an agent. This "
        "command stopped at that write and committed nothing past it. Remedy: run "
        "this command again once the other kyo command finishes."
    )


def read_only_refusal(what: str) -> str:
    """The sentence a write on a store `kyo serve` opened read-only is refused with
    (KYO-453) -- raised by :meth:`Store.transaction` and, for a write that
    reached SQLite's ``query_only`` without opening one, by :func:`kyosei.cli.run`."""
    return (
        f"{what} tried to write to the store, and kyo serve opens every store "
        "read-only: the HTTP server serves reads and never a write to kyosei/ (law 12). "
        "Run the command in a terminal on the machine that holds the store instead"
    )


def is_read_only_refusal(exc: BaseException) -> bool:
    """Whether ``exc`` is SQLite refusing a write on a ``query_only`` connection."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)  # Python 3.11+
    if code is not None and code & 0xFF == sqlite3.SQLITE_READONLY:
        return True
    return "readonly database" in str(exc)


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """A connection to ``path``. ``read_only`` makes writing it impossible (KYO-342).

    ``PRAGMA query_only`` rather than SQLite's ``mode=ro`` URI, and the difference
    matters for exactly this case: the database this is used on is another working
    tree's live store, in WAL mode, and a WAL reader needs to write the ``-shm``
    index even when it reads nothing. ``mode=ro`` would refuse the store the
    redirect exists to read; ``query_only`` refuses every statement that would
    change *content* and leaves WAL bookkeeping alone. Belt to
    :meth:`Store.transaction`'s braces: the transaction guard is the one that says
    WHY in a sentence a person can act on, and this is the one that holds if a
    future write path forgets to open a transaction at all.

    ``busy_timeout`` is set explicitly (KYO-421): see :data:`BUSY_TIMEOUT_MS`. It is
    the first statement on the connection so that even ``journal_mode`` below waits
    rather than failing on a store another process is writing.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)}")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


def create_database(config: Config) -> None:
    """Create a fresh database. Refuses to overwrite an existing one (law 6)."""
    if config.db_path.exists():
        raise KyoseiError(f"{config.db_path} already exists; refusing to overwrite it")
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(config.db_path)
    try:
        conn.executescript(DDL)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)", ("schema_version", str(SCHEMA_VERSION))
        )
        # Nothing else goes in `meta`: a key no code path reads is decoration (law 2).
    finally:
        conn.close()


def open_store(
    root: Path,
    *,
    code_root: Path | None = None,
    store_via_main_checkout: Path | None = None,
    read_only: bool = False,
) -> Store:
    """Open ``kyosei.db``, failing loudly — never mid-command — on any version drift.

    ``code_root`` (KYO-54) is the code repository this store serves, which under
    the split layout is *not* ``root``. It is carried on the :class:`Config` so
    that checks reached through a ``Store`` — ``kyo doctor``'s hook and skill
    drift checks, for two — look for ``.claude/`` where the code is rather than
    where the records are, and report nothing found in a directory that was
    never going to have it.

    ``store_via_main_checkout`` (KYO-342) is :attr:`kyosei.config.Layout.store_via_main_checkout`
    — set when ``root`` is the *main checkout's* store, reached from a linked git
    worktree whose own ``kyosei/`` has no database. It is carried on the
    :class:`Config` for exactly one consumer, :meth:`Store.transaction`, which
    refuses over it: reads resolve through to another working tree's store, writes
    never do. Passed in rather than re-derived here because the caller has already
    asked git, and a second answer to a question with one true answer is the pair
    law 3 forbids.

    KYO-3.c1: the schema version is recorded in two places, ``kyosei/config.yml``
    and the db's own ``meta`` table; they are supposed to move together (written by
    the same ``kyo init`` / ``kyo rebuild`` call), but only the db's ``meta`` table
    is physically bound to the file that is about to be queried, so it is the
    authority here — a hand-edited or stale ``config.yml`` is reported as *its own*
    drift, distinct from "this db predates the code" (law 3: two fields that must
    agree, or the check fails). Every failure below is a plain :class:`KyoseiError`
    (exit 1) raised *before* any table this build might have added is ever queried,
    so a store from an earlier format never reaches a bare ``sqlite3.OperationalError``
    traceback mid-command — the failure mode this bug was filed against.
    """
    config = load_config(
        root, code_root=code_root, store_via_main_checkout=store_via_main_checkout
    )
    if not config.db_path.is_file():
        # KYO-73. This message named `kyo init`, which was the truth for as long as a
        # store lived inside the code repository: no database there meant no project
        # yet. KYO-56 made the split layout the default, and the first command a
        # second checkout of a store repository runs lands here — with the store fully
        # present and only its (gitignored) database missing. `kyo init` then refuses,
        # so the named remedy was a dead end. `currency.missing_database_remedy` reads
        # what is actually on disk and names the command that clears *this* state.
        # Imported inside the function: `currency` reaches `export`, which imports this
        # module.
        from .currency import missing_database_remedy

        raise KyoseiError(
            f"{config.db_path} is missing; {missing_database_remedy(config)}"
        )

    # KYO-453: ``read_only`` is `kyo serve`'s, and carried to the Store below so its
    # transaction refuses by a sentence naming the server.
    conn = connect(
        config.db_path,
        read_only=read_only or config.store_via_main_checkout is not None,
    )
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError as exc:
        conn.close()
        raise KyoseiError(
            f"{config.db_path} does not look like a Kyosei store ({exc}); "
            "run `kyo rebuild --force` to reconstruct it from kyosei/export/ (the file "
            "already exists, so plain `kyo rebuild` refuses it; --force is what actually "
            "clears this — KYO-5)"
        ) from exc
    if row is None:
        conn.close()
        raise KyoseiError(
            f"{config.db_path} has no schema_version recorded in its meta table; "
            "run `kyo rebuild --force` to reconstruct it from kyosei/export/ (the file "
            "already exists, so plain `kyo rebuild` refuses it; --force is what actually "
            "clears this — KYO-5)"
        )

    db_version = int(row["value"])
    # KYO-168: two directions, two remedies. This was one symmetric `!=` branch giving
    # KYO-5's `kyo rebuild --force` advice either way, and only one of the two is a case
    # that command fixes. `rebuild.py` stamps *this build's* SCHEMA_VERSION into the db it
    # reconstructs and rewrites config.yml to match (KYO-5.c1), and its tolerance runs one
    # way only — it is full of care for exports written before the current schema and has
    # none for an export written by a LATER build, which by the design of the compat
    # machinery it cannot have: a build cannot know what a future schema added. So the
    # symmetric advice told the holder of a store that is AHEAD of the build to reconstruct
    # a healthy store under an older schema, silently dropping whatever the later schema
    # added, and to rewrite their config claiming the downgrade was intended. Nearly
    # unreachable while one store and one build move together; ordinary once more than
    # one installed `kyo` reads the same stores -- a second machine, or the `kyo serve` a
    # desktop app reads through -- and one of them is older than the build that wrote one.
    #
    # KYO-178: and all three refusals below carry a payload as well as a sentence.
    # KYO-168's split fixed what a human reads; it left a program with nothing but the
    # prose to branch on, and the desktop app (KYO-175.c4) has to tell these two
    # directions apart to know whether a remedy is safe. `SchemaMismatch` builds the
    # payload out of its required arguments — see `errors.py` for why it is a class
    # and not a `.data` assignment here — so what a caller reads is `direction`,
    # `older_side` and all three version numbers, never a substring of the sentence.
    if db_version < SCHEMA_VERSION:
        conn.close()
        raise SchemaMismatch(
            f"{config.db_path} is schema_version {db_version}, this kyosei build speaks "
            f"schema_version {SCHEMA_VERSION}; run `kyo rebuild --force` to reconstruct it "
            "from kyosei/export/ under the current schema (the db already exists, so plain "
            "`kyo rebuild` refuses it; --force is what actually clears this — KYO-5). "
            "That rebuild reads every export document exactly as it was written, so it "
            "leaves kyosei/export/ stamped at the OLDER schema_version you are upgrading "
            "past; run `kyo export --refresh` next to rewrite it from the rebuilt "
            "database, then `kyo doctor` to confirm the two agree (KYO-216)",
            direction=SCHEMA_BUILD_AHEAD_OF_DB,
            build_schema_version=SCHEMA_VERSION,
            db_schema_version=db_version,
            config_schema_version=config.schema_version,
            config_path=config.config_path,
            db_path=config.db_path,
        )
    if db_version > SCHEMA_VERSION:
        conn.close()
        # Deliberately names no command that would reconstruct the store: every such
        # command here writes the OLDER schema over a newer one. Deliberately readable
        # out of a terminal too — KYO-175 puts this on screen in the desktop app, where
        # the reader is not at a shell and may not be the person who installed anything.
        # KYO-182: the three clauses this shares verbatim with `rebuild.py`'s later-export
        # refusal come from `contracts` and are not restated here. The message is still
        # this message — it names a database, it is raised at open, and the two sentences
        # that are ITS OWN (which artifact, and what must not be reconstructed) stay where
        # a reader meets them.
        raise SchemaMismatch(
            f"{config.db_path} is schema_version {db_version}; this kyosei build speaks "
            f"schema_version {SCHEMA_VERSION}, which is older. "
            + stale_build_clause(artifact="store", cannot="open a store")
            + " "
            + upgrade_the_build_clause(db_version)
            + f" Nothing here should be reconstructed under schema_version {SCHEMA_VERSION}"
            " to make it openable; that would rewrite the store under the older schema, "
            "drop whatever the later one added, and record the downgrade as if it were "
            "intended. " + installed_build_clause() + " — KYO-168",
            direction=SCHEMA_DB_AHEAD_OF_BUILD,
            build_schema_version=SCHEMA_VERSION,
            db_schema_version=db_version,
            config_schema_version=config.schema_version,
            config_path=config.config_path,
            db_path=config.db_path,
        )
    if config.schema_version != db_version:
        conn.close()
        # KYO-180: the lossless remedy first, and unhedged. This site is reached only
        # once both branches above have passed, so `db_version == SCHEMA_VERSION` is a
        # precondition of it raising at all: the db agrees with the build, and
        # config.yml is by construction the mis-stated party. The old wording offered
        # `kyo rebuild --force` first and put the hand correction second behind "if it
        # alone was mis-edited" — which reads as a caveat on the cheap remedy but is,
        # at this site, the only thing that can have happened. So the reader was
        # steered to reconstruct the entire store to change one scalar in a YAML file.
        # Both remedies stay; the order and the hedge are what change (wording only —
        # nothing about which states reach here moved).
        raise SchemaMismatch(
            f"{config.config_path} declares schema_version {config.schema_version} but "
            f"{config.db_path} is stamped schema_version {db_version} (law 3: these must "
            "agree). The db is not the party that is wrong: it matches this build, which "
            f"speaks schema_version {SCHEMA_VERSION}, and the two checks above this one "
            "have already established that — so config.yml is the mis-stated copy. "
            f"Correct its schema_version to {db_version} by hand; that is the whole fix "
            "and it changes one line. `kyo rebuild --force` reconciles them too (it "
            "rewrites config.yml's schema_version to match the rebuilt db — KYO-5.c1), "
            "at the price of reconstructing the entire store to correct one scalar",
            direction=SCHEMA_CONFIG_DRIFT,
            build_schema_version=SCHEMA_VERSION,
            db_schema_version=db_version,
            config_schema_version=config.schema_version,
            config_path=config.config_path,
            db_path=config.db_path,
        )
    return Store(config, conn, read_only=read_only)
