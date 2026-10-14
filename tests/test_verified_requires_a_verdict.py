"""KYO-546: entering the verified role needs a recorded verdict on every live criterion.

A consumer report: the consumer's record APP-239 moved
open -> verified with all four criteria still `unmet-unbuilt` and no verdict on any of
them -- the `kyo accept` that would have recorded the report's verdicts had been
refused, and the move went through anyway. The verified role means "a verdict is
recorded; only the decision to complete remains"; nothing held a move to it to that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei.contracts import (
    CONTRACT_VERIFIED_REQUIRES_A_VERDICT,
    CriterionView,
    check_transition,
    default_ladder,
)
from kyosei.errors import ContractViolation

from .conftest import new_record, valid_report


def _verdict(criterion_id: str, verdict: str) -> dict:
    return {
        "criterion_id": criterion_id,
        "verdict": verdict,
        "method": "reasoned-about",
        "evidence": f"{criterion_id} {verdict} by test",
    }


def _dispatch_and_report(kyo, tmp_path: Path, record: str, verdicts: list[dict]) -> str:
    """Dispatch ``record`` and ingest a report carrying ``verdicts``; apply nothing."""
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    path = tmp_path / f"{dispatch_id}.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=dispatched.json["worked_from_sha"],
                criteria_verdicts=verdicts,
            )
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatch_id, "--file", str(path))
    assert ingested.code == 0, ingested
    return dispatch_id


def _refused(kyo, record: str) -> str:
    moved = kyo("move", record, "verified")
    assert moved.code == 2, moved
    assert CONTRACT_VERIFIED_REQUIRES_A_VERDICT in moved.err
    return moved.err


def test_verified_is_refused_without_a_verdict_and_verify_clears_it(project: Path, kyo):
    """c1: the APP-239 shape, open -> verified with nothing recorded, is refused, names
    the criterion and `kyo verify`; recording the verdict lets the same move through."""
    record = new_record(kyo, criteria=["first claim"])
    c1 = f"{record}.c1"
    err = _refused(kyo, record)
    assert f"{c1} is unmet-unbuilt, never verified: first claim" in err
    assert f"`kyo verify {c1} --verdict <verdict> --method <how>`" in err
    assert "kyo accept" not in err, "no report is waiting, so none is named"

    verified = kyo("verify", c1, "--verdict", "met", "--method", "reasoned-about")
    assert verified.code == 0, verified
    moved = kyo("move", record, "verified")
    assert moved.code == 0, moved


def test_an_unmet_verdict_is_still_a_verdict(project: Path, kyo):
    """c1: the role asks that a verdict be recorded, not that it be `met` -- an
    `unmet-unbuilt` verdict leaves the state where it was and still satisfies it."""
    record = new_record(kyo, criteria=["first claim"])
    c1 = f"{record}.c1"
    ran = kyo("verify", c1, "--verdict", "unmet-unbuilt", "--method", "reasoned-about")
    assert ran.code == 0, ran
    moved = kyo("move", record, "verified")
    assert moved.code == 0, moved


def test_needs_verification_is_untouched(project: Path, kyo):
    """Entering needs-verification is how verdicts get asked for; it is not refused."""
    record = new_record(kyo, criteria=["first claim"])
    moved = kyo("move", record, "built")
    assert moved.code == 0, moved


def test_refusal_names_the_unapplied_report_and_accept_clears_it(
    git_project: Path, kyo, tmp_path
):
    """c3: a report holding a verdict for the criterion was ingested and never applied
    (the reporter's case). The refusal names that dispatch as `kyo accept <id>`, and
    running exactly that lets the move through."""
    record = new_record(kyo, criteria=["first claim"])
    c1 = f"{record}.c1"
    dispatch_id = _dispatch_and_report(kyo, tmp_path, record, [_verdict(c1, "met")])
    err = _refused(kyo, record)
    assert f"`kyo accept {dispatch_id}`" in err
    assert f"`kyo verify {c1} --verdict <verdict> --method <how>`" in err

    accepted = kyo("accept", dispatch_id)
    assert accepted.code == 0, accepted
    moved = kyo("move", record, "verified")
    assert moved.code == 0, moved


def test_a_report_with_no_verdict_for_it_is_not_named(git_project: Path, kyo, tmp_path):
    """c3: an ingested report that says nothing about the unverdicted criterion is not
    a remedy for it and is not offered."""
    record = new_record(kyo, criteria=["first claim"])
    silent = _dispatch_and_report(kyo, tmp_path, record, [])
    err = _refused(kyo, record)
    assert silent not in err


def test_the_contract_itself() -> None:
    """The unit form: removing the raise fails this whatever the callers do."""
    ladder = default_ladder()
    common = {
        "record_id": "TST-1",
        "kind": "bug",
        "from_status": "open",
        "reason": None,
        "requires_criteria": True,
        "ladder": ladder,
        "open_docs_obligations": [],
    }
    bare = CriterionView(id="TST-1.c1", text="a", state="unmet-unbuilt")
    with pytest.raises(ContractViolation) as refused:
        check_transition(to_status="verified", criteria=[bare], **common)
    assert refused.value.contract == CONTRACT_VERIFIED_REQUIRES_A_VERDICT
    with pytest.raises(ContractViolation) as named:
        check_transition(
            to_status="verified", criteria=[bare], waiting_reports=["TST-1.d1"], **common
        )
    assert "`kyo accept TST-1.d1`" in str(named.value)
    # A verdict on record, a verdict-only state, or a void criterion: nothing to refuse.
    for ok in (
        CriterionView(id="TST-1.c1", text="a", state="unmet-unbuilt", has_verdict=True),
        CriterionView(id="TST-1.c1", text="a", state="met"),
        CriterionView(id="TST-1.c1", text="a", state="void-withdrawn"),
    ):
        check_transition(to_status="verified", criteria=[ok], **common)
    check_transition(to_status="built", criteria=[bare], **common)
