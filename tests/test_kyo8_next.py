"""KYO-8.c5: kyo next — dispatchable, ungated, criteria-present records whose
touches are disjoint from every running dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .conftest import git_worktree, kinds_yml, new_record, valid_report, verify_criterion


def test_a_plain_record_with_criteria_is_ready(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("next", "--json")
    assert result.code == 0, result
    assert record in [r["id"] for r in result.json["ready"]]


def test_a_kind_requiring_criteria_with_none_is_not_ready(kyo) -> None:
    record = new_record(kyo, criteria=[])
    result = kyo("next", "--json")
    assert record not in [r["id"] for r in result.json["ready"]]


def test_a_non_dispatchable_kind_is_not_ready(kyo, project: Path, write_file) -> None:
    """`plan` ships with `policy.dispatchable: false`."""
    idea = project / "ideas" / "a-plan.md"
    idea.write_text("# A Plan\n\n\n", encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "plan",
        "--touches-file",
        write_file("plan-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    record = result.json["record"]["id"]
    ready = kyo("next", "--json").json["ready"]
    assert record not in [r["id"] for r in ready]


def test_a_gated_record_is_not_ready(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    ready = kyo("next", "--json").json["ready"]
    assert record not in [r["id"] for r in ready]


def test_clearing_the_gate_makes_it_ready_again(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    gated = kyo("gate", record, "awaiting:human-review", "--json")
    gate_id = gated.json["gate_id"]
    kyo("ungate", gate_id)
    ready = kyo("next", "--json").json["ready"]
    assert record in [r["id"] for r in ready]


def test_a_done_record_is_not_ready(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about")
    kyo("move", record, "working")
    kyo("move", record, "built")
    kyo("move", record, "verified")
    kyo("move", record, "done")
    ready = kyo("next", "--json").json["ready"]
    assert record not in [r["id"] for r in ready]


def test_a_wont_do_record_is_not_ready(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("wont-do", record, "--why", "dead premise")
    ready = kyo("next", "--json").json["ready"]
    assert record not in [r["id"] for r in ready]


def test_a_record_with_a_running_dispatch_is_not_ready(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    ready = kyo("next", "--json").json["ready"]
    assert record not in [r["id"] for r in ready]


def test_touch_collision_with_a_running_dispatch_excludes_the_other_record(
    kyo, git_project, write_file
) -> None:
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# A\n\n\n", encoding="utf-8")
    rec_a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", write_file("a-criteria.txt", "x\n"),
        "--touches-file", write_file("a-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert rec_a.code == 0, rec_a
    record_a = rec_a.json["record"]["id"]

    idea_b = git_project / "ideas" / "b.md"
    idea_b.write_text("# B\n\n\n", encoding="utf-8")
    rec_b = kyo(
        "promote", str(idea_b), "--kind", "chore",
        "--criteria-file", write_file("b-criteria.txt", "y\n"),
        "--touches-file", write_file("b-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert rec_b.code == 0, rec_b
    record_b = rec_b.json["record"]["id"]

    dispatched = kyo("dispatch", record_a, "--json")
    assert dispatched.code == 0, dispatched

    ready_ids = [r["id"] for r in kyo("next", "--json").json["ready"]]
    assert record_a not in ready_ids  # already running
    assert record_b not in ready_ids  # touches collide with the running dispatch


def test_disjoint_touches_stay_ready_alongside_a_running_dispatch(
    kyo, git_project, write_file
) -> None:
    """KYO-48: record B declares a scope of its own, and it does not overlap. This
    is the only shape in which "disjoint" is a fact rather than an assumption —
    before KYO-48 this test passed with B declaring nothing at all, which proved
    the opposite of what it was named for."""
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# A\n\n\n", encoding="utf-8")
    rec_a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", write_file("a-criteria.txt", "x\n"),
        "--touches-file", write_file("a-touches.txt", "kyosei/config.yml\n"),
        "--json",
    )
    record_a = rec_a.json["record"]["id"]

    record_b = new_record(kyo, kind="chore", criteria=["unrelated"])
    assert kyo(
        "edit", record_b, "--touches-file", write_file("b-touches.txt", "kyosei/kinds.yml\n")
    ).code == 0

    kyo("dispatch", record_a, "--json")
    ready_ids = [r["id"] for r in kyo("next", "--json").json["ready"]]
    assert record_b in ready_ids


# ------------------------------------------------------------------------- KYO-48
#
# A record with NO touches was offered as ready alongside a dispatch whose scope
# it really did overlap, because disjointness is set intersection and the empty
# set intersects nothing: "I have not declared my scope" and "I have declared it
# and it is independent" produced the same favourable answer. Law 6 inverted —
# absence rendered as an ordinary value, and the ordinary value it borrowed was
# the permissive one. Demonstrated live in this repository.


def _make_touched_files(project: Path, touches: str) -> None:
    """A touch must name a file that exists (`kyo promote` validates it), so a test
    that wants a scope outside the store's own files has to create them first."""
    for line in touches.splitlines():
        path_part = line.partition("#")[0].strip()
        if not path_part:
            continue
        path = project / path_part
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder\n", encoding="utf-8")


