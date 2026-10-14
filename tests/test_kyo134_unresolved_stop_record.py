"""KYO-134: an unresolved ``SubagentStop`` says nothing was checked, and only to a
transcript.

KYO-128 gave the ``SubagentStop`` guards three named outcomes and got two of them
onto solid ground. The third, ``unresolved`` — declared worktrees ARE running and
this stop matched none of them — exits 0, names the tree it read, names the
candidate dispatches, and prints that nothing was checked. That is the right
verdict and this file does not reopen it (see :func:`test_an_unresolved_stop_still
_exits_zero_and_still_does_not_block`, which is the boundary).

What KYO-128 named as its own known limitation is the whole subject here: on exit 0
neither stdout nor stderr is fed back to any agent, so that warning lands only in
the hook's own output in the CONTROLLER's transcript. It is addressed to the one
party who can act on it, nothing obliges them to read it, and by the next session it
is gone. Same shape as the defect KYO-128 fixed — a check whose result does not
reach the party who can act on it — one step milder.

So the fix is storage before it is display: `kyo check-clean` wrote nothing at all.
Every test below is about a fact surviving the process that produced it.

KYO-418.c6 changed the FORM of that storage and nothing about its durability. An
unresolved stop was one events-ledger row each -- nearly half of this repository's
events, almost all of them a controller's own Explore and research agents -- and is
now a count on each running candidate dispatch's own row
(`tick_audit_json["unresolved"]`, with the stop's reason code), exported with that
record, exactly as KYO-375 counts ticks. The tests below pin the same four facts
against the counter: it outlives the transcript and a rebuild, it names the
candidate, doctor reports it while the candidate runs, and a failed write is loud.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import event_lines
from .test_subagent_stop_worktree import (
    CONTROLLER_EDIT,
    _running_dispatch,
    _stop,
)


@pytest.fixture
def controller_dirty(git_project: Path) -> Path:
    """The controller's checkout, dirty throughout — as in KYO-128's own tests.

    Re-declared rather than imported: a pytest fixture is registered by the module
    that defines it, and importing the function would give a plain callable.
    """
    (git_project / CONTROLLER_EDIT).write_text("edited by the controller\n", encoding="utf-8")
    return git_project


def _unresolved_stop(kyo, tmp_path: Path, controller: Path, name: str) -> str:
    """One stop that cannot be matched, while a dispatch DOES declare a worktree.

    Returns the running dispatch's id — the candidate the stop could have been.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, name)
    result = _stop(kyo, "check-clean", tmp_path, controller, None, "--json")
    assert result.code == 0, result
    assert result.json["subject"]["outcome"] == "unresolved", result.json
    return dispatch_id


def _unresolved_counted(kyo, dispatch_id: str) -> dict | None:
    """The unresolved-stop counter on one dispatch, read from `kyo worktrees --json`."""
    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    return listing.json["stop_audits"]["unresolved_counted"].get(dispatch_id)


def _stop_audit_events(project: Path) -> list[dict]:
    """Every recorded stop audit, read from the GIT-CANONICAL ledger on disk.

    Deliberately not read through the database: "durable" here means the fact
    survives the process, the session and a `kyo rebuild`, and the export is what
    carries it. A test that only queried sqlite would pass over a build that wrote
    the row and never exported it.
    """
    out = []
    for event in event_lines(project):
        if (event.get("after") or {}).get("stop_audit"):
            out.append(event)
    return out


# ------------------------------------------------- c1: it outlives the transcript


