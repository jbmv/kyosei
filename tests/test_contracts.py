"""Law 7 — transition contracts are enforced at the transition, naming the unmet item.

The failure this prevents: two cards entered a contract-bearing column with unmet
contracts in one session and the CLI accepted both.
"""

from __future__ import annotations

import pytest

from kyosei.contracts import (
    CONTRACT_ACTIVE_REQUIRES_CRITERIA,
    CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION,
    CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA,
    CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW,
    CONTRACT_RETIRED_REQUIRES_REASON,
    CONTRACT_TERMINAL_IS_TERMINAL,
    CONTRACT_VERIFICATION_REQUIRES_CRITERIA,
    check_transition,
    default_ladder,
)
from kyosei.errors import EXIT_CONTRACT, ContractViolation

from .conftest import new_record, verify_criterion


def test_done_requires_every_criterion_settled(kyo) -> None:
    record = new_record(kyo, criteria=["ships a fix", "logs stay clean"])
    result = kyo("move", record, "done")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_COMPLETE_REQUIRES_SETTLED_CRITERIA in result.err
    # The message names the unmet items, both of them, by id and text.
    assert f"{record}.c1 is unmet-unbuilt: ships a fix" in result.err
    assert f"{record}.c2 is unmet-unbuilt: logs stay clean" in result.err
    assert kyo("show", record, "--json").json["status"] == "open"


def test_done_accepts_met_and_void_criteria(kyo) -> None:
    record = new_record(kyo, criteria=["ships a fix", "logs stay clean"])
    verify_criterion(kyo, f"{record}.c1", "met")
    assert (
        kyo(
            "edit", record, "--criterion", f"{record}.c2", "--state", "void-premise-gone",
            "--why", "that log was deleted",
        ).code
        == 0
    )
    assert kyo("move", record, "done").code == 0
    assert kyo("show", record, "--json").json["status"] == "done"


def test_zero_criteria_no_longer_vacuously_completes_done(kyo) -> None:
    """The hole found by a live smoke test: "all criteria met" is vacuously
    true at zero criteria, so a record with no criteria at all used to sail into
    `done`. `working` already refuses zero criteria for requires_criteria kinds, but a
    transition may skip forward (charter), so `open -> done` must be checked too.
    """
    record = new_record(kyo)  # kind=bug, requires_criteria, zero criteria authored
    result = kyo("move", record, "done")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION in result.err
    assert "wont-do" in result.err
    assert kyo("show", record, "--json").json["status"] == "open"


def test_all_void_criteria_refuse_done_too(kyo) -> None:
    """All-void is the same hole wearing criteria: zero of them are actually met."""
    record = new_record(kyo, criteria=["a", "b"])
    assert kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", "premise died",
    ).code == 0
    assert kyo(
        "edit", record, "--criterion", f"{record}.c2", "--state", "void-withdrawn",
        "--why", "not worth pursuing after all",
    ).code == 0
    result = kyo("move", record, "done")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_COMPLETE_REQUIRES_A_MET_CRITERION in result.err


def test_kinds_without_requires_criteria_skip_the_met_criterion_check(kyo) -> None:
    """``plan``'s completion is a status distribution over members, not criteria."""
    record = new_record(kyo, kind="plan", title="milestone 1")
    assert kyo("move", record, "done").code == 0


def test_unmet_unobserved_still_blocks_done(kyo) -> None:
    """"I could not observe it" is not "it is fine" — the whole point of law 10."""
    record = new_record(kyo, criteria=["the cron fires nightly"])
    verify_criterion(
        kyo, f"{record}.c1", "unmet-unobserved", why="no access to the scheduler host"
    )
    result = kyo("move", record, "done")
    assert result.code == EXIT_CONTRACT
    assert "unmet-unobserved" in result.err


def test_working_requires_at_least_one_criterion(kyo) -> None:
    record = new_record(kyo, kind="chore", title="tidy the logs")
    result = kyo("move", record, "working")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_ACTIVE_REQUIRES_CRITERIA in result.err
    assert record in result.err


def test_exempt_kinds_may_enter_working_without_criteria(kyo) -> None:
    record = new_record(kyo, kind="plan", title="milestone 1")
    assert kyo("move", record, "working").code == 0


def test_wont_do_requires_a_reason(kyo) -> None:
    record = new_record(kyo)
    result = kyo("wont-do", record)
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_RETIRED_REQUIRES_REASON in result.err
    assert kyo("show", record, "--json").json["status"] == "open"


