"""The turn index and the transcript archive (KYO-657, KYO-683)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from kyosei import cmd_turns, transcript_archive
from kyosei.config import load_config
from kyosei.harness import claude_code_transcript as transcript
from kyosei.scaffold import KYOSEI_GITIGNORE

from .conftest import declare_person, new_record, opt_in_transcripts

SESSION = "7c1d2e3f-1111-4a2b-9c3d-0123456789ab"
AFTER_CLEAR = "7c1d2e3f-2222-4a2b-9c3d-0123456789ab"
PROMPT_1 = "aaaaaaaa-0000-4000-8000-000000000001"
PROMPT_2 = "aaaaaaaa-0000-4000-8000-000000000002"
PROMPT_3 = "aaaaaaaa-0000-4000-8000-000000000003"
SECRET = "words that must never reach the index"
HUMAN_OBSERVED = ("--verdict", "met", "--method", "human-observed")


def person(uuid: str, prompt_id: str, text: str, stamp: str) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "promptId": prompt_id,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        "origin": {"kind": "human"},
        "promptSource": "typed",
        "message": {"role": "user", "content": text},
    }


def tool_result(uuid: str, prompt_id: str, stamp: str) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "promptId": prompt_id,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        "toolUseResult": {"stdout": "ok"},
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }


def notification(uuid: str, prompt_id: str, stamp: str) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "promptId": prompt_id,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        "origin": {"kind": "task-notification", "producer": "session-task"},
        "promptSource": "system",
        "message": {"role": "user", "content": "<task-notification>done</task-notification>"},
    }


def queued(uuid: str, text: str, stamp: str, kind: str = "human") -> dict:
    """A mid-turn message as measured: an attachment with no promptId."""
    return {
        "type": "attachment",
        "uuid": uuid,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        "attachment": {
            "type": "queued_command",
            "prompt": text,
            "commandMode": "prompt",
            "origin": {"kind": kind},
            "timestamp": stamp,
        },
    }


def transcript_path(session: str = SESSION) -> Path:
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    return config / "projects" / "-a-project" / f"{session}.jsonl"


def write_transcript(records: list[dict], session: str = SESSION) -> Path:
    path = transcript_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def append_transcript(records: list[dict], session: str = SESSION) -> None:
    with transcript_path(session).open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(r) + "\n" for r in records))


def prompt_hook(kyo, prompt_id: str, prompt: str = SECRET, session: str = SESSION):
    payload = {
        "session_id": session,
        "hook_event_name": "UserPromptSubmit",
        "prompt_id": prompt_id,
        "prompt": prompt,
        "transcript_path": str(transcript_path(session)),
    }
    return kyo("turns", "--hook", stdin=json.dumps(payload))


def stop_hook(kyo, session: str = SESSION, event: str = "Stop"):
    payload = {
        "session_id": session,
        "hook_event_name": event,
        "transcript_path": str(transcript_path(session)),
    }
    return kyo("turns", "--hook", stdin=json.dumps(payload))


def index(project: Path) -> list[dict]:
    return cmd_turns.read_lines(project)


def archive_of(project: Path) -> Path:
    return transcript_archive.archive_dir(load_config(project))


# ------------------------------------------------------------------ KYO-657.c1


def test_the_hook_prints_nothing_and_writes_one_line_without_text(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    run = prompt_hook(kyo, PROMPT_1)
    assert run.code == 0 and run.out == "" and run.err == "", run
    (line,) = index(project)
    assert line["session"] == SESSION and line["prompt_id"] == PROMPT_1 and line["at"]
    assert line["origin"] == cmd_turns.UNCONFIRMED
    assert SECRET not in cmd_turns.turns_path(project).read_text(encoding="utf-8")
    prompt_hook(kyo, PROMPT_2)
    assert [entry["prompt_id"] for entry in index(project)] == [PROMPT_1, PROMPT_2]


def test_a_project_that_has_not_opted_in_gets_no_index(kyo, project: Path) -> None:
    run = prompt_hook(kyo, PROMPT_1)
    assert run.code == 0 and run.out == "", run
    assert not cmd_turns.turns_path(project).exists()
    write_transcript([person("p-1", PROMPT_1, "hello", "2026-11-08T10:00:00.000Z")])
    assert stop_hook(kyo).code == 0
    assert not cmd_turns.turns_path(project).exists()


def test_the_index_is_gitignored_from_a_new_store() -> None:
    patterns = KYOSEI_GITIGNORE.splitlines()
    assert "turns.jsonl" in patterns and "turns-cursor.json" in patterns
    assert "turns.jsonl.*.tmp" in patterns


# ------------------------------------------------------------------ KYO-657.c2


def test_a_mid_turn_message_the_hook_missed_is_added_from_the_transcript(
    kyo, project: Path
) -> None:
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "start", "2026-11-08T10:00:00.000Z"),
            queued("q-1", "and also this", "2026-11-08T10:00:05.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    assert stop_hook(kyo).code == 0
    first, added = index(project)
    assert first["prompt_id"] == PROMPT_1 and first["origin"] == "human"
    assert added["prompt_id"] is None and added["turn"] == "q-1"
    assert added["origin"] == "human" and added["source"] == cmd_turns.SOURCE_TRANSCRIPT
    assert added["at"] == "2026-11-08T10:00:05.000Z"
    stop_hook(kyo)
    assert len(index(project)) == 2, "a second Stop adds nothing"


def test_a_mid_turn_message_leaves_one_line_per_prompt(kyo, project: Path) -> None:
    """Both hook payloads carry the enclosing prompt's id, as they do in a real session."""
    opt_in_transcripts(project)
    prompt_hook(kyo, PROMPT_1, prompt="start")
    (before,) = index(project)
    prompt_hook(kyo, PROMPT_1, prompt=PASTE)
    assert index(project) == [before], "the repeated prompt id adds no line and changes none"
    write_transcript(
        [
            person("p-1", PROMPT_1, "start", "2026-11-08T10:00:00.000Z"),
            tool_result("t-1", PROMPT_1, "2026-11-08T10:00:03.000Z"),
            queued("q-1", PASTE, "2026-11-08T10:00:05.000Z"),
            tool_result("t-2", PROMPT_1, "2026-11-08T10:00:08.000Z"),
        ]
    )
    assert stop_hook(kyo).code == 0
    enclosing, message = index(project)
    assert enclosing["prompt_id"] == PROMPT_1 and enclosing["turn"] == "p-1"
    assert enclosing["origin"] == "human" and enclosing["source"] == cmd_turns.SOURCE_HOOK
    assert enclosing["at"] == before["at"] and enclosing["pasted"] is False
    assert message["prompt_id"] is None and message["turn"] == "q-1"
    assert message["origin"] == "human" and message["pasted"] is True
    assert message["at"] == "2026-11-08T10:00:05.000Z"
    assert cmd_turns.confirmed_paste_flags(project, SESSION) == {
        PROMPT_1: False,
        "p-1": False,
        "q-1": True,
    }
    stop_hook(kyo)
    assert index(project) == [enclosing, message], "a second Stop changes nothing"


