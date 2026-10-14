"""KYO-137: docs obligations, end to end.

Eight criteria over one subsystem, filed deliberately large. What each test here is
for, in the criteria's own terms:

* **c1** a record declares the documents it affects, under ``doc-refs``, validated
  against the repository the way ``touches`` is.
* **c2** at report time, a dispatch whose changed files intersect a document's
  ``governs:`` targets raises an obligation naming that document and that section.
* **c3** one obligation per ``governs:``-bearing section, never per file.
* **c4** a document counts as updated when a controller records a verdict with a
  method — not when its timestamp moves. This is the one most easily lost, because a
  timestamp comparison already exists and would have been the cheap thing to reuse.
* **c5** an open obligation refuses the completion move, naming the obligation.
* **c6** nothing under ``ideas/`` ever raises one.
* **c7** a record whose description predates a claim it cites is surfaced as a prompt,
  from the store alone, refusing nothing.
* **c8** ``governs_staleness()``'s ``cannot-tell`` and ``ungoverned`` never raise, so a
  project that declared nothing is not flagged as stale.

The provenance of the whole thing is a consumer report: the controller had just merged a
dispatch and had no prompt pointing at the document.
Every test below is a shape of that failure, or a shape of the false positive that
would be the wrong cure for it (KYO-33).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei import dispatch_docs, docs_obligations, docs_regions, docs_staleness
from kyosei.contracts import (
    CONTRACT_COMPLETE_REQUIRES_DOCS_OBLIGATIONS_SETTLED,
    CONTRACT_DOCS_OBLIGATION_ALREADY_SETTLED,
)

from .conftest import new_record, valid_report, verify_criterion


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(project: Path, path: str, text: str) -> None:
    target = project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(project: Path, message: str) -> str:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", message)
    return _git(project, "rev-parse", "HEAD")


def _branch_editing(project: Path, branch: str, edits: dict[str, str]) -> str:
    """Make ``branch`` off HEAD, write ``edits`` on it, commit, and come back."""
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", branch)
    for path, text in edits.items():
        _write(project, path, text)
    sha = _commit(project, f"work on {branch}")
    _git(project, "checkout", "-q", base)
    return sha


def _declare_touches(kyo, project: Path, record: str, *paths: str) -> None:
    scope = project / f"{record}-touches.txt"
    scope.write_text("".join(p + "\n" for p in paths), encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0


def _declare_doc_refs(kyo, project: Path, record: str, *lines: str):
    scope = project / f"{record}-doc-refs.txt"
    scope.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return kyo("edit", record, "--doc-refs-file", str(scope))


def _report(kyo, project: Path, dispatch_id: str, record: str, sha: str, **extra):
    path = project / f"report-{dispatch_id}.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=sha,
                **extra,
            )
        ),
        encoding="utf-8",
    )
    return kyo("report", dispatch_id, "--file", str(path), "--json")


GOVERNED_DOC = """# The architecture

## How the CLI is wired

governs: src/app.py

The CLI hands each subcommand a context and an argument namespace.

## How releases are cut

governs: release.sh

