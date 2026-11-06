"""Credits at the enforcement points, and `kyo credit` (KYO-644).

The transcripts written here copy the shapes :mod:`tests.test_transcript_check` documents;
the words are invented.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from kyosei.contracts import REDACTION_MARKER
from kyosei.harness import claude_code_transcript as tr

from .conftest import (
    CREDIT_ELSEWHERE,
    TEST_PERSON,
    declare_person,
    new_record,
)

SESSION = "7c1d2e3f-1111-4a2b-9c3d-0123456789ab"


# ------------------------------------------------------------------ transcript fixtures


def _record(kind: str, uuid: str, stamp: str, **extra) -> dict:
    return {
        "type": kind,
        "uuid": uuid,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        **extra,
    }


def person(uuid: str, text: str, stamp: str, source: str = "typed") -> dict:
    return _record(
        "user",
        uuid,
        stamp,
        message={"role": "user", "content": text},
        origin={"kind": "human"},
        promptSource=source,
    )


def agent_text(uuid: str, text: str, stamp: str) -> dict:
    return _record(
        "assistant", uuid, stamp, message={"role": "assistant", "content": [{"type": "text", "text": text}]}
    )


def agent_tool(uuid: str, stamp: str) -> dict:
    return _record(
        "assistant",
        uuid,
        stamp,
        message={"role": "assistant", "content": [{"type": "tool_use", "id": f"t-{uuid}", "name": "Bash"}]},
    )


def tool_result(uuid: str, stamp: str) -> dict:
    return _record(
        "user",
        uuid,
        stamp,
        message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        toolUseResult={"stdout": "ok"},
    )


def write_transcript(records: list[dict], session: str = SESSION) -> Path:
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    path = config / "projects" / "-a-project" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


CONVERSATION = [
    person("p-1", "should the cache stay?", "2026-11-06T10:00:00.000Z"),
    agent_text("a-1", "I recommend keeping it.", "2026-11-06T10:00:30.000Z"),
    person("p-2", "keep the cache, it is fast enough", "2026-11-06T10:01:00.000Z"),
    agent_tool("a-2", "2026-11-06T10:01:10.000Z"),
    tool_result("t-2", "2026-11-06T10:01:20.000Z"),
    agent_text("a-3", "Done, the banner shows.", "2026-11-06T10:01:40.000Z"),
    person("p-3", "yes", "2026-11-06T10:02:00.000Z"),
    person("p-4", "the banner looks right", "2026-11-06T11:00:00.000Z", source="suggestion_accepted"),
    agent_text("a-4", "Noted.", "2026-11-06T11:00:10.000Z"),
    person("p-5", "yes", "2026-11-06T11:01:00.000Z"),
]


def _file(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def _db(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.row_factory = sqlite3.Row
    return conn


def _credits(project: Path) -> list[sqlite3.Row]:
    with _db(project) as conn:
        return list(conn.execute("SELECT * FROM credits ORDER BY created_at, id"))


def _subjects(project: Path, credit_id: str) -> list[tuple[str, str]]:
    with _db(project) as conn:
        return [
            (r["subject_kind"], r["subject_id"])
            for r in conn.execute(
                "SELECT * FROM credit_subjects WHERE credit_id = ? ORDER BY rowid", (credit_id,)
            )
        ]


# ------------------------------------------------------------------ kyo verify


def test_a_human_observed_verdict_is_refused_without_a_credit(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    words = _file(tmp_path / "w.txt", "it works on the laptop")
    refused = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
                  "--evidence-file", words)  # fmt: skip
    assert refused.code == 2, refused
    assert "human-observed-requires-credit" in refused.err
    assert _credits(project) == []
    ok = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
             "--evidence-file", words, *CREDIT_ELSEWHERE, "--json")  # fmt: skip
    assert ok.code == 0, ok
    (credit,) = _credits(project)
    assert credit["person"] == TEST_PERSON and credit["role"] == "observed"
    assert credit["transcript_check"] == "not-applicable"
    assert credit["source_note"] == "the test suite"
    assert _subjects(project, credit["id"]) == [("verification", ok.json["verification_id"])]
    with _db(project) as conn:
        verification = conn.execute(
            "SELECT created_at, evidence_id FROM verifications WHERE id = ?",
            (ok.json["verification_id"],),
        ).fetchone()
    # Written in the verdict's own transaction, with its stamp, so it is not reconstructed.
    assert credit["created_at"] == verification["created_at"]
    assert credit["quote_id"] == verification["evidence_id"]


def test_the_only_declared_person_is_the_default_and_is_echoed(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project, "jbmv")
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
              "--evidence-file", _file(tmp_path / "w", "fine"), *CREDIT_ELSEWHERE)  # fmt: skip
    assert ran.code == 0, ran
    assert "observed by jbmv" in ran.out


def test_with_two_people_declared_the_person_must_be_named(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project, "ada")
    declare_person(project, "bo")
    words = _file(tmp_path / "w", "fine")
    base = ("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
            "--evidence-file", words, *CREDIT_ELSEWHERE)  # fmt: skip
    refused = kyo(*base)
    assert refused.code == 2 and "the person" in refused.err, refused
    assert kyo(*base, "--person", "bo").code == 0


def test_the_quote_is_checked_against_the_sessions_transcript(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    declare_person(project)
    write_transcript(CONVERSATION)
    base = ("--verdict", "met", "--method", "human-observed", "--session", SESSION)
    absent = kyo("verify", f"{record}.c1", *base,
                 "--evidence-file", _file(tmp_path / "x", "words never said"))  # fmt: skip
    assert absent.code == 2 and "credit-quote-not-found" in absent.err, absent
    ambiguous = kyo("verify", f"{record}.c1", *base, "--evidence-file", _file(tmp_path / "y", "yes"))
    assert ambiguous.code == 2 and "credit-quote-ambiguous" in ambiguous.err, ambiguous
    assert "p-3" in ambiguous.err and "p-5" in ambiguous.err
    chosen = kyo("verify", f"{record}.c1", *base, "--evidence-file", str(tmp_path / "y"),
                 "--turn", "p-3")  # fmt: skip
    assert chosen.code == 0, chosen
    matched = kyo("verify", f"{record}.c2", *base,
                  "--evidence-file", _file(tmp_path / "z", "the banner looks right"))  # fmt: skip
    assert matched.code == 0, matched
    first, second = _credits(project)
    assert (first["transcript_check"], first["source_turn"], first["words_form"]) == (
        "matched-paste-unknown", "p-3", "typed")  # fmt: skip
    assert first["observed_at"] == "2026-11-06T10:02:00.000Z"
    # KYO-640.n1, KYO-644.c6: a prompt sent from Claude Code's suggestions.
    assert second["words_form"] == "accepted-suggestion"
    assert second["source_session"] == SESSION


def test_the_session_defaults_to_claude_codes_variable(kyo, project: Path, tmp_path, monkeypatch) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    write_transcript(CONVERSATION)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
              "--evidence-file", _file(tmp_path / "w", "keep the cache"))  # fmt: skip
    assert ran.code == 0, ran
    assert _credits(project)[0]["source_turn"] == "p-2"


def test_session_and_source_other_together_are_a_usage_error(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
              "--evidence-file", _file(tmp_path / "w", "x"), "--session", SESSION,
              *CREDIT_ELSEWHERE)  # fmt: skip
    assert ran.code == 64, ran


def test_credit_flags_on_another_method_are_refused(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "agent-observed",
              "--person", "x")  # fmt: skip
    assert ran.code == 64, ran


# ------------------------------------------------------------------ kyo ungate, carried settles


def _gated(kyo, gate_type: str = "awaiting:human-review", kind: str = "bug") -> tuple[str, str]:
    record = new_record(kyo, kind=kind, criteria=["a"] if kind == "bug" else None)
    gated = kyo("gate", record, gate_type, "--do", "look at the banner", "--json")
    assert gated.code == 0, gated
    return record, gated.json["gate_id"]


def test_a_person_gate_needs_a_credit_or_withdrawn(kyo, project: Path, tmp_path) -> None:
    record, gate = _gated(kyo)
    declare_person(project)
    refused = kyo("ungate", gate, "--why", "done")
    assert refused.code == 2 and "person-gate-clear-requires-credit" in refused.err, refused
    words = _file(tmp_path / "w", "the banner is right")
    ok = kyo("ungate", gate, "--quote-file", words, *CREDIT_ELSEWHERE, "--json")
    assert ok.code == 0, ok
    assert ok.json["cleared_as"] == "answered"
    (credit,) = _credits(project)
    assert _subjects(project, credit["id"]) == [("gate", gate)]
    with _db(project) as conn:
        row = conn.execute("SELECT cleared_as, cleared_at FROM gates WHERE id = ?", (gate,)).fetchone()
    assert row["cleared_as"] == "answered"
    assert credit["created_at"] == row["cleared_at"]


def test_withdrawn_needs_a_reason_and_records_no_person(kyo, project: Path) -> None:
    _record_id, gate = _gated(kyo)
    assert kyo("ungate", gate, "--withdrawn").code == 64
    ok = kyo("ungate", gate, "--withdrawn", "--why", "the banner was removed", "--json")
    assert ok.code == 0, ok
    assert ok.json["cleared_as"] == "withdrawn"
    assert _credits(project) == []


def test_other_gates_clear_as_before(kyo, project: Path) -> None:
    _record_id, gate = _gated(kyo, "awaiting:event")
    ok = kyo("ungate", gate, "--why", "it happened", "--json")
    assert ok.code == 0, ok
    assert ok.json["cleared_as"] is None


def test_bulk_ungate_is_all_or_nothing_over_a_person_gate(kyo, project: Path) -> None:
    record, human = _gated(kyo)
    other = kyo("gate", record, "awaiting:event", "--do", "wait", "--json").json["gate_id"]
    refused = kyo("ungate", other, human, "--why", "x")
    assert refused.code == 2, refused
    with _db(project) as conn:
        assert conn.execute("SELECT count(*) FROM gates WHERE cleared_at IS NULL").fetchone()[0] == 2


def test_a_carried_settle_over_a_person_gate_needs_a_credit(kyo, project: Path, tmp_path) -> None:
    record, gate = _gated(kyo, kind="reminder")
    declare_person(project)
    refused = kyo("wont-do", record, "--why", "moot")
    assert refused.code == 2 and "person-gate-clear-requires-credit" in refused.err, refused
    ok = kyo("wont-do", record, "--why", "moot", "--withdrawn", "--json")
    assert ok.code == 0, ok
    assert ok.json["cleared_gates"][0]["cleared_as"] == "withdrawn"
    record2, gate2 = _gated(kyo, kind="reminder")
    words = _file(tmp_path / "w", "go ahead, it is answered")
    moved = kyo("move", record2, "done", "--why", "answered", "--quote-file", words,
                *CREDIT_ELSEWHERE, "--json")  # fmt: skip
    assert moved.code == 0, moved
    (credit,) = _credits(project)
    assert _subjects(project, credit["id"]) == [("gate", gate2)]


# ------------------------------------------------------------------ kyo confirm


def test_confirm_writes_one_credit_naming_every_verdict_and_the_gate(kyo, project: Path) -> None:
    """KYO-644.c2."""
    record = new_record(kyo, criteria=["a", "b"])
    declare_person(project)
    gate = kyo("gate", record, "awaiting:human-review", "--do", f"check {record}.c1 and {record}.c2",
               "--json").json["gate_id"]  # fmt: skip
    ran = kyo("confirm", record, "--quote", "both look right", *CREDIT_ELSEWHERE, "--no-close", "--json")
    assert ran.code == 0, ran
    (credit,) = _credits(project)
    subjects = _subjects(project, credit["id"])
    with _db(project) as conn:
        verifications = [
            r["id"] for r in conn.execute("SELECT id FROM verifications ORDER BY seq")
        ]
    assert subjects == [*(("verification", v) for v in verifications), ("gate", gate)]
    assert len(verifications) == 2
    assert ran.json["credit_id"] == credit["id"]
    assert f"observed by {TEST_PERSON}" in ran.out


def test_confirm_without_a_credit_writes_nothing(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    kyo("gate", record, "awaiting:human-review", "--do", f"check {record}.c1")
    ran = kyo("confirm", record, "--quote", "fine")
    assert ran.code == 2 and "human-observed-requires-credit" in ran.err, ran
    with _db(project) as conn:
        assert conn.execute("SELECT count(*) FROM verifications").fetchone()[0] == 0


def test_confirm_with_only_a_gate_credits_the_gate(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked")
    gate = kyo("gate", record, "awaiting:human-run", "--do", "run it", "--json").json["gate_id"]
    ran = kyo("confirm", record, "--quote", "ran it, fine", *CREDIT_ELSEWHERE, "--no-close", "--json")
    assert ran.code == 0, ran
    (credit,) = _credits(project)
    assert _subjects(project, credit["id"]) == [("gate", gate)]


# ------------------------------------------------------------------ decisions


def _decision_file(tmp_path: Path, text: str) -> str:
    return _file(tmp_path / "decision.yml", text)


def test_a_decision_is_refused_without_a_decided_credit(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c3."""
    declare_person(project)
    words = _file(tmp_path / "w", "use SQLite")
    refused = kyo("new", "decision", "--title", "Use SQLite", "--evidence-file", words)
    assert refused.code == 2 and "filing-requires-credit" in refused.err, refused
    ok = kyo("new", "decision", "--title", "Use SQLite", "--quote-file", words,
             "--decision-file", _decision_file(tmp_path, "outcome: unprompted\n"),
             *CREDIT_ELSEWHERE, "--json")  # fmt: skip
    assert ok.code == 0, ok
    (credit,) = _credits(project)
    record = ok.json["id"]
    assert credit["role"] == "decided" and credit["outcome"] == "unprompted"
    assert _subjects(project, credit["id"]) == [("record", record)]
    # --quote-file alone is also the filing's evidence, stored once.
    with _db(project) as conn:
        evidence = list(conn.execute("SELECT id FROM evidence_artifacts WHERE record_id = ?", (record,)))
        created = conn.execute("SELECT created_at FROM records WHERE id = ?", (record,)).fetchone()[0]
    assert [e["id"] for e in evidence] == [credit["quote_id"]]
    assert credit["created_at"] == created


