"""KYO-537: a worker's report cannot carry the method ``human-observed``.

That label says a person looked. A worker has written it when no one had, and the
orchestrator then had to re-record the verdicts. ``kyo report`` and ``--check`` refuse it;
the orchestrator's own ``kyo verify`` still accepts it.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import report_schema as rs
from kyosei.report_schema import validate_report

from .conftest import new_record, valid_report


def _doc(verdict_method: str, docs_method: str | None = None) -> dict:
    extra = {}
    if docs_method is not None:
        extra["docs_verdicts"] = [
            {
                "doc_path": "README.md", "section": "Intro", "verdict": "not-affected",
                "method": docs_method, "reason": "read it",
            }
        ]
    return valid_report(
        dispatch_id="TST-1.d1", record_id="TST-1", worked_from_sha="abc1234",
        criteria_verdicts=[
            {"criterion_id": "TST-1.c1", "verdict": "met", "method": verdict_method}
        ],
        **extra,
    )  # fmt: skip


def test_criterion_verdict_with_human_observed_is_refused_naming_the_entry() -> None:
    problems = validate_report(_doc("human-observed"))
    assert len(problems) == 1, problems
    assert problems[0].startswith("criteria_verdicts[0].method:")
    assert "'machine-checked', 'reasoned-about'" in problems[0]


def test_docs_verdict_with_human_observed_is_refused_naming_the_entry() -> None:
    problems = validate_report(_doc("machine-checked", docs_method="human-observed"))
    assert len(problems) == 1, problems
    assert problems[0].startswith("docs_verdicts[0].method:")


def test_both_entries_are_named_when_both_carry_it() -> None:
    problems = validate_report(_doc("human-observed", docs_method="human-observed"))
    assert [p.split(".method")[0] for p in problems] == ["criteria_verdicts[0]", "docs_verdicts[0]"]


def test_corrected_report_passes() -> None:
    assert validate_report(_doc("reasoned-about", docs_method="I re-read the section")) == []


def test_kyo_report_check_and_ingest_refuse_it_and_the_corrected_file_passes(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    record = new_record(kyo, criteria=["it works"])
    d = kyo("dispatch", record, "--json")
    assert d.code == 0, d
    dispatch_id = d.json["dispatch_id"]

    def report(method: str) -> str:
        path = tmp_path / "r.json"
        doc = valid_report(
            dispatch_id=dispatch_id, record_id=record,
            worked_from_sha=d.json["worked_from_sha"],
            criteria_verdicts=[
                {"criterion_id": f"{record}.c1", "verdict": "met", "method": method}
            ],
        )  # fmt: skip
        path.write_text(json.dumps(doc), encoding="utf-8")
        return str(path)

    bad = report("human-observed")
    for extra in (["--check"], []):
        refused = kyo("report", dispatch_id, "--file", bad, *extra)
        assert refused.code != 0, refused
        assert "criteria_verdicts[0].method" in (refused.out + refused.err)
        assert "reasoned-about" in (refused.out + refused.err)
    good = report("reasoned-about")
    assert kyo("report", dispatch_id, "--file", good, "--check").code == 0
    assert kyo("report", dispatch_id, "--file", good).code == 0


def test_report_schema_text_says_workers_may_not_use_it(kyo) -> None:
    printed = kyo("report-schema")
    assert printed.code == 0, printed
    assert "may NOT use it" in printed.out
    # The schema document itself still lists the value: it is the orchestrator's label.
    assert "human-observed" in rs.schema_document()["$defs"] or "human-observed" in printed.out


def test_orchestrator_verify_still_accepts_human_observed(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["it works"])
    verified = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed"
    )
    assert verified.code == 0, verified
