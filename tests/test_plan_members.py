"""A plan can show its members.

A consumer report: `plan` is the one kind
whose completion is *defined* as a status distribution over its member records
(``kinds.yml``: ``requires_criteria: false``, ``dispatchable: false``) — and
nothing rendered that distribution. `kyo show` printed criteria (0 by policy),
touches (0), gates, claims, dispatches, reports and flags — every empty section a
plan is defined not to have — and no members. `kyo list` had no ``--plan``. The
html page carried no link to one.

Membership is stored on the MEMBER (``plan_id``), so nothing walked the relation
the other way and reading a plan meant globbing ``kyosei/export/``. The natural
first question about a plan, which records are its members, had no CLI answer.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import new_record, verify_criterion


def _plan_with_members(kyo) -> tuple[str, list[str]]:
    # `plan` does not carry `intake` in the default registry, so `kyo new plan` is
    # refused since KYO-43; `new_record` opens the registry's own escape hatch.
    plan = new_record(kyo, kind="plan", title="A plan")
    members = []
    for n in range(1, 4):
        # Criteria so a member can actually move status — `bug` requires them, which
        # is KYO-26's own mechanism refusing `working` without them.
        result = kyo(
            "new", "bug", "--title", f"member {n}", "--plan", plan,
            "--criteria-file", "-", "--json",
            stdin="it works\n",
        )
        assert result.code == 0, result
        members.append(result.json["record"]["id"])
    return plan, members


def test_list_filters_by_plan(kyo) -> None:
    plan, members = _plan_with_members(kyo)
    new_record(kyo, kind="bug", title="not a member")

    result = kyo("list", "--plan", plan, "--json")
    assert result.code == 0, result
    assert [row["id"] for row in result.json["records"]] == members


def test_list_plan_refuses_an_id_that_does_not_exist(kyo) -> None:
    """Law 6: an id that does not exist is an error, not an empty list — an empty
    result would read as "this plan has no members".

    KYO-271: routed through the same `_require_plan` as `kyo new --plan` and
    `kyo edit --plan` (KYO-237), so this is exit 2 (`ContractViolation`), not the
    generic exit 1 `kyo list` used before — see the empty-id and not-a-plan-kind
    siblings below, and ``tests/test_records.py::test_an_unknown_plan_fails_loudly``
    for the write-side originals this now matches."""
    result = kyo("list", "--plan", "TST-999")
    assert result.code == 2, result
    assert "TST-999" in result.err


def test_list_plan_refuses_an_empty_plan_id(kyo) -> None:
    result = kyo("list", "--plan", "")
    assert result.code == 2, result
    assert "plan" in result.err


def test_list_plan_refuses_a_plan_id_that_is_not_a_plan_kind(kyo) -> None:
    not_a_plan = new_record(kyo, kind="bug", title="an ordinary bug")
    result = kyo("list", "--plan", not_a_plan)
    assert result.code == 2, result
    assert not_a_plan in result.err
    assert "not a plan" in result.err


def test_show_renders_the_members_and_their_distribution(kyo) -> None:
    plan, members = _plan_with_members(kyo)
    kyo("move", members[0], "working")

    result = kyo("show", plan)
    assert result.code == 0, result
    assert "members (3)" in result.out
    assert "open:2" in result.out and "working:1" in result.out
    for member in members:
        assert member in result.out


def test_show_json_carries_the_members(kyo) -> None:
    plan, members = _plan_with_members(kyo)
    shown = kyo("show", plan, "--json")
    assert [m["id"] for m in shown.json["members"]] == members
    assert all("status" in m for m in shown.json["members"])


def test_show_json_members_carry_their_standing(kyo) -> None:
    """KYO-280: the text renderer's distribution line already had every member's
    standing; the JSON envelope dropped it, so the desktop's member pill was empty.
    Each member now rides with `standing` beside `status`, the record's own rule
    (KYO-44.c2)."""
    plan, members = _plan_with_members(kyo)
    kyo("move", members[0], "working")
    shown = kyo("show", plan, "--json")
    by_id = {m["id"]: m for m in shown.json["members"]}
    assert all(m["standing"] for m in by_id.values()), by_id
    assert by_id[members[0]]["standing"] == "working"
    assert by_id[members[1]]["standing"] == "open"
    assert shown.json["standing"] == "open"


def test_a_record_with_no_members_renders_no_members_section(kyo) -> None:
    """Every record would otherwise gain an empty section — the exact complaint
    the consumer report makes about the sections a plan is defined not to have."""
    record = new_record(kyo, kind="bug", title="Ordinary")
    assert "members (" not in kyo("show", record).out


def test_membership_is_not_written_into_the_export_document(kyo, project: Path) -> None:
    """Derived at display time on purpose. In the export it would mean a member's
    `--plan` edit silently stales the *plan's* export — a drift class this store
    does not otherwise have, and `kyo doctor` would start failing for it."""
    plan, _members = _plan_with_members(kyo)
    exported = (project / "kyosei" / "export" / f"{plan}.json").read_text()
    assert "members" not in exported
    assert kyo("doctor").code == 0


# --------------------------------------------------------------------- KYO-237
#
# `kyo new --plan` and `kyo edit --plan` reach the same `plan_id` column, and used
# to validate it inconsistently: `kyo new` skipped validation entirely for an empty
# string (`if args.plan:` is false for `''`), reaching the `plan_id` FOREIGN KEY
# unchecked and surfacing a raw `sqlite3.IntegrityError` -- a trace naming no id, no
# remedy, for an INSERT that had already rolled back by the time anyone read it.
# `kyo edit` checked existence (`args.plan is not None`) but, like `kyo new`, never
# checked that the referenced record was actually a `plan` kind. Three distinct
# mistakes -- empty, unknown, and "real record, wrong kind" -- now get three
# distinct sentences (law 6), a shared exit code (2, `ContractViolation`, on the
# same reasoning KYO-68.c3 gave the kind-registry refusals beside these call sites),
# and run before any write.


def test_new_refuses_an_empty_plan_id(kyo) -> None:
    before = {row["id"] for row in kyo("list", "--json").json["records"]}
    result = kyo("new", "bug", "--title", "should not be created", "--plan", "")
    assert result.code == 2, result
    assert "plan" in result.err
    after = {row["id"] for row in kyo("list", "--json").json["records"]}
    assert after == before


def test_new_refuses_an_unknown_plan_id(kyo) -> None:
    before = {row["id"] for row in kyo("list", "--json").json["records"]}
    result = kyo("new", "bug", "--title", "should not be created", "--plan", "TST-999")
    assert result.code == 2, result
    assert "TST-999" in result.err
    after = {row["id"] for row in kyo("list", "--json").json["records"]}
    assert after == before


def test_new_refuses_a_plan_id_that_is_not_a_plan_kind(kyo) -> None:
    not_a_plan = new_record(kyo, kind="bug", title="an ordinary bug")
    before = {row["id"] for row in kyo("list", "--json").json["records"]}
    result = kyo(
        "new", "bug", "--title", "should not be created", "--plan", not_a_plan
    )
    assert result.code == 2, result
    assert not_a_plan in result.err
    assert "not a plan" in result.err
    after = {row["id"] for row in kyo("list", "--json").json["records"]}
    assert after == before


def test_edit_refuses_an_empty_plan_id(kyo) -> None:
    record = new_record(kyo)
    before = kyo("show", record, "--json").json["plan"]
    result = kyo("edit", record, "--plan", "")
    assert result.code == 2, result
    assert "plan" in result.err
    assert kyo("show", record, "--json").json["plan"] == before


def test_edit_refuses_an_unknown_plan_id(kyo) -> None:
    record = new_record(kyo)
    before = kyo("show", record, "--json").json["plan"]
    result = kyo("edit", record, "--plan", "TST-999")
    assert result.code == 2, result
    assert "TST-999" in result.err
    assert kyo("show", record, "--json").json["plan"] == before


def test_edit_refuses_a_plan_id_that_is_not_a_plan_kind(kyo) -> None:
    record = new_record(kyo)
    not_a_plan = new_record(kyo, kind="bug", title="an ordinary bug")
    before = kyo("show", record, "--json").json["plan"]
    result = kyo("edit", record, "--plan", not_a_plan)
    assert result.code == 2, result
    assert not_a_plan in result.err
    assert "not a plan" in result.err
    assert kyo("show", record, "--json").json["plan"] == before


# --------------------------------------------------------------------- KYO-308
#
# `kyo move <plan> done` used to succeed over a plan whose members were still open,
# printing only the plan's own transition — the one fact that matters at the moment a
# plan closes (its members' status distribution) was the one thing the command did not
# say. KYO-308.c1: a completion move now states that distribution, refuses by name when
# a member is unsettled, and each of the refusal's two named remedies (settle, or
# detach with `kyo edit --no-plan`) clears it — plus the third, `--force`, which moves
# the plan anyway and records that it closed over open members.


def test_move_to_done_refuses_a_plan_with_open_members(kyo) -> None:
    plan, members = _plan_with_members(kyo)
    result = kyo("move", plan, "done")
    assert result.code == 2, result
    for member in members:
        assert member in result.err
    assert "--force" in result.err
    assert "kyo edit <MEMBER-ID> --no-plan" in result.err
    # refused before anything moved
    assert kyo("show", plan, "--json").json["status"] == "open"


def test_move_to_done_names_only_the_unsettled_members(kyo) -> None:
    plan, members = _plan_with_members(kyo)
    verify_criterion(kyo, f"{members[0]}.c1", "met")
    assert kyo("move", members[0], "done").code == 0
    result = kyo("move", plan, "done")
    assert result.code == 2, result
    assert members[0] not in result.err
    assert members[1] in result.err
    assert members[2] in result.err


def test_move_to_done_remedy_settle_the_members(kyo) -> None:
    """The first named remedy: settle every member, then the move succeeds exactly as
    it always did, and now also prints the distribution (KYO-308.c1, "checked")."""
    plan, members = _plan_with_members(kyo)
    for member in members:
        verify_criterion(kyo, f"{member}.c1", "met")
        assert kyo("move", member, "done").code == 0, member
    result = kyo("move", plan, "done")
    assert result.code == 0, result
    assert "plan members (3)" in result.out
    assert "done:3" in result.out
    assert kyo("show", plan, "--json").json["status"] == "done"
    # no forced-closure note: nothing was open when this moved
    assert kyo("show", plan, "--json").json["notes"] == []


def test_move_to_done_remedy_detach_the_members(kyo) -> None:
    """The second named remedy: `kyo edit --no-plan` detaches a member, and a plan
    with none left to settle moves exactly as a plan always has."""
    plan, members = _plan_with_members(kyo)
    for member in members:
        assert kyo("edit", member, "--no-plan").code == 0, member
    result = kyo("move", plan, "done")
    assert result.code == 0, result
    assert "plan members (0)" in result.out


def test_move_to_done_force_closes_over_open_members_and_records_it(kyo) -> None:
    plan, members = _plan_with_members(kyo)
    result = kyo("move", plan, "done", "--force")
    assert result.code == 0, result
    assert kyo("show", plan, "--json").json["status"] == "done"
    for member in members:
        assert member in result.out

    shown = kyo("show", plan, "--json").json
    assert len(shown["notes"]) == 1
    note_text = shown["notes"][0]["text"]
    assert "--force" in note_text
    for member in members:
        assert member in note_text
    # a later reader can find it without reading JSON, too
    assert shown["notes"][0]["id"] in kyo("show", plan).out


def test_move_to_done_without_force_writes_no_note(kyo) -> None:
    plan, _members = _plan_with_members(kyo)
    assert kyo("move", plan, "done").code == 2
    assert kyo("show", plan, "--json").json["notes"] == []


def test_move_to_a_non_completion_status_does_not_gate_on_members(kyo) -> None:
    """The refusal is specifically about *completion*; retiring a plan makes no claim
    that its members finished, so `kyo wont-do`'s own contract (a reason) is the only
    one in play."""
    plan, _members = _plan_with_members(kyo)
    result = kyo("move", plan, "wont-do", "--why", "abandoned")
    assert result.code == 0, result
    assert kyo("show", plan, "--json").json["status"] == "wont-do"


def test_move_to_done_treats_an_unvoided_knowledge_member_as_unsettled(kyo) -> None:
    """Knowledge archetype rule chosen for KYO-308.c1: a knowledge member counts as
    unsettled until it is voided (there is no 'complete' rung on its ladder), read off
    the ladder's own roles with no archetype lookup at all — `kyo void` is the only
    move that ever leaves a knowledge record's initial status (`check_archetype_allows_
    status_flow` refuses every other one), so `StatusLadder.is_settled` already reads
    'open' as unsettled and 'voided' as settled for it, the same as any work member."""
    plan = new_record(kyo, kind="plan", title="plan with a decision")
    decision = new_record(kyo, kind="decision", title="a decision")
    assert kyo("edit", decision, "--plan", plan).code == 0

    refused = kyo("move", plan, "done")
    assert refused.code == 2, refused
    assert decision in refused.err

    assert kyo("void", decision, "--why", "premise died").code == 0
    result = kyo("move", plan, "done")
    assert result.code == 0, result
    assert kyo("show", plan, "--json").json["status"] == "done"


def test_move_to_done_on_an_already_retired_plan_reports_terminal_not_members(kyo) -> None:
    """A terminal status cannot be left at all, `--force` or not — the more
    fundamental refusal, and this check must defer to it rather than report
    'unsettled members' over a move nothing could ever complete."""
    plan, _members = _plan_with_members(kyo)
    assert kyo("move", plan, "wont-do", "--why", "abandoned").code == 0

    result = kyo("move", plan, "done")
    assert result.code == 2, result
    assert "terminal" in result.err

    forced = kyo("move", plan, "done", "--force")
    assert forced.code == 2, forced
    assert "terminal" in forced.err


def test_move_non_plan_record_is_unaffected_by_the_plan_members_check(kyo) -> None:
    """The completion move of a record that is not a plan carries none of this new
    machinery — no distribution line, no note, byte-for-byte the same move it always
    was (KYO-308's task: 'a non-plan record's move must be byte-for-byte unchanged')."""
    record = new_record(kyo, criteria=["returns 200"])
    verify_criterion(kyo, f"{record}.c1", "met")
    result = kyo("move", record, "done")
    assert result.code == 0, result
    assert "plan members" not in result.out
    assert kyo("show", record, "--json").json["notes"] == []

