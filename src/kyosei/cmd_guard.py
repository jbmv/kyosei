"""What the worker-side guards share: which worktree a run is about, and saying so.

``check-branch`` (:mod:`kyosei.cmd_check_branch`), ``check-clean``
(:mod:`kyosei.cmd_check_clean`) and ``check-command`` (:mod:`kyosei.cmd_check_command`)
all run against a dispatched worker's worktree, from a Claude Code hook, and none of them
writes to the store. They are the only commands whose subject is the worktree rather than
the record, which is why KYO-57 gave them their own file rather than leaving them beside
``dispatch``/``report``: a record about what a hook checks and a record about how a
dispatch is created should not collide. This module holds the part the stop guards share:
resolving the stop to a tree and naming that tree, and how, on every run.
"""


from __future__ import annotations

import sys
from argparse import Namespace

from .commands import (
    Context,
    Output,
)
from .config import (
    LAYOUT_IN_REPO,
    LAYOUT_SPLIT,
)
from .errors import GuardRefusal
from .stop_target import (
    STOP_TARGET_MATCHED,
    STOP_TARGET_NO_TRANSCRIPT,
    STOP_TARGET_NO_WORKTREES,
    STOP_TARGET_NOT_A_WORKER,
    STOP_TARGET_STORE_UNREADABLE,
    StopTarget,
    resolve_stop_target,
)


def _guarantee_lines(ctx: Context, guarded_label: str, *, guards_store: bool = True) -> list[str]:
    """What this guard is actually guaranteeing, under this project's layout.

    KYO-54.c2. The split layout makes the guard *stronger*: a branch in the code
    repository cannot express a diff under ``kyosei/`` or ``ideas/`` at all,
    because neither is in that repository. But "stronger" and "vacuous" produce
    the same exit code, and a check that silently stops checking anything is
    indistinguishable from one that is working — so the guarantee is stated
    every time, in the same breath as the verdict, rather than left to be
    inferred from a zero.
    """
    layout = ctx.layout
    if layout is None or layout.kind == LAYOUT_IN_REPO:
        if not guards_store:
            return [
                (layout.describe() if layout else "store layout: in-repo"),
                f"  so {guarded_label} are in this very repository, and what is outstanding "
                "there is the orchestrator's to commit, never a worker's — which is why they "
                "are excluded here and forbidden by `kyo check-branch`",
            ]
        return [
            (layout.describe() if layout else "store layout: in-repo"),
            f"  so this diff check is what stands between a worker branch and the store: "
            f"{guarded_label} are real paths in this repository, and a commit touching "
            "them is a real merge that would reach the store",
        ]
    if layout.kind == LAYOUT_SPLIT:
        if not guards_store:
            return [
                layout.describe(),
                f"  so {guarded_label} could not appear here anyway: the store's own "
                "uncommitted changes are in a different repository, where they are the "
                "orchestrator's to commit and cannot block a worker's stop",
            ]
        return [
            layout.describe(),
            f"  so a branch here cannot diff the store at all — {guarded_label} are not in "
            "this repository, and worker isolation is a physical property rather than a "
            "check that could fail",
            f"  what is still checked: a *stray* {guarded_label} inside this repository, "
            "which would be a second copy of a store nothing reads, not the real one",
        ]
    unconfigured_note = (
        "  so there is no store to guard here and this check is guarding nothing: exit 0 "
        "is NOT evidence that a worker stayed out of a store"
        if guards_store
        else f"  no store is configured here, so {guarded_label} were excluded by name "
        "only — the uncommitted-work check above is unaffected and still means what it says"
    )
    return [
        layout.describe(),
        unconfigured_note,
        "  if this project is meant to have a store, run `kyo init` (which puts one "
        "outside this repository by default and writes the pointer to it here)",
    ]


# ------------------- KYO-128: which tree is this run of the guard actually about?


