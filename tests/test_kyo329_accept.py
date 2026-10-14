"""KYO-331: ``kyo accept`` — one act to apply a report you have read.

The measurement the record was filed on: accepting a worker's report in one wave was
the same seven commands about ten times, and a step was easy to drop (KYO-263's doc-ref
obligation and KYO-314's drift correction each needed a second pass). What this file
checks is the property that makes the shortcut safe rather than the shortcut itself —
that ``kyo accept`` applies exactly what the separate commands apply, refuses where they
refuse, and refuses on its own account only where the CONTROLLER's judgment is the thing
being skipped.

The fixtures are imported from :mod:`tests.test_kyo137_docs_obligations` rather than
rebuilt, on :mod:`tests.test_kyo199_docs_verdicts`'s reasoning exactly: a second "project
with a governed document and a branch" would be a second answer to what raising looks
like, and the whole point of this command is that it agrees with the commands it runs.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import dispatch_docs, explanations, touch_drift
from kyosei.cmd_accept import ACCEPT_TOUCHES_NOTHING_TO_COMPARE, CONTRACT_ACCEPT_NEEDS_REVIEW
from kyosei.cmd_next import ungated_unobserved_remedy
from kyosei.errors import EXIT_CONTRACT

from .test_kyo137_docs_obligations import (
    _commit_anything,
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
    _report,
    _write,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The one section the fixture's branch implicates — `src/app.py` is what it changes,
#: and that is the file this section governs.
GOVERNED = ("docs/architecture.md", "How the CLI is wired")


def _met(criterion_id: str, evidence: str = "make test: exit=0") -> dict:
    return {
        "criterion_id": criterion_id,
        "verdict": "met",
        "method": "machine-checked",
        "evidence": evidence,
    }


def _docs_verdict(verdict: str = "not-affected") -> dict:
    return {
        "doc_path": GOVERNED[0],
        "section": GOVERNED[1],
        "verdict": verdict,
        "method": "read the section against the diff",
        "reason": "the section describes the wiring, which this change does not move",
    }


def _states(kyo, record: str) -> dict[str, str]:
    return {c["id"]: c["state"] for c in kyo("show", record, "--json").json["criteria"]}


def _add_second_criterion(kyo, git_project: Path, record: str) -> str:
    """KYO-343: ``--except`` needs a report with at least two verdicts to hold one
    back from. Returns the new criterion's id (``{record}.c2``)."""
    more = git_project / "a-second-criterion.txt"
    more.write_text("a second thing is also true\n", encoding="utf-8")
    added = kyo("edit", record, "--add-criteria-file", str(more))
    assert added.code == 0, added
    return f"{record}.c2"


# ------------------------------------------------------------------------ KYO-331.c1


def test_accept_applies_verdicts_docs_verdicts_and_the_drift_correction_in_one_act(
    kyo, git_project: Path
) -> None:
    """c1. One invocation, three writes, and every id it minted printed.

    The assertion that matters is not "exit 0": it is that each of the three things a
    controller did by hand actually landed — the criterion carries a verification with
    the worker's method, the obligation carries a verdict, and the record's touches are
    the corrected list — and that each step's payload is the composed command's own, so
    nothing here is a second implementation of what it applies.
    """
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1")],
        docs_verdicts=[_docs_verdict()],
        # `release.sh` is in the fixture's tree and NOT in the record's declared
        # touches, so the correction has something real to store: this is the drift a
        # controller applies by hand after every merge.
        touched_files=["src/app.py", "release.sh"],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    payload = accepted.json
    assert payload["report_id"] == reported.json["report_id"]
    assert [step["data"]["command"] for step in payload["steps"]] == [
        "verify",
        "docs settle",
        "edit",
    ], "each step must be the composed command's own output, not a second implementation"

    shown = kyo("show", record, "--json").json
    criterion = shown["criteria"][0]
    assert criterion["state"] == "met"
    assert criterion["verifications"][-1]["method"] == "machine-checked"
    settled = {(o["doc_path"], o["section"]): o["verdict"] for o in shown["docs_obligations"]}
    assert settled[GOVERNED] == "not-affected"
    # The second section governs `release.sh`, which `touched_files[]` names, so the
    # ingest raised it too and the report answered it nowhere. It stays open, which is
    # `kyo docs settle --from-report`'s own behaviour: the worker proposes, and an
    # obligation nobody proposed a verdict for is not one this command may invent.
    assert settled[("docs/architecture.md", "How releases are cut")] is None
    assert sorted(t["path"] for t in shown["touches"]) == ["release.sh", "src/app.py"], (
        "the drift correction is the third write, and it is the one that needed a second "
        "pass on KYO-314"
    )

    # The ids it minted, in the text a controller reads.
    text = kyo("accept", dispatch_id).out  # idempotent second run, for the text
    assert dispatch_id in text and reported.json["report_id"] in text
    assert "kyo verify --from-report" in accepted.json["steps"][0]["command"]


# ------------------------------------------------------------------------ KYO-331.c2


