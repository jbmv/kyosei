"""The Stop-hook discussion check, `kyo discuss skip`, and doctor's shares (KYO-662;
decision KYO-654, DC4).

The transcripts written here reuse the record shapes of ``tests/test_kyo658_discussions.py``;
the words are invented.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from kyosei import cmd_sessions, cmd_turns, install_hooks
from kyosei.cmd_discuss import CUE_ASKED, CUE_PERSON, CUE_REPLY, detect, person_cue, reply_cue
from kyosei.config import load_config
from kyosei.harness import claude_code_transcript as transcript

from .conftest import invoke, opt_in_transcripts
from .test_kyo658_discussions import (
    DECIDING_WORDS,
    PROMPT_1,
    PROMPT_2,
    PROMPT_3,
    SESSION,
    _decision_flags,
    _record,
    agent_text,
    append_transcript,
    index_line,
    notification,
    person,
    prompt_hook,
    transcript_path,
    write_transcript,
)

MEASURE = Path(__file__).parent / "fixtures" / "kyo662_measure_detector.py"
ENV_TRANSCRIPTS = "KYOSEI_KYO662_TRANSCRIPTS"
ENV_SPANS = "KYOSEI_KYO662_SPANS"


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


@pytest.fixture
def indexed(kyo, project: Path, session: str) -> Path:
    """An opted-in project whose index holds three prompts of one session, as in
    ``tests/test_kyo658_discussions.py``: the person's question and the agent's
    recommendation, a task notification, and the person's deciding words."""
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "should the cache stay?", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "I recommend keeping it.", "2026-11-06T10:00:30.000Z"),
            notification("n-2", PROMPT_2, "2026-11-06T10:00:40.000Z"),
            agent_text("a-2", "The run finished.", "2026-11-06T10:00:50.000Z"),
            person("p-3", PROMPT_3, DECIDING_WORDS, "2026-11-06T10:01:00.000Z"),
        ]
    )
    for prompt_id in (PROMPT_1, PROMPT_2, PROMPT_3):
        prompt_hook(kyo, prompt_id)
    return project


def stop(kyo, prompt_id: str | None, reply: str, *, json_flag: bool = False, **extra):
    payload = {
        "session_id": SESSION,
        "hook_event_name": "Stop",
        "transcript_path": str(transcript_path()),
        "last_assistant_message": reply,
        "stop_hook_active": False,
        **extra,
    }
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    args = ("--json",) if json_flag else ()
    return kyo(*args, "discuss", "check", "--hook", stdin=json.dumps(payload))


def feedback(run) -> str:
    """The additionalContext of a check that fired, asserting the output is that and no more."""
    assert run.code == 0 and run.err == "", run
    (line,) = run.out.splitlines()
    document = json.loads(line)
    assert document == {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": document["hookSpecificOutput"]["additionalContext"],
        }
    }
    return document["hookSpecificOutput"]["additionalContext"]


def silent(run) -> bool:
    return run.code == 0 and run.out == "" and run.err == ""


# ------------------------------------------------------------------ the detector


@pytest.mark.parametrize(
    "words",
    [
        "is the cache worth keeping?",
        "how about dropping the cache",
        "I'm leaning toward the second one",
        "let's discuss the layout before building",
        "what are the options here",
    ],
)
def test_the_person_cue_fires_on_a_question_or_a_phrase_that_opens_a_choice(words: str) -> None:
    assert person_cue(words)


@pytest.mark.parametrize(
    "words",
    [
        "go ahead and merge it",
        "run `grep -c '?' notes.txt` and commit",
        "see https://example.org/search?q=cache and fix it",
        "```\nwhat = options or None  # ?\n```\nmerge it",
    ],
)
def test_the_person_cue_ignores_instructions_code_and_urls(words: str) -> None:
    assert not person_cue(words)


