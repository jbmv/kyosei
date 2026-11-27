"""KYO-988: a session that ran, or is running, while the project had no session hook.

A session is listed for its project only once one of its hooks has run. Two writers fill
the gaps that leaves: the ``SessionStart`` hook lists the sessions whose transcripts sit
beside its own with no row (c4), and `kyo harness install claude-code` and `kyo harness
migrate`, run inside a Claude Code session, write that session's row and say what the
session still lacks (c5, c6).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kyosei import cmd_harness, cmd_sessions
from kyosei.cmd_sessions import (
    EVENT_ENABLED,
    EVENT_START,
    EVENT_STOP,
    EVENT_TRANSCRIPT,
    MAX_SESSIONS,
    SESSION_ID_ENV,
    STATE_ENDED,
    STATE_RUNNING,
    read_sessions,
    sessions_path,
    unlisted_transcripts,
    write_sessions,
)

from .conftest import invoke, opt_in_transcripts

OWN = "11111111-1111-4111-8111-111111111111"
EARLIER = "22222222-2222-4222-8222-222222222222"
FIRST = "33333333-3333-4333-8333-333333333333"


def hookless_transcript(directory: Path, session_id: str, cwd: str, start: str, end: str) -> Path:
    """A transcript shaped as the two of 2026-11-26 were: untimed records first, then the
    first timestamped one carrying the cwd, then turns, then untimed records last."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "mode", "sessionId": session_id},
        {"type": "permission-mode", "sessionId": session_id},
        {"type": "system", "timestamp": start, "sessionId": session_id, "cwd": cwd},
        {"type": "user", "timestamp": end, "sessionId": session_id, "cwd": cwd,
         "message": {"role": "user", "content": "hello"}},  # fmt: skip
        {"type": "last-prompt", "sessionId": session_id},
        {"type": "cost-state", "sessionId": session_id},
    ]
    path = directory / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def start_hook(project: Path, transcripts: Path, session_id: str = OWN):
    payload = {
        "session_id": session_id,
        "hook_event_name": EVENT_START,
        "cwd": str(project),
        "transcript_path": str(transcripts / f"{session_id}.jsonl"),
    }
    return invoke(["--root", str(project), "sessions", "--hook"], stdin=json.dumps(payload))


# ------------------------------------------------------------- c4: listed from a transcript


def test_c4_the_first_hooked_start_lists_the_sessions_that_ran_with_no_hook(
    project: Path, tmp_path: Path
) -> None:
    opt_in_transcripts(project)
    transcripts = tmp_path / "claude" / "projects" / "-work-proj"
    hookless_transcript(
        transcripts, FIRST, str(project), "2026-11-26T00:42:52.741Z", "2026-11-26T00:43:40.000Z"
    )
    hookless_transcript(
        transcripts, EARLIER, str(project), "2026-11-26T00:44:20.044Z", "2026-11-26T01:00:08.823Z"
    )
    # A subagent's transcript, and a file that is no session's, are not sessions.
    (transcripts / FIRST / "subagents").mkdir(parents=True)
    hookless_transcript(transcripts / FIRST / "subagents", OWN.replace("1", "4"), "/x", "a", "b")
    (transcripts / "notes.jsonl").write_text("{}\n", encoding="utf-8")
    assert read_sessions(sessions_path(project)).entries == []

    ran = start_hook(project, transcripts)
    assert (ran.code, ran.out) == (0, ""), ran
    entries = {e["session_id"]: e for e in read_sessions(sessions_path(project)).entries}
    assert set(entries) == {OWN, FIRST, EARLIER}, entries
    earlier = entries[EARLIER]
    assert earlier["state"] == STATE_ENDED and earlier["last_event"] == EVENT_TRANSCRIPT
    assert earlier["first_seen"] == "2026-11-26T00:44:20.044Z"
    assert earlier["last_seen"] == "2026-11-26T01:00:08.823Z"
    assert earlier["ended_at"] is None and earlier["pid"] is None and earlier["stops"] == 0
    assert earlier["cwd"] == str(project)
    assert cmd_sessions.expand_home(earlier["transcript_path"]) == transcripts / f"{EARLIER}.jsonl"
    assert entries[OWN]["state"] == STATE_RUNNING and entries[OWN]["last_event"] == EVENT_START
    # `kyo sessions`, the rows' command-line reader, says how they were listed.
    listing = invoke(["--root", str(project), "sessions"])
    assert "listed from its transcript by a later session's start" in listing.out

    # A second start adds nothing: each is listed once.
    again = start_hook(project, transcripts, "55555555-5555-4555-8555-555555555555")
    assert again.code == 0
    assert len(read_sessions(sessions_path(project)).entries) == 4