def test_accept_refuses_a_report_with_premise_false_until_reviewed(
    kyo, git_project: Path
) -> None:
    """c2. The entries are the reason this command has a flag at all, so the refusal
    prints them whole — and the remedy it names clears it (the standing rule)."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1")],
        premise_false=[
            {
                "claim": "the brief said src/app.py holds the wiring",
                "correction": "it holds the entry point; the wiring is in src/wire.py",
                "impact": "the touches table named the wrong file",
            }
        ],
        disagreements=[
            {
                "topic": "where the flag belongs",
                "brief_said": "put it on `kyo edit`",
                "worker_did": "put it on `kyo accept`",
                "rationale": "the edit path already has fourteen flags",
            }
        ],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported

    refused = kyo("accept", dispatch_id)
    assert refused.code == EXIT_CONTRACT, refused
    assert CONTRACT_ACCEPT_NEEDS_REVIEW in refused.err
    for quoted in (
        "the brief said src/app.py holds the wiring",
        "it holds the entry point; the wiring is in src/wire.py",
        "the touches table named the wrong file",
        "where the flag belongs",
        "the edit path already has fourteen flags",
    ):
        assert quoted in refused.err, f"the refusal must print {quoted!r}"
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}, (
        "a refusal must apply nothing at all"
    )

    cleared = kyo("accept", dispatch_id, "--reviewed", "--json")
    assert cleared.code == 0, cleared
    assert _states(kyo, record) == {f"{record}.c1": "met"}
    assert cleared.json["reviewed"] is True
    assert len(cleared.json["premise_false"]) == 1


def test_accept_needs_no_reviewed_flag_when_the_report_carries_neither(
    kyo, git_project: Path
) -> None:
    """c2's other direction, and the one that keeps the flag from becoming a habit: a
    report with nothing to read is accepted without it."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=["src/app.py"],
    )  # fmt: skip
    assert reported.code == 0, reported
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    assert "carries no premise_false" in accepted.out


# ------------------------------------------------------------------------ KYO-331.c3


def test_accept_leaves_a_void_verdict_exactly_where_verify_from_report_leaves_it(
    kyo, git_project: Path
) -> None:
    """c3. ``void-premise-gone`` is a premise judgment, not a verified fact (KYO-4.c3),
    and ``kyo verify --from-report`` names the command that applies it rather than
    applying it. Composing that command rather than re-deciding is what makes this true
    here: the entry is reported and left, in the same words, and the criterion does not
    move."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "void-premise-gone",
                "method": "reasoned-about",
                "reason": "the surface it was about was deleted in the same wave",
            }
        ],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    verify_step = accepted.json["steps"][0]["data"]
    assert verify_step["applied"] == []
    assert [row["criterion_id"] for row in verify_step["not_applied"]] == [f"{record}.c1"]
    assert "kyo edit" in verify_step["not_applied"][0]["why"], (
        "the entry is reported with the command that would apply it — verify's own "
        "sentence, not a second one written here"
    )
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}


def test_accept_leaves_a_restated_criterion_exactly_where_verify_leaves_it(
    kyo, git_project: Path
) -> None:
    """c3's second half (KYO-58): restating mints a new id and voids the old one, and a
    verdict recorded against superseded text is the re-attachment restatement exists to
    prevent. The report names the old id; accept names the replacement and applies
    nothing."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=["src/app.py"],
    )  # fmt: skip
    assert reported.code == 0, reported
    restated = kyo(
        "restate", f"{record}.c1", "--text-file", "-", stdin="the CLI is wired, precisely\n"
    )
    assert restated.code == 0, restated

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    not_applied = accepted.json["steps"][0]["data"]["not_applied"]
    assert [row["criterion_id"] for row in not_applied] == [f"{record}.c1"]
    assert f"{record}.c2" in not_applied[0]["why"], "the replacement is named"
    assert _states(kyo, record)[f"{record}.c2"] == "unmet-unbuilt"


# ------------------------------------------------------------------------ KYO-331.c4


def test_accept_prints_the_gate_line_for_an_unobserved_criterion_and_writes_no_gate(
    kyo, git_project: Path
) -> None:
    """c4. A gate carries an instruction to a person, so no tool mints one out of a
    criterion's reason (KYO-267). The line printed is the one every other surface
    prints, asserted against the function that produces it rather than against a copy
    of its wording."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "unmet-unobserved",
                "method": "reasoned-about",
                "reason": "the banner is only visible to a human with the app in front of them",
            }
        ],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    waiting = accepted.json["ungated_unobserved"]
    assert [row["id"] for row in waiting] == [record]
    expected = ungated_unobserved_remedy(
        record,
        [
            {
                "id": f"{record}.c1",
                "reason": "the banner is only visible to a human with the app in front of them",
            }
        ],
    )
    assert waiting[0]["remedy"] == expected
    assert expected in kyo("accept", dispatch_id).out, (
        "the exact line has to reach the controller's terminal, not only --json"
    )
    assert kyo("show", record, "--json").json["gates"] == [], (
        "this command writes no gate — the whole point of printing the line"
    )


# ------------------------------------------------------------------------ KYO-331.c5


def _closable(kyo, git_project: Path):
    """A dispatch whose report settles every criterion and every obligation."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1")],
        docs_verdicts=[_docs_verdict()],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported
    return record, dispatch_id


