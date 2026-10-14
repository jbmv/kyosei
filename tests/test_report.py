"""KYO-7.c3/c4: ``kyo report`` — structured ingest, id cross-checks, ordering,
and the no-auto-apply rule."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR, EXIT_USAGE

from .conftest import git_worktree, new_record, valid_report


def _dispatch(kyo, record: str, **extra) -> dict:
    result = kyo("dispatch", record, "--json", *[str(x) for x in extra.get("args", ())])
    assert result.code == 0, result
    return result.json


def _write(tmp_path: Path, name: str, payload: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_report_ingests_a_valid_report_and_moves_dispatch_to_reported(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("--full", "report", d["dispatch_id"], "--file", path, "--json")
    assert result.code == 0, result
    assert result.json["record"]["dispatches"][0]["state"] == "reported"
    assert result.json["record"]["dispatches"][0]["report_id"] == result.json["report_id"]


def test_report_never_auto_applies_verdicts_to_criteria(kyo, git_project, tmp_path) -> None:
    """KYO-7.c4: ingesting a 'met' verdict must NOT change the criterion's actual
    state — that stays a controller act via `kyo verify`."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    path = _write(tmp_path, "r.json", report)
    kyo("report", d["dispatch_id"], "--file", path)

    shown = kyo("show", record, "--json").json
    assert shown["criteria"][0]["state"] == "unmet-unbuilt", (
        "the report's verdict must not have been applied to the criterion"
    )
    assert shown["criteria"][0]["verifications"] == []


