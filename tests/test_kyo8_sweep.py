"""KYO-8.c1/c2/c3: kyo sweep's three flag classes, and kyo flag resolve."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .conftest import git_init, new_record, verify_criterion


def _promote_with_touches(kyo, project: Path, write_file, *, touch_path: str, name="promote-me"):
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDo the thing.\n", encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "chore",
        "--criteria-file",
        write_file(f"{name}-criteria.txt", "it works\n"),
        "--touches-file",
        write_file(f"{name}-touches.txt", f"{touch_path}\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


# --------------------------------------------------------------- KYO-8.c2: touch-missing


def test_sweep_flags_a_touch_that_no_longer_exists(kyo, project: Path, write_file) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    record = _promote_with_touches(kyo, project, write_file, touch_path="widget.py")

    target.unlink()
    result = kyo("sweep", "--json")
    assert result.code == 0, result
    assert result.json["flags"], "expected a touch-missing flag"
    flag = result.json["flags"][0]
    assert flag["record_id"] == record
    assert flag["flag_type"] == "touch-missing"
    assert "widget.py" in flag["detail"]

    shown = kyo("show", record, "--json")
    assert len(shown.json["flags"]) == 1
    assert shown.json["flags"][0]["resolved_at"] is None


def test_sweeping_again_does_not_duplicate_an_unresolved_flag(
    kyo, project: Path, write_file
) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    record = _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    target.unlink()

    first = kyo("sweep", "--json")
    assert len(first.json["flags"]) == 1
    second = kyo("sweep", "--json")
    assert second.json["flags"] == []  # already flagged, unresolved — no duplicate

    shown = kyo("show", record, "--json")
    assert len(shown.json["flags"]) == 1


def test_a_kind_with_swept_false_is_never_flagged(kyo, project: Path, write_file) -> None:
    """A work-archetype kind with policy.swept: false must never be touched by
    `kyo sweep`, even with a missing touch — `swept`'s promised full consumer
    (schema.py's `touches` COLUMN_CONSUMERS entry)."""
    import yaml

    kinds_path = project / "kyosei" / "kinds.yml"
    raw = yaml.safe_load(kinds_path.read_text(encoding="utf-8"))
    raw["kinds"]["plan"]["policy"]["swept"] = False
    kinds_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    idea = project / "ideas" / "a-plan.md"
    idea.write_text("# A Plan\n\nOwns some work.\n", encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "plan",
        "--touches-file",
        write_file("plan-touches.txt", "widget.py\n"),
        "--json",
    )
    assert result.code == 0, result
    target.unlink()

    swept = kyo("sweep", "--json")
    assert swept.json["flags"] == []


# ----------------------------------------------------------------- KYO-8.c1: touch-changed


def test_sweep_flags_a_touch_changed_since_the_last_sweep(
    kyo, project: Path, write_file
) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    record = _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    git_init(project, commit_message="baseline")

    baseline = kyo("sweep", "--json")
    assert baseline.code == 0, baseline
    assert baseline.json["flags"] == [], "first sweep just records a baseline"
    assert baseline.json["base_sha"] is not None
    assert baseline.json["prior_sha"] is None

    target.write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "change widget"], cwd=project, check=True, capture_output=True
    )

    swept = kyo("sweep", "--json")
    assert swept.code == 0, swept
    assert len(swept.json["flags"]) == 1
    flag = swept.json["flags"][0]
    assert flag["record_id"] == record
    assert flag["flag_type"] == "touch-changed"
    assert "widget.py" in flag["detail"]
    assert swept.json["prior_sha"] == baseline.json["base_sha"]


def test_sweep_with_no_git_repo_skips_touch_changed_without_crashing(
    kyo, project: Path, write_file
) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _promote_with_touches(kyo, project, write_file, touch_path="widget.py")

    result = kyo("sweep", "--json")
    assert result.code == 0, result
    assert result.json["base_sha"] is None
    assert result.json["flags"] == []
    # KYO-63: skipping the class is fine; skipping it quietly is not. The run says
    # which question it did not answer, rather than leaving an empty flag list to
    # speak for it.
    assert result.json["coverage"] == "no-git"


def test_sweeping_twice_with_no_new_commits_produces_no_new_flags(
    kyo, project: Path, write_file
) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    git_init(project, commit_message="baseline")

    kyo("sweep")
    second = kyo("sweep", "--json")
    assert second.json["flags"] == []
    assert second.json["prior_sha"] == second.json["base_sha"]


# ------------------------------------------------------- KYO-8.c3: near-duplicate criteria


