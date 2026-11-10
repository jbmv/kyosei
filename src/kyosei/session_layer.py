"""A live Claude Code session, in Kyosei's terms (KYO-688; chunk E2 of the native-app plan).

This module composes the two adapters, :mod:`kyosei.harness.claude_code_transcript` and
:mod:`kyosei.harness.claude_code_pane`, and speaks in turns, replies, tool calls, screens
and background work. It reads no transcript row and no screen line itself, so a change in a
Claude Code release is repaired in the adapter it concerns.

``kyo serve`` reaches this layer through :mod:`kyosei.serve_sessions` (KYO-689, the read
half of chunk E4). The layer's sending half is three methods of :class:`LiveSession`, and
:mod:`kyosei.serve_send` (KYO-699) is the one ``kyosei.serve*`` module that may call them;
``tests/test_kyo_serve_send.py`` fails if another reaches them.

During a turn the layer reads both sources, because a reply reaches the transcript as one
row when it finishes while the screen changes throughout (spike S4). :class:`LiveReply`
holds the text the screen shows and retires it when the record lands. A session id changes
on ``/clear``, and :meth:`LiveSession.refresh` follows it to the new transcript.

READING A TRANSCRIPT IS OPT-IN PER PROJECT (KYO-656). :func:`open_session` asks
:func:`kyosei.config.transcripts_opted_in` before it opens anything and refuses otherwise.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import threading
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISREG
from urllib.parse import urlsplit

from .cmd_controller import Tmux, controller_env, find_tmux
from .cmd_sessions import recorded_transcript_path
from .cmd_statusline import read_cache, read_limits, read_weekly_at_start
from .config import READ_TRANSCRIPTS_KEY, Config, store_home, transcripts_opted_in
from .errors import KyoseiError
from .harness import claude_code_pane as pane
from .harness import claude_code_transcript as transcript
from .transcript_archive import archive_dir

#: Where screens no state matched are kept for cataloguing, under ``$KYOSEI_HOME``.
UNRECOGNISED_DIRNAME = "pane-unrecognised"

#: How many landed replies are remembered, so that a reply still on the screen is not
#: offered again; more than any one screen holds.
_RETIRED_KEPT = 32

#: How far back the pane is read when the reply in progress has scrolled off the screen.
_SCROLLBACK_LINES = 2000

#: How many sessions' line totals are kept between reads.
_LINE_TOTALS_KEPT = 64

_line_totals: OrderedDict[Path, transcript.SessionLines] = OrderedDict()
_line_totals_lock = threading.Lock()

NOT_OPTED_IN = (
    "this project has not opted in to reading transcripts, so the session layer will not open "
    f"one; to opt in, set `{READ_TRANSCRIPTS_KEY}: true` in kyosei/config.yml"
)


class SessionNotFound(KyoseiError):
    """No transcript exists for the session id given."""


#: The adapter's segment types and its refusal, so that a caller of the sending methods
#: below need not name the adapter.
Typed = pane.Typed
Pasted = pane.Pasted
PaneNotReady = pane.PaneNotReady


@dataclass
class LiveReply:
    """The reply the screen is showing, until its transcript record lands.

    :meth:`observe_screen` takes each capture while a turn runs; :meth:`observe_record`
    takes each reply as the transcript yields it. The screen goes on showing a reply after
    its record has landed, so a text once retired is not offered again.
    """

    text: str | None = None
    _retired: deque[str] = field(default_factory=lambda: deque(maxlen=_RETIRED_KEPT))

    def observe_screen(self, raw: str) -> str | None:
        current = pane.reply_in_progress(raw)
        if current is None or any(pane.same_reply(current, done) for done in self._retired):
            self.text = None
        else:
            self.text = current
        return self.text

    def observe_record(self, record_text: str) -> bool:
        """Retire the text in progress if this record is that reply."""
        if not record_text.strip():
            return False
        if record_text not in self._retired:
            self._retired.append(record_text)
        if self.text is not None and pane.same_reply(self.text, record_text):
            self.text = None
            return True
        return False


@dataclass(frozen=True)
class ContextUse:
    """How much of the context window the latest reply used (KYO-691)."""

    #: The reply's input, cache-read and cache-creation tokens added together.
    tokens: int
    #: None when the window of the reply's model cannot be known.
    window: int | None
    #: Rounded as Claude Code's own panel rounds it; None whenever ``window`` is.
    percent: int | None


@dataclass(frozen=True)
class SessionFacts:
    """What the status strip shows, as of the session's latest reply (KYO-691)."""

    at: str
    model: str | None
    effort: str | None
    git_branch: str | None
    context: ContextUse
    #: As the transcript last recorded it, which may be after the latest reply (KYO-698).
    permission_mode: str | None = None
    #: Across the session and all of its subagents, so it can move without a reply (KYO-701).
    lines: transcript.LinesChanged = transcript.LinesChanged()
    #: The account's usage limits as `kyo statusline` last saved them, read at each build
    #: of the facts; None with no file or no window left (KYO-714).
    limits: dict | None = None
    #: This session's prompt cache as `kyo statusline` last saved it, read at each build of
    #: the facts; None when nothing was saved for it (KYO-715).
    cache: dict | None = None
    #: The weekly window's figure first saved for this session, to measure the session's own
    #: use against; None when none was saved (KYO-717).
    weekly_at_start: dict | None = None


