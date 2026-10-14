"""KYO-71: a declared touch list, checked against the files that were actually edited.

Found by the KYO-65 worker, which hit the bug on itself while fixing its
sibling. Touch-disjointness has now failed three distinct ways:

1. **Undeclared** (KYO-48, fixed) — an empty touch list intersected nothing, so "I
   have not said what I touch" produced the same favourable answer as "I have said,
   and we are independent".
2. **Declared but unconsulted** (KYO-65, fixed) — ``--also-covers`` records' touches
   were never read, so a dispatch could work in files nothing knew it claimed.
3. **Declared but incomplete** — this record. The list is read, and it is wrong.

The live instance was this record's own dispatch: KYO-65's declared touches omitted
``src/kyosei/contracts.py``, its fix needed that file, and KYO-68.d1 was running with
``contracts.py`` in its claim. Two running dispatches, one file, no refusal — because
the check compared two DECLARED sets and they did not overlap. The claim was false at
the moment it was compared.

The first two fixes made the check read MORE of what was declared. No amount of
reading declarations establishes the relationship between what was declared and what
happened; that needs the diff.

**And what it does about a difference is report, never refuse.** Refusing would have
blocked five of that session's dispatches, every one of which was right to edit what
it edited. The touches table is a *prediction*, written by a controller who has not
done the work yet; it is wrong often, in small ways. The honest goal is that the
RECORD ends up true, not that the prediction was.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kyosei import touch_drift
from kyosei.contracts import WORKER_REPORT_PATH

from .conftest import new_record


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


def _declare(kyo, project: Path, record: str, *paths: str) -> None:
    for path in paths:
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("x = 1\n", encoding="utf-8")
    scope = project / f"{record}-touches.txt"
    scope.write_text("".join(p + "\n" for p in paths), encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0


def _branch_editing(project: Path, branch: str, *paths: str) -> str:
    """Make ``branch`` off the current HEAD, edit ``paths`` on it, and come back."""
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", branch)
    for path in paths:
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("edited = True\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", f"work on {branch}")
    sha = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", base)
    return sha


def _main_branch(project: Path) -> str:
    return _git(project, "rev-parse", "--abbrev-ref", "HEAD")


# --------------------------------------------------------------------- KYO-71.c1


def test_a_command_names_the_files_edited_outside_the_claim(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    (git_project / "src" / "undeclared.py").write_text("x = 1\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/declared.py", "src/undeclared.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    assert result.code == 0, result
    drift = result.json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_FOUND
    assert drift["undeclared_files"] == ["src/undeclared.py"]
    assert "src/declared.py" in drift["claimed_files"]


def test_it_uses_the_same_diff_check_branch_computes(kyo, git_project: Path) -> None:
    """KYO-71.c1 says so explicitly, and it is why this costs one set difference:
    `kyo check-branch` already runs `git diff --name-only <against>...<branch>`. Two
    copies of that diff could disagree about the merge base; one function cannot."""
    from kyosei.git_queries import branch_changed_files

    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/declared.py", "src/extra.py")

    guard = kyo("check-branch", "kyo-71", "--against", against, "--json")
    assert guard.code == 0, guard
    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]

    assert sorted(guard.json["changed_files"]) == sorted(drift["changed_files"])
    assert sorted(branch_changed_files(git_project, against, "kyo-71")) == sorted(
        guard.json["changed_files"]
    )


def test_a_covered_records_touches_count_as_part_of_the_claim(kyo, git_project: Path) -> None:
    """KYO-65's fix, inherited: both sides of the comparison are *claims*, not single
    records. A file declared only by an ``--also-covers`` record is claimed, and must
    not show up as drift."""
    primary = new_record(kyo, criteria=["a"])
    covered = new_record(kyo, criteria=["b"])
    _declare(kyo, git_project, primary, "src/primary.py")
    _declare(kyo, git_project, covered, "src/covered.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/primary.py", "src/covered.py")

    dispatch_id = kyo(
        "dispatch", primary, "--no-create-worktree", "--branch", "kyo-71", "--also-covers",
        covered, "--json",
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_NONE
    assert drift["undeclared_files"] == []
    assert drift["covers"] == [covered]


def test_an_undeclared_branch_is_not_checked_and_says_so(kyo, git_project: Path) -> None:
    """Law 6, the shape KYO-48 and KYO-32 both took: "the comparison did not run"
    must never render as "nothing drifted"."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    dispatch_id = kyo("dispatch", record, "--json").json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--json")
    assert result.code == 0, result
    assert result.json["touch_drift"]["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    assert "NOT CHECKED" in kyo("touch-drift", dispatch_id).out


def test_an_unresolvable_ref_is_not_checked_rather_than_clean(kyo, git_project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "no-such-branch", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--json")
    assert result.code == 0, result
    assert result.json["touch_drift"]["outcome"] == touch_drift.DRIFT_NOT_CHECKED


def test_store_paths_are_never_reported_as_drift(kyo, git_project: Path) -> None:
    """`kyo check-branch` already refuses a branch that diffs `kyosei/` or `ideas/`.
    Reporting the same file here would state one fact twice with the weaker of the
    two remedies attached."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/declared.py", "ideas/stray.md")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["undeclared_files"] == []
    assert not any(p.startswith("ideas/") for p in drift["changed_files"])


# --------------------------------------------------------------------- KYO-71.c2


def test_a_branch_that_edited_outside_its_claim_still_passes_the_merge_guard(
    kyo, git_project: Path
) -> None:
    """KYO-71.c2, verbatim. Five of one session's dispatches edited files their
    records did not name, and every one of them was right to: KYO-52's prose warning
    could only go at a shared funnel; KYO-54's criteria were about code another
    record had moved; KYO-57 corrected docstrings in eight files its own move had
    falsified. A guard that refused would have blocked all five."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/never-declared.py")

    guard = kyo("check-branch", "kyo-71", "--against", against)
    assert guard.code == 0, guard

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against)
    assert drift.code == 0, drift
    assert "src/never-declared.py" in drift.out


