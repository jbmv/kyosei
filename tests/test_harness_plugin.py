"""KYO-593: the Claude Code plugin and its one-entry marketplace ship inside the package.

The package carries a one-plugin marketplace that Claude Code loads in place, so the plugin
is always the installed build's own. These tests hold the files only; registering them with
Claude Code is `kyo harness install` (KYO-594).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from kyosei import harness, install_skill
from kyosei.install_hooks import (
    HOOK_INSTALL_TARGETS,
    PATH_GUARD_MESSAGE,
    PATH_GUARD_PREFIX,
    _load_hook_fragment,
    merge_hooks,
)
from kyosei.install_skill import (
    INSTALLED_SKILL_DIR,
    SKILL_ASSET,
    SKILL_ASSETS,
    SKILL_DIR_TOKEN,
    SKILL_REFERENCE_ASSET,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "src" / "kyosei" / "harness" / "claude-code"
PLUGIN = SOURCE / "plugin"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- c1


def test_the_marketplace_lists_one_plugin_named_kyosei_at_dot_slash_plugin() -> None:
    marketplace = _json(SOURCE / ".claude-plugin" / "marketplace.json")
    assert marketplace["name"] == "kyosei"
    assert [(p["name"], p["source"]) for p in marketplace["plugins"]] == [("kyosei", "./plugin")]


def test_the_plugin_carries_its_manifest_skill_reference_and_hooks() -> None:
    for relative in (
        ".claude-plugin/plugin.json",
        "skills/kyosei/SKILL.md",
        "skills/kyosei/REFERENCE.md",
        "hooks/hooks.json",
    ):
        assert (PLUGIN / relative).is_file(), relative
    assert _json(PLUGIN / ".claude-plugin" / "plugin.json")["name"] == "kyosei"


def test_the_plugin_version_is_the_package_version() -> None:
    """The plugin's version string: `claude plugin validate --strict` needs one,
    and the package's is the only one that means anything."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    assert _json(PLUGIN / ".claude-plugin" / "plugin.json")["version"] == version


def test_the_skill_files_were_moved_not_copied() -> None:
    defaults = REPO_ROOT / "src" / "kyosei" / "defaults"
    assert not (defaults / "skill.md").exists()
    assert not (defaults / "skill-reference.md").exists()
    assert [a.packaged_path() for a in SKILL_ASSETS] == [
        harness.CLAUDE_CODE_PLUGIN_DIR / "skills" / "kyosei" / "SKILL.md",
        harness.CLAUDE_CODE_PLUGIN_DIR / "skills" / "kyosei" / "REFERENCE.md",
    ]


def test_install_skill_writes_its_copies_from_the_plugins_files(tmp_path: Path) -> None:
    written = install_skill.install_skill(tmp_path)
    assert [path for path, changed in written[:2]] == [
        tmp_path / SKILL_ASSET.target,
        tmp_path / SKILL_REFERENCE_ASSET.target,
    ]
    for asset in SKILL_ASSETS:
        plugin_text = (PLUGIN / "skills" / "kyosei" / asset.source).read_text(encoding="utf-8")
        # KYO-604: a consumer's copy ends with the digest of what was written.
        body, digest = install_skill.split_skill_digest(
            (tmp_path / asset.target).read_text(encoding="utf-8")
        )
        assert digest is not None
        assert body == install_skill.installed_skill_text(plugin_text)


# ---------------------------------------------------------------------------- c2


def _invocations(command: str) -> tuple[str, ...] | str:
    """The `kyo` argument strings a plugin command runs, in order; the command itself
    when it is not the hook script followed by `kyo` arguments."""
    prefix = harness.HOOK_SCRIPT_INVOCATION + " "
    parts = command.split(" && ")
    if not all(part.startswith(prefix) for part in parts):
        return command
    return tuple(part[len(prefix) :] for part in parts)


def _commands_by_event(hooks: dict) -> dict[str, Counter]:
    """The `kyo` invocations each plugin command runs, counted per event."""
    return {
        event: Counter(_invocations(h["command"]) for group in groups for h in group["hooks"])
        for event, groups in hooks.items()
    }


