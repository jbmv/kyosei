"""Project discovery and ``kyosei/config.yml``.

A *store root* is any directory containing ``kyosei/config.yml``. Discovery walks
up from the working directory, so the CLI behaves like git from anywhere in the
tree.

KYO-54/KYO-56: the store root is no longer necessarily the code repository. Two
layouts are supported, and every command resolves which one it is looking at
before it does anything else:

``in-repo``
    ``kyosei/`` and ``ideas/`` live inside the code repository, which is what
    every release before this one did. Still first-class: it is the right answer
    for a project whose records are not sensitive and whose team wants them
    reviewed in the same pull request as the code.

``split`` (the default ``kyo init`` now chooses)
    The store lives at ``~/kyosei/<project>/`` — its own git repository, whose
    audience the consumer controls — and the code repository carries exactly one
    file about it: :data:`STORE_LINK_NAME`, a pointer naming where the store is.
    Nothing else about the store is committed to the code repository, which is
    the whole point: ``ideas/``, record descriptions, shift notes, evidence
    stored verbatim and frozen briefs quoting all of them stop being committed
    beside the code by default.

Both layouts read and write the *same* store: a split store root is an ordinary
store root, with the same ``kyosei/config.yml``, the same ``kyosei/export/`` and
the same rebuild path. That is deliberate — the split changes *discovery*, not
the store format, so nothing in the schema, the export or ``kyo rebuild`` had to
learn about it (and a store repository stays recoverable from that repository
alone).

The one thing a command must not confuse is *which root it means*. A store root
answers "where do records live"; a code root answers "which repository is the
work happening in" — the git repo ``kyo dispatch`` stamps a base sha from, the
one ``kyo check-branch`` diffs, the one a record's ``touches`` resolve against,
the one holding ``.claude/settings.json``. :class:`Layout` carries both, and
under ``in-repo`` they are the same directory (which is why the distinction
could go unnoticed until now).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from .contracts import STORE_LINK_NAME as _contracts_store_link_name
from .errors import KyoseiError
from .registry import load_yaml_refusing_duplicates
from .schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS, SCHEMA_VERSION

KYOSEI_DIR = "kyosei"
CONFIG_NAME = "config.yml"
DB_NAME = "kyosei.db"

#: KYO-186: the session hooks' local record of which Claude Code sessions this
#: project has and where each one is on claude.ai. Beside :data:`DB_NAME` and on
#: the same side of the line it draws: local state, gitignored, rebuilt by the next
#: session that runs — never ``kyosei/export/``, which is git-canonical.
#:
#: The name lives here rather than in :mod:`kyosei.cmd_sessions` because
#: :mod:`kyosei.scaffold` writes the ignore for it, and importing the command module
#: from there would close a cycle (``scaffold`` ← ``commands`` ← ``cmd_sessions``).
#: One constant with two consumers, so there is no second spelling of the filename
#: for them to drift apart on (law 3).
SESSIONS_NAME = "sessions.json"

#: KYO-362: where `kyo doctor --hook` leaves the report its one stderr line points at.
#: Beside :data:`DB_NAME` and :data:`SESSIONS_NAME`, and on the same side of the line
#: they draw: local state, gitignored, rewritten by the next failing run and deleted by
#: the next clean one — never ``kyosei/export/``, which is git-canonical, and never
#: committed, since it is a rendering of checks anyone re-runs in one command.
#:
#: Here rather than in :mod:`kyosei.cmd_store`, for exactly :data:`SESSIONS_NAME`'s
#: reason: :mod:`kyosei.scaffold` writes the ignore line for it, and importing a
#: command module from there would close a cycle. One constant with two consumers, so
#: there is no second spelling of the filename for them to drift apart on (law 3).
DOCTOR_REPORT_NAME = "doctor-report.txt"

#: The pointer file a split-layout code repository carries, and the *only* thing
#: that repository holds about the store. Committed on purpose: it names a
#: location, never any of the prose the store keeps. Declared in
#: :mod:`kyosei.contracts` beside the paths `kyo check-branch` guards, since KYO-403
#: made it one of them; this is the same object, not a second spelling.
STORE_LINK_NAME = _contracts_store_link_name

#: Overrides the parent directory new stores are created under. Read at
#: ``kyo init`` time only — once a project exists, its store link is what says
#: where its store is, so moving this variable never silently re-points an
#: existing project at a different store.
STORE_HOME_ENV = "KYOSEI_HOME"

#: ``~/kyosei``, not ``~/.kyosei`` (KYO-56). ``ideas/`` is a folder of documents a
#: human opens and edits by hand, in Obsidian or any editor; a dotfolder is
#: conventionally application config, machine-managed state a user is not
#: expected to browse. Hiding the ideas layer would fight the one workflow this
#: design has protected throughout. Enumerating ``~/kyosei/*`` is also a project
#: picker for free.
STORE_HOME_DIRNAME = "kyosei"

LAYOUT_IN_REPO = "in-repo"
LAYOUT_SPLIT = "split"
#: Neither layout is configured here: no ``kyosei/config.yml`` at or above this
#: directory and no store link either. A real, nameable state rather than an
#: exception in every caller — the worktree guards in particular must be able to
#: say "I am guarding nothing" out loud instead of failing obscurely or passing
#: as though they had checked something (KYO-54.c2).
LAYOUT_UNCONFIGURED = "unconfigured"

CONFIG_HEADER = (
    "# Kyosei project configuration, written by `kyo init`.\n"
    "#\n"
    "# The comment above each key below names the code path(s) that actually read it,\n"
    "# generated straight from kyosei.schema.CONFIG_CONSUMERS -- the same registry\n"
    "# `kyo doctor`'s config_consumers check reads (law 2: no stored field without a\n"
    "# consumer; law 3: derive the documentation, never hand-maintain a duplicate of\n"
    "# it that can drift — KYO-2, replacing the defaults/config.yml example that\n"
    "# documented fields no code consumed).\n"
)


class SetupCommand(str):
    """One ``worktree_setup`` entry: the command, and the note the project attached.

    KYO-164. ``worktree_setup`` was a list of bare commands, and this repository's own
    entries carried trailing ``#`` comments explaining the half of each command that is
    not inferable from it — that ``npm ci`` exits 0 in about two seconds while SKIPPING
    install scripts under npm's allow-scripts policy, so a desktop worker who runs it
    and believes it has a working tree does not. YAML comments are not values
    (``yaml.safe_load`` drops them), so the brief rendered the commands and none of the
    caveats. KYO-158 solved that for the config FILE by editing in place; the brief
    cannot, because it renders into a different document. So the note becomes data: an
    entry may be a mapping ``{run: ..., note: ...}`` beside the bare-string form.

    WHY A ``str`` SUBCLASS rather than a dataclass, which is the obvious shape. c2 says
    a project that declares bare commands must keep getting exactly the brief rendering
    it gets today, and every existing consumer of this key — the brief's code block, the
    `kyo dispatch` echo, this repo's own tests — reads the entries *as strings*. A
    dataclass makes c2 a promise each of those consumers has to be edited to keep, and
    the next consumer has to remember. A ``str`` that carries a note makes it true by
    construction: an entry with no note is indistinguishable from the string it used to
    be, including under ``==``, formatting and sorting, so the bare case cannot be
    changed by a renderer that has not heard of notes.
    """

    #: The project's explanation of this command, or ``""`` when it declared none.
    note: str

    def __new__(cls, run: str, note: str = "") -> SetupCommand:
        self = super().__new__(cls, run)
        self.note = note
        return self

    @property
    def run(self) -> str:
        """The command itself — the same value as the string, named for a reader."""
        return str(self)


@dataclass(frozen=True)
class Config:
    root: Path
    schema_version: int
    project_prefix: str
    export_dir: str
    #: The code repository this store serves — the same directory as ``root``
    #: under the in-repo layout, the repository holding the store link under the
    #: split one. Not stored in ``kyosei/config.yml`` and deliberately not
    #: stored anywhere else in the store either: it is a machine-specific
    #: absolute path (KYO-52), and it is already known by the discovery that
    #: found this store in the first place. Defaulted to ``root`` so a
    #: ``load_config(root)`` that has no opinion keeps the pre-KYO-54 behaviour.
    code_root: Path | None = None
    #: KYO-60. The layout named at `kyo init`, or ``None`` in a project that
    #: predates the key — which is every project initialised before KYO-54, this
    #: repository included. A *historical* fact, never the live answer: use
    #: :func:`resolve_layout` for where the store is now.
    layout_at_init: str | None = None
    #: KYO-60. ``{"actor": ..., "at": ...}`` once somebody has accepted that
    #: pushing the code repository publishes this store, ``None`` until then.
    publication_acknowledged: dict | None = None
    #: KYO-61. This project's default worker model, or ``None`` in a project that
    #: has never named one — which is every project written before the key, and
    #: which means *nothing declared*, never "the controller's own model".
    #: Overridable per kind (``kinds.yml`` ``policy.worker_model``) and per
    #: dispatch (``kyo dispatch --model``); resolved by
    #: :func:`kyosei.cmd_dispatch.resolve_worker_model`.
    worker_model: str | None = None
    #: KYO-143. The commands that turn a freshly created dispatch worktree into one
    #: the project's own tests can run in — empty in a project that has declared
    #: none, which is every project written before this key.
    #:
    #: ``git worktree add`` checks out tracked files and nothing else, so everything
    #: gitignored (``.venv/``, ``node_modules/``, a built binary, a seeded database)
    #: is absent from a worktree the moment a worker is sent to it. What is needed to
    #: fix that is a fact only the consuming project knows, so it is *declared* here
    #: and never guessed: Kyosei renders these commands into the frozen brief and
    #: prints them beside the ``git worktree add`` line, and runs none of them. A
    #: store command that ran a project's package manager on its own initiative would
    #: be assuming exactly what this key exists to have the project state.
    #:
    #: The default is in code rather than in a packaged file, and it is the empty
    #: tuple rather than any command — KYO-129's rule for that choice: a default
    #: belongs in the shipped file when the file and the fallback must provably
    #: match, and in code when the fallback has to reach projects that will never
    #: receive the file. This is squarely the second case. ``config.yml`` is
    #: *generated* per project and never copied from a packaged template, so every
    #: project initialised before this key exists has a config with no line for it
    #: and no way to acquire one; the behaviour that must reach those projects is
    #: exactly the empty case. And a shipped command would be a guess about a
    #: project Kyosei has never seen — worse here than elsewhere, because a wrong
    #: provisioning command in a brief is indistinguishable to a worker from a
    #: right one until it fails. Absence is meaningful on its own: it makes the
    #: brief state what a fresh worktree is missing rather than imply it was
    #: provisioned (KYO-143.c4).
    #: KYO-164: each entry is a :class:`SetupCommand` — a ``str`` carrying the
    #: project's note about it, so a reader that wants only the command sees the
    #: string it always saw.
    worktree_setup: tuple[SetupCommand, ...] = ()
    #: KYO-298. The commands this project considers "the whole suite" — the ones a
    #: dispatched worker must not run inside its worktree while its dispatch is
    #: running, because the controller runs them once per wave instead.
    #:
    #: Declared rather than guessed, on ``worktree_setup``'s reasoning exactly: only
    #: the project knows whether its suite is `make test`, `npm run check`, `cargo
    #: test --all` or nothing at all. Empty in every project that has declared none —
    #: which is every project written before this key — and empty means **nothing is
    #: refused**, never "guess a suite command". `kyo check-command` is silent in
    #: that project and `kyo doctor` has nothing to notice.
    #:
    #: Why this is a mechanism rather than a sentence in a brief: during one wave the
    #: standing rule "run the tests that cover your change, not the whole suite" was
    #: in every brief, and two of five workers ran the suite anyway
    #: because another line of the same brief (``worktree_setup``) told them to. Two
    #: full-context re-writes followed, 172,733 and 325,752 tokens, 28% of all the
    #: cache that wave's workers wrote. A rule that loses to a contradiction inside
    #: its own brief is not enforced by restating it.
    controller_only_commands: tuple[str, ...] = ()
    #: KYO-519: which spelling of the key above this file declared --
    #: :data:`ORCHESTRATOR_ONLY_KEY`, :data:`CONTROLLER_ONLY_KEY` (the spelling before the
    #: rename, read permanently), or ``None`` when neither is present. Read by
    #: ``doctor_install.check_orchestrator_only_setup``, which notices the old spelling and
    #: names the hand rename and the precondition it waits on.
    orchestrator_only_key: str | None = None
    #: KYO-312. The longest timeout a dispatched worker's Bash call may DECLARE while it
    #: runs inside its own worktree, in whole seconds — or ``None``, which is every
    #: project that has not opted in, and means nothing is refused for being long.
    #:
    #: **No default, and that is the point of the key.** What makes a long foreground
    #: command expensive is the harness's prompt cache expiring inside it, and how long
    #: that cache lives is a property of the consumer's harness rather than of Kyosei: a
    #: project whose workers hold a one-hour cache pays nothing for a twenty-minute wait,
    #: and a ceiling guessed at them would refuse commands that cost them nothing. So the
    #: number is declared or the check does not exist. Its sibling
    #: ``controller_only_commands`` is declared for the neighbouring reason — only the
    #: project knows which command IS its suite — and the two are independent: a project
    #: may opt into either, both or neither, because "the suite is the controller's" and
    #: "a wait longer than this costs more than it buys" are different claims.
    #:
    #: Seconds, and the unit is in the name. The value it is compared against arrives
    #: from the `PreToolUse` payload in MILLIseconds (the Bash tool's own ``timeout``),
    #: and a key called ``..._timeout_ceiling`` would be a number whose unit a maintainer
    #: could only learn by being wrong about it by a factor of a thousand.
    worker_command_timeout_ceiling_seconds: int | None = None
    #: KYO-303. Whether `kyo doctor` may print its one NOTICE about `kyo docs suggest`
    #: having something to draft. ``None`` in every project that has not answered —
    #: which is every project written before this key — and ``None`` means YES, the
    #: notice prints. Only ``False`` turns it off.
    #:
    #: Three states rather than two, on ``worker_model``'s reasoning: "never considered"
    #: and "considered and declined" are different facts about a project, and the second
    #: is the one this key exists to record. Nothing reads the difference today (the
    #: notice treats absent and true alike) and the difference is kept anyway, because
    #: collapsing it at the parser is what makes it unrecoverable later.
    docs_suggest_notice: bool | None = None
    #: KYO-394. Whether `kyo doctor` may print its NOTICE about a kind whose
    #: ``accepts_from`` lacks an entry the packaged default grants. ``docs_suggest_notice``'s
    #: three states on its terms: ``None`` (every project written before this key) and
    #: ``True`` print it, only ``False`` — a project that narrowed a kind on purpose —
    #: stops it.
    kind_grants_notice: bool | None = None
    #: KYO-556.c5. Whether `kyo doctor` (and the BUILD CHANGED brief) may print the
    #: NOTICE about a knowledge kind whose ``policy`` lacks a key the packaged default
    #: sets. ``kind_grants_notice``'s three states: only ``False`` stops it.
    kind_policy_notice: bool | None = None
    #: KYO-410. The branch this repository's worker branches are cut from, when the
    #: project declares it. ``None`` in every project that has not — and ``None`` means
    #: ASK GIT: :func:`kyosei.cmd_check_branch.resolve_trunk` tries origin/HEAD,
    #: init.defaultBranch, ``main`` and ``master`` in that order. Declared only where
    #: git's own answers are wrong or absent; a declaration naming no ref is refused
    #: where it is read, never skipped.
    trunk: str | None = None
    #: KYO-416. The root-commit sha(s) of the code repository this store was
    #: scaffolded for, sorted and comma-joined, or ``None`` — every store written
    #: before the key, and one scaffolded beside a repository with no commits yet.
    #: ``None`` is "not established", never "matches": `kyo init` refuses to adopt a
    #: store whose recorded identity differs from the repository asking, and says so
    #: out loud when it cannot tell (:func:`kyosei.scaffold.init_layout`).
    code_repo_id: str | None = None
    #: KYO-342. The main checkout this store was reached through, when
    #: :attr:`code_root` is a linked git worktree whose own ``kyosei/`` has no
    #: database — ``None`` in every ordinary invocation. Carried on the config
    #: rather than looked up again because the only thing that reads it is
    #: :func:`refuse_store_write`, and a writer that had to re-ask git whether it
    #: was allowed could ask at a different moment and get a different answer.
    #:
    #: Not a ``kyosei/config.yml`` key and deliberately not stored anywhere: it is
    #: a fact about *this invocation's* working directory, like :attr:`code_root`
    #: beside it, and a store that recorded it would be recording one machine's
    #: checkout layout into a git-canonical file (KYO-52).
    store_via_main_checkout: Path | None = None

    @property
    def repo_root(self) -> Path:
        """Where *code* lives: git queries, ``touches``, ``.claude/`` settings."""
        return self.code_root or self.root

    @property
    def kyosei_dir(self) -> Path:
        return self.root / KYOSEI_DIR

    @property
    def db_path(self) -> Path:
        return self.kyosei_dir / DB_NAME

    @property
    def config_path(self) -> Path:
        return self.kyosei_dir / CONFIG_NAME

    @property
    def export_path(self) -> Path:
        return self.kyosei_dir / self.export_dir


def config_path_for(root: Path) -> Path:
    return root / KYOSEI_DIR / CONFIG_NAME


# ------------------------------------------------- KYO-54: where the store lives


@dataclass(frozen=True)
class Layout:
    """One invocation's answer to "where is the store, and where is the code?"."""

    kind: str  # LAYOUT_IN_REPO | LAYOUT_SPLIT | LAYOUT_UNCONFIGURED
    store_root: Path
    code_root: Path
    #: The store link that produced a split layout, for messages that must name
    #: the file a consumer would edit to re-point it.
    link_path: Path | None = None
    #: KYO-342. The main checkout :attr:`store_root` was reached through, set only
    #: where :func:`_store_through_main_checkout` actually redirected: this
    #: invocation is inside a **linked worktree** (what ``git worktree add``
    #: creates, and what every dispatch worktree is) whose own ``kyosei/`` has no
    #: database, and the store was found in that checkout instead.
    #:
    #: Not set merely because the invocation is in a worktree — a field carried for
    #: a case nothing reads is decoration (law 2). The one refusal that needs the
    #: path where there was nothing to redirect to asks git for it at the moment it
    #: writes the sentence (:func:`kyosei.currency.missing_database_remedy`), which
    #: is an error path and can afford to.
    #:
    #: Read through :attr:`store_via_main_checkout`, which adds the layout guard.
    #:
    #: KYO-580: also set under the split layout, where it means the **store link**
    #: was read through the main checkout — the linked worktree carries no
    #: ``.kyosei`` of its own (an untracked link is not checked out) and the main
    #: checkout's was used instead. Read through :attr:`link_via_main_checkout`.
    main_checkout: Path | None = None

    @property
    def configured(self) -> bool:
        return self.kind != LAYOUT_UNCONFIGURED

    @property
    def split(self) -> bool:
        return self.kind == LAYOUT_SPLIT

    @property
    def store_via_main_checkout(self) -> Path | None:
        """The main checkout this store was reached through, or ``None`` (KYO-342).

        Set exactly when :func:`resolve_layout` looked in a linked worktree, found
        an in-repo store there with no database (``kyosei.db`` is gitignored and
        ``git worktree add`` checks out tracked files only), and resolved through
        git's common dir to the same store in the main checkout instead.

        Never set under the split layout: that store is outside the repository
        already, so a worktree of the code repository reaches the one database
        there by the ordinary route and nothing needs redirecting.

        Everything this answers is about **writes**. A store reached this way is
        read-only from here — see :func:`refuse_store_write`.
        """
        if self.kind != LAYOUT_IN_REPO or self.main_checkout is None:
            return None
        return self.main_checkout if self.store_root != self.code_root else None

    @property
    def link_via_main_checkout(self) -> Path | None:
        """The main checkout whose ``.kyosei`` named this split store, or ``None`` (KYO-580).

        Set exactly when :func:`resolve_layout` walked up a linked worktree to its
        git toplevel without finding a store link, and found one at the same
        relative position in the main checkout. Unlike
        :attr:`store_via_main_checkout` this makes nothing read-only: a split store
        is outside both checkouts already, so the worktree reaches the same
        database by either route, and only the pointer was borrowed.
        """
        if self.kind != LAYOUT_SPLIT:
            return None
        return self.main_checkout

    @property
    def store_root_in_this_checkout(self) -> Path:
        """Where *this* checkout's own copy of the store's tracked files is (KYO-342).

        The same directory as :attr:`store_root` in every ordinary invocation, and
        the worktree's own ``kyosei/`` when the store was reached through the main
        checkout — derived from the two roots rather than carried beside them (law
        3), since the relative path inside the repository is the same on both sides
        by construction.

        The distinction is not academic, and one command makes it: `kyo
        install-procedures` compares the packaged rule ledger against
        ``kyosei/procedures.md``, which is a **tracked file**, present in the
        worktree at whatever this branch holds, and the copy this branch would be
        diffed on. A redirect meant for a gitignored database must not quietly move
        that comparison to another working tree's copy — the preview would describe
        a hand-off to a file the reader is not looking at, and the refusal that
        guards the write would ask git about the wrong tree and find nothing to
        refuse.
        """
        through = self.store_via_main_checkout
        if through is None:
            return self.store_root
        return self.code_root / self.store_root.relative_to(through)

    def describe(self) -> str:
        """One line naming the layout and what follows from it.

        Every command that says what it is doing about the store says it with
        this (law 3), so "the store is somewhere else" can never be reported by
        one command and silently assumed by another.
        """
        through = self.store_via_main_checkout
        if through is not None:
            return (
                f"store layout: in-repo — {self.code_root} is a linked git worktree, whose "
                f"{KYOSEI_DIR}/ carries no database of its own, so this store was reached "
                f"through the main checkout at {through} and is read-only from here"
            )
        if self.kind == LAYOUT_IN_REPO:
            return (
                f"store layout: in-repo — {KYOSEI_DIR}/ and ideas/ live inside "
                f"{self.code_root} and are committed to the same git repository as its code"
            )
        linked_through = self.link_via_main_checkout
        if linked_through is not None:
            return (
                f"store layout: split — the store lives at {self.store_root}, its own git "
                f"repository; {self.code_root} is a linked git worktree with no "
                f"{STORE_LINK_NAME} of its own, so the store link was read through the "
                f"main checkout at {linked_through} ({self.link_path})"
            )
        if self.kind == LAYOUT_SPLIT:
            return (
                f"store layout: split — the store lives at {self.store_root}, its own git "
                f"repository, and {self.code_root} carries only the {STORE_LINK_NAME} pointer "
                "to it"
            )
        return (
            f"store layout: unconfigured — no {KYOSEI_DIR}/{CONFIG_NAME} at or above "
            f"{self.code_root} (in-repo layout) and no {STORE_LINK_NAME} store link "
            "(split layout)"
        )


