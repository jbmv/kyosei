"""``kyo check-push`` — the guard between a push and everything the store holds.

KYO-60. A project that keeps its store inside the code repository publishes, on
its first push, every idea file, record description, note, shift note, evidence
artifact and frozen brief it has ever held — plus ``_events.jsonl``, which has
already copied prose out of all of them. That choice is made once, at `kyo init`,
often by someone who has not yet written anything private into the store; the
consequence arrives months later at a `git push`, by which time the decision is
forgotten and the store holds a great deal more than it did. ``kyo redact``
cannot help afterwards: it explicitly cannot reach committed history.

**Block the agent, warn the human.** The two situations are different and get
different treatment:

* An agent asking to push has no standing to publish the maintainer's private
  thinking. ``--pre-tool-use`` reads Claude Code's ``PreToolUse`` payload and
  exits 2 — the tool call never runs and the message reaches the agent, which
  must surface it rather than proceed. Same mechanism as the ``SubagentStop``
  guards in :mod:`kyosei.cmd_check_clean` and :mod:`kyosei.cmd_check_branch`,
  distributed the same way (``kyo install-hooks``), reported the same way
  (`kyo doctor`'s ``hook_config``).
* A human pushing their own repository owns that decision. ``--advisory`` prints
  the same thing and exits 0; it is what the git ``pre-push`` hook runs, which
  ``kyo harness install claude-code`` writes on the plugin route and ``kyo
  install-hooks`` on the copies (KYO-612), and ``git push --no-verify`` skips it.
  ``.git/hooks`` is not versioned, so a fresh clone does not have that half at
  all — a real limitation of the human side, stated rather than papered over.

**It fires once.** A warning on every push is noise, and noise gets ignored —
the exact failure KYO-33 was filed for. ``--acknowledge`` records who and when in
``kyosei/config.yml`` under ``publication_acknowledged``, and every mode is silent
afterwards. That acknowledgement is *per project*, not per clone: the hazard is a
property of the project, and re-blocking every agent in every fresh clone forever
is precisely the noise this is trying to avoid. It names the actor, so a colleague
who inherits it can see whose call it was.

**It measures rather than describes.** The numbers are counted at the moment the
push is attempted, from the store surfaces themselves — see
:data:`SURFACE_COUNTERS`, one counter per surface
:data:`kyosei.install_prose.COMMITTED_PROSE_SURFACES` declares.

**A private term in a pushed commit is refused for everyone (KYO-669).** The messages
and author names of the commits a push sends are matched against the terms in
:mod:`kyosei.private_terms`. Publishing a store is a choice a person may reasonably
make, so that half only warns them; a private term in a public commit is almost always
a mistake and is expensive to undo, so this half refuses a person's push as well as an
agent's. ``--pre-push`` is the git hook's mode and exits :data:`EXIT_PRIVATE_TERM`,
which is the only status the hook turns into a refusal: the store warning, a missing
``kyo`` and a broken ``kyo`` all leave a push alone. ``git push --no-verify`` remains
the deliberate way past it.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import events_ledger as events_ledger_module
from . import export as export_module
from . import ideas as ideas_module
from . import ledgers as ledgers_module
from . import shell_words
from .clock import now
from .commands import Context, Output
from .config import (
    KYOSEI_DIR,
    LAYOUT_SPLIT,
    LAYOUT_UNCONFIGURED,
    Layout,
    read_raw_config,
    record_publication_acknowledgement,
    refuse_store_write,
    resolve_layout,
)
from .contracts import (
    CONTRACT_PUSH_CARRIES_A_PRIVATE_TERM,
    CONTRACT_PUSH_PUBLISHES_THE_STORE,
    GUARDED_BRANCH_DIRS,
)
from .errors import EXIT_CONTRACT, EXIT_PRIVATE_TERM, GuardRefusal, KyoseiError
from .git_queries import run_git
from .install_prose import COMMITTED_PROSE_SURFACES
from .private_terms import Terms, project_terms
from .scaffold import IDEAS_DIR

# ------------------------------------------------------- what counts as "a push"

#: ``git`` options that sit *before* the subcommand and take a separate value, so
#: ``git -C some/dir push`` is still a push.
_GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)

#: ``git`` options that point it at a repository by a route other than a directory
#: move. A push carrying one is published from somewhere this parse does not follow,
#: so its directory is reported unknown rather than assumed (KYO-409).
_GIT_ELSEWHERE_OPTIONS = frozenset({"--git-dir", "--work-tree"})

#: Non-``git`` commands that push as a side effect. Kept to an explicit, short
#: list rather than a pattern: this is a *blocking* hook, and matching broadly is
#: the dangerous direction (see :func:`publishing_commands`).
_IMPLICIT_PUSH_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gh", "pr", "create"),
    ("gh", "repo", "create"),
)


def _git_push_moves(argv: tuple[str, ...]) -> list[str] | None:
    """The ``-C`` directories of a ``git … push``, in order, or ``None`` when ``argv``
    is not a ``git push`` at all. ``--git-dir``/``--work-tree`` contribute a ``"$"``
    entry, which :func:`kyosei.shell_words.moved` reads as unknown: they send the
    push somewhere this does not follow."""
    walked = _walk_git_push(argv)
    return None if walked is None else walked[0]


def _walk_git_push(argv: tuple[str, ...]) -> tuple[list[str], int] | None:
    """:func:`_git_push_moves`'s answer and the index of the ``push`` word in ``argv``."""
    if not argv or Path(argv[0]).name != "git":
        return None
    moves: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in _GIT_VALUE_OPTIONS:
            if token == "-C" and index + 1 < len(argv):
                moves.append(argv[index + 1])
            elif token in _GIT_ELSEWHERE_OPTIONS:
                moves.append("$")
            index += 2
            continue
        if token.startswith("-"):
            if token.split("=", 1)[0] in _GIT_ELSEWHERE_OPTIONS:
                moves.append("$")
            index += 1
            continue
        return (moves, index) if token == "push" else None
    return None


