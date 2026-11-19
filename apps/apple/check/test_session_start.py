"""KYO-964, KYO-965 and KYO-966 against the test server and the scripted session, without the app.

Each test starts a new project's session through the server's Start route, in a pane of the
run's private tmux server that no client has attached to, as a session the app starts is.
What is asserted is read from the server's routes, the send log and the pane itself.

- KYO-964: the session opens on the trust dialog and records nothing, and the orchestrator
  terminal reaches it by the project's session name and types only what is sent to it.
- KYO-965.c1, the reproduction: the first prompt of such a session, in a pane of tmux's
  default 80x24, once the dialog has been answered there. The prompt is the length of the one
  the maintainer could not send (794 characters), which scrolls the input box at that size.
  With the pane reader before KYO-810 (``fdbcb8399^``) the session never took it; with the
  reader at this base it is delivered.
- KYO-966.c1: ``/resume`` typed in the session's terminal changes the id of the session in
  that pane, and the server says so on the old session's event stream.
"""

from __future__ import annotations

import base64
import http.client
import json
import urllib.parse
from collections.abc import Iterator
from typing import Any

import harness
import pytest
import scripted_session

#: As long as the prompt of the send log's not-submitted row on 2026-11-19 (KYO-965.n1).
FIRST_PROMPT = " ".join(
    f"Sentence {number} of a first prompt to a new session, long enough to scroll its box."
    for number in range(1, 12)
)[:794]
assert len(FIRST_PROMPT) == 794


class Terminal:
    """One orchestrator terminal stream, read event by event."""

    def __init__(self, server: harness.Server) -> None:
        address = urllib.parse.urlsplit(server.url)
        self.server = server
        self.connection = http.client.HTTPConnection(
            address.hostname or "", address.port, timeout=30
        )
        self.connection.request(
            "GET",
            f"/v1/projects/{server.name}/orchestrator/terminal",
            headers={"Authorization": f"Bearer {server.token}"},
        )
        self.response = self.connection.getresponse()
        self.status = self.response.status
        self.refusal = None if self.status == 200 else json.loads(self.response.read())

    def event(self, name: str) -> dict[str, Any]:
        event, data = "", ""
        while True:
            line = self.response.fp.readline()
            if not line:
                raise harness.CheckError(f"the terminal stream ended before a {name} event")
            text = line.decode("utf-8").rstrip("\n")
            if text.startswith("event: "):
                event = text[7:]
            elif text.startswith("data: "):
                data = text[6:]
            elif not text and event:
                if event == name:
                    return json.loads(data)
                event, data = "", ""

    def type(self, connection: str, data: str) -> tuple[int, Any]:
        return self.server.ask(
            "POST", "orchestrator/terminal/input", {"connection": connection, "data": data}
        )

    def close(self) -> None:
        self.connection.close()


def send_log(run: harness.Run) -> list[dict[str, Any]]:
    return [json.loads(line) for line in harness.each_line(run.kyosei_home / "send-log.jsonl")]


def acts(run: harness.Run) -> list[str]:
    """What the session did, in order, leaving out each redrawing of its screen."""
    return [row["event"] for row in run.session_events() if row["event"] != "screen"]


def pane_size(run: harness.Run, session: str) -> str:
    done = run.tmux("display-message", "-p", "-t", f"={session}:", "#{pane_width}x#{pane_height}")
    return done.stdout.strip()


def pane_text(run: harness.Run, session: str) -> str:
    return run.tmux("capture-pane", "-p", "-t", f"={session}:").stdout


@pytest.fixture
def waiting(run: harness.Run) -> Iterator[tuple[harness.Server, dict[str, Any]]]:
    """A new project whose started session waits on the trust dialog."""
    project = harness.make_project(run, "fresh", "NEW")
    harness.commit_all(run, project, "A new project")
    run.use_script({}, trust_prompt=True)
    server = harness.start_server(run, project)
    status, started = server.ask("POST", "orchestrator/start", {})
    assert status == 200, started
    yield server, started
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []


def answer_trust(run: harness.Run, server: harness.Server) -> dict[str, Any]:
    """Answer the dialog in the orchestrator terminal, as the person does, and return the
    session the server then lists."""
    terminal = Terminal(server)
    try:
        assert terminal.status == 200, terminal.refusal
        connection = terminal.event("ready")["connection"]
        terminal.event("screen")
        status, typed = terminal.type(connection, "\r")
        assert status == 200, typed
    finally:
        terminal.close()
    return harness.poll("the server to list the session", server.live_session, deadline=20)


def test_kyo964_the_trust_dialog_is_reached_by_name_and_given_only_what_is_typed(
    run: harness.Run, waiting: tuple[harness.Server, dict[str, Any]]
) -> None:
    server, started = waiting
    assert started["state"] == "waiting-for-trust", started
    # Claude Code on the dialog has run no hook: the project records no session.
    assert server.get("sessions")["sessions"] == []
    terminal = Terminal(server)
    try:
        assert terminal.status == 200, terminal.refusal
        ready = terminal.event("ready")
        assert ready["session_id"] is None and ready["pane"]["tmux_session"] == "NEW"
        painted = base64.b64decode(terminal.event("screen")["data"]).decode("utf-8", "replace")
        assert scripted_session.TRUST_YES in painted
        # Opening the terminal typed nothing: the dialog still stands, and nothing began.
        assert acts(run) == ["trust-prompt"]
        assert scripted_session.TRUST_YES in pane_text(run, "NEW")
        status, typed = terminal.type(ready["connection"], "\r")
        assert status == 200, typed
    finally:
        terminal.close()
    harness.poll("the session to begin", lambda: run.session_events("started"))
    assert acts(run)[:3] == ["trust-prompt", "trusted", "started"]
    row = harness.poll("the server to list the session", server.live_session, deadline=20)
    assert row["session_id"] == run.session_events("started")[0]["session_id"]


