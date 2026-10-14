"""KYO-70: dispatch worktrees have a home, and something removes them.

KYO-89 later put a **space** in that home's name, and the second half of this file is
about why. A dispatched worker verifies its work in a worktree; the repository the
work is integrated into is wherever the maintainer keeps it. Every environmental
property on which those two differ, and on which the worktree's value is the more
permissive, is a class of bug the worker cannot observe — its suite passes, `main`'s
fails, and the worker was not careless. KYO-83.d1 is the instance: a message quoting
a path with ``shlex.quote``, a test parsing it back with ``\\S+``, green under
``~/kyosei/<project>/worktrees/`` and red on a `main` checked out under a path with
spaces in it.

Raised on finding `kyosei-wt-60` and `kyosei-wt-69` sitting beside unrelated
projects, where they were visible and possibly alarming to an unsuspecting user.

`kyo dispatch --worktree DIR` had no opinion about DIR, so the controller invented
one and picked a sibling of the project. The parent of a project is not the tool's
to use — in that case it held unrelated work, and nothing in `kyo init`, the README
or the skill warned that dispatching would put anything there. And nothing removed
them: the controller deleted each wave's by hand, and a dispatch that died and was
never resumed left a directory AND a branch behind with no command that noticed.

The same question was already settled for the store (`~/kyosei/<project>/`).
A worktree is the same class of artifact, so it gets the same answer, one level
down: `~/kyosei/<project>/dispatch worktrees/<branch>/` -- the space being KYO-89's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kyosei.config import WORKTREE_HOME_TOKEN, WORKTREES_DIRNAME

from .conftest import git_worktree, invoke, new_record, valid_report


def _worktrees_home(store_home: Path, project: Path) -> Path:
    return store_home / project.name / WORKTREES_DIRNAME


def _declare_disjoint_touches(kyo, project: Path, first: str, second: str) -> None:
    """Give two records real, non-overlapping touch lists.

    KYO-48 made an undeclared scope its own refusal, distinct from a collision, so a
    test about *where* work happens has to say what the work touches — and the paths
    have to exist, because `kyo edit --touches-file` validates them against the repo
    at the boundary (charter, Layer 1).
    """
    (project / "src").mkdir(exist_ok=True)
    for record, name in ((first, "one.py"), (second, "two.py")):
        (project / "src" / name).write_text("x = 1\n", encoding="utf-8")
        scope = project / f"{record}-touches.txt"
        scope.write_text(f"src/{name}\n", encoding="utf-8")
        assert kyo("edit", record, "--touches-file", str(scope)).code == 0


# --------------------------------------------------------------------- KYO-70.c1


def test_a_declared_branch_derives_a_worktree_under_the_kyosei_home(
    kyo, git_project: Path, store_home: Path
) -> None:
    """The path is derived, not demanded. Requiring one is what made every worktree
    this tool ever used a sibling of the code repository."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-70", "--json")
    assert result.code == 0, result

    expected = _worktrees_home(store_home, git_project) / "kyo-70"
    assert Path(result.json["worktree_resolved"]) == expected.resolve()
    assert result.json["worktree_derived"] is True


def test_the_derived_default_is_never_a_sibling_of_the_code_repository(
    kyo, git_project: Path
) -> None:
    """KYO-70.c1's second clause, stated as its own assertion because it is the
    entire complaint: directories appearing beside a maintainer's unrelated work."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-70", "--json")
    resolved = Path(result.json["worktree_resolved"])
    assert resolved.parent != git_project.parent, resolved
    # And not inside the repository either — that is the one arrangement git warns
    # about, which is why `.kyosei-worktrees/` was never an option.
    assert git_project.resolve() not in resolved.parents, resolved


def test_worktree_still_overrides_the_derived_default(kyo, git_project: Path, tmp_path) -> None:
    tree = tmp_path / "somewhere-else"
    git_worktree(git_project, tree)
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-70", "--worktree", str(tree), "--json")
    assert result.code == 0, result
    assert Path(result.json["worktree_resolved"]) == tree.resolve()
    assert result.json["worktree_derived"] is False


def test_with_no_branch_the_project_root_default_is_untouched(kyo, git_project: Path) -> None:
    """KYO-22's default is load-bearing and stays: the project root IS the
    shared-checkout case, so two undeclared dispatches must still collide. There is
    no branch to name a directory after anyway."""
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    _declare_disjoint_touches(kyo, git_project, first, second)

    assert kyo("dispatch", first, "--json").json["worktree"] == "."
    collided = kyo("dispatch", second)
    assert collided.code != 0
    assert "already running" in collided.err


def test_two_dispatches_on_one_branch_still_collide(kyo, git_project: Path) -> None:
    """Branch-per-worktree is the only naming that cannot collide with itself — git
    forbids two worktrees on one branch — so two dispatches declaring the same
    branch land on one path and the existing refusal catches them. That is the
    correct answer, not a coincidence."""
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    _declare_disjoint_touches(kyo, git_project, first, second)

    assert kyo("dispatch", first, "--branch", "shared").code == 0
    clash = kyo("dispatch", second, "--branch", "shared")
    assert clash.code != 0
    assert "already running" in clash.err


def test_dispatch_prints_the_git_worktree_add_command(kyo, git_project: Path) -> None:
    """`kyo dispatch` derives the path and creates its home; creating a git worktree
    is a code-repository act a store command has no business performing. The
    controller should not have to reconstruct the command either."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-70")
    assert result.code == 0, result
    assert "git worktree add" in result.out
    assert "-b kyo-70" in result.out


