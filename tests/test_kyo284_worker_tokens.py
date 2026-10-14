"""KYO-284: a dispatch's worker token use, summed from the transcript the
stop-audit resolution already matched to it.

The requirement is that the Activity list shows a worker's model and token use
("opus 160k tokens") beside a running dispatch, and the same beside a finished one.
Kyosei does not spawn workers and
has no billing surface, so the only record of what a worker cost is the harness's
own transcript — and the only thing in this codebase that knows which transcript
belongs to which dispatch is KYO-128/154's stop resolution, which matches a
stopping agent's spawn prompt to the dispatch id Kyosei itself minted.

WHAT THIS FILE PINS, and the first two matter most:

* the number is summed from THAT match and from nothing else. Nothing here searches
  the harness's directories for a transcript that looks like it might be a
  dispatch's; a second search beside KYO-128's would be a second answer to the
  question of whose file is whose, built with none of the first one's care
  (:func:`test_the_sum_reads_only_the_transcript_the_resolution_matched`);
* a dispatch with no matched transcript reads as a NAMED OUTCOME, never as zero
  tokens (:func:`test_a_dispatch_with_no_recorded_stop_says_so_rather_than_zero`,
  :func:`test_an_unresolved_stop_names_itself_as_the_reason`). A zero is a number,
  and a number is a claim;
* the ~32-second SubagentStop tick (KYO-163) writes dozens of matched rows naming
  one transcript, and the sum deduplicates by path, so a LONG dispatch does not
  read as an EXPENSIVE one
  (:func:`test_the_stop_tick_does_not_multiply_the_total`).
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.stop_audit import StopAudit, StopAuditLedger
from kyosei.stop_target import STOP_TARGET_MATCHED, STOP_TARGET_UNRESOLVED
from kyosei.worker_tokens import (
    CONTEXT_REWRITE_MIN_TOKENS,
    CONTEXT_REWRITE_SHARE,
    TOKENS_LEDGER_UNREADABLE,
    TOKENS_NO_TRANSCRIPT_RECORDED,
    TOKENS_NOT_SUMMED,
    _is_context_rewrite,
    sum_transcript_usage,
    worker_tokens_from,
    worker_tokens_index,
)

from .test_subagent_stop_worktree import _payload, _running_dispatch

# One assistant message's worth of `usage`, in the shape observed on this project's
# own transcripts (`~/.claude/projects/<slug>/<session>/subagents/
# agent-<id>.jsonl`): four token counts, plus keys that are not token counts.
USAGE = {
    "input_tokens": 7,
    "cache_creation_input_tokens": 1_100,
    "cache_read_input_tokens": 90_000,
    "output_tokens": 640,
    # Not a token count, and deliberately present: a total that added up every
    # integer a `usage` happens to carry would drift the next time the harness adds
    # one of these.
    "service_tier": "standard",
}


def _worker_transcript(
    tmp_path: Path,
    agent_id: str,
    prompt: str,
    *,
    messages: int = 3,
    model: str = "claude-opus-5",
) -> Path:
    """A subagent transcript carrying a spawn prompt and ``messages`` billed turns.

    Shaped as :func:`tests.test_subagent_stop_worktree._transcript` shapes one --
    `<project>/<session>.jsonl` beside `<project>/<session>/subagents/agent-<id>.jsonl`,
    first `user` record is the spawn prompt -- with the assistant records the real
    thing carries appended, each with its own `usage`. Returns the SESSION
    transcript, which is what a hook payload's `transcript_path` names.
    """
    session_dir = tmp_path / "transcripts" / "session"
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    parent = session_dir.with_suffix(".jsonl")
    parent.write_text("", encoding="utf-8")
    lines = [
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "agentId": agent_id,
                "cwd": "/the/controllers/cwd",
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }
        )
    ]
    for index in range(messages):
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "agentId": agent_id,
                    "message": {
                        "role": "assistant",
                        "model": model,
                        "content": [{"type": "text", "text": f"turn {index}"}],
                        "usage": dict(USAGE),
                    },
                }
            )
        )
        # A tool result comes back as a `user` record and is billed to nobody. It is
        # here because the sum has to skip it: counting it would double every turn.
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "agentId": agent_id,
                    "message": {"role": "user", "content": "tool result"},
                }
            )
        )
    (session_dir / "subagents" / f"agent-{agent_id}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return parent


def _agent_file(parent: Path, agent_id: str) -> Path:
    return parent.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"


def _matched(dispatch_id: str, transcript: Path, *, event: str = "KYO-E1") -> StopAudit:
    """One recorded stop that the resolution matched to ``dispatch_id``."""
    return StopAudit(
        event_id=event,
        at="2026-09-16T12:00:00.000Z",
        outcome=STOP_TARGET_MATCHED,
        directory="/some/worktree",
        why="the prompt names that dispatch id",
        dispatch_id=dispatch_id,
        candidates=[dispatch_id],
        agent_id="a1b2c3d4e5f6a7b8c",
        transcript=str(transcript),
    )


# ------------------------------------------------- the seam that sums a transcript


def test_the_four_usage_counts_are_summed_and_nothing_else_is(tmp_path: Path) -> None:
    """c4. Four named counts, the model the transcript names, and the turn count.

    `service_tier` sits in the same object and is not a token count. Summing every
    integer in a `usage` would pass today and start lying the first time the harness
    adds a field, which is why the four are named.
    """
    parent = _worker_transcript(tmp_path, "aaa", "prompt", messages=3)

    usage = sum_transcript_usage(_agent_file(parent, "aaa"))

    assert usage.messages == 3
    assert usage.models == {"claude-opus-5"}
    assert usage.counts == {
        "input_tokens": 21,
        "cache_creation_input_tokens": 3_300,
        "cache_read_input_tokens": 270_000,
        "output_tokens": 1_920,
    }


def test_a_transcript_that_is_not_there_is_an_empty_answer_not_an_exception(
    tmp_path: Path,
) -> None:
    """This runs on the desktop's 5-second activity poll and inside `kyo worktrees`.

    A transcript the harness has rotated away under us must cost the caller a
    "unknown", never an exception that takes the whole activity list down.
    """
    usage = sum_transcript_usage(tmp_path / "nothing-here.jsonl")

    assert usage.messages == 0
    assert usage.models == set()
    assert set(usage.counts.values()) == {0}
    assert usage.latest_turn_tokens == 0


# ------------------------------------------------------------------ KYO-292.c4
#
# The harness writes one transcript ROW per content block, and every row of one
# assistant message repeats that message's `id` and its whole `usage` object. So a
# sum over rows counts a multi-block message once per block. Measured on KYO-289.d1's
# live transcript: 179 usage-bearing rows against 106 distinct message
# ids, and the first rows' `output_tokens` summed to 1,501 against the last rows'
# 4,752 -- the first row of a message carries a streaming-start figure.


def _blocks(path: Path, *rows: tuple[str | None, dict]) -> None:
    """Write a transcript of raw assistant rows: ``(message id, usage)`` each.

    Deliberately not built on :func:`_worker_transcript`: what this pins is the row
    shape the harness actually writes, and a fixture that could only express one row
    per message could not fail on the defect.
    """
    lines = []
    for message_id, usage in rows:
        message = {
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": "a block"}],
            "usage": usage,
        }
        if message_id is not None:
            message["id"] = message_id
        lines.append(json.dumps({"type": "assistant", "message": message}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_multi_block_message_is_counted_once_and_by_its_last_row(tmp_path: Path) -> None:
    """c4: group by `message.id`, take the LAST row, count distinct messages.

    Three rows of one message and one row of another. Summing rows would report four
    messages and 3x the first message's counts, which is the defect; grouping without
    preferring the last row would report the streaming-start `output_tokens` (the 9
    below) rather than the message's real 640.
    """
    transcript = tmp_path / "blocks.jsonl"
    early = {
        "input_tokens": 7,
        "cache_creation_input_tokens": 1_100,
        "cache_read_input_tokens": 90_000,
        "output_tokens": 9,
    }
    settled = {**early, "output_tokens": 640}
    _blocks(
        transcript,
        ("msg_one", early),
        ("msg_one", {**early, "output_tokens": 300}),
        ("msg_one", settled),
        ("msg_two", {**settled, "output_tokens": 60}),
    )

    usage = sum_transcript_usage(transcript)

    assert usage.messages == 2, "four rows, two messages"
    assert usage.models == {"claude-opus-5"}
    assert usage.counts == {
        # Two messages' worth, never four -- this is the assertion that fails if rows
        # are summed again.
        "input_tokens": 14,
        "cache_creation_input_tokens": 2_200,
        "cache_read_input_tokens": 180_000,
        # And the LAST row of `msg_one`, not its streaming-start 9 and not its 300.
        "output_tokens": 700,
    }
    # KYO-292.c2: the latest turn is the LAST row in the file -- `msg_two`'s only row,
    # all four of its counts, cache reads included -- and not any of the sums above.
    assert usage.latest_turn == {
        "input_tokens": 7,
        "cache_creation_input_tokens": 1_100,
        "cache_read_input_tokens": 90_000,
        "output_tokens": 60,
    }
    assert usage.latest_turn_tokens == 91_167


def test_a_row_with_no_message_id_is_its_own_message(tmp_path: Path) -> None:
    """There is nothing to group an unidentified row with, so it stands alone.

    Dropping it would lose tokens that were really spent, and folding every one of
    them together under a single empty key would lose all but the last. Every
    transcript written before the harness carried ids is entirely this shape, which
    is why the existing fixtures above still read as one message per turn.
    """
    transcript = tmp_path / "anonymous.jsonl"
    usage = {
        "input_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 11,
    }
    _blocks(transcript, (None, usage), (None, usage), ("msg_one", usage), ("msg_one", usage))

    usage = sum_transcript_usage(transcript)

    assert usage.messages == 3, "two unidentified rows and one two-row message"
    assert usage.counts["input_tokens"] == 15
    assert usage.counts["output_tokens"] == 33


def test_the_headline_is_the_latest_turn_and_not_any_running_sum(tmp_path: Path) -> None:
    """c2: the card's figure is a context size, and a sum can never be one.

    Measured twice on KYO-289.d1's transcript. The decisive reading is
    the second: the harness reported 189,568 where the last message read 187,726 and
    every cumulative sum was ALREADY PAST IT -- 190,141 for input + cache creation +
    output counted once per message. A figure smaller than a running total cannot be
    that running total, whatever it is. The shape below is the same one: two messages
    whose sums exceed the last message's own counts.
    """
    transcript = tmp_path / "turns.jsonl"
    _blocks(
        transcript,
        (
            "msg_one",
            {
                "input_tokens": 4,
                "cache_creation_input_tokens": 2_000,
                "cache_read_input_tokens": 50_000,
                "output_tokens": 900,
            },
        ),
        (
            "msg_two",
            {
                "input_tokens": 6,
                "cache_creation_input_tokens": 1_000,
                "cache_read_input_tokens": 52_000,
                "output_tokens": 30,
            },
        ),
    )

    usage = sum_transcript_usage(transcript)

    # The latest turn is the last message alone, cache reads INCLUDED.
    assert usage.latest_turn_tokens == 6 + 1_000 + 52_000 + 30
    # And it is smaller than the cumulative sum without cache reads, which is the
    # relation that ruled a running sum out. A regression to any sum fails here.
    assert usage.latest_turn_tokens < sum(usage.counts.values())
    cumulative_without_cache_reads = (
        usage.counts["input_tokens"]
        + usage.counts["cache_creation_input_tokens"]
        + usage.counts["output_tokens"]
    )
    assert usage.latest_turn_tokens != cumulative_without_cache_reads


def test_the_latest_turn_travels_on_the_answer_and_is_not_added_across_transcripts(
    tmp_path: Path,
) -> None:
    """A resumed worker holds two transcripts, and it had one latest turn.

    The counts are summed across both, because the worker really spent all of them.
    The latest turn is the MOST RECENT transcript's last message and nothing else:
    adding the two together would report a context size the worker never had.
    `paths` is built from the matched stops oldest-first, so the last readable
    transcript is the most recent one.
    """
    first = _worker_transcript(tmp_path / "a", "one", "prompt", messages=2)
    second = _worker_transcript(tmp_path / "b", "two", "prompt", messages=1)
    ledger = StopAuditLedger(
        audits=[
            _matched("KYO-1.d1", _agent_file(first, "one"), event="KYO-E1"),
            _matched("KYO-1.d1", _agent_file(second, "two"), event="KYO-E2"),
        ]
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.transcripts == 2
    assert spend.messages == 3
    # Summed across both, as before.
    assert spend.output_tokens == 3 * USAGE["output_tokens"]
    # And one turn, from the second transcript -- one message's four counts, which is
    # the same number whichever of these transcripts' last messages it came from here,
    # so the assertion that carries the weight is that it is not two of them.
    one_turn = (
        USAGE["input_tokens"]
        + USAGE["cache_creation_input_tokens"]
        + USAGE["cache_read_input_tokens"]
        + USAGE["output_tokens"]
    )
    assert spend.latest_turn_tokens == one_turn
    assert spend.as_json()["latest_turn_tokens"] == one_turn
    # And `kyo show` prints it, because c3 is a reader checking the app's row
    # against that command -- a figure on only one of the two cannot be compared.
    assert f"latest turn {one_turn} tokens" in spend.sentence()


# -------------------------------------------- the matched transcript, and only it


def test_the_sum_reads_only_the_transcript_the_resolution_matched(tmp_path: Path) -> None:
    """c4, and the whole shape of this record.

    Two transcripts sit side by side in one session directory, as they do in every
    real shift. Exactly one of them is named by a stop row the resolution matched to
    this dispatch, and that is the only one summed -- the other worker's turns,
    twice as many and in the same directory, appear nowhere in the answer. A
    re-implementation that went looking for "the transcript for this dispatch"
    itself would have to get this right a second time, from scratch.
    """
    mine = _worker_transcript(tmp_path, "mine", "prompt", messages=2)
    _worker_transcript(tmp_path, "theirs", "prompt", messages=4)
    ledger = StopAuditLedger(audits=[_matched("KYO-1.d1", _agent_file(mine, "mine"))])

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.outcome == STOP_TARGET_MATCHED
    assert spend.known
    assert spend.messages == 2
    assert spend.transcripts == 1
    assert spend.model == "claude-opus-5"
    assert spend.output_tokens == 1_280
    assert spend.total_tokens == 2 * sum(
        value for key, value in USAGE.items() if key != "service_tier"
    )


def test_the_stop_tick_does_not_multiply_the_total(tmp_path: Path) -> None:
    """KYO-163's tick writes one row per ~32s per running agent, all one transcript.

    Deduplicating by path is what stops a long dispatch reading as an expensive one.
    Without it this dispatch's total would be forty times what it cost, and the
    number would grow while the worker sat idle.
    """
    parent = _worker_transcript(tmp_path, "mine", "prompt", messages=2)
    path = _agent_file(parent, "mine")
    ledger = StopAuditLedger(
        audits=[_matched("KYO-1.d1", path, event=f"KYO-E{n}") for n in range(40)]
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.transcripts == 1
    assert spend.messages == 2
    assert "over 40 recorded stop(s)" in spend.why


def test_two_transcripts_for_one_dispatch_are_both_summed(tmp_path: Path) -> None:
    """A worker resumed under a fresh agent id genuinely has two transcripts.

    Which is also why the dedupe above is by PATH and not "take the first row".
    """
    first = _worker_transcript(tmp_path, "first", "prompt", messages=2)
    second = _worker_transcript(tmp_path, "second", "prompt", messages=5, model="claude-haiku-5")
    ledger = StopAuditLedger(
        audits=[
            _matched("KYO-1.d1", _agent_file(first, "first"), event="KYO-E1"),
            _matched("KYO-1.d1", _agent_file(second, "second"), event="KYO-E2"),
        ]
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.transcripts == 2
    assert spend.messages == 7
    assert spend.model == "claude-haiku-5, claude-opus-5"


# ------------------------------------------------- law 6: unknown is not a number


def test_a_dispatch_with_no_recorded_stop_says_so_rather_than_zero() -> None:
    """c1/c2/c4. "Nobody measured this" is not "this cost nothing"."""
    spend = worker_tokens_from(StopAuditLedger(), "KYO-1.d1")

    assert spend.outcome == TOKENS_NO_TRANSCRIPT_RECORDED
    assert spend.known is False
    assert spend.total_tokens == 0
    assert "no stop has been recorded against this dispatch" in spend.why
    assert spend.sentence().startswith("worker tokens: unknown")


def test_an_unresolved_stop_names_itself_as_the_reason() -> None:
    """c1/c4: the reason is the RESOLUTION's own named outcome, not a new word.

    A stop that named this dispatch as a candidate and could not be matched to it is
    exactly `unresolved`, and saying so hands a reader the vocabulary the rest of
    this machinery already uses -- and the `kyo worktrees` section that explains it.

    KYO-375: the fixture's ``why`` is WORKER-shaped (a prompt was read and named
    nothing) rather than the tick's ("no readable transcript"), because since
    KYO-375.c2 a tick-shaped row reads as not-a-worker and gets its own separate
    sentence -- see ``test_a_tick_names_itself_apart_from_a_worker_stop`` below for
    that half.
    """
    ledger = StopAuditLedger(
        audits=[
            StopAudit(
                event_id="KYO-E1",
                at="2026-09-16T12:00:00.000Z",
                outcome=STOP_TARGET_UNRESOLVED,
                directory="/the/controllers/cwd",
                why=(
                    "the stopping agent's prompt names none of the 1 dispatch(es) "
                    "with a declared worktree (first user message of "
                    "/the/session/subagents/agent-a.jsonl)"
                ),
                dispatch_id=None,
                candidates=["KYO-1.d1"],
            )
        ]
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.outcome == STOP_TARGET_UNRESOLVED
    assert spend.known is False
    assert "1 stop(s) named this dispatch as a candidate" in spend.why


def test_a_tick_names_itself_apart_from_a_worker_stop() -> None:
    """KYO-375.c2: a tick's sentence, said separately from a worker stop's.

    ``kyo show KYO-263.d1`` used to read "134 stop(s) named this dispatch as a
    candidate and none of them could be matched to it" for a dispatch every one of
    whose 134 rows was a tick -- indistinguishable from a worker's stop that failed
    to match. Since KYO-375 a tick writes no row at all; what a dispatch's own
    counter carries is a count, and the sentence names it as ticks, not as stops.
    """
    ledger = StopAuditLedger(
        tick_counters={
            "KYO-1.d1": {
                "count": 3,
                "first_seen": "2026-09-16T12:00:00.000Z",
                "last_seen": "2026-09-16T12:01:04.000Z",
            }
        }
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.outcome == STOP_TARGET_UNRESOLVED
    assert spend.known is False
    assert "3 tick(s) by an agent with no transcript named it; none was a worker's" in spend.why
    assert "stop(s) named this dispatch as a candidate" not in spend.why


def test_a_tick_and_a_worker_stop_are_both_named_when_both_exist() -> None:
    """KYO-375.c2: neither swallows the other when a dispatch has both.

    A dispatch can genuinely have an unmatched worker stop AND ticks naming it
    (a worker's stop went unaudited, and the tick kept arriving after). Both
    sentences are said, not just one.
    """
    ledger = StopAuditLedger(
        audits=[
            StopAudit(
                event_id="KYO-E1",
                at="2026-09-16T12:00:00.000Z",
                outcome=STOP_TARGET_UNRESOLVED,
                directory="/the/controllers/cwd",
                why=(
                    "the stopping agent's prompt names none of the 1 dispatch(es) "
                    "with a declared worktree (first user message of "
                    "/the/session/subagents/agent-a.jsonl)"
                ),
                dispatch_id=None,
                candidates=["KYO-1.d1"],
            )
        ],
        tick_counters={
            "KYO-1.d1": {
                "count": 5,
                "first_seen": "2026-09-16T12:00:00.000Z",
                "last_seen": "2026-09-16T12:02:40.000Z",
            }
        },
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert "1 stop(s) named this dispatch as a candidate" in spend.why
    assert "5 tick(s) by an agent with no transcript named it; none was a worker's" in spend.why


def test_a_matched_row_from_before_this_shipped_is_unknown_not_guessed_at() -> None:
    """Every row written before KYO-284 carries no transcript path.

    The alternative was to go looking for the file from the session id -- a second
    guess at the harness's private layout, which c4 forbids by name. An honest
    "cannot be summed" for the past is the price of not having two answers to whose
    transcript is whose.
    """
    old = _matched("KYO-1.d1", Path("/unused"))
    old.transcript = None
    ledger = StopAuditLedger(audits=[old])

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.outcome == TOKENS_NO_TRANSCRIPT_RECORDED
    assert "only began storing that path in KYO-284" in spend.why


def test_a_ledger_that_cannot_be_read_is_its_own_answer() -> None:
    """Law 6's third answer, kept apart from "read it and there was nothing"."""
    spend = worker_tokens_from(StopAuditLedger(unreadable="database is locked"), "KYO-1.d1")

    assert spend.outcome == TOKENS_LEDGER_UNREADABLE
    assert spend.known is False
    assert "database is locked" in spend.why


# -------------------------------------------------- the budget a 5s poll runs under


def _at(day: int) -> str:
    """One ISO timestamp per fixture day, in the store's own format."""
    return f"2026-09-{day:02d}T12:00:00.000Z"


def test_the_listing_budget_always_sums_the_running_rows(tmp_path: Path) -> None:
    """`kyo worktrees --json` is on the desktop's 5-second poll (KYO-264).

    So it sums the most recently CREATED dispatches and every running one, and says
    plainly of the rest that they were not summed on this run -- which is a different
    fact from "unknown", and points at the command that will sum one on demand. The
    running row is the one this whole feature is for and is never the row that falls
    off.
    """
    parent = _worker_transcript(tmp_path, "mine", "prompt", messages=1)
    oldest = "KYO-1.d0"
    ids = [oldest] + [f"KYO-1.d{n}" for n in range(1, 10)]
    created = {dispatch_id: _at(index + 1) for index, dispatch_id in enumerate(ids)}
    ledger = StopAuditLedger(
        audits=[_matched(oldest, _agent_file(parent, "mine"))],
    )

    capped = worker_tokens_index(ledger, ids, created_at=created, limit=3)
    assert capped[oldest].outcome == TOKENS_NOT_SUMMED
    assert capped[oldest].known is False
    assert f"kyo show {oldest}" in capped[oldest].why
    assert capped["KYO-1.d9"].outcome == TOKENS_NO_TRANSCRIPT_RECORDED

    kept = worker_tokens_index(ledger, ids, created_at=created, always=[oldest], limit=3)
    assert kept[oldest].known
    assert kept[oldest].messages == 1


# ------------------------------------------------------------------------ KYO-292
#
# The budget above was spent on `dispatch_ids[-limit:]`, documented as "the store's
# own oldest-first order". `kyo worktrees` lists in `dispatches.seq` -- INSERTION
# order, which a rebuild fills from the export directory, so the ids come out in a
# lexical shuffle. Measured on this project's own store: the 24 summed
# rows were KYO-69.d1..KYO-99.d1 plus every `.d2`/`.d3`, and the four most recent
# dispatches all read `not-summed-this-run`, which the desktop renders as "tokens
# unknown" -- on exactly the rows the feature exists for.


def test_the_budget_follows_creation_order_not_the_listings_order(tmp_path: Path) -> None:
    """c1: the newest N are summed even when they sit in the middle of the listing.

    The listing below is this store's own shape in miniature and every one of its
    pathologies is deliberate: `KYO-100.d1` sorts before `KYO-99.d1` (ids past 99
    beside ids under 100), and a `.d2` sorts after every `.d1` whatever its record's
    number is. The two NEWEST dispatches -- `KYO-100.d1` at the head and `KYO-99.d1`
    in the middle -- are nowhere near the tail, and the tail holds the two oldest.

    This test fails if the selection goes back to the tail of the listing: the
    assertions on `KYO-12.d2`/`KYO-3.d2` below are exactly the rows a `[-limit:]`
    would have summed.
    """
    parent = _worker_transcript(tmp_path, "mine", "prompt", messages=1)
    listed = ["KYO-100.d1", "KYO-3.d1", "KYO-99.d1", "KYO-12.d2", "KYO-3.d2"]
    created = {
        "KYO-100.d1": _at(17),  # newest of all, and FIRST in the listing
        "KYO-99.d1": _at(16),  # second newest, and in the MIDDLE of the listing
        "KYO-3.d1": _at(3),
        "KYO-12.d2": _at(2),  # oldest two, and LAST in the listing
        "KYO-3.d2": _at(1),
    }
    ledger = StopAuditLedger(
        audits=[
            _matched("KYO-100.d1", _agent_file(parent, "mine"), event="KYO-E1"),
            _matched("KYO-3.d2", _agent_file(parent, "mine"), event="KYO-E2"),
        ]
    )

    index = worker_tokens_index(ledger, listed, created_at=created, limit=2)

    # The two newest are the two summed, wherever they sit in the listing.
    assert index["KYO-100.d1"].known is True
    assert index["KYO-100.d1"].messages == 1
    assert index["KYO-99.d1"].outcome == TOKENS_NO_TRANSCRIPT_RECORDED
    # And the tail is NOT summed -- `KYO-3.d2` has a matched transcript sitting right
    # there, so a budget spent on the tail would have reported a real number for it.
    assert index["KYO-3.d2"].outcome == TOKENS_NOT_SUMMED
    assert index["KYO-3.d2"].known is False
    assert index["KYO-12.d2"].outcome == TOKENS_NOT_SUMMED
    assert index["KYO-3.d1"].outcome == TOKENS_NOT_SUMMED
    # Every id in the listing still gets an entry, whichever side of the budget it is.
    assert set(index) == set(listed)


def test_a_dispatch_with_no_creation_time_loses_the_budget_rather_than_the_listing(
    tmp_path: Path,
) -> None:
    """A missing timestamp sorts oldest; it never raises and never wins the budget.

    `created_at` comes off the store, so an absent value means a row this listing
    could not date -- a reason to lose a sum that `kyo show` will do on demand, not a
    reason for the whole activity list to go dark.
    """
    parent = _worker_transcript(tmp_path, "mine", "prompt", messages=1)
    listed = ["KYO-1.d1", "KYO-2.d1"]
    ledger = StopAuditLedger(audits=[_matched("KYO-1.d1", _agent_file(parent, "mine"))])

    index = worker_tokens_index(
        ledger, listed, created_at={"KYO-2.d1": _at(4), "KYO-1.d1": None}, limit=1
    )

    assert index["KYO-1.d1"].outcome == TOKENS_NOT_SUMMED
    assert index["KYO-2.d1"].outcome == TOKENS_NO_TRANSCRIPT_RECORDED
    # And it is still summed when it is running, which no budget may drop.
    kept = worker_tokens_index(
        ledger, listed, created_at={"KYO-2.d1": _at(4)}, always=["KYO-1.d1"], limit=1
    )
    assert kept["KYO-1.d1"].known is True


# --------------------------------------------- end to end, through the real hook


def test_a_real_stop_makes_the_number_readable_from_kyo_show_and_worktrees(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c4, through the whole path and with nothing hand-written into the store.

    The hook runs as Claude Code runs it, the resolution matches the transcript the
    way it matches one for a `check-clean`, the row lands in the git-canonical
    events ledger, and both surfaces the record names -- `kyo show <dispatch-id>`
    and the JSON the desktop's activity list reads -- answer with the same four
    counts and the model the transcript names, not the one the controller declared.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-tokens")
    agent = "a1b2c3d4e5f6a7b8c"
    prompt = f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    parent = _worker_transcript(tmp_path, agent, prompt, messages=4)

    stop = kyo("check-clean", "--subagent-stop", stdin=_payload(parent, git_project, agent))
    assert stop.code == 0, stop

    shown = kyo("show", dispatch_id, "--json")
    assert shown.code == 0, shown
    spend = shown.json["worker_tokens"]
    assert spend["outcome"] == STOP_TARGET_MATCHED, spend
    assert spend["known"] is True
    assert spend["model"] == "claude-opus-5"
    assert spend["messages"] == 4
    assert spend["input_tokens"] == 28
    assert spend["cache_creation_input_tokens"] == 4_400
    assert spend["cache_read_input_tokens"] == 360_000
    assert spend["output_tokens"] == 2_560
    assert spend["total_tokens"] == 366_988

    text = kyo("show", dispatch_id)
    assert text.code == 0, text
    assert "worker tokens: 366988 total on claude-opus-5" in text.out, text.out

    listed = kyo("worktrees", "--json")
    assert listed.code == 0, listed
    rows = [row for row in listed.json["worktrees"] if row["dispatch_id"] == dispatch_id]
    assert rows, listed.json["worktrees"]
    assert rows[0]["worker_tokens"] == spend
    # And on the human's line too, for the row that is still running.
    plain = kyo("worktrees")
    assert plain.code == 0, plain
    assert "claude-opus-5 · 366988 tokens" in plain.out, plain.out


def test_a_dispatch_nothing_stopped_on_reads_unknown_end_to_end(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The other half of c1/c2: `kyo show` on a dispatch with no stop row.

    It must say unknown and say why, on the surface a controller actually reads --
    a criterion met by a zero would be met by a bug.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-untouched")

    shown = kyo("show", dispatch_id, "--json")

    assert shown.code == 0, shown
    assert shown.json["worker_tokens"]["known"] is False
    assert shown.json["worker_tokens"]["outcome"] == TOKENS_NO_TRANSCRIPT_RECORDED
    text = kyo("show", dispatch_id)
    assert "worker tokens: unknown" in text.out, text.out


# ------------------------------------------------------------------ KYO-298.c6
#
# WHAT A FULL-CONTEXT RE-WRITE IS, AND WHY IT IS COUNTED HERE. The harness's prompt
# cache has a time-to-live. A worker that sits through a long foreground command --
# the whole suite, ten to seventeen minutes -- comes back to an expired cache, and the
# harness re-writes its ENTIRE context as cache creation on the worker's very next
# message. That message is the whole cost of the wait, and it is the only durable
# evidence that the wait happened at all.
#
# It is counted in the transcript reader rather than in a controller's script because
# KYO-298 is a POLICY ("the whole suite is the controller's") whose only honest
# justification is a measurement -- and a policy justified by a measurement nobody can
# re-take is a policy that outlives its evidence. Carried per dispatch, in every
# project, `kyo show` answers "is this working?" without anyone parsing a transcript by
# hand.
#
# THE THRESHOLDS were measured on one wave of five workers in this store: exactly
# two messages across all five transcripts cleared both, at 172,733 and 325,752 tokens,
# each immediately after a full-suite run, and the three workers that ran test files
# only produced none. The fixtures below are built from those two shapes.

#: The re-write itself, in the shape KYO-297 measured: nearly the whole input side is
#: cache creation, because the cache it would otherwise have read has expired.
REWRITE_USAGE = {
    "input_tokens": 4,
    "cache_creation_input_tokens": 172_733,
    "cache_read_input_tokens": 0,
    "output_tokens": 120,
}

#: A healthy long turn: two orders of magnitude more cache READ than written. This is
#: what every message of a worker that never waited looks like, and it is the shape the
#: share threshold exists to exclude.
HEALTHY_USAGE = {
    "input_tokens": 7,
    "cache_creation_input_tokens": 1_100,
    "cache_read_input_tokens": 90_000,
    "output_tokens": 640,
}

#: A big write that is NOT a re-write: most of the input side, but small. A short
#: conversation re-writes all of its 30,000-token context routinely and it costs
#: nothing worth counting, which is what the floor is for.
SMALL_WRITE_USAGE = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 12_000,
    "cache_read_input_tokens": 0,
    "output_tokens": 90,
}


def _usage_transcript(
    tmp_path: Path, agent_id: str, prompt: str, usages: list[dict], *, ids: bool = True
) -> Path:
    """A subagent transcript whose assistant messages carry exactly ``usages``.

    :func:`_worker_transcript` writes identical turns, which cannot express the
    difference a re-write IS. Same file layout, so the same stop resolution matches it
    and the end-to-end tests below go through the real hook rather than around it.
    """
    session_dir = tmp_path / "transcripts" / "session"
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    parent = session_dir.with_suffix(".jsonl")
    parent.write_text("", encoding="utf-8")
    lines = [
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "agentId": agent_id,
                "cwd": "/the/controllers/cwd",
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }
        )
    ]
    for index, usage in enumerate(usages):
        message = {
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": f"turn {index}"}],
            "usage": dict(usage),
        }
        if ids:
            message["id"] = f"msg_{index}"
        lines.append(json.dumps({"type": "assistant", "agentId": agent_id, "message": message}))
    (session_dir / "subagents" / f"agent-{agent_id}.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return parent


def test_one_rewrite_and_one_that_is_not(tmp_path: Path) -> None:
    """c6's fixture, exactly as the criterion words it: one re-write and one without.

    Four messages: the first (never counted, whatever it looks like -- writing the whole
    context once is what STARTING a conversation is), a healthy turn, the re-write, and
    another healthy turn. One counted, and the tokens are the re-write's cache creation
    rather than its whole usage, because what is being measured is what the wait cost.
    """
    parent = _usage_transcript(
        tmp_path,
        "rw1",
        "prompt",
        [REWRITE_USAGE, HEALTHY_USAGE, REWRITE_USAGE, HEALTHY_USAGE],
    )

    usage = sum_transcript_usage(_agent_file(parent, "rw1"))

    assert usage.messages == 4
    assert usage.context_rewrites == 1
    assert usage.context_rewrite_tokens == 172_733


def test_a_worker_that_never_waited_counts_none(tmp_path: Path) -> None:
    """The result the policy is trying to produce, and it must be distinguishable.

    Three of the five workers measured ran test files only and produced
    no re-write at all. Zero here is a finding, not an absence -- see
    :meth:`WorkerTokens.rewrite_clause`, which says so in words rather than by silence.
    """
    parent = _usage_transcript(
        tmp_path, "rw2", "prompt", [HEALTHY_USAGE, HEALTHY_USAGE, HEALTHY_USAGE]
    )

    usage = sum_transcript_usage(_agent_file(parent, "rw2"))

    assert usage.context_rewrites == 0
    assert usage.context_rewrite_tokens == 0


def test_a_big_write_below_the_floor_is_not_a_rewrite(tmp_path: Path) -> None:
    """Both thresholds are load-bearing, and this is the one the SHARE alone would miss.

    12,000 tokens is 99% of its message's input side and is not worth a controller's
    attention: a short worker re-writes its whole small context routinely. Without the
    floor, every such worker would read as having paid for a wait it never had.
    """
    parent = _usage_transcript(
        tmp_path, "rw3", "prompt", [HEALTHY_USAGE, SMALL_WRITE_USAGE, SMALL_WRITE_USAGE]
    )

    assert sum_transcript_usage(_agent_file(parent, "rw3")).context_rewrites == 0


def test_a_large_write_inside_a_large_context_is_not_a_rewrite(tmp_path: Path) -> None:
    """...and the one the FLOOR alone would miss.

    60,000 tokens of creation beside 900,000 of cache read is over the floor and is
    ordinary incremental caching in a long conversation -- the cache was there and was
    read. A re-write is the case where it was NOT.
    """
    ordinary_but_large = {
        "input_tokens": 9,
        "cache_creation_input_tokens": 60_000,
        "cache_read_input_tokens": 900_000,
        "output_tokens": 700,
    }
    parent = _usage_transcript(
        tmp_path, "rw4", "prompt", [HEALTHY_USAGE, ordinary_but_large]
    )

    assert sum_transcript_usage(_agent_file(parent, "rw4")).context_rewrites == 0


def test_a_rewrite_split_across_content_blocks_is_counted_once(tmp_path: Path) -> None:
    """c6's "counted once per message id, last row wins" -- KYO-292.c4's rule, reused.

    The harness writes one transcript ROW per content block and repeats the whole
    `usage` on each. A re-write in a three-block message is ONE re-write; counting rows
    would report three and treble the tokens, which is the same defect KYO-292 fixed for
    the totals, in a number whose whole job is to be compared against a threshold.
    """
    transcript = tmp_path / "blocks.jsonl"
    _blocks(
        transcript,
        ("m1", dict(HEALTHY_USAGE)),
        ("m2", dict(REWRITE_USAGE)),
        ("m2", dict(REWRITE_USAGE)),
        ("m2", dict(REWRITE_USAGE)),
    )

    usage = sum_transcript_usage(transcript)

    assert usage.messages == 2
    assert usage.context_rewrites == 1
    assert usage.context_rewrite_tokens == 172_733


def test_the_thresholds_are_read_from_one_place(tmp_path: Path) -> None:
    """Law 3, where a drifting copy would be silent.

    The numbers appear in the code, in `kyo show`'s sentence and in the README. The
    predicate is one function; this is the test that fails if a second copy of the
    arithmetic appears anywhere, because it drives the boundary from the constants
    themselves rather than from 50,000 typed again here.
    """
    just_over = {
        "input_tokens": 0,
        "cache_creation_input_tokens": CONTEXT_REWRITE_MIN_TOKENS + 1,
        "cache_read_input_tokens": 0,
        "output_tokens": 1,
    }
    just_under = {**just_over, "cache_creation_input_tokens": CONTEXT_REWRITE_MIN_TOKENS}
    share_just_under = {
        "input_tokens": 0,
        "cache_creation_input_tokens": CONTEXT_REWRITE_MIN_TOKENS + 1,
        # Enough cache read to drop the creation share below the threshold.
        "cache_read_input_tokens": int((CONTEXT_REWRITE_MIN_TOKENS + 1) * 0.5),
        "output_tokens": 1,
    }

    assert _is_context_rewrite(just_over) is True
    assert _is_context_rewrite(just_under) is False
    assert _is_context_rewrite(share_just_under) is False
    assert 0 < CONTEXT_REWRITE_SHARE <= 1


def test_the_count_is_summed_across_a_resumed_workers_transcripts(tmp_path: Path) -> None:
    """A resumed worker holds two transcripts, and each one's first message is its own
    legitimate first write -- so a worker that waited in both really did pay twice.

    Deliberately the opposite treatment from ``latest_turn_tokens``, which belongs to
    ONE transcript (adding two would report a context size the worker never had). The
    two quantities answer different questions and this file is where that is pinned.
    """
    first = _usage_transcript(
        tmp_path / "a", "twin", "prompt", [HEALTHY_USAGE, REWRITE_USAGE]
    )
    second = _usage_transcript(
        tmp_path / "b", "twin", "prompt", [HEALTHY_USAGE, REWRITE_USAGE]
    )
    ledger = StopAuditLedger(
        audits=[
            _matched("KYO-1.d1", _agent_file(first, "twin"), event="KYO-E1"),
            _matched("KYO-1.d1", _agent_file(second, "twin"), event="KYO-E2"),
        ]
    )

    spend = worker_tokens_from(ledger, "KYO-1.d1")

    assert spend.transcripts == 2
    assert spend.context_rewrites == 2
    assert spend.context_rewrite_tokens == 2 * 172_733


def test_the_rewrite_reaches_kyo_show_and_worktrees_json(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c6's two surfaces, through the real hook and with nothing written into the store
    by hand: the sentence a controller reads, and the additive JSON fields the desktop's
    activity poll reads.

    ADDITIVE is asserted rather than assumed: every field `kyo worktrees --json` carried
    before is still there and still means the same thing, so a consumer that has never
    heard of re-writes is unaffected by this record.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-rewrite")
    agent = "b7c0326701b2a6cf1"
    prompt = f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    parent = _usage_transcript(
        tmp_path, agent, prompt, [HEALTHY_USAGE, REWRITE_USAGE, HEALTHY_USAGE]
    )

    stop = kyo("check-clean", "--subagent-stop", stdin=_payload(parent, git_project, agent))
    assert stop.code == 0, stop

    spend = kyo("show", dispatch_id, "--json").json["worker_tokens"]
    assert spend["context_rewrites"] == 1
    assert spend["context_rewrite_tokens"] == 172_733
    # Additive: the KYO-284/292 fields are untouched beside them.
    assert spend["messages"] == 3
    assert spend["latest_turn_tokens"] == sum(HEALTHY_USAGE.values())

    text = kyo("show", dispatch_id)
    assert "1 full-context re-write(s)" in text.out, text.out
    assert "172733 tokens of cache creation" in text.out, text.out

    rows = [
        row
        for row in kyo("worktrees", "--json").json["worktrees"]
        if row["dispatch_id"] == dispatch_id
    ]
    assert rows and rows[0]["worker_tokens"] == spend


def test_a_worker_with_no_rewrite_says_so_rather_than_saying_nothing(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """Zero is the outcome this whole record is trying to produce, so it is SAID.

    A reader who cannot tell "this worker never re-wrote its context" from "nobody
    looked" cannot tell whether the policy is working -- which is the only question
    the counter exists to answer.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-clean-run")
    agent = "c7c0326701b2a6cf2"
    prompt = f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
    parent = _usage_transcript(tmp_path, agent, prompt, [HEALTHY_USAGE, HEALTHY_USAGE])

    stop = kyo("check-clean", "--subagent-stop", stdin=_payload(parent, git_project, agent))
    assert stop.code == 0, stop

    assert kyo("show", dispatch_id, "--json").json["worker_tokens"]["context_rewrites"] == 0
    assert "no full-context re-write" in kyo("show", dispatch_id).out
