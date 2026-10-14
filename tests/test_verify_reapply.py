"""KYO-415: applying a report twice, and an unmet verdict on a completed record.

Two defects, both of the form "the command's
outcome is not what its own status says":

* ``kyo verify --from-report D`` re-applied the same report on every rerun — a new
  verification row and a new evidence artifact per entry, ``met -> met``, exit 0 — which
  also made ``kyo accept``'s "a second run is safe" false. The fix derives "already
  applied" from rows already stored (the events ledger), so no schema bump (c9).
* ``kyo verify`` could flip a criterion of a ``done`` record to unmet and nothing
  noticed. The verdict is still recorded; what changed is that the command says so, with
  the remedy, and ``kyo doctor`` keeps saying so as a notice until it is resolved.

Fixtures are the shared ones :mod:`tests.test_kyo329_accept` already leans on.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import doctor as doctor_module
from kyosei.schema import SCHEMA_VERSION

from .conftest import new_record
from .test_kyo137_docs_obligations import (
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
)
from .test_kyo329_accept import _docs_verdict, _met, _states

NOTICE = "completed_with_unmet_criteria"


def _count(db, table: str) -> int:
    with db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _ingested(kyo, git_project: Path, **report_extra) -> tuple[str, str]:
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    report_extra.setdefault("criteria_verdicts", [_met(f"{record}.c1")])
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, touched_files=["src/app.py"], **report_extra
    )
    assert reported.code == 0, reported
    return record, dispatch_id


# ------------------------------------------------------------------ c4, c5, c6, c9


def test_rerunning_verify_from_report_records_nothing_twice(kyo, git_project: Path, db) -> None:
    """c4. The second run writes no verification and no evidence, exits 0, and names each
    skipped entry as already applied in text and in --json. Remove the skip in
    `_verify_from_report` and the row counts grow, which is what this asserts against."""
    record, dispatch_id = _ingested(kyo, git_project)
    first = kyo("verify", "--from-report", dispatch_id, "--json")
    assert first.code == 0, first
    assert [row["criterion_id"] for row in first.json["applied"]] == [f"{record}.c1"]
    assert first.json["already_applied"] == []
    verifications, evidence = _count(db, "verifications"), _count(db, "evidence_artifacts")

    again = kyo("verify", "--from-report", dispatch_id, "--json")
    assert again.code == 0, again
    assert again.json["applied"] == []
    assert [row["criterion_id"] for row in again.json["already_applied"]] == [f"{record}.c1"]
    assert again.json["already_applied"][0]["verification_id"] == (
        first.json["applied"][0]["verification_id"]
    )
    assert _count(db, "verifications") == verifications, "no second verification row"
    assert _count(db, "evidence_artifacts") == evidence, "no second evidence artifact"

    text = kyo("verify", "--from-report", dispatch_id)
    assert text.code == 0, text
    assert "already applied (1)" in text.out
    assert f"{record}.c1" in text.out
    assert "--again" in text.out, "the line names the way to apply them anyway (c5)"
    assert _count(db, "verifications") == verifications


def test_again_clears_the_skip(kyo, git_project: Path, db) -> None:
    """c5. The remedy the already-applied line names clears it: `--again` records the
    report's verdicts a second time, deliberately."""
    record, dispatch_id = _ingested(kyo, git_project)
    assert kyo("verify", "--from-report", dispatch_id).code == 0
    before = _count(db, "verifications")

    skipped = kyo("verify", "--from-report", dispatch_id, "--json")
    assert skipped.json["applied"] == [] and skipped.json["already_applied"]

    forced = kyo("verify", "--from-report", dispatch_id, "--again", "--json")
    assert forced.code == 0, forced
    assert forced.json["again"] is True
    assert [row["criterion_id"] for row in forced.json["applied"]] == [f"{record}.c1"]
    assert forced.json["already_applied"] == []
    assert _count(db, "verifications") == before + 1


