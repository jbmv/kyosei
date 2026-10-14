"""KYO-412: the docs-governance parser must not answer "nothing to do" on a guess.

Each suppressed-obligation case (markdown regions, compact YAML sequences, ``governs:``
spellings) has its own test here, and each fails if its fix is taken back: a region finder
that cuts a section short, or a ``governs:`` spelling that parses to nothing, turns a real
obligation into a silent clean. One-line splitting and staleness by instant are the two
low-severity parser/verdict bugs beside them.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import docs_check, docs_parse, docs_regions, docs_staleness, docs_targets
from kyosei.docs_check import GOVERNS_FORMAT, check_docs
from kyosei.docs_parse import section_metadata
from kyosei.docs_regions import FileChange, symbol_region_changed
from kyosei.docs_staleness import governs_staleness

# --------------------------------------------------------------------- markdown regions

#: An Install section that runs from line 3 to line 10, and holds an
#: indented code block whose first line is a shell comment.
INSTALL_MD = (
    "# Project\n"  # 1
    "\n"  # 2
    "## Install\n"  # 3
    "Create a venv:\n"  # 4
    "\n"  # 5
    "    # create the venv\n"  # 6
    "    python -m venv .venv\n"  # 7
    "\n"  # 8
    "Then install.\n"  # 9
    "Done.\n"  # 10
    "## Usage\n"  # 11
    "Run it.\n"  # 12
)

#: A prose line starting `#1` inside a section.
PRIORITY_MD = (
    "## Goals\n"  # 1
    "Our aims:\n"  # 2
    "#1 priority is speed, #hashtag aside\n"  # 3
    "#hashtag\n"  # 4
    "The rest of the goals.\n"  # 5
    "## Other\n"  # 6
    "x\n"  # 7
)


def test_indented_comment_does_not_end_markdown_section() -> None:
    assert docs_regions._markdown_regions(INSTALL_MD, "Install") == ((3, 10),)


def test_indented_comment_change_later_in_section_is_raised() -> None:
    after = INSTALL_MD.replace("Then install.", "Then install with uv.")
    change = FileChange("README.md", INSTALL_MD, after, frozenset({9}), frozenset({9}))
    assert symbol_region_changed(change, "Install") is True


def test_hash_prose_line_does_not_end_markdown_section() -> None:
    assert docs_regions._markdown_regions(PRIORITY_MD, "Goals") == ((1, 5),)
    after = PRIORITY_MD.replace("The rest", "All the rest")
    change = FileChange("GOALS.md", PRIORITY_MD, after, frozenset({5}), frozenset({5}))
    assert symbol_region_changed(change, "Goals") is True


def test_markdown_regions_and_sections_share_one_heading_rule() -> None:
    """Every heading `_sections` splits on is one `_markdown_regions` starts a region at,
    and nothing else is: the two readers agree on where each section begins and ends."""
    # All headings at one level, so each region is exactly its section's own lines.
    text = INSTALL_MD.replace("# Project", "## Project") + PRIORITY_MD.replace(
        "## Goals", "## Goals list"
    )
    lines = text.splitlines()
    sections = [s for s in docs_parse._sections(text) if s.heading != docs_parse.PREAMBLE]
    starts: list[int] = []
    for section in sections:
        regions = docs_regions._markdown_regions(text, section.heading)
        assert regions, section.heading
        start, end = regions[0]
        assert docs_parse.HEADING_RE.match(lines[start - 1])
        starts.append(start)
        # The region spans exactly the heading plus the section's body as `_sections` read it.
        assert end - start == len(section.body), section.heading
    heading_lines = [
        n for n, line in enumerate(lines, start=1) if docs_parse.HEADING_RE.match(line)
    ]
    assert starts == heading_lines


# ---------------------------------------------------------------- compact YAML sequences

WORKFLOW_YML = "name: ci\nsteps:\n- run: make build\n- run: make test\nother: 1\n"


def test_compact_sequence_extends_yaml_key_region() -> None:
    assert docs_regions._yaml_regions(WORKFLOW_YML, "steps") == ((2, 4),)


def test_compact_sequence_change_is_inside_region() -> None:
    after = WORKFLOW_YML.replace("make test", "make check")
    change = FileChange("ci.yml", WORKFLOW_YML, after, frozenset({4}), frozenset({4}))
    assert symbol_region_changed(change, "steps") is True


def test_compact_sequence_items_with_continuations_and_nested_key() -> None:
    text = (
        "jobs:\n"  # 1
        "  build:\n"  # 2
        "    steps:\n"  # 3
        "    - name: build\n"  # 4
        "      run: make\n"  # 5
        "    - run: make test\n"  # 6
        "    env: {}\n"  # 7
    )
    assert docs_regions._yaml_regions(text, "steps") == ((3, 6),)


def test_key_with_value_does_not_absorb_following_keys() -> None:
    assert docs_regions._yaml_regions("a: 1\nb: 2\n", "a") == ((1, 1),)


# ----------------------------------------------------------------- governs: spellings


def test_one_line_html_comment_governs() -> None:
    assert section_metadata(["<!-- governs: src/gone.py -->"])["governs"] == ["src/gone.py"]


def test_one_line_html_comment_missing_file_is_reported(kyo, project: Path) -> None:
    doc = project / "docs" / "PRD.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("## Gone\n\n<!-- governs: src/gone.py -->\n\nProse.\n", encoding="utf-8")
    result = kyo("docs", "check", "--json")
    assert result.code == 3, result
    assert [p["target"] for p in result.json["problems"]] == ["src/gone.py"]


def test_star_bullets_govern() -> None:
    assert section_metadata(["governs:", "  * src/x.py"])["governs"] == ["src/x.py"]


def test_plus_bullets_govern() -> None:
    assert section_metadata(["governs:", "  + src/x.py", "  - src/y.py"])["governs"] == [
        "src/x.py",
        "src/y.py",
    ]


def test_blank_line_before_first_item_is_skipped() -> None:
    assert section_metadata(["governs:", "", "  - src/x.py"])["governs"] == ["src/x.py"]


def test_comment_terminator_still_ends_list() -> None:
    body = ["<!--", "governs:", "  * a.py", "  - b.py", "-->", "- c.py", "* d.py"]
    assert section_metadata(body)["governs"] == ["a.py", "b.py"]


def test_blank_line_after_an_item_still_ends_list() -> None:
    assert section_metadata(["governs:", "  - a.py", "", "- prose bullet"])["governs"] == ["a.py"]


def _project_with(tmp_path: Path, body: str) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "d.md").write_text(f"## Section\n\n{body}\n\nProse.\n", "utf-8")
    return tmp_path


def test_governs_naming_nothing_is_a_problem(tmp_path: Path) -> None:
    root = _project_with(tmp_path, "<!--\ngoverns:\n-->")
    result = check_docs(root)
    assert result.state == docs_check.DOCS_PROBLEMS
    assert [(p.section, p.target) for p in result.problems] == [("Section", "governs:")]
    assert GOVERNS_FORMAT in result.says


def test_governs_naming_nothing_cleared_by_real_target(tmp_path: Path) -> None:
    root = _project_with(tmp_path, "<!--\ngoverns:\n  - src/real.py\n-->")
    result = check_docs(root)
    assert result.state == docs_check.DOCS_CLEAN, result.says
    assert result.problems == ()


def test_one_line_governs_with_only_commas_is_a_problem(tmp_path: Path) -> None:
    root = _project_with(tmp_path, "governs: ,")
    assert [p.target for p in check_docs(root).problems] == ["governs:"]


# ----------------------------------------------------------------- one-line splitting


def test_brace_group_on_one_line_is_one_target() -> None:
    assert section_metadata(["governs: src/{a,b}/n.py"])["governs"] == ["src/{a,b}/n.py"]
    assert section_metadata(["governs: x.py, src/{a,b}/n.py, y.py"])["governs"] == [
        "x.py",
        "src/{a,b}/n.py",
        "y.py",
    ]


def test_brace_group_on_one_line_expands_like_list_form(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / "src" / name).mkdir(parents=True)
        (tmp_path / "src" / name / "n.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    one_line = "## One\n\ngoverns: src/{a,b}/n.py\n"
    listed = "## Listed\n\ngoverns:\n  - src/{a,b}/n.py\n"
    (tmp_path / "docs" / "d.md").write_text(one_line + "\n" + listed, encoding="utf-8")
    result = check_docs(tmp_path)
    assert result.state == docs_check.DOCS_CLEAN, result.says
    sections = {
        s.heading: s
        for s in docs_parse._sections(
            (tmp_path / "docs" / "d.md").read_text(),
            docs_targets.target_resolver(tmp_path, docs_parse.DEFAULT),
        )
    }
    assert sections["One"].governed_paths == sections["Listed"].governed_paths
    assert set(sections["One"].governed_paths) == {"src/a/n.py", "src/b/n.py"}


def test_trailing_comment_terminator_is_not_part_of_target() -> None:
    assert section_metadata(["governs: x.py -->"])["governs"] == ["x.py"]


def test_governs_format_names_accepted_spellings() -> None:
    assert "<!-- governs: x.py -->" in GOVERNS_FORMAT
    assert "commas inside a `{a,b,c}` group do not split it" in GOVERNS_FORMAT
    assert "`*` and `+`" in GOVERNS_FORMAT
    assert "blank lines allowed before the first" in GOVERNS_FORMAT
    assert "names no target is a problem" in GOVERNS_FORMAT


# ----------------------------------------------------------------- staleness by instant


def test_staleness_compares_instants_not_strings(tmp_path: Path) -> None:
    dates = {
        "docs/d.md": "2026-09-25T18:00:00+00:00",
        # 19:00Z -- an hour after the doc, but sorts earlier as a string.
        "src/code.py": "2026-09-25T12:00:00-07:00",
    }
    assert dates["src/code.py"] < dates["docs/d.md"]
    result = governs_staleness(
        tmp_path, "docs/d.md", ["src/code.py"], commit_at=lambda _root, path: dates[path]
    )
    assert result.state == docs_staleness.STALENESS_STALE
    assert result.changed == ("src/code.py",)


def test_staleness_current_when_code_earlier_in_absolute_time(tmp_path: Path) -> None:
    dates = {
        "docs/d.md": "2026-09-25T12:00:00-07:00",  # 19:00Z
        "src/code.py": "2026-09-25T18:30:00+00:00",  # 18:30Z, sorts later as a string
    }
    result = governs_staleness(
        tmp_path, "docs/d.md", ["src/code.py"], commit_at=lambda _root, path: dates[path]
    )
    assert result.state == docs_staleness.STALENESS_CURRENT
