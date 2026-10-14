"""KYO-591: every packaged hook command is silent, exit 0, outside a Kyosei project.

A plugin's hooks fire in every session where it is enabled, so a hook that refuses or
prints where there is no Kyosei project is noise in every other repository on the machine.
Two layers decide it:

* with `kyo` on PATH, `kyo` decides, by its own project resolution
  (:func:`kyosei.cmd_check_build.hook_outside_any_project`) -- which includes KYO-580's
  read-through from a linked worktree to the main checkout's store link, the case a shell
  walk alone misses (c1, c2);
* with `kyo` missing, the PATH guard decides by a marker walk, and speaks (exit 127) only
  where it finds `kyosei/config.yml` or `.kyosei` (c3).

Every packaged command is read from the fragments themselves, so a command added later is
covered without this file changing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from kyosei.cli import build_parser
from kyosei.cmd_check_build import QUIET_OUTSIDE_PROJECT, hook_outside_any_project
from kyosei.config import STORE_LINK_NAME
from kyosei.install_hooks import PATH_GUARD_MESSAGE, PATH_GUARD_PREFIX, hook_action

from .conftest import git_init, git_worktree, invoke

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = REPO_ROOT / "src" / "kyosei" / "defaults" / "hooks"


def packaged_commands() -> list[tuple[str, str, str]]:
    """``(fragment, event, command)`` for every hook in every packaged fragment."""
    found = []
    for path in sorted(FRAGMENTS.glob("*.json")):
        fragment = json.loads(path.read_text(encoding="utf-8"))
        for event, groups in fragment.items():
            for group in groups:
                for hook in group["hooks"]:
                    found.append((path.name, event, hook["command"]))
    return found


def payload_for(event: str, cwd: Path) -> str:
    """A payload shaped like the one Claude Code sends on ``event``. The PreToolUse one
    is a push, the input that makes `kyo check-push` do the most work."""
    base = {"session_id": "s-591", "cwd": str(cwd), "hook_event_name": event}
    if event == "PreToolUse":
        base.update(tool_name="Bash", tool_input={"command": "git push origin main"})
    elif event in ("Stop", "SubagentStop"):
        base.update(stop_hook_active=False, agent_id="a-591")
    elif event == "UserPromptSubmit":
        base.update(prompt="hello")
    elif event == "SessionStart":
        base.update(source="startup")
    return json.dumps(base)


@pytest.fixture
def kyo_bin(tmp_path: Path) -> Path:
    """A directory holding a `kyo` that runs THIS tree's package, for a PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "kyo"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m kyosei "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return bin_dir


def run_hook(
    command: str, *, cwd: Path, path: str, home: Path, stdin: str = "{}", project_dir=None
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": path, "HOME": str(home), "KYOSEI_HOME": str(home / "kyosei-home")}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _not_a_repository(directory: Path) -> bool:
    found = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=directory, capture_output=True, text=True
    )
    return found.returncode != 0


# ------------------------------------------------------------------ c1: kyo on PATH


def test_every_packaged_command_is_silent_outside_any_project(
    tmp_path: Path, kyo_bin: Path
) -> None:
    """KYO-591.c1: in an empty directory outside any git repository, with a temporary
    KYOSEI_HOME, each packaged command exits 0 with empty stdout and stderr."""
    commands = packaged_commands()
    assert len(commands) >= 8, commands
    home = tmp_path / "home"
    home.mkdir()
    for index, (name, event, command) in enumerate(commands):
        empty = tmp_path / f"empty-{index}"
        empty.mkdir()
        assert _not_a_repository(empty), "the fixture must sit outside every git repository"
        result = run_hook(
            command,
            cwd=empty,
            path=f"{kyo_bin}:/usr/bin:/bin",
            home=home,
            stdin=payload_for(event, empty),
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), (
            f"{name} ({event}): {hook_action(command)!r} -> {result!r}"
        )
        # Nothing was written there either: `kyo sessions --hook` would otherwise fold
        # the session into `<cwd>/kyosei/sessions.json`.
        assert list(empty.iterdir()) == [], f"{name}: wrote {list(empty.iterdir())}"