def test_report_ingest_reports_drift_and_still_ingests(kyo, git_project: Path, tmp_path) -> None:
    """The ingest must not be failable by a drift finding — which is why the
    comparison runs outside `kyo report`'s transaction, not merely why it is
    documented not to refuse."""
    import json

    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    sha = _branch_editing(git_project, "kyo-71", "src/declared.py", "src/surprise.py")

    dispatched = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "dispatch_id": dispatched["dispatch_id"],
                "record_id": record,
                "worked_from_sha": dispatched["worked_from_sha"],
                "criteria_verdicts": [
                    {"criterion_id": criterion, "verdict": "met", "method": "machine-checked"}
                ],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [{"sha": sha, "subject": "work"}],
            }
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatched["dispatch_id"], "--file", str(report), "--json")
    assert ingested.code == 0, ingested
    drift = ingested.json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_FOUND
    assert "src/surprise.py" in drift["undeclared_files"]


def test_there_is_no_strict_flag_to_turn_the_report_into_a_refusal() -> None:
    """A flag that made this refuse is a flag somebody eventually turns on, and the
    record's whole finding is that the branch which edits outside its claim is
    usually the branch that was right."""
    from kyosei.cli import build_parser

    actions = {
        action.dest
        for action in build_parser()._subparsers._group_actions[0]  # type: ignore[union-attr]
        .choices["touch-drift"]
        ._actions
    }
    assert "strict" not in actions
    assert "force" not in actions


# --------------------------------------------------------------------- KYO-71.c3


def test_where_it_fires_is_recorded_in_the_code_that_fires_it() -> None:
    """KYO-71.c3 asked for the firing point to be decided and the trade named. It is
    report-ingest time (plus `kyo touch-drift` on demand): complete branch, store in
    hand, and the moment a controller would otherwise correct touches by hand. The
    rejected alternative is worker stop time — earlier, but the worktree may hold no
    store to read and a worker's scope is incomplete mid-work."""
    doc = touch_drift._drift_for_dispatch.__doc__ or ""
    assert "Report time (chosen)" in doc
    assert "Worker stop time (not chosen)" in doc
    assert "concurrent collision" in doc


def test_the_worker_stop_guard_is_deliberately_unchanged(kyo, git_project: Path) -> None:
    """`kyo check-clean` runs in the worker's worktree, where under the in-repo
    layout there is no `kyosei.db` at all — the comparison is not merely early
    there, it is not computable. It stays a pure git check."""
    result = kyo("check-clean", "--json")
    assert result.code == 0, result
    assert "touch_drift" not in result.json


# --------------------------------------------------------------------- KYO-71.c4


