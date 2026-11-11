"""KYO-802: the session-start brief asks for the docs-coverage decision until it is made.

The block is printed in the compact and the full form while ``docs_coverage.level`` is
absent and the project has ungoverned code; it ends when any level is recorded, and it is
never printed where no document exists. ``kyo onboard --check`` reads the same level.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import cmd_onboard
from tests.test_kyo801_docs_coverage import _basic, _put

HEADING = "DOCS COVERAGE: not yet decided for this project"


def _brief(kyo, *flags: str) -> str:
    result = kyo("brief", *flags)
    assert result.code == 0, result
    return result.out


def test_the_block_prints_in_every_form_with_counts_reason_and_commands(
    kyo, git_project: Path
) -> None:
    _basic(git_project)
    for flags in (("--compact",), ("--compact", "--session-start"), ()):
        text = _brief(kyo, *flags)
        assert HEADING in text
        assert "KYO-790" in text and "74 documentation errors" in text
        assert "`kyo docs coverage`" in text
        assert "`kyo docs coverage --level notice|problem|off`" in text
        assert "your person" in text
    # a.py is governed; b.py and sub/c.ts are not, of three source files
    assert "2 of 3 source files are governed by no document section" in text
    data = kyo("brief", "--json").json
    assert data["docs_coverage_ask"]["ungoverned"] == 2
    assert data["docs_coverage_ask"]["code"] == 3


def test_any_recorded_level_ends_the_block(kyo, git_project: Path) -> None:
    _basic(git_project)
    assert HEADING in _brief(kyo, "--compact")
    for level in ("notice", "problem", "off"):
        if level == "problem":
            # problem needs a declared code list; notice and off do not
            continue
        assert kyo("docs", "coverage", "--level", level).code == 0
        assert HEADING not in _brief(kyo, "--compact")
        assert kyo("brief", "--json").json["docs_coverage_ask"] is None
        (git_project / "kyosei" / "config.yml").write_text(
            (git_project / "kyosei" / "config.yml")
            .read_text(encoding="utf-8")
            .split("\ndocs_coverage:", 1)[0]
            .rstrip("\n")
            + "\n",
            encoding="utf-8",
        )
        assert HEADING in _brief(kyo, "--compact")


def test_no_block_in_a_project_with_no_documents(kyo, git_project: Path) -> None:
    _put(git_project, {"src/a.py": "A = 1\n", "src/b.py": "B = 1\n"})
    assert HEADING not in _brief(kyo, "--compact")
    assert kyo("brief", "--json").json["docs_coverage_ask"] is None


def test_no_block_where_every_file_is_governed(kyo, git_project: Path) -> None:
    _put(
        git_project,
        {
            "docs/notes.md": "# Notes\n\n## Section\n\ngoverns: src/a.py\n\nText.\n",
            "src/a.py": "A = 1\n",
        },
    )
    assert HEADING not in _brief(kyo, "--compact")


def test_onboard_check_reads_the_level_as_unanswered_until_set(
    kyo, git_project: Path
) -> None:
    assert "docs_coverage" in cmd_onboard.PROJECT_LABELS
    _basic(git_project)

    def item() -> dict:
        result = kyo("onboard", "--check", "--json")
        (project,) = result.json["projects"]
        return next(i for i in project["items"] if i["label"] == "docs_coverage")

    first = item()
    assert first["status"] == "missing"
    assert "kyo docs coverage --level" in first["fix"]
    assert kyo("docs", "coverage", "--level", "notice").code == 0
    assert item()["status"] == "present"
