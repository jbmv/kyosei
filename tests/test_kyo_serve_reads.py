"""KYO-454: the desktop app's SQL and git reads as read-only commands.

`kyo briefs`, `kyo shift-notes`, `kyo gates` and `kyo activity` answer what
``apps/desktop/src/main/store.ts`` (readBriefRuns, readShiftNotesBetween,
readEventsBetween, readOpenGates, readOpenGateCounts, readDispatchRows,
readRecordRows) and ``gitfacts.ts`` read directly. c1: each answers with ``--json`` and
has a `kyo reference` entry. c2: each is served by `kyo serve`, runs on a store opened
read-only, and writes no row.

Every server here binds loopback on an ephemeral port and is stopped by the test that
started it; every repository lives under ``tmp_path``.
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import subprocess
from pathlib import Path

import pytest

from kyosei import cli, query, serve
from kyosei.errors import EXIT_USAGE

from .conftest import git_worktree, invoke, new_record
from .test_kyo_serve import register, start, stop

NEW_COMMANDS = ("briefs", "shift-notes", "gates", "activity")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=T", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def history(git_project: Path, kyo, write_file, tmp_path: Path) -> dict:
    """A store with two briefs, three shift notes, open and cleared gates, and two
    dispatches: one running in a live worktree with a commit, one closed whose
    directory is gone."""
    project = git_project
    live = new_record(kyo, title="Live dispatch", criteria=["it runs"])
    gone = new_record(kyo, title="Gone dispatch", criteria=["it went"])
    quiet = new_record(kyo, title="No dispatch", criteria=["nothing"])
    base = _git(project, "rev-parse", "HEAD")

    live_tree = tmp_path / "wt-live"
    git_worktree(project, live_tree)
    _git(live_tree, "checkout", "-q", "-b", "live-branch")
    dispatched = kyo("dispatch", live, "--worktree", str(live_tree), "--branch",
                     "live-branch", "--force", "--json")
    assert dispatched.code == 0, dispatched
    live_id = dispatched.json["dispatch_id"]
    worked_from = kyo("show", live_id, "--json").json["worked_from_sha"]
    (live_tree / "work.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(live_tree, "add", "work.txt")
    _git(live_tree, "commit", "-q", "-m", "the worker's commit")
    (live_tree / ".kyosei-report.json").write_text("{}\n", encoding="utf-8")
    (live_tree / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")

    gone_tree = tmp_path / "wt-gone"
    git_worktree(project, gone_tree)
    dispatched = kyo("dispatch", gone, "--worktree", str(gone_tree), "--force", "--json")
    assert dispatched.code == 0, dispatched
    gone_id = dispatched.json["dispatch_id"]
    closed = kyo("dispatch", "close", gone_id, "--terminated-no-report", "--why", "gone")
    assert closed.code == 0, closed
    _git(project, "worktree", "remove", "--force", str(gone_tree))

    gated = kyo("gate", live, "awaiting:human-review", "--do", "look at it", "--json")
    assert gated.code == 0, gated
    assert kyo("gate", gone, "awaiting:event", "--do", "a release").code == 0
    cleared = kyo("gate", quiet, "awaiting:human-run", "--do", "run it", "--json")
    assert cleared.code == 0, cleared
    cleared_id = kyo("show", quiet, "--json").json["gates"][0]["id"]
    assert kyo("ungate", cleared_id, "--why", "ran it").code == 0

    assert kyo("note", "--shift", "--file", write_file("n1.md", "the first note")).code == 0
    first = kyo("brief", "--session-start", "--json")
    assert first.code == 0, first
    assert kyo("note", "--shift", "--file", write_file("n2.md", "the second note")).code == 0
    second = kyo("brief", "--json")
    assert second.code == 0, second
    assert kyo("note", "--shift", "--file", write_file("n3.md", "after the last")).code == 0
    return {
        "project": project,
        "live": live,
        "gone": gone,
        "quiet": quiet,
        "live_id": live_id,
        "gone_id": gone_id,
        "live_tree": live_tree,
        "gone_tree": gone_tree,
        "base": base,
        "worked_from": worked_from,
        "cleared_gate": cleared_id,
    }


# ------------------------------------------------------------- c1: kyo briefs


def test_briefs_lists_every_run_newest_first_with_its_positions(history, kyo) -> None:
    listed = kyo("briefs", "--json")
    assert listed.code == 0, listed
    runs = listed.json["briefs"]
    assert [run["seq"] for run in runs] == [2, 1]
    newest, oldest = runs
    assert oldest["session_start"] is True and newest["session_start"] is False
    for key in ("id", "at", "actor", "last_event_seq", "last_shift_note_seq"):
        assert key in newest
    assert newest["last_shift_note_seq"] == 2 and oldest["last_shift_note_seq"] == 1
    assert newest["last_event_seq"] > oldest["last_event_seq"]


def test_one_brief_carries_what_lies_between_it_and_the_one_before(history, kyo) -> None:
    newest, oldest = kyo("briefs", "--json").json["briefs"]
    shown = kyo("briefs", newest["id"], "--json")
    assert shown.code == 0, shown
    assert shown.json["run"] == newest
    assert shown.json["previous_brief_id"] == oldest["id"]
    assert [note["text"] for note in shown.json["shift_notes"]] == ["the second note"]
    events = shown.json["events"]
    assert events and all(
        oldest["last_event_seq"] < event["seq"] <= newest["last_event_seq"] for event in events
    )
    assert [event["seq"] for event in events] == sorted(
        (event["seq"] for event in events), reverse=True
    )
    assert set(events[0]) == {"id", "seq", "at", "actor", "command", "record_id"}
    first = kyo("briefs", oldest["id"], "--json").json
    assert first["previous_brief_id"] is None
    assert [note["text"] for note in first["shift_notes"]] == ["the first note"]
    assert min(event["seq"] for event in first["events"]) == 1


def test_briefs_latest_is_the_newest_run_shown(history, kyo) -> None:
    newest = kyo("briefs", "--json").json["briefs"][0]
    latest = kyo("briefs", "--latest", "--json")
    assert latest.code == 0
    assert latest.json == kyo("briefs", newest["id"], "--json").json


def test_briefs_latest_on_a_store_with_none_is_an_answer(project, kyo) -> None:
    latest = kyo("briefs", "--latest", "--json")
    assert latest.code == 0, latest
    assert latest.json["run"] is None and latest.json["events"] == []


def test_an_unknown_brief_names_kyo_briefs_and_a_listed_id_clears_it(history, kyo) -> None:
    refused = kyo("briefs", "TST-B99", "--json")
    assert refused.code != 0
    assert "`kyo briefs`" in refused.json["error"]
    listed = kyo("briefs", "--json").json["briefs"]  # the remedy: read the list
    assert kyo("briefs", listed[0]["id"], "--json").code == 0


def test_a_brief_id_and_latest_together_are_refused(history, kyo) -> None:
    refused = kyo("briefs", "TST-B1", "--latest", "--json")
    assert refused.code == EXIT_USAGE
    assert "not both" in refused.json["error"]
    assert kyo("briefs", "TST-B1", "--json").code == 0  # the remedy: one of the two


# -------------------------------------------------------- c1: kyo shift-notes


def test_shift_notes_are_newest_first_with_the_handoff_marked(history, kyo) -> None:
    result = kyo("shift-notes", "--json")
    assert result.code == 0, result
    notes = result.json["notes"]
    assert [note["text"] for note in notes] == ["after the last", "the second note",
                                               "the first note"]
    session_start = kyo("briefs", "--json").json["briefs"][1]
    assert notes[2]["handoff_for"] == session_start["id"]
    # The second brief was not a session start, so the note it stood at hands off to none.
    assert notes[1]["handoff_for"] is None and notes[0]["handoff_for"] is None
    for key in ("id", "seq", "author", "created_at", "redacted_at", "redacted_why"):
        assert key in notes[0]


def test_shift_notes_latest_is_the_note_after_the_last_brief(history, kyo) -> None:
    latest = kyo("shift-notes", "--latest", "--json")
    assert latest.code == 0
    assert latest.json["note"]["text"] == "after the last"
    assert latest.json["note"] == kyo("shift-notes", "--json").json["notes"][0]


def test_shift_notes_latest_on_an_empty_log_is_null(project, kyo) -> None:
    assert kyo("shift-notes", "--latest", "--json").json["note"] is None


# ------------------------------------------------------------ c1: kyo gates


def test_gates_lists_open_gates_only_with_their_records(history, kyo) -> None:
    result = kyo("gates", "--json")
    assert result.code == 0, result
    gates = result.json["gates"]
    assert {gate["record_id"] for gate in gates} == {history["live"], history["gone"]}
    assert history["cleared_gate"] not in {gate["id"] for gate in gates}
    live = next(gate for gate in gates if gate["record_id"] == history["live"])
    assert live["type"] == "awaiting:human-review" and live["do"] == "look at it"
    assert live["record_title"] == "Live dispatch"
    assert live["record_kind"] == "bug" and live["record_status"]
    assert live["tags"] == [] and live["created_at"] and live["seq"] == 1
    assert result.json["count"] == 2 and result.json["types"] is None
    assert result.json["open_counts"] == {history["live"]: 1, history["gone"]: 1}


def test_gates_type_narrows_the_rows_and_not_the_counts(history, kyo) -> None:
    result = kyo("gates", "--type", "awaiting:human-review", "--type",
                 "awaiting:human-run", "--json")
    assert result.code == 0, result
    assert [gate["record_id"] for gate in result.json["gates"]] == [history["live"]]
    assert result.json["types"] == ["awaiting:human-review", "awaiting:human-run"]
    assert result.json["open_counts"] == {history["live"]: 1, history["gone"]: 1}


def test_an_unknown_gate_type_is_a_usage_error_naming_the_types(history, kyo) -> None:
    refused = kyo("gates", "--type", "awaiting:nobody")
    assert refused.code == EXIT_USAGE
    assert "awaiting:human-review" in refused.err
    assert kyo("gates", "--type", "awaiting:human-review").code == 0


# ---------------------------------------------------------- c1: kyo activity


def _rows(kyo, *extra: str) -> dict[str, dict]:
    result = kyo("activity", *extra, "--json")
    assert result.code == 0, result
    return {row["id"]: row for row in result.json["rows"]}


def test_activity_rows_carry_the_store_half(history, kyo) -> None:
    rows = _rows(kyo)
    live, gone = rows[history["live_id"]], rows[history["gone_id"]]
    assert list(rows) == [history["live_id"], history["gone_id"]]  # running first
    assert live["state"] == "running" and gone["state"] == "terminated-no-report"
    assert live["record_title"] == "Live dispatch" and live["record_kind"] == "bug"
    assert live["branch"] == "live-branch" and live["worked_from_sha"] == history["worked_from"]
    assert live["open_gates"] == 1
    assert live["classes"] == ["running", "gated"]
    assert gone["classes"] == ["terminated-no-report", "gated"]
    assert gone["terminated_why"] == "gone" and gone["closed_at"]
    for key in ("worker_model", "report_id", "created_at", "covers", "verdict_counts",
                "unaudited_stops", "worktree", "worktree_resolved"):
        assert key in live
    assert live["worktree_resolved"] == str(history["live_tree"].resolve())


def test_activity_rows_carry_worker_tokens_and_end_times(history, kyo) -> None:
    rows = _rows(kyo)
    for row in rows.values():
        assert isinstance(row["worker_tokens"]["outcome"], str)
        assert "known" in row["worker_tokens"] and "total_tokens" in row["worker_tokens"]
        assert isinstance(row["finished_at_why"], str)
    assert rows[history["live_id"]]["finished_at"] is None
    assert rows[history["gone_id"]]["finished_at_source"] is not None


def test_activity_git_facts_for_a_live_worktree(history, kyo) -> None:
    git = _rows(kyo)[history["live_id"]]["git"]
    assert git["outcome"] == "read" and git["present"] is True
    assert git["branch"] == "live-branch"
    assert git["commits_past_base"] == 1
    assert git["latest_commit_message"] == "the worker's commit"
    assert git["latest_commit_sha"] == _git(history["live_tree"], "rev-parse", "HEAD")
    assert (git["lines_added"], git["lines_removed"]) == (3, 0)
    assert git["merged_into"] is False
    assert git["report_file_present"] is True
    # An untracked file makes the tree dirty, and the row says which path (the report
    # file is gitignored in a Kyosei project, so it is not one).
    assert git["dirty"] is True and git["dirty_paths"] == ["?? scratch.txt"]


def test_activity_git_facts_merged_and_clean(history, kyo) -> None:
    (history["live_tree"] / ".kyosei-report.json").unlink()
    (history["live_tree"] / "scratch.txt").unlink()
    _git(history["project"], "merge", "-q", "--ff-only", "live-branch")
    git = _rows(kyo)[history["live_id"]]["git"]
    assert git["dirty"] is False and git["dirty_paths"] == []
    assert git["merged_into"] is True and git["report_file_present"] is False
    assert _rows(kyo, "--against", "no-such-ref")[history["live_id"]]["git"][
        "merged_into"
    ] is None


def test_a_gone_worktree_is_a_fact_not_an_error(history, kyo) -> None:
    git = _rows(kyo)[history["gone_id"]]["git"]
    assert git["outcome"] == "directory-gone" and git["present"] is False
    assert git["dirty"] is None and git["commits_past_base"] is None
    assert "is not there" in git["why"]


def test_a_gone_worktree_takes_its_lines_from_the_code_repository(history, kyo) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(history["live_tree"])],
                   cwd=history["project"], check=True, capture_output=True)
    git = _rows(kyo)[history["live_id"]]["git"]
    assert git["outcome"] == "directory-gone"
    assert (git["lines_added"], git["lines_removed"]) == (3, 0)


def test_activity_finished_is_the_window_beside_every_running_one(history, kyo) -> None:
    assert list(_rows(kyo, "--finished", "0")) == [history["live_id"]]
    result = kyo("activity", "--finished", "0", "--json").json
    assert result["running"] == 1 and result["finished_shown"] == 0
    assert result["finished_total"] == 1


def test_a_negative_finished_is_refused_and_zero_clears_it(history, kyo) -> None:
    refused = kyo("activity", "--finished", "-1", "--json")
    assert refused.code == EXIT_USAGE and "--finished" in refused.json["error"]
    assert kyo("activity", "--finished", "0", "--json").code == 0


def test_verdict_counts_are_sorted_and_name_a_missing_verdict() -> None:
    body = (
        '{"criteria_verdicts": [{"verdict": "met"}, {"verdict": "unmet-unobserved"}, '
        '{"verdict": "met"}, {}]}'
    )
    assert query.verdict_counts(body) == [
        {"verdict": "(no verdict)", "count": 1},
        {"verdict": "met", "count": 2},
        {"verdict": "unmet-unobserved", "count": 1},
    ]
    assert query.verdict_counts(None) == [] and query.verdict_counts("not json") == []


def test_a_row_without_a_worktree_says_so() -> None:
    from kyosei.cmd_worktrees import git_facts

    facts = git_facts(None, "abc")
    assert facts["outcome"] == "no-worktree-declared" and facts["present"] is False


def test_a_directory_that_is_not_a_worktree_says_so(tmp_path: Path) -> None:
    from kyosei.cmd_worktrees import git_facts

    plain = tmp_path / "plain"
    plain.mkdir()
    facts = git_facts(plain, None)
    assert facts["outcome"] == "not-a-git-worktree" and facts["present"] is True


# --------------------------------------------------- c1: a kyo reference entry


@pytest.mark.parametrize("command", NEW_COMMANDS)
def test_each_new_command_has_a_reference_entry(command: str) -> None:
    result = invoke(["reference", command, "--json"])
    assert result.code == 0, result
    assert f"kyo {command}" in result.out


# ------------------------------------------- c2: served, read-only, no row written


@pytest.mark.parametrize("command", NEW_COMMANDS)
def test_each_new_command_is_served_and_not_mutating(command: str) -> None:
    assert command in serve.SERVED_COMMANDS
    assert command not in serve.NOT_SERVED
    assert command not in cli.MUTATING_COMMANDS


def _snapshot(project: Path) -> dict[str, list[tuple]]:
    """Every row of every table, and every export file's bytes."""
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        snapshot = {
            table: sorted(map(tuple, conn.execute(f'SELECT * FROM "{table}"')), key=repr)
            for table in tables
        }
    finally:
        conn.close()
    export = project / "kyosei" / "export"
    snapshot["<export>"] = sorted(
        (str(path.relative_to(export)), path.read_bytes())
        for path in export.rglob("*")
        if path.is_file()
    )
    return snapshot


