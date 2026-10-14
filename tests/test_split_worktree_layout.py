"""KYO-403: a split project's dispatch worktree must not resolve to the live store.

Under the split layout the code repository carries only the ``.kyosei`` store link,
`kyo init` leaves that link uncommitted, and `git worktree add` checks out tracked
files only. The worktree home is ``~/kyosei/<store>/dispatch worktrees/`` — inside
the store root — so a worktree cut before the link was committed had no link, and
`kyo` there climbed out of the worktree's own git toplevel, found the store's
``kyosei/config.yml``, and answered "in-repo, code_root == store_root, branch main".
The worker then wrote the controller's live store and both stop guards inspected
the store repository instead of the worker's.

Four mechanisms, one test group each:

* c1/c2 — :func:`kyosei.config.resolve_layout`'s upward walk stops at the start's git
  toplevel, and nothing that resolved before stops resolving;
* c3 — `kyo dispatch --branch/--worktree` refuses a split project whose link is not
  committed as it stands on HEAD, and the remedy it names clears the refusal;
* c4 — `kyo check-branch` guards the link as an exact path;
* c5 — the store repository ignores the worktree home nested inside it.

c6 (moving the home) was decided against; the reasoning is on
:func:`kyosei.install_gitignore.install_worktree_home_gitignore`, and the last test here pins
the one property that decision rests on: the home did not move.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from kyosei import config as config_module
from kyosei import install_gitignore
from kyosei.config import LAYOUT_IN_REPO, LAYOUT_SPLIT, STORE_LINK_NAME
from kyosei.contracts import GUARDED_BRANCH_FILES, WORKER_REPORT_PATH
from kyosei.errors import KyoseiError

from .conftest import allow_direct_filing, git_init, invoke

BRANCH = "kyo-403-branch"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _sh(cwd: Path, line: str) -> None:
    """Run a remedy exactly as a message printed it, through a shell."""
    subprocess.run(line, cwd=cwd, shell=True, check=True, capture_output=True, text=True)


def _init_split_leaving_the_link_untracked(tmp_path: Path) -> tuple[Path, Path]:
    """The record's arrangement: a code repository with history, then `kyo init`,
    which writes ``.kyosei`` and does not commit it."""
    code = tmp_path / "code"
    code.mkdir()
    (code / "src").mkdir()
    (code / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    git_init(code)
    result = invoke(["init", "--defaults", str(code), "--prefix", "TST", "--json"])
    assert result.code == 0, result
    store = Path(result.json["store_root"])
    status = _git(code, "status", "--porcelain").stdout
    assert f"?? {STORE_LINK_NAME}" in status, (
        "the fixture no longer reproduces the record: `kyo init` committed the link"
    )
    return code, store


def _derived_worktree(code: Path, store: Path, branch: str = BRANCH) -> Path:
    """A real worktree at the path `kyo dispatch --branch` derives — inside the store."""
    tree = config_module.default_worktree_dir(store.name, branch)
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(code, "worktree", "add", "-q", "-b", branch, str(tree))
    assert store.resolve() in tree.resolve().parents, "the home must sit inside the store"
    return tree


def _where(cwd: Path, monkeypatch) -> object:
    monkeypatch.chdir(cwd)
    return invoke(["where", "--json"])


# ---------------------------------------------------------------- c1: the bounded walk


def test_a_worktree_without_the_link_reads_it_through_the_main_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-580.c1: the walk still stops at the worktree's own toplevel (KYO-403), and
    the untracked link is then read from the same place in the main checkout."""
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    tree = _derived_worktree(code, store)
    assert not (tree / STORE_LINK_NAME).exists()

    where = _where(tree, monkeypatch)

    # Before KYO-403: layout in-repo, code_root == store_root, branch main. Before
    # KYO-580: unconfigured.
    assert where.code == 0, where
    assert where.json["configured"] is True, where.out
    assert where.json["layout"] == LAYOUT_SPLIT
    assert Path(where.json["store_root"]) == store
    assert Path(where.json["code_root"]).resolve() == tree.resolve()
    assert where.json["branch"] == BRANCH
    assert Path(where.json["store_link"]) == (code / STORE_LINK_NAME).resolve()
    assert where.json["store_via_main_checkout"] is None  # nothing is read-only
    text = invoke(["where"])
    assert text.code == 0, text
    assert "store link was read through the main checkout" in text.out, text.out

    for start in (tree, tree / "src"):
        layout = config_module.resolve_layout(start)
        assert layout.kind == LAYOUT_SPLIT
        assert layout.store_root == store
        assert layout.code_root == tree.resolve(), start
        assert layout.link_via_main_checkout == code.resolve()
        assert layout.store_via_main_checkout is None


