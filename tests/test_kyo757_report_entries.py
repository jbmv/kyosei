"""KYO-757: a report's false premises, disagreements, corrected numbers and verdicts have
ids, read from the report's stored body by place, and `kyo show` reads one.

The ids are what a client anchors a prompt to (`tests/test_kyo_serve_notes.py`); here they
are checked as `kyo show --json` gives them and as `kyo show <entry id>` prints an entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import cmd_views, report_entries
from kyosei.errors import EXIT_ERROR, EXIT_USAGE

from .conftest import new_record, valid_report


def _file(tmp_path: Path, kyo, dispatch: dict, payload: dict, name: str) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = kyo("report", dispatch["dispatch_id"], "--file", str(path), "--json")
    assert result.code == 0, result
    return result.json["report_id"]


@pytest.fixture
def reported(kyo, git_project, tmp_path: Path) -> dict:
    """A record with one report holding two false premises and one of each other entry."""
    record = new_record(kyo, title="Owning record", criteria=["first thing"])
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    body = valid_report(
        dispatch_id=dispatched.json["dispatch_id"],
        record_id=record,
        worked_from_sha=dispatched.json["worked_from_sha"],
        premise_false=[
            {"claim": "the cache is warm", "correction": "it is cold\non a fresh clone"},
            {"claim": "the suite takes a minute", "correction": "four", "impact": "none"},
        ],
        disagreements=[
            {
                "topic": "where the helper lives",
                "brief_said": "in the route",
                "worker_did": "a module of its own",
                "rationale": "two callers",
            }
        ],
        corrected_numbers=[
            {
                "claim": "tests in the file",
                "brief_value": "12",
                "actual_value": "14",
                "method": "counted",
            }
        ],
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "met",
                "method": "machine-checked",
                "evidence": "the walk passes",
            }
        ],
    )
    report = _file(tmp_path, kyo, dispatched.json, body, "first.json")
    return {"record": record, "report": report, "body": body, "dispatch": dispatched.json}


def test_show_json_gives_each_entry_its_id_beside_the_body(kyo, reported: dict) -> None:
    """The record's and the report's --json carry the ids, and the body is left as stored."""
    report = reported["report"]
    wanted = {
        "premise_false": [f"{report}.p1", f"{report}.p2"],
        "disagreements": [f"{report}.x1"],
        "corrected_numbers": [f"{report}.n1"],
        "criteria_verdicts": [f"{report}.v1"],
    }
    on_the_record = kyo("show", reported["record"], "--json").json["reports"][0]
    assert on_the_record["entry_ids"] == wanted
    alone = kyo("show", report, "--json").json
    assert alone["entry_ids"] == wanted
    # The body is what an amendment is written from, and the schema refuses an unknown key.
    assert alone["body"] == reported["body"] == on_the_record["body"]


def test_the_ids_are_derived_and_nothing_is_stored_for_them(kyo, reported: dict) -> None:
    """No schema change: the export document and the stored body hold no entry id."""
    exported = Path(kyo.project) / "kyosei" / "export" / f"{reported['record']}.json"
    assert "entry_ids" not in exported.read_text(encoding="utf-8")
    assert f"{reported['report']}.p1" not in exported.read_text(encoding="utf-8")


def test_each_entry_is_shown_by_its_id_in_text_and_json(kyo, reported: dict) -> None:
    report, record, body = reported["report"], reported["record"], reported["body"]
    cases = {
        f"{report}.p2": ("false premise", "premise_false", body["premise_false"][1]),
        f"{report}.x1": ("disagreement", "disagreements", body["disagreements"][0]),
        f"{report}.n1": ("corrected number", "corrected_numbers", body["corrected_numbers"][0]),
        f"{report}.v1": ("verdict", "criteria_verdicts", body["criteria_verdicts"][0]),
    }
    for entry_id, (name, array, entry) in cases.items():
        shown = kyo("show", entry_id)
        assert shown.code == 0, shown
        first, second = shown.out.splitlines()[:2]
        assert first == f"{name} {entry_id}  on {record}  Owning record"
        assert report in second and "[current]" in second
        for key, value in entry.items():
            assert f"  {key}: {value}" in shown.out, (entry_id, key)
        as_json = kyo("show", entry_id, "--json").json
        assert as_json["object"] == "report-entry"
        assert (as_json["id"], as_json["kind"], as_json["entry"]) == (entry_id, array, entry)
        assert (as_json["report_id"], as_json["record_id"]) == (report, record)
        assert as_json["standing"] == "current" and as_json["amended_by"] is None
    # A value of several lines keeps them, indented under its key.
    assert "  correction: it is cold\n    on a fresh clone" in kyo("show", f"{report}.p1").out


def test_the_report_lists_its_entries_with_the_words_an_anchor_quotes(
    kyo, reported: dict
) -> None:
    report = reported["report"]
    shown = kyo("show", report).out
    assert "entries (5):" in shown
    for line in (
        f"  {report}.p1  false premise: the cache is warm",
        f"  {report}.p2  false premise: the suite takes a minute",
        f"  {report}.x1  disagreement: where the helper lives",
        f"  {report}.n1  corrected number: tests in the file",
        f"  {report}.v1  verdict: the walk passes",
    ):
        assert line in shown, line


def test_a_superseded_reports_entries_keep_their_ids_and_say_so(
    kyo, reported: dict, tmp_path: Path
) -> None:
    """An amendment is a second report with ids of its own; the first one's stay readable."""
    first = reported["report"]
    amended = {**reported["body"], "amends": first}
    amended["premise_false"] = amended["premise_false"][1:]
    second = _file(tmp_path, kyo, reported["dispatch"], amended, "second.json")
    old = kyo("show", f"{first}.p1", "--json").json
    assert old["entry"]["claim"] == "the cache is warm"
    assert old["standing"] == "superseded" and old["amended_by"] == second
    assert f"SUPERSEDED by {second}" in kyo("show", f"{first}.p1").out
    new = kyo("show", f"{second}.p1", "--json").json
    assert new["entry"]["claim"] == "the suite takes a minute" and new["standing"] == "current"
    # The amending report holds one premise, so the second place is the first report's alone.
    assert kyo("show", f"{first}.p2").code == 0
    assert kyo("show", f"{second}.p2").code == EXIT_ERROR


