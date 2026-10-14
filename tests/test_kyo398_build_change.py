"""KYO-398: a session is told when the `kyo` under it changed, and build dates are UTC.

The measured defect: a consumer session told its maintainer that two fixes
(KYO-396, KYO-397) were not in the Kyosei version it had, a build dated 2026-09-24.
Both were ancestors of that build and the installed runtime carried them byte for byte.
Two clocks produced the misreading: the build was dated by the committer's LOCAL date
(22:34 -0400 -> 2026-09-24) while the changelog dated the records it carried by UTC
event times (02:34Z -> 2026-09-25), and nothing said the listed records were IN
the build. Separately, since KYO-335 every project runs one bundled `kyo`, so a re-pack
changes the tool under every project at once and nothing told any of them.

Sections follow the criteria: c1 (UTC build time), c2 (the changelog states
containment; entries carry full instants), c3 (each brief run records its build, with
rebuild defaults for the prior format), c4/c5 (the session-start notice), c6 (the
per-prompt hook).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from kyosei import buildinfo, changelog, cmd_check_build, install_hooks
from kyosei.buildinfo import BuildIdentity
from kyosei.changelog import Changelog, Entry

from .conftest import invoke

# ------------------------------------------------------------------- c1: UTC build time

#: The instant from the record: 22:34 at -0400 on the 24th is 02:34Z on the 25th.
LOCAL_EVENING = "2026-09-24T22:34:00-04:00"
SAME_INSTANT_UTC = "2026-09-25T02:34:00.000Z"


def _repo_committed_at(tmp_path: Path, when: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_DATE": when,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=repo, check=True, env=env)
    return repo


def test_a_commit_made_at_2234_minus_0400_is_dated_by_its_utc_day(tmp_path: Path) -> None:
    """c1's named test. The local reading (`%cI[:10]`) says 2026-09-24; the store's
    clock says 2026-09-25, and the build must be dated on the store's clock."""
    repo = _repo_committed_at(tmp_path, LOCAL_EVENING)
    live = buildinfo.git_identity(repo)
    assert live is not None
    assert live["commit_time"] == SAME_INSTANT_UTC
    assert live["commit_date"] == "2026-09-25", "the committer's local date leaked through"


def test_the_stamp_and_version_json_carry_the_full_utc_time(tmp_path: Path) -> None:
    """c1: the stamp an installed copy reads, and `--json`'s fields, carry the instant;
    the printed line carries its UTC date and says it is UTC."""
    repo = _repo_committed_at(tmp_path, LOCAL_EVENING)
    package = tmp_path / "pkg"
    package.mkdir()
    stamp = buildinfo.write_stamp(package, repo)
    assert stamp["commit_time"] == SAME_INSTANT_UTC
    assert stamp["commit_date"] == "2026-09-25"

    identity = buildinfo.build_identity(package)
    assert identity.source == buildinfo.SOURCE_STAMP
    assert identity.as_data()["commit_time"] == SAME_INSTANT_UTC
    assert identity.as_data()["commit_date"] == "2026-09-25"
    line = buildinfo.version_line(identity)
    assert "(2026-09-25 UTC)" in line, line
    assert "2026-09-24" not in line, line


def test_version_json_from_this_checkout_carries_commit_time() -> None:
    payload = json.loads(invoke(["version", "--json"]).out)
    assert payload["commit_time"].endswith("Z") and len(payload["commit_time"]) == 24
    assert payload["commit_date"] == payload["commit_time"][:10]


def test_a_stamp_from_before_commit_time_is_reported_as_stamped(tmp_path: Path) -> None:
    """An old stamp's date is the committer's local one and carries no instant to
    convert, so it is shown as it was stamped, without claiming to be UTC."""
    (tmp_path / buildinfo.STAMP_FILENAME).write_text(
        json.dumps({"version": "0.1.0", "commit": "a" * 40, "commit_date": "2026-09-24"}),
        encoding="utf-8",
    )
    identity = buildinfo.build_identity(tmp_path)
    assert identity.commit_time is None and identity.commit_date == "2026-09-24"
    assert "UTC" not in buildinfo.version_line(identity)


def test_a_record_completed_the_same_utc_day_is_not_dated_after_the_build() -> None:
    """The misreading itself: a record completed at 02:40Z on the day of a build
    committed at 02:34Z printed as dated after a build "of 2026-09-24"."""
    entry = Entry(
        "KYO-396",
        "bug",
        None,
        "t",
        "2026-09-25",
        changelog.DATED_BY_EVENT,
        completed_at="2026-09-25T02:30:00.000Z",
    )
    build_date = buildinfo.utc_timestamp(LOCAL_EVENING)[:10]
    assert entry.completed_on == build_date


