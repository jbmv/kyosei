"""KYO-661 (decision KYO-654, DC9, candidate C4'): a dispatch names the prompt it answered.

A dispatch made in a turn the person opened records that prompt; one made in a turn a
notification opened records that it was not typed by the person; with no session, no
opted-in index or no line it records nothing and says so. The pointer states where the
work began, never that the work was approved.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from .conftest import new_record, opt_in_transcripts
from .test_kyo658_discussions import (
    PROMPT_1,
    PROMPT_2,
    SESSION,
    agent_text,
    notification,
    person,
    prompt_hook,
    write_transcript,
)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


def _person_turn(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript([person("p-1", PROMPT_1, "go on", "2026-11-06T10:00:00.000Z")])
    prompt_hook(kyo, PROMPT_1)


def _notification_turn(kyo, project: Path) -> None:
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "go on", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "dispatched", "2026-11-06T10:00:30.000Z"),
            notification("n-2", PROMPT_2, "2026-11-06T10:00:40.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    prompt_hook(kyo, PROMPT_2)


def test_a_dispatch_made_in_a_turn_the_person_opened_names_that_prompt(
    kyo, git_project: Path, session: str
) -> None:
    _person_turn(kyo, git_project)
    record = new_record(kyo, criteria=["a"])
    made = kyo("dispatch", record, "--json")
    assert made.code == 0, made
    assert made.json["answered_prompt"]["prompt_id"] == PROMPT_1
    assert made.json["answered_prompt"]["origin"] == "human"
    shown = kyo("show", record)
    assert f"answered prompt: prompt {PROMPT_1[:8]}" in shown.out
    assert "typed by the person" in shown.out
    exported = json.loads(
        (git_project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    assert exported["dispatches"][0]["answered_prompt"]["prompt_id"] == PROMPT_1


def test_a_dispatch_made_in_a_turn_a_notification_opened_says_so(
    kyo, git_project: Path, session: str
) -> None:
    _notification_turn(kyo, git_project)
    record = new_record(kyo, criteria=["a"])
    made = kyo("dispatch", record, "--json")
    assert made.code == 0, made
    prompt = made.json["answered_prompt"]
    assert prompt["prompt_id"] == PROMPT_2
    assert prompt["origin"] == "task-notification"
    shown = kyo("show", record)
    assert "opened by a notification, not typed by the person" in shown.out
    assert "typed by the person)" not in shown.out


def test_with_no_session_or_no_index_nothing_is_recorded_and_show_says_so(
    kyo, git_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    record = new_record(kyo, criteria=["a"])
    made = kyo("dispatch", record, "--json")
    assert made.code == 0, made
    assert made.json["answered_prompt"] is None
    assert "CLAUDE_CODE_SESSION_ID" in made.json["answered_prompt_note"]
    assert "not recorded" in kyo("show", record).out
    # a session in a project that has not opted in has no index either
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    other = new_record(kyo, criteria=["a"])
    again = kyo("dispatch", other, "--force", "--json")
    assert again.code == 0, again
    assert again.json["answered_prompt"] is None
    assert "opted in" in again.json["answered_prompt_note"]


def test_the_prompt_survives_export_and_rebuild(kyo, git_project: Path, session: str) -> None:
    _person_turn(kyo, git_project)
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record).code == 0
    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    conn = sqlite3.connect(git_project / "kyosei" / "kyosei.db")
    try:
        (stored,) = conn.execute("SELECT answered_prompt_json FROM dispatches").fetchone()
    finally:
        conn.close()
    assert json.loads(stored)["prompt_id"] == PROMPT_1


def test_a_schema_33_export_rebuilds_with_no_prompt(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record).code == 0
    path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema_version"] = 33
    del doc["dispatches"][0]["answered_prompt"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0, "a schema 33 export must rebuild"
    conn = sqlite3.connect(git_project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute("SELECT answered_prompt_json FROM dispatches").fetchall() == [(None,)]
    finally:
        conn.close()
    assert "not recorded" in kyo("show", record).out
