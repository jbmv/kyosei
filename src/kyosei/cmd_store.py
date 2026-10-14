"""``where`` / ``export`` / ``rebuild`` / ``doctor`` — store maintenance.

The commands whose subject is the store as an artifact rather than any record in
it: say where it is and whether this build can open it, regenerate the
git-canonical export, rebuild the database from that export, and check the
invariants that hold the two together.
"""

from __future__ import annotations

import contextlib
from argparse import Namespace
from pathlib import Path

from . import clock, currency, events_ledger, export, ledger_repair, ledgers
from . import config as config_module
from .commands import (
    Context,
    Output,
)
from .config import (
    DOCTOR_REPORT_NAME,
    KYOSEI_DIR,
    Layout,
    config_path_for,
    load_config,
)
from .errors import BrokenInvariants, KyoseiError, SchemaMismatch, UsageError
from .git_queries import _git_current_branch
from .rebuild import rebuild_from_export
from .schema import SCHEMA_VERSION

#: The one place :func:`cmd_where` says why it has no branch to report, so a reader
#: (and the desktop app's title bar) can tell "this project is not in git at all"
#: from "git is here and HEAD is detached" without parsing either sentence.
WHERE_NO_PROJECT = "no-project"
WHERE_NO_GIT = "no-git"
WHERE_DETACHED_OR_UNREADABLE = "no-branch"


def _current_branch(root: Path) -> tuple[str | None, str | None, str | None]:
    """The branch at ``root``, or why there is none — never a refusal.

    :func:`kyosei.git_queries._git_current_branch` is the one implementation of
    "what branch am I on" in this tool and stays it; what is added here is a caller
    that treats its refusal as an *answer*. That command raises for two different
    situations — ``root`` is not a git repository at all, and ``root`` is a
    repository whose HEAD is detached — and ``kyo check-branch``, which is a guard,
    is right to stop on both. This command is a locator: a project that is not in
    git is a project it must still be able to place (KYO-184.c4), so the raise is
    caught here and turned into ``branch: null`` with the reason beside it.

    Returns ``(branch, reason, detail)``: exactly one of ``branch`` and ``reason``
    is set, and ``detail`` carries git's own sentence when there is one.
    """
    try:
        return (_git_current_branch(root), None, None)
    except KyoseiError as exc:
        message = str(exc)
        # Told apart by which of the two refusals `_git_current_branch` raised, not
        # by re-running git: "does not look like a git repository" is its no-repo
        # sentence and "detached HEAD" is its other one. A third refusal it might
        # grow lands in the general bucket rather than being claimed as either.
        if "detached HEAD" in message:
            return (None, WHERE_DETACHED_OR_UNREADABLE, message)
        return (None, WHERE_NO_GIT, message)


def _refuse_a_write_from_a_worktree(ctx: Context, what: str) -> None:
    """KYO-342's write guard, for the two writers here that open no ``Store``.

    ``kyo rebuild`` and ``kyo export --refresh`` are the store's two file-level
    writers: one replaces ``kyosei.db``, the other rewrites every document in
    ``kyosei/export/``, and neither opens the transaction that carries the guard for
    everything else (:meth:`kyosei.store.Store.transaction`). From a linked worktree
    whose store resolved through to the main checkout, both would write that
    checkout's files.
    """
    config_module.refuse_store_write(
        None if ctx.layout is None else ctx.layout.store_via_main_checkout,
        ctx.code_root,
        what,
    )


def _scratchpad_path(layout: Layout) -> Path:
    """``kyo serve``'s scratchpad path for ``layout``, imported late (serve is not cheap)."""
    from .serve_files import scratchpad_path

    return scratchpad_path(layout)


