"""KYO-721: `kyo serve`'s terminal routes, the stream of a session's pane and the keys typed in.

Every test runs a real tmux on a PRIVATE server (``tmux -S <tmp>/sock``), never ``-L kyosei``
and never the default server, and ends only that server. The session layer looks for a pane
on ``-L default`` and ``-L kyosei``, so the ``tmux`` on the served project's ``PATH`` is a
script that turns the first into the private socket and refuses the second. The pane runs
``cat -v`` or ``sh``, never Claude Code. Every request goes over a real socket and every
stream is read under a socket timeout. Each error that names a remedy is followed in the
same test by that remedy, and the request is repeated.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import ast
import base64
import fcntl
import http.client
import json
import os
import pty
import shutil
import struct
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path

import pytest

import kyosei
from kyosei import device_tokens, serve_terminal

from .conftest import invoke
from .test_kyo_serve import body_json
from .test_kyo_serve_send import enable, issue, log_rows, post
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    SESSIONS,
    Bed,
    Stream,
    bed,
    reaches_send,
    running,
)

TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(TMUX is None, reason="tmux is not installed")

TERMINAL = f"{SESSIONS}/{OLD}/terminal"
INPUT = f"{TERMINAL}/input"
RESIZE = f"{TERMINAL}/resize"
SCRUBBED = {k: v for k, v in os.environ.items() if not k.startswith("TMUX")}


class Pane:
    """A private tmux server with one session, whose pane the bed's session is said to be in."""

    def __init__(self, bed: Bed, directory: Path) -> None:
        self.bed = bed
        self.socket = directory / "sock"
        self.clients: list[tuple[subprocess.Popen, int]] = []

    def tmux(self, *args: str) -> str:
        done = subprocess.run(
            [TMUX, "-S", str(self.socket), "-f", "/dev/null", *args],
            capture_output=True,
            text=True,
            check=False,
            env={**SCRUBBED, "TERM": "xterm-256color"},
            timeout=10,
        )
        return done.stdout.strip()

    def start(self, program: str, *, cols: int = 100, rows: int = 30) -> None:
        self.tmux("new-session", "-d", "-s", "term", "-x", str(cols), "-y", str(rows), program)
        pid = self.tmux("list-panes", "-t", "=term:", "-F", "#{pane_pid}")
        assert pid.isdigit(), pid
        sessions = self.bed.claude / "sessions"
        for old in sessions.glob("*.json"):
            old.unlink()
        (sessions / f"{pid}.json").write_text(
            json.dumps({"pid": int(pid), "sessionId": OLD, "status": "busy"}), encoding="utf-8"
        )
        wrapper = self.bed.tmux_log.parent / "bin" / "tmux"
        wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$1 $2" = "-L default" ]; then\n'
            "  shift 2\n"
            f'  exec "{TMUX}" -S "{self.socket}" "$@"\n'
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    def size(self) -> str:
        size = "#{window_width}x#{window_height}"
        return self.tmux("display-message", "-p", "-t", "=term:", size)

    def screen(self) -> str:
        return self.tmux("capture-pane", "-p", "-t", "=term:")

    def control_clients(self) -> list[str]:
        rows = self.tmux("list-clients", "-F", "#{client_control_mode} #{client_flags}")
        return [row for row in rows.splitlines() if row.startswith("1 ")]

    def attach_terminal(self, cols: int, rows: int) -> subprocess.Popen:
        """An ordinary tmux client under a pty of the given size, as a person's terminal is."""
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        client = subprocess.Popen(  # noqa: S603 - the resolved tmux
            [TMUX, "-S", str(self.socket), "attach-session", "-t", "=term"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            env={**SCRUBBED, "TERM": "xterm-256color"},
        )
        os.close(slave)

        def drain() -> None:
            try:
                while os.read(master, 65536):
                    pass
            except OSError:
                pass

        threading.Thread(target=drain, daemon=True).start()
        self.clients.append((client, master))
        return client

    def stop(self) -> None:
        for client, master in self.clients:
            client.kill()
            client.wait(timeout=5)
            os.close(master)
        self.tmux("kill-server")


@pytest.fixture
def pane(bed: Bed):
    # Short and under /tmp: a unix socket path is limited to 104 bytes on macOS.
    directory = Path(tempfile.mkdtemp(prefix="kyt.", dir="/tmp"))
    made = Pane(bed, directory)
    yield made
    made.stop()
    shutil.rmtree(directory, ignore_errors=True)


class Terminal(Stream):
    """One open terminal stream."""

    def __init__(self, bed: Bed, token: str | None, query: str = "") -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", bed.client.port, timeout=15)
        self.conn.putrequest("GET", f"{TERMINAL}{query}", skip_host=True)
        self.conn.putheader("Host", f"127.0.0.1:{bed.client.port}")
        if token is not None:
            self.conn.putheader("Authorization", f"Bearer {token}")
        self.conn.endheaders()
        self.response = self.conn.getresponse()
        self.status = self.response.status
        self.refusal = body_json(self.response.read()) if self.status != 200 else None

    def bytes_of(self, name: str) -> bytes:
        return base64.b64decode(self.event(name)["data"])

    def output_until(self, wanted: bytes, *, within: float = 10.0) -> bytes:
        """Output events' bytes, joined, up to the one that completes ``wanted``."""
        seen = b""
        deadline = time.monotonic() + within
        while wanted not in seen:
            assert time.monotonic() < deadline, seen
            seen += self.bytes_of("output")
        return seen


@pytest.fixture
def opened(bed: Bed):
    streams: list[Terminal] = []

    def _open(token: str | None, query: str = "") -> Terminal:
        made = Terminal(bed, token, query)
        streams.append(made)
        return made

    yield _open
    for stream in streams:
        stream.close()


@pytest.fixture
def token(store_home: Path) -> str:
    """Sending enabled, and a token with the terminal permission."""
    enable(store_home)
    return issue("mac", device_tokens.PERMISSION_TERMINAL)


def wait_until(check, *, within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.05)


def ready(stream: Terminal) -> str:
    assert stream.status == 200, stream.refusal
    assert stream.response.headers["Content-Type"].startswith("text/event-stream")
    assert stream.block() == ["retry: 1000", ": kyo serve terminal"]
    return stream.event("ready")["connection"]


# ------------------------------------------------------------ c2: the terminal permission


def test_a_token_with_only_send_is_refused_and_the_remedy_clears_it(
    bed: Bed, pane: Pane, store_home: Path, opened
) -> None:
    pane.start("cat -v")
    enable(store_home)
    sender = issue("ipad", device_tokens.PERMISSION_SEND)

    refused = opened(sender)
    assert refused.status == 403 and refused.refusal["reason"] == "no-terminal-permission"
    assert "--permission terminal" in refused.refusal["error"]
    assert "`kyo devices grant ipad --permission terminal`" in refused.refusal["error"]
    status, typed = post(bed, INPUT, {"connection": "any", "data": "x"}, sender)
    assert status == 403 and typed["reason"] == "no-terminal-permission"
    status, sized = post(bed, RESIZE, {"connection": "any", "cols": 80, "rows": 24}, sender)
    assert status == 403 and sized["reason"] == "no-terminal-permission"
    # No client was attached to the pane's session, and nothing reached the pane.
    assert pane.control_clients() == [] and pane.screen() == ""

    # The remedy the refusal names (KYO-747): grant, and the same token then opens it.
    assert invoke(["devices", "grant", "ipad", "--permission", "terminal"]).code == 0
    holder = sender
    stream = opened(holder)
    connection = ready(stream)
    stream.event("screen")
    assert post(bed, INPUT, {"connection": connection, "data": "x"}, holder)[0] == 200
    wait_until(lambda: pane.screen() == "x")


def test_a_terminal_needs_a_token_and_the_remedy_clears_it(
    bed: Bed, pane: Pane, store_home: Path, opened
) -> None:
    pane.start("cat -v")
    enable(store_home)
    refused = opened(None)
    assert refused.status == 401 and refused.refusal["reason"] == "no-token"
    assert "kyo devices issue <name> --permission terminal" in refused.refusal["error"]
    assert refused.response.headers["WWW-Authenticate"] == "Bearer"
    assert opened("kyo_not-a-token").refusal["reason"] == "unknown-token"
    ready(opened(issue("mac", "terminal")))


def test_a_terminal_is_refused_until_sending_is_on_and_the_setting_clears_it(
    bed: Bed, pane: Pane, store_home: Path, opened
) -> None:
    pane.start("cat -v")
    holder = issue("mac", "terminal")
    refused = opened(holder)
    assert refused.status == 403 and refused.refusal["reason"] == "send-disabled"
    assert "serve_send: true" in refused.refusal["error"]
    assert pane.control_clients() == []
    enable(store_home)
    ready(opened(holder))


def test_the_permission_is_one_kyo_devices_grants() -> None:
    assert device_tokens.PERMISSION_TERMINAL in device_tokens.PERMISSIONS
    assert "terminal" in invoke(["devices", "--help"]).out


# ------------------------------------------------------------------ c2: the pane, streamed


def test_the_stream_paints_the_screen_and_then_carries_what_the_pane_writes(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("sh -c \"printf '\\033[31mred\\033[0m plain\\n'; exec cat -v\"")
    wait_until(lambda: "plain" in pane.screen())
    stream = opened(token)
    ready(stream)
    screen = stream.event("screen")
    assert (screen["cols"], screen["rows"]) == (100, 30)
    painted = base64.b64decode(screen["data"])
    # The colour is carried, and the cursor is put where the pane has it: row 2, column 1.
    assert b"\x1b[31mred" in painted and b"plain" in painted
    assert b"\x1b[2;1H" in painted

    pane.tmux("send-keys", "-t", "=term:", "-l", "typed elsewhere")
    assert b"typed elsewhere" in stream.output_until(b"typed elsewhere")


def test_what_is_typed_reaches_the_pane_byte_for_byte(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("env LC_ALL=C cat -v")
    stream = opened(token)
    connection = ready(stream)
    stream.event("screen")
    # Text beyond ASCII, Escape, an arrow key and Return, as an emulator encodes them.
    typed = "hé \x1b\x1b[A\r"
    status, sent = post(bed, INPUT, {"connection": connection, "data": typed}, token)
    assert status == 200 and sent["bytes"] == len(typed.encode("utf-8"))
    # `cat -v` writes a control character as ^X and a byte above ASCII as M-: the pane was
    # given exactly the bytes of the request.
    wait_until(lambda: pane.screen().splitlines()[-1:] == ["hM-CM-) ^[^[[A"])
    assert b"^[^[[A" in stream.output_until(b"^[^[[A")


def test_a_long_paste_arrives_whole_and_in_order(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    # Not a line at a time: a terminal's line discipline holds no more than 1024 bytes of one.
    pane.start("sh -c 'stty -echo -icanon; exec cat'")
    stream = opened(token)
    connection = ready(stream)
    stream.event("screen")
    text = "".join(f"{index:04d} " for index in range(400))
    assert post(bed, INPUT, {"connection": connection, "data": text}, token)[0] == 200
    wait_until(lambda: "0399" in pane.screen())
    joined = pane.tmux("capture-pane", "-p", "-J", "-S", "-", "-t", "=term:").replace("\n", "")
    assert joined.strip() == text.strip()


def test_ending_the_panes_program_closes_the_stream(
    bed: Bed, pane: Pane, token: str, opened, store_home: Path
) -> None:
    pane.start("sh")
    stream = opened(token)
    connection = ready(stream)
    stream.event("screen")
    assert post(bed, INPUT, {"connection": connection, "data": "exit\r"}, token)[0] == 200
    closed = stream.event("closed")
    assert closed["reason"] == "pane-closed" and "closed" in closed["text"]
    with pytest.raises(EOFError):
        stream.block()
    wait_until(lambda: [row["outcome"] for row in log_rows(store_home)] == ["opened", "closed"])
    assert log_rows(store_home)[-1]["reason"] == "pane-closed"


# ------------------------------------------------------------------ c1: a second viewer


def test_a_late_joiner_is_painted_the_same_screen_and_both_see_what_follows(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("cat -v")
    first = opened(token)
    connection = ready(first)
    first.event("screen")
    assert post(bed, INPUT, {"connection": connection, "data": "before\r"}, token)[0] == 200
    first.output_until(b"before\r\nbefore")

    second = opened(token)
    joined = ready(second)
    assert joined != connection
    assert b"before" in second.bytes_of("screen")
    assert len(pane.control_clients()) == 2

    assert post(bed, INPUT, {"connection": joined, "data": "after"}, token)[0] == 200
    assert b"after" in first.output_until(b"after")
    assert b"after" in second.output_until(b"after")


# ------------------------------------------------------------------------- c5: the size


def test_a_viewer_sizes_a_session_nobody_is_attached_to(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("cat -v")
    assert pane.size() == "100x30"
    stream = opened(token, "?cols=70&rows=12")
    connection = ready(stream)
    wait_until(lambda: pane.size() == "70x12")
    wait_until(lambda: (lambda s: (s["cols"], s["rows"]))(stream.event("screen")) == (70, 12))

    status, sized = post(bed, RESIZE, {"connection": connection, "cols": 90, "rows": 20}, token)
    assert status == 200 and (sized["cols"], sized["rows"]) == (90, 20)
    wait_until(lambda: pane.size() == "90x20")
    # The pane's new size reaches the client as a repaint at that size.
    repainted = stream.event("screen")
    assert (repainted["cols"], repainted["rows"]) == (90, 20)


def test_a_viewer_never_resizes_a_session_its_own_terminal_is_attached_to(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("cat -v")
    terminal = pane.attach_terminal(120, 40)
    # One row of the terminal is tmux's status line.
    wait_until(lambda: pane.size() == "120x39")

    stream = opened(token, "?cols=70&rows=12")
    connection = ready(stream)
    screen = stream.event("screen")
    # The viewer draws the pane at the size its own terminal gave it.
    assert (screen["cols"], screen["rows"]) == (120, 39)
    assert post(bed, RESIZE, {"connection": connection, "cols": 60, "rows": 10}, token)[0] == 200
    assert post(bed, INPUT, {"connection": connection, "data": "typed"}, token)[0] == 200
    wait_until(lambda: "typed" in pane.screen())
    assert pane.size() == "120x39"

    # With the terminal gone, the viewer is the only client, and the window takes its size.
    terminal.kill()
    wait_until(lambda: pane.size() == "60x10")
    wait_until(lambda: (lambda s: (s["cols"], s["rows"]))(stream.event("screen")) == (60, 10))


@pytest.mark.parametrize(
    "document",
    [
        {"cols": 80},
        {"cols": 80, "rows": "24"},
        {"cols": 5, "rows": 24},
        {"cols": 80, "rows": 1000},
        {"cols": True, "rows": 24},
    ],
)
def test_a_size_out_of_range_is_refused_and_one_in_range_clears_it(
    bed: Bed, pane: Pane, token: str, opened, document: dict
) -> None:
    pane.start("cat -v")
    stream = opened(token)
    connection = ready(stream)
    status, refused = post(bed, RESIZE, {"connection": connection, **document}, token)
    assert status == 400 and "a whole number from" in refused["error"]
    assert pane.size() == "100x30"
    assert post(bed, RESIZE, {"connection": connection, "cols": 80, "rows": 24}, token)[0] == 200


def test_the_stream_takes_only_a_size_in_its_query(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("cat -v")
    refused = opened(token, f"?token={token}")
    assert refused.status == 400 and "?cols=N&rows=N" in refused.refusal["error"]
    assert "Authorization: Bearer" in refused.refusal["error"]
    assert opened(token, "?cols=4&rows=24").status == 400
    ready(opened(token, "?cols=80&rows=24"))


# ------------------------------------------------------------- input: what is refused


def test_input_needs_an_open_connection_and_opening_the_stream_clears_it(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("cat -v")
    status, refused = post(bed, INPUT, {"connection": "0123456789abcdef", "data": "x"}, token)
    assert status == 409 and refused["reason"] == "no-connection"
    assert "GET /v1/projects/{project}/sessions/{id}/terminal" in refused["error"]
    assert pane.screen() == ""

    stream = opened(token)
    connection = ready(stream)
    assert post(bed, INPUT, {"connection": connection, "data": "x"}, token)[0] == 200
    wait_until(lambda: pane.screen() == "x")

    # Another device's token does not type through this device's connection.
    other = issue("ipad", "terminal")
    status, refused = post(bed, INPUT, {"connection": connection, "data": "y"}, other)
    assert status == 409 and refused["reason"] == "no-connection"
    # A closed connection is no longer one.
    stream.close()
    wait_until(lambda: pane.control_clients() == [])
    assert post(bed, INPUT, {"connection": connection, "data": "z"}, token)[0] == 409
    assert pane.screen() == "x"


@pytest.mark.parametrize("document", [{}, {"data": ""}, {"data": 7}, {"data": ["x"]}])
def test_a_malformed_input_is_refused(
    bed: Bed, pane: Pane, token: str, opened, document: dict
) -> None:
    pane.start("cat -v")
    connection = ready(opened(token))
    status, refused = post(bed, INPUT, {"connection": connection, **document}, token)
    assert status == 400 and '"data"' in refused["error"]
    assert post(bed, INPUT, {"data": "x"}, token)[1]["reason"] == "bad-request"
    assert pane.screen() == ""


def test_an_input_too_large_is_refused_and_sending_it_in_parts_clears_it(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("sh -c 'stty -echo; exec cat > /dev/null'")
    connection = ready(opened(token))
    whole = "x" * (serve_terminal.MAX_INPUT_BYTES + 1)
    status, refused = post(bed, INPUT, {"connection": connection, "data": whole}, token)
    assert status == 413 and refused["reason"] == "input-too-large"
    assert "in parts" in refused["error"]
    for part in (whole[:4096], whole[4096:8192]):
        assert post(bed, INPUT, {"connection": connection, "data": part}, token)[0] == 200


def test_the_routes_answer_their_own_methods_and_nothing_else_is_beneath_them(
    bed: Bed, pane: Pane, token: str
) -> None:
    pane.start("cat -v")
    status, headers, _ = bed.client.request(TERMINAL, method="POST", body=b"{}", headers={
        "Content-Type": "application/json"})
    assert status == 405 and headers["Allow"] == "GET"
    status, headers, _ = bed.get(INPUT)
    assert status == 405 and headers["Allow"] == "POST"
    assert bed.get(f"{TERMINAL}/keys")[0] == 404
    status, refused = post(bed, f"{INPUT}?token={token}", {"connection": "c", "data": "x"}, token)
    assert status == 400 and "Authorization: Bearer" in refused["error"]


def test_a_session_with_no_pane_is_refused(bed: Bed, pane: Pane, token: str, opened) -> None:
    # The private server holds no session, so the session's process is in no pane.
    pane.start("cat -v")
    pane.tmux("kill-server")
    refused = opened(token)
    assert refused.status == 409 and refused.refusal["reason"] == "no-pane"


# ----------------------------------------------------------------------------- c3: the log


def test_a_connection_is_logged_with_its_device_when_it_opens_and_when_it_ends(
    bed: Bed, pane: Pane, token: str, opened, store_home: Path
) -> None:
    pane.start("cat -v")
    stream = opened(token)
    connection = ready(stream)
    secret = "hunter2-typed-at-a-prompt"
    assert post(bed, INPUT, {"connection": connection, "data": secret}, token)[0] == 200
    (row,) = log_rows(store_home)
    assert row["action"] == "terminal" and row["outcome"] == "opened" and row["status"] == 200
    assert row["device"]["name"] == "mac" and row["project"] == "proj"
    assert row["session_id"] == OLD and row["segments"] is None

    stream.close()
    wait_until(lambda: len(log_rows(store_home)) == 2)
    closed = log_rows(store_home)[1]
    assert closed["action"] == "terminal" and closed["outcome"] == "closed"
    assert closed["reason"] == "client-left" and closed["device"]["name"] == "mac"
    # What was typed is in no row.
    assert secret not in (store_home / "send-log.jsonl").read_text(encoding="utf-8")
    wait_until(lambda: pane.control_clients() == [])


def test_a_refused_connection_and_a_refused_input_are_logged(
    bed: Bed, pane: Pane, store_home: Path, opened
) -> None:
    pane.start("cat -v")
    enable(store_home)
    sender = issue("ipad", "send")
    assert opened(sender).status == 403
    assert post(bed, INPUT, {"connection": "c", "data": "x"}, sender)[0] == 403
    assert opened(None).status == 401
    rows = log_rows(store_home)
    assert [(row["action"], row["outcome"], row["reason"]) for row in rows] == [
        ("terminal", "refused", "no-terminal-permission"),
        ("terminal-input", "refused", "no-terminal-permission"),
        ("terminal", "refused", "no-token"),
    ]
    assert rows[0]["device"]["name"] == "ipad" and rows[2]["device"] is None


def test_an_unusable_log_refuses_the_connection_and_moving_it_clears_it(
    bed: Bed, pane: Pane, token: str, opened, store_home: Path
) -> None:
    pane.start("cat -v")
    log = store_home / "send-log.jsonl"
    log.mkdir()
    refused = opened(token)
    assert refused.status == 500 and refused.refusal["reason"] == "send-log-unusable"
    assert "only made when it can be recorded" in refused.refusal["error"]
    assert pane.control_clients() == []
    log.rmdir()
    ready(opened(token))


# ------------------------------------------------------------- an open stream is rechecked


def test_revoking_the_token_ends_an_open_stream_and_a_new_token_clears_it(
    bed: Bed, pane: Pane, token: str, opened, monkeypatch
) -> None:
    monkeypatch.setattr(serve_terminal, "RECHECK_SECONDS", 0.2)
    pane.start("cat -v")
    stream = opened(token)
    connection = ready(stream)
    assert invoke(["devices", "revoke", "mac"]).code == 0
    closed = stream.event("closed")
    assert closed["reason"] == "revoked-token"
    assert "kyo devices issue <name> --permission terminal" in closed["text"]
    wait_until(lambda: pane.control_clients() == [])
    assert post(bed, INPUT, {"connection": connection, "data": "x"}, token)[0] == 401
    ready(opened(issue("mac", "terminal")))


def test_stopping_the_server_detaches_every_viewer(
    bed: Bed, pane: Pane, token: str, opened
) -> None:
    pane.start("cat -v")
    ready(opened(token))
    ready(opened(token))
    assert len(pane.control_clients()) == 2
    bed.server.terminals.close_all()
    wait_until(lambda: pane.control_clients() == [])
    # The session itself is left running.
    assert pane.tmux("list-sessions", "-F", "#{session_name}") == "term"


# ------------------------------------------------------------------- the pieces, alone


def test_a_repaint_sets_the_modes_the_program_had_set() -> None:
    facts = b"80,24,3,5,0,1,1,0,0,1,0,0,0,1,1,0,23"
    cols, rows, data = serve_terminal.repaint(facts, [b"one", b"\x1b]8;;http://x\x1b\\two"])
    assert (cols, rows) == (80, 24)
    # The alternate screen first, the lines from its top, then the cursor and the modes.
    assert data.startswith(b"\x1b[?1049hone\x1b[0m\r\n")
    assert data.index(b"\x1b[6;4H") > data.index(b"two")
    assert b"\x1b]8;;\x1b\\\x1b[0m" in data
    for sequence in (b"\x1b[?1h", b"\x1b[?1006h", b"\x1b[?2004h", b"\x1b[?25l"):
        assert sequence in data
    for absent in (b"\x1b[?7l", b"\x1b[4h", b"\x1b[?1000h", b"\x1b[1;24r"):
        assert absent not in data
    # A tmux without one of the formats prints nothing for it, which reads as unset.
    assert serve_terminal.repaint(b"80,24,0,0,1,,,,,1", [])[2] == b"\x1b[1;1H"


def test_control_mode_output_is_unescaped() -> None:
    assert serve_terminal.unescaped(rb"a\033[1mb\134\015\012") == b"a\x1b[1mb\\\r\n"
    assert serve_terminal.unescaped("hé".encode()) == "hé".encode()


def test_the_terminal_module_reaches_the_pane_only_through_its_own_client() -> None:
    """The one other ``serve*`` module that sends keys, and how: pinned, so it cannot grow."""
    path = Path(kyosei.__file__).parent / "serve_terminal.py"
    source = path.read_text(encoding="utf-8")
    found = sorted({entry.split(": ", 1)[1] for entry in reaches_send(source, path.name)})
    # `live.tmux()` names the server the pane is on, and `send-keys -H` is the one way keys go.
    assert found == ["'send-keys'", ".tmux"]
    built = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.JoinedStr) and "send-keys" in ast.unparse(node)
    ]
    assert built == ["f'send-keys -H -t {pane} {keys}'"]
