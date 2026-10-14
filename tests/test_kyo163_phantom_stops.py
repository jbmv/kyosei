"""KYO-163: stop-audit rows naming agent ids that exist nowhere.

KYO-134 made an unresolved ``SubagentStop`` durable. What it then recorded was a
population nobody had measured: 389 rows in one session of this project against 40
agents that were ever spawned, 365 of them ``unresolved``, every one carrying a
distinct, non-null agent id with no spawn sidecar, no subagent transcript and no
appearance as an ``agentId`` anywhere under ``~/.claude/projects``.

WHAT THEY ARE, measured rather than inferred (c1), and written down in full beside
:data:`~kyosei.stop_audit.STOP_TICK_PERIOD_SECONDS`: Claude Code delivers
``SubagentStop`` to the hook ON A ~32-SECOND TIMER, ONCE PER RUNNING SUBAGENT, with
a fresh agent id every tick. The decisive observation was causal rather than
correlational — spawning one extra helper added one more 32-second stream to the
arrivals within 32 seconds, and the stream count follows the agent count.

WHAT THEY ARE NOT, and it is worth knowing this was tested rather than assumed: they
are not ordinary agents whose transcripts Kyosei looks for in the wrong place. The
harness files every nested agent under the controller session's own ``subagents/``
directory whatever its depth — there is no per-agent directory anywhere under
``~/.claude/projects`` — and a helper spawned at ``spawnDepth: 2`` by a dispatched
worker was observed writing its sidecar to exactly the path
:func:`~kyosei.stop_target._dispatch_prompt` searches, resolving, and producing
exactly one row while 17 traceless ones arrived alongside it. That the search path
is right is already pinned by ``test_kyo154_helper_stops.py``, which classifies a
helper by reading ``parentAgentId`` out of a sidecar at that path; this file does not
duplicate it.

WHAT THIS FILE PROTECTS. Two things, and the second matters more than the first:

* the measurement survives, so a recurrence is recognised instead of re-derived
  from transcripts that do not outlive the session (c4);
* and NOTHING is classified on the absence of a trace (c3). KYO-154's dispatch was
  offered "every worker leaves a sidecar, so an agent without one isn't a worker"
  and refused it, correctly — it would silently stop auditing a real worker whose
  sidecar the harness failed to write, which is KYO-128's dangerous half arriving
  by a new route and is the exact case the audit exists for. Every row is still
  written and every row keeps its outcome; the profile annotates a count and
  decides nothing. :func:`test_the_profile_reclassifies_nothing` and
  :func:`test_the_doctor_finding_does_not_shrink_when_the_tick_is_named` are that
  line, and they are the tests to keep if any here are dropped.

KYO-373 REVERSED THE LAST BULLET, narrowly and on the record. Measured on this store:
6,277 of 6,284 unresolved rows were agents with no transcript anywhere, 286 of them
against 2 matched worker stops in one afternoon, and the section listing them was
12,791 lines. A stop whose agent has no transcript, no sidecar and no sidechain row, in
a session whose ``subagents/`` directory DOES hold other agents' transcripts, is now
classified not-a-worker (`STOP_TARGET_NO_TRANSCRIPT`), and a row written before that
with the "no readable transcript" sentence is read the same way. What these tests still
pin is the part that survives: a population is never reclassified by its SHAPE. Their
fixtures therefore carry rows that could be a worker's (:data:`UNMATCHED_WHY` — a
transcript was read and its prompt named no dispatch), and a tick-shaped population of
those is counted in full. The no-transcript rows are pinned in
`tests/test_dispatch_worktree.py` (KYO-373 section).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kyosei import doctor as doctor_module
from kyosei import stop_audit, stop_target, worker_tokens
from kyosei.errors import EXIT_DOCTOR
from kyosei.stop_audit import STOP_TICK_PERIOD_SECONDS, StopAudit, StopAuditLedger
from kyosei.stop_target import STOP_TARGET_MATCHED, STOP_TARGET_UNRESOLVED

from .conftest import event_lines, legacy_unresolved_rows
from .test_kyo134_unresolved_stop_record import (
    _stop_audit_events,
    controller_dirty,  # noqa: F401
)
from .test_subagent_stop_worktree import _git, _payload, _running_dispatch, _stop, _transcript

BASE = datetime(2026, 8, 30, 19, 3, 16, tzinfo=UTC)

#: The ``why`` every tick row has carried: the sentence `_dispatch_prompt` writes when
#: no transcript exists. Since KYO-373 a row carrying it (and no stored reason) is read
#: as not-a-worker.
TICK_WHY = "no readable transcript for that agent"

#: A row that could be a worker's: a transcript WAS read and its prompt named none of
#: the candidates. KYO-373 classifies nothing like this, whatever the population's
#: shape — which is the invariant the tests below that use it keep.
UNMATCHED_WHY = (
    "the stopping agent's prompt names none of the 1 dispatch(es) with a declared "
    "worktree (first user message of /the/session/subagents/agent-a.jsonl)"
)


def _at(offset_seconds: float) -> str:
    """A ledger timestamp, in the shape the store writes them."""
    stamp = BASE + timedelta(seconds=offset_seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def _ledger(
    *,
    rows: int,
    period: float,
    concurrency: int = 1,
    distinct_ids: bool = True,
    outcome: str = STOP_TARGET_UNRESOLVED,
    why: str = TICK_WHY,
) -> StopAuditLedger:
    """A ledger of ``rows`` stops arriving every ``period / concurrency`` seconds.

    ``concurrency`` is how many dispatches each row recorded as running candidates.
    Modelling it explicitly is the point of c4: N agents each ticking once per
    period produce arrivals N times as fast, and a measurement that did not divide
    by it would call the same phenomenon a different number every time the shift
    got busier.
    """
    ledger = StopAuditLedger()
    gap = period / concurrency
    for index in range(rows):
        ledger.audits.append(
            StopAudit(
                event_id=f"KYO-E{index}",
                at=_at(index * gap),
                outcome=outcome,
                directory="/the/controllers/cwd",
                why=why,
                dispatch_id=None,
                candidates=[f"KYO-1.d{n}" for n in range(concurrency)],
                agent_id=(f"a{index:016x}" if distinct_ids else "a0000000000000000"),
            )
        )
    return ledger


# ------------------------------------------------------ c4: the pattern is measured


def test_the_tick_signature_is_recognised_from_the_ledger_alone() -> None:
    """c4. The rows themselves carry enough to recognise a recurrence.

    Nothing here reads a transcript or a sidecar: the ledger stores the timestamps
    and the candidate dispatches (KYO-134.c2) and the harness's agent id, and those
    three are the measurement. That is what makes this survive the session the
    phenomenon happened in.
    """
    profile = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS).tick_profile()

    assert profile.rows == 40
    assert profile.distinct_agents == 40
    assert profile.every_id_distinct
    assert profile.cadence_seconds == pytest.approx(STOP_TICK_PERIOD_SECONDS)
    assert profile.looks_like_the_tick
    sentence = profile.sentence()
    assert sentence and "32s timer PER RUNNING SUBAGENT" in sentence, sentence


def test_the_cadence_is_measured_against_dispatch_activity_not_wall_clock() -> None:
    """c4, and the reason the measurement is a product rather than a rate.

    The same 32-second-per-agent tick arrives five times as fast with five workers
    running. Divided by wall-clock it would look like five different phenomena and
    a recurrence would be unrecognisable; measured against dispatch activity it is
    one number that stays put. A regression that dropped the multiplication would
    still pass every other test in this file at ``concurrency=1``.
    """
    alone = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, concurrency=1)
    busy = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, concurrency=5)

    assert busy.tick_profile().cadence_seconds == pytest.approx(
        alone.tick_profile().cadence_seconds
    )
    assert busy.tick_profile().looks_like_the_tick
    # ...and the raw arrivals really were five times as fast, or the assertion above
    # is comparing two identical fixtures and proving nothing.
    assert busy.audits[1].at != alone.audits[1].at


def test_the_measurement_travels_with_the_ledgers_json() -> None:
    """c4. `kyo worktrees --json` carries it, so it is readable after the fact."""
    payload = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS).as_json()["arrival_pattern"]

    assert payload["rows"] == 40
    assert payload["every_id_distinct"] is True
    assert payload["looks_like_the_subagent_stop_tick"] is True
    assert payload["tick_period_seconds"] == STOP_TICK_PERIOD_SECONDS
    assert payload["cadence_seconds_per_running_dispatch"] == pytest.approx(
        STOP_TICK_PERIOD_SECONDS
    )


# ------------------------------------- the signature is narrow, in both directions


def test_a_handful_of_genuine_unresolved_stops_is_not_called_the_tick() -> None:
    """A cadence over three rows is a coincidence, and must not be reported as one.

    The tick runs continuously for as long as any agent does, so a real instance
    passes the row floor within minutes. A project with a few real unresolved stops
    must read exactly what it read before KYO-163, with nothing appended about a
    phenomenon it is not having.
    """
    profile = _ledger(rows=3, period=STOP_TICK_PERIOD_SECONDS).tick_profile()

    assert not profile.looks_like_the_tick
    assert profile.sentence() is None


def test_a_repeated_agent_id_is_not_the_tick() -> None:
    """Half the signature, and it has to be load-bearing on its own.

    A population that keeps naming the SAME agent is a retry or a reconnect, not
    this — that was the first candidate KYO-163's record offered and the distinct
    ids are what ruled it out. If it ever comes back it must not be described as
    the tick.
    """
    profile = _ledger(
        rows=40, period=STOP_TICK_PERIOD_SECONDS, distinct_ids=False
    ).tick_profile()

    assert profile.distinct_agents == 1
    assert not profile.every_id_distinct
    assert not profile.looks_like_the_tick
    assert profile.sentence() is None


def test_a_cadence_nowhere_near_the_tick_is_not_the_tick() -> None:
    """The other half. Distinct ids alone describe any busy session."""
    profile = _ledger(rows=40, period=600.0).tick_profile()

    assert profile.every_id_distinct
    assert not profile.looks_like_the_tick
    assert profile.sentence() is None


def test_rows_that_are_not_unresolved_are_not_profiled() -> None:
    """A ledger of clean `matched` stops has no arrival pattern to report."""
    profile = _ledger(
        rows=40, period=STOP_TICK_PERIOD_SECONDS, outcome=STOP_TARGET_MATCHED
    ).tick_profile()

    assert profile.rows == 0
    assert profile.sentence() is None


def test_an_unparseable_timestamp_degrades_rather_than_raising() -> None:
    """This feeds a read surface a hook renders; it may not raise, ever."""
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS)
    ledger.audits[5].at = "not a timestamp at all"

    profile = ledger.tick_profile()
    assert profile.cadence_seconds == pytest.approx(STOP_TICK_PERIOD_SECONDS)
    assert ledger.as_json()["arrival_pattern"]["rows"] == 40


# --------------------------------------------------- c3: nothing is reclassified


def test_the_profile_reclassifies_nothing() -> None:
    """c3, stated as bluntly as it can be.

    Nothing rests on "an agent without a sidecar is not a worker". The profile is
    computed over a population and returns a sentence; it does not touch an
    outcome, a `why`, a candidate list or the unresolved set. Every row that went
    in comes out unchanged and still unresolved.

    KYO-373: over rows that could be a worker's. Rows with no transcript are
    classified by what is missing for THAT agent, never by the population's shape;
    this is the shape half, and it still holds.
    """
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, why=UNMATCHED_WHY)
    before = [
        (a.event_id, a.outcome, a.why, tuple(a.candidates), a.agent_id) for a in ledger.audits
    ]

    assert ledger.tick_profile().looks_like_the_tick

    after = [
        (a.event_id, a.outcome, a.why, tuple(a.candidates), a.agent_id) for a in ledger.audits
    ]
    assert after == before, "computing the profile mutated the ledger"
    assert len(ledger.unresolved) == 40, (
        "rows left the unresolved set because they looked like the tick — this is the "
        "absence-of-a-trace classification KYO-163.c3 forbids"
    )


def test_a_stop_is_still_recorded_and_still_unresolved_whatever_the_population() -> None:
    """The write side is untouched: no row is suppressed for looking like a tick."""
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, why=UNMATCHED_WHY)
    assert ledger.tick_profile().looks_like_the_tick
    assert len(ledger.audits) == 40
    assert all(a.outcome == STOP_TARGET_UNRESOLVED for a in ledger.audits)
    assert "could NOT be matched" in ledger.sentence()


# ------------------------------------------- the ledger reads the stored agent id


def test_a_recorded_stop_puts_the_harnesss_agent_id_in_the_ledger(
    kyo, controller_dirty: Path, tmp_path: Path  # noqa: F811
) -> None:
    """KYO-134 stored ``agent_id`` and matched it against nothing.

    That left a stored field with no machine consumer, which is what law 2 exists
    to prevent, and it is why the population could not be characterised without
    going back to the transcripts. The profile is its first consumer, so the field
    has to survive the round trip through the store and back out of the ledger.
    """
    _running_dispatch(kyo, tmp_path, "wt-agent-id")
    # KYO-418.c6: a current build counts an unresolved stop instead of writing it; the
    # profile reads the rows a store's history holds, so this writes one the old way.
    with legacy_unresolved_rows():
        result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert result.code == 0, result
    assert result.json["subject"]["outcome"] == "unresolved", result.json

    recorded = event_lines(controller_dirty)[-1]
    assert recorded["after"]["agent_id"], "the harness's agent id was not written down"

    listing = kyo("worktrees", "--json")
    assert listing.code == 0, listing
    pattern = listing.json["stop_audits"]["arrival_pattern"]
    assert pattern["rows"] == 1, pattern
    assert pattern["distinct_agent_ids"] == 1, (
        "the stored agent id did not reach the ledger, so the profile cannot count it: "
        f"{pattern}"
    )
    # ...and one row is still nowhere near a pattern, which is the next test's job to
    # keep true but is worth asserting here too: reading the field must not by itself
    # start describing anything.
    assert pattern["looks_like_the_subagent_stop_tick"] is False, pattern


def test_a_row_written_before_the_agent_id_was_stored_reads_as_unknown() -> None:
    """Rows predating KYO-134's ``agent_id`` must count as unknown, not as distinct.

    An absent id read as its own value would make every legacy row "distinct" and
    hand the tick signature to a population that never carried it.
    """
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS)
    for audit in ledger.audits:
        audit.agent_id = None

    profile = ledger.tick_profile()
    assert profile.distinct_agents == 0
    assert not profile.looks_like_the_tick
    assert profile.sentence() is None


# ---------------------------------------------------- c2: reported, not unwritten


def test_doctor_names_the_tick_when_the_population_carries_its_signature(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """c2. The choice was reporting, not writing, and this is the reporting half.

    The count a controller reads is a function of how long workers ran. Saying so,
    in the place the count appears, is what makes it interpretable — and it is the
    whole of what changed, because the rows are all still there.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-doctor-tick")
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, why=UNMATCHED_WHY)
    for audit in ledger.audits:
        audit.candidates = [dispatch_id]
    monkeypatch.setattr(stop_audit, "stop_audits", lambda _store: ledger)

    result = kyo("doctor", "--json")
    failures = result.json["checks"]["unresolved_stops"]["failures"]
    assert failures, result.json["checks"]["unresolved_stops"]
    assert "ARRIVAL PATTERN" in failures[0], failures[0]
    assert "PER RUNNING SUBAGENT" in failures[0], failures[0]