def cmd_where(ctx: Context, args: Namespace) -> Output:
    """``kyo where`` — where is this project, and can this build open its store?

    KYO-184. The command exists because those two questions had no command of their
    own, so the desktop app answered them by borrowing two that were never about
    them: ``kyo check-branch --json`` was mined for ``layout``/``store_root``/
    ``branch`` with its exit code deliberately ignored (it means "this branch diffs
    the store", which is true and irrelevant to where the store is), and
    ``kyo list --json`` was run as a probe because opening the store is what makes a
    schema refusal arrive. Both worked. Both did real work nobody asked for —
    ``check-branch`` runs git and computes a three-dot diff, ``list`` reads and
    renders every record — on the path between a human opening a window and the
    window saying anything.

    Three properties, each load-bearing:

    * **It answers without an openable store.** ``PROJECT_OPTIONAL``, so it runs
      where there is no project at all and says ``layout: "unconfigured"`` rather
      than failing; and where there IS a project whose store this build cannot
      open, ``can_open: false`` is the *answer*, not a refusal. This command exits
      0 in every one of those cases on purpose: its subject is the question, so
      "no" is a question answered rather than a command that failed, and a consumer
      asking "is this directory a Kyosei project I can read" must be able to read
      the reply out of a clean exit. The refusal it would otherwise have raised is
      carried in ``store_problem`` verbatim.
    * **The mismatch payload is carried, never re-derived** (c2). On a schema
      mismatch this catches :class:`kyosei.errors.SchemaMismatch` and copies its own
      ``schema_mismatch`` payload — the one KYO-178 made a required constructor
      argument so it cannot be omitted — into this envelope under the same key. Not
      one field of it is recomputed here, so a consumer branching on ``direction``
      reads the same three ``schema_version`` numbers whichever command the refusal
      arrived out of.
    * **It answers outside git** (c4). ``branch`` is null with ``branch_problem``
      naming which of "not a git repository" and "detached HEAD" it was, rather
      than the whole command failing the way a guard is right to.

    Read-only in the strongest sense available: it opens the store (that is how the
    openability question is answered at all) and runs no transaction, writes no
    events row and regenerates no export — it is deliberately not in
    ``cli.MUTATING_COMMANDS``, which is where this project keeps that declaration
    and what ``kyo reference`` renders "Writes to the store: **no**" from.
    """
    layout: Layout | None = ctx.layout
    configured = layout is not None and layout.configured
    data: dict = {
        "ok": True,
        "command": "where",
        # The directory the question was asked about, resolved — so a caller that
        # passed a relative `--root`, or none at all, can see what was searched from.
        "cwd": str(ctx.root if layout is None else layout.code_root),
        "layout": layout.kind if layout is not None else None,
        "configured": configured,
        "store_root": None,
        "code_root": None,
        "store_link": None,
        "config_path": None,
        "db_path": None,
        "project_prefix": None,
        # KYO-342. Null in every ordinary invocation; the main checkout's path when
        # this store was reached through it out of a linked git worktree. A consumer
        # that renders a board has to be able to say which store answered, and from a
        # worktree the nearest `kyosei/` is not it.
        "store_via_main_checkout": None,
        "branch": None,
        "branch_problem": WHERE_NO_PROJECT,
        "branch_problem_detail": None,
        # This build's own number, always — the openability answer is a comparison
        # against it, and a consumer that renders the comparison should never have to
        # ask a second command which build it just talked to.
        "build_schema_version": SCHEMA_VERSION,
        # KYO-379. The machine's store home -- `$KYOSEI_HOME`, or `~/kyosei` -- which
        # holds the per-project homes and, beside them, the machine's project
        # registry (`projects.json`). Needs no project, so it is set in every answer,
        # including the unconfigured one. The desktop app reads the registry's
        # location from here rather than carrying a TypeScript copy of
        # `config.store_home()` (law 3).
        "store_home": str(config_module.store_home()),
        # KYO-572. The session scratchpad -- the file the desktop app's Scratchpad
        # shows -- resolved by `serve_files.scratchpad_path`, the derivation `kyo serve`
        # uses, never a second spelling of it. Null with no project to name it after.
        "scratchpad": None,
        "can_open": False,
        "store_problem": None,
    }

    if layout is None or not configured:
        described = layout.describe() if layout is not None else "store layout: unconfigured"
        data["store_problem"] = described
        return Output(
            [
                described,
                f"  looked from: {data['cwd']}",
                "  there is nothing here to open; `kyo init` creates a project",
                f"  store home: {data['store_home']}",
                f"  this build speaks schema_version {SCHEMA_VERSION}",
            ],
            data,
        )

    assert layout is not None
    branch, branch_problem, branch_detail = _current_branch(layout.code_root)
    data.update(
        {
            "store_root": str(layout.store_root),
            "code_root": str(layout.code_root),
            "store_link": str(layout.link_path) if layout.link_path is not None else None,
            "config_path": str(config_path_for(layout.store_root)),
            "store_via_main_checkout": (
                None
                if layout.store_via_main_checkout is None
                else str(layout.store_via_main_checkout)
            ),
            "branch": branch,
            "branch_problem": branch_problem,
            "branch_problem_detail": branch_detail,
            "scratchpad": str(_scratchpad_path(layout)),
        }
    )

    # `config.yml` before the database: it holds the project's prefix and the paths,
    # and it is readable in exactly the states the database is not (a fresh clone,
    # whose `kyosei.db` is gitignored and absent). A config this build cannot read
    # is itself an answer — `store_problem` says so below when `open_store` reaches
    # the same file and refuses.
    try:
        config = load_config(layout.store_root, code_root=layout.code_root)
    except KyoseiError:
        config = None
    if config is not None:
        data["project_prefix"] = config.project_prefix
        data["db_path"] = str(config.db_path)

    try:
        store = ctx.store()
    except SchemaMismatch as exc:
        # As stored (c2). `exc.data` is the payload the class built out of its own
        # required arguments; nothing here reads it apart and nothing rebuilds it.
        data.update(exc.data)
        data["store_problem"] = str(exc)
    except KyoseiError as exc:
        data["store_problem"] = str(exc)
    else:
        data["can_open"] = True
        data["project_prefix"] = store.prefix

    lines = [
        layout.describe(),
        f"  store root: {data['store_root']}",
        f"  code root:  {data['code_root']}",
    ]
    if data["store_link"] is not None:
        lines.append(f"  store link: {data['store_link']}")
    if data["store_via_main_checkout"] is not None:
        # KYO-342.c4. The `store root:` line above already prints a directory that is
        # not under `code root:`, which is the split layout's shape and would be read
        # as one; this says what actually happened and that it was a read-only
        # resolution, so a reader never has to infer either.
        lines.append(
            f"  reached through: the main checkout at {data['store_via_main_checkout']} "
            "— this is a linked git worktree, whose own kyosei/ carries no database; "
            "reads answer out of that store, writes are refused here"
        )
    lines.append(f"  store home: {data['store_home']}")
    lines.append(f"  scratchpad: {data['scratchpad']}")
    if data["project_prefix"] is not None:
        lines.append(f"  records:    {data['project_prefix']}-N")
    if branch is not None:
        lines.append(f"  branch:     {branch}")
    else:
        lines.append(f"  branch:     none ({branch_detail or branch_problem})")
    if data["can_open"]:
        lines.append(
            f"  this build (schema_version {SCHEMA_VERSION}) can open {data['db_path']}"
        )
    else:
        lines.append(
            f"  this build (schema_version {SCHEMA_VERSION}) CANNOT open this store: "
            f"{data['store_problem']}"
        )
    return Output(lines, data)


