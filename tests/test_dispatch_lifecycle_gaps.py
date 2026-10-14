"""KYO-411: four gaps in the dispatch lifecycle, found by a review of the CLI.

At each step the tool trusted what it was told or left out the one thing it should
have compared against:

* A second ``kyo dispatch KYO-x`` while ``KYO-x.d1`` ran could not see ``.d1``
  (the worktree-collision check read only OTHER records' dispatches), so it minted
  ``.d2`` into the same derived worktree; and an unwritable ``--brief-out`` raised a
  raw traceback after the dispatch row existed, which is what invites that retry.
* A report with no ``commits[]`` read "nothing to check", whether or not the
  branch had moved, and nothing compared the branch against the reported shas.
* After a merge, docs obligations came from the report's ``touched_files[]``
  alone, unnormalised, so an omitted or ``./``-spelled path raised nothing.
* Stop-audit rows wrote absolute home-directory paths into the git-canonical
  events ledger.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import dispatch_docs, report_ingest, stop_audit
from kyosei.errors import EXIT_CONTRACT
from kyosei.store import open_store

from .conftest import event_lines, git_worktree, new_record, valid_report
from .test_kyo137_docs_obligations import (
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
)
from .test_subagent_stop_worktree import CONTROLLER_EDIT, _running_dispatch, _stop


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit_everything(project: Path, message: str) -> None:
    _git(project, "add", "-A")
    if _git(project, "status", "--porcelain"):
        _git(project, "commit", "-q", "-m", message)


def _write_json(tmp_path: Path, name: str, payload: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _dispatches_of(kyo, record: str) -> list[str]:
    return [d["id"] for d in kyo("show", record, "--json").json["dispatches"]]


# ---------------------------------------------------------------- c1, c2 — re-dispatch


def test_redispatching_a_running_record_into_the_same_worktree_is_refused(
    kyo, git_project: Path
) -> None:
    """c1. The derived worktree is named after the branch, so a retry of the same
    command lands on the same path. Remove the own-record half of the collision check
    and the second dispatch succeeds, which is what this fails on."""
    record = new_record(kyo, criteria=["a"])
    first = kyo("dispatch", record, "--branch", "kyo-x", "--json")
    assert first.code == 0, first
    first_id = first.json["dispatch_id"]

    second = kyo("dispatch", record, "--branch", "kyo-x")
    assert second.code == EXIT_CONTRACT, second
    assert first_id in second.err
    assert "<worktrees>/kyo-x" in second.err, second.err
    assert "--force" in second.err and "--worktree" in second.err
    # Refused means nothing was created.
    assert _dispatches_of(kyo, record) == [first_id]


def test_force_lets_the_redispatch_through(kyo, git_project: Path) -> None:
    """c1, the override the refusal names."""
    record = new_record(kyo, criteria=["a"])
    first_id = kyo("dispatch", record, "--branch", "kyo-x", "--json").json["dispatch_id"]
    forced = kyo("dispatch", record, "--branch", "kyo-x", "--force", "--json")
    assert forced.code == 0, forced
    assert sorted(_dispatches_of(kyo, record)) == sorted([first_id, forced.json["dispatch_id"]])


def test_a_different_worktree_clears_the_refusal(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c2. CLAUDE.md: an error that names a remedy owes a test that the remedy clears
    it. The message names `--worktree`; a real linked worktree of this repository
    (KYO-402 refuses anything else) is given and the dispatch succeeds."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-x").code == 0
    assert kyo("dispatch", record, "--branch", "kyo-x").code == EXIT_CONTRACT

    elsewhere = git_worktree(git_project, tmp_path / "elsewhere")
    retried = kyo("dispatch", record, "--branch", "kyo-x", "--worktree", elsewhere, "--json")
    assert retried.code == 0, retried
    assert len(_dispatches_of(kyo, record)) == 2


# ------------------------------------------------------------------- c3 — --brief-out


def test_an_unwritable_brief_out_refuses_before_any_dispatch_exists(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c3. A missing directory is the common case. Remove the up-front check and the
    dispatch row is committed before the write fails, which this asserts against."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "dispatch", record, "--branch", "kyo-x", "--brief-out", str(tmp_path / "no" / "b.md")
    )
    assert result.code != 0, result
    assert "Traceback" not in result.err
    assert "cannot be written" in result.err
    assert "Nothing was dispatched" in result.err
    assert _dispatches_of(kyo, record) == []

    # And the command then works as asked once the path is writable.
    ok = kyo("dispatch", record, "--branch", "kyo-x", "--brief-out", str(tmp_path / "b.md"))
    assert ok.code == 0, ok
    assert (tmp_path / "b.md").read_text(encoding="utf-8").startswith("# Dispatch brief")


def test_a_directory_as_brief_out_is_refused_too(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-x", "--brief-out", str(tmp_path))
    assert result.code != 0
    assert "is a directory" in result.err
    assert _dispatches_of(kyo, record) == []


def test_a_write_that_fails_after_the_commit_names_the_dispatch_it_created(
    kyo, git_project: Path, tmp_path: Path, monkeypatch
) -> None:
    """c3's other half: whatever the up-front check cannot foresee still fails as a
    Kyosei error that names the dispatch that WAS created, never a traceback."""
    record = new_record(kyo, criteria=["a"])
    target = tmp_path / "b.md"
    real_write_text = Path.write_text

    def failing_write_text(self, *args, **kwargs):
        if self == target:
            raise OSError(28, "No space left on device")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)
    result = kyo("dispatch", record, "--branch", "kyo-x", "--brief-out", str(target))
    monkeypatch.undo()

    assert result.code != 0, result
    assert "Traceback" not in result.err
    created = _dispatches_of(kyo, record)
    assert created == [f"{record}.d1"]
    assert f"{record}.d1" in result.err
    assert "Do not re-run" in result.err


# ---------------------------------------------------------- c4, c5, c6 — branch check


def test_a_branch_that_never_advanced_is_named_not_nothing_to_check(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c4/c5. The branch exists, sits exactly at worked_from_sha, and the report carries
    no commits. Before KYO-411 this read "nothing to check" — the same line a worker
    whose commits went to a detached HEAD gets. Remove the rev-list comparison and the
    outcome falls back to `no-commits`, which this fails on."""
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-x", "--json").json
    _commit_everything(git_project, "store churn")
    _git(git_project, "branch", "kyo-x")
    base = _git(git_project, "rev-parse", "kyo-x")

    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=base, commits=[]
    )
    path = _write_json(tmp_path, "r.json", report)
    as_json = kyo("report", d["dispatch_id"], "--file", path, "--json")
    assert as_json.code == 0, as_json
    check = as_json.json["branch_check"]
    assert check["outcome"] == report_ingest.BRANCH_CHECK_DID_NOT_ADVANCE
    assert check["advance"]["count"] == 0
    assert [w["code"] for w in check["warnings"]] == [report_ingest.BRANCH_WARNING_DID_NOT_ADVANCE]

    # The text rendering, from the same computation.
    lines = report_ingest._branch_check_lines(check, d["dispatch_id"])
    assert lines[0].startswith("branch check: BRANCH DID NOT ADVANCE"), lines
    assert not any("nothing to check" in line for line in lines)


