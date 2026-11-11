"""KYO-773: a dispatch's brief says each thing once, and its length is printed by section.

Measured on five briefs frozen on 2026-11-11 (KYO-751.d1, KYO-746.d1, KYO-770.d1,
KYO-763.d1, KYO-774.d1), the text a brief repeated verbatim was in two tables of the
Touches section. The touches table had one row per record and path, so a path six
covered records declared was six rows. The test-history table had one row per declared
file and test, so each declared path and its dispatch count were printed up to five
times. Both now have one row per path. The third repetition was smaller: the
disjointness block printed one sentence per running dispatch, and for a wave it is the
same sentence each time.

`kyo dispatch` also prints the brief's length by section under its digest, so the
section that grew is named rather than inferred from a total.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from kyosei import brief as brief_module

from .conftest import git_worktree, new_record, valid_report


def _promote(kyo, project: Path, tmp_path: Path, name: str, touches: list[str]) -> str:
    """A promoted record declaring ``touches`` (the door `kyo promote` uses)."""
    for line in touches:
        target = project / line.split("#", 1)[0].lstrip("+")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("placeholder\n", encoding="utf-8")
        symbol = line.partition("#")[2]
        if symbol and symbol not in target.read_text(encoding="utf-8"):
            with target.open("a", encoding="utf-8") as handle:
                handle.write(f"def {symbol}():\n    return None\n")
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


def _brief(result) -> str:
    assert result.code == 0, result
    return result.json["record"]["dispatches"][0]["brief"]


def _touch_rows(brief: str) -> list[list[str]]:
    """The rows of the touches table, each as its three cells."""
    table = brief.split("## Touches", 1)[1].split("This is also the set", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[1:]]


# ----------------------------------------------------- c1: one row per path


def test_a_path_three_records_declare_is_one_row_naming_all_three(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The criterion's own case. Against the earlier renderer this table has three rows
    for ``src/shared.py``, one per record, and the assertion on the row count fails."""
    first = _promote(kyo, git_project, tmp_path, "first", ["src/shared.py", "src/own.py"])
    second = _promote(kyo, git_project, tmp_path, "second", ["src/shared.py"])
    third = _promote(kyo, git_project, tmp_path, "third", ["src/shared.py", "src/third.py"])

    brief = _brief(kyo("--full", "dispatch", first, "--also-covers", second, third, "--json"))
    rows = _touch_rows(brief)

    shared = [row for row in rows if row[1] == "src/shared.py"]
    assert len(shared) == 1, rows
    assert shared[0] == [
        f"{first} (this dispatch's own record); {second}, {third} (--also-covers)",
        "src/shared.py",
        "(whole file)",
    ]
    # Every path once, and a path one record declares still says which.
    assert sorted(row[1] for row in rows) == ["src/own.py", "src/shared.py", "src/third.py"]
    assert [f"{first} (this dispatch's own record)", "src/own.py", "(whole file)"] in rows
    assert [f"{third} (--also-covers)", "src/third.py", "(whole file)"] in rows
    # The reader of this table still finds every path, so the boundary a worker is
    # told is unchanged by the merge (KYO-76.c2).
    assert brief_module.touch_paths_named_in(brief) == {
        "src/own.py", "src/shared.py", "src/third.py",
    }


