"""``kyo check-build`` and the store-unreadable line every hook-shaped command prints.

Which invocations are hook-shaped, how a failed store open is identified, and the
per-prompt comparison of the build a session opened on with the one its hooks run now.
"""


from __future__ import annotations

import sys
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

from . import stop_target
from .commands import (
    Context,
    Output,
)
from .config import (
    DB_NAME,
    KYOSEI_DIR,
)

# ---------------------------------------------------------------------- KYO-335
#
# EVERY HOOK, NOT ONLY `SubagentStop`. Every hook-shaped invocation whose store cannot be
# read says NOTHING WAS CHECKED, not only the stop guard: a refusal that is accurate about
# its cause says nothing about the whole guard layer having stopped
# (docs/design/guards.md#kyo-335).
#
# WHERE THIS IS DECIDED, and why it is not in seven command bodies. Every one of
# those commands fails the same way at the same instant: the first `ctx.store()`
# raises, `kyo.cli.main` catches a `KyoseiError`, prints it and exits. So the
# sentence is added once, at that catch, to the invocations that are hook-shaped —
# and a command that grows a store read later cannot forget to have it.
#
# HOW THE FAILURE IS IDENTIFIED, without a marker on the exception. `main` cannot
# tell a store-open refusal from any other `KyoseiError` by looking at it, and a
# marker would have to be threaded through `open_store`, `load_config` and every
# raise between them. It does not have to look: it can ASK, by trying to open the
# store itself (:func:`store_open_problem`). If the store opens, the failure was
# about something else and nothing here applies; if it does not, the store really is
# unreadable, whatever the original exception was about. The question is answered by
# the same code path the hook just failed on, so the two can never disagree.
#
# THE EXIT CODE IS FORCED NON-BLOCKING, and that is not a detail. KYO-316 measured
# every event this project installs into, against Claude Code 2.1.278: exit 2 on
# `PreToolUse` blocks the tool call, and a store-unreadable guard exiting 2 would
# refuse EVERY Bash call in the project — the KYO-33 failure, at the worst possible
# moment, since by construction nobody can then run the command that fixes it. The
# same measurement is why the line goes to stderr and is exactly one line: a
# non-zero, non-2 exit renders as a `<hook name> hook error` notice carrying the
# FIRST LINE OF STDERR, and a hook that exits 0 has its stderr sent to the debug log
# where nobody sees it.


@dataclass(frozen=True)
class HookInvocation:
    """One way this project's hooks invoke `kyo`, and what it checks when it runs."""

    #: The subcommand, as ``.claude/settings.json`` spells it.
    command: str
    #: The argparse ``dest`` whose truth makes this the *hook's* invocation rather
    #: than a hand-run one, or ``None`` where the hook runs the bare command and
    #: there is nothing to tell apart — `kyo sweep` and `kyo doctor` are run by the
    #: `Stop` hook exactly as a person runs them, and a person who runs them against
    #: an unreadable store wants this sentence too.
    flag: str | None
    #: What did not happen, as the line says it.
    does: str


#: Every invocation of `kyo` this project's packaged hooks make (KYO-335.c3).
#:
#: Hand-declared here and held to the packaged fragments by
#: ``tests/test_kyo335_path_build_lag.py``, which parses ``defaults/hooks/*.json``
#: and fails if a hook invokes a subcommand this table does not carry. Declared
#: rather than derived at runtime for the reason the whole section exists: this is
#: read on a path where something has already gone wrong with reading files, and a
#: table that had to load and parse packaged JSON to answer could fail there too.
HOOK_INVOCATIONS: tuple[HookInvocation, ...] = (
    HookInvocation("brief", "session_start", "the session-start brief"),
    HookInvocation("sessions", "hook", "this session's row in the session ledger"),
    HookInvocation("sweep", None, "the store sweep"),
    HookInvocation("doctor", None, "the store's invariant checks"),
    HookInvocation("check-push", "pre_tool_use", "the publication guard"),
    HookInvocation("check-command", "pre_tool_use", "the orchestrator-only command guard"),
    # KYO-422: opt-in, and no packaged fragment names it yet (see `cmd_pre_tool_use`).
    # Listed so a hand-wired one says NOTHING WAS CHECKED like the two it stands for.
    HookInvocation(
        "pre-tool-use",
        None,
        "the PreToolUse guards (publication and orchestrator-only command)",
    ),
    HookInvocation("check-clean", "subagent_stop", "the worker-worktree cleanliness guard"),
    HookInvocation("check-branch", "subagent_stop", "the worker-branch store guard"),
    HookInvocation("check-build", "hook", "the per-prompt build-change check"),
)


