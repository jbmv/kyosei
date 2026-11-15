"""KYO-954: the comment-keeping writers of the editable project settings.

The third build of proposal KYO-950 (sections 5.1, 5.2, 5.5 and the CLI column of 5.3):
:func:`kyosei.config_splice.splice` applies four rules and changes one line; ``kyo config
set`` and ``unset``, ``kyo prior-art-setting`` and ``kyo docs coverage --level`` write
through it atomically; every attempt is a row of ``$KYOSEI_HOME/config-log.jsonl``; the
brief states each change since the previous brief.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from kyosei import config as config_module
from kyosei import config_splice, config_view, export
from kyosei.config import validate_config_text
from kyosei.errors import KyoseiError

from .conftest import invoke
from .test_kyo342_worktree_store import _from, worktree_project  # noqa: F401

PATH = Path("/proj/kyosei/config.yml")

#: A file carrying comments above and beside keys, a commented example of a key and a
#: trailing comment, the shapes of section 5.1.
FIXTURE = """\
# Kyosei project configuration.
#
# schema_version: read by x
schema_version: 40

# project_prefix: read by y
project_prefix: TST  # the id prefix, never changed

export_dir: export

# worker_model: read by cmd_dispatch.resolve_worker_model
# The model workers run on; decided 2026-09-01.
worker_model: claude-sonnet-5-5  # sonnet for settled work

# KYO-312. Uncomment to declare the longest timeout.
#
# worker_command_timeout_ceiling_seconds: 240

# read_transcripts: read by cmd
read_transcripts: false

docs_coverage:
  # the level
  level: notice
  code:
    - src/**/*.py
"""


def check(text: str) -> None:
    validate_config_text(text, PATH)


def lines_changed(before: str, after: str) -> tuple[list[str], list[str]]:
    """The lines only ``before`` has and the lines only ``after`` has: what lies between
    their common first lines and their common last lines."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while (
        tail < min(len(old), len(new)) - head
        and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]
    ):
        tail += 1
    return old[head : len(old) - tail], new[head : len(new) - tail]


# ------------------------------------------------------------------ c1: the four rules


def test_rule_1_replaces_the_value_and_keeps_the_trailing_comment() -> None:
    change = config_splice.Change("worker_model", "claude-opus-5-5")
    after = config_splice.splice(FIXTURE, change, validate=check, path=PATH)
    removed, added = lines_changed(FIXTURE, after)
    assert removed == ["worker_model: claude-sonnet-5-5  # sonnet for settled work\n"]
    assert added == ["worker_model: claude-opus-5-5  # sonnet for settled work\n"]


def test_rule_1_on_a_nested_key_changes_that_line_only() -> None:
    change = config_splice.Change("docs_coverage.level", "problem")
    after = config_splice.splice(FIXTURE, change, validate=check, path=PATH)
    assert lines_changed(FIXTURE, after) == (["  level: notice\n"], ["  level: problem\n"])


def test_rule_2_turns_the_commented_example_into_the_live_line() -> None:
    change = config_splice.Change("worker_command_timeout_ceiling_seconds", 300)
    after = config_splice.splice(FIXTURE, change, validate=check, path=PATH)
    assert lines_changed(FIXTURE, after) == (
        ["# worker_command_timeout_ceiling_seconds: 240\n"],
        ["worker_command_timeout_ceiling_seconds: 300\n"],
    )


def test_rule_3_appends_under_the_generated_comment_after_a_blank_line() -> None:
    bare = FIXTURE.replace("# worker_command_timeout_ceiling_seconds: 240\n", "")
    change = config_splice.Change("trunk", "develop")
    after = config_splice.splice(bare, change, validate=check, path=PATH)
    assert after.startswith(bare)
    tail = after[len(bare) :]
    assert tail.splitlines()[0] == ""
    assert tail.splitlines()[1].startswith("# trunk: read by ")
    assert tail.splitlines()[2] == "trunk: develop"
    assert len(tail.splitlines()) == 3


def test_rule_3_nested_adds_only_the_levels_that_are_missing() -> None:
    with_recx = FIXTURE + "\n# recx: read by x\nrecx:\n  prior_art:  # the trigger\n    limit: 3\n"
    change = config_splice.Change("recx.prior_art.state", "on", literal="on")
    after = config_splice.splice(with_recx, change, validate=lambda _: None, path=PATH)
    assert lines_changed(with_recx, after) == ([], ["    state: on\n"])
    assert yaml.safe_load(after)["recx"]["prior_art"] == {"limit": 3, "state": True}