def _promoted_with_scope(kyo, project, write_file, name: str, touches: str) -> str:
    _make_touched_files(project, touches)
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\n\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file(f"{name}-criteria.txt", "it works\n"),
        "--touches-file", write_file(f"{name}-touches.txt", touches),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_a_zero_touch_record_is_not_offered_as_ready_beside_a_running_dispatch(
    kyo, git_project, write_file
) -> None:
    """The live case, reproduced: the running dispatch covers `kyosei/config.yml`,
    which is also where the undeclared record's real work lives — and `kyo next`
    could not know that, which is the whole point. It must not answer "ready"."""
    running = _promoted_with_scope(kyo, git_project, write_file, "running", "kyosei/config.yml\n")
    unscoped = new_record(kyo, kind="chore", title="Real scope undeclared", criteria=["it works"])
    assert kyo("dispatch", running, "--json").code == 0

    result = kyo("next", "--json")
    assert result.code == 0, result
    assert unscoped not in [r["id"] for r in result.json["ready"]]

    unknown = result.json["scope_unknown"]
    assert unscoped in [r["id"] for r in unknown], "the record vanished instead of being named"
    entry = next(r for r in unknown if r["id"] == unscoped)
    assert unscoped in entry["reason"] and "no touches" in entry["reason"]


def test_declaring_the_real_scope_is_what_moves_it_back_into_ready(
    kyo, git_project, write_file
) -> None:
    """The fix must not be "hide anything unscoped forever": declaring a genuinely
    disjoint scope makes the record ready again, and declaring the colliding one
    keeps it out — which is the second half of the live demonstration."""
    running = _promoted_with_scope(kyo, git_project, write_file, "running", "kyosei/config.yml\n")
    other = new_record(kyo, kind="chore", title="Scope declared later", criteria=["it works"])
    assert kyo("dispatch", running, "--json").code == 0

    kyo("edit", other, "--touches-file", write_file("t-collide.txt", "kyosei/config.yml\n"))
    after_collision = kyo("next", "--json").json
    assert other not in [r["id"] for r in after_collision["ready"]]
    assert other not in [r["id"] for r in after_collision["scope_unknown"]], (
        "a declared collision is a known fact, not an unknown scope"
    )

    kyo("edit", other, "--touches-file", write_file("t-disjoint.txt", "kyosei/kinds.yml\n"))
    assert other in [r["id"] for r in kyo("next", "--json").json["ready"]]


def test_a_running_dispatch_with_no_touches_makes_every_scope_unknown(
    kyo, git_project, write_file
) -> None:
    """The mirror of the same hole: if the work already RUNNING declared nothing,
    nothing can be shown disjoint from it, however carefully the candidate
    declared its own scope."""
    unscoped_runner = new_record(kyo, kind="chore", title="Runs unscoped", criteria=["it works"])
    candidate = _promoted_with_scope(kyo, git_project, write_file, "cand", "kyosei/kinds.yml\n")
    assert kyo("dispatch", unscoped_runner, "--json").code == 0

    result = kyo("next", "--json").json
    assert candidate not in [r["id"] for r in result["ready"]]
    entry = next(r for r in result["scope_unknown"] if r["id"] == candidate)
    assert unscoped_runner in entry["reason"]


