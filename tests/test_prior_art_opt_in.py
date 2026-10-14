"""KYO-586: opting a project into prior-art search.

`kyo prior-art-setting on|off` writes ``recx.prior_art.state`` (c1); `kyo onboard` asks the
question when recx_cli is importable (c2); the session-start brief prints one line while the
extra is installed and nothing is recorded (c3); the references and the skill say all of it
(c4). recx-cli is in the dev extra, so it is importable here unless a test hides it.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from kyosei import cmd_onboard, prior_art
from kyosei.cli import COMMAND_TABLE, MUTATING_COMMANDS

REPO = Path(__file__).resolve().parent.parent
DECISIONS = REPO / "docs" / "guide" / "setup-decisions.md"
INSTALL = REPO / "docs" / "reference" / "topics" / "install.md"
OPERATING = REPO / "docs" / "reference" / "topics" / "operating.md"
REFERENCE = REPO / "src/kyosei/harness/claude-code/plugin/skills/kyosei" / "REFERENCE.md"
SKILL = REPO / "src/kyosei/harness/claude-code/plugin/skills/kyosei" / "SKILL.md"


def _config(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def _raw(project: Path) -> dict:
    return yaml.safe_load(_config(project).read_text(encoding="utf-8"))


def _append(project: Path, text: str) -> None:
    path = _config(project)
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


@pytest.fixture
def no_extra(monkeypatch):
    """recx_cli unimportable, as in an installation without the extra. Returns the undo."""
    real = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "recx_cli" or name.startswith("recx_cli."):
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    return lambda: monkeypatch.setattr(importlib.util, "find_spec", real)


# ---------------------------------------------------------------------- c1


def test_c1_the_command_is_registered_and_is_not_a_store_write() -> None:
    assert COMMAND_TABLE["prior-art-setting"][0] is prior_art.cmd_prior_art_setting
    assert "prior-art-setting" not in MUTATING_COMMANDS


def test_c1_on_and_off_are_both_recorded_explicitly(kyo, project) -> None:
    assert prior_art.read_prior_art_config(project).state == prior_art.STATE_ABSENT
    before = _config(project).read_text(encoding="utf-8")

    on = kyo("prior-art-setting", "on", "--json")
    assert on.code == 0, on
    assert on.json["state"] == "on" and on.json["previous"] == "absent"
    assert on.json["written"] is True
    assert on.json["on"] == {"proposal": ["new", "promote", "spawn"]}
    after_on = _config(project).read_text(encoding="utf-8")
    assert after_on.startswith(before), "the lines already in the file are kept byte for byte"
    assert prior_art.read_prior_art_config(project).state == "on"
    assert "# recx: read by prior_art.read_prior_art_config" in after_on

    off = kyo("prior-art-setting", "off", "--json")
    assert off.code == 0, off
    assert (off.json["state"], off.json["previous"]) == ("off", "on")
    cfg = prior_art.read_prior_art_config(project)
    assert cfg.state == "off", "off is a recorded choice, not an absent block"
    assert _raw(project)["recx"]["prior_art"]["state"] in ("off", False)
    assert kyo("doctor").code == 0


def test_c1_a_repeat_writes_nothing_and_says_so(kyo, project) -> None:
    assert kyo("prior-art-setting", "off").code == 0
    text = _config(project).read_text(encoding="utf-8")
    again = kyo("prior-art-setting", "off", "--json")
    assert again.code == 0 and again.json["written"] is False
    assert _config(project).read_text(encoding="utf-8") == text
    assert "already off" in kyo("prior-art-setting", "off").out


def test_c1_keeps_the_operators_trigger_limit_index_url_and_comments(kyo, project) -> None:
    _append(
        project,
        "# Operator, 2026-10-12: proposals and investigations only.\n"
        "recx:\n"
        "  prior_art:\n"
        "    state: off   # declined until the index settles\n"
        "    on:\n"
        "      proposal: [new]\n"
        "    limit: 3\n"
        "    index_url: file:///tmp/index.json\n",
    )
    result = kyo("prior-art-setting", "on", "--json")
    assert result.code == 0, result
    text = _config(project).read_text(encoding="utf-8")
    assert "# Operator, 2026-10-12: proposals and investigations only." in text
    assert "    state: on   # declined until the index settles\n" in text
    cfg = prior_art.read_prior_art_config(project)
    assert cfg.state == "on"
    assert cfg.on == {"proposal": ("new",)}
    assert cfg.limit == 3
    assert cfg.index_url == "file:///tmp/index.json"
    assert result.json["on"] == {"proposal": ["new"]}


def test_c1_a_block_with_no_state_line_is_rerendered_with_its_keys_kept(project) -> None:
    """The splice's fallback: a flow-style block has no `state:` line to substitute."""
    _append(project, "recx: {prior_art: {state: off, limit: 7}}\nworker_model: opus\n")
    before, after, written = prior_art.set_prior_art_state(project, "on")
    assert written and before.state == "off" and after.state == "on"
    assert after.limit == 7
    raw = _raw(project)
    assert raw["worker_model"] == "opus"


def test_c1_a_malformed_block_is_refused_unchanged(kyo, project) -> None:
    _append(project, "recx:\n  prior_art:\n    limit: 3\n")
    text = _config(project).read_text(encoding="utf-8")
    refused = kyo("prior-art-setting", "off")
    assert refused.code != 0
    assert "must record a choice" in refused.err
    assert _config(project).read_text(encoding="utf-8") == text


