"""KYO-745: ``kyo report <dispatch> --amend-with-branch`` amends the latest report from the
commits an orchestrator added to the branch, and ``kyo accept``'s refusal names it."""

from __future__ import annotations

from pathlib import Path

from kyosei.cmd_accept import CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT
from kyosei.contracts import (
    CONTRACT_REPORT_AMEND_NO_REPORT,
    CONTRACT_REPORT_AMEND_NOTHING_BEYOND,
)
from kyosei.errors import EXIT_CONTRACT

from .test_kyo137_docs_obligations import (
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
    _write,
)
from .test_kyo_accept_tip_and_summary import _dispatch_with_a_report, _met


def _commit(project: Path, branch: str, text: str, subject: str) -> str:
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", branch)
    _write(project, "src/app.py", text)
    _git(project, "add", "src/app.py")
    _git(project, "commit", "-q", "-m", subject)
    tip = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", base)
    return tip


def test_the_amendment_carries_the_report_and_adds_the_commits_oldest_first(
    kyo, git_project: Path
) -> None:
    record, dispatch_id, reported, reported_tip = _dispatch_with_a_report(kyo, git_project)
    first = _commit(git_project, "kyo-137", "x = 3\n", "fix the first thing")
    second = _commit(git_project, "kyo-137", "x = 4\n", "fix the second thing")
    done = kyo("report", dispatch_id, "--amend-with-branch", "--json")
    assert done.code == 0, done
    new_id = done.json["report_id"]
    assert new_id != reported.json["report_id"]
    body = kyo("show", new_id, "--json").json
    text = str(body)
    assert reported.json["report_id"] in text
    assert text.index(first) < text.index(second)
    assert "[orchestrator] fix the first thing" in text
    assert f"{record}.c1" in text and reported_tip in text


def test_the_amended_report_clears_accepts_refusal(kyo, git_project: Path) -> None:
    record, dispatch_id, reported, _tip = _dispatch_with_a_report(kyo, git_project)
    _commit(git_project, "kyo-137", "x = 3\n", "late fix")
    refused = kyo("accept", dispatch_id)
    assert refused.code == EXIT_CONTRACT and CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT in refused.err
    assert f"kyo report {dispatch_id} --amend-with-branch" in refused.err
    assert kyo("report", dispatch_id, "--amend-with-branch").code == 0
    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted


def test_check_writes_nothing(kyo, git_project: Path) -> None:
    _record, dispatch_id, reported, _tip = _dispatch_with_a_report(kyo, git_project)
    _commit(git_project, "kyo-137", "x = 3\n", "late fix")
    checked = kyo("report", dispatch_id, "--amend-with-branch", "--check")
    assert checked.code == 0, checked
    again = kyo("report", dispatch_id, "--amend-with-branch", "--json")
    assert again.code == 0, again


def test_a_branch_with_nothing_beyond_the_report_is_refused_and_accept_is_the_remedy(
    kyo, git_project: Path
) -> None:
    _record, dispatch_id, _reported, _tip = _dispatch_with_a_report(kyo, git_project)
    refused = kyo("report", dispatch_id, "--amend-with-branch")
    assert refused.code == EXIT_CONTRACT
    assert CONTRACT_REPORT_AMEND_NOTHING_BEYOND in refused.err
    assert f"kyo accept {dispatch_id}" in refused.err
    assert kyo("accept", dispatch_id).code == 0, "the remedy it names runs"


def test_a_dispatch_with_no_ingested_report_is_refused_and_the_filing_is_the_remedy(
    kyo, git_project: Path
) -> None:
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    dispatch_id = dispatched.json["dispatch_id"]
    refused = kyo("report", dispatch_id, "--amend-with-branch")
    assert refused.code == EXIT_CONTRACT
    assert CONTRACT_REPORT_AMEND_NO_REPORT in refused.err
    assert f"kyo report {dispatch_id} --file" in refused.err


def test_file_and_amend_with_branch_are_exclusive(kyo, git_project: Path, tmp_path: Path) -> None:
    _record, dispatch_id, _reported, _tip = _dispatch_with_a_report(kyo, git_project)
    both = kyo("report", dispatch_id, "--file", str(tmp_path / "r.json"), "--amend-with-branch")
    assert both.code != 0 and "not both" in both.err
    neither = kyo("report", dispatch_id)
    assert neither.code != 0 and "--amend-with-branch" in neither.err