def test_rule_4_removal_deletes_the_value_line_and_keeps_the_comments_above() -> None:
    change = config_splice.Change("worker_model", remove=True)
    after = config_splice.splice(FIXTURE, change, validate=check, path=PATH)
    assert lines_changed(FIXTURE, after) == (
        ["worker_model: claude-sonnet-5-5  # sonnet for settled work\n"],
        [],
    )
    assert "# The model workers run on; decided 2026-09-01." in after
    # The comments stay as they were, and a later write puts the line back (rule 3).
    back = config_splice.splice(
        after, config_splice.Change("worker_model", "claude-sonnet-5-5"), validate=check, path=PATH
    )
    assert back.startswith(after) and back.endswith("worker_model: claude-sonnet-5-5\n")


def test_removing_an_absent_key_changes_nothing() -> None:
    assert (
        config_splice.splice(
            FIXTURE, config_splice.Change("trunk", remove=True), validate=check, path=PATH
        )
        == FIXTURE
    )


@pytest.mark.parametrize(
    "change",
    [
        config_splice.Change("worker_model", "m-1"),
        config_splice.Change("worker_command_timeout_ceiling_seconds", 90),
        config_splice.Change("read_transcripts", True),
        config_splice.Change("docs_coverage.level", "off"),
        config_splice.Change("trunk", "main"),
        config_splice.Change("worker_model", remove=True),
        config_splice.Change("read_transcripts", remove=True),
    ],
    ids=lambda change: f"{change.key}-{'remove' if change.remove else change.value}",
)
def test_every_line_but_the_changed_one_is_byte_for_byte_the_same(
    change: config_splice.Change,
) -> None:
    after = config_splice.splice(FIXTURE, change, validate=check, path=PATH)
    removed, added = lines_changed(FIXTURE, after)
    # One line changed, added or removed, and a blank line and a generated comment with an
    # added key: everything else is the same lines in the same places.
    assert len(removed) <= 1 and len(added) <= 3
    old, new = FIXTURE.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(new) - len(old) == len(added) - len(removed)
    assert yaml.safe_load(after) != yaml.safe_load(FIXTURE)


# ------------------------------------------------------------------ c2: validated, refused


def test_every_candidate_is_validated_by_the_reader_before_it_is_returned() -> None:
    seen: list[str] = []

    def spy(candidate: str) -> None:
        seen.append(candidate)
        validate_config_text(candidate, PATH)

    after = config_splice.splice(
        FIXTURE, config_splice.Change("worker_model", "m-1"), validate=spy, path=PATH
    )
    assert seen == [after]
    # The reader's own refusal is the writer's: a ceiling of zero is not written.
    with pytest.raises(KyoseiError, match="worker_command_timeout_ceiling_seconds"):
        config_splice.splice(
            FIXTURE,
            config_splice.Change("worker_command_timeout_ceiling_seconds", 0),
            validate=check,
            path=PATH,
        )


def test_validate_config_text_is_the_body_load_config_applies(project: Path) -> None:
    path = config_module.config_path_for(project)
    broken = path.read_text(encoding="utf-8") + "\nworker_model: ''\n"
    with pytest.raises(KyoseiError) as from_text:
        validate_config_text(broken, path)
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(KyoseiError) as from_load:
        config_module.load_config(project)
    assert str(from_text.value) == str(from_load.value)


@pytest.mark.parametrize(
    "text,change,named",
    [
        # a key on more than one line
        (
            FIXTURE.replace("export_dir: export\n", "export_dir: export\nworker_model: a\n"),
            config_splice.Change("worker_model", "m-1"),
            "repeats the key",
        ),
        # a flow mapping
        (
            FIXTURE.replace(
                "docs_coverage:\n  # the level\n  level: notice\n  code:\n    - src/**/*.py\n",
                "docs_coverage: {level: notice}\n",
            ),
            config_splice.Change("docs_coverage.level", "problem"),
            "line ",
        ),
        # a value over more than one line
        (
            FIXTURE.replace(
                "worker_model: claude-sonnet-5-5  # sonnet for settled work\n",
                "worker_model: >\n  claude-sonnet-5-5\n",
            ),
            config_splice.Change("worker_model", "m-1"),
            "line ",
        ),
    ],
    ids=["duplicate-key", "flow-mapping", "multi-line-value"],
)
def test_a_file_the_splice_cannot_change_exactly_is_refused_naming_the_line(
    text: str, change: config_splice.Change, named: str
) -> None:
    with pytest.raises(KyoseiError) as raised:
        config_splice.splice(text, change, validate=lambda _: None, path=PATH)
    message = str(raised.value)
    assert named in message