def cmd_export(ctx: Context, args: Namespace) -> Output:
    """``kyo export --refresh [--force]`` — rewrite every drifted export document (KYO-6),
    and re-append every ledger row the database holds and the export lacks (KYO-404).

    The second half is what makes "regenerate kyosei/export/ from the database" true of
    the whole export rather than of the documents alone: a row the ledgers are missing
    (an interruption after COMMIT and before the append) is otherwise dropped by the
    next ``kyo rebuild``, and the currency remedy that sends people here promises that
    nothing is lost. ``ledger_repair.repair_ledgers`` appends nothing on a complete store.

    Refusing a bare ``kyo export`` (no ``--refresh``) rather than treating it as a
    silent no-op keeps "success" honest (law 6): there is nothing else for `export` to
    do yet, so running it without saying what you want done is a usage error, not a
    quiet pass.

    ``--force`` is KYO-81. This is the one writer to ``kyosei/export/`` that does not
    pass through ``Store.transaction()`` — it allocates nothing, so it never opens one
    — and therefore the one writer KYO-75's guard cannot see. What it does is exactly
    what that guard exists to prevent: ``refresh_exports`` rewrites every record
    document from the database and ``write_allocations`` overwrites
    ``_allocations.json`` with the database's counters, so over a database whose
    export is *ahead* of it (an ordinary state for a second checkout after a pull)
    it replaces pulled record documents with older text and lowers a
    git-canonical counter, after which the next allocation hands out an id the export
    has already issued (law 4).

    Guarded here rather than inside ``export.refresh_exports`` /
    ``ledgers.write_allocations``, which every mutating command calls *post-commit*: a
    refusal raised there would fire after the transaction guard had already passed and
    the commit had already happened, leaving the two sides further apart than it found
    them. This is the only command that reaches them with no transaction in front, so
    this is where the check goes.

    Consent is a flag, and the same flag ``kyo rebuild --force`` takes (KYO-81.c2).
    The two are mirror images — each overwrites one side of this seam with the other —
    and each one's ``--force`` is required in exactly the case where running it
    destroys what the other side holds. A confirmation prompt was the alternative and
    is not available: this command is run by workers, by hooks, and by programs that
    call ``kyo`` on a viewer's behalf, none of which have a human at a terminal.
    """
    # KYO-342, ahead of both actions: `--migrate-events` moves bytes between files in
    # `kyosei/export/` and `--refresh` rewrites them, and from a linked worktree whose
    # store resolved through to the main checkout both write that checkout's export.
    _refuse_a_write_from_a_worktree(ctx, "write this store's kyosei/export/")
    if getattr(args, "migrate_events", False):
        return _migrate_events(ctx)
    if not args.refresh:
        raise UsageError(
            "kyo export needs a flag saying what to do; pass --refresh to rewrite every "
            "drifted export document from the database, or --migrate-events to rewrite "
            "a monolithic kyosei/export/_events.jsonl as bounded shards (KYO-268)"
        )
    store = ctx.store()
    seam = currency.db_export_currency(store)
    # `would_reissue_an_id`, the write predicate, and not `db_is_behind`, the read one
    # — KYO-80 is the record about a command asking the wrong one of the two. An export
    # document with no row behind it over agreeing counters sets `db_is_behind`, and a
    # refresh neither deletes it (`refresh_exports` deliberately does not delete files
    # with no record) nor lowers any counter: refusing there would be a refusal over
    # damage that does not occur. A counter the export is ahead on is the state in
    # which this command destroys something.
    if seam.would_reissue_an_id and not args.force:
        raise KyoseiError(
            f"refusing to refresh kyosei/export/ from the database: {seam.sentence()}. "
            "`kyo export --refresh` rewrites every record document in kyosei/export/ "
            "from the database and overwrites kyosei/export/_allocations.json with the "
            "database's counters — which here overwrites the export with an older "
            "database and lowers a git-canonical counter, after which the next "
            "allocation hands out an id the export has already issued (law 4). To make "
            f"the database current instead, {seam.remedy()}. If you have decided the "
            "database is the side to keep and mean to overwrite kyosei/export/ with it, "
            "pass --force"
        )
    forced = bool(args.force and seam.would_reissue_an_id)
    lowered = list(seam.export_ahead_scopes) if forced else []
    # KYO-404: the ledgers first, for the reason `_finish_mutation` appends first — they
    # are the history a rebuild reads, and the documents below are re-derivable.
    appended = ledger_repair.repair_ledgers(store)
    drifted = export.refresh_exports(store)
    ledgers.write_allocations(store)
    total = len(store.all_record_ids())
    lines = [f"refreshed {total} export document(s); {len(drifted)} were drifted"]
    lines += [f"  fixed: {rid}" for rid in drifted]
    if not drifted:
        lines.append("  (nothing was drifted)")
    appended_total = sum(appended.values())
    lines.append(
        f"re-appended {appended_total} ledger row(s) the database held and "
        "kyosei/export/ did not"
    )
    lines += [
        f"  {table}: {count}" for table, count in appended.items() if count
    ]
    # Law 6: --force did the thing the unforced command refuses to do, so it says so
    # rather than reporting the same clean success as a refresh that destroyed nothing.
    if forced:
        lines.append(
            f"  --force: overwrote kyosei/export/ from the database, though {seam.sentence()}"
        )
        lines.append(f"    counter(s) lowered to the database's value: {', '.join(lowered)}")
    return Output(
        lines,
        {
            "ok": True,
            "command": "export",
            "refreshed": total,
            "drifted": drifted,
            "ledger_rows_appended": appended,
            "forced_over_export_ahead": forced,
            "counters_lowered": lowered,
            "db_export_currency": currency.as_data(seam),
        },
    )


