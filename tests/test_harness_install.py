"""KYO-594: `kyo harness install claude-code` -- the stable path, the marketplace
registration, and the local-scope enablement.

Every test here writes the link under the test's own ``$KYOSEI_HOME`` (conftest's autouse
``store_home``). Most run a stub `claude` that records its arguments and does nothing else.
The one test that runs the real `claude` sets ``CLAUDE_CONFIG_DIR`` to a temporary
directory, leaves ``HOME`` alone, and is skipped, saying so, when `claude` is not on PATH.
Git's global configuration and global excludes are pointed at empty temporary files, since
the machine's own excludes file may already ignore ``.claude/settings.local.json``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import cmd_harness
from kyosei.harness import CLAUDE_CODE_DIR

from .conftest import git_init, invoke

STUB_CLAUDE = """#!/bin/sh
printf '%s\\n' "$(pwd -P)|$CLAUDE_CONFIG_DIR|$*" >> "$KYO594_CLAUDE_LOG"
"""

GIT_DIR = str(Path(shutil.which("git") or "/usr/bin/git").parent)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch) -> Path:
    """No global git configuration and no global excludes file for this test."""
    config = tmp_path / "git-global-config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg


@pytest.fixture
def stub_claude(tmp_path: Path, monkeypatch) -> Path:
    """A `claude` first on PATH that appends ``cwd|CLAUDE_CONFIG_DIR|args`` to a log."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    program = bin_dir / "claude"
    program.write_text(STUB_CLAUDE, encoding="utf-8")
    program.chmod(0o755)
    log = tmp_path / "claude.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("KYO594_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{GIT_DIR}")
    return log


def calls(log: Path) -> list[tuple[str, str]]:
    """``(cwd, args)`` for each stub `claude` run."""
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        cwd, _config, args = line.split("|", 2)
        out.append((cwd, args))
    return out


def link(store_home: Path) -> Path:
    return store_home / "harness" / "claude-code"


def git_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    result = invoke(["init", str(root), "--in-repo", "--prefix", "TST"])
    assert result.code == 0, result
    git_init(root)
    return root.resolve()


def install(root: Path | None = None, *extra: str):
    args = ["harness", "install", "claude-code", *extra]
    if root is not None:
        args = ["--root", str(root), *args]
    return invoke(args)


# ------------------------------------------------------------------ c1: link and register


def test_it_links_the_running_package_and_registers_that_path(
    tmp_path: Path, store_home: Path, stub_claude: Path, monkeypatch
) -> None:
    elsewhere = tmp_path / "not-a-project"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = install()
    assert result.code == 0, result
    path = link(store_home)
    assert path.is_symlink()
    assert os.readlink(path) == str(CLAUDE_CODE_DIR)
    assert (path / ".claude-plugin" / "marketplace.json").is_file()
    assert calls(stub_claude) == [(str(path.parent.resolve()), f"plugin marketplace add {path}")]
    assert "not enabled anywhere yet" in result.out