def test_a_refined_decision_cites_the_refining_turn(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c3: outcome refined, with the refining turn beside the accepting one."""
    declare_person(project)
    write_transcript(CONVERSATION)
    decision = _decision_file(
        tmp_path,
        "recommended: keep the cache\noutcome: refined\nrefining_turn: p-1\n"
        "alternatives:\n  - text: keep the cache\n    chosen: true\n  - text: drop it\n",
    )
    ok = kyo("new", "decision", "--title", "Keep the cache",
             "--quote-file", _file(tmp_path / "w", "keep the cache, it is fast enough"),
             "--decision-file", decision, "--session", SESSION, "--json")  # fmt: skip
    assert ok.code == 0, ok
    (credit,) = _credits(project)
    assert credit["outcome"] == "refined"
    assert (credit["refining_session"], credit["refining_turn"]) == (SESSION, "p-1")
    assert credit["source_turn"] == "p-2"
    assert credit["recommended_by"] == "orchestrator"
    assert json.loads(credit["alternatives_json"]) == [
        {"text": "keep the cache", "chosen": True},
        {"text": "drop it", "chosen": False},
    ]
    bad = _decision_file(tmp_path, "recommended: x\noutcome: refined\nrefining_turn: a-1\n")
    refused = kyo("new", "decision", "--title", "Other",
                  "--quote-file", str(tmp_path / "w"), "--decision-file", bad,
                  "--session", SESSION)  # fmt: skip
    assert refused.code == 64 and "refining_turn a-1" in refused.err, refused


def test_spawning_a_decision_from_a_proposal_needs_the_credit_too(kyo, project: Path, tmp_path) -> None:
    proposal = new_record(kyo, kind="proposal", title="Pick a store", criteria=["chosen"])
    declare_person(project)
    refused = kyo("spawn", proposal, "--kind", "decision", "--title", "Use SQLite")
    assert refused.code == 2 and "filing-requires-credit" in refused.err, refused
    ok = kyo("spawn", proposal, "--kind", "decision", "--title", "Use SQLite",
             "--quote-file", _file(tmp_path / "w", "approved, go with SQLite"),
             "--decision-file", _decision_file(tmp_path, "recommended: SQLite\noutcome: accepted\n"),
             *CREDIT_ELSEWHERE)  # fmt: skip
    assert ok.code == 0, ok
    assert _credits(project)[0]["outcome"] == "accepted"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("outcome: overrode\n", "names none"),
        ("recommended: x\noutcome: unprompted\n", "unprompted"),
        ("recommended: x\noutcome: accepted\nalternatives:\n  - text: a\n", "chosen"),
        ("outcome: maybe\n", "outcome is"),
    ],
)
def test_a_malformed_decision_file_is_refused_by_name(kyo, project: Path, tmp_path, text, fragment) -> None:
    declare_person(project)
    ran = kyo("new", "decision", "--title", "X", "--quote-file", _file(tmp_path / "w", "x"),
              "--decision-file", _decision_file(tmp_path, text), *CREDIT_ELSEWHERE)  # fmt: skip
    assert ran.code == 64 and fragment in ran.err, ran


# ------------------------------------------------------------------ discussion spans


def test_a_span_is_measured_from_the_transcript(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c5 and c9."""
    declare_person(project)
    write_transcript(CONVERSATION)
    ok = kyo("new", "decision", "--title", "Keep the cache",
             "--quote-file", _file(tmp_path / "w", "keep the cache"),
             "--decision-file", _decision_file(tmp_path, "recommended: keep\noutcome: accepted\n"),
             "--session", SESSION, "--span-first", "p-1", "--span-last", "p-5")  # fmt: skip
    assert ok.code == 0, ok
    (credit,) = _credits(project)
    assert credit["span_session"] == SESSION
    assert (credit["span_first_turn"], credit["span_last_turn"]) == ("p-1", "p-5")
    assert credit["span_person_turns"] == 5
    # a-1 is one reply; a-2 (a tool call), t-2 and a-3 are ONE reply ending in text; a-4 one.
    assert credit["span_agent_turns"] == 3
    # 10:00:00 to 10:02:00 is 120 s; the 58-minute gap is a break; 11:00:00 to 11:01:00 is 60 s.
    assert credit["span_active_seconds"] == 180
    assert (credit["span_started_at"], credit["span_ended_at"]) == (
        "2026-11-06T10:00:00.000Z", "2026-11-06T11:01:00.000Z")  # fmt: skip


def test_a_reply_ending_in_a_tool_call_is_not_a_reply_to_the_person(tmp_path, monkeypatch) -> None:
    """KYO-644.c9: counted per agent turn that ends in text, not per record with text."""
    write_transcript(
        [
            person("p-1", "go", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "starting", "2026-11-06T10:00:01.000Z"),
            agent_tool("a-2", "2026-11-06T10:00:02.000Z"),
            person("p-2", "stop", "2026-11-06T10:00:03.000Z"),
            agent_text("a-3", "one", "2026-11-06T10:00:04.000Z"),
            agent_tool("a-4", "2026-11-06T10:00:05.000Z"),
            agent_text("a-5", "two", "2026-11-06T10:00:06.000Z"),
        ]
    )
    measure = tr.measure_span(SESSION, "p-1", "a-5")
    assert (measure.person_turns, measure.agent_turns) == (2, 1)


def test_an_absent_transcript_stores_the_span_ids_with_empty_measures(kyo, project: Path, tmp_path) -> None:
    declare_person(project)
    ok = kyo("new", "decision", "--title", "Keep the cache",
             "--quote-file", _file(tmp_path / "w", "keep the cache"),
             "--decision-file", _decision_file(tmp_path, "outcome: unprompted\n"),
             *CREDIT_ELSEWHERE, "--span-session", SESSION,
             "--span-first", "p-1", "--span-last", "p-5")  # fmt: skip
    assert ok.code == 0, ok
    (credit,) = _credits(project)
    assert (credit["span_session"], credit["span_first_turn"], credit["span_last_turn"]) == (
        SESSION, "p-1", "p-5")  # fmt: skip
    assert credit["span_person_turns"] is None and credit["span_active_seconds"] is None


def test_a_span_naming_a_record_the_transcript_lacks_is_refused(kyo, project: Path, tmp_path) -> None:
    declare_person(project)
    write_transcript(CONVERSATION)
    ran = kyo("new", "decision", "--title", "X", "--quote-file", _file(tmp_path / "w", "keep the cache"),
              "--decision-file", _decision_file(tmp_path, "outcome: unprompted\n"),
              "--session", SESSION, "--span-first", "p-1", "--span-last", "nope")  # fmt: skip
    assert ran.code == 64 and "nope" in ran.err, ran


# ------------------------------------------------------------------ kyo credit


def _legacy_verdict(kyo, project: Path, record: str) -> str:
    """A human-observed verdict written before credits, as the backfill finds one."""
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "agent-observed", "--json")
    vid = ran.json["verification_id"]
    with _db(project) as conn:
        conn.execute("UPDATE verifications SET method = 'human-observed' WHERE id = ?", (vid,))
    return vid


