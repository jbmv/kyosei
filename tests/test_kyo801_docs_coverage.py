"""KYO-801: code that no document section governs is reported, and a project chooses how
loudly (proposal KYO-797, section 3).

A ``governs:`` line under a heading names the code a section describes; a branch that
changes that code raises an obligation to read the section. Code that no section names
raises nothing. ``kyo docs coverage`` measures that, the ``docs_coverage:`` key in
``kyosei/config.yml`` records the project's choice (``off``, ``notice``, ``problem``), and
`kyo doctor` reports the finding in the tier the choice names.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from kyosei import docs_coverage
from kyosei import doctor as doctor_module
from kyosei.config import CONFIG_KEY_EXAMPLES, render_config
from kyosei.doctor import NOTICE_CHECKS, TIER_NOTICE, tier_of
from kyosei.errors import EXIT_DOCTOR, KyoseiError
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

DOC = """# Notes

## Governed section

governs: src/a.py

What a.py does.

## Section governing nothing

Prose with no target.
"""

POINTER_DOCUMENTS_YML = """\
documents:
  design:
    summary: The design record.
    globs:
      - docs/design/**/*.md
    section_status:
      statuses: [current, amended, superseded]
"""

POINTER_DOC = """# A

## KYO-1

Why alpha returns what it returns.
"""

POINTER_MODULE = (
    '"""A module."""\n\n\ndef alpha():\n    """docs/design/a.md#kyo-1 says why."""\n    return 1\n'
)


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout


def _put(project: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "files")