def _hook_invocations() -> list[list[str]]:
    """Every `kyo` invocation in every packaged command, as argv lists."""
    found = []
    for _name, _event, command in packaged_commands():
        for part in hook_action(command).split("&&"):
            words = part.split()
            assert words[0] == "kyo", command
            found.append(words[1:])
    return found


def test_every_packaged_invocation_is_a_hook_mode_kyo_resolves(
    tmp_path: Path, monkeypatch
) -> None:
    """The decision itself, per invocation: every packaged `kyo` call but `check-push`
    is a hook mode in :data:`QUIET_OUTSIDE_PROJECT`, and is judged outside a project by
    `kyo`'s own resolution. `check-push` keeps judging pushes (KYO-409.c3) and is held
    silent by the test above instead."""
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    for argv in _hook_invocations():
        args = parser.parse_args(argv)
        if args.command == "check-push":
            assert hook_outside_any_project(args) is False
            continue
        assert args.command in QUIET_OUTSIDE_PROJECT, argv
        assert hook_outside_any_project(args) is True, argv
        # The same invocation run by hand (its hook flag off) is not silenced.
        hand_run = Namespace(**{**vars(args), QUIET_OUTSIDE_PROJECT[args.command]: False})
        assert hook_outside_any_project(hand_run) is False, argv


def test_a_hand_run_outside_a_project_still_refuses(tmp_path: Path, monkeypatch) -> None:
    """Only the hook's own invocation is silent: `kyo sweep` by hand in the wrong
    directory still says there is no project here."""
    monkeypatch.chdir(tmp_path)
    assert invoke(["sweep", "--hook"]).code == 0
    hand = invoke(["sweep"])
    assert hand.code != 0
    assert "no Kyosei project found" in hand.err


# ------------------------------------------------- c2: kyo's own resolution, KYO-580