def test_the_remedy_a_refusal_names_clears_it() -> None:
    flow = FIXTURE.replace(
        "docs_coverage:\n  # the level\n  level: notice\n  code:\n    - src/**/*.py\n",
        "docs_coverage: {level: notice}\n",
    )
    change = config_splice.Change("docs_coverage.level", "problem")
    with pytest.raises(KyoseiError, match="hand edit"):
        config_splice.splice(flow, change, validate=lambda _: None, path=PATH)
    hand_edited = flow.replace(
        "docs_coverage: {level: notice}\n", "docs_coverage:\n  level: notice\n"
    )
    assert "level: problem" in config_splice.splice(
        hand_edited, change, validate=lambda _: None, path=PATH
    )


# ------------------------------------------------------------------ c3, c4, c5: kyo config


def config_text(project: Path) -> str:
    return config_module.config_path_for(project).read_text(encoding="utf-8")


def set_value(project: Path, *args: str):
    return invoke(["--root", str(project), "config", *args])


def test_config_set_and_unset_write_the_three_keys_keeping_every_comment(project: Path) -> None:
    before = config_text(project)
    assert set_value(project, "set", "worker_model", "claude-opus-5-5").code == 0
    assert set_value(project, "set", "worker_command_timeout_ceiling_seconds", "240").code == 0
    assert set_value(project, "set", "read_transcripts", "true").code == 0
    after = config_text(project)
    values = yaml.safe_load(after)
    assert values["worker_model"] == "claude-opus-5-5"
    assert values["worker_command_timeout_ceiling_seconds"] == 240
    assert values["read_transcripts"] is True
    kept = [line for line in before.splitlines() if line.startswith("#")]
    assert all(
        line in after for line in kept if "worker" not in line and "read_transcripts" not in line
    )
    config_module.load_config(project)  # the reader opens it
    for key in ("worker_model", "worker_command_timeout_ceiling_seconds", "read_transcripts"):
        assert set_value(project, "unset", key).code == 0
    final = yaml.safe_load(config_text(project))
    assert not {"worker_model", "worker_command_timeout_ceiling_seconds", "read_transcripts"} & set(
        final
    )


def test_config_set_writes_with_the_atomic_write(project: Path, monkeypatch) -> None:
    calls: list[Path] = []
    real = export.atomic_write_text

    def spy(path: Path, text: str) -> None:
        calls.append(path)
        real(path, text)

    monkeypatch.setattr(export, "atomic_write_text", spy)
    assert set_value(project, "set", "worker_model", "claude-opus-5-5").code == 0
    assert calls == [config_module.config_path_for(project)]


def test_config_set_json_reports_the_change(project: Path) -> None:
    result = set_value(project, "set", "worker_model", "m-1", "--json")
    assert result.code == 0, result
    document = json.loads(result.out)
    assert (document["key"], document["previous"], document["value"], document["written"]) == (
        "worker_model",
        None,
        "m-1",
        True,
    )
    again = json.loads(set_value(project, "set", "worker_model", "m-1", "--json").out)
    assert again["written"] is False


def test_config_set_is_refused_in_a_dispatch_worktree_and_logged(
    worktree_project,  # noqa: F811
    store_home: Path,
) -> None:
    project, tree, _, _ = worktree_project
    before = config_text(project)
    refused = _from(tree, "config", "set", "worker_model", "m-1")
    assert refused.code != 0
    assert "linked git worktree" in refused.err and str(project) in refused.err
    assert config_text(project) == before
    rows = config_splice.read_log(os.environ)
    assert [row["outcome"] for row in rows] == ["refused"]


@pytest.mark.parametrize("bad", ["", "has space", ".leading", "a;b", "x" * 0 + "$(id)"])
def test_worker_model_outside_the_install_agents_pattern_is_refused(
    project: Path, bad: str
) -> None:
    before = config_text(project)
    result = set_value(project, "set", "worker_model", bad)
    assert result.code != 0
    assert config_text(project) == before
    assert config_splice.read_log(os.environ)[-1]["outcome"] == "refused"


