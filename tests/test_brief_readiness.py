"""What `kyo brief` says about what needs doing.

Two consumer reports, below, concern the same output for the same
reason: `kyo brief` is the one place the `/kyosei`
skill tells a controller to trust *instead of* reading records ("Do not re-derive
board state by reading record files"), so anything it leaves out is not a
formatting gap — it is a fact the controller is instructed not to go and check.

- **The status column.** `_ready_records` puts `status` on every row and `kyo next` prints
  it; the brief's formatter dropped it. Under a heading reading "ready work", an
  `open` bug and a `verified` one rendered identically. A controller called three
  bugs "the three most consequential still open" and argued a priority call on
  it — one was `verified`, two were `done`, all fixed the day before.
- **The `built` queue.** `built` -> `verified` is a real transition the status flow already
  models, and nothing listed the `built` queue. Those records are not dispatchable
  (nothing to hand out), not gated (no condition to clear) and not done; a
  consumer ended a day with three of them and the tool could point at none.
"""

from __future__ import annotations

import json

import yaml

from .conftest import kinds_yml, new_record, valid_report, verify_criterion


def _built_record(kyo, *, title: str, criteria: list[str]) -> str:
    record = new_record(kyo, kind="bug", title=title, criteria=criteria)
    assert kyo("move", record, "working").code == 0
    assert kyo("move", record, "built").code == 0
    return record


# ----------------------------------------------------------- the status column


def test_the_brief_prints_the_status_of_every_dispatchable_row(kyo) -> None:
    record = new_record(kyo, kind="bug", title="A request stayed in the queue", criteria=["a"])
    assert kyo("move", record, "working").code == 0

    result = kyo("brief")
    assert result.code == 0, result
    row = next(line for line in result.out.splitlines() if record in line)
    assert "working" in row, f"the brief dropped the status again: {row!r}"


def test_the_brief_and_kyo_next_render_the_same_status(kyo) -> None:
    """The data was never missing — only this formatter dropped it. If the two
    renderings disagree again, one of them is lying about the same rows."""
    record = new_record(kyo, kind="bug", title="Same rows, same facts", criteria=["a"])
    kyo("move", record, "working")

    next_row = next(line for line in kyo("next").out.splitlines() if record in line)
    brief_row = next(line for line in kyo("brief").out.splitlines() if record in line)
    for field in (record, "bug", "working"):
        assert field in next_row and field in brief_row


def test_a_verified_record_is_not_described_as_ready_work(kyo) -> None:
    """The heading gave up the word, not the definition: `kyo next`'s
    "dispatchable, ungated, disjoint" is correct and says nothing about whether
    any work remains. A `verified` record satisfies it while needing nothing."""
    record = new_record(kyo, kind="bug", title="Already checked", criteria=["a"])
    # KYO-546: entering verified needs a recorded verdict.
    ran = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about")
    assert ran.code == 0, ran
    for status in ("working", "built", "verified"):
        assert kyo("move", record, status).code == 0

    out = kyo("brief").out
    assert "ready work" not in out
    assert "dispatchable now" in out
    row = next(line for line in out.splitlines() if record in line)
    assert "verified" in row, "a verified record must not read as outstanding work"


# ------------------------------------------------------------- the built queue


def test_built_records_get_their_own_queue(kyo) -> None:
    record = _built_record(kyo, title="Needs thirty seconds of a human", criteria=["a"])
    result = kyo("brief", "--json")
    assert result.code == 0, result
    awaiting = result.json["awaiting_verification"]
    assert [item["id"] for item in awaiting] == [record]
    assert "built, awaiting verification (1)" in kyo("brief").out


def test_the_queue_says_what_each_record_needs(kyo) -> None:
    """Each row says what its record needs, and that is derived rather than
    guessed: the criteria not yet settled are what stands between the record and
    done, so they are what a verifier should look at."""
    record = _built_record(kyo, title="Two things to check", criteria=["the first", "the second"])
    assert kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed").code == 0

    awaiting = kyo("brief", "--json").json["awaiting_verification"][0]
    assert [c["id"] for c in awaiting["unsettled_criteria"]] == [f"{record}.c2"]
    assert "the second" in kyo("brief").out


def test_a_built_record_with_everything_settled_names_the_move(kyo) -> None:
    record = _built_record(kyo, title="Just needs the move", criteria=["the only one"])
    assert kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed").code == 0

    out = kyo("brief").out
    assert kyo("brief", "--json").json["awaiting_verification"][0]["unsettled_criteria"] == []
    assert f"kyo move {record} verified" in out