def test_an_unresolved_stop_leaves_a_durable_record(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c1. The stop's own output is the transcript; this is what remains.

    Before this, `kyo check-clean` wrote nothing anywhere. The warning existed for
    exactly as long as the controller's scrollback did.
    """
    dispatch_id = _unresolved_stop(kyo, tmp_path, controller_dirty, "wt-durable")

    # KYO-418.c6: counted on the candidate's own row, not appended as a ledger row.
    assert _stop_audit_events(controller_dirty) == []
    counted = _unresolved_counted(kyo, dispatch_id)
    assert counted is not None and counted["count"] == 1, counted
    assert counted["reasons"] == {"prompt-names-none": 1}, counted
    # Durable means in the git-canonical export: the dispatch's record document.
    record = dispatch_id.rsplit(".", 1)[0]
    document = json.loads(
        (controller_dirty / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    exported = next(d for d in document["dispatches"] if d["id"] == dispatch_id)
    assert exported["tick_audit"]["unresolved"]["count"] == 1, exported["tick_audit"]


def test_the_record_survives_a_rebuild_from_the_export(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c1, the half that makes "durable" mean anything.

    ``kyosei.db`` is gitignored and rebuilt from the export; a row that lived only
    in the database would be gone at the next `kyo rebuild`, which is a routine
    operation and not a disaster. The export ledger is the copy that lasts.
    """
    dispatch_id = _unresolved_stop(kyo, tmp_path, controller_dirty, "wt-rebuilt")

    assert kyo("rebuild", "--force").code == 0
    counted = _unresolved_counted(kyo, dispatch_id)
    assert counted is not None and counted["count"] == 1, counted


# ----------------------------------------------------- c2: it names the candidates


def test_the_record_names_the_stops_candidate_dispatches(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c2. Without this the warning is unactionable by construction.

    An unresolved stop is precisely one that could not say WHICH worktree it was
    about. The candidates are the whole of what it did know, so a record that drops
    them preserves the alarm and discards the only thing a controller could act on.
    """
    dispatch_id = _unresolved_stop(kyo, tmp_path, controller_dirty, "wt-candidates")

    # KYO-418.c6: the candidate IS where the count lives, so it cannot be dropped.
    assert _unresolved_counted(kyo, dispatch_id)["count"] == 1

    listing = kyo("worktrees")
    assert listing.code == 0, listing
    assert dispatch_id in listing.out, "the controller cannot tell which worktree went unaudited"


# -------------------------------------- c3: on a surface a controller already reads


def test_doctor_reports_an_unaudited_stop_while_its_dispatch_is_still_running(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c3. `kyo doctor` runs from this project's Stop and SessionEnd hooks.

    That is the whole test: no new command, nothing a controller has to be told to
    run. A `kyo unaudited-stops` would have satisfied c1 and c2 and changed nothing
    about who finds out.

    KYO-214 changed the EXIT CODE and nothing else here: this is a notice, so `kyo
    doctor` reports it and exits 0. "Reports" is what c3 asked for and is what is
    asserted below — the finding, its count, and the implicated dispatch, in full, on
    the surface a controller already reads. It was exiting 3, which rendered the whole
    Stop hook as an error block on every stop for the life of every dispatch.
    """
    dispatch_id = _unresolved_stop(kyo, tmp_path, controller_dirty, "wt-doctor")

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    failures = result.json["checks"]["unresolved_stops"]["failures"]
    assert failures, result.json["checks"]["unresolved_stops"]
    assert dispatch_id in failures[0]
    assert "NOTHING WAS CHECKED" in failures[0]
    assert result.json["notices"] == [f"[unresolved_stops] {failures[0]}"], result.json["notices"]

    printed = kyo("doctor")
    assert printed.code == 0, printed
    assert "NOTHING WAS CHECKED" in printed.out, (
        "the finding stopped reaching the controller it was written for"
    )


def test_doctor_clears_once_the_candidate_dispatches_are_no_longer_running(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The bound on c3, and a deliberate limit rather than a gap.

    The remedy this check names is "look at that worktree before merging its
    branch". Once no candidate is running the controller has necessarily handled
    the branch, so a doctor that kept failing would be unclearable — and an
    unclearable doctor is one that stops being read, which would put the finding
    back where KYO-134 found it. The record itself is permanent; only the failure
    clears, and `kyo worktrees` still shows it.
    """
    dispatch_id = _unresolved_stop(kyo, tmp_path, controller_dirty, "wt-cleared")
    closed = kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "the worker crashed"
    )
    assert closed.code == 0, f"fixture could not terminate the dispatch: {closed}"

    doctored = kyo("doctor", "--json")
    assert doctored.json["checks"]["unresolved_stops"]["failures"] == [], (
        "the failure did not clear once no candidate dispatch was still running"
    )
    assert _unresolved_counted(kyo, dispatch_id)["count"] == 1, (
        "the durable record was dropped along with the doctor failure"
    )


# ------------------------------------------- c4: the line KYO-128 drew, still drawn


def test_an_unresolved_stop_still_exits_zero_and_still_does_not_block(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c4 — a boundary, not a feature.

    KYO-128 argued not-blocking from measured harm: the paths an unresolved stop
    would cite belong to the CONTROLLER's branch, which the woken worker is
    forbidden to commit, so a block there wakes the one party powerless to clear
    it — ~229,000 tokens on one dispatch that woke three times against it. Adding a
    durable record must not smuggle a block back in. The controller's checkout is
    dirty here, which is exactly the state that used to block.
    """
    _running_dispatch(kyo, tmp_path, "wt-noblock")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert result.code == 0, result
    assert result.json["dirty_entries"], "the fixture's controller edit should have been seen"
    assert result.json["checked_the_worker_tree"] is False
    assert CONTROLLER_EDIT not in result.err


def test_a_stop_still_exits_zero_when_the_record_cannot_be_written(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c4 under the failure that makes it hardest, and the one to expect.

    A database behind its export is an ORDINARY state for a second checkout after a
    pull, and ``Store._refuse_if_behind_export`` refuses every write
    transaction in it (KYO-75). A SubagentStop hook that let that refusal escape
    would turn a routine pull into a blocked stop for every worker — a store
    condition punishing workers, which is the exact class of harm KYO-128 measured.

    So the write is best-effort and the failure is reported rather than raised. It
    must still be reported: a durable record that silently did not happen is worse
    than no durable record, because the controller now believes one exists.
    """
    _running_dispatch(kyo, tmp_path, "wt-unwritable")
    allocations = controller_dirty / "kyosei" / "export" / "_allocations.json"
    counters = json.loads(allocations.read_text(encoding="utf-8"))
    counters["event"] = counters.get("event", 0) + 5000
    allocations.write_text(json.dumps(counters, indent=2), encoding="utf-8")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert result.code == 0, result
    assert result.json["stop_audit_event"] is None
    # KYO-418.c6: the count is the record now, and a count that did not happen is as
    # loud as a row that did not.
    assert "NOT COUNTED" in (result.json["stop_audit"] or ""), result.json["stop_audit"]
    assert "NOT COUNTED" in result.out + result.err, (
        "a record that did not happen must never be silent"
    )


# ------------------------- c5: absence of evidence is not evidence of absence


def test_a_store_with_no_stop_audits_says_so_rather_than_rendering_nothing(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c5, the state most likely to be built wrong.

    An empty section reads exactly like a check that never ran. This project has
    learned that three times — ``governs_staleness``'s ``cannot-tell``, KYO-129's
    ``no-documents``, KYO-149's whole subject — so a store that has recorded
    nothing must SAY it has recorded nothing, and must not say that no stop went
    unaudited.
    """
    result = kyo("worktrees", "--json")
    assert result.code == 0, result
    sentence = result.json["stop_audits"]["sentence"]
    assert result.json["stop_audits"]["recorded"] == 0
    assert "none recorded" in sentence
    assert "NOT the same as" in sentence, sentence
    assert sentence in result.out, "the distinction exists in the data but not for a reader"


def test_a_store_whose_stops_all_resolved_says_that_instead(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134.c5's other half, and the reason ``matched`` stops are recorded at all.

    "Nothing went unaudited" is only sayable when something is known to have been
    audited. Recording only the failures would make the empty case ambiguous
    forever: no rows would mean both "every stop was fine" and "nothing ever looked".
    A resolved stop is the evidence that the recorder ran.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-allgood")
    assert _stop(kyo, "check-clean", tmp_path, controller_dirty, dispatch_id).code == 0

    result = kyo("worktrees", "--json")
    audits = result.json["stop_audits"]
    assert audits["recorded"] == 1, audits
    assert audits["unresolved"] == []
    assert "no stop is known to have gone unaudited" in audits["sentence"]
    assert "none recorded" not in audits["sentence"], (
        "a store that HAS audited stops must not read like one that never has"
    )


def test_a_stop_with_no_worktree_in_play_is_not_recorded_and_says_why(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The bound on what gets written, stated as a test so it cannot drift quietly.

    With no running dispatch declaring a worktree, the project root IS every
    dispatch's worktree: there is nothing that could have gone unaudited, and a row
    per stop would put every ordinary research subagent in a git-canonical ledger.
    That stop still blocks on the controller's dirty tree — KYO-128 settled that —
    and the guard says out loud that it recorded nothing, rather than leaving a
    reader to infer it from an absence.
    """
    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 2, result
    assert "not recorded" in result.err, result.err
    assert _stop_audit_events(controller_dirty) == []
