"""A Base over ideas and over docs — the other two sources, one engine (KYO-109).

KYO-108 built the engine and ran it over records. A Base is a saved view over
records, ideas or docs, so this file is about the two that read files rather
than a database, and about the property that makes them the same feature rather than
three: **one result shape**. A renderer that can draw a records Base draws an ideas one
without learning a second document.

The load-bearing tests here are the ones that would still pass if the sources were
three separate implementations, and then would not: the cross-source envelope test
(KYO-109.c4) and the prose-normalisation test (KYO-109.c3), which pins a requirement
the Base design states up front rather than leaving it to be rediscovered.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from kyosei import docs_staleness
from kyosei import query as query_module

from .conftest import git_init, new_record
from .test_bases import bases_yml, write_bases

# --------------------------------------------------------------------------- helpers


def write_idea(project: Path, rel: str, text: str) -> Path:
    path = project / "ideas" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_doc(project: Path, rel: str, text: str) -> Path:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def base_over(source: str, **fields: object) -> str:
    """One base named ``only`` over ``source``, from a mapping of its declared fields."""
    lines = ["bases:", "  only:", "    summary: A saved view.", f"    source: {source}"]
    for key, value in fields.items():
        lines.append(f"    {key.replace('_', '-')}: {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def run_only(kyo, project: Path, source: str, **fields: object) -> dict:
    write_bases(project, base_over(source, **fields))
    result = kyo("base", "run", "only", "--json")
    assert result.code == 0, result
    return result.json


def refuse_only(kyo, project: Path, source: str, **fields: object):
    write_bases(project, base_over(source, **fields))
    result = kyo("base", "list")
    assert result.code == 1, result
    return result


STAMPED_IDEA = """\
---
author: ann
area: query
promoted:
  - TST-1
promoted_at:
  - "2026-08-27T10:00:00.000Z"
---

# The first idea

Some prose about the founding
charter, which wrapped.

## A promoted section

> **Promoted -> TST-1** · 2026-08-27 · #promoted #TST-1
> Text now lives in the record.

## An unpromoted section

Text that is still here.
"""

BARE_IDEA = """\
# No frontmatter at all

Just prose, filed in ten seconds.
"""

GOVERNING_DOC = """\
---
scope: server
---

# Widgets

## How widgets are made

governs: src/widget.py, src/widget.py#make_widget
status: current

Prose about the mechanism, which mentions the founding
charter across a line break.

## The old way