def test_the_output_is_actionable_as_a_touches_file_correction(
    kyo, git_project: Path
) -> None:
    """KYO-71.c4: this controller corrected touches by hand after nearly every
    dispatch, and every one of those was a diff the tool could have shown. So the
    output names the record and the exact lines — the WHOLE corrected list, because
    `--touches-file` replaces rather than appends, so a diff would leave the
    controller merging by hand at the one moment this exists to save."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/declared.py", "src/found-later.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against)
    assert result.code == 0, result
    assert f"kyo edit {record} --touches-file" in result.out

    lines = kyo("touch-drift", dispatch_id, "--against", against, "--json").json[
        "touch_drift"
    ]["touches_file_lines"]
    # KYO-543.c2: the file the worker CREATED exists only on its unmerged branch, so
    # the correction claims it as `+path` — the form `kyo edit --touches-file` takes
    # for a file that does not exist yet — rather than a plain path it would refuse.
    assert lines == ["src/declared.py", "+src/found-later.py"]
    assert "do not exist in this checkout yet" not in result.out
    assert "+src/found-later.py" in result.out

    # The correction the tool suggests is one the tool takes, before the merge.
    corrected = git_project / "corrected-touches.txt"
    corrected.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(corrected)).code == 0
    _git(git_project, "merge", "-q", "--no-edit", "kyo-71")

    # The correction landed: the record now claims what the branch actually edited,
    # the created file under the marker it was stored with (KYO-225.c3: a `+` stored
    # while the file was absent goes on reading correctly after the work lands).
    assert sorted(t["path"] for t in kyo("show", record, "--json").json["touches"]) == [
        "+src/found-later.py",
        "src/declared.py",
    ]

    # And KYO-85: asked again after that merge, this dispatch has no report and its
    # branch is now contained in `against`, so the three-dot diff examines nothing.
    # It used to answer "none" here — a clean bill, computed from an empty diff.
    after = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert after["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    assert "already merged" in after["detail"]


def test_the_correction_names_the_primary_record_not_a_covered_one(
    kyo, git_project: Path
) -> None:
    """Dividing new files among covered records would be the tool guessing at
    authorship. The primary record is the one whose dispatch this is and the one a
    controller opens."""
    primary = new_record(kyo, criteria=["a"])
    covered = new_record(kyo, criteria=["b"])
    _declare(kyo, git_project, primary, "src/primary.py")
    _declare(kyo, git_project, covered, "src/covered.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/surprise.py")

    dispatch_id = kyo(
        "dispatch", primary, "--no-create-worktree", "--branch", "kyo-71", "--also-covers",
        covered, "--json",
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    drift = result.json["touch_drift"]
    assert drift["record_id"] == primary
    # The primary's own claim plus the drift — never the covered record's paths,
    # which belong to a different record's touches table.
    assert drift["touches_file_lines"] == ["src/primary.py", "+src/surprise.py"]
    assert f"kyo edit {primary} --touches-file" in kyo(
        "touch-drift", dispatch_id, "--against", against
    ).out


def test_claimed_but_untouched_paths_are_reported_not_removed(
    kyo, git_project: Path
) -> None:
    """A prediction that was cautious is not a prediction that was wrong, and an
    unfinished record has claims it has not reached yet. So they are named and
    kept in the suggested lines rather than silently dropped."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/touched.py", "src/planned.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-71", "src/touched.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-71", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["unused_claims"] == ["src/planned.py"]
    assert "src/planned.py" in drift["touches_file_lines"]
    assert drift["outcome"] == touch_drift.DRIFT_NONE


# ===================================================================== KYO-85
#
# The same check, one merge later. `git diff <against>...<branch>` is three-dot, so
# the moment the branch merges its merge base becomes its own tip and the diff is
# empty by construction. That empty list rendered as "none — all 0 file(s) ... are
# claimed", which is the sentence that means "checked, and you are fine".
#
# Reported by a consumer project's controller, unprompted, as the one thing it would
# fix first: a tool that reports no drift when there is drift is worse than having no
# tool. It ran the command across four dispatches and it never once helped,
# because `kyo report` tells a controller to apply the correction AFTER the merge —
# so the documented workflow routes you to this command at precisely the moment it
# had stopped working.
#
# Reproduced in this repo on KYO-73.d1, whose own ingested report records three
# drifted files: `kyo touch-drift KYO-73.d1` answered "none — all 0 file(s)".


