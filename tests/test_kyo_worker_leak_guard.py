"""KYO-535: a dispatched worker's writes OUTSIDE its worktree -- the main checkout,
``$KYOSEI_HOME`` and its ``projects.json`` -- are named at its stop (c1), the commands
that register a project refuse the real home from inside a running dispatch's worktree
(c2), and the orchestrator's own commands in the main checkout are untouched (c3).

In one session a worker left eight scratch files in the main checkout's root, another's
`kyo init` created ``~/kyosei/p1`` and registered it in the real ``projects.json``, and a
third's scratch copy was registered the same way. `kyo check-clean` and `kyo
check-branch` read only the worker's own worktree, so every one of those stops passed.

Every fixture here runs under conftest's temporary ``$KYOSEI_HOME``; "the real home" in
these tests is that temporary one, made real by the same facts that make ``~/kyosei``
real for this repository's own workers (a dispatch worktree inside it, the project
registered in it).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from kyosei import config as config_module
from kyosei.cmd_check_clean import BASELINE_DIRNAME, baseline_path

from .conftest import invoke, new_record
from .test_subagent_stop_worktree import _payload, _running_dispatch, _stop, _transcript

# --------------------------------------------------------------------------- helpers


def _remedies(text: str) -> list[str]:
    """Every command the stop printed after ``remove it:``, as printed."""
    return [
        line.split("remove it: ", 1)[1].strip()
        for line in text.splitlines()
        if "remove it: " in line
    ]


def _run_remedy(command: str) -> None:
    """Run one printed remedy exactly as printed: `rm`/`rm -r` through a real process,
    and `KYOSEI_HOME=... kyo projects remove ...` through the CLI with that variable set."""
    words = shlex.split(command)
    if words[0] == "rm":
        subprocess.run(words, check=True, capture_output=True)
        return
    assert words[0].startswith("KYOSEI_HOME=") and words[1] == "kyo", command
    home = words[0].split("=", 1)[1]
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KYOSEI_HOME", home)
        result = invoke(words[2:])
    assert result.code == 0, result


def _repeat_stop(kyo, tmp_path: Path, cwd: Path, dispatch_id: str, *args: str):
    """A stop that follows a blocked one: Claude Code sets ``stop_hook_active``."""
    agent = "a1b2c3d4e5f6a7b8c"
    transcript = _transcript(
        tmp_path, agent, f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    )
    payload = json.loads(_payload(transcript, cwd, agent))
    payload["stop_hook_active"] = True
    return kyo("check-clean", "--subagent-stop", *args, stdin=json.dumps(payload))


# ---------------------------------------------------------------- c1: at the stop


def test_dispatch_takes_a_baseline_in_the_git_common_dir(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The comparison's other half. Remove `take_baseline` from `kyo dispatch` and every
    blocking test below passes clean instead -- and this one fails first."""
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-baseline")
    path = baseline_path(tree, dispatch_id)
    assert path is not None and path.is_file()
    assert path.parent.name == BASELINE_DIRNAME
    assert path == baseline_path(git_project, dispatch_id), "one file, found from both sides"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["main_checkout"] == str(git_project.resolve())
    assert document["registry_roots"] == [str(git_project.resolve())]
    assert "src/wt-baseline.py" in document["untracked"]


