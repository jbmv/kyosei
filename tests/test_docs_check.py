"""KYO-13.c3: ``kyo docs check`` — Layer 3's skeleton validator. A ``governs:``
line inside a docs/**/*.md section names a path or path#symbol that must exist
in the repo, same resolution rules as a record's ``touches``.
"""

from __future__ import annotations

from pathlib import Path


def _write_doc(project: Path, rel: str, text: str) -> Path:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_documents_yml(project: Path, body: str) -> Path:
    path = project / "kyosei" / "documents.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_docs_check_passes_with_no_docs_directory(kyo) -> None:
    """No ``docs/`` and no markdown anywhere is still not a *failure* — but it is no
    longer spelled the same way as a pass either; see the KYO-129 block below."""
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True
    assert result.json["problems"] == []


def test_docs_check_passes_when_governs_target_exists(kyo, project: Path) -> None:
    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## Widget creation\n\ngoverns: src/widget.py, src/widget.py#make_widget\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True


def test_docs_check_fails_on_a_missing_path(kyo, project: Path) -> None:
    _write_doc(project, "docs/PRD.md", "## Gone\n\ngoverns: src/nope.py\n")
    result = kyo("docs", "check")
    assert result.code == 3
    assert "src/nope.py" in result.err


def test_docs_check_fails_on_a_missing_symbol(kyo, project: Path) -> None:
    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(project, "docs/PRD.md", "## Widget\n\ngoverns: src/widget.py#not_a_real_symbol\n")
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert result.json["ok"] is False
    problem = result.json["problems"][0]
    assert problem["target"] == "src/widget.py#not_a_real_symbol"
    assert problem["section"] == "Widget"
    assert problem["doc_path"] == "docs/PRD.md"


def test_docs_check_reports_every_problem_in_one_pass(kyo, project: Path) -> None:
    _write_doc(
        project,
        "docs/PRD.md",
        "## A\n\ngoverns: does/not/exist.py\n\n## B\n\ngoverns: also/missing.py\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert len(result.json["problems"]) == 2


def test_amended_by_on_a_current_section_fails_the_build(kyo, project: Path) -> None:
    """KYO-98's test, inverted by KYO-499 as its docstring said it would be: the "pairs
    agree" half of Layer 3 is built, and the self-contradicting section below — an
    ``amended-by`` on a ``status: current`` section, the ``amended_by``/``status``
    footgun law 3 names — now fails ``kyo docs check``. The charter's Layer 3 bullet
    moved from "Outstanding" to "Enforced" in the same commit."""
    _write_doc(
        project,
        "docs/PRD.md",
        "## Widget creation\n\n"
        "status: current\n"
        "amended-by: Widget creation v2\n\n"
        "Prose that its own metadata says has been amended.\n\n"
        "## Widget creation v2\n\n"
        "status: current\n\n"
        "The amendment.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3, result
    [problem] = result.json["section_problems"]
    assert problem["reason"] == "section-link-without-status"
    assert problem["section"] == "Widget creation"


def test_docs_check_ignores_files_outside_docs(kyo, project: Path) -> None:
    """A `governs:` line in some other markdown file (e.g. an idea) is not this
    validator's concern -- only docs/**/*.md."""
    _write_doc(project, "ideas/thought.md", "governs: totally/nonexistent.py\n")
    result = kyo("docs", "check", "--json")
    assert result.code == 0
    assert result.json["problems"] == []


# ------------------------------------------------------------------------ KYO-109
# The same format, READ rather than validated: a Base over the docs source selects on
# this metadata. `kyo docs check`'s behaviour is unchanged and is asserted so above.


def test_the_section_metadata_keys_are_a_closed_vocabulary(project: Path) -> None:
    """Why not "any ``key: value`` line is metadata": ordinary prose starts with a word
    and a colon all the time, and a query surface whose field set is whatever a sentence
    began with is one no refusal can name (law 6)."""
    from kyosei.docs_parse import SECTION_METADATA_KEYS, section_metadata

    assert SECTION_METADATA_KEYS == ("governs", "status", "amended-by", "superseded-by")
    metadata = section_metadata(
        [
            "Note: this line is prose, not a property.",
            "governs: src/widget.py",
            "status: current",
        ]
    )
    assert metadata == {
        "governs": ["src/widget.py"],
        "status": "current",
        "amended-by": None,
        "superseded-by": None,
    }
    assert "Note" not in metadata


def test_every_metadata_key_is_present_even_when_the_section_declares_none(
    project: Path,
) -> None:
    """A filled mapping rather than a sparse one, so a caller never has to tell "this
    section declares no status" apart from "I forgot to look"."""
    from kyosei.docs_parse import SECTION_METADATA_KEYS, section_metadata

    metadata = section_metadata(["just prose", ""])
    assert set(metadata) == set(SECTION_METADATA_KEYS)
    assert metadata["governs"] == []
    assert metadata["status"] is None


def test_a_document_reads_its_sections_its_governs_and_its_title(project: Path) -> None:
    from kyosei.docs_targets import documents

    _write_doc(
        project,
        "docs/PRD.md",
        "# Widgets\n\n## Made\n\ngoverns: src/widget.py, src/widget.py#make\n\n"
        "## Retired\n\ngoverns: src/old.py\nstatus: current\namended-by: Made\n",
    )
    [document] = documents(project)
    assert document.path == "docs/PRD.md"
    assert document.title == "Widgets"
    assert document.governs == ["src/widget.py", "src/widget.py#make", "src/old.py"]
    by_heading = {section.heading: section for section in document.sections}
    assert by_heading["Retired"].metadata["amended-by"] == "Made"
    # The preamble is a section of the file, not a heading of level one.
    assert by_heading["(preamble)"].level == 0


def test_the_broken_targets_a_query_sees_are_the_ones_the_check_fails_on(
    kyo, project: Path
) -> None:
    """One resolution rule with two consumers (law 3): the build failure and a docs
    Base's ``governs_broken`` column cannot disagree about whether a document still
    governs what it claims to."""
    from kyosei.docs_targets import broken_governs_targets, documents

    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## A\n\ngoverns: src/widget.py, src/gone.py, src/widget.py#nope\n",
    )
    [document] = documents(project)
    broken = broken_governs_targets(project, document.governs)
    failed = [problem["target"] for problem in kyo("docs", "check", "--json").json["problems"]]
    assert broken == failed == ["src/gone.py", "src/widget.py#nope"]


def test_staleness_never_answers_current_from_evidence_it_does_not_have(
    project: Path,
) -> None:
    """The dangerous failure is an ABSENT flag, so every state git
    cannot decide is distinguishable from ``current`` and carries why.

    ``commit_at`` is injected here rather than driving a real repository, so each state
    can be provoked exactly — including the mixed case, where some governed paths date
    and one does not, which is the one a "call it current if nothing looks stale" rule
    would get wrong.
    """
    from kyosei.docs_staleness import (
        STALENESS_CANNOT_TELL,
        STALENESS_CURRENT,
        STALENESS_STALE,
        STALENESS_UNGOVERNED,
        governs_staleness,
        staleness_answered,
    )

    dates = {
        "docs/PRD.md": "2026-08-20T00:00:00+00:00",
        "src/old.py": "2026-08-19T00:00:00+00:00",
        "src/new.py": "2026-08-21T00:00:00+00:00",
    }
    lookup = lambda _root, path: dates.get(path)  # noqa: E731

    current = governs_staleness(project, "docs/PRD.md", ["src/old.py"], commit_at=lookup)
    stale = governs_staleness(project, "docs/PRD.md", ["src/new.py"], commit_at=lookup)
    mixed = governs_staleness(
        project, "docs/PRD.md", ["src/old.py", "src/untracked.py"], commit_at=lookup
    )
    undated = governs_staleness(
        project, "docs/nowhere.md", ["src/old.py"], commit_at=lookup
    )
    ungoverned = governs_staleness(project, "docs/PRD.md", [], commit_at=lookup)

    assert current.state == STALENESS_CURRENT
    assert stale.state == STALENESS_STALE and stale.changed == ("src/new.py",)
    assert mixed.state == STALENESS_CANNOT_TELL
    assert "src/untracked.py" in mixed.says
    assert undated.state == STALENESS_CANNOT_TELL
    assert ungoverned.state == STALENESS_UNGOVERNED
    assert staleness_answered(current.state) and staleness_answered(stale.state)
    for unanswered in (mixed, undated, ungoverned):
        assert not staleness_answered(unanswered.state)
        assert unanswered.says


# ------------------------------------------------------------------------ KYO-129
# WHICH FILES ARE DOCUMENTS. Discovery was `docs/` and only `docs/`, which a consumer
# audit found the failure shape of: no `docs/` directory at all, README.md and one
# operating document beside it both stale, and `kyo docs check` exiting 0 with the word
# "clean" over files it had never opened. The enforcement was real; it could not see the
# files that went stale.


def test_a_root_level_document_is_checked(kyo, project: Path) -> None:
    """KYO-129.c1. The reporting consumer's exact situation: no ``docs/`` directory, a
    governed document at the repository root, and a target that has since moved."""
    _write_doc(project, "mock-test-environment.md", "## Setup\n\ngoverns: src/gone.py\n")
    assert not (project / "docs").exists()

    result = kyo("docs", "check", "--json")

    assert result.code == 3, result
    [problem] = result.json["problems"]
    assert problem["doc_path"] == "mock-test-environment.md"
    assert problem["target"] == "src/gone.py"


def test_root_markdown_is_scanned_with_nothing_declared(kyo, project: Path) -> None:
    """KYO-129.c3, the constraint that decides whether this fix reaches anyone.

    A registry alone would have closed the gap only for projects that went and wrote
    config — which is no existing consumer, and least of all the one that reported it.
    So the default is in the code and covers both places, and this project declares
    nothing: no ``documents.yml`` is written here at all, which is the state every store
    initialised before this release is in.
    """
    # Deleted rather than left empty, so this is the state of a store that predates the
    # asset entirely — the projects the reporting consumer's is one of, and the ones a
    # default seeded into the packaged documents.yml would never have reached.
    (project / "kyosei" / "documents.yml").unlink()
    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(project, "README.md", "## Widgets\n\ngoverns: src/widget.py\n")
    _write_doc(project, "docs/design.md", "## Design\n\ngoverns: src/widget.py\n")

    result = kyo("docs", "check", "--json")

    assert result.code == 0, result
    assert result.json["document_paths"] == ["*.md", "docs/**/*.md"]
    assert result.json["document_paths_declared"] is False
    assert result.json["scanned"] == ["README.md", "docs/design.md"]


def test_the_project_declares_which_paths_are_documents(kyo, project: Path) -> None:
    """KYO-129.c2, in the registry shape KYO-100/101/102 established: one file per
    dimension under ``kyosei/``, named for the plural, entries carrying a summary.

    Declaring anything REPLACES the default rather than adding to it, which is the half
    worth pinning: a project that says "my documents are under handbook/" has said the
    root is not one, and a set that only ever grew would make that unsayable.
    """
    _write_documents_yml(
        project,
        "documents:\n"
        "  handbook:\n"
        "    summary: The operating handbook.\n"
        "    globs:\n"
        "      - handbook/**/*.md\n",
    )
    _write_doc(project, "handbook/ops/runbook.md", "## Run\n\ngoverns: src/gone.py\n")
    _write_doc(project, "README.md", "## Root\n\ngoverns: also/gone.py\n")

    result = kyo("docs", "check", "--json")

    assert result.code == 3, result
    assert result.json["document_paths"] == ["handbook/**/*.md"]
    assert result.json["document_paths_declared"] is True
    assert result.json["scanned"] == ["handbook/ops/runbook.md"]
    assert [p["target"] for p in result.json["problems"]] == ["src/gone.py"]


def test_a_project_with_no_documents_is_told_so_not_told_it_is_clean(kyo) -> None:
    """KYO-129.c5, and the reason this was filed as a bug rather than a feature.

    The old failure was exit 0 and no output — indistinguishable from a project that was
    checked and found fine. That is the "reported clean on no evidence" shape
    ``governs_staleness``'s ``cannot-tell`` state was built to avoid, reappearing one
    level up at file discovery, so it gets the same treatment here: a named state, a
    ``says`` carrying the evidence, and a whitelist saying which states mean the question
    was actually answered.

    Not a build failure. A project may legitimately hold no Layer 3 documents, and
    failing every such consumer on upgrade would be the validator asserting a defect it
    has not observed — the mirror of the bug, not the fix for it.
    """
    result = kyo("docs", "check", "--json")

    assert result.code == 0, result
    assert result.json["state"] == "no-documents"
    assert result.json["checked"] is False
    assert result.json["scanned"] == []
    # The distinguishing fact, and the one a caller reading only `ok` never gets.
    assert result.json["ok"] is True
    assert "nothing was checked" in result.json["says"]
    assert "*.md" in result.json["says"]


def test_declaring_where_the_documents_are_clears_the_no_documents_state(
    kyo, project: Path
) -> None:
    """The remedy the ``no-documents`` message names actually clears it.

    A message naming a remedy that does not apply to the state that produced it is the
    defect class KYO-73 was filed against; this project's rule is that every such message
    has a test proving the remedy works. The message says: declare where the documents
    are, in a ``documents:`` mapping with a ``globs:`` list. So this does that, and
    nothing else, and the state changes.
    """
    _write_doc(project, "notes/architecture.md", "## Shape\n\ngoverns: src/gone.py\n")
    before = kyo("docs", "check", "--json")
    assert before.json["state"] == "no-documents"
    assert str(project / "kyosei" / "documents.yml") in before.json["says"]

    _write_documents_yml(
        project,
        "documents:\n"
        "  notes:\n"
        "    summary: Where this project keeps its architecture notes.\n"
        "    globs:\n"
        "      - notes/*.md\n",
    )

    after = kyo("docs", "check", "--json")
    assert after.json["state"] == "problems"
    assert after.json["checked"] is True
    assert [p["doc_path"] for p in after.json["problems"]] == ["notes/architecture.md"]


def test_the_clean_state_says_what_it_scanned(kyo, project: Path) -> None:
    """A pass carries its evidence too, which is the rule :class:`Staleness` already
    holds itself to: a result that shows failures with detail and passes with one word
    invites the reader to trust the pass more than it has earned."""
    _write_doc(project, "README.md", "## Nothing claimed here.\n")

    result = kyo("docs", "check", "--json")

    assert result.code == 0, result
    assert result.json["state"] == "clean"
    assert result.json["checked"] is True
    assert result.json["scanned"] == ["README.md"]
    assert "*.md" in result.json["says"]


def test_only_two_of_the_three_states_mean_the_question_was_answered() -> None:
    """The whitelist, and the direction it fails in. Every state added to this vocabulary
    is UNANSWERED until someone decides otherwise — the same rule
    :func:`staleness_answered` states, because the dangerous failure is an absent flag."""
    from kyosei.docs_check import DOCS_STATES, docs_check_answered

    assert DOCS_STATES == ("clean", "problems", "no-documents")
    assert docs_check_answered("clean") and docs_check_answered("problems")
    assert not docs_check_answered("no-documents")
    assert not docs_check_answered("something-invented-later")


def test_a_governs_example_inside_a_code_fence_is_not_a_claim(kyo, project: Path) -> None:
    """KYO-129 widened discovery to root-level ``*.md``, and the file that lands in first
    is the README — the one markdown file in a project guaranteed to contain examples of
    the format. This repository's own README quotes a ``governs:`` line inside a fence to
    explain what one is. Without fence-awareness that example becomes a claim the build
    enforces, and every consumer's README does the same the day they upgrade: a second
    bug shipped behind the fix for the first.

    A ``# comment`` in a fence was already being read as a heading before this, in
    ``docs/`` where discovery already reached; the same fix covers both.
    """
    _write_doc(
        project,
        "README.md",
        "# The docs format\n\n"
        "A `governs:` line names a path that must exist:\n\n"
        "```markdown\n"
        "## How dispatch briefs are generated\n\n"
        "governs: src/does_not_exist.py\n"
        "status: current\n"
        "```\n\n"
        "That is the whole format.\n",
    )

    result = kyo("docs", "check", "--json")

    assert result.code == 0, result
    assert result.json["problems"] == []
    assert result.json["scanned"] == ["README.md"]

    from kyosei.docs_targets import documents

    [document] = documents(project)
    assert document.governs == []
    # The quoted heading is not a section of the document either.
    assert [section.heading for section in document.sections] == [
        "(preamble)",
        "The docs format",
    ]
    # The fenced lines are still part of the section's body, so a prose filter over the
    # document still finds text in its examples. What is filtered is what the metadata
    # parser is shown, not what the section is.
    assert "governs: src/does_not_exist.py" in document.body()


def test_a_document_path_may_not_climb_out_of_the_project(kyo, project: Path) -> None:
    """These globs walk the filesystem, unlike ``scopes.yml``'s, which only ever match a
    path a record already declared. So a pattern that would read outside the repository
    is refused when the file is read, naming it — rather than quietly enumerating
    somebody's home directory because a config line had a ``..`` in it."""
    _write_documents_yml(
        project,
        "documents:\n"
        "  escape:\n"
        "    summary: Somewhere else entirely.\n"
        "    globs:\n"
        "      - ../../*.md\n",
    )

    result = kyo("docs", "check")

    assert result.code != 0, result
    assert "documents.yml" in result.err
    assert "'../../*.md'" in result.err


def test_the_check_and_the_query_source_enumerate_one_set(kyo, project: Path) -> None:
    """KYO-129.c4, held by construction rather than by agreement: both callers resolve
    the same :class:`DocumentSet` and walk it with the same enumerator, so a document one
    can see and the other cannot is not a state this module can express."""
    from kyosei.docs_check import check_docs
    from kyosei.docs_parse import document_set
    from kyosei.docs_targets import documents

    _write_doc(project, "README.md", "## Root\n\ngoverns: src/gone.py\n")
    _write_doc(project, "docs/design.md", "## Design\n\ngoverns: src/gone.py\n")

    resolved = document_set(project / "kyosei")
    checked = check_docs(project, documents_in=resolved).scanned
    queried = tuple(document.path for document in documents(project, documents_in=resolved))

    assert checked == queried == ("README.md", "docs/design.md")


# ------------------------------------------------------------------------ KYO-139
# THE DOCUMENTS THEMSELVES, on the command every project can already run.
#
# The KYO-109 query source is reachable only through a Base declaring `source: docs`,
# and a reader of the governance layer cannot require that a project happen to have
# declared one — this project has not. So `kyo docs check --json` carries the rows, and
# these checks hold them to the query source's own answers rather than to a second
# opinion about the same files.


def test_docs_check_json_carries_the_documents_it_scanned(kyo, project: Path) -> None:
    """KYO-139.c1. A reader of this payload has the documents, not just a verdict."""
    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "# Widgets\n\n## Widget creation\n\ngoverns: src/widget.py\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    [row] = result.json["documents"]
    assert row["id"] == "docs/PRD.md"
    assert row["title"] == "Widgets"
    assert row["governs"] == ["src/widget.py"]
    assert row["governs_broken"] == []
    assert [section["heading"] for section in row["sections"]] == [
        "(preamble)",
        "Widgets",
        "Widget creation",
    ]
    assert [section["governs"] for section in row["sections"]][2] == ["src/widget.py"]


def test_the_payload_rows_are_the_query_sources_own_rows(kyo, project: Path) -> None:
    """Law 3. `document_row` is the one answer, and both callers are callers of it —
    so the desktop app's docs mode and a `source: docs` Base cannot come to different
    conclusions about whether the same file is stale or what it governs."""
    from kyosei.docs_parse import document_set
    from kyosei.docs_staleness import document_row
    from kyosei.docs_targets import documents

    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(project, "docs/a.md", "## A\n\ngoverns: src/widget.py, src/gone.py\n")
    _write_doc(project, "docs/b.md", "## B\n\nno governs line here.\n")

    payload = kyo("docs", "check", "--json").json["documents"]
    resolved = document_set(project / "kyosei")
    expected = [
        document_row(project, document)
        for document in documents(project, documents_in=resolved)
    ]
    assert [{k: v for k, v in row.items() if k != "sections"} for row in payload] == expected


def test_cannot_tell_reaches_the_payload_as_itself_and_not_as_current(
    kyo, project: Path
) -> None:
    """KYO-139.c3's floor at the CLI. The `project` fixture is not a git repository, so
    git can date nothing in it — the exact evidence-free case. A payload that spelled
    that `current` would let a viewer paint a clean docs tree on no evidence, which is
    what `defaults/bases.yml` says this field exists to prevent."""
    from kyosei.docs_staleness import (
        STALENESS_CANNOT_TELL,
        STALENESS_UNGOVERNED,
        staleness_answered,
    )

    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(project, "docs/governed.md", "## A\n\ngoverns: src/widget.py\n")
    _write_doc(project, "docs/ungoverned.md", "## B\n\nprose only.\n")

    rows = {row["id"]: row for row in kyo("docs", "check", "--json").json["documents"]}
    governed = rows["docs/governed.md"]
    assert governed["staleness"] == STALENESS_CANNOT_TELL
    assert not staleness_answered(governed["staleness"])
    assert governed["staleness_says"]
    assert rows["docs/ungoverned.md"]["staleness"] == STALENESS_UNGOVERNED
    # The two undecided states are distinguishable from each other, not merely from
    # `current`: "nothing relates this to the code" and "git could not answer" are
    # different things for a reader to do something about.
    assert governed["staleness"] != rows["docs/ungoverned.md"]["staleness"]


def test_the_documents_are_carried_even_when_the_check_fails(kyo, project: Path) -> None:
    """A broken `governs:` target is exactly when a reader most wants the tree. The
    rows ride on the failure payload, so a viewer does not go blank on the projects
    that need it."""
    _write_doc(project, "docs/PRD.md", "## Gone\n\ngoverns: src/nope.py\n")
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert result.json["ok"] is False
    [row] = result.json["documents"]
    assert row["governs_broken"] == ["src/nope.py"]


def test_a_project_with_no_documents_carries_an_empty_list_not_a_missing_key(
    kyo,
) -> None:
    """KYO-139.c6's floor at the CLI: `no-documents` and "documents that failed to
    load" must not be spelled the same way. The state says which, and `documents` is
    present and empty rather than absent — a consumer branching on a missing key would
    have to guess."""
    from kyosei.docs_check import DOCS_NO_DOCUMENTS

    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["state"] == DOCS_NO_DOCUMENTS
    assert result.json["documents"] == []


# ------------------------------------------------------------------------ KYO-200
#
# A consumer's 178-section `apps/server/NOTES.md` carried a `governs:` list under every
# one of its headings, generated and validated by that project's own tooling since
# before Kyosei existed — and this parser could not read a single one of them. Three
# differences, each with its own criterion below: the list is a YAML continuation
# rather than a comma-split line (c1), the symbol separator is `:` rather than `#`
# (c2), and the paths are relative to the app the document lives in rather than to the
# repository root (c3). Two more the request did not mention and the file does: a
# quarter of the targets name a symbol and no path at all (c6), and the whole thing has
# to survive contact with the real file (c7).
#
# The fixed point throughout is THEIR format. Nothing here asks them to rewrite it.

#: KYO-200.c4: the block from the consumer's report, HTML comment included, with its
#: names replaced and every structural detail kept. Asserted against rather than
#: described, so the format that motivated this record cannot drift out from under the
#: parser that reads it. Every detail is load-bearing: the two-space list indent, the
#: `see_also: [...]` value on a key this parser still does not know, the `-->`
#: terminator (which begins with a dash and must not be read as a fourth target), and —
#: since KYO-204 — `amended_by: []`/`superseded_by: []`, now read as this parser's own
#: `amended-by`/`superseded-by` rather than left alone, with `[]` the one spelling read
#: as empty.
CONSUMER_NOTES_BLOCK = """\
<!--notes-meta
area: billing
status: current
amended_by: []
superseded_by: []
see_also: [159, 165, 166, 175, 178, 180]
governs:
  - src/app/derive/enrichment.py:reading_carries_over
  - src/app/derive/load.py:CarryForward
  - src/app/scripts/check_v2_cycle.py
-->
"""


def _metadata(text: str) -> dict:
    from kyosei.docs_parse import section_metadata

    return section_metadata(text.splitlines())


# ------------------------------------------------------------ c1: the two list forms


def test_a_bare_governs_key_takes_its_targets_from_the_list_lines_below_it() -> None:
    """KYO-200.c1. `governs:` with an empty value is not "no targets" — it opens a
    YAML list, and each `- ` line under it is one target."""
    metadata = _metadata("governs:\n  - a.py\n  - b.py\n")
    assert metadata["governs"] == ["a.py", "b.py"]


def test_the_list_stops_at_the_first_line_that_is_not_a_list_item() -> None:
    """KYO-200.c1, and the half that matters most: a list that ran to the end of the
    section would swallow every bullet in the prose below it. The `-->` case is the
    real one — the consumer's blocks live inside an HTML comment, so the line after the
    last target begins with a dash."""
    metadata = _metadata("governs:\n  - a.py\n-->\n\n- not a target\n")
    assert metadata["governs"] == ["a.py"]

    stopped_by_prose = _metadata("governs:\n  - a.py\n\nProse.\n\n- a bullet\n")
    assert stopped_by_prose["governs"] == ["a.py"]


def test_the_one_line_comma_form_still_parses_and_may_share_a_section() -> None:
    """KYO-200.c1. The form `kyo docs check` has enforced since KYO-13 is not replaced,
    and a section may write both — accumulation across `governs:` lines was already the
    rule and the second spelling joins it rather than competing with it."""
    assert _metadata("governs: a.py, b.py\n")["governs"] == ["a.py", "b.py"]
    both = _metadata("governs: a.py, b.py\n\ngoverns:\n  - c.py\n  - d.py\n")
    assert both["governs"] == ["a.py", "b.py", "c.py", "d.py"]


def test_a_bare_key_that_is_not_governs_does_not_open_a_list() -> None:
    """KYO-200.c1. Only `governs` is list-valued. A bare `status:` contributes nothing
    and, the part worth a test, does not capture the `- ` lines under it — otherwise a
    section writing `amended_by:` above its governs list would silently attribute those
    targets to the wrong key."""
    metadata = _metadata("status:\n  - a.py\n  - b.py\n")
    assert metadata["governs"] == []
    assert metadata["status"] is None


def test_a_list_form_target_is_checked_like_any_other(kyo, project: Path) -> None:
    """KYO-200.c1 end to end: the list form reaches `kyo docs check`, which fails on a
    list line naming a file that is not there. Before this the line matched nothing at
    all and the section read as governing nothing — a silent pass, which is the failure
    shape this whole module keeps being filed against."""
    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## Widget\n\ngoverns:\n  - src/widget.py\n  - src/gone.py\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert [p["target"] for p in result.json["problems"]] == ["src/gone.py"]


# ---------------------------------------------------------- c2: the two separators


def test_a_colon_separated_target_resolves_exactly_as_a_hash_one(
    kyo, project: Path
) -> None:
    """KYO-200.c2. Same file, same symbol, two spellings, one verdict — checked in the
    direction that fails as well as the one that passes, since a separator that is
    silently ignored would also make the passing case pass."""
    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## Widget\n\ngoverns:\n"
        "  - src/widget.py:make_widget\n"
        "  - src/widget.py#make_widget\n",
    )
    assert kyo("docs", "check", "--json").code == 0

    _write_doc(project, "docs/PRD.md", "## Widget\n\ngoverns: src/widget.py:nope\n")
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert result.json["problems"][0]["target"] == "src/widget.py:nope"


