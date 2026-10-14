"""KYO-7.c6: `kyo split` — successor carries the unmet remainder, back-linked
both ways; met criteria are refused; law 4 (supersede, never delete)."""

from __future__ import annotations

from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR

from .conftest import new_record, verify_criterion


def test_split_creates_a_successor_carrying_the_named_unmet_criteria(kyo, project) -> None:
    record = new_record(kyo, title="Big task", criteria=["fix the leak", "add the test"])
    result = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    assert result.code == 0, result
    successor_id = result.json["successor_id"]

    successor = kyo("show", successor_id, "--json").json
    assert successor["kind"] == "bug"
    assert len(successor["criteria"]) == 1
    assert successor["criteria"][0]["text"] == "add the test"
    assert successor["criteria"][0]["state"] == "unmet-unbuilt"
    assert successor["criteria"][0]["id"] != f"{record}.c2"  # new id (law 4)


def test_split_void_withdraws_the_original_with_an_auto_reason_naming_the_successor(
    kyo, project
) -> None:
    record = new_record(kyo, criteria=["fix the leak", "add the test"])
    result = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    successor_id = result.json["successor_id"]

    original = kyo("show", record, "--json").json
    moved = original["criteria"][1]
    assert moved["id"] == f"{record}.c2"
    assert moved["state"] == "void-withdrawn"
    assert successor_id in moved["reason"]


def test_split_leaves_the_original_criteria_and_kept_criteria_untouched(kyo, project) -> None:
    record = new_record(kyo, criteria=["fix the leak", "add the test"])
    verify_criterion(kyo, f"{record}.c1", "met", method="human-observed")
    kyo("split", record, "--to-successor", f"{record}.c2")

    original = kyo("show", record, "--json").json
    kept = original["criteria"][0]
    assert kept["id"] == f"{record}.c1"
    assert kept["state"] == "met"
    assert len(kept["verifications"]) == 1


def test_splitting_a_met_criterion_is_refused(kyo, project) -> None:
    record = new_record(kyo, criteria=["fix the leak", "add the test"])
    verify_criterion(kyo, f"{record}.c1", "met", method="human-observed")
    result = kyo("split", record, "--to-successor", f"{record}.c1")
    assert result.code == EXIT_CONTRACT
    assert f"{record}.c1" in result.err

    # nothing was created or changed
    original = kyo("show", record, "--json").json
    assert original["criteria"][0]["state"] == "met"


def test_splitting_a_void_criterion_is_refused(kyo, project) -> None:
    record = new_record(kyo, criteria=["fix the leak", "add the test"])
    kyo("edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", "no longer applies")
    result = kyo("split", record, "--to-successor", f"{record}.c1")
    assert result.code == EXIT_CONTRACT


def test_successor_gets_the_originals_touches(kyo, project, tmp_path) -> None:
    idea = project / "ideas" / "widget.md"
    idea.write_text("# Widget\n\nFix it.\n", encoding="utf-8")
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("fix the leak\nadd the test\n", encoding="utf-8")
    touches_file = tmp_path / "touches.txt"
    touches_file.write_text("kyosei/config.yml\n", encoding="utf-8")
    record = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]

    result = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    successor = kyo("show", result.json["successor_id"], "--json").json
    assert [t["path"] for t in successor["touches"]] == ["kyosei/config.yml"]


def test_split_link_is_produced_by_both_ways(kyo, project) -> None:
    record = new_record(kyo, criteria=["fix the leak", "add the test"])
    result = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    successor_id = result.json["successor_id"]

    successor = kyo("show", successor_id, "--json").json
    original = kyo("show", record, "--json").json
    out_link = next(link for link in successor["links"] if link["direction"] == "outgoing")
    assert out_link["type"] == "produced-by"
    assert out_link["other"] == record
    in_link = next(link for link in original["links"] if link["direction"] == "incoming")
    assert in_link["type"] == "produced-by"
    assert in_link["other"] == successor_id


def test_split_refuses_a_criterion_that_belongs_to_a_different_record(kyo, project) -> None:
    record = new_record(kyo, criteria=["a"])
    other = new_record(kyo, criteria=["b"])
    result = kyo("split", record, "--to-successor", f"{other}.c1")
    assert result.code == EXIT_ERROR
    assert other in result.err


def test_split_refuses_duplicate_criterion_ids(kyo, project) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    result = kyo("split", record, "--to-successor", f"{record}.c1", f"{record}.c1")
    assert result.code != 0


def test_split_moves_several_unmet_criteria_at_once(kyo, project) -> None:
    record = new_record(kyo, criteria=["a", "b", "c"])
    verify_criterion(kyo, f"{record}.c1", "met", method="human-observed")
    result = kyo(
        "split", record, "--to-successor", f"{record}.c2", f"{record}.c3", "--json"
    )
    assert result.code == 0, result
    successor = kyo("show", result.json["successor_id"], "--json").json
    assert {c["text"] for c in successor["criteria"]} == {"b", "c"}
