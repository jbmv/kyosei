"""The Claude Code transcript reader for credit checks (KYO-643).

Every fixture below copies the *shape* of a record read from this project's own
transcripts (Claude Code 2.1.241 to 2.1.319, read 2026-11-06): its type, the fields that
distinguish it, and where its text sits. The words in them are invented, because the
transcripts are private and this repository is public.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kyosei import cmd_sessions
from kyosei.cmd_sessions import (
    BridgeLookup,
    apply_event,
    expand_home,
    home_relative,
    read_sessions,
    record_event,
    recorded_transcript_path,
    sessions_path,
    write_sessions,
)
from kyosei.harness import claude_code_transcript as tr

SESSION = "5b0c6a1e-1111-4a2b-9c3d-0123456789ab"
OTHER = "5b0c6a1e-2222-4a2b-9c3d-0123456789ab"
STAMP = "2026-10-10T13:50:23.960Z"

#: A typed turn present in every readable fixture, so the file carries `origin`.
ANCHOR = "the anchor turn about lanterns"


# ------------------------------------------------------------------ record builders
#
# One builder per shape in KYO-638 section 5.2's table, plus the two KYO-640 adds.


def _base(kind: str, uuid: str, stamp: str = STAMP) -> dict:
    return {
        "type": kind,
        "uuid": uuid,
        "parentUuid": None,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/somewhere/project",
        "version": "2.1.300",
    }


def typed(uuid: str, text: str, *, source: str = "typed", stamp: str = STAMP) -> dict:
    """The person's typed message: string content, origin human."""
    record = _base("user", uuid, stamp)
    record.update(
        message={"role": "user", "content": text},
        origin={"kind": "human"},
        promptSource=source,
    )
    if source == "queued":
        record["turnOrigin"] = "human"
    return record


def typed_with_image(uuid: str, text: str) -> dict:
    record = typed(uuid, text)
    record["message"]["content"] = [
        {"type": "text", "text": text},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}},
    ]
    return record


def tool_result(uuid: str, text: str) -> dict:
    """A tool result: a list of tool_result blocks and a toolUseResult."""
    record = _base("user", uuid)
    record.update(
        message={
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_x", "content": text}],
        },
        toolUseResult={"stdout": text, "stderr": "", "interrupted": False},
        sourceToolAssistantUUID="a-1",
    )
    return record


def peer(uuid: str, text: str) -> dict:
    """Another session's message (a subagent hand-back)."""
    record = _base("user", uuid)
    record.update(
        message={"role": "user", "content": text},
        origin={"kind": "peer"},
        turnOrigin="peer",
        promptSource="system",
        isMeta=True,
    )
    return record


def task_notification(uuid: str, text: str) -> dict:
    record = _base("user", uuid)
    record.update(
        message={
            "role": "user",
            "content": f"<task-notification><summary>{text}</summary></task-notification>",
        },
        origin={"kind": "task-notification"},
        promptSource="system",
    )
    return record


def skill_body(uuid: str, text: str) -> dict:
    """Skill text injected on load: isMeta, sourceToolUseID, a list of text blocks."""
    record = _base("user", uuid)
    record.update(
        message={"role": "user", "content": [{"type": "text", "text": text}]},
        isMeta=True,
        sourceToolUseID="toolu_skill",
    )
    return record


def shell_input(uuid: str, text: str) -> dict:
    record = _base("user", uuid)
    record.update(message={"role": "user", "content": f"<bash-input>{text}</bash-input>"})
    return record


def shell_output(uuid: str, text: str) -> dict:
    """`!` output carries turnOrigin human and no origin."""
    record = _base("user", uuid)
    record.update(
        message={
            "role": "user",
            "content": f"<bash-stdout>{text}</bash-stdout><bash-stderr></bash-stderr>",
        },
        turnOrigin="human",
    )
    return record


def interrupt(uuid: str, _text: str = "") -> dict:
    record = _base("user", uuid)
    record.update(
        message={
            "role": "user",
            "content": [{"type": "text", "text": "[Request interrupted by user]"}],
        }
    )
    return record


def assistant_text(uuid: str, text: str) -> dict:
    record = _base("assistant", uuid)
    record.update(message={"role": "assistant", "content": [{"type": "text", "text": text}]})
    return record


