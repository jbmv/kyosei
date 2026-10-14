"""KYO-490: a record cannot settle past an open gate.

A settle, by `kyo accept --close` (KYO-481) or by plain `kyo move`, either clears the
gate with its evidence first or is refused. A move into a completion or retired status
of a record with an open
gate is refused before any change, naming each gate and both remedies — `kyo ungate`
and `--force`. Every other move is unaffected: gates stay orthogonal to status until
the record settles.

`kyo accept --close`'s half lives in tests/test_accept_outcome.py beside the other
`--close` outcomes.
"""

from __future__ import annotations

import pytest

from kyosei.contracts import (
    CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES,
    CriterionView,
    GateView,
    check_transition,
    default_ladder,
)
from kyosei.errors import ContractViolation

from .conftest import new_record

GATE_DO = "run the nightly import against a fresh store\nthen paste the summary"


def _met_record(kyo) -> str:
    """A bug whose one criterion is met, so `done` is otherwise permitted."""
    record = new_record(kyo, criteria=["the import finishes"])
    assert kyo("move", record, "working").code == 0
    verified = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    assert verified.code == 0, verified
    return record


def _gate(kyo, record: str, gate_type: str = "awaiting:human-run") -> str:
    args = ["gate", record, gate_type]
    stdin = None
    if gate_type != "awaiting:human-review":
        # Two lines, through stdin (law 5): the refusal names the first only.
        args += ["--do-file", "-"]
        stdin = GATE_DO + "\n"
    gated = kyo(*args, "--json", stdin=stdin)
    assert gated.code == 0, gated
    return gated.json["gate_id"]


def _status(kyo, record: str) -> str:
    return kyo("show", record, "--json").json["status"]


def _open_gate_ids(kyo, record: str) -> list[str]:
    return [g["id"] for g in kyo("show", record, "--json").json["gates"] if not g["cleared_at"]]


def _assert_refused_naming(result, gate_id: str, gate_type: str) -> None:
    assert result.code == 2, result
    assert CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES in result.err
    assert gate_id in result.err and gate_type in result.err
    assert "run the nightly import against a fresh store" in result.err, "payload line 1"
    assert "then paste the summary" not in result.err, "only the payload's first line"
    assert f"kyo ungate {gate_id} --why" in result.err
    assert "--force" in result.err


# ---------------------------------------------------------------- c1: every entry point


def test_move_to_done_is_refused_over_an_open_gate(kyo) -> None:
    record = _met_record(kyo)
    gate_id = _gate(kyo, record)
    refused = kyo("move", record, "done")
    _assert_refused_naming(refused, gate_id, "awaiting:human-run")
    assert _status(kyo, record) == "working", "refused before any change"