def _migrate_events(ctx: Context) -> Output:
    """``kyo export --migrate-events`` — the monolithic events ledger becomes shards.

    KYO-268.c3. **Why this and not `kyo rebuild`**, which the criterion names first.
    ``rebuild`` reads ``kyosei/export/`` and writes ``kyosei.db``; its docstring says
    three times over that it changes not one byte of the export, and two of its own
    behaviours are built on that — ``legacy_absolute_paths`` and
    ``stale_export_documents`` both REPORT a thing they could repair, precisely because
    "removing what is already committed to ``kyosei/export/`` is ... the maintainer's
    call, not a rebuild's" (KYO-52). Deleting a git-canonical file from inside the one
    command every consumer runs after a pull, and runs again with ``--force`` when it
    fails, would break that contract at the worst possible moment. `kyo export` is
    already the command whose job is rewriting export artifacts, so the migration goes
    where the writes are.

    Writes no database and opens no store — ``ctx.store()`` is deliberately not called.
    The migration moves bytes between files in ``kyosei/export/`` and changes none of
    them, so there is nothing for a db to be consistent with and nothing for the
    db/export seam guard (KYO-81) to protect: an export that is ahead of its database
    is still exactly as far ahead afterwards.
    """
    export_directory = load_config(ctx.root).export_path
    if not export_directory.is_dir():
        raise KyoseiError(f"{export_directory} does not exist; there is no ledger to migrate")
    monolithic = events_ledger.events_path_in(export_directory)
    result = events_ledger.migrate_events_to_shards(export_directory)
    if not result["migrated"]:
        return Output(
            [
                f"{monolithic} is not there — this export's events ledger is already "
                f"sharded ({result['events']} event(s) in {len(result['shards'])} shard(s))",
                "  nothing to do",
            ],
            {"ok": True, "command": "export", "migrated": False, **result},
        )
    return Output(
        [
            f"migrated {result['events']} event(s) into {len(result['shards'])} shard(s) "
            f"under {events_ledger.events_dir_in(export_directory)}",
            f"  removed {monolithic}",
            f"  shard limit: {events_ledger.EVENTS_SHARD_MAX_BYTES} bytes; "
            f"{result['bytes']} bytes written",
            "",
            "Every line was moved verbatim — same bytes, same schema_version stamp — so "
            "a rebuild reads exactly what it read before. What changed is which file "
            "each line is in, and therefore how much of the ledger a commit re-stores.",
            "",
            "This fixed the working tree, not history: every commit that already carries "
            f"{monolithic.name} still does, and reclaiming that space is a git history "
            "rewrite — see docs/reference/topics/store.md (in Kyosei's own repository), "
            "\"Where the store lives\".",
        ],
        {"ok": True, "command": "export", **result},
    )