def test_a_model_the_pattern_accepts_is_written(project: Path) -> None:
    assert set_value(project, "set", "worker_model", "us.anthropic.claude-x:0[1m]").code == 0


@pytest.mark.parametrize("value", ["0", "-1", "4m", "2.5", "true"])
def test_the_ceiling_refuses_what_the_reader_refuses(project: Path, value: str) -> None:
    before = config_text(project)
    assert set_value(project, "set", "worker_command_timeout_ceiling_seconds", value).code != 0
    assert config_text(project) == before


def test_a_key_that_is_not_editable_is_refused_naming_its_class(project: Path) -> None:
    before = config_text(project)
    shown = set_value(project, "set", "project_prefix", "ZZZ")
    assert shown.code != 0 and "shown only" in shown.err and "class: shown" in shown.err
    request = set_value(project, "set", "trunk", "develop")
    assert request.code != 0 and "class: request" in request.err
    assert "asking the session" in request.err
    other = set_value(project, "set", "docs_coverage.level", "off")
    assert other.code != 0 and "kyo docs coverage --level" in other.err
    unknown = set_value(project, "set", "no_such_key", "1")
    assert unknown.code != 0 and "kyo config" in unknown.err
    assert config_text(project) == before
    assert all(row["outcome"] == "refused" for row in config_splice.read_log(os.environ))


def test_a_key_that_adds_what_the_path_build_cannot_read_restores_the_bytes(
    project: Path, monkeypatch
) -> None:
    before = config_text(project)

    def cannot_open(root, *, environ=None, executable=None):
        return config_module.PathBuild(
            executable=Path("/usr/bin/kyo"), can_open=False, problem="unknown key worker_model"
        )

    monkeypatch.setattr(config_module, "probe_path_build", cannot_open)
    result = set_value(project, "set", "worker_model", "m-1")
    assert result.code != 0
    assert "restored" in result.err and "unknown key worker_model" in result.err
    assert config_text(project) == before
    assert config_splice.read_log(os.environ)[-1]["outcome"] == "failed"


def test_a_change_to_a_key_already_present_does_not_ask_the_path_build(
    project: Path, monkeypatch
) -> None:
    assert set_value(project, "set", "worker_model", "m-1").code == 0
    asked: list[Path] = []
    monkeypatch.setattr(config_module, "probe_path_build", lambda root, **kw: asked.append(root))
    assert set_value(project, "set", "worker_model", "m-2").code == 0
    assert asked == []


# ------------------------------------------------------------------ c6: the other writers


def test_prior_art_setting_writes_through_the_splice_keeping_comments(
    project: Path, monkeypatch
) -> None:
    monkeypatch.setattr("kyosei.prior_art.extra_installed", lambda: True)
    path = config_module.config_path_for(project)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n# recx: read by x\nrecx:\n  # why: the maintainer asked, 2026-11-01\n  prior_art:\n"
        "    state: off  # not yet\n    limit: 4  # four\n",
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")
    assert invoke(["--root", str(project), "prior-art-setting", "on"]).code == 0
    after = path.read_text(encoding="utf-8")
    assert lines_changed(before, after) == (
        ["    state: off  # not yet\n"],
        ["    state: on  # not yet\n"],
    )
    assert invoke(["--root", str(project), "prior-art-setting", "off"]).code == 0
    assert lines_changed(after, path.read_text(encoding="utf-8")) == (
        ["    state: on  # not yet\n"],
        ["    state: off  # not yet\n"],
    )
    assert [row["key"] for row in config_splice.read_log(os.environ)] == [
        "recx.prior_art.state"
    ] * 2


def test_docs_coverage_level_refuses_where_it_would_have_re_rendered(
    git_project: Path,
) -> None:
    """The fallback to ``render_config`` is gone: this fails if it returns, since the flow
    mapping below could only be written by re-rendering the file and losing its comment."""
    path = config_module.config_path_for(git_project)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n# a comment a project wrote, which a re-render would drop\n"
        "docs_coverage: {level: notice}\n",
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")
    result = invoke(["--root", str(git_project), "docs", "coverage", "--level", "off"])
    assert result.code != 0 and "hand edit" in result.err
    assert path.read_text(encoding="utf-8") == before
    assert config_splice.read_log(os.environ)[-1]["outcome"] == "refused"