governs: src/vanished.py
amended-by: How widgets are made
"""


def a_pool(project: Path) -> None:
    """One stamped idea with properties and sections, one with nothing at all."""
    write_idea(project, "v2 design/one.md", STAMPED_IDEA)
    write_idea(project, "bare.md", BARE_IDEA)


def a_docs_tree(project: Path) -> None:
    write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    write_doc(project, "docs/design/widgets.md", GOVERNING_DOC)


# --------------------------------------------------------------------- KYO-109.c1
# A Base can run over ideas: frontmatter properties are queryable fields, a promoted
# stub is queryable by what its frontmatter states, and an idea with no frontmatter is
# a row with empty properties rather than an error.


def test_a_base_runs_over_the_ideas_pool(kyo, project: Path) -> None:
    """The rows are the files, and a row is called by its path — the ideas layer has no
    identifiers of its own and must not acquire any (law 13)."""
    a_pool(project)
    data = run_only(kyo, project, "ideas")
    assert [row["id"] for row in data["rows"]] == [
        "ideas/bare.md",
        "ideas/v2 design/one.md",
    ]
    assert data["applied"]["source"] == "ideas"
    assert data["summary"]["total"] == 2


def test_an_idea_with_no_frontmatter_is_a_row_with_empty_properties(
    kyo, project: Path
) -> None:
    """KYO-109.c1, and law 13 in the query layer: the pool has zero required ceremony,
    so the file most of an ideas tree looks like is a row, not a refusal."""
    write_idea(project, "bare.md", BARE_IDEA)
    data = run_only(kyo, project, "ideas")
    [row] = data["rows"]
    assert row["properties"] == {}
    assert row["title"] == "No frontmatter at all"
    assert row["stamped"] is False
    assert row["promoted"] == []


def test_a_promoted_stub_is_queryable_by_what_its_frontmatter_states(
    kyo, project: Path
) -> None:
    """The stamp KYO-30 moved into frontmatter for exactly this reason: without it, a
    Base over ideas has nothing to select on."""
    a_pool(project)
    data = run_only(kyo, project, "ideas", filters={"properties.promoted": "TST-1"})
    assert [row["id"] for row in data["rows"]] == ["ideas/v2 design/one.md"]
    [row] = data["rows"]
    assert row["properties"]["promoted"] == ["TST-1"]
    assert row["properties"]["promoted_at"] == ["2026-08-27T10:00:00.000Z"]
    # Beside the raw properties, the promotion machinery's own reading of them — which
    # also recognises the pre-KYO-30 bottom-of-file block that no property read can see.
    assert row["promoted"] == ["TST-1"]
    assert row["stamped"] is True


def test_a_bottom_stamped_file_is_promoted_without_saying_so_in_its_properties(
    kyo, project: Path
) -> None:
    """Why the row carries `promoted` as well as `properties.promoted`: a file still
    carrying the pre-KYO-30 trailing block has no frontmatter to select on, and reading
    only properties would report it as unpromoted."""
    write_idea(
        project,
        "legacy.md",
        "# An old one\n\nprose\n\n---\npromoted: TST-9\n"
        "promoted_at: 2026-01-01T00:00:00.000Z\n---\n",
    )
    [row] = run_only(kyo, project, "ideas")["rows"]
    assert row["properties"] == {}
    assert row["promoted"] == ["TST-9"]
    assert row["stamped"] is True


def test_a_human_authored_property_is_a_queryable_field(kyo, project: Path) -> None:
    """Any structured property the design wants ideas to be queryable by (`author:`,
    `scope:`, `kind:`) arrives through that same block. Nothing in this tool
    decides which keys are legitimate, so the same key works as a filter, a column and
    a grouping."""
    a_pool(project)
    data = run_only(
        kyo,
        project,
        "ideas",
        filters={"properties.author": "ann"},
        columns=["id", "properties.area"],
        group_by="properties.author",
    )
    assert [row["id"] for row in data["rows"]] == ["ideas/v2 design/one.md"]
    assert data["groups"] == [
        {"value": "ann", "count": 1, "ids": ["ideas/v2 design/one.md"]}
    ]
    assert data["rows"][0]["properties"]["area"] == "query"


def test_a_property_no_file_declares_is_an_empty_column_not_a_refusal(
    kyo, project: Path
) -> None:
    """The one thing about a file source that cannot be validated on load. Which keys
    exist is a fact about the files, and the files are never validated (law 12/13) — so
    asking for one nothing declares is an empty column, and this tool never tells a
    human which frontmatter keys their own vault may use."""
    a_pool(project)
    data = run_only(kyo, project, "ideas", columns=["id", "properties.nobody-writes-this"])
    assert data["applied"]["columns"] == ["id", "properties.nobody-writes-this"]
    assert [row["properties"].get("nobody-writes-this") for row in data["rows"]] == [
        None,
        None,
    ]
    # And asking for it as a filter selects nothing rather than everything: a property
    # the file does not state is not an answer to a question about that property.
    data = run_only(
        kyo, project, "ideas", filters={"properties.nobody-writes-this": "x"}
    )
    assert data["rows"] == []


def test_a_boolean_field_filters_on_the_word_the_column_shows(
    kyo, project: Path
) -> None:
    """The ``unpromoted`` example the packaged bases.yml documents, run. A field's
    filter takes the word its column prints, so a reader who has seen the listing can
    write the filter without a second vocabulary."""
    a_pool(project)
    data = run_only(kyo, project, "ideas", filters={"stamped": "false"})
    assert [row["id"] for row in data["rows"]] == ["ideas/bare.md"]
    data = run_only(kyo, project, "ideas", filters={"stamped": "true"})
    assert [row["id"] for row in data["rows"]] == ["ideas/v2 design/one.md"]


def test_a_staleness_filter_selects_the_documents_a_banner_would_warn_about(
    kyo, project: Path
) -> None:
    """The ``stale-docs`` example the packaged bases.yml documents, run — and the shape
    the `touches x governs` banner is a rendering of."""
    a_docs_tree(project)
    write_doc(project, "src/vanished.py", "# now it exists\n")
    write_doc(project, "docs/musing.md", "# A musing\n\nNo claims.\n")
    git_init(project)
    time.sleep(1.1)  # git commit dates have one-second resolution
    write_doc(project, "src/widget.py", "def make_widget():\n    return 2\n")
    commit(project, "change the governed code")

    data = run_only(kyo, project, "docs", filters={"staleness": "stale"})
    assert [row["id"] for row in data["rows"]] == ["docs/design/widgets.md"]


def test_a_partly_promoted_file_exposes_its_sections_state(kyo, project: Path) -> None:
    """KYO-107: one section of a file is promoted and the rest survive. The row says
    which — through :func:`kyosei.ideas.sections`, the promotion machinery's own
    addressing, rather than a second reading of the same file."""
    a_pool(project)
    data = run_only(kyo, project, "ideas", filters={"path": "*one.md"})
    [row] = data["rows"]
    by_heading = {section["heading"]: section for section in row["sections"]}
    assert by_heading["A promoted section"]["promoted_to"] == ["TST-1"]
    assert by_heading["An unpromoted section"]["promoted_to"] == []
    assert by_heading["An unpromoted section"]["has_text"] is True
    assert row["section_count"] == 3


def test_the_ideas_source_writes_nothing(kyo, project: Path) -> None:
    """Law 12: the pool is never validated, swept or enforced against — and a query
    surface over it is exactly where that could quietly stop being true."""
    a_pool(project)
    before = {
        path: path.read_bytes() for path in (project / "ideas").rglob("*.md")
    }
    assert run_only(kyo, project, "ideas")["summary"]["total"] == 2
    assert {path: path.read_bytes() for path in (project / "ideas").rglob("*.md")} == before


def test_the_ideas_counted_split_is_what_is_still_waiting_to_become_work(
    kyo, project: Path
) -> None:
    """The summary slot filled in this source's own terms. A stamped idea points at a
    record that the records source already counts; counting it here too would weigh it
    twice in "how much is open"."""
    a_pool(project)
    summary = run_only(kyo, project, "ideas")["summary"]
    assert summary == {"total": 2, "counted": 1, "not_counted": 1}


# --------------------------------------------------------------------- KYO-109.c2
# A Base can run over docs, through the same engine, on the same terms.


def test_a_base_runs_over_the_docs_tree(kyo, project: Path) -> None:
    a_docs_tree(project)
    data = run_only(kyo, project, "docs")
    [row] = data["rows"]
    assert row["id"] == "docs/design/widgets.md"
    assert row["title"] == "Widgets"
    assert row["governs"] == [
        "src/widget.py",
        "src/widget.py#make_widget",
        "src/vanished.py",
    ]
    # Whatever git could or could not answer here, the state is one of the four named
    # ones — a rendering switching on it has a closed set to switch on.
    assert row["staleness"] in docs_staleness.STALENESS_STATES


def test_the_broken_governs_targets_are_the_ones_kyo_docs_check_fails_on(
    kyo, project: Path
) -> None:
    """One resolution rule, two consumers: the build failure and the banner cannot
    disagree about whether a document still governs what it claims to."""
    a_docs_tree(project)
    [row] = run_only(kyo, project, "docs")["rows"]
    checked = kyo("docs", "check", "--json")
    assert checked.code == 3
    assert row["governs_broken"] == [
        problem["target"] for problem in checked.json["problems"]
    ]
    assert row["governs_broken"] == ["src/vanished.py"]


def test_a_governs_filter_selects_the_documents_that_claim_a_path(
    kyo, project: Path
) -> None:
    """The half of `touches x governs` this source owes the banner: given a path, which
    documents claim it. A filter matches any item of a multi-valued field."""
    a_docs_tree(project)
    write_doc(project, "docs/other.md", "# Other\n\nNo claims here.\n")
    data = run_only(kyo, project, "docs", filters={"governs": "src/widget.py"})
    assert [row["id"] for row in data["rows"]] == ["docs/design/widgets.md"]


def test_the_amendment_state_is_readable_without_becoming_a_check(
    kyo, project: Path
) -> None:
    """This source reads both fields and acts on neither (law 2: the fields have a
    reader). The cross-check is ``kyo docs check``'s, since KYO-499, and it is a check
    of its own: the pair below disagrees, and the validator says so as a section-status
    problem beside — never inside — the unresolved ``governs:`` target.
    """
    a_docs_tree(project)
    [row] = run_only(kyo, project, "docs")["rows"]
    by_heading = {section["heading"]: section for section in row["sections"]}
    assert by_heading["How widgets are made"]["status"] == "current"
    assert by_heading["The old way"]["amended_by"] == "How widgets are made"
    assert row["amended"] is True
    # The validator's governs: problems are still the unresolved target alone; the pair
    # (an amended-by with no status) is KYO-499's section-status problem.
    checked = kyo("docs", "check", "--json").json
    assert [problem["target"] for problem in checked["problems"]] == ["src/vanished.py"]
    assert [problem["reason"] for problem in checked["section_problems"]] == [
        "section-link-without-status"
    ]


def test_a_docs_frontmatter_property_is_queryable_on_the_same_terms(
    kyo, project: Path
) -> None:
    """"The same engine on the same terms" (KYO-109.c2), shown where the two file
    sources are most alike: a YAML frontmatter block is read by one reader, so a
    ``scope:`` on a doc is the same kind of queryable fact as an ``author:`` on an
    idea."""
    a_docs_tree(project)
    data = run_only(kyo, project, "docs", filters={"properties.scope": "server"})
    assert [row["id"] for row in data["rows"]] == ["docs/design/widgets.md"]
    assert data["rows"][0]["properties"] == {"scope": "server"}


def test_a_docs_file_with_no_frontmatter_is_a_row_with_empty_properties(
    kyo, project: Path
) -> None:
    write_doc(project, "docs/plain.md", "# Plain\n\nNo properties.\n")
    [row] = run_only(kyo, project, "docs")["rows"]
    assert row["properties"] == {}


# --------------------------------------------------------------------- KYO-109.c3
# Every operator reaching body prose normalises whitespace on both sides, via
# kyosei.prose.phrase_pattern. Frontmatter values are one-line and matched as values.


@pytest.mark.parametrize("source", ["ideas", "docs"])
def test_a_contains_needle_matches_across_a_hard_wrapped_line(
    kyo, project: Path, source: str
) -> None:
    """The criterion this design was given rather than left to rediscover.

    Both fixtures wrap "founding charter" across a line break, deliberately: this repo's
    prose is hard-wrapped on purpose, and a fixed-string match over it answers "does the
    phrase appear WITHOUT an intervening break", which is not the question anyone asks.
    The needle here also carries a run of spaces of its own, so the normalisation is
    shown to be on both sides and not only on the haystack.
    """
    a_pool(project)
    a_docs_tree(project)
    data = run_only(kyo, project, source, filters={"contains": "founding   charter"})
    assert len(data["rows"]) == 1
    unmatched = run_only(kyo, project, source, filters={"contains": "founding treaty"})
    assert unmatched["rows"] == []


def test_the_body_operator_goes_through_phrase_pattern(
    kyo, project: Path, monkeypatch
) -> None:
    """c3 names the function, not the behaviour, and for a reason: KYO-66 fixed four
    matchers that each reimplemented "does this phrase appear" and each got it wrong.
    So this asserts the call, by replacing :func:`kyosei.prose.phrase_pattern` with a
    recording wrapper — if a future operator matches prose some other way, the phrase it
    matched will not show up here.
    """
    from kyosei import prose

    seen: list[str] = []
    real = prose.phrase_pattern

    def recording(phrase: str) -> str:
        seen.append(phrase)
        return real(phrase)

    monkeypatch.setattr(prose, "phrase_pattern", recording)
    a_pool(project)
    run_only(kyo, project, "ideas", filters={"contains": "founding charter"})
    assert "founding charter" in seen


def test_a_property_value_is_matched_as_a_value_not_as_a_phrase_in_it(
    kyo, project: Path
) -> None:
    """Frontmatter property values are one-line and unaffected by the normalisation,
    which is about the body. So a property filter is whole-value equality: it does not
    find ``ann`` inside ``ann lee``, and ``contains:`` is the operator that searches
    within text."""
    write_idea(project, "a.md", "---\nauthor: ann lee\n---\n\n# A\n\nprose\n")
    assert run_only(kyo, project, "ideas", filters={"properties.author": "ann"})[
        "rows"
    ] == []
    assert (
        len(
            run_only(
                kyo, project, "ideas", filters={"properties.author": "ann lee"}
            )["rows"]
        )
        == 1
    )


def test_a_contains_filter_with_no_words_is_refused_when_the_file_is_read(
    kyo, project: Path
) -> None:
    """An empty phrase matches at every position, so a base declaring one would answer
    every question with the whole pool and look like it was working (law 6)."""
    result = refuse_only(kyo, project, "ideas", filters={"contains": "   "})
    assert "contains" in result.err
    assert "matches every file" in result.err


# --------------------------------------------------------------------- KYO-109.c4
# One result shape: a --json consumer needs no per-source special-casing.

ALL_SOURCES = ["records", "ideas", "docs"]


def a_row_in_every_source(kyo, project: Path) -> None:
    new_record(kyo, kind="bug", title="widget explodes")
    a_pool(project)
    a_docs_tree(project)


def test_one_definition_shape_runs_against_all_three_sources(kyo, project: Path) -> None:
    """KYO-109.c4, stated as the record states it: ONE definition shape, three sources,
    and a consumer that special-cases none of them.

    ``id`` and ``title`` are fields of all three — a record's id and a file's path are
    the same slot — so this really is one definition, not three that rhyme. What differs
    is the rest of the field set, which is what "rows from ideas and docs carry file
    paths where records carry ids" means; what may not differ is the envelope.
    """
    a_row_in_every_source(kyo, project)
    documents = {
        source: run_only(
            kyo, project, source, columns=["id", "title"], sort=["-id"], group_by="title"
        )
        for source in ALL_SOURCES
    }
    shapes = {source: sorted(data) for source, data in documents.items()}
    assert len(set(map(tuple, shapes.values()))) == 1, shapes
    for source, data in documents.items():
        assert data["applied"]["columns"] == ["id", "title"], source
        assert data["applied"]["sort"] == ["-id"], source
        assert data["applied"]["group-by"] == "title", source
        assert sorted(data["summary"]) == ["counted", "not_counted", "total"], source
        assert data["db_export_currency"]["direction"] == "in-step", source
        assert data["rows"], source
        for row in data["rows"]:
            assert set(row) >= {"id", "title"}, source
        for group in data["groups"]:
            assert sorted(group) == ["count", "ids", "value"], source
            assert group["ids"][0] in [row["id"] for row in data["rows"]], source


def test_no_grouping_binding_is_null_on_every_source(kyo, project: Path) -> None:
    a_row_in_every_source(kyo, project)
    for source in ALL_SOURCES:
        assert run_only(kyo, project, source)["groups"] is None, source


def test_the_sort_is_the_same_arrangement_step_on_every_source(
    kyo, project: Path
) -> None:
    """Sorting is not per-source code: one `bases.sorted_rows` over one field-resolution
    rule, so ``-id`` reverses a records base and an ideas base identically."""
    a_row_in_every_source(kyo, project)
    for source in ALL_SOURCES:
        ascending = [row["id"] for row in run_only(kyo, project, source, sort=["id"])["rows"]]
        descending = [
            row["id"] for row in run_only(kyo, project, source, sort=["-id"])["rows"]
        ]
        assert descending == list(reversed(ascending)), source


def test_a_default_base_shows_the_fields_that_sources_own_row_has(
    kyo, project: Path
) -> None:
    """The envelope is one and the field set is three. A base declaring no columns gets
    its own source's fields — which is why "no special-casing" is a claim about columns,
    grouping and currency rather than about field names."""
    a_row_in_every_source(kyo, project)
    for source, expected in (
        ("records", query_module.ROW_FIELDS),
        ("ideas", query_module.IDEAS_ROW_FIELDS),
        ("docs", query_module.DOCS_ROW_FIELDS),
    ):
        data = run_only(kyo, project, source)
        assert data["applied"]["columns"] == list(expected), source
        for row in data["rows"]:
            assert set(expected) <= set(row), source


def test_the_engine_holds_one_result_shape_and_one_runner(kyo) -> None:
    """The structural half of c4, and the thing a passing behavioural test cannot show:
    that ``run_base`` does not branch on the source. If it did, the three would drift
    the day one of them gained a key — and the two that are not the board's would drift
    unnoticed, because nobody looks at them."""
    from kyosei import bases

    runner = Path(bases.__file__).read_text(encoding="utf-8")
    # Past the docstring, which is allowed to talk about the branch that is not there.
    body = runner.split("def run_base(")[1].split('"""')[2]
    assert "source ==" not in body
    assert "SOURCE_IDEAS" not in body and "SOURCE_DOCS" not in body
    assert "ideas" not in body and "docs" not in body
    # And every source really is a value in the one registry, not a code path.
    assert set(query_module.SOURCES) == set(ALL_SOURCES)
    assert tuple(bases.BASE_SOURCES) == tuple(query_module.SOURCES)