def test_kyo965_c1_a_new_sessions_first_prompt_is_delivered_after_the_trust_answer(
    run: harness.Run, waiting: tuple[harness.Server, dict[str, Any]], results
) -> None:
    server, _ = waiting
    row = answer_trust(run, server)
    session = row["session_id"]
    # A session is now made at 160x50 (cmd_controller.START_COLUMNS, START_ROWS). The prompt
    # the maintainer could not send went to one made at tmux's default 80x24, which is
    # restored here: at that size the box shows seven rows and this prompt needs eleven.
    started_at = pane_size(run, "NEW")
    assert started_at == "160x50", started_at
    run.tmux("resize-window", "-t", "=NEW:", "-x", "80", "-y", "24")
    harness.poll("the pane at 80x24", lambda: pane_size(run, "NEW") == "80x24")
    size = pane_size(run, "NEW")
    lines = scripted_session.wrap(FIRST_PROMPT, 78, "❯ ", "  ")
    assert len(lines) > scripted_session.box_rows(24), len(lines)
    status, held = server.ask(
        "POST", f"sessions/{session}/held", {"segments": [{"kind": "typed", "text": FIRST_PROMPT}]}
    )
    assert status == 200, held
    taken = harness.poll("the session to take the prompt", lambda: run.session_events("prompt"),
                         deadline=30)  # fmt: skip
    assert taken[0]["text"] == FIRST_PROMPT
    harness.poll(
        "the scripted reply in the pane",
        lambda: "Scripted reply 1." in pane_text(run, "NEW"),
        deadline=20,
    )
    rows = [(r["action"], r["outcome"], r["reason"]) for r in send_log(run)]
    results.measurements["kyo965_first_prompt"] = {
        "started_at": started_at,
        "pane_size": size,
        "prompt_rows_at_that_size": len(lines),
        "prompt_chars": len(FIRST_PROMPT),
        "box_rows_at_that_size": scripted_session.box_rows(int(size.split("x")[1])),
        "send_log": rows,
    }
    # The terminal's closing row is written when its stream ends, which can follow the hold.
    assert sorted(rows, key=str) == sorted(
        [
            ("orchestrator-start", "started", "waiting-for-trust"),
            ("orchestrator-terminal", "opened", None),
            ("orchestrator-terminal", "closed", "client-left"),
            ("hold", "held", None),
            ("held-delivery", "sent", None),
        ],
        key=str,
    ), rows
    assert not any(row.get("reason") == "not-submitted" for row in send_log(run))


def test_kyo966_c1_resume_changes_the_panes_session_and_the_server_says_so(
    run: harness.Run, waiting: tuple[harness.Server, dict[str, Any]]
) -> None:
    server, _ = waiting
    first = answer_trust(run, server)["session_id"]
    events = server.events(first)
    try:
        harness.poll(
            "the stream's ready event", lambda: [f for f in events.frames if f[1] == "ready"]
        )
        run.tmux("send-keys", "-t", "=NEW:", "-l", "--", "/resume")
        run.tmux("send-keys", "-t", "=NEW:", "Enter")
        resumed = harness.poll("the session to resume", lambda: run.session_events("resumed"))
        second = resumed[0]["session_id"]
        assert second != first and resumed[0]["previous"] == first
        moved = harness.poll(
            "the server's session event",
            lambda: [f for f in events.frames if f[1] == "session"],
            deadline=15,
        )
        assert moved[0][2]["session_id"] == second
        assert moved[0][2]["previous_session_id"] == first
    finally:
        events.close()
    listed = harness.poll(
        "the server to list the resumed session as live",
        lambda: [
            r for r in server.get("sessions")["sessions"] if r["session_id"] == second and r["live"]
        ],
        deadline=15,
    )
    assert listed[0]["has_pane"] is True
    old = [r for r in server.get("sessions")["sessions"] if r["session_id"] == first]
    assert not old or old[0]["live"] is False, old


def test_kyo964_no_orchestrator_session_is_refused_naming_the_start(run: harness.Run) -> None:
    project = harness.make_project(run, "idle", "IDL")
    harness.commit_all(run, project, "A project with no session")
    run.use_script({})
    server = harness.start_server(run, project)
    terminal = Terminal(server)
    try:
        assert terminal.status == 409 and terminal.refusal["reason"] == "no-orchestrator-session"
        assert "kyo orchestrator start" in terminal.refusal["error"]
    finally:
        terminal.close()
    status, _ = server.ask("POST", "orchestrator/start", {})
    assert status == 200
    again = Terminal(server)
    try:
        assert again.status == 200, again.refusal
    finally:
        again.close()
