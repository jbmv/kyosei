"""Which side of the db/export seam moved (KYO-74).

The database is authoritative and ``kyosei/export/`` is generated from it — that
was true without qualification while the store lived inside the code repository
and one process owned both files. KYO-56 made the split layout the default: the
store is its own git repository, ``kyosei.db`` is gitignored there, and a second
checkout pulls record changes the way it pulls code. After a pull the export is
legitimately *ahead* of the database, and nothing in the tool could tell.

Two symptoms came out of that one gap (KYO-74). Reads presented a database that
was missing records as a complete board, with a count and no warning. And
``kyo doctor`` asserted a direction it had no input for — "the database is
authoritative", "(stale export)" — which is correct under the in-repo layout and
inverted in the pulled case, where acting on it destroys the pulled work.

This module is the missing input. It compares two things the store already keeps
and nobody was comparing:

``kyosei/export/_allocations.json`` vs the ``allocations`` table
    The id counters. Law 4 makes them monotonic, and every mutating command
    allocates at least one id inside its transaction and rewrites the snapshot
    just after it, so the counters are a *generation number* for the store: no
    operation can be recorded on one side without moving them. Comparing them
    scope by scope says which side has recorded operations the other has not,
    which is exactly the direction question.

the record ids in the database vs the ``<ID>.json`` files in ``kyosei/export/``
    Not the direction — the *consequence* a read surface has to declare. A record
    file the database has no row for is a record a listing silently omits.

Both are cheap enough for the read path, which is a hard constraint: ``kyo list``
and ``kyo brief`` run inside the session hooks. One small file read, one
``iterdir``, and two indexed selects — deliberately NOT
``doctor_exports.check_export_roundtrip``'s rebuild-into-a-temporary-store, which answers
a different question at a cost no read surface can pay.

Where the counters cannot settle it, the answer is
:data:`CURRENCY_UNDECIDABLE` and the tool says so rather than picking — the
"could not tell" third state a check needs when its evidence cannot decide, in the
shape settled on KYO-32's branch check.

Two predicates come off that one comparison and they are not interchangeable:
:attr:`Currency.db_is_behind` and :attr:`Currency.would_reissue_an_id` disagree in
both directions (see the second for which cases). Which one a caller asks is decided
by what the caller is about to do, never by which command it is:

    a surface that *presents* the board asks ``db_is_behind``, and warns;
    anything that *writes* asks ``would_reissue_an_id``, and refuses.

A command that does both — ``kyo brief`` reads a board and allocates a brief-run id —
asks each question once, in the place that already answers it: the write question on
``Store.transaction()`` (KYO-75), the read question through
:func:`read_surface_warning_from` and :func:`as_data`. It asks neither one again
itself, which is how it came to refuse on the read predicate in a state where writing
was permitted, over a message naming a consequence that would not occur (KYO-80). The
one writer to ``kyosei/export/`` that opens no transaction — ``kyo export --refresh``,
which allocates nothing — asks the write question at its own call site, for that
reason and no other (KYO-81).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import Store

#: The counters agree and every export file has a record behind it. Nothing to say.
CURRENCY_IN_STEP = "in-step"
#: The database has recorded operations the export has not — the normal direction,
#: and the only one that existed before the split layout. ``kyosei/export/`` needs
#: regenerating.
CURRENCY_DB_AHEAD = "db-ahead"
#: The export has recorded operations the database has not — a pull, a fresh clone
#: that was rebuilt and has since fallen behind, a restored ``kyosei/export/``.
#: ``kyosei.db`` needs rebuilding.
CURRENCY_EXPORT_AHEAD = "export-ahead"
#: Neither of the above. Either both sides moved independently (a genuine fork), or
#: the evidence contradicts itself. Named, never resolved by guessing: the whole
#: defect KYO-74 records is a tool asserting a direction it had no input for.
CURRENCY_UNDECIDABLE = "cannot-tell"

#: What makes the database current with its export. ``--force`` and not plain
#: ``kyo rebuild`` because the database file exists in this state, and
#: ``rebuild_from_export`` refuses an existing one without it — the KYO-5 lesson
#: about naming a remedy that would itself be refused.
REBUILD_REMEDY = "kyo rebuild --force"
#: What makes the export current with the database (KYO-6).
REFRESH_REMEDY = "kyo export --refresh"

#: KYO-362: how many items any one piece of evidence in this module names before it
#: stops and says how many are left.
EVIDENCE_LIMIT = 5


def some_of(items: list[str], *, separator: str = ", ", limit: int = EVIDENCE_LIMIT) -> str:
    """The first few of ``items``, and how many were not shown.

    KYO-362, and it is the second instance of the defect that record was filed for,
    found by the test that record's last criterion asks for. This module's sentence
    is what `kyo doctor`'s ``db_export_currency`` finding is, and every list in it
    grows with the STORE: a fork of a 300-record store produced a single finding just
    under six thousand characters, naming every counter twice over — printed in full
    from the ``Stop`` hook, beside the allocation maps the record was actually about.

    Evidence is a sample, not an inventory. Five items say what kind of drift this is
    and where to start looking; the sixth through the three-hundredth say the same
    thing again, and the count is what a reader needs from the rest of them. Anyone
    who wants the inventory has `kyo doctor --json`, which carries the lists whole.
    """
    shown = separator.join(items[:limit])
    if len(items) > limit:
        shown += f"{separator}and {len(items) - limit} more"
    return shown


@dataclass(frozen=True)
class Currency:
    """One answer to "which side of the db/export seam moved, and how do you know?"."""

    direction: str
    #: ``{scope: next_value}`` from the ``allocations`` table.
    db_allocations: dict[str, int] = field(default_factory=dict)
    #: ``{scope: next_value}`` from ``kyosei/export/_allocations.json``.
    disk_allocations: dict[str, int] = field(default_factory=dict)
    #: Scopes whose on-disk counter is ahead of the database's.
    export_ahead_scopes: tuple[str, ...] = ()
    #: Scopes whose database counter is ahead of the on-disk one.
    db_ahead_scopes: tuple[str, ...] = ()
    #: Record ids with an export file and no row in the database.
    records_only_in_export: tuple[str, ...] = ()
    #: Record ids with a row in the database and no export file.
    records_only_in_db: tuple[str, ...] = ()
    #: Why the direction could not be decided, when it could not. Always ``None``
    #: for the three decided directions — a reason attached to a decided answer is
    #: the decoration law 2 exists to prevent.
    undecidable_because: str | None = None

    @property
    def in_step(self) -> bool:
        return self.direction == CURRENCY_IN_STEP

    @property
    def db_is_behind(self) -> bool:
        """Is the database missing something the export holds?

        True for :data:`CURRENCY_EXPORT_AHEAD` and also for the undecidable case in
        which export files have no row behind them — a read surface's question is
        "am I about to present an incomplete record set as a complete one", and that
        is answered by the evidence, not by whether the direction could be named.
        """
        return self.direction == CURRENCY_EXPORT_AHEAD or bool(self.records_only_in_export)

    @property
    def would_reissue_an_id(self) -> bool:
        """Would allocating from this database break law 4? (KYO-75)

        Deliberately NOT :attr:`db_is_behind`, which is a *read* surface's question
        ("am I about to present an incomplete record set as a complete one"). A
        write's question is narrower and sharper: ``ledgers.write_allocations``
        rewrites the whole snapshot from the database, so the moment any on-disk
        counter is ahead, a write lowers a git-canonical counter and the next
        ``allocate`` in that scope hands out an id the export has already issued.
        That is this, and only this.

        The two predicates disagree in both directions, which is why they are two:

        * a genuine fork — both sides moved, ``cannot-tell`` — has counters the
          export is ahead on, so a write reissues an id, and yet ``db_is_behind`` is
          False whenever no record file lacks a row. The read question is answered
          "nothing missing"; the write question must still be answered "refuse".
        * an export document with no row behind it, over counters that agree, sets
          ``db_is_behind`` — a listing really would under-report — while no write
          against it lowers anything or reissues anything. Refusing there would block
          a consumer in the one state whose remedy is "do NOT run either command
          until you know which side you mean to keep" (:meth:`remedy`): a refusal
          with no action behind it, which is worse than the friction it prevents.
        """
        return bool(self.export_ahead_scopes)

    def sentence(self) -> str:
        """One line naming the direction and the evidence for it.

        Every surface that says something about this seam says it with this (law 3),
        so ``kyo list``, ``kyo brief`` and ``kyo doctor`` can never describe one
        comparison three different ways.
        """
        if self.direction == CURRENCY_EXPORT_AHEAD:
            return (
                "the database is behind its export: "
                f"{self._counter_evidence()}{self._record_evidence()}"
            )
        if self.direction == CURRENCY_DB_AHEAD:
            return (
                "the export is behind its database: "
                f"{self._counter_evidence()}{self._record_evidence()}"
            )
        if self.direction == CURRENCY_UNDECIDABLE:
            return (
                "cannot tell which side of kyosei.db / kyosei/export/ moved: "
                f"{self.undecidable_because}"
            )
        return "the database and kyosei/export/ are in step"

    def _counter_evidence(self) -> str:
        scopes = self.export_ahead_scopes or self.db_ahead_scopes
        parts = [
            f"{scope} counter {self.disk_allocations.get(scope, 0)} on disk vs "
            f"{self.db_allocations.get(scope, 0)} in the database"
            for scope in scopes
        ]
        return some_of(parts, separator="; ") if parts else "the id counters disagree"

    def _record_evidence(self) -> str:
        if self.records_only_in_export:
            return (
                f"; {len(self.records_only_in_export)} record(s) in kyosei/export/ with no "
                f"row in the database ({some_of(self.records_only_in_export)})"
            )
        if self.records_only_in_db:
            return (
                f"; {len(self.records_only_in_db)} record(s) in the database with no export "
                f"file ({some_of(self.records_only_in_db)})"
            )
        return ""

    def remedy(self) -> str:
        """What to run — including, where it applies, what running it would destroy."""
        if self.direction == CURRENCY_EXPORT_AHEAD:
            return (
                f"run `{REBUILD_REMEDY}` to rebuild kyosei.db from kyosei/export/ "
                "(the export is the newer side here, so nothing is lost)"
            )
        if self.direction == CURRENCY_DB_AHEAD:
            return (
                f"run `{REFRESH_REMEDY}` to regenerate kyosei/export/ from the database "
                "— it rewrites the documents and counters and re-appends any ledger row "
                "the database holds and the export lacks (KYO-404), so nothing the "
                "database recorded is lost"
            )
        if self.direction == CURRENCY_UNDECIDABLE:
            return (
                f"do NOT run `{REBUILD_REMEDY}` or `{REFRESH_REMEDY}` until you know which "
                "side you mean to keep: each one overwrites the other side, and which side "
                "is newer is precisely what could not be determined here. Compare "
                "kyosei/export/ against its git history first"
            )
        return ""


def _compare_allocations(
    db_allocations: dict[str, int], disk_allocations: dict[str, int]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(scopes where the export is ahead, scopes where the database is ahead)``."""
    export_ahead = []
    db_ahead = []
    for scope in sorted(set(db_allocations) | set(disk_allocations)):
        on_disk = int(disk_allocations.get(scope, 0))
        in_db = int(db_allocations.get(scope, 0))
        if on_disk > in_db:
            export_ahead.append(scope)
        elif in_db > on_disk:
            db_ahead.append(scope)
    return tuple(export_ahead), tuple(db_ahead)


