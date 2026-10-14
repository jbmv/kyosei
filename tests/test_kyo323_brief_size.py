"""KYO-327: a smaller, better-oriented worker brief.

The brief is the one document every dispatched worker reads in full, and it is paid
for on every turn of that worker's context. Measured on this store, a
frozen brief runs to roughly 26,000 bytes (KYO-308.d1: 26,239; KYO-319.d1: 25,549) and
the task-specific part of it is a small fraction: the Standing rules section carried
every worker-audience rule *entire*, each ``enforced-by`` bullet's whole argument —
the history, the measured incident, the admission of what the mechanism does not
cover. That argument is what makes a rule trustworthy and it is read once, if ever.

Two changes, one per criterion, and they pull in opposite directions on purpose:

* **c1 takes bytes out.** Each rule becomes its title, a one-line ``enforced-by``
  (the level, and the commands or tests holding it up) and the first sentence of the
  ledger's reasoning — the ledger's own words throughout, never a paraphrase, so no
  rule's meaning can change. The rest is one read-only command away (`kyo rules`),
  which is the half that makes the shortening a move rather than a deletion. The
  standing *stance* is not a rule and is untouched.
* **c2 puts bytes back, for the thing a worker spends its first turns on.** Where
  each declared symbol was at freeze time, and which test files past dispatches on
  these paths also changed — the store knew both and said neither.

Every test below fails if its mechanism is removed. The two canaries at the bottom
are KYO-34's lesson: a check never shown to fail is not known to check anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import brief as brief_module
from kyosei import commands
from kyosei.procedures import parse_procedures, rule_sections, worker_rules

from .conftest import git_worktree, valid_report

PACKAGED_LEDGER = (
    Path(__file__).resolve().parents[1] / "src" / "kyosei" / "defaults" / "procedures.md"
)


def _ledger_text(project: Path) -> str:
    return (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")


def _rules_section(brief: str) -> str:
    """The Standing rules block of a frozen brief, stance excluded."""
    return brief.split("## Standing rules (worker-audience)")[1].split("## Standing stance")[0]


def _full_text_rules_block(rules) -> str:
    """What the brief rendered before KYO-327: every ``enforced-by`` bullet entire.

    Kept here rather than deleted with the code, because "measurably smaller" is a
    comparison and a comparison needs both sides. This is the pre-KYO-327 renderer,
    verbatim — so the size assertions below are against what this project actually
    used to ship, computed from the same ``Rule`` objects rather than against a
    number somebody typed.
    """
    lines = []
    for r in rules:
        lines.append(f"- **{r.title}** (enforced-by: {r.enforced_by})")
        if r.first_line:
            lines.append(f"  {r.first_line}")
    return "\n".join(lines) + "\n"


def _promote(kyo, project: Path, tmp_path: Path, name: str, touches: list[str]) -> str:
    """A promoted record declaring ``touches`` (the door `kyo promote` uses)."""
    for line in touches:
        target = project / line.split("#", 1)[0].lstrip("+")
        if not target.exists() and not line.endswith("/"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("placeholder\n", encoding="utf-8")
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDoes a thing.\n", encoding="utf-8")
    criteria = tmp_path / f"{name}-criteria.txt"
    criteria.write_text("done\n", encoding="utf-8")
    touch_file = tmp_path / f"{name}-touches.txt"
    touch_file.write_text("".join(f"{line}\n" for line in touches), encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", str(criteria), "--touches-file", str(touch_file), "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _worktree(tmp_path: Path, name: str) -> str:
    # KYO-402: a real linked worktree of the project fixture (``tmp_path / "proj"``);
    # `kyo dispatch --worktree` refuses a bare directory.
    return git_worktree(tmp_path / "proj", tmp_path / name)


def _brief_of(kyo, record: str, worktree: str | None = None) -> str:
    args = ["dispatch", record, "--json"]
    if worktree:
        args += ["--worktree", worktree]
    result = kyo("--full", *args)
    assert result.code == 0, result
    return result.json["record"]["dispatches"][0]["brief"]


def _report_touching(kyo, tmp_path, record_id, worktree, files: list[str]) -> None:
    """One dispatch on ``record_id`` that reports having touched ``files``."""
    dispatched = kyo("dispatch", record_id, "--worktree", worktree, "--json")
    assert dispatched.code == 0, dispatched
    report = tmp_path / f"{dispatched.json['dispatch_id']}.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record_id,
                worked_from_sha=dispatched.json["worked_from_sha"],
                touched_files=files,
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatched.json["dispatch_id"], "--file", str(report)).code == 0


# ------------------------------------------- c1: one line per rule, the rest on demand


def test_every_worker_rule_still_reaches_the_brief(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The check that makes the shortening safe (law 15). A rule that stopped reaching
    its audience would be a rule deleted, not a rule summarised — and the whole of c1
    rests on the claim that nothing was lost, only moved."""
    record = _promote(kyo, git_project, tmp_path, "r", [])
    section = _rules_section(_brief_of(kyo, record))
    rules = worker_rules(_ledger_text(git_project))

    assert rules, "the fixture project ships no worker-audience rules to test with"
    for rule in rules:
        assert rule.title in section, rule.title
        # The ledger's own sentence, not a paraphrase of it.
        assert rule.first_line in section, rule.title