def store_home(environ: dict[str, str] | None = None) -> Path:
    """The directory new stores are created under: ``$KYOSEI_HOME`` or ``~/kyosei``."""
    env = (environ if environ is not None else os.environ).get(STORE_HOME_ENV)
    if env and env.strip():
        return Path(env.strip()).expanduser()
    return Path.home() / STORE_HOME_DIRNAME


def default_store_dir(project_name: str, environ: dict[str, str] | None = None) -> Path:
    """Where ``kyo init`` puts a new project's store by default (KYO-56)."""
    return store_home(environ) / project_name


# ------------------------------------------------- KYO-70: where worktrees live

#: KYO-70. Dispatch worktrees materialised as siblings of the code repository —
#: ``../kyosei-wt-60`` beside a maintainer's other projects — because
#: ``kyo dispatch --worktree DIR`` had no opinion about DIR and the controller
#: invented one. The parent of a project is not the tool's to use: in the case that
#: raised this it held unrelated projects, and a maintainer looking at it mid-session
#: saw directories they did not create, named after a tool they never asked to put
#: anything there.
#:
#: KYO-56 already settled the same question for the store (``~/kyosei/<project>/``,
#: chosen precisely so Kyosei stops putting things where they surprise people), and a
#: worktree is the same class of artifact. ``~/kyosei/<project>/dispatch worktrees/<branch>/``
#: inherits every property that argument established: one conventional home,
#: discoverable by the same rule as the store, outside whatever directory the user
#: keeps their projects in, and under a path they already know belongs to this tool.
#:
#: Nesting inside the code repository (``.kyosei-worktrees/``) is deliberately not an
#: option: a worktree may live anywhere, and inside the main worktree is the one
#: arrangement git itself warns about.
#:
#: **KYO-89: the space is deliberate, and it is the whole record.** A dispatched worker
#: verifies its work here; the repository the work is integrated into is wherever the
#: maintainer keeps it. Every environmental property on which those two differ, and on
#: which *this* one is the more permissive, is a class of bug a worker cannot observe:
#: its suite passes, `main`'s fails, and no amount of worker diligence closes the gap.
#: KYO-83.d1 shipped exactly that — a message quoting a path with ``shlex.quote`` and a
#: test parsing it back with ``\S+``, green in a spaceless worktree and red on a `main`
#: checked out under a directory whose name contains a space.
#:
#: Path spacing is one instance of the class, and the cheapest one to invert: put a
#: space in the home and a worker's suite becomes strictly *harder* to pass than the
#: integration environment's, so the failure mode becomes a worker reporting a problem
#: that does not exist on `main` — visible, cheap and self-announcing — rather than a
#: worker reporting success on one that does.
#:
#: The rejected alternative, recorded so it is not reinvented: a lint for
#: path-sensitive patterns. It only ever catches the patterns someone thought of.
#:
#: The name stays typeable, and says what the directory is, because KYO-70's actual
#: requirement is that a maintainer who finds this directory can tell what put it there
#: and can `cd` into it. A path nobody can type — a newline, a quote, a ``$`` — would
#: exercise more of the class and fail KYO-70 in a different way, so it is not used.
WORKTREES_DIRNAME = "dispatch worktrees"

#: KYO-89's one migration cost, made visible rather than left to accumulate. Worktrees
#: created before the rename sit under the old name, and a stored ``<worktrees>`` token
#: resolves through :func:`worktree_home` — so after the rename those directories
#: resolve nowhere, and `kyo worktrees` would call them ``already-gone`` while they sit
#: on disk. That is precisely the silence KYO-70 exists to end ("nothing removed them …
#: across sessions they accumulated silently in a directory the tool was never given"),
#: so `kyo worktrees` names any surviving legacy home instead of pretending it is not
#: there. Nothing is ever removed from one automatically: the tool did not create these
#: under the name it now uses, and a directory it cannot account for is not its to
#: delete.
LEGACY_WORKTREES_DIRNAMES: tuple[str, ...] = ("worktrees",)

#: KYO-70.c2, the interaction with KYO-52 that had to be **decided** rather than
#: discovered. KYO-52 stores path-typed columns relative to the code root so the
#: git-canonical export carries no username. A worktree home under ``~/kyosei/``
#: makes that relative form something like ``../../../kyosei/<project>/dispatch worktrees/
#: kyo-60`` — which does drop the username, but encodes how deep the project sits in
#: the user's directory tree and is meaningless on a checkout at a different depth.
#:
#: Of the three options on the record — store it relative to the store root, store a
#: resolved-at-read-time token, or accept the ``..`` chain — this is the second. The
#: token names the *rule* that produced the path rather than any machine's spelling
#: of it, so it carries no username, no depth, and no anchor that a different
#: checkout could invalidate: ``<worktrees>/kyo-61`` means "this project's
#: conventional worktree home, branch kyo-61" on every machine, including one whose
#: ``$KYOSEI_HOME`` points somewhere else entirely. Storing it relative to the store
#: root was the near miss: it is stable under the split layout and *wrong* under the
#: in-repo one, where the store root is the code repository and the relative form
#: goes straight back to a ``..`` chain.
#:
#: An explicitly-given ``--worktree`` outside the home is still stored KYO-52's way,
#: unchanged. This adds a spelling for the paths Kyosei itself chose; it does not
#: reinterpret anyone else's.
WORKTREE_HOME_TOKEN = "<worktrees>"


