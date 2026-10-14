"""KYO-477: ``kyo report <dispatch-id> --file F --check`` — a worker's dry run.

A first ingest that fails the schema costs the controller a hand-edit of the
worker's JSON, and a worker had no way to run the ingest's checks before handing
back. ``--check`` runs those checks through
the same functions the ingest calls and writes nothing; the brief tells the worker to
run it and shows a filled example the schema is tested against.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from kyosei.brief import report_example
from kyosei.contracts import VERIFICATION_METHODS, WORKER_REPORT_PATH
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR
from kyosei.report_schema import validate_report

from .conftest import event_lines, git_init, invoke, new_record, valid_report


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _export_fingerprint(project: Path) -> dict[str, str]:
    export = project / "kyosei" / "export"
    return {
        str(p.relative_to(export)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(export.rglob("*"))
        if p.is_file()
    }


def _state(project: Path) -> tuple[int, dict[str, str]]:
    """What a write would change: the events ledger's length and the export's bytes."""
    return len(event_lines(project)), _export_fingerprint(project)


def _write(tmp_path: Path, name: str, payload: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture
def dispatched(kyo, tmp_path: Path) -> dict:
    """A git project with a record dispatched on branch ``work``, one commit on it.

    Returns ``project``, ``record``, ``dispatch_id``, ``worked_from_sha``, ``sha``.
    """
    project: Path = kyo.project
    git_init(project)
    record = new_record(kyo, criteria=["a", "b"], title="a record to report on")
    # KYO-485: this fixture makes and checks out the branch itself, so dispatch must not.
    result = kyo("dispatch", record, "--json", "--branch", "work", "--no-create-worktree")
    assert result.code == 0, result
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "store: dispatch")
    _git(project, "branch", "work")
    _git(project, "checkout", "-q", "work")
    (project / "worker.txt").write_text("output\n", encoding="utf-8")
    _git(project, "add", "worker.txt")
    _git(project, "commit", "-q", "-m", "worker output")
    sha = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", "main")
    return {
        "project": project,
        "record": record,
        "dispatch_id": result.json["dispatch_id"],
        "worked_from_sha": result.json["worked_from_sha"],
        "sha": sha,
    }


def _report(d: dict, **overrides) -> dict:
    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=d["record"],
        worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{d['record']}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
        commits=[{"sha": d["sha"], "subject": "worker output"}],
    )
    report.update(overrides)
    return report


# ---------------------------------------------------------------- c1: nothing changes


def test_a_passing_check_exits_zero_and_writes_nothing(kyo, dispatched, tmp_path) -> None:
    project = dispatched["project"]
    path = _write(tmp_path, "r.json", _report(dispatched))
    before = _state(project)
    result = kyo("report", dispatched["dispatch_id"], "--file", path, "--check")
    assert result.code == 0, result
    assert "nothing was written" in result.out
    assert "branch check: VERIFIED" in result.out
    assert _state(project) == before
    shown = kyo("show", dispatched["dispatch_id"], "--json")
    assert shown.json["state"] == "running"
    # The same file then ingests: --check passing means the ingest's checks pass.
    ingested = kyo("report", dispatched["dispatch_id"], "--file", path)
    assert ingested.code == 0, ingested
    assert _state(project) != before


def test_a_failing_branch_check_exits_nonzero_and_writes_nothing(
    kyo, dispatched, tmp_path
) -> None:
    project = dispatched["project"]
    _git(project, "checkout", "-q", "-b", "stray")
    (project / "stray.txt").write_text("x\n", encoding="utf-8")
    _git(project, "add", "stray.txt")
    _git(project, "commit", "-q", "-m", "stray")
    stray = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", "main")
    path = _write(tmp_path, "r.json", _report(dispatched, commits=[{"sha": stray}]))
    before = _state(project)
    result = kyo("report", dispatched["dispatch_id"], "--file", path, "--check")
    assert result.code == EXIT_CONTRACT, result
    assert "report-commit-off-branch" in result.err
    assert stray in result.err
    assert _state(project) == before


def test_a_verdict_for_an_uncovered_record_is_refused_by_the_check(
    kyo, dispatched, tmp_path
) -> None:
    project = dispatched["project"]
    other = new_record(kyo, criteria=["x"], title="another record")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "store: another record")
    report = _report(
        dispatched,
        criteria_verdicts=[
            {"criterion_id": f"{other}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    path = _write(tmp_path, "r.json", report)
    before = _state(project)
    result = kyo("report", dispatched["dispatch_id"], "--file", path, "--check")
    assert result.code == EXIT_ERROR, result
    assert f"{other}.c1" in result.err and "KYO-12" in result.err
    assert _state(project) == before


# ------------------------------------------- c3: three refused report shapes


def _schema_refusal(kyo, d: dict, tmp_path: Path, report: dict) -> str:
    project = d["project"]
    path = _write(tmp_path, "r.json", report)
    before = _state(project)
    result = kyo("report", d["dispatch_id"], "--file", path, "--check")
    assert result.code == EXIT_ERROR, result
    assert "does not conform to the report schema" in result.err
    assert _state(project) == before
    return result.err


def test_unmet_unobserved_without_reason_is_refused_naming_reason(
    kyo, dispatched, tmp_path
) -> None:
    report = _report(
        dispatched,
        criteria_verdicts=[
            {
                "criterion_id": f"{dispatched['record']}.c1",
                "verdict": "unmet-unobserved",
                "method": "reasoned-about",
            }
        ],
    )
    err = _schema_refusal(kyo, dispatched, tmp_path, report)
    assert "criteria_verdicts[0]" in err
    assert "'unmet-unobserved' requires a non-empty 'reason'" in err


def test_free_text_method_is_refused_naming_the_allowed_values(
    kyo, dispatched, tmp_path
) -> None:
    report = _report(
        dispatched,
        criteria_verdicts=[
            {
                "criterion_id": f"{dispatched['record']}.c1",
                "verdict": "met",
                "method": "I ran the tests and they passed",
            }
        ],
    )
    err = _schema_refusal(kyo, dispatched, tmp_path, report)
    assert "criteria_verdicts[0].method" in err
    for method in VERIFICATION_METHODS:
        assert method in err


def test_missing_dispatch_and_record_ids_are_refused_naming_both(
    kyo, dispatched, tmp_path
) -> None:
    report = _report(dispatched)
    del report["dispatch_id"]
    del report["record_id"]
    err = _schema_refusal(kyo, dispatched, tmp_path, report)
    assert "missing required key(s) dispatch_id, record_id" in err


# ----------------------------------------------------- c2: from a dispatch worktree


def test_the_check_runs_from_inside_a_dispatch_worktree(
    kyo, dispatched, tmp_path, monkeypatch
) -> None:
    """The worker's case: cwd is a linked worktree of the project, whose store is the
    main checkout's opened read-only (KYO-342). A pass and a refusal both run, and the
    main checkout's store is untouched by either."""
    project = dispatched["project"]
    tree = tmp_path / "worktree"
    _git(project, "worktree", "add", "-q", str(tree), "work")
    assert not (tree / "kyosei" / "kyosei.db").exists()
    good = tree / WORKER_REPORT_PATH
    good.write_text(json.dumps(_report(dispatched)), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad_report = _report(dispatched)
    bad_report["criteria_verdicts"][0]["method"] = "free text"
    bad.write_text(json.dumps(bad_report), encoding="utf-8")

    before = _state(project)
    monkeypatch.chdir(tree)
    passed = invoke(
        ["report", dispatched["dispatch_id"], "--file", WORKER_REPORT_PATH, "--check"]
    )
    assert passed.code == 0, passed
    assert "nothing was written" in passed.out
    refused = invoke(["report", dispatched["dispatch_id"], "--file", str(bad), "--check"])
    assert refused.code == EXIT_ERROR, refused
    assert "method" in refused.err
    assert _state(project) == before


# ------------------------------------------------------------- c4/c5: the brief


def _brief(kyo, record: str) -> tuple[str, dict]:
    result = kyo("--full", "dispatch", record, "--json")
    assert result.code == 0, result
    return result.json["record"]["dispatches"][0]["brief"], result.json


def test_the_brief_tells_the_worker_to_run_the_check_with_its_dispatch_id(
    kyo, git_project
) -> None:
    record = new_record(kyo, criteria=["a"])
    brief, payload = _brief(kyo, record)
    dispatch_id = payload["dispatch_id"]
    assert f"`kyo report {dispatch_id} --file {WORKER_REPORT_PATH} --check`" in brief


def test_the_briefs_filled_example_is_the_object_the_schema_accepts(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    brief, payload = _brief(kyo, record)
    example = report_example(payload["dispatch_id"], record, payload["worked_from_sha"])
    assert validate_report(example) == []
    verdicts = {cv["verdict"]: cv for cv in example["criteria_verdicts"]}
    assert verdicts["unmet-unobserved"]["reason"]
    # The brief renders that very object, not a hand-typed copy of it.
    block = re.search(r"```json\n(.*?)\n```", brief, re.DOTALL)
    assert block is not None, "the brief carries no filled report example"
    assert json.loads(block.group(1)) == example


# ------------------------------------------------- KYO-544.c2: commits[] order warning


def _second_commit_on_work(d: dict) -> str:
    project = d["project"]
    _git(project, "checkout", "-q", "work")
    (project / "worker2.txt").write_text("more\n", encoding="utf-8")
    _git(project, "add", "worker2.txt")
    _git(project, "commit", "-q", "-m", "more worker output")
    sha = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", "main")
    return sha


@pytest.mark.parametrize("check", [True, False], ids=["--check", "ingest"])
def test_a_newest_first_commits_list_warns_naming_the_order_to_use(
    kyo, dispatched, tmp_path, check
) -> None:
    from kyosei.report_ingest import BRANCH_WARNING_COMMITS_OUT_OF_ORDER

    newer = _second_commit_on_work(dispatched)
    reversed_commits = [{"sha": newer}, {"sha": dispatched["sha"]}]
    path = _write(tmp_path, "r.json", _report(dispatched, commits=reversed_commits))
    argv = ["report", dispatched["dispatch_id"], "--file", path] + (["--check"] if check else [])
    result = kyo(*argv)
    assert result.code == 0, result  # a warning, never a refusal
    assert "branch check: VERIFIED" in result.out
    assert BRANCH_WARNING_COMMITS_OUT_OF_ORDER in result.out
    assert "oldest first" in result.out and newer in result.out


@pytest.mark.parametrize("check", [True, False], ids=["--check", "ingest"])
def test_an_oldest_first_commits_list_ending_on_the_tip_does_not_warn(
    kyo, dispatched, tmp_path, check
) -> None:
    from kyosei.report_ingest import BRANCH_WARNING_COMMITS_OUT_OF_ORDER

    newer = _second_commit_on_work(dispatched)
    ordered = [{"sha": dispatched["sha"]}, {"sha": newer}]
    path = _write(tmp_path, "r.json", _report(dispatched, commits=ordered))
    argv = ["report", dispatched["dispatch_id"], "--file", path] + (["--check"] if check else [])
    result = kyo(*argv)
    assert result.code == 0, result
    assert "branch check: VERIFIED" in result.out
    assert BRANCH_WARNING_COMMITS_OUT_OF_ORDER not in result.out


def test_a_commits_list_that_omits_the_tip_warns(kyo, dispatched, tmp_path) -> None:
    """The tip missing, with no worked_from_sha to name it as unlisted: the order
    warning is what says commits[] must end on the tip."""
    from kyosei.report_ingest import BRANCH_WARNING_COMMITS_OUT_OF_ORDER

    newer = _second_commit_on_work(dispatched)
    report = _report(dispatched, commits=[{"sha": dispatched["sha"]}])
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", dispatched["dispatch_id"], "--file", path, "--check")
    assert result.code == 0, result
    # The tip is named either as an unlisted commit or by the order warning, and the
    # order to use is stated beside it.
    assert newer in result.out and "oldest first" in result.out
    assert (
        BRANCH_WARNING_COMMITS_OUT_OF_ORDER in result.out
        or "branch-commits-not-in-report" in result.out
    )
