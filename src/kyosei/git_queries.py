"""Read-only git queries.

Every ``kyo`` verb that has to ask git a question — which sha is HEAD, which
branch am I on, is this commit reachable from that branch, what changed between
two shas — asks it here. Split out of ``commands.py`` by KYO-57: the callers sit
in three unrelated subjects (``cmd_dispatch``, ``cmd_guard``, ``cmd_sweep``),
so leaving these beside any one of them would have put that subject's file into
the other two's touch lists.

Every function here is read-only and failure-tolerant in the way its caller
needs: git may be absent, the repo may have no commits, a sha may not exist.
None of them ever writes to the repository.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .contracts import (
    BRANCH_REACH_CANNOT_TELL,
    BRANCH_REACH_OFF_BRANCH,
    BRANCH_REACH_REACHABLE,
)
from .errors import KyoseiError

# KYO-300: the hunk parser is pure and lives with the rest of the symbol-region logic,
# so this module keeps its one job — run git, hand the bytes to somebody who can read
# them — and the parsing stays reachable from a test with no repository in it.
from .sweep import DiffHunk, parse_diff_hunks

# ------------------------------------------------------ KYO-417: the one way to run git
#
# Every git subprocess in the package goes through :func:`run_git`, and
# ``tests/test_run_git.py`` walks the AST of every module under ``src/kyosei/`` to keep it
# that way. Each call site used to build its own argv and environment, and none of them
# pinned the settings below, so each defect was 27 defects.

#: KYO-417.c1: prepended to every git argv. Without it git C-quotes any path holding a
#: non-ASCII byte (``"ideas/caf\\303\\251.md"``) in every listing not run under ``-z``,
#: and a prefix or equality test against the real path then fails without an error.
GIT_PINNED_CONFIG = ("-c", "core.quotePath=false")

#: KYO-417.c1: removed from the environment of every git call. git exports ``GIT_DIR``
#: into its own hooks (and a `git rebase --exec`, a wrapper script or a shell may have
#: exported any of the three), and while it is set git ignores ``cwd=`` -- so a `kyo`
#: verb run from a hook asks about whichever repository the hook belongs to, not the
#: one it named. Every caller here means the repository at ``cwd`` and nothing else;
#: none keeps any of the three (the audit is in KYO-417's report).
GIT_SCRUBBED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")

#: KYO-417.c6: set in the environment of every git call.
#:
#: * ``GIT_TERMINAL_PROMPT=0`` -- a credential prompt is a question nobody is there to
#:   answer: `kyo` runs from session hooks and from the desktop app with no terminal.
#: * ``GIT_NO_LAZY_FETCH=1`` -- in a partial clone (``--filter=blob:none``) a diff or
#:   ``git show`` that needs a blob git does not have fetches it from the promisor
#:   remote on the spot. From a Stop hook that is a network round-trip per file, or a
#:   stall; with this set the command fails instead, and every caller already has a
#:   "git could not say" answer for a failed command.
#: * ``GIT_OPTIONAL_LOCKS=0`` -- a read such as ``git status`` otherwise takes the index
#:   lock to write back refreshed stat data, and a hook running beside the human's own
#:   git command then makes one of them fail on ``index.lock``. It only drops locks git
#:   calls optional, so a write that needs its lock still takes it.
GIT_PINNED_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}

#: KYO-417.c1: the bound on a git call that names none. Generous, because a slow disk
#: or a large repository is not a failure; finite, because a git that never returns
#: holds the Stop hook that called it until the harness kills the hook.
GIT_TIMEOUT_SECONDS = 30.0


class GitTimeout(OSError):
    """A git call did not finish within its timeout and was stopped (KYO-417.c5).

    An :class:`OSError` on purpose: every call site already had an ``except OSError``
    (or a ``FileNotFoundError`` beside it) that says "git could not be run", and a
    timed-out call is exactly that answer -- never a traceback, and never "git is not
    installed", which is why a caller that names the missing binary catches
    ``FileNotFoundError`` separately.
    """


def git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment every git call runs with: ``base`` (``os.environ`` by default)
    without :data:`GIT_SCRUBBED_ENV`, plus :data:`GIT_PINNED_ENV`."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k not in GIT_SCRUBBED_ENV}
    env.update(GIT_PINNED_ENV)
    return env


def run_git(
    cwd: Path | str,
    *args: str,
    timeout: float = GIT_TIMEOUT_SECONDS,
    text: bool = True,
    encoding: str | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess:
    """``git <args>`` in ``cwd`` -- the one entry point every git subprocess uses.

    Pins :data:`GIT_PINNED_CONFIG`, runs with :func:`git_env`, and is always bounded
    by ``timeout`` (a finite number of seconds; there is no way to ask for none).
    Never checks the return code: callers read it, as they always did.

    Raises :class:`FileNotFoundError` when git is not installed, :class:`GitTimeout`
    when the call ran out of time, and any other :class:`OSError` the spawn raised
    (``cwd`` missing or not a directory). All three are ``OSError``, so a caller's
    existing "git could not say" branch covers each.
    """
    if timeout is None or not (0 < timeout < float("inf")):
        raise ValueError(f"run_git needs a finite positive timeout, got {timeout!r}")
    try:
        return subprocess.run(
            ["git", *GIT_PINNED_CONFIG, *args],
            cwd=cwd,
            capture_output=True,
            text=text,
            encoding=encoding,
            errors=errors,
            check=False,
            env=git_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitTimeout(
            f"`git {' '.join(args)}` in {cwd} did not finish within {timeout:g}s and was stopped"
        ) from exc


def _git_could_not_answer(exc: OSError, root: Path, verb: str) -> KyoseiError:
    """KYO-417.c5: a git that ran and was stopped at its bound (:class:`GitTimeout`),
    or could not start in ``root`` -- worded as that, never as "git is not installed",
    which is the ``FileNotFoundError`` each caller still names on its own."""
    return KyoseiError(f"git could not answer in {root} ({exc}); cannot {verb}")


def _git_rev_parse_head(root: Path) -> str:
    """The base sha `kyo dispatch` freezes into a brief (law 16: read from git,
    never typed from memory). Errors cleanly when ``root`` is not a git repository —
    the failure this build was explicitly asked to make clean rather than a bare
    traceback (KYO-7.c1)."""
    try:
        result = run_git(root, "rev-parse", "HEAD")
    except FileNotFoundError as exc:
        raise KyoseiError(f"git is not available on PATH ({exc}); cannot dispatch") from exc
    except OSError as exc:
        raise _git_could_not_answer(exc, root, "dispatch") from exc
    if result.returncode != 0:
        raise KyoseiError(
            f"{root} does not look like a git repository (`git rev-parse HEAD` failed: "
            f"{result.stderr.strip()}); `kyo dispatch` needs a git repo to stamp a base sha"
        )
    sha = result.stdout.strip()
    if not sha:
        raise KyoseiError(f"`git rev-parse HEAD` in {root} produced no output")
    return sha


def _git_is_ancestor(root: Path, ancestor_sha: str, descendant_sha: str) -> bool | None:
    """KYO-10: is ``ancestor_sha`` reachable from ``descendant_sha`` (i.e. did the
    worker branch from the dispatch's recorded base sha, or somewhere after it)?

    ``True``/``False`` when git can answer; ``None`` when it can't (git missing,
    ``root`` not a repo, or either sha unknown here) — callers must treat ``None``
    as "no verdict", not as either answer, per law 6 (never let "unknown" quietly
    read as "fine"). Best-effort only: never raises, because this only sharpens an
    already-optional informational note in ``kyo report``'s output, and a report
    must still ingest even if this repo can't resolve the shas (e.g. the worker's
    branch was never merged/fetched here).
    """
    try:
        result = run_git(root, "merge-base", "--is-ancestor", ancestor_sha, descendant_sha)
    except OSError:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _git_commit_reachability(root: Path, sha: str, branch: str) -> tuple[str, str]:
    """KYO-32: is ``sha`` reachable from ``branch``? Returns one of the three
    :data:`contracts.BRANCH_REACH_OUTCOMES` plus a short human reason.

    Deliberately NOT :func:`_git_is_ancestor`, even though the underlying git
    plumbing (``merge-base --is-ancestor``) is the same. That helper exists to
    sharpen an *optional informational note* about ``worked_from_sha``: it never
    raises, collapses every failure mode into a single ``None``, and its one caller
    treats ``None`` as "say a bit less". Reusing it here would leave that caller's
    behaviour untouched but would make its own documented contract false, and — the
    reason that matters — ``--is-ancestor`` exits 128 for *both* "no such branch"
    and "no such commit", so a single ``None`` cannot tell a controller which of the
    two it is. This function checks each ref separately precisely so the "cannot
    tell" outcome can say what could not be told, which is what law 6 asks of it.

    Never raises: an unusable git is a ``cannot-tell``, not a crash, because the
    report itself must still be ingestable in a clone that cannot resolve the refs.
    """

    def run(*git_args: str) -> subprocess.CompletedProcess | None:
        try:
            return run_git(root, *git_args)
        except OSError:
            return None

    probe = run("rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
    if probe is None:
        return (
            BRANCH_REACH_CANNOT_TELL,
            f"git is not available (or {root} is unusable), so nothing here could be checked",
        )
    if probe.returncode != 0:
        return (
            BRANCH_REACH_CANNOT_TELL,
            f"branch {branch!r} does not resolve to a commit in {root}",
        )
    sha_probe = run("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if sha_probe is None or sha_probe.returncode != 0:
        return (
            BRANCH_REACH_CANNOT_TELL,
            f"commit {sha} is not an object this repository knows ({root})",
        )
    result = run("merge-base", "--is-ancestor", sha, branch)
    if result is None:
        return (BRANCH_REACH_CANNOT_TELL, "git could not be run to compare them")
    if result.returncode == 0:
        return (BRANCH_REACH_REACHABLE, f"reachable from {branch}")
    if result.returncode == 1:
        return (BRANCH_REACH_OFF_BRANCH, f"not reachable from {branch}")
    return (
        BRANCH_REACH_CANNOT_TELL,
        f"`git merge-base --is-ancestor` exited {result.returncode}: {result.stderr.strip()}",
    )


def _git_current_branch(root: Path) -> str:
    """The branch `kyo check-branch` inspects when the caller doesn't name one
    explicitly — always read from git, never assumed (law 16)."""
    try:
        result = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    except FileNotFoundError as exc:
        raise KyoseiError(f"git is not available on PATH ({exc}); cannot check-branch") from exc
    except OSError as exc:
        raise _git_could_not_answer(exc, root, "check-branch") from exc
    if result.returncode != 0:
        raise KyoseiError(
            f"{root} does not look like a git repository (`git rev-parse --abbrev-ref HEAD` "
            f"failed: {result.stderr.strip()})"
        )
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        raise KyoseiError(
            f"{root} is in a detached HEAD state; pass an explicit branch to `kyo check-branch`"
        )
    return branch


def _git_head_or_none(root: Path) -> str | None:
    """A soft variant of :func:`_git_rev_parse_head`: `kyo sweep` must not crash
    outside a git repo, only skip its touch-changed flag class for that run."""
    try:
        result = run_git(root, "rev-parse", "HEAD")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _git_commit_exists(root: Path, sha: str) -> bool:
    """KYO-63: does ``sha`` name a commit *this* repository actually has?

    `kyo sweep` records a base sha and diffs from it on the next run. Under the split
    layout (KYO-54) the repository holding that bookkeeping is not the repository the
    sha belongs to, so nothing guarantees a given clone of the code contains it: it may
    be shallow, a fork, an unmerged branch, or a history that was rewritten. This is the
    question that has to be asked *before* the diff, because
    :func:`_git_diff_paths` collapses "the base is not here" into the same ``None`` as
    every other diff failure, and sweep must be able to say which it hit.

    ``False`` for an unusable git or an unusable root as well: the caller's only use for
    the answer is "may I trust a diff from this sha", and every one of those cases means
    no.
    """
    try:
        result = run_git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    except OSError:
        return False
    return result.returncode == 0


def _git_history_identity(root: Path) -> str | None:
    """KYO-63: an identity for the *history* in ``root`` — its root commit sha(s),
    sorted and comma-joined — or ``None`` when one cannot be established honestly.

    A sha alone cannot tell a sweep whether it is looking at the repository it swept
    last time: an absent commit and a foreign repository look identical from inside a
    failed diff. The root commit is the cheapest thing that identifies a history rather
    than a location: it survives cloning, renaming, forking and re-remoting (all of
    which keep the same history, and for which a sweep base is still meaningful), and it
    differs for an unrelated repository (for which it is not). Merge-of-two-histories
    repositories have several roots, hence the sorted list rather than one value.

    ``None`` — never a guess — when: git is unavailable, ``root`` is not a repository or
    has no commits, or the repository is **shallow**. A shallow clone's oldest commit is
    a graft boundary, not a root, so recording it would compare unequal to the same
    repository's real root and manufacture a "different history" verdict out of a
    deepening. ``None`` means "identity not established", and callers must not read it
    as either "same" or "different" (law 6).
    """

    def run(*git_args: str) -> subprocess.CompletedProcess | None:
        try:
            return run_git(root, *git_args)
        except OSError:
            return None

    shallow = run("rev-parse", "--is-shallow-repository")
    if shallow is None or shallow.returncode != 0 or shallow.stdout.strip() == "true":
        return None
    roots = run("rev-list", "--max-parents=0", "HEAD")
    if roots is None or roots.returncode != 0:
        return None
    shas = sorted(line.strip() for line in roots.stdout.splitlines() if line.strip())
    return ",".join(shas) or None


CHANGED_PATH_FLAGS = ("--name-only", "--no-renames", "-z")
"""KYO-401: the flags every changed-path listing in this module shares, in one place.