def _is_git_push(argv: tuple[str, ...]) -> bool:
    return _git_push_moves(argv) is not None


@dataclass(frozen=True)
class Push:
    """One segment of a command line that publishes, and the directory it runs in."""

    segment: str
    #: Where the push runs — the payload's cwd, moved by any ``cd`` ahead of it and by
    #: ``git -C`` — or ``None`` when a move this parse cannot follow made it unknown.
    directory: Path | None
    #: The invocation's words, which say what it would send (KYO-669.c5).
    argv: tuple[str, ...] = ()


def pushes(command: str, *, cwd: Path | None) -> list[Push]:
    """Every segment of ``command`` that would publish a repository, with where.

    :func:`publishing_commands`'s match, read through
    :func:`kyosei.shell_words.walk` so each push carries the directory it would run
    in: ``cd <dir> && git push`` and ``git -C <dir> push`` publish ``<dir>``'s
    repository, not the one the hook happens to belong to (KYO-409.c3).
    """
    found: list[Push] = []
    for step in shell_words.walk(command, cwd):
        argv = step.invocation.argv
        moves = _git_push_moves(argv)
        if moves is not None:
            where = step.cwd
            for target in moves:
                where = shell_words.moved(where, target)
            found.append(Push(step.segment, where, tuple(argv)))
            continue
        for prefix in _IMPLICIT_PUSH_COMMANDS:
            if tuple(argv[: len(prefix)]) == prefix:
                found.append(Push(step.segment, step.cwd, tuple(argv)))
                break
    return found


def publishing_commands(command: str) -> list[str]:
    """Every segment of a shell line that would publish this repository.

    **This match is narrow on purpose, and the misses are the accepted cost.**
    ``git push``, ``git push -u origin main`` and a ``gh pr create`` that pushes
    implicitly are not one pattern, and there is no pattern that catches a
    ``make release`` or a deploy script that pushes three layers down. Matching
    broadly would mean a *blocking* hook firing on commands that publish nothing,
    and a blocking hook that cries wolf is trained away — KYO-33 is this project's
    own record of routine false positives teaching people to stop reading an
    advisory, and KYO-69 is the same shape at scale. So: an unambiguous ``git``
    invocation whose subcommand is ``push`` (leading global options and all), plus
    the two ``gh`` verbs that push as a side effect, and nothing else.

    What "an invocation" means is :mod:`kyosei.shell_words`' reading, shared with
    `kyo check-command` since KYO-409: segments split on ``&`` as well as ``&&``,
    ``||``, ``;``, ``|`` and newlines, ``( … )`` unwrapped, and the command word read
    past ``NAME=value`` prefixes and transparent wrappers (``env``, ``command``,
    ``nohup``, ``time``, ``timeout N``, ``nice``, ``exec``) — so
    ``GIT_TERMINAL_PROMPT=0 git push`` and ``sleep 1 & git push`` are pushes, while
    ``echo "git push"`` and a ``git push`` inside a heredoc body are not.

    The consequence, stated rather than hidden: a push this does not recognise is
    not warned about. The two other things standing behind that gap are the git
    ``pre-push`` hook, which sees every push git itself performs however it was
    invoked, and the split layout being the default since KYO-56, which removes
    the hazard rather than warning about it.
    """
    return [push.segment for push in pushes(command, cwd=None)]


def command_in_pre_tool_use_payload(raw: str) -> str | None:
    """The shell command a ``PreToolUse`` payload describes, if it describes one.

    Returns ``None`` for any payload that is not a ``Bash`` tool call — including
    one this build does not understand. A guard that guessed at an unfamiliar
    payload shape would block tool calls it cannot read, which on a blocking hook
    is the failure mode that matters.
    """
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def pre_tool_use_cwd(raw: str) -> Path | None:
    """The ``cwd`` a `PreToolUse` payload states, resolved, or ``None``.

    Read from the payload rather than from :func:`os.getcwd`, deliberately: the hook
    process inherits whatever directory the harness happens to be in, which is the
    same inherited-environment trap KYO-128 fixed for the stop guards. The payload is
    the harness's own statement about where the tool call will run. Shared by both
    `PreToolUse` guards (KYO-409): `kyo check-command` asks whether that is a running
    worktree, `kyo check-push` which repository a push from there publishes.
    """
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("cwd")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve()
    except OSError:  # pragma: no cover
        return None


