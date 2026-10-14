"""``show`` / ``list`` / ``base`` / ``map`` / ``ideas`` / ``docs`` — reads.

Commands that display what is stored and change nothing. Rendering itself lives
in ``render.py``; what is here is the shape of each view's ``--json`` document.

KYO-108 moved *the query* one module out, to ``query.py``: ``kyo list`` and every
saved Base (``bases.py``) are two renderings of one engine, which is what stops a
saved view from becoming a second query path that drifts from the listing.
"""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from . import (
    bases,
    commands,
    currency,
    docs_check,
    docs_history,
    docs_numbered,
    docs_obligations,
    docs_parse,
    docs_regions,
    docs_staleness,
    docs_suggest,
    docs_targets,
    explanations,
    export,
    git_queries,
    ids,
    kinds,
    prior_art,
    query,
    stop_audit,
    worker_tokens,
)
from . import ideas as ideas_module
from .clock import TIMESTAMP_FORMAT, elapsed_days, now
from .commands import (
    Context,
    Output,
    _load_registry,
    _load_status_ladder,
    _reason,
    _touch_record,
)
from .config import KYOSEI_DIR
from .contracts import (
    CONTRACT_DOCS_OBLIGATION_ALREADY_SETTLED,
    CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED,
    OBLIGATION_NOT_AFFECTED,
    OBLIGATION_UPDATED,
    OBLIGATION_VERDICTS,
    is_new_file_touch,
    standing,
    touch_target,
)
from .errors import (
    BrokenInvariants,
    ContractViolation,
    GuardRefusal,
    KyoseiError,
    NotFound,
    UsageError,
)
from .prose import single_line
from .render import (
    criterion_text,
    echo_block,
    echo_value,
    evidence_text,
    flag_text,
    map_text,
    report_standings,
    result_value,
    shift_note_text,
    show_text,
)
from .store import Store


def cmd_ideas(ctx: Context, args: Namespace) -> Output:
    if args.action == "review":
        return _cmd_ideas_review(ctx, args)
    if args.action == "migrate-stamps":
        return _cmd_ideas_migrate_stamps(ctx, args)
    # pragma: no cover - argparse's choices=[...] already refuses anything else.
    raise UsageError(f"unknown ideas action {args.action!r}")


def _document_rows(code_root: Path, documents_in: docs_parse.DocumentSet) -> list[dict]:
    """Every document in ``documents_in``, as a row carrying its sections (KYO-139).

    One memoised date lookup for the whole tree, shared with the staleness of every
    row: the same governed path is named by several documents, and a git subprocess per
    naming would make dating the tree the expensive half of reading it.
    """
    commit_at = docs_staleness.memoised_commit_at()
    return [
        {
            **docs_staleness.document_row(code_root, document, commit_at=commit_at),
            "sections": docs_staleness.section_rows(document),
        }
        for document in docs_targets.documents(code_root, documents_in=documents_in)
    ]


def cmd_docs(ctx: Context, args: Namespace) -> Output:
    """``kyo docs check`` — KYO-13.c3, Layer 3's skeleton validator: every ``governs:``
    target in one of this project's documents must exist. Read-only; no store is opened.

    WHICH files those are is the project's declaration since KYO-129 (``documents.yml``,
    defaulting to root-level ``*.md`` plus ``docs/**/*.md``), and this command prints it
    in every outcome. Before that, discovery was ``docs/`` and only ``docs/``, so a
    project keeping its operating documents at the repository root got exit 0 and one
    word — "clean" — over documents this validator had never opened. The paths line is
    what makes the difference visible: a reader can now tell a clean bill of health from
    a scan that found nothing to examine, which is what the third state below is for.

    Reading ``documents.yml`` is the one thing here that touches the store directory —
    the registries live beside the records, the code they describe does not, and
    ``ctx.root``/``ctx.code_root`` are those two places. Still no store, no database.
    """
    # KYO-137. `check` opens no store: the two below read and write rows, so the docstring
    # above holds for `check` and says so by naming it.
    # KYO-199.c6: `preview` is the second store-free action, and for it that is a
    # requirement rather than a property — see :func:`_cmd_docs_preview`.
    if args.action == "obligations":
        return _cmd_docs_obligations(ctx, args)
    if args.action == "settle":
        return _cmd_docs_settle(ctx, args)
    if args.action == "preview":
        return _cmd_docs_preview(ctx, args)
    # KYO-299: the fourth action, and the only one that writes to the CODE tree rather
    # than to the store. It is the controller's act on the merged tree — see
    # :func:`_cmd_docs_assemble`.
    if args.action == "assemble":
        return _cmd_docs_assemble(ctx, args)
    # KYO-303: the fifth action, and the second (with `check`) that neither opens a
    # store nor writes one. It drafts declarations and never applies them — see
    # :func:`_cmd_docs_suggest`.
    if args.action == "suggest":
        return _cmd_docs_suggest(ctx, args)
    # KYO-502: the sixth action. Store-free like `check`; it writes generated files in
    # the CODE tree, or with --check writes nothing — see :func:`_cmd_docs_index`.
    if args.action == "index":
        return _cmd_docs_index(ctx, args)
    if args.action != "check":
        # pragma: no cover - argparse's choices=[...] already refuses anything else.
        raise UsageError(f"unknown docs action {args.action!r}")

    documents_in = docs_parse.document_set(ctx.root / KYOSEI_DIR)
    result = docs_check.check_docs(ctx.code_root, documents_in=documents_in)
    data = {
        # `ok` keeps meaning exactly what it meant — did the validator find a broken
        # claim — because a project with no documents has not failed anything. What it
        # never again means on its own is "checked and clean": `checked` is the field
        # that answers that, and a consumer reading only `ok` is told by `state` which
        # of the two clean-looking outcomes it got.
        "ok": result.state != docs_check.DOCS_PROBLEMS,
        "checked": result.answered,
        "state": result.state,
        "says": result.says,
        "command": "docs check",
        "root": str(ctx.root),
        "document_paths": list(result.document_set.globs),
        "document_paths_declared": result.document_set.declared,
        "scanned": list(result.scanned),
        "problems": [
            {
                "doc_path": p.doc_path,
                "section": p.section,
                "target": p.target,
                "reason": p.reason,
            }
            for p in result.problems
        ],
        # KYO-499: the section-status refusals, each naming its rule and its remedy.
        "section_problems": [p.as_json() for p in result.section_problems],
        # KYO-351.c2: valid declarations that do less than they look like they do.
        # Separate from `problems` and absent from `ok` on purpose — see
        # `docs_check.GovernsNotice`, and `cmd_store.cmd_doctor` for the two-tier shape
        # this follows.
        "notices": [
            {
                "doc_path": n.doc_path,
                "section": n.section,
                "target": n.target,
                "says": n.says,
            }
            for n in result.notices
        ],
        "region_suffixes": list(docs_regions.REGION_SUFFIXES),
        # KYO-139: the documents themselves, each as the docs query source's row plus
        # its sections.
        #
        # WHY HERE, on a command whose job is a pass/fail verdict. The KYO-109 query
        # source is reachable only through a Base a project has declared with `source:
        # docs`, and a reader — the desktop app's docs mode, and anything else wanting
        # the governance layer rather than the build's verdict on it — cannot require
        # that a project happen to have declared one, and many projects have not. So the
        # rows are put on the command every project can already run.
        #
        # NOT a second enumerator and not a second answer: `docs_staleness.document_row`
        # is the same function `query._docs_rows` builds its rows from, over the same
        # `document_set` this check just scanned, so the verdict above and the rows
        # below cannot disagree about which files are documents or about whether one
        # is stale. `staleness` is carried in all four of its states, `cannot-tell`
        # included — a consumer that collapsed it into `current` would report a clean
        # docs tree on no evidence, which is what `defaults/bases.yml` says this field
        # exists to prevent.
        "documents": _document_rows(ctx.code_root, documents_in),
        # KYO-204.c2: the dead-section skip count as a typed field, beside the text
        # already embedded in `says` — the patch KYO-202.r1 handed over.
        "dead_sections_skipped": result.dead_sections_skipped,
    }
    if result.state == docs_check.DOCS_PROBLEMS:
        failures = [
            f"{p.doc_path} [{p.section}] governs {p.target!r}: {p.reason}"
            for p in result.problems
        ]
        # KYO-200.c5: the accepted shapes, stated once at the end of the list rather
        # than once per unresolved target — 154 times on the consumer file that
        # motivated it. This is the only text the failure path prints, so the
        # sentence has to be here and not only in `result.says`.
        if result.problems:
            failures.append(docs_check.GOVERNS_FORMAT)
        # KYO-499: each section-status refusal carries its rule's name and its remedy.
        failures += [p.line() for p in result.section_problems]
        failures.append(_dead_sections_line(result))
        # KYO-187: see `cmd_store.cmd_doctor`'s sibling refusal — the payload is a
        # required keyword argument so it cannot be left off, and the raise stays a
        # call the remedy-coverage walk can see.
        raise BrokenInvariants(failures, data=data)
    if result.state == docs_check.DOCS_NO_DOCUMENTS:
        # Exit 0, loudly. Not a build failure: "this project holds no Layer 3 documents"
        # is a legitimate state, and failing every consumer that is in it on the day they
        # upgrade would be this validator asserting a defect it has not observed — the
        # mirror of the silence KYO-129 was filed against, not a fix for it.
        return Output([f"docs check: no documents — {result.says}"], data)
    return Output(
        [
            f"docs check: {len(result.scanned)} document(s), 0 problem(s) — clean",
            f"  scanned: {result.document_set.says}",
            *(
                f"  section status: {chosen.says}"
                for chosen in result.document_set.section_status
            ),
            f"  {_dead_sections_line(result)}",
            *_narrowing_notice_lines(result),
        ],
        data,
    )


def _cmd_docs_index(ctx: Context, args: Namespace) -> Output:
    """``kyo docs index [--check]`` — KYO-502: the generated section-status indexes.

    Renders one file per ``section_status.index`` path declared in documents.yml
    (:mod:`kyosei.docs_index`), relative to the code root. ``--check`` writes nothing and
    raises when any committed copy differs, naming the command that clears it — the shape
    of ``kyo reference`` and its committed-file test (KYO-138). A project declaring no
    index is told so and exits 0: there is nothing to be stale.
    """
    from . import docs_index
    from .docs_index import IndexStale

    documents_in = docs_parse.document_set(ctx.root / KYOSEI_DIR)
    files = docs_index.indexes(ctx.code_root, documents_in.section_status)
    check = bool(getattr(args, "check", False))
    data = {
        "ok": True,
        "command": "docs index",
        "check": check,
        "indexes": [{"path": item.path, "sets": list(item.sets)} for item in files],
        "stale": [],
    }
    if not files:
        return Output(
            [
                "docs index: no document set declares `section_status.index` in "
                f"{documents_in.registry_path}, so there is no index to write"
            ],
            data,
        )
    stale = docs_index.stale(ctx.code_root, files)
    data["stale"] = [item.path for item in stale]
    if check:
        if stale:
            data["ok"] = False
            raise IndexStale(
                [
                    f"{item.path} differs from what `kyo docs index` renders now from the "
                    f"{', '.join(item.sets)} set's documents and src/ pointers — run "
                    "`kyo docs index` and commit the file; do not edit it by hand"
                    for item in stale
                ],
                data=data,
            )
        return Output(
            [f"docs index: {item.path} is current" for item in files],
            data,
        )
    for item in stale:
        destination = ctx.code_root / item.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(item.text, encoding="utf-8")
    return Output(
        [
            f"docs index: {'wrote' if item in stale else 'unchanged'} {item.path} "
            f"({', '.join(item.sets)})"
            for item in files
        ],
        data,
    )


def _narrowing_notice_lines(result: docs_check.DocsCheck) -> list[str]:
    """KYO-351.c2's text half: the ``#symbol``s that narrow nothing, and the exit code
    they do not move.

    Printed on the clean path only, which is the same asymmetry ``kyo doctor`` states
    for its own notices (KYO-214, ``cmd_store.cmd_doctor``): the failure path's text is
    the failure's, ``errors.BrokenInvariants`` renders the problems it was handed, and a
    notice standing beside a problem reaches a text-mode reader through ``--json`` and on
    the next clean run. Naming the count and the reason in one header rather than
    repeating the suffix rule per target: a consumer whose every governed file is
    TypeScript has one fact to learn, not 101 copies of it.
    """
    if not result.notices:
        return []
    return [
        f"  {len(result.notices)} governs: target(s) name a symbol the region "
        f"instrument cannot place — a file type it cannot read, or no declaration it "
        f"finds (KYO-358, KYO-397) — reported, not failures; `kyo docs check` exits 0 "
        f"and these still raise their section on any change to the files they name:",
        *(
            f"    {notice.doc_path} [{notice.section}] governs {notice.target!r}: "
            f"{notice.says}"
            for notice in result.notices
        ),
    ]


def _dead_sections_line(result: docs_check.DocsCheck) -> str:
    """KYO-204.c2's text half: the CLEAN and PROBLEMS branches above print their own
    lines rather than ``result.says``, so the skip count has to be said here too or a
    reader of the terminal never learns a section was skipped as dead."""
    return (
        f"{result.dead_sections_skipped} dead section(s) skipped (status historical or "
        "superseded, or a non-empty superseded-by): their governs: lists are history, "
        "not checked"
    )


#: KYO-199.c6: said on every preview run, passing or empty. The command's whole value is
#: that a worker can run it in the tree it is working in, and a worker is forbidden to
#: write to the store at all (`kyo check-branch`) — so "this wrote nothing" is not a
#: reassurance to infer from the absence of an error, it is the fact the reader needs.
PREVIEW_WRITES_NOTHING = (
    "this is a preview: nothing was written to the store, no obligation exists yet, and "
    "no obligation id can. The ids are minted when your orchestrator ingests your report "
    "(`kyo report`), which is also what raises these for real"
)


