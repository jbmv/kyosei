"""KYO-401: every changed-path listing sees both sides of a rename, and every path verbatim.

``git diff --name-only`` with git's default rename
detection lists only a rename's destination, so ``git mv ideas/x.md docs/x.md`` passed
`kyo check-branch` with rc=0 and the merge deleted the idea file; and with the default
``core.quotePath`` a non-ASCII path under ``ideas/`` printed C-quoted
(``"ideas/caf\\303\\251.md"``), so ``startswith('ideas/')`` failed on the leading quote.

The fix is one set of flags (``git_queries.CHANGED_PATH_FLAGS``: ``--no-renames -z``)
shared by all four listings. The tests below are the ones that fail if either flag is
removed (c6): the rename tests go green-to-red without ``--no-renames``, the non-ASCII,
space and newline tests without ``-z``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei import dispatch_docs, docs_obligations, git_queries, touch_drift

from .conftest import new_record, valid_report

CAFE = "ideas/café.md"


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


def _head_branch(project: Path) -> str:
    return _git(project, "rev-parse", "--abbrev-ref", "HEAD")


# A body long enough that git's rename detection pairs the two sides confidently.
BODY = "".join(f"line {n} of a file that is about to move\n" for n in range(20))


def _branch_renaming(project: Path, branch: str, source: str, destination: str) -> str:
    """Commit ``source`` on the current branch (if absent), then ``git mv`` it on
    ``branch`` and come back. Returns the rename commit's sha."""
    base = _head_branch(project)
    if not (project / source).exists():
        _write(project, source, BODY)
        _commit(project, f"add {source}")
    _git(project, "checkout", "-q", "-b", branch)
    (project / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(project, "mv", source, destination)
    sha = _commit(project, f"move {source} to {destination}")
    _git(project, "checkout", "-q", base)
    return sha


def _branch_writing(project: Path, branch: str, files: dict[str, str]) -> str:
    base = _head_branch(project)
    _git(project, "checkout", "-q", "-b", branch)
    for path, text in files.items():
        _write(project, path, text)
    sha = _commit(project, f"work on {branch}")
    _git(project, "checkout", "-q", base)
    return sha


def _git_default_listing_shows_only_the_destination(project: Path, against: str, branch: str):
    """The premise, measured rather than assumed: plain ``git diff --name-only`` in this
    very repository hides the source. If git ever stops doing that, these tests would
    pass for the wrong reason, so they say so here instead."""
    plain = _git(project, "diff", "--name-only", f"{against}...{branch}").splitlines()
    return plain


# ------------------------------------------------------------------ c1: renames out


def test_check_branch_refuses_a_rename_out_of_ideas(kyo, git_project: Path) -> None:
    against = _head_branch(git_project)
    _branch_renaming(git_project, "moves-idea", "ideas/x.md", "docs/x.md")
    assert _git_default_listing_shows_only_the_destination(
        git_project, against, "moves-idea"
    ) == ["docs/x.md"]

    result = kyo("check-branch", "moves-idea", "--against", against, "--json")
    assert result.code == 2, result
    assert result.json["ok"] is False
    assert result.json["guarded_files"] == ["ideas/x.md"]
    assert sorted(result.json["changed_files"]) == ["docs/x.md", "ideas/x.md"]

    human = kyo("check-branch", "moves-idea", "--against", against)
    assert human.code == 2
    assert "ideas/x.md" in human.err


def test_check_branch_refuses_a_rename_out_of_kyosei(kyo, git_project: Path) -> None:
    against = _head_branch(git_project)
    _branch_renaming(git_project, "moves-store", "kyosei/kinds.yml", "docs/kinds.yml")

    result = kyo("check-branch", "moves-store", "--against", against, "--json")
    assert result.code == 2, result
    assert "kyosei/kinds.yml" in result.json["guarded_files"]


# ------------------------------------------------------------ c2: non-ASCII, unquoted


def test_check_branch_refuses_a_non_ascii_idea_and_names_it_unquoted(
    kyo, git_project: Path
) -> None:
    against = _head_branch(git_project)
    _branch_writing(git_project, "adds-cafe", {CAFE: "a thought\n"})
    # The premise: git's default listing quotes it.
    quoted = _git(git_project, "diff", "--name-only", f"{against}...adds-cafe")
    assert quoted.startswith('"'), quoted

    result = kyo("check-branch", "adds-cafe", "--against", against, "--json")
    assert result.code == 2, result
    assert result.json["guarded_files"] == [CAFE]

    human = kyo("check-branch", "adds-cafe", "--against", against)
    assert human.code == 2
    assert CAFE in human.err
    assert "\\303" not in human.err


def test_check_branch_refuses_a_modified_non_ascii_idea(kyo, git_project: Path) -> None:
    _write(git_project, CAFE, "first\n")
    _commit(git_project, "baseline idea")
    against = _head_branch(git_project)
    _branch_writing(git_project, "edits-cafe", {CAFE: "second\n"})

    result = kyo("check-branch", "edits-cafe", "--against", against, "--json")
    assert result.code == 2, result
    assert result.json["guarded_files"] == [CAFE]


# ----------------------------------------------------- c3: all four listings, directly


def test_branch_changed_files_lists_both_sides_and_verbatim_paths(git_project: Path) -> None:
    against = _head_branch(git_project)
    _write(git_project, "src/old name.py", BODY)
    _commit(git_project, "baseline")
    base = _head_branch(git_project)
    _git(git_project, "checkout", "-q", "-b", "odd-paths")
    _git(git_project, "mv", "src/old name.py", "src/new name.py")
    _write(git_project, "docs/café.md", "x\n")
    _write(git_project, "docs/two\nlines.md", "x\n")
    _commit(git_project, "odd paths")
    _git(git_project, "checkout", "-q", base)

    changed = git_queries.branch_changed_files(git_project, against, "odd-paths")
    assert sorted(changed) == sorted(
        ["src/old name.py", "src/new name.py", "docs/café.md", "docs/two\nlines.md"]
    )


def test_commit_changed_files_lists_both_sides_and_verbatim_paths(git_project: Path) -> None:
    _write(git_project, "ideas/old.md", BODY)
    _commit(git_project, "baseline")
    (git_project / "docs").mkdir(exist_ok=True)
    _git(git_project, "mv", "ideas/old.md", "docs/new.md")
    _write(git_project, "docs/café two\nlines.md", "x\n")
    sha = _commit(git_project, "move and add")

    changed = git_queries.commit_changed_files(git_project, sha)
    assert changed is not None
    assert sorted(changed) == sorted(["ideas/old.md", "docs/new.md", "docs/café two\nlines.md"])


def test_git_diff_paths_lists_both_sides_and_verbatim_paths(git_project: Path) -> None:
    _write(git_project, "src/a.py", BODY)
    before = _commit(git_project, "baseline")
    _git(git_project, "mv", "src/a.py", "src/b.py")
    _write(git_project, "src/ü \n.py", "x\n")
    after = _commit(git_project, "move")

    assert git_queries._git_diff_paths(git_project, before, after) == {
        "src/a.py",
        "src/b.py",
        "src/ü \n.py",
    }


def test_git_diff_added_paths_counts_a_rename_destination_as_added(git_project: Path) -> None:
    _write(git_project, "src/a.py", BODY)
    before = _commit(git_project, "baseline")
    _git(git_project, "mv", "src/a.py", "src/b.py")
    _write(git_project, "src/né.py", "x\n")
    after = _commit(git_project, "move")

    # The source is deleted, not added; the destination and the new file are both adds.
    assert git_queries._git_diff_added_paths(git_project, before, after) == {
        "src/b.py",
        "src/né.py",
    }


def test_one_set_of_flags_serves_every_listing() -> None:
    """One helper rather than four copies: this pins the flags
    the helper passes, so a fifth listing that reached for bare ``--name-only`` would
    have to bypass the helper to regress."""
    assert "--no-renames" in git_queries.CHANGED_PATH_FLAGS
    assert "-z" in git_queries.CHANGED_PATH_FLAGS
    assert "--name-only" in git_queries.CHANGED_PATH_FLAGS


# --------------------------------------------------- c4: a renamed governed file


GOVERNED_DOC = """# The architecture

## How the CLI is wired

governs: src/app.py

The CLI hands each subcommand a context and an argument namespace.
"""


def _report(kyo, project: Path, dispatch_id: str, record: str, sha: str):
    path = project / f"report-{dispatch_id}.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id, record_id=record, worked_from_sha=sha
            )
        ),
        encoding="utf-8",
    )
    return kyo("report", dispatch_id, "--file", str(path), "--json")