# ------------------------------------------- what this repository would publish


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _count_idea_files(store_dir: Path, export_dir: Path) -> int:
    """Every ``ideas/**/*.md``, ``scratchpad.md`` included.

    :func:`kyosei.ideas.idea_files` excludes the scratchpad because nothing sweeps
    or reviews it. That exclusion is exactly backwards here: unreviewed intake is
    the *most* exposed prose in the project, not the least, so it is counted.
    """
    directory = ideas_module.ideas_dir(store_dir)
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*.md") if path.is_file())


def _count_records(store_dir: Path, export_dir: Path) -> int:
    return len(export_module.record_export_files_in(export_dir))


def _count_evidence_artifacts(store_dir: Path, export_dir: Path) -> int:
    total = 0
    for path in export_module.record_export_files_in(export_dir):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict):
            total += len(document.get("evidence_artifacts") or [])
    return total


#: One counter per surface :data:`kyosei.install_prose.COMMITTED_PROSE_SURFACES`
#: declares, keyed by that surface's ``key``.
#:
#: KYO-60.c2 asks for numbers measured at the moment of the push rather than a
#: general description of what a store contains, and this table is the mechanism:
#: every number in the message comes from a call here, against the directory the
#: push would actually publish. ``tests/test_kyo60_push_guard.py`` fails if a
#: surface is declared with no counter or a counter with no surface, so the list
#: and the measurements cannot drift apart (law 3).
SURFACE_COUNTERS: dict[str, Callable[[Path, Path], int]] = {
    "ideas": _count_idea_files,
    "records": _count_records,
    "evidence_artifacts": _count_evidence_artifacts,
    "shift_notes": lambda store, exports: _count_lines(
        ledgers_module.shift_notes_path_in(exports)
    ),
    "person_notes": lambda store, exports: _count_lines(
        ledgers_module.person_notes_path_in(exports)
    ),
    "person_note_tag_changes": lambda store, exports: _count_lines(
        ledgers_module.person_note_tag_changes_path_in(exports)
    ),
    "briefs": lambda store, exports: _count_lines(ledgers_module.briefs_path_in(exports)),
    # KYO-268.c2: the events ledger is a directory of bounded shards, so its count comes
    # from the reader that knows which files it is made of, not from one path.
    "events": lambda store, exports: events_ledger_module.count_event_lines_in(exports),
}


#: Why a push would publish (or would not). Each value is also what ``--json``
#: reports, so a caller can branch on the state without parsing the prose.
STATE_IN_REPO = "in-repo"
STATE_PREDATES_LAYOUT_CONFIG = "predates-the-layout-config"
STATE_SPLIT_WITH_STORE_STILL_HERE = "split-but-the-store-is-still-committed-here"
STATE_SPLIT = "split"
STATE_NO_PROJECT = "no-project"


@dataclass(frozen=True)
class PublicationState:
    """Whether pushing this code repository publishes this project's store."""

    state: str
    publishes: bool
    code_root: Path
    store_root: Path
    #: The directory the counts were measured in — the copy of the store that is
    #: actually inside the code repository, which under
    #: :data:`STATE_SPLIT_WITH_STORE_STILL_HERE` is *not* the live store.
    measured_in: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)
    acknowledged: dict | None = None
    #: Paths under ``kyosei/``/``ideas/`` the code repository still tracks, for the
    #: split-with-residue case. Empty otherwise.
    residue: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        return self.publishes and self.acknowledged is None

    def why(self) -> str:
        if self.state == STATE_IN_REPO:
            return (
                f"this project chose the in-repo layout at `kyo init`, so {KYOSEI_DIR}/ and "
                f"{IDEAS_DIR}/ are committed inside {self.code_root} alongside its code"
            )
        if self.state == STATE_PREDATES_LAYOUT_CONFIG:
            return (
                f"this project predates the layout config — {KYOSEI_DIR}/config.yml carries "
                "no `layout_at_init` key and there is no store link — so no one ever chose "
                f"this; the store is simply inside {self.code_root}, and that is treated as "
                "in-repo here because treating it as anything else would silently exempt "
                "every project initialised before KYO-54, which is all of them"
            )
        if self.state == STATE_SPLIT_WITH_STORE_STILL_HERE:
            return (
                f"this project's live store is at {self.store_root}, outside the code "
                f"repository — but {self.code_root} still tracks {len(self.residue)} file(s) "
                f"under {' or '.join(GUARDED_BRANCH_DIRS)}, so pushing publishes that copy "
                "anyway. The move out was not finished"
            )
        if self.state == STATE_SPLIT:
            return (
                f"this project's store is at {self.store_root}, in its own git repository; "
                f"{self.code_root} carries no store to publish"
            )
        return f"no Kyosei project is configured at or above {self.code_root}"


