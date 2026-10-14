"""KYO-330: ``kyo new/promote/spawn --from FILE`` -- a whole record from one file.

``--from`` is an alternative to ``--title``/``--scope``/``--plan``/
``--description-file``/``--criteria-file``/``--touches-file``, not a second write
path: it parses a markdown file (YAML front matter for ``title``/``scope``/``plan``/
``touches``, a ``## Criteria`` section holding one criterion per list item, and
everything else the description) into exactly the values those flags would have
produced, and hands them to the SAME write path the flags already use
(``cmd_records._parse_record_file``). That is what makes KYO-330.c2 -- "``--from``
files the identical record" -- true by construction, and this module's first test
proves it by filing the same record both ways and diffing what landed.

Each test here fails if its mechanism is removed (AGENTS.md's standing rule); the
two that assert a validation message also assert the remedy it names clears the
refusal (KYO-330.c4).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .conftest import new_record


def _declare_scope(project: Path, name: str, *, summary: str = "a scope for tests") -> None:
    """Add ``name`` to this project's ``kyosei/scopes.yml`` (scopes are optional and
    validated when given -- the default file this test's ``project`` fixture ships
    declares none, so a scope has to be declared before it can be used at all)."""
    path = project / "kyosei" / "scopes.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("scopes", {})[name] = {"summary": summary}
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _stored(kyo, record_id: str) -> dict:
    shown = kyo("show", record_id, "--json")
    assert shown.code == 0, shown
    return shown.json


def _comparable(document: dict) -> dict:
    """The part of a stored record that ``--from`` and the separate flags must agree
    on -- everything but the id and the timestamps every write mints fresh."""
    return {
        "kind": document["kind"],
        "title": document["title"],
        "scope": document["scope"],
        "plan": document["plan"],
        "description": document["description"],
        "criteria": [c["text"] for c in document["criteria"]],
        "touches": [(t["path"], t["symbol"]) for t in document["touches"]],
    }


# ------------------------------------------------------------------- KYO-330.c1/c2


def test_new_from_file_stores_title_scope_plan_touches_criteria_description(
    kyo, project: Path, write_file
) -> None:
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    _declare_scope(project, "core")
    plan_id = new_record(kyo, kind="plan", title="A plan")
    record_file = write_file(
        "record.md",
        "---\n"
        "title: Fix the widget explosion\n"
        "scope: core\n"
        f"plan: {plan_id}\n"
        "touches:\n"
        "  - widget.py#render\n"
        "  - +widget_test.py\n"
        "---\n"
        "\n"
        "Some description text here.\n"
        "Second line of description.\n"
        "\n"
        "## Criteria\n"
        "- The widget no longer explodes on cold cache\n"
        "- A regression test covers it\n",
    )
    result = kyo("new", "task", "--from", record_file, "--json", "--full")
    assert result.code == 0, result
    record = result.json["record"]
    assert record["kind"] == "task"
    assert record["title"] == "Fix the widget explosion"
    assert record["scope"] == "core"
    assert record["plan"] == plan_id
    assert record["description"] == (
        "Some description text here.\nSecond line of description."
    )
    assert [c["text"] for c in record["criteria"]] == [
        "The widget no longer explodes on cold cache",
        "A regression test covers it",
    ]
    assert [(t["path"], t["symbol"]) for t in record["touches"]] == [
        ("widget.py", "render"),
        ("+widget_test.py", None),
    ]


def test_from_file_produces_the_identical_record_to_the_separate_flags(
    kyo, project: Path, write_file
) -> None:
    """KYO-330.c2, proven by diffing the stored documents rather than asserted by
    inspection: whatever ``_parse_record_file`` returns falls into the SAME
    variables ``cmd_new`` already used, so the only way this can differ is a bug."""
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    record_file = write_file(
        "record.md",
        "---\n"
        "title: Fix the widget explosion\n"
        "touches:\n"
        "  - widget.py#render\n"
        "---\n"
        "\n"
        "A description.\n"
        "\n"
        "## Criteria\n"
        "- One criterion\n"
        "- Two criterion\n",
    )
    via_from = kyo("new", "task", "--from", record_file, "--json", "--full")
    assert via_from.code == 0, via_from

    via_flags = kyo(
        "new",
        "task",
        "--title",
        "Fix the widget explosion",
        "--description-file",
        write_file("desc.txt", "A description."),
        "--criteria-file",
        write_file("crit.txt", "One criterion\nTwo criterion\n"),
        "--touches-file",
        write_file("touch.txt", "widget.py#render\n"),
        "--json",
        "--full",
        "--force",  # KYO-538: the same record was just filed via --from
    )
    assert via_flags.code == 0, via_flags

    assert _comparable(via_from.json["record"]) == _comparable(via_flags.json["record"])


def test_a_criteria_section_is_optional_exactly_as_criteria_file_is(
    kyo, write_file
) -> None:
    record_file = write_file(
        "record.md", "---\ntitle: No criteria here\n---\n\nJust a description.\n"
    )
    result = kyo("new", "task", "--from", record_file, "--json", "--full")
    assert result.code == 0, result
    assert result.json["record"]["criteria"] == []


# ---------------------------------------------------------------------- KYO-330.c3


def test_check_validates_and_files_nothing(kyo, project: Path, write_file) -> None:
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    before = kyo("list", "--json")
    assert before.code == 0, before
    record_file = write_file(
        "record.md",
        "---\ntitle: Checked only\ntouches:\n  - widget.py\n---\n\n## Criteria\n- one\n",
    )
    result = kyo("new", "task", "--from", record_file, "--check", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True
    assert result.json["check"] is True
    assert result.json["record_id"] is None
    assert result.json["title"] == "Checked only"
    assert result.json["criteria"] == ["one"]
    assert "nothing filed" in kyo("new", "task", "--from", record_file, "--check").out
    after = kyo("list", "--json")
    assert after.code == 0, after
    assert len(after.json["records"]) == len(before.json["records"])


def test_check_still_validates_touches_against_the_repo(kyo, write_file) -> None:
    record_file = write_file(
        "record.md", "---\ntitle: Bad touch\ntouches:\n  - does/not/exist.py\n---\n"
    )
    result = kyo("new", "task", "--from", record_file, "--check")
    assert result.code == 1, result
    assert "does/not/exist.py" in result.err


def test_check_without_from_is_refused(kyo) -> None:
    result = kyo("new", "task", "--title", "x", "--check")
    assert result.code == 64, result
    assert "--check" in result.err and "--from" in result.err


# ------------------------------------------------------------------- KYO-330.c4


def test_a_criteria_line_with_no_marker_names_the_file_and_line_and_the_remedy_clears_it(
    kyo, write_file
) -> None:
    bad = write_file(
        "bad.md",
        "---\ntitle: x\n---\n\n## Criteria\nno marker on this line\n- a real item\n",
    )
    result = kyo("new", "task", "--from", bad, "--check")
    assert result.code == 1, result
    assert f"{bad}:6:" in result.err
    assert "not a list item" in result.err
    assert "add the marker" in result.err

    fixed = write_file(
        "fixed.md",
        "---\ntitle: x\n---\n\n## Criteria\n- no marker on this line\n- a real item\n",
    )
    cleared = kyo("new", "task", "--from", fixed, "--check")
    assert cleared.code == 0, cleared


def test_a_wrapped_continuation_line_joins_the_item_above_it(kyo, write_file) -> None:
    record_file = write_file(
        "record.md",
        "---\ntitle: x\n---\n\n## Criteria\n- one item\n  that wraps\n",
    )
    result = kyo("new", "task", "--from", record_file, "--check", "--json")
    assert result.code == 0, result
    assert result.json["criteria"] == ["one item that wraps"]


def test_no_front_matter_names_the_remedy(kyo, write_file) -> None:
    record_file = write_file("record.md", "just some text, no front matter\n")
    result = kyo("new", "task", "--from", record_file)
    assert result.code == 1, result
    assert "no front matter" in result.err


def test_missing_title_names_the_remedy(kyo, write_file) -> None:
    record_file = write_file("record.md", "---\nscope: core\n---\n")
    result = kyo("new", "task", "--from", record_file)
    assert result.code == 1, result
    assert "no 'title:'" in result.err


def test_an_unknown_front_matter_key_is_refused(kyo, write_file) -> None:
    record_file = write_file("record.md", "---\ntitle: x\nbogus: y\n---\n")
    result = kyo("new", "task", "--from", record_file)
    assert result.code == 1, result
    assert "bogus" in result.err


# --------------------------------------------------------------- KYO-330: --from vs
# the flags it replaces


def test_from_combined_with_title_is_refused_by_name(kyo, write_file) -> None:
    record_file = write_file("record.md", "---\ntitle: x\n---\n")
    result = kyo("new", "task", "--from", record_file, "--title", "y")
    assert result.code == 64, result
    assert "--from" in result.err and "--title" in result.err


def test_new_without_title_or_from_is_refused(kyo) -> None:
    result = kyo("new", "task")
    assert result.code == 64, result
    assert "--title" in result.err and "--from" in result.err


# ---------------------------------------------------------------------- KYO-330.c5


def test_promote_accepts_from_on_the_same_terms(
    kyo, project: Path, write_file
) -> None:
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    idea = project / "ideas" / "widget.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# An idea\n\nPromoted prose lives here.\n", encoding="utf-8")
    record_file = write_file(
        "record.md",
        "---\ntitle: Promoted title\ntouches:\n  - widget.py\n---\n\n## Criteria\n- c1\n",
    )
    result = kyo("promote", str(idea), "--kind", "task", "--from", record_file, "--json", "--full")
    assert result.code == 0, result
    record = result.json["record"]
    assert record["title"] == "Promoted title"
    assert [c["text"] for c in record["criteria"]] == ["c1"]
    assert [t["path"] for t in record["touches"]] == ["widget.py"]
    # The description is still the promoted idea, never the --from file's own prose
    # (`kyo promote` has no --description-file for --from to stand in for).
    assert "Promoted prose lives here." in record["description"]
    assert not idea.exists()  # promotion still consumes the idea file


# --------------------------------------------------------------------- KYO-340.c1


def test_promote_from_accepts_a_plan_key_and_files_membership(
    kyo, project: Path, write_file
) -> None:
    """KYO-340.c1: `kyo promote --from FILE`'s front matter may carry `plan:`,
    validated on the same terms `kyo new --plan` applies (`_require_plan`) --
    `kyo promote` still has no `--plan` flag of its own; the file is the only door.
    Files one and reads the membership back with `kyo list --plan`, the way
    ``tests/test_plan_members.py`` already does for the flags path."""
    idea = project / "ideas" / "widget.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# An idea\n\nBody.\n", encoding="utf-8")
    plan_id = new_record(kyo, kind="plan", title="A plan")
    record_file = write_file(
        "record.md",
        f"---\ntitle: Promoted onto a plan\nplan: {plan_id}\ntouches:\n"
        "  - kyosei/config.yml\n---\n\n## Criteria\n- c1\n",
    )
    result = kyo("promote", str(idea), "--kind", "task", "--from", record_file, "--json", "--full")
    assert result.code == 0, result
    record = result.json["record"]
    assert record["plan"] == plan_id

    listed = kyo("list", "--plan", plan_id, "--json")
    assert listed.code == 0, listed
    assert [row["id"] for row in listed.json["records"]] == [record["id"]]


def test_promote_from_plan_key_is_validated_like_new_plan(
    kyo, project: Path, write_file
) -> None:
    """KYO-340.c1's other half: the same validation, not a new one -- an id that is
    not a plan is refused exactly as `kyo new --plan` refuses it (`_require_plan`)."""
    idea = project / "ideas" / "widget.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# An idea\n\nBody.\n", encoding="utf-8")
    not_a_plan = new_record(kyo, kind="bug", title="not a plan", criteria=["repro"])
    record_file = write_file(
        "record.md",
        f"---\ntitle: x\nplan: {not_a_plan}\ntouches:\n  - kyosei/config.yml\n---\n\n"
        "## Criteria\n- c1\n",
    )
    result = kyo("promote", str(idea), "--kind", "task", "--from", record_file, "--check")
    assert result.code == 2, result
    assert not_a_plan in result.err
    assert idea.exists()  # refused before anything touched the idea file


# --------------------------------------------------------------------- KYO-340.c2


def test_promote_from_accepts_a_body_identical_to_the_idea_after_whitespace(
    kyo, project: Path, write_file
) -> None:
    """KYO-340.c2, first half: a `--from` body that merely echoes the idea back
    (whitespace aside -- the wrapping is free to differ) is accepted, and the idea's
    own text remains the description, exactly as when `--from` has no body at all."""
    idea = project / "ideas" / "widget.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    # No frontmatter, so the WHOLE file (heading included) is what a whole-file
    # promotion moves (`ideas.promoted_body`) -- the comparison has to be against
    # that, not just the last line.
    idea.write_text("# An idea\n\nThe widget explodes under cold cache.\n", encoding="utf-8")
    record_file = write_file(
        "record.md",
        "---\ntitle: x\ntouches:\n  - kyosei/config.yml\n---\n\n"
        # Re-wrapped and padded with extra spaces throughout -- whitespace differs,
        # the words do not.
        "#   An  idea\n\nThe   widget  explodes\nunder cold cache.\n\n"
        "## Criteria\n- c1\n",
    )
    result = kyo("promote", str(idea), "--kind", "task", "--from", record_file, "--json", "--full")
    assert result.code == 0, result
    record = result.json["record"]
    assert record["description"] == (
        "# An idea\n\nThe widget explodes under cold cache.\n\n---\n"
        "Promoted from: ideas/widget.md\n"
    )
    assert result.json["description_note"] is None
    assert not idea.exists()  # promotion still consumes the idea file


def test_promote_from_a_differing_body_is_stored_and_echoed(
    kyo, project: Path, write_file
) -> None:
    """KYO-340.c2, second half: a `--from` body that genuinely differs from the
    idea's own text is stored as the description instead, and the write-echo says
    which text landed -- the idea file is still consumed on the same terms `kyo
    promote` already has (KYO-106/KYO-228), whichever text was stored."""
    idea = project / "ideas" / "widget.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# An idea\n\nRaw notes, half-formed.\n", encoding="utf-8")
    record_file = write_file(
        "record.md",
        "---\ntitle: x\ntouches:\n  - kyosei/config.yml\n---\n\n"
        "A cleaned-up description, not the idea's raw text.\n\n"
        "## Criteria\n- c1\n",
    )
    result = kyo("promote", str(idea), "--kind", "task", "--from", record_file, "--json", "--full")
    assert result.code == 0, result
    record = result.json["record"]
    assert "A cleaned-up description, not the idea's raw text." in record["description"]
    assert "Raw notes, half-formed." not in record["description"]
    assert "Promoted from: ideas/widget.md" in record["description"]
    assert not idea.exists()  # promotion still consumes the idea file
    assert result.json["description_note"] is not None
    assert "they differ" in result.json["description_note"]

    # The human-readable echo (not only the --json document) carries the same note.
    idea2 = project / "ideas" / "widget2.md"
    idea2.write_text("# Another idea\n\nRaw notes, half-formed.\n", encoding="utf-8")
    record_file2 = write_file(
        "record2.md",
        "---\ntitle: y\ntouches:\n  - kyosei/config.yml\n---\n\n"
        "Another cleaned-up description.\n\n## Criteria\n- c1\n",
    )
    prose_result = kyo("promote", str(idea2), "--kind", "task", "--from", record_file2)
    assert prose_result.code == 0, prose_result
    assert "stored --from's body" in prose_result.out
    assert "they differ" in prose_result.out


# --------------------------------------------------------------------- KYO-340.c3


def test_the_same_file_is_accepted_by_new_and_promote_check(
    kyo, project: Path, write_file
) -> None:
    """KYO-340.c3: one file drafted once, checked against both routes a kind might
    demand -- fails if either refuses it, which is exactly what happened before
    KYO-340 (`kyo promote --from` refused `plan:` and a body outright)."""
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    idea = project / "ideas" / "widget.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# An idea\n\nSome idea text.\n", encoding="utf-8")
    plan_id = new_record(kyo, kind="plan", title="A plan")
    record_file = write_file(
        "record.md",
        f"---\ntitle: One file, either route\nplan: {plan_id}\ntouches:\n"
        "  - widget.py\n---\n\nA drafted description.\n\n## Criteria\n- c1\n",
    )

    via_new = kyo("new", "task", "--from", record_file, "--check", "--json")
    assert via_new.code == 0, via_new

    via_promote = kyo(
        "promote", str(idea), "--kind", "task", "--from", record_file, "--check", "--json"
    )
    assert via_promote.code == 0, via_promote


def test_spawn_accepts_from_on_the_same_terms(kyo, project: Path, write_file) -> None:
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    source_id = new_record(kyo, kind="bug", title="A bug", criteria=["repro"])
    record_file = write_file(
        "record.md",
        "---\ntitle: Spawned evidence\ntouches:\n  - widget.py\n---\n\n"
        "Spawned description.\n\n## Criteria\n- decided\n",
    )
    result = kyo(
        "spawn", source_id, "--kind", "evidence", "--from", record_file, "--json", "--full"
    )
    assert result.code == 0, result
    record = result.json["record"]
    assert record["title"] == "Spawned evidence"
    assert record["description"].startswith("Spawned description.")
    assert [c["text"] for c in record["criteria"]] == ["decided"]
    assert [t["path"] for t in record["touches"]] == ["widget.py"]


def test_spawn_from_refuses_a_plan_key(kyo, project: Path, write_file) -> None:
    source_id = new_record(kyo, kind="bug", title="A bug", criteria=["repro"])
    record_file = write_file("record.md", "---\ntitle: x\nplan: TST-1\n---\n")
    result = kyo("spawn", source_id, "--kind", "evidence", "--from", record_file, "--check")
    assert result.code == 1, result
    assert "no --plan flag" in result.err
