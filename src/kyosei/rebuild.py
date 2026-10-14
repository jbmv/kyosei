"""``kyo rebuild`` — reconstruct ``kyosei.db`` from ``kyosei/export/`` alone.

Charter, Layer 2: "Once ``kyo rebuild`` reconstructs a byte-identical store from
export and ``kyo doctor`` verifies the round-trip in both directions, ``kyosei.db``
is gitignored in consumer projects; ``kyosei/export/`` is the git-canonical
serialization." That promise only holds because the export is *complete*:

* the per-record ``<ID>.json`` files carry every record/criterion/note/gate/link,
  each with its own stable ``seq`` (never re-derived from allocation order);
* ``_events.jsonl`` carries the full audit log, append-only;
* ``_allocations.json`` carries the id counters. Deriving them from ``max(seq)`` ALONE
  would re-enable the reissued-id incident law 4 forbids: a scope whose highest-numbered
  row was later voided or deleted by hand would let the next allocation collide. So the
  snapshot is the floor it always was, and ``max(seq) + 1`` over the loaded rows is a
  second floor beneath it (KYO-414): the counter is whichever is higher, so neither a
  deleted row nor a snapshot written below the ledger can reissue an id.

Rebuild is a bulk load: foreign keys are held off for the duration (a record can
reference a plan created later in id order) and re-enabled before the transaction
commits.

KYO-179: it is also a **version boundary**, and until this record it was an unguarded
one. Every per-record document stamps the ``schema_version`` it was written at, and
nothing read it: the compat machinery infers the source format from the ABSENCE of a
field (``schema.EXPORT_FIELD_DEFAULTS``), which is sound for a document older than this
build and structurally incapable of noticing a newer one — a newer document is missing
nothing, it merely carries keys this build never looks at. So an export from a later
build rebuilt with a full exit 0, dropping whatever the later schema added, stamping this
build's version into the db, and rewriting ``kyosei/config.yml`` to match (KYO-5.c1) so
the project's own record claimed the downgrade was intended. :func:`read_record_documents`
is now the one door a record document enters this module through, it reads the stamp, and
it refuses an export this build cannot fully read before anything at all is written.

KYO-181 closes the half KYO-179 named and left open. A record document's stamp cannot see
a bump that changed only a LEDGER, because no ledger file carried a version at all — and
two of this store's own bumps were exactly that shape (KYO-63 at schema_version 10,
KYO-94 at 12). Under either, an export from the later build has ledger rows with keys this
build does not read, not one record document need have changed, and the extra keys were
dropped in silence. From schema_version 17 every ledger line carries its own
``schema_version`` (``export.stamp_ledger_row``), :func:`read_ledger_rows` is the one door
they enter through, and it refuses the same way and at the same moment — before the
existing database is unlinked and before ``config.yml`` is rewritten.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import events_ledger as events_ledger_module
from . import export as export_module
from . import ids
from . import ledgers as ledgers_module
from .config import Config, write_config
from .contracts import (
    installed_build_clause,
    stale_build_clause,
    upgrade_the_build_clause,
)
from .errors import (
    EXPORT_AHEAD_LEDGERS,
    EXPORT_AHEAD_RECORD_DOCUMENTS,
    ExportSchemaAhead,
    KyoseiError,
)
from .schema import (
    DDL,
    SCHEMA_VERSION,
    apply_brief_run_defaults,
    apply_event_defaults,
    apply_export_defaults,
    apply_sweep_run_defaults,
    stored_path_from_export,
)
from .store import (
    SCOPE_BRIEF,
    SCOPE_EVENT,
    SCOPE_LINK,
    SCOPE_RECORD,
    SCOPE_SHIFT_NOTE,
    SCOPE_SWEEP,
    connect,
    scope_for_child,
)


def rebuild_from_export(
    export_dir: Path,
    config: Config,
    *,
    force: bool = False,
    legacy_absolute_paths: list[str] | None = None,
    config_comments_dropped: list[str] | None = None,
    stale_export_documents: list[str] | None = None,
    load_ledgers: bool = True,
) -> int:
    """Build a fresh ``kyosei.db`` at ``config.db_path`` from ``export_dir``.

    Refuses an existing database unless ``force`` (law 6: rebuild must never silently
    discard a live store). Returns the number of records loaded.

    KYO-3.c3: on ANY failure after the db file is created — a bad export, a
    dangling foreign key, an interrupted process — the partial file is removed
    before the error propagates, so a failed rebuild never leaves a ``kyosei.db``
    that blocks the retry with "already exists" (the second live failure KYO-3
    observed: a hand migration plus ``rebuild --force`` was needed to recover).

    KYO-5.c1: on success, ``kyosei/config.yml``'s ``schema_version`` is rewritten
    to match the schema this build (and the db it just built) actually speaks —
    the db's own ``meta.schema_version`` and this write always agree by
    construction, because both come from the same :data:`kyosei.schema.SCHEMA_VERSION`
    in the same call. Before this, ``kyo rebuild`` left ``config.yml`` at whatever
    version it already declared, so a rebuild that changed schema versions produced
    a *new* drift between db and config — and the drift error's own named remedy,
    "run `kyo rebuild`", did not fix it, because rebuild never touched config.yml
    (the exact bug KYO-5 was filed against).

    KYO-52: pass a list as ``legacy_absolute_paths`` to be told, without anything
    being changed, which path-typed values in this export are still absolute —
    written before schema_version 9, when ``dispatches.worktree`` and
    ``touches.path`` held resolved absolute paths. They load verbatim: removing what
    is already committed to ``kyosei/export/`` is a git history rewrite and the
    maintainer's call, not a rebuild's. Counting them is the part a rebuild can
    honestly do, because an absolute path is inert data that otherwise survives in
    silence.

    KYO-158: pass a list as ``config_comments_dropped`` to be told the same way about
    ``kyosei/config.yml``. This function rewrites that file on every run (KYO-5.c1
    again), and ``config.yml`` is git-tracked in a consumer project, so anything the
    rewrite does not reproduce arrives as an unexplained diff — or is not looked at,
    and gets committed. The rewrite normally edits the version number in place and
    keeps every comment; when it cannot, the lines it did not carry are collected
    here instead of disappearing quietly.

    KYO-216: pass a list as ``stale_export_documents`` to be told which record ids were
    read from a document stamping an OLDER ``schema_version`` than :data:`SCHEMA_VERSION`
    — the version this call just wrote into the fresh database (KYO-5.c1's
    ``config.yml`` rewrite makes that the same number, always). A rebuild reads every
    export document exactly as it was written and changes not one byte of it, so the
    moment a schema bump changes what this build writes to the db, ``kyosei/export/``
    is behind it — every document there still stamps the version it was written at,
    which doctor's ``exports``/``export_roundtrip`` checks then report as drift on the
    very next run (the bug this record was filed over: 396 findings across two checks
    for one format bump, with no pointer at the moment it mattered). Nothing here
    rewrites those documents — ``kyo export --refresh`` is the one command that does,
    and it is the caller's remedy to name (``cmd_store.cmd_rebuild``) — this only
    reports which ones are now behind, the same way ``legacy_absolute_paths`` reports
    without changing anything.

    KYO-408: ``load_ledgers=False`` builds the record half only — every record document,
    the counters, and EMPTY events/sweeps/briefs/shift-notes tables. It exists for
    ``kyo doctor --hook``, whose round-trip compares record documents and counters and
    nothing else: no record document is derived from a ledger row and no ledger table
    carries a foreign key, so it is the same comparison with the whole ledger (hundreds
    of megabytes, held in memory at once) left out. A database built this way is a
    probe, never a store: ``kyo rebuild`` never passes it.
    """
    if not export_dir.is_dir():
        raise KyoseiError(f"{export_dir} does not exist; nothing to rebuild from")

    # KYO-179.c2: the whole export is read and version-checked HERE, before the existing
    # database is unlinked, before a new one is created, and before `write_config` below
    # rewrites `kyosei/config.yml`. A refusal that fired later would already have deleted
    # the store it was protecting (`--force` unlinks four lines down) or left the
    # project's own declaration rewritten to this build's older version — the state
    # KYO-179 is about, reached by the command that refused to reach it.
    # KYO-181.c2: and the ledgers with them, in the same breath and for the same reason.
    # Both halves of the export are read and version-checked before the first destructive
    # line below, so an export that is ahead in EITHER half costs the project nothing —
    # a guard that covered only the documents would still delete a live store on the
    # ledger-only bump that is the common shape (KYO-63, KYO-94).
    documents = read_record_documents(export_dir)
    ledgers = read_ledger_rows(export_dir, read=load_ledgers)
    # KYO-407.c4: and the counter snapshot with them. `_load` used to read it for itself,
    # after `--force` had already unlinked the database four lines down, so a torn
    # `_allocations.json` failed the rebuild with the store it was rebuilding gone and
    # every later command reporting a missing database. Read here, a file that does not
    # parse refuses (`export.ExportFileUnreadable`, naming the file and the `git
    # checkout` that restores it) while the existing database is still in place.
    allocations = ledgers_module.read_allocations_in(export_dir)

    # KYO-216: which documents this rebuild is about to leave behind, computed off
    # the same read this call already did — not a second pass over the export.
    # `apply_export_defaults` never rewrites an existing `schema_version` key (it
    # only fills in fields an OLDER format left absent), so the stamp read here is
    # exactly what was on disk; `document_schema_version` supplies the one default
    # a document with no stamp at all still needs.
    if stale_export_documents is not None:
        for doc in documents:
            stamped = export_module.document_schema_version(doc, where=f"{doc['id']}.json")
            if stamped < SCHEMA_VERSION:
                stale_export_documents.append(doc["id"])

    if config.db_path.exists():
        if not force:
            raise KyoseiError(
                f"{config.db_path} already exists; refusing to rebuild over it "
                "(pass --force to overwrite)"
            )
        config.db_path.unlink()

    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(config.db_path)
    try:
        conn.executescript(DDL)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        record_count = _load(
            conn,
            export_dir,
            documents,
            ledgers,
            legacy_absolute_paths if legacy_absolute_paths is not None else [],
            allocations=allocations,
        )
        conn.execute("PRAGMA foreign_keys = ON")
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise KyoseiError(
                f"rebuild produced {len(problems)} dangling foreign key(s); export is corrupt"
            )
    except BaseException:
        conn.close()
        config.db_path.unlink(missing_ok=True)
        raise
    else:
        conn.close()

    # KYO-5.c1: the db now speaks SCHEMA_VERSION; make config.yml say the same,
    # so the two never disagree the moment rebuild finishes (law 3: derive, don't
    # duplicate — this is the second copy, kept in step by construction).
    #
    # KYO-158: this call passes the required keys only, and everything else in the
    # file survives because `write_config` carries forward every key
    # `OPTIONAL_CONFIG_KEYS` declares rather than a list somebody has to remember to
    # extend. It normally updates `schema_version` in place, so a project's own
    # comments survive too; `config_comments_dropped` collects what a full re-render
    # could not keep, on the occasions one is needed, so the rebuild says what it did
    # rather than leaving an unexplained diff in a git-tracked file (KYO-158.c4).
    write_config(
        config.root,
        project_prefix=config.project_prefix,
        export_dir=config.export_dir,
        rewritten_wholesale=config_comments_dropped,
    )
    return record_count


def read_record_documents(export_dir: Path) -> list[dict]:
    """Every record document in ``export_dir``, checked and defaulted.

    **KYO-179: the one door a record document enters this module through, and it reads
    the version the document stamps in itself before it hands anything back.** Nothing
    else here opens a per-record file, and ``tests/test_schema_compat.py`` walks this
    module's AST to keep it that way — the property, not the instance. A second
    document-reading path that skipped this function would reintroduce the whole defect
    while every test of the refusal below stayed green.

    Two things happen here, in this order and for one reason each:

    * **Refuse a document from a later build.** ``export.document_schema_version`` reads
      the stamp; a document above this build's :data:`kyosei.schema.SCHEMA_VERSION` is
      refused, because loading it would reconstruct the store from the fields this build
      happens to know and drop whatever the later schema added — silently, since the keys
      it cannot read are keys it never looks at. See
      :func:`_refuse_documents_from_a_later_build` for the message and why it names no
      command.
    * **Fill in what an earlier format did not write** (KYO-3.c2). ``apply_export_defaults``
      applies schema.py's small, explicit per-version defaults table, rather than a
      scattered ``.get(..., [])`` at every call site — a pre-M3 document with no
      ``touches`` key loads as empty touches. Deliberately *after* the refusal: defaulting
      a document this build cannot read is work that must not be visible to anything.

    The whole set is read before any of it is checked, and the checked set is the set
    returned. A guard that re-opened the files after passing them would be checking one
    export and loading another.
    """
    documents = [
        # KYO-407.c3: through the export's one JSON loader, so a torn document is
        # refused naming its file, line and `git checkout` rather than as a bare
        # JSONDecodeError traceback.
        (path, export_module.load_json_file(path))
        for path in export_module.record_export_files_in(export_dir)
    ]
    _refuse_documents_from_a_later_build(export_dir, documents)
    return [apply_export_defaults(doc) for _, doc in documents]


def _refuse_documents_from_a_later_build(
    export_dir: Path, documents: list[tuple[Path, dict]]
) -> None:
    """KYO-179.c1: refuse an export whose documents were written by a later schema.

    The mirror of ``store.open_store``'s ahead-direction refusal (KYO-168), at the
    boundary that actually travels between builds: ``kyosei/export/`` is the
    git-canonical serialization, and ``kyosei.db`` is gitignored. The db boundary at
    least had a refusal to reword; this one had no guard at all.

    Every document is checked, not the first — nothing writes ``kyosei/export/``
    transactionally, so a half-written export in which some documents are ahead and some
    are not is a state that exists, and it is refused on the strength of any one of them.
    Mixed versions *at or below* this build stay ordinary: that is what the compat
    machinery is for, and every pre-9/pre-11/pre-13/KYO-52 tolerance is untouched here.

    Deliberately names no command that would reconstruct the store, for the reason
    ``store.py`` gives one boundary over: every such command writes the older schema over
    the newer one. Deliberately readable out of a terminal too — the desktop app shows
    it through ``kyo serve``, and KYO-175 points it at stores it does not control, so the
    reader of this sentence may not be at a shell and may not be the person who installed
    anything.
    """
    stamped = sorted(
        (path.name, export_module.document_schema_version(doc, where=path.name))
        for path, doc in documents
    )
    ahead = [(name, version) for name, version in stamped if version > SCHEMA_VERSION]
    if not ahead:
        return
    _refuse_an_export_from_a_later_build(
        export_dir,
        noun="record document(s)",
        source=EXPORT_AHEAD_RECORD_DOCUMENTS,
        ahead_count=len(ahead),
        shown=[f"{name} (schema_version {version})" for name, version in ahead],
        files=[{"file": name, "schema_version": version} for name, version in ahead],
        highest=max(version for _, version in ahead),
        record="KYO-179",
    )


def _refuse_an_export_from_a_later_build(
    export_dir: Path,
    *,
    noun: str,
    source: str,
    ahead_count: int,
    shown: list[str],
    files: list[dict],
    highest: int,
    record: str,
) -> None:
    """The one refusal both export-boundary version guards raise (KYO-181).

    KYO-179 wrote this message for record documents; the ledger guard beside it is the
    same mechanism reaching further into the same directory, not a second one, so it says
    the same thing in the same order — and the wording lives in one place rather than in
    two that can drift apart (law 3). What varies is only what the reader needs in order
    to act: which half of the export is ahead, which files, and which record to look up.

    Deliberately names no command, for the reason ``store.py`` gives one boundary over and
    the reason KYO-180 gives generally about remedy ordering: the lossless remedy is named
    first and here it is the ONLY one, because every command that would make this export
    loadable by this build writes the older schema over the newer one. There is no
    destructive remedy to put second — offering one at all would be offering the harm.

    Readable out of a terminal too (KYO-175): the reader may not be at
    a shell and may not be the person who installed anything.

    Raised INLINE as :class:`kyosei.errors.ExportSchemaAhead`, and that is load-bearing
    rather than stylistic. The first draft built a plain ``KyoseiError``, assigned
    ``.data`` and raised the variable — which works, and is invisible to the remedy walk
    in ``tests/test_kyo5_error_remedies.py``, because that walk matches raises by CLASS
    NAME on the ``raise`` node itself. The site silently left the surface: this message
    could then have started naming a destructive command with nothing to notice. The
    check caught it, and the fix is the one KYO-178 already wrote down — a class whose
    required keyword arguments make the payload impossible to leave off.
    """
    listed = ", ".join(shown[:3])
    if len(shown) > 3:
        listed += f", and {len(shown) - 3} more"
    raise ExportSchemaAhead(
        f"{export_dir} holds {ahead_count} {noun} written by a later schema: "
        f"{listed}. This kyosei build speaks schema_version {SCHEMA_VERSION}, which is "
        "older. "
        # KYO-182: the three clauses this shares verbatim with `store.open_store`'s
        # ahead-direction refusal come from `contracts` and are not restated here. The
        # sentences that are ITS OWN — an export directory and named files, and that
        # nothing was written — stay, because they are what tell this reader which
        # artifact is meant and what state it was left in.
        + stale_build_clause(artifact="export", cannot="read an export")
        + " "
        + upgrade_the_build_clause(highest)
        + " Nothing has been written — no database was created and no config.yml was "
        "touched — because reconstructing this store under schema_version "
        f"{SCHEMA_VERSION} would load only the fields this build knows, drop whatever the "
        "later schema added, and then rewrite the project's own schema_version to record "
        "the downgrade as if it were intended. " + installed_build_clause() + f" — {record}",
        # KYO-178's shape: the payload rides `exc.data`, which `cli.main` merges into the
        # `--json` error envelope, so it arrives out of whatever command was running when
        # the export refused to load — no new command and no second call.
        source=source,
        build_schema_version=SCHEMA_VERSION,
        export_schema_version=highest,
        files=files,
    )


def read_ledger_rows(export_dir: Path, *, read: bool = True) -> dict[str, list[dict]]:
    """Every append-only ledger in ``export_dir``, keyed by filename, version-checked.

    **KYO-181: the one door a ledger row enters this module through**, and the exact
    counterpart of :func:`read_record_documents` one artifact class over. Nothing else
    here reads a ledger, and ``tests/test_schema_compat.py`` walks this module's AST to
    keep it that way — the property, not the instance, for the reason KYO-179 gave: a
    second reading path that skipped the check would reintroduce the whole defect while
    every test of the refusal below stayed green.

    KYO-179 closed this boundary for record documents and said, in the note further down,
    exactly what it was leaving open: a bump that touched only a ledger changes no record
    document, so nothing stamped a version anywhere its guard could see. Two of this
    store's own bumps were that shape (KYO-63 at 10, KYO-94 at 12), which makes it the
    common case rather than the exotic one. From schema_version 17 the ledger lines carry
    their own stamp (:func:`kyosei.export.stamp_ledger_row`) and this reads it.

    The ledgers are read through :data:`kyosei.ledgers.LEDGER_READERS` rather than by
    naming four files here, so a fifth ledger is guarded by existing rather than by
    somebody remembering to extend a list.

    What this deliberately does NOT do is fill in defaults, which is where it differs
    from its record-document twin. The pre-10 and pre-12 ledger tolerances
    (``schema.apply_sweep_run_defaults`` / ``apply_brief_run_defaults``) stay exactly
    where they were, at their own load sites, keyed on the absence of the FIELD rather
    than on any version number — so a line written before this bump, carrying no stamp
    and no ``coverage``, is tolerated by precisely the machinery that always tolerated it
    (KYO-181.c4). Adding a stamp gave the ledgers a ceiling, not a new floor.

    KYO-408: ``read=False`` answers every registered ledger as empty without opening
    one — the shape :func:`rebuild_from_export`'s ``load_ledgers=False`` needs, answered
    here so that this function stays the one place in the module that names the
    registry.
    """
    if not read:
        return {name: [] for name in ledgers_module.LEDGER_READERS}
    ledgers = {name: reader(export_dir) for name, reader in ledgers_module.LEDGER_READERS.items()}
    _refuse_ledger_rows_from_a_later_build(export_dir, ledgers)
    return ledgers


def _refuse_ledger_rows_from_a_later_build(
    export_dir: Path, ledgers: dict[str, list[dict]]
) -> None:
    """KYO-181.c2: refuse an export whose ledger lines were written by a later schema.

    Every line of every ledger is checked, not the first of each — the same reason
    :func:`_refuse_documents_from_a_later_build` checks every document, and a stronger
    one here: a ledger is append-only, so a file whose first thousand lines predate a
    bump and whose last line was written after it is not a corrupt state at all, it is
    the NORMAL state of every ledger that has outlived a bump. Mixed versions at or below
    this build stay ordinary; a single line above it refuses the export.

    Reported per file rather than per line, with a count. A line number would be a
    position in a file that ``kyo redact`` may rewrite and that git may merge, and the
    reader's next action is to install a newer build either way — the file and the version
    are what that decision needs.
    """
    ahead: list[tuple[str, int, int]] = []
    total = 0
    for name in sorted(ledgers):
        later = [
            version
            for version in (
                export_module.ledger_schema_version(row, where=name) for row in ledgers[name]
            )
            if version > SCHEMA_VERSION
        ]
        if later:
            ahead.append((name, max(later), len(later)))
            total += len(later)
    if not ahead:
        return
    _refuse_an_export_from_a_later_build(
        export_dir,
        noun="ledger line(s)",
        source=EXPORT_AHEAD_LEDGERS,
        ahead_count=total,
        shown=[
            f"{name} ({count} line(s) at schema_version {version})"
            for name, version, count in ahead
        ],
        files=[
            {"file": name, "schema_version": version, "lines": count}
            for name, version, count in ahead
        ],
        highest=max(version for _, version, _ in ahead),
        record="KYO-181",
    )


# KYO-179, the edge this record left open and the decision taken on it:
# ``kyosei/config.yml`` stamps a ``schema_version`` too, it is present when no record
# document is, and it is deliberately NOT a signal here.
#
# The case for consulting it is real. A record document is only rewritten when its record
# is touched, so a project whose build moved forward but whose records have not been
# mutated since has an export in which every document still stamps the older version and
# config.yml is the only thing that knows.
#
# KYO-179 had a sharper version of that argument, and KYO-181 has since removed it rather
# than answered it: the four ledgers carried no stamp at all, so for a bump that touched
# only a ledger — KYO-63 at 10 and KYO-94 at 12, both of this store's own — config.yml was
# the ONLY version signal in the whole export. It is not any more. The ledger lines carry
# their own stamp from schema_version 17 (``export.stamp_ledger_row``), and
# ``read_ledger_rows`` above refuses on it. What is left unstamped is ``_allocations.json``,
# which is a snapshot rewritten in full rather than a ledger, and which is its own record's
# problem for the reasons ``ledgers.LEDGER_READERS`` sets out.
#
# It is not consulted anyway, because refusing on it would break a shipped contract this
# record does not have standing to overturn. KYO-5.c1 makes ``kyo rebuild --force``
# precisely the remedy for a config.yml whose ``schema_version`` disagrees — that is what
# KYO-5 was filed about, and ``store.open_store``'s config/db drift refusal names that
# command in its message, in both directions, because config.yml is the one version copy
# this codebase already treats as possibly mis-edited by hand. Three tests in
# ``tests/test_kyo5_error_remedies.py`` hold it down, one of them by proving that the
# named remedy actually clears the error it is named in. A guard here that refused an
# ahead config would make that named remedy fail on the exact case those tests use, and
# a tool cannot tell a store that is genuinely on a later schema from a config.yml
# somebody typed a wrong number into: the stamp is not bound to any data.
#
# So the rule is: the version signal at this boundary is the one the LOADED BYTES carry —
# the documents' stamp and, since KYO-181, the ledger lines' — because it is physically
# bound to the data about to be read. config.yml alone never blocks a rebuild, and ``kyo
# rebuild`` goes on rewriting it (KYO-5.c1). KYO-181 closing the ledger hole is what makes
# that rule cheap to hold rather than a debt: the case config.yml was the only signal for
# now has a signal of its own, bound to data, and nobody has to weaken someone else's
# remedy to reach it.
# ``test_a_config_that_alone_declares_a_later_schema_still_rebuilds`` in
# ``tests/test_schema_compat.py`` pins this decision, so the next person to reach for the
# obvious guard trips over the reason first.


#: KYO-414: every table whose ``seq`` is handed out by an allocation scope, and the
#: scope. Project-wide scopes first, then the per-record child tables keyed by the child
#: kind :func:`kyosei.store.scope_for_child` names their scope after. One list, read by
#: :func:`loaded_allocation_highs` for both rebuild's seeding and ``kyo doctor``'s
#: ``check_allocations`` -- the doctor's own hand-written copy of the child half had
#: already dropped ``doc_refs`` and ``docs_obligations``, and
#: ``tests/test_store_races.py`` holds this list against ``ids.CHILD_KIND_BY_LETTER``.
ALLOCATED_PROJECT_TABLES: tuple[tuple[str, str], ...] = (
    ("records", SCOPE_RECORD),
    ("links", SCOPE_LINK),
    ("events", SCOPE_EVENT),
    ("sweep_runs", SCOPE_SWEEP),
    ("brief_runs", SCOPE_BRIEF),
    ("shift_notes", SCOPE_SHIFT_NOTE),
)
ALLOCATED_CHILD_TABLES: tuple[tuple[str, str], ...] = (
    ("criteria", "criterion"),
    ("notes", "note"),
    ("gates", "gate"),
    ("touches", "touch"),
    ("claims", "claim"),
    ("verifications", "verification"),
    ("evidence_artifacts", "evidence"),
    ("dispatches", "dispatch"),
    ("reports", "report"),
    ("flags", "flag"),
    ("doc_refs", "doc-ref"),
    ("docs_obligations", "docs-obligation"),
)


def loaded_allocation_highs(conn: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """``(scope, highest seq in use, what uses it)`` for every scope with a row in use.

    What law 4 needs of a counter is exactly one thing: that it is above every id it
    has handed out. This is the other half of that comparison, read from the rows."""
    highs: list[tuple[str, int, str]] = []
    for table, scope in ALLOCATED_PROJECT_TABLES:
        high = conn.execute(f"SELECT MAX(seq) AS high FROM {table}").fetchone()[0]
        if high is not None:
            highs.append((scope, int(high), table))
    for table, child_kind in ALLOCATED_CHILD_TABLES:
        for record_id, high in conn.execute(
            f"SELECT record_id, MAX(seq) AS high FROM {table} GROUP BY record_id"
        ):
            highs.append(
                (scope_for_child(record_id, child_kind), int(high), f"{table} of {record_id}")
            )
    return highs


def _load(
    conn: sqlite3.Connection,
    export_dir: Path,
    documents: list[dict],
    ledgers: dict[str, list[dict]],
    legacy_absolute_paths: list[str],
    *,
    allocations: dict[str, int] | None = None,
) -> int:
    """Insert an already-read, already-checked document set, plus the ledgers.

    KYO-179: the documents arrive as an argument rather than being opened here. This
    function used to list and parse them itself, and that is exactly what made the guard
    easy to bypass — the reading happened inside the loading, at the point where a db
    already existed and the version question was three hundred lines too late to ask.
    :func:`read_record_documents` is now the only reader, and this takes what it returns.

    KYO-181: the ledgers arrive the same way, for what turned out to be the same reason.
    They used to be read here, on the grounds that they carried no ``schema_version`` to
    consult — which was true, and was the defect rather than a justification for it. They
    carry one from schema_version 17 on, :func:`read_ledger_rows` is now their only
    reader, and this takes what that returns. ``export_dir`` is still a parameter for
    ``_allocations.json`` alone, which is a rewritten snapshot rather than a ledger and
    carries no stamp.

    KYO-407.c4: ``allocations`` is that snapshot already read, which is how
    :func:`rebuild_from_export` passes it -- read before anything destructive, like the
    documents and ledgers. ``None`` reads it from ``export_dir`` here, for a caller
    building a probe database that has nothing to protect.
    """
    if allocations is None:
        allocations = ledgers_module.read_allocations_in(export_dir)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for doc in documents:
            conn.execute(
                "INSERT INTO records (id, seq, kind, title, status, plan_id, scope,"
                " since_note, description, wont_do_reason, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc["id"],
                    int(ids.RECORD_RE.match(doc["id"]).group(2)),
                    doc["kind"],
                    doc["title"],
                    doc["status"],
                    doc["plan"],
                    # KYO-100: present on every document this build writes; filled in as
                    # None for a pre-13 export by `apply_export_defaults` above, which is
                    # where the "a document missing the key declared no scope" rule lives.
                    doc["scope"],
                    # KYO-498: None for a pre-26 export (EXPORT_FIELD_DEFAULTS[26]). Not
                    # checked against the shift-note ledger here: that loads in a later
                    # pass, and the write-time check in `kyo new`/`kyo edit` is the guard.
                    doc["since_note"],
                    doc["description"],
                    doc["wont_do_reason"],
                    doc["created_at"],
                    doc["updated_at"],
                ),
            )

        # Evidence artifacts load before verifications, which reference them by id
        # (foreign keys are OFF for the duration of this load regardless, but this
        # keeps insertion order matching the reference direction).
        for doc in documents:
            for artifact in doc["evidence_artifacts"]:
                conn.execute(
                    "INSERT INTO evidence_artifacts (id, record_id, seq, text, sha256,"
                    " supports_id, author, created_at, redacted_at, redacted_why)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact["id"],
                        doc["id"],
                        artifact["seq"],
                        artifact["text"],
                        artifact["sha256"],
                        artifact["supports_id"],
                        artifact["author"],
                        artifact["created_at"],
                        artifact["redacted_at"],
                        artifact["redacted_why"],
                    ),
                )
            for claim in doc["claims"]:
                conn.execute(
                    "INSERT INTO claims (id, record_id, seq, statement, level, method,"
                    " author, created_at, supersedes, superseded_by, superseded_at,"
                    " redacted_at, redacted_why)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        claim["id"],
                        doc["id"],
                        claim["seq"],
                        claim["statement"],
                        claim["level"],
                        claim["method"],
                        claim["author"],
                        claim["created_at"],
                        claim["supersedes"],
                        claim["superseded_by"],
                        claim["superseded_at"],
                        claim["redacted_at"],
                        claim["redacted_why"],
                    ),
                )

        seen_links: set[str] = set()
        for doc in documents:
            for criterion in doc["criteria"]:
                conn.execute(
                    "INSERT INTO criteria (id, record_id, seq, text, state, reason,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        criterion["id"],
                        doc["id"],
                        criterion["seq"],
                        criterion["text"],
                        criterion["state"],
                        criterion["reason"],
                        criterion["created_at"],
                        criterion["updated_at"],
                    ),
                )
                for verification in criterion["verifications"]:
                    conn.execute(
                        "INSERT INTO verifications (id, criterion_id, record_id, seq, verdict,"
                        " method, reason, author, created_at, evidence_id)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            verification["id"],
                            criterion["id"],
                            doc["id"],
                            verification["seq"],
                            verification["verdict"],
                            verification["method"],
                            verification["reason"],
                            verification["author"],
                            verification["created_at"],
                            verification["evidence_id"],
                        ),
                    )
            for note in doc["notes"]:
                conn.execute(
                    "INSERT INTO notes (id, record_id, seq, text, created_at,"
                    " superseded_by, superseded_at, redacted_at, redacted_why)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        note["id"],
                        doc["id"],
                        note["seq"],
                        note["text"],
                        note["created_at"],
                        note["superseded_by"],
                        note["superseded_at"],
                        note["redacted_at"],
                        note["redacted_why"],
                    ),
                )
            for touch in doc["touches"]:
                conn.execute(
                    "INSERT INTO touches (id, record_id, seq, path, symbol, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        touch["id"],
                        doc["id"],
                        touch["seq"],
                        # KYO-52: relative from schema_version 9 on, absolute in an
                        # older document (kept verbatim; see the function's docstring).
                        stored_path_from_export(
                            touch["path"],
                            where=f"touches.path on {touch['id']}",
                            legacy=legacy_absolute_paths,
                        ),
                        touch["symbol"],
                        touch["created_at"],
                    ),
                )
            # KYO-137. `doc_refs.path` has been root-relative since the column existed
            # (schema_version 16), so it needs none of the pre-9 absolute-path tolerance
            # `touches.path` above carries: there is no earlier format it could have
            # been written in, and wrapping it in that rule would claim otherwise.
            for doc_ref in doc["doc_refs"]:
                conn.execute(
                    "INSERT INTO doc_refs (id, record_id, seq, path, symbol, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        doc_ref["id"],
                        doc["id"],
                        doc_ref["seq"],
                        doc_ref["path"],
                        doc_ref["section"],
                        doc_ref["created_at"],
                    ),
                )
            for obligation in doc["docs_obligations"]:
                conn.execute(
                    "INSERT INTO docs_obligations (id, record_id, seq, dispatch_id,"
                    " doc_path, section, cause, detail, created_at, verdict, method,"
                    " author, settled_at, settled_why)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        obligation["id"],
                        doc["id"],
                        obligation["seq"],
                        obligation["dispatch_id"],
                        obligation["doc_path"],
                        obligation["section"],
                        obligation["cause"],
                        obligation["detail"],
                        obligation["created_at"],
                        obligation["verdict"],
                        obligation["method"],
                        obligation["author"],
                        obligation["settled_at"],
                        obligation["settled_why"],
                    ),
                )
            for gate in doc["gates"]:
                conn.execute(
                    "INSERT INTO gates (id, record_id, seq, gate_type, detail, tags_json,"
                    " created_at, cleared_at, cleared_why) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        gate["id"],
                        doc["id"],
                        gate["seq"],
                        gate["type"],
                        gate["do"],
                        # KYO-102: NULL and `[]` are the same fact (this gate carries no
                        # tags), and NULL is the one the column stores, so a document
                        # written before the field round-trips back to the state it
                        # describes rather than to an empty-list-shaped near-miss that
                        # `check_exports` would then report as a difference.
                        json.dumps(gate["tags"]) if gate["tags"] else None,
                        gate["created_at"],
                        gate["cleared_at"],
                        gate["cleared_why"],
                    ),
                )
            # Milestone 5 (KYO-7): dispatches and reports point at each other
            # (dispatch.report_id -> reports, reports.dispatch_id -> dispatches);
            # foreign keys are OFF for the whole load (see module docstring), so
            # insertion order between the two does not matter here.
            for dispatch in doc["dispatches"]:
                conn.execute(
                    "INSERT INTO dispatches (id, record_id, seq, state, brief,"
                    " worked_from_sha, worktree, branch, worker_model,"
                    " touch_override_json,"
                    " covers_json, report_id, terminated_why, closed_at,"
                    " tick_audit_json, created_at,"
                    " updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        dispatch["id"],
                        doc["id"],
                        dispatch["seq"],
                        # KYO-345: loaded verbatim, and there is deliberately no
                        # defaults entry for the `withdrawn` member schema_version 22
                        # added to this enum. The tables in `schema` answer "an older
                        # document is MISSING a key this build reads"; a state value
                        # this build does not know is the opposite direction, and it
                        # is refused whole one door up, on the document's own stamp
                        # (`_refuse_an_export_from_a_later_build`, KYO-179), rather
                        # than rewritten here into a value nobody wrote.
                        dispatch["state"],
                        dispatch["brief"],
                        dispatch["worked_from_sha"],
                        # KYO-52: the value's *meaning* changed at schema_version 9
                        # (root-relative, not absolute) without the column changing,
                        # so the version — not the column — is what says how to read
                        # it. schema.DISPATCH_FIELD_DEFAULTS[6] has already filled in
                        # None for a document older than the field itself.
                        stored_path_from_export(
                            dispatch["worktree"],
                            where=f"dispatches.worktree on {dispatch['id']}",
                            legacy=legacy_absolute_paths,
                        ),
                        # KYO-32: schema.DISPATCH_FIELD_DEFAULTS[8] has already
                        # filled this in as None for any document exported under
                        # schema_version < 8, so the subscript is safe and a
                        # pre-KYO-32 export rebuilds as "no branch declared".
                        dispatch["branch"],
                        # KYO-61: schema.DISPATCH_FIELD_DEFAULTS[11] has already
                        # filled this in as None for any document exported under
                        # schema_version < 11, so a pre-KYO-61 export rebuilds as
                        # "no model was ever declared" — which is exactly what was
                        # true of it, rather than a model name invented at load time.
                        dispatch["worker_model"],
                        json.dumps(dispatch["touch_override"], sort_keys=True)
                        if dispatch["touch_override"] is not None
                        else None,
                        json.dumps(dispatch["covers"], sort_keys=True)
                        if dispatch.get("covers")
                        else None,
                        dispatch["report_id"],
                        dispatch["terminated_why"],
                        dispatch["closed_at"],
                        # KYO-375: schema.DISPATCH_FIELD_DEFAULTS[23] has already
                        # filled this in as None for any document exported under
                        # schema_version < 23, which is exactly the truth: every tick
                        # before this shipped wrote a row to the events ledger
                        # instead, and this column carries only what was counted
                        # since.
                        json.dumps(dispatch["tick_audit"], sort_keys=True)
                        if dispatch.get("tick_audit")
                        else None,
                        dispatch["created_at"],
                        dispatch["updated_at"],
                    ),
                )
            for report in doc["reports"]:
                conn.execute(
                    "INSERT INTO reports (id, record_id, dispatch_id, seq, worked_from_sha,"
                    " body_json, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        report["id"],
                        doc["id"],
                        report["dispatch_id"],
                        report["seq"],
                        report["worked_from_sha"],
                        json.dumps(report["body"], sort_keys=True),
                        report["author"],
                        report["created_at"],
                    ),
                )
            # Milestone 6 (KYO-8): flags are a child of the record they primarily
            # concern; foreign keys stay OFF for the whole load (module docstring),
            # so `other_record_id` naming a record inserted later in id order is
            # fine.
            for flag in doc["flags"]:
                conn.execute(
                    "INSERT INTO flags (id, record_id, seq, flag_type, detail,"
                    " other_record_id, other_criterion_id, sweep_run_id, created_at,"
                    " resolved_at, resolved_by, resolved_why)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        flag["id"],
                        doc["id"],
                        flag["seq"],
                        flag["flag_type"],
                        flag["detail"],
                        flag["other_record_id"],
                        flag["other_criterion_id"],
                        flag["sweep_run_id"],
                        flag["created_at"],
                        flag["resolved_at"],
                        flag["resolved_by"],
                        flag["resolved_why"],
                    ),
                )
            for link in doc["links"]:
                if link["direction"] != "outgoing" or link["id"] in seen_links:
                    continue
                seen_links.add(link["id"])
                conn.execute(
                    "INSERT INTO links (id, seq, src_id, edge_type, dst_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        link["id"],
                        link["seq"],
                        doc["id"],
                        link["type"],
                        link["other"],
                        link["created_at"],
                    ),
                )

        for event in ledgers[events_ledger_module.EVENTS_FILENAME]:
            # KYO-418: a line written before schema_version 25 carries a whole `before`
            # and no `changed`; one written since carries `changed` and no `before`.
            # Both load, each into its own column, and the readers key on which one a
            # row holds -- the defaults say what either missing key means.
            event = apply_event_defaults(dict(event))
            conn.execute(
                "INSERT INTO events (id, seq, at, actor, command, record_id,"
                " before_json, after_json, changed_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["id"],
                    event["seq"],
                    event["at"],
                    event["actor"],
                    event["command"],
                    event["record_id"],
                    None
                    if event["before"] is None
                    else json.dumps(event["before"], sort_keys=True),
                    None if event["after"] is None else json.dumps(event["after"], sort_keys=True),
                    None
                    if event["changed"] is None
                    else json.dumps(event["changed"], sort_keys=True),
                ),
            )

        # Milestone 6 (KYO-8): the three project-wide ledgers, loaded the same way
        # as `events` above — absent files (a pre-M6 export) load as empty lists,
        # not an error (schema.apply_export_defaults handles the per-record
        # `flags` key the same way; these three tables have no per-record
        # analogue, so there is nothing for that function to default here).
        for row in ledgers[ledgers_module.SWEEPS_FILENAME]:
            # KYO-63: a ledger line written before schema_version 10 carries no
            # `code_repo_id`/`coverage`/`coverage_detail`, and the defaults table (not a
            # `.get()` here) says what those absences mean: the run recorded neither
            # which code history it swept nor whether staleness was answered at all.
            row = apply_sweep_run_defaults(row)
            conn.execute(
                "INSERT INTO sweep_runs (id, seq, at, actor, base_sha, code_repo_id,"
                " coverage, coverage_detail, flags_created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["seq"],
                    row["at"],
                    row["actor"],
                    row["base_sha"],
                    row["code_repo_id"],
                    row["coverage"],
                    row["coverage_detail"],
                    row["flags_created"],
                ),
            )
        for row in ledgers[ledgers_module.BRIEFS_FILENAME]:
            # KYO-94: a ledger line written before schema_version 12 carries no
            # `session_start`, and the defaults table says what that absence means —
            # the run was never asked whether it opened a session, which is a third
            # value and not a "no". Nothing anchors a session on it.
            # KYO-398: and a line written before 24 carries no build — NULL in all
            # three, which the session-start notice renders as "cannot tell".
            row = apply_brief_run_defaults(row)
            conn.execute(
                "INSERT INTO brief_runs (id, seq, at, actor, last_event_seq,"
                " last_shift_note_seq, session_start, build_commit, build_commit_time,"
                " build_source, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["seq"],
                    row["at"],
                    row["actor"],
                    row["last_event_seq"],
                    row["last_shift_note_seq"],
                    row["session_start"],
                    row["build_commit"],
                    row["build_commit_time"],
                    row["build_source"],
                    row["session_id"],
                ),
            )
        for row in ledgers[ledgers_module.SHIFT_NOTES_FILENAME]:
            conn.execute(
                "INSERT INTO shift_notes (id, seq, text, author, created_at,"
                " redacted_at, redacted_why) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["seq"],
                    row["text"],
                    row["author"],
                    row["created_at"],
                    # KYO-25: a shift note exported before redaction existed carries
                    # no such key; the ledger rows are read as plain dicts rather than
                    # through apply_export_defaults, so the default lives here.
                    row.get("redacted_at"),
                    row.get("redacted_why"),
                ),
            )

        # KYO-414: each counter is seeded at max(snapshot, highest
        # loaded seq + 1). The snapshot still decides every scope it holds higher --
        # which is what keeps an id whose highest sibling was voided or deleted by hand
        # from being reissued -- but it can no longer LOWER a counter below an id the
        # export already carries. `_allocations.json` is a snapshot overwritten after
        # each command's commit, so two overlapping writers could leave it below the
        # ledger, and a rebuild that trusted it verbatim then failed every later write
        # with an IntegrityError on the reissued id. Taking the max only ever raises.
        seeded = {scope: int(value) for scope, value in allocations.items()}
        for scope, high, _what in loaded_allocation_highs(conn):
            seeded[scope] = max(seeded.get(scope, 0), high + 1)
        for scope, next_value in seeded.items():
            conn.execute(
                "INSERT INTO allocations (scope, next_value) VALUES (?, ?)", (scope, next_value)
            )

        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(documents)


__all__ = ["read_ledger_rows", "read_record_documents", "rebuild_from_export"]
