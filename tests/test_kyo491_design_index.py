"""KYO-502 (KYO-491's follow-up 4): ``kyo docs index`` and the committed section index.

A document set declaring ``section_status.index`` in documents.yml gets a generated
file at that path; ``--check`` writes nothing and exits 1 when the committed copy
differs. The shape is KYO-138's
(``tests/test_kyo138_command_reference.py``): one test holds this repository's committed
file to what the command renders now, a canary proves that comparison can fail, and a
remedy test proves running the command clears the failure.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from kyosei import docs_index, docs_parse
from kyosei import registry as registry_module

REPO_ROOT = Path(__file__).resolve().parents[1]

#: This repository's committed index. The design set's `section_status:` block in
#: kyosei/documents.yml names it with `index: docs/reference/design-sections.md`; that
#: file is the controller's to edit (GUARDED_BRANCH_DIRS), so until the line lands the
#: test supplies the same path itself and renders exactly what the command would.
INDEX_PATH = "docs/reference/design-sections.md"

_DOCUMENTS_YML = """\
documents:
  design:
    summary: The design record.
    globs:
      - docs/design/**/*.md
    section_status:
      statuses: [current, amended, superseded]
      index: docs/reference/design-sections.md
"""

_A = """\
# A

## KYO-1

status: amended
amended-by: KYO-2

Why the first thing (`one`).

## KYO-2

status: amended
amended-by: KYO-3

Why KYO-2 changed KYO-1.

## KYO-3

Why KYO-3 changed KYO-2.

## KYO-4

Why nobody points here.
"""

_B = """\
# B

## KYO-9

Why nine.
"""

_SRC = """\
# docs/design/a.md#kyo-1 and docs/design/a.md#kyo-3
# docs/design/b.md#kyo-9
x = 1  # docs/design/a.md#kyo-3
"""


def _repo_registry_dir(tmp_path: Path) -> Path:
    """Where this repository's ``documents.yml`` is read from.

    The maintainer's checkout carries the store, whose ``kyosei/documents.yml`` declares
    the design set. A public checkout has no store, so there the same declaration of the
    design set (:data:`_DOCUMENTS_YML`) is written to ``tmp_path`` and read from there:
    the committed index is still held to what the published design documents yield.
    """
    live = REPO_ROOT / "kyosei"
    if (live / "documents.yml").is_file():
        return live
    (tmp_path / "documents.yml").write_text(_DOCUMENTS_YML, encoding="utf-8")
    return tmp_path


def _repo_sets(tmp_path: Path) -> list[docs_parse.SectionStatusSet]:
    sets = list(docs_parse.document_set(_repo_registry_dir(tmp_path)).section_status)
    design = [chosen for chosen in sets if chosen.name == "design"]
    assert design, "kyosei/documents.yml no longer declares section_status: on design"
    return [
        chosen if chosen.index else dataclasses.replace(chosen, index=INDEX_PATH)
        for chosen in design
    ]


def _fixture(root: Path, *, documents_yml: str = _DOCUMENTS_YML) -> None:
    for rel, text in {
        "kyosei/documents.yml": documents_yml,
        "docs/design/a.md": _A,
        "docs/design/b.md": _B,
        "src/pkg/mod.py": _SRC,
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _render(root: Path) -> docs_index.IndexFile:
    [item] = docs_index.indexes(root, docs_parse.document_set(root / "kyosei").section_status)
    return item


# ------------------------------------------------- c2: the committed file is held to it


def test_the_committed_index_is_what_kyo_docs_index_renders_right_now(tmp_path: Path) -> None:
    [item] = docs_index.indexes(REPO_ROOT, _repo_sets(tmp_path))
    assert item.path == INDEX_PATH
    committed = (REPO_ROOT / INDEX_PATH).read_text(encoding="utf-8")
    assert committed == item.text, (
        f"{INDEX_PATH} is stale: a design section's status or links, or a src/ pointer "
        f"into one, changed since it was generated. Run `{docs_index.INDEX_COMMAND}` and "
        "commit the file; do not edit it by hand"
    )


def test_a_declared_index_in_this_repository_is_the_committed_path(tmp_path: Path) -> None:
    """Once the controller adds `index:` to documents.yml, the test above stops supplying
    the path; this keeps the two from naming different files."""
    for chosen in docs_parse.document_set(_repo_registry_dir(tmp_path)).section_status:
        if chosen.index:
            assert chosen.index == INDEX_PATH


def test_the_staleness_comparison_can_fail(tmp_path: Path) -> None:
    """The canary: a fixture section's status changes and the comparison says so."""
    _fixture(tmp_path)
    item = _render(tmp_path)
    (tmp_path / item.path).parent.mkdir(parents=True)
    (tmp_path / item.path).write_text(item.text, encoding="utf-8")
    assert docs_index.stale(tmp_path, [item]) == []

    (tmp_path / "docs/design/b.md").write_text(
        _B.replace(
            "## KYO-9\n", "## KYO-9\n\nstatus: amended\namended-by: docs/design/a.md#kyo-4\n"
        ),
        encoding="utf-8",
    )
    again = _render(tmp_path)
    assert again.text != item.text
    assert [stale.path for stale in docs_index.stale(tmp_path, [again])] == [item.path]