def _stop_target(ctx: Context, args: Namespace) -> tuple[StopTarget | None, str]:
    """The worktree this invocation should read, or ``None`` when not hook-driven.

    ``--subagent-stop`` is the only thing that reads stdin, and it is the flag the
    packaged hook passes. Run by hand the commands behave exactly as before: their
    subject is the repository you are standing in, which is correct and is not what
    KYO-128 is about.

    The raw payload is returned alongside the target because the stop record carries its
    ``session_id``/``agent_id``. Read here and passed on rather than read twice: stdin
    does not survive a second read (docs/design/guards.md#kyo-134).
    """
    if not getattr(args, "subagent_stop", False):
        return None, ""
    raw = "" if sys.stdin is None or sys.stdin.closed else sys.stdin.read()
    target = resolve_stop_target(ctx, raw)
    # KYO-592: kept on the invocation for :func:`unblocks_repeat_stops`, which sees the
    # refusal after this function's caller has raised it.
    args.resolved_stop = (target, raw)
    return target, raw


def _subject_lines(
    target: StopTarget | None, command: str, audit: str | None = None
) -> list[str]:
    """Name the tree that was read and say how it was chosen — always, both ways.

    A silent fallback to the cwd would report a verdict about a tree the check never
    examined, so the subject is stated on every hook-driven run, passing or failing
    (docs/design/guards.md#kyo-128).

    ``audit`` is KYO-134's line, rendered here so that it appears on every exit path
    this function already covers rather than on the ones someone remembered. It says
    whether this stop was written down, deliberately not written down, or failed to
    be — a durable record that quietly did not happen is worse than none.
    """
    if target is None:
        return []
    if target.outcome == STOP_TARGET_MATCHED:
        head = f"{command}: reading {target.directory} — the stopping worker's own worktree"
    elif target.outcome == STOP_TARGET_NO_WORKTREES:
        head = f"{command}: reading {target.directory} — the hook's own cwd"
    elif target.outcome == STOP_TARGET_NOT_A_WORKER:
        # KYO-154.c2. Not "NOTHING WAS CHECKED" — that sentence is for a stop this
        # guard could not place, and this one is placed. What it is placed as is a
        # stop with no worktree of its own to check, which is a different fact and
        # has to read as one or the vocabulary is doing no work.
        head = (
            f"{command}: NO VERDICT — the agent that stopped is a HELPER spawned by "
            f"{target.helper_of}'s worker, not that worker. It has no worktree of its "
            f"own; {target.directory} is its parent's and the parent is still working "
            "in it"
        )
    elif target.outcome == STOP_TARGET_NO_TRANSCRIPT:
        # KYO-373. Normally never rendered — both guards return one line of their own
        # first — but a vocabulary with an outcome it cannot name would print the
        # `else` below, "Fell back to reading", about a stop that read nothing.
        head = (
            f"{command}: NO VERDICT, and no directory checked — the agent that stopped "
            "has no transcript, so it is not a dispatched worker"
        )
    elif target.outcome == STOP_TARGET_STORE_UNREADABLE:
        # KYO-325. Same shape as the `else` below — nothing was checked, the cwd is
        # not known to be the worker's — with the cause named in the head rather than
        # only in `why`, because this is the one outcome whose own record cannot be
        # written (the store is what it would be written to), so the line a human
        # reads in the transcript is the only place it exists.
        head = (
            f"{command}: NOTHING WAS CHECKED about the stopping worker's tree, because "
            f"this project's STORE could not be read. Fell back to reading "
            f"{target.directory}, which is the hook's own cwd and is NOT known to be "
            "that worker's — and this stop cannot be recorded either"
        )
    else:
        head = (
            f"{command}: NOTHING WAS CHECKED about the stopping worker's tree. Fell back "
            f"to reading {target.directory}, which is the hook's own cwd and is NOT known "
            "to be that worker's"
        )
    lines = [head, f"  why this tree: {target.why}"]
    if target.candidates:
        lines.append(
            # Every dispatch that declared a worktree still on disk, whatever its state: a
            # worker outlives the ingest of its own report, so this list must never be
            # labelled "running" (docs/design/guards.md#kyo-201).
            "  dispatches with a declared worktree: " + ", ".join(target.candidates)
        )
    if audit is not None:
        lines.append(f"  durable record of this stop: {audit}")
    return lines


