"""Pure unit tests for kyosei.sweep — the text-similarity and touch-comparison
mechanics KYO-8.c1/c2/c3/c4 are built from, tested without a project fixture."""

from __future__ import annotations

from pathlib import Path

from kyosei import sweep


def test_identical_text_is_a_near_duplicate() -> None:
    text = "the export must round-trip through kyo rebuild without drift"
    is_dup, ratio = sweep.is_near_duplicate(text, text)
    assert is_dup
    assert ratio == 1.0


def test_case_and_punctuation_do_not_defeat_the_match() -> None:
    a = "The export must round-trip through `kyo rebuild` without drift."
    b = "the export must round trip through kyo rebuild without drift"
    is_dup, ratio = sweep.is_near_duplicate(a, b)
    assert is_dup
    assert ratio > 0.95


def test_short_texts_are_never_flagged_precision_over_recall() -> None:
    """Two short criteria can collide on sheer brevity despite meaning
    different things — precision over recall (this build's task brief)."""
    is_dup, ratio = sweep.is_near_duplicate("returns 200", "returns 201")
    assert not is_dup
    assert ratio == 0.0  # short-circuited before any ratio is computed


def test_clearly_different_text_is_not_a_near_duplicate() -> None:
    a = "kyo doctor fails loudly when a criterion's state disagrees with its verification"
    b = "kyo rebuild reconstructs the database from the export directory alone"
    is_dup, ratio = sweep.is_near_duplicate(a, b)
    assert not is_dup
    assert ratio < sweep.SIMILARITY_THRESHOLD


def test_find_near_duplicate_criteria_skips_same_record_pairs() -> None:
    text = "the criterion text must be at least this long to ever be compared"
    criteria = [
        sweep.CriterionText(record_id="TST-1", criterion_id="TST-1.c1", text=text),
        sweep.CriterionText(record_id="TST-1", criterion_id="TST-1.c2", text=text),
    ]
    assert sweep.find_near_duplicate_criteria(criteria) == []


def test_find_near_duplicate_criteria_matches_across_records_in_order() -> None:
    text_a = "the export must round-trip through kyo rebuild without any byte drift"
    text_b = "the export must round trip through kyo rebuild without any byte drift"
    criteria = [
        sweep.CriterionText(record_id="TST-1", criterion_id="TST-1.c1", text=text_a),
        sweep.CriterionText(record_id="TST-2", criterion_id="TST-2.c1", text=text_b),
    ]
    matches = sweep.find_near_duplicate_criteria(criteria)
    assert len(matches) == 1
    match = matches[0]
    assert match.record_a == "TST-1"
    assert match.record_b == "TST-2"
    assert match.criterion_a == "TST-1.c1"
    assert match.criterion_b == "TST-2.c1"
    assert match.ratio >= sweep.SIMILARITY_THRESHOLD


def test_find_creation_time_duplicates_flags_title_and_criteria() -> None:
    warnings = sweep.find_creation_time_duplicates(
        new_title="Widget explodes on cold cache startup",
        new_criteria_texts=["the widget must not explode on a cold cache"],
        existing_titles=[("TST-1", "Widget explodes on cold cache start up")],
        existing_criteria=[
            ("TST-1", "TST-1.c1", "the widget must not explode on a cold cache")
        ],
    )
    kinds = {w.kind for w in warnings}
    assert "title" in kinds
    assert "criterion" in kinds


def test_find_creation_time_duplicates_empty_when_nothing_close() -> None:
    warnings = sweep.find_creation_time_duplicates(
        new_title="Unrelated feature request",
        new_criteria_texts=["totally different acceptance criterion here"],
        existing_titles=[("TST-1", "Widget explodes on cold cache start up")],
        existing_criteria=[],
    )
    assert warnings == []


def test_changed_touch_paths_intersects_and_sorts() -> None:
    result = sweep.changed_touch_paths(
        ["b.py", "a.py", "c.py"], {"a.py", "c.py", "unrelated.py"}
    )
    assert result == ["a.py", "c.py"]


def test_missing_touch_paths(tmp_path: Path) -> None:
    (tmp_path / "exists.py").write_text("x = 1\n", encoding="utf-8")
    result = sweep.missing_touch_paths(tmp_path, ["exists.py", "gone.py"])
    assert result == ["gone.py"]
