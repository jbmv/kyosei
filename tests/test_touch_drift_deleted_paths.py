"""KYO-436: the touch-drift correction drops a claimed file that no longer exists.

Found accepting KYO-431.d1 (the cmd_review.py split): the branch deleted a file its
record claimed, the correction kept the claim ("an unfinished record has claims it has
not reached"), and `kyo edit --touches-from-drift` then refused its own list, because a
plain path must exist. So `kyo accept` ended INCOMPLETE on every dispatch that deleted a
claimed file. The same happens to a claimed file another merge removed.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import touch_drift

from .conftest import new_record
from .test_touch_drift import _declare, _dispatch_and_report, _git, _main_branch, _touch_paths


def _branch_deleting(project: Path, branch: str, deleted: str, edited: str) -> str:
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", branch)
    _git(project, "rm", "-q", deleted)
    (project / edited).write_text("edited = True\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", f"work on {branch}")
    sha = _git(project, "rev-parse", "HEAD")
    _git(project, "checkout", "-q", base)
    return sha


def _a_dispatch_that_deletes_a_claimed_file(kyo, git_project: Path, tmp_path, record):
    _declare(kyo, git_project, record, "src/split_me.py", "src/kept.py", "src/unreached.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    sha = _branch_deleting(git_project, "kyo-436", "src/split_me.py", "src/kept.py")
    dispatched = kyo("dispatch", record, "--branch", "kyo-436", "--json").json
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    from .test_touch_drift import _report_file

    report = _report_file(
        tmp_path, dispatched, record, criterion, sha,
        touched_files=["src/split_me.py", "src/kept.py"],
    )  # fmt: skip
    ingested = kyo("report", dispatched["dispatch_id"], "--file", str(report), "--json")
    assert ingested.code == 0, ingested
    return dispatched["dispatch_id"], against


def test_a_claimed_file_the_branch_deleted_is_dropped_and_the_edit_succeeds(
    kyo, git_project: Path, tmp_path
) -> None:
    """c1 and c3: after the merge the deleted path is gone from the correction, the
    unreached claim is still kept, the edit applies, and the output says what it
    dropped and why."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, against = _a_dispatch_that_deletes_a_claimed_file(
        kyo, git_project, tmp_path, record
    )
    _git(git_project, "merge", "-q", "--no-edit", "kyo-436")
    assert not (git_project / "src" / "split_me.py").exists()

    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert "src/split_me.py" not in drift["touches_file_lines"]
    assert drift["touches_file_lines"] == ["src/kept.py", "src/unreached.py"]
    assert drift["gone_paths_dropped"] == [
        {"line": "src/split_me.py", "why": touch_drift.GONE_DELETED_BY_THIS_BRANCH}
    ]

    applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert applied.code == 0, applied
    assert "touch validation failed" not in applied.err
    assert "deleted by this branch: src/split_me.py" in applied.out
    assert _touch_paths(kyo, record) == ["src/kept.py", "src/unreached.py"]


def test_before_the_merge_the_deleted_claim_still_exists_and_is_kept(
    kyo, git_project: Path, tmp_path
) -> None:
    """Pre-merge the file is still in the checkout, so the claim is still valid and
    nothing is dropped — the drop is about the file being gone, not about the branch
    having touched it."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, against = _a_dispatch_that_deletes_a_claimed_file(
        kyo, git_project, tmp_path, record
    )
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert "src/split_me.py" in drift["touches_file_lines"]
    assert drift["gone_paths_dropped"] == []


def test_a_claimed_file_another_merge_removed_is_dropped_too(
    kyo, git_project: Path, tmp_path
) -> None:
    """The second shape: this branch never touched the file, a different merge
    deleted it from the trunk."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py", "src/elsewhere.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-436b", "src/declared.py"
    )
    _git(git_project, "rm", "-q", "src/elsewhere.py")
    _git(git_project, "commit", "-q", "-m", "another merge removes it")

    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["touches_file_lines"] == ["src/declared.py"]
    assert drift["gone_paths_dropped"] == [
        {"line": "src/elsewhere.py", "why": touch_drift.GONE_FROM_THE_TRUNK}
    ]
    text = kyo("touch-drift", dispatch_id, "--against", against).out
    assert "removed from this checkout by some other change: src/elsewhere.py" in text

    applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert applied.code == 0, applied
    assert _touch_paths(kyo, record) == ["src/declared.py"]