def test_a_repeated_prompt_id_is_judged_within_its_own_session(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    prompt_hook(kyo, PROMPT_1)
    prompt_hook(kyo, PROMPT_1, session=AFTER_CLEAR)
    assert [line["session"] for line in index(project)] == [SESSION, AFTER_CLEAR]


def test_a_prompt_the_hook_never_saw_is_added_from_the_transcript(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript([person("p-1", PROMPT_1, "start", "2026-11-08T10:00:00.000Z")])
    assert stop_hook(kyo).code == 0
    (line,) = index(project)
    assert line["prompt_id"] == PROMPT_1 and line["source"] == cmd_turns.SOURCE_TRANSCRIPT


def test_a_stop_reads_only_what_was_written_since_the_last_one(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    stop_hook(kyo)
    facts, offset = transcript.read_turn_facts(transcript_path(), 0)
    assert len(facts) == 1 and offset == transcript_path().stat().st_size
    append_transcript([person("p-2", PROMPT_2, "two", "2026-11-08T10:01:00.000Z")])
    newer, _ = transcript.read_turn_facts(transcript_path(), offset)
    assert [fact.prompt_id for fact in newer] == [PROMPT_2]
    stop_hook(kyo)
    assert [line["prompt_id"] for line in index(project)] == [PROMPT_1, PROMPT_2]


def test_an_unfinished_last_line_is_left_for_the_next_read(tmp_path: Path) -> None:
    path = tmp_path / f"{SESSION}.jsonl"
    whole = json.dumps(person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")) + "\n"
    path.write_text(whole + '{"type": "user", "uu', encoding="utf-8")
    facts, offset = transcript.read_turn_facts(path, 0)
    assert len(facts) == 1 and offset == len(whole.encode("utf-8"))


# ------------------------------------------------------------------ KYO-657.c3


def test_prompt_ids_match_a_recorded_multi_turn_session(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "first", "2026-11-08T10:00:00.000Z"),
            tool_result("t-1", PROMPT_1, "2026-11-08T10:00:10.000Z"),
            person("p-2", PROMPT_2, "second", "2026-11-08T10:01:00.000Z"),
            tool_result("t-2", PROMPT_2, "2026-11-08T10:01:10.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    prompt_hook(kyo, PROMPT_2)
    stop_hook(kyo)
    lines = index(project)
    assert [(line["prompt_id"], line["turn"]) for line in lines] == [
        (PROMPT_1, "p-1"),
        (PROMPT_2, "p-2"),
    ], "one line per prompt, tied to the record that opened it and not its tool results"
    assert all(line["origin"] == "human" and line["source"] == "hook" for line in lines)


def test_a_task_notification_is_marked_not_the_persons(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript([notification("n-1", PROMPT_3, "2026-11-08T10:00:00.000Z")])
    prompt_hook(kyo, PROMPT_3, prompt="<task-notification>done</task-notification>")
    stop_hook(kyo)
    (line,) = index(project)
    assert line["origin"] == "task-notification"
    assert cmd_turns.confirmed_paste_flags(project, SESSION) == {}


def test_a_peers_queued_message_is_not_indexed_as_the_persons(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript([queued("q-1", "report", "2026-11-08T10:00:00.000Z", kind="peer")])
    stop_hook(kyo)
    assert index(project) == []


# ------------------------------------------------------------------ KYO-657.c4

PASTE = 'see <pasted_content id="8cf2">\nsomeone else wrote this\n</pasted_content id="8cf2"> ok'


def test_the_index_records_a_paste_and_the_transcript_settles_it(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, PASTE, "2026-11-08T10:00:00.000Z"),
            person("p-2", PROMPT_2, "typed only", "2026-11-08T10:01:00.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1, prompt=PASTE)
    prompt_hook(kyo, PROMPT_2, prompt="typed only")
    assert [line["pasted"] for line in index(project)] == [True, False]
    stop_hook(kyo)
    flags = cmd_turns.confirmed_paste_flags(project, SESSION)
    assert flags[PROMPT_1] is True and flags[PROMPT_2] is False


def test_a_paste_element_excludes_its_contents_without_any_history(tmp_path: Path) -> None:
    write_transcript([person("p-1", PROMPT_1, PASTE, "2026-11-08T10:00:00.000Z")])
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    outside = transcript.check_quote(SESSION, "see", config=config)
    assert outside.outcome == transcript.MATCHED, outside
    inside = transcript.check_quote(SESSION, "someone else wrote", config=config)
    assert inside.outcome == transcript.NOT_FOUND and "pasted" in inside.why, inside


def test_an_index_line_turns_paste_unknown_into_matched(kyo, project: Path) -> None:
    """A Remote Control turn is not in the prompt history, so only the index can settle it."""
    opt_in_transcripts(project)
    write_transcript([person("p-1", PROMPT_1, "keep the cache", "2026-11-08T10:00:00.000Z")])
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    before = transcript.check_quote(SESSION, "keep the cache", config=config)
    assert before.outcome == transcript.MATCHED_PASTE_UNKNOWN, before
    prompt_hook(kyo, PROMPT_1, prompt="keep the cache")
    stop_hook(kyo)
    after = transcript.check_quote(
        SESSION,
        "keep the cache",
        config=config,
        paste_flags=cmd_turns.confirmed_paste_flags(project, SESSION),
    )
    assert after.outcome == transcript.MATCHED, after


def test_an_unconfirmed_line_does_not_settle_a_paste(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    prompt_hook(kyo, PROMPT_1, prompt="keep the cache")
    assert cmd_turns.confirmed_paste_flags(project, SESSION) == {}


def test_a_credit_through_the_cli_reads_the_index(kyo, project: Path, tmp_path) -> None:
    opt_in_transcripts(project)
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    write_transcript([person("p-1", PROMPT_1, "it works here", "2026-11-08T10:00:00.000Z")])
    prompt_hook(kyo, PROMPT_1, prompt="it works here")
    stop_hook(kyo)
    words = tmp_path / "w"
    words.write_text("it works here", encoding="utf-8")
    ok = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--session", SESSION,
             "--evidence-file", str(words))  # fmt: skip
    assert ok.code == 0, ok
    assert _transcript_check(project) == "matched"


def _transcript_check(project: Path) -> str:
    with sqlite3.connect(project / "kyosei" / "kyosei.db") as conn:
        return conn.execute("SELECT transcript_check FROM credits").fetchone()[0]


# ------------------------------------------------------------------ KYO-657.c5


def _notices(kyo) -> list[str]:
    return kyo("doctor", "--json").json["checks"]["transcript_retention"]["failures"]


def _settings(value: object) -> None:
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(
        json.dumps({"cleanupPeriodDays": value}), encoding="utf-8"
    )


def test_doctor_gives_a_notice_for_an_unset_period_and_setting_it_clears(
    kyo, project: Path
) -> None:
    opt_in_transcripts(project)
    (notice,) = _notices(kyo)
    assert "cleanupPeriodDays" in notice and "unset" in notice
    _settings(36500)
    assert _notices(kyo) == []


def test_doctor_gives_a_notice_for_a_short_period(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    _settings(30)
    (notice,) = _notices(kyo)
    assert "30 days" in notice
    _settings(365)
    assert _notices(kyo) == []


def test_the_retention_notice_is_a_notice_and_needs_the_opt_in(kyo, project: Path) -> None:
    assert _notices(kyo) == []
    opt_in_transcripts(project)
    run = kyo("doctor", "--json")
    assert run.json["checks"]["transcript_retention"]["tier"] == "notice"


# ------------------------------------------------------------------ KYO-683


def test_the_stop_hook_copies_the_transcript_and_its_subagents(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    path = write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    subagents = path.parent / SESSION / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-a.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    assert stop_hook(kyo).code == 0
    archive = archive_of(project)
    assert (archive / path.name).read_bytes() == path.read_bytes()
    assert (archive / SESSION / "subagents" / "agent-a.jsonl").read_text() == '{"a": 1}\n'
    assert stop_hook(kyo, event="SessionEnd").code == 0, "SessionEnd runs the same copy"
    assert (archive / path.name).stat().st_mode & 0o777 == 0o600


def test_a_second_run_with_no_new_rows_writes_nothing(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    path = write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    stop_hook(kyo)
    copy = archive_of(project) / path.name
    before = copy.stat().st_mtime_ns
    run = kyo("--json", "turns", "--hook", stdin=json.dumps(
        {"session_id": SESSION, "hook_event_name": "Stop", "transcript_path": str(path)}
    ))  # fmt: skip
    assert run.json["archived"] == [] and run.json["bytes_copied"] == 0, run
    assert copy.stat().st_mtime_ns == before


def test_only_new_bytes_are_appended(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    path = write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    stop_hook(kyo)
    append_transcript([person("p-2", PROMPT_2, "two", "2026-11-08T10:01:00.000Z")])
    copy = archive_of(project) / path.name
    size_before = copy.stat().st_size
    result = transcript_archive.archive_session(path, archive_of(project))
    assert result.bytes_copied == path.stat().st_size - size_before
    assert copy.read_bytes() == path.read_bytes()


def test_a_clear_gives_the_next_transcript_its_own_copy(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    first = write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    stop_hook(kyo)
    second = write_transcript(
        [person("p-2", PROMPT_2, "two", "2026-11-08T10:01:00.000Z")], session=AFTER_CLEAR
    )
    stop_hook(kyo, session=AFTER_CLEAR)
    archive = archive_of(project)
    assert (archive / first.name).is_file() and (archive / second.name).is_file()


def test_a_rewritten_transcript_keeps_the_earlier_copy(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    path = write_transcript(
        [
            person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z"),
            person("p-2", PROMPT_2, "two", "2026-11-08T10:01:00.000Z"),
        ]
    )
    stop_hook(kyo)
    original = path.read_bytes()
    write_transcript([person("p-3", PROMPT_3, "three", "2026-11-08T10:02:00.000Z")])
    stop_hook(kyo)
    archive = archive_of(project)
    assert (archive / path.name).read_bytes() == path.read_bytes()
    (kept,) = archive.glob(f"{SESSION}.rewritten-*.jsonl")
    assert kept.read_bytes() == original
    assert len(transcript.archived_transcripts(SESSION, archive)) == 2


def test_a_project_that_has_not_opted_in_archives_nothing(kyo, project: Path) -> None:
    write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    assert stop_hook(kyo).code == 0
    assert not archive_of(project).exists()


def test_a_credit_matches_an_archived_session_whose_original_was_deleted(
    kyo, project: Path, tmp_path
) -> None:
    opt_in_transcripts(project)
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    path = write_transcript([person("p-1", PROMPT_1, "the archive has it", "2026-11-08T10:00:00Z")])
    stop_hook(kyo)
    path.unlink()
    words = tmp_path / "w"
    words.write_text("the archive has it", encoding="utf-8")
    ok = kyo("verify", f"{record}.c1", *HUMAN_OBSERVED, "--session", SESSION,
             "--evidence-file", str(words))  # fmt: skip
    assert ok.code == 0, ok
    assert _transcript_check(project) == "matched"


def test_an_original_still_present_is_preferred_to_the_archive(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / f"{SESSION}.jsonl").write_text("", encoding="utf-8")
    path = write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    found = transcript.candidate_transcripts(
        SESSION, config=Path(os.environ["CLAUDE_CONFIG_DIR"]), archive=archive
    )
    assert found == [path]


def test_the_archive_is_outside_every_git_repository_of_a_split_store(tmp_path: Path) -> None:
    """A split store is a repository at $KYOSEI_HOME/<project>; the archive is a sibling."""
    home = Path(os.environ["KYOSEI_HOME"])
    store = home / "proj"
    (store / ".git").mkdir(parents=True)
    archive = home / "transcript-archive" / "proj"
    assert transcript_archive.archive_location_problem(archive) is None
    assert transcript_archive.enclosing_git_repository(store / "x") == store


def test_doctor_reports_an_archive_inside_a_git_repository(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opt_in_transcripts(project)
    assert kyo("doctor", "--json").json["checks"]["transcript_archive_location"]["failures"] == []
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    monkeypatch.setenv("KYOSEI_HOME", str(project / "inside"))
    (problem,) = kyo("doctor", "--json").json["checks"]["transcript_archive_location"]["failures"]
    assert str(project) in problem and "$KYOSEI_HOME" in problem


def test_the_hook_refuses_to_archive_inside_a_git_repository(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opt_in_transcripts(project)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    monkeypatch.setenv("KYOSEI_HOME", str(project / "inside"))
    write_transcript([person("p-1", PROMPT_1, "one", "2026-11-08T10:00:00.000Z")])
    run = stop_hook(kyo)
    assert run.code == 0 and run.out == "", run
    assert not (project / "inside").exists()
    assert len(index(project)) == 1, "the index does not depend on the archive"
