"""KYO-291: a record a dispatch formally covers names that dispatch and its report.

`kyo dispatch <primary> --also-covers <covered>` makes one dispatch speak for several
records, and everything it produces hangs off the primary one: the dispatch row, the
frozen brief, the branch, and the report ingested into it. The covered record carried
none of it — `kyo show KYO-177` printed `dispatches (0)` and `reports (0, 0
superseded)` while `KYO-133.d1` formally covered it, so the record whose criteria that
report settled could not say where the report was.

The report is linked to the covered record rather than split, so what is checked
here is a READ of `covers_json` walked backwards: a `covered by dispatches on other
records` block in the text, `covered_by_dispatches` in `--json`, the record's own
`dispatches (N)` count untouched, and a record nothing covers printing exactly what it
printed before.
"""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import new_record, valid_report

HEADING = "covered by dispatches on other records"


def _report_against(kyo, git_project: Path, dispatch: dict, primary: str, covered: str) -> str:
    """Ingest a report for ``dispatch``, settling a criterion on each record."""
    body = valid_report(
        dispatch_id=dispatch["dispatch_id"],
        record_id=primary,
        worked_from_sha=dispatch["worked_from_sha"],
        criteria_verdicts=[
            {"criterion_id": f"{primary}.c1", "verdict": "met", "method": "reasoned-about"},
            {"criterion_id": f"{covered}.c1", "verdict": "met", "method": "reasoned-about"},
        ],
    )
    path = git_project / "report.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    result = kyo("report", dispatch["dispatch_id"], "--file", str(path), "--json")
    assert result.code == 0, result
    return result.json["report_id"]


def test_a_covered_record_names_the_running_dispatch_that_covers_it(
    kyo, git_project: Path
) -> None:
    """Before any report exists: the covered record already says who is speaking
    for it, and says plainly that nothing has reported yet."""
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    dispatch = kyo("dispatch", primary, "--also-covers", covered, "--json")
    assert dispatch.code == 0, dispatch
    dispatch_id = dispatch.json["dispatch_id"]

    shown = kyo("show", covered)
    assert shown.code == 0, shown
    assert HEADING in shown.out
    line = next(ln for ln in shown.out.splitlines() if ln.strip().startswith(dispatch_id))
    assert "state: running" in line
    # The primary record, because that is where the brief, the branch and any report
    # actually live — a covered record that named only the dispatch id would still
    # leave a reader without the page that holds it.
    assert f"dispatched against: {primary}" in line
    assert "(none ingested yet)" in line


def test_a_covered_record_names_the_report_once_one_is_ingested(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    dispatch = kyo("dispatch", primary, "--also-covers", covered, "--json")
    report_id = _report_against(kyo, git_project, dispatch.json, primary, covered)

    shown = kyo("show", covered)
    line = next(
        ln for ln in shown.out.splitlines() if ln.strip().startswith(dispatch.json["dispatch_id"])
    )
    assert "state: reported" in line
    assert f"dispatched against: {primary}" in line
    assert f"report: {report_id}" in line

    # And that report really does settle this record's own criterion — `kyo verify
    # --from-report` reaches a covered record's criteria already (KYO-198), which is
    # exactly why a reader of this record needs to be able to reach the report.
    assert kyo("verify", "--from-report", dispatch.json["dispatch_id"]).code == 0
    assert kyo("show", covered, "--json").json["criteria"][0]["state"] == "met"


def test_the_json_field_is_additive_and_carries_the_same_four_facts(
    kyo, git_project: Path
) -> None:
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    dispatch = kyo("dispatch", primary, "--also-covers", covered, "--json")
    report_id = _report_against(kyo, git_project, dispatch.json, primary, covered)

    payload = kyo("show", covered, "--json").json
    assert payload["covered_by_dispatches"] == [
        {
            "id": dispatch.json["dispatch_id"],
            "state": "reported",
            "primary_record_id": primary,
            "report_id": report_id,
        }
    ]
    # Additive: everything the document carried before is still under the key it was
    # under, and the new field is not one of them.
    assert payload["id"] == covered
    assert payload["criteria"][0]["id"] == f"{covered}.c1"


def test_the_covered_records_own_dispatch_count_stays_its_own(kyo, git_project: Path) -> None:
    """`dispatches (N)` answers "who was sent to work on this record", and a dispatch
    covering it is not one of them. Two different questions, two different blocks."""
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    dispatch = kyo("dispatch", primary, "--also-covers", covered, "--json")

    shown = kyo("show", covered)
    assert "dispatches (0):" in shown.out
    assert "reports (0, 0 superseded):" in shown.out
    assert HEADING in shown.out

    payload = kyo("show", covered, "--json").json
    assert payload["dispatches"] == []
    assert payload["reports"] == []
    assert [entry["id"] for entry in payload["covered_by_dispatches"]] == [
        dispatch.json["dispatch_id"]
    ]


def test_the_primary_record_gains_nothing_it_did_not_already_print(
    kyo, git_project: Path
) -> None:
    """The primary already says `also formally covers` on its own dispatch. Listing
    its own dispatch a second time under the new heading would make one dispatch read
    as two."""
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    kyo("dispatch", primary, "--also-covers", covered, "--json")

    shown = kyo("show", primary)
    assert "dispatches (1):" in shown.out
    assert f"also formally covers: {covered}" in shown.out
    assert HEADING not in shown.out
    assert kyo("show", primary, "--json").json["covered_by_dispatches"] == []


def test_a_record_nothing_covers_prints_what_it_printed_before(kyo, git_project: Path) -> None:
    """The one place the block departs from the always-a-section rule: a record no
    dispatch covered — nearly every record — must be byte-identical to what it was."""
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    bystander = new_record(kyo, criteria=["bystander criterion"])

    before = kyo("show", bystander)
    kyo("dispatch", primary, "--also-covers", covered, "--json")
    after = kyo("show", bystander)

    assert after.out == before.out
    assert HEADING not in after.out
    # `--json` carries the key unconditionally, because a machine reader cannot tell an
    # absent key from a build that never had the field.
    assert kyo("show", bystander, "--json").json["covered_by_dispatches"] == []


def test_a_record_covered_by_two_dispatches_names_both_in_dispatch_order(
    kyo, git_project: Path
) -> None:
    covered = new_record(kyo, criteria=["covered criterion"])
    first = new_record(kyo, criteria=["first primary criterion"])
    second = new_record(kyo, criteria=["second primary criterion"])
    one = kyo("dispatch", first, "--also-covers", covered, "--json")
    assert one.code == 0, one
    two = kyo("dispatch", second, "--also-covers", covered, "--force", "--json")
    assert two.code == 0, two

    payload = kyo("show", covered, "--json").json
    assert [entry["id"] for entry in payload["covered_by_dispatches"]] == [
        one.json["dispatch_id"],
        two.json["dispatch_id"],
    ]
    assert [entry["primary_record_id"] for entry in payload["covered_by_dispatches"]] == [
        first,
        second,
    ]


def test_nothing_about_the_covering_walk_is_stored(kyo, git_project: Path) -> None:
    """A read, not a field: the export document is where a schema version would have
    to move, and it does not carry this."""
    from .conftest import export_of

    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    kyo("dispatch", primary, "--also-covers", covered, "--json")

    doc = json.loads(export_of(git_project, covered).read_text(encoding="utf-8"))
    assert "covered_by_dispatches" not in doc
    assert doc["dispatches"] == []
    # And `kyo doctor` still agrees the export matches the database, which it would
    # not if a display-time derivation had leaked into a stored document.
    assert kyo("doctor", "--json").json["ok"] is True