def _cmd_docs_preview(ctx: Context, args: Namespace) -> Output:
    """``kyo docs preview [--against main] [--branch HEAD]`` — KYO-199.c6.

    What ``kyo report`` would raise against this project's documents if this branch's
    diff were ingested right now, grouped per document exactly as ``kyo report`` and
    ``kyo docs obligations`` group it (c1). The reader is a **dispatched worker**, which
    settles three design questions at once:

    * **It opens no store, and cannot.** A worker's worktree has no ``kyosei.db`` — under
      the split layout the store is a different repository, and under the in-repo layout
      the database is gitignored, so ``git worktree add`` never checked one out. A
      preview that needed the store would be a command that only the controller could
      run, which is the reader who did not need it. It reads ``documents.yml`` and the
      documents themselves, and that is all.
    * **It therefore covers the ``governs:`` half of the join and says so.** The other
      half — a record's own ``doc-refs`` — is a stored declaration, and this cannot see
      it. Naming the gap is the whole of law 6 here: a worker who read "no documents
      implicated" without it would have read a guarantee this command cannot give.
    * **It mints no ids.** :data:`PREVIEW_WRITES_NOTHING` is printed on every outcome,
      including the empty one.

    The base is a flag rather than an assumption because a worker's merge base is
    whatever its controller branched it from, and the default (``main``) is the same
    default ``kyo report``'s own join uses so the two answers line up by construction.
    """
    against = getattr(args, "against", None) or "main"
    branch = getattr(args, "branch", None) or "HEAD"
    documents_in = docs_parse.document_set(ctx.root / KYOSEI_DIR)
    try:
        changed = git_queries.branch_changed_files(ctx.code_root, against, branch)
    except KyoseiError as error:
        raise UsageError(
            f"{error}. `kyo docs preview` diffs <base>...<branch> in {ctx.code_root}; "
            f"name the base your worktree was branched from with --against <ref>, and "
            f"the branch with --branch <ref> if it is not the one checked out"
        ) from error
    found, suppressed = docs_obligations.obligations_and_suppressions_for_branch(
        ctx.code_root, against, branch, changed, documents_in=documents_in
    )
    groups = docs_obligations.group_obligations_per_document(
        [
            {
                "doc_path": obligation.doc_path,
                "section": obligation.section,
                "cause": obligation.cause,
                "detail": obligation.says,
                "targets": obligation.targets,
            }
            for obligation in found
        ]
    )
    lines = [
        f"docs preview: {len(found)} obligation(s) across {len(groups)} document(s) that "
        f"`kyo report` would raise for {against}...{branch} ({len(changed)} changed path(s))",
        f"  scanned: {documents_in.says}",
    ]
    if not found:
        lines.append(
            "  (none) — no changed path intersects a governs: target in this project's "
            "documents, and no changed region holds a pointer into a set with pointer "
            "obligations on"
        )
    lines += docs_obligations.grouped_obligation_lines(groups)
    lines.append(
        "  read each section above and propose a verdict per section in your report's "
        "`docs_verdicts[]` (doc_path, section, verdict, method, reason). Your orchestrator "
        "settles; you propose"
    )
    # KYO-352.c4: the other half of the answer, said on every run including the empty
    # one. What a `#symbol` suppressed is invisible by construction — it is an absence —
    # and a project that has just narrowed a hundred targets has no other way to audit
    # what stopped raising.
    # KYO-500.c2: a pointer's region quiets a pointer the way a #symbol quiets a target,
    # and a pointer no finder could place raises on the whole file. Both halves are said
    # on every run that has any, beside the #symbol line, because all three answer one
    # audit question: how precise was this answer?
    listing = bool(getattr(args, "suppressed", False))
    # KYO-530: a section a comment-only change quieted is its own bucket, whatever its
    # cause, so it is never counted as a #symbol's or a region's doing.
    by_comment = [row for row in suppressed if row.comment_only]
    by_symbol = [
        row
        for row in suppressed
        if row.cause != docs_obligations.OBLIGATION_POINTER and not row.comment_only
    ]
    by_region = [
        row
        for row in suppressed
        if row.cause == docs_obligations.OBLIGATION_POINTER and not row.comment_only
    ]
    coarse = docs_obligations.coarse_pointer_lines(found)
    hint = " (--suppressed lists them; `--json` always carries them)"
    lines.append(
        f"  suppressed by a declared #symbol: {len(by_symbol)} section(s) that a "
        f"whole-file target would have raised"
        + (hint if by_symbol and not listing else "")
    )
    if by_symbol and listing:
        lines += docs_obligations.suppression_lines(by_symbol)
    if by_region:
        lines.append(
            f"  quieted by a pointer's region: {len(by_region)} section(s) a pointer in a "
            f"changed file names, whose region this branch did not change"
            + (hint if not listing else "")
        )
        if listing:
            lines += docs_obligations.suppression_lines(by_region)
    if by_comment:
        lines.append(
            f"  quieted as comment-only: {len(by_comment)} section(s) whose changed files "
            f"this branch changed only in comments and docstrings"
            + (hint if not listing else "")
        )
        if listing:
            lines += docs_obligations.suppression_lines(by_comment)
    if coarse:
        lines.append(
            f"  raised at file granularity: {len(coarse)} pointer section(s) no region "
            "finder could place"
        )
        lines += coarse
    # KYO-555.c7: the history lint `kyo check-branch` runs at a worker's stop, both
    # grades, so a worker sees what will print (or block) before it stops.
    history = docs_history.branch_history_lint(
        ctx.code_root, against, branch, changed, documents_in.section_status
    )
    blocking = len(history.blocking)
    lines.append(
        f"  history lint: {len(history.matches)} history phrase(s) newly introduced into "
        f"{len(history.documents)} changed ssot document(s)"
        + (f", {blocking} of which would block `kyo check-branch`" if blocking else "")
    )
    lines += [f"    {line}" for line in history.lines()]
    lines.append(
        "  not covered here: obligations a record's own doc-refs raise. Those are a "
        "stored declaration and this command reads no store"
    )
    lines.append(f"  {PREVIEW_WRITES_NOTHING}")
    return Output(
        lines,
        {
            "ok": True,
            "command": "docs preview",
            "wrote_to_store": False,
            "against": against,
            "branch": branch,
            "root": str(ctx.code_root),
            "changed_files": changed,
            "document_paths": list(documents_in.globs),
            "obligations": [
                {
                    "doc_path": obligation.doc_path,
                    "section": obligation.section,
                    "cause": obligation.cause,
                    "detail": obligation.says,
                    "targets": list(obligation.targets),
                    "changed": list(obligation.changed),
                }
                for obligation in found
            ],
            "groups": [_group_json(group) for group in groups],
            # KYO-352.c4. Always carried, never gated on the flag: the flag is about how
            # long the terminal output is, and a machine reader auditing a hundred
            # narrowed targets is the reader this list was built for.
            "suppressed": [
                {
                    "doc_path": obligation.doc_path,
                    "section": obligation.section,
                    "cause": obligation.cause,
                    "targets": list(obligation.targets),
                    "changed": list(obligation.changed),
                    "detail": obligation.says,
                    "comment_only": obligation.comment_only,
                }
                for obligation in suppressed
            ],
            # KYO-500.c2: the pointer obligations above that raised at file granularity.
            "raised_at_file_granularity": [
                {"doc_path": obligation.doc_path, "section": obligation.section}
                for obligation in found
                if obligation.coarse
            ],
            "covers_doc_refs": False,
            "history_lint": history.as_json(),
        },
    )


#: KYO-303. Said on every `kyo docs suggest` run, in both outcomes. The command's whole
#: value depends on the reader believing the first half, and the second half is what
#: stops it from becoming an obligation nobody agreed to: nothing in `kyo doctor`'s
#: problems reads a suggestion, and the one notice it does produce is switchable off.
SUGGEST_WRITES_NOTHING = (
    "nothing was written and nothing is enforced: `documents.yml` is your declaration "
    "and this command only says what the repository looks like. No `kyo doctor` problem "
    "depends on any of it; the single NOTICE it prints is turned off with "
    "`docs_suggest_notice: false` in kyosei/config.yml"
)


#: KYO-299. Said on every `kyo docs assemble` run that has follow-up commands to name,
#: and worded so that nobody has to look for the guarantee: Kyosei prints these and runs
#: none of them, the same posture `kyo dispatch` takes with `worktree_setup`.
ASSEMBLE_NAMES_COMMANDS = (
    "then run this project's own follow-up command(s) — `kyo docs assemble` NAMES these "
    "and runs none of them, exactly as a brief prints worktree_setup and executes none "
    "of it. Numbering and appending are Kyosei's; your index and your validator are "
    "yours"
)


def _cmd_docs_suggest(ctx: Context, args: Namespace) -> Output:
    """``kyo docs suggest [--json]`` — KYO-303.c1, a draft and never a decision.

    The tone and the posture are `kyo new`'s about scope, deliberately: that command
    prints ``derived (suggestion only, nothing stored)`` beside a record's touches, says
    where the suggestion came from, and stores nothing — "derivation never overrides a
    declaration". Documents had no such surface. `documents.yml` starts from a default
    pair of globs and everything after that is hand-authored, so a consumer with a
    27,000-line numbered notes file and a generated index beside it got nothing from the
    ``numbered:`` block KYO-299 built until somebody wrote it by hand, and nothing told
    them the block existed.

    **Opens no store and writes nothing**, like ``check`` and ``preview`` beside it. It
    reads ``documents.yml`` out of the store DIRECTORY (registries live beside the
    records) and the files out of the code tree, which is why it takes both roots.
    """
    found = docs_suggest.suggest(ctx.root / KYOSEI_DIR, ctx.code_root)
    documents_in = docs_parse.document_set(ctx.root / KYOSEI_DIR)
    data = {
        "ok": True,
        "command": "docs suggest",
        "wrote_to_store": False,
        "wrote_anything": False,
        "root": str(ctx.root),
        "code_root": str(ctx.code_root),
        "document_paths": list(documents_in.globs),
        "document_paths_declared": documents_in.declared,
        "label": docs_suggest.SUGGESTION_LABEL,
        "kinds": list(docs_suggest.SUGGESTION_KINDS),
        "suggestions": [suggestion.row() for suggestion in found],
        "says": SUGGEST_WRITES_NOTHING,
    }
    if not found:
        # One line, and it is a real answer rather than silence: "this project's
        # declaration already covers what I can see" is what a reader needs, and it is
        # not the same sentence as "this build cannot suggest anything" (law 6).
        return Output(
            [
                f"docs suggest: nothing to suggest — {documents_in.says}. "
                f"{SUGGEST_WRITES_NOTHING}"
            ],
            data,
        )
    lines = [
        f"docs suggest: {len(found)} suggestion(s) for {KYOSEI_DIR}/documents.yml "
        f"({docs_suggest.SUGGESTION_LABEL})",
        f"  scanned: {documents_in.says}",
    ]
    lines += docs_suggest.suggestion_lines(found)
    lines.append(f"  {SUGGEST_WRITES_NOTHING}")
    return Output(lines, data)


def _assemble_created_at(ctx: Context) -> dict[str, str] | None:
    """``{dispatch id: created_at}``, or ``None`` when no store could be read.

    Best-effort, and the distinction is carried rather than collapsed: "this store
    contains none of these dispatches" and "no store was consulted" produce the same
    ordering (everything falls to the by-file-name tail) and are different facts about
    WHY, and the command prints which one it acted on. A fixture repository with no
    store is a real case — it is how KYO-299.c6 is tested — and a command that could
    only run where a database exists would be one the end-to-end test cannot reach.
    """
    try:
        store = ctx.store()
    except Exception:  # noqa: BLE001 - a missing/unreadable store is an ordering input
        return None
    return {row["id"]: row["created_at"] for row in store.all_dispatches()}


def _cmd_docs_assemble(ctx: Context, args: Namespace) -> Output:
    """``kyo docs assemble [--dry-run]`` — KYO-299.c3, the controller's act.

    Run it on the MERGED tree, after a wave's branches are in and before the project's
    own index command. That is not a convention: it is the only moment the numbering is
    decidable. A worker cannot know what number its section gets, because the answer
    depends on every other branch in the wave, and the branches that would have to agree
    are the ones that cannot see each other. So the worker writes one file nobody else
    writes, and the number is assigned here, once, by the party that can see all of them.

    **It writes to the code tree, not to the store**, which makes it the first `kyo docs`
    action to write anywhere but the store, and the reason it is `kyo docs` and not
    `kyo assemble`: the declaration it acts on is a document set's, and the thing it
    changes is a document.

    ``--dry-run`` prints the same plan and writes nothing. Not a separate code path —
    the plan is computed in full either way and the flag decides only whether
    :func:`kyosei.docs_numbered.apply_assembly` is called — because a dry run that
    exercised different code would be a rehearsal of something else.
    """
    dry_run = bool(getattr(args, "dry_run", False))
    created_at = _assemble_created_at(ctx)
    plan = docs_numbered.assemble_plan(
        ctx.root / KYOSEI_DIR, ctx.code_root, created_at=created_at
    )
    data = {
        "ok": plan.ok,
        "command": "docs assemble",
        "root": str(ctx.code_root),
        "dry_run": dry_run,
        "wrote": False,
        "store_consulted": created_at is not None,
        "numbered_documents": [
            {
                "path": document.path,
                "set": document.set_name,
                "fragments": document.fragments_dir,
                "heading": document.heading,
                "after": list(document.after),
            }
            for document in plan.documents
        ],
        "assignments": [
            {
                "fragment": assignment.fragment,
                "doc_path": assignment.doc_path,
                "number": assignment.number,
                "dispatch_id": assignment.dispatch_id,
                "ordered_by_dispatch": assignment.ordered_by_dispatch,
                "heading": assignment.heading,
            }
            for assignment in plan.assignments
        ],
        "refusals": [
            {
                "fragment": refusal.fragment,
                "reason": refusal.reason,
                "says": refusal.says,
                "remedy": refusal.remedy,
            }
            for refusal in plan.refusals
        ],
        "follow_up": {path: list(after) for path, after in plan.follow_up.items()},
        # KYO-493: one entry per earlier section a fragment's `amends:` header names.
        "amendments": [amendment.as_json() for amendment in plan.amendments],
    }

    if not plan.documents:
        return Output(
            [
                "docs assemble: this project declares no numbered documents — no "
                f"document set in {ctx.root / KYOSEI_DIR / 'documents.yml'} carries a "
                "`numbered:` block, so there are no fragments to assemble. Declare one "
                "with `fragments:` (where workers write) and `heading:` (the template "
                "its numbered headings follow) to use this",
            ],
            data,
        )

    if not plan.ok:
        raise GuardRefusal(
            CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED,
            [
                "nothing was written — not the fragment below, and not the documents "
                "that had no problem. A half-assembled wave consumes numbers, deletes "
                "the fragments that carried them, and leaves you reconstructing which "
                "half happened",
                *(refusal.line() for refusal in plan.refusals),
            ],
            data=data,
        )

    documents_touched = sorted({assignment.doc_path for assignment in plan.assignments})
    if not plan.assignments:
        return Output(
            [
                "docs assemble: 0 fragment(s) — nothing to assemble, and nothing was "
                "written",
                *(
                    f"  {document.path} — fragments in {document.fragments_dir}/ "
                    f"(none present), last numbered section "
                    f"{_last_number_says(ctx.code_root, document)}"
                    for document in plan.documents
                ),
            ],
            data,
        )

    if not dry_run:
        docs_numbered.apply_assembly(ctx.code_root, plan)
        data["wrote"] = True

    lines = [
        f"docs assemble: {len(plan.assignments)} fragment(s) "
        f"{'would be appended' if dry_run else 'appended'} across "
        f"{len(documents_touched)} document(s)",
    ]
    for doc_path in documents_touched:
        lines.append(f"  {doc_path}")
        for assignment in plan.assignments:
            if assignment.doc_path != doc_path:
                continue
            who = assignment.dispatch_id or "(no dispatch in the file name)"
            how = (
                "dispatch-creation order"
                if assignment.ordered_by_dispatch
                else "ordered after the datable fragments, by file name"
            )
            lines.append(
                f"    {assignment.fragment}  ->  {assignment.number}  [{who}, {how}]"
            )
            if assignment.heading:
                lines.append(f"        {assignment.heading}")
        # KYO-493: the write-backs, before the follow-up commands are named — the
        # project's index command then reads blocks that are already correct.
        for amendment in plan.amendments:
            if amendment.doc_path != doc_path:
                continue
            lines.append(f"        {amendment.line()}")
            lines.append(f"            {amendment.heading.strip()}")
        after = plan.follow_up.get(doc_path)
        if after:
            lines.append(f"    {ASSEMBLE_NAMES_COMMANDS}")
            lines += [f"      {command}" for command in after]
    if created_at is None:
        lines.append(
            "  no store was consulted, so nothing could be placed in dispatch-creation "
            "order and every fragment was ordered by file name"
        )
    if dry_run:
        lines.append(
            "  --dry-run: nothing was written and no fragment was removed. Re-run "
            "without --dry-run to apply exactly this plan"
        )
    else:
        lines.append(
            f"  {len(plan.removes)} fragment file(s) removed; commit the documents and "
            "the removals together"
        )
    return Output(lines, data)


