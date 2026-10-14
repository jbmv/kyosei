"""KYO-545: ``kyo show <report-id>`` prints a stored report instead of refusing it.

Once a worker's worktree is pruned, the stored body is the only copy of its report,
and it is the copy an amending report starts from."""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.errors import EXIT_ERROR

from .conftest import new_record, valid_report


def _dispatch(kyo, record: str) -> dict:
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    return result.json


def _file(tmp_path: Path, kyo, d: dict, payload: dict, name: str) -> dict:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = kyo("report", d["dispatch_id"], "--file", str(path), "--json")
    assert result.code == 0, result
    return result.json


def test_a_shown_body_round_trips_into_an_amending_report(kyo, git_project, tmp_path) -> None:
    """c1: the --json body, plus `amends`, is valid input to `kyo report --file`."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    original = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    first_id = _file(tmp_path, kyo, d, original, "first.json")["report_id"]

    shown = kyo("show", first_id, "--json")
    assert shown.code == 0, shown
    doc = shown.json
    assert doc["object"] == "report"
    assert doc["id"] == first_id
    assert doc["dispatch_id"] == d["dispatch_id"]
    assert doc["record_id"] == record
    assert doc["created_at"]
    assert doc["amends"] is None and doc["amended_by"] is None
    assert doc["standing"] == "current"
    assert doc["body"] == original  # verbatim

    amended_body = {**doc["body"], "amends": first_id}
    second_id = _file(tmp_path, kyo, d, amended_body, "second.json")["report_id"]
    assert second_id != first_id

    first_again = kyo("show", first_id, "--json").json
    assert first_again["amended_by"] == second_id
    assert first_again["standing"] == "superseded"
    second = kyo("show", second_id, "--json").json
    assert second["amends"] == first_id
    assert second["standing"] == "current"
    assert second["body"] == amended_body

    text = kyo("show", first_id)
    assert text.code == 0, text
    assert f"report {first_id}  (dispatch {d['dispatch_id']} on {record})" in text.out
    assert f"amended by: {second_id}" in text.out
    assert "ingested: " in text.out
    assert f'"criterion_id": "{record}.c1"' in text.out
    text2 = kyo("show", second_id)
    assert f"amends: {first_id}" in text2.out


def test_an_unknown_report_id_names_the_reports_the_record_has(
    kyo, git_project, tmp_path
) -> None:
    """c2: no shape refusal for a report id; an unknown one names what exists."""
    record = new_record(kyo, criteria=["a"])
    none_yet = kyo("show", f"{record}.r1")
    assert none_yet.code == EXIT_ERROR, none_yet
    assert "is a report id" not in none_yet.err
    assert f"no report '{record}.r1'" in none_yet.err
    assert "no reports at all" in none_yet.err

    d = _dispatch(kyo, record)
    payload = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"]
    )
    first_id = _file(tmp_path, kyo, d, payload, "r.json")["report_id"]
    missing = kyo("show", f"{record}.r9")
    assert missing.code == EXIT_ERROR, missing
    assert f"these reports: {first_id}" in missing.err

    # A report id on a record that does not exist keeps the record's own answer.
    no_record = kyo("show", "TST-9999.r1")
    assert no_record.code == EXIT_ERROR, no_record
    assert "no record" in no_record.err