# ---------------------------------------------- c2: containment, and full completion times


def _packaged(entries: list[Entry]) -> Changelog:
    return Changelog(entries, changelog.SOURCE_PACKAGED)


def test_the_packaged_changelog_says_every_record_is_in_this_build() -> None:
    entry = Entry(
        "KYO-1",
        "task",
        None,
        "t",
        "2026-09-25",
        changelog.DATED_BY_EVENT,
        completed_at="2026-09-25T01:00:00.000Z",
    )
    text = "\n".join(changelog.text_lines(_packaged([entry]), [entry], "2026-09-25"))
    assert "Every record listed is IN the build you are running" in text
    assert "can name nothing newer than it" in text


def test_the_containment_sentence_is_on_the_real_command() -> None:
    """Asserted through `kyo changelog`, so removing the sentence from the summary
    line fails here and not only in a helper's test."""
    result = invoke(["changelog"])
    assert result.code == 0, result
    assert changelog.containment_sentence(changelog.load()) in result.out


def test_entries_carry_their_full_utc_completion_time(kyo, project: Path, tmp_path: Path) -> None:
    """c2's data half: the packaged file carries `completed_at`, and the date is that
    instant's date. Generated from a fixture store with one record moved to done, so it
    does not depend on this repository's own store being in the checkout."""
    from .conftest import new_record

    record = new_record(kyo, criteria=["a criterion to settle"], title="a completed record")
    for status in ("working", "built"):
        assert kyo("move", record, status).code == 0
    verified = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    assert verified.code == 0, verified
    for status in ("verified", "done"):
        assert kyo("move", record, status).code == 0
    out = tmp_path / "packaged"
    out.mkdir()
    document = changelog.write_packaged_data(out, project)
    assert document["entries"], document.get("unavailable")
    written = json.loads((out / changelog.CHANGELOG_FILENAME).read_text(encoding="utf-8"))
    assert [raw["id"] for raw in written["entries"]] == [record]
    for raw in written["entries"][:20]:
        assert raw["completed_at"].endswith("Z"), raw
        assert raw["completed_on"] == raw["completed_at"][:10], raw


def test_since_a_timestamp_is_strictly_after_it() -> None:
    at = "2026-09-25T02:34:00.000Z"
    before = Entry(
        "A-1", "t", None, "a", "2026-09-25", "event", completed_at="2026-09-25T02:00:00.000Z"
    )
    exact = Entry("A-2", "t", None, "b", "2026-09-25", "event", completed_at=at)
    after = Entry(
        "A-3", "t", None, "c", "2026-09-25", "event", completed_at="2026-09-25T03:00:00.000Z"
    )
    assert changelog.resolve_since("2026-09-24T22:34:00-04:00") == at
    assert changelog.since_entries([before, exact, after], at) == [after]


# ------------------------------------------------------- c3: each brief run records its build


def _stamped(commit: str, commit_time: str | None) -> BuildIdentity:
    return BuildIdentity(
        version="0.1.0",
        commit=commit,
        commit_date=commit_time[:10] if commit_time else None,
        dirty=False,
        source=buildinfo.SOURCE_STAMP,
        kind=buildinfo.INSTALL_KIND_UV_TOOL,
        commit_time=commit_time,
    )


OLD = ("a" * 40, "2026-09-20T10:00:00.000Z")
NEW = ("b" * 40, "2026-09-25T02:34:00.000Z")


@pytest.fixture
def running(monkeypatch):
    """Set which build `kyo` believes it is, as the stamp of a bundled copy would."""

    def use(commit: str | None, commit_time: str | None = None) -> None:
        identity = (
            _stamped(commit, commit_time)
            if commit
            else BuildIdentity(
                "0.1.0", None, None, None, buildinfo.SOURCE_UNKNOWN, buildinfo.INSTALL_KIND_UNKNOWN
            )
        )
        monkeypatch.setattr(buildinfo, "build_identity", lambda *a, **k: identity)

    return use


@pytest.fixture
def packaged_entries(monkeypatch):
    def use(entries: list[Entry]) -> None:
        monkeypatch.setattr(changelog, "load", lambda: _packaged(entries))

    return use