def _last_number_says(code_root: Path, document: docs_numbered.NumberedDocument) -> str:
    """``204``, or a phrase for a document that has no numbered heading yet — including
    one that does not exist, which is a legitimate state for a declaration made before
    the first section is written."""
    source = code_root / document.path
    if not source.is_file():
        return "(the document does not exist yet; the first section would be 1)"
    found = docs_numbered.last_number(source.read_text(encoding="utf-8"), document.heading)
    return str(found) if found is not None else "(none yet; the next would be 1)"


def _group_json(group: dict) -> dict:
    """A grouping as a machine consumer reads it: everything except ``members``.

    The members are the rows, and the rows are already carried in full beside the
    groupings on every surface that prints them. Emitting them twice would make a
    consumer choose which copy is authoritative, which is the question law 3 exists to
    prevent anyone from having to answer.
    """
    return {key: value for key, value in group.items() if key != "members"}


def _obligation_row(row) -> dict:
    return {
        "id": row["id"],
        "record_id": row["record_id"],
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


def _obligation_groupings(
    rows: list[dict], whole_file: dict[tuple[str, str], tuple[str, ...]] | None = None
) -> list[dict]:
    """The open obligations in ``rows``, grouped by (dispatch, document) (KYO-159).

    **Why the document, and why within one dispatch.** Replaying every stored report's
    ``touched_files[]`` through the raise path against this repository's own ``governs:``
    declarations gives four candidate units, and they cost very differently::

        per obligation (today)    416   mean 4.04    —
        per governed FILE         309   mean 3.00    26% fewer acts
        per DOCUMENT              145   mean 1.41    65% fewer acts
        per DISPATCH               83   mean 0.81    80% fewer acts

    27 sections ever fire and they concentrate in two documents, so one changed file
    typically fires several sections of the SAME document. Grouping by governed file
    makes a reader cross between the charter and the README for each file; grouping by
    document lets them open one document once and answer every section that dispatch
    disturbed, which is why the document is the unit this prints a line for.

    The per-DISPATCH row is cheaper still and is deliberately NOT offered: a dispatch's
    obligations span two documents, and opening two files is two readings however few
    commands it takes to record them. This prints the cheapest unit that is honestly one
    editorial act, not the cheapest unit.

    Within one dispatch, because two dispatches raising against one document are two
    reviews of two different diffs. A grouping across them would be this function
    forming a set out of unrelated work, which the tool never does (see
    :func:`_cmd_docs_settle`).

    Nothing here settles, drops, collapses or hides an obligation: every row is still
    listed individually above its group's line, and the line names every member. This is
    a rendering of the same rows, not a second set of them (KYO-159.c2).

    **KYO-199.c1/c2**: the per-document half is
    :func:`docs_obligations.group_obligations_per_document` now — the same function ``kyo
    report`` and ``kyo docs preview`` group with, so the
    three surfaces cannot disagree about the unit. What stays here is the *dispatch*
    half, which is this command's alone: it lists rows from many dispatches, and the two
    paragraphs above are why they must not be pooled.

    ``whole_file`` maps (document, section) to the whole-file ``governs:`` targets that
    section declares, for c2's once-per-document nudge. Passed in rather than read here
    because it costs a walk of the documents, and this function is also called by a
    caller (``--json``) that has already paid for one.
    """
    # KYO-554: a standing obligation has no dispatch, so its record stands in for one:
    # two records' standing obligations are two decisions, never one editorial act.
    order: list[tuple[str | None, str | None]] = []
    per_dispatch: dict[tuple[str | None, str | None], list[dict]] = {}
    for row in rows:
        if row["verdict"] is not None:
            continue
        key = (row["dispatch_id"], None if row["dispatch_id"] else row["record_id"])
        if key not in per_dispatch:
            per_dispatch[key] = []
            order.append(key)
        per_dispatch[key].append(
            {**row, "targets": (whole_file or {}).get((row["doc_path"], row["section"]), ())}
        )
    groups: list[dict] = []
    for key in order:
        dispatch_id, standing_on = key
        for group in docs_obligations.group_obligations_per_document(per_dispatch[key]):
            group["dispatch_id"] = dispatch_id
            group["record_id"] = group["members"][0]["record_id"]
            if standing_on is not None:
                group["standing_on"] = standing_on
            groups.append(group)
    return groups


def _cmd_docs_obligations(ctx: Context, args: Namespace) -> Output:
    """``kyo docs obligations`` — what this project's documents are owed (KYO-137).

    Open by default and ``--all`` for the history, on the same reasoning `kyo flag`'s
    listing uses: what a controller needs on sight is the work outstanding, and the
    settled rows are evidence somebody looked rather than a queue.

    KYO-159 adds the second half of the listing: beside each (dispatch, document)
    grouping of the open rows, the ``kyo docs settle`` line that would discharge it with
    every id spelled out. See :func:`_obligation_groupings` for which unit is grouped and
    why, and :func:`docs_obligations.settle_command` for why the line is completed rather than
    run.

    **KYO-199.c1**: the open rows are now *printed* in those groupings rather than as a
    flat list with a tail naming which documents held more than one. The tail was a
    second rendering of the grouping the listing already had, and a reader following it
    scrolled back up to find the rows it meant. One block per document, its sections
    beneath it, its settle line at its foot — the same shape ``kyo report`` and
    ``kyo docs preview`` print, out of the same function.

    Settled rows (``--all``) stay a flat list underneath, because they are history rather
    than work: there is nothing to settle, so there is no act to group them into.

    Read-only, and still read-only after both additions. Nothing here decides that a
    document is fresh — that is :func:`_cmd_docs_settle`'s job and it takes a person's
    verdict to do it — and nothing here settles a grouping either. It prints a command
    and runs none of it.
    """
    store = ctx.store()
    rows = [
        _obligation_row(row)
        for row in store.conn.execute(
            "SELECT * FROM docs_obligations ORDER BY record_id, seq"
        )
    ]
    if getattr(args, "record", None):
        rows = [row for row in rows if row["record_id"] == args.record]
    shown = rows if getattr(args, "all", False) else [r for r in rows if r["verdict"] is None]
    groupings = _obligation_groupings(
        shown,
        docs_obligations.section_whole_file_targets(
            ctx.code_root, documents_in=docs_parse.document_set(ctx.root / KYOSEI_DIR)
        ),
    )

    lines: list[str] = []
    open_count = sum(1 for row in rows if row["verdict"] is None)
    scope = f" on {args.record}" if getattr(args, "record", None) else ""
    lines.append(
        f"docs obligations{scope}: {open_count} open of {len(rows)}"
        + ("" if getattr(args, "all", False) else " (open only; --all for every one)")
    )
    if not shown:
        # Law 6, and the shape `docs check`'s no-documents state already has one level
        # down: an empty list here means nothing is outstanding, and it says which
        # question it is answering so it cannot be read as "nothing was raised, ever".
        lines.append(
            "  (none) — no document section is waiting on a verdict"
            if not getattr(args, "all", False)
            else "  (none) — no dispatch has raised one against this project's documents"
        )
    lines += docs_obligations.grouped_obligation_lines(groupings)
    if groupings:
        lines.append(
            "  each line above is copied and completed, never run as printed: the tool "
            "has the ids and does not have the verdict, the method or the reason. "
            "Settling a grouping settles exactly the ids it names, and nothing else"
        )
    settled = [row for row in shown if row["verdict"] is not None]
    if settled:
        lines.append(f"settled ({len(settled)}) — history, not work:")
        for row in settled:
            lines.append(
                f"  {row['id']}  [{row['verdict']}]  {row['doc_path']} [{row['section']}]  "
                f"({row['cause']}, {row['dispatch_id'] or 'standing'})"
            )
            lines.append(f"      {row['method']}  {row['author']}  {row['settled_at']}")
            if row["settled_why"]:
                lines.append(f"      why: {row['settled_why']}")
    return Output(
        lines,
        {
            "ok": True,
            "command": "docs obligations",
            "open": open_count,
            "total": len(rows),
            "obligations": shown,
            # Law 3: the text above and this are two renderings of one computation.
            # Every grouping is carried, not only the multi-member ones the text
            # bothers to print a line for — a consumer counting acts needs all of them.
            "groupings": [_group_json(group) for group in groupings],
        },
    )


def _named_settle_ids(args: Namespace) -> list[str]:
    """The obligation ids the caller spelled out, in order, without repeats.

    ``nargs="*"`` hands back a list; a hand-built :class:`argparse.Namespace` (anything
    embedding these handlers) may still hand back the single string the flag took before
    KYO-159, and both mean the same thing. A repeated id is one obligation named twice,
    not a set of two, so it collapses — the *set* is still exactly what the caller wrote,
    which is the whole of KYO-159.c2's "every member is a member the caller named".
    """
    raw = getattr(args, "id", None) or []
    if isinstance(raw, str):
        raw = [raw]
    ids: list[str] = []
    for value in raw:
        value = str(value).strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def _check_settle_all_shape(args: Namespace, ids: list[str]) -> None:
    """``kyo docs settle --all`` is ``--record <id> --all`` and nothing else (KYO-528.c2).

    A separate function so its two refusals are their own error sites and
    :func:`_cmd_docs_settle`'s keep their ordinals.
    """
    if ids:
        raise UsageError(
            "`kyo docs settle --all` settles every open obligation of the record named "
            "by --record, so it takes no obligation id; drop the ids, or drop --all to "
            "settle just those"
        )
    if not getattr(args, "record", None):
        raise UsageError(
            "`kyo docs settle --all` needs --record <id>: it settles every open "
            "obligation of that one record, e.g. `kyo docs settle --record KYO-1 "
            "--all --verdict not-affected --method <how> --why <reason>`"
        )


def _cmd_docs_settle(ctx: Context, args: Namespace) -> Output:
    """``kyo docs settle`` — a controller's verdict against a named set (KYO-137.c4,
    KYO-159).

    The docs-governance rule, and this function is the whole of it: **a document counts as
    updated when a controller records a verdict with a method, not when its timestamp
    moves.** Nothing anywhere else can clear an obligation. There is no path that
    compares an mtime, no path that compares two git dates, and no ``--force``; the
    absence is the mechanism, because the alternative — a checker that also clears what
    it raised — is what makes a typo fix look like a documentation update.

    ``--method`` is required for the same reason `kyo verify` requires it (law 9): a
    verdict without its instrument is a claim whose weight cannot be judged later, and
    "I re-read the section" and "a test asserts this" are both honest answers that a
    reader must be able to tell apart.

    An already-settled obligation is refused rather than overwritten (law 4/9: verdicts
    are provenance, and quietly replacing one destroys the contest that is the valuable
    part). The refusal names what the standing verdict is and who recorded it.

    **KYO-159: more than one id is one act over a set the CALLER named.** KYO-153
    measured that the rate a controller experiences is set on this path, not on the raise
    path, and that the cure is not dedup — deduplicating per governed file drops
    obligations that are genuinely distinct (the charter's explanation and the README's
    documentation of one mechanism can both be wrong), and settling one would silently
    discharge the other. That is a false negative at a blocking gate, which the
    docs-governance design exists to prevent. So nothing is dropped and nothing is
    derived. Three properties, and
    each is a refusal rather than an intention:

    * **The tool forms no set.** There is no ``--all-for-document``, ``--all-for-file`` or
      ``--all-for-dispatch``, because a derived set is the tool drawing the boundary of a
      review it did not perform. :func:`_cmd_docs_obligations` prints the candidate line
      with every id spelled out and applies none of it — the KYO-71 pattern, where
      ``kyo report`` prints the exact ``kyo edit --touches-file`` line it would need.
    * **The set is verdict-homogeneous, by construction.** One ``--verdict`` for the whole
      set and no spelling for a per-member one. "I changed all six of these" and "I read
      all six and none needed changing" are each one defensible statement; "some of each"
      is not, and refusing the mix partitions sets mechanically rather than by the
      caller's good intentions.
    * **All or nothing.** Every named id is resolved and checked for a standing verdict
      *before* any row is written, so a set containing one already-settled member settles
      nobody. The alternative — settle what you can, refuse the rest — would leave a set
      whose members carry different verdicts recorded in different acts, which is the
      mixed state the paragraph above refuses at the front door.

    A reason is required for a set of more than one (``--why``/``--why-file``). For a
    single obligation the section and the verdict already say what was reviewed; for six,
    the reason IS the evidence that the review covered every member, and KYO-159.c4 asks
    for exactly that. It is the one thing a rubber stamp cannot supply cheaply.

    **KYO-199.c4** adds the second form, ``--from-report``, in
    :func:`_cmd_docs_settle_from_report`. The three refusals above are unaffected by it:
    it settles nothing that already carries a verdict, it forms no set of its own, and
    the verdicts it applies were each written down by the worker with its own method and
    reason. What it removes is the retyping.
    """
    if getattr(args, "from_report", None):
        return _cmd_docs_settle_from_report(ctx, args)
    if getattr(args, "only", None):
        raise UsageError(
            "--only selects from a report's docs_verdicts[], so it needs --from-report "
            "<dispatch-id>; settling obligations by hand already names them positionally"
        )
    store = ctx.store()
    ids = _named_settle_ids(args)
    if getattr(args, "all", False):
        # KYO-528.c2: the set is the caller's own naming of one RECORD, spelled out in
        # the receipt; the tool still derives nothing from a document or governed file.
        _check_settle_all_shape(args, ids)
        record_id = store.record(args.record)["id"]
        ids = [row["id"] for row in store.open_docs_obligations(record_id)]
        if not ids:
            return Output(
                [f"{record_id} has no open docs obligations; nothing was settled"],
                {
                    "ok": True,
                    "command": "docs settle",
                    "record_id": record_id,
                    "obligations": [],
                    "settled": 0,
                },
            )
    if not ids:
        raise UsageError(
            "`kyo docs settle` needs at least one obligation id. `kyo docs obligations` "
            "lists what is open and prints the line to copy for each grouping; "
            "`--record <id> --all` settles every open obligation of one record"
        )
    verdict = args.verdict
    if verdict not in OBLIGATION_VERDICTS:
        raise UsageError(
            f"{verdict!r} is not a docs verdict. `kyo docs settle <id> --verdict "
            f"{OBLIGATION_UPDATED} --method <how>` if the document was changed to match "
            f"the work; --verdict {OBLIGATION_NOT_AFFECTED} if it was read and needs no "
            "change. There is deliberately no verdict for 'not looked at yet' — that is "
            "what an open obligation already is, and none for 'some of each' — a set "
            "carrying two answers is two acts"
        )
    method = single_line(args.method, what="--method")
    why = _reason(args)
    if len(ids) > 1 and not why:
        raise UsageError(
            f"settling {len(ids)} obligations in one act needs --why (or --why-file): "
            "one verdict and one method over a set say what was decided, and the reason "
            "is the only place that says what the review covered. Name what made these "
            f"{len(ids)} one editorial act, or settle them one at a time"
        )

    with store.transaction():
        # Resolved first, every one of them, before a single row is written. A NotFound
        # here names the id that does not exist and leaves the rest untouched.
        rows = [store.docs_obligation(obligation_id) for obligation_id in ids]
        standing_verdicts = [row for row in rows if row["verdict"] is not None]
        if standing_verdicts:
            raise ContractViolation(
                CONTRACT_DOCS_OBLIGATION_ALREADY_SETTLED,
                [
                    f"{row['id']} already carries the verdict {row['verdict']!r}, "
                    f"recorded by {row['author']} at {row['settled_at']} "
                    f"({row['method']})"
                    for row in standing_verdicts
                ]
                + [
                    "A verdict is provenance and is never overwritten (law 9). If the "
                    "document has since changed again, that is a new observation about "
                    "a new state of the code, and the dispatch that changed it raises "
                    "its own obligation.",
                ]
                + (
                    [
                        f"Nothing was settled: {len(standing_verdicts)} of the "
                        f"{len(ids)} obligations named already carry a verdict, and "
                        "settling the rest would leave this set holding two verdicts "
                        "recorded in two acts. Name the open ones.",
                    ]
                    if len(ids) > 1
                    else []
                ),
            )
        # Distinct, in first-named order: a set may span records, and each one that
        # loses an obligation needs its export regenerated and its updated_at moved.
        record_ids: list[str] = []
        for row in rows:
            if row["record_id"] not in record_ids:
                record_ids.append(row["record_id"])
        before = {
            record_id: export.record_document(store, record_id) for record_id in record_ids
        }
        # One timestamp for the whole act, which is what makes it readable as one act
        # later: `kyo show` renders these rows with their settled_at, and a set settled
        # together shares a verdict, a method, a reason, an author AND a moment.
        stamp = now()
        store.conn.executemany(
            "UPDATE docs_obligations SET verdict = ?, method = ?, author = ?,"
            " settled_at = ?, settled_why = ? WHERE id = ?",
            [(verdict, method, ctx.actor, stamp, why, obligation_id) for obligation_id in ids],
        )
        for record_id in record_ids:
            _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            # Every member, spelled out, in the ledger. The event is where "these were
            # one act" is recorded, and it names the set rather than summarising it —
            # so no schema column is needed to answer "what did that one act cover".
            command=f"docs settle {' '.join(ids)} {verdict}",
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)

    settled_rows = [_obligation_row(store.docs_obligation(obligation_id)) for obligation_id in ids]
    still_open = {
        record_id: len(store.open_docs_obligations(record_id)) for record_id in record_ids
    }
    if len(ids) == 1:
        lines = [f"settled {ids[0]}: {rows[0]['doc_path']} [{rows[0]['section']}]"]
    else:
        lines = [
            f"settled {len(ids)} obligations in one act, all carrying the same verdict, "
            "method and reason:"
        ]
        lines += [
            f"  {row['id']}  {row['doc_path']} [{row['section']}]" for row in settled_rows
        ]
    lines.append(echo_value("verdict", verdict))
    lines.append(echo_value("method", method))
    if why:
        lines.append(echo_value("why", why))
    for record_id in record_ids:
        open_now = still_open[record_id]
        lines.append(
            f"{record_id} now has {open_now} open docs obligation(s)"
            + ("" if open_now else f" — {explanations.say('docs-nothing-blocks')}")
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "docs settle",
            # `obligation`/`open_on_record`/`record` are the single-obligation shape this
            # command has always returned, kept exactly as they were for the caller that
            # settles one: the first member, its record's open count, its record. The
            # plural keys beside them are the whole act.
            "record_id": record_ids[0],
            "obligation": settled_rows[0],
            "open_on_record": still_open[record_ids[0]],
            "record": after[record_ids[0]],
            "obligations": settled_rows,
            "settled": len(settled_rows),
            "open_by_record": still_open,
            "records": [after[record_id] for record_id in record_ids],
        },
    )