def test_a_zero_touch_record_stays_ready_when_nothing_is_running(kyo, git_project) -> None:
    """Zero-ceremony intake is not taxed (laws 12/13). With no dispatch running
    there is nothing to be disjoint FROM, so the answer is knowable and it is
    yes — the unknown only exists relative to work in flight."""
    record = new_record(kyo, kind="chore", criteria=["it works"])
    result = kyo("next", "--json").json
    assert record in [r["id"] for r in result["ready"]]
    assert result["scope_unknown"] == []


def test_next_counts_the_unknown_scope_class_out_loud(kyo, git_project, write_file) -> None:
    """Law 6: an excluded record is counted and named in the human output, never
    silently dropped — and the count line is printed even when it is zero, so its
    absence is a statement rather than a silence."""
    empty = kyo("next")
    assert empty.code == 0, empty
    assert "0 record(s) with unknown scope" in empty.out

    running = _promoted_with_scope(kyo, git_project, write_file, "running", "kyosei/config.yml\n")
    unscoped = new_record(kyo, kind="chore", title="Real scope undeclared", criteria=["it works"])
    kyo("dispatch", running, "--json")

    result = kyo("next")
    assert result.code == 0, result
    assert "1 record(s) with unknown scope" in result.out
    assert unscoped in result.out
    assert "--touches-file" in result.out, "the remedy must be on the surface that hides the record"


def test_the_brief_shows_the_same_unknown_scope_class(kyo, git_project, write_file) -> None:
    """`kyo brief` shares `kyo next`'s computation and is what a session hook puts
    in front of a controller; hiding the class in one and showing it in the other
    would put the silence back where it started."""
    running = _promoted_with_scope(kyo, git_project, write_file, "running", "kyosei/config.yml\n")
    unscoped = new_record(kyo, kind="chore", title="Real scope undeclared", criteria=["it works"])
    kyo("dispatch", running, "--json")

    result = kyo("brief")
    assert result.code == 0, result
    assert "scope unknown" in result.out
    assert unscoped in result.out
    brief = kyo("brief", "--json").json
    assert unscoped not in [r["id"] for r in brief["ready"]]
    assert unscoped in [r["id"] for r in brief["scope_unknown"]]


# ------------------------------------------------------------------------- KYO-65
#
# The same computation, the other surface. `kyo next` and `kyo dispatch` share
# `commands.touch_standing` precisely so they cannot disagree about what is safe,
# so a claim that grows to include a dispatch's `--also-covers` records has to
# grow in both at once or the pairing is broken.


def test_next_withholds_a_record_colliding_only_with_a_covered_record(
    kyo, git_project, write_file
) -> None:
    """KYO-65.c1/c4: the live shape. The running dispatch's PRIMARY record names
    nothing the candidate names; the record it also covers names the candidate's
    only file. Before KYO-65 this candidate was offered as ready, which is how
    `render.py`, `html_site.py` and `brief.py` came to be edited by KYO-43.d1
    while `kyo next` believed they were unclaimed."""
    primary = _promoted_with_scope(kyo, git_project, write_file, "primary", "src/kyosei/cli.py\n")
    covered = _promoted_with_scope(
        kyo, git_project, write_file, "covered", "src/kyosei/render.py\n"
    )
    candidate = _promoted_with_scope(kyo, git_project, write_file, "cand", "src/kyosei/render.py\n")
    assert kyo("dispatch", primary, "--also-covers", covered, "--json").code == 0

    listing = kyo("next", "--json").json
    assert candidate not in [r["id"] for r in listing["ready"]], (
        "the candidate's only file is claimed by the record the dispatch covers"
    )
    assert candidate not in [r["id"] for r in listing["scope_unknown"]], (
        "every record in the comparison declared a scope; this is a known collision"
    )
    assert covered not in [r["id"] for r in listing["ready"]], (
        "the covered record is being worked right now — it is not available work"
    )


