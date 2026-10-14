"""``kyo check-clean`` — a worker may not stop with uncommitted work in its worktree.

The porcelain reading, the content re-test, and how loud an unresolved stop is. The stop
resolution it shares with ``check-branch`` is :mod:`kyosei.cmd_guard`.
"""


from __future__ import annotations

import json
import os
import shlex
import subprocess
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import git_queries, stop_audit
from .clock import now
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
    LAYOUT_IN_REPO,
    STORE_HOME_ENV,
    git_worktree_facts,
    store_home,
)
from .contracts import (
    CONTRACT_WORKTREE_HAS_UNCOMMITTED_WORK,
    GUARDED_BRANCH_DIRS,
    WORKER_REPORT_PATH,
)
from .errors import GuardRefusal, KyoseiError
from .stop_audit import TickProfile
from .stop_target import (
    STOP_TARGET_MATCHED,
    STOP_TARGET_NO_TRANSCRIPT,
    STOP_TARGET_NOT_A_WORKER,
    STOP_TARGET_UNRESOLVED,
    StopTarget,
    _payload_of,
)

# ------- KYO-169: how loud, decided by the POPULATION and never by the single row


def _population_profile(ctx: Context) -> TickProfile | None:
    """This store's unresolved stops, measured — or ``None`` when unmeasurable.

    The question asked here is deliberately not "is *this* stop real?": a tick's
    ``SubagentStop`` payload is shape-identical to a dispatched worker's, and any
    per-row rule would silence the real worker whose sidecar the harness failed to
    write, which is the one case the audit exists for (docs/design/guards.md#kyo-169).

    So the same question `kyo doctor` already asks is asked here, of the same
    public reader: what does the POPULATION look like. The answer changes how much
    transcript one stop costs and nothing else — no outcome, no exit code, no row.

    Never raises, and returns ``None`` rather than a default on every unknown: this
    runs inside a ``SubagentStop`` hook, where a read that failed must fall back to
    saying MORE, not less. ``None`` means "not measured", and every caller treats
    that as "print the full explanation".

    Cost (measured in docs/design/guards.md#kyo-169): the read is the same full scan
    `kyo doctor` and `kyo worktrees` already perform on every invocation — ``events``
    has no index on ``command`` — so it grows with the whole ledger rather than with the
    stop rows. It is paid only on the unresolved path, where something is about to be
    printed anyway, and only after :func:`~kyosei.stop_audit.record_unresolved_audit`
    (the writer for that path since KYO-418) has already opened the store, so
    :meth:`~kyosei.commands.Context.store` is a cache hit rather than a second open.
    """
    try:
        ledger = stop_audit.stop_audits(ctx.store())
    except Exception:  # noqa: BLE001 - a hook must never die on the store
        return None
    if ledger.unreadable is not None:
        return None
    return ledger.tick_profile()


def _tick_line(command: str, tick: TickProfile, audit: str | None) -> str:
    """The whole of what an unplaceable stop costs when the population is the tick.

    One line, and it points at the durable record instead of asking for an
    investigation: ``cmd_check_clean`` writes every stop down before any exit path (a
    row through ``record_stop_audit``, or since KYO-418 an unresolved stop's count in
    place through ``record_unresolved_audit``, which ``audit`` here reports), so this
    is a notification about a record, and a notification does not need to restate the
    record (docs/design/guards.md#kyo-169). What it drops, deliberately, is the closing
    imperative ("Check the tree this dispatch was sent to before merging it"), which on
    a tick asks a controller to investigate an event that did not happen: no agent
    finished.
    """
    cadence = (
        f"~{tick.cadence_seconds:.0f}s per running dispatch"
        if tick.cadence_seconds is not None
        else "the measured cadence"
    )
    return (
        f"{command}: stop not placed; {audit or 'NOT RECORDED'}. 1 of {tick.rows} "
        f"unresolved stops here with the measured SubagentStop-tick signature "
        f"({cadence}, a fresh agent id each) — the timer, not an agent finishing, "
        f"so there is nothing to investigate (KYO-163, KYO-169)"
    )