def test_the_colon_rule_is_stated_and_holds_from_both_sides() -> None:
    """KYO-200.c2. The rule `split_target` states: a `:` separates iff it is not
    followed by `/` and no `/` follows it. One test per side, because a rule with only
    its positive case tested is a rule that has not been distinguished from "split on
    every colon"."""
    from kyosei.docs_parse import split_target

    # Separating: the colon sits in the final path segment.
    assert split_target("adapters/alpha/client.py:fetch") == ("adapters/alpha/client.py", "fetch")
    assert split_target("Makefile:dev-db-up") == ("Makefile", "dev-db-up")
    # Not separating: a colon inside a directory name, and a scheme's colon.
    assert split_target("docs/a:b/thing.md") == ("docs/a:b/thing.md", None)
    assert split_target("https://example.com/x.py") == ("https://example.com/x.py", None)
    # `#` still wins wherever both appear, being the unambiguous one.
    assert split_target("a:b.py#sym") == ("a:b.py", "sym")

    assert "not immediately followed by" in (split_target.__doc__ or "")
    assert "no ``/`` appears after it" in (split_target.__doc__ or "")


def test_one_splitting_function_serves_the_check_staleness_and_obligations(
    project: Path,
) -> None:
    """KYO-200.c2's real content: not that three call sites agree today, but that there
    is one of them. Staleness dates a colon target's path half, and obligation raising
    matches on it, because both go through `split_target` rather than partitioning for
    themselves."""
    from kyosei import docs_obligations, docs_staleness, docs_targets

    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    doc = _write_doc(
        project, "docs/PRD.md", "## Widget\n\ngoverns:\n  - src/widget.py:make_widget\n"
    )
    [document] = docs_targets.documents(project)
    assert document.governs == ["src/widget.py:make_widget"]
    assert document.resolved[0].paths == ("src/widget.py",)

    dates = {"docs/PRD.md": "2020-01-01", "src/widget.py": "2020-06-01"}
    stale = docs_staleness.governs_staleness(
        project, "docs/PRD.md", document.resolved, commit_at=lambda _r, p: dates.get(p)
    )
    assert stale.state == docs_staleness.STALENESS_STALE
    assert stale.changed == ("src/widget.py",)

    [obligation] = docs_obligations.obligations_for_changed_files(project, ["src/widget.py"])
    assert obligation.targets == ("src/widget.py:make_widget",)
    assert obligation.changed == ("src/widget.py",)
    assert doc.exists()


# ------------------------------------------------------------- c3: the path bases


def test_a_document_set_may_declare_one_base_or_a_list_of_them(
    kyo, project: Path
) -> None:
    """KYO-200.c3. A scalar and a list are both accepted — the common case is one base,
    and forcing a one-element list on everyone so the three-base case has a spelling is
    a config file charging everybody for a minority."""
    _write_doc(project, "apps/server/src/thing.py", "def go():\n    return 1\n")
    _write_doc(project, "apps/server/NOTES.md", "## Thing\n\ngoverns:\n  - src/thing.py:go\n")

    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n    paths_relative_to: apps/server\n",
    )
    assert kyo("docs", "check", "--json").code == 0

    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n"
        "    paths_relative_to: [apps/nowhere, apps/server]\n",
    )
    assert kyo("docs", "check", "--json").code == 0


def test_a_target_resolves_under_the_first_base_that_holds_it(project: Path) -> None:
    """KYO-200.c3. Order is the whole content of a base *list*, so the test is that the
    earlier base wins when both hold the file — not merely that a later one is tried."""
    from kyosei import docs_targets

    _write_doc(project, "first/thing.py", "def go():\n    return 1\n")
    _write_doc(project, "second/thing.py", "def go():\n    return 1\n")
    first = docs_targets.resolve_target(project, "thing.py:go", ("first", "second"))
    second = docs_targets.resolve_target(project, "thing.py:go", ("second", "first"))
    assert first.paths == ("first/thing.py",)
    assert second.paths == ("second/thing.py",)


def test_every_consumer_downstream_of_the_parser_sees_a_root_relative_path(
    project: Path,
) -> None:
    """KYO-200.c3, and the reason resolution happens at parse time rather than in each
    consumer: staleness dates a path, obligation raising intersects one against git's
    changed-file list, and both speak root-relative. A base-relative path handed to
    either would name nothing and look exactly like a file that had not changed."""
    from kyosei import docs_obligations, docs_parse, docs_targets

    _write_doc(project, "apps/server/src/thing.py", "def go():\n    return 1\n")
    _write_doc(project, "apps/server/NOTES.md", "## Thing\n\ngoverns:\n  - src/thing.py:go\n")
    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n    paths_relative_to: apps/server\n",
    )
    documents_in = docs_parse.document_set(project / "kyosei")
    assert documents_in.paths_relative_to == ("apps/server",)

    [document] = docs_targets.documents(project, documents_in=documents_in)
    assert document.governs == ["src/thing.py:go"]  # rendered as written
    assert document.resolved[0].paths == ("apps/server/src/thing.py",)  # resolved

    obligations = docs_obligations.obligations_for_changed_files(
        project, ["apps/server/src/thing.py"], documents_in=documents_in
    )
    assert [o.changed for o in obligations] == [("apps/server/src/thing.py",)]
    assert docs_obligations.governed_symbol_paths(project, documents_in=documents_in) == [
        "apps/server/src/thing.py"
    ]


def test_the_failure_message_names_every_base_it_tried(kyo, project: Path) -> None:
    """KYO-200.c3. A resolver that searched three directories and reports only "no such
    file" hands the reader a fact they cannot act on: the set it searched is the one
    thing they would have to change."""
    _write_doc(project, "apps/server/NOTES.md", "## Thing\n\ngoverns:\n  - src/gone.py\n")
    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n"
        "    paths_relative_to: [apps/server, apps/other, .]\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    reason = result.json["problems"][0]["reason"]
    assert "apps/server" in reason
    assert "apps/other" in reason
    assert "the project root" in reason


def test_a_set_without_the_field_behaves_exactly_as_before(kyo, project: Path) -> None:
    """KYO-200.c3's floor. Every project upgrading into this field is in this state, and
    a resolver that started searching somewhere new for them would be this record
    changing an answer nobody asked it to change."""
    from kyosei import docs_parse

    _write_doc(project, "apps/server/src/thing.py", "def go():\n    return 1\n")
    _write_doc(project, "apps/server/NOTES.md", "## Thing\n\ngoverns:\n  - src/thing.py:go\n")
    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n",
    )
    documents_in = docs_parse.document_set(project / "kyosei")
    assert documents_in.paths_relative_to == ()
    assert documents_in.bases == ("",)
    # `src/thing.py` is a real file under `apps/server` and NOT under the root, so the
    # undeclared project must still fail on it — the base is what changes the answer.
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert result.json["problems"][0]["target"] == "src/thing.py:go"


def test_a_base_may_not_climb_out_of_the_project(kyo, project: Path) -> None:
    """KYO-200.c3, `relative_globs`'s rule applied to the field beside it: a base is
    joined to the code root and handed to the filesystem, so an absolute one or one
    climbing through `..` would make a config file the instruction that reads outside
    the repository, silently."""
    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n    paths_relative_to: ../elsewhere\n",
    )
    result = kyo("docs", "check")
    assert result.code != 0
    assert "paths_relative_to" in result.err
    assert ".." in result.err


# ------------------------------------------- c4: the consumer's block, names replaced


def test_the_consumer_notes_block_parses_to_its_three_targets() -> None:
    """KYO-200.c4. The block as the consumer sent it, HTML comment and all. The
    parser reads inside HTML comments today — `_fenced` skips code fences only — and
    that is deliberate and load-bearing here, not an oversight to be "fixed"."""
    metadata = _metadata(CONSUMER_NOTES_BLOCK)
    assert metadata["governs"] == [
        "src/app/derive/enrichment.py:reading_carries_over",
        "src/app/derive/load.py:CarryForward",
        "src/app/scripts/check_v2_cycle.py",
    ]
    assert metadata["status"] == "current"
    # KYO-204: `amended_by`/`superseded_by` (underscore) are read as the same field as
    # this parser's own `amended-by`/`superseded-by` (hyphen), not a different key left
    # alone — the consumer writes `[]` for "nothing", which this parser stores as the
    # literal flattened text rather than as None (it is not absent; it is present and
    # empty, and `section_is_dead` is what tells the two apart).
    assert metadata["amended-by"] == "[]"
    assert metadata["superseded-by"] == "[]"