Tag, build, publish.
"""


def _project_with_a_governed_doc(kyo, project: Path) -> tuple[str, str]:
    """A git project, one document governing two files, a record, and a branch.

    Returns ``(record_id, base_branch)``. The branch ``kyo-137`` changes ``src/app.py``
    and nothing else, so exactly one of the document's two sections is implicated —
    which is what makes this fixture a c3 test as well as a c2 one.
    """
    _write(project, "docs/architecture.md", GOVERNED_DOC)
    _write(project, "src/app.py", "x = 1\n")
    _write(project, "release.sh", "echo release\n")
    _commit(project, "baseline")
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    _branch_editing(project, "kyo-137", {"src/app.py": "x = 2\n"})
    return record, base


def _dispatch_and_report(kyo, project: Path, record: str, branch: str = "kyo-137"):
    dispatched = kyo("dispatch", record, "--branch", branch, "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(project, "rev-parse", branch)
    result = _report(kyo, project, dispatch_id, record, sha)
    assert result.code == 0, result
    return dispatch_id, result


# ------------------------------------------------------------------------ KYO-137.c1


def test_a_record_can_declare_the_documents_it_affects(kyo, git_project: Path) -> None:
    """The back path: a record names the documents it affects under ``doc-refs``."""
    _write(git_project, "README.md", "# Readme\n\n## Replaying a shift\n\nHow to.\n")
    _commit(git_project, "readme")
    record = new_record(kyo, criteria=["a"])

    result = _declare_doc_refs(kyo, git_project, record, "README.md#Replaying a shift")
    assert result.code == 0, result

    shown = kyo("show", record, "--json").json
    assert [(r["path"], r["section"]) for r in shown["doc_refs"]] == [
        ("README.md", "Replaying a shift")
    ]
    assert "doc-refs (1):" in kyo("show", record).out


def test_a_doc_ref_naming_a_missing_file_is_refused_like_a_touch(
    kyo, git_project: Path
) -> None:
    """"Validated against the repository the way ``touches`` is" (c1), which means the
    same refusal shape and the same collect-then-report style (law 6): three bad lines
    are fixed in one pass, not by rerunning once per line."""
    _write(git_project, "README.md", "# Readme\n")
    _commit(git_project, "readme")
    record = new_record(kyo, criteria=["a"])

    result = _declare_doc_refs(
        kyo, git_project, record, "nope.md", "README.md#no such heading"
    )
    assert result.code == 1, result
    assert "doc-ref validation failed" in result.err
    # Both problems, not just the first.
    assert "nope.md" in result.err
    assert "no such heading" in result.err
    assert kyo("show", record, "--json").json["doc_refs"] == []


def test_doc_refs_replace_rather_than_append(kyo, git_project: Path) -> None:
    """`_replace_doc_refs`, on `_replace_touches`' reasoning: a doc-refs list is a
    working scope written before the work, not a claim, and an empty file means an
    empty list rather than "no change" (law 6)."""
    _write(git_project, "README.md", "# Readme\n")
    _write(git_project, "docs/architecture.md", "# Architecture\n")
    _commit(git_project, "docs")
    record = new_record(kyo, criteria=["a"])

    assert _declare_doc_refs(kyo, git_project, record, "README.md").code == 0
    assert _declare_doc_refs(kyo, git_project, record, "docs/architecture.md").code == 0
    refs = kyo("show", record, "--json").json["doc_refs"]
    assert [r["path"] for r in refs] == ["docs/architecture.md"]

    assert _declare_doc_refs(kyo, git_project, record).code == 0
    assert kyo("show", record, "--json").json["doc_refs"] == []


# --------------------------------------------------------------------- KYO-137.c2, c3


def test_report_raises_an_obligation_for_the_governing_section(
    kyo, git_project: Path
) -> None:
    """c2, and the join that is the mechanism: `kyo report` already knows what the
    branch changed (KYO-71) and the document already names what it governs."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)

    obligations = result.json["docs_obligations"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_RAISED
    assert len(obligations["raised"]) == 1
    raised = obligations["raised"][0]
    assert raised["doc_path"] == "docs/architecture.md"
    assert raised["section"] == "How the CLI is wired"
    assert raised["cause"] == docs_obligations.OBLIGATION_GOVERNS
    # The evidence, not just the verdict: which target, and which changed file.
    assert "src/app.py" in raised["detail"]


def test_an_obligation_is_per_section_not_per_file(kyo, git_project: Path) -> None:
    """c3: a README with one stale paragraph is not a stale README. The document here
    governs two files and the branch changed one, so the untouched section must not be
    named — reporting per file would name both."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)

    sections = [r["section"] for r in result.json["docs_obligations"]["raised"]]
    assert sections == ["How the CLI is wired"]
    assert "How releases are cut" not in json.dumps(result.json["docs_obligations"])


def test_two_governing_sections_both_changed_raise_two_obligations(
    kyo, git_project: Path
) -> None:
    """The other half of c3: per-section is not a synonym for "at most one per file"."""
    _write(git_project, "docs/architecture.md", GOVERNED_DOC)
    _write(git_project, "src/app.py", "x = 1\n")
    _write(git_project, "release.sh", "echo release\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py", "release.sh")
    _branch_editing(
        git_project, "kyo-137", {"src/app.py": "x = 2\n", "release.sh": "echo v2\n"}
    )
    _, result = _dispatch_and_report(kyo, git_project, record)

    sections = sorted(r["section"] for r in result.json["docs_obligations"]["raised"])
    assert sections == ["How releases are cut", "How the CLI is wired"]


def test_a_governs_line_inside_a_code_fence_raises_nothing(kyo, git_project: Path) -> None:
    """KYO-129 made root-level ``*.md`` a document by default, and a README is the one
    markdown file guaranteed to contain examples of this format. A quoted ``governs:``
    is inert (`docs_parse._fenced`) and an obligation must not fire on one — without
    this, every consumer's README becomes a live claim the day they upgrade."""
    _write(
        git_project,
        "README.md",
        "# Readme\n\n## How to declare one\n\nWrite it like this:\n\n"
        "```markdown\ngoverns: src/app.py\n```\n",
    )
    _write(git_project, "src/app.py", "x = 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    _branch_editing(git_project, "kyo-137", {"src/app.py": "x = 2\n"})
    _, result = _dispatch_and_report(kyo, git_project, record)

    assert result.json["docs_obligations"]["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NONE


def test_a_doc_ref_raises_an_obligation_the_join_would_miss(kyo, git_project: Path) -> None:
    """The residual `doc-refs` exists for, and the consumer's actual case: a README
    paragraph ABOUT a record's subject that cites none of its files. No ``governs:``
    target could catch it, which is why the back path is not redundant with the join."""
    _write(
        git_project,
        "README.md",
        "# Readme\n\n## Replaying a captured shift\n\nProse about the subject.\n",
    )
    _write(git_project, "src/app.py", "x = 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    assert (
        _declare_doc_refs(
            kyo, git_project, record, "README.md#Replaying a captured shift"
        ).code
        == 0
    )
    _branch_editing(git_project, "kyo-137", {"src/app.py": "x = 2\n"})
    _, result = _dispatch_and_report(kyo, git_project, record)

    raised = result.json["docs_obligations"]["raised"]
    assert [(r["doc_path"], r["section"], r["cause"]) for r in raised] == [
        ("README.md", "Replaying a captured shift", docs_obligations.OBLIGATION_DOC_REFS)
    ]


def test_a_branchless_dispatch_is_not_checked_rather_than_clean(
    kyo, git_project: Path
) -> None:
    """Law 6, the shape KYO-85 already gave touch drift: "the comparison did not run"
    must never render as "no document is implicated"."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    dispatch_id = kyo("dispatch", record, "--json").json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "HEAD")
    result = _report(kyo, git_project, dispatch_id, record, sha)

    assert result.code == 0, result
    obligations = result.json["docs_obligations"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NOT_CHECKED
    assert obligations["detail"]


def test_an_amended_report_does_not_raise_the_same_obligation_twice(
    kyo, git_project: Path
) -> None:
    """A worker resumed on a dispatch files an amending report (KYO-90) over the same
    branch. Re-raising would make an obligation the controller had already settled
    reappear open, so the amendment path would punish itself for being used."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    dispatch_id, first = _dispatch_and_report(kyo, git_project, record)
    obligation_id = first.json["docs_obligations"]["raised"][0]["id"]
    assert (
        kyo(
            "docs", "settle", obligation_id, "--verdict", "updated", "--method", "re-read it"
        ).code
        == 0
    )

    sha = _git(git_project, "rev-parse", "kyo-137")
    again = _report(
        kyo,
        git_project,
        dispatch_id,
        record,
        sha,
        amends=first.json["report_id"],
    )
    assert again.code == 0, again
    assert again.json["docs_obligations"]["raised"] == []
    obligations = kyo("docs", "obligations", "--all", "--json").json["obligations"]
    assert len(obligations) == 1
    assert obligations[0]["verdict"] == "updated"


# ------------------------------------------------------------------------ KYO-137.c4


def test_a_document_is_updated_when_a_verdict_says_so_not_when_its_mtime_moves(
    kyo, git_project: Path
) -> None:
    """c4, and the criterion the brief warned would get quietly
    implemented as a timestamp comparison because that is what already exists.

    The document is edited AND committed after the code — so ``governs_staleness()``
    now reports it ``current`` — and the obligation is still open, because nothing but
    a controller's assertion closes one.
    """
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    _write(git_project, "docs/architecture.md", GOVERNED_DOC + "\nUpdated prose.\n")
    _commit(git_project, "update the document")
    # The timestamp answer now says the document is current...
    staleness = docs_staleness.governs_staleness(
        git_project, "docs/architecture.md", ["src/app.py"]
    )
    assert staleness.state == docs_staleness.STALENESS_CURRENT
    # ...and the obligation is still open, because a timestamp is not a verdict.
    still_open = kyo("docs", "obligations", "--json").json
    assert [o["id"] for o in still_open["obligations"]] == [obligation_id]

    settled = kyo(
        "docs",
        "settle",
        obligation_id,
        "--verdict",
        "updated",
        "--method",
        "rewrote the section against the diff",
    )
    assert settled.code == 0, settled
    assert kyo("docs", "obligations", "--json").json["open"] == 0


def test_a_verdict_carries_its_method_and_author(kyo, git_project: Path) -> None:
    """Law 9. A verdict without its instrument is a claim whose weight cannot be
    judged later, which is the whole difference between this and a timestamp."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    settled = kyo(
        "docs",
        "settle",
        obligation_id,
        "--verdict",
        "not-affected",
        "--method",
        "read the section",
        "--why",
        "the change is internal to a function the section does not describe",
        "--json",
    )
    assert settled.code == 0, settled
    row = settled.json["obligation"]
    assert row["verdict"] == "not-affected"
    assert row["method"] == "read the section"
    assert row["author"] == "orchestrator"  # KYO-519: the default actor
    assert row["settled_at"]
    assert "does not describe" in row["settled_why"]


def test_an_unknown_verdict_is_refused_with_the_vocabulary_named(
    kyo, git_project: Path
) -> None:
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    refused = kyo(
        "docs", "settle", obligation_id, "--verdict", "probably-fine", "--method", "x"
    )
    assert refused.code == 64, refused
    assert "updated" in refused.err and "not-affected" in refused.err


def test_a_settled_obligation_is_not_silently_re_settled(kyo, git_project: Path) -> None:
    """Law 9: a verdict is provenance, and overwriting one destroys the contest that is
    the valuable part. The refusal names the standing verdict and who recorded it."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]
    assert (
        kyo("docs", "settle", obligation_id, "--verdict", "updated", "--method", "a").code
        == 0
    )

    again = kyo(
        "docs", "settle", obligation_id, "--verdict", "not-affected", "--method", "b"
    )
    assert again.code == 2, again
    assert CONTRACT_DOCS_OBLIGATION_ALREADY_SETTLED in again.err
    assert "updated" in again.err


# ------------------------------------------------------------------------ KYO-137.c5


def test_an_open_obligation_refuses_the_completion_move_and_names_it(
    kyo, git_project: Path
) -> None:
    """c5, and what makes raising an obligation and settling it by verdict a mechanism
    rather than an exhortation."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]
    verify_criterion(kyo, f"{record}.c1")

    refused = kyo("move", record, "done")
    assert refused.code == 2, refused
    assert CONTRACT_COMPLETE_REQUIRES_DOCS_OBLIGATIONS_SETTLED in refused.err
    # Named, per c5 — the id, the document and the section, not just a count.
    assert obligation_id in refused.err
    assert "docs/architecture.md" in refused.err
    assert "How the CLI is wired" in refused.err
    assert kyo("show", record, "--json").json["status"] != "done"


def test_settling_the_obligation_clears_the_refusal(kyo, git_project: Path) -> None:
    """KYO-5.c2: an error naming a remedy must have a test proving the remedy clears
    the error. The remedy this one names is `kyo docs settle`."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]
    verify_criterion(kyo, f"{record}.c1")
    assert kyo("move", record, "done").code == 2

    assert (
        kyo(
            "docs", "settle", obligation_id, "--verdict", "updated", "--method", "rewrote it"
        ).code
        == 0
    )
    moved = kyo("move", record, "done")
    assert moved.code == 0, moved
    assert kyo("show", record, "--json").json["status"] == "done"


def test_an_open_obligation_does_not_block_retirement(kyo, git_project: Path) -> None:
    """The deliberate carve-out. A record being abandoned ships no code, so the
    document it would have made stale is not stale — and refusing `kyo wont-do` would
    trap a record nobody intends to finish behind a documentation task nobody owes."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _dispatch_and_report(kyo, git_project, record)

    retired = kyo("wont-do", record, "--why", "the approach was wrong")
    assert retired.code == 0, retired
    # And the obligation stays open and readable rather than being settled by a side
    # effect: nothing observed the document, so nothing may claim to have.
    assert kyo("docs", "obligations", "--json").json["open"] == 1


# ------------------------------------------------------------------------ KYO-137.c6


def test_a_document_under_ideas_never_raises_an_obligation(kyo, git_project: Path) -> None:
    """c6. Intake is zero-ceremony and an idea file is SUPPOSED to
    be a stale snapshot of a thought; sweeping them would generate noise in proportion
    to how well the intake layer is being used."""
    _write(
        git_project,
        "ideas/thinking.md",
        "# A thought\n\n## Something\n\ngoverns: src/app.py\n\nHalf an idea.\n",
    )
    _write(git_project, "src/app.py", "x = 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    _branch_editing(git_project, "kyo-137", {"src/app.py": "x = 2\n"})
    _, result = _dispatch_and_report(kyo, git_project, record)

    assert result.json["docs_obligations"]["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NONE


def test_a_changed_file_under_ideas_never_raises_an_obligation(
    kyo, git_project: Path
) -> None:
    """The other direction of c6, and the one a document-side-only guard would miss: a
    real document governing a path under ``ideas/`` that the branch changed."""
    _write(
        git_project,
        "docs/architecture.md",
        "# Architecture\n\n## Intake\n\ngoverns: ideas/thinking.md\n\nProse.\n",
    )
    _write(git_project, "ideas/thinking.md", "# A thought\n")
    _write(git_project, "src/app.py", "x = 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    _branch_editing(git_project, "kyo-137", {"ideas/thinking.md": "# Edited\n"})

    obligations = docs_obligations.obligations_for_changed_files(
        git_project, ["ideas/thinking.md"]
    )
    assert obligations == []


def test_the_unobligated_prefixes_are_the_guarded_ones(kyo) -> None:
    """Law 3, and the reason this is one tuple rather than two: "the store and the
    intake layer" is one fact. If `kyo check-branch`'s guarded set ever grows, this
    grows with it rather than drifting behind."""
    from kyosei.contracts import GUARDED_BRANCH_DIRS

    assert docs_obligations.UNOBLIGATED_PREFIXES == GUARDED_BRANCH_DIRS
    assert "ideas/" in docs_obligations.UNOBLIGATED_PREFIXES


# ------------------------------------------------------------------------ KYO-137.c8


def test_an_ungoverned_document_raises_nothing(kyo, git_project: Path) -> None:
    """c8's first half. A project that declared nothing is not flagged as stale —
    which is the state every consumer is in on the day they upgrade, and flagging them
    would be this build asserting a defect it has not observed."""
    _write(git_project, "docs/architecture.md", "# Architecture\n\nProse, no governs.\n")
    _write(git_project, "src/app.py", "x = 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    _branch_editing(git_project, "kyo-137", {"src/app.py": "x = 2\n"})
    _, result = _dispatch_and_report(kyo, git_project, record)

    assert result.json["docs_obligations"]["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NONE
    staleness = docs_staleness.governs_staleness(git_project, "docs/architecture.md", [])
    assert staleness.state == docs_staleness.STALENESS_UNGOVERNED
    assert not docs_obligations.staleness_raises_obligation(staleness.state)


def test_a_project_with_no_documents_raises_nothing(kyo, git_project: Path) -> None:
    """c8's stated consequence, one level up at file discovery — the state KYO-129's
    ``no-documents`` was built to make visible."""
    _write(git_project, "src/app.py", "x = 1\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    _branch_editing(git_project, "kyo-137", {"src/app.py": "x = 2\n"})
    _, result = _dispatch_and_report(kyo, git_project, record)

    assert result.json["docs_obligations"]["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NONE


def test_cannot_tell_and_ungoverned_are_never_raising_states(kyo) -> None:
    """c8 stated as the predicate the code actually consults, not only as a case.

    A whitelist of exactly one value: every state added to the staleness vocabulary is
    non-raising until someone decides otherwise, because the failure that costs here is
    a flag raised on no evidence (KYO-33), not one withheld.
    """
    assert not docs_obligations.staleness_raises_obligation(docs_staleness.STALENESS_CANNOT_TELL)
    assert not docs_obligations.staleness_raises_obligation(docs_staleness.STALENESS_UNGOVERNED)
    assert not docs_obligations.staleness_raises_obligation(docs_staleness.STALENESS_CURRENT)
    assert docs_obligations.staleness_raises_obligation(docs_staleness.STALENESS_STALE)
    # And the whitelist covers the whole vocabulary, so a state added later is not
    # silently admitted by a check that only knew about four.
    raising = [
        s
        for s in docs_staleness.STALENESS_STATES
        if docs_obligations.staleness_raises_obligation(s)
    ]
    assert raising == [docs_staleness.STALENESS_STALE]


def test_an_undatable_repository_raises_nothing_through_the_staleness_path(
    kyo, tmp_path: Path
) -> None:
    """c8's second half, at the level it is decidable: git declining to answer is not
    evidence of staleness, and the honest thing to do with an undecided question at a
    blocking gate is not to block on it."""
    root = tmp_path / "nogit"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "docs" / "architecture.md").write_text(GOVERNED_DOC, encoding="utf-8")

    staleness = docs_staleness.governs_staleness(root, "docs/architecture.md", ["src/app.py"])
    assert staleness.state == docs_staleness.STALENESS_CANNOT_TELL
    assert not docs_obligations.staleness_raises_obligation(staleness.state)


# ------------------------------------------------------------------------ KYO-137.c7


def _description_flags(swept) -> list[dict]:
    """The c7 flags one `kyo sweep --json` run created."""
    assert swept.code == 0, swept
    return [
        flag
        for flag in swept.json["flags"]
        if flag["flag_type"] == "description-predates-claim"
    ]


def _claim(kyo, project: Path, record: str, statement: str):
    path = project / f"claim-{record}-{abs(hash(statement))}.txt"
    path.write_text(statement, encoding="utf-8")
    return kyo(
        "claim",
        record,
        "--file",
        str(path),
        "--level",
        "asserted",
        "--method",
        "read it",
    )


def test_a_description_older_than_a_claim_is_surfaced_as_a_prompt(
    kyo, git_project: Path
) -> None:
    """c7, and a consumer's instance (APP-58): a record's own description going stale
    against its own cited evidence claim, both inside the store — the one case where
    Kyosei holds both halves and can compare them without reading the repository."""
    record = new_record(kyo, criteria=["a"])
    description = git_project / "description.md"
    description.write_text("What this record is about.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0
    assert _claim(kyo, git_project, record, "the measurement came out at 315").code == 0

    swept = kyo("sweep", "--json")
    assert swept.code == 0, swept
    mine = _description_flags(swept)
    assert len(mine) == 1, swept
    assert mine[0]["record_id"] == record
    assert "315" in mine[0]["detail"]


def test_the_prompt_refuses_nothing(kyo, git_project: Path) -> None:
    """"without repository access and without refusing anything" (c7). A claim being
    newer than a description is not proof the description is wrong, so this may never
    stand between a record and any move it could otherwise make."""
    record = new_record(kyo, criteria=["a"])
    description = git_project / "description.md"
    description.write_text("What this record is about.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0
    assert _claim(kyo, git_project, record, "a later measurement").code == 0
    assert kyo("sweep").code == 0
    verify_criterion(kyo, f"{record}.c1")

    moved = kyo("move", record, "done")
    assert moved.code == 0, moved


def test_a_description_edited_after_its_claims_is_not_flagged(
    kyo, git_project: Path
) -> None:
    """The bound that keeps this from being wallpaper. Editing the description after
    the claim is the answer to the prompt, and the prompt has to notice."""
    record = new_record(kyo, criteria=["a"])
    assert _claim(kyo, git_project, record, "the measurement came out at 315").code == 0
    description = git_project / "description.md"
    description.write_text("Rewritten after the claim landed.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0

    assert _description_flags(kyo("sweep", "--json")) == []


def test_a_superseded_claim_does_not_flag_the_description(kyo, git_project: Path) -> None:
    """Comparing a description against a claim that has itself been replaced would
    flag history. `store.live_claims` is where that bound lives."""
    record = new_record(kyo, criteria=["a"])
    description = git_project / "description.md"
    description.write_text("What this record is about.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0
    first = _claim(kyo, git_project, record, "the first measurement, since replaced")
    assert first.code == 0, first
    claim_id = f"{record}.m1"
    path = git_project / "second.txt"
    path.write_text("the corrected measurement\n", encoding="utf-8")
    superseding = kyo(
        "claim",
        record,
        "--file",
        str(path),
        "--level",
        "verified",
        "--method",
        "re-measured",
        "--supersedes",
        claim_id,
    )
    assert superseding.code == 0, superseding

    # The superseding claim is itself newer than the description, so one flag is
    # expected — and it must name the LIVE claim, never the one it replaced.
    flags = _description_flags(kyo("sweep", "--json"))
    assert len(flags) == 1
    assert claim_id not in flags[0]["detail"]
    assert "corrected measurement" in flags[0]["detail"]


def test_description_edited_at_reads_the_events_ledger_not_updated_at(
    kyo, git_project: Path
) -> None:
    """The distinction the whole class rests on. `records.updated_at` moves whenever
    anything on the record moves, so against it "the description predates this claim"
    answers a different question — and answers it wrong in the safe-looking direction,
    reporting a stale description as fresh because something unrelated was edited.
    """
    from kyosei.config import load_config
    from kyosei.store import open_store

    record = new_record(kyo, criteria=["a"])
    description = git_project / "description.md"
    description.write_text("What this record is about.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0

    store = open_store(git_project, code_root=git_project)
    try:
        described_at = store.description_edited_at(record)
    finally:
        store.close()

    # Something unrelated moves the record on.
    assert kyo("edit", record, "--title", "A different title").code == 0
    store = open_store(git_project, code_root=git_project)
    try:
        assert store.description_edited_at(record) == described_at
        assert store.record(record)["updated_at"] > described_at
    finally:
        store.close()
    load_config  # noqa: B018 - imported to prove the store opens the same way commands do


def test_a_record_whose_description_never_changed_dates_from_its_creation(
    kyo, git_project: Path
) -> None:
    """The fallback is a real answer, not a default: the description dates from the
    record's creation because nothing has touched it since. It is also what a record
    created before this build has, whose history holds no such event to find."""
    from kyosei.store import open_store

    record = new_record(kyo, criteria=["a"])
    store = open_store(git_project, code_root=git_project)
    try:
        assert store.description_edited_at(record) == store.record(record)["created_at"]
    finally:
        store.close()


def test_one_flag_per_record_however_many_claims_are_newer(kyo, git_project: Path) -> None:
    """A record with several claims newer than its description has one thing wrong
    with it; several rows saying so is the per-file mush c3 rejects one level
    up. The claim named is the newest, because that is the one whose date makes it."""
    record = new_record(kyo, criteria=["a"])
    description = git_project / "description.md"
    description.write_text("What this record is about.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0
    for statement in ("the first finding", "the second finding", "the third finding"):
        assert _claim(kyo, git_project, record, statement).code == 0

    flags = _description_flags(kyo("sweep", "--json"))
    assert len(flags) == 1
    assert "third finding" in flags[0]["detail"]


def test_the_class_needs_no_repository_at_all(kyo) -> None:
    """"without repository access" (c7), asserted where it is decidable: the finder is
    a pure function over stored values, so the whole class can be exercised with no
    filesystem, no git, and no store."""
    from kyosei import sweep as sweep_module

    found = sweep_module.find_descriptions_predating_their_claims(
        [
            sweep_module.RecordClaims(
                record_id="TST-1",
                description_at="2026-08-01T00:00:00Z",
                claims=(
                    ("TST-1.m1", "2026-07-01T00:00:00Z", "older, and fine"),
                    ("TST-1.m2", "2026-09-01T00:00:00Z", "newer, and the one named"),
                ),
            ),
            sweep_module.RecordClaims(
                record_id="TST-2",
                description_at="2026-08-01T00:00:00Z",
                claims=(("TST-2.m1", "2026-07-01T00:00:00Z", "older"),),
            ),
            sweep_module.RecordClaims(record_id="TST-3", description_at="2026-08-01T00:00:00Z"),
        ]
    )
    assert [f.record_id for f in found] == ["TST-1"]
    assert found[0].claim_id == "TST-1.m2"


def test_settling_an_obligation_writes_an_event(kyo, git_project: Path) -> None:
    """`kyo docs settle` is a mutation and carries a mutation's obligations: an events
    row and a regenerated export (``commands._commit_and_export``/``_finish_mutation``).

    This assertion lives here rather than in ``tests/test_events.py`` with the rest of
    `MUTATING_COMMANDS` because settling needs an obligation to settle, which needs a
    git repository, a document, a branch, a dispatch and a report — the fixture at the
    top of this file. That module's own list names this test.
    """
    import sqlite3

    def events() -> int:
        conn = sqlite3.connect(git_project / "kyosei" / "kyosei.db")
        try:
            return conn.execute("SELECT count(*) FROM events").fetchone()[0]
        finally:
            conn.close()

    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    before = events()
    settled = kyo(
        "docs", "settle", obligation_id, "--verdict", "updated", "--method", "rewrote it"
    )
    assert settled.code == 0, settled
    assert events() > before, "docs settle mutated without writing an event"

    # And the git-canonical export carries the verdict, not just the database.
    exported = json.loads(
        (git_project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    assert exported["docs_obligations"][0]["verdict"] == "updated"
    assert exported["docs_obligations"][0]["method"] == "rewrote it"


def test_the_report_names_what_it_raised_in_its_human_output(kyo, git_project: Path) -> None:
    """The controller reads the text, not the JSON. All three states say something."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-137", "--json").json[
        "dispatch_id"
    ]
    sha = _git(git_project, "rev-parse", "kyo-137")
    path = git_project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(dispatch_id=dispatch_id, record_id=record, worked_from_sha=sha)
        ),
        encoding="utf-8",
    )
    result = kyo("report", dispatch_id, "--file", str(path))
    assert result.code == 0, result
    assert "docs obligations: 1 raised" in result.out
    assert "docs/architecture.md [How the CLI is wired]" in result.out
    assert "kyo docs settle" in result.out


