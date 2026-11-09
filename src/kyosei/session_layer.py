"""A live Claude Code session, in Kyosei's terms (KYO-688; chunk E2 of the native-app plan).

This module composes the two adapters, :mod:`kyosei.harness.claude_code_transcript` and
:mod:`kyosei.harness.claude_code_pane`, and speaks in turns, replies, tool calls, screens
and background work. It reads no transcript row and no screen line itself, so a change in a
Claude Code release is repaired in the adapter it concerns.

``kyo serve`` reaches this layer through :mod:`kyosei.serve_sessions` (KYO-689, the read
half of chunk E4). The layer's sending half is the pane adapter's and is not reachable
from any ``kyosei.serve*`` module; ``tests/test_kyo_serve_sessions.py`` fails if one reaches it.

During a turn the layer reads both sources, because a reply reaches the transcript as one
row when it finishes while the screen changes throughout (spike S4). :class:`LiveReply`
holds the text the screen shows and retires it when the record lands. A session id changes
on ``/clear``, and :meth:`LiveSession.refresh` follows it to the new transcript.

READING A TRANSCRIPT IS OPT-IN PER PROJECT (KYO-656). :func:`open_session` asks
:func:`kyosei.config.transcripts_opted_in` before it opens anything and refuses otherwise.
"""

from __future__ import annotations

import os
import subprocess
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .cmd_controller import Tmux, controller_env, find_tmux
from .cmd_sessions import recorded_transcript_path
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

NOT_OPTED_IN = (
    "this project has not opted in to reading transcripts, so the session layer will not open "
    f"one; to opt in, set `{READ_TRANSCRIPTS_KEY}: true` in kyosei/config.yml"
)


class SessionNotFound(KyoseiError):
    """No transcript exists for the session id given."""


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


def context_use(usage: transcript.Usage, window: int | None) -> ContextUse:
    tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_creation_tokens
    if window is None:
        return ContextUse(tokens, None, None)
    # Integer rounding half up: round() would send an exact half to the even neighbour.
    return ContextUse(tokens, window, min(100, (tokens * 100 + window // 2) // window))


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
        )

    def tmux(self) -> Tmux:
        """A tmux client on the server holding this session's pane."""
        binary = self._tmux_binary()
        ref = self.pane
        if binary is None or ref is None:
            raise KyoseiError(f"session {self.session_id} has no tmux pane to read")
        return Tmux(binary, ref.socket_args, controller_env(self.environ))

    def _unrecognised(self, screen: str) -> None:
        pane.log_unrecognised(screen, self.log_dir)

    def refresh(self) -> SessionUpdate:
        """Follow any ``/clear``, read new transcript rows, then read the screen."""
        followed = self._follow_clear()
        delta = self.transcript.read()
        for reply in delta.replies:
            self.live_reply.observe_record(reply.text)
        screen, background, in_progress = None, None, None
        raw = self._capture()
        if raw is not None:
            screen = pane.classify(raw, log_unrecognised_screen=self._unrecognised)
            background = screen.background
            if screen.state == pane.WORKING:
                raw = self._scrollback(raw)
                in_progress = self.live_reply.observe_screen(raw)
            else:
                self.live_reply.text = None
        return SessionUpdate(self.session_id, delta, screen, in_progress, background, followed)

    def screen(self) -> pane.Screen | None:
        """What the pane shows now, without reading the transcript; None with no pane."""
        raw = self._capture()
        if raw is None:
            return None
        return pane.classify(raw, log_unrecognised_screen=self._unrecognised)

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


def open_session(
    config: Config,
    store_root: Path | str,
    session_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> LiveSession:
    """Open ``session_id``, or refuse in a project that has not opted in (KYO-656)."""
    if not transcripts_opted_in(config):
        raise KyoseiError(NOT_OPTED_IN)
    env = dict(os.environ if environ is None else environ)
    claude = transcript.config_dir(env)
    try:
        found = transcript.candidate_transcripts(
            session_id,
            config=claude,
            recorded=recorded_transcript_path(store_root, session_id),
            archive=archive_dir(config, env),
        )
    except ValueError as exc:
        raise KyoseiError(str(exc)) from exc
    if not found:
        raise SessionNotFound(f"no transcript for session {session_id}")
    return LiveSession(
        session_id,
        found[0],
        config_dir=claude,
        environ=env,
        log_dir=store_home(env) / UNRECOGNISED_DIRNAME,
    )