# --------------------------------------------------------------------- KYO-109.c5
# The docs source exposes the staleness and currency data the touches x governs banner
# needs, in the same could-not-tell-capable form the records source carries.


def commit(project: Path, message: str, *paths: str) -> None:
    """Commit exactly ``paths`` (everything, if none are named)."""
    subprocess.run(
        ["git", "add", *(paths or ["-A"])], cwd=project, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=project, check=True, capture_output=True
    )


def test_a_doc_is_current_with_the_code_it_governs(kyo, project: Path) -> None:
    a_docs_tree(project)
    write_doc(project, "src/vanished.py", "# now it exists\n")
    git_init(project)
    [row] = run_only(kyo, project, "docs")["rows"]
    assert row["staleness"] == docs_staleness.STALENESS_CURRENT
    assert "at or after" in row["staleness_says"]


def test_a_doc_is_stale_when_the_code_it_governs_moved_after_it(
    kyo, project: Path
) -> None:
    """The `touches x governs` banner's own question. The design draws it as a
    prominent, actionable warning; what this source owes it is the answer and the
    evidence behind it."""
    a_docs_tree(project)
    write_doc(project, "src/vanished.py", "# now it exists\n")
    git_init(project)
    time.sleep(1.1)  # git commit dates have one-second resolution
    write_doc(project, "src/widget.py", "def make_widget():\n    return 2\n")
    commit(project, "change the governed code")

    [row] = run_only(kyo, project, "docs")["rows"]
    assert row["staleness"] == docs_staleness.STALENESS_STALE
    assert "src/widget.py" in row["staleness_says"]


