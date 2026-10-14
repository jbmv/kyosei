"""Law 10 — criteria are states, not booleans."""

from __future__ import annotations

from pathlib import Path

from kyosei import report_schema
from kyosei.contracts import (
    CRITERION_STATES,
    DEFAULT_CRITERION_STATE,
    EDIT_DIRECT_CRITERION_STATES,
    VERIFY_VERDICTS,
)
from kyosei.errors import EXIT_CONTRACT

from .conftest import new_record, verify_criterion

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_new_criteria_default_to_unmet_unbuilt(kyo) -> None:
    record = new_record(kyo, criteria=["the endpoint returns 200"])
    criterion = kyo("show", record, "--json").json["criteria"][0]
    assert criterion["state"] == DEFAULT_CRITERION_STATE == "unmet-unbuilt"
    assert criterion["reason"] is None


def test_all_five_states_are_reachable(kyo) -> None:
    """Milestone 4: verified facts (``VERIFY_VERDICTS``) come from ``kyo verify``;
    only the two void states may still be set directly via ``kyo edit`` (KYO-4.c3).
    """
    record = new_record(kyo, criteria=["a", "b", "c", "d", "e"])
    for index, state in enumerate(CRITERION_STATES, start=1):
        criterion_id = f"{record}.c{index}"
        if state in VERIFY_VERDICTS:
            why = f"reason for {state}" if state == "unmet-unobserved" else None
            assert verify_criterion(kyo, criterion_id, state, why=why).code == 0, state
        else:
            assert state in EDIT_DIRECT_CRITERION_STATES
            args = ["edit", record, "--criterion", criterion_id, "--state", state]
            args += ["--why", f"reason for {state}"]
            assert kyo(*args).code == 0, state
    assert [c["state"] for c in kyo("show", record, "--json").json["criteria"]] == list(
        CRITERION_STATES
    )


def test_void_withdrawn_requires_a_reason(kyo) -> None:
    """Deliberately dropped, premise intact — distinct from void-premise-gone so the
    first can never be used as a lie about the second (law 4)."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn")
    assert result.code == EXIT_CONTRACT
    assert "requires-reason" in result.err
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "decided this wasn't worth pursuing",
    )
    assert result.code == 0


def test_states_match_the_report_schema_enum() -> None:
    """Law 3: derive agreement, don't hope for it.

    ``report.schema.json``'s ``criterionVerdict.verdict`` enum is the other
    place this state set is written down; this test fails the moment the two drift.

    KYO-149: read through :func:`kyosei.report_schema.schema_document`, the same
    call ``kyo report-schema`` prints from, rather than by re-spelling a path. The
    schema moved into the package that release; a test holding its own copy of the
    path would have had to be found and edited, and the one that was missed would
    have gone on reading a file that was no longer there.
    """
    schema = report_schema.schema_document()
    schema_states = set(schema["$defs"]["criterionVerdict"]["properties"]["verdict"]["enum"])
    assert schema_states == set(CRITERION_STATES)


def test_unobserved_and_void_require_a_reason(kyo) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unobserved", "--method", "reasoned-about"
    )
    assert result.code == EXIT_CONTRACT
    assert "requires-reason" in result.err

    result = kyo("edit", record, "--criterion", f"{record}.c2", "--state", "void-premise-gone")
    assert result.code == EXIT_CONTRACT
    assert "requires-reason" in result.err


def test_unknown_criterion_state_is_refused(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("edit", record, "--criterion", f"{record}.c1", "--state", "done")
    assert result.code == EXIT_CONTRACT
    assert "criterion-state-must-be-known" in result.err


def test_criterion_must_belong_to_the_named_record(kyo) -> None:
    """The check that `--criterion` names a criterion of the record being edited —
    exercised here on a void state, since ``met`` (and the other verified facts)
    now go through `kyo verify`, which takes no separate record argument to
    mismatch against."""
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    result = kyo(
        "edit", second, "--criterion", f"{first}.c1", "--state", "void-withdrawn",
        "--why", "wrong record",
    )
    assert result.code == 1
    assert first in result.err


def test_unknown_criterion_id_fails_loudly(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("verify", f"{record}.c99", "--verdict", "met", "--method", "reasoned-about")
    assert result.code == 1
    assert "no criterion" in result.err


def test_reason_may_arrive_from_a_file(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    reason = "The premise died when the vendor dropped the endpoint.\nSee TST-9."
    path = write_file("reason.md", reason + "\n")
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why-file", path,
    )
    assert result.code == 0
    assert kyo("show", record, "--json").json["criteria"][0]["reason"] == reason


def test_state_change_is_echoed_as_a_transition(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about"
    )
    assert "stored criterion state: unmet-unbuilt -> met" in result.out


def test_edit_refuses_to_set_a_verified_state_directly(kyo) -> None:
    """KYO-4.c3: ``met``/``unmet-unbuilt``/``unmet-unobserved`` must come from
    ``kyo verify`` (method + evidence), never a bare ``kyo edit --state`` flip."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("edit", record, "--criterion", f"{record}.c1", "--state", "met")
    assert result.code == 64
    assert "kyo verify" in result.err
