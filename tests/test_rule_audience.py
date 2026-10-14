"""A rule can name more than one audience.

"Never state a timestamp, line count, or other tool-derivable fact from memory"
was ``audience: worker``. Brief generation injects worker rules into the *worker's*
brief and controller rules into `kyo brief` — so the controller, who writes the
brief's task-specific prose and therefore writes more tool-derivable facts than
any worker does, never had it in context, and a misstated test count reached frozen
briefs until workers corrected it via ``corrected_numbers[]``.

Law 15 working correctly on a mis-assigned input. There was no two-audience syntax,
and that absence was itself the defect.

The generalisable form, which is why this got its own module: **a rule that only
its non-authors can see is a rule the tool cannot enforce against the person most
able to break it.**
"""

from __future__ import annotations

from pathlib import Path

from kyosei.procedures import human_rules, orchestrator_rules, unknown_audiences, worker_rules

TWO_AUDIENCES = """\
### A rule for two audiences

- **audience**: worker, controller
- **enforced-by**: aspirational

Both of them need this one.
"""

ONE_AUDIENCE = """\
### A rule for one audience

- **audience**: worker
- **enforced-by**: aspirational

Only the worker needs this one.
"""

BAD_AUDIENCE = """\
### A rule nobody selects on

- **audience**: reviewer
- **enforced-by**: aspirational

Nothing filters on this.
"""


def test_a_rule_can_name_two_audiences() -> None:
    assert [r.title for r in worker_rules(TWO_AUDIENCES)] == ["A rule for two audiences"]
    assert [r.title for r in orchestrator_rules(TWO_AUDIENCES)] == ["A rule for two audiences"]
    assert human_rules(TWO_AUDIENCES) == []


def test_a_single_audience_rule_still_reaches_only_that_audience() -> None:
    """The change is additive: the old syntax means exactly what it did."""
    assert [r.title for r in worker_rules(ONE_AUDIENCE)] == ["A rule for one audience"]
    assert orchestrator_rules(ONE_AUDIENCE) == []


def test_an_audience_nothing_selects_on_is_reported(kyo, project: Path) -> None:
    """Law 2: a field with no consumer is decoration, and an audience nothing
    filters on is a rule that reaches nobody while looking like it reaches
    someone — the same shape as the bug this whole module is about."""
    assert unknown_audiences(BAD_AUDIENCE) == [("A rule nobody selects on", "reviewer")]

    procedures = project / "kyosei" / "procedures.md"
    procedures.write_text(procedures.read_text() + "\n" + BAD_AUDIENCE, encoding="utf-8")
    result = kyo("doctor")
    assert result.code != 0
    assert "procedures" in result.err
    assert "reaches nobody" in result.err


def test_the_tool_derivable_rule_now_reaches_the_controller(kyo, project: Path) -> None:
    """The specific mis-assignment, in this project's shipped defaults."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    titles = {r.title for r in orchestrator_rules(text)}
    assert any("tool-derivable fact" in title for title in titles)

    brief = kyo("brief").out
    assert "tool-derivable fact" in brief


def test_it_still_reaches_the_worker(kyo, project: Path) -> None:
    """Widening must not narrow: the rule was a worker rule for good reason."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    titles = {r.title for r in worker_rules(text)}
    assert any("tool-derivable fact" in title for title in titles)


def test_dispatch_warns_about_a_bare_measurement_in_task_prose(
    kyo, git_project: Path, write_file
) -> None:
    """The brief freezes the instant it is generated
    and the worker cannot correct it before acting, so this warning has exactly one
    useful moment to fire — and this error class is always the same syntactic
    shape."""
    from .conftest import new_record

    record = new_record(kyo, kind="task", title="Measured", criteria=["a"])
    result = kyo(
        "dispatch", record,
        "--task-file", write_file("t.txt", "npm test is 62 tests, ~20s. Make it faster."),
        "--json",
    )
    assert result.code == 0, result
    assert "62 tests" in result.json["measurement_warnings"]
    assert "~20s" in result.json["measurement_warnings"]


def test_the_warning_does_not_refuse_the_dispatch(kyo, git_project: Path, write_file) -> None:
    """Flags, never enforces — a measured number is perfectly legitimate, and the
    warning only asks whether it was measured."""
    from .conftest import new_record

    record = new_record(kyo, kind="task", title="Measured", criteria=["a"])
    result = kyo(
        "dispatch", record, "--task-file", write_file("t.txt", "There are 114 tests."),
    )
    assert result.code == 0, result
    assert "warning:" in result.out
    assert record in kyo("show", record, "--json").json["id"]


def test_prose_with_no_measurements_warns_about_nothing(
    kyo, git_project: Path, write_file
) -> None:
    from .conftest import new_record

    record = new_record(kyo, kind="task", title="Unmeasured", criteria=["a"])
    result = kyo(
        "dispatch", record,
        "--task-file", write_file("t.txt", "Make the banner move to the other pane."),
        "--json",
    )
    assert result.json["measurement_warnings"] == []