# ------------------------------------------------------- c1: the command and --check


def test_kyo_docs_index_writes_the_file_and_check_passes(kyo, project: Path) -> None:
    _fixture(project)
    result = kyo("docs", "index", "--json")
    assert result.code == 0, result
    assert result.json["stale"] == [INDEX_PATH]
    assert (project / INDEX_PATH).read_text(encoding="utf-8") == _render(project).text
    checked = kyo("docs", "index", "--check")
    assert checked.code == 0, checked
    assert f"{INDEX_PATH} is current" in checked.out


def test_check_writes_nothing_exits_1_naming_the_command_and_the_remedy_clears_it(
    kyo, project: Path
) -> None:
    _fixture(project)
    assert kyo("docs", "index").code == 0
    (project / "docs/design/a.md").write_text(
        _A.replace("## KYO-4\n", "## KYO-4\n\nstatus: superseded\nsuperseded-by: KYO-3\n"),
        encoding="utf-8",
    )
    before = (project / INDEX_PATH).read_text(encoding="utf-8")

    refused = kyo("docs", "index", "--check")
    assert refused.code == 1, refused
    assert "`kyo docs index`" in refused.err + refused.out
    assert INDEX_PATH in refused.err + refused.out
    assert (project / INDEX_PATH).read_text(encoding="utf-8") == before

    assert kyo("docs", "index").code == 0
    cleared = kyo("docs", "index", "--check")
    assert cleared.code == 0, cleared


def test_a_missing_index_file_is_stale_too(kyo, project: Path) -> None:
    _fixture(project)
    refused = kyo("docs", "index", "--check")
    assert refused.code == 1, refused
    assert not (project / INDEX_PATH).exists()


def test_a_project_declaring_no_index_is_told_so_and_exits_0(kyo, project: Path) -> None:
    _fixture(project, documents_yml=_DOCUMENTS_YML.replace(f"      index: {INDEX_PATH}\n", ""))
    for args in (("docs", "index"), ("docs", "index", "--check")):
        result = kyo(*args)
        assert result.code == 0, result
        assert "no document set declares `section_status.index`" in result.out
    assert not (project / INDEX_PATH).exists()


# ---------------------------------------------------------- what the index contains