def test_the_worktree_home_is_created_but_the_worktree_is_not(
    kyo, git_project: Path, store_home: Path
) -> None:
    # KYO-485: `kyo dispatch --branch` now creates the worktree by default; this is the
    # opt-out, which keeps KYO-70's original behaviour (home made, worktree not).
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-70").code == 0
    home = _worktrees_home(store_home, git_project)
    assert home.is_dir(), "the home has to exist for `git worktree add` to work"
    assert not (home / "kyo-70").exists(), "--no-create-worktree creates nothing"


# --------------------------------------------------------------------- KYO-70.c2


def test_a_derived_worktree_stores_a_token_not_a_dot_dot_chain(
    kyo, git_project: Path
) -> None:
    """The decision KYO-70.c2 demanded. KYO-52 stores path-typed columns relative to
    the code root; a home under ~/kyosei makes that `../../../kyosei/<project>/...`,
    which drops the username but encodes how deep the project sits in the user's
    tree and is meaningless on a checkout at another depth.

    Of the three options on the record, this is the token: it names the RULE that
    produced the path rather than any machine's spelling of it."""
    record = new_record(kyo, criteria=["a"])
    stored = kyo("dispatch", record, "--branch", "kyo-70", "--json").json["worktree"]
    assert stored == f"{WORKTREE_HOME_TOKEN}/kyo-70"
    assert ".." not in stored
    assert not Path(stored).is_absolute()


