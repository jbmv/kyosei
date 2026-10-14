"""The stop-audit ledger: every SubagentStop resolution, recorded and read back."""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime

from . import events_ledger, export, ledgers
from .clock import now
from .commands import Context
from .contracts import DISPATCH_STATE_RUNNING
from .stop_target import (
    STOP_REASON_LABELS,
    STOP_REASON_NO_TRANSCRIPT,
    STOP_REASON_OTHER,
    STOP_REASON_PROMPT_NAMES_NONE,
    STOP_REASON_PROMPT_NAMES_SEVERAL,
    STOP_TARGET_MATCHED,
    STOP_TARGET_NO_TRANSCRIPT,
    STOP_TARGET_NO_WORKTREES,
    STOP_TARGET_NOT_A_WORKER,
    STOP_TARGET_UNRESOLVED,
    StopTarget,
    _payload_of,
)

# ---------------------- KYO-134: a stop resolution that outlives the transcript
#
# Every stop where a declared worktree was in play is written down, in one of two forms.
# `matched` stops and the outcomes a human audits (a helper of a dispatched worker, a
# store that could not be read) are rows in the `events` ledger -- append-only,
# git-canonical, rebuilt from export -- and a `matched` row is the evidence the recorder
# ran at all, so "no unresolved stops" and "nothing ever recorded a stop" stay
# distinguishable. A not-a-worker tick (KYO-375) and an `unresolved` stop (KYO-418) are
# COUNTED in place instead, on each running candidate's `dispatches.tick_audit_json`;
# :data:`COUNTED_IN_PLACE` is the one table that decides which. A stop with no declared
# worktree in play is not recorded; it is ordinary session churn.
#
# The write is best-effort and never raises. It runs inside a hook, so a store that will
# not open, a database behind its export or a locked database must not turn a stop into a
# crash or a block; a failed write is reported in the guard's own output as a failed
# write. History: docs/design/dispatch.md#kyo-134.

#: The ``events.command`` value carried by one recorded stop resolution. Queried
#: as an exact match by :func:`stop_audits`, so it is a constant rather than a
#: string spelled twice.
STOP_AUDIT_COMMAND = "check-clean --subagent-stop"


# ------------------------------------------------------------------ KYO-163: the tick
#
# Claude Code delivers `SubagentStop` to this project's hook on a ~32-second timer, once
# per running subagent, minting a fresh agent id every tick: a periodic per-agent signal,
# not an agent finishing. The search path is not the cause -- a real agent at any spawn
# depth resolves through it.
#
# Kyosei cannot filter these at the write side. A tick's payload is shape-identical to a
# real worker's; the only difference is whether a transcript and spawn sidecar exist, and
# classifying on that ABSENCE would silently stop auditing a real worker whose sidecar the
# harness failed to write. So no stop is reclassified and no outcome changes; since
# KYO-375 a tick is counted rather than written as a row, which changes its record's
# form, not its classification. What changes is what is reported: the population is measured by
# :meth:`StopAuditLedger.tick_profile` and named by `kyo doctor`. The measurements and the
# experiments that ruled out a misfiled helper: docs/design/dispatch.md#kyo-163.

#: The observed period of Claude Code's per-subagent ``SubagentStop`` tick.
#: Measured, not chosen — see the block above. Used only to RECOGNISE the pattern
#: in :meth:`StopAuditLedger.tick_profile`; nothing schedules against it and no
#: verdict depends on it.
STOP_TICK_PERIOD_SECONDS = 32.0

#: How far the measured cadence may sit from :data:`STOP_TICK_PERIOD_SECONDS` and
#: still be called the tick. Wide on purpose: the cadence is divided by Kyosei's
#: count of running dispatches, which is a proxy for the number of live AGENTS and
#: undercounts helpers, so the quotient is noisy even when the underlying metronome
#: is exact. A loose band that says "this looks like the tick" is worth more than a
#: tight one that says nothing whenever a helper is running.
STOP_TICK_TOLERANCE_SECONDS = 16.0

#: Gaps longer than this are treated as a break between bursts rather than as one
#: interval of the tick, and are left out of the cadence. Without it a single
#: overnight gap dominates the median.
_STOP_TICK_MAX_GAP_SECONDS = 300.0

#: Below this many rows a cadence is not a pattern, it is a coincidence. The tick
#: produces rows continuously for as long as any agent runs, so a genuine instance
#: reaches this within minutes; a project with a handful of real unresolved stops
#: never does, and is never described as the tick.
_STOP_TICK_MIN_ROWS = 8


def _stop_audit_seconds(at: str) -> float | None:
    """One ledger timestamp as epoch seconds, or ``None`` if it will not parse.

    Never raises: this feeds a read surface that a hook renders, and a row with a
    timestamp from a future format must degrade to "not counted" rather than take
    the whole profile — and with it `kyo doctor` — down with it.
    """
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