def test_a_new_untracked_file_in_the_main_checkout_blocks_and_its_remedy_clears_it(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-519's eight scratch files: the stop names the file and the `rm` that removes
    it, and running that `rm` is what makes the next stop pass."""
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-scratch")
    (git_project / "run.txt").write_text("scratch\n", encoding="utf-8")

    blocked = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id)
    assert blocked.code == 2, blocked
    assert "worker-wrote-outside-its-worktree" in blocked.err
    assert str(git_project / "run.txt") in blocked.err
    remedies = _remedies(blocked.err)
    assert remedies == [f"rm {shlex.quote(str(git_project / 'run.txt'))}"]

    _run_remedy(remedies[0])
    cleared = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id, "--json")
    assert cleared.code == 0, cleared
    assert cleared.json["outside_the_worktree"]["untracked_in_main_checkout"] == []


def test_a_new_directory_under_the_home_blocks_and_its_remedy_clears_it(
    kyo, git_project: Path, tmp_path: Path, store_home: Path
) -> None:
    """KYO-521's ``~/kyosei/p1``, as a directory."""
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-homedir")
    (store_home / "p1" / "kyosei").mkdir(parents=True)

    blocked = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id, "--json")
    assert blocked.code == 2, blocked
    assert blocked.json["outside_the_worktree"]["new_home_dirs"] == [str(store_home / "p1")]
    text = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id).err
    (remedy,) = _remedies(text)
    assert remedy == f"rm -r {shlex.quote(str(store_home / 'p1'))}"

    _run_remedy(remedy)
    assert _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id).code == 0


def test_a_new_registry_entry_blocks_and_its_remedy_clears_it(
    kyo, git_project: Path, tmp_path: Path, store_home: Path
) -> None:
    """KYO-521's and KYO-505's registrations. The scratch project lives OUTSIDE the
    home (`--in-repo`), so the registry entry is the only trace, and still named."""
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-registry")
    scratch = tmp_path / "scratch-copy"
    assert invoke(["init", str(scratch), "--in-repo", "--prefix", "SCR"]).code == 0

    blocked = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id)
    assert blocked.code == 2, blocked
    assert str(scratch.resolve()) in blocked.err
    (remedy,) = _remedies(blocked.err)
    assert remedy.startswith(f"KYOSEI_HOME={shlex.quote(str(store_home))} kyo projects remove")

    _run_remedy(remedy)
    assert _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id).code == 0


