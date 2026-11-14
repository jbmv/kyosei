"""KYO-940: a question asked with ``AskUserQuestion`` reaches a client while it is open.

Claude Code writes the assistant record holding the call when the call resolves, together
with its result (measured on 2.1.325; see :mod:`kyosei.cmd_sessions`). So the question is
taken from the ``PreToolUse`` hook's payload, saved per session under ``$KYOSEI_HOME``, and
sent by ``kyo serve`` as a pending call until the transcript holds a call with its id.

The rows fed here are in the order Claude Code writes them: nothing while the question is
open, then the call and its result in one write. The shapes of the hook payload, the call
and the result were read from a session on 2.1.325.
"""

# ruff: noqa: F811 -- the serve fixtures are imported by name, and pytest injects them

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import cmd_sessions, install_hooks
from kyosei.harness import CLAUDE_CODE_HOOKS_PATH
from kyosei.harness import claude_code_transcript as transcript

from .conftest import invoke, opt_in_transcripts
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    SESSIONS,
    Bed,
    bed,
    json_body,
    running,
    stream,
)

CALL_ID = "toolu_016NcvDxa8ZL6cf9G4gcMFib"

QUESTIONS = [
    {
        "question": "Which colour do you want?",
        "header": "Colour",
        "options": [
            {"label": "Red", "description": "Select red as the colour."},
            {"label": "Green", "description": "Select green as the colour."},
            {"label": "Blue", "description": "Select blue as the colour."},
        ],
        "multiSelect": False,
    },
    {
        "question": "Which fruits do you want?",
        "header": "Fruits",
        "options": [
            {"label": "Apple", "description": "Select apple."},
            {"label": "Pear", "description": "Select pear."},
            {"label": "Plum", "description": "Select plum."},
        ],
        "multiSelect": True,
    },
]


def payload(session_id: str = OLD, **changed: object) -> dict:
    """A ``PreToolUse`` payload with the keys Claude Code 2.1.325 sends for the tool."""
    return {
        "session_id": session_id,
        "transcript_path": "/nowhere/transcript.jsonl",
        "cwd": "/work",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": QUESTIONS},
        "tool_use_id": CALL_ID,
        **changed,
    }


def call_row(call_id: str = CALL_ID) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": "aaaaaaaa-0000-4000-8000-000000000001",
            "timestamp": "2026-11-14T21:38:20.636Z",
            "isSidechain": False,
            "message": {
                "id": "msg_question",
                "role": "assistant",
                "model": "claude-test",
                "content": [
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": "AskUserQuestion",
                        "input": {"questions": QUESTIONS},
                    }
                ],
            },
        }
    ) + "\n"


def result_row(answers: dict[str, str], call_id: str = CALL_ID) -> str:
    said = ", ".join(f'"{question}"="{answer}"' for question, answer in answers.items())
    return json.dumps(
        {
            "type": "user",
            "uuid": "aaaaaaaa-0000-4000-8000-000000000002",
            "timestamp": "2026-11-14T21:39:05.196Z",
            "isSidechain": False,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": f"The user answered: {said}. Read the answers carefully.",
                    }
                ],
            },
            "toolUseResult": {"questions": QUESTIONS, "answers": answers, "annotations": {}},
        }
    ) + "\n"


def dismissed_row(call_id: str = CALL_ID) -> str:
    return json.dumps(
        {
            "type": "user",
            "uuid": "aaaaaaaa-0000-4000-8000-000000000003",
            "timestamp": "2026-11-14T21:40:07.834Z",
            "isSidechain": False,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "is_error": True,
                        "content": "The user doesn't want to proceed with this tool use.",
                    }
                ],
            },
            "toolUseResult": "User rejected tool use",
        }
    ) + "\n"


def hook(project: Path, document: dict):
    return invoke(["--root", str(project), "sessions", "--hook"], stdin=json.dumps(document))


# ------------------------------------------------------------------------------ the hook


def test_the_hook_saves_the_question_with_its_call_id_and_says_nothing(
    project: Path, store_home: Path
) -> None:
    opt_in_transcripts(project)
    ran = hook(project, payload())
    assert (ran.code, ran.out, ran.err) == (0, "", "")
    saved = cmd_sessions.read_question(None, OLD)
    assert saved is not None
    assert saved["call_id"] == CALL_ID
    assert saved["input"] == {"questions": QUESTIONS}
    path = cmd_sessions.question_path(None, OLD)
    assert path is not None and store_home in path.parents, "under $KYOSEI_HOME, in no repository"
    assert path.stat().st_mode & 0o777 == 0o600
    # The session's ledger is none of this hook's business under this event.
    assert not cmd_sessions.sessions_path(project).exists()