def _unresolved_lines(
    target: StopTarget,
    command: str,
    finding: str,
    audit: str | None = None,
    tick: TickProfile | None = None,
) -> list[str]:
    """What a hook-driven run says when it could not identify the worker's tree.

    It does NOT block, and that is a deliberate choice rather than an omission. Blocking
    here is the measured failure this record exists to end: a worker cannot commit paths
    that belong to the controller's branch, so a block on the controller's tree is a
    condition the only woken party is powerless to clear
    (docs/design/guards.md#kyo-128).

    Law 6 is satisfied by naming the outcome, not by picking a verdict: "we could
    not tell" is reported as itself, loudly, instead of being rendered as either
    "clean" or "dirty". `kyo check-branch` already takes this exact line for a
    repository with no store configured — exit 0 while saying out loud that it is
    guarding nothing, rather than blocking every stop in a project that never asked
    for one (KYO-54.c2) — and the same reasoning applies with more force here,
    because there the guard was vacuous and here it would be actively wrong.

    The party who can act on this is the controller, so the remedy is addressed to
    the controller and the worker is not woken at all.

    ``audit`` says where the same finding is kept durably
    (docs/design/guards.md#kyo-134). That record is a report, not a block: this
    function's exit code is unchanged and KYO-134.c4 is the test that keeps it that way.

    Once the finding is durable, printing it loudly is a notification about a record
    rather than the record (docs/design/guards.md#kyo-169). ``tick`` is the one input
    that shortens it, it is a measurement of the POPULATION, and it changes only how
    many lines are printed — never the exit code, never the row, never `kyo doctor`'s
    count (c5).
    """
    if target.outcome == STOP_TARGET_NOT_A_WORKER:
        # KYO-154.c1, the whole of it. What must not happen is the guard blocking an
        # agent for a tree it did not touch — and the helper case is the sharpest
        # version of that, because the block would be delivered to the one party who
        # can neither commit those files (they are its parent's, mid-edit), revert
        # them (that would destroy its parent's work), nor wait them out. Reporting
        # the finding is still worth doing: it is true of the tree, and it is what a
        # controller reading the transcript would want to see. Acting on it is not.
        return [
            *_subject_lines(target, command, audit),
            f"  what is outstanding in the parent's tree: {finding}",
            "  that work belongs to the worker still running here, not to the helper "
            "that just stopped — it is the parent's to commit before ITS own stop, and "
            "this guard will ask it for exactly that then",
            "  nothing is asked of the agent that stopped, and nothing is wrong: a "
            "worker's helper has no stop obligation of its own (KYO-154)",
        ]
    # KYO-169.c1/c4. The ONLY thing consulted here is the population's measured
    # signature; `target` is not examined, so no property of this row — its agent
    # id, its `why`, whether a transcript or a sidecar exists for it — can make it
    # quieter than its neighbours or louder than them. A genuine worker's unaudited
    # stop is therefore never singled out for silence; when the population is not
    # the tick (or could not be measured at all) EVERY unplaceable stop, that one
    # included, gets the full explanation below, unchanged (c2).
    if (
        tick is not None
        and tick.looks_like_the_tick
        and target.outcome == STOP_TARGET_UNRESOLVED
    ):
        return [_tick_line(command, tick, audit)]
    return [
        *_subject_lines(target, command, audit),
        f"  what was found there anyway: {finding}",
        "  this is information about the orchestrator's checkout, not a verdict about "
        "the worker that just stopped — nothing here is that worker's to clear",
        "  orchestrator: `kyo worktrees` lists the declared worktrees and now flags the "
        "ones an unaudited stop named; `kyo doctor` fails while such a dispatch is "
        "still running. Check the tree this dispatch was sent to before merging it",
    ]


# ------------- KYO-128.c4/c5: a status flag is not the same fact as a difference


@dataclass(frozen=True)
class _StatusEntry:
    """One entry of ``git status --porcelain -z``, parsed.

    ``line`` is rendered back into the non-``-z`` porcelain spelling this command
    has always printed, so the output a reader (and every existing test) sees is
    unchanged by the switch to NUL parsing.
    """

    code: str
    paths: tuple[str, ...]

    @property
    def line(self) -> str:
        if len(self.paths) > 1:
            return f"{self.code} {self.paths[1]} -> {self.paths[0]}"
        return f"{self.code} {self.paths[0]}"


def _porcelain_entries(stdout: str) -> list[_StatusEntry]:
    """Parse ``git status --porcelain -z``.

    Rename and copy entries carry a second, NUL-separated path (the original), so
    the fields cannot simply be split and zipped — the parser has to know when to
    consume two.
    """
    fields = stdout.split("\0")
    entries: list[_StatusEntry] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            # The trailing empty field, and anything too short to be `XY path`.
            continue
        code, path = field[:2], field[3:]
        paths = [path]
        if ("R" in code or "C" in code) and index < len(fields):
            paths.append(fields[index])
            index += 1
        entries.append(_StatusEntry(code, tuple(paths)))
    return entries


#: KYO-410: the arguments ``check-clean``'s ``git status`` passes so that what it
#: lists is decided here rather than by the user's git config. Each overrides a
#: setting that would make an uncommitted path invisible to the guard.
STATUS_PINNED_ARGS: tuple[str, ...] = ("--untracked-files=all", "--ignore-submodules=none")