def test_an_entry_the_report_does_not_hold_names_the_report_and_its_listing(
    kyo, reported: dict
) -> None:
    """The remedy the refusal names runs and prints the ids the report does hold."""
    report, record = reported["report"], reported["record"]
    refused = kyo("show", f"{report}.p9")
    assert refused.code == EXIT_ERROR, refused
    assert f"holds false premise ids {report}.p1, {report}.p2" in refused.err
    assert f"`kyo show {report}`" in refused.err
    listing = kyo("show", report)
    assert listing.code == 0 and f"{report}.p1" in listing.out
    # A report the record does not hold, and a record the store does not hold.
    absent = kyo("show", f"{record}.r9.x1")
    assert absent.code == EXIT_ERROR and f"these reports: {report}" in absent.err
    assert kyo("show", "TST-999.r1.p1").code == EXIT_ERROR
    assert "no record" in kyo("show", "TST-999.r1.p1").err


def test_a_letter_no_entry_has_is_refused_as_a_shape(kyo, reported: dict) -> None:
    """Docs verdicts and commits have no entry id, and the shapes named include the four."""
    refused = kyo("show", f"{reported['report']}.c1")
    assert refused.code == EXIT_USAGE, refused
    assert "PREFIX-N.rN.pN, .xN, .nN or .vN" in refused.err
    assert any("rN.pN" in shape for shape, _what in cmd_views.SHOWABLE_SHAPES)


def test_the_words_of_an_entry_are_what_says_which_one_it_is() -> None:
    verdict = {"criterion_id": "TST-1.c1", "verdict": "unmet-unobserved", "method": "x"}
    assert report_entries.words("criteria_verdicts", verdict) == "TST-1.c1 unmet-unobserved"
    assert report_entries.words("criteria_verdicts", {**verdict, "reason": "no window"}) == (
        "no window"
    )
    assert report_entries.words("disagreements", {"topic": "the letters"}) == "the letters"
    assert report_entries.parse("TST-1.r2.x3") == ("TST-1.r2", "disagreements", 3)
    # One dot is a record's own child, and a place is counted from 1.
    assert report_entries.parse("TST-1.x3") is None
    assert report_entries.parse("TST-1.r2.p0") is None
    # Every array with ids is one the report schema requires, so an old body has them all.
    assert set(report_entries.KEYS) == set(report_entries.ARRAY_BY_LETTER.values())