def test_a_link_in_the_worktree_itself_wins_over_the_main_checkouts(
    tmp_path: Path,
) -> None:
    """KYO-580.c2: a worktree that carries its own link (copied here, as recx's
    `worktree_setup` did) is never redirected — shown by breaking the main
    checkout's link, which the worktree then never reads."""
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    tree = _derived_worktree(code, store)
    (tree / STORE_LINK_NAME).write_text(
        (code / STORE_LINK_NAME).read_text(encoding="utf-8"), encoding="utf-8"
    )
    (code / STORE_LINK_NAME).write_text(f"store: {tmp_path / 'nowhere'}\n", encoding="utf-8")

    layout = config_module.resolve_layout(tree / "src")
    assert layout.kind == LAYOUT_SPLIT
    assert layout.store_root == store
    assert layout.link_path == tree.resolve() / STORE_LINK_NAME
    assert layout.link_via_main_checkout is None
    assert "read through the main checkout" not in layout.describe()


def test_a_worktree_whose_main_checkout_has_no_link_names_its_remedy_and_it_clears(
    tmp_path: Path, monkeypatch
) -> None:
    """The bound still refuses where neither checkout carries a link; the message no
    longer asks for a commit, and restoring the link in the main checkout — left
    untracked — resolves the split layout on the worker's own branch."""
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    (code / STORE_LINK_NAME).unlink()
    tree = _derived_worktree(code, store)
    monkeypatch.chdir(tree)
    refused = invoke(["list"])
    assert refused.code != 0, refused
    assert "it may stay untracked" in refused.err
    assert "git add" not in refused.err
    assert "`kyo init --store DIR` run there" in refused.err

    restored = invoke(["init", "--defaults", str(code), "--store", str(store), "--json"])
    assert restored.code == 0, restored
    assert f"?? {STORE_LINK_NAME}" in _git(code, "status", "--porcelain").stdout

    where = _where(tree, monkeypatch)
    assert where.code == 0, where
    assert where.json["layout"] == LAYOUT_SPLIT
    assert Path(where.json["store_root"]) == store
    assert Path(where.json["code_root"]).resolve() == tree.resolve()
    assert where.json["branch"] == BRANCH


def test_a_plain_repository_with_no_project_keeps_the_init_remedy_alone(
    tmp_path: Path,
) -> None:
    """The worktree sentence is only for a linked worktree; a fresh repository nobody
    has initialised gets the message it always got."""
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    with pytest.raises(KyoseiError) as raised:
        config_module.resolve_layout(repo)
    assert "run `kyo init` first" in str(raised.value)
    assert "it may stay untracked" not in str(raised.value)


def test_outside_any_repository_the_walk_still_climbs(tmp_path: Path) -> None:
    """The bound is a git toplevel; with none, a store above is found as before."""
    root = tmp_path / "plain"
    assert invoke(["init", str(root), "--in-repo", "--prefix", "TST"]).code == 0
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    assert config_module.resolve_layout(deep).store_root == root.resolve()


# ------------------------------------------------- c2: what resolved before still does


