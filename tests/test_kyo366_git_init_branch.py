"""KYO-366: the suite's fixtures must not depend on the host's global
``init.defaultBranch``.

The measured defect: ``tests/conftest.py#git_init`` ran a bare ``git init -q``, so
the fixture repos landed on whatever the host's global ``init.defaultBranch`` said
(or git's own compiled-in ``master`` default when that setting is unset). On a
machine with no such global setting, ten files failed with
``'main' is not a known git ref`` -- on a machine where ``init.defaultBranch=main``
is set globally, the same bug passes silently. A test that just calls ``git_init``
and asserts ``main`` would pass on such a machine whether or not the fix is
present, proving nothing; this test
isolates the global config for the duration of the call so it fails on the unfixed
tree regardless of the host's own setting.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .conftest import git_init


def test_git_init_names_main_even_with_no_global_default_branch(
    tmp_path: Path, monkeypatch
) -> None:
    """``GIT_CONFIG_GLOBAL`` pointed at an empty file plus ``GIT_CONFIG_NOSYSTEM``
    removes both the user-level and system-level git config for this call, the way
    a machine with no global setting does -- so this reproduces the defect on any
    machine, including one where the global ``init.defaultBranch`` is already
    ``main`` and would otherwise mask it."""
    empty_global_config = tmp_path / "empty-gitconfig"
    empty_global_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    project = tmp_path / "repo"
    project.mkdir()
    (project / "a.txt").write_text("x\n", encoding="utf-8")

    git_init(project)

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "main"