def hook_output(uuid: str, text: str) -> dict:
    record = _base("attachment", uuid)
    record["attachment"] = {"type": "hook_success", "content": text, "hookName": "SessionStart"}
    return record


def queued(uuid: str, text: str, *, origin: str | None = "human", mode: str = "prompt") -> dict:
    """A message sent while the agent is mid-turn: a queued_command attachment."""
    record = _base("attachment", uuid)
    attachment: dict = {
        "type": "queued_command",
        "prompt": text,
        "commandMode": mode,
        "source_uuid": "q-src",
        "timestamp": record["timestamp"],
    }
    if origin is not None:
        attachment["origin"] = {"kind": origin}
        attachment["humanTurn"] = origin == "human"
    record["attachment"] = attachment
    return record


def ask(uuid: str, tool_id: str, question: str, options: list[tuple[str, str]]) -> dict:
    """The agent's AskUserQuestion call."""
    record = _base("assistant", uuid)
    record["message"] = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": tool_id,
                "name": "AskUserQuestion",
                "input": {
                    "questions": [
                        {
                            "question": question,
                            "header": "Choice",
                            "multiSelect": False,
                            "options": [{"label": lab, "description": d} for lab, d in options],
                        }
                    ]
                },
            }
        ],
    }
    return record


def answer(uuid: str, tool_id: str, question: str, chosen: str) -> dict:
    """The tool result carrying the person's choice."""
    record = _base("user", uuid)
    record.update(
        message={
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f'Your questions have been answered: "{question}"="{chosen}". '
                    "You can now continue with these answers in mind.",
                }
            ],
        },
        toolUseResult={"questions": [{"question": question}], "answers": {question: chosen}},
        sourceToolAssistantUUID="a-ask",
    )
    return record


# ------------------------------------------------------------------ fixtures on disk


def write_transcript(
    config: Path,
    records: list[dict],
    *,
    project: str = "-somewhere-project",
    session: str = SESSION,
) -> Path:
    path = config / "projects" / project / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def write_history(config: Path, lines: list[dict]) -> None:
    config.mkdir(parents=True, exist_ok=True)
    (config / "history.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def history_line(
    display: str, millis: int, *, session: str = SESSION, pasted: dict | None = None
) -> dict:
    return {
        "display": display,
        "pastedContents": pasted or {},
        "timestamp": millis,
        "project": "/somewhere/project",
        "sessionId": session,
    }


@pytest.fixture
def config(tmp_path: Path) -> Path:
    return tmp_path / "claude-config"


def check(config: Path, quote: str, **kwargs) -> tr.CheckResult:
    return tr.check_quote(SESSION, quote, config=config, **kwargs)


# ------------------------------------------------------------------ c1: record shapes

INCLUDED = {
    "typed prompt": lambda u, w: typed(u, f"please {w} today"),
    "queued prompt": lambda u, w: typed(u, f"please {w} today", source="queued"),
    "text beside an image": lambda u, w: typed_with_image(u, f"see [Image #1] and {w}"),
    "mid-turn queued_command": lambda u, w: queued(u, f"also {w} meanwhile"),
}

EXCLUDED = {
    "tool result": tool_result,
    "peer message": peer,
    "task notification": task_notification,
    "skill body": skill_body,
    "shell input": shell_input,
    "shell output": shell_output,
    "assistant text": assistant_text,
    "hook output": hook_output,
    "queued_command from a task notification": lambda u, w: queued(
        u, w, origin="task-notification", mode="task-notification"
    ),
    "queued_command from a peer": lambda u, w: queued(u, w, origin="peer"),
}


@pytest.mark.parametrize("shape", sorted(INCLUDED))
def test_each_included_shape_is_read_as_the_persons_words(config: Path, shape: str) -> None:
    write_transcript(config, [typed("u-anchor", ANCHOR), INCLUDED[shape]("u-x", "move the heron")])
    result = check(config, "move the heron")
    assert result.outcome in (tr.MATCHED, tr.MATCHED_PASTE_UNKNOWN), result.why
    assert result.turn == "u-x"


@pytest.mark.parametrize("shape", sorted(EXCLUDED))
def test_each_excluded_shape_is_not_the_persons_words(config: Path, shape: str) -> None:
    write_transcript(config, [typed("u-anchor", ANCHOR), EXCLUDED[shape]("u-x", "move the heron")])
    result = check(config, "move the heron")
    assert result.outcome == tr.NOT_FOUND, result.why
    assert result.turn is None