def test_report_refuses_mismatched_dispatch_id(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    # KYO-48: two concurrent dispatches also need declared, disjoint scopes now —
    # undeclared is its own refusal. This test is about report/dispatch id
    # agreement, so give each record what it needs rather than --force past
    # refusals it is not exercising.
    scope_a = tmp_path / "scope-a.txt"
    scope_a.write_text("kyosei/config.yml\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope_a)).code == 0
    d = _dispatch(kyo, record)
    other = new_record(kyo, criteria=["b"])
    scope_b = tmp_path / "scope-b.txt"
    scope_b.write_text("kyosei/kinds.yml\n", encoding="utf-8")
    assert kyo("edit", other, "--touches-file", str(scope_b)).code == 0
    # KYO-22: two concurrent dispatches need somewhere each to work.
    second_tree = tmp_path / "second-worktree"
    git_worktree(kyo.project, second_tree)
    d2 = _dispatch(kyo, other, args=("--worktree", second_tree))
    report = valid_report(
        dispatch_id=d2["dispatch_id"],  # wrong -- report claims a different dispatch
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_CONTRACT
    assert d2["dispatch_id"] in result.err
    assert d["dispatch_id"] in result.err


def test_report_refuses_mismatched_record_id(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id="TST-999",  # wrong record
        worked_from_sha=d["worked_from_sha"],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_CONTRACT
    assert "TST-999" in result.err


def test_report_refuses_a_criterion_verdict_for_an_unknown_criterion(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c99", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR


def test_report_refuses_a_criterion_from_a_different_record(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    other = new_record(kyo, criteria=["b"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{other}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert other in result.err


def test_report_refuses_missing_required_keys(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    broken = {
        "dispatch_id": d["dispatch_id"],
        "record_id": record,
        # worked_from_sha missing, criteria_verdicts missing, etc.
    }
    path = _write(tmp_path, "r.json", broken)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "worked_from_sha" in result.err


def test_report_refuses_bad_verdict_enum(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "sort-of", "method": "reasoned-about"}
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "sort-of" in result.err


def test_report_refuses_a_void_verdict_missing_its_reason(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "void-premise-gone",
                "method": "reasoned-about",
                # no "reason" -- the schema's if/then requires one
            }
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "reason" in result.err


def test_report_refuses_unknown_top_level_keys(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        made_up_field="nope",
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "made_up_field" in result.err


def test_report_refuses_an_unknown_commit_key_and_names_what_it_accepts(
    kyo, git_project, tmp_path
) -> None:
    """KYO-241.c2, reproducing KYO-234.d1's exact failure: a worker put `message`
    on a `commits[]` entry -- the obvious word for the object, and refused because
    it is not one the schema accepts. The refusal must name what IS accepted,
    beside the key that was not, so the fix is one edit against this message
    instead of a second trip to `kyo report-schema`."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": "0123abc", "message": "should have been sha/subject"}],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "message" in result.err
    assert "sha" in result.err
    assert "subject" in result.err


def test_report_refuses_the_retired_commits_summary_key_and_names_the_survivor(
    kyo, git_project, tmp_path
) -> None:
    """KYO-270.c1: `commits[].summary` (KYO-224) and `commits[].subject` (KYO-241)
    meant the same thing under two names -- `summary` is retired, `subject` is the
    survivor, and a report still using the retired key is refused the same way any
    other unknown key is: the unknown-key message names `subject` as what the
    entry accepts. A report already ingested with `summary` stays valid history
    (`test_a_report_without_subject_renders_exactly_as_before` covers reading it
    back); only a *new* report is refused for writing it."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": "0123abc", "summary": "the commit's own subject line"}],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "summary" in result.err
    assert "subject" in result.err
    assert "sha" in result.err


def test_report_refuses_an_unknown_disagreement_key_and_names_what_it_accepts(
    kyo, git_project, tmp_path
) -> None:
    """KYO-241.c2, reproducing KYO-233.d1's exact failure: a worker put `severity`
    on a `disagreements[]` entry. Same remedy as the commit case above."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        disagreements=[
            {
                "topic": "x",
                "brief_said": "y",
                "worker_did": "z",
                "rationale": "because",
                "severity": "high",
            }
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_ERROR
    assert "severity" in result.err
    for key in ("topic", "brief_said", "worker_did", "rationale"):
        assert key in result.err


def test_report_refuses_ingest_against_an_already_reported_dispatch(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"]
    )
    path = _write(tmp_path, "r.json", report)
    first = kyo("report", d["dispatch_id"], "--file", path)
    assert first.code == 0, first

    second = kyo("report", d["dispatch_id"], "--file", path)
    assert second.code == EXIT_CONTRACT
    assert "reported" in second.err


def test_summary_prints_premise_false_and_disagreements_before_any_verdict(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
        premise_false=[{"claim": "the cache was cold", "correction": "it was warm"}],
        disagreements=[
            {
                "topic": "approach",
                "brief_said": "patch the cache layer",
                "worker_did": "patched the eviction policy instead",
                "rationale": "the cache layer was already correct",
            }
        ],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == 0, result

    premise_pos = result.out.index("premise_false")
    disagreements_pos = result.out.index("disagreements")
    verdicts_pos = result.out.index("criteria_verdicts")
    assert premise_pos < verdicts_pos
    assert disagreements_pos < verdicts_pos
    assert "the cache was cold" in result.out
    assert "patched the eviction policy instead" in result.out


#: KYO-208.c1: the review order the `/kyosei` skill mandates, as the strings
#: `kyo report` prints for each. The trailing "(" is load-bearing: every one of
#: these is a counted heading (`premise_false (0):`), so a stray mention of the
#: word inside an obligation's detail cannot be mistaken for the heading.
REVIEW_ORDER: tuple[str, ...] = (
    "premise_false (",
    "disagreements (",
    "corrected_numbers (",
    "criteria_verdicts (",
    "commits (",
)


def test_the_report_review_block_prints_before_obligations_and_drift(
    kyo, git_project, tmp_path
) -> None:
    """KYO-208.c1/c3: the skill says review `premise_false[]` FIRST, and this
    command used to lead with the docs-obligation lines and the touch-drift lines
    instead — so a consumer report described grepping the two apart by hand before
    the report could be read at all. The five review headings print in the skill's
    order, and both findings print after all of them.

    The teeth: move either `lines += _docs_obligation_lines(...)` or
    `lines += drift_lines(...)` back above the `premise_false` heading in
    `report_ingest.cmd_report` and this fails.
    """
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
        premise_false=[{"claim": "the cache was cold", "correction": "it was warm"}],
        commits=[{"sha": "abc1234", "subject": "fix it"}],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == 0, result

    at = [result.out.index(heading) for heading in REVIEW_ORDER]
    assert at == sorted(at), (
        f"review headings out of order: {list(zip(REVIEW_ORDER, at, strict=True))}"
    )
    # Both findings still print — this reorders, it never drops either (law 6).
    obligations_at = result.out.index("docs obligations:")
    drift_at = result.out.index("touch drift:")
    assert at[-1] < obligations_at, "docs obligations must print after `commits`"
    assert at[-1] < drift_at, "touch drift must print after `commits`"
    assert at[0] < obligations_at, "premise_false must be read before the obligations"
    assert at[0] < drift_at, "premise_false must be read before the drift"


def test_the_report_json_payload_keys_did_not_move_with_the_lines(
    kyo, git_project, tmp_path
) -> None:
    """KYO-208.c1's other half: a machine consumer reads keys, never line order, so
    the reorder above must be invisible to `--json` (law 3)."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path, "--json")
    assert result.code == 0, result
    for key in (
        "premise_false",
        "disagreements",
        "corrected_numbers",
        "criteria_verdicts",
        "commits",
        "docs_obligations",
        "touch_drift",
        "branch_check",
    ):
        assert key in result.json, f"--json lost the {key!r} key"


def test_the_schema_says_a_clarification_does_not_belong_in_premise_false(kyo) -> None:
    """KYO-208.c2: a worker filed a premise_false entry whose correction began "This
    is consistent, not false". The array-level description said what the slot is FOR
    and never what does not belong in it; `kyo report-schema` prints the wording that
    now does. Delete the sentence from `src/kyosei/report.schema.json` and this
    fails."""
    printed = kyo("report-schema", "--json")
    assert printed.code == 0, printed
    said = printed.json["properties"]["premise_false"]["description"]
    assert "the observation that falsified it" in said
    assert "belongs in 'summary'" in said
    # The same document, as bytes, is what a worker actually reads.
    assert "the observation that falsified it" in kyo("report-schema").out


def test_report_stores_every_array_structurally_and_it_round_trips(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
        premise_false=[{"claim": "x", "correction": "y"}],
        disagreements=[
            {"topic": "t", "brief_said": "b", "worker_did": "w", "rationale": "r"}
        ],
        corrected_numbers=[
            {"claim": "count", "brief_value": "5", "actual_value": "7", "method": "recount"}
        ],
        commits=[{"sha": "abc1234", "subject": "fix it"}],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path, "--json")
    assert result.code == 0, result

    shown = kyo("show", record, "--json").json
    stored = shown["reports"][0]["body"]
    assert stored["premise_false"] == report["premise_false"]
    assert stored["disagreements"] == report["disagreements"]
    assert stored["corrected_numbers"] == report["corrected_numbers"]
    assert stored["criteria_verdicts"] == report["criteria_verdicts"]
    assert stored["commits"] == report["commits"]


# --------------------------------------------------------------------- KYO-10


def test_report_notes_the_expected_one_commit_offset_as_not_a_problem(
    kyo, git_project, tmp_path
) -> None:
    """KYO-10: `kyo dispatch` stamps its base sha, then (in the real controller
    repo) a separate commit lands recording the dispatch itself -- so a worker's
    real worktree branch point is expected to be one commit ahead of the brief's
    stated base sha. That expected, by-construction offset must read as expected,
    not as generic "worth checking" noise (the recurring-noise bug KYO-10 filed)."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)

    (git_project / "after-dispatch.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=git_project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "after dispatch"], cwd=git_project, check=True)
    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_project, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert new_head != d["worked_from_sha"]

    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=new_head
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == 0, result
    assert "expected by construction" in result.out
    assert "worth checking" not in result.out


def test_report_still_flags_a_worked_from_sha_with_no_ancestry_relation(
    kyo, git_project, tmp_path
) -> None:
    """A worked_from_sha that is NOT reachable from the dispatch's recorded base
    (unrelated/unknown history) is still worth a controller's attention -- KYO-10
    narrows the noise, it does not remove the check."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha="0" * 40
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == 0, result
    assert "worth checking" in result.out
    assert "expected by construction" not in result.out


# ------------------------------------------------------------------------- KYO-32
#
# A dispatch declares its branch, and a report's commits must be on it. The defect,
# as a consumer reported it: the worker made a branch of its own rather than using the
# one the harness declared, and the merge targeted an empty branch and said "up to
# date", which would have silently shipped nothing. The
# failure is silent in the worst way -- `git merge` of an untouched branch reports
# "Already up to date" and exits 0, so the controller's own success signal lies.
# `kyo report` is the last point before that merge at which the tool can notice.


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit_everything(project: Path, message: str) -> None:
    """Park the store churn `kyo` itself writes, so later git operations (which
    require a clean tree) are not blocked by it."""
    _git(project, "add", "-A")
    if _git(project, "status", "--porcelain"):
        _git(project, "commit", "-q", "-m", message)


def _commit_on_new_branch(project: Path, branch: str, filename: str) -> str:
    """Create ``branch`` off HEAD, commit one file on it, return the commit's sha."""
    _git(project, "checkout", "-q", "-b", branch)
    (project / filename).write_text("worker output", encoding="utf-8")
    _git(project, "add", filename)
    _git(project, "commit", "-q", "-m", f"work on {branch}")
    return _git(project, "rev-parse", "HEAD")


def test_report_refuses_a_commit_that_is_not_on_the_declared_branch(
    kyo, git_project, tmp_path
) -> None:
    """KYO-32.c2, the consumer's exact case: the harness declared `declared`, the
    worker committed on `stray`, and the merge the controller was about to run would
    have reported success while shipping nothing."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "declared"))
    _commit_everything(git_project, "store churn")
    _git(git_project, "branch", "declared")
    stray_sha = _commit_on_new_branch(git_project, "stray", "worker.txt")

    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": stray_sha}],
    )
    path = _write(tmp_path, "r.json", report)
    result = kyo("report", d["dispatch_id"], "--file", path)
    assert result.code == EXIT_CONTRACT, result
    assert "report-commit-off-branch" in result.err
    assert "declared" in result.err  # names the declared branch
    assert stray_sha in result.err  # names the offending sha
    # Refused means refused: nothing was ingested, and the dispatch is still running.
    assert kyo("show", record, "--json").json["dispatches"][0]["state"] == "running"


def test_moving_the_commit_onto_the_declared_branch_clears_the_refusal(
    kyo, git_project, tmp_path
) -> None:
    """CLAUDE.md's standing rule: an error message that names a remedy owes a test
    proving the remedy actually clears it."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "declared"))
    _commit_everything(git_project, "store churn")
    _git(git_project, "branch", "declared")
    stray_sha = _commit_on_new_branch(git_project, "stray", "worker.txt")

    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": stray_sha}],
    )
    assert kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report)).code == (
        EXIT_CONTRACT
    )

    # The remedy the message names: move the commit onto the declared branch.
    _git(git_project, "checkout", "-q", "declared")
    _git(git_project, "cherry-pick", stray_sha)
    moved_sha = _git(git_project, "rev-parse", "HEAD")

    report["commits"] = [{"sha": moved_sha}]
    result = kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r2.json", report))
    assert result.code == 0, result
    assert "branch check: VERIFIED" in result.out


def test_report_accepts_and_says_so_when_the_commit_is_on_the_declared_branch(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "declared"))
    _commit_everything(git_project, "store churn")
    sha = _commit_on_new_branch(git_project, "declared", "worker.txt")

    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": sha}],
    )
    result = kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report), "--json")
    assert result.code == 0, result
    check = result.json["branch_check"]
    assert check["branch"] == "declared"
    assert check["outcome"] == "verified"
    assert check["commits"] == [
        {"sha": sha, "outcome": "reachable", "detail": "reachable from declared"}
    ]


def test_cannot_tell_is_its_own_outcome_and_is_distinguishable_from_reachable(
    kyo, git_project, tmp_path
) -> None:
    """KYO-32.c3, law 6. Two reports against two dispatches on the same declared
    branch: one commit git can resolve, one it cannot. The point is not that the
    second is refused -- it is that a controller reading the output cannot mistake
    "could not be determined" for "checked and fine". Exit codes alone would not
    establish that, so this compares the rendered text of both."""
    first = new_record(kyo, kind="bug", title="First", criteria=["a"])
    second = new_record(kyo, kind="bug", title="Second", criteria=["b"])
    d_ok = _dispatch(kyo, first, args=("--no-create-worktree", "--branch", "declared"))
    d_unknown = _dispatch(
        kyo, second, args=("--no-create-worktree", "--branch", "declared", "--force")
    )
    _commit_everything(git_project, "store churn")
    sha = _commit_on_new_branch(git_project, "declared", "worker.txt")

    reachable = kyo(
        "report",
        d_ok["dispatch_id"],
        "--file",
        _write(
            tmp_path,
            "ok.json",
            valid_report(
                dispatch_id=d_ok["dispatch_id"],
                record_id=first,
                worked_from_sha=d_ok["worked_from_sha"],
                commits=[{"sha": sha}],
            ),
        ),
    )
    assert reachable.code == 0, reachable

    unknown = kyo(
        "report",
        d_unknown["dispatch_id"],
        "--file",
        _write(
            tmp_path,
            "unknown.json",
            valid_report(
                dispatch_id=d_unknown["dispatch_id"],
                record_id=second,
                worked_from_sha=d_unknown["worked_from_sha"],
                # A well-formed sha that is no object in this repository: exactly the
                # "cannot tell" case -- git cannot say reachable OR not reachable.
                commits=[{"sha": "0" * 40}],
            ),
        ),
    )
    assert unknown.code == 0, unknown

    assert "branch check: VERIFIED" in reachable.out
    assert "CANNOT TELL" not in reachable.out

    assert "branch check: CANNOT TELL" in unknown.out
    assert "VERIFIED" not in unknown.out
    assert "This is NOT a pass" in unknown.out
    # It says *what* could not be told, not merely that something could not be.
    assert "is not an object this repository knows" in unknown.out


def test_an_unknown_branch_is_cannot_tell_and_names_the_branch_as_the_reason(
    kyo, git_project, tmp_path
) -> None:
    """The other half of c3's "sha or branch unknown in this repo": the sha resolves
    and the branch does not. `merge-base --is-ancestor` exits 128 for both, so the
    two are told apart by probing each ref, not by the comparison's exit code."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "never-created"))
    _commit_everything(git_project, "store churn")
    sha = _git(git_project, "rev-parse", "HEAD")

    result = kyo(
        "report",
        d["dispatch_id"],
        "--file",
        _write(
            tmp_path,
            "r.json",
            valid_report(
                dispatch_id=d["dispatch_id"],
                record_id=record,
                worked_from_sha=d["worked_from_sha"],
                commits=[{"sha": sha}],
            ),
        ),
        "--json",
    )
    assert result.code == 0, result
    check = result.json["branch_check"]
    assert check["outcome"] == "cannot-tell"
    assert check["commits"][0]["outcome"] == "cannot-tell"
    assert "does not resolve to a commit" in check["commits"][0]["detail"]


def test_report_says_it_did_not_check_when_no_branch_was_declared(
    kyo, git_project, tmp_path
) -> None:
    """A dispatch without --branch behaves exactly as before -- but the fact that
    nothing was checked is stated, not left as silence for a controller to read as
    a pass."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo(
        "report",
        d["dispatch_id"],
        "--file",
        _write(
            tmp_path,
            "r.json",
            valid_report(
                dispatch_id=d["dispatch_id"],
                record_id=record,
                worked_from_sha=d["worked_from_sha"],
                commits=[{"sha": "0" * 40}],
            ),
        ),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["branch_check"]["outcome"] == "not-declared"
    assert result.json["branch_check"]["branch"] is None


def test_a_declared_branch_with_no_reported_commits_says_there_was_nothing_to_check(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "declared"))
    result = kyo(
        "report",
        d["dispatch_id"],
        "--file",
        _write(
            tmp_path,
            "r.json",
            valid_report(
                dispatch_id=d["dispatch_id"],
                record_id=record,
                worked_from_sha=d["worked_from_sha"],
            ),
        ),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["branch_check"]["outcome"] == "no-commits"


# ---------------------------------------------------------------------------- KYO-90
#
# A worker resumed on the same dispatch after its first report was ingested. Every
# test below is about one property: the corrected account reaches the store, the
# earlier one is still there and readable, and `kyo report`'s own consumers run
# against whichever report is current — never against the one that was replaced.


def _first_report(kyo, tmp_path, record: str, d: dict, **extra) -> str:
    """Ingest a first report and return its id."""
    result = kyo(
        "report",
        d["dispatch_id"],
        "--file",
        _write(
            tmp_path,
            "first.json",
            valid_report(
                dispatch_id=d["dispatch_id"],
                record_id=record,
                worked_from_sha=d["worked_from_sha"],
                **extra,
            ),
        ),
        "--json",
    )
    assert result.code == 0, result
    return result.json["report_id"]


def test_a_second_report_that_declares_nothing_is_still_refused(
    kyo, git_project, tmp_path
) -> None:
    """KYO-90 does not relax `dispatch-already-terminal`. A different account
    landing silently over the first is a worse failure than the one being fixed,
    so the refusal survives — what changes is that it now names the way out."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)

    second = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "second.json",
        valid_report(
            dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"]
        ),
    ))
    assert second.code == EXIT_CONTRACT, second
    assert "dispatch-already-terminal" in second.err
    # The remedy is named, and it names the report to amend rather than saying
    # "use --amend" and leaving the controller to work out against what.
    assert "amends" in second.err
    assert first_id in second.err


def test_the_remedy_the_refusal_names_actually_ingests_the_corrected_report(
    kyo, git_project, tmp_path
) -> None:
    """CLAUDE.md's standing rule: an error message naming a remedy owes a test
    proving the remedy clears it. This is also KYO-90.c1 end to end."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)

    corrected = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        disagreements=[
            {
                "topic": "the third finding, made after the merge",
                "brief_said": "nothing — it could not have",
                "worker_did": "found it and reported it",
                "rationale": "the finding is the point",
            }
        ],
        amends=first_id,
    )
    result = kyo(
        "report", d["dispatch_id"], "--file", _write(tmp_path, "amended.json", corrected), "--json"
    )
    assert result.code == 0, result
    assert result.json["amends"] == first_id
    assert result.json["report_id"] != first_id

    shown = kyo("show", record, "--json").json
    # KYO-90.c1: the corrected report reached the store, AND the earlier one is
    # still there — superseded, not overwritten and not deleted.
    ids = [r["id"] for r in shown["reports"]]
    assert ids == [first_id, result.json["report_id"]], shown["reports"]
    assert shown["dispatches"][0]["report_id"] == result.json["report_id"]
    assert shown["dispatches"][0]["state"] == "reported"
    # The superseded report still says exactly what it said.
    first_body = next(r for r in shown["reports"] if r["id"] == first_id)["body"]
    assert first_body["disagreements"] == []
    current_body = next(
        r for r in shown["reports"] if r["id"] == result.json["report_id"]
    )["body"]
    assert len(current_body["disagreements"]) == 1