def test_each_rule_carries_one_line_of_enforced_by_and_not_its_essay(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c1 proper. The level is kept verbatim — a rule that said ``mechanism, partial``
    must not come out reading ``mechanism`` — and what the argument *cites* is lifted
    out of it, because "which check will fail me" is the operative half. What goes is
    the argument itself, and only where there was one to go."""
    record = _promote(kyo, git_project, tmp_path, "r", [])
    section = _rules_section(_brief_of(kyo, record))

    for rule in worker_rules(_ledger_text(git_project)):
        summary = brief_module.enforced_by_summary(rule.enforced_by)
        assert f"- **{rule.title}** (enforced-by: {summary})" in section, rule.title
        assert "\n" not in summary, rule.title
        level = rule.enforced_by.split(" — ")[0].strip()
        assert summary.startswith(level), (rule.title, summary)
        if len(rule.enforced_by) > len(level) + 40:
            # There was an essay, and it is not in the brief.
            assert rule.enforced_by not in section, rule.title


def test_the_rules_section_is_measurably_smaller_than_the_full_text_it_replaces(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """"Measurably", against the renderer this replaced rather than against a number.

    Both sides are computed from the same ``Rule`` objects here, so this stays true as
    the ledger grows and fails the moment a rule's essay finds its way back in."""
    rules = worker_rules(_ledger_text(git_project))
    before = _full_text_rules_block(rules)
    after = brief_module._worker_rules_block(rules)

    assert len(after) < len(before) / 2, (len(before), len(after))
    # And the block a real brief carries is that block, not some third rendering.
    section = _rules_section(
        _brief_of(kyo, _promote(kyo, git_project, tmp_path, "r", []))
    )
    assert after.strip() in section


def test_the_full_text_is_one_named_read_only_command_away(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The other half of c1, and the one that would make the first half a deletion if
    it were false. The brief names a command; the command returns the text the brief
    stopped carrying, for every rule it shortened."""
    record = _promote(kyo, git_project, tmp_path, "r", [])
    section = _rules_section(_brief_of(kyo, record))
    assert brief_module.WORKER_RULE_FULL_TEXT_COMMAND in section

    ledger = _ledger_text(git_project)
    for rule in worker_rules(ledger):
        printed = kyo("rules", rule.title)
        assert printed.code == 0, (rule.title, printed)
        # The whole enforced-by bullet — the part the brief no longer carries — comes
        # back, soft-wrap and all.
        assert rule.enforced_by in " ".join(printed.out.split()), rule.title
        # Verbatim, not re-flowed: the brief says "prints one rule verbatim", so the
        # section arrives exactly as the ledger wrote it. (`procedures.rule_bodies`
        # collapses every paragraph onto one line, which is right for comparing two
        # copies and wrong for showing one to a person — hence `rule_sections`.)
        assert rule_sections(ledger)[rule.title] in printed.out, rule.title


def test_rules_lists_every_rule_and_refuses_a_title_it_does_not_have(
    kyo, git_project: Path
) -> None:
    """A pointer a worker cannot act on is not a pointer. `kyo rules` with no argument
    is how a worker finds the title to ask for, and a miss says so rather than
    printing nothing and exiting 0."""
    listed = kyo("rules", "--json")
    assert listed.code == 0, listed
    titles = [r["title"] for r in listed.json["rules"]]
    assert titles == [r.title for r in parse_procedures(_ledger_text(git_project))]

    missed = kyo("rules", "no rule says this")
    assert missed.code != 0
    assert "kyo rules" in (missed.err + missed.out)

    # The remedy the refusal names, followed: bare `kyo rules` lists the titles, and a
    # title taken from that listing is one the command prints rather than refuses.
    found = kyo("rules", titles[0])
    assert found.code == 0, found
    assert titles[0] in found.out


def test_the_standing_stance_is_still_verbatim(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The stance is NOT a rule and is not shortened: it is the one paragraph whose
    exact wording is the mechanism — "contradicting this brief must be cheaper than
    complying with it" is the sentence workers have acted on."""
    record = _promote(kyo, git_project, tmp_path, "r", [])
    brief = _brief_of(kyo, record)
    assert brief_module.STANDING_STANCE.strip() in brief


# ------------------------------- c2: where the symbols are, and where the tests have been


def test_a_symbol_touch_is_told_the_line_it_was_found_at(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c2's first half. The touch was already resolved against the tree when it was
    declared; this keeps the answer instead of making every worker re-derive it."""
    source = git_project / "src" / "widget.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# header\n\n\ndef paint():\n    return 1\n", encoding="utf-8")
    record = _promote(kyo, git_project, tmp_path, "sym", ["src/widget.py#paint"])

    brief = _brief_of(kyo, record, _worktree(tmp_path, "w1"))
    assert "| src/widget.py | paint (line 4) |" in brief
    # ...with the caveat that keeps it from being read as a current fact.
    assert brief_module.TOUCH_SYMBOL_LINE_NOTE in brief


def test_a_whole_file_touch_gets_no_line_and_no_note(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-147.c3's silence, applied here: a claim with no symbol to place contributes
    no number and no paragraph explaining the number it does not have."""
    record = _promote(kyo, git_project, tmp_path, "whole", ["kyosei/config.yml"])
    brief = _brief_of(kyo, record, _worktree(tmp_path, "w2"))

    assert "| kyosei/config.yml | (whole file) |" in brief
    assert "(line " not in brief
    assert brief_module.TOUCH_SYMBOL_LINE_NOTE not in brief


def test_the_tests_past_dispatches_changed_are_listed_as_history(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c2's second half, off the same query the co-occurrence block uses. Two
    dispatches on this path changed ``tests/test_widget.py``; the third is told where
    this project has been putting the tests for it — labelled history, and explicitly
    not a coverage claim."""
    history = _promote(kyo, git_project, tmp_path, "hist", ["src/widget.py"])
    for n in range(2):
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wh{n}"),
            ["src/widget.py", "tests/test_widget.py"],
        )

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["src/widget.py"])
    brief = _brief_of(kyo, fresh, _worktree(tmp_path, "wf"))

    assert "### Where the tests for these files have been" in brief
    assert "| src/widget.py | tests/test_widget.py | 2 of 2 dispatches |" in brief
    assert brief_module.TEST_HISTORY_LABEL in brief
    # The label is the mechanism, not decoration: it must deny coverage in words.
    assert "not a claim that any of them exercises your change" in brief


def test_a_store_with_no_test_history_says_nothing_at_all(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-147.c3 again, and c2 states it outright: nothing is listed when there is no
    history. Not a heading, not an empty table, not "no tests found" — which would be
    a claim about the suite this store cannot make."""
    record = _promote(kyo, git_project, tmp_path, "lonely", ["src/widget.py"])
    brief = _brief_of(kyo, record, _worktree(tmp_path, "w3"))

    assert "Where the tests for these files have been" not in brief
    assert "History, not coverage" not in brief


def test_only_test_files_reach_the_test_history_block(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The block answers one question. A report that names a test file and a mirror
    file contributes the test file here and leaves the mirror to the co-occurrence
    block, which is the one that makes a claim about the worker's own change."""
    history = _promote(kyo, git_project, tmp_path, "mixed", ["src/widget.py"])
    for n in range(2):
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wm{n}"),
            ["src/widget.py", "tests/test_widget.py", "docs/reference/widget.md"],
        )

    fresh = _promote(kyo, git_project, tmp_path, "mixed-fresh", ["src/widget.py"])
    result = kyo("--full", "dispatch", fresh, "--worktree", _worktree(tmp_path, "wmf"), "--json")
    assert result.code == 0, result
    brief = result.json["record"]["dispatches"][0]["brief"]

    block = brief.split("### Where the tests for these files have been")[1].split("\n###")[0]
    assert "tests/test_widget.py" in block
    assert "docs/reference/widget.md" not in block
    # And the classifier the block leans on says the same, directly: it is a label on
    # history and the one place a wrong answer here could come from.
    assert commands.is_test_path("tests/test_widget.py")
    assert commands.is_test_path("apps/desktop/src/__tests__/shell.spec.ts")
    assert not commands.is_test_path("docs/reference/widget.md")
    assert not commands.is_test_path("src/kyosei/contest.py")


# --------------------------------------------------------------------------- canaries


def test_the_size_check_can_fail() -> None:
    """KYO-34's lesson. The shrink assertion is only worth something if it is capable
    of failing — so hand it the pre-KYO-327 rendering as if it were the new one."""
    rules = worker_rules(PACKAGED_LEDGER.read_text(encoding="utf-8"))
    before = _full_text_rules_block(rules)
    assert not len(before) < len(before) / 2


def test_the_summariser_keeps_the_level_and_drops_the_argument() -> None:
    """The unit, on the two shapes the ledger actually writes: a level followed by an
    em dash and an argument, and a bullet that is nothing but its mechanisms."""
    long_form = brief_module.enforced_by_summary(
        "mechanism, partial — `kyo check-branch` refuses it, and "
        + ("history " * 60)
        + "(KYO-1)."
    )
    assert long_form == "mechanism, partial — `kyo check-branch`"
    assert "history" not in long_form

    bare = brief_module.enforced_by_summary("aspirational")
    assert bare == "aspirational"

    # The ledger is project-editable, so a consumer writing the break as an ASCII
    # double hyphen gets a line like everyone else — and the cut is the EARLIEST
    # break, never the first spelling that happens to appear.
    ascii_break = brief_module.enforced_by_summary(
        "aspirational -- nothing checks it — see `kyo doctor` and " + ("why " * 40)
    )
    assert ascii_break == "aspirational — `kyo doctor`"
