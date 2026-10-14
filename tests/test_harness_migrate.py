"""KYO-595: `kyo harness migrate`, the copies mode behind `--copies`, and the mode-aware
upgrade line, which names only the per-project commands that fit the project's harness.

Every test writes the link under the test's own ``$KYOSEI_HOME`` (conftest's autouse
``store_home``) and runs a stub `claude` under a temporary ``CLAUDE_CONFIG_DIR``, as
``tests/test_harness_install.py`` does; no test here runs the real `claude`.
"""

# ruff: noqa: F811 -- the KYO-594 fixtures are imported by name, and pytest injects them

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from kyosei import buildinfo, cmd_harness_migrate
from kyosei.harness import CLAUDE_CODE_DIR
from kyosei.install_hooks import PRE_PUSH_MARKER, SUPERSEDED_HOOK_COMMANDS
from kyosei.install_skill import (
    CLAUDE_MD_BEGIN,
    SKILL_ASSET,
    SKILL_ASSETS,
    SKILL_REFERENCE_ASSET,
    stamp_skill_text,
)

from .conftest import invoke
from .test_harness_install import (  # noqa: F401 -- fixtures and helpers shared with KYO-594
    GIT_DIR,
    calls,
    git_project,
    isolated_git,
    link,
    stub_claude,
)

ENABLED = ("plugin install kyosei@kyosei --scope local",)


def migrate(root: Path, *extra: str):
    return invoke(["--root", str(root), "harness", "migrate", *extra])


def settings_of(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def hook_commands(root: Path) -> list[str]:
    out = []
    for groups in settings_of(root).get("hooks", {}).values():
        for group in groups:
            out += [hook["command"] for hook in group["hooks"]]
    return out


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def on_the_copies(tmp_path: Path) -> Path:
    """A git project with the copies installed and committed, and an operator's own
    line in CLAUDE.md outside the section."""
    root = git_project(tmp_path)
    claude_md = root / "CLAUDE.md"
    claude_md.write_text("# My project\n\nHouse rules here.\n", encoding="utf-8")
    assert invoke(["--root", str(root), "install-hooks", "--copies"]).code == 0
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "copies")
    return root


# ------------------------------------------------------------ c1, c2: enable and remove


def test_migrate_enables_the_plugin_then_removes_every_unedited_copy(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    root = on_the_copies(tmp_path)
    (root / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                **settings_of(root),
                "permissions": {"allow": ["Bash(make lint)"]},
            }
        ),
        encoding="utf-8",
    )
    commits_before = git(root, "rev-list", "--count", "HEAD")
    pre_push = root / ".git" / "hooks" / "pre-push"
    assert PRE_PUSH_MARKER in pre_push.read_text(encoding="utf-8")

    result = migrate(root)
    assert result.code == 0, result

    # The plugin was registered and enabled, in that order, before anything was removed.
    assert calls(stub_claude) == [
        (str(link(store_home).parent.resolve()), f"plugin marketplace add {link(store_home)}"),
        (str(root), ENABLED[0]),
    ]
    assert os.readlink(link(store_home)) == str(CLAUDE_CODE_DIR)
    # Hooks: every Kyosei entry gone, the operator's own key kept.
    settings = settings_of(root)
    assert "hooks" not in settings
    assert settings["permissions"] == {"allow": ["Bash(make lint)"]}
    # Skill: both files and the emptied directories gone.
    for asset in SKILL_ASSETS:
        assert not (root / asset.target).exists()
    assert not (root / ".claude" / "skills").exists()
    # CLAUDE.md: the section gone, every byte outside it kept.
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == (
        "# My project\n\nHouse rules here.\n"
    )
    # c2: nothing committed, the pre-push hook left and named, every removal named.
    assert git(root, "rev-list", "--count", "HEAD") == commits_before
    assert git(root, "status", "--porcelain").strip()
    assert pre_push.is_file()
    assert "the advisory git pre-push hook (it is untracked" in result.out
    assert "removed:" in result.out
    assert "nothing was committed" in result.out
    assert f"- {SKILL_ASSET.target}: the copied /kyosei skill file (unedited" in result.out
    assert "kyo brief --compact --session-start" in result.out
    data = migrate(root, "--json").json
    assert data["removed"] == []
    assert data["plugin_enabled"] is True