def test_c4_history_serves_a_session_listed_from_its_transcript(
    project: Path, tmp_path: Path
) -> None:
    """``GET sessions``, the Mac app's History, reads the file; the row is in it, ended."""
    from kyosei.serve_sessions import ProjectSessions

    opt_in_transcripts(project)
    transcripts = tmp_path / "claude" / "projects" / "-work-proj"
    hookless_transcript(
        transcripts, EARLIER, str(project), "2026-11-26T00:44:20.044Z", "2026-11-26T01:00:08.823Z"
    )
    assert start_hook(project, transcripts).code == 0
    rows = ProjectSessions(str(project), dict(os.environ)).list_sessions()["sessions"]
    listed = [row for row in rows if row["session_id"] == EARLIER]
    assert len(listed) == 1 and listed[0]["state"] == STATE_ENDED, rows
    assert listed[0]["live"] is False


def test_c4_nothing_is_read_in_a_project_that_has_not_opted_in(
    project: Path, tmp_path: Path
) -> None:
    transcripts = tmp_path / "claude" / "projects" / "-work-proj"
    hookless_transcript(transcripts, EARLIER, str(project), "a", "b")
    assert start_hook(project, transcripts).code == 0
    assert [e["session_id"] for e in read_sessions(sessions_path(project)).entries] == [OWN]


def test_c4_a_full_file_passes_over_transcripts_older_than_its_oldest_entry(
    tmp_path: Path,
) -> None:
    transcripts = tmp_path / "projects" / "p"
    old = hookless_transcript(transcripts, EARLIER, "/p", "a", "b")
    os.utime(old, (1_000_000_000, 1_000_000_000))  # 2001
    entries = [
        {"session_id": f"{index:08d}-0000-4000-8000-000000000000",
         "last_seen": "2026-01-01T00:00:00.000Z"}  # fmt: skip
        for index in range(MAX_SESSIONS)
    ]
    assert unlisted_transcripts(entries, transcripts, OWN) == []
    assert unlisted_transcripts(entries[:-1], transcripts, OWN) == [old]


# ------------------------------------------------- c5, c6: the session the command runs in

PLUGIN_STUB = """#!/bin/sh
if [ "$1 $2" = "plugin install" ]; then
  mkdir -p .claude
  printf '{"enabledPlugins": {"kyosei@kyosei": true}}\\n' > .claude/settings.local.json
fi
exit 0
"""


