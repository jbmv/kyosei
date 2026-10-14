"""KYO-299: doc fragments — workers add numbered sections as per-dispatch files, and
the controller assembles them with the next number.

The defect, measured on a consumer project rather than imagined: its
``apps/server/NOTES.md`` is 27,471 lines, append-only by that project's own
rule, headed ``## 204. <title> (<date>)``, its numbers cited across the repository, and
two index files generated from it by the project's own command. Two workers who each add
"## 205." collide three times — on the number, on the tail of the file, and on both
generated indexes — and they collide *however their touches were declared*, because a
touch narrows a claim and does not move a byte. The controller's own board carries the
same shape: in one wave four of six dispatches needed ``--force`` over hot files and all
six branches merged clean.

The fixtures below are the same SHAPE as that document and none of its content: a
numbered heading, a metadata block beneath it citing other sections, a project command
that regenerates an index. What is asserted here is that Kyosei owns none of that — it
does not own the numbering format (the project declares the template), it does not
generate the index, and it never runs the project's command. It names it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from kyosei import brief as brief_module
from kyosei import docs_numbered, registry
from kyosei.contracts import (
    CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED,
    CONTRACT_NUMBERED_SECTION_BELONGS_IN_A_FRAGMENT,
)

from .conftest import git_init, invoke, new_record
from .reference_pages import OPERATING

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The declaration every test here starts from — the shape a consumer would write.
DECLARATION = {
    "documents": {
        "notes": {
            "summary": "The append-only engineering notes, one numbered section each.",
            "globs": ["apps/server/NOTES.md"],
            "numbered": {
                "fragments": "apps/server/notes-fragments",
                "heading": "## {number}.",
                "after": ["make notes-index"],
            },
        }
    }
}

#: A document of the same shape as the consumer's: numbered headings, a metadata block
#: under each citing other sections by number.
NOTES = """# Engineering notes

## 203. An earlier finding (2026-09-01)
<!--notes-meta
area: pipeline
status: current
see_also: [198]
-->

An earlier finding.

## 204. The one before this wave (2026-09-16)
<!--notes-meta
area: data-quality
status: current
see_also: [203]
-->

The section this wave's fragments follow.
"""


def declare(project: Path, declaration: dict | None = None) -> Path:
    """Write ``documents.yml`` into an in-repo project's store and return its path."""
    path = project / "kyosei" / "documents.yml"
    path.write_text(
        yaml.safe_dump(declaration if declaration is not None else DECLARATION, sort_keys=False),
        encoding="utf-8",
    )
    return path


def seed_notes(project: Path, text: str = NOTES) -> Path:
    path = project / "apps" / "server" / "NOTES.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    (project / "apps" / "server" / "notes-fragments").mkdir(parents=True, exist_ok=True)
    return path


def write_fragment(project: Path, name: str, body: str) -> Path:
    path = project / "apps" / "server" / "notes-fragments" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def section(title: str) -> str:
    """A fragment that uses the placeholder in its heading AND inside its own body —
    the case the placeholder's spelling exists for."""
    return (
        f"## {{{{next}}}}. {title}\n"
        "<!--notes-meta\n"
        "area: pipeline\n"
        "status: current\n"
        "see_also: [204]\n"
        "-->\n"
        "\n"
        f"Section {{{{next}}}} is this one, and it supersedes nothing.\n"
    )


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


# ------------------------------------------------------------------------ c1


def test_documents_yml_declares_a_numbered_append_only_document(project: Path):
    """c1: the declaration is read, and every field it carries has a reader."""
    declare(project)
    seed_notes(project)
    found = docs_numbered.numbered_documents(project / "kyosei", project)
    assert [document.path for document in found] == ["apps/server/NOTES.md"]
    document = found[0]
    assert document.set_name == "notes"
    assert document.fragments_dir == "apps/server/notes-fragments"
    assert document.heading == "## {number}."
    assert document.after == ("make notes-index",)
    # The three derived values every consumer uses, derived HERE and nowhere else.
    assert document.fragment_path("TST-1.d1", "retry-budget") == (
        "apps/server/notes-fragments/TST-1.d1--retry-budget.md"
    )
    assert docs_numbered.last_number(NOTES, document.heading) == 204
    assert docs_numbered.heading_pattern(document.heading).match("## 204. Anything at all")


def test_a_project_that_declares_nothing_has_no_numbered_documents(project: Path):
    """The opt-out, and it is the state every project is in until it declares one."""
    assert docs_numbered.numbered_documents(project / "kyosei", project) == []
    declare(
        project,
        {"documents": {"notes": {"summary": "Just a document set.", "globs": ["*.md"]}}},
    )
    assert docs_numbered.numbered_documents(project / "kyosei", project) == []


@pytest.mark.parametrize(
    ("numbered", "expected"),
    [
        (
            {"fragments": "f", "heading": "## {number}.", "regenerate": "make index"},
            "unknown key(s) regenerate",
        ),
        (
            {"fragments": "../outside", "heading": "## {number}."},
            "must not climb out of it with '..'",
        ),
        (
            {"fragments": "/etc/notes", "heading": "## {number}."},
            "must be relative to the project root",
        ),
        ({"fragments": "f", "heading": "## Section:"}, "must carry {number} exactly once"),
        (
            {"fragments": "f", "heading": "## {number}. and {number} again"},
            "must carry {number} exactly once",
        ),
        ({"fragments": "f", "heading": "{number}. no hash"}, "must begin with one to six '#'"),
        ({"heading": "## {number}."}, "is missing fragments, which is required"),
        ({"fragments": "f"}, "is missing heading, which is required"),
        ({"fragments": "f", "heading": "## {number}.", "after": []}, "must not be an empty list"),
        ({"fragments": "f", "heading": "## {number}.", "after": "make index"}, "after must be"),
    ],
)
def test_a_bad_numbered_declaration_is_refused_by_name(
    project: Path, kyo, numbered: dict, expected: str
):
    """c1: every way of getting the declaration wrong is a named error when it is READ,
    not a silence that shows up as a mis-numbered document three waves later."""
    declare(
        project,
        {
            "documents": {
                "notes": {
                    "summary": "The notes.",
                    "globs": ["apps/server/NOTES.md"],
                    "numbered": numbered,
                }
            }
        },
    )
    result = kyo("docs", "check")
    assert result.code != 0, result
    assert expected in (result.out + result.err), result


def test_the_declaration_is_refused_before_it_can_be_acted_on(project: Path, kyo):
    """c1's remedy half: the refusal clears when the declaration is corrected, and the
    corrected one is what every consumer then sees."""
    seed_notes(project)
    declare(
        project,
        {
            "documents": {
                "notes": {
                    "summary": "The notes.",
                    "globs": ["apps/server/NOTES.md"],
                    "numbered": {"fragments": "apps/server/notes-fragments"},
                }
            }
        },
    )
    assert kyo("docs", "assemble").code != 0
    declare(project)
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    assert "nothing to assemble" in result.out