def test_an_amendment_naming_a_report_that_is_not_the_current_one_is_refused(
    kyo, git_project, tmp_path
) -> None:
    """The anti-silent-overwrite half. A correction written against an account
    that has itself already been superseded was written without reading what it
    replaces, and is refused rather than reconciled."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)
    second = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=first_id,
        ),
    ), "--json")
    assert second.code == 0, second

    stale = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "stale.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=first_id,  # still pointing at r1, which r2 already replaced
        ),
    ))
    assert stale.code == EXIT_CONTRACT, stale
    assert "report-amends-wrong-report" in stale.err
    assert second.json["report_id"] in stale.err
    # Refused means refused: the store still has exactly two reports.
    assert len(kyo("show", record, "--json").json["reports"]) == 2


def test_amending_a_dispatch_that_has_never_reported_is_refused(
    kyo, git_project, tmp_path
) -> None:
    """A correction aimed at an account that does not exist. Refused rather than
    quietly ingested as a first report, which would file a claim about
    supersession the store has no way to make true."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "r.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=f"{record}.r1",
        ),
    ))
    assert result.code == EXIT_CONTRACT, result
    assert "report-amends-wrong-report" in result.err
    assert kyo("show", record, "--json").json["reports"] == []


def test_an_amendment_reruns_the_branch_check_and_refuses_an_off_branch_commit(
    kyo, git_project, tmp_path
) -> None:
    """KYO-90.c2, the half with teeth. On KYO-83.d1 the branch check never ran
    against the final state — the third commit was never confirmed reachable from
    the declared branch. An amendment goes through the same gate as a first
    report, and that gate still refuses."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "declared"))
    _commit_everything(git_project, "store churn")
    on_branch = _commit_on_new_branch(git_project, "declared", "worker.txt")
    first_id = _first_report(kyo, tmp_path, record, d, commits=[{"sha": on_branch}])

    _git(git_project, "checkout", "-q", "-b", "stray")
    (git_project / "stray.txt").write_text("elsewhere", encoding="utf-8")
    _git(git_project, "add", "stray.txt")
    _git(git_project, "commit", "-q", "-m", "on the wrong branch")
    stray_sha = _git(git_project, "rev-parse", "HEAD")

    refused = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            commits=[{"sha": on_branch}, {"sha": stray_sha}],
            amends=first_id,
        ),
    ))
    assert refused.code == EXIT_CONTRACT, refused
    assert "report-commit-off-branch" in refused.err
    assert stray_sha in refused.err
    # The first report is untouched and still current: a refused amendment
    # changes nothing at all.
    shown = kyo("show", record, "--json").json
    assert [r["id"] for r in shown["reports"]] == [first_id]
    assert shown["dispatches"][0]["report_id"] == first_id


def test_an_amendment_verifies_the_commit_the_first_report_did_not_carry(
    kyo, git_project, tmp_path
) -> None:
    """The KYO-83 shape exactly: a third commit lands after the first report, and
    the amended report is the one the branch check gets to see."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record, args=("--no-create-worktree", "--branch", "declared"))
    _commit_everything(git_project, "store churn")
    first_sha = _commit_on_new_branch(git_project, "declared", "worker.txt")
    first_id = _first_report(kyo, tmp_path, record, d, commits=[{"sha": first_sha}])

    (git_project / "fix.txt").write_text("the fix found after the merge", encoding="utf-8")
    _git(git_project, "add", "fix.txt")
    _git(git_project, "commit", "-q", "-m", "the third commit")
    fix_sha = _git(git_project, "rev-parse", "HEAD")

    result = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            commits=[{"sha": first_sha}, {"sha": fix_sha}],
            amends=first_id,
        ),
    ), "--json")
    assert result.code == 0, result
    check = result.json["branch_check"]
    assert check["outcome"] == "verified"
    assert [c["sha"] for c in check["commits"]] == [first_sha, fix_sha]