def test_an_edited_hook_and_an_edited_skill_file_are_left_and_named(
    tmp_path: Path, stub_claude: Path
) -> None:
    root = on_the_copies(tmp_path)
    settings = settings_of(root)
    stop = settings["hooks"]["Stop"]
    extended = None
    for group in stop:
        for hook in group["hooks"]:
            if "kyo sweep --hook" in hook["command"]:
                hook["command"] += " && make notify"
                extended = hook["command"]
    assert extended is not None
    stop.append({"hooks": [{"type": "command", "command": "make lint"}]})
    (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    reference = root / SKILL_REFERENCE_ASSET.target
    reference.write_text(reference.read_text(encoding="utf-8") + "my note\n", encoding="utf-8")

    result = migrate(root, "--json")
    assert result.code == 0, result
    commands = hook_commands(root)
    assert extended in commands
    assert "make lint" in commands
    assert len(commands) == 2
    assert reference.is_file()
    assert not (root / SKILL_ASSET.target).exists()
    left = {entry["what"]: entry["why"] for entry in result.json["left"]}
    assert "was edited" in left[f"Stop hook: {extended}"]
    assert "edited since `kyo install-skill` wrote it" in left["a /kyosei skill file"]
    assert not any("make lint" in what for what in left)


def test_an_earlier_builds_unedited_skill_and_a_superseded_hook_are_removed(
    tmp_path: Path, stub_claude: Path
) -> None:
    """The KYO-604 digest is what says a copy another build wrote is unedited; a line an
    earlier release installed is matched exactly in SUPERSEDED_HOOK_COMMANDS."""
    root = git_project(tmp_path)
    skill = root / SKILL_ASSET.target
    skill.parent.mkdir(parents=True)
    skill.write_text(stamp_skill_text("# An earlier build's skill\n"), encoding="utf-8")
    unstamped = root / SKILL_REFERENCE_ASSET.target
    unstamped.write_text("# A reference written before KYO-604, edited\n", encoding="utf-8")
    superseded = "kyo doctor && kyo sweep"
    assert superseded in SUPERSEDED_HOOK_COMMANDS
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": superseded}]}]}}),
        encoding="utf-8",
    )
    result = migrate(root, "--json")
    assert result.code == 0, result
    assert not skill.exists()
    assert unstamped.is_file()
    assert settings_of(root) == {}
    removed = [entry["what"] for entry in result.json["removed"]]
    assert f"Stop hook: {superseded}" in removed


def test_a_claude_md_that_was_only_the_section_is_removed_whole(
    tmp_path: Path, stub_claude: Path
) -> None:
    root = git_project(tmp_path)
    assert invoke(["--root", str(root), "install-skill", "--copies"]).code == 0
    claude_md = root / "CLAUDE.md"
    assert claude_md.read_text(encoding="utf-8").startswith(CLAUDE_MD_BEGIN)
    assert migrate(root).code == 0
    assert not claude_md.exists()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Top\n\n{S}\n", "# Top\n"),
        ("# Top\n\n{S}\n\n## After\n", "# Top\n\n## After\n"),
        ("{S}\n", ""),
        ("no section here\n", None),
    ],
)
def test_the_claude_md_section_is_removed_and_nothing_else(text: str, expected) -> None:
    from kyosei.install_skill import CLAUDE_MD_SECTION

    assert (
        cmd_harness_migrate.claude_md_without_section(text.replace("{S}", CLAUDE_MD_SECTION))
        == expected
    )


