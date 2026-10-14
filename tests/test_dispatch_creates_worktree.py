"""KYO-485 (proposal KYO-484): `kyo dispatch --branch` creates the dispatch's worktree.

Before this the command derived the path and printed a `git worktree add` line for the
controller to paste — seven times in one day in this repo — on the strength of one
comment (KYO-70) that a store command must not perform a code-repository act. Now it
runs `git worktree add` itself, in the code repository, after every refusal and
immediately before the dispatch row is written. `--no-create-worktree` keeps the old
printed line; `--base <ref>` records a deliberate starting point as the dispatch's base
sha and cuts the new branch there.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from kyosei import commands
from kyosei import config as config_module
from kyosei.errors import EXIT_CONTRACT

from .conftest import allow_direct_filing, invoke, new_record, valid_report
from .reference_pages import OPERATING

SKILL = (
    Path(__file__).resolve().parents[1]
    / "src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md"
)
#: The Quick start cheatsheet that names the dispatch flags, a README section until
#: KYO-516 moved it to its reference page.
README = OPERATING


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _registered(repo: Path) -> set[Path]:
    listing = _git(repo, "worktree", "list", "--porcelain")
    return {
        Path(line[len("worktree ") :]).resolve()
        for line in listing.splitlines()
        if line.startswith("worktree ")
    }


def _derived(project: Path, branch: str) -> Path:
    return config_module.default_worktree_dir(project.name, branch)


def _dispatches(kyo, record: str) -> list:
    return kyo("show", record, "--json").json["dispatches"]


# ------------------------------------------------------------------------------ c1


def test_a_new_branch_gets_a_created_worktree_cut_at_head(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    head = _git(git_project, "rev-parse", "HEAD")
    result = kyo("dispatch", record, "--branch", "kyo-new")
    assert result.code == 0, result

    tree = _derived(git_project, "kyo-new")
    assert tree.resolve() in _registered(git_project)
    assert _git(tree, "symbolic-ref", "--short", "HEAD") == "kyo-new"
    assert _git(tree, "rev-parse", "HEAD") == head
    assert "created it: git worktree add" in result.out
    assert str(tree.resolve()) in result.out or str(tree) in result.out
    assert "create it with:" not in result.out


def test_an_existing_branch_is_checked_out_not_recreated(kyo, git_project: Path) -> None:
    _git(git_project, "commit", "-q", "--allow-empty", "-m", "on main")
    _git(git_project, "branch", "existing", "HEAD~1")
    tip = _git(git_project, "rev-parse", "existing")
    record = new_record(kyo, criteria=["a"])

    result = kyo("dispatch", record, "--branch", "existing", "--json")
    assert result.code == 0, result
    assert result.json["worktree_created"] is True
    tree = _derived(git_project, "existing")
    assert _git(tree, "symbolic-ref", "--short", "HEAD") == "existing"
    assert _git(tree, "rev-parse", "HEAD") == tip


# ------------------------------------------------------------------------------ c2


def test_a_failed_git_worktree_add_leaves_no_dispatch_row(kyo, git_project: Path) -> None:
    """`main` is checked out in the project itself, so git refuses a second worktree."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "main")
    assert result.code != 0, result
    assert result.err.startswith("kyo: "), result.err
    assert "Traceback" not in result.err
    assert "git worktree add" in result.err
    assert "main" in result.err and "already" in result.err, result.err  # git's own words
    assert _dispatches(kyo, record) == []
    assert not _derived(git_project, "main").exists()


def test_the_failure_remedy_clears_it(kyo, git_project: Path) -> None:
    """The remedy the message names: fix what git names (free the branch)."""
    _git(git_project, "branch", "busy")
    elsewhere = git_project.parent / "holder"
    _git(git_project, "worktree", "add", "-q", str(elsewhere), "busy")
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "busy").code != 0
    _git(git_project, "worktree", "remove", str(elsewhere))
    assert kyo("dispatch", record, "--branch", "busy").code == 0