@pytest.fixture
def stub_claude(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    program = bin_dir / "claude"
    program.write_text(PLUGIN_STUB, encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def harness(project: Path, *words: str):
    return invoke(["--root", str(project), "harness", *words, "--json"])


def test_c5_install_inside_a_session_lists_that_session_with_its_process(
    project: Path, stub_claude: None, monkeypatch
) -> None:
    monkeypatch.setenv(SESSION_ID_ENV, OWN)
    process = cmd_sessions.SessionProcess(pid=4242, name="claude", host="workstation")
    monkeypatch.setattr(cmd_sessions, "session_process", lambda start=None: process)
    run = harness(project, "install", "claude-code")
    assert run.code == 0, run
    data = run.json
    assert data["plugin_enabled"] is True
    assert data["this_session"]["recorded"] is True, data["this_session"]
    (entry,) = read_sessions(sessions_path(project)).entries
    assert entry["session_id"] == OWN and entry["state"] == STATE_RUNNING
    assert entry["last_event"] == EVENT_ENABLED and entry["cwd"] == str(project.resolve())
    assert (entry["pid"], entry["process"], entry["host"]) == (4242, "claude", "workstation")
    assert entry["stops"] == 0 and entry["transcript_path"] is None
    text = invoke(["--root", str(project), "harness", "install", "claude-code"]).out
    assert "is listed for the project already" in text
    assert cmd_harness.RELOAD_COMMAND in text

    # Once the person reloads, the session's first Stop folds into the same row.
    payload = {"session_id": OWN, "hook_event_name": EVENT_STOP, "cwd": str(project),
               "transcript_path": None}  # fmt: skip
    hooked = invoke(["--root", str(project), "sessions", "--hook"], stdin=json.dumps(payload))
    assert hooked.code == 0, hooked
    (after,) = read_sessions(sessions_path(project)).entries
    assert after["first_seen"] == entry["first_seen"] and after["last_event"] == EVENT_STOP
    assert after["stops"] == 1


def test_c5_c6_the_output_names_the_reload_and_what_the_session_lacks(
    project: Path, stub_claude: None, monkeypatch
) -> None:
    monkeypatch.setenv(SESSION_ID_ENV, OWN)
    run = invoke(["--root", str(project), "harness", "install", "claude-code"])
    assert run.code == 0, run
    assert f"this Claude Code session ({OWN}) is listed for the project now" in run.out
    assert "the Mac app show it" in run.out
    assert "Tell the person to type /reload-plugins in this session now, or to start a new " \
        "session" in run.out
    assert "reach only sessions started afterwards" in run.out


def test_c6_outside_a_session_it_says_new_sessions_only_and_that_the_app_does_not_show_one(
    project: Path, stub_claude: None, monkeypatch
) -> None:
    monkeypatch.delenv(SESSION_ID_ENV, raising=False)
    run = invoke(["--root", str(project), "harness", "install", "claude-code"])
    assert run.code == 0, run
    assert "reach only the Claude Code sessions started in this project from now on" in run.out
    assert "Tell the person to start a new session" in run.out
    assert "the Mac app does not show a session that was running before this command" in run.out
    assert not sessions_path(project).exists()


def test_c6_a_session_that_cannot_be_listed_is_told_the_app_does_not_show_it(
    project: Path, stub_claude: None, monkeypatch
) -> None:
    monkeypatch.setenv(SESSION_ID_ENV, OWN)
    monkeypatch.setattr(
        cmd_sessions, "write_sessions", lambda path, entries: f"{path} could not be written"
    )
    run = invoke(["--root", str(project), "harness", "install", "claude-code"])
    assert run.code == 0, run
    assert "could not be listed for the project" in run.out
    assert "reach only sessions started afterwards" in run.out
    assert "start a new session" in run.out
    assert "the Mac app does not show this session until then" in run.out


def test_c5_an_existing_row_is_left_as_its_hooks_wrote_it(
    project: Path, stub_claude: None, monkeypatch
) -> None:
    hooked = {"session_id": OWN, "state": STATE_RUNNING, "last_event": EVENT_STOP,
              "last_seen": "2026-11-26T00:00:00.000Z", "stops": 3}  # fmt: skip
    write_sessions(sessions_path(project), [hooked])
    monkeypatch.setenv(SESSION_ID_ENV, OWN)
    run = harness(project, "install", "claude-code")
    assert run.code == 0, run
    assert run.json["this_session"]["recorded"] is False
    (entry,) = read_sessions(sessions_path(project)).entries
    assert entry["last_event"] == EVENT_STOP and entry["stops"] == 3


def test_c5_migrate_inside_a_session_lists_it_too(
    project: Path, stub_claude: None, monkeypatch
) -> None:
    monkeypatch.setenv(SESSION_ID_ENV, OWN)
    run = harness(project, "migrate")
    assert run.code == 0, run
    assert run.json["this_session"]["recorded"] is True
    (entry,) = read_sessions(sessions_path(project)).entries
    assert entry["session_id"] == OWN and entry["last_event"] == EVENT_ENABLED
