"""``kyo dispatch`` — freezing a brief for one worker.

Resolving the worker model, refusing a finished record or a colliding worktree, and
generating the brief from the record, its touches and the project's standing rules.
Closing a dispatch lives in :mod:`kyosei.dispatch_close`, ingesting its report in
:mod:`kyosei.report_ingest`.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
from argparse import Namespace
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import brief as brief_module

# KYO-295: `cmd_next` is imported for the finished-but-not-moved class, which is
# `kyo next`'s and is read off that module here rather than re-derived. Safe at
# module scope in this direction only — `cmd_next` imports nothing that reaches
# back here.
from . import cmd_next, commands, docs_recx, explanations, export
from . import config as config_module
from . import procedures as procedures_module
from .clock import now
from .cmd_check_clean import take_baseline
from .commands import (
    Context,
    Output,
    _load_registry,
    _load_status_ladder,
    _read_worker_procedures,
    _touch_record,
)
from .contracts import (
    CONTRACT_DISPATCH_RECORD_BLOCKED,
    CONTRACT_DISPATCH_RECORD_FINISHED,
    CONTRACT_DISPATCH_SCOPE_UNKNOWN,
    CONTRACT_DISPATCH_TOUCH_DISJOINTNESS,
    CONTRACT_DISPATCH_WORKTREE_COLLISION,
    DISPATCH_STATE_RUNNING,
    SCOPE_UNKNOWN_REMEDY,
    TOUCH_COLLIDES,
    TOUCH_SCOPE_UNKNOWN,
    collision_detail,
    is_directory_touch,
    scope_unknown_detail,
    touch_covers,
)
from .dispatch_close import _refuse_close_only_flags, cmd_dispatch_close
from .dispatch_docs import numbered_document_rows
from .errors import ContractViolation, KyoseiError, UsageError
from .git_queries import (
    _git_rev_parse_head,
    branch_delete,
    checked_out_branch,
    local_branch_sha,
    registered_linked_worktrees,
    resolve_commit,
    worktree_add,
    worktree_remove,
)
from .prose import fingerprint, read_prose
from .render import echo_block, echo_value
from .touch_drift import (
    _cooccurrence_lines,
    declaration_provenance,
    provenance_lines,
    touch_symbol_lines,
)

#: KYO-27: the syntactic shapes a hand-typed measurement takes.
#: The brief freezes the instant it is generated, so this warning has exactly one
#: useful moment to fire — and this error class is always the same shape: a bare
#: count or duration a controller copied from a doc instead of measuring
#: (docs/design/dispatch.md#kyo-27).
_MEASUREMENT_RE = re.compile(
    r"(?<![\w.])(?:~\s*)?\d[\d,]*\s*(?:tests?|lines?|files?|records?|rows?|"
    r"ms|s\b|sec|secs|seconds?|m\b|min|mins|minutes?)",
    re.IGNORECASE,
)


def _normalised_measurement(value: str) -> str:
    """One measurement reduced to the form two spellings of it share (KYO-348).

    Whitespace out (``15 s`` and ``15s`` are the same figure), case down (``15S``),
    and a leading approximation marker off (``~20s`` states the same figure as
    ``20s`` — the tilde is the controller hedging, not part of the number). Commas
    are deliberately KEPT: ``1,019`` and ``1019`` are the same quantity but not the
    same spelling, and a criterion that states one has not stated the other.
    """
    return "".join(value.split()).lstrip("~").lower()


def _measurements_in(text: str) -> list[str]:
    """Every count or duration ``text`` states, in order, without repeats."""
    found: list[str] = []
    for match in _MEASUREMENT_RE.finditer(text):
        value = match.group(0).strip()
        if value not in found:
            found.append(value)
    return found


def _measurement_warnings(prose: str, stated: Iterable[str] = ()) -> list[str]:
    """Bare counts and durations in controller-written task prose.

    Warns, never refuses (charter: "flags, never enforces"). A measured number is
    perfectly legitimate — the warning asks whether it *was* measured, at the last
    moment anyone can still answer.

    KYO-348: ``stated`` is the criterion text of the dispatched record and of every
    record named with ``--also-covers``, and a figure appearing verbatim in one of
    them is not warned about: a number the record's own criteria state IS the source,
    where a number copied from a doc has a source that can be stale, and the criteria
    are the only place in reach that can tell a design constant from a remembered
    measurement (docs/design/dispatch.md#kyo-348).

    The comparison is measurement-to-measurement, not substring-to-text, and that is
    load-bearing: ``5s`` occurs inside ``15s``, so a criterion mentioning fifteen
    seconds would silence a warning about five. Both sides are run through the SAME
    :data:`_MEASUREMENT_RE` the warning itself is built on (law 3), so a figure this
    check cannot see in the prose is not one it can accidentally match in a criterion.
    """
    quiet = {_normalised_measurement(v) for text in stated for v in _measurements_in(text)}
    return [v for v in _measurements_in(prose) if _normalised_measurement(v) not in quiet]


#: KYO-61: where a resolved worker model came from, named once so the dispatch echo,
#: the frozen brief and ``--json`` are three renderings of one answer rather than three
#: places that can disagree (law 3). ``MODEL_SOURCE_NONE`` is a first-class outcome, not
#: a shrug: "no model was declared anywhere" is the state every dispatch was in
#: before this field existed, and it must never render as a chosen value.
MODEL_SOURCE_DISPATCH = "dispatch"
MODEL_SOURCE_KIND = "kind"
MODEL_SOURCE_PROJECT = "project"
MODEL_SOURCE_NONE = "none"

#: How each source reads in a sentence. Keyed by the constants above so adding a
#: fourth source without a description is a KeyError at the call site rather than a
#: blank in a frozen brief.
MODEL_SOURCE_LABELS: dict[str, str] = {
    MODEL_SOURCE_DISPATCH: "declared for this dispatch (`kyo dispatch --model`)",
    MODEL_SOURCE_KIND: "this kind's policy.worker_model in kyosei/kinds.yml",
    MODEL_SOURCE_PROJECT: "this project's worker_model in kyosei/config.yml",
    MODEL_SOURCE_NONE: (
        "nothing declared one — not kyosei/config.yml, not this kind's "
        "policy.worker_model, not `kyo dispatch --model`"
    ),
}


def resolve_worker_model(
    config, kind_def, override: str | None = None
) -> tuple[str | None, str]:
    """KYO-61: which model this dispatch runs on, and which declaration said so.

    Three layers, most specific first: ``kyo dispatch --model`` beats the kind's
    ``policy.worker_model`` beats ``kyosei/config.yml``'s ``worker_model``. The
    project-level default is the base because the choice is a *project* property —
    a project's tolerance for cost, and its sense of which work needs a stronger
    model, does not change between sessions, and a controller re-deciding it from
    scratch each session is the same failure as any other unrecorded standing
    decision. The per-kind layer exists because the kind registry already carries
    per-kind dispatch policy (``dispatchable``, ``unattended_ok``, ``write_scope``)
    and because what a *brief asks for* — an argument, or a mechanical fix fully
    specified by its criteria — tracks the kind more closely than it tracks a
    record's size.

    Returns ``(model, source)``; ``model`` is ``None`` exactly when ``source`` is
    :data:`MODEL_SOURCE_NONE`. The pair is returned rather than the value alone so
    that no caller has to re-derive "and where did that come from" — the two facts
    are one computation, and the brief states both.

    **This is not enforcement, and cannot become it.** Kyosei does not spawn
    workers; the controller's harness does, and no ``kyo`` command can make a
    harness honour a value in a config file. So this resolves, records and states —
    and ``kyosei/procedures.md`` says ``enforced-by: aspirational`` verbatim, the
    same standing ``write_scope`` already has. Checking it against a worker's own
    report was considered and refused on the record (KYO-32's lesson): a worker
    reporting the condition its own dispatch declared is a second, weaker source of
    truth for one fact, and law 3 says the two then get to disagree.
    """
    if override and override.strip():
        return override.strip(), MODEL_SOURCE_DISPATCH
    from_kind = kind_def.policy.get("worker_model") if kind_def is not None else None
    if isinstance(from_kind, str) and from_kind.strip():
        return from_kind.strip(), MODEL_SOURCE_KIND
    from_project = getattr(config, "worker_model", None)
    if isinstance(from_project, str) and from_project.strip():
        return from_project.strip(), MODEL_SOURCE_PROJECT
    return None, MODEL_SOURCE_NONE


def _not_running(store, dispatch_id: str) -> bool:
    """KYO-535: may this other dispatch's baseline be removed? Only when THIS store
    knows the dispatch and it has left ``running`` -- an id it does not know may be
    another store's sharing the code repository, and is left alone."""
    row = store.conn.execute(
        "SELECT state FROM dispatches WHERE id = ?", (dispatch_id,)
    ).fetchone()
    return row is not None and row["state"] != DISPATCH_STATE_RUNNING


def _refuse_a_finished_record(ctx: Context, record: Mapping, *, force: bool) -> dict | None:
    """KYO-295: refuse to dispatch a record `kyo next` has already withheld as
    finished-but-not-moved, and return the class row when ``--force`` overrode it.

    `kyo next` withholds this class (KYO-87, widened by KYO-289); `kyo dispatch` must not
    freeze a brief for a record that surface calls finished, or a worker is sent to
    build something whose own criteria already say it is built
    (docs/design/dispatch.md#kyo-295).

    This is KYO-48's shape reused exactly: that record made `kyo dispatch` refuse an
    undeclared scope off ``commands.touch_standing`` -- *the same function* `kyo next`
    filters with -- rather than off a second implementation of "is this safe?". Here
    the shared function is :func:`cmd_next.settled_not_moved_entry`, and the refusal
    prints the class's own ``needs`` string, so the move this names and the move the
    listing names are one value rather than two that agree today.

    ``--force`` still dispatches, like every other refusal in this command: the
    controller may know the record is genuinely unfinished and the criteria stale,
    and the override is recorded on the dispatch rather than argued about here. The
    returned row is what the caller records and echoes; ``None`` means the record is
    not in the class at all, which is the ordinary case.

    One thing the shared function makes visible rather than fixes, worth knowing
    before reading the remedy: the class's named move is the move that *lets the
    class go from where the record now stands*, and for a record below the verified
    rung that is the verified rung, which leaves it in the class one rung higher
    (KYO-173's two-step, and why naming ``verified`` from ``verified`` was a no-op).
    So the refusal a record at ``open`` prints names the move that clears it in two,
    and the refusal a record at ``verified`` prints names the move that clears it in
    one. That is the listing's behaviour, byte for byte, which is the point.
    """
    store = ctx.store()
    ladder = _load_status_ladder(store)
    entry = cmd_next.settled_not_moved_entry(
        ladder,
        record,
        store.criteria(record["id"]),
        cmd_next._reported_dispatches_by_record(store).get(record["id"], []),
    )
    if entry is None:
        return None
    if force:
        return entry
    reporters = (
        f" ({', '.join(entry['reported_by'])} reported)" if entry["reported_by"] else ""
    )
    raise ContractViolation(
        CONTRACT_DISPATCH_RECORD_FINISHED,
        [
            f"{record['id']} has {len(store.criteria(record['id']))} criteria and every "
            f"one is settled{reporters}, so there is nothing left to build and "
            f"`kyo next` withholds it from its ready list under \"finished, not moved\" "
            f"(KYO-87, KYO-289) — it needs the status move, not a worker",
            f"run `{entry['needs']}`, or pass --force to dispatch it anyway",
        ],
    )


def _refuse_a_blocked_record(ctx: Context, record: Mapping, *, force: bool) -> list[dict]:
    """KYO-570: refuse to dispatch a record an unsettled ``blocks`` source holds back,
    and return the blockers ``--force`` overrode (``[]`` when there were none).

    Off :func:`cmd_next.unsettled_blockers` -- the function `kyo next` and the brief
    withhold the record with -- for KYO-295's reason: two implementations of "may this
    start?" get to disagree, and one does not. The refusal names each blocker, its
    status and title, and ``--force``.
    """
    store = ctx.store()
    blockers = cmd_next.unsettled_blockers(store, _load_status_ladder(store), record["id"])
    if not blockers or force:
        return blockers
    raise ContractViolation(
        CONTRACT_DISPATCH_RECORD_BLOCKED,
        [
            f"{record['id']} is blocked by {b['id']} (status {b['status']}, not settled) "
            f"through link {b['link_id']}: {b['title']}"
            for b in blockers
        ]
        + [
            f'`kyo next` withholds {record["id"]} under "blocked" until every blocker '
            f"settles (KYO-570); settle each blocker (move it to a complete or retired "
            f"status), retract an edge that is wrong with "
            + ", ".join(f"`kyo unlink {b['link_id']} --why TEXT`" for b in blockers)
            + ", or pass --force to dispatch it anyway"
        ],
    )


def _git_out(cwd: Path, *git_args: str) -> str | None:
    """Stripped stdout of ``git <args>`` in ``cwd``, or ``None`` if it failed (KYO-402)."""
    from .git_queries import run_git

    try:
        result = run_git(cwd, *git_args)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _refuse_an_invalid_branch_name(code_root: Path, branch: str) -> None:
    """KYO-402.c6: ``--branch`` must be a name git would create a branch under, and must
    not make :func:`kyosei.config.default_worktree_dir` step out of the worktree home."""
    if branch in (".", "..") or _git_out(code_root, "check-ref-format", "--branch", branch) is None:
        raise UsageError(
            f"--branch {branch!r} is not a valid branch name (`git check-ref-format "
            "--branch` rejects it); the dispatch's worktree is derived from this name, so "
            "pass a name git would accept for `git worktree add -b <name>`"
        )


def _refuse_a_foreign_worktree(code_root: Path, directory: Path, given: str) -> None:
    """KYO-402.c7: ``--worktree`` must be its own git toplevel whose ``--git-common-dir``
    is code_root's — that is, a worktree ``git worktree add`` made of this repository."""
    remedy = (
        "create it with `git worktree add <path>` from the code repository "
        f"({code_root}), then dispatch into it"
    )
    toplevel = _git_out(directory, "rev-parse", "--show-toplevel")
    if toplevel is None or Path(toplevel).resolve() != directory.resolve():
        raise UsageError(
            f"--worktree {given} is not the top of a git worktree"
            + (f" (it is inside {toplevel})" if toplevel else " (git does not see one there)")
            + f"; {remedy}"
        )
    ours = _git_out(code_root, "rev-parse", "--git-common-dir")
    theirs = _git_out(directory, "rev-parse", "--git-common-dir")
    if (
        ours is None
        or theirs is None
        or (code_root / ours).resolve() != (directory / theirs).resolve()
    ):
        raise UsageError(
            f"--worktree {given} is a git checkout, but not a worktree of {code_root} "
            "(its `git rev-parse --git-common-dir` differs) — a separate clone, or "
            f"another repository; {remedy}"
        )


def _resolve_base(ctx: Context, args: Namespace, branch: str | None) -> str | None:
    """KYO-485 (a consumer report): ``--base <ref>`` declares where the dispatch's
    work starts, for a re-dispatch deliberately cut from somewhere other than the code
    repository's HEAD (a predecessor's unmerged branch, say). The commit it names
    becomes the dispatch's ``worked_from_sha`` -- the same stored field every dispatch
    already carries, so no schema change -- and the start point of the worktree
    `kyo dispatch` creates. ``None`` when not given. Refused before any row exists."""
    given = getattr(args, "base", None)
    if given is None:
        return None
    ref = given.strip()
    if not ref:
        raise UsageError("--base was given an empty ref; pass a branch, tag or sha, or omit it")
    if not (branch or args.worktree):
        raise UsageError(
            "--base names where this dispatch's worktree starts, and a dispatch with "
            "neither --branch nor --worktree has no worktree of its own (it shares the "
            "project root); pass --branch NAME with it, or omit --base"
        )
    sha = resolve_commit(ctx.code_root, ref)
    if sha is None:
        raise KyoseiError(
            f"--base {ref!r} does not resolve to a commit in the code repository "
            f"({ctx.code_root}); `git rev-parse --verify {ref}^{{commit}}` fails there. "
            "Nothing was dispatched. Pass a branch, tag or sha that exists in that "
            "repository (`git fetch` it first if it lives on a remote)"
        )
    return sha


@dataclass
class _WorktreeCreation:
    """KYO-485: the ``git worktree add`` a ``--branch`` dispatch performs, planned
    before the transaction opens (every refusal it can draw is drawn there) and run
    inside it, after every other refusal and immediately before the dispatch row is
    written -- so a refused dispatch creates nothing and a failed ``git worktree
    add`` leaves no row behind.

    Used as a context manager OUTSIDE ``store.transaction()``, so its ``__exit__``
    sees anything that failed after the worktree was made, the transaction's own
    commit included, and removes the worktree (and the branch, when this call cut it)
    rather than leave a directory no dispatch row names. If that removal itself
    fails, the error says so and names the directory.
    """

    code_root: Path
    path: Path
    branch: str
    #: the sha a NEW branch is cut at; ``None`` when ``branch`` already exists and is
    #: checked out as it stands.
    start_point: str | None
    record_id: str
    created: bool = False

    def command(self) -> str:
        tail = (
            f"-b {shlex.quote(self.branch)} {self.start_point}"
            if self.start_point is not None
            else shlex.quote(self.branch)
        )
        return f"git worktree add {shlex.quote(str(self.path))} {tail}"

    def run(self) -> None:
        ok, message = worktree_add(
            self.code_root, self.path, self.branch, start_point=self.start_point
        )
        if not ok:
            raise KyoseiError(
                f"`{self.command()}` failed in the code repository ({self.code_root}): "
                f"{message}. Nothing was dispatched -- no dispatch row exists for "
                f"{self.record_id} from this call. Fix what git names (a branch checked "
                "out in another worktree is the usual cause), or pass --worktree DIR to "
                "dispatch into a worktree you made yourself"
            )
        self.created = True

    def __enter__(self) -> _WorktreeCreation:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None or not self.created:
            return False
        removed, why = worktree_remove(self.code_root, self.path)
        if removed and self.start_point is not None:
            branch_delete(self.code_root, self.branch)
        if not removed:
            raise KyoseiError(
                f"dispatching {self.record_id} failed after its worktree was created "
                f"({exc}); no dispatch row was written, and the worktree {self.path} "
                f"could not be removed ({why}). Remove it with `git worktree remove "
                f"{shlex.quote(str(self.path))}` in {self.code_root} before dispatching again"
            ) from exc
        return False


def _plan_the_worktree(
    ctx: Context, path: Path, branch: str, record_id: str, base_sha: str | None,
    worked_from_sha: str,
) -> _WorktreeCreation | None:
    """KYO-485.c1/c3/c5: decide what ``kyo dispatch --branch`` does about its derived
    worktree, refusing everything refusable before any row is written. ``None`` means
    reuse: ``path`` is already a registered worktree of this repository with
    ``branch`` checked out, which is what a create would have produced."""
    if path.exists() or path.is_symlink():
        registered = registered_linked_worktrees(ctx.code_root) or set()
        if path.resolve() in registered and checked_out_branch(path) == branch:
            return None
        if path.resolve() in registered:
            what = (
                f"a worktree of {ctx.code_root} with "
                f"{checked_out_branch(path) or 'a detached HEAD'} checked out, not {branch}"
            )
        else:
            what = f"not a registered worktree of {ctx.code_root}"
        raise KyoseiError(
            f"the worktree path {path} already exists and is {what}; a dispatch on "
            f"--branch {branch} creates its worktree there and will neither adopt nor "
            "overwrite another directory. Nothing was dispatched. Pass --worktree DIR to "
            "dispatch into an existing worktree of this repository, or a different "
            "--branch NAME so the derived path is a fresh one"
        )
    tip = local_branch_sha(ctx.code_root, branch)
    if tip is None:
        return _WorktreeCreation(ctx.code_root, path, branch, worked_from_sha, record_id)
    if base_sha is not None and tip != base_sha:
        raise KyoseiError(
            f"--branch {branch} already exists at {tip}, and --base names {base_sha}; "
            f"the worktree would check out {branch} as it stands, not start at the base. "
            "Nothing was dispatched. Drop --base to work on the existing branch, or name a "
            "new --branch to have it cut at the base"
        )
    return _WorktreeCreation(ctx.code_root, path, branch, None, record_id)


def _broad_claim_warnings(
    store, record_id: str, also_covers: Iterable[str], ladder
) -> list[dict]:
    """KYO-539.c1: directory touches of this dispatch that also contain a touch of
    another OPEN record. Warns, never refuses: a broad claim is sometimes right, and the
    refusing check (running dispatches only) is untouched. What is cheap to fix is the
    declaration, so each entry carries the suggestion. Open means the record's status
    role is not settled; a status the registry no longer knows counts as open.
    """
    own = {record_id, *also_covers}
    mine = [
        row["path"]
        for rid in sorted(own)
        for row in store.touches(rid)
        if is_directory_touch(row["path"])
    ]
    if not mine:
        return []
    found: dict[str, dict[str, set[str]]] = {}
    for rec in store.conn.execute("SELECT id, status FROM records ORDER BY id"):
        if rec["id"] in own or ladder.is_settled(rec["status"]):
            continue
        for row in store.touches(rec["id"]):
            for directory in mine:
                if touch_covers(directory, row["path"]):
                    found.setdefault(directory, {}).setdefault(rec["id"], set()).add(
                        row["path"]
                    )
    return [
        {"directory": d, "records": {r: sorted(p) for r, p in sorted(by.items())}}
        for d, by in sorted(found.items())
    ]


def _broad_claim_lines(warnings: list[dict]) -> list[str]:
    """KYO-547: one line per claimed directory, each other open record as its id and
    file count, then the advice once. The per-file listing lives only in ``--json``
    (``broad_claim_warnings``): sixteen records' files per directory, repeated on
    every dispatch, ran past a screen and went unread."""
    if not warnings:
        return []
    lines: list[str] = []
    for w in warnings:
        recs = w["records"]
        who = ", ".join(
            f"{r} ({len(p)} file{'' if len(p) == 1 else 's'})" for r, p in recs.items()
        )
        lines.append(
            f"  warning: this record claims the directory {w['directory']}, which also "
            f"contains touches of {len(recs)} other open record"
            f"{'' if len(recs) == 1 else 's'}: {who}"
        )
    example = warnings[0]["directory"]
    lines.append(
        "  A directory claim serialises every other record in that tree; write "
        f"`+<directory><file>` (e.g. `+{example}<file>`) for each file this work creates "
        "and the named files for the ones it edits. The files themselves are in "
        "`--json` under broad_claim_warnings (the dispatch was NOT refused)"
    )
    return lines


def _cmd_dispatch_create(ctx: Context, args: Namespace) -> Output:
    """``kyo dispatch <record-id> [--force] [--brief-out FILE] [--task-file FILE]``.

    KYO-7.c1/c2: freezes a brief generated from the record and checks touch
    disjointness against every other *running* dispatch before doing so. Record
    moves are never implied — dispatch state is its own dimension (Core schema).
    """
    store = ctx.store()
    record_id = args.target
    record = store.record(record_id)
    registry = _load_registry(store)
    kind_def = registry.require(record["kind"])
    if kind_def.archetype != "work" or not kind_def.policy.get("dispatchable", False):
        raise KyoseiError(
            f"{record_id} is kind {record['kind']!r}; its policy.dispatchable is not true in "
            "kyosei/kinds.yml, so it cannot be dispatched"
        )
    # KYO-295: checked here, beside the kind-policy refusal and before anything is
    # frozen — a record with nothing left to build is not a candidate whose scope is
    # worth comparing, which is the same order `_ready_records` puts these in.
    finished = _refuse_a_finished_record(ctx, record, force=args.force)
    # KYO-570: after the finished refusal, as `_ready_records` orders the classes.
    blocked_by = _refuse_a_blocked_record(ctx, record, force=args.force)

    # KYO-54: the *code* repository's HEAD, which under the split layout is not the
    # store's. Stamping the store repo's HEAD here would be confidently wrong: a
    # worker branches from the code, and every later ancestry check compares
    # against what this records.
    worked_from_sha = _git_rev_parse_head(ctx.code_root)
    task_prose = read_prose(args.task_file, what="--task-file") if args.task_file else ""

    # KYO-32. Declared exactly like `--worktree`: `kyo dispatch` runs in the
    # controller's checkout, so `git rev-parse --abbrev-ref HEAD` here would read
    # the controller's own branch with total confidence and be wrong. Unlike
    # `--worktree` there is deliberately NO default — the project root is a real
    # answer to "where", but there is no honest default answer to "which branch",
    # and inventing one (say, the controller's current branch) is exactly the
    # confident-and-wrong failure this record exists to end. Undeclared stays NULL,
    # and `kyo report` says so rather than checking against a guess.
    #
    # Existence is NOT checked here, on purpose: a declared branch that this repo
    # cannot resolve is one of the "cannot tell" cases the check at report time is
    # required to surface as its own outcome, so refusing it at dispatch time
    # would move a law-6 decision into a place with less information about it.
    #
    # KYO-70 moved this above the worktree block, because the derived default
    # worktree is named after the branch.
    branch = args.branch.strip() if args.branch else None
    if args.branch is not None and not branch:
        raise UsageError("--branch was given an empty name; pass a branch name or omit the flag")
    if branch:
        # KYO-402: existence is still not checked (above), but SHAPE is. The derived
        # worktree is named after the branch, and `--branch ..` derived the worktree
        # home's parent — the store root, `.git` and all — which a later prune then
        # deleted. A name git itself would refuse to create is refused here, before any
        # store write, rather than recorded and acted on.
        _refuse_an_invalid_branch_name(ctx.code_root, branch)

    # KYO-485: after the branch (``--base`` needs one, or a ``--worktree``) and before
    # the worktree block, which cuts a new branch at the sha this returns.
    base_sha = _resolve_base(ctx, args, branch)
    if base_sha is not None:
        worked_from_sha = base_sha
    create_worktree = not getattr(args, "no_create_worktree", False)

    # KYO-22. Declared by the controller, never sniffed: `kyo
    # dispatch` runs in the controller's cwd, which is normally the main checkout
    # while the worker is sent somewhere else, so reading the current worktree
    # would record the wrong place with total confidence.
    #
    # KYO-52: stored relative (`contracts.store_path`), never resolved to an
    # absolute path. `kyosei/export/` is git-canonical, so an absolute path here
    # is permanent, machine-specific and carries a username; `../kyosei-wt-31`
    # carries the same fact without the identity. A sibling worktree — the normal
    # case — has a `..` relative form, so it is stored, not refused. Display
    # resolves (see the echo below); storage does not.
    #
    # KYO-54 + KYO-52, resolved together at merge: the anchor is `code_root`, not
    # `root`. A worktree is a CODE location — it is where a worker branches and
    # commits — so under the split layout it must be expressed relative to the
    # code repository, not to a store that may live under ~/kyosei. The two are
    # the same path under the in-repo layout, so this changes nothing there.
    #
    # KYO-70: with a branch declared and no `--worktree`, the path is DERIVED
    # under this project's Kyosei home (`~/kyosei/<project>/dispatch worktrees/<branch>`)
    # rather than demanded from the controller — which is what made every worktree
    # this tool has ever used a sibling of the code repository, in a directory the
    # tool was never given. With no branch either, the old project-root default
    # stands and is load-bearing: that default IS the shared-checkout case, so two
    # undeclared dispatches still collide, which is exactly the situation that cost
    # one consumer three separate failures. Nothing here weakens that; it gives the
    # *declared* case a home.
    derived_worktree = False
    base_is_branch_tip = False
    creation: _WorktreeCreation | None = None
    reused_worktree = False
    if args.worktree:
        if not Path(args.worktree).expanduser().is_dir():
            raise KyoseiError(
                f"--worktree {args.worktree} is not a directory; create the worktree first "
                "(e.g. `git worktree add`), then dispatch into it"
            )
        worktree_dir: Path = Path(args.worktree).expanduser()
        # KYO-402: a directory, and specifically a worktree of THIS repository — its own
        # toplevel, sharing code_root's object store. A subdirectory of the checkout or
        # a separate clone was accepted before, recorded, and later deleted by a prune.
        _refuse_a_foreign_worktree(ctx.code_root, worktree_dir, args.worktree)
    elif branch:
        worktree_dir = config_module.default_worktree_dir(
            ctx.worktree_project_name, branch, ctx.environ
        )
        derived_worktree = True
        # The home, which `git worktree add` needs to exist — and creating it here is
        # what makes "an in-repo project gets a Kyosei home solely for worktrees"
        # (KYO-70.c5) a thing that happens rather than a thing that is documented.
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        if create_worktree:
            # KYO-485: planned (and refused) here, run just before the row is written.
            creation = _plan_the_worktree(
                ctx, worktree_dir, branch, record_id, base_sha, worked_from_sha
            )
            reused_worktree = creation is None
            # KYO-487: a worktree that checks out an EXISTING branch as it stands (a
            # `.d2` re-dispatch onto its predecessor's branch, from a consumer report)
            # starts at that branch's tip, not at the code repository's HEAD -- so the
            # tip is the base this dispatch records and its brief states. With
            # `--base` the plan above has already refused any tip that differs.
            if base_sha is None and (creation is None or creation.start_point is None):
                tip = local_branch_sha(ctx.code_root, branch)
                if tip is not None:
                    worked_from_sha = tip
                    base_is_branch_tip = True
    else:
        worktree_dir = ctx.code_root
    worktree = ctx.store_worktree(worktree_dir)
    worktree_resolved = ctx.resolve_worktree(worktree)

    # KYO-61. Resolved here, beside `--worktree` and `--branch`, because it is the
    # same kind of thing: a condition of the work, declared by the controller,
    # recorded on the dispatch and stated in the frozen brief. Unlike those two it
    # is never checked afterwards, and the brief says so in the same breath as it
    # says the name — a field the tool records but cannot verify has to admit that,
    # or it becomes the decoration law 2 exists to prevent.
    if args.model is not None and not args.model.strip():
        raise UsageError("--model was given an empty name; pass a model name or omit the flag")
    worker_model, model_source = resolve_worker_model(store.config, kind_def, args.model)

    # KYO-13.c4 / KYO-12: a dispatch may formally declare additional records it
    # covers. Each must exist (store.record raises NotFound loudly, law 6) and
    # must not just repeat the primary record.
    also_covers: list[str] = []
    for covered_id in args.also_covers or []:
        if covered_id == record_id:
            raise UsageError(
                f"--also-covers names {covered_id!r}, which is already this dispatch's own "
                "record; it is covered implicitly and does not need to be repeated"
            )
        store.record(covered_id)  # existence check; raises NotFound if unknown
        if covered_id not in also_covers:
            also_covers.append(covered_id)

    # KYO-348: computed HERE rather than beside `task_prose` above, because the set of
    # records whose criteria may state a figure is not known until `--also-covers` has
    # been resolved — and the brief a controller writes task prose for is the brief
    # covering all of them. Same records, same order, as the brief's own criteria
    # tables; reading only the primary record's criteria would go on warning about a
    # figure an also-covered record's criterion states verbatim, which is the KYO-12
    # asymmetry ("a dispatch either formally declares what it covers, or it does not")
    # reappearing in a warning.
    measurements = _measurement_warnings(
        task_prose,
        stated=[
            row["text"]
            for cid in (record_id, *also_covers)
            for row in store.criteria(cid)
        ],
    )

    with creation or contextlib.nullcontext(), store.transaction():
        before = {record_id: export.record_document(store, record_id)}
        covered_docs = [export.record_document(store, cid) for cid in also_covers]

        # KYO-132.c5, moved above the touch check by KYO-147.c1: read once, used
        # four times now. The disjointness check, the narrowing statement, the
        # declaration-provenance statement, the worktree-collision check and the
        # brief's concurrency bullet are five consumers of ONE fact — "which other
        # dispatches are running right now" — and law 3 says two reads of it get to
        # disagree. Here that would mean refusing a dispatch for contention the brief
        # then told the worker did not exist, or stating provenance about a set that
        # is not the set anything was compared against, which is precisely the defect
        # this record was filed for.
        all_running = store.running_dispatches()
        others_running = [row for row in all_running if row["record_id"] != record_id]
        # KYO-411: this record's OWN running dispatches, kept apart
        # from `others_running` rather than folded into it. The touch-disjointness check
        # must not see them — a record's touches trivially overlap themselves, so every
        # re-dispatch would be refused as a collision with itself — but the worktree
        # collision check below MUST: a second `kyo dispatch KYO-x` while `KYO-x.d1` is
        # still running derives the very same `<worktrees>/<branch>` path, and before
        # this it could not see `.d1` at all, so it minted `.d2` into the same checkout,
        # branch and index and reported success.
        own_running = [row for row in all_running if row["record_id"] == record_id]
        # KYO-48: three outcomes, not two. `kyo next` sorts records with the same
        # call, so the list a controller is offered and the dispatches this refuses
        # are answers to one question asked once (commands.touch_standing).
        #
        # KYO-65: `also_covers` is passed in because this dispatch does not exist
        # yet — the records it is about to formally cover are part of what it
        # claims, and there is nowhere else to read them from at this instant.
        verdict, collisions, unknown_scope = commands.touch_standing(
            store, record_id, running=others_running, also_covers=also_covers
        )
        # KYO-147.c5: overlaps that did not become a refusal because both sides were
        # symbol-precise and disjoint. Read from the same pass, off the same running
        # set, so what is stated and what was decided cannot be two answers.
        narrowings = commands.touch_narrowings(
            store, record_id, running=others_running, also_covers=also_covers
        )
        broad_claims = _broad_claim_warnings(
            store, record_id, also_covers, _load_status_ladder(store)
        )
        if verdict == TOUCH_COLLIDES and not args.force:
            raise ContractViolation(
                CONTRACT_DISPATCH_TOUCH_DISJOINTNESS,
                [f"{collision_detail(c)} — pass --force to override" for c in collisions],
            )
        if verdict == TOUCH_SCOPE_UNKNOWN and not args.force:
            # Not a collision — nothing is known to overlap. That is the point: an
            # undeclared scope silently *passed* this check until KYO-48, so the
            # tool reported "not colliding" about a comparison it never made.
            raise ContractViolation(
                CONTRACT_DISPATCH_SCOPE_UNKNOWN,
                [scope_unknown_detail(u) for u in unknown_scope]
                + [SCOPE_UNKNOWN_REMEDY],
            )

        # A dispatch created before this column existed has worktree NULL: unknown,
        # not "the same place as everything else", so it never triggers a refusal.
        #
        # Compared *resolved*, not as stored strings (KYO-52): a row written before
        # schema_version 9 holds an absolute path and this one holds a relative one,
        # and two spellings of the same directory must still collide — the whole
        # point of the check is that it never says "not colliding" about two workers
        # in one checkout.
        concurrent = [
            {"dispatch_id": row["id"], "record_id": row["record_id"]} for row in others_running
        ]
        sharing = [
            row
            # KYO-411: every running dispatch, this record's own included (see
            # `own_running` above) — two workers in one checkout is the same hazard
            # whether or not they happen to be working on the same record.
            for row in [*own_running, *others_running]
            if row["worktree"] is not None
            # KYO-70: resolved through the Context, so a row holding the
            # `<worktrees>` token, a KYO-52 relative path and a pre-9 absolute path
            # are all compared as directories. Three spellings of one place must
            # still collide — the whole point of this check is that it never says
            # "not colliding" about two workers in one checkout.
            and ctx.resolve_worktree(row["worktree"]) == worktree_resolved
        ]
        if sharing and not args.force:
            raise ContractViolation(
                CONTRACT_DISPATCH_WORKTREE_COLLISION,
                [
                    f"{row['id']} (dispatching {row['record_id']}) is already running in "
                    f"{worktree} ({worktree_resolved}) — two dispatches in one checkout "
                    "share a branch and an "
                    "index, so one worker's commit can land on the other's branch. "
                    + (
                        # KYO-411: a re-dispatch of the same record is the likely retry
                        # after a crash, so say that the earlier one still exists.
                        f"{row['id']} is this record's own earlier dispatch and it is "
                        "still running; read it with `kyo show "
                        f"{row['id']}` before creating another. "
                        if row["record_id"] == record_id
                        else ""
                    )
                    + "Give "
                    "this one its own `git worktree add` path via --worktree, or pass "
                    "--force to override"
                    for row in sharing
                ],
            )

        # KYO-411: `--brief-out` is written only after the dispatch row
        # has committed, so a path that cannot be written used to surface as a raw OSError
        # traceback AFTER the dispatch existed and BEFORE any line named it — and the
        # controller, seeing a crash, ran the command again. Checked here, inside the
        # transaction but before anything is written (a refusal rolls back), so the
        # common failures (a missing directory, a directory given as the file, a
        # read-only file or directory) refuse with no dispatch created. The write itself
        # is still guarded below for whatever this check cannot foresee.
        brief_out_path = Path(args.brief_out) if args.brief_out else None
        if brief_out_path is not None:
            import os

            parent = brief_out_path.parent
            problem = None
            if brief_out_path.is_dir():
                problem = "it is a directory, not a file"
            elif not parent.is_dir():
                problem = f"its directory {parent} does not exist"
            elif brief_out_path.exists() and not os.access(brief_out_path, os.W_OK):
                problem = "the file exists and is not writable"
            elif not brief_out_path.exists() and not os.access(parent, os.W_OK | os.X_OK):
                problem = f"its directory {parent} is not writable"
            if problem is not None:
                raise KyoseiError(
                    f"--brief-out {args.brief_out} cannot be written: {problem}. Nothing was "
                    f"dispatched — no dispatch row exists for {record_id} from this call. Pass "
                    "a path in an existing, writable directory (or omit --brief-out and read "
                    "the brief with `kyo show <dispatch-id>`)"
                )

        dispatch_id, seq = store.new_child_id(record_id, "dispatch")
        # KYO-395.c1: a let-through overlap that is a WARNING names both dispatches,
        # and this one's id exists only from this line on. Stamped onto the entries
        # before the brief is frozen, so the terminal, `--json` and the stored brief
        # (which `kyo show` reads back, c3) all carry the same sentence.
        for entry in commands.narrowing_warnings(narrowings):
            entry["candidate_dispatch_id"] = dispatch_id
        stamp = now()
        doc = export.record_document(store, record_id)
        rules = procedures_module.worker_rules(_read_worker_procedures(store))
        # KYO-76: the brief's Touches section renders THIS claim — the same
        # per-record path sets `touch_standing` compared against every other running
        # dispatch a few lines up — rather than the primary record's touches alone.
        # Before this, a worker on a two-record dispatch was handed the criteria of
        # both records and the touches of one, and the write-scope statement below it
        # then told the worker its boundary excluded files its own criteria required.
        claim = commands._claim_of(store, record_id, also_covers)
        # KYO-147.c1/c2/c5: three answers this store could already give and never
        # did, computed once here and rendered twice — into the controller's terminal
        # below and into the frozen brief the worker reads (law 3).
        provenance = declaration_provenance(store, others_running)
        cooccurrence = commands.touch_cooccurrence(store, claim.files)
        # KYO-327.c2: two more answers this store could already give and did not —
        # where the declared symbols are, and which test files past dispatches on
        # these paths also changed. Both resolved here, beside the three above, for
        # the same law-3 reason: `brief.py` renders what it is handed.
        test_history = commands.touch_test_history(store, claim.files)
        symbol_lines = touch_symbol_lines(ctx.code_root, [doc, *covered_docs])
        brief_text = brief_module.generate_brief(
            record=doc,
            kind=kind_def,
            dispatch_id=dispatch_id,
            worked_from_sha=worked_from_sha,
            base_ref=args.base.strip() if base_sha is not None else None,
            # KYO-487: the existing branch whose tip ``worked_from_sha`` is, so the
            # brief does not call that tip "HEAD in this repo".
            base_branch=branch if base_is_branch_tip else None,
            worktree=worktree,
            branch=branch or "",
            worker_model=worker_model,
            worker_model_source=MODEL_SOURCE_LABELS[model_source],
            generated_at=stamp,
            worker_rules=rules,
            controller_task_prose=task_prose,
            covered_records=covered_docs,
            claimed_paths=claim.per,
            concurrent_dispatches=concurrent,
            # KYO-147.c1: the same list `kyo dispatch` prints to the controller. The
            # worker is the party who cannot see the other dispatches at all, so the
            # honest statement about what the guard established belongs in the frozen
            # brief at least as much as in the terminal.
            disjointness_provenance=provenance,
            # KYO-147.c5: overlaps the check narrowed rather than refused.
            touch_narrowings=narrowings,
            # KYO-147.c2/c3: what this record's declared files have historically
            # dragged in. Empty on a young store, and rendered as nothing at all.
            touch_cooccurrence=cooccurrence,
            # KYO-327.c2: where the declared symbols were at this instant, and where
            # this project has historically put the tests for these paths. Both
            # render as nothing at all when there is nothing to say.
            touch_symbol_lines=symbol_lines,
            touch_test_history=test_history,
            # KYO-143: what this project declared a fresh worktree needs. Read from
            # the loaded config, never sniffed from the worktree — `kyo dispatch`
            # normally runs in a different checkout from the worker's (the same
            # reason `--worktree` is declared rather than inferred), so looking for
            # a `.venv` from here would answer a question about the wrong tree.
            worktree_setup=store.config.worktree_setup,
            # KYO-299.c2: what this project declared numbered and append-only, with
            # this dispatch's own fragment path already computed. Read here rather than
            # in `brief.py` for the same reason `worktree_setup` is read here: the
            # brief renders declarations, it does not go looking for them.
            numbered_documents=numbered_document_rows(ctx, dispatch_id),
            # KYO-614.c1: whether this project governs its RECX.yaml, read here for the
            # same reason: the brief renders declarations, it does not go looking.
            recx_manifest=docs_recx.project_manifest(ctx.root / config_module.KYOSEI_DIR)
            is not None,
        )
        # KYO-48: a forced dispatch records what it overrode, and "the tool could
        # not tell" is as much a thing to have overridden as a known overlap.
        overrides = collisions + unknown_scope
        override_json = json.dumps(overrides, sort_keys=True) if overrides else None
        covers_json = json.dumps(also_covers, sort_keys=True) if also_covers else None
        # KYO-485: the last thing before the row. Every refusal above has had its
        # chance, so a dispatch that would be refused creates no worktree; a failure
        # here raises before the row exists; and a failure after it is undone by
        # `_WorktreeCreation.__exit__`, which wraps this transaction.
        if creation is not None:
            creation.run()
        store.conn.execute(
            "INSERT INTO dispatches (id, record_id, seq, state, brief, worked_from_sha,"
            " worktree, branch, worker_model, touch_override_json, covers_json, report_id,"
            " terminated_why, closed_at, tick_audit_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)",
            (
                dispatch_id,
                record_id,
                seq,
                DISPATCH_STATE_RUNNING,
                brief_text,
                worked_from_sha,
                worktree,
                branch,
                worker_model,
                override_json,
                covers_json,
                stamp,
                stamp,
            ),
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"dispatch {record_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    # KYO-535.c1: what lies OUTSIDE the worktree right now, so this dispatch's stop can
    # name what appeared there since. Taken once the row exists (a refused dispatch
    # leaves no baseline) and after any `git worktree add` above (the controller's own
    # new worktree is not a leak). Never fails the dispatch; the line says either way.
    baseline_file, baseline_line = take_baseline(
        ctx.code_root, dispatch_id, ctx.environ, stale=lambda other: _not_running(store, other)
    )

    lines = [f"dispatched {record_id} as {dispatch_id}", echo_value("base sha", worked_from_sha)]
    lines.append(f"  {baseline_line}")
    # KYO-52: the stored value is relative; the human needs somewhere to `cd`, and a
    # terminal line is not git-canonical, so display resolves and storage does not.
    lines.append(echo_value("worktree", f"{worktree}  (resolves to {worktree_resolved})"))
    if creation is not None:
        # KYO-485 (proposal KYO-484): `kyo dispatch --branch` runs `git worktree add`
        # itself, in the code repository. KYO-70's stance — that creating a worktree
        # is a code-repository act a store command must not perform — had no decision
        # behind it, KYO-402 already has `kyo worktrees --prune` run `git worktree
        # remove`, and a controller pasting the printed command on every dispatch is
        # where hand-made collisions came from. `--no-create-worktree` keeps the old
        # printed line below.
        lines.append(
            f"  created it: {creation.command()}  (run in {ctx.code_root})"
        )
    elif reused_worktree:
        lines.append(
            f"  reused it: already a worktree of {ctx.code_root} with {branch} checked "
            "out, so no `git worktree add` ran"
        )
    if derived_worktree and (creation is not None or not worktree_resolved.is_dir()):
        if creation is None:
            # `--no-create-worktree`: record, derive, and print the command — the
            # behaviour every dispatch had before KYO-485.
            lines.append(
                # KYO-89: quoted, because the derived home contains a space on purpose
                # and this line is meant to be run as printed.
                f"  create it with: git worktree add {shlex.quote(str(worktree_resolved))} "
                f"-b {shlex.quote(branch)}"
                + (f" {worked_from_sha}" if base_sha is not None else "")
                + "  (or without -b if the branch already exists)"
            )
        # KYO-143: `git worktree add` creates a tree holding tracked files and
        # nothing else, so this is the one moment where what the worker will find
        # is being decided — and the only moment at which saying so is free. What
        # follows is printed, never run: `kyo dispatch` is a store command, and a
        # store command that ran a project's package manager on its own initiative
        # would be assuming exactly what `worktree_setup` exists to have the
        # project declare. The same value reaches the worker in the frozen brief;
        # this line is for the controller, who is the one holding a shell here.
        if store.config.worktree_setup:
            # KYO-489: the reasoning is explained once, in `kyo reference dispatch`.
            lines.append(f"  then provision it ({explanations.say('worktree-setup-not-run')}):")
            lines.extend(f"    {command}" for command in store.config.worktree_setup)
        else:
            # KYO-143.c4: stated, not silent. A controller who reads this and
            # believes the worktree is ready is the failure this line prevents,
            # and the remedy is named because the project can end the warning.
            lines.append(
                "  this project declares no `worktree_setup`, so that worktree will "
                "hold tracked files only — anything gitignored (`.venv/`, "
                "`node_modules/`, built binaries) will be missing and the worker will "
                "rebuild it by hand; declare the commands in kyosei/config.yml to have "
                "them stated in the brief"
            )
    # KYO-32: echoed either way. "(not declared — `kyo report` will not check where
    # this dispatch's commits landed)" is a statement, not a silence: the absence of
    # the check is the thing a controller most needs to know it is living without.
    lines.append(
        echo_value(
            "branch",
            branch
            or "(not declared — `kyo report` will not check where this dispatch's "
            "commits landed; pass --branch NAME to have it checked)",
        )
    )
    # KYO-61: echoed either way, like the branch line above it. A dispatch that
    # declared no model says so — the absence is the fact worth reading, because it
    # is the state every dispatch ran in, before this field, without anyone noticing.
    lines.append(
        echo_value(
            "worker model",
            f"{worker_model} ({MODEL_SOURCE_LABELS[model_source]}; "
            f"{explanations.say('worker-model-not-enforced')})"
            if worker_model
            else f"(not declared — {MODEL_SOURCE_LABELS[MODEL_SOURCE_NONE]})",
        )
    )
    if measurements:
        # Warns, never refuses. The brief froze a moment ago and the worker cannot
        # correct it before acting on it, so this is the last point at which "did
        # you measure that, or copy it?" is a cheap question (KYO-27).
        lines.append(
            f"  warning: the task prose states {len(measurements)} measurement(s) — "
            f"{', '.join(repr(m) for m in measurements)}. This brief is frozen now, so a "
            "number copied from a doc rather than measured just became something the "
            "worker has to correct from inside. Re-measure before you rely on it."
        )
    if also_covers:
        lines.append(f"also formally covers: {', '.join(also_covers)}")
    if collisions:
        lines.append(f"--force overrode {len(collisions)} touch collision(s):")
        # KYO-65.c2: the same sentence the refusal would have printed, so a
        # controller who forced past an overlap owned by an also-covered record
        # can still see which record that was.
        lines += [f"  {collision_detail(c)}" for c in collisions]
    if unknown_scope:
        lines.append(
            f"--force overrode {len(unknown_scope)} unanswerable disjointness check(s):"
        )
        lines += [f"  {scope_unknown_detail(u)}" for u in unknown_scope]
    if finished is not None:
        # KYO-295: --force got past the finished-but-not-moved refusal. Printed with
        # the same sentence the refusal would have carried, for KYO-65.c2's reason:
        # a controller who forced past a check is owed what it would have said, and a
        # dispatch whose record's own criteria all say "built" is the one most worth
        # seeing again in the echo.
        lines.append(
            f"--force overrode the finished-but-not-moved refusal: {record_id} has "
            f"every criterion settled and `kyo next` withholds it; it needs "
            f"`{finished['needs']}`"
        )
    if blocked_by:
        # KYO-570: what the blocked refusal would have said, for KYO-65.c2's reason.
        lines.append(f"--force overrode the blocked refusal ({len(blocked_by)} blocker(s)):")
        lines += [
            f"  {b['id']} (status {b['status']}, not settled) through link {b['link_id']}"
            for b in blocked_by
        ]
    # KYO-147.c1: printed on every dispatch, including — especially — the ones that
    # passed. A guard that says "I checked a prediction" is honest; one that says
    # nothing and lets the absence of a refusal mean "no collision" is not.
    lines += provenance_lines(provenance)
    # KYO-147.c5: stated where the refusal it replaced would have been printed.
    warnings = commands.narrowing_warnings(narrowings)
    symbol_narrowings = [n for n in narrowings if n not in warnings]
    if symbol_narrowings:
        lines.append(
            f"{len(symbol_narrowings)} file(s) are shared with a running dispatch at "
            "disjoint symbols and were NOT refused:"
        )
        lines += [f"  {commands.narrowing_detail(n)}" for n in symbol_narrowings]
    # KYO-395: a new file under another dispatch's directory touch — let through, and
    # said as a warning, because the other worker may create that same name.
    if warnings:
        lines.append(
            f"{len(warnings)} new file(s) lie under a directory a running dispatch "
            "claims, and were let through with a WARNING rather than refused:"
        )
        lines += [f"  {commands.narrowing_detail(n)}" for n in warnings]
    lines += _broad_claim_lines(broad_claims)
    lines += _cooccurrence_lines(cooccurrence)
    # KYO-326: the brief is the one thing here a controller does not act on — it stays
    # in context to be re-read every turn (34,870 bytes for KYO-263.d1)
    # although what a controller reads back out of it is `kyo show <dispatch-id>`,
    # read-only, any time after this call returns. Everything printed above this line
    # (warnings, the drag-in summary, the disjointness lines, the worktree command) is
    # unchanged — only the brief itself moves off the default terminal print.
    # `--print-brief` restores the old behaviour verbatim; `--brief-out` and `--json`
    # were never affected either way.
    if args.print_brief:
        lines += echo_block("brief", brief_text)
    else:
        lines.append(
            f"stored brief ({len(brief_text)} chars, sha256:{fingerprint(brief_text)}) — "
            f"read it with `kyo show {dispatch_id}` ({explanations.say('print-brief')})"
        )
    if brief_out_path is not None:
        try:
            brief_out_path.write_text(brief_text, encoding="utf-8")
        except OSError as exc:
            # KYO-411: the dispatch row is committed by now. A traceback here reads as
            # "the dispatch failed" and invites a second one into the same worktree, so
            # the error names what DID happen and says not to retry.
            raise KyoseiError(
                f"dispatched {record_id} as {dispatch_id} — that dispatch IS recorded and "
                f"running — but --brief-out {args.brief_out} could not be written "
                f"({exc}). Do not re-run `kyo dispatch {record_id}`; read the frozen "
                f"brief with `kyo show {dispatch_id}`"
            ) from exc
        lines.append(echo_value("brief written to", str(brief_out_path)))
    return Output(
        lines,
        {
            "ok": True,
            "command": "dispatch",
            "dispatch_id": dispatch_id,
            "record_id": record_id,
            "worked_from_sha": worked_from_sha,
            "collisions_overridden": collisions,
            # KYO-48: kept separate from `collisions_overridden` rather than
            # folded into it — "we know these overlap" and "we cannot tell
            # whether they overlap" are different facts, and a consumer that
            # merged them would be making exactly the mistake this record fixed.
            "scope_unknown_overridden": unknown_scope,
            # KYO-295: the finished-but-not-moved class row when --force dispatched a
            # record `kyo next` withholds, and `null` when it did not. Kept separate
            # from the touch overrides above for the same reason those two are kept
            # apart: "these overlap", "we cannot tell whether they overlap" and "this
            # record has nothing left to build" are three different facts.
            "finished_not_moved_overridden": finished,
            # KYO-570: the unsettled `blocks` sources --force dispatched past, `[]`
            # when the record was not blocked.
            "blocked_overridden": blocked_by,
            # KYO-147.c1: what the disjointness check actually compared, per other
            # running dispatch. A consumer reading only `collisions_overridden: []`
            # learns that nothing was overridden, not that anything was established.
            "disjointness_provenance": provenance,
            # KYO-147.c5: file-level overlaps that both sides made precise enough not
            # to be a refusal. Never folded into `collisions_overridden` — a narrowing
            # is not an override, and nothing was forced past.
            "touch_narrowings": narrowings,
            # KYO-147.c2/c3: carries the thresholds and the report count alongside the
            # findings, so `--json` can say WHY it was silent while the text output
            # says nothing at all.
            "touch_cooccurrence": cooccurrence,
            # Stored form (root-relative) and the resolved form separately, so a
            # consumer reading `--json` never has to guess which one it holds.
            "worktree": worktree,
            "worktree_resolved": str(worktree_resolved),
            # KYO-535.c1: the baseline `kyo check-clean --subagent-stop` compares this
            # dispatch's stop against, or `null` when none could be written.
            "outside_baseline": None if baseline_file is None else str(baseline_file),
            # KYO-70: whether this path was derived under the project's Kyosei home
            # or handed in with `--worktree`. A consumer that cannot tell the two
            # apart cannot tell whether the directory is expected to exist yet.
            "worktree_derived": derived_worktree,
            # KYO-485: whether this call ran `git worktree add`, and whether it found
            # the worktree already there on this branch and used it as it stood.
            "worktree_created": creation is not None,
            "worktree_reused": reused_worktree,
            "branch": branch,
            # KYO-61: the resolved value and where it came from, separately, so a
            # consumer never has to guess whether `null` means "declared nothing"
            # or "this build does not know" — `worker_model_source` says which.
            "worker_model": worker_model,
            "worker_model_source": model_source,
            "measurement_warnings": measurements,
            "broad_claim_warnings": broad_claims,
            "also_covers": also_covers,
            "record": after[record_id],
        },
    )


def cmd_dispatch(ctx: Context, args: Namespace) -> Output:
    if args.target == "close":
        return cmd_dispatch_close(ctx, args)
    if args.dispatch_id:
        raise UsageError(
            f"unexpected extra argument {args.dispatch_id!r} after record id {args.target!r}; "
            "did you mean `kyo dispatch close <dispatch-id>`?"
        )
    _refuse_close_only_flags(ctx, args)
    # KYO-403: after the create, never inside it — a top-up that must follow a
    # success. (KYO-580 removed the refusal that once preceded it: a worktree of a
    # split project now reads an untracked `.kyosei` through the main checkout.)
    output = _cmd_dispatch_create(ctx, args)
    _ignore_a_nested_worktree_home(ctx, args, output)
    return output


def _ignore_a_nested_worktree_home(ctx: Context, args: Namespace, output: Output) -> None:
    """KYO-403.c5: after a dispatch that derived its worktree under this project's
    home, make sure the store repository ignores that home when it sits inside it —
    see :func:`kyosei.install_gitignore.install_worktree_home_gitignore`. Hung here because a
    derived dispatch is the act that creates the home, so no project reaches a
    nested worktree without passing through it; the line lands in the store's own
    working tree, beside the records this dispatch just wrote, for the controller's
    next store commit.
    """
    if ctx.layout is None or not ctx.layout.split or args.worktree or not args.branch:
        return
    from . import install_gitignore

    changes = install_gitignore.install_worktree_home_gitignore(
        ctx.root, config_module.worktree_home(ctx.worktree_project_name, ctx.environ)
    )
    output.lines.extend(changes)
