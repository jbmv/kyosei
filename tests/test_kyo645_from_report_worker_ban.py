"""KYO-645 (decision KYO-640, P11): the worker ban on ``human-observed`` is re-applied
when a stored report is applied, by ``kyo verify --from-report`` and ``kyo accept``.

Ingest refuses the method (KYO-537), so the only stored report that carries it is one
stored before that rule; these tests build one by rewriting a stored body in place.
"""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import new_record, valid_report


def _store_report_with_human_observed(kyo, db, tmp_path: Path):
    record = new_record(kyo, criteria=["it works"])
    d = kyo("dispatch", record, "--json").json

    def verdict(method: str) -> dict:
        return {"criterion_id": f"{record}.c1", "verdict": "met", "method": method,
                "evidence": "looked"}  # fmt: skip

    def file(name: str, method: str, **extra) -> str:
        doc = valid_report(
            dispatch_id=d["dispatch_id"], record_id=record,
            worked_from_sha=d["worked_from_sha"],
            criteria_verdicts=[verdict(method)], **extra,
        )  # fmt: skip
        path = tmp_path / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return str(path)

    ingested = kyo(
        "report", d["dispatch_id"], "--file", file("r1.json", "reasoned-about"), "--json"
    )
    assert ingested.code == 0, ingested
    first = ingested.json["report_id"]
    conn = db()
    body = json.loads(
        conn.execute("SELECT body_json FROM reports WHERE id = ?", (first,)).fetchone()[0]
    )
    body["criteria_verdicts"][0]["method"] = "human-observed"
    conn.execute("UPDATE reports SET body_json = ? WHERE id = ?", (json.dumps(body), first))
    conn.close()
    return record, d["dispatch_id"], first, file


def test_verify_from_report_refuses_then_a_corrected_report_clears_it(
    kyo, git_project: Path, tmp_path: Path, db
) -> None:
    record, dispatch_id, first, file = _store_report_with_human_observed(kyo, db, tmp_path)
    refused = kyo("verify", "--from-report", dispatch_id)
    assert refused.code != 0, refused
    text = refused.out + refused.err
    assert "human-observed" in text and f"{record}.c1" in text
    assert "amends" in text and "reasoned-about" in text

    fixed = kyo("report", dispatch_id, "--file", file("r2.json", "reasoned-about", amends=first))
    assert fixed.code == 0, fixed
    assert kyo("verify", "--from-report", dispatch_id).code == 0


def test_accept_refuses_the_same_report_and_the_corrected_one_clears_it(
    kyo, git_project: Path, tmp_path: Path, db
) -> None:
    record, dispatch_id, first, file = _store_report_with_human_observed(kyo, db, tmp_path)
    refused = kyo("accept", dispatch_id)
    assert refused.code != 0, refused
    assert "human-observed" in refused.out + refused.err
    fixed = kyo("report", dispatch_id, "--file", file("r2.json", "reasoned-about", amends=first))
    assert fixed.code == 0, fixed
    accepted = kyo("accept", dispatch_id)
    assert "human-observed" not in accepted.out + accepted.err, accepted
