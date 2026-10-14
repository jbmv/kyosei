"""KYO-519: landing 1 of the controller -> orchestrator rename (KYO-513).

The build reads both spellings and says the new word. Each alias here is permanent, so
each has a test that fails if the alias is removed:

* `kyo orchestrator` is the command and `kyo controller` an argparse alias of it, in the
  command table and serve's never-served list; its JSON says `"command": "orchestrator"`
  whichever spelling ran.
* New writes default the actor to `orchestrator`.
* `orchestrator_only_commands` / `controller_only_commands` and `orchestrator_command` /
  `controller_command` are one key each under two spellings; both with different values
  are refused by name; the old project-config spelling is a doctor notice naming the
  hand rename and its precondition; `kyo rebuild` keeps the old key.
* `## Orchestrator-audience rules` and `## Controller-audience rules` are one section, and
  `orchestrator` is an audience token read with `controller`.
* `kyo brief --json` says `orchestrator_rules`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kyosei import cli, config, home_settings, procedures, serve
from kyosei.cmd_controller import cmd_controller

from .conftest import invoke, new_record
from .test_kyo_controller import bin_dir, env, socket_dir  # noqa: F401 - fixtures


def _subparsers() -> dict:
    parser = cli.build_parser()
    action = next(a for a in parser._actions if isinstance(a.choices, dict) and a.choices)
    return dict(action.choices)


# -------------------------------------------------------------------------- the command


def test_controller_is_an_alias_of_orchestrator_everywhere_the_command_is_looked_up() -> None:
    choices = _subparsers()
    assert "controller" in cli.ORCHESTRATOR_COMMAND_ALIASES
    # The parser: one subparser under both names.
    assert choices["controller"] is choices["orchestrator"]
    # The command table: both spellings dispatch to the one handler.
    assert cli.COMMAND_TABLE["orchestrator"][0] is cmd_controller
    assert cli.COMMAND_TABLE["controller"][0] is cmd_controller
    # serve's never-served list: neither spelling is reachable over HTTP.
    assert "orchestrator" in serve.NOT_SERVED and "controller" in serve.NOT_SERVED
    assert "orchestrator" not in serve.SERVED_COMMANDS
    assert "controller" not in serve.SERVED_COMMANDS


@pytest.mark.parametrize("spelling", ["orchestrator", "controller"])
def test_the_json_names_orchestrator_whichever_spelling_ran(env, spelling) -> None:  # noqa: F811
    run = invoke([spelling, "list", "--json"])
    assert run.code == 0, run
    assert run.json["command"] == "orchestrator"
    assert run.json["action"] == "list"


def test_the_reference_documents_the_command_once_and_names_the_alias() -> None:
    text = cli.reference_markdown()
    assert "## `kyo orchestrator`" in text
    assert "## `kyo controller`" not in text
    assert "also accepted as `kyo controller`" in text
    # The old spelling still finds the one section.
    assert cli.reference_markdown(command="controller").startswith("## `kyo orchestrator`")
    assert "controller" not in cli.reference_command_names()


# ----------------------------------------------------------------------------- actor


def test_a_new_write_defaults_its_actor_to_orchestrator(kyo) -> None:
    rid = new_record(kyo)
    assert kyo("show", rid, "--json").code == 0
    export = kyo.project / "kyosei" / "export" / "_events"
    actors = {
        json.loads(line)["actor"]
        for path in export.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert actors == {"orchestrator"}


def test_the_actor_help_says_a_query_must_match_both_values() -> None:
    help_text = _subparsers()["new"].format_help()
    assert "'orchestrator'" in help_text and "'controller'" in help_text


# --------------------------------------------------------------- the project config key


def _config_path(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def _set_keys(project: Path, **keys: object) -> None:
    path = _config_path(project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.update(keys)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _drop_key(project: Path, key: str) -> None:
    path = _config_path(project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop(key)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("key", [config.ORCHESTRATOR_ONLY_KEY, config.CONTROLLER_ONLY_KEY])
def test_either_spelling_of_the_whole_suite_key_is_read(project: Path, key: str) -> None:
    _set_keys(project, **{key: ["make test"]})
    loaded = config.load_config(project)
    assert loaded.controller_only_commands == ("make test",)
    assert loaded.orchestrator_only_key == key


def test_both_spellings_with_the_same_commands_are_accepted(project: Path) -> None:
    _set_keys(
        project,
        orchestrator_only_commands=["make test"],
        controller_only_commands=["make test"],
    )
    assert config.load_config(project).controller_only_commands == ("make test",)


def test_both_spellings_with_different_commands_are_refused_by_name_and_the_remedy_clears_it(
    kyo,
) -> None:
    _set_keys(
        kyo.project,
        orchestrator_only_commands=["make test"],
        controller_only_commands=["npm run check"],
    )
    run = kyo("list")
    assert run.code != 0
    assert "orchestrator_only_commands" in run.err and "controller_only_commands" in run.err
    assert "keep one of them" in run.err
    # The remedy: keep one of them.
    _drop_key(kyo.project, "orchestrator_only_commands")
    assert kyo("list").code == 0, kyo("list")


def _spelling_notices(kyo) -> list[str]:
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    findings = result.json["checks"]["orchestrator_only_setup"]["failures"]
    return [f for f in findings if "rename" in f]


def test_the_old_spelling_is_a_notice_naming_the_hand_rename_and_its_precondition(kyo) -> None:
    _set_keys(kyo.project, controller_only_commands=["make test"])
    notices = _spelling_notices(kyo)
    assert len(notices) == 1
    text = notices[0]
    assert "controller_only_commands" in text and "orchestrator_only_commands" in text
    assert "by hand" in text and "at least KYO-519" in text and "path_build_lag" in text
    # The remedy it names clears it: rename the key by hand.
    _drop_key(kyo.project, "controller_only_commands")
    _set_keys(kyo.project, orchestrator_only_commands=["make test"])
    assert _spelling_notices(kyo) == []


def test_the_notice_is_a_notice_and_never_fails_doctor(kyo) -> None:
    _set_keys(kyo.project, controller_only_commands=["make test"])
    assert kyo("doctor").code == 0


def test_rebuild_keeps_either_spelling(kyo) -> None:
    for key in (config.CONTROLLER_ONLY_KEY, config.ORCHESTRATOR_ONLY_KEY):
        other = (
            config.ORCHESTRATOR_ONLY_KEY
            if key == config.CONTROLLER_ONLY_KEY
            else config.CONTROLLER_ONLY_KEY
        )
        raw = yaml.safe_load(_config_path(kyo.project).read_text(encoding="utf-8"))
        raw.pop(other, None)
        raw[key] = ["make test"]
        _config_path(kyo.project).write_text(
            yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
        )
        assert kyo("rebuild", "--force").code == 0
        after = yaml.safe_load(_config_path(kyo.project).read_text(encoding="utf-8"))
        assert after.get(key) == ["make test"], key


def test_the_guard_refuses_under_the_new_spelling(kyo) -> None:
    _set_keys(kyo.project, orchestrator_only_commands=["make test"])
    from kyosei.cmd_check_command import controller_only_matches

    declared = config.load_config(kyo.project).controller_only_commands
    assert controller_only_matches("make test", declared, cwd=None)


# ------------------------------------------------------------- the machine settings key


def _settings(tmp_path: Path, text: str) -> dict:
    home = tmp_path / "home-settings"
    home.mkdir(exist_ok=True)
    (home / home_settings.SETTINGS_FILE).write_text(text, encoding="utf-8")
    return {"KYOSEI_HOME": str(home)}


@pytest.mark.parametrize(
    "key", [home_settings.ORCHESTRATOR_COMMAND_KEY, home_settings.CONTROLLER_COMMAND_KEY]
)
def test_either_spelling_of_the_machine_command_is_read(tmp_path: Path, key: str) -> None:
    environ = _settings(tmp_path, f'{key}: ["claude", "--remote-control", "{{prefix}}"]\n')
    loaded = home_settings.load(environ)
    assert loaded.source == "file"
    assert loaded.controller_argv("KYO") == ["claude", "--remote-control", "KYO"]


def test_both_machine_spellings_with_different_values_are_refused_and_the_remedy_clears_it(
    tmp_path: Path,
) -> None:
    environ = _settings(
        tmp_path, 'orchestrator_command: ["claude"]\ncontroller_command: ["claude", "-c"]\n'
    )
    with pytest.raises(Exception) as caught:
        home_settings.load(environ)
    message = str(caught.value)
    assert "orchestrator_command" in message and "controller_command" in message
    assert "keep one of them" in message
    environ = _settings(tmp_path, 'orchestrator_command: ["claude"]\n')
    assert home_settings.load(environ).controller_command == ("claude",)


# -------------------------------------------------------------- the procedures ledger

_LEDGER = """# Procedures