def test_the_interrupt_marker_is_not_a_turn(config: Path) -> None:
    write_transcript(config, [typed("u-anchor", ANCHOR), interrupt("u-x")])
    turns = tr.find_person_turns(SESSION, config=config).turns
    assert [t.id for t in turns] == ["u-anchor"]


def test_a_sidechain_record_is_not_the_persons_words(config: Path) -> None:
    side = typed("u-x", "move the heron")
    side["isSidechain"] = True
    write_transcript(config, [typed("u-anchor", ANCHOR), side])
    assert check(config, "move the heron").outcome == tr.NOT_FOUND


def test_a_shell_command_with_a_human_origin_is_still_excluded(config: Path) -> None:
    record = typed("u-x", "<bash-input>move the heron</bash-input>")
    write_transcript(config, [typed("u-anchor", ANCHOR), record])
    assert check(config, "move the heron").outcome == tr.NOT_FOUND


def test_a_slash_command_contributes_only_its_arguments(config: Path) -> None:
    command = typed(
        "u-x",
        "<command-message>tidy</command-message>\n<command-name>/tidy</command-name>\n"
        "<command-args>fold the heron maps</command-args>",
    )
    write_transcript(config, [typed("u-anchor", ANCHOR), command])
    assert check(config, "fold the heron maps").turn == "u-x"
    assert check(config, "command-name").outcome == tr.NOT_FOUND


def test_the_agents_echo_of_a_quote_does_not_make_it_ambiguous(config: Path) -> None:
    """KYO-638 5.2, measured on KYO-637: the quote appears in the person's turn and again
    in the agent's tool_use and the tool_result of its own `kyo attach`."""
    echo = assistant_text("a-1", "attaching: schema lanterns, yes")
    write_transcript(
        config,
        [typed("u-1", "schema lanterns, yes"), echo, tool_result("t-1", "schema lanterns, yes")],
    )
    result = check(config, "schema lanterns, yes")
    assert (result.outcome, result.turn) == (tr.MATCHED_PASTE_UNKNOWN, "u-1")


def test_an_askuserquestion_answer_is_the_persons_choice(config: Path) -> None:
    question = "Which path should the ferry take?"
    records = [
        typed("u-anchor", ANCHOR),
        ask(
            "a-ask",
            "toolu_ask",
            question,
            [
                ("North channel (Recommended)", "Shorter, colder"),
                ("South channel", "Longer, calmer"),
            ],
        ),
        answer("u-ans", "toolu_ask", question, "North channel (Recommended)"),
    ]
    write_transcript(config, records)
    result = check(config, "North channel (Recommended)")
    assert (result.outcome, result.turn, result.form) == (tr.MATCHED, "u-ans", tr.FORM_ANSWER)
    # The question and the option descriptions are the agent's words, not the person's,
    # even though the tool result's content string repeats the question.
    assert check(config, "Which path should the ferry take?").outcome == tr.NOT_FOUND
    assert check(config, "Shorter, colder").outcome == tr.NOT_FOUND


def test_answers_without_an_askuserquestion_call_are_not_read(config: Path) -> None:
    question = "Which path should the ferry take?"
    write_transcript(
        config,
        [typed("u-anchor", ANCHOR), answer("u-ans", "toolu_other", question, "North channel")],
    )
    assert check(config, "North channel").outcome == tr.NOT_FOUND


# ------------------------------------------------------------------ c2: outcomes


def test_matched(config: Path) -> None:
    write_transcript(config, [typed("u-1", "ship the heron release on friday")])
    write_history(config, [history_line("ship the heron release on friday", 1_790_000_000_000)])
    result = check(config, "ship the heron release")
    assert (result.outcome, result.source, result.turn) == (tr.MATCHED, tr.SOURCE_TRANSCRIPT, "u-1")
    assert result.observed_at == STAMP
    assert result.accepted


def test_not_found(config: Path) -> None:
    write_transcript(config, [typed("u-1", "ship the heron release on friday")])
    result = check(config, "ship the otter release")
    assert result.outcome == tr.NOT_FOUND
    assert not result.accepted