def test_move_to_wont_do_enforces_the_same_contract(kyo) -> None:
    """One contract, one enforcement point, whichever verb the caller reached for."""
    record = new_record(kyo)
    assert kyo("move", record, "wont-do").code == EXIT_CONTRACT
    assert kyo("move", record, "wont-do", "--why", "superseded by TST-9").code == 0
    assert kyo("show", record, "--json").json["wont_do_reason"] == "superseded by TST-9"


def test_wont_do_is_terminal(kyo) -> None:
    record = new_record(kyo)
    assert kyo("wont-do", record, "--why", "dead premise").code == 0
    result = kyo("move", record, "open")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_TERMINAL_IS_TERMINAL in result.err


def test_knowledge_archetype_records_refuse_move(kyo) -> None:
    """decision/evidence have no status flow — their lifecycle is amendment (milestone 4)."""
    record = new_record(kyo, kind="decision", title="use SQLite")
    result = kyo("move", record, "working")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW in result.err
    assert "amendment" in result.err
    assert kyo("show", record, "--json").json["status"] == "open"


def test_knowledge_archetype_records_refuse_wont_do_too(kyo) -> None:
    record = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    result = kyo("wont-do", record, "--why", "no longer relevant")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_KNOWLEDGE_HAS_NO_STATUS_FLOW in result.err


def test_backwards_transitions_are_allowed(kyo) -> None:
    """A regression must be expressible; a board that cannot say so rots."""
    record = new_record(kyo, criteria=["ships a fix"])
    assert kyo("move", record, "working").code == 0
    assert kyo("move", record, "built").code == 0
    assert kyo("move", record, "working").code == 0


def test_unknown_status_is_a_usage_error_not_a_contract_violation(kyo) -> None:
    record = new_record(kyo)
    result = kyo("move", record, "shipped")
    assert result.code == 64
    assert "not a status" in result.err


def test_check_transition_guards_unknown_statuses_at_the_library_level() -> None:
    with pytest.raises(ContractViolation) as caught:
        check_transition(
            record_id="TST-1",
            kind="bug",
            from_status="open",
            to_status="shipped",
            criteria=[],
            reason=None,
            requires_criteria=True,
            ladder=default_ladder(),
            open_docs_obligations=[],
        )
    assert caught.value.contract == "status-must-be-known"


# ------------------------------------------------ KYO-413.c1: verification rungs


@pytest.mark.parametrize("status", ["built", "verified"])
def test_zero_criteria_record_cannot_skip_to_a_verification_rung(kyo, status) -> None:
    """KYO-413.c1: `open -> built -> verified` never entered an
    active status, so `active-requires-criteria` never ran and a record with nothing
    to verify reached `kyo next`'s awaiting-verification queue. Each rung refuses it
    by name, and the refusal names the remedy.
    """
    record = new_record(kyo)  # kind=bug, requires_criteria, zero criteria
    result = kyo("move", record, status)
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_VERIFICATION_REQUIRES_CRITERIA in result.err
    assert f"kyo edit {record} --add-criteria-file" in result.err
    assert kyo("show", record, "--json").json["status"] == "open"


def test_adding_a_criterion_clears_the_verification_rung_refusal(kyo) -> None:
    """The remedy the refusal names is the one that clears it."""
    record = new_record(kyo)
    assert kyo("move", record, "built").code == EXIT_CONTRACT
    added = kyo("edit", record, "--add-criteria-file", "-", stdin="the fix ships\n")
    assert added.code == 0, added
    assert kyo("move", record, "built").code == 0
    # KYO-546: entering verified needs a recorded verdict.
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about")
    assert ran.code == 0, ran
    assert kyo("move", record, "verified").code == 0
    assert kyo("show", record, "--json").json["status"] == "verified"


def test_kinds_without_requires_criteria_may_enter_verification_rungs(kyo) -> None:
    record = new_record(kyo, kind="plan", title="milestone 1")
    assert kyo("move", record, "built").code == 0


def test_verification_rung_check_is_on_role_not_name() -> None:
    """Direct against check_transition: both non-active verification roles refuse."""
    ladder = default_ladder()
    for status in ("built", "verified"):
        with pytest.raises(ContractViolation) as exc:
            check_transition(
                record_id="TST-1", kind="bug", from_status="open", to_status=status,
                criteria=[], reason=None, requires_criteria=True, ladder=ladder,
                open_docs_obligations=[],
            )
        assert exc.value.contract == CONTRACT_VERIFICATION_REQUIRES_CRITERIA