def test_again_without_from_report_is_refused(kyo, git_project: Path) -> None:
    """`--again` means nothing to a hand-typed verdict, and silently ignoring it would
    let a caller believe it did something."""
    record = new_record(kyo, criteria=["a thing is true"])
    refused = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked", "--again"
    )
    assert refused.code != 0
    assert "--from-report" in refused.err


def test_a_verdict_recorded_by_hand_since_is_not_called_a_duplicate(
    kyo, git_project: Path
) -> None:
    """The already-applied skip keys on the criterion's LATEST verification. A controller
    who recorded a verdict of their own after the report was applied has made the
    report's the older word, so it is not called a duplicate — and, since KYO-511, it is
    not applied over that newer verdict either: it is named as a newer verdict kept, and
    `--again` is what re-applies it. (Before KYO-511 this test asserted the rerun applied
    it, which is the overwrite a consumer's APP-209 hit.)"""
    record, dispatch_id = _ingested(kyo, git_project)
    assert kyo("verify", "--from-report", dispatch_id).code == 0
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about"
    ).code == 0

    rerun = kyo("verify", "--from-report", dispatch_id, "--json")
    assert rerun.code == 0, rerun
    assert rerun.json["applied"] == []
    assert rerun.json["already_applied"] == []
    assert [row["criterion_id"] for row in rerun.json["newer_verdict_kept"]] == [
        f"{record}.c1"
    ]
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}
    forced = kyo("verify", "--from-report", dispatch_id, "--again", "--json")
    assert [row["criterion_id"] for row in forced.json["applied"]] == [f"{record}.c1"]


def test_accept_twice_leaves_one_verification_per_criterion(kyo, git_project: Path, db) -> None:
    """c6. The claim in `cmd_accept`'s docstring and the README — "a second run is safe"
    — made true for the verify step: two runs, one verification row."""
    record, dispatch_id = _ingested(kyo, git_project)
    assert kyo("accept", dispatch_id).code == 0
    second = kyo("accept", dispatch_id, "--json")
    assert second.code == 0, second
    shown = kyo("show", record, "--json").json
    assert len(shown["criteria"][0]["verifications"]) == 1
    assert _count(db, "verifications") == 1
    assert second.json["steps"][0]["data"]["already_applied"][0]["criterion_id"] == (
        f"{record}.c1"
    )


def test_the_skip_survives_a_rebuild(kyo, git_project: Path) -> None:
    """c9. The detection derives from the events ledger, which `kyo rebuild` restores,
    so the answer does not depend on the gitignored db surviving."""
    record, dispatch_id = _ingested(kyo, git_project)
    assert kyo("verify", "--from-report", dispatch_id).code == 0
    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt
    rerun = kyo("verify", "--from-report", dispatch_id, "--json")
    assert rerun.json["applied"] == []
    assert [row["criterion_id"] for row in rerun.json["already_applied"]] == [f"{record}.c1"]


def test_no_schema_bump_ships_with_this() -> None:
    """c9. The dedupe reads rows already stored; the plan's single schema record owns
    any bump. A change here means someone added a stored field for it."""
    # KYO-418 was that single schema record, and its bump to 25 the plan's only one. The
    # bump to 26 is KYO-498's (records.since_note), not this dedupe's; a further bump must
    # move this pin and name the record that owns it. The bump to 27 is KYO-554's
    # (docs_obligations.dispatch_id nullable, for a record's standing obligations).
    assert SCHEMA_VERSION == 27


# ------------------------------------------------------------------------- c7, c8


def _done_record(kyo) -> str:
    record = new_record(kyo, criteria=["a thing is true"])
    met = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked")
    assert met.code == 0, met
    moved = kyo("move", record, "done")
    assert moved.code == 0, moved
    return record