def test_ambiguous_and_naming_the_turn_resolves_it(config: Path) -> None:
    write_transcript(
        config,
        [
            typed("u-1", "yes", stamp="2026-10-10T10:00:00.000Z"),
            typed("u-2", "yes", stamp="2026-10-10T11:00:00.000Z"),
        ],
    )
    result = check(config, "yes")
    assert result.outcome == tr.AMBIGUOUS
    assert not result.accepted
    assert result.candidates == (
        ("u-1", "2026-10-10T10:00:00.000Z"),
        ("u-2", "2026-10-10T11:00:00.000Z"),
    )
    named = check(config, "yes", turn="u-2")
    assert (named.outcome, named.turn) == (tr.MATCHED_PASTE_UNKNOWN, "u-2")
    assert check(config, "yes", turn="u-9").outcome == tr.NOT_FOUND


def test_transcript_absent(config: Path) -> None:
    write_transcript(config, [typed("u-1", "yes")], session=OTHER)
    result = check(config, "yes")
    assert result.outcome == tr.TRANSCRIPT_ABSENT
    assert result.accepted


def test_unreadable_when_no_record_carries_origin(config: Path) -> None:
    """An older Claude Code wrote no `origin`; the reader does not guess at it."""
    old = typed("u-1", "ship the heron release")
    del old["origin"]
    write_transcript(config, [old, assistant_text("a-1", "ok")])
    result = check(config, "ship the heron release")
    assert result.outcome == tr.UNREADABLE
    assert result.accepted


def test_unreadable_when_the_file_is_not_jsonl(config: Path) -> None:
    path = write_transcript(config, [])
    path.write_text("this is not a transcript\n", encoding="utf-8")
    assert check(config, "anything").outcome == tr.UNREADABLE


def test_a_quote_found_only_in_the_history_is_matched_with_the_history_named(config: Path) -> None:
    write_history(
        config,
        [
            history_line("ship the heron release on friday", 1_790_000_000_123),
            history_line("ship the heron release", 1_790_000_000_999, session=OTHER),
        ],
    )
    result = check(config, "ship the heron release on friday")
    assert result.outcome == tr.MATCHED
    assert result.source == tr.SOURCE_HISTORY
    assert result.turn == "history:1790000000123"
    assert result.observed_at == "2026-09-21T14:13:20.123Z"


def test_the_history_also_serves_an_unreadable_transcript(config: Path) -> None:
    old = typed("u-1", "ship the heron release")
    del old["origin"]
    write_transcript(config, [old])
    write_history(config, [history_line("ship the heron release", 1_790_000_000_000)])
    result = check(config, "ship the heron release")
    assert (result.outcome, result.source) == (tr.MATCHED, tr.SOURCE_HISTORY)


def test_a_present_transcript_decides_over_the_history(config: Path) -> None:
    """KYO-640: a mismatch against a present transcript is refused."""
    write_transcript(config, [typed("u-1", ANCHOR)])
    write_history(config, [history_line("ship the heron release", 1_790_000_000_000)])
    result = check(config, "ship the heron release")
    assert result.outcome == tr.NOT_FOUND
    assert "prompt history does" in result.why


def test_history_with_no_match_leaves_the_transcript_outcome(config: Path) -> None:
    write_history(config, [history_line("something else", 1_790_000_000_000)])
    assert check(config, "ship the heron release").outcome == tr.TRANSCRIPT_ABSENT


def test_history_skips_shell_commands_and_keeps_slash_command_arguments(config: Path) -> None:
    write_history(
        config,
        [
            history_line("!ls the heron", 1_790_000_000_000),
            history_line("/tidy fold the maps", 1_790_000_000_001),
        ],
    )
    assert check(config, "ls the heron").outcome == tr.TRANSCRIPT_ABSENT
    assert check(config, "fold the maps").source == tr.SOURCE_HISTORY
    assert check(config, "tidy").outcome == tr.TRANSCRIPT_ABSENT


# ------------------------------------------------------------------ c3: pasted content
#
# The shape observed 2026-11-06 in a recovered transcript of this project (Claude Code
# 2.1.247): the person sent a message while the agent was mid-turn, typing one line and
# pasting a 37-line crash report under it. The transcript holds a queued_command
# attachment whose prompt is the typed line, a newline, and the whole paste, with no
# marker between them. history.jsonl holds the same prompt as the typed line, a newline
# and `[Pasted text #2 +37 lines]`, with `pastedContents` {"2": {id, type, contentHash}},
# stamped about three minutes after the attachment (when the queued message was taken
# into the turn).