def test_c1_on_without_the_extra_is_refused_and_installing_it_clears_it(
    kyo, project, no_extra
) -> None:
    text = _config(project).read_text(encoding="utf-8")
    refused = kyo("prior-art-setting", "on")
    assert refused.code != 0
    assert prior_art.INSTALL_COMMAND in refused.err
    assert _config(project).read_text(encoding="utf-8") == text, "a refusal writes nothing"
    # `off` is a choice that needs nothing installed.
    assert kyo("prior-art-setting", "off").code == 0
    # The remedy: the extra installed (here: recx_cli importable again).
    no_extra()
    cleared = kyo("prior-art-setting", "on")
    assert cleared.code == 0, cleared
    assert prior_art.read_prior_art_config(project).state == "on"


def test_c1_on_without_the_extra_still_skips_creation_with_the_install_line(
    kyo, project, write_file, no_extra
) -> None:
    """KYO-585's inherited choice: state on, recx absent, a creating command names the
    install command in one line and exits 0."""
    _append(project, "recx:\n  prior_art:\n    state: on\n")
    made = kyo(
        "new", "proposal", "--title", "Search", "--description-file",
        write_file("d.txt", "x"), "--json",
    )
    assert made.code == 0, made
    assert made.json["prior_art"]["skipped"] == "not-installed"


# ---------------------------------------------------------------------- c2


def _d12(text: str) -> str:
    start = text.index("- **[D12]")
    end = text.index("\n- **[", start + 1)
    return text[start:end]


def test_c2_onboard_asks_when_recx_is_importable() -> None:
    entry = _d12(cmd_onboard.render_prompt())
    assert "This build can import recx_cli" in entry
    assert "Ask" in entry and "?" in entry
    for phrase in ("`kyo new`", "`kyo promote`", "`kyo spawn`", "never on an edit",
                   "`recx.prior_art.on`", "`kyo prior-art-setting on`",
                   "`kyo prior-art-setting off`"):
        assert phrase in entry, phrase


def test_c2_onboard_says_not_to_ask_without_the_extra(no_extra) -> None:
    text = cmd_onboard.render_prompt()
    entry = " ".join(_d12(text).split())
    assert "This build cannot import recx_cli" in entry
    assert "Do not ask when the extra is not installed" in entry
    assert cmd_onboard.RECX_PLACEHOLDER not in text


def test_c2_the_question_is_a_setup_decision() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    assert re.search(r"^### D12\. Prior-art search", decisions, re.MULTILINE)
    section = decisions[decisions.index("### D12.") :]
    section = section[: section.index("\n---")]
    question = re.search(r'"(Should Kyosei search[^"]+)"', section).group(1)
    prompt_text = " ".join(cmd_onboard.packaged_prompt_text().split())
    assert " ".join(question.split()) in prompt_text
    assert "`kyo prior-art-setting on`" in section


# ---------------------------------------------------------------------- c3


def _brief(kyo) -> str:
    result = kyo("brief", "--compact")
    assert result.code == 0, result
    return result.out


def test_c3_the_brief_asks_while_unchosen_and_either_answer_removes_it(kyo, project) -> None:
    assert prior_art.UNCHOSEN_LINE in _brief(kyo)
    assert kyo("brief", "--json").json["prior_art_unchosen"] == prior_art.UNCHOSEN_LINE
    assert "kyo prior-art-setting" in prior_art.UNCHOSEN_LINE

    assert kyo("prior-art-setting", "off").code == 0
    assert prior_art.UNCHOSEN_LINE not in _brief(kyo)
    assert kyo("brief", "--json").json["prior_art_unchosen"] is None

    assert kyo("prior-art-setting", "on").code == 0
    assert prior_art.UNCHOSEN_LINE not in _brief(kyo)


def test_c3_no_line_without_the_extra_and_no_config_read(
    kyo, project, monkeypatch, no_extra
) -> None:
    def never(root):
        raise AssertionError("the config is read although recx is not installed")

    monkeypatch.setattr(prior_art, "read_prior_art_config", never)
    assert prior_art.unchosen_line(project) is None
    assert "PRIOR-ART" not in _brief(kyo)


def test_c3_the_brief_line_never_imports_recx(project, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "recx_cli" or name.startswith("recx_cli."):
            raise AssertionError("recx_cli was imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert prior_art.unchosen_line(project) == prior_art.UNCHOSEN_LINE


# ---------------------------------------------------------------------- c4


@pytest.mark.parametrize("path", [INSTALL, OPERATING, REFERENCE], ids=lambda p: p.name)
def test_c4_the_references_state_the_opt_in(path: Path) -> None:
    text = " ".join(path.read_text(encoding="utf-8").split())
    for phrase in (
        "uv tool install 'kyosei[recx]'",
        "kyo prior-art-setting on",
        "kyo prior-art-setting off",
        "`kyo new`, `kyo promote` or `kyo spawn`",
        "an edit, a move or a status change",
        "recx.prior_art.on",
        "kyo prior-art <record-id>",
        "strangers",
    ):
        assert phrase in text, f"{path.name}: {phrase}"


def test_c4_the_skill_tells_the_orchestrator_to_ask() -> None:
    reference = " ".join(REFERENCE.read_text(encoding="utf-8").split())
    assert "When you see it, ask the operator" in reference
    skill = SKILL.read_text(encoding="utf-8")
    assert "`PRIOR-ART:` line is the operator's question" in skill