def test_close_walks_the_ladder_to_the_completion_status(kyo, git_project: Path) -> None:
    """c5. The rungs are the record's own history of having been built and then
    verified, so the walk records each one — this is the `kyo move verified` / `kyo move
    done` pair the controller typed by hand, plus the rungs before it."""
    record, dispatch_id = _closable(kyo, git_project)
    was = kyo("show", record, "--json").json["status"]

    accepted = kyo("accept", dispatch_id, "--close", "--json")
    assert accepted.code == 0, accepted
    assert [row["record_id"] for row in accepted.json["closed"]] == [record]
    assert accepted.json["left_open"] == []
    assert kyo("show", record, "--json").json["status"] == "done"

    walked = accepted.json["closed"][0]["walked"]
    assert walked[-1] == "done"
    assert "verified" in walked, (
        "the ladder's own rungs are walked, not skipped — a single move from open to "
        "done is permitted by the contracts and is not what a controller does"
    )
    assert accepted.json["closed"][0]["was"] == was


def test_close_inherits_kyo346_flag_settlement_from_cmd_move(kyo, git_project: Path, db) -> None:
    """KYO-346.c1: ``--close`` walks a record to its completion status through
    `cmd_move` (this file's own module docstring, and c5's fixture above), so the
    settlement `_transition` performs on every completion move applies here without
    any change of its own — checked directly rather than assumed, per the KYO-346
    brief's own instruction."""
    record, dispatch_id = _closable(kyo, git_project)
    with db() as conn:
        conn.execute(
            "INSERT INTO flags (id, record_id, seq, flag_type, detail,"
            " other_record_id, other_criterion_id, sweep_run_id, created_at,"
            " resolved_at, resolved_by, resolved_why) VALUES"
            " (?, ?, 1, 'touch-changed', 'seeded for KYO-346', NULL, NULL, NULL,"
            " '2026-09-01T00:00:00.000Z', NULL, NULL, NULL)",
            (f"{record}.f1", record),
        )

    accepted = kyo("accept", dispatch_id, "--close", "--json")
    assert accepted.code == 0, accepted
    assert kyo("show", record, "--json").json["status"] == "done"

    flag = kyo("show", record, "--json").json["flags"][0]
    assert flag["id"] == f"{record}.f1"
    assert flag["resolved_at"] is not None
    assert record in flag["resolved_why"]
    assert "done" in flag["resolved_why"]
    assert "KYO-346" in flag["resolved_why"]


def test_close_leaves_a_record_it_cannot_finish_and_says_why(kyo, git_project: Path) -> None:
    """c5's other half, and the one law 6 is about: a close that silently skipped a
    record would read exactly like one that closed it."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    # A second criterion the report says nothing about: settled criteria are what the
    # completion contract asks for, and this one stays unmet.
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
    assert accepted.json["closed"] == []
    left = accepted.json["left_open"]
    assert [row["record_id"] for row in left] == [record]
    why = "\n".join(left[0]["why"])
    assert "complete-requires-settled-criteria" in why, (
        "the reason is the contract's own refusal, so a controller reads the same "
        "sentence `kyo move` would have given them"
    )
    assert f"{record}.c2" in why
    assert kyo("show", record, "--json").json["status"] != "done"


def test_without_close_nothing_moves(kyo, git_project: Path) -> None:
    """``--close`` is opt-in: deciding a record is finished is a judgment made with the
    whole board in view, and applying a report is not that judgment."""
    record, dispatch_id = _closable(kyo, git_project)
    before = kyo("show", record, "--json").json["status"]
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    assert kyo("show", record, "--json").json["status"] == before
    assert "--close was not passed" in accepted.out


# --------------------------------------------- the drift refusal a merge makes ordinary


def test_a_report_with_nothing_to_compare_is_skipped_not_refused(
    kyo, git_project: Path
) -> None:
    """KYO-353.c1. A consumer project's orchestrator met this: a report with
    zero commits gets REFUSED printed at the touches step, and "nothing to diff" is
    not a refusal. A report carrying no `commits[]` and no `touched_files[]` (the
    fixture here, and the merged branch KYO-314 was filed on) is a fact knowable from
    the report alone — there is nothing to compare no matter what the branch or git
    history says — so the step is not run at all, and `--json` marks it `skipped`
    rather than `refused`."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=[],
    )  # fmt: skip
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    drift_step = accepted.json["steps"][2]
    assert drift_step["skipped"] == ACCEPT_TOUCHES_NOTHING_TO_COMPARE, drift_step
    assert not drift_step["refused"], drift_step
    assert "nothing to compare" in " ".join(drift_step["lines"]), drift_step

    printed = kyo("accept", dispatch_id)
    assert "REFUSED" not in printed.out, printed
    assert "nothing to compare" in printed.out, printed
    assert _states(kyo, record) == {f"{record}.c1": "met"}, (
        "the steps before it still applied"
    )