def test_the_text_output_names_the_unadvanced_branch(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c5, the text half, read off the command itself rather than the renderer."""
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-x", "--json").json
    _commit_everything(git_project, "store churn")
    _git(git_project, "branch", "kyo-x")
    base = _git(git_project, "rev-parse", "kyo-x")
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=base, commits=[]
    )
    result = kyo("report", d["dispatch_id"], "--file", _write_json(tmp_path, "r.json", report))
    assert result.code == 0, result
    assert "branch check: BRANCH DID NOT ADVANCE" in result.out
    assert "nothing to check" not in result.out


def test_commits_on_the_branch_the_report_does_not_list_are_a_named_warning(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c6. Two commits past worked_from_sha, one reported. Each reported sha is
    reachable, so KYO-32's check alone says VERIFIED; the branch comparison says what
    else the merge will ship."""
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-x", "--json").json
    _commit_everything(git_project, "store churn")
    base = _git(git_project, "rev-parse", "HEAD")
    _git(git_project, "checkout", "-q", "-b", "kyo-x")
    (git_project / "one.txt").write_text("1", encoding="utf-8")
    _git(git_project, "add", "one.txt")
    _git(git_project, "commit", "-q", "-m", "one")
    listed = _git(git_project, "rev-parse", "HEAD")
    (git_project / "two.txt").write_text("2", encoding="utf-8")
    _git(git_project, "add", "two.txt")
    _git(git_project, "commit", "-q", "-m", "two")
    unlisted = _git(git_project, "rev-parse", "HEAD")

    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=base,
        # Abbreviated on purpose: a short sha still accounts for its commit.
        commits=[{"sha": listed[:10]}],
    )
    path = _write_json(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path, "--json")
    assert result.code == 0, result
    check = result.json["branch_check"]
    assert check["outcome"] == "verified"
    warnings = {w["code"]: w for w in check["warnings"]}
    assert set(warnings) == {report_ingest.BRANCH_WARNING_UNLISTED_COMMITS}
    assert warnings[report_ingest.BRANCH_WARNING_UNLISTED_COMMITS]["shas"] == [unlisted]

    lines = report_ingest._branch_check_lines(check, d["dispatch_id"])
    assert any(report_ingest.BRANCH_WARNING_UNLISTED_COMMITS in line for line in lines), lines
    assert any(unlisted in line for line in lines)