def _refuse_flags_that_from_report_supplies(args: Namespace) -> None:
    """A verdict typed beside ``--from-report`` is the retyping this path removes, and
    honouring one would silently overwrite what a worker actually reported."""
    supplied = [
        flag
        for flag, value in (
            ("OBLIGATION_ID", _named_settle_ids(args)),
            ("--verdict", args.verdict),
            ("--method", args.method),
            ("--why", getattr(args, "why", None)),
            ("--why-file", getattr(args, "why_file", None)),
        )
        if value
    ]
    if supplied:
        raise UsageError(
            f"--from-report reads the verdict, method and reason from the report's "
            f"docs_verdicts[], so it takes none of {', '.join(supplied)}. Applying a "
            "subset is --only <obligation-id> ...; recording a verdict of your own "
            "instead is `kyo docs settle <obligation-id> --verdict <verdict> --method "
            "<how>`, without --from-report"
        )


def _cmd_docs_settle_from_report(ctx: Context, args: Namespace) -> Output:
    """``kyo docs settle --from-report <dispatch-id> [--only <obligation-id>...]``.

    KYO-199.c4. The worker read the governed sections its own diff raised — it had the
    diff and the document open, which the controller no longer does days later — and
    wrote one entry per section into its report's ``docs_verdicts[]``. This applies those
    entries as one controller act, attributed to ``ctx.actor``, because the controller is
    who is applying them and not the worker who proposed them. Exactly the shape
    ``kyo verify --from-report`` (KYO-198) has, for the same measured reason: the
    deliberate step is unchanged, what it costs is not.

    **Why this may carry two verdicts where a hand-typed set may not.** KYO-159 refuses a
    mixed set because *the caller* forming one would be recording "some of each" as a
    single statement they never made. Nothing is formed here: each entry is its own
    statement, with its own verdict, its own method and its own reason, written by the
    party who did that reading. Grouping them into one transaction records that the
    controller reviewed and applied them together, which is true, and does not merge what
    they say. The homogeneity rule still binds every hand-typed set, unchanged.

    **Two kinds of entry are named rather than applied**, both printed by name so the
    count that landed is never inferred from the count in the file (law 6):

    * an entry matching no obligation this dispatch raised — a paraphrased heading, or a
      verdict on a section the branch never touched. See
      :func:`docs_numbered.match_docs_verdicts`.
    * an obligation that already carries a verdict. Verdicts are provenance and are never
      overwritten (law 4/9), and unlike the hand-typed path this does not refuse the
      whole run for one: the rest is the controller's review of a worker's reading, and
      sending them back to per-obligation retyping over one already-settled row is the
      cost this exists to remove.
    """
    store = ctx.store()
    _refuse_flags_that_from_report_supplies(args)
    dispatch_id = args.from_report
    dispatch = store.dispatch(dispatch_id)
    report_id = dispatch["report_id"]
    if not report_id:
        raise UsageError(
            f"dispatch {dispatch_id} carries no ingested report, so there are no "
            f"docs_verdicts to apply (its state is {dispatch['state']!r}). Ingest the "
            f"worker's report first: `kyo report {dispatch_id} --file <report-path>`"
        )
    body = json.loads(store.report(report_id)["body_json"])
    entries = list(body.get("docs_verdicts") or [])
    rows = [
        _obligation_row(row)
        for row in store.docs_obligations(dispatch["record_id"])
        if row["dispatch_id"] == dispatch_id
    ]
    # KYO-393: the same numbered-document reading `kyo report`'s ingest passes.
    matched, unmatched = docs_numbered.match_docs_verdicts(
        rows, entries, docs_numbered.numbered_templates(ctx.root / KYOSEI_DIR, ctx.code_root)
    )
    by_number = [pair for pair in matched if pair.get("by_number") is not None]

    not_applied = [
        {
            "obligation_id": None,
            "doc_path": row["entry"]["doc_path"],
            "section": row["entry"]["section"],
            "why": docs_numbered.unmatched_verdict_line(row),
        }
        for row in unmatched
    ]
    if args.only:
        wanted = list(dict.fromkeys(args.only))
        present = {pair["obligation"]["id"] for pair in matched}
        absent = [obligation_id for obligation_id in wanted if obligation_id not in present]
        if absent:
            raise UsageError(
                f"--only names {', '.join(absent)}, which report {report_id} carries no "
                f"matched docs verdict for (it carries: "
                f"{', '.join(sorted(present)) or '(none)'}). Nothing was applied: drop "
                f"{', '.join(absent)} from --only, or drop --only to apply every verdict "
                f"{report_id} matched"
            )
        selected = [pair for pair in matched if pair["obligation"]["id"] in set(wanted)]
    else:
        selected = matched

    pending: list[dict] = []
    for pair in selected:
        obligation, entry = pair["obligation"], pair["entry"]
        if obligation["verdict"] is not None:
            not_applied.append(
                {
                    "obligation_id": obligation["id"],
                    "doc_path": obligation["doc_path"],
                    "section": obligation["section"],
                    "why": (
                        f"already carries the verdict {obligation['verdict']!r}, recorded "
                        f"by {obligation['author']} at {obligation['settled_at']} "
                        f"({obligation['method']}). A verdict is provenance and is never "
                        "overwritten (law 9)"
                    ),
                }
            )
            continue
        if entry["verdict"] not in OBLIGATION_VERDICTS:  # pragma: no cover - schema-checked
            raise UsageError(
                f"report {report_id} gives {obligation['id']} the verdict "
                f"{entry['verdict']!r}, which is not one of "
                f"{', '.join(OBLIGATION_VERDICTS)}"
            )
        pending.append(
            {
                "obligation": obligation,
                "verdict": entry["verdict"],
                "method": single_line(entry["method"], what="docs_verdicts[].method"),
                "why": entry["reason"],
            }
        )

    applied: list[dict] = []
    record_ids: list[str] = []
    for row in pending:
        record_id = row["obligation"]["record_id"]
        if record_id not in record_ids:
            record_ids.append(record_id)
    after: dict = {}
    events: list = []
    if pending:
        with store.transaction():
            before = {
                record_id: export.record_document(store, record_id)
                for record_id in record_ids
            }
            stamp = now()
            store.conn.executemany(
                "UPDATE docs_obligations SET verdict = ?, method = ?, author = ?,"
                " settled_at = ?, settled_why = ? WHERE id = ?",
                [
                    (
                        row["verdict"],
                        row["method"],
                        ctx.actor,
                        stamp,
                        row["why"],
                        row["obligation"]["id"],
                    )
                    for row in pending
                ],
            )
            for record_id in record_ids:
                _touch_record(store, record_id)
            after, events = commands._commit_and_export(
                store,
                # Every member spelled out, as the hand-typed path does it: the event is
                # where "these were one act" is recorded, and it names the set.
                command=(
                    f"docs settle --from-report {dispatch_id} "
                    + " ".join(row["obligation"]["id"] for row in pending)
                ),
                actor=ctx.actor,
                record_ids=record_ids,
                before=before,
            )
        commands._finish_mutation(store, record_ids, events)
        applied = [
            {
                "obligation_id": row["obligation"]["id"],
                "record_id": row["obligation"]["record_id"],
                "doc_path": row["obligation"]["doc_path"],
                "section": row["obligation"]["section"],
                "verdict": row["verdict"],
                "method": row["method"],
            }
            for row in pending
        ]

    lines = [
        f"applied {len(applied)} of {len(entries)} docs verdict(s) from report "
        f"{report_id} (dispatch {dispatch_id}), as {ctx.actor}"
    ]
    for row in applied:
        lines.append(
            f"  {row['obligation_id']}  {row['doc_path']} [{row['section']}] -> "
            f"{row['verdict']}  ({row['method']})"
        )
    if by_number:
        # KYO-393.c1: matched by section number, and named — the heading differs.
        lines.append(f"matched by section number, heading differs ({len(by_number)}):")
        lines += [f"  - {docs_numbered.number_matched_line(pair)}" for pair in by_number]
    if args.only:
        lines.append(
            f"--only: {len(selected)} of the report's {len(matched)} matched verdict(s) "
            "selected; the rest are unapplied and stay readable in the report"
        )
    lines.append(f"not applied ({len(not_applied)}):")
    lines += [
        f"  - {row['obligation_id'] or row['doc_path']}: {row['why']}"
        for row in not_applied
    ] or ["  (none)"]
    still_open = {
        record_id: len(store.open_docs_obligations(record_id)) for record_id in record_ids
    }
    for record_id in record_ids:
        open_now = still_open[record_id]
        lines.append(
            f"{record_id} now has {open_now} open docs obligation(s)"
            + ("" if open_now else f" — {explanations.say('docs-nothing-blocks')}")
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "docs settle",
            "from_report": dispatch_id,
            "report_id": report_id,
            "record_id": dispatch["record_id"],
            "applied": applied,
            "settled": len(applied),
            "not_applied": not_applied,
            # KYO-393: the two halves `kyo accept` reads back — the entries that matched
            # no obligation (a subset of `not_applied`, which also names already-settled
            # rows) and the ones matched by section number with a differing heading.
            "unmatched": [
                {
                    "doc_path": row["entry"]["doc_path"],
                    "section": row["entry"]["section"],
                    "verdict": row["entry"]["verdict"],
                    "why": row["why"],
                    "line": docs_numbered.unmatched_verdict_line(row),
                }
                for row in unmatched
            ],
            "matched_by_number": [
                {
                    "obligation_id": pair["obligation"]["id"],
                    "doc_path": pair["entry"]["doc_path"],
                    "section": pair["entry"]["section"],
                    "obligation_section": pair["obligation"]["section"],
                    "number": pair["by_number"],
                }
                for pair in by_number
            ],
            "open_by_record": still_open,
            "records": [after[record_id] for record_id in record_ids] if after else [],
        },
    )