def test_touch_drift_after_an_amendment_reads_the_amended_report(
    kyo, git_project, tmp_path
) -> None:
    """KYO-90.c2's other half. Drift compares the record's declared touches
    against the CURRENT report's touched_files[], both at ingest and on a later
    `kyo touch-drift` — never against the account that was replaced."""
    record = new_record(kyo, criteria=["a"])
    (git_project / "src").mkdir(exist_ok=True)
    (git_project / "src" / "declared.py").write_text("# declared\n", encoding="utf-8")
    (git_project / "src" / "found_later.py").write_text(
        "# found after the merge\n", encoding="utf-8"
    )
    scope = tmp_path / "touches.txt"
    scope.write_text("src/declared.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d, touched_files=["src/declared.py"])
    # The first report is clean: nothing outside the record's claim.
    first_drift = kyo("touch-drift", d["dispatch_id"], "--json")
    assert first_drift.json["touch_drift"]["outcome"] == "none", first_drift

    ingest = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            touched_files=["src/declared.py", "src/found_later.py"],
            amends=first_id,
        ),
    ), "--json")
    assert ingest.code == 0, ingest
    assert ingest.json["touch_drift"]["outcome"] == "drift", ingest.json["touch_drift"]
    assert "src/found_later.py" in ingest.json["touch_drift"]["undeclared_files"]

    later = kyo("touch-drift", d["dispatch_id"], "--json")
    assert later.code == 0, later
    assert "src/found_later.py" in later.json["touch_drift"]["undeclared_files"]