@dataclass
class TickProfile:
    """KYO-163.c4: the arrival pattern of unresolved stops, measured from the ledger.

    Computed over the POPULATION rather than per row, and deliberately so. It
    annotates a count; it never classifies an individual stop, never changes an
    outcome, and never causes a stop to stop being audited. A single unresolved row
    from a genuine worker keeps its own ``why`` and its own place in the ledger
    whatever this says.
    """

    #: Unresolved rows the profile was computed over.
    rows: int = 0
    #: How many distinct, non-null agent ids those rows carry. Equal to ``rows``
    #: when every stop names an agent id seen exactly once — half of the tick's
    #: signature.
    distinct_agents: int = 0
    #: Median of (gap to the next unresolved row x running dispatches at that
    #: moment). One tick per running agent per period makes this the period itself,
    #: which is what measures the arrival pattern AGAINST DISPATCH ACTIVITY rather
    #: than against wall-clock — the thing KYO-163.c4 asks for.
    cadence_seconds: float | None = None
    #: Earliest and latest unresolved row considered.
    first_at: str | None = None
    last_at: str | None = None

    @property
    def every_id_distinct(self) -> bool:
        return self.rows > 0 and self.distinct_agents == self.rows

    @property
    def looks_like_the_tick(self) -> bool:
        """Whether this population carries the signature described above.

        BOTH halves are required. A fresh id every time is on its own just as
        consistent with a busy session that spawned many agents; a ~32s cadence is
        on its own just as consistent with a coincidence over a handful of rows.
        Together, over a population, they are the pattern KYO-163 measured.
        """
        if not self.every_id_distinct or self.cadence_seconds is None:
            return False
        if self.rows < _STOP_TICK_MIN_ROWS:
            return False
        return (
            abs(self.cadence_seconds - STOP_TICK_PERIOD_SECONDS)
            <= STOP_TICK_TOLERANCE_SECONDS
        )

    def as_json(self) -> dict:
        return {
            "rows": self.rows,
            "distinct_agent_ids": self.distinct_agents,
            "every_id_distinct": self.every_id_distinct,
            "cadence_seconds_per_running_dispatch": self.cadence_seconds,
            "tick_period_seconds": STOP_TICK_PERIOD_SECONDS,
            "looks_like_the_subagent_stop_tick": self.looks_like_the_tick,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "sentence": self.sentence(),
        }

    def sentence(self) -> str | None:
        """What to say about the population, or ``None`` when there is nothing to say.

        ``None`` rather than a hedge: a project whose unresolved stops are NOT this
        pattern must read exactly what it read before, with nothing appended about a
        phenomenon it is not having.
        """
        if not self.looks_like_the_tick or self.cadence_seconds is None:
            return None
        return (
            f"ARRIVAL PATTERN: this ledger's {self.rows} unplaced stop rows carry "
            f"{self.distinct_agents} "
            f"distinct agent ids — one per row — and arrive every "
            f"{self.cadence_seconds:.0f}s per running dispatch. That is the measured "
            f"signature of Claude Code delivering SubagentStop on a "
            f"~{STOP_TICK_PERIOD_SECONDS:.0f}s timer PER RUNNING SUBAGENT with a fresh "
            "agent id each tick, rather than of agents finishing (KYO-163). The hook "
            "payload is shape-identical for a tick and a real worker, so every row is "
            "still recorded; since KYO-373 the ones whose agent has no transcript "
            "anywhere are counted as not-a-worker rather than as unmatched, and a count "
            "of the rest is still a function of how long workers ran, not only of how "
            "many stops went unaudited"
        )


@dataclass
class StopAudit:
    """One recorded stop resolution, read back out of the ``events`` ledger."""

    event_id: str
    at: str
    outcome: str
    directory: str
    why: str
    dispatch_id: str | None
    candidates: list[str]
    #: KYO-163: the harness's own id for the agent that stopped, as
    #: :func:`record_stop_audit` stored it. KYO-134 wrote this field for a human to
    #: line a row up with a session by hand and deliberately matched it against
    #: nothing, which left it a stored field with no machine consumer;
    #: :meth:`StopAuditLedger.tick_profile` is its first one. Still matched against
    #: no agent — only counted.
    agent_id: str | None = None
    #: KYO-154: set when this row is a helper's stop, to the dispatch whose worker
    #: spawned it. ``None`` on every row written before KYO-154 shipped, which is
    #: why it is read defensively below rather than assumed present.
    helper_of: str | None = None
    #: KYO-284: the transcript file this stop's resolution read the prompt out of.
    #: ``None`` on every row written before KYO-284 shipped — which is most of any
    #: existing store's ledger, and is why :func:`worker_tokens` answers
    #: :data:`TOKENS_NO_TRANSCRIPT_RECORDED` for those dispatches instead of going
    #: looking for the file itself.
    transcript: str | None = None
    #: KYO-373.c1: the ``STOP_REASON_*`` code the resolution stored, or ``None`` on a
    #: row written before KYO-373 (read through :func:`_legacy_stop_reason` then).
    stored_reason: str | None = None

    @property
    def untranscribed(self) -> bool:
        """KYO-373.c2: a stop by an agent with no transcript — not a dispatched worker.

        Two routes, and the second is the whole of "the old ledger prints tersely
        too": the outcome itself, on rows written since KYO-373; and, on a row written
        before it (no stored reason), an ``unresolved`` outcome whose ``why`` is the
        sentence :func:`_dispatch_prompt` has always written for exactly this case.
        The stored rows are not rewritten — this is how they are READ.
        """
        if self.outcome == STOP_TARGET_NO_TRANSCRIPT:
            return True
        return (
            self.outcome == STOP_TARGET_UNRESOLVED
            and self.stored_reason is None
            and _legacy_stop_reason(self.why) == STOP_REASON_NO_TRANSCRIPT
        )

    @property
    def unresolved(self) -> bool:
        """An unmatched WORKER stop: unplaced, and not classified as not-a-worker."""
        return self.outcome == STOP_TARGET_UNRESOLVED and not self.untranscribed

    @property
    def unplaced(self) -> bool:
        """Either of the two above — the population KYO-163 measured as one."""
        return self.unresolved or self.untranscribed

    @property
    def helper(self) -> bool:
        return self.outcome == STOP_TARGET_NOT_A_WORKER

    @property
    def reason(self) -> str | None:
        """Why this stop was not placed, as a ``STOP_REASON_*`` code; ``None`` if it was."""
        if not self.unplaced:
            return None
        if self.stored_reason in STOP_REASON_LABELS:
            return self.stored_reason
        return _legacy_stop_reason(self.why)


def _legacy_stop_reason(why: str) -> str:
    """The reason code for a row written before KYO-373 stored one, from its ``why``.

    The one place that reads a reason out of prose, and only prose this module wrote:
    each prefix below is the opening of a sentence :func:`resolve_stop_target` (or
    :func:`_dispatch_prompt`, which it quotes) produced for exactly that case.
    """
    if why.startswith("no readable transcript"):
        return STOP_REASON_NO_TRANSCRIPT
    if why.startswith("the stopping agent's prompt names none of the"):
        return STOP_REASON_PROMPT_NAMES_NONE
    if why.startswith("the stopping agent's prompt names") and "different dispatch" in why:
        return STOP_REASON_PROMPT_NAMES_SEVERAL
    return STOP_REASON_OTHER


