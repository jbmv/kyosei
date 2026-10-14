"""KYO-478: a completion move refused over unsettled criteria names the ingested,
unapplied report that already met them, and the command that applies it.

A consumer report: three dispatches on one record, only the
last accepted; `kyo move <id> done` refused naming c1-c3 as unmet and recommended
`kyo split --to-successor`, while the first two dispatches' reports held `met` for
them unapplied. The split is the wrong remedy there: the verdicts exist and only
need applying.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.contracts import CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA

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


def _refusal(kyo, record: str) -> str:
    moved = kyo("move", record, "done")
    assert moved.code == 2, moved
    assert CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA in moved.err
    return moved.err


def test_refusal_names_the_unapplied_report_and_its_command(git_project: Path, kyo, tmp_path):
    """c1: two dispatches; the first report's `met` for c1 was ingested and never
    applied. The refusal names that dispatch and the command against c1."""
    record = new_record(kyo, criteria=["first claim"])
    c1 = f"{record}.c1"
    first = _dispatch_and_report(kyo, tmp_path, record, [_verdict(c1, "met")])
    second = _dispatch_and_report(kyo, tmp_path, record, [])

    err = _refusal(kyo, record)
    assert f"dispatch {first}'s ingested report holds an unapplied `met` for {c1}" in err
    assert f"`kyo verify --from-report {first} --only {c1}`" in err
    assert second not in err
    # c3: every unsettled criterion is covered, so `kyo split` is not offered.
    assert "kyo split" not in err


def test_the_named_remedy_clears_the_refusal(git_project: Path, kyo, tmp_path):
    """c2: running exactly the command the refusal names lets the move through."""
    record = new_record(kyo, criteria=["first claim"])
    c1 = f"{record}.c1"
    first = _dispatch_and_report(kyo, tmp_path, record, [_verdict(c1, "met")])
    _refusal(kyo, record)

    applied = kyo("verify", "--from-report", first, "--only", c1)
    assert applied.code == 0, applied
    moved = kyo("move", record, "done")
    assert moved.code == 0, moved


def test_uncovered_criteria_keep_the_split_line(git_project: Path, kyo, tmp_path):
    """c3 + c4: a criterion no report met keeps `kyo split`; a report holding an unmet
    verdict, or one whose `met` was applied and then overridden by hand, is not named."""
    record = new_record(kyo, criteria=["covered", "reported unmet", "applied then reverted"])
    c1, c2, c3 = (f"{record}.c{n}" for n in (1, 2, 3))
    covering = _dispatch_and_report(kyo, tmp_path, record, [_verdict(c1, "met")])
    unmet = _dispatch_and_report(kyo, tmp_path, record, [_verdict(c2, "unmet-unbuilt")])
    applied = _dispatch_and_report(kyo, tmp_path, record, [_verdict(c3, "met")])
    ran = kyo("verify", "--from-report", applied)
    assert ran.code == 0, ran
    # A newer, hand-recorded verdict: the report's `met` was applied, so re-applying
    # it would overwrite this decision — it must not be offered as the remedy.
    reverted = kyo(
        "verify", c3, "--verdict", "unmet-unbuilt", "--method", "reasoned-about"
    )
    assert reverted.code == 0, reverted

    err = _refusal(kyo, record)
    assert f"`kyo verify --from-report {covering} --only {c1}`" in err
    assert unmet not in err
    assert applied not in err
    assert f"`kyo split {record} --to-successor {c2} {c3}`" in err


def test_no_report_keeps_the_existing_refusal(git_project: Path, kyo):
    """c3: with nothing ingested the refusal is the one it always was."""
    record = new_record(kyo, criteria=["a claim"])
    err = _refusal(kyo, record)
    assert f"`kyo split {record} --to-successor {record}.c1`" in err
    assert "--from-report" not in err