def test_a_doc_git_cannot_date_reads_cannot_tell_and_never_current(
    kyo, project: Path
) -> None:
    """The state the design adds because a view that can only paint 'stale' and
    'not stale' has nowhere to put 'could not tell'.

    The dangerous failure is an ABSENT flag, so this asserts the three answers are
    DISTINGUISHABLE — not merely that none of them crashes. The mtime fallback that
    would have made this case look ``current`` is deliberately not taken: on a fresh
    clone every file's mtime is the checkout time, and a banner built on that would
    report a whole docs tree current the first time anyone ran it.
    """
    a_docs_tree(project)  # no git repository at all
    write_doc(project, "src/vanished.py", "# now it exists\n")
    [ungitted] = run_only(kyo, project, "docs")["rows"]

    git_init(project)
    [dated] = run_only(kyo, project, "docs")["rows"]

    assert ungitted["staleness"] == docs_staleness.STALENESS_CANNOT_TELL
    assert dated["staleness"] != ungitted["staleness"]
    assert dated["staleness"] in (
        docs_staleness.STALENESS_CURRENT,
        docs_staleness.STALENESS_STALE,
    )
    assert "docs/design/widgets.md" in ungitted["staleness_says"]
    assert "not that it did not" in ungitted["staleness_says"]
    assert not docs_staleness.staleness_answered(ungitted["staleness"])