def _approval(kyo, project: Path, tmp_path: Path) -> str:
    """The person's decided credit approving a backfill batch."""
    ran = kyo("new", "decision", "--title", "Approve the backfill",
              "--quote-file", _file(tmp_path / "approve", "approved, credit them"),
              "--decision-file", _decision_file(tmp_path, "outcome: unprompted\n"),
              *CREDIT_ELSEWHERE, "--json")  # fmt: skip
    assert ran.code == 0, ran
    return ran.json["credit_id"]


def test_kyo_credit_needs_a_basis_and_accepts_the_persons_approval(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c4."""
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    vid = _legacy_verdict(kyo, project, record)
    words = _file(tmp_path / "w", "it is very fast")
    base = ("credit", vid, "--quote-file", words, *CREDIT_ELSEWHERE)
    refused = kyo(*base)
    assert refused.code == 2 and "reconstructed-credit-requires-basis" in refused.err, refused
    basis = _approval(kyo, project, tmp_path)
    ok = kyo(*base, "--basis", basis, "--json")
    assert ok.code == 0, ok
    credit = next(c for c in _credits(project) if c["id"] == ok.json["credit_id"])
    assert credit["basis_id"] == basis
    assert _subjects(project, credit["id"]) == [("verification", vid)]


def test_a_basis_that_is_not_the_persons_approval_is_refused(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    declare_person(project)
    vid = _legacy_verdict(kyo, project, record)
    observed = kyo("verify", f"{record}.c2", "--verdict", "met", "--method", "human-observed",
                   "--evidence-file", _file(tmp_path / "o", "b is fine"), *CREDIT_ELSEWHERE,
                   "--json").json["credit_id"]  # fmt: skip
    ran = kyo("credit", vid, "--quote-file", _file(tmp_path / "w", "fast"), *CREDIT_ELSEWHERE,
              "--basis", observed)  # fmt: skip
    assert ran.code == 2 and "credit-basis-not-approval" in ran.err, ran


def test_a_subject_already_credited_is_superseded_not_doubled(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    vid = _legacy_verdict(kyo, project, record)
    basis = _approval(kyo, project, tmp_path)
    words = _file(tmp_path / "w", "fast")
    base = ("credit", vid, "--quote-file", words, *CREDIT_ELSEWHERE, "--basis", basis, "--json")
    first = kyo(*base)
    assert first.code == 0, first
    again = kyo(*base)
    assert again.code == 2 and "credit-subject-already-credited" in again.err, again
    replaced = kyo(*base, "--supersede", first.json["credit_id"])
    assert replaced.code == 0, replaced
    old = next(c for c in _credits(project) if c["id"] == first.json["credit_id"])
    assert old["superseded_by"] == replaced.json["credit_id"]


def test_an_authorized_go_ahead_takes_no_decision_file(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c8: a go-ahead approving a plan already stated."""
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    with _db(project) as conn:
        conn.execute("UPDATE records SET created_at = '2026-11-06T09:00:00.000Z' WHERE id = ?", (record,))
    write_transcript(CONVERSATION)
    with_file = kyo("credit", record, "--role", "authorized",
                    "--quote-file", _file(tmp_path / "w", "keep the cache"), "--session", SESSION,
                    "--decision-file", _decision_file(tmp_path, "outcome: accepted\n"))  # fmt: skip
    assert with_file.code == 64, with_file
    ok = kyo("credit", record, "--role", "authorized", "--quote-file", str(tmp_path / "w"),
             "--session", SESSION, "--json")  # fmt: skip
    assert ok.code == 0, ok
    credit = next(c for c in _credits(project) if c["id"] == ok.json["credit_id"])
    assert credit["role"] == "authorized"
    assert credit["outcome"] is None and credit["recommended"] is None
    assert credit["basis_id"] is None


def test_a_decision_record_still_needs_a_decided_credit(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c8: authorized is never a decision's credit."""
    declare_person(project)
    ran = kyo("new", "decision", "--title", "X", "--quote-file", _file(tmp_path / "w", "build it"),
              *CREDIT_ELSEWHERE)  # fmt: skip
    assert ran.code == 2 and "--decision-file" in ran.err, ran


# ------------------------------------------------------------------ kyo redact


def test_redact_covers_a_credits_free_text_and_the_ledgers(kyo, project: Path, tmp_path) -> None:
    """KYO-644.c7."""
    declare_person(project)
    secret_rec = "the recommendation naming customer 4421-XYZ"
    secret_alt = "the alternative naming customer 4421-XYZ"
    secret_where = "a phone call with customer 4421-XYZ"
    decision = _decision_file(
        tmp_path,
        f"recommended: {secret_rec}\noutcome: accepted\nalternatives:\n"
        f"  - text: {secret_alt}\n    chosen: true\n",
    )
    ok = kyo("new", "decision", "--title", "X", "--quote-file", _file(tmp_path / "w", "yes do it"),
             "--decision-file", decision, "--source-other", secret_where, "--json")  # fmt: skip
    assert ok.code == 0, ok
    record, credit_id = ok.json["id"], ok.json["credit_id"]
    # An edit afterwards copies the credit into another snapshot.
    assert kyo("edit", record, "--title", "Y").code == 0
    redacted = kyo("redact", credit_id, "--why", "a customer identifier", "--json")
    assert redacted.code == 0, redacted
    assert redacted.json["complete"], redacted
    (credit,) = _credits(project)
    assert credit["recommended"] == credit["source_note"] == REDACTION_MARKER
    assert all("4421" not in a["text"] for a in json.loads(credit["alternatives_json"]))
    assert json.loads(credit["alternatives_json"])[0]["chosen"] is True
    for path in (project / "kyosei" / "export").rglob("*"):
        if path.is_file():
            assert "4421-XYZ" not in path.read_text(encoding="utf-8", errors="replace"), path
    with _db(project) as conn:
        for row in conn.execute("SELECT before_json, after_json, changed_json FROM events"):
            assert "4421-XYZ" not in json.dumps(tuple(row)), row
    doctor = kyo("doctor")
    assert "4421" not in doctor.out + doctor.err
    assert "credit" not in " ".join(l for l in doctor.out.splitlines() if "redact" in l), doctor