def test_an_undeclared_file_the_branch_created_is_still_kept_before_the_merge(
    kyo, git_project: Path, tmp_path
) -> None:
    """The one absent line that must NOT be dropped: a file the branch created exists
    only at its tip until the merge, and is real drift (KYO-71's caveat). KYO-543.c2:
    kept as `+path`, the form `kyo edit` accepts for a file not there yet, so it no
    longer waits on the merge."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-436c", "src/declared.py", "src/created.py"
    )
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert "+src/created.py" in drift["touches_file_lines"]
    assert drift["not_yet_in_this_checkout"] == []
    assert drift["gone_paths_dropped"] == []


def test_accept_completes_its_touches_step_for_a_deleted_claim(
    kyo, git_project: Path, tmp_path
) -> None:
    """c2: `kyo accept` runs the same correction and its touches step is no longer
    refused for this reason."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, _ = _a_dispatch_that_deletes_a_claimed_file(
        kyo, git_project, tmp_path, record
    )
    _git(git_project, "merge", "-q", "--no-edit", "kyo-436")

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    [step] = [s for s in accepted.json["steps"] if "--touches-from-drift" in s["argv"]]
    assert not step.get("refused"), step
    assert _touch_paths(kyo, record) == ["src/kept.py", "src/unreached.py"]


def test_an_undeclared_file_the_branch_deleted_is_dropped_after_the_merge(
    kyo, git_project: Path, tmp_path
) -> None:
    """An undeclared deletion is dropped only on positive evidence — the trunk's history
    records the deletion — never on absence alone, which a created-and-unmerged file
    shares (see the KYO-147 test that keeps refusing that one)."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    (git_project / "src" / "stray.py").write_text("x = 1\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    sha = _branch_deleting(git_project, "kyo-436d", "src/stray.py", "src/declared.py")
    dispatched = kyo("dispatch", record, "--branch", "kyo-436d", "--json").json
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    from .test_touch_drift import _report_file

    report = _report_file(
        tmp_path, dispatched, record, criterion, sha,
        touched_files=["src/declared.py", "src/stray.py"],
    )  # fmt: skip
    assert kyo("report", dispatched["dispatch_id"], "--file", str(report)).code == 0
    _git(git_project, "merge", "-q", "--no-edit", "kyo-436d")

    drift = kyo(
        "touch-drift", dispatched["dispatch_id"], "--against", against, "--json"
    ).json["touch_drift"]
    assert drift["touches_file_lines"] == ["src/declared.py"]
    assert drift["gone_paths_dropped"] == [
        {"line": "src/stray.py", "why": touch_drift.GONE_DELETED_BY_THIS_BRANCH}
    ]
    applied = kyo("edit", record, "--touches-from-drift", dispatched["dispatch_id"])
    assert applied.code == 0, applied


# --------------------------------------------------------------------- KYO-543


def _a_merged_fragment_the_trunk_deleted(kyo, git_project: Path, tmp_path, record):
    """A consumer project's sequence: the branch CREATES a notes fragment, the branch is
    merged, and `kyo docs assemble` deletes the fragment on the trunk. The branch is
    left in place, so its tip still has the file."""
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    fragment = "apps/web/notes-fragments/KYO-1.d1--a-fragment.md"
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-543", "src/declared.py", fragment
    )
    _git(git_project, "merge", "-q", "--no-edit", "kyo-543")
    _git(git_project, "rm", "-q", fragment)
    _git(git_project, "commit", "-q", "-m", "docs assemble consumes the fragment")
    tip = _git(git_project, "rev-parse", "kyo-543")
    assert _git(git_project, "cat-file", "-t", f"{tip}:{fragment}") == "blob"
    return dispatch_id, against, fragment


def test_a_created_file_the_trunk_deleted_after_the_merge_is_dropped(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-543.c1: the tip still has the fragment, but the tip is merged into HEAD and
    HEAD's history deletes it, so it is dropped — named as this branch's own file
    deleted after the merge — and the edit stores the correction without a refusal.
    Restoring `deleted_here`'s unconditional "the tip has it, keep it" check keeps the
    fragment and this test fails."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, against, fragment = _a_merged_fragment_the_trunk_deleted(
        kyo, git_project, tmp_path, record
    )
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["touches_file_lines"] == ["src/declared.py"]
    assert drift["gone_paths_dropped"] == [
        {"line": fragment, "why": touch_drift.GONE_AFTER_THE_MERGE}
    ]
    text = kyo("touch-drift", dispatch_id, "--against", against).out
    assert (
        f"this branch's own file, deleted on the trunk after the branch merged: {fragment}"
        in text
    )

    applied = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert applied.code == 0, applied
    assert "touch validation failed" not in applied.err
    assert _touch_paths(kyo, record) == ["src/declared.py"]


def test_accept_completes_its_touches_step_after_docs_assemble_deleted_a_fragment(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-543.c1, through the command that hit it: `kyo accept`'s touches step."""
    record = new_record(kyo, criteria=["a"])
    dispatch_id, _, _ = _a_merged_fragment_the_trunk_deleted(
        kyo, git_project, tmp_path, record
    )
    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    [step] = [s for s in accepted.json["steps"] if "--touches-from-drift" in s["argv"]]
    assert not step.get("refused"), step
    assert _touch_paths(kyo, record) == ["src/declared.py"]