def test_the_doctor_finding_does_not_shrink_when_the_tick_is_named(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """c3 at the reporting surface, and the trap this record was sent to avoid.

    Naming the tick must not quieten the finding. The count still counts every
    unresolved row, the check still REPORTS, and the implicated worktree is still
    named — because a real worker's unaudited stop is somewhere in that population
    and nothing here can tell which one it is. A version that filtered the count
    down to "probably real" rows would pass the test above and lose the worker.

    KYO-214: "still fails" is now "still reports". The exit code was always a separate
    question from the count, and a finding whose own text says it is not a block is a
    notice — so the assertion below is that the whole finding is still there, in the
    check's own findings and in the printed output, at full length. Filtering it down
    would still fail this test; exiting 0 on it is what the finding always said.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-doctor-loud")
    # KYO-373: rows that could be a worker's. A tick-SHAPED population of those still
    # counts in full; rows with no transcript are counted apart and never reach here.
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, why=UNMATCHED_WHY)
    for audit in ledger.audits:
        audit.candidates = [dispatch_id]
    monkeypatch.setattr(stop_audit, "stop_audits", lambda _store: ledger)

    result = kyo("doctor", "--json")
    assert result.json["checks"]["unresolved_stops"]["failures"], (
        "naming the tick emptied the check"
    )
    failures = result.json["checks"]["unresolved_stops"]["failures"]
    assert "40 stop(s)" in failures[0], failures[0]
    assert "NOTHING WAS CHECKED" in failures[0], failures[0]
    assert dispatch_id in failures[0], failures[0]


def test_doctor_says_nothing_extra_when_the_population_is_not_the_tick(
    kyo, controller_dirty: Path, tmp_path: Path  # noqa: F811
) -> None:
    """One genuine unresolved stop reads exactly as it did before KYO-163."""
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-doctor-plain")
    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert result.json["subject"]["outcome"] == "unresolved", result.json

    doctored = kyo("doctor", "--json")
    failures = doctored.json["checks"]["unresolved_stops"]["failures"]
    assert failures, doctored.json["checks"]["unresolved_stops"]
    assert dispatch_id in failures[0]
    assert "ARRIVAL PATTERN" not in failures[0], (
        "a single stop was described as a 32-second-per-agent pattern"
    )


# ===================================================================================
# KYO-169: what the same measurement is worth on the OTHER surface — the per-stop
# transcript prose the guards print, which was measured at about 8 long lines
# rendered TWICE per unplaceable stop against a ledger of 494 unresolved rows.
#
# The whole difficulty is that no rule here may key off an individual row. The
# population below always contains :data:`WORKER_AGENT` — a genuine dispatched
# worker with a full sidecar and a readable transcript, taken from this store's own
# ledger — and the tests come in pairs: the same row under two populations must read
# differently, and two different rows (one traceable, one not) under one population
# must read identically. Together those say the decision is the population's.
# ===================================================================================

#: An agent id modelled on a row of this project's own ledger:
#: a dispatched worker on KYO-148.d1 with a spawn sidecar and a 526-entry transcript,
#: sitting `unresolved` among the ticks because its own dispatch had stopped running
#: by the time it stopped. It is the row that makes "no trace, so not a worker" a
#: silencer of real workers, and every test below keeps it in the bucket.
WORKER_AGENT = "a2468ace13579bdf0"

#: The dispatch that worker was on. Its prompt names it; no such dispatch is running
#: in the fixture, which is exactly the condition that made the real row unresolved.
WORKER_DISPATCH = "KYO-148.d1"


def _traceable_worker_stops(kyo, tmp_path: Path, controller: Path, *args: str):
    """The KYO-148.d1 row, reproduced: a real worker, fully traceable, unplaceable.

    Everything a per-row rule would look at is present — a spawn sidecar at the path
    :func:`~kyosei.stop_target._spawn_sidecar` searches, a readable transcript, and
    a worker's own dispatch prompt. What is absent is the only thing that could place
    it: the dispatch it names is no longer running. That is the real row's ``why``,
    verbatim in shape, and it is why silencing on traceability loses this worker.
    """
    transcript = _transcript(
        tmp_path,
        WORKER_AGENT,
        f"You are a dispatched Kyosei worker on dispatch **{WORKER_DISPATCH}**.",
    )
    return kyo(
        "check-clean",
        "--subagent-stop",
        *args,
        stdin=_payload(transcript, controller, WORKER_AGENT),
    )


def _tick_population(dispatch_id: str, *, rows: int = 40) -> StopAuditLedger:
    """A ledger carrying the tick signature — with the real worker's row inside it.

    ``_ledger`` alone would be a population of phantoms, and a fix that only ever
    met phantoms could quite happily be keying off traceability without any test
    noticing. Putting :data:`WORKER_AGENT` in the bucket is what makes the
    population honest: it is where the real one was.
    """
    ledger = _ledger(rows=rows, period=STOP_TICK_PERIOD_SECONDS)
    for audit in ledger.audits:
        audit.candidates = [dispatch_id]
    ledger.audits[rows // 2].agent_id = WORKER_AGENT
    return ledger


def _lines(result) -> list[str]:
    return [line for line in (result.out + result.err).splitlines() if line.strip()]


# `--json` replaces the human lines rather than accompanying them, so a test that
# wants both the prose and the verdict has to ask twice. Two runs mean two ledger
# rows, which is fine here and is itself the shape of the phenomenon.


# ------------------------- c1: a tick population costs one line, and points at the record


def test_an_unplaceable_stop_in_a_tick_population_costs_one_line(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """KYO-169.c1. The measured complaint was ~8 long lines, twice, per stop.

    What survives is one line that says where the durable record is. What goes is
    the closing imperative — "Check the tree this dispatch was sent to before
    merging it" — which on a tick asks the controller to investigate an event that
    did not happen: no agent finished, the harness delivered `SubagentStop` on a
    timer.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-quiet")
    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: _tick_population(dispatch_id)
    )

    verdict = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert verdict.code == 0, verdict
    assert verdict.json["subject"]["outcome"] == STOP_TARGET_UNRESOLVED, verdict.json
    assert verdict.json["population_looks_like_the_tick"] is True, verdict.json

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 0, result
    lines = _lines(result)
    assert len(lines) == 1, f"an unplaceable tick stop still cost {len(lines)} lines: {lines}"
    only = lines[0]
    assert "Check the tree this dispatch was sent to" not in only, (
        "the tick still asks the controller to investigate a stop that never happened"
    )
    # KYO-418.c6: the durable record of an unresolved stop is its count on the
    # candidate's own row now, and the line names where that is read.
    assert "counted as unmatched against" in only and "`kyo worktrees`" in only, (
        "the short line does not say where the durable record is, which is the only "
        "thing that makes it safe to be short"
    )
    assert "KYO-163" in only, only


