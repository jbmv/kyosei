"""KYO-13.c5: ``kyo check-branch`` — the guard the ledger's store-write rule
advertised made mechanical. (Historical: the pre-KYO-97 wording of that rule
credited "kyo doctor and a merge guard"; KYO-97 corrected the attribution to
this command, and KYO-103 pins the shipped quotations of it.)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .conftest import new_record


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    )


# ------------------------------------------------------- KYO-285: a matched base


def _worktree_dispatch(kyo, tmp_path: Path, name: str) -> tuple[str, Path]:
    """A running dispatch whose worktree is a REAL ``git worktree add`` off the
    project's own repository, sharing its history — unlike the independent
    ``git init`` fixture ``tests/test_subagent_stop_worktree.py`` uses for its
    cleanliness checks, which is fine for those (they only read ``git status``
    and ``git diff`` inside one directory) but useless here: KYO-285's own
    resolution has to look up the dispatch's recorded ``worked_from_sha`` and
    compute a real ``git merge-base`` against it *inside the worktree*, which an
    unrelated history could never resolve.

    Starts the new branch from the project's CURRENT ``HEAD`` — exactly what
    ``kyo dispatch`` itself stamps as ``worked_from_sha`` — so a caller that
    checks out a non-main branch first gets a dispatch genuinely cut from it.
    """
    project: Path = kyo.project
    tree = tmp_path / name
    _git(project, "worktree", "add", "-q", "-b", f"{name}-branch", str(tree))
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / f"{name}.py").write_text(
        "# worked on by this dispatch\n", encoding="utf-8"
    )
    scope = tmp_path / f"{name}-touches.txt"
    scope.write_text(f"src/{name}.py\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"], title=f"record for {name}")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    result = kyo("dispatch", record, "--json", "--worktree", str(tree))
    assert result.code == 0, result
    return result.json["dispatch_id"], tree


def _transcript(tmp_path: Path, agent_id: str, dispatch_id: str) -> Path:
    """A session transcript shaped the way Claude Code writes one — just enough
    for ``resolve_stop_target`` to read a prompt naming ``dispatch_id`` back out
    of it (mirrors ``tests/test_subagent_stop_worktree.py``'s own helper)."""
    session_dir = tmp_path / "transcripts" / "session"
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    parent = session_dir.with_suffix(".jsonl")
    parent.write_text("", encoding="utf-8")
    prompt = f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    (session_dir / "subagents" / f"agent-{agent_id}.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "agentId": agent_id,
                "cwd": "/the/controllers/cwd",
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return parent


def _matched_stop(kyo, tmp_path: Path, cwd: Path, dispatch_id: str, *args: str):
    """Run ``check-branch`` as the hook runs it, for a subagent whose prompt
    names ``dispatch_id`` — the same resolution ``--subagent-stop`` uses to pick
    which worktree to read, reused here to pick which base to diff against."""
    agent = "a1b2c3d4e5f6a7b8c"
    transcript = _transcript(tmp_path, agent, dispatch_id)
    payload = json.dumps(
        {
            "session_id": "session",
            "transcript_path": str(transcript),
            "cwd": str(cwd),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "agent_id": agent,
        }
    )
    return kyo("check-branch", "--subagent-stop", *args, stdin=payload)


def test_check_branch_passes_a_clean_branch(kyo, git_project: Path) -> None:
    _git(git_project, "checkout", "-b", "feature")
    (git_project / "src" / "kyosei").mkdir(parents=True, exist_ok=True)
    (git_project / "src" / "kyosei" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "add widget")

    result = kyo("check-branch", "feature", "--against", "main", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True
    assert result.json["guarded_files"] == []


def test_check_branch_fails_on_a_kyosei_diff(kyo, git_project: Path) -> None:
    _git(git_project, "checkout", "-b", "bad-branch")
    (git_project / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "sneaky store edit")

    result = kyo("check-branch", "bad-branch", "--against", "main", "--json")
    assert result.code == 2, result
    assert result.json["ok"] is False
    assert any("kyosei/kinds.yml" in f for f in result.json["guarded_files"])


def test_check_branch_fails_on_an_ideas_diff(kyo, git_project: Path) -> None:
    _git(git_project, "checkout", "-b", "touches-ideas")
    (git_project / "ideas" / "new-thought.md").write_text("a thought\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "add idea")

    result = kyo("check-branch", "touches-ideas", "--against", "main")
    assert result.code == 2
    assert "ideas/new-thought.md" in result.err


def test_check_branch_defaults_against_to_main(kyo, git_project: Path) -> None:
    _git(git_project, "checkout", "-b", "no-against-flag")
    (git_project / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "edit")

    result = kyo("check-branch", "no-against-flag")
    assert result.code == 2


def test_check_branch_defaults_branch_to_current(kyo, git_project: Path) -> None:
    _git(git_project, "checkout", "-b", "implicit-branch")
    (git_project / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "edit")

    result = kyo("check-branch", "--against", "main", "--json")
    assert result.code == 2
    assert result.json["branch"] == "implicit-branch"


def test_check_branch_unknown_ref_fails_loudly(kyo, git_project: Path) -> None:
    result = kyo("check-branch", "nonexistent-branch", "--against", "main")
    assert result.code == 1
    assert "nonexistent-branch" in result.err


# --------- KYO-285.c1: a dispatch cut from a non-main branch is not blamed for
# --------- that branch's own (pre-existing) commits under kyosei/ or ideas/


def test_check_branch_matched_dispatch_diffs_against_its_own_base_not_main(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The record's own repro: a branch cut from another branch that carries a
    store commit passes when the WORKER'S OWN commits touch neither kyosei/ nor
    ideas/ — main must never enter the comparison once the stop is matched.

    ``--against main`` is passed explicitly here on purpose: it is exactly what
    the packaged hook fragment itself passes
    (``kyo check-branch --subagent-stop --against main``), and the fragment's
    command line is not changing as part of this record — so the fix has to
    retarget the base even though ``main`` was named on the command line, or
    every real ``SubagentStop`` this project's own hook fires would still be
    running the pre-KYO-285 comparison."""
    _git(git_project, "checkout", "-q", "-b", "ui-branch")
    (git_project / "kyosei" / "controller-note.md").write_text(
        "a controller store commit\n", encoding="utf-8"
    )
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "controller store commit on ui-branch")

    dispatch_id, tree = _worktree_dispatch(kyo, tmp_path, "wtbaseok")
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit, touching nothing guarded")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id, "--json", "--against", "main")
    assert result.code == 0, result
    data = result.json
    assert data["against"] != "main", data
    assert data["against_source"] == "dispatch-base", data
    assert data["guarded_files"] == []
    assert "kyosei/controller-note.md" not in data["changed_files"], (
        "the controller's own commit on the branch this dispatch was cut from leaked "
        "into the diff — the base was not actually retargeted"
    )


def test_check_branch_matched_dispatch_still_fails_on_its_own_store_diff(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The other half of KYO-285.c1: retargeting the base must not become a way
    to launder a worker's OWN store edit — only commits before the dispatch's
    recorded base sha are exempt, never the worker's own."""
    _git(git_project, "checkout", "-q", "-b", "ui-branch")
    (git_project / "kyosei" / "controller-note.md").write_text(
        "a controller store commit\n", encoding="utf-8"
    )
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "controller store commit on ui-branch")

    dispatch_id, tree = _worktree_dispatch(kyo, tmp_path, "wtbasebad")
    (tree / "kyosei" / "kinds.yml").write_text(
        "kinds: {sneaky: true}\n", encoding="utf-8"
    )
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's OWN sneaky store edit")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id, "--json", "--against", "main")
    assert result.code == 2, result
    data = result.json
    assert data["against_source"] == "dispatch-base", data
    assert any("kyosei/kinds.yml" in f for f in data["guarded_files"]), data


def test_check_branch_a_non_main_explicit_against_bypasses_the_dispatch_base(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """``main`` is the fallback spelling, retargeted when a dispatch is matched
    (the two tests above); a DIFFERENT explicit ``--against <ref>`` is a real,
    named intent and must still win outright, matched dispatch or not."""
    _git(git_project, "checkout", "-q", "-b", "ui-branch")
    (git_project / "kyosei" / "controller-note.md").write_text(
        "a controller store commit\n", encoding="utf-8"
    )
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "controller store commit on ui-branch")

    dispatch_id, tree = _worktree_dispatch(kyo, tmp_path, "wtbaseexplicit")
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit")

    result = _matched_stop(
        kyo, tmp_path, git_project, dispatch_id, "--json", "--against", "ui-branch"
    )
    assert result.code == 0, result
    data = result.json
    assert data["against"] == "ui-branch", data
    assert data["against_source"] == "explicit", data


def test_check_branch_unmatched_stop_still_compares_against_main(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-285.c1's fallback: with no running dispatch to match this stop to,
    ``main`` is still the base — dropped to only when nothing narrower is known,
    never silently."""
    _git(git_project, "checkout", "-q", "-b", "solo-branch")
    (git_project / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "edit")

    payload = json.dumps(
        {
            "session_id": "session",
            "cwd": str(git_project),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
        }
    )
    result = kyo("check-branch", "--subagent-stop", "--json", stdin=payload)
    assert result.code == 2, result
    assert result.json["against"] == "main"
    assert result.json["against_source"] == "main-unmatched"