TYPED_LINE = "maybe it is because the window sits behind the others on my second display"
PASTE = "\n".join(
    [
        "-------------------------------------",
        "Translated Report (Lantern Edition)",
        "-------------------------------------",
        "Process:   Lantern [4242]",
        "Path:      /Applications/Lantern.app/Contents/MacOS/Lantern",
    ]
    + [f"Thread {n}:: lantern-worker queue frame {n}" for n in range(32)]
)


def _paste_fixture(config: Path, *, with_history: bool = True) -> None:
    write_transcript(
        config,
        [
            typed("u-anchor", ANCHOR, stamp="2026-08-28T13:00:00.000Z"),
            queued("u-paste", f"{TYPED_LINE}\n{PASTE}"),
        ],
    )
    if with_history:
        write_history(
            config,
            [
                history_line(
                    f"{TYPED_LINE}\n[Pasted text #2 +37 lines]",
                    1_787_923_410_139,
                    pasted={"2": {"id": 2, "type": "text", "contentHash": "0123456789abcdef"}},
                )
            ],
        )


def test_the_paste_fixture_is_the_observed_size() -> None:
    assert len(PASTE.splitlines()) == 37


def test_the_typed_part_of_a_turn_with_a_paste_is_matched(config: Path) -> None:
    _paste_fixture(config)
    result = check(config, "the window sits behind the others")
    assert (result.outcome, result.turn, result.form) == (tr.MATCHED, "u-paste", tr.FORM_MID_TURN)


def test_pasted_content_is_not_matched_as_the_persons_words(config: Path) -> None:
    _paste_fixture(config)
    result = check(config, "Translated Report (Lantern Edition)")
    assert result.outcome == tr.NOT_FOUND
    assert "inside pasted content of turn u-paste" in result.why


def test_a_quote_cannot_run_from_the_typed_text_into_the_paste(config: Path) -> None:
    _paste_fixture(config)
    assert check(config, "on my second display ----").outcome == tr.NOT_FOUND
    # An omission does not bridge into the paste either.
    assert check(config, "the window [...] Lantern Edition").outcome == tr.NOT_FOUND


def test_a_paste_between_two_typed_parts_of_a_typed_prompt(config: Path) -> None:
    write_transcript(config, [typed("u-1", "before the paste\nPASTED BODY LINE\nafter the paste")])
    write_history(
        config,
        [
            history_line(
                "before the paste\n[Pasted text #1 +1 lines]\nafter the paste",
                1_790_000_000_000,
                pasted={"1": {"id": 1, "type": "text", "content": "PASTED BODY LINE"}},
            )
        ],
    )
    assert check(config, "after the paste").outcome == tr.MATCHED
    assert check(config, "before the paste [...] after the paste").outcome == tr.MATCHED
    assert check(config, "PASTED BODY LINE").outcome == tr.NOT_FOUND


def test_without_a_history_line_the_paste_boundary_is_unknown(config: Path) -> None:
    _paste_fixture(config, with_history=False)
    # Neither half can be ruled in or out, and the result says so.
    assert check(config, "the window sits behind").outcome == tr.MATCHED_PASTE_UNKNOWN
    assert check(config, "Translated Report (Lantern Edition)").outcome == tr.MATCHED_PASTE_UNKNOWN


def test_a_history_line_from_another_session_does_not_pair(config: Path) -> None:
    _paste_fixture(config, with_history=False)
    write_history(
        config, [history_line(f"{TYPED_LINE}\n[Pasted text #2 +37 lines]", 1, session=OTHER)]
    )
    assert check(config, "the window sits behind").outcome == tr.MATCHED_PASTE_UNKNOWN


def test_history_as_second_source_excludes_its_placeholders(config: Path) -> None:
    write_history(
        config, [history_line(f"{TYPED_LINE}\n[Pasted text #2 +37 lines]", 1_787_923_410_139)]
    )
    assert check(config, "on my second display").source == tr.SOURCE_HISTORY
    assert check(config, "Pasted text").outcome == tr.TRANSCRIPT_ABSENT


# ------------------------------------------------------------------ c4: what the store gets

SECRET_WORDS = "the private heron sentence nobody else typed"