def test_the_queue_holds_only_built_records(kyo) -> None:
    """Not open, not working, not verified, not done — `built` is a distinct state
    with a distinct audience, which is the whole reason it exists."""
    ids = {}
    for status in ("open", "working", "built", "verified", "done"):
        record = new_record(kyo, kind="bug", title=f"A {status} record", criteria=["a"])
        if status != "open":
            kyo("move", record, "working")
        if status in ("built", "verified", "done"):
            kyo("move", record, "built")
        if status in ("verified", "done"):
            kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
            kyo("move", record, "verified")
        if status == "done":
            kyo("move", record, "done")
        ids[status] = record

    awaiting = {item["id"] for item in kyo("brief", "--json").json["awaiting_verification"]}
    assert awaiting == {ids["built"]}


def test_the_queue_survives_compact_mode(kyo) -> None:
    """The `SessionStart` hook injects `--compact`, so a queue only the full
    rendering carries is a queue the controller never sees at session start —
    which is exactly when it matters."""
    record = _built_record(kyo, title="Visible at session start", criteria=["a"])
    out = kyo("brief", "--compact").out
    assert "built, awaiting verification (1)" in out
    assert record in out


# ------------------------------------------------------------------- KYO-87
#
# A consumer's controller found that `kyo brief`'s 'dispatchable now (31)' included
# 16 records already verified and merged: half the list was finished work, on the
# surface a controller is told to read instead of the records.
#
# The obvious reading — their controller never moved those records out of `open`,
# and the brief is reporting the board as it stands — is available and insufficient.
# The tool already knew: each of those records had a dispatch in state `reported`
# and criteria that were all settled, and nothing compared any of it.
#
# `_awaiting_verification` is the section that exists for "worked but not settled",
# and it selects on `status = 'built'` and nothing else — the one fact in the whole
# picture that depends on a controller having remembered to declare it.