def _report_file(tmp_path, dispatched, record, criterion, sha, **extra):
    import json

    path = tmp_path / f"report-{record}.json"
    path.write_text(
        json.dumps(
            {
                "dispatch_id": dispatched["dispatch_id"],
                "record_id": record,
                "worked_from_sha": dispatched["worked_from_sha"],
                "criteria_verdicts": [
                    {"criterion_id": criterion, "verdict": "met", "method": "machine-checked"}
                ],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [{"sha": sha, "subject": "work"}],
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return path


def _dispatch_and_report(kyo, git_project: Path, tmp_path, record, branch, *touched):
    """Dispatch ``record`` on ``branch``, edit ``touched`` there, and ingest a report
    that names them in ``touched_files[]`` — the shape every report this project has
    ever ingested actually has."""
    # The branch is built FIRST, before any `kyo` write leaves uncommitted export
    # changes in the main checkout — otherwise `git add -A` here would commit half a
    # store onto the branch and the later merge would conflict on it.
    sha = _branch_editing(git_project, branch, *touched)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", branch, "--json").json
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    report = _report_file(tmp_path, dispatched, record, criterion, sha, touched_files=list(touched))
    ingested = kyo("report", dispatched["dispatch_id"], "--file", str(report), "--json")
    assert ingested.code == 0, ingested
    return dispatched["dispatch_id"], ingested.json["touch_drift"]


# --------------------------------------------------------------------- KYO-85.c1


def test_the_answer_survives_the_merge_and_the_deletion_of_the_branch(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-85.c1 and c4 together, in the order the workflow actually happens.

    The branch is merged (which empties the three-dot diff) and then deleted (which
    was the *honest* failure — "not a known git ref"), and the answer is the same
    finding, file for file, that the ingest printed. It is the same finding because
    it is the same computation over the same evidence: the report's touched_files[],
    which needs no ref at all.
    """
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)

    dispatch_id, at_ingest = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-85", "src/declared.py", "src/surprise.py"
    )
    assert at_ingest["outcome"] == touch_drift.DRIFT_FOUND
    assert at_ingest["undeclared_files"] == ["src/surprise.py"]

    _git(git_project, "merge", "-q", "--no-edit", "kyo-85")
    _git(git_project, "branch", "-q", "-D", "kyo-85")

    after = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert after["outcome"] == at_ingest["outcome"]
    assert after["undeclared_files"] == at_ingest["undeclared_files"]
    # KYO-543.c2: at ingest the branch was unmerged and the created file was claimed
    # as `+src/surprise.py`; after the merge it exists, so it is the plain path.
    assert at_ingest["touches_file_lines"] == ["src/declared.py", "+src/surprise.py"]
    assert after["touches_file_lines"] == ["src/declared.py", "src/surprise.py"]
    assert after["source"] == touch_drift.DRIFT_SOURCE_REPORT
    assert "src/surprise.py" in kyo("touch-drift", dispatch_id, "--against", against).out


def test_the_reproduction_from_the_record_no_longer_says_none(
    kyo, git_project: Path, tmp_path
) -> None:
    """The evidence artifact on KYO-85, as a test: a dispatch whose report records
    drift, whose branch is merged, asked again.

        $ kyo touch-drift KYO-73.d1
        touch drift: none — all 0 file(s) kyo-73-db-export-seam changed are claimed by ...

    Three drifted files, and the command reported none. Not "could not tell" — none.
    """
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)

    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-85", "src/one.py", "src/two.py", "src/three.py"
    )
    _git(git_project, "merge", "-q", "--no-edit", "kyo-85")

    result = kyo("touch-drift", dispatch_id, "--against", against)
    assert result.code == 0, result
    assert "none" not in result.out.splitlines()[0]
    assert "all 0 file(s)" not in result.out
    for path in ("src/one.py", "src/two.py", "src/three.py"):
        assert path in result.out


def test_the_report_is_preferred_at_ingest_time_too(kyo, git_project: Path, tmp_path) -> None:
    """Why c1 holds structurally rather than by luck. If the ingest answered from git
    and later runs answered from the report, "the same drifted files" would be a claim
    about the worker's list being right — which is exactly the kind of claim KYO-71
    exists because nobody can make. One source, asked twice."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")

    _, at_ingest = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-85", "src/declared.py", "src/surprise.py"
    )
    assert at_ingest["source"] == touch_drift.DRIFT_SOURCE_REPORT


def test_a_report_that_names_fewer_files_than_the_branch_changed_is_said_so(
    kyo, git_project: Path, tmp_path
) -> None:
    """The cost of preferring a declaration, paid where it can still be checked.

    touched_files[] is the worker's statement, and KYO-71's whole finding is that a
    declaration and what happened are different things. So while the branch is still
    diffable, the two are compared and the difference is named — the drift finding is
    still computed from the report, because that is the list that survives the merge.
    """
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    # `kyo report` takes no --against, so this asks the question at the default. The
    # branch is renamed rather than assumed to be called that: which name `git init`
    # picks is a property of whoever's machine is running the suite, and a test that
    # reads one is a test that means something different somewhere else.
    _git(git_project, "branch", "-M", "main")

    sha = _branch_editing(git_project, "kyo-85", "src/declared.py", "src/forgotten.py")
    dispatched = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-85", "--json"
    ).json
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    report = _report_file(
        tmp_path, dispatched, record, criterion, sha, touched_files=["src/declared.py"]
    )
    ingested = kyo("report", dispatched["dispatch_id"], "--file", str(report), "--json")
    assert ingested.code == 0, ingested

    drift = ingested.json["touch_drift"]
    assert drift["source"] == touch_drift.DRIFT_SOURCE_REPORT
    assert drift["unreported_by_worker"] == ["src/forgotten.py"]
    assert "src/forgotten.py" in ingested.out


# --------------------------------------------------------------------- KYO-85.c2


def test_a_merged_branch_with_no_report_is_not_checked_rather_than_clean(
    kyo, git_project: Path
) -> None:
    """The defect itself, at its narrowest: nothing to read but a diff, and the diff
    cannot be read. The command must say that — the empty diff of a merged branch is
    the absence of a finding, not a finding of absence."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-85", "src/declared.py", "src/surprise.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-85", "--json"
    ).json["dispatch_id"]
    _git(git_project, "merge", "-q", "--no-edit", "kyo-85")

    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    assert result.code == 0, result
    assert result.json["touch_drift"]["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    assert "NOT CHECKED" in kyo("touch-drift", dispatch_id, "--against", against).out


def test_the_remedy_the_merged_case_names_actually_clears_it(
    kyo, git_project: Path, tmp_path
) -> None:
    """The `not-checked` detail tells a controller to file the dispatch's report and
    ask again. Every message this project prints that names a remedy owes a test that
    the remedy works (AGENTS.md), and this one is load-bearing: it is the whole reason
    "could not tell" is an acceptable answer here rather than a dead end."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)

    dispatched = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-85", "--json"
    ).json
    sha = _branch_editing(git_project, "kyo-85", "src/declared.py", "src/surprise.py")
    _git(git_project, "merge", "-q", "--no-edit", "kyo-85")

    blocked = kyo("touch-drift", dispatched["dispatch_id"], "--against", against, "--json")
    detail = blocked.json["touch_drift"]["detail"]
    assert blocked.json["touch_drift"]["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    assert "kyo report" in detail and "touched_files" in detail

    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    report = _report_file(
        tmp_path,
        dispatched,
        record,
        criterion,
        sha,
        touched_files=["src/declared.py", "src/surprise.py"],
    )
    assert kyo("report", dispatched["dispatch_id"], "--file", str(report)).code == 0

    cleared = kyo("touch-drift", dispatched["dispatch_id"], "--against", against, "--json")
    drift = cleared.json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_FOUND
    assert drift["undeclared_files"] == ["src/surprise.py"]


def test_no_code_path_renders_none_for_an_empty_comparison(kyo, git_project: Path) -> None:
    """KYO-85.c2's second clause, asserted where it is enforced rather than at each
    caller that would have to remember. Whatever the source, an empty body of evidence
    examined nothing, and the single computation that could render "none" refuses to."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    dispatch = {
        "id": "TST-1.d1",
        "record_id": record,
        "branch": "whatever",
        "covers_json": None,
        "report_id": None,
    }
    from kyosei.store import open_store

    store = open_store(git_project)
    for source in (touch_drift.DRIFT_SOURCE_GIT, touch_drift.DRIFT_SOURCE_REPORT):
        drift = touch_drift.touch_drift(
            store, dispatch, [], source=source, examined_nothing="nothing was examined"
        )
        assert drift["outcome"] == touch_drift.DRIFT_NOT_CHECKED
        assert drift["detail"] == "nothing was examined"
        assert "NOT CHECKED" in touch_drift.drift_lines(drift)[0]


def test_an_empty_touched_files_list_does_not_displace_a_diff_that_works(
    kyo, git_project: Path, tmp_path
) -> None:
    """A report may carry `touched_files: []`. That is a statement, but it is not
    evidence about which files were edited, and preferring it over a diff that can
    still answer would swap a working check for a shrug."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)

    dispatched = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-85", "--json"
    ).json
    sha = _branch_editing(git_project, "kyo-85", "src/declared.py", "src/surprise.py")
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    report = _report_file(tmp_path, dispatched, record, criterion, sha, touched_files=[])
    assert kyo("report", dispatched["dispatch_id"], "--file", str(report)).code == 0

    drift = kyo(
        "touch-drift", dispatched["dispatch_id"], "--against", against, "--json"
    ).json["touch_drift"]
    assert drift["source"] == touch_drift.DRIFT_SOURCE_GIT
    assert drift["undeclared_files"] == ["src/surprise.py"]