def test_a_drift_correction_that_cannot_be_checked_is_a_line_not_a_failure(
    kyo, git_project: Path
) -> None:
    """KYO-353.c2: the touches step still runs, and still refuses in its own words,
    for any report that is NOT the "nothing to compare" shape above. Here the report
    carries a `commits[]` entry (so the step is run), but the branch is gone and that
    commit's sha is not reachable from main — KYO-344's fallback cannot answer either
    — which is `kyo edit --touches-from-drift`'s own refusal
    (`touch-drift-apply-needs-a-finding`), caught and printed as REFUSED exactly as
    before. That is a fact about the dispatch, not a failure of the acceptance."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    _commit_anything(git_project, "the store, before the merge")
    _git(git_project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    _git(git_project, "branch", "-D", "kyo-137")

    unreachable_sha = "1234567890abcdef1234567890abcdef12345678"
    reported = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        sha,
        criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=[],
        commits=[{"sha": unreachable_sha, "subject": "not actually on main"}],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, f"the refused step must not fail the whole act: {accepted}"
    drift_step = accepted.json["steps"][2]
    assert drift_step["refused"], drift_step
    assert "touch-drift-apply-needs-a-finding" in drift_step["refused"]
    assert not drift_step["skipped"], drift_step
    assert "REFUSED" in kyo("accept", dispatch_id).out
    assert _states(kyo, record) == {f"{record}.c1": "met"}, (
        "the steps before it still applied, which is the whole reason to tolerate this one"
    )


def test_accept_is_safe_to_run_twice(kyo, git_project: Path) -> None:
    """The property that makes committing step by step acceptable (see the module
    docstring): nothing here nests a transaction, so a second run has to be harmless."""
    record, dispatch_id = _closable(kyo, git_project)
    first = kyo("accept", dispatch_id, "--close", "--json")
    assert first.code == 0, first
    second = kyo("accept", dispatch_id, "--close", "--json")
    assert second.code == 0, second
    assert second.json["steps"][1]["data"]["applied"] == [], (
        "an obligation that already carries a verdict is named, not overwritten (law 9)"
    )
    assert second.json["closed"] == []
    assert [row["why"] for row in second.json["left_open"]] == [["already done"]]
    assert kyo("show", record, "--json").json["status"] == "done"


def test_accept_refuses_a_dispatch_with_no_ingested_report(kyo, git_project: Path) -> None:
    """The refusal is `kyo verify --from-report`'s own, raised from the same function —
    one wording for one state, and it already names `kyo report` as the remedy."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    refused = kyo("accept", dispatched.json["dispatch_id"])
    assert refused.code == EXIT_CONTRACT, refused
    assert "verify-from-report-needs-a-report" in refused.err
    assert f"`kyo report {dispatched.json['dispatch_id']} --file" in refused.err


# ---------------------------------------------------------------- KYO-343: --except


def test_accept_except_applies_the_complement_and_holds_the_named_one_back(
    kyo, git_project: Path
) -> None:
    """c1. ``--except`` hands ``kyo verify --from-report`` the complement as its own
    ``--only``: the named criterion's verdict stays unapplied, every other reported
    verdict lands, and the output lists the excepted one with the state the report
    proposed."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    c2 = _add_second_criterion(kyo, git_project, record)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1"), _met(c2)],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--except", c2, "--json")
    assert accepted.code == 0, accepted
    assert "--only" in accepted.json["steps"][0]["command"], (
        "the complement, not a second implementation of the selection"
    )
    assert _states(kyo, record) == {f"{record}.c1": "met", c2: "unmet-unbuilt"}, (
        "the excepted verdict must stay unapplied, and every other one must land"
    )
    excepted = accepted.json["excepted"]
    assert [row["criterion_id"] for row in excepted] == [c2]
    assert excepted[0]["verdict"] == "met", "the state the report proposed, not what landed"
    assert excepted[0]["record_id"] == record

    text = kyo("accept", dispatch_id, "--except", c2).out
    assert c2 in text and "held back" in text


def test_accept_except_refuses_an_id_the_report_carries_no_verdict_for(
    kyo, git_project: Path
) -> None:
    """c2. A mistyped or stale ``--except`` id is refused before anything is written,
    naming the ids the report does carry — the same shape ``kyo verify --only``
    refuses an absent name with."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=["src/app.py"],
    )  # fmt: skip
    assert reported.code == 0, reported

    refused = kyo("accept", dispatch_id, "--except", "KYO-999.c1")
    assert refused.code == EXIT_CONTRACT, refused
    assert "accept-except-names-an-absent-criterion" in refused.err
    assert "KYO-999.c1" in refused.err
    assert f"{record}.c1" in refused.err, "the ids the report DOES carry must be named"
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}, (
        "a refusal must apply nothing at all"
    )

    # The remedy the message points at -- drop the offender -- clears it.
    cleared = kyo("accept", dispatch_id, "--except", f"{record}.c1", "--json")
    assert cleared.code == 0, cleared
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}