def _fragment_commands_by_event() -> dict[str, Counter]:
    """What the fragments named by HOOK_INSTALL_TARGETS run on each event, read from
    the fragment files directly rather than through `merge_hooks`, each command as the
    `kyo` argument strings it runs behind its inline guard."""
    found: dict[str, Counter] = {}
    for event, name in HOOK_INSTALL_TARGETS:
        for group in _load_hook_fragment(name):
            for hook in group["hooks"]:
                action = hook["command"].removeprefix(PATH_GUARD_PREFIX)
                assert action != hook["command"], f"{name}: no PATH guard"
                invocations = tuple(p.removeprefix("kyo ") for p in action.split(" && "))
                assert all(p.startswith("kyo ") for p in action.split(" && ")), action
                found.setdefault(event, Counter())[invocations] += 1
    return found


def _disagreements(hooks: dict) -> list[str]:
    plugin = _commands_by_event(hooks)
    fragments = _fragment_commands_by_event()
    return [
        f"{event}: plugin runs {sorted(map(str, plugin.get(event, Counter()).elements()))}, "
        f"the fragments run {sorted(map(str, fragments.get(event, Counter()).elements()))}"
        for event in sorted(set(plugin) | set(fragments))
        if plugin.get(event) != fragments.get(event)
    ]


def test_the_plugin_hooks_run_the_fragments_kyo_invocations_on_each_event() -> None:
    """KYO-605.c2, replacing KYO-593's byte-for-byte command comparison: the plugin's
    commands are the hook script plus `kyo` arguments, the fragments' are the inline
    guard plus `kyo` invocations, and on every event the two run the same ones."""
    hooks = _json(harness.CLAUDE_CODE_HOOKS_PATH)["hooks"]
    assert _disagreements(hooks) == [], (
        "the plugin's hooks.json and the hook fragments disagree; rewrite it with "
        "`.venv/bin/python -m kyosei.harness`"
    )


@pytest.mark.parametrize("change", ["drop", "add", "move", "argument", "inline"])
def test_the_comparison_fails_when_the_two_disagree(change: str) -> None:
    """The check above, shown biting: each way a hand edit could part the two."""
    hooks = json.loads(json.dumps(_json(harness.CLAUDE_CODE_HOOKS_PATH)["hooks"]))
    if change == "drop":
        hooks["SubagentStop"][0]["hooks"].pop()
    elif change == "add":
        hooks["Stop"].append(
            {"hooks": [{"type": "command", "command": harness.HOOK_SCRIPT_INVOCATION + " x"}]}
        )
    elif change == "move":
        hooks["SessionStart"].append(hooks["SessionEnd"].pop())
    elif change == "argument":
        hook = hooks["SubagentStop"][0]["hooks"][1]
        hook["command"] = hook["command"].replace("--against main", "--against trunk")
    else:
        # The fragment's own command, inline guard and all, is no longer what the plugin
        # runs: a revert to KYO-593's hooks.json reads as a disagreement.
        hooks["UserPromptSubmit"][0]["hooks"][0]["command"] = (
            PATH_GUARD_PREFIX + "kyo check-build --hook"
        )
    assert _disagreements(hooks) != []


def test_the_committed_hooks_file_is_the_generated_one() -> None:
    """Byte for byte, so the timeouts and matchers `kyo install-hooks` writes are the
    plugin's too, and the remedy the message names is the one that clears it."""
    assert (
        harness.CLAUDE_CODE_HOOKS_PATH.read_text(encoding="utf-8") == harness.render_plugin_hooks()
    ), "rewrite the plugin's hooks.json with `.venv/bin/python -m kyosei.harness`"


def test_the_regeneration_command_writes_the_generated_file(tmp_path: Path) -> None:
    target = tmp_path / "hooks.json"
    target.write_text("{}\n", encoding="utf-8")
    assert harness.write_plugin_hooks(target) is True
    assert target.read_text(encoding="utf-8") == harness.render_plugin_hooks()
    assert harness.write_plugin_hooks(target) is False


def test_the_generator_refuses_a_command_the_script_form_cannot_carry() -> None:
    assert harness.plugin_hook_command(PATH_GUARD_PREFIX + "kyo a --b && kyo c") == (
        f"{harness.HOOK_SCRIPT_INVOCATION} a --b && {harness.HOOK_SCRIPT_INVOCATION} c"
    )
    for command in (
        "kyo sweep --hook",  # no guard
        PATH_GUARD_PREFIX + "kyo a || kyo b",
        PATH_GUARD_PREFIX + "kyo a; rm -rf x",
        PATH_GUARD_PREFIX + "git status",
    ):
        with pytest.raises(ValueError):
            harness.plugin_hook_command(command)