def _tracked_store_paths(code_root: Path) -> list[str]:
    """Files under ``kyosei/``/``ideas/`` that the code repository tracks.

    Asked of git rather than of the filesystem: a *tracked* file is what a push
    publishes, and an untracked leftover directory is not. Falls back to the
    filesystem when git cannot answer, because "git is unavailable" must not
    silently become "nothing would be published".
    """
    try:
        result = run_git(
            code_root, "ls-files", "--", *[d.rstrip("/") for d in GUARDED_BRANCH_DIRS]
        )
    except OSError:
        result = None
    if result is None or result.returncode != 0:
        return [
            d for d in GUARDED_BRANCH_DIRS if (code_root / d.rstrip("/")).is_dir()
        ]
    return [line for line in result.stdout.splitlines() if line.strip()]


def publication_state(layout: Layout, raw_config: dict | None = None) -> PublicationState:
    """Would pushing ``layout.code_root`` publish this project's store, and why?

    The only consumer of ``layout_at_init`` and ``publication_acknowledged``
    (:data:`kyosei.schema.CONFIG_CONSUMERS`). Three publishing states rather than
    one, because they are three different mistakes with three different remedies,
    and a message that could not tell them apart would be describing a situation
    the consumer is not in (KYO-60.c5, KYO-60.c6).

    Measures :attr:`~kyosei.config.Layout.store_root_in_this_checkout`, never
    :attr:`~kyosei.config.Layout.store_root` (KYO-357). In a linked worktree whose
    own ``kyosei/`` carries no database, the latter is redirected to the *main
    checkout's* store (KYO-342) — right for a read of ``kyosei.db``, and wrong
    here: ``config.yml`` and ``kyosei/export/`` are tracked files, this branch has
    its own checked-out copy, and a push from here publishes *that* copy, on
    *this* branch, not whatever the main checkout happens to have out. Reading
    the main checkout's ``publication_acknowledged`` would answer for the wrong
    tree's acknowledgement, and counting the main checkout's surfaces would
    report numbers this push does not publish (KYO-355 fixed the same
    mismeasurement in ``changelog.py``).
    """
    code_root = layout.code_root
    store_root = layout.store_root_in_this_checkout
    if layout.kind == LAYOUT_UNCONFIGURED:
        return PublicationState(
            STATE_NO_PROJECT, False, code_root=code_root, store_root=store_root
        )

    config = read_raw_config(store_root) if raw_config is None else raw_config
    acknowledged = config.get("publication_acknowledged")
    acknowledged = acknowledged if isinstance(acknowledged, dict) else None
    exports_name = str(config.get("export_dir") or "export")

    if layout.kind == LAYOUT_SPLIT:
        residue = _tracked_store_paths(code_root)
        if not residue:
            return PublicationState(
                STATE_SPLIT,
                False,
                code_root=code_root,
                store_root=store_root,
                acknowledged=acknowledged,
            )
        measured_in = code_root
        state = STATE_SPLIT_WITH_STORE_STILL_HERE
    else:
        residue = []
        measured_in = store_root
        state = (
            STATE_IN_REPO
            if config.get("layout_at_init") is not None
            else STATE_PREDATES_LAYOUT_CONFIG
        )

    exports = measured_in / KYOSEI_DIR / exports_name
    counts = {
        surface.key: SURFACE_COUNTERS[surface.key](measured_in, exports)
        for surface in COMMITTED_PROSE_SURFACES
    }
    return PublicationState(
        state,
        True,
        code_root=code_root,
        store_root=store_root,
        measured_in=measured_in,
        counts=counts,
        acknowledged=acknowledged,
        residue=residue,
    )


# ---------------------------------------------------------------- the message


def _plural(count: int, unit: str) -> str:
    return f"{count:,} {unit}" if count == 1 else f"{count:,} {unit}s"


def warning_lines(state: PublicationState, *, trigger: str | None = None) -> list[str]:
    """What is about to become public, measured, and the two ways out.

    The surfaces and their descriptions come from
    :data:`kyosei.install_prose.COMMITTED_PROSE_SURFACES` — the same list `kyo init`
    prints at the moment the layout is chosen — so the warning at the push and the
    disclosure at init can never describe different things (law 3). Only the
    numbers are new, and every one of them was measured a moment ago.
    """
    headline = (
        f"`{trigger}` would publish this project's Kyosei store"
        if trigger
        else "pushing this repository would publish this project's Kyosei store"
    )
    lines = [f"{headline}: {state.why()}"]
    lines.append(
        f"what becomes public, counted in {state.measured_in} just now (not a general "
        "description — these are this project's actual numbers):"
    )
    for surface in COMMITTED_PROSE_SURFACES:
        count = state.counts.get(surface.key, 0)
        lines.append(f"    {_plural(count, surface.unit)} — {surface.path} — {surface.what}")
    lines.append(
        "`kyo redact` removes prose from the store and from the git-canonical ledgers "
        "going forward; it cannot reach a commit that has already been pushed"
    )
    lines.append(
        "an agent has no standing to make this call on the maintainer's behalf: surface "
        "this to them rather than working around it"
    )
    lines.append(
        "remedy — move the store out of this repository, per "
        "docs/reference/topics/store.md (in Kyosei's own repository), \"Moving an existing "
        f"store out of the code repository\": `mv {KYOSEI_DIR} {IDEAS_DIR} "
        "~/kyosei/<project>/`, commit the removal, `git init` in the store, then "
        "`kyo init --store ~/kyosei/<project>`; this check goes silent as soon as the "
        "code repository stops carrying the store"
    )
    lines.append(
        "or accept it — `kyo check-push --acknowledge` records who and when in "
        f"{KYOSEI_DIR}/config.yml, and nothing asks again"
    )
    return lines