def _reported_dispatch(kyo, git_project, record: str, *, also_covers: str | None = None) -> str:
    """Dispatch ``record``, file a schema-valid report, return the dispatch id.

    Deliberately reports *nothing* about the criteria: `kyo report` does not apply
    verdicts to criteria (applying them is a controller act), so this is exactly the
    consumer's sequence — a dispatch reports, the controller verifies by hand, the
    branch merges, and the status move is the step nobody performs.
    """
    args = ["dispatch", record, "--json"]
    if also_covers is not None:
        args = ["dispatch", record, "--also-covers", also_covers, "--json"]
    dispatched = kyo(*args)
    assert dispatched.code == 0, dispatched
    report_path = git_project / f"report-{dispatched.json['dispatch_id']}.json"
    report_path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record,
                worked_from_sha=dispatched.json["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatched.json["dispatch_id"], "--file", str(report_path))
    assert ingested.code == 0, ingested
    return dispatched.json["dispatch_id"]


def _finished_but_not_moved(kyo, git_project, *, title: str = "Merged, and still open") -> str:
    """The exact state the consumer had 16 of: dispatched, reported, every criterion
    verified, and still `open` because moving a record is the controller's act."""
    record = new_record(kyo, kind="bug", title=title, criteria=["the thing works"])
    _reported_dispatch(kyo, git_project, record)
    verify_criterion(kyo, f"{record}.c1")
    assert kyo("show", record, "--json").json["status"] == "open"
    return record


def _section(out: str, heading_prefix: str) -> list[str]:
    """The lines under one heading, up to the blank line that ends the section."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading_prefix))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        body.append(line)
    return body


def _next_ready_rows(out: str) -> list[str]:
    """`kyo next`'s own ready list — its heading leads with the count."""
    heading = next(line for line in out.splitlines() if line.endswith("ready record(s):"))
    return _section(out, heading)


def test_a_reported_and_fully_verified_record_leaves_dispatchable_now(kyo, git_project) -> None:
    """KYO-87.c1: it does not appear there, and it appears in a class of its own
    naming what it actually needs."""
    record = _finished_but_not_moved(kyo, git_project)

    data = kyo("brief", "--json").json
    assert record not in [item["id"] for item in data["ready"]]
    settled = data["settled_not_moved"]
    assert [item["id"] for item in settled] == [record]
    assert settled[0]["needs"] == f"kyo move {record} verified"

    out = kyo("brief").out
    assert record not in "\n".join(_section(out, "dispatchable now ("))
    assert f"kyo move {record} verified" in "\n".join(_section(out, "finished but not moved ("))


def test_the_class_is_derived_from_the_dispatch_and_not_from_status_built(
    kyo, git_project
) -> None:
    """KYO-87.c2. The record is still `open` — nobody declared anything — and it is
    classified anyway, off the dispatch's state and its criteria. It is *not* in
    `awaiting_verification`, which is the section that reads `status = 'built'` and
    was the only one of these the tool had."""
    record = _finished_but_not_moved(kyo, git_project)

    data = kyo("brief", "--json").json
    assert kyo("show", record, "--json").json["status"] == "open"
    assert [item["id"] for item in data["settled_not_moved"]] == [record]
    assert data["awaiting_verification"] == []
    assert data["settled_not_moved"][0]["reported_by"] == [f"{record}.d1"]


def test_nothing_moved_the_record_to_produce_the_classification(kyo, git_project) -> None:
    """A status change fires transition contracts and is the controller's act
    The fix is a classification, never an automatic move — so running the
    surfaces must leave the stored status exactly where it was."""
    record = _finished_but_not_moved(kyo, git_project)
    kyo("brief")
    kyo("next")
    assert kyo("show", record, "--json").json["status"] == "open"


def test_kyo_next_and_kyo_brief_cannot_disagree_about_a_finished_record(
    kyo, git_project
) -> None:
    """KYO-87.c3. Both surfaces read one computation, so there is no second place
    for readiness to be decided differently. Asserted on every class, not just
    `ready`: a record `kyo next` drops and the brief explains is the same
    disagreement wearing a different heading."""
    finished = _finished_but_not_moved(kyo, git_project)
    untouched = new_record(kyo, kind="bug", title="Nobody has touched this", criteria=["a"])

    nxt = kyo("next", "--json").json
    brief = kyo("brief", "--json").json
    for key in ("ready", "scope_unknown", "settled_not_moved"):
        assert [item["id"] for item in nxt[key]] == [item["id"] for item in brief[key]], key
    assert [item["id"] for item in nxt["ready"]] == [untouched]
    assert [item["id"] for item in nxt["settled_not_moved"]] == [finished]


def test_no_surface_calls_a_reported_verified_open_record_dispatchable(kyo, git_project) -> None:
    """KYO-87.c4, against the human renderings rather than the payload: the
    consumer read a *heading*, and the count under it was the whole complaint."""
    record = _finished_but_not_moved(kyo, git_project)

    brief_rows = _section(kyo("brief").out, "dispatchable now (")
    next_rows = _next_ready_rows(kyo("next").out)
    compact_rows = _section(kyo("brief", "--compact").out, "dispatchable now (")
    for rows in (brief_rows, next_rows, compact_rows):
        assert record not in "\n".join(rows), rows
    assert "dispatchable now (0)" in kyo("brief").out
    assert record in kyo("brief", "--compact").out, "dropped from the session-start brief entirely"


def test_a_reported_dispatch_with_an_unsettled_criterion_stays_dispatchable(
    kyo, git_project
) -> None:
    """The boundary the fix refuses to flatten. A worker can report `unmet-unbuilt`
    and merge nothing; that record needs dispatching again, and calling it finished
    because a dispatch reported would answer a list that flattened two states with a
    list that flattens two others. It says what happened to it instead."""
    record = new_record(kyo, kind="bug", title="Reported, not finished", criteria=["a", "b"])
    _reported_dispatch(kyo, git_project, record)
    verify_criterion(kyo, f"{record}.c1")

    data = kyo("brief", "--json").json
    assert [item["id"] for item in data["ready"]] == [record]
    assert data["settled_not_moved"] == []
    row = data["ready"][0]["prior_dispatch"]
    assert f"{record}.d1" in row and "1 of 2 criteria unsettled" in row
    assert row in kyo("brief").out
    assert row in kyo("next").out


def test_a_covered_record_is_classified_by_the_dispatch_that_covered_it(
    kyo, git_project
) -> None:
    """A dispatch either formally declares what it covers or it carries nothing for
    it (KYO-12/KYO-13.c4). A covered record's work is reported by that dispatch, and
    it is the record a controller is *most* likely to leave behind, because it was
    never the subject of the dispatch."""
    primary = new_record(kyo, kind="bug", title="The subject", criteria=["a"])
    covered = new_record(kyo, kind="bug", title="Carried along", criteria=["b"])
    _reported_dispatch(kyo, git_project, primary, also_covers=covered)
    verify_criterion(kyo, f"{primary}.c1")
    verify_criterion(kyo, f"{covered}.c1")

    settled = kyo("brief", "--json").json["settled_not_moved"]
    assert {item["id"] for item in settled} == {primary, covered}
    by_id = {item["id"]: item for item in settled}
    assert by_id[covered]["reported_by"] == [f"{primary}.d1"]


def test_a_terminated_no_report_dispatch_is_not_evidence_that_work_finished(
    kyo, git_project
) -> None:
    """`terminated-no-report` is its own loud state precisely because nothing was
    reported. Reading it as "finished" would be a check reporting success without
    evidence — the failure shape this project's CLAUDE.md names.

    KYO-289 moved where that shows. The class no longer asks whether a dispatch
    reported, so "is this record finished" is answered by its criteria and a dead
    dispatch changes nothing either way. What `terminated-no-report` must still never
    do is *count as a report* — and the store says so in the two places a controller
    reads: the record's criteria are still open, so it is still dispatchable, and no
    surface credits the crashed dispatch with having reported on it.
    """
    record = new_record(kyo, kind="bug", title="Crashed mid-flight", criteria=["a", "b"])
    dispatched = kyo("dispatch", record, "--json")
    kyo("dispatch", "close", dispatched.json["dispatch_id"], "--terminated-no-report",
        "--why", "the worker crashed")
    verify_criterion(kyo, f"{record}.c1")

    data = kyo("brief", "--json").json
    assert data["settled_not_moved"] == []
    assert [item["id"] for item in data["ready"]] == [record]
    assert "prior_dispatch" not in data["ready"][0], (
        "a dispatch that reported nothing is being rendered as one that did"
    )


def test_a_terminated_dispatch_is_never_named_as_a_reporter_of_finished_work(
    kyo, git_project
) -> None:
    """The same rule where KYO-289 relocated it. Settle every criterion by hand and
    the record is finished — by its criteria, which is the only evidence there is.
    The crashed dispatch must not appear in `reported_by`, because nothing it did is
    evidence of anything (contracts: DISPATCH_STATE_TERMINATED_NO_REPORT)."""
    record = new_record(kyo, kind="bug", title="Crashed, finished by hand", criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    kyo("dispatch", "close", dispatched.json["dispatch_id"], "--terminated-no-report",
        "--why", "the worker crashed")
    verify_criterion(kyo, f"{record}.c1")

    settled = kyo("brief", "--json").json["settled_not_moved"]
    assert [item["id"] for item in settled] == [record]
    assert settled[0]["reported_by"] == []
    section = "\n".join(_section(kyo("brief").out, "finished but not moved ("))
    assert record in section
    assert dispatched.json["dispatch_id"] not in section
    assert "reported" not in section


def test_a_record_with_no_criteria_at_all_is_never_called_finished(kyo, git_project) -> None:
    """"No unsettled criteria" must not be satisfied vacuously. A record with
    nothing to settle carries no evidence that its work is done, and a class that
    swallowed it would be asserting completion from an empty set.

    Reachable only where a project's own registry says a dispatchable kind needs no
    criteria, which is a legal thing for `kyosei/kinds.yml` to say — so the test
    says it, rather than asserting the guard cannot be hit under the defaults."""
    raw = yaml.safe_load(kinds_yml(kyo.project).read_text(encoding="utf-8"))
    raw["kinds"]["chore"]["policy"]["requires_criteria"] = False
    kinds_yml(kyo.project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    record = new_record(kyo, kind="chore", title="Nothing to check", criteria=[])
    _reported_dispatch(kyo, git_project, record)
    assert kyo("show", record, "--json").json["criteria"] == []

    data = kyo("brief", "--json").json
    assert data["settled_not_moved"] == []
    assert [item["id"] for item in data["ready"]] == [record]


def test_the_class_prints_at_zero_like_every_other_readiness_class(kyo, git_project) -> None:
    """KYO-48's rule, applied to the new heading: a section that appears only when
    occupied teaches a reader that its absence means nothing was checked."""
    new_record(kyo, kind="bug", title="Ordinary open work", criteria=["a"])
    assert "finished but not moved (0)" in kyo("brief").out
    assert "0 record(s) finished but not moved (NOT ready)" in kyo("next").out


# -------------------------------------------------------------------- KYO-173


def test_running_the_named_remedy_clears_the_finished_but_not_moved_class(
    kyo, git_project
) -> None:
    """KYO-173.c1, and the whole of the defect in one test.

    The class is derived from the dispatch and the criteria and never asks what
    status the record is in now — which is right, and is why the *remedy* has to
    ask. It used to say `verified` at every rung, so a controller who ran it on a
    record already at `verified` got ``moved``, got ``verified -> verified``, and
    got the record back in the list on the next brief. Asserted by running exactly
    what the surface prints, twice, and requiring the class to be empty after.
    """
    record = _finished_but_not_moved(kyo, git_project)

    for _ in range(2):
        remedy = kyo("brief", "--json").json["settled_not_moved"]
        if not remedy:
            break
        command = remedy[0]["needs"].split()
        assert command[0] == "kyo"
        assert kyo(*command[1:]).code == 0, remedy[0]["needs"]

    assert kyo("brief", "--json").json["settled_not_moved"] == []
    assert kyo("next", "--json").json["settled_not_moved"] == []
    assert kyo("show", record, "--json").json["status"] == "done"


def test_the_remedy_names_the_completion_move_once_the_record_is_verified(
    kyo, git_project
) -> None:
    """The half a controller meets second. At `verified` the only outstanding move
    is the ladder's completion status, and naming the rung the record already
    occupies is what made this class unclearable."""
    record = _finished_but_not_moved(kyo, git_project)
    assert kyo("move", record, "verified").code == 0

    settled = kyo("brief", "--json").json["settled_not_moved"]
    assert [item["id"] for item in settled] == [record]
    assert settled[0]["needs"] == f"kyo move {record} done"
    assert f"kyo move {record} done" in kyo("brief").out
    assert f"kyo move {record} done" in kyo("next").out


def test_a_move_to_the_status_the_record_already_holds_reports_no_transition(kyo) -> None:
    """The smaller defect underneath, and the one that let the first hide: a
    self-move fires no transition contract, changes nothing, and used to print
    ``moved`` — the same word a working command prints. It stays exit 0 and
    idempotent; what it may not do is claim a transition it did not make."""
    record = new_record(kyo, kind="bug", title="Already where it is", criteria=["a"])
    assert kyo("move", record, "working").code == 0
    before = kyo("show", record, "--json").json["updated_at"]

    result = kyo("move", record, "working")
    assert result.code == 0, result
    assert "moved" not in result.out
    assert "working -> working" not in result.out
    assert "already working" in result.out

    assert kyo("move", record, "working", "--json").json["changed"] is False
    assert kyo("show", record, "--json").json["updated_at"] == before, (
        "a no-op move touched the record"
    )


def test_a_real_move_still_reports_the_transition_it_made(kyo) -> None:
    """The mechanism above must not swallow a genuine move — the test that fails
    if the no-op branch is ever widened to cover one."""
    record = new_record(kyo, kind="bug", title="Actually moving", criteria=["a"])

    result = kyo("move", record, "working")
    assert result.code == 0
    assert f"moved {record}" in result.out
    assert "open -> working" in result.out
    assert kyo("move", record, "built", "--json").json["changed"] is True


# ------------------------------------------------------------------------- KYO-267
#
# The third class the brief could not name. `_awaiting_verification` keys on the
# status `built`, so a record left at `open` with one criterion at `unmet-unobserved`
# was in no section of this document: not dispatchable-with-a-reason, not gated, not
# awaiting verification. The only place the ask lived was a shift note, so the person
# had to be asked by hand (KYO-264.c3, KYO-265.c3).
#
# The section is beside "built, awaiting verification" because it is the same shape one
# step further along: the looking has been asked for, in a criterion's own reason, and
# nothing on the board carries the ask.

_UNOBSERVED_HEADING = "unobserved, waiting on a person, ungated"
_NEXT_HEADING_TAIL = "record(s) with an unobserved criterion and no gate:"


def _waiting_on_a_person(
    kyo,
    *,
    title: str = "Waiting on somebody to look",
    reason: str = "the maintainer has not watched the running app yet",
) -> str:
    """A dispatchable record with one criterion `unmet-unobserved` and no gate."""
    record = new_record(kyo, kind="bug", title=title, criteria=["the panel refreshes"])
    assert (
        kyo(
            "verify",
            f"{record}.c1",
            "--verdict",
            "unmet-unobserved",
            "--method",
            "reasoned-about",
            "--why",
            reason,
        ).code
        == 0
    )
    return record


def _unobserved_ids(kyo, command: str = "brief") -> set[str]:
    return {item["id"] for item in kyo(command, "--json").json["ungated_unobserved"]}


def test_the_brief_gives_an_unobserved_ungated_criterion_its_own_section(kyo) -> None:
    """KYO-267.c2: the record appears, under its own heading, beside the built queue."""
    record = _waiting_on_a_person(kyo)
    out = kyo("brief").out
    body = _section(out, _UNOBSERVED_HEADING)
    assert any(record in line for line in body), out
    assert _unobserved_ids(kyo) == {record}


def test_the_section_carries_the_criterion_id_and_its_reason(kyo) -> None:
    """The reason is what the person is being asked to observe, so the controller
    composing the gate needs it here rather than one `kyo show` away."""
    record = _waiting_on_a_person(kyo, reason="watch the badge after a cold start")
    body = "\n".join(_section(kyo("brief").out, _UNOBSERVED_HEADING))
    assert f"{record}.c1" in body
    assert "watch the badge after a cold start" in body
    assert f'kyo gate {record} awaiting:human-run --do "' in body


def test_the_section_names_the_remedy_even_when_it_is_empty(kyo) -> None:
    """KYO-267.c2's empty-state clause, and KYO-48's reasoning for printing at zero:
    a heading that appears only when occupied teaches a reader that its absence means
    nothing was checked."""
    out = kyo("brief").out
    body = "\n".join(_section(out, _UNOBSERVED_HEADING))
    assert f"{_UNOBSERVED_HEADING} (0):" in out
    assert "(none)" in body
    assert 'kyo gate <id> awaiting:human-run --do "..."' in body


def test_gating_the_record_drops_it_from_the_section_and_into_gated_work(kyo) -> None:
    """KYO-267.c2's first remedy, proved to clear the surface it covers: the gate IS
    the fix, so the class has to let go the moment one exists."""
    record = _waiting_on_a_person(kyo)
    assert _unobserved_ids(kyo) == {record}

    assert (
        kyo(
            "gate",
            record,
            "awaiting:human-run",
            "--do",
            "watch the panel refresh after a restart",
        ).code
        == 0
    )
    assert _unobserved_ids(kyo) == set()
    gated = _section(kyo("brief").out, "gated work")
    assert any(record in line for line in gated), gated


def test_verifying_the_criterion_met_drops_it_from_both(kyo) -> None:
    """KYO-267.c2's second remedy: the person looked, so there is no wait left to
    make findable and nothing to gate."""
    record = _waiting_on_a_person(kyo)
    verify_criterion(kyo, f"{record}.c1", "met", method="human-observed")
    assert _unobserved_ids(kyo) == set()
    assert _unobserved_ids(kyo, "next") == set()
    assert not any(
        record in line for line in _section(kyo("brief").out, _UNOBSERVED_HEADING)
    )


def test_a_running_dispatch_keeps_the_record_out_of_the_section(kyo, git_project) -> None:
    """Until the worker stops, `unmet-unobserved` is the honest state of something
    they are about to observe."""
    record = _waiting_on_a_person(kyo)
    assert kyo("dispatch", record, "--json").code == 0
    assert _unobserved_ids(kyo) == set()


def test_kyo_next_and_kyo_brief_cannot_disagree_about_the_class(kyo) -> None:
    """KYO-87.c3's standard, applied to the new class: one computation, two
    renderings. A record one surface names and the other hides is the two surfaces
    disagreeing about the same row."""
    record = _waiting_on_a_person(kyo)
    assert _unobserved_ids(kyo, "next") == _unobserved_ids(kyo, "brief") == {record}
    next_out = kyo("next").out
    heading = next(
        line for line in next_out.splitlines() if line.endswith(_NEXT_HEADING_TAIL)
    )
    body = "\n".join(_section(next_out, heading))
    assert record in body
    assert f"{record}.c1" in body
    assert f"kyo gate {record} awaiting:human-run" in body


def test_the_section_survives_compact_mode(kyo) -> None:
    """`--compact` is what the `SessionStart` hook injects, so a class only the long
    rendering carries is a class the controller never sees at session start — which
    is exactly when the ask is still actionable. With an item waiting, the remedy
    sentence prints under compact too (KYO-267; KYO-419.c6 drops it only when the
    section is empty)."""
    record = _waiting_on_a_person(kyo)
    out = kyo("brief", "--compact").out
    assert f"{_UNOBSERVED_HEADING} (1):" in out
    assert record in out
    assert 'kyo gate <id> awaiting:human-run --do "..."' in out
    assert 'kyo gate <id> awaiting:human-run --do "..."' in kyo("brief").out