def test_show_says_which_report_is_current_and_that_an_earlier_one_exists(
    kyo, git_project, tmp_path
) -> None:
    """KYO-90.c3. A reader must not be able to mistake the superseded report for
    the account of what happened."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)
    amended = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=first_id,
        ),
    ), "--json")
    assert amended.code == 0, amended
    current_id = amended.json["report_id"]

    out = kyo("show", record).out
    assert "reports (2, 1 superseded):" in out
    assert f"{first_id}" in out
    assert "[superseded]" in out
    assert "[current]" in out
    assert f"SUPERSEDED by {current_id}" in out
    assert f"amends {first_id}" in out
    # The dispatch line must not name the superseded report as "the" report.
    assert f"report: {current_id}  (current; supersedes {first_id})" in out


def test_an_amendment_does_not_reopen_a_closed_dispatch(kyo, git_project, tmp_path) -> None:
    """The live instance arrived after the dispatch was closed (KYO-83.d1 was
    closed before the correction was found), so a mechanism that refused a closed
    dispatch would have refused the only case it was built for. It is accepted,
    and it changes neither `state` nor `closed_at`."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)
    assert kyo("dispatch", "close", d["dispatch_id"]).code == 0
    closed_at = kyo("show", record, "--json").json["dispatches"][0]["closed_at"]
    assert closed_at

    result = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=first_id,
        ),
    ))
    assert result.code == 0, result
    assert "stays closed" in result.out
    after = kyo("show", record, "--json").json["dispatches"][0]
    assert after["state"] == "reported"
    assert after["closed_at"] == closed_at