def _every_outcome(config: Path) -> list[tr.CheckResult]:
    results = []
    write_transcript(
        config,
        [
            typed("u-1", f"{SECRET_WORDS} and yes", stamp="2026-10-10T10:00:00.000Z"),
            typed("u-2", "yes", stamp="2026-10-10T11:00:00.000Z"),
        ],
    )
    results.append(check(config, "the private heron sentence"))  # matched-paste-unknown
    results.append(check(config, "yes"))  # ambiguous
    results.append(check(config, "absent words"))  # not-found
    results.append(tr.check_quote(OTHER, "absent", config=config))  # transcript-absent
    return results


def test_store_fields_are_the_uuid_the_timestamp_and_the_outcome(config: Path) -> None:
    results = _every_outcome(config)
    assert [r.outcome for r in results] == [
        tr.MATCHED_PASTE_UNKNOWN,
        tr.AMBIGUOUS,
        tr.NOT_FOUND,
        tr.TRANSCRIPT_ABSENT,
    ]
    matched = results[0]
    assert matched.store_fields() == {
        "transcript_check": tr.MATCHED_PASTE_UNKNOWN,
        "source_turn": "u-1",
        "observed_at": "2026-10-10T10:00:00.000Z",
    }
    for result in results:
        assert set(result.store_fields()) == {"transcript_check", "source_turn", "observed_at"}


def test_no_transcript_text_or_path_leaves_in_a_result(config: Path) -> None:
    """Not the turn's other text, its neighbours or the path (KYO-638 5.5), in any field,
    including `why`, which a caller may show."""
    write_transcript(config, [assistant_text("a-0", "the agent's neighbouring reply")])
    for result in _every_outcome(config):
        rendered = json.dumps(result.__dict__) + repr(result)
        assert "nobody else typed" not in rendered
        assert "neighbouring reply" not in rendered
        assert "-somewhere-project" not in json.dumps(result.store_fields())
        assert str(config) not in json.dumps(result.store_fields())


# ------------------------------------------------------------------ c5: where the files are