def test_the_numbered_field_is_not_a_path_dialect(project: Path):
    """KYO-142's rule still holds: ``numbered`` is a mapping, not a pattern field, so it
    does not inherit either dialect's reserved name — and the dimension still builds."""
    assert "numbered" in registry.DOCUMENT.optional_fields
    assert registry.DOCUMENT.optional_fields["numbered"] is registry.numbered_sections
    assert registry.numbered_sections is not registry.relative_globs
    assert registry.numbered_sections is not registry.match_globs


# ------------------------------------------------------------------------ c2


def brief_for(project: Path, kyo) -> str:
    record = new_record(kyo, kind="task", title="Add a notes section", criteria=["c"])
    git_init(project)
    task = project / "task.txt"
    task.write_text("write the section\n", encoding="utf-8")
    out = project / "brief.md"
    result = kyo(
        "dispatch",
        record,
        "--branch",
        "feature",
        "--task-file",
        str(task),
        "--brief-out",
        str(out),
    )
    assert result.code == 0, result
    return out.read_text(encoding="utf-8")


def test_the_brief_states_the_fragment_path_and_the_two_prohibitions(project: Path, kyo):
    """c2: a frozen brief for a project with such a document says, per document, where
    the fragment goes, what heads it, what the worker must not do, and what is still an
    ordinary edit."""
    declare(project)
    seed_notes(project)
    text = brief_for(project, kyo)
    # the path, with this dispatch's own id in it
    assert "apps/server/notes-fragments/TST-1.d1--<short-slug>.md" in text
    # the placeholder, in the heading and stated as usable inside the section
    assert "`## {{next}}. <title>`" in text
    assert "anywhere inside the section that cites this" in text
    # the two prohibitions
    assert "Do not append a numbered section to `apps/server/NOTES.md` itself" in text
    assert "do not regenerate its index" in text
    # and the thing that is still an ordinary edit
    assert "Editing an EXISTING section of `apps/server/NOTES.md` is an ordinary edit" in text
    # the project's own follow-up command is named as the CONTROLLER's, not the worker's
    assert "make notes-index" in text


def test_a_brief_for_a_project_with_no_numbered_document_says_nothing(project: Path, kyo):
    """The block is generated, so it must also be absent — an instruction printed to a
    project that has no such document is noise in every brief it ever writes."""
    text = brief_for(project, kyo)
    assert "Documents that grow by numbered section" not in text


def test_the_brief_block_renders_from_the_declaration_alone():
    """The unit beneath the two above: nothing about the block is hand-typed per
    dispatch, and an empty declaration renders as nothing at all."""
    assert brief_module._doc_fragments_block("TST-1.d1", []) == []
    lines = brief_module._doc_fragments_block(
        "TST-9.d2",
        [
            {
                "path": "docs/LOG.md",
                "set": "log",
                "fragments": "docs/log-fragments",
                "heading": "### Entry {number}:",
                "after": [],
                "placeholder": "{{next}}",
                "placeholder_heading": "### Entry {{next}}:",
                "fragment_path": "docs/log-fragments/TST-9.d2--<short-slug>.md",
            }
        ],
    )
    text = "\n".join(lines)
    assert "docs/log-fragments/TST-9.d2--<short-slug>.md" in text
    assert "`### Entry {{next}}: <title>`" in text


def test_the_brief_and_readme_say_one_file_per_section():
    """KYO-486.c2: a worker with several sections is told several files, never "they go
    in that one file, one after another" — which assembly would number as one section."""
    lines = brief_module._doc_fragments_block(
        "TST-9.d2",
        [
            {
                "path": "docs/LOG.md",
                "set": "log",
                "fragments": "docs/log-fragments",
                "heading": "### Entry {number}:",
                "after": [],
                "placeholder": "{{next}}",
                "placeholder_heading": "### Entry {{next}}:",
                "fragment_path": "docs/log-fragments/TST-9.d2--<short-slug>.md",
            }
        ],
    )
    text = " ".join(lines)
    assert "one file per new section" in text
    assert "write several files, one per section, each with a distinct slug" in text
    assert "one after another" not in text
    assert "one file for this whole dispatch" not in text

    readme = OPERATING.read_text(encoding="utf-8")
    start = readme.index("### Doc fragments")
    guide = " ".join(readme[start : readme.index("\n### ", start + 3)].split())
    assert "one file per new section" in guide
    assert "several files, with distinct slugs, when a dispatch adds several" in guide
    assert "one file for the whole dispatch" not in guide

    shipped = " ".join(
        (REPO_ROOT / "src/kyosei/defaults/documents.yml").read_text(encoding="utf-8").split()
    )
    assert "ONE fragment file per new section" in shipped


# ------------------------------------------------------------------------ c3


def test_assemble_appends_with_the_next_number_and_names_the_follow_up(project: Path, kyo):
    """c3: the whole act, end to end, through the CLI."""
    declare(project)
    notes = seed_notes(project)
    write_fragment(project, "TST-1.d1--retry-budget.md", section("Retry budget (2026-09-18)"))
    result = kyo("docs", "assemble")
    assert result.code == 0, result

    text = notes.read_text(encoding="utf-8")
    assert "## 205. Retry budget (2026-09-18)" in text
    # the placeholder is replaced THROUGHOUT the section, not only in the heading
    assert "Section 205 is this one" in text
    assert "{{next}}" not in text
    # the fragment is gone
    assert not (project / "apps/server/notes-fragments/TST-1.d1--retry-budget.md").exists()
    # which dispatch became which number, then the project's own command, NAMED
    assert "TST-1.d1" in result.out
    assert "->  205" in result.out
    assert "make notes-index" in result.out
    assert "runs none of them" in result.out


def test_assemble_never_runs_the_projects_commands(project: Path, kyo):
    """c3, and the property the record says is not ours to trade away. The declared
    follow-up command would leave a file behind; it does not, because it is never run."""
    sentinel = project / "index-was-generated"
    declaration = {
        "documents": {
            "notes": {
                "summary": "The notes.",
                "globs": ["apps/server/NOTES.md"],
                "numbered": {
                    "fragments": "apps/server/notes-fragments",
                    "heading": "## {number}.",
                    "after": [f"touch {sentinel}"],
                },
            }
        }
    }
    declare(project, declaration)
    seed_notes(project)
    write_fragment(project, "TST-1.d1--a.md", section("A finding"))
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    assert f"touch {sentinel}" in result.out, "the command must be NAMED"
    assert not sentinel.exists(), "the command must not have been RUN"


def test_dry_run_prints_the_same_plan_and_writes_nothing(project: Path, kyo):
    """c3: ``--dry-run`` is the same plan, not a different code path."""
    declare(project)
    notes = seed_notes(project)
    fragment = write_fragment(project, "TST-1.d1--a.md", section("A finding"))
    before = notes.read_text(encoding="utf-8")

    dry = kyo("docs", "assemble", "--dry-run")
    assert dry.code == 0, dry
    assert notes.read_text(encoding="utf-8") == before
    assert fragment.exists()
    assert "--dry-run: nothing was written" in dry.out

    wet = kyo("docs", "assemble")
    assert wet.code == 0, wet
    # the same assignment, fragment for fragment and number for number
    assert "->  205" in dry.out and "->  205" in wet.out
    assert not fragment.exists()


