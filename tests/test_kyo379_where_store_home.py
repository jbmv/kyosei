"""KYO-379: `kyo where --json` reports the machine's store home.

The desktop app keeps the machine's project registry at
``$KYOSEI_HOME/projects.json`` (default ``~/kyosei/projects.json``), beside the
per-project homes. Where that directory is, is :func:`kyosei.config.store_home`'s
rule and nobody else's (law 3), so the app asks `kyo where` rather than carrying a
TypeScript copy of it. These tests hold the field to the rule both ways -- the
override and the default -- and in the one state where `where` has no project to
describe, because the registry is asked about before any project is chosen.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import config as config_module

from .conftest import invoke


def test_where_reports_the_kyosei_home_override(tmp_path, monkeypatch) -> None:
    home = tmp_path / "somewhere else" / "kyo-home"
    monkeypatch.setenv("KYOSEI_HOME", str(home))
    nowhere = tmp_path / "no project"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    result = invoke(["where", "--json"])
    assert result.code == 0
    assert result.json["store_home"] == str(home)
    assert result.json["configured"] is False


def test_where_reports_the_default_store_home_without_the_override(
    tmp_path, monkeypatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv("KYOSEI_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    nowhere = tmp_path / "no project"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    payload = invoke(["where", "--json"]).json
    assert payload["store_home"] == str(fake_home / config_module.STORE_HOME_DIRNAME)


def test_a_blank_override_falls_back_to_the_default(tmp_path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("KYOSEI_HOME", "   ")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    payload = invoke(["--root", str(tmp_path), "where", "--json"]).json
    assert payload["store_home"] == str(fake_home / "kyosei")


def test_where_reports_store_home_for_a_configured_project_too(
    git_project, store_home
) -> None:
    result = invoke(["--root", str(git_project), "where", "--json"])
    assert result.json["configured"] is True
    assert result.json["store_home"] == str(store_home)
    text = invoke(["--root", str(git_project), "where"]).out
    assert f"store home: {store_home}" in text