def cmd_rebuild(ctx: Context, args: Namespace) -> Output:
    """Reconstruct ``kyosei.db`` from ``kyosei/export/`` alone (charter, Layer 2).

    Deliberately does not go through ``ctx.store()``: that path requires an existing
    database, and rebuild's entire purpose is to run when one is missing (or, with
    ``--force``, to replace one).

    Which is also why KYO-342's guard is called here by name. Every other writer
    reaches the store through ``Store.transaction()`` and inherits that guard; this
    one opens no store, and from a linked worktree whose reads resolve through to the
    main checkout it would rebuild *that* checkout's database — the live store of the
    tree a worker was sent away from, from a command whose whole purpose is to
    replace one.
    """
    _refuse_a_write_from_a_worktree(ctx, "rebuild this store's database")
    config = load_config(ctx.root)
    legacy_absolute_paths: list[str] = []
    config_comments_dropped: list[str] = []
    stale_export_documents: list[str] = []
    count = rebuild_from_export(
        config.export_path,
        config,
        force=args.force,
        legacy_absolute_paths=legacy_absolute_paths,
        config_comments_dropped=config_comments_dropped,
        stale_export_documents=stale_export_documents,
    )
    lines = [
        f"rebuilt {config.db_path} from {config.export_path}",
        f"loaded {count} record(s)",
    ]
    # KYO-52: stated, never fixed. These were written before schema_version 9, when
    # path-typed columns held resolved absolute paths; they carry a username and a
    # directory layout, and removing them from a git-canonical export is a history
    # rewrite — the maintainer's decision. A rebuild that stayed silent about them
    # would be reporting a clean store it does not have (law 6).
    if legacy_absolute_paths:
        lines.append(
            f"  note: {len(legacy_absolute_paths)} absolute path(s) loaded verbatim from "
            "export documents written before schema_version 9 (KYO-52). Nothing was "
            "changed: they are already committed, and removing them is a git history "
            "rewrite. New paths are stored relative to the project root. "
            "`kyo rebuild --json` lists each one under `legacy_absolute_paths`."
        )
        # KYO-423.c4: the count stays (law 6); the per-path lines do not. Nothing can
        # change these paths, so relisting every one on every rebuild was reading cost
        # with no decision behind it, and `--json` already carries the whole list.
    # KYO-158: `config.yml` is rewritten on every rebuild to keep `schema_version`
    # honest, and it is git-tracked. A rewrite that dropped a project's own comments
    # in silence is the same failure as the key deletion this record was filed for:
    # the reader sees a diff nothing accounts for, or does not see it and commits it.
    # Normally nothing is dropped and nothing is said (KYO-33).
    if config_comments_dropped:
        lines.append(
            f"  note: {config.config_path} was re-rendered from its values rather than "
            f"edited in place, and {len(config_comments_dropped)} line(s) carrying "
            "comments were not reproduced. The keys and their generated comments are "
            "intact; the text below is not, and is listed so it can be restored by "
            "hand (KYO-158)."
        )
        lines += [f"    {entry}" for entry in config_comments_dropped]
    # KYO-216: a rebuild reads every export document exactly as it was written and
    # writes THIS build's SCHEMA_VERSION into the database it just created — so the
    # moment a schema bump changed what this build writes, kyosei/export/ is behind
    # the store it was just used to reconstruct, and doctor's `exports` /
    # `export_roundtrip` checks report every one of those documents on the very next
    # run. This is the pointer that was missing: the open-time refusal and the
    # README both name `kyo rebuild --force`, and nothing said what to run next.
    if stale_export_documents:
        lines.append(
            f"kyosei/export/ holds {len(stale_export_documents)} document(s) stamped "
            f"behind this database's schema_version {SCHEMA_VERSION}; run `kyo export "
            "--refresh` to rewrite them and clear doctor's drift findings."
        )
    # KYO-335.c1. This is the one command that both rewrites `kyosei/config.yml` and
    # stamps THIS build's `schema_version` into a store — so it is the one command
    # that can, in a single run, put a project's store beyond the reach of the `kyo`
    # every one of its hooks runs. It says so and proceeds: the rebuild is what the
    # caller asked for and it is correct; the condition is about the OTHER build.
    # Asked after the write rather than before it, because what matters is what that
    # build makes of the store as it now stands, not as it stood.
    path_build = (
        config_module.probe_path_build(config.root)
        if config_module.project_runs_kyo_from_path(ctx.code_root)
        else None
    )
    if path_build is not None and path_build.blind:
        lines.append(
            "  note: the rebuild is done, and the `kyo` on PATH — the build every hook "
            "in this project runs — cannot open what it wrote:"
        )
        lines += [
            f"    {line}"
            for line in config_module.path_build_lag_lines(
                path_build, subject="the store this rebuild just wrote"
            )
        ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "rebuild",
            "db": str(config.db_path),
            "export": str(config.export_path),
            "records": count,
            "legacy_absolute_paths": legacy_absolute_paths,
            "config_comments_dropped": config_comments_dropped,
            "stale_export_documents": stale_export_documents,
        },
    )