def test_a_doc_that_governs_nothing_is_not_painted_current(kyo, project: Path) -> None:
    """"Nothing about this file went stale" and "this file makes no claim about any
    code" are different facts. Painting the second as the first is a banner asserting
    currency from no evidence, which is the same law-6 shape as the case above."""
    write_doc(project, "docs/musing.md", "# A musing\n\nNo governs: line here.\n")
    git_init(project)
    [row] = run_only(kyo, project, "docs")["rows"]
    assert row["staleness"] == docs_staleness.STALENESS_UNGOVERNED
    assert row["staleness"] not in (
        docs_staleness.STALENESS_CURRENT,
        docs_staleness.STALENESS_STALE,
    )
    assert not docs_staleness.staleness_answered(row["staleness"])


def test_an_undatable_governed_path_is_cannot_tell_rather_than_current(
    kyo, project: Path
) -> None:
    """Half an answer is not an answer. A doc whose governed file git has never seen
    cannot be called current on the strength of the files it *could* date."""
    a_docs_tree(project)
    write_doc(project, "src/vanished.py", "# now it exists\n")
    git_init(project)
    write_doc(
        project,
        "docs/design/widgets.md",
        "# Widgets\n\n## Made\n\ngoverns: src/untracked.py\n",
    )
    commit(project, "govern a file git has never seen", "docs")
    # Written after the commit and never added: the file is really there, so it is not
    # a broken target — it is a target git cannot date.
    write_doc(project, "src/untracked.py", "x = 1\n")

    [row] = run_only(kyo, project, "docs")["rows"]
    assert row["staleness"] == docs_staleness.STALENESS_CANNOT_TELL
    assert "src/untracked.py" in row["staleness_says"]