def hook_invocation(args: Namespace) -> HookInvocation | None:
    """Which hook-shaped invocation ``args`` is, or ``None`` for anything else."""
    command = getattr(args, "command", None)
    for shape in HOOK_INVOCATIONS:
        if shape.command != command:
            continue
        if shape.flag is None or getattr(args, shape.flag, False):
            return shape
    return None


# ---------------------------------------------------------------------- KYO-591
#
# SILENT OUTSIDE A KYOSEI PROJECT. A plugin's hooks fire in every session where it is
# enabled, so each packaged hook command must exit 0 with nothing on stdout or stderr
# where no Kyosei project resolves.
# "No project" is decided by `kyo`'s own resolution, `resolve_layout`, which includes
# KYO-580's read-through from a linked worktree to the main checkout's store link; a
# shell walk alone would miss that case, which is why the PATH guard's walk is only the
# fallback for `kyo` being absent (`install_hooks.PATH_GUARD_PREFIX`).
#
# Decided once, in `cli.run`, before the context is built: `brief`, `sweep` and
# `doctor` require a project and would refuse in `build_context`, before any handler
# could say anything. `check-push` is not in the table, and neither is `pre-tool-use`
# (which runs it): a push from a session outside any project may still publish a
# Kyosei project named by `git -C` (KYO-409.c3), so that guard keeps judging pushes
# and is silent only about what does not block (`cmd_publish.cmd_check_push`).

#: KYO-591: per command, the argparse ``dest`` that marks the hook's own invocation —
#: the one that is silent, exit 0, where no project resolves. A hand-run invocation
#: (the flag absent) keeps its refusal: a person who runs `kyo sweep` in the wrong
#: directory is told so.
QUIET_OUTSIDE_PROJECT: dict[str, str] = {
    "brief": "session_start",
    "sessions": "hook",
    "sweep": "hook",
    "doctor": "hook",
    "check-command": "pre_tool_use",
    "check-clean": "subagent_stop",
    "check-branch": "subagent_stop",
    "check-build": "hook",
}


def hook_outside_any_project(args: Namespace) -> bool:
    """True when ``args`` is a hook-mode invocation (:data:`QUIET_OUTSIDE_PROJECT`) and
    `kyo`'s own resolution finds no Kyosei project for it (KYO-591).

    The resolution is :func:`kyosei.config.resolve_layout` with the invocation's own
    ``--root``, the same call ``build_context`` makes. A resolution that itself refuses
    (a store link that names nothing readable) is NOT "no project": something there
    says it is one, so the command runs and says what is wrong, as it always has.
    """
    from .config import LAYOUT_UNCONFIGURED, resolve_layout
    from .errors import KyoseiError

    flag = QUIET_OUTSIDE_PROJECT.get(getattr(args, "command", None) or "")
    if flag is None or not getattr(args, flag, False):
        return False
    try:
        layout = resolve_layout(explicit_root=getattr(args, "root", None), required=False)
    except KyoseiError:
        return False
    return layout.kind == LAYOUT_UNCONFIGURED


