"""Where a dispatch's work happens is declared, recorded, and refused if taken.

Touch-disjointness
silently *implied* an isolation the tool never arranged and could not observe. Two
correctly-disjoint dispatches into one checkout produced three distinct failures
in a single session: one worker's commit landed on the other's branch (that branch
now permanently carries it, and merge order became load-bearing), a test failure
was attributed to the wrong tree, and the controller told its operator the workers
were not colliding when they were.

The path is **declared**, never sniffed. `kyo dispatch` runs in the controller's
cwd, which is normally the main checkout while the worker is sent elsewhere — so
reading the current worktree would record the wrong place with total confidence.
Defaulting to the project root when nothing is declared is what makes the
undeclared case collide, which is exactly the situation that cost that session
three failures.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from kyosei import contracts

from .conftest import git_worktree, legacy_unresolved_rows, new_record


def _dispatch(kyo, record: str, *args: str):
    return kyo("dispatch", record, "--json", *args)


def _with_scope(kyo, tmp_path, record: str, path: str) -> str:
    """Declare a touch list on an existing record.

    KYO-48: an UNDECLARED scope is now its own refusal, distinct from a collision,
    so a test about where work happens has to say what the work touches — which is
    what these tests always claimed about their records anyway ("both records are
    touch-disjoint"). Before KYO-48 that claim was never checked: two records
    declaring nothing read as disjoint from each other and from everything else.
    """
    scope = tmp_path / f"{record}-touches.txt"
    scope.write_text(path + "\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    return record


def test_a_dispatch_records_where_its_work_happens(kyo, git_project: Path, tmp_path) -> None:
    tree = tmp_path / "wt-1"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, criteria=["a"])
    result = _dispatch(kyo, record, "--worktree", str(tree))
    assert result.code == 0, result

    dispatch = kyo("show", record, "--json").json["dispatches"][0]
    # KYO-52: recorded relative to the project root, never resolved to an absolute
    # path. The fixture tree is a sibling of the project, so this is the `..` form.
    assert dispatch["worktree"] == os.path.relpath(tree.resolve(), git_project.resolve())
    assert not os.path.isabs(dispatch["worktree"])


def test_an_undeclared_dispatch_defaults_to_the_project_root(kyo, git_project: Path) -> None:
    """Which is the shared-checkout case, on purpose — see the next test."""
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record).code == 0
    dispatch = kyo("show", record, "--json").json["dispatches"][0]
    # KYO-52: the project root relative to itself is ".", not its absolute path.
    assert dispatch["worktree"] == "."


def test_two_dispatches_into_one_checkout_are_refused(kyo, git_project: Path, tmp_path) -> None:
    """The shared-checkout case. Both records are touch-disjoint and would both have been
    allowed before; sharing a branch and an index is what actually hurt."""
    first = _with_scope(
        kyo, tmp_path, new_record(kyo, kind="bug", title="First", criteria=["a"]),
        "kyosei/config.yml",
    )
    second = _with_scope(
        kyo, tmp_path, new_record(kyo, kind="bug", title="Second", criteria=["b"]),
        "kyosei/kinds.yml",
    )
    assert _dispatch(kyo, first).code == 0

    result = kyo("dispatch", second)
    assert result.code == 2, result
    assert "dispatch-worktree-collision" in result.err
    assert "--worktree" in result.err


def test_giving_the_second_its_own_worktree_clears_the_refusal(
    kyo, git_project: Path, tmp_path
) -> None:
    """The remedy the error names, proven to work (CLAUDE.md's standing rule)."""
    first = _with_scope(
        kyo, tmp_path, new_record(kyo, kind="bug", title="First", criteria=["a"]),
        "kyosei/config.yml",
    )
    second = _with_scope(
        kyo, tmp_path, new_record(kyo, kind="bug", title="Second", criteria=["b"]),
        "kyosei/kinds.yml",
    )
    assert _dispatch(kyo, first).code == 0
    assert kyo("dispatch", second).code == 2

    tree = tmp_path / "wt-2"
    git_worktree(kyo.project, tree)
    assert _dispatch(kyo, second, "--worktree", str(tree)).code == 0


def test_force_overrides_the_worktree_refusal(kyo, git_project: Path) -> None:
    """Symmetric with touch-disjointness, which is the right precedent: the tool
    already knows how to say "not while that one is running", and how to be told
    otherwise."""
    first = new_record(kyo, kind="bug", title="First", criteria=["a"])
    second = new_record(kyo, kind="bug", title="Second", criteria=["b"])
    assert _dispatch(kyo, first).code == 0
    assert _dispatch(kyo, second, "--force").code == 0


def test_a_closed_dispatch_frees_its_worktree(kyo, git_project: Path, tmp_path) -> None:
    """Only *running* dispatches hold a worktree — the check reads
    `running_dispatches`, so finishing one releases the path."""
    first = new_record(kyo, kind="bug", title="First", criteria=["a"])
    second = new_record(kyo, kind="bug", title="Second", criteria=["b"])
    dispatch_id = _dispatch(kyo, first).json["dispatch_id"]
    assert kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "worker died"
    ).code == 0

    assert _dispatch(kyo, second).code == 0


def test_a_nonexistent_worktree_is_refused_before_anything_is_written(
    kyo, git_project: Path, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--worktree", str(tmp_path / "never-created"))
    assert result.code == 1, result
    assert "not a directory" in result.err
    assert kyo("show", record, "--json").json["dispatches"] == []


def test_the_brief_states_the_worktree_as_a_machine_filled_slot(
    kyo, git_project: Path, tmp_path
) -> None:
    """A worker had to *infer* where it was, and two of them inferred the same
    checkout. The brief is frozen, so this is the one moment to say it."""
    tree = tmp_path / "wt-3"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--worktree", str(tree)).code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    relative = os.path.relpath(tree.resolve(), git_project.resolve())
    assert f"`{relative}`" in brief
    assert "do your work here, and nowhere else" in brief
    # KYO-52: the brief is frozen into git-canonical `_briefs.jsonl`, so the
    # absolute form must not be in it at all.
    assert str(tree.resolve()) not in brief
    assert "relative to the project root" in brief


def test_the_worktree_survives_a_rebuild(kyo, git_project: Path, tmp_path) -> None:
    tree = tmp_path / "wt-4"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--worktree", str(tree)).code == 0

    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["dispatches"][0]["worktree"] == os.path.relpath(
        tree.resolve(), git_project.resolve()
    )


# ------------------------------------------------------------------------- KYO-32
#
# The same shape one field over: *which branch* the dispatch's commits must land
# on. A consumer project reported that a worker made its own branch rather than using
# the one the harness prepared, so the merge targeted an empty branch and said 'up to
# date', which would have silently shipped nothing. Nothing in the brief had ever named
# a branch, so the worker was not
# disobeying — it was guessing, exactly as the pre-KYO-22 worker guessed a checkout.


def test_a_dispatch_records_the_branch_its_commits_must_land_on(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    result = _dispatch(kyo, record, "--branch", "kyo-99")
    assert result.code == 0, result
    assert result.json["branch"] == "kyo-99"

    dispatch = kyo("show", record, "--json").json["dispatches"][0]
    assert dispatch["branch"] == "kyo-99"


def test_an_undeclared_branch_is_null_not_a_guess(kyo, git_project: Path) -> None:
    """Unlike --worktree, there is deliberately no default. "The project root" is a
    real answer to *where*; there is no honest default answer to *which branch*, and
    the controller's own current branch would be confidently wrong."""
    record = new_record(kyo, criteria=["a"])
    result = _dispatch(kyo, record)
    assert result.code == 0, result
    assert result.json["branch"] is None
    assert kyo("show", record, "--json").json["dispatches"][0]["branch"] is None


def test_an_undeclared_dispatch_is_otherwise_unchanged(kyo, git_project: Path) -> None:
    """KYO-32.c1: adding --branch must not change what a dispatch without it does."""
    record = new_record(kyo, criteria=["a"])
    result = _dispatch(kyo, record)
    assert result.code == 0, result
    assert result.json["worked_from_sha"]
    assert kyo("show", record, "--json").json["dispatches"][0]["state"] == "running"


def test_the_brief_states_the_branch_as_a_machine_filled_slot(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--branch", "kyo-99").code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    assert "**Branch**: `kyo-99`" in brief
    assert "commit on this branch and no other" in brief


def test_the_brief_says_so_when_no_branch_was_declared(kyo, git_project: Path) -> None:
    """The absence is stated, not implied — a worker reading the brief must be able
    to tell "no branch was named" apart from "the slot is missing"."""
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record).code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    assert "**Branch**: (not declared at dispatch time)" in brief


def test_dispatch_output_states_the_branch_either_way(kyo, git_project: Path) -> None:
    record = new_record(kyo, kind="bug", title="First", criteria=["a"])
    declared = kyo("dispatch", record, "--branch", "kyo-99")
    assert declared.code == 0, declared
    assert "kyo-99" in declared.out

    second = new_record(kyo, kind="bug", title="Second", criteria=["b"])
    undeclared = kyo("dispatch", second, "--force")
    assert undeclared.code == 0, undeclared
    assert "not declared" in undeclared.out


def test_an_empty_branch_name_is_refused_rather_than_stored(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "   ")
    assert result.code != 0, result
    assert "--branch" in result.err
    assert kyo("show", record, "--json").json["dispatches"] == []


def test_the_branch_survives_a_rebuild(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--branch", "kyo-99").code == 0

    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["dispatches"][0]["branch"] == "kyo-99"


def test_the_branch_column_has_a_registered_consumer(kyo, git_project: Path) -> None:
    """KYO-32.c5 / law 2: `kyo doctor`'s column-consumer check is what makes "every
    stored field ships with a consumer" mechanical rather than a promise. This is
    the test that fails if `dispatches.branch` is ever added without its readers."""
    from kyosei.schema import COLUMN_CONSUMERS

    assert COLUMN_CONSUMERS["dispatches"]["branch"], "dispatches.branch declares no consumer"
    # KYO-62: this pin is what made the KYO-57 worker leave `schema.py` alone —
    # correcting the registry required editing a test their dispatch forbade, and
    # a half-corrected dict is worse than an untouched one. The reader did not
    # change; it moved from `commands` to `cmd_dispatch` when the command modules
    # were split, and `doctor.check_column_consumers` now resolves the name rather
    # than trusting it.
    assert "report_ingest.cmd_report" in COLUMN_CONSUMERS["dispatches"]["branch"]

    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--branch", "kyo-99").code == 0
    # `--json` reports every check's verdict whether or not doctor as a whole
    # passes, so this asserts the two c5 names deliberately rather than leaning on
    # an overall exit code that unrelated checks could move.
    checks = kyo("doctor", "--json").json["checks"]
    assert checks["column_consumers"]["ok"], checks["column_consumers"]
    assert checks["config_consumers"]["ok"], checks["config_consumers"]


# ------------------------------------------------------------------------- KYO-52
#
# `kyosei/export/` is git-canonical, so everything in it is permanent and
# publishable-by-accident. An absolute path there is machine-specific (it means
# nothing on another checkout) and personally identifying (it carries a username
# and a directory layout). Measured on this repo's own store at intake: 482
# occurrences across 11 files, most of them the worktree path — recorded on the
# dispatch row, then restated in every frozen brief.


def test_no_absolute_path_reaches_the_worktree_column(kyo, git_project: Path, tmp_path) -> None:
    """KYO-52.c1, stated as the property rather than as one expected string: an
    absolute `--worktree` is accepted, and nothing absolute is stored anywhere the
    export can carry it."""
    tree = tmp_path / "wt-abs"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--worktree", str(tree)).code == 0

    dispatch = kyo("show", record, "--json").json["dispatches"][0]
    assert not os.path.isabs(dispatch["worktree"])

    # The export document is the git-canonical artefact; the absolute form must not
    # appear anywhere in it, including inside the frozen brief it carries.
    exported = (git_project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    assert str(tree.resolve()) not in exported
    assert json.dumps(str(tree.resolve()))[1:-1] not in exported


def test_a_worktree_outside_the_project_root_is_stored_as_a_relative_form(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-52.c2, the decision this record left open, pinned.

    A sibling worktree is the NORMAL case for `kyo dispatch --worktree`, so it is
    neither refused nor replaced by a marker: `os.path.relpath` expresses it with a
    `..` chain, and the `..` chain is exactly the part that carries no ancestor
    directory names — no username, no layout above the project. The fact survives;
    the identity does not."""
    tree = tmp_path / "kyosei-wt-31"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record, "--worktree", str(tree)).code == 0

    stored = kyo("show", record, "--json").json["dispatches"][0]["worktree"]
    assert stored.startswith("../")
    assert stored.endswith("kyosei-wt-31")
    # And it still names a real directory: display resolves what storage relativised.
    assert contracts.resolve_stored_path(git_project, stored) == tree.resolve()


def test_the_dispatch_output_resolves_the_path_for_the_human(
    kyo, git_project: Path, tmp_path
) -> None:
    """Display resolves; storage does not. The controller has to `cd` somewhere, and
    a terminal line is not git-canonical."""
    tree = tmp_path / "wt-echo"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--worktree", str(tree))
    assert result.code == 0, result
    assert str(tree.resolve()) in result.out
    assert os.path.relpath(tree.resolve(), git_project.resolve()) in result.out


def test_a_legacy_absolute_worktree_still_collides_with_the_same_directory(
    kyo, git_project: Path, tmp_path, db
) -> None:
    """The compat half of the collision check (KYO-52 + KYO-22): a dispatch row
    written before schema_version 9 holds an absolute path. Two spellings of one
    directory must still be one directory — a check that answered "not colliding"
    because the strings differed would be the exact failure KYO-22 was filed
    against, reintroduced by the fix for KYO-52."""
    tree = tmp_path / "wt-shared"
    git_worktree(kyo.project, tree)
    first = _with_scope(
        kyo, tmp_path, new_record(kyo, kind="bug", title="First", criteria=["a"]),
        "kyosei/config.yml",
    )
    second = _with_scope(
        kyo, tmp_path, new_record(kyo, kind="bug", title="Second", criteria=["b"]),
        "kyosei/kinds.yml",
    )
    assert _dispatch(kyo, first, "--worktree", str(tree)).code == 0

    conn = db()
    conn.execute("UPDATE dispatches SET worktree = ?", (str(tree.resolve()),))
    conn.close()

    result = kyo("dispatch", second, "--worktree", str(tree))
    assert result.code == 2, result
    assert "dispatch-worktree-collision" in result.err


def test_a_path_with_no_relative_form_is_refused_rather_than_stored_absolute(
    git_project: Path, monkeypatch
) -> None:
    """The one case with no `..` answer: a different drive on Windows, where
    `os.path.relpath` raises. Refusing is deliberate — falling back to the absolute
    form would put back exactly what `store_path` exists to keep out, and silently,
    since an absolute path is inert data that still resolves."""

    def boom(*_args, **_kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(contracts.os.path, "relpath", boom)
    with pytest.raises(Exception) as excinfo:
        contracts.store_path(git_project, git_project)
    assert "relative to the project root" in str(excinfo.value)


def test_an_absolute_touch_path_is_stored_relative_to_the_project_root(
    kyo, git_project: Path
) -> None:
    """`touches.path` is a path-typed column too, and `root / path` accepts an
    absolute path unchanged — so an absolute touch used to be stored verbatim in the
    git-canonical export (KYO-52.c1)."""
    target = git_project / "kyosei" / "kinds.yml"
    record = new_record(kyo, criteria=["a"])
    result = kyo("edit", record, "--touches-file", "-", stdin=f"{target.resolve()}\n")
    assert result.code == 0, result

    touches = kyo("show", record, "--json").json["touches"]
    assert [t["path"] for t in touches] == ["kyosei/kinds.yml"]
    exported = (git_project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    assert str(target.resolve()) not in exported


def test_the_worktree_column_still_has_its_registered_consumers(kyo, git_project: Path) -> None:
    """Law 2, for the reader added by this record: `kyo rebuild` is now a consumer
    of the column, because reading a pre-9 (absolute) value back is its job."""
    from kyosei.schema import COLUMN_CONSUMERS

    assert "rebuild.stored_path_from_export" in COLUMN_CONSUMERS["dispatches"]["worktree"]
    assert "rebuild.stored_path_from_export" in COLUMN_CONSUMERS["touches"]["path"]

    record = new_record(kyo, criteria=["a"])
    assert _dispatch(kyo, record).code == 0
    checks = kyo("doctor", "--json").json["checks"]
    assert checks["column_consumers"]["ok"], checks["column_consumers"]


# ------------------------------------------------------------------------ KYO-223
#
# `kyo worktrees` printed, for every already-pruned worktree, a list of every unaudited
# stop event that could have been that one. In a consumer project, six old worktrees
# produced several hundred event ids and pushed the two live ones off the screen.


def _remove_worktree(repo: Path, tree: Path) -> None:
    """Gone the way git leaves it (KYO-402: every dispatch worktree is now registered),
    so the row is plainly gone rather than a stale admin entry."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(tree)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _worktree_dispatch(kyo, git_project: Path, tmp_path, name: str) -> tuple[str, Path]:
    """A dispatch declaring its own worktree directory, and that directory."""
    tree = tmp_path / name
    git_worktree(kyo.project, tree)
    (git_project / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    record = _with_scope(
        kyo, tmp_path, new_record(kyo, criteria=["a"], title=f"record for {name}"),
        f"{name}.py",
    )
    result = _dispatch(kyo, record, "--worktree", str(tree))
    assert result.code == 0, result
    return result.json["dispatch_id"], tree


def _unresolved_stops(kyo, tmp_path: Path, git_project: Path, how_many: int) -> None:
    """``how_many`` stop audits that could not name their worktree.

    Every dispatch running at the moment of the stop becomes one of its candidates, so
    running this while several dispatches are open is what gives many rows the same
    many event ids — the shape the consumer measured.
    """
    from .test_subagent_stop_worktree import CONTROLLER_EDIT, _stop

    (git_project / CONTROLLER_EDIT).write_text("edited by the controller\n", encoding="utf-8")
    # KYO-418.c6: a current build counts these in place; the rendering tests that use
    # this helper are about the rows a store's history still holds, so they get rows.
    with legacy_unresolved_rows():
        for _ in range(how_many):
            result = _stop(kyo, "check-clean", tmp_path, git_project, None, "--json")
            assert result.code == 0, result
            assert result.json["subject"]["outcome"] == "unresolved", result.json


def test_finished_and_gone_worktrees_are_one_line_and_do_not_bury_the_live_one(
    kyo, git_project: Path, tmp_path
) -> None:
    """c1 and c3. Six settled rows, many unaudited stops, one live worktree: the live
    row is at the top and no settled row spells an event id."""
    settled: list[tuple[str, Path]] = [
        _worktree_dispatch(kyo, git_project, tmp_path, f"wt-old-{n}") for n in range(6)
    ]
    # Many stops while all six are running, so each of the six names all of them.
    _unresolved_stops(kyo, tmp_path, git_project, 4)
    for dispatch_id, tree in settled:
        assert kyo(
            "dispatch", "close", dispatch_id,
            "--terminated-no-report", "--why", "worker crashed, no report recovered",
        ).code == 0
        _remove_worktree(git_project, tree)
    live_id, live_tree = _worktree_dispatch(kyo, git_project, tmp_path, "wt-live")

    listing = kyo("worktrees")
    assert listing.code == 0, listing
    lines = listing.out.splitlines()

    live_at = next(i for i, line in enumerate(lines) if str(live_tree) in line)
    assert live_at <= 4, (
        "the row that still needs a decision is buried under the settled ones:\n"
        + "\n".join(lines[: live_at + 1])
    )
    assert live_id in lines[live_at]

    # Every settled row is present, one line each, with the count and no event ids.
    event_ids = [
        audit["event_id"]
        for audit in kyo("worktrees", "--json").json["stop_audits"]["unresolved"]
    ]
    assert len(event_ids) == 4, event_ids
    for dispatch_id, tree in settled:
        row = [line for line in lines if str(tree) in line]
        assert len(row) == 1, f"{dispatch_id} did not render as exactly one line: {row}"
        assert "unaudited stop(s)" in row[0], row[0]
        assert not any(event_id in row[0] for event_id in event_ids), row[0]
    assert "finished-and-gone worktree(s), one line each" in listing.out


def test_the_stop_ids_stay_reachable_in_json_and_behind_the_flag(
    kyo, git_project: Path, tmp_path
) -> None:
    """c2. KYO-134's per-event detail is moved, never deleted: `--json` carries
    `unaudited_stops` for every row and `--expand-stops` puts the ids back in text."""
    dispatch_id, tree = _worktree_dispatch(kyo, git_project, tmp_path, "wt-settled")
    _unresolved_stops(kyo, tmp_path, git_project, 2)
    assert kyo(
        "dispatch", "close", dispatch_id,
        "--terminated-no-report", "--why", "worker crashed, no report recovered",
    ).code == 0
    _remove_worktree(git_project, tree)

    data = kyo("worktrees", "--json").json
    row = next(r for r in data["worktrees"] if r["dispatch_id"] == dispatch_id)
    assert len(row["unaudited_stops"]) == 2, row
    event_ids = row["unaudited_stops"]

    default = kyo("worktrees").out
    default_row = next(line for line in default.splitlines() if str(tree) in line)
    assert not any(event_id in default_row for event_id in event_ids), (
        "a settled row spelled its event ids without being asked"
    )
    assert "2 unaudited stop(s)" in default_row, default_row
    assert "--expand-stops" in default, "the default says where the ids went"
    # KYO-373.c1 reversed what this used to pin: KYO-134.c5's tail section named every
    # unresolved stop by default, which was bounded by the number of stops and reached
    # 12,791 lines on this project's own store. The default names none of them now;
    # `--expand-stops` puts them back, and `--json` always carries them.
    assert not any(event_id in default for event_id in event_ids)
    expanded = kyo("worktrees", "--expand-stops").out
    assert all(event_id in expanded for event_id in event_ids)

    expanded_row = next(
        line for line in kyo("worktrees", "--expand-stops").out.splitlines()
        if str(tree) in line
    )
    for event_id in event_ids:
        assert event_id in expanded_row
    assert "went UNAUDITED and could have been this one" in expanded_row


def test_a_still_present_finished_worktree_keeps_todays_detail(
    kyo, git_project: Path, tmp_path
) -> None:
    """c1's other half. Settled means finished AND gone, never either alone: a finished
    dispatch whose directory is still there is exactly what `--prune` is about to act
    on, and that row is the one a controller is deciding about."""
    dispatch_id, tree = _worktree_dispatch(kyo, git_project, tmp_path, "wt-present")
    _unresolved_stops(kyo, tmp_path, git_project, 2)
    assert kyo(
        "dispatch", "close", dispatch_id,
        "--terminated-no-report", "--why", "worker crashed, no report recovered",
    ).code == 0
    assert tree.exists()

    listing = kyo("worktrees").out
    event_ids = [
        audit["event_id"]
        for audit in kyo("worktrees", "--json").json["stop_audits"]["unresolved"]
    ]
    row = next(line for line in listing.splitlines() if str(tree) in line)
    assert all(event_id in row for event_id in event_ids), row


# ------------------------------------------------------------------------ KYO-373
#
# "A bare `kyo worktrees` printed 12,791 lines (4.2MB)" — 6,284 of 6,514 recorded stops
# unmatched, 6,277 of those "no readable transcript", two lines each by default. And in
# one session: 286 such stops against 2 matched worker stops, each one
# running `check-clean`/`check-branch` against the controller's own cwd.

#: A stopping agent the harness wrote nothing for: the ~32s tick's shape (KYO-163).
TICK_AGENT = "a0f00000000000373"


def _session_with_a_worker(tmp_path: Path) -> Path:
    """A session transcript whose ``subagents/`` holds one real agent's transcript.

    That sibling is the witness `_untranscribed_agent` requires: the harness is
    demonstrably writing transcripts where the resolution looks, in this session.
    """
    from .test_subagent_stop_worktree import _transcript

    return _transcript(tmp_path, "a1b2c3d4e5f6a7b8c", "You are a research subagent.")


def _tick_stop(kyo, command: str, transcript: Path, cwd: Path, *args: str):
    from .test_subagent_stop_worktree import _payload

    return kyo(command, "--subagent-stop", *args, stdin=_payload(transcript, cwd, TICK_AGENT))


def _git_calls(monkeypatch) -> list[tuple[list[str], str | None]]:
    """Every git command run from here on, with the directory it ran in.

    Installed AFTER a test's setup, so it sees only the guard under test. The CLI runs
    in-process (``conftest.invoke``), so this is the guard's own `subprocess.run`.
    """
    import subprocess

    calls: list[tuple[list[str], str | None]] = []
    real_run = subprocess.run

    def recording_run(argv, *args, **kwargs):
        if argv and argv[0] == "git":
            cwd = kwargs.get("cwd")
            calls.append((list(argv), None if cwd is None else str(cwd)))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    return calls


def test_a_stop_by_an_agent_with_no_transcript_checks_no_directory(
    kyo, git_project: Path, tmp_path, monkeypatch
) -> None:
    """KYO-373.c2. Classified not-a-worker, exit 0, and NOT ONE git command anywhere.

    Both trees are dirty on purpose: the controller's (the fallback the guard used to
    read) and the declared worker's (the tree a wrongly-matched stop would read). A
    guard that checked either would have something to say; this one must say nothing
    about any tree, because there is no tree this agent worked in.
    """
    from kyosei.stop_target import STOP_REASON_NO_TRANSCRIPT, STOP_TARGET_NO_TRANSCRIPT

    from .test_subagent_stop_worktree import CONTROLLER_EDIT, _running_dispatch

    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-373-tick")
    (tree / "uncommitted.txt").write_text("the worker's\n", encoding="utf-8")
    (git_project / CONTROLLER_EDIT).write_text("the controller's\n", encoding="utf-8")
    transcript = _session_with_a_worker(tmp_path)
    calls = _git_calls(monkeypatch)

    for command in ("check-clean", "check-branch"):
        result = _tick_stop(kyo, command, transcript, git_project, "--json")
        assert result.code == 0, result
        assert calls == [], f"{command} ran git for an agent with no tree: {calls}"
        subject = result.json["subject"]
        assert subject["outcome"] == STOP_TARGET_NO_TRANSCRIPT, subject
        assert subject["reason"] == STOP_REASON_NO_TRANSCRIPT, subject
        assert dispatch_id in subject["candidates"], subject
        assert result.json["checked_a_directory"] is False, result.json
        text = _tick_stop(kyo, command, transcript, git_project)
        assert text.code == 0, text
        assert "Fell back to reading" not in text.out + text.err, text
        assert calls == [], f"{command} ran git for an agent with no tree: {calls}"

    # KYO-375.c1: counted against the dispatch's own row, not recorded as a row of
    # its own — `check-branch` never wrote (only `check-clean` does, KYO-134), so
    # only its two calls above are in the count.
    ledger = kyo("worktrees", "--json").json["stop_audits"]
    assert ledger["unresolved"] == [], ledger["unresolved"]
    assert ledger["no_transcript"] == [], ledger["no_transcript"]  # no row was written
    assert ledger["tick_counters"][dispatch_id]["count"] == 2, ledger["tick_counters"]
    assert ledger["tick_counters"][dispatch_id]["first_seen"] is not None, ledger["tick_counters"]
    assert ledger["tick_counters"][dispatch_id]["last_seen"] is not None, ledger["tick_counters"]
    assert ledger["census"]["no_transcript"] == 2, ledger["census"]
    assert ledger["census"]["unmatched_worker"] == 0, ledger["census"]
    rows = kyo("worktrees", "--json").json["worktrees"]
    row = next(r for r in rows if r["dispatch_id"] == dispatch_id)
    assert row["unaudited_stops"] == [], "a tick was counted as this worker's unaudited stop"

    # And the counter is durable: it survived the read above because it lives on the
    # dispatch's own row, which `kyo show <dispatch-id>` also reads straight off
    # `export._dispatch` — never a new file, never the events ledger.
    tick_audit = kyo("show", dispatch_id, "--json").json["tick_audit"]
    assert tick_audit["count"] == 2, tick_audit


def test_a_second_tick_increments_the_counter_in_place(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-375.c1: the mechanism a row-writing regression would break silently.

    Two ticks naming the same dispatch must read back as one counter with count 2,
    not two independent counters and not a row appended per tick — the whole point
    of "in place". If `record_tick_audit` were reverted to writing an events row per
    tick (the bug this record fixes), this test's `count == 2` would instead be a
    `no_transcript` list of length 2 and this assertion would fail.
    """
    from .test_subagent_stop_worktree import _running_dispatch

    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-375-counter")
    transcript = _session_with_a_worker(tmp_path)

    first = _tick_stop(kyo, "check-clean", transcript, git_project, "--json")
    assert first.code == 0, first
    second = _tick_stop(kyo, "check-clean", transcript, git_project, "--json")
    assert second.code == 0, second

    ledger = kyo("worktrees", "--json").json["stop_audits"]
    assert ledger["tick_counters"][dispatch_id]["count"] == 2, ledger["tick_counters"]
    assert (
        ledger["tick_counters"][dispatch_id]["first_seen"]
        <= ledger["tick_counters"][dispatch_id]["last_seen"]
    ), ledger["tick_counters"]
    # No row was written for either tick, and no events-ledger diff exists to show it.
    assert ledger["no_transcript"] == [], ledger["no_transcript"]
    assert ledger["census"]["recorded"] == 2, ledger["census"]


def test_an_absent_transcript_is_not_classified_where_the_harness_wrote_none_at_all(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-373's narrowing, and KYO-163's objection it answers.

    A session with no agent transcript under ``subagents/`` at all is one where a
    missing file cannot be told from a harness that moved its files — and there,
    calling every stop "not a worker" would silence every real worker at once. The
    stop stays ``unresolved`` and is audited exactly as before, with the reason code
    saying which kind of unresolved it is.
    """
    from kyosei.stop_target import STOP_REASON_NO_TRANSCRIPT, STOP_TARGET_UNRESOLVED

    from .test_subagent_stop_worktree import _running_dispatch

    _running_dispatch(kyo, tmp_path, "wt-373-unwitnessed")
    transcript = tmp_path / "bare-session.jsonl"
    transcript.write_text("", encoding="utf-8")

    result = _tick_stop(kyo, "check-clean", transcript, git_project, "--json")
    assert result.code == 0, result
    subject = result.json["subject"]
    assert subject["outcome"] == STOP_TARGET_UNRESOLVED, subject
    assert subject["reason"] == STOP_REASON_NO_TRANSCRIPT, subject
    assert "layout change" in subject["why"], subject["why"]
    assert result.json["checked_the_worker_tree"] is False
    assert kyo("worktrees", "--json").json["stop_audits"]["census"]["unmatched_worker"] == 1


def test_a_legacy_no_transcript_row_reads_as_not_a_worker() -> None:
    """KYO-373: the stored ledger is not rewritten; it is READ differently.

    A row written before KYO-373 carries no reason code, and the sentence
    `_dispatch_prompt` wrote for a missing transcript is the whole of what says which
    case it was. A row with a transcript whose prompt named nothing stays unmatched.
    """
    from kyosei.stop_audit import StopAudit, StopAuditLedger
    from kyosei.stop_target import STOP_REASON_PROMPT_NAMES_NONE, STOP_TARGET_UNRESOLVED

    def row(event: str, why: str) -> StopAudit:
        return StopAudit(
            event_id=event, at="2026-09-23T12:00:00.000Z", outcome=STOP_TARGET_UNRESOLVED,
            directory="/the/controllers/cwd", why=why, dispatch_id=None,
            candidates=["KYO-1.d1"], agent_id=event,
        )

    ledger = StopAuditLedger(audits=[
        row("KYO-E1", "no readable transcript for agent a1 under /s/subagents or /s.jsonl"),
        row("KYO-E2", "the stopping agent's prompt names none of the 1 dispatch(es) with "
                      "a declared worktree (first user message of /s/subagents/agent-a2.jsonl)"),
    ])

    assert [a.event_id for a in ledger.untranscribed] == ["KYO-E1"]
    assert [a.event_id for a in ledger.unresolved] == ["KYO-E2"]
    assert ledger.unresolved[0].reason == STOP_REASON_PROMPT_NAMES_NONE
    assert ledger.by_dispatch()["KYO-1.d1"]["not_a_worker"] == 1
    assert ledger.by_dispatch()["KYO-1.d1"]["unmatched"] == 1
    assert "1 of 2 recorded stop(s) could NOT be matched" in ledger.sentence()


def _mixed_ledger(kyo, git_project: Path, tmp_path) -> tuple[str, list[str]]:
    """One running dispatch, 1 matched stop, 2 unmatched worker stops, 3 ticks."""
    from .test_subagent_stop_worktree import CONTROLLER_EDIT, _running_dispatch, _stop

    # Written BEFORE the dispatch, so it is in the dispatch's KYO-535 baseline: what this
    # ledger is about is the stops, and a file that appeared in the main checkout after
    # the dispatch began is a KYO-535 finding that blocks the first stop by design.
    (git_project / CONTROLLER_EDIT).write_text("the controller's\n", encoding="utf-8")
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-373-mixed")
    assert _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id).code == 0
    unmatched = []
    with legacy_unresolved_rows():  # rows a pre-KYO-418 store holds (see conftest)
        for _ in range(2):
            result = _stop(kyo, "check-clean", tmp_path, git_project, None, "--json")
            assert result.json["subject"]["outcome"] == "unresolved", result.json
            unmatched.append(result.json["stop_audit_event"])
    transcript = _session_with_a_worker(tmp_path)
    for _ in range(3):
        assert _tick_stop(kyo, "check-clean", transcript, git_project).code == 0
    return dispatch_id, unmatched


def test_worktrees_prints_counts_per_dispatch_and_the_per_stop_lines_only_when_asked(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-373.c1, as KYO-423.c5 left it. The default is the ledger's sentence on one
    line naming `--expand-stops`; the per-dispatch lines AND the per-stop `! KYO-E…`
    lines are under `--expand-stops`; every stop is in `--json`."""
    dispatch_id, unmatched = _mixed_ledger(kyo, git_project, tmp_path)

    default = kyo("worktrees")
    assert default.code == 0, default
    # The stop-audit section: from its sentence on. The live row above it keeps
    # KYO-223's own per-row column, which is about that directory and not this section.
    section = default.out[default.out.index("stop audits:") :]
    assert "could NOT be matched" in section
    per_dispatch = [line for line in section.splitlines() if line.strip().startswith(dispatch_id)]
    assert per_dispatch == [], "a per-dispatch census line came back to the default print"
    assert "  ! KYO-E" not in section, "a per-stop line came back to the default print"
    assert "it could have been any of" not in section
    assert not any(event in section for event in unmatched), section
    assert "--expand-stops" in section, "the default does not say where the lines went"

    expanded = kyo("worktrees", "--expand-stops").out
    expanded_section = expanded[expanded.index("stop audits:") :]
    per_dispatch = [
        line for line in expanded_section.splitlines() if line.strip().startswith(dispatch_id)
    ]
    assert len(per_dispatch) == 1, expanded_section
    by_reason = (
        "2 unmatched (no transcript 0, prompt names none 2, prompt names several 0, other 0)"
    )
    assert by_reason in per_dispatch[0], per_dispatch[0]
    assert "3 not a worker" in per_dispatch[0], per_dispatch[0]
    for event in unmatched:
        assert f"  ! {event} at " in expanded, expanded
    assert expanded.count("it could have been any of") == 2, expanded

    ledger = kyo("worktrees", "--json").json["stop_audits"]
    assert [row["event_id"] for row in ledger["unresolved"]] == unmatched
    # KYO-375: the 3 ticks wrote no row — `no_transcript` (the literal-row list) is
    # empty, and what they left behind is the dispatch's own counter instead. The
    # census still folds the counter in, so the totals below are unchanged from
    # what this store's own history already reads (KYO-375's task).
    assert ledger["no_transcript"] == [], ledger
    assert ledger["tick_counters"][dispatch_id]["count"] == 3, ledger["tick_counters"]
    assert ledger["census"] == {
        "recorded": 6, "matched": 1, "unmatched_worker": 2, "not_a_worker": 3,
        "helpers": 0, "no_transcript": 3, "other": 0,
    }, ledger["census"]


def test_worktrees_folds_zero_unmatched_dispatches_into_one_line_by_default(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-375.c3, as KYO-423.c5 left it. "`kyo worktrees` prints one line per
    dispatch a tick ever named" was the third of KYO-375's three findings — 133 lines on
    this store, 121 of them "0 unmatched ...; N not a worker", nothing to act on.
    KYO-375 folded those into one summary line; KYO-423 moved the whole census behind
    `--expand-stops`, so by default no dispatch gets a census line at all, and
    `--expand-stops` lists every one — the one with an unmatched worker stop first.
    """
    from .test_subagent_stop_worktree import _running_dispatch

    with_unmatched_id, _unmatched = _mixed_ledger(kyo, git_project, tmp_path)
    transcript = _session_with_a_worker(tmp_path)
    tick_only_ids = [
        _running_dispatch(kyo, tmp_path, name)[0]
        for name in ("wt-375-tickonly-a", "wt-375-tickonly-b")
    ]
    # KYO-201: a tick names every dispatch with a worktree still on disk as a
    # candidate, whatever its state — so one more tick, now that three dispatches
    # are running, puts all three (the mixed one and the two tick-only ones) on
    # the ledger/counter at once.
    assert _tick_stop(kyo, "check-clean", transcript, git_project).code == 0

    default = kyo("worktrees")
    assert default.code == 0, default
    section = default.out[default.out.index("stop audits:") :]
    for dispatch_id in [*tick_only_ids, with_unmatched_id]:
        assert dispatch_id not in section, (
            f"{dispatch_id} got a census line in the default output: {section}"
        )

    expanded = kyo("worktrees", "--expand-stops").out
    expanded_section = expanded[expanded.index("stop audits:") :]
    for dispatch_id in tick_only_ids:
        assert dispatch_id in expanded_section, expanded_section
    assert "dispatch(es) named only by ticks" not in expanded_section, expanded_section
    assert expanded_section.index(with_unmatched_id) < min(
        expanded_section.index(d) for d in tick_only_ids
    ), "the dispatch with an unmatched worker stop is no longer listed first"


def test_doctor_prints_the_stop_ledger_census_beside_unresolved_stops(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-373.c3. One line, three counts, on the check's own line — and not a finding.

    The census rides with ``unresolved_stops`` whatever that check found; it never
    changes the check's ``ok`` or the exit code. Only the two unmatched worker stops
    are the check's finding; the three ticks are in the census and nowhere else.
    """
    dispatch_id, _unmatched = _mixed_ledger(kyo, git_project, tmp_path)

    doctored = kyo("doctor")
    assert doctored.code == 0, doctored
    line = next(
        line for line in doctored.out.splitlines() if line.strip().endswith("(KYO-373)")
    )
    assert "unresolved_stops — stop ledger: 6 recorded" in line, line
    assert "1 matched to their worker's worktree" in line, line
    assert "2 unmatched worker stop(s)" in line, line
    assert "3 not a worker (0 helper(s) of a worker, 3 agent(s) with no transcript)" in line

    check = kyo("doctor", "--json").json["checks"]["unresolved_stops"]
    assert check["census"] == line.split(" — ", 1)[1], check
    assert "2 stop(s)" in check["failures"][0], check["failures"]
    assert dispatch_id in check["failures"][0]
