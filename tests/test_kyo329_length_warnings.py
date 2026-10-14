"""KYO-333: warn (never refuse) on a long title or a criterion that bundles more
than one separately-checkable claim, on the bare-measurement warning's own terms
(`cmd_dispatch`'s `_measurement_warnings` for `kyo dispatch`'s frozen task prose):
printed after the echo, carried as data under ``--json``, never a refusal and never
an exit-code change.

Measured on this store: titles filed in one day averaged 115 characters (titles are
read in a list); criteria filed the same day averaged 452
characters against 217 store-wide, each bundling several separately-checkable
claims into one sentence a worker can only answer with one verdict. The two
thresholds live as named constants in ``contracts.py``
(:data:`kyosei.contracts.TITLE_LENGTH_WARN`,
:data:`kyosei.contracts.CRITERION_LENGTH_WARN`), with the measurement that chose
them in the comment above them (KYO-333.c4) — this file exercises the sites that
call them, not the constants themselves.

c5, added when the orchestrator met it filing KYO-337: a `--from` front-matter
`touches:` line containing a colon and a space (a heading fragment copied verbatim)
reads to YAML as a one-entry mapping rather than a scalar. The bottom of this file
provokes that refusal and proves quoting the line is the remedy that clears it.
"""

from __future__ import annotations

from pathlib import Path

from kyosei.contracts import CRITERION_LENGTH_WARN, TITLE_LENGTH_WARN

from .conftest import new_record

LONG_TITLE = "x" * (TITLE_LENGTH_WARN + 10)
SHORT_TITLE = "a short title"
LONG_CRITERION = "a" * (CRITERION_LENGTH_WARN + 10) + "."
SHORT_CRITERION = "a short, single claim."


# --------------------------------------------------------------------- KYO-333.c1


def test_new_warns_on_a_long_title(kyo) -> None:
    result = kyo("new", "task", "--title", LONG_TITLE, "--json")
    assert result.code == 0, result  # never refuses
    assert result.json["title_length_warning"] is not None
    assert str(TITLE_LENGTH_WARN) in result.json["title_length_warning"]
    # stored exactly as given, not truncated or rewritten
    assert result.json["record"]["title"] == LONG_TITLE


def test_new_prints_the_title_warning_line(kyo) -> None:
    result = kyo("new", "task", "--title", LONG_TITLE)
    assert result.code == 0, result
    assert "warning:" in result.out
    assert "title is" in result.out


def test_new_with_a_short_title_has_no_warning(kyo) -> None:
    result = kyo("new", "task", "--title", SHORT_TITLE, "--json")
    assert result.code == 0, result
    assert result.json["title_length_warning"] is None
    assert "warning:" not in result.out


def test_new_from_door_warns_on_a_long_title(kyo, write_file) -> None:
    from_file = write_file("rec.md", f"---\ntitle: {LONG_TITLE}\n---\n")
    result = kyo("new", "task", "--from", from_file, "--json")
    assert result.code == 0, result
    assert result.json["title_length_warning"] is not None


def test_promote_warns_on_a_long_title(kyo, project: Path, write_file) -> None:
    idea = project / "ideas" / "long.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text(f"# {LONG_TITLE}\n\nSome body text.\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["title_length_warning"] is not None