@dataclass
class StopAuditLedger:
    """Every recorded stop resolution, and — separately — the unresolved ones.

    Both, and not just the second: a caller holding only the unresolved list
    cannot tell an empty one from a recorder that never ran, which is precisely
    the distinction KYO-134.c5 exists to force. :meth:`sentence` is where that
    three-way answer is written down once, so no surface has to re-derive it.
    """

    audits: list[StopAudit] = field(default_factory=list)
    #: KYO-375.c1: per-dispatch tick counters, read alongside the rows above --
    #: ``{dispatch_id: {"count": int, "first_seen": str, "last_seen": str}}``. A
    #: not-a-worker tick since this shipped writes no row to ``audits`` at all; what
    #: it wrote instead lives here, one entry per candidate dispatch, incremented in
    #: place on ``dispatches.tick_audit_json``. A row written before this shipped is
    #: still read out of ``audits`` (through :attr:`StopAudit.untranscribed`) —
    #: nothing here rewrites it — so every count below folds both sources together.
    tick_counters: dict[str, dict] = field(default_factory=dict)
    #: KYO-418.c6: per-dispatch counts of ``unresolved`` stops --
    #: ``{dispatch_id: {"count", "first_seen", "last_seen", "reasons": {code: n}}}`` --
    #: written in place of the rows :attr:`unresolved` reads. Folded into every count
    #: below the same way :attr:`tick_counters` is, so no reader has to know which of
    #: the two wrote a number.
    unresolved_counters: dict[str, dict] = field(default_factory=dict)
    #: Set when the ledger could not be read at all — law 6's third answer, kept
    #: apart from "read it and there was nothing".
    unreadable: str | None = None

    @property
    def unresolved(self) -> list[StopAudit]:
        return [audit for audit in self.audits if audit.unresolved]

    @property
    def helpers(self) -> list[StopAudit]:
        """KYO-154: the recorded stops that were a worker's helper, not a worker."""
        return [audit for audit in self.audits if audit.helper]

    @property
    def untranscribed(self) -> list[StopAudit]:
        """KYO-373: the recorded stops by an agent with no transcript — not a worker."""
        return [audit for audit in self.audits if audit.untranscribed]

    @property
    def unplaced(self) -> list[StopAudit]:
        """Unmatched worker stops and untranscribed ones together, in ledger order.

        KYO-163 measured this population as one before KYO-373 split it, and the
        measurement is still about the whole of it: :meth:`tick_profile` reads this.
        """
        return [audit for audit in self.audits if audit.unplaced]

    def counted_ticks(self) -> int:
        """KYO-375.c1: ticks counted since the write side stopped recording them.

        Summed across every dispatch's counter — never per-row, because a counter
        carries no per-tick id to iterate.
        """
        return sum(counter.get("count", 0) for counter in self.tick_counters.values())

    def counted_unresolved(self) -> int:
        """KYO-418.c6: unresolved stops counted since the write side stopped recording
        them as rows. Summed across dispatches, as :meth:`counted_ticks` is: a stop
        counted in place is counted once on EACH running candidate it could have been,
        so this is a count of (stop, candidate) pairs, never fewer than the stops."""
        return sum(counter.get("count", 0) for counter in self.unresolved_counters.values())

    def census(self) -> dict[str, int]:
        """KYO-373.c3 / KYO-375.c1: the ledger's denominator, split three ways.

        ``matched + unmatched_worker + not_a_worker + other == recorded``. ``other``
        is an outcome string this build does not know — a row from a newer build —
        counted rather than dropped, so the sum is always the whole ledger.

        ``no_transcript`` (and therefore ``not_a_worker`` and ``recorded``) folds in
        :meth:`counted_ticks`: a tick since KYO-375 writes no row to ``audits`` at
        all, so a census that only summed rows would silently shrink the moment the
        write side stopped growing the ledger. This is the fold that keeps the
        numbers this store's own history already reads (KYO-375's task) — a
        pre-cutover row and a post-cutover count both land in the same total.
        """
        matched = sum(1 for audit in self.audits if audit.outcome == STOP_TARGET_MATCHED)
        legacy_unmatched = len(self.unresolved)
        counted_unresolved = self.counted_unresolved()
        unmatched = legacy_unmatched + counted_unresolved
        helpers = len(self.helpers)
        legacy_untranscribed = len(self.untranscribed)
        counted = self.counted_ticks()
        no_transcript = legacy_untranscribed + counted
        return {
            "recorded": len(self.audits) + counted + counted_unresolved,
            "matched": matched,
            "unmatched_worker": unmatched,
            "not_a_worker": helpers + no_transcript,
            "helpers": helpers,
            "no_transcript": no_transcript,
            "other": (
                len(self.audits) - matched - legacy_unmatched - helpers - legacy_untranscribed
            ),
        }

    def census_sentence(self) -> str:
        """The census in one line — what `kyo doctor` prints beside ``unresolved_stops``."""
        if self.unreadable is not None:
            return self.sentence()
        c = self.census()
        other = f", {c['other']} of an outcome this build does not know" if c["other"] else ""
        return (
            f"stop ledger: {c['recorded']} recorded — {c['matched']} matched to their "
            f"worker's worktree, {c['unmatched_worker']} unmatched worker stop(s), "
            f"{c['not_a_worker']} not a worker ({c['helpers']} helper(s) of a worker, "
            f"{c['no_transcript']} agent(s) with no transcript){other} (KYO-373)"
        )

    def by_dispatch(self) -> dict[str, dict[str, int]]:
        """KYO-373.c1 / KYO-375.c1: per candidate dispatch, the unplaced stops that
        named it, by reason.

        Each dispatch maps to ``{"unmatched": n, <STOP_REASON_*>: n, ...,
        "not_a_worker": n}`` — the reason counts cover the UNMATCHED worker stops, and
        ``not_a_worker`` counts the untranscribed ones apart, because c2 is that they
        are not unmatched worker stops. Insertion order is first-named order for the
        rows below; a dispatch named ONLY by counted ticks (no row of its own in
        ``unplaced``) is appended after them, in :attr:`tick_counters`' own order —
        there is no earlier row of its to have named it first.
        """
        out: dict[str, dict[str, int]] = {}
        for audit in self.unplaced:
            for dispatch_id in audit.candidates:
                counts = out.setdefault(
                    dispatch_id,
                    {"unmatched": 0, **dict.fromkeys(STOP_REASON_LABELS, 0), "not_a_worker": 0},
                )
                if audit.untranscribed:
                    counts["not_a_worker"] += 1
                else:
                    counts["unmatched"] += 1
                    counts[audit.reason or STOP_REASON_OTHER] += 1
        # KYO-375.c1: a tick since this shipped left no row above — its count lives
        # only on the candidate dispatch's own counter, folded in here the same way
        # a legacy untranscribed row is, so a reader of this map never has to know
        # which of the two wrote any given number.
        for dispatch_id, counter in self.tick_counters.items():
            counts = out.setdefault(
                dispatch_id,
                {"unmatched": 0, **dict.fromkeys(STOP_REASON_LABELS, 0), "not_a_worker": 0},
            )
            counts["not_a_worker"] += counter.get("count", 0)
        # KYO-418.c6: and an unresolved stop since then, counted on the dispatch with
        # its reason code -- the same two numbers a legacy row adds above.
        for dispatch_id, counter in self.unresolved_counters.items():
            counts = out.setdefault(
                dispatch_id,
                {"unmatched": 0, **dict.fromkeys(STOP_REASON_LABELS, 0), "not_a_worker": 0},
            )
            counts["unmatched"] += counter.get("count", 0)
            reasons = counter.get("reasons")
            for code, n in (reasons.items() if isinstance(reasons, dict) else []):
                if isinstance(n, int):
                    key = code if code in STOP_REASON_LABELS else STOP_REASON_OTHER
                    counts[key] += n
        return out

    @property
    def latest_at(self) -> str | None:
        return self.audits[-1].at if self.audits else None

    def tick_profile(self) -> TickProfile:
        """KYO-163.c4: measure the unresolved rows' arrival pattern. Never raises.

        Measured AGAINST DISPATCH ACTIVITY rather than against wall-clock: each gap
        is multiplied by the number of running dispatches the row itself recorded as
        candidates, so N agents ticking once per period each collapse to one period
        rather than to a rate that looks N times more alarming the more work is in
        flight. That is the quantity a future recurrence is recognised by, and
        storing the candidates on every row (KYO-134.c2) is what makes it computable
        after the fact instead of only live.
        """
        # KYO-373: over the unplaced population, not the unmatched-worker part of it.
        # The tick IS the untranscribed rows; measuring only what is left after they
        # were classified would measure the tick's absence.
        unresolved = self.unplaced
        profile = TickProfile(rows=len(unresolved))
        if not unresolved:
            return profile
        profile.first_at = unresolved[0].at
        profile.last_at = unresolved[-1].at
        profile.distinct_agents = len({a.agent_id for a in unresolved if a.agent_id})
        products: list[float] = []
        for earlier, later in zip(unresolved, unresolved[1:], strict=False):
            start = _stop_audit_seconds(earlier.at)
            end = _stop_audit_seconds(later.at)
            if start is None or end is None:
                continue
            gap = end - start
            # A negative gap means the ledger is not in the time order `stop_audits`
            # promises; count nothing rather than a nonsense cadence.
            if gap < 0 or gap > _STOP_TICK_MAX_GAP_SECONDS:
                continue
            concurrent = len(earlier.candidates)
            if concurrent < 1:
                continue
            products.append(gap * concurrent)
        if products:
            profile.cadence_seconds = statistics.median(products)
        return profile

    def sentence(self) -> str:
        """The three-way answer, in the words every surface renders (KYO-134.c5).

        Never "no unresolved stops" over an empty ledger. An empty section is not
        evidence of absence, and this project has learned that three times.
        """
        if self.unreadable is not None:
            return (
                "stop audits: CANNOT TELL — this store's event ledger could not be read "
                f"({self.unreadable}), so nothing here says whether any stop went unaudited"
            )
        counted_unresolved = self.counted_unresolved()
        if not self.audits and counted_unresolved:
            # KYO-418.c6: the unmatched stops are counts now, not rows, so an empty
            # row set says nothing about them.
            return (
                f"stop audits: none recorded as rows, and {counted_unresolved} stop(s) "
                "that could NOT be matched to a running dispatch's worktree were COUNTED "
                "instead (KYO-418), so nothing was checked about those trees"
            )
        if not self.audits:
            counted = self.counted_ticks()
            if counted:
                # KYO-375: a store whose only stops so far are ticks writes no row at
                # all, so "none recorded" below would be law 6's forbidden reading —
                # absence rendered as "every stop was checked" rather than as itself.
                return (
                    f"stop audits: none recorded as rows, and {counted} tick(s) by "
                    "agent(s) with no transcript were COUNTED instead (KYO-375) — not "
                    "a dispatched worker, so nothing here says whether a real worker's "
                    "stop has gone unaudited"
                )
            return (
                "stop audits: none recorded in this store yet. This is NOT the same as "
                "'every stop was checked' — it is the absence of the evidence. A row is "
                "written only when a SubagentStop happens while a dispatch has a declared "
                "worktree, so an empty ledger is what a project that has not dispatched "
                "into a worktree since this build looks like"
            )
        unresolved = self.unresolved
        # KYO-154: a helper's stop is recorded and is not unresolved, so without this
        # clause the sentence below would claim every recorded stop "resolved to the
        # stopping worker's own worktree" while some of them resolved to the opposite
        # finding — that the stopping agent had no worktree of its own at all. Two
        # different facts, and the first sentence this project wrote for a ledger it
        # could not yet count is not allowed to absorb the second silently.
        helpers = self.helpers
        # Sentence-medial in both branches, so KYO-134's own closing clause survives
        # verbatim. That clause is pinned by a test, and it should be: it is the
        # sentence a controller reads to decide nothing needs looking at.
        # KYO-373: and the agents with no transcript, the same way and for the same
        # reason — placed, as not-a-worker, and therefore not in the unmatched count.
        untranscribed = self.untranscribed
        counted_ticks = self.counted_ticks()
        notes = [
            *(
                [
                    f"{len(helpers)} of them to a HELPER a worker spawned rather than to "
                    "the worker, identified as such and deliberately not audited — KYO-154"
                ]
                if helpers
                else []
            ),
            *(
                [
                    f"{len(untranscribed)} of them to an agent with NO transcript "
                    "anywhere, which is not a dispatched worker and was checked against "
                    "no directory — KYO-373"
                ]
                if untranscribed
                else []
            ),
            *(
                [
                    f"{counted_ticks} further tick(s) by an agent with no transcript "
                    "were COUNTED rather than recorded here — KYO-375"
                ]
                if counted_ticks
                else []
            ),
        ]
        helper_note = f" ({'; '.join(notes)})" if notes else ""
        if counted_unresolved:
            return (
                f"stop audits: {len(unresolved)} of {len(self.audits)} recorded stop(s), "
                f"and {counted_unresolved} further stop(s) counted rather than recorded "
                "(KYO-418), could NOT be matched to the worktree the stopping worker was "
                f"dispatched into{helper_note}, so nothing was checked about that worker's "
                "tree"
            )
        if not unresolved:
            return (
                f"stop audits: {len(self.audits)} recorded, most recent {self.latest_at} — "
                f"every one of them was placed{helper_note}, so no stop is known to have "
                "gone unaudited"
            )
        return (
            f"stop audits: {len(unresolved)} of {len(self.audits)} recorded stop(s) could "
            "NOT be matched to the worktree the stopping worker was dispatched into"
            f"{helper_note}, so nothing was checked about that worker's tree"
        )

    def as_json(self) -> dict:
        tick = self.tick_profile()
        return {
            "recorded": len(self.audits),
            # KYO-418.c6: unresolved stops counted in place, per running candidate.
            "unresolved_counted": dict(self.unresolved_counters),
            "unresolved": [
                {
                    "event_id": audit.event_id,
                    "at": audit.at,
                    "why": audit.why,
                    "fell_back_to": audit.directory,
                    "candidates": list(audit.candidates),
                    "reason": audit.reason,
                }
                for audit in self.unresolved
            ],
            # KYO-154: counted, not listed. A helper's stop is not a finding — it is
            # the evidence that the discriminator ran, which is the same reason
            # KYO-134.c5 writes `matched` rows at all.
            "helpers": len(self.helpers),
            # KYO-373.c1/c2: every untranscribed stop, per stop, in the same shape as
            # `unresolved` — moved out of that list because they are not unmatched
            # worker stops, never dropped. `fell_back_to` is null on a row written
            # since KYO-373, because nothing fell back: no directory was read.
            "no_transcript": [
                {
                    "event_id": audit.event_id,
                    "at": audit.at,
                    "why": audit.why,
                    "fell_back_to": (
                        None
                        if audit.outcome == STOP_TARGET_NO_TRANSCRIPT
                        else audit.directory
                    ),
                    "candidates": list(audit.candidates),
                    "agent_id": audit.agent_id,
                }
                for audit in self.untranscribed
            ],
            # KYO-375.c1: the counter a tick increments in place instead of writing
            # a row above — one entry per dispatch it has ever named, never dropped
            # on rotation the way a `no_transcript` row above never was either.
            "tick_counters": dict(self.tick_counters),
            "census": self.census(),
            "by_dispatch": self.by_dispatch(),
            "latest_at": self.latest_at,
            "unreadable": self.unreadable,
            "sentence": self.sentence(),
            # KYO-163.c4: the measurement travels with the ledger, so a recurrence is
            # recognised from stored data rather than re-derived from transcripts that
            # will not survive the session.
            "arrival_pattern": tick.as_json(),
        }

    def naming(self, dispatch_id: str) -> list[StopAudit]:
        """The unresolved stops that named ``dispatch_id`` as a candidate.

        KYO-134.c2 from the other end: a controller looking at one worktree wants
        to know whether a stop that could have been this one went unaudited.
        """
        return [audit for audit in self.unresolved if dispatch_id in audit.candidates]

    def naming_unplaced(self, dispatch_id: str) -> list[StopAudit]:
        """:meth:`naming`, with the untranscribed stops too (KYO-373).

        KYO-375: this no longer sees a tick counted since the write side stopped
        recording them — see :meth:`ticks_naming` for that count, kept apart because
        a counter carries no per-row :class:`StopAudit` to return.
        """
        return [audit for audit in self.unplaced if dispatch_id in audit.candidates]

    def ticks_naming(self, dispatch_id: str) -> int:
        """KYO-375.c2: how many ticks — legacy rows plus the counter — named
        ``dispatch_id`` as a candidate. A count, not a list: the counter has no
        per-tick agent id or timestamp to hand back, only the totals.

        :func:`worker_tokens_from` reads this apart from :meth:`naming`, which is
        WORKER-shaped unmatched stops only — the split c2 asks for.
        """
        legacy = sum(1 for audit in self.untranscribed if dispatch_id in audit.candidates)
        return legacy + self.tick_counters.get(dispatch_id, {}).get("count", 0)

    def latest_matched(self, dispatch_id: str) -> StopAudit | None:
        """The last recorded stop the resolution matched to ``dispatch_id``.

        KYO-286: the closest thing this store holds to "when that worker stopped".
        The LAST one and not the first, because the ~32s SubagentStop tick (KYO-163)
        writes a matched row per period while the worker runs, so the first is when
        it started being noticed and the last is when it was last seen alive — which
        is within one tick of the moment it really stopped, and is the only end time
        anything here can observe. :attr:`audits` is oldest-first (`stop_audits`
        orders by ``seq``), so this is the tail of the matched ones.
        """
        matched = [
            audit
            for audit in self.audits
            if audit.dispatch_id == dispatch_id and audit.outcome == STOP_TARGET_MATCHED
        ]
        return matched[-1] if matched else None


