"""KYO-562: `kyo docs suggest` drafts ``numbered:`` only from history showing growth at
the end.

From a consumer project's report: its ``CHARTER.md`` is a plan with ``## 1.`` to
``## 12.``, edited in place and committed once, and `kyo docs suggest` called it
"numbered and append-only" — so `kyo doctor` showed a notice
suggesting a fragments directory for it. Numbered headings that increase densely describe
that plan exactly as well as they describe a log; whether a file grows only at its end is
a fact about its history, and nothing read the history.

What counts (:class:`kyosei.docs_suggest.GrowthEvidence`): a commit that adds a numbered
heading to a revision that already had one and changes nothing from the first numbered
heading up to the last is an append; a commit that changes anything in a section that
was no longer the newest is an in-place edit. At least
:data:`~kyosei.docs_suggest.MIN_APPEND_COMMITS` appends and no in-place edit drafts the
set; in-place edits and no append yield nothing; everything else — a single-commit file
included — is asked as a question and drafts nothing.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kyosei import docs_history, docs_suggest

from .test_docs_suggest import commit, declare, git

CHARTER_PATH = "CHARTER.md"


def charter(sections: int = 12, *, edited: dict[int, str] | None = None) -> str:
    """A plan of that shape: a title, a preamble and ``## 1.`` to ``## n.``."""
    edited = edited or {}
    parts = ["# Project charter\n\nWhat this project is for.\n"]
    for number in range(1, sections + 1):
        body = edited.get(number, f"The plan for part {number}.")
        parts.append(f"\n## {number}. Part {number}\n\n{body}\n")
    return "".join(parts)


def declare_charter(project: Path) -> None:
    declare(
        project,
        {
            "documents": {
                "charter": {
                    "summary": "The project charter.",
                    "globs": [CHARTER_PATH],
                }
            }
        },
    )


def suggestions(project: Path) -> list[docs_suggest.DocsSuggestion]:
    return docs_suggest.suggest(project / "kyosei", project)


def drafted(project: Path) -> list[docs_suggest.DocsSuggestion]:
    return [suggestion for suggestion in suggestions(project) if suggestion.drafted]


def growth(project: Path) -> docs_suggest.GrowthEvidence:
    return docs_suggest.growth_evidence(project, CHARTER_PATH, "## {number}.")


# ------------------------------------------------------------------------ c4, c5
# The single-commit charter: a question, never a drafted set.


def test_a_single_commit_numbered_charter_drafts_no_numbered_set(project: Path) -> None:
    """c5, the reported case itself: one commit, ``## 1.`` to ``## 12.``.

    Before KYO-562 this file was drafted a ``numbered:`` set with a fragments directory.
    """
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(), "the charter")

    found = suggestions(project)

    assert drafted(project) == []
    assert not any(suggestion.yaml_lines() for suggestion in found)
    assert all(suggestion.block is None for suggestion in found)


def test_with_no_evidence_it_asks_the_question_and_nothing_more(project: Path) -> None:
    """c4: at most a question, worded as the record words it, with no YAML."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(), "the charter")

    (question,) = suggestions(project)

    assert question.kind == docs_suggest.SUGGEST_NUMBERED_QUESTION
    assert question.says == "CHARTER.md has numbered headings; is it an append-only log?"
    assert question.block is None
    assert question.row()["yaml"] is None and question.row()["block"] is None
    evidence = "\n".join(question.evidence)
    assert "12 heading(s)" in evidence
    assert "1 commit(s) changed it" in evidence
    assert "nothing is drafted" in question.how


def test_an_uncommitted_or_unreadable_file_is_asked_too(project: Path) -> None:
    """c4's "no evidence" includes no history at all: untracked, or no repository."""
    declare_charter(project)
    (project / CHARTER_PATH).write_text(charter(), encoding="utf-8")

    (question,) = suggestions(project)
    assert question.kind == docs_suggest.SUGGEST_NUMBERED_QUESTION
    assert "git could not read" in "\n".join(question.evidence)

    git(project, "commit", "-q", "--allow-empty", "-m", "a repository, the file untracked")
    (question,) = suggestions(project)
    assert question.kind == docs_suggest.SUGGEST_NUMBERED_QUESTION
    assert "has no commits" in "\n".join(question.evidence)


def test_the_command_prints_the_question_without_yaml(kyo, project: Path) -> None:
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(), "the charter")

    result = kyo("docs", "suggest")
    data = kyo("docs", "suggest", "--json").json

    assert result.code == 0, result
    assert (
        "[numbered-question] CHARTER.md has numbered headings; is it an append-only log?"
        in result.out
    )
    assert "numbered:\n" not in result.out and "fragments:" not in result.out
    (row,) = data["suggestions"]
    assert row["kind"] == docs_suggest.SUGGEST_NUMBERED_QUESTION
    assert row["block"] is None


# ------------------------------------------------------------------------ c3
# Doctor's notice follows the same evidence.


def test_doctor_shows_no_notice_for_the_single_commit_charter(kyo, project: Path) -> None:
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(), "the charter")

    findings = kyo("doctor", "--json").json["checks"]["docs_suggestions"]["failures"]

    assert findings == []


