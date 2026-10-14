"""KYO-540: ``kyo accept`` refuses a report the branch has moved past, and ``kyo report``
and ``kyo accept`` open with a one-line summary.

Fixtures come from :mod:`tests.test_kyo137_docs_obligations`, as
:mod:`tests.test_kyo329_accept` does, so this agrees with the commands it runs.
"""

from __future__ import annotations

from pathlib import Path

from kyosei.cmd_accept import CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT
from kyosei.errors import EXIT_CONTRACT

from .test_kyo137_docs_obligations import (
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
    _report,
    _write,
)


def _met(criterion_id: str) -> dict:
    return {
        "criterion_id": criterion_id,
        "verdict": "met",
        "method": "machine-checked",
        "evidence": "make test: exit=0",
    }


def _dispatch_with_a_report(kyo, project: Path):
    """A live branch `kyo-137`, a report listing its tip, then a further commit on the
    branch -- the worker whose background task kept committing after it wrote the report."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    reported_tip = _git(project, "rev-parse", "kyo-137")
    reported = _report(
        kyo,
        project,
        dispatch_id,
        record,
        reported_tip,
        criteria_verdicts=[_met(f"{record}.c1")],
        commits=[{"sha": reported_tip, "subject": "work on kyo-137"}],
    )
    assert reported.code == 0, reported
    return record, dispatch_id, reported, reported_tip


def _commit_on_branch(project: Path, branch: str, text: str) -> str:
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", branch)
    _write(project, "src/app.py", text)
    _git(project, "add", "src/app.py")
    _git(project, "commit", "-q", "-m", "late background commit")
    tip = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", base)
    return tip


# ------------------------------------------------------------------------ KYO-540.c1


def test_accept_refuses_when_the_branch_moved_past_the_reports_last_commit(
    kyo, git_project: Path
) -> None:
    record, dispatch_id, reported, reported_tip = _dispatch_with_a_report(kyo, git_project)
    new_tip = _commit_on_branch(git_project, "kyo-137", "x = 3\n")

    refused = kyo("accept", dispatch_id)
    assert refused.code == EXIT_CONTRACT, refused
    assert CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT in refused.err
    assert new_tip in refused.err and reported_tip in refused.err, "both shas named"
    assert '"amends"' in refused.err and reported.json["report_id"] in refused.err
    assert {c["state"] for c in kyo("show", record, "--json").json["criteria"]} != {"met"}, (
        "nothing may be applied by a refused accept"
    )


def test_an_amending_report_ending_on_the_tip_clears_the_refusal(
    kyo, git_project: Path
) -> None:
    """The remedy the refusal names, followed, makes the same accept succeed."""
    record, dispatch_id, reported, reported_tip = _dispatch_with_a_report(kyo, git_project)
    new_tip = _commit_on_branch(git_project, "kyo-137", "x = 3\n")
    assert kyo("accept", dispatch_id).code == EXIT_CONTRACT

    amended = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        reported_tip,
        criteria_verdicts=[_met(f"{record}.c1")],
        commits=[
            {"sha": reported_tip, "subject": "work on kyo-137"},
            {"sha": new_tip, "subject": "late background commit"},
        ],
        amends=reported.json["report_id"],
    )
    assert amended.code == 0, amended
    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    assert accepted.json["report_id"] == amended.json["report_id"]


def test_accept_does_not_refuse_when_the_branch_is_gone_or_the_report_lists_no_commits(
    kyo, git_project: Path
) -> None:
    """Nothing to compare is not a refusal: a merged-and-deleted branch is the ordinary
    case after a merge."""
    record, dispatch_id, _reported, _tip = _dispatch_with_a_report(kyo, git_project)
    _git(git_project, "branch", "-D", "kyo-137")
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted


# ------------------------------------------------------------------------ KYO-540.c2


def test_report_text_opens_with_a_summary_of_the_counts_that_matter(
    kyo, git_project: Path
) -> None:
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    path = git_project / "r.json"
    import json

    from .conftest import valid_report

    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=sha,
                criteria_verdicts=[_met(f"{record}.c1")],
                premise_false=[{"claim": "a", "correction": "b", "impact": "c"}],
                touched_files=["src/app.py"],
            )
        ),
        encoding="utf-8",
    )
    result = kyo("report", dispatch_id, "--file", str(path))
    assert result.code == 0, result
    first = result.out.splitlines()[0]
    assert first.startswith("summary: report "), first
    assert "premise_false 1" in first and "disagreements 0" in first
    assert "1 met" in first and "open docs obligations 1" in first


def test_accept_text_opens_with_a_summary_that_says_whether_it_is_complete(
    kyo, git_project: Path
) -> None:
    record, dispatch_id, _reported, _tip = _dispatch_with_a_report(kyo, git_project)
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    first = accepted.out.splitlines()[0]
    assert first.startswith("summary: COMPLETE — report "), first
    assert "premise_false 0" in first and "1 met" in first
    assert "open docs obligations" in first

    # `--close` cannot finish the record (it holds an open docs obligation), so the
    # same line says INCOMPLETE and counts what is open.
    incomplete = kyo("accept", dispatch_id, "--close").out.splitlines()[0]
    assert incomplete.startswith("summary: NOT COMPLETE — report "), incomplete
    assert "open docs obligations 1" in incomplete


# ------------------------------------------------------------------------ KYO-544.c1


def test_accept_takes_a_complete_report_whose_commits_are_listed_newest_first(
    kyo, git_project: Path
) -> None:
    """A consumer dispatch, APP-58.d1: every commit listed, newest first, branch unmoved. The
    tip is among commits[], so nothing moved; comparing the tip with commits[-1] (the
    oldest) refused it."""
    record, base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    older = _git(git_project, "rev-parse", "kyo-137")
    newer = _commit_on_branch(git_project, "kyo-137", "x = 4\n")
    reported = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        _git(git_project, "rev-parse", base),
        criteria_verdicts=[_met(f"{record}.c1")],
        commits=[
            {"sha": newer, "subject": "late background commit"},
            {"sha": older, "subject": "work on kyo-137"},
        ],
    )
    assert reported.code == 0, reported
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    assert CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT not in accepted.err