def _home_directories(ctx: Context) -> list[str]:
    """KYO-411: the spellings of the user's home directory a stored path may start
    with — ``$HOME`` as given and its resolved form, longest first so the more specific
    one is replaced before its prefix."""
    import os

    home = (ctx.environ or {}).get("HOME") or os.path.expanduser("~")
    spellings = set()
    for candidate in (home, os.path.realpath(home)):
        candidate = candidate.rstrip("/")
        if candidate and candidate != "~" and len(candidate) > 1:
            spellings.add(candidate)
    return sorted(spellings, key=len, reverse=True)


def _home_relative(ctx: Context, text: str) -> str:
    """KYO-411: ``text`` with every absolute home-directory prefix written as ``~``.

    Only a prefix followed by ``/`` or the end of a path is replaced, so a home of
    ``/Users/ann`` never rewrites ``/Users/anna``. :func:`stop_audits` expands a
    ``~``-relative transcript path back when it reads the row.
    """
    for home in _home_directories(ctx):
        text = re.sub(re.escape(home) + r"(?=/|$|[\s'\"`),;:])", "~", text)
    return text


def _portable_stop_audit(ctx: Context, target: StopTarget) -> dict:
    """KYO-411: :meth:`StopTarget.as_json` with its paths in stored, portable form.

    ``directory`` goes through the same rule every stored worktree does
    (:meth:`Context.store_worktree`: the ``<worktrees>`` token under the worktree home,
    code-root-relative otherwise — KYO-52/KYO-70), ``transcript`` is written relative
    to the home directory (``~/...``), and ``why`` has its absolute home prefixes
    replaced the same way. Never raises: a directory with no relative form falls back
    to the home-relative spelling, because a hook must not die on the store.
    """
    stored = target.as_json()
    try:
        stored["directory"] = ctx.store_worktree(target.directory)
    except Exception:  # noqa: BLE001 - a hook must never die on the store
        stored["directory"] = _home_relative(ctx, str(target.directory))
    if stored.get("transcript"):
        stored["transcript"] = _home_relative(ctx, stored["transcript"])
    stored["why"] = _home_relative(ctx, stored.get("why") or "")
    return stored