@pytest.mark.parametrize(
    "reply",
    [
        "Both work. I recommend the first.",
        "It is your call.",
        "Done.\n\n- D1: keep the cache\n- D2: drop it\n\nNothing else changed.",
        "**Option A** — keep it.\n**Option B** — drop it.\n",
        "The cache is stale after a restart.\n\nDo we keep it?",
    ],
)
def test_the_reply_cue_fires_on_a_recommendation_options_or_a_closing_question(reply: str) -> None:
    assert reply_cue(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Merged. The suite passes.",
        "Why did it fail? The port was taken.\n\nI changed the port.\n\nThe run is green.",
        "D1 was implemented as decided.",
    ],
)
def test_the_reply_cue_ignores_a_report(reply: str) -> None:
    assert not reply_cue(reply)


def test_detect_names_each_cue() -> None:
    assert detect(("keep it?",), "I recommend keeping it.", True) == [
        CUE_PERSON,
        CUE_REPLY,
        CUE_ASKED,
    ]
    assert detect((), "Merged.", False) == []


def test_a_turn_view_reads_the_persons_words_mid_turn_messages_and_the_question_tool() -> None:
    records = [
        person("p-1", PROMPT_1, "merge it", "2026-11-06T10:00:00.000Z"),
        _record(
            "assistant",
            "a-0",
            "2026-11-06T10:00:05.000Z",
            message={
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "AskUserQuestion", "input": {}}],
            },
        ),
        _record(
            "attachment",
            "q-1",
            "2026-11-06T10:00:10.000Z",
            attachment={
                "type": "queued_command",
                "commandMode": "prompt",
                "origin": {"kind": "human"},
                "prompt": "or should it wait",
            },
        ),
        agent_text("a-1", "Merged.", "2026-11-06T10:00:30.000Z"),
        person("p-2", PROMPT_2, "thanks", "2026-11-06T10:01:00.000Z"),
    ]
    first, second = (transcript.turn_view(turn) for turn in transcript.split_turns(records))
    assert first.prompt_id == PROMPT_1 and first.origin == "human"
    assert first.person == ("merge it", "or should it wait")
    assert first.asked and first.reply == "Merged."
    assert second.person == ("thanks",) and second.reply is None and not second.asked


