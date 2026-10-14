"""``kyo worktrees`` — dispatch worktree housekeeping: listing, judging and pruning."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from . import config as config_module
from .commands import Context, Output
from .contracts import (
    DISPATCH_STATE_REPORTED,
    DISPATCH_STATE_RUNNING,
    DISPATCH_STATE_TERMINATED_NO_REPORT,
    DISPATCH_STATE_WITHDRAWN,
)
from .git_queries import (
    branch_is_contained_in,
    registered_linked_worktrees,
    worktree_admin_entries,
    worktree_remove,
)
from .render import echo_value
from .stop_audit import StopAuditLedger, stop_audits
from .stop_target import STOP_REASON_LABELS
from .worker_tokens import dispatch_end, report_ingest_times, worker_tokens_index

# --------------------------------------------------- KYO-70: worktree housekeeping

#: KYO-70.c3, law 6: what `kyo worktrees` found about one dispatch's directory, named
#: once so the listing, the prune decision and ``--json`` are three renderings of one
#: judgment. ``DIRTY`` and ``NOT_A_WORKTREE`` are refusals with different remedies, and
#: ``GONE`` is not a failure at all — a directory somebody already deleted by hand is
#: the state pruning is trying to reach.
WORKTREE_STATE_LIVE = "dispatch-still-running"
WORKTREE_STATE_PRUNABLE = "prunable"
WORKTREE_STATE_DIRTY = "holds-uncommitted-work"
WORKTREE_STATE_GONE = "already-gone"
WORKTREE_STATE_UNKNOWN = "cannot-tell"
#: KYO-174.c4: the directory is gone (same as ``GONE``) AND git's own worktree
#: admin state still holds a stale entry for it — the near-opposite vocabulary
#: `git worktree list` uses for "prunable" (record body: "the two vocabularies are
#: near-opposites and both are correct in their own frame"). Split out of ``GONE``
#: so a controller can tell "nothing left to do here" from "run `git worktree
#: prune` to finish clearing this one" instead of both reading as the same thing.
WORKTREE_STATE_STALE_GIT_ENTRY = "stale-git-entry"
#: KYO-207: the dispatch is finished and the directory is clean, but its branch is NOT
#: contained in the project's main branch — so the work is committed, and nothing has
#: merged or rehearsed it yet. "Finished" meant `reported`, and a consumer pruned one
#: dispatch's worktree while APP-58's went with it before its branch had been merged;
#: nothing was lost, because the worker had committed and the branch survives by design,
#: but the controller had to recreate the worktree to run the rehearsal from that code.
#: The skill sells `--prune` as safe because "branches are left alone", which is true and
#: is not the property that was needed: the DIRECTORY is the thing a rehearsal runs in.
WORKTREE_STATE_UNMERGED = "branch-not-merged"
#: KYO-207, law 6's half of it: git could not answer whether the branch is contained —
#: the base ref is unknown here (a project whose trunk is not called `main` and that did
#: not pass ``--against``), the branch is gone, or git is unusable. Withheld from a prune
#: exactly like :data:`WORKTREE_STATE_UNMERGED`, and named separately because "not
#: merged" and "could not tell" are different facts and only one of them is a finding.
WORKTREE_STATE_MERGE_UNKNOWN = "cannot-tell-if-merged"

#: KYO-402: the recorded directory exists but `git worktree list --porcelain` in the code
#: repository does not list it as a linked worktree — a separate clone, a subdirectory of
#: the checkout, a store root, or a legacy relative value re-resolved after a move onto
#: something this tool never created. Never removable, by any flag: a dispatch record is
#: a claim about a path, and only git's own registration says the path is a worktree
#: whose removal cannot take anything else with it.
WORKTREE_STATE_NOT_REGISTERED = "not-a-registered-worktree"

#: KYO-475: this dispatch is finished, but a RUNNING dispatch records the same resolved
#: path — the withdrawn-then-re-frozen `.d2` of KYO-345, which legitimately shares its
#: predecessor's worktree. Rows are one per dispatch and the prune loop removes by row,
#: so the finished row was judged on its own state and removed the running worker's
#: directory (a consumer report). Never removable, by any flag, while that holder runs.
WORKTREE_STATE_HELD_BY_RUNNING = "held-by-running-dispatch"

#: KYO-207: the states `--prune` removes only when `--include-unmerged` says so.
WORKTREE_STATES_NEEDING_INCLUDE_UNMERGED: tuple[str, ...] = (
    WORKTREE_STATE_UNMERGED,
    WORKTREE_STATE_MERGE_UNKNOWN,
)

#: The dispatch states whose worktree has no further work to do. ``running`` is
#: excluded and that is the whole safety property: a worker may be mid-run in there.
#: KYO-345 adds ``withdrawn``, and it is the clearest member of the set rather than a
#: borderline one — a dispatch that was never handed to a worker has nobody in its
#: directory by construction. ``--prune`` still refuses to remove a worktree holding
#: uncommitted work, so the case of a directory a controller did use by hand is
#: covered by the check that has always covered it.
FINISHED_DISPATCH_STATES: frozenset[str] = frozenset(
    {DISPATCH_STATE_REPORTED, DISPATCH_STATE_TERMINATED_NO_REPORT, DISPATCH_STATE_WITHDRAWN}
)


def _worktree_dirty(directory: Path) -> tuple[bool | None, str]:
    """Does ``directory`` hold uncommitted work? ``None`` when git cannot say.

    Everything, including untracked files: the case this exists for is a worker that
    died before its first commit, whose output is untracked by definition. A worker
    on this project died mid-run with 89 uncommitted files and the worktree was the
    only surviving copy — resuming into it recovered the work, and a prune that had
    run first would have destroyed it.
    """
    from .git_queries import run_git

    try:
        status = run_git(directory, "status", "--porcelain")
    except OSError as exc:
        return None, f"could not run git in {directory}: {exc}"
    if status.returncode != 0:
        return None, (status.stderr.strip() or "git status failed").splitlines()[0]
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        return True, f"{len(dirty)} uncommitted or untracked path(s)"
    return False, "clean"


def _worktree_merge_judgment(
    ctx: Context, row, directory: Path, why: str, *, against: str
) -> tuple[bool | None, str, str]:
    """Has the code in a finished, clean, registered worktree landed? (KYO-207, KYO-402)

    Asked of the worktree's own HEAD, never of the branch the dispatch declared. The
    declared branch is what the controller will merge, but it is not where the commits
    are if the worker committed on a detached HEAD or on another branch: that branch
    never moves, so it reads as "merged into main" while the only ref holding the work
    is this directory's HEAD — and a prune that removed the directory made the commits
    unreachable (confirmed with `git fsck`). Returns
    ``(merged, state, detail)``.
    """
    branch = row["branch"]
    head = _git_rev_parse(directory, "HEAD")
    if head is None:
        return None, WORKTREE_STATE_MERGE_UNKNOWN, (
            f"{why}, but `git rev-parse HEAD` failed in {directory}, so nothing can say "
            f"whether its code is contained in {against} — remove it anyway with "
            "`--prune --include-unmerged`"
        )
    merged = branch_is_contained_in(ctx.code_root, head, against)
    short = head[:12]
    if merged:
        return True, WORKTREE_STATE_PRUNABLE, (
            f"{why}; its HEAD {short} is merged into {against}, so this directory is not "
            "the only copy of anything committed in it"
        )
    if merged is None:
        return None, WORKTREE_STATE_MERGE_UNKNOWN, (
            f"{why}, but git could not say whether its HEAD {short} is contained in "
            f"{against} (unknown base ref, or git unusable here) — name the right base "
            "with `--against <ref>`, or remove it anyway with `--prune --include-unmerged`"
        )
    # Not merged. Say where the unmerged commits are, because the remedy differs: a
    # declared branch that holds them is the ordinary "not merged yet"; a declared branch
    # that never advanced while HEAD did means the work exists nowhere but this directory.
    branch_sha = _git_rev_parse(ctx.code_root, f"{branch}^{{commit}}") if branch else None
    worked_from = row["worked_from_sha"]
    if branch and branch_sha is not None and branch_is_contained_in(
        ctx.code_root, head, branch
    ):
        where = f"{branch} is NOT merged into {against}"
    elif branch and branch_sha is not None and worked_from and branch_sha == worked_from:
        where = (
            f"the declared branch {branch} never advanced past worked_from_sha "
            f"{worked_from[:12]} while the worktree HEAD did (now {short}, not contained in "
            f"{against}) — the worker's commits are held by this directory's HEAD, not by "
            f"{branch}; point a branch at {short} before removing it"
        )
    elif branch and branch_sha is not None:
        where = (
            f"its HEAD {short} is NOT merged into {against} and is not on the declared "
            f"branch {branch} — point a branch at {short} before removing it"
        )
    elif branch:
        where = (
            f"its HEAD {short} is NOT merged into {against}, and the declared branch "
            f"{branch} does not resolve in {ctx.code_root} — point a branch at {short} "
            "before removing it"
        )
    else:
        where = (
            f"its HEAD {short} is NOT merged into {against} and the dispatch declared no "
            f"branch — point a branch at {short} before removing it"
        )
    return False, WORKTREE_STATE_UNMERGED, (
        f"{why}, but {where}. `--prune` leaves this in place; `--prune "
        "--include-unmerged` removes it anyway (no branch is ever touched)"
    )


def _git_rev_parse(root: Path, rev: str) -> str | None:
    """``git rev-parse --verify --quiet <rev>`` in ``root``, or ``None`` (KYO-402)."""
    from .git_queries import run_git

    try:
        result = run_git(root, "rev-parse", "--verify", "--quiet", rev)
    except OSError:
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def _worktree_rows(ctx: Context, store, *, against: str = "main") -> list[dict]:
    """Every dispatch that declared a worktree, with what is true of it now.

    KYO-207: "what is true of it" now includes whether its BRANCH has landed. "Finished"
    was read from the dispatch state alone, and the two questions are independent: a
    dispatch reports the moment its worker stops, which is before anybody has merged or
    rehearsed a line of what it wrote. ``branch_is_contained_in`` was already in this
    module for the drift check, so this asks a question the module could already answer
    and simply never did.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    # KYO-174.c4: asked once per call, not once per row — every row's "is the
    # directory gone" question can reuse the same answer to git's own admin
    # state, and `git worktree list` costs nothing extra per row it need not pay.
    # ``None`` (git unusable) is not a guess: it just means no row here can be
    # upgraded from plain GONE to the more specific stale-entry state.
    admin_entries = worktree_admin_entries(ctx.code_root)
    # KYO-402: the only directories a prune may remove. Asked once, like the admin
    # entries above (it is the same listing); ``None`` when git cannot answer, and then
    # no finished row can be judged removable at all.
    registered = registered_linked_worktrees(ctx.code_root)
    dispatches = list(store.all_dispatches())
    # KYO-475: which running dispatches hold each resolved path, asked once per call. A
    # finished row on one of these paths is never removable, whatever its own state.
    held_by_running: dict[Path, list[str]] = {}
    for row in dispatches:
        if row["worktree"] is None or row["closed_at"]:
            continue
        if row["state"] in FINISHED_DISPATCH_STATES:
            continue
        holder = ctx.resolve_worktree(row["worktree"]).resolve()
        held_by_running.setdefault(holder, []).append(row["id"])
    for row in dispatches:
        stored = row["worktree"]
        if stored is None:
            continue
        resolved = ctx.resolve_worktree(stored)
        # The project root is the undeclared-worktree default (KYO-22), not a
        # directory this tool created, and pruning it would delete the checkout.
        if resolved == ctx.code_root.resolve():
            continue
        key = f"{row['id']}:{resolved}"
        if key in seen:
            continue
        seen.add(key)
        finished = row["state"] in FINISHED_DISPATCH_STATES or bool(row["closed_at"])
        # KYO-207: `None` until something asks, and something only asks about a finished,
        # clean, existing directory — the one case where the answer can change a decision.
        # A `git merge-base --is-ancestor` per row that a prune would skip anyway would be
        # a cost paid for a fact nobody reads.
        merged: bool | None = None
        if not resolved.exists():
            if admin_entries is not None and admin_entries.get(str(resolved)):
                state, detail = (
                    WORKTREE_STATE_STALE_GIT_ENTRY,
                    "the directory is gone, but `git worktree list` still holds a "
                    "stale admin entry for it (git itself marks it prunable) — run "
                    "`git worktree prune` to clear it",
                )
            else:
                state, detail = WORKTREE_STATE_GONE, "the directory is not there"
        elif not finished:
            state, detail = (
                WORKTREE_STATE_LIVE,
                f"dispatch is {row['state']} — a worker may be in there right now",
            )
        elif resolved.resolve() in held_by_running:
            holders = ", ".join(held_by_running[resolved.resolve()])
            state, detail = (
                WORKTREE_STATE_HELD_BY_RUNNING,
                f"dispatch is {row['state']}, but this path is held by running dispatch "
                f"{holders}, which records the same worktree — `kyo worktrees --prune` "
                "never removes it, with or without --include-unmerged, until that "
                "dispatch is finished too",
            )
        elif registered is None:
            state, detail = (
                WORKTREE_STATE_UNKNOWN,
                f"`git worktree list` could not be run in {ctx.code_root}, so nothing can "
                "say whether this directory is a worktree of this repository",
            )
        elif resolved.resolve() not in registered:
            # KYO-402. Before this, any directory a finished dispatch had recorded was
            # rmtree'd once it looked clean — a separate clone holding an unpushed
            # branch, `lib/` inside the main checkout, and (with `--branch ..`) the
            # split store with its `.git` were all deleted in a reproduction. A
            # recorded path is a claim; git's registration is the fact.
            state, detail = (
                WORKTREE_STATE_NOT_REGISTERED,
                f"the path is not a registered worktree of this repository (`git worktree "
                f"list` in {ctx.code_root} does not list it), so `kyo worktrees --prune` "
                "never removes it, with or without --include-unmerged — delete it by hand "
                "if it is really yours to delete",
            )
        else:
            dirty, why = _worktree_dirty(resolved)
            if dirty is None:
                state, detail = WORKTREE_STATE_UNKNOWN, why
            elif dirty:
                state, detail = WORKTREE_STATE_DIRTY, why
            else:
                merged, state, detail = _worktree_merge_judgment(
                    ctx, row, resolved, why, against=against
                )
        rows.append(
            {
                "dispatch_id": row["id"],
                "record_id": row["record_id"],
                "dispatch_state": row["state"],
                # KYO-292/KYO-286: the two timestamps this listing's consumers need and
                # could not get from it. `created_at` is what the token budget is spent
                # by (:func:`worker_tokens_index` — the ids in THIS list are not in
                # creation order, see that function), and it is the start of the run
                # time the desktop's activity row draws; `report_id` is how
                # :func:`dispatch_end` finds the ingest that stopped the clock on a
                # dispatch nobody ever closed.
                "created_at": row["created_at"],
                # KYO-325: and the other end of the same run, which is what says
                # whether a stop row could have been this dispatch's worker's at all.
                # Not rendered; read by `worker_tokens_index` below.
                "updated_at": row["updated_at"],
                "closed_at": row["closed_at"],
                "report_id": row["report_id"],
                "branch": row["branch"],
                "worktree": stored,
                "worktree_resolved": str(resolved),
                "state": state,
                "detail": detail,
                # KYO-207.c2: the same three-way answer the listing renders, as data.
                # `None` on every row the question was not asked about (still running,
                # gone, dirty, no branch) as well as on the row git could not answer —
                # `state` is what tells those apart, which is why it is the field the
                # prune decision reads.
                "branch_merged": merged,
                "merge_base": against,
            }
        )
    return rows