#: KYO-362: the key ``cmd_doctor --hook`` puts its single stderr line under, in the
#: command's own ``--json`` envelope, and the key ``cli.main`` reads back out of the
#: failure's ``.data`` to print INSTEAD of the failure's whole report.
#:
#: Carried on the envelope rather than on a new exception class, and that is a
#: decision rather than a shortcut. ``errors.DoctorFailure`` renders its message from
#: the findings it was handed, so a one-line variant needs either a field on that
#: class or a subclass — and a subclass is not free here: ``ERROR_CLASS_NAMES`` in
#: ``tests/test_kyo5_error_remedies.py`` is matched by name against the AST, so every
#: new ``KyoseiError`` subclass has to be registered there or the remedy-coverage walk
#: silently shrinks. The envelope already exists, already reaches ``--json``, and
#: already travels on the failure (``BrokenInvariants.data``); and ``cli.main``
#: already has exactly one precedent for a hook-shaped invocation replacing the line
#: it prints — KYO-335's ``cmd_check_build.hook_store_unreadable_line``, in the same
#: ``except`` block, four lines away.
DOCTOR_HOOK_LINE_KEY = "doctor_hook_line"

#: The ``--json`` key naming the file ``--hook`` wrote, or ``None`` when this run
#: wrote none (it passed, or the write failed). A consumer reading the envelope should
#: not have to re-derive the path from the store root and a filename constant (law 3).
DOCTOR_REPORT_PATH_KEY = "doctor_report_path"

#: KYO-408: the ``--hook --json`` key saying how each check with a hook form
#: (``doctor.HOOK_FORMS``) was satisfied on this run — e.g.
#: ``{"export_roundtrip": {"mode": "appends-verified"}}``, or ``"full"`` with the reason
#: the cheaper tiers could not be used.
DOCTOR_HOOK_FORMS_KEY = "hook_forms"