def test_spawn_warns_on_a_long_title(kyo, write_file) -> None:
    source = new_record(kyo, kind="bug", title="a short source title", criteria=["x"])
    result = kyo(
        "spawn", source, "--kind", "task",
        "--title", LONG_TITLE,
        "--criteria-file", write_file("c.txt", "y\n"),
        "--touches-file", write_file("t.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["title_length_warning"] is not None


def test_edit_title_warns_on_a_long_title(kyo) -> None:
    record = new_record(kyo, title=SHORT_TITLE, criteria=["x"])
    result = kyo("edit", record, "--title", LONG_TITLE, "--json")
    assert result.code == 0, result
    assert result.json["title_length_warning"] is not None
    assert result.json["record"]["title"] == LONG_TITLE


def test_edit_that_does_not_touch_the_title_carries_no_title_warning(kyo) -> None:
    record = new_record(kyo, title=SHORT_TITLE, criteria=["x"])
    result = kyo(
        "edit", record, "--add-criteria-file", "-", "--json",
        stdin="another short claim.\n",
    )
    assert result.code == 0, result
    assert result.json["title_length_warning"] is None


# --------------------------------------------------------------------- KYO-333.c2


def test_new_warns_on_a_long_criterion(kyo, write_file) -> None:
    result = kyo(
        "new", "task", "--title", SHORT_TITLE,
        "--criteria-file", write_file("c.txt", LONG_CRITERION + "\n"),
        "--json",
    )
    assert result.code == 0, result
    warnings = result.json["criteria_length_warnings"]
    assert len(warnings) == 1
    assert str(CRITERION_LENGTH_WARN) in warnings[0]["warning"]
    # stored exactly as given
    stored = kyo("show", result.json["record_id"], "--json").json
    assert stored["criteria"][0]["text"] == LONG_CRITERION


def test_new_warns_on_semicolon_bundled_criteria(kyo, write_file) -> None:
    bundled = "the export round-trips; the ledger stays append-only; doctor is clean."
    result = kyo(
        "new", "task", "--title", SHORT_TITLE,
        "--criteria-file", write_file("c.txt", bundled + "\n"),
        "--json",
    )
    assert result.code == 0, result
    warnings = result.json["criteria_length_warnings"]
    assert len(warnings) == 1
    assert "clauses" in warnings[0]["warning"]


def test_semicolons_inside_backticks_are_not_counted_as_clauses(kyo, write_file) -> None:
    # One shell command, quoted verbatim, carrying its own semicolon -- a single
    # claim, not two bundled ones.
    text = "running `cmd a; cmd b` leaves the store clean."
    result = kyo(
        "new", "task", "--title", SHORT_TITLE,
        "--criteria-file", write_file("c.txt", text + "\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["criteria_length_warnings"] == []


def test_new_with_a_short_single_claim_criterion_has_no_warning(kyo, write_file) -> None:
    result = kyo(
        "new", "task", "--title", SHORT_TITLE,
        "--criteria-file", write_file("c.txt", SHORT_CRITERION + "\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["criteria_length_warnings"] == []


def test_promote_warns_on_a_long_criterion(kyo, project: Path, write_file) -> None:
    idea = project / "ideas" / "x.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text("# a short idea title\n\nbody\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", LONG_CRITERION + "\n"),
        "--touches-file", write_file("t.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert len(result.json["criteria_length_warnings"]) == 1


def test_spawn_warns_on_a_long_criterion(kyo, write_file) -> None:
    source = new_record(kyo, kind="bug", title="a short source title", criteria=["x"])
    result = kyo(
        "spawn", source, "--kind", "task",
        "--title", SHORT_TITLE,
        "--criteria-file", write_file("c.txt", LONG_CRITERION + "\n"),
        "--touches-file", write_file("t.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    assert len(result.json["criteria_length_warnings"]) == 1


def test_edit_add_criteria_file_warns_on_a_long_criterion(kyo, write_file) -> None:
    record = new_record(kyo, title=SHORT_TITLE, criteria=["x"])
    result = kyo(
        "edit", record, "--add-criteria-file", write_file("c.txt", LONG_CRITERION + "\n"),
        "--json",
    )
    assert result.code == 0, result
    assert len(result.json["criteria_length_warnings"]) == 1


def test_edit_criterion_text_file_correction_warns(kyo, write_file) -> None:
    record = new_record(kyo, title=SHORT_TITLE, criteria=["short claim"])
    show = kyo("show", record, "--json").json
    criterion_id = show["criteria"][0]["id"]
    result = kyo(
        "edit", record, "--criterion", criterion_id,
        "--text-file", write_file("c2.txt", LONG_CRITERION + "\n"),
        "--json",
    )
    assert result.code == 0, result
    assert len(result.json["criteria_length_warnings"]) == 1
    assert result.json["criteria_length_warnings"][0]["criterion_id"] == criterion_id


def test_restate_warns_on_a_long_replacement(kyo, write_file) -> None:
    record = new_record(kyo, title=SHORT_TITLE, criteria=["short claim"])
    show = kyo("show", record, "--json").json
    criterion_id = show["criteria"][0]["id"]
    result = kyo(
        "restate", criterion_id,
        "--text-file", write_file("c3.txt", LONG_CRITERION + "\n"),
        "--json",
    )
    assert result.code == 0, result
    assert len(result.json["criteria_length_warnings"]) == 1


# --------------------------------------------------------------------- KYO-333.c3


def test_neither_warning_ever_refuses(kyo, write_file) -> None:
    """Both warnings fire together on one filing and the command still succeeds --
    the charter's own law, "flags, never enforces"."""
    result = kyo(
        "new", "task", "--title", LONG_TITLE,
        "--criteria-file", write_file("c.txt", LONG_CRITERION + "\n"),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["ok"] is True
    assert result.json["title_length_warning"] is not None
    assert len(result.json["criteria_length_warnings"]) == 1


def test_json_carries_both_warnings_as_data_even_without_a_warning(kyo) -> None:
    """The keys are present and null/empty rather than absent when nothing warns --
    so a consumer never has to distinguish "no warning" from "this build predates
    the field"."""
    result = kyo("new", "task", "--title", SHORT_TITLE, "--json")
    assert result.code == 0, result
    assert "title_length_warning" in result.json
    assert result.json["title_length_warning"] is None
    assert result.json["criteria_length_warnings"] == []


# --------------------------------------------------------------------- KYO-333.c5


def test_from_touches_mapping_line_is_refused_naming_quoting_as_the_remedy(
    kyo, write_file
) -> None:
    """A touches line with a colon and a space -- a section heading copied verbatim
    -- reads to YAML as a one-entry mapping, not a scalar. Met filing KYO-337: this
    used to fall through to a bare ``str(item)`` and store a Python dict repr as the
    touch path."""
    from_file = write_file(
        "bad.md",
        "---\n"
        "title: touches mapping test\n"
        "touches:\n"
        "  - .claude/skills/kyosei/SKILL.md#4. Ingesting reports: read, don't retype\n"
        "---\n",
    )
    result = kyo("new", "task", "--from", from_file, "--check")
    assert result.code != 0
    assert "not a string" in result.err
    assert "quote" in result.err


def test_quoting_the_touches_line_clears_the_refusal(kyo, project: Path, write_file) -> None:
    # The touched path has to actually exist for `--check` to get past touch
    # validation and prove the refusal above is really gone, not merely replaced
    # by a later one.
    touched = project / ".claude" / "skills" / "kyosei" / "SKILL.md"
    touched.parent.mkdir(parents=True, exist_ok=True)
    touched.write_text("# dummy\n## 4. Ingesting reports: read, don't retype\n", encoding="utf-8")

    from_file = write_file(
        "good.md",
        "---\n"
        "title: touches mapping test\n"
        'touches:\n'
        '  - ".claude/skills/kyosei/SKILL.md#4. Ingesting reports: read, don\'t retype"\n'
        "---\n",
    )
    result = kyo("new", "task", "--from", from_file, "--check", "--json")
    assert result.code == 0, result
    assert result.json["touches"][0]["path"] == ".claude/skills/kyosei/SKILL.md"


def test_a_touches_entry_that_is_an_ordinary_string_is_unaffected(kyo, write_file) -> None:
    from_file = write_file(
        "plain.md",
        "---\ntitle: plain touches\ntouches:\n  - kyosei/config.yml\n---\n",
    )
    result = kyo("new", "task", "--from", from_file, "--check", "--json")
    assert result.code == 0, result
    assert result.json["touches"][0]["path"] == "kyosei/config.yml"


# ----------------------------------------------------------------------- KYO-348
#
# The bare-measurement warning's own blind spot, filed by the same consumer that
# filed KYO-333 and on the same terms. `_measurement_warnings` saw the task prose
# and nothing else, so it could not tell a number REMEMBERED from a number
# SPECIFIED: their controller wrote "a 15s grace" for a record whose own criterion
# says `15s`, and `kyo dispatch` asked whether it had measured a figure it had
# just designed. A design constant is not a measurement.
#
# The fix gives the check sight of the criteria of the dispatched record and of
# every record named with `--also-covers` — the same set the frozen brief already
# renders criteria tables for — and nothing else. It is still a warning, it still
# never refuses, and a figure no criterion states still warns exactly as it did,
# which is the half asserted second and the half that makes the first worth
# anything (KYO-34's lesson: a silencer never shown to stay loud is not known to
# be a silencer rather than a mute).


def test_a_duration_the_records_own_criterion_states_raises_no_warning(
    kyo, git_project, write_file
) -> None:
    from .conftest import new_record

    record = new_record(
        kyo,
        kind="task",
        title="Grace period",
        criteria=["the guard waits 15s before it fires, and says so"],
    )
    # Deliberately NOT --json: the silence has to be silence on the surface a
    # controller actually reads, not merely an empty list in a payload.
    result = kyo(
        "dispatch", record,
        "--task-file", write_file("t.txt", "Add a 15s grace before the guard fires."),
    )
    assert result.code == 0, result
    assert "warning:" not in result.out
    assert "15s" not in result.out


def test_the_same_figure_with_no_criterion_stating_it_still_warns(
    kyo, git_project, write_file
) -> None:
    from .conftest import new_record

    record = new_record(
        kyo,
        kind="task",
        title="Grace period",
        criteria=["the guard waits before it fires, and says how long"],
    )
    result = kyo(
        "dispatch", record,
        "--task-file", write_file("t.txt", "Add a 15s grace before the guard fires."),
        "--json",
    )
    assert result.code == 0, result  # still a warning, never a refusal
    assert result.json["measurement_warnings"] == ["15s"]


def test_whitespace_between_the_number_and_its_unit_is_ignored(
    kyo, git_project, write_file
) -> None:
    """Same figure, two spellings. The criterion says `15 s`; the prose says `15s`."""
    from .conftest import new_record

    record = new_record(
        kyo, kind="task", title="Grace", criteria=["the guard waits 15 s before it fires"]
    )
    result = kyo(
        "dispatch", record,
        "--task-file", write_file("t.txt", "Add a 15s grace."),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["measurement_warnings"] == []


def test_a_criterion_of_an_also_covered_record_silences_it_too(
    kyo, git_project, write_file
) -> None:
    """KYO-12's rule, applied to this warning: what a dispatch formally covers is
    what its brief speaks for, so the criteria of an also-covered record are as much
    a specification the controller is writing to as the primary record's."""
    from .conftest import new_record

    primary = new_record(kyo, kind="task", title="Guard", criteria=["the guard exists"])
    covered = new_record(
        kyo, kind="task", title="Grace", criteria=["the guard waits 15s before it fires"]
    )
    result = kyo(
        "dispatch", primary, "--also-covers", covered,
        "--task-file", write_file("t.txt", "Add a 15s grace before the guard fires."),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["measurement_warnings"] == []


def test_a_criterion_of_a_record_this_dispatch_does_not_cover_silences_nothing(
    kyo, git_project, write_file
) -> None:
    """The other direction of the one above, and the reason it is a separate test
    rather than a second half: a figure is silenced because THIS dispatch formally
    covers the record that states it, never because some record in the store happens
    to mention it. Reading the whole board here would silence a warning on the
    strength of a criterion the worker's brief does not contain."""
    from .conftest import new_record

    new_record(kyo, kind="task", title="Grace", criteria=["the guard waits 15s before it fires"])
    primary = new_record(kyo, kind="task", title="Guard", criteria=["the guard exists"])
    result = kyo(
        "dispatch", primary,
        "--task-file", write_file("t.txt", "Add a 15s grace before the guard fires."),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["measurement_warnings"] == ["15s"]


def test_a_smaller_figure_inside_a_larger_one_is_not_silenced(
    kyo, git_project, write_file
) -> None:
    """`5s` occurs inside `15s`. A substring comparison would read a criterion about
    fifteen seconds as a statement about five, which is the false silence that makes
    a warning worse than no warning."""
    from .conftest import new_record

    record = new_record(
        kyo, kind="task", title="Grace", criteria=["the guard waits 15s before it fires"]
    )
    result = kyo(
        "dispatch", record,
        "--task-file", write_file("t.txt", "Poll every 5s until the guard fires."),
        "--json",
    )
    assert result.code == 0, result
    assert result.json["measurement_warnings"] == ["5s"]