def test_docs_coverage_level_writes_through_the_atomic_write(
    git_project: Path, monkeypatch
) -> None:
    calls: list[Path] = []
    real = export.atomic_write_text
    monkeypatch.setattr(export, "atomic_write_text", lambda p, t: (calls.append(p), real(p, t))[1])
    result = invoke(["--root", str(git_project), "docs", "coverage", "--level", "notice"])
    assert result.code == 0, result
    assert calls == [config_module.config_path_for(git_project)]


# ------------------------------------------------------------------ c7: the log


def test_a_write_is_a_row_of_a_private_log(project: Path, store_home: Path) -> None:
    assert set_value(project, "set", "worker_model", "m-1").code == 0
    assert set_value(project, "set", "worker_model", "m-2").code == 0
    path = store_home / "config-log.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    first, second = config_splice.read_log(os.environ)
    assert (first["outcome"], first["previous"], first["new"]) == ("written", None, "m-1")
    assert (second["previous"], second["new"]) == ("m-1", "m-2")
    for row in (first, second):
        assert row["device"] == "cli" and row["project"] == "proj"
        assert row["key"] == "worker_model"
        assert row["file"] == str(config_module.config_path_for(project))
        assert row["at"].endswith("Z")


def test_a_log_inside_a_git_repository_stops_the_write(
    project: Path, tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    monkeypatch.setenv("KYOSEI_HOME", str(repository / "home"))
    before = config_text(project)
    result = set_value(project, "set", "worker_model", "m-1")
    assert result.code != 0
    assert "git repository" in result.err and "nothing was written" in result.err
    assert config_text(project) == before


def test_the_log_that_cannot_be_opened_is_cleared_by_the_remedy_it_names(
    project: Path, tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    monkeypatch.setenv("KYOSEI_HOME", str(repository / "home"))
    assert set_value(project, "set", "worker_model", "m-1").code != 0
    monkeypatch.setenv("KYOSEI_HOME", str(tmp_path / "outside"))
    assert set_value(project, "set", "worker_model", "m-1").code == 0


# ------------------------------------------------------------------ c8: last_change, --log


def test_the_documents_report_each_settings_latest_logged_change(project: Path) -> None:
    def row(document: dict, key: str) -> dict:
        return next(r for r in document["settings"] if r["key"] == key)

    before = json.loads(set_value(project, "--json").out)
    assert row(before, "worker_model")["last_change"] is None
    set_value(project, "set", "worker_model", "m-1")
    set_value(project, "set", "worker_model", "m-2")
    set_value(project, "set", "read_transcripts", "true")
    document = json.loads(set_value(project, "--json").out)
    change = row(document, "worker_model")["last_change"]
    assert (change["previous"], change["new"], change["device"]) == ("m-1", "m-2", "cli")
    assert row(document, "read_transcripts")["last_change"]["new"] is True
    assert row(document, "trunk")["last_change"] is None
    assert config_view.build_machine_document(os.environ)["settings"][0]["last_change"] is None


def test_config_log_prints_the_log(project: Path) -> None:
    set_value(project, "set", "worker_model", "m-1")
    set_value(project, "set", "trunk", "x")
    printed = set_value(project, "--log")
    assert printed.code == 0, printed
    assert "worker_model" in printed.out and "written" in printed.out
    assert "refused" in printed.out and "trunk" in printed.out
    document = json.loads(set_value(project, "--log", "--json").out)
    assert [row["outcome"] for row in document["log"]] == ["written", "refused"]


# ------------------------------------------------------------------ c9: the brief


def test_the_brief_states_each_change_since_the_previous_brief_and_asks_nothing(
    project: Path,
) -> None:
    assert invoke(["--root", str(project), "brief"]).code == 0
    set_value(project, "set", "worker_model", "m-1")
    set_value(project, "set", "trunk", "x")  # refused: not a change
    brief = invoke(["--root", str(project), "brief", "--compact"])
    assert brief.code == 0, brief
    lines = [line for line in brief.out.splitlines() if line.startswith("config:")]
    assert len(lines) == 1
    line = lines[0]
    assert 'worker_model changed from (absent) to "m-1" by cli' in line
    assert "?" not in line
    document = json.loads(invoke(["--root", str(project), "brief", "--json"]).out)
    assert document["config_changes"] == []  # the next brief has already stated it


def test_a_brief_with_no_logged_change_prints_no_config_line(project: Path) -> None:
    assert invoke(["--root", str(project), "brief"]).code == 0
    assert "config:" not in invoke(["--root", str(project), "brief"]).out