def test_a_report_with_no_commits_on_a_branch_that_moved_says_so(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c6, the no-commits side: the branch has work and the report lists none."""
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-x", "--json").json
    _commit_everything(git_project, "store churn")
    base = _git(git_project, "rev-parse", "HEAD")
    _git(git_project, "checkout", "-q", "-b", "kyo-x")
    (git_project / "one.txt").write_text("1", encoding="utf-8")
    _git(git_project, "add", "one.txt")
    _git(git_project, "commit", "-q", "-m", "one")
    sha = _git(git_project, "rev-parse", "HEAD")

    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=base, commits=[]
    )
    result = kyo("report", d["dispatch_id"], "--file", _write_json(tmp_path, "r.json", report))
    assert result.code == 0, result
    assert "branch check: UNREPORTED COMMITS" in result.out
    assert sha in result.out


# ----------------------------------------------------- c7, c8, c9 — docs obligations


def test_a_governed_file_the_report_left_out_still_raises_its_obligation(
    kyo, git_project: Path
) -> None:
    """c7/c9. The branch changed `src/app.py`, merged before ingest, and the report's
    touched_files[] named something else. The two-dot diff from the recorded base sha
    names it anyway; remove that source and the report's list wins alone again and the
    section is never raised."""
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    _, reported = _merged_ingest(
        kyo, git_project, record, touched_files=["src/unrelated.py"]
    )
    assert reported.code == 0, reported
    obligations = reported.json["docs_obligations"]
    assert ("docs/architecture.md", "How the CLI is wired") in {
        (row["doc_path"], row["section"]) for row in obligations["raised"]
    }, obligations
    assert "src/app.py" in obligations["changed_files"]
    # c9: which source answered, and which paths came from which.
    assert (
        obligations["changed_source"] == dispatch_docs.DOCS_CHANGED_SOURCE_BASE_SHA_AND_REPORT
    )
    says = obligations["changed_says"]
    assert "only by the git diff" in says and "src/app.py" in says
    assert "only by the report's touched_files[]" in says and "src/unrelated.py" in says


def test_a_dot_slash_spelling_raises_the_same_obligation(
    kyo, git_project: Path, db
) -> None:
    """c8. Git's diff is made unreadable (the recorded base sha is replaced with one
    this repository does not know), so the report's list is the only source — and
    `./src/app.py` must still match `governs: src/app.py`. Remove the normalisation
    and nothing is raised."""
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    _commit_everything(git_project, "the store, before the merge")
    _git(git_project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    conn = db()
    conn.execute(
        "UPDATE dispatches SET worked_from_sha = ? WHERE id = ?", ("0" * 40, dispatch_id)
    )
    conn.close()

    report = valid_report(
        dispatch_id=dispatch_id,
        record_id=record,
        worked_from_sha=sha,
        touched_files=["./src/app.py"],
    )
    path = git_project / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = kyo("report", dispatch_id, "--file", str(path), "--json")
    assert result.code == 0, result
    obligations = result.json["docs_obligations"]
    assert obligations["changed_source"] == dispatch_docs.DOCS_CHANGED_SOURCE_REPORT
    assert obligations["changed_files"] == ["src/app.py"]
    assert {(row["doc_path"], row["section"]) for row in obligations["raised"]} == {
        ("docs/architecture.md", "How the CLI is wired")
    }
    # c9: the report-only case says the diff could not be read.
    assert "could not be read" in obligations["changed_says"]


@pytest.mark.parametrize(
    ("spelled", "normal"),
    [
        ("./src/b.py", "src/b.py"),
        ("././src/b.py", "src/b.py"),
        ("src//b.py", "src/b.py"),
        ("src/./b.py", "src/b.py"),
        (" src/b.py ", "src/b.py"),
        ("./", ""),
        (".", ""),
    ],
)
def test_reported_paths_are_normalised(spelled: str, normal: str) -> None:
    assert dispatch_docs._normalise_reported_path(spelled) == normal


# ----------------------------------------------------------------- c10, c11 — paths


def _stop_audit_events(project: Path) -> list[dict]:
    return [e for e in event_lines(project) if (e.get("after") or {}).get("stop_audit")]


def test_a_stop_audit_row_carries_no_absolute_home_path(
    kyo, git_project: Path, tmp_path: Path, monkeypatch
) -> None:
    """c10. The test's own tmp directory stands in for the home directory, so every
    path the hook sees — the worker's worktree, the transcript, the controller's
    checkout — is under "home". Remove the relativisation and the row holds them
    verbatim, which is what this fails on."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (git_project / CONTROLLER_EDIT).write_text("dirty\n", encoding="utf-8")
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-home")

    matched = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id, "--json")
    assert matched.code == 0, matched
    unresolved = _stop(kyo, "check-clean", tmp_path, git_project, None, "--json")
    assert unresolved.code == 0, unresolved

    events = _stop_audit_events(git_project)
    outcomes = {e["after"]["stop_audit"]["outcome"] for e in events}
    # KYO-418.c6: an unresolved stop is counted in place, never a row -- so the only
    # rows that can carry a path are the ones still written.
    assert outcomes == {"matched"}, outcomes
    for event in events:
        audit = event["after"]["stop_audit"]
        assert str(tmp_path) not in json.dumps(audit), audit
        assert not audit["directory"].startswith("/"), audit["directory"]
        if audit.get("transcript"):
            assert audit["transcript"].startswith("~/"), audit["transcript"]
    matched_row = next(e for e in events if e["after"]["stop_audit"]["outcome"] == "matched")
    assert matched_row["after"]["stop_audit"]["directory"] == "../wt-home"

    # The reader expands the stored transcript back to a path that opens, which is
    # what `worker_tokens` needs (KYO-284).
    store = open_store(git_project, code_root=git_project)
    try:
        ledger = stop_audit.stop_audits(store)
    finally:
        store.close()
    transcripts = [a.transcript for a in ledger.audits if a.dispatch_id == dispatch_id]
    assert transcripts and all(Path(t).is_absolute() and Path(t).exists() for t in transcripts)