def test_a_failure_after_the_worktree_exists_removes_it(
    kyo, git_project: Path, monkeypatch
) -> None:
    """The row write failing after `git worktree add`: the worktree and the branch this
    call cut are removed, and the transaction leaves no row."""
    record = new_record(kyo, criteria=["a"])

    def boom(*args, **kwargs):
        raise RuntimeError("injected after the worktree was created")

    monkeypatch.setattr(commands, "_commit_and_export", boom)
    failed = kyo("dispatch", record, "--branch", "kyo-undo")
    monkeypatch.undo()
    assert failed.code != 0, failed
    assert "injected after the worktree was created" in failed.err, failed

    tree = _derived(git_project, "kyo-undo")
    assert not tree.exists()
    assert tree.resolve() not in _registered(git_project)
    assert _git(git_project, "branch", "--list", "kyo-undo") == ""
    assert _dispatches(kyo, record) == []


# ------------------------------------------------------------------------------ c3


def test_an_existing_worktree_on_the_branch_is_reused_without_git(
    kyo, git_project: Path
) -> None:
    tree = _derived(git_project, "kyo-r")
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(git_project, "worktree", "add", "-q", "-b", "kyo-r", str(tree))
    record = new_record(kyo, criteria=["a"])

    result = kyo("dispatch", record, "--branch", "kyo-r", "--json")
    assert result.code == 0, result
    assert result.json["worktree_reused"] is True
    assert result.json["worktree_created"] is False
    text = kyo("dispatch", new_record(kyo, criteria=["b"]), "--branch", "kyo-r", "--force")
    assert "reused it" in text.out, text


def test_any_other_existing_directory_is_refused_and_the_remedy_clears_it(
    kyo, git_project: Path
) -> None:
    squatter = _derived(git_project, "kyo-s")
    squatter.mkdir(parents=True)
    (squatter / "precious.txt").write_text("mine\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"])

    refused = kyo("dispatch", record, "--branch", "kyo-s")
    assert refused.code != 0, refused
    assert str(squatter) in refused.err
    assert "--worktree" in refused.err and "--branch" in refused.err
    assert _dispatches(kyo, record) == []
    assert (squatter / "precious.txt").read_text(encoding="utf-8") == "mine\n"

    # The remedy, as named: a different --branch, so the derived path is fresh.
    assert kyo("dispatch", record, "--branch", "kyo-s2").code == 0


def test_a_worktree_on_another_branch_is_refused(kyo, git_project: Path) -> None:
    tree = _derived(git_project, "kyo-t")
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(git_project, "worktree", "add", "-q", "-b", "other", str(tree))
    record = new_record(kyo, criteria=["a"])
    refused = kyo("dispatch", record, "--branch", "kyo-t")
    assert refused.code != 0
    assert "other checked out, not kyo-t" in refused.err
    assert _dispatches(kyo, record) == []


def test_a_second_running_dispatch_into_the_created_worktree_is_still_refused(
    kyo, git_project: Path
) -> None:
    """KYO-411: the second dispatch finds the first's worktree, reuses it, and the
    collision check refuses it."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-x").code == 0
    second = kyo("dispatch", record, "--branch", "kyo-x")
    assert second.code == EXIT_CONTRACT, second
    assert "dispatch-worktree-collision" in second.err
    assert len(_dispatches(kyo, record)) == 1


# ------------------------------------------------------------------------------ c4


def test_no_create_worktree_restores_the_printed_command(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-p", "--json")
    assert result.code == 0, result
    assert result.json["worktree_created"] is False
    assert not _derived(git_project, "kyo-p").exists()
    assert _git(git_project, "branch", "--list", "kyo-p") == ""
    text = kyo(
        "dispatch", new_record(kyo, criteria=["b"]), "--no-create-worktree", "--branch",
        "kyo-q", "--force",
    )
    assert "create it with: git worktree add" in text.out, text


def test_a_dispatch_without_branch_or_worktree_creates_nothing(
    kyo, git_project: Path
) -> None:
    before = _registered(git_project)
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    assert result.json["worktree_created"] is False
    assert _registered(git_project) == before


# ------------------------------------------------------------------------------ c5


def _diverged_predecessor(repo: Path) -> str:
    """A branch `pred` one commit past the initial one, and main advanced past the
    initial one separately -- so `pred` is NOT a descendant of main's HEAD."""
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    sha = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "predecessor's work")
    _git(repo, "branch", "pred", sha)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main moves on")
    return sha


