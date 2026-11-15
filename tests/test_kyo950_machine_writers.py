"""KYO-955: the writers of ``serve_idle_message`` and of an agent type's model and effort.

The fourth build of proposal KYO-950 (section 5.3, the two rows for these settings):
``kyo config --machine set serve_idle_message true|false`` splices ``settings.yml`` and
``kyo install-agents --type T=MODEL[:EFFORT] --in-place`` splices two lines of an existing
agent type's front matter. Both write atomically, take an optional expected etag, and log
each attempt, which ``kyo config`` reports as ``last_change``.

Every file here lies in a temporary directory; ``$KYOSEI_HOME`` is the suite's own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from kyosei import cmd_install_agents, config_splice, home_settings
from kyosei.errors import KyoseiError

from .conftest import invoke

SETTINGS = """\
# The machine's settings.
# controller_command was renamed orchestrator_command (KYO-519).
controller_command: ["claude", "--remote-control", "{prefix}"]  # this machine's kyo is old

serve_send: true  # sending is on
# serve_idle_message: false

# kept
"""


@pytest.fixture
def settings(store_home: Path) -> Path:
    store_home.mkdir(parents=True, exist_ok=True)
    path = home_settings.settings_path({"KYOSEI_HOME": str(store_home)})
    path.write_text(SETTINGS, encoding="utf-8")
    return path


def machine(*args: str):
    return invoke(["config", "--machine", *args])


def log_rows() -> list[dict]:
    return config_splice.read_log(os.environ)


def differing(before: str, after: str) -> tuple[list[str], list[str]]:
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    return [x for x in old if x not in new], [x for x in new if x not in old]


# ------------------------------------------------------------------ c1, c2: settings.yml


def test_set_changes_that_one_line_and_nothing_else(settings: Path) -> None:
    run = machine("set", "serve_idle_message", "true")
    assert run.code == 0, run
    after = settings.read_text(encoding="utf-8")
    assert differing(SETTINGS, after) == (
        ["# serve_idle_message: false\n"],
        ["serve_idle_message: true\n"],
    )
    loaded = home_settings.load({"KYOSEI_HOME": str(settings.parent)})
    assert loaded.serve_idle_message is True and loaded.serve_send is True
    # The command key keeps the spelling the file uses.
    assert "\ncontroller_command: [" in after and "\norchestrator_command:" not in after
    assert loaded.controller_command == ("claude", "--remote-control", "{prefix}")


def test_set_flips_a_present_line_keeping_its_trailing_comment(settings: Path) -> None:
    settings.write_text(SETTINGS.replace("# serve_idle_message: false\n",
                                         "serve_idle_message: true  # trial\n"))
    assert machine("set", "serve_idle_message", "false").code == 0
    after = settings.read_text(encoding="utf-8")
    assert "serve_idle_message: false  # trial\n" in after
    assert differing(SETTINGS.replace("# serve_idle_message: false\n",
                                      "serve_idle_message: true  # trial\n"), after)[0] == [
        "serve_idle_message: true  # trial\n"
    ]


def test_set_to_the_value_already_held_writes_nothing(settings: Path) -> None:
    settings.write_text(SETTINGS + "serve_idle_message: true\n")
    before = settings.read_bytes()
    run = machine("set", "serve_idle_message", "true", "--json")
    assert run.code == 0 and json.loads(run.out)["written"] is False
    assert settings.read_bytes() == before


def test_the_candidate_is_validated_by_the_parser_the_reader_uses(
    settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = home_settings.parse

    def spy(text: str, path: Path):
        seen.append(text)
        return real(text, path)

    monkeypatch.setattr(home_settings, "parse", spy)
    assert machine("set", "serve_idle_message", "true").code == 0
    assert seen == [settings.read_text(encoding="utf-8")]
    # load() reads through the same function.
    seen.clear()
    home_settings.load({"KYOSEI_HOME": str(settings.parent)})
    assert len(seen) == 1


def test_a_file_the_reader_refuses_is_not_written(settings: Path) -> None:
    broken = SETTINGS + "serve_idle_message: yes please\nsurprise: 1\n"
    settings.write_text(broken)
    run = machine("set", "serve_idle_message", "true")
    assert run.code != 0
    assert settings.read_text() == broken


def test_a_missing_settings_file_is_refused_and_the_remedy_clears_it(store_home: Path) -> None:
    path = home_settings.settings_path({"KYOSEI_HOME": str(store_home)})
    assert not path.exists()
    run = machine("set", "serve_idle_message", "true")
    assert run.code != 0 and "does not create it" in run.err and str(path) in run.err
    assert not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")  # the remedy: an empty file, by hand
    assert machine("set", "serve_idle_message", "true").code == 0
    assert home_settings.load({"KYOSEI_HOME": str(store_home)}).serve_idle_message is True


@pytest.mark.parametrize("key", ["serve_send", "orchestrator_command", "controller_command"])
def test_the_keys_set_by_hand_are_refused_naming_that(settings: Path, key: str) -> None:
    run = machine("set", key, "true")
    assert run.code != 0 and "set at the machine by hand" in run.err and key in run.err
    assert settings.read_text() == SETTINGS


def test_another_key_and_a_value_that_is_not_a_boolean_are_refused(settings: Path) -> None:
    other = machine("set", "no_such_key", "true")
    assert other.code != 0 and "serve_idle_message" in other.err
    value = machine("set", "serve_idle_message", "maybe")
    assert value.code != 0 and "`true` or `false`" in value.err
    assert machine("unset", "serve_idle_message").code != 0
    assert settings.read_text() == SETTINGS


def test_the_terminal_allows_it_on_while_serve_send_is_off(settings: Path) -> None:
    settings.write_text(SETTINGS.replace("serve_send: true", "serve_send: false"))
    assert machine("set", "serve_idle_message", "true").code == 0


# ------------------------------------------------------------------ c5: etag


def test_machine_writer_refuses_a_different_etag_and_the_current_one_clears_it(
    settings: Path,
) -> None:
    stale = "sha256:" + "0" * 64
    run = machine("set", "serve_idle_message", "true", "--if-match", stale)
    assert run.code != 0 and "changed since it was read" in run.err
    assert settings.read_text() == SETTINGS
    current = config_splice.etag_of(settings.read_bytes())
    assert machine("set", "serve_idle_message", "true", "--if-match", current).code == 0


# ------------------------------------------------------------------ c6: the log


def test_writes_and_refusals_are_rows_and_the_machine_document_reports_the_last(
    settings: Path,
) -> None:
    machine("set", "serve_idle_message", "true")
    machine("set", "serve_send", "false")
    machine("set", "serve_idle_message", "false")
    rows = log_rows()
    assert [(r["outcome"], r["key"]) for r in rows] == [
        ("written", "serve_idle_message"),
        ("refused", "serve_send"),
        ("written", "serve_idle_message"),
    ]
    assert rows[0]["file"] == str(settings) and rows[2]["previous"] is True
    document = json.loads(machine("--json").out)
    row = next(r for r in document["settings"] if r["key"] == "serve_idle_message")
    change = row["last_change"]
    assert (change["previous"], change["new"], change["device"]) == (True, False, "cli")
    assert next(r for r in document["settings"] if r["key"] == "serve_send")["last_change"] is None


def test_a_missing_file_refusal_is_a_row(store_home: Path) -> None:
    machine("set", "serve_idle_message", "true")
    assert [r["outcome"] for r in log_rows()] == ["refused"]


# ------------------------------------------------------------------ c3, c4: agent types


def agents_dir(project: Path) -> Path:
    return project / ".claude" / "agents"


def installed(project: Path) -> Path:
    run = invoke(
        ["--root", str(project), "install-agents", "--type", "worker=sonnet:medium",
         "--type", "designer=opus:high"]
    )
    assert run.code == 0, run
    return agents_dir(project)


def in_place(project: Path, *args: str):
    return invoke(["--root", str(project), "install-agents", "--in-place", *args])


def test_in_place_changes_the_two_lines_and_leaves_the_body_alone(project: Path) -> None:
    path = installed(project) / "kyosei-worker.md"
    # The project has edited the description and the body since it was written.
    edited = path.read_text().replace("A dispatched", "An edited, dispatched")
    edited += "\nAn added line.\n"
    path.write_text(edited)
    before_body = edited.split("---", 2)[2]
    run = in_place(project, "--type", "worker=claude-opus-5-5:high")
    assert run.code == 0, run
    after = path.read_text()
    assert after.split("---", 2)[2] == before_body
    assert differing(edited, after) == (
        ["model: sonnet\n", "effort: medium\n"],
        ["model: claude-opus-5-5\n", "effort: high\n"],
    )
    meta = yaml.safe_load(after.split("---", 2)[1])
    assert (meta["name"], meta["model"], meta["effort"]) == (
        "kyosei-worker",
        "claude-opus-5-5",
        "high",
    )
    # The other type is untouched.
    other = (agents_dir(project) / "kyosei-designer.md").read_text().split("---", 2)[1]
    assert yaml.safe_load(other)["model"] == "opus"


def test_in_place_keeps_a_files_line_endings_and_a_trailing_comment(project: Path) -> None:
    path = installed(project) / "kyosei-worker.md"
    crlf = path.read_text().replace("model: sonnet\n", "model:   sonnet  # chosen\n").replace(
        "\n", "\r\n"
    )
    path.write_bytes(crlf.encode())
    assert in_place(project, "--type", "worker=haiku:low").code == 0
    after = path.read_bytes().decode()
    assert "model:   haiku  # chosen\r\n" in after and "effort: low\r\n" in after
    assert after.count("\r\n") == crlf.count("\r\n") and after.count("\n") == after.count("\r\n")
    assert differing(crlf, after)[0] == ["model:   sonnet  # chosen\r\n", "effort: medium\r\n"]


def test_in_place_with_no_effort_names_the_templates_default(project: Path) -> None:
    path = installed(project) / "kyosei-designer.md"
    assert in_place(project, "--type", "designer=sonnet").code == 0
    assert "effort: high\n" in path.read_text()  # DEFAULT_EFFORT["designer"]


def test_in_place_refuses_a_missing_file_and_the_remedy_clears_it(project: Path) -> None:
    run = in_place(project, "--type", "worker=sonnet")
    assert run.code != 0 and "does not exist" in run.err and "without it" in run.err
    assert not (agents_dir(project) / "kyosei-worker.md").exists()
    assert invoke(["--root", str(project), "install-agents", "--type", "worker=sonnet"]).code == 0
    assert in_place(project, "--type", "worker=haiku").code == 0


@pytest.mark.parametrize(
    "mutate,named",
    [
        (lambda t: t.split("---", 2)[2], "no front matter"),
        (lambda t: t.replace("name: kyosei-worker", "name: other"), "not 'kyosei-worker'"),
        (lambda t: t.replace("effort: medium\n", ""), "no `effort:` line"),
        (lambda t: t.replace("\n---\n", "\n", 1), "never closes"),
    ],
    ids=["no-front-matter", "other-name", "no-effort-line", "unclosed"],
)
def test_in_place_refuses_a_file_that_is_not_the_templates(
    project: Path, mutate, named: str
) -> None:
    path = installed(project) / "kyosei-worker.md"
    path.write_text(mutate(path.read_text()))
    before = path.read_bytes()
    run = in_place(project, "--type", "worker=haiku:low")
    assert run.code != 0 and named in run.err
    assert path.read_bytes() == before


def test_the_remedy_for_a_file_without_front_matter_clears_it(project: Path) -> None:
    path = installed(project) / "kyosei-worker.md"
    path.write_text("just a body\n")
    assert in_place(project, "--type", "worker=haiku:low").code != 0
    assert invoke(
        ["--root", str(project), "install-agents", "--type", "worker=haiku:low", "--force"]
    ).code == 0
    assert in_place(project, "--type", "worker=opus:high").code == 0


@pytest.mark.parametrize(
    "spec", ["worker=-bad", "worker=bad model", "worker=:high"]
)
def test_in_place_refuses_a_model_or_effort_outside_the_patterns(
    project: Path, spec: str
) -> None:
    path = installed(project) / "kyosei-worker.md"
    before = path.read_bytes()
    assert in_place(project, "--type", spec).code != 0
    assert path.read_bytes() == before


def test_the_front_matter_editor_refuses_what_the_option_parser_would_let_by(
    project: Path,
) -> None:
    text = installed(project).joinpath("kyosei-worker.md").read_text()
    p = Path("x.md")
    with pytest.raises(KyoseiError, match="model id"):
        cmd_install_agents.edit_front_matter(text, "worker", "bad model", "high", p)
    with pytest.raises(KyoseiError, match="effort level"):
        cmd_install_agents.edit_front_matter(text, "worker", "opus", "ultra", p)


def test_a_refused_file_leaves_every_file_unwritten(project: Path) -> None:
    directory = installed(project)
    worker, designer = directory / "kyosei-worker.md", directory / "kyosei-designer.md"
    designer.write_text(designer.read_text().replace("name: kyosei-designer", "name: x"))
    before = (worker.read_bytes(), designer.read_bytes())
    run = in_place(project, "--type", "worker=haiku:low", "--type", "designer=haiku:low")
    assert run.code != 0
    assert (worker.read_bytes(), designer.read_bytes()) == before


def test_in_place_is_not_combined_with_force_and_print_writes_nothing(project: Path) -> None:
    path = installed(project) / "kyosei-worker.md"
    before = path.read_bytes()
    assert in_place(project, "--type", "worker=haiku", "--force").code != 0
    printed = in_place(project, "--type", "worker=haiku:low", "--print")
    assert printed.code == 0 and "would change" in printed.out
    assert path.read_bytes() == before


# ------------------------------------------------------------------ c5, c6 for agent types


def test_in_place_refuses_a_different_etag_and_the_current_one_clears_it(
    project: Path,
) -> None:
    path = installed(project) / "kyosei-worker.md"
    run = in_place(project, "--type", "worker=haiku:low", "--if-match", "sha256:" + "0" * 64)
    assert run.code != 0 and "changed since it was read" in run.err
    current = config_splice.etag_of(path.read_bytes())
    assert in_place(project, "--type", "worker=haiku:low", "--if-match", current).code == 0
    # An etag is one file's digest.
    two = in_place(project, "--type", "worker=haiku", "--type", "designer=haiku",
                   "--if-match", current)
    assert two.code != 0 and "one --type" in two.err


def test_in_place_etag_is_checked_again_before_the_rename(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = installed(project) / "kyosei-worker.md"
    real = cmd_install_agents.edit_front_matter

    def racing(text, template, model, effort, p):
        result = real(text, template, model, effort, p)
        path.write_bytes(path.read_bytes() + b"x")  # another writer, after the read
        return result

    monkeypatch.setattr(cmd_install_agents, "edit_front_matter", racing)
    # The pre-check pass edits once too; the write's own pass is the one that races.
    run = in_place(project, "--type", "worker=haiku:low")
    assert run.code != 0 and "changed while the change was being prepared" in run.err


def test_each_in_place_write_and_refusal_is_a_row_and_the_project_document_reports_it(
    project: Path,
) -> None:
    path = installed(project) / "kyosei-worker.md"
    assert in_place(project, "--type", "worker=haiku:low").code == 0
    path.write_text(path.read_text().replace("name: kyosei-worker", "name: x"))
    assert in_place(project, "--type", "worker=opus:high").code != 0
    rows = log_rows()
    assert [r["outcome"] for r in rows] == ["written", "refused"]
    assert rows[0]["file"] == str(path) and rows[0]["key"] == "kyosei-worker"
    assert rows[0]["previous"] == {"model": "sonnet", "effort": "medium"}
    assert rows[0]["new"] == {"model": "haiku", "effort": "low"}
    assert "not 'kyosei-worker'" in rows[1]["reason"]
    document = json.loads(invoke(["--root", str(project), "config", "--json"]).out)
    worker = next(a for a in document["agent_types"] if a["name"] == "kyosei-worker")
    assert worker["last_change"]["new"] == {"model": "haiku", "effort": "low"}
    assert worker["last_change"]["device"] == "cli"
    designer = next(a for a in document["agent_types"] if a["name"] == "kyosei-designer")
    assert designer["last_change"] is None


def test_a_write_that_cannot_be_logged_does_not_happen(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = installed(project) / "kyosei-worker.md"
    before = path.read_bytes()
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    monkeypatch.setenv("KYOSEI_HOME", str(repository / "home"))
    run = in_place(project, "--type", "worker=haiku:low")
    assert run.code != 0 and "cannot be used" in run.err
    assert path.read_bytes() == before
