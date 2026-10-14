"""KYO-492: `kyo ungate` clears several gates, or every open gate with a tag, in one act.

The decided pattern behind it (KYO-479): a project declares a `deploy` gate tag, a
record's work is gated `awaiting:event --tag deploy` when it merges, and after a deploy
the gates for what shipped are cleared. A consumer's deploys shipped different subsets,
so both "these records" (several ids) and "everything tagged deploy" (`--tag`) are
needed — and both clear all of their gates or none of them.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import event_lines, new_record

DEPLOY = """\
gate-tags:
  deploy:
    summary: Merged, waiting on the next deploy to be live.
  ui-review:
    summary: A person has to look at a rendered interface and judge it.
"""


def declare_tags(project: Path) -> None:
    (project / "kyosei" / "gate-tags.yml").write_text(DEPLOY, encoding="utf-8")


def gate(kyo, record: str, *tags: str) -> str:
    args = ["gate", record, "awaiting:event", "--do", "live after the next deploy"]
    for tag in tags:
        args += ["--tag", tag]
    result = kyo(*args, "--json")
    assert result.code == 0, result
    return result.json["gate_id"]


def gates_of(kyo, record: str) -> dict[str, dict]:
    return {g["id"]: g for g in kyo("show", record, "--json").json["gates"]}


def ungate_events(project: Path) -> list[dict]:
    return [e for e in event_lines(project) if str(e.get("command", "")).startswith("ungate")]


# ------------------------------------------------------------------------ KYO-492.c1


def test_several_ids_clear_together_one_line_per_gate(kyo, project: Path) -> None:
    first, second = new_record(kyo), new_record(kyo)
    g1, g2 = gate(kyo, first), gate(kyo, second)
    g3 = gate(kyo, second)

    result = kyo("ungate", g1, g2, g3, "--why", "deployed 2026-10-03")
    assert result.code == 0, result
    for gate_id, record in ((g1, first), (g2, second), (g3, second)):
        assert f"cleared {gate_id} on {record} (awaiting:event)" in result.out
        cleared = gates_of(kyo, record)[gate_id]
        assert cleared["cleared_at"] is not None
        assert cleared["cleared_why"] == "deployed 2026-10-03"
    assert len({gates_of(kyo, r)[g]["cleared_at"] for g, r in ((g1, first), (g2, second))}) == 1


def test_several_ids_are_one_transaction_one_event_per_record(kyo, project: Path) -> None:
    first, second = new_record(kyo), new_record(kyo)
    g1, g2 = gate(kyo, first), gate(kyo, second)
    assert kyo("ungate", g1, g2, "--why", "deployed").code == 0
    events = ungate_events(project)
    assert {e["command"] for e in events} == {f"ungate {g1} {g2}"}
    assert sorted(e["record_id"] for e in events) == sorted([first, second])


def test_one_bad_id_clears_nothing_and_each_bad_id_is_named(kyo, project: Path) -> None:
    first, second, third = new_record(kyo), new_record(kyo), new_record(kyo)
    good, done = gate(kyo, first), gate(kyo, second)
    assert kyo("ungate", done).code == 0
    missing = f"{third}.g9"

    result = kyo("ungate", good, done, missing, "--why", "deployed")
    assert result.code == 1
    assert "nothing was cleared" in result.err
    assert f"{done}: {done} was already cleared at" in result.err
    assert f"{missing}: no gate {missing!r}" in result.err
    assert gates_of(kyo, first)[good]["cleared_at"] is None, "a refused bulk ungate wrote"
    assert len(ungate_events(project)) == 1  # the earlier single clearing only


# ------------------------------------------------------------------------ KYO-492.c2


def test_tag_lists_open_tagged_gates_and_writes_nothing(kyo, project: Path) -> None:
    declare_tags(project)
    a, b, c = new_record(kyo), new_record(kyo), new_record(kyo)
    ga, gb = gate(kyo, a, "deploy"), gate(kyo, b, "deploy", "ui-review")
    gc = gate(kyo, c, "ui-review")
    gd = gate(kyo, c, "deploy")
    assert kyo("ungate", gd).code == 0  # already cleared: not part of the selection
    events_before = len(event_lines(project))

    result = kyo("ungate", "--tag", "deploy", "--why", "deployed", "--json")
    assert result.code == 0, result
    assert result.json["applied"] is False
    assert [g["gate_id"] for g in result.json["gates"]] == [ga, gb]
    text = kyo("ungate", "--tag", "deploy")
    assert ga in text.out and gb in text.out and gc not in text.out and gd not in text.out
    assert "--apply" in text.out
    assert len(event_lines(project)) == events_before
    assert gates_of(kyo, a)[ga]["cleared_at"] is None
    assert gates_of(kyo, b)[gb]["cleared_at"] is None


def test_tag_apply_clears_every_tagged_gate_in_one_transaction(kyo, project: Path) -> None:
    declare_tags(project)
    a, b, c = new_record(kyo), new_record(kyo), new_record(kyo)
    ga, gb = gate(kyo, a, "deploy"), gate(kyo, b, "deploy")
    gc = gate(kyo, c, "ui-review")

    result = kyo("ungate", "--tag", "deploy", "--apply", "--why", "deployed 2026-10-03")
    assert result.code == 0, result
    assert f"cleared {ga} on {a}" in result.out and f"cleared {gb} on {b}" in result.out
    assert gates_of(kyo, a)[ga]["cleared_why"] == "deployed 2026-10-03"
    assert gates_of(kyo, b)[gb]["cleared_why"] == "deployed 2026-10-03"
    assert gates_of(kyo, c)[gc]["cleared_at"] is None, "a gate without the tag was cleared"
    assert {e["command"] for e in ungate_events(project)} == {"ungate --tag deploy --apply"}
    assert kyo("list", "--gate-tag", "deploy", "--json").json["records"] == []

    again = kyo("ungate", "--tag", "deploy", "--apply")
    assert again.code == 0
    assert "nothing to clear" in again.out


def test_an_undeclared_tag_is_refused_naming_the_registry(kyo, project: Path) -> None:
    declare_tags(project)
    for extra in ([], ["--apply"]):
        result = kyo("ungate", "--tag", "deploi", *extra)
        assert result.code != 0
        assert "gate-tags.yml" in result.err
        assert "deploi" in result.err


def test_tag_and_ids_together_are_refused(kyo, project: Path) -> None:
    declare_tags(project)
    record = new_record(kyo)
    gate_id = gate(kyo, record, "deploy")
    result = kyo("ungate", gate_id, "--tag", "deploy")
    assert result.code == 64
    assert gates_of(kyo, record)[gate_id]["cleared_at"] is None
    assert kyo("ungate", gate_id, "--apply").code == 64
    assert kyo("ungate").code == 64


# ------------------------------------------------------------------------ KYO-492.c3


def test_a_bare_id_is_todays_command(kyo, project: Path) -> None:
    record = new_record(kyo)
    gate_id = gate(kyo, record)
    result = kyo("ungate", gate_id, "--why", "deployed", "--json")
    assert result.code == 0
    assert result.json["gate_id"] == gate_id
    assert result.json["record_id"] == record
    assert [e["command"] for e in ungate_events(project)] == [f"ungate {gate_id}"]
    again = kyo("ungate", gate_id)
    assert again.code == 1
    assert "nothing was cleared" not in again.err
    assert f"{gate_id} was already cleared at" in again.err


# ------------------------------------------------------------------------ KYO-492.c4


def test_the_packaged_registry_carries_a_commented_deploy_example(tmp_path: Path) -> None:
    """Shipped commented (no tag is seeded), and valid the moment it is uncommented."""
    from kyosei import registry as registry_module
    from kyosei.scaffold import defaults_dir

    text = (defaults_dir() / "gate-tags.yml").read_text(encoding="utf-8")
    assert "\ngate-tags: {}\n" in text, "the packaged file must still declare no tags"
    example = text.split("\n# gate-tags:\n", 1)[1]
    uncommented = "gate-tags:\n" + "".join(
        line[2:] + "\n" for line in example.splitlines() if line.startswith("#   ")
    )
    kyosei_dir = tmp_path / "kyosei"
    kyosei_dir.mkdir()
    (kyosei_dir / "gate-tags.yml").write_text(uncommented, encoding="utf-8")
    assert "deploy" in registry_module.load_gate_tag_registry(kyosei_dir).names


def test_each_shape_refusal_clears_by_the_reinvocation_it_names(kyo, project: Path) -> None:
    """The remedies `cmd_ungate#0` and `#2` name (recorded in test_kyo5_error_remedies)."""
    declare_tags(project)
    record = new_record(kyo)
    gate_id = gate(kyo, record, "deploy")

    both = kyo("ungate", gate_id, "--tag", "deploy")
    assert both.code == 64 and "not both" in both.err
    assert kyo("ungate", "--tag", "deploy").code == 0  # dropped the ids

    neither = kyo("ungate")
    assert neither.code == 64 and "--tag" in neither.err
    assert kyo("ungate", gate_id).code == 0  # named an id