def test_accept_except_close_leaves_a_record_holding_it_and_names_it_as_the_reason(
    kyo, git_project: Path
) -> None:
    """c3. The excepted criterion was never selected, so it stays unsettled, and the
    ladder's own ``complete-requires-settled-criteria`` refusal — the same one
    :func:`_close_blocker` reads for every other unsettled criterion — leaves the
    record where it is and names the excepted criterion as why."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    c2 = _add_second_criterion(kyo, git_project, record)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1"), _met(c2)],
        docs_verdicts=[_docs_verdict()],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--except", c2, "--close", "--json")
    assert accepted.code == 0, accepted
    assert accepted.json["closed"] == []
    left = accepted.json["left_open"]
    assert [row["record_id"] for row in left] == [record]
    why = "\n".join(left[0]["why"])
    assert "complete-requires-settled-criteria" in why
    assert c2 in why, "the excepted criterion must be named as the reason"
    assert kyo("show", record, "--json").json["status"] != "done"


def test_accept_except_prints_the_gate_line_and_writes_no_gate(
    kyo, git_project: Path
) -> None:
    """c4. For each excepted criterion, a `kyo gate` line for its record is printed,
    and no gate is written — the same "print, never run" reasoning
    ``ungated_unobserved_remedy`` follows, applied to a held-back verdict rather than
    an unobserved one."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    c2 = _add_second_criterion(kyo, git_project, record)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1"), _met(c2)],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--except", c2, "--json")
    assert accepted.code == 0, accepted
    remedy = accepted.json["excepted"][0]["gate_remedy"]
    assert remedy.startswith(f"kyo gate {record} awaiting:event --do "), remedy
    assert kyo("show", record, "--json").json["gates"] == [], (
        "this command writes no gate — the whole point of printing the line"
    )

    text = kyo("accept", dispatch_id, "--except", c2).out
    assert remedy in text, "the exact line has to reach the controller's terminal"
    assert kyo("show", record, "--json").json["gates"] == []


def test_accept_except_naming_every_reported_verdict_applies_none(
    kyo, git_project: Path
) -> None:
    """The edge `--only` cannot express directly (it needs at least one id): excepting
    every criterion the report carries a verdict for leaves nothing to select, so the
    verify step is skipped rather than run with an empty ``--only`` or falling back to
    applying everything."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=["src/app.py"],
    )  # fmt: skip
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--except", f"{record}.c1", "--json")
    assert accepted.code == 0, accepted
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}
    assert [row["criterion_id"] for row in accepted.json["excepted"]] == [f"{record}.c1"]
    assert all(step["argv"][0] != "verify" for step in accepted.json["steps"]), (
        "nothing was selectable, so no verify step ran at all"
    )


# ----------------------------------------------------------- KYO-344: the branch is gone


def test_touches_from_drift_falls_back_to_the_reports_commits_once_the_branch_is_gone(
    kyo, git_project: Path
) -> None:
    """c1. A consumer report's case: the branch is deleted before accepting, the report
    named no ``touched_files[]``, and its ``commits[]`` were already proved reachable
    (KYO-32) and are on main after the merge. ``kyo edit --touches-from-drift`` — and
    so `kyo accept`'s third step — computes drift from what those commits changed
    instead of refusing, and says in its output that it did so because the branch is
    gone."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    _commit_anything(git_project, "the store, before the merge")
    _git(git_project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    _git(git_project, "branch", "-D", "kyo-137")

    reported = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        sha,
        criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=[],
        commits=[{"sha": sha, "subject": "work on kyo-137"}],
    )
    assert reported.code == 0, reported

    read = kyo("touch-drift", dispatch_id, "--json")
    assert read.code == 0, read
    drift = read.json["touch_drift"]
    assert drift["source"] == touch_drift.DRIFT_SOURCE_COMMITS
    assert drift["changed_files"] == ["src/app.py"], (
        "computed from what the commit itself changed, not the whole merge"
    )
    assert "no longer exists" in kyo("touch-drift", dispatch_id).out

    corrected = kyo("edit", record, "--touches-from-drift", dispatch_id, "--json")
    assert corrected.code == 0, corrected
    assert sorted(t["path"] for t in kyo("show", record, "--json").json["touches"]) == [
        "src/app.py"
    ]