def _cmd_ideas_review(ctx: Context, args: Namespace) -> Output:
    """``kyo ideas review`` — unpromoted ideas by age, with advisory broken-mention
    flags (law 12/13: the ideas layer is never enforced against — this only lists).

    KYO-30.c4: "promoted" is read from top-of-file frontmatter. A file still carrying
    the pre-KYO-30 bottom block is *also* recognised — dropping it back into this
    listing would offer an already-promoted idea for promotion a second time — but it
    is counted separately and the count names ``kyo ideas migrate-stamps``, because a
    stamp Obsidian cannot see is the whole defect this record exists to fix.
    """
    root = ctx.root
    reference = datetime.strptime(now(), TIMESTAMP_FORMAT).replace(tzinfo=UTC)

    items = []
    legacy_stamped: list[str] = []
    for path in ideas_module.idea_files(root):
        text = path.read_text(encoding="utf-8")
        if ideas_module.needs_stamp_migration(text):
            legacy_stamped.append(path.relative_to(root).as_posix())
            continue
        if ideas_module.is_stamped(text):
            continue
        at, source = ideas_module.last_activity_at(root, path)
        items.append(
            {
                "path": path.relative_to(root).as_posix(),
                "last_activity": at,
                "last_activity_source": source,
                "age_days": ideas_module.age_days(reference, at),
                "flags": ideas_module.flagged_mentions(root, text),
            }
        )
    items.sort(key=lambda item: item["last_activity"])

    lines = [f"{len(items)} unpromoted idea(s), oldest first:"]
    for item in items:
        flag_text = f"  flags: {', '.join(item['flags'])}" if item["flags"] else ""
        lines.append(
            f"  {item['path']:<40} age:{item['age_days']:>4}d "
            f"({item['last_activity_source']}){flag_text}"
        )
    if not items:
        lines.append("  (none)")
    if legacy_stamped:
        lines.append(
            f"{len(legacy_stamped)} promoted idea(s) still carry the old bottom-of-file "
            "stamp, which Obsidian cannot render; run `kyo ideas migrate-stamps` to see "
            "what moving them to frontmatter would do"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "ideas review",
            "ideas": items,
            "legacy_stamped": legacy_stamped,
        },
    )


def _cmd_ideas_migrate_stamps(ctx: Context, args: Namespace) -> Output:
    """``kyo ideas migrate-stamps [--apply]`` — move bottom-of-file promotion stamps
    into top-of-file YAML frontmatter (KYO-30.c4).

    Dry-run by default, and loud about which mode it ran in: this is the one command in
    the tool that rewrites files in ``ideas/``, the human's own directory, and a human
    is owed the list before the edit. Idempotent by construction rather than by
    bookkeeping — :func:`ideas.migrate_text` returns ``None`` for a file with no
    trailing block left, so a second run finds nothing to do without consulting any
    record of the first (KYO-30.c5).
    """
    root = ctx.root
    pending: list[tuple[Path, str, dict]] = []
    for path in ideas_module.idea_files(root):
        text = path.read_text(encoding="utf-8")
        migrated = ideas_module.migrate_text(text)
        if migrated is None:
            continue
        stamps = ideas_module.promotion_stamps(migrated)
        pending.append(
            (
                path,
                migrated,
                {
                    "path": path.relative_to(root).as_posix(),
                    "promoted": [stamp.record_id for stamp in stamps],
                    "promoted_at": [stamp.at for stamp in stamps],
                },
            )
        )

    applied = bool(args.apply)
    if applied:
        for path, migrated, _ in pending:
            path.write_text(migrated, encoding="utf-8")

    files = [entry for _, _, entry in pending]
    if not files:
        lines = ["0 idea file(s) carry a bottom-of-file promotion stamp; nothing to migrate"]
    else:
        verb = "migrated to top-of-file frontmatter" if applied else "would be migrated"
        lines = [f"{len(files)} idea file(s) {verb}:"]
        lines.extend(
            f"  {entry['path']:<50} promoted: {', '.join(entry['promoted'])}" for entry in files
        )
        if not applied:
            lines.append("nothing written (dry run); re-run with --apply to write them")
    return Output(
        lines,
        {
            "ok": True,
            "command": "ideas migrate-stamps",
            "applied": applied,
            "count": len(files),
            "files": files,
        },
    )


def _plan_members(store: Store, plan_id: str) -> list[dict]:
    """Members of a plan, with their statuses (KYO-27).

    Membership is stored on the MEMBER (``plan_id``), so nothing walked the
    relation the other way and reading a plan meant globbing ``kyosei/export/``.
    ``plan`` is the one kind whose completion is *defined* as a status
    distribution over its members (kinds.yml: ``requires_criteria: false``,
    ``dispatchable: false``) — so this was its entire purpose going unrendered,
    while ``kyo show`` printed every section a plan is defined not to have.

    Derived at display time on purpose: putting it in the export document would
    make a member's ``--plan`` edit silently stale the *plan's* export, which is a
    drift class this store does not otherwise have.
    """
    return [
        dict(row)
        for row in store.conn.execute(
            "SELECT id, kind, status, title FROM records WHERE plan_id = ? ORDER BY seq",
            (plan_id,),
        )
    ]


def _standings(store: Store, docs: list[dict]) -> dict[str, str]:
    """id -> the word each record's status is rendered as (KYO-44.c2).

    Derived from the registry at render time rather than stored: the archetype is
    already known wherever a record is displayed, so a column would be a second copy
    of one fact (law 3). A kind missing from the registry falls back to the raw
    status — `kyo doctor` is what reports that, not a renderer guessing.
    """
    registry = _load_registry(store)
    ladder = _load_status_ladder(store)
    out: dict[str, str] = {}
    for doc in docs:
        kind_def = registry.get(doc["kind"])
        out[doc["id"]] = (
            standing(doc["status"], kind_def.archetype, ladder) if kind_def else doc["status"]
        )
    return out


def _covered_by_dispatches(store: Store, record_id: str) -> list[dict]:
    """Dispatches on OTHER records that formally cover this one (KYO-291).

    A dispatch may speak for several records at once (``--also-covers``, KYO-13.c4),
    and everything it produces hangs off the one record it was dispatched against: the
    dispatch row, and the report ingested into it. The covered records carried nothing
    at all — ``kyo show KYO-177`` printed ``dispatches (0)`` and ``reports (0, 0
    superseded)`` while ``KYO-133.d1`` formally covered it, so the record whose criteria
    that report settled could not say where the report was.

    Linking rather than splitting, which was the decision here: a report
    is one stored artifact with one sha, ``kyo verify --from-report`` already settles a
    covered record's criteria from it, and a second copy under a second id would be a
    second account of one dispatch.

    Derived at display time, exactly as :func:`_plan_members` and :func:`_link_titles`
    are, and for the same reason (law 3): ``covers_json`` already holds the relation and
    this walks it backwards. NOTHING is stored, no schema version moves, and a record's
    own ``dispatches`` list is untouched — a dispatch covering this record is not this
    record's dispatch and must never be counted as one.
    """
    out: list[dict] = []
    for row in store.conn.execute(
        "SELECT id, record_id, state, report_id, covers_json FROM dispatches"
        " WHERE covers_json IS NOT NULL ORDER BY seq"
    ):
        if row["record_id"] == record_id:
            # Its own dispatch, which happens to cover others: already under
            # `dispatches`, and listing it twice would make one dispatch read as two.
            continue
        if record_id not in commands.dispatch_covers(row):
            continue
        out.append(
            {
                "id": row["id"],
                "state": row["state"],
                # The record the dispatch hangs off — where the brief, the branch and
                # the report actually live, and the thing this section exists to name.
                "primary_record_id": row["record_id"],
                # NULL until a report is ingested. An explicit null rather than an
                # omitted key, on the same terms as a dispatch's `branch` and
                # `worker_model`: "nothing has reported yet" is a fact a reader needs,
                # and a missing key renders identically to a build without the field.
                "report_id": row["report_id"],
            }
        )
    return out


def _link_titles(store: Store, doc: dict) -> dict[str, str]:
    """id -> title for the far end of every edge on this record (KYO-47.c2).

    Read at display time for the same reason ``_plan_members`` is: a title stored
    beside the edge would stale on rename, and the edge lives on two documents.
    """
    return {
        link["other"]: store.record(link["other"])["title"]
        for link in doc["links"]
    }


def _show_shift_note(store: Store, note_id: str) -> Output:
    """``kyo show PREFIX-SN`` (KYO-94.c1/c2).

    The store could find a shift note by id in order to *destroy* it — `kyo redact` parses
    this exact shape — and had no way to find one in order to *display* it. A note was
    therefore written by id, deleted by id, and read exactly once, by whoever happened to
    run the next `kyo brief`; every later brief stamped the watermark past it and no
    command shipped could reach it again.

    Dispatched on the id's shape rather than reached as a fallback after "no record":
    ids are typed (law 6), so `kyo show TST-S9` against a store with no such note must say
    *no shift note*, never *no record* — the second sentence teaches a reader that this id
    space does not exist.

    This does not make a shift note a record. No criteria, no gates, no amendment path: the
    log is append-only and never amended, which is the property that makes it worth
    reading, and the separate ``PREFIX-SN`` space is how that is kept true.
    """
    row = store.shift_note(note_id)
    if row is None:
        raise NotFound(
            f"no shift note {note_id!r}; shift-note ids are printed by `kyo note --shift` "
            "when it appends one and by `kyo brief` (in the shift log and in the handoff "
            "block, which names the id of the note it hands you)"
        )
    note = dict(row)
    return Output([shift_note_text(note)], {"object": "shift-note", **note})


def _show_dispatch(store: Store, dispatch_id: str) -> Output:
    """``kyo show <dispatch-id>`` (KYO-177).

    A frozen brief is the most re-read object in the dispatch loop — what the worker
    was actually handed, and what a controller returns to in order to check what a
    dispatch declared, re-read a worktree line, or see what prose went out. Nothing
    printed one back: ``kyo show KYO-168.d2`` answered "no record 'KYO-168.d2'", true
    of the record table and useless as an answer, since a dispatch id names an object
    this store holds and can render.

    Dispatched on the id's shape, the same way :func:`_show_shift_note` is, and for
    the same reason: ids are typed (law 6), so an unknown dispatch id must read
    *no dispatch*, never *no record* — the second sentence teaches a reader that this
    id space does not exist. Read-only throughout (no transaction opened here, no row
    written anywhere), so running it twice changes nothing (c1) and it is not, and
    cannot become, a store-writing command (c4) — `kyo show` is not in
    ``cli.MUTATING_COMMANDS`` and this branch adds no write of its own.
    """
    row = store.dispatch(dispatch_id)  # raises NotFound("no dispatch ...") if unknown
    doc = export._dispatch(row)
    doc["record_id"] = row["record_id"]
    # KYO-284: what this dispatch's worker cost, or the named reason that is not
    # known. Summed on demand and without the budget `kyo worktrees` runs under,
    # because this command is one explicit ask about one dispatch and is on nobody's
    # poll — which is what makes it the answer a capped listing row points at.
    # `worker model` below is what the CONTROLLER declared (`kyo dispatch --model`,
    # enforced-by: aspirational); the model inside `worker_tokens` is what the
    # transcript says actually ran, and the two are deliberately not merged.
    spend = worker_tokens.worker_tokens(store, dispatch_id)
    doc["worker_tokens"] = spend.as_json()
    # KYO-592: the repeat stops a stop guard reported without blocking.
    unblocked = stop_audit.unblocked_stops(store, dispatch_id)
    doc["unblocked_stops"] = [
        {"event_id": u.event_id, "at": u.at, "guard": u.guard, "contract": u.contract,
         "finding": u.finding, "omitted": u.omitted}
        for u in unblocked
    ]
    lines = [
        f"dispatch {dispatch_id}  (dispatching {doc['record_id']})",
        echo_value("state", doc["state"]),
        echo_value("worked from sha", doc["worked_from_sha"]),
        echo_value("worktree", doc["worktree"] or "(not declared)"),
        echo_value("branch", doc["branch"] or "(not declared)"),
        echo_value("worker model", doc["worker_model"] or "(not declared)"),
        echo_value(
            "also covers", ", ".join(doc["covers"]) if doc["covers"] else "(none)"
        ),
        echo_value("report", doc["report_id"] or "(none ingested)"),
        echo_value("created", doc["created_at"]),
        echo_value("closed", doc["closed_at"] or "(not closed)"),
        spend.sentence(),
        *stop_audit.unblocked_stop_lines(unblocked),
    ]
    if doc["terminated_why"]:
        lines += echo_block("terminated because", doc["terminated_why"])
    lines += echo_block("brief", doc["brief"])
    return Output(lines, {"object": "dispatch", **doc})


