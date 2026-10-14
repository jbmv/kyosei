"""KYO-410: the stop guards read what they claim to check, whatever the git config says.

Two findings, one file, because both are the same
defect: a guard that answered "clean" by reading something other than its subject.

* ``kyo check-clean`` inherited ``status.showUntrackedFiles=no`` from the user's git
  config and listed no untracked file at all, so a worker's uncommitted new module
  stopped "clean". The status call now pins its listing.
* ``kyo check-branch`` spelled the trunk ``main``. In a ``master`` repository an
  unmatched stop exited 1 with "'main' is not a known git ref" -- a NON-blocking
  SubagentStop error, so the store guard silently did not run -- and a matched stop
  lost its KYO-294 cut point. The trunk is now resolved once, by
  :func:`kyosei.cmd_check_branch.resolve_trunk`, in a documented order.

Every test here runs git with the host's global and system config switched off, so
the answer is this fixture's and never the machine's (KYO-366's reasoning).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from kyosei.config import load_config
from kyosei.errors import KyoseiError

from .test_kyo294_inherited_store_diff import (
    _dispatch_then_commit_then_worktree,
    _matched_stop,
)

REPO = Path(__file__).resolve().parents[1]
PACKAGED_FRAGMENT = REPO / "src" / "kyosei" / "defaults" / "hooks" / "subagent-stop.json"
INSTALLED_SETTINGS = REPO / ".claude" / "settings.json"


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No global or system git config: ``init.defaultBranch`` and
    ``status.showUntrackedFiles`` are whatever each test sets in the repo, and
    nothing the host happens to carry."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _store_commit_on(repo: Path, branch: str) -> None:
    """A branch whose own commit writes under ``kyosei/`` -- the defect's repro."""
    _git(repo, "checkout", "-q", "-b", branch)
    (repo / "kyosei" / "export" / "KYO-9.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a worker writing the store")


def _unmatched_stop(kyo, cwd: Path, *args: str):
    """A SubagentStop this guard cannot place on a running dispatch: no dispatch
    base, so the comparison stands on the trunk alone."""
    payload = json.dumps(
        {
            "session_id": "session",
            "cwd": str(cwd),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
        }
    )
    return kyo(*args, stdin=payload)


def _hook_argv(command: str) -> list[str]:
    """The ``kyo ...`` argv a hook command line runs, past its KYO-316 PATH guard."""
    tail = command.rsplit("; ", 1)[-1]
    argv = shlex.split(tail)
    assert argv[0] == "kyo", command
    return argv[1:]


def _check_branch_commands() -> list[tuple[str, str]]:
    """Every check-branch line this criterion names: the one shipped in
    ``defaults/hooks`` and the one this repository has INSTALLED (install-hooks is
    not re-run by this record, so the installed line must work as it stands)."""
    found: list[tuple[str, str]] = []
    sources = [("packaged", PACKAGED_FRAGMENT)]
    if INSTALLED_SETTINGS.is_file():
        sources.append(("installed", INSTALLED_SETTINGS))
    for label, path in sources:
        text = json.loads(path.read_text(encoding="utf-8"))
        # The fragment is the bare event mapping; settings.json nests it under "hooks".
        events = text.get("hooks", text)
        for groups in events.get("SubagentStop", []):
            for hook in groups.get("hooks", []):
                command = hook.get("command", "")
                if "kyo check-branch" in command:
                    found.append((label, command))
    assert {label for label, _ in found} >= {"packaged"}, found
    return found