def test_a_created_file_deleted_on_the_trunk_without_the_merge_is_still_kept(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-543's edge: the tip has the file and the trunk's history even deletes the
    same path, but the branch is NOT merged — the file is unlanded work (KYO-71), not a
    consumed fragment, so ancestry rather than the deletion decides: it is kept, as the
    new file it is (c2)."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    (git_project / "src" / "recreated.py").write_text("old = 1\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    _git(git_project, "rm", "-q", "src/recreated.py")
    _git(git_project, "commit", "-q", "-m", "the trunk deletes an older file of that name")
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-543b", "src/declared.py", "src/recreated.py"
    )
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert "+src/recreated.py" in drift["touches_file_lines"]
    assert drift["gone_paths_dropped"] == []


def test_accept_before_the_merge_stores_a_created_file_as_a_new_file_touch(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-543.c2 (a consumer's APP-219.d1 and APP-252.d1: a migration and a notes
    fragment, accepted before merging): the created file is in the correction as
    `+path`, so `kyo accept`'s touches step stores it rather than meeting the plain-path
    refusal; after the merge KYO-307's landing names it as the plain path again."""
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "src/declared.py")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "baseline")
    against = _main_branch(git_project)
    migration = "migrations/0042_add_a_column.sql"
    dispatch_id, _ = _dispatch_and_report(
        kyo, git_project, tmp_path, record, "kyo-543c", "src/declared.py", migration
    )
    assert not (git_project / migration).exists()

    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json["touch_drift"]
    assert drift["touches_file_lines"] == [f"+{migration}", "src/declared.py"]
    text = kyo("touch-drift", dispatch_id, "--against", against).out
    assert f"claims it as a new file: +{migration}" in text

    accepted = kyo("accept", dispatch_id, "--json")
    assert accepted.code == 0, accepted
    [step] = [s for s in accepted.json["steps"] if "--touches-from-drift" in s["argv"]]
    assert not step.get("refused"), step
    assert sorted(_touch_paths(kyo, record)) == [f"+{migration}", "src/declared.py"]

    _git(git_project, "merge", "-q", "--no-edit", "kyo-543c")
    after = kyo("edit", record, "--touches-from-drift", dispatch_id)
    assert after.code == 0, after
    assert sorted(_touch_paths(kyo, record)) == [migration, "src/declared.py"]