def test_the_obligations_listing_reads_in_both_states(kyo, git_project: Path) -> None:
    """`kyo docs obligations` open-by-default, `--all` for the history, `--record` to
    narrow — and an empty result that says which question it answered rather than
    printing nothing (law 6)."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    listing = kyo("docs", "obligations")
    assert listing.code == 0, listing
    assert obligation_id in listing.out
    assert "1 open of 1" in listing.out

    assert kyo("docs", "settle", obligation_id, "--verdict", "updated", "--method", "x").code == 0
    after = kyo("docs", "obligations")
    assert "0 open of 1" in after.out
    assert "(none)" in after.out
    # --all still shows it, with the verdict that settled it.
    every = kyo("docs", "obligations", "--all")
    assert obligation_id in every.out
    assert "[updated]" in every.out
    # And --record narrows to one record's own.
    mine = kyo("docs", "obligations", "--all", "--record", record, "--json")
    assert [o["id"] for o in mine.json["obligations"]] == [obligation_id]
    other = kyo("docs", "obligations", "--all", "--record", "TST-999", "--json")
    assert other.json["obligations"] == []


def test_show_renders_a_settled_obligation_with_its_provenance(
    kyo, git_project: Path
) -> None:
    """`kyo show` is where a record's whole state is read. A settled obligation shows
    the verdict, the method and the author — an open one shows the word "open" rather
    than a blank a reader could take for either answer."""
    record, _ = _project_with_a_governed_doc(kyo, git_project)
    _, result = _dispatch_and_report(kyo, git_project, record)
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    open_text = kyo("show", record).out
    assert "docs obligations (1 open of 1):" in open_text
    assert "[open]" in open_text

    assert (
        kyo(
            "docs", "settle", obligation_id, "--verdict", "not-affected", "--method",
            "read the section",
        ).code
        == 0
    )
    settled_text = kyo("show", record).out
    assert "docs obligations (0 open of 1):" in settled_text
    assert "[not-affected]" in settled_text
    assert "read the section" in settled_text
    assert "orchestrator" in settled_text


# ------------------------------------------------------------------------ KYO-153
#
# The join validated the `#symbol` half of a target and then threw it away. These cover
# the fix and, more importantly, the shape of the fix: it may only ever suppress on a
# positive answer, because a weak instrument must never be re-admitted as a veto.

_APP = '''\
"""A module."""


def alpha(argument):
    """Alpha."""
    return argument + 1


def beta(argument):
    """Beta."""
    return argument + 2
'''

_SYMBOL_DOC = """# The architecture

## Alpha's contract

governs: src/app.py#alpha

Alpha adds one.
"""


def _symbol_project(kyo, project: Path, doc: str = _SYMBOL_DOC) -> str:
    _write(project, doc_path := "docs/architecture.md", doc)
    assert doc_path
    _write(project, "src/app.py", _APP)
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    return record


def _changes(project: Path, branch: str, *paths: str):
    return docs_regions.branch_file_changes(project, "main", branch, paths)


def _obligations(project: Path, branch: str, changed: list[str]):
    return docs_obligations.obligations_for_changed_files(
        project, changed, changes=_changes(project, branch, *changed)
    )


# ------------------------------------------------------------------------ KYO-153.c1


def test_a_symbol_target_stays_quiet_when_another_symbol_changed(
    kyo, git_project: Path
) -> None:
    """c1, and the whole point of the record. ``governs: src/app.py#alpha`` must not fire
    because ``beta`` moved. Before KYO-153 this raised: the join took
    ``target.partition('#')[0]`` and never looked at the symbol again, which is where
    KYO-144 measured 57% of one section's fires coming from."""
    _symbol_project(kyo, git_project)
    _branch_editing(git_project, "kyo-153", {"src/app.py": _APP.replace("+ 2", "+ 3")})

    assert _obligations(git_project, "kyo-153", ["src/app.py"]) == []


