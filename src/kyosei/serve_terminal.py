"""``kyo serve``'s terminal routes (KYO-721): a session's pane, drawn and typed into.

Under ``/v1/projects/{project}/sessions/{id}/``:

- ``terminal`` (GET, Server-Sent Events, optionally ``?cols=N&rows=N``) streams the pane: a
  ``ready`` event naming the connection, a ``screen`` event that repaints the whole pane, then
  an ``output`` event for each run of bytes the pane's program writes, and a ``closed`` event
  when the pane or its tmux server has gone;
- ``terminal/input`` (POST, ``{"connection", "data"}``) types ``data`` into the pane, byte for
  byte;
- ``terminal/resize`` (POST, ``{"connection", "cols", "rows"}``) states the size of the
  client's view.

THE DELIBERATE EXCEPTION. Every other route that sends keys reads the screen first and sends
only to an idle or working one (:mod:`kyosei.serve_send`), and never answers a prompt. These
routes send whatever the person types, to whatever the screen shows: that is what answering
the trust prompt, a permission prompt or a menu needs, and what ``/exit`` needs. It is a
larger grant than ``send``, so it has a permission of its own, ``terminal``, and a token
issued to send messages does not hold it.

WHAT STANDS IN FRONT OF A CONNECTION, in order, each refused by a sentence naming its remedy:

1. the machine's ``serve_send: true``, as for a send, since a connection types;
2. a token held by a device, with the ``terminal`` permission;
3. the project's opt-in to reading transcripts and its record of the session, as the read
   routes ask;
4. a pane.

An input or a resize needs the first two again, and names an open connection of the same
device, project and session. Both are read afresh on every request, and an open stream looks
again every few seconds, so clearing the setting or revoking the token ends a connection.

HOW THE PANE REACHES THE CLIENT (measured on tmux 3.7, private sockets, KYO-721.d1). Each
connection is one tmux control-mode client, ``tmux -C attach-session -f ignore-size``, on
the server that holds the pane. Control mode was chosen over the two alternatives measured:

- ``pipe-pane`` carries output only, and a pane has one pipe, so a second viewer, or a pipe
  the person set up, would be displaced;
- a second ordinary client attached under a pty carries tmux's own rendering, status line
  and prefix key, so a viewer could switch to any session of that server, and its size takes
  part in sizing the window.

A control client receives ``%output`` with the bytes the pane's program wrote, colours and
cursor movement included, which a terminal emulator draws as the terminal does. Any number
of control clients may attach, each receiving the same bytes, so a second viewer sees the
same screen. Keys go back through the same client as ``send-keys -H``, which writes each byte
to the pane unchanged; the emulator has already encoded arrows, Escape and pastes as a
terminal would.

THE FIRST SCREEN, AND A LATE JOINER. ``%output`` begins when the client attaches, so what is
already on the screen is read once: the pane's size, cursor and modes by ``display-message``
and its text with colours by ``capture-pane -e``, with up to :data:`HISTORY_LINES` lines of
history above it. tmux queues a pane's output for a control client in the same step that it
draws it, and answers a command in a later one, so every ``%output`` that follows the
capture's answer is newer than the capture and none is drawn twice. The same repaint is sent
whenever the pane's size changes.

SIZE. The pane has one size, and every viewer draws it at that size. The client is attached
with tmux's ``ignore-size`` flag and states the size of its view with ``refresh-client -C``.
tmux leaves such a client out of sizing a window while any client without the flag is
attached, and sizes the window from the flagged clients when they are the only ones. Measured:
with a terminal attached at 120x40, a connection stating 70x12 and typing left the window at
120x39; when the terminal detached, the window became 70x12. A session's own tmux client is
therefore never resized by a viewer, and a session nobody is attached to takes the viewer's
size.

A TWO-WAY CHANNEL OVER A PLAIN HTTP SERVER. The stream is Server-Sent Events, as the other
streams are, and keys are POSTs, as the other writes are, so the guards in front of every
route apply unchanged and the server needs no WebSocket framing. A client sends one POST at
a time, so that keys arrive in the order typed.

THE LOG. Each connection is a row of the send log (:data:`kyosei.serve_send.LOG_FILE`) with
the action ``terminal``: its device, project and session, and the outcome ``opened``,
``refused`` or ``failed``; a second row with the outcome ``closed`` and the reason it ended
is written when it ends. The log is opened first, so a connection that could not be recorded
is not made. What is typed is not logged, since it can be a password; a refused input or
resize is, with the action ``terminal-input`` or ``terminal-resize``.

This module sends keys to the pane it was opened on and to no other, through its own control
client. ``tests/test_kyo_serve_terminal.py`` pins what it reaches.
"""