``--no-renames`` because git's default rename detection prints only a rename's
DESTINATION: ``git mv ideas/x.md docs/x.md`` would list ``docs/x.md`` alone, so
`kyo check-branch` never saw the guarded source it deletes, touch drift never saw the
file that left, and no docs obligation was raised for the governed path that moved.
Without it a rename is a delete plus an add, and both sides reach every consumer.

``-z`` because without it git C-quotes any path with a non-ASCII byte, a quote, a
backslash or a control character (``"ideas/caf\\303\\251.md"``), and newline-splitting
cannot carry a path that contains a newline. With it git prints every path verbatim,
NUL-terminated, whatever ``core.quotePath`` says — which is why that setting is not
also passed: under ``-z`` it changes nothing, so it would be a mechanism no test could
catch the removal of.
"""


def _run_changed_paths(root: Path, command: str, *args: str) -> subprocess.CompletedProcess:
    """``git <command> --name-only --no-renames -z <args>``, decoded as UTF-8.

    Raises ``OSError`` (``FileNotFoundError`` when git is missing) for each caller to
    turn into its own failure shape — three of them return ``None`` and one raises by
    name. Undecodable bytes are replaced rather than raising, so a guarded prefix such
    as ``ideas/`` still matches on a path whose tail is not UTF-8.
    """
    return run_git(
        root, command, *CHANGED_PATH_FLAGS, *args, encoding="utf-8", errors="replace"
    )


def _split_changed_paths(stdout: str) -> list[str]:
    """The NUL-terminated path list :func:`_run_changed_paths` printed, in git's order.

    Never stripped: under ``-z`` a leading or trailing space is part of the path.
    """
    return [path for path in stdout.split("\0") if path]


def branch_changed_files(root: Path, against: str, branch: str) -> list[str]:
    """``git diff --name-only <against>...<branch>`` — what a branch actually changed.

    KYO-71 lifted this out of ``cmd_check_branch.cmd_check_branch``, which is the only place
    it existed. Two checks now ask the same question of the same three-dot diff — "did
    this branch touch the store?" and "did it edit outside what its record claimed?" —
    and the second is only cheap *because* the first already computes this. Two copies
    of the diff could disagree about the merge base and would eventually be given
    different flags; one function cannot (law 3).

    Raises rather than returning a sentinel: both callers need the refs verified by
    name so their error says which ref was unknown, and neither has a useful "the diff
    could not be computed" branch — unlike :func:`_git_diff_paths`, whose caller
    (`kyo sweep`) genuinely does.
    """

    def not_available(exc: OSError) -> KyoseiError:
        return KyoseiError(
            f"git is not available on PATH ({exc}); cannot diff {against}...{branch}"
        )

    def could_not_run(exc: OSError) -> KyoseiError:
        return KyoseiError(f"cannot diff {against}...{branch} in {root} ({exc})")

    for ref in (branch, against):
        try:
            verified = run_git(root, "rev-parse", "--verify", "--quiet", ref)
        except FileNotFoundError as exc:
            raise not_available(exc) from exc
        except OSError as exc:
            raise could_not_run(exc) from exc
        if verified.returncode != 0:
            raise KyoseiError(
                f"{ref!r} is not a known git ref in {root} "
                f"(`git rev-parse --verify {ref}` failed)"
            )
    # KYO-401: renames arrive as both sides, and paths arrive unquoted — see
    # CHANGED_PATH_FLAGS for what each flag closed.
    try:
        diff = _run_changed_paths(root, "diff", f"{against}...{branch}")
    except FileNotFoundError as exc:
        raise not_available(exc) from exc
    except OSError as exc:
        raise could_not_run(exc) from exc
    if diff.returncode != 0:
        raise KyoseiError(
            f"`git diff {' '.join(CHANGED_PATH_FLAGS)} {against}...{branch}` failed: "
            f"{diff.stderr.strip()}"
        )
    return _split_changed_paths(diff.stdout)


def commit_changed_files(root: Path, sha: str) -> list[str] | None:
    """The files ``sha`` itself changed against its first parent, or ``None`` if the
    diff could not be read.

    KYO-344: the fallback source for touch drift once a dispatch's branch is gone and
    its report named no ``touched_files[]``. A report's ``commits[]`` is what
    :func:`_check_report_commits_on_branch` has already proved reachable from the
    branch at ingest time (KYO-32); once every one of them is *also* reachable from the
    trunk, the union of what each commit's own diff touched stands in for the branch
    diff that no longer has a ref to compute. ``git diff-tree`` rather than
    :func:`branch_changed_files`'s ``git diff`` — there is no branch tip left to name a
    range against, only the commits themselves.

    ``None`` rather than an empty list on failure, on :func:`_git_diff_paths`'s own
    reasoning: the caller must not read "could not tell" as "changed nothing".
    """
    try:
        # KYO-401: CHANGED_PATH_FLAGS for the same reasons as the branch diff. The
        # plumbing `diff-tree` already ignores `diff.renames`, so `--no-renames` only
        # pins what it does today; `-z` is what un-quotes the paths here.
        result = _run_changed_paths(root, "diff-tree", "--no-commit-id", "-r", sha)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return _split_changed_paths(result.stdout)


def branch_is_contained_in(root: Path, branch: str, against: str) -> bool | None:
    """KYO-85: is every commit on ``branch`` already reachable from ``against``?

    The question :func:`branch_changed_files` cannot answer about its own result. That
    diff is three-dot — ``merge-base(against, branch)..branch`` — so once ``branch`` is
    merged the merge base *is* the branch tip and the diff is empty **by construction**,
    for a branch that changed a hundred files. An empty list from a merged branch is not
    a finding; it is the absence of one, and the caller has to be able to tell the two
    apart (law 6).

    ``True``/``False`` when git can answer; ``None`` when it cannot — git missing,
    ``root`` unusable, or either ref unknown here. Never raises: the caller's other
    outcomes are all reportable, and a check that crashed rather than saying "could not
    tell" would be the same silence one level along.
    """

    def run(*git_args: str) -> subprocess.CompletedProcess | None:
        try:
            return run_git(root, *git_args)
        except OSError:
            return None

    for ref in (branch, against):
        probe = run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if probe is None or probe.returncode != 0:
            return None
    result = run("merge-base", "--is-ancestor", branch, against)
    if result is None:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _git_diff_paths(root: Path, from_sha: str, to_sha: str) -> set[str] | None:
    """Files changed between two shas, or ``None`` if the diff could not be run
    (e.g. ``from_sha`` no longer resolves after a history rewrite since the
    last sweep). `kyo sweep` treats ``None`` as "skip touch-changed this run",
    never a crash — the other two flag classes still run regardless."""
    if from_sha == to_sha:
        return set()
    try:
        # KYO-401: a rename is its source AND its destination here too.
        result = _run_changed_paths(root, "diff", from_sha, to_sha)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return set(_split_changed_paths(result.stdout))


def _git_diff_added_paths(root: Path, from_sha: str, to_sha: str) -> set[str] | None:
    """The files CREATED between two shas, or ``None`` if the diff could not be run.

    :func:`_git_diff_paths` with ``--diff-filter=A``, and ``None`` on exactly the same
    conditions, so a caller that already tolerates "the window could not be read" needs
    no second tolerance. Its one consumer is `kyo sweep`'s touch-changed check
    (KYO-225.c3): a record that declared `+src/x.py` — *this work creates that file* —
    must not be flagged for staleness by the commit that created it, which is the
    record's own declared output arriving. Distinguishing that from a later edit to the
    same file is not something the path list alone can do, which is why this asks git
    for the one bit the path list is missing rather than guessing from existence.

    Returning ``None`` here is never read as "nothing was added": the caller falls back
    to treating every changed path as an ordinary change, which is what it did before
    this existed. A false flag is recoverable; a silently suppressed one is not.
    """
    if from_sha == to_sha:
        return set()
    try:
        # KYO-401: under `--no-renames` a rename's destination is an `A`, so a file a
        # record declared `+path` and created by moving another file counts as added.
        result = _run_changed_paths(root, "diff", "--diff-filter=A", from_sha, to_sha)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return set(_split_changed_paths(result.stdout))


def _git_diff_hunks(root: Path, revs: list[str], path: str) -> list[DiffHunk] | None:
    """The ``@@`` hunk headers of one file's diff over ``revs``, or ``None``.

    KYO-300: the one bit of git the symbol-aware checks need and the file-level ones
    never did. ``revs`` is passed through verbatim so the SAME function answers both
    callers without learning what either is doing — `kyo sweep` hands it
    ``[prior_sha, current_sha]`` (two-dot, the window it is sweeping) and `kyo
    touch-drift` hands it ``[f"{against}...{branch}"]`` (three-dot, the branch's own
    work), which is exactly the pair of ranges each of them already computes its file
    list from.

    ``--unified=0`` because the question is which lines changed, not which lines are
    nearby: three lines of context on either side would push a hunk into the region of
    the symbol above and below every real edit, and the whole point of this is to stop
    saying "your symbol changed" about an edit that was somewhere else.

    ``None`` — never an empty list — whenever git cannot answer, on exactly the
    conditions :func:`_git_diff_paths` returns ``None`` on. Every caller reads ``None``
    as "the region question is unanswerable, so flag the file", which is the direction
    the whole KYO-300 section of :mod:`kyosei.sweep` leans.
    """
    try:
        result = run_git(root, "diff", "--unified=0", *revs, "--", path)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_diff_hunks(result.stdout)


def _git_file_at(root: Path, rev: str, path: str) -> str | None:
    """``path``'s text as of ``rev`` (``git show REV:PATH``), or ``None`` (KYO-406).

    The other half of :func:`_git_diff_hunks`. A hunk's new-side line numbers belong to
    the tree at the diff's new side, so the symbol regions they are tested against must
    be placed in THAT tree's copy of the file -- never in whatever sits in the checkout,
    which may carry uncommitted edits (the sweep) or not carry the branch's edits at all
    (touch drift), and in either case numbers its lines differently.

    ``None`` whenever git cannot answer: git missing, ``rev`` unknown, or ``path`` absent
    at ``rev`` (deleted in that commit). Callers read it as "the region cannot be
    placed", never as "no region changed". Decoded with ``errors="replace"``, as the
    working-tree reads this replaced were.
    """
    try:
        result = run_git(root, "show", f"{rev}:{path}", text=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def worktree_admin_entries(root: Path) -> dict[str, bool] | None:
    """``git worktree list --porcelain`` in ``root``, as ``{absolute path: is
    prunable}`` -- git's OWN admin state, not kyosei's (KYO-174.c4).

    The record this fixes names the trap directly: ``git worktree list`` calls an
    entry ``prunable`` when its directory is gone and its admin file is stale, which
    is a near-opposite fact from `kyo worktrees`' own ``prunable`` (a live, clean,
    finished directory this tool would still act on). A `kyo worktrees` row cannot
    say "git still holds a stale entry here, run `git worktree prune`" without
    asking git this exact question, which is what this function is for. KYO-402 gave
    it a second consumer, :func:`registered_linked_worktrees`: the same listing is
    what says which recorded directories are worktrees of this repository at all,
    and so which ones `kyo worktrees --prune` may remove.

    ``None`` -- never a guess -- when git cannot answer at all: git missing,
    ``root`` unusable, or the command exits non-zero. Never raises, and law 6 says
    "cannot tell" must not read as "no stale entries" -- nor, since KYO-402, as "no
    registered worktrees", which is why a prune removes nothing on ``None``.
    """
    try:
        result = run_git(root, "worktree", "list", "--porcelain")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    entries: dict[str, bool] = {}
    current: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree ") :].strip()
            if current:
                entries[current] = False
        elif current is not None and (line == "prunable" or line.startswith("prunable ")):
            entries[current] = True
    return entries


def registered_linked_worktrees(root: Path) -> set[Path] | None:
    """The directories ``git worktree list --porcelain`` in ``root`` names as LINKED
    worktrees of this repository -- every entry but the first (git always lists the
    main working tree first) -- each resolved, so a caller can compare them against
    another resolved path (KYO-402).

    This is the only set `kyo worktrees --prune` may remove from. A directory a
    dispatch merely *recorded* is a claim in the store; a directory git lists here
    is one ``git worktree add`` created against this repository's own object store,
    so removing it cannot take a separate clone, a subdirectory of the checkout or a
    store root with it. ``None`` -- never a guess -- when git cannot answer.
    """
    entries = worktree_admin_entries(root)
    if entries is None:
        return None
    return {Path(path).resolve() for path in list(entries)[1:]}


def worktree_remove(root: Path, path: Path) -> tuple[bool, str]:
    """``git worktree remove <path>`` in ``root`` -- one registered worktree, its
    directory and its admin entry together, in one act git itself performs (KYO-402).

    It replaced ``shutil.rmtree`` followed by a bare repository-wide ``git worktree
    prune``: the rmtree removed whatever directory a dispatch had recorded, registered
    or not, and the prune then cleared every stale admin entry in the repository,
    including ones this run had nothing to do with. Deliberately no ``--force``: the
    caller only reaches here for a row it has already judged clean, and a worktree git
    still refuses to remove (locked, or dirty after the judgment was made) is a
    reported failure, not something to push through. Gitignored files go with the
    directory, as they would with any removal.

    ``(True, output)`` on success; ``(False, reason)`` when git refused or could not
    be run. Never raises.
    """
    try:
        result = run_git(root, "worktree", "remove", str(path))
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (
            result.stderr.strip() or result.stdout.strip() or "`git worktree remove` failed"
        )
    return True, result.stdout.strip()


def last_commit_at(root: Path, relative_path: str) -> str | None:
    """The ISO-8601 commit date of the last commit touching ``relative_path``.

    ``None`` when git cannot answer at all — git absent, ``root`` not a repository,
    the path untracked, or the repository empty. Law 6: the four are one answer here
    only because every caller does the same thing with them (say "could not tell"),
    and none of them may be read as "unchanged".

    Split out of ``ideas._git_last_commit_at`` by KYO-109, which needed the same
    question asked of a doc and of the files it ``governs:``. It is the same question,
    so it is asked in one place — and this module is where every ``kyo`` verb asks
    git anything (KYO-57).
    """
    try:
        # KYO-417: keeps its own 5s bound (it runs once per governed doc and target),
        # which before this raised TimeoutExpired past the `except OSError` below.
        result = run_git(root, "log", "-1", "--format=%cI", "--", relative_path, timeout=5)
    except OSError:
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return None
    return line


def resolve_commit(root: Path, ref: str) -> str | None:
    """The full sha ``ref`` names as a commit in ``root``, or ``None`` when git cannot
    resolve it to one (an unknown ref, a tree or blob, git absent) -- KYO-485's
    ``kyo dispatch --base``, which must refuse before any row is written. Never raises."""
    try:
        result = run_git(
            root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"
        )
    except OSError:
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def local_branch_sha(root: Path, branch: str) -> str | None:
    """The sha ``refs/heads/<branch>`` points at in ``root``, or ``None`` when no such
    local branch exists or git cannot answer (KYO-485). Never raises."""
    return resolve_commit(root, f"refs/heads/{branch}")


def checked_out_branch(worktree: Path) -> str | None:
    """The short name of the branch checked out in ``worktree``, or ``None`` when it is
    detached, not a checkout, or git cannot answer (KYO-485). Never raises."""
    try:
        result = run_git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
    except OSError:
        return None
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else None


def worktree_add(
    root: Path, path: Path, branch: str, *, start_point: str | None
) -> tuple[bool, str]:
    """``git worktree add`` in ``root`` -- the counterpart of :func:`worktree_remove`,
    and since KYO-485 the act `kyo dispatch --branch` performs instead of printing.

    With ``start_point`` (a sha), ``-b <branch> <start_point>``: a new branch cut
    there. Without it, ``<branch>`` is an existing branch and is checked out. Never
    ``--force``: a branch already checked out elsewhere, or a path git still holds a
    stale entry for, is git's refusal to report, not something to push through.

    ``(True, output)`` on success; ``(False, git's message)`` otherwise. Never raises.
    """
    args = ["worktree", "add", str(path)]
    args += ["-b", branch, start_point] if start_point is not None else [branch]
    try:
        result = run_git(root, *args)
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (
            result.stderr.strip() or result.stdout.strip() or "`git worktree add` failed"
        )
    return True, (result.stderr.strip() or result.stdout.strip())


def branch_delete(root: Path, branch: str) -> tuple[bool, str]:
    """``git branch -D <branch>`` in ``root`` -- only ever for a branch the caller has
    just created and committed nothing to (KYO-485's undo of a dispatch that failed
    after its worktree was made). ``(ok, message)``; never raises."""
    try:
        result = run_git(root, "branch", "-D", branch)
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr.strip() or "`git branch -D` failed"
    return True, result.stdout.strip()