def test_next_and_dispatch_agree_about_a_covered_records_claim(
    kyo, git_project, write_file, tmp_path
) -> None:
    """KYO-48.c3's pairing, extended to KYO-65: whatever `kyo next` offers,
    `kyo dispatch` must accept, with a covered record in the picture."""
    primary = _promoted_with_scope(kyo, git_project, write_file, "primary", "src/kyosei/cli.py\n")
    covered = _promoted_with_scope(
        kyo, git_project, write_file, "covered", "src/kyosei/render.py\n"
    )
    colliding = _promoted_with_scope(kyo, git_project, write_file, "coll", "src/kyosei/render.py\n")
    disjoint = _promoted_with_scope(kyo, git_project, write_file, "disj", "src/kyosei/kinds.py\n")
    assert kyo("dispatch", primary, "--also-covers", covered, "--json").code == 0

    ready_ids = [r["id"] for r in kyo("next", "--json").json["ready"]]
    assert ready_ids == [disjoint]
    assert colliding not in ready_ids

    elsewhere = tmp_path / "wt-disjoint"
    git_worktree(kyo.project, elsewhere)
    accepted = kyo("dispatch", disjoint, "--worktree", str(elsewhere), "--json")
    assert accepted.code == 0, (
        "`kyo next` offered it and `kyo dispatch` refused — the two surfaces disagree"
    )


def test_next_marks_the_scope_unknown_when_a_covered_record_declares_nothing(
    kyo, git_project, write_file
) -> None:
    """KYO-65.c3 on `kyo next`: the union must not read a missing touch list as an
    empty one just because the primary record declared its own."""
    primary = _promoted_with_scope(kyo, git_project, write_file, "primary", "src/kyosei/cli.py\n")
    unscoped = new_record(kyo, kind="chore", title="Covered, unscoped", criteria=["it works"])
    candidate = _promoted_with_scope(kyo, git_project, write_file, "cand", "src/kyosei/kinds.py\n")
    assert kyo("dispatch", primary, "--also-covers", unscoped, "--json").code == 0

    listing = kyo("next", "--json").json
    assert candidate not in [r["id"] for r in listing["ready"]]
    entry = next(r for r in listing["scope_unknown"] if r["id"] == candidate)
    assert unscoped in entry["reason"], "the record whose scope is missing must be named"


# ------------------------------------------------------------------------ KYO-289
#
# KYO-87 gave the board a "finished but not moved" class and gated it on a dispatch
# having reported. That gate excluded the one population it most needed to catch:
# work nobody dispatched. On this repository's own store, most of the records
# `kyo next --json` offered as ready were at `verified` with every criterion settled,
# built in-session or verified by hand, and so carried no reported dispatch. This is
# KYO-87's defect reached by the other road: a record with nothing left to build is
# handed out as dispatchable because nobody dispatched it.
#
# The tests below are the ones that fail if the dispatch-history condition is put
# back into `_ready_records`, plus the two boundaries that must survive it.


def _settled_without_any_dispatch(kyo, *, title: str = "Built in-session") -> str:
    """The live shape: criteria met by hand, no dispatch of any kind, still `open`."""
    record = new_record(kyo, kind="bug", title=title, criteria=["the thing works"])
    verify_criterion(kyo, f"{record}.c1")
    assert kyo("show", record, "--json").json["status"] == "open"
    assert kyo("show", record, "--json").json["dispatches"] == []
    return record


def test_a_record_with_every_criterion_settled_is_not_ready_without_any_dispatch(
    kyo,
) -> None:
    """KYO-289.c1. The condition is the criteria, not the dispatch history."""
    record = _settled_without_any_dispatch(kyo)

    data = kyo("next", "--json").json
    assert record not in [r["id"] for r in data["ready"]], (
        "a record with nothing left to build is being offered as dispatchable "
        "because nobody dispatched it"
    )
    assert record not in [r["id"] for r in data["scope_unknown"]], (
        "finished work must not reappear in the other class the surfaces offer"
    )
    assert [item["id"] for item in data["settled_not_moved"]] == [record]


def test_the_class_carries_an_empty_reported_by_rather_than_omitting_it(kyo) -> None:
    """KYO-289.c1, law 6: nothing reported, so the list is empty — present and
    empty, never absent. The desktop app reads these rows off the same JSON."""
    record = _settled_without_any_dispatch(kyo)

    item = kyo("next", "--json").json["settled_not_moved"][0]
    assert item["reported_by"] == []
    assert item["needs"] == f"kyo move {record} verified"


