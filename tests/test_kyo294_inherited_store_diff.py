"""KYO-294: a worktree cut after the controller committed the dispatch inherits a
store diff, and `kyo check-branch` used to block the worker's stop for it while the
worker's own brief called that state normal.

The sequence is KYO-10's and it is the ORDINARY one, not an edge case: `kyo dispatch`
stamps HEAD as the base sha and *then* the controller commits the store (the dispatch
records themselves), so `git worktree add` cuts the worker's branch one or more
store-only commits past the recorded base. KYO-285 had just made the stop check diff
against that recorded base -- correct for a dispatch cut from another branch, and
wrong here, because the controller's own `kyosei/export/` commit then lands in the
worker's three-dot diff. Every worktree of an ordinary wave is cut that way.
One worker cleared the block with `git rebase --onto` *after* a hand-back its
controller had already ingested and merged, so main kept the pre-rebase shas while
the branch carried different ones for the same tree.

Two halves, and this file exists to keep them from being changed one at a time:

* the check judges the commits the branch added past the point it was cut from the
  trunk (:func:`kyosei.cmd_check_branch._branch_cut_point`), so an inherited store diff does
  not block a stop and the worker's OWN store commit still does;
* the brief's Base SHA bullet says exactly that, in
  :data:`kyosei.brief.BASE_SHA_INHERITED_STORE_COMMITS`, which
  :func:`test_the_brief_s_base_sha_note_and_the_stop_check_agree` reads back against
  the check's behaviour on the same fixture (KYO-294.c2).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei.brief import BASE_SHA_INHERITED_STORE_COMMITS

from .conftest import git_worktree, new_record


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _controller_store_commit(project: Path, name: str) -> str:
    """One commit under ``kyosei/`` on the controller's own branch — what `kyo
    dispatch` itself produces when the controller commits the store after freezing
    the brief, and what the worker must never be blamed for."""
    (project / "kyosei" / f"{name}.md").write_text(
        f"controller store commit: {name}\n", encoding="utf-8"
    )
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", f"store: {name}")
    return _head(project)


def _dispatch_then_commit_then_worktree(
    kyo, tmp_path: Path, name: str
) -> tuple[str, Path, str, str, Path]:
    """The exact KYO-294 sequence, in the order it really happens.

    1. a record with declared touches, so disjointness is answerable;
    2. ``kyo dispatch --worktree <path>`` — stamps ``worked_from_sha`` at the
       controller's current HEAD. The path has to exist for that flag, so it is an
       empty directory here and `git worktree add` gets it in step 4;
    3. the controller commits the store — the dispatch record itself, in real life;
    4. ``git worktree add`` — which now cuts the worker's branch at the commit from
       step 3, one AHEAD of the sha step 2 recorded.

    Returns ``(dispatch_id, worktree, base_sha, inherited_sha, brief_path)``.
    """
    project: Path = kyo.project
    base_sha = _head(project)

    scope = tmp_path / f"{name}-touches.txt"
    scope.write_text(f"+src/{name}.py\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"], title=f"record for {name}")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0

    tree = tmp_path / name
    git_worktree(kyo.project, tree)
    brief_path = tmp_path / f"{name}-brief.md"
    dispatched = kyo(
        "dispatch", record, "--json", "--worktree", str(tree), "--brief-out", str(brief_path)
    )
    assert dispatched.code == 0, dispatched
    assert dispatched.json["worked_from_sha"] == base_sha, dispatched.json

    inherited_sha = _controller_store_commit(project, f"{name}-dispatch-record")
    assert inherited_sha != base_sha

    _git(project, "worktree", "remove", "--force", str(tree))
    _git(project, "worktree", "add", "-q", "-b", f"{name}-branch", str(tree))
    assert _head(tree) == inherited_sha, (
        "the fixture did not reproduce the record: the worktree must be cut at the "
        "controller's store commit, one ahead of the recorded base sha"
    )
    return dispatched.json["dispatch_id"], tree, base_sha, inherited_sha, brief_path


def _transcript(tmp_path: Path, agent_id: str, dispatch_id: str) -> Path:
    """A session transcript shaped the way Claude Code writes one, just enough for
    ``resolve_stop_target`` to read the dispatch id back out of the worker's prompt
    (the same helper ``tests/test_check_branch.py`` uses, and for the same reason:
    the base resolution only happens for a stop that RESOLVES to a dispatch)."""
    session_dir = tmp_path / "transcripts" / "session"
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    parent = session_dir.with_suffix(".jsonl")
    parent.write_text("", encoding="utf-8")
    (session_dir / "subagents" / f"agent-{agent_id}.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "agentId": agent_id,
                "cwd": "/the/controllers/cwd",
                "gitBranch": "main",
                "message": {
                    "role": "user",
                    "content": (
                        f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return parent


def _matched_stop(kyo, tmp_path: Path, cwd: Path, dispatch_id: str, *args: str):
    """``kyo check-branch --subagent-stop --against main``, exactly as the packaged
    hook fragment spells it — ``main`` included, because that literal is what every
    real SubagentStop in this project passes and a fix that only worked without it
    would never run."""
    agent = "a7c1f0e2b9d4a3c6e"
    payload = json.dumps(
        {
            "session_id": "session",
            "transcript_path": str(_transcript(tmp_path, agent, dispatch_id)),
            "cwd": str(cwd),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "agent_id": agent,
        }
    )
    return kyo(
        "check-branch", "--subagent-stop", "--json", "--against", "main", *args, stdin=payload
    )


# ------------------------------------------------------------------- KYO-294.c1


def test_an_inherited_store_commit_does_not_block_the_worker_s_stop(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The record's own repro. The worker committed nothing under ``kyosei/``; the
    only store diff between the recorded base and its branch is the controller's."""
    dispatch_id, tree, base_sha, inherited_sha, _brief = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "inherited"
    )
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit, touching nothing guarded")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert result.code == 0, result
    data = result.json
    assert data["ok"] is True, data
    assert data["guarded_files"] == [], data
    assert data["against"] == inherited_sha, data
    assert data["against_source"] == "branch-cut-point", data
    assert data["dispatch_base_sha"] == base_sha, data
    assert "kyosei/inherited-dispatch-record.md" not in data["changed_files"], (
        "the controller's own store commit leaked into the worker's diff — the base "
        "was not moved forward to the point the branch was cut from"
    )


def test_the_worker_s_own_store_commit_is_still_refused(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The half that must not move. Judging the branch's own commits is only safe if
    the branch's own commits are exactly what is judged: a worker that writes to the
    store from a worktree cut past the base is refused as loudly as ever."""
    dispatch_id, tree, _base, _inherited, _brief = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "sneaky"
    )
    (tree / "kyosei" / "kinds.yml").write_text("kinds: {sneaky: true}\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's OWN store edit")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert result.code == 2, result
    data = result.json
    assert data["against_source"] == "branch-cut-point", data
    assert any("kyosei/kinds.yml" in path for path in data["guarded_files"]), data


def test_a_second_inherited_store_commit_is_no_different_from_one(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The brief predicts an offset of zero or one commit; nothing makes one the
    limit. A controller that commits the store twice before creating the worktree
    produces the same inherited state, and the cut point is read from the branch
    rather than counted forward from the base."""
    dispatch_id, tree, base_sha, _first, _brief = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "twice"
    )
    # Already cut: a further controller commit lands on main AFTER the worktree
    # exists, which must not move the cut point either.
    later = _controller_store_commit(git_project, "twice-later")
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit")

    result = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert result.code == 0, result
    data = result.json
    assert data["against"] not in (base_sha, later), data
    assert data["guarded_files"] == [], data


def test_a_worktree_cut_exactly_at_the_recorded_base_is_unchanged(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-285's own path, still taken. With no commit between the dispatch and the
    worktree there is nothing later than the recorded base to move to, and both the
    base and the stated source of it are what that record made them.

    The worktree is created FIRST here — the other order is the whole of this
    record, and this is the case that must come out the same as before it.
    """
    project: Path = kyo.project
    tree = tmp_path / "at-base"
    _git(project, "worktree", "add", "-q", "-b", "at-base-branch", str(tree))
    scope = tmp_path / "at-base-touches.txt"
    scope.write_text("+src/at_base.py\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"], title="record cut at the base")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    dispatched = kyo("dispatch", record, "--json", "--worktree", str(tree))
    assert dispatched.code == 0, dispatched
    assert _head(tree) == dispatched.json["worked_from_sha"]
    dispatch_id = dispatched.json["dispatch_id"]
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit")

    result = _matched_stop(kyo, tmp_path, tree, dispatch_id)
    assert result.code == 0, result
    assert result.json["against_source"] == "dispatch-base", result.json


# ------------------------------------------------------------------- KYO-294.c2


def test_the_brief_s_base_sha_note_and_the_stop_check_agree(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-294.c2, both directions in one test, on the fixture the note describes.

    Before this record the brief told a worker that a worktree cut one commit ahead
    of the recorded base was normal and "not something to hand-reconcile", and the
    stop check refused the worker for exactly that. This asserts the pair:

    * the frozen brief carries the sentence, verbatim from the constant the bullet
      renders — delete or reword the note and this fails;
    * the check behaves the way the sentence says on the very worktree the sentence
      is about, in both directions: the inherited store diff passes, and the
      worker's own store commit is still refused.

    So neither half can be changed alone. Whichever one someone edits next, the
    other has to move with it or this test is what says so.
    """
    dispatch_id, tree, _base, _inherited, brief_path = _dispatch_then_commit_then_worktree(
        kyo, tmp_path, "agree"
    )
    brief = brief_path.read_text(encoding="utf-8")
    assert BASE_SHA_INHERITED_STORE_COMMITS in brief, (
        "the Base SHA bullet no longer carries the claim the stop check is asserted "
        "against below; a worker reading this brief is told nothing about which of "
        "its commits the guard will judge"
    )

    # What the note claims, half one: an inherited store diff does not block the stop.
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit")
    passed = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert passed.code == 0, (
        f"the brief says {BASE_SHA_INHERITED_STORE_COMMITS!r}, and the stop check "
        f"blocked this worker anyway: {passed}"
    )
    assert passed.json["guarded_files"] == [], passed.json

    # What the note claims, half two: a store commit the worker makes itself is
    # refused exactly as it always was.
    (tree / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own store edit")
    refused = _matched_stop(kyo, tmp_path, git_project, dispatch_id)
    assert refused.code == 2, (
        f"the brief says {BASE_SHA_INHERITED_STORE_COMMITS!r}, and the stop check let "
        f"the worker's own store commit through: {refused}"
    )
    assert any("kyosei/kinds.yml" in path for path in refused.json["guarded_files"])


def test_the_pinned_note_is_what_the_bullet_actually_renders() -> None:
    """The constant is only worth pinning if the bullet renders it. A copy of the
    sentence retyped into the bullet would keep the test above green while the two
    drifted, so the source is read for the reference rather than the words."""
    source = (Path(__file__).resolve().parents[1] / "src" / "kyosei" / "brief.py").read_text(
        encoding="utf-8"
    )
    assert "BASE_SHA_INHERITED_STORE_COMMITS}" in source, (
        "src/kyosei/brief.py defines the pinned Base SHA sentence but no longer "
        "interpolates it into the bullet"
    )


# ------------------------------------------------------------------- KYO-294.c3


def test_the_skill_says_when_a_hand_back_is_final() -> None:
    """KYO-294.c3. The second cost of the same sequence: the stop hook runs after the
    worker's final message, so a controller that ingests and merges on the hand-back
    alone can merge shas the worker is about to rewrite. The shipped skill carries the
    paragraph; it is read from its packaged source, the one copy this repository keeps
    since it moved onto the plugin (KYO-610 removed the .claude/skills/kyosei/ mirror)."""
    root = Path(__file__).resolve().parents[1]
    copies = [root / "src/kyosei/harness/claude-code/plugin/skills/kyosei" / "SKILL.md"]
    texts = [path.read_text(encoding="utf-8") for path in copies]
    for path, text in zip(copies, texts, strict=True):
        assert "A hand-back is not final until the worker's stop hook has passed" in text, (
            f"{path} does not say when a hand-back becomes final (KYO-294.c3)"
        )
        section = text.split("## 4. Ingesting reports")[1].split("\n## ")[0]
        assert "stop hook has passed" in section, (
            "the paragraph is in the skill but not in the report-ingest section, "
            "which is where a controller reads before merging"
        )