def test_a_second_run_with_no_fragments_changes_nothing_and_says_so(project: Path, kyo):
    """c3: idempotent by construction — the fragments are the work queue, and an empty
    queue is an outcome with words rather than a silent exit 0."""
    declare(project)
    notes = seed_notes(project)
    write_fragment(project, "TST-1.d1--a.md", section("A finding"))
    assert kyo("docs", "assemble").code == 0
    after_first = notes.read_text(encoding="utf-8")

    second = kyo("docs", "assemble")
    assert second.code == 0, second
    assert notes.read_text(encoding="utf-8") == after_first
    assert "0 fragment(s) — nothing to assemble" in second.out
    assert "last numbered section 205" in second.out


def test_fragments_are_taken_in_dispatch_creation_order_then_by_file_name(project: Path):
    """c3's ordering rule, unit-tested so the assertion is about the RULE and not about
    which of two dispatches a wall clock happened to stamp first.

    File-name order here is the REVERSE of dispatch-creation order, so a run that
    ordered by name would number them the other way round and this would fail.
    """
    declare(project)
    seed_notes(project)
    write_fragment(project, "TST-1.d1--zebra.md", section("Written second"))
    write_fragment(project, "TST-2.d1--alpha.md", section("Written first"))
    plan = docs_numbered.assemble_plan(
        project / "kyosei",
        project,
        created_at={
            "TST-2.d1": "2026-09-18T10:00:00.000000Z",
            "TST-1.d1": "2026-09-18T11:00:00.000000Z",
        },
    )
    assert [(a.dispatch_id, a.number) for a in plan.assignments] == [
        ("TST-2.d1", 205),
        ("TST-1.d1", 206),
    ]
    assert all(a.ordered_by_dispatch for a in plan.assignments)


def test_a_fragment_with_no_resolvable_dispatch_is_still_assembled_and_says_so(project: Path):
    """c3, and the record's own question: a fragment written by hand, or by a
    controller, must still be assemblable — ordered after the resolvable ones, and
    saying which it got."""
    declare(project)
    seed_notes(project)
    write_fragment(project, "aaa-by-hand.md", section("Written by a human"))
    write_fragment(project, "TST-1.d1--from-a-worker.md", section("Written by a worker"))
    plan = docs_numbered.assemble_plan(
        project / "kyosei",
        project,
        created_at={"TST-1.d1": "2026-09-18T10:00:00.000000Z"},
    )
    assert [(a.fragment, a.number, a.ordered_by_dispatch) for a in plan.assignments] == [
        ("apps/server/notes-fragments/TST-1.d1--from-a-worker.md", 205, True),
        ("apps/server/notes-fragments/aaa-by-hand.md", 206, False),
    ]


def test_a_document_with_no_numbered_heading_yet_starts_at_one(project: Path, kyo):
    declare(project)
    seed_notes(project, "# Engineering notes\n\nNothing numbered yet.\n")
    write_fragment(project, "TST-1.d1--first.md", section("The first finding"))
    assert kyo("docs", "assemble").code == 0
    text = (project / "apps/server/NOTES.md").read_text(encoding="utf-8")
    assert "## 1. The first finding" in text


def test_a_numbered_heading_inside_a_code_fence_is_not_the_last_section(project: Path):
    """The README of a project that HAS such a document quotes its own format. Reading
    the example as the document's tail would number the next section from a fence."""
    text = NOTES + "\n```\n## 999. An example of the format\n```\n"
    assert docs_numbered.last_number(text, "## {number}.") == 204


# ------------------------------------------------------------------------ c4


def test_a_fragment_with_no_placeholder_heading_is_refused_and_moving_it_clears(
    project: Path, kyo
):
    """c4: refused by name, before anything is written, and the named remedy clears it."""
    declare(project)
    notes = seed_notes(project)
    before = notes.read_text(encoding="utf-8")
    bad = write_fragment(project, "TST-1.d1--a.md", "## 205. I picked my own number\n\nbody\n")

    result = kyo("docs", "assemble")
    assert result.code == 2, result
    message = result.out + result.err
    assert CONTRACT_NUMBERED_SECTION_BELONGS_IN_A_FRAGMENT not in message
    assert CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED in message
    assert "TST-1.d1--a.md" in message
    assert "remedy: head the section `## {{next}}. <title>`" in message
    assert notes.read_text(encoding="utf-8") == before, "nothing may be written"
    assert bad.exists(), "and no fragment may be removed"

    # the remedy, run exactly as named
    bad.write_text("## {{next}}. I picked my own number\n\nbody\n", encoding="utf-8")
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared
    assert "## 205. I picked my own number" in notes.read_text(encoding="utf-8")


def test_a_fragment_naming_an_undeclared_document_is_refused_and_naming_a_real_one_clears(
    project: Path, kyo
):
    """c4: a target document that is not declared."""
    declaration = {
        "documents": {
            "notes": {
                "summary": "Two numbered documents, one fragments directory.",
                "globs": ["apps/server/NOTES.md", "apps/server/DECISIONS.md"],
                "numbered": {
                    "fragments": "apps/server/notes-fragments",
                    "heading": "## {number}.",
                },
            }
        }
    }
    declare(project, declaration)
    notes = seed_notes(project)
    decisions = project / "apps" / "server" / "DECISIONS.md"
    decisions.write_text("# Decisions\n\n## 7. An earlier decision\n\nbody\n", encoding="utf-8")
    fragment = write_fragment(
        project,
        "TST-1.d1--a.md",
        "<!--kyosei-fragment document: apps/server/CHANGELOG.md -->\n" + section("A finding"),
    )

    result = kyo("docs", "assemble")
    assert result.code == 2, result
    message = result.out + result.err
    assert "apps/server/CHANGELOG.md" in message
    assert "is not one of the documents the notes set enumerates" in message
    assert "{{next}}" not in notes.read_text(encoding="utf-8")

    fragment.write_text(
        "<!--kyosei-fragment document: apps/server/DECISIONS.md -->\n" + section("A finding"),
        encoding="utf-8",
    )
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared
    assert "## 8. A finding" in decisions.read_text(encoding="utf-8")
    assert "A finding" not in notes.read_text(encoding="utf-8")