def db_export_currency(store: Store) -> Currency:
    """Compare the database against ``kyosei/export/`` and name which side moved.

    Cheap by construction (see the module docstring): one small JSON read, one
    directory listing, two indexed selects. Nothing is rebuilt and no export
    document is re-derived — ``doctor_exports.check_exports`` already does that, and it is
    this function's *caller*, not its implementation.
    """
    from . import export as export_module
    from . import ledgers as ledgers_module

    export_directory = export_module.export_dir(store)
    db_allocations = ledgers_module.expected_allocations(store)
    disk_allocations = ledgers_module.read_allocations_in(export_directory)

    db_records = set(store.all_record_ids())
    exported_records = {p.stem for p in export_module.record_export_files_in(export_directory)}
    only_in_export = tuple(sorted(exported_records - db_records))
    only_in_db = tuple(sorted(db_records - exported_records))

    export_ahead, db_ahead = _compare_allocations(db_allocations, disk_allocations)

    common = {
        "db_allocations": db_allocations,
        "disk_allocations": disk_allocations,
        "export_ahead_scopes": export_ahead,
        "db_ahead_scopes": db_ahead,
        "records_only_in_export": only_in_export,
        "records_only_in_db": only_in_db,
    }

    if not disk_allocations and (db_allocations or exported_records):
        # `_allocations.json` is rewritten by every mutating command, so a store that
        # has ever been written to has one. Missing, it is the *counter* that is gone,
        # not a side of the seam that failed to move — there is nothing to compare.
        return Currency(
            CURRENCY_UNDECIDABLE,
            undecidable_because=(
                f"{ledgers_module.allocations_path_in(export_directory)} does not exist, so "
                "the id counters this comparison reads are not there to compare"
            ),
            **common,
        )

    if export_ahead and db_ahead:
        return Currency(
            CURRENCY_UNDECIDABLE,
            undecidable_because=(
                "both sides have moved — the export is ahead on "
                f"{len(export_ahead)} counter(s) ({some_of(export_ahead)}) and the "
                f"database is ahead on {len(db_ahead)} counter(s) ({some_of(db_ahead)})"
            ),
            **common,
        )
    if export_ahead:
        return Currency(CURRENCY_EXPORT_AHEAD, **common)
    if db_ahead:
        return Currency(CURRENCY_DB_AHEAD, **common)
    if only_in_export or only_in_db:
        # The counters say neither side has recorded an operation the other has not,
        # and yet the record sets differ. One of the two pieces of evidence is wrong
        # and this function cannot say which, so it says that (law 6) rather than
        # letting the record sets silently outvote the counters.
        return Currency(
            CURRENCY_UNDECIDABLE,
            undecidable_because=(
                "the id counters agree, so neither side has recorded an operation the "
                "other has not, and yet the record sets differ"
                + (
                    f" — {', '.join(only_in_export)} exist(s) only in kyosei/export/"
                    if only_in_export
                    else ""
                )
                + (
                    f" — {', '.join(only_in_db)} exist(s) only in the database"
                    if only_in_db
                    else ""
                )
                + ". A record document was added or removed by hand, or a rebuild was "
                "interrupted"
            ),
            **common,
        )
    return Currency(CURRENCY_IN_STEP, **common)