def _report(kyo, tmp_path: Path, dispatch: dict, record: str, sha: str):
    path = tmp_path / f"{dispatch['dispatch_id']}.json"
    path.write_text(
        json.dumps(
            valid_report(dispatch_id=dispatch["dispatch_id"], record_id=record,
                         worked_from_sha=sha)
        ),
        encoding="utf-8",
    )
    return kyo("report", dispatch["dispatch_id"], "--file", str(path))


def test_base_cuts_the_worktree_there_and_records_it(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    pred = _diverged_predecessor(git_project)
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-r2", "--base", "pred", "--json")
    assert result.code == 0, result
    assert result.json["worked_from_sha"] == pred

    tree = _derived(git_project, "kyo-r2")
    assert _git(tree, "rev-parse", "HEAD") == pred
    assert _git(tree, "symbolic-ref", "--short", "HEAD") == "kyo-r2"

    dispatch = _dispatches(kyo, record)[0]
    assert dispatch["worked_from_sha"] == pred
    assert f"`{pred}`" in dispatch["brief"]
    assert "--base pred" in dispatch["brief"]

    reported = _report(kyo, tmp_path, result.json, record, pred)
    assert reported.code == 0, reported
    assert "not a known descendant" not in reported.out + reported.err


def test_without_base_the_same_report_draws_the_warning(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The contrast that makes the test above mean something."""
    pred = _diverged_predecessor(git_project)
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--no-create-worktree", "--branch", "kyo-r2", "--json")
    assert result.code == 0, result
    reported = _report(kyo, tmp_path, result.json, record, pred)
    assert reported.code == 0, reported
    assert "not a known" in reported.out + reported.err


def test_an_unresolvable_base_is_refused_before_any_row(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    refused = kyo("dispatch", record, "--branch", "kyo-b", "--base", "no-such-ref")
    assert refused.code != 0, refused
    assert "no-such-ref" in refused.err and "Traceback" not in refused.err
    assert _dispatches(kyo, record) == []
    assert not _derived(git_project, "kyo-b").exists()
    # The remedy: a ref that resolves.
    assert kyo("dispatch", record, "--branch", "kyo-b", "--base", "main").code == 0


def test_base_needs_a_branch_or_worktree(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    refused = kyo("dispatch", record, "--base", "main")
    assert refused.code != 0
    assert "--branch" in refused.err
    assert kyo("dispatch", record, "--base", "", "--branch", "x").code != 0
    assert kyo("dispatch", record, "--base", "main", "--branch", "kyo-ok").code == 0


def test_base_disagreeing_with_an_existing_branch_is_refused(kyo, git_project: Path) -> None:
    pred = _diverged_predecessor(git_project)
    assert pred
    record = new_record(kyo, criteria=["a"])
    refused = kyo("dispatch", record, "--branch", "pred", "--base", "main")
    assert refused.code != 0
    assert "already exists" in refused.err
    assert _dispatches(kyo, record) == []
    # Both remedies it names clear it.
    assert kyo("dispatch", record, "--branch", "pred-r2", "--base", "main").code == 0
    other = new_record(kyo, criteria=["b"])
    assert kyo("dispatch", other, "--branch", "pred", "--force").code == 0


# ------------------------------------------------------------------- KYO-487


def test_an_existing_branch_records_its_tip_as_the_base(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-487.c1: `--branch B` onto an existing, diverged B with no `--base` records
    B's tip (where the worktree starts), says so in the brief, and a report from that
    tip draws no not-a-known-descendant warning. Fails if the default reverts to HEAD."""
    pred = _diverged_predecessor(git_project)
    head = _git(git_project, "rev-parse", "HEAD")
    assert pred != head
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "pred", "--json")
    assert result.code == 0, result
    assert result.json["worked_from_sha"] == pred
    assert _git(_derived(git_project, "pred"), "rev-parse", "HEAD") == pred

    dispatch = _dispatches(kyo, record)[0]
    assert dispatch["worked_from_sha"] == pred
    assert f"`{pred}` — the tip of the existing branch `pred`" in dispatch["brief"]
    assert "HEAD in this repo" not in dispatch["brief"]

    reported = _report(kyo, tmp_path, result.json, record, pred)
    assert reported.code == 0, reported
    assert "not a known" not in reported.out + reported.err


def test_a_reused_worktree_on_an_existing_branch_records_its_tip(
    kyo, git_project: Path
) -> None:
    """KYO-487.c1, the reuse path: a worktree already on the branch starts at its tip."""
    pred = _diverged_predecessor(git_project)
    tree = _derived(git_project, "pred")
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(git_project, "worktree", "add", str(tree), "pred")
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "pred", "--json")
    assert result.code == 0, result
    assert result.json["worked_from_sha"] == pred