def test_a_fragment_that_names_no_document_in_an_ambiguous_set_is_refused(project: Path, kyo):
    """c4, the other half of the same refusal: which document is not decidable."""
    declare(
        project,
        {
            "documents": {
                "notes": {
                    "summary": "Two numbered documents.",
                    "globs": ["apps/server/NOTES.md", "apps/server/DECISIONS.md"],
                    "numbered": {
                        "fragments": "apps/server/notes-fragments",
                        "heading": "## {number}.",
                    },
                }
            }
        },
    )
    seed_notes(project)
    (project / "apps" / "server" / "DECISIONS.md").write_text("# D\n\n## 7. x\n", encoding="utf-8")
    fragment = write_fragment(project, "TST-1.d1--a.md", section("A finding"))

    result = kyo("docs", "assemble")
    assert result.code == 2, result
    assert "this fragment names none" in (result.out + result.err)

    fragment.write_text(
        "<!--kyosei-fragment document: apps/server/NOTES.md -->\n" + section("A finding"),
        encoding="utf-8",
    )
    assert kyo("docs", "assemble").code == 0


def test_a_placeholder_left_in_the_document_is_refused_and_replacing_it_clears(
    project: Path, kyo
):
    """c4: a placeholder that would remain in a document after assembly. It means a
    number nobody will ever write, and one left behind is invisible forever after."""
    declare(project)
    notes = seed_notes(project, NOTES + "\nAn earlier section cites {{next}} by mistake.\n")
    before = notes.read_text(encoding="utf-8")
    write_fragment(project, "TST-1.d1--a.md", section("A finding"))

    result = kyo("docs", "assemble")
    assert result.code == 2, result
    message = result.out + result.err
    assert "would still carry {{next}} after this assembly" in message
    assert "replace each remaining {{next}} with the number" in message
    assert notes.read_text(encoding="utf-8") == before

    notes.write_text(before.replace("{{next}}", "203"), encoding="utf-8")
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared
    assert "## 205. A finding" in notes.read_text(encoding="utf-8")


def test_one_refused_fragment_voids_the_whole_run(project: Path, kyo):
    """c4's "writes nothing at all": the good fragment is not assembled either, and its
    document is untouched. Partial assembly is the one outcome there is no way back from.
    """
    declare(project)
    notes = seed_notes(project)
    before = notes.read_text(encoding="utf-8")
    good = write_fragment(project, "TST-1.d1--good.md", section("A good finding"))
    write_fragment(project, "TST-2.d1--bad.md", "no heading at all\n")

    result = kyo("docs", "assemble")
    assert result.code == 2, result
    assert notes.read_text(encoding="utf-8") == before
    assert good.exists()
    assert "not the documents that had no problem" in (result.out + result.err)


# ------------------------------------------------------------- KYO-486: one section each


def test_a_fragment_carrying_two_placeholder_headings_is_refused_in_the_plan(project: Path):
    """KYO-486.c1, from KYO-480.d1's reproduction: two ``## {{next}}.`` headings in one
    fragment were both given the one number the fragment received — ``## 205. first``
    and ``## 205. second`` — with ``plan.ok`` true. The refusal lives in the plan, so
    ``--dry-run`` shows it too, and the plan carries nothing to apply."""
    declare(project)
    seed_notes(project)
    write_fragment(project, "TST-1.d1--both.md", section("first") + "\n" + section("second"))
    plan = docs_numbered.assemble_plan(project / "kyosei", project)
    assert not plan.ok
    assert [r.reason for r in plan.refusals] == [docs_numbered.FRAGMENT_SEVERAL_SECTIONS]
    assert plan.refusals[0].fragment == "apps/server/notes-fragments/TST-1.d1--both.md"
    assert not plan.assignments and not plan.new_text and not plan.removes


def test_a_placeholder_heading_quoted_in_a_fence_is_not_a_second_section(project: Path):
    """The count reads what assembly would number: a fragment quoting the heading format
    inside a code fence is one section, as :func:`numbered_headings` would read it."""
    declare(project)
    seed_notes(project)
    body = section("About the format") + "\n```\n## {{next}}. an example\n```\n"
    write_fragment(project, "TST-1.d1--format.md", body)
    plan = docs_numbered.assemble_plan(project / "kyosei", project)
    assert plan.ok, plan.refusals
    assert [a.number for a in plan.assignments] == [205]


def test_two_sections_in_one_fragment_are_refused_and_splitting_them_clears(
    project: Path, kyo
):
    """KYO-486.c1 through the command: refused as a whole run, nothing written, the
    fragment named — and the named remedy, one file per section with distinct slugs,
    run exactly as written, clears it and numbers them consecutively."""
    declare(project)
    notes = seed_notes(project)
    before = notes.read_text(encoding="utf-8")
    good = write_fragment(project, "TST-2.d1--unrelated.md", section("Unrelated"))
    both = write_fragment(
        project, "TST-1.d1--both.md", section("first") + "\n" + section("second")
    )

    dry = kyo("docs", "assemble", "--dry-run")
    assert dry.code == 2, dry
    assert "TST-1.d1--both.md" in dry.out + dry.err

    result = kyo("docs", "assemble")
    assert result.code == 2, result
    message = result.out + result.err
    assert CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED in message
    assert "TST-1.d1--both.md" in message
    assert "carries 2 headings" in message
    assert "remedy: split it into one file per section" in message
    assert "<dispatch-id>--<slug>.md" in message
    assert notes.read_text(encoding="utf-8") == before, "nothing may be written"
    assert both.exists() and good.exists(), "and no fragment may be removed"

    # the remedy, run exactly as named
    both.unlink()
    write_fragment(project, "TST-1.d1--first.md", section("first"))
    write_fragment(project, "TST-1.d1--second.md", section("second"))
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared
    text = notes.read_text(encoding="utf-8")
    numbers = [n for n, _ in docs_numbered.numbered_headings(text, "## {number}.")]
    assert numbers == [203, 204, 205, 206, 207], "every section its own number"


def test_several_fragments_from_one_dispatch_number_in_file_name_order(project: Path):
    """KYO-486.c3: a dispatch adding several sections writes several files; they are
    numbered in file-name order, AFTER dispatch-creation order has placed the dispatch.

    The later-created dispatch's file sorts first by name, and the earlier dispatch's two
    files are named against their writing order, so a rule ordering by name alone, or
    by anything but name within a dispatch, fails this.
    """
    declare(project)
    seed_notes(project)
    write_fragment(project, "TST-1.d1--alpha.md", section("TST-1's, created later"))
    write_fragment(project, "TST-2.d1--yankee.md", section("TST-2's second"))
    write_fragment(project, "TST-2.d1--bravo.md", section("TST-2's first"))
    plan = docs_numbered.assemble_plan(
        project / "kyosei",
        project,
        created_at={
            "TST-2.d1": "2026-09-18T10:00:00.000000Z",
            "TST-1.d1": "2026-09-18T11:00:00.000000Z",
        },
    )
    assert plan.ok, plan.refusals
    assert [(a.fragment.rsplit("/", 1)[1], a.number) for a in plan.assignments] == [
        ("TST-2.d1--bravo.md", 205),
        ("TST-2.d1--yankee.md", 206),
        ("TST-1.d1--alpha.md", 207),
    ]


# ------------------------------------------------------------------------ c5