def _show_flag(store: Store, flag_id: str) -> Output:
    """``kyo show <flag-id>`` (KYO-220.c1). See :func:`kyosei.render.flag_text`."""
    flag = export._flag(store.flag(flag_id))  # raises NotFound("no flag ...") if unknown
    flag["record_id"] = ids.record_of_child(flag_id)
    return Output([flag_text(flag)], {"object": "flag", **flag})


def _show_criterion(store: Store, criterion_id: str) -> Output:
    """``kyo show <criterion-id>`` (KYO-220.c2).

    Built through :func:`kyosei.export._criterion`, which is the same function
    ``kyo show <record>`` renders its criteria block from, so the two readings of one
    criterion cannot disagree about its verdict history.
    """
    row = store.criterion(criterion_id)  # raises NotFound("no criterion ...")
    record_id = row["record_id"]
    criterion = export._criterion(store, row)
    return Output(
        [criterion_text(criterion, record_id=record_id)],
        {"object": "criterion", "record_id": record_id, **criterion},
    )


def _untrusted_notice(artifact: dict) -> list[str] | None:
    """The lines ``kyo show`` prints before a recx search artifact, or ``None`` when
    ``artifact`` is not one (KYO-585). One implementation, read by the evidence view and
    by a record's ``--json`` (KYO-587), so the app shows the terminal's own words."""
    if not artifact.get("text") or artifact.get("redacted_at"):
        return None
    document = prior_art.recx_search_document(artifact["text"])
    return None if document is None else prior_art.untrusted_preamble(document)


def _with_untrusted_notice(artifact: dict) -> dict:
    """KYO-587: ``artifact`` with ``untrusted_notice`` added when it is a recx search
    artifact, and unchanged otherwise. Beside it, ``untrusted_text`` is the text as
    ``kyo show <evidence-id>`` prints it, control and format characters escaped, while
    ``text`` stays verbatim because the digest is computed over it. Both are reads, not
    stored fields: neither is in the export document and no schema version moves."""
    notice = _untrusted_notice(artifact)
    if notice is None:
        return artifact
    return {
        **artifact,
        "untrusted_notice": notice,
        "untrusted_text": prior_art.escape_controls(artifact["text"]),
    }


def _show_evidence(store: Store, evidence_id: str) -> Output:
    """``kyo show <evidence-id>`` (KYO-220.c2)."""
    row = store.evidence_artifact(evidence_id)  # raises NotFound("no evidence ...")
    artifact = export._evidence(row)
    # KYO-585: recx's search JSON is recognised by its own top-level
    # `recx_search` and `untrusted` keys, with no schema change. Kyosei's fixed line and
    # recx's notice come before the content, and control characters are escaped.
    preamble = _untrusted_notice(artifact)
    if preamble is None:
        return Output(
            [evidence_text(artifact, record_id=row["record_id"])],
            {"object": "evidence", "record_id": row["record_id"], **artifact},
        )
    shown = {**artifact, "text": prior_art.escape_controls(artifact["text"])}
    return Output(
        [*preamble, "", evidence_text(shown, record_id=row["record_id"])],
        {
            "object": "evidence",
            "record_id": row["record_id"],
            "untrusted_notice": preamble,
            **artifact,
        },
    )


def _show_report(store: Store, report_id: str) -> Output:
    """``kyo show <report-id>`` (KYO-545).

    Once a worker's worktree is pruned its ``.kyosei-report.json`` is gone, and the
    stored body is the only copy -- exactly the copy an amending report starts from.
    ``--json`` carries that body verbatim under ``body``, so taking it, adding
    ``"amends": "<this id>"`` and filing it with ``kyo report <dispatch> --file`` is
    the amendment. Standing and both directions of the amendment chain come from
    :func:`render.report_standings`, the one derivation ``kyo show <record>`` uses.
    """
    record_id = ids.record_of_child(report_id) or ""
    try:
        row = store.report(report_id)
    except NotFound:
        store.record(record_id)  # an unknown record keeps its own `no record` answer
        existing = [r["id"] for r in store.reports(record_id)]
        raise NotFound(
            f"no report {report_id!r}; {record_id} has "
            + (f"these reports: {', '.join(existing)}" if existing else "no reports at all")
        ) from None
    report = export._report(row)
    siblings = [
        export._report(r) for r in store.reports(row["record_id"])
        if r["dispatch_id"] == row["dispatch_id"]
    ]
    dispatch = export._dispatch(store.dispatch(row["dispatch_id"]))
    chain = report_standings({"dispatches": [dispatch], "reports": siblings})[report_id]
    lines = [
        f"report {report_id}  (dispatch {report['dispatch_id']} on {row['record_id']})",
        result_value("standing", chain["standing"]),
        result_value("ingested", report["created_at"]),
        result_value("author", report["author"]),
        result_value("worked from sha", report["worked_from_sha"]),
        result_value("amends", chain["amends"] or "(nothing -- a first report)"),
        result_value("amended by", chain["amended_by"] or "(nothing amends it)"),
        "body:",
        json.dumps(report["body"], indent=2, ensure_ascii=False),
    ]
    return Output(
        lines,
        {
            "object": "report",
            "record_id": row["record_id"],
            **report,
            "standing": chain["standing"],
            "amends": chain["amends"],
            "amended_by": chain["amended_by"],
        },
    )


#: Every id shape ``kyo show`` accepts, in the order its help text and its refusal name
#: them: the spelling, and what it names. One table, read by three surfaces — the
#: dispatch below, the refusal beside it, and `kyo show --help` (and therefore
#: docs/reference/cli.md) — because the defect KYO-220 reports is precisely that those
#: three had drifted: the help named three shapes, the command answered "no record" for
#: everything else, and the id shapes the tool mints did not tell you which were which.
SHOWABLE_SHAPES: tuple[tuple[str, str], ...] = (
    ("PREFIX-N", "a record"),
    ("PREFIX-N.cN", "a criterion, with its verdict history"),
    ("PREFIX-N.eN", "an evidence artifact"),
    ("PREFIX-N.fN", "a sweep flag"),
    ("PREFIX-N.dN", "a dispatch, printing its frozen brief back"),
    ("PREFIX-N.rN", "a stored report, its amendment chain and its body"),
    ("PREFIX-SN", "a shift note"),
)

SHOWABLE_SHAPES_HELP = "; ".join(f"{shape} — {what}" for shape, what in SHOWABLE_SHAPES)


def _refuse_shape(identifier: str, child_kind: str | None) -> UsageError:
    """KYO-220.c3: refuse an id as a SHAPE, naming the shape it is.

    ``kyo show KYO-215.f2`` answered ``no record 'KYO-215.f2'`` — true of the record
    table and false as an answer, because that id names an object this store holds. The
    consumer's own summary of the resulting confusion is the criterion's wording: "the
    id shapes do not tell you what is showable". A refusal that names the shape it was
    handed teaches that in one line, which "no record" cannot, and the same refusal now
    covers the shapes that genuinely have no reader (a note, a touch, a link, a sweep
    run) rather than letting them fall through a message about records.
    """
    if child_kind is not None:
        was = f"{identifier!r} is a {child_kind} id"
    elif ids.LINK_RE.match(identifier):
        was = f"{identifier!r} is a link id"
    elif ids.SWEEP_RUN_RE.match(identifier):
        was = f"{identifier!r} is a sweep-run id"
    elif ids.BRIEF_RUN_RE.match(identifier):
        was = f"{identifier!r} is a brief-run id"
    else:
        was = f"{identifier!r} is not an id shape this project mints"
    return UsageError(
        f"`kyo show` cannot show that: {was}. It accepts {SHOWABLE_SHAPES_HELP}"
    )


#: KYO-420.c3: the keys ``kyo show --fields`` always keeps — the envelope's own, so a
#: narrowed document is still recognisably one ``kyo show`` answer.
SHOW_FIELDS_ENVELOPE = ("ok", "command")


def cmd_show(ctx: Context, args: Namespace) -> Output:
    fields = getattr(args, "fields", None)
    if fields is not None and not getattr(args, "json", False):
        # Law 6: a flag that quietly did nothing in text mode would teach a caller it
        # had been honoured.
        raise UsageError(
            "`kyo show --fields` narrows the --json document and has nothing to narrow "
            "in text mode; add --json: `kyo show <id> --json --fields status,title`"
        )
    output = _show(ctx, args)
    if fields is None:
        return output
    return Output(output.lines, _only_fields(output.data, fields))


def _only_fields(data: dict, spec: str) -> dict:
    """``data`` narrowed to the comma-separated top-level keys in ``spec``, plus
    :data:`SHOW_FIELDS_ENVELOPE`. An unknown or empty name is refused naming every key
    this object has, rather than answered with an object missing it."""
    wanted = [name.strip() for name in spec.split(",")]
    available = sorted(key for key in data if key not in SHOW_FIELDS_ENVELOPE)
    unknown = [name for name in wanted if name not in data or name in SHOW_FIELDS_ENVELOPE]
    if not any(wanted) or unknown:
        named = ", ".join(repr(name) for name in unknown if name) or "no field at all"
        raise UsageError(
            f"--fields was given {named}, which this object does not have. "
            f"Valid fields: {', '.join(available)}"
        )
    # A record's document is the record spread at the top level and carries no
    # envelope keys of its own (a dispatch's or a flag's does); the narrowed answer
    # always has both, so every `--fields` reply reads the same way.
    narrowed = {"ok": data.get("ok", True), "command": data.get("command", "show")}
    narrowed.update({name: data[name] for name in wanted})
    return narrowed