def record_stop_audit(
    ctx: Context, target: StopTarget, raw_payload: str
) -> tuple[str | None, str]:
    """Write one durable row for a hook-driven stop resolution. Never raises.

    Returns ``(event_id, sentence)``; ``event_id`` is ``None`` when nothing was
    written, and the sentence always says which of the three that was — recorded,
    deliberately not recorded, or attempted and failed. A guard renders it, so a
    write that did not happen is never silent.

    KYO-375.c1: never called for :data:`STOP_TARGET_NO_TRANSCRIPT` since this
    shipped — see :func:`record_tick_audit`, which `check-clean` calls instead for
    that one outcome. A not-a-worker tick is the class of stop this ledger now
    summarises rather than records.
    """
    if target.outcome == STOP_TARGET_NO_WORKTREES:
        return None, (
            "not recorded: no running dispatch had a declared worktree, so there was "
            "nothing about this stop that could have gone unaudited"
        )
    payload = _payload_of(raw_payload)
    after = {
        # KYO-411: never `target.as_json()` verbatim. The events
        # ledger is git-canonical, and that held the controller checkout's and the
        # harness transcript's absolute paths — one machine's layout and its username,
        # which KYO-52 took out of every other stored path.
        "stop_audit": _portable_stop_audit(ctx, target),
        # Which stop this was, as the harness named it. Not matched against
        # anything here — it is what lets a human line one of these rows up with a
        # session after the fact, which is the whole point of a durable record.
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id") or payload.get("agentId"),
    }
    try:
        store = ctx.store()
        with store.transaction():
            event = store.record_event(
                actor=ctx.actor,
                command=STOP_AUDIT_COMMAND,
                record_id=None,
                before=None,
                after=after,
            )
        events_ledger.append_events(store, [event])
        ledgers.write_allocations(store)
    except Exception as exc:  # noqa: BLE001 - a hook must never die on the store
        return None, (
            f"NOT RECORDED: this stop could not be written to the store ({exc}), so it "
            "survives only in this transcript — the exact gap KYO-134 exists to close"
        )
    return event["id"], f"recorded durably as {event['id']}; `kyo worktrees` lists these"