def branch_adding(project: Path, *, to_document: str, edit_only: bool = False) -> None:
    """Commit a change on branch ``feature`` cut from ``main``."""
    git(project, "checkout", "-q", "-b", "feature")
    notes = project / "apps" / "server" / "NOTES.md"
    if edit_only:
        notes.write_text(
            notes.read_text(encoding="utf-8").replace(
                "An earlier finding.", "An earlier finding, corrected in place."
            ),
            encoding="utf-8",
        )
    else:
        notes.write_text(notes.read_text(encoding="utf-8") + to_document, encoding="utf-8")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "work")


def test_check_branch_refuses_a_numbered_heading_written_into_the_document(project: Path, kyo):
    """c5: and the refusal names the fragment path the section belongs in."""
    declare(project)
    seed_notes(project)
    git_init(project)
    git(project, "branch", "-M", "main")
    branch_adding(project, to_document="\n## 205. I wrote this by hand\n\nbody\n")

    result = kyo("check-branch", "--against", "main")
    assert result.code == 2, result
    message = result.out + result.err
    assert CONTRACT_NUMBERED_SECTION_BELONGS_IN_A_FRAGMENT in message
    assert "## 205. I wrote this by hand" in message
    assert "apps/server/notes-fragments/<your-dispatch-id>--<short-slug>.md" in message
    assert "kyo docs assemble" in message
    assert "not refused: EDITING an existing section" in message


def test_moving_the_section_into_a_fragment_clears_the_refusal(project: Path, kyo):
    """c5's remedy half, run exactly as the refusal names it."""
    declare(project)
    notes = seed_notes(project)
    git_init(project)
    git(project, "branch", "-M", "main")
    branch_adding(project, to_document="\n## 205. I wrote this by hand\n\nbody\n")
    assert kyo("check-branch", "--against", "main").code == 2

    notes.write_text(NOTES, encoding="utf-8")
    write_fragment(project, "TST-1.d1--by-hand.md", section("I wrote this by hand"))
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "moved into a fragment")

    result = kyo("check-branch", "--against", "main")
    assert result.code == 0, result


def test_a_branch_that_only_edits_existing_sections_is_not_refused(project: Path, kyo):
    """c5: editing an existing section stays an ordinary edit. A guard that refused
    those would refuse the normal maintenance of the document it protects."""
    declare(project)
    seed_notes(project)
    git_init(project)
    git(project, "branch", "-M", "main")
    branch_adding(project, to_document="", edit_only=True)

    result = kyo("check-branch", "--against", "main")
    assert result.code == 0, result


def test_check_branch_is_silent_for_a_project_that_declares_nothing(project: Path, kyo):
    """The opt-out reaches the guard too: no declaration, no new way to be refused."""
    seed_notes(project)
    git_init(project)
    git(project, "branch", "-M", "main")
    branch_adding(project, to_document="\n## 205. I wrote this by hand\n\nbody\n")
    assert kyo("check-branch", "--against", "main").code == 0


def test_the_guard_reads_added_headings_and_not_the_diffs_line_numbers():
    """The unit beneath the two above. An edit ABOVE an existing heading moves every
    later heading's line number without adding one; a line-based answer would refuse it.
    """
    template = "## {number}."
    before = NOTES
    edited = NOTES.replace("An earlier finding.", "An earlier finding.\n\nOne more paragraph.")
    assert docs_numbered.added_numbered_headings(before, edited, template) == []
    added = edited + "\n## 205. New\n"
    assert docs_numbered.added_numbered_headings(before, added, template) == ["## 205. New"]


# ------------------------------------------------------------------------ c6