def store_open_problem(ctx: Context) -> str | None:
    """This build's own words for why it cannot open ``ctx``'s store, or ``None``.

    Asks the question rather than inferring it — see this section's header. Never
    raises: it is called from an error handler, and an exception thrown there would
    replace a bad message with no message at all.
    """
    try:
        ctx.store()
    except Exception as exc:  # noqa: BLE001 - the answer to "can it be opened" is this
        return str(exc)
    return None


def hook_store_unreadable_line(ctx: Context | None, args: Namespace) -> str | None:
    """The one line a hook prints when its `kyo` cannot read the store (KYO-335.c3).

    ``None`` whenever this does not apply — not a hook-shaped invocation, no context
    to ask with, or a store that opens perfectly well and a failure that was about
    something else entirely.
    """
    from .buildinfo import upgrade_route

    shape = hook_invocation(args)
    if shape is None or ctx is None:
        return None
    problem = store_open_problem(ctx)
    if problem is None:
        return None
    # ONE line, whatever shape the refusal itself has: a hook error notice renders the
    # FIRST line of stderr and nothing else (KYO-316's measurement), so a refusal that
    # wrapped — `errors.SchemaMismatch` is free to grow, and `open_store` raises it —
    # would lose everything after its first newline, remedy included.
    problem = " ".join(problem.split())
    running = sys.argv[0] or "the `kyo` this hook ran"
    return (
        f"kyo {shape.command}: NOTHING WAS CHECKED — {shape.does} did not run, because "
        f"this `kyo` ({running}) cannot read this project's store: {problem}. Every "
        "hook this project installs runs the `kyo` on PATH, so while this lasts each "
        "one exits having checked nothing and the exit says only that it failed "
        f"(KYO-325 measured 120.5 minutes of it). Remedy: undo the store-side change "
        f"this build refuses, or {upgrade_route()}"
    )


# ---------------------------------------------------------------------- KYO-398.c6
#
# A REINSTALL MID-SESSION. The session-start brief compares its build with the previous
# session's (``cmd_brief._build_change``); nothing compared the build a session OPENED
# on with the one its hooks run now, and one machine-wide `kyo` serves every project:
# reinstalling it (``uv tool upgrade`` or ``make install-tool``) swaps the `kyo` under
# all of them while their sessions keep going. KYO-398 was written when a re-pack of
# the desktop app did that swap (KYO-335); since KYO-450 the app bundles no `kyo` and a
# re-pack moves none. `UserPromptSubmit`
# fires once per prompt the user sends, which is the cheapest moment that still lands
# before the session acts on a stale belief about its tool.
#
# COST IS THE DESIGN CONSTRAINT. This runs on every prompt, so it opens no `Store`
# (no version checks, no currency seam, no transaction, no export), resolves no config
# beyond the layout `build_context` already found, and asks git nothing on a stamped
# build: one read-only SQLite query for the opening brief's row, and the build stamp.
# Silence is its success — `UserPromptSubmit`'s stdout is injected into the session's
# context, so anything it prints on an unchanged build is noise on every prompt.