def test_a_legacy_row_with_absolute_paths_still_reads_the_same(
    kyo, git_project: Path, db
) -> None:
    """c11. Events are immutable history: a row written before KYO-411 holds absolute
    `directory` and `transcript` values, and it must read back — and list in
    `kyo worktrees` — exactly as it did."""
    record = new_record(kyo, criteria=["a"])
    legacy_dir = "/Users/somebody/code/project"
    legacy_transcript = "/Users/somebody/.claude/projects/p/s/subagents/agent-a.jsonl"
    rows = [
        (
            "unresolved",
            {
                "outcome": "unresolved",
                "resolved": False,
                "directory": legacy_dir,
                "why": f"the prompt names none of the candidates; read {legacy_dir}",
                "dispatch_id": None,
                "candidates": [f"{record}.d1"],
                "helper_of": None,
                "transcript": legacy_transcript,
                "reason": "prompt-names-none",
            },
        ),
        (
            "matched",
            {
                "outcome": "matched",
                "resolved": True,
                "directory": legacy_dir + "-wt",
                "why": "matched",
                "dispatch_id": f"{record}.d1",
                "candidates": [f"{record}.d1"],
                "helper_of": None,
                "transcript": legacy_transcript,
                "reason": None,
            },
        ),
    ]
    conn = db()
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    for offset, (name, audit) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO events (id, seq, at, actor, command, record_id, before_json,"
            " after_json) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
            (
                f"LEGACY-{name}",
                seq + offset,
                "2026-09-01T00:00:00.000Z",
                "controller",
                stop_audit.STOP_AUDIT_COMMAND,
                json.dumps({"stop_audit": audit, "session_id": "s", "agent_id": "a"}),
            ),
        )
    conn.close()

    store = open_store(git_project, code_root=git_project)
    try:
        ledger = stop_audit.stop_audits(store)
    finally:
        store.close()
    by_id = {a.event_id: a for a in ledger.audits}
    assert by_id["LEGACY-unresolved"].directory == legacy_dir
    assert by_id["LEGACY-unresolved"].transcript == legacy_transcript
    assert by_id["LEGACY-unresolved"].unresolved
    assert by_id["LEGACY-matched"].dispatch_id == f"{record}.d1"
    assert by_id["LEGACY-matched"].outcome == "matched"
    assert by_id["LEGACY-matched"].transcript == legacy_transcript

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    unresolved = listing.json["stop_audits"]["unresolved"]
    assert [u["event_id"] for u in unresolved] == ["LEGACY-unresolved"]
    assert unresolved[0]["fell_back_to"] == legacy_dir
    assert unresolved[0]["candidates"] == [f"{record}.d1"]
