"""KYO-670: ``kyo credit --withdraw`` retracts a credit that should not exist.

A withdrawal keeps the credit and its words readable, records who withdrew it, when and
why, and removes it from the current credits as a supersession does, with nothing in its
place. Every assertion reads a command's output or the store the command wrote.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kyosei import changelog

from .conftest import CREDIT_ELSEWHERE, DECISION_FILE, TEST_PERSON, declare_person, new_record
from .test_credit_coverage import LEGACY, NEW, _doctor, _restamp

WORDS = "the filter works on the laptop too"
WHY = "quotes a process correction, not an override"


def _credited_verdict(kyo) -> tuple[str, str, str]:
    """A human-observed verdict credited by the command that records it."""
    record = new_record(kyo, criteria=["a"])
    declare_person(kyo.project)
    made = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
        "--evidence-file", "-", *CREDIT_ELSEWHERE, "--json", stdin=WORDS,
    )  # fmt: skip
    assert made.code == 0, made
    return record, made.json["verification_id"], made.json["credit_id"]


def _overrode_decision(kyo, tmp_path: Path) -> tuple[str, str]:
    """A decision record whose filing credit says the person overrode a recommendation."""
    declare_person(kyo.project)
    decision_file = tmp_path / "d.yml"
    decision_file.write_text(
        "outcome: overrode\nrecommended: adopt A\nrecommended_by: orchestrator\n",
        encoding="utf-8",
    )
    quote = tmp_path / "q.txt"
    quote.write_text("no, B", encoding="utf-8")
    made = kyo(
        "new", "decision", "--title", "Pick a thing", "--quote-file", str(quote),
        "--decision-file", str(decision_file), *CREDIT_ELSEWHERE, "--json",
    )  # fmt: skip
    assert made.code == 0, made
    return made.json["id"], made.json["credit_id"]


# ------------------------------------------------------------------------- c1


def test_withdraw_marks_the_credit_and_show_renders_it_withdrawn(kyo, project: Path) -> None:
    record, verification, credit = _credited_verdict(kyo)
    done = kyo("credit", "--withdraw", credit, "--why", WHY, "--json")
    assert done.code == 0, done
    assert done.json["withdrawn"] == credit and done.json["subjects"] == [verification]

    shown = kyo("show", record, "--json").json
    (row,) = shown["credits"]
    assert row["id"] == credit
    assert row["withdrawn_why"] == WHY
    assert row["withdrawn_at"] and row["withdrawn_by"]
    assert row["superseded_by"] is None
    # The quote stays in the store, unredacted.
    quote = next(e for e in shown["evidence_artifacts"] if e["id"] == row["quote_id"])
    assert quote["text"] == WORDS and quote["redacted_at"] is None
    # No verification names it as its credit any more.
    assert shown["criteria"][0]["verifications"][0]["credit"] is None

    text = kyo("show", record).out
    assert "withdrawn credits (1):" in text
    assert f"{credit}  observed by {TEST_PERSON} ({verification})" in text
    assert f'"{WORDS}"' in text
    assert f"by {row['withdrawn_by']}: {WHY}" in text
    # Not shown as the verdict's credit.
    assert f"observed by {TEST_PERSON} (credit {credit}" not in text


def test_withdrawal_round_trips_through_export_and_rebuild(kyo, project: Path) -> None:
    record, _verification, credit = _credited_verdict(kyo)
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    export_path = project / "kyosei" / "export" / f"{record}.json"
    before = json.loads(export_path.read_text(encoding="utf-8"))["credits"]
    assert before[0]["withdrawn_why"] == WHY
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert json.loads(export_path.read_text(encoding="utf-8"))["credits"] == before


def test_a_record_with_no_withdrawn_credit_shows_no_block(kyo, project: Path) -> None:
    record, _verification, _credit = _credited_verdict(kyo)
    assert "withdrawn credits" not in kyo("show", record).out


def test_a_withdrawn_subject_can_be_credited_again_without_supersede(
    kyo, project: Path, tmp_path: Path
) -> None:
    """The withdrawn credit no longer blocks a new one on the same row."""
    record, verification, credit = _credited_verdict(kyo)
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    decision = tmp_path / "d.yml"
    decision.write_text(DECISION_FILE, encoding="utf-8")
    approved = tmp_path / "a.txt"
    approved.write_text("approved", encoding="utf-8")
    approval = kyo(
        "new", "decision", "--title", "Credit it again", "--quote-file", str(approved),
        "--decision-file", str(decision), *CREDIT_ELSEWHERE, "--json",
    )  # fmt: skip
    assert approval.code == 0, approval
    quote = tmp_path / "w.txt"
    quote.write_text("it is fast", encoding="utf-8")
    again = kyo(
        "credit", verification, "--quote-file", str(quote), *CREDIT_ELSEWHERE,
        "--basis", approval.json["credit_id"], "--json",
    )  # fmt: skip
    assert again.code == 0, again
    shown = kyo("show", record, "--json").json
    assert shown["criteria"][0]["verifications"][0]["credit"] == again.json["credit_id"]


# ------------------------------------------------------------------------- c2


def test_a_withdrawn_credit_counts_in_no_list_filter(kyo, project: Path, tmp_path: Path) -> None:
    decision, credit = _overrode_decision(kyo, tmp_path)

    def ids(*args: str) -> set[str]:
        result = kyo("list", *args, "--json")
        assert result.code == 0, result
        return {r["id"] for r in result.json["records"]}

    assert decision in ids("--overrode")
    assert decision in ids("--person", TEST_PERSON)
    assert decision in ids("--decided-by", TEST_PERSON)
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    assert decision not in ids("--overrode")
    assert decision not in ids("--person", TEST_PERSON)
    assert decision not in ids("--decided-by", TEST_PERSON)
    rows = {r["id"]: r for r in kyo("list", "--json").json["records"]}
    assert rows[decision]["people"] == []
    # Nor in the changelog's "decided by", nor in the decision header kyo show prints.
    document = kyo("show", decision, "--json").json
    assert changelog._decided_by(document, {}) == ()
    assert "decided by:" not in kyo("show", decision).out


def test_a_withdrawn_credit_does_not_cover_its_row_and_the_legacy_notice_counts_it(
    kyo, project: Path
) -> None:
    """Before schema 28: the row is in the legacy notice again, named as withdrawn."""
    record, verification, credit = _credited_verdict(kyo)
    _restamp(project, 27)
    result, checks = _doctor(kyo)
    assert checks[LEGACY]["ok"] and checks[NEW]["ok"], checks
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    result, checks = _doctor(kyo)
    assert result.code == 0, result
    assert checks[NEW]["ok"]
    text = " ".join(checks[LEGACY]["failures"])
    assert "1 human-observed verdicts" in text
    assert record in text
    named = f"1 of them had their credit withdrawn with `kyo credit --withdraw` ({verification})"
    assert named in text


def test_a_row_uncredited_by_withdrawal_at_schema_28_is_a_notice_not_a_problem(
    kyo, project: Path
) -> None:
    """A withdrawal is a command, not a bypass of enforcement, so a row it uncredits is
    the legacy notice's even when it was written at schema 28 or later."""
    _record, verification, credit = _credited_verdict(kyo)
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    result, checks = _doctor(kyo)
    assert result.code == 0, result
    assert checks[NEW]["ok"], checks[NEW]
    assert verification in " ".join(checks[LEGACY]["failures"])