#: KYO-410: the same for the content re-test's ``git diff --quiet``. `diff.external`
#: and a textconv driver are output-shaping config a diff honours; neither may decide
#: whether a flagged path is identical to HEAD, and a submodule the status call was
#: told to show must not be waved through here by `diff.ignoreSubmodules`.
DIFF_PINNED_ARGS: tuple[str, ...] = ("--no-ext-diff", "--no-textconv", "--ignore-submodules=none")


def _has_content_difference(run_git, entry: _StatusEntry) -> bool:
    """Is this flagged path really different from HEAD, or only flagged?

    Three kinds of entry are dirty without asking git twice, and each for its own
    reason rather than out of caution:

    * **untracked** (``?``) — there is no HEAD blob to compare against, so "identical
      to HEAD" is not a question that can be asked. This is also the case the guard
      most exists for: a worker that died before its first commit leaves output that
      is untracked by definition, and the consumer's own example of this check
      working correctly was an untracked report file. KYO-206 gave the report
      its own excluded path, so what this case now catches is source a worker
      wrote and never committed.
    * **unmerged** (``U``) — a conflict in progress is uncommitted work whatever
      ``git diff`` says about it mid-merge.
    * **renamed or copied** (``R``/``C``) — the difference is in the index's idea of
      the path, which a content comparison of either path alone can miss.

    Everything else is re-tested against HEAD **twice**, and both are needed:

    * ``git diff --quiet HEAD -- <path>`` compares HEAD against the WORKING TREE.
    * ``git diff --quiet --cached HEAD -- <path>`` compares HEAD against the INDEX.

    Only the first would be a real hole rather than a nicety. A path staged with one
    content and then restored in the working tree reports ``MM``, and ``git diff
    HEAD`` on it is empty — while ``git commit`` would still commit the staged
    content. Dropping it as "identical to HEAD" would lose committed-intent work and
    reintroduce a false negative one layer down from the one being fixed.

    Exit 1 means differences; anything above 1 means git could not answer at all, and
    law 6 says an unknown must not be read as "clean".
    """
    if any(flag in entry.code for flag in "?URC"):
        return True
    for path in entry.paths:
        for against_index in (False, True):
            args = ["diff", "--quiet", *DIFF_PINNED_ARGS]
            if against_index:
                args.append("--cached")
            if run_git(*args, "HEAD", "--", path).returncode != 0:
                return True
    return False


# ------------------- KYO-535: what a worker left OUTSIDE its worktree, against a baseline

#: KYO-535.c1. One worker left eight scratch files in the main checkout's
#: root, another's `kyo init` created ``~/kyosei/p1`` and registered it in the real
#: ``projects.json``, and a third's scratch copy was registered the same way. This
#: command read only the worker's own worktree, so a stop passed clean over all three.
#:
#: What is compared is a BASELINE taken by `kyo dispatch` the moment the dispatch row
#: exists, not a timestamp: a ``projects.json`` entry carries no time of its own, and
#: file birth times are not portable (Linux's Python exposes none), so "appeared after
#: the dispatch began" is answerable only against what was there when it began.
#:
#: Kept in the code repository's git common directory, one JSON file per dispatch, and
#: never in the store: the baseline is absolute paths on one machine (a home directory,
#: a username), which the git-canonical export must not carry (KYO-52), and git's
#: common directory is reachable from the main checkout and from every linked worktree
#: by the same `git rev-parse`, so `kyo dispatch` and this stop check find one file
#: without either knowing the other's environment. `kyo dispatch` removes the
#: baselines of dispatches that are no longer running each time it writes a new one.
#: Why it blocks only once, and the KYO-128 tension that decided it:
#: docs/design/guards.md#kyo-535.
BASELINE_DIRNAME = "kyosei-dispatch-baselines"

#: KYO-535.c1: the refusal code when the only thing wrong is outside the worktree.
CONTRACT_WORKER_WROTE_OUTSIDE_ITS_WORKTREE = "worker-wrote-outside-its-worktree"