#: The flag that puts the unaudited-stop ids back on a settled row (KYO-223.c2). Named
#: once because `cmd_worktrees` prints it and the tests assert it (law 3).
EXPAND_STOPS_FLAG = "--expand-stops"


def _worktree_row_is_settled(row: dict) -> bool:
    """Is there anything left to decide about this row? (KYO-223.c1)

    Settled means the dispatch is finished AND the directory is already gone — the two
    together, never either alone. A finished dispatch whose directory is still there is
    exactly what ``--prune`` is about to act on; a directory that is gone under a still
    running dispatch is a worker's tree that vanished, which is a finding rather than a
    tidy row. :data:`WORKTREE_STATE_STALE_GIT_ENTRY` is deliberately NOT settled even
    though its directory is gone too: it names a `git worktree prune` the reader is
    being asked to run, and a row carrying an instruction is a row with a decision in it.
    """
    return (
        row["state"] == WORKTREE_STATE_GONE
        and row["dispatch_state"] in FINISHED_DISPATCH_STATES
    )


def _stop_census_lines(ledger: StopAuditLedger, rows: list[dict]) -> list[str]:
    """KYO-423.c5: the stop-audit census ``kyo worktrees --expand-stops`` prints under
    the ledger's sentence, and the default output no longer does.

    KYO-373.c1 folded the per-stop lines (12,791 of them on one store, 6,277 by agents
    with no transcript) behind the flag and left one line per named
    dispatch; KYO-375.c3 folded the tick-only dispatches into one line. KYO-423 moves
    what was left behind the same flag, so asking for it now prints all of it: every
    dispatch the unplaced stops named, with its unmatched stops by reason, then each
    unmatched stop with its candidates. Stops by an agent with no transcript are
    counted even here, and ``--json`` carries each one.
    """
    lines: list[str] = []
    states = {row["dispatch_id"]: row["dispatch_state"] for row in rows}
    named = ledger.by_dispatch()
    if named:
        lines.append(
            "  per dispatch those stops named — unmatched worker stops by reason, then "
            "stops by an agent with no transcript (not a worker, KYO-373):"
        )
    # `named` is insertion-ordered by first-named order (`by_dispatch`'s own promise);
    # the dispatches with unmatched stops go first, ties in first-named order.
    ordered = [
        *[(d, c) for d, c in named.items() if c["unmatched"] > 0],
        *[(d, c) for d, c in named.items() if c["unmatched"] == 0],
    ]
    for dispatch_id, counts in ordered:
        reasons = ", ".join(
            f"{label} {counts[code]}" for code, label in STOP_REASON_LABELS.items()
        )
        lines.append(
            f"    {dispatch_id} ({states.get(dispatch_id, 'not listed above')}): "
            f"{counts['unmatched']} unmatched ({reasons}); "
            f"{counts['not_a_worker']} not a worker"
        )
    for audit in ledger.unresolved:
        lines.append(
            f"  ! {audit.event_id} at {audit.at}: {audit.why}. Fell back to reading "
            f"{audit.directory}, which is the orchestrator's cwd and not the stopping "
            "worker's tree"
        )
        # KYO-134.c2: the candidates, so a controller can tell WHICH worktrees went
        # unaudited. This is the whole of what the stop knew, and withholding it
        # would leave a warning nobody can act on — which is the defect, not the fix.
        lines.append(
            "    it could have been any of: "
            + (", ".join(audit.candidates) if audit.candidates else "(no candidates recorded)")
        )
    if ledger.untranscribed:
        # Counted, not listed, even here: one line per tick is the 6,000-line section
        # KYO-373 removed, and asking for the unmatched stops is not asking for those.
        lines.append(
            f"  {len(ledger.untranscribed)} stop(s) by an agent with no transcript "
            "are not listed one per line even here; `--json` carries each one "
            "under `stop_audits.no_transcript`"
        )
    return lines