def test_the_class_names_the_move_against_the_ladder_for_an_undispatched_record(
    kyo,
) -> None:
    """KYO-173's rule holds for a record no dispatch ever touched: a record already
    at the verified rung is told the completion move, not the rung it stands on."""
    record = _settled_without_any_dispatch(kyo)
    for status in ("working", "built", "verified"):
        assert kyo("move", record, status).code == 0

    settled = kyo("next", "--json").json["settled_not_moved"]
    assert [item["id"] for item in settled] == [record]
    assert settled[0]["needs"] == f"kyo move {record} done"


def test_running_the_named_move_clears_an_undispatched_record_from_the_class(
    kyo,
) -> None:
    """The remedy a controller is handed actually empties the class (KYO-173.c1),
    by running exactly what the surface printed until it does."""
    record = _settled_without_any_dispatch(kyo)

    for _ in range(3):
        rows = kyo("next", "--json").json["settled_not_moved"]
        if not rows:
            break
        command = rows[0]["needs"].split()
        assert command[0] == "kyo"
        assert kyo(*command[1:]).code == 0, rows[0]["needs"]

    assert kyo("next", "--json").json["settled_not_moved"] == []
    assert kyo("show", record, "--json").json["status"] == "done"


def test_both_surfaces_withhold_the_undispatched_finished_record(kyo) -> None:
    """KYO-87.c3 under the wider class: `kyo next` and `kyo brief` read one
    computation, so neither may call this record ready while the other calls it
    finished — asserted on the human renderings, which is what a controller reads."""
    record = _settled_without_any_dispatch(kyo)

    nxt = kyo("next", "--json").json
    brief = kyo("brief", "--json").json
    for key in ("ready", "scope_unknown", "settled_not_moved"):
        assert [r["id"] for r in nxt[key]] == [r["id"] for r in brief[key]], key

    assert "1 record(s) finished but not moved (NOT ready)" in kyo("next").out
    assert "finished but not moved (1)" in kyo("brief").out
    assert "dispatchable now (0)" in kyo("brief").out
    assert record in kyo("brief", "--compact").out, "dropped from the session-start brief"


def test_the_rendered_sentence_does_not_assert_a_dispatch_that_never_existed(
    kyo,
) -> None:
    """KYO-289.c3. The line under the record used to open with the reporters and the
    word "reported"; with nothing to name it would have opened with a bare " reported
    and ...", asserting a dispatch the store does not have."""
    record = _settled_without_any_dispatch(kyo)

    for out in (kyo("next").out, kyo("brief").out):
        line = next(
            ln for ln in out.splitlines() if "needs `kyo move" in ln and "every criterion" in ln
        )
        assert "reported" not in line, line
        assert line.strip().startswith("every criterion is settled"), line
    for out in (kyo("next").out, kyo("brief").out):
        assert "every criterion is settled, however the work was done" in out
    assert record in kyo("next").out