def store_link_path(code_root: Path) -> Path:
    return code_root / STORE_LINK_NAME


def render_store_link(store_root: Path) -> str:
    """The store link's text. Home-relative when it can be, so the file stays
    portable across machines rather than pinning one person's absolute path into
    a repository everyone clones (the same defect KYO-52 names inside the store).
    """
    # KYO-569: asked of both spellings, because `kyo init` hands this a resolved store
    # root and a home reached through a symlink is only its prefix once resolved too.
    home = Path.home()
    try:
        shown = "~/" + store_root.relative_to(home).as_posix()
    except ValueError:
        try:
            shown = "~/" + store_root.resolve().relative_to(home.resolve()).as_posix()
        except ValueError:
            shown = store_root.as_posix()
    return (
        "# Kyosei store link, written by `kyo init` (KYO-54/KYO-56).\n"
        "#\n"
        "# This project's store -- its records, its ideas/ intake layer, its shift notes,\n"
        "# its evidence artifacts and the frozen briefs quoting all of them -- lives\n"
        "# OUTSIDE this repository, at the path below, committed to its own git repository\n"
        "# there. This one-line pointer is the only thing about the store that is\n"
        "# committed here.\n"
        "#\n"
        "# store: read by kyosei.config.resolve_layout, which is how every `kyo` command\n"
        "# finds this project. A path starting with ~ resolves against the current user's\n"
        "# home directory (so this file survives being cloned onto another machine); a\n"
        "# relative path resolves against this file's own directory.\n"
        f"store: {shown}\n"
    )


def write_store_link(code_root: Path, store_root: Path) -> Path:
    path = store_link_path(code_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_store_link(store_root), encoding="utf-8")
    return path


def read_store_link(code_root: Path) -> Path | None:
    """The store this directory points at, or ``None`` if it carries no link.

    A link that exists but does not resolve is an error, never a silent fall
    through to "no project here": a consumer who moved or never cloned their
    store must be told that, not told they have no project (law 6).
    """
    path = store_link_path(code_root)
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise KyoseiError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not str(raw.get("store", "")).strip():
        raise KyoseiError(
            f"{path} does not name a store; it must contain a single `store:` key holding "
            "the path to this project's store directory (the one containing "
            f"{KYOSEI_DIR}/{CONFIG_NAME}) — re-run `kyo init --store DIR`, or write the "
            "path in by hand"
        )
    target = Path(str(raw["store"]).strip()).expanduser()
    if not target.is_absolute():
        target = code_root / target
    return Path(os.path.normpath(target))


def _layout_at(candidate: Path) -> Layout | None:
    """The layout rooted exactly at ``candidate``, or ``None`` if it is neither."""
    if config_path_for(candidate).is_file():
        return Layout(LAYOUT_IN_REPO, store_root=candidate, code_root=candidate)
    target = read_store_link(candidate)
    if target is None:
        return None
    if not config_path_for(target).is_file():
        raise KyoseiError(
            f"{store_link_path(candidate)} points at {target}, which is not a Kyosei store "
            f"(no {KYOSEI_DIR}/{CONFIG_NAME} there). This project's store is committed to "
            "its own git repository: clone it to that path, correct the `store:` line, or "
            "run `kyo init --store DIR` to make a new one"
        )
    return Layout(
        LAYOUT_SPLIT,
        store_root=target,
        code_root=candidate,
        link_path=store_link_path(candidate),
    )


# --------------------------------------- KYO-342: a store read from a worktree


@dataclass(frozen=True)
class GitWorktreeFacts:
    """What git says about the checkout a directory sits in. Read, never inferred.

    Every field comes from a ``git rev-parse`` run at the moment of the call (law
    16). A directory that is not a git checkout at all yields ``is_repo=False`` and
    nothing else: stores are scaffolded outside git and in throwaway trees, and a
    caller that refused to answer there would be refusing on the strength of a
    question it could not ask.

    "Is this a **linked** worktree" is git's own fact rather than an inference from
    the path: ``--git-common-dir`` is the *shared* administrative directory, and it
    differs from ``--git-dir`` in exactly the trees ``git worktree add`` creates —
    which is what a dispatch worktree is, whatever it or its branch is named.

    One implementation, two consumers, because they ask the identical question for
    opposite purposes: :func:`kyosei.install_procedures.install_procedures_refusal` refuses a
    write from a linked worktree, and :func:`_store_through_main_checkout` resolves
    a read out of one (law 3 — the second consumer is what would otherwise have
    become a second spelling of ``--git-common-dir``).
    """

    is_repo: bool
    branch: str = ""
    #: The main checkout's working tree, set only for a LINKED worktree.
    main_checkout: Path | None = None
    #: Whether the repository carries the branch the caller asked about, and
    #: ``False`` when it asked about none — the branch to name is the caller's
    #: choice, never this function's (:data:`kyosei.install_procedures.MAIN_BRANCH` is the
    #: one spelling of it in this package).
    has_named_branch: bool = False
    #: This checkout's own top-level directory — what a path inside it is made
    #: relative to before being looked for under :attr:`main_checkout`.
    toplevel: Path | None = None