def test_two_branches_each_add_a_section_merge_clean_and_number_consecutively(
    project: Path, kyo
):
    """c6, end to end in a real repository. This is the whole point of the record: the
    two branches below would have conflicted three ways if each had written its own
    "## 205." into the document, and they do not touch the document at all.
    """
    declare(project)
    notes = seed_notes(project)
    git_init(project)
    git(project, "branch", "-M", "main")

    for branch, name, title in (
        ("worker-one", "TST-1.d1--retry-budget.md", "Retry budget"),
        ("worker-two", "TST-2.d1--archive-close.md", "Archive close"),
    ):
        git(project, "checkout", "-q", "main")
        git(project, "checkout", "-q", "-b", branch)
        write_fragment(project, name, section(title))
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", branch)
        # each branch is clean by the guard that would have caught the other approach
        assert kyo("check-branch", branch, "--against", "main").code == 0

    git(project, "checkout", "-q", "main")
    for branch in ("worker-one", "worker-two"):
        merged = subprocess.run(
            ["git", "merge", "--no-edit", branch],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert merged.returncode == 0, merged.stderr
        assert "CONFLICT" not in merged.stdout

    result = kyo("docs", "assemble")
    assert result.code == 0, result
    text = notes.read_text(encoding="utf-8")
    numbers = [number for number, _ in docs_numbered.numbered_headings(text, "## {number}.")]
    assert numbers == [203, 204, 205, 206]
    assert "## 205." in text and "## 206." in text
    assert {"Retry budget", "Archive close"} == {
        line.split(". ", 1)[1]
        for line in text.splitlines()
        if line.startswith("## 205.") or line.startswith("## 206.")
    }
    assert "{{next}}" not in text
    assert sorted((project / "apps/server/notes-fragments").iterdir()) == []


# ------------------------------------------------------------------------ c7


@pytest.mark.parametrize(
    "path",
    [
        "docs/reference/topics/operating.md",
        "src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md",
        "src/kyosei/defaults/documents.yml",
    ],
)
def test_the_consumer_documentation_names_the_command(path: str):
    """c7: a mechanism nobody is told about is a mechanism nobody adopts."""
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert "kyo docs assemble" in text, f"{path} must name the controller's command"
    assert "numbered:" in text, f"{path} must name the declaration"




def test_the_readme_operating_guide_says_when_to_run_it():
    """c7: the adoption story is the declaration, what workers are told, the command,
    and WHEN — after the wave's merges, before the project's own index command."""
    text = OPERATING.read_text(encoding="utf-8")
    start = text.index("### Doc fragments")
    end = text.index("###", start + 3)
    guide = text[start:end]
    for expected in (
        "numbered:",
        "fragments:",
        "heading:",
        "after:",
        "kyo docs assemble",
        "after the wave",
        "{{next}}",
    ):
        assert expected in guide, f"the operating guide must say {expected!r}"


# ------------------------------------------------------------------ the CLI itself


def test_assemble_is_a_docs_action_with_a_dry_run(project: Path, kyo):
    """KYO-138's reference is generated from the parser, so the flag has to be on it."""
    result = invoke(["docs", "--help"])
    assert "assemble" in result.out
    assert "--dry-run" in result.out


def test_a_project_with_no_numbered_document_is_told_so_rather_than_nothing(
    project: Path, kyo
):
    """The no-documents state has words, for `kyo docs check`'s reason: a silent exit 0
    is indistinguishable from a run that assembled something."""
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    assert "declares no numbered documents" in result.out
    assert "numbered:" in result.out


# ================================================================ KYO-493: amends
#
# A fragment's header names earlier sections it amends; `kyo docs assemble` writes the
# new number onto each one's back-link and sets its status, all-or-nothing.

import json  # noqa: E402

AMENDS = {"field": "amended_by", "status": "amended"}


def amends_declaration(amends: object = AMENDS) -> dict:
    """:data:`DECLARATION` with ``numbered.amends`` set to ``amends``."""
    declaration = yaml.safe_load(yaml.safe_dump(DECLARATION))
    declaration["documents"]["notes"]["numbered"]["amends"] = amends
    return declaration


def amending(title: str, header: str) -> str:
    """A fragment whose first line is ``header``."""
    return f"{header}\n{section(title)}"


def section_block(text: str, number: int) -> str:
    """Section ``number`` of a document, heading through the line before the next."""
    start = text.index(f"## {number}.")
    following = text.find("\n## ", start + 1)
    return text[start : following if following != -1 else len(text)]


# ------------------------------------------------------------------------ c1


@pytest.mark.parametrize(
    ("amends", "expected"),
    [
        ({"field": "amended_by"}, "amends is missing status, which is required"),
        ({"status": "amended"}, "amends is missing field, which is required"),
        ({"field": "see_also", "status": "amended"}, "amends.field 'see_also' must be one of"),
        ({"field": "amended_by", "status": "superseded"}, "is a dead status"),
        ({"field": "amended_by", "status": "Historical"}, "is a dead status"),
        ({"field": "amended_by", "status": ""}, "amends.status must be a non-empty"),
        (
            {"field": "amended_by", "status": "amended", "also": "x"},
            "amends carries unknown key(s) also",
        ),
        ("amended_by", "amends must be a mapping holding exactly field and status"),
    ],
)
def test_a_bad_amends_declaration_is_refused_by_name_and_correcting_it_clears(
    project: Path, kyo, amends: object, expected: str
):
    """KYO-493.c1: every wrong `numbered.amends` is refused when the file is read, by
    name, and declaring it as the refusal says clears it."""
    seed_notes(project)
    declare(project, amends_declaration(amends))
    refused = kyo("docs", "assemble")
    assert refused.code != 0, refused
    assert expected in (refused.out + refused.err), refused

    declare(project, amends_declaration())
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared


def test_both_spellings_of_the_back_link_are_accepted(project: Path):
    """KYO-493.c1: `amended-by` and `amended_by`, Kyosei's key and its alias."""
    for field in registry.NUMBERED_AMENDS_FIELDS:
        assert registry.numbered_sections(
            {
                "fragments": "f",
                "heading": "## {number}.",
                "amends": {"field": field, "status": "amended"},
            }
        ) is None
    assert set(registry.NUMBERED_AMENDS_FIELDS) == {"amended-by", "amended_by"}
    seed_notes(project)
    declare(project, amends_declaration({"field": "amended-by", "status": "amended"}))
    (document,) = docs_numbered.numbered_documents(project / "kyosei", project)
    assert (document.amends_field, document.amends_status) == ("amended-by", "amended")


# ------------------------------------------------------------------------ c2


@pytest.mark.parametrize(
    "header", ["amends: 203, 204", "amends: [203, 204]", "amends: [203,204, 203]"]
)
def test_assemble_writes_the_back_link_and_status_the_header_names(
    project: Path, kyo, header: str
):
    """KYO-493.c2: inserted after `status:` in the declared spelling, status set, and
    the header line is not carried into the document."""
    declare(project, amends_declaration())
    notes = seed_notes(project)
    write_fragment(
        project, "TST-1.d1--fix.md", amending("A correction", f"<!-- kyosei-fragment {header} -->")
    )
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    text = notes.read_text(encoding="utf-8")
    for number in (203, 204):
        block = section_block(text, number)
        assert "status: amended\namended_by: [205]\n" in block, block
    assert "kyosei-fragment" not in text
    assert "## 205. A correction" in text


def test_an_existing_back_link_is_extended_in_its_own_spelling(project: Path, kyo):
    """KYO-493.c2: a back-link already present keeps its spelling and gains the number;
    a status already at the declared value is left alone."""
    declare(project, amends_declaration())
    notes = seed_notes(
        project,
        NOTES.replace(
            "status: current\nsee_also: [203]",
            "status: amended\namended-by: [188,201]\nsee_also: [203]",
        ),
    )
    write_fragment(
        project, "TST-1.d1--fix.md", amending("A correction", HEADER_204)
    )
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    block = section_block(notes.read_text(encoding="utf-8"), 204)
    assert "amended-by: [188, 201, 205]" in block
    assert "amended_by" not in block
    assert "status: amended (unchanged)" in result.out


def test_a_header_naming_document_and_amends_in_either_order_is_read():
    """KYO-493.c2: both keys, either order, once each."""
    (document,) = [
        docs_numbered.NumberedDocument(
            set_name="notes",
            path="apps/server/NOTES.md",
            fragments_dir="f",
            heading="## {number}.",
            siblings=("apps/server/NOTES.md",),
        )
    ]
    for header in (
        "<!-- kyosei-fragment document: apps/server/NOTES.md amends: 178 -->",
        "<!--kyosei-fragment amends: [178] document: apps/server/NOTES.md-->",
    ):
        text = f"{header}\n## {{{{next}}}}. x\n"
        fragment = docs_numbered.fragment_from_text("f/x.md", text, [document])
        assert fragment.header_problem is None, fragment.header_problem
        assert fragment.amends == (178,)
        assert fragment.declared_document == "apps/server/NOTES.md"
        assert "kyosei-fragment" not in fragment.body


# ------------------------------------------------------------------------ c3


def test_two_fragments_amending_one_section_write_one_line_in_assembly_order(
    project: Path, kyo
):
    """KYO-493.c3: one back-link line carrying both numbers, never two lines."""
    declare(project, amends_declaration())
    notes = seed_notes(project)
    write_fragment(project, "TST-1.d1--a.md", amending("A", "<!-- kyosei-fragment amends: 203 -->"))
    write_fragment(project, "TST-2.d1--b.md", amending("B", "<!-- kyosei-fragment amends: 203 -->"))
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    block = section_block(notes.read_text(encoding="utf-8"), 203)
    assert block.count("amended_by") == 1, block
    assert "amended_by: [205, 206]" in block


def test_a_dead_section_gets_the_back_link_and_keeps_its_status(project: Path, kyo):
    """KYO-493.c3: `section_is_dead` decides; the output says the status was kept."""
    declare(project, amends_declaration())
    notes = seed_notes(
        project,
        NOTES.replace("status: current\nsee_also: [198]", "status: superseded\nsee_also: [198]"),
    )
    write_fragment(project, "TST-1.d1--a.md", amending("A", "<!-- kyosei-fragment amends: 203 -->"))
    result = kyo("docs", "assemble")
    assert result.code == 0, result
    block = section_block(notes.read_text(encoding="utf-8"), 203)
    assert "status: superseded\namended_by: [205]" in block
    assert "amends §203" in result.out
    assert "status: superseded (kept: the section is dead" in result.out


# ------------------------------------------------------------------------ c4


def _undeclared(project: Path) -> None:
    declare(project)


def _declare_amends(project: Path) -> None:
    declare(project, amends_declaration())


def _fragment_header(header: str):
    def write(project: Path) -> None:
        write_fragment(project, "TST-1.d1--fix.md", amending("A correction", header))

    return write


def _notes(text: str):
    def write(project: Path) -> None:
        (project / "apps" / "server" / "NOTES.md").write_text(text, encoding="utf-8")

    return write


HEADER_204 = "<!-- kyosei-fragment amends: 204 -->"
HEADER_203 = "<!-- kyosei-fragment amends: 203 -->"
_IN_204 = "data-quality\nstatus: current\n"
AMBIGUOUS = NOTES.replace(
    "## 203. An earlier", "## 203. A duplicate\nstatus: current\n\n## 203. An earlier"
)
NO_STATUS = NOTES.replace(_IN_204, "data-quality\n")
SCALAR_BACK_LINK = NOTES.replace(_IN_204, _IN_204 + "amended_by: 134\n")
TWICE_BACK_LINK = NOTES.replace(_IN_204, _IN_204 + "amended_by: [1]\namended-by: [2]\n")

#: (reason, set the bad state up, the remedy the refusal names, a phrase it must say).
REFUSAL_CASES = [
    (
        docs_numbered.FRAGMENT_HEADER_UNREADABLE,
        [_declare_amends, _fragment_header("<!-- kyosei-fragment amend: 204 -->")],
        _fragment_header("<!-- kyosei-fragment amends: 204 -->"),
        "unknown key 'amend'",
    ),
    (
        docs_numbered.FRAGMENT_HEADER_UNREADABLE,
        [_declare_amends, _fragment_header("<!-- kyosei-fragment amends: 39k -->")],
        _fragment_header("<!-- kyosei-fragment amends: 204 -->"),
        "name its parent section 39",
    ),
    (
        docs_numbered.AMENDS_UNDECLARED,
        [_undeclared, _fragment_header("<!-- kyosei-fragment amends: 204 -->")],
        _declare_amends,
        "section(s) 204",
    ),
    (
        docs_numbered.AMENDS_UNKNOWN_SECTION,
        [_declare_amends, _fragment_header("<!-- kyosei-fragment amends: 150 -->")],
        _fragment_header("<!-- kyosei-fragment amends: 204 -->"),
        "section 150",
    ),
    (
        docs_numbered.AMENDS_AMBIGUOUS_SECTION,
        [_declare_amends, _notes(AMBIGUOUS), _fragment_header(HEADER_203)],
        _notes(AMBIGUOUS.replace("## 203. A duplicate", "## 202. A duplicate")),
        "section 203",
    ),
    (
        docs_numbered.AMENDS_NO_STATUS_LINE,
        [_declare_amends, _notes(NO_STATUS), _fragment_header(HEADER_204)],
        _notes(NOTES),
        "section 204",
    ),
    (
        docs_numbered.AMENDS_UNREADABLE_BACK_LINK,
        [_declare_amends, _notes(SCALAR_BACK_LINK), _fragment_header(HEADER_204)],
        _notes(SCALAR_BACK_LINK.replace("amended_by: 134", "amended_by: [134]")),
        "'amended_by: 134'",
    ),
    (
        docs_numbered.AMENDS_UNREADABLE_BACK_LINK,
        [_declare_amends, _notes(TWICE_BACK_LINK), _fragment_header(HEADER_204)],
        _notes(TWICE_BACK_LINK.replace("amended-by: [2]\n", "")),
        "appears 2 times",
    ),
]


@pytest.mark.parametrize(
    ("reason", "setup", "remedy", "says"),
    REFUSAL_CASES,
    ids=[f"{case[0]}-{index}" for index, case in enumerate(REFUSAL_CASES)],
)
def test_each_amends_refusal_voids_the_run_and_its_remedy_clears_it(
    project: Path, kyo, reason: str, setup, remedy, says: str
):
    """KYO-493.c4: refused before anything is written — including a fragment that had
    no problem — naming the fragment and the section, and the remedy clears it."""
    seed_notes(project)
    for step in setup:
        step(project)
    innocent = write_fragment(project, "TST-0.d1--fine.md", section("Unrelated"))
    notes = project / "apps" / "server" / "NOTES.md"
    before = notes.read_text(encoding="utf-8")

    refused = kyo("docs", "assemble")
    assert refused.code == 2, refused
    message = refused.out + refused.err
    assert CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED in message
    assert "TST-1.d1--fix.md" in message
    assert says in message, message
    assert notes.read_text(encoding="utf-8") == before, "nothing may be written"
    assert innocent.exists(), "and no fragment may be removed"
    data = json.loads(kyo("docs", "assemble", "--json").out)
    reasons = {row["reason"] for row in (data.get("data") or data)["refusals"]}
    assert reason in reasons, reasons

    remedy(project)
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared


def test_amending_a_section_this_run_numbers_is_refused_and_the_named_remedy_clears(
    project: Path, kyo
):
    """KYO-493.c4, `amends-unassembled-section`: names the fragment this run would give
    the number to, and the remedy is run exactly as it reads."""
    declare(project, amends_declaration())
    notes = seed_notes(project)
    write_fragment(project, "TST-1.d1--new.md", section("New"))
    amender = write_fragment(
        project, "TST-2.d1--fix.md", amending("Fix", "<!-- kyosei-fragment amends: 205 -->")
    )
    before = notes.read_text(encoding="utf-8")
    refused = kyo("docs", "assemble")
    assert refused.code == 2, refused
    message = refused.out + refused.err
    assert docs_numbered.AMENDS_UNASSEMBLED_SECTION in json.dumps(
        json.loads(kyo("docs", "assemble", "--json").out)
    )
    assert "TST-2.d1--fix.md" in message
    assert "amends section 205" in message
    assert "this run would give 205 to apps/server/notes-fragments/TST-1.d1--new.md" in message
    assert notes.read_text(encoding="utf-8") == before

    # the remedy: move it out, assemble, put it back with the number, assemble again
    held = amender.read_text(encoding="utf-8")
    amender.unlink()
    assert kyo("docs", "assemble").code == 0
    amender.write_text(held, encoding="utf-8")
    cleared = kyo("docs", "assemble")
    assert cleared.code == 0, cleared
    assert "amended_by: [206]" in section_block(notes.read_text(encoding="utf-8"), 205)


# ------------------------------------------------------------------------ c5


def test_dry_run_prints_every_write_back_and_matches_the_applied_plan(project: Path, kyo):
    """KYO-493.c5: the same plan, printed and carried as `amendments[]`, then applied."""
    declare(project, amends_declaration())
    notes = seed_notes(project)
    write_fragment(
        project, "TST-1.d1--fix.md", amending("Fix", "<!-- kyosei-fragment amends: 203, 204 -->")
    )
    before = notes.read_text(encoding="utf-8")

    dry = kyo("docs", "assemble", "--dry-run")
    assert dry.code == 0, dry
    assert notes.read_text(encoding="utf-8") == before
    assert "amends §203   amended_by: (no line) -> [205]   status: current -> amended" in dry.out
    assert "amends §204   amended_by: (no line) -> [205]   status: current -> amended" in dry.out
    dry_json = json.loads(kyo("docs", "assemble", "--dry-run", "--json").out)
    planned = (dry_json.get("data") or dry_json)["amendments"]
    assert [row["section"]["number"] for row in planned] == [203, 204]
    assert planned[0]["by"] == [205]
    assert planned[0]["field_before"] is None and planned[0]["field_after"] == "[205]"
    assert (planned[0]["status_before"], planned[0]["status_after"]) == ("current", "amended")
    assert planned[0]["status_kept_because"] is None
    assert notes.read_text(encoding="utf-8") == before

    wet_json = json.loads(kyo("docs", "assemble", "--json").out)
    assert (wet_json.get("data") or wet_json)["amendments"] == planned


def test_dry_run_prints_the_same_refusals(project: Path, kyo):
    """KYO-493.c5: and exits non-zero on them, as an applying run does."""
    declare(project, amends_declaration())
    seed_notes(project)
    write_fragment(
        project, "TST-1.d1--fix.md", amending("Fix", "<!-- kyosei-fragment amends: 150 -->")
    )
    dry = kyo("docs", "assemble", "--dry-run")
    assert dry.code == 2, dry
    assert "amends section 150" in dry.out + dry.err


# ------------------------------------------------------------------------ c6


def test_the_brief_states_the_amends_header_only_when_the_set_declares_it(
    project: Path, kyo
):
    """KYO-493.c6."""
    declare(project, amends_declaration())
    seed_notes(project)
    text = brief_for(project, kyo)
    assert "`<!-- kyosei-fragment amends: 178, 186 -->`" in text
    assert "Do not edit their `amended_by` or their `status` in place" in text
    assert "sets its status to `amended`" in text


def test_the_brief_says_nothing_of_amends_for_a_set_without_it(project: Path, kyo):
    """KYO-493.c6: a set with no `amends:` gets no such line."""
    declare(project)
    seed_notes(project)
    text = brief_for(project, kyo)
    assert "Documents that grow by numbered section" in text
    assert "kyosei-fragment amends" not in text


# ------------------------------------------------------------------------ c7


CHECK_BRANCH_CASES = [
    (
        "<!-- kyosei-fragment amend: 204 -->", None, amends_declaration(),
        docs_numbered.FRAGMENT_HEADER_UNREADABLE,
    ),
    ("<!-- kyosei-fragment amends: 204 -->", None, DECLARATION, docs_numbered.AMENDS_UNDECLARED),
    (
        "<!-- kyosei-fragment amends: 150 -->", None, amends_declaration(),
        docs_numbered.AMENDS_UNKNOWN_SECTION,
    ),
    (
        "<!-- kyosei-fragment amends: 203 -->", AMBIGUOUS, amends_declaration(),
        docs_numbered.AMENDS_AMBIGUOUS_SECTION,
    ),
    (
        "<!-- kyosei-fragment amends: 204 -->", NO_STATUS, amends_declaration(),
        docs_numbered.AMENDS_NO_STATUS_LINE,
    ),
    (
        "<!-- kyosei-fragment amends: 204 -->", SCALAR_BACK_LINK, amends_declaration(),
        docs_numbered.AMENDS_UNREADABLE_BACK_LINK,
    ),
]


@pytest.mark.parametrize(
    ("header", "notes_text", "declaration", "reason"),
    CHECK_BRANCH_CASES,
    ids=[case[3] for case in CHECK_BRANCH_CASES],
)
def test_check_branch_refuses_a_fragment_assemble_would_refuse(
    project: Path, kyo, header: str, notes_text: str | None, declaration: dict, reason: str
):
    """KYO-493.c7: the mistake reaches the worker who can fix it, with the remedy."""
    declare(project, declaration)
    seed_notes(project, notes_text or NOTES)
    git_init(project)
    git(project, "checkout", "-q", "-b", "feature")
    fragment = write_fragment(project, "TST-1.d1--fix.md", amending("Fix", header))
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "work")

    result = kyo("check-branch", "--against", "main")
    assert result.code == 2, result
    message = result.out + result.err
    assert CONTRACT_FRAGMENT_CANNOT_BE_ASSEMBLED in message
    assert "TST-1.d1--fix.md" in message
    assert "remedy:" in message
    data = json.loads(kyo("check-branch", "--against", "main", "--json").out)
    reasons = {row["reason"] for row in (data.get("data") or data)["fragment_refusals"]}
    assert reasons == {reason}

    # the remedy: here, dropping the header clears every case alike
    fragment.write_text(section("Fix"), encoding="utf-8")
    git(project, "commit", "-q", "-am", "no amends")
    assert kyo("check-branch", "--against", "main").code == 0