def test_a_symbol_target_fires_when_that_symbol_changed(kyo, git_project: Path) -> None:
    """The other half of c1, and the half that stops the fix from being a way to make the
    gate quiet: the same declaration on the same file must still raise when the region it
    names is the one that moved."""
    _symbol_project(kyo, git_project)
    _branch_editing(git_project, "kyo-153", {"src/app.py": _APP.replace("+ 1", "+ 9")})

    raised = _obligations(git_project, "kyo-153", ["src/app.py"])
    assert [(o.doc_path, o.section, o.targets) for o in raised] == [
        ("docs/architecture.md", "Alpha's contract", ("src/app.py#alpha",))
    ]


def test_an_insertion_inside_the_symbol_fires_without_naming_it(
    kyo, git_project: Path
) -> None:
    """The case that rules out the cheap instrument, stated as a test so nobody rebuilds
    it. Grepping the symbol's NAME in the diff hunks would suppress here: the added line
    lives inside ``alpha`` and does not contain the word ``alpha``. That is exactly the
    charter's ``cli.py#build_parser`` case — an ``add_parser(`` line added inside the
    function — so an instrument that misses it would suppress the true positives KYO-144
    counted while claiming to remove the false ones."""
    _symbol_project(kyo, git_project)
    _branch_editing(
        git_project,
        "kyo-153",
        {"src/app.py": _APP.replace('    """Alpha."""\n', '    """Alpha."""\n    pass\n')},
    )

    raised = _obligations(git_project, "kyo-153", ["src/app.py"])
    assert len(raised) == 1


def test_a_decorator_above_the_symbol_is_part_of_it(kyo, git_project: Path) -> None:
    """A change to ``@lru_cache`` above a function is a change to that function, so the
    region starts at the first decorator rather than at the ``def``."""
    decorated = _APP.replace("def alpha", "@staticmethod\ndef alpha")
    _write(git_project, "src/app.py", decorated)
    _write(git_project, "docs/architecture.md", _SYMBOL_DOC)
    _commit(git_project, "baseline")
    _branch_editing(
        git_project, "kyo-153", {"src/app.py": decorated.replace("@staticmethod", "@classmethod")}
    )

    assert len(_obligations(git_project, "kyo-153", ["src/app.py"])) == 1


def test_a_symbol_target_fires_when_the_symbol_is_deleted(kyo, git_project: Path) -> None:
    """Located on one side only. A definition this diff removed is the strongest possible
    'changed', and a region-intersection written carelessly would find no region on the
    after side and fall through to silence."""
    _symbol_project(kyo, git_project)
    _branch_editing(
        git_project, "kyo-153", {"src/app.py": '"""A module."""\n\n\ndef beta(a):\n    return a\n'}
    )

    assert len(_obligations(git_project, "kyo-153", ["src/app.py"])) == 1


# ------------------------------------------------------------------------ KYO-153.c2


def test_a_bare_path_target_still_fires_on_any_change(kyo, git_project: Path) -> None:
    """c2, the compatibility constraint. Precision is opt-in: a declaration that named no
    symbol keeps the file-level answer it was written against, so nothing weakens because
    this landed. Same file and same unrelated edit as the c1 test that goes quiet."""
    _symbol_project(kyo, git_project, _SYMBOL_DOC.replace("#alpha", ""))
    _branch_editing(git_project, "kyo-153", {"src/app.py": _APP.replace("+ 2", "+ 3")})

    assert len(_obligations(git_project, "kyo-153", ["src/app.py"])) == 1


def test_a_section_mixing_bare_and_narrowed_targets_raises_once_on_the_bare_one(
    kyo, git_project: Path
) -> None:
    """The mixed declaration, which is what a document actually looks like mid-migration.
    The narrowed target goes quiet and the bare one still speaks, so the section raises —
    and the obligation names only the target that actually matched, not both."""
    _write(git_project, "src/other.py", "y = 1\n")
    _symbol_project(
        kyo,
        git_project,
        _SYMBOL_DOC.replace("governs: src/app.py#alpha", "governs: src/app.py#alpha, src/other.py"),
    )
    _branch_editing(
        git_project,
        "kyo-153",
        {"src/app.py": _APP.replace("+ 2", "+ 3"), "src/other.py": "y = 2\n"},
    )

    raised = _obligations(git_project, "kyo-153", ["src/app.py", "src/other.py"])
    assert [o.targets for o in raised] == [("src/other.py",)]
    assert [o.changed for o in raised] == [("src/other.py",)]


# ------------------------------------------------------------------------ KYO-153.c5


def test_the_join_consults_the_symbol_and_not_only_the_path(kyo, git_project: Path) -> None:
    """c5 stated against the join itself rather than through a project, so it fails on the
    exact reversion it guards: restore ``path_part = target.partition('#')[0]`` and this
    raises, because the path IS in the changed set and only the symbol says otherwise."""
    change = docs_regions.FileChange(
        path="src/app.py",
        before_text=_APP,
        after_text=_APP.replace("+ 2", "+ 3"),
        before_lines=frozenset({11}),
        after_lines=frozenset({11}),
    )
    changed = frozenset({"src/app.py"})

    assert (
        docs_obligations._target_raises("src/app.py#alpha", changed, {"src/app.py": change})
        is None
    )
    # ...and the same target with no symbol, and the same symbol with the region moved,
    # both still raise — so the test cannot be satisfied by a join that raises nothing.
    assert (
        docs_obligations._target_raises("src/app.py", changed, {"src/app.py": change})
        == "src/app.py"
    )
    moved = docs_regions.FileChange(
        path="src/app.py",
        before_text=_APP,
        after_text=_APP.replace("+ 1", "+ 9"),
        before_lines=frozenset({6}),
        after_lines=frozenset({6}),
    )
    assert docs_obligations._target_raises("src/app.py#alpha", changed, {"src/app.py": moved}) == (
        "src/app.py"
    )


def test_symbol_regions_locates_python_and_markdown_and_nothing_else(kyo) -> None:
    """The instrument's three answers, which the caller is required to keep apart. A
    suffix it cannot read returns ``None`` — 'did not run' — and never ``()``, so a
    project whose documents govern Go keeps the file-level check it had."""
    assert docs_regions.symbol_regions("src/app.py", _APP, "alpha") == ((4, 6),)
    assert docs_regions.symbol_regions("src/app.py", _APP, "gamma") == ()
    assert docs_regions.symbol_regions("src/app.py", "def (\n", "alpha") is None
    assert docs_regions.symbol_regions("main.go", "func alpha() {}\n", "alpha") is None
    assert docs_regions.symbol_regions("doc.md", "# One\n\ntext\n\n# Two\n\nmore\n", "One") == (
        (1, 4),
    )
    assert ".py" in docs_regions.REGION_SUFFIXES and ".md" in docs_regions.REGION_SUFFIXES


def test_a_qualified_method_name_is_a_region(kyo) -> None:
    """``Store.allocate`` and ``allocate`` both name the method, so a document may narrow
    to a method without ambiguity and may also name one that happens to be unique."""
    source = (
        "class Store:\n    def allocate(self):\n        return 1\n\n\n"
        "def allocate():\n    return 2\n"
    )
    assert docs_regions.symbol_regions("s.py", source, "Store.allocate") == ((2, 3),)
    assert docs_regions.symbol_regions("s.py", source, "allocate") == ((2, 3), (6, 7))
    assert docs_regions.symbol_regions("s.py", source, "Store") == ((1, 3),)


def test_a_module_level_constant_is_a_region(kyo) -> None:
    """``contracts.py#GUARDED_BRANCH_DIRS`` is the shape half this project's own governed
    symbols would take, and a def-only region finder would call it cannot-tell forever."""
    source = "A = 1\nGUARDED = (\n    'kyosei/',\n)\nB = 2\n"
    assert docs_regions.symbol_regions("c.py", source, "GUARDED") == ((2, 4),)


# ------------------------------------- KYO-153: cannot-tell may never suppress


def test_every_way_the_instrument_can_fail_raises_rather_than_suppresses(kyo) -> None:
    """Cannot-tell never suppresses, as a property rather than four cases. An instrument
    demoted from settling a question must not be re-admitted as a veto: a raise is a
    visible question a controller dismisses in one command, a suppression is a silent
    false negative nobody ever sees. So every non-answer here must come back raising."""
    changed = frozenset({"src/app.py", "main.go"})
    cannot_tell = {
        "an unreadable before revision": docs_regions.FileChange(
            "src/app.py", None, _APP, frozenset(), frozenset({5})
        ),
        "an unreadable after revision": docs_regions.FileChange(
            "src/app.py", _APP, None, frozenset({5}), frozenset()
        ),
        "a revision that will not parse": docs_regions.FileChange(
            "src/app.py", "def (\n", _APP, frozenset({1}), frozenset({11})
        ),
        "a symbol that names no region": docs_regions.FileChange(
            "src/app.py", _APP, _APP.replace("+ 2", "+ 3"), frozenset({11}), frozenset({11})
        ),
    }
    for why, change in cannot_tell.items():
        symbol = "gamma" if why == "a symbol that names no region" else "alpha"
        assert docs_regions.symbol_region_changed(change, symbol) is None, why
        assert docs_obligations._target_raises(
            f"src/app.py#{symbol}", changed, {"src/app.py": change}
        ) == "src/app.py", why

    # A file type the instrument cannot read at all, and the case that matters most for a
    # consumer: it is not a failed lookup, it is an instrument that never ran.
    go = docs_regions.FileChange("main.go", "func alpha() {}\n", "func alpha() { x }\n")
    assert docs_regions.symbol_region_changed(go, "alpha") is None
    assert docs_obligations._target_raises("main.go#alpha", changed, {"main.go": go}) == "main.go"

    # And no diff evidence at all — every caller predating KYO-153, and every branch git
    # declined to diff. The pre-KYO-153 file-level answer is the conservative one.
    assert docs_obligations._target_raises("src/app.py#alpha", changed, {}) == "src/app.py"


def test_a_branch_that_cannot_be_diffed_raises_everything_it_used_to(
    kyo, git_project: Path
) -> None:
    """The degradation path end to end. ``branch_file_changes`` is best-effort by design —
    it can afford to be, because its only failure mode is an absent entry and an absent
    entry raises. An unknown ref must therefore produce the OLD number of obligations,
    not zero, which is the failure that would look like a clean run."""
    _symbol_project(kyo, git_project)
    _branch_editing(git_project, "kyo-153", {"src/app.py": _APP.replace("+ 2", "+ 3")})

    assert docs_regions.branch_file_changes(git_project, "main", "no-such-branch") == {}
    assert (
        len(
            docs_obligations.obligations_for_changed_files(
                git_project,
                ["src/app.py"],
                changes=docs_regions.branch_file_changes(git_project, "main", "no-such-branch"),
            )
        )
        == 1
    )


def test_governed_symbol_paths_names_only_what_a_target_narrows(kyo, git_project: Path) -> None:
    """What the diff read is scoped to. A target with no symbol is answered from its name
    alone, so fetching its two revisions would buy nothing — and deriving the list from
    the documents means a document that starts naming a symbol is covered without anyone
    remembering to widen a constant."""
    _write(git_project, "src/other.py", "y = 1\n")
    _symbol_project(
        kyo,
        git_project,
        _SYMBOL_DOC.replace("governs: src/app.py#alpha", "governs: src/app.py#alpha, src/other.py"),
    )

    assert docs_obligations.governed_symbol_paths(git_project) == ["src/app.py"]


