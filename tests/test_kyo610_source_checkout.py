"""KYO-610: `kyo harness migrate` in the repository the Kyosei package is built from, and what
`kyo doctor`, `kyo onboard --check` and the session-start brief say there afterwards.

KYO-595.c6 refused the migration in the source checkout, whose ``.claude/skills/kyosei/``
files are mirrors of its own source rather than copies an installed build wrote. This
repository now moves onto the plugin like a consumer (KYO-610), so the command treats a
mirror as unedited when it equals the installed form of this checkout's
source, and a hook entry as a copy when this checkout's own fragment source installs it.

Every test builds a temporary checkout shaped like the source repository: the skill's
source under ``src/kyosei/harness/claude-code/plugin/skills/kyosei/`` and the hook fragments
under ``src/kyosei/defaults/hooks/``, each a line AHEAD of the running build's packaged copy,
so a comparison against the running build instead of against the checkout would leave the
mirror and the edited hook in place and fail these tests. Claude Code is the fake `claude`
of ``tests/test_harness_check.py``, under a temporary ``CLAUDE_CONFIG_DIR``; ``$KYOSEI_HOME``
is the suite's temporary one (conftest's autouse ``store_home``).
"""

# ruff: noqa: F811 -- the KYO-596 fixtures are imported by name, and pytest injects them

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kyosei.cmd_harness_migrate import harness_mode
from kyosei.install_hooks import HOOK_INSTALL_TARGETS, _load_hook_fragment, _strip_comment_fields
from kyosei.install_skill import (
    CLAUDE_MD_SECTION,
    SKILL_ASSETS,
    SKILL_REFERENCE_ASSET,
    installed_skill_text,
    packaged_skill_asset_text,
)
from kyosei.scaffold import HOOKS_SOURCE_SUBDIR, PACKAGED_ASSET_SOURCE_DIR, defaults_dir

from .conftest import invoke
from .test_harness_check import (  # noqa: F401 -- fixtures and helpers shared with KYO-596
    fake_claude,
    git_project,
    harness,
    isolated,
)

#: The line each of this checkout's skill sources carries and the running build's lacks.
AHEAD = "\nA line only this checkout's source carries (KYO-610's fixture).\n"

#: The hook command this checkout's fragment source carries in place of the build's.
BUILD_STOP = "kyo sweep --hook && kyo doctor --hook"
AHEAD_STOP = "kyo sweep --hook && kyo doctor --hook && kyo sessions --hook"