#: KYO-418.c6: the key inside ``dispatches.tick_audit_json`` an ``unresolved`` stop is
#: counted under, beside the tick counter's own ``count``/``first_seen``/``last_seen``.
#: ``{"count", "first_seen", "last_seen", "reasons": {STOP_REASON_*: n}}``. An older
#: counter has no such key, which reads as zero -- nothing was counted there then.
UNRESOLVED_COUNTER_KEY = "unresolved"


def _running_candidates(store, candidates: list[str]) -> list[tuple[str, str, str | None]]:
    """``(dispatch_id, record_id, tick_audit_json)`` for each candidate still RUNNING.

    KYO-418.c7. The candidate list comes from
    :func:`_declared_worktree_rows`, which by KYO-201 ignores state and keeps every
    dispatch whose directory still exists -- right for MATCHING a stop, because a
    worker can stop after its report was ingested. It is wrong for COUNTING one: a
    counter on a reported or done dispatch measured how long its directory sat
    unpruned, and every ~32 s tick rewrote that finished record's export (a dirty
    record document for a dispatch already reported). Only a
    running dispatch is one a stop could still be about.
    """
    out: list[tuple[str, str, str | None]] = []
    for dispatch_id in candidates:
        row = store.conn.execute(
            "SELECT record_id, state, tick_audit_json FROM dispatches WHERE id = ?",
            (dispatch_id,),
        ).fetchone()
        if row is None or row["state"] != DISPATCH_STATE_RUNNING:
            continue
        out.append((dispatch_id, row["record_id"], row["tick_audit_json"]))
    return out


def _load_counter(raw: str | None) -> dict:
    try:
        counter = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        counter = {}
    return counter if isinstance(counter, dict) else {}


def _count_in_place(
    ctx: Context, target: StopTarget, *, unresolved: bool
) -> tuple[list[str], str | None]:
    """Increment the in-place stop counter on each RUNNING candidate; ``(counted, error)``.

    One writer for both classes a stop is counted rather than recorded for: a
    not-a-worker tick (KYO-375, the counter's top-level ``count``) and an ``unresolved``
    stop (KYO-418, under :data:`UNRESOLVED_COUNTER_KEY`, with its reason). Only the
    counted dispatches' own records are re-exported, so the count survives a
    ``kyo rebuild``; never a finished dispatch's (:func:`_running_candidates`).
    """
    stamp = now()
    counted: list[str] = []
    try:
        store = ctx.store()
        touched_record_ids: set[str] = set()
        with store.transaction():
            for dispatch_id, record_id, raw in _running_candidates(store, target.candidates):
                counter = _load_counter(raw)
                slot = counter
                if unresolved:
                    slot = counter.get(UNRESOLVED_COUNTER_KEY)
                    if not isinstance(slot, dict):
                        slot = {}
                    reasons = slot.get("reasons")
                    reasons = dict(reasons) if isinstance(reasons, dict) else {}
                    code = target.reason or STOP_REASON_OTHER
                    reasons[code] = int(reasons.get(code) or 0) + 1
                    slot["reasons"] = reasons
                    counter[UNRESOLVED_COUNTER_KEY] = slot
                slot["count"] = int(slot.get("count") or 0) + 1
                slot["first_seen"] = slot.get("first_seen") or stamp
                slot["last_seen"] = stamp
                store.conn.execute(
                    "UPDATE dispatches SET tick_audit_json = ? WHERE id = ?",
                    (json.dumps(counter, sort_keys=True), dispatch_id),
                )
                counted.append(dispatch_id)
                touched_record_ids.add(record_id)
        if touched_record_ids:
            export.write_exports(store, sorted(touched_record_ids))
    except Exception as exc:  # noqa: BLE001 - a hook must never die on the store
        return [], str(exc)
    return counted, None


