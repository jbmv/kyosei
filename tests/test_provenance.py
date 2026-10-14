"""KYO-4: claims, verifications, evidence artifacts (law 9).

Charter: "Verification level (asserted/derived/verified) plus method, instrument,
author, and date. Claims keep a *history of verdicts* ... Provenance on every
claim." And law 3: a criterion's state is derived from its latest verification,
never set directly.
"""

from __future__ import annotations

import json

from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR

from .conftest import export_of, new_record, verify_criterion

# ------------------------------------------------------------------------- claim


def test_claim_records_statement_level_method_author_time(kyo) -> None:
    record = new_record(kyo)
    result = kyo("--full",
        "--actor", "worker-3", "claim", record,
        "--level", "derived", "--method", "raw SQL: SELECT count(*) FROM events",
        "--file", "-", "--json", stdin="there are 41 events so far",
    )
    assert result.code == 0, result
    claim = result.json["record"]["claims"][0]
    assert claim["statement"] == "there are 41 events so far"
    assert claim["level"] == "derived"
    assert claim["method"] == "raw SQL: SELECT count(*) FROM events"
    assert claim["author"] == "worker-3"
    assert claim["created_at"].endswith("Z")
    assert claim["id"] == f"{record}.m1"


def test_claim_unknown_level_is_refused(kyo) -> None:
    record = new_record(kyo)
    result = kyo(
        "claim", record, "--level", "guessed", "--method", "vibes",
        "--file", "-", stdin="x",
    )
    assert result.code == 64
    assert "claim level" in result.err


def test_claim_statement_arrives_only_via_file_or_stdin(kyo) -> None:
    """Law 5: no flag carries the statement as a shell argument."""
    result = kyo("claim", "--help")
    assert "--statement" not in result.out


def test_show_renders_claims(kyo) -> None:
    record = new_record(kyo)
    kyo(
        "claim", record, "--level", "asserted", "--method", "read the code",
        "--file", "-", stdin="the cache TTL is 30s",
    )
    out = kyo("show", record).out
    assert "the cache TTL is 30s" in out
    assert "asserted" in out
    assert "read the code" in out


def test_claim_survives_the_export_round_trip(kyo, project) -> None:
    record = new_record(kyo)
    kyo(
        "claim", record, "--level", "verified", "--method", "ran it",
        "--file", "-", stdin="p95 is 40ms",
    )
    doc = json.loads(export_of(project, record).read_text(encoding="utf-8"))
    assert doc["claims"][0]["statement"] == "p95 is 40ms"
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["claims"][0]["statement"] == "p95 is 40ms"


# ------------------------------------------------------- claim supersession (c2)


def test_claim_supersession_keeps_full_history(kyo) -> None:
    record = new_record(kyo)
    first = kyo(
        "claim", record, "--level", "asserted", "--method", "guessed",
        "--file", "-", "--json", stdin="the endpoint is deprecated",
    ).json["claim_id"]
    second = kyo(
        "claim", record, "--level", "verified", "--method", "checked the changelog",
        "--file", "-", "--supersedes", first, "--json",
        stdin="the endpoint is NOT deprecated, that was wrong",
    ).json["claim_id"]

    doc = kyo("show", record, "--json").json
    claims = {c["id"]: c for c in doc["claims"]}
    assert claims[first]["superseded_by"] == second
    assert claims[first]["statement"] == "the endpoint is deprecated"  # never edited in place
    assert claims[second]["supersedes"] == first
    assert claims[second]["statement"] == "the endpoint is NOT deprecated, that was wrong"

    out = kyo("show", record).out
    assert first in out and second in out  # both readable, not just the latest


def test_a_claim_can_only_be_superseded_once(kyo) -> None:
    record = new_record(kyo)
    first = kyo(
        "claim", record, "--level", "asserted", "--method", "m",
        "--file", "-", "--json", stdin="x",
    ).json["claim_id"]
    kyo(
        "claim", record, "--level", "asserted", "--method", "m",
        "--file", "-", "--supersedes", first, stdin="y",
    )
    result = kyo(
        "claim", record, "--level", "asserted", "--method", "m",
        "--file", "-", "--supersedes", first, stdin="z",
    )
    assert result.code == EXIT_ERROR
    assert "already superseded" in result.err


def test_claim_supersedes_must_belong_to_the_same_record(kyo) -> None:
    a = new_record(kyo, title="a")
    b = new_record(kyo, title="b")
    claim_a = kyo(
        "claim", a, "--level", "asserted", "--method", "m", "--file", "-", "--json", stdin="x"
    ).json["claim_id"]
    result = kyo(
        "claim", b, "--level", "asserted", "--method", "m",
        "--file", "-", "--supersedes", claim_a, stdin="y",
    )
    assert result.code == EXIT_ERROR
    assert a in result.err


# ---------------------------------------------------------------------- verify


def test_verify_derives_the_criterion_state(kyo) -> None:
    record = new_record(kyo, criteria=["the endpoint returns 200"])
    result = kyo(
        "--actor", "worker-3", "verify", f"{record}.c1",
        "--verdict", "met", "--method", "machine-checked",
    )
    assert result.code == 0, result
    doc = kyo("show", record, "--json").json
    assert doc["criteria"][0]["state"] == "met"
    verification = doc["criteria"][0]["verifications"][0]
    assert verification["verdict"] == "met"
    assert verification["method"] == "machine-checked"
    assert verification["author"] == "worker-3"
    assert verification["id"] == f"{record}.v1"