def test_an_unresolvable_ref_says_a_report_would_not_need_one(kyo, git_project: Path) -> None:
    """The consumer's other half: after the merge they deleted the branch, and got
    "NOT CHECKED — not a known git ref". Honest, and a dead end. It now says what
    would answer the question without any ref."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "no-such-branch", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_NOT_CHECKED
    assert "touched_files" in drift["detail"]


# --------------------------------------------------------------------- KYO-85.c3


def test_the_corrected_list_is_emitted_as_a_command_that_applies_it(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-85.c3. The consumer hand-transcribed touched_files[] into a file and passed
    --touches-file four times: "it has the data."

    `--touches-file` already reads stdin when handed `-`, so the correction needs no
    new flag and no temporary file — only a line that is runnable as printed. Read
    back with shlex.split, per KYO-83: a whitespace-hostile assertion here would pass
    on quoting it was supposed to check.
    """
    import shlex

    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")

    dispatch_id, drift = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-85", "src/declared.py", "src/surprise.py"
    )
    command = drift["apply_command"]
    assert command in kyo("touch-drift", dispatch_id).out

    printf, _, edit = command.partition(" | ")
    # `%s\n` inside single quotes reaches printf as the two characters it needs;
    # shlex hands back exactly what the shell would.
    # KYO-543.c2: the branch created surprise.py and is not merged, so the correction
    # claims it as a new file.
    assert shlex.split(printf) == ["printf", "%s\\n", "src/declared.py", "+src/surprise.py"]
    assert shlex.split(edit) == ["kyo", "edit", record, "--touches-file", "-"]

    # And it is not a suggestion: run it, and the record's touches are corrected.
    _git(git_project, "merge", "-q", "--no-edit", "kyo-85")
    applied = kyo("edit", record, "--touches-file", "-", stdin="src/declared.py\nsrc/surprise.py\n")
    assert applied.code == 0, applied
    assert sorted(t["path"] for t in kyo("show", record, "--json").json["touches"]) == [
        "src/declared.py",
        "src/surprise.py",
    ]