def doctor_report_path(ctx: Context) -> Path:
    """Where ``kyo doctor --hook`` leaves its full report: beside ``kyosei.db``.

    Beside the database rather than in ``kyosei/export/`` because it is derived local
    state with the same life as ``sessions.json`` — written by the run that fails,
    deleted by the run that passes, and gitignored by the line
    :func:`kyosei.scaffold.store_gitignore_text` declares for it. Under the split
    layout that is the STORE's ``kyosei/``, not the code repository's, which is why
    this is derived from the context rather than from a cwd.
    """
    return ctx.root / KYOSEI_DIR / DOCTOR_REPORT_NAME


def doctor_hook_line(failing: list[str], report: Path | None) -> str:
    """The one line ``--hook`` prints to stderr. One line, and a pointer (KYO-362).

    Everything in it is what a reader needs to decide whether to stop and look: how
    many problems, which checks they came from, and where the detail is. It names
    `kyo doctor` too, because the file is the wrong place to read from mid-session and
    re-running the checks is one command — and because a path with no command beside
    it asks the reader to work out what opens it.

    What is deliberately NOT in it: the findings themselves. The whole defect is that
    a check formatting a store-sized object into its message turns a hook error into
    a screenful, and a hook line that quoted even the first finding would be one
    long check away from the same failure. Notices are out for the same reason and a
    second one: they exit 0 on their own, so they are never why this line is printing.
    """
    where = (
        f"Full report: {report}"
        if report is not None
        else "The full report could not be written"
    )
    return (
        f"kyo doctor: {len(failing)} problem(s) from {', '.join(failing)}. "
        f"{where} — or run `kyo doctor` to read the findings here."
    )