def _config(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def _declare(project: Path, block: str) -> None:
    """Replace any ``docs_coverage`` the config carries with ``block`` (YAML text)."""
    path = _config(project)
    head = path.read_text(encoding="utf-8").split("\ndocs_coverage:", 1)[0]
    path.write_text(head.rstrip("\n") + "\n" + block, encoding="utf-8")


def _basic(project: Path) -> None:
    _put(
        project,
        {
            "docs/notes.md": DOC,
            "src/a.py": "A = 1\n",
            "src/b.py": "B = 1\n",
            "src/sub/c.ts": "export const c = 1\n",
            "tests/test_x.py": "def test_x():\n    pass\n",
            "package-lock.json": "{}\n",
        },
    )


def _findings(kyo, name: str) -> list[str]:
    result = kyo("doctor", "--json")
    assert result.code in (0, EXIT_DOCTOR), result
    return result.json["checks"][name]["failures"]


# ------------------------------------------------------------------- c1: the command


def test_the_command_lists_what_no_section_governs_and_opens_no_database(
    kyo, git_project: Path
) -> None:
    """c1. Ungoverned files by directory, the sections governing nothing, `--json`; and
    nothing stored, with the database gone."""
    _basic(git_project)
    (git_project / "kyosei" / "kyosei.db").unlink()
    before = _config(git_project).read_text(encoding="utf-8")

    result = kyo("docs", "coverage", "--json")
    assert result.code == 0, result
    data = result.json
    assert data["wrote_to_store"] is False
    assert data["code_files"] == 3 and data["governed"] == 1
    assert data["ungoverned"] == [
        {"directory": "src", "files": ["b.py"]},
        {"directory": "src/sub", "files": ["c.ts"]},
    ]
    # Tests, lock files and documents are not code under the default rule.
    listed = {name for row in data["ungoverned"] for name in row["files"]}
    assert "test_x.py" not in listed and "package-lock.json" not in listed
    (notes,) = [d for d in data["documents"] if d["doc_path"] == "docs/notes.md"]
    assert notes["governing"] == 1 and notes["sections"] == 3
    assert notes["governing_nothing"] == ["Notes", "Section governing nothing"]

    text = kyo("docs", "coverage")
    assert text.code == 0, text
    assert "2 of 3 code files" in text.out
    assert "src/sub/ (1): c.ts" in text.out
    assert "docs/notes.md: 1 of 3 sections govern code" in text.out
    assert "nothing was written" in text.out
    assert _config(git_project).read_text(encoding="utf-8") == before


def test_a_file_reached_only_by_a_pointer_comment_is_listed_apart(
    kyo, git_project: Path
) -> None:
    """A pointer raises for the region around the comment, so it is not governance."""
    _put(
        git_project,
        {
            "kyosei/documents.yml": POINTER_DOCUMENTS_YML,
            "docs/design/a.md": POINTER_DOC,
            "src/pkg/mod.py": POINTER_MODULE,
            "src/pkg/other.py": "X = 1\n",
        },
    )
    data = kyo("docs", "coverage", "--json").json
    assert data["pointer_only"] == ["src/pkg/mod.py"]
    # Not counted as governed.
    assert data["ungoverned_count"] == 2 and data["governed"] == 0
    assert "reached only by a `src/` pointer" in kyo("docs", "coverage").out


def test_a_target_reaching_more_than_twenty_files_is_named_as_broad(
    kyo, git_project: Path
) -> None:
    files = {f"src/m{i:02d}.py": "X = 1\n" for i in range(21)}
    files["docs/wide.md"] = "# W\n\n## All of it\n\ngoverns: src/*.py\n\nEverything.\n"
    _put(git_project, files)
    data = kyo("docs", "coverage", "--json").json
    assert data["ungoverned_count"] == 0
    assert data["broad_targets"] == [
        {"doc_path": "docs/wide.md", "section": "All of it", "target": "src/*.py", "files": 21}
    ]
    assert "src/*.py: 21 files" in kyo("docs", "coverage").out


def test_a_project_with_no_documents_is_told_nothing(kyo, git_project: Path) -> None:
    _put(git_project, {"src/a.py": "A = 1\n"})
    data = kyo("docs", "coverage", "--json").json
    assert data["has_documents"] is False and data["ungoverned"] == []
    assert "nothing could govern" in kyo("docs", "coverage").out
    result = kyo("doctor", "--json")
    assert result.json["checks"]["docs_coverage"]["failures"] == []
    assert result.json["checks"]["docs_coverage_required"]["failures"] == []


def test_a_declared_code_list_replaces_the_default_rule(kyo, git_project: Path) -> None:
    _basic(git_project)
    _declare(git_project, "docs_coverage:\n  code:\n    - src/**/*.ts\n    - package-lock.json\n")
    data = kyo("docs", "coverage", "--json").json
    assert data["code_declared"] is True
    assert data["code_files"] == 2
    assert [row["files"] for row in data["ungoverned"]] == [["package-lock.json"], ["c.ts"]]


def test_unmapped_files_are_not_counted(kyo, git_project: Path) -> None:
    _basic(git_project)
    _declare(
        git_project,
        "docs_coverage:\n  unmapped:\n    - glob: src/sub/**\n      why: a build artifact\n",
    )
    data = kyo("docs", "coverage", "--json").json
    assert data["excluded"] == ["src/sub/c.ts"]
    assert data["ungoverned"] == [{"directory": "src", "files": ["b.py"]}]


# ------------------------------------------------------------ c2: the declaration


def test_the_key_is_registered_optional_with_its_readers() -> None:
    """Law 2: `kyo doctor`'s config_consumers check resolves these against the source."""
    assert CONFIG_CONSUMERS["docs_coverage"] == (
        "cmd_views._cmd_docs_coverage",
        "doctor_registry.check_docs_coverage",
        "doctor_registry.check_docs_coverage_required",
    )
    assert "docs_coverage" in OPTIONAL_CONFIG_KEYS
    assert "docs_coverage" in CONFIG_KEY_EXAMPLES


def test_the_declaration_parses_into_its_parts(kyo, git_project: Path) -> None:
    _declare(
        git_project,
        "docs_coverage:\n  level: problem\n  code:\n    - src/**/*.py\n  unmapped:\n"
        "    - glob: src/**/__init__.py\n      why: no behaviour of its own\n",
    )
    assert docs_coverage.read_config(git_project, git_project) == docs_coverage.CoverageConfig(
        level="problem",
        code=("src/**/*.py",),
        unmapped=(docs_coverage.Unmapped("src/**/__init__.py", "no behaviour of its own"),),
    )
    # The key is one every command accepts: the config still loads for a hook.
    assert kyo("brief").code == 0
    assert kyo("doctor", "--json").json["checks"]["config_consumers"]["failures"] == []


def test_absent_declares_nothing_and_acts_as_the_notice(git_project: Path) -> None:
    config = docs_coverage.read_config(git_project, git_project)
    assert not config.declared and docs_coverage.effective_level(config) == "notice"


@pytest.mark.parametrize(
    ("block", "named"),
    [
        ("docs_coverage: yes\n", "must be a mapping"),
        ("docs_coverage:\n  lvl: off\n", "lvl"),
        ("docs_coverage:\n  level: loud\n", "level must be one of off, notice, problem"),
        ("docs_coverage:\n  level: true\n", "level must be one of"),
        ("docs_coverage:\n  code: src/**\n", "code must be a non-empty list"),
        ("docs_coverage:\n  code: []\n", "code must be a non-empty list"),
        ("docs_coverage:\n  code: [3]\n", "non-empty strings"),
        ("docs_coverage:\n  unmapped: [src/**]\n", "unmapped entry 1 must be a mapping"),
        (
            "docs_coverage:\n  unmapped:\n    - glob: src/**\n",
            "unmapped entry 1 (src/**) needs a one-line why",
        ),
        (
            "docs_coverage:\n  unmapped:\n    - glob: src/**\n      why: a\n      note: b\n",
            "unmapped entry 1 carries key(s) it does not read: note",
        ),
        (
            "docs_coverage:\n  unmapped:\n    - glob: src/**\n      why: \"a\\nb\"\n",
            "one-line why",
        ),
    ],
)
def test_anything_else_is_refused_by_name_and_the_remedy_clears_it(
    kyo, git_project: Path, block: str, named: str
) -> None:
    """c2. The refusal names the key or value; deleting the key (its other remedy) works,
    and so does the shape it prints."""
    _declare(git_project, block)
    with pytest.raises(KyoseiError) as refusal:
        docs_coverage.read_config(git_project, git_project)
    message = str(refusal.value)
    assert named in message and "hand edit" in message
    refused = kyo("docs", "coverage")
    assert refused.code != 0 and named in refused.err
    # It names the problem, and nothing else is blinded: the config still loads.
    assert kyo("brief").code == 0
    names = kyo("doctor", "--json").json["checks"]
    assert any("docs_coverage" in f for f in names["docs_coverage_required"]["failures"])
    assert names["docs_coverage"]["failures"] == []

    shape = message.split("write it as\n", 1)[1].rsplit("\nin ", 1)[0]
    _declare(git_project, shape + "\n")
    docs_coverage.read_config(git_project, git_project)
    _declare(git_project, "")
    assert kyo("docs", "coverage").code == 0


def test_problem_without_a_code_list_is_refused_and_the_printed_draft_parses(
    kyo, git_project: Path
) -> None:
    """c2. The refusal prints a draft `code:` derived from the tracked files, and writing
    it under the key is what clears the refusal."""
    _basic(git_project)
    _declare(git_project, "docs_coverage:\n  level: problem\n")
    with pytest.raises(KyoseiError) as refusal:
        docs_coverage.read_config(git_project, git_project)
    message = str(refusal.value)
    assert "level: problem with no code: list" in message
    draft = message.split("keep level: problem:\n", 1)[1].split("\nor set level", 1)[0]
    assert "src/**/*.py" in draft and "src/**/*.ts" in draft
    assert "tests/" not in draft and "package-lock" not in draft

    _declare(git_project, "docs_coverage:\n  level: problem\n" + draft + "\n")
    config = docs_coverage.read_config(git_project, git_project)
    assert config.code == ("src/**/*.py", "src/**/*.ts") and config.level == "problem"


# -------------------------------------------------------------------- c3: the doctor


def test_the_two_checks_are_registered_in_their_tiers() -> None:
    assert "docs_coverage" in NOTICE_CHECKS and "docs_coverage_required" not in NOTICE_CHECKS
    assert tier_of("docs_coverage") == TIER_NOTICE
    assert tier_of("docs_coverage_required") != TIER_NOTICE
    assert "docs_coverage" in doctor_module.CHECKS
    assert "docs_coverage_required" in doctor_module.CHECKS


def test_by_default_it_is_a_notice_that_does_not_fail_the_run(
    kyo, git_project: Path
) -> None:
    _basic(git_project)
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    (finding,) = result.json["checks"]["docs_coverage"]["failures"]
    assert "2 of 3 source-suffix code files are governed by no document section" in finding
    assert "kyo docs coverage" in finding and "--level notice|problem|off" in finding
    assert len(finding) < doctor_module.MAX_FINDING_CHARS
    assert result.json["checks"]["docs_coverage_required"]["failures"] == []
    assert "[docs_coverage]" in kyo("doctor").out


def test_a_long_directory_name_keeps_the_finding_under_the_limit(
    kyo, git_project: Path
) -> None:
    deep = "a" * 60
    files = {f"{deep}/{deep}/x{i}.py": "X = 1\n" for i in range(4)}
    files["docs/notes.md"] = DOC
    _put(git_project, files)
    (finding,) = _findings(kyo, "docs_coverage")
    assert len(finding) < doctor_module.MAX_FINDING_CHARS, len(finding)


def test_at_problem_it_is_a_problem_and_never_both(kyo, git_project: Path) -> None:
    _basic(git_project)
    _declare(git_project, "docs_coverage:\n  level: problem\n  code:\n    - src/**/*.py\n")
    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    (finding,) = result.json["checks"]["docs_coverage_required"]["failures"]
    assert "1 of 2 declared code files are governed by no document section" in finding
    assert "`unmapped:`" in finding
    assert result.json["checks"]["docs_coverage"]["failures"] == []

    # The remedy it names: a `governs:` target, or an `unmapped` entry with its reason.
    _declare(
        git_project,
        "docs_coverage:\n  level: problem\n  code:\n    - src/**/*.py\n  unmapped:\n"
        "    - glob: src/b.py\n      why: not yet mapped\n",
    )
    cleared = kyo("doctor", "--json")
    assert cleared.code == 0, cleared
    assert cleared.json["checks"]["docs_coverage_required"]["failures"] == []


@pytest.mark.parametrize("level", ["notice", "problem"])
def test_a_complete_map_reports_nothing(kyo, git_project: Path, level: str) -> None:
    _put(git_project, {"docs/notes.md": DOC, "src/a.py": "A = 1\n"})
    _declare(git_project, f"docs_coverage:\n  level: {level}\n  code:\n    - src/**/*.py\n")
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["docs_coverage"]["failures"] == []
    assert result.json["checks"]["docs_coverage_required"]["failures"] == []


def test_off_silences_both(kyo, git_project: Path) -> None:
    _basic(git_project)
    _declare(git_project, "docs_coverage:\n  level: off\n")
    checks = kyo("doctor", "--json").json["checks"]
    assert checks["docs_coverage"]["failures"] == []
    assert checks["docs_coverage_required"]["failures"] == []


def test_the_notice_names_the_command_that_ends_it_and_it_does(
    kyo, git_project: Path
) -> None:
    """The finding offers `--level notice|problem|off`; running it records the answer.
    Only `off` clears the notice; the answer is kept either way."""
    _basic(git_project)
    assert _findings(kyo, "docs_coverage")
    assert kyo("docs", "coverage", "--level", "off").code == 0
    assert _findings(kyo, "docs_coverage") == []


# ---------------------------------------------------------------- c4: stale exclusions


def test_an_unmapped_glob_matching_no_tracked_file_is_reported(
    kyo, git_project: Path
) -> None:
    _put(git_project, {"docs/notes.md": DOC, "src/a.py": "A = 1\n"})
    _declare(
        git_project,
        "docs_coverage:\n  unmapped:\n    - glob: gone/**\n      why: removed long ago\n",
    )
    data = kyo("docs", "coverage", "--json").json
    assert data["unmatched_unmapped"] == ["gone/**"]
    assert "matches no tracked file" in kyo("docs", "coverage").out
    (finding,) = _findings(kyo, "docs_coverage")
    assert "'gone/**' matches no tracked file" in finding

    # The remedy it names: remove the entry.
    _declare(git_project, "")
    assert _findings(kyo, "docs_coverage") == []


def test_an_unmapped_glob_that_matches_nothing_is_a_problem_at_that_level(
    kyo, git_project: Path
) -> None:
    _put(git_project, {"docs/notes.md": DOC, "src/a.py": "A = 1\n"})
    _declare(
        git_project,
        "docs_coverage:\n  level: problem\n  code:\n    - src/**/*.py\n  unmapped:\n"
        "    - glob: gone/**\n      why: removed long ago\n",
    )
    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR
    (finding,) = result.json["checks"]["docs_coverage_required"]["failures"]
    assert "'gone/**' matches no tracked file" in finding


# -------------------------------------------------------------------- c5: --level


def test_level_writes_the_key_and_echoes_what_it_stored(kyo, git_project: Path) -> None:
    before = _config(git_project).read_text(encoding="utf-8")
    result = kyo("docs", "coverage", "--level", "notice")
    assert result.code == 0, result
    assert "stored docs_coverage level: notice" in result.out
    assert "kyosei/config.yml" in result.out
    after = _config(git_project).read_text(encoding="utf-8")
    # Appended; every byte before it, comments included, is as it was.
    assert after.startswith(before.rstrip("\n"))
    stored = yaml.safe_load(after)
    assert stored["docs_coverage"] == {"level": "notice"}
    assert stored["schema_version"] == yaml.safe_load(before)["schema_version"]


def test_a_bare_off_is_read_as_off_and_written_quoted(kyo, git_project: Path) -> None:
    """YAML reads an unquoted `off` as false; the declaration means the level."""
    _declare(git_project, "docs_coverage:\n  level: off\n")
    assert docs_coverage.read_config(git_project, git_project).level == "off"
    assert kyo("docs", "coverage", "--level", "off").code == 0
    assert "level: 'off'" in _config(git_project).read_text(encoding="utf-8")
    assert docs_coverage.read_config(git_project, git_project).level == "off"
    stored = kyo("docs", "coverage", "--level", "notice", "--json")
    assert stored.json["level"] == "notice" and stored.json["wrote_to_store"] is True


def test_level_changes_one_line_and_keeps_the_rest_of_the_declaration(
    kyo, git_project: Path
) -> None:
    block = (
        "docs_coverage:\n  level: notice  # not yet\n  code:\n    - src/**/*.py\n"
        "  unmapped:\n    - glob: src/**/__init__.py\n      why: nothing in them\n"
    )
    _declare(git_project, block)
    result = kyo("docs", "coverage", "--level", "problem")
    assert result.code == 0, result
    text = _config(git_project).read_text(encoding="utf-8")
    assert "  level: problem\n" in text and "not yet" not in text
    assert text.split("\ndocs_coverage:\n", 1)[1] == block.split("docs_coverage:\n", 1)[1].replace(
        "  level: notice  # not yet\n", "  level: problem\n"
    )
    assert "unmapped: 1 entry" in result.out and "code: declared" in result.out
    # The config still loads and reads back what was stored.
    assert docs_coverage.read_config(git_project, git_project).level == "problem"


def test_level_problem_without_a_code_list_is_refused_with_a_draft_and_writes_nothing(
    kyo, git_project: Path
) -> None:
    """c5 with c2: the refusal's draft, written under the key, lets the command succeed."""
    _basic(git_project)
    before = _config(git_project).read_text(encoding="utf-8")
    refused = kyo("docs", "coverage", "--level", "problem")
    assert refused.code != 0, refused
    assert "level: problem with no code: list" in refused.err
    assert _config(git_project).read_text(encoding="utf-8") == before

    draft = refused.err.split("keep level: problem:\n", 1)[1].split("\nor set level", 1)[0]
    _declare(git_project, "docs_coverage:\n" + draft + "\n")
    assert kyo("docs", "coverage", "--level", "problem").code == 0
    config = docs_coverage.read_config(git_project, git_project)
    assert config.level == "problem" and config.code


def test_level_refuses_a_value_outside_the_three(kyo, git_project: Path) -> None:
    refused = kyo("docs", "coverage", "--level", "loud")
    assert refused.code != 0 and "loud" in refused.err


def test_the_declaration_survives_the_rebuild_that_rewrites_the_config(
    kyo, git_project: Path
) -> None:
    assert kyo("docs", "coverage", "--level", "off").code == 0
    assert kyo("rebuild", "--force").code == 0
    assert docs_coverage.read_config(git_project, git_project).level == "off"


def test_render_config_carries_the_key_and_offers_it_when_absent() -> None:
    values = {
        "schema_version": 1,
        "project_prefix": "TST",
        "export_dir": "export",
        "docs_coverage": {"level": "off"},
    }
    assert "# docs_coverage: read by" in render_config(values)
    absent = {k: v for k, v in values.items() if k != "docs_coverage"}
    offered = render_config(absent)
    assert "# docs_coverage:\n#   level: notice" in offered
    assert "older than this key refuses it" in offered


def test_the_glob_dialect_is_documents_ymls() -> None:
    """`**` crosses directories and `*` does not, as in `documents.yml`."""
    assert docs_coverage._matches(["src/**/*.py"], "src/a.py")
    assert docs_coverage._matches(["src/**/*.py"], "src/x/y/a.py")
    assert not docs_coverage._matches(["src/*.py"], "src/x/a.py")


def test_level_is_refused_from_a_linked_worktree_that_reaches_the_store_through_the_main(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The write goes to the main checkout's config, outside the branch's diff, so it is
    refused there as every store writer is (KYO-342); reading is not."""
    from .conftest import invoke

    tree = tmp_path / "linked"
    _git(git_project, "worktree", "add", "-q", "-b", "kyo-801-branch", str(tree))
    before = _config(git_project).read_text(encoding="utf-8")
    refused = invoke(["--root", str(tree), "docs", "coverage", "--level", "off"])
    assert refused.code != 0 and str(git_project) in refused.err, refused
    assert _config(git_project).read_text(encoding="utf-8") == before
    assert (tree / "kyosei" / "config.yml").read_text(encoding="utf-8") == before
    assert invoke(["--root", str(tree), "docs", "coverage"]).code == 0