HAND_WRITTEN = "# Kyosei\n\nWritten by hand, outside the section.\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def source_checkout(tmp_path: Path) -> Path:
    """A git project shaped like Kyosei's source repository, on the copies: the skill
    mirrors, the copied hooks and the CLAUDE.md section, all committed."""
    root = git_project(tmp_path)
    for asset in SKILL_ASSETS:
        source = root / asset.source_path
        source.parent.mkdir(parents=True, exist_ok=True)
        text = packaged_skill_asset_text(asset) + AHEAD
        source.write_text(text, encoding="utf-8")
        mirror = root / asset.target
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(installed_skill_text(text), encoding="utf-8")
    packaged_hooks = defaults_dir()
    assert packaged_hooks is not None
    hooks_source = root / PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR
    shutil.copytree(packaged_hooks / HOOKS_SOURCE_SUBDIR, hooks_source)
    stop = hooks_source / "stop.json"
    stop_text = stop.read_text(encoding="utf-8")
    assert stop_text.count(f'; {BUILD_STOP}"') == 1, "the fixture's premise moved"
    stop.write_text(stop_text.replace(f'; {BUILD_STOP}"', f'; {AHEAD_STOP}"'), encoding="utf-8")
    hooks: dict[str, list] = {}
    for event, name in HOOK_INSTALL_TARGETS:
        for group in _load_hook_fragment(name, hooks_source):
            hooks.setdefault(event, []).append(
                {**group, "hooks": _strip_comment_fields(group["hooks"])}
            )
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"hooks": hooks, "permissions": {"allow": ["Bash(make lint)"]}}, indent=2),
        encoding="utf-8",
    )
    (root / "CLAUDE.md").write_text(f"{HAND_WRITTEN}\n{CLAUDE_MD_SECTION}\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "source checkout on the copies")
    return root


def migrate(root: Path, *extra: str):
    return invoke(["--root", str(root), "harness", "migrate", *extra])


def hook_commands(root: Path) -> list[str]:
    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return [
        hook["command"]
        for groups in settings.get("hooks", {}).values()
        for group in groups
        for hook in group["hooks"]
    ]


# ------------------------------------------------------- c1: the migration, in place of c6


def test_the_premise_the_running_build_alone_would_call_these_edited(tmp_path: Path) -> None:
    """Without KYO-610 the mirrors and the ahead hook match nothing the running build
    installs: the comparison has to be against the checkout's own source."""
    root = source_checkout(tmp_path)
    for asset in SKILL_ASSETS:
        assert (root / asset.target).read_text(encoding="utf-8") != installed_skill_text(
            packaged_skill_asset_text(asset)
        )
    assert any(command.endswith(AHEAD_STOP) for command in hook_commands(root))
    assert harness_mode(root) == "copies"


def test_migrate_in_the_source_checkout_enables_the_plugin_and_removes_the_mirror(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = source_checkout(tmp_path)
    commits_before = git(root, "rev-list", "--count", "HEAD")
    result = migrate(root, "--json")
    assert result.code == 0, result
    data = result.json
    assert data["plugin_enabled"] is True
    assert data["left"] == [], data["left"]
    # The plugin is enabled at local scope, as anywhere else.
    local = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert local["enabledPlugins"]["kyosei@kyosei"] is True
    assert "plugin install kyosei@kyosei --scope local" in fake_claude.read_text(
        encoding="utf-8"
    )
    # Hooks: every Kyosei entry gone, the ahead one included; the operator's key kept.
    settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"permissions": {"allow": ["Bash(make lint)"]}}
    removed = [entry["what"] for entry in data["removed"]]
    assert any(what.endswith(AHEAD_STOP) for what in removed)
    # The mirror: both files and the emptied directories gone.
    for asset in SKILL_ASSETS:
        assert not (root / asset.target).exists()
        assert (root / asset.source_path).is_file(), "the source is never touched"
    assert not (root / ".claude" / "skills").exists()
    # CLAUDE.md: the section gone, the hand-written text kept byte for byte.
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == HAND_WRITTEN
    # Nothing committed; the note names the build's age and the way out.
    assert git(root, "rev-list", "--count", "HEAD") == commits_before
    note = data["source_checkout"]
    assert "the repository the Kyosei package is built from" in note
    assert "`claude plugin disable kyosei@kyosei` in this repository" in note
    assert harness_mode(root) == "plugin"


def test_the_note_is_printed_in_the_source_checkout_and_absent_elsewhere(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = source_checkout(tmp_path)
    result = migrate(root)
    assert result.code == 0, result
    assert "source checkout:" in result.out
    assert "`claude plugin disable kyosei@kyosei`" in result.out
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    other = git_project(consumer)
    assert invoke(["--root", str(other), "install-hooks", "--copies"]).code == 0
    elsewhere = migrate(other, "--json")
    assert elsewhere.code == 0, elsewhere
    assert elsewhere.json["source_checkout"] is None
    assert "source checkout:" not in migrate(other).out


def test_an_edited_mirror_is_left_and_named(tmp_path: Path, fake_claude: Path) -> None:
    root = source_checkout(tmp_path)
    reference = root / SKILL_REFERENCE_ASSET.target
    reference.write_text(reference.read_text(encoding="utf-8") + "my note\n", encoding="utf-8")
    result = migrate(root, "--json")
    assert result.code == 0, result
    assert reference.is_file()
    (left,) = result.json["left"]
    assert left["path"] == SKILL_REFERENCE_ASSET.target
    assert f"it differs from {SKILL_REFERENCE_ASSET.source_path}" in left["why"]
    assert "carry any edit of the copy into the source and delete the copy" in left["why"]
    # The remedy clears it: with the copy deleted, a second run leaves nothing but the
    # advisory pre-push hook, which the first run wrote (KYO-612) and every run names.
    reference.unlink()
    again = migrate(root, "--json")
    assert again.code == 0, again
    assert [entry["what"] for entry in again.json["left"]] == ["the advisory git pre-push hook"]


# ------------------------------------------------ c3: doctor, onboard and brief afterwards


def test_on_the_plugin_the_source_checkout_hears_no_copy_finding_and_still_the_build_lag(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = source_checkout(tmp_path)
    assert migrate(root).code == 0
    # onboard --check: the harness item, reporting the plugin, and no copy items.
    item = harness(root)
    assert item["status"] == "present", item
    # doctor: nothing from the copy checks, nothing from harness, the lag notice kept.
    checks = invoke(["--root", str(root), "doctor", "--json"]).json["checks"]
    for name in ("harness", "hook_config", "hook_config_superseded", "skill", "skill_superseded"):
        assert checks[name]["failures"] == [], (name, checks[name])
    lag = checks["installed_build_lag"]["failures"]
    assert any("/kyosei skill" in line for line in lag), lag
    assert any("hook templates" in line for line in lag), lag
    # brief: no skill or hooks finding, and the build's age still said.
    brief = invoke(["--root", str(root), "brief", "--json", "--session-start"])
    assert brief.code == 0, brief
    assets = {finding["asset"] for finding in brief.json["asset_drift"]}
    assert "skill" not in assets and "hooks" not in assets, brief.json["asset_drift"]
    assert "build" in assets
    lines = invoke(["--root", str(root), "brief", "--session-start"]).out.splitlines()
    assert not [line for line in lines if line.startswith(("  skill:", "  hooks:"))]
    assert any(line.startswith("  build:") for line in lines)
    # The lag notice names `kyo install-skill` only to forbid it, never as the remedy.
    remedies = ("run `kyo install-skill`", "run `kyo install-hooks`")
    assert not [line for line in lines if any(remedy in line for remedy in remedies)]
    assert not [line for line in lines if "Kyosei section" in line]