def test_read_turn_finds_a_turn_that_starts_before_the_first_window(tmp_path: Path) -> None:
    path = tmp_path / "long.jsonl"
    padding = "x" * 4096
    records = [
        person("p-0", PROMPT_2, "an earlier prompt", "2026-11-06T09:00:00.000Z"),
        agent_text("a-0", "An earlier answer.", "2026-11-06T09:00:30.000Z"),
        person("p-1", PROMPT_1, "should the cache stay?", "2026-11-06T10:00:00.000Z"),
        *(
            agent_text(f"a-{n}", padding, "2026-11-06T10:00:10.000Z")
            for n in range(1, 2 * transcript._TAIL_BYTES // 4096)
        ),
        agent_text("a-last", "Kept.", "2026-11-06T10:05:00.000Z"),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    assert path.stat().st_size > 2 * transcript._TAIL_BYTES
    turn = transcript.read_turn(path, PROMPT_1)
    assert turn is not None and turn.person == ("should the cache stay?",)
    assert turn.reply == "Kept."
    assert transcript.read_turn(path, None).prompt_id == PROMPT_1
    assert transcript.read_turn(path, PROMPT_3) is None


# ------------------------------------------------------------------ KYO-662.c2: the hook


def test_the_check_returns_additional_context_and_does_not_block(kyo, indexed: Path) -> None:
    """A question from the person, a recommendation in reply, no discussion open: one line
    of hook JSON carrying additionalContext, exit 0, nothing on stderr, no `decision`."""
    words = feedback(stop(kyo, PROMPT_1, "I recommend keeping it."))
    line = index_line(indexed, PROMPT_1)
    assert f"the person's message at {line['at']} (prompt {PROMPT_1[:8]})" in words
    assert "no discussion is open in this session" in words
    assert 'kyo discuss open --title "<topic>"' in words
    assert f"kyo discuss skip --at-prompt {PROMPT_1} --why" in words
    assert CUE_PERSON in words and CUE_REPLY in words


def test_the_check_passes_once_a_discussion_is_open(kyo, indexed: Path) -> None:
    """The first remedy the message names."""
    assert feedback(stop(kyo, PROMPT_3, "Which do you want?"))
    assert kyo("discuss", "open", "--title", "Whether the cache stays").code == 0
    passed = stop(kyo, PROMPT_3, "Which do you want?")
    assert silent(passed), passed
    why = stop(kyo, PROMPT_3, "Which do you want?", json_flag=True).json
    assert why["fire"] is False and "names this turn" in why["why"]
    # An earlier turn of the session is attached too, by the discussion being open.
    assert "is open in this session" in stop(kyo, PROMPT_1, "x", json_flag=True).json["why"]


def test_the_check_passes_once_the_turn_is_skipped_with_a_recorded_reason(
    kyo, indexed: Path
) -> None:
    """The second remedy the message names, run as the message spells it."""
    assert feedback(stop(kyo, PROMPT_1, "I recommend keeping it."))
    skipped = kyo(
        "discuss", "skip", "--at-prompt", PROMPT_1, "--why", "a question about a log line",
        "--json",
    )  # fmt: skip
    assert skipped.code == 0, skipped
    line = index_line(indexed, PROMPT_1)
    assert line["skip"]["why"] == "a question about a log line" and line["skip"]["at"]
    assert skipped.json["skip"] == line["skip"] and skipped.json["prompt_id"] == PROMPT_1
    assert silent(stop(kyo, PROMPT_1, "I recommend keeping it."))
    # The Stop hook's own fold of the transcript keeps the reason on the line.
    payload = {
        "session_id": SESSION,
        "hook_event_name": "Stop",
        "transcript_path": str(transcript_path()),
    }
    assert kyo("turns", "--hook", stdin=json.dumps(payload)).code == 0
    assert index_line(indexed, PROMPT_1)["skip"] == line["skip"]
    # Another turn of the session is still asked about.
    assert feedback(stop(kyo, PROMPT_3, "Which do you want?"))


def test_skip_without_at_prompt_marks_the_sessions_latest_prompt(kyo, indexed: Path) -> None:
    assert kyo("discuss", "skip", "--why", "a remark").code == 0
    assert index_line(indexed, PROMPT_3)["skip"]["why"] == "a remark"
    assert "skip" not in index_line(indexed, PROMPT_1)


def test_a_line_standing_for_the_agents_reply_is_never_the_one_skipped(
    kyo, indexed: Path
) -> None:
    """A reply line repeats its prompt's id and comes after it; the skip goes on the
    prompt's line, by default and when the prompt is named."""
    with cmd_turns.turns_path(indexed).open("a", encoding="utf-8") as handle:
        replies = ((PROMPT_1, "2026-11-06T10:00:31Z"), (PROMPT_3, "2999-01-01T00:00:00Z"))
        for prompt_id, at in replies:
            reply = {
                "session": SESSION,
                "prompt_id": prompt_id,
                "at": at,
                "origin": "assistant",
                "role": cmd_turns.REPLY_ROLE,
                "source": "hook",
            }
            handle.write(json.dumps(reply) + "\n")
    assert kyo("discuss", "skip", "--why", "a remark").code == 0
    assert kyo("discuss", "skip", "--at-prompt", PROMPT_1, "--why", "another").code == 0
    lines = cmd_turns.read_lines(indexed)
    skipped = [(line["prompt_id"], line.get("role")) for line in lines if "skip" in line]
    assert sorted(skipped) == [(PROMPT_1, None), (PROMPT_3, None)]
    assert silent(stop(kyo, PROMPT_1, "I recommend keeping it."))


def test_skip_refuses_without_a_reason_and_with_one_it_is_recorded(kyo, indexed: Path) -> None:
    refused = kyo("discuss", "skip")
    assert refused.code == 64 and '--why "<one line>"' in refused.err
    assert kyo("discuss", "skip", "--why", "a remark").code == 0


def test_skip_refuses_where_there_is_no_index(kyo, project: Path, session: str) -> None:
    no_index = kyo("discuss", "skip", "--why", "x")
    assert no_index.code == 64 and "nothing to skip here" in no_index.err


def test_the_check_asks_once_per_turn(kyo, indexed: Path) -> None:
    """`stop_hook_active` marks the stop that follows a Stop hook's feedback: nothing is
    said then, whatever the agent did with the first prompt."""
    assert feedback(stop(kyo, PROMPT_1, "I recommend keeping it."))
    assert silent(stop(kyo, PROMPT_1, "I recommend keeping it.", stop_hook_active=True))


def test_the_check_says_nothing_when_no_cue_fires(kyo, indexed: Path) -> None:
    assert silent(stop(kyo, PROMPT_3, "Kept."))
    assert stop(kyo, PROMPT_3, "Kept.", json_flag=True).json["why"] == "no cue fired"


def test_a_reply_that_puts_a_choice_fires_on_a_turn_the_person_did_not_open(
    kyo, indexed: Path
) -> None:
    words = feedback(stop(kyo, PROMPT_2, "The run finished. Do you want it merged?"))
    assert words.startswith("your reply in the turn opened at ")
    assert f"(prompt {PROMPT_2[:8]})" in words and CUE_PERSON not in words


def test_the_question_tool_fires(kyo, indexed: Path) -> None:
    append_transcript(
        [
            _record(
                "assistant",
                "a-3",
                "2026-11-06T10:01:10.000Z",
                message={
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "AskUserQuestion", "input": {}}],
                },
            )
        ]
    )
    assert CUE_ASKED in feedback(stop(kyo, PROMPT_3, "Recorded."))