def read_line_totals(path: Path) -> transcript.LinesChanged:
    """The total for the session at ``path``, reading only what was appended since the last.

    Held for the life of the process, because a route opens a new :class:`LiveSession` on
    each request and a session's subagents can be many and large.
    """
    with _line_totals_lock:
        follower = _line_totals.get(path)
        if follower is None:
            follower = _line_totals[path] = transcript.SessionLines(path)
            while len(_line_totals) > _LINE_TOTALS_KEPT:
                _line_totals.popitem(last=False)
        _line_totals.move_to_end(path)
        return follower.read()


def context_use(usage: transcript.Usage, window: int | None) -> ContextUse:
    tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_creation_tokens
    if window is None:
        return ContextUse(tokens, None, None)
    # Integer rounding half up: round() would send an exact half to the even neighbour.
    return ContextUse(tokens, window, min(100, (tokens * 100 + window // 2) // window))


def _joined(
    first: transcript.TranscriptDelta, second: transcript.TranscriptDelta
) -> transcript.TranscriptDelta:
    """Two deltas read one after the other, as the one a single read would have returned."""
    return transcript.TranscriptDelta(
        **{
            part.name: getattr(first, part.name) + getattr(second, part.name)
            for part in dataclasses.fields(first)
        }
    )


@dataclass(frozen=True)
class SessionUpdate:
    """What one :meth:`LiveSession.refresh` learned."""

    session_id: str
    delta: transcript.TranscriptDelta
    screen: pane.Screen | None
    reply_in_progress: str | None
    #: The running background work the footer reports, or None without a pane.
    background: pane.BackgroundWork | None
    #: True when the session moved to a new transcript and id (``/clear``) in this refresh.
    followed_clear: bool
    #: The pane's words, or None without a pane.
    terminal: pane.Terminal | None = None


@dataclass(frozen=True)
class PaneView:
    """One capture of the pane, classified and as text."""

    screen: pane.Screen
    terminal: pane.Terminal


@dataclass(frozen=True)
class UntrackedFiles:
    """Files no commit holds and git does not ignore."""

    files: int
    #: The lines of those it could count.
    lines: int
    #: How many it could not: binary, not a regular file, too large, or past the limit.
    uncounted: int


@dataclass(frozen=True)
class UncommittedChanges:
    """What a working directory holds that no commit does (KYO-693).

    ``added`` and ``removed`` are git's own count of tracked files against ``HEAD``, staged
    or not. ``root`` is None when the directory cannot be read as a repository, and ``why``
    then says so. ``remote`` is the ``origin`` remote as ``owner/name`` (KYO-700).
    """

    cwd: str | None
    root: str | None = None
    why: str | None = None
    added: int = 0
    removed: int = 0
    #: Tracked files that differ, binary ones included.
    files: int = 0
    untracked: UntrackedFiles = UntrackedFiles(0, 0, 0)
    #: None without an ``origin``, or with one that names no repository on a host.
    remote: str | None = None
    #: Commits on HEAD that the branch's upstream lacks; None when it has no upstream (KYO-717).
    ahead: int | None = None


class LiveSession:
    """One session's transcript and, when it is in tmux, its pane."""

    def __init__(
        self,
        session_id: str,
        path: Path,
        *,
        config_dir: Path,
        environ: Mapping[str, str],
        log_dir: Path,
    ) -> None:
        self.session_id = session_id
        self.transcript = transcript.SessionTranscript(path)
        self.config_dir = config_dir
        self.environ = dict(environ)
        self.log_dir = log_dir
        self.live_reply = LiveReply()
        self.pane_lookup = self._find_pane()
        #: Rows a refresh read before its reading of the pane raised, and whether that
        #: refresh followed a ``/clear``; the next refresh returns them (KYO-728).
        self._unreported: tuple[transcript.TranscriptDelta, bool] | None = None

    def _tmux_binary(self) -> str | None:
        return find_tmux(self.environ)

    def _run_tmux(self, socket: tuple[str, ...], *args: str) -> subprocess.CompletedProcess:
        binary = self._tmux_binary()
        if binary is None:
            return subprocess.CompletedProcess([], 1, "", "tmux not found")
        return Tmux(binary, socket, controller_env(self.environ)).run(*args)

    def _find_pane(self) -> pane.PaneLookup:
        return pane.find_pane(self.session_id, self._run_tmux, config=self.config_dir)

    @property
    def pane(self) -> pane.PaneRef | None:
        return self.pane_lookup.pane

    @property
    def process_running(self) -> bool:
        """Whether Claude Code's own registry holds a live process on this session.

        A registry file can outlive a process killed without a chance to remove it, so
        the recorded pid must also still exist.
        """
        for entry in pane.read_registry(self.config_dir):
            if entry.session_id == self.session_id and _pid_exists(entry.pid):
                return True
        return False

    def facts(self) -> SessionFacts | None:
        """The session's model, effort, branch and context use; None before its first reply."""
        latest = self.transcript.latest_facts
        if latest is None:
            return None
        window = transcript.context_window(latest.model, latest.requested_model, self.config_dir)
        return SessionFacts(
            latest.at,
            latest.model,
            latest.effort,
            latest.git_branch,
            context_use(latest.usage, window),
            self.transcript.latest_permission_mode,
            self.lines_changed(),
            read_limits(self.environ),
            read_cache(self.session_id, self.environ),
            read_weekly_at_start(self.session_id, self.environ),
        )

    def lines_changed(self) -> transcript.LinesChanged:
        """The lines this session and its subagents have changed, as of now (KYO-701)."""
        return read_line_totals(self.transcript.path)

    def tmux(self) -> Tmux:
        """A tmux client on the server holding this session's pane."""
        binary = self._tmux_binary()
        ref = self.pane
        if binary is None or ref is None:
            raise KyoseiError(f"session {self.session_id} has no tmux pane to read")
        return Tmux(binary, ref.socket_args, controller_env(self.environ))

    def _unrecognised(self, screen: str) -> None:
        pane.log_unrecognised(screen, self.log_dir)

    def _pane_id(self) -> str:
        ref = self.pane
        if ref is None:
            raise KyoseiError(f"session {self.session_id} has no tmux pane to send to")
        return ref.pane_id

    def send_message(self, segments: list[pane.Typed | pane.Pasted]) -> None:
        """Send a message to this session's pane; refused on a screen not idle or working."""
        pane.send_message(
            self.tmux(), self._pane_id(), segments, log_unrecognised_screen=self._unrecognised
        )

    def send_slash_command(self, command: str) -> None:
        """Send a slash command to this session's pane; refused on a screen not idle."""
        pane.send_slash_command(
            self.tmux(), self._pane_id(), command, log_unrecognised_screen=self._unrecognised
        )

    def send_interrupt(self) -> None:
        """Interrupt this session's running turn; refused on a screen not working."""
        pane.send_interrupt(
            self.tmux(), self._pane_id(), log_unrecognised_screen=self._unrecognised
        )

    @property
    def unreported(self) -> bool:
        """Whether a refresh that raised left rows for the next refresh to return."""
        return self._unreported is not None

    def refresh(self) -> SessionUpdate:
        """Follow any ``/clear``, read new transcript rows, then read the screen.

        The transcript's reader keeps its place, so rows read here are not read again. When
        reading the screen then raises, the rows are kept and the next refresh returns them
        ahead of its own (KYO-728); before that they were lost to the caller.
        """
        followed = self._follow_clear()
        delta = self.transcript.read()
        for reply in delta.replies:
            self.live_reply.observe_record(reply.text)
        if self._unreported is not None:
            kept, was_followed = self._unreported
            self._unreported = None
            if not followed:  # rows of a transcript this session has since left are dropped
                delta = _joined(kept, delta)
                followed = was_followed
        try:
            screen, background, in_progress, words = self._read_pane()
        except BaseException:
            self._unreported = (delta, followed)
            raise
        return SessionUpdate(
            self.session_id, delta, screen, in_progress, background, followed, words
        )

    def _read_pane(
        self,
    ) -> tuple[pane.Screen | None, pane.BackgroundWork | None, str | None, pane.Terminal | None]:
        """The screen, its background work, the reply in progress and the pane's words."""
        screen, background, in_progress, words = None, None, None, None
        raw = self._capture()
        if raw is not None:
            screen = pane.classify(raw, log_unrecognised_screen=self._unrecognised)
            words = pane.terminal(raw, screen)
            background = screen.background
            if screen.state == pane.WORKING:
                raw = self._scrollback(raw)
                in_progress = self.live_reply.observe_screen(raw)
            else:
                self.live_reply.text = None
        return screen, background, in_progress, words

    def screen(self) -> pane.Screen | None:
        """What the pane shows now, without reading the transcript; None with no pane."""
        view = self.view()
        return None if view is None else view.screen

    def view(self) -> PaneView | None:
        """What the pane shows now, classified and as text; None with no pane."""
        raw = self._capture()
        if raw is None:
            return None
        screen = pane.classify(raw, log_unrecognised_screen=self._unrecognised)
        return PaneView(screen, pane.terminal(raw, screen))

    def image(self, turn_id: str, index: int) -> transcript.SessionImage | None:
        """Image ``index`` of the person's turn ``turn_id``, or None when it holds no such image."""
        return self.transcript.image(turn_id, index)

    def subagent(self, agent_id: str) -> transcript.SessionTranscript | None:
        """Subagent ``agent_id``'s transcript, read, or None when this session holds no such agent.

        Raises :class:`OSError` when the file cannot be opened.
        """
        path = transcript.subagent_transcript(self.transcript.path, agent_id)
        if path is None:
            return None
        reader = transcript.SessionTranscript(path, sidechain=True)
        reader.read()
        return reader

    def subagent_model_effort(self, agent_id: str) -> tuple[str | None, str | None]:
        """The model and effort subagent ``agent_id``'s own transcript records (KYO-726).

        None for each that the transcript does not state, or when there is no such file.
        """
        path = transcript.subagent_transcript(self.transcript.path, agent_id)
        if path is None:
            return None, None
        return transcript.subagent_model_effort(path)

    def subagent_dispatch(self, agent_id: str) -> str | None:
        """The id of the dispatch subagent ``agent_id`` was started as a worker for, or None.

        Read from the prompt of the tool call that started it, so an agent this session's
        transcript has not listed yet names none. Raises :class:`OSError`.
        """
        self.transcript.read()
        for ref in self.transcript.subagents:
            if ref.agent_id == agent_id:
                return dispatch_named(ref.prompt)
        return None

    def _capture(self) -> str | None:
        """The pane's screen, or None when there is no pane to read.

        A pane that has gone captures as nothing, which is not a screen: it would classify
        as unrecognised and be logged for cataloguing. The pane is looked up again, so a
        session whose pane has closed reports none from then on.
        """
        ref = self.pane
        if ref is None:
            return None
        raw = pane.capture(self.tmux(), ref.pane_id)
        if raw.strip():
            return raw
        self.pane_lookup = self._find_pane()
        return None

    def _scrollback(self, raw: str) -> str:
        """The capture to read the reply from: the screen, or scrollback when it has gone."""
        if pane.reply_in_progress(raw) is not None:
            return raw
        ref = self.pane
        if ref is None:
            return raw
        return (
            self.tmux()
            .run("capture-pane", "-p", "-e", "-S", f"-{_SCROLLBACK_LINES}", "-t", ref.pane_id)
            .stdout
        )

    def _follow_clear(self) -> bool:
        """Move to the session that ``/clear`` started, if this one has been cleared."""
        target = self._cleared_to()
        if target is None:
            return False
        self.session_id, path = target
        self.transcript = transcript.SessionTranscript(path)
        self.live_reply = LiveReply()
        return True

    def _cleared_to(self) -> tuple[str, Path] | None:
        path = self.transcript.path
        if self.pane is not None:
            current = pane.session_of_process(self.pane.pid, self.config_dir)
            if current is not None:
                # The live process's own record is authoritative, and needs no first prompt.
                beside = path.with_name(f"{current}.jsonl")
                changed = current != self.session_id and beside.is_file()
                return (current, beside) if changed else None
        link = transcript.clear_successor(path)
        return (link.session_id, link.path) if link is not None else None


#: How a dispatched worker's prompt opens. Only its opening counts: a prompt asking another
#: agent to review "the dispatched worker for X" names a dispatch that agent is not working.
_DISPATCH_NAMED = re.compile(
    r"\s*(?:you are )?the dispatched worker for (\S+?\.d\d+)(?!\d)", re.IGNORECASE
)


def dispatch_named(prompt: str) -> str | None:
    """The dispatch id a subagent's prompt starts it as the worker for, or None."""
    found = _DISPATCH_NAMED.match(prompt)
    return found.group(1) if found else None


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists, under another account
        return True
    except OSError:
        return False
    return True


def is_working(screen: pane.Screen) -> bool:
    """Whether a screen shows work running: a turn, or background shells or subagents."""
    return screen.state == pane.WORKING or screen.background.running


def _transcripts_of(
    config: Config, store_root: Path | str, session_id: str, claude: Path, env: Mapping[str, str]
) -> list[Path]:
    """Where ``session_id``'s transcript may be, refused in a project not opted in (KYO-656)."""
    if not transcripts_opted_in(config):
        raise KyoseiError(NOT_OPTED_IN)
    try:
        return transcript.candidate_transcripts(
            session_id,
            config=claude,
            recorded=recorded_transcript_path(store_root, session_id),
            archive=archive_dir(config, env),
        )
    except ValueError as exc:
        raise KyoseiError(str(exc)) from exc


def session_title(
    config: Config,
    store_root: Path | str,
    session_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """The title ``session_id``'s transcript holds, or None; no pane is looked for."""
    env = dict(os.environ if environ is None else environ)
    found = _transcripts_of(config, store_root, session_id, transcript.config_dir(env), env)
    return transcript.session_title(found[0]) if found else None


def open_session(
    config: Config,
    store_root: Path | str,
    session_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> LiveSession:
    """Open ``session_id``, or refuse in a project that has not opted in (KYO-656)."""
    env = dict(os.environ if environ is None else environ)
    claude = transcript.config_dir(env)
    found = _transcripts_of(config, store_root, session_id, claude, env)
    if not found:
        raise SessionNotFound(f"no transcript for session {session_id}")
    return LiveSession(
        session_id,
        found[0],
        config_dir=claude,
        environ=env,
        log_dir=store_home(env) / UNRECOGNISED_DIRNAME,
    )


# ------------------------------------------------------------------ uncommitted changes

#: How long one git command may take before the read is given up.
GIT_SECONDS = 10.0

#: An untracked file past either limit is reported as uncounted rather than read.
UNTRACKED_FILE_BYTES = 16 * 1024 * 1024
UNTRACKED_FILES_READ = 2000

#: Git's own test for a binary file looks for a NUL in this many leading bytes.
_BINARY_PROBE_BYTES = 8000

_GIT_READ_ONLY = (
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.autoRefreshIndex=false",
)

#: These would point git at some other repository than the session's.
_GIT_LOCATION_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


#: The URL schemes of a repository on another machine.
_REMOTE_SCHEMES = frozenset({"http", "https", "ssh", "git", "git+ssh", "ssh+git"})

_REMOTE_SEGMENT = re.compile(r"[\w.-]+")


def _git_environment(environ: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items() if key not in _GIT_LOCATION_VARIABLES}


def _git_reader(binary: str, env: Mapping[str, str]):
    """A function running one read-only git command in a directory."""

    def git(directory: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [binary, *_GIT_READ_ONLY, *args],
            cwd=directory,
            env=env,
            capture_output=True,
            timeout=GIT_SECONDS,
            check=False,
        )

    return git


def uncommitted_changes(
    cwd: str | None, *, environ: Mapping[str, str] | None = None
) -> UncommittedChanges:
    """What the repository holding ``cwd`` has that no commit does, by read-only git commands.

    Tracked files are counted across the whole repository as ``git diff HEAD --numstat``
    counts them, and untracked files stay apart from that figure, in ``untracked``, because
    Claude Code's own diff statistics give an untracked file no lines (the worker report of
    KYO-693.d1 states what was read). ``--no-optional-locks`` keeps git from rewriting the
    index when it takes a lock it could skip, ``diff.autoRefreshIndex`` is off because ``git
    diff`` refreshes the index regardless of that flag, and ``core.fsmonitor`` is off so a
    repository's configuration cannot start a program from here. A failure to read a diff
    raises :class:`KyoseiError`; a remote that cannot be read is only absent.
    """
    env = _git_environment(environ)
    if not cwd:
        return UncommittedChanges(cwd, why="no working directory is recorded for this session")
    where = Path(cwd)
    if not where.is_dir():
        return UncommittedChanges(cwd, why=f"{cwd} is not a directory on this machine")
    binary = shutil.which("git", path=env.get("PATH"))
    if binary is None:
        return UncommittedChanges(cwd, why="git was not found on this server's PATH")
    git = _git_reader(binary, env)
    top = git(where, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        said = top.stderr.decode("utf-8", errors="replace").strip()
        return UncommittedChanges(cwd, why=said or f"{cwd} is not in a git repository")
    root = Path(os.fsdecode(top.stdout).rstrip("\n"))
    has_head = git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0
    # Before the first commit there is no HEAD to compare with, only what has been staged.
    base = "HEAD" if has_head else "--cached"
    diff = git(root, "-c", "diff.relative=false", "diff", base, "--numstat", "--")
    listed = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    for result in (diff, listed):
        if result.returncode != 0:
            said = result.stderr.decode("utf-8", errors="replace").strip()
            raise KyoseiError(f"git could not read {root}: {said}")
    added, removed, files = _numstat(diff.stdout)
    origin = git(root, "remote", "get-url", "origin")
    counted = git(root, "rev-list", "--count", "@{upstream}..HEAD")
    if counted.returncode != 0:
        # A branch pushed with `git push origin <branch>` has no upstream configured, but
        # origin's branch of the same name is what it is ahead of (KYO-717).
        branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        name = branch.stdout.decode("utf-8", errors="replace").strip()
        if branch.returncode == 0 and name:
            counted = git(root, "rev-list", "--count", f"refs/remotes/origin/{name}..HEAD")
    ahead = (
        int(counted.stdout.decode("ascii", errors="replace").strip() or 0)
        if counted.returncode == 0 and counted.stdout.strip().isdigit()
        else None
    )
    return UncommittedChanges(
        cwd,
        str(root),
        None,
        added,
        removed,
        files,
        _untracked(root, listed.stdout),
        repository_name(origin.stdout.decode("utf-8", errors="replace").strip())
        if origin.returncode == 0
        else None,
        ahead,
    )


def repository_name(url: str) -> str | None:
    """The ``owner/name`` a git remote URL points at, or None when it states no such thing.

    Only the path of the URL can reach the result, so the user and password of
    ``https://token@host/owner/name.git`` and any query or fragment are dropped. A password
    holding a ``/`` ends git's reading of the host early and leaves the rest in the path,
    which is why a segment of anything but word characters, dots and hyphens gives None
    rather than being passed on. A host's nested groups stay, as ``group/sub/name``. A local
    path or ``file://`` URL has no owner to state.
    """
    if "://" in url:
        try:
            parts = urlsplit(url)
        except ValueError:
            return None
        if parts.scheme not in _REMOTE_SCHEMES or not parts.netloc:
            return None
        path = parts.path
    else:
        # git reads a colon before any slash as the scp-like form, host:path.
        host, colon, path = url.partition(":")
        if not colon or not host or "/" in host:
            return None
    segments = path.strip("/").split("/")
    if len(segments) < 2:
        return None
    segments[-1] = segments[-1].removesuffix(".git")
    if not all(_REMOTE_SEGMENT.fullmatch(s) and s.strip(".") for s in segments):
        return None
    return "/".join(segments)


def branch_lines(
    root: Path, base: str, branch: str, *, environ: Mapping[str, str] | None = None
) -> tuple[int, int] | None:
    """Lines added and removed between commit ``base`` and the tip of ``branch``, by read-only git.

    None when either is not in the repository at ``root``, as when the branch has been
    deleted. Binary files count no lines. A failure to read the diff raises
    :class:`KyoseiError`.
    """
    env = _git_environment(environ)
    binary = shutil.which("git", path=env.get("PATH"))
    if binary is None:
        return None
    git = _git_reader(binary, env)
    shas: list[str] = []
    for name in (base, f"refs/heads/{branch}"):
        found = git(root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
        if found.returncode != 0:
            return None
        shas.append(found.stdout.decode("ascii").strip())
    diff = git(root, "-c", "diff.relative=false", "diff", "--numstat", *shas, "--")
    if diff.returncode != 0:
        said = diff.stderr.decode("utf-8", errors="replace").strip()
        raise KyoseiError(f"git could not read {root}: {said}")
    added, removed, _files = _numstat(diff.stdout)
    return added, removed


def _numstat(output: bytes) -> tuple[int, int, int]:
    """Lines added, lines removed and files, from ``--numstat``; a binary file has no lines."""
    added, removed, files = 0, 0, 0
    for line in output.splitlines():
        fields = line.split(b"\t", 2)
        if len(fields) != 3:
            continue
        files += 1
        if fields[0].isdigit() and fields[1].isdigit():
            added += int(fields[0])
            removed += int(fields[1])
    return added, removed, files


def _untracked(root: Path, listing: bytes) -> UntrackedFiles:
    names = [name for name in listing.split(b"\0") if name]
    lines, uncounted = 0, 0
    for position, name in enumerate(names):
        counted = None
        if position < UNTRACKED_FILES_READ:
            counted = _lines_in(root / os.fsdecode(name))
        if counted is None:
            uncounted += 1
        else:
            lines += counted
    return UntrackedFiles(len(names), lines, uncounted)


def _lines_in(path: Path) -> int | None:
    """How many lines the text file at ``path`` holds; None for anything that is not one."""
    try:
        stat = path.lstat()
        if not S_ISREG(stat.st_mode) or stat.st_size > UNTRACKED_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:_BINARY_PROBE_BYTES]:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
