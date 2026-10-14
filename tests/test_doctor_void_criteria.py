"""A void criterion is not a desynced one — ``kyo split`` must not leave a store
that no sanctioned command can repair.

Reported by the first consumer project outside this repo (criterion APP-58.c4)
while two dispatches were in flight, and reproduced here from a clean store in four
commands before anything was changed:

    kyo new task --title ... --criteria-file (two criteria)
    kyo verify REP-1.c2 --verdict unmet-unobserved --method reasoned-about --why ...
    kyo split REP-1 --to-successor REP-1.c2
    kyo doctor        # -> criterion_verification_agreement fails, permanently

The chain: ``kyo split`` voids the criteria it carries to the successor (law 4:
supersede, never delete), ``check_criterion_verification_agreement`` demanded state
and latest verdict agree, and ``kyo verify`` accepts only ``met`` /
``unmet-unbuilt`` / ``unmet-unobserved`` — so no sanctioned command could ever
restore agreement.

The check was applied outside its own premise. ``EDIT_DIRECT_CRITERION_STATES``
(the two void states) are premise judgments that ``contracts.py`` already declares
are *not* verified facts, and ``VERIFY_VERDICTS`` is deliberately disjoint from
them. Two alternatives were offered by the reporter and rejected: having split
retire the verification would delete provenance (law 9, append-only), and widening
the verdict vocabulary would let a premise judgment masquerade as a verified fact,
which is exactly what KYO-4.c3 split those two vocabularies to prevent.
"""

from __future__ import annotations

from .conftest import new_record


def test_splitting_a_verified_criterion_leaves_doctor_clean(kyo) -> None:
    """The reported case, end to end."""
    record = new_record(kyo, kind="task", title="Split me", criteria=["the first", "the second"])
    assert kyo(
        "verify",
        f"{record}.c2",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "not run yet",
    ).code == 0
    assert kyo("doctor").code == 0, "precondition: the store is clean before the split"

    assert kyo("split", record, "--to-successor", f"{record}.c2").code == 0
    result = kyo("doctor")
    assert result.code == 0, result
    assert "criterion_verification_agreement" not in result.err


def test_voiding_a_verified_criterion_by_hand_leaves_doctor_clean(kyo) -> None:
    """`kyo split` is not the only door: `kyo edit --criterion --state` sets the
    same two void states directly, and hit the same wall."""
    record = new_record(kyo, kind="task", title="Void me", criteria=["the only one"])
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed"
    ).code == 0
    assert kyo(
        "edit",
        record,
        "--criterion",
        f"{record}.c1",
        "--state",
        "void-premise-gone",
        "--why",
        "the feature it checked was removed",
    ).code == 0

    assert kyo("doctor").code == 0


def test_a_real_desync_between_verifiable_states_still_fails(kyo, db) -> None:
    """The exemption must not become a hole. Between two states a verification
    *can* record, disagreement is still the law-3 failure the check exists for."""
    record = new_record(kyo, kind="task", title="Desynced", criteria=["the only one"])
    assert kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed"
    ).code == 0

    conn = db()
    conn.execute(
        "UPDATE criteria SET state = 'unmet-unbuilt' WHERE id = ?", (f"{record}.c1",)
    )
    conn.close()

    result = kyo("doctor")
    assert result.code != 0
    assert "criterion_verification_agreement" in result.err


def test_the_verification_history_survives_the_void(kyo) -> None:
    """Nothing is hidden by the exemption — the verdict that was recorded is still
    on the record, which is what makes voiding a supersede rather than a delete."""
    record = new_record(kyo, kind="task", title="History kept", criteria=["a", "b"])
    kyo(
        "verify",
        f"{record}.c2",
        "--verdict",
        "unmet-unobserved",
        "--method",
        "reasoned-about",
        "--why",
        "never run",
    )
    kyo("split", record, "--to-successor", f"{record}.c2")

    shown = kyo("show", record, "--json")
    criterion = next(c for c in shown.json["criteria"] if c["id"] == f"{record}.c2")
    assert criterion["state"] == "void-withdrawn"
    assert criterion["reason"], "a void state still requires its reason (law 10)"
    assert [v["verdict"] for v in criterion["verifications"]] == ["unmet-unobserved"]


def test_a_void_criterion_still_needs_a_reason(kyo, db) -> None:
    """The other check that guards this path is untouched: voiding without a reason
    is still a doctor failure, so the exemption did not remove the guard, only the
    one that could not be satisfied."""
    record = new_record(kyo, kind="task", title="Unreasoned void", criteria=["a"])
    conn = db()
    conn.execute(
        "UPDATE criteria SET state = 'void-withdrawn', reason = '' WHERE id = ?",
        (f"{record}.c1",),
    )
    conn.close()

    result = kyo("doctor")
    assert result.code != 0
    assert "criterion_states" in result.err