# ------------------------------------------------------------------------ KYO-153.c4


def test_two_sections_governing_one_file_still_raise_two_obligations(
    kyo, git_project: Path
) -> None:
    """c4, decided rather than fallen into. KYO-144 measured that deduplicating per
    governed file takes this repository's 384 obligations to 287 — 25% of them — because
    the charter explains a mechanism and the README documents it. The dedup is refused
    anyway: collapsing two sections to one would have to pick a winner, and the loser's
    staleness becomes a silent false negative, which is the failure the cannot-tell rule
    exists to forbid. The cost belongs on the settle path, where one verdict discharging a
    set is cheap and visible. KYO-137.c3 chose the section as the unit; this keeps it."""
    _write(
        git_project,
        "docs/architecture.md",
        "# Why\n\n## Alpha\n\ngoverns: src/app.py\n\nWhy.\n",
    )
    _write(
        git_project,
        "README.md",
        "# How\n\n## Running alpha\n\ngoverns: src/app.py\n\nHow.\n",
    )
    _write(git_project, "src/app.py", _APP)
    _commit(git_project, "baseline")
    _branch_editing(git_project, "kyo-153", {"src/app.py": _APP.replace("+ 1", "+ 9")})

    raised = _obligations(git_project, "kyo-153", ["src/app.py"])
    assert sorted((o.doc_path, o.section) for o in raised) == [
        ("README.md", "Running alpha"),
        ("docs/architecture.md", "Alpha"),
    ]


# --------------------------------------------------------------------------- KYO-159
#
# KYO-153 fixed the raise path, re-measured, and found the fix changed the rate by
# zero: the lever is the SETTLE path. What follows is that lever, and the shape of it
# matters more than the saving. Deduplicating per governed file would buy the same 26%
# and buy it by dropping obligations that are genuinely distinct — settling the
# charter's section would silently discharge the README's, which is the false negative
# at a blocking gate that the cannot-tell rule exists to prevent. So nothing here drops
# anything. All 400-odd obligations still appear, still individually listed, still
# individually reviewable; what falls is the number of ACTS a controller performs, and
# only because they said, member by member, which ones were one editorial act.
#
# Two things were settled at the controller desk before this was built, and both are
# refusals rather than intentions:
#
#   1. The controller names the set; the tool forms none. No `--all-for-document`.
#      `kyo docs obligations` prints the line to copy — the KYO-71 pattern, where
#      `kyo report` prints the exact `kyo edit --touches-file` line and applies none.
#   2. One verdict, one method, one reason per set, and the set is verdict-homogeneous.
#      "I changed all six" and "I read all six and none needed changing" are each one
#      defensible statement; "some of each" is not.

_SET_DOC = """# The architecture

## Why the CLI is wired this way

governs: src/app.py

The reasoning.

## What the CLI does on start-up

governs: src/app.py

More reasoning about the same file.

## How releases are cut

governs: release.sh

Tag, build, publish.
"""

_SET_README = """# The handbook

## Running the CLI

governs: src/app.py

How to operate it.
"""


def _project_with_a_set_of_obligations(kyo, project: Path) -> tuple[str, str, list[dict]]:
    """A dispatch raising three obligations: two on one document, one on another.

    The shape this record is about, and the shape this repository's own history has —
    27 sections ever fire and they concentrate in two documents, so one changed file
    puts a question to several sections of the SAME document. Returns
    ``(record, dispatch_id, raised)`` with ``raised`` in the order the report raised it.
    """
    _write(project, "docs/architecture.md", _SET_DOC)
    _write(project, "README.md", _SET_README)
    _write(project, "src/app.py", "x = 1\n")
    _write(project, "release.sh", "echo release\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    _branch_editing(project, "kyo-159", {"src/app.py": "x = 2\n"})
    dispatch_id, result = _dispatch_and_report(kyo, project, record, branch="kyo-159")
    raised = result.json["docs_obligations"]["raised"]
    # Two sections of the architecture document govern src/app.py, and one section of
    # the README does. The `release.sh` section is silent because the branch did not
    # touch it — which is what keeps this fixture a c3 test as well as a KYO-159 one.
    assert len(raised) == 3, result
    return record, dispatch_id, raised


def _ids(raised: list[dict], doc_path: str) -> list[str]:
    return [row["id"] for row in raised if row["doc_path"] == doc_path]


# ------------------------------------------------------------------------ KYO-159.c1


def test_one_act_discharges_a_set_the_controller_named(kyo, git_project: Path) -> None:
    """c1. Three obligations, one command, one verdict — and the ledger still carries
    three rows, each with its own id, because nothing was collapsed."""
    record, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")
    assert len(charter) == 2

    settled = kyo(
        "docs",
        "settle",
        *charter,
        "--verdict",
        "updated",
        "--method",
        "rewrote both sections against the diff",
        "--why",
        "one reading of the architecture document answered both sections",
        "--json",
    )
    assert settled.code == 0, settled
    assert settled.json["settled"] == 2
    assert [row["id"] for row in settled.json["obligations"]] == charter
    # One verdict, one method, one reason, one moment — the four things that make a set
    # of rows readable later as one act.
    assert {row["verdict"] for row in settled.json["obligations"]} == {"updated"}
    assert len({row["method"] for row in settled.json["obligations"]}) == 1
    assert len({row["settled_why"] for row in settled.json["obligations"]}) == 1
    assert len({row["settled_at"] for row in settled.json["obligations"]}) == 1
    # And the README's obligation is untouched: it was not named.
    assert kyo("docs", "obligations", "--json").json["open"] == 1
    assert record in settled.out


def test_a_set_may_span_documents_because_the_controller_draws_the_boundary(
    kyo, git_project: Path
) -> None:
    """Decision 1's other half. The tool derives no membership, so it equally forbids
    itself from RESTRICTING membership to one document — a controller who read both
    documents in one sitting says so by naming all four ids, and nothing refuses that."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    every = [row["id"] for row in raised]

    settled = kyo(
        "docs", "settle", *every, "--verdict", "not-affected", "--method", "read them all",
        "--why", "the change is internal and no section describes the internals",
        "--json",
    )
    assert settled.code == 0, settled
    assert settled.json["settled"] == len(every)
    assert kyo("docs", "obligations", "--json").json["open"] == 0


# ------------------------------------------------------------------------ KYO-159.c2


def test_a_set_settle_discharges_exactly_the_ids_it_named(kyo, git_project: Path) -> None:
    """c2, and the criterion that can pass while being violated.

    A set-settle that quietly discharged a sibling because it names the same document
    would satisfy every other criterion here and still be the exact false negative at a
    blocking gate that the cannot-tell rule exists to prevent. So: two sections of one
    document, one of them named, and the other must still be open afterwards."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")
    named, sibling = charter[0], charter[1]

    settled = kyo(
        "docs", "settle", named, "--verdict", "updated", "--method", "rewrote it", "--json"
    )
    assert settled.code == 0, settled
    assert [row["id"] for row in settled.json["obligations"]] == [named]

    still_open = kyo("docs", "obligations", "--json").json
    open_ids = [row["id"] for row in still_open["obligations"]]
    assert sibling in open_ids, (
        "settling one section of a document silently discharged its sibling — the "
        "false negative a blocking gate must never produce"
    )
    assert named not in open_ids