def test_each_brief_run_records_its_build_in_the_db_and_the_ledger(
    kyo, project, running, db
) -> None:
    running(*NEW)
    assert kyo("brief").code == 0
    row = db().execute("SELECT * FROM brief_runs ORDER BY seq DESC LIMIT 1").fetchone()
    assert (row["build_commit"], row["build_commit_time"], row["build_source"]) == (
        NEW[0],
        NEW[1],
        buildinfo.SOURCE_STAMP,
    )
    ledger = project / "kyosei" / "export" / "_briefs.jsonl"
    line = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert (line["build_commit"], line["build_commit_time"], line["build_source"]) == (
        NEW[0],
        NEW[1],
        buildinfo.SOURCE_STAMP,
    )


def test_a_pre_24_brief_ledger_rebuilds_with_no_build_and_cannot_tell(
    kyo, project, running
) -> None:
    """The rebuild default for the prior export format, and c5 through it: a previous
    session-start brief with no recorded build says "cannot tell"."""
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    ledger = project / "kyosei" / "export" / "_briefs.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        for key in ("build_commit", "build_commit_time", "build_source", "schema_version"):
            del row[key]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0

    running(*NEW)
    brief = kyo("brief", "--session-start", "--compact")
    assert brief.code == 0, brief
    first = brief.out.splitlines()[0]
    assert first.startswith("BUILD: cannot tell whether the `kyo` under this project changed"), (
        first
    )
    assert "before briefs recorded their build" in first
    payload = kyo("brief", "--session-start", "--json").json
    # The brief above recorded NEW, so the one after it is unchanged — and silent.
    assert payload["build_change"]["state"] == "unchanged"


# ------------------------------------------------------------- c4/c5: the session-start notice


def test_a_changed_build_is_said_first_with_both_builds_and_the_records_between(
    kyo, running, packaged_entries
) -> None:
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0

    had = Entry(
        "KYO-1",
        "task",
        None,
        "already had",
        "2026-09-20",
        "event",
        completed_at="2026-09-20T09:00:00.000Z",
    )
    new = Entry(
        "KYO-396",
        "bug",
        "cli",
        "the fix",
        "2026-09-25",
        "event",
        completed_at="2026-09-25T02:30:00.000Z",
    )
    later = Entry(
        "KYO-999",
        "task",
        None,
        "not in the new build",
        "2026-09-26",
        "event",
        completed_at="2026-09-26T00:00:00.000Z",
    )
    packaged_entries([later, new, had])
    running(*NEW)
    result = kyo("brief", "--session-start", "--compact")
    assert result.code == 0, result
    lines = result.out.splitlines()
    assert lines[0].startswith("BUILD CHANGED"), lines[:3]
    assert "build aaaaaaaa (committed 2026-09-20T10:00:00.000Z)" in lines[0]
    assert "build bbbbbbbb (committed 2026-09-25T02:34:00.000Z)" in lines[0]
    notice = "\n".join(lines[: lines.index("")])
    assert "KYO-396" in notice
    assert "KYO-1 " not in notice and "KYO-999" not in notice

    payload = kyo("brief", "--json").json
    assert payload["build_change"] is None, "a mid-session brief is not where this is said"


def test_an_unchanged_build_prints_no_notice(kyo, running) -> None:
    running(*NEW)
    assert kyo("brief", "--session-start").code == 0
    result = kyo("brief", "--session-start", "--compact")
    assert not result.out.startswith("BUILD"), result.out.splitlines()[0]
    assert kyo("brief", "--session-start", "--json").json["build_change"]["state"] == "unchanged"


def test_the_first_session_of_a_project_prints_no_notice(kyo, running) -> None:
    running(*NEW)
    result = kyo("brief", "--session-start", "--compact")
    assert not result.out.startswith("BUILD"), result.out.splitlines()[0]


def test_an_unknown_running_build_cannot_tell(kyo, running) -> None:
    """c5's other half: the running build cannot name itself."""
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    running(None)
    first = kyo("brief", "--session-start", "--compact").out.splitlines()[0]
    assert first.startswith("BUILD: cannot tell"), first
    assert "cannot say which snapshot it is" in first


def test_removing_the_notice_fails_this_test(kyo, running, monkeypatch) -> None:
    """c4's "a test fails if the notice is removed", demonstrated: blank the renderer
    and the first line is no longer the notice."""
    from kyosei import render

    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    running(*NEW)
    monkeypatch.setattr(render, "build_change_lines", lambda change: [])
    first = kyo("brief", "--session-start", "--compact").out.splitlines()[0]
    assert not first.startswith("BUILD CHANGED")


