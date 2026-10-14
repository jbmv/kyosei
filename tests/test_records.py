"""Record creation, editing, plan membership, and ``show``."""

from __future__ import annotations

from .conftest import new_record, verify_criterion


def test_a_new_record_starts_open(kyo) -> None:
    record = new_record(kyo)
    document = kyo("show", record, "--json").json
    assert document["status"] == "open"
    assert document["kind"] == "bug"
    assert document["description"] == ""
    assert document["plan"] is None
    assert document["wont_do_reason"] is None


def test_kind_must_be_a_lowercase_slug(kyo) -> None:
    result = kyo("new", "Not A Kind", "--title", "x")
    assert result.code == 64
    assert "lowercase slug" in result.err


def test_kind_must_exist_in_the_registry(kyo) -> None:
    """Milestone 2: a well-shaped but undefined kind is a loud, listed error."""
    result = kyo("new", "spike-investigation", "--title", "look into it")
    assert result.code == 1
    assert "spike-investigation" in result.err
    assert "bug" in result.err  # the known-kinds list, so the caller can pick a real one


def test_criteria_arrive_one_per_line(kyo) -> None:
    record = new_record(kyo, criteria=["returns 200", "logs nothing", "under 200ms"])
    texts = [c["text"] for c in kyo("show", record, "--json").json["criteria"]]
    assert texts == ["returns 200", "logs nothing", "under 200ms"]


def test_a_plan_is_just_a_record(kyo) -> None:
    """There is no milestone entity — a plan is a record that owns members."""
    plan = new_record(kyo, kind="plan", title="milestone 1")
    member = kyo(
        "new", "bug", "--title", "a member", "--plan", plan, "--json"
    ).json["record"]
    assert member["plan"] == plan


def test_joining_and_leaving_a_plan(kyo) -> None:
    plan = new_record(kyo, kind="plan", title="milestone 1")
    record = new_record(kyo)
    assert kyo("edit", record, "--plan", plan).code == 0
    assert kyo("show", record, "--json").json["plan"] == plan
    assert kyo("edit", record, "--no-plan").code == 0
    assert kyo("show", record, "--json").json["plan"] is None


def test_a_record_cannot_be_its_own_plan(kyo) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--plan", record)
    assert result.code == 1
    assert "own plan" in result.err


def test_an_unknown_plan_fails_loudly(kyo) -> None:
    """KYO-237: raised to exit 2 (`ContractViolation`) so this refusal reads as the
    same class of problem as the kind-registry ones beside it — see
    ``tests/test_plan_members.py`` for the empty-id and not-a-plan-kind siblings
    this refusal now shares a home with."""
    assert kyo("new", "bug", "--title", "x", "--plan", "TST-404").code == 2
    record = new_record(kyo)
    assert kyo("edit", record, "--plan", "TST-404").code == 2


def test_plan_and_no_plan_together_are_refused(kyo) -> None:
    plan = new_record(kyo, kind="plan", title="p")
    record = new_record(kyo)
    result = kyo("edit", record, "--plan", plan, "--no-plan")
    assert result.code == 64


def test_show_renders_every_section(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["returns 200"])
    other = new_record(kyo, title="two")
    kyo("edit", record, "--description-file", write_file("d.md", "why this matters"))
    kyo("edit", record, "--note-file", write_file("n.md", "a thought"))
    kyo("gate", record, "awaiting:human-run", "--do", "run make test")
    kyo("link", record, "blocks", other)
    kyo(
        "claim", record, "--level", "asserted", "--method", "code read",
        "--file", "-", stdin="the widget explodes when the cache is cold",
    )
    verify_criterion(kyo, f"{record}.c1", "met", evidence="200 OK, 12ms")

    out = kyo("show", record).out
    sections = (
        "description:", "criteria (1)", "gates (1 open of 1)", "links (1)", "notes (1",
        "claims (1)", "evidence artifacts (1)",
    )
    for expected in sections:
        assert expected in out, expected
    assert "run make test" in out
    assert "why this matters" in out
    assert "the widget explodes when the cache is cold" in out


def test_several_changes_in_one_edit_apply_together(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit", record,
        "--title", "Renamed",
        "--description-file", write_file("d.md", "new description"),
        "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "not needed after all",
    )
    assert result.code == 0
    document = kyo("show", record, "--json").json
    assert document["title"] == "Renamed"
    assert document["description"] == "new description"
    assert document["criteria"][0]["state"] == "void-withdrawn"


def test_state_without_criterion_is_refused(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("edit", record, "--state", "met").code == 64
    assert kyo("edit", record, "--criterion", f"{record}.c1").code == 64