def opening_brief_build(db_path: Path, session_id: str | None = None) -> dict | None:
    """The build this session's opening brief recorded, read without opening a `Store`.

    With ``session_id`` (the hook's payload names one), "this session's opening brief"
    is the most recent brief run with ``session_start = 1`` recorded FOR that session,
    never another concurrent session's (docs/design/guards.md#kyo-418). Without one (a
    hand run) it is the most recent session-start brief of any session — the same anchor
    ``Store.latest_session_brief_run`` names (KYO-94). ``None`` when there is no
    database, no such row, or a database this query cannot read (one from before
    schema_version 25 has no session column): every one of those is "nothing to
    compare", and a per-prompt hook must never turn one into an error.

    ``mode=ro`` is the guarantee c6 asks for, not a nicety: SQLite refuses every write
    on a read-only connection, so nothing on this path can write the store however it
    is changed later.
    """
    import sqlite3

    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        if session_id is None:
            row = conn.execute(
                "SELECT id, at, build_commit, build_commit_time, build_source FROM brief_runs"
                " WHERE session_start = 1 ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, at, build_commit, build_commit_time, build_source FROM brief_runs"
                " WHERE session_start = 1 AND session_id = ? ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return None if row is None else dict(row)


def build_check_line(opening: dict, identity) -> str:
    """The one line a prompt gets when the build under the session changed."""
    from .buildinfo import SHORT_SHA

    def label(commit: str, when: str | None) -> str:
        return f"build {commit[:SHORT_SHA]}" + (f" (committed {when})" if when else "")

    since = (
        f"`kyo changelog --since {opening['build_commit_time']}`"
        if opening.get("build_commit_time")
        else "`kyo changelog --all`"
    )
    return (
        f"kyo changed under this session: its opening brief ({opening['id']}) ran "
        f"{label(opening['build_commit'], opening.get('build_commit_time'))}, and the "
        f"`kyo` on PATH is now {label(identity.commit, identity.commit_time)} — "
        "reinstalling it (uv tool upgrade, or make install-tool) moves every project on "
        f"this machine. What changed: {since}"
    )


def cmd_check_build(ctx: Context, args: Namespace) -> Output:
    """``kyo check-build [--hook]`` — has the `kyo` under this session changed since its
    opening brief (KYO-398.c6)?

    With ``--hook`` (the packaged ``UserPromptSubmit`` hook) it prints ONE line when
    the two builds differ and nothing at all otherwise — including when there is no
    project, no opening brief, a brief from before builds were recorded, or a build
    that cannot name itself — and always exits 0. Run by hand it says which of those it
    found. It never opens the store for writing (:func:`opening_brief_build`).
    """
    from .buildinfo import build_identity

    hook = bool(getattr(args, "hook", False))
    data: dict = {"ok": True, "command": "check-build", "changed": False, "state": None}

    def answer(state: str, line: str | None) -> Output:
        data["state"] = state
        data["line"] = line
        if hook and state != "changed":
            return Output([], data)
        return Output([line] if line else [], data)

    if ctx.layout is None or not ctx.layout.configured:
        return answer("no-project", "check-build: no Kyosei project here — nothing to compare")
    # KYO-418: the hook compares against ITS OWN session's opening
    # brief, named by the payload's `session_id`. A payload that names no session has
    # no opening brief to compare with, and says nothing -- never another session's.
    session_id: str | None = None
    if hook:
        raw = ""
        try:
            if sys.stdin is not None and not sys.stdin.closed and not sys.stdin.isatty():
                raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
        payload = stop_target._payload_of(raw)  # noqa: SLF001 - the one hook-payload reader
        candidate = payload.get("session_id")
        session_id = candidate if isinstance(candidate, str) and candidate else None
        data["session_id"] = session_id
        if session_id is None:
            return answer(
                "no-session",
                "check-build: the hook payload names no session, so there is no opening "
                "brief of its own to compare with",
            )
    opening = opening_brief_build(ctx.root / KYOSEI_DIR / DB_NAME, session_id)
    data["opening_brief"] = opening
    if opening is None:
        return answer(
            "no-opening-brief",
            "check-build: no session-start brief is recorded in this project's database "
            "(or it predates recorded builds), so there is nothing to compare with",
        )
    if not opening.get("build_commit"):
        return answer(
            "cannot-tell",
            f"check-build: the opening brief {opening['id']} recorded no build, so whether "
            "the tool changed since cannot be told",
        )
    identity = build_identity(with_dirty=False)
    data["running"] = {
        "commit": identity.commit,
        "commit_time": identity.commit_time,
        "source": identity.source,
    }
    if not identity.known:
        return answer(
            "cannot-tell",
            "check-build: the `kyo` running now cannot say which build it is "
            "(`kyo version` says why)",
        )
    if identity.commit == opening["build_commit"]:
        return answer(
            "unchanged",
            f"check-build: build {identity.short_commit} — unchanged since the opening "
            f"brief {opening['id']}",
        )
    data["changed"] = True
    return answer("changed", build_check_line(opening, identity))
