"""KYO-8.c6/c7: kyo brief (the session briefing) and kyo note --shift."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kyosei.brief import enforcement_kind
from kyosei.install_procedures import packaged_procedures_text
from kyosei.install_skill import SKILL_ASSET
from kyosei.procedures import orchestrator_rules, worker_rules

from .conftest import git_init, new_record, valid_report, verify_criterion


def test_brief_counts_records_by_kind(kyo) -> None:
    new_record(kyo, kind="bug", criteria=["a"])
    new_record(kyo, kind="bug", criteria=["b"])
    new_record(kyo, kind="chore", criteria=["c"])
    result = kyo("brief", "--json")
    assert result.code == 0, result
    by_kind = {e["kind"]: e for e in result.json["counts_by_kind"]}
    assert by_kind["bug"]["total"] == 2
    assert by_kind["chore"]["total"] == 1
    assert by_kind["bug"]["counted"] is True


def test_brief_lists_ready_work_matching_kyo_next(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    ready = kyo("next", "--json").json["ready"]
    brief_ready = kyo("brief", "--json").json["ready"]
    assert ready == brief_ready
    assert record in [r["id"] for r in brief_ready]


def test_brief_lists_gated_work_with_its_condition(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-run", "--do", "run the migration, then reload the page")
    result = kyo("brief", "--json")
    gated = result.json["gated"]
    assert len(gated) == 1
    assert gated[0]["record_id"] == record
    assert gated[0]["gate_type"] == "awaiting:human-run"
    assert gated[0]["detail"] == "run the migration, then reload the page"


def test_brief_lists_running_dispatches(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    result = kyo("brief", "--json")
    running = result.json["running_dispatches"]
    assert len(running) == 1
    assert running[0]["id"] == dispatched.json["dispatch_id"]


def test_a_reported_dispatch_drops_out_of_running(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    dispatch_id = dispatched.json["dispatch_id"]
    sha = dispatched.json["worked_from_sha"]
    report_path = git_project / "report.json"
    import json as jsonlib

    report_path.write_text(
        jsonlib.dumps(valid_report(dispatch_id=dispatch_id, record_id=record, worked_from_sha=sha)),
        encoding="utf-8",
    )
    reported = kyo("report", dispatch_id, "--file", str(report_path))
    assert reported.code == 0, reported

    result = kyo("brief", "--json")
    assert result.json["running_dispatches"] == []


def test_brief_lists_terminated_no_report_dispatches(kyo, git_project) -> None:
    """The base case, KYO-314.c1's first of three: an unanswered terminated-no-report
    dispatch (its record still open, no later dispatch reported for it) stays listed
    exactly as before this criterion existed."""
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    dispatch_id = dispatched.json["dispatch_id"]
    kyo("dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "crashed")

    result = kyo("brief", "--json")
    tnr = result.json["terminated_no_report"]
    assert len(tnr) == 1
    assert tnr[0]["id"] == dispatch_id
    assert tnr[0]["terminated_why"] == "crashed"
    assert result.json["terminated_no_report_answered_count"] == 0
    assert "terminated-no-report dispatches (1)" in kyo("brief").out
    assert "terminated-no-report dispatches (1)" in kyo("brief", "--compact").out


def _report_dispatch(kyo, git_project, *, dispatch_id: str, record_id: str, worked_from_sha: str):
    """File a minimal schema-valid report for an already-dispatched id."""
    import json as jsonlib

    report_path = git_project / f"report-{dispatch_id}.json"
    report_path.write_text(
        jsonlib.dumps(
            valid_report(
                dispatch_id=dispatch_id, record_id=record_id, worked_from_sha=worked_from_sha
            )
        ),
        encoding="utf-8",
    )
    result = kyo("report", dispatch_id, "--file", str(report_path))
    assert result.code == 0, result
    return result


def test_terminated_no_report_leaves_the_list_once_its_record_is_settled(
    kyo, git_project
) -> None:
    """KYO-314.c1's second case: the record reaches a settled (completion/retired)
    status -- somebody finished it, however, and the crashed dispatch that never
    reported is no longer a fact anyone needs flagged every session."""
    record = new_record(kyo, kind="bug", criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    kyo(
        "dispatch", "close", dispatched.json["dispatch_id"], "--terminated-no-report",
        "--why", "crashed",
    )
    verify_criterion(kyo, f"{record}.c1")
    for status in ("working", "built", "verified", "done"):
        assert kyo("move", record, status).code == 0, status

    result = kyo("brief", "--json")
    assert result.json["terminated_no_report"] == []
    assert result.json["terminated_no_report_answered_count"] == 1
    out = kyo("brief").out
    assert "terminated-no-report dispatches (0)" in out
    assert "1 more answered" in out


def test_terminated_no_report_leaves_the_list_once_a_later_dispatch_reports(
    kyo, git_project
) -> None:
    """KYO-314.c1's third case: a LATER dispatch on the same record speaks for it and
    reaches `reported` -- the crashed attempt is answered by the one that came after
    it, not by anything the crashed dispatch itself did."""
    record = new_record(kyo, kind="bug", criteria=["a"])
    first = kyo("dispatch", record, "--json")
    kyo(
        "dispatch", "close", first.json["dispatch_id"], "--terminated-no-report",
        "--why", "crashed",
    )

    second = kyo("dispatch", record, "--json")
    _report_dispatch(
        kyo, git_project,
        dispatch_id=second.json["dispatch_id"], record_id=record,
        worked_from_sha=second.json["worked_from_sha"],
    )

    result = kyo("brief", "--json")
    assert result.json["terminated_no_report"] == []
    assert result.json["terminated_no_report_answered_count"] == 1


def test_terminated_no_report_leaves_the_list_when_a_later_dispatch_covers_the_record(
    kyo, git_project
) -> None:
    """'Speaks for the record' includes a dispatch that named it with
    `--also-covers`, the same relation `_reported_dispatches_by_record` derives for
    "finished but not moved" -- reused here rather than re-derived."""
    covered = new_record(kyo, kind="bug", criteria=["a"])
    first = kyo("dispatch", covered, "--json")
    kyo(
        "dispatch", "close", first.json["dispatch_id"], "--terminated-no-report",
        "--why", "crashed",
    )

    primary = new_record(kyo, kind="bug", criteria=["b"])
    covering = kyo("dispatch", primary, "--also-covers", covered, "--json")
    _report_dispatch(
        kyo, git_project,
        dispatch_id=covering.json["dispatch_id"], record_id=primary,
        worked_from_sha=covering.json["worked_from_sha"],
    )

    result = kyo("brief", "--json")
    assert result.json["terminated_no_report"] == []
    assert result.json["terminated_no_report_answered_count"] == 1


def test_terminated_no_report_stays_listed_when_the_report_predates_it(
    kyo, git_project
) -> None:
    """The word in the criterion is LATER, and this is why: a report that predates
    the crashed dispatch (the record was reported once, left open, and re-dispatched
    -- and THAT attempt is the one that crashed) must not silently answer it."""
    record = new_record(kyo, kind="bug", criteria=["a", "b"])
    first = kyo("dispatch", record, "--json")
    _report_dispatch(
        kyo, git_project,
        dispatch_id=first.json["dispatch_id"], record_id=record,
        worked_from_sha=first.json["worked_from_sha"],
    )
    # `a` and `b` are both still open -- the record is not settled by this report.

    second = kyo("dispatch", record, "--json")
    kyo(
        "dispatch", "close", second.json["dispatch_id"], "--terminated-no-report",
        "--why", "crashed after the report",
    )

    result = kyo("brief", "--json")
    tnr = result.json["terminated_no_report"]
    assert [d["id"] for d in tnr] == [second.json["dispatch_id"]]
    assert result.json["terminated_no_report_answered_count"] == 0


def test_brief_lists_unresolved_sweep_flags(kyo, project: Path, write_file) -> None:
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    idea = project / "ideas" / "a.md"
    idea.write_text("# A\n\n\n", encoding="utf-8")
    rec = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("a-criteria.txt", "x\n"),
        "--touches-file", write_file("a-touches.txt", "widget.py\n"),
        "--json",
    )
    record = rec.json["record"]["id"]
    target.unlink()
    swept = kyo("sweep", "--json")
    flag_id = swept.json["flags"][0]["flag_id"]

    before_resolve = kyo("brief", "--json")
    assert flag_id in [f["id"] for f in before_resolve.json["unresolved_flags"]]

    kyo("flag", "resolve", flag_id)
    after_resolve = kyo("brief", "--json")
    assert flag_id not in [f["id"] for f in after_resolve.json["unresolved_flags"]]
    assert record  # keep the record id referenced for clarity


def test_note_shift_requires_the_flag(kyo) -> None:
    result = kyo("note", "--file", "-", stdin="a thought")
    assert result.code == 64  # usage error: --shift is required


def test_note_shift_appends_and_echoes_back(kyo) -> None:
    result = kyo(
        "note", "--shift", "--file", "-", stdin="pick up the flaky test before anything else"
    )
    assert result.code == 0, result
    assert "pick up the flaky test" in result.out


def test_brief_surfaces_shift_notes_since_the_previous_brief(kyo) -> None:
    kyo("note", "--shift", "--file", "-", stdin="first thought")
    first_brief = kyo("brief", "--json")
    assert [n["text"] for n in first_brief.json["shift_notes"]] == ["first thought"]

    # A second brief immediately after sees nothing new.
    second_brief = kyo("brief", "--json")
    assert second_brief.json["shift_notes"] == []

    kyo("note", "--shift", "--file", "-", stdin="second thought")
    third_brief = kyo("brief", "--json")
    assert [n["text"] for n in third_brief.json["shift_notes"]] == ["second thought"]


def test_brief_deltas_since_previous_brief(kyo) -> None:
    first = kyo("brief", "--json")
    assert first.json["previous_brief_at"] is None
    events_before_any_record = len(first.json["deltas"])

    new_record(kyo, criteria=["a"])
    second = kyo("brief", "--json")
    assert second.json["previous_brief_at"] is not None
    # exactly the events since the first brief: the new record's creation event
    # (plus the first brief's own event, which the first brief could not have
    # seen about itself).
    assert len(second.json["deltas"]) >= 1
    assert any(d["command"] == "new bug" for d in second.json["deltas"])
    assert events_before_any_record == 0


def test_brief_compact_is_shorter_but_carries_the_same_counts(kyo) -> None:
    new_record(kyo, criteria=["a"])
    full = kyo("brief")
    compact = kyo("brief", "--compact")
    assert compact.code == 0
    assert len("\n".join(compact.out.splitlines())) <= len("\n".join(full.out.splitlines()))


def test_brief_writes_a_ledger_entry(kyo, project: Path) -> None:
    kyo("brief")
    ledger = project / "kyosei" / "export" / "_briefs.jsonl"
    assert ledger.is_file()
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_doctor_passes_after_brief_and_shift_notes(kyo) -> None:
    new_record(kyo, criteria=["a"])
    kyo("note", "--shift", "--file", "-", stdin="handoff")
    kyo("brief")
    result = kyo("doctor", "--json")
    assert result.code == 0, result.json


# ------------------------ KYO-69.c2: whose ground moved, and which half to read first


def _promote_touching(kyo, project: Path, write_file, *, path: str, name: str) -> str:
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDo the thing.\n", encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "bug",
        "--criteria-file",
        write_file(f"{name}-criteria.txt", "it reproduces\n"),
        "--touches-file",
        write_file(f"{name}-touches.txt", f"{path}\n"),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_the_brief_names_both_touch_changed_scopes_even_at_zero(kyo, git_project) -> None:
    """KYO-48's reasoning, applied to KYO-69's split: a heading that appears only when
    it is occupied teaches a reader that its absence means nothing was checked."""
    kyo("sweep")
    human = kyo("brief")
    assert "staleness under work that was actually done (dispatched or reported): 0" in human.out
    assert "drift under records that have merely sat open (never dispatched): 0" in human.out


def test_the_brief_ranks_staleness_under_real_work_above_drift_under_an_open_record(
    kyo, project: Path, write_file
) -> None:
    """KYO-69.c2, the decision this build made: `touch-changed` still fires for a record
    with no dispatch history, but the brief no longer presents the two as one list.

    "The ground moved under work that was actually done" is a staleness warning. "Files
    this record names have been changing while it sat open" is a description of a
    codebase being worked on, which is a codebase's normal state — and in this repository
    it once accounted for every unresolved flag.
    """
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    worked = _promote_touching(kyo, project, write_file, path="alpha.py", name="worked-on")
    open_only = _promote_touching(kyo, project, write_file, path="beta.py", name="just-open")
    git_init(project, commit_message="baseline")
    kyo("sweep")

    dispatched = kyo("dispatch", worked, "--json")
    assert dispatched.code == 0, dispatched

    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "move both"], cwd=project, check=True, capture_output=True
    )
    assert kyo("sweep").code == 0

    result = kyo("brief", "--json")
    assert result.code == 0, result
    flags = result.json["unresolved_flags"]
    scopes = {f["record_id"]: f["touch_changed_scope"] for f in flags}
    assert scopes[worked] == "worked"
    assert scopes[open_only] == "open-only"
    # Ranked, not merely labelled: the half worth reading first is printed first.
    assert [f["record_id"] for f in flags] == [worked, open_only]

    human = kyo("brief")
    assert "staleness under work that was actually done (dispatched or reported): 1" in human.out
    assert "drift under records that have merely sat open (never dispatched): 1" in human.out
    # KYO-222: one commit moved both files, so the two flags share a range and read as
    # one finding. The ranking survives inside it — the worked member is named first —
    # and the line states the split rather than losing it to the grouping.
    assert human.out.index(f"{worked}.f1") < human.out.index(f"{open_only}.f1")
    assert "[1 worked, 1 open-only]" in human.out


# --------------------- KYO-213: this build vs this project's packaged copies


def _drop_rule(text: str, title: str) -> str:
    """``text`` with the ``### {title}`` rule section cut out, up to the next rule
    heading or section boundary — whichever comes first.

    A small, self-contained stand-in for ``tests/test_procedures.py``'s own
    ``_without_rule`` (not imported from there on purpose: this file's job is only to
    drive `kyo brief`, and a bug in a shared helper should not be able to hide behind
    two callers agreeing with each other)."""
    start = text.index(f"### {title}")
    rest = text[start:]
    boundaries = [
        i
        for i in (rest.find("\n### ", 1), rest.find("\n## ", 1), rest.find("\n---\n", 1))
        if i != -1
    ]
    end = start + (min(boundaries) + 1 if boundaries else len(rest))
    result = text[:start] + text[end:]
    assert f"### {title}" not in result
    return result


def test_a_project_with_no_drift_prints_no_block(kyo, project: Path) -> None:
    """Both copies in this repository are identical and its own ledger already has
    every packaged rule (the record's own premise) — a freshly scaffolded project
    with the skill installed is the same shape, so the block is silent."""
    kyo("install-skill", "--copies")
    result = kyo("brief", "--json")
    assert result.json["asset_drift"] == []
    assert "this build vs this project's copies" not in kyo("brief").out
    assert "this build vs this project's copies" not in kyo("brief", "--compact").out


def test_brief_names_a_stale_skill_and_the_block_clears_after_install(
    kyo, project: Path
) -> None:
    """KYO-213.c3: upgrade the packaged skill under a project by one line, see the
    block naming `kyo install-skill` in the next brief, run it, see the block gone."""
    kyo("install-skill", "--copies")
    skill_path = project / SKILL_ASSET.target
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8") + "\nOne stale extra line.\n",
        encoding="utf-8",
    )

    drifted = kyo("brief", "--json")
    findings = drifted.json["asset_drift"]
    assert any(f["asset"] == "skill" for f in findings)
    assert any(f["remedy"] == "kyo install-skill" for f in findings)

    human = kyo("brief")
    assert "this build vs this project's copies" in human.out
    assert "kyo install-skill" in human.out
    # compact and full alike (KYO-213.c1) — nothing here is elided under --compact.
    assert "kyo install-skill" in kyo("brief", "--compact").out

    assert kyo("install-skill", "--copies").code == 0
    cleared = kyo("brief", "--json")
    assert cleared.json["asset_drift"] == []
    assert "this build vs this project's copies" not in kyo("brief").out


def test_brief_names_a_missing_procedures_rule_and_the_block_clears_after_install(
    kyo, project: Path
) -> None:
    """KYO-213.c3, the ledger shape: a packaged rule missing from the store copy
    names `kyo install-procedures`, and running it clears the block."""
    ledger = project / "kyosei" / "procedures.md"
    title = worker_rules(packaged_procedures_text())[0].title
    ledger.write_text(
        _drop_rule(ledger.read_text(encoding="utf-8"), title), encoding="utf-8"
    )

    drifted = kyo("brief", "--json")
    findings = drifted.json["asset_drift"]
    matching = [f for f in findings if f["asset"] == "procedures" and title in f["detail"]]
    assert matching, findings
    assert matching[0]["remedy"] == "kyo install-procedures"

    human = kyo("brief")
    assert "this build vs this project's copies" in human.out
    assert title in human.out
    assert "kyo install-procedures" in human.out

    assert kyo("install-procedures").code == 0
    cleared = kyo("brief", "--json")
    assert not any(
        f["asset"] == "procedures" and title in f["detail"] for f in cleared.json["asset_drift"]
    )


def test_a_reworded_procedures_rule_names_no_command(kyo, project: Path) -> None:
    """KYO-213's task section 2: a rule both copies carry, worded differently, gets no
    remedy command — only its title and the pointer to `kyo doctor`."""
    ledger = project / "kyosei" / "procedures.md"
    title = worker_rules(packaged_procedures_text())[0].title
    original = ledger.read_text(encoding="utf-8")
    # Reword the BODY, not the heading — the comparison is keyed on the ``###``
    # title (KYO-95/KYO-212), so changing the title would make this a *missing*
    # rule (a different title the packaged copy doesn't recognise) rather than the
    # same rule worded differently.
    heading = f"### {title}\n"
    insert_at = original.index(heading) + len(heading)
    reworded = (
        original[:insert_at]
        + "\nThis project reworded this rule on purpose, for this test.\n"
        + original[insert_at:]
    )
    assert reworded != original
    ledger.write_text(reworded, encoding="utf-8")

    result = kyo("brief", "--json")
    matching = [f for f in result.json["asset_drift"] if f["asset"] == "procedures"]
    assert matching, result.json["asset_drift"]
    assert all(f["remedy"] is None for f in matching)
    assert any("kyo doctor" in f["detail"] for f in matching)

    human = kyo("brief")
    assert "this build vs this project's copies" in human.out
    assert "kyo install-procedures" not in human.out
    assert "kyo doctor" in human.out


# ------------------------------------------------------------------------ KYO-222
#
# A consumer report: the brief's 'unresolved sweep flags' ranked four flags as staleness
# under worked records when all four were raised by one commit that appended to shared
# files. A sweep that groups flags by the commit range that raised them shows that in one
# line.


def _commit(project: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=project, check=True, capture_output=True
    )


def test_flags_from_one_commit_range_read_as_one_finding(
    kyo, project: Path, write_file
) -> None:
    """KYO-222.c1/c3. Two records' flags share a range because one commit touched both
    of their declared files; a third record's flag was raised by a later window and has
    its own. The shared range is one line; the other is still its own."""
    for name in ("alpha.py", "beta.py", "gamma.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    together = [
        _promote_touching(kyo, project, write_file, path="alpha.py", name="shared-a"),
        _promote_touching(kyo, project, write_file, path="beta.py", name="shared-b"),
    ]
    alone = _promote_touching(kyo, project, write_file, path="gamma.py", name="on-its-own")
    git_init(project, commit_message="baseline")
    assert kyo("sweep", "--json").json["flags"] == [], "first sweep records a baseline"

    # One commit that appends to both shared files: two records, one event.
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 2\n", encoding="utf-8")
    _commit(project, "one commit, two records' files")
    assert kyo("sweep").code == 0

    # A later window, a different file, a different range.
    (project / "gamma.py").write_text("x = 2\n", encoding="utf-8")
    _commit(project, "a separate change, later")
    assert kyo("sweep").code == 0

    flags = kyo("brief", "--json").json["unresolved_flags"]
    ranges = {f["record_id"]: f["touch_changed_range"] for f in flags}
    assert ranges[together[0]] == ranges[together[1]], ranges
    assert ranges[alone] != ranges[together[0]], ranges
    shared_range = ranges[together[0]]

    human = kyo("brief").out
    group_line = next(
        line for line in human.splitlines() if line.strip().startswith(shared_range)
    )
    assert "2 touch-changed flag(s) from one commit range" in group_line
    assert together[0] in group_line and together[1] in group_line
    assert "[2 worked, 0 open-only]" in group_line or "[0 worked, 2 open-only]" in group_line
    # Neither member is also rendered as a flag line of its own: one event, one line.
    assert not [
        line
        for line in human.splitlines()
        if line.startswith(f"  {together[0]}.") or line.startswith(f"  {together[1]}.")
    ], human

    # The record with its own range is untouched by the grouping.
    own_line = next(
        line for line in human.splitlines() if line.strip().startswith(f"{alone}.f")
    )
    assert f"{alone}  touch-changed" in own_line
    assert "from one commit range" not in own_line


def test_a_non_touch_changed_flag_is_never_grouped(kyo, project: Path, write_file) -> None:
    """c1's last clause. Only `touch-changed` stores a range; every other class has no
    grouping key, and a key nothing could be read out of must not become a bucket."""
    (project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, path="widget.py", name="missing-touch")
    (project / "widget.py").unlink()
    assert kyo("sweep", "--json").code == 0

    flags = kyo("brief", "--json").json["unresolved_flags"]
    mine = next(f for f in flags if f["record_id"] == record)
    assert mine["flag_type"] == "touch-missing"
    assert mine["touch_changed_range"] is None
    human = kyo("brief").out
    assert f"  {mine['id']}  {record}  touch-missing" in human
    assert "from one commit range" not in human


# ------------------------------------------------------------------------ KYO-326
#
# `kyo brief --compact`, which the SessionStart hook runs, listed every unresolved
# sweep flag although nothing at session start acts on one flag rather than on the
# counts. The full brief's per-flag/
# per-group listing (pinned by the tests above, all of which drive `kyo brief`
# without `--compact`) is unchanged; only the compact rendering moves to counts.


def test_compact_brief_states_sweep_flag_counts_by_class_not_the_flags_themselves(
    kyo, project: Path, write_file
) -> None:
    (project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, path="widget.py", name="missing-touch")
    (project / "widget.py").unlink()
    assert kyo("sweep", "--json").code == 0

    flags = kyo("brief", "--json").json["unresolved_flags"]
    mine = next(f for f in flags if f["record_id"] == record)
    assert mine["flag_type"] == "touch-missing"

    compact = kyo("brief", "--compact").out
    # Counted by class (worked/open-only, already unconditional above this point) and
    # by flag type -- not listed by id. Scoped to the section itself (up to the next
    # blank line), since a flag's record id could otherwise coincidentally recur
    # elsewhere in the brief.
    assert "unresolved sweep flags (1):" in compact
    section = compact.split("unresolved sweep flags")[1].split("\n\n")[0]
    assert "touch-missing: 1" in section
    assert mine["id"] not in section
    assert record not in section
    # And it names the command that actually lists them.
    assert "kyo flag list" in section

    # The full brief is untouched: still one line per flag, ids and all.
    full = kyo("brief").out
    assert f"  {mine['id']}  {record}  touch-missing" in full


def test_compact_brief_names_no_command_when_there_is_nothing_to_list(kyo) -> None:
    compact = kyo("brief", "--compact").out
    assert "unresolved sweep flags (0):" in compact
    assert "kyo flag list" not in compact


def test_compact_brief_json_carries_the_full_flag_list_regardless(
    kyo, project: Path, write_file
) -> None:
    """``--json`` is untouched by ``--compact`` either way (unaffected by this dispatch's
    change to the compact text rendering)."""
    (project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, path="widget.py", name="missing-touch")
    (project / "widget.py").unlink()
    assert kyo("sweep", "--json").code == 0

    full_json = kyo("brief", "--json").json["unresolved_flags"]
    compact_json = kyo("brief", "--compact", "--json").json["unresolved_flags"]
    assert full_json == compact_json
    assert any(f["record_id"] == record for f in compact_json)


def test_resolving_a_grouped_flag_is_still_per_flag(kyo, project: Path, write_file) -> None:
    """c2. A grouping that made its members unreachable would have replaced a reading
    problem with an acting one, so the ids stay on the page and `kyo flag resolve`
    is unchanged."""
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    first = _promote_touching(kyo, project, write_file, path="alpha.py", name="grouped-a")
    second = _promote_touching(kyo, project, write_file, path="beta.py", name="grouped-b")
    git_init(project, commit_message="baseline")
    assert kyo("sweep", "--json").json["flags"] == []
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 2\n", encoding="utf-8")
    _commit(project, "one commit")
    assert kyo("sweep").code == 0

    human = kyo("brief").out
    assert "resolve individually: " in human
    flag_ids = [f["id"] for f in kyo("brief", "--json").json["unresolved_flags"]]
    assert len(flag_ids) == 2
    assert all(flag_id in human for flag_id in flag_ids)

    assert kyo("flag", "resolve", flag_ids[0], "--why", "read the commit; unrelated").code == 0
    after = kyo("brief", "--json").json["unresolved_flags"]
    assert [f["id"] for f in after] == [flag_ids[1]], "resolving one closed both"
    # One flag left in that range, so it is no longer a group.
    assert "from one commit range" not in kyo("brief").out
    assert first in human and second in human


# ------------------------------------------- KYO-336: compact controller rules


def _orchestrator_rules_block(out: str) -> str:
    return out.split("orchestrator-audience rules:")[1]


def test_compact_brief_orchestrator_rules_are_one_line_each(kyo, project: Path) -> None:
    """KYO-336.c1, as KYO-419.c4 shortened it: under ``--compact``, each
    controller-audience rule is its title alone, one line, and `kyo rules` is named
    for the full text. The first sentence and the enforced-by summary KYO-336 kept
    on that line are gone from the compact form (the full brief keeps both)."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    rules = orchestrator_rules(text)
    assert rules, "the packaged ledger must carry at least one controller rule"

    section = _orchestrator_rules_block(kyo("brief", "--compact").out)
    for rule in rules:
        assert f"  - {rule.title} ({enforcement_kind(rule.enforced_by)})\n" in section
        if rule.first_line:
            assert rule.first_line not in section
        assert "enforced-by" not in section
    assert "kyo rules" in section
    assert "nothing can check which search a human or a controller" not in section


def test_full_brief_orchestrator_rules_are_unchanged(kyo, project: Path) -> None:
    """KYO-336.c2, superseded by KYO-531.c1: the non-compact rendering is the same
    one line per rule as the compact one -- the title and the enforced-by kind in a
    word. The enforced-by argument and first sentence it used to carry are in
    ``--json`` and `kyo rules "<title>"`."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    rules = orchestrator_rules(text)
    section = _orchestrator_rules_block(kyo("brief").out)
    for rule in rules:
        assert f"  - {rule.title} ({enforcement_kind(rule.enforced_by)})\n" in section
        assert f"(enforced-by: {rule.enforced_by})" not in section


def test_compact_brief_json_orchestrator_rules_carry_full_text_regardless(kyo) -> None:
    """KYO-336.c2: ``--json`` is untouched by ``--compact`` — the compact text
    rendering is the only thing this dispatch changed."""
    full_json = kyo("brief", "--json").json["orchestrator_rules"]
    compact_json = kyo("brief", "--compact", "--json").json["orchestrator_rules"]
    assert full_json == compact_json
    assert full_json, "the packaged ledger must carry at least one controller rule"


# ---------------------------------------- KYO-356.c2: the kyosei:// link-form example


def test_a_fresh_store_still_prints_the_link_form_with_a_placeholder_id(kyo) -> None:
    """KYO-356.c2. A project with no records yet has no real id to show, but the FORM
    still has to reach a controller before the first record exists — so the id in the
    example has to be an unmistakable placeholder, never a shape that could be read as
    a real one."""
    section = _orchestrator_rules_block(kyo("brief").out)
    assert "kyosei://TST/" in section
    assert "TST-<n>" in section
    assert "[TST-<n>](kyosei://TST/TST-<n>)" in section

    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["link_example"] == "[TST-<n>](kyosei://TST/TST-<n>)"


def test_a_record_makes_the_link_example_use_a_real_id_from_this_store(kyo) -> None:
    """KYO-356.c2's other half: once this store has a record, the example is built
    from it rather than the placeholder — the store's own prefix on an id a
    controller here could actually click, not the skill's `KYO-263` from a different
    project entirely."""
    record = new_record(kyo, criteria=["a"])
    assert record.startswith("TST-")

    section = _orchestrator_rules_block(kyo("brief").out)
    expected = f"[{record}](kyosei://TST/{record})"
    assert expected in section
    assert "TST-<n>" not in section

    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["link_example"] == expected


def test_the_link_example_uses_the_most_recently_allocated_record(kyo) -> None:
    """Not just *a* record — a real one a controller is likely to actually be citing,
    which the most recently allocated id is closer to than the first one this store
    ever minted."""
    new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])

    result = kyo("brief", "--json")
    assert result.code == 0, result
    assert result.json["link_example"] == f"[{second}](kyosei://TST/{second})"


def test_the_link_example_prints_under_compact_too(kyo) -> None:
    """KYO-356.c2. The example is for a controller who is about to write a closing
    summary, which is exactly the situation `--compact` (the SessionStart hook's own
    invocation) is read in — so it must not be one of the things `--compact` trims."""
    record = new_record(kyo, criteria=["a"])
    expected = f"[{record}](kyosei://TST/{record})"
    assert expected in _orchestrator_rules_block(kyo("brief", "--compact").out)