def test_the_docs_source_carries_the_same_currency_block_the_records_source_does(
    kyo, project: Path
) -> None:
    """The other half of c5, and c4's currency clause: the db/export seam is a fact
    about the store the run was made against, so every source reports it, in the same
    three-plus-one directions, from the same one comparison."""
    new_record(kyo, kind="bug", title="one")
    a_docs_tree(project)
    clean = {
        source: run_only(kyo, project, source)["db_export_currency"]
        for source in ALL_SOURCES
    }
    (project / "kyosei" / "export" / "TST-404.json").write_text("{}\n", encoding="utf-8")
    murky = {
        source: run_only(kyo, project, source)["db_export_currency"]
        for source in ALL_SOURCES
    }
    for source in ALL_SOURCES:
        assert clean[source]["direction"] == "in-step", source
        assert murky[source]["direction"] == "cannot-tell", source
        assert murky[source]["undecidable_because"], source
        assert "TST-404" in murky[source]["says"], source


# ------------------------------------------------------- refusals, per source (law 6)


def test_a_records_field_is_not_an_ideas_field(kyo, project: Path) -> None:
    """The field set is per source, so the refusal is too: a column that is a record's
    is refused on an ideas base naming what an ideas row actually has."""
    result = refuse_only(kyo, project, "ideas", columns=["id", "gate_tags"])
    assert "gate_tags" in result.err
    for field_name in ("stamped", "promoted", "properties"):
        assert field_name in result.err