def test_a_symbol_two_records_declare_is_named_once(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The symbol column is merged the same way: each symbol once, in the order the
    records first name it."""
    first = _promote(kyo, git_project, tmp_path, "first", ["src/shared.py#alpha"])
    second = _promote(
        kyo, git_project, tmp_path, "second", ["src/shared.py#alpha", "src/shared.py#beta"]
    )

    brief = _brief(kyo("--full", "dispatch", first, "--also-covers", second, "--json"))
    (row,) = _touch_rows(brief)

    assert row[0] == f"{first} (this dispatch's own record); {second} (--also-covers)"
    assert re.fullmatch(r"alpha \(line \d+\), beta \(line \d+\)", row[2]), row


def test_a_whole_file_claim_is_not_hidden_by_another_records_symbol(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """One record claims the file and another names a symbol in it. The claim is the
    whole file, and a merged cell that listed only the symbol would understate it."""
    first = _promote(kyo, git_project, tmp_path, "first", ["src/shared.py#alpha"])
    second = _promote(kyo, git_project, tmp_path, "second", ["src/shared.py"])

    brief = _brief(kyo("--full", "dispatch", first, "--also-covers", second, "--json"))
    (row,) = _touch_rows(brief)

    assert row[2].startswith("(whole file), alpha"), row


# ----------------------------------------------------- c2: the length by section


def test_dispatch_prints_the_briefs_length_by_section_beside_its_digest(
    kyo, git_project: Path
) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--task-file", "-", stdin="Fix the race.")
    assert result.code == 0, result

    lines = result.out.splitlines()
    (at,) = [i for i, line in enumerate(lines) if line.startswith("stored brief (")]
    total = int(re.match(r"stored brief \((\d+) chars", lines[at]).group(1))
    by_section = lines[at + 1]
    assert by_section.startswith("  by section (chars): "), by_section
    named = dict(
        re.fullmatch(r"(.+) (\d+)", part).groups()
        for part in by_section.split(": ", 1)[1].split(" · ")
    )
    assert list(named) == [
        "header", "record summary", "criteria", "touches", "claims", "write scope",
        "standing rules", "stance", "task", "report",
    ]
    assert sum(int(chars) for chars in named.values()) == total


def test_a_longer_task_grows_the_task_section_and_no_other(kyo, git_project: Path) -> None:
    """What the line is for: the section that grew is the one whose number moved."""

    def sections(prose: str) -> dict[str, int]:
        record = new_record(kyo, title="Same title", criteria=["a"])
        result = kyo("dispatch", record, "--task-file", "-", "--force", "--json", stdin=prose)
        assert result.code == 0, result
        return {row["section"]: row["chars"] for row in result.json["brief_sections"]}

    short = sections("Fix it.")
    long = sections("Fix it." + " And explain why." * 40)

    assert long["task"] - short["task"] == len(" And explain why.") * 40
    for name in ("standing rules", "stance", "write scope", "report", "criteria"):
        assert long[name] == short[name], name


def test_the_json_lengths_sum_to_the_stored_brief(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["a"])
    covered = new_record(kyo, criteria=["b"])
    result = kyo("--full", "dispatch", primary, "--also-covers", covered, "--json")

    brief = _brief(result)
    sections = result.json["brief_sections"]
    assert sum(row["chars"] for row in sections) == len(brief)
    # A covered record adds its section, between the criteria and the touches.
    names = [row["section"] for row in sections]
    assert names[names.index("criteria") + 1 : names.index("touches") + 1] == [
        "also covers", "touches",
    ]


def test_every_heading_the_brief_writes_is_one_the_lengths_are_split_on(
    kyo, git_project: Path
) -> None:
    """The writer and the reader share one table. A ``## `` heading added to the brief
    without a row in it would be counted into the section above it, silently."""
    record = new_record(kyo, criteria=["a"])
    brief = _brief(kyo("--full", "dispatch", record, "--json"))

    known = {heading for _name, heading in brief_module.BRIEF_SECTION_HEADINGS}
    written = [line for line in brief.splitlines() if line.startswith("## ")]
    assert written and set(written) <= known, set(written) - known


def test_a_heading_inside_the_task_prose_opens_no_section() -> None:
    """The task is the orchestrator's own prose and may carry headings, including one
    spelled like a generated heading that comes earlier in the brief."""
    brief = "\n".join([
        "# Dispatch brief", "", "## Record summary", "", "text", "",
        "## Criteria", "", "rows", "", "## Standing stance", "", "stance", "", "---", "",
        "## Task", "", "do this", "", "## Criteria", "", "and this", "", "## Notes", "",
        "## Report", "", "write it", "",
    ])
    lengths = brief_module.brief_section_lengths(brief)

    assert [name for name, _ in lengths] == [
        "header", "record summary", "criteria", "stance", "task", "report",
    ]
    assert sum(chars for _, chars in lengths) == len(brief)
    task = dict(lengths)["task"]
    assert task == len("## Task\n\ndo this\n\n## Criteria\n\nand this\n\n## Notes\n\n")


# ----------------------------------------------------- c3: the other repetition


def _worktree(tmp_path: Path, name: str) -> str:
    return git_worktree(tmp_path / "proj", tmp_path / name)


def test_the_test_history_has_one_row_for_each_declared_file(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """Two test files changed alongside one declared file are one row. Against the
    earlier renderer they are two rows, each repeating the declared path and the
    number of dispatches."""
    history = _promote(kyo, git_project, tmp_path, "hist", ["src/widget.py"])
    for n, files in enumerate([
        ["src/widget.py", "tests/test_widget.py", "tests/test_gadget.py"],
        ["src/widget.py", "tests/test_widget.py"],
    ]):
        dispatched = kyo("dispatch", history, "--worktree", _worktree(tmp_path, f"wh{n}"), "--json")
        assert dispatched.code == 0, dispatched
        report = tmp_path / f"report{n}.json"
        report.write_text(
            json.dumps(
                valid_report(
                    dispatch_id=dispatched.json["dispatch_id"],
                    record_id=history,
                    worked_from_sha=dispatched.json["worked_from_sha"],
                    touched_files=files,
                )
            ),
            encoding="utf-8",
        )
        assert kyo("report", dispatched.json["dispatch_id"], "--file", str(report)).code == 0

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["src/widget.py"])
    brief = _brief(
        kyo("--full", "dispatch", fresh, "--worktree", _worktree(tmp_path, "wf"), "--json")
    )

    block = brief.split("### Where the tests for these files have been")[1].split("\n##")[0]
    rows = [line for line in block.splitlines() if line.startswith("| src/")]
    assert rows == [
        "| src/widget.py | 2 | tests/test_widget.py (2), tests/test_gadget.py (1) |"
    ], block


def test_running_dispatches_with_one_finding_share_one_line(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """Two running dispatches, neither ever checked against work: the finding is said
    once and names both. Against the earlier renderer it is two lines."""
    a = _promote(kyo, git_project, tmp_path, "a", ["src/a.py"])
    b = _promote(kyo, git_project, tmp_path, "b", ["src/b.py", "src/b2.py"])
    c = _promote(kyo, git_project, tmp_path, "c", ["src/c.py"])
    first = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa"), "--json")
    second = kyo("dispatch", b, "--worktree", _worktree(tmp_path, "wb"), "--json")
    assert first.code == 0 and second.code == 0, (first, second)

    brief = _brief(
        kyo("--full", "dispatch", c, "--worktree", _worktree(tmp_path, "wc"), "--json")
    )
    block = brief.split("### What the disjointness check compared")[1].split("\n##")[0]

    assert block.count("never checked against work") == 1, block
    assert (
        f"- `{first.json['dispatch_id']}` (dispatching {a}, 1 declared path(s)), "
        f"`{second.json['dispatch_id']}` (dispatching {b}, 2 declared path(s)): "
        "never checked against work"
    ) in block, block