def test_the_index_counts_lists_follows_chains_and_names_unpointed_sections(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    text = _render(tmp_path).text
    assert "generated by `kyo docs index`" in text.lower() and "do not edit" in text
    # Summary: a.md has five headed sections (title + four), two amended, three pointers.
    assert "| [docs/design/a.md](../design/a.md) | 5 | 3 | 2 | 0 | 3 |" in text
    assert "| [docs/design/b.md](../design/b.md) | 2 | 2 | 0 | 0 | 1 |" in text
    # Non-current rows: KYO-1's chain runs through KYO-2 to KYO-3, its current head.
    row = next(line for line in text.splitlines() if line.startswith("| [KYO-1]"))
    cells = [cell.strip() for cell in row.strip("|").split(" | ")]
    assert cells[0] == "[KYO-1](../design/a.md#kyo-1)"
    assert cells[1] == "amended"
    assert cells[2] == "[KYO-2](../design/a.md#kyo-2)"
    assert cells[3] == "[KYO-3](../design/a.md#kyo-3)"
    assert cells[4] == "Why the first thing (`one`)."
    # Current sections are counted, not listed.
    assert not any(line.startswith("| [KYO-3]") for line in text.splitlines())
    # Unpointed: KYO-2 and KYO-4 have no pointer; the document titles are not listed.
    unpointed = text.split("### Sections no code points at", 1)[1]
    assert "[docs/design/a.md KYO-2](../design/a.md#kyo-2)" in unpointed
    assert "[docs/design/a.md KYO-4](../design/a.md#kyo-4)" in unpointed
    assert "KYO-1]" not in unpointed and "KYO-9" not in unpointed
    assert "a.md A]" not in unpointed


# ------------------------------------------- KYO-512: an index listing every section

_EVERY_YML = _DOCUMENTS_YML.replace(
    f"      index: {INDEX_PATH}\n", f"      index: {INDEX_PATH}\n      index_lists: every\n"
)

_NOTES_YML = """\
documents:
  notes:
    summary: Numbered notes.
    globs:
      - notes/NOTES.md
    section_status:
      statuses: [current, amended, superseded, historical]
      index: notes/NOTES-SECTIONS.md
      index_lists: every
      index_depth: 2
    numbered:
      fragments: notes/fragments
      heading: "## {number}."
"""

_NOTES = """\
# Notes

## 1. The first finding

<!--notes-meta
status: amended
amended_by: [2]
governs:
  - pkg/one.py:fetch
  - pkg/two.py
-->

The first finding's prose.

### A sub-heading inside section 1

More prose.

## 2. The second finding

<!--notes-meta
status: amended
amended_by: [3]
-->

Narrows §1.

## 3. The third finding

status: current

Narrows §2.

## 4. A historical one

status: historical

Kept as history.
"""


def _rows(text: str, after: str) -> list[list[str]]:
    """The data rows of the first table under the line ``after``."""
    tail = text.split(after, 1)[1].splitlines()
    rows: list[list[str]] = []
    started = False
    for line in tail:
        if line.startswith("|"):
            started = True
            if not line.startswith("|---"):
                rows.append([cell.strip() for cell in line.strip().strip("|").split(" | ")])
        elif started:
            break
    return rows[1:]  # drop the header row


def _notes_fixture(root: Path, notes: str = _NOTES, documents_yml: str = _NOTES_YML) -> None:
    for rel, text in {"kyosei/documents.yml": documents_yml, "notes/NOTES.md": notes}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_kyo512_c1_one_fixture_set_rendered_both_ways(tmp_path: Path) -> None:
    """c1: the same documents, default and `index_lists: every`. The default lists the
    two amended sections only; `every` lists all seven headed sections, one row each,
    with the head of each amended section's chain."""
    _fixture(tmp_path)
    default = _render(tmp_path).text
    assert [row[0] for row in _rows(default, "### [docs/design/a.md]")] == [
        "[KYO-1](../design/a.md#kyo-1)",
        "[KYO-2](../design/a.md#kyo-2)",
    ]
    assert "Every section" not in default

    _fixture(tmp_path, documents_yml=_EVERY_YML)
    every = _render(tmp_path).text
    assert every != default
    assert "Every section is listed below, one row each." in every
    a_rows = _rows(every, "### [docs/design/a.md]")
    assert a_rows == [
        ["[A](../design/a.md#a)", "current (no status line)", "", ""],
        [
            "[KYO-1](../design/a.md#kyo-1)",
            "amended",
            "[KYO-2](../design/a.md#kyo-2)",
            "[KYO-3](../design/a.md#kyo-3)",
        ],
        [
            "[KYO-2](../design/a.md#kyo-2)",
            "amended",
            "[KYO-3](../design/a.md#kyo-3)",
            "[KYO-3](../design/a.md#kyo-3)",
        ],
        ["[KYO-3](../design/a.md#kyo-3)", "current (no status line)", "", ""],
        ["[KYO-4](../design/a.md#kyo-4)", "current (no status line)", "", ""],
    ]
    assert len(_rows(every, "### [docs/design/b.md]")) == 2
    # The summary table and the unpointed list are the default's, unchanged.
    assert "| [docs/design/a.md](../design/a.md) | 5 | 3 | 2 | 0 | 3 |" in every
    assert default.split("### Sections no code points at")[1] == every.split(
        "### Sections no code points at"
    )[1]


def test_kyo512_c1_a_numbered_set_rows_carry_the_number_title_and_chain_head(
    tmp_path: Path,
) -> None:
    """c1 in a numbered set: number and title in their own cells, the direct link and
    the chain followed to its head (1 -> 2 -> 3), both named by number, and
    `index_depth: 2` leaving the `###` heading out."""
    _notes_fixture(tmp_path)
    item = _render(tmp_path)
    assert item.path == "notes/NOTES-SECTIONS.md"
    rows = _rows(item.text, "### [notes/NOTES.md]")
    assert [row[:5] for row in rows] == [
        ["", "Notes", "current (no status line)", "", ""],
        ["1", "The first finding", "amended", "2", "3"],
        ["2", "The second finding", "amended", "3", "3"],
        ["3", "The third finding", "current", "", ""],
        ["4", "A historical one", "historical", "", ""],
    ]
    assert "sub-heading" not in item.text.split("### [notes/NOTES.md]")[1].split("###")[0]
    assert "at heading level 2 or above is listed below" in item.text

    # Without a depth the sub-heading gets its row too: every section, one row each.
    _notes_fixture(tmp_path, documents_yml=_NOTES_YML.replace("      index_depth: 2\n", ""))
    item = _render(tmp_path)
    rows = _rows(item.text, "### [notes/NOTES.md]")
    assert rows[2][:2] == ["", "A sub-heading inside section 1"]
    assert len(rows) == 6


def test_kyo512_c2_a_row_carries_its_governs_targets(tmp_path: Path) -> None:
    """c2: the governs column appears when the document's sections declare targets, each
    target as written; a document whose sections declare none has no such column."""
    _notes_fixture(tmp_path)
    item = _render(tmp_path)
    assert "| # | title | status | links | head of chain | governs |" in item.text
    rows = _rows(item.text, "### [notes/NOTES.md]")
    assert rows[1][5] == "`pkg/one.py:fetch`, `pkg/two.py`"
    assert rows[2][5] == ""

    _fixture(tmp_path, documents_yml=_EVERY_YML)
    every = _render(tmp_path).text
    assert "| section | status | links | head of chain |\n" in every
    assert "governs" not in every


def test_kyo512_c3_a_prose_edit_leaves_the_index_byte_identical(tmp_path: Path) -> None:
    """c3: prose added, reworded and moved down the file changes nothing in the
    rendered index; a status change (the canary) does."""
    _notes_fixture(tmp_path)
    sets = docs_parse.document_set(tmp_path / "kyosei").section_status
    [before] = docs_index.indexes(tmp_path, sets)

    edited = _NOTES.replace(
        "The first finding's prose.",
        "Three new lines a worker added in place.\n\nThey move every later line.\n\n"
        "The first finding's prose, reworded.",
    ).replace("Kept as history.", "Kept as history, and now explained at length.")
    _notes_fixture(tmp_path, notes=edited)
    [after] = docs_index.indexes(tmp_path, sets)
    assert after.text == before.text

    retired = edited.replace("status: historical", "status: superseded\nsuperseded-by: 3")
    _notes_fixture(tmp_path, notes=retired)
    [canary] = docs_index.indexes(tmp_path, sets)
    assert canary.text != before.text


@pytest.mark.parametrize(
    ("block", "named"),
    [
        ("index_lists: all", "index_lists must be one of non-current, every; got 'all'"),
        ("index_lists: [every]", "index_lists must be one of non-current, every"),
        (
            "index_lists: every\n      index_depth: 7",
            "index_depth must be a heading level, 1 to 6",
        ),
        ("index_lists: every\n      index_depth: two", "index_depth must be a heading level"),
        ("index_depth: 2", "does not declare index_lists: every"),
    ],
)
def test_kyo512_c4_a_malformed_option_is_refused_by_name_and_the_remedy_clears_it(
    kyo, project: Path, block: str, named: str
) -> None:
    _fixture(project)
    bad = _DOCUMENTS_YML.replace(
        f"      index: {INDEX_PATH}\n", f"      index: {INDEX_PATH}\n      {block}\n"
    )
    _fixture(project, documents_yml=bad)
    result = kyo("docs", "check")
    assert result.code != 0, result
    assert "section_status" in result.err and named in result.err

    _fixture(project, documents_yml=_EVERY_YML)
    assert kyo("docs", "check").code == 0


def test_kyo512_c4_index_lists_without_an_index_is_refused_and_adding_one_clears_it() -> None:
    refused = registry_module.section_status_block(
        {"statuses": ["current"], "index_lists": "every"}
    )
    assert refused is not None and "declares no index: path" in refused
    assert (
        registry_module.section_status_block(
            {"statuses": ["current"], "index_lists": "every", "index": "x.md"}
        )
        is None
    )


def test_kyo512_the_set_states_what_its_index_lists(tmp_path: Path) -> None:
    _notes_fixture(tmp_path)
    [chosen] = docs_parse.document_set(tmp_path / "kyosei").section_status
    assert chosen.index_lists == "every" and chosen.index_depth == 2
    says = "index: notes/NOTES-SECTIONS.md, listing every section to heading level 2"
    assert says in chosen.says
    _fixture(tmp_path)
    [default] = docs_parse.document_set(tmp_path / "kyosei").section_status
    assert default.index_lists == "non-current" and default.index_depth is None
    assert "listing every section" not in default.says


# ------------------------------------------- KYO-520: index_fields and the depth-following count

_FIELDS_YML = _NOTES_YML.replace(
    "      index_depth: 2\n", "      index_depth: 2\n      index_fields: [area, owner]\n"
)

_FIELDS_NOTES = _NOTES.replace(
    "status: amended\namended_by: [2]\ngovs", "x"
).replace(
    "governs:\n  - pkg/one.py:fetch", "area: derive\ngoverns:\n  - pkg/one.py:fetch"
).replace("status: current\n", "status: current\narea: tooling\n")


def test_kyo520_c1_named_fields_render_as_columns_empty_where_undeclared(
    tmp_path: Path,
) -> None:
    _notes_fixture(tmp_path, notes=_FIELDS_NOTES, documents_yml=_FIELDS_YML)
    text = _render(tmp_path).text
    assert "| governs | area | owner |\n" in text
    rows = _rows(text, "### [notes/NOTES.md]")
    assert [row[-2:] for row in rows] == [
        ["", ""],  # the file's own title heading
        ["derive", ""],
        ["", ""],
        ["tooling", ""],
        ["", ""],
    ]


def test_kyo520_c2_a_malformed_index_fields_is_refused_by_name_and_the_remedy_clears_it() -> (
    None
):
    base = {"statuses": ["current"], "index": "x.md", "index_lists": "every"}
    for bad, named in [
        ("area", "index_fields must be a non-empty list of non-empty field names"),
        ([], "index_fields must be a non-empty list"),
        ([""], "index_fields must be a non-empty list"),
        ([3], "index_fields must be a non-empty list"),
        (["area", "Status"], "index_fields names Status, which the index already has"),
        (["area", "area"], "names a field twice"),
    ]:
        refused = registry_module.section_status_block({**base, "index_fields": bad})
        assert refused is not None and named in refused, (bad, refused)
        assert registry_module.section_status_block({**base, "index_fields": ["area"]}) is None
    refused = registry_module.section_status_block(
        {"statuses": ["current"], "index": "x.md", "index_fields": ["area"]}
    )
    assert refused is not None and "does not declare index_lists: every" in refused


def test_kyo520_c2_the_refusal_reaches_docs_check(kyo, project: Path) -> None:
    _fixture(project, documents_yml=_EVERY_YML.replace(
        "index_lists: every\n", "index_lists: every\n      index_fields: [links]\n"
    ))
    result = kyo("docs", "check")
    assert result.code != 0 and "index_fields names links" in result.err
    _fixture(project, documents_yml=_EVERY_YML.replace(
        "index_lists: every\n", "index_lists: every\n      index_fields: [area]\n"
    ))
    assert kyo("docs", "check").code == 0


def test_kyo520_c3_the_summary_count_follows_index_depth(tmp_path: Path) -> None:
    _notes_fixture(tmp_path)
    text = _render(tmp_path).text
    # Six headed sections in the file, five at level 2 or above: the count equals the rows.
    assert len(_rows(text, "### [notes/NOTES.md]")) == 5
    assert "5 section(s) across 1 document(s)" in text
    unlimited = _NOTES_YML.replace("      index_depth: 2\n", "")
    _notes_fixture(tmp_path, documents_yml=unlimited)
    text = _render(tmp_path).text
    assert "6 section(s) across 1 document(s)" in text
    assert len(_rows(text, "### [notes/NOTES.md]")) == 6


def test_kyo520_c4_a_prose_edit_leaves_the_index_byte_identical(tmp_path: Path) -> None:
    _notes_fixture(tmp_path, notes=_FIELDS_NOTES, documents_yml=_FIELDS_YML)
    sets = docs_parse.document_set(tmp_path / "kyosei").section_status
    [before] = docs_index.indexes(tmp_path, sets)
    edited = _FIELDS_NOTES.replace(
        "The first finding's prose.", "Added lines.\n\nThey move later ones.\n\nReworded."
    )
    _notes_fixture(tmp_path, notes=edited, documents_yml=_FIELDS_YML)
    [after] = docs_index.indexes(tmp_path, sets)
    assert after.text == before.text
    changed = edited.replace("area: derive", "area: chain-adapter")
    _notes_fixture(tmp_path, notes=changed, documents_yml=_FIELDS_YML)
    [canary] = docs_index.indexes(tmp_path, sets)
    assert canary.text != before.text and "chain-adapter" in canary.text


def test_kyo520_c5_documents_yml_describes_both() -> None:
    text = (REPO_ROOT / "src/kyosei/defaults/documents.yml").read_text(encoding="utf-8")
    assert "index_fields: [area]" in text and "summary table's section count follows it" in text