def read_surface_warning_from(currency: Currency) -> list[str]:
    """The lines a *read* surface adds when it is about to under-report (KYO-74.c1).

    Law 6: a success that does not say what succeeded is the failure this project
    keeps filing. ``kyo list`` printing "1 record(s)" over a store that holds two is
    exactly that, one layer below KYO-31's stale viewer — at the db/export seam
    instead of the html/store seam.

    Empty unless the database is actually missing something the export holds, so the
    common case costs one comparison and prints nothing. Takes the
    already-computed :class:`Currency` rather than a ``Store`` so a command that also
    puts the comparison in its ``--json`` payload runs it once (law 3: one comparison,
    one answer, however many surfaces render it).
    """
    if not currency.db_is_behind:
        return []
    return [
        f"WARNING: {currency.sentence()}.",
        f"  What you just read is incomplete. To make it current, {currency.remedy()}.",
    ]


def as_data(currency: Currency) -> dict:
    """The machine-readable form of one comparison, for a ``--json`` payload.

    A JSON consumer reading a record count is making exactly the mistake the human
    warning exists to prevent, so the same answer has to reach both surfaces
    (KYO-74.c1 names ``kyo list`` and ``kyo brief``, not their human renderings).
    """
    return {
        "direction": currency.direction,
        "db_is_behind": currency.db_is_behind,
        "records_only_in_export": list(currency.records_only_in_export),
        "records_only_in_db": list(currency.records_only_in_db),
        "export_ahead_scopes": list(currency.export_ahead_scopes),
        "db_ahead_scopes": list(currency.db_ahead_scopes),
        "undecidable_because": currency.undecidable_because,
        "says": currency.sentence(),
        "remedy": currency.remedy(),
    }