# ------- KYO-373: an agent with no transcript is not a worker, and has no tree


def _untranscribed_output(
    target: StopTarget,
    command: str,
    audit: str | None,
    data: dict,
) -> Output:
    """What either guard says for :data:`STOP_TARGET_NO_TRANSCRIPT`, having read NOTHING.

    An agent the harness kept no transcript for has no tree at all, and the only
    directory the guard could fall back to is the controller's own cwd, which these
    guards must never check. So the callers return this BEFORE any git command runs, in
    any directory (docs/design/guards.md#kyo-373).

    One line, because this fires on the ~32s tick (KYO-163) for every running agent:
    it is a notification about a row, and the row carries the ``why``.
    """
    data.update(
        {
            "ok": True,
            "subject": target.as_json(),
            "checked_the_worker_tree": False,
            # Nothing was read: no directory, no git. Said as data so a consumer need
            # not infer it from an empty file list, which a clean tree also produces.
            "checked_a_directory": False,
        }
    )
    recorded = f"; {audit}" if audit is not None else ""
    return Output(
        [
            f"{command}: NO VERDICT, and no directory checked — the agent that stopped "
            "has no transcript anywhere the harness writes one, so it is not a "
            f"dispatched worker (KYO-373){recorded}"
        ],
        data,
    )


def is_repeat_stop(raw_payload: str) -> bool:
    """Claude Code's ``stop_hook_active``: this stop follows one a stop hook blocked."""
    from .stop_target import _payload_of

    return _payload_of(raw_payload).get("stop_hook_active") is True


def unblocked_repeat_stop(
    ctx: Context,
    guard: str,
    target: StopTarget,
    raw_payload: str,
    refusal: GuardRefusal,
) -> Output:
    """KYO-592: a refusal on a repeat stop, reported and recorded instead of blocking.

    The first stop blocks with the full remedy; this is the stop after it, where the
    worker has already been told and blocking again only spends another wake (Claude
    Code's own backstop is eight). The refusal's own lines are printed unchanged, under
    a line saying they are not enforced, and the stop is written to the events ledger
    (:func:`kyosei.stop_audit.record_unblocked_stop`) so the orchestrator sees it in `kyo
    show <dispatch-id>` and `kyo report`. Exit 0.
    """
    from .stop_audit import record_unblocked_stop

    event_id, sentence = record_unblocked_stop(
        ctx,
        guard=guard,
        target=target,
        raw_payload=raw_payload,
        contract=refusal.contract,
        finding=list(refusal.unmet),
    )
    lines = [
        f"{guard}: NOT blocking: this stop already follows a blocked one (stop_hook_active), "
        f"so contract {refusal.contract} is reported here, not enforced -- orchestrator: "
        "read it before accepting this work (KYO-592)",
        *(f"  - {item}" for item in refusal.unmet),
        f"  unblocked stop: {sentence}",
    ]
    data = {
        **refusal.data,
        "repeat_stop": True,
        "blocked": False,
        "unblocked_contract": refusal.contract,
        "unblocked_stop_event": event_id,
        "unblocked_stop": sentence,
    }
    return Output(lines, data)


def unblocks_repeat_stops(guard: str):
    """KYO-592: a stop guard's command, with a refusal on a repeat stop turned into
    :func:`unblocked_repeat_stop`'s report. A decorator rather than a split of the
    command body, so every refusal keeps being raised inside the command that owns it.
    The stop is the one :func:`_stop_target` resolved for this invocation; a hand run
    resolves none and is never unblocked."""
    import functools

    def wrap(command):
        @functools.wraps(command)
        def run(ctx: Context, args: Namespace) -> Output:
            try:
                return command(ctx, args)
            except GuardRefusal as refusal:
                target, raw = getattr(args, "resolved_stop", None) or (None, "")
                if target is None or not is_repeat_stop(raw):
                    raise
                return unblocked_repeat_stop(ctx, guard, target, raw, refusal)

        return run

    return wrap