def test_an_existing_link_is_repointed_at_the_running_package(
    tmp_path: Path, store_home: Path, stub_claude: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "another-build"
    other.mkdir()
    path = link(store_home)
    path.parent.mkdir(parents=True)
    path.symlink_to(other)
    result = invoke(["harness", "install", "claude-code", "--json"])
    assert result.code == 0, result
    assert os.readlink(path) == str(CLAUDE_CODE_DIR)
    assert result.json["repointed_from"] == str(other)
    assert result.json["marketplace_registered"] is True
    again = invoke(["harness", "install", "claude-code", "--json"])
    assert again.json["repointed_from"] is None


def test_a_directory_in_the_links_place_is_refused_and_moving_it_aside_clears_it(
    tmp_path: Path, store_home: Path, stub_claude: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = link(store_home)
    path.mkdir(parents=True)
    refused = install()
    assert refused.code != 0
    assert "move it aside" in refused.err
    assert calls(stub_claude) == []
    path.rename(path.with_name("claude-code.old"))
    cleared = install()
    assert cleared.code == 0, cleared
    assert os.readlink(path) == str(CLAUDE_CODE_DIR)


def test_no_other_module_in_the_package_creates_a_symbolic_link() -> None:
    """c1's "no other kyo command writes the link": the link is a symbolic link, and the
    only code in the package that creates one is :func:`kyosei.cmd_harness.write_link`."""
    package = Path(cmd_harness.__file__).parent
    pattern = re.compile(r"\bsymlink_to\(|\bos\.symlink\(|\bhardlink_to\(")
    writers = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    )
    assert writers == ["cmd_harness.py"]


# ------------------------------------------------------------- c2: enable at local scope


def test_in_a_project_it_ignores_the_local_settings_then_enables_the_plugin(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    root = git_project(tmp_path)
    result = install(root)
    assert result.code == 0, result
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/settings.local.json\n" in gitignore
    assert "gitignore: added .claude/settings.local.json" in result.out
    assert (
        subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", ".claude/settings.local.json"]
        ).returncode
        == 0
    )
    assert calls(stub_claude) == [
        (str(link(store_home).parent.resolve()), f"plugin marketplace add {link(store_home)}"),
        (str(root), "plugin install kyosei@kyosei --scope local"),
    ]
    again = install(root)
    assert again.code == 0, again
    assert "gitignore:" not in again.out
    assert (root / ".gitignore").read_text(encoding="utf-8") == gitignore


def test_a_path_git_already_ignores_leaves_the_gitignore_alone(
    tmp_path: Path, stub_claude: Path, isolated_git: Path
) -> None:
    (isolated_git / "git").mkdir()
    (isolated_git / "git" / "ignore").write_text(
        "**/.claude/settings.local.json\n", encoding="utf-8"
    )
    root = git_project(tmp_path)
    before = (root / ".gitignore").read_text(encoding="utf-8")
    result = install(root)
    assert result.code == 0, result
    assert (root / ".gitignore").read_text(encoding="utf-8") == before
    assert "gitignore:" not in result.out
    assert calls(stub_claude)[-1] == (str(root), "plugin install kyosei@kyosei --scope local")


def test_a_rule_that_re_includes_the_file_refuses_and_removing_it_clears_it(
    tmp_path: Path, stub_claude: Path
) -> None:
    root = git_project(tmp_path)
    (root / ".claude").mkdir(exist_ok=True)
    reinclude = root / ".claude" / ".gitignore"
    reinclude.write_text("!settings.local.json\n", encoding="utf-8")
    refused = install(root)
    assert refused.code != 0
    assert "remove the pattern that re-includes it" in refused.err
    assert not any("plugin install" in args for _cwd, args in calls(stub_claude))
    reinclude.unlink()
    cleared = install(root)
    assert cleared.code == 0, cleared
    assert calls(stub_claude)[-1] == (str(root), "plugin install kyosei@kyosei --scope local")


def test_a_tracked_local_settings_file_refuses_and_untracking_it_clears_it(
    tmp_path: Path, stub_claude: Path
) -> None:
    root = git_project(tmp_path)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-f", ".claude/settings.local.json"], check=True)
    refused = install(root)
    assert refused.code != 0
    assert "git rm --cached .claude/settings.local.json" in refused.err
    subprocess.run(
        ["git", "-C", str(root), "rm", "-q", "--cached", ".claude/settings.local.json"],
        check=True,
    )
    cleared = install(root)
    assert cleared.code == 0, cleared
    assert calls(stub_claude)[-1] == (str(root), "plugin install kyosei@kyosei --scope local")


# ------------------------------------------------------------------- c3: no claude on PATH


def test_without_claude_it_writes_the_link_prints_the_commands_and_exits_0(
    tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PATH", GIT_DIR)
    if shutil.which("claude", path=GIT_DIR) is not None:
        pytest.skip(f"a claude sits in {GIT_DIR}, beside git, so PATH cannot lack it here")
    root = git_project(tmp_path)
    result = install(root)
    assert result.code == 0, result
    path = link(store_home)
    assert os.readlink(path) == str(CLAUDE_CODE_DIR)
    assert "claude is not on PATH" in result.out
    assert f"  claude plugin marketplace add {path}" in result.out
    assert f"  cd {root} && claude plugin install kyosei@kyosei --scope local" in result.out
    data = invoke(["--root", str(root), "harness", "install", "claude-code", "--json"]).json
    assert data["claude"] is None
    assert data["marketplace_registered"] is False
    assert data["plugin_enabled"] is False
    assert len(data["commands_to_run"]) == 2


# ------------------------------------------------------------------- c5: copied hooks


def test_copied_hooks_register_and_link_but_do_not_enable_and_name_migrate(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    root = git_project(tmp_path)
    assert invoke(["--root", str(root), "install-hooks", "--copies"]).code == 0
    result = install(root)
    assert result.code == 0, result
    assert os.readlink(link(store_home)) == str(CLAUDE_CODE_DIR)
    assert calls(stub_claude) == [
        (str(link(store_home).parent.resolve()), f"plugin marketplace add {link(store_home)}")
    ]
    assert "NOT enabled" in result.out
    assert f"next: run `kyo harness migrate` in {root}" in result.out
    assert "kyo brief --compact --session-start" in result.out
    # KYO-595.c5: KYO-594's manual fallback is gone now that migrate exists.
    assert "by hand" not in result.out

    # The remedy it names clears it: migrate removes the copies and enables the plugin,
    # after which install enables it again with nothing in the way.
    migrated = invoke(["--root", str(root), "harness", "migrate"])
    assert migrated.code == 0, migrated
    assert calls(stub_claude)[-1] == (str(root), "plugin install kyosei@kyosei --scope local")
    cleared = install(root)
    assert cleared.code == 0, cleared
    assert "NOT enabled" not in cleared.out
    assert calls(stub_claude)[-1] == (str(root), "plugin install kyosei@kyosei --scope local")


def test_a_superseded_hook_line_counts_as_a_copy(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / ".claude").mkdir(parents=True)
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
    assert cmd_harness.copied_hook_commands(root) == ["kyo doctor && kyo sweep"]


def test_an_unrelated_hook_is_not_a_copy(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "make lint"}]}]}}),
        encoding="utf-8",
    )
    assert cmd_harness.copied_hook_commands(root) == []