def _write_doctor_report(path: Path, problems: list[str], notices: list[str]) -> Path | None:
    """Write the full report, or answer ``None`` — a hook must never die on a write.

    The report is the findings, in full, in the same strings the failure and the
    ``--json`` envelope carry (law 3: ``doctor.sort_into_tiers`` is the one place they
    are formatted). Notices are in the file even though they are out of the line: the
    file is where detail lives, and a reader who opens it wants everything the run
    found.
    """
    lines = [
        f"kyo doctor — {clock.now()}",
        "",
        f"problems ({len(problems)}) — these are why the hook failed:",
        *(f"  - {problem}" for problem in problems),
    ]
    if notices:
        lines += [
            "",
            f"notices ({len(notices)}) — reported, not failures; `kyo doctor` exits 0 "
            "with these outstanding:",
            *(f"  - {notice}" for notice in notices),
        ]
    lines += [
        "",
        "This file is rewritten by every failing `kyo doctor --hook` and deleted by "
        "the first one that passes.",
        "`kyo doctor` re-runs the same checks and prints the same findings in the "
        "terminal.",
        "",
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return None
    return path


def _clear_doctor_report(path: Path) -> None:
    """Remove a report an earlier failing run left. Never raises: a clean store must
    not be turned into a failing hook by a file it could not delete."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def cmd_doctor(ctx: Context, args: Namespace) -> Output:
    """KYO-214: two tiers, and the exit code answers only to the first one.

    ``problems`` fail the command with exit 3, as every doctor finding used to.
    ``notices`` are findings a reader may need and cannot act on now (see
    ``doctor.NOTICE_CHECKS`` for the bar): they are printed in full, under their own
    heading, and exit 0. Both lists reach ``--json`` separately, and ``checks`` still
    carries every check's own findings with the tier it declared, so a consumer never
    has to re-derive which list a line came from.

    The one asymmetry, stated rather than hidden: on the failing path the human-readable
    message is the failure's, and ``errors.DoctorFailure`` renders the problems it was
    given — so a notice standing beside a problem reaches a text-mode reader through
    ``--json`` and on the next clean run, not in that error block. Carrying it in the
    failure's own text needs a field on that class, which is outside this dispatch's
    declared touches; KYO-214's report recommends it.

    KYO-362 adds ``--hook``, which changes what is PRINTED. The same checks run in the
    same order, and the exit status is the one bare `kyo doctor` would have given over
    the same export. What it changes is that the report goes to a file beside
    ``kyosei.db`` and one line naming it goes to stderr — because the hook this runs
    from is read in a session transcript, where a failing `kyo doctor` on a
    consumer-sized store filled the whole terminal on every stop.

    KYO-408 adds the one thing ``--hook`` changes about the checks themselves:
    ``export_roundtrip`` runs in its hook form (``doctor.HOOK_FORMS``), which rebuilds
    only what changed since the last passing round-trip instead of the whole store on
    every stop, and ``hook_forms`` in ``--json`` says how each such check was satisfied.
    Bare `kyo doctor` always runs the whole rebuild.
    """
    from . import doctor as doctor_module
    from . import doctor_dispatch

    store = ctx.store()
    hook = bool(getattr(args, "hook", False))
    # KYO-408: the hook form runs every check, but satisfies `export_roundtrip` from what
    # the last passing run verified where it can (doctor_exports.check_export_roundtrip_hook) —
    # and says how, under `hook_forms`, so a pass is never silently a cached one.
    hook_forms: dict = {}
    results = doctor_module.run_checks(store, hook=hook, detail=hook_forms)
    # KYO-564: the verdict `kyo onboard --check` reads back without opening the database,
    # recorded by both forms and on both the passing and the failing path.
    doctor_module.record_last_result(store, results, hook=hook)
    # KYO-373.c3: one-line counts beside a check's own line, never findings.
    censuses = doctor_dispatch.census_lines(store)
    tiers = doctor_module.sort_into_tiers(results)
    problems = tiers[doctor_module.TIER_PROBLEM]
    notices = tiers[doctor_module.TIER_NOTICE]
    data = {
        "ok": not problems,
        "command": "doctor",
        "root": str(ctx.root),
        "project_prefix": store.prefix,
        "records": len(store.all_record_ids()),
        "problems": problems,
        "notices": notices,
        # Per check: `ok` is "this check found nothing", which is NOT the top-level
        # `ok` above ("nothing that fails the command") — a notice-tier check with
        # findings is `ok: false` inside a run that exits 0, and `tier` is what says so.
        "checks": {
            name: {
                "ok": not msgs,
                "tier": doctor_module.tier_of(name),
                "failures": msgs,
                **({"census": censuses[name]} if name in censuses else {}),
            }
            for name, msgs in results.items()
        },
    }
    if hook:
        data[DOCTOR_HOOK_FORMS_KEY] = hook_forms
        # KYO-362. Both branches run, in both directions: a store that has started
        # passing must not keep pointing a reader at a report of findings that are
        # gone, so the clean path DELETES rather than merely not writing. That is the
        # same self-healing rule KYO-31 wrote into this hook's ordering — a file the
        # tool wrote and never removes is a stale remedy that nothing clears.
        report = doctor_report_path(ctx)
        written: Path | None = None
        if problems:
            written = _write_doctor_report(report, problems, notices)
            failing = [
                name
                for name, msgs in results.items()
                if msgs and doctor_module.tier_of(name) == doctor_module.TIER_PROBLEM
            ]
            data[DOCTOR_HOOK_LINE_KEY] = doctor_hook_line(failing, written)
        else:
            _clear_doctor_report(report)
        data[DOCTOR_REPORT_PATH_KEY] = str(written) if written is not None else None
    if problems:
        # `data=` so `--json` still reports which checks failed. KYO-187: required,
        # not assigned above the raise — an assignment can be forgotten with nothing
        # failing, and `raise error` is invisible to the remedy-coverage walk.
        raise BrokenInvariants(problems, data=data)
    if hook:
        # Silent on the passing path: this is the shape every hook-driven command in
        # this package takes (`kyo check-push`'s KYO-33 lesson, restated on
        # `cmd_check_command.cmd_check_command`), and a stop that printed a summary of a run
        # that found nothing would be noise on every single stop. Notices are not
        # suppressed so much as left where they already reach a reader — `--json`, and
        # the next bare `kyo doctor`.
        return Output([], data)
    lines = [
        f"doctor: {len(results)} checks over {data['records']} record(s) — "
        f"problems ({len(problems)}), notices ({len(notices)})"
    ]
    lines += [
        f"  {'ok  ' if not msgs else 'note'}  {name}"
        + (f" — {censuses[name]}" if name in censuses else "")
        for name, msgs in results.items()
    ]
    if notices:
        lines.append(
            f"notices ({len(notices)}) — reported, not failures; `kyo doctor` exits 0 "
            "with these outstanding:"
        )
        lines += [f"  - {notice}" for notice in notices]
    return Output(lines, data)
