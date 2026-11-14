"""KYO-946.c1 to c4: the scripted session behind the test server, without the app.

One session is started for the module, through the server's own Start route, and each test
sends it a prompt that names the turn the test is about (``named`` in the script), so the
tests do not depend on their order. What is asserted is read from three places: the server's
routes, which is what a client sees; the transcript file; and the session's own log of what
it wrote and showed, with the wall clock at each moment (``scripted_session.py``, THE LOG).

The order and timing rules of proposal KYO-942, section 3, each have a test here, named in
its first line.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import harness
import pytest
import scripted_session

COLOUR = {
    "question": "Which colour do you prefer?",
    "header": "Colour",
    "multiSelect": False,
    "options": [
        {"label": "Red", "description": "Choose red."},
        {"label": "Blue", "description": "Choose blue."},
    ],
}

WORKING = "Hatching… (12s · ↓ 3.6k tokens · thought for 3s)"

SCRIPT: dict[str, Any] = {
    "named": {
        "ask with no hook": [
            {"ask": {"questions": [COLOUR], "answer": {"after": 2.5, "choose": "Blue"}}},
            {"say": "Blue it is."},
        ],
        "ask with the hook": [
            {
                "ask": {
                    "questions": [COLOUR],
                    "hook": "held",
                    "answer": {"after": 3.0, "choose": "Red"},
                }
            },
            {"say": "Red it is."},
        ],
        "ask with the hook, written": [
            {
                "ask": {
                    "questions": [COLOUR],
                    "hook": "written",
                    "answer": {"after": 1.0, "choose": "Red"},
                }
            },
        ],
        "ask and wait for keys": [{"ask": {"questions": [COLOUR]}}, {"say": "Answered by keys."}],
        "two tools": [
            {
                "tool": {
                    "name": "Bash",
                    "input": {"command": "echo one"},
                    "result": "one",
                    "seconds": 1.2,
                }
            },
            {
                "tool": {
                    "name": "Read",
                    "input": {"file_path": "notes.md"},
                    "result": "# Notes",
                    "seconds": 0.2,
                }
            },
            {"say": "Both tools ran."},
        ],
        "a partial line": [{"say": "Written in two parts.", "partial": 2.0}],
        "held and not held": [
            {"say": "Written late.", "written_after": 1.5},
            {"say": "Written at once."},
        ],
        "work in the background": [
            {"working": WORKING},
            {"background": {"shells": 2, "agents": [["general-purpose", "Count the files"]]}},
            {"wait": 2.5},
            {"background": {"shells": 0, "agents": []}},
            {"say": "Finished the background work."},
        ],
        "to be interrupted": [{"wait": 30}, {"say": "Never said."}],
    }
}


class Bed:
    def __init__(self, run: harness.Run, server: harness.Server, started: dict[str, Any]) -> None:
        self.run = run
        self.server = server
        self.started = started["started"]
        self.row = started["row"]
        self.id = self.row["session_id"]
        self.transcript = Path(run.session_events("started")[0]["transcript"])

    def send(self, *segments: tuple[str, str] | str) -> tuple[int, Any]:
        body = [
            {"kind": "typed", "text": segment}
            if isinstance(segment, str)
            else {"kind": segment[0], "text": segment[1]}
            for segment in segments
        ]
        return self.server.ask("POST", f"sessions/{self.id}/send", {"segments": body})

    def play(self, prompt: str) -> float:
        """Send a prompt and return the wall clock at which the session took it."""
        before = len(self.run.session_events("prompt"))
        status, answer = self.send(prompt)
        assert status == 200, answer
        taken = harness.poll(
            "the session to take the prompt",
            lambda: self.run.session_events("prompt")[before:],
        )
        assert taken[0]["text"] == prompt
        self.prompt = taken[0]["uuid"]
        return taken[0]["t"]

    def rows_of_the_turn(self) -> list[dict[str, Any]]:
        """The transcript's whole lines after the record of the prompt last played."""
        rows = self.rows()
        at = next(index for index, row in enumerate(rows) if row["uuid"] == self.prompt)
        return rows[at + 1 :]

    def since(self, moment: float, event: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self.run.session_events(event) if row["t"] >= moment]

    def idle_again(self, moment: float, deadline: float = 30.0) -> None:
        """Wait until the turn begun at ``moment`` has ended and its hooks have run."""
        harness.poll(
            "the turn to end",
            lambda: [
                row
                for row in self.since(moment, "hook")
                if row["command"] == "sessions" and row["hook_event"] == "Stop"
            ],
            deadline=deadline,
        )

    def rows(self) -> list[dict[str, Any]]:
        """The transcript's whole lines."""
        text = self.transcript.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.split("\n")[:-1]]

    def turns(self) -> dict[str, Any]:
        return self.server.get(f"sessions/{self.id}/turns")

    def screen(self) -> dict[str, Any]:
        return self.server.get(f"sessions/{self.id}/screen")

    def screen_state(self) -> str | None:
        return (self.screen().get("screen") or {}).get("state")

    def writes(self, moment: float) -> list[dict[str, Any]]:
        return self.since(moment, "write")