def cmd_worktrees(ctx: Context, args: Namespace) -> Output:
    """``kyo worktrees [--prune]`` — KYO-70.c3: list dispatch worktrees, and remove
    the ones whose dispatch is finished.

    Listing is the default and ``--prune`` is the explicit apply (law 11: dry run by
    default). Seven things are never removed, and each says which it is rather than
    being silently skipped:

    * **KYO-402: a recorded directory that ``git worktree list`` in the code repository
      does not list as a linked worktree** — by any flag;
    * a worktree whose dispatch is still ``running`` — a worker may be in it now;
    * **KYO-475: a finished dispatch's worktree that a running dispatch also records**
      — by any flag, since the path is the running worker's whatever this row says;
    * a worktree holding **uncommitted work**, tracked or untracked — it may be the only
      surviving copy of a worker's output (docs/design/dispatch.md#kyo-70);
    * a worktree git could not answer about at all, which is law 6's "unknown must
      not read as either answer";
    * **KYO-207: a worktree whose HEAD is not contained in the project's main
      branch** (KYO-402: its HEAD, not its declared branch), and
    * one git could not decide that about. Both are removed by
      ``--prune --include-unmerged``, and both name that flag in their own line.

    KYO-207: "finished" means merged, not ``reported``. A dispatch is ``reported`` the
    instant its worker stops, which says nothing about whether anyone has merged or
    rehearsed what it wrote, and the DIRECTORY is what a rehearsal runs in
    (docs/design/dispatch.md#kyo-207).

    The branch is still deliberately left alone. Deleting a branch destroys history that
    may not be merged anywhere, and this command's subject is a directory.

    ``--against`` names the base the containment question is asked against, defaulting to
    ``main``. It exists because the question has no answer without it in a project whose
    trunk is called something else, and a check that cannot answer must not quietly
    withhold every directory forever.

    Removal is ``git worktree remove <path>``, one path at a time
    (:func:`kyosei.git_queries.worktree_remove`), which clears that path's admin entry in
    the same act and leaves every other stale entry for git. An unregistered recorded
    directory (a separate clone, a subdirectory of the checkout, a store root) is
    ``WORKTREE_STATE_NOT_REGISTERED`` and never removed; a directory an earlier build's
    prune left stale is ``WORKTREE_STATE_STALE_GIT_ENTRY`` rather than plain "gone"
    (KYO-174, KYO-402; docs/design/dispatch.md#kyo-402).

    KYO-402 also moved the merged question from the declared branch to the worktree's
    own HEAD (:func:`_worktree_merge_judgment`), so a worker that committed on a
    detached HEAD does not read as merged because its declared branch never moved.

    "Branches are left alone on purpose" names only the branches of the worktrees *this
    run* removed (KYO-196), not every branch the store ever declared.
    """
    store = ctx.store()
    against = getattr(args, "against", None) or "main"
    rows = _worktree_rows(ctx, store, against=against)
    pruning = bool(getattr(args, "prune", False))
    including_unmerged = bool(getattr(args, "include_unmerged", False))
    # KYO-207: one set, computed once, so the dry run's count and the prune's decision
    # are the same judgment rather than two expressions that could drift (law 3).
    removable = {WORKTREE_STATE_PRUNABLE}
    if including_unmerged:
        removable.update(WORKTREE_STATES_NEEDING_INCLUDE_UNMERGED)

    removed: list[str] = []
    failures: list[str] = []
    removed_branches: set[str] = set()
    if pruning:
        for row in rows:
            if row["state"] not in removable:
                continue
            directory = Path(row["worktree_resolved"])
            # KYO-402: every removable state was judged against git's registration
            # (WORKTREE_STATE_NOT_REGISTERED otherwise), and removal is git's own act on
            # that one path — directory and admin entry together — never an rmtree plus
            # a repository-wide `git worktree prune` that also cleared entries this run
            # had nothing to do with.
            ok, output = worktree_remove(ctx.code_root, directory)
            if not ok:
                failures.append(f"{directory}: `git worktree remove` refused: {output}")
                continue
            removed.append(str(directory))
            if row["branch"]:
                removed_branches.add(row["branch"])
            row["state"] = WORKTREE_STATE_GONE
            row["detail"] = "removed by `kyo worktrees --prune`"

    home = config_module.worktree_home(ctx.worktree_project_name, ctx.environ)
    # KYO-89: the home was renamed to give it a space, so anything created under the
    # old name resolves nowhere and would list as `already-gone` while sitting on
    # disk. Named, never removed: this command did not create those directories under
    # the name it now uses, and a silent accumulation in a directory the tool was
    # never given is the exact defect KYO-70 exists to end.
    stranded = [
        directory
        for directory in config_module.legacy_worktree_homes(
            ctx.worktree_project_name, ctx.environ
        )
        if directory.is_dir() and any(directory.iterdir())
    ]
    lines = [
        f"worktrees for {ctx.worktree_project_name} ({len(rows)} declared by a dispatch)",
        echo_value("default home", str(home)),
    ]
    for directory in stranded:
        held = sorted(p.name for p in directory.iterdir())
        lines.append(
            f"  ! {directory} is a home this project used before KYO-89 and still holds "
            f"{len(held)} entr(y/ies): {', '.join(held)}. Nothing here resolves to them "
            "any more and nothing will remove them — move anything you still want, then "
            "delete the directory by hand"
        )
    if not rows:
        lines.append("  (no dispatch has ever declared a worktree outside the project root)")
    # KYO-134: which of these trees a stop went unaudited over. Read once, before the
    # loop, because every row asks the same ledger the same question.
    ledger = stop_audits(store)
    # KYO-284: and what each of those workers cost, off the same ledger read. Every
    # row gets an entry — a number, or the named reason there is none — because the
    # desktop's activity list renders this and derives nothing of its own.
    tokens = worker_tokens_index(
        ledger,
        [row["dispatch_id"] for row in rows],
        # KYO-292: by CREATION time, not by position in this list. `rows` follows
        # `dispatches.seq`, which is insertion order — a rebuild replays the export
        # directory, so the ids come out in a lexical shuffle and the newest
        # dispatches are nowhere near the tail.
        created_at={row["dispatch_id"]: row["created_at"] for row in rows},
        # KYO-325: the window each dispatch's worker could have stopped in, so a
        # dispatch whose stop was never recorded says that, instead of reporting the
        # ticks that name it hours later as stops that failed to match. Open-ended for
        # a dispatch still running — its worker has not stopped yet.
        ran_between={
            row["dispatch_id"]: (
                row["created_at"],
                None if row["dispatch_state"] == "running" else (row["updated_at"] or None),
            )
            for row in rows
            if row["created_at"]
        },
        always=[row["dispatch_id"] for row in rows if row["state"] == WORKTREE_STATE_LIVE],
    )
    # KYO-286: and when each of them finished, off the same ledger read plus one query
    # for the reports table. Every row carries the answer and the name of the source it
    # came from, because the desktop's activity list renders this and derives nothing.
    ingested = report_ingest_times(store)
    for row in rows:
        row["worker_tokens"] = tokens[row["dispatch_id"]].as_json()
        row.update(
            dispatch_end(
                ledger,
                dispatch_id=row["dispatch_id"],
                state=row["dispatch_state"],
                report_id=row["report_id"],
                report_ingested_at=ingested.get(row["report_id"] or ""),
                closed_at=row["closed_at"],
            ).as_json()
        )
    expand_stops = bool(getattr(args, "expand_stops", False))
    # KYO-223. A row whose dispatch is finished and whose directory is already gone is
    # settled: there is nothing left to decide about it, `--prune` will not touch it,
    # and the unaudited-stop ids it carries are the same ids on every such row. Six of
    # them produced several hundred event ids on a consumer's terminal and pushed the
    # two rows that DID need a decision off the screen. So the settled rows are one
    # line each and they go last; everything a controller might act on — live,
    # unmerged, dirty, cannot-tell, stale-git-entry, prunable — keeps today's detail
    # and keeps its place at the top.
    settled = [row for row in rows if _worktree_row_is_settled(row)]
    live = [row for row in rows if not _worktree_row_is_settled(row)]
    for row in live:
        unaudited = ledger.naming(row["dispatch_id"])
        # An extra clause on the row rather than a separate section: a controller
        # scanning this list is deciding about THIS directory, and a finding kept
        # somewhere else is a finding they read after they have already decided.
        column = (
            f" — ! {len(unaudited)} stop(s) went UNAUDITED and could have been this one: "
            + ", ".join(audit.event_id for audit in unaudited)
            if unaudited
            else ""
        )
        row["unaudited_stops"] = [audit.event_id for audit in unaudited]
        # KYO-284: on the live rows only. A controller reading this list is deciding
        # about a worker that is still running, and "what has this one cost so far"
        # is part of that decision; on a settled row it is history, and the settled
        # rows are one line each on purpose (KYO-223).
        spend = tokens[row["dispatch_id"]]
        cost = (
            f" — {spend.model or 'model not named'} · {spend.total_tokens} tokens"
            if spend.known
            else ""
        )
        lines.append(
            f"  {row['worktree_resolved']}  [{row['state']}]  "
            f"{row['dispatch_id']} ({row['dispatch_state']}) — {row['detail']}{cost}{column}"
        )
    if settled:
        lines.append(
            f"  {len(settled)} finished-and-gone worktree(s), one line each"
            + (
                ""
                if expand_stops
                else f" — `kyo worktrees {EXPAND_STOPS_FLAG}` names their unaudited stops, "
                "and `--json` always carries them"
            )
            + ":"
        )
    for row in settled:
        unaudited = ledger.naming(row["dispatch_id"])
        row["unaudited_stops"] = [audit.event_id for audit in unaudited]
        if not unaudited:
            column = ""
        elif expand_stops:
            column = (
                f" — ! {len(unaudited)} stop(s) went UNAUDITED and could have been this "
                "one: " + ", ".join(audit.event_id for audit in unaudited)
            )
        else:
            # The count, never the ids: the count is the fact that would make a reader
            # go looking, and the ids are what made six settled rows unreadable. The
            # warning is not softened — it still says UNAUDITED on every row that has
            # one — only the list behind it is moved (law 6: nothing is hidden that the
            # reader cannot get back, and the line says where it went).
            column = f" — ! {len(unaudited)} unaudited stop(s)"
        lines.append(
            f"  {row['worktree_resolved']}  [{row['state']}]  "
            f"{row['dispatch_id']} ({row['dispatch_state']}) — {row['detail']}{column}"
        )
    # KYO-134.c5. Printed on every run, including — especially — the run with nothing
    # to report. "No unresolved stops" and "nothing ever recorded a stop" are
    # different facts, and a section that simply renders empty states the first while
    # meaning the second. `sentence()` is the one place that distinction is written.
    # KYO-423.c5: the stop-audit census is not what a controller asking "what can I
    # prune" came for, so by default it is ONE line: the ledger's sentence — which
    # carries the unmatched count, and says CANNOT TELL or none-recorded where that is
    # the answer (KYO-134.c5) — naming the flag that prints the rest. An unaudited
    # worker stop is therefore never silently hidden (KYO-134); the per-dispatch lines,
    # the tick fold (KYO-375) and the per-stop lines (KYO-373) are all behind
    # `--expand-stops`, and `--json` carries every stop whichever way this prints.
    if not expand_stops:
        lines.append(
            f"{ledger.sentence()} — `kyo worktrees {EXPAND_STOPS_FLAG}` prints the "
            "per-dispatch stop census"
        )
    else:
        lines.append(ledger.sentence())
        lines.extend(_stop_census_lines(ledger, rows))
    # KYO-196: this used to name every branch any dispatch had EVER declared a
    # worktree for — `rows` is every row `_worktree_rows` ever returns, most of
    # them long finished, and pruning a worktree never removes a dispatch, so
    # that list only grew. Only the branches of the worktrees THIS run actually
    # removed are the ones a reader could have mistaken for deleted — computed
    # once, here, so the printed line and the JSON key below are one value, not
    # two expressions that could drift apart.
    branches_left_alone = sorted(removed_branches)
    # KYO-207.c2: what a prune WOULD remove, said before one is run and on the same
    # judgment the prune itself reads. Printed on both paths, because a controller who
    # just ran `--prune` without `--include-unmerged` needs to know what it skipped.
    unmerged = [r for r in rows if r["state"] == WORKTREE_STATE_UNMERGED]
    merge_unknown = [r for r in rows if r["state"] == WORKTREE_STATE_MERGE_UNKNOWN]
    if unmerged or merge_unknown:
        lines.append(
            f"{len(unmerged) + len(merge_unknown)} finished worktree(s) are held back "
            f"because their branch is not contained in {against} "
            f"({len(unmerged)} not merged, {len(merge_unknown)} git could not tell): "
            + ", ".join(
                f"{r['dispatch_id']} ({r['branch']})" for r in [*unmerged, *merge_unknown]
            )
            + ". A reported dispatch is not a merged one, and the rehearsal of that "
            "branch runs in that directory (KYO-207). `kyo worktrees --prune "
            "--include-unmerged` removes them anyway; `--against <ref>` names a "
            "different base."
        )
    if not pruning:
        prunable = [r for r in rows if r["state"] in removable]
        lines.append(
            f"{len(prunable)} would be removed by `kyo worktrees --prune"
            + (" --include-unmerged" if including_unmerged else "")
            + "`; nothing has been removed by this run"
        )
        held = [r for r in rows if r["state"] == WORKTREE_STATE_DIRTY]
        if held:
            lines.append(
                f"{len(held)} hold uncommitted work and `--prune` will REFUSE them — that "
                "directory may be the only surviving copy of a dead worker's output; commit "
                "or copy it out first"
            )
    else:
        lines.append(f"removed {len(removed)} worktree(s)")
        lines += [f"  - {path}" for path in removed]
        lines += [f"  ! {failure}" for failure in failures]
        if branches_left_alone:
            lines.append(
                f"branches are left alone on purpose ({len(branches_left_alone)}): "
                + ", ".join(branches_left_alone)
                + " — deleting one destroys history that may not be merged anywhere"
            )
    return Output(
        lines,
        {
            "ok": not failures,
            "command": "worktrees",
            "home": str(home),
            # KYO-89: the same finding the text renders, as data — a consumer that
            # cannot see these cannot act on them either.
            "legacy_homes_still_holding_work": [str(d) for d in stranded],
            # KYO-134: the same three-way answer the text renders, as data. A
            # consumer reading only `worktrees` above cannot tell a store with no
            # unresolved stops from one that has never recorded a stop at all.
            "stop_audits": ledger.as_json(),
            "pruned": pruning,
            # KYO-207: the base the containment question was asked against, whether the
            # override was in play, and which rows it would have changed. A consumer that
            # cannot see these cannot tell "nothing to prune" from "everything withheld".
            "against": against,
            "include_unmerged": including_unmerged,
            "unmerged": [r["dispatch_id"] for r in unmerged],
            "merge_unknown": [r["dispatch_id"] for r in merge_unknown],
            "worktrees": rows,
            "removed": removed,
            "failures": failures,
            # KYO-196.c1: the same list the "left alone on purpose" line renders,
            # as data — empty except on a prune run that actually removed
            # something with a declared branch.
            "branches_left_alone": branches_left_alone,
        },
    )