def test_doctor_and_a_rebuild_both_accept_an_amended_dispatch(
    kyo, git_project, tmp_path
) -> None:
    """The amendment relation is stored inside the report document, so it needs no
    schema change — which is only true if the export round-trips it and every
    integrity check agrees with the result."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)
    assert kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=first_id,
        ),
    )).code == 0

    assert kyo("rebuild", "--force").code == 0
    doctor = kyo("doctor", "--json")
    assert doctor.code == 0, doctor
    shown = kyo("show", record, "--json").json
    assert len(shown["reports"]) == 2


def test_doctor_catches_a_second_report_with_no_stated_relation(
    kyo, git_project, tmp_path
) -> None:
    """The mechanism half of KYO-90.c1, held down the way
    check_restate_backlink_symmetry holds down its relation: if a second report
    ever reaches the store without saying what it supersedes, `kyo doctor` says
    so rather than leaving two competing accounts to be read as one."""
    from kyosei.doctor_dispatch import check_report_amendment_chain
    from kyosei.store import open_store

    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first_id = _first_report(kyo, tmp_path, record, d)
    amended = kyo("report", d["dispatch_id"], "--file", _write(
        tmp_path,
        "amended.json",
        valid_report(
            dispatch_id=d["dispatch_id"],
            record_id=record,
            worked_from_sha=d["worked_from_sha"],
            amends=first_id,
        ),
    ), "--json")
    assert amended.code == 0, amended

    store = open_store(git_project)
    assert check_report_amendment_chain(store) == []
    # Strip the declaration back out, behind the command's back: two reports, no
    # stated relation. That is the state the check exists to name.
    body = json.loads(
        store.conn.execute(
            "SELECT body_json FROM reports WHERE id = ?", (amended.json["report_id"],)
        ).fetchone()["body_json"]
    )
    body.pop("amends")
    store.conn.execute(
        "UPDATE reports SET body_json = ? WHERE id = ?",
        (json.dumps(body), amended.json["report_id"]),
    )
    failures = check_report_amendment_chain(store)
    assert any(first_id in f and "nothing amends it" in f for f in failures), failures


# ------------------------------------------------------------------ KYO-198


def _reported(kyo, tmp_path, record: str, verdicts: list[dict], name: str = "r.json") -> dict:
    """Dispatch `record`, ingest a report carrying `verdicts`, return the report payload."""
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record,
        worked_from_sha=d["worked_from_sha"], criteria_verdicts=verdicts,
    )  # fmt: skip
    ingested = kyo(
        "report", d["dispatch_id"], "--file", _write(tmp_path, name, report), "--json"
    )
    assert ingested.code == 0, ingested
    return ingested.json


def test_verify_from_report_applies_the_reports_own_verdicts_as_one_act(
    kyo, git_project, tmp_path
) -> None:
    """KYO-198.c1. Every field `kyo verify` asks for is already in the report, in the
    shape it asks for it — so it is read, not retyped. The verification carries the
    entry's method, its evidence (as an evidence artifact on the criterion) and its
    reason, and it is attributed to the controller running this command, not to the
    worker who reported it."""
    record = new_record(kyo, criteria=["a", "b"])
    ingested = _reported(
        kyo, tmp_path, record,
        [
            {
                "criterion_id": f"{record}.c1", "verdict": "met",
                "method": "machine-checked",
                "evidence": "make test: 1907 passed, exit=0",
            },
            {
                "criterion_id": f"{record}.c2", "verdict": "unmet-unobserved",
                "method": "reasoned-about",
                "reason": "the surface it needs was not reachable from the worktree",
            },
        ],
    )  # fmt: skip

    applied = kyo(
        "verify", "--from-report", ingested["dispatch_id"], "--actor", "the-controller", "--json",
    )  # fmt: skip
    assert applied.code == 0, applied
    by_criterion = {row["criterion_id"]: row for row in applied.json["applied"]}
    assert set(by_criterion) == {f"{record}.c1", f"{record}.c2"}
    assert applied.json["not_applied"] == []
    assert applied.json["report_id"] == ingested["report_id"]

    # c1: the id it minted is printed, per criterion, and is a real verification.
    for criterion_id, row in by_criterion.items():
        assert row["verification_id"] in applied.out, applied.out
        assert criterion_id in applied.out

    shown = kyo("show", record, "--json").json
    states = {c["id"]: c for c in shown["criteria"]}
    assert states[f"{record}.c1"]["state"] == "met"
    assert states[f"{record}.c2"]["state"] == "unmet-unobserved"
    assert states[f"{record}.c2"]["reason"] == (
        "the surface it needs was not reachable from the worktree"
    )

    recorded = states[f"{record}.c1"]["verifications"][-1]
    assert recorded["method"] == "machine-checked"
    assert recorded["author"] == "the-controller", (
        "applying a worker's verdict is the controller's act; the verification carries "
        "who applied it, not who reported it"
    )
    assert recorded["evidence_id"], (
        "the entry carried evidence and it must land as an evidence artifact, not vanish"
    )
    evidence = {e["id"]: e for e in shown["evidence_artifacts"]}
    carried = evidence[recorded["evidence_id"]]
    assert carried["text"].strip() == "make test: 1907 passed, exit=0"
    assert carried["supports_id"] == f"{record}.c1"
    assert states[f"{record}.c2"]["verifications"][-1]["method"] == "reasoned-about"


def test_verify_from_report_only_applies_the_named_criteria(
    kyo, git_project, tmp_path
) -> None:
    """KYO-198.c2, the half that applies. The rest stay exactly as they were and stay
    readable in the report — that is what makes `--only` a way to apply what you have
    checked rather than a way to lose the rest."""
    record = new_record(kyo, criteria=["a", "b"])
    ingested = _reported(
        kyo, tmp_path, record,
        [
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"},
            {"criterion_id": f"{record}.c2", "verdict": "met", "method": "reasoned-about"},
        ],
    )  # fmt: skip
    applied = kyo(
        "verify", "--from-report", ingested["dispatch_id"], "--only", f"{record}.c1", "--json",
    )  # fmt: skip
    assert applied.code == 0, applied
    assert [row["criterion_id"] for row in applied.json["applied"]] == [f"{record}.c1"]
    states = {c["id"]: c["state"] for c in kyo("show", record, "--json").json["criteria"]}
    assert states[f"{record}.c1"] == "met"
    assert states[f"{record}.c2"] == "unmet-unbuilt", (
        "--only must leave the criteria it did not name alone"
    )


def test_verify_from_report_refuses_an_only_the_report_has_no_verdict_for(
    kyo, git_project, tmp_path
) -> None:
    """KYO-198.c2, the half that refuses. A mistyped criterion id in `--only` is the
    failure this flag makes possible, and answering it with "applied 0" would look
    exactly like success."""
    record = new_record(kyo, criteria=["a", "b"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}],
    )  # fmt: skip
    refused = kyo(
        "verify", "--from-report", ingested["dispatch_id"], "--only", f"{record}.c2",
    )  # fmt: skip
    assert refused.code == EXIT_CONTRACT, refused
    assert f"{record}.c2" in refused.err, refused.err
    assert "verify-only-names-an-absent-criterion" in refused.err
    states = {c["id"]: c["state"] for c in kyo("show", record, "--json").json["criteria"]}
    assert states == {f"{record}.c1": "unmet-unbuilt", f"{record}.c2": "unmet-unbuilt"}, (
        "a refusal must apply nothing at all"
    )

    # The remedy the message names -- drop the offender -- clears it (standing rule).
    cleared = kyo(
        "verify", "--from-report", ingested["dispatch_id"], "--only", f"{record}.c1", "--json",
    )  # fmt: skip
    assert cleared.code == 0, cleared
    assert [row["criterion_id"] for row in cleared.json["applied"]] == [f"{record}.c1"]


def test_verify_from_report_refuses_a_dispatch_with_no_report_and_kyo_report_clears_it(
    kyo, git_project, tmp_path
) -> None:
    """KYO-198.c3, both halves in one test because the second is only meaningful after
    the first: the message names `kyo report` as the remedy, and running exactly that
    makes the same command succeed."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    refused = kyo("verify", "--from-report", d["dispatch_id"])
    assert refused.code == EXIT_CONTRACT, refused
    assert "verify-from-report-needs-a-report" in refused.err
    assert f"`kyo report {d['dispatch_id']} --file" in refused.err, refused.err

    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )  # fmt: skip
    ingest = kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report))
    assert ingest.code == 0, ingest

    cleared = kyo("verify", "--from-report", d["dispatch_id"], "--json")
    assert cleared.code == 0, cleared
    assert [row["criterion_id"] for row in cleared.json["applied"]] == [f"{record}.c1"]