def test_the_quiet_line_is_shorter_than_the_explanation_it_replaces(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """c1's "at most one SHORT line", measured rather than asserted by eye.

    One line is not a saving if it is the eight concatenated. The comparison is
    against the same stop's own full explanation, produced by the same command in
    the same fixture with only the POPULATION changed.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-size")

    loud = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: _tick_population(dispatch_id)
    )
    quiet = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)

    loud_text = "\n".join(_lines(loud))
    quiet_text = "\n".join(_lines(quiet))
    assert len(_lines(loud)) >= 4, _lines(loud)
    assert len(quiet_text) < len(loud_text) / 2, (
        f"the 'short' line is {len(quiet_text)} chars against {len(loud_text)}: "
        f"{quiet_text}"
    )


# ---------------- c2: a population that is not the tick reads exactly as it did before


def test_a_population_that_is_not_the_tick_keeps_the_whole_explanation(
    kyo, controller_dirty: Path, tmp_path: Path  # noqa: F811
) -> None:
    """KYO-169.c2. One genuine unplaceable stop must cost what it cost before.

    Nothing is monkeypatched here: the population is the real one this fixture
    built, which is a single row and therefore nowhere near the signature. Every
    sentence KYO-128 and KYO-134 wrote has to still be there, including the remedy
    addressed to the controller — because in this population it is a real remedy.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-loud")

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 0, result
    combined = result.out + result.err
    assert "NOTHING WAS CHECKED" in combined, combined
    assert "why this tree:" in combined, combined
    assert dispatch_id in combined, "the candidate dispatch stopped being named"
    assert "Check the tree this dispatch was sent to before merging it" in combined, (
        "the controller's remedy was dropped from a stop that is not the tick"
    )
    assert "durable record of this stop:" in combined, combined

    verdict = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert verdict.json["population_looks_like_the_tick"] is False, verdict.json


def test_an_unreadable_ledger_prints_more_rather_than_less(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """The failure direction, chosen on purpose (c2).

    A guard that cannot measure the population must not quieten anything. `None`
    means "not measured", and not measured prints in full — the opposite default
    would turn a broken store read into silence at exactly the moment a controller
    most needs the noise.
    """
    _running_dispatch(kyo, tmp_path, "wt-169-blind")

    def _explode(_store):
        raise RuntimeError("the ledger is gone")

    monkeypatch.setattr(stop_audit, "stop_audits", _explode)

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert result.code == 0, result
    assert "NOTHING WAS CHECKED" in result.out + result.err
    verdict = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert verdict.json["population_looks_like_the_tick"] is None, verdict.json


def test_an_unreadable_ledger_reported_as_such_prints_more_rather_than_less(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """The same, by the route `stop_audits` actually takes: it never raises.

    :func:`~kyosei.stop_audit.stop_audits` degrades to a ledger carrying
    ``unreadable`` rather than throwing, and an empty such ledger profiles as "not
    the tick" only by accident of having no rows. Reading the field is what makes
    that deliberate.
    """
    _running_dispatch(kyo, tmp_path, "wt-169-degraded")
    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: StopAuditLedger(unreadable="disk on fire")
    )

    result = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    assert "NOTHING WAS CHECKED" in result.out + result.err
    verdict = _stop(kyo, "check-clean", tmp_path, controller_dirty, None, "--json")
    assert verdict.json["population_looks_like_the_tick"] is None, verdict.json


# ------------------------------------ c3: one stop, one explanation, not one per guard


def test_one_stop_produces_one_explanation_across_both_subagent_stop_guards(
    kyo, controller_dirty: Path, tmp_path: Path  # noqa: F811
) -> None:
    """KYO-169.c3, and the halving that is true whatever the population is.

    The ``SubagentStop`` hook runs both guards on every stop and both rendered the
    same finding. `kyo check-branch` already declines to RECORD the stop, for the
    reason its own comment gives — two rows in a git-canonical ledger for one event
    would make every count of them twice the truth — and that reasoning simply never
    reached the prose. This is the same fixture as c2, so the explanation itself is
    the full one; what must not happen is that it arrives twice.
    """
    _running_dispatch(kyo, tmp_path, "wt-169-once")
    _git(controller_dirty, "branch", "-M", "main")

    clean = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)
    branch = _stop(
        kyo, "check-branch", tmp_path, controller_dirty, None, "--against", "main"
    )
    assert clean.code == 0, clean
    assert branch.code == 0, branch

    combined = (clean.out + clean.err) + (branch.out + branch.err)
    assert combined.count("NOTHING WAS CHECKED") == 1, (
        "the same stop was explained by both guards — this is the duplication "
        f"KYO-169.c3 removes:\n{combined}"
    )
    assert combined.count("what was found there anyway:") == 1, combined
    assert len(_lines(branch)) == 1, (
        f"check-branch still spends more than a pointer on this stop: {_lines(branch)}"
    )
    assert "check-clean" in (branch.out + branch.err), (
        "check-branch went quiet without saying which guard owns the explanation — "
        "a check that stops saying anything is indistinguishable from one that passed"
    )


def test_check_branchs_json_verdict_is_untouched_by_the_deferral(
    kyo, controller_dirty: Path, tmp_path: Path  # noqa: F811
) -> None:
    """c3 only removes PROSE. A machine consumer loses nothing."""
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-json")
    _git(controller_dirty, "branch", "-M", "main")

    result = _stop(
        kyo, "check-branch", tmp_path, controller_dirty, None, "--json", "--against", "main"
    )
    data = result.json
    assert data["subject"]["outcome"] == STOP_TARGET_UNRESOLVED, data
    assert data["checked_the_worker_tree"] is False, data
    assert data["subject"]["candidates"] == [dispatch_id], data
    assert "changed_files" in data and "guarded_files" in data, data


# ------------- c4: the decision is the population's, and never the individual row's


def test_the_traceable_workers_stop_is_told_apart_by_nothing_but_its_population(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """KYO-169.c4, against the real row: same stop, two populations, two volumes.

    The stopping agent here IS the KYO-148.d1 row — a genuine dispatched worker with
    a sidecar and a transcript, unplaceable only because its own dispatch had stopped
    running. Under a population that is not the tick it gets the full explanation.
    Under one that is, it gets the short line, exactly like every other row: not
    because anything was decided about IT, but because nothing was.

    A rule shaped "this stop has no trace, so it is not a worker" would fail this
    test in the first half — it would silence this worker in both populations.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-worker")

    verdict = _traceable_worker_stops(kyo, tmp_path, controller_dirty, "--json")
    assert verdict.json["subject"]["outcome"] == STOP_TARGET_UNRESOLVED, verdict.json
    # The real row's `why` names the transcript it READ — "first user message of
    # .../agent-a2468ace13579bdf0.jsonl" — which is the evidence that this stop is
    # traceable and was placed anyway on the only ground that matters: the dispatch
    # its prompt names is not running. A fixture that lost the transcript would
    # produce a different `why` and prove nothing about traceability.
    assert f"agent-{WORKER_AGENT}.jsonl" in verdict.json["subject"]["why"], (
        "the fixture no longer reproduces the KYO-148.d1 row: this stop's transcript "
        f"was not read, so it is not the traceable case — {verdict.json['subject']}"
    )

    alone = _traceable_worker_stops(kyo, tmp_path, controller_dirty)
    assert "NOTHING WAS CHECKED" in alone.out + alone.err, (
        "a real dispatched worker's unaudited stop was silenced on its own account — "
        "the KYO-128 failure returning by KYO-154's refused route"
    )
    assert "Check the tree this dispatch was sent to before merging it" in (
        alone.out + alone.err
    )

    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: _tick_population(dispatch_id)
    )
    in_the_tick = _traceable_worker_stops(kyo, tmp_path, controller_dirty)
    assert len(_lines(in_the_tick)) == 1, _lines(in_the_tick)


def test_a_traceable_stop_and_a_traceless_one_read_identically_in_one_population(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """KYO-169.c4 from the other side, and this is the half that pins the rule.

    The test above shows the population can change the volume. This one shows the
    ROW cannot: two stops arriving into the same population — one with a full
    sidecar and transcript naming a dispatch, one with neither — produce the same
    number of lines. Any per-row traceability test, however it were spelled, would
    split these two apart.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-pair")
    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: _tick_population(dispatch_id)
    )

    traceable = _traceable_worker_stops(kyo, tmp_path, controller_dirty)
    traceless = _stop(kyo, "check-clean", tmp_path, controller_dirty, None)

    assert len(_lines(traceable)) == len(_lines(traceless)) == 1, (
        f"traceable: {_lines(traceable)}\ntraceless: {_lines(traceless)}"
    )


# ------------- c5: every row is still written and `kyo doctor` still reports them all


def test_the_quiet_stop_is_still_written_to_the_ledger(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """KYO-169.c5. Quieter prose, identical write side.

    Read from the git-canonical export rather than the database, for KYO-134's
    reason: "durable" means the fact survives the process, and the export is what
    carries it.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-written")
    before = len(_stop_audit_events(controller_dirty))
    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: _tick_population(dispatch_id)
    )

    result = _traceable_worker_stops(kyo, tmp_path, controller_dirty, "--json")
    assert result.json["subject"]["outcome"] == STOP_TARGET_UNRESOLVED, result.json

    # KYO-418.c6: the write side is identical for a quiet stop and a loud one -- and
    # for both it is now a count on the running candidate's own row, not a row.
    assert len(_stop_audit_events(controller_dirty)) == before
    monkeypatch.undo()
    counted = kyo("worktrees", "--json").json["stop_audits"]["unresolved_counted"]
    assert counted[dispatch_id]["count"] == 1, "a stop went uncounted because it was quiet"


def test_the_doctor_finding_is_unchanged_by_the_quieter_transcript(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """KYO-169.c5. The transcript got shorter; the durable surface did not.

    KYO-157 is a separate open record about how much of `kyo doctor` a controller
    wades through, and this record must not quietly do its job. So the doctor is
    read here with the real ledger — the monkeypatch is undone before it runs — and
    it must still report, still count the row the quiet stop wrote, and still name the
    worktree implicated. (KYO-214 moved the finding to the notice tier, so "still
    fails" became "still appears, at full length, as a notice"; the count and the
    named worktree are what this test was ever about.)
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-169-doctor")
    monkeypatch.setattr(
        stop_audit, "stop_audits", lambda _store: _tick_population(dispatch_id)
    )
    quiet = _traceable_worker_stops(kyo, tmp_path, controller_dirty)
    assert len(_lines(quiet)) == 1, _lines(quiet)

    monkeypatch.undo()
    doctored = kyo("doctor", "--json")
    assert doctored.json["notices"], "the quiet stop stopped being a doctor finding"
    failures = doctored.json["checks"]["unresolved_stops"]["failures"]
    assert failures, doctored.json["checks"]["unresolved_stops"]
    assert "1 stop(s)" in failures[0], failures[0]
    assert "NOTHING WAS CHECKED" in failures[0], failures[0]
    assert dispatch_id in failures[0], failures[0]


# ===================================================================================
# KYO-214: the same finding, in the tier its own text has always claimed.
#
# The population below is this file's tick-shaped one, because that is the population
# an operator actually reads: 1567 rows carrying 1567 distinct agent ids, arriving
# every ~32s per running dispatch, reported as ONE finding that says of itself "this is
# not a block and never was" — and, until KYO-214, exiting 3 from the last command in
# the `Stop` hook chain, so Claude Code rendered every stop in the session as an error
# block for the whole duration of every dispatch.
#
# The pair below is the mechanism and its canary. The first says the finding is printed
# in full and exits 0; the second reverts the one declaration that makes it a notice and
# shows the first one's `code == 0` going red, which is what stops that assertion from
# being a coincidence of the fixture (KYO-34's lesson, and KYO-214.c4 in as many words).
# ===================================================================================


def _tick_notice(kyo, tmp_path: Path, name: str, monkeypatch) -> str:
    """A running dispatch and a tick-shaped ledger of unresolved stops against it.

    KYO-373: unresolved stops that could be a worker's, so they still reach the
    finding; the no-transcript rows a real tick writes are not-a-worker now.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, name)
    ledger = _ledger(rows=40, period=STOP_TICK_PERIOD_SECONDS, why=UNMATCHED_WHY)
    for audit in ledger.audits:
        audit.candidates = [dispatch_id]
    monkeypatch.setattr(stop_audit, "stop_audits", lambda _store: ledger)
    return dispatch_id


def test_a_tick_shaped_population_is_a_notice_printed_in_full_and_doctor_exits_zero(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """KYO-214.c1/c2/c4 in one run: the whole finding, both surfaces, exit 0."""
    dispatch_id = _tick_notice(kyo, tmp_path, "wt-214-notice", monkeypatch)

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True, result.json
    assert result.json["problems"] == [], result.json["problems"]
    assert result.json["checks"]["unresolved_stops"]["tier"] == "notice", result.json["checks"]
    # The check still FOUND something — `ok` per check is "found nothing", which is a
    # different question from the run's exit code, and both are answerable from here.
    assert result.json["checks"]["unresolved_stops"]["ok"] is False

    assert len(result.json["notices"]) == 1, result.json["notices"]
    notice = result.json["notices"][0]
    assert notice.startswith("[unresolved_stops] ")
    # c2: the text is what KYO-157 and KYO-163 left, not a shortened stand-in.
    for fragment in (
        "40 stop(s)",
        "NOTHING WAS CHECKED",
        dispatch_id,
        "this is not a block and never was",
        "ARRIVAL PATTERN",
    ):
        assert fragment in notice, (fragment, notice)

    printed = kyo("doctor")
    assert printed.code == 0, printed
    assert "notices (1)" in printed.out, printed.out
    assert notice in printed.out, (
        "the notice reached --json but not the reader the hook chain prints to"
    )


def test_moving_the_notice_back_to_the_problem_tier_fails_the_command_again(
    kyo, controller_dirty: Path, tmp_path: Path, monkeypatch  # noqa: F811
) -> None:
    """The canary for the test above: delete the declaration, watch it go red.

    ``NOTICE_CHECKS`` is the entire mechanism — one name in one frozenset beside
    ``CHECKS`` — so emptying it is exactly "the notice is moved back to the problem
    tier". Under that mutation the identical fixture exits 3 with the identical text,
    which is both the bug KYO-214 was filed for and the proof that the assertion above
    is load-bearing rather than a property of the fixture.
    """
    dispatch_id = _tick_notice(kyo, tmp_path, "wt-214-canary", monkeypatch)
    monkeypatch.setattr(doctor_module, "NOTICE_CHECKS", frozenset())

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    assert result.json["notices"] == [], result.json["notices"]
    assert len(result.json["problems"]) == 1, result.json["problems"]
    assert dispatch_id in result.json["problems"][0]
    assert result.json["checks"]["unresolved_stops"]["tier"] == "problem"


# ------ KYO-325: an unplaced row that names a dispatch is not a stop that failed
#                 to match it, whatever arrived while its worktree still exists
#
# The reporting consequence of this file's own subject, and the reason KYO-325 was
# filed with the wrong premise. `kyo show KYO-263.d1` once said "134 stop(s)
# named this dispatch as a candidate and none of them could be matched to it" — which
# reads as "the worker's stop was seen and the match failed", and sent the record's
# description looking at the search path, the agent id and the worktree comparison.
# All 134 were ticks recorded HOURS after that worker had handed back, naming it only
# because its worktree directory still exists: every tick names every dispatch whose
# directory is on disk (`candidates`). The worker's own stop was never recorded at
# all — the ledger was silent for the whole two hours it ran in, because the hook's
# `kyo` could not open the store (`STOP_TARGET_STORE_UNREADABLE`).
#
# So a dispatch's own run window is what separates "no stop matched" from "no stop
# happened", and without it the first sentence is printed over both. KYO-375 pulled
# ticks themselves out of "N stop(s) named this dispatch as a candidate" (they get
# their own sentence, and since KYO-375 they write no row at all) — the window logic
# below is about WORKER-shaped rows now, which is what `_window_ledger` models below;
# the ticks half of KYO-325's story is `tests/test_dispatch_worktree.py`'s.


def _window_ledger(dispatch_id: str, *, ticks_at: list[float]) -> StopAuditLedger:
    """Unmatched WORKER stops naming ``dispatch_id`` as a candidate, at the given
    offsets — ``ticks_at`` names the parameter's origin (KYO-325's own story was
    about ticks) rather than its current shape (KYO-375: a tick is not one of
    these any more, see the module note above)."""
    ledger = StopAuditLedger()
    for index, offset in enumerate(ticks_at):
        ledger.audits.append(
            StopAudit(
                event_id=f"KYO-E{index}",
                at=_at(offset),
                outcome=STOP_TARGET_UNRESOLVED,
                directory="/the/controllers/cwd",
                why=UNMATCHED_WHY,
                dispatch_id=None,
                candidates=[dispatch_id],
                agent_id=f"a{index:016x}",
            )
        )
    return ledger


def test_ticks_after_a_dispatch_finished_are_not_reported_as_its_unmatched_stops() -> None:
    """The sentence that was wrong, measured against the case that made it wrong."""
    ledger = _window_ledger("KYO-1.d1", ticks_at=[7200.0, 7232.0, 7264.0])

    spend = worker_tokens.worker_tokens_from(
        ledger, "KYO-1.d1", ran_between=(_at(0), _at(3600))
    )

    assert spend.outcome == worker_tokens.TOKENS_NO_STOP_WHILE_RUNNING
    assert not spend.known
    assert "NO stop of any kind was recorded while this dispatch was running" in spend.why
    # The distinction the old sentence collapsed: nothing failed to match here.
    assert "none of them could be matched" not in spend.why
    # And it names what to do about it rather than leaving a reader to guess.
    assert stop_target.STOP_TARGET_STORE_UNREADABLE in spend.why


def test_a_stop_inside_the_window_is_still_reported_as_an_unmatched_stop() -> None:
    """The other half, and the one that must not be lost to the fix.

    A row recorded WHILE the dispatch was running genuinely could have been its
    worker's, so "stops were seen and none matched" is the right sentence and the
    search path, the agent id and the worktree comparison are all back in the frame.
    """
    ledger = _window_ledger("KYO-1.d1", ticks_at=[1800.0])

    spend = worker_tokens.worker_tokens_from(
        ledger, "KYO-1.d1", ran_between=(_at(0), _at(3600))
    )

    assert spend.outcome == STOP_TARGET_UNRESOLVED
    assert "none of them could be matched to it" in spend.why
    assert "NO stop of any kind" not in spend.why


def test_a_running_dispatch_has_an_open_window_and_is_never_called_silent() -> None:
    """A worker that has not stopped yet has not missed its stop.

    The window's far end is left open for a dispatch in state `running`, so a tick
    arriving right now is inside it. Closing the window at `updated_at` would report
    every live worker as one whose stop was never recorded.
    """
    ledger = _window_ledger("KYO-1.d1", ticks_at=[7200.0])

    spend = worker_tokens.worker_tokens_from(ledger, "KYO-1.d1", ran_between=(_at(0), None))

    assert spend.outcome == STOP_TARGET_UNRESOLVED, spend.why


def test_without_a_window_the_old_sentence_is_unchanged() -> None:
    """`ran_between` is optional, and absent means "narrow nothing".

    A caller that cannot say when a dispatch ran must not have a window invented for
    it: the claim this fix buys is that NOTHING happened in a stretch of time, and
    that claim is worthless if the stretch is a guess.
    """
    ledger = _window_ledger("KYO-1.d1", ticks_at=[7200.0])

    spend = worker_tokens.worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.outcome == STOP_TARGET_UNRESOLVED
    assert "1 stop(s) named this dispatch as a candidate and none of them could be" in spend.why