def test_no_username_or_depth_reaches_the_export(kyo, git_project: Path) -> None:
    """The question KYO-52 was found while answering — what would leak if this repo
    were pushed public — asked again of the new home."""
    import json
    import os

    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-70").code == 0
    doc = json.loads(
        (git_project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    stored = doc["dispatches"][0]["worktree"]
    assert stored == f"{WORKTREE_HOME_TOKEN}/kyo-70"
    assert os.path.expanduser("~") not in stored


def test_an_explicit_worktree_elsewhere_is_still_stored_kyo52s_way(
    kyo, git_project: Path, tmp_path
) -> None:
    """The token adds a spelling for the paths Kyosei itself chose. It does not
    reinterpret anyone else's — a handed-in `--worktree` keeps KYO-52's relative
    form exactly as before."""
    tree = tmp_path / "wt-sibling"
    git_worktree(git_project, tree)
    record = new_record(kyo, criteria=["a"])
    stored = kyo("dispatch", record, "--worktree", str(tree), "--json").json["worktree"]
    assert stored.startswith(".."), stored
    assert WORKTREE_HOME_TOKEN not in stored


def test_a_token_and_an_absolute_path_to_one_place_still_collide(
    kyo, git_project: Path, store_home: Path, db
) -> None:
    """Three spellings of one directory — the token, KYO-52's relative form and a
    pre-schema-9 absolute path — must all compare as the same place, or the
    collision check says "not colliding" about two workers in one checkout."""
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    _declare_disjoint_touches(kyo, git_project, first, second)

    assert kyo("dispatch", first, "--branch", "kyo-70").code == 0
    absolute = str((_worktrees_home(store_home, git_project) / "kyo-70").resolve())
    conn = db()
    conn.execute("UPDATE dispatches SET worktree = ? WHERE record_id = ?", (absolute, first))
    conn.close()

    git_worktree(git_project, _worktrees_home(store_home, git_project) / "kyo-70")
    clash = kyo("dispatch", second, "--worktree", absolute)
    assert clash.code != 0
    assert "already running" in clash.err


def test_the_token_survives_a_rebuild(kyo, git_project: Path, store_home: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-70").code == 0
    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    stored = kyo("show", record, "--json").json["dispatches"][0]["worktree"]
    assert stored == f"{WORKTREE_HOME_TOKEN}/kyo-70"


# --------------------------------------------------------------------- KYO-70.c3


def _finished_dispatch_in(
    kyo, git_project: Path, tree: Path, record: str, *, branch: str | None = None
) -> None:
    # KYO-402: a registered worktree of the project, as `kyo dispatch --worktree` now
    # requires and as the only kind of directory `--prune` may remove. It used to be a
    # standalone `git init` — the separate-clone case `--prune` was shown deleting.
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    args = ["dispatch", record, "--worktree", str(tree), "--json"]
    if branch is not None:
        args += ["--branch", branch]
    result = kyo(*args)
    assert result.code == 0, result
    dispatch_id = result.json["dispatch_id"]
    closed = kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "the worker died"
    )
    assert closed.code == 0, closed


def test_worktrees_lists_what_a_prune_would_remove(kyo, git_project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-finished"
    _finished_dispatch_in(kyo, git_project, tree, record)

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    entry = next(r for r in listing.json["worktrees"] if r["worktree_resolved"] == str(tree))
    assert entry["state"] == "prunable"
    assert listing.json["pruned"] is False
    assert listing.json["removed"] == []
    assert tree.is_dir(), "listing is the dry run (law 11); nothing may be removed"


def test_prune_removes_a_finished_dispatchs_worktree(kyo, git_project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-finished"
    _finished_dispatch_in(kyo, git_project, tree, record)

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert str(tree) in pruned.json["removed"]
    assert not tree.exists()


def test_prune_refuses_a_worktree_holding_uncommitted_work(
    kyo, git_project: Path, tmp_path
) -> None:
    """The case this whole command is shaped around. A worker on this project died
    mid-run with 89 uncommitted files; that directory was the only surviving copy of
    its output, and resuming into it recovered the work. Untracked counts: a worker
    that died before its first commit has nothing but untracked files."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-dirty"
    _finished_dispatch_in(kyo, git_project, tree, record)
    (tree / "unsaved-work.txt").write_text("89 files worth\n", encoding="utf-8")

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert tree.is_dir()
    entry = next(r for r in pruned.json["worktrees"] if r["worktree_resolved"] == str(tree))
    assert entry["state"] == "holds-uncommitted-work"


def test_prune_never_touches_a_running_dispatchs_worktree(
    kyo, git_project: Path, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-running"
    git_worktree(git_project, tree)
    assert kyo("dispatch", record, "--worktree", str(tree)).code == 0

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert tree.is_dir()
    entry = next(r for r in pruned.json["worktrees"] if r["worktree_resolved"] == str(tree))
    assert entry["state"] == "dispatch-still-running"


def test_prune_never_removes_the_project_root(kyo, git_project: Path) -> None:
    """The undeclared-worktree default is the project root itself. Pruning it would
    delete the checkout, so it is excluded by identity, not by luck."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--json")
    assert result.json["worktree"] == "."
    assert kyo("dispatch", "close", result.json["dispatch_id"], "--terminated-no-report",
               "--why", "done").code == 0

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert git_project.is_dir()


def test_prune_leaves_the_branch_alone_and_says_so(kyo, git_project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-branch"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    result = kyo("dispatch", record, "--worktree", str(tree), "--branch", "kyo-keepme", "--json")
    assert result.code == 0, result
    assert kyo("dispatch", "close", result.json["dispatch_id"], "--terminated-no-report",
               "--why", "died").code == 0

    pruned = kyo("worktrees", "--prune")
    assert pruned.code == 0, pruned
    assert "kyo-keepme" in pruned.out
    assert "left alone" in pruned.out


# --------------------------------------------------------------------- KYO-174/KYO-196


def test_prune_leaves_git_agreeing_the_worktree_is_gone(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-174.c1. `shutil.rmtree` alone leaves git's own admin state stale —
    ``git worktree list`` keeps a `prunable` entry forever, since pruning a
    worktree never removes a dispatch. A *real*, git-registered worktree here
    (unlike `_finished_dispatch_in`'s standalone `git init`), so the assertion is
    about git's own bookkeeping, not just the directory."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-real"
    subprocess.run(
        ["git", "worktree", "add", "-b", "kyo-real-prune-test", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    before = subprocess.run(
        ["git", "worktree", "list"], cwd=git_project, check=True, capture_output=True, text=True
    ).stdout
    assert str(tree) in before

    result = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert result.code == 0, result
    assert kyo(
        "dispatch",
        "close",
        result.json["dispatch_id"],
        "--terminated-no-report",
        "--why",
        "done",
    ).code == 0

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert str(tree) in pruned.json["removed"]

    after = subprocess.run(
        ["git", "worktree", "list"], cwd=git_project, check=True, capture_output=True, text=True
    ).stdout
    assert str(tree) not in after, (
        f"git worktree list still holds an entry after --prune:\nbefore:\n{before}\n"
        f"after:\n{after}"
    )


def test_prune_still_refuses_a_worktree_holding_uncommitted_work_kyo174(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-174.c2, re-proven against the shape this dispatch changed: nothing
    about routing removal through `git worktree prune` afterward may weaken the
    refusal a dirty tree gets *before* removal is ever attempted. Delete-the-
    mechanism proof lives in the report, not here (deleting `_worktree_dirty`'s
    call site is what KYO-70's own suite already exercises); this proves the
    behaviour end to end on a real git-registered worktree."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-real-dirty"
    subprocess.run(
        ["git", "worktree", "add", "-b", "kyo-real-dirty-test", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    result = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert result.code == 0, result
    assert kyo(
        "dispatch",
        "close",
        result.json["dispatch_id"],
        "--terminated-no-report",
        "--why",
        "done",
    ).code == 0
    (tree / "unsaved-work.txt").write_text("recovered here or nowhere\n", encoding="utf-8")

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert tree.is_dir()
    entry = next(r for r in pruned.json["worktrees"] if r["worktree_resolved"] == str(tree))
    assert entry["state"] == "holds-uncommitted-work"
    # And git's own admin entry is naturally still there too — nothing ran a
    # prune over a directory the tool refused to touch.
    still = subprocess.run(
        ["git", "worktree", "list"], cwd=git_project, check=True, capture_output=True, text=True
    ).stdout
    assert str(tree) in still


def test_prune_never_deletes_a_branch(kyo, git_project: Path, tmp_path) -> None:
    """KYO-174.c3, on a real git-registered worktree: the branch a finished,
    clean, removed worktree was on must still resolve after `--prune`."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-real-branch-survives"
    subprocess.run(
        ["git", "worktree", "add", "-b", "kyo-branch-must-survive", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    result = kyo(
        "dispatch", record, "--worktree", str(tree), "--branch", "kyo-branch-must-survive",
        "--json",
    )
    assert result.code == 0, result
    assert kyo(
        "dispatch",
        "close",
        result.json["dispatch_id"],
        "--terminated-no-report",
        "--why",
        "done",
    ).code == 0

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert str(tree) in pruned.json["removed"]
    assert pruned.json["branches_left_alone"] == ["kyo-branch-must-survive"]

    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "kyo-branch-must-survive"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, "the branch must still resolve after --prune removed its tree"


def test_worktrees_names_a_stale_git_admin_entry_distinctly_from_gone(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-174.c4. The record's own vocabulary clash: `git worktree list` and `kyo
    worktrees` mean near-opposite things by "prunable". A directory removed by
    some means OTHER than `kyo worktrees --prune` (simulating exactly what the 40
    historical ghosts were: `git worktree prune` never ran) must read differently
    from a directory that was simply never a git worktree at all — both are
    "the directory is not there", but only one still needs `git worktree prune`
    to finish clearing it."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-stale-admin"
    subprocess.run(
        ["git", "worktree", "add", "-b", "kyo-stale-admin-test", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    result = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert result.code == 0, result
    assert kyo(
        "dispatch",
        "close",
        result.json["dispatch_id"],
        "--terminated-no-report",
        "--why",
        "done",
    ).code == 0

    # Remove the directory WITHOUT going through git or `kyo worktrees --prune` —
    # exactly what left git's admin state behind for the 40 the record describes.
    shutil.rmtree(tree)

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    entry = next(r for r in listing.json["worktrees"] if r["worktree_resolved"] == str(tree))
    assert entry["state"] == "stale-git-entry"
    assert "git worktree prune" in entry["detail"]
    assert entry["state"] != "already-gone"


def test_a_never_registered_gone_directory_still_reads_as_plain_gone(
    kyo, git_project: Path, tmp_path
) -> None:
    """The other half of KYO-174.c4: not every gone directory has a stale git
    entry behind it. A directory that was never a git-registered worktree in the
    first place — just a plain directory `kyo dispatch --worktree` accepted, since
    long removed — must keep reading as the plain, original `already-gone`:
    nothing to run, nothing left for git to forget. KYO-402: `--worktree` now refuses
    a plain directory, so the same end state is reached the way git leaves it after
    `git worktree remove`: no directory and no admin entry."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-never-was"
    git_worktree(git_project, tree)
    result = kyo("dispatch", record, "--worktree", str(tree), "--json")
    assert result.code == 0, result
    assert kyo(
        "dispatch",
        "close",
        result.json["dispatch_id"],
        "--terminated-no-report",
        "--why",
        "never ran",
    ).code == 0
    subprocess.run(
        ["git", "worktree", "remove", str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    entry = next(r for r in listing.json["worktrees"] if r["worktree_resolved"] == str(tree))
    assert entry["state"] == "already-gone"


def test_prune_names_only_the_branches_of_worktrees_it_removed(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-196.c1/c3. The line used to name every branch any dispatch in the store
    had EVER declared a worktree for — accumulating forever, since pruning a
    worktree does not remove a dispatch. Two FINISHED dispatches here; only one
    worktree is actually prunable (the other holds uncommitted work), and only
    its branch may appear in what this run reports removing."""
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["a"])
    clean_tree = tmp_path / "wt-clean-branch"
    _finished_dispatch_in(kyo, git_project, clean_tree, first, branch="kyo-clean-branch")
    dirty_tree = tmp_path / "wt-dirty-branch"
    _finished_dispatch_in(kyo, git_project, dirty_tree, second, branch="kyo-dirty-branch")
    (dirty_tree / "unsaved.txt").write_text("still here\n", encoding="utf-8")

    listing = kyo("worktrees", "--json").json["worktrees"]
    clean_branch = next(r["branch"] for r in listing if r["worktree_resolved"] == str(clean_tree))
    dirty_branch = next(r["branch"] for r in listing if r["worktree_resolved"] == str(dirty_tree))
    assert clean_branch != dirty_branch

    # One --prune --json call is the whole act; asserting against its structured
    # `removed` / `branches_left_alone` fields is the strong form of this check
    # (KYO-196.c1's JSON-payload half) — a rendered text line and this JSON array
    # come from the same `removed_branches` set, so this also settles which
    # branches the *line* is built from without a second, destructive prune call
    # (the sibling test `test_prune_leaves_the_branch_alone_and_says_so` already
    # proves the line itself renders a real branch name and the "left alone"
    # phrase, in plain text).
    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert str(clean_tree) in pruned.json["removed"]
    assert str(dirty_tree) not in pruned.json["removed"]
    assert pruned.json["branches_left_alone"] == [clean_branch]
    assert dirty_branch not in pruned.json["branches_left_alone"]


def test_prune_removing_nothing_prints_no_branch_list(kyo, git_project: Path, tmp_path) -> None:
    """KYO-196.c2. A prune that removes nothing (the worktree is still running,
    so nothing is prunable) must print no "branches are left alone" line at all,
    and `kyo worktrees` without `--prune` must print none either — the concept
    only means something once something has actually been removed."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-running-only"
    git_worktree(git_project, tree)
    assert kyo(
        "dispatch", record, "--worktree", str(tree), "--branch", "kyo-still-running"
    ).code == 0

    pruned_json = kyo("worktrees", "--prune", "--json")
    assert pruned_json.code == 0, pruned_json
    assert pruned_json.json["removed"] == []
    assert pruned_json.json["branches_left_alone"] == []

    pruned_text = kyo("worktrees", "--prune")
    assert pruned_text.code == 0, pruned_text
    assert "left alone" not in pruned_text.out

    listed_json = kyo("worktrees", "--json")
    assert listed_json.code == 0, listed_json
    assert listed_json.json["branches_left_alone"] == []

    listed_text = kyo("worktrees")
    assert listed_text.code == 0, listed_text
    assert "left alone" not in listed_text.out


# --------------------------------------------------------------------- KYO-70.c4/c5


def test_init_says_where_worktrees_go_beside_where_the_store_goes(tmp_path) -> None:
    """KYO-70.c4. Nothing used to warn that dispatching would materialise
    directories at all, which is how two of them came to sit unnoticed next to
    unrelated projects."""
    code = tmp_path / "somecode"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), "--prefix", "TST"])
    assert result.code == 0, result
    assert "store root" in result.out
    # KYO-558.c6: init names where the explanation is; --explain prints it.
    assert "dispatch worktrees go: `kyo init --explain" in result.out
    result = invoke(["init", str(code), "--explain"])
    assert "worktrees" in result.out
    assert "kyo worktrees --prune" in result.out


def test_an_in_repo_project_gets_a_kyosei_home_solely_for_worktrees(
    tmp_path, store_home: Path
) -> None:
    """KYO-70.c5, decided rather than left to fall through. The alternative on the
    record — keep siblings and merely warn — was refused: siblings are the entire
    defect, and a warning leaves the directories exactly where they surprise people.
    Nothing about the store moves; what appears under ~/kyosei/<name>/ is one
    worktrees directory and nothing else."""
    code = tmp_path / "inrepo"
    code.mkdir()
    result = invoke(["init", str(code), "--in-repo", "--prefix", "TST", "--json"])
    assert result.code == 0, result
    assert result.json["layout"] == "in-repo"
    assert result.json["worktree_home"] == str(store_home / "inrepo" / WORKTREES_DIRNAME)

    text = invoke(["init", str(tmp_path / "inrepo2"), "--in-repo", "--explain"])
    assert "solely to hold worktrees" in text.out


def test_a_split_project_puts_worktrees_beside_its_store(tmp_path, store_home: Path) -> None:
    """One home per project, discoverable by the same rule as the store — which is
    the store home's own argument, inherited whole."""
    code = tmp_path / "splitcode"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), "--prefix", "TST", "--json"])
    assert result.code == 0, result
    assert result.json["layout"] == "split"
    store_root = Path(result.json["store_root"])
    assert result.json["worktree_home"] == str(store_root / WORKTREES_DIRNAME)
    assert store_root.parent == store_home


# --------------------------------------------------------------------- KYO-89.c1


def test_a_derived_worktree_path_contains_a_space(
    kyo, git_project: Path, store_home: Path
) -> None:
    """KYO-89.c1, at the one place it can be asserted without creating a worktree.

    A worker cannot see this: its own worktree was created before the change that
    would move it, and `kyo dispatch` is a store write no worker may make. What it
    can check is the pure function every derived path comes out of.

    The point of the space is not the space. It is that a worker's `make test` now
    exercises quoting and escaping that `main`'s checkout may or may not — and
    whichever way `main` goes, the worker's environment is no longer the *more*
    permissive of the two, so a whole class of bug stops being invisible from where
    the work is done.
    """
    from kyosei import config as config_module

    derived = config_module.default_worktree_dir("someproject", "kyo-89")
    assert " " in str(derived), derived
    # And through the whole command, not only the helper: the value a dispatch
    # actually records and resolves.
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-89", "--json")
    assert result.code == 0, result
    assert " " in result.json["worktree_resolved"], result.json["worktree_resolved"]


def test_the_printed_git_worktree_add_command_survives_the_space(
    kyo, git_project: Path, store_home: Path
) -> None:
    """The first thing the space breaks, and the first thing it catches.

    `kyo dispatch` prints a `git worktree add` line for the controller to run as
    printed. Unquoted, a spaced home turns that one command into two arguments and
    the controller gets a directory named after the first word. So the line is
    spelled for a shell — and read back the way a shell reads it, per KYO-83: a
    ``\\S+``-shaped assertion here would pass on the quoting it is supposed to check.
    """
    import shlex

    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-89")
    assert result.code == 0, result
    spelled = next(
        line for line in result.out.splitlines() if "git worktree add" in line
    )
    argv = shlex.split(spelled.partition("create it with: ")[2])

    expected = _worktrees_home(store_home, git_project) / "kyo-89"
    assert argv[:3] == ["git", "worktree", "add"], argv
    assert Path(argv[3]) == expected.resolve(), argv
    assert argv[4:6] == ["-b", "kyo-89"], argv


# --------------------------------------------------------------------- KYO-89.c2


def test_the_home_is_still_under_the_kyosei_home_and_still_named_for_the_branch(
    kyo, git_project: Path, store_home: Path
) -> None:
    """KYO-89.c2 verbatim: the space must not undo KYO-70. The home stays one level
    under this project's ~/kyosei directory, the leaf stays the branch name, and the
    directory is still nowhere near the maintainer's own projects."""
    record = new_record(kyo, criteria=["a"])
    resolved = Path(
        kyo("dispatch", record, "--branch", "kyo-89", "--json").json["worktree_resolved"]
    )

    assert resolved.name == "kyo-89", resolved
    assert resolved.parent == _worktrees_home(store_home, git_project).resolve()
    assert resolved.parent.parent.parent == store_home.resolve()
    assert resolved.parent != git_project.parent
    assert git_project.resolve() not in resolved.parents


def test_the_home_is_still_typeable_and_still_says_what_it_holds(kyo) -> None:
    """KYO-70's actual requirement, which the space had to be weighed against: a
    maintainer who finds this directory can tell what put it there and can `cd` into
    it. A newline, a quote or a `$` would exercise more of the class and fail that,
    so the name carries exactly one ordinary space and still reads as English."""
    from kyosei.config import WORKTREES_DIRNAME

    assert " " in WORKTREES_DIRNAME
    assert "worktrees" in WORKTREES_DIRNAME
    assert WORKTREES_DIRNAME.strip() == WORKTREES_DIRNAME
    assert not (set(WORKTREES_DIRNAME) - set("abcdefghijklmnopqrstuvwxyz -"))


def test_a_worktree_under_the_spaced_home_still_stores_as_a_token(
    kyo, git_project: Path
) -> None:
    """KYO-70.c2's property, re-asserted against the new name: what reaches the
    git-canonical export is the rule, not a spelling — so renaming the home again
    would not rewrite a single stored row."""
    record = new_record(kyo, criteria=["a"])
    stored = kyo("dispatch", record, "--branch", "kyo-89", "--json").json["worktree"]
    assert stored == f"{WORKTREE_HOME_TOKEN}/kyo-89"
    assert " " not in stored


def test_worktrees_names_a_pre_kyo89_home_that_still_holds_work(
    kyo, git_project: Path, store_home: Path
) -> None:
    """The one migration cost of the rename, made visible rather than left to
    accumulate. A directory created under the old name resolves nowhere now, so
    `kyo worktrees` would call every row `already-gone` while the work sat on disk —
    the silent accumulation KYO-70 exists to end. Named, never removed: this command
    did not create them under the name it now uses."""
    legacy = store_home / git_project.name / "worktrees"
    (legacy / "kyo-60").mkdir(parents=True)
    (legacy / "kyo-60" / "unsaved.txt").write_text("work\n", encoding="utf-8")

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    assert listing.json["legacy_homes_still_holding_work"] == [str(legacy)]
    assert "kyo-60" in kyo("worktrees").out
    assert (legacy / "kyo-60" / "unsaved.txt").is_file(), "nothing may be removed"

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert (legacy / "kyo-60" / "unsaved.txt").is_file(), "--prune must not reach it either"


# --------------------------------------------------------------------------- KYO-207
#
# "Finished" meant the dispatch state was `reported`, which is true the instant the
# worker stops and says nothing about whether anybody has merged or rehearsed what it
# wrote. APP-20 reported; the controller pruned another dispatch's worktree and APP-20's
# went with it. Nothing was lost — the worker had committed and the branch survives by
# design — but the controller had to recreate the worktree to run the rehearsal from that
# code. `branch_is_contained_in` was already in this module for the drift check.


def _reported_dispatch_on_a_real_branch(
    kyo, git_project: Path, tree: Path, record: str, branch: str, tmp_path: Path
) -> str:
    """A dispatch that has REPORTED, in a real git worktree of the project, whose branch
    carries a commit of its own — the consumer's APP-20 exactly."""
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(tree)],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    (tree / "worker-output.txt").write_text("the work\n", encoding="utf-8")
    for args in (
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "the worker's own commit"],
    ):
        subprocess.run(args, cwd=tree, check=True, capture_output=True, text=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip()

    dispatched = kyo(
        "dispatch", record, "--worktree", str(tree), "--branch", branch, "--json"
    )
    assert dispatched.code == 0, dispatched
    report = tmp_path / f"{branch}-report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record,
                worked_from_sha=dispatched.json["worked_from_sha"],
                commits=[{"sha": sha, "subject": "the work"}],
            )
        ),
        encoding="utf-8",
    )
    ingested = kyo(
        "--full", "report", dispatched.json["dispatch_id"], "--file", str(report), "--json"
    )
    assert ingested.code == 0, ingested
    assert ingested.json["record"]["dispatches"][0]["state"] == "reported"
    return dispatched.json["dispatch_id"]


def _row(result, tree: Path) -> dict:
    return next(
        r for r in result.json["worktrees"] if r["worktree_resolved"] == str(tree)
    )


def test_prune_leaves_a_reported_dispatch_whose_branch_is_not_merged(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-207.c1/c3, the consumer's sequence exactly. Restore
    `finished = row["state"] in FINISHED_DISPATCH_STATES ...` as the whole prune decision
    in `cmd_worktrees._worktree_rows` and this fails: the directory is removed while the
    branch is still unmerged, and the rehearsal has nowhere to run."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-app-20"
    _reported_dispatch_on_a_real_branch(
        kyo, git_project, tree, record, "kyo-207-unmerged", tmp_path
    )

    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert pruned.json["removed"] == []
    assert tree.is_dir(), "a reported-but-unmerged dispatch's worktree must survive"
    row = _row(pruned, tree)
    assert row["state"] == "branch-not-merged"
    assert row["branch_merged"] is False
    assert "kyo-207-unmerged" in row["detail"], "the line has to name the branch"
    assert "--include-unmerged" in row["detail"], "and the flag that removes it anyway"


def test_the_same_worktree_is_removed_once_the_branch_merges(
    kyo, git_project: Path, tmp_path
) -> None:
    """The other half of c3: this withholds on a FACT, not on a permanent refusal. Merge
    the branch and the same command removes the same directory."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-then-merged"
    _reported_dispatch_on_a_real_branch(
        kyo, git_project, tree, record, "kyo-207-will-merge", tmp_path
    )
    assert kyo("worktrees", "--prune", "--json").json["removed"] == []

    subprocess.run(
        ["git", "merge", "--no-ff", "-m", "merge it", "kyo-207-will-merge"],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )
    pruned = kyo("worktrees", "--prune", "--json")
    assert pruned.code == 0, pruned
    assert str(tree) in pruned.json["removed"]
    assert not tree.exists()


def test_include_unmerged_removes_it_anyway(kyo, git_project: Path, tmp_path) -> None:
    """c3's third leg: the override the held-back line names actually works, and the
    branch it was on still resolves afterwards — the directory is this command's subject,
    never the history."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-override"
    _reported_dispatch_on_a_real_branch(
        kyo, git_project, tree, record, "kyo-207-override", tmp_path
    )

    pruned = kyo("worktrees", "--prune", "--include-unmerged", "--json")
    assert pruned.code == 0, pruned
    assert str(tree) in pruned.json["removed"]
    assert not tree.exists()
    assert pruned.json["include_unmerged"] is True
    survives = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "kyo-207-override"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert survives.returncode == 0, "pruning a directory must never delete a branch"


def test_the_plain_listing_says_whether_each_finished_branch_is_merged(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-207.c2: a reader sees what a prune would remove BEFORE running one. The skill
    sold `--prune` as safe because "branches are left alone", which is true and is not the
    property the consumer needed."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-listing"
    _reported_dispatch_on_a_real_branch(
        kyo, git_project, tree, record, "kyo-207-listing", tmp_path
    )

    listing = kyo("worktrees")
    assert listing.code == 0, listing
    assert "NOT merged into main" in listing.out
    assert "kyo-207-listing" in listing.out
    assert "--include-unmerged" in listing.out
    assert "0 would be removed by `kyo worktrees --prune`" in listing.out
    assert tree.is_dir(), "listing is the dry run (law 11)"

    as_json = kyo("worktrees", "--json")
    dispatch_id = _row(as_json, tree)["dispatch_id"]
    assert as_json.json["unmerged"] == [dispatch_id]
    assert as_json.json["against"] == "main"


def test_against_names_a_trunk_that_is_not_called_main(
    kyo, git_project: Path, tmp_path
) -> None:
    """Without this flag the containment question has no answer in a project whose trunk
    is called something else, and every finished worktree there would be withheld forever
    — a cure worse than the defect (KYO-33)."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-trunk"
    _reported_dispatch_on_a_real_branch(
        kyo, git_project, tree, record, "kyo-207-trunk", tmp_path
    )
    subprocess.run(
        ["git", "branch", "trunk", "kyo-207-trunk"],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    )

    held = kyo("worktrees", "--json")
    assert _row(held, tree)["state"] == "branch-not-merged"
    against_trunk = kyo("worktrees", "--against", "trunk", "--json")
    row = _row(against_trunk, tree)
    assert row["state"] == "prunable"
    assert row["branch_merged"] is True
    assert row["merge_base"] == "trunk"


def test_a_branch_that_does_not_resolve_is_still_prunable(
    kyo, git_project: Path, tmp_path
) -> None:
    """Law 6's line drawn where it belongs. "git could not tell" and "the branch is not
    there" are different facts: a branch that does not resolve holds no unmerged work
    this directory could be the only copy of, so withholding it would strand every
    worktree whose branch was never created or has since been deleted. KYO-402: the
    question is now asked of the worktree's HEAD, which here is the trunk's own tip, so
    the row is prunable because that HEAD is merged — not because the branch is absent
    (an absent branch with unmerged commits on HEAD is withheld; see
    tests/test_worktree_prune_safety.py)."""
    record = new_record(kyo, criteria=["a"])
    tree = tmp_path / "wt-no-such-branch"
    _finished_dispatch_in(kyo, git_project, tree, record, branch="kyo-207-never-created")

    listing = kyo("worktrees", "--json")
    row = _row(listing, tree)
    assert row["state"] == "prunable"
    assert "is merged into main" in row["detail"]
    assert kyo("worktrees", "--prune", "--json").json["removed"] == [str(tree)]