def test_migrate_writes_the_pre_push_hook_a_copies_project_lacked(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    """KYO-612: the plugin route keeps KYO-60's human half, so migrate writes the hook
    when the project on the copies never had it (a fresh clone, say), as `kyo harness
    install claude-code` does."""
    root = on_the_copies(tmp_path)
    pre_push = root / ".git" / "hooks" / "pre-push"
    pre_push.unlink()
    result = migrate(root, "--json")
    assert result.code == 0, result
    assert PRE_PUSH_MARKER in pre_push.read_text(encoding="utf-8")
    assert result.json["pre_push"]["changes"] == [
        f"git pre-push: wrote {pre_push} (advisory; `git push --no-verify` skips it, and "
        ".git/hooks is not versioned, so a fresh clone will not have it)"
    ]
    assert not any("pre-push" in entry["what"] for entry in result.json["left"])


def test_a_failed_enablement_removes_nothing(tmp_path: Path, stub_claude: Path) -> None:
    root = on_the_copies(tmp_path)
    program = Path(os.environ["PATH"].split(os.pathsep)[0]) / "claude"
    good = program.read_text(encoding="utf-8")
    program.write_text(
        good + 'case "$*" in *"plugin install"*) echo boom >&2; exit 1;; esac\n',
        encoding="utf-8",
    )
    before = hook_commands(root)
    refused = migrate(root)
    assert refused.code != 0
    assert "boom" in refused.err
    assert hook_commands(root) == before
    assert (root / SKILL_ASSET.target).is_file()


def test_without_claude_it_refuses_and_changes_nothing_and_claude_on_path_clears_it(
    tmp_path: Path, stub_claude: Path, monkeypatch, store_home: Path
) -> None:
    root = on_the_copies(tmp_path)
    stub_path = os.environ["PATH"]
    monkeypatch.setenv("PATH", GIT_DIR)
    if Path(GIT_DIR, "claude").exists():
        pytest.skip(f"a claude sits in {GIT_DIR}, beside git, so PATH cannot lack it here")
    refused = migrate(root)
    assert refused.code != 0
    assert "`claude` is not on PATH" in refused.err
    assert (root / SKILL_ASSET.target).is_file()
    assert not link(store_home).is_symlink()
    monkeypatch.setenv("PATH", stub_path)
    cleared = migrate(root)
    assert cleared.code == 0, cleared
    assert not (root / SKILL_ASSET.target).exists()


def test_an_unreadable_settings_file_refuses_before_anything_and_fixing_it_clears_it(
    tmp_path: Path, stub_claude: Path
) -> None:
    root = on_the_copies(tmp_path)
    settings = root / ".claude" / "settings.json"
    settings.write_text("{not json", encoding="utf-8")
    refused = migrate(root)
    assert refused.code != 0
    assert "correct the file and run `kyo harness migrate` again" in refused.err
    assert calls(stub_claude) == []
    assert (root / SKILL_ASSET.target).is_file()
    settings.write_text("{}\n", encoding="utf-8")
    assert migrate(root).code == 0


def test_install_without_a_harness_is_refused_and_naming_it_clears_it(
    tmp_path: Path, stub_claude: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    refused = invoke(["harness", "install"])
    assert refused.code != 0
    assert "`kyo harness install claude-code`" in refused.err
    assert invoke(["harness", "install", "claude-code"]).code == 0


def test_outside_a_project_it_refuses(tmp_path: Path, stub_claude: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = invoke(["harness", "migrate"])
    assert result.code != 0
    assert "runs in a Kyosei project" in result.err


# ------------------------------------------------------------- c6: the source checkout
#
# KYO-595.c6's refusal in the repository the package is built from is gone: KYO-610 moved
# that repository onto the plugin like a consumer, and tests/test_kyo610_source_checkout.py
# holds the migration there (the mirror read against the checkout's own source).


# ---------------------------------------------------------------- c3: the copies mode


@pytest.mark.parametrize("command", ["install-skill", "install-hooks"])
def test_a_project_on_the_copies_keeps_writing_them_with_no_flag(
    tmp_path: Path, command: str
) -> None:
    root = git_project(tmp_path)
    skill = root / SKILL_ASSET.target
    skill.parent.mkdir(parents=True)
    skill.write_text("# an old copy\n", encoding="utf-8")
    result = invoke(["--root", str(root), command])
    assert result.code == 0, result
    assert "# an old copy" not in skill.read_text(encoding="utf-8")


def test_copied_hooks_alone_put_a_project_on_the_copies(tmp_path: Path) -> None:
    root = git_project(tmp_path)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "kyo doctor && kyo sweep"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    assert cmd_harness_migrate.harness_mode(root) == cmd_harness_migrate.MODE_COPIES
    assert invoke(["--root", str(root), "install-skill"]).code == 0
    assert (root / SKILL_ASSET.target).is_file()


def _plugin_project(tmp_path: Path) -> Path:
    root = git_project(tmp_path)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"kyosei@kyosei": True}}), encoding="utf-8"
    )
    assert cmd_harness_migrate.harness_mode(root) == cmd_harness_migrate.MODE_PLUGIN
    return root


