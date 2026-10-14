"""KYO-59 — clearing a gate flags the criteria that were written under it.

A gate's whole meaning is "this record is waiting on something that will change
what it should be", so clearing one always implies something just changed, and
every criterion authored before that moment was written under the assumption it
has now resolved. The test is mechanical — ``criterion.created_at`` against the
gate's resolution — with no text analysis and no similarity scoring in it.
"""

from __future__ import annotations

from kyosei import contracts

from .conftest import new_record


def _gate(kyo, record: str, gate_type: str, do: str | None = "the decision lands") -> str:
    args = ["gate", record, gate_type]
    if do is not None:
        args += ["--do", do]
    result = kyo(*args, "--json")
    assert result.code == 0, result
    return result.json["gate_id"]


def _unresolved(kyo, record: str) -> list[dict]:
    return [
        f for f in kyo("show", record, "--json").json["flags"] if f["resolved_at"] is None
    ]


# ------------------------------------------------------------------------- c1


def test_clearing_a_holding_for_gate_flags_the_criteria_written_under_it(kyo) -> None:
    """The KYO-30 case: a record held open across a design decision, whose two
    criteria described the pre-decision world when the gate cleared."""
    record = new_record(kyo, criteria=["the stamp is a single value", "the four stamped ideas"])
    gate = _gate(kyo, record, "holding-for", "the v2 promoted-table design decides the stamp")

    result = kyo("ungate", gate, "--why", "the design decided it", "--json")
    assert result.code == 0, result
    flag = result.json["flag"]
    assert flag is not None, "clearing the gate said nothing about the stale criteria"
    assert flag["flag_type"] == contracts.FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA
    assert flag["criteria"] == [f"{record}.c1", f"{record}.c2"]
    assert f"{record}.c1" in flag["detail"] and gate in flag["detail"]
    assert "kyo restate" in flag["detail"], (
        "KYO-58 is what makes this a mechanism rather than a nag: the flag names the "
        "path to fixing what it points at"
    )


def test_the_new_flag_class_is_declared_and_known_to_doctor(kyo) -> None:
    assert contracts.FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA in contracts.FLAG_TYPES
    assert contracts.FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA not in contracts.SWEEP_FLAG_TYPES
    assert set(contracts.SWEEP_FLAG_TYPES) < set(contracts.FLAG_TYPES)
    assert set(contracts.GATE_TYPES) >= contracts.GATE_TYPES_FLAGGING_STALE_CRITERIA

    record = new_record(kyo, criteria=["a"])
    gate = _gate(kyo, record, "holding-for")
    kyo("ungate", gate)
    doctor = kyo("doctor", "--json")
    assert doctor.code == 0, doctor
    assert doctor.json["checks"]["flag_types"]["failures"] == []


def test_the_flag_is_resolvable_like_any_other(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    gate = _gate(kyo, record, "holding-for")
    flag_id = kyo("ungate", gate, "--json").json["flag"]["flag_id"]

    assert _unresolved(kyo, record)
    resolved = kyo("flag", "resolve", flag_id, "--why", "reviewed; both still stand")
    assert resolved.code == 0, resolved
    assert _unresolved(kyo, record) == []


def test_a_record_with_no_criteria_is_not_flagged(kyo) -> None:
    """Law 6: the check ran and found nothing is said out loud, and is a different
    statement from the check not applying to this gate type at all."""
    record = new_record(kyo)
    gate = _gate(kyo, record, "holding-for")
    result = kyo("ungate", gate)
    assert result.code == 0, result
    assert "no criteria predate this clearing" in result.out
    assert _unresolved(kyo, record) == []


def test_settled_criteria_do_not_raise_the_flag(kyo) -> None:
    """A met criterion's verdict was recorded against the world as it was, and a
    void one directs nobody. Only a criterion still able to send a worker somewhere
    can send one to the wrong place — which is the harm this flag is about."""
    record = new_record(kyo, criteria=["already true", "still open"])
    kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    gate = _gate(kyo, record, "holding-for")
    flag = kyo("ungate", gate, "--json").json["flag"]
    assert flag["criteria"] == [f"{record}.c2"]


# ------------------------------------------------------------------------- c2


def test_the_flag_reaches_the_next_session_s_brief_and_clears_from_it(kyo) -> None:
    """The KYO-30 case spanned a session boundary, so the flag has to as well.
    Drives the whole loop: clear the gate, take a brief the way a SessionStart hook
    would, resolve, take another."""
    record = new_record(kyo, criteria=["the stamp is a single value"])
    gate = _gate(kyo, record, "holding-for", "the design decides the stamp")
    kyo("brief")  # the session in which the gate was cleared

    flag_id = kyo("ungate", gate, "--why", "decided", "--json").json["flag"]["flag_id"]

    next_session = kyo("brief", "--json")
    assert next_session.code == 0, next_session
    flagged = [f for f in next_session.json["unresolved_flags"] if f["id"] == flag_id]
    assert flagged, "the flag did not survive to the next brief"
    assert flagged[0]["flag_type"] == contracts.FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA
    assert flag_id in kyo("brief").out

    kyo("flag", "resolve", flag_id, "--why", "reviewed")
    after = kyo("brief", "--json")
    assert [f for f in after.json["unresolved_flags"] if f["id"] == flag_id] == []


# ------------------------------------------------------------------------- c3


def test_only_holding_for_raises_the_flag(kyo) -> None:
    """KYO-59.c3, decided: ``holding-for`` clears when a DECISION was made, which is
    what rewrites what a record should be. The other three clear when something
    happened or someone looked. Firing on all four would make the flag routine
    enough to ignore — the KYO-33 failure this project already has a record about —
    for three types with no observed case behind them.
    """
    quiet = [
        g
        for g in contracts.GATE_TYPES
        if g not in contracts.GATE_TYPES_FLAGGING_STALE_CRITERIA
    ]
    assert quiet, "this test is vacuous if every gate type fires"
    for gate_type in quiet:
        record = new_record(kyo, criteria=["written before the gate cleared"])
        gate = _gate(kyo, record, gate_type, "run the migration")
        result = kyo("ungate", gate)
        assert result.code == 0, result
        assert _unresolved(kyo, record) == [], gate_type
        assert "does not raise" in result.out, (
            f"{gate_type} said nothing about why it did not flag — silence reads the same "
            "as a check that ran and found nothing"
        )
    assert sorted(contracts.GATE_TYPES_FLAGGING_STALE_CRITERIA) == ["holding-for"]