def test_accept_applies_the_touches_correction_after_the_branch_is_gone(
    kyo, git_project: Path
) -> None:
    """c1, at the level `kyo accept` runs it: the third step no longer refuses just
    because the branch is gone, when the report's own commits[] can answer instead."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    _commit_anything(git_project, "the store, before the merge")
    _git(git_project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    _git(git_project, "branch", "-D", "kyo-137")

    reported = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        sha,
        criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=[],
        commits=[{"sha": sha, "subject": "work on kyo-137"}],
    )
    assert reported.code == 0, reported

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    drift_step = accepted.json["steps"][2]
    assert not drift_step["refused"], drift_step
    assert sorted(t["path"] for t in kyo("show", record, "--json").json["touches"]) == [
        "src/app.py"
    ]


def test_touches_from_drift_names_all_three_remedies_when_none_can_answer(
    kyo, git_project: Path
) -> None:
    """c2. When neither the branch nor the commits can answer, the refusal names what
    would: the branch, reachable commits[], or the report's touched_files[]. Restoring
    the branch is the remedy the message names, and it clears it (the standing rule)."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-137")
    _commit_anything(git_project, "the store, before the merge")
    _git(git_project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    _git(git_project, "branch", "-D", "kyo-137")

    reported = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        sha,
        criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=[],
        commits=[],
    )
    assert reported.code == 0, reported

    blocked = kyo("touch-drift", dispatch_id, "--json")
    assert blocked.code == 0, blocked
    drift = blocked.json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    detail = drift["detail"]
    assert "kyo-137" in detail, detail
    assert "commits[]" in detail, detail
    assert "touched_files[]" in detail, detail
    assert "no longer resolves" in detail, detail

    # The remedy the message names first -- restore the branch -- clears THIS refusal:
    # the branch resolves again, so the fallback this test is about no longer fires at
    # all. What is left is the ordinary already-merged case (KYO-314), a different,
    # well-understood refusal rather than the "nothing can answer" one above.
    _git(git_project, "branch", "kyo-137", sha)
    cleared = kyo("touch-drift", dispatch_id, "--json")
    assert cleared.code == 0, cleared
    cleared_drift = cleared.json["touch_drift"]
    assert cleared_drift["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    assert "no longer resolves" not in cleared_drift["detail"]
    assert "already merged" in cleared_drift["detail"], cleared_drift["detail"]


# ------------------------------------------------------------------------ KYO-331.c6


def test_the_readme_report_ingestion_passage_describes_the_command() -> None:
    """c6 is checked by reading; this is the half a reading cannot do — that the passage
    still names the command and its two flags after somebody edits around it."""
    from tests.reference_pages import OPERATING, rel

    text = OPERATING.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    for token in ("kyo accept", "--reviewed", "--close"):
        assert token in text, f"{rel(OPERATING)} never names {token}"


def test_the_fixtures_this_file_leans_on_are_the_shared_ones() -> None:
    """The fixtures come from the docs-obligations file rather than from a copy here;
    this fails if that import ever becomes a local rebuild."""
    assert _governed_doc_and_a_branch_that_only_touches_code.__module__.endswith(
        "test_kyo137_docs_obligations"
    )
    assert _merged_ingest.__module__.endswith("test_kyo137_docs_obligations")


# ------------------------------------------------------------------------ KYO-393

#: A document declared ``numbered:``, with the section the fixture's branch raises
#: (it governs ``src/app.py``) numbered 205 — the consumer's own number.
NUMBERED_NOTES = """# Notes

## 204. APP-57: how releases are cut (2026-09-15)

governs: release.sh

Tag, build, publish.

## 205. APP-58: how the CLI is wired (2026-09-16)

governs: src/app.py

The CLI hands each subcommand a context and an argument namespace.
"""
NOTES_205 = ("NOTES.md", "205. APP-58: how the CLI is wired (2026-09-16)")

NUMBERED_DECLARATION = """documents:
  notes:
    summary: the project's numbered, append-only notes
    globs: [NOTES.md]
    numbered:
      fragments: notes-fragments
      heading: '## {number}.'
"""


def _numbered_verdict(section: str, verdict: str = "not-affected") -> dict:
    return {**_docs_verdict(verdict), "doc_path": "NOTES.md", "section": section}


def _numbered_ingest(kyo, git_project: Path, *verdicts: dict, criteria: bool = True):
    """The shared fixture, with NOTES.md declared numbered before anything branches."""
    _write(git_project, "NOTES.md", NUMBERED_NOTES)
    _write(git_project, "kyosei/documents.yml", NUMBERED_DECLARATION)
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_met(f"{record}.c1")] if criteria else [],
        docs_verdicts=list(verdicts),
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported
    raised = {(r["doc_path"], r["section"]) for r in reported.json["docs_obligations"]["raised"]}
    assert raised == {NOTES_205}, raised
    return record, dispatch_id, reported


def _notes_verdict(kyo, record: str) -> str | None:
    rows = kyo("show", record, "--json").json["docs_obligations"]
    return {(o["doc_path"], o["section"]): o["verdict"] for o in rows}[NOTES_205]


def test_a_numbered_documents_verdict_is_applied_by_section_number_and_named(
    kyo, git_project: Path
) -> None:
    """KYO-393.c1: the report's heading differs from the document's after the number,
    and the verdict is applied anyway — and both `kyo report` and `kyo accept` say it
    was matched by number, naming both headings."""
    record, dispatch_id, reported = _numbered_ingest(
        kyo, git_project, _numbered_verdict("205. the CLI wiring")
    )
    ingest = reported.json["docs_obligations"]
    assert [p["by_number"] for p in ingest["verdicts_matched_by_number"]] == [205]
    ingest_text = "\n".join(dispatch_docs._docs_verdict_lines(ingest))
    assert "matched by number: NOTES.md [205. the CLI wiring]" in ingest_text

    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    assert _notes_verdict(kyo, record) == "not-affected"
    assert "matched by section number, heading differs (1):" in accepted.out
    assert f"[205. the CLI wiring] matched [{NOTES_205[1]}]" in accepted.out
    assert "matched no obligation" not in accepted.out