def test_verify_from_report_reads_the_current_report_not_the_superseded_one(
    kyo, git_project, tmp_path
) -> None:
    """Reports are superseded, never overwritten (KYO-90), and the dispatch row names
    which one is current. An amended report is what gets applied — the earlier verdict
    is exactly the account the amendment exists to replace."""
    record = new_record(kyo, criteria=["a"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}],
    )  # fmt: skip
    d_id = ingested["dispatch_id"]
    amended = valid_report(
        dispatch_id=d_id, record_id=record,
        worked_from_sha=kyo("show", record, "--json").json["dispatches"][0]["worked_from_sha"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1", "verdict": "unmet-unbuilt",
                "method": "reasoned-about",
            }
        ],
        amends=ingested["report_id"],
    )  # fmt: skip
    second = kyo("report", d_id, "--file", _write(tmp_path, "r2.json", amended), "--json")
    assert second.code == 0, second

    applied = kyo("verify", "--from-report", d_id, "--json")
    assert applied.code == 0, applied
    assert applied.json["report_id"] == second.json["report_id"]
    assert applied.json["applied"][0]["verdict"] == "unmet-unbuilt"


def test_a_void_verdict_is_reported_as_not_applied_and_names_the_command_that_does(
    kyo, git_project, tmp_path
) -> None:
    """The report schema allows five verdicts and `kyo verify` records three. The two
    void states are premise judgments, not verified facts (KYO-4.c3), so routing them
    through here would make `--from-report` a way around a refusal that exists on
    purpose. They are named as not applied, with the command that does apply them."""
    record = new_record(kyo, criteria=["a", "b"])
    ingested = _reported(
        kyo, tmp_path, record,
        [
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"},
            {
                "criterion_id": f"{record}.c2", "verdict": "void-premise-gone",
                "method": "reasoned-about",
                "reason": "the subsystem it was about was deleted",
            },
        ],
    )  # fmt: skip
    applied = kyo("verify", "--from-report", ingested["dispatch_id"], "--json")
    assert applied.code == 0, applied
    assert [row["criterion_id"] for row in applied.json["applied"]] == [f"{record}.c1"]
    skipped = applied.json["not_applied"]
    assert [row["criterion_id"] for row in skipped] == [f"{record}.c2"]
    assert "kyo edit" in skipped[0]["why"] and "--state void-premise-gone" in skipped[0]["why"]
    printed = kyo("verify", "--from-report", ingested["dispatch_id"])
    assert printed.code == 0, printed
    assert "not applied (1)" in printed.out and f"{record}.c2" in printed.out
    states = {c["id"]: c["state"] for c in kyo("show", record, "--json").json["criteria"]}
    assert states[f"{record}.c2"] == "unmet-unbuilt", (
        "a void verdict must not be applied by a command that does not record void states"
    )


