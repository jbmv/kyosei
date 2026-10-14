"""KYO-58 — correction before the first verdict, restatement after it.

Immutability is right and its start line was wrong. The hazard it exists for is a
verdict attesting to text that later changed; an unverified criterion has no
attestation to protect. These tests cover the three mechanisms that follow:

* ``kyo edit --criterion X --text-file`` while nothing has attested to X (c1/c4);
* ``kyo restate X --text-file``, void-and-add in one transaction with a mechanical
  ``replaces``/``replaced-by`` relation and a `kyo doctor` symmetry check (c2);
* the ``superseded`` reading, DERIVED from ``(state, reason)`` rather than stored,
  and rendered wherever a criterion's state is shown to a human (c5/c6).
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import contracts, report_schema
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR, EXIT_USAGE

from .conftest import event_lines, export_of, new_record, verify_criterion

REPO_ROOT = Path(__file__).resolve().parents[1]


def _criteria(kyo, record: str) -> list[dict]:
    return kyo("show", record, "--json").json["criteria"]


# --------------------------------------------------------------- c1: correction


def test_an_unverified_criterion_can_have_its_text_corrected(kyo, write_file) -> None:
    """The KYO-33.c2 case: a criterion that named a wrong example, caught while it
    was ``unmet-unbuilt`` with no verdict recorded."""
    record = new_record(kyo, criteria=["path.py:105 is flagged by the scanner"])
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("fix.txt", "path.py:117 is flagged by the scanner\n"),
    )
    assert result.code == 0, result

    criterion = _criteria(kyo, record)[0]
    assert criterion["text"] == "path.py:117 is flagged by the scanner"
    assert criterion["id"] == f"{record}.c1", "a correction keeps the id (law 4)"
    assert criterion["state"] == "unmet-unbuilt"


def test_the_original_wording_stays_in_the_events_ledger(kyo, write_file) -> None:
    """The edit is an event like any other, so nothing is lost by correcting it."""
    record = new_record(kyo, criteria=["amber AND red"])
    kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("fix.txt", "amber at one minute\n"),
    )
    events = event_lines(kyo.project)
    # KYO-418: a new line carries `after` + `changed`, an old one `before`; the prior
    # document is read through the one reader that answers for both.
    from kyosei.events_ledger import event_before_document

    befores = [
        c["text"]
        for e in events
        for c in (event_before_document(e) or {}).get("criteria", [])
    ]
    assert "amber AND red" in befores


def test_a_verified_criterion_refuses_correction_and_names_restate(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["the endpoint returns 200"])
    verify_criterion(kyo, f"{record}.c1", "met", method="machine-checked")

    result = kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("fix.txt", "the endpoint returns 201\n"),
    )
    assert result.code == EXIT_CONTRACT, result
    assert contracts.CONTRACT_CRITERION_TEXT_IS_ATTESTED in result.err
    assert f"kyo restate {record}.c1" in result.err
    assert _criteria(kyo, record)[0]["text"] == "the endpoint returns 200"


def test_a_verification_that_left_the_state_alone_still_closes_the_window(
    kyo, write_file
) -> None:
    """``kyo verify --verdict unmet-unbuilt`` records a verdict without changing the
    state. The window is "no verification row AND still unmet-unbuilt" — a
    conjunction precisely so this case cannot slip through as "looks untouched"."""
    record = new_record(kyo, criteria=["the retry backs off"])
    verify_criterion(kyo, f"{record}.c1", "unmet-unbuilt")
    assert _criteria(kyo, record)[0]["state"] == "unmet-unbuilt"

    result = kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("fix.txt", "the retry backs off exponentially\n"),
    )
    assert result.code == EXIT_CONTRACT, result
    assert contracts.CONTRACT_CRITERION_TEXT_IS_ATTESTED in result.err


def test_a_withdrawn_criterion_refuses_correction(kyo, write_file) -> None:
    """A void state writes no verification row, but it *is* a recorded judgment
    about this wording, with a required reason that reads as one."""
    record = new_record(kyo, criteria=["ship the migration"])
    assert kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "decided against it",
    ).code == 0
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("fix.txt", "ship the migration in two steps\n"),
    )
    assert result.code == EXIT_CONTRACT, result
    assert contracts.CONTRACT_CRITERION_TEXT_IS_ATTESTED in result.err


def test_the_refusal_clears_once_the_criterion_is_restated(kyo, write_file) -> None:
    """KYO-58.c1's last clause: the remedy the refusal names actually works, and
    leaves a criterion that is itself correctable again (it has no verdicts yet).

    This is the KYO-5 rule applied to a new error — an error that names a remedy
    must name one that clears it.
    """
    record = new_record(kyo, criteria=["the endpoint returns 200"])
    verify_criterion(kyo, f"{record}.c1", "met", method="machine-checked")
    blocked = kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("a.txt", "the endpoint returns 201\n"),
    )
    assert blocked.code == EXIT_CONTRACT

    restated = kyo(
        "restate", f"{record}.c1", "--text-file", write_file("b.txt", "returns 201\n"), "--json"
    )
    assert restated.code == 0, restated
    replacement = restated.json["replacement_id"]

    # the exact edit that was refused, now permitted on the replacement
    correction = kyo(
        "edit", record, "--criterion", replacement,
        "--text-file", write_file("c.txt", "the endpoint returns 201 on create\n"),
    )
    assert correction.code == 0, correction
    assert _criteria(kyo, record)[1]["text"] == "the endpoint returns 201 on create"


def test_correction_needs_a_criterion_and_refuses_an_empty_file(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("edit", record, "--text-file", write_file("x.txt", "b\n")).code == EXIT_USAGE
    assert kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "x", "--text-file", write_file("y.txt", "b\n"),
    ).code == EXIT_USAGE
    assert kyo(
        "edit", record, "--criterion", f"{record}.c1", "--text-file", write_file("z.txt", "\n")
    ).code == EXIT_USAGE


# ------------------------------------------- c4: the boundary, and what it costs


def test_a_criterion_already_quoted_into_a_frozen_brief_is_still_correctable(
    kyo, git_project, write_file
) -> None:
    """KYO-58.c4, decided: the window closes at the first VERDICT, not at "first
    quoted into a frozen brief".

    KYO-33.c2 had been dispatched and read by a worker before any verdict existed,
    so a never-dispatched boundary would have refused the one correction the
    mechanism exists for. What the brief case gets is loudness, not refusal — and
    the frozen brief still says exactly what it said, which is why nothing is lost.
    """
    record = new_record(kyo, criteria=["path.py:105 is flagged"])
    dispatched = kyo("--full", "dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    frozen = dispatched.json["record"]["dispatches"][0]["brief"]
    assert "path.py:105 is flagged" in frozen

    result = kyo(
        "edit", record, "--criterion", f"{record}.c1",
        "--text-file", write_file("fix.txt", "path.py:117 is flagged\n"),
    )
    assert result.code == 0, result
    assert dispatch_id in result.out, "the correction names the brief that quoted it"
    assert "frozen brief" in result.out

    after = kyo("show", record, "--json").json
    assert after["criteria"][0]["text"] == "path.py:117 is flagged"
    assert "path.py:105 is flagged" in after["dispatches"][0]["brief"], (
        "the frozen brief is unchanged: what the worker read stays recoverable"
    )


# ------------------------------------------------------------------ c2: restate


def test_restate_voids_and_adds_atomically_with_a_backlink(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["the stamp is a single value"])
    result = kyo(
        "restate", f"{record}.c1",
        "--text-file", write_file("new.txt", "the stamp is a list of promotions\n"),
        "--json",
    )
    assert result.code == 0, result
    replacement = result.json["replacement_id"]

    criteria = _criteria(kyo, record)
    assert [c["id"] for c in criteria] == [f"{record}.c1", replacement]
    assert criteria[0]["state"] == "void-withdrawn"
    assert criteria[0]["reason"] == contracts.restate_reason(replacement)
    assert criteria[1]["state"] == contracts.DEFAULT_CRITERION_STATE
    assert criteria[1]["text"] == "the stamp is a list of promotions"


def test_restate_leaves_the_original_s_verdict_history_attached_to_its_own_text(
    kyo, write_file
) -> None:
    """The conservative answer, and `kyo split`'s: a criterion with verdicts may be
    restated, because nothing about its history moves — the replacement starts
    unverified, so no evidence re-attaches to wording nobody checked."""
    record = new_record(kyo, criteria=["the endpoint returns 200"])
    verify_criterion(kyo, f"{record}.c1", "met", method="machine-checked")
    kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "returns 201\n"))

    original, replacement = _criteria(kyo, record)
    assert original["text"] == "the endpoint returns 200"
    assert [v["verdict"] for v in original["verifications"]] == ["met"]
    assert replacement["verifications"] == [], "the new wording carries no borrowed verdict"
    assert replacement["state"] == "unmet-unbuilt"


def test_restate_refuses_an_already_void_criterion(kyo, write_file) -> None:
    """Its ``reason`` holds the judgment that voided it, and that is the only live
    copy — a restatement would overwrite it with its own back-link."""
    record = new_record(kyo, criteria=["a"])
    kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", "the vendor dropped the endpoint",
    )
    result = kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "b\n"))
    assert result.code == EXIT_CONTRACT, result
    assert contracts.CONTRACT_RESTATE_REQUIRES_A_LIVE_CRITERION in result.err
    assert "--add-criteria-file" in result.err
    assert _criteria(kyo, record)[0]["reason"] == "the vendor dropped the endpoint"


def test_restate_of_an_unknown_criterion_changes_nothing(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("restate", f"{record}.c9", "--text-file", write_file("n.txt", "b\n"))
    assert result.code == EXIT_ERROR, result
    assert len(_criteria(kyo, record)) == 1


def test_doctor_passes_after_a_restatement(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "a, properly\n"))
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["restate_backlink_symmetry"]["failures"] == []


def test_doctor_catches_a_restatement_naming_a_criterion_that_does_not_exist(
    kyo, db, write_file
) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "a, properly\n"))
    conn = db()
    conn.execute(
        "UPDATE criteria SET reason = ? WHERE id = ?",
        (contracts.restate_reason(f"{record}.c99"), f"{record}.c1"),
    )
    failures = kyo("doctor", "--json").json["checks"]["restate_backlink_symmetry"]["failures"]
    assert any("does not exist" in f for f in failures), failures


def test_doctor_catches_a_restatement_pointing_at_another_record(kyo, db, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    other = new_record(kyo, criteria=["elsewhere"])
    kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "a, properly\n"))
    conn = db()
    conn.execute(
        "UPDATE criteria SET reason = ? WHERE id = ?",
        (contracts.restate_reason(f"{other}.c1"), f"{record}.c1"),
    )
    failures = kyo("doctor", "--json").json["checks"]["restate_backlink_symmetry"]["failures"]
    assert any("belongs to" in f for f in failures), failures


def test_doctor_catches_two_criteria_claiming_one_replacement(kyo, db, write_file) -> None:
    """The uniqueness clause is what makes the reverse direction derivable: a
    lookup that can return two answers is not a back-link."""
    record = new_record(kyo, criteria=["a", "b"])
    first = kyo(
        "restate", f"{record}.c1", "--text-file", write_file("n.txt", "a, properly\n"), "--json"
    )
    replacement = first.json["replacement_id"]
    conn = db()
    conn.execute(
        "UPDATE criteria SET state = 'void-withdrawn', reason = ? WHERE id = ?",
        (contracts.restate_reason(replacement), f"{record}.c2"),
    )
    failures = kyo("doctor", "--json").json["checks"]["restate_backlink_symmetry"]["failures"]
    assert any("both claim" in f for f in failures), failures


# ------------------------------------------- c5: derived, not a new stored state


def test_superseded_is_not_a_stored_criterion_state() -> None:
    """KYO-44's precedent, one level down: the reading is derived from two columns
    that already exist. A new member of ``CRITERION_STATES`` would have to be added
    to the report schema's verdict enum (a worker could then file it), to the compat
    machinery, and to every reader."""
    assert contracts.CRITERION_STANDING_SUPERSEDED not in contracts.CRITERION_STATES
    schema = report_schema.schema_document()  # KYO-149: the packaged copy
    enum = schema["$defs"]["criterionVerdict"]["properties"]["verdict"]["enum"]
    assert contracts.CRITERION_STANDING_SUPERSEDED not in enum


def test_the_export_stores_no_derived_supersession_field(kyo, write_file) -> None:
    """A derived field in the git-canonical export is a second copy of the same
    fact that a stale rebuild could contradict (KYO-44's stated reason for keeping
    ``standing`` out of ``kyosei/export/``)."""
    record = new_record(kyo, criteria=["a"])
    kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "a, properly\n"))
    doc = json.loads(export_of(kyo.project, record).read_text(encoding="utf-8"))
    assert set(doc["criteria"][0]) == {
        "id", "seq", "text", "state", "reason", "created_at", "updated_at", "verifications",
    }


def test_the_reading_is_derived_from_state_and_reason_alone() -> None:
    assert contracts.criterion_standing("void-withdrawn", "restated as TST-1.c4") == "superseded"
    assert contracts.criterion_standing("void-withdrawn", "we dropped it") == "void-withdrawn"
    # a live criterion whose reason happens to look like one is not superseded
    assert contracts.criterion_standing("unmet-unbuilt", "restated as TST-1.c4") == "unmet-unbuilt"
    assert contracts.replaced_by("void-withdrawn", "restated as TST-1.c4") == "TST-1.c4"
    assert contracts.replaced_by("void-premise-gone", "restated as TST-1.c4") is None


# ------------------------------------ c6: every surface that shows a criterion


def test_show_names_the_criterion_that_replaced_a_superseded_one(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["the stamp is a single value"])
    result = kyo(
        "restate", f"{record}.c1", "--text-file", write_file("n.txt", "a list\n"), "--json"
    )
    replacement = result.json["replacement_id"]

    shown = kyo("show", record).out
    assert f"superseded by {replacement}" in shown
    assert "void-withdrawn" in shown, "the stored state stays visible beside the reading"


def test_the_dispatch_brief_names_the_replacement_of_what_it_did_not_show(
    kyo, git_project, write_file
) -> None:
    record = new_record(kyo, criteria=["the stamp is a single value"])
    result = kyo(
        "restate", f"{record}.c1", "--text-file", write_file("n.txt", "a list\n"), "--json"
    )
    replacement = result.json["replacement_id"]
    brief = kyo("--full", "dispatch", record, "--json").json["record"]["dispatches"][0]["brief"]
    assert f"{record}.c1 (superseded by {replacement})" in brief


def test_kyo_list_shows_no_criterion_state_at_all(kyo, write_file) -> None:
    """KYO-58.c6 names `kyo list` among the surfaces where a criterion's state is
    shown to a human. It is not one: `kyo list` renders one line per RECORD (id,
    kind, standing, open gates, title) and no criterion appears in it. Asserted
    rather than left as prose, so that if `kyo list` ever grows criteria this test
    fails and c6's obligation is picked up rather than quietly missed.
    """
    record = new_record(kyo, criteria=["the stamp is a single value"])
    kyo("restate", f"{record}.c1", "--text-file", write_file("n.txt", "a list\n"))
    listed = kyo("list", "--json")
    assert listed.code == 0
    assert all("criteria" not in item for item in listed.json["records"])
    assert f"{record}.c1" not in kyo("list").out


# ----------------------------------------- c3: briefs render live criteria only


def _brief_for(kyo, record: str, *args: str) -> str:
    result = kyo("--full", "dispatch", record, *args, "--json")
    assert result.code == 0, result
    return result.json["record"]["dispatches"][0]["brief"]


def test_a_brief_renders_live_criteria_and_counts_the_rest(
    kyo, git_project, write_file
) -> None:
    """The KYO-30 shape: five criteria, two dead. A worker dispatched today read all
    five, and brief length is a measured constraint."""
    record = new_record(kyo, criteria=["stale one", "stale two", "the live one"])
    kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", "the design decided otherwise",
    )
    kyo("restate", f"{record}.c2", "--text-file", write_file("n.txt", "the restated one\n"))

    brief = _brief_for(kyo, record)
    assert "the live one" in brief
    assert "the restated one" in brief
    assert "stale one" not in brief
    assert "stale two" not in brief
    assert "2 live criteria · 2 void, not shown" in brief
    assert f"{record}.c1 (void-premise-gone)" in brief
    assert f"{record}.c2 (superseded by {record}.c4)" in brief


def test_a_brief_states_the_count_even_when_nothing_was_withheld(kyo, git_project) -> None:
    """Law 6: "no void criteria" and "void criteria you were not shown" must never
    render as the same thing, which is what silent filtering would make them."""
    record = new_record(kyo, criteria=["the only one"])
    brief = _brief_for(kyo, record)
    assert "1 live criteria · 0 void" in brief


def test_the_filter_applies_to_covered_records_too(kyo, git_project, write_file) -> None:
    """A covered record's criteria are ones the worker may file verdicts against,
    so they get the same treatment as the primary record's — one function renders
    both tables, and this is the test that keeps it that way."""
    primary = new_record(kyo, title="Primary", criteria=["primary live"])
    covered = new_record(kyo, title="Covered", criteria=["covered dead", "covered live"])
    kyo(
        "edit", covered, "--criterion", f"{covered}.c1", "--state", "void-withdrawn",
        "--why", "dropped",
    )
    brief = _brief_for(kyo, primary, "--also-covers", covered)
    assert "covered live" in brief
    assert "covered dead" not in brief
    assert f"1 void, not shown: {covered}.c1 (void-withdrawn)" in brief


def test_a_record_whose_criteria_are_all_dead_says_so(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["dropped"])
    kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "dropped",
    )
    # KYO-295: a record whose every criterion is settled -- and void IS settled -- is
    # one `kyo next` withholds as finished, so `kyo dispatch` now refuses it off the
    # same function. That is right for this record too: nothing is left to build, and
    # what it needs is a retirement, not a worker. The brief's wording for the case is
    # still the subject here, and `--force` is the one road left to a brief that says
    # it, so the refusal is asserted first and then overridden on the record.
    refused = kyo("dispatch", record, "--json")
    assert refused.code == 2
    assert "dispatch-record-finished" in refused.out
    brief = _brief_for(kyo, record, "--force")
    assert "(no live criteria)" in brief
    assert "0 live criteria · 1 void, not shown" in brief