@pytest.fixture(scope="module")
def bed(results) -> Iterator[Bed]:
    run = harness.Run()
    try:
        project = harness.make_project(run)
        (project / "notes.md").write_text("# Notes\n", encoding="utf-8")
        harness.commit_all(run, project, "A project for the scripted session")
        run.use_script(SCRIPT)
        server = harness.start_server(run, project)
        began = time.monotonic()
        made = Bed(run, server, server.start_session())
        results.measurements["scripted_session_start_seconds"] = round(time.monotonic() - began, 2)
        yield made
        assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    finally:
        run.stop()


def _moment(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def _frames_naming(events: harness.Events, call_id: str) -> list[tuple[float, str, Any]]:
    return [frame for frame in events.frames if call_id in json.dumps(frame[2])]


# ------------------------------------------------------------------------------ c1


def test_the_start_route_started_the_session_in_the_private_tmux_server(bed: Bed) -> None:
    """KYO-946.c1."""
    run = bed.run
    assert bed.started["state"] == "started" and bed.started["session"] == "CHK"
    # The server asked for `tmux -L kyosei`, and the wrapper sent that to the private socket.
    assert bed.started["socket"] == "tmux -L kyosei"
    creating = [
        call for call in harness.read_tmux_log(run.tmux_log) if " new-session " in call.arguments
    ]
    assert [(call.verdict, call.socket) for call in creating] == [("mapped", str(run.tmux_socket))]
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []

    # The private server holds one pane, whose process is the scripted session and no claude.
    began = run.session_events("started")[0]
    panes = run.tmux("list-panes", "-a", "-F", "#{session_name} #{pane_pid}").stdout.split("\n")
    assert [pane for pane in panes if pane] == [f"CHK {began['pid']}"]
    command = subprocess.run(
        ["/bin/ps", "-o", "command=", "-p", str(began["pid"])],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert str(harness.SCRIPTED_SESSION) in command and "claude" not in command.replace(
        str(run.claude), ""
    )
    record = json.loads((run.claude / "sessions" / f"{began['pid']}.json").read_text("utf-8"))
    assert record["sessionId"] == bed.id == began["session_id"]

    # The server lists it as live, with a pane it can read and a transcript it can read.
    assert bed.row["live"] is True and bed.row["has_pane"] is True
    assert bed.row["state"] == "running" and bed.row["screen_state"] == "idle", bed.row
    screen = bed.screen()
    assert screen["screen"]["state"] == "idle"
    assert "❯" in screen["terminal"]["text"] and "Claude Code" in screen["terminal"]["text"]
    turns = bed.turns()
    assert turns["ok"] is True and turns["session_id"] == bed.id


# ------------------------------------------------------------------------------ c2


def test_a_prompt_sent_through_the_server_is_typed_submitted_and_recorded(bed: Bed) -> None:
    """KYO-946.c2, the server's half: typed lines, a paste, Return, and the record."""
    before = len(bed.run.session_events("prompt"))
    status, answer = bed.send("Line one\nline two, ", ("pasted", "and a paste."))
    assert status == 200 and answer["ok"] is True and answer["action"] == "send", answer
    wanted = "Line one\nline two, and a paste."
    taken = harness.poll("the prompt", lambda: bed.run.session_events("prompt")[before:])[0]
    assert taken["text"] == wanted
    bed.idle_again(taken["t"])

    record = next(row for row in bed.rows() if row["uuid"] == taken["uuid"])
    assert record["type"] == "user" and record["message"]["content"] == wanted
    turns = bed.turns()
    assert turns["readable"] is True
    assert wanted in [turn["text"] for turn in turns["turns"]]
    assert any(reply["text"].startswith("Scripted reply") for reply in turns["replies"])
    # The hook that records a prompt ran, as the plugin's does on UserPromptSubmit.
    hooks = [
        (row["command"], row["hook_event"], row["status"]) for row in bed.since(taken["t"], "hook")
    ]
    assert hooks[0] == ("turns", "UserPromptSubmit", 0)
    assert ("turns", "Stop", 0) in hooks and ("sessions", "Stop", 0) in hooks
    sent = [
        json.loads(line)
        for line in harness.each_line(bed.run.kyosei_home / "send-log.jsonl")
        if '"send"' in line
    ]
    assert sent[-1]["outcome"] == "sent" and [s["kind"] for s in sent[-1]["segments"]] == [
        "typed",
        "pasted",
    ]


# ------------------------------------------------------------------- c3, the four rules


def test_rule_1_a_question_is_written_with_its_result_and_holds_the_screen(bed: Bed) -> None:
    """Rule 1 and KYO-946.c4 with no hook: nothing of the call reaches the transcript or a
    subscriber while the dialog is open, and the call and its result arrive together."""
    events = bed.server.events(bed.id)
    try:
        began = bed.play("ask with no hook")
        harness.poll(
            "the question on the screen", lambda: bed.screen_state() == "ask-user-question"
        )
        shown = bed.since(began, "screen")
        asked_at = next(row["t"] for row in shown if row["shows"] == "ask-user-question")
        # While it is open: the file holds the prompt and no call, and no client is told of one.
        time.sleep(1.0)
        assert bed.screen_state() == "ask-user-question"
        assert bed.rows_of_the_turn() == []
        assert all(
            call["name"] != "AskUserQuestion" or call["at"] < scripted_session.stamp(began - 1)
            for call in bed.turns()["tool_calls"]
        )
        assert not [frame for frame in events.frames if "AskUserQuestion" in json.dumps(frame[2])
                    and frame[0] >= began]  # fmt: skip
        bed.idle_again(began)
        time.sleep(1.2)  # two samples of the stream
    finally:
        events.close()

    answered = bed.since(began, "answered")[0]
    assert answered["answers"] == {COLOUR["question"]: "Blue"}
    # One write holds the call and its result, in that order.
    both = [write for write in bed.writes(began) if len(write["records"]) == 2]
    assert len(both) == 1 and both[0]["part"] == "whole"
    call, result = both[0]["records"]
    assert (call["type"], call["content"]) == ("assistant", ["tool_use"])
    assert (result["type"], result["content"]) == ("user", ["tool_result"])
    # The screen showed the question from the call's moment to the answer, about 2.5 s.
    assert answered["t"] - asked_at >= 2.3
    after = [row for row in bed.since(asked_at, "screen") if row["shows"] != "ask-user-question"]
    assert after[0]["t"] >= answered["t"] - 0.05
    # Rule 4 here: the call is stamped when it was made, and written when it was answered.
    assert both[0]["t"] - _moment(call["timestamp"]) >= 2.3
    assert abs(both[0]["t"] - _moment(result["timestamp"])) < 0.5
    # A subscriber's first frame naming the call holds its result too.
    call_id = answered["call_id"]
    naming = _frames_naming(events, call_id)
    assert naming, "no frame of the stream named the call"
    first = next(c for c in naming[0][2]["tool_calls"] if c["call_id"] == call_id)
    assert naming[0][0] >= answered["t"] and first["result"] is not None
    assert not [frame for frame in naming if '"pending": true' in json.dumps(frame[2])]


def test_with_the_hook_the_open_question_reaches_a_subscriber_first(bed: Bed, results) -> None:
    """KYO-946.c4 with the hook: the question is announced while the dialog is open and the
    transcript does not hold the call; the transcript's record of it follows the answer."""
    events = bed.server.events(bed.id)
    try:
        began = bed.play("ask with the hook")
        hook = harness.poll(
            "the PreToolUse hook",
            lambda: [row for row in bed.since(began, "hook") if row["hook_event"] == "PreToolUse"],
        )[0]
        assert hook["status"] == 0
        pending = harness.poll(
            "a frame announcing the open question",
            lambda: [
                f for f in events.frames if f[0] >= began and '"pending": true' in json.dumps(f[2])
            ],
            deadline=5,
        )
        assert not bed.since(began, "answered"), "the question was answered before it was announced"
        assert bed.rows_of_the_turn() == []
        bed.idle_again(began)
        time.sleep(1.2)
    finally:
        events.close()
    answered = bed.since(began, "answered")[0]
    announced = json.dumps(pending[0][2])
    assert answered["call_id"] in announced and COLOUR["question"] in announced
    assert pending[0][0] < answered["t"]
    # The order of what the session did: the hook, the dialog, the answer, then one write.
    order = [
        (row["event"], row.get("hook_event") or row.get("shows") or len(row.get("records", [])))
        for row in bed.since(began)
        if row["event"] in ("hook", "screen", "answered", "write")
    ]
    start = order.index(("hook", "PreToolUse"))
    assert order[start : start + 4] == [
        ("hook", "PreToolUse"),
        ("screen", "ask-user-question"),
        ("answered", 0),
        ("write", 2),
    ]
    # The first frame that names the call is the announcement, and a later one carries the
    # same call, by its id, from the transcript with its result.
    naming = _frames_naming(events, answered["call_id"])
    assert naming[0] is pending[0]
    settled = [
        call
        for frame in naming
        for call in (frame[2].get("tool_calls") or [] if isinstance(frame[2], dict) else [])
        if call["call_id"] == answered["call_id"] and not call.get("pending")
    ]
    assert settled and settled[-1]["result"] is not None
    results.measurements["question_with_hook"] = {
        "announced_seconds_after_the_hook": round(pending[0][0] - hook["t"], 2),
        "announced_seconds_before_the_answer": round(answered["t"] - pending[0][0], 2),
    }


def test_with_the_hook_the_call_can_be_written_at_once_as_was_measured(bed: Bed) -> None:
    """The measured case of rule 1 with a hook: the call's record is written before the hook
    runs, and its result alone when the question is answered."""
    began = bed.play("ask with the hook, written")
    bed.idle_again(began)
    order = [
        (row["event"], row.get("hook_event") or [r["content"] for r in row.get("records", [])])
        for row in bed.since(began)
        if row["event"] in ("hook", "write") and row.get("hook_event") != "UserPromptSubmit"
    ]
    assert order[0:3] == [
        ("write", [["tool_use"]]),
        ("hook", "PreToolUse"),
        ("write", [["tool_result"]]),
    ]


def test_a_question_is_answered_by_keys_sent_to_the_pane(bed: Bed) -> None:
    """The prompt takes the keys a person presses: Down and Return choose the second option."""
    began = bed.play("ask and wait for keys")
    harness.poll("the question on the screen", lambda: bed.screen_state() == "ask-user-question")
    options = bed.screen()["screen"]["options"]
    assert [option["label"] for option in options][:2] == ["Red", "Blue"]
    assert bed.screen()["screen"]["question"] == COLOUR["question"]
    # The server sends no key to a prompt (KYO-568): a send is refused and nothing is typed.
    status, refusal = bed.send("not for a prompt")
    assert status != 200, refusal
    bed.run.tmux("send-keys", "-t", "=CHK:", "Down", "Enter")
    bed.idle_again(began)
    assert bed.since(began, "answered")[0]["answers"] == {COLOUR["question"]: "Blue"}
    assert not bed.since(began, "prompt")[1:]


def test_rule_2_a_result_is_never_written_before_its_call(bed: Bed) -> None:
    """Rule 2: a script cannot name a row, so cannot order a result first; and a played tool's
    call is in the file, and served as running, before its result is written."""
    for refused in (
        {"turns": [[{"result": {"tool_use_id": "toolu_1", "content": "early"}}]]},
        {"turns": [[{"record": {"type": "user"}}]]},
        {"turns": [[{"say": "two", "tool": {"name": "Bash"}}]]},
        {"turns": [[{"tool": {"name": "AskUserQuestion", "input": {}}}]]},
        {"turns": [[{"working": "no ellipsis"}]]},
        {"turns": [[{"ask": {"questions": [COLOUR], "hook": "sometimes"}}]]},
        {"named": {"x": [{"wait": -1}]}},
        {"rows": []},
    ):
        with pytest.raises(scripted_session.ScriptError):
            scripted_session.check_script(refused)
    assert scripted_session.check_script(SCRIPT)[1].keys() == SCRIPT["named"].keys()

    began = bed.play("two tools")
    running = harness.poll(
        "the first call served with no result",
        lambda: [
            call for call in bed.turns()["tool_calls"]
            if call["name"] == "Bash" and call["result"] is None
        ],
    )  # fmt: skip
    assert running[0]["input"] == {"command": "echo one"}
    assert harness.poll("the screen to be working", lambda: bed.screen_state() == "working")
    bed.idle_again(began)
    seen: set[str] = set()
    results = 0
    for row in bed.rows():
        content = row.get("message", {}).get("content")
        for part in content if isinstance(content, list) else []:
            if part.get("type") == "tool_use":
                seen.add(part["id"])
            if part.get("type") == "tool_result":
                assert part["tool_use_id"] in seen, "a result is in the file before its call"
                results += 1
    assert results >= 2
    kinds = [write["records"][0]["content"] for write in bed.writes(began)]
    assert kinds == [["tool_use"], ["tool_result"], ["tool_use"], ["tool_result"], ["text"]]
    by_id = {call["input"].get("command", call["input"].get("file_path")): call
             for call in bed.turns()["tool_calls"]}  # fmt: skip
    assert by_id["echo one"]["result"] is not None and by_id["notes.md"]["result"] is not None


def test_rule_3_a_line_written_in_part_is_waited_for(bed: Bed) -> None:
    """Rule 3: for two seconds the file ends inside a line; the server serves no part of the
    reply and no error, and serves the reply once the line is whole."""
    began = bed.play("a partial line")
    first = harness.poll(
        "the first part of the write",
        lambda: [write for write in bed.writes(began) if write.get("part") == "first"],
    )[0]
    text = bed.transcript.read_text(encoding="utf-8")
    assert not text.endswith("\n")
    with pytest.raises(ValueError):
        json.loads(text.split("\n")[-1])
    during = bed.turns()
    assert during["ok"] is True and during["readable"] is True
    assert "Written in two parts." not in [reply["text"] for reply in during["replies"]]
    bed.idle_again(began)
    second = next(write for write in bed.writes(began) if write.get("part") == "second")
    assert second["t"] - first["t"] >= 1.9
    assert bed.transcript.read_text(encoding="utf-8").endswith("\n")
    assert "Written in two parts." in [reply["text"] for reply in bed.turns()["replies"]]


def test_rule_4_a_record_is_stamped_at_its_event_and_not_at_its_write(bed: Bed) -> None:
    """Rule 4: a record held for 1.5 s carries the earlier time, and one not held its own."""
    began = bed.play("held and not held")
    bed.idle_again(began)
    late = None
    for write in bed.writes(began):
        record = write["records"][0]
        apart = write["t"] - _moment(record["timestamp"])
        if late is None:
            late = apart
            assert apart >= 1.4, f"the held record was stamped {apart:.2f} s before its write"
        else:
            assert 0 <= apart < 0.5, (
                f"the record not held was stamped {apart:.2f} s before its write"
            )
    assert late is not None
    # The file's order is the order of writing, and the stamps in it still rise.
    stamps = [row["timestamp"] for row in bed.rows()]
    assert stamps == sorted(stamps)


# -------------------------------------------------------------------- the screen and the keys


def test_the_working_row_and_the_background_work_are_served_from_the_screen(bed: Bed) -> None:
    began = bed.play("work in the background")
    busy = harness.poll(
        "the background work on the screen",
        lambda: (
            bed.screen() if (bed.screen()["screen"].get("background") or {}).get("shells") else None
        ),
    )
    assert busy["screen"]["state"] == "working"
    assert busy["terminal"]["working_line"] == WORKING
    assert busy["screen"]["background"]["shells"] == 2
    assert (
        busy["screen"]["background"]["agents"]
        == [{"type": "general-purpose", "description": "Count the files"}]
        or len(busy["screen"]["background"]["agents"]) == 1
    )
    row = bed.server.live_session()
    assert row is not None and row["work_running"] is True
    bed.idle_again(began)
    assert bed.screen_state() == "idle"
    assert "Finished the background work." in [reply["text"] for reply in bed.turns()["replies"]]


def test_escape_from_the_server_interrupts_a_running_turn(bed: Bed) -> None:
    began = bed.play("to be interrupted")
    harness.poll("the screen to be working", lambda: bed.screen_state() == "working")
    status, answer = bed.server.ask("POST", f"sessions/{bed.id}/interrupt", {})
    assert status == 200, answer
    bed.idle_again(began, deadline=10)
    assert bed.since(began, "interrupted")
    assert bed.screen_state() == "idle"
    last = bed.rows()[-1]
    assert last["message"]["content"][0]["text"] == "[Request interrupted by user]"
    assert "Never said." not in [reply["text"] for reply in bed.turns()["replies"]]