def test_config_dir_is_claude_config_dir_when_set(tmp_path: Path) -> None:
    assert tr.config_dir({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path
    assert tr.config_dir({}) == Path.home() / ".claude"


def test_the_module_cites_the_documentation_it_states() -> None:
    doc = tr.__doc__ or ""
    for url in (
        "https://code.claude.com/docs/en/env-vars",
        "https://code.claude.com/docs/en/claude-directory",
        "https://code.claude.com/docs/en/sessions",
    ):
        assert url in doc
    assert "CLAUDE_CONFIG_DIR" in doc and "CLAUDE_CODE_PROJECT_DIR_NAME" in doc


def test_transcript_and_history_are_read_under_claude_config_dir(tmp_path: Path) -> None:
    config = tmp_path / "work-config"
    write_transcript(config, [typed("u-1", "ship the heron release")])
    write_history(config, [history_line("ship the heron release", 1_790_000_000_000)])
    result = tr.check_quote(
        SESSION, "ship the heron release", environ={"CLAUDE_CONFIG_DIR": str(config)}
    )
    assert (result.outcome, result.turn) == (tr.MATCHED, "u-1")


def test_the_search_does_not_depend_on_the_project_directory_name(config: Path) -> None:
    """CLAUDE_CODE_PROJECT_DIR_NAME, or the 200-character truncation, can give any name."""
    write_transcript(config, [typed("u-1", "ship the heron release")], project="work")
    assert check(config, "ship the heron release").turn == "u-1"


def test_copies_of_one_session_are_read_as_one(config: Path) -> None:
    write_transcript(config, [typed("u-1", "ship the heron release")], project="a")
    write_transcript(config, [typed("u-1", "ship the heron release")], project="b")
    assert check(config, "ship the heron release").outcome == tr.MATCHED_PASTE_UNKNOWN


def test_the_recorded_path_is_read_first(tmp_path: Path, config: Path) -> None:
    elsewhere = tmp_path / "moved" / f"{SESSION}.jsonl"
    elsewhere.parent.mkdir()
    elsewhere.write_text(json.dumps(typed("u-r", "only in the recorded file")) + "\n")
    result = check(config, "only in the recorded file", recorded=elsewhere)
    assert result.turn == "u-r"


def test_a_session_id_that_is_not_a_uuid_is_refused(config: Path) -> None:
    with pytest.raises(ValueError):
        tr.check_quote("../../etc/passwd", "x", config=config)
    with pytest.raises(ValueError):
        tr.check_quote("*", "x", config=config)


# ------------------------------------------------------------------ matching rules


@pytest.mark.parametrize(
    "turn, quote, hit",
    [
        ("Ship   the\nheron", "Ship the heron", True),  # whitespace collapses
        ("Ship the heron", "ship the heron", False),  # case is kept
        ("Ship the heron!", "Ship the heron.", False),  # punctuation is kept
        ("café lanterns", "café lanterns", True),  # NFC on both sides
        ("first we fold, then we ship the maps", "first we fold [...] the maps", True),
        ("first we fold, then we ship the maps", "first we fold […] the maps", True),
        ("the maps, then first we fold", "first we fold [...] the maps", False),  # in order
        ("I did it yesterday", "yes", False),  # not inside a word
        ("yes, do it", "yes", True),
    ],
)
def test_matching(config: Path, turn: str, quote: str, hit: bool) -> None:
    write_transcript(config, [typed("u-1", turn)])
    assert (check(config, quote).outcome == tr.MATCHED_PASTE_UNKNOWN) is hit


def test_an_empty_quote_is_refused(config: Path) -> None:
    with pytest.raises(ValueError):
        check(config, "  [...]  ")


# ------------------------------------------------------------------ sessions.json records the path


def test_home_relative_round_trips(tmp_path: Path) -> None:
    home = tmp_path / "home"
    inside = str(home / ".claude" / "projects" / "p" / f"{SESSION}.jsonl")
    assert home_relative(inside, home) == f"~/.claude/projects/p/{SESSION}.jsonl"
    assert expand_home(home_relative(inside, home), home) == Path(inside)
    assert home_relative("/elsewhere/x.jsonl", home) == "/elsewhere/x.jsonl"
    assert home_relative(None, home) is None


def test_an_entry_records_the_transcript_path_home_relative() -> None:
    lookup = BridgeLookup(None, cmd_sessions.BRIDGE_ABSENT, "none")
    path = str(Path.home() / ".claude" / "projects" / "p" / f"{SESSION}.jsonl")
    entries, entry = apply_event(
        [],
        session_id=SESSION,
        event="SessionStart",
        cwd="/p",
        lookup=lookup,
        stamp=STAMP,
        transcript_path=path,
    )
    assert entry["transcript_path"] == f"~/.claude/projects/p/{SESSION}.jsonl"
    # A later event that names no transcript keeps the recorded one.
    _, later = apply_event(
        entries, session_id=SESSION, event="Stop", cwd="/p", lookup=lookup, stamp=STAMP
    )
    assert later["transcript_path"] == f"~/.claude/projects/p/{SESSION}.jsonl"


def test_the_reader_finds_the_path_the_hook_recorded(tmp_path: Path, config: Path) -> None:
    store = tmp_path / "store"
    transcript = write_transcript(
        config, [typed("u-1", "ship the heron release")], project="anything"
    )
    record_event(
        store, session_id=SESSION, event="SessionStart", cwd="/p", transcript_path=str(transcript)
    )
    recorded = recorded_transcript_path(store, SESSION)
    assert recorded == transcript
    assert recorded_transcript_path(store, OTHER) is None
    result = tr.check_quote(
        SESSION, "ship the heron release", config=tmp_path / "empty", recorded=recorded
    )
    assert result.turn == "u-1"


def test_an_entry_written_before_the_field_existed_reads_as_unrecorded(tmp_path: Path) -> None:
    store = tmp_path / "store"
    write_sessions(sessions_path(store), [{"session_id": SESSION, "last_seen": STAMP}])
    assert read_sessions(sessions_path(store)).entries
    assert recorded_transcript_path(store, SESSION) is None


def test_kyo_sessions_lists_the_recorded_transcript(tmp_path: Path) -> None:
    store = tmp_path / "store"
    record_event(
        store,
        session_id=SESSION,
        event="SessionStart",
        cwd="/p",
        transcript_path="/elsewhere/t.jsonl",
    )
    ctx = Namespace(root=store)
    out = cmd_sessions.cmd_sessions(ctx, Namespace(hook=False))
    assert any("transcript: /elsewhere/t.jsonl" in line for line in out.lines)