def test_a_new_branch_still_records_head_as_the_base(kyo, git_project: Path) -> None:
    """KYO-487.c2: a branch that does not exist yet is cut at HEAD, recorded as HEAD."""
    _diverged_predecessor(git_project)
    head = _git(git_project, "rev-parse", "HEAD")
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-fresh", "--json")
    assert result.code == 0, result
    assert result.json["worked_from_sha"] == head
    brief = _dispatches(kyo, record)[0]["brief"]
    assert f"`{head}` — HEAD in this repo" in brief
    assert "tip of the existing branch" not in brief


# ------------------------------------------------------------------------------ c6


def test_a_split_layout_creates_the_worktree_from_the_code_repository(
    split_project: tuple[Path, Path],
) -> None:
    code, store = split_project
    allow_direct_filing(store, "bug")
    made = invoke(
        ["--root", str(code), "new", "bug", "--title", "split", "--criteria-file", "-",
         "--json"],
        stdin="a criterion\n",
    )
    assert made.code == 0, made
    record = made.json["record"]["id"]
    result = invoke(["--root", str(code), "dispatch", record, "--branch", "kyo-split", "--json"])
    assert result.code == 0, result

    tree = Path(result.json["worktree_resolved"])
    assert tree.resolve() in _registered(code)
    assert (tree / "src" / "widget.py").is_file(), "a checkout of the code, not the store"
    store_trees = _registered(store) if (store / ".git").exists() else set()
    assert tree.resolve() not in store_trees
    assert _git(code, "branch", "--list", "kyo-split") != ""


# ------------------------------------------------------------------------------ c7


def test_worktree_setup_is_printed_and_not_run(kyo, git_project: Path) -> None:
    config = git_project / "kyosei" / "config.yml"
    text = config.read_text(encoding="utf-8").split("worktree_setup:", 1)[0]
    config.write_text(
        text.rstrip("\n") + "\nworktree_setup:\n  - touch SETUP-RAN\n", encoding="utf-8"
    )
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-setup")
    assert result.code == 0, result
    assert "touch SETUP-RAN" in result.out
    assert "printed, not run" in result.out
    tree = _derived(git_project, "kyo-setup")
    assert tree.is_dir()
    assert not (tree / "SETUP-RAN").exists()
    assert not (git_project / "SETUP-RAN").exists()


# ------------------------------------------------------------------------------ c8


def _section_3(text: str) -> str:
    start = text.index("## 3. Dispatching")
    return text[start : text.index("\n## ", start + 1)]


def test_the_skill_describes_creation_and_the_opt_out() -> None:
    section = _section_3(SKILL.read_text(encoding="utf-8"))
    assert not re.search(r"prints the `git worktree add`", section), (
        "the skill still tells the controller to run the printed `git worktree add`"
    )
    assert "creates the worktree" in section
    assert "--no-create-worktree" in section
    assert "--base" in section


def test_the_readme_describes_creation_and_the_opt_out() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "printed\n                                           # with the `git worktree add`" \
        not in readme
    assert "--no-create-worktree" in readme
    assert "--base" in readme