def test_a_superseding_credit_withdrawn_uncovers_the_row_too(kyo, project: Path) -> None:
    """Coverage reads only a current credit: a row whose credit was superseded and whose
    superseding credit was then withdrawn has no credit left."""
    from .test_credit_coverage import _credit

    record, verification, first = _credited_verdict(kyo)
    second = _credit(project, record, [("verification", verification)])
    from kyosei.store import open_store

    store = open_store(project)
    try:
        with store.transaction():
            store.supersede_credit(first, second)
        from kyosei.export import write_export

        write_export(store, record)
    finally:
        store.close()
    assert kyo("credit", "--withdraw", second, "--why", WHY).code == 0
    _, checks = _doctor(kyo)
    assert verification in " ".join(checks[LEGACY]["failures"])


def test_a_withdrawn_transcript_absent_credit_is_not_noticed(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    from .test_credit_coverage import _credit, _human_verdict

    record, verdict = _human_verdict(kyo)
    session = "7212f0bb-a568-49c4-9b87-d3209a201b6b"
    credit = _credit(
        project, record, [("verification", verdict)], transcript_check="transcript-absent"
    )
    config = tmp_path / "claude-config"
    transcript = config / "projects" / "-a-project" / f"{session}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    _, checks = _doctor(kyo)
    assert credit in str(checks["credit_transcripts_appeared"])
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    _, checks = _doctor(kyo)
    assert checks["credit_transcripts_appeared"]["ok"], checks["credit_transcripts_appeared"]


def test_a_withdrawn_credit_is_no_basis_and_cannot_be_rechecked_or_superseded(
    kyo, project: Path, tmp_path: Path
) -> None:
    decision, credit = _overrode_decision(kyo, tmp_path)
    assert kyo("credit", "--withdraw", credit, "--why", WHY).code == 0
    record = new_record(kyo, criteria=["a"])
    verification = kyo("verify", f"{record}.c1", "--verdict", "met", "--method",
                       "agent-observed", "--json").json["verification_id"]  # fmt: skip
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.execute("UPDATE verifications SET method = 'human-observed' WHERE id = ?", (verification,))
    conn.commit()
    conn.close()
    quote = tmp_path / "w.txt"
    quote.write_text("it is fast", encoding="utf-8")
    refused = kyo("credit", verification, "--quote-file", str(quote), *CREDIT_ELSEWHERE,
                  "--basis", credit)  # fmt: skip
    assert refused.code != 0 and "a withdrawn decided credit" in refused.err
    rechecked = kyo("credit", "--recheck", credit)
    assert rechecked.code != 0 and "it was withdrawn" in rechecked.err
    again = kyo("credit", "--withdraw", credit, "--why", WHY)
    assert again.code != 0 and "already withdrawn" in again.err
    assert "nothing was written" in again.err