def test_an_unreadable_settings_file_refuses_before_writing_and_fixing_it_clears_it(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    root = git_project(tmp_path)
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text("{not json", encoding="utf-8")
    refused = install(root)
    assert refused.code != 0
    assert "correct the file" in refused.err
    assert not link(store_home).is_symlink()
    assert calls(stub_claude) == []
    settings.write_text("{}\n", encoding="utf-8")
    cleared = install(root)
    assert cleared.code == 0, cleared
    assert calls(stub_claude)[-1] == (str(root), "plugin install kyosei@kyosei --scope local")


# ------------------------------------------- KYO-612: the git pre-push hook on this route


def _kyo_on_path(stub_bin: Path) -> None:
    """A `kyo` beside the stub `claude` that runs this tree's package, as an installed
    `kyo` would run when git invokes the hook."""
    shim = stub_bin / "kyo"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m kyosei "$@"\n', encoding="utf-8")
    shim.chmod(0o755)


def test_a_hand_typed_push_after_a_plugin_install_prints_the_kyo60_warning(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    """KYO-612.c1. In an in-repo project set up by `kyo harness install claude-code` and
    no copies, the advisory hook is written, and a real `git push` that would publish the
    store prints the warning and still succeeds."""
    from kyosei.install_hooks import PRE_PUSH_MARKER

    root = git_project(tmp_path)
    result = install(root, "--json")
    assert result.code == 0, result
    hook = root / ".git" / "hooks" / "pre-push"
    assert hook.is_file(), result.out
    assert PRE_PUSH_MARKER in hook.read_text(encoding="utf-8")
    assert hook.stat().st_mode & 0o111
    assert result.json["pre_push"]["changes"] == [
        f"git pre-push: wrote {hook} (advisory; `git push --no-verify` skips it, and "
        ".git/hooks is not versioned, so a fresh clone will not have it)"
    ]
    assert not (root / ".claude" / "settings.json").exists(), "no copies were written"

    _kyo_on_path(tmp_path / "stub-bin")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    # A person's shell: the stub `claude` and the `kyo` shim, git, and the system tools.
    shell_path = os.pathsep.join([str(tmp_path / "stub-bin"), GIT_DIR, "/usr/bin", "/bin"])
    pushed = subprocess.run(
        ["git", "-C", str(root), "push", str(remote), "HEAD:main"],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": shell_path},
    )
    assert pushed.returncode == 0, pushed.stderr
    # git hands a pre-push hook its own stdout (not stderr, as for most hooks); a person
    # at a terminal sees both.
    shown = pushed.stdout + pushed.stderr
    assert "would publish this project's Kyosei store" in shown, shown

    again = install(root, "--json")
    assert again.json["pre_push"] == {"changes": [], "notes": []}


def test_an_operators_own_pre_push_hook_is_kept_and_the_line_to_add_is_named(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    """KYO-612.c2: never overwritten on the plugin route either; the note names
    PRE_PUSH_MANUAL_LINE, the same guarded line the copies name."""
    from kyosei.install_hooks import PRE_PUSH_MANUAL_LINE

    root = git_project(tmp_path)
    hook = root / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    result = install(root, "--json")
    assert result.code == 0, result
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    notes = result.json["pre_push"]["notes"]
    assert len(notes) == 1 and "left alone" in notes[0]
    assert PRE_PUSH_MANUAL_LINE in notes[0]
    text = install(root)
    assert f"! {notes[0]}" in text.out


def test_without_claude_the_pre_push_hook_is_still_written(
    tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    """The hook is git's, not Claude Code's, so a machine without `claude` gets it too."""
    monkeypatch.setenv("PATH", GIT_DIR)
    if shutil.which("claude", path=GIT_DIR) is not None:
        pytest.skip(f"a claude sits in {GIT_DIR}, beside git, so PATH cannot lack it here")
    root = git_project(tmp_path)
    assert install(root).code == 0
    assert (root / ".git" / "hooks" / "pre-push").is_file()


# ------------------------------- KYO-613: kyo init's setup-incomplete CLAUDE.md section


def test_enabling_the_plugin_removes_inits_section_and_keeps_the_operators_lines(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    """KYO-613.c2. `kyo init` wrote the section; enabling the plugin removes it, with no
    setup agent deleting it by hand. A file that held only the section goes with it."""
    from kyosei.install_skill import CLAUDE_MD_SETUP_INCOMPLETE_SECTION

    root = git_project(tmp_path)
    claude_md = root / "CLAUDE.md"
    assert claude_md.read_text(encoding="utf-8") == CLAUDE_MD_SETUP_INCOMPLETE_SECTION + "\n"
    result = install(root, "--json")
    assert result.code == 0, result
    assert result.json["claude_md_section_removed"] is True
    assert not claude_md.exists()

    other = git_project(tmp_path / "second")
    md = other / "CLAUDE.md"
    md.write_text(
        "# Mine\n\nHouse rules.\n\n" + CLAUDE_MD_SETUP_INCOMPLETE_SECTION + "\n",
        encoding="utf-8",
    )
    shown = install(other)
    assert shown.code == 0, shown
    assert md.read_text(encoding="utf-8") == "# Mine\n\nHouse rules.\n"
    assert "CLAUDE.md: removed the setup-incomplete section" in shown.out
    assert invoke(
        ["--root", str(other), "harness", "install", "claude-code", "--json"]
    ).json["claude_md_section_removed"] is False


def test_an_earlier_builds_init_section_is_removed_and_any_other_section_is_kept(
    tmp_path: Path, store_home: Path, stub_claude: Path
) -> None:
    from kyosei.install_skill import (
        CLAUDE_MD_BEGIN,
        CLAUDE_MD_END,
        CLAUDE_MD_SECTION,
        SUPERSEDED_SETUP_INCOMPLETE_BODIES,
    )

    root = git_project(tmp_path)
    md = root / "CLAUDE.md"
    old = f"{CLAUDE_MD_BEGIN}\n{SUPERSEDED_SETUP_INCOMPLETE_BODIES[0]}\n{CLAUDE_MD_END}\n"
    md.write_text("# Mine\n\n" + old, encoding="utf-8")
    assert install(root).code == 0
    assert md.read_text(encoding="utf-8") == "# Mine\n"

    for kept in (
        f"# Mine\n\n{CLAUDE_MD_SECTION}\n",
        f"# Mine\n\n{old.replace('incomplete', 'incomplete (edited)')}",
    ):
        md.write_text(kept, encoding="utf-8")
        result = install(root, "--json")
        assert result.json["claude_md_section_removed"] is False
        assert md.read_text(encoding="utf-8") == kept


def test_without_claude_inits_section_stays_and_the_output_says_how_it_goes(
    tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PATH", GIT_DIR)
    if shutil.which("claude", path=GIT_DIR) is not None:
        pytest.skip(f"a claude sits in {GIT_DIR}, beside git, so PATH cannot lack it here")
    root = git_project(tmp_path)
    result = install(root)
    assert result.code == 0, result
    assert (root / "CLAUDE.md").is_file()
    assert "CLAUDE.md keeps the setup-incomplete section `kyo init` wrote" in result.out


# ---------------------------------------------------------------------- the real claude


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="`claude` is not on PATH, so the real registration cannot be exercised here",
)
def test_the_real_claude_loads_the_plugin_in_place_through_the_link(
    tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    root = git_project(tmp_path)
    result = install(root)
    assert result.code == 0, result
    path = link(store_home)
    listed = subprocess.run(
        ["claude", "plugin", "list", "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    plugins = {entry["id"]: entry for entry in json.loads(listed.stdout)}
    assert plugins["kyosei@kyosei"]["scope"] == "local"
    assert plugins["kyosei@kyosei"]["readFromFolder"] == str(path / "plugin")
    local = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert local["enabledPlugins"]["kyosei@kyosei"] is True
    assert not (Path.home() / ".claude" / "plugins" / "known_marketplaces.json").is_file() or (
        str(path)
        not in (Path.home() / ".claude" / "plugins" / "known_marketplaces.json").read_text(
            encoding="utf-8"
        )
    )