# ------------------------------------------ KYO-454: dispatch activity

#: How many finished dispatches `kyo activity` shows beside every running one, by
#: default — the desktop activity view's own window (`ACTIVITY_FINISHED_DEFAULT`).
ACTIVITY_FINISHED_DEFAULT = 12

#: The file a worker writes its structured report to, in its own worktree.
REPORT_FILE = ".kyosei-report.json"

#: How long any one git call behind a git-facts row is given. Short, because the
#: activity view reads this on a poll: a git that has not answered by then is a
#: repository under an index lock, and the honest row says "git did not answer".
GIT_FACTS_TIMEOUT_SECONDS = 5.0

#: The ``outcome`` words a git-facts row carries (the desktop's `GitFactsOutcome`).
GIT_FACTS_READ = "read"
GIT_FACTS_NO_WORKTREE = "no-worktree-declared"
GIT_FACTS_GONE = "directory-gone"
GIT_FACTS_NOT_A_WORKTREE = "not-a-git-worktree"


def _git_answer(directory: Path, *args: str) -> tuple[bool, str]:
    """``(ok, stdout-or-why)`` for one bounded git call; never raises."""
    from .git_queries import run_git

    try:
        result = run_git(directory, *args, timeout=GIT_FACTS_TIMEOUT_SECONDS)
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr.strip() or f"git {args[0]} failed")
    return True, result.stdout.strip()


