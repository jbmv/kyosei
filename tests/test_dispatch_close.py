"""KYO-7.c5 / law 8: `kyo dispatch close` — refuses without a report unless one of
the two report-less states is named explicitly; a dispatch never renders as anything
that reads like "done" without a report.

KYO-345 adds the second of those states, and the bottom half of this file is about
what makes it a different fact rather than a synonym. `--terminated-no-report` means
the work STOPPED: a worker was spawned and died, and the session brief tells the next
controller to flag it. A dispatch frozen with the wrong model and never handed to
anybody is not that — but a frozen brief is immutable by design, so until this record
there was no exit from `running` that did not enter a death into the store. A consumer
project's controller met it: the only way out was `kyo dispatch close
--terminated-no-report`, a status that means the work died midway and that the brief
tells the next controller to flag.

So the tests below check the two halves separately. That `--withdrawn` WORKS (c1) is
the small half; that it is not loud ANYWHERE (c2) is the half the record is about, and
it is asserted against the actual rendered surfaces — `kyo brief`, `kyo show`, the
close echo — rather than against the stored value, because a state that reads as a
death on any one of them has not solved the problem it was filed for.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.contracts import DISPATCH_STATE_WITHDRAWN
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR, EXIT_USAGE

from .conftest import git_worktree, new_record, valid_report


def _dispatch(kyo, record: str) -> dict:
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    return result.json


def _promote(kyo, project: Path, tmp_path: Path, name: str, touch: str) -> str:
    """A dispatchable record declaring exactly one touch, for the claim tests."""
    idea = project / "ideas" / f"{name}.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text(f"# Task {name}\n\nDoes a thing.\n", encoding="utf-8")
    touches = tmp_path / f"touches-{name}.txt"
    touches.write_text(f"{touch}\n", encoding="utf-8")
    criteria = tmp_path / f"criteria-{name}.txt"
    criteria.write_text("done\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", str(criteria), "--touches-file", str(touches), "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_close_refuses_a_running_dispatch_with_no_report(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo("dispatch", "close", d["dispatch_id"])
    assert result.code == EXIT_CONTRACT
    assert "terminated-no-report" in result.err
    assert "--why" in result.err

    still_running = kyo("show", record, "--json").json["dispatches"][0]
    assert still_running["state"] == "running"


def test_close_with_terminated_no_report_requires_why(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo("dispatch", "close", d["dispatch_id"], "--terminated-no-report")
    assert result.code == EXIT_CONTRACT
    assert "--why" in result.err


def test_close_with_terminated_no_report_and_why_succeeds(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo(
        "dispatch", "close", d["dispatch_id"],
        "--terminated-no-report", "--why", "worker crashed, no report recovered",
        "--json",
    )
    assert result.code == 0, result
    assert result.json["state"] == "terminated-no-report"

    shown = kyo("show", record, "--json").json["dispatches"][0]
    assert shown["state"] == "terminated-no-report"
    assert shown["terminated_why"] == "worker crashed, no report recovered"
    assert shown["closed_at"]
    assert shown["report_id"] is None


def test_dispatch_never_renders_as_done_without_a_report(kyo, git_project) -> None:
    """The literal string 'done' must never appear as this dispatch's state,
    whether running, terminated, or after close."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    running = kyo("show", record, "--json").json["dispatches"][0]
    assert running["state"] in {"running", "reported", "terminated-no-report"}
    assert running["state"] != "done"

    kyo("dispatch", "close", d["dispatch_id"], "--terminated-no-report", "--why", "abandoned")
    closed = kyo("show", record, "--json").json["dispatches"][0]
    assert closed["state"] == "terminated-no-report"
    assert closed["state"] != "done"
    assert "done" not in closed["state"]


def test_close_succeeds_on_a_reported_dispatch_without_terminated_flag(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"]
    )
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    kyo("report", d["dispatch_id"], "--file", str(path))

    result = kyo("dispatch", "close", d["dispatch_id"], "--json")
    assert result.code == 0, result
    assert result.json["state"] == "reported"

    shown = kyo("show", record, "--json").json["dispatches"][0]
    assert shown["closed_at"]
    assert shown["state"] == "reported"


def test_close_refuses_terminated_no_report_on_an_already_reported_dispatch(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"]
    )
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    kyo("report", d["dispatch_id"], "--file", str(path))

    result = kyo(
        "dispatch", "close", d["dispatch_id"], "--terminated-no-report", "--why", "nope"
    )
    assert result.code == EXIT_ERROR


