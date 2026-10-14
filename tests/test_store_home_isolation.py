"""The store-home redirect survives a test's own ``monkeypatch.undo()``."""

from __future__ import annotations

import os
from pathlib import Path


def test_monkeypatch_undo_does_not_restore_the_real_store_home(
    store_home: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SOMETHING_ELSE", "1")
    monkeypatch.undo()
    assert os.environ.get("KYOSEI_HOME") == str(store_home)


def test_init_after_undo_registers_in_the_test_home_only(
    store_home: Path, monkeypatch, tmp_path: Path
) -> None:
    from .conftest import invoke

    monkeypatch.undo()
    assert invoke(["init", str(tmp_path / "p"), "--in-repo", "--prefix", "TST"]).code == 0
    assert (store_home / "projects.json").is_file()
