"""KYO-415: ``kyo accept`` says whether everything it set out to
apply actually landed.

``ok`` was hard-coded true and the exit status is 0 whenever nothing raised — which is
by design, since a tolerated refusal is "a fact about the dispatch rather than a failure
of the acceptance" (the module docstring). That default was kept, so the exit
status stays (c3) and the signal is additive: a
``complete`` field in ``--json`` (c1) and an ``INCOMPLETE`` line in the text (c2).
"""

from __future__ import annotations

from pathlib import Path

from kyosei.cmd_accept import ACCEPT_TOUCHES_NOTHING_TO_COMPARE

from .reference_pages import DISPATCH
from .test_kyo137_docs_obligations import (
    _commit_anything,
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
    _report,
)
from .test_kyo329_accept import _closable, _docs_verdict, _met

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The keys `kyo accept --json` carried before KYO-415 — each keeps its name (c1).
KEYS_BEFORE = {
    "ok", "command", "dispatch_id", "report_id", "record_id", "covers", "reviewed",
    "premise_false", "disagreements", "except_ids", "excepted", "steps",
    "ungated_unobserved", "unmatched_docs_verdicts", "closed", "left_open",
}  # fmt: skip


def _refused_touches_step(kyo, git_project: Path) -> tuple[str, str]:
    """A dispatch whose drift-correction step is refused: the branch is gone and the
    report's one commit is unreachable, so `kyo edit --touches-from-drift` refuses with
    `touch-drift-apply-needs-a-finding` — the tolerated refusal KYO-415 is about."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    _commit_anything(git_project, "the store, before the merge")
    _git(git_project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    _git(git_project, "branch", "-D", "kyo-137")
    reported = _report(
        kyo, git_project, dispatch_id, record, sha,
        criteria_verdicts=[_met(f"{record}.c1")], touched_files=[],
        commits=[{"sha": "1234567890abcdef1234567890abcdef12345678", "subject": "gone"}],
    )  # fmt: skip
    assert reported.code == 0, reported
    return record, dispatch_id


def test_a_refused_step_makes_accept_incomplete(kyo, git_project: Path) -> None:
    """c1 and c2 for a refused step, and c3: the exit status is still 0."""
    _record, dispatch_id = _refused_touches_step(kyo, git_project)
    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, "c3: the exit status keeps today's meaning"
    assert accepted.json["complete"] is False
    assert accepted.json["ok"] is True, "`ok` keeps its meaning"
    assert set(accepted.json) >= KEYS_BEFORE, sorted(KEYS_BEFORE - set(accepted.json))
    assert accepted.json["steps"][2]["refused"]

    text = kyo("accept", dispatch_id)
    assert text.code == 0
    [line] = [line for line in text.out.splitlines() if "INCOMPLETE" in line]
    assert "1 step(s) refused" in line
    assert f"kyo edit {_record} --touches-from-drift {dispatch_id}" in line, (
        "the line names the refused step"
    )


def test_a_record_left_open_by_close_makes_accept_incomplete(kyo, git_project: Path) -> None:
    """c1 and c2 for `--close` leaving a covered record short of completion."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    more = git_project / "more-criteria.txt"
    more.write_text("the banner turns red\n", encoding="utf-8")
    assert kyo("edit", record, "--add-criteria-file", str(more)).code == 0
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        docs_verdicts=[_docs_verdict()], touched_files=["src/app.py"],
    )  # fmt: skip
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--close", "--json")
    assert accepted.code == 0, accepted
    assert [row["record_id"] for row in accepted.json["left_open"]] == [record]
    assert accepted.json["complete"] is False

    [line] = [
        line for line in kyo("accept", dispatch_id, "--close").out.splitlines()
        if "INCOMPLETE" in line
    ]  # fmt: skip
    assert "left open" in line and record in line


def test_a_whole_acceptance_is_complete_and_prints_no_incomplete_line(
    kyo, git_project: Path
) -> None:
    """c1's true arm, and the rerun: a second `--close` finds the record already done,
    which is finished rather than left open."""
    _record, dispatch_id = _closable(kyo, git_project)
    first = kyo("accept", dispatch_id, "--close", "--json")
    assert first.json["complete"] is True, first.json
    second = kyo("accept", dispatch_id, "--close", "--json")
    assert second.json["left_open"] and second.json["complete"] is True
    assert "INCOMPLETE" not in kyo("accept", dispatch_id, "--close").out


def test_a_step_skipped_for_nothing_to_compare_does_not_make_it_incomplete(
    kyo, git_project: Path
) -> None:
    """c1: a skip is not a refusal (KYO-353)."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=[],
    )  # fmt: skip
    assert reported.code == 0, reported
    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.json["steps"][2]["skipped"] == ACCEPT_TOUCHES_NOTHING_TO_COMPARE
    assert accepted.json["complete"] is True
    assert "INCOMPLETE" not in kyo("accept", dispatch_id).out


def test_the_readme_and_the_skill_say_to_read_complete() -> None:
    """c3: the exit status is unchanged, so the documents that describe `kyo accept`
    have to say where the completeness signal is."""
    readme = DISPATCH.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    skill = (REPO_ROOT / "src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md").read_text(
        encoding="utf-8"
    )
    for text in (readme, skill):
        assert "INCOMPLETE" in text
        assert "`complete`" in text


def test_close_leaves_a_gated_record_open_and_clearing_the_gate_completes_it(
    kyo, git_project: Path
) -> None:
    """KYO-490.c3: `--close` reads the open-gate refusal through `_close_blocker`, so a
    gated covered record stays at its status, counts toward ``complete: false`` and the
    INCOMPLETE line, and its left-open row names the gate; ungating and re-running
    `--close` completes it."""
    record, dispatch_id = _closable(kyo, git_project)
    was = kyo("show", record, "--json").json["status"]
    gated = kyo("gate", record, "awaiting:human-review", "--json")
    assert gated.code == 0, gated
    gate_id = gated.json["gate_id"]

    accepted = kyo("accept", dispatch_id, "--close", "--json")
    assert accepted.code == 0, accepted
    assert accepted.json["complete"] is False
    [row] = accepted.json["left_open"]
    assert row["record_id"] == record and row["status"] == was
    assert any(gate_id in line and "awaiting:human-review" in line for line in row["why"])
    assert any(f"kyo ungate {gate_id} --why" in line for line in row["why"])
    assert kyo("show", record, "--json").json["status"] == was, "not walked part way"

    [line] = [
        line for line in kyo("accept", dispatch_id, "--close").out.splitlines()
        if "INCOMPLETE" in line
    ]  # fmt: skip
    assert "left open" in line and record in line

    assert kyo("ungate", gate_id, "--why", "reviewed on the call").code == 0
    again = kyo("accept", dispatch_id, "--close", "--json")
    assert again.code == 0, again
    assert [r["record_id"] for r in again.json["closed"]] == [record]
    assert again.json["complete"] is True
    assert kyo("show", record, "--json").json["status"] == "done"