def test_what_was_there_at_dispatch_and_the_store_dirs_never_count(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The baseline, not a timestamp: the controller's file from before the dispatch is
    not a finding, and neither is anything under `kyosei/` or `ideas/`, which the
    controller writes all session (KYO-17)."""
    (git_project / "the-controllers-file.txt").write_text("mine\n", encoding="utf-8")
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-quiet")
    (git_project / "ideas" / "a-new-thought.md").write_text("# Later\n", encoding="utf-8")
    (git_project / "kyosei" / "export" / "TST-99.json").write_text("{}\n", encoding="utf-8")

    result = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id, "--json")
    assert result.code == 0, result
    outside = result.json["outside_the_worktree"]
    assert outside["untracked_in_main_checkout"] == []
    assert outside["problems"] == []


def test_a_repeat_stop_reports_without_blocking(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """What bounds a finding the worker cannot clear (another agent's file, a removal
    the harness refuses) to one wake: the stop that follows a block passes, and still
    names every path with its remedy."""
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-repeat")
    (git_project / "not-mine.txt").write_text("someone else's\n", encoding="utf-8")
    assert _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id).code == 2

    repeat = _repeat_stop(kyo, tmp_path, git_project, dispatch_id)
    assert repeat.code == 0, repeat
    assert str(git_project / "not-mine.txt") in repeat.out
    assert "NOT blocking" in repeat.out
    assert _remedies(repeat.out)


def test_a_dirty_worktree_refusal_names_the_outside_paths_too(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """One wake, both findings: a worker blocked for uncommitted work is shown what it
    left outside in the same refusal, since its next stop may be a repeat stop."""
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-both")
    (tree / "unsaved.py").write_text("x = 1\n", encoding="utf-8")
    (git_project / "status.txt").write_text("scratch\n", encoding="utf-8")

    blocked = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id)
    assert blocked.code == 2, blocked
    assert "worktree-has-uncommitted-work" in blocked.err
    assert "unsaved.py" in blocked.err
    assert str(git_project / "status.txt") in blocked.err


def test_a_missing_baseline_is_said_not_read_as_clean(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-nobase")
    baseline_path(tree, dispatch_id).unlink()
    (git_project / "run.txt").write_text("scratch\n", encoding="utf-8")

    result = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id)
    assert result.code == 0, result
    assert f"no baseline was taken for {dispatch_id}" in result.out


def test_a_finished_dispatchs_baseline_is_removed_by_the_next_dispatch(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """Baselines do not accumulate: each dispatch removes those of dispatches this store
    knows and that have left `running`."""
    first, tree = _running_dispatch(kyo, tmp_path, "wt-first")
    old = baseline_path(tree, first)
    assert old.is_file()
    assert kyo(
        "dispatch", "close", first, "--terminated-no-report", "--why", "test"
    ).code == 0
    second, _tree = _running_dispatch(kyo, tmp_path, "wt-second")
    assert not old.exists()
    assert baseline_path(git_project, second).is_file()


# ------------------------------------- c2/c3: registering from a dispatch's worktree


def _branch_dispatch(kyo, tmp_path: Path, branch: str) -> tuple[str, Path]:
    """A running dispatch whose worktree `kyo dispatch --branch` derived under
    ``$KYOSEI_HOME`` (KYO-70) and created (KYO-485) -- the layout every real one has."""
    record = new_record(kyo, criteria=["a"], title=f"record for {branch}")
    project: Path = kyo.project
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / f"{branch}.py").write_text("# touched\n", encoding="utf-8")
    scope = tmp_path / f"{branch}-touches.txt"
    scope.write_text(f"src/{branch}.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    result = kyo("dispatch", record, "--json", "--branch", branch)
    assert result.code == 0, result
    tree = Path(result.json["worktree_resolved"])
    assert tree.is_dir()
    return result.json["dispatch_id"], tree


def _registry(home: Path) -> list[str]:
    path = home / "projects.json"
    return [e["root"] for e in json.loads(path.read_text())["projects"]] if path.is_file() else []


def test_init_from_inside_a_dispatch_worktree_is_refused_and_creates_nothing(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    """c2, KYO-521's command: a split `kyo init` would create ``<home>/p1`` and register
    it. Refused before either, naming the worktree and both ways out."""
    dispatch_id, tree = _branch_dispatch(kyo, tmp_path, "kyo-leak")
    before = _registry(store_home)
    monkeypatch.chdir(tree)

    refused = invoke(["init", "--defaults", str(tmp_path / "p1"), "--prefix", "P1"])
    assert refused.code != 0, refused
    assert dispatch_id in refused.err and str(tree) in refused.err
    assert "KYOSEI_HOME" in refused.err and "KYOSEI_ALLOW_REAL_HOME=1" in refused.err
    assert not (store_home / "p1").exists()
    assert not (tmp_path / "p1").exists()
    assert _registry(store_home) == before


def test_the_named_remedy_a_temporary_home_clears_the_refusal(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    _dispatch_id, tree = _branch_dispatch(kyo, tmp_path, "kyo-tmphome")
    before = _registry(store_home)
    monkeypatch.chdir(tree)
    assert invoke(["init", "--defaults", str(tmp_path / "p1"), "--prefix", "P1"]).code != 0

    scratch_home = tmp_path / "scratch-home"
    monkeypatch.setenv("KYOSEI_HOME", str(scratch_home))
    result = invoke(["init", "--defaults", str(tmp_path / "p1"), "--prefix", "P1"])
    assert result.code == 0, result
    assert (scratch_home / "p1").is_dir()
    assert _registry(scratch_home) == [str((tmp_path / "p1").resolve())]
    assert _registry(store_home) == before, "the real registry was reached"


def test_the_named_override_clears_the_refusal(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    _dispatch_id, tree = _branch_dispatch(kyo, tmp_path, "kyo-override")
    monkeypatch.chdir(tree)
    assert invoke(["init", "--defaults", str(tmp_path / "p1"), "--prefix", "P1"]).code != 0

    monkeypatch.setenv(config_module.REAL_HOME_FROM_DISPATCH_ENV, "1")
    result = invoke(["init", "--defaults", str(tmp_path / "p1"), "--prefix", "P1"])
    assert result.code == 0, result
    assert str((tmp_path / "p1").resolve()) in _registry(store_home)


def test_the_worktrees_own_kyo_run_from_the_main_checkout_is_refused(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    """A worker's Bash cwd is reset to the CONTROLLER's checkout between calls, so a
    worker running its worktree's `.venv/bin/python -m kyosei` is seen by where the
    package was imported from, not by its cwd. Remove that candidate and this passes."""
    _dispatch_id, tree = _branch_dispatch(kyo, tmp_path, "kyo-pkg")
    monkeypatch.chdir(git_project)
    monkeypatch.setattr(config_module, "PACKAGE_DIR", tree / "src" / "kyosei")

    refused = invoke(["init", str(tmp_path / "p1"), "--in-repo", "--prefix", "P1"])
    assert refused.code != 0, refused
    assert "the kyosei package this command runs from" in refused.err
    assert not (tmp_path / "p1").exists()


def test_projects_add_from_inside_a_dispatch_worktree_is_refused(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    """KYO-505's case: a scratch copy registered by hand."""
    _dispatch_id, tree = _branch_dispatch(kyo, tmp_path, "kyo-add")
    scratch_home = tmp_path / "scratch-home"
    monkeypatch.setenv("KYOSEI_HOME", str(scratch_home))
    assert invoke(["init", str(tmp_path / "copy"), "--in-repo", "--prefix", "CPY"]).code == 0
    monkeypatch.setenv("KYOSEI_HOME", str(store_home))
    before = _registry(store_home)
    monkeypatch.chdir(tree)

    refused = invoke(["projects", "add", str(tmp_path / "copy")])
    assert refused.code != 0, refused
    assert "kyo projects add would register" in refused.err
    assert _registry(store_home) == before


def test_a_finished_dispatchs_worktree_is_not_guarded(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    """Precision: the guard is about a RUNNING dispatch, asked of the store."""
    dispatch_id, tree = _branch_dispatch(kyo, tmp_path, "kyo-done")
    assert kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "test"
    ).code == 0
    monkeypatch.chdir(tree)
    assert invoke(["init", str(tmp_path / "p1"), "--in-repo", "--prefix", "P1"]).code == 0


def test_an_unset_home_is_the_real_one() -> None:
    """``$KYOSEI_HOME`` unset is ``~/kyosei``, the account's own: never a scratch home.
    Asked of the pure predicate, so no test ever runs a write against the real home."""
    why = config_module._why_home_is_real(
        Path("/nonexistent/home"), Path("/nonexistent/wt"), Path("/nonexistent/main"), {}
    )
    assert why is not None and "unset" in why


def test_the_orchestrators_own_commands_in_the_main_checkout_are_unaffected(
    kyo, git_project: Path, tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    """c3. With a running dispatch whose worktree sits in the real home, the controller's
    `kyo init` and `kyo projects add` from the main checkout write the real registry as
    they always did."""
    _dispatch_id, _tree = _branch_dispatch(kyo, tmp_path, "kyo-c3")
    monkeypatch.chdir(git_project)

    new_project = str(tmp_path / "new-project")
    assert invoke(["init", "--defaults", new_project, "--prefix", "NEW"]).code == 0
    assert (store_home / "new-project").is_dir()
    assert str((tmp_path / "new-project").resolve()) in _registry(store_home)

    assert invoke(["init", str(tmp_path / "other"), "--in-repo", "--prefix", "OTH"]).code == 0
    assert invoke(["projects", "remove", str(tmp_path / "other")]).code == 0
    assert invoke(["projects", "add", str(tmp_path / "other")]).code == 0
    assert str((tmp_path / "other").resolve()) in _registry(store_home)