def _set_trunk(project: Path, value: str | None) -> None:
    """Write (or remove) the ``trunk:`` line in the fixture's config.yml by hand --
    the remedy the refusal names is a hand edit."""
    path = project / "kyosei" / "config.yml"
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("trunk:")
    ]
    if value is not None:
        lines.append(f"trunk: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------- KYO-410.c1


def test_check_clean_sees_an_untracked_file_under_show_untracked_files_no(
    kyo, git_project: Path
) -> None:
    """The defect's repro, as a test: with the listing suppressed by config the
    guard used to print "nothing uncommitted" and exit 0. It must name the file."""
    _git(git_project, "config", "status.showUntrackedFiles", "no")
    (git_project / "src").mkdir(exist_ok=True)
    (git_project / "src" / "newmod.py").write_text("x = 1\n", encoding="utf-8")
    # The premise, checked rather than assumed: git itself now lists nothing.
    assert _git(git_project, "status", "--porcelain").stdout.strip() == ""

    result = kyo("check-clean", "--json")
    assert result.code == 2, result
    assert any("src/newmod.py" in line for line in result.json["dirty_entries"]), result.json
    assert "src/newmod.py" in result.err


def test_check_clean_names_each_untracked_file_inside_a_new_directory(
    kyo, git_project: Path
) -> None:
    """``--untracked-files=all``, not ``normal``: a new package is listed file by
    file, so the refusal names the work rather than a directory."""
    _git(git_project, "config", "status.showUntrackedFiles", "no")
    package = git_project / "newpkg"
    package.mkdir()
    (package / "a.py").write_text("a = 1\n", encoding="utf-8")
    (package / "b.py").write_text("b = 1\n", encoding="utf-8")

    result = kyo("check-clean", "--json")
    assert result.code == 2, result
    dirty = " ".join(result.json["dirty_entries"])
    assert "newpkg/a.py" in dirty and "newpkg/b.py" in dirty, result.json


def test_check_clean_still_passes_a_clean_tree_under_the_pinned_listing(
    kyo, git_project: Path
) -> None:
    """The pin widens what is SEEN, not what counts: a committed tree is clean."""
    _git(git_project, "config", "status.showUntrackedFiles", "no")
    result = kyo("check-clean", "--json")
    assert result.code == 0, result
    assert result.json["dirty_entries"] == []


# ------------------------------------------------------------------- KYO-410.c2


def test_packaged_and_installed_hook_lines_refuse_a_store_write_in_a_master_repo(
    kyo, git_project: Path
) -> None:
    """The record's repro, run through the literal command lines: a ``master``
    repository with no ``main`` ref, a branch writing ``kyosei/export/KYO-9.json``,
    and an unmatched stop. Exit 2 naming the file, never exit 1 naming ``main``."""
    _git(git_project, "branch", "-m", "main", "master")
    _store_commit_on(git_project, "w")

    commands = _check_branch_commands()
    for label, command in commands:
        argv = _hook_argv(command)
        assert "--against" in argv and argv[argv.index("--against") + 1] == "main", (
            f"the {label} line no longer spells --against main; this test's premise "
            "(the literal is resolved at runtime) needs revisiting"
        )
        result = _unmatched_stop(kyo, git_project, *argv, "--json")
        assert result.code == 2, (label, result)
        assert "is not a known git ref" not in result.err, (label, result)
        assert result.json["against"] == "master", (label, result.json)
        assert result.json["trunk"] == {"ref": "master", "source": "git-builtin-default"}
        assert "kyosei/export/KYO-9.json" in result.json["guarded_files"], result.json


def test_the_hand_run_default_resolves_the_trunk_too(kyo, git_project: Path) -> None:
    """``kyo check-branch w`` with no ``--against`` is the same case: the implicit
    default was the same literal."""
    _git(git_project, "branch", "-m", "main", "master")
    _store_commit_on(git_project, "w")
    result = kyo("check-branch", "w", "--json")
    assert result.code == 2, result
    assert result.json["against"] == "master", result.json


# ------------------------------------------------------------------- KYO-410.c3


def test_a_matched_stop_in_a_master_repo_measures_the_cut_point_from_master(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-294 in a ``master`` repository. The controller commits the store on
    ``master`` between stamping the dispatch's sha and cutting the worktree; the
    worker commits nothing guarded. Measured from a literal ``main`` there was no
    cut point, the base fell back to the recorded sha, and the controller's commit
    blocked the worker."""
    _git(git_project, "branch", "-m", "main", "master")
    dispatch_id, tree, base_sha, inherited_sha, _brief = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "mastercut"
    )
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit, touching nothing guarded")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert result.code == 0, result
    data = result.json
    assert data["against_source"] == "branch-cut-point", data
    assert data["against"] == inherited_sha, data
    assert data["dispatch_base_sha"] == base_sha, data
    assert data["trunk"]["ref"] == "master", data
    assert "kyosei/mastercut-dispatch-record.md" not in data["changed_files"], data


def test_a_matched_stop_in_a_master_repo_still_refuses_the_worker_s_own_store_write(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    _git(git_project, "branch", "-m", "main", "master")
    dispatch_id, tree, _base, _inherited, _brief = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "mastersneaky"
    )
    (tree / "kyosei" / "kinds.yml").write_text("kinds: {sneaky: true}\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's OWN store edit")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert result.code == 2, result
    assert any("kyosei/kinds.yml" in p for p in result.json["guarded_files"]), result.json


# ------------------------------------------------------------------- KYO-410.c4
#
# One test per rung of the order, each arranged so that the rung BELOW it would give
# a different answer -- otherwise a test could pass with its rung deleted.


def test_rung_1_the_config_key_wins_over_everything_git_says(
    kyo, git_project: Path
) -> None:
    head = _git(git_project, "rev-parse", "HEAD").stdout.strip()
    _git(git_project, "update-ref", "refs/remotes/origin/main", head)
    _git(git_project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(git_project, "branch", "develop")
    _set_trunk(git_project, "develop")
    _store_commit_on(git_project, "w")

    result = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert result.code == 2, result
    assert result.json["trunk"] == {"ref": "develop", "source": "config"}, result.json
    assert load_config(git_project).trunk == "develop"


def test_rung_2_origin_head_names_the_local_branch_it_advertises(
    kyo, git_project: Path
) -> None:
    _git(git_project, "branch", "-m", "main", "trunk")
    _git(git_project, "branch", "main")  # rung 4 would answer `main`
    head = _git(git_project, "rev-parse", "HEAD").stdout.strip()
    _git(git_project, "update-ref", "refs/remotes/origin/trunk", head)
    _git(git_project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    _git(git_project, "config", "init.defaultBranch", "main")  # rung 3 would too
    _store_commit_on(git_project, "w")

    result = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert result.code == 2, result
    assert result.json["trunk"] == {"ref": "trunk", "source": "origin-head"}, result.json


def test_rung_2_origin_head_without_a_local_branch_uses_the_remote_ref(
    kyo, git_project: Path
) -> None:
    head = _git(git_project, "rev-parse", "HEAD").stdout.strip()
    _git(git_project, "update-ref", "refs/remotes/origin/trunk", head)
    _git(git_project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    _store_commit_on(git_project, "w")

    result = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert result.code == 2, result
    assert result.json["trunk"] == {"ref": "origin/trunk", "source": "origin-head"}


def test_rung_3_init_default_branch_when_that_branch_exists(
    kyo, git_project: Path
) -> None:
    _git(git_project, "branch", "-m", "main", "stable")
    _git(git_project, "branch", "main")  # rung 4 would answer `main`
    _git(git_project, "config", "init.defaultBranch", "stable")
    _store_commit_on(git_project, "w")

    result = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert result.code == 2, result
    assert result.json["trunk"] == {"ref": "stable", "source": "init-default-branch"}


def test_rung_3_is_skipped_when_init_default_branch_names_no_branch(
    kyo, git_project: Path
) -> None:
    _git(git_project, "config", "init.defaultBranch", "nonesuch")
    _store_commit_on(git_project, "w")
    result = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert result.code == 2, result
    assert result.json["trunk"] == {"ref": "main", "source": "main"}


def test_rung_4_main_before_git_s_builtin_master(kyo, git_project: Path) -> None:
    _git(git_project, "branch", "master")  # rung 5 would answer `master`
    _store_commit_on(git_project, "w")
    result = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert result.code == 2, result
    assert result.json["trunk"] == {"ref": "main", "source": "main"}


def test_a_configured_trunk_naming_no_ref_is_refused_and_correcting_it_clears(
    kyo, git_project: Path
) -> None:
    """A declaration is not skipped to a rung the project did not choose: it is
    refused, naming the key. The remedy the message names -- set the key to the
    real branch -- is then applied and the refusal is gone."""
    _git(git_project, "branch", "-m", "main", "master")
    _store_commit_on(git_project, "w")
    _set_trunk(git_project, "mian")

    refused = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop")
    assert refused.code == 1, refused
    assert "key 'trunk'" in refused.err and "'mian'" in refused.err, refused
    assert "config.yml" in refused.err

    _set_trunk(git_project, "master")
    cleared = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert cleared.code == 2, cleared  # the real verdict: the branch wrote the store
    assert "key 'trunk'" not in cleared.err
    assert cleared.json["trunk"] == {"ref": "master", "source": "config"}


def test_a_configured_trunk_naming_no_ref_is_refused_on_a_matched_stop_too(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """A matched stop has a recorded base to stand on, but a wrong declaration is
    still refused there rather than quietly bypassed."""
    dispatch_id, tree, _base, _inherited, _brief = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "badtrunk"
    )
    _set_trunk(git_project, "mian")
    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert result.code == 1, result
    assert "key 'trunk'" in result.err


def test_no_rung_answering_is_refused_naming_the_key_and_the_key_clears_it(
    kyo, git_project: Path
) -> None:
    _git(git_project, "branch", "-m", "main", "develop")
    _store_commit_on(git_project, "w")

    refused = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop")
    assert refused.code == 1, refused
    assert "cannot tell which branch is the trunk" in refused.err, refused
    assert "trunk: <branch>" in refused.err

    _set_trunk(git_project, "develop")
    cleared = _unmatched_stop(kyo, git_project, "check-branch", "--subagent-stop", "--json")
    assert cleared.code == 2, cleared
    assert cleared.json["trunk"] == {"ref": "develop", "source": "config"}


def test_an_explicit_non_main_against_needs_no_trunk_at_all(kyo, git_project: Path) -> None:
    """``--against <ref>`` naming anything but the spelling ``main`` wins outright
    (KYO-285) -- so it works even where no trunk can be resolved."""
    _git(git_project, "branch", "-m", "main", "develop")
    _store_commit_on(git_project, "w")
    result = kyo("check-branch", "w", "--against", "develop", "--json")
    assert result.code == 2, result
    assert result.json["against_source"] == "explicit"


@pytest.mark.parametrize("value", ["''", "42", "[main]"])
def test_a_blank_or_non_string_trunk_key_is_refused_at_load(
    git_project: Path, value: str
) -> None:
    _set_trunk(git_project, value)
    with pytest.raises(KyoseiError, match="key 'trunk' must be a branch name"):
        load_config(git_project)
    _set_trunk(git_project, None)
    assert load_config(git_project).trunk is None