from __future__ import annotations

import base64
import contextlib
import os
import queue
import re
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from . import device_tokens, serve_send
from .errors import KyoseiError
from .serve import API_PREFIX, Refusal
from .serve_events import KEEPALIVE, frame
from .serve_sessions import ProjectSessions

#: What follows ``sessions/{id}/``: the stream, and the two POST routes beneath it.
ROUTE = "terminal"
INPUT = "input"
RESIZE = "resize"

#: The row's action in the send log, for a connection and for a refused input or resize.
LOG_ACTION = "terminal"
LOG_INPUT = "terminal-input"
LOG_RESIZE = "terminal-resize"

OPENING = b"retry: 1000\n: kyo serve terminal\n\n"

#: How many lines above the screen a repaint carries, for the emulator's scrollback.
HISTORY_LINES = 1000

#: The most one input request may carry, in UTF-8 bytes. A longer paste is sent in parts.
MAX_INPUT_BYTES = 65536

#: How many bytes one ``send-keys -H`` carries.
_BYTES_PER_COMMAND = 256

#: The sizes a client may state. tmux accepts more; a view outside these is a mistake.
MIN_COLS, MAX_COLS = 10, 500
MIN_ROWS, MAX_ROWS = 3, 300

#: How long a tmux command may take to answer before the connection is given up.
COMMAND_SECONDS = 5.0

#: How many events may wait for a slow client before its stream is ended. It reconnects and
#: is sent the screen afresh.
QUEUE_LIMIT = 4096

#: How often a stream with nothing to send checks that its client is still there.
_CLIENT_CHECK_SECONDS = 0.25

#: How often an open stream checks that its device and the machine's setting still allow it.
RECHECK_SECONDS = 5.0

_READER_THREAD = "kyo-serve-terminal-reader"

_PANE_ID = re.compile(r"^%\d+$")
_WINDOW_ID = re.compile(rb"^@\d+$")
_OCTAL = re.compile(rb"\\([0-7]{3})")
_HYPERLINK = b"\x1b]8;"

#: What one ``display-message`` reads of the pane before a repaint, in this order.
_PANE_FORMAT = ",".join(
    f"#{{{name}}}"
    for name in (
        "pane_width",
        "pane_height",
        "cursor_x",
        "cursor_y",
        "cursor_flag",
        "alternate_on",
        "keypad_cursor_flag",
        "keypad_flag",
        "insert_flag",
        "wrap_flag",
        "mouse_standard_flag",
        "mouse_button_flag",
        "mouse_any_flag",
        "mouse_sgr_flag",
        "bracket_paste_flag",
        "scroll_region_upper",
        "scroll_region_lower",
    )
)

Done = Callable[[bool, list[bytes]], None]


class _Ended(Exception):
    """The control client has gone, so a command could not be given to it."""


