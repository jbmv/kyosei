"""``kyo list`` — one line per record, and a counted/not-counted summary.

``policy.counted``'s first consumer (law 2, kyosei/kinds.yml).
"""

from __future__ import annotations

from pathlib import Path

from kyosei import query
from kyosei.contracts import STANDING_CURRENT, STANDING_VOID, default_ladder

from .conftest import new_record, verify_criterion
from .test_statuses import RENAMED_RETIREMENT, write_statuses


def test_lists_every_record(kyo) -> None:
    a = new_record(kyo, title="one")
    b = new_record(kyo, kind="plan", title="two")
    ids = {r["id"] for r in kyo("list", "--json").json["records"]}
    assert ids == {a, b}


def test_filters_by_kind(kyo) -> None:
    new_record(kyo, kind="bug", title="a bug")
    plan = new_record(kyo, kind="plan", title="a plan")
    result = kyo("list", "--kind", "plan", "--json").json
    assert [r["id"] for r in result["records"]] == [plan]


def test_filters_by_status(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    other = new_record(kyo, criteria=["b"])
    kyo("move", record, "working")
    result = kyo("list", "--status", "working", "--json").json
    assert [r["id"] for r in result["records"]] == [record]
    assert other not in [r["id"] for r in result["records"]]


def test_status_open_returns_no_knowledge_record(kyo) -> None:
    """KYO-99.c1. `--status open` used to answer from the stored column, which a
    knowledge record still carries the initial status name in — so the question "what
    work is open" returned every decision ever filed, each of them printed `current` by
    the very listing that matched them on `open`."""
    decision = new_record(kyo, kind="decision", title="how statuses are stored")
    work = new_record(kyo, kind="bug", title="a real open")

    ladder = default_ladder()
    opens = [r["id"] for r in kyo("list", "--status", ladder.initial, "--json").json["records"]]
    assert opens == [work]
    assert decision not in opens

    # And it is still reachable — by the word the listing actually prints for it.
    current = [
        r["id"] for r in kyo("list", "--status", STANDING_CURRENT, "--json").json["records"]
    ]
    assert current == [decision]


def test_the_open_filter_does_not_change_what_is_stored(kyo) -> None:
    """KYO-99.c2. The fix is filter-side: the decision still *stores* the initial status
    name (KYO-101.r1 — the ladder is the work archetype's, and the shared column keeps
    holding it), still *reads* `current`, and only the filter moved."""
    decision = new_record(kyo, kind="decision", title="how statuses are stored")
    shown = kyo("show", decision, "--json").json
    assert shown["status"] == default_ladder().initial
    assert shown["standing"] == STANDING_CURRENT

    row = next(r for r in kyo("list", "--json").json["records"] if r["id"] == decision)
    assert (row["status"], row["standing"]) == (default_ladder().initial, STANDING_CURRENT)


def test_filters_by_every_declared_work_status(kyo) -> None:
    """KYO-99.c3. Moving the filter to the rendered word must not cost any rung of the
    ladder: every declared status still selects the records that display it."""
    ladder = default_ladder()
    record = new_record(kyo, criteria=["it works"])
    verify_criterion(kyo, f"{record}.c1", "met")
    for name in ladder.names:
        if name == ladder.retired:
            continue
        if name != ladder.initial:
            assert kyo("move", record, name).code == 0
        listed = [r["id"] for r in kyo("list", "--status", name, "--json").json["records"]]
        assert listed == [record], name

    retired = new_record(kyo, criteria=["never mind"])
    assert kyo("wont-do", retired, "--why", "the premise died").code == 0
    listed = [r["id"] for r in kyo("list", "--status", ladder.retired, "--json").json["records"]]
    assert listed == [retired]


def test_the_status_filter_follows_a_renamed_ladder(kyo, project: Path) -> None:
    """KYO-99.c3, the role half. Nothing in the filter compares against a status name
    frozen into the code: a project that renames its retirement terminal still finds its
    voided knowledge records under `void`, and never under the new name."""
    write_statuses(project, RENAMED_RETIREMENT)
    knowledge = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    work = new_record(kyo, kind="bug", title="a real open")
    assert kyo("void", knowledge, "--why", "measured the wrong thing").code == 0
    assert kyo("wont-do", work, "--why", "not worth doing").code == 0

    shelved = [r["id"] for r in kyo("list", "--status", "shelved", "--json").json["records"]]
    assert shelved == [work]
    voided = [r["id"] for r in kyo("list", "--status", STANDING_VOID, "--json").json["records"]]
    assert voided == [knowledge]


def test_unknown_kind_filter_fails_loudly(kyo) -> None:
    result = kyo("list", "--kind", "ghost-kind")
    assert result.code == 1
    assert "ghost-kind" in result.err


def test_unknown_status_filter_is_a_usage_error(kyo) -> None:
    """KYO-99.c3: and the refusal still names every word that would have worked —
    the project's declared ladder, plus the two knowledge standings."""
    result = kyo("list", "--status", "shipped")
    assert result.code == 64
    for name in default_ladder().names:
        assert name in result.err
    assert STANDING_CURRENT in result.err
    assert STANDING_VOID in result.err


def test_summary_separates_counted_from_not_counted(kyo) -> None:
    """``bug`` is counted; ``plan`` and ``decision`` are not (defaults/kinds.yml)."""
    new_record(kyo, kind="bug", title="counted")
    new_record(kyo, kind="plan", title="not counted")
    new_record(kyo, kind="decision", title="also not counted")
    summary = kyo("list", "--json").json["summary"]
    assert summary == {"total": 3, "counted": 1, "not_counted": 2}


def test_gates_open_count_is_shown(kyo) -> None:
    record = new_record(kyo)
    kyo("gate", record, "awaiting:human-review")
    gate_id = kyo("show", record, "--json").json["gates"][0]["id"]
    kyo("gate", record, "holding-for", "--do", "the measurement finishes")
    row = next(r for r in kyo("list", "--json").json["records"] if r["id"] == record)
    assert row["gates_open"] == 2
    kyo("ungate", gate_id, "--why", "reviewed")
    row = next(r for r in kyo("list", "--json").json["records"] if r["id"] == record)
    assert row["gates_open"] == 1


def test_human_output_has_one_line_per_record(kyo) -> None:
    record = new_record(kyo, title="Widget explodes")
    out = kyo("list").out
    assert record in out
    assert "Widget explodes" in out


def test_the_listing_is_the_shared_engine(kyo) -> None:
    """KYO-108.c4, from this side. ``kyo list`` no longer owns a query: it builds a
    :class:`kyosei.query.Filters` out of its flags and renders what the engine returns,
    which is the same engine every saved Base runs on. The row it prints carries
    exactly the engine's field set — see ``tests/test_bases.py`` for the other half.
    """
    record = new_record(kyo, title="Widget explodes")
    [row] = kyo("list", "--json").json["records"]
    assert row["id"] == record
    assert set(row) == set(query.ROW_FIELDS)


def test_every_filter_flag_is_a_declared_engine_filter(kyo) -> None:
    """No flag on this command narrows the listing by anything the engine — and
    therefore a Base — does not know about. The converse is asserted in
    ``tests/test_bases.py``; together they are "no filter available to one surface and
    not the other"."""
    for name in query.FILTER_NAMES:
        flag = query.filter_flag(name)
        # Every one is accepted by the parser and refuses an undeclared value rather
        # than answering with an empty listing (law 6).
        assert kyo("list", flag, "definitely-not-declared").code != 0, flag


def test_list_rows_carry_the_records_own_timestamps(kyo) -> None:
    """KYO-467.c2: `kyo list --json` rows carry `created_at` and `updated_at`, the same
    stamps `kyo show` reports, so a records list with no Base selected can sort by
    either; a move moves `updated_at` and never `created_at`."""
    first = new_record(kyo, kind="task", title="first", criteria=["it holds"])
    second = new_record(kyo, kind="task", title="second", criteria=["it holds"])
    moved = kyo("move", first, "working")
    assert moved.code == 0, moved
    rows = {row["id"]: row for row in kyo("list", "--json").json["records"]}
    for record in (first, second):
        shown = kyo("show", record, "--json", "--full").json
        assert rows[record]["created_at"] == shown["created_at"]
        assert rows[record]["updated_at"] == shown["updated_at"]
    assert rows[first]["updated_at"] >= rows[first]["created_at"]
    assert rows[first]["updated_at"] >= rows[second]["updated_at"]