def test_doctor_shows_the_notice_once_the_history_shows_growth(kyo, project: Path) -> None:
    """The other half of c3: the same file, grown at its end twice, is a draft and is
    counted — so the silence above is the evidence speaking, not the notice gone."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(10), "the charter")
    commit(project, CHARTER_PATH, charter(11), "part 11")
    commit(project, CHARTER_PATH, charter(12), "part 12")

    findings = kyo("doctor", "--json").json["checks"]["docs_suggestions"]["failures"]

    assert len(findings) == 1, findings
    assert "has 1 draft declaration(s)" in findings[0]
    assert docs_suggest.SUGGEST_NUMBERED in findings[0]


# ------------------------------------------------------------------------ c1, c2


def test_growth_at_its_end_drafts_the_set_and_says_why(project: Path) -> None:
    """c1: two commits each adding a section after the last one, none editing earlier."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(10), "the charter")
    commit(project, CHARTER_PATH, charter(11), "part 11")
    commit(project, CHARTER_PATH, charter(12), "part 12")

    (suggestion,) = suggestions(project)

    assert suggestion.kind == docs_suggest.SUGGEST_NUMBERED
    block = yaml.safe_load("\n".join(suggestion.yaml_lines()))
    assert block["documents"]["charter"]["numbered"]["heading"] == "## {number}."
    assert (
        "its history shows it growing at its end: 3 commit(s) changed it on the "
        "first-parent chain: 2 added a numbered section at its end and none edited an "
        "earlier section" in suggestion.evidence
    )


def test_one_append_is_not_yet_growth(project: Path) -> None:
    """The threshold: a plan gains a section once; a log does it again and again."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(11), "the charter")
    commit(project, CHARTER_PATH, charter(12), "part 12")

    assert growth(project).appends == 1
    (question,) = suggestions(project)
    assert question.kind == docs_suggest.SUGGEST_NUMBERED_QUESTION


def test_an_earlier_section_edited_after_a_later_one_was_added_is_not_proposed(
    project: Path,
) -> None:
    """c2: appends at the end, and then section 2 edited in place. Not a draft: the
    most it gets is the question, with the edit named in the evidence."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(10), "the charter")
    commit(project, CHARTER_PATH, charter(11), "part 11")
    commit(project, CHARTER_PATH, charter(12), "part 12")
    commit(project, CHARTER_PATH, charter(12, edited={2: "Rewritten."}), "revise part 2")

    evidence = growth(project)
    assert (evidence.appends, evidence.in_place) == (2, 1)
    assert drafted(project) == []
    (question,) = suggestions(project)
    assert question.kind == docs_suggest.SUGGEST_NUMBERED_QUESTION
    assert "1 edited a section after a later one existed" in "\n".join(question.evidence)


def test_a_file_only_ever_edited_in_place_gets_nothing(project: Path) -> None:
    """In-place edits and no append: the history has answered, so nothing is asked."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(), "the charter")
    commit(project, CHARTER_PATH, charter(edited={3: "Revised."}), "revise part 3")
    commit(project, CHARTER_PATH, charter(edited={3: "Revised.", 7: "Also."}), "revise 7")

    assert growth(project).verdict == docs_suggest.EDITED_IN_PLACE
    assert suggestions(project) == []


def test_the_preamble_and_the_newest_section_are_neither(project: Path) -> None:
    """Editing above the first numbered heading, or the section still being written, is
    neither an append nor an in-place edit — a log's intro gets tweaked too."""
    longer = {10: "Still being written, now longer."}

    def revised(sections: int, intro: bool = True) -> str:
        text = charter(sections, edited=longer)
        return text.replace("What this", "What all of this") if intro else text

    declare_charter(project)
    commit(project, CHARTER_PATH, charter(10), "the charter")
    commit(project, CHARTER_PATH, revised(10, intro=False), "the newest part grows")
    commit(project, CHARTER_PATH, revised(10), "the intro")
    commit(project, CHARTER_PATH, revised(11), "part 11")
    commit(project, CHARTER_PATH, revised(12), "part 12")

    evidence = growth(project)
    assert (evidence.commits, evidence.appends, evidence.in_place) == (5, 2, 0)
    assert evidence.verdict == docs_suggest.GROWS_AT_END


def test_text_inserted_just_above_the_newest_heading_is_an_in_place_edit(
    project: Path,
) -> None:
    """The boundary: a line added after section 11's body but before ``## 12.`` lands in
    section 11, which is no longer the newest."""
    declare_charter(project)
    commit(project, CHARTER_PATH, charter(12), "the charter")
    text = (project / CHARTER_PATH).read_text(encoding="utf-8")
    commit(
        project,
        CHARTER_PATH,
        text.replace("\n## 12.", "\nA late note on part 11.\n\n## 12."),
        "note",
    )

    assert growth(project).in_place == 1


# ------------------------------------------------------------------------ the reader


def test_document_history_rebuilds_every_revision_across_a_rename_and_a_merge(
    project: Path,
) -> None:
    """The git reader in docs_history: first-parent, renames followed, each revision
    rebuilt from the diffs — and the last rebuilt revision is the file as committed."""
    commit(project, "OLD.md", charter(3), "create")
    commit(project, "OLD.md", charter(4), "append")
    git(project, "checkout", "-q", "-b", "side")
    commit(project, "OLD.md", charter(4, edited={1: "Edited on a branch."}), "side edit")
    git(project, "checkout", "-q", "main")
    commit(project, "OLD.md", charter(5), "append on main")
    git(project, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    git(project, "mv", "OLD.md", "NEW.md")
    git(project, "commit", "-q", "-m", "rename")

    history = docs_history.document_history(project, "NEW.md")

    assert history is not None
    assert len(history) == 5  # create, append, append on main, merge, rename
    assert history[0].before_text is None
    assert history[-1].after_text == (project / "NEW.md").read_text(encoding="utf-8").rstrip(
        "\n"
    )
    merge = history[3]
    assert "Edited on a branch." in merge.after_text
    assert "Edited on a branch." not in (merge.before_text or "")


def test_document_history_says_cannot_tell_outside_a_repository(tmp_path: Path) -> None:
    (tmp_path / "X.md").write_text("# x\n", encoding="utf-8")

    assert docs_history.document_history(tmp_path, "X.md") is None