def _split_project_with_outside_worktree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A split project whose `.kyosei` link is untracked (as `kyo init` leaves it), and
    a linked worktree OUTSIDE the store, which therefore has no link of its own and no
    marker anywhere above it: only KYO-580's read-through finds the project."""
    code = tmp_path / "code"
    code.mkdir()
    (code / "src").mkdir()
    (code / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    git_init(code)
    made = invoke(["init", "--defaults", str(code), "--prefix", "TST", "--json"])
    assert made.code == 0, made
    store = Path(made.json["store_root"])
    tree = tmp_path / "elsewhere" / "tree"
    git_worktree(code, tree)
    assert not (tree / STORE_LINK_NAME).exists()
    return code, store, tree


def test_a_split_worktree_reading_through_the_main_checkout_still_runs_every_hook(
    tmp_path: Path, monkeypatch, kyo_bin: Path
) -> None:
    """KYO-591.c2: no marker at or above the worktree, so a shell walk from there would
    call it "no project"; `kyo`'s own resolution reads the main checkout's link and the
    hooks run as before."""
    _code, _store, tree = _split_project_with_outside_worktree(tmp_path)
    for directory in tree.parents:
        assert not (directory / "kyosei" / "config.yml").exists(), directory
        assert not (directory / STORE_LINK_NAME).exists(), directory

    monkeypatch.chdir(tree)
    parser = build_parser()
    for argv in _hook_invocations():
        args = parser.parse_args(argv)
        assert hook_outside_any_project(args) is False, argv

    # And for real, through the packaged commands: the session-start brief still
    # prints its brief, and the SubagentStop guard still names the tree it read.
    # `run_hook` puts KYOSEI_HOME at home/kyosei-home, which is where the store_home
    # fixture put the store this project was initialised into.
    env_home = Path(os.environ["KYOSEI_HOME"]).parent
    brief = run_hook(
        next(c for _n, _e, c in packaged_commands() if "kyo brief" in c),
        cwd=tree,
        path=f"{kyo_bin}:/usr/bin:/bin",
        home=env_home,
        stdin=payload_for("SessionStart", tree),
    )
    assert brief.returncode == 0, brief
    assert brief.stdout.strip(), brief
    branch = next(c for _n, _e, c in packaged_commands() if "check-branch" in c)
    checked = run_hook(
        branch,
        cwd=tree,
        path=f"{kyo_bin}:/usr/bin:/bin",
        home=env_home,
        stdin=payload_for("SubagentStop", tree),
    )
    assert "check-branch" in checked.stdout + checked.stderr, checked


# --------------------------------------------------------- c3: kyo missing from PATH


def _guard_only(tmp_path: Path, cwd: Path, *, project_dir=None) -> subprocess.CompletedProcess:
    """The guard by itself, followed by a marker that would print if it fell through."""
    return run_hook(
        PATH_GUARD_PREFIX + "echo FELL-THROUGH",
        cwd=cwd,
        path="/usr/bin:/bin",
        home=tmp_path,
        project_dir=project_dir,
    )


def _spoke(result: subprocess.CompletedProcess) -> bool:
    return (
        result.returncode == 127
        and result.stdout == ""
        and result.stderr.strip().splitlines() == [PATH_GUARD_MESSAGE]
    )


def test_the_guard_is_silent_without_a_marker_and_speaks_with_one(tmp_path: Path) -> None:
    """KYO-591.c3, both cases, for every packaged command: no marker -> exit 0, no
    output; `kyosei/config.yml` above the hook's directory -> the one line, exit 127."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _not_a_repository(empty)
    marked = tmp_path / "marked"
    (marked / "kyosei").mkdir(parents=True)
    (marked / "kyosei" / "config.yml").write_text("prefix: TST\n", encoding="utf-8")
    nested = marked / "a" / "b"
    nested.mkdir(parents=True)
    for name, event, command in packaged_commands():
        quiet = run_hook(command, cwd=empty, path="/usr/bin:/bin", home=tmp_path)
        assert (quiet.returncode, quiet.stdout, quiet.stderr) == (0, "", ""), (name, quiet)
        loud = run_hook(command, cwd=nested, path="/usr/bin:/bin", home=tmp_path)
        assert _spoke(loud), (name, event, loud)


def test_the_guard_reads_claude_project_dir_before_its_own_cwd(tmp_path: Path) -> None:
    """The walk starts at ``$CLAUDE_PROJECT_DIR`` when the harness sets it."""
    empty = tmp_path / "empty"
    empty.mkdir()
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / STORE_LINK_NAME).write_text("store: /nowhere\n", encoding="utf-8")
    assert _spoke(_guard_only(tmp_path, empty, project_dir=linked))
    quiet = _guard_only(tmp_path, linked, project_dir=empty)
    assert (quiet.returncode, quiet.stdout, quiet.stderr) == (0, "", "")


def test_the_guard_finds_the_link_beside_the_main_checkout(tmp_path: Path) -> None:
    """KYO-591.c3's second place: a linked worktree with no marker at or above it, whose
    main checkout (named by `git rev-parse --git-common-dir`) carries `.kyosei`. Removing
    the main checkout's link silences it again, so it is that link that was found."""
    code, _store, tree = _split_project_with_outside_worktree(tmp_path)
    assert _spoke(_guard_only(tmp_path, tree))
    (code / STORE_LINK_NAME).unlink()
    quiet = _guard_only(tmp_path, tree)
    assert (quiet.returncode, quiet.stdout, quiet.stderr) == (0, "", "")


def test_with_kyo_present_the_guard_falls_through_untouched(tmp_path: Path) -> None:
    """The walk runs only when `kyo` is missing: with one on PATH the guarded command
    runs, in a directory with no marker at all."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kyo").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "kyo").chmod(0o755)
    result = run_hook(
        PATH_GUARD_PREFIX + "echo FELL-THROUGH",
        cwd=tmp_path,
        path=f"{bin_dir}:/usr/bin:/bin",
        home=tmp_path,
    )
    assert (result.returncode, result.stdout.strip()) == (0, "FELL-THROUGH"), result