@pytest.mark.parametrize("command", ["install-skill", "install-hooks"])
@pytest.mark.parametrize("mode", ["none", "plugin"])
def test_elsewhere_the_copies_need_the_flag_and_the_plugin_is_named(
    tmp_path: Path, command: str, mode: str
) -> None:
    root = _plugin_project(tmp_path) if mode == "plugin" else git_project(tmp_path)
    if mode == "none":
        assert cmd_harness_migrate.harness_mode(root) == cmd_harness_migrate.MODE_NONE
    claude_md_before = (root / "CLAUDE.md").read_text(encoding="utf-8")
    refused = invoke(["--root", str(root), command])
    assert refused.code != 0
    assert "the Claude Code plugin is the default" in refused.err
    assert "`kyo harness install claude-code`" in refused.err
    assert f"`kyo {command} --copies`" in refused.err
    assert not (root / ".claude" / "skills").exists()
    assert not (root / ".claude" / "settings.json").exists()
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == claude_md_before
    # `--print` writes nothing, so it is not refused.
    assert invoke(["--root", str(root), command, "--print"]).code == 0
    # The remedy the refusal names clears it.
    cleared = invoke(["--root", str(root), command, "--copies"])
    assert cleared.code == 0, cleared
    assert (root / SKILL_ASSET.target).is_file()


# ---------------------------------------------------------------- c4: the upgrade line


def _upgrade_line(root: Path) -> str:
    result = invoke(["--root", str(root), "version"])
    assert result.code == 0, result
    lines = [line.strip() for line in result.out.splitlines()]
    step = [line for line in lines if line.startswith("then, in each project:")]
    if not step:
        pytest.skip("this build's identity is unknown, so `kyo version` prints no upgrade line")
    return step[0]


def test_the_upgrade_line_names_the_copies_commands_on_the_copies(tmp_path: Path) -> None:
    root = git_project(tmp_path)
    assert invoke(["--root", str(root), "install-skill", "--copies"]).code == 0
    step = buildinfo.per_project_upgrade_step(root)
    assert step.startswith(buildinfo.PER_PROJECT_UPGRADE_COMMANDS)
    assert "`kyo harness migrate`" in step
    assert buildinfo.PER_PROJECT_UPGRADE_COMMANDS in _upgrade_line(root)


def test_the_upgrade_line_names_only_install_procedures_for_a_plugin_project(
    tmp_path: Path,
) -> None:
    """KYO-595.c4: no harness command for a plugin project,
    but procedures are a copy on every route, so `kyo install-procedures` stays."""
    root = _plugin_project(tmp_path)
    line = _upgrade_line(root)
    assert "`kyo install-procedures`" in line
    for harness_command in ("`kyo install-hooks`", "`kyo install-skill`"):
        assert harness_command not in line


def test_the_upgrade_line_points_a_project_on_neither_at_the_plugin(tmp_path: Path) -> None:
    root = git_project(tmp_path)
    step = buildinfo.per_project_upgrade_step(root)
    assert "kyo install-hooks" not in step
    assert "`kyo harness install claude-code`" in step
