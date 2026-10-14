"""``kyo check-branch`` — the merge guard: a worker branch may not diff the store.

Which base it diffs against (the dispatch's recorded base, the configured trunk), the lines
it prints about the tree it read, and the numbered-section refusal live here. The stop
resolution it shares with ``check-clean`` is :mod:`kyosei.cmd_guard`.
"""


from __future__ import annotations

from argparse import Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import docs_check, docs_history, docs_numbered, docs_parse, docs_regions
from . import registry as registry_module
from .cmd_guard import (
    _guarantee_lines,
    _stop_target,
    _subject_lines,
    _untranscribed_output,
    unblocks_repeat_stops,
)
from .commands import (
    Context,
    Output,
)
from .config import (
    KYOSEI_DIR,
    LAYOUT_IN_REPO,
    LAYOUT_UNCONFIGURED,
    config_path_for,
    load_config,
)
from .contracts import (
    CONTRACT_BRANCH_TOUCHES_STORE,
    CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED,
    CONTRACT_HISTORY_PROSE_INTRODUCED,
    CONTRACT_NUMBERED_SECTION_BELONGS_IN_A_FRAGMENT,
    CONTRACT_SECTION_STATUS_DISAGREES,
    GUARDED_BRANCH_DIRS,
    GUARDED_BRANCH_FILES,
)
from .errors import GuardRefusal, KyoseiError
from .git_queries import _git_current_branch, _git_is_ancestor, branch_changed_files, run_git
from .stop_target import STOP_TARGET_NO_TRANSCRIPT, StopTarget

# ------------------- KYO-285: which BASE the merge guard actually diffs against


def _git_merge_base(repo: Path, ref_a: str, ref_b: str) -> str | None:
    """``git merge-base <ref_a> <ref_b>`` in ``repo``, or ``None`` when git could
    not answer (either ref unknown here, ``repo`` unusable, or git missing).

    Never raises: this only sharpens the base :func:`_resolve_against` would
    otherwise fall back to, and a hook must never die computing an optional
    refinement. ``None`` is a real outcome a caller has to handle -- a dispatch's
    recorded base sha from a history this worktree does not share -- not a bug.
    """
    try:
        result = run_git(repo, "merge-base", ref_a, ref_b)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _dispatch_worked_from_sha(ctx: Context, dispatch_id: str) -> str | None:
    """The base sha ``kyo dispatch`` froze for ``dispatch_id``, or ``None`` when
    the store cannot answer. Best-effort, like every other store read a
    ``SubagentStop`` guard performs (:func:`_population_profile` is the sibling
    example) -- a store this build cannot open must not crash the hook that is
    about to print a verdict anyway."""
    try:
        row = ctx.store().dispatch(dispatch_id)
    except Exception:  # noqa: BLE001 - a hook must never die on the store
        return None
    sha = row["worked_from_sha"]
    return sha or None


def _branch_cut_point(
    repo: Path, branch: str, dispatch_base: str, trunk: str
) -> str | None:
    """KYO-294: the point ``branch`` was actually cut from the trunk, when that is
    LATER than the merge-base with the dispatch's recorded base sha.

    ``kyo dispatch`` stamps HEAD as the base and the controller *then* commits the
    store, so the ordinary worktree is cut one or more store-only commits PAST the
    recorded base. Diffing against the recorded base alone would hand the worker a
    ``kyosei/export/`` diff it never wrote (docs/design/guards.md#kyo-294).

    So there are two candidate bases and the honest one is the LATER of them:

    * ``dispatch_base`` — ``merge-base(branch, worked_from_sha)``, KYO-285's answer;
    * ``trunk_base`` — ``merge-base(branch, <trunk>)``, the fork point from main.

    Whichever is a descendant of the other is the point this branch was cut from, and
    that is what ``check-branch`` must judge the branch's own commits beyond. In
    KYO-294's case the trunk base is the controller's store commit, one ahead of the
    recorded base, and it wins. In KYO-285's case (a dispatch cut from a long-lived UI
    branch that carries store commits of its own) the trunk base is far BEHIND the
    recorded base, so ``dispatch_base`` wins and that record's fix is untouched.
    Returns ``None`` whenever nothing later than ``dispatch_base`` can be established
    — no trunk ref here, git unusable, or the two candidates not ancestor-related
    (a branch carrying a merge can have two incomparable ancestors, and "later" is
    then not a thing that exists) — and the caller keeps KYO-285's base.

    **Why a worker's own store commit is still refused.** Both candidates are
    merge-bases of the worker's branch against refs whose *content* the worker does
    not author: a sha frozen before the worker existed, and the trunk. A commit the
    worker makes on its branch is by construction a descendant of both and reachable
    from neither, so it is inside the three-dot diff from whichever is chosen — there
    is no arrangement of the worker's own commits that moves either candidate
    forward. The one thing that does move the trunk base forward is the trunk itself
    growing a commit the branch already contains, which means the controller merged
    it: no longer an unreviewed worker edit. What this does NOT survive is a worker
    that rewrites ``main`` in its own repository, and nothing at stop time could —
    this is a diff check, not a write lock, exactly as the worker-facing rule says.
    """
    trunk_base = _git_merge_base(repo, branch, trunk)
    if not trunk_base or trunk_base == dispatch_base:
        return None
    # `is True`, never truthiness: `None` is "git could not answer" and must not read
    # as either verdict (law 6, and :func:`_git_is_ancestor`'s own contract).
    if _git_is_ancestor(repo, dispatch_base, trunk_base) is True:
        return trunk_base
    return None


