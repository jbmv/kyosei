"""KYO-566: nothing sends ideas to `ideas/scratchpad.md`, which every reader skips.

KYO-114 moved the scratchpad to the per-project home (``serve_files.scratchpad_target``,
what the desktop app's Scratchpad opens), and ``ideas.idea_files`` and the app's tree skip
any ``scratchpad.md`` under ``ideas/``. The skill, onboarding and the guide kept telling
people and orchestrators to write there, and `kyo init` kept scaffolding the file, so a
thought written in it was found again only by somebody holding its path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kyosei import scaffold
from kyosei.serve_files import project_paths

REPO = Path(__file__).resolve().parents[1]
RETIRED_PATH = "ideas/scratchpad.md"


def _packaged_and_guide_text() -> list[Path]:
    """Every packaged text file and every guide page, and the README."""
    defaults = REPO / "src" / "kyosei" / "defaults"
    files = [
        p
        for p in defaults.rglob("*")
        if p.is_file() and p.suffix in {".md", ".yml", ".sh", ".py", ".json", ".txt"}
    ]
    # KYO-593: the `/kyosei` skill's two files moved out of defaults/ into the plugin.
    files += sorted((REPO / "src/kyosei/harness/claude-code/plugin/skills/kyosei").rglob("*.md"))
    files += sorted((REPO / "docs" / "guide").rglob("*.md"))
    files += [REPO / "README.md"]
    return [p for p in files if p.is_file()]


def test_no_packaged_text_or_guide_page_names_ideas_scratchpad() -> None:
    """KYO-566.c1. The literal path is never named in what a reader is told to follow.

    A mention is refused outright rather than judged as "a place to write": the skill's
    warning says "a `scratchpad.md` under `ideas/`" precisely so that this check can stay
    a plain substring test, which nobody has to argue with.
    """
    offenders = [
        f"{path.relative_to(REPO)}:{n}"
        for path in _packaged_and_guide_text()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if RETIRED_PATH in line
    ]
    assert offenders == [], (
        f"{RETIRED_PATH} is skipped by `kyo ideas review` and the app's ideas tree "
        f"(KYO-114), so text sending a reader there loses their thought: {offenders}"
    )


def test_the_check_reads_the_files_it_claims_to() -> None:
    names = {p.name for p in _packaged_and_guide_text()}
    assert {
        "SKILL.md",
        "REFERENCE.md",
        "onboard.md",
        "setup-decisions.md",
        "01-what-kyosei-is.md",
    } <= names


@pytest.mark.parametrize(
    "path",
    [
        REPO / "src/kyosei/harness/claude-code/plugin/skills/kyosei" / "SKILL.md",
    ],
)
def test_the_skill_names_the_scratchpad_and_where_a_lasting_thought_goes(path: Path) -> None:
    """KYO-566.c2, as KYO-572.c2 restated it: the Ideas paragraph names the scratchpad by
    the `kyo where` line that prints its path (no longer the path form), and sends a
    thought to be found later to its own file."""
    text = path.read_text(encoding="utf-8")
    start = text.index("**Ideas**")
    paragraph = text[start : text.index("\n\n", start)]
    assert "`kyo where` prints as `scratchpad:`" in paragraph
    assert "a thought to be found later goes in its own `ideas/<name>.md`" in paragraph


def test_init_scaffolds_no_ideas_scratchpad(project: Path) -> None:
    """KYO-566.c4, in-repo layout (the split layout is pinned in test_split_store)."""
    assert (project / "ideas").is_dir()
    assert not (project / "ideas" / "scratchpad.md").exists()
    assert all(
        not relative.endswith(scaffold.SCRATCHPAD_BASENAME)
        for relative, _ in scaffold.SCAFFOLD_FILES
    )


def _expected_scratchpad(project: Path) -> Path:
    return project_paths(str(project), dict(os.environ)).scratchpad


def test_doctor_reports_a_written_ideas_scratchpad_as_a_notice(kyo, project: Path) -> None:
    """KYO-566.c3. Invisible to the app and the CLI, and where its content belongs."""
    leftover = project / "ideas" / "scratchpad.md"
    leftover.write_text("# TLS for the dev-box server\n", encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == 0, "a notice never fails the hook it is printed from"
    assert result.json["problems"] == []
    (notice,) = [n for n in result.json["notices"] if n.startswith("[ideas_scratchpad] ")]
    assert str(leftover) in notice
    assert "invisible to the desktop app and to the CLI" in notice
    assert str(_expected_scratchpad(project)) in notice, "names the scratchpad's real path"
    assert "ideas/<name>.md" in notice, "names where a thought to be found later goes"

    human = kyo("doctor")
    assert "[ideas_scratchpad]" in human.out + human.err


def test_doctor_finds_one_at_any_depth_and_calls_an_untouched_scaffold_empty(
    kyo, project: Path
) -> None:
    nested = project / "ideas" / "sub" / "scratchpad.md"
    nested.parent.mkdir()
    nested.write_text(scaffold.RETIRED_IDEAS_SCRATCHPAD_TEMPLATE, encoding="utf-8")

    result = kyo("doctor", "--json")
    (notice,) = [n for n in result.json["notices"] if n.startswith("[ideas_scratchpad] ")]
    assert str(nested) in notice
    assert "nothing to move" in notice


def test_the_notice_is_silent_without_the_file_and_clears_when_it_is_deleted(
    kyo, project: Path
) -> None:
    result = kyo("doctor", "--json")
    assert result.json["checks"]["ideas_scratchpad"]["ok"]
    assert not any(n.startswith("[ideas_scratchpad]") for n in result.json["notices"])

    leftover = project / "ideas" / "scratchpad.md"
    leftover.write_text("a thought\n", encoding="utf-8")
    assert not kyo("doctor", "--json").json["checks"]["ideas_scratchpad"]["ok"]

    leftover.unlink()  # the remedy the notice names
    assert kyo("doctor", "--json").json["checks"]["ideas_scratchpad"]["ok"]