def test_a_working_tree_build_gets_one_line_and_git_log(kyo, monkeypatch) -> None:
    """The decision for a copy that runs live from a checkout: still said, since it is
    true, but as the two commits and `git log`, not a record list."""

    def live(commit: str, when: str) -> BuildIdentity:
        return BuildIdentity(
            "0.1.0",
            commit,
            when[:10],
            False,
            buildinfo.SOURCE_GIT,
            buildinfo.INSTALL_KIND_WORKING_TREE,
            commit_time=when,
        )

    monkeypatch.setattr(buildinfo, "build_identity", lambda *a, **k: live(*OLD))
    assert kyo("brief", "--session-start").code == 0
    monkeypatch.setattr(buildinfo, "build_identity", lambda *a, **k: live(*NEW))
    lines = kyo("brief", "--session-start", "--compact").out.splitlines()
    assert lines[0].startswith("BUILD CHANGED")
    assert "`git log aaaaaaaa..bbbbbbbb`" in lines[1]


def test_date_only_packaged_entries_degrade_to_the_day_and_say_so() -> None:
    old_day = Entry("K-1", "t", None, "same day as the old build", "2026-09-20", "record")
    found = changelog.between([old_day], OLD[1], NEW[1])
    assert found.entries == [old_day] and not found.exact


# ------------------------------------------------------------- c6: the per-prompt hook


def test_the_prompt_hook_is_packaged_and_installed(kyo, project) -> None:
    assert ("UserPromptSubmit", "prompt-submit.json") in install_hooks.HOOK_INSTALL_TARGETS
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for g in settings["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert [install_hooks.hook_action(c) for c in commands] == ["kyo check-build --hook"]
    assert "check-build" in {shape.command for shape in cmd_check_build.HOOK_INVOCATIONS}


#: KYO-418: the hook payloads both hooks receive -- the brief records the session it
#: opened, and check-build compares against that session's opening brief only.
SESSION_A = json.dumps({"session_id": "session-a", "hook_event_name": "SessionStart"})
PROMPT_A = json.dumps({"session_id": "session-a", "hook_event_name": "UserPromptSubmit"})


def test_the_prompt_hook_names_both_builds_only_when_they_differ(kyo, project, running) -> None:
    running(*OLD)
    assert kyo("brief", "--session-start", stdin=SESSION_A).code == 0
    db_file = project / "kyosei" / "kyosei.db"
    ledger = project / "kyosei" / "export" / "_briefs.jsonl"
    before = (db_file.stat().st_mtime_ns, ledger.read_bytes())

    same = kyo("check-build", "--hook", stdin=PROMPT_A)
    assert (same.code, same.out, same.err) == (0, "", "")

    running(*NEW)
    changed = kyo("check-build", "--hook", stdin=PROMPT_A)
    assert changed.code == 0
    lines = changed.out.splitlines()
    assert len(lines) == 1, lines
    assert "build aaaaaaaa" in lines[0] and "build bbbbbbbb" in lines[0]
    assert f"`kyo changelog --since {OLD[1]}`" in lines[0]
    # Nothing written: not the database, not the ledger.
    assert (db_file.stat().st_mtime_ns, ledger.read_bytes()) == before


def test_the_prompt_hook_is_silent_with_no_project_or_no_opening_brief(
    kyo, tmp_path, running, monkeypatch
) -> None:
    running(*NEW)
    elsewhere = tmp_path / "nowhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    none = invoke(["check-build", "--hook"], stdin=PROMPT_A)
    assert (none.code, none.out) == (0, "")
    assert kyo("brief").code == 0  # a brief, but not a session-start one
    no_anchor = kyo("check-build", "--hook", stdin=PROMPT_A)
    assert (no_anchor.code, no_anchor.out) == (0, "")


def test_the_prompt_hook_never_opens_the_store(kyo, running, monkeypatch) -> None:
    from kyosei import commands

    running(*OLD)
    assert kyo("brief", "--session-start", stdin=SESSION_A).code == 0
    running(*NEW)

    def refuse(self):  # pragma: no cover - reaching it is the failure
        raise AssertionError("check-build opened the store")

    monkeypatch.setattr(commands.Context, "store", refuse)
    assert "build bbbbbbbb" in kyo("check-build", "--hook", stdin=PROMPT_A).out


def test_the_opening_brief_is_read_read_only(project, kyo, running) -> None:
    running(*OLD)
    assert kyo("brief", "--session-start").code == 0
    db_file = project / "kyosei" / "kyosei.db"
    row = cmd_check_build.opening_brief_build(db_file)
    assert row is not None and row["build_commit"] == OLD[0]
    # A database from before the build columns answers "nothing to compare".
    conn = sqlite3.connect(db_file)
    conn.execute("ALTER TABLE brief_runs DROP COLUMN build_source")
    conn.commit()
    conn.close()
    assert cmd_check_build.opening_brief_build(db_file) is None