def test_check_branch_passes_a_readable_amends_and_a_number_above_the_tail(
    project: Path, kyo
):
    """KYO-493.c7: a well-formed header passes, and `amends-unassembled-section` is
    not decidable on a worker's tree, so a number above the tail is not refused there."""
    declare(project, amends_declaration())
    seed_notes(project)
    git_init(project)
    git(project, "checkout", "-q", "-b", "feature")
    write_fragment(project, "TST-1.d1--a.md", amending("A", "<!-- kyosei-fragment amends: 204 -->"))
    write_fragment(project, "TST-1.d1--b.md", amending("B", "<!-- kyosei-fragment amends: 299 -->"))
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "work")
    result = kyo("check-branch", "--against", "main")
    assert result.code == 0, result


# ------------------------------------------------------------------------ c8


def _region(text: str, start_marker: str) -> str:
    start = text.index(start_marker)
    level = start_marker.split(" ", 1)[0] + " "
    following = text.find("\n" + level, start + 1)
    return " ".join(text[start : following if following != -1 else len(text)].split())


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("docs/reference/topics/operating.md", "### Doc fragments"),
        ("docs/design/charter.md", "## Layer 3 — Documentation SOT"),
        ("docs/design/docs.md", "## KYO-493"),
    ],
)
def test_the_documentation_states_the_declaration_header_and_refusals(path: str, marker: str):
    """KYO-493.c8."""
    text = _region((REPO_ROOT / path).read_text(encoding="utf-8"), marker)
    assert "numbered.amends" in text, path
    assert "kyosei-fragment amends:" in text, path
    for reason in docs_numbered.AMENDS_REFUSALS:
        assert reason in text, f"{path} must name {reason}"
