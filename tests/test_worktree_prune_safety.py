"""KYO-402: `kyo worktrees --prune` removes only what git registered, and asks the
worktree's own HEAD whether its code has landed.

A review reproduced `--prune` deleting a full separate clone holding an unpushed
branch, `lib/` inside
the main checkout, and — with `--branch ..` — the split store with its `.git`. The
only exclusion was "not the code root"; everything else a finished, clean dispatch
had RECORDED was `shutil.rmtree`'d, then a bare `git worktree prune` ran over the
whole repository. Separately, merged-ness was read from the declared branch, so a
worker that committed on a detached HEAD read as "merged into main" and its commits
became unreachable when the directory went.

Every directory here lives under ``tmp_path``; nothing touches this repository.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei.errors import EXIT_USAGE

from .conftest import invoke, new_record, valid_report


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _in_code(code: Path):
    def run(*args: str, stdin: str | None = None):
        return invoke(["--root", str(code), *args], stdin=stdin)

    return run


def _export_path(store_dir: Path, record: str) -> Path:
    return next(store_dir.rglob(f"export/{record}.json"))


def _dispatch_closed_in(kyo, record: str, tree: Path, *extra: str) -> str:
    dispatched = kyo("dispatch", record, "--worktree", str(tree), *extra, "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    closed = kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "done"
    )
    assert closed.code == 0, closed
    return dispatch_id


def _rewrite_recorded_worktree(kyo, store_dir: Path, record: str, value: str) -> None:
    """Point a finished dispatch's STORED worktree somewhere else, as a store written
    before KYO-402 (or a legacy relative value re-resolved after a move) can: through
    the git-canonical export and a rebuild, never through `kyo dispatch`, which now
    refuses these paths at the door (c7)."""
    path = _export_path(store_dir, record)
    document = json.loads(path.read_text(encoding="utf-8"))
    (dispatch,) = document["dispatches"]
    dispatch["worktree"] = value
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt


def _registered_worktree(code: Path, tree: Path, *extra: str) -> None:
    _git(code, "worktree", "add", *extra, str(tree))


def _row(listing, dispatch_id: str) -> dict:
    return next(r for r in listing.json["worktrees"] if r["dispatch_id"] == dispatch_id)


# ------------------------------------------------------------------- c1, c2, c3


def test_prune_never_removes_a_recorded_directory_git_did_not_register(
    split_project, tmp_path
) -> None:
    """c1 + c2. A separate clone, a subdirectory of the checkout and the split store
    root — each recorded by a finished, clean dispatch, each a kind of directory
    `--prune` was shown deleting. c2: with the registered-worktree check removed, the
    subdirectory row reads prunable (its HEAD is the trunk's own tip), which this test
    refuses on the state assertion before git's own refusal is ever reached."""
    code, store = split_project
    kyo = _in_code(code)
    clone = tmp_path / "separate-clone"
    _git(tmp_path, "clone", "-q", str(code), str(clone))
    _git(clone, "checkout", "-q", "-b", "unpushed")
    (clone / "only-here.txt").write_text("the only copy\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "-m", "mine")
    subdirectory = code / "src"
    # The subdirectory first: it is the case c2 names, and the one whose row reads
    # `prunable` the moment the registration check is gone (its HEAD is the trunk's).
    targets = {"subdirectory": subdirectory, "clone": clone, "store": store}

    ids: dict[str, str] = {}
    for name, target in targets.items():
        record = new_record(kyo, title=f"recorded at {name}", criteria=["a"])
        placeholder = tmp_path / f"wt-{name}"
        _registered_worktree(code, placeholder, "--detach")
        ids[name] = _dispatch_closed_in(kyo, record, placeholder)
        _git(code, "worktree", "remove", str(placeholder))
        _rewrite_recorded_worktree(kyo, store, record, str(target))

    pruned = kyo("worktrees", "--prune", "--include-unmerged", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    for name, target in targets.items():
        row = _row(pruned, ids[name])
        assert row["state"] != "prunable", (name, row)
        assert target.is_dir(), f"{name} ({target}) was removed"
        assert row["state"] == "not-a-registered-worktree", (name, row)
        assert "not a registered worktree of this repository" in row["detail"]
    assert (clone / "only-here.txt").is_file()
    assert (store / ".git").exists()
    assert (subdirectory / "widget.py").is_file()
    assert _git(code, "status", "--porcelain") == ""


def test_a_legacy_relative_value_resolving_onto_an_unrelated_directory_survives(
    git_project: Path, kyo, tmp_path
) -> None:
    """c3. The store holds values like ``../kyosei-wt-68``; after the checkout moves
    (a store moved to another machine) they re-resolve against wherever it lives now, onto
    directories this tool never created. Such a directory is never removed."""
    record = new_record(kyo, criteria=["a"])
    placeholder = tmp_path / "wt-placeholder"
    _registered_worktree(git_project, placeholder, "--detach")
    dispatch_id = _dispatch_closed_in(kyo, record, placeholder)
    _git(git_project, "worktree", "remove", str(placeholder))

    unrelated = git_project.parent / "kyosei-wt-68"
    unrelated.mkdir()
    (unrelated / "somebody-elses.txt").write_text("not yours\n", encoding="utf-8")
    _rewrite_recorded_worktree(kyo, git_project, record, "../kyosei-wt-68")

    listing = kyo("worktrees", "--json")
    row = _row(listing, dispatch_id)
    assert row["worktree"] == "../kyosei-wt-68"
    assert Path(row["worktree_resolved"]) == unrelated.resolve()
    assert row["state"] == "not-a-registered-worktree"

    pruned = kyo("worktrees", "--prune", "--include-unmerged", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert (unrelated / "somebody-elses.txt").is_file()


# ------------------------------------------------------------------------ c4


def test_removal_is_per_path_and_leaves_unrelated_stale_entries_to_git(
    git_project: Path, kyo, tmp_path
) -> None:
    """c4. `git worktree remove <path>` for the one worktree this run removed — its
    admin entry goes with it — and no repository-wide `git worktree prune`: a stale
    entry for a directory somebody else deleted by hand is still in git's list."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-real"
    _registered_worktree(git_project, tree, "--detach")
    _dispatch_closed_in(kyo, record, tree)

    stranger = tmp_path / "wt-somebody-elses"
    _registered_worktree(git_project, stranger, "--detach")
    for child in stranger.iterdir():
        if child.is_dir():
            subprocess.run(["rm", "-rf", str(child)], check=True)
        else:
            child.unlink()
    stranger.rmdir()
    before = _git(git_project, "worktree", "list", "--porcelain")
    assert f"worktree {stranger.resolve()}" in before or f"worktree {stranger}" in before

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == [str(tree)]
    assert not tree.exists()

    after = _git(git_project, "worktree", "list", "--porcelain")
    assert str(tree.resolve()) not in after and str(tree) not in after
    assert str(stranger.resolve()) in after or str(stranger) in after, (
        "an unrelated stale admin entry was cleared by a repository-wide prune:\n" + after
    )


# ------------------------------------------------------------------------ c5


def test_commits_on_a_detached_head_keep_the_worktree_even_though_the_branch_is_merged(
    git_project: Path, kyo, tmp_path
) -> None:
    """c5. The worker committed on a detached HEAD, so the declared
    branch never moved past worked_from_sha — which IS contained in main. Read from the
    branch, this row was "merged" and prunable; read from the worktree's HEAD, it holds
    the only copy of a commit."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-detached"
    _registered_worktree(git_project, tree, "-b", "kyo-declared")
    dispatch_id = _dispatch_closed_in(kyo, record, tree, "--branch", "kyo-declared")
    _git(tree, "checkout", "-q", "--detach")
    (tree / "worker.txt").write_text("the work\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "committed on a detached HEAD")
    sha = _git(tree, "rev-parse", "HEAD")
    assert _git(git_project, "rev-parse", "kyo-declared") == _git(git_project, "rev-parse", "main")

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert tree.is_dir()
    row = _row(pruned, dispatch_id)
    assert row["state"] == "branch-not-merged", row
    assert row["branch_merged"] is False
    assert "never advanced past worked_from_sha" in row["detail"]
    assert "while the worktree HEAD did" in row["detail"]
    subprocess.run(["git", "cat-file", "-e", sha], cwd=git_project, check=True)


# ------------------------------------------------------------------------ c6


def test_dispatch_refuses_a_branch_name_git_would_not_create(
    git_project: Path, kyo
) -> None:
    """c6. `--branch ..` derived the worktree home's parent — the store root — as the
    worktree. Refused before any store write, with a valid name still dispatching."""
    record = new_record(kyo, criteria=["a"])
    before = _export_path(git_project, record).read_text(encoding="utf-8")
    for name in ("..", ".", "a..b", "has space", "-leading-dash"):
        refused = kyo("dispatch", record, f"--branch={name}")
        assert refused.code == EXIT_USAGE, (name, refused)
        assert "not a valid branch name" in refused.err
        assert kyo("show", record, "--json").json["dispatches"] == []
        assert _export_path(git_project, record).read_text(encoding="utf-8") == before

    accepted = kyo("dispatch", record, "--branch", "kyo-402/ok", "--json")
    assert accepted.code == 0, accepted
    assert accepted.json["worktree"].endswith("kyo-402-ok")


# ------------------------------------------------------------------------ c7


def test_dispatch_refuses_a_worktree_that_is_not_one_of_this_repository(
    git_project: Path, kyo, tmp_path
) -> None:
    """c7. A subdirectory of the checkout and a separate clone are refused, naming
    `git worktree add`; doing exactly that and re-running the dispatch succeeds."""
    record = new_record(kyo, criteria=["a"])
    subdirectory = git_project / "kyosei"
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(git_project), str(clone))
    plain = tmp_path / "plain"
    plain.mkdir()
    for directory in (subdirectory, clone, plain):
        refused = kyo("dispatch", record, "--worktree", str(directory))
        assert refused.code == EXIT_USAGE, (directory, refused)
        assert "git worktree add" in refused.err
        assert kyo("show", record, "--json").json["dispatches"] == []

    tree = tmp_path / "wt-proper"
    _registered_worktree(git_project, tree, "--detach")
    accepted = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert accepted.code == 0, accepted


# ------------------------------------------------------------- KYO-475


def _finish(
    kyo, project: Path, dispatch_id: str, record: str, worked_from: str, how: str
) -> None:
    """End dispatch A in one of the four ways a dispatch row counts as finished."""
    if how in ("withdrawn", "terminated-no-report"):
        closed = kyo("dispatch", "close", dispatch_id, f"--{how}", "--why", "done")
        assert closed.code == 0, closed
        return
    report = project.parent / f"{dispatch_id}-report.json"
    report.write_text(
        json.dumps(
            valid_report(dispatch_id=dispatch_id, record_id=record, worked_from_sha=worked_from)
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatch_id, "--file", str(report), "--json")
    assert ingested.code == 0, ingested
    if how == "closed":
        closed = kyo("dispatch", "close", dispatch_id)
        assert closed.code == 0, closed


@pytest.mark.parametrize("how", ["withdrawn", "reported", "closed", "terminated-no-report"])
def test_prune_never_removes_a_path_a_running_dispatch_also_records(
    git_project: Path, kyo, tmp_path, how: str
) -> None:
    """KYO-475.c1-c3 (a consumer report). Dispatch A finished, then B re-frozen into
    the same registered, clean, merged worktree (KYO-345's `.d2` path) and still
    running. A's row was judged on A's state alone and read `prunable`, and the prune
    loop removes by row — so B's worker lost its directory mid-run."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-shared"
    _registered_worktree(git_project, tree, "--detach")
    first = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert first.code == 0, first
    first_id = first.json["dispatch_id"]
    _finish(kyo, git_project, first_id, record, first.json["worked_from_sha"], how)
    second = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert second.code == 0, second
    second_id = second.json["dispatch_id"]

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    row = _row(listing, first_id)
    assert row["state"] == "held-by-running-dispatch", row
    assert second_id in row["detail"], row
    assert _row(listing, second_id)["state"] == "dispatch-still-running"
    text = kyo("worktrees")
    assert f"[held-by-running-dispatch]  {first_id}" in text.out, text.out
    assert f"held by running dispatch {second_id}" in text.out, text.out

    pruned = kyo("worktrees", "--prune", "--include-unmerged", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert tree.is_dir()


def test_prune_removes_a_shared_path_once_every_dispatch_recording_it_is_finished(
    git_project: Path, kyo, tmp_path
) -> None:
    """KYO-475.c4: the guard is about RUNNING holders only. Two finished dispatches on
    one path leave it removable exactly as one would."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-shared-finished"
    _registered_worktree(git_project, tree, "--detach")
    _dispatch_closed_in(kyo, record, tree)
    first = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert first.code == 0, first
    closed = kyo(
        "dispatch", "close", first.json["dispatch_id"], "--withdrawn", "--why", "done"
    )
    assert closed.code == 0, closed

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == [str(tree)]
    assert not tree.exists()