def test_the_consumer_notes_block_is_read_the_same_way_inside_a_real_document(
    kyo, project: Path
) -> None:
    """KYO-200.c4 through the command rather than the function: the block under a real
    `## NN.` heading, in a file `kyo docs check` enumerates, with its bases declared."""
    for rel in (
        "apps/server/src/app/derive/enrichment.py",
        "apps/server/src/app/derive/load.py",
        "apps/server/src/app/scripts/check_v2_cycle.py",
    ):
        _write_doc(
            project,
            rel,
            "def reading_carries_over():\n    return 1\n\n\nclass CarryForward:\n    pass\n",
        )
    _write_doc(
        project,
        "apps/server/NOTES.md",
        f"## 158. Invoice carry-over\n\n{CONSUMER_NOTES_BLOCK}\nProse.\n",
    )
    _write_documents_yml(
        project,
        "documents:\n  app:\n    summary: the server's notes\n"
        "    globs: [apps/server/NOTES.md]\n    paths_relative_to: apps/server\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    [row] = result.json["documents"]
    assert len(row["governs"]) == 3
    assert row["governs_broken"] == []
    [section] = [s for s in row["sections"] if s["level"] == 2]
    assert section["status"] == "current"


# --------------------------------------------------- c5: the format, stated by the tool


def test_the_module_docstring_states_both_forms_both_separators_and_the_base() -> None:
    """KYO-200.c5. A consumer should learn the accepted shapes from the tool, not from a
    message thread — which is literally how this record's three asks arrived."""
    from kyosei import docs_check

    doc = docs_check.__doc__ or ""
    assert "comma-separated" in doc
    assert "- src/kyosei/brief.py" in doc  # the list form, shown
    assert "path#symbol" in doc and "path:symbol" in doc
    assert "paths_relative_to" in doc


def test_the_docs_check_failure_text_states_the_accepted_shapes(
    kyo, project: Path
) -> None:
    """KYO-200.c5. On the failure path `kyo docs check` prints the per-problem reasons
    and nothing else, so the format has to ride on the reason — the moment a consumer
    who wrote an unaccepted shape is actually reading."""
    _write_doc(project, "docs/PRD.md", "## Gone\n\ngoverns: src/nope.py\n")
    result = kyo("docs", "check")
    assert result.code == 3
    assert "path:symbol" in result.err
    assert "- target" in result.err
    assert "paths_relative_to" in result.err


# ------------------------------------------------------------ c6: the symbol shapes


def test_a_dotted_module_symbol_resolves_as_the_module_file_plus_the_symbol(
    project: Path,
) -> None:
    """KYO-200.c6. `global_queue.brain.step` is the module `global_queue/brain.py` and
    the symbol `step` — the consumer's own checker's rule, matched rather than
    reinvented so the two tools classify one string one way."""
    from kyosei import docs_targets

    _write_doc(project, "src/global_queue/brain.py", "def step():\n    return 1\n")
    target = docs_targets.resolve_target(project, "global_queue.brain.step", ("src",))
    assert target.resolved
    assert target.shape == docs_targets.SHAPE_PATH_SYMBOL
    assert target.paths == ("src/global_queue/brain.py",)
    assert target.raw == "global_queue.brain.step"  # rendered as written


def test_a_one_dot_model_attribute_is_a_symbol_and_not_a_module_path(
    project: Path,
) -> None:
    """KYO-200.c6, and the distinction that decides whether the real file goes green:
    `OrderSnapshot.total_cents` is a `Class.attribute` pair, not a module, and
    reading it as one would look for a file `OrderSnapshot.py` no project has. The
    two-dot minimum is the consumer's checker's rule (`_dotted_module_symbol`)."""
    from kyosei import docs_targets

    _write_doc(project, "src/models.py", "class OrderSnapshot:\n    total_cents = 0\n")
    # The trap, planted deliberately: a file that a one-dot module reading WOULD find.
    # Without the two-dot minimum this target resolves here instead, silently governing
    # the wrong file — which is why the minimum needs a test that can tell them apart
    # rather than one that merely watches the fallback rescue it.
    _write_doc(project, "src/OrderSnapshot.py", "total_cents = 0\n")
    target = docs_targets.resolve_target(project, "OrderSnapshot.total_cents", ("src",))
    assert target.resolved
    assert target.shape == docs_targets.SHAPE_SYMBOL
    assert target.paths == ("src/models.py",)
    # And the three-segment form on the same tree still IS read as a module path, so
    # the minimum is a boundary rather than a blanket refusal to read dotted targets.
    _write_doc(project, "src/deep/models.py", "class ScrapeRun:\n    pass\n")
    dotted = docs_targets.resolve_target(project, "deep.models.ScrapeRun", ("src",))
    assert dotted.shape == docs_targets.SHAPE_PATH_SYMBOL
    assert dotted.paths == ("src/deep/models.py",)


def test_an_undotted_identifier_resolves_when_some_file_carries_it_as_a_word(
    project: Path,
) -> None:
    """KYO-200.c6. `ScrapeRun` names no file, so the only honest resolution is to look
    for it — whole-word, so a target is not satisfied by a substring of a longer name."""
    from kyosei import docs_targets

    _write_doc(project, "src/models.py", "class ScrapeRun:\n    pass\n")
    _write_doc(project, "src/other.py", "class ScrapeRunner:\n    pass\n")
    found = docs_targets.resolve_target(project, "ScrapeRun", ("src",))
    assert found.paths == ("src/models.py",)
    missing = docs_targets.resolve_target(project, "NoSuchThing", ("src",))
    assert not missing.resolved
    assert "carries every word of it" in (missing.reason or "")


def test_a_dotted_symbol_is_one_question_whether_or_not_a_path_precedes_it(
    project: Path,
) -> None:
    """KYO-200.c2 in the direction nobody thinks to check, and c6's rule reaching the
    other side of a separator. `adapters/base.py:ChainAdapter.prepare` names a method, and
    a class and its method are declared on different lines — so requiring the literal
    `ChainAdapter.prepare` fails a target naming code that is plainly there. Measured on
    the real document: 35 of its targets, every one of them live."""
    from kyosei import docs_targets

    _write_doc(
        project, "src/base.py", "class ChainAdapter:\n    def prepare(self):\n        return 1\n"
    )
    after_path = docs_targets.resolve_target(project, "base.py:ChainAdapter.prepare", ("src",))
    bare = docs_targets.resolve_target(project, "ChainAdapter.prepare", ("src",))
    assert after_path.resolved and after_path.paths == ("src/base.py",)
    assert bare.resolved and bare.paths == ("src/base.py",)
    # And it is not a licence to match anything: a symbol whose words are not all there
    # still fails, on either side of the separator.
    gone = docs_targets.resolve_target(project, "base.py:ChainAdapter.teardown", ("src",))
    gone_bare = docs_targets.resolve_target(project, "ChainAdapter.teardown", ("src",))
    assert not gone.resolved and not gone_bare.resolved


def test_the_symbol_rule_is_stated_in_the_parser() -> None:
    """KYO-200.c6 asks for the rule to be written down where the code is, not only in a
    test — because the next person to meet an unresolvable target reads the function."""
    from kyosei.docs_targets import resolve_target, target_shape

    assert "no path separator and no file extension" in (target_shape.__doc__ or "")
    resolves = " ".join((resolve_target.__doc__ or "").split())
    assert "resolves as the module's file under the bases" in resolves
    assert "at least one searchable file under the bases" in resolves


def test_docs_check_renders_each_symbol_shape_as_what_it_is(kyo, project: Path) -> None:
    """KYO-200.c6. A bare symbol that resolves must not be reported as a broken path,
    and one that does not must say it was a symbol it could not find — the two used to
    be the same message because neither shape existed."""
    _write_doc(project, "src/models.py", "class ScrapeRun:\n    pass\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## Models\n\ngoverns:\n  - ScrapeRun\n  - NoSuchSymbol\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    [problem] = result.json["problems"]
    assert problem["target"] == "NoSuchSymbol"
    assert "a symbol target" in problem["reason"]
    [row] = result.json["documents"]
    assert row["governs_broken"] == ["NoSuchSymbol"]


def test_a_symbol_target_governs_the_files_it_resolved_to(project: Path) -> None:
    """KYO-200.c6's last clause. Staleness and obligation raising treat a bare symbol as
    governing the file(s) it was found in — otherwise the quarter of the real corpus
    that is this shape would be parsed, rendered, and then silently exempt from every
    mechanism that matters."""
    from kyosei import docs_obligations, docs_staleness, docs_targets

    _write_doc(project, "src/models.py", "class ScrapeRun:\n    pass\n")
    _write_doc(project, "docs/PRD.md", "## Models\n\ngoverns:\n  - ScrapeRun\n")
    [document] = docs_targets.documents(project)
    assert document.resolved[0].paths == ("src/models.py",)

    [obligation] = docs_obligations.obligations_for_changed_files(project, ["src/models.py"])
    assert obligation.targets == ("ScrapeRun",)
    assert obligation.changed == ("src/models.py",)
    # And the symbol-narrowed fetch `kyo report` drives (`governed_symbol_paths`) asks
    # git for the file the symbol resolved to — without which a bare-symbol target is
    # parsed and rendered and then never given a diff to be precise about.
    assert docs_obligations.governed_symbol_paths(project) == ["src/models.py"]

    dates = {"docs/PRD.md": "2020-01-01", "src/models.py": "2020-06-01"}
    stale = docs_staleness.governs_staleness(
        project, "docs/PRD.md", document.resolved, commit_at=lambda _r, p: dates.get(p)
    )
    assert stale.state == docs_staleness.STALENESS_STALE
    assert stale.changed == ("src/models.py",)


# ------------------------------------------------------------------------ KYO-204
#
# A section whose `status` is `historical`/`superseded`, or whose `superseded-by`
# names something, keeps its `governs:` list as history — pruning it to satisfy the
# checker destroys the record of what it used to govern; keeping it and resolving it
# fails the build forever once the code it names is gone. `amended` is a different,
# narrower question (KYO-98's "pairs agree", built by KYO-499) and stays current.


def test_amended_by_and_superseded_by_read_the_underscore_spelling_as_the_hyphen_key() -> None:
    """KYO-204.c1. The consumer writes `amended_by:`/`superseded_by:` (underscore);
    this parser's own keys are `amended-by`/`superseded-by` (hyphen). Both spellings
    land on the one field, on both the valued and the bare-key path."""
    valued = _metadata("amended_by: Widget v2\nsuperseded_by: 177\n")
    assert valued["amended-by"] == "Widget v2"
    assert valued["superseded-by"] == "177"

    # The bare-key path: `governs:` is the only key list-valued lines fall under, and
    # an aliased bare key must not open a list either.
    bare = _metadata("superseded_by:\n  - not/a/target.py\n")
    assert bare["superseded-by"] is None
    assert bare["governs"] == []


def test_a_dead_status_or_a_nonempty_superseded_by_marks_a_section_dead() -> None:
    """KYO-204.c1's classification rule, direct: `historical`/`superseded` status, or a
    non-empty `superseded-by` (either spelling), is dead; `amended` and a populated
    `amended-by` are current. `[]`, whitespace-insensitive, is the one spelling
    `superseded-by` reads as empty."""
    from kyosei.docs_parse import section_is_dead

    assert section_is_dead({"status": "historical", "superseded-by": None}) is True
    # Case-insensitive:
    assert section_is_dead({"status": "Superseded", "superseded-by": None}) is True
    assert section_is_dead({"status": "current", "superseded-by": "177"}) is True
    assert section_is_dead({"status": "current", "superseded-by": " [177] "}) is True
    assert section_is_dead({"status": "current", "superseded-by": "[]"}) is False
    assert section_is_dead({"status": "current", "superseded-by": None}) is False
    assert section_is_dead({"status": "amended", "amended-by": "Widget v2"}) is False
    assert section_is_dead({"status": None, "superseded-by": None}) is False


#: KYO-204.c4: one section of each kind -- current, amended, historical, superseded by
#: status, and superseded by key with the underscore spelling -- each governing a target
#: that does not exist. Only the first two (current, amended) are checked; the other
#: three are dead and skipped. A fixed target name across all five is what makes this
#: fixture prove the SKIP rather than a coincidence about which files happen to exist.
_DEAD_SECTIONS_FIXTURE = """\
## Current

status: current
governs: does/not/exist.py

## Amended

status: amended
amended-by: Current v2
governs: does/not/exist.py

## Historical

status: historical
governs: does/not/exist.py

## Superseded by status

status: superseded
governs: does/not/exist.py

## Superseded by key (underscore spelling)

superseded_by: 177
governs: does/not/exist.py
"""


def test_a_dead_section_is_skipped_and_an_amended_one_is_not(kyo, project: Path) -> None:
    """KYO-204.c2/c4. Five sections, one target each, all naming a file that does not
    exist. Only `Current` and `Amended` are live -- `kyo docs check` fails on exactly
    those two and skips the other three, counting and naming why."""
    _write_doc(project, "docs/PRD.md", _DEAD_SECTIONS_FIXTURE)
    result = kyo("docs", "check", "--json")

    assert result.code == 3
    problem_sections = sorted(p["section"] for p in result.json["problems"])
    assert problem_sections == ["Amended", "Current"]
    assert "3 dead section(s) skipped" in result.json["says"]


def test_the_clean_state_also_reports_zero_or_more_dead_sections_skipped(
    kyo, project: Path
) -> None:
    """KYO-204.c2. The skip count is reported in the CLEAN state too, and is honestly
    zero when nothing is dead -- the same "say it even when it's zero" rule `says`
    already holds itself to for the rest of its fields."""
    _write_doc(project, "docs/PRD.md", "## A\n\ngoverns: src/kept.py\n")
    _write_doc(project, "src/kept.py", "x = 1\n")
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["state"] == "clean"
    assert "0 dead section(s) skipped" in result.json["says"]


def test_a_dead_sections_target_is_never_even_resolved(project: Path) -> None:
    """KYO-204.c2's stronger claim: not merely excused from failing, but never handed to
    the resolver at all. `Section.resolved` is empty for the dead section even though a
    resolver was supplied and the target does not exist -- if it HAD been resolved, it
    would have produced a GovernsTarget with a failure reason, not an empty tuple."""
    from kyosei.docs_parse import DEFAULT, _sections
    from kyosei.docs_targets import target_resolver

    resolve = target_resolver(project, DEFAULT)
    sections = _sections(_DEAD_SECTIONS_FIXTURE, resolve)
    by_heading = {s.heading: s for s in sections}
    assert by_heading["Historical"].dead is True
    assert by_heading["Historical"].resolved == ()
    assert by_heading["Current"].dead is False
    assert len(by_heading["Current"].resolved) == 1  # resolved (and failed) as normal


def test_obligation_raising_and_staleness_skip_a_dead_section_the_same_way_docs_check_does(
    project: Path,
) -> None:
    """KYO-204.c3. A dispatch that changes a file only a `historical` section governs
    raises no obligation for it, and a document whose only governing section is dead is
    UNGOVERNED for staleness -- not falsely broken, not falsely stale -- agreeing with
    `kyo docs check`, which also does not flag it (mechanism-deletion: reverting
    `document_row`'s fallback from `document.live_governs` to `document.governs`, or
    dropping `_governs_obligations`'s `if section.dead: continue`, turns this red)."""
    from kyosei import docs_obligations, docs_staleness, docs_targets

    _write_doc(project, "src/gone.py", "x = 1\n")  # exists, so obligations has a real diff
    _write_doc(
        project,
        "docs/PRD.md",
        "## Retired\n\nstatus: historical\ngoverns: src/gone.py\n",
    )
    [document] = docs_targets.documents(project)
    assert document.sections[1].dead is True  # preamble is [0]

    # Obligation raising: changing the file the dead section governs raises nothing.
    obligations = docs_obligations.obligations_for_changed_files(project, ["src/gone.py"])
    assert obligations == []

    # Staleness: the document has no LIVE governs target, so it is UNGOVERNED, not
    # CANNOT_TELL over a raw fallback that leaked the dead section's target back in.
    row = docs_staleness.document_row(
        project, document, commit_at=lambda _r, p: {"docs/PRD.md": "2020-01-01"}.get(p)
    )
    assert row["staleness"] == docs_staleness.STALENESS_UNGOVERNED
    assert row["governs_broken"] == []

    # A CURRENT section governing the same file agrees in the opposite direction: it
    # DOES raise, which is what proves the dead section's absence above is the skip
    # rule and not an accident of `src/gone.py` being unreachable some other way.
    _write_doc(
        project,
        "docs/PRD2.md",
        "## Live\n\ngoverns: src/gone.py\n",
    )
    [live_document] = [d for d in docs_targets.documents(project) if d.path == "docs/PRD2.md"]
    live_obligations = docs_obligations.obligations_for_changed_files(project, ["src/gone.py"])
    assert [o.doc_path for o in live_obligations] == ["docs/PRD2.md"]
    live_row = docs_staleness.document_row(
        project, live_document, commit_at=lambda _r, p: {"docs/PRD2.md": "2020-01-01"}.get(p)
    )
    assert live_row["staleness"] != docs_staleness.STALENESS_UNGOVERNED


# ------------------------------------------------------------------------ KYO-202
#
# The consumer's own drift checker (tools/check_governs_drift.py) tolerates a single
# `{a,b,c}` brace group (`expand_governs_entry`) and, separately, this
# project's own addition: a shell glob resolving against real files. Five of their real
# NOTES.md targets used one shape or the other and were unresolved until this record.


def test_a_single_brace_group_expands_to_one_alternative_per_comma_item() -> None:
    """KYO-202.c1. `_brace_expand`, direct: one group, N alternatives, everything else
    about the string untouched."""
    from kyosei.docs_targets import _brace_expand

    alts, refusal = _brace_expand("adapters/{a,b,c}/normalize.py")
    assert refusal is None
    assert alts == [
        "adapters/a/normalize.py",
        "adapters/b/normalize.py",
        "adapters/c/normalize.py",
    ]

    # No group at all: unchanged, one-element list.
    assert _brace_expand("adapters/gamma/normalize.py") == (
        ["adapters/gamma/normalize.py"], None,
    )

    # A single group that is not a well-formed comma list -- fewer than two
    # alternatives, or an alternative outside the plain path-token charset -- is left
    # as literal text rather than guessed at.
    assert _brace_expand("adapters/{onlyone}/normalize.py") == (
        ["adapters/{onlyone}/normalize.py"], None,
    )
    assert _brace_expand("adapters/{a, b}/normalize.py") == (
        ["adapters/{a, b}/normalize.py"], None,  # a space inside the group
    )


def test_two_brace_groups_refuse_by_name_rather_than_guess(kyo, project: Path) -> None:
    """KYO-202.c3. A target with two brace groups is refused, naming the shape, not
    silently expanded into a cross product or a guess at which group was meant."""
    from kyosei.docs_targets import _brace_expand

    alts, refusal = _brace_expand("adapters/{a,b}/{x,y}/normalize.py")
    assert alts == []
    assert refusal is not None
    assert "at most one" in refusal and "2" in refusal

    _write_doc(
        project,
        "docs/PRD.md",
        "## Two groups\n\ngoverns:\n  - adapters/{a,b}/{x,y}/normalize.py\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    [problem] = result.json["problems"]
    assert "at most one" in problem["reason"]


def test_a_brace_group_resolves_when_every_alternative_exists(kyo, project: Path) -> None:
    """KYO-202.c1/c2. Every one of the consumer's real 8-alternative adapter lines,
    reproduced small: all files present, all governed."""
    for chain in ("gamma", "alpha", "beta"):
        _write_doc(project, f"adapters/{chain}/normalize.py", "def normalize():\n    pass\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## Adapters\n\ngoverns:\n  - adapters/{gamma,alpha,beta}/normalize.py\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    [row] = result.json["documents"]
    assert row["governs_broken"] == []

    from kyosei import docs_targets

    [document] = docs_targets.documents(project)
    [target] = document.resolved
    assert target.resolved
    assert sorted(target.paths) == [
        "adapters/alpha/normalize.py",
        "adapters/beta/normalize.py",
        "adapters/gamma/normalize.py",
    ]


def test_a_brace_group_fails_when_any_single_alternative_is_missing(
    kyo, project: Path
) -> None:
    """KYO-202.c1: expansion happens BEFORE resolution, so each alternative is an
    ordinary path target afterwards -- if an adapter file was deleted, that is real drift
    and this must fail exactly as a normal missing path does, naming which one."""
    _write_doc(project, "adapters/gamma/normalize.py", "def normalize():\n    pass\n")
    # alpha/normalize.py is deliberately absent.
    _write_doc(
        project,
        "docs/PRD.md",
        "## Adapters\n\ngoverns:\n  - adapters/{gamma,alpha}/normalize.py\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    [problem] = result.json["problems"]
    assert "adapters/alpha/normalize.py" in problem["reason"]
    assert "no such file" in problem["reason"]


def test_a_glob_resolves_when_at_least_one_file_matches_and_governs_every_match(
    kyo, project: Path
) -> None:
    """KYO-202.c1. `deploy/run_*.sh`, the record's own example: at least one match
    resolves it, and every match is governed, not only the first."""
    _write_doc(project, "deploy/run_migrate.sh", "#!/bin/sh\n")
    _write_doc(project, "deploy/run_backup.sh", "#!/bin/sh\n")
    _write_doc(project, "deploy/other.sh", "#!/bin/sh\n")  # must NOT be swept in
    _write_doc(project, "docs/PRD.md", "## Deploy\n\ngoverns: deploy/run_*.sh\n")

    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    [row] = result.json["documents"]
    assert row["governs_broken"] == []

    from kyosei import docs_targets

    [document] = docs_targets.documents(project)
    [target] = document.resolved
    assert sorted(target.paths) == ["deploy/run_backup.sh", "deploy/run_migrate.sh"]


def test_a_glob_matching_nothing_refuses_by_name_rather_than_resolving_to_nothing(
    kyo, project: Path
) -> None:
    """KYO-202.c3. A glob with zero matches is a real problem (the shorthand names
    something that used to exist, or a typo) and must fail loudly, not resolve to an
    empty, silently-satisfied governs target."""
    _write_doc(project, "docs/PRD.md", "## Deploy\n\ngoverns: deploy/run_*.sh\n")
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    [problem] = result.json["problems"]
    assert problem["target"] == "deploy/run_*.sh"
    assert "shell-glob" in problem["reason"] and "no file" in problem["reason"]


def test_a_glob_resolves_under_the_first_base_that_has_any_match(project: Path) -> None:
    """KYO-202.c1, the base-list rule already established for a plain path (c3 of
    KYO-200) applied to a glob: the first base with any match wins, not a union across
    every declared base."""
    from kyosei.docs_targets import resolve_target

    _write_doc(project, "apps/server/deploy/run_migrate.sh", "#!/bin/sh\n")
    _write_doc(project, "deploy/run_backup.sh", "#!/bin/sh\n")  # under the root base too

    target = resolve_target(
        project, "deploy/run_*.sh", bases=["apps/server", ""]
    )
    assert target.resolved
    assert target.paths == ("apps/server/deploy/run_migrate.sh",)


def test_glob_and_brace_shorthand_are_documented_in_the_accepted_shapes(
    kyo, project: Path
) -> None:
    """KYO-200.c5's own rule extended to KYO-202's two new shapes: a consumer who
    writes one of these sees the accepted format from the tool, in the same failure
    text every other shape's rejection already carries."""
    from kyosei import docs_check

    assert "{a,b,c}" in docs_check.GOVERNS_FORMAT
    assert "glob" in docs_check.GOVERNS_FORMAT

    _write_doc(project, "docs/PRD.md", "## Gone\n\ngoverns: src/nope.py\n")
    result = kyo("docs", "check")
    assert result.code == 3
    assert "{a,b,c}" in result.err and "glob" in result.err


def test_kyo202_c2_measured_on_the_real_consumer_file(project: Path) -> None:
    """KYO-202.c2. A small, self-contained reproduction of the five real shapes the
    consumer's apps/server/NOTES.md carries (measured directly against the real file in
    the report's evidence, not here -- this project must not read outside its own
    worktree in a committed test). All five must resolve once expansion/glob-matching
    is in place; each is refused (not silently accepted) if the underlying files are
    missing, proving this is a real check and not a rubber stamp."""
    from kyosei.docs_targets import resolve_target

    bases = ["apps/server/src/app", "apps/server", "."]
    for chain in (
        "gamma", "alpha", "beta", "delta", "epsilon", "zeta",
        "eta", "theta",
    ):
        _write_doc(
            project,
            f"apps/server/src/app/adapters/{chain}/normalize.py",
            "def normalize():\n    pass\n",
        )
    for chain in ("iota", "kappa", "lambda", "mu"):
        _write_doc(
            project,
            f"apps/server/src/app/adapters/{chain}/normalize.py",
            "def normalize():\n    pass\n",
        )
    _write_doc(project, "deploy/run_backfill.sh", "#!/bin/sh\n")

    five_real_shapes = [
        "deploy/run_*.sh",
        "adapters/{gamma,alpha,beta,delta,epsilon,zeta,eta,theta}/normalize.py",
        "adapters/{iota,kappa,lambda,mu}/normalize.py",
    ]
    for raw in five_real_shapes:
        target = resolve_target(project, raw, bases=bases)
        assert target.resolved, (raw, target.reason)


# ------------- KYO-351: a `#symbol` the instrument can never region, said out loud


_SCSS_SOURCE = ".pane-left {\n  .handle { width: var(--pane-width); }\n}\n"


def test_docs_check_notices_a_symbol_on_a_file_type_it_cannot_region(
    kyo, project: Path
) -> None:
    """KYO-351.c2. The declaration is valid — it resolves, it raises its section — and
    what it does not do is narrow anything, because `docs_regions.symbol_regions` returns
    "cannot tell" for every suffix outside `REGION_SUFFIXES` and cannot-tell raises.

    A consumer wrote 101 of these and measured that the obligations raised did not move.
    Nothing had ever said so, in either direction: `kyo docs check` accepted them in
    silence and `kyo report` offered the form unconditionally.

    The file type here is `.scss` and not `.ts` or `.css` because KYO-352 taught the
    finder the brace languages and KYO-363 the data ones: the record's own wording
    ("outside REGION_SUFFIXES") survived both landings, and this test is what proves the
    notice follows the tuple rather than a remembered list. Every file the reporting
    consumer's documents govern is `.ts`/`.tsx`/`.js`/`.mjs`/`.json`/`.css`/`.yml`, and
    after KYO-363 the tuple holds all seven — so the suffix that stands for "a file type
    the instrument cannot region" is now a stylesheet dialect rather than CSS itself.
    `.scss` is outside on purpose: its nesting and `&`-composition mean the text before a
    `{` is not the selector a document would name, which is the one thing `_css_regions`
    matches on.
    """
    from kyosei import docs_regions

    _write_doc(project, "src/styles/panes.scss", _SCSS_SOURCE)
    _write_doc(
        project,
        "docs/PRD.md",
        "## The left pane\n\ngoverns: src/styles/panes.scss#pane-left\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")

    # Exit status unchanged, and `ok` unchanged: this is a notice, not a problem.
    assert result.code == 0, result
    assert result.json["ok"] is True
    assert result.json["problems"] == []
    notices = result.json["notices"]
    assert len(notices) == 1, notices
    assert notices[0]["target"] == "src/styles/panes.scss#pane-left"
    assert notices[0]["doc_path"] == "docs/PRD.md"
    assert notices[0]["section"] == "The left pane"
    assert "narrows nothing" in notices[0]["says"]
    # The suffixes that DO narrow are named, and named from the tuple itself.
    assert docs_regions.region_suffixes_says() in notices[0]["says"]
    assert result.json["region_suffixes"] == list(docs_regions.REGION_SUFFIXES)

    text = kyo("docs", "check")
    assert text.code == 0, text
    assert "src/styles/panes.scss#pane-left" in text.out
    assert "exits 0" in text.out


def test_docs_check_says_nothing_about_a_symbol_that_does_narrow(
    kyo, project: Path
) -> None:
    """The other side of c2: a notice printed over every `#symbol` would be the
    unconditional sentence this record was filed about, wearing the other hat."""
    from kyosei import docs_regions

    _write_doc(project, "src/widget.py", "def make_widget():\n    return 1\n")
    _write_doc(project, "docs/one.md", "## Heading\n\nprose\n")
    _write_doc(
        project,
        "docs/PRD.md",
        "## Widget creation\n\ngoverns: src/widget.py#make_widget, docs/one.md#Heading, "
        "src/widget.py\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["notices"] == []
    assert all(
        docs_regions.narrows_by_symbol(f"x{suffix}") for suffix in docs_regions.REGION_SUFFIXES
    )


def test_the_readme_names_exactly_the_suffixes_a_symbol_narrows_for() -> None:
    """KYO-351.c3, and the whole point of the record: the prose and the tuple cannot
    drift. The paragraph is read out of the committed README and its backticked suffix
    list compared to `REGION_SUFFIXES` itself, so adding a file type without saying so
    in the README fails here, and saying so without adding it fails here too.
    """
    import re

    from kyosei import docs_regions
    from tests.reference_pages import DOCS_GOVERNANCE

    # The Layer 3 section left the README for its reference page (KYO-516).
    readme = DOCS_GOVERNANCE.read_text(encoding="utf-8")
    # Whitespace-normalised, because a README paragraph is hard-wrapped and where the
    # wrap falls is not a fact this test has any business asserting.
    paragraphs = [" ".join(block.split()) for block in readme.split("\n\n")]
    said_in = [p for p in paragraphs if "these suffixes and no others:" in p]
    assert len(said_in) == 1, (
        "docs-governance.md must carry exactly one sentence naming the suffixes a "
        "`#symbol` "
        f"narrows for; found {len(said_in)}"
    )
    said = re.findall(r"`(\.[a-z]+)`", said_in[0])
    assert tuple(said) == docs_regions.REGION_SUFFIXES, (said, docs_regions.REGION_SUFFIXES)


# ------ KYO-352: a `#symbol` narrows on the brace languages too, and never silently


_VIEWER_TS = """// The controller for the viewer's two panes.
import { Pane } from "./pane";

export const DEFAULT_WIDTH = 240;

/** Open the left pane. */
export function showLeft(pane: Pane): void {
  pane.open(DEFAULT_WIDTH);
}

export class ViewerController {
  private pane: Pane;

  showRight(): void {
    this.pane.open();
  }

  hide(): void {
    this.pane.close();
  }
}
"""


def _change(text: str | None, lines: set[int], after: str | None = "same") -> object:
    from kyosei import docs_regions

    return docs_regions.FileChange(
        "src/main/ViewerController.ts",
        text,
        text if after == "same" else after,
        frozenset(lines),
        frozenset(lines),
    )


def test_a_typescript_symbol_raises_only_when_its_own_lines_change() -> None:
    """KYO-352.c1, over the three shapes a TypeScript document actually governs: a
    top-level function, a class method and a top-level const.

    The line numbers are read off `_VIEWER_TS` above: `showLeft`'s body is line 8, its
    doc comment line 6; `showRight`'s body is line 15 and `hide`'s declaration line 18;
    `DEFAULT_WIDTH` is line 4.
    """
    from kyosei import docs_regions

    for symbol, inside, outside in (
        ("showLeft", 8, 4),
        ("showRight", 15, 18),
        ("DEFAULT_WIDTH", 4, 8),
    ):
        regions = docs_regions.symbol_regions(
            "src/main/ViewerController.ts", _VIEWER_TS, symbol
        )
        assert regions, (symbol, regions)
        assert docs_regions.symbol_region_changed(_change(_VIEWER_TS, {inside}), symbol) is True, (
            f"a change on line {inside} is inside {symbol}'s own region"
        )
        assert (
            docs_regions.symbol_region_changed(_change(_VIEWER_TS, {outside}), symbol) is False
        ), f"a change on line {outside} is outside {symbol}'s own region"
    # The comment block directly above a declaration belongs to it, for the reason a
    # Python decorator does (`_python_regions`): an edit to it is an edit to the thing.
    assert docs_regions.symbol_region_changed(_change(_VIEWER_TS, {6}), "showLeft") is True


def test_every_case_the_typescript_finder_cannot_decide_still_raises() -> None:
    """KYO-352.c2, and the constraint the whole design answers to (decision 9 in
    docs/design/docs.md): an instrument in front of a BLOCKING gate may suppress on exactly
    one outcome — the region located on both sides with no changed line inside either.
    Everything else raises, so every way this approximation is wrong costs a question a
    controller dismisses in one command rather than a silent false negative."""
    from kyosei import docs_regions

    # 1. The symbol names no declaration this can find.
    assert (
        docs_regions.symbol_regions("a.ts", _VIEWER_TS, "neverDeclaredAnywhere") == ()
    )
    assert (
        docs_regions.symbol_region_changed(_change(_VIEWER_TS, {8}), "neverDeclaredAnywhere")
        is None
    )
    # 2. The file was added by this branch, and 3. deleted by it: one side has no text.
    added = _change(None, {8}, after=_VIEWER_TS)
    deleted = _change(_VIEWER_TS, {8}, after=None)
    assert docs_regions.symbol_region_changed(added, "showLeft") is None
    assert docs_regions.symbol_region_changed(deleted, "showLeft") is None
    # 4. Unbalanced text: the braces say the block the indentation produced is not a
    #    whole block, so this symbol is not placed at all and its section raises.
    truncated = (
        "export function showLeft(): void {\n  if (open) {\n    pane.open();\n"
        "\nexport const OTHER = 1;\n"
    )
    assert docs_regions.symbol_regions("a.ts", truncated, "showLeft") == ()
    assert docs_regions.symbol_region_changed(_change(truncated, {5}), "showLeft") is None
    # The veto is over the regions of the symbol being asked about, and says nothing
    # about the rest of the file: `OTHER`'s own one-line block closes nothing and is
    # placed. Stated rather than left implicit -- "the file is unreadable" and "this
    # symbol is unplaceable" are different claims and only the second is made here.
    assert docs_regions.symbol_regions("a.ts", truncated, "OTHER") == ((5, 5),)
    # 5. Unparseable in the other direction -- a minified file, whose every symbol is on
    #    one line. The region is that line, so every change to the file reaches it.
    minified = "export function showLeft(){pane.open();}export const A=1;\n"
    assert docs_regions.symbol_regions("a.ts", minified, "showLeft") == ((1, 1),)
    assert docs_regions.symbol_region_changed(_change(minified, {1}), "showLeft") is True


_MODIFIED_TS = """export class ViewerController {
  private showLeft(o: Options): void {
    this.left.open(o);
  }

  public async load(): Promise<void> {
    await this.store.read();
  }

  static get instance(): ViewerController {
    return shared;
  }

  protected abstract override render(): void;

  private readonly onClick = () => {
    this.showLeft({});
  };

  async *pages(): AsyncGenerator<Page> {
    yield* this.store.pages();
  }

  set width(value: number) {
    this.left.resize(value);
  }

  wrap<T>(value: T): T {
    return showLeft(value);
  }

  caller(): void {
    await showLeft({});
    new showLeft({});
  }
}
"""


def test_a_class_method_behind_a_modifier_is_a_declaration() -> None:
    """KYO-358.c1 and c2. KYO-352 read a bare `  showLeft(): void {` and nothing with a
    word in front of it, which a TypeScript consumer measured as most of the methods
    their documents describe: `src/main/ViewerController.ts#showLeft` went on raising
    like the whole file after the suffix landed, and 22 of their 89 still-whole targets
    were whole for this reason alone. Every one of these returned `()` before.
    """
    from kyosei import docs_regions

    spans = {
        "showLeft": (2, 5),
        "load": (6, 9),
        "instance": (10, 13),
        "render": (14, 15),
        "onClick": (16, 19),
        "pages": (20, 23),
        "width": (24, 27),
        "wrap": (28, 31),
        # The LAST member: nothing follows it but the class's own closing brace, which
        # is not part of it. Before KYO-358 the block ran through that brace, the brace
        # veto fired, and the last method of every class returned `()`.
        "caller": (32, 35),
    }
    for symbol, span in spans.items():
        assert docs_regions.symbol_regions("a.ts", _MODIFIED_TS, symbol) == (span,), symbol
    # c2: the list of words is closed. `return showLeft(`, `await showLeft(` and
    # `new showLeft(` sit at the start of their lines exactly as a modifier would, and
    # none is a declaration -- `showLeft` has the one region above and no second.
    for line in ("    return showLeft(value);", "    await showLeft({});", "    new showLeft({});"):
        assert line in _MODIFIED_TS
        assert not docs_regions._declares(line, "showLeft"), line
    assert docs_regions.symbol_regions("a.ts", _VIEWER_TS, "hide") == ((18, 20),)
    # And an edit inside `load` no longer reaches the section that governs `showLeft`.
    assert docs_regions.symbol_region_changed(_change(_MODIFIED_TS, {7}), "showLeft") is False
    assert docs_regions.symbol_region_changed(_change(_MODIFIED_TS, {3}), "showLeft") is True


def test_docs_check_notices_a_symbol_it_finds_no_region_for(kyo, project: Path) -> None:
    """KYO-358.c3, KYO-351's notice one case further along. The suffix is one the
    instrument reads, the symbol appears in the file so the target resolves, and nothing
    declares it -- here it is only imported -- so the region finder returns `()` and the
    section raises like the bare path. The consumer asked for exactly this sentence
    after narrowing 77 targets and learning which took from a preview of old merges."""
    _write_doc(project, "src/main/ViewerController.ts", _VIEWER_TS)
    _write_doc(
        project,
        "docs/PRD.md",
        "## Panes\n\ngoverns: src/main/ViewerController.ts#Pane\n\n"
        "## Left\n\ngoverns: src/main/ViewerController.ts#showLeft\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True
    notices = result.json["notices"]
    assert [n["target"] for n in notices] == ["src/main/ViewerController.ts#Pane"], notices
    assert "no region found" in notices[0]["says"]
    assert "like the bare path" in notices[0]["says"]


_INJECTED_TS = """export class SyncController {
  private serialize<T>(fn: () => Promise<T>): Promise<T> {
    return this.chain.then(fn);
  }
  private tight(): void {}
  private readonly unproven = new UnprovenSaves(
    {
      current: () => this.current,
      serialize: (fn) => this.serialize(fn),
      reloadToBase: async () => {
        await this.waitForReady();
      },
      drainQueue: () => this.drainQueue(),
    },
  );

  private load(
    storeId: string,
    force: boolean,
  ): void {
    this.page.load(storeId, force);
  }
}
"""


def test_a_declaration_ends_at_its_next_sibling_not_at_the_next_blank_line() -> None:
    """KYO-360. A consumer's collaborators take the controller's methods as properties
    of an object literal — `serialize: (fn) => this.serialize(fn),` — and that property
    is a second line declaring `serialize`. Its region used to be the whole unbroken run
    of non-blank lines below it (`sweep.region_end`'s rule for hard-wrapped prose): every
    sibling property to the end of the literal, not brace-balanced, and the veto then
    discarded the METHOD's region with it. Both of their promoted targets returned `()`.
    """
    from kyosei import docs_regions

    def regions(symbol: str) -> object:
        return docs_regions.symbol_regions("a.ts", _INJECTED_TS, symbol)

    # c2: the method keeps its region, and the property is its own one line.
    assert regions("serialize") == ((2, 4), (9, 9))
    # c2, KYO-358's left-alone case: a one-line sibling directly below, no blank line.
    assert regions("tight") == ((5, 5),)
    # c1: a property's block is its own block and not its siblings'.
    assert regions("reloadToBase") == ((10, 12),)
    assert regions("drainQueue") == ((13, 13),)
    assert regions("unproven") == ((6, 16),)
    # c3: `): void {` sits at the declaration's own indentation and begins with closing
    # punctuation, so it continues the signature. Ending there would be a two-line
    # region with no brace in it -- balanced, kept, and SILENT about the body.
    assert regions("load") == ((17, 22),)
    assert docs_regions.symbol_region_changed(_change(_INJECTED_TS, {21}), "load") is True
    # An edit to a sibling property no longer reaches the section governing the method.
    assert docs_regions.symbol_region_changed(_change(_INJECTED_TS, {13}), "serialize") is False
    assert docs_regions.symbol_region_changed(_change(_INJECTED_TS, {9}), "serialize") is True
    # c3: a block cut short -- a template literal's text at column 0 ends it early --
    # is missing its closing brace, so the veto still fires and the section raises.
    cut = "class A {\n  render(): string {\n    return `\nplain text\n`;\n  }\n}\n"
    assert docs_regions.symbol_regions("a.ts", cut, "render") == ()


def test_the_two_region_finders_agree_about_where_a_block_ends() -> None:
    """KYO-352.c3's teeth. Two finders remain and they answer different questions, so
    this pins where they are allowed to differ.

    `sweep.symbol_regions` (KYO-300, symbol-aware touches) anchors on EVERY line that
    mentions the symbol and widens each anchor back to the top of its paragraph;
    `docs_check` anchors only on lines that DECLARE it. One shared primitive decides
    where a block ends (`sweep.region_end`), so the permitted difference is exactly the
    anchor rule: the docs gate's region is always contained in the sweep finder's.
    """
    from kyosei import docs_regions, sweep

    lines = _VIEWER_TS.splitlines()
    for symbol in ("showLeft", "showRight", "DEFAULT_WIDTH"):
        gate = docs_regions.symbol_regions("a.ts", _VIEWER_TS, symbol)
        touches = sweep.symbol_regions(_VIEWER_TS, symbol)
        assert gate and touches, (symbol, gate, touches)
        for start, end in gate:
            assert any(
                r.start <= start and end <= r.end for r in touches
            ), f"{symbol}: the docs gate's region {start}-{end} is not inside {touches}"
            # And the shared primitive is the same answer, not a copy of it.
            declaration = next(
                index
                for index, line in enumerate(lines)
                if docs_regions._declares(line, symbol.rsplit(".", 1)[-1])
            )
            assert end == sweep.region_end(lines, declaration)
    # The difference that makes them two finders: DEFAULT_WIDTH is USED inside
    # showLeft, so the touch finder claims that block too and the docs gate does not.
    used_in = sweep.symbol_regions(_VIEWER_TS, "DEFAULT_WIDTH")
    assert len(used_in) == 2, used_in
    assert len(docs_regions.symbol_regions("a.ts", _VIEWER_TS, "DEFAULT_WIDTH")) == 1


def test_the_preview_shows_what_a_declared_symbol_suppressed(kyo, git_project: Path) -> None:
    """KYO-352.c4. A narrowing that is wrong is invisible — nothing raised is nothing
    seen — and the consumer who asked for this feature also warned about it: ~100
    judgment calls about what each section is "about" were written while the check was
    inert, and all of them go live the day a suffix is added. So the count is printed on
    every preview and `--suppressed` names the sections.
    """
    from .test_kyo137_docs_obligations import _branch_editing, _commit, _git, _write

    _write(
        git_project,
        "docs/viewer.md",
        "# The viewer\n\n## How the left pane opens\n\n"
        "governs: src/main/ViewerController.ts#showLeft\n\nProse.\n",
    )
    _write(git_project, "src/main/ViewerController.ts", _VIEWER_TS)
    base = _commit(git_project, "baseline")
    _branch_editing(
        git_project,
        "kyo-352",
        {
            "src/main/ViewerController.ts": _VIEWER_TS.replace(
                "this.pane.close();", "this.pane.close(true);"
            )
        },
    )
    _git(git_project, "checkout", "-q", "kyo-352")

    preview = kyo("docs", "preview", "--against", base, "--json")
    assert preview.code == 0, preview
    assert preview.json["obligations"] == [], (
        "the branch edits `hide`, which is outside `showLeft`'s region, so the narrowed "
        "join raises nothing"
    )
    suppressed = preview.json["suppressed"]
    assert len(suppressed) == 1, suppressed
    assert suppressed[0]["doc_path"] == "docs/viewer.md"
    assert suppressed[0]["section"] == "How the left pane opens"
    assert suppressed[0]["targets"] == ["src/main/ViewerController.ts#showLeft"]
    assert suppressed[0]["changed"] == ["src/main/ViewerController.ts"]

    listed = kyo("docs", "preview", "--against", base, "--suppressed")
    assert listed.code == 0, listed
    assert "suppressed by a declared #symbol: 1 section(s)" in listed.out
    assert "How the left pane opens" in listed.out

    counted = kyo("docs", "preview", "--against", base)
    assert "suppressed by a declared #symbol: 1 section(s)" in counted.out
    assert "--suppressed lists them" in counted.out


# ---- KYO-363: the data formats, where `package.json#scripts` narrowed nothing at all


_PACKAGE_JSON = """{
  "name": "viewer",
  "version": "0.1.0",
  "scripts": {
    "build": "tsc -p .",
    "test": "node --test",
    "note": "a } brace and a \\" quote inside a string value"
  },
  "dependencies": {
    "electron": "^44.0.0"
  },
  "files": [
    "out",
    "assets"
  ]
}
"""


def test_a_json_key_is_its_line_through_the_end_of_its_value() -> None:
    """KYO-363.c1. The shape the reporting consumer measured: `package.json` alone was
    named by 8 of their sections, every one of them raised by every dependency bump,
    because `.json` had no region finder and cannot-tell raises.

    Line numbers are read off `_PACKAGE_JSON` above: `scripts` opens on line 4 and its
    object closes on line 8, `dependencies` on 9 through 11, `files` (an array) on 12
    through 15, and the three scalars are one line each.
    """
    from kyosei import docs_regions

    def regions(symbol: str) -> object:
        return docs_regions.symbol_regions("package.json", _PACKAGE_JSON, symbol)

    # Top level, and the three value shapes: an object, an array and a scalar.
    assert regions("scripts") == ((4, 8),)
    assert regions("dependencies") == ((9, 11),)
    assert regions("files") == ((12, 15),)
    assert regions("name") == ((2, 2),)
    assert regions("version") == ((3, 3),)
    # Nested: a key inside a matched key's value is still found, so the finder has to
    # walk INTO a value it already matched rather than stepping over it.
    assert regions("build") == ((5, 5),)
    assert regions("test") == ((6, 6),)
    # A brace and an escaped quote inside a string value end nothing -- the whole reason
    # this is a string-aware scan and not a bracket count.
    assert regions("note") == ((7, 7),)
    # A key that is not there names no region, which raises like the bare path.
    assert regions("devDependencies") == ()


def test_a_json_file_that_will_not_parse_is_cannot_tell() -> None:
    """The veto. `json.loads` is the one judge of whether this is JSON at all, and a file
    it refuses is `None` -- cannot tell, which raises -- rather than a span guessed from
    a half-read structure. A wrong region is the one outcome worse than no region.
    """
    from kyosei import docs_regions

    truncated = '{\n  "scripts": {\n    "build": "tsc"\n'
    assert docs_regions.symbol_regions("package.json", truncated, "scripts") is None
    trailing_comma = '{\n  "scripts": {"build": "tsc"},\n}\n'
    assert docs_regions.symbol_regions("package.json", trailing_comma, "scripts") is None
    # A document is not always an object: an array parses, carries no keys, and is `()`.
    assert docs_regions.symbol_regions("a.json", "[1, 2]\n", "scripts") == ()
    # Every occurrence of a key is a region, the `.py` rule for a repeated name: two
    # `"build"` keys in different objects widen what raises rather than picking one.
    twice = '{\n  "a": {\n    "build": 1\n  },\n  "b": {\n    "build": 2\n  }\n}\n'
    assert docs_regions.symbol_regions("a.json", twice, "build") == ((3, 3), (6, 6))


_WORKFLOW_YML = """# What this workflow is for.
# Two comment lines, and both of them are part of `name`.
name: build

on:
  push:
    branches:
      - main

env:
  NODE_ENV: production

jobs:
  build:
    steps:
      - name: checkout
        uses: actions/checkout@v4
      - name: run
        run: |
          echo "starting"
          build: not a key
          echo "done"
    timeout-minutes: 10

tags: [a, b]
"""


def test_a_yaml_key_is_its_line_through_the_lines_indented_under_it() -> None:
    """KYO-363.c2. Line numbers are read off `_WORKFLOW_YML` above: `name` is line 3 under
    two comment lines, `on:` is 5 and its block ends at 8, `jobs:` is 13 and the last line
    indented under it is 23, `run: |` is 19 and its block scalar ends at 22.
    """
    from kyosei import docs_regions

    def regions(symbol: str) -> object:
        return docs_regions.symbol_regions("ci.yml", _WORKFLOW_YML, symbol)

    # Top level, with the comment block directly above it inside the region -- the rule
    # `_python_regions` follows for a decorator and `_declaration_regions` for a JSDoc
    # block. `name` is also a key at depth, twice, under two `- ` sequence dashes.
    assert regions("name") == ((1, 3), (16, 16), (18, 18))
    # A nested block, and a list value.
    assert regions("on") == ((5, 8),)
    assert regions("branches") == ((7, 8),)
    assert regions("NODE_ENV") == ((11, 11),)
    # A block scalar is covered by the indentation rule for free...
    assert regions("run") == ((19, 22),)
    # ...and a line inside one that LOOKS like a key declares nothing: `build` has the
    # one region at line 14 and no second at line 21.
    assert regions("build") == ((14, 23),)
    # Flow style on one line is the one-line region.
    assert regions("tags") == ((25, 25),)
    assert regions("nowhere") == ()
    # Both spellings, because which one a project writes is a coin flip.
    assert docs_regions.symbol_regions("ci.yaml", _WORKFLOW_YML, "on") == ((5, 8),)
    assert ".yaml" in docs_regions.REGION_SUFFIXES


def test_the_yaml_shapes_this_cannot_describe_are_vetoed_rather_than_guessed() -> None:
    """The other half of c2. The region rule is "the key's line through the lines indented
    under it", and there are shapes that parse perfectly and are not that: a merge key's
    content is somewhere else in the file, an alias's content is somewhere else in the
    file, and a flow mapping spanning lines has no indentation to read. Each is `None`.
    """
    from kyosei import docs_regions

    def regions(text: str, symbol: str = "jobs") -> object:
        return docs_regions.symbol_regions("ci.yml", text, symbol)

    assert regions("defaults: &base\n  shell: bash\njobs:\n  <<: *base\n") is None
    assert regions("jobs: &anchor\n  build: 1\n") is None
    assert regions("jobs: {\n  build: 1,\n}\n") is None
    # Not YAML at all: the parser is the judge, and it raises rather than answering.
    assert regions("jobs:\n  - a\n   bad: indent\n") is None
    assert regions("jobs:\n\tbuild: 1\n") is None
    # And a flow mapping that opens and closes on one line is fine -- the veto is about
    # spanning lines, not about flow style.
    assert regions("jobs: {build: 1}\n") == ((1, 1),)
    # A `#` inside a quoted value is not a comment, and a `{` inside one is not a flow
    # bracket: neither may veto a file that is perfectly readable.
    assert regions('jobs:\n  note: "a # and a { here"\n') == ((1, 2),)
    # And the two false vetoes the first version of this finder produced, both measured
    # on real stores' own `kyosei/config.yml`: `'`, `&` and `*` are only special where a
    # node may BEGIN, and in a plain scalar they are ordinary characters. Without this
    # every project whose config says `the tree's default` or `npm ci && node x.js` lost
    # the narrowing on its whole config file, silently, with nothing raised to say so.
    assert regions("jobs:\n  note: the tree's own default\n") == ((1, 2),)
    assert regions("jobs:\n  run: cd apps/desktop && npm ci\n") == ((1, 2),)
    assert regions("jobs:\n  glob: src/*.ts\n") == ((1, 2),)
    # Quoted keys and values still read as quoted, including `''` for a literal quote.
    assert regions("jobs:\n  'it''s': 1\n") == ((1, 2),)
    assert docs_regions.symbol_regions("a.yml", "'jobs': 1\n", "jobs") == ((1, 1),)


_SHELL_CSS = """/* The shell's tokens. A } brace in a comment ends nothing. */
:root {
  --pane-width: 240px;
  --label: "a } brace in a string";
}

.pane-left,
.pane-right {
  width: var(--pane-width);
}

@media (max-width: 600px) {
  .pane-left {
    width: 100%;
  }
}
"""


def test_a_css_rule_is_its_prelude_through_its_matching_brace() -> None:
    """KYO-363.c3. Line numbers are read off `_SHELL_CSS` above: `:root` is 2 through 5,
    the selector list opens on 7 and closes on 10, the `@media` block is 12 through 16
    and the rule inside it 13 through 15.
    """
    from kyosei import docs_regions

    def regions(symbol: str) -> object:
        return docs_regions.symbol_regions("shell.css", _SHELL_CSS, symbol)

    # A plain selector, and a rule nested inside an at-rule -- both are a prelude with a
    # block, which is why nesting costs the scanner nothing.
    assert regions(":root") == ((2, 5),)
    assert regions("@media (max-width: 600px)") == ((12, 16),)
    assert regions(".pane-left") == ((13, 15),), (
        "the selector list on lines 7-8 is one decision about two selectors and is not "
        "named by `.pane-left` alone"
    )
    # A selector list is matched WHOLE, with whitespace collapsed across its lines.
    assert regions(".pane-left, .pane-right") == ((7, 10),)
    assert regions(".pane-left,\n.pane-right") == ((7, 10),)
    # A custom property is its declaration line -- declared, not merely used: line 9
    # mentions `--pane-width` inside a `var()` and is not a second region.
    assert regions("--pane-width") == ((3, 3),)
    # A `}` inside a string ends nothing, exactly as one inside the comment on line 1
    # does not open or close anything.
    assert regions("--label") == ((4, 4),)
    assert regions(".no-such-rule") == ()


def test_css_braces_that_do_not_close_are_cannot_tell() -> None:
    """The other half of c3. Brace matching is what `_declaration_regions` refused for the
    brace languages, and it is safe here only because the two hiding places for a brace --
    a string and a `/* */` comment -- are closed forms this reads. An unbalanced file is
    `None` for every symbol in it: the nesting the scanner believed in was never there, so
    every span it produced was measured against a structure it had already misread.
    """
    from kyosei import docs_regions

    def regions(text: str, symbol: str = ".a") -> object:
        return docs_regions.symbol_regions("shell.css", text, symbol)

    assert regions(".a { color: red;\n") is None
    assert regions(".a { color: red; }\n}\n") is None
    assert regions("/* unterminated\n.a { color: red; }\n") is None
    assert regions('.a { content: "unterminated;\n}\n') is None
    # A brace hiding in a comment or a string does NOT unbalance the file.
    assert regions("/* } */\n.a { color: red; }\n") == ((2, 2),)
    assert regions('.a { content: "}"; }\n') == ((1, 1),)


def test_a_data_format_symbol_that_names_nothing_is_said_out_loud(
    kyo, project: Path
) -> None:
    """KYO-363.c1's last clause, and c2/c3's: a key absent from the file gets `()`, and
    `kyo docs check` names it in the same sentence KYO-358 built for the brace languages
    rather than accepting it in silence. The suffix IS one the instrument reads now, so
    this is the "no region found" notice and not the "narrows nothing" one.
    """
    _write_doc(project, "package.json", _PACKAGE_JSON)
    _write_doc(project, "ci.yml", _WORKFLOW_YML)
    _write_doc(project, "shell.css", _SHELL_CSS)
    _write_doc(
        project,
        "docs/PRD.md",
        # One per format naming something that is in the file as TEXT -- so the target
        # resolves -- and is not a key or a selector: a JSON value (`viewer` is what
        # `name` is SET to), a YAML value, and a CSS property name.
        "## The runtime\n\ngoverns: package.json#viewer\n\n"
        "## The env\n\ngoverns: ci.yml#production\n\n"
        "## The width\n\ngoverns: shell.css#width\n\n"
        # ...and one per format that DOES name a region, so the notice is shown to be
        # about the region and not about the suffix.
        "## The build\n\ngoverns: package.json#scripts\n\n"
        "## CI\n\ngoverns: ci.yml#env\n\n"
        "## Tokens\n\ngoverns: shell.css#:root\n\nProse.\n",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True, result.json
    assert result.json["problems"] == [], result.json["problems"]
    notices = result.json["notices"]
    assert sorted(n["target"] for n in notices) == [
        "ci.yml#production",
        "package.json#viewer",
        "shell.css#width",
    ], notices
    for notice in notices:
        # KYO-358's sentence, not KYO-351's: the suffix IS read now, so what is missing
        # is a region and not a region finder.
        assert "no region found" in notice["says"], notice
        assert "like the bare path" in notice["says"], notice


def test_the_new_suffixes_are_in_the_tuple_every_message_is_spelled_from() -> None:
    """KYO-363.c5. `region_suffixes_says` is derived from `REGION_SUFFIXES`, so the notice
    and the nudge follow the tuple by construction; what needs a test is that the tuple
    itself moved, and the README test above is what keeps the prose with it.
    """
    from kyosei import docs_regions

    for suffix in (".json", ".yml", ".yaml", ".css"):
        assert suffix in docs_regions.REGION_SUFFIXES, suffix
        assert docs_regions.narrows_by_symbol(f"x{suffix}"), suffix
        assert f"`{suffix}`" in docs_regions.region_suffixes_says(), suffix
    # And the suffix that still stands for "cannot region" is outside it.
    assert ".scss" not in docs_regions.REGION_SUFFIXES
    assert not docs_regions.narrows_by_symbol("x.scss")


def test_the_data_formats_narrow_end_to_end(kyo, git_project: Path) -> None:
    """KYO-363.c4, the question the record is actually about: a section governing
    `package.json#scripts` is not raised by a dependency bump and is raised by a script
    change, and the same pair for a `.yml` key and a `.css` selector.

    All three in one project and two branches off one base, because the fact under test
    is the join's answer over a diff and three copies of the scaffolding would say it
    three times.
    """
    from .test_kyo137_docs_obligations import _branch_editing, _commit, _git, _write

    _write(
        git_project,
        "docs/viewer.md",
        "# The viewer\n\n"
        "## How the build runs\n\ngoverns: package.json#scripts\n\nProse.\n\n"
        "## What CI does\n\ngoverns: ci.yml#env\n\nProse.\n\n"
        "## The shell's tokens\n\ngoverns: shell.css#:root\n\nProse.\n",
    )
    _write(git_project, "package.json", _PACKAGE_JSON)
    _write(git_project, "ci.yml", _WORKFLOW_YML)
    _write(git_project, "shell.css", _SHELL_CSS)
    base = _commit(git_project, "baseline")
    assert kyo("docs", "check").code == 0, "every target resolves before anything is asked"

    _branch_editing(
        git_project,
        "outside",
        {
            "package.json": _PACKAGE_JSON.replace("^44.0.0", "^45.0.0"),
            "ci.yml": _WORKFLOW_YML.replace("timeout-minutes: 10", "timeout-minutes: 20"),
            "shell.css": _SHELL_CSS.replace("width: 100%", "width: 90%"),
        },
    )
    _branch_editing(
        git_project,
        "inside",
        {
            "package.json": _PACKAGE_JSON.replace("tsc -p .", "tsc -p tsconfig.json"),
            "ci.yml": _WORKFLOW_YML.replace("NODE_ENV: production", "NODE_ENV: test"),
            "shell.css": _SHELL_CSS.replace("--pane-width: 240px", "--pane-width: 260px"),
        },
    )

    _git(git_project, "checkout", "-q", "outside")
    preview = kyo("docs", "preview", "--against", base, "--json")
    assert preview.code == 0, preview
    assert preview.json["obligations"] == [], (
        "a dependency bump, a sibling yaml key and a rule outside `:root` are all "
        "outside the governed regions, so the narrowed join raises nothing"
    )
    assert len(preview.json["suppressed"]) == 3, preview.json["suppressed"]

    _git(git_project, "checkout", "-q", "inside")
    raised = kyo("docs", "preview", "--against", base, "--json")
    assert raised.code == 0, raised
    sections = sorted(o["section"] for o in raised.json["obligations"])
    assert sections == ["How the build runs", "The shell's tokens", "What CI does"], sections


# ------------------------------------------------------------------------ KYO-393


def _verdict(doc: str, section: str, verdict: str = "not-affected") -> dict:
    return {
        "doc_path": doc,
        "section": section,
        "verdict": verdict,
        "method": "read it",
        "reason": "unaffected",
    }


NUMBERED = {"NOTES.md": "## {number}."}
OBLIGATIONS = [
    {
        "id": "KYO-1.o1",
        "doc_path": "NOTES.md",
        "section": "205. APP-60: the dashboard (2026-09-16)",
    },
    {"id": "KYO-1.o2", "doc_path": "NOTES.md", "section": "206. APP-61: the export"},
    {"id": "KYO-1.o3", "doc_path": "docs/a.md", "section": "12. Not numbered here"},
]


def test_a_numbered_documents_verdict_matches_by_section_number() -> None:
    """KYO-393.c1: the number is the key; the rest of the heading may differ, and the
    pair says it was matched by number so every caller can name it."""
    from kyosei import docs_numbered

    matched, unmatched = docs_numbered.match_docs_verdicts(
        OBLIGATIONS, [_verdict("NOTES.md", "205. APP-60 dashboard")], NUMBERED
    )
    assert unmatched == []
    assert [pair["obligation"]["id"] for pair in matched] == ["KYO-1.o1"]
    assert matched[0]["by_number"] == 205
    line = docs_numbered.number_matched_line(matched[0])
    assert "205. APP-60 dashboard" in line and "205. APP-60: the dashboard" in line
    assert "section number 205" in line


def test_an_exact_heading_match_is_not_reported_as_matched_by_number() -> None:
    from kyosei import docs_numbered

    matched, _ = docs_numbered.match_docs_verdicts(
        OBLIGATIONS, [_verdict("NOTES.md", "206. APP-61: the export")], NUMBERED
    )
    assert matched[0]["obligation"]["id"] == "KYO-1.o2"
    assert "by_number" not in matched[0]


def test_number_matching_needs_the_document_declared_numbered() -> None:
    """A heading that merely begins with a digit is not a numbered section: without
    the declaration, and without passing it, the exact heading is the only key."""
    from kyosei import docs_numbered

    _, unmatched = docs_numbered.match_docs_verdicts(
        OBLIGATIONS, [_verdict("docs/a.md", "12. Paraphrased")], NUMBERED
    )
    assert [row["why"] for row in unmatched] == [docs_numbered.VERDICT_UNMATCHED_NO_OBLIGATION]
    _, unmatched = docs_numbered.match_docs_verdicts(
        OBLIGATIONS, [_verdict("NOTES.md", "205. APP-60 dashboard")]
    )
    assert [row["why"] for row in unmatched] == [docs_numbered.VERDICT_UNMATCHED_NO_OBLIGATION]


def test_a_number_another_verdict_already_claimed_is_a_duplicate_not_applied() -> None:
    """KYO-393.c2: whether the first entry reached the section by heading or by number,
    the second is a contradiction the controller resolves."""
    from kyosei import docs_numbered

    for first in ("205. APP-60: the dashboard (2026-09-16)", "205. something else"):
        matched, unmatched = docs_numbered.match_docs_verdicts(
            OBLIGATIONS,
            [
                _verdict("NOTES.md", first),
                _verdict("NOTES.md", "205. a third wording", "updated"),
            ],
            NUMBERED,
        )
        assert [pair["entry"]["section"] for pair in matched] == [first]
        assert [row["why"] for row in unmatched] == [docs_numbered.VERDICT_UNMATCHED_DUPLICATE]
        line = docs_numbered.unmatched_verdict_line(unmatched[0])
        assert "205" in line and "already answered" in line


def test_a_number_two_obligations_share_matches_neither() -> None:
    from kyosei import docs_numbered

    twice = [*OBLIGATIONS, {"id": "KYO-1.o9", "doc_path": "NOTES.md", "section": "205. again"}]
    matched, unmatched = docs_numbered.match_docs_verdicts(
        twice, [_verdict("NOTES.md", "205. paraphrase")], NUMBERED
    )
    assert matched == [] and len(unmatched) == 1


def test_section_number_reads_only_the_declared_template() -> None:
    from kyosei import docs_numbered

    assert docs_numbered.section_number("205. anything", "## {number}.") == 205
    assert docs_numbered.section_number("## 205. anything", "## {number}.") == 205
    assert docs_numbered.section_number("Note 7: x", "### Note {number}:") == 7
    assert docs_numbered.section_number("205 no dot", "## {number}.") is None
    assert docs_numbered.section_number("Preamble", "## {number}.") is None


def test_numbered_templates_reads_the_documents_yml_declaration(project: Path) -> None:
    from kyosei import docs_numbered

    _write_doc(project, "NOTES.md", "# Notes\n\n## 1. first\n")
    _write_documents_yml(
        project,
        "documents:\n"
        "  notes:\n"
        "    summary: the numbered notes\n"
        "    globs: [NOTES.md]\n"
        "    numbered:\n"
        "      fragments: notes-fragments\n"
        "      heading: '## {number}.'\n",
    )
    assert docs_numbered.numbered_templates(project / "kyosei", project) == {
        "NOTES.md": "## {number}."
    }
    _write_documents_yml(project, "documents: [not, a, mapping]\n")
    assert docs_numbered.numbered_templates(project / "kyosei", project) == {}, (
        "a refused documents.yml falls back to exact-heading matching, never an exception"
    )


# ------------------------------------------------------------------------ KYO-396
#
# `_python_regions` located defs, classes and module-level assignments, and nothing in a
# class body, so `path#OrderSnapshot.total_cents` got `()` — cannot-tell, raises —
# and the narrowest spelling a document can write about a dataclass field raised exactly
# like the whole file.


_MODELS_PY = '''\
from dataclasses import dataclass

LIMIT = 10


@dataclass
class OrderSnapshot:
    sku: str
    total_cents: float
    price: float = 0.0

    def total(self) -> float:
        return self.total_cents * self.price


class Chain:
    kind = "chain"
    retries = 3

    class Settings:
        timeout: int = 30
        region = "us"
'''


def _py_change(before: str, after: str, lines: set[int]) -> object:
    from kyosei import docs_regions

    return docs_regions.FileChange(
        "src/models.py", before, after, frozenset(lines), frozenset(lines)
    )


def test_a_class_body_assignment_is_a_region_by_its_qualified_name() -> None:
    """KYO-396.c1: a dataclass field, a plain class attribute and a nested class's field
    each raise when their own line changes and do not when only another line of the
    same class does. Line numbers are read off `_MODELS_PY` above."""
    from kyosei import docs_regions

    def regions(symbol: str) -> object:
        return docs_regions.symbol_regions("src/models.py", _MODELS_PY, symbol)

    assert regions("OrderSnapshot.total_cents") == ((9, 9),)
    assert regions("Chain.kind") == ((17, 17),)
    assert regions("Chain.Settings.timeout") == ((21, 21),)

    cases = [
        # (symbol, the edit, line it is on, a sibling edit, the sibling's line)
        (
            "OrderSnapshot.total_cents",
            ("total_cents: float", "total_cents: int"), 9,
            ("price: float = 0.0", "price: float = 1.0"), 10,
        ),
        (
            "Chain.kind",
            ('kind = "chain"', 'kind = "store"'), 17,
            ("retries = 3", "retries = 4"), 18,
        ),
        (
            "Chain.Settings.timeout",
            ("timeout: int = 30", "timeout: int = 60"), 21,
            ('region = "us"', 'region = "eu"'), 22,
        ),
    ]
    for symbol, (old, new), line, (s_old, s_new), s_line in cases:
        own = _py_change(_MODELS_PY, _MODELS_PY.replace(old, new), {line})
        assert docs_regions.symbol_region_changed(own, symbol) is True, symbol
        sibling = _py_change(_MODELS_PY, _MODELS_PY.replace(s_old, s_new), {s_line})
        assert docs_regions.symbol_region_changed(sibling, symbol) is False, symbol


def test_a_class_field_is_found_by_its_bare_name_only_when_it_is_the_only_definition(
) -> None:
    """KYO-396.c2, and the rule pinned: a module-level assignment is located by its bare
    name as always; a class-body assignment by its bare name ONLY when nothing else in
    the file defines that name — not a def, a class, a module-level assignment, nor the
    same name in a second class. Ambiguous bare names stay unlocated (`()`, which raises
    at file level) rather than narrowing to lines the document may not have meant."""
    from kyosei import docs_regions

    def regions(text: str, symbol: str) -> object:
        return docs_regions.symbol_regions("m.py", text, symbol)

    # Module level: bare name, unchanged.
    assert regions(_MODELS_PY, "LIMIT") == ((3, 3),)
    # The one definition of `total_cents` in the file: located by its bare name too.
    assert regions(_MODELS_PY, "total_cents") == ((9, 9),)
    assert regions(_MODELS_PY, "timeout") == ((21, 21),)

    two_classes = "class A:\n    name = 1\n\n\nclass B:\n    name: str = 'b'\n"
    assert regions(two_classes, "name") == ()
    assert regions(two_classes, "A.name") == ((2, 2),)
    assert regions(two_classes, "B.name") == ((6, 6),)

    shadowed = "name = 0\n\n\nclass A:\n    name = 1\n"
    assert regions(shadowed, "name") == ((1, 1),), "the module-level one only"
    as_method = "class A:\n    size = 1\n\n\nclass B:\n    def size(self):\n        return 2\n"
    assert regions(as_method, "size") == ((6, 7),), "the def only"
    # A local inside a function body is never a region.
    local = "def f():\n    total = 1\n    return total\n"
    assert regions(local, "total") == ()
    assert regions(local, "f.total") == ()


# ------------------------------------------------------------------------ KYO-397
#
# A bare-symbol target resolves by text to every code file carrying its words, which for
# a consumer project's `OrderSnapshot.total_cents` was 110 files, and a file that only USES
# the field got `()` from the region finder — cannot-tell — so a change to any of the 110
# raised the section. Resolution stays textual (that is what `kyo docs check` validates);
# raising narrows to the file(s) whose region finder locates a definition.


_CALLER_PY = '''\
from .models import OrderSnapshot


def render(snapshot: OrderSnapshot) -> str:
    return f"{snapshot.total_cents:.1f} cents"
'''

_GOVERNS_FIELD = (
    "# Inventory\n\n## Totals\n\ngoverns:\n  - OrderSnapshot.total_cents\n\nProse.\n"
)

_CALLERS = ("src/views/card.py", "src/views/table.py", "src/api/export.py", "src/jobs/sync.py")


def _snapshot_tree(project: Path) -> None:
    """A consumer project's shape, small: one defining file and several that only mention it."""
    _write_doc(project, "src/models.py", _MODELS_PY)
    for caller in _CALLERS:
        _write_doc(project, caller, _CALLER_PY)
    _write_doc(project, "docs/inventory.md", _GOVERNS_FIELD)


def _same_file_change(path: str, text: str, edited: str, line: int) -> object:
    from kyosei import docs_regions

    return docs_regions.FileChange(path, text, edited, frozenset({line}), frozenset({line}))


def test_a_bare_symbol_raises_on_its_definition_and_not_on_a_mention(project: Path) -> None:
    """KYO-397.c1. Resolution is unchanged — every file carrying the words — and the
    definition-bearing subset is the one file the region finder places it in."""
    from kyosei import docs_obligations, docs_targets

    _snapshot_tree(project)
    [document] = docs_targets.documents(project)
    [target] = document.resolved
    assert target.shape == docs_targets.SHAPE_SYMBOL
    assert set(target.paths) == {"src/models.py", *_CALLERS}
    assert target.defined_in == ("src/models.py",)

    edited_caller = _CALLER_PY.replace(":.1f", ":.2f")
    caller_only = {
        path: _same_file_change(path, _CALLER_PY, edited_caller, 5) for path in _CALLERS
    }
    assert docs_obligations.obligations_for_changed_files(
        project, list(_CALLERS), changes=caller_only
    ) == [], "every caller changed, the definition did not: nothing raised"

    field = _same_file_change(
        "src/models.py",
        _MODELS_PY,
        _MODELS_PY.replace("total_cents: float", "total_cents: int"),
        9,
    )
    [raised] = docs_obligations.obligations_for_changed_files(
        project, ["src/models.py", *_CALLERS], changes={**caller_only, "src/models.py": field}
    )
    assert raised.section == "Totals"
    assert raised.changed == ("src/models.py",)

    sibling = _same_file_change(
        "src/models.py", _MODELS_PY, _MODELS_PY.replace("sku: str", "sku: bytes"), 8
    )
    assert docs_obligations.obligations_for_changed_files(
        project, ["src/models.py"], changes={"src/models.py": sibling}
    ) == [], "another line of the defining file is not the definition"

    # No diff evidence at all degrades to file level, as every symbol target always has.
    assert docs_obligations.obligations_for_changed_files(project, [_CALLERS[0]])


def test_a_mention_the_finder_cannot_read_still_raises(project: Path) -> None:
    """KYO-397.c1's qualifier and decision 9's asymmetry: a resolved file whose type has
    no region finder cannot tell a definition from a mention, and cannot-tell raises."""
    from kyosei import docs_obligations, docs_targets

    _snapshot_tree(project)
    script = "#!/bin/sh\n# reads OrderSnapshot total_cents from the export\n"
    _write_doc(project, "src/tools/dump.sh", script)
    [document] = docs_targets.documents(project)
    assert "src/tools/dump.sh" in document.resolved[0].paths
    change = _same_file_change("src/tools/dump.sh", script, script + "echo\n", 3)
    [raised] = docs_obligations.obligations_for_changed_files(
        project, ["src/tools/dump.sh"], changes={"src/tools/dump.sh": change}
    )
    assert raised.changed == ("src/tools/dump.sh",)


def test_a_definition_this_diff_moves_raises_on_both_ends(project: Path) -> None:
    """Why the per-file answer comes from the diff, not the tree: the working tree knows
    one side of a move. Here the tree (the branch's after side) says the definition is in
    `src/inventory.py`; the diff also sees it leave `src/models.py`. Both files changed,
    both are definitions on some side, and the section is raised."""
    from kyosei import docs_obligations, docs_regions, docs_targets

    moved_out = _MODELS_PY.replace("    total_cents: float\n", "")
    new_home = "class OrderSnapshot:\n    total_cents: float\n"
    _write_doc(project, "src/models.py", moved_out.replace("total_cents * ", ""))
    _write_doc(project, "src/inventory.py", new_home)
    _write_doc(project, "src/views/card.py", _CALLER_PY)
    _write_doc(project, "docs/inventory.md", _GOVERNS_FIELD)
    [document] = docs_targets.documents(project)
    assert document.resolved[0].defined_in == ("src/inventory.py",)
    # `src/models.py` no longer carries the words, so only the new home is resolved; the
    # removal side is what `kyo report` sees on the controller's (before) tree:
    removal = docs_regions.FileChange(
        "src/models.py", _MODELS_PY, moved_out, frozenset({9}), frozenset({8, 9})
    )
    assert docs_regions.symbol_region_changed(removal, "OrderSnapshot.total_cents")
    added = docs_regions.FileChange(
        "src/inventory.py", "", new_home, frozenset({1}), frozenset({1, 2})
    )
    [raised] = docs_obligations.obligations_for_changed_files(
        project, ["src/inventory.py"], changes={"src/inventory.py": added}
    )
    assert raised.changed == ("src/inventory.py",)


def test_a_symbol_defined_nowhere_raises_everywhere_and_docs_check_says_so(
    kyo, project: Path
) -> None:
    """KYO-397.c2. With no located definition there is nothing to narrow to, so every
    resolved file raises on change, as before — and `kyo docs check` names the target
    and the file count, exit status untouched."""
    from kyosei import docs_obligations, docs_targets

    for caller in _CALLERS:
        _write_doc(project, caller, _CALLER_PY)  # the class lives in a library, say
    _write_doc(project, "docs/inventory.md", _GOVERNS_FIELD)
    [document] = docs_targets.documents(project)
    assert document.resolved[0].defined_in == ()
    edited = _CALLER_PY.replace(":.1f", ":.2f")
    change = _same_file_change(_CALLERS[1], _CALLER_PY, edited, 5)
    [raised] = docs_obligations.obligations_for_changed_files(
        project, [_CALLERS[1]], changes={_CALLERS[1]: change}
    )
    assert raised.changed == (_CALLERS[1],)

    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    [notice] = result.json["notices"]
    assert notice["target"] == "OrderSnapshot.total_cents"
    assert f"{len(_CALLERS)} file(s)" in notice["says"]
    assert "no definition found" in notice["says"]
    text = kyo("docs", "check")
    assert "OrderSnapshot.total_cents" in text.out

    # And the notice clears once a definition exists — the remedy it implies.
    _write_doc(project, "src/models.py", _MODELS_PY)
    assert kyo("docs", "check", "--json").json["notices"] == []


def test_the_preview_does_not_raise_a_bare_symbol_for_a_caller_only_diff(
    kyo, git_project: Path
) -> None:
    """KYO-397.c3, end to end through `kyo docs preview`: a branch that changes only a
    caller of a governed field raises nothing, and the section is listed as suppressed —
    so a reader sees what the narrowing did. Remove definition-only raising and the
    caller's `()` is cannot-tell again, and this raises."""
    from .test_kyo137_docs_obligations import _branch_editing, _commit, _git, _write

    _write(git_project, "src/models.py", _MODELS_PY)
    for caller in _CALLERS:
        _write(git_project, caller, _CALLER_PY)
    _write(git_project, "docs/inventory.md", _GOVERNS_FIELD)
    base = _commit(git_project, "baseline")
    assert kyo("docs", "check").code == 0

    _branch_editing(
        git_project, "caller", {_CALLERS[0]: _CALLER_PY.replace(":.1f", ":.2f")}
    )
    _branch_editing(
        git_project,
        "field",
        {"src/models.py": _MODELS_PY.replace("total_cents: float", "total_cents: int")},
    )

    _git(git_project, "checkout", "-q", "caller")
    preview = kyo("docs", "preview", "--against", base, "--json")
    assert preview.code == 0, preview
    assert preview.json["obligations"] == [], preview.json["obligations"]
    [suppressed] = preview.json["suppressed"]
    assert suppressed["section"] == "Totals"
    assert suppressed["changed"] == [_CALLERS[0]]

    _git(git_project, "checkout", "-q", "field")
    raised = kyo("docs", "preview", "--against", base, "--json")
    assert [o["section"] for o in raised.json["obligations"]] == ["Totals"]


# ------------------------------------------------------------------------ KYO-499
#
# Section status and its links (docs/design/docs.md#kyo-499). A1, pairs agree, holds in
# every document of every project. A2-A5 hold in a set that declares `section_status:`
# in documents.yml.

import json  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from kyosei import docs_check  # noqa: E402

from .conftest import git_init  # noqa: E402

_OPTED_IN = """\
documents:
  design:
    summary: The design record.
    globs:
      - docs/design/**/*.md
    section_status:
      statuses: [current, amended, superseded]
"""


def _section_problems(kyo) -> list[dict]:
    return kyo("docs", "check", "--json").json["section_problems"]


def _reasons(kyo) -> list[str]:
    return [problem["reason"] for problem in _section_problems(kyo)]


# ------------------------------------------------------------ c1: pairs agree (A1)


@pytest.mark.parametrize(
    ("block", "reason", "fixed"),
    [
        # a link, and a status that says nothing changed (either spelling, either link)
        ("status: current\namended-by: Later", "section-link-without-status",
         "status: amended\namended-by: Later"),
        ("amended_by: [Later]", "section-link-without-status",
         "status: amended\namended_by: [Later]"),
        ("superseded-by: Later", "section-link-without-status",
         "status: superseded\nsuperseded-by: Later"),
        ("status: current\nsuperseded_by: [Later]", "section-link-without-status",
         "status: superseded\nsuperseded_by: [Later]"),
        # a status that says something changed it, and no link (`[]` names nothing)
        ("status: amended", "section-status-without-link",
         "status: amended\namended-by: Later"),
        ("status: amended\namended_by: []", "section-status-without-link",
         "status: amended\namended_by: [Later]"),
        ("status: superseded\nsuperseded_by: []", "section-status-without-link",
         "status: superseded\nsuperseded_by: [Later]"),
    ],
)
def test_pairs_that_disagree_are_refused_in_any_project_and_the_remedy_clears_it(
    kyo, project: Path, block: str, reason: str, fixed: str
) -> None:
    """KYO-499.c1, both directions, both spellings, `[]` as empty — in a project that
    declares nothing at all (the default document set), and with the remedy the
    refusal names applied to prove it clears."""
    doc = "## Earlier\n\n{block}\n\nProse.\n\n## Later\n\nLater changed Earlier.\n"
    _write_doc(project, "README.md", doc.format(block=block))
    result = kyo("docs", "check", "--json")
    assert result.code == 3, result
    [problem] = result.json["section_problems"]
    assert problem["reason"] == reason
    assert problem["section"] == "Earlier"
    assert problem["doc_path"] == "README.md"
    text = kyo("docs", "check")
    assert reason in text.err and "Earlier" in text.err
    assert problem["remedy"] in text.err

    _write_doc(project, "README.md", doc.format(block=fixed))
    cleared = kyo("docs", "check", "--json")
    assert cleared.code == 0, cleared
    assert cleared.json["section_problems"] == []


def test_a_retired_section_may_keep_its_amendments_and_historical_needs_no_link(
    kyo, project: Path
) -> None:
    """KYO-499.c1: `superseded` may carry an earlier `amended-by` (c2), and
    `historical` is dead without naming a successor (KYO-204's vocabulary)."""
    _write_doc(
        project,
        "docs/notes.md",
        "## Old\n\nstatus: superseded\namended-by: Mid\nsuperseded-by: New\n\n"
        "## Older\n\nstatus: historical\n\n## Mid\n\nMid. ## New\n\nNew.\n",
    )
    assert _section_problems(kyo) == []


def test_a_status_alone_that_is_not_amended_or_superseded_is_not_a_pair(
    kyo, project: Path
) -> None:
    """KYO-499.c1: A1 asks only whether the pair agrees. A consumer's own statuses
    (`accepted`, `proposed`) have no link to agree with, and pass everywhere — the
    closed vocabulary is a per-set opt-in (c2)."""
    _write_doc(project, "docs/notes.md", "## A\n\nstatus: accepted\n\n## B\n\nStatus: proposed\n")
    assert _section_problems(kyo) == []


# ------------------------------------------------------- c2: the documents.yml block


@pytest.mark.parametrize(
    ("block", "named"),
    [
        ("section_status: [current]", "must be a mapping"),
        ("section_status:\n      pointer_obligations: true", "is missing statuses"),
        ("section_status:\n      statuses: []", "statuses must be a non-empty list"),
        ("section_status:\n      statuses: [current, '']", "statuses must be a non-empty list"),
        ("section_status:\n      statuses: [current]\n      pointer_obligations: yes please",
         "pointer_obligations must be true or false"),
        ("section_status:\n      statuses: [current]\n      index: ../outside.md",
         "must not climb out"),
        ("section_status:\n      statuses: [current]\n      indexes: x.md",
         "unknown key(s) indexes"),
    ],
)
def test_a_malformed_section_status_block_is_refused_by_name_and_the_remedy_clears_it(
    kyo, project: Path, block: str, named: str
) -> None:
    """KYO-499.c2: refused when the file is read, naming the key or value."""
    _write_doc(project, "docs/design/a.md", "## A\n\nProse.\n")
    _write_documents_yml(
        project,
        "documents:\n  design:\n    summary: Design.\n    globs: [docs/design/**/*.md]\n"
        f"    {block}\n",
    )
    result = kyo("docs", "check")
    assert result.code != 0, result
    assert "section_status" in result.err
    assert named in result.err

    _write_documents_yml(project, _OPTED_IN)
    assert kyo("docs", "check").code == 0


def test_a_valid_block_is_read_with_its_defaults_and_stated() -> None:
    """KYO-499.c2: `pointer_obligations` defaults to true, `index` to none, and both are
    what `kyo docs check` states for the set."""
    from kyosei import registry as registry_module
    from kyosei.docs_parse import declared_section_status_sets

    assert registry_module.section_status_block({"statuses": ["current"]}) is None
    entry = registry_module.Entry(
        "design",
        "Design.",
        {"globs": ["docs/**/*.md"], "section_status": {"statuses": ["current", "amended"]}},
    )

    class _Registry:
        declared_names = ("design",)

        def __getitem__(self, name):
            return entry

    [chosen] = declared_section_status_sets(_Registry())
    assert chosen.statuses == ("current", "amended")
    assert chosen.pointer_obligations is True and chosen.index is None
    assert chosen.globs == ("docs/**/*.md",)
    assert "pointer obligations on" in chosen.says


def test_the_clean_run_states_the_opted_in_set(kyo, project: Path) -> None:
    _write_documents_yml(project, _OPTED_IN)
    _write_doc(project, "docs/design/a.md", "## A\n\nProse.\n")
    result = kyo("docs", "check")
    assert result.code == 0, result
    assert "section status: design (statuses: current, amended, superseded" in result.out


# ------------------------------------------------------- c3: the opted-in set (A2-A5)


def _opted(project: Path, docs: dict[str, str]) -> None:
    _write_documents_yml(project, _OPTED_IN)
    for rel, text in docs.items():
        _write_doc(project, rel, text)


def test_a_status_outside_the_sets_vocabulary_is_refused_and_the_remedy_clears_it(
    kyo, project: Path
) -> None:
    _opted(project, {"docs/design/a.md": "## KYO-1\n\nstatus: retired\n\nProse.\n"})
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_STATUS_UNKNOWN
    assert "'retired'" in problem["says"] and "current, amended, superseded" in problem["says"]
    _write_doc(project, "docs/design/a.md", "## KYO-1\n\nstatus: current\n\nProse.\n")
    assert _section_problems(kyo) == []


def test_outside_an_opted_in_set_a_status_and_a_link_are_not_held_to_the_set_rules(
    kyo, project: Path
) -> None:
    """KYO-499.c3 applies to the set's own documents only; A1 still holds everywhere."""
    _opted(project, {"docs/design/a.md": "## KYO-1\n\nProse.\n"})
    _write_doc(
        project, "README.md", "## Intro\n\nstatus: amended\namended-by: Nowhere\n\nProse.\n"
    )
    # README.md is not a document under this declaration at all; declare it as one.
    _write_documents_yml(
        project,
        _OPTED_IN + "  operating:\n    summary: Top level.\n    globs: ['*.md']\n",
    )
    assert _section_problems(kyo) == []


def test_a_bare_link_resolves_in_the_same_document_only(kyo, project: Path) -> None:
    """KYO-499.c3: a bare key names a heading in THIS document. KYO-2 exists in b.md and
    not in a.md, so naming it bare from a.md is unresolved; the pointer resolves."""
    _opted(
        project,
        {
            "docs/design/a.md": "## KYO-1\n\nstatus: amended\namended-by: KYO-2\n\nProse.\n",
            "docs/design/b.md": "## KYO-2\n\nThis changed KYO-1.\n",
        },
    )
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_UNRESOLVED
    assert "KYO-2 has no section in docs/design/a.md" in problem["says"]
    assert "`<path>.md#<anchor>`" in problem["remedy"]

    _write_doc(
        project,
        "docs/design/a.md",
        "## KYO-1\n\nstatus: amended\namended-by: docs/design/b.md#kyo-2\n\nProse.\n",
    )
    assert _section_problems(kyo) == []


def test_a_link_to_a_missing_file_or_anchor_is_unresolved(kyo, project: Path) -> None:
    _opted(
        project,
        {
            "docs/design/a.md": (
                "## KYO-1\n\nstatus: amended\n"
                "amended-by: docs/design/gone.md#kyo-2, docs/design/a.md#kyo-9\n\nProse.\n"
            ),
        },
    )
    says = [problem["says"] for problem in _section_problems(kyo)]
    assert any("docs/design/gone.md is not a file" in line for line in says)
    assert any("has no heading whose anchor is #kyo-9" in line for line in says)


def test_a_self_link_is_refused_and_the_remedy_clears_it(kyo, project: Path) -> None:
    _opted(project, {"docs/design/a.md": "## KYO-1\n\nstatus: amended\namended-by: KYO-1\n"})
    assert _reasons(kyo) == [docs_check.SECTION_LINK_TO_ITSELF]
    _write_doc(
        project,
        "docs/design/a.md",
        "## KYO-1\n\nstatus: amended\namended-by: KYO-2\n\n## KYO-2\n\nNarrows KYO-1.\n",
    )
    assert _section_problems(kyo) == []


def test_a_linked_section_that_never_cites_back_is_refused_and_the_remedy_clears_it(
    kyo, project: Path
) -> None:
    """KYO-499.c3 (A3): the forward direction is derived from the amending section's
    prose. Its own back-link line does not count as prose; a `KYO-n.cN` mention and the
    pointer spelling both do."""
    _opted(
        project,
        {
            "docs/design/a.md": (
                "## KYO-1\n\nstatus: amended\namended-by: KYO-2\n\n"
                "## KYO-2\n\nstatus: amended\namended-by: KYO-3\n\nSays nothing of it.\n\n"
                "## KYO-3\n\nNarrows KYO-2.c1.\n"
            ),
        },
    )
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_NOT_CITED_BACK
    assert problem["section"] == "KYO-1"
    assert "never mentions KYO-1" in problem["says"]
    assert "add a sentence to KYO-2" in problem["remedy"]

    _write_doc(
        project,
        "docs/design/a.md",
        "## KYO-1\n\nstatus: amended\namended-by: KYO-2\n\n"
        "## KYO-2\n\nstatus: amended\namended-by: KYO-3\n\n"
        "It narrows docs/design/a.md#kyo-1.\n\n"
        "## KYO-3\n\nNarrows KYO-2.c1.\n",
    )
    assert _section_problems(kyo) == []


def test_a_citation_under_a_subheading_counts_and_the_next_sibling_does_not(
    kyo, project: Path
) -> None:
    """KYO-510.c1/c2: A3 reads the linked section's whole region (its sub-headings
    included), and stops at the next heading of the same depth."""
    head = "## KYO-1\n\nstatus: amended\namended-by: KYO-2\n\n"
    _opted(
        project,
        {"docs/design/a.md": head + "## KYO-2\n\nIntro.\n\n### Detail\n\nNarrows KYO-1.\n"},
    )
    assert _section_problems(kyo) == []
    _write_doc(
        project,
        "docs/design/a.md",
        head + "## KYO-2\n\nIntro.\n\n### Detail\n\nNothing.\n\n## KYO-3\n\nNarrows KYO-1.\n",
    )
    assert _reasons(kyo) == [docs_check.SECTION_LINK_NOT_CITED_BACK]


def test_a_mention_must_be_the_whole_key(kyo, project: Path) -> None:
    """`KYO-10` does not mention `KYO-1`."""
    _opted(
        project,
        {"docs/design/a.md": "## KYO-1\n\nstatus: amended\namended-by: KYO-2\n\n"
         "## KYO-2\n\nNarrows KYO-10.\n\n## KYO-10\n\nOther.\n"},
    )
    assert _reasons(kyo) == [docs_check.SECTION_LINK_NOT_CITED_BACK]


_RETIRED = (
    "## KYO-1\n\nstatus: superseded\nsuperseded-by: KYO-2\n\nThe old way.\n\n"
    "## KYO-2\n\nReplaces KYO-1.\n"
)


@pytest.mark.parametrize(
    "fixed",
    [
        # re-pointed at the successor
        "# Why: docs/design/a.md#kyo-2\nx = 1\n",
        # or the successor named in the same comment block, by id or by pointer
        "# Why: docs/design/a.md#kyo-1, which KYO-2 superseded.\nx = 1\n",
        "# Why: docs/design/a.md#kyo-1;\n# now docs/design/a.md#kyo-2.\nx = 1\n",
    ],
)
def test_a_src_pointer_to_a_superseded_section_is_refused_and_the_remedy_clears_it(
    kyo, project: Path, fixed: str
) -> None:
    """KYO-499.c3 (A5): the code is the "elsewhere" that cites a section as current."""
    _opted(
        project,
        {
            "docs/design/a.md": _RETIRED,
            "src/thing.py": "x = 0\n\n# Why: docs/design/a.md#kyo-1\nx = 1\n",
        },
    )
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.POINTER_TO_SUPERSEDED_SECTION
    assert problem["doc_path"] == "src/thing.py"
    assert "src/thing.py:3 points at docs/design/a.md#kyo-1" in problem["says"]
    assert "KYO-2" in problem["says"]
    assert "re-point it at the successor" in problem["remedy"]

    _write_doc(project, "src/thing.py", fixed)
    assert _section_problems(kyo) == []


def test_a_successor_named_in_another_comment_block_does_not_count(
    kyo, project: Path
) -> None:
    _opted(
        project,
        {
            "docs/design/a.md": _RETIRED,
            "src/thing.py": "# KYO-2 is elsewhere.\n\n# Why: docs/design/a.md#kyo-1\nx = 1\n",
        },
    )
    assert _reasons(kyo) == [docs_check.POINTER_TO_SUPERSEDED_SECTION]


def test_a_pointer_to_a_current_or_amended_section_passes(kyo, project: Path) -> None:
    _opted(
        project,
        {
            "docs/design/a.md": _RETIRED,
            "src/thing.py": "# Why: docs/design/a.md#kyo-2\nx = 1\n",
        },
    )
    assert _section_problems(kyo) == []


def test_every_rule_reports_in_one_pass_and_fails_the_build(kyo, project: Path) -> None:
    """The c3 refusals are failures exactly as an unresolved governs: target is."""
    _opted(
        project,
        {
            "docs/design/a.md": (
                "## KYO-1\n\nstatus: retired\n\n## KYO-2\n\nstatus: amended\n"
                "amended-by: KYO-2, KYO-9\n"
            ),
        },
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert result.json["ok"] is False
    assert sorted(_reasons(kyo)) == sorted(
        [
            docs_check.SECTION_STATUS_UNKNOWN,
            docs_check.SECTION_LINK_TO_ITSELF,
            docs_check.SECTION_LINK_UNRESOLVED,
        ]
    )


# ------------------------------------------------------- c4: the worker's stop hook


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def _branch_with(project: Path, files: dict[str, str]) -> None:
    git_init(project)
    _git(project, "checkout", "-q", "-b", "feature")
    for rel, text in files.items():
        _write_doc(project, rel, text)
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "work")


def _check_branch_reasons(kyo) -> list[str]:
    data = json.loads(kyo("check-branch", "--against", "main", "--json").out)
    return [row["reason"] for row in (data.get("data") or data)["section_status_problems"]]


def test_check_branch_refuses_a_changed_document_whose_pair_disagrees(
    kyo, project: Path
) -> None:
    """KYO-499.c4: A1, at the stop hook, with the remedy that clears it."""
    from kyosei.contracts import CONTRACT_SECTION_STATUS_DISAGREES

    _write_doc(project, "docs/notes.md", "## A\n\nProse.\n")
    _branch_with(project, {"docs/notes.md": "## A\n\nstatus: amended\n\nProse.\n"})
    result = kyo("check-branch", "--against", "main")
    assert result.code == 2, result
    message = result.out + result.err
    assert CONTRACT_SECTION_STATUS_DISAGREES in message
    assert "section-status-without-link" in message
    assert "add `amended-by:`" in message
    assert _check_branch_reasons(kyo) == ["section-status-without-link"]

    _write_doc(project, "docs/notes.md", "## A\n\nstatus: amended\namended-by: B\n\n## B\n\nB.\n")
    _git(project, "commit", "-q", "-am", "fix")
    assert kyo("check-branch", "--against", "main").code == 0


def test_check_branch_runs_the_set_rules_and_the_pointer_rule(kyo, project: Path) -> None:
    """KYO-499.c4: A2-A4 on a changed opted-in document, A5 on a changed src/ file —
    the same refusals `kyo docs check` names."""
    _write_documents_yml(project, _OPTED_IN)
    _write_doc(project, "docs/design/a.md", _RETIRED)
    _write_doc(project, "src/thing.py", "x = 1\n")
    _branch_with(project, {"src/thing.py": "# Why: docs/design/a.md#kyo-1\nx = 1\n"})
    assert _check_branch_reasons(kyo) == [docs_check.POINTER_TO_SUPERSEDED_SECTION]
    assert kyo("check-branch", "--against", "main").code == 2

    _write_doc(project, "src/thing.py", "# Why: docs/design/a.md#kyo-2\nx = 1\n")
    _write_doc(project, "docs/design/a.md", _RETIRED + "\n## KYO-3\n\nstatus: gone\n")
    _git(project, "commit", "-q", "-am", "more")
    assert _check_branch_reasons(kyo) == [docs_check.SECTION_STATUS_UNKNOWN]

    _write_doc(project, "docs/design/a.md", _RETIRED)
    _git(project, "commit", "-q", "-am", "fix")
    assert kyo("check-branch", "--against", "main").code == 0


def test_check_branch_ignores_a_problem_the_branch_did_not_touch(kyo, project: Path) -> None:
    """KYO-499.c4: a disagreement already on main is the controller's `kyo docs check`
    finding, not a reason to block a worker whose branch never touched it."""
    _write_doc(project, "docs/notes.md", "## A\n\nstatus: amended\n\nProse.\n")
    _branch_with(project, {"docs/other.md": "## B\n\nProse.\n"})
    assert kyo("check-branch", "--against", "main").code == 0


def test_check_branch_reads_the_whole_set_when_a_set_document_changes(
    kyo, project: Path
) -> None:
    """Retiring a section on the branch breaks a pointer in a src/ file it never
    touched, and that pointer is the branch's problem."""
    _write_documents_yml(project, _OPTED_IN)
    _write_doc(project, "docs/design/a.md", "## KYO-1\n\nThe way.\n\n## KYO-2\n\nNew.\n")
    _write_doc(project, "src/thing.py", "# Why: docs/design/a.md#kyo-1\nx = 1\n")
    _branch_with(project, {"docs/design/a.md": _RETIRED})
    assert _check_branch_reasons(kyo) == [docs_check.POINTER_TO_SUPERSEDED_SECTION]


# ------------------------------------------------------- c5, c6: this repository

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The opt-in the controller adds to this repository's kyosei/documents.yml (a guarded
#: path no worker writes), appended here to a copy so the check runs against it now.
_DESIGN_OPT_IN = """\
    section_status:
      statuses: [current, amended, superseded]
"""

#: KYO-499.c5: the nine sections measured when the design set was backfilled.
_MEASURED = {
    ("docs/design/scaffold.md", "KYO-27"): "KYO-316, KYO-362",
    ("docs/design/scaffold.md", "KYO-31"): "KYO-362",
    ("docs/design/scaffold.md", "KYO-215"): "KYO-362",
    ("docs/design/scaffold.md", "KYO-316"): "KYO-362, KYO-450",
    ("docs/design/guards.md", "KYO-285"): "KYO-294, KYO-410",
    ("docs/design/guards.md", "KYO-294"): "docs/design/dispatch.md#kyo-485",
    ("docs/design/review.md", "KYO-69"): "KYO-418",
    ("docs/design/dispatch.md", "KYO-325"): "KYO-375",
    ("docs/design/doctor.md", "KYO-309"): "KYO-466",
}


#: The design set as this repository's store declares it, for a checkout without the
#: store: a public checkout carries the design documents but not ``kyosei/``.
_DESIGN_STAND_IN = (
    "documents:\n"
    "  design:\n"
    "    summary: The design record.\n"
    "    globs:\n"
    "      - docs/design/**/*.md\n" + _DESIGN_OPT_IN
)


def test_this_repositorys_design_set_passes_with_the_opt_in(tmp_path: Path) -> None:
    """KYO-499.c5: the backfill satisfies A1-A5 with `section_status:` declared on the
    `design` set. Where the store is present this reads its live kyosei/documents.yml
    rather than a copy with the block appended; a checkout without the store reads the
    same declaration of the design set from :data:`_DESIGN_STAND_IN`."""
    from kyosei.docs_parse import _sections, document_set

    live = _REPO_ROOT / "kyosei" / "documents.yml"
    if live.is_file():
        declared = live.read_text(encoding="utf-8")
        assert _DESIGN_OPT_IN.strip() in declared, "this repository's design set lost its opt-in"
        registry_dir = live.parent
    else:
        (tmp_path / "documents.yml").write_text(_DESIGN_STAND_IN, encoding="utf-8")
        registry_dir = tmp_path
    documents_in = document_set(registry_dir)
    # KYO-553: reference and guide opted in too (as id-less ssot: sets), so design is one
    # of several; what this test holds is that design's opt-in survives and checks clean.
    assert "design" in [chosen.name for chosen in documents_in.section_status]
    result = docs_check.check_docs(_REPO_ROOT, documents_in=documents_in)
    assert result.section_problems == (), [p.line() for p in result.section_problems]

    for (doc, heading), links in _MEASURED.items():
        sections = _sections((_REPO_ROOT / doc).read_text(encoding="utf-8"))
        [section] = [s for s in sections if s.heading == heading]
        assert section.metadata["status"] == "amended", (doc, heading)
        assert section.metadata["amended-by"] == links, (doc, heading)
    for doc, heading in [
        ("docs/design/doctor.md", "KYO-466"),
        ("docs/design/dispatch.md", "KYO-485"),
        ("docs/design/scaffold.md", "KYO-450"),
    ]:
        assert f"\n## {heading}\n" in (_REPO_ROOT / doc).read_text(encoding="utf-8")


def _region(text: str, start_marker: str) -> str:
    start = text.index(start_marker)
    level = start_marker.split(" ", 1)[0] + " "
    following = text.find("\n" + level, start + 1)
    while following != -1 and text[:following].count("```") % 2:
        following = text.find("\n" + level, following + 1)
    return " ".join(text[start : following if following != -1 else len(text)].split())


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("docs/reference/topics/docs-governance.md", "## Layer 3: the docs-SOT skeleton validator"),
        ("docs/design/charter.md", "## Layer 3 — Documentation SOT"),
        ("docs/design/docs.md", "## KYO-499"),
    ],
)
def test_the_documentation_states_the_rules_and_the_opt_in(path: str, marker: str) -> None:
    """KYO-499.c6: README, the charter and docs.md name the opt-in and, for the two that
    state the rules, every refusal; the charter's bullet reads Enforced."""
    text = _region((_REPO_ROOT / path).read_text(encoding="utf-8"), marker)
    assert "section_status:" in text, path
    if path == "docs/design/docs.md":
        return
    for reason in docs_check.SECTION_STATUS_REFUSALS:
        assert reason in text, f"{path} must name {reason}"
    if path == "docs/design/charter.md":
        assert "Outstanding, therefore aspirational" not in text
        assert "*Enforced.* The `amended-by`/`status` cross-check" in text


# ------------------------------------------------------------------------ KYO-505
#
# A numbered set (KYO-299) that opts into section_status: `kyo docs assemble` writes a
# bare section number into `amended_by: [..]` (KYO-493), so in a set declaring
# `numbered:` a bare number names the heading the set's `numbered.heading` renders for
# it, and A3 accepts the number in a citation spelling (docs/design/docs.md#kyo-505).

_NUMBERED = """\
documents:
  notes:
    summary: Numbered notes.
    globs: ["docs/notes/NOTES.md"]
    numbered:
      fragments: docs/notes/fragments
      heading: "## {number}."
      amends:
        field: amended_by
        status: amended
    section_status:
      statuses: [current, amended, superseded, historical]
"""

_NOTES = "docs/notes/NOTES.md"


def _numbered(project: Path, text: str, documents_yml: str = _NUMBERED) -> None:
    _write_documents_yml(project, documents_yml)
    _write_doc(project, _NOTES, text)


def _notes(link: str, cite: str = "This amends §14.", status: str = "amended") -> str:
    return (
        "# Notes\n\n"
        f"## 14. The rounding shortcut\n\n<!-- notes-meta\nstatus: {status}\n{link}\n-->\n\n"
        "The old way.\n\n"
        f"## 15. The vendor rounding shortcut, revisited (2026-10-04)\n\n{cite}\n\n"
        "## 150. Unrelated\n\nNothing.\n"
    )


@pytest.mark.parametrize(
    ("link", "status"),
    [
        ("amended_by: [15]", "amended"),
        ("amended-by: 15", "amended"),
        ("amended_by: [150, 15]", "amended"),
        ("superseded_by: [15]", "superseded"),
    ],
)
def test_a_bare_number_in_a_numbered_set_names_its_numbered_heading(
    kyo, project: Path, link: str, status: str
) -> None:
    """KYO-505.c1: either spelling, flow list or comma form. Without the numbered
    resolution every one of these is `section-link-unresolved` — 332 of them in one consumer
    project's notes — because `15` is neither the heading text nor its anchor."""
    cite = "This amends §14, and §150 is unrelated." if "150" in link else "This amends §14."
    text = _notes(link, status=status)
    if "150" in link:
        text = text.replace("## 150. Unrelated\n\nNothing.", "## 150. Unrelated\n\nSee §14.")
    _numbered(project, text.replace("This amends §14.", cite))
    assert _section_problems(kyo) == []


def test_a_bare_number_naming_no_section_names_the_heading_it_looked_for_and_the_remedy_clears_it(
    kyo, project: Path
) -> None:
    """KYO-505.c2: still unresolved, and the message says which heading was looked for."""
    _numbered(project, _notes("amended_by: [16]"))
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_UNRESOLVED
    assert "16 has no section in docs/notes/NOTES.md" in problem["says"]
    assert "`## 16.`" in problem["says"]

    # The remedy: write the section that changed this one.
    _write_doc(project, _NOTES, _notes("amended_by: [16]") + "\n## 16. Later\n\nAmends §14.\n")
    assert _section_problems(kyo) == []


def test_two_sections_carrying_one_number_resolve_to_neither(kyo, project: Path) -> None:
    _numbered(project, _notes("amended_by: [15]") + "\n## 15. Again\n\nAmends §14.\n")
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_UNRESOLVED
    assert "2 sections of docs/notes/NOTES.md carry the number 15" in problem["says"]


def test_a_set_without_numbered_resolves_a_bare_number_exactly_as_before(
    kyo, project: Path
) -> None:
    """KYO-505.c2: the same document in a set that declares no `numbered:` — `15` is
    matched against the whole heading and then its anchor, and names nothing."""
    unnumbered = "\n".join(
        line
        for line in _NUMBERED.splitlines()
        if not line.lstrip().startswith(("numbered:", "fragments:", "heading:", "amends:",
                                         "field:", "status: amended"))
    ) + "\n"
    assert "numbered" not in unnumbered
    _numbered(project, _notes("amended_by: [15]"), documents_yml=unnumbered)
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_UNRESOLVED
    assert problem["says"].endswith("15 has no section in docs/notes/NOTES.md")


@pytest.mark.parametrize(
    "cite",
    [
        "This amends §14.",
        "This amends § 14's shortcut.",
        "This amends `docs/notes/NOTES.md` §14.",
        "This amends docs/notes/NOTES.md §14.",
        "This amends `NOTES.md` §14 (relative to this document).",
        "**This amends section 14.**",
        "This amends sections 3, 9 and 14.",
        "Both (§12/§14) were wrong.",
        "Section 14b left a decision open.",
    ],
)
def test_a3_accepts_the_number_in_a_citation_spelling(kyo, project: Path, cite: str) -> None:
    """KYO-505.c3: every spelling in NUMBERED_CITE_SPELLINGS, as it is written."""
    _numbered(project, _notes("amended_by: [15]", cite))
    assert _section_problems(kyo) == []


@pytest.mark.parametrize(
    "cite",
    [
        "This touches §140 and §1400.",  # inside a longer number
        "This amends `apps/desktop/NOTES.md` §14.",  # another document's 14
        "This amends 14.",  # a bare number: also a date, a version, a count
        "see_also: [14]",  # a link list, not prose saying what changed
        "Nothing about it at all.",  # a true refusal, never suppressed
    ],
)
def test_a3_refuses_what_is_not_a_citation_of_this_section(
    kyo, project: Path, cite: str
) -> None:
    _numbered(project, _notes("amended_by: [15]", cite))
    [problem] = _section_problems(kyo)
    assert problem["reason"] == docs_check.SECTION_LINK_NOT_CITED_BACK
    assert "§14" in problem["remedy"] and "section 14" in problem["remedy"]

    # The remedy the refusal names clears it.
    _write_doc(project, _NOTES, _notes("amended_by: [15]", cite + " This amends §14."))
    assert _section_problems(kyo) == []


def test_the_cite_back_spellings_are_one_stated_list() -> None:
    """KYO-505.c3: removing a spelling from the list fails this test, and the pattern
    behind each listed spelling is held by the parametrized cases above."""
    assert docs_check.NUMBERED_CITE_SPELLINGS == ("§N", "<path> §N", "section N")


def test_kyo_docs_check_states_what_a_bare_number_names_in_the_set(
    kyo, project: Path
) -> None:
    _numbered(project, _notes("amended_by: [15]"))
    result = kyo("docs", "check")
    assert result.code == 0, result
    assert "a bare number names its `## {number}.` heading" in result.out


def test_kyo_docs_index_links_a_numbered_sets_bare_number_to_its_section(
    project: Path,
) -> None:
    """KYO-505.c4: the index resolves through the checker's corpus, so the link renders
    as a link to `## 15.`'s anchor and the chain's head is that section, not
    `15 (unresolved)`."""
    from kyosei import docs_index
    from kyosei.docs_parse import document_set

    _numbered(
        project,
        _notes("amended_by: [15]"),
        documents_yml=_NUMBERED + "      index: docs/notes/INDEX.md\n",
    )
    [item] = docs_index.indexes(project, document_set(project / "kyosei").section_status)
    anchor = "NOTES.md#15-the-vendor-rounding-shortcut-revisited-2026-10-04"
    assert f"[15]({anchor})" in item.text, item.text
    assert "(unresolved)" not in item.text


@pytest.mark.parametrize(
    ("path", "marker", "number"),
    [
        (
            "docs/reference/topics/docs-governance.md",
            "## Layer 3: the docs-SOT skeleton validator",
            "15",
        ),
        ("docs/design/docs.md", "## KYO-505", "N"),
        ("src/kyosei/defaults/documents.yml", "# A set may opt in to stricter rules", "15"),
    ],
)
def test_the_documentation_states_what_a_bare_number_names_and_each_cite_back_spelling(
    path: str, marker: str, number: str
) -> None:
    """KYO-505.c6: the defaults comment, the README and docs/design/docs.md say what a
    bare key names in a numbered set and name every spelling A3 accepts there."""
    text = (_REPO_ROOT / path).read_text(encoding="utf-8")
    region = " ".join(text[text.index(marker) :].split())
    assert "numbered.heading" in region, path
    for spelling in docs_check.NUMBERED_CITE_SPELLINGS:
        assert f"`{spelling.replace('N', number)}`" in region, f"{path} must name {spelling}"