def missing_database_remedy(config) -> str:
    """What to tell someone whose ``kyosei.db`` is not there (KYO-73.c1).

    Takes a :class:`kyosei.config.Config` rather than a ``Store``, because the whole
    point is that no store can be opened — this is the first thing a fresh clone of a
    store repository hits, ``kyosei.db`` being gitignored there by design.

    ``kyo init`` was the remedy this message named for as long as the store lived in
    the code repository, where a missing database really did mean "no project here
    yet". Under the split layout the same message sends a consumer to a command that
    refuses them, because the store *is* there — only its database is not.

    KYO-342 is the same defect a third time, and the sharpest of the three: in a
    **linked git worktree** the remedy this named, `kyo rebuild`, is a write under
    ``kyosei/`` — the one act a dispatched worker is forbidden — and it would also
    rewrite ``config.yml`` onto the worker's own branch. Two consumer projects
    reported every worker of every wave reaching it. Usually there is now nothing to
    report at all, because the store resolves through to the main checkout
    (:func:`kyosei.config._store_through_main_checkout`); this branch is what is left
    when that cannot be done, and it names the checkout the database belongs in
    rather than a command to run where running it is the defect.
    """
    from . import export as export_module
    from .config import DB_NAME, KYOSEI_DIR, git_worktree_facts

    worktree = git_worktree_facts(config.root)
    if worktree.main_checkout is not None:
        # Reached only where the redirect did not fire: the main checkout holds no
        # store at this relative path, or holds one whose database is missing too.
        # The instruction is the same either way, and the path in the sentence is
        # what lets a reader tell which.
        return (
            f"this is a linked git worktree, whose main checkout is "
            f"{worktree.main_checkout}. `git worktree add` checks out tracked files "
            "only and `kyosei.db` is gitignored, so no worktree ever has a database "
            "of its own — a read here answers out of the main checkout's, and there "
            f"is none at {worktree.main_checkout / KYOSEI_DIR / DB_NAME} either. That "
            f"checkout is where it has to be built — `kyo rebuild` in "
            f"{worktree.main_checkout}, by whoever is working there — and this same "
            "read then works from this worktree unchanged, reading through to it. "
            f"Building one here instead writes {KYOSEI_DIR}/ on this branch, which is "
            "the one thing a dispatched worker must not do"
        )

    export_directory = config.export_path
    if not export_directory.is_dir():
        return (
            f"{export_directory} does not exist either, so there is nothing to rebuild "
            f"from — restore this store from its git repository, or start a new project "
            f"elsewhere with `kyo init`"
        )
    record_files = export_module.record_export_files_in(export_directory)
    return (
        f"run `kyo rebuild` to reconstruct it from {export_directory} "
        f"({len(record_files)} record document(s) there). `kyosei.db` is gitignored, so a "
        "fresh clone of a store repository has exactly this shape and this is its first "
        "command"
    )


__all__ = [
    "CURRENCY_DB_AHEAD",
    "CURRENCY_EXPORT_AHEAD",
    "CURRENCY_IN_STEP",
    "CURRENCY_UNDECIDABLE",
    "REBUILD_REMEDY",
    "REFRESH_REMEDY",
    "Currency",
    "as_data",
    "db_export_currency",
    "missing_database_remedy",
    "read_surface_warning_from",
]