def test_a_criterion_restated_since_the_report_is_not_verified_under_its_old_id(
    kyo, git_project, tmp_path, write_file
) -> None:
    """KYO-58 mints a new id for restated wording and voids the old one; a report
    written before the restatement names the old id. Recording a verdict against
    superseded text is the evidence re-attachment restatement exists to prevent, so
    the entry is skipped, the replacement is named, and the rest of the batch still
    applies."""
    record = new_record(kyo, criteria=["a", "b"])
    ingested = _reported(
        kyo, tmp_path, record,
        [
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"},
            {"criterion_id": f"{record}.c2", "verdict": "met", "method": "reasoned-about"},
        ],
    )  # fmt: skip
    restated = kyo(
        "restate", f"{record}.c2",
        "--text-file", write_file("rt.txt", "b, said more sharply\n"), "--json",
    )  # fmt: skip
    assert restated.code == 0, restated

    applied = kyo("verify", "--from-report", ingested["dispatch_id"], "--json")
    assert applied.code == 0, applied
    assert [row["criterion_id"] for row in applied.json["applied"]] == [f"{record}.c1"]
    skipped = applied.json["not_applied"]
    assert [row["criterion_id"] for row in skipped] == [f"{record}.c2"]
    assert "restated as" in skipped[0]["why"] and "KYO-58" in skipped[0]["why"]
    states = {c["id"]: c["state"] for c in kyo("show", record, "--json").json["criteria"]}
    assert states[f"{record}.c2"] == "void-withdrawn", "the voided criterion stays voided"


def test_from_report_refuses_the_flags_it_reads_from_the_report(
    kyo, git_project, tmp_path
) -> None:
    """A verdict typed beside `--from-report` is the retyping this path removes, and
    honouring one would silently overwrite what a worker actually reported."""
    record = new_record(kyo, criteria=["a"])
    ingested = _reported(
        kyo, tmp_path, record,
        [{"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}],
    )  # fmt: skip
    for extra in (
        ["--verdict", "met"],
        ["--method", "reasoned-about"],
        [f"{record}.c1"],
    ):
        refused = kyo("verify", "--from-report", ingested["dispatch_id"], *extra)
        assert refused.code == EXIT_USAGE, (extra, refused)
        assert "--from-report reads the verdict" in refused.err, refused.err
    assert kyo("show", record, "--json").json["criteria"][0]["state"] == "unmet-unbuilt"


def test_only_without_from_report_is_refused_rather_than_ignored(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    refused = kyo("verify", f"{record}.c1", "--verdict", "met", "--method",
                  "reasoned-about", "--only", f"{record}.c1")  # fmt: skip
    assert refused.code == EXIT_USAGE, refused
    assert "--only" in refused.err and "--from-report" in refused.err


def test_verify_without_from_report_still_needs_its_three_arguments(kyo, git_project) -> None:
    """The positional and the two flags stopped being argparse-required so that
    `--from-report` could supply them. What must not change is that omitting one
    without `--from-report` still fails, and still says which."""
    record = new_record(kyo, criteria=["a"])
    refused = kyo("verify", f"{record}.c1", "--verdict", "met")
    assert refused.code == EXIT_USAGE, refused
    assert "--method" in refused.err
    assert "--from-report" in refused.err, (
        "the message must name the other form, or a reader learns only half the command"
    )
    assert kyo("show", record, "--json").json["criteria"][0]["state"] == "unmet-unbuilt"


def test_the_report_note_names_from_report_beside_kyo_verify(
    kyo, git_project, tmp_path
) -> None:
    """KYO-198.c4. The note that creates the need names the mechanism that meets it,
    with this dispatch's own id in it rather than a placeholder to fill in."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )  # fmt: skip
    ingested = kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report))
    assert ingested.code == 0, ingested
    assert "NOT applied" in ingested.out
    assert f"`kyo verify --from-report {d['dispatch_id']}`" in ingested.out, ingested.out
    assert "--only" in ingested.out


# ------------------------------------------------------------------------ KYO-224
#
# A consumer report described a worker's report refused for one null `notes` field,
# fixed by hand before ingesting. Serialising an unset
# optional as `null` is what a typed struct does; being refused for it is a refusal
# about how the document was written rather than about anything in it.


def test_a_null_notes_on_a_criteria_verdict_is_accepted(kyo, git_project, tmp_path) -> None:
    """c1/c2. The document shape a consumer had to edit by hand."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "met",
                "method": "reasoned-about",
                "notes": None,
                "evidence": None,
                "reason": None,
            }
        ],
    )
    result = kyo(
        "--full", "report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report), "--json"
    )
    assert result.code == 0, result
    assert result.json["record"]["dispatches"][0]["state"] == "reported"


def test_every_optional_field_the_schema_calls_nullable_accepts_a_null(
    kyo, git_project, tmp_path
) -> None:
    """c1. `notes` is the field that was reported; the fix is the whole class, and
    this is the whole class, read off the validator's own mirror of the document."""
    from kyosei import report_schema

    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    nulls_by_shape: dict[str, dict] = {}
    for shape, key in report_schema.NULLABLE_OPTIONAL:
        nulls_by_shape.setdefault(shape, {})[key] = None
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about",
                **nulls_by_shape.get("criterionVerdict", {}),
            }
        ],
        premise_false=[
            {"claim": "a", "correction": "b", **nulls_by_shape.get("premiseFalse", {})}
        ],
        commits=[
            {"sha": "abcdef1", **nulls_by_shape.get("commitRef", {})}
        ],
        # `amends` is nullable too, but a first report may not name one at all, so it
        # is the one top-level key that cannot ride in this document.
        **{k: v for k, v in nulls_by_shape.get("report", {}).items() if k != "amends"},
    )
    problems = report_schema.validate_report(report)
    assert problems == [], problems
    result = kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report), "--json")
    assert result.code == 0, result


def test_a_null_in_a_required_field_is_still_refused_by_name(
    kyo, git_project, tmp_path
) -> None:
    """c1's other half. `premise_false: null` is not `premise_false: []` — the empty
    array is the positive statement ("I checked and found nothing") this schema exists
    to force, and a null erases the distinction rather than expressing it."""
    from kyosei import report_schema

    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
    )
    report["premise_false"] = None
    problems = report_schema.validate_report(report)
    assert any("premise_false" in problem for problem in problems), problems
    result = kyo("report", d["dispatch_id"], "--file", _write(tmp_path, "r.json", report))
    assert result.code != 0
    assert "premise_false" in result.err

    # And the field that is optional in the schema but required by its if/then: a null
    # reason under a void verdict is a missing reason, and is reported as one.
    void = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1", "verdict": "void-withdrawn",
                "method": "reasoned-about", "reason": None,
            }
        ],
    )
    void_problems = report_schema.validate_report(void)
    assert any("reason" in problem for problem in void_problems), void_problems