# ------------------- KYO-410: which branch IS the trunk, asked once, in one place

#: The ``kyosei/config.yml`` key that declares this repository's trunk by name.
TRUNK_CONFIG_KEY = "trunk"

#: The spelling ``check-branch`` has always used for "the trunk" -- the CLI's implicit
#: default and the packaged SubagentStop hook's explicit ``--against main``. It is only
#: that: a SPELLING, which :func:`resolve_trunk` turns into whatever this repository's trunk
#: is actually called (docs/design/guards.md#kyo-410). Keeping the hook's command line as it
#: is (rather than writing a resolved name into every project's settings) means no hook
#: drift and no SUPERSEDED_HOOK_COMMANDS pairing: every installed copy of the line gets the
#: fix.
TRUNK_SPELLING = "main"

#: Git's own built-in value for ``init.defaultBranch`` when nobody set one (git
#: before 3.0): the branch a plain ``git init`` creates. Tried LAST, after ``main``,
#: so a repository that has both keeps the answer it had before KYO-410.
GIT_BUILTIN_DEFAULT_BRANCH = "master"


@dataclass(frozen=True)
class Trunk:
    """The ref :func:`resolve_trunk` settled on, and which rung of the order gave it.

    ``source`` is one of ``config`` (``trunk:`` in config.yml), ``origin-head``
    (``refs/remotes/origin/HEAD``), ``init-default-branch``, ``main``, or
    ``git-builtin-default`` -- reported on every run, because an answer this guard
    derived is only checkable when it says where it came from.
    """

    ref: str
    source: str

    def describe(self) -> str:
        return {
            "config": f"`{TRUNK_CONFIG_KEY}: {self.ref}` in config.yml",
            "origin-head": "refs/remotes/origin/HEAD",
            "init-default-branch": "git config init.defaultBranch",
            "main": "nothing declared or advertised, and `main` exists",
            "git-builtin-default": (
                "nothing declared or advertised, no `main`, and `master` "
                "(git's own init.defaultBranch default) exists"
            ),
        }[self.source]

    def as_json(self) -> dict:
        return {"ref": self.ref, "source": self.source}