# ------------------------------------------------------------------------- KYO-605


def _plugin_commands() -> list[str]:
    hooks = _json(harness.CLAUDE_CODE_HOOKS_PATH)["hooks"]
    return [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]


def test_every_plugin_command_is_the_hook_script_plus_kyo_arguments() -> None:
    """KYO-605.c1, the hooks.json half: every command names the plugin's one script
    through ${CLAUDE_PLUGIN_ROOT}, and nothing else."""
    assert harness.HOOK_SCRIPT_INVOCATION == 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/kyo-hook"'
    assert harness.CLAUDE_CODE_HOOK_SCRIPT_PATH == PLUGIN / "hooks" / "kyo-hook"
    commands = _plugin_commands()
    assert len(commands) >= 8
    for command in commands:
        assert isinstance(_invocations(command), tuple), command
        assert "command -v" not in command, command


def test_every_plugin_command_is_shorter_than_120_characters() -> None:
    """KYO-605.c4: what Claude Code prints when a hook fails is the whole command."""
    too_long = [(len(c), c) for c in _plugin_commands() if len(c) >= 120]
    assert too_long == []


def test_the_committed_hook_script_is_the_generated_one_and_executable() -> None:
    script = harness.CLAUDE_CODE_HOOK_SCRIPT_PATH
    assert script.read_text(encoding="utf-8") == harness.render_hook_script(), (
        "rewrite the plugin's hook script with `.venv/bin/python -m kyosei.harness`"
    )
    assert os.access(script, os.X_OK), "the hook script lost its executable bit"


def test_the_hook_script_carries_the_fragments_guard_verbatim() -> None:
    """KYO-605.c1: the same text, so the same behaviour, as every fragment's guard."""
    text = harness.render_hook_script()
    assert text.startswith("#!/bin/sh\n")
    (guard_line,) = [line for line in text.splitlines() if line.startswith("command -v kyo")]
    assert guard_line == PATH_GUARD_PREFIX + 'exec kyo "$@"'


def test_the_script_regeneration_restores_text_and_mode(tmp_path: Path) -> None:
    target = tmp_path / "kyo-hook"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o644)
    assert harness.write_hook_script(target) is True
    assert target.read_text(encoding="utf-8") == harness.render_hook_script()
    assert target.stat().st_mode & 0o777 == 0o755
    assert harness.write_hook_script(target) is False


NO_KYO_PATH = "/usr/bin:/bin"