def test_nothing_is_deduplicated_away_before_a_controller_can_see_it(
    kyo, git_project: Path
) -> None:
    """The dedup this record refused, asserted as an absence. Two documents describe
    one changed file and BOTH appear — the saving comes from settling them together,
    never from one of them not being raised."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    assert sorted({row["doc_path"] for row in raised}) == [
        "README.md",
        "docs/architecture.md",
    ]
    listing = kyo("docs", "obligations", "--json").json
    assert listing["open"] == 3
    assert len(listing["obligations"]) == 3


def test_a_set_with_one_settled_member_settles_nobody(kyo, git_project: Path) -> None:
    """All or nothing, and it is verdict-homogeneity's other half.

    Settling what it can and refusing the rest would leave a set whose members carry
    different verdicts recorded in different acts — the mixed state decision 2 refuses
    at the front door. The refusal names the standing verdict and writes nothing."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")
    assert (
        kyo("docs", "settle", charter[0], "--verdict", "updated", "--method", "a").code == 0
    )

    refused = kyo(
        "docs", "settle", *charter, "--verdict", "not-affected", "--method", "b",
        "--why", "read both",
    )
    assert refused.code == 2, refused
    assert CONTRACT_DOCS_OBLIGATION_ALREADY_SETTLED in refused.err
    assert charter[0] in refused.err
    assert "Nothing was settled" in refused.err

    # And the still-open member is still open, carrying no verdict from the refused act.
    rows = kyo("docs", "obligations", "--all", "--json").json["obligations"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[charter[0]]["verdict"] == "updated"
    assert by_id[charter[1]]["verdict"] is None


# ------------------------------------------------------------------------ KYO-159.c4


def test_a_set_carries_one_verdict_and_there_is_no_way_to_spell_two(
    kyo, git_project: Path
) -> None:
    """Decision 2, and the mechanism c4's "honest about covering every member" rests on.

    Verdict-homogeneity is held by construction: ONE `--verdict` for the whole set and
    no per-member spelling anywhere on the surface. This test fails if that is relaxed
    — if `--verdict` ever learns a comma list, a repeat, or an `id=verdict` pair, one of
    the assertions below stops holding."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")

    # A per-member verdict has no spelling: a comma list is not a verdict...
    refused = kyo(
        "docs", "settle", *charter, "--verdict", "updated,not-affected", "--method", "x",
        "--why", "y",
    )
    assert refused.code == 64, refused
    assert "not a docs verdict" in refused.err

    # ...and neither is an `id=verdict` pair, which lands as an unknown obligation id
    # rather than as a second verdict.
    pair = kyo(
        "docs", "settle", f"{charter[0]}=updated", f"{charter[1]}=not-affected",
        "--verdict", "updated", "--method", "x", "--why", "y",
    )
    assert pair.code != 0, pair
    assert "no docs obligation" in pair.err

    # Repeating the flag is argparse's last-wins, not a set of two: whatever it settles
    # on, every member carries the SAME verdict.
    settled = kyo(
        "docs", "settle", *charter, "--verdict", "updated", "--verdict", "not-affected",
        "--method", "x", "--why", "read both sections in one pass", "--json",
    )
    assert settled.code == 0, settled
    assert len({row["verdict"] for row in settled.json["obligations"]}) == 1


def test_the_tool_offers_no_way_to_settle_a_set_it_formed_itself(kyo) -> None:
    """Decision 1, asserted as an absence. A derived set is the tool drawing the
    boundary of a review it did not perform — which is exactly what c2 and c4 exist to
    prevent — so none of these flags may ever appear."""
    from kyosei.cli import build_parser

    docs = build_parser()._subparsers._group_actions[0].choices["docs"]  # type: ignore[attr-defined]
    spelled = {option for action in docs._actions for option in action.option_strings}
    for derived in ("--all-for-document", "--all-for-file", "--all-for-dispatch"):
        assert derived not in spelled, (
            f"{derived} forms a set the controller did not name. `kyo docs obligations` "
            "prints the ids to copy; the tool must not assert on anyone's behalf that "
            "they read the whole document"
        )


def test_settling_a_set_requires_a_reason_that_covers_every_member(
    kyo, git_project: Path
) -> None:
    """c4's second half. For one obligation the section and the verdict already say what
    was reviewed. For several, the reason IS the evidence that the review covered every
    member — and it is the one thing a rubber stamp cannot supply cheaply."""
    _, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")

    refused = kyo("docs", "settle", *charter, "--verdict", "updated", "--method", "x")
    assert refused.code == 64, refused
    assert "--why" in refused.err
    assert "one editorial act" in refused.err
    # Nothing was written by the refusal.
    assert kyo("docs", "obligations", "--json").json["open"] == 3

    # One obligation still settles without one: the single case is unchanged.
    assert (
        kyo("docs", "settle", charter[0], "--verdict", "updated", "--method", "x").code == 0
    )


def test_the_act_names_every_member_in_the_events_ledger(kyo, git_project: Path) -> None:
    """Where "these were one act" is recorded. The event names the set rather than
    summarising it, which is why no schema column was needed to answer, later, what that
    one act covered."""
    import sqlite3

    record, _, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")
    assert (
        kyo(
            "docs", "settle", *charter, "--verdict", "updated", "--method", "rewrote both",
            "--why", "one reading of the document answered both sections",
        ).code
        == 0
    )

    conn = sqlite3.connect(git_project / "kyosei" / "kyosei.db")
    try:
        commands = [
            row[0]
            for row in conn.execute("SELECT command FROM events WHERE record_id = ?", (record,))
        ]
    finally:
        conn.close()
    settle_events = [c for c in commands if c.startswith("docs settle")]
    assert len(settle_events) == 1, "one act wrote more than one event"
    for obligation_id in charter:
        assert obligation_id in settle_events[0], (
            "the event summarised the set instead of naming its members"
        )


# ------------------------------------------------------- KYO-159: the printed line


def test_the_listing_prints_the_settle_line_with_every_id_spelled_out(
    kyo, git_project: Path
) -> None:
    """Decision 1's remedy, and the KYO-71 pattern it copies: the tool has the ids, so
    the controller should not retype them — and the tool has neither the verdict nor the
    method nor the reason, so those stay placeholders. It prints and applies nothing."""
    _, dispatch_id, raised = _project_with_a_set_of_obligations(kyo, git_project)
    charter = _ids(raised, "docs/architecture.md")

    listing = kyo("docs", "obligations", "--json")
    assert listing.code == 0, listing
    groupings = {
        (group["dispatch_id"], group["doc_path"]): group
        for group in listing.json["groupings"]
    }
    # Grouped by (dispatch, document): two dispatches raising against one document are
    # two reviews of two diffs, and this must never merge them.
    assert set(groupings) == {
        (dispatch_id, "docs/architecture.md"),
        (dispatch_id, "README.md"),
    }
    charter_group = groupings[(dispatch_id, "docs/architecture.md")]
    assert charter_group["obligation_ids"] == charter
    for obligation_id in charter:
        assert obligation_id in charter_group["settle_command"]
    assert charter_group["settle_command"].startswith("kyo docs settle ")

    # The text rendering says the same thing (law 3: two renderings, one computation).
    text = kyo("docs", "obligations").out
    for obligation_id in charter:
        assert obligation_id in text
    assert "kyo docs settle " + " ".join(charter) in text
    # The single-member README grouping gets no line: one id is not worth copying.
    assert "README.md — 1 open" not in text

    # Nothing was applied by printing it.
    assert kyo("docs", "obligations", "--json").json["open"] == 3


def test_the_printed_line_settles_exactly_its_grouping_when_completed(
    kyo, git_project: Path
) -> None:
    """The line is copied and completed, not run as printed — the verdict, method and
    reason are the review, and only a person performed it. Completed, it settles the
    grouping's members and nothing else."""
    _, dispatch_id, raised = _project_with_a_set_of_obligations(kyo, git_project)
    group = next(
        g
        for g in kyo("docs", "obligations", "--json").json["groupings"]
        if g["doc_path"] == "docs/architecture.md"
    )
    assert len(group["obligation_ids"]) == 2

    settled = kyo(
        "docs", "settle", *group["obligation_ids"], "--verdict", "updated",
        "--method", "rewrote both sections", "--why", "one reading covered both", "--json",
    )
    assert settled.code == 0, settled
    after = kyo("docs", "obligations", "--json").json
    assert after["open"] == 1
    assert [row["doc_path"] for row in after["obligations"]] == ["README.md"]
    # The grouping that remains is the one nobody settled.
    assert [g["doc_path"] for g in after["groupings"]] == ["README.md"]
    assert dispatch_id == group["dispatch_id"]


def test_the_report_groups_a_documents_obligations_and_prints_its_settle_line(
    kyo, git_project: Path
) -> None:
    """KYO-199.c1 replaces what KYO-159 shipped here, and the replacement is the reason.

    KYO-159's `kyo report` printed a flat list and then a tail saying which documents
    held more than one, pointing at `kyo docs obligations` for the line to copy. That was
    deliberate — one place owned the line — and it cost the controller a second command
    and a second reading of the same grouping, on the surface where they were already
    looking. The line is printed here now, and there is still exactly one place that owns
    it: `docs_obligations.settle_command`, which every surface calls. So the tail went away
    rather than being kept beside the blocks that duplicate it.
    """
    _write(git_project, "docs/architecture.md", _SET_DOC)
    _write(git_project, "README.md", _SET_README)
    _write(git_project, "src/app.py", "x = 1\n")
    _write(git_project, "release.sh", "echo release\n")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, git_project, record, "src/app.py")
    _branch_editing(git_project, "kyo-159", {"src/app.py": "x = 2\n"})
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-159", "--json").json[
        "dispatch_id"
    ]
    sha = _git(git_project, "rev-parse", "kyo-159")
    path = git_project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(dispatch_id=dispatch_id, record_id=record, worked_from_sha=sha)
        ),
        encoding="utf-8",
    )
    # The text, not the JSON: this is what the controller reads.
    result = kyo("report", dispatch_id, "--file", str(path))
    assert result.code == 0, result

    assert "docs obligations: 3 raised across 2 document(s)" in result.out
    assert "hold more than one" not in result.out, (
        "the KYO-159 tail is a second rendering of the grouping now printed above it"
    )
    raised = kyo("show", record, "--json").json["docs_obligations"]
    together = sorted(
        row["id"] for row in raised if row["doc_path"] == "docs/architecture.md"
    )
    assert len(together) == 2
    # One block per document, and that document's block carries the line that settles
    # exactly its own members — every id spelled out, nothing else named.
    block = result.out.split("docs/architecture.md — 2 section(s)")[1].split("README.md")[0]
    settle = next(line for line in block.splitlines() if "kyo docs settle" in line)
    assert sorted(
        token for token in settle.split() if token.startswith(f"{record}.")
    ) == together
    assert "--why" in settle, "a set of two is one editorial act and needs its reason"


# ---------------------------------------------------------------------------- KYO-211
#
# Merge first, ingest second — the order this project's own standing rule asks for (run
# the suite on the merged tree before ingesting) and the order a schema-changing dispatch
# FORCES, because the controller's CLI on main cannot validate the report until the
# branch lands. `_docs_obligations_for_dispatch` read the branch's three-dot diff and
# nothing else, and a merged branch's three-dot diff is empty by construction: so the
# ingest printed "docs obligations: none — 0 changed path(s) intersected no governs:
# section" while `kyo docs preview` against the same base printed seven obligations. Two
# true statements about two different diffs, one of which examined nothing.


def _commit_anything(project: Path, message: str) -> str:
    """`_commit`, but tolerant of a tree with nothing staged."""
    _git(project, "add", "-A")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    return _git(project, "rev-parse", "HEAD")