def test_a_reported_dispatch_is_still_named_on_the_row(kyo, git_project) -> None:
    """The dispatch stopped deciding the class; it did not stop being a fact about
    it. Where one reported, the row names it and the sentence says so."""
    record = new_record(kyo, kind="bug", title="Worked by a worker", criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    report = git_project / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record,
                worked_from_sha=dispatched.json["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatched.json["dispatch_id"], "--file", str(report)).code == 0
    verify_criterion(kyo, f"{record}.c1")

    item = kyo("next", "--json").json["settled_not_moved"][0]
    assert item["reported_by"] == [f"{record}.d1"]
    assert f"{record}.d1 reported; every criterion is settled" in kyo("next").out
    assert f"{record}.d1 reported; every criterion is settled" in kyo("brief").out


def test_an_unsettled_criterion_keeps_a_record_ready_however_it_was_worked(kyo) -> None:
    """KYO-289.c2, the boundary KYO-87 drew and this change must not blur: settled
    means settled, and a record with anything open is genuinely dispatchable."""
    record = new_record(kyo, kind="bug", title="One of two", criteria=["a", "b"])
    verify_criterion(kyo, f"{record}.c1")

    data = kyo("next", "--json").json
    assert [r["id"] for r in data["ready"]] == [record]
    assert data["settled_not_moved"] == []
    assert "prior_dispatch" not in data["ready"][0], (
        "no dispatch reported on this record, so nothing may claim one did"
    )


def test_an_unobserved_criterion_is_unsettled_and_keeps_the_record_ready(kyo) -> None:
    """`unmet-unobserved` is a wait on a person, not a settled state
    (`contracts.SETTLED_CRITERION_STATES`) — so KYO-267's class and this one cannot
    swallow each other's records."""
    record = new_record(kyo, kind="bug", title="Waiting on a human", criteria=["a"])
    verify_criterion(
        kyo,
        f"{record}.c1",
        "unmet-unobserved",
        method="human-observed",
        why="the maintainer has not looked yet",
    )

    data = kyo("next", "--json").json
    assert [r["id"] for r in data["ready"]] == [record]
    assert data["settled_not_moved"] == []
    assert [r["id"] for r in data["ungated_unobserved"]] == [record]


def test_a_record_with_no_criteria_is_still_never_called_finished(kyo) -> None:
    """KYO-87's guard, which KYO-289 must not widen past: "no unsettled criteria"
    stays unsatisfiable by an empty set, with or without a dispatch. Reachable only
    where a project's registry says a dispatchable kind needs none."""
    raw = yaml.safe_load(kinds_yml(kyo.project).read_text(encoding="utf-8"))
    raw["kinds"]["chore"]["policy"]["requires_criteria"] = False
    kinds_yml(kyo.project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    record = new_record(kyo, kind="chore", title="Nothing to check", criteria=[])
    assert kyo("show", record, "--json").json["criteria"] == []

    data = kyo("next", "--json").json
    assert data["settled_not_moved"] == []
    assert [r["id"] for r in data["ready"]] == [record]


def test_a_settled_record_with_an_open_gate_is_in_no_class_at_all(kyo) -> None:
    """The gate check runs before the class, and stays there: a gated record is
    withheld from the board entirely, and a finished-and-gated record must not
    reappear under a heading that tells a controller to move it."""
    record = _settled_without_any_dispatch(kyo)
    assert kyo("gate", record, "awaiting:human-review").code == 0

    data = kyo("next", "--json").json
    assert data["settled_not_moved"] == []
    assert record not in [r["id"] for r in data["ready"]]


# ------------------------------------------------------------------------ KYO-295
#
# Everything above is about a LISTING. Until this record the withholding was only
# that: `kyo next` and `kyo brief` declined to offer a finished record and
# `kyo dispatch <that record>` froze a brief for it anyway, so the surface a
# controller reads and the surface a controller acts on disagreed about the same
# record. The same shape KYO-48 closed for touch-disjointness, one class along, and
# the fix is the same one: the refusal is raised off `_ready_records`' own function
# (`cmd_next.settled_not_moved_entry`) rather than off a second derivation.


def test_dispatch_refuses_the_record_kyo_next_withholds_as_finished(kyo) -> None:
    """KYO-295.c1. The record `kyo next` lists under finished-but-not-moved is the
    record `kyo dispatch` refuses — asserted as one fact about one id, so a change
    that narrows either surface alone fails here."""
    record = _settled_without_any_dispatch(kyo)
    listed = kyo("next", "--json").json["settled_not_moved"]
    assert [item["id"] for item in listed] == [record]

    refused = kyo("dispatch", record)
    assert refused.code == 2, refused
    assert "dispatch-record-finished" in refused.err
    # The move the listing names and the move the refusal names are one value.
    assert listed[0]["needs"] in refused.err, refused.err
    assert "--force" in refused.err


def test_the_refusal_names_the_same_move_at_every_rung_the_listing_does(kyo) -> None:
    """KYO-173's ladder rule reaches the refusal because it is the same string. A
    record at the verified rung is told the completion move by both surfaces, not
    the rung it is already standing on."""
    record = _settled_without_any_dispatch(kyo)
    for status in ("working", "built", "verified"):
        assert kyo("move", record, status).code == 0

    needs = kyo("next", "--json").json["settled_not_moved"][0]["needs"]
    assert needs == f"kyo move {record} done"
    refused = kyo("dispatch", record)
    assert refused.code == 2, refused
    assert needs in refused.err, refused.err


def test_the_named_move_clears_the_dispatch_refusal(kyo, git_project: Path) -> None:
    """KYO-295.c1's remedy half: what the refusal tells the controller to run is
    run here, with nothing else, and the dispatch that was refused then succeeds.

    Provoked from the verified rung on purpose. The class's named move is the one
    that lets it go *from where the record stands* (KYO-173), so from `open` it
    names the verified rung and the record is still finished one rung higher —
    that two-step is the listing's behaviour and the refusal inherits it verbatim,
    which is the point of sharing the function. The rung where the named move is
    the move that clears the refusal outright is the verified one.
    """
    record = _settled_without_any_dispatch(kyo)
    for status in ("working", "built", "verified"):
        assert kyo("move", record, status).code == 0
    refused = kyo("dispatch", record)
    assert refused.code == 2, refused

    remedy = refused.err.split("run `kyo ")[1].split("`")[0].split()
    assert remedy[0] == "move" and remedy[1] == record, refused.err
    assert kyo(*remedy).code == 0, refused.err

    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    assert dispatched.json["finished_not_moved_overridden"] is None


def test_force_still_dispatches_a_finished_record_and_says_what_it_overrode(
    kyo, git_project: Path
) -> None:
    """Every other refusal in `kyo dispatch` yields to `--force`, and this one does
    too: the controller may know the criteria are stale. What must not happen is a
    forced dispatch that looks like an unforced one, so the override is echoed and
    carried in `--json`."""
    record = _settled_without_any_dispatch(kyo)
    forced = kyo("dispatch", record, "--force", "--json")
    assert forced.code == 0, forced
    override = forced.json["finished_not_moved_overridden"]
    assert override is not None, forced.json
    assert override["id"] == record
    assert override["needs"] == f"kyo move {record} verified"

    # The echo a controller actually reads, off a second record so the assertion is
    # about the text output rather than the --json one above.
    other = _settled_without_any_dispatch(kyo, title="Also built in-session")
    echoed = kyo("dispatch", other, "--force")
    assert echoed.code == 0, echoed
    assert "--force overrode the finished-but-not-moved refusal" in echoed.out
    assert f"kyo move {other} verified" in echoed.out


def test_dispatch_still_hands_out_a_record_with_an_unsettled_criterion(
    kyo, git_project: Path
) -> None:
    """The boundary, from the side that matters most: this refusal must not become
    a reason a genuinely unfinished record cannot be dispatched. One unsettled
    criterion is the whole difference, on both surfaces."""
    record = new_record(kyo, criteria=["done already", "still open"])
    verify_criterion(kyo, f"{record}.c1")

    data = kyo("next", "--json").json
    assert [r["id"] for r in data["settled_not_moved"]] == []
    assert record in [r["id"] for r in data["ready"]]
    assert kyo("dispatch", record, "--json").code == 0


def test_dispatch_still_hands_out_a_record_with_no_criteria_at_all(
    kyo, git_project: Path
) -> None:
    """KYO-87's guard, carried into the refusal: "no unsettled criteria" must not be
    satisfiable by the empty set, or a kind whose policy needs no criteria becomes
    undispatchable the moment this check exists."""
    raw = yaml.safe_load(kinds_yml(kyo.project).read_text(encoding="utf-8"))
    raw["kinds"]["chore"]["policy"]["requires_criteria"] = False
    kinds_yml(kyo.project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    record = new_record(kyo, kind="chore", title="Nothing to check", criteria=[])

    assert kyo("next", "--json").json["settled_not_moved"] == []
    assert kyo("dispatch", record, "--json").code == 0


def test_the_two_surfaces_are_one_function(kyo, git_project: Path) -> None:
    """KYO-295.c1's "off the same function" clause, asserted as the thing it is for
    rather than by reading the imports: patch the shared entry point and BOTH the
    listing and the refusal change. Two derivations could not both move."""
    from kyosei import cmd_next

    record = _settled_without_any_dispatch(kyo)
    assert kyo("dispatch", record).code == 2
    original = cmd_next.settled_not_moved_entry
    cmd_next.settled_not_moved_entry = lambda *a, **k: None
    try:
        assert kyo("next", "--json").json["settled_not_moved"] == []
        assert kyo("dispatch", record, "--json").code == 0
    finally:
        cmd_next.settled_not_moved_entry = original
