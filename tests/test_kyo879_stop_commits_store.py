"""KYO-879: the Stop hook (``kyo doctor --hook``) commits a split store, one commit a
turn, and only names a store that lives inside the code repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kyosei import stop_commit

from .conftest import git_init, invoke


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _store_repo(store: Path) -> None:
    """The store as its own repository with one commit of what ``init`` wrote."""
    _git(store, "init", "-q", "-b", "main")
    _git(store, "config", "user.email", "test@example.com")
    _git(store, "config", "user.name", "Test")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "--allow-empty", "-m", "init")


def _file_record(code: Path, title: str) -> str:
    result = invoke(["--root", str(code), "new", "bug", "--title", title, "--force", "--json"])
    assert result.code == 0, result
    return result.json["id"]


def _hook(code: Path):
    return invoke(["--root", str(code), "doctor", "--hook"])


def test_a_split_store_is_committed_at_stop_with_the_record_ids(split_project) -> None:
    code, store = split_project
    _store_repo(store)
    before = _git(store, "rev-list", "--count", "HEAD")
    first = _file_record(code, "first")
    second = _file_record(code, "second")
    _hook(code)
    assert int(_git(store, "rev-list", "--count", "HEAD")) == int(before) + 1
    message = _git(store, "log", "-1", "--format=%B")
    assert first in message and second in message
    assert "first" not in message
    assert _git(store, "status", "--porcelain", "--", "kyosei", "ideas") == ""


def test_a_stop_with_nothing_uncommitted_makes_no_commit(split_project) -> None:
    code, store = split_project
    _store_repo(store)
    _file_record(code, "once")
    _hook(code)
    head = _git(store, "rev-parse", "HEAD")
    _hook(code)
    assert _git(store, "rev-parse", "HEAD") == head


def test_an_in_repo_store_is_named_and_never_committed(project) -> None:
    git_init(project)
    head = _git(project, "rev-parse", "HEAD")
    invoke(["--root", str(project), "new", "bug", "--title", "inside", "--force", "--json"])
    ctx_lines = _stop_lines(project)
    assert len(ctx_lines) == 1 and "uncommitted store path" in ctx_lines[0]
    assert "kyosei/export" in ctx_lines[0]
    assert _git(project, "rev-parse", "HEAD") == head


def test_a_worktree_session_commits_nothing(split_project) -> None:
    code, store = split_project
    _store_repo(store)
    worktree = code.parent / "wt"
    _git(code, "worktree", "add", "-q", "-b", "wt-branch", str(worktree))
    head = _git(store, "rev-parse", "HEAD")
    invoke(["--root", str(code), "new", "bug", "--title", "worker side", "--force", "--json"])
    assert _stop_lines(worktree) == []
    assert _git(store, "rev-parse", "HEAD") == head
    # the same store, from the main checkout, is committed
    _hook(code)
    assert _git(store, "rev-parse", "HEAD") != head


def test_a_failed_commit_is_one_line_and_does_not_raise(split_project, monkeypatch) -> None:
    code, store = split_project
    _store_repo(store)
    _file_record(code, "x")

    def refuse(*_a, **_k):
        raise stop_commit.KyoseiError("git commit failed in store: no identity")

    monkeypatch.setattr(stop_commit, "_commit", refuse)
    lines = _stop_lines(code)
    assert len(lines) == 1 and "not committed" in lines[0]


def _stop_lines(root: Path) -> list[str]:
    from kyosei import config as config_module
    from kyosei.commands import Context

    layout = config_module.resolve_layout(root)
    ctx = Context(root=layout.store_root, actor="test", argv=[], layout=layout)
    return stop_commit.commit_at_stop(ctx)