def test_a_records_filter_is_not_a_file_source_filter(kyo, project: Path) -> None:
    """`status: open` is a real question about records and no question at all about a
    file. Refused when the base is read rather than answered with an empty view."""
    result = refuse_only(kyo, project, "docs", filters={"status": "open"})
    assert "status" in result.err
    assert "contains" in result.err  # the operators this source does offer


def test_a_dotted_path_into_something_that_is_not_the_properties_mapping_is_refused(
    kyo, project: Path
) -> None:
    result = refuse_only(kyo, project, "ideas", columns=["title.author"])
    assert "title.author" in result.err
    assert "properties" in result.err


def test_a_records_base_cannot_reach_into_properties(kyo, project: Path) -> None:
    """Records have no open field, so the dotted form is refused there — the records
    source's closed field set is exactly what KYO-108 shipped and this task does not
    widen it."""
    result = refuse_only(kyo, project, "records", columns=["properties.author"])
    assert "properties.author" in result.err
    assert "no field of the records source has keys inside it" in result.err


def test_a_list_valued_field_cannot_be_grouped_by_on_a_file_source(
    kyo, project: Path
) -> None:
    """The declared list fields are refused on load, exactly as ``gate_tags`` is."""
    result = refuse_only(kyo, project, "ideas", group_by="promoted")
    assert "promoted" in result.err