def test_obligations_for_changed_files_raises_for_a_renamed_governed_file(
    git_project: Path,
) -> None:
    _write(git_project, "docs/architecture.md", GOVERNED_DOC)
    _write(git_project, "src/app.py", BODY)
    _commit(git_project, "baseline")
    against = _head_branch(git_project)
    _branch_renaming(git_project, "moves-app", "src/app.py", "src/cli.py")
    _branch_writing(git_project, "edits-app", {"src/app.py": BODY + "more\n"})

    def sections(branch: str) -> list[tuple[str, str]]:
        changed = git_queries.branch_changed_files(git_project, against, branch)
        return [
            (o.doc_path, o.section)
            for o in docs_obligations.obligations_for_changed_files(git_project, changed)
        ]

    in_place = sections("edits-app")
    assert in_place == [("docs/architecture.md", "How the CLI is wired")]
    assert sections("moves-app") == in_place


def test_report_raises_the_obligation_for_a_renamed_governed_file(
    kyo, git_project: Path
) -> None:
    _write(git_project, "docs/architecture.md", GOVERNED_DOC)
    _write(git_project, "src/app.py", BODY)
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["the CLI moved"])
    scope = git_project.parent / "kyo-401-touches.txt"  # outside the repo: never committed
    scope.write_text("src/app.py\n+src/cli.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    _branch_renaming(git_project, "kyo-401-doc", "src/app.py", "src/cli.py")

    dispatched = kyo("dispatch", record, "--branch", "kyo-401-doc", "--json")
    assert dispatched.code == 0, dispatched
    sha = _git(git_project, "rev-parse", "kyo-401-doc")
    result = _report(kyo, git_project, dispatched.json["dispatch_id"], record, sha)
    assert result.code == 0, result
    obligations = result.json["docs_obligations"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_RAISED
    assert [(r["doc_path"], r["section"]) for r in obligations["raised"]] == [
        ("docs/architecture.md", "How the CLI is wired")
    ]


# ----------------------------------------------------- c5: touch drift sees the source


def test_touch_drift_names_the_source_of_a_rename_outside_the_claim(
    kyo, git_project: Path
) -> None:
    _write(git_project, "src/outside.py", BODY)
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    scope = git_project.parent / "kyo-401-touches.txt"  # outside the repo: never committed
    scope.write_text("+src/moved.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    against = _head_branch(git_project)
    _branch_renaming(git_project, "kyo-401-drift", "src/outside.py", "src/moved.py")

    dispatch_id = kyo("dispatch", record, "--branch", "kyo-401-drift", "--json").json[
        "dispatch_id"
    ]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json[
        "touch_drift"
    ]
    assert drift["outcome"] == touch_drift.DRIFT_FOUND
    assert "src/outside.py" in drift["changed_files"]
    assert drift["undeclared_files"] == ["src/outside.py"]