def test_verify_with_evidence_file_stores_and_links_the_artifact(kyo) -> None:
    record = new_record(kyo, criteria=["returns 200"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "machine-checked",
        "--evidence-file", "-", stdin="HTTP/1.1 200 OK\ncontent-length: 42",
    )
    assert result.code == 0, result
    doc = kyo("show", record, "--json").json
    verification = doc["criteria"][0]["verifications"][0]
    assert verification["evidence_id"] is not None
    artifact = next(e for e in doc["evidence_artifacts"] if e["id"] == verification["evidence_id"])
    assert artifact["text"] == "HTTP/1.1 200 OK\ncontent-length: 42"
    assert artifact["supports_id"] == f"{record}.c1"
    assert len(artifact["sha256"]) == 64


def test_verify_unmet_unobserved_requires_a_reason(kyo) -> None:
    record = new_record(kyo, criteria=["the cron fires nightly"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unobserved", "--method", "reasoned-about"
    )
    assert result.code == EXIT_CONTRACT
    assert "requires-reason" in result.err
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unobserved", "--method", "reasoned-about",
        "--why", "no access to the scheduler host",
    )
    assert result.code == 0
    assert kyo("show", record, "--json").json["criteria"][0]["reason"] == (
        "no access to the scheduler host"
    )


def test_verify_refuses_void_verdicts(kyo) -> None:
    """Void states are premise judgments, not verified facts — `kyo edit`'s job."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "void-withdrawn", "--method", "reasoned-about"
    )
    assert result.code == 64
    assert "kyo edit" in result.err


def test_verify_unknown_method_is_refused(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "vibes")
    assert result.code == 64
    assert "verification method" in result.err


def test_verify_re_verification_appends_a_new_event_and_moves_the_state(kyo) -> None:
    """A criterion can be true, then false, then true again — the history is the
    valuable part (law 9)."""
    record = new_record(kyo, criteria=["a"])
    verify_criterion(kyo, f"{record}.c1", "met", method="machine-checked")
    verify_criterion(
        kyo, f"{record}.c1", "unmet-unobserved", method="reasoned-about", why="regressed"
    )
    doc = kyo("show", record, "--json").json
    assert doc["criteria"][0]["state"] == "unmet-unobserved"
    assert [v["verdict"] for v in doc["criteria"][0]["verifications"]] == [
        "met",
        "unmet-unobserved",
    ]


def test_edit_cannot_set_a_verified_state_directly(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    for state in ("met", "unmet-unbuilt", "unmet-unobserved"):
        result = kyo("edit", record, "--criterion", f"{record}.c1", "--state", state)
        assert result.code == 64, state
        assert "kyo verify" in result.err


def test_edit_still_sets_void_states_directly(kyo) -> None:
    """The one honest direct-flip path law 3 leaves standing (KYO-4.c3)."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "not needed",
    )
    assert result.code == 0
    assert kyo("show", record, "--json").json["criteria"][0]["state"] == "void-withdrawn"


# --------------------------------------------------------------------- attach


def test_attach_stores_verbatim_with_sha256_and_echoes_it(kyo) -> None:
    record = new_record(kyo)
    text = "GET /widgets -> 500\nRetry-After: 30"
    human = kyo("attach", record, "--file", "-", stdin=text)
    assert human.code == 0, human
    assert "sha256" in human.out  # law 5: every write echoes back what it stored

    record2 = new_record(kyo, title="two")
    result = kyo("--full", "attach", record2, "--file", "-", "--json", stdin=text)
    assert result.code == 0, result
    artifact = result.json["record"]["evidence_artifacts"][0]
    assert artifact["text"] == text
    import hashlib

    assert artifact["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_attach_links_to_what_it_supports(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("--full",
        "attach", record, "--file", "-", "--supports", f"{record}.c1", "--json", stdin="evidence"
    )
    assert result.code == 0, result
    assert result.json["record"]["evidence_artifacts"][0]["supports_id"] == f"{record}.c1"


def test_attach_supports_must_name_something_real(kyo) -> None:
    record = new_record(kyo)
    result = kyo("attach", record, "--file", "-", "--supports", "TST-404", stdin="x")
    assert result.code == EXIT_ERROR


def test_attach_survives_the_export_round_trip(kyo, project) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("attach", record, "--file", "-", "--supports", f"{record}.c1", stdin="captured output")
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    artifact = kyo("show", record, "--json").json["evidence_artifacts"][0]
    assert artifact["text"] == "captured output"
    assert artifact["supports_id"] == f"{record}.c1"


def test_attach_empty_capture_is_refused(kyo) -> None:
    record = new_record(kyo)
    result = kyo("attach", record, "--file", "-", stdin="   \n")
    assert result.code == 64


# ---------------------------------------------------------------- doctor (c5)


def test_doctor_catches_a_manufactured_state_verdict_disagreement(kyo, project) -> None:
    record = new_record(kyo, criteria=["a"])
    verify_criterion(kyo, f"{record}.c1", "met", method="machine-checked")
    assert kyo("doctor").code == 0

    import sqlite3

    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.execute("UPDATE criteria SET state = 'unmet-unbuilt' WHERE id = ?", (f"{record}.c1",))
    conn.commit()
    conn.close()

    result = kyo("doctor", "--json")
    assert result.code == 3
    failures = result.json["checks"]["criterion_verification_agreement"]["failures"]
    assert any(f"{record}.c1" in f for f in failures)


def test_doctor_passes_when_state_and_latest_verdict_agree(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    verify_criterion(kyo, f"{record}.c1", "met", method="machine-checked")
    verify_criterion(
        kyo, f"{record}.c1", "unmet-unobserved", method="reasoned-about", why="regressed"
    )
    assert kyo("doctor").code == 0