def test_a_number_already_claimed_is_reported_as_a_duplicate_and_not_applied(
    kyo, git_project: Path
) -> None:
    """KYO-393.c2, end to end: the first entry settles section 205; the second, reaching
    it by number with another verdict, is named and never overwrites it."""
    record, dispatch_id, _ = _numbered_ingest(
        kyo,
        git_project,
        _numbered_verdict(NOTES_205[1]),
        _numbered_verdict("205. the CLI, again", "updated"),
    )
    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    assert _notes_verdict(kyo, record) == "not-affected"
    [row] = accepted.json["unmatched_docs_verdicts"]
    assert row["section"] == "205. the CLI, again"
    assert row["why"] == "duplicate-entry"


def test_the_last_line_of_accept_counts_the_unmatched_verdicts_and_names_the_command(
    kyo, git_project: Path
) -> None:
    """KYO-393.c3: the consumer's sixty lines named the unmatched verdicts in the
    middle; the last line is where they are counted now. Remove it and this fails."""
    _record, dispatch_id, _ = _numbered_ingest(
        kyo,
        git_project,
        _numbered_verdict(NOTES_205[1]),
        _numbered_verdict("APP-58 without its number"),
        _numbered_verdict("999. a section this branch never raised"),
    )
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    # KYO-489: the last line of all is the `explained in` pointer, which ends every
    # output that printed a short form; this is the last line of accept's own.
    lines = accepted.out.strip().splitlines()
    assert lines[-1] == explanations.footer_line("accept"), lines
    last = lines[-2]
    assert last.startswith("2 docs verdict(s) from report "), last
    assert "matched no obligation and were NOT applied" in last
    assert f"`kyo docs settle --from-report {dispatch_id}` lists them" in last

    # The remedy it names does list them, and applies nothing a second time.
    listed = kyo("docs", "settle", "--from-report", dispatch_id, "--json")
    assert listed.code == 0, listed
    assert listed.json["settled"] == 0
    assert [row["section"] for row in listed.json["unmatched"]] == [
        "APP-58 without its number",
        "999. a section this branch never raised",
    ]


def test_accept_with_every_verdict_matched_ends_without_the_unmatched_line(
    kyo, git_project: Path
) -> None:
    _record, dispatch_id, _ = _numbered_ingest(
        kyo, git_project, _numbered_verdict(NOTES_205[1])
    )
    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    assert "matched no obligation" not in accepted.out
    lines = accepted.out.strip().splitlines()
    assert lines[-1] == explanations.footer_line("accept"), lines
    assert lines[-2].startswith("--close was not passed")


def test_close_refused_over_open_obligations_names_the_unmatched_verdicts(
    kyo, git_project: Path
) -> None:
    """KYO-393.c4: `--close` refuses over the open obligation, and the refusal names
    the verdict that was meant to settle it rather than leaving the controller to find
    it sixty lines up."""
    record, dispatch_id, _ = _numbered_ingest(
        kyo, git_project, _numbered_verdict("APP-58: how the CLI is wired")
    )
    accepted = kyo("accept", dispatch_id, "--close", "--json")
    assert accepted.code == 0, accepted
    [left] = accepted.json["left_open"]
    assert left["record_id"] == record
    why = "\n".join(left["why"])
    assert "NOTES.md" in why
    assert "matched no obligation on these documents" in why
    assert "NOTES.md [APP-58: how the CLI is wired]" in why
    assert [row["section"] for row in left["unmatched_docs_verdicts"]] == [
        "APP-58: how the CLI is wired"
    ]
    text = kyo("accept", dispatch_id, "--close").out
    assert "NOTES.md [APP-58: how the CLI is wired]" in text.split("left (1):")[1]


# ------------------------------------------------------------------------ KYO-511


def _unobserved(criterion_id: str) -> dict:
    return {
        "criterion_id": criterion_id,
        "verdict": "unmet-unobserved",
        "method": "machine-checked",
        "reason": "the check failed in the worktree for a reason the merge cleared",
    }