## {heading}

### Rule A

- **audience**: {token}
- **enforced-by**: aspirational

A rule.
"""


def test_orchestrator_is_a_known_audience_selected_with_controller() -> None:
    assert {"orchestrator", "controller"} <= procedures.KNOWN_AUDIENCES
    for token in ("orchestrator", "controller"):
        text = _LEDGER.format(heading="Orchestrator-audience rules", token=token)
        assert [r.title for r in procedures.orchestrator_rules(text)] == ["Rule A"]
        assert procedures.unknown_audiences(text) == []


@pytest.mark.parametrize(
    ("store_heading", "packaged_heading"),
    [
        ("Orchestrator-audience rules", "Controller-audience rules"),
        ("Controller-audience rules", "Orchestrator-audience rules"),
    ],
)
def test_the_two_headings_are_one_section_so_no_second_section_is_appended(
    store_heading: str, packaged_heading: str
) -> None:
    packaged = _LEDGER.format(heading=packaged_heading, token="controller")
    store = f"# Procedures\n\n## {store_heading}\n\nA project's own words.\n"
    merged, added = procedures.merge_ledger(packaged, store)
    assert added == ["Rule A"]
    assert merged.count("-audience rules") == 1, merged
    assert merged.index("### Rule A") > merged.index(f"## {store_heading}")


def test_install_procedures_appends_no_second_section_under_the_new_heading(kyo) -> None:
    """Since KYO-524 the packaged ledger says the new heading, so the case that matters
    is a project still under the OLD one that has none of the section's rules: they land
    in its existing section, and no second section is appended."""
    path = kyo.project / "kyosei" / "procedures.md"
    text = path.read_text(encoding="utf-8")
    assert "## Orchestrator-audience rules" in text
    head, rest = text.split("## Orchestrator-audience rules", 1)
    tail_heading = rest.find("\n## ")
    tail = rest[tail_heading:] if tail_heading != -1 else ""
    path.write_text(
        head + "## Controller-audience rules\n\nOur own words.\n" + tail, encoding="utf-8"
    )
    assert kyo("install-procedures").code == 0
    after = path.read_text(encoding="utf-8")
    assert after.count("## Controller-audience rules") == 1
    assert "## Orchestrator-audience rules" not in after
    assert procedures.orchestrator_rules(after)


# --------------------------------------------------------------------- the brief key


def test_the_brief_json_says_orchestrator_rules(kyo) -> None:
    data = kyo("brief", "--json").json
    assert "orchestrator_rules" in data and "controller_rules" not in data
    assert data["orchestrator_rules"]
