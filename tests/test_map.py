"""``kyo map`` — the kind registry rendered as a circles-and-arrows graph."""

from __future__ import annotations


def test_json_carries_kind_nodes_and_pseudo_sources(kyo) -> None:
    data = kyo("map", "--json").json
    node_ids = {n["id"] for n in data["nodes"]}
    assert {"bug", "task", "decision", "evidence"} <= node_ids
    assert {"intake", "idea"} <= node_ids
    pseudo = {n["id"] for n in data["nodes"] if n["type"] == "pseudo"}
    assert pseudo == {"intake", "idea"}


def test_kind_nodes_carry_archetype_and_policy(kyo) -> None:
    data = kyo("map", "--json").json
    bug = next(n for n in data["nodes"] if n["id"] == "bug")
    assert bug["archetype"] == "work"
    assert bug["policy"]["counted"] is True
    decision = next(n for n in data["nodes"] if n["id"] == "decision")
    assert decision["archetype"] == "knowledge"


def test_edges_come_from_accepts_from(kyo) -> None:
    data = kyo("map", "--json").json
    edges = {(e["from"], e["to"]) for e in data["edges"]}
    assert ("intake", "bug") in edges
    assert ("bug", "task") in edges
    assert ("idea", "task") in edges


def test_human_output_is_indented_text(kyo) -> None:
    out = kyo("map").out
    assert "bug" in out
    assert "[work]" in out
    assert "pseudo-sources:" in out