def test_the_hook_saves_nothing_in_a_project_that_has_not_opted_in(project: Path) -> None:
    ran = hook(project, payload())
    assert (ran.code, ran.out, ran.err) == (0, "", "")
    assert cmd_sessions.read_question(None, OLD) is None


@pytest.mark.parametrize(
    "changed",
    [
        {"tool_name": "Bash"},
        {"tool_use_id": None},
        {"tool_input": "not an object"},
        {"agent_id": "a-subagent"},
        {"session_id": "../outside"},
    ],
)
def test_the_hook_saves_no_question_it_cannot_name(project: Path, changed: dict) -> None:
    opt_in_transcripts(project)
    ran = hook(project, payload(**changed))
    assert (ran.code, ran.out, ran.err) == (0, "", "")
    home = cmd_sessions.store_home(None) / cmd_sessions.QUESTIONS_DIRNAME
    assert not home.exists() or not list(home.iterdir())


@pytest.mark.parametrize("event", cmd_sessions.SESSION_EVENTS)
def test_each_session_event_removes_the_saved_question(project: Path, event: str) -> None:
    opt_in_transcripts(project)
    hook(project, payload())
    assert cmd_sessions.read_question(None, OLD) is not None
    hook(project, {"session_id": OLD, "hook_event_name": event, "cwd": str(project)})
    assert cmd_sessions.read_question(None, OLD) is None


def test_a_file_of_another_shape_is_read_as_no_question(project: Path) -> None:
    opt_in_transcripts(project)
    hook(project, payload())
    path = cmd_sessions.question_path(None, OLD)
    assert path is not None
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**document, "schema": 99}), encoding="utf-8")
    assert cmd_sessions.read_question(None, OLD) is None
    path.write_text("not json", encoding="utf-8")
    assert cmd_sessions.read_question(None, OLD) is None