def _git_output(repo: Path, *args: str) -> str | None:
    """Stripped stdout of one read-only git query, or ``None`` when git could not
    answer. Never raises: a trunk rung git cannot answer is a rung skipped."""
    try:
        result = run_git(repo, *args)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _commit_ref_exists(repo: Path, ref: str) -> bool:
    return _git_output(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") is not None


def _local_branch_exists(repo: Path, name: str) -> bool:
    return _commit_ref_exists(repo, f"refs/heads/{name}")


def configured_trunk(ctx: Context) -> tuple[str | None, str | None]:
    """``(trunk, unreadable)``: this project's declared ``trunk:``, or ``None``.

    Read from config.yml directly, as :func:`declared_command_policy` reads its keys,
    and for its reason: ``check-branch`` never opens the store. A config that will
    not load yields no declaration CARRYING the reason, so the caller can say so
    rather than pass it off as a project that declared nothing (KYO-335's shape).
    """
    if ctx.layout is None or not ctx.layout.configured:
        return None, None
    try:
        config = load_config(ctx.root, code_root=ctx.code_root)
    except Exception as exc:  # noqa: BLE001 - a hook must never die on a config it cannot read
        return None, str(exc)
    return config.trunk, None


def resolve_trunk(repo: Path, configured: str | None, *, config_path: Path | str) -> Trunk:
    """KYO-410: this repository's trunk, in one documented order.

    1. ``trunk:`` in config.yml -- a declaration, so it is never second-guessed: a
       declared trunk that names no commit here is REFUSED, naming the key, rather
       than quietly skipped to a rung the project did not choose (law 6);
    2. ``refs/remotes/origin/HEAD`` -- what the remote advertises as its default
       branch. The local branch of that name when there is one (a worker's branch is
       cut from the local trunk), else the remote-tracking ref itself;
    3. ``git config init.defaultBranch`` -- when that branch exists here;
    4. ``main`` -- when it exists, which is every answer this guard gave before;
    5. ``master`` -- git's own built-in ``init.defaultBranch`` before git 3.0, so a
       repository made by a plain ``git init`` with nothing configured resolves too.

    Rungs 2-5 are observations, so a rung that names nothing here is skipped. When
    every rung is empty the refusal names the key that settles it.
    """
    if configured is not None:
        if _commit_ref_exists(repo, configured):
            return Trunk(configured, "config")
        raise KyoseiError(
            f"{config_path} key '{TRUNK_CONFIG_KEY}' names {configured!r}, which is not a "
            f"known git ref in {repo} (`git rev-parse --verify {configured}` failed) -- "
            f"hand edit {config_path}: set `{TRUNK_CONFIG_KEY}:` to the branch this "
            "repository's worker branches are cut from, or delete the line to let "
            "origin/HEAD, init.defaultBranch or main decide"
        )
    advertised = _git_output(
        repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
    )
    if advertised:
        name = advertised.split("/", 1)[1] if advertised.startswith("origin/") else advertised
        if _local_branch_exists(repo, name):
            return Trunk(name, "origin-head")
        if _commit_ref_exists(repo, advertised):
            return Trunk(advertised, "origin-head")
    default_branch = _git_output(repo, "config", "--get", "init.defaultBranch")
    if default_branch and _local_branch_exists(repo, default_branch):
        return Trunk(default_branch, "init-default-branch")
    if _local_branch_exists(repo, TRUNK_SPELLING):
        return Trunk(TRUNK_SPELLING, "main")
    if _local_branch_exists(repo, GIT_BUILTIN_DEFAULT_BRANCH):
        return Trunk(GIT_BUILTIN_DEFAULT_BRANCH, "git-builtin-default")
    tried_default = (
        f"init.defaultBranch ({default_branch}) names no branch here"
        if default_branch
        else "no init.defaultBranch"
    )
    raise KyoseiError(
        f"cannot tell which branch is the trunk in {repo}: no '{TRUNK_CONFIG_KEY}' key in "
        f"{config_path}, no refs/remotes/origin/HEAD, {tried_default}, and neither "
        f"`{TRUNK_SPELLING}` nor `{GIT_BUILTIN_DEFAULT_BRANCH}` exists -- hand edit "
        f"{config_path}: add `{TRUNK_CONFIG_KEY}: <branch>` naming the branch worker "
        "branches are cut from, or pass --against <ref>"
    )


def _resolve_against(
    ctx: Context,
    explicit_against: str | None,
    target: StopTarget | None,
    repo: Path,
    branch: str,
) -> tuple[str, list[str], dict]:
    """Which ref ``cmd_check_branch`` diffs ``branch`` against, and why.

    ``main`` is the right base only for a dispatch cut from main: a dispatch cut from a
    branch that carries commits of its own would be blamed for them
    (docs/design/guards.md#kyo-285).

    The fix reuses the resolution ``--subagent-stop`` already performs to answer
    a *different* question ("which worktree do I read?") -- whether this stop
    resolves to a specific running dispatch (``target.dispatch_id`` is only ever
    set for :data:`~kyosei.stop_target.STOP_TARGET_MATCHED`). When it does, the
    base is that dispatch's own recorded ``worked_from_sha``, via
    ``git merge-base <branch> <worked_from_sha>`` rather than the sha alone: on
    the ordinary path (the worker branched directly from it) the merge-base IS
    that sha, and where the branch and the recorded sha share a history this
    worktree can resolve but the branch does not descend directly from it, the
    merge-base is still the honest common ancestor rather than a two-dot diff
    against a sha the branch never came from.

    KYO-294 makes that merge-base a FLOOR rather than the answer. The recorded sha
    is stamped before the controller commits the store, so the ordinary worktree is
    cut one or more store-only commits past it and inherits their diff; the base is
    therefore the LATER of the KYO-285 merge-base and the branch's fork point from
    the trunk, which is the point the branch was actually cut from. The whole of that
    reasoning, and why a worker's own store commit is still refused, is in
    :func:`_branch_cut_point`.

    An explicit ``--against <ref>`` naming anything OTHER than ``main`` wins over
    any of this outright: a stated intent to diff against a specific, named ref
    is never second-guessed by a match this run happened to make. ``main`` itself
    does NOT get that treatment, on purpose: the packaged hook fragment spells
    out `kyo check-branch --subagent-stop --against main` explicitly (so its
    intent is self-documenting rather than resting on an implicit CLI default),
    and this record's whole fix would be dead code if the hook's own literal
    `--against main` were treated as an override no dispatch could ever beat --
    every SubagentStop this project's own hook fires would still be running the
    pre-KYO-285 comparison. So the CLI default (nothing passed) and the hook's
    own explicit `main` are one case: `main` is the FALLBACK spelling, used
    whenever nothing sharper is known, not a value that forecloses looking for
    something sharper. A human who really does want the old, unretargeted
    comparison against `main` on a matched dispatch still has an escape hatch --
    naming an actual branch, `--against main^{commit}`, or simply not running
    with `--subagent-stop` at all.

    KYO-410: and `main` is a SPELLING, not a branch name. Wherever it is the answer (the
    fallback, and the trunk the KYO-294 cut point is measured from) the ref used is
    :func:`resolve_trunk`'s, which is ``main`` in a repository whose trunk is ``main``
    and the real trunk everywhere else (docs/design/guards.md#kyo-410).

    Always returns a usable ref or raises :class:`KyoseiError` naming the remedy: a
    declared ``trunk:`` that names no ref is refused wherever it would be used, and a
    trunk no rung can find is refused only where the comparison has nothing else to
    stand on -- a matched stop with a recorded base still diffs against that base.
    """
    if explicit_against and explicit_against != TRUNK_SPELLING:
        return (
            explicit_against,
            [f"  against: {explicit_against} (explicit --against)"],
            {"against_source": "explicit", "dispatch_base_sha": None, "trunk": None},
        )

    configured, unreadable = configured_trunk(ctx)
    unreadable_lines = (
        [
            f"  config.yml did not load ({unreadable}), so no `{TRUNK_CONFIG_KEY}:` was "
            "read and the trunk was looked for in git alone (KYO-410)"
        ]
        if unreadable
        else []
    )
    trunk: Trunk | None = None
    try:
        trunk = resolve_trunk(repo, configured, config_path=config_path_for(ctx.root))
    except KyoseiError:
        if configured is not None:
            # A declaration that names nothing is refused wherever it is read: the
            # project said which branch this is, and it is wrong (KYO-410.c4).
            raise

    def fallback(line: str | None, info: dict) -> tuple[str, list[str], dict]:
        # A trunk no rung found is refused HERE, where nothing else can stand in for
        # it -- asked again rather than re-raised from a variable, so the refusal is
        # raised at its own site (the remedy walk matches raise sites by class).
        trunk_ref = (
            trunk
            if trunk is not None
            else resolve_trunk(repo, configured, config_path=config_path_for(ctx.root))
        )
        # KYO-423.c2: ``line is None`` is a hand run -- no stop to resolve, so no
        # reason is owed beyond "this is the default".
        head = (
            f"  against: {trunk_ref.ref} (default), this repository's trunk "
            f"({trunk_ref.describe()})"
            if line is None
            else f"  against: {trunk_ref.ref}, this repository's trunk "
            f"({trunk_ref.describe()}) -- {line}"
        )
        return (
            trunk_ref.ref,
            [head, *unreadable_lines],
            {**info, "trunk": trunk_ref.as_json()},
        )

    trunk_json = trunk.as_json() if trunk is not None else None

    if target is not None and target.dispatch_id is not None:
        base_sha = _dispatch_worked_from_sha(ctx, target.dispatch_id)
        if base_sha:
            merge_base = _git_merge_base(repo, branch, base_sha)
            if merge_base:
                # KYO-294: the recorded base is a FLOOR for the cut point, not the cut
                # point itself. See :func:`_branch_cut_point`. KYO-410: measured from
                # the resolved trunk, never from a literal `main`.
                cut = (
                    _branch_cut_point(repo, branch, merge_base, trunk.ref)
                    if trunk is not None
                    else None
                )
                if cut is not None:
                    assert trunk is not None
                    return (
                        cut,
                        [
                            f"  against: {cut}, the point {branch} was cut from "
                            f"{trunk.ref} ({trunk.describe()}) -- "
                            f"later than {target.dispatch_id}'s recorded base sha "
                            f"({base_sha}), because the orchestrator committed the store "
                            "between stamping that sha and creating this worktree, so "
                            "those commits are inherited and not this branch's (KYO-294)",
                            *unreadable_lines,
                        ],
                        {
                            "against_source": "branch-cut-point",
                            "dispatch_base_sha": base_sha,
                            "trunk": trunk_json,
                        },
                    )
                return (
                    merge_base,
                    [
                        f"  against: {merge_base}, the merge-base of {branch} and "
                        f"{target.dispatch_id}'s own recorded base sha ({base_sha}) -- "
                        "not the trunk, because this stop resolved to that dispatch "
                        "(KYO-285)",
                        *unreadable_lines,
                    ],
                    {
                        "against_source": "dispatch-base",
                        "dispatch_base_sha": base_sha,
                        "trunk": trunk_json,
                    },
                )
            return fallback(
                f"{target.dispatch_id}'s recorded base sha ({base_sha}) and {branch} "
                "share no merge-base this repository can compute, so this fell back to "
                "the trunk (KYO-285)",
                {"against_source": "main-fallback", "dispatch_base_sha": base_sha},
            )
        return fallback(
            f"{target.dispatch_id} carries no recorded base sha to diff against "
            "instead, so this fell back to the trunk (KYO-285)",
            {"against_source": "main-fallback", "dispatch_base_sha": None},
        )

    # KYO-423.c2: a hand run (no `--subagent-stop`) has no stop to resolve, and the
    # KYO-285 sentence below described a hook stop that never happened. The text says
    # only "(default)" there; the JSON is unchanged on both paths -- `against_source`
    # stays "main-unmatched", because this record changes text output only.
    return fallback(
        None
        if target is None
        else "this stop did not resolve to a specific running dispatch, so there is no "
        "recorded base sha to diff against instead (KYO-285)",
        {"against_source": "main-unmatched", "dispatch_base_sha": None},
    )


def _deferred_lines(command: str) -> list[str]:
    """One stop, one explanation — the other guard's (KYO-169.c3).

    Both ``SubagentStop`` guards render the same finding about the same stop, and `kyo
    check-branch` already declines to *record* the stop for that reason (see
    :func:`cmd_check_branch`); the explanation is one event's too
    (docs/design/guards.md#kyo-169).

    It is `check-branch` that gives way rather than `check-clean`, and not by coin
    toss: `check-clean` is the guard that writes the durable row, so its output is
    the one that can name it. `check-branch` on an unplaceable stop is worse than
    redundant — the tree it fell back to is the controller's checkout, sitting on
    ``main``, so the diff it reports is ``main`` against ``main``: the vacuity
    KYO-128 fixed for the *matched* case survives untouched here by construction.

    Silence is not the alternative. This project's own rule (KYO-48, KYO-54.c2) is
    that a check which stopped checking anything must not be indistinguishable from
    one that passed, so the line says which guard owns the explanation and where to
    read the verdict this run did reach.
    """
    return [
        f"{command}: NOT REPEATED HERE — one stop, one explanation (KYO-169); "
        "`kyo check-clean --subagent-stop` reports this stop and writes its durable "
        "row. This run's own verdict is in `--json`"
    ]


@unblocks_repeat_stops("check-branch")
def cmd_check_branch(ctx: Context, args: Namespace) -> Output:
    """``kyo check-branch [<branch>] [--against <ref>]`` — KYO-13.c5, the merge
    guard `kyosei/procedures.md` already advertises ("never write to the
    structured store directly ... a merge guard fail[s] a branch that diffs
    anything under kyosei/"), made mechanical rather than aspirational.

    Fails (exit 2) if ``branch`` diffs anything under ``kyosei/`` or ``ideas/``
    relative to its merge-base with ``against``. Read-only: no store is opened,
    no transaction runs, nothing is written — this is a git check, not a store
    mutation, so it works even against a worktree whose ``kyosei/kyosei.db``
    does not exist (a worker's worktree never has one; it is gitignored).
    """
    guarded_label = " or ".join(GUARDED_BRANCH_DIRS)
    layout_kind = ctx.layout.kind if ctx.layout is not None else LAYOUT_IN_REPO

    # KYO-128: the same retarget `check-clean` takes. Run from the hook's inherited cwd (the
    # controller's checkout, on `main`) this check would diff `main` against `main` and pass
    # vacuously (docs/design/guards.md#kyo-128). KYO-134: the stop itself is written down by
    # `check-clean` alone — see :func:`cmd_check_clean`. Both run on every stop from one hook,
    # so recording the stop in both would put two rows in a git-canonical ledger for one event
    # and make every count of them twice the truth. KYO-169: and the same is true of the
    # EXPLANATION, which is one event's too; :func:`_deferred_lines` carries it, and this
    # command's unresolved branch below is where it lands. KYO-592: the payload is read for
    # one thing, ``stop_hook_active``; a refusal on a repeat stop is this guard's own finding,
    # recorded as its own row, never the stop's (:func:`unblocks_repeat_stops`).
    target, _raw = _stop_target(ctx, args)

    # KYO-54.c2: with no project configured at all, there is no store to protect
    # and nothing this command could truthfully check. It says so and exits 0
    # rather than either failing every `SubagentStop` in a repository that never
    # asked for a store, or passing as though it had checked something.
    if layout_kind == LAYOUT_UNCONFIGURED:
        lines = [
            *_subject_lines(target, "check-branch"),
            "check-branch: nothing to guard",
            *_guarantee_lines(ctx, guarded_label),
        ]
        if getattr(args, "quiet", False):
            # KYO-420.c6: a pass, in one line naming the tree.
            lines = [f"check-branch: nothing to guard in {ctx.code_root} (no project configured)"]
        return Output(
            lines,
            {
                "subject": target.as_json() if target is not None else None,
                "checked_the_worker_tree": target is None or target.resolved,
                "ok": True,
                "command": "check-branch",
                "layout": layout_kind,
                "guarding": False,
                "branch": None,
                "against": args.against or TRUNK_SPELLING,
                "guarded_dirs": list(GUARDED_BRANCH_DIRS),
                "changed_files": [],
                "guarded_files": [],
            },
        )

    # KYO-373.c2: an agent with no transcript has no tree, and the fallback below
    # would diff the CONTROLLER's checkout. Returned before `_git_current_branch`,
    # which is itself a git command run in a directory.
    if target is not None and target.outcome == STOP_TARGET_NO_TRANSCRIPT:
        return _untranscribed_output(
            target,
            "check-branch",
            None,
            {
                "command": "check-branch",
                "layout": layout_kind,
                "guarding": True,
                "branch": None,
                "against": args.against or TRUNK_SPELLING,
                "guarded_dirs": list(GUARDED_BRANCH_DIRS),
                "changed_files": [],
                "guarded_files": [],
            },
        )

    # The *code* repository, which under the split layout is not the store root:
    # the branch a worker commits on lives here, and running these git queries in
    # the store would answer confidently about the wrong repository.
    # KYO-128: the stopping worker's own worktree when the hook could name it.
    repo = target.directory if target is not None else ctx.code_root
    branch = args.branch or _git_current_branch(repo)

    # KYO-285: `main` is a fallback now, not the default — see `_resolve_against`.
    # An explicit `--against` naming anything else still wins outright. KYO-410: and
    # `main` itself (the packaged hook's own `--against main`) is a spelling for this
    # repository's trunk, resolved by :func:`resolve_trunk`, not a branch name.
    against, against_lines, against_info = _resolve_against(
        ctx, args.against, target, repo, branch
    )

    # KYO-71: the one three-dot diff, shared. `kyo touch-drift` asks the same
    # question of the same computation ("what did this branch actually change?") to
    # compare it against what the dispatch's records claimed — which is only cheap
    # because this already existed. Two copies could drift about the merge base.
    changed = branch_changed_files(repo, against, branch)

    def _is_guarded(path: str) -> bool:
        # KYO-403: and the store link, as an exact path — see GUARDED_BRANCH_FILES.
        return path in GUARDED_BRANCH_FILES or any(
            path == d.rstrip("/") or path.startswith(d) for d in GUARDED_BRANCH_DIRS
        )

    guarded = sorted(p for p in changed if _is_guarded(p))
    unresolved = target is not None and not target.resolved
    # KYO-299.c5: the second thing this branch can be refused for, and the only one
    # that is about the CODE tree rather than the store. Computed here so it reaches
    # `data` on every path, refused below only when the store check did not already
    # refuse — a branch that wrote to `kyosei/` has a bigger problem than its headings.
    added_sections = _added_numbered_sections(ctx, repo, against, branch, changed)
    # KYO-493: a fragment this branch wrote whose `amends:` header assemble would
    # refuse — caught here, where the worker who can fix it still holds the branch.
    fragment_refusals = _fragment_amends_refusals(ctx, repo, branch, changed)
    # KYO-499.c4: the section-status rules `kyo docs check` runs, kept to the problems
    # this branch's own changes are part of.
    section_problems = _section_status_problems(ctx, repo, branch, changed)
    # KYO-555.c4/c5: diary prose this branch newly introduced into an ssot document.
    # Printed on every outcome; blocks only in a `history_lint: block` set.
    history = _history_lint(ctx, repo, against, branch, changed)

    data = {
        "ok": not guarded or unresolved,
        "command": "check-branch",
        "layout": layout_kind,
        "guarding": True,
        "repo": str(repo),
        "store_root": str(ctx.root),
        "branch": branch,
        "against": against,
        "guarded_dirs": list(GUARDED_BRANCH_DIRS),
        "changed_files": changed,
        "guarded_files": guarded,
        # KYO-128: which tree this verdict is about, and how that was decided.
        "subject": target.as_json() if target is not None else None,
        "checked_the_worker_tree": target is None or target.resolved,
        # KYO-285: which base `against` actually is, and whether it came from a
        # matched dispatch's own recorded base sha rather than main.
        **against_info,
        # KYO-299.c5: numbered sections this branch added straight to a declared
        # append-only document, one entry per document.
        "numbered_sections_added": [
            {
                "doc_path": found.document.path,
                "fragments": found.document.fragments_dir,
                "headings": list(found.headings),
            }
            for found in added_sections
        ],
        "fragment_refusals": [
            {
                "fragment": refusal.fragment,
                "reason": refusal.reason,
                "says": refusal.says,
                "remedy": refusal.remedy,
            }
            for refusal in fragment_refusals
        ],
        "section_status_problems": [problem.as_json() for problem in section_problems],
        "history_lint": history.as_json(),
    }
    if unresolved:
        assert target is not None
        # KYO-169.c3: the explanation belongs to `check-clean`, which is also the
        # guard that wrote this stop down. See :func:`_deferred_lines`. `data` is
        # untouched — a machine consumer still gets the subject, the branch, the
        # changed files and `checked_the_worker_tree`; it is the duplicated PROSE
        # that goes, and only that.
        return Output(_deferred_lines("check-branch"), data)
    if guarded:
        # `data=` so `--json` still reports branch/against/guarded_files on failure.
        # KYO-187: a required keyword argument rather than an `error.data = data`
        # assignment above a `raise error` — that shape could omit the payload with
        # nothing failing, and was invisible to the remedy-coverage walk.
        raise GuardRefusal(
            CONTRACT_BRANCH_TOUCHES_STORE,
            [
                *_subject_lines(target, "check-branch"),
                *against_lines,
                *(
                    # KYO-403: the store link is refused in its own words — it is not
                    # "under" either directory, and what it breaks is different.
                    f"{branch} diffs {p} relative to {against} — the store link that "
                    "tells every `kyo` in this repository where the store is; a worker "
                    "branch that adds, deletes or re-points it changes the project's "
                    "layout, and after a merge every guard here would answer about the "
                    f"wrong store or none (KYO-403). Restore it on {branch} with "
                    f"`git checkout {against} -- {p}` (or `git rm {p}` if "
                    f"{against} has none) and commit"
                    if p in GUARDED_BRANCH_FILES
                    else f"{branch} diffs {p}, under {guarded_label} relative to {against} "
                    "— a worker branch must never touch the structured store or the ideas "
                    "layer (kyosei/procedures.md's merge guard, KYO-13.c5)"
                    for p in guarded
                ),
            ],
            data=data,
        )
    if added_sections:
        data["ok"] = False
        raise GuardRefusal(
            CONTRACT_NUMBERED_SECTION_BELONGS_IN_A_FRAGMENT,
            [
                *_subject_lines(target, "check-branch"),
                *against_lines,
                *_numbered_section_lines(
                    added_sections,
                    branch=branch,
                    against=against,
                    dispatch_id=target.dispatch_id if target is not None else None,
                ),
            ],
            data=data,
        )
    if fragment_refusals:
        data["ok"] = False
        raise GuardRefusal(
            CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED,
            [
                *_subject_lines(target, "check-branch"),
                *against_lines,
                f"{branch} carries {len(fragment_refusals)} fragment problem(s) that "
                "`kyo docs assemble` would refuse on the merged tree, voiding the whole "
                "wave's assembly — fix them here, where the branch is still yours",
                *(f"  {refusal.line()}" for refusal in fragment_refusals),
            ],
            data=data,
        )
    if section_problems:
        data["ok"] = False
        raise GuardRefusal(
            CONTRACT_SECTION_STATUS_DISAGREES,
            [
                *_subject_lines(target, "check-branch"),
                *against_lines,
                f"{branch} changes {len(section_problems)} section status or link that "
                "`kyo docs check` refuses — fix them here, where the branch is still yours "
                "(KYO-499)",
                *(f"  {problem.line()}" for problem in section_problems),
            ],
            data=data,
        )
    if history.blocking:
        data["ok"] = False
        raise GuardRefusal(
            CONTRACT_HISTORY_PROSE_INTRODUCED,
            [
                *_subject_lines(target, "check-branch"),
                *against_lines,
                f"{branch} newly introduces {len(history.blocking)} block-grade history "
                "phrase(s) into a document of a `history_lint: block` ssot set. An ssot "
                "document states what is true now (KYO-555)",
                *(f"  {line}" for line in history.lines()),
                f"  to keep one deliberately, commit with the trailer `{docs_history.TRAILER}: "
                "<path> <pattern> <reason>` naming that file and pattern; `kyo report` "
                "lists it for your orchestrator to review",
            ],
            data=data,
        )
    lines = [
        *_subject_lines(target, "check-branch"),
        *against_lines,
        f"check-branch {branch} against {against}: clean "
        f"({len(changed)} file(s) changed, none under {guarded_label})",
        *_guarantee_lines(ctx, guarded_label),
    ]
    if getattr(args, "quiet", False):
        # KYO-420.c6: a pass is read on every SubagentStop, so `--quiet` states it in
        # one line naming the tree. Only a PASS: every refusal above raised before this
        # and prints in full, and so does an unplaceable stop (`unresolved`), whose
        # whole point is the explanation. `--json` never reads `lines`.
        lines = [
            f"check-branch {branch} against {against} in {repo}: clean "
            f"({len(changed)} file(s) changed, none under {guarded_label})"
        ]
    lines += _history_warning_lines(history)
    return Output(lines, data)


# ------------------- KYO-555: diary prose a branch introduces into an ssot document


def _history_lint(
    ctx: Context, repo: Path, against: str, branch: str, changed: Sequence[str]
) -> docs_history.HistoryLint:
    """The history lint over the ssot documents this branch changed (KYO-555).

    Never raises, for :func:`_added_numbered_sections`' reason.
    """
    try:
        documents_in = docs_parse.document_set(ctx.root / KYOSEI_DIR)
        return docs_history.branch_history_lint(
            repo, against, branch, changed, documents_in.section_status
        )
    except Exception:  # noqa: BLE001 - a hook must never die on a config it cannot read
        return docs_history.HistoryLint()


def _history_warning_lines(history: docs_history.HistoryLint) -> list[str]:
    """The lines a passing stop prints for what the history lint found, none blocking."""
    if not history.matches:
        return []
    return [
        f"history lint: {len(history.matches)} history phrase(s) newly introduced into "
        "an ssot document (KYO-555); none blocks this stop",
        *(f"  {line}" for line in history.lines()),
    ]


# ------------------- KYO-299.c5: a numbered section written straight into the document


@dataclass(frozen=True)
class AddedSections:
    """One declared append-only document, and the numbered headings a branch added to it."""

    document: docs_numbered.NumberedDocument
    headings: tuple[str, ...]


def _added_numbered_sections(
    ctx: Context, repo: Path, against: str, branch: str, changed: Sequence[str]
) -> list[AddedSections]:
    """Which declared numbered documents this branch added a numbered heading to.

    Never raises and never reports a failure as a refusal: a project that declares no
    numbered document (which is every project until it declares one), a registry this
    build cannot read, a git that cannot answer — all of them come back empty, because a
    `SubagentStop` guard that died computing an optional check would block a stop over
    something that is nobody's fault. The direction of the failure matters and this is
    the safe one: the cost of a missed refusal is a merge conflict the controller
    resolves by hand, which is exactly the status quo this record improves on.

    Narrowed to the documents the branch actually changed before any blob is read, for
    :func:`kyosei.docs_regions.branch_file_changes`'s reason: two ``git show`` calls rather
    than two hundred.
    """
    try:
        documents = docs_numbered.numbered_documents(ctx.root / KYOSEI_DIR, repo)
    except Exception:  # noqa: BLE001 - a hook must never die on a config it cannot read
        return []
    wanted = {document.path: document for document in documents if document.path in set(changed)}
    if not wanted:
        return []
    try:
        changes = docs_regions.branch_file_changes(repo, against, branch, sorted(wanted))
    except Exception:  # noqa: BLE001 - see above
        return []
    found: list[AddedSections] = []
    for path, document in sorted(wanted.items()):
        change = changes.get(path)
        if change is None:
            continue
        headings = docs_numbered.added_numbered_headings(
            change.before_text, change.after_text, document.heading
        )
        if headings:
            found.append(AddedSections(document=document, headings=tuple(headings)))
    return found


def _numbered_section_lines(
    found: Sequence[AddedSections],
    *,
    branch: str,
    against: str,
    dispatch_id: str | None,
) -> list[str]:
    """The refusal, and it is three things: the rule, why, and the exact path to move to.

    The remedy is a path rather than an instruction, because "write it as a fragment"
    is not actionable by a worker who has never heard of one — and it is this
    dispatch's own path whenever the stop resolved to a dispatch, so the worker is told
    where its file goes rather than how to derive the name.
    """
    who = dispatch_id or "<your-dispatch-id>"
    lines: list[str] = []
    for entry in found:
        document = entry.document
        target = document.fragment_path(who, "<short-slug>")
        for heading in entry.headings:
            lines.append(
                f"{branch} adds a numbered section to {document.path} relative to "
                f"{against}: {heading!r}. That document is declared numbered and "
                f"append-only ({document.set_name} in kyosei/documents.yml), so its "
                "numbers are assigned on the merged tree and never on a branch"
            )
        lines.append(
            f"  why: every worker adding a section to {document.path} in this wave "
            "would pick the same next number, collide on the tail of the file, and "
            "collide again on everything generated from it. A touch cannot prevent "
            "that — a touch narrows a claim and does not move a byte"
        )
        example = document.heading.replace(
            registry_module.HEADING_NUMBER, docs_numbered.FRAGMENT_PLACEHOLDER
        )
        lines.append(
            f"  remedy: move the section into `{target}` instead, heading it "
            f"`{example} <title>` and using {docs_numbered.FRAGMENT_PLACEHOLDER} anywhere the "
            "section "
            "cites its own number. Leave the document itself unchanged. Your orchestrator "
            "runs `kyo docs assemble` after the wave merges, which appends it with the "
            "next number"
        )
        lines.append(
            f"  not refused: EDITING an existing section of {document.path}. That is an "
            "ordinary edit, serialized by a `path#section heading` touch. Only ADDING a "
            "numbered section goes through a fragment"
        )
    return lines


# ------------------- KYO-493: a fragment's `amends:` header, checked on the worker's tree


def _fragment_amends_refusals(
    ctx: Context, repo: Path, branch: str, changed: Sequence[str]
) -> list[docs_numbered.FragmentRefusal]:
    """The amends refusals ``kyo docs assemble`` would raise for the fragments this
    branch changed, read from the branch's own blobs (KYO-493.c7).

    Every one of :data:`kyosei.docs_numbered.AMENDS_REFUSALS` except
    ``amends-unassembled-section``, which a worker's tree cannot decide: a number above
    the document's tail may be a section another branch in the wave adds.

    Never raises, for :func:`_added_numbered_sections`' reason.
    """
    try:
        documents = docs_numbered.numbered_documents(ctx.root / KYOSEI_DIR, repo)
    except Exception:  # noqa: BLE001 - a hook must never die on a config it cannot read
        return []
    by_dir: dict[str, list[docs_numbered.NumberedDocument]] = {}
    for document in documents:
        by_dir.setdefault(document.fragments_dir, []).append(document)

    def show(path: str) -> str | None:
        try:
            result = run_git(repo, "show", f"{branch}:{path}")
        except OSError:
            return None
        return result.stdout if result.returncode == 0 else None

    refusals: list[docs_numbered.FragmentRefusal] = []
    for path in sorted(changed):
        directory, _, name = path.rpartition("/")
        members = by_dir.get(directory)
        if not members or not name.endswith(".md"):
            continue
        text = show(path)
        if text is None:  # removed on this branch
            continue
        fragment = docs_numbered.fragment_from_text(path, text, members)
        early = docs_numbered.header_refusals(fragment)
        if early:
            refusals += early
            continue
        if not fragment.amends or not fragment.attributed:
            continue
        _, _, problems = docs_numbered.amend_targets(
            fragment, show(fragment.document.path) or ""
        )
        refusals += problems
    return refusals


# ------------------- KYO-499: section status and links, checked on the worker's tree


def _section_status_problems(
    ctx: Context, repo: Path, branch: str, changed: Sequence[str]
) -> list[docs_check.SectionStatusProblem]:
    """The section-status refusals ``kyo docs check`` would raise that this branch's
    changes are part of (KYO-499.c4): A1 to A4 on the documents it changed, and A5 on
    the ``src/`` files it changed — or on every ``src/`` file pointing into an opted-in
    document it changed, since retiring a section breaks pointers it never touched.

    Files are read from the branch's own blobs, the way :func:`_fragment_amends_refusals`
    reads them, so the verdict is about what would merge. Never raises, for
    :func:`_added_numbered_sections`' reason.
    """
    try:
        documents_in = docs_parse.document_set(ctx.root / KYOSEI_DIR)
    except Exception:  # noqa: BLE001 - a hook must never die on a config it cannot read
        return []
    touched = set(changed)
    changed_documents = touched.intersection(
        path.relative_to(repo).as_posix()
        for path in docs_parse._iter_docs_files(repo, documents_in.globs)
    )
    opted = docs_check.opted_documents(repo, documents_in.section_status)
    changed_sources = sorted(
        p for p in touched if p.startswith(f"{docs_check.POINTER_SOURCE_ROOT}/")
    )
    if not changed_documents and not (opted and changed_sources):
        return []
    # A changed opted-in document can break a link or a pointer that lives elsewhere (a
    # section that stops citing back, a section newly superseded), so the whole set and
    # every src/ file are read then; otherwise only what changed.
    opted_changed = bool(touched.intersection(opted))
    documents = sorted(changed_documents | (set(opted) if opted_changed else set()))
    sources = docs_check.source_files(repo) if opted_changed else changed_sources
    shown: dict[str, str | None] = {}

    def read(path: str) -> str | None:
        if path not in shown:
            try:
                result = run_git(repo, "show", f"{branch}:{path}")
            except OSError:
                shown[path] = None
            else:
                shown[path] = result.stdout if result.returncode == 0 else None
        return shown[path]

    problems = docs_check.section_status_problems(read, documents, opted, sources)
    return [problem for problem in problems if touched.intersection(problem.paths)]

