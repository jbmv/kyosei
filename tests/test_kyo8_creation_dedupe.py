"""KYO-8.c4: kyo new and kyo promote warn (never block) on a title/criteria
close match against an existing open record."""

from __future__ import annotations

from pathlib import Path

from .conftest import new_record


def test_new_warns_on_a_near_duplicate_title(kyo) -> None:
    new_record(kyo, title="Widget explodes on a cold cache start up")
    result = kyo(
        "new", "bug", "--title", "Widget explodes on a cold cache startup", "--json"
    )
    assert result.code == 0, result  # never blocks
    assert result.json["duplicate_warnings"]
    assert result.json["duplicate_warnings"][0]["kind"] == "title"


def test_new_prints_a_warning_line_in_human_output(kyo) -> None:
    new_record(kyo, title="Widget explodes on a cold cache start up")
    result = kyo("new", "bug", "--title", "Widget explodes on a cold cache startup")
    assert result.code == 0, result
    assert "WARNING" in result.out


def test_new_warns_on_near_duplicate_criteria(kyo) -> None:
    new_record(kyo, criteria=["the export must round-trip through kyo rebuild without drift"])
    result = kyo(
        "new", "bug", "--title", "Unrelated title",
        "--criteria-file", "-", "--json",
        stdin="the export must round trip through kyo rebuild without any drift\n",
    )
    assert result.code == 0, result
    kinds = {w["kind"] for w in result.json["duplicate_warnings"]}
    assert "criterion" in kinds


def test_new_with_no_similar_record_has_no_warnings(kyo) -> None:
    new_record(kyo, title="Widget explodes on a cold cache start up")
    result = kyo("new", "bug", "--title", "Totally unrelated database migration", "--json")
    assert result.code == 0, result
    assert result.json["duplicate_warnings"] == []


def test_a_wont_do_record_does_not_trigger_a_warning(kyo) -> None:
    record = new_record(kyo, title="Widget explodes on a cold cache start up", criteria=["a"])
    kyo("wont-do", record, "--why", "dead premise")
    result = kyo(
        "new", "bug", "--title", "Widget explodes on a cold cache startup", "--json"
    )
    assert result.json["duplicate_warnings"] == []


def test_promote_warns_on_a_near_duplicate_title(kyo, project: Path, write_file) -> None:
    new_record(kyo, title="Widget explodes on a cold cache start up")
    idea = project / "ideas" / "dup.md"
    idea.write_text("# Widget explodes on a cold cache startup\n\n\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result  # never blocks
    assert result.json["duplicate_warnings"]
    assert result.json["duplicate_warnings"][0]["kind"] == "title"

    idea2 = project / "ideas" / "dup2.md"
    idea2.write_text("# Widget explodes on a cold cache startup\n\n\n", encoding="utf-8")
    human = kyo(
        "promote", str(idea2), "--kind", "chore",
        "--criteria-file", write_file("c2.txt", "y\n"),
        "--touches-file", write_file("t2.txt", "kyosei/config.yml\n"),
    )
    assert human.code == 0, human
    assert "WARNING" in human.out