def _governed_doc_and_a_branch_that_only_touches_code(kyo, project: Path):
    """:func:`_project_with_a_governed_doc`, with the store settled on main first.

    The difference matters only to the tests below: those merge the branch, and the
    shared fixture's branch commit sweeps up whatever the store had written by then
    (in-repo layout, ``git add -A``), so main and the branch would both carry edits to
    ``kyosei/export/`` and the merge would conflict on the store rather than on the
    subject under test. Committing the store before branching leaves the branch carrying
    exactly ``src/app.py``, which is also what makes ``changed_files`` assertable.
    """
    _write(project, "docs/architecture.md", GOVERNED_DOC)
    _write(project, "src/app.py", "x = 1\n")
    _write(project, "release.sh", "echo release\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    _commit_anything(project, "the store, before anything branches")
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _branch_editing(project, "kyo-137", {"src/app.py": "x = 2\n"})
    return record, base


def _merged_ingest(kyo, project: Path, record: str, **report_extra):
    """Dispatch on `kyo-137`, merge it into main, and only then ingest the report."""
    # KYO-487: `kyo-137` already carries the "worker's" commit here, and a dispatch
    # that creates its worktree on an existing branch records that branch's tip as
    # its base -- which would leave nothing between base and branch to diff. The
    # fixture stands in for a worker who commits after dispatch, so it records HEAD,
    # as every dispatch did before KYO-485 created worktrees.
    dispatched = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json"
    )
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(project, "rev-parse", "kyo-137")
    _commit_anything(project, "the store, before the merge")
    _git(project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    assert _git(project, "branch", "--contains", "kyo-137", "--list", "main").strip(), (
        "the fixture did not actually merge the branch"
    )
    return dispatch_id, _report(kyo, project, dispatch_id, record, sha, **report_extra)


def test_ingesting_after_the_merge_raises_what_the_preview_listed(
    kyo, git_project: Path
) -> None:
    """KYO-211.c1/c4. The reproduction from the record: preview against the base while
    the branch is live, merge, ingest, and compare. Delete the merged-branch arm of
    `dispatch_docs._docs_changed_paths` and the ingest goes back to reading an empty
    three-dot diff, which is what this asserts against.
    """
    record, base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    preview = kyo("docs", "preview", "--against", base, "--branch", "kyo-137", "--json")
    assert preview.code == 0, preview
    previewed = {(r["doc_path"], r["section"]) for r in preview.json["obligations"]}
    assert previewed == {("docs/architecture.md", "How the CLI is wired")}

    _, reported = _merged_ingest(kyo, git_project, record, touched_files=["src/app.py"])
    assert reported.code == 0, reported
    obligations = reported.json["docs_obligations"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_RAISED, obligations
    assert {
        (row["doc_path"], row["section"]) for row in obligations["raised"]
    } == previewed, (
        "a merged branch must raise exactly what the preview listed against its base"
    )


def test_the_ingest_says_which_source_named_the_changed_paths(
    kyo, git_project: Path
) -> None:
    """c1's other half: which evidence answered is stated, not inferred from a count."""
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    _, reported = _merged_ingest(kyo, git_project, record, touched_files=["src/app.py"])
    obligations = reported.json["docs_obligations"]
    # KYO-411: the git diff from the recorded base sha is read beside the report's list
    # now, and both named `src/app.py`, so the source says both.
    assert (
        obligations["changed_source"] == dispatch_docs.DOCS_CHANGED_SOURCE_BASE_SHA_AND_REPORT
    )
    assert "already merged into main" in obligations["changed_says"]
    assert "touched_files[]" in obligations["changed_says"]
    lines = dispatch_docs._docs_obligation_lines(obligations)
    assert any("changed paths:" in line for line in lines), lines


def test_a_merged_branch_whose_report_named_no_files_falls_back_to_the_base_sha(
    kyo, git_project: Path
) -> None:
    """c1's fallback. `touched_files[]` is optional; the two-dot diff from the recorded
    base sha names both of its own ends and so survives the merge as well."""
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    _, reported = _merged_ingest(kyo, git_project, record)
    obligations = reported.json["docs_obligations"]
    assert obligations["changed_source"] == dispatch_docs.DOCS_CHANGED_SOURCE_BASE_SHA
    assert obligations["changed_files"] == ["src/app.py"]
    assert {(row["doc_path"], row["section"]) for row in obligations["raised"]} == {
        ("docs/architecture.md", "How the CLI is wired")
    }


def test_a_live_branch_still_reads_its_own_diff_and_says_so(
    kyo, git_project: Path
) -> None:
    """The unmerged path is untouched — this record adds a fallback, it does not replace
    the evidence that already works."""
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    _, reported = _dispatch_and_report(kyo, git_project, record)
    obligations = reported.json["docs_obligations"]
    assert obligations["changed_source"] == dispatch_docs.DOCS_CHANGED_SOURCE_BRANCH_DIFF
    assert obligations["changed_files"] == ["src/app.py"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_RAISED


def test_an_ingest_that_cannot_name_a_changed_path_is_not_checked_never_none(
    kyo, git_project: Path
) -> None:
    """c2. A merged branch whose report named no files and whose two-dot diff from the
    recorded base sha is empty examined NOTHING — and "nothing was examined" must never
    render as "no document is implicated" (law 6, KYO-85's own argument)."""
    record, _ = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    # A branch that IS main. Contained by construction, and there is no diff at either
    # end of it — the shape a rebased or already-landed branch leaves behind.
    _git(git_project, "branch", "kyo-empty", "main")
    dispatched = kyo("dispatch", record, "--branch", "kyo-empty", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(git_project, "rev-parse", "kyo-empty")
    result = _report(kyo, git_project, dispatch_id, record, sha)
    assert result.code == 0, result

    obligations = result.json["docs_obligations"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NOT_CHECKED
    assert obligations["detail"], "a NOT CHECKED outcome that cannot say why is a shrug"
    assert "compares kyo-empty against itself" in obligations["detail"]
    assert "kyo docs preview" in obligations["detail"], (
        "the outcome names the command that answers the question it could not"
    )
    # The text rendering the controller actually reads says NOT CHECKED, never "none".
    lines = dispatch_docs._docs_obligation_lines(obligations)
    assert lines[0].startswith("docs obligations: NOT CHECKED — "), lines
    assert not any(line.startswith("docs obligations: none") for line in lines)


# ------------------------------------------------------------------------ KYO-219
#
# `kyo show` on a record with docs obligations was unreadable: one consumer's record
# printed 59 obligations, each repeating the same 400-character controller note, 87 KB
# in total. The listing is not wrong, it is just printed once per row when the text it
# repeats was written once — which is exactly what `kyo docs settle --why` over a
# governed-file set produces (KYO-159). These cover the collapse, the counts line, the
# flag that restores the full listing, and `--json` staying complete.


def _two_obligations(kyo, project: Path) -> tuple[str, list[str]]:
    """A record whose dispatch raised two obligations, and their ids."""
    _write(project, "docs/architecture.md", GOVERNED_DOC)
    _write(project, "src/app.py", "x = 1\n")
    _write(project, "release.sh", "echo release\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare_touches(kyo, project, record, "src/app.py", "release.sh")
    _branch_editing(project, "kyo-219", {"src/app.py": "x = 2\n", "release.sh": "echo v2\n"})
    _, result = _dispatch_and_report(kyo, project, record, branch="kyo-219")
    raised = result.json["docs_obligations"]["raised"]
    assert len(raised) == 2, raised
    return record, [row["id"] for row in raised]


#: A settle note of the length the consumer measured, so the assertions below are about
#: the size the record was filed about rather than about a toy string.
_LONG_WHY = (
    "Both sections were read against the branch in full and neither describes the "
    "behaviour the change moved: the wiring section names the subcommand table and the "
    "release section names the tag step, and this dispatch changed neither of those "
    "two things, only the module bodies underneath them, so the prose still reads true "
    "and there is nothing here to update in either place."
)


def test_show_collapses_obligations_that_share_one_settlement(
    kyo, git_project: Path
) -> None:
    """c1. One `kyo docs settle --why` over a set stamps one note onto every member;
    `kyo show` prints it once, with the count and the member ids, not once per row."""
    record, obligation_ids = _two_obligations(kyo, git_project)
    settled = kyo(
        "docs", "settle", *obligation_ids,
        "--verdict", "not-affected", "--method", "read both sections",
        "--why", _LONG_WHY,
    )
    assert settled.code == 0, settled

    out = kyo("show", record).out
    assert out.count(_LONG_WHY) == 1, (
        "the shared note is carried once for the whole group, not once per obligation"
    )
    assert "[not-affected]  2 obligation(s)" in out
    for obligation_id in obligation_ids:
        assert obligation_id in out, "every member is still named by id"
    # The bound c1 asks for: what the block costs is set by the distinct texts, not by
    # the number of rows. Two obligations sharing a settlement print the note once, so
    # a third sharing it would add its detail line and nothing else.
    assert out.count("read both sections") == 1


def test_show_prints_the_counts_by_state_line_ahead_of_the_list(
    kyo, git_project: Path
) -> None:
    """c1. "How many are still open" is the question the block is opened with, and at
    59 rows a reader who has to reach the end to answer it has already lost."""
    record, obligation_ids = _two_obligations(kyo, git_project)
    assert kyo(
        "docs", "settle", obligation_ids[0], "--verdict", "updated", "--method", "m"
    ).code == 0

    out = kyo("show", record).out
    assert "docs obligations (1 open of 2):" in out
    assert "by state: open 1, updated 1" in out
    lines = out.splitlines()
    counts_at = next(i for i, line in enumerate(lines) if "by state:" in line)
    first_entry = next(i for i, line in enumerate(lines) if obligation_ids[0] in line)
    assert counts_at < first_entry, "the counts line goes ahead of the list"


def test_expand_obligations_restores_the_full_per_obligation_listing(
    kyo, git_project: Path
) -> None:
    """c2. The listing is moved behind a word, never deleted: with the flag every
    obligation is printed in full again, note and all."""
    record, obligation_ids = _two_obligations(kyo, git_project)
    assert kyo(
        "docs", "settle", *obligation_ids,
        "--verdict", "not-affected", "--method", "read both sections",
        "--why", _LONG_WHY,
    ).code == 0

    expanded = kyo("show", record, "--expand-obligations").out
    assert expanded.count(_LONG_WHY) == len(obligation_ids), (
        "expanded, the note is printed once per obligation — today's rendering"
    )
    for obligation_id in obligation_ids:
        assert f"{obligation_id}  [not-affected]  docs/architecture.md" in expanded
    # And the flag is named where a reader of the collapsed block will see it.
    assert "--expand-obligations" in kyo("show", record).out


def test_show_json_carries_every_obligation_and_the_grouping_beside_it(
    kyo, git_project: Path
) -> None:
    """c2 and c3. `--json` is unchanged — every obligation, individually, under the key
    it has always been under — and the grouping rides beside it so the desktop viewer
    reads the terminal's own answer instead of deriving a second one."""
    record, obligation_ids = _two_obligations(kyo, git_project)
    assert kyo(
        "docs", "settle", *obligation_ids,
        "--verdict", "not-affected", "--method", "read both sections",
        "--why", _LONG_WHY,
    ).code == 0

    shown = kyo("show", record, "--json").json
    assert [row["id"] for row in shown["docs_obligations"]] == obligation_ids
    assert all(row["settled_why"] == _LONG_WHY for row in shown["docs_obligations"])

    groups = shown["docs_obligation_groups"]
    assert groups["total"] == 2 and groups["open"] == 0
    assert groups["counts_by_state"] == [{"state": "not-affected", "count": 2}]
    assert len(groups["groups"]) == 1
    group = groups["groups"][0]
    assert group["count"] == 2
    assert group["obligation_ids"] == obligation_ids
    assert group["settled_why"] == _LONG_WHY
    # Two sections, so two distinct details inside the one settlement: the inner level
    # is a real level, not a formality.
    assert [entry["count"] for entry in group["details"]] == [1, 1]


def test_collapse_obligations_is_a_rendering_of_the_same_rows(kyo) -> None:
    """c3. The function the renderer and the viewer share drops nothing, invents
    nothing, and puts every row in exactly one group and one detail bucket."""
    rows = [
        {
            "id": f"TST-1.o{n}",
            "doc_path": "README.md",
            "section": f"Section {n}",
            "cause": docs_obligations.OBLIGATION_GOVERNS,
            "dispatch_id": "TST-1.d1",
            "detail": "same detail" if n < 3 else f"detail {n}",
            "verdict": None if n == 1 else "not-affected",
            "method": None if n == 1 else "read it",
            "author": None if n == 1 else "controller",
            "settled_at": None if n == 1 else "2026-09-11T00:00:00Z",
            "settled_why": None if n == 1 else "one note",
        }
        for n in (1, 2, 3)
    ]
    collapsed = docs_obligations.collapse_obligations(rows)

    assert collapsed["total"] == 3
    assert collapsed["open"] == 1
    assert collapsed["counts_by_state"] == [
        {"state": "open", "count": 1},
        {"state": "not-affected", "count": 2},
    ]
    # Every row is a member of exactly one group, and of exactly one of its details.
    every_member = [
        row["id"] for group in collapsed["groups"] for row in group["members"]
    ]
    assert sorted(every_member) == sorted(row["id"] for row in rows)
    every_detail_member = [
        obligation_id
        for group in collapsed["groups"]
        for entry in group["details"]
        for obligation_id in entry["obligation_ids"]
    ]
    assert sorted(every_detail_member) == sorted(row["id"] for row in rows)
    # The open row settles into its own group: nothing shared with the settled pair.
    open_group = next(g for g in collapsed["groups"] if g["state"] == "open")
    assert open_group["obligation_ids"] == ["TST-1.o1"]
    assert open_group["settled_why"] is None
    # o2 and o3 share the settlement; only o2's detail is the one o1 also carries, and
    # o1 is in another group, so the detail buckets inside the pair stay separate.
    settled_group = next(g for g in collapsed["groups"] if g["state"] == "not-affected")
    assert [entry["detail"] for entry in settled_group["details"]] == [
        "same detail",
        "detail 3",
    ]


# ------------------------------------------- KYO-500: a code pointer raises on its section
#
# A branch that changes a
# code region holding a `<doc path>#<anchor>` pointer into a set with
# `section_status.pointer_obligations` on raises an obligation on that section, cause
# `pointer`, through the same raise / preview / settle / completion path `governs:` uses.

_POINTER_DOCUMENTS_YML = """\
documents:
  design:
    summary: The design record.
    globs:
      - docs/design/**/*.md
    section_status:
      statuses: [current, amended, superseded]
{extra}"""

_POINTER_DOC = """# A

## KYO-1

Why alpha returns what it returns.

## KYO-2

Why the limit is three.

## KYO-3

status: superseded
superseded-by: KYO-1

What alpha used to do.
"""

_POINTER_MODULE = '''"""A module whose code cites its design."""

import os

# The limit's reason: docs/design/a.md#kyo-2.
LIMIT = 3


def alpha():
    """docs/design/a.md#kyo-1 says why. Formerly docs/design/a.md#kyo-3."""
    return 1


def beta():
    return os.sep
'''


def _pointer_project(
    kyo, project: Path, *, extra_yml: str = "", files: dict[str, str] | None = None
) -> str:
    """A git project whose design set opts in, a module with pointers, and a record.

    Returns the base branch name; the store is committed before anything branches.
    """
    _write(project, "kyosei/documents.yml", _POINTER_DOCUMENTS_YML.format(extra=extra_yml))
    _write(project, "docs/design/a.md", _POINTER_DOC)
    _write(project, "src/pkg/mod.py", _POINTER_MODULE)
    for path, text in (files or {}).items():
        _write(project, path, text)
    _commit(project, "baseline")
    return _git(project, "rev-parse", "--abbrev-ref", "HEAD")


def _pointer_record(kyo, project: Path, *paths: str) -> str:
    record = new_record(kyo, criteria=["alpha is right"])
    _declare_touches(kyo, project, record, *(paths or ("src/pkg/mod.py",)))
    _commit_anything(project, "the store, before anything branches")
    return record


def _edit_alpha(text: str = _POINTER_MODULE) -> str:
    return text.replace("    return 1\n", "    return 2\n")


def _preview(kyo, base: str, branch: str, *flags: str):
    return kyo("docs", "preview", "--against", base, "--branch", branch, *flags)


def test_a_changed_region_holding_a_pointer_raises_on_its_section(
    kyo, git_project: Path
) -> None:
    """KYO-500.c1: `kyo report` raises one `pointer` obligation per section a pointer in a
    changed region names, and `says` names the file, the line and the region. LIMIT's
    pointer sits in a region this branch did not change, and KYO-3 is superseded (dead),
    so exactly one section is raised. Removing the join leaves this list empty."""
    _pointer_project(kyo, git_project)
    record = _pointer_record(kyo, git_project)
    _branch_editing(git_project, "kyo-500", {"src/pkg/mod.py": _edit_alpha()})
    _, result = _dispatch_and_report(kyo, git_project, record, branch="kyo-500")

    raised = result.json["docs_obligations"]["raised"]
    assert [(row["doc_path"], row["section"], row["cause"]) for row in raised] == [
        ("docs/design/a.md", "KYO-1", docs_obligations.OBLIGATION_POINTER)
    ]
    detail = raised[0]["detail"]
    assert "src/pkg/mod.py:10" in detail, detail
    assert "def alpha" in detail
    assert "docs/design/a.md#kyo-1" in detail


def test_the_preview_raises_exactly_what_the_report_raises(kyo, git_project: Path) -> None:
    """KYO-500.c1, the preview half: the same join, read by a worker with no store."""
    base = _pointer_project(kyo, git_project)
    _branch_editing(git_project, "kyo-500", {"src/pkg/mod.py": _edit_alpha()})
    preview = _preview(kyo, base, "kyo-500", "--json")
    assert preview.code == 0, preview
    rows = preview.json["obligations"]
    assert [(row["section"], row["cause"]) for row in rows] == [("KYO-1", "pointer")]
    assert "def alpha" in rows[0]["detail"]
    # The pointer whose region did not change is the suppression report's, not a raise.
    quieted = [row for row in preview.json["suppressed"] if row["cause"] == "pointer"]
    assert [row["section"] for row in quieted] == ["KYO-2"]
    text = _preview(kyo, base, "kyo-500", "--suppressed")
    assert "quieted by a pointer's region: 1 section(s)" in text.out
    assert "[KYO-2]" in text.out


def test_a_pointer_whose_region_is_unchanged_raises_nothing(kyo, git_project: Path) -> None:
    """KYO-500.c2, second half: the file changed, the pointers' regions did not."""
    base = _pointer_project(kyo, git_project)
    _branch_editing(
        git_project,
        "kyo-500",
        {"src/pkg/mod.py": _POINTER_MODULE.replace("return os.sep", "return os.linesep")},
    )
    preview = _preview(kyo, base, "kyo-500", "--json")
    assert preview.json["obligations"] == []
    assert {row["section"] for row in preview.json["suppressed"]} == {"KYO-1", "KYO-2"}


def test_a_pointer_no_finder_can_place_raises_at_file_granularity_and_says_so(
    kyo, git_project: Path
) -> None:
    """KYO-500.c2, first half: a pointer in a file the region finder cannot read (a shell
    script: no finder; a trailing comment: `()`) raises on any change to the file, and the
    suppression report names it as raised at file granularity."""
    script = "# docs/design/a.md#kyo-2\necho one\n"
    tail = 'X = 1\n\n# docs/design/a.md#kyo-1, after the last statement\n'
    base = _pointer_project(
        kyo, git_project, files={"src/pkg/run.sh": script, "src/pkg/tail.py": tail}
    )
    _branch_editing(
        git_project,
        "kyo-500",
        {
            "src/pkg/run.sh": script + "echo two\n",
            "src/pkg/tail.py": tail.replace("X = 1", "X = 2"),
        },
    )
    preview = _preview(kyo, base, "kyo-500", "--json")
    rows = {row["section"]: row for row in preview.json["obligations"]}
    assert set(rows) == {"KYO-1", "KYO-2"}
    assert "file granularity" in rows["KYO-2"]["detail"]
    assert "src/pkg/run.sh" in rows["KYO-2"]["detail"]
    assert "file granularity" in rows["KYO-1"]["detail"]
    assert {row["section"] for row in preview.json["raised_at_file_granularity"]} == {
        "KYO-1",
        "KYO-2",
    }
    text = _preview(kyo, base, "kyo-500")
    assert "raised at file granularity: 2 pointer section(s)" in text.out


def test_pointer_obligations_off_raises_nothing(kyo, git_project: Path) -> None:
    """KYO-499's flag is what this record makes act, so `false` must mean nothing."""
    base = _pointer_project(kyo, git_project, extra_yml="      pointer_obligations: false\n")
    _branch_editing(git_project, "kyo-500", {"src/pkg/mod.py": _edit_alpha()})
    preview = _preview(kyo, base, "kyo-500", "--json")
    assert preview.json["obligations"] == []


def test_a_pointer_obligation_round_trips_settles_and_blocks_completion(
    kyo, git_project: Path
) -> None:
    """KYO-500.c3: the cause survives export and `kyo rebuild`, blocks `complete` while
    open, and `kyo docs settle` clears it exactly as it clears a `governs:` one."""
    _pointer_project(kyo, git_project)
    record = _pointer_record(kyo, git_project)
    _branch_editing(git_project, "kyo-500", {"src/pkg/mod.py": _edit_alpha()})
    _, result = _dispatch_and_report(kyo, git_project, record, branch="kyo-500")
    obligation_id = result.json["docs_obligations"]["raised"][0]["id"]

    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    stored = kyo("docs", "obligations", "--json").json["obligations"]
    assert [(row["id"], row["cause"]) for row in stored] == [(obligation_id, "pointer")]
    assert "def alpha" in stored[0]["detail"]

    verify_criterion(kyo, f"{record}.c1")
    refused = kyo("move", record, "done")
    assert refused.code == 2, refused
    assert CONTRACT_COMPLETE_REQUIRES_DOCS_OBLIGATIONS_SETTLED in refused.err
    assert obligation_id in refused.err

    settled = kyo(
        "docs", "settle", obligation_id, "--verdict", "updated", "--method", "marked it"
    )
    assert settled.code == 0, settled
    assert kyo("move", record, "done").code == 0


def test_a_pointer_obligation_settles_from_the_workers_docs_verdicts(
    kyo, git_project: Path
) -> None:
    """KYO-500.c3: `docs_verdicts[]` matches a pointer obligation and `--from-report`
    applies it, as for `governs:` (KYO-199.c4)."""
    _pointer_project(kyo, git_project)
    record = _pointer_record(kyo, git_project)
    _branch_editing(git_project, "kyo-500", {"src/pkg/mod.py": _edit_alpha()})
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-500", "--json").json["dispatch_id"]
    verdict = {
        "doc_path": "docs/design/a.md",
        "section": "KYO-1",
        "verdict": "not-affected",
        "method": "read the section against the diff",
        "reason": "alpha's reason still holds",
    }
    result = _report(
        kyo, git_project, dispatch_id, record, _git(git_project, "rev-parse", "kyo-500"),
        docs_verdicts=[verdict],
    )
    assert result.code == 0, result
    applied = kyo("docs", "settle", "--from-report", dispatch_id, "--json")
    assert applied.code == 0, applied
    assert applied.json["settled"] == 1
    assert kyo("docs", "obligations", "--json").json["open"] == 0


def _replay(project: Path, base: str) -> dict[str, list[str]]:
    """The measurement's commit loop, through the built join: per first-parent commit
    after ``base``, the sections its pointer obligations name."""
    from kyosei import docs_parse

    documents_in = docs_parse.document_set(project / "kyosei")
    shas = _git(project, "rev-list", "--first-parent", "--reverse", f"{base}..HEAD").split()
    found: dict[str, list[str]] = {}
    for sha in shas:
        parent = f"{sha}^1"
        changed = _git(project, "diff", "--name-only", parent, sha).split()
        raised, _ = docs_obligations.obligations_and_suppressions_for_branch(
            project, parent, sha, changed, documents_in=documents_in
        )
        found[_git(project, "log", "-1", "--format=%s", sha)] = [
            row.section for row in raised if row.cause == docs_obligations.OBLIGATION_POINTER
        ]
    return found


def test_replaying_two_commits_raises_the_region_change_and_not_the_moved_one(
    kyo, git_project: Path
) -> None:
    """KYO-500.c4: the measurement's commit loop over a fixture of two commits.

    The first changes alpha's body: it raises KYO-1. The second only MOVES code that
    holds pointers — a new function inserted above alpha shifts it and LIMIT down, and one
    appended right after beta marks beta's last line as an insertion point, which is the
    edge the measurement counted as a change.
    Neither region's text changed, so it raises nothing."""
    _pointer_project(kyo, git_project)
    base = _git(git_project, "rev-parse", "HEAD")
    _write(git_project, "src/pkg/mod.py", _edit_alpha())
    _commit(git_project, "change alpha")
    moved = (
        _edit_alpha().replace(
            "# The limit's reason", "def first():\n    return 0\n\n\n# The limit's reason"
        )
        + "\n\ndef gamma():\n    return None\n"
    )
    _write(git_project, "src/pkg/mod.py", moved)
    _commit(git_project, "move things")
    assert _replay(git_project, base) == {"change alpha": ["KYO-1"], "move things": []}


def test_pointer_region_is_the_innermost_def_or_the_statement_a_comment_precedes() -> None:
    """KYO-500.c1's region rule, and c2's two cannot-tell answers."""
    from kyosei.docs_regions import pointer_region

    assert pointer_region("m.py", _POINTER_MODULE, 10) == (9, 11, "def alpha")
    # A comment above a module-level statement belongs to that statement.
    assert pointer_region("m.py", _POINTER_MODULE, 5) == (4, 6, "module-level statement at line 6")
    nested = "class C:\n    def m(self):\n        # here\n        return 1\n"
    assert pointer_region("m.py", nested, 3) == (2, 4, "def m")
    assert pointer_region("m.py", "x = 1\n# trailing\n", 2) == ()
    assert pointer_region("m.py", "def (:\n", 1) is None
    assert pointer_region("m.sh", "# x\necho\n", 1) is None


def test_a_deleted_file_is_read_as_a_change_with_no_after_side(kyo, git_project: Path) -> None:
    """KYO-500: a deleted source file's pointers sat in regions the branch removed, so the
    diff reader keeps it (before side only) and the pointer raises."""
    base = _pointer_project(kyo, git_project)
    _git(git_project, "checkout", "-q", "-b", "kyo-500")
    (git_project / "src/pkg/mod.py").unlink()
    _commit(git_project, "delete the module")
    _git(git_project, "checkout", "-q", base)
    changes = docs_regions.branch_file_changes(git_project, base, "kyo-500")
    assert changes["src/pkg/mod.py"].after_text is None
    assert changes["src/pkg/mod.py"].before_text == _POINTER_MODULE
    preview = _preview(kyo, base, "kyo-500", "--json")
    assert {row["section"] for row in preview.json["obligations"]} == {"KYO-1", "KYO-2"}
