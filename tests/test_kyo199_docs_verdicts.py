"""KYO-199: who reads the governed section, and what the reading costs.

Two stores were measured and both say the same thing: docs obligations are
overwhelmingly settled ``not-affected`` — roughly nine to one in a consumer project's
store, roughly two to one in this one — and one of that consumer's dispatches raised
sixty at once. The mechanism is working; the *reading* is the controller's cost, every
time, and at sixty sections nobody reads them, so the verdicts get made from the file
diff instead. What this record moves is who does the reading (the worker, who has the
diff and the document open already) and how the output is shaped. The stored unit is
untouched: still one obligation per section (KYO-153.c4).

The criteria, and where each is tested here:

* **c1** ``kyo report`` and ``kyo docs obligations`` render obligations grouped per
  document, one settle line per document naming every id in the block.
* **c2** an obligation raised by a whole-file ``governs:`` target says so, and offers the
  ``path#symbol`` form — once per document, never once per obligation.
* **c3** is a measurement the controller records over its next ten reports. Nothing here.
* **c4** the report schema's ``docs_verdicts[]``, ``kyo report``'s matching of each entry
  to the obligation it raised, and ``kyo docs settle --from-report``.
* **c5** the worker-audience rule and the brief that carries it.
* **c6** ``kyo docs preview`` — the same join, read-only, from a worktree.

The fixtures are imported from :mod:`tests.test_kyo137_docs_obligations` rather than
rebuilt: a second "project with a governed document and a branch" would be a second
answer to the question of what raising looks like, and the point of the preview is that
it agrees with what ``kyo report`` does.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kyosei import docs_obligations, docs_regions
from kyosei.cmd_views import PREVIEW_WRITES_NOTHING

from .conftest import new_record, valid_report
from .test_kyo137_docs_obligations import (
    _SET_DOC,
    _SET_README,
    _branch_editing,
    _commit,
    _declare_touches,
    _dispatch_and_report,
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
    _project_with_a_governed_doc,
    _project_with_a_set_of_obligations,
    _write,
)


def _on_branch(project: Path, branch: str):
    """Check ``branch`` out, the way a worker's worktree already has it out."""
    _git(project, "checkout", "-q", branch)


