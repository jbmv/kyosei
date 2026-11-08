"""KYO-647: credits in ``kyo show``, ``kyo list``, the export and the changelog.

Credits are written through :meth:`kyosei.store.Store.add_credit` directly: the commands
that write them are KYO-644's. Every assertion reads a command's output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import buildinfo
from kyosei.export import write_export
from kyosei.store import open_store

from .conftest import CREDIT_ELSEWHERE, invoke, new_record

SESSION = "7212f0bb-a568-49c4-9b87-d3209a201b6b"
TURN = "e4060c50-1111-2222-3333-444455556666"
WORDS = "the filter works on the laptop too"


@pytest.fixture(autouse=True)
def people(project: Path) -> None:
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + "people:\n  jbmv: {}\n  ada:\n    name: Ada L\n",
        encoding="utf-8",
    )


def _verified(kyo, project: Path, *, words: str = WORDS, record: str | None = None):
    """A record with one met verdict whose evidence holds ``words``; returns
    ``(record, verification id, evidence id)``."""
    record = record or new_record(kyo, criteria=["a"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about",
        "--evidence-file", "-", stdin=words,
    )  # fmt: skip
    assert result.code == 0, result
    store = open_store(project)
    try:
        evidence = store.evidence_artifacts(record)[-1]["id"]
        verification = store.verifications_for_criterion(f"{record}.c1")[-1]["id"]
    finally:
        store.close()
    return record, verification, evidence


def _credit(project: Path, record: str, quote: str, subjects, *, late: bool = False, **fields):
    defaults = {
        "person": "jbmv", "role": "observed", "source_kind": "claude-code",
        "source_session": SESSION, "source_turn": TURN, "transcript_check": "matched",
        "observed_at": "2026-11-07T10:01:48Z", "author": "orchestrator",
    }  # fmt: skip
    defaults.update(fields)
    store = open_store(project)
    try:
        with store.transaction():
            credit_id = store.add_credit(record, quote_id=quote, subjects=subjects, **defaults)
            # A real writer stamps a live credit with its subject's own time (KYO-644). This
            # fixture writes the credit after the subject, so it is stamped explicitly: early
            # for a live credit, late for a reconstructed one. Without the early stamp the
            # credit reads as live only while today is before the fixed `observed_at` above.
            stamp = "2099-01-01T00:00:00.000Z" if late else "2000-01-01T00:00:00.000Z"
            store.conn.execute(
                "UPDATE credits SET created_at = ? WHERE id = ?", (stamp, credit_id)
            )
        write_export(store, record)
    finally:
        store.close()
    return credit_id


def test_a_credited_verification_shows_who_observed_and_the_transcript_check(
    kyo, project: Path
) -> None:
    record, verification, evidence = _verified(kyo, project)
    credit = _credit(project, record, evidence, [("verification", verification)])
    text = kyo("show", record).out
    assert f"observed by jbmv (credit {credit}): \"{WORDS}\"" in text
    assert "session 7212f0bb, turn e4060c50, 2026-11-07T10:01:48Z, transcript matched" in text
    assert "reconstructed" not in text
    # the row's author is still the agent that wrote it
    assert f"{verification}  met  reasoned-about  orchestrator" in text


def test_a_reconstructed_credit_with_an_absent_transcript_says_so(kyo, project: Path) -> None:
    record, verification, evidence = _verified(kyo, project)
    basis = _credit(
        project, record, evidence, [("record", record)], role="decided", outcome="accepted"
    )
    credit = _credit(
        project, record, evidence, [("verification", verification)], late=True,
        transcript_check="transcript-absent", source_session=None, source_turn=None,
        observed_at=None, source_kind="other", source_note="said in chat", basis_id=basis,
    )  # fmt: skip
    text = kyo("show", record).out
    assert f"(credit {credit}, reconstructed 2099-01-01, approved in {basis})" in text
    assert "said in chat, transcript absent" in text
    document = kyo("show", record, "--json").json
    by_id = {c["id"]: c for c in document["credits"]}
    assert by_id[credit]["reconstructed"] is True
    assert by_id[basis]["reconstructed"] is False


def test_show_json_and_the_export_carry_person_role_outcome_and_transcript_check(
    kyo, project: Path
) -> None:
    decision = new_record(kyo, kind="decision", title="Pick a thing", criteria=["a"])
    _, _, evidence = _verified(kyo, project, record=decision)
    credit = _credit(
        project, decision, evidence, [("record", decision)], role="decided", person="ada",
        outcome="accepted", recommended="schema fields", recommended_by="orchestrator",
        alternatives=[{"text": "notes"}],
    )  # fmt: skip
    shown = kyo("show", decision, "--json").json
    [c] = [c for c in shown["credits"] if c["id"] == credit]
    assert (c["person"], c["role"], c["outcome"], c["transcript_check"]) == (
        "ada", "decided", "accepted", "matched",
    )  # fmt: skip
    assert c["person_name"] == "Ada L"
    assert c["reconstructed"] is False
    exported = json.loads((project / "kyosei" / "export" / f"{decision}.json").read_text())
    [e] = [c for c in exported["credits"] if c["id"] == credit]
    assert (e["person"], e["role"], e["outcome"], e["transcript_check"]) == (
        "ada", "decided", "accepted", "matched",
    )  # fmt: skip
    # derived fields are the show document's alone
    assert "reconstructed" not in e and "person_name" not in e
    verification = shown["criteria"][0]["verifications"][0]
    assert verification["credit"] is None
    assert "credit" not in exported["criteria"][0]["verifications"][0]


def test_show_json_names_the_credit_on_each_verification(kyo, project: Path) -> None:
    record, verification, evidence = _verified(kyo, project)
    credit = _credit(project, record, evidence, [("verification", verification)])
    [v] = kyo("show", record, "--json").json["criteria"][0]["verifications"]
    assert v["credit"] == credit


def test_a_decision_header_names_who_decided_what_was_recommended_and_the_depth(
    kyo, project: Path
) -> None:
    decision = new_record(kyo, kind="decision", title="Pick a thing", criteria=["a"])
    _, _, evidence = _verified(kyo, project, record=decision)
    credit = _credit(
        project, decision, evidence, [("record", decision)], role="decided",
        outcome="refined", refining_session=SESSION, refining_turn="aaaabbbb-0000",
        recommended="schema fields, not notes", recommended_by="orchestrator",
        alternatives=[
            {"text": "schema fields with a consumer", "chosen": True},
            {"text": "credits written as notes"},
        ],
        span_session=SESSION, span_first_turn="t1", span_last_turn="t9",
        span_person_turns=14, span_agent_turns=40, span_active_seconds=2160,
        span_started_at="2026-11-06T10:00:00Z", span_ended_at="2026-11-06T12:00:00Z",
    )  # fmt: skip
    text = kyo("show", decision).out
    assert f"decided by:   jbmv (credit {credit}, transcript matched)" in text
    assert (
        "recommended:  schema fields, not notes  (orchestrator) — "
        "refined (turn aaaabbbb, session 7212f0bb)"
    ) in text
    assert (
        "alternatives: schema fields with a consumer [chosen]; credits written as notes"
    ) in text
    # depth is message and reply counts and active time; the wall-clock span is not effort
    assert (
        "discussion:   14 of your messages, 40 replies, 36m active, session 7212f0bb" in text
    )
    assert "2h" not in text


def test_an_accepted_suggestion_reads_as_the_persons_choice_not_their_writing(
    kyo, project: Path
) -> None:
    record, verification, evidence = _verified(kyo, project, words="yes, ship it")
    _credit(
        project, record, evidence, [("verification", verification)],
        words_form="accepted-suggestion",
    )  # fmt: skip
    text = kyo("show", record).out
    assert "accepted a suggestion (their choice, not their writing): \"yes, ship it\"" in text
    other, other_v, other_e = _verified(kyo, project, words="typed words")
    _credit(project, other, other_e, [("verification", other_v)], words_form="typed")
    assert "not their writing" not in kyo("show", other).out


def test_a_gate_cleared_by_an_answer_and_one_withdrawn_read_differently(
    kyo, project: Path
) -> None:
    record, _, evidence = _verified(kyo, project)
    for _ in range(2):
        assert kyo("gate", record, "awaiting:human-review", "--do", "look at it").code == 0
    store = open_store(project)
    try:
        first, second = [g["id"] for g in store.conn.execute("SELECT id FROM gates")]
    finally:
        store.close()
    # KYO-644: cleared through the command, then given the shape each clearing reads as.
    assert kyo("ungate", first, "--withdrawn", "--why", "the person said yes").code == 0
    assert kyo("ungate", second, "--withdrawn", "--why", "no longer matters").code == 0
    store = open_store(project)
    try:
        with store.transaction():
            store.conn.execute("UPDATE gates SET cleared_as='answered' WHERE id = ?", (first,))
            store.conn.execute("UPDATE gates SET cleared_as='withdrawn' WHERE id = ?", (second,))
    finally:
        store.close()
    credit = _credit(project, record, evidence, [("gate", first)])
    text = kyo("show", record).out
    assert f"cleared: answered by jbmv (credit {credit}" in text
    assert "cleared: withdrawn — no longer matters" in text
    gates = {g["id"]: g for g in kyo("show", record, "--json").json["gates"]}
    assert gates[first]["credit"] == credit and gates[first]["cleared_as"] == "answered"
    assert gates[second]["credit"] is None


def test_a_credited_verdict_that_reverses_an_agents_names_what_it_overrides(
    kyo, project: Path
) -> None:
    record, agent_v, _ = _verified(kyo, project)
    # KYO-644: the human-observed verdict is credited by the command that records it.
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "human-observed",
        "--evidence-file", "-", "--person", "jbmv", *CREDIT_ELSEWHERE, stdin="it is not there",
    )  # fmt: skip
    assert result.code == 0, result
    text = kyo("show", record).out
    assert f"overrides {agent_v} (met, reasoned-about, orchestrator)" in text
    verifications = kyo("show", record, "--json").json["criteria"][0]["verifications"]
    assert verifications[1]["overrides"]["id"] == agent_v
    assert verifications[0]["overrides"] is None


def test_list_filters_return_the_records_they_name(kyo, project: Path) -> None:
    observed, verification, evidence = _verified(kyo, project)
    _credit(project, observed, evidence, [("verification", verification)])
    decided = new_record(kyo, kind="decision", title="Pick a thing", criteria=["a"])
    _, _, d_evidence = _verified(kyo, project, record=decided)
    _credit(
        project, decided, d_evidence, [("record", decided)], role="decided", person="ada",
        outcome="overrode",
    )  # fmt: skip
    bare = new_record(kyo, criteria=["a"], title="uncredited")

    def ids(*args: str) -> set[str]:
        result = kyo("list", *args, "--json")
        assert result.code == 0, result
        return {r["id"] for r in result.json["records"]}

    assert ids("--person", "jbmv") == {observed}
    assert ids("--person", "ada") == {decided}
    assert ids("--decided-by", "ada") == {decided}
    assert ids("--decided-by", "jbmv") == set()
    assert ids("--overrode") == {decided}
    assert bare not in ids("--person", "jbmv")
    rows = {r["id"]: r for r in kyo("list", "--json").json["records"]}
    assert rows[observed]["people"] == ["jbmv"] and rows[bare]["people"] == []


def test_a_verdict_override_is_found_by_overrode(kyo, project: Path) -> None:
    record, _, _ = _verified(kyo, project)
    # An agent's own reversal credits nobody, and is not a person overriding anything.
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "agent-observed",
    ).code == 0  # fmt: skip
    assert kyo("list", "--overrode", "--json").json["records"] == []
    # KYO-644: a person's reversal is credited by the command that records it.
    assert kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about").code == 0
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "human-observed",
        "--evidence-file", "-", "--person", "jbmv", *CREDIT_ELSEWHERE, stdin="not there",
    ).code == 0  # fmt: skip
    assert [r["id"] for r in kyo("list", "--overrode", "--json").json["records"]] == [record]


def test_an_undeclared_person_is_refused_rather_than_answered_with_nothing(
    kyo, project: Path
) -> None:
    for flag in ("--person", "--decided-by"):
        result = kyo("list", flag, "nobody")
        assert result.code != 0
        assert "'nobody'" in result.err + result.out
        assert "declared: jbmv, ada" in result.err + result.out


def test_a_base_can_filter_and_show_people(kyo, project: Path) -> None:
    record, verification, evidence = _verified(kyo, project)
    _credit(project, record, evidence, [("verification", verification)])
    (project / "kyosei" / "bases.yml").write_text(
        "bases:\n  by-jbmv:\n    summary: credited to jbmv\n    source: records\n"
        "    filters:\n      person: jbmv\n    columns: [id, people]\n",
        encoding="utf-8",
    )
    result = kyo("base", "run", "by-jbmv", "--json")
    assert result.code == 0, result
    assert [r["id"] for r in result.json["rows"]] == [record]


def test_the_changelog_names_who_decided_a_completed_record(
    kyo, project: Path, monkeypatch
) -> None:
    """The changelog lists completed records only, and a decision (knowledge archetype)
    has no status flow, so the credit shows on a work record that carries a ``decided``
    credit. A record without one prints exactly what it did before."""
    from kyosei import changelog

    def finished(title: str) -> str:
        record = new_record(kyo, criteria=["a"], title=title)
        for status in ("working", "built"):
            assert kyo("move", record, status).code == 0
        verified = kyo(
            "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
            "--evidence-file", "-", "--person", "jbmv", *CREDIT_ELSEWHERE, stdin="it works",
        )  # fmt: skip
        assert verified.code == 0, verified
        for status in ("verified", "done"):
            assert kyo("move", record, status).code == 0
        return record

    decided = finished("decided work")
    plain = finished("plain work")
    assert kyo("attach", decided, "--file", "-", stdin="Approved in chat.").code == 0
    store = open_store(project)
    try:
        quote = store.evidence_artifacts(decided)[-1]["id"]
    finally:
        store.close()
    _credit(
        project, decided, quote, [("record", decided)], role="decided", person="ada",
        outcome="accepted",
    )  # fmt: skip
    monkeypatch.setattr(buildinfo, "source_tree_root", lambda *a, **k: project)

    by_id = {e.id: e for e in changelog.entries_from_store(project)}
    assert by_id[decided].decided_by == ("Ada L",)
    assert by_id[plain].decided_by == ()
    text = invoke(["changelog", "--all"]).out
    [line] = [ln for ln in text.splitlines() if decided in ln]
    assert line.endswith("decided work — decided by Ada L")
    [plain_line] = [ln for ln in text.splitlines() if plain in ln]
    assert "decided by" not in plain_line
    payload = json.loads(invoke(["changelog", "--all", "--json"]).out)
    data = {e["id"]: e for e in payload["entries"]}
    assert data[decided]["decided_by"] == ["Ada L"] and "decided_by" not in data[plain]
    # the packaged shape round-trips and the markdown carries it
    entry = changelog.Entry.from_data(data[decided])
    assert entry.decided_by == ("Ada L",)
    loaded = changelog.Changelog(entries=[entry], source=changelog.SOURCE_STORE)
    assert "— decided by Ada L" in changelog.markdown(loaded, [entry], None)


def test_an_authorized_credit_is_never_shown_or_found_as_decided(kyo, project: Path) -> None:
    """KYO-647.c6. KYO-644 added ``authorized`` to the vocabulary: a go-ahead carries no
    outcome, recommendation or alternatives."""
    decision = new_record(kyo, kind="decision", title="Go ahead", criteria=["a"])
    # KYO-644: filing wrote the decision's own decided credit; this test is about a record
    # whose only credit is a go-ahead, so that one is removed.
    store = open_store(project)
    try:
        with store.transaction():
            store.conn.execute("DELETE FROM credit_subjects")
            store.conn.execute("DELETE FROM credits")
    finally:
        store.close()
    _, _, evidence = _verified(kyo, project, record=decision)
    credit = _credit(
        project, decision, evidence, [("record", decision)], role="authorized",
        span_session=SESSION, span_first_turn="t1", span_last_turn="t2",
        span_person_turns=1, span_agent_turns=3, span_active_seconds=45,
        span_started_at="2026-11-06T10:00:00Z", span_ended_at="2026-11-06T10:01:00Z",
    )  # fmt: skip
    text = kyo("show", decision).out
    assert f"authorized by: jbmv (credit {credit}, transcript matched)" in text
    assert "decided by" not in text
    assert "discussion:   1 of your messages, 3 replies, 45s active" in text
    assert kyo("list", "--decided-by", "jbmv", "--json").json["records"] == []
    assert [r["id"] for r in kyo("list", "--person", "jbmv", "--json").json["records"]] == [
        decision
    ]


# ------------------------------------------------------------------ KYO-653.c3


def _doc_with_credit(**credit) -> tuple[dict, dict]:
    doc = {"created_at": "2026-11-06T09:00:00.000Z", "gates": [], "criteria": []}
    base = {
        "created_at": "2026-11-06T09:00:00.000Z",
        "basis_id": None,
        "observed_at": None,
        "subjects": [{"kind": "record", "id": "X-1"}],
    }
    return doc, {**base, **credit}


def test_a_credit_one_second_after_its_subject_is_reconstructed() -> None:
    """KYO-653.c3: there is no tolerance; writers stamp the subject's own time."""
    from kyosei import render

    assert not hasattr(render, "RECONSTRUCTED_WINDOW_SECONDS")
    doc, same = _doc_with_credit()
    assert render.credit_reconstructed(doc, same) is False
    doc, late = _doc_with_credit(created_at="2026-11-06T09:00:01.000Z")
    assert render.credit_reconstructed(doc, late) is True


def test_a_go_ahead_given_after_its_subject_is_not_reconstructed_unless_it_has_a_basis() -> None:
    """KYO-653.c3: matched turn later than the subject and no basis (written from the
    session that holds the turn) is live; the same credit resting on a basis is not."""
    from kyosei import render

    later = {"created_at": "2026-11-06T10:05:00.000Z", "observed_at": "2026-11-06T10:01:00.000Z"}
    doc, live = _doc_with_credit(**later)
    assert render.credit_reconstructed(doc, live) is False
    doc, based = _doc_with_credit(basis_id="X-1.p1", **later)
    assert render.credit_reconstructed(doc, based) is True
    doc, before = _doc_with_credit(
        created_at="2026-11-06T10:05:00.000Z", observed_at="2026-11-06T08:00:00.000Z"
    )
    assert render.credit_reconstructed(doc, before) is True