def test_an_in_repo_project_resolves_from_root_subdirectory_and_linked_worktree(
    tmp_path: Path,
) -> None:
    project = tmp_path / "proj"
    assert invoke(["init", str(project), "--in-repo", "--prefix", "TST"]).code == 0
    (project / "src").mkdir()
    (project / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    git_init(project)
    assert (project / "kyosei" / "kyosei.db").is_file()

    for start in (project, project / "src"):
        layout = config_module.resolve_layout(start)
        assert layout.kind == LAYOUT_IN_REPO
        assert layout.store_root == project.resolve()

    tree = tmp_path / "linked"
    _git(project, "worktree", "add", "-q", "-b", "in-repo-branch", str(tree))
    # KYO-342: the worktree's own kyosei/ has no database, so the read goes through
    # the main checkout — found at the worktree's own toplevel, inside the bound.
    for start in (tree, tree / "src"):
        layout = config_module.resolve_layout(start)
        assert layout.kind == LAYOUT_IN_REPO
        assert layout.store_root == project.resolve()
        assert layout.store_via_main_checkout == project.resolve()


def test_a_split_project_with_a_committed_link_resolves_everywhere(
    split_project: tuple[Path, Path], tmp_path: Path
) -> None:
    code, store = split_project  # `git_init` after `kyo init`: the link is committed
    tree = _derived_worktree(code, store)
    for start in (code, code / "src", tree, tree / "src"):
        layout = config_module.resolve_layout(start)
        assert layout.kind == LAYOUT_SPLIT, start
        assert layout.store_root == store
        assert layout.store_via_main_checkout is None
        assert layout.link_via_main_checkout is None  # KYO-580.c2: its own link
    assert config_module.resolve_layout(tree).code_root == tree.resolve()


# --------------------------------------------- c3: dispatch with an untracked link


def _dispatchable_record(code: Path, store: Path, tmp_path: Path) -> str:
    allow_direct_filing(store, "bug")
    made = invoke(
        ["--root", str(code), "new", "bug", "--title", "split work", "--criteria-file", "-",
         "--json"],
        stdin="a criterion\n",
    )
    assert made.code == 0, made
    record = made.json["record"]["id"]
    touches = tmp_path / "touches.txt"
    touches.write_text("src/widget.py\n", encoding="utf-8")
    edited = invoke(["--root", str(code), "edit", record, "--touches-file", str(touches)])
    assert edited.code == 0, edited
    return record


def _dispatch(code: Path, record: str, *extra: str):
    return invoke(["--root", str(code), "dispatch", record, "--branch", BRANCH, *extra])


def test_dispatch_with_an_untracked_link_and_the_worker_reads_its_brief(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-580.c3: KYO-403.c3's refusal is gone, and the case it refused works — the
    worker in the new worktree finds the store and reads its own brief."""
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    record = _dispatchable_record(code, store, tmp_path)

    dispatched = _dispatch(code, record)
    assert dispatched.code == 0, dispatched
    assert "dispatch-needs-a-committed-store-link" not in dispatched.err
    tree = config_module.default_worktree_dir(store.name, BRANCH)
    assert tree.is_dir() and not (tree / STORE_LINK_NAME).exists()

    monkeypatch.chdir(tree)
    shown = invoke(["show", f"{record}.d1"])
    assert shown.code == 0, shown
    assert f"# Dispatch brief: {record}" in shown.out, shown.out


def test_the_refusal_and_its_contract_are_removed() -> None:
    from kyosei import cmd_dispatch, contracts

    assert not hasattr(cmd_dispatch, "_refuse_an_uncommitted_store_link")
    assert not hasattr(contracts, "CONTRACT_DISPATCH_NEEDS_A_COMMITTED_STORE_LINK")


def test_a_dispatch_that_shares_the_code_root_is_not_refused(tmp_path: Path) -> None:
    """No --branch, no --worktree: the worker is in the code root, where the link is."""
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    record = _dispatchable_record(code, store, tmp_path)
    shared = invoke(["--root", str(code), "dispatch", record])
    assert shared.code == 0, shared


# ------------------------------------------------------------- c4: check-branch guards it


def _branch_worktree(code: Path, tmp_path: Path) -> Path:
    tree = tmp_path / "branch-tree"
    _git(code, "worktree", "add", "-q", "-b", BRANCH, str(tree))
    return tree


def _commit_all(tree: Path, message: str) -> None:
    _git(tree, "add", "-A")
    _git(tree, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m",
         message)


def _check_branch(code: Path):
    return invoke(["--root", str(code), "check-branch", BRANCH, "--against", "main"])


@pytest.mark.parametrize("change", ["delete", "modify"])
def test_check_branch_refuses_a_branch_that_deletes_or_repoints_the_link(
    split_project: tuple[Path, Path], tmp_path: Path, change: str
) -> None:
    code, _store = split_project
    tree = _branch_worktree(code, tmp_path)
    link = tree / STORE_LINK_NAME
    if change == "delete":
        link.unlink()
    else:
        link.write_text("store: ~/somewhere/else\n", encoding="utf-8")
    _commit_all(tree, f"{change} the link")

    refused = _check_branch(code)
    assert refused.code == 2, refused
    assert "branch-touches-store" in refused.err
    assert f"diffs {STORE_LINK_NAME} relative to main" in refused.err

    # The remedy the refusal names, run in the branch's own tree.
    remedy = f"git checkout main -- {shlex.quote(STORE_LINK_NAME)}"
    assert f"`git checkout main -- {STORE_LINK_NAME}`" in refused.err
    _sh(tree, remedy)
    _commit_all(tree, "restore the link")
    assert _check_branch(code).code == 0


def test_check_branch_refuses_a_branch_that_adds_the_link(
    git_project: Path, tmp_path: Path
) -> None:
    code = git_project  # in-repo: main has no link, the branch adds one
    tree = _branch_worktree(code, tmp_path)
    (tree / STORE_LINK_NAME).write_text("store: ~/kyosei/elsewhere\n", encoding="utf-8")
    _commit_all(tree, "add a link")

    refused = _check_branch(code)
    assert refused.code == 2, refused
    assert f"diffs {STORE_LINK_NAME} relative to main" in refused.err
    assert f"`git rm {STORE_LINK_NAME}`" in refused.err
    _sh(tree, f"git rm -q {STORE_LINK_NAME}")
    _commit_all(tree, "drop the link")
    assert _check_branch(code).code == 0


def test_the_link_is_guarded_as_an_exact_path_not_a_prefix(
    git_project: Path, tmp_path: Path
) -> None:
    """``.kyosei-report.json`` begins with the link's name and stays the worker's."""
    assert STORE_LINK_NAME in GUARDED_BRANCH_FILES
    assert config_module.STORE_LINK_NAME is STORE_LINK_NAME
    code = git_project
    tree = _branch_worktree(code, tmp_path)
    (tree / f"{STORE_LINK_NAME}-notes.txt").write_text("mine\n", encoding="utf-8")
    _commit_all(tree, "a file that merely starts with the name")
    assert WORKER_REPORT_PATH.startswith(STORE_LINK_NAME)
    assert _check_branch(code).code == 0


def test_stop_guards_in_a_worktree_with_a_borrowed_link_inspect_the_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-580.c4: with the link read through the main checkout, `check-clean` and
    `check-branch` run in the worktree judge the worktree's tree and branch — a dirty
    file there is found, the main checkout's clean tree is not what is reported, and
    the branch checked is the worker's."""
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    tree = _derived_worktree(code, store)
    monkeypatch.chdir(tree)

    (tree / "src" / "widget.py").write_text("x = 2\n", encoding="utf-8")
    dirty = invoke(["check-clean", "--json"])
    assert dirty.code != 0, dirty
    assert "src/widget.py" in dirty.out + dirty.err

    _commit_all(tree, "worker change")
    assert invoke(["check-clean"]).code == 0

    clean = invoke(["check-branch", "--against", "main", "--json"])
    assert clean.code == 0, clean
    assert BRANCH in clean.out, clean.out

    (tree / "ideas").mkdir()
    (tree / "ideas" / "note.md").write_text("an idea\n", encoding="utf-8")
    _commit_all(tree, "a store write on the worker branch")
    refused = invoke(["check-branch", "--against", "main"])
    assert refused.code == 2, refused
    assert "branch-touches-store" in refused.err
    assert BRANCH in refused.out + refused.err


# ---------------------------------------------- c5: the store ignores the nested home


def test_a_derived_dispatch_makes_the_store_ignore_its_worktree_home(
    tmp_path: Path,
) -> None:
    code, store = _init_split_leaving_the_link_untracked(tmp_path)
    _sh(code, f"git add {STORE_LINK_NAME} && git commit -q -m link")
    record = _dispatchable_record(code, store, tmp_path)
    _git(store, "add", "-A")
    _git(store, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m",
         "store")

    dispatched = _dispatch(code, record)
    assert dispatched.code == 0, dispatched
    assert "gitignore: added /dispatch worktrees/" in dispatched.out
    # KYO-485: the dispatch created the worktree itself; it sits inside the store.
    tree = config_module.default_worktree_dir(store.name, BRANCH)
    assert tree.is_dir() and store.resolve() in tree.resolve().parents

    status = _git(store, "status", "--porcelain", "--untracked-files=all").stdout
    assert "dispatch worktrees" not in status, status
    assert config_module.WORKTREES_DIRNAME in (store / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_the_worktree_home_ignore_is_idempotent_and_a_no_op_outside_the_store(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    _git(store, "init", "-q", "-b", "main")
    home = store / config_module.WORKTREES_DIRNAME
    assert install_gitignore.install_worktree_home_gitignore(store, home)
    assert install_gitignore.install_worktree_home_gitignore(store, home) == []
    text = (store / ".gitignore").read_text(encoding="utf-8")
    assert text.count("/dispatch worktrees/") == 1

    # An in-repo project's home is outside every store: nothing to write.
    other = tmp_path / "elsewhere" / config_module.WORKTREES_DIRNAME
    assert install_gitignore.install_worktree_home_gitignore(store, other) == []


# ------------------------------------------------------ c6: the home did not move


def test_the_worktree_home_is_unchanged(tmp_path: Path) -> None:
    """KYO-403 chose the ignore over a move, so stored ``<worktrees>/<name>`` tokens
    keep resolving where they always did — including every running dispatch's."""
    home = config_module.worktree_home("proj")
    assert home == config_module.store_home() / "proj" / "dispatch worktrees"
    assert config_module.resolve_worktree_path(tmp_path, "proj", "<worktrees>/x") == (
        home / "x"
    ).resolve()
