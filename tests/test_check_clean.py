"""``kyo check-clean``: the ``SubagentStop`` hook's uncommitted-work check, with
the paths ``kyo check-branch`` forbids excluded.

The two halves of that hook contradicted each other in any checkout the controller
also ran ``kyo`` in:
the controller's writes to ``kyosei/export/`` dirtied the tree, the dirty check's
only available remedy was to commit them, committing them put ``kyosei/`` diffs on
the dispatch branch, and ``check-branch`` fails exactly on that — while
``procedures.md`` separately forbids the worker to write there at all. Self-feeding
too: the ``Stop`` hook's own ``kyo sweep`` dirties the export directory as part of
stopping. One worker fired ten times against the loop and gave up.

The fix is that the exclusion list is *derived* from the prohibition list rather
than restated beside it, so the two cannot drift; ``test_the_two_halves_agree``
below is the test that fails if anyone ever restates it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei.contracts import GUARDED_BRANCH_DIRS, WORKER_REPORT_PATH


def _commit_all(project: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=project, check=True, capture_output=True
    )


def test_a_clean_worktree_passes(kyo, git_project: Path) -> None:
    result = kyo("check-clean")
    assert result.code == 0, result
    assert "nothing uncommitted" in result.out


def test_uncommitted_work_blocks_the_stop(kyo, git_project: Path) -> None:
    (git_project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    result = kyo("check-clean")
    assert result.code == 2, result
    assert "worktree-has-uncommitted-work" in result.err
    assert "widget.py" in result.err


def test_committing_clears_the_error_the_message_names(kyo, git_project: Path) -> None:
    """CLAUDE.md's standing rule: an error naming a remedy ships with a test
    proving the remedy clears it. The remedy here is the message's own
    "commit them before finishing"."""
    (git_project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    blocked = kyo("check-clean")
    assert blocked.code == 2
    assert "commit them before finishing" in blocked.err

    _commit_all(git_project, "the worker's own work")
    assert kyo("check-clean").code == 0


def test_a_dirty_store_does_not_block_a_worker(kyo, git_project: Path) -> None:
    """The reported case exactly: the controller's `kyo` writes dirty
    `kyosei/export/` in a shared checkout, and the worker cannot commit them
    (check-branch fails) or write them (procedures.md forbids it). Before this
    command existed the worker had no move at all."""
    export_dir = git_project / "kyosei" / "export"
    (export_dir / "_events.jsonl").write_text('{"stub": true}\n', encoding="utf-8")
    (git_project / "ideas" / "new-thought.md").write_text("# Later\n", encoding="utf-8")

    result = kyo("check-clean")
    assert result.code == 0, result


def test_the_sweep_that_stopping_runs_does_not_fail_the_next_stop(
    kyo, git_project: Path
) -> None:
    """The self-feeding half: the `Stop` hook's own `kyo sweep` writes to
    `kyosei/export/`, so before this fix, stopping cleanly created the condition
    that failed the following stop."""
    assert kyo("sweep").code == 0
    assert kyo("check-clean").code == 0, "a sweep's own writes must not block a worker"


def test_a_dirty_store_and_dirty_work_still_blocks(kyo, git_project: Path) -> None:
    """The exclusion must not become a hole: real uncommitted work still blocks
    even when the store is dirty at the same time."""
    (git_project / "kyosei" / "export" / "_events.jsonl").write_text("{}\n", encoding="utf-8")
    (git_project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    result = kyo("check-clean")
    assert result.code == 2, result
    assert "widget.py" in result.err
    assert "_events.jsonl" not in result.err


def test_untracked_files_count_as_uncommitted(kyo, git_project: Path) -> None:
    """`git status --porcelain` reports these as `??`; a worker leaving new files
    behind is the same failure as leaving edits behind."""
    (git_project / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
    result = kyo("check-clean")
    assert result.code == 2, result
    assert "brand_new.py" in result.err


def test_the_two_halves_agree(kyo, git_project: Path) -> None:
    """The actual fix: check-clean's exclusions are check-branch's prohibitions,
    read from one constant. If someone restates either list, these stop matching
    and this fails."""
    clean = kyo("check-clean", "--json")
    assert clean.code == 0, clean
    assert clean.json["excluded_dirs"] == list(GUARDED_BRANCH_DIRS)

    branch = kyo("check-branch", "--against", "HEAD", "--json")
    assert branch.code == 0, branch
    assert branch.json["guarded_dirs"] == clean.json["excluded_dirs"]


def test_check_clean_opens_no_store(kyo, git_project: Path) -> None:
    """Read-only like check-branch — it has to work in a worker's worktree, which
    never has a `kyosei/kyosei.db` (it is gitignored and never copied there)."""
    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("check-clean")
    assert result.code == 0, result


# ------------------------------------------------------------------------ KYO-206
#
# A consumer's sequence: the brief said
# "write your structured report ... commit your work before you stop" and named no path;
# this command counted any untracked file; so the only way to satisfy both sentences was
# to commit the report. The first two merges carried `report.json` into main, the third
# conflicted on it add/add, the fourth modify/delete, and `kyo touch-drift` then proposed
# the same file as a touch whose `kyo edit --touches-file` line failed validation once the
# controller had dropped it. One named path fixes both ends.


def test_a_worktree_whose_only_untracked_file_is_the_report_passes(
    kyo, git_project: Path
) -> None:
    """KYO-206.c2/c4, the first half of the consumer's path, end to end. Delete the
    `exclusions.append(...)` line in `cmd_check_clean.cmd_check_clean` and this fails: the
    report reads as untracked work and the stop is blocked."""
    (git_project / WORKER_REPORT_PATH).write_text('{"dispatch_id": "TST-1.d1"}\n',
                                                  encoding="utf-8")
    result = kyo("check-clean")
    assert result.code == 0, result
    assert "nothing uncommitted" in result.out
    assert WORKER_REPORT_PATH not in result.out.split("nothing uncommitted")[0]


def test_the_exclusion_holds_without_any_gitignore_entry(kyo, git_project: Path) -> None:
    """The exclusion lives in the command, not only in `.gitignore` — so a project that
    predates the entry `kyo init`/`kyo install-hooks` writes is fixed by upgrading rather
    than by editing a file by hand. Simulated by removing the entry this `kyo init` wrote.
    """
    (git_project / ".gitignore").unlink()
    _commit_all(git_project, "a project from before the ignore entry existed")
    (git_project / WORKER_REPORT_PATH).write_text("{}\n", encoding="utf-8")
    assert kyo("check-clean").code == 0


def test_the_report_exclusion_does_not_hide_real_work(kyo, git_project: Path) -> None:
    """KYO-33's shape: the cure must not be worse than the defect. One file is excluded,
    by exact path — a worker that stopped with uncommitted source beside its report is
    still blocked, and the report is not what the message names."""
    (git_project / WORKER_REPORT_PATH).write_text("{}\n", encoding="utf-8")
    (git_project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    result = kyo("check-clean")
    assert result.code == 2, result
    assert "widget.py" in result.err
    dirty = kyo("check-clean", "--json")
    assert dirty.json["dirty_entries"] == ["?? widget.py"], dirty.json["dirty_entries"]


def test_check_clean_says_which_report_path_it_excluded(kyo, git_project: Path) -> None:
    """Law 6: an exclusion a consumer cannot see is indistinguishable from a filter
    hiding something. Stated in the passing text and carried in `--json`."""
    result = kyo("check-clean")
    assert result.code == 0, result
    assert WORKER_REPORT_PATH in result.out
    assert kyo("check-clean", "--json").json["excluded_report_path"] == WORKER_REPORT_PATH


def test_kyo_report_still_reads_the_report_from_that_path(kyo, git_project: Path) -> None:
    """c2's other half, and the property that makes the exclusion safe: excluding the
    path from the dirty check changes nothing about ingesting it. `--file` reads whatever
    path it is given, and the brief now gives it this one."""
    from .conftest import new_record, valid_report

    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    path = git_project / WORKER_REPORT_PATH
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record,
                worked_from_sha=dispatched.json["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    ingested = kyo(
        "--full", "report", dispatched.json["dispatch_id"], "--file", str(path), "--json"
    )
    assert ingested.code == 0, ingested
    assert ingested.json["record"]["dispatches"][0]["state"] == "reported"
    # And the tree is still clean with the report sitting in it, uncommitted.
    assert kyo("check-clean").code == 0