def test_a_path_with_a_space_survives_the_emitted_command(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-89's lesson applied to KYO-85's new output the day it is written, rather
    than after it merges. A worker's own checkout now has a spaced path, but the paths
    inside a repository are its own business — so the case is made explicitly."""
    import shlex

    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")

    _, drift = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-85", "src/declared.py", "src/a file.py"
    )
    printf, _, _edit = drift["apply_command"].partition(" | ")
    assert shlex.split(printf)[2:] == ["+src/a file.py", "src/declared.py"]

    # `kyo edit --touches-file` validates against the working tree, so the branch that
    # created the file has to be merged before the correction can be applied — which
    # is the state this whole record is about.
    _git(git_project, "merge", "-q", "--no-edit", "kyo-85")
    applied = kyo("edit", record, "--touches-file", "-", stdin="src/a file.py\nsrc/declared.py\n")
    assert applied.code == 0, applied


# ===================================================================== KYO-209
#
# The fourth way a declared claim and the diff can disagree, and the only one where
# the record was RIGHT and the check was wrong. A record whose work creates a tree
# declares that tree (`apps/desktop/`, KYO-209); every file the branch then creates
# under it is exactly what the record said would happen, and set difference called all
# of them drift. That is not a nuisance finding — the suggested correction listed the
# whole new tree file by file, marked as "do not exist in this checkout yet", against
# a record that had already said the true thing in one line.


def _declare_lines(kyo, project: Path, record: str, *lines: str) -> None:
    """Declare touches verbatim — no file is created, because the point of a
    directory touch is a path nothing has created yet."""
    scope = project / f"{record}-dir-touches.txt"
    scope.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0


def test_files_created_under_a_declared_directory_are_covered_not_drift(
    kyo, git_project: Path
) -> None:
    """KYO-209.c2's second half, and the consumer's actual complaint one step on: the
    branch built the tree its record claimed, and every file in it read as work done
    outside the claim."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "apps/desktop/")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(
        git_project, "kyo-209", "apps/desktop/main.js", "apps/desktop/ui/window.js"
    )

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-209", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_NONE
    assert drift["undeclared_files"] == []
    # The declared tree did its job, so it is not an unused claim either.
    assert drift["unused_claims"] == []
    # And the correction offered is the line the record already has — not the tree
    # enumerated back at a controller who declared it correctly the first time.
    assert drift["touches_file_lines"] == ["apps/desktop/"]


def test_a_file_outside_the_declared_tree_is_still_drift(kyo, git_project: Path) -> None:
    """A directory touch widens a claim; it must not dissolve it. `apps/desktop/`
    covers what is under `apps/desktop/`, and a sibling that merely shares a prefix
    with the string is not under it."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "apps/desktop/")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-209", "apps/desktop/main.js", "apps/desktop-old.js")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-209", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_FOUND
    assert drift["undeclared_files"] == ["apps/desktop-old.js"]


def test_a_directory_touch_is_never_reported_as_not_yet_in_this_checkout(
    kyo, git_project: Path
) -> None:
    """That list is a statement about what `kyo edit --touches-file` would refuse
    right now, and it refuses nothing about a directory touch. Naming an absent
    `apps/desktop/` there would send a controller to wait for a merge it does not
    need — a false limit is worse than none, because it is believed."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "apps/desktop/")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-209", "apps/desktop/main.js", "src/surprise.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-209", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    drift = result.json["touch_drift"]
    assert not (git_project / "apps" / "desktop").exists()
    assert "apps/desktop/" in drift["touches_file_lines"]
    # KYO-543.c2: the one real file the branch created is claimed as `+path`, which
    # `kyo edit` takes before the merge, so nothing here waits on one.
    assert "+src/surprise.py" in drift["touches_file_lines"]
    assert drift["not_yet_in_this_checkout"] == []

    # And the correction is applicable as it stands, without a merge.
    corrected = git_project / "corrected.txt"
    corrected.write_text(
        "".join(line + "\n" for line in drift["touches_file_lines"]), encoding="utf-8"
    )
    assert kyo("edit", record, "--touches-file", str(corrected)).code == 0


def test_a_declared_tree_nothing_landed_under_is_an_unused_claim(
    kyo, git_project: Path
) -> None:
    """The KYO-71.c4 reading, unchanged for the new form: a prediction that was
    cautious is not a prediction that was wrong. Named, kept in the suggested lines,
    and not an error."""
    record = new_record(kyo, criteria=["a"])
    (git_project / "src").mkdir(parents=True, exist_ok=True)
    (git_project / "src" / "touched.py").write_text("x = 1\n", encoding="utf-8")
    _declare_lines(kyo, git_project, record, "apps/desktop/", "src/touched.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-209", "src/touched.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-209", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_NONE
    assert drift["unused_claims"] == ["apps/desktop/"]
    assert "apps/desktop/" in drift["touches_file_lines"]


# --------------------------------------------------------------------- KYO-206.c3


def test_the_report_path_is_never_reported_as_drift(kyo, git_project: Path) -> None:
    """From a consumer report: `kyo touch-drift` proposed the
    worker's report file as a touch, so the `kyo edit --touches-file` line it printed
    named the report — and once the controller had dropped that file from the tree, the
    edit failed validation ("no such file"). The report is the dispatch's OUTPUT, not a
    file its work edited.

    Delete `_DRIFT_IGNORED_PATHS` from `_considered` in `touch_drift` and this fails.
    """
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    # A branch that added ONLY the report — the shape the consumer's branches had,
    # because the brief told them to commit before stopping and named no ignored path.
    # `git add -f`, because `kyo init` now writes the ignore entry: what is reproduced
    # here is a branch made BEFORE that entry existed, which is the branch the exclusion
    # in `_considered` has to keep answering about.
    _git(git_project, "checkout", "-q", "-b", "kyo-206")
    (git_project / WORKER_REPORT_PATH).write_text('{"dispatch_id": "x"}\n', encoding="utf-8")
    _git(git_project, "add", "-f", WORKER_REPORT_PATH)
    _git(git_project, "commit", "-q", "-m", "the report, committed the old way")
    _git(git_project, "checkout", "-q", against)

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-206", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    assert result.code == 0, result
    drift = result.json["touch_drift"]
    assert WORKER_REPORT_PATH not in drift["changed_files"]
    assert drift["undeclared_files"] == []
    assert WORKER_REPORT_PATH not in drift["touches_file_lines"]
    assert WORKER_REPORT_PATH not in (drift["apply_command"] or "")
    # The same absence in the rendering the controller actually reads.
    assert WORKER_REPORT_PATH not in "\n".join(touch_drift.drift_lines(drift))


def test_a_report_that_names_its_own_path_in_touched_files_does_not_drift(
    kyo, git_project: Path, tmp_path
) -> None:
    """The same exclusion on the other source of evidence. `touch_drift` is answered
    from the report's `touched_files[]` wherever there is one (KYO-85), so a worker that
    listed its own report there would put the file straight back into the suggested
    `kyo edit --touches-file` correction — which is the line the consumer could not run.
    """
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")

    sha = _branch_editing(git_project, "kyo-206b", "src/declared.py")
    dispatched = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-206b", "--json"
    ).json
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    report = _report_file(
        tmp_path,
        dispatched,
        record,
        criterion,
        sha,
        touched_files=["src/declared.py", WORKER_REPORT_PATH],
    )
    ingested = kyo("report", dispatched["dispatch_id"], "--file", str(report), "--json")
    assert ingested.code == 0, ingested

    drift = ingested.json["touch_drift"]
    assert drift["source"] == touch_drift.DRIFT_SOURCE_REPORT
    assert drift["changed_files"] == ["src/declared.py"]
    assert drift["undeclared_files"] == []
    assert WORKER_REPORT_PATH not in drift["touches_file_lines"]