def _diff_stat(directory: Path, base: str, head: str) -> tuple[int, int] | None:
    """Lines added and removed ``base...head``, binary files counting as nothing."""
    ok, out = _git_answer(directory, "diff", "--numstat", f"{base}...{head}")
    if not ok:
        return None
    added = removed = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return added, removed


def git_facts(
    directory: Path | None, base: str | None, *, against: str = "main"
) -> dict:
    """What git says about one dispatch's worktree, or the named reason it cannot.

    The rule `kyo worktrees` reads git under: A MISSING DIRECTORY IS A FACT, NEVER AN
    ERROR — a pruned worktree is the ordinary end of a finished dispatch. Every path ends
    in a named ``outcome`` and a ``why``; nothing raises and nothing is guessed. ``dirty``
    is `git status --porcelain` as git printed it; ``commits_past_base`` is ``None``
    (never 0) when the base is not in this repository; ``merged_into`` is ``None`` when
    there is no ``against`` here to compare with.
    """
    facts: dict = {
        "worktree": str(directory) if directory is not None else None,
        "present": False,
        "dirty": None,
        "dirty_paths": [],
        "branch": None,
        "commits_past_base": None,
        "latest_commit_sha": None,
        "latest_commit_message": None,
        "lines_added": None,
        "lines_removed": None,
        "merged_into": None,
        "report_file_present": False,
    }
    if directory is None:
        return {
            **facts,
            "outcome": GIT_FACTS_NO_WORKTREE,
            "why": "this dispatch declared no worktree, so there is no directory to look at",
        }
    if not directory.is_dir():
        return {
            **facts,
            "outcome": GIT_FACTS_GONE,
            "why": f"{directory} is not there — for a finished dispatch the ordinary end "
            "state (`kyo worktrees --prune` removes it); for a running one, the directory "
            "was removed out from under it",
        }
    facts["present"] = True
    facts["report_file_present"] = (directory / REPORT_FILE).is_file()
    ok, inside = _git_answer(directory, "rev-parse", "--is-inside-work-tree")
    if not ok or inside != "true":
        return {
            **facts,
            "outcome": GIT_FACTS_NOT_A_WORKTREE,
            "why": f"{directory} is there and git does not consider it a worktree: {inside}",
        }
    status_ok, status = _git_answer(directory, "status", "--porcelain")
    dirty_paths = (
        [line.strip() for line in status.splitlines() if line.strip()] if status_ok else []
    )
    facts["dirty"] = bool(dirty_paths) if status_ok else None
    facts["dirty_paths"] = dirty_paths
    ok, branch = _git_answer(directory, "rev-parse", "--abbrev-ref", "HEAD")
    facts["branch"] = branch if ok and branch != "HEAD" else None
    ok, head = _git_answer(directory, "log", "-1", "--format=%H%x1f%s")
    if ok and head:
        sha, _, subject = head.partition("\x1f")
        facts["latest_commit_sha"], facts["latest_commit_message"] = sha, subject
    if base:
        ok, past = _git_answer(directory, "rev-list", "--count", f"{base}..HEAD")
        facts["commits_past_base"] = int(past) if ok and past.isdigit() else None
        lines = _diff_stat(directory, base, "HEAD")
        if lines is not None:
            facts["lines_added"], facts["lines_removed"] = lines
    facts["merged_into"] = branch_is_contained_in(directory, "HEAD", against)
    return {
        **facts,
        "outcome": GIT_FACTS_READ,
        "why": f"read from {directory}"
        if status_ok
        else f"read from {directory}, except its working-tree status: {status}",
    }