def _quiet_run(argv: list[str], **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cli.run(argv, **kwargs)


def _argvs(history: dict) -> list[list[str]]:
    return [
        ["briefs"],
        ["briefs", "--latest"],
        ["briefs", "TST-B1"],
        ["shift-notes"],
        ["shift-notes", "--latest"],
        ["gates"],
        ["gates", "--type", "awaiting:event"],
        ["activity"],
        ["activity", "--finished", "0"],
    ]


def test_each_new_read_runs_read_only_and_writes_no_row(history) -> None:
    project = history["project"]
    before = _snapshot(project)
    for argv in _argvs(history):
        code, payload = _quiet_run(
            ["--root", str(project), *argv, "--json"], read_only=True
        )
        assert code == 0, (argv, payload)
        assert payload["ok"] is True
    assert _snapshot(project) == before


def test_the_snapshot_sees_a_write(history) -> None:
    """The check above is only worth something if a write would change the snapshot:
    `kyo brief` writes a brief_runs row, and it does."""
    project = history["project"]
    before = _snapshot(project)
    assert _quiet_run(["--root", str(project), "brief", "--json"])[0] == 0
    assert _snapshot(project) != before


@pytest.fixture
def served_history(history, store_home: Path):
    register(store_home, history["project"])
    client = start()
    try:
        yield client
    finally:
        stop(client)


SERVED_ROUTES = [
    ("briefs", ["briefs"]),
    ("briefs/TST-B2", ["briefs", "TST-B2"]),
    ("briefs?latest", ["briefs", "--latest"]),
    ("shift-notes", ["shift-notes"]),
    ("shift-notes?latest", ["shift-notes", "--latest"]),
    ("gates", ["gates"]),
    ("gates?type=awaiting:event", ["gates", "--type", "awaiting:event"]),
    ("activity?finished=5", ["activity", "--finished", "5"]),
]


@pytest.mark.parametrize(("route", "argv"), SERVED_ROUTES, ids=[r for r, _ in SERVED_ROUTES])
def test_a_served_read_is_byte_for_byte_the_clis_json(
    served_history, history, route: str, argv: list[str]
) -> None:
    project = history["project"]
    before = _snapshot(project)
    status, _, body = served_history.get(f"/v1/projects/proj/cmd/{route}")
    expected = invoke([*argv, "--root", str(project), "--json"])
    assert expected.code == 0, expected
    assert status == 200
    assert body.decode("utf-8") == expected.out
    assert _snapshot(project) == before