def _obligation_rows(project: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM docs_obligations"))
    finally:
        conn.close()


# ------------------------------------------------------ c6: the preview, from a worktree


def test_the_preview_names_the_sections_report_would_raise(kyo, git_project: Path) -> None:
    """c6's first half. The worker is shown exactly the sections its controller will be
    shown, because both come out of one function (`docs_obligations.obligations_for_branch`)."""
    record, base = _project_with_a_governed_doc(kyo, git_project)
    _on_branch(git_project, "kyo-137")

    preview = kyo("docs", "preview", "--against", base, "--json")
    assert preview.code == 0, preview
    previewed = {
        (row["doc_path"], row["section"]) for row in preview.json["obligations"]
    }
    assert previewed == {("docs/architecture.md", "How the CLI is wired")}
    # The other section of the same document governs a file this branch did not touch,
    # so the preview inherits c3's per-section unit rather than naming the document.
    assert "How releases are cut" not in json.dumps(preview.json["obligations"])

    _git(git_project, "checkout", "-q", base)
    _, reported = _dispatch_and_report(kyo, git_project, record)
    raised = {
        (row["doc_path"], row["section"])
        for row in reported.json["docs_obligations"]["raised"]
    }
    assert raised == previewed, (
        "the preview and the ingest must answer the same question the same way; they "
        "are one computation and this is the assertion that keeps them one"
    )


def test_the_preview_writes_nothing_and_says_so(kyo, git_project: Path) -> None:
    """c6's second half, and the reason it is stated rather than inferred: a dispatched
    worker is forbidden to write to the store at all, so 'this wrote nothing' is the fact
    the reader needs, not a reassurance to take from the absence of an error."""
    _project_with_a_governed_doc(kyo, git_project)
    _on_branch(git_project, "kyo-137")
    before = len(_obligation_rows(git_project))

    preview = kyo("docs", "preview", "--against", "main", "--json")
    assert preview.code == 0, preview
    assert preview.json["wrote_to_store"] is False
    assert PREVIEW_WRITES_NOTHING in kyo("docs", "preview", "--against", "main").out
    assert len(_obligation_rows(git_project)) == before


def test_the_preview_runs_in_a_checkout_that_has_no_database(kyo, git_project: Path) -> None:
    """The property the whole action exists for. `git worktree add` checks out tracked
    files only, and `kyosei/kyosei.db` is gitignored — so a worker's worktree never has
    one. A preview that opened the store would be a command only the controller could
    run, which is the reader who did not need it."""
    _project_with_a_governed_doc(kyo, git_project)
    _on_branch(git_project, "kyo-137")
    (git_project / "kyosei" / "kyosei.db").unlink()

    preview = kyo("docs", "preview", "--against", "main", "--json")
    assert preview.code == 0, preview
    assert [row["section"] for row in preview.json["obligations"]] == [
        "How the CLI is wired"
    ]
    assert not (git_project / "kyosei" / "kyosei.db").exists(), (
        "the preview opened (and therefore created) a store database"
    )


def test_the_preview_names_the_half_of_the_join_it_cannot_see(kyo, git_project: Path) -> None:
    """Law 6. A record's `doc-refs` are a stored declaration and this command reads no
    store, so a worker reading 'no documents implicated' would otherwise have read a
    guarantee the command cannot give."""
    _project_with_a_governed_doc(kyo, git_project)
    _on_branch(git_project, "kyo-137")
    assert kyo("docs", "preview", "--against", "main", "--json").json["covers_doc_refs"] is False
    assert "doc-refs" in kyo("docs", "preview", "--against", "main").out


def test_an_unknown_base_is_a_usage_error_naming_the_flag(kyo, git_project: Path) -> None:
    """The one failure a worker actually hits: it was branched from something other than
    `main`, and the message has to name the flag that fixes it rather than the ref."""
    _project_with_a_governed_doc(kyo, git_project)
    _on_branch(git_project, "kyo-137")
    result = kyo("docs", "preview", "--against", "no-such-base")
    assert result.code != 0
    assert "--against" in result.out + result.err


def test_a_branch_that_changed_nothing_governed_says_so_rather_than_printing_nothing(
    kyo, git_project: Path
) -> None:
    """The empty outcome is a real answer and has to read as one (law 6)."""
    _project_with_a_governed_doc(kyo, git_project)
    _git(git_project, "checkout", "-q", "-b", "untouched")
    (git_project / "unrelated.txt").write_text("nothing governed\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "unrelated")

    preview = kyo("docs", "preview", "--against", "main", "--json")
    assert preview.code == 0, preview
    assert preview.json["obligations"] == []
    assert "(none)" in kyo("docs", "preview", "--against", "main").out


def test_the_preview_is_a_second_reading_of_one_computation(kyo, git_project: Path) -> None:
    """The teeth for the pair above. `_docs_obligations_for_dispatch` and the preview
    both call `docs_obligations.obligations_for_branch`; delete that function and both go
    down together, which is what makes 'they cannot disagree' structural."""
    assert hasattr(docs_obligations, "obligations_for_branch")
    record, base = _project_with_a_governed_doc(kyo, git_project)
    found = docs_obligations.obligations_for_branch(
        git_project,
        base,
        "kyo-137",
        _git(git_project, "diff", "--name-only", f"{base}...kyo-137").splitlines(),
    )
    assert [(o.doc_path, o.section) for o in found] == [
        ("docs/architecture.md", "How the CLI is wired")
    ]
    assert record  # the fixture's record id, unused here beyond building the tree


# ---------------------------------------- c1: one block per document, one settle line


def test_the_listing_prints_a_block_per_document_with_its_own_settle_line(
    kyo, git_project: Path
) -> None:
    """c1 on `kyo docs obligations`. Three obligations, two documents, two blocks, and
    each block's line names exactly its own members."""
    record, dispatch_id, raised = _project_with_a_set_of_obligations(kyo, git_project)
    listing = kyo("docs", "obligations")
    assert listing.code == 0, listing

    architecture = [r["id"] for r in raised if r["doc_path"] == "docs/architecture.md"]
    readme = [r["id"] for r in raised if r["doc_path"] == "README.md"]
    assert len(architecture) == 2 and len(readme) == 1

    block = listing.out.split("docs/architecture.md — 2 section(s)")[1].split("README.md")[0]
    settle = next(line for line in block.splitlines() if "kyo docs settle" in line)
    assert sorted(t for t in settle.split() if t.startswith(f"{record}.")) == sorted(
        architecture
    )
    assert readme[0] not in settle, "a document's line must not reach into another's"
    assert dispatch_id in listing.out


def test_the_grouping_hides_nothing_and_the_tail_that_duplicated_it_is_gone(
    kyo, git_project: Path
) -> None:
    """The property KYO-159 was careful about, carried forward: every row is still listed
    individually inside its block, and every id is still reachable. What is gone is the
    second rendering of the same grouping — the "N document(s) hold more than one" tail,
    which pointed a reader back up at rows they had already scrolled past."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    listing = kyo("docs", "obligations")
    for row in raised:
        assert row["id"] in listing.out
        assert row["section"] in listing.out
    assert "hold more than one" not in listing.out
    groupings = kyo("docs", "obligations", "--json").json["groupings"]
    assert [g["doc_path"] for g in groupings] == list(
        dict.fromkeys(row["doc_path"] for row in raised)
    ), "the groups are in the order the rows were raised, and every document has one"
    assert "members" not in groupings[0], (
        "the rows are carried once, beside the groupings, not inside them as well"
    )


def test_settled_rows_stay_readable_and_out_of_the_groupings(kyo, git_project: Path) -> None:
    """`--all` is history rather than work: there is nothing to settle, so there is no act
    to group it into, and it must not acquire a settle line that would be refused."""
    record, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    readme = next(r["id"] for r in raised if r["doc_path"] == "README.md")
    assert kyo(
        "docs", "settle", readme, "--verdict", "not-affected", "--method", "read it"
    ).code == 0

    everything = kyo("docs", "obligations", "--all")
    assert readme in everything.out
    assert "settled (1)" in everything.out
    groupings = kyo("docs", "obligations", "--all", "--json").json["groupings"]
    assert readme not in json.dumps(groupings)
    assert record


def test_one_function_shapes_the_grouping_on_every_surface(kyo, git_project: Path) -> None:
    """The teeth for c1. `kyo report`, `kyo docs obligations` and `kyo docs preview` all
    call `docs_obligations.group_obligations_per_document`; three renderings could disagree
    about the unit, and the preview exists precisely so a worker sees what the controller
    will see."""
    assert hasattr(docs_obligations, "group_obligations_per_document")
    grouped = docs_obligations.group_obligations_per_document(
        [
            {"doc_path": "README.md", "section": "One", "id": "R-1.o1"},
            {"doc_path": "docs/a.md", "section": "Two", "id": "R-1.o2"},
            {"doc_path": "README.md", "section": "Three", "id": "R-1.o3"},
        ]
    )
    assert [g["doc_path"] for g in grouped] == ["README.md", "docs/a.md"]
    assert grouped[0]["obligation_ids"] == ["R-1.o1", "R-1.o3"]
    assert "R-1.o1 R-1.o3" in grouped[0]["settle_command"]
    # A group with no ids — a preview — offers no line rather than an unrunnable one.
    preview = docs_obligations.group_obligations_per_document(
        [{"doc_path": "README.md", "section": "One"}]
    )
    assert preview[0]["settle_command"] is None


# ------------------------- c2: the whole-file nudge, once per document and no more often


_MIXED_DOC = """# The architecture

## Why the CLI is wired this way

governs: src/app.py

The reasoning.

## What the CLI does on start-up

governs: src/app.py

More reasoning about the same file.

## The parser's own contract

governs: src/app.py#parse_args

Narrow already.
"""


def _project_with_a_whole_file_governs(kyo, project: Path):
    """One document: two whole-file sections and one already narrowed to a symbol.

    Reports in TEXT rather than ``--json``, because the nudge is a rendering and the
    controller reads the rendering. A dispatch reports once, so this cannot be both.
    """
    _write(project, "docs/architecture.md", _MIXED_DOC)
    _write(project, "src/app.py", "def parse_args():\n    return 1\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    _branch_editing(project, "kyo-199", {"src/app.py": "def parse_args():\n    return 2\n"})
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-199", "--json").json[
        "dispatch_id"
    ]
    path = project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=_git(project, "rev-parse", "kyo-199"),
            )
        ),
        encoding="utf-8",
    )
    return record, kyo("report", dispatch_id, "--file", str(path))


def test_a_whole_file_target_offers_the_narrower_form_once_per_document(
    kyo, git_project: Path
) -> None:
    """c2. The remedy is a single edit to one `governs:` line, so it is offered once for
    the document — not once for each of the sections that happen to share the mistake,
    which is the per-row noise this record was filed about."""
    _, reported = _project_with_a_whole_file_governs(kyo, git_project)
    assert reported.code == 0, reported
    text = reported.out
    assert text.count("`governs: <path>#<symbol>`") == 1, text
    nudge = next(line for line in text.splitlines() if "governs whole files" in line)
    assert "src/app.py" in nudge
    assert "src/app.py#parse_args" not in nudge, (
        "an already-narrowed target has no narrower form to be offered"
    )


def test_the_nudge_is_absent_when_every_matched_target_is_already_narrow(
    kyo, git_project: Path
) -> None:
    """The false positive that would make the nudge unreadable: printed unconditionally
    it becomes a line every reader learns to skip, including on the documents that took
    the advice."""
    assert docs_obligations.whole_file_targets(["src/app.py#parse_args"]) == ()
    assert docs_obligations.whole_file_targets(["module.sub.symbol"]) == ()
    assert docs_obligations.whole_file_targets(["src/app.py"]) == ("src/app.py",)
    # A colon-spelled target is the same question as its `#` twin (KYO-200).
    assert docs_obligations.whole_file_targets(["src/app.py:parse_args"]) == ()


# ------------- KYO-351: the offer is made only where the narrowing it offers is real


_STYLESHEET_DOC = """# The viewer

## How the left pane opens

governs: src/styles/panes.scss

The reasoning.
"""

_MIXED_SUFFIX_DOC = """# The viewer and the CLI

## How the left pane opens

governs: src/styles/panes.scss

The reasoning.

## Why the CLI is wired this way

governs: src/app.py

More reasoning.
"""

_PANES_SCSS = ".pane-left {\n  width: 240px;\n}\n"


def test_the_nudge_is_offered_only_for_targets_a_symbol_could_narrow() -> None:
    """KYO-351.c1, at the unit the three surfaces share.

    Measured by a consumer: `docs_regions.REGION_SUFFIXES` is the whole of
    the narrowing, every file their four documents govern is outside it, and this
    sentence was printed to them anyway. They narrowed 101 targets on the strength of
    it and measured that the obligations raised did not move — 8 / 10 / 10 before and
    after, identical section sets.
    """
    assert docs_obligations.whole_file_nudge(["src/styles/panes.scss"]) == "", (
        "an offer that is false for every target it names is worse than no offer"
    )
    python_only = docs_obligations.whole_file_nudge(["src/app.py"])
    assert "src/app.py" in python_only
    # The suffix list in the sentence is the tuple the instrument consults, not a
    # second spelling of it — after a suffix is added, this sentence is already true.
    assert docs_regions.region_suffixes_says() in python_only
    mixed = docs_obligations.whole_file_nudge(["src/styles/panes.scss", "src/app.py"])
    assert "src/app.py" in mixed
    assert "panes.scss" not in mixed, (
        "a mixed document is offered the narrowing for the targets it holds for, and "
        "told nothing about the ones it does not"
    )


def test_a_document_governing_only_unnarrowable_files_is_offered_nothing(
    kyo, git_project: Path
) -> None:
    """c1 end to end, on the surface the consumer actually read: `kyo report`.

    The section still raises — a stylesheet that changed is a stylesheet that changed,
    and nothing here makes the gate quieter. What goes away is the sentence telling its
    author to make an edit that would change nothing.

    The file type is `.scss` and not `.ts` or `.css`: KYO-352 taught the finder the brace
    languages and KYO-363 the data ones, and this test asks the question KYO-351 asked,
    which is about the tuple rather than about any particular suffix in it. `.scss` is
    outside the tuple on purpose and not by oversight — nesting means a prelude inside a
    block is not a selector, and `&`-composition means the selector a document names is
    never the one written — so it is the stylesheet suffix that still stands for "a file
    type this instrument cannot region".
    """
    _write(git_project, "docs/viewer.md", _STYLESHEET_DOC)
    _write(git_project, "src/styles/panes.scss", _PANES_SCSS)
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["the pane opens"])
    _declare_touches(kyo, git_project, record, "src/styles/panes.scss")
    _branch_editing(
        git_project,
        "kyo-351",
        {"src/styles/panes.scss": _PANES_SCSS.replace("240px", "260px")},
    )
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-351", "--json").json[
        "dispatch_id"
    ]
    path = git_project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=_git(git_project, "rev-parse", "kyo-351"),
            )
        ),
        encoding="utf-8",
    )
    reported = kyo("report", dispatch_id, "--file", str(path))
    assert reported.code == 0, reported
    assert "How the left pane opens" in reported.out, reported.out
    assert "governs whole files" not in reported.out, reported.out
    assert "`governs: <path>#<symbol>`" not in reported.out, reported.out


def test_a_mixed_document_is_offered_only_the_targets_the_offer_holds_for(
    kyo, git_project: Path
) -> None:
    """c1's third case, through the same grouping function `kyo report`,
    `kyo docs obligations` and `kyo docs preview` all render with."""
    _write(git_project, "docs/viewer.md", _MIXED_SUFFIX_DOC)
    _write(git_project, "src/styles/panes.scss", _PANES_SCSS)
    _write(git_project, "src/app.py", "def parse_args():\n    return 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["the pane opens"])
    _declare_touches(kyo, git_project, record, "src/styles/panes.scss", "src/app.py")
    _branch_editing(
        git_project,
        "kyo-351",
        {
            "src/styles/panes.scss": _PANES_SCSS.replace("240px", "260px"),
            "src/app.py": "def parse_args():\n    return 2\n",
        },
    )
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-351", "--json").json[
        "dispatch_id"
    ]
    path = git_project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=_git(git_project, "rev-parse", "kyo-351"),
            )
        ),
        encoding="utf-8",
    )
    reported = kyo("report", dispatch_id, "--file", str(path))
    assert reported.code == 0, reported
    nudge = next(
        line for line in reported.out.splitlines() if "governs whole files" in line
    )
    assert "src/app.py" in nudge
    assert "panes.scss" not in nudge, nudge


def test_the_listing_offers_the_same_narrower_form_the_report_did(
    kyo, git_project: Path
) -> None:
    """`kyo docs obligations` reads stored rows, which never carried the matched targets;
    it asks the documents instead (`section_whole_file_targets`). Same offer, same
    once-per-document rule, from the other side of the store."""
    _project_with_a_whole_file_governs(kyo, git_project)
    listing = kyo("docs", "obligations")
    assert listing.out.count("`governs: <path>#<symbol>`") == 1, listing.out
    assert "governs whole files (src/app.py)" in listing.out


# ------------------- c4: docs_verdicts[] — the schema, the match, and applying the match


def _verdict(doc_path: str, section: str, verdict: str = "not-affected", **extra) -> dict:
    return {
        "doc_path": doc_path,
        "section": section,
        "verdict": verdict,
        "method": extra.get("method", "read the section against my own diff"),
        "reason": extra.get("reason", "the section describes behaviour this diff kept"),
    }


def _project_reporting_verdicts(kyo, project: Path, verdicts: list[dict], **report_extra):
    """The KYO-159 fixture, reported with a ``docs_verdicts[]`` of our choosing."""
    _write(project, "docs/architecture.md", _SET_DOC)
    _write(project, "README.md", _SET_README)
    _write(project, "src/app.py", "x = 1\n")
    _write(project, "release.sh", "echo release\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    _branch_editing(project, "kyo-199", {"src/app.py": "x = 2\n"})
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-199", "--json").json[
        "dispatch_id"
    ]
    path = project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=_git(project, "rev-parse", "kyo-199"),
                docs_verdicts=verdicts,
                **report_extra,
            )
        ),
        encoding="utf-8",
    )
    return record, dispatch_id, path



def test_the_schema_accepts_docs_verdicts_and_refuses_a_malformed_entry() -> None:
    """c4's first half. The key is (doc_path, section) and not an obligation id, because
    the ids do not exist when the worker writes the report — `kyo report` mints them out
    of the very join the ingest is running."""
    from kyosei import report_schema

    base = {
        "dispatch_id": "KYO-1.d1",
        "record_id": "KYO-1",
        "worked_from_sha": "0123abc",
        "criteria_verdicts": [],
        "premise_false": [],
        "disagreements": [],
        "corrected_numbers": [],
        "commits": [],
    }
    assert report_schema.validate_report(
        {**base, "docs_verdicts": [_verdict("README.md", "Running the CLI")]}
    ) == []
    # No reason: the field a controller reviews before applying under their own name.
    thin = _verdict("README.md", "Running the CLI")
    del thin["reason"]
    assert "reason" in " ".join(report_schema.validate_report({**base, "docs_verdicts": [thin]}))
    # A verdict `kyo docs settle` would refuse must not be writable here either.
    bad = _verdict("README.md", "Running the CLI", verdict="looked-at-it")
    assert "looked-at-it" in " ".join(
        report_schema.validate_report({**base, "docs_verdicts": [bad]})
    )
    # And a report that omits the key entirely is still valid — every report filed
    # before KYO-199 stays valid, and a dispatch that raised nothing has nothing to say.
    assert report_schema.validate_report(base) == []


def test_report_prints_each_matched_verdict_beside_the_obligation_it_answers(
    kyo, git_project: Path
) -> None:
    """c4. The controller reads the worker's proposal in place, under the section it is
    about, rather than cross-referencing a JSON array against a list of ids."""
    record, dispatch_id, path = _project_reporting_verdicts(
        kyo,
        git_project,
        [
            _verdict("README.md", "Running the CLI", "updated", method="rewrote it"),
            _verdict("docs/architecture.md", "Why the CLI is wired this way"),
        ],
    )
    result = kyo("report", dispatch_id, "--file", str(path))
    assert result.code == 0, result
    assert "worker proposes updated (rewrote it)" in result.out
    assert "worker proposes not-affected" in result.out
    assert "docs_verdicts: 2 matched an obligation this dispatch raised, 0 matched none" \
        in result.out
    # Proposed, not applied: every obligation is still open and still blocks completion.
    assert kyo("docs", "obligations", "--json").json["open"] == 3
    assert record


def test_report_names_an_entry_that_matched_no_obligation(kyo, git_project: Path) -> None:
    """The mechanism the worker-audience rule cites. A verdict against a section this
    branch never touched, or a paraphrased heading, cannot pass silently — and the two
    reasons are named apart, because a typo and a verdict on untouched work call for
    opposite responses."""
    _, dispatch_id, path = _project_reporting_verdicts(
        kyo,
        git_project,
        [
            _verdict("docs/architecture.md", "How releases are cut"),
            _verdict("README.md", "running the cli"),
        ],
    )
    result = kyo("report", dispatch_id, "--file", str(path))
    assert result.code == 0, result
    assert "0 matched an obligation this dispatch raised, 2 matched none" in result.out
    assert "How releases are cut" in result.out
    assert "running the cli" in result.out
    assert "never on a paraphrase" in result.out


def test_a_second_entry_for_one_section_is_reported_rather_than_overwriting_the_first(
    kyo, git_project: Path
) -> None:
    """Two verdicts about one section are a contradiction the controller resolves, not
    one the order of a JSON array resolves."""
    _, dispatch_id, path = _project_reporting_verdicts(
        kyo,
        git_project,
        [
            _verdict("README.md", "Running the CLI", "updated"),
            _verdict("README.md", "Running the CLI", "not-affected"),
        ],
    )
    result = kyo("report", dispatch_id, "--file", str(path))
    assert "1 matched an obligation this dispatch raised, 1 matched none" in result.out
    assert "a second docs_verdicts[] entry names this section" in result.out


def test_settle_from_report_applies_the_matched_entries_as_one_controller_act(
    kyo, git_project: Path
) -> None:
    """c4's second half. Each obligation gets the verdict, method and reason the worker
    wrote for THAT section — and the author on every row is the controller, because the
    controller is who applied them."""
    record, dispatch_id, path = _project_reporting_verdicts(
        kyo,
        git_project,
        [
            _verdict("README.md", "Running the CLI", "updated", method="rewrote it",
                     reason="the operating steps changed"),
            _verdict("docs/architecture.md", "Why the CLI is wired this way",
                     method="read it", reason="the reasoning still holds"),
        ],
    )
    assert kyo("report", dispatch_id, "--file", str(path), "--json").code == 0

    applied = kyo("docs", "settle", "--from-report", dispatch_id, "--json")
    assert applied.code == 0, applied
    assert applied.json["settled"] == 2
    stored = kyo("docs", "obligations", "--all", "--json").json["obligations"]
    readme = next(row for row in stored if row["doc_path"] == "README.md")
    assert readme["verdict"] == "updated"
    assert readme["method"] == "rewrote it"
    assert readme["settled_why"] == "the operating steps changed"
    assert readme["author"] != "worker", "the controller is who applied these"
    # Two verdicts in one act, which a hand-typed set may not carry: each is its own
    # statement written by the party who did that reading, and nothing was merged.
    verdicts = {row["verdict"] for row in stored if row["verdict"]}
    assert verdicts == {"updated", "not-affected"}
    assert kyo("docs", "obligations", "--json").json["open"] == 1
    assert record


def test_only_applies_a_subset_and_leaves_the_rest_readable(kyo, git_project: Path) -> None:
    """Partial acceptance: the controller checked one of them and says so, rather than
    applying a reading they have not done."""
    record, dispatch_id, path = _project_reporting_verdicts(
        kyo,
        git_project,
        [
            _verdict("README.md", "Running the CLI", "updated"),
            _verdict("docs/architecture.md", "Why the CLI is wired this way"),
        ],
    )
    raised = kyo("report", dispatch_id, "--file", str(path), "--json").json
    readme = next(
        row["id"]
        for row in raised["docs_obligations"]["raised"]
        if row["doc_path"] == "README.md"
    )
    applied = kyo("docs", "settle", "--from-report", dispatch_id, "--only", readme, "--json")
    assert applied.code == 0, applied
    assert [row["obligation_id"] for row in applied.json["applied"]] == [readme]
    assert "--only: 1 of the report's 2 matched verdict(s) selected" in kyo(
        "docs", "settle", "--from-report", dispatch_id, "--only", readme
    ).out, "the unapplied remainder has to be said, not left to be counted"
    still = kyo("docs", "obligations", "--json").json
    assert readme not in json.dumps(still["obligations"])
    assert record


def test_an_already_settled_obligation_is_named_rather_than_overwritten(
    kyo, git_project: Path
) -> None:
    """Verdicts are provenance (law 9). Unlike the hand-typed path this does not refuse
    the whole run for one already-settled row: the rest is a reading the controller has
    reviewed, and sending them back to per-obligation retyping over one row is the cost
    this exists to remove."""
    _, dispatch_id, path = _project_reporting_verdicts(
        kyo,
        git_project,
        [
            _verdict("README.md", "Running the CLI", "updated"),
            _verdict("docs/architecture.md", "Why the CLI is wired this way"),
        ],
    )
    raised = kyo("report", dispatch_id, "--file", str(path), "--json").json
    readme = next(
        row["id"]
        for row in raised["docs_obligations"]["raised"]
        if row["doc_path"] == "README.md"
    )
    assert kyo(
        "docs", "settle", readme, "--verdict", "not-affected", "--method", "I read it first"
    ).code == 0

    applied = kyo("docs", "settle", "--from-report", dispatch_id)
    assert applied.code == 0, applied
    assert "already carries the verdict 'not-affected'" in applied.out
    assert "applied 1 of 2" in applied.out
    row = next(
        r
        for r in kyo("docs", "obligations", "--all", "--json").json["obligations"]
        if r["id"] == readme
    )
    assert row["method"] == "I read it first", "the standing verdict was overwritten"


def test_from_report_refuses_the_flags_it_supplies(kyo, git_project: Path) -> None:
    """A verdict typed beside --from-report is the retyping this path removes, and
    honouring one would silently overwrite what the worker actually reported."""
    _, dispatch_id, path = _project_reporting_verdicts(
        kyo, git_project, [_verdict("README.md", "Running the CLI")]
    )
    assert kyo("report", dispatch_id, "--file", str(path), "--json").code == 0
    refused = kyo(
        "docs", "settle", "--from-report", dispatch_id, "--verdict", "updated",
        "--method", "mine",
    )
    assert refused.code != 0
    assert "--verdict" in refused.out + refused.err
    assert "--only <obligation-id>" in refused.out + refused.err


def test_only_without_from_report_is_refused_rather_than_ignored(
    kyo, git_project: Path
) -> None:
    """Silently ignoring it would settle every obligation in a set the caller asked to
    settle part of."""
    _project_with_a_governed_doc(kyo, git_project)
    refused = kyo("docs", "settle", "--only", "TST-1.o1", "--verdict", "updated",
                  "--method", "x")
    assert refused.code != 0
    assert "--from-report" in refused.out + refused.err


def test_only_naming_an_unmatched_obligation_applies_nothing(kyo, git_project: Path) -> None:
    """All or nothing on the selection itself: a mistyped id must not quietly become
    'apply the ones I spelled right'."""
    _, dispatch_id, path = _project_reporting_verdicts(
        kyo, git_project, [_verdict("README.md", "Running the CLI")]
    )
    assert kyo("report", dispatch_id, "--file", str(path), "--json").code == 0
    refused = kyo("docs", "settle", "--from-report", dispatch_id, "--only", "TST-1.o99")
    assert refused.code != 0
    assert "Nothing was applied" in refused.out + refused.err
    assert kyo("docs", "obligations", "--json").json["open"] == 3


def test_a_dispatch_with_no_ingested_report_says_which_command_ingests_one(
    kyo, git_project: Path
) -> None:
    """Law 6 with its remedy attached: the state is named and so is the command that
    leaves it."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-137", "--json").json[
        "dispatch_id"
    ]
    refused = kyo("docs", "settle", "--from-report", dispatch_id)
    assert refused.code != 0
    assert f"kyo report {dispatch_id}" in refused.out + refused.err


# ------------ c5: the rule reaches the worker from the brief, not from a task paragraph


def test_the_brief_names_docs_verdicts_beside_criteria_verdicts_and_the_preview(
    kyo, git_project: Path
) -> None:
    """c5's second half. The instruction is generated, not typed into a task block per
    dispatch — an instruction only some briefs carry is one most workers never receive
    (the same reasoning KYO-90's amendment paragraph gives about itself)."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    assert kyo("dispatch", record, "--branch", "kyo-137", "--json").code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]

    report_section = brief.split("## Report")[1]
    assert "`docs_verdicts[]`" in report_section
    assert "criteria_verdicts[]" in report_section
    assert "kyo docs preview --against main" in report_section
    # The stance, in the same breath as the instruction: nothing the worker writes
    # settles anything.
    assert "your orchestrator settles and you propose" in report_section


def test_the_packaged_ledger_carries_the_rule_with_a_mechanism_behind_it() -> None:
    """c5's first half. `enforced-by` is not decoration (law 1): the rule cites
    `kyo report`'s matching, which exists (`docs_numbered.match_docs_verdicts`) and is
    exercised above — so this must not read `aspirational`."""
    from kyosei.procedures import parse_procedures

    packaged = (
        Path(__file__).resolve().parents[1] / "src" / "kyosei" / "defaults" / "procedures.md"
    ).read_text(encoding="utf-8")
    rule = next(
        r
        for r in parse_procedures(packaged)
        if r.title.startswith("Read each governed section your branch raises")
    )
    assert rule.audience == "worker"
    assert "aspirational" not in rule.enforced_by
    assert "unmatched" in rule.enforced_by
    assert "kyo docs preview" in packaged


def test_the_rule_reaches_a_worker_through_the_generated_brief(kyo, git_project: Path) -> None:
    """Law 15: a rule that lives where its audience does not look is not a rule. The
    worker-audience block of a dispatch brief is where this audience looks."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    assert kyo("dispatch", record, "--branch", "kyo-137", "--json").code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    rules = brief.split("## Standing rules (worker-audience)")[1].split("## Standing stance")[0]
    assert "Read each governed section your branch raises" in rules


# --------------------------------------------------------- KYO-211.c3: after the merge


def test_docs_verdicts_match_obligations_raised_from_the_fallback_source(
    kyo, git_project: Path
) -> None:
    """KYO-211.c3. The whole value of c4's fallback is that everything downstream of the
    join behaves as if the branch were still live — so a worker's `docs_verdicts[]` entry
    matches an obligation raised from the report's `touched_files[]` exactly as it matches
    one raised from a three-dot diff. Matching is by (document, section), which is why it
    can: the pair is a fact about the document, not about which git command found it.
    """
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    _, reported = _merged_ingest(
        kyo,
        git_project,
        record,
        touched_files=["src/app.py"],
        docs_verdicts=[
            {
                "doc_path": "docs/architecture.md",
                "section": "How the CLI is wired",
                "verdict": "not-affected",
                "method": "reasoned-about",
                "reason": "the wiring the section describes did not change",
            }
        ],
    )
    assert reported.code == 0, reported
    obligations = reported.json["docs_obligations"]
    # KYO-411: after a merge the two-dot diff from the recorded base sha is read
    # and unioned with touched_files[]; matching is unchanged either way.
    assert obligations["changed_source"] in (
        "report-touched-files",
        "two-dot-diff-from-the-recorded-base-sha+report-touched-files",
    )
    assert obligations["verdicts_matched"] == 1, obligations
    assert obligations["verdicts_unmatched"] == []
    answered = obligations["verdicts_answered"]
    assert answered == [row["id"] for row in obligations["raised"]]
    # And the proposal rides on the obligation it answered, as it does for a live branch.
    assert obligations["raised"][0]["proposed"]["verdict"] == "not-affected"