def test_sweep_flags_near_duplicate_criteria_across_records(
    kyo, project: Path, write_file
) -> None:
    idea_a = project / "ideas" / "a.md"
    idea_a.write_text("# A\n\nFirst.\n", encoding="utf-8")
    rec_a = kyo(
        "promote",
        str(idea_a),
        "--kind",
        "chore",
        "--criteria-file",
        write_file("a-criteria.txt", "the export must round-trip through kyo rebuild\n"),
        "--touches-file",
        write_file("a-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert rec_a.code == 0, rec_a

    idea_b = project / "ideas" / "b.md"
    idea_b.write_text("# B\n\nSecond.\n", encoding="utf-8")
    rec_b = kyo(
        "promote",
        str(idea_b),
        "--kind",
        "chore",
        "--criteria-file",
        write_file("b-criteria.txt", "the export must round trip through kyo rebuild\n"),
        "--touches-file",
        write_file("b-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert rec_b.code == 0, rec_b

    swept = kyo("sweep", "--json")
    assert swept.code == 0, swept
    dup_flags = [f for f in swept.json["flags"] if f["flag_type"] == "near-duplicate-criteria"]
    assert len(dup_flags) == 1
    assert dup_flags[0]["record_id"] == rec_a.json["record"]["id"]
    assert rec_b.json["record"]["id"] in dup_flags[0]["detail"]


def test_near_duplicate_criteria_within_the_same_record_is_not_flagged(
    kyo, project: Path, write_file
) -> None:
    idea = project / "ideas" / "self-dup.md"
    idea.write_text("# Self Dup\n\nSame idea twice.\n", encoding="utf-8")
    text = "the export must round-trip through kyo rebuild without drift"
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "chore",
        "--criteria-file",
        write_file("self-dup-criteria.txt", f"{text}\n{text}\n"),
        "--touches-file",
        write_file("self-dup-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    swept = kyo("sweep", "--json")
    dup_flags = [f for f in swept.json["flags"] if f["flag_type"] == "near-duplicate-criteria"]
    assert dup_flags == []


# --------------------------------------------------------------------- kyo flag resolve


def test_flag_resolve_marks_it_resolved(kyo, project: Path, write_file) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    record = _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    target.unlink()
    swept = kyo("sweep", "--json")
    flag_id = swept.json["flags"][0]["flag_id"]

    resolved = kyo("flag", "resolve", flag_id, "--why", "false positive, refactor in progress")
    assert resolved.code == 0, resolved

    shown = kyo("show", record, "--json")
    flag = shown.json["flags"][0]
    assert flag["resolved_at"] is not None
    assert flag["resolved_by"] == "orchestrator"
    assert flag["resolved_why"] == "false positive, refactor in progress"


def test_resolving_an_already_resolved_flag_is_a_contract_violation(
    kyo, project: Path, write_file
) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    target.unlink()
    flag_id = kyo("sweep", "--json").json["flags"][0]["flag_id"]

    assert kyo("flag", "resolve", flag_id).code == 0
    again = kyo("flag", "resolve", flag_id)
    assert again.code == 2
    assert "already resolved" in again.err


def test_resolving_an_unknown_flag_id_fails_loudly(kyo) -> None:
    result = kyo("flag", "resolve", "TST-1.f99")
    assert result.code == 1


def test_a_resolved_flag_is_excluded_from_unresolved_and_a_new_sweep_can_reflag(
    kyo, project: Path, write_file
) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    target.unlink()
    flag_id = kyo("sweep", "--json").json["flags"][0]["flag_id"]
    kyo("flag", "resolve", flag_id)

    # Still missing — sweeping again re-flags it (the old flag stays resolved,
    # a new one is created; sweep never edits, only flags).
    resweep = kyo("sweep", "--json")
    assert len(resweep.json["flags"]) == 1
    assert resweep.json["flags"][0]["flag_id"] != flag_id


# --------------------------------------------------------------------------- doctor


def test_doctor_passes_after_sweep_and_flag_resolve(kyo, project: Path, write_file) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    target.unlink()
    flag_id = kyo("sweep", "--json").json["flags"][0]["flag_id"]
    kyo("flag", "resolve", flag_id, "--why", "acknowledged")

    result = kyo("doctor", "--json")
    assert result.code == 0, result.json if result.out else result.err


def test_sweep_run_baseline_is_recorded_even_with_zero_flags(kyo, project: Path) -> None:
    new_record(kyo, kind="bug", criteria=["a"])
    result = kyo("sweep", "--json")
    assert result.code == 0, result
    assert result.json["sweep_run_id"]
    assert result.json["flags"] == []


# ------------------------------------------------- KYO-63: staleness coverage
# Sweep's bookkeeping describes the CODE repository's history while living in the
# STORE repository. These record what one run can say about that, in the layout where
# the two are the same repo; `tests/test_split_store.py` drives the failure itself,
# which needs two repositories to exist at all.


def test_a_sweep_records_the_code_history_it_swept_not_only_a_sha(
    kyo, git_project: Path
) -> None:
    """KYO-63.c1: a sha alone cannot tell the next run whether it is looking at the
    repository this one swept — an absent commit and a foreign repository are the same
    failed diff. The root commit is what identifies a history across clone, fork and
    rename, so that is what the row carries beside the sha."""
    baseline = kyo("sweep", "--json")
    assert baseline.code == 0, baseline
    assert baseline.json["coverage"] == "baseline"

    root_commit = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=git_project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    row = json.loads(
        (git_project / "kyosei" / "export" / "_sweeps.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert row["code_repo_id"] == root_commit
    assert row["base_sha"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_project, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert row["coverage"] == "baseline"

    # The second run has a base to diff from, in the history it recorded: answered.
    second = kyo("sweep", "--json")
    assert second.json["coverage"] == "swept"
    assert "diffed" in second.json["coverage_detail"]


def test_the_first_sweep_does_not_call_its_own_baseline_a_clean_result(
    kyo, git_project: Path
) -> None:
    """The narrowest form of the whole record: zero flags is not an answer. A first
    sweep has no window to diff at all, and its output says that where the flag count
    is read rather than only in a JSON field nobody prints."""
    result = kyo("sweep")
    assert result.code == 0, result
    assert "not answered" in result.out
    assert "not a clean bill" in result.out


def test_the_brief_says_a_store_has_never_been_swept(kyo, project: Path) -> None:
    """KYO-63.c2, one step earlier than the split-layout failure: a store nobody has
    swept has exactly the same empty flag list as a store with nothing stale, and the
    brief is where a controller reads that list at session start."""
    new_record(kyo, kind="bug", criteria=["a"])
    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["staleness"]["coverage"] == "never-swept"
    assert result.json["staleness"]["sweep_run_id"] is None

    human = kyo("brief")
    assert "staleness coverage (never-swept)" in human.out
    assert "not a clean bill" in human.out


def test_a_brief_after_a_real_sweep_reports_staleness_as_answered(
    kyo, git_project: Path
) -> None:
    """The other half of the mechanism: on a store where the check did run, the brief
    must not carry the warning at all, or it is noise rather than a signal."""
    kyo("sweep")
    kyo("sweep")
    result = kyo("brief", "--json")
    assert result.json["staleness"]["coverage"] == "swept"
    human = kyo("brief")
    assert "staleness coverage (swept):" in human.out
    assert "not a clean bill" not in human.out


def test_sweep_ledger_is_git_reviewable(kyo, project: Path) -> None:
    """The sweep-run bookkeeping lands in kyosei/export/_sweeps.jsonl, an
    append-only ledger mirroring _events.jsonl (charter, Layer 2)."""
    kyo("sweep")
    ledger = project / "kyosei" / "export" / "_sweeps.jsonl"
    assert ledger.is_file()
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["flags_created"] == 0


# ------------------------------------- KYO-69: one open touch-changed finding per record


def _promote_touching(
    kyo, project: Path, write_file, *, paths: list[str], name: str, kind: str = "chore"
) -> str:
    """Promote a record declaring several touches. The single-path helper above predates
    KYO-69, whose whole subject is a record whose touch list keeps being hit by different
    subsets of itself on different sweeps."""
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDo the thing.\n", encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        kind,
        "--criteria-file",
        write_file(f"{name}-criteria.txt", "it works\n"),
        "--touches-file",
        write_file(f"{name}-touches.txt", "".join(f"{p}\n" for p in paths)),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _commit_change(project: Path, path: str, text: str) -> None:
    (project / path).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"change {path}"],
        cwd=project,
        check=True,
        capture_output=True,
    )


def _open_touch_changed(kyo, record: str) -> list[dict]:
    shown = kyo("show", record, "--json")
    assert shown.code == 0, shown
    return [
        f
        for f in shown.json["flags"]
        if f["resolved_at"] is None and f["flag_type"] == "touch-changed"
    ]


def test_repeated_sweeps_over_a_moving_record_keep_exactly_one_open_finding(
    kyo, project: Path, write_file
) -> None:
    """KYO-69.c4 — the regression test the defect exists for.

    `kyo sweep` runs on every Stop hook, so before supersession a record that merely sat
    open across a busy day collected one flag per session-stop, every one true and all of
    them the same finding. Against the pre-fix build this asserts 1 and gets 3.
    """
    files = ["alpha.py", "beta.py", "gamma.py"]
    for name in files:
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=files, name="movers")
    git_init(project, commit_message="baseline")

    baseline = kyo("sweep", "--json")
    assert baseline.json["flags"] == []
    origin = baseline.json["base_sha"]

    for i, name in enumerate(files):
        _commit_change(project, name, f"x = {i + 2}\n")
        swept = kyo("sweep", "--json")
        assert swept.code == 0, swept

    open_flags = _open_touch_changed(kyo, record)
    assert len(open_flags) == 1, open_flags
    detail = open_flags[0]["detail"]
    # The one flag says everything the three separate ones would have said.
    for name in files:
        assert name in detail
    assert detail.startswith(f"changed over {origin[:12]}..")
    assert "in 3 sweeps" in detail


def test_a_later_sweep_widens_the_standing_flag_rather_than_filing_another(
    kyo, project: Path, write_file
) -> None:
    """KYO-69.c1 in detail: same flag id, same start sha, later end sha, unioned files,
    and the sweep reports it as widened rather than created."""
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(
        kyo, project, write_file, paths=["alpha.py", "beta.py"], name="widen-me"
    )
    git_init(project, commit_message="baseline")
    origin = kyo("sweep", "--json").json["base_sha"]

    _commit_change(project, "alpha.py", "x = 2\n")
    first = kyo("sweep", "--json")
    assert len(first.json["flags"]) == 1
    assert first.json["widened_flags"] == []
    flag_id = first.json["flags"][0]["flag_id"]
    assert "alpha.py" in first.json["flags"][0]["detail"]
    assert "beta.py" not in first.json["flags"][0]["detail"]

    _commit_change(project, "beta.py", "x = 2\n")
    second = kyo("sweep", "--json")
    assert second.json["flags"] == [], "a re-observed finding is not a new flag"
    assert len(second.json["widened_flags"]) == 1
    widened = second.json["widened_flags"][0]
    assert widened["flag_id"] == flag_id, "the standing flag is the one that widens"
    assert "alpha.py" in widened["detail"] and "beta.py" in widened["detail"]
    assert widened["detail"].startswith(f"changed over {origin[:12]}..")
    assert f"..{second.json['base_sha'][:12]} in 2 sweeps" in widened["detail"]
    assert len(_open_touch_changed(kyo, record)) == 1

    human = kyo("sweep")
    assert "widened" in human.out


def test_a_flag_this_build_cannot_parse_is_never_silently_rewritten(
    kyo, project: Path, write_file, db
) -> None:
    """The safe direction when supersession cannot read what is already there.

    Widening a detail this build did not write would destroy a finding nobody has read.
    A second, visible flag is recoverable — a silent overwrite is not — so an unparseable
    open flag is left exactly as it stands and the new observation is filed beside it.
    """
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(
        kyo, project, write_file, paths=["alpha.py", "beta.py"], name="unreadable"
    )
    git_init(project, commit_message="baseline")
    kyo("sweep")
    _commit_change(project, "alpha.py", "x = 2\n")
    kyo("sweep")

    open_flags = _open_touch_changed(kyo, record)
    assert len(open_flags) == 1
    hand_written = "someone edited this by hand and did not use the range form"
    with db() as conn:
        conn.execute(
            "UPDATE flags SET detail = ? WHERE id = ?", (hand_written, open_flags[0]["id"])
        )

    _commit_change(project, "beta.py", "x = 2\n")
    swept = kyo("sweep", "--json")
    assert len(swept.json["flags"]) == 1, "the new observation is filed, not merged away"
    assert swept.json["widened_flags"] == []

    after = _open_touch_changed(kyo, record)
    assert len(after) == 2
    assert hand_written in [f["detail"] for f in after], "the unreadable flag is untouched"


# ------------------------------------------------- KYO-69.c3: the collapse migration


def _accumulate_pre_fix_flags(
    kyo, project: Path, monkeypatch, *, files: list[str]
) -> None:
    """Reproduce the pre-KYO-69 store state: one new flag per sweep, per record.

    Rather than freezing a hand-written fixture of what the old build wrote, this drives
    the real sweep with supersession's *reader* disabled — which is exactly the
    "existing flag I cannot parse" branch, and therefore the pre-fix behaviour (file
    another one) through the current code path, allocations and exports included.
    """
    from kyosei import sweep as sweep_module

    monkeypatch.setattr(sweep_module, "parse_touch_changed_detail", lambda detail: None)
    for i, name in enumerate(files):
        _commit_change(project, name, f"x = {i + 2}\n")
        assert kyo("sweep", "--json").code == 0
    monkeypatch.undo()


def test_collapse_is_a_dry_run_until_apply(kyo, project: Path, write_file, monkeypatch) -> None:
    """KYO-69.c3: the migration reports what it would do and writes nothing by default.

    A migration whose rehearsal and whose real run look alike is one people run twice
    believing the first was the rehearsal — and this one runs against a store that is
    being swept from a session hook while it runs.
    """
    files = ["alpha.py", "beta.py", "gamma.py"]
    for name in files:
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=files, name="collapse-me")
    git_init(project, commit_message="baseline")
    kyo("sweep")
    _accumulate_pre_fix_flags(kyo, project, monkeypatch, files=files)
    assert len(_open_touch_changed(kyo, record)) == 3, "pre-fix state not reproduced"

    dry = kyo("flag", "collapse", "--json")
    assert dry.code == 0, dry
    assert dry.json["applied"] is False
    assert dry.json["unresolved_before"] == 3
    assert dry.json["unresolved_after"] == 1
    assert dry.json["flags_superseded"] == 2
    assert len(dry.json["plan"]) == 1
    plan = dry.json["plan"][0]
    assert plan["record_id"] == record
    assert plan["survivor_flag_id"] == f"{record}.f1"
    assert plan["superseded_flag_ids"] == [f"{record}.f2", f"{record}.f3"]

    assert len(_open_touch_changed(kyo, record)) == 3, "a dry run must write nothing"
    assert "dry run" in kyo("flag", "collapse").out


def test_collapse_merges_the_duplicates_into_one_finding_and_is_idempotent(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-69.c3: the duplicates are collapsed, not bulk-resolved, and running it twice
    is a no-op — the store it runs against is being actively swept."""
    files = ["alpha.py", "beta.py", "gamma.py"]
    for name in files:
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=files, name="collapse-me")
    git_init(project, commit_message="baseline")
    origin = kyo("sweep", "--json").json["base_sha"]
    _accumulate_pre_fix_flags(kyo, project, monkeypatch, files=files)

    applied = kyo("flag", "collapse", "--apply", "--json")
    assert applied.code == 0, applied
    assert applied.json["applied"] is True
    assert applied.json["unresolved_before"] == 3
    assert applied.json["unresolved_after"] == 1

    survivors = _open_touch_changed(kyo, record)
    assert len(survivors) == 1
    survivor = survivors[0]
    assert survivor["id"] == f"{record}.f1"
    # Nothing any of the three said was discarded: widest window, every file, all sweeps.
    assert survivor["detail"].startswith(f"changed over {origin[:12]}..")
    assert "in 3 sweeps" in survivor["detail"]
    for name in files:
        assert name in survivor["detail"]

    # The duplicates are CLOSED AS SUPERSEDED, which is not the same act as a human
    # judging and dismissing them — the reason says so, and names where the finding went.
    shown = kyo("show", record, "--json").json["flags"]
    closed = [f for f in shown if f["resolved_at"] is not None]
    assert len(closed) == 2
    for flag in closed:
        assert flag["resolved_by"], "law 3: resolved_at and resolved_by move together"
        assert flag["resolved_why"].startswith(f"superseded by {record}.f1")
        assert "NOT judged and dismissed" in flag["resolved_why"]

    assert kyo("doctor").code == 0, "the collapsed store still passes its own invariants"

    again = kyo("flag", "collapse", "--apply", "--json")
    assert again.json["plan"] == []
    assert again.json["flags_superseded"] == 0
    assert again.json["unresolved_before"] == 1
    assert again.json["unresolved_after"] == 1
    assert _open_touch_changed(kyo, record)[0]["detail"] == survivor["detail"]


def test_collapse_refuses_a_flag_id(kyo) -> None:
    """`kyo flag collapse` operates on the whole store; a flag id would read as
    "collapse just this one", which is not a thing it can do."""
    result = kyo("flag", "collapse", "TST-1.f1")
    assert result.code != 0
    assert "takes no flag id" in result.err


# ------------------------------------------- KYO-346: flags settle with their own record

#: Every class a completion or retirement move closes (KYO-346.c1) plus the two it
#: deliberately leaves open (KYO-346.c2) — every test below seeds all five on one
#: record so a test that only checked the three could not pass by accident.
_ALL_FLAG_TYPES = (
    "touch-changed",
    "touch-missing",
    "description-predates-claim",
    "near-duplicate-criteria",
    "gate-cleared-stale-criteria",
)
_STALENESS_FLAG_TYPES = (
    "touch-changed",
    "touch-missing",
    "description-predates-claim",
)


def _seed_every_flag_type(db, record_id: str) -> dict[str, str]:
    """Insert one open flag of each class in :data:`_ALL_FLAG_TYPES` directly — the
    generating conditions for `near-duplicate-criteria` and
    `gate-cleared-stale-criteria` are their own fixtures elsewhere in this suite;
    what KYO-346 is about is what happens to a flag once it exists, not how `kyo
    sweep`/`kyo ungate` raise one, so seeding it directly is the more direct test."""
    ids: dict[str, str] = {}
    with db() as conn:
        for seq, flag_type in enumerate(_ALL_FLAG_TYPES, start=1):
            flag_id = f"{record_id}.f{seq}"
            conn.execute(
                "INSERT INTO flags (id, record_id, seq, flag_type, detail,"
                " other_record_id, other_criterion_id, sweep_run_id, created_at,"
                " resolved_at, resolved_by, resolved_why) VALUES"
                " (?, ?, ?, ?, 'seeded for KYO-346 tests', NULL, NULL, NULL,"
                " '2026-09-01T00:00:00.000Z', NULL, NULL, NULL)",
                (flag_id, record_id, seq, flag_type),
            )
            ids[flag_type] = flag_id
    return ids


def _flags_by_id(kyo, record_id: str) -> dict[str, dict]:
    return {f["id"]: f for f in kyo("show", record_id, "--json").json["flags"]}


def _seed_staleness_flags(db, record_id: str) -> dict[str, str]:
    """Only the three classes :func:`_seed_every_flag_type` also seeds — for a test
    whose fixture must not carry a `near-duplicate-criteria` /
    `gate-cleared-stale-criteria` residue (KYO-346.c2 leaves those open regardless
    of the record's status, so a fixture built to prove c4's exact count needs none
    of them under its settled record)."""
    ids: dict[str, str] = {}
    with db() as conn:
        for seq, flag_type in enumerate(_STALENESS_FLAG_TYPES, start=1):
            flag_id = f"{record_id}.f{seq}"
            conn.execute(
                "INSERT INTO flags (id, record_id, seq, flag_type, detail,"
                " other_record_id, other_criterion_id, sweep_run_id, created_at,"
                " resolved_at, resolved_by, resolved_why) VALUES"
                " (?, ?, ?, ?, 'seeded for KYO-346 tests', NULL, NULL, NULL,"
                " '2026-09-01T00:00:00.000Z', NULL, NULL, NULL)",
                (flag_id, record_id, seq, flag_type),
            )
            ids[flag_type] = flag_id
    return ids


def test_the_completion_move_closes_only_the_three_staleness_flag_types(kyo, db) -> None:
    """KYO-346.c1/c2. Moving a record to its kind's completion status resolves its
    open touch-changed, touch-missing and description-predates-claim flags, each
    with a reason naming the move and the actor — and leaves
    near-duplicate-criteria and gate-cleared-stale-criteria open, on purpose."""
    record = new_record(kyo, criteria=["ships a fix"])

    verify_criterion(kyo, f"{record}.c1", "met")
    ids = _seed_every_flag_type(db, record)

    moved = kyo("move", record, "done", "--json")
    assert moved.code == 0, moved
    assert moved.json["flags_closed"] == 3
    assert {f["flag_id"] for f in moved.json["closed_flags"]} == {
        ids[t] for t in _STALENESS_FLAG_TYPES
    }

    flags = _flags_by_id(kyo, record)
    for flag_type in _STALENESS_FLAG_TYPES:
        flag = flags[ids[flag_type]]
        assert flag["resolved_at"] is not None, flag_type
        assert flag["resolved_by"] == "orchestrator", flag_type
        assert record in flag["resolved_why"]
        assert "done" in flag["resolved_why"]
        assert "orchestrator" in flag["resolved_why"]
        assert "KYO-346" in flag["resolved_why"]
    for flag_type in ("near-duplicate-criteria", "gate-cleared-stale-criteria"):
        flag = flags[ids[flag_type]]
        assert flag["resolved_at"] is None, flag_type


def test_the_completion_move_prints_how_many_flags_it_closed(kyo, db) -> None:
    """KYO-346.c1: 'the command's output says how many it closed' — the human
    rendering, not only the JSON payload."""
    record = new_record(kyo, criteria=["ships a fix"])

    verify_criterion(kyo, f"{record}.c1", "met")
    _seed_every_flag_type(db, record)

    moved = kyo("move", record, "done")
    assert moved.code == 0, moved
    assert "closed 3" in moved.out


def test_wont_do_closes_open_staleness_flags(kyo, db) -> None:
    """KYO-346.c1: `kyo wont-do` is the other move that settles a record."""
    record = new_record(kyo, criteria=["ships a fix"])
    ids = _seed_every_flag_type(db, record)

    moved = kyo("wont-do", record, "--why", "superseded", "--json")
    assert moved.code == 0, moved
    assert moved.json["flags_closed"] == 3

    flags = _flags_by_id(kyo, record)
    for flag_type in _STALENESS_FLAG_TYPES:
        assert flags[ids[flag_type]]["resolved_at"] is not None, flag_type
    for flag_type in ("near-duplicate-criteria", "gate-cleared-stale-criteria"):
        assert flags[ids[flag_type]]["resolved_at"] is None, flag_type


def test_void_closes_open_staleness_flags_on_a_knowledge_record(kyo, db, project: Path) -> None:
    """KYO-346.c1: `kyo void` is the knowledge archetype's own settling move. `kyo
    sweep` never raises a staleness flag on a knowledge record (archetype: work is
    the whole of its corpus), so this seeds one directly — the mechanism has to hold
    regardless of how a flag arrived on the record, not only for the shape `kyo
    sweep` itself produces."""

    record = new_record(kyo, kind="decision", title="a decision")
    ids = _seed_every_flag_type(db, record)

    voided = kyo("void", record, "--why", "filed in error", "--json")
    assert voided.code == 0, voided
    assert voided.json["flags_closed"] == 3

    flags = _flags_by_id(kyo, record)
    for flag_type in _STALENESS_FLAG_TYPES:
        assert flags[ids[flag_type]]["resolved_at"] is not None, flag_type
    for flag_type in ("near-duplicate-criteria", "gate-cleared-stale-criteria"):
        assert flags[ids[flag_type]]["resolved_at"] is None, flag_type


def test_a_non_settling_move_leaves_flags_open(kyo, db) -> None:
    """The gate on :data:`contracts.SETTLED_ROLES`: `open -> working` is a real
    transition through `_transition`, and it must not incidentally close anything —
    only a completion or retired status does."""
    record = new_record(kyo, criteria=["ships a fix"])
    ids = _seed_every_flag_type(db, record)

    moved = kyo("move", record, "working", "--json")
    assert moved.code == 0, moved
    assert moved.json.get("flags_closed", 0) == 0

    flags = _flags_by_id(kyo, record)
    for flag_id in ids.values():
        assert flags[flag_id]["resolved_at"] is None


# ------------------------------------- KYO-346.c3: the repair for accumulated debt


def test_flag_resolve_settled_requires_a_reason(kyo, db) -> None:
    record = new_record(kyo, criteria=["ships a fix"])

    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "done").code == 0
    _seed_every_flag_type(db, record)

    result = kyo("flag", "resolve", "--settled")
    assert result.code != 0
    assert "needs a reason" in result.err


def test_flag_resolve_settled_refuses_a_flag_id(kyo) -> None:
    result = kyo("flag", "resolve", "TST-1.f1", "--settled", "--why", "repair")
    assert result.code != 0
    assert "takes no flag id" in result.err


def test_flag_resolve_settled_is_a_dry_run_until_apply(kyo, db) -> None:
    """KYO-346.c3, on `kyo flag collapse`'s own terms: the plan is printed, nothing
    is written without --apply, and the before/after counts are per record and per
    type."""
    record = new_record(kyo, criteria=["ships a fix"])

    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo("move", record, "done").code == 0
    # Debt accumulated the way it did in the wild: seeded AFTER the record had
    # already settled, so the completion move's own settlement (KYO-346.c1) never
    # had a chance to close these — exactly what the repair exists for.
    ids = _seed_every_flag_type(db, record)

    dry = kyo("flag", "resolve", "--settled", "--why", "repairing accumulated debt", "--json")
    assert dry.code == 0, dry
    assert dry.json["applied"] is False
    assert dry.json["records_affected"] == 1
    assert dry.json["flags_closed"] == 3
    assert dry.json["by_type"] == dict.fromkeys(_STALENESS_FLAG_TYPES, 1)
    assert dry.json["plan"][0]["record_id"] == record

    flags = _flags_by_id(kyo, record)
    for flag_type in _STALENESS_FLAG_TYPES:
        assert flags[ids[flag_type]]["resolved_at"] is None, "a dry run must write nothing"
    assert "dry run" in kyo("flag", "resolve", "--settled", "--why", "x").out


def test_flag_resolve_settled_apply_closes_per_record_and_per_type(kyo, db) -> None:
    record_a = new_record(kyo, criteria=["ships a fix"])
    record_b = new_record(kyo, criteria=["ships another fix"])

    for record in (record_a, record_b):
        verify_criterion(kyo, f"{record}.c1", "met")
        assert kyo("move", record, "done").code == 0
    ids_a = _seed_every_flag_type(db, record_a)
    ids_b = _seed_every_flag_type(db, record_b)
    # A third record, still open: its flags must not be touched by this repair even
    # though it also carries every type.
    still_open = new_record(kyo, criteria=["not finished"])
    ids_open = _seed_every_flag_type(db, still_open)

    before = len(kyo("brief", "--json").json["unresolved_flags"])
    applied = kyo(
        "flag", "resolve", "--settled", "--apply", "--why", "repairing accumulated debt",
        "--json",
    )  # fmt: skip
    assert applied.code == 0, applied
    assert applied.json["applied"] is True
    assert applied.json["records_affected"] == 2
    assert applied.json["flags_closed"] == 6
    assert applied.json["by_type"] == dict.fromkeys(_STALENESS_FLAG_TYPES, 2)

    for record, ids in ((record_a, ids_a), (record_b, ids_b)):
        flags = _flags_by_id(kyo, record)
        for flag_type in _STALENESS_FLAG_TYPES:
            flag = flags[ids[flag_type]]
            assert flag["resolved_at"] is not None, (record, flag_type)
            assert flag["resolved_by"] == "orchestrator"
            assert flag["resolved_why"] == "repairing accumulated debt"
        for flag_type in ("near-duplicate-criteria", "gate-cleared-stale-criteria"):
            assert flags[ids[flag_type]]["resolved_at"] is None, (record, flag_type)

    still_open_flags = _flags_by_id(kyo, still_open)
    for flag_id in ids_open.values():
        assert still_open_flags[flag_id]["resolved_at"] is None, "an open record is untouched"

    after = len(kyo("brief", "--json").json["unresolved_flags"])
    assert after == before - 6

    # Idempotent, exactly like `kyo flag collapse`: nothing left to repair.
    again = kyo(
        "flag", "resolve", "--settled", "--apply", "--why", "repairing accumulated debt",
        "--json",
    )  # fmt: skip
    assert again.json["records_affected"] == 0
    assert again.json["flags_closed"] == 0


def test_after_the_settled_repair_briefs_unresolved_count_matches_open_records(
    kyo, db
) -> None:
    """KYO-346.c4: after the repair runs in a fixture store, `kyo brief`'s
    unresolved-flag count equals the number of flags under records that are still
    open.

    Seeds only staleness-class flags on the settled record: c2's other two classes
    (near-duplicate-criteria, gate-cleared-stale-criteria) stay open under a settled
    record on purpose, so a fixture that carried one would make brief's count come
    out higher than "flags under records still open" — a fact about c2, not a
    failure of this repair, and not what this criterion is measuring. Covered on its
    own terms by the tests above.
    """
    settled = new_record(kyo, criteria=["ships a fix"])
    verify_criterion(kyo, f"{settled}.c1", "met")
    assert kyo("move", settled, "done").code == 0
    _seed_staleness_flags(db, settled)  # accumulated debt, as above

    still_open = new_record(kyo, criteria=["not finished"])
    open_ids = _seed_every_flag_type(db, still_open)

    applied = kyo(
        "flag", "resolve", "--settled", "--apply", "--why", "repairing accumulated debt",
        "--json",
    )  # fmt: skip
    assert applied.code == 0, applied

    brief = kyo("brief", "--json").json
    assert len(brief["unresolved_flags"]) == len(open_ids)


# -------------------------------------------- KYO-69: the detail format, read and written


def test_the_touch_changed_detail_round_trips() -> None:
    """`format_touch_changed_detail` and `parse_touch_changed_detail` are an exact pair.

    They are the only two functions that know how a touch-changed finding is written
    down, and supersession depends on the second being able to read whatever the first
    wrote — including the paths, in order, and the sweep count.
    """
    from kyosei import sweep as sweep_module

    for sweeps in (1, 2, 9):
        finding = sweep_module.TouchChangedFinding(
            from_sha="629b06c21f9b1111",
            to_sha="89ab1aae5dc32222",
            paths=("src/kyosei/cli.py", "src/kyosei/schema.py", "tests/test_x.py"),
            sweeps=sweeps,
        )
        text = sweep_module.format_touch_changed_detail(finding)
        parsed = sweep_module.parse_touch_changed_detail(text)
        assert parsed is not None
        assert parsed.paths == finding.paths
        assert parsed.sweeps == sweeps
        assert parsed.from_sha == finding.from_sha[:12]
        assert parsed.to_sha == finding.to_sha[:12]


def test_the_pre_kyo69_detail_form_is_still_readable() -> None:
    """Flags written before KYO-69 are all in the old form, and the collapse migration
    has to be able to read them or it cannot merge them."""
    from kyosei import sweep as sweep_module

    legacy = (
        "changed since last sweep (629b06c21f9b..de99fc3c3953): "
        "src/kyosei/cli.py, src/kyosei/commands.py"
    )
    parsed = sweep_module.parse_touch_changed_detail(legacy)
    assert parsed is not None
    assert parsed.from_sha == "629b06c21f9b"
    assert parsed.to_sha == "de99fc3c3953"
    assert parsed.paths == ("src/kyosei/cli.py", "src/kyosei/commands.py")
    assert parsed.sweeps == 1


def test_an_unrecognised_detail_parses_to_none_rather_than_guessing() -> None:
    from kyosei import sweep as sweep_module

    for text in ("", "a hand-written note", "changed over zzzz..yyyy in 2 sweeps: "):
        assert sweep_module.parse_touch_changed_detail(text) is None


def test_merging_findings_never_narrows_the_window_or_drops_a_path() -> None:
    """The property that makes closing the duplicates honest rather than a bulk resolve.

    KYO-60's nine flags each named a different sha range and an overlapping file subset;
    the surviving flag has to say what all nine said.
    """
    from kyosei import sweep as sweep_module

    findings = [
        sweep_module.TouchChangedFinding("aaa1", "bbb2", ("cli.py", "commands.py"), 1),
        sweep_module.TouchChangedFinding("bbb2", "ccc3", ("cli.py", "schema.py"), 1),
        sweep_module.TouchChangedFinding("ddd4", "eee5", ("tests/test_x.py",), 1),
    ]
    merged = sweep_module.merge_touch_changed_findings(findings)
    assert merged.from_sha == "aaa1"
    assert merged.to_sha == "eee5"
    assert merged.sweeps == 3
    assert set(merged.paths) == {"cli.py", "commands.py", "schema.py", "tests/test_x.py"}


# ------------------------------------------------------------------------ KYO-220
#
# A consumer report: flags had no read path, since `kyo flag` only resolved or
# collapsed, and reading what APP-58.f2 said meant parsing `kyo brief --json`. Its
# companion item: `kyo show` rejected criterion, evidence and flag ids but accepted
# shift-note ids, so the id shapes did not tell a reader what was showable.


def _a_touch_changed_flag(kyo, project: Path, write_file) -> tuple[str, str]:
    """A store with exactly one open touch-changed flag. Returns (record, flag id)."""
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    record = _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    git_init(project, commit_message="baseline")
    assert kyo("sweep", "--json").json["flags"] == []
    target.write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "change widget"],
        cwd=project, check=True, capture_output=True,
    )
    swept = kyo("sweep", "--json")
    assert len(swept.json["flags"]) == 1, swept
    flags = kyo("show", record, "--json").json["flags"]
    assert len(flags) == 1, flags
    return record, flags[0]["id"]


def test_show_prints_one_flag_in_full(kyo, project: Path, write_file) -> None:
    """c1. Every fact the consumer went to `kyo brief --json` for: type, record, state,
    the sha range and the paths, when it was raised and by which sweep."""
    record, flag_id = _a_touch_changed_flag(kyo, project, write_file)

    shown = kyo("show", flag_id)
    assert shown.code == 0, shown
    assert f"flag {flag_id}  [open]  touch-changed on {record}" in shown.out
    assert "stored sha range: " in shown.out
    assert "widget.py" in shown.out
    assert "stored raised: " in shown.out
    sweep_run = kyo("show", flag_id, "--json").json["sweep_run_id"]
    assert sweep_run and sweep_run in shown.out, "the sweep that raised it is named"
    # No `kyo brief --json` in the loop: the whole flag is in this one document.
    assert kyo("show", flag_id, "--json").json["object"] == "flag"


def test_show_prints_a_resolved_flags_resolution_and_why(
    kyo, project: Path, write_file
) -> None:
    """c1's second half: a resolved flag shows the resolution and the --why with it."""
    _, flag_id = _a_touch_changed_flag(kyo, project, write_file)
    why = "read the diff; the touch list is still right and the change is unrelated"
    assert kyo("flag", "resolve", flag_id, "--why", why).code == 0

    shown = kyo("show", flag_id)
    assert f"flag {flag_id}  [resolved]" in shown.out
    assert "stored resolved: " in shown.out
    assert why in shown.out


def test_flag_list_lists_open_flags_and_all_includes_resolved(
    kyo, project: Path, write_file
) -> None:
    """c1. One line each — id, type, record, state — without parsing a brief."""
    record, flag_id = _a_touch_changed_flag(kyo, project, write_file)

    listed = kyo("flag", "list")
    assert listed.code == 0, listed
    assert flag_id in listed.out and "touch-changed" in listed.out
    assert record in listed.out and "[open]" in listed.out
    assert kyo("flag", "list", "--json").json["open"] == 1

    assert kyo("flag", "resolve", flag_id, "--why", "judged and dismissed").code == 0
    after = kyo("flag", "list")
    assert flag_id not in after.out, "a resolved flag is not what `list` is asking about"
    assert "(none)" in after.out
    every = kyo("flag", "list", "--all")
    assert flag_id in every.out and "[resolved]" in every.out
    assert kyo("flag", "list", "--all", "--json").json["open"] == 0
    assert [f["id"] for f in kyo("flag", "list", "--all", "--json").json["flags"]] == [flag_id]


def test_flag_list_takes_no_flag_id_and_says_what_does(
    kyo, project: Path, write_file
) -> None:
    """A listing handed one id is a reader asking for `kyo show`, and is told so."""
    _, flag_id = _a_touch_changed_flag(kyo, project, write_file)
    refused = kyo("flag", "list", flag_id)
    assert refused.code == 64, refused
    assert f"kyo show {flag_id}" in refused.err


def test_show_prints_a_criterion_with_its_verdict_history(kyo, project: Path) -> None:
    """c2. A criterion's state is DERIVED from its latest verification, so a reading
    that showed only the state would hide the evidence this store actually keeps."""
    record = new_record(kyo, criteria=["the widget turns"])
    criterion_id = kyo("show", record, "--json").json["criteria"][0]["id"]
    assert kyo(
        "verify", criterion_id, "--verdict", "met", "--method", "human-observed"
    ).code == 0

    shown = kyo("show", criterion_id)
    assert shown.code == 0, shown
    assert f"criterion {criterion_id}  [met]  on {record}" in shown.out
    assert "the widget turns" in shown.out
    assert "verifications (1):" in shown.out
    assert "human-observed" in shown.out
    data = kyo("show", criterion_id, "--json").json
    assert data["object"] == "criterion" and data["record_id"] == record
    assert [v["verdict"] for v in data["verifications"]] == ["met"]


def test_show_prints_an_evidence_artifact_and_its_redaction(
    kyo, project: Path, write_file
) -> None:
    """c2's other half, including the case the schema is careful about: after
    `kyo redact` there is no text AND no digest, and the rendering says so."""
    record = new_record(kyo, criteria=["a"])
    attached = kyo(
        "attach", record, "--file", write_file("ev.txt", "the query returned 4 rows\n"),
        "--json",
    )
    assert attached.code == 0, attached
    evidence_id = attached.json["evidence_id"]

    shown = kyo("show", evidence_id)
    assert shown.code == 0, shown
    assert f"evidence {evidence_id}  on {record}" in shown.out
    assert "the query returned 4 rows" in shown.out
    assert "stored sha256: " in shown.out
    assert kyo("show", evidence_id, "--json").json["object"] == "evidence"

    assert kyo(
        "redact", evidence_id, "--why", "it named a live customer id"
    ).code == 0
    redacted = kyo("show", evidence_id)
    assert "stored redacted: " in redacted.out
    assert "it named a live customer id" in redacted.out
    assert "the query returned 4 rows" not in redacted.out


def test_show_refuses_an_unshowable_shape_as_a_shape(kyo, project: Path, write_file) -> None:
    """c3. `no record 'KYO-215.f2'` was true of the record table and useless as an
    answer. A refusal that names the shape it was handed teaches the id space."""
    record = new_record(kyo, criteria=["a"])
    note = kyo("--full", "edit", record, "--note-file", write_file("n.txt", "a note\n"), "--json")
    assert note.code == 0, note
    note_id = note.json["record"]["notes"][-1]["id"]

    refused = kyo("show", note_id)
    assert refused.code == 64, refused
    assert "no record" not in refused.err
    assert f"{note_id!r} is a note id" in refused.err
    # And it names what it does accept, every shape, in one line.
    for shape in ("PREFIX-N.cN", "PREFIX-N.eN", "PREFIX-N.fN", "PREFIX-N.dN", "PREFIX-SN"):
        assert shape in refused.err

    # A shape the project mints that is not a child id at all.
    link_refusal = kyo("show", "TST-L1")
    assert link_refusal.code == 64, link_refusal
    assert "is a link id" in link_refusal.err
    # And something that is no id shape at all.
    nonsense = kyo("show", "widget.py")
    assert nonsense.code == 64, nonsense
    assert "is not an id shape this project mints" in nonsense.err
    # A record id that names nothing keeps its own, different answer (law 6).
    missing = kyo("show", "TST-9999")
    assert missing.code == 1, missing
    assert "no record" in missing.err


def test_show_help_names_every_shape_it_accepts(kyo, project: Path) -> None:
    """c2's last clause. The help, the dispatch and the refusal read one table."""
    from kyosei import cmd_views

    helped = kyo("show", "--help")
    assert helped.code == 0, helped
    for shape, _what in cmd_views.SHOWABLE_SHAPES:
        assert shape in helped.out, shape