# ===================================================================== KYO-225
#
# The same finding as KYO-209's, one scale smaller and reported by a different
# consumer: every worker reported touch drift, because a new file could not be
# declared as a touch until it existed. The directory form was not the answer
# there — the work added files beside existing ones, so claiming the enclosing tree
# would have overstated the scope and collided with every other dispatch under it.


def test_a_file_created_under_its_own_declared_marker_is_covered_not_drift(
    kyo, git_project: Path
) -> None:
    """KYO-225.c2. The branch created exactly the file its record claimed it would."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "+src/new_module.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-225", "src/new_module.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-225", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_NONE
    assert drift["undeclared_files"] == []
    assert drift["unused_claims"] == []
    # The correction offered is the line the record already has, marker and all.
    assert drift["touches_file_lines"] == ["+src/new_module.py"]
    # And the marked line is not listed as a path `kyo edit --touches-file` would
    # refuse today: that form is accepted whether or not the file exists, so telling a
    # controller to wait for a merge would be telling them to wait for nothing.
    assert drift["not_yet_in_this_checkout"] == []


def test_a_sibling_file_the_marker_does_not_name_is_still_drift(
    kyo, git_project: Path
) -> None:
    """The marker claims ONE file. That is the whole reason it exists beside the
    directory form, so it must not quietly widen into the directory form."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "+src/new_module.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _branch_editing(git_project, "kyo-225", "src/new_module.py", "src/other_module.py")

    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-225", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["outcome"] == touch_drift.DRIFT_FOUND
    assert drift["undeclared_files"] == ["src/other_module.py"]


# ===================================================================== KYO-307
#
# The far side of KYO-71's own caveat, found by this repo's controller the first time
# it used `kyo edit --touches-from-drift` (the flag KYO-302 had just put into the
# skill):
#
#     $ kyo edit KYO-294 --touches-from-drift KYO-294.d1
#     touch validation failed:
#       - +tests/test_kyo294_inherited_store_diff.py: that path is already there, so
#         this work does not create it ...; drop the '+' to claim the file as it stands
#
# The same refusal met KYO-300 and KYO-303 in the same session. Every piece of it is
# working as designed and the result is a one-step correction that fails exactly when
# the workflow says to run it: a record that declares `+path` (KYO-225) is the ordinary
# case for a dispatch that adds a test file, the skill applies the correction AFTER the
# merge because a created file exists only on the branch until then, and after the
# merge the re-emitted `+path` names a file that now exists. The controller went back
# to building the touches file by hand — from the correction's own lines, with the `+`
# dropped, which is precisely the edit this now makes.


def _touch_paths(kyo, record: str) -> list[str]:
    return [t["path"] for t in kyo("show", record, "--json").json["touches"]]


def _a_dispatch_that_creates_a_file(kyo, git_project: Path, tmp_path, record: str):
    """A record claiming one existing file and one it will create, dispatched, worked
    and reported — with an undeclared edit to a file that was already on main.

    The undeclared file is deliberately one that EXISTS before the branch: this fixture
    is used on both sides of the merge, and a correction naming a file the branch
    created is refused pre-merge for the older reason (KYO-71's caveat), which would
    hide the one this record is about.
    """
    for path in ("src/declared.py", "src/surprise.py"):
        target = git_project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    _declare_lines(kyo, git_project, record, "src/declared.py", "+tests/test_new.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    dispatch_id, at_ingest = _dispatch_and_report(
        kyo,
        git_project,
        tmp_path,
        record,
        "kyo-307",
        "src/declared.py",
        "tests/test_new.py",
        "src/surprise.py",
    )
    return dispatch_id, against, at_ingest


