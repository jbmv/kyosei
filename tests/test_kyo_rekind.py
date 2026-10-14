"""KYO-509: `kyo edit <id> --kind <kind>` re-kinds a record.

Before it, nothing changed a record's kind, so a kind removed from ``kinds.yml`` left
its records as a `kyo doctor` problem at every stop with only two ways out: put the
kind back, or retire the records. The change is a plain column update carried by the
ordinary edit event and the record's export document -- ``kind`` is already in both,
and `kyo rebuild` reads it back from the export -- so these tests prove it survives a
rebuild rather than trusting that it does.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from kyosei.cmd_records import (
    CONTRACT_REKIND_ARCHETYPE_DIFFERS,
    CONTRACT_REKIND_STRANDS_STATE,
    CONTRACT_REKIND_UNKNOWN_KIND,
)
from kyosei.errors import EXIT_CONTRACT

from .conftest import new_record


def _remove_kind(project: Path, name: str) -> None:
    """Delete ``name`` from the project's kinds.yml, and every edge naming it, the way a
    maintainer retiring a kind would (the loader refuses a dangling edge)."""
    path = project / "kyosei" / "kinds.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["kinds"][name]
    for entry in data["kinds"].values():
        for key in ("accepts_from", "produces"):
            if key in entry:
                entry[key] = [token for token in entry[key] if token != name]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _kind_registry_problems(kyo) -> list[str]:
    result = kyo("doctor", "--json")
    return result.json["checks"]["kind_registry"]["failures"]


def test_edit_kind_changes_the_kind_names_both_and_survives_a_rebuild(kyo, project) -> None:
    record = new_record(kyo, kind="chore", criteria=["a"])
    result = kyo("edit", record, "--kind", "task")
    assert result.code == 0, result
    assert "chore -> task" in result.out
    assert f"edited {record} (task)" in result.out

    exported = json.loads((project / "kyosei" / "export" / f"{record}.json").read_text())
    assert exported["kind"] == "task"

    assert kyo("rebuild", "--force").code == 0
    shown = kyo("show", record, "--json")
    assert shown.code == 0, shown
    assert shown.json["kind"] == "task"


def test_edit_kind_json_carries_the_new_kind(kyo) -> None:
    record = new_record(kyo, kind="chore", criteria=["a"])
    result = kyo("edit", record, "--kind", "bug", "--json")
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "bug"


def test_edit_kind_refuses_a_kind_that_does_not_exist(kyo) -> None:
    record = new_record(kyo, kind="chore", criteria=["a"])
    result = kyo("edit", record, "--kind", "ghost-kind")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_REKIND_UNKNOWN_KIND in result.err
    assert "'ghost-kind' is not defined in kyosei/kinds.yml" in result.err
    assert kyo("show", record, "--json").json["kind"] == "chore"


def test_edit_kind_refuses_a_different_archetype(kyo) -> None:
    record = new_record(kyo, kind="chore", criteria=["a"])
    result = kyo("edit", record, "--kind", "decision")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_REKIND_ARCHETYPE_DIFFERS in result.err
    assert "archetype work" in result.err and "archetype knowledge" in result.err


def test_a_refused_rekind_writes_nothing_else_in_the_same_call(kyo, project) -> None:
    record = new_record(kyo, kind="chore", title="Original", criteria=["a"])
    result = kyo("edit", record, "--kind", "decision", "--title", "Changed")
    assert result.code == EXIT_CONTRACT, result
    exported = json.loads((project / "kyosei" / "export" / f"{record}.json").read_text())
    assert exported["title"] == "Original"
    assert exported["kind"] == "chore"


def test_edit_kind_refuses_to_unmake_a_plan_with_members(kyo) -> None:
    plan = new_record(kyo, kind="plan")
    member = new_record(kyo, kind="task", criteria=["a"])
    assert kyo("edit", member, "--plan", plan).code == 0
    result = kyo("edit", plan, "--kind", "task")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_REKIND_STRANDS_STATE in result.err
    assert member in result.err
    assert kyo("edit", member, "--no-plan").code == 0
    assert kyo("edit", plan, "--kind", "task").code == 0


def test_the_undefined_kind_problem_names_the_edit_and_the_edit_clears_it(kyo, project) -> None:
    """KYO-509.c3: the doctor's remedy is real -- following it clears the problem."""
    record = new_record(kyo, kind="chore", criteria=["a"])
    _remove_kind(project, "chore")

    problems = _kind_registry_problems(kyo)
    assert len(problems) == 1
    assert f"`kyo edit {record} --kind <kind>`" in problems[0]
    assert "restore 'chore' to kyosei/kinds.yml" in problems[0]

    # The removed kind is still described by the packaged default, so its archetype
    # is known and a knowledge target is still refused.
    refused = kyo("edit", record, "--kind", "decision")
    assert refused.code == EXIT_CONTRACT, refused
    assert CONTRACT_REKIND_ARCHETYPE_DIFFERS in refused.err

    result = kyo("edit", record, "--kind", "task")
    assert result.code == 0, result
    assert "chore -> task" in result.out
    assert _kind_registry_problems(kyo) == []
    assert kyo("doctor").code == 0


def test_a_carried_item_rekinds_only_to_a_carried_kind(kyo, project) -> None:
    """A since-note is kept only by a carried kind, so re-kinding one away is refused
    -- and the same re-kind succeeds once the since-note is not stranded by it."""
    noted = kyo("note", "--shift", "--file", "-", "--json", stdin="an item, noted first")
    assert noted.code == 0, noted
    filed = kyo(
        "new", "reminder", "--title", "an item", "--since-note", noted.json["shift_note_id"],
        "--json",
    )
    assert filed.code == 0, filed
    record = filed.json["record"]["id"]
    result = kyo("edit", record, "--kind", "task")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_REKIND_STRANDS_STATE in result.err
    assert "reminder" in result.err

    path = project / "kyosei" / "kinds.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["kinds"]["task"]["policy"]["carried"] = True
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert kyo("edit", record, "--kind", "task").code == 0