# ------------------------------------------- what the commits being pushed carry

#: ``git push`` options that take their value as the next word when not written with ``=``.
_PUSH_VALUE_OPTIONS = frozenset({"--repo", "--receive-pack", "--exec", "--push-option"})

#: How many offending lines a refusal lists. A first push scans the whole history, and
#: the ``--json`` payload carries every hit whatever this says.
_MAX_HITS_SHOWN = 20


@dataclass(frozen=True)
class CommitScan:
    """What scanning a push's commits found. ``hits`` name a commit and a line, never
    a term; ``note`` says why commits that should have been read were not."""

    hits: list[str] = field(default_factory=list)
    commits: int = 0
    note: str | None = None


def _is_zero(sha: str) -> bool:
    return set(sha) == {"0"}


def _commits_sent(
    root: Path, revisions: list[str], remote: str | None
) -> list[tuple[str, str, str]]:
    """``(sha, author name, message)`` of each commit ``revisions`` reach that ``remote``
    does not already have, as far as its remote-tracking refs say.

    Excluding those refs is what makes a branch with no counterpart on the remote
    cheap and exact: such a push is reported with an all-zero remote sha, and walking
    the tip's whole history would refuse it over commits that were published long ago
    and that this push does not send. A ``remote`` that is not a configured remote (a
    bare URL) has no tracking refs, so everything the revisions reach is read. That
    errs towards reading too much, which is the safe direction here.

    Raises :class:`OSError` when git cannot be run, and :class:`ValueError` when it ran
    and refused.
    """
    if not revisions:
        return []
    known = run_git(root, "remote")
    exclude = (
        [f"--remotes={remote}"]
        if known.returncode == 0 and remote in known.stdout.split()
        else []
    )
    result = run_git(
        root,
        "log",
        "--no-show-signature",
        "-z",
        "--format=%H%x00%an%x00%B",
        *revisions,
        "--not",
        *exclude,
        "--",
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ValueError("git log refused the range")
    fields = result.stdout.split("\0")
    return [(fields[i], fields[i + 1], fields[i + 2]) for i in range(0, len(fields) - 2, 3)]


def _commit_hits(terms: Terms, commits: list[tuple[str, str, str]]) -> list[str]:
    """``<sha> message line <n> (kinds)`` and ``<sha> author name (kinds)``. The sha is
    all that identifies a commit here: its subject may be the line that offends."""
    hits: list[str] = []
    for sha, author, message in commits:
        short = sha[:12]
        kinds = terms.kinds_on_published_line(author)
        if kinds:
            hits.append(f"{short} author name ({', '.join(kinds)})")
        for number, line in enumerate(message.split("\n"), start=1):
            kinds = terms.kinds_on_published_line(line)
            if kinds:
                hits.append(f"{short} message line {number} ({', '.join(kinds)})")
    return hits


def _scan_revisions(
    layout: Layout, root: Path, revisions: list[str], remote: str | None
) -> CommitScan:
    """Match what ``revisions`` send. A failure to read the terms or the commits says
    so and refuses nothing: a guard that cannot answer must not stop a push."""
    try:
        terms = project_terms(layout)
    except (KyoseiError, OSError, UnicodeDecodeError):
        return CommitScan(note="the private terms could not be read; no commit was scanned")
    if terms.empty:
        return CommitScan()
    try:
        commits = _commits_sent(root, revisions, remote)
    except (OSError, ValueError):
        return CommitScan(note="git could not list the commits being pushed; none was scanned")
    return CommitScan(hits=_commit_hits(terms, commits), commits=len(commits))


def scan_pre_push(layout: Layout, ref_lines: str, remote: str) -> CommitScan:
    """Scan what git says it is pushing: one ``<local ref> <local sha> <remote ref>
    <remote sha>`` line per ref, on the hook's stdin.

    A local sha of zeros is a deletion, which sends nothing. A remote sha of zeros is a
    branch the remote lacks, and a remote sha this clone has never fetched (the remote
    moved on) cannot be excluded; for both, :func:`_commits_sent` bounds the walk by
    the remote-tracking refs.
    """
    root = layout.code_root
    revisions: list[str] = []
    for line in ref_lines.splitlines():
        parts = line.split()
        if len(parts) != 4 or _is_zero(parts[1]):
            continue
        revisions.append(parts[1])
        if not _is_zero(parts[3]) and _has_commit(root, parts[3]):
            revisions.append(f"^{parts[3]}")
    return _scan_revisions(layout, root, revisions, remote)


def _has_commit(root: Path, sha: str) -> bool:
    try:
        return run_git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
    except OSError:
        return False


def _default_push_remote(root: Path) -> str | None:
    """The remote a bare ``git push`` from ``root`` would use, by git's own precedence."""

    def config(key: str) -> str | None:
        try:
            result = run_git(root, "config", "--get", key)
        except OSError:
            return None
        return result.stdout.strip() or None if result.returncode == 0 else None

    try:
        head = run_git(root, "symbolic-ref", "--short", "-q", "HEAD")
    except OSError:
        return None
    branch = head.stdout.strip() if head.returncode == 0 else ""
    return (
        (config(f"branch.{branch}.pushRemote") if branch else None)
        or config("remote.pushDefault")
        or (config(f"branch.{branch}.remote") if branch else None)
        or "origin"
    )


def _resolve_commit(root: Path, name: str) -> str | None:
    try:
        result = run_git(root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _what_a_push_sends(argv: tuple[str, ...], root: Path) -> tuple[list[str], str | None]:
    """``(revisions, remote)`` a command line would send, read without the network.

    ``git push --dry-run`` would answer exactly, and is not used because it contacts
    the remote from inside a hook that runs before every push an agent attempts, and
    can stop to ask for credentials. The git hook, which is given the real ref lines,
    stands behind whatever this reading misses: a ``push.default`` of ``matching`` and
    a configured ``remote.<name>.push`` are read as the current branch.
    """
    if Path(argv[0]).name == "gh":
        if tuple(argv[:3]) == ("gh", "repo", "create") and "--push" not in argv:
            return [], None
        return ["HEAD"], _default_push_remote(root)

    walked = _walk_git_push(argv)
    if walked is None:
        return [], None
    words = list(argv[walked[1] + 1 :])
    remote: str | None = None
    selectors: list[str] = []
    positional: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        index += 1
        if word == "--":
            positional.extend(words[index:])
            break
        if word.startswith("--"):
            name, equals, value = word.partition("=")
            if name in ("--delete", "--dry-run"):
                return [], None
            if name in ("--all", "--branches"):
                selectors.append("--branches")
            elif name == "--mirror":
                selectors.extend(["--branches", "--tags"])
            elif name == "--tags":
                selectors.append("--tags")
            elif name in _PUSH_VALUE_OPTIONS:
                if not equals and index < len(words):
                    value = words[index]
                    index += 1
                if name == "--repo":
                    remote = value
        elif word.startswith("-") and len(word) > 1:
            if {"d", "n"} & set(word[1:]):
                return [], None
            if word.endswith("o"):
                index += 1
        else:
            positional.append(word)

    if remote is None and positional:
        remote = positional.pop(0)
    remote = remote or _default_push_remote(root)

    revisions = list(selectors)
    specs = iter(positional)
    for spec in specs:
        source = f"refs/tags/{next(specs, '')}" if spec == "tag" else spec
        source = source.removeprefix("+").split(":", 1)[0]
        sha = _resolve_commit(root, source) if source else None
        if sha:
            revisions.append(sha)
    if not positional and not selectors:
        revisions.append("HEAD")
    return revisions, remote


def scan_pushes(found: list[Push], hook_layout: Layout) -> CommitScan:
    """Scan every push on a command line, each in the project it would publish.

    A push whose directory could not be followed, or that is not in a Kyosei project,
    is not scanned. The store guard fails closed on the first, because a false block
    there costs one acknowledgement; here a false block would be a refusal over
    another repository's commits, and the git hook still reads the real ones.
    """
    hits: list[str] = []
    commits = 0
    notes: list[str] = []
    for push in found:
        target = push_layout(push.directory, hook_layout)
        if push.directory is None or target is None or not target.configured:
            continue
        revisions, remote = _what_a_push_sends(push.argv, push.directory)
        scan = _scan_revisions(target, push.directory, revisions, remote)
        hits.extend(scan.hits)
        commits += scan.commits
        if scan.note:
            notes.append(scan.note)
    return CommitScan(
        hits=list(dict.fromkeys(hits)),
        commits=commits,
        note=next(iter(notes), None),
    )


def private_term_lines(
    hits: list[str], *, trigger: str | None, for_a_person: bool
) -> list[str]:
    """The refusal: which commit, which line, what kind, and how to clear it."""
    headline = f"`{trigger}` would send" if trigger else "this push would send"
    lines = [
        f"{headline} a commit with a private term or home path in its message or author "
        "name (the term is not printed; the terms are this machine's project registry and "
        "this store's private-words.txt):"
    ]
    lines.extend(f"    {hit}" for hit in hits[:_MAX_HITS_SHOWN])
    if len(hits) > _MAX_HITS_SHOWN:
        lines.append(f"    and {len(hits) - _MAX_HITS_SHOWN} more")
    lines.append(
        "remedy — amend the message before pushing: `git commit --amend` for the newest "
        "commit, `git rebase -i <commit>^` for an earlier one, and `--reset-author` or "
        "`--author=<name>` on the amend for an author name; then push again"
    )
    if for_a_person:
        lines.append(
            "`git push --no-verify` is the deliberate way past this, once you have read "
            "the line and it is meant to be public"
        )
    else:
        lines.append(
            "an agent has no standing to publish this: rewrite the message, or surface it "
            "to the maintainer if the line is meant to be public"
        )
    return lines


# ------------------------------------------------------------------ the command


def _acknowledge(ctx: Context, layout: Layout, state: PublicationState) -> Output:
    # KYO-357: the same guard every other store writer goes through since KYO-342
    # (see store.py's Store.transaction and cmd_store.py's
    # _refuse_a_write_from_a_worktree), placed first for the reason store.py gives —
    # a store this process may not write at all is not a store worth asking
    # STATE_NO_PROJECT about. Without it, a linked worktree whose own ``kyosei/``
    # carries no database would write `publication_acknowledged` into the MAIN
    # checkout's config.yml: another working tree, on whatever branch it has out,
    # outside this branch's diff, where `kyo check-branch` cannot see it — and
    # acknowledging a push is the maintainer's call alone (KYO-60), never a
    # dispatched worker's.
    refuse_store_write(
        layout.store_via_main_checkout, layout.code_root, "acknowledge a publication"
    )
    if state.state == STATE_NO_PROJECT:
        raise KyoseiError(
            f"no Kyosei project is configured at or above {state.code_root}, so there is "
            "nothing to acknowledge publishing"
        )
    stamp = {"actor": ctx.actor, "at": now()}
    record_publication_acknowledgement(state.store_root, actor=stamp["actor"], at=stamp["at"])
    return Output(
        [
            f"recorded: {stamp['actor']} accepted at {stamp['at']} that pushing "
            f"{state.code_root} publishes this project's store",
            f"  written to {state.store_root / KYOSEI_DIR / 'config.yml'} as "
            "`publication_acknowledged`, which is committed with the rest of the store — "
            "a colleague who clones this project inherits it, and can see from the actor "
            "that it was not their call",
            "  `kyo check-push` is silent from here on; nothing un-acknowledges it but "
            "editing that key out by hand",
        ],
        {
            "ok": True,
            "command": "check-push",
            "acknowledged": stamp,
            "state": state.state,
            "publishes": state.publishes,
        },
    )


def _git_toplevel(directory: Path) -> Path | None:
    try:
        result = run_git(directory, "rev-parse", "--show-toplevel")
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def push_layout(directory: Path | None, hook_layout: Layout) -> Layout | None:
    """The project a push from ``directory`` would publish; ``None`` when unknown.

    KYO-409.c3. The hook belongs to one project and the push may be of another: a
    scratch repository ``cd``-ed into, a sibling checkout named by ``git -C``. So the
    push's own directory is resolved the way every `kyo` invocation resolves its
    project (:func:`kyosei.config.resolve_layout`, walking up), with one boundary
    that walk does not have: a project found ABOVE the git top level of the push's
    directory is a different repository's, and a push of a repository nested inside
    it publishes none of it. A directory whose project cannot be resolved at all (a
    broken store link) is likewise not judged with anybody's counts.

    The hook's own layout is kept whenever the push resolves to the same code root,
    so ``kyo --root`` and the linked-worktree measurement :func:`publication_state`
    documents behave exactly as they did before this function existed.
    """
    if directory is None:
        return None
    if directory == hook_layout.code_root:
        return hook_layout
    try:
        found = resolve_layout(directory, required=False)
    except KyoseiError:
        return None
    if found.kind == LAYOUT_UNCONFIGURED:
        return found
    toplevel = _git_toplevel(directory) if directory.is_dir() else None
    if toplevel is not None and found.code_root != toplevel and found.code_root in toplevel.parents:
        return Layout(LAYOUT_UNCONFIGURED, store_root=toplevel, code_root=toplevel)
    if found.code_root == hook_layout.code_root:
        return hook_layout
    return found


def cmd_check_push(ctx: Context, args: Namespace, raw: str | None = None) -> Output:
    """``kyo check-push [--pre-tool-use | --advisory | --pre-push REMOTE | --acknowledge]``.

    Bare, it answers the question directly and exits 2 if a push from here would
    publish an unacknowledged store — which is what makes the behaviour testable
    and what a controller can run by hand before deciding anything.

    ``--pre-tool-use`` reads the payload FIRST and returns before measuring anything
    when the command publishes nothing (KYO-409.c1): every Bash call an agent makes
    reaches this hook, and :func:`publication_state` counts every event line and
    parses every record file — measured at 0.25 s and 123 MB on this repository, to
    answer "not a push". When it is a push, the project judged is the one the push's
    own directory belongs to (:func:`push_layout`), not the hook's.

    ``raw`` is the ``--pre-tool-use`` payload when the caller has already read it
    (KYO-422: `kyo pre-tool-use` reads stdin once and hands the same text to both
    PreToolUse guards); omitted, it is read from stdin here.
    """
    layout = ctx.layout or Layout(LAYOUT_UNCONFIGURED, ctx.root, ctx.root)

    if args.acknowledge:
        return _acknowledge(ctx, layout, publication_state(layout))

    trigger: str | None = None
    directory: Path | None = layout.code_root
    scan = CommitScan()
    pre_push = args.pre_push is not None
    # KYO-591: the hook's own directory resolves to no Kyosei project. This guard is
    # not silenced wholesale like the other hook modes (`cmd_check_build.
    # QUIET_OUTSIDE_PROJECT`): a push from here may still publish a Kyosei project
    # named by `git -C` (KYO-409.c3), and that push is still refused. What it stops
    # saying is everything that does not block, so outside a project it prints
    # nothing unless it refuses.
    outside_any_project = bool(args.pre_tool_use) and layout.kind == LAYOUT_UNCONFIGURED
    if args.pre_tool_use:
        if raw is None:
            raw = sys.stdin.read()
        command = command_in_pre_tool_use_payload(raw)
        found = (
            pushes(command, cwd=pre_tool_use_cwd(raw) or layout.code_root) if command else []
        )
        if not found:
            # Every Bash call the agent makes reaches this hook. Anything that is
            # not a push is none of its business, and saying so on each one would
            # bury the one message that matters — nor is it worth measuring a store
            # for, which is why no state is reported here.
            return Output(
                []
                if outside_any_project
                else ["check-push: not a publishing command; nothing checked"],
                {"ok": True, "command": "check-push", "matched": False},
            )
        judged: list[tuple[Push, PublicationState]] = []
        states: dict[Path | None, PublicationState] = {}
        for push in found:
            if push.directory not in states:
                # A directory the parse cannot follow (``cd "$DIR"``, ``git
                # --git-dir=…``) is judged against the hook's own project, as every
                # push was before KYO-409: this guard fails closed, and a false block
                # costs one `--acknowledge`, where a false pass publishes the store.
                target = push_layout(push.directory, layout)
                states[push.directory] = publication_state(
                    target if target is not None else layout
                )
            judged.append((push, states[push.directory]))
        push, state = next(
            ((p, st) for p, st in judged if st.blocks), judged[0]
        )
        trigger = push.segment
        directory = push.directory
        # The store refusal comes first and is complete on its own; there is no use
        # naming a commit in a push that cannot go ahead for another reason.
        if not state.blocks:
            scan = scan_pushes(found, layout)
    else:
        state = publication_state(layout)
        if pre_push:
            # Typed by hand there are no ref lines, and reading a terminal would hang.
            stdin_is_empty = sys.stdin is None or sys.stdin.closed or sys.stdin.isatty()
            scan = scan_pre_push(layout, "" if stdin_is_empty else sys.stdin.read(), args.pre_push)

    data = {
        "ok": not state.blocks and not scan.hits,
        "command": "check-push",
        "matched": trigger is not None,
        "trigger": trigger,
        "directory": str(directory) if directory is not None else None,
        # False when the push's directory could not be followed and the hook's own
        # project was judged in its place (KYO-409, fail closed).
        "directory_followed": trigger is None or directory is not None,
        "state": state.state,
        "publishes": state.publishes,
        "blocks": state.blocks,
        "acknowledged": state.acknowledged,
        "code_root": str(state.code_root),
        "store_root": str(state.store_root),
        "measured_in": str(state.measured_in) if state.measured_in else None,
        "counts": state.counts,
        "residue": state.residue,
        "advisory": bool(args.advisory) or pre_push,
        "commits_scanned": scan.commits,
        # Every hit, where the message lists the first few: none of these names a term.
        "private_term_hits": scan.hits,
    }

    if scan.hits:
        raise GuardRefusal(
            CONTRACT_PUSH_CARRIES_A_PRIVATE_TERM,
            private_term_lines(scan.hits, trigger=trigger, for_a_person=pre_push),
            data=data,
            exit_code=EXIT_PRIVATE_TERM if pre_push else EXIT_CONTRACT,
        )

    notes = [f"check-push: {scan.note}"] if scan.note else []

    if not state.blocks:
        if state.publishes and state.acknowledged:
            reason = (
                f"already acknowledged by {state.acknowledged.get('actor')} at "
                f"{state.acknowledged.get('at')}"
            )
        else:
            reason = state.why()
        if outside_any_project:
            return Output([], data)
        return Output([f"check-push: nothing to warn about — {reason}", *notes], data)

    lines = warning_lines(state, trigger=trigger)
    if args.advisory or pre_push:
        # The human half: say it, and get out of the way. `git push --no-verify`
        # skips this hook entirely, which is the correct escape for someone
        # pushing their own repository.
        return Output(
            [
                "check-push (advisory — this does not block your push):",
                *[f"  {line}" for line in lines],
                *notes,
            ],
            data,
        )
    # KYO-187: the payload is a required keyword argument, so a refusal cannot travel
    # without the state it refused over — and the raise stays a call the
    # remedy-coverage walk can see.
    raise GuardRefusal(CONTRACT_PUSH_PUBLISHES_THE_STORE, lines, data=data)
