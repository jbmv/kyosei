"""KYO-572: `kyo where` prints the session scratchpad's path.

The desktop app's Scratchpad is the file ``kyo serve`` reads and writes
(``serve_files.scratchpad_target``), and an orchestrator that writes the scratchpad
anywhere else writes where the maintainer cannot see it (KYO-566). So `kyo where`
names that file, and it names it through ``serve_files`` -- the same derivation the
server uses -- rather than a second spelling of ``<store home>/<project>/scratchpad.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

from kyosei import serve_files

from .conftest import invoke


def test_where_prints_the_scratchpad_serve_resolves(git_project: Path, store_home: Path) -> None:
    payload = invoke(["--root", str(git_project), "where", "--json"]).json
    served = serve_files.project_paths(str(git_project), dict(os.environ))
    assert payload["scratchpad"] == str(served.scratchpad)
    assert payload["scratchpad"] == str(serve_files.scratchpad_target(served))
    assert Path(payload["scratchpad"]).is_absolute()
    assert payload["scratchpad"] == str(store_home / git_project.name / "scratchpad.md")
    text = invoke(["--root", str(git_project), "where"]).out
    assert f"scratchpad: {payload['scratchpad']}" in text


def test_where_prints_the_scratchpad_under_the_split_layout(
    split_project: tuple[Path, Path],
) -> None:
    code_root, store_root = split_project
    payload = invoke(["--root", str(code_root), "where", "--json"]).json
    served = serve_files.project_paths(str(code_root), dict(os.environ))
    assert payload["scratchpad"] == str(served.scratchpad)
    assert payload["scratchpad"] == str(store_root / "scratchpad.md")


def test_where_reads_the_path_from_serve_files_not_a_copy(
    git_project: Path, monkeypatch
) -> None:
    """Removing the call to ``serve_files`` (a second spelling) fails here."""
    elsewhere = git_project / "a path only serve_files knows.md"
    monkeypatch.setattr(serve_files, "scratchpad_path", lambda layout, environ=None: elsewhere)
    payload = invoke(["--root", str(git_project), "where", "--json"]).json
    assert payload["scratchpad"] == str(elsewhere)


def test_where_names_no_scratchpad_without_a_project(tmp_path: Path, monkeypatch) -> None:
    nowhere = tmp_path / "no project"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    payload = invoke(["where", "--json"]).json
    assert payload["configured"] is False
    assert payload["scratchpad"] is None