def _show(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    if ids.SHIFT_NOTE_RE.match(args.id):
        return _show_shift_note(store, args.id)
    child_kind = ids.child_kind(args.id)
    if child_kind == "dispatch":
        return _show_dispatch(store, args.id)
    # KYO-220.c1/c2: three more child shapes the tool mints and could not read back.
    if child_kind == "flag":
        return _show_flag(store, args.id)
    if child_kind == "criterion":
        return _show_criterion(store, args.id)
    if child_kind == "evidence":
        return _show_evidence(store, args.id)
    if child_kind == "report":
        return _show_report(store, args.id)
    if not ids.is_record_id(args.id):
        # Everything left is a shape this command does not accept, INCLUDING the child
        # shapes above that have no reader. A record id that names nothing still falls
        # through to `no record ...` below: that id has the right shape and the wrong
        # value, which is a different answer and stays a different answer (law 6).
        raise _refuse_shape(args.id, child_kind)
    doc = export.record_document(store, args.id)
    members = _plan_members(store, args.id)
    standings = _standings(store, [doc, *members])
    covered_by = _covered_by_dispatches(store, args.id)
    carried = _carried_standing(store, doc)
    # KYO-225.c3: the new-file touches whose file is not in this checkout yet. Asked
    # here, where the project root is, so `kyo show` stops annotating a touch the moment
    # the work it declared has landed — without anyone editing the stored touch.
    pending_touches = {
        touch["path"]
        for touch in doc["touches"]
        if is_new_file_touch(touch["path"])
        and not (ctx.code_root / touch_target(touch["path"])).exists()
    }
    return Output(
        [
            show_text(
                doc,
                members=members,
                standings=standings,
                link_titles=_link_titles(store, doc),
                expand_obligations=bool(getattr(args, "expand_obligations", False)),
                pending_touches=pending_touches,
                covered_by=covered_by,
                carried=carried,
            )
        ],
        # `standing` rides beside `status`, never instead of it: a JSON consumer
        # must be able to see both the stored fact and how it reads (KYO-44.c2).
        #
        # KYO-219.c3: `docs_obligation_groups` rides beside `docs_obligations` on the
        # same terms and for the same reason. It is ADDITIVE — every obligation is
        # still carried individually under the key it has always been under (c2), and
        # a consumer that has never heard of the grouping reads exactly what it read
        # before. What it buys is that the desktop viewer, which renders this same
        # document, collapses the way the terminal collapses because it is reading the
        # terminal's own answer rather than a second implementation of the rule.
        {
            **doc,
            # KYO-280: each member carries its own standing, on the same terms as the
            # record's -- `_standings` was already computed over the members for the
            # text renderer's distribution line, and the JSON envelope dropped it, so a
            # viewer drawing a member's standing pill had nothing to draw.
            "members": [{**m, "standing": standings.get(m["id"], "")} for m in members],
            "standing": standings[doc["id"]],
            # KYO-291, and additive on the same terms as `standing` and
            # `docs_obligation_groups` above: the dispatches on OTHER records that
            # formally cover this one. Named apart from `dispatches` deliberately —
            # `dispatches` is and stays this record's own, and a consumer summing the
            # two would be double-counting one dispatch. Always present, `[]` when
            # nothing covers this record, because a JSON consumer cannot tell an absent
            # key from a build that never had one. This is a READ of `covers_json`: it
            # is not in the export document, no schema version moves for it, and
            # `kyo rebuild` has nothing new to reconstruct.
            "covered_by_dispatches": covered_by,
            # KYO-587: a recx search artifact carries the lines
            # `kyo show <evidence-id>` prints before it, so the desktop app shows the
            # same words without a second recognition rule. Every other artifact is
            # exactly what the export document holds.
            "evidence_artifacts": [_with_untrusted_notice(a) for a in doc["evidence_artifacts"]],
            "docs_obligation_groups": docs_obligations.collapse_obligations(
                doc["docs_obligations"]
            ),
            # KYO-497: present only on a record of a `policy.carried` kind, so every
            # other record's --json is byte-for-byte what it was.
            **({"carried": carried} if carried is not None else {}),
        },
    )


def _carried_standing(store: Store, doc: dict) -> dict | None:
    """KYO-497: how long a carried record has been carried, for `kyo show`.

    ``None`` for a record whose kind is not ``policy.carried`` (or is not in the
    registry at all). ``age_days`` is whole days from the record's tool-stamped
    ``created_at`` to now, by the same :func:`clock.elapsed_days` the brief's carried
    section uses, and is ``None`` once the record has settled: a settled item is no
    longer being carried, so it has no age to report.
    """
    kind_def = _load_registry(store).get(doc["kind"])
    if kind_def is None or not kind_def.carried:
        return None
    settled = _load_status_ladder(store).is_settled(doc["status"])
    # KYO-498: the basis is the named shift note's tool-stamped `created_at` when
    # the record names one, and the record's own otherwise -- the brief's rule, read here
    # from the same two stored instants. A named note this store does not hold falls
    # back to the record's `created_at` (and `since_note` still says what was named).
    since = doc["created_at"]
    since_note = doc.get("since_note")
    if since_note:
        note = store.shift_note(since_note)
        if note is not None:
            since = note["created_at"]
    return {
        "since": since,
        "since_note": since_note,
        "age_days": None if settled else elapsed_days(since, now()),
    }


def _record_line(item: dict) -> str:
    """One record, as ``kyo list`` prints it and as a Base's default columns render it.

    Named once because two surfaces print it (law 3), and it is the same row shape out
    of the same engine on both.
    """
    return (
        f"{item['id']:<10} {item['kind']:<14} {item['standing']:<9} "
        f"gates:{item['gates_open']:<2} {(item['scope'] or '-'):<12} {item['title']}"
        + (f"  [{', '.join(item['gate_tags'])}]" if item["gate_tags"] else "")
    )


def cmd_list(ctx: Context, args: Namespace) -> Output:
    """One line per record; a summary split by the registry's ``counted`` policy.

    ``counted``'s first consumer (law 2, kyosei/kinds.yml's policy table): the queue
    summary must separate kinds a project wants weighed in "how much work is open"
    from kinds it does not (e.g. ``plan``, ``decision``).

    KYO-108: the query itself is :mod:`kyosei.query`, shared verbatim with ``kyo base``.
    What is left here is the flags-to-:class:`kyosei.query.Filters` step and the
    rendering. Every refusal, every filter and the row shape moved into the engine so
    that a saved Base cannot become a second query path that drifts from this one —
    the observation that ``kyo list --json`` is already most of the engine, made
    structural.
    """
    result = query.validated_run(
        ctx,
        query.Filters.from_args(args),
        unsettled=bool(getattr(args, "unsettled", False)),
    )

    # KYO-420.c4: `--limit N` prints the first N matching rows. The counts in the
    # summary stay the counts of EVERY match, and the line says that it was limited
    # and how many it shows, so a truncated listing never reads as a short one.
    limit = getattr(args, "limit", None)
    shown = result.rows if limit is None else result.rows[:limit]
    lines = [_record_line(item) for item in shown]
    summary_line = (
        f"{len(result.rows)} record(s): {result.counted} counted, "
        f"{result.not_counted} not counted (kyosei/kinds.yml policy.counted)"
    )
    if limit is not None:
        summary_line += (
            f"; limited to {limit} by --limit: showing {len(shown)} of {len(result.rows)}"
        )
    lines.append(summary_line)
    # KYO-74.c1. The count above is the surface the defect lands on: a record that
    # exists in kyosei/export/ and not in the database is simply absent from it, and
    # nothing distinguishes "one record" from "one record and one I cannot see". Law
    # 6 — a success that does not say what succeeded. The warning goes *after* the
    # count deliberately: it is the last thing on the terminal, next to the number it
    # contradicts, rather than scrolled off the top by the listing.
    lines += currency.read_surface_warning_from(result.currency)
    summary = result.summary
    if limit is not None:
        # Additive, and only under --limit: a default `--json` is byte-identical.
        summary = {
            **summary,
            "limit": limit,
            "shown": len(shown),
            "limited": len(shown) < len(result.rows),
        }
    data = {
        "ok": True,
        "command": "list",
        "records": shown,
        "summary": summary,
        # Beside the human lines, never instead of them: a JSON consumer reading
        # `summary.total` is making exactly the mistake the warning exists to prevent.
        "db_export_currency": currency.as_data(result.currency),
    }
    return Output(lines, data)


def cmd_base(ctx: Context, args: Namespace) -> Output:
    """``kyo base list`` / ``kyo base run NAME [--group-by FIELD]`` — the saved views
    (KYO-108, KYO-120).

    Read-only in both actions and by construction: nothing here opens a transaction,
    and running a Base is :mod:`kyosei.query` plus arrangement. A Base is *config*, not
    store data — no record field is added and no schema version moves (KYO-108.c5) —
    which is why a definition is validated against the step-1 registries when the file
    is read rather than checked against anything stored.
    """
    if args.action == "list":
        if getattr(args, "group_by", None) is not None:
            # Law 6: a flag that quietly did nothing here would teach a caller that it
            # had been honoured. `list` renders declarations; there is no run to
            # rearrange.
            raise UsageError(
                "`kyo base list` renders what kyosei/bases.yml declares and runs "
                "nothing, so it has no grouping to override. `--group-by` belongs on a "
                "run: `kyo base run <name> --group-by <field>`"
            )
        return _cmd_base_list(ctx)
    if args.action == "run":
        return _cmd_base_run(ctx, args)
    # pragma: no cover - argparse's choices=[...] already refuses anything else.
    raise UsageError(f"unknown base action {args.action!r}")


def _cmd_base_list(ctx: Context) -> Output:
    """Every saved Base, with the file they were read from.

    Names the path whether or not the file exists, and says what a declaration looks
    like when it does not: law 6 — "(none)" with no way to get from there to one is a
    dead end. Validation runs here too, so a broken ``bases.yml`` is loud on the
    command that runs nothing, not only on the one that runs something.
    """
    store = ctx.store()
    registry = bases.load_base_registry(store)
    definitions = [
        bases.Base.from_entry(entry).as_definition() for entry in registry.entries.values()
    ]
    lines = [f"{len(definitions)} saved base(s) in {registry.path}:"]
    for definition in definitions:
        lines.append(
            f"  {definition['name']:<20} {definition['source']:<8} {definition['summary']}"
        )
        applied = definition["filters"]
        if applied:
            lines.append(
                "    filters: " + ", ".join(f"{key}={value!r}" for key, value in applied.items())
            )
    if not definitions:
        lines.append(
            f"  (none) — declare one with a top-level `{bases.BASE_PLURAL}:` mapping in "
            f"{registry.path}; `kyo base run <name>` then runs it"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "base list",
            "path": str(registry.path),
            "exists": registry.exists,
            "bases": definitions,
        },
    )


def _cmd_base_run(ctx: Context, args: Namespace) -> Output:
    """One saved Base, run read-only over its source.

    ``--group-by FIELD`` replaces the file's declared binding for this run and reaches
    nothing else (KYO-120): the same rows, the same columns, the same order, arranged
    into different groups. It is refused by :func:`kyosei.bases.group_by_problem`
    before the query runs, so an unknown or multi-valued field renders nothing.
    """
    if not args.name:
        raise UsageError(
            "`kyo base run` needs the name of a saved base; `kyo base list` shows them"
        )
    store = ctx.store()
    registry = bases.load_base_registry(store)
    # `require` is the shared registry refusal (law 3): an unknown base name is refused
    # with the same sentence an unknown scope or gate tag gets, naming the file and what
    # it does declare.
    base = bases.Base.from_entry(registry.require(args.name))
    override = getattr(args, "group_by", None)
    run = bases.run_base(store, base, override)

    # The field the run ACTUALLY grouped by, read back out of the document rather than
    # off the definition (KYO-120). The terminal and `--json` are two renderings of one
    # run: a text surface printing the file's declared key over rows the flag had
    # regrouped would be the same lie in a different font.
    applied_group_by = run.data["applied"][bases.GROUP_BY_FIELD]

    lines = [f"base {base.name!r} over {base.source}: {base.summary}"]
    groups = run.data["groups"]
    if groups is None:
        lines += [_base_row_line(row, base.columns) for row in run.data["rows"]]
    else:
        by_id = {row["id"]: row for row in run.data["rows"]}
        for group in groups:
            lines.append(f"  {applied_group_by} = {group['value']!r} ({group['count']})")
            lines += [_base_row_line(by_id[rid], base.columns) for rid in group["ids"]]
    grouped_by = f", grouped by {applied_group_by}" if applied_group_by else ""
    if override is not None:
        # Naming the displaced key rather than only the applied one: the reader is
        # looking at an arrangement the reviewable file does not describe, and the line
        # that states how these rows are arranged is where that belongs.
        grouped_by += (
            f" (--group-by, overriding the base's {base.group_by})"
            if base.group_by
            else " (--group-by; the base declares none)"
        )
    lines.append(
        f"{run.data['summary']['total']} row(s), sorted by {', '.join(base.sort)}" + grouped_by
    )
    # KYO-74: the same warning `kyo list` prints, from the same comparison, because a
    # Base is the same read of the same store, and a rendering that could only paint
    # "stale" and "not stale" would say less than `kyo list` does.
    lines += currency.read_surface_warning_from(run.currency)
    return Output(lines, {"ok": True, "command": "base run", **run.data})


def _base_row_line(row: dict, columns: tuple[str, ...]) -> str:
    """One row, rendered through the Base's declared columns.

    A records Base whose ``columns`` are the engine's full field set prints exactly what
    ``kyo list`` prints — the same row, through the same function — which is the
    visible half of "one engine, two surfaces". Every other combination, on every
    source, is the generic projection below: :func:`kyosei.query.field_value` is the one
    rule a column name is resolved by, so ``properties.author`` prints here the same way
    it sorts and groups (KYO-109).
    """
    if tuple(columns) == query.ROW_FIELDS:
        return _record_line(row)
    return "  " + "  ".join(_cell(query.field_value(row, column)) for column in columns)


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict):
        # A file source's whole `properties` mapping as one cell. Shown as the file
        # states it — `key=value`, in file order — because the reason to print the
        # mapping rather than one key out of it is to see what the file actually has.
        return ", ".join(f"{key}={_cell(item)}" for key, item in value.items()) or "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "-"
    return str(value)


def cmd_map(ctx: Context, args: Namespace) -> Output:
    """Render the kind registry as a graph: kinds + ``intake``/``idea`` pseudo-nodes,
    promotion edges from ``accepts_from``/``produces``. ``--json`` is the contract a
    future viewer consumes (charter: "the viewer renders it as a circles-and-arrows
    kind map").
    """
    store = ctx.store()
    registry = _load_registry(store)
    nodes, edges = kinds.registry_graph(registry)

    data = {"ok": True, "command": "map", "nodes": nodes, "edges": edges}
    return Output([map_text(nodes, edges)], data)


def cmd_reference(ctx: Context, args: Namespace) -> Output:
    """``kyo reference [--output FILE]`` — the command reference, generated (KYO-138).

    A reference page enumerating every command and option was missing outside the
    ``/kyosei`` skill. Established CLIs do not *write* that page by hand — Cobra emits
    ``GenMarkdownTree``, Python projects run ``sphinx-argparse``,
    Click has ``sphinx-click`` — and this command is that answer here.

    Why generation rather than the governance machinery KYO-137 built: detection leaves
    a window in which the reference is wrong and somebody is reading it. Generation
    closes the window, and — the property that actually matters — it **cannot omit**. A
    hand-written reference can only contain what its author remembered; this one
    contains what the parser has. KYO-131 is the measured case: ``kyo edit --note-file``
    was the sole writer of ``RECORD.nN`` notes, no prose surface named it, and a
    consumer concluded from ``kyo redact --help`` that the id shape targeted an object
    nothing could produce. The flag appears in this output whether or not anyone
    remembers it exists.

    Three properties, each load-bearing:

    * **``PROJECT_NONE``.** It describes the installed tool, not a store. The readers who
      need it most are a worker in a dispatch worktree and a consumer who has just
      installed the thing — neither reliably has a project, and refusing them would be
      the same wall KYO-149 removed from ``kyo report-schema``.
    * **stdout by default, ``--output`` to write.** Nothing is copied into a consumer's
      tree by ``kyo init``: a copy is a second thing to update, and a stale reference is
      worse than no reference because it looks authoritative. This project's own
      committed copy exists only because a test (KYO-138.c4) makes staleness a build
      failure rather than a discovery.
    * **The render is not here.** :func:`kyosei.cli.reference_markdown` walks the parser;
      this function chooses where the bytes go. The import is deferred to call time
      because ``cli`` imports this module at load time — the cycle is real and the lazy
      import is the seam, the same way a command handler is named in ``COMMAND_TABLE``
      rather than registering itself.

    ``--json`` carries the same markdown as one field alongside where it went, so
    ``kyo reference --json`` is usable by a caller that wants the text and the path
    without parsing human output.

    KYO-221 adds the optional positional: one command's section, sliced out of the same
    generation by :func:`kyosei.cli.reference_markdown`. The slicing is there and not
    here for the reason the third property above already gives — this function chooses
    where the bytes go, and the parser walk is the other module's.
    """
    from .cli import build_parser, reference_markdown

    command = getattr(args, "of_command", None)
    if command and args.output:
        # KYO-221.c2. `--output` writes the WHOLE reference, and a generated file is
        # written by replacement: `kyo reference show --output docs/reference/cli.md`
        # would leave a file that looks like the reference and holds one command, with
        # nothing saying so until somebody went looking for a section that used to be
        # there. Refused rather than quietly narrowed, and the refusal names the two
        # things the caller might have meant.
        raise UsageError(
            f"`--output` writes the whole reference, and `kyo reference {command}` "
            f"prints one section to read — writing one section over a file that holds "
            f"the whole document would silently lose the rest. Run `kyo reference "
            f"{command}` without --output to read that section, or `kyo reference "
            f"--output {args.output}` to write the whole reference"
        )
    text = reference_markdown(build_parser(), command=command)

    if not args.output:
        return Output(
            text.splitlines(),
            {
                "ok": True,
                "command": "reference",
                "output": None,
                # KYO-221: which section this is, or `null` for the whole document —
                # so a consumer reading `markdown` can tell a slice from the reference.
                "of_command": command,
                "markdown": text,
            },
        )

    destination = Path(args.output).expanduser()
    if destination.parent and not destination.parent.exists():
        raise UsageError(
            f"cannot write the reference to {destination}: its directory "
            f"{destination.parent} does not exist. Create it, or drop --output to print "
            f"the reference to stdout"
        )
    destination.write_text(text, encoding="utf-8")
    return Output(
        [
            f"wrote {len(text.splitlines())} line(s) of generated command reference to "
            f"{destination}",
            # KYO-156: echo the caller's OWN destination. This used to print
            # `REFERENCE_REGENERATE_COMMAND` — Kyosei's `--output docs/reference/cli.md`
            # — to a consumer who had just written somewhere else entirely, so the one
            # remedy the message named was the one path they could not use. The remedy a
            # message names has to be one that works for its reader (law 1), and here the
            # reader's own argument is the remedy.
            f"regenerate with `kyo reference --output {destination}` after any change to "
            f"the parser; do not edit the file by hand",
        ],
        {
            "ok": True,
            "command": "reference",
            "output": str(destination),
            "of_command": None,
            "markdown": text,
        },
    )