def _git_common_dir(repo: Path) -> Path | None:
    try:
        result = git_queries.run_git(
            repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
    except OSError:
        return None
    text = result.stdout.strip()
    return Path(text) if result.returncode == 0 and text else None


def baseline_path(repo: Path, dispatch_id: str) -> Path | None:
    """Where ``dispatch_id``'s baseline lives, found from any checkout of ``repo``."""
    common = _git_common_dir(repo)
    return None if common is None else common / BASELINE_DIRNAME / f"{dispatch_id}.json"


def _untracked_in(main_checkout: Path) -> list[str] | None:
    """The main checkout's untracked (not ignored) files, ``None`` when git could not say.

    The store directories are left out for the reason they are left out of the worker's
    own check (KYO-17): the controller writes ``kyosei/`` and ``ideas/`` all session, and
    a worker can neither commit nor remove what is the controller's to commit.
    """
    exclusions = [f":(exclude,top){d.rstrip('/')}" for d in GUARDED_BRANCH_DIRS]
    try:
        result = git_queries.run_git(
            main_checkout,
            "status", "--porcelain", "-z", *STATUS_PINNED_ARGS, "--", ":(top)", *exclusions,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return sorted(
        entry.paths[0] for entry in _porcelain_entries(result.stdout) if entry.code == "??"
    )


def _home_dirs(home: Path) -> list[str] | None:
    """The directories directly under ``$KYOSEI_HOME``; ``None`` when unreadable.

    Top level only: a store `kyo init` creates is ``<home>/<project>``, while the
    controller's own new dispatch worktrees land two levels down, under
    ``<home>/<project>/dispatch worktrees/`` (KYO-70), and must not read as a leak.
    """
    try:
        return sorted(child.name for child in home.iterdir() if child.is_dir())
    except FileNotFoundError:
        return []
    except OSError:
        return None


def _registry_roots(home: Path) -> tuple[list[str] | None, str | None]:
    from .cmd_projects import REGISTRY_FILE, read_entries
    from .errors import KyoseiError as _Unreadable

    try:
        return [entry["root"] for entry in read_entries(home / REGISTRY_FILE)], None
    except _Unreadable as exc:
        return None, str(exc)


def _snapshot(main_checkout: Path, home: Path) -> dict:
    roots, problem = _registry_roots(home)
    return {
        "untracked": _untracked_in(main_checkout),
        "home_dirs": _home_dirs(home),
        "registry_roots": roots,
        "registry_problem": problem,
    }


def take_baseline(
    code_root: Path,
    dispatch_id: str,
    environ: dict[str, str],
    *,
    stale: Callable[[str], bool] = lambda _dispatch_id: False,
) -> tuple[Path | None, str]:
    """`kyo dispatch`'s half of KYO-535.c1: write ``dispatch_id``'s baseline.

    Returns the file written (``None`` when none could be) and the sentence the
    dispatch prints about it. Never raises: a dispatch that exists already is not
    undone because its baseline could not be written -- the stop check then says it
    had nothing to compare against, which is the failure stated rather than hidden.
    ``stale`` says which OTHER baselines may be removed (a dispatch no longer running).
    """
    path = baseline_path(code_root, dispatch_id)
    facts = git_worktree_facts(code_root)
    main_checkout = facts.main_checkout or facts.toplevel
    if path is None or main_checkout is None:
        return None, (
            f"no outside-the-worktree baseline for {dispatch_id}: {code_root} is not a git "
            "checkout, so its stop check will compare nothing outside the worktree (KYO-535)"
        )
    home = store_home(environ).expanduser()
    document = {
        "dispatch_id": dispatch_id,
        "taken_at": now(),
        "main_checkout": str(main_checkout),
        "kyosei_home": str(home),
        **_snapshot(main_checkout, home),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        for other in path.parent.glob("*.json"):
            if other != path and stale(other.stem):
                other.unlink(missing_ok=True)
    except OSError as exc:
        return None, (
            f"no outside-the-worktree baseline for {dispatch_id}: {path} could not be "
            f"written ({exc}), so its stop check will compare nothing outside the worktree "
            "(KYO-535)"
        )
    return path, (
        f"baseline for the stop check: {path} -- the main checkout's untracked files, the "
        f"directories under {home} and its projects.json entries, as they are now; a new "
        "one at this dispatch's stop is named to its worker (KYO-535)"
    )


@dataclass
class OutsideFinding:
    """What appeared outside a stopping worker's worktree since its dispatch began."""

    dispatch_id: str
    baseline: Path | None
    taken_at: str | None = None
    main_checkout: Path | None = None
    home: Path | None = None
    untracked: list[Path] = field(default_factory=list)
    home_dirs: list[Path] = field(default_factory=list)
    registry_roots: list[str] = field(default_factory=list)
    #: Each part that could not be compared, said rather than read as "nothing new".
    problems: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.untracked or self.home_dirs or self.registry_roots)

    def as_json(self) -> dict:
        return {
            "dispatch_id": self.dispatch_id,
            "baseline": None if self.baseline is None else str(self.baseline),
            "taken_at": self.taken_at,
            "main_checkout": None if self.main_checkout is None else str(self.main_checkout),
            "kyosei_home": None if self.home is None else str(self.home),
            "untracked_in_main_checkout": [str(p) for p in self.untracked],
            "new_home_dirs": [str(p) for p in self.home_dirs],
            "new_registry_roots": list(self.registry_roots),
            "problems": list(self.problems),
        }


def _same_path(a: str, b: str) -> bool:
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()


def outside_the_worktree(target: StopTarget | None) -> OutsideFinding | None:
    """KYO-535.c1: compare now against the stopping dispatch's baseline.

    Only for a stop placed as a dispatched worker's own (``matched``): every other
    outcome has no dispatch whose beginning there is to compare against. Paths inside
    the worker's own worktree are left to the check above, which already reads them.
    """
    if target is None or target.outcome != STOP_TARGET_MATCHED or not target.dispatch_id:
        return None
    dispatch_id = target.dispatch_id
    path = baseline_path(target.directory, dispatch_id)
    finding = OutsideFinding(dispatch_id, path)
    if path is None or not path.is_file():
        finding.problems.append(
            f"no baseline was taken for {dispatch_id} (dispatched before KYO-535, or "
            f"{'its baseline file is gone' if path is not None else 'no git common dir'}), "
            "so nothing outside its worktree was compared"
        )
        return finding
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
        main_checkout = Path(baseline["main_checkout"])
        home = Path(baseline["kyosei_home"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        finding.problems.append(f"the baseline {path} could not be read ({exc})")
        return finding
    finding.taken_at = baseline.get("taken_at")
    finding.main_checkout = main_checkout
    finding.home = home
    current = _snapshot(main_checkout, home)
    worktree = target.directory.resolve()

    before_untracked, after_untracked = baseline.get("untracked"), current["untracked"]
    if before_untracked is None or after_untracked is None:
        finding.problems.append(
            f"the untracked files of {main_checkout} could not be listed "
            f"{'at dispatch' if before_untracked is None else 'now'}"
        )
    else:
        for relative in sorted(set(after_untracked) - set(before_untracked)):
            absolute = main_checkout / relative
            if absolute.resolve().is_relative_to(worktree):
                continue
            finding.untracked.append(absolute)

    before_dirs, after_dirs = baseline.get("home_dirs"), current["home_dirs"]
    if before_dirs is None or after_dirs is None:
        finding.problems.append(f"the directories under {home} could not be listed")
    else:
        finding.home_dirs = [home / name for name in sorted(set(after_dirs) - set(before_dirs))]

    before_roots, after_roots = baseline.get("registry_roots"), current["registry_roots"]
    if before_roots is None or after_roots is None:
        finding.problems.append(
            f"{home / 'projects.json'} could not be read "
            f"({baseline.get('registry_problem') or current['registry_problem']})"
        )
    else:
        finding.registry_roots = [
            root
            for root in after_roots
            if not any(_same_path(root, old) for old in before_roots)
        ]
    return finding


def _outside_lines(finding: OutsideFinding, *, blocking: bool) -> list[str]:
    """Each path by name, the command that removes it, and what to do instead."""
    lines = [f"  outside the worktree: {problem} (KYO-535)" for problem in finding.problems]
    if not finding.found:
        return lines
    count = len(finding.untracked) + len(finding.home_dirs) + len(finding.registry_roots)
    home = finding.home
    lines.append(
        f"{count} path(s) OUTSIDE this worktree appeared after {finding.dispatch_id} was "
        f"dispatched (baseline {finding.taken_at}) -- by this worker, or by anyone else "
        "working since (KYO-535):"
    )
    lines += [
        f"  untracked in the main checkout: {path}  -- remove it: rm {shlex.quote(str(path))}"
        for path in finding.untracked
    ]
    lines += [
        f"  new directory under ${STORE_HOME_ENV} {home}: {path}  -- remove it: "
        f"rm -r {shlex.quote(str(path))}"
        for path in finding.home_dirs
    ]
    lines += [
        f"  new entry in {home / 'projects.json' if home else 'projects.json'}: {root}  -- "
        f"remove it: {STORE_HOME_ENV}={shlex.quote(str(home))} kyo projects remove "
        f"{shlex.quote(root)}"
        for root in finding.registry_roots
    ]
    if blocking:
        lines += [
            "  remove each one you made with the command beside it, then stop again; work "
            "you meant to keep belongs in your worktree, committed there. Scratch files go "
            "under your worktree or your brief's scratch directory, and a scratch "
            f"`kyo init` under a temporary home (`{STORE_HOME_ENV}=\"$(mktemp -d)\"`)",
            "  one that is not yours, or that the harness will not let you remove: leave "
            "it, name it in your final message, and stop again -- a repeat stop "
            "(stop_hook_active) reports these without blocking",
        ]
    else:
        lines.append(
            "  NOT blocking: this stop already follows a blocked one (stop_hook_active), so "
            "these are reported, not enforced -- orchestrator: each is removed by the "
            "command beside it"
        )
    return lines


@unblocks_repeat_stops("check-clean")
def cmd_check_clean(ctx: Context, args: Namespace) -> Output:
    """``kyo check-clean`` — KYO-17: the ``SubagentStop`` hook's uncommitted-work
    check, as a CLI command that excludes exactly the paths ``kyo check-branch``
    forbids. The two halves of the stop hook must never contradict each other: the
    controller's own writes under ``kyosei/`` would otherwise leave a worker one remedy
    ("commit them") that ``check-branch`` refuses (docs/design/guards.md#kyo-17).

    Read-only, like ``check-branch``: a git query, no store opened, no transaction.
    Exit 2 (contract violation) when the worktree holds work the worker really does
    own, so the hook blocks the stop; exit 0 when everything outstanding lives under
    :data:`GUARDED_BRANCH_DIRS`, which is the controller's to commit, not the
    worker's.

    **KYO-206: and :data:`WORKER_REPORT_PATH`, which is nobody's to commit.** The same
    contradiction one file along (docs/design/guards.md#kyo-206). The report is the
    dispatch's OUTPUT: `kyo report` ingests it and the store keeps it, so the code
    repository never needs a copy. Excluded here rather than left to `.gitignore` alone,
    so a project that predates the ignore entry `kyo init`/`kyo install-hooks` writes
    gets the fix too.
    """

    # KYO-128: which tree. `--subagent-stop` resolves the stopping worker's own
    # worktree from the hook payload; without it the subject is where you are
    # standing, which is what every hand-run invocation means.
    target, raw_payload = _stop_target(ctx, args)

    # KYO-134: this command is the one that writes the stop down, and it writes it
    # HERE — before anything is read about the tree and before any exit path can be
    # taken. Recording after the verdict would mean the one outcome most worth
    # keeping, a `git status` that raised in a directory the guard could not read,
    # is the one that never gets kept. Neither writer below ever raises or changes
    # an exit code; a write that failed comes back as a sentence saying so
    # (KYO-134.c4).
    #
    # KYO-375.c1: a not-a-worker tick is the one outcome that does NOT go through
    # `record_stop_audit` any more — it writes no row to the events ledger at all.
    # `record_tick_audit` counts it against each candidate dispatch's own row
    # instead, in place, so this is still the one command that writes the stop
    # down; what it writes down for a tick just is not a row.
    #
    # KYO-418.c6: and an `unresolved` stop -- a non-worker subagent whose prompt names
    # no dispatch, the bulk of the ledger's stop rows -- is counted the same way, on
    # each running candidate's counter (`record_unresolved_audit`). A row is still
    # written for `matched` and for the outcomes a human audits: a helper of a
    # dispatched worker, and a store that could not be read.
    audit_line: str | None = None
    audit_event_id: str | None = None
    if target is not None:
        count_in_place = stop_audit.COUNTED_IN_PLACE.get(target.outcome)
        if count_in_place is not None:
            audit_line = count_in_place(ctx, target)
        else:
            audit_event_id, audit_line = stop_audit.record_stop_audit(ctx, target, raw_payload)

    layout_kind = ctx.layout.kind if ctx.layout is not None else LAYOUT_IN_REPO

    # KYO-373.c2: recorded above, classified, and nothing read — see
    # :func:`_untranscribed_output`. The `git status` below would run in the
    # controller's cwd, which is the fallback this outcome exists to retire.
    if target is not None and target.outcome == STOP_TARGET_NO_TRANSCRIPT:
        return _untranscribed_output(
            target,
            "check-clean",
            audit_line,
            {
                "command": "check-clean",
                "layout": layout_kind,
                "root": None,
                "store_root": str(ctx.root),
                "excluded_dirs": list(GUARDED_BRANCH_DIRS),
                "excluded_report_path": WORKER_REPORT_PATH,
                "dirty_entries": [],
                "flagged_entries": [],
                "stat_only_entries": [],
                "stop_audit_event": audit_event_id,
                "stop_audit": audit_line,
                "population_looks_like_the_tick": None,
            },
        )

    # The worktree the worker is actually in — the code repository, not the store
    # (KYO-54): under the split layout the store is a different repository, whose
    # cleanliness is the controller's business and never a worker's.
    repo = target.directory if target is not None else ctx.code_root

    def run_git(*git_args: str) -> subprocess.CompletedProcess:
        try:
            return git_queries.run_git(repo, *git_args)
        except FileNotFoundError as exc:
            raise KyoseiError(f"git is not available on PATH ({exc}); cannot check-clean") from exc
        except (NotADirectoryError, OSError) as exc:
            raise KyoseiError(f"cannot run git in {repo} ({exc}); cannot check-clean") from exc

    # `:(exclude,top)` is git's own exclusion pathspec, anchored at the repository
    # root — the same anchoring `check-branch` assumes when it compares
    # repo-root-relative diff paths against these prefixes. Derived from
    # GUARDED_BRANCH_DIRS, never restated, so the two checks cannot drift apart.
    #
    # KYO-17 settled WHICH paths are excluded and KYO-54 settled WHICH repository is
    # read. Neither is touched here; KYO-128 is about which *worktree* and about what
    # a flag on a path actually means.
    #
    # KYO-206 adds one FILE to the same list, and for the same reason the directories
    # are there: the two halves of the stop hook must not contradict each other. The
    # report is the worker's
    # OUTPUT, not its work: it is ingested by `kyo report`, stored structurally, and
    # has no business in the code repository's history. Excluded here whether or not
    # the project's `.gitignore` carries the entry `kyo init`/`kyo install-hooks`
    # write, so a project that predates that entry is fixed too.
    exclusions = [f":(exclude,top){d.rstrip('/')}" for d in GUARDED_BRANCH_DIRS]
    exclusions.append(f":(exclude,top){WORKER_REPORT_PATH}")
    # `-z`: NUL-separated and never quoted. The paths below are fed straight back to
    # `git diff`, and porcelain's default quoting of unusual names would send a
    # different path than the one git flagged.
    #
    # KYO-410: the listing is PINNED, never inherited from the user's git config:
    # `status.showUntrackedFiles=no` would list no untracked file at all, and a worker that
    # never committed its new modules would stop "clean" (docs/design/guards.md#kyo-410).
    # `--untracked-files=all` also names each file inside a new directory rather than the
    # directory alone, and `--ignore-submodules=none` overrides `diff.ignoreSubmodules` /
    # `submodule.<name>.ignore`, which would hide a submodule the worker moved. See
    # STATUS_PINNED_ARGS.
    status = run_git(
        "status", "--porcelain", "-z", *STATUS_PINNED_ARGS, "--", ":(top)", *exclusions
    )
    if status.returncode != 0:
        raise KyoseiError(
            f"`git status --porcelain` failed in {repo}: {status.stderr.strip()}"
        )
    flagged = _porcelain_entries(status.stdout)
    guarded_label = " or ".join(GUARDED_BRANCH_DIRS)
    # KYO-206: what this command actually excluded, which is now the store directories
    # AND the worker's report path. Two labels rather than one, because they are excluded
    # for two different reasons and the output says which: the directories are the
    # controller's to commit, and the report is nobody's to commit.
    excluded_label = f"{guarded_label} or {WORKER_REPORT_PATH}"

    # KYO-128.c4/c5. `git status` reports what the index says; it can flag a path whose blob
    # is identical to HEAD — a stale stat-cache entry, or a commit landing between the
    # hook's own two observations (docs/design/guards.md#kyo-128). Each flagged TRACKED path
    # is re-tested for a real content difference; an untracked one has no HEAD blob to
    # compare against and stays dirty, which is the case that matters most (a dead worker's
    # uncommitted source is untracked by definition).
    dirty_entries: list[_StatusEntry] = []
    stat_only_entries: list[_StatusEntry] = []
    for entry in flagged:
        (dirty_entries if _has_content_difference(run_git, entry) else stat_only_entries).append(
            entry
        )
    dirty = [entry.line for entry in dirty_entries]
    stat_only = [entry.line for entry in stat_only_entries]
    stat_only_note = (
        [
            f"  {len(stat_only)} path(s) carried a `git status` flag but are identical to "
            "HEAD and were not counted as work (KYO-128.c4):",
            *(f"    {line}" for line in stat_only),
        ]
        if stat_only
        else []
    )

    # KYO-535.c1: what appeared OUTSIDE the worktree since this dispatch began, for a
    # stop placed as the worker's own. `None` on every other outcome and on a hand run.
    outside = outside_the_worktree(target)
    # A stop that already follows a blocked one: Claude Code sets this when the agent
    # is continuing because a stop hook blocked it. It is what bounds a finding the
    # worker cannot clear -- another agent's file, or a removal the harness refuses --
    # to ONE wake, the KYO-128 lesson about blocking a party powerless to act. Since
    # KYO-592 the refusal below is still raised on a repeat stop, and
    # :func:`cmd_check_clean` turns it into a report and a recorded row, as it does for
    # a dirty worktree; here it only chooses the outside lines' wording.
    repeat_stop = _payload_of(raw_payload).get("stop_hook_active") is True
    outside_blocks = outside is not None and outside.found
    outside_lines = (
        _outside_lines(outside, blocking=not repeat_stop) if outside is not None else []
    )

    unresolved = target is not None and not target.resolved
    # KYO-169. Measured only where it can change anything: an unplaceable stop, on a
    # hook-driven run, is the one case about to print the eight-line explanation.
    # A matched stop, a helper's stop and every hand-run invocation pay nothing.
    tick = (
        _population_profile(ctx)
        if unresolved and target is not None and target.outcome == STOP_TARGET_UNRESOLVED
        else None
    )
    data = {
        "ok": (not dirty and not outside_blocks) or unresolved,
        "command": "check-clean",
        "layout": layout_kind,
        "root": str(repo),
        "store_root": str(ctx.root),
        "excluded_dirs": list(GUARDED_BRANCH_DIRS),
        # KYO-206: the report path this command does not count, as data. A consumer
        # that cannot see the exclusion cannot tell it from a filter hiding something.
        "excluded_report_path": WORKER_REPORT_PATH,
        "dirty_entries": dirty,
        # Everything `git status` flagged, and the subset ruled out by the content
        # re-test, kept separately: a consumer that can only see the filtered list
        # cannot tell a quiet worktree from a filter that is hiding something.
        "flagged_entries": [entry.line for entry in flagged],
        "stat_only_entries": stat_only,
        # KYO-128: which tree this verdict is about, and how that was decided.
        "subject": target.as_json() if target is not None else None,
        "checked_the_worker_tree": target is None or target.resolved,
        # KYO-134: whether this stop outlives the transcript, and as what. `None`
        # for both on a hand-run invocation, which records nothing and says so by
        # rendering no line at all. `stop_audit_event` is `None` while
        # `stop_audit` still holds a sentence exactly when nothing was written —
        # deliberately not recorded, or attempted and failed — and those two are
        # different facts a consumer must be able to tell apart.
        "stop_audit_event": audit_event_id,
        "stop_audit": audit_line,
        # KYO-169: WHY this stop's prose is one line rather than eight, made
        # inspectable rather than inferable from its length. `None` means the
        # question was not asked (a placeable stop, a hand run) or could not be
        # answered (the ledger did not read), and both of those print in full.
        # The number is a property of the POPULATION, never of this row: two stops
        # a second apart carry the same value whatever either one of them is.
        "population_looks_like_the_tick": (
            tick.looks_like_the_tick if tick is not None else None
        ),
        # KYO-535.c1: the comparison against the dispatch's baseline, or `None` where
        # no dispatch's stop was placed. `repeat_stop` is why a finding did not block.
        "outside_the_worktree": outside.as_json() if outside is not None else None,
        "repeat_stop": repeat_stop,
    }

    if unresolved:
        assert target is not None
        finding = (
            f"{len(dirty)} uncommitted or unstaged path(s)" if dirty else "nothing uncommitted"
        )
        return Output(
            _unresolved_lines(target, "check-clean", finding, audit_line, tick), data
        )

    if dirty:
        # KYO-187: see the sibling refusal in `cmd_check_branch` for why the payload
        # is a required keyword argument rather than an assignment above the raise.
        raise GuardRefusal(
            CONTRACT_WORKTREE_HAS_UNCOMMITTED_WORK,
            [
                *_subject_lines(target, "check-clean", audit_line),
                f"{len(dirty)} uncommitted or unstaged path(s) in {repo}, outside "
                f"{excluded_label}: commit them before finishing — an uncommitted worktree at "
                "stop time is treated as work that does not durably exist yet",
                *(f"  {line}" for line in dirty),
                *stat_only_note,
                *outside_lines,
            ],
            data=data,
        )
    if outside_blocks:
        # KYO-535.c1: the worktree is clean, and something outside it is not.
        raise GuardRefusal(
            CONTRACT_WORKER_WROTE_OUTSIDE_ITS_WORKTREE,
            [*_subject_lines(target, "check-clean", audit_line), *outside_lines],
            data=data,
        )
    if getattr(args, "quiet", False):
        # KYO-420.c6: the pass in one line naming the tree; see the same branch in
        # `cmd_check_branch`. The stop-audit sentence and any stat-only note are still
        # in `--json` (`stop_audit`, `stat_only_entries`).
        return Output(
            [
                f"check-clean: clean — nothing uncommitted in {repo} outside {excluded_label}",
                *outside_lines,
            ],
            data,
        )
    return Output(
        [
            *_subject_lines(target, "check-clean", audit_line),
            f"check-clean: nothing uncommitted in {repo} outside {guarded_label} "
            f"(those are the orchestrator's to commit, not a worker's) or "
            f"{WORKER_REPORT_PATH} (KYO-206: your report is output, not work — write it "
            "there, leave it uncommitted, and your orchestrator ingests it with `kyo "
            "report <dispatch-id> --file <report-path>`)",
            *stat_only_note,
            *outside_lines,
            *_guarantee_lines(ctx, guarded_label, guards_store=False),
        ],
        data,
    )
