"""KYO-714: `kyo statusline` saves Claude Code's usage limits, and manages its own entry.

Every test runs under the suite's autouse ``store_home`` fixture, which points
``$KYOSEI_HOME`` and ``$CLAUDE_CONFIG_DIR`` at the test's own temporary directory, so
neither the real store home nor the real ``~/.claude/settings.json`` is read or written.
The payloads follow the field table of https://code.claude.com/docs/en/statusline.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kyosei import cmd_statusline

from .conftest import invoke

RESETS = 1_999_999_999  # 2033: in the future for as long as this suite is run

DOCUMENTED = {
    "session_id": "abc123",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
    "workspace": {"current_dir": "/work"},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": RESETS},
        "seven_day": {"used_percentage": 41.2, "resets_at": RESETS + 3600},
    },
}


def statusline(payload: object, *flags: str):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return invoke(["statusline", *flags], stdin=text)


def limits_file(store_home: Path) -> Path:
    return store_home / cmd_statusline.LIMITS_FILE


def settings_file() -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"


# ------------------------------------------------------------------ c1: saving


def test_both_windows_are_saved_with_the_read_time_and_printed(store_home: Path) -> None:
    before = datetime.now(UTC)
    run = statusline(DOCUMENTED)
    assert run.code == 0, run
    assert run.out == "5-hour 24% · weekly 41%\n"
    saved = json.loads(limits_file(store_home).read_text(encoding="utf-8"))
    assert saved["five_hour"] == {"used_percentage": 23.5, "resets_at": RESETS}
    assert saved["seven_day"] == {"used_percentage": 41.2, "resets_at": RESETS + 3600}
    read_at = datetime.fromisoformat(saved["read_at"].replace("Z", "+00:00"))
    assert before - timedelta(seconds=1) <= read_at <= datetime.now(UTC)


def test_the_store_home_is_the_one_holding_device_tokens(store_home: Path) -> None:
    from kyosei import device_tokens

    assert cmd_statusline.limits_path().parent == device_tokens.tokens_path().parent


@pytest.mark.parametrize(
    ("window", "row"), [("five_hour", "5-hour 24%"), ("seven_day", "weekly 41%")]
)
def test_one_window_is_saved_and_printed_alone(store_home: Path, window: str, row: str) -> None:
    payload = {"rate_limits": {window: DOCUMENTED["rate_limits"][window]}}
    run = statusline(payload)
    assert run.code == 0 and run.out == f"{row}\n"
    saved = json.loads(limits_file(store_home).read_text(encoding="utf-8"))
    assert set(saved) == {"read_at", window}


def test_a_new_reading_replaces_the_last_whole(store_home: Path) -> None:
    statusline(DOCUMENTED)
    statusline({"rate_limits": {"seven_day": {"used_percentage": 50, "resets_at": RESETS}}})
    saved = json.loads(limits_file(store_home).read_text(encoding="utf-8"))
    assert "five_hour" not in saved and saved["seven_day"]["used_percentage"] == 50
    assert [p.name for p in store_home.iterdir()] == [cmd_statusline.LIMITS_FILE]


# ------------------------------------------------- c2: silent on everything else


def _save_first(store_home: Path) -> str:
    assert statusline(DOCUMENTED).code == 0
    return limits_file(store_home).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "abc123", "model": {"id": "claude-opus-5-5"}},
        {"rate_limits": {}},
        {"rate_limits": {"five_hour": {"used_percentage": "high", "resets_at": RESETS}}},
        {"rate_limits": {"five_hour": {"used_percentage": True, "resets_at": RESETS}}},
        "{not json",
        "",
        "[1, 2]",
        "\x00\x01",
    ],
)
def test_input_with_no_usable_window_prints_nothing_and_leaves_the_file(
    store_home: Path, payload: object
) -> None:
    before = _save_first(store_home)
    run = statusline(payload)
    assert (run.code, run.out, run.err) == (0, "", "")
    assert limits_file(store_home).read_text(encoding="utf-8") == before


def test_no_input_and_no_file_writes_nothing(store_home: Path) -> None:
    run = statusline({"session_id": "abc123"})
    assert (run.code, run.out, run.err) == (0, "", "")
    assert not limits_file(store_home).exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
def test_an_unwritable_store_home_prints_nothing_and_leaves_the_file(store_home: Path) -> None:
    before = _save_first(store_home)
    store_home.chmod(0o500)
    try:
        run = statusline({"rate_limits": {"five_hour": {"used_percentage": 99, "resets_at": 1}}})
    finally:
        store_home.chmod(0o700)
    assert (run.code, run.out, run.err) == (0, "", "")
    assert limits_file(store_home).read_text(encoding="utf-8") == before


def test_a_store_home_that_is_a_file_prints_nothing(tmp_path: Path, monkeypatch) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("KYOSEI_HOME", str(blocker))
    run = statusline(DOCUMENTED)
    assert (run.code, run.out, run.err) == (0, "", "")
    assert blocker.read_text(encoding="utf-8") == "x"


# -------------------------------------------------------- c3: the settings entry


def test_install_sets_the_status_line_and_keeps_every_other_key() -> None:
    path = settings_file()
    path.parent.mkdir(parents=True)
    others = {"model": "opus", "hooks": {"Stop": [{"hooks": []}]}, "effortLevel": "high"}
    path.write_text(json.dumps(others), encoding="utf-8")
    run = invoke(["statusline", "--install"])
    assert run.code == 0, run
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after == {**others, "statusLine": {"type": "command", "command": "kyo statusline"}}
    again = invoke(["statusline", "--install", "--json"])
    assert again.code == 0 and again.json["change"] == "already set"
    assert json.loads(path.read_text(encoding="utf-8")) == after


def test_install_creates_a_missing_settings_file() -> None:
    assert invoke(["statusline", "--install"]).code == 0
    assert json.loads(settings_file().read_text(encoding="utf-8")) == {
        "statusLine": cmd_statusline.STATUS_LINE
    }


def test_a_different_status_line_is_refused_and_named_and_the_remedy_clears_it() -> None:
    path = settings_file()
    path.parent.mkdir(parents=True)
    theirs = {"type": "command", "command": "~/.claude/statusline.sh", "padding": 0}
    path.write_text(json.dumps({"model": "opus", "statusLine": theirs}), encoding="utf-8")
    refused = invoke(["statusline", "--install"])
    assert refused.code != 0
    assert "~/.claude/statusline.sh" in refused.err and str(path) in refused.err
    assert "remove that `statusLine` entry" in refused.err
    assert json.loads(path.read_text(encoding="utf-8"))["statusLine"] == theirs
    # The remedy the refusal names: remove that entry, then run the install again.
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["statusLine"]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert "`kyo statusline --install`" in refused.err
    cleared = invoke(["statusline", "--install"])
    assert cleared.code == 0, cleared
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "model": "opus",
        "statusLine": cmd_statusline.STATUS_LINE,
    }


def test_uninstall_removes_only_its_own_entry() -> None:
    path = settings_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    assert invoke(["statusline", "--install"]).code == 0
    removed = invoke(["statusline", "--uninstall"])
    assert removed.code == 0, removed
    assert json.loads(path.read_text(encoding="utf-8")) == {"model": "opus"}

    theirs = {"model": "opus", "statusLine": {"type": "command", "command": "other"}}
    path.write_text(json.dumps(theirs), encoding="utf-8")
    left = invoke(["statusline", "--uninstall", "--json"])
    assert left.code == 0 and left.json["change"] == "left alone"
    assert json.loads(path.read_text(encoding="utf-8")) == theirs


@pytest.mark.parametrize("action", ["--install", "--uninstall"])
def test_print_shows_the_change_and_writes_nothing(action: str) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True)
    start = {"model": "opus"}
    if action == "--uninstall":
        start["statusLine"] = dict(cmd_statusline.STATUS_LINE)
    text = json.dumps(start)
    path.write_text(text, encoding="utf-8")
    run = invoke(["statusline", action, "--print", "--json"])
    assert run.code == 0, run
    assert run.json["written"] is False
    assert run.json["change"] == ("set" if action == "--install" else "removed")
    assert path.read_text(encoding="utf-8") == text


def test_print_without_install_names_the_form_that_works() -> None:
    refused = invoke(["statusline", "--print"])
    assert refused.code != 0 and "`kyo statusline --install --print`" in refused.err
    cleared = invoke(["statusline", "--install", "--print"])
    assert cleared.code == 0, cleared
    assert not settings_file().exists()


@pytest.mark.parametrize("text", ["{not json", "[1]"])
def test_unreadable_settings_are_refused_and_correcting_them_clears_it(text: str) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    refused = invoke(["statusline", "--install"])
    assert refused.code != 0 and "Correct the file and run the command again" in refused.err
    assert path.read_text(encoding="utf-8") == text
    path.write_text("{}", encoding="utf-8")
    assert invoke(["statusline", "--install"]).code == 0


# ------------------------------------------------------------- the reading half


def test_read_limits_states_iso_reset_times(store_home: Path) -> None:
    statusline(DOCUMENTED)
    limits = cmd_statusline.read_limits()
    assert limits is not None
    assert limits["five_hour"] == {
        "used_percentage": 23.5,
        "resets_at": "2033-05-18T03:33:19.000Z",
    }
    assert limits["seven_day"]["resets_at"] == "2033-05-18T04:33:19.000Z"
    assert isinstance(limits["read_at"], str) and limits["read_at"].endswith("Z")


def test_read_limits_leaves_out_a_window_that_has_reset(store_home: Path) -> None:
    statusline(DOCUMENTED)
    between = datetime.fromtimestamp(RESETS + 60, UTC)
    limits = cmd_statusline.read_limits(now=between)
    assert limits is not None and "five_hour" not in limits and "seven_day" in limits
    after = datetime.fromtimestamp(RESETS + 7200, UTC)
    assert cmd_statusline.read_limits(now=after) is None


def test_read_limits_is_none_with_no_file_or_an_unreadable_one(store_home: Path) -> None:
    assert cmd_statusline.read_limits() is None
    store_home.mkdir(parents=True, exist_ok=True)
    limits_file(store_home).write_text("{oops", encoding="utf-8")
    assert cmd_statusline.read_limits() is None