def test_grouping_by_a_list_valued_property_is_refused_at_the_row_that_proves_it(
    kyo, project: Path
) -> None:
    """An open property cannot be refused on load — which shape a human's frontmatter
    value takes is a fact about their file, and law 13 forbids this tool having an
    opinion about it. So the refusal fires at run time, names the file, and says what to
    do instead; it does not silently stringify, which would file ``[a, b]`` and
    ``[b, a]`` as two different groups.
    """
    write_idea(project, "tagged.md", "---\ntags: [a, b]\n---\n\n# T\n\nprose\n")
    write_bases(project, base_over("ideas", group_by="properties.tags"))
    assert kyo("base", "list").code == 0  # the file itself is legal
    result = kyo("base", "run", "only")
    assert result.code == 1
    assert "tagged.md" in result.err
    assert "two groups" in result.err


def test_every_source_is_named_in_the_packaged_bases_file(project: Path) -> None:
    """`kyo init` writes the file that configures this, so the file has to describe the
    sources this build actually runs (law 6: a closed set the caller cannot see is a
    guessing game)."""
    text = bases_yml(project).read_text(encoding="utf-8")
    for source in ALL_SOURCES:
        assert source in text
    assert "contains:" in text
    assert "properties." in text


# --------------------------------------------------------------------- KYO-129.c4
# The docs SOURCE shared the check's hardcoded `docs/**/*.md` enumerator, so the blind
# spot was never confined to `kyo docs check`: a project whose documents sit at the
# repository root got no rows here either, and the `stale-docs` base shipped in
# defaults/bases.yml returned nothing on exactly the projects whose documents had gone
# stale. Both callers now resolve one DocumentSet through one enumerator.


def test_a_root_level_document_is_a_row_in_the_docs_source(kyo, project: Path) -> None:
    """The consumer's own situation, asked of the query side: no ``docs/`` directory,
    documents at the root, and a base over ``source: docs`` that must see them."""
    write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    write_doc(
        project,
        "mock-test-environment.md",
        "# Mock test environment\n\n## Setup\n\ngoverns: src/widget.py, src/vanished.py\n",
    )
    assert not (project / "docs").exists()

    data = run_only(kyo, project, "docs")

    [row] = data["rows"]
    assert row["id"] == "mock-test-environment.md"
    assert row["title"] == "Mock test environment"
    assert row["governs"] == ["src/widget.py", "src/vanished.py"]
    assert row["governs_broken"] == ["src/vanished.py"]


def test_a_declared_document_path_moves_the_query_source_with_the_check(
    kyo, project: Path
) -> None:
    """One resolver, two consumers (KYO-129.c2 + c4). A project that declares its
    documents live under ``handbook/`` has said so to both surfaces at once — the base
    and the build cannot end up asking about different trees."""
    (project / "kyosei" / "documents.yml").write_text(
        "documents:\n"
        "  handbook:\n"
        "    summary: The operating handbook.\n"
        "    globs:\n"
        "      - handbook/**/*.md\n",
        encoding="utf-8",
    )
    write_doc(project, "handbook/ops.md", "# Ops\n\ngoverns: src/vanished.py\n")
    write_doc(project, "docs/design/widgets.md", GOVERNING_DOC)

    rows = run_only(kyo, project, "docs")["rows"]
    checked = kyo("docs", "check", "--json")

    assert [row["id"] for row in rows] == ["handbook/ops.md"]
    assert checked.json["scanned"] == ["handbook/ops.md"]
    assert [p["doc_path"] for p in checked.json["problems"]] == ["handbook/ops.md"]
