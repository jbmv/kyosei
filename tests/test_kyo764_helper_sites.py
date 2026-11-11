"""KYO-764.c2: the refusals the remedy ledger found once it followed ``raise helper(...)``.

Each test provokes one of them and shows the remedy it names clears it, so the ledger's
entry for the site can cite a proof. The sites are listed against their tests in
``tests/test_kyo5_error_remedies.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import device_tokens, home_settings, serve_install

from .conftest import invoke

# ---------------------------------------------------------------- home_settings.load

BAD_SETTINGS = [
    ("controller_command: [claude\n", "is not YAML"),
    ("- claude\n", "not a mapping of settings"),
    ("surprise: 1\n", "has a key this build does not know"),
    ("controller_command: claude\n", "not a non-empty list of non-empty strings"),
]


@pytest.mark.parametrize(("text", "says"), BAD_SETTINGS)
def test_a_malformed_machine_settings_file_is_refused_and_removing_it_clears_it(
    tmp_path: Path, text: str, says: str
) -> None:
    environ = {"KYOSEI_HOME": str(tmp_path)}
    path = tmp_path / home_settings.SETTINGS_FILE
    path.write_text(text, encoding="utf-8")
    with pytest.raises(Exception) as caught:
        home_settings.load(environ)
    assert says in str(caught.value)
    assert "remove the file" in str(caught.value)
    path.unlink()
    assert home_settings.load(environ).source == "default"


def test_an_unreadable_machine_settings_file_is_refused_and_removing_it_clears_it(
    tmp_path: Path,
) -> None:
    environ = {"KYOSEI_HOME": str(tmp_path)}
    path = tmp_path / home_settings.SETTINGS_FILE
    path.mkdir()  # reading a directory as text is an OSError, the file's other failure
    with pytest.raises(Exception) as caught:
        home_settings.load(environ)
    assert "could not be read" in str(caught.value)
    path.rmdir()
    assert home_settings.load(environ).source == "default"


# ---------------------------------------------------------- device_tokens._device


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        (3, "devices[0] is not an object"),
        ({"id": "x"}, "devices[0] is not a complete entry"),
    ],
)
def test_a_malformed_device_entry_is_refused_and_issuing_afresh_clears_it(
    store_home: Path, entry: object, says: str
) -> None:
    path = store_home / device_tokens.TOKENS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"devices": [entry]}), encoding="utf-8")
    refused = invoke(["devices", "list"])
    assert refused.code != 0 and says in refused.err
    assert "kyo devices issue" in refused.err
    path.rename(path.with_name("device-tokens.moved-aside"))
    assert invoke(["devices", "issue", "ipad", "--permission", "send"]).code == 0
    assert invoke(["devices", "list"]).code == 0


# ----------------------------------------------- serve_install._unsupported


@pytest.mark.parametrize("action", ["install", "uninstall", "restart"])
def test_another_platform_is_refused_for_every_service_action(monkeypatch, action: str) -> None:
    monkeypatch.setattr(serve_install, "platform_manager", lambda: None)
    run = invoke(["serve", f"--{action}"])
    assert run.code == 1 and "run `kyo serve` in the foreground" in run.err