def test_move_to_wont_do_is_refused_over_an_open_gate(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    gate_id = _gate(kyo, record, "holding-for")
    refused = kyo("move", record, "wont-do", "--why", "superseded")
    _assert_refused_naming(refused, gate_id, "holding-for")
    assert _status(kyo, record) == "open"


def test_wont_do_is_refused_over_an_open_gate(kyo) -> None:
    """The `kyo wont-do` route: retired with its awaiting:human-run gate still open."""
    record = new_record(kyo, criteria=["a"])
    gate_id = _gate(kyo, record)
    refused = kyo("wont-do", record, "--why", "superseded")
    _assert_refused_naming(refused, gate_id, "awaiting:human-run")
    assert _status(kyo, record) == "open"


def test_void_is_refused_over_an_open_gate(kyo) -> None:
    record = new_record(kyo, kind="decision", title="use SQLite", criteria=["a store is chosen"])
    gate_id = _gate(kyo, record, "awaiting:event")
    refused = kyo("void", record, "--why", "filed in error")
    _assert_refused_naming(refused, gate_id, "awaiting:event")
    assert _status(kyo, record) == "open"


def test_every_open_gate_is_named_and_a_cleared_one_is_not(kyo) -> None:
    record = _met_record(kyo)
    cleared = _gate(kyo, record, "awaiting:human-review")
    assert kyo("ungate", cleared, "--why", "reviewed").code == 0
    first = _gate(kyo, record, "awaiting:event")
    second = _gate(kyo, record, "awaiting:human-review")
    refused = kyo("move", record, "done")
    assert refused.code == 2, refused
    assert first in refused.err and second in refused.err
    assert cleared not in refused.err
    assert "(no payload)" in refused.err, "a review gate carries none, and says so"


def test_the_contract_itself_refuses_both_settling_roles() -> None:
    """The unit form: removing the raise fails this whatever the callers do."""
    ladder = default_ladder()
    gate = GateView(id="TST-1.g1", gate_type="awaiting:human-run", detail=GATE_DO)
    common = {
        "record_id": "TST-1",
        "kind": "bug",
        "from_status": "working",
        "criteria": [CriterionView(id="TST-1.c1", text="a", state="met")],
        "requires_criteria": True,
        "ladder": ladder,
        "open_docs_obligations": [],
    }
    for to_status, reason in ((ladder.complete, None), (ladder.retired, "moot")):
        with pytest.raises(ContractViolation) as refused:
            check_transition(to_status=to_status, reason=reason, open_gates=[gate], **common)
        assert refused.value.contract == CONTRACT_SETTLED_REQUIRES_NO_OPEN_GATES
    check_transition(to_status=ladder.complete, reason=None, open_gates=[], **common)
    check_transition(to_status="built", reason=None, open_gates=[gate], **common)


# ---------------------------------------------------------------- c2: the remedies clear it


def test_ungating_then_repeating_the_move_succeeds(kyo) -> None:
    record = _met_record(kyo)
    gate_id = _gate(kyo, record)
    assert kyo("move", record, "done").code == 2
    assert kyo("ungate", gate_id, "--why", "ran it; the summary is on the record").code == 0
    moved = kyo("move", record, "done")
    assert moved.code == 0, moved
    assert _status(kyo, record) == "done"


@pytest.mark.parametrize(
    ("argv", "status"),
    [
        (("move", "{r}", "done"), "done"),
        (("move", "{r}", "wont-do", "--why", "superseded"), "wont-do"),
        (("wont-do", "{r}", "--why", "superseded"), "wont-do"),
    ],
)
def test_force_settles_and_leaves_the_gate_open(kyo, argv, status) -> None:
    record = _met_record(kyo)
    gate_id = _gate(kyo, record)
    forced = kyo(*(a.format(r=record) for a in argv), "--force", "--json")
    assert forced.code == 0, forced
    assert forced.json["forced_over_gates"] == [gate_id]
    assert _status(kyo, record) == status
    assert _open_gate_ids(kyo, record) == [gate_id], "the gate is left open, not cleared"
    text = kyo("show", record).out
    assert gate_id in text


def test_force_on_void_settles_and_leaves_the_gate_open(kyo) -> None:
    record = new_record(kyo, kind="decision", title="use SQLite", criteria=["a store is chosen"])
    gate_id = _gate(kyo, record, "awaiting:event")
    forced = kyo("void", record, "--why", "filed in error", "--force")
    assert forced.code == 0, forced
    assert gate_id in forced.out and "--force" in forced.out, "said, not absorbed"
    assert _open_gate_ids(kyo, record) == [gate_id]


# ---------------------------------------------------------------- c4: other moves unaffected


def test_non_settling_moves_ignore_open_gates(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    gate_id = _gate(kyo, record)
    # KYO-546: entering verified needs a recorded verdict; an unmet one changes nothing.
    ran = kyo("verify", f"{record}.c1", "--verdict", "unmet-unbuilt", "--method", "reasoned-about")
    assert ran.code == 0, ran
    for status in ("working", "built", "verified", "working", "open"):
        moved = kyo("move", record, status)
        assert moved.code == 0, (status, moved)
    assert _open_gate_ids(kyo, record) == [gate_id]