def test_the_packaged_hooks_run_the_sessions_hook_before_a_question() -> None:
    """Fails if the hook that announces the question is taken out of either install route."""
    groups = install_hooks._load_hook_fragment("pre-tool-use.json", None, "PreToolUse")
    asked = [group for group in groups if group.get("matcher") == transcript.QUESTION_TOOL]
    assert len(asked) == 1
    assert install_hooks.command_elements(asked[0]["hooks"][0]["command"]) == {
        "kyo sessions --hook"
    }
    plugin = json.loads(CLAUDE_CODE_HOOKS_PATH.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    commands = [
        hook["command"]
        for group in plugin
        if group.get("matcher") == transcript.QUESTION_TOOL
        for hook in group["hooks"]
    ]
    assert len(commands) == 1 and commands[0].endswith("kyo-hook\" sessions --hook")


# ---------------------------------------------------------------------------- the stream


def test_the_stream_sends_the_question_before_the_transcript_holds_the_call(
    bed: Bed, stream
) -> None:
    environ = bed.server.environ
    opened = stream()
    ready = opened.event("ready")
    assert ready["pending_tool_calls"] == []
    before = bed.transcript.read_bytes()

    # The dialog opens: the hook runs, and the transcript is not written.
    assert cmd_sessions.record_question(environ, payload()) is None
    event = opened.event("transcript")
    assert bed.transcript.read_bytes() == before
    assert event["cursor"] == ready["cursor"], "a pending call is no object of the transcript"
    assert event["tool_calls"] == []
    (pending,) = event["pending_tool_calls"]
    assert pending["call_id"] == CALL_ID
    assert pending["name"] == "AskUserQuestion"
    assert pending["input"] == {"questions": QUESTIONS}
    assert (pending["result"], pending["uuid"], pending["pending"]) == (None, None, True)

    # A client that joins while the question is open is told of it at once.
    joined = stream()
    assert [c["call_id"] for c in joined.event("ready")["pending_tool_calls"]] == [CALL_ID]

    # Answered: Claude Code writes the call and its result together.
    bed.append(
        [
            call_row(),
            result_row(
                {"Which colour do you want?": "Red", "Which fruits do you want?": "Apple, Plum"}
            ),
        ]
    )
    event = opened.event("transcript")
    assert event["pending_tool_calls"] == [], "the transcript's call has taken its place"
    (call,) = event["tool_calls"]
    assert call["call_id"] == CALL_ID, "one id, so a client keeps one object"
    assert call["result"] is not None and call["result"]["is_error"] is False
    assert event["cursor"] != ready["cursor"]

    # A client that joins afterwards is told of no pending call, though the file remains.
    assert cmd_sessions.read_question(environ, OLD) is not None
    assert stream().event("ready")["pending_tool_calls"] == []


def test_a_dismissed_question_is_pending_no_more(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    assert cmd_sessions.record_question(bed.server.environ, payload()) is None
    assert len(opened.event("transcript")["pending_tool_calls"]) == 1
    bed.append([call_row(), dismissed_row()])
    event = opened.event("transcript")
    assert event["pending_tool_calls"] == []
    (call,) = event["tool_calls"]
    assert call["result"]["is_error"] is True
    assert event["turns"] == [], "a dismissal is no answer of the person's"


def test_a_question_removed_before_the_transcript_held_it_is_withdrawn(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    assert cmd_sessions.record_question(bed.server.environ, payload()) is None
    assert len(opened.event("transcript")["pending_tool_calls"]) == 1
    cmd_sessions.clear_question(bed.server.environ, OLD)
    assert opened.event("transcript")["pending_tool_calls"] == []


def test_a_frame_that_changes_no_pending_call_does_not_state_them(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    bed.append([call_row("toolu_another")])
    assert "pending_tool_calls" not in opened.event("transcript")


# --------------------------------------------------------------------------- the answers


def answer_turn(bed: Bed, answers: dict[str, str]) -> dict:
    bed.append([call_row(), result_row(answers)])
    turns = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["turns"]
    return next(turn for turn in turns if turn["id"] == "aaaaaaaa-0000-4000-8000-000000000002")


def test_an_answer_is_served_with_its_question_and_whether_it_was_picked(bed: Bed) -> None:
    turn = answer_turn(
        bed,
        {
            "Which colour do you want?": "Teal, please",
            "Which fruits do you want?": "Apple, Plum",
        },
    )
    assert turn["text"] == "Teal, please\nApple, Plum"
    assert turn["typed"] == ["Teal, please", "Apple, Plum"], "what a credit is checked against"
    assert turn["answers"] == [
        {
            "question": "Which colour do you want?",
            "header": "Colour",
            "multi_select": False,
            "parts": [{"text": "Teal, please", "picked": False}],
        },
        {
            "question": "Which fruits do you want?",
            "header": "Fruits",
            "multi_select": True,
            "parts": [{"text": "Apple", "picked": True}, {"text": "Plum", "picked": True}],
        },
    ]


def test_a_prompt_the_person_typed_carries_no_answers(bed: Bed) -> None:
    turns = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["turns"]
    typed = [turn for turn in turns if turn["form"] != "answer"]
    assert typed and all(turn["answers"] == [] for turn in typed)


@pytest.mark.parametrize(
    ("answer", "labels", "multi", "parts"),
    [
        ("Red", ["Red", "Green"], False, [("Red", True)]),
        ("Crimson", ["Red", "Green"], False, [("Crimson", False)]),
        # A single-select answer is one piece, whatever it holds.
        ("Red, Green", ["Red", "Green"], False, [("Red, Green", False)]),
        ("Red, Green", ["Red", "Green"], True, [("Red", True), ("Green", True)]),
        ("Red, and mauve", ["Red", "Green"], True, [("Red", True), ("and mauve", False)]),
        ("mauve, Red", ["Red", "Green"], True, [("mauve, Red", False)]),
        # A label that holds the separator, and one that begins with another.
        (
            "Red, White, Blue",
            ["Red", "Red, White", "Blue"],
            True,
            [("Red, White", True), ("Blue", True)],
        ),
        ("Red, White", ["Red, White", "Blue"], True, [("Red, White", True)]),
    ],
)
def test_an_answer_is_divided_into_the_labels_picked_and_the_text_typed(
    answer: str, labels: list[str], multi: bool, parts: list[tuple[str, bool]]
) -> None:
    found = transcript._answer_parts(answer, labels, multi)
    assert [(part.text, part.picked) for part in found] == parts
    assert ", ".join(part.text for part in found) == answer


def test_answers_whose_question_the_result_does_not_hold_are_left_undivided(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    row = json.loads(result_row({"A question the result does not list": "Red"}))
    path.write_text(call_row() + json.dumps(row) + "\n", encoding="utf-8")
    reader = transcript.SessionTranscript(path)
    (turn,) = reader.read().turns
    assert (turn.form, turn.segments, turn.answers) == ("answer", ("Red",), ())
    assert reader.holds_call(CALL_ID) and not reader.holds_call("toolu_other")
