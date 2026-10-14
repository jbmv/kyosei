"""KYO-596: the one `harness` item `kyo onboard --check` and `kyo doctor` read for a project
on the Claude Code plugin, in place of the hooks, skill and CLAUDE.md-section items, which
the plugin makes redundant.

Most tests run a fake `claude` that keeps Claude Code's own files under a temporary
``CLAUDE_CONFIG_DIR`` in the shapes the real one writes (``plugins/known_marketplaces.json``,
``plugins/installed_plugins.json``, ``enabledPlugins`` in ``.claude/settings.local.json``,
as of claude 2.1.296) and answers ``plugin list --json`` from them. One
test runs the real `claude` under a temporary ``CLAUDE_CONFIG_DIR`` and is skipped, saying
so, when it is not on PATH. ``$KYOSEI_HOME`` is the suite's temporary one throughout
(conftest's autouse ``store_home``).

For every state that prints a fix, a test puts a project in that state, runs the fix, and
shows the item then reads ``present`` (c3). ``doubled``'s fix is `kyo harness migrate`
(KYO-595), which this build does not have; its test does what migrate does, removing the
copies, and says so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import cmd_onboard, doctor_install
from kyosei.harness import CLAUDE_CODE_DIR
from kyosei.install_skill import CLAUDE_MD, CLAUDE_MD_BEGIN, CLAUDE_MD_END, SKILL_ASSETS

from .conftest import git_init, invoke

GIT_DIR = str(Path(shutil.which("git") or "/usr/bin/git").parent)
PLUGIN_ID = "kyosei@kyosei"

#: The fake `claude`: the subcommands `kyo harness install` and the harness item run, kept
#: in Claude Code's own files so the fallback that reads those files reads the same state.
FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path

config = Path(os.environ["CLAUDE_CONFIG_DIR"])
plugins = config / "plugins"
plugins.mkdir(parents=True, exist_ok=True)
known_path = plugins / "known_marketplaces.json"
installed_path = plugins / "installed_plugins.json"

def load(path, empty):
    return json.loads(path.read_text()) if path.is_file() else empty

def save(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2))

def set_enabled(project, value):
    path = Path(project) / ".claude" / "settings.local.json"
    document = load(path, {})
    enabled = document.setdefault("enabledPlugins", {})
    if value is None:
        enabled.pop("kyosei@kyosei", None)
    else:
        enabled["kyosei@kyosei"] = value
    save(path, document)

def enabled_in(project):
    for path in (
        Path(project) / ".claude" / "settings.local.json",
        Path(project) / ".claude" / "settings.json",
        config / "settings.json",
    ):
        value = load(path, {}).get("enabledPlugins", {}).get("kyosei@kyosei")
        if value is not None:
            return bool(value)
    return False

args = sys.argv[1:]
known = load(known_path, {})
installed = load(installed_path, {"version": 2, "plugins": {}})
records = installed["plugins"].setdefault("kyosei@kyosei", [])
cwd = os.getcwd()
with open(os.environ["KYO596_CLAUDE_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
if args[:3] == ["plugin", "marketplace", "add"]:
    known["kyosei"] = {
        "source": {"source": "directory", "path": args[3]},
        "installLocation": args[3],
    }
    save(known_path, known)
elif args[:3] == ["plugin", "marketplace", "remove"]:
    known.pop(args[3], None)
    for record in records:
        set_enabled(record["projectPath"], None)
    installed["plugins"].pop("kyosei@kyosei", None)
    save(known_path, known)
    save(installed_path, installed)
elif args[:2] == ["plugin", "install"]:
    if "kyosei" not in known:
        sys.exit("Plugin not found in marketplace")
    if not any(r.get("projectPath") == cwd for r in records):
        records.append({"scope": "local", "projectPath": cwd, "version": "0.1.0"})
    save(installed_path, installed)
    set_enabled(cwd, True)
elif args[:3] == ["plugin", "list", "--json"]:
    source = known.get("kyosei", {}).get("source", {})
    out = []
    for record in records:
        entry = {
            "id": "kyosei@kyosei",
            "scope": record["scope"],
            "enabled": record.get("projectPath") == cwd and enabled_in(cwd),
            "projectPath": record.get("projectPath"),
        }
        if source.get("source") == "directory":
            entry["readFromFolder"] = str(Path(source["path"]) / "plugin")
        out.append(entry)
    print(json.dumps(out))
else:
    sys.exit(f"fake claude: unsupported {args}")
'''


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch) -> Path:
    """No global git configuration or excludes, and Claude Code's config in tmp."""
    config = tmp_path / "git-global-config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    claude_config = tmp_path / "claude-config"
    claude_config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    return claude_config


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch) -> Path:
    """The fake `claude`, first and alone (beside git) on PATH; returns its call log."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    program = bin_dir / "claude"
    program.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    program.chmod(0o755)
    log = tmp_path / "claude.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("KYO596_CLAUDE_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{GIT_DIR}")
    return log


def git_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    result = invoke(["init", str(root), "--in-repo", "--prefix", "TST"])
    assert result.code == 0, result
    git_init(root)
    return root.resolve()


def items(root: Path) -> dict[str, dict]:
    result = invoke(["--root", str(root), "onboard", "--check", "--json"])
    assert result.code == 0, result
    (project,) = result.json["projects"]
    return {item["label"]: item for item in project["items"]}


def harness(root: Path) -> dict:
    found = items(root)
    assert "harness" in found, sorted(found)
    for label in cmd_onboard.COPIES_LABELS:
        assert label not in found, f"{label} is read beside the harness item"
    return found["harness"]


def doctor_harness(root: Path, *extra: str) -> list[str]:
    result = invoke(["--root", str(root), "doctor", "--json", *extra])
    return result.json["checks"]["harness"]["failures"]


def run_fix(root: Path, command: str) -> None:
    """Run one printed fix, as the operator would, in the project."""
    words = command.strip("`").split()
    if words[0] == "kyo":
        result = invoke(["--root", str(root), *words[1:]])
        assert result.code == 0, result
    else:
        subprocess.run(words, cwd=root, check=True, capture_output=True)


def link() -> Path:
    return Path(os.environ["KYOSEI_HOME"]) / "harness" / "claude-code"


def install_plugin(root: Path) -> None:
    result = invoke(["--root", str(root), "harness", "install", "claude-code"])
    assert result.code == 0, result
    assert harness(root)["status"] == "present"


def remove_the_copies(root: Path) -> None:
    """What `kyo harness migrate` (KYO-595) does: the copied hook entries, the skill copies
    and the CLAUDE.md section go; the plugin stays enabled."""
    settings = root / ".claude" / "settings.json"
    document = json.loads(settings.read_text(encoding="utf-8"))
    document.pop("hooks", None)
    settings.write_text(json.dumps(document), encoding="utf-8")
    for asset in SKILL_ASSETS:
        (root / asset.target).unlink(missing_ok=True)
    md = root / CLAUDE_MD
    text = md.read_text(encoding="utf-8")
    start, end = text.index(CLAUDE_MD_BEGIN), text.index(CLAUDE_MD_END) + len(CLAUDE_MD_END)
    md.write_text(text[:start] + text[end:], encoding="utf-8")


# ------------------------------------------------- c1: which item, by which route


def test_a_project_on_the_copies_reads_the_three_items_and_no_harness(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    assert invoke(["--root", str(root), "install-hooks", "--copies"]).code == 0
    found = items(root)
    assert "harness" not in found
    for label in cmd_onboard.COPIES_LABELS:
        assert found[label]["status"] == "present", found[label]
    assert doctor_harness(root) == []
    assert fake_claude.read_text(encoding="utf-8") == "", "the copies need no `claude`"


def test_a_project_with_neither_reads_harness_missing_and_runs_no_claude(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    item = harness(root)
    assert item["status"] == "missing"
    assert item["fix"].startswith("`kyo harness install claude-code`")
    assert "`kyo install-hooks --copies`" in item["fix"], (
        "the copies are offered as the alternative"
    )
    # Doctor has never called a project with no hooks a problem.
    assert doctor_harness(root) == []
    # Nothing enables the plugin, so nothing had to ask `claude`.
    assert fake_claude.read_text(encoding="utf-8") == ""
    run_fix(root, "kyo harness install claude-code")
    assert harness(root)["status"] == "present"


def test_present_reads_from_claude_and_retires_the_copy_checks_in_doctor(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    item = harness(root)
    assert item["fix"] is None
    assert str(link() / "plugin") in item["detail"]
    assert "read from `claude plugin list --json`" in item["detail"]
    result = invoke(["--root", str(root), "doctor", "--json"])
    for name in ("harness", "hook_config", "hook_config_superseded", "skill"):
        assert result.json["checks"][name]["failures"] == [], (name, result.json["checks"][name])


# ------------------------------------------------- c3: each state, and its fix


def test_missing_when_enabled_but_never_installed_and_the_fix_clears_it(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    local = root / ".claude" / "settings.local.json"
    local.parent.mkdir(exist_ok=True)
    local.write_text(json.dumps({"enabledPlugins": {PLUGIN_ID: True}}), encoding="utf-8")
    item = harness(root)
    assert item["status"] == "missing"
    assert "no kyosei@kyosei installed" in item["detail"]
    assert item["fix"].startswith("`kyo harness install claude-code`")
    assert doctor_harness(root), "an enabled plugin that does not load is a doctor problem"
    run_fix(root, "kyo harness install claude-code")
    assert harness(root)["status"] == "present"
    assert doctor_harness(root) == []


def test_behind_when_the_link_points_at_another_kyo_and_the_fix_repoints_it(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    other = tmp_path / "another-build" / "harness" / "claude-code"
    (other / "plugin").mkdir(parents=True)
    link().unlink()
    link().symlink_to(other)
    item = harness(root)
    assert item["status"] == "behind"
    assert str(other) in item["detail"]
    assert item["fix"].startswith("`kyo harness install claude-code`")
    assert any("harness is behind" in f for f in doctor_harness(root))
    run_fix(root, "kyo harness install claude-code")
    assert os.readlink(link()) == str(CLAUDE_CODE_DIR)
    assert harness(root)["status"] == "present"
    assert doctor_harness(root) == []


def test_behind_when_the_link_is_broken_and_the_fix_repoints_it(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    link().unlink()
    link().symlink_to(tmp_path / "uninstalled-build")
    item = harness(root)
    assert item["status"] == "behind"
    assert "broken" in item["detail"]
    run_fix(root, "kyo harness install claude-code")
    assert harness(root)["status"] == "present"


def _copied_fix_clears(root: Path) -> None:
    item = harness(root)
    assert item["status"] == "copied", item
    assert item["fix"].startswith("`claude plugin marketplace remove kyosei`, then `kyo")
    assert any("harness is copied" in f for f in doctor_harness(root))
    run_fix(root, "claude plugin marketplace remove kyosei")
    run_fix(root, "kyo harness install claude-code")
    assert harness(root)["status"] == "present"
    assert doctor_harness(root) == []


def test_copied_when_the_plugin_is_a_cached_copy_and_re_registering_clears_it(
    tmp_path: Path, fake_claude: Path, isolated: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    # A marketplace Claude Code fetched (a git repository, say) rather than a folder: the
    # plugin list carries no `readFromFolder`, which is what a cached copy looks like.
    known = isolated / "plugins" / "known_marketplaces.json"
    known.write_text(
        json.dumps({"kyosei": {"source": {"source": "github", "repo": "someone/kyosei"}}}),
        encoding="utf-8",
    )
    assert "cached copy" in harness(root)["detail"]
    _copied_fix_clears(root)


def test_copied_when_registered_at_another_path_and_re_registering_clears_it(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    elsewhere = tmp_path / "a-copy-of-the-marketplace"
    shutil.copytree(CLAUDE_CODE_DIR, elsewhere, symlinks=True)
    run_fix(root, f"claude plugin marketplace add {elsewhere}")
    detail = harness(root)["detail"]
    assert str(elsewhere / "plugin") in detail
    _copied_fix_clears(root)


def test_doubled_when_the_copies_remain_and_removing_them_clears_it(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    assert invoke(["--root", str(root), "install-hooks", "--copies"]).code == 0
    item = harness(root)
    assert item["status"] == "doubled"
    assert item["fix"].startswith("`kyo harness migrate`")
    assert ".claude/skills/kyosei/SKILL.md" in item["detail"]
    assert "kyo brief --compact --session-start" in item["detail"]
    result = invoke(["--root", str(root), "doctor", "--json"])
    assert any("harness is doubled" in f for f in result.json["checks"]["harness"]["failures"])
    assert result.json["checks"]["hook_config"]["failures"] == [], "harness speaks for them"
    # `kyo harness migrate` is KYO-595's, absent from this build: do what it does.
    remove_the_copies(root)
    assert harness(root)["status"] == "present"
    assert doctor_harness(root) == []


# ------------------------------------------------- c2: the sources, and saying which


def test_without_claude_on_path_it_reads_claude_codes_files_and_says_so(
    tmp_path: Path, fake_claude: Path, monkeypatch, isolated: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    monkeypatch.setenv("PATH", GIT_DIR)
    if shutil.which("claude", path=GIT_DIR) is not None:
        pytest.skip(f"a claude sits in {GIT_DIR}, beside git, so PATH cannot lack it here")
    before = fake_claude.read_text(encoding="utf-8")
    item = harness(root)
    assert item["status"] == "present"
    assert f"Claude Code's files under {isolated / 'plugins'}" in item["detail"]
    assert "claude is not on PATH" in item["detail"]
    assert fake_claude.read_text(encoding="utf-8") == before
    # The same files tell a cached copy from a folder.
    (isolated / "plugins" / "known_marketplaces.json").write_text(
        json.dumps({"kyosei": {"source": {"source": "github", "repo": "someone/kyosei"}}}),
        encoding="utf-8",
    )
    assert harness(root)["status"] == "copied"


def test_the_doctor_hook_form_reads_files_and_names_its_form(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    calls_before = fake_claude.read_text(encoding="utf-8")
    result = invoke(["--root", str(root), "doctor", "--hook", "--json"])
    assert result.json["checks"]["harness"]["failures"] == []
    assert result.json["hook_forms"]["harness"]["mode"] == "claude-code-files"
    assert fake_claude.read_text(encoding="utf-8") == calls_before, "no claude on a stop"


def test_the_enablement_comes_from_the_settings_files_by_precedence(
    tmp_path: Path, isolated: Path
) -> None:
    root = tmp_path / "r"
    (root / ".claude").mkdir(parents=True)
    env = {"CLAUDE_CONFIG_DIR": str(isolated)}
    assert doctor_install.plugin_enablement(root, env)[0] is None
    (isolated / "settings.json").write_text(
        json.dumps({"enabledPlugins": {PLUGIN_ID: True}}), encoding="utf-8"
    )
    assert doctor_install.plugin_enablement(root, env)[:2] == (
        True,
        str(isolated / "settings.json"),
    )
    (root / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {PLUGIN_ID: False}}), encoding="utf-8"
    )
    assert doctor_install.plugin_enablement(root, env)[0] is False


def test_a_worktree_reads_the_main_checkouts_local_scope(
    tmp_path: Path, fake_claude: Path
) -> None:
    root = git_project(tmp_path)
    install_plugin(root)
    tree = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", "-b", "w", str(tree)], check=True
    )
    enabled, where, _unreadable = doctor_install.plugin_enablement(tree, os.environ)
    assert enabled is True
    assert where == str(root / ".claude" / "settings.local.json")


# ------------------------------------------------------------- the real claude


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="`claude` is not on PATH, so its own `plugin list --json` cannot be read here",
)
def test_the_real_claude_missing_present_copied_and_back(
    tmp_path: Path, isolated: Path
) -> None:
    root = git_project(tmp_path)
    assert harness(root)["status"] == "missing"
    run_fix(root, "kyo harness install claude-code")
    item = harness(root)
    assert item["status"] == "present", item
    assert "read from `claude plugin list --json`" in item["detail"]
    elsewhere = tmp_path / "a-copy-of-the-marketplace"
    shutil.copytree(CLAUDE_CODE_DIR, elsewhere, symlinks=True)
    for args in (
        ["plugin", "marketplace", "remove", "kyosei"],
        ["plugin", "marketplace", "add", str(elsewhere)],
        ["plugin", "install", PLUGIN_ID, "--scope", "local"],
    ):
        subprocess.run(
            ["claude", *args], cwd=root, check=True, capture_output=True, timeout=120,
            stdin=subprocess.DEVNULL,
        )
    item = harness(root)
    assert item["status"] == "copied", item
    run_fix(root, "claude plugin marketplace remove kyosei")
    run_fix(root, "kyo harness install claude-code")
    assert harness(root)["status"] == "present"