# --------------------------------------------------------------------------- KYO-226
#
# Consumers had no way to learn what changed between builds: a consumer reported a
# defect that had already been fixed in the build they were running. Nothing on either
# side knew which build was installed, and nothing could say what a newer one would
# contain.
#
# Both commands are `PROJECT_NONE` on `kyo reference`'s reasoning exactly: they answer a
# question about the *tool*, their reader is a worker in a dispatch worktree or a
# consumer who has just installed it, and searching upward for a project could only find
# something irrelevant or fail. Neither is in `MUTATING_COMMANDS` — they write no record,
# no event and no export, and `--output` writes a file in the filesystem.
#
# The derivations are not here, for `cmd_reference`'s reason: `kyosei.buildinfo` knows
# what the build is and `kyosei.changelog` knows what the store says; these two functions
# choose where the bytes go.


def cmd_version(ctx: Context, args: Namespace) -> Output:
    """``kyo version`` — which build this copy is (KYO-226.c1).

    One line, and the same line `kyo brief` opens a session with, so a consumer reading
    a brief and a consumer running this command are never reconciling two sentences.
    The supporting lines below it exist because the line alone answers *what* and this
    command's readers also need *what next*: which kind of install this copy is
    (KYO-317.c1 — a uv tool, a working tree, or "cannot tell"), the changelog since
    this build, and the reinstall that moves it —
    named for THAT kind rather than the one uv-only sentence every install used to read,
    whichever one was actually running.
    """
    from . import buildinfo

    identity = buildinfo.build_identity()
    lines = [buildinfo.version_line(identity)]
    lines.append(f"  installed as: {buildinfo.INSTALL_KIND_LABELS[identity.kind]}")
    if identity.known:
        lines.append(
            f"  what changed since it: `kyo changelog` (defaults to --since "
            f"{identity.commit_date})"
        )
        route = buildinfo.upgrade_route(identity.kind, identity.release)
        lines.append(f"  to move to a newer build: {route}")
        # KYO-318.n1: its own sentence, for two reasons that arrived together. The
        # retired bundled kind's clause ended "...has no separate uv install to move",
        # which ran straight into ", then `kyo install-hooks`" and read as one instruction. And
        # the per-project step is not always those three commands: in a checkout that
        # is itself the SOURCE of the packaged assets, two of them write this build's
        # older copies over the newer source and now refuse to (KYO-318.c1) — so the
        # line that names them has to ask the same question the installers do.
        lines.append(
            f"  then, in each project: "
            f"{buildinfo.per_project_upgrade_step(ctx.code_root)}"
        )
    return Output(lines, {"ok": True, "command": "version", **identity.as_data()})


def cmd_changelog(ctx: Context, args: Namespace) -> Output:
    """``kyo changelog [--since X | --all] [--output FILE]`` — what changed (KYO-226.c3).

    The default window is this build's own snapshot date, which is the question a
    consumer actually has after a reinstall: *what does the build I just installed have
    that the one I was running did not*. `--all` is the escape hatch for the whole
    history, and `--since` takes either a date or a commit this checkout can resolve.

    A changelog with no window would be the wrong default in the one direction that
    matters: a thousand entries is not read, and the entry the reader needed is in it.
    """
    from . import buildinfo
    from . import changelog as changelog_module

    if args.all and args.since:
        raise UsageError(
            "`--all` and `--since` ask for different windows — `--all` prints every "
            "completed record and `--since` prints one slice of them. Drop one"
        )

    loaded = changelog_module.load()
    identity = buildinfo.build_identity()
    if args.all:
        since = None
    elif args.since:
        since = changelog_module.resolve_since(args.since)
    else:
        # KYO-226.c3's default. `None` rather than a guess when the build cannot say
        # which snapshot it is: a window computed from an unknown date would be a
        # filter nobody could account for, so the whole document is printed and
        # `kyo version` says why it could not be narrowed.
        since = identity.commit_date

    shown = changelog_module.since_entries(loaded.entries, since)
    data = {
        "ok": True,
        "command": "changelog",
        "since": since,
        "source": loaded.source,
        "unavailable": loaded.unavailable,
        "total": len(loaded.entries),
        "shown": len(shown),
        "output": None,
        "entries": [entry.as_data() for entry in shown],
    }

    if not args.output:
        return Output(changelog_module.text_lines(loaded, shown, since), data)

    text = changelog_module.markdown(loaded, shown, since)
    destination = Path(args.output).expanduser()
    if destination.parent and not destination.parent.exists():
        raise UsageError(
            f"cannot write the changelog to {destination}: its directory "
            f"{destination.parent} does not exist. Create it, or drop --output to print "
            f"the changelog to stdout"
        )
    destination.write_text(text, encoding="utf-8")
    data["output"] = str(destination)
    data["markdown"] = text
    # KYO-372.c2: which data this came from, stated beside the write -- `--output`'s
    # only other line is "wrote N line(s)", which says nothing a reader could use to
    # tell a live-store answer from a packaged one.
    where = (
        "generated from this checkout's store"
        if loaded.source == changelog_module.SOURCE_STORE
        else "read from the data file packaged with this build"
    )
    lines = [
        f"wrote {len(text.splitlines())} line(s) of generated changelog to "
        f"{destination} ({where})",
    ]
    hint = changelog_module.live_store_hint(loaded, destination)
    if hint:
        lines.append(f"NOTE: {hint}")
    else:
        lines.append(
            # KYO-156's rule: the remedy a message names is the reader's own path, not
            # Kyosei's. A consumer who wrote somewhere else is told how to refresh the
            # file they actually have.
            f"regenerate with `kyo changelog --all --output {destination}` after any "
            f"record is completed; do not edit the file by hand",
        )
    return Output(lines, data)


# ------------------------------------------------- KYO-454: the ledgers a viewer reads
#
# `kyo brief` writes a `brief_runs` row on every run, so the one command whose subject is
# briefs was a write, and the desktop app read the ledgers below by SQL instead
# (`apps/desktop/src/main/store.ts`). These three commands are those reads, made the
# CLI's: each is a projection `kyosei.query` makes in one SELECT, opens no transaction and
# writes no row, and `kyo serve` serves each (serve.SERVED_COMMANDS).


def _one_line(text: str | None) -> str:
    """Prose flattened onto one listing line; the ``--json`` document carries it whole."""
    return " ".join((text or "").split())


def _brief_run_line(run: dict) -> str:
    session = {True: "  [session start]", False: "", None: "  [session start: not recorded]"}
    return (
        f"  {run['id']}  {run['at']}  {run['actor']}{session[run['session_start']]}  "
        f"(through event {run['last_event_seq']}, shift note {run['last_shift_note_seq']})"
    )


def _brief_window(store: Store, runs: list[dict], index: int) -> dict:
    """``runs[index]`` with the previous brief and what lies between the two.

    ``runs`` is newest first, so the brief BEFORE this one is the next entry. The window
    is the one `kyo brief` itself surfaces: the shift notes and ledger entries after the
    previous brief's positions and up to this one's.
    """
    run = runs[index]
    previous = runs[index + 1] if index + 1 < len(runs) else None
    return {
        "run": run,
        "previous_brief_id": previous["id"] if previous else None,
        "shift_notes": query.shift_notes_between(
            store, previous["last_shift_note_seq"] if previous else 0, run["last_shift_note_seq"]
        ),
        "events": query.events_between(
            store, previous["last_event_seq"] if previous else 0, run["last_event_seq"]
        ),
    }


def cmd_briefs(ctx: Context, args: Namespace) -> Output:
    """``kyo briefs [BRIEF_ID | --latest]`` (KYO-454): the session briefs the store kept.

    Read-only, unlike `kyo brief`, which derives a new briefing and records a run. With
    no argument, every run newest first. With a brief id, or ``--latest``, that one run,
    the id of the brief before it, and what lies between the two — the shift notes and
    the ledger entries it closed over. Nothing regenerates a past brief's prose: the store
    never kept it.
    """
    store = ctx.store()
    runs = query.brief_runs(store)
    brief_id = getattr(args, "brief_id", None)
    latest = bool(getattr(args, "latest", False))
    if brief_id and latest:
        raise UsageError("name a brief id or pass --latest, not both")
    if not brief_id and not latest:
        lines = [f"{len(runs)} session brief(s), newest first"]
        lines += [_brief_run_line(run) for run in runs]
        if not runs:
            lines.append("  (none yet; `kyo brief` writes the first)")
        return Output(lines, {"ok": True, "command": "briefs", "briefs": runs})
    if latest:
        if not runs:
            return Output(
                ["this store holds no session brief yet; `kyo brief` writes the first"],
                {"ok": True, "command": "briefs", "run": None, "previous_brief_id": None,
                 "shift_notes": [], "events": []},
            )
        index = 0
    else:
        index = next((i for i, run in enumerate(runs) if run["id"] == brief_id), -1)
        if index < 0:
            raise NotFound(
                f"no session brief {brief_id!r}; `kyo briefs` lists the ones this store holds"
            )
    window = _brief_window(store, runs, index)
    run = window["run"]
    lines = [
        f"brief {run['id']}  (previous: {window['previous_brief_id'] or 'none'})",
        _brief_run_line(run),
        f"shift notes since the previous brief ({len(window['shift_notes'])}):",
    ]
    for note in window["shift_notes"]:
        text = f"[redacted: {note['redacted_why']}]" if note["redacted_at"] else note["text"]
        lines.append(f"  {note['id']}  {note['created_at']}  {note['author']}: {_one_line(text)}")
    lines.append(f"ledger entries in that window ({len(window['events'])}):")
    for event in window["events"]:
        lines.append(
            f"  {event['id']}  {event['at']}  {event['actor']}  {event['command']}"
            + (f"  {event['record_id']}" if event["record_id"] else "")
        )
    return Output(lines, {"ok": True, "command": "briefs", **window})


def cmd_shift_notes(ctx: Context, args: Namespace) -> Output:
    """``kyo shift-notes [--latest]`` (KYO-454): the shift log, newest first.

    Every note, with ``handoff_for``: the id of the earliest session-start brief that
    stood at this note (whose ``last_shift_note_seq`` names it), or ``None``. ``--latest``
    is the newest note alone — including one written after the last brief, which no
    brief's window holds. `kyo show <shift-note-id>` prints one by id.
    """
    store = ctx.store()
    notes = query.shift_notes_between(store, 0, None)
    handoffs: dict[int, str] = {}
    for run in query.brief_runs(store):  # newest first: the earliest session start wins
        if run["session_start"] and run["last_shift_note_seq"] > 0:
            handoffs[run["last_shift_note_seq"]] = run["id"]
    for note in notes:
        note["handoff_for"] = handoffs.get(note["seq"])
    if getattr(args, "latest", False):
        note = notes[0] if notes else None
        lines = [shift_note_text(note)] if note else ["this store holds no shift note yet"]
        return Output(lines, {"ok": True, "command": "shift-notes", "note": note})
    lines = [f"{len(notes)} shift note(s), newest first"]
    for note in notes:
        text = f"[redacted: {note['redacted_why']}]" if note["redacted_at"] else note["text"]
        handoff = f"  [handoff to {note['handoff_for']}]" if note["handoff_for"] else ""
        lines.append(
            f"  {note['id']}  {note['created_at']}  {note['author']}{handoff}: "
            f"{_one_line(text)}"
        )
    return Output(lines, {"ok": True, "command": "shift-notes", "notes": notes})


def cmd_gates(ctx: Context, args: Namespace) -> Output:
    """``kyo gates [--type TYPE ...]`` (KYO-454): every open gate, across every record.

    One row per gate not yet cleared, with the record it is on (title, kind, status), in
    record then gate order; ``--type`` narrows to those gate types. ``open_counts`` is how
    many gates each record has open, of every type, whatever ``--type`` says — the number
    a row that shows only a count wants. `kyo gate` adds one; `kyo ungate` clears one.
    """
    store = ctx.store()
    types = tuple(dict.fromkeys(getattr(args, "type", None) or ())) or None
    gates = query.open_gates(store, types)
    counts = query.open_gate_counts(store)
    lines = [
        f"{len(gates)} open gate(s)"
        + (f" of type {', '.join(types)}" if types else "")
        + f" on {len({gate['record_id'] for gate in gates})} record(s)"
    ]
    for gate in gates:
        tags = f" [{', '.join(gate['tags'])}]" if gate["tags"] else ""
        detail = f": {_one_line(gate['do'])[:120]}" if gate["do"] else ""
        lines.append(
            f"  {gate['id']}  {gate['type']}{tags}{detail}  — {gate['record_id']} "
            f"({gate['record_status']}) {_one_line(gate['record_title'])[:100]}"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "gates",
            "types": list(types) if types else None,
            "count": len(gates),
            "gates": gates,
            "open_counts": counts,
        },
    )
