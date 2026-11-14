"""``kyo serve``'s idle timer: one automated message to a session left idle (KYO-929, KYO-927).

A person who leaves a session for most of an hour usually returns to a cold prompt cache, and
often clears the session, which discards what the agent had not yet written down. The timer
sends the session one message shortly before that cache expires, so that the agent can finish
its closeout while it is still cheap to do so, or, when subagents are still running, so that
their results are read on a warm cache.

THE SETTING. ``serve_idle_message: true`` in ``$KYOSEI_HOME/settings.yml``
(:mod:`kyosei.home_settings`), off by default. It takes effect only where ``serve_send`` is
also true. The server reads it when it starts, to decide whether to run the thread at all,
and at every look, so clearing either setting stops the messages without a restart. Switching
it on needs a restart.

THE LOOK. Every :data:`IDLE_POLL_SECONDS` the timer visits each recorded, unended session of
each project the server serves. A session is sent a message when all of these hold, checked in
this order so that most sessions cost one small file read:

1. the session's prompt cache (:func:`kyosei.cmd_statusline.read_cache`) has an ``expires_at``
   that is in the future and less than :data:`EXPIRY_WINDOW` away;
2. the latest entry of its transcript is at least :data:`IDLE_AFTER` old, so a cache whose
   lifetime is five minutes never starts the timer;
3. the session has a pane, and its screen is idle with an empty input box (a prompt, a dialog,
   a working turn and typed text each prevent it);
4. no message is held for it (:mod:`kyosei.serve_held`);
5. it is not in an idle period already messaged (below).

The message goes through :func:`kyosei.serve_send.deliver_idle`, as typed keys, with a log row
marked ``automatic`` whose reason is ``idle-timer``. The timer sends no slash command and no
interrupt.

ONCE PER IDLE PERIOD. The message makes a turn, which refreshes the cache, which will near
expiry again. The timer therefore sends nothing more to a session until a turn of the
person's, one the send log does not mark automatic, appears in its transcript later than the
last idle-timer row. That state is read from the log and the transcript at each look and kept
nowhere else, so a restart does not forget it.

THE TEXT. One of two messages (:func:`message_text`), chosen by whether the pane's footer
shows a subagent still running. Each begins by saying it was not typed by the person, and the
same sentence is what a client draws it by (:mod:`kyosei.serve_sessions` marks the turn).
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from . import home_settings, serve_send
from .cmd_sessions import STATE_ENDED, read_sessions
from .cmd_statusline import read_cache
from .errors import KyoseiError
from .serve import Refusal
from .serve_sessions import ProjectSessions

#: How often the sessions are looked at.
IDLE_POLL_SECONDS = 30.0

#: A transcript's latest entry must be at least this old.
IDLE_AFTER = timedelta(minutes=30)

#: A cache's ``expires_at`` must lie within this far ahead.
EXPIRY_WINDOW = timedelta(minutes=5)

#: How much of a transcript's end is read to find its latest entry's time.
_TAIL_BYTES = 256 * 1024

_THREAD = "kyo-serve-idle-timer"

#: The sentence every automated message begins with.
AUTOMATED_PREFIX = "Automated message from kyo serve, not typed by the person:"


def _clock_time(moment: datetime) -> str:
    """``H:MM AM`` or ``H:MM PM``, without a leading zero on the hour."""
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def message_text(minutes: int, expires_at: datetime, *, subagents: bool) -> str:
    """The message for a session idle ``minutes`` minutes whose cache expires at ``expires_at``
    (a moment in the zone the reader keeps time in)."""
    if subagents:
        return (
            f"{AUTOMATED_PREFIX} the main chat has had no activity for {minutes} minutes, and "
            "subagents are still running. This message refreshed your prompt cache so that "
            "their results are read on a warm one. Nothing else is asked of you."
        )
    return (
        f"{AUTOMATED_PREFIX} this session has had no activity for {minutes} minutes, and its "
        f"prompt cache expires at {_clock_time(expires_at)}. The person is likely to clear the "
        "session when they return. If you have closeout tasks, such as an interim shift note, "
        "complete them now."
    )


def _stamp(moment: datetime) -> str:
    """A moment as the send log writes one (:func:`kyosei.clock.now`'s shape), so that the
    row carries the time the timer acted on, which a test's clock states."""
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _moment(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def latest_entry_at(path: Path) -> datetime | None:
    """The time of the last record in a transcript that carries one, read from its end."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - _TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return None
    for raw in reversed(tail.split(b"\n")):
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and (moment := _moment(record.get("timestamp"))) is not None:
            return moment
    return None


class IdleTimer:
    """The thread and the look. ``now`` and ``tz`` are replaced by a test."""

    def __init__(
        self,
        server: Any,
        *,
        now: Callable[[], datetime] | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.server = server
        self.environ: dict[str, str] = server.environ
        self.now: Callable[[], datetime] = now or (lambda: datetime.now(UTC))
        #: The zone the cache's expiry is stated in; None is this machine's.
        self.tz = tz
        self._thread: threading.Thread | None = None
        self._complained = False

    # ------------------------------------------------------------------------ the thread

    def start(self) -> None:
        """Run the thread when the machine's settings switch the timer on."""
        try:
            wanted = home_settings.load(self.environ).serve_idle_message
        except KyoseiError as exc:
            self.server.log_stream.write(
                f"kyo serve: the idle timer is not started, because the machine's settings "
                f"could not be read: {exc}\n"
            )
            return
        if wanted and self._thread is None and not self.server.stopping.is_set():
            self._thread = threading.Thread(target=self._run, name=_THREAD, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        stopping: threading.Event = self.server.stopping
        while not stopping.wait(self.server.idle_poll_seconds):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - one bad look must not end the timer
                self.server.log_stream.write(f"kyo serve: the idle timer's look failed: {exc}\n")

    # ------------------------------------------------------------------------- the look

    def enabled(self) -> bool:
        """Whether both settings are on now; settings that cannot be read switch it off."""
        try:
            settings = home_settings.load(self.environ)
        except KyoseiError as exc:
            if not self._complained:
                self._complained = True
                self.server.log_stream.write(
                    f"kyo serve: the idle timer sends nothing, because the machine's settings "
                    f"could not be read: {exc}\n"
                )
            return False
        self._complained = False
        return settings.serve_idle_message and settings.serve_send

    def tick(self) -> list[str]:
        """One look at every session; the ids of those sent a message."""
        if not self.enabled():
            return []
        now = self.now()
        sent: list[str] = []
        for project in self.server.projects():
            try:
                sessions = ProjectSessions(project.root, self.environ)
                entries = read_sessions(sessions.paths.sessions).entries
            except (Refusal, KyoseiError, OSError):
                continue  # not opted in to transcripts, or no sessions recorded
            for entry in entries:
                session_id = entry.get("session_id")
                if not isinstance(session_id, str) or entry.get("state") == STATE_ENDED:
                    continue
                try:
                    if self._look(project.name, project.root, sessions, session_id, now):
                        sent.append(session_id)
                except Exception as exc:  # noqa: BLE001 - one session must not stop the rest
                    self.server.log_stream.write(
                        f"kyo serve: the idle timer could not look at session {session_id}: "
                        f"{exc}\n"
                    )
        return sent

    def _look(
        self,
        project_name: str,
        root: str,
        sessions: ProjectSessions,
        session_id: str,
        now: datetime,
    ) -> bool:
        cache = read_cache(session_id, self.environ)
        expires = _moment(None if cache is None else cache["expires_at"])
        if expires is None or not now < expires < now + EXPIRY_WINDOW:
            return False
        live = sessions.open_live(session_id)
        if live.pane is None:
            return False
        latest = latest_entry_at(live.transcript.path)
        if latest is None or now - latest < IDLE_AFTER:
            return False
        if self._already_messaged(live, session_id):
            return False
        if self.server.held_messages.holds(session_id):
            return False
        screen = live.screen()
        if screen is None or screen.state != serve_send.IDLE_SCREEN or screen.input_text:
            return False
        text = message_text(
            int((now - latest).total_seconds() // 60),
            expires.astimezone(self.tz),
            subagents=bool(screen.background.agents),
        )
        with self.server.held_messages.session_lock(session_id):
            if self.server.held_messages.holds(session_id):
                return False
            outcome = serve_send.deliver_idle(
                self.environ, project_name, session_id, lambda: root, text, at=_stamp(now)
            )
        if outcome.outcome == "failed":
            self.server.log_stream.write(
                f"kyo serve: the idle timer's message to session {session_id} failed "
                f"({outcome.reason}): {outcome.detail}\n"
            )
        if outcome.log_problem:
            self.server.log_stream.write(
                f"kyo serve: the idle timer's message was sent, not logged: {outcome.log_problem}\n"
            )
        return outcome.outcome == "delivered"

    def _already_messaged(self, live: Any, session_id: str) -> bool:
        """Whether the last idle-timer message has had no turn of the person's since.

        A turn counts as the person's when the send log does not record it as an automatic
        send, so the turn the idle message itself made, and any later one the log marks, do not.
        """
        sends = serve_send.automatic_sends(self.environ, session_id)
        timer = [send for send in sends if send.reason == serve_send.IDLE_TIMER]
        if not timer:
            return False
        since = timer[-1].at
        try:
            live.transcript.read()
            turns = live.transcript.turns
        except (OSError, subprocess.SubprocessError):
            return True  # a transcript that cannot be read cannot show the person returned
        for turn in turns:
            at = _moment(turn.timestamp)
            if at is None or at <= since:
                continue
            if not serve_send.is_automatic_turn(sends, turn.timestamp, turn.full_text):
                return False
        return True