def git_worktree_facts(root: Path, *, branch: str | None = None) -> GitWorktreeFacts:
    """:class:`GitWorktreeFacts` for the checkout ``root`` sits in.

    ``branch`` names the branch whose existence the caller wants answered, and
    leaving it out asks about none.
    """

    from .git_queries import run_git

    def _git(*args: str) -> str | None:
        try:
            result = run_git(root, *args)
        except OSError:  # pragma: no cover
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    git_dir = _git("rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return GitWorktreeFacts(is_repo=False)
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
    main_checkout: Path | None = None
    if common is not None and Path(common) != Path(git_dir):
        # The main checkout is the working tree the shared admin directory belongs to.
        main_checkout = Path(common).parent
    toplevel = _git("rev-parse", "--show-toplevel")
    has_named_branch = branch is not None and (
        _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is not None
    )
    return GitWorktreeFacts(
        is_repo=True,
        branch=_git("rev-parse", "--abbrev-ref", "HEAD") or "",
        main_checkout=main_checkout,
        has_named_branch=has_named_branch,
        toplevel=Path(toplevel).resolve() if toplevel else None,
    )


def _store_through_main_checkout(layout: Layout) -> Layout:
    """Resolve an in-repo store a linked worktree cannot see (KYO-342).

    ``kyosei.db`` is gitignored — the charter's Layer 2 says ``kyosei/export/`` is
    the git-canonical serialization and the database is rebuilt from it — and
    ``git worktree add`` checks out tracked files and nothing else. So every
    dispatch worktree has a complete ``kyosei/`` with a ``config.yml``, an
    ``export/`` and **no database**, and every `kyo` read run there refused, naming
    `kyo rebuild`: a write under ``kyosei/`` and the one act a dispatched worker is
    forbidden. Two consumer projects hit it with every worker of every wave.

    Both repairs were on the table and this is the first: the store is *resolved*
    through git's common dir to the same relative path in the main checkout, so a
    read from a worktree answers out of the database the controller is actually
    operating. The alternative — leave resolution alone and only correct the
    refusal's wording — leaves every worker needing its controller to paste the
    text of anything it wants to read, which is the state that filed the bug.

    Three properties hold it inside its case:

    * **It is reached only when there is nothing to read here.** The worktree's own
      ``kyosei.db`` is looked for first, so a tree that has one (a maintainer's
      second checkout, a worktree somebody rebuilt in deliberately) is untouched,
      and the common case pays no subprocess at all.
    * **Which store a read reports** is the main checkout's database, not this
      branch's ``kyosei/export/``. The two differ on purpose: the export here is a
      checkout of whatever this branch holds, and may be behind — or ahead of — the
      live store. Reporting the live one is the answer a worker asking about its own
      dispatch needs, and :meth:`Layout.describe` says which one it was out loud
      rather than leaving a consumer to assume the nearer one.
    * **Reads only.** The redirected store belongs to another working tree, so a
      write from here would land outside this branch entirely — see
      :func:`refuse_store_write`, which every writer goes through.

    Split layouts are returned untouched: that store is outside the repository
    already, and a worktree of the code repository reaches its one database by the
    ordinary route.
    """
    if layout.kind != LAYOUT_IN_REPO:
        return layout
    if (layout.store_root / KYOSEI_DIR / DB_NAME).is_file():
        return layout
    facts = git_worktree_facts(layout.store_root)
    if facts.main_checkout is None or facts.toplevel is None:
        return layout
    try:
        relative = layout.store_root.relative_to(facts.toplevel)
    except ValueError:  # pragma: no cover - a store root outside its own toplevel
        return layout
    candidate = (facts.main_checkout / relative).resolve()
    # Both conditions, and the second is not implied by the first: a main checkout
    # can carry the store and still have no database — a fresh clone, or a checkout
    # nobody has rebuilt in yet. That state is the one KYO-342.c3 covers, and the
    # layout it gets is the unredirected one, so the missing-database refusal is
    # raised about the worktree's own path and says where the database belongs.
    if not config_path_for(candidate).is_file():
        return layout
    if not (candidate / KYOSEI_DIR / DB_NAME).is_file():
        return layout
    return Layout(
        LAYOUT_IN_REPO,
        store_root=candidate,
        code_root=layout.code_root,
        main_checkout=facts.main_checkout,
    )


def refuse_store_write(main_checkout: Path | None, code_root: Path, what: str) -> None:
    """Refuse ``what`` when this store was reached through the main checkout (KYO-342).

    The counterpart of :func:`_store_through_main_checkout`, and the reason that
    resolution is safe to do at all. A redirected store is another working tree's:
    a write from here would land in the main checkout, under whatever branch it has
    checked out, outside this branch's diff — so the stop-time store guard that
    reads a worker's own commits would see nothing and report clean. Refusing at
    the moment of the write is what keeps "a dispatched worker never writes the
    store" true, rather than a check that fires somewhere the damage is not.

    ``main_checkout`` is :attr:`Layout.store_via_main_checkout` (or the
    ``Config`` field carrying it): ``None`` in every ordinary invocation, and the
    whole function is then a no-op.
    """
    if main_checkout is None:
        return
    raise KyoseiError(
        f"refusing to {what}: {code_root} is a linked git worktree, whose {KYOSEI_DIR}/ "
        f"carries no database, so this store was reached through the main checkout at "
        f"{main_checkout} — for reads. A write from here would land in that checkout's "
        f"store, on whatever branch it has out and outside this branch's diff, where the "
        f"stop-time store guard cannot see it. Run this in {main_checkout} instead"
    )


def _is_git_toplevel(directory: Path) -> bool:
    """Whether ``directory`` is the top of a git checkout (KYO-403).

    Git's own marker, read without a subprocess: a main checkout carries a ``.git``
    directory and a linked worktree or submodule a ``.git`` file, and either sits at
    exactly the directory ``git rev-parse --show-toplevel`` names. Asked once per
    level of an upward walk that every `kyo` invocation makes, so it stays a stat.
    """
    return (directory / ".git").exists()


def _split_link_through_main_checkout(
    here: Path, toplevel: Path, main_checkout: Path
) -> Layout | None:
    """A split layout whose link only the main checkout carries (KYO-580).

    The split layout's ``.kyosei`` names a local store path, so a public repository
    may reasonably keep it untracked — and ``git worktree add`` checks out tracked
    files only, so every linked worktree of such a repository has no link and its
    upward walk stops at its own toplevel (KYO-403) with nothing found. This is the
    split-layout counterpart of :func:`_store_through_main_checkout`: for each
    directory the walk visited, deepest first, look at the same position relative
    to the main checkout, and resolve the store its link names.

    The code root is the worktree's directory, never the main checkout's: the
    worker's branch, tree and guards are this checkout's, and only the pointer is
    borrowed. Reached only after the walk found nothing, so a worktree that carries
    its own link (committed or copied) is never redirected, and a checkout that is
    not a linked worktree never gets here.
    """
    for candidate in (here, *here.parents):
        try:
            relative = candidate.relative_to(toplevel)
        except ValueError:
            return None
        through = main_checkout / relative
        target = read_store_link(through)
        if target is not None:
            if not config_path_for(target).is_file():
                # The same refusal `_layout_at` makes about a link that names no store.
                _layout_at(through)
            return Layout(
                LAYOUT_SPLIT,
                store_root=target,
                code_root=candidate,
                link_path=store_link_path(through),
                main_checkout=main_checkout,
            )
        if candidate == toplevel:
            return None
    return None  # pragma: no cover - the loop always reaches the toplevel


def resolve_layout(
    start: Path | None = None,
    *,
    explicit_root: str | Path | None = None,
    required: bool = True,
) -> Layout:
    """Find this invocation's store and code roots.

    ``explicit_root`` (``kyo --root``) names a directory that is asked directly,
    without a walk — and it may be either a store root or a code repository that
    links to one, so ``--root`` keeps meaning "the project over there" under both
    layouts. Otherwise the search walks up from ``start`` like git's, and the
    *deepest* match wins.

    ``required=False`` returns a :data:`LAYOUT_UNCONFIGURED` layout instead of
    raising, for the two guards that have something honest to say when no
    project is configured at all.
    """
    if explicit_root is not None:
        candidate = Path(explicit_root).expanduser().resolve()
        found = _layout_at(candidate)
        if found is not None:
            return _store_through_main_checkout(found)
        # An explicit `--root` is taken at its word even when it holds no project:
        # it becomes the store root, and whatever the command then tries to read
        # fails with its own message about the missing file (`kyosei/config.yml
        # does not exist`), which is more specific than a discovery error. That is
        # also the pre-KYO-54 behaviour, and `kyo ideas review` depends on it —
        # the ideas layer is never enforced against, so an absent one degrades to
        # an empty listing rather than an error (law 12).
        return Layout(LAYOUT_UNCONFIGURED, store_root=candidate, code_root=candidate)

    here = (start or Path.cwd()).resolve()
    stopped_at: Path | None = None
    for candidate in (here, *here.parents):
        found = _layout_at(candidate)
        if found is not None:
            return _store_through_main_checkout(found)
        # KYO-403: the walk ends at the start's git toplevel. A store found above a
        # DIFFERENT repository's top is not this checkout's project — and the one
        # arrangement that produced it is exactly Kyosei's own: a split project's
        # dispatch worktrees live under ``~/kyosei/<store>/dispatch worktrees/``,
        # inside the store root, so a worktree cut before ``.kyosei`` was committed
        # climbed out of itself, found the store's own ``kyosei/config.yml``, and
        # answered "in-repo, code_root == store_root, branch main". Every guard then
        # inspected the store repository instead of the worker's, and every write
        # landed in the controller's live store. Outside any repository the walk is
        # unchanged; ``--root`` never walks at all.
        if _is_git_toplevel(candidate):
            stopped_at = candidate
            break
    in_linked_worktree = stopped_at is not None and (stopped_at / ".git").is_file()
    main_checkout: Path | None = None
    if in_linked_worktree:
        assert stopped_at is not None
        main_checkout = git_worktree_facts(stopped_at).main_checkout
        if main_checkout is not None:
            found = _split_link_through_main_checkout(here, stopped_at, main_checkout)
            if found is not None:
                return found
    if required:
        # The worktree sentence only where it can be true: a linked worktree's
        # ``.git`` is a file. A plain repository nobody has initialised gets the
        # message it always got.
        bound = (
            f". The search stopped at {stopped_at}, the top of the linked git worktree "
            "it started in, because a store above a different repository's top is not "
            "this checkout's project, and the main checkout"
            + (f" at {main_checkout}" if main_checkout is not None else "")
            + f" has no {STORE_LINK_NAME} at the same place either (KYO-580): if this "
            "is a dispatch worktree of a split-layout project, restore the store link "
            "in the main checkout — it may stay untracked, since a worktree reads it "
            "from there, and `kyo init --store DIR` run there writes one — or copy it "
            "into this worktree, or name the project with `kyo --root`"
            if in_linked_worktree
            else ""
        )
        raise KyoseiError(
            f"no Kyosei project found at or above {here} "
            f"(looked for {KYOSEI_DIR}/{CONFIG_NAME}, and for a {STORE_LINK_NAME} store "
            f"link naming a store elsewhere); run `kyo init` first{bound}"
        )
    return Layout(LAYOUT_UNCONFIGURED, store_root=here, code_root=here)


def project_home_name(layout: Layout) -> str:
    """The name this project occupies under ``$KYOSEI_HOME`` (KYO-70).

    Under the split layout that is the store directory's own name — the one
    `kyo init` created at ``~/kyosei/<project>/`` — so a project's store and its
    worktrees sit under one directory, discoverable by one rule.

    Under the in-repo layout there is no such directory, and KYO-70.c5 required a
    decision rather than a fallthrough. The decision is: an in-repo project gets a
    Kyosei home **solely for worktrees**, named after its code repository. The
    alternative on the record — keep siblings and merely warn — was refused because
    siblings are the entire defect: a warning would leave the directories exactly
    where they surprise people and only add a line nobody reads twice. Nothing about
    the store moves; the in-repo layout stays in-repo, and what appears under
    ``~/kyosei/<name>/`` is a ``dispatch worktrees/`` directory and nothing else.

    KYO-342: asked from *inside* one of those worktrees, the code root is the
    worktree's own directory, whose name is a branch — so the answer would be
    ``~/kyosei/kyo-342-worktree-store/``, a home this project has never used and
    where none of its worktrees are. The name has to come from the repository the
    worktree belongs to, which is the main checkout, so that a read run in a
    worktree resolves a stored ``<worktrees>`` token to the same directory the
    controller stored it from.
    """
    if layout.split:
        return layout.store_root.name
    through = layout.store_via_main_checkout
    return (through if through is not None else layout.code_root).name


def worktree_home(project_name: str, environ: dict[str, str] | None = None) -> Path:
    """``~/kyosei/<project>/dispatch worktrees`` — where this project's dispatch
    worktrees go. The space is KYO-89's; see :data:`WORKTREES_DIRNAME`."""
    return store_home(environ) / project_name / WORKTREES_DIRNAME


def legacy_worktree_homes(
    project_name: str, environ: dict[str, str] | None = None
) -> list[Path]:
    """Homes this project's worktrees used to live under and may still occupy
    (KYO-89). Existence is the caller's question; this only says where to look."""
    return [
        store_home(environ) / project_name / name
        for name in LEGACY_WORKTREES_DIRNAMES
        if name != WORKTREES_DIRNAME
    ]


def default_worktree_dir(
    project_name: str, branch: str, environ: dict[str, str] | None = None
) -> Path:
    """The worktree a dispatch on ``branch`` gets when nobody names one (KYO-70.c1).

    One directory per branch, because that is what git allows: two worktrees cannot
    share a branch, so branch-per-worktree is the only naming that cannot collide
    with itself — and two dispatches that *do* declare the same branch land on the
    same path and are refused by the existing worktree-collision check, which is the
    correct answer rather than a coincidence.
    """
    safe = branch.strip().replace("/", "-")
    # KYO-402: `..` here named the home's PARENT (the store root) as a worktree, and a
    # later prune deleted it. `kyo dispatch` refuses such names first; this is the
    # backstop for any other caller, so the derived path can never leave the home.
    if safe in ("", ".", ".."):
        raise ValueError(f"branch {branch!r} cannot name a worktree under the worktree home")
    return worktree_home(project_name, environ) / safe


def store_worktree_path(
    code_root: Path,
    project_name: str,
    path: str | Path,
    environ: dict[str, str] | None = None,
) -> str:
    """The value to store for a worktree path (KYO-70.c2).

    A path under this project's worktree home stores as
    ``<worktrees>/<name>`` — the rule that produced it, not one machine's spelling.
    Anything else falls through to :func:`kyosei.contracts.store_path`'s KYO-52
    behaviour unchanged, so an explicitly-given ``--worktree`` somewhere else is
    stored exactly as it was before this record.
    """
    from .contracts import store_path

    resolved = Path(path).expanduser().resolve()
    home = worktree_home(project_name, environ).expanduser()
    try:
        relative = resolved.relative_to(home.resolve(strict=False))
    except ValueError:
        return store_path(code_root, resolved)
    return f"{WORKTREE_HOME_TOKEN}/{relative.as_posix()}"


def resolve_worktree_path(
    code_root: Path,
    project_name: str,
    stored: str,
    environ: dict[str, str] | None = None,
) -> Path:
    """The directory a stored worktree value names, resolved on *this* machine.

    Reads all three forms a row can hold: the KYO-70 token, the KYO-52 code-root
    relative form, and the pre-schema-9 absolute form. The value says which it is, so
    no version flag is needed — the same property that let KYO-52 read legacy rows
    without one.
    """
    from .contracts import resolve_stored_path

    if stored == WORKTREE_HOME_TOKEN or stored.startswith(WORKTREE_HOME_TOKEN + "/"):
        tail = stored[len(WORKTREE_HOME_TOKEN) :].lstrip("/")
        home = worktree_home(project_name, environ)
        return (home / tail).expanduser().resolve() if tail else home.expanduser().resolve()
    return resolve_stored_path(code_root, stored)


# ------------------------- KYO-535: a running dispatch's worktree and the real home

#: KYO-535.c2: the one explicit override for :func:`refuse_real_home_from_a_dispatch_worktree`.
#: An environment variable rather than a flag, because the guard sits under two commands
#: (`kyo init` and `kyo projects add`) and the override has to mean the same on both;
#: and set to exactly ``1``, so an inherited empty or stray value overrides nothing.
REAL_HOME_FROM_DISPATCH_ENV = "KYOSEI_ALLOW_REAL_HOME"

#: The directory the running ``kyosei`` package was imported from. A module constant so
#: a test can stand a fixture worktree in for it: what it detects is a worker running
#: its worktree's own ``.venv/bin/python -m kyosei`` while its shell's cwd has been
#: reset to the main checkout -- the KYO-521 shape, where the cwd says nothing.
PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class DispatchWorktreeReach:
    """A home-writing command about to reach the real home from a dispatch's worktree."""

    worktree: Path
    #: How the worktree was seen: the cwd, the package's own location, or a path the
    #: command was given.
    seen_as: str
    #: The running dispatch whose worktree it is; ``None`` when the store could not be
    #: read to say, which is treated as one rather than as none (law 6).
    dispatch_id: str | None
    home: Path
    #: Why ``home`` counts as the real home rather than a scratch one.
    why_real: str
    note: str = ""


def _existing_ancestor(path: Path) -> Path | None:
    """``path`` or its nearest existing ancestor: `kyo init DIR` may name a directory it
    is about to create, and git can only be asked from one that exists."""
    candidate = path.expanduser()
    for step in (candidate, *candidate.parents):
        if step.is_dir():
            return step
    return None


def _why_home_is_real(
    home: Path, worktree: Path, main_checkout: Path, environ: dict[str, str]
) -> str | None:
    """Why ``home`` is the store home this dispatch's project really uses, or ``None``.

    "Real" is decided from facts about the home, never from whether a variable is set:
    a controller that exports ``$KYOSEI_HOME`` hands the same value to every worker it
    spawns, so "the variable is set" proves nothing about a scratch home. A home is
    real when any of these holds, and a temporary one a worker made holds none:

    * ``$KYOSEI_HOME`` is unset, or names the account's own ``~/kyosei`` anyway;
    * the dispatch's worktree lies inside it (a derived ``<worktrees>`` path, KYO-70);
    * its ``projects.json`` registers the main checkout this worktree belongs to --
      or cannot be read, which is not assumed to be a scratch registry.
    """
    from .cmd_projects import REGISTRY_FILE, read_entries

    if not environ.get(STORE_HOME_ENV, "").strip():
        return f"${STORE_HOME_ENV} is unset, so it is the account's own ~/{STORE_HOME_DIRNAME}"
    resolved = home.expanduser().resolve()
    if resolved == (Path.home() / STORE_HOME_DIRNAME).resolve():
        return f"it is the account's own ~/{STORE_HOME_DIRNAME}"
    if worktree.resolve().is_relative_to(resolved):
        return "the dispatch's own worktree lies inside it"
    try:
        entries = read_entries(resolved / REGISTRY_FILE)
    except KyoseiError:
        return f"its {REGISTRY_FILE} cannot be read, so it is not assumed to be a scratch one"
    main = main_checkout.resolve()
    if any(Path(entry["root"]).expanduser().resolve() == main for entry in entries):
        return f"its {REGISTRY_FILE} registers {main}, the repository this dispatch works on"
    return None


def _running_dispatch_in(
    worktree: Path, main_checkout: Path, environ: dict[str, str]
) -> tuple[bool, str | None, str]:
    """Is ``worktree`` a RUNNING dispatch's worktree? ``(is, dispatch id, note)``.

    Asked of the store the worktree resolves to (KYO-342 reaches the main checkout's
    from a linked worktree), comparing each running row's stored worktree resolved on
    this machine. A linked worktree of a repository with no Kyosei project is not a
    dispatch. A store that cannot be opened is reported as a dispatch with no id: an
    unknown is not read as "not one" (law 6), and the override is named either way.
    """
    try:
        layout = resolve_layout(explicit_root=worktree, required=False)
        if not layout.configured:
            return False, None, ""
        from .store import open_store

        store = open_store(
            layout.store_root,
            code_root=layout.code_root,
            store_via_main_checkout=layout.store_via_main_checkout,
            read_only=True,
        )
        try:
            name = project_home_name(layout)
            target = worktree.resolve()
            for row in store.running_dispatches():
                stored = row["worktree"]
                # The controller stored any KYO-52 relative form against the main
                # checkout, so it is resolved against that and not this worktree.
                if stored and resolve_worktree_path(
                    main_checkout, name, stored, environ
                ) == target:
                    return True, row["id"], ""
        finally:
            store.close()
        return False, None, ""
    except Exception as exc:  # noqa: BLE001 - an unreadable store is reported, not skipped
        return True, None, f"whether a dispatch is running there could not be read ({exc})"


def dispatch_worktree_reach(
    environ: dict[str, str], given: list[tuple[str, Path]] | None = None
) -> DispatchWorktreeReach | None:
    """KYO-535.c2: is this home-writing command running from inside a running
    dispatch's worktree, against the real home? ``None`` when it is not.

    Three places are asked, because a worker's shell does not stay where it was put:
    the cwd (a worker that ``cd``-ed into its worktree), the directory the ``kyosei``
    package itself was imported from (a worker running its worktree's own
    ``.venv/bin/python -m kyosei`` from a cwd the harness reset to the main checkout --
    as measured, a subagent's Bash cwd is the controller's), and any path
    the command was given. A worker using the INSTALLED `kyo` from the main checkout is
    indistinguishable from its controller here; that case is the stop check's
    (:func:`kyosei.cmd_check_clean.outside_the_worktree`, KYO-535.c1).

    The main checkout is never a linked worktree, so nothing the controller runs there
    reaches the store query at all (KYO-535.c3).
    """
    home = store_home(environ)
    seen: set[Path] = set()
    candidates = [("the current directory", Path.cwd()), (
        "the kyosei package this command runs from", PACKAGE_DIR
    ), *(given or [])]
    for seen_as, directory in candidates:
        existing = _existing_ancestor(directory)
        if existing is None:
            continue
        facts = git_worktree_facts(existing)
        if facts.main_checkout is None or facts.toplevel is None:
            continue
        worktree = facts.toplevel
        if worktree in seen:
            continue
        seen.add(worktree)
        why_real = _why_home_is_real(home, worktree, facts.main_checkout, environ)
        if why_real is None:
            continue
        running, dispatch_id, note = _running_dispatch_in(
            worktree, facts.main_checkout, environ
        )
        if running:
            return DispatchWorktreeReach(worktree, seen_as, dispatch_id, home, why_real, note)
    return None


def refuse_real_home_from_a_dispatch_worktree(
    environ: dict[str, str],
    *,
    command: str,
    what: str,
    given: list[tuple[str, Path]] | None = None,
) -> None:
    """KYO-535.c2: refuse a write to the real ``$KYOSEI_HOME`` from a dispatch's worktree.

    In one recorded case a worker's `kyo init` created ``~/kyosei/p1`` and registered it in
    the real ``projects.json``, and another's scratch copy was registered the same way;
    `kyo check-clean` and `kyo check-branch` read only the worker's own worktree, so
    neither noticed. Refused BEFORE anything is created, so a refusal leaves nothing to
    clean up. :data:`REAL_HOME_FROM_DISPATCH_ENV` set to ``1`` is the explicit override.
    What it can and cannot see: docs/design/guards.md#kyo-535.
    """
    if environ.get(REAL_HOME_FROM_DISPATCH_ENV, "").strip() == "1":
        return
    reach = dispatch_worktree_reach(environ, given)
    if reach is None:
        return
    whose = f"dispatch {reach.dispatch_id}'s" if reach.dispatch_id else "a dispatch's"
    unknown = f"; {reach.note}, so it is treated as one" if reach.note else ""
    raise KyoseiError(
        f"{command} would {what} in {reach.home}, the real Kyosei home ({reach.why_real}), "
        f"and it is running from inside {whose} worktree {reach.worktree} (seen as "
        f"{reach.seen_as}{unknown}). Nothing was created or registered. A dispatched "
        f"worker's scratch project belongs in a temporary home: run it again with "
        f"{STORE_HOME_ENV} set to a temporary directory (`{STORE_HOME_ENV}=\"$(mktemp -d)\" "
        f"{command} ...`). If the real home is really meant, say so explicitly with "
        f"{REAL_HOME_FROM_DISPATCH_ENV}=1 (KYO-535)"
    )


def find_root(start: Path | None = None) -> Path:
    """The store root at or above ``start`` — either layout.

    KYO-342: inside a linked git worktree whose own ``kyosei/`` has no database,
    that is the *main checkout's* store root, reached through git's common dir.
    A caller holding only this path has lost the fact that it was reached that
    way, and with it the fact that writing there is refused — which is why
    everything that decides anything takes the whole :class:`Layout` and this
    stays what it always was, the one-line answer to "which directory".
    """
    return resolve_layout(start).store_root


def load_config(
    root: Path,
    *,
    code_root: Path | None = None,
    store_via_main_checkout: Path | None = None,
) -> Config:
    path = config_path_for(root)
    if not path.is_file():
        raise KyoseiError(f"{path} does not exist; {root} is not a Kyosei project")
    try:
        # KYO-413.c6: a repeated key is refused by name rather than resolved to
        # whichever copy came last.
        raw = load_yaml_refusing_duplicates(path.read_text(encoding="utf-8"), path) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - depends on user damage
        raise KyoseiError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise KyoseiError(f"{path} must contain a YAML mapping, found {type(raw).__name__}")

    unknown = sorted(set(raw) - set(CONFIG_CONSUMERS))
    if unknown:
        raise KyoseiError(
            f"{path} carries keys no code path reads: {', '.join(unknown)} "
            "(law 2 — remove them or ship their consumer)"
        )
    # KYO-60: optional keys are absent in every project written before they
    # existed, which is what makes them optional. Unknown keys are still refused
    # above, so "optional" widens exactly two names and nothing else.
    missing = sorted(set(CONFIG_CONSUMERS) - OPTIONAL_CONFIG_KEYS - set(raw))
    if missing:
        raise KyoseiError(f"{path} is missing required keys: {', '.join(missing)}")

    acknowledged = raw.get("publication_acknowledged")
    if acknowledged is not None and not isinstance(acknowledged, dict):
        raise KyoseiError(
            f"{path} key 'publication_acknowledged' must be a mapping carrying who "
            f"acknowledged and when, or absent; found {type(acknowledged).__name__} "
            "— delete the line and re-run `kyo check-push --acknowledge`"
        )
    # KYO-61: a model name, or nothing. An empty/blank value is refused rather than
    # normalised to None — "worker_model:" with nothing after it reads to a human as
    # a declaration, and silently treating it as absent is exactly the kind of
    # unknown-borrowing-a-value this store keeps refusing to do (law 6).
    worker_model = raw.get("worker_model")
    if worker_model is not None and (
        not isinstance(worker_model, str) or not worker_model.strip()
    ):
        raise KyoseiError(
            f"{path} key 'worker_model' must be a non-empty model name (a string), or "
            f"absent to declare none; found {worker_model!r} — delete the line, or give "
            "it a value"
        )
    worktree_setup = _parse_worktree_setup(raw.get("worktree_setup"), path)
    controller_only_commands, orchestrator_only_key = _read_orchestrator_only_commands(
        raw, path
    )
    timeout_ceiling = _parse_timeout_ceiling_seconds(
        raw.get("worker_command_timeout_ceiling_seconds"), path
    )
    # KYO-303: a boolean or nothing. Refused rather than coerced — `docs_suggest_notice:
    # "no"` and `docs_suggest_notice: 0` each read to a person as switching the notice
    # off, and Python would agree with one of them and not the other. A key whose whole
    # job is to record a decision must not be able to record a different one silently.
    notice = raw.get("docs_suggest_notice")
    if notice is not None and not isinstance(notice, bool):
        # The remedy is a text editor and the message says only that, naming no command:
        # the two commands a reader might expect here (`kyo doctor`, which is what prints
        # the notice, and `kyo docs suggest`, which is what the notice is about) are both
        # things this refusal BLOCKS until the line is fixed, so naming either would be
        # the KYO-77 defect exactly — a remedy that does not apply where it was raised.
        # Its sibling `worker_model` above is refused the same way for the same reason.
        raise KyoseiError(
            f"{path} key 'docs_suggest_notice' must be true or false, or absent to leave "
            f"the notice on; found {notice!r} — write `docs_suggest_notice: false` to "
            "turn the notice off, or delete the line"
        )
    # KYO-394: `docs_suggest_notice`'s parse exactly, for its reason — a key whose
    # whole job is to record a decision must not record a different one silently.
    grants_notice = raw.get("kind_grants_notice")
    if grants_notice is not None and not isinstance(grants_notice, bool):
        raise KyoseiError(
            f"{path} key 'kind_grants_notice' must be true or false, or absent to leave "
            f"the notice on; found {grants_notice!r} — write `kind_grants_notice: false` "
            "to turn the notice off, or delete the line"
        )
    # KYO-413.c7: `int(...)` on a hand-edited `schema_version: abc` was a raw ValueError
    # traceback, and `str(...)` turned `project_prefix: null` into the prefix "None".
    # Checked last so the raises above keep their positions. A bool is refused too:
    # `True` is an int to Python and a version to nobody.
    schema_version = raw["schema_version"]
    if isinstance(schema_version, str) and schema_version.strip().isdigit():
        schema_version = int(schema_version)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise KyoseiError(
            f"{path} key 'schema_version' must be a whole number; found "
            f"{schema_version!r} — write back the number this store was built at "
            f"(`git log -p -- {path}` shows the last value it held)"
        )
    project_prefix = raw["project_prefix"]
    if not isinstance(project_prefix, str) or not project_prefix.strip():
        raise KyoseiError(
            f"{path} key 'project_prefix' must be the non-empty id prefix every record "
            f"carries (the letters before the dash, e.g. KYO in KYO-1); found "
            f"{project_prefix!r} — write it back as it was, since every record id in "
            "this store already carries it"
        )
    # KYO-410: a branch name, or nothing — `worker_model`'s parse, for its reason: a
    # blank `trunk:` reads to a human as a declaration, and treating it as absent
    # would hand the question back to git without saying so. Whether the name is a
    # real ref is checked where it is used (`cmd_check_branch.resolve_trunk`), because
    # loading config runs no git and a store command must not start to.
    trunk = raw.get("trunk")
    if trunk is not None and (not isinstance(trunk, str) or not trunk.strip()):
        raise KyoseiError(
            f"{path} key 'trunk' must be a branch name (a string), or absent to let "
            f"git's origin/HEAD, init.defaultBranch or main decide; found {trunk!r} — "
            "delete the line, or give it a value"
        )
    # KYO-416: a string of root shas, or nothing. Refused rather than coerced, on
    # `worker_model`'s reasoning: a value that is not one is a store saying something
    # about its code repository that the adoption check cannot read.
    code_repo_id = raw.get("code_repo_id")
    if code_repo_id is not None and (
        not isinstance(code_repo_id, str) or not code_repo_id.strip()
    ):
        raise KyoseiError(
            f"{path} key 'code_repo_id' must be the code repository's root-commit sha(s), "
            f"comma-joined, or absent; found {code_repo_id!r} — delete the line (the "
            "store then reads as one whose code repository is not recorded)"
        )
    # KYO-556.c5: `kind_grants_notice`'s parse exactly, for its reason. Last, after
    # every other raise, so the raises above keep their positions.
    policy_notice = raw.get("kind_policy_notice")
    if policy_notice is not None and not isinstance(policy_notice, bool):
        raise KyoseiError(
            f"{path} key 'kind_policy_notice' must be true or false, or absent to leave "
            f"the notice on; found {policy_notice!r} — write `kind_policy_notice: false` "
            "to turn the notice off, or delete the line"
        )
    layout_at_init = raw.get("layout_at_init")
    return Config(
        root=root,
        schema_version=schema_version,
        project_prefix=project_prefix,
        export_dir=str(raw["export_dir"]),
        code_root=code_root,
        layout_at_init=str(layout_at_init) if layout_at_init is not None else None,
        publication_acknowledged=acknowledged,
        worker_model=worker_model.strip() if worker_model is not None else None,
        worktree_setup=worktree_setup,
        controller_only_commands=controller_only_commands,
        orchestrator_only_key=orchestrator_only_key,
        worker_command_timeout_ceiling_seconds=timeout_ceiling,
        docs_suggest_notice=notice,
        kind_grants_notice=grants_notice,
        kind_policy_notice=policy_notice,
        trunk=trunk.strip() if trunk is not None else None,
        code_repo_id=code_repo_id.strip() if code_repo_id is not None else None,
        store_via_main_checkout=store_via_main_checkout,
    )


#: The keys a ``worktree_setup`` mapping entry may carry (KYO-164). Closed, and the
#: refusal names the set: a misspelled ``notes:`` that parsed to an entry with no note
#: would put the brief back to rendering commands only, which is the defect this key's
#: mapping form exists to fix — silently, and in the one document nobody can diff.
SETUP_ENTRY_KEYS = frozenset({"run", "note"})


def _parse_setup_entry(entry: object, index: int, path: Path) -> SetupCommand:
    """One ``worktree_setup`` entry: a bare command, or ``{run: ..., note: ...}``.

    KYO-164. The bare string is unchanged and stays the recommended form — a project
    that just wants three commands should not have to write a mapping — and the mapping
    is how a project attaches the caveat that used to live in a YAML comment the brief
    never saw. Every refusal below names the fix, because a config this key cannot parse
    is a project whose workers get a brief that describes a tree nobody looked at.
    """
    where = f"{path} key 'worktree_setup' entry {index + 1}"
    if isinstance(entry, str):
        run, note = entry, ""
    elif isinstance(entry, dict):
        unknown = sorted(set(entry) - SETUP_ENTRY_KEYS)
        if unknown:
            raise KyoseiError(
                f"{where} has key(s) no consumer reads: {', '.join(unknown)} — a "
                f"worktree_setup entry is either a bare command string or a mapping with "
                f"{' and '.join(sorted(SETUP_ENTRY_KEYS))} (note is optional)"
            )
        if "run" not in entry:
            raise KyoseiError(
                f"{where} is a mapping with no 'run' key, so it declares a note about no "
                f"command; write it as:\n"
                f"      - run: <the command>\n"
                f"        note: <what the command does not say about itself>"
            )
        run, note = entry["run"], entry.get("note") or ""
        if not isinstance(note, str) or (entry.get("note") is not None and not note.strip()):
            # A blank note reads as a declaration while declaring nothing, and renders as
            # an empty comment beside the command — the same refusal `worker_model` makes
            # about a blank value, for the same reason (law 6).
            raise KyoseiError(
                f"{where} has an empty 'note', which looks like a declaration while "
                f"declaring nothing — delete the key, or say what the command does not "
                f"say about itself"
            )
    else:
        raise KyoseiError(
            f"{where} must be a command (a string) or a mapping with "
            f"{' and '.join(sorted(SETUP_ENTRY_KEYS))}; found {type(entry).__name__}"
        )

    if not isinstance(run, str) or not run.strip():
        raise KyoseiError(
            f"{where} must be a non-empty command (a string); found {run!r} — remove the "
            "entry, or give it a command"
        )
    # Rendered one-per-line into a printed block and into the frozen brief's markdown,
    # both of which are line-oriented; a newline inside one entry would silently become
    # two commands in the brief and one string here. The note is rendered as one comment
    # line beside its command and is held to the same rule for the same reason.
    for label, text in (("command", run), ("note", note)):
        if "\n" in text:
            raise KyoseiError(
                f"{where}'s {label} contains a newline, but each is rendered as one line "
                "in the brief and in the `kyo dispatch` output — "
                + (
                    "split it into one list entry per command"
                    if label == "command"
                    else "write it as a single line"
                )
            )
    return SetupCommand(run.strip(), note.strip())


def _parse_worktree_setup(value: object, path: Path) -> tuple[SetupCommand, ...]:
    """KYO-143: validate ``worktree_setup`` into a tuple of shell commands.

    KYO-164: each is a :class:`SetupCommand` — a ``str`` that also carries the note the
    project attached, so every existing reader sees exactly the string it saw before and
    a reader that knows about notes can render one beside its command.

    Absent means *nothing declared* and yields ``()`` — a state the brief states
    out loud rather than papering over (KYO-143.c4). Every other shape that could
    be mistaken for a declaration is refused with the remedy named, because this
    key's whole value is that a worker can trust what the brief says about its
    worktree: a key that silently parsed to nothing would put the brief back to
    describing a tree it has not looked at.
    """
    if value is None:
        return ()
    # A bare string is the likely first attempt and is genuinely ambiguous — one
    # command, or a shell script to be run by a shell Kyosei never invokes? Refused
    # rather than guessed, with the one-line fix named (law 6).
    if isinstance(value, str):
        raise KyoseiError(
            f"{path} key 'worktree_setup' must be a LIST of commands, not a bare "
            f"string; found {value!r} — write it as a list with one entry:\n"
            f"    worktree_setup:\n      - {value}"
        )
    if not isinstance(value, list):
        raise KyoseiError(
            f"{path} key 'worktree_setup' must be a list of shell commands, or absent "
            f"to declare none; found {type(value).__name__}"
        )
    # An empty list reads to a human as a declaration while declaring nothing, and
    # is indistinguishable downstream from the absent case it would silently become
    # — the same refusal `worker_model` makes about a blank value, for the same
    # reason (law 6: an unknown never borrows a value).
    if not value:
        raise KyoseiError(
            f"{path} key 'worktree_setup' is an empty list, which declares nothing "
            "while looking like a declaration — delete the key to declare that this "
            "project needs no worktree setup, or list the commands it does need"
        )
    return tuple(_parse_setup_entry(entry, index, path) for index, entry in enumerate(value))


#: KYO-519: the canonical spelling of the whole-suite key, and the spelling
#: it had before the controller -> orchestrator rename. Both are registered in
#: ``schema.CONFIG_CONSUMERS`` and ``schema.OPTIONAL_CONFIG_KEYS`` PERMANENTLY: a build
#: older than KYO-519 refuses the new spelling (every hook goes blind, KYO-335), so no
#: command ever writes it -- the operator renames the key by hand once every `kyo` that
#: reads this store is at least KYO-519 -- and dropping the old one from the optional
#: set would make the next `kyo rebuild` delete a project's declaration (KYO-158).
ORCHESTRATOR_ONLY_KEY = "orchestrator_only_commands"
CONTROLLER_ONLY_KEY = "controller_only_commands"


def _read_orchestrator_only_commands(
    raw: dict, path: Path
) -> tuple[tuple[str, ...], str | None]:
    """The whole-suite commands, read under either spelling, and which spelling it was.

    Both spellings present with the SAME commands is accepted (two copies of one answer);
    with different commands it is refused by name, because which one the project meant is
    a guess (law 6).
    """
    present = [key for key in (ORCHESTRATOR_ONLY_KEY, CONTROLLER_ONLY_KEY) if key in raw]
    parsed = {key: _parse_controller_only_commands(raw.get(key), path, key) for key in present}
    if len(present) == 2 and parsed[ORCHESTRATOR_ONLY_KEY] != parsed[CONTROLLER_ONLY_KEY]:
        raise KyoseiError(
            f"{path} declares both '{ORCHESTRATOR_ONLY_KEY}' and '{CONTROLLER_ONLY_KEY}' "
            "with different commands, and they are two spellings of one key (KYO-519) -- "
            f"hand edit {path}: keep one of them. Keep '{CONTROLLER_ONLY_KEY}' while any "
            "kyo that reads this store is older than KYO-519, because an older build "
            f"refuses '{ORCHESTRATOR_ONLY_KEY}'"
        )
    if not present:
        return (), None
    return parsed[present[0]], present[0]


def _parse_controller_only_commands(
    value: object, path: Path, key: str = CONTROLLER_ONLY_KEY
) -> tuple[str, ...]:
    """KYO-298: validate ``controller_only_commands`` into a tuple of command lines.

    **The empty cases are both legal, and they mean the same thing: nothing is
    refused.** This is the one place this key differs deliberately from its sibling
    ``worktree_setup``, which refuses an empty list because there an empty
    declaration is indistinguishable from the absent one it would silently become.
    Here "declare nothing" and "declare an empty list" are the SAME decision — this
    project opts out of the guard — and both are safe, because the failure mode of
    an empty value is that a blocking `PreToolUse` hook stays silent rather than
    that a worker is told a falsehood about its worktree. Refusing the empty list
    would mean a consumer who tried the key, decided against it and emptied it gets
    a config every `kyo` command refuses to load (KYO-298.c1).

    Everything that is not a list of non-empty command lines IS refused, with the
    fix named: this list decides what a blocking hook refuses, so a value that
    parsed to something other than what the maintainer wrote would either block a
    command they never named or silently stop blocking one they did.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        # The likely first attempt, and genuinely ambiguous: one command, or a shell
        # line holding several? Refused rather than split on a guess — the same
        # refusal `worktree_setup` makes, for the same reason.
        raise KyoseiError(
            f"{path} key '{key}' must be a LIST of commands, not a "
            f"bare string; found {value!r} — write it as a list with one entry:\n"
            f"    {key}:\n      - {value}"
        )
    if not isinstance(value, list):
        raise KyoseiError(
            f"{path} key '{key}' must be a list of commands, or "
            f"absent to declare none; found {type(value).__name__} — hand edit "
            f"{path}: write the key as a list, or delete it"
        )
    commands: list[str] = []
    for index, entry in enumerate(value):
        where = f"{path} key '{key}' entry {index + 1}"
        if not isinstance(entry, str) or not entry.strip():
            raise KyoseiError(
                f"{where} must be a non-empty command line (a string) — found "
                f"{entry!r}. Hand edit {path}: write the command as a string, or "
                "remove the entry"
            )
        try:
            tokens = shlex.split(entry.strip())
        except ValueError as exc:
            raise KyoseiError(
                f"{where} is not a parseable command line ({exc}): {entry!r}. Hand "
                f"edit {path}: this is matched token by token against what a worker "
                "types, so it has to parse the same way a shell would"
            ) from exc
        if not tokens:
            raise KyoseiError(
                f"{where} holds no command once parsed: {entry!r}. Hand edit {path}: "
                "give it a command, or remove the entry"
            )
        commands.append(" ".join(tokens))
    return tuple(commands)


def _parse_timeout_ceiling_seconds(value: object, path: Path) -> int | None:
    """KYO-312: validate ``worker_command_timeout_ceiling_seconds`` into seconds, or ``None``.

    **Absent is the opt-out and the only default.** There is no shipped number, because
    the number is a fact about the consumer's harness rather than about Kyosei: what
    makes a long foreground command expensive is a prompt cache expiring inside it, and
    a project whose workers hold a one-hour cache pays nothing for a twenty-minute wait.
    A guessed ceiling would refuse commands that cost that project nothing, which is the
    over-blocking direction a blocking `PreToolUse` hook does not survive (KYO-33).

    Everything else is refused rather than coerced, and each refusal is a hand edit,
    because this one number decides what a blocking hook refuses:

    * a **bool** — YAML's ``true`` is an ``int`` in Python, and ``ceiling: true`` would
      otherwise parse to a one-second ceiling that refuses nearly every command a worker
      types. Checked first for exactly that reason.
    * a **float** or a **string** — ``"4m"`` and ``240.0`` are both a maintainer trying
      to say something this key cannot express, and guessing which way they meant it is
      the failure law 6 is about. ``4m`` is also the shape most likely to be *meant*,
      which is why the refusal spells the whole-seconds form back.
    * **zero or negative** — a ceiling of zero refuses every timed command in the
      worktree, which nobody writes on purpose; it is the shape of a half-finished edit.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise KyoseiError(
            f"{path} key 'worker_command_timeout_ceiling_seconds' must be a whole number "
            f"of SECONDS, or absent to declare no ceiling; found {value!r} — hand edit "
            f"{path}: write it as e.g. `worker_command_timeout_ceiling_seconds: 240` "
            "(four minutes), or delete the line"
        )
    if value <= 0:
        raise KyoseiError(
            f"{path} key 'worker_command_timeout_ceiling_seconds' is {value}, which "
            "would refuse every command that declares any timeout at all inside a "
            f"running dispatch's worktree — hand edit {path}: give it a positive number "
            "of seconds, or delete the line to declare no ceiling"
        )
    return value


# ---------------------------------------------------------------- KYO-560: onboarding

#: The config key that records the setup decisions an operator declined or deferred.
ONBOARDING_KEY = "onboarding"

#: The two answers the map records. A decision answered YES leaves its own trace (a
#: remote, a ``worktree_setup``, a hooks file), so only these two need writing down.
ONBOARDING_STATUSES: tuple[str, ...] = ("declined", "deferred")

#: The keys an answer's mapping form may carry: closed, on ``SETUP_ENTRY_KEYS``'
#: reasoning -- a misspelled ``reasons:`` that parsed to an answer with no reason would
#: drop the one thing the operator wrote down for the next agent.
ONBOARDING_ENTRY_KEYS = frozenset({"status", "reason"})

#: A setup-decision id as `docs/guide/setup-decisions.md` heads them (``### D4. ...``).
DECISION_ID_RE = re.compile(r"[A-Z]\d+")


@dataclass(frozen=True)
class OnboardingAnswer:
    """One recorded answer: the decision's id, ``declined`` or ``deferred``, and the
    operator's reason, ``""`` when none was written."""

    decision: str
    status: str
    reason: str = ""


def _onboarding_example(path: Path) -> str:
    return (
        f"hand edit {path}: write it as, e.g.,\n"
        "  onboarding:\n"
        "    B2: declined\n"
        "    D4: {status: deferred, reason: no code yet, so no install command}\n"
        "or delete the entry"
    )


def parse_onboarding(value: object, path: Path) -> dict[str, OnboardingAnswer]:
    """KYO-560: ``onboarding:`` into ``{decision id: answer}``, in the file's order.

    Each value is either a bare ``declined``/``deferred`` or a mapping
    ``{status: declined|deferred, reason: <text>}`` -- the bare-or-mapping shape
    ``worktree_setup``'s entries already take (KYO-164), so a one-word answer stays one
    word and a reason never needs a second key elsewhere. Absent or empty is ``{}``.

    Every other shape raises :class:`KyoseiError` naming the key and the entry, and
    nothing is guessed: ``B2: no`` is YAML's ``False``, and reading it as "declined"
    would be this store recording a decision nobody wrote (law 6). Called by its
    readers (:func:`kyosei.cmd_onboard.answered_items`, and since KYO-561
    :func:`kyosei.cmd_brief.record_links_declined`), never by :func:`load_config`,
    on ``close_wave``'s terms: the key changes no behaviour, so a typo in it must not
    blind every hook.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise KyoseiError(
            f"{path} key {ONBOARDING_KEY!r} must be a mapping from setup-decision ids to "
            f"`declined` or `deferred`; found {type(value).__name__} — "
            + _onboarding_example(path)
        )
    answers: dict[str, OnboardingAnswer] = {}
    statuses = " or ".join(f"`{s}`" for s in ONBOARDING_STATUSES)
    for ident, entry in value.items():
        where = f"{path} key {ONBOARDING_KEY!r} entry {ident!r}"
        if not isinstance(ident, str) or not DECISION_ID_RE.fullmatch(ident):
            raise KyoseiError(
                f"{where} is not a setup-decision id: ids are a capital letter and a "
                "number, the bracketed ids of the setup prompt (B2, D4) — "
                + _onboarding_example(path)
            )
        reason: object = ""
        if isinstance(entry, dict):
            unknown = sorted(map(str, set(entry) - ONBOARDING_ENTRY_KEYS))
            if unknown:
                raise KyoseiError(
                    f"{where} has key(s) no consumer reads: {', '.join(unknown)} — an "
                    f"answer's mapping carries `status` and an optional `reason` only; "
                    + _onboarding_example(path)
                )
            status = entry.get("status")
            reason = entry.get("reason", "")
            if not isinstance(reason, str) or ("reason" in entry and not reason.strip()):
                raise KyoseiError(
                    f"{where} has a `reason` that is not text, or is empty: found "
                    f"{reason!r} — write the reason, or drop the `reason` key; "
                    + _onboarding_example(path)
                )
        else:
            status = entry
        if status not in ONBOARDING_STATUSES:
            raise KyoseiError(
                f"{where} must be {statuses}, bare or as `status:` with an optional "
                f"`reason:`; found {status!r} — " + _onboarding_example(path)
            )
        answers[ident] = OnboardingAnswer(ident, str(status), " ".join(str(reason).split()))
    return answers


#: KYO-298.c1: optional keys a freshly written ``config.yml`` documents as a
#: COMMENTED EXAMPLE rather than by their absence.
#:
#: Absence is the right documentation for most optional keys, because absence is
#: what they mean: nobody has acknowledged publication, no layout was named at init.
#: ``controller_only_commands`` is different in one respect that matters — a
#: consumer cannot opt into a guard they have never heard of, and this key's whole
#: design is that the project decides for itself whether the guard exists. So the
#: generated file carries the offer, commented out, beside the generated comment
#: naming its readers. Uncommenting it is the entire opt-in; leaving it is the
#: entire opt-out, and a file that never mentioned it could only be one of those.
#:
#: Rendered only when the key is ABSENT from ``values``: once a project has declared
#: one, its own value and its own comments are what the file says (KYO-158), and an
#: example beside a real declaration would be two answers to one question.
#:
#: KYO-303 adds ``docs_suggest_notice`` on the same ground, running the other way. That
#: key exists only to say NO to something, and the thing it says no to is a notice
#: `kyo doctor` prints at people who may never have run `kyo docs suggest` — so the
#: switch has to be visible in the file the notice is about, or the only way to find it
#: is to go looking for a way to silence a tool, which is when people stop reading it
#: instead (KYO-33). c2 asks for "one stated config line"; this is where it is stated.
#:
#: KYO-563 adds ``worker_model`` and KYO-560 adds ``onboarding``, on the first ground:
#: `kyo onboard` tells an agent to write both here, and an agent setting up a consumer
#: project had to work out where and in what shape from prose, because the file it was
#: editing offered neither.
CONFIG_KEY_EXAMPLES: dict[str, str] = {
    "worker_model": (
        "# KYO-61. Uncomment to name the model this project's dispatched workers run on\n"
        "# by default (setup decision D1): every brief then states it. A kind may\n"
        "# override it (`policy.worker_model` in kyosei/kinds.yml) and one dispatch may\n"
        "# override both (`kyo dispatch --model`). Left commented out, a brief names no\n"
        "# model and the harness picks one.\n"
        "#\n"
        "# worker_model: claude-sonnet-5-5"
    ),
    "onboarding": (
        "# KYO-560. The setup decisions the operator DECLINED or DEFERRED, by their id in\n"
        "# `kyo onboard`'s prompt: a bare `declined` or `deferred`, or a mapping with a\n"
        "# reason. `kyo onboard --check` then reports each as declined or deferred, with\n"
        "# no fix, instead of as missing -- so the next agent does not ask again. A\n"
        "# decision answered yes needs no entry; its own setting is the answer. A kyo\n"
        "# older than this key refuses it, so declare it only once every kyo that reads\n"
        "# this store has it.\n"
        "#\n"
        "# onboarding:\n"
        "#   B2: declined\n"
        "#   D4:\n"
        "#     status: deferred\n"
        "#     reason: no code yet, so no install or test command"
    ),
    "controller_only_commands": (
        "# KYO-298. Uncomment and edit to declare which commands are THE WHOLE SUITE\n"
        "# here. `kyo check-command --pre-tool-use` (a PreToolUse hook installed by\n"
        "# `kyo install-hooks`) then refuses one of these inside the worktree of a\n"
        "# dispatch that is still running, naming what to run instead; `kyo doctor`\n"
        "# notices a worktree_setup entry that is itself one of them. Left commented\n"
        "# out, nothing is refused and nothing is noticed -- declaring the key is the\n"
        "# whole of opting in. A worker may still run one by prefixing it with\n"
        "# KYOSEI_SUITE_OK=1, which it is asked to report as a disagreement.\n"
        "# KYO-519: the key is also spelled orchestrator_only_commands; use that\n"
        "# spelling only once every kyo that reads this store is at least KYO-519,\n"
        "# because an older kyo refuses it and every hook goes blind.\n"
        "#\n"
        "# controller_only_commands:\n"
        "#   - make test\n"
        "#   - npm run check"
    ),
    "worker_command_timeout_ceiling_seconds": (
        "# KYO-312. Uncomment to declare the longest timeout a dispatched worker's Bash\n"
        "# call may DECLARE while it runs inside its own worktree. `kyo check-command\n"
        "# --pre-tool-use` (the same PreToolUse hook as above) then refuses a call whose\n"
        "# own timeout is longer, naming the remedy: detach the long run and wait in\n"
        "# SLICES, each look under the ceiling, because every look is a cache read that\n"
        "# keeps the cache alive and one expiry is a full context re-write. Left\n"
        "# commented out, no command is refused for being long -- declaring the key is\n"
        "# the whole of opting in, and it is independent of controller_only_commands.\n"
        "# There is no default: what a long wait costs depends on how long YOUR workers'\n"
        "# prompt cache lives, and a project whose workers hold a one-hour cache wants no\n"
        "# such refusal. A worker may still wait long by putting KYOSEI_LONG_OK=1 in\n"
        "# front of the command line, which it is asked to report as a disagreement.\n"
        "#\n"
        "# worker_command_timeout_ceiling_seconds: 240"
    ),
    "docs_suggest_notice": (
        "# KYO-303. `kyo docs suggest` drafts the documents.yml declarations this\n"
        "# repository looks like it wants -- a numbered: block for a document whose\n"
        "# headings are numbered, an exclusion for a generated file inside a set's\n"
        "# globs -- with the evidence beside each one. It writes nothing and no\n"
        "# `kyo doctor` PROBLEM depends on it; doctor prints one NOTICE when it has\n"
        "# something to say. Uncomment to stop that notice once you have read the\n"
        "# suggestions and declined them -- a suggestion that cannot be declined is\n"
        "# not a suggestion.\n"
        "#\n"
        "# docs_suggest_notice: false"
    ),
}


def render_config(values: dict[str, object]) -> str:
    """Render ``config.yml``'s text: one generated comment per key, naming its
    consumer(s) from :data:`kyosei.schema.CONFIG_CONSUMERS`.

    ``values`` must carry exactly the keys ``CONFIG_CONSUMERS`` declares, less any
    of :data:`kyosei.schema.OPTIONAL_CONFIG_KEYS` this project has no value for —
    same check :func:`load_config` makes on read, so a key can never go
    undocumented and a required key can never go unset (law 2, both directions).
    Because the comment text is generated from ``CONFIG_CONSUMERS`` rather than
    hand-typed, a consumer that gets renamed or a field that grows a second reader
    changes this file's comments automatically the next time it is written —
    nothing here is a parallel copy that could say something the code no longer
    does (law 3; KYO-2).
    """
    unknown = sorted(set(values) - set(CONFIG_CONSUMERS))
    missing = sorted(set(CONFIG_CONSUMERS) - OPTIONAL_CONFIG_KEYS - set(values))
    if unknown or missing:
        raise KyoseiError(
            "kyosei.config.render_config called out of step with CONFIG_CONSUMERS: "
            f"unknown keys {unknown}, missing keys {missing}"
        )
    lines = [CONFIG_HEADER.rstrip("\n")]
    for key in sorted(CONFIG_CONSUMERS):
        if key not in values:
            if key == CONTROLLER_ONLY_KEY and ORCHESTRATOR_ONLY_KEY in values:
                # KYO-519: the same key under its new spelling is declared; an example
                # beside it would be two answers to one question (KYO-158).
                continue
            consumers = ", ".join(CONFIG_CONSUMERS[key])
            example = CONFIG_KEY_EXAMPLES.get(key)
            if example is not None:
                lines.append(f"# {key}: read by {consumers}")
                lines.extend(example.rstrip("\n").splitlines())
                lines.append("")
            continue
        consumers = ", ".join(CONFIG_CONSUMERS[key])
        lines.append(f"# {key}: read by {consumers}")
        value_yaml = yaml.safe_dump(
            {key: values[key]}, sort_keys=False, default_flow_style=False
        )
        lines.append(value_yaml.rstrip("\n"))
    return "\n".join(lines) + "\n"


def read_raw_config(root: Path) -> dict:
    """``kyosei/config.yml`` as it stands on disk, or ``{}`` if there is none.

    Deliberately unvalidated: the callers below are rewriting the file, and one
    of them (`kyo rebuild`) exists precisely to repair a config that
    :func:`load_config` would refuse.
    """
    path = config_path_for(root)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:  # pragma: no cover - depends on user damage
        return {}
    return raw if isinstance(raw, dict) else {}


def record_publication_acknowledgement(root: Path, *, actor: str, at: str) -> Path:
    """Store ``publication_acknowledged`` without disturbing anything else (KYO-60).

    Deliberately *not* :func:`write_config`. That function stamps the build's
    ``SCHEMA_VERSION`` — correct for `kyo init` and for `kyo rebuild`, which have
    just written a store at that version, and wrong here: acknowledging a
    publication says nothing about the store's schema, and silently bumping the
    number would erase exactly the db/config drift KYO-5's schema-version errors
    exist to report. Every other key is carried through untouched, and
    :func:`render_config` still refuses a key no consumer reads.
    """
    raw = read_raw_config(root)
    missing = sorted(set(CONFIG_CONSUMERS) - OPTIONAL_CONFIG_KEYS - set(raw))
    if missing:
        raise KyoseiError(
            f"{config_path_for(root)} is missing required keys: {', '.join(missing)} — "
            "it cannot be rewritten safely; repair it (or run `kyo rebuild`) first"
        )
    values = dict(raw)
    values["publication_acknowledged"] = {"actor": actor, "at": at}
    path = config_path_for(root)
    path.write_text(render_config(values), encoding="utf-8")
    return path


def write_config(
    root: Path,
    *,
    project_prefix: str,
    export_dir: str = "export",
    rewritten_wholesale: list[str] | None = None,
    **optional: object,
) -> Path:
    """Write ``kyosei/config.yml``, carrying forward every optional key already set.

    KYO-60: the carry-forward is the point, not a nicety. This function is not
    only `kyo init`'s — `kyo rebuild` calls it on every rebuild to keep
    ``schema_version`` in step with the database it just rewrote (KYO-5.c1), and
    it passes only the required keys. Rendering from those alone would delete a
    stored acknowledgement, so a rebuild would silently re-arm the push warning
    somebody had already answered, and the once-only guarantee (KYO-60.c3) would
    hold only until the next rebuild. Explicit arguments still win, so
    ``kyo check-push --acknowledge`` can overwrite an older acknowledgement.

    KYO-158: *which* keys are carried is derived, never listed. It used to be a
    hand-written parameter list — ``layout_at_init``, ``publication_acknowledged``,
    ``worker_model`` — and ``worktree_setup`` (KYO-143) was added to the parser, to
    ``CONFIG_CONSUMERS`` and to :class:`Config`, but not to that list. So every
    ``kyo rebuild`` silently deleted it, and the comment block a project wrote
    around it, from any store that had declared one. The carried set is now exactly
    :data:`kyosei.schema.OPTIONAL_CONFIG_KEYS` — which is not a second list to keep
    in step but the *same* set ``load_config``, ``render_config`` and
    ``doctor_consumers.check_config_consumers`` already read. The three keys this function
    computes instead of carrying (``schema_version``, stamped from the build, and
    the two supplied by the caller) are exactly the non-optional ones, so nothing
    here has to be remembered when the next key is added: an optional key is
    carried the moment it is registered, and a required one makes
    :func:`render_config` refuse the call by name until it is supplied.

    ``**optional`` accepts any key in ``OPTIONAL_CONFIG_KEYS`` and refuses anything
    else, naming what is legitimate — the same failure a misspelled keyword used to
    produce, with the registry quoted instead of a signature.

    Pass ``rewritten_wholesale`` a list to be told, without anything being blocked,
    that the file could not be updated in place and was re-rendered from its values
    — the case where a project's own comments do not survive (KYO-158.c4).
    """
    unknown = sorted(set(optional) - OPTIONAL_CONFIG_KEYS)
    if unknown:
        raise KyoseiError(
            f"kyosei.config.write_config got keyword(s) no config key claims: "
            f"{', '.join(unknown)} — the optional keys it may be given are "
            f"{', '.join(sorted(OPTIONAL_CONFIG_KEYS))} "
            "(kyosei.schema.OPTIONAL_CONFIG_KEYS)"
        )
    path = config_path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_raw_config(root)
    values: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "project_prefix": project_prefix,
        "export_dir": export_dir,
    }
    # Derived from the registry, in sorted order so the rendered file is stable.
    for key in sorted(OPTIONAL_CONFIG_KEYS):
        given = optional.get(key)
        carried = given if given is not None else existing.get(key)
        if carried is not None:
            values[key] = carried

    before = path.read_text(encoding="utf-8") if path.is_file() else None
    if before is not None:
        patched = _patched_config_text(before, existing, values)
        if patched is not None:
            if patched != before:
                path.write_text(patched, encoding="utf-8")
            return path

    after = render_config(values)
    if before is not None and rewritten_wholesale is not None:
        rewritten_wholesale.extend(_comment_lines_dropped(before, after))
    path.write_text(after, encoding="utf-8")
    return path


#: The per-key comment :func:`render_config` generates, whatever consumers it names,
#: plus whatever the project appended to it. Kyosei's half is derived from
#: ``CONFIG_CONSUMERS`` and must be refreshed on every write or KYO-2's generated
#: documentation could drift; the suffix is the project's and must survive. The
#: consumer list is a run of dotted identifiers, so the two are separable without
#: guessing — anything after the last identifier belongs to whoever typed it. A dot is
#: part of an identifier only BETWEEN two words: the full stop a project puts after the
#: list ("... doctor.check_x. Operator, 2026-09-19: declared.") is theirs, and a pattern
#: that read it as the list's last character regenerated the comment without it, running
#: the tool's half straight into the person's (KYO-359).
_DOTTED_NAME = r"\w+(?:\.\w+)*"
_GENERATED_KEY_COMMENT = re.compile(
    rf"^#[ \t]*(?P<key>[A-Za-z_]\w*)[ \t]*: read by "
    rf"(?P<consumers>{_DOTTED_NAME}(?:, {_DOTTED_NAME})*)(?P<suffix>.*)$"
)

#: Values :func:`_patched_config_text` will substitute on a single line. A mapping or
#: a list is re-rendered as a block, which cannot be spliced into one line without
#: guessing at the indentation and the comments already inside it.
_SUBSTITUTABLE = (str, int, float, bool)


def _patched_config_text(
    before: str, existing: dict, values: dict[str, object]
) -> str | None:
    """``before`` with only what actually changed substituted, or ``None``.

    KYO-158.c4: a project's comments — the block explaining *why* this project's
    ``worktree_setup`` is what it is, and the inline notes beside its individual
    commands — are lost by a full re-render, because ``yaml.safe_load`` does not keep
    them and a round-trip YAML library is a dependency decision above one record.
    They do not have to be lost. The rebuild that motivates this call changes exactly
    one scalar (``schema_version``), and a file whose key *set* is already right can
    have that scalar substituted on its own line, leaving every other byte — every
    comment, every blank line, the existing key order — untouched.

    Returns ``None`` whenever that cannot be done exactly, which sends the caller to
    the honest full re-render: a key was added or removed (the new key needs its
    generated comment, and only ``render_config`` knows where to put it), a changed
    value is a block rather than a scalar, or a key is not on a line of its own. The
    patched text is re-parsed and compared against ``values`` before it is returned,
    so a splice that produced something other than what was asked for falls back
    rather than shipping.
    """
    if set(existing) != set(values):
        return None
    lines = before.splitlines(keepends=True)
    positions: dict[str, int] = {}
    for key in values:
        hits = [i for i, line in enumerate(lines) if re.match(rf"{re.escape(key)}[ \t]*:", line)]
        if len(hits) != 1:
            return None
        positions[key] = hits[0]

    for key, index in positions.items():
        if existing[key] != values[key]:
            replacement = _substituted_value_line(lines[index], key, existing[key], values[key])
            if replacement is None:
                return None
            lines[index] = replacement
        # KYO-2: the generated comment names the code paths that read this key and is
        # regenerated on every write precisely so it cannot drift from the registry.
        # Preserving the project's comments must not smuggle in a stale copy of ours.
        comment_index = index - 1
        if comment_index >= 0:
            refreshed = _refreshed_generated_comment(lines[comment_index], key)
            if refreshed is not None:
                lines[comment_index] = refreshed

    patched = "".join(lines)
    try:
        if yaml.safe_load(patched) != values:
            return None
    except yaml.YAMLError:  # pragma: no cover - a splice that broke the document
        return None
    return patched


def _substituted_value_line(
    line: str, key: str, old: object, new: object
) -> str | None:
    """``line`` with ``key``'s value replaced, keeping any comment trailing it."""
    if not isinstance(new, _SUBSTITUTABLE):
        return None
    rendered = yaml.safe_dump({key: new}, default_flow_style=False, sort_keys=False).strip()
    if "\n" in rendered:
        return None
    body = line.rstrip("\r\n")
    ending = line[len(body) :]
    # Find where a trailing comment starts by asking the parser rather than by
    # scanning for '#': a '#' inside a quoted value is not a comment, and treating
    # one as if it were would move half a value into a comment.
    trailing = ""
    for offset in (i for i, char in enumerate(body) if char == "#"):
        try:
            if yaml.safe_load(body[:offset]) == {key: old}:
                # Keep the spacing the project chose, not a spacing of our own.
                trailing = body[len(body[:offset].rstrip()) :]
                break
        except yaml.YAMLError:
            continue
    return rendered + trailing + ending


def _refreshed_generated_comment(line: str, key: str) -> str | None:
    """The generated comment for ``key``, current consumers, project suffix kept.

    ``None`` if ``line`` is not this key's generated comment — a blank line, a
    comment the project wrote, or another key's.
    """
    body = line.rstrip("\r\n")
    match = _GENERATED_KEY_COMMENT.match(body)
    if match is None or match.group("key") != key or key not in CONFIG_CONSUMERS:
        return None
    consumers = ", ".join(CONFIG_CONSUMERS[key])
    return f"# {key}: read by {consumers}{match.group('suffix')}" + line[len(body) :]


def _comment_lines_dropped(before: str, after: str) -> list[str]:
    """Lines of ``before`` that carry comment text and are not in ``after``.

    Deliberately literal: it reports lines that were in the file and are not in the
    file any more, rather than trying to classify what a comment *meant*. A rewrite
    that keeps everything reports nothing, which is what makes saying something
    worth reading (KYO-33: a notice that fires routinely trains people past it).
    """
    kept = set(after.splitlines())
    return [
        line
        for line in before.splitlines()
        if "#" in line and line.strip() and line not in kept
    ]


# ---------------------------------------------------------------------- KYO-335
#
# THE `kyo` ON PATH IS A DIFFERENT PROGRAM FROM THE ONE YOU ARE RUNNING, and every
# hook in a project runs that one. Measured on this store (KYO-325.r1, and the
# correction note on KYO-325): the controller added
# `worker_command_timeout_ceiling_seconds` to `kyosei/config.yml` from a working-tree
# `kyo` that reads the key; the `kyo` the hooks ran was an installed build, a snapshot
# behind the source tree, which refuses a key no code path of ITS reads (see
# `load_config` above). For the next 120.5 minutes `ctx.store()` raised inside every
# hook: no session-start brief, no stop audit, no push or command guard, and the two
# `SubagentStop` guards reporting on the controller's checkout instead of three
# workers' trees. It ended only when an unrelated reinstall brought the builds back
# into agreement.
#
# The trigger is standing and it is not the config key: it is that the build which
# WRITES a store can be newer than the build which READS it on every hook, and
# nothing on either side asked the other. So this section asks — once, cheaply, of
# the actual program a hook's `/bin/sh` would find.
#
# WHY THE QUESTION IS PUT TO THAT BUILD RATHER THAN INFERRED. A version string cannot
# answer it: "older" does not imply "refuses this store" (most older builds read it
# fine), and the two builds' schema numbers say nothing about a config key. `kyo
# where --json` is the one question whose answer IS the thing wanted — it exits 0
# whether or not the store opens, carries `can_open`, and carries the refusal it
# would otherwise have raised verbatim in `store_problem` (KYO-184). What comes back
# is the PATH build's own sentence about its own refusal, never a reconstruction of
# it here.

#: The program name a hook's shell looks up. The packaged hooks are `command -v kyo`
#: followed by `kyo <subcommand>` (KYO-316), so this is the name that decides which
#: build every guard in a project actually is.
PATH_PROGRAM = "kyo"

#: How long the probe waits for that build. It is one `kyo where`, which opens a
#: store and renders nothing; a build that cannot answer in this long is a build the
#: caller should be told about rather than blocked by, and :func:`probe_path_build`
#: returns the timeout as a problem it could not read rather than raising.
PROBE_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class PathBuild:
    """The `kyo` a hook's shell would run, and whether it can open this store.

    :attr:`can_open` is a three-valued answer on purpose (law 6): ``True`` it opens
    the store, ``False`` it refuses the store and :attr:`problem` is its own words
    for why, ``None`` the probe could not get an answer at all — that build is too
    old to have `kyo where`, or it timed out, or it is not executable here. ``None``
    is never rendered as either verdict, and a caller that treated it as "fine"
    would be re-making the mistake this record is about.
    """

    #: The resolved path ``command -v kyo`` would print.
    executable: Path
    can_open: bool | None
    #: That build's OWN refusal text, verbatim from ``kyo where --json``'s
    #: ``store_problem``, or the reason the probe got no answer. ``None`` when the
    #: store opens.
    problem: str | None
    #: What that build calls itself — ``kyo version``'s line — or ``None`` when it
    #: could not say. Read only when the store does NOT open, so the common path
    #: stays one subprocess.
    version_line: str | None = None
    #: That build's own ``install_kind`` (``buildinfo.INSTALL_KIND_*``), which is
    #: what decides the remedy: a released uv tool moves with `uv tool upgrade`,
    #: a snapshot by re-snapshotting it (KYO-446). Asked of that build rather than derived
    #: from its path here, because it is the build that knows.
    install_kind: str | None = None
    #: KYO-446.c3: that build's own ``release`` fact from ``kyo version --json``
    #: (KYO-445), which splits a uv tool's remedy: ``uv tool upgrade kyosei`` for a
    #: release, the re-snapshot for anything else. ``None`` when that build predates
    #: the field -- which also predates every release, so it reads as unreleased.
    release: bool | None = None

    @property
    def blind(self) -> bool:
        """Is every hook running this build checking nothing? Only a definite no."""
        return self.can_open is False


def path_program(
    environ: dict[str, str] | None = None, *, argv0: str | None = None
) -> Path | None:
    """The `kyo` on PATH, unless it is the program already running.

    ``None`` in the two cases that have nothing to report: nothing called ``kyo`` is
    on PATH at all (the packaged hooks' own `command -v` guard covers that, KYO-316,
    and it names the install remedy far better than a second voice would), or what is
    on PATH is this very file's console script — where "the PATH build" and "the
    running build" are one program and there is no lag to describe.

    The identity test is a cheap short-circuit rather than the authority: two
    different scripts can run the same source (``.venv/bin/kyo`` and ``python -m
    kyosei`` out of the same tree), and those answer ``can_open: true`` through the
    probe and say nothing anyway. What it buys is that the overwhelmingly common
    case — a hook running the very build it was installed by — costs no subprocess.
    """
    environ = os.environ if environ is None else environ
    found = shutil.which(PATH_PROGRAM, path=environ.get("PATH"))
    if not found:
        return None
    resolved = Path(found).resolve()
    argv0 = sys.argv[0] if argv0 is None else argv0
    try:
        if argv0 and Path(argv0).resolve() == resolved:
            return None
    except OSError:  # pragma: no cover - a argv[0] this box cannot stat
        pass
    return resolved


def probe_path_build(
    root: Path,
    *,
    environ: dict[str, str] | None = None,
    executable: Path | None = None,
) -> PathBuild | None:
    """Ask the `kyo` on PATH whether it can open the store at ``root``.

    ``None`` when there is nothing to ask (see :func:`path_program`). Otherwise one
    ``kyo where --json --root <root>``, run with this process's environment
    inherited exactly as a hook's would be, and a second call
    (``kyo version --json``) only when the answer is no — so a healthy project pays
    one subprocess and the broken one pays two to be able to name the build and its
    upgrade route.

    Never raises. Every way this can fail — the build predates `kyo where`, it is not
    executable, it hangs, it prints something that is not JSON — becomes
    ``can_open=None`` with the reason in :attr:`PathBuild.problem`, because a probe
    that raised would take down the very commands it exists to keep honest.
    """
    executable = path_program(environ) if executable is None else executable
    if executable is None:
        return None
    where, problem = _probe_json(executable, ["where", "--json", "--root", str(root)])
    if where is None:
        return PathBuild(executable=executable, can_open=None, problem=problem)
    can_open = where.get("can_open")
    if can_open is True:
        return PathBuild(executable=executable, can_open=True, problem=None)
    if can_open is not False:
        return PathBuild(
            executable=executable,
            can_open=None,
            problem=(
                f"`{executable} where --json` answered without a `can_open` field, so "
                "this build could not be asked whether it can open the store"
            ),
        )
    stated = where.get("store_problem")
    version, _ = _probe_json(executable, ["version", "--json"])
    return PathBuild(
        executable=executable,
        can_open=False,
        problem=str(stated) if stated else "that build gave no reason",
        version_line=(
            str(version.get("line")) if isinstance(version, dict) and version.get("line") else None
        ),
        install_kind=(
            str(version.get("kind")) if isinstance(version, dict) and version.get("kind") else None
        ),
        release=(
            version.get("release")
            if isinstance(version, dict) and isinstance(version.get("release"), bool)
            else None
        ),
    )


def _probe_json(executable: Path, argv: list[str]) -> tuple[dict | None, str]:
    """``<executable> <argv>`` parsed as JSON, or ``(None, why not)``."""
    try:
        result = subprocess.run(
            [str(executable), *argv],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"`{executable} {' '.join(argv)}` did not answer within "
            f"{PROBE_TIMEOUT_SECONDS}s, so what that build makes of this store is "
            "not known"
        )
    except OSError as exc:
        return None, (
            f"`{executable} {' '.join(argv)}` could not be run ({exc}), so what that "
            "build makes of this store is not known"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        first = (result.stderr or result.stdout or "").strip().splitlines()
        return None, (
            f"`{executable} {' '.join(argv)}` exited {result.returncode} without JSON "
            f"({first[0] if first else 'no output'}), so what that build makes of this "
            "store is not known — a build from before that command existed answers "
            "this way"
        )
    return (payload if isinstance(payload, dict) else None), ""


def project_runs_kyo_from_path(code_root: Path) -> bool:
    """Does this project install hooks that run the `kyo` on PATH? (KYO-335)

    The structural gate on every caller of :func:`probe_path_build`, and it takes
    :func:`kyosei.doctor_install.check_hook_config`'s posture one field along ("silent when
    no settings file exists: hooks are a convenience, not a requirement"): a project
    whose hooks do not run `kyo` pays nothing when the PATH build is behind, so a
    finding about it would be noise in the one command a reader consults to find
    real trouble. It also keeps the probe — the only thing here that starts a
    subprocess — off every store that was never the subject of this record, which is
    what makes leaving the check switched on cheap.

    Deliberately a text search rather than a walk of the hook structure. The question
    is "would a shell here ever run `kyo`", the file is the consumer's own and may
    carry hooks Kyosei never wrote, and ``install_hooks.hook_drift`` is already the check
    that has opinions about that file's shape.

    Imported locally: :mod:`kyosei.scaffold` imports this module.
    """
    from .install_hooks import CLAUDE_SETTINGS_REL

    try:
        text = (code_root / CLAUDE_SETTINGS_REL).read_text(encoding="utf-8")
    except OSError:
        return False
    return f"{PATH_PROGRAM} " in text


def path_build_remedy(build: PathBuild) -> str:
    """How to move the PATH build so it stops refusing this store.

    Asked of :mod:`kyosei.buildinfo`'s existing per-install-kind answer, with that
    build's OWN kind (read off its ``kyo version --json``) rather than this one's —
    the KYO-317 defect exactly one step along: a line printed about one install that
    names the upgrade route of another sends a reader to a command that moves a CLI
    they are not running. KYO-446: that build's ``release`` travels with its kind, so a
    uv tool's remedy is the re-snapshot for an unreleased snapshot and ``uv tool
    upgrade kyosei`` for a release -- never this copy's answer to either. ``False``
    rather than ``None`` when it did not say: ``upgrade_route`` with no release fact
    and no kind would fill both in from the RUNNING copy.
    """
    from .buildinfo import upgrade_route

    return upgrade_route(build.install_kind, build.release is True)


def path_build_lag_lines(build: PathBuild, *, subject: str) -> list[str]:
    """The finding, in the one voice `kyo doctor` and `kyo rebuild` both speak.

    ``subject`` is what the caller was doing — "this store", "the store this rebuild
    just wrote" — so the same three sentences read correctly whether they are a
    warning before the fact or a finding after it. One function because the two
    callers must not be able to describe this condition differently (law 3).
    """
    named = build.version_line or f"the build at {build.executable}"
    return [
        f"the `kyo` on PATH ({build.executable}) cannot open {subject}: {build.problem}",
        f"  that build: {named}",
        "  what it costs: every hook this project installs runs the `kyo` on PATH, so "
        "until this is cleared each one exits without checking anything — no "
        "session-start brief, no stop audit, no worker-tree or push or command guard "
        "(KYO-325 measured 120.5 minutes of exactly this)",
        f"  remedy: {path_build_remedy(build)} — or undo the store-side change that "
        "build refuses, which is the same fix from the other end",
    ]
