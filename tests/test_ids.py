"""Law 4 — ids are stable, opaque, monotonic, and never reused.

Two failures this guards against: ``--remove-ac`` renumbering silently rewrote
every citation, and an archived id got reissued so edits landed on a ghost. Each
test here would fail if the mechanism that prevents one of those were removed.
"""

from __future__ import annotations

from .conftest import new_record


def test_record_ids_are_project_prefixed_and_monotonic(kyo) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    assert first == "TST-1"
    assert second == "TST-2"


def test_ids_are_not_reused_after_wont_do(kyo) -> None:
    """The archived-id-reissued incident, made impossible."""
    retired = new_record(kyo, title="dead premise")
    assert kyo("wont-do", retired, "--why", "the premise is gone").code == 0
    fresh = new_record(kyo, title="new work")
    assert fresh != retired
    assert fresh == "TST-2"


def test_allocation_table_is_the_only_source_of_ids(kyo, db) -> None:
    """Ids come from ``allocations``, not from ``MAX(seq) + 1``.

    Deleting rows directly (which the CLI never does) must not let the counter
    walk backwards — that is the difference between the two designs.
    """
    new_record(kyo, title="one")
    new_record(kyo, title="two")
    with db() as conn:
        conn.execute("DELETE FROM criteria WHERE record_id = 'TST-2'")
        conn.execute("DELETE FROM links")
        conn.execute("DELETE FROM events WHERE record_id = 'TST-2'")
        conn.execute("DELETE FROM records WHERE id = 'TST-2'")
        allocation = conn.execute(
            "SELECT next_value FROM allocations WHERE scope = 'record'"
        ).fetchone()["next_value"]
    assert allocation == 3
    assert new_record(kyo, title="three") == "TST-3"


def test_criterion_ids_never_renumber(kyo, write_file) -> None:
    """Voiding c1 must leave c2 called c2 — positions are never ids."""
    record = new_record(kyo, criteria=["first", "second"])
    assert kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", "premise gone",
    ).code == 0
    added = write_file("more.txt", "third\n")
    assert kyo("edit", record, "--add-criteria-file", added).code == 0

    doc = kyo("show", record, "--json").json
    assert [c["id"] for c in doc["criteria"]] == [
        f"{record}.c1",
        f"{record}.c2",
        f"{record}.c3",
    ]
    assert doc["criteria"][1]["text"] == "second"
    # The voided criterion is still there, with its reason — supersede, never delete.
    assert doc["criteria"][0]["state"] == "void-premise-gone"
    assert doc["criteria"][0]["reason"] == "premise gone"


def test_no_command_can_delete_a_criterion(kyo) -> None:
    """There is no ``--remove-ac``; the only retirement path is a void state."""
    help_text = kyo("edit", "--help")
    assert "remove" not in help_text.out
    assert "--state" in help_text.out


def test_child_ids_are_scoped_to_their_record(kyo) -> None:
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    assert kyo("show", first, "--json").json["criteria"][0]["id"] == f"{first}.c1"
    assert kyo("show", second, "--json").json["criteria"][0]["id"] == f"{second}.c1"


def test_gate_ids_keep_climbing_after_a_gate_is_cleared(kyo) -> None:
    record = new_record(kyo)
    first = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    assert kyo("ungate", first).code == 0
    second = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    assert (first, second) == (f"{record}.g1", f"{record}.g2")
