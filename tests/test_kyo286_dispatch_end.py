"""KYO-286: when a dispatch finished, and which of three sources says so.

`dispatches.closed_at` is written by `kyo dispatch close` and by nothing else, and a
dispatch that simply reported is usually never closed: it reads "closed: (not closed)"
while `reported` indefinitely. So the activity JSON carried no end time for such a row
at all, the desktop fell back to "now minus created", and a finished dispatch showed a
run time and a finished-when that both kept growing, measured from the start to now
rather than from the start to the end.

WHAT THIS FILE PINS:

* the PRECEDENCE, which is the whole of the fix: the latest matched worker stop, else
  the report's ingest time, else `closed_at`. A close and an ingest are a
  CONTROLLER's acts and can land hours after the worker stopped, so neither of them
  outranks the stop (:func:`test_a_matched_stop_outranks_both_the_ingest_and_the_close`);
* a RUNNING dispatch has no end time, deliberately. The ~32s SubagentStop tick
  (KYO-163) writes a matched stop every period while a worker runs, so "the latest
  matched stop" for a running dispatch is a timestamp from moments ago, and freezing
  a clock on it would stop the clock on the one row the activity view exists for
  (:func:`test_a_running_dispatch_has_no_end_time_however_many_stops_it_has`);
* the answer is never a bare timestamp: :attr:`DispatchEnd.source` names which of the
  three it is and :attr:`DispatchEnd.why` says it in a sentence, on every branch
  including the two that answer ``None``;
* and it all reaches `kyo worktrees --json`, over a real store, through the real hook
  (:func:`test_a_reported_but_never_closed_dispatch_stops_its_clock_end_to_end`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei.contracts import (
    DISPATCH_STATE_REPORTED,
    DISPATCH_STATE_RUNNING,
    DISPATCH_STATE_TERMINATED_NO_REPORT,
)
from kyosei.stop_audit import StopAudit, StopAuditLedger
from kyosei.stop_target import STOP_TARGET_MATCHED, STOP_TARGET_UNRESOLVED
from kyosei.worker_tokens import (
    DISPATCH_END_CLOSED_AT,
    DISPATCH_END_REPORT_INGEST,
    DISPATCH_END_WORKER_STOP,
    dispatch_end,
)

from .test_kyo284_worker_tokens import _worker_transcript
from .test_subagent_stop_worktree import _payload, _running_dispatch

STOPPED_AT = "2026-09-17T09:00:00.000Z"
INGESTED_AT = "2026-09-17T11:30:00.000Z"
CLOSED_AT = "2026-09-18T08:00:00.000Z"


def _stop(
    dispatch_id: str, at: str, *, event: str, outcome: str = STOP_TARGET_MATCHED
) -> StopAudit:
    return StopAudit(
        event_id=event,
        at=at,
        outcome=outcome,
        directory="/some/worktree",
        why="the prompt names that dispatch id",
        dispatch_id=dispatch_id if outcome == STOP_TARGET_MATCHED else None,
        candidates=[dispatch_id],
    )


def _end(ledger: StopAuditLedger, **overrides):
    """:func:`dispatch_end` for a finished dispatch, with every source available."""
    kwargs = {
        "dispatch_id": "KYO-1.d1",
        "state": DISPATCH_STATE_REPORTED,
        "report_id": "KYO-1.r1",
        "report_ingested_at": INGESTED_AT,
        "closed_at": CLOSED_AT,
    }
    kwargs.update(overrides)
    return dispatch_end(ledger, **kwargs)


# ------------------------------------------------------------------ the precedence


def test_a_matched_stop_outranks_both_the_ingest_and_the_close() -> None:
    """c1's ordering, with all three present and the two later ones losing.

    The ingest is 2.5 hours after the stop and the close is a day after it, which is
    the ordinary shape: a controller ingests a report when it gets to it and closes a
    dispatch later still. The stop is the only one of the three that is the WORKER
    stopping, so it wins however much earlier it is.
    """
    ledger = StopAuditLedger(audits=[_stop("KYO-1.d1", STOPPED_AT, event="KYO-E7")])

    end = _end(ledger)

    assert end.at == STOPPED_AT
    assert end.source == DISPATCH_END_WORKER_STOP
    assert "KYO-E7" in end.why


def test_the_latest_matched_stop_is_the_one_taken_not_the_first() -> None:
    """The ~32s tick (KYO-163) writes one matched row per period while a worker runs.

    So the FIRST matched stop is roughly when the worker was first noticed and the
    LAST is when it was last seen alive — within one tick of when it really stopped.
    Taking the first would report a run time a worker's whole session too short.
    """
    ledger = StopAuditLedger(
        audits=[
            _stop("KYO-1.d1", "2026-09-17T08:00:00.000Z", event="KYO-E1"),
            _stop("KYO-1.d1", "2026-09-17T08:32:00.000Z", event="KYO-E2"),
            _stop("KYO-1.d1", STOPPED_AT, event="KYO-E3"),
        ]
    )

    assert _end(ledger).at == STOPPED_AT


def test_another_dispatchs_stops_are_not_this_ones_end() -> None:
    """A matched row belongs to exactly one dispatch, and an UNRESOLVED one to none.

    The unresolved row below names this dispatch as a candidate, which is precisely
    the row that must not be read as its end: "a stop that could have been this one"
    is what KYO-134 records so a human can look, not an attribution.
    """
    ledger = StopAuditLedger(
        audits=[
            _stop("KYO-2.d1", "2026-09-17T23:00:00.000Z", event="KYO-E1"),
            _stop(
                "KYO-1.d1",
                "2026-09-17T23:30:00.000Z",
                event="KYO-E2",
                outcome=STOP_TARGET_UNRESOLVED,
            ),
        ]
    )

    end = _end(ledger)

    assert end.at == INGESTED_AT
    assert end.source == DISPATCH_END_REPORT_INGEST
    assert "KYO-1.r1" in end.why


def test_the_close_is_the_last_resort_and_says_so() -> None:
    """Nothing stopped on it and it carries no report: `closed_at` is all there is.

    That is the `terminated-no-report` shape — a dispatch closed by hand after its
    worker's machine died — and it is the one case the old behaviour got right.
    """
    end = _end(
        StopAuditLedger(),
        state=DISPATCH_STATE_TERMINATED_NO_REPORT,
        report_id=None,
        report_ingested_at=None,
    )

    assert end.at == CLOSED_AT
    assert end.source == DISPATCH_END_CLOSED_AT
    assert "kyo dispatch close" in end.why


def test_a_report_id_with_no_readable_ingest_time_falls_through_to_the_close() -> None:
    """`report_ingest_times` answers empty for a table it could not read (law 6).

    A row that then claimed the ingest time it does not have would be inventing one;
    it falls through to the close instead, and the source says which it took.
    """
    end = _end(StopAuditLedger(), report_ingested_at=None)

    assert end.at == CLOSED_AT
    assert end.source == DISPATCH_END_CLOSED_AT


def test_a_finished_dispatch_none_of_the_three_can_date_says_so_rather_than_now() -> None:
    """Law 6: the answer is ``None`` with a sentence, never a timestamp standing in.

    Substituting `now()` for an unknown end is the whole defect this record is about,
    so the one place that could reintroduce it answers nothing at all.
    """
    end = _end(StopAuditLedger(), report_id=None, report_ingested_at=None, closed_at=None)

    assert end.at is None
    assert end.source is None
    assert "nothing in this store records when this dispatch finished" in end.why


# ------------------------------------------------- the running row's clock keeps going


def test_a_running_dispatch_has_no_end_time_however_many_stops_it_has() -> None:
    """c1: "a running dispatch's clock still runs", and the tick is why it must.

    Claude Code delivers `SubagentStop` on a ~32s timer per running subagent
    (KYO-163), each one matched and recorded, so a running dispatch accumulates
    matched stops for as long as it runs. Reading the latest of those as an end time
    would freeze the clock on every running row within half a minute of it starting
    — which is the row the whole activity view exists for.
    """
    ledger = StopAuditLedger(
        audits=[
            _stop("KYO-1.d1", "2026-09-18T12:00:00.000Z", event="KYO-E1"),
            _stop("KYO-1.d1", "2026-09-18T12:00:32.000Z", event="KYO-E2"),
        ]
    )

    end = _end(ledger, state=DISPATCH_STATE_RUNNING, report_id=None, report_ingested_at=None)

    assert end.at is None
    assert end.source is None
    assert "still running" in end.why


# ------------------------------------------- end to end, over a real store and hook


def test_a_reported_but_never_closed_dispatch_stops_its_clock_end_to_end(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The whole path, with nothing hand-written into the store.

    A dispatch is created, a `SubagentStop` is run over a transcript the way Claude
    Code runs it, a report is ingested, and the dispatch is never closed — which is
    the shape KYO-286 was seen on. `kyo worktrees --json` then carries an end time
    that is the stop, a source that names it, and the `created_at` the run time is
    measured from; `closed_at` stays null, which is exactly why the old row grew.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-end")
    record_id = dispatch_id.split(".")[0]
    agent = "a1b2c3d4e5f6a7b8c"
    prompt = f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    parent = _worker_transcript(tmp_path, agent, prompt, messages=2)
    # The guard exits 2 on a dirty worker tree, which is not this test's subject: the
    # RECORDED resolution is written either way, and that row is what stops the clock.
    stop = kyo("check-clean", "--subagent-stop", stdin=_payload(parent, git_project, agent))
    assert stop.code in (0, 2), stop

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    body = {
        "dispatch_id": dispatch_id,
        "record_id": record_id,
        "worked_from_sha": head,
        "summary": "a report, ingested, with the dispatch left open exactly as usual",
        "commits": [],
        "touched_files": [],
        "premise_false": [],
        "disagreements": [],
        "corrected_numbers": [],
        "criteria_verdicts": [],
    }
    report_file = tmp_path / "end-report.json"
    report_file.write_text(json.dumps(body), encoding="utf-8")
    ingested = kyo("report", dispatch_id, "--file", str(report_file))
    assert ingested.code == 0, ingested

    listed = kyo("worktrees", "--json")
    assert listed.code == 0, listed
    rows = [row for row in listed.json["worktrees"] if row["dispatch_id"] == dispatch_id]
    assert rows, listed.json["worktrees"]
    row = rows[0]

    # Never closed -- the state the growing clock was read off.
    assert row["closed_at"] is None
    # And yet it has an end, and the end is the worker's stop rather than the ingest.
    assert row["finished_at_source"] == DISPATCH_END_WORKER_STOP
    assert row["finished_at"], row
    assert row["finished_at_why"]
    # The start of the run time, which this listing did not carry before (KYO-292).
    assert row["created_at"]
    assert row["created_at"] <= row["finished_at"]
    assert str(tree) == row["worktree_resolved"]