def repaint(facts: bytes, lines: list[bytes]) -> tuple[int, int, bytes]:
    """``(cols, rows, bytes)``: what an emulator is sent to show a captured pane.

    ``facts`` is one :data:`_PANE_FORMAT` line and ``lines`` a ``capture-pane -p -e``. The
    bytes paint the lines from the top of a cleared screen, so the last ``rows`` of them are
    the visible screen and the others its scrollback, then set the modes the pane's program
    had set and put the cursor where it was.
    """
    numbers = [int(part) if part.isdigit() else 0 for part in facts.strip().split(b",")]
    numbers += [0] * (17 - len(numbers))
    (cols, rows, x, y, cursor, alternate, keypad_cursor, keypad, insert, wrap) = numbers[:10]
    (mouse_standard, mouse_button, mouse_any, mouse_sgr, paste, upper, lower) = numbers[10:17]
    out = [b"\x1b[?1049h" if alternate else b""]
    for index, line in enumerate(lines):
        if index:
            out.append(b"\r\n")
        out.append(line)
        # A capture states each line's styling from the default, so each line ends at it.
        out.append(b"\x1b]8;;\x1b\\\x1b[0m" if _HYPERLINK in line else b"\x1b[0m")
    if rows and (upper, lower) != (0, rows - 1) and lower > upper:
        out.append(b"\x1b[%d;%dr" % (upper + 1, lower + 1))
    out.append(b"\x1b[%d;%dH" % (y + 1, x + 1))
    for on, sequence in (
        (insert, b"\x1b[4h"),
        (not wrap, b"\x1b[?7l"),
        (keypad_cursor, b"\x1b[?1h"),
        (keypad, b"\x1b="),
        (mouse_standard, b"\x1b[?1000h"),
        (mouse_button, b"\x1b[?1002h"),
        (mouse_any, b"\x1b[?1003h"),
        (mouse_sgr, b"\x1b[?1006h"),
        (paste, b"\x1b[?2004h"),
        (not cursor, b"\x1b[?25l"),
    ):
        if on:
            out.append(sequence)
    return cols, rows, b"".join(out)


def unescaped(data: bytes) -> bytes:
    """The bytes of one ``%output`` line: control mode writes ``\\ooo`` for some of them."""
    return _OCTAL.sub(lambda match: bytes([int(match.group(1), 8)]), data)


def _event(name: str, document: dict) -> bytes:
    return frame(name, document)