def test_the_correction_applies_on_both_sides_of_the_merge(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-307.c1, in the order the workflow happens: the created file is on the branch
    only, the correction keeps the marker and applies; the branch merges, the file is
    now there, and the correction names it as it stands and applies again."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, against, at_ingest = _a_dispatch_that_creates_a_file(
        kyo, git_project, tmp_path, record
    )

    # Before the merge — unchanged from KYO-225: the file does not exist yet, so the
    # claim to create it is still the true one and is emitted as it stands.
    assert at_ingest["landed_new_files"] == []
    assert at_ingest["touches_file_lines"] == [
        "+tests/test_new.py",
        "src/declared.py",
        "src/surprise.py",
    ]
    before = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert before.code == 0, before
    assert "+tests/test_new.py" in _touch_paths(kyo, record)
    # Nothing was converted, so nothing says anything was.
    assert "now exists" not in before.out

    _git(git_project, "merge", "-q", "--no-edit", "kyo-307")
    assert (git_project / "tests" / "test_new.py").is_file()

    after = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert after["touches_file_lines"] == [
        "src/declared.py",
        "src/surprise.py",
        "tests/test_new.py",
    ]
    assert after["landed_new_files"] == [
        {"was": "+tests/test_new.py", "now": "tests/test_new.py"}
    ]
    applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert applied.code == 0, applied
    # And it says which lines it converted, rather than quietly rewriting the claim.
    assert "+tests/test_new.py -> tests/test_new.py" in applied.out
    assert _touch_paths(kyo, record) == after["touches_file_lines"]


def test_the_refusal_this_record_reproduces_is_gone(kyo, git_project: Path, tmp_path) -> None:
    """The reproduction from the record, as a test. Every part of it was working as
    designed, which is why the assertion is about the outcome: the one-step correction
    the skill sends a controller to, run where the skill says to run it, succeeds."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, _, _ = _a_dispatch_that_creates_a_file(kyo, git_project, tmp_path, record)
    _git(git_project, "merge", "-q", "--no-edit", "kyo-307")

    applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert applied.code == 0, applied
    assert "that path is already there" not in applied.err
    assert "touch validation failed" not in applied.err


def test_the_printed_lines_paste_in_on_both_sides_of_the_merge(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-307.c2. The flag reads `kyo touch-drift`'s own list, so agreement is
    structural — and this is what that is worth: the lines a controller reads are the
    lines `kyo edit --touches-file` accepts, in both situations. It matters because the
    other place the conversion could have gone is validation, where relaxing the rule
    for the flag would have left these printed lines refused."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, against, _ = _a_dispatch_that_creates_a_file(
        kyo, git_project, tmp_path, record
    )

    for merged in (False, True):
        if merged:
            _git(git_project, "merge", "-q", "--no-edit", "kyo-307")
        printed = kyo("touch-drift", dispatch_id, "--against", against, "--json")
        assert printed.code == 0, printed
        lines = printed.json["touch_drift"]["touches_file_lines"]
        # The human rendering carries the same lines the JSON does — read before
        # anything is applied, because a correction that has been applied is no longer
        # a correction to print.
        text = kyo("touch-drift", dispatch_id, "--against", against)
        for line in lines:
            assert f"    {line}" in text.out, text
        # Exactly as printed, through the hand path...
        pasted = kyo(
            "edit", record, "--touches-file", "-",
            stdin="".join(line + "\n" for line in lines),
        )
        assert pasted.code == 0, pasted
        assert _touch_paths(kyo, record) == lines
        # ...and through the flag, which must agree line for line.
        applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
        assert applied.code == 0, applied
        assert _touch_paths(kyo, record) == lines


def test_a_directory_touch_needs_no_conversion_after_the_merge(
    kyo, git_project: Path, tmp_path
) -> None:
    """The `dir/` form has the same shape and NOT the same bug, and that is worth a
    test rather than a sentence: a directory touch is accepted whether or not the tree
    exists (KYO-209), so there is nothing for the merge to invalidate. Converting one
    would be inventing a change to a line that was already applicable."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "apps/desktop/")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-307", "apps/desktop/main.js"
    )
    _git(git_project, "merge", "-q", "--no-edit", "kyo-307")
    assert (git_project / "apps" / "desktop").is_dir()

    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["touches_file_lines"] == ["apps/desktop/"]
    assert drift["landed_new_files"] == []
    applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert applied.code == 0, applied
    assert _touch_paths(kyo, record) == ["apps/desktop/"]


def test_a_marked_path_that_became_a_directory_keeps_its_marker(
    kyo, git_project: Path, tmp_path
) -> None:
    """Only a marked path that is now a FILE is converted. A `+path` the branch turned
    into a directory describes nothing that happened, and the honest answer is the
    existing refusal — converting it would hand a controller `src/thing` to store as a
    file touch, which validation would then refuse for a different reason and which no
    reader could act on."""
    record = new_record(kyo, criteria=["a"])
    _declare_lines(kyo, git_project, record, "+src/thing")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-307", "src/thing/inner.py"
    )
    _git(git_project, "merge", "-q", "--no-edit", "kyo-307")
    assert (git_project / "src" / "thing").is_dir()

    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["landed_new_files"] == []
    assert "+src/thing" in drift["touches_file_lines"]