def record_tick_audit(ctx: Context, target: StopTarget) -> str:
    """KYO-375.c1: what ``check-clean`` does for a not-a-worker tick, in place of
    :func:`record_stop_audit`. Never raises — a hook must never die on the store.

    A tick writes NO events row. It increments a count plus first/last-seen timestamps on
    each candidate dispatch's own row (``dispatches.tick_audit_json``, the object
    :func:`stop_audits` already reads), overwriting a small bounded value rather than
    appending, and re-exports that record so the count survives `kyo rebuild`
    (docs/design/dispatch.md#kyo-375).

    KYO-418.c7: counted against RUNNING candidates only (:func:`_running_candidates`),
    so a tick never rewrites the export of a reported or done dispatch's record.

    Deliberately does NOT go through ``commands._touch_record`` /
    ``_commit_and_export``: those stamp ``records.updated_at``, which is read
    elsewhere as "when was this record's work last touched" (docs staleness,
    listings) and must not move every ~32 seconds a tick happens to name a
    dispatch that record has nothing to do with finishing.
    """
    if not target.candidates:
        return (
            "not counted: this tick named no running dispatch with a declared "
            "worktree as a candidate, so there was nothing to count it against"
        )
    counted, error = _count_in_place(ctx, target, unresolved=False)
    if error is not None:
        return (
            f"NOT COUNTED: this tick's per-dispatch counter could not be written "
            f"({error}), so it survives only in this transcript"
        )
    if not counted:
        return (
            "not counted: none of this tick's candidate dispatch(es) is still running, "
            "so there was nothing it could have been about"
        )
    return (
        f"counted against {len(counted)} candidate dispatch(es)' tick audit "
        "(KYO-375) — `kyo worktrees` and `kyo doctor` read it; no row was written "
        "to the events ledger"
    )


def record_unresolved_audit(ctx: Context, target: StopTarget) -> str:
    """KYO-418.c6: what ``check-clean`` does for an ``unresolved`` stop, in place of
    :func:`record_stop_audit`. Never raises.

    Everything that reads unresolved stops reads their count -- ``kyo doctor``'s
    ``unresolved_stops`` notice, ``kyo worktrees``' per-dispatch reasons, the census -- so
    the count is kept IN PLACE on each running candidate's counter
    (:data:`UNRESOLVED_COUNTER_KEY`), with the stop's reason code, and survives
    ``kyo rebuild`` through that record's export (docs/design/dispatch.md#kyo-418).

    It is counted, not reclassified. KYO-163 refused to stop WRITING these rows because
    that would mean deciding the stopping agent was not a worker; this decides nothing
    of the kind. The stop stays ``unresolved``, the doctor notice still counts it while
    a candidate is running, and the only thing that changes is that its record is a
    number rather than a line. What a counter cannot carry is the per-stop detail (agent
    id, transcript, time of each stop), and that is the same trade KYO-375 made for
    ticks.
    """
    if not target.candidates:
        return (
            "not counted: this stop named no dispatch with a declared worktree as a "
            "candidate, so there was nothing to count it against"
        )
    counted, error = _count_in_place(ctx, target, unresolved=True)
    if error is not None:
        return (
            f"NOT COUNTED: this unmatched stop's per-dispatch counter could not be written "
            f"({error}), so it survives only in this transcript"
        )
    if not counted:
        return (
            "not counted: none of this stop's candidate dispatch(es) is still running, "
            "so there is no running worktree it could have been about"
        )
    return (
        f"counted as unmatched against {len(counted)} running candidate dispatch(es) "
        "(KYO-418) — `kyo worktrees` and `kyo doctor` read it; no row was written to "
        "the events ledger"
    )


#: KYO-418.c6: the stop outcomes counted IN PLACE rather than written as an events row,
#: and the writer that counts each. `check-clean` consults this one table, so which
#: outcomes are rows is decided here and nowhere else: every outcome not named below
#: (`matched`, a helper of a worker, an unreadable store) still gets a row through
#: :func:`record_stop_audit`.
COUNTED_IN_PLACE = {
    STOP_TARGET_NO_TRANSCRIPT: record_tick_audit,
    STOP_TARGET_UNRESOLVED: record_unresolved_audit,
}


