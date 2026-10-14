"""KYO-592: a repeat stop reports the finding, records it, and does not block again.

Claude Code sets ``stop_hook_active`` on a SubagentStop payload when the agent is already
continuing because a stop hook blocked it. The first stop blocks with the full remedy; the
repeat stop prints the same finding, exits 0, and writes a row the orchestrator reads in
`kyo show <dispatch-id>` and in `kyo report` -- for `kyo check-clean` and `kyo check-branch`
alike (the KYO-128 rule: do not keep blocking a party that cannot act).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei import stop_audit

from .conftest import valid_report
from .test_subagent_stop_worktree import _payload, _running_dispatch, _transcript

AGENT = "a1b2c3d4e5f6a7b8c"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _guard(kyo, command: str, tmp_path: Path, cwd: Path, dispatch_id: str, *, repeat: bool):
    """Run one stop guard as the hook does, for the worker of ``dispatch_id``, with
    ``stop_hook_active`` set to ``repeat``."""
    transcript = _transcript(
        tmp_path, AGENT, f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    )
    payload = json.loads(_payload(transcript, cwd, AGENT))
    payload["stop_hook_active"] = repeat
    args = ["--against", "main"] if command == "check-branch" else []
    return kyo(command, "--subagent-stop", *args, stdin=json.dumps(payload))


def _dirty_worker(kyo, tmp_path: Path) -> tuple[str, Path]:
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-dirty")
    (tree / "unsaved.py").write_text("x = 1\n", encoding="utf-8")
    return dispatch_id, tree


def _store_writing_worker(kyo, tmp_path: Path) -> tuple[str, Path]:
    """A worker branch that commits under `kyosei/`, which `check-branch` refuses."""
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-store")
    _git(tree, "checkout", "-q", "-b", "worker-branch")
    (tree / "kyosei" / "export").mkdir(parents=True, exist_ok=True)
    (tree / "kyosei" / "export" / "forged.json").write_text("{}\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "a worker writing the store")
    return dispatch_id, tree


def _unblocked_rows(kyo, dispatch_id: str) -> list:
    from kyosei.store import open_store

    store = open_store(kyo.project)
    try:
        return stop_audit.unblocked_stops(store, dispatch_id)
    finally:
        store.close()


# ------------------------------------------------------------- c1 and c3, per command


def test_check_clean_blocks_the_first_stop_and_reports_the_repeat(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    dispatch_id, _tree = _dirty_worker(kyo, tmp_path)

    first = _guard(kyo, "check-clean", tmp_path, git_project, dispatch_id, repeat=False)
    assert first.code == 2, first
    assert "worktree-has-uncommitted-work" in first.err
    assert "commit them before finishing" in first.err

    repeat = _guard(kyo, "check-clean", tmp_path, git_project, dispatch_id, repeat=True)
    assert repeat.code == 0, repeat
    assert "NOT blocking" in repeat.out
    assert "worktree-has-uncommitted-work" in repeat.out
    assert "unsaved.py" in repeat.out, "the finding itself is printed, not only a count"
    assert "recorded durably as" in repeat.out


def test_check_branch_blocks_the_first_stop_and_reports_the_repeat(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    dispatch_id, _tree = _store_writing_worker(kyo, tmp_path)

    first = _guard(kyo, "check-branch", tmp_path, git_project, dispatch_id, repeat=False)
    assert first.code == 2, first
    assert "kyosei/export/forged.json" in first.err

    repeat = _guard(kyo, "check-branch", tmp_path, git_project, dispatch_id, repeat=True)
    assert repeat.code == 0, repeat
    assert "NOT blocking" in repeat.out
    assert "kyosei/export/forged.json" in repeat.out
    assert "recorded durably as" in repeat.out


def test_an_absent_stop_hook_active_blocks_as_before(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c3: the field absent is the field false."""
    dispatch_id, _tree = _dirty_worker(kyo, tmp_path)
    transcript = _transcript(
        tmp_path, AGENT, f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    )
    payload = json.loads(_payload(transcript, git_project, AGENT))
    del payload["stop_hook_active"]
    result = kyo("check-clean", "--subagent-stop", stdin=json.dumps(payload))
    assert result.code == 2, result
    assert _unblocked_rows(kyo, dispatch_id) == []


# ------------------------------------------------------------------------------ c2


def test_each_unblocked_stop_is_recorded_and_shown_by_show_and_report(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c2: one row per unblocked stop, per guard, in the events ledger the stop audit
    uses; `kyo show <dispatch-id>` and `kyo report` both list them."""
    dispatch_id, tree = _store_writing_worker(kyo, tmp_path)
    (tree / "unsaved.py").write_text("x = 1\n", encoding="utf-8")
    assert _guard(kyo, "check-clean", tmp_path, git_project, dispatch_id, repeat=False).code == 2
    assert _guard(kyo, "check-branch", tmp_path, git_project, dispatch_id, repeat=False).code == 2
    assert _unblocked_rows(kyo, dispatch_id) == [], "a blocked stop is not an unblocked one"

    assert _guard(kyo, "check-clean", tmp_path, git_project, dispatch_id, repeat=True).code == 0
    assert _guard(kyo, "check-branch", tmp_path, git_project, dispatch_id, repeat=True).code == 0

    rows = _unblocked_rows(kyo, dispatch_id)
    assert [row.guard for row in rows] == ["check-clean", "check-branch"]
    assert rows[0].contract == "worktree-has-uncommitted-work"
    assert any("unsaved.py" in line for line in rows[0].finding)
    assert any("forged.json" in line for line in rows[1].finding)
    exported = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted((kyo.project / "kyosei" / "export" / "_events").glob("*.jsonl"))
    )
    assert stop_audit.STOP_UNBLOCKED_COMMAND in exported, "durable: in the git-canonical ledger"

    shown = kyo("show", dispatch_id)
    assert shown.code == 0, shown
    assert "unblocked repeat stops: 2" in shown.out
    assert rows[0].event_id in shown.out and rows[1].event_id in shown.out
    assert len(kyo("show", dispatch_id, "--json").json["unblocked_stops"]) == 2

    dispatch = kyo("show", dispatch_id, "--json").json
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=dispatch["record_id"],
                worked_from_sha=dispatch["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatch_id, "--file", str(report_path))
    assert ingested.code == 0, ingested
    assert "unblocked repeat stops: 2" in ingested.out, ingested.out
    assert rows[0].event_id in ingested.out and rows[1].event_id in ingested.out