def _activity_classes(row: dict) -> list[str]:
    """Which classes one activity row is in, read off the store's own words.

    ``running`` and ``terminated-no-report`` are the dispatch's state;
    ``finished-but-not-moved`` is KYO-87's (reported, and its record still ``open``);
    ``gated`` is a record carrying an open gate.
    """
    classes: list[str] = []
    if row["state"] == DISPATCH_STATE_RUNNING:
        classes.append(DISPATCH_STATE_RUNNING)
    if row["state"] == DISPATCH_STATE_TERMINATED_NO_REPORT:
        classes.append(DISPATCH_STATE_TERMINATED_NO_REPORT)
    if row["state"] == DISPATCH_STATE_REPORTED and row["record_status"] == "open":
        classes.append("finished-but-not-moved")
    if row["open_gates"] > 0:
        classes.append("gated")
    return classes


def cmd_activity(ctx: Context, args: Namespace) -> Output:
    """``kyo activity [--finished N] [--against REF]`` (KYO-454): the dispatch activity rows.

    Every running dispatch, then the ``N`` newest finished ones — each with its record,
    its current report's verdict counts, its record's open-gate count and classes, the
    worker's token spend and end time (the same answers `kyo worktrees --json` carries),
    the stops that went unaudited and could have been it, and what git says about its
    worktree now (:func:`git_facts`). When the worktree is gone, lines added and removed
    are asked of the code repository, base to declared branch.

    Read-only: no transaction, no row written, and nothing removed — `kyo worktrees
    --prune` is where removal lives.
    """
    from . import query

    store = ctx.store()
    finished = getattr(args, "finished", None)
    finished = ACTIVITY_FINISHED_DEFAULT if finished is None else finished
    if finished < 0:
        from .errors import UsageError

        raise UsageError(f"--finished must be 0 or more, got {finished}")
    against = getattr(args, "against", None) or "main"
    every = query.dispatch_rows(store)
    running = [row for row in every if row["state"] == DISPATCH_STATE_RUNNING]
    rows = running + [row for row in every if row["state"] != DISPATCH_STATE_RUNNING][:finished]
    counts = query.open_gate_counts(store)
    ledger = stop_audits(store)
    ids = [row["id"] for row in rows]
    tokens = worker_tokens_index(
        ledger,
        ids,
        created_at={row["id"]: row["created_at"] for row in rows},
        ran_between={
            row["id"]: (
                row["created_at"],
                None if row["state"] == DISPATCH_STATE_RUNNING else (row["updated_at"] or None),
            )
            for row in rows
            if row["created_at"]
        },
        always=[row["id"] for row in running],
    )
    ingested = report_ingest_times(store)
    home = config_module.worktree_home(ctx.worktree_project_name, ctx.environ)
    lines = [
        f"dispatch activity for {ctx.worktree_project_name}: {len(running)} running, "
        f"{len(rows) - len(running)} of {len(every) - len(running)} finished shown "
        f"(--finished {finished})"
    ]
    for row in rows:
        row["open_gates"] = counts.get(row["record_id"], 0)
        row["classes"] = _activity_classes(row)
        row["unaudited_stops"] = [audit.event_id for audit in ledger.naming(row["id"])]
        row["worker_tokens"] = tokens[row["id"]].as_json()
        row.update(
            dispatch_end(
                ledger,
                dispatch_id=row["id"],
                state=row["state"],
                report_id=row["report_id"],
                report_ingested_at=ingested.get(row["report_id"] or ""),
                closed_at=row["closed_at"],
            ).as_json()
        )
        stored = row["worktree"]
        directory = ctx.resolve_worktree(stored) if stored and stored.strip() else None
        row["worktree_resolved"] = str(directory) if directory is not None else None
        facts = git_facts(directory, row["worked_from_sha"] or None, against=against)
        if facts["lines_added"] is None and row["branch"] and row["worked_from_sha"]:
            lines_from_repo = _diff_stat(ctx.code_root, row["worked_from_sha"], row["branch"])
            if lines_from_repo is not None:
                facts["lines_added"], facts["lines_removed"] = lines_from_repo
        row["git"] = facts
        spend = tokens[row["id"]]
        cost = f" · {spend.total_tokens} tokens" if spend.known else ""
        where = (
            f"{facts['commits_past_base']} past base" if facts["commits_past_base"] is not None
            else facts["outcome"]
        )
        dirty = " · dirty" if facts["dirty"] else ""
        classes = f" [{', '.join(row['classes'])}]" if row["classes"] else ""
        lines.append(
            f"  {row['id']} ({row['state']}){classes} {row['record_id']} "
            f"{' '.join((row['record_title'] or '').split())} — {row['branch'] or 'no branch'}, "
            f"{where}{dirty}{cost}"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "activity",
            "home": str(home),
            "against": against,
            "finished": finished,
            "running": len(running),
            "finished_shown": len(rows) - len(running),
            "finished_total": len(every) - len(running),
            "stop_audits": ledger.sentence(),
            "rows": rows,
        },
    )