def _consumer_sequence(kyo, git_project: Path) -> tuple[str, str]:
    """A consumer report's sequence up to the accept: the report says
    `unmet-unobserved`, and after it was ingested the orchestrator verified the same
    criterion `met`, machine-checked, with evidence."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        criteria_verdicts=[_unobserved(f"{record}.c1")],
        docs_verdicts=[_docs_verdict()],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported
    evidence = git_project.parent / "kyo511-evidence.txt"
    evidence.write_text("make test: exit=0 on main after the merge\n", encoding="utf-8")
    verified = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked",
        "--evidence-file", str(evidence),
    )  # fmt: skip
    assert verified.code == 0, verified
    return record, dispatch_id


def test_accept_close_keeps_a_verdict_newer_than_the_report(
    kyo, git_project: Path, db
) -> None:
    """KYO-511.c1, the consumer sequence: report unmet, a later verify met, then
    `kyo accept --reviewed --close`. The report's verdict is the older word, so it is
    skipped and named, the criterion stays met and the record closes. Remove the
    newer-verdict skip in `_verify_from_report` and the report's `unmet-unobserved` lands
    on top, `--close` leaves the record open, and every assertion below fails."""
    record, dispatch_id = _consumer_sequence(kyo, git_project)
    verifications = _count(db, "verifications")

    accepted = kyo("accept", dispatch_id, "--reviewed", "--close", "--json")
    assert accepted.code == 0, accepted
    verify = accepted.json["steps"][0]["data"]
    assert verify["applied"] == []
    [kept] = verify["newer_verdict_kept"]
    assert kept["criterion_id"] == f"{record}.c1"
    assert kept["verdict"] == "unmet-unobserved", "the report's verdict, not applied"
    assert kept["latest_verdict"] == "met"
    assert _count(db, "verifications") == verifications, "nothing written over it"
    assert _states(kyo, record) == {f"{record}.c1": "met"}
    assert accepted.json["complete"] is True, accepted.json
    assert [row["record_id"] for row in accepted.json["closed"]] == [record]
    assert kyo("show", record, "--json").json["status"] == "done"


def test_verify_from_report_names_each_criterion_it_skipped_for_a_newer_verdict(
    kyo, git_project: Path
) -> None:
    """KYO-511.c1, the text: which criteria were skipped, and why."""
    record, dispatch_id = _consumer_sequence(kyo, git_project)
    text = kyo("verify", "--from-report", dispatch_id)
    assert text.code == 0, text
    assert "applied 0 of 1 verdict(s)" in text.out
    assert "newer verdict kept (1)" in text.out
    [line] = [line for line in text.out.splitlines() if line.startswith(f"  - {record}.c1:")]
    assert "report says unmet-unobserved" in line
    assert "latest is met" in line
    assert "--again" in text.out, "the line names the way to apply it anyway"
    assert _states(kyo, record) == {f"{record}.c1": "met"}

    # The same lines reach `kyo accept`'s text through its verify step.
    accepted = kyo("accept", dispatch_id)
    assert "newer verdict kept (1)" in accepted.out


def test_again_applies_the_report_over_a_newer_verdict(kyo, git_project: Path, db) -> None:
    """KYO-511.c2. `--again` still means "apply the report's verdicts even though the
    store would skip them": the ones this report already applied (KYO-415) and, now,
    the ones a newer verdict has superseded. It is the remedy the skip line names, and
    it clears the skip."""
    record, dispatch_id = _consumer_sequence(kyo, git_project)
    before = _count(db, "verifications")
    forced = kyo("verify", "--from-report", dispatch_id, "--again", "--json")
    assert forced.code == 0, forced
    assert forced.json["again"] is True
    assert [row["criterion_id"] for row in forced.json["applied"]] == [f"{record}.c1"]
    assert forced.json["newer_verdict_kept"] == []
    assert _count(db, "verifications") == before + 1
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unobserved"}

    # And the KYO-415 meaning is unchanged: a rerun now finds the report's own verdict
    # latest, skips it as already applied, and `--again` applies it once more.
    rerun = kyo("verify", "--from-report", dispatch_id, "--json")
    assert rerun.json["applied"] == [] and rerun.json["newer_verdict_kept"] == []
    assert [row["criterion_id"] for row in rerun.json["already_applied"]] == [f"{record}.c1"]
    assert kyo("verify", "--from-report", dispatch_id, "--again").code == 0
    assert _count(db, "verifications") == before + 2


def test_applying_the_same_report_twice_writes_one_verification(
    kyo, git_project: Path, db
) -> None:
    """KYO-511.c3. With no newer verdict between the two runs the second is still the
    KYO-415 skip (already applied), not the newer-verdict one, and writes nothing."""
    record, dispatch_id = _closable(kyo, git_project)
    first = kyo("accept", dispatch_id, "--json")
    assert first.code == 0, first
    count = _count(db, "verifications")
    assert count == 1
    second = kyo("verify", "--from-report", dispatch_id, "--json")
    assert second.json["applied"] == []
    assert second.json["newer_verdict_kept"] == []
    assert [row["criterion_id"] for row in second.json["already_applied"]] == [f"{record}.c1"]
    assert _count(db, "verifications") == count


def test_a_verdict_recorded_before_the_report_was_ingested_is_applied_over(
    kyo, git_project: Path
) -> None:
    """The comparison is the report's ingest time: a verdict older than that is the
    older word, and the report's verdict applies over it as it always did."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about"
    ).code == 0
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, criteria_verdicts=[_met(f"{record}.c1")],
        touched_files=["src/app.py"],
    )  # fmt: skip
    assert reported.code == 0, reported
    applied = kyo("verify", "--from-report", dispatch_id, "--json")
    assert [row["criterion_id"] for row in applied.json["applied"]] == [f"{record}.c1"]
    assert applied.json["newer_verdict_kept"] == []
    assert _states(kyo, record) == {f"{record}.c1": "met"}


def _count(db, table: str) -> int:
    with db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