class _Control:
    """One tmux control-mode client on a pane's session, and the thread that reads it."""

    def __init__(
        self,
        argv: list[str],
        env: dict[str, str],
        pane_id: str,
        emit: Callable[[bytes | None], None],
    ) -> None:
        self.pane = pane_id.encode("ascii")
        self._emit = emit
        self._write_lock = threading.Lock()
        self._pending: list[Done | None] = []
        self._block: tuple[list[bytes], bool, list[bytes]] | None = None
        self._output: list[bytes] = []
        self._window: bytes | None = None
        #: Set once the first screen is queued; no output is sent before it.
        self.painted = threading.Event()
        #: Repaints asked for and not yet answered; output is held back while there is one.
        self._snapshots = 0
        self._counting = threading.Lock()
        self._ended = False
        self.reason = "tmux-exited"
        self.detail = ""
        self._attach_error = b""
        self.process = subprocess.Popen(  # noqa: S603 - a resolved binary, argv built here
            argv,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._read, name=_READER_THREAD, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------------- commands

    def command(self, *lines: str, done: tuple[Done | None, ...] | None = None) -> None:
        """Write command lines in one write, with one callback for each line's answer."""
        callbacks = done if done is not None else (None,) * len(lines)
        with self._write_lock:
            if self._ended:
                raise _Ended()
            self._pending.extend(callbacks)
            try:
                assert self.process.stdin is not None
                self.process.stdin.write("".join(f"{line}\n" for line in lines).encode("utf-8"))
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                del self._pending[-len(callbacks) :]
                raise _Ended() from exc

    def call(self, line: str) -> tuple[bool, list[bytes]]:
        """One command and its answer: whether tmux accepted it, and the lines it printed."""
        answered = threading.Event()
        answer: list[tuple[bool, list[bytes]]] = []

        def done(ok: bool, lines: list[bytes]) -> None:
            answer.append((ok, lines))
            answered.set()

        try:
            self.command(line, done=(done,))
        except _Ended:
            return False, [self._why_ended()]
        if not answered.wait(COMMAND_SECONDS):
            return False, [b"tmux did not answer"]
        return answer[0]

    def snapshot(self) -> None:
        """Ask for the whole screen; the reader queues it as a ``screen`` event."""
        pane = self.pane.decode("ascii")
        facts: list[bytes] = []

        def read(ok: bool, lines: list[bytes]) -> None:
            if ok and lines:
                facts.append(lines[0])

        def captured(ok: bool, lines: list[bytes]) -> None:
            with self._counting:
                self._snapshots -= 1
                newer = self._snapshots > 0
            if not ok or not facts:
                self._gone("pane-closed", b" ".join(lines).decode("utf-8", "replace"))
                return
            if newer:
                return  # A newer one is on its way, and is the one to draw.
            cols, rows, data = repaint(facts[0], lines)
            self._emit(
                _event(
                    "screen",
                    {
                        "cols": cols,
                        "rows": rows,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                )
            )
            self.painted.set()

        with self._counting:
            self._snapshots += 1
        try:
            self.command(
                f"display-message -p -t {pane} '{_PANE_FORMAT}'",
                f"capture-pane -p -e -S -{HISTORY_LINES} -t {pane}",
                done=(read, captured),
            )
        except _Ended:
            with self._counting:
                self._snapshots -= 1

    def identify(self) -> bool:
        """Learn the pane's window, whose layout changes are the pane's resizes."""
        ok, lines = self.call(f"display-message -p -t {self.pane.decode('ascii')} '#{{window_id}}'")
        if ok and lines and _WINDOW_ID.match(lines[0].strip()):
            self._window = lines[0].strip()
            return True
        self.detail = self.detail or b" ".join(lines).decode("utf-8", "replace")
        return False

    # --------------------------------------------------------------------------- reading

    def _read(self) -> None:
        assert self.process.stdout is not None
        descriptor = self.process.stdout.fileno()
        rest = b""
        try:
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                *lines, rest = (rest + chunk).split(b"\n")
                for line in lines:
                    self._line(line)
                self._flush()
        except OSError:
            pass
        finally:
            self._finish()

    def _flush(self) -> None:
        if self._output:
            data = b"".join(self._output)
            self._output = []
            self._emit(_event("output", {"data": base64.b64encode(data).decode("ascii")}))

    def _line(self, line: bytes) -> None:
        if self._block is not None:
            key, own, lines = self._block
            parts = line.split()
            if parts[:1] in ([b"%end"], [b"%error"]) and parts[1:3] == key:
                self._block = None
                self._answered(own, parts[0] == b"%end", lines)
            else:
                lines.append(line)
            return
        if line.startswith(b"%output "):
            _, pane, *data = line.split(b" ", 2)
            # Output read before a repaint is in the repaint, so it is not sent as well.
            if pane == self.pane and self.painted.is_set() and not self._snapshots and data:
                self._output.append(unescaped(data[0]))
            return
        self._flush()
        parts = line.split()
        if parts[:1] == [b"%begin"] and len(parts) >= 3:
            own = len(parts) > 3 and parts[3].isdigit() and bool(int(parts[3]) & 1)
            self._block = (parts[1:3], own, [])
        elif parts[:1] == [b"%layout-change"] and parts[1:2] == [self._window]:
            self.snapshot()
        elif parts[:1] in ([b"%window-close"], [b"%unlinked-window-close"]):
            if parts[1:2] == [self._window]:
                self._gone("pane-closed", "")
        elif parts[:1] == [b"%exit"]:
            # A bare %exit is the session ending under the client; any other names its cause.
            if len(parts) == 1:
                self.reason = "pane-closed"
            self.detail = self.detail or b" ".join(parts[1:]).decode("utf-8", "replace")

    def _answered(self, own: bool, ok: bool, lines: list[bytes]) -> None:
        if not own:
            # The attach itself, which tmux answers as a command of its own.
            if not ok:
                self._attach_error = b" ".join(lines)
            return
        with self._write_lock:
            done = self._pending.pop(0) if self._pending else None
        if done is not None:
            done(ok, lines)

    def _gone(self, reason: str, detail: str) -> None:
        self.reason, self.detail = reason, detail or self.detail
        self.stop()

    def _why_ended(self) -> bytes:
        return (self._attach_error or self.detail.encode("utf-8")) or b"its tmux client has ended"

    def _finish(self) -> None:
        with self._write_lock:
            self._ended = True
            pending, self._pending = self._pending, []
        with contextlib.suppress(OSError, ValueError):
            assert self.process.stderr is not None
            said = self.process.stderr.read().strip()
            if said and not self._attach_error:
                self._attach_error = said
        for done in pending:
            if done is not None:
                done(False, [self._why_ended()])
        self._emit(None)

    def stop(self) -> None:
        """Detach the client. Closing its input is how a control client is told to leave."""
        with self._write_lock:
            self._ended = True
        with contextlib.suppress(OSError, ValueError):
            assert self.process.stdin is not None
            self.process.stdin.close()
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=1.0)

    def attach_error(self) -> str:
        return self._why_ended().decode("utf-8", "replace")


class Connection:
    """One open terminal stream: its device, its control client and its queue of events."""

    def __init__(self, project: str, session_id: str, device: dict) -> None:
        self.id = secrets.token_hex(8)
        self.project = project
        self.session_id = session_id
        self.device = device
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=QUEUE_LIMIT)
        self.overflowed = False
        self.control: _Control | None = None
        #: One input at a time, so two requests' bytes do not interleave.
        self.typing = threading.Lock()
        self.closed = False

    def put(self, chunk: bytes | None) -> None:
        try:
            self.queue.put_nowait(chunk)
        except queue.Full:
            self.overflowed = True