def stop_audits(store) -> StopAuditLedger:
    """Every recorded stop resolution, oldest first, plus every dispatch's counted-
    tick audit. Never raises.

    Read by `kyo doctor` (``doctor_dispatch.check_unresolved_stops``) and by
    `kyo worktrees` (:func:`cmd_worktrees`) — KYO-134.c3, surfaces a controller
    already reads rather than a new command nobody runs.
    """
    import os

    try:
        rows = store.conn.execute(
            "SELECT id, at, after_json FROM events WHERE command = ? ORDER BY seq",
            (STOP_AUDIT_COMMAND,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - a read surface must degrade, not die
        return StopAuditLedger(unreadable=str(exc))
    ledger = StopAuditLedger()
    for row in rows:
        try:
            after = json.loads(row["after_json"] or "{}")
            subject = after.get("stop_audit") or {}
        except json.JSONDecodeError:
            continue
        outcome = subject.get("outcome")
        if not isinstance(outcome, str):
            continue
        candidates = subject.get("candidates")
        ledger.audits.append(
            StopAudit(
                event_id=row["id"],
                at=row["at"],
                outcome=outcome,
                directory=str(subject.get("directory") or ""),
                why=str(subject.get("why") or ""),
                dispatch_id=subject.get("dispatch_id"),
                candidates=[c for c in (candidates or []) if isinstance(c, str)],
                # KYO-163. Read from `after` and not from `stop_audit`: the agent id
                # is the HARNESS's name for the stop, stored beside the resolution
                # rather than inside it (KYO-134), and it is absent on rows written
                # before that. `None` there, and counted as unknown, never as a
                # distinct id.
                agent_id=(
                    after.get("agent_id") if isinstance(after.get("agent_id"), str) else None
                ),
                helper_of=subject.get("helper_of"),
                stored_reason=(
                    subject.get("reason") if isinstance(subject.get("reason"), str) else None
                ),
                # KYO-411: a row written since then stores the transcript relative to
                # the home directory (`~/...`); expanded here so every reader —
                # `worker_tokens` opens the file — gets a path it can open. A legacy
                # absolute value passes through `expanduser` unchanged.
                transcript=(
                    os.path.expanduser(subject["transcript"])
                    if isinstance(subject.get("transcript"), str)
                    else None
                ),
            )
        )
    # KYO-375.c1: every dispatch's counted-tick audit, read alongside the rows
    # above. A separate query and a separate table — a tick since this shipped
    # never reaches `events` at all — degraded independently of the read above so
    # a `dispatches` read that fails does not also blank out the legacy rows this
    # store already has.
    try:
        counter_rows = store.conn.execute(
            "SELECT id, tick_audit_json FROM dispatches WHERE tick_audit_json IS NOT NULL"
        ).fetchall()
    except Exception:  # noqa: BLE001 - a read surface must degrade, not die
        counter_rows = []
    for row in counter_rows:
        try:
            counter = json.loads(row["tick_audit_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(counter, dict):
            continue
        if isinstance(counter.get("count"), int):
            ledger.tick_counters[row["id"]] = counter
        # KYO-418.c6: the unresolved stops counted on this dispatch since they stopped
        # being written as rows.
        unresolved = counter.get(UNRESOLVED_COUNTER_KEY)
        if isinstance(unresolved, dict) and isinstance(unresolved.get("count"), int):
            ledger.unresolved_counters[row["id"]] = unresolved
    return ledger


# ---------------------------------------- KYO-592: a repeat stop that was not blocked
#
# Claude Code sets ``stop_hook_active`` on a Stop/SubagentStop payload when the agent is
# already continuing because a stop hook blocked it. The first stop blocks with the full
# remedy; a repeat stop reports the finding and does not block again (the KYO-128 rule:
# do not keep blocking a party that cannot act), for `check-clean` and `check-branch`
# alike. What it did not enforce is written down here, in the same `events` ledger KYO-134's
# stop audit uses, so the orchestrator sees it in `kyo show <dispatch-id>` and in `kyo
# report` rather than only in a transcript nobody reads. A row of its own, under its own
# ``command``, rather than a field on the stop-audit row: `check-clean` writes that row
# before it reads the tree (KYO-134), and `check-branch` writes none, so neither has the
# verdict when that row is written.

#: The ``events.command`` value of one unblocked repeat stop.
STOP_UNBLOCKED_COMMAND = "subagent-stop unblocked"

#: At most this many finding lines are stored per row; the rest are counted. A dirty
#: worktree can list hundreds of paths, and the events ledger is git-canonical.
UNBLOCKED_FINDING_LINES = 20


@dataclass(frozen=True)
class UnblockedStop:
    """One repeat stop a guard reported without blocking (KYO-592)."""

    event_id: str
    at: str
    guard: str
    contract: str
    dispatch_id: str | None
    finding: list[str]
    omitted: int = 0

    def line(self) -> str:
        """One line naming the stop, for the surfaces that list these."""
        first = self.finding[0] if self.finding else "(no finding recorded)"
        return f"{self.at} {self.guard}: {self.contract} -- {first} ({self.event_id})"


def record_unblocked_stop(
    ctx: Context,
    *,
    guard: str,
    target: StopTarget,
    raw_payload: str,
    contract: str,
    finding: list[str],
) -> tuple[str | None, str]:
    """Write one durable row for a repeat stop that was reported, not blocked. Never
    raises: like :func:`record_stop_audit` it runs inside a hook, and returns
    ``(event_id, sentence)`` with ``event_id`` ``None`` when nothing was written."""
    payload = _payload_of(raw_payload)
    kept = [_home_relative(ctx, line) for line in finding[:UNBLOCKED_FINDING_LINES]]
    try:
        directory = ctx.store_worktree(target.directory)
    except Exception:  # noqa: BLE001 - a hook must never die on the store
        directory = _home_relative(ctx, str(target.directory))
    after = {
        "unblocked_stop": {
            "guard": guard,
            "contract": contract,
            "dispatch_id": target.dispatch_id or target.helper_of,
            "outcome": target.outcome,
            "directory": directory,
            "finding": kept,
            "omitted": max(0, len(finding) - len(kept)),
        },
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id") or payload.get("agentId"),
    }
    try:
        store = ctx.store()
        with store.transaction():
            event = store.record_event(
                actor=ctx.actor,
                command=STOP_UNBLOCKED_COMMAND,
                record_id=None,
                before=None,
                after=after,
            )
        events_ledger.append_events(store, [event])
        ledgers.write_allocations(store)
    except Exception as exc:  # noqa: BLE001 - a hook must never die on the store
        return None, (
            f"NOT RECORDED: this unblocked stop could not be written to the store ({exc}), "
            "so it survives only in this transcript"
        )
    return event["id"], (
        f"recorded durably as {event['id']}; `kyo show <dispatch-id>` and `kyo report` "
        "list these"
    )


def unblocked_stops(store, dispatch_id: str | None = None) -> list[UnblockedStop]:
    """Every unblocked repeat stop, oldest first, or only ``dispatch_id``'s. Never
    raises: a read surface degrades to nothing rather than failing the command."""
    try:
        rows = store.conn.execute(
            "SELECT id, at, after_json FROM events WHERE command = ? ORDER BY seq",
            (STOP_UNBLOCKED_COMMAND,),
        ).fetchall()
    except Exception:  # noqa: BLE001 - a read surface must degrade, not die
        return []
    found: list[UnblockedStop] = []
    for row in rows:
        try:
            subject = json.loads(row["after_json"] or "{}").get("unblocked_stop") or {}
        except (AttributeError, json.JSONDecodeError):
            continue
        if not isinstance(subject, dict):
            continue
        owner = subject.get("dispatch_id") if isinstance(subject.get("dispatch_id"), str) else None
        if dispatch_id is not None and owner != dispatch_id:
            continue
        finding = subject.get("finding")
        found.append(
            UnblockedStop(
                event_id=row["id"],
                at=row["at"],
                guard=str(subject.get("guard") or "?"),
                contract=str(subject.get("contract") or "?"),
                dispatch_id=owner,
                finding=[str(line) for line in finding] if isinstance(finding, list) else [],
                omitted=subject.get("omitted") if isinstance(subject.get("omitted"), int) else 0,
            )
        )
    return found


def unblocked_stop_lines(stops: list[UnblockedStop]) -> list[str]:
    """What `kyo show <dispatch-id>` and `kyo report` print for these: nothing when
    there are none, otherwise a count and one line each."""
    if not stops:
        return []
    return [
        f"unblocked repeat stops: {len(stops)} -- a stop guard reported a finding without "
        "blocking, because the worker was already continuing from a blocked stop "
        "(stop_hook_active, KYO-592); check each one before accepting",
        *(f"  {stop.line()}" for stop in stops),
    ]
