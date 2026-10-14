"""What a dispatch's worker spent, and when the dispatch ended, from the stop ledger."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import DISPATCH_STATE_RUNNING
from .stop_audit import StopAudit, StopAuditLedger, stop_audits
from .stop_target import (
    STOP_TARGET_MATCHED,
    STOP_TARGET_NO_WORKTREES,
    STOP_TARGET_STORE_UNREADABLE,
    STOP_TARGET_UNRESOLVED,
)

# ------------------- KYO-284: what a dispatch's worker cost, counted in tokens
#
# Nothing here searches for a transcript. The only record of what a worker cost is the
# harness's own transcript (a `usage` object on every assistant message), and a second
# search of the harness's private layout could disagree with KYO-128's about which file is
# whose. `resolve_stop_target` already matches a stopping agent's transcript to a dispatch
# by the dispatch id Kyosei minted, and KYO-134 writes every resolution to the events
# ledger; this reads that ledger back, opens the file the resolution matched and adds up
# its `usage` objects. The number is right or absent, never a plausible number for the
# wrong worker; the cost is that rows written before the path was stored read
# `no-transcript-recorded` forever. History: docs/design/dispatch.md#kyo-284.
#
# WHY THE TICK DOES NOT INFLATE ANYTHING. KYO-163: SubagentStop arrives on a ~32s
# timer per running agent, so one worker produces dozens of `matched` rows naming
# the same transcript. They are deduplicated BY PATH before anything is summed, so
# a long dispatch does not read as a expensive one. A dispatch legitimately holding
# two transcripts — a worker resumed under a fresh agent id — sums both, which is
# also why this is a set of paths rather than "the" transcript.

#: No `matched` stop row for this dispatch names a transcript. Two ways to get
#: here and the sentence says which: the dispatch has no recorded stop at all, or
#: it has rows from before KYO-284 taught the resolution to store the path.
TOKENS_NO_TRANSCRIPT_RECORDED = "no-transcript-recorded"
#: The events ledger itself could not be read, so nothing here is a statement
#: about this dispatch at all. Law 6's third answer, kept apart from "read it and
#: there was nothing" exactly as :attr:`StopAuditLedger.unreadable` is.
TOKENS_LEDGER_UNREADABLE = "ledger-unreadable"
#: KYO-325: the ledger was read, and it holds no stop of ANY outcome from the window
#: this dispatch was running in. Distinct from :data:`STOP_TARGET_UNRESOLVED`, which
#: means "stops were seen and none of them matched": here nothing was seen, so no
#: matching was attempted, and the search path, the agent id and the worktree
#: comparison are all uninvolved. It is what a hook that could not open the store
#: leaves behind — see :data:`STOP_TARGET_STORE_UNREADABLE`.
TOKENS_NO_STOP_WHILE_RUNNING = "no-stop-recorded-while-running"

#: The four counts an assistant message's `usage` object carries. Named rather
#: than summed blind: a `usage` gains keys (`service_tier`, `server_tool_use`)
#: that are not token counts, and adding every integer it happens to hold is how a
#: total starts drifting the next time the harness adds one.
USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)

# --------------------- KYO-298.c6: what a full-context re-write looks like in a
#                                    transcript, and the measurement behind it
#
# The harness's prompt cache has a time-to-live. A worker that sits through a long
# foreground command — the whole suite, ten to seventeen minutes — comes back to an
# expired cache and the harness re-writes its ENTIRE context as cache creation on the
# worker's very next message. That message is the whole cost of the wait, it is
# visible in the transcript, and until this it was only ever countable by a
# controller's ad-hoc script over one wave's files.
#
# The thresholds below have a measurement behind them: docs/design/dispatch.md#kyo-298.
#
# THE THRESHOLDS ARE A STARTING POINT WITH THAT MEASUREMENT BEHIND THEM, not a law.
# Both are needed and each rules out a different false positive:
#
#   * the FLOOR (50,000) rules out a small conversation's ordinary first writes — a
#     short worker whose whole context is 30,000 tokens re-writes all of it routinely
#     and it costs nothing worth counting.
#   * the SHARE (80% of that message's whole input side) rules out a big conversation
#     doing ordinary incremental caching: a healthy long turn reads two orders of
#     magnitude more cache than it writes, so its creation share is tiny.
#
# The FIRST message of a transcript is never counted whatever it looks like: writing
# the whole context once is what starting a conversation IS, and counting it would
# report one re-write for every worker that ever ran.

#: A message's cache creation must exceed this to be a re-write at all.
CONTEXT_REWRITE_MIN_TOKENS = 50_000
#: ...and must be at least this share of that message's whole input side
#: (``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``).
CONTEXT_REWRITE_SHARE = 0.8


def _is_context_rewrite(counts: Mapping[str, int]) -> bool:
    """Does this one message's ``usage`` look like a full-context re-write?

    One place, read by the transcript reader and by every test that asserts about the
    thresholds, so the numbers in the docstrings above and the numbers the code
    applies cannot come apart (law 3).
    """
    creation = counts.get("cache_creation_input_tokens", 0)
    if creation <= CONTEXT_REWRITE_MIN_TOKENS:
        return False
    context = (
        creation
        + counts.get("input_tokens", 0)
        + counts.get("cache_read_input_tokens", 0)
    )
    return context > 0 and creation >= CONTEXT_REWRITE_SHARE * context


@dataclass
class WorkerTokens:
    """What one dispatch's worker cost, or the named reason that is not known.

    :attr:`outcome` is :data:`STOP_TARGET_MATCHED` when the counts below are real
    and one of the named outcomes otherwise — never a zero standing in for an
    unknown, which is the whole reason this is a type and not four integers.
    """

    dispatch_id: str
    outcome: str
    why: str
    #: The model the transcript names, verbatim (`claude-opus-5`), or ``None``.
    #: Several distinct models across one dispatch's transcripts are reported as
    #: all of them, joined — a resumed worker can genuinely have run on two.
    model: str | None = None
    #: How many distinct transcript files were summed, and how many assistant
    #: messages in them carried a `usage`. Both are here so a suspicious total can
    #: be traced to "it read nothing" rather than to "it read zero".
    transcripts: int = 0
    messages: int = 0
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    #: KYO-292.c2: the size of the worker's LATEST turn — all four counts of the last
    #: assistant message in the most recent transcript matched to this dispatch, cache
    #: reads INCLUDED. Not a running total and not one of the sums above: it is how big
    #: the conversation had become by the last thing the worker said.
    #:
    #: This is the figure claude.ai's agent card shows; a running sum cannot be, because
    #: the card's figure was measured smaller than one (docs/design/dispatch.md#kyo-292).
    #:
    #: The ~1% shortfall is the last message's `output_tokens`, which the transcript
    #: records mid-stream; a consumer rendering this says so rather than presenting it
    #: as exact.
    latest_turn_tokens: int = 0
    #: KYO-298.c6: how many of this worker's messages re-wrote nearly their whole
    #: context, and what that cost in cache creation. See
    #: :data:`CONTEXT_REWRITE_MIN_TOKENS` for what counts and the wave it was measured
    #: on. Summed across a resumed worker's transcripts, because each transcript is
    #: its own conversation and each one's first message is its own legitimate first
    #: write.
    #:
    #: Carried on the dispatch, in every project, because KYO-298's policy ("the whole
    #: suite is the controller's") is justified only by this measurement, and a policy
    #: whose measurement nobody can re-take outlives its evidence.
    context_rewrites: int = 0
    context_rewrite_tokens: int = 0

    @property
    def known(self) -> bool:
        """Are the counts below a measurement rather than a placeholder?"""
        return self.outcome == STOP_TARGET_MATCHED

    @property
    def total_tokens(self) -> int:
        """Every token the worker's messages were billed for, cache reads included.

        All four counts, and the third one dominates: a long agent turn re-reads a
        cached prompt every message, so `cache_read_input_tokens` is routinely two
        orders of magnitude above the rest. That makes this total a true statement
        about tokens and a poor proxy for "how much work was done", and a consumer
        that wants the second should render the components, which is why they are
        all carried separately rather than pre-mixed into one number here.
        """
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
            + self.output_tokens
        )

    def as_json(self) -> dict:
        return {
            "dispatch_id": self.dispatch_id,
            "outcome": self.outcome,
            "known": self.known,
            "why": self.why,
            "model": self.model,
            "transcripts": self.transcripts,
            "messages": self.messages,
            "input_tokens": self.input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "output_tokens": self.output_tokens,
            # KYO-292.c2: the latest turn's size, carried rather than derived
            # downstream. The desktop renders exactly this number, so there is one
            # place that decides what "tokens" means on a row and it is the place
            # that reads the transcript (law 3) — and no consumer has to know that it
            # is the LAST message's counts rather than a sum of the ones above.
            "latest_turn_tokens": self.latest_turn_tokens,
            # KYO-298.c6: ADDITIVE fields — two new keys beside the existing ones,
            # nothing renamed and nothing re-meant, so `kyo worktrees --json`'s
            # consumers (the desktop's activity poll among them) read exactly what
            # they read before and a consumer that has not heard of re-writes is
            # unaffected.
            "context_rewrites": self.context_rewrites,
            "context_rewrite_tokens": self.context_rewrite_tokens,
            "total_tokens": self.total_tokens,
        }

    def rewrite_clause(self) -> str:
        """The re-write half of :meth:`sentence`, said either way.

        Zero is a RESULT here, not an absence, so it gets a sentence of its own rather
        than being rendered by silence: "this worker never re-wrote its context" is the
        outcome KYO-298's whole mechanism is trying to produce, and a reader who
        cannot tell it apart from "nobody looked" cannot tell whether the policy is
        working.
        """
        if not self.context_rewrites:
            return (
                "no full-context re-write: no message after the first re-wrote most of "
                "its own context, which is what a worker that never sat through a long "
                "foreground command looks like"
            )
        return (
            f"{self.context_rewrites} full-context re-write(s) costing "
            f"{self.context_rewrite_tokens} tokens of cache creation — a message after "
            "the first whose cache creation was over "
            f"{CONTEXT_REWRITE_MIN_TOKENS} tokens and at least "
            f"{CONTEXT_REWRITE_SHARE:.0%} of its whole context, which is what the "
            "harness's prompt cache expiring under a long foreground command looks "
            "like (KYO-298)"
        )

    def sentence(self) -> str:
        """One line for a human, on both branches. Never a bare number."""
        if not self.known:
            return f"worker tokens: unknown — {self.why}"
        model = self.model or "(no model named by the transcript)"
        return (
            f"worker tokens: {self.total_tokens} total on {model} "
            f"({self.input_tokens} input, {self.cache_creation_input_tokens} cache "
            f"creation, {self.cache_read_input_tokens} cache read, "
            f"{self.output_tokens} output) over {self.messages} assistant message(s) "
            f"in {self.transcripts} transcript(s); latest turn {self.latest_turn_tokens} "
            "tokens (all four counts of the last assistant message — what claude.ai's "
            "agent card shows, and what the desktop's activity row shows, to within the "
            f"output tokens the transcript records mid-stream); {self.rewrite_clause()} "
            f"— {self.why}"
        )


@dataclass
class TranscriptUsage:
    """What one transcript file says about what a worker used.

    Two different quantities, kept apart because they answer different questions and
    KYO-292 is what happens when one is presented as the other. :attr:`counts` is
    CUMULATIVE — every message's usage, added up — and :attr:`latest_turn` is the
    usage of the LAST assistant message alone, which is how big the conversation had
    become by the last thing the worker said.
    """

    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(USAGE_FIELDS, 0))
    models: set[str] = field(default_factory=set)
    #: Distinct assistant messages, not transcript rows. See below.
    messages: int = 0
    #: The four counts of the last assistant message, or all zeroes for a transcript
    #: with none. Cache reads included: this is a context size, not a spend.
    latest_turn: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(USAGE_FIELDS, 0)
    )
    #: KYO-298.c6: this transcript's messages after the first that re-wrote nearly
    #: their whole context, and the cache creation they cost. A third quantity beside
    #: the two above, and a third question: not "what did this cost in total" nor "how
    #: big did the conversation get", but "how often did this worker pay for its whole
    #: context again".
    context_rewrites: int = 0
    context_rewrite_tokens: int = 0

    @property
    def latest_turn_tokens(self) -> int:
        """All four counts of that last message — what the agent card shows."""
        return sum(self.latest_turn.values())


def sum_transcript_usage(path: Path) -> TranscriptUsage:
    """Read one transcript's `usage` objects: the counts, the models, the messages,
    and the size of the worker's latest turn.

    ONE ROW IS NOT ONE MESSAGE (KYO-292.c4). The harness writes a transcript row per
    content BLOCK, and every row of one assistant message repeats that message's `id`
    and its whole `usage` object, so rows are grouped by `message.id` and the LAST row of
    each message counts: the first row's `output_tokens` is a streaming-start figure.
    Measured: docs/design/dispatch.md#kyo-292.

    A row whose message carries no `id` is its own message. That is the honest
    reading — without an id there is nothing to group it with, and dropping it would
    lose tokens that were really spent — and it is what every transcript written
    before ids appeared looks like.

    THE LATEST TURN (KYO-292.c2) is the last usage-bearing row in the file, which is
    by construction the last row of the last message — so it needs no grouping of its
    own, only the last one seen. It is carried because it, and not any sum, is the
    figure claude.ai's agent card shows; see :attr:`WorkerTokens.latest_turn_tokens`
    for the two measurements that settled that.

    Streamed a line at a time and never read whole: these files reach tens of
    megabytes, and this runs on the desktop app's activity poll. Measured on this
    project's own transcripts, 3.4 MB / 1316 lines summed in 0.014s, because the
    cheap substring test below skips every line that has no `usage` in it at all
    without paying `json.loads` for it. What is held across the stream is one small
    dict of counts per distinct message, not the file.

    Unreadable is an empty answer, never an exception: the caller's job is to say
    "unknown" with a reason, and a transcript the harness has rotated away under us
    must not take `kyo worktrees` — or the app's whole activity list — down.
    """
    models: set[str] = set()
    # The latest `usage` seen for each message, keyed by its `id` — last row wins, per
    # the streaming-start figure above — in FIRST-SEEN order, which `dict` preserves.
    #
    # KYO-298.c6 is why the order matters and why the id-less rows now live in the
    # same mapping rather than in a separate running total. A re-write is defined
    # relative to a position ("a message AFTER the first"), so the messages have to be
    # a sequence and not a bag; and a row with no id is its own message, so it has to
    # take a place in that sequence rather than being accumulated off to one side. It
    # gets a synthetic key that no real message id can collide with. The totals and
    # the message count below are computed from this one mapping and are unchanged by
    # the merge: an id-bearing message still contributes its last row once, and an
    # id-less row still contributes itself once.
    latest: dict[str, dict[str, int]] = {}
    anonymous = 0
    latest_turn = dict.fromkeys(USAGE_FIELDS, 0)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                model = message.get("model")
                if isinstance(model, str) and model:
                    models.add(model)
                counts = {
                    field_name: value
                    for field_name in USAGE_FIELDS
                    if isinstance(value := usage.get(field_name), int)
                    and not isinstance(value, bool)
                }
                # The last usage-bearing row in the file is the last row of the last
                # message, which is the whole of what the latest turn needs.
                latest_turn = {**dict.fromkeys(USAGE_FIELDS, 0), **counts}
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id:
                    # Last row wins: see the streaming-start figure above.
                    latest[message_id] = counts
                else:
                    # No id to group by, so it is its own message — and it keeps its
                    # place in the sequence, because "after the first" is a question
                    # about position (KYO-298.c6).
                    anonymous += 1
                    latest[f"\0anonymous-{anonymous}"] = counts
    except OSError:
        return TranscriptUsage()
    totals = dict.fromkeys(USAGE_FIELDS, 0)
    for counts in latest.values():
        for field_name, value in counts.items():
            totals[field_name] += value
    # KYO-298.c6. `[1:]`: the first message of a transcript writes the whole context
    # by definition — that is what starting a conversation IS — so counting it would
    # report one re-write for every worker that ever ran.
    rewrites = [
        counts for counts in list(latest.values())[1:] if _is_context_rewrite(counts)
    ]
    return TranscriptUsage(
        counts=totals,
        models=models,
        messages=len(latest),
        latest_turn=latest_turn,
        context_rewrites=len(rewrites),
        context_rewrite_tokens=sum(
            counts.get("cache_creation_input_tokens", 0) for counts in rewrites
        ),
    )


def _rows_within(
    audits: list[StopAudit], window: tuple[str | None, str | None] | None
) -> list[StopAudit]:
    """The recorded stops that fall inside ``window``, or all of them for no window.

    Both ends are optional and an absent one means "unbounded on that side", which is
    the honest reading of a dispatch that is still running (no end) or of a row a
    rebuild left without a timestamp. ISO-8601 UTC strings compare correctly as
    strings and that is deliberately all this does: every timestamp in this store is
    written by the same formatter, and parsing them to compare them would introduce a
    failure mode ("that timestamp did not parse") in a function whose whole job is to
    keep one sentence honest.
    """
    if window is None:
        return list(audits)
    start, end = window
    return [
        audit
        for audit in audits
        if (start is None or audit.at >= start) and (end is None or audit.at <= end)
    ]


def _window_sentence(window: tuple[str | None, str | None]) -> str:
    """``created_at`` .. ``updated_at``, said in words a reader can check."""
    start, end = window
    return f"{start or 'its creation'} .. {end or 'now'}"


def _tick_counters_active_within(
    counters: dict[str, dict], window: tuple[str | None, str | None] | None
) -> bool:
    """KYO-375: whether ANY dispatch's counted-tick activity falls inside ``window``.

    :func:`_rows_within` answers the same question of ``ledger.audits`` — "did the
    ledger write anything during this stretch, proving the store was writable" —
    and a tick since this shipped never reaches ``audits`` at all, so that check
    alone would call a window full of nothing but counted ticks the same as one the
    store never wrote to. Global, like :func:`_rows_within` applied to the whole
    ledger: any dispatch's counter overlapping the window is evidence the writer
    ran, whichever dispatch it was counted against.
    """
    if window is None or not counters:
        return False
    start, end = window
    for counter in counters.values():
        first_seen = counter.get("first_seen")
        last_seen = counter.get("last_seen")
        if not isinstance(first_seen, str) or not isinstance(last_seen, str):
            continue
        if (end is None or first_seen <= end) and (start is None or last_seen >= start):
            return True
    return False


def worker_tokens_from(
    ledger: StopAuditLedger,
    dispatch_id: str,
    *,
    ran_between: tuple[str | None, str | None] | None = None,
) -> WorkerTokens:
    """What ``dispatch_id``'s worker cost, per an already-read stop-audit ledger.

    Separate from :func:`worker_tokens` because `kyo worktrees` asks this of every
    row and the ledger is one query for all of them — the same reason that command
    reads the ledger once before its loop rather than once per row.

    ``ran_between`` (KYO-325) is the dispatch's own ``(created_at, updated_at)``, when
    its worker could possibly have stopped. It changes one sentence: without it, a
    dispatch with no matched row reads "N stop(s) named this dispatch as a candidate and
    none of them could be matched to it", which claims the worker's stop was seen. Ticks
    recorded after the worker handed back name a dispatch whose worktree still exists;
    with the window, "stops seen, none matched" and "no stop recorded while it ran"
    separate (docs/design/dispatch.md#kyo-325).
    """
    if ledger.unreadable is not None:
        return WorkerTokens(
            dispatch_id,
            TOKENS_LEDGER_UNREADABLE,
            "this store's event ledger could not be read "
            f"({ledger.unreadable}), so nothing here is a statement about this "
            "dispatch's token use either way",
        )
    mine = [audit for audit in ledger.audits if audit.dispatch_id == dispatch_id]
    matched = [audit for audit in mine if audit.outcome == STOP_TARGET_MATCHED]
    # Deduplicated by path, in first-seen order: the ~32s tick (KYO-163) writes one
    # row per period per running agent, all of them naming the same file.
    paths: list[Path] = []
    seen: set[str] = set()
    for audit in matched:
        if audit.transcript and audit.transcript not in seen:
            seen.add(audit.transcript)
            paths.append(Path(audit.transcript))
    if not paths:
        if matched:
            why = (
                f"{len(matched)} stop(s) were matched to this dispatch, but none of them "
                "recorded which transcript it read — the resolution only began storing "
                "that path in KYO-284, so a dispatch that finished before this build "
                "cannot be summed and is not guessed at"
            )
        elif ledger.naming(dispatch_id) or ledger.ticks_naming(dispatch_id):
            # KYO-375.c2: split apart. `naming` is WORKER-shaped unmatched stops
            # only; `ticks` is a count (legacy rows plus the counter) of ticks that
            # named this dispatch, which are not candidate worker stops and must
            # stop being described as ones.
            naming = ledger.naming(dispatch_id)
            ticks = ledger.ticks_naming(dispatch_id)
            # KYO-325. `ran_between` is the dispatch's own window; rows outside it
            # cannot be its worker's stop, whatever they name as a candidate. A
            # counted tick carries no per-row timestamp any more, so whether counted
            # activity touched the window is asked of the counters directly.
            during = _rows_within(ledger.audits, ran_between)
            ticks_during = _tick_counters_active_within(ledger.tick_counters, ran_between)
            if ran_between is not None and not during and not ticks_during:
                why = (
                    f"NO stop of any kind was recorded while this dispatch was running "
                    f"({_window_sentence(ran_between)}), so this is not a stop that "
                    "failed to match — it is the absence of one, and the transcript "
                    "resolution never ran for this worker. The "
                    f"{len(naming) + ticks} row(s)/tick(s) that name this dispatch as a "
                    "candidate all arrive outside that window, because its worktree "
                    "directory still exists; the ~32s SubagentStop tick (KYO-163) names "
                    "every such dispatch on every tick. The measured way a ledger goes "
                    "silent like that is a hook whose `kyo` could not open the store: "
                    f"the stop then resolves to `{STOP_TARGET_NO_WORKTREES}` and no row "
                    f"is written at all (`{STOP_TARGET_STORE_UNREADABLE}` is what it "
                    "resolves to since KYO-325, and a build old enough to have that "
                    "defect is one to upgrade)"
                )
                return WorkerTokens(dispatch_id, TOKENS_NO_STOP_WHILE_RUNNING, why)
            if ran_between is not None:
                inside = {audit.event_id for audit in during}
                counted = [audit for audit in naming if audit.event_id in inside]
                worker_clause = (
                    f"{len(counted)} stop(s) named this dispatch as a candidate while it "
                    "was running and none of them could be matched to it, so no "
                    "transcript is known to be this worker's"
                    if naming
                    else None
                )
            else:
                worker_clause = (
                    f"{len(naming)} stop(s) named this dispatch as a candidate and none "
                    "of them could be matched to it, so no transcript is known to be "
                    "this worker's"
                    if naming
                    else None
                )
            # KYO-375.c2: the sentence `kyo show <dispatch>` prints for the
            # not-a-worker population, said separately from the worker one above —
            # never folded into "N stop(s) named this dispatch as a candidate",
            # which reads as a worker's stop that failed to match, and 134 of them
            # were once ticks recorded hours after a worker had already handed back
            # (KYO-325's own story).
            tick_clause = (
                f"{ticks} tick(s) by an agent with no transcript named it; none was a "
                "worker's (KYO-375)"
                if ticks
                else None
            )
            why = "; ".join(clause for clause in (worker_clause, tick_clause) if clause)
            return WorkerTokens(dispatch_id, STOP_TARGET_UNRESOLVED, why)
        elif (
            ran_between is not None
            and ledger.audits
            and not _rows_within(ledger.audits, ran_between)
            and not _tick_counters_active_within(ledger.tick_counters, ran_between)
        ):
            why = (
                "no stop of any kind was recorded while this dispatch was running "
                f"({_window_sentence(ran_between)}), and no later row names it either. "
                "The transcript resolution never ran for this worker, so there is "
                "nothing to sum and nothing was matched wrongly — see "
                f"`{STOP_TARGET_STORE_UNREADABLE}` for the measured way a hook goes "
                "silent without saying so (KYO-325)"
            )
            return WorkerTokens(dispatch_id, TOKENS_NO_STOP_WHILE_RUNNING, why)
        else:
            why = (
                "no stop has been recorded against this dispatch, so the transcript "
                "resolution never ran for it and there is nothing to sum. A row is "
                "written only when a SubagentStop happens while a dispatch has a "
                "declared worktree (KYO-134)"
            )
        return WorkerTokens(dispatch_id, TOKENS_NO_TRANSCRIPT_RECORDED, why)
    totals = dict.fromkeys(USAGE_FIELDS, 0)
    models: set[str] = set()
    messages = 0
    read: list[Path] = []
    # KYO-292.c2: the latest turn belongs to ONE transcript, never to a sum of them.
    # `paths` is in the order the matched stops were recorded (oldest first), so the
    # last readable one is the most recent, and its last message is this worker's
    # latest turn. A resumed worker holding two transcripts (KYO-284) is exactly the
    # case this has to get right: adding the two turns together would report a
    # context size the worker never had.
    latest_turn = dict.fromkeys(USAGE_FIELDS, 0)
    # KYO-298.c6: SUMMED across transcripts, unlike the latest turn directly above.
    # Each transcript is its own conversation with its own legitimate first write, so
    # a resumed worker that re-wrote its context in each of two transcripts really did
    # pay for it twice.
    rewrites = 0
    rewrite_tokens = 0
    for path in paths:
        usage = sum_transcript_usage(path)
        if usage.messages == 0:
            continue
        read.append(path)
        messages += usage.messages
        models |= usage.models
        latest_turn = usage.latest_turn
        rewrites += usage.context_rewrites
        rewrite_tokens += usage.context_rewrite_tokens
        for field_name in USAGE_FIELDS:
            totals[field_name] += usage.counts[field_name]
    if not read:
        return WorkerTokens(
            dispatch_id,
            TOKENS_NO_TRANSCRIPT_RECORDED,
            f"{len(paths)} transcript(s) were matched to this dispatch and none of them "
            "could be read, or none carried a usage-bearing assistant message: "
            + ", ".join(str(path) for path in paths),
        )
    return WorkerTokens(
        dispatch_id,
        STOP_TARGET_MATCHED,
        f"summed from {len(read)} transcript(s) the stop-audit resolution matched to "
        f"{dispatch_id} over {len(matched)} recorded stop(s)",
        model=", ".join(sorted(models)) if models else None,
        transcripts=len(read),
        messages=messages,
        latest_turn_tokens=sum(latest_turn.values()),
        context_rewrites=rewrites,
        context_rewrite_tokens=rewrite_tokens,
        **totals,
    )


def dispatch_run_window(store, dispatch_id: str) -> tuple[str | None, str | None] | None:
    """When ``dispatch_id``'s worker could possibly have stopped, per the store.

    ``(created_at, updated_at)``, and ``updated_at`` is left OPEN for a dispatch that
    is still running: a running dispatch's worker has not stopped yet, so "now" is
    the only honest upper bound and a stored timestamp would close the window before
    the event it is meant to contain. For a dispatch that has left `running`,
    ``updated_at`` is when it did — ingesting the report is the last thing that moves
    it — and a worker's stop arrives before that or not at all.

    ``None`` whenever the store cannot answer, which is the signal
    :func:`worker_tokens_from` reads as "do not narrow anything": a window that might
    be wrong is worse than no window, because the sentence it buys asserts that
    nothing happened in it.
    """
    try:
        row = store.dispatch(dispatch_id)
    except Exception:  # noqa: BLE001 - a read surface must degrade, not die
        return None
    if row is None:
        return None
    try:
        created = row["created_at"]
        state = row["state"]
        updated = row["updated_at"]
    except (IndexError, KeyError):
        return None
    if not created:
        return None
    return (created, None if state == "running" else (updated or None))


def worker_tokens(store, dispatch_id: str) -> WorkerTokens:
    """What ``dispatch_id``'s worker cost. Never raises; see :class:`WorkerTokens`."""
    return worker_tokens_from(
        stop_audits(store), dispatch_id, ran_between=dispatch_run_window(store, dispatch_id)
    )


#: This dispatch was not summed on this run, because the run had a budget and this
#: row was outside it. Not "unknown": the number is obtainable, by
#: `kyo show <dispatch-id>`, which sums one dispatch on demand and is never capped.
TOKENS_NOT_SUMMED = "not-summed-this-run"

#: How many of a listing's dispatches get their transcripts summed. `kyo
#: worktrees --json` is on the desktop app's 5-SECOND activity poll (KYO-264), and
#: this store already holds 144 dispatches that declared a worktree; summing every
#: one of them per tick would put seconds of transcript parsing on a loop that has
#: to finish in well under five. 24 because the app shows 12 finished rows
#: (`ACTIVITY_FINISHED_DEFAULT`) beside its running ones, so twice that covers
#: everything any view asks for with room to spare, and the rows past it are ones
#: nothing is rendering. Measured per transcript on this project's own files: 3.4
#: MB in 0.014s, so the budget is ~0.3s of parsing in the worst case where every
#: row in it has a readable transcript.
WORKER_TOKEN_ROW_LIMIT = 24


def worker_tokens_index(
    ledger: StopAuditLedger,
    dispatch_ids: list[str],
    *,
    created_at: Mapping[str, str | None],
    ran_between: Mapping[str, tuple[str | None, str | None] | None] | None = None,
    always: Iterable[str] = (),
    limit: int = WORKER_TOKEN_ROW_LIMIT,
) -> dict[str, WorkerTokens]:
    """Sum the transcripts of a listing's dispatches, under a budget.

    The budget is spent on the ``limit`` most recently CREATED dispatches, read off
    ``created_at`` — never on the tail of ``dispatch_ids`` (KYO-292). This listing's
    order is ``dispatches.seq``, the order rows were inserted, and a rebuild inserts from
    the export directory (`KYO-100.d1` before `KYO-99.d1`, every `.d2` after every
    `.d1`), so the tail is not the newest (docs/design/dispatch.md#kyo-292).

    ``always`` names the ones that are summed whatever the budget says; `kyo
    worktrees` passes its live dispatches, because a running worker is the one row
    the whole feature is for and it must never be the row that falls off the end.

    An id with no entry in ``created_at`` — or one whose stored timestamp is
    ``None`` — sorts as the oldest there is rather than raising: a missing timestamp
    is a reason to lose the budget, not a reason for the whole listing to fail. Ties
    are broken by position in ``dispatch_ids`` so the selection is deterministic.

    Every id in ``dispatch_ids`` gets an entry. A row outside the budget carries
    :data:`TOKENS_NOT_SUMMED` and says where to get the number, rather than being
    absent and leaving a consumer to guess whether absence means zero.
    """
    required = set(always)
    position = {dispatch_id: index for index, dispatch_id in enumerate(dispatch_ids)}
    by_creation = sorted(
        dict.fromkeys(dispatch_ids),
        key=lambda dispatch_id: (created_at.get(dispatch_id) or "", position[dispatch_id]),
    )
    budgeted = set(by_creation[-limit:]) if limit > 0 else set()
    summed = required | budgeted
    out: dict[str, WorkerTokens] = {}
    for dispatch_id in dispatch_ids:
        if dispatch_id in summed:
            out[dispatch_id] = worker_tokens_from(
                ledger,
                dispatch_id,
                # KYO-325: optional here too, and absent means "say the unnarrowed
                # sentence". A caller that does not know when a dispatch ran must not
                # have a window invented for it.
                ran_between=(ran_between or {}).get(dispatch_id),
            )
        else:
            out[dispatch_id] = WorkerTokens(
                dispatch_id,
                TOKENS_NOT_SUMMED,
                f"not summed on this run: this listing sums the {limit} most recently "
                "created dispatches and every running one, because it is read on a "
                f"5-second poll. `kyo show {dispatch_id}` sums this one on demand",
            )
    return out


# ------------------------- KYO-286: when a dispatch finished, and how we know that
#
# `dispatches.closed_at` is set by `kyo dispatch close` and nothing else, and a dispatch
# that simply reported is usually never closed, so "now minus created" is not a run time
# (docs/design/dispatch.md#kyo-286).
#
# Three sources, in this order, and the order is the whole content of the fix: a
# close or an ingest is a CONTROLLER's act and can come hours after the worker
# stopped, while a matched stop is the worker stopping. So neither of the two
# outranks the stop, and the source is named on the row rather than left for a
# reader to infer from which field happens to be set.

#: The end time came from the latest stop the stop-audit resolution matched to this
#: dispatch (KYO-128/134) — the closest thing recorded to the worker itself stopping.
DISPATCH_END_WORKER_STOP = "worker-stop"
#: The end time is when the report the dispatch's ``report_id`` names was ingested.
DISPATCH_END_REPORT_INGEST = "report-ingest"
#: The end time is ``dispatches.closed_at`` — `kyo dispatch close` and nothing else.
DISPATCH_END_CLOSED_AT = "closed-at"


@dataclass
class DispatchEnd:
    """When a dispatch finished, and which of the three sources says so.

    :attr:`at` is ``None`` on a dispatch that has not finished and on one whose end
    nothing recorded, and :attr:`source` is ``None`` with it — never a timestamp
    standing in for "we do not know", which is exactly the substitution
    (``now()`` for an end time) that KYO-286 is about. :attr:`why` is a sentence on
    every branch, so a consumer rendering a tip never has to write its own account
    of a fact it did not derive.
    """

    at: str | None
    source: str | None
    why: str

    def as_json(self) -> dict:
        return {
            "finished_at": self.at,
            "finished_at_source": self.source,
            "finished_at_why": self.why,
        }


def report_ingest_times(store) -> dict[str, str]:
    """When each report in this store was ingested, by report id. Never raises.

    One query for the whole listing, on the same terms as :func:`stop_audits`: `kyo
    worktrees` asks this of every row and a per-row lookup would pay for the same
    table over and over. An unreadable table is an empty answer, and a dispatch that
    needed it falls through to its ``closed_at`` with the source saying so.
    """
    try:
        rows = store.conn.execute("SELECT id, created_at FROM reports").fetchall()
    except Exception:  # noqa: BLE001 - a read surface must degrade, not die
        return {}
    return {row["id"]: row["created_at"] for row in rows if row["created_at"]}


def dispatch_end(
    ledger: StopAuditLedger,
    *,
    dispatch_id: str,
    state: str,
    report_id: str | None,
    report_ingested_at: str | None,
    closed_at: str | None,
) -> DispatchEnd:
    """When ``dispatch_id`` finished: the matched stop, else the ingest, else the close.

    A RUNNING dispatch has no end time and gets ``None`` here, deliberately and
    first: the ~32s SubagentStop tick (KYO-163) records a matched stop every period
    while a worker runs, so "the latest matched stop" for a running dispatch is a
    timestamp from moments ago and a consumer that froze its clock on it would stop
    the clock on the one row the whole activity view exists for.
    """
    if state == DISPATCH_STATE_RUNNING:
        return DispatchEnd(
            None,
            None,
            "this dispatch is still running, so it has no end time — its clock is "
            "still going, and the stop-audit tick (KYO-163) records a matched stop "
            "every ~32s while a worker runs, which is not the worker stopping",
        )
    stop = ledger.latest_matched(dispatch_id)
    if stop is not None and stop.at:
        return DispatchEnd(
            stop.at,
            DISPATCH_END_WORKER_STOP,
            f"the latest stop the stop-audit resolution matched to this dispatch "
            f"({stop.event_id}), which is the closest thing recorded to its worker "
            "stopping",
        )
    if report_id and report_ingested_at:
        return DispatchEnd(
            report_ingested_at,
            DISPATCH_END_REPORT_INGEST,
            f"when {report_id} was ingested — no worker stop was matched to this "
            "dispatch, and an ingest can come long after the worker stopped",
        )
    if closed_at:
        return DispatchEnd(
            closed_at,
            DISPATCH_END_CLOSED_AT,
            "when `kyo dispatch close` closed it — no worker stop was matched to "
            "this dispatch and it carries no report, and a close can come long "
            "after the worker stopped",
        )
    return DispatchEnd(
        None,
        None,
        "nothing in this store records when this dispatch finished: no stop was "
        "matched to it, it has no ingested report, and it was never closed",
    )