@pytest.fixture
def fake_kyo(tmp_path: Path) -> Path:
    """A directory holding a `kyo` that prints its arguments and stdin and exits with
    ``$FAKE_KYO_STATUS``, so a test can see exactly what the script ran."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    kyo = bin_dir / "kyo"
    kyo.write_text(
        '#!/bin/sh\necho "kyo $*"\ncat\necho "to stderr" >&2\nexit "${FAKE_KYO_STATUS:-0}"\n',
        encoding="utf-8",
    )
    kyo.chmod(0o755)
    return bin_dir


def _run(
    command: str, *, cwd: Path, path: str, plugin_root: Path, extra: dict | None = None
) -> tuple[int, str, str]:
    env = {
        "PATH": path,
        "HOME": str(cwd),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        **(extra or {}),
    }
    run = subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=cwd,
        env=env,
        input="{}",
        capture_output=True,
        text=True,
        timeout=60,
    )
    return run.returncode, run.stdout, run.stderr


@pytest.fixture
def places(tmp_path: Path) -> dict[str, Path]:
    """A plugin root with a space in it holding the committed script, a directory
    outside any project and any git repository, and a subdirectory of a project."""
    root = tmp_path / "plugin root"
    (root / "hooks").mkdir(parents=True)
    shutil.copy2(harness.CLAUDE_CODE_HOOK_SCRIPT_PATH, root / "hooks" / "kyo-hook")
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    (project / "kyosei").mkdir(parents=True)
    (project / "kyosei" / "config.yml").write_text("prefix: TST\n", encoding="utf-8")
    inside = project / "src" / "deep"
    inside.mkdir(parents=True)
    linked = tmp_path / "split"
    (linked / "sub").mkdir(parents=True)
    (linked / ".kyosei").write_text("../store\n", encoding="utf-8")
    for directory in (outside, inside, linked / "sub"):
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=directory, capture_output=True
        )
        assert probe.returncode != 0, f"{directory} must sit outside every git repository"
    return {"root": root, "outside": outside, "inside": inside, "linked": linked / "sub"}


def test_the_script_without_kyo_outside_a_project_is_silent(places: dict[str, Path]) -> None:
    """KYO-605.c3, kyo absent and no Kyosei project: exit 0 and nothing printed."""
    assert shutil.which("kyo", path=NO_KYO_PATH) is None
    for command in _plugin_commands():
        assert _run(
            command, cwd=places["outside"], path=NO_KYO_PATH, plugin_root=places["root"]
        ) == (0, "", ""), command


@pytest.mark.parametrize("where", ["inside", "linked"])
def test_the_script_without_kyo_inside_a_project_prints_the_guard_line(
    places: dict[str, Path], where: str
) -> None:
    """KYO-605.c3, kyo absent inside a project (by `kyosei/config.yml` or by the
    `.kyosei` store link): the KYO-316 line once, exit 127, and a chain stops there."""
    assert shutil.which("kyo", path=NO_KYO_PATH) is None
    for command in _plugin_commands():
        assert _run(command, cwd=places[where], path=NO_KYO_PATH, plugin_root=places["root"]) == (
            127,
            "",
            PATH_GUARD_MESSAGE + "\n",
        ), command


def test_the_script_without_kyo_reads_claude_project_dir_before_its_cwd(
    places: dict[str, Path],
) -> None:
    """As the inline guard does: ``$CLAUDE_PROJECT_DIR`` decides, the cwd only without it."""
    command = _plugin_commands()[0]
    root = places["root"]
    in_project = {"CLAUDE_PROJECT_DIR": str(places["inside"])}
    outside = {"CLAUDE_PROJECT_DIR": str(places["outside"])}
    assert (
        _run(command, cwd=places["outside"], path=NO_KYO_PATH, plugin_root=root, extra=in_project)[
            0
        ]
        == 127
    )
    assert _run(
        command, cwd=places["inside"], path=NO_KYO_PATH, plugin_root=root, extra=outside
    ) == (0, "", "")


@pytest.mark.parametrize("status", ["0", "3"])
def test_the_script_with_kyo_runs_it_with_the_arguments_and_its_status(
    places: dict[str, Path], fake_kyo: Path, status: str
) -> None:
    """KYO-605.c3, kyo present: the script runs `kyo` with its arguments, passes the
    hook's stdin through, and exits with kyo's own status, in or out of a project."""
    path = f"{fake_kyo}:{NO_KYO_PATH}"
    for where in ("outside", "inside"):
        got = _run(
            f"{harness.HOOK_SCRIPT_INVOCATION} check-branch --subagent-stop --against main",
            cwd=places[where],
            path=path,
            plugin_root=places["root"],
            extra={"FAKE_KYO_STATUS": status},
        )
        assert got == (
            int(status),
            "kyo check-branch --subagent-stop --against main\n{}",
            "to stderr\n",
        ), where


@pytest.mark.parametrize("where", ["outside", "inside"])
@pytest.mark.parametrize("kyo_present", [False, True], ids=["kyo-absent", "kyo-present"])
def test_each_plugin_command_behaves_as_the_fragment_command_it_replaces(
    places: dict[str, Path], fake_kyo: Path, where: str, kyo_present: bool
) -> None:
    """KYO-605.c1's "exactly as the packaged fragments do", run rather than read: on each
    event, every plugin command and the fragment command it was generated from give the
    same exit status, stdout and stderr, with kyo absent and with it present."""
    path = f"{fake_kyo}:{NO_KYO_PATH}" if kyo_present else NO_KYO_PATH
    settings, _ = merge_hooks({})
    for event, groups in settings["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                fragment = hook["command"]
                plugin = harness.plugin_hook_command(fragment)
                results = [
                    _run(c, cwd=places[where], path=path, plugin_root=places["root"])
                    for c in (fragment, plugin)
                ]
                assert results[0] == results[1], (event, fragment, results)
                assert plugin in _plugin_commands(), plugin


# ---------------------------------------------------------------------------- c3


def test_the_packaged_skill_names_its_reference_by_skill_dir_and_the_copy_by_path(
    tmp_path: Path,
) -> None:
    """KYO-593.c3. Inside the plugin no `.claude/skills/kyosei/` exists, so the packaged
    text names `${CLAUDE_SKILL_DIR}/REFERENCE.md`, which Claude Code substitutes;
    the copy `kyo install-skill` writes is read as plain text too, so it names the path.
    """
    installed_path = f"{INSTALLED_SKILL_DIR}/REFERENCE.md"
    packaged = SKILL_ASSET.packaged_path().read_text(encoding="utf-8")
    assert f"{SKILL_DIR_TOKEN}/REFERENCE.md" in packaged
    assert installed_path not in packaged

    install_skill.install_skill(tmp_path)
    copy = (tmp_path / SKILL_ASSET.target).read_text(encoding="utf-8")
    assert installed_path in copy
    assert SKILL_DIR_TOKEN not in copy
    assert copy.count(installed_path) == packaged.count(f"{SKILL_DIR_TOKEN}/REFERENCE.md")


def test_the_substitution_inverts_exactly_on_both_packaged_files() -> None:
    """The source repository's mirror remedy runs the substitution backwards, which
    is lossless only while the packaged text never spells the installed directory."""
    for asset in SKILL_ASSETS:
        packaged = asset.packaged_path().read_text(encoding="utf-8")
        assert f"{INSTALLED_SKILL_DIR}/" not in packaged, asset.source
        assert (
            install_skill.packaged_skill_text_from_installed(
                install_skill.installed_skill_text(packaged)
            )
            == packaged
        )


# ---------------------------------------------------------------------------- c4


def _description(text: str) -> str:
    match = re.search(r"(?m)^description: (.+)$", text.split("\n---", 1)[0])
    assert match, "the skill has no description in its frontmatter"
    return match.group(1)


def test_the_skill_loads_on_the_session_start_brief_not_on_claude_md() -> None:
    """Under the plugin there is no CLAUDE.md section; the brief the
    SessionStart hook prints names the project in every session either route opens."""
    description = _description(SKILL_ASSET.packaged_path().read_text(encoding="utf-8"))
    assert "session-start brief names a Kyosei project" in description
    assert "CLAUDE.md" not in description


# ---------------------------------------------------------------------------- c5


def _copy_buildable_tree(destination: Path) -> None:
    """The files a wheel build reads, without the checkout's store, venv or git: the
    build hook writes its stamp and changelog into the copy, not into this tree."""
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(REPO_ROOT / name, destination / name)
    shutil.copytree(REPO_ROOT / "build_backend", destination / "build_backend")
    shutil.copytree(
        REPO_ROOT / "src" / "kyosei",
        destination / "src" / "kyosei",
        ignore=shutil.ignore_patterns("__pycache__", "_build_stamp.json", "_changelog.json"),
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH to build a wheel")
def test_the_wheel_carries_every_file_of_the_plugin_tree(tmp_path: Path) -> None:
    """The packaging check: package-data lists explicit patterns, and a
    dot-directory is the easiest one to leave out without any build saying so."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _copy_buildable_tree(tree)
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    out = tmp_path / "dist"
    run = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), str(tree)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    (wheel,) = out.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        shipped = set(archive.namelist())
    expected = {
        "kyosei/harness/claude-code/" + p.relative_to(SOURCE).as_posix()
        for p in SOURCE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert any("/.claude-plugin/" in name for name in expected)
    assert len([n for n in expected if n.endswith("/.claude-plugin/marketplace.json")]) == 1
    assert len([n for n in expected if n.endswith("/plugin/.claude-plugin/plugin.json")]) == 1
    assert sorted(expected - shipped) == []


# ---------------------------------------------------------------------------- c6


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude (the Claude Code CLI) is not on PATH, so `claude plugin validate` cannot run",
)
@pytest.mark.parametrize("target", [SOURCE, PLUGIN], ids=["marketplace", "plugin"])
def test_claude_plugin_validate_strict_passes(target: Path, tmp_path: Path) -> None:
    """Run under a scratch CLAUDE_CONFIG_DIR so the check reads and writes nothing of
    the operator's own Claude Code configuration."""
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)}
    run = subprocess.run(
        ["claude", "plugin", "validate", "--strict", str(target)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert run.returncode == 0, run.stdout + run.stderr
