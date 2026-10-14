"""KYO-570: an unsettled `blocks` source holds its destination back.

`kyo next`, the session brief and `kyo dispatch` read no link before this record, so
`kyo link A blocks B` echoed a consequence nothing enforced and a consumer's
orchestrator saw every one of its 16 records listed ready. The blocked class is
computed once, inside `cmd_next._ready_records`, and refused off the same function
(`cmd_next.unsettled_blockers`) by `kyo dispatch`.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import new_record

BLOCKER = "The blocker"
BLOCKED = "The blocked one"


def _blocked_pair(kyo) -> tuple[str, str]:
    blocker = new_record(kyo, title=BLOCKER, criteria=["a"])
    blocked = new_record(kyo, title=BLOCKED, criteria=["b"])
    linked = kyo("link", blocker, "blocks", blocked)
    assert linked.code == 0, linked
    return blocker, blocked


def _settle_done(kyo, record: str) -> None:
    assert kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about").code == 0
    for status in ("working", "built", "verified", "done"):
        assert kyo("move", record, status).code == 0, status


def test_kyo_next_withholds_a_blocked_record_under_its_own_heading(kyo) -> None:
    """KYO-570.c1: not ready; listed under "blocked" with a count; the row names each
    blocker and its status."""
    blocker, blocked = _blocked_pair(kyo)
    data = kyo("next", "--json").json
    assert blocked not in [r["id"] for r in data["ready"]]
    assert blocker in [r["id"] for r in data["ready"]], "the blocker itself stays ready"

    out = kyo("next").out
    assert "1 record(s) blocked by an unsettled record (NOT ready):" in out
    lines = out.splitlines()
    row = next(i for i, ln in enumerate(lines) if ln.strip().startswith(blocked))
    assert f"blocked by {blocker} (open)" in lines[row + 1], out


def test_kyo_next_prints_the_blocked_heading_at_zero(kyo) -> None:
    new_record(kyo, criteria=["a"])
    out = kyo("next").out
    assert "0 record(s) blocked by an unsettled record (NOT ready):" in out


def test_kyo_next_json_carries_the_blocked_group_with_blocker_ids(kyo) -> None:
    """KYO-570.c5."""
    blocker, blocked = _blocked_pair(kyo)
    second = new_record(kyo, title="Another blocker", criteria=["c"])
    assert kyo("link", second, "blocks", blocked).code == 0
    held = kyo("next", "--json").json["blocked"]
    assert [r["id"] for r in held] == [blocked]
    assert [b["id"] for b in held[0]["blockers"]] == [blocker, second]
    assert {b["status"] for b in held[0]["blockers"]} == {"open"}


def test_only_incoming_blocks_edges_hold_a_record_back(kyo) -> None:
    """The source of a `blocks` edge is not held back, and no other edge type is."""
    a = new_record(kyo, title="a", criteria=["a"])
    b = new_record(kyo, title="b", criteria=["b"])
    assert kyo("link", a, "needs-same-change-as", b).code == 0
    data = kyo("next", "--json").json
    assert data["blocked"] == []
    assert {a, b} <= {r["id"] for r in data["ready"]}


def test_the_brief_renders_the_same_blocked_group_from_the_same_call(kyo) -> None:
    """KYO-570.c2: the brief's dispatchable-now section withholds the record and the
    blocked group matches `kyo next` row for row."""
    blocker, blocked = _blocked_pair(kyo)
    nxt = kyo("next", "--json").json
    brief = kyo("brief", "--json").json
    for key in ("ready", "blocked"):
        assert [r["id"] for r in nxt[key]] == [r["id"] for r in brief[key]], key
    assert nxt["blocked"] == brief["blocked"]

    out = kyo("brief").out
    assert "dispatchable now (1)" in out, out
    assert "blocked, NOT dispatchable now (1):" in out
    assert f"blocked by {blocker} (open)" in out
    compact = kyo("brief", "--compact").out
    assert blocked in compact and f"blocked by {blocker} (open)" in compact


def test_a_record_whose_blockers_are_all_settled_is_ready_again(kyo) -> None:
    """KYO-570.c3: one blocker done, the other wont-do -- both settled on the ladder."""
    blocker, blocked = _blocked_pair(kyo)
    second = new_record(kyo, title="Another blocker", criteria=["c"])
    assert kyo("link", second, "blocks", blocked).code == 0

    _settle_done(kyo, blocker)
    held = kyo("next", "--json").json["blocked"]
    assert [b["id"] for b in held[0]["blockers"]] == [second], "a settled blocker drops out"

    assert kyo("wont-do", second, "--why", "dead premise").code == 0
    data = kyo("next", "--json").json
    assert data["blocked"] == []
    assert blocked in [r["id"] for r in data["ready"]]


def test_a_gated_blocker_is_unsettled(kyo) -> None:
    blocker, blocked = _blocked_pair(kyo)
    assert kyo("gate", blocker, "awaiting:human-review").code == 0
    held = kyo("next", "--json").json["blocked"]
    assert [r["id"] for r in held] == [blocked]


def test_a_gated_blocked_record_is_withheld_by_its_gate_and_not_listed_twice(kyo) -> None:
    """The choice the brief asked to be stated: the gate check runs first, so a record
    both gated and blocked is in neither `ready` nor `blocked`."""
    _blocker, blocked = _blocked_pair(kyo)
    assert kyo("gate", blocked, "awaiting:human-review").code == 0
    data = kyo("next", "--json").json
    assert data["blocked"] == []
    assert blocked not in [r["id"] for r in data["ready"]]


def test_dispatch_refuses_a_blocked_record_naming_each_blocker_and_force(
    kyo, git_project: Path
) -> None:
    """KYO-570.c4, the refusal half."""
    blocker, blocked = _blocked_pair(kyo)
    second = new_record(kyo, title="Another blocker", criteria=["c"])
    assert kyo("link", second, "blocks", blocked).code == 0
    refused = kyo("dispatch", blocked)
    assert refused.code == 2, refused
    assert "dispatch-record-blocked" in refused.err
    assert blocker in refused.err and second in refused.err
    assert "--force" in refused.err


def test_force_dispatches_a_blocked_record_and_says_what_it_overrode(
    kyo, git_project: Path
) -> None:
    """KYO-570.c4, the override half."""
    blocker, blocked = _blocked_pair(kyo)
    forced = kyo("dispatch", blocked, "--force", "--json")
    assert forced.code == 0, forced
    assert [b["id"] for b in forced.json["blocked_overridden"]] == [blocker]

    _other_blocker, other = _blocked_pair(kyo)
    echoed = kyo("dispatch", other, "--force")
    assert echoed.code == 0, echoed
    assert "--force overrode the blocked refusal" in echoed.out


def test_settling_the_blocker_clears_the_dispatch_refusal(kyo, git_project: Path) -> None:
    """The remedy the refusal names, run, clears it."""
    blocker, blocked = _blocked_pair(kyo)
    assert kyo("dispatch", blocked).code == 2
    assert kyo("wont-do", blocker, "--why", "dead premise").code == 0
    dispatched = kyo("dispatch", blocked, "--json")
    assert dispatched.code == 0, dispatched
    assert dispatched.json["blocked_overridden"] == []


def test_kyo_link_says_what_blocks_enforces(kyo) -> None:
    """KYO-570.c6."""
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    last = kyo("link", blocker, "blocks", blocked).out.rstrip("\n").splitlines()[-1]
    assert "not offered by `kyo next` or dispatched until" in last
    assert last.index(blocked) < last.index(blocker)
    assert last.endswith("settles."), last
