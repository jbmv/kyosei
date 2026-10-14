"""KYO-128: the ``SubagentStop`` guards audit the WORKER's worktree, not the
controller's checkout — and a ``git status`` flag is not the same fact as a
difference.

``kyo check-clean`` and ``kyo check-branch`` were correct commands run in the wrong
place. A ``SubagentStop`` hook inherits the CONTROLLER session's cwd — the project
root — never the worktree the stopping subagent was dispatched into, and neither
command was told otherwise. Reported by a consumer, re-verified here. It failed in
both directions:

* **False positive on the controller**, on every dispatch that finished while the
  controller had edits in flight, which during a multi-dispatch shift is most of the
  time. Measured cost: ~229,000 subagent tokens on ONE dispatch that woke three times
  after it had already reported, and over half a million across a single shift —
  workers re-deriving, correctly, that the modified ``kyosei/export/`` paths were the
  controller's and not their own. They could not clear the condition: those paths
  belong to a branch their brief forbids them to commit on.
* **False negative on the worker**, which is the one that matters. A dispatch ending
  with genuinely uncommitted work in its own worktree passed silently, because that
  worktree was never examined. That is exactly the failure the hook exists to catch.

Every test below keeps the CONTROLLER's checkout dirty throughout, with edits no
worker made. No assertion may ever cite them; several assert they are absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kyosei.git_queries import GIT_PINNED_CONFIG

from .conftest import new_record, valid_report

CONTROLLER_EDIT = "the-controllers-own-uncommitted-file.txt"


# ------------------------------------------------------------------ fixtures/helpers


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _mkrepo(path: Path, repo: Path) -> None:
    """A detached linked worktree of ``repo`` standing in for a dispatch worktree, with
    ``tracked.txt`` (which the tests below edit) committed on the project's trunk.

    KYO-402: this used to be an independent ``git init`` repository, which `kyo
    dispatch --worktree` now refuses — a directory that is not a worktree of the code
    repository is exactly what `kyo worktrees --prune` used to delete. What these
    commands read is still ``git status`` and ``git diff`` in the directory.
    """
    if not (repo / "tracked.txt").exists():
        # Committed on the project's own trunk, and only that path: the controller's
        # checkout stays exactly as dirty as each test made it, and the worktree cut
        # below starts at the sha `kyo dispatch` then stamps, with nothing of its own.
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-q", "-m", "tracked.txt", "--", "tracked.txt")
    _git(repo, "worktree", "add", "-q", "--detach", str(path))


def _running_dispatch(kyo, tmp_path: Path, name: str) -> tuple[str, Path]:
    """A dispatch in state ``running`` with a declared worktree, and that worktree."""
    tree = tmp_path / name
    _mkrepo(tree, kyo.project)
    record = new_record(kyo, criteria=["a"], title=f"record for {name}")
    # KYO-48: an undeclared scope is its own refusal, so a dispatch has to say what it
    # touches — and a declared touch must resolve to a real path in the project.
    project: Path = kyo.project
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / f"{name}.py").write_text("# worked on by this dispatch\n", encoding="utf-8")
    scope = tmp_path / f"{name}-touches.txt"
    scope.write_text(f"src/{name}.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    result = kyo("dispatch", record, "--json", "--worktree", str(tree))
    assert result.code == 0, result
    return result.json["dispatch_id"], tree


def _transcript(tmp_path: Path, agent_id: str, prompt: str) -> Path:
    """A session transcript shaped the way Claude Code writes one.

    ``<project>/<session>.jsonl`` beside ``<project>/<session>/subagents/agent-<id>.jsonl``,
    whose first ``user`` record is the prompt the subagent was spawned with. Verified
    against a real transcript on disk, including that the record's own ``cwd`` and
    ``gitBranch`` fields hold the CONTROLLER's values and so cannot be used for this.

    KYO-325.c2: and the ``agent-<id>.meta.json`` sidecar the harness writes BESIDE it,
    because that pair is the real directory layout and a fixture carrying only half of
    it cannot fail for a defect that lives in the other half. Its keys are copied from
    real sidecars the harness wrote; a top-level worker's carries no
    ``parentAgentId``, which is exactly what makes it a worker and not a helper
    (KYO-154).
    """
    session_dir = tmp_path / "transcripts" / "session"
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    parent = session_dir.with_suffix(".jsonl")
    parent.write_text("", encoding="utf-8")
    (session_dir / "subagents" / f"agent-{agent_id}.meta.json").write_text(
        json.dumps(
            {
                "agentType": "general-purpose",
                "description": "a dispatched worker",
                "toolUseId": f"toolu_{agent_id}",
                "spawnDepth": 1,
                "requestShape": "background",
                "requestNonInteractive": True,
                "model": "opus",
            }
        ),
        encoding="utf-8",
    )
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


def _payload(transcript: Path, cwd: Path, agent_id: str | None = None) -> str:
    payload = {
        "session_id": "session",
        "transcript_path": str(transcript),
        # The hook's cwd is the CONTROLLER's, always. That is the whole defect.
        "cwd": str(cwd),
        "hook_event_name": "SubagentStop",
        "stop_hook_active": False,
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return json.dumps(payload)


@pytest.fixture
def controller_dirty(git_project: Path) -> Path:
    """The controller's checkout, dirty for the whole of every test in this file."""
    (git_project / CONTROLLER_EDIT).write_text("edited by the controller\n", encoding="utf-8")
    return git_project