def test_close_refuses_a_second_close(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    first = kyo(
        "dispatch", "close", d["dispatch_id"], "--terminated-no-report", "--why", "abandoned"
    )
    assert first.code == 0, first
    second = kyo(
        "dispatch", "close", d["dispatch_id"], "--terminated-no-report", "--why", "abandoned again"
    )
    assert second.code == EXIT_CONTRACT
    assert "already" in second.err


def test_close_needs_a_dispatch_id(kyo, project: Path) -> None:
    result = kyo("dispatch", "close")
    assert result.code == EXIT_USAGE
    assert "dispatch id" in result.err


# --------------------------------------------------------------------- KYO-345.c1


def test_close_with_withdrawn_and_why_succeeds(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo(
        "dispatch", "close", d["dispatch_id"],
        "--withdrawn", "--why", "froze it with the wrong model; no worker was spawned",
        "--json",
    )
    assert result.code == 0, result
    assert result.json["state"] == DISPATCH_STATE_WITHDRAWN

    shown = kyo("show", record, "--json").json["dispatches"][0]
    assert shown["state"] == "withdrawn"
    assert shown["terminated_why"] == "froze it with the wrong model; no worker was spawned"
    assert shown["closed_at"]
    assert shown["report_id"] is None


def test_close_with_withdrawn_requires_why(kyo, git_project) -> None:
    """The reason is the whole record of what the mistake was. A withdrawal with no
    account of it is the bare label law 8 refuses — and it is the state a controller
    is most tempted to enter silently, because the mistake is their own."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo("dispatch", "close", d["dispatch_id"], "--withdrawn")
    assert result.code == EXIT_CONTRACT
    assert "--why" in result.err
    assert "withdrawn" in result.err

    still_running = kyo("show", record, "--json").json["dispatches"][0]
    assert still_running["state"] == "running"


def test_close_refuses_withdrawn_on_a_dispatch_that_has_a_report(
    kyo, git_project, tmp_path
) -> None:
    """A dispatch that reported was handed to a worker by definition, so the one
    thing `--withdrawn` asserts about it is false."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    report = valid_report(
        dispatch_id=d["dispatch_id"], record_id=record, worked_from_sha=d["worked_from_sha"]
    )
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    kyo("report", d["dispatch_id"], "--file", str(path))

    result = kyo("dispatch", "close", d["dispatch_id"], "--withdrawn", "--why", "nope")
    assert result.code == EXIT_ERROR
    assert "--withdrawn" in result.err

    shown = kyo("show", record, "--json").json["dispatches"][0]
    assert shown["state"] == "reported"


def test_the_refusal_to_close_a_running_dispatch_names_both_states(kyo, git_project) -> None:
    """KYO-68's half of this: a message that names only the remedy that existed
    first steers every mis-frozen brief into recording a death. Both, with what
    separates them."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo("dispatch", "close", d["dispatch_id"])
    assert result.code == EXIT_CONTRACT
    assert "--terminated-no-report" in result.err
    assert "--withdrawn" in result.err


def test_the_two_close_states_cannot_both_be_named(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo(
        "dispatch", "close", d["dispatch_id"],
        "--terminated-no-report", "--withdrawn", "--why", "which is it",
    )
    assert result.code == EXIT_USAGE
    assert "--withdrawn" in result.err

    still_running = kyo("show", record, "--json").json["dispatches"][0]
    assert still_running["state"] == "running"


def test_withdrawn_beside_a_record_id_is_refused_like_its_sibling(kyo, git_project) -> None:
    """KYO-133 on the new flag: a close-only flag the pre-freeze check does not know
    is one that freezes a SECOND brief for the record the controller was trying to
    stop dispatching."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    result = kyo("dispatch", record, "--withdrawn", "--why", "oops")
    assert result.code == EXIT_USAGE
    assert "--withdrawn" in result.err
    assert d["dispatch_id"] in result.err
    # nothing was frozen
    assert len(kyo("show", record, "--json").json["dispatches"]) == 1


# --------------------------------------------------------------------- KYO-345.c2


def test_a_withdrawn_dispatch_is_not_in_the_briefs_terminated_list(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    kyo("dispatch", "close", d["dispatch_id"], "--withdrawn", "--why", "wrong model, never spawned")

    brief = kyo("brief", "--json")
    assert brief.code == 0, brief
    listed = [row["id"] for row in brief.json["terminated_no_report"]]
    assert d["dispatch_id"] not in listed
    assert d["dispatch_id"] not in [row["id"] for row in brief.json["running_dispatches"]]
    # and not hidden behind the "answered and not shown" count either (KYO-314): it
    # was never a question, so it is not a withheld answer.
    assert brief.json["terminated_no_report_answered_count"] == 0

    text = kyo("brief")
    assert text.code == 0, text
    assert d["dispatch_id"] not in text.out


def test_a_withdrawn_dispatch_never_reads_as_died_mid_work(kyo, git_project) -> None:
    """The word, on every surface that prints this dispatch at all. One column holds
    the reason a dispatch ended with no report; what a reader sees in front of it is
    derived from the state, so "terminated" cannot reach a withdrawal."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    closed = kyo(
        "dispatch", "close", d["dispatch_id"], "--withdrawn", "--why", "wrong model, never spawned"
    )
    assert closed.code == 0, closed
    assert "withdrawn because" in closed.out
    assert "terminated" not in closed.out

    shown = kyo("show", record)
    assert shown.code == 0, shown
    assert "withdrawn because: wrong model, never spawned" in shown.out
    assert "terminated" not in shown.out


def test_a_withdrawn_dispatch_is_still_in_the_records_dispatch_history(kyo, git_project) -> None:
    """Law 4: the attempt happened and its id exists forever. Withdrawing it removes
    it from the controller's attention, never from the record's account of itself."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    kyo("dispatch", "close", d["dispatch_id"], "--withdrawn", "--why", "wrong branch")

    shown = kyo("show", record)
    assert d["dispatch_id"] in shown.out
    assert "withdrawn" in shown.out

    history = kyo("show", record, "--json").json["dispatches"]
    assert [row["id"] for row in history] == [d["dispatch_id"]]
    assert history[0]["state"] == "withdrawn"


def test_withdrawing_releases_the_touch_claim_for_the_next_dispatch(
    kyo, git_project, tmp_path
) -> None:
    a = _promote(kyo, git_project, tmp_path, "a", "kyosei/config.yml")
    b = _promote(kyo, git_project, tmp_path, "b", "kyosei/config.yml")

    first = kyo("dispatch", a, "--json")
    assert first.code == 0, first
    collided = kyo("dispatch", b)
    assert collided.code == EXIT_CONTRACT
    assert first.json["dispatch_id"] in collided.err

    kyo("dispatch", "close", first.json["dispatch_id"], "--withdrawn", "--why", "wrong record")

    freed = kyo("dispatch", b, "--json")
    assert freed.code == 0, freed
    assert not freed.json["collisions_overridden"]


def test_withdrawing_releases_the_worktree_claim_for_the_next_dispatch(
    kyo, git_project, tmp_path
) -> None:
    # Disjoint touches, so the only thing that can refuse the second dispatch is the
    # worktree these two records would share.
    a = _promote(kyo, git_project, tmp_path, "a", "kyosei/config.yml")
    b = _promote(kyo, git_project, tmp_path, "b", "kyosei/kinds.yml")
    worktree = tmp_path / "wt"
    git_worktree(kyo.project, worktree)
    where = str(worktree)

    first = kyo("dispatch", a, "--worktree", where, "--json")
    assert first.code == 0, first
    collided = kyo("dispatch", b, "--worktree", where)
    assert collided.code == EXIT_CONTRACT
    assert first.json["dispatch_id"] in collided.err

    kyo("dispatch", "close", first.json["dispatch_id"], "--withdrawn", "--why", "wrong model")

    freed = kyo("dispatch", b, "--worktree", where, "--json")
    assert freed.code == 0, freed


def test_a_withdrawn_dispatch_passes_doctor(kyo, git_project) -> None:
    """The state is in `contracts.DISPATCH_STATES` and carries its why, so the
    consistency check that would call it an unknown state is green — the check that
    is exactly what an OLDER build would fail on this store, which is why the
    schema_version moved with it (KYO-345.c3)."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    kyo("dispatch", "close", d["dispatch_id"], "--withdrawn", "--why", "never spawned")

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["ok"], result.json


def test_a_withdrawn_dispatch_survives_the_export_rebuild_round_trip(kyo, git_project) -> None:
    """KYO-345.c3's other half: the value is what travels between builds, so it has
    to come back out of `kyosei/export/` as itself."""
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    kyo("dispatch", "close", d["dispatch_id"], "--withdrawn", "--why", "wrong model, never spawned")

    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt

    shown = kyo("show", record, "--json").json["dispatches"][0]
    assert shown["state"] == "withdrawn"
    assert shown["terminated_why"] == "wrong model, never spawned"


def test_a_second_dispatch_of_the_withdrawn_record_is_a_d2(kyo, git_project) -> None:
    """What the consumer actually needed: withdraw the mis-frozen one, dispatch
    again. Law 4 — the id is not reused and the first attempt stays on the record."""
    record = new_record(kyo, criteria=["a"])
    first = _dispatch(kyo, record)
    kyo("dispatch", "close", first["dispatch_id"], "--withdrawn", "--why", "wrong model")

    second = kyo("dispatch", record, "--model", "claude-opus-5", "--json")
    assert second.code == 0, second
    assert second.json["dispatch_id"] != first["dispatch_id"]
    assert second.json["dispatch_id"].endswith(".d2")

    states = {row["id"]: row["state"] for row in kyo("show", record, "--json").json["dispatches"]}
    assert states[first["dispatch_id"]] == "withdrawn"
    assert states[second.json["dispatch_id"]] == "running"