def _failed(session_id: str, why: str) -> Refusal:
    return Refusal(
        502,
        f"the terminal of session {session_id} could not be opened: {why}",
        reason="terminal-failed",
    )


def _size(document: dict, where: str) -> tuple[int, int]:
    cols, rows = document.get("cols"), document.get("rows")
    for name, value, low, high in (
        ("cols", cols, MIN_COLS, MAX_COLS),
        ("rows", rows, MIN_ROWS, MAX_ROWS),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise serve_send.bad_request(
                f'{where} needs "{name}": a whole number from {low} to {high}; the size is '
                f"{MIN_COLS}x{MIN_ROWS} at the least and {MAX_COLS}x{MAX_ROWS} at the most"
            )
    return cols, rows  # type: ignore[return-value]


def size_from_query(pairs: list[tuple[str, str, bool]]) -> tuple[int, int] | None:
    """The view's size from the stream's query string, or None when it gives none."""
    given: dict[str, Any] = {}
    for key, value, _bare in pairs:
        if key not in ("cols", "rows"):
            raise Refusal(
                400,
                f"?{key} is not a query the terminal route takes; it takes ?cols=N&rows=N, the "
                "size of the client's view, and the token goes in `Authorization: Bearer "
                "<token>`",
                reason="bad-request",
            )
        given[key] = int(value) if value.isascii() and value.isdigit() else value
    if not given:
        return None
    return _size(given, "the query string")


class Terminals:
    """Every open terminal connection of one server."""

    def __init__(self, server: Any) -> None:
        self._server = server
        self._lock = threading.Lock()
        self._open: dict[str, Connection] = {}

    @property
    def _environ(self) -> dict[str, str]:
        return self._server.environ

    # ------------------------------------------------------------------------- opening

    def open(
        self,
        project_name: str,
        root_of: Callable[[], str],
        session_id: str,
        authorization: str | None,
        size: Callable[[], tuple[int, int] | None],
    ) -> Connection:
        """Open and log one connection; returns it with its first screen queued, or raises."""
        attempt = serve_send._Attempt(LOG_ACTION, project_name, session_id)
        try:
            descriptor = serve_send._open_log(self._environ)
        except Refusal as refusal:
            raise Refusal(
                refusal.status,
                f"no terminal was opened, because a connection is only made when it can be "
                f"recorded: {refusal}",
                **refusal.extra,
            ) from refusal
        connection: Connection | None = None
        try:
            try:
                connection = self._opened(root_of, session_id, authorization, size, attempt)
            except Refusal as refusal:
                attempt.status = refusal.status
                attempt.reason = refusal.extra.get("reason", "session-route")
                attempt.outcome = "failed" if attempt.reason == "terminal-failed" else "refused"
                raise
            except BaseException:
                attempt.reason = "server-error"
                raise
            attempt.outcome, attempt.status = "opened", 200
        finally:
            try:
                serve_send._append(descriptor, attempt)
            except OSError as exc:
                if connection is not None:
                    self._drop(connection)
                    raise Refusal(
                        500,
                        f"no terminal was opened, because the send log "
                        f"{serve_send.log_path(self._environ)} could not be written: {exc}",
                        reason="send-log-unusable",
                    ) from exc
            finally:
                os.close(descriptor)
        return connection

    def _opened(
        self,
        root_of: Callable[[], str],
        session_id: str,
        authorization: str | None,
        size: Callable[[], tuple[int, int] | None],
        attempt: serve_send._Attempt,
    ) -> Connection:
        """Everything that stands in front of a connection, then the connection."""
        environ = self._environ
        serve_send._require_enabled(environ)
        permission = device_tokens.PERMISSION_TERMINAL
        device = serve_send._device_presented(environ, authorization, permission)
        attempt.device = {"id": device.id, "name": device.name}
        serve_send._require_permission(device, permission)
        wanted = size()
        project = ProjectSessions(root_of(), environ)
        project.require(session_id)
        live = project.open_live(session_id)
        ref = live.pane
        if ref is None:
            raise Refusal(
                409,
                f"session {session_id} has no pane to show ({live.pane_lookup.why}); it must be "
                "running in tmux on this machine",
                reason="no-pane",
            )
        if not _PANE_ID.match(ref.pane_id):
            raise _failed(session_id, f"tmux named its pane {ref.pane_id!r}")
        connection = Connection(attempt.project, session_id, attempt.device)
        try:
            client = live.tmux()
            control = _Control(
                [
                    client.binary,
                    *client.socket_args,
                    "-C",
                    "attach-session",
                    "-f",
                    "ignore-size",
                    "-t",
                    f"={ref.session_name}",
                ],
                client.env,
                ref.pane_id,
                connection.put,
            )
        except (KyoseiError, OSError) as exc:
            raise _failed(session_id, str(exc)) from exc
        connection.control = control
        connection.put(
            _event(
                "ready",
                {
                    "session_id": session_id,
                    "connection": connection.id,
                    "device": connection.device,
                    "pane": {
                        "pane_id": ref.pane_id,
                        "socket": " ".join(ref.socket_args),
                        "tmux_session": ref.session_name,
                    },
                },
            )
        )
        if not control.identify():
            control.stop()
            raise _failed(session_id, control.attach_error())
        if wanted is not None:
            control.call(f"refresh-client -C {wanted[0]}x{wanted[1]}")
        control.snapshot()
        if not control.painted.wait(COMMAND_SECONDS):
            control.stop()
            raise _failed(session_id, control.attach_error())
        with self._lock:
            self._open[connection.id] = connection
        return connection

    # ------------------------------------------------------------------------ streaming

    def serve(
        self,
        connection: Connection,
        write: Callable[[bytes], None],
        stopping: threading.Event,
        client_gone: Callable[[], bool],
        *,
        keepalive_seconds: float,
    ) -> str:
        """Write the connection's events until it ends; returns the reason it ended."""
        idle = 0.0
        checked = time.monotonic()
        try:
            write(OPENING)
            while True:
                if stopping.is_set():
                    return "server-stopped"
                if connection.overflowed:
                    return "client-too-slow"
                if client_gone():
                    return "client-left"
                if time.monotonic() - checked >= RECHECK_SECONDS:
                    checked = time.monotonic()
                    refused = self._no_longer_allowed(connection)
                    if refused is not None:
                        reason = refused.extra.get("reason", "refused")
                        write(_event("closed", {"reason": reason, "text": str(refused)}))
                        return reason
                try:
                    chunk = connection.queue.get(timeout=_CLIENT_CHECK_SECONDS)
                except queue.Empty:
                    idle += _CLIENT_CHECK_SECONDS
                    if idle >= keepalive_seconds:
                        write(KEEPALIVE)
                        idle = 0.0
                    continue
                if chunk is None:
                    control = connection.control
                    reason = control.reason if control is not None else "tmux-exited"
                    detail = control.detail if control is not None else ""
                    text = (
                        "The pane has closed."
                        if reason == "pane-closed"
                        else "The terminal's tmux client has ended."
                    )
                    write(
                        _event(
                            "closed",
                            {"reason": reason, "text": f"{text} {detail}".strip()},
                        )
                    )
                    return reason
                write(chunk)
                idle = 0.0
        except OSError:  # the client went away: a broken pipe or a reset
            return "client-left"

    def _no_longer_allowed(self, connection: Connection) -> Refusal | None:
        """The refusal a new connection of this device would now meet, or None."""
        permission = device_tokens.PERMISSION_TERMINAL
        try:
            serve_send._require_enabled(self._environ)
            try:
                devices = device_tokens.read_devices(device_tokens.tokens_path(self._environ))
            except KyoseiError as exc:
                raise Refusal(500, str(exc), reason="tokens-unreadable") from exc
            for device in devices:
                if device.id == connection.device["id"]:
                    serve_send._require_permission(device, permission)
                    return None
            raise Refusal(
                401,
                f"the device of this connection, {connection.device['name']}, is no longer in "
                "the token file; issue it a token with "
                f"`kyo devices issue <name> --permission {permission}`",
                reason="unknown-token",
            )
        except Refusal as refusal:
            return refusal

    def close(self, connection: Connection, reason: str) -> None:
        """End a connection and record that it ended. The row is written if it can be."""
        if not self._drop(connection):
            return
        attempt = serve_send._Attempt(
            LOG_ACTION,
            connection.project,
            connection.session_id,
            device=connection.device,
            outcome="closed",
            status=200,
            reason=reason,
        )
        self._record(attempt)

    def _drop(self, connection: Connection) -> bool:
        with self._lock:
            known = self._open.pop(connection.id, None) is not None
            first = not connection.closed
            connection.closed = True
        if connection.control is not None:
            connection.control.stop()
        return known and first

    def close_all(self) -> None:
        """Detach every control client, when the server stops."""
        with self._lock:
            connections = list(self._open.values())
        for connection in connections:
            if connection.control is not None:
                connection.control.stop()

    def _record(self, attempt: serve_send._Attempt) -> None:
        try:
            descriptor = serve_send._open_log(self._environ)
        except Refusal:
            return
        try:
            with contextlib.suppress(OSError):
                serve_send._append(descriptor, attempt)
        finally:
            os.close(descriptor)

    # ---------------------------------------------------------------- input and resize

    def _connection(
        self, document: dict, project_name: str, session_id: str, device: device_tokens.Device
    ) -> Connection:
        given = document.get("connection")
        if not isinstance(given, str) or not given:
            raise serve_send.bad_request(
                'the body needs "connection": the id in the `ready` event of this session\'s '
                "terminal stream"
            )
        with self._lock:
            connection = self._open.get(given)
        if (
            connection is None
            or connection.project != project_name
            or connection.session_id != session_id
            or connection.device["id"] != device.id
        ):
            raise Refusal(
                409,
                f"no open terminal connection of device {device.name} to session {session_id} "
                f"has that id; open the stream with GET {API_PREFIX}/projects/{{project}}/"
                f"sessions/{{id}}/{ROUTE} and use the `connection` its `ready` event gives",
                reason="no-connection",
            )
        return connection

    def _request(
        self,
        action: str,
        project_name: str,
        session_id: str,
        authorization: str | None,
        read_body: Callable[[], bytes],
        act: Callable[[Connection, dict], dict],
    ) -> dict:
        body = serve_send.Body(read_body)
        attempt = serve_send._Attempt(action, project_name, session_id)
        try:
            serve_send._require_enabled(self._environ)
            permission = device_tokens.PERMISSION_TERMINAL
            device = serve_send._device_presented(self._environ, authorization, permission)
            attempt.device = {"id": device.id, "name": device.name}
            serve_send._require_permission(device, permission)
            document = serve_send.json_object(body.read())
            connection = self._connection(document, project_name, session_id, device)
            return act(connection, document)
        except Refusal as refusal:
            body.drain()
            attempt.status = refusal.status
            attempt.reason = refusal.extra.get("reason", "session-route")
            attempt.outcome = "failed" if attempt.reason == "terminal-failed" else "refused"
            self._record(attempt)
            raise

    def input(
        self,
        project_name: str,
        session_id: str,
        authorization: str | None,
        read_body: Callable[[], bytes],
    ) -> dict:
        """Type a request's ``data`` into its connection's pane."""
        return self._request(
            LOG_INPUT, project_name, session_id, authorization, read_body, self._type
        )

    def _type(self, connection: Connection, document: dict) -> dict:
        data = document.get("data")
        if not isinstance(data, str) or not data:
            raise serve_send.bad_request('the body needs "data": the text typed, not empty')
        raw = data.encode("utf-8", "replace")
        if len(raw) > MAX_INPUT_BYTES:
            raise Refusal(
                413,
                f'"data" is {len(raw)} bytes, and one input takes at most {MAX_INPUT_BYTES}; '
                "send a longer paste in parts, one request after another",
                reason="input-too-large",
            )
        control = connection.control
        assert control is not None
        pane = control.pane.decode("ascii")
        with connection.typing:
            for start in range(0, len(raw), _BYTES_PER_COMMAND):
                part = raw[start : start + _BYTES_PER_COMMAND]
                keys = " ".join(f"{byte:02x}" for byte in part)
                ok, lines = control.call(f"send-keys -H -t {pane} {keys}")
                if not ok:
                    said = b" ".join(lines).decode("utf-8", "replace")
                    raise Refusal(
                        502,
                        f"typing into session {connection.session_id} did not finish after "
                        f"{start} of {len(raw)} bytes: {said}",
                        reason="terminal-failed",
                    )
        return {"ok": True, "session_id": connection.session_id, "bytes": len(raw)}

    def resize(
        self,
        project_name: str,
        session_id: str,
        authorization: str | None,
        read_body: Callable[[], bytes],
    ) -> dict:
        """State the size of a connection's view. The pane's size arrives as a ``screen``."""
        return self._request(
            LOG_RESIZE, project_name, session_id, authorization, read_body, self._resize
        )

    def _resize(self, connection: Connection, document: dict) -> dict:
        cols, rows = _size(document, "the body")
        control = connection.control
        assert control is not None
        ok, lines = control.call(f"refresh-client -C {cols}x{rows}")
        if not ok:
            said = b" ".join(lines).decode("utf-8", "replace")
            raise Refusal(
                502,
                f"the size of session {connection.session_id}'s terminal was not stated: {said}",
                reason="terminal-failed",
            )
        return {"ok": True, "session_id": connection.session_id, "cols": cols, "rows": rows}