def test_single_verify_of_an_unmet_verdict_on_a_done_record_names_the_remedy(
    kyo, project: Path
) -> None:
    """c7, the original repro. The verdict is still recorded; the line naming the
    record's status and both remedies is what is new — delete it and this fails."""
    record = _done_record(kyo)
    flipped = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about"
    )
    assert flipped.code == 0, flipped
    assert _states(kyo, record) == {f"{record}.c1": "unmet-unbuilt"}, "still recorded"
    assert f"{record} is done" in flipped.out
    assert f"kyo split {record} --to-successor {record}.c1" in flipped.out
    assert f"kyo move {record} open" in flipped.out

    as_json = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about",
        "--json",
    )  # fmt: skip
    found = as_json.json["completed_with_unmet"]
    assert found["record_id"] == record and found["status"] == "done"
    assert found["split_remedy"] == f"kyo split {record} --to-successor {record}.c1"


def test_a_met_verdict_on_a_done_record_says_nothing(kyo, project: Path) -> None:
    record = _done_record(kyo)
    again = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked", "--json"
    )
    assert again.json["completed_with_unmet"] is None
    assert "--to-successor" not in kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked"
    ).out


def test_verify_from_report_on_a_done_record_names_the_remedy(kyo, git_project: Path) -> None:
    """c7 for the path most verdicts arrive by. The report says unmet; the controller
    had already verified and closed the record by hand, so applying it now lands an
    unmet verdict on a done record. Since KYO-511 that hand verdict is newer than the
    report and a plain run keeps it, so landing the report's takes `--again`."""
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo, git_project, record, touched_files=["src/app.py"],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "unmet-unbuilt",
                "method": "machine-checked",
                "evidence": "the regression test fails on main",
            }
        ],
        docs_verdicts=[_docs_verdict()],
    )  # fmt: skip
    assert reported.code == 0, reported
    assert kyo("docs", "settle", "--from-report", dispatch_id).code == 0
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked"
    ).code == 0
    assert kyo("move", record, "done").code == 0

    kept = kyo("verify", "--from-report", dispatch_id, "--json")
    assert kept.code == 0, kept
    assert kept.json["applied"] == [] and kept.json["completed_with_unmet"] == []
    applied = kyo("verify", "--from-report", dispatch_id, "--again", "--json")
    assert applied.code == 0, applied
    [found] = applied.json["completed_with_unmet"]
    assert found["record_id"] == record
    text = kyo("verify", "--from-report", dispatch_id, "--again")
    assert f"kyo split {record} --to-successor {record}.c1" in text.out
    assert f"{record} is done" in text.out


def _notices(kyo) -> list[str]:
    result = kyo("doctor", "--json")
    return list(result.json["checks"][NOTICE]["failures"])


def test_doctor_notices_a_done_record_holding_an_unmet_criterion(kyo, project: Path) -> None:
    """c8. A notice, not a problem: the Stop hook stays green. Remove the check and the
    finding is gone, which is what this asserts against."""
    record = _done_record(kyo)
    assert _notices(kyo) == []
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about"
    ).code == 0
    [finding] = _notices(kyo)
    assert record in finding and f"--to-successor {record}.c1" in finding
    assert NOTICE in doctor_module.NOTICE_CHECKS
    ran = kyo("doctor")
    assert ran.code == 0, "a notice never fails doctor"
    assert NOTICE in ran.out


def test_reopening_the_record_clears_the_notice(kyo, project: Path) -> None:
    """c8: the reopen remedy the notice names clears it."""
    record = _done_record(kyo)
    kyo("verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about")
    [finding] = _notices(kyo)
    assert f"kyo move {record} open" in finding
    assert kyo("move", record, "open").code == 0
    assert _notices(kyo) == []


def test_splitting_the_criterion_to_a_successor_clears_the_notice(kyo, project: Path) -> None:
    """c8: the split remedy the notice names clears it."""
    record = _done_record(kyo)
    kyo("verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about")
    [finding] = _notices(kyo)
    split = f"kyo split {record} --to-successor {record}.c1"
    assert split in finding
    done = kyo(*split.split()[1:])
    assert done.code == 0, done
    assert _notices(kyo) == []