def _stop(kyo, command: str, tmp_path: Path, cwd: Path, dispatch_id: str | None, *args: str):
    """Run one guard as the hook runs it, for a subagent whose prompt names ``dispatch_id``."""
    agent = "a1b2c3d4e5f6a7b8c"
    prompt = (
        f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
        if dispatch_id
        else "You are a research subagent. Read some files and report back."
    )
    transcript = _transcript(tmp_path, agent, prompt)
    return kyo(
        command, "--subagent-stop", *args, stdin=_payload(transcript, cwd, agent)
    )


# ------------------------------------------------- c1/c2/c3: which tree gets audited


def test_the_check_reads_the_declared_worktree_not_the_hooks_cwd(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c1. The named subject of the verdict is the worker's own worktree."""
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-subject")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id, "--json")
    assert result.code == 0, result
    data = result.json
    assert data["root"] == str(tree)
    assert data["subject"]["outcome"] == "matched"
    assert data["subject"]["dispatch_id"] == dispatch_id
    assert data["checked_the_worker_tree"] is True


def test_a_dirty_worker_worktree_is_blocked_while_the_controller_is_dirty(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c2 — the false negative, which is the half that matters.

    Before this, the worktree was never examined, so this stop passed silently.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-dirty")
    (tree / "report.json").write_text('{"unreported": true}\n', encoding="utf-8")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert result.code == 2, result
    assert "report.json" in result.err
    assert CONTROLLER_EDIT not in result.err, (
        "the block cited the controller's file — the false positive is back"
    )


def test_a_clean_worker_worktree_passes_while_the_controller_is_dirty(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c3 — the false positive, and the expensive one.

    The controller's checkout is dirty with an edit no worker made. Before this, every
    dispatch finishing in that state was blocked on a condition it was powerless to
    clear, and woken again to re-derive the same finding.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-clean")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert result.code == 0, result
    assert CONTROLLER_EDIT not in result.out + result.err


def test_the_merge_guard_is_retargeted_too_and_stops_being_vacuous(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c1 for `check-branch`, whose failure here was sharper than a wrong tree.

    Run from the inherited cwd it was VACUOUS: the controller's checkout sits on
    `main`, so the branch defaulted to `main` and it diffed `main` against `main` —
    zero changed files, "clean", every time, for every dispatch that used a worktree.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-branch")
    _git(tree, "checkout", "-q", "-b", "worker-branch")
    (tree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the worker's own commit")

    result = _stop(
        kyo, "check-branch", tmp_path, controller_dirty, dispatch_id, "--json", "--against", "main"
    )
    assert result.code == 0, result
    data = result.json
    assert data["repo"] == str(tree)
    assert data["branch"] == "worker-branch", (
        "the guard read the controller's branch, so it was comparing main against main"
    )
    assert data["changed_files"] == ["feature.py"]


# ------------------------------------------- c6: an unmatched stop names its subject


def test_an_unmatched_stop_names_the_tree_it_fell_back_to(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c6. A verdict that does not name its subject is the defect being fixed.

    A silent fallback to the cwd would reproduce it exactly, so the fallback says both
    which tree it read and why it read that one.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-elsewhere")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 0, result
    combined = result.out + result.err
    assert str(controller_dirty) in combined, "the fallback did not name the tree it read"
    assert "NOTHING WAS CHECKED" in combined
    assert dispatch_id in combined, "the unresolved report did not name the candidates"


def test_an_unmatched_stop_does_not_block(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c6, the argued half.

    The controller's checkout is dirty and unmatched. Blocking here is the measured
    failure this record ends: the paths cited belong to the controller's branch, which
    the woken worker is forbidden to commit and forbidden to write, so it is a
    condition the only party woken is powerless to clear. Law 6 is met by naming the
    outcome — "could not tell", reported as itself — not by picking a verdict.
    """
    _running_dispatch(kyo, tmp_path, "wt-somewhere")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert result.code == 0, result
    data = result.json
    assert data["subject"]["outcome"] == "unresolved"
    assert data["checked_the_worker_tree"] is False, (
        "an unresolved stop must not claim to have checked the worker's tree"
    )
    assert data["dirty_entries"], "the fixture's controller edit should have been seen"


def test_with_no_declared_worktree_in_play_the_cwd_is_still_guarded(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The case the old behaviour got right by accident, now right on purpose.

    No running dispatch declared a worktree, so the project root IS every dispatch's
    worktree and the guard must still block. Not blocking here would trade one silent
    failure for another.
    """
    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 2, result
    assert CONTROLLER_EDIT in result.err
    assert "no-declared-worktree-in-play" not in result.err  # rendered as prose, not a token


# --------------- KYO-325: a store that cannot be READ is not a store with nothing in it
#
# Measured on this project's own store. `kyosei/config.yml` gained
# `worker_command_timeout_ceiling_seconds` (KYO-312's opt-in). The `kyo` the hooks
# actually run was an installed build behind the source tree, and it refuses a config
# key no code path of ITS build reads. So `ctx.store()` raised inside
# `_declared_worktree_rows`, whose `except Exception: return []` handed
# `resolve_stop_target` an empty candidate list, indistinguishable from "no dispatch
# declared a worktree". Until the build caught up:
#
#   * the stop-audit ledger recorded NOTHING — zero rows, including none of the
#     regular ticks that had been arriving every few seconds;
#   * three workers (KYO-263.d1, KYO-288.d1, KYO-316.d1) stopped inside that window
#     and `check-clean`/`check-branch` reported "clean" about the CONTROLLER's
#     checkout, having never looked at the worktrees they were dispatched into —
#     KYO-128's exact defect, back by a new route;
#   * `kyo show` reads "worker tokens: unknown" for those three dispatches, while the
#     dispatches outside the window report real totals.
#
# The tests below are that story, in the order the harness produces it.


def test_a_store_that_cannot_be_read_is_not_a_store_with_no_worktrees_in_it(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The defect itself: the store is unreadable and the guard says so.

    The fixture breaks the store the way it really broke — a config key the build
    does not read — and the full real directory layout is in play
    (``agent-<id>.jsonl`` plus its ``.meta.json`` sidecar under ``subagents/``),
    because a dispatched worker's stop is what has to stop being mis-answered.

    Without the fix this passes silently with exit 0 and the words "no dispatch has
    declared a worktree", which is a POSITIVE claim about a question nobody could
    answer.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-unreadable")
    (tree / "left-behind.py").write_text("uncommitted work\n", encoding="utf-8")
    config = kyo.project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\na_key_no_code_path_reads: 240\n",
        encoding="utf-8",
    )

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    said = result.out + result.err

    assert "STORE could not be read" in said, said
    # The remedy is named, and it is the one that actually cleared it.
    assert "a_key_no_code_path_reads" in said
    assert "upgrade" in said
    # And the sentence that was wrong is gone: this stop is NOT placed anywhere.
    assert "the stopping worker's own worktree" not in said
    assert "IS every dispatch's worktree here" not in said


def test_an_unreadable_store_says_the_stop_could_not_be_recorded_either(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The half that made it invisible.

    `record_stop_audit` writes no row for `no-declared-worktree-in-play` ON PURPOSE —
    "there was nothing about this stop that could have gone unaudited" — and that
    sentence was being printed over a stop nobody could say anything about. The store
    is also what a stop is recorded IN, so this outcome genuinely cannot leave a
    durable row; what it must do instead is say so, in the one place it still exists.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-unrecordable")
    config = kyo.project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\na_key_no_code_path_reads: 1\n",
        encoding="utf-8",
    )

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    said = result.out + result.err

    assert "NOT RECORDED" in said, said
    assert "nothing about this stop that could have gone unaudited" not in said


def test_the_stop_resolves_again_by_itself_once_the_store_can_be_read(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """And the remedy clears it — which is how the real ledger came back.

    Nothing about the search path, the agent id or the worktree comparison ever
    changed; the same stop, against the same transcript and sidecar, matches the
    moment the store opens. Pairs with the two above so "the store was the problem"
    is a demonstrated claim and not a diagnosis.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-recovered")
    (tree / "left-behind.py").write_text("uncommitted work\n", encoding="utf-8")
    config = kyo.project / "kyosei" / "config.yml"
    was = config.read_text(encoding="utf-8")
    config.write_text(was + "\na_key_no_code_path_reads: 1\n", encoding="utf-8")
    broken = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert "STORE could not be read" in broken.out + broken.err

    config.write_text(was, encoding="utf-8")
    fixed = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)

    said = fixed.out + fixed.err
    assert "the stopping worker's own worktree" in said, said
    assert str(tree) in said
    # And it is the WORKER's uncommitted file that blocks, never the controller's.
    assert fixed.code == 2, fixed
    assert "left-behind.py" in said
    assert CONTROLLER_EDIT not in said


def test_a_dispatch_id_matches_as_a_whole_token(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """`KYO-12.d1` must not match inside `KYO-12.d11`.

    The dispatch id is what is matched precisely because Kyosei minted it; matching it
    loosely would give that specificity straight back.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-token")
    (tree / "report.json").write_text("{}\n", encoding="utf-8")

    agent = "abc123"
    transcript = _transcript(
        tmp_path, agent, f"You are a dispatched worker on dispatch {dispatch_id}9."
    )
    result = kyo(
        "check-clean",
        "--subagent-stop",
        "--json",
        stdin=_payload(transcript, controller_dirty, agent),
    )
    assert result.code == 0, result
    assert result.json["subject"]["outcome"] == "unresolved", (
        f"{dispatch_id}9 was matched as {dispatch_id}"
    )


# ------------------------ c4/c5: a status flag is not the same fact as a difference


def test_a_real_content_difference_is_still_counted(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c5, the control. The content re-test must not swallow real work."""
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-real")
    (tree / "tracked.txt").write_text("genuinely changed\n", encoding="utf-8")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert result.code == 2, result
    assert "tracked.txt" in result.err


def test_work_staged_then_reverted_in_the_worktree_is_still_counted(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c5, the hole the obvious version of c4 would have opened.

    Staged with one content and restored in the working tree, a path reports `MM` and
    `git diff HEAD` on it is EMPTY — while `git commit` would still commit the staged
    content. Testing only the working tree against HEAD would drop this as
    "identical to HEAD", losing real work one layer below the false negative being
    fixed. This is a real git state, not a forged one.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-staged")
    (tree / "tracked.txt").write_text("staged content\n", encoding="utf-8")
    _git(tree, "add", "tracked.txt")
    (tree / "tracked.txt").write_text("one\n", encoding="utf-8")  # back to HEAD's content
    assert _git(tree, "diff", "--quiet", "HEAD", "--", "tracked.txt").returncode == 0, (
        "fixture precondition: HEAD vs the working tree must be empty here"
    )

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert result.code == 2, result
    assert "tracked.txt" in result.err


def test_an_untracked_path_stays_dirty(kyo, controller_dirty: Path, tmp_path: Path) -> None:
    """An untracked path has no HEAD blob, so "identical to HEAD" is not a question
    that can be asked of it — and this is the case the guard most exists for. A worker
    that died before its first commit leaves output that is untracked by definition;
    the consumer's own example of this check working correctly was an untracked
    `report.json`."""
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-untracked")
    (tree / "report.json").write_text('{"unreported": true}\n', encoding="utf-8")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert result.code == 2, result
    assert "report.json" in result.err


def test_a_stat_only_flag_is_not_counted_as_uncommitted_work(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch
) -> None:
    """KYO-128.c4.

    `git status --porcelain` can flag a path whose blob is identical to HEAD — from a
    stale index stat-cache entry, or from a commit landing between the hook's own two
    observations. Observed in the field as `M README.md` with `git diff` and
    `git diff HEAD` both empty and the two blobs the same object.

    Neither producer is reachable deterministically from a test, so the STATUS CALL is
    forged and every other git call runs for real — which is the shape of the claim
    anyway: given that git status flags a path and git reports no difference for it,
    the path is not counted. The consumer's own harness forged the same line the same
    way, through a `git` shim on PATH.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-statonly")

    from kyosei import cmd_check_clean

    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if (
            # KYO-417: run_git puts its pinned `-c` settings between `git` and the verb.
            [a for a in args if a not in GIT_PINNED_CONFIG][:3] == ["git", "status", "--porcelain"]
            and Path(kwargs.get("cwd", ".")) == tree
        ):
            return subprocess.CompletedProcess(args, 0, " M tracked.txt\0", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cmd_check_clean.subprocess, "run", fake_run)

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id, "--json")
    assert result.code == 0, result
    data = result.json
    assert data["flagged_entries"] == [" M tracked.txt"], (
        "the fixture did not reach the code under test"
    )
    assert data["stat_only_entries"] == [" M tracked.txt"]
    assert data["dirty_entries"] == []


def test_the_stat_only_exclusion_is_reported_rather_than_silent(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch
) -> None:
    """A filter nobody can see is indistinguishable from a check that stopped working.

    Same standing rule as `_guarantee_lines`: what was ruled out is stated, not
    inferred from a passing exit code.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-statonly-said")

    from kyosei import cmd_check_clean

    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if (
            # KYO-417: run_git puts its pinned `-c` settings between `git` and the verb.
            [a for a in args if a not in GIT_PINNED_CONFIG][:3] == ["git", "status", "--porcelain"]
            and Path(kwargs.get("cwd", ".")) == tree
        ):
            return subprocess.CompletedProcess(args, 0, " M tracked.txt\0", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cmd_check_clean.subprocess, "run", fake_run)

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id)
    assert result.code == 0, result
    assert "tracked.txt" in result.out
    assert "identical to" in result.out


# ------------------------------------------------ the flag is what changes behaviour


# ------------- KYO-201: a worker outlives the ingest of its own report, and says so
#
# The failure this section reproduces was observed in the field, not constructed.
# KYO-174's worker wrote its report, the controller ingested it while the worker was
# still alive waiting on a background suite run, and the worker's next `SubagentStop`
# was resolved — confidently, with the sentence "the stopping worker's own worktree" —
# to KYO-194.d1's tree, where it reported nine uncommitted files belonging to another
# live worker. Two ordinary controller acts made it reachable: naming another dispatch
# in a prompt (which is the RIGHT thing to do when two dispatches touch one file), and
# ingesting a report before the worker's process ended (the report file is the
# deliverable; nothing says the worker is finished when it exists).
#
# Every test below uses a brief `kyo dispatch` actually generated as the worker's
# prompt, rather than a hand-written sentence, because the two halves of the trap are
# both things generation puts there: the `- **Dispatch id**:` header naming the
# dispatch as its own, and the "Other dispatches running" bullet naming everybody
# else's.


def _dispatch_with_worktree(kyo, tmp_path: Path, name: str, brief_out: Path | None = None):
    """One dispatch with a declared worktree; returns `kyo dispatch --json` and the tree.

    `_running_dispatch` above returns only the id and the path, and every test here
    needs the record id and base sha as well (to ingest a report) or the frozen brief
    (to use as the worker's prompt).
    """
    tree = tmp_path / name
    _mkrepo(tree, kyo.project)
    record = new_record(kyo, criteria=["a"], title=f"record for {name}")
    project: Path = kyo.project
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / f"{name}.py").write_text("# worked on by this dispatch\n", encoding="utf-8")
    scope = tmp_path / f"{name}-touches.txt"
    scope.write_text(f"src/{name}.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    args = ["dispatch", record, "--json", "--worktree", str(tree)]
    if brief_out is not None:
        args += ["--brief-out", str(brief_out)]
    result = kyo(*args)
    assert result.code == 0, result
    return result.json, tree


def _ingest_report(kyo, info: dict, tmp_path: Path) -> None:
    """Move a dispatch from `running` to `reported` — the act this record turns on.

    The worker is still alive at this point in every test below, which is the whole
    premise: `kyo report` ingests a FILE, and a file exists before its author stops.
    """
    path = tmp_path / f"report-{info['dispatch_id']}.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=info["dispatch_id"],
                record_id=info["record_id"],
                worked_from_sha=info["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    result = kyo("report", info["dispatch_id"], "--file", str(path))
    assert result.code == 0, result


def _stop_with_prompt(kyo, tmp_path: Path, cwd: Path, prompt: str, *args: str):
    """Run `check-clean` as the hook runs it, for an agent spawned with ``prompt``."""
    agent = "a1b2c3d4e5f6a7b8c"
    transcript = _transcript(tmp_path, agent, prompt)
    return kyo(
        "check-clean", "--subagent-stop", *args, stdin=_payload(transcript, cwd, agent)
    )


def _two_dispatches(kyo, tmp_path: Path):
    """B dispatched first and still running, then A — so A's OWN brief names B.

    That ordering is not decoration: the "Other dispatches running" bullet is frozen
    into a brief from the dispatches running at that instant, so dispatching B first
    is what makes A's generated prompt name two dispatches at all.
    """
    b_info, b_tree = _dispatch_with_worktree(kyo, tmp_path, "wt-201-other")
    brief_path = tmp_path / "a-brief.md"
    a_info, a_tree = _dispatch_with_worktree(kyo, tmp_path, "wt-201-own", brief_out=brief_path)
    brief = brief_path.read_text(encoding="utf-8")
    assert b_info["dispatch_id"] in brief, (
        "fixture precondition: A's own brief must name B, or nothing here is a trap"
    )
    return a_info, a_tree, b_info, b_tree, brief


def test_a_worker_whose_report_was_ingested_resolves_to_its_own_worktree(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-201.c1 and c3 — the reproduction, exactly as the criterion spells it out.

    A is `reported` while its worker is still running, B is `running`, A's prompt
    names both, and the stop is A's. Narrow the candidate set back to
    `store.running_dispatches()` and this goes red: A leaves that set at the instant
    of the ingest above, so the only candidate its prompt names is B's.
    """
    a_info, a_tree, b_info, b_tree, brief = _two_dispatches(kyo, tmp_path)
    _ingest_report(kyo, a_info, tmp_path)

    result = _stop_with_prompt(kyo, tmp_path, controller_dirty, brief, "--json")
    assert result.code == 0, result
    subject = result.json["subject"]
    assert subject["outcome"] == "matched", subject
    assert subject["dispatch_id"] == a_info["dispatch_id"], subject
    assert result.json["root"] == str(a_tree)
    assert str(b_tree) not in result.json["root"]


def test_a_reported_workers_stop_does_not_report_another_workers_files(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-201, the measured harm rather than the mechanism.

    In the field this stop was told its own tree was dirty, and named nine files
    another live worker had not yet committed. A worker less careful than the one it
    happened to would have committed or discarded them — which is the harm KYO-128
    and KYO-154 were filed to prevent, reached by a new path.
    """
    a_info, _a_tree, _b_info, b_tree, brief = _two_dispatches(kyo, tmp_path)
    (b_tree / "another-workers-work-in-progress.py").write_text("x = 1\n", encoding="utf-8")
    _ingest_report(kyo, a_info, tmp_path)

    result = _stop_with_prompt(kyo, tmp_path, controller_dirty, brief)
    combined = result.out + result.err
    assert result.code == 0, result
    assert "another-workers-work-in-progress.py" not in combined, (
        "the stop was handed another dispatch's live, uncommitted work as its own"
    )
    assert CONTROLLER_EDIT not in combined


def test_the_brief_header_says_which_named_dispatch_is_the_stops_own(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-201.c2, on its own. Both dispatches are running here, so widening the
    candidate set changes nothing and only the preference can decide this.

    Delete the header preference and the prompt names two worktrees, which is the
    existing refusal — correct where nothing says which is its own, and needless
    here, where the brief's own header does.
    """
    a_info, a_tree, _b_info, _b_tree, brief = _two_dispatches(kyo, tmp_path)

    result = _stop_with_prompt(kyo, tmp_path, controller_dirty, brief, "--json")
    assert result.code == 0, result
    subject = result.json["subject"]
    assert subject["outcome"] == "matched", subject
    assert subject["dispatch_id"] == a_info["dispatch_id"]
    assert result.json["root"] == str(a_tree)


def test_the_reason_lists_every_named_dispatch_with_its_state(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-201.c2's second half. A reader of the hook's message has to be able to see
    what was considered and why one won — a preference nobody can see is
    indistinguishable from a lucky guess, which is what the old behaviour was."""
    a_info, _a_tree, b_info, _b_tree, brief = _two_dispatches(kyo, tmp_path)
    _ingest_report(kyo, a_info, tmp_path)

    result = _stop_with_prompt(kyo, tmp_path, controller_dirty, brief, "--json")
    assert result.code == 0, result
    subject = result.json["subject"]
    # Pinned to the outcome the preference produced, not just to the sentence: the
    # refusal below carries the same list, so a test that read `why` alone would
    # stay green with the preference deleted and would be guarding nothing.
    assert subject["outcome"] == "matched", subject
    why = subject["why"]
    assert f"{a_info['dispatch_id']} (reported)" in why, why
    assert f"{b_info['dispatch_id']} (running)" in why, why


def test_a_prompt_naming_two_worktrees_with_no_header_is_still_refused(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The refusal KYO-201 keeps. The preference is a preference, not a tie-break of
    last resort: with nothing saying which dispatch the prompt is ABOUT, picking one
    is the thing this record is about."""
    a_info, _a_tree, b_info, _b_tree, _brief = _two_dispatches(kyo, tmp_path)
    prompt = (
        f"Work on {a_info['dispatch_id']}. Note that {b_info['dispatch_id']} is "
        "editing the same file, so rebase before you merge."
    )

    result = _stop_with_prompt(kyo, tmp_path, controller_dirty, prompt, "--json")
    assert result.code == 0, result
    assert result.json["subject"]["outcome"] == "unresolved", result.json["subject"]
    assert result.json["subject"]["dispatch_id"] is None


def test_a_header_naming_a_dispatch_that_is_no_candidate_refuses_rather_than_guessing(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The preference decides in both directions, and this is the direction that
    matters. A prompt whose header claims a dispatch this store cannot place must not
    then be matched to some other dispatch it merely mentions — that IS KYO-201."""
    _a_info, _a_tree, b_info, b_tree, _brief = _two_dispatches(kyo, tmp_path)
    prompt = (
        "# Dispatch brief: KYO-9999 — a dispatch this store has never heard of\n"
        "\n"
        "- **Dispatch id**: KYO-9999.d1\n"
        f"- **Other dispatches running**: 1 — {b_info['dispatch_id']}.\n"
    )

    result = _stop_with_prompt(kyo, tmp_path, controller_dirty, prompt, "--json")
    assert result.code == 0, result
    subject = result.json["subject"]
    assert subject["outcome"] == "unresolved", subject
    assert result.json["root"] != str(b_tree)


def test_the_header_pattern_reads_a_brief_kyosei_actually_generated(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The preference is only worth anything while it matches what `generate_brief`
    writes, and those are two files that can drift apart in silence. This runs the
    resolver's own pattern over a brief the tool produced, so a change to the header
    bullet's shape lands here rather than in a hook that quietly stops preferring."""
    from kyosei.stop_target import _brief_own_dispatch

    brief_path = tmp_path / "generated-brief.md"
    info, _tree = _dispatch_with_worktree(kyo, tmp_path, "wt-201-header", brief_out=brief_path)

    assert _brief_own_dispatch(brief_path.read_text(encoding="utf-8")) == info["dispatch_id"]


def test_a_helper_of_a_worker_whose_brief_names_two_dispatches_is_still_a_helper(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-154 survives KYO-201's widening, and only because the preference reaches
    the parent lookup too.

    A worker's own prompt IS its brief, and a brief names every other dispatch that
    was running when it froze. So the moment the candidate set stopped being
    "running only", `_dispatches_named_by(parent_prompt, ...)` began returning two
    rows where the helper check requires exactly one — which would have retired
    KYO-154's outcome silently, with no test red anywhere, and put the helper back
    on its parent's dirty tree. Applying the header preference to the parent prompt
    is what keeps it firing; it also makes it correct, because under the old narrow
    set a parent whose brief named one still-running other dispatch was placed as
    THAT dispatch — the same misattribution, one level up.
    """
    from .test_kyo154_helper_stops import HELPER_AGENT, WORKER_AGENT, _write_agent

    a_info, a_tree, _b_info, _b_tree, brief = _two_dispatches(kyo, tmp_path)
    (a_tree / "work-in-progress.py").write_text("# the PARENT is still editing\n", "utf-8")

    session_dir = tmp_path / "transcripts" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir.with_suffix(".jsonl").write_text("", encoding="utf-8")
    _write_agent(session_dir, WORKER_AGENT, brief)
    _write_agent(
        session_dir,
        HELPER_AGENT,
        f"Survey the README for dispatch {a_info['dispatch_id']} and report back. Read-only.",
        spawned_by=WORKER_AGENT,
    )

    result = kyo(
        "check-clean",
        "--subagent-stop",
        "--json",
        stdin=_payload(session_dir.with_suffix(".jsonl"), controller_dirty, HELPER_AGENT),
    )
    assert result.code == 0, result
    subject = result.json["subject"]
    assert subject["outcome"] == "helper-of-a-dispatched-worker", subject
    assert subject["helper_of"] == a_info["dispatch_id"], subject


def test_a_dispatch_whose_worktree_is_gone_is_not_a_candidate(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The one narrowing on "whatever its state", stated as a test.

    A pruned worktree cannot be the tree a stopping worker worked in, cannot hold
    uncommitted work, and cannot be audited — so it is not an answer, it is a
    confident answer about nothing. It is also what keeps the cwd guarded: without
    this, one finished dispatch anywhere in a project's history would make every
    research subagent's stop report "declared worktrees are in play" forever.
    """
    info, tree = _dispatch_with_worktree(kyo, tmp_path, "wt-201-pruned")
    _ingest_report(kyo, info, tmp_path)
    shutil.rmtree(tree)

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 2, result
    assert CONTROLLER_EDIT in result.err, (
        "a worktree that no longer exists was still counted as in play, so the cwd — "
        "which IS every live dispatch's tree here — stopped being guarded"
    )


def test_without_the_flag_nothing_reads_stdin_and_the_subject_is_the_cwd(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """Run by hand, both commands mean "the repository I am standing in", which is
    correct and is not what KYO-128 is about. The mechanism is the flag; a test that
    did not pin this would let the payload read leak into every invocation."""
    _running_dispatch(kyo, tmp_path, "wt-unused")

    result = kyo("check-clean", "--json")
    assert result.code == 2, result
    assert result.json["subject"] is None
    assert result.json["root"] == str(controller_dirty)