def test_a_turn_naming_a_record_no_open_discussion_is_about_fires_again(
    kyo, project: Path, session: str
) -> None:
    opt_in_transcripts(project)
    prefix = load_config(project).project_prefix
    first, second = f"{prefix}-901", f"{prefix}-902"  # named, and need not exist
    write_transcript(
        [
            person("p-1", PROMPT_1, f"should {first} stay?", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "Yes.", "2026-11-06T10:00:30.000Z"),
            person("p-2", PROMPT_2, f"and what about {second}?", "2026-11-06T10:01:00.000Z"),
            agent_text("a-2", "It can go.", "2026-11-06T10:01:30.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    assert kyo("discuss", "open", "--title", f"Whether {first} stays").code == 0
    prompt_hook(kyo, PROMPT_2)
    assert silent(stop(kyo, PROMPT_1, "Yes."))
    words = feedback(stop(kyo, PROMPT_2, "It can go."))
    assert f"it names {second}, which no discussion open in this session is about" in words
    assert kyo("discuss", "open", "--title", f"Whether {second} goes").code == 0
    assert silent(stop(kyo, PROMPT_2, "It can go."))


@pytest.mark.parametrize(
    "stdin",
    ["", "not json", "[]", json.dumps({"hook_event_name": "Stop", "session_id": SESSION})],
)
def test_the_check_is_silent_and_exits_zero_on_a_payload_it_cannot_use(
    kyo, indexed: Path, stdin: str
) -> None:
    assert silent(kyo("discuss", "check", "--hook", stdin=stdin))


def test_the_check_is_silent_when_the_transcript_is_missing(kyo, indexed: Path) -> None:
    transcript_path().unlink()
    assert silent(stop(kyo, PROMPT_1, "I recommend keeping it."))
    told = stop(kyo, PROMPT_1, "I recommend keeping it.", json_flag=True).json
    assert told["ok"] is True and told["fire"] is False and "could not check" in told["why"]


def test_the_check_is_silent_in_a_project_that_has_not_opted_in(
    kyo, project: Path, session: str
) -> None:
    write_transcript(
        [
            person("p-1", PROMPT_1, "should the cache stay?", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "I recommend keeping it.", "2026-11-06T10:00:30.000Z"),
        ]
    )
    assert silent(stop(kyo, PROMPT_1, "I recommend keeping it."))


def test_check_without_hook_is_refused_naming_the_flag(kyo, project: Path) -> None:
    refused = kyo("discuss", "check")
    assert refused.code == 64 and "kyo discuss check --hook" in refused.err
    assert kyo("discuss", "check", "--hook", stdin="").code == 0
    misplaced = kyo("discuss", "skip", "--hook", "--why", "x")
    assert misplaced.code == 64 and "--hook belongs to `kyo discuss check`" in misplaced.err


def test_the_check_is_silent_where_no_project_resolves(tmp_path: Path) -> None:
    """A plugin's hooks fire in every session, Kyosei project or not (KYO-591)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run = invoke(["--root", str(elsewhere), "discuss", "check", "--hook"], stdin="{}")
    assert silent(run), run


# ------------------------------------------------------------------ the hook group


def test_the_check_has_its_own_stop_only_group_in_the_packaged_fragment() -> None:
    """KYO-774's layout: a failing sweep or doctor cannot skip the check, because it is in
    no `&&` chain with them, and it is installed under Stop alone."""
    fragment = json.loads(
        (Path(install_hooks.__file__).parent / "defaults" / "hooks" / "stop.json").read_text(
            encoding="utf-8"
        )
    )
    groups = [
        group
        for group in fragment["Stop"]
        if any("discuss check --hook" in hook["command"] for hook in group["hooks"])
    ]
    (group,) = groups
    (hook,) = group["hooks"]
    assert group["events"] == ["Stop"]
    assert hook["command"].endswith("; kyo discuss check --hook")
    assert "sweep" not in hook["command"] and "doctor" not in hook["command"]
    under_stop = install_hooks._load_hook_fragment("stop.json", event="Stop")
    under_end = install_hooks._load_hook_fragment("stop.json", event="SessionEnd")
    assert "discuss check --hook" in json.dumps(under_stop)
    assert "discuss check --hook" not in json.dumps(under_end)


# ------------------------------------------------------------------ KYO-662.c3: doctor


def _findings(kyo, name: str) -> tuple[list[str], list[str]]:
    result = kyo("doctor", "--json").json
    return (
        [found for found in result["problems"] if name in found],
        [found for found in result["notices"] if name in found],
    )


def test_doctor_reports_weekly_shares_of_live_late_and_unindexed(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    assert _findings(kyo, "discussion_shares") == ([], [])
    flags = _decision_flags(indexed, tmp_path)
    live = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1, "--json").json["id"]
    assert kyo("spawn", live, "--kind", "decision", "--title", "Live", *flags).code == 0
    late = kyo(
        "new", "decision", "--title", "Late", *flags, "--opened-late", "--at-prompt", PROMPT_1
    )
    assert late.code == 0, late
    # A discussion opened where no session is known names no turn of the index.
    os.environ.pop("CLAUDE_CODE_SESSION_ID")
    absent = kyo(
        "new", "decision", "--title", "Unindexed", *flags, "--source-other", "a call",
        "--opened-late",
    )  # fmt: skip
    assert absent.code == 0, absent
    os.environ["CLAUDE_CODE_SESSION_ID"] = SESSION
    assert kyo("discuss", "skip", "--at-prompt", PROMPT_1, "--why", "a remark").code == 0

    problems, (notice,) = _findings(kyo, "discussion_shares")
    assert problems == []
    assert "3 decision(s) filed from a discussion" in notice
    assert "opened live 1 (33%), late 1 (33%), unindexed 1 (33%)" in notice
    assert "1 Stop-check prompt(s) skipped" in notice and "week 20" in notice


def _stops(project: Path, session_id: str, count: int) -> None:
    for _ in range(count):
        cmd_sessions.record_event(
            project, session_id=session_id, event="Stop", cwd=str(project), transcript_path=None
        )


def test_a_session_with_stops_and_no_index_lines_is_a_problem(
    kyo, project: Path, session: str
) -> None:
    opt_in_transcripts(project)
    _stops(project, SESSION, 1)
    assert _findings(kyo, "sessions_unindexed") == ([], [])  # one Stop can precede the turn hook
    _stops(project, SESSION, 1)
    (problem,), notices = _findings(kyo, "sessions_unindexed")
    assert notices == [] and SESSION in problem and "2 stops" in problem
    assert "kyo install-hooks" in problem and "start a new session" in problem
    assert kyo("doctor").code != 0


def test_a_later_indexed_prompt_clears_the_problem(
    kyo, project: Path, session: str
) -> None:
    """The remedy the problem names: with the hooks running, a new session's prompt is
    indexed, and the earlier session is no longer reported."""
    opt_in_transcripts(project)
    _stops(project, SESSION, 2)
    assert _findings(kyo, "sessions_unindexed")[0]
    later = {
        "session_id": "9d9d9d9d-2222-4a2b-9c3d-0123456789ab",
        "hook_event_name": "UserPromptSubmit",
        "prompt_id": PROMPT_2,
        "prompt": "words",
    }
    assert kyo("turns", "--hook", stdin=json.dumps(later)).code == 0
    assert _findings(kyo, "sessions_unindexed") == ([], [])


def test_a_session_with_index_lines_is_not_a_problem(kyo, indexed: Path) -> None:
    _stops(indexed, SESSION, 3)
    assert _findings(kyo, "sessions_unindexed") == ([], [])


def test_a_project_that_keeps_no_index_has_no_such_problem(kyo, project: Path) -> None:
    _stops(project, SESSION, 3)
    assert _findings(kyo, "sessions_unindexed") == ([], [])


def test_a_session_entry_counts_its_stops_and_keeps_the_last_stop_time() -> None:
    lookup = cmd_sessions.find_bridge_session(None)
    entries: list[dict] = []
    for event, stamp in (
        ("SessionStart", "2026-11-06T10:00:00.000Z"),
        ("Stop", "2026-11-06T10:01:00.000Z"),
        ("Stop", "2026-11-06T10:02:00.000Z"),
        ("SessionEnd", "2026-11-06T10:03:00.000Z"),
    ):
        entries, entry = cmd_sessions.apply_event(
            entries, session_id=SESSION, event=event, cwd=None, lookup=lookup, stamp=stamp
        )
    assert entry["stops"] == 2 and entry["stopped_at"] == "2026-11-06T10:02:00.000Z"


# ------------------------------------------------------------------ KYO-662.c1: the measure


def _measure_module():
    spec = importlib.util.spec_from_file_location("kyo662_measure_detector", MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_measuring_script_counts_recall_and_fires_outside_spans(tmp_path: Path) -> None:
    """The script on an invented session: two spans, one caught at its first turn and one
    missed, and one firing turn outside both."""
    path = tmp_path / "t" / f"{SESSION}.jsonl"
    path.parent.mkdir()
    turns = [
        ("p-1", PROMPT_1, "should the cache stay?", "Yes."),  # span one: caught
        ("p-2", PROMPT_2, "keep it", "Kept."),  # span one's last turn
        ("p-3", PROMPT_3, "drop the queue", "Dropped."),  # span two: missed
        ("p-4", "aaaaaaaa-0000-4000-8000-000000000004", "what about the log?", "Rotated."),
    ]
    records = []
    for minute, (uuid, prompt_id, words, reply) in enumerate(turns):
        records.append(person(uuid, prompt_id, words, f"2026-11-06T10:0{minute}:00.000Z"))
        records.append(agent_text(f"a-{uuid}", reply, f"2026-11-06T10:0{minute}:30.000Z"))
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    spans = tmp_path / "spans.tsv"
    spans.write_text(
        "session\topening_uuid\tfirst_uuid\tlast_uuid\n"
        f"{SESSION}\t\tp-1\tp-2\n"
        f"{SESSION}\t\tp-3\tp-3\n",
        encoding="utf-8",
    )
    result = _measure_module().measure([path.parent], spans)
    assert result["agent_turns"] == 4 and result["spans_eligible"] == 2
    assert result["spans_caught"] == 1 and result["recall"] == 0.5
    assert result["fires"] == 2 and result["fires_outside_every_span"] == 1
    assert SESSION not in json.dumps(result) and "cache" not in json.dumps(result)


@pytest.mark.skipif(
    not (os.environ.get(ENV_TRANSCRIPTS) and os.environ.get(ENV_SPANS)),
    reason=f"the private transcripts and spans are named by ${ENV_TRANSCRIPTS} and ${ENV_SPANS}",
)
def test_the_detector_meets_its_recall_and_firing_bounds_on_the_private_transcripts() -> None:
    """KYO-662.c1, where the maintainer's transcripts are present. Nothing from them is
    asserted on or printed but counts."""
    directories = [
        Path(part).expanduser()
        for part in os.environ[ENV_TRANSCRIPTS].split(os.pathsep)
        if part
    ]
    result = _measure_module().measure(directories, Path(os.environ[ENV_SPANS]).expanduser())
    assert result["spans_eligible"] > 0
    assert result["recall"] >= 0.84, result
    assert result["fires_outside_every_span"] <= 260, result
