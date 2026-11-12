"""``kyo discuss``: a discussion opened when it starts, at a turn the index stamped (KYO-658).

Decision KYO-654 adopted DC2 and DC3 of the decision-capture proposal (KYO-651, sections
5.1 Tiers 1 and 3, in the store repository). A DISCUSSION is a record of the ``discussion``
kind (:data:`kyosei.kinds.DISCUSSION_KIND`): knowledge archetype, not counted, swept. Its
life is its turn references (``turn_refs``, ``KYO-n.sN``), each naming one of the person's
turns as the turn index (:mod:`kyosei.cmd_turns`) recorded it:

* ``kyo discuss open --title "<topic>"`` files one with an ``opened`` reference to the
  latest confirmed person turn of ``$CLAUDE_CODE_SESSION_ID``. ``--at-prompt <prompt>`` names an
  earlier indexed turn instead, and the lag between that turn and the opening is shown.
* ``kyo discuss note <id> --file F`` keeps what was put to the person, the options and the
  recommendation, as a record note, with a ``note`` reference to the turn it answers.
* Spawning a decision, proposal, plan or task from it closes it, with a ``closed``
  reference naming what it produced; ``kyo discuss close <id> --dropped --why "..."``
  closes it with nothing produced. A knowledge record has no status flow, so open and
  closed are read from the references (:func:`discussion_state`), never stored twice.

NOTHING IS TYPED BY AN AGENT (law 16). The session is the one Claude Code sets in its shell;
the prompt id, the transcript record's uuid, the time and whether the hook or the
transcript stamped it are copied from the index line. Before reading the index, the command
folds the session's transcript into it, as the Stop hook does
(:func:`kyosei.cmd_turns.confirm_from_transcript`): the prompt being answered is in the
transcript as soon as it was sent, while the Stop hook that would confirm it has not run,
so without this the latest confirmed turn would be the one before it. When there is no line
to stamp from (no session, a project that has not opted in to reading transcripts, or no
confirmed line for the session) the reference is ``index_state: absent`` and carries no
time but its own ``created_at``.

DECISIONS REQUIRE A DISCUSSION (DC3). A kind whose policy sets ``requires_discussion`` (the
shipped ``decision``) is filed, by ``kyo new`` or ``kyo spawn``, from its discussion: spawned
from it, or from a record the discussion produced, or naming it with ``--discussion``. With
none, ``--opened-late`` opens one in the same transaction, at the turn ``--at-prompt`` names, and
its ``opened`` reference is ``late`` for good; with no index it records ``index_state:
absent``. Otherwise the filing is refused under ``decision-requires-discussion``
(:func:`filing_discussion`). As with credits (KYO-656, DC12), the rule applies only where
``people:`` declares someone, so an upgrade refuses nothing before the operator has chosen;
and a store scaffolded before this build has no ``requires_discussion`` key until its
operator adds it (``kyo doctor``'s kind-policy notice prints the line).

A decided credit filed from a discussion takes its span from the discussion's references
(:func:`kyosei.credits.discussion_span`) unless ``--span-first`` and ``--span-last`` give
one, so nobody reconstructs where it began.

WHY A TURN REFERENCE IS NOT A NOTE'S TURN ANCHOR. Both name a transcript record by session
and uuid (``turn`` here, ``target`` there), and the names follow the index and the anchor
table respectively. An anchor (:mod:`kyosei.cmd_notes`) addresses a span of characters
inside one record, and its digest is computed from that record's text when the anchor is
made, so it needs the transcript and goes stale when the text changes. A turn reference
addresses a whole prompt of the person's, and its facts come from the turn index, which
holds no text and survives the transcript's deletion. It has nothing to digest, and the
integrity object for a whole prompt is the per-line commitment the index line will carry
(KYO-663), which a reference cites by its session and prompt id rather than repeating.

THE STOP CHECK (KYO-662, decision KYO-654 DC4, proposal section 5.1 Tier 2; moved to the next
prompt by KYO-876). ``kyo discuss check --hook`` runs at every Stop, in its own hook group. It
reads the turn that just ended (the person's words and whether AskUserQuestion was used, from
the transcript through :func:`kyosei.harness.claude_code_transcript.read_turn`; the reply,
from the payload's ``last_assistant_message``) and asks two things: does a cue say a choice
was put to the person or raised by them (:func:`detect`), and is the turn attached, which
means a discussion is open in this session or already names the turn. At Stop it prints
nothing and does not continue the conversation: when a cue fires on an unattached turn it
writes ``candidate`` on the turn's index line (:func:`kyosei.cmd_turns.mark_candidate`). The
first version printed hook feedback at Stop, which Claude Code answers by asking the agent for
visible text after the skip's tool call, so the person read a line about a skip (KYO-876).

At the session's next UserPromptSubmit the same command, run in that event's hook group, names
the newest candidate once, as one line of context (:func:`prompt_line`): the turn's time, its
prompt id and the two commands. The agent then opens a discussion or skips inside the turn it is
already taking, and says nothing about a skip. A candidate is marked asked when named, so it
is named once, and is cleared by a discussion that names the turn or by a skip. The check says
nothing when the turn is attached or its index line carries a skip. It never exits non-zero
and never prints anything else: any failure to read the store, the index or the transcript
is a pass, reported only under ``--json``.

``kyo discuss skip --why "<one line>"`` writes the reason on the turn's index line
(:func:`kyosei.cmd_turns.mark_skipped`), so a dismissal is kept, and ``kyo doctor`` counts
them per week beside the shares of decisions opened live, late and unindexed
(:func:`kyosei.doctor_records.check_discussion_shares`).

The cue lists are deliberately broad. Measured on the maintainer's earlier transcripts by
``tests/fixtures/kyo662_measure_detector.py`` (which reads private files named at run time
and prints counts only), they are tuned for recall on the openings of known discussions
against the number of turn ends outside any discussion at which the check would speak.
"""

from __future__ import annotations

import json
import re
import sys
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path

from . import commands, export
from .clock import now
from .cmd_sessions import recorded_transcript_path
from .cmd_turns import (
    HUMAN,
    confirm_from_transcript,
    mark_candidate,
    mark_skipped,
    next_candidate,
    prompt_lines,
    read_lines,
)
from .commands import Context, Output, _load_registry, _load_status_ladder, _touch_record
from .config import READ_TRANSCRIPTS_KEY, transcripts_opted_in
from .contracts import TURN_REF_ABSENT, TURN_REF_INDEXED
from .credits import ENV_SESSION, enforced
from .errors import ContractViolation, KyoseiError, NotFound, UsageError
from .harness import claude_code_transcript as transcript
from .kinds import DISCUSSION_KIND
from .prose import read_prose, single_line
from .render import echo_block, echo_value, turn_ref_lines
from .store import Store

CONTRACT_DECISION_REQUIRES_DISCUSSION = "decision-requires-discussion"

ROLE_OPENED = "opened"
ROLE_NOTE = "note"
ROLE_CLOSED = "closed"
#: KYO-749.c2: a record spawned from a discussion that is already closed.
CONTRACT_SPAWN_FROM_CLOSED_DISCUSSION = "spawn-from-closed-discussion"

#: How many of the session's confirmed turns a refusal over ``--at-prompt`` lists.
LISTED_TURNS = 5

#: The shortest ``--at-prompt`` prefix accepted, so a one-character typo matches nothing.
MIN_AT_PREFIX = 4


# ------------------------------------------------------------------ the stamp


@dataclass(frozen=True)
class TurnStamp:
    """One of the person's turns as the index holds it, or why there is none to stamp from.

    ``store_fields()`` is what a turn reference stores from it."""

    index_state: str
    session: str | None
    prompt_id: str | None = None
    turn: str | None = None
    at: str | None = None
    source: str | None = None
    why: str = ""

    @property
    def indexed(self) -> bool:
        return self.index_state == TURN_REF_INDEXED

    def store_fields(self) -> dict:
        return {
            "index_state": self.index_state,
            "session": self.session,
            "prompt_id": self.prompt_id,
            "turn": self.turn,
            "at": self.at,
            "source": self.source,
        }

    def label(self) -> str:
        if not self.indexed:
            return f"index absent: {self.why}"
        prompt = (self.prompt_id or self.turn or "?")[:8]
        return f"prompt {prompt} at {self.at} (session {(self.session or '?')[:8]}, {self.source})"


def _session_of(ctx: Context) -> str | None:
    return ctx.environ.get(ENV_SESSION) or None


def _refresh_index(ctx: Context, session: str) -> None:
    """Fold the session's transcript into the index, as the Stop hook would. Best effort: a
    transcript that cannot be found or read leaves the index as it is."""
    if ctx.layout is not None and ctx.layout.store_via_main_checkout is not None:
        return  # a worktree's kyo never writes the main checkout's index (cmd_turns._run_hook)
    try:
        paths = transcript.candidate_transcripts(
            session,
            config=transcript.config_dir(ctx.environ),
            recorded=recorded_transcript_path(ctx.root, session),
        )
    except ValueError:
        return
    if not paths:
        return
    try:
        confirm_from_transcript(ctx.root, session, Path(paths[0]))
    except OSError:
        return


def person_turns(ctx: Context, store: Store, session: str) -> list[dict]:
    """The session's confirmed person turns in the index, oldest first, after folding in the
    transcript. ``[]`` in a project that has not opted in."""
    if not transcripts_opted_in(store.config):
        return []
    _refresh_index(ctx, session)
    lines = [
        line
        for line in read_lines(ctx.root)
        if line.get("session") == session
        and line.get("origin") == HUMAN
        and isinstance(line.get("at"), str)
    ]
    return sorted(lines, key=lambda line: line["at"])


def _absent(session: str | None, why: str) -> TurnStamp:
    return TurnStamp(TURN_REF_ABSENT, session, why=why)


def _from_line(line: dict) -> TurnStamp:
    return TurnStamp(
        TURN_REF_INDEXED,
        line["session"],
        prompt_id=line.get("prompt_id"),
        turn=line.get("turn"),
        at=line["at"],
        source=line.get("source"),
    )


def _turn_listing(lines: list[dict]) -> list[str]:
    return [
        f"  prompt {line.get('prompt_id') or '(mid-turn)'}  turn {line.get('turn')}  {line['at']}"
        for line in lines[-LISTED_TURNS:]
    ]


def current_stamp(ctx: Context, store: Store, at: str | None = None) -> TurnStamp:
    """The turn a discussion is stamped at: the one ``at`` names, or the session's latest
    confirmed person turn. ``at`` matches a prompt id or a transcript record uuid, whole or
    by a unique prefix of at least :data:`MIN_AT_PREFIX` characters. Refuses an ``at`` that
    names nothing; with no index to name from it refuses ``at`` too, rather than drop it."""
    session = _session_of(ctx)
    if session is None:
        stamp = _absent(None, f"no Claude Code session (${ENV_SESSION} is unset)")
    elif not transcripts_opted_in(store.config):
        stamp = _absent(
            session,
            "this project has not opted in to reading transcripts "
            f"(`{READ_TRANSCRIPTS_KEY}:` in kyosei/config.yml), so it keeps no turn index",
        )
    else:
        lines = person_turns(ctx, store, session)
        if not lines:
            stamp = _absent(
                session, f"the turn index holds no confirmed turn of the person's in {session}"
            )
        elif at is None:
            return _from_line(lines[-1])
        else:
            return _from_line(_match_at(lines, at, session))
    if at is not None:
        raise UsageError(
            f"--at-prompt names a turn of the turn index, and there is none to name ({stamp.why}); "
            "drop --at-prompt, and the opening is recorded with index_state absent"
        )
    return stamp


def _match_at(lines: list[dict], at: str, session: str) -> dict:
    exact = [line for line in lines if at in (line.get("prompt_id"), line.get("turn"))]
    if len(exact) == 1:
        return exact[0]

    def named(line: dict) -> bool:
        ids = (line.get("prompt_id"), line.get("turn"))
        return any(isinstance(v, str) and v.startswith(at) for v in ids)

    found = [line for line in lines if named(line)] if len(at) >= MIN_AT_PREFIX else []
    if len(found) == 1:
        return found[0]
    what = "matches several" if found else "matches none"
    raise UsageError(
        "\n".join(
            [
                f"--at-prompt {at!r} {what} of the person's confirmed turns in session {session}; "
                "give a prompt id or a transcript record uuid, whole or by a unique prefix of "
                f"at least {MIN_AT_PREFIX} characters. The latest:",
                *_turn_listing(lines),
            ]
        )
    )


# ------------------------------------------------------------------ the state


def discussion_state(refs: list[dict]) -> dict:
    """What a discussion's turn references say about it (export shape, oldest first).

    ``open`` until a ``closed`` reference exists; ``late`` when its opening was written
    after its decision; ``produced`` lists the records whose spawning closed it."""
    opened = next((r for r in refs if r["role"] == ROLE_OPENED), None)
    closed = [r for r in refs if r["role"] == ROLE_CLOSED]
    return {
        "open": not closed,
        "opened": opened,
        "late": bool(opened and opened["late"]),
        "index_state": opened["index_state"] if opened else None,
        "closed": closed[0] if closed else None,
        "produced": [r["produced_id"] for r in closed if r.get("produced_id")],
    }


def _refs(store: Store, record_id: str) -> list[dict]:
    return [export._turn_ref(r) for r in store.turn_refs(record_id)]


def require_discussion(store: Store, record_id: str) -> dict:
    """The record ``record_id``, refused unless it is a discussion that has not been voided."""
    row = store.record(record_id)
    if row["kind"] != DISCUSSION_KIND:
        raise UsageError(f"{record_id} is a {row['kind']!r}, not a {DISCUSSION_KIND!r}")
    if _load_status_ladder(store).is_terminal(row["status"]):
        raise UsageError(f"{record_id} is void; a voided discussion is not filed from")
    return row


def discussion_of(store: Store, record_id: str) -> str | None:
    """The discussion a record was filed from: a ``produced-by`` link to a discussion,
    directly or through the one record it was spawned from. None when there is none."""
    seen: set[str] = set()
    frontier = [record_id]
    for _hop in range(2):
        nxt: list[str] = []
        for rid in frontier:
            for link in store.links_from(rid):
                if link["edge_type"] != "produced-by" or link["dst_id"] in seen:
                    continue
                seen.add(link["dst_id"])
                try:
                    kind = store.record(link["dst_id"])["kind"]
                except NotFound:
                    continue
                if kind == DISCUSSION_KIND:
                    return link["dst_id"]
                nxt.append(link["dst_id"])
        frontier = nxt
    return None


def discussion_summary(store: Store, record_id: str) -> dict | None:
    """For ``kyo show`` of a record filed from a discussion: the discussion's id, whether it
    is open, whether its opening was late and stamped, and when it was opened."""
    discussion_id = discussion_of(store, record_id)
    if discussion_id is None:
        return None
    state = discussion_state(_refs(store, discussion_id))
    opened = state["opened"] or {}
    return {
        "id": discussion_id,
        "open": state["open"],
        "late": state["late"],
        "index_state": state["index_state"],
        "opened_at": opened.get("at"),
        "opened_turn": opened.get("turn"),
        "opened_session": opened.get("session"),
    }


# ------------------------------------------------------------------ writing


def file_discussion(
    ctx: Context, store: Store, title: str, stamp: TurnStamp, *, late: bool, created_at: str
) -> tuple[str, str]:
    """Insert a discussion record and its ``opened`` reference, inside the caller's
    transaction. Returns ``(record id, turn reference id)``."""
    registry = _load_registry(store)
    if DISCUSSION_KIND not in registry:
        raise KyoseiError(
            f"kind {DISCUSSION_KIND!r} is not defined in kyosei/kinds.yml, so no discussion "
            "can be opened; copy the `discussion:` entry from the packaged default kinds.yml "
            "(and `discussion` into the accepts_from of the kinds it produces)"
        )
    record_id = store.new_record_id()
    seq = int(record_id.rsplit("-", 1)[1])
    store.conn.execute(
        "INSERT INTO records (id, seq, kind, title, status, plan_id, scope, description,"
        " wont_do_reason, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, NULL, NULL, '', NULL, ?, ?)",
        (
            record_id,
            seq,
            DISCUSSION_KIND,
            title,
            _load_status_ladder(store).initial,
            created_at,
            created_at,
        ),
    )
    ref_id = store.add_turn_ref(
        record_id,
        role=ROLE_OPENED,
        author=ctx.actor,
        late=late,
        created_at=created_at,
        **stamp.store_fields(),
    )
    return record_id, ref_id


def close_on(
    ctx: Context,
    store: Store,
    discussion_id: str,
    stamp: TurnStamp,
    *,
    produced_id: str | None = None,
    why: str | None = None,
    created_at: str,
) -> str | None:
    """Write the ``closed`` reference, inside the caller's transaction, unless the
    discussion is already closed. Returns the reference id, or None."""
    if not discussion_state(_refs(store, discussion_id))["open"]:
        return None
    ref_id = store.add_turn_ref(
        discussion_id,
        role=ROLE_CLOSED,
        author=ctx.actor,
        produced_id=produced_id,
        why=why,
        created_at=created_at,
        **stamp.store_fields(),
    )
    _touch_record(store, discussion_id)
    return ref_id


def _link(store: Store, src: str, dst: str, stamp: str) -> str | None:
    """``src produced-by dst``, unless that link exists already."""
    exists = store.conn.execute(
        "SELECT 1 FROM links WHERE src_id = ? AND edge_type = 'produced-by' AND dst_id = ?",
        (src, dst),
    ).fetchone()
    if exists:
        return None
    link_id, lseq = store.new_link_id()
    store.conn.execute(
        "INSERT INTO links (id, seq, src_id, edge_type, dst_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (link_id, lseq, src, "produced-by", dst, stamp),
    )
    return link_id


# ------------------------------------------------------------- filing a decision


@dataclass
class FilingDiscussion:
    """How one `kyo new` / `kyo spawn` names the discussion a record came from, resolved
    before anything is written (:func:`filing_discussion`).

    ``discussion_id`` is an existing discussion; ``late`` set means one is opened in the
    filing's transaction at ``opening``. ``current`` is the person's latest turn, which
    the ``closed`` reference stamps. ``refs`` are the discussion's turn references as the
    credit's span reads them, the late opening's included."""

    current: TurnStamp
    discussion_id: str | None = None
    late: bool = False
    opening: TurnStamp | None = None
    via_source: bool = False
    refs: list[dict] = field(default_factory=list)
    keep_open: bool = False

    def write(self, ctx: Context, store: Store, record_id: str, title: str, stamp: str) -> dict:
        """Inside the filing's transaction: open the late discussion, link the record to
        it, close it. Returns what was done, for the receipt."""
        done: dict = {"late": self.late, "link_id": None, "closed_ref": None}
        discussion_id = self.discussion_id
        if self.late:
            assert self.opening is not None
            discussion_id, opened_ref = file_discussion(
                ctx, store, title, self.opening, late=True, created_at=stamp
            )
            done["opened_ref"] = opened_ref
        assert discussion_id is not None
        done["discussion_id"] = discussion_id
        if not self.via_source:
            done["link_id"] = _link(store, record_id, discussion_id, stamp)
        done["kept_open"] = self.keep_open
        if not self.keep_open:
            done["closed_ref"] = close_on(
                ctx, store, discussion_id, self.current, produced_id=record_id, created_at=stamp
            )
        return done

    def record_ids(self) -> list[str]:
        """The existing discussion this filing changes, for the events it writes."""
        return [self.discussion_id] if self.discussion_id and not self.late else []


def add_filing_flags(parser) -> None:
    """``--discussion``, ``--opened-late`` and ``--at-prompt`` on `kyo new` and `kyo spawn`."""
    parser.add_argument(
        "--discussion",
        metavar="ID",
        help="the discussion this record came from, which filing it closes; a kind whose "
        "policy sets requires_discussion (the default `decision`) names one (KYO-658)",
    )
    parser.add_argument(
        "--opened-late",
        action="store_true",
        help="no discussion was opened: open one now, in the same act, marked late for "
        "good. With a turn index, --at-prompt names the person's turn it began at",
    )
    parser.add_argument(
        "--at-prompt",
        dest="at",
        metavar="PROMPT",
        help="with --opened-late, or `kyo discuss open`: the person's turn the discussion "
        "began at, a prompt id or transcript record uuid from the turn index (a unique "
        "prefix will do); defaults, for `kyo discuss open`, to the latest",
    )


def filing_discussion(
    ctx: Context,
    store: Store,
    kind_def,
    args: Namespace,
    *,
    command: str,
    spawn_source: str | None = None,
) -> FilingDiscussion | None:
    """KYO-658 (DC3): the discussion a filing of ``kind_def`` names, or None when it names
    none and needs none. Refuses, writing nothing, a filing of a kind that sets
    ``requires_discussion`` with no discussion where the rule applies
    (``decision-requires-discussion``); and refuses the flags on a kind that does not."""
    resolved = _filing_discussion(
        ctx, store, kind_def, args, command=command, spawn_source=spawn_source
    )
    keep_open = bool(getattr(args, "keep_open", False))
    if keep_open:
        if resolved is None:
            raise UsageError(
                "--keep-open leaves a discussion open when a record is spawned from it, and "
                "this filing names none: spawn from a discussion, or give --discussion <id>"
            )
        resolved.keep_open = True
    return resolved


def _refuse_closed_source(store: Store, spawn_source: str) -> None:
    """KYO-749.c2: a record spawned from a discussion that is already closed."""
    if not discussion_state(_refs(store, spawn_source))["open"]:
        raise ContractViolation(
            CONTRACT_SPAWN_FROM_CLOSED_DISCUSSION,
            [
                f"{spawn_source} is closed (an earlier spawn from it closed it, or it was "
                "dropped), and a record filed from it now would claim a discussion that has "
                "ended: nothing was written",
                "a closed discussion is not reopened; open the next one with `kyo discuss "
                "open`, or spawn the earlier records with `kyo spawn --keep-open` so the "
                "discussion stays open for the later ones",
            ],
        )


def _filing_discussion(
    ctx: Context,
    store: Store,
    kind_def,
    args: Namespace,
    *,
    command: str,
    spawn_source: str | None = None,
) -> FilingDiscussion | None:
    given = getattr(args, "discussion", None)
    late = bool(getattr(args, "opened_late", False))
    at = getattr(args, "at", None)
    if spawn_source is not None and store.record(spawn_source)["kind"] == DISCUSSION_KIND:
        _refuse_closed_source(store, spawn_source)
    if not kind_def.requires_discussion:
        given_flags = (("--discussion", given), ("--opened-late", late), ("--at-prompt", at))
        flags = [flag for flag, value in given_flags if value]
        if flags:
            raise UsageError(
                f"{', '.join(flags)} name a discussion, and kind {kind_def.name!r} does not "
                "set policy.requires_discussion"
            )
        if spawn_source is not None and store.record(spawn_source)["kind"] == DISCUSSION_KIND:
            return FilingDiscussion(
                current_stamp(ctx, store),
                discussion_id=spawn_source,
                via_source=True,
                refs=_refs(store, spawn_source),
            )
        return None
    if given and late:
        raise UsageError(
            "--discussion names the discussion this came from and --opened-late says none was "
            "opened; give one"
        )
    if at is not None and not late:
        raise UsageError(
            "--at-prompt goes with --opened-late here: it names where the late discussion began"
        )
    from_source = None
    via_source = False
    if spawn_source is not None:
        if store.record(spawn_source)["kind"] == DISCUSSION_KIND:
            from_source, via_source = spawn_source, True
        else:
            from_source = discussion_of(store, spawn_source)
    if from_source is not None and (late or (given and given != from_source)):
        raise UsageError(
            f"{spawn_source} came from discussion {from_source}, which this filing is filed "
            "from; drop --discussion and --opened-late"
        )
    discussion_id = given or from_source
    if discussion_id is not None:
        require_discussion(store, discussion_id)
        return FilingDiscussion(
            current_stamp(ctx, store),
            discussion_id=discussion_id,
            via_source=via_source,
            refs=_refs(store, discussion_id),
        )
    if late:
        opening = current_stamp(ctx, store, at)
        if opening.indexed and at is None:
            lines = person_turns(ctx, store, opening.session or "")
            raise ContractViolation(
                CONTRACT_DECISION_REQUIRES_DISCUSSION,
                [
                    f"--opened-late opens the discussion at the person's turn it began at, and "
                    f"session {opening.session} has a turn index, so `{command}` names that "
                    "turn: nothing was written",
                    f"add --at-prompt <prompt>, a prompt id from the index. The latest of the "
                    f"person's {len(lines)} confirmed turns:",
                    *_turn_listing(lines),
                ],
            )
        current = current_stamp(ctx, store)
        ref = {"role": ROLE_OPENED, "late": True, **opening.store_fields()}
        return FilingDiscussion(current, late=True, opening=opening, refs=[ref])
    if not enforced(store):
        return None
    raise ContractViolation(
        CONTRACT_DECISION_REQUIRES_DISCUSSION,
        [
            f"kind {kind_def.name!r} sets policy.requires_discussion, so `{command}` names the "
            "discussion it came from: nothing was written",
            "file it from the discussion `kyo discuss open` filed: `kyo spawn <discussion-id> "
            f"--kind {kind_def.name}`, or give the same command --discussion <discussion-id>",
            "or, when no discussion was opened, give the same command --opened-late (with "
            "--at-prompt <prompt> where the session has a turn index), which opens one now and "
            "marks it late for good",
        ],
    )


def filing_lines(store: Store, done: dict | None) -> list[str]:
    """What a filing echoes about its discussion."""
    if not done:
        return []
    discussion_id = done["discussion_id"]
    refs = _refs(store, discussion_id)
    late = " (opened late, at this filing)" if done["late"] else ""
    out = [f"discussion: {discussion_id}{late}"]
    if done["late"]:
        opened = discussion_state(refs)["opened"]
        out += [f"  {line}" for line in turn_ref_lines([opened])]
    if done.get("link_id"):
        out.append(f"  link: {done['link_id']}  produced-by {discussion_id}")
    if done.get("kept_open"):
        out.append(f"  left open: {discussion_id} (--keep-open)")
    elif done.get("closed_ref"):
        out.append(
            f"  closed: {done['closed_ref']} — this filing closed {discussion_id}; "
            "--keep-open leaves a discussion open for the next record"
        )
    return out


# ------------------------------------------------------------------ the command


def _receipt(store: Store, record_id: str, head: str) -> list[str]:
    refs = _refs(store, record_id)
    return [head, *(f"  {line}" for line in turn_ref_lines(refs))]


def _open(ctx: Context, store: Store, args: Namespace) -> Output:
    if args.id is not None:
        raise UsageError("open takes no id: it files a new discussion")
    if not getattr(args, "title", None):
        raise UsageError('open needs the topic: give --title "<topic>"')
    title = single_line(args.title, what="--title")
    stamp = current_stamp(ctx, store, getattr(args, "at", None))
    with store.transaction():
        created = now()
        record_id, ref_id = file_discussion(
            ctx, store, title, stamp, late=False, created_at=created
        )
        after, events = commands._commit_and_export(
            store,
            command="discuss open",
            actor=ctx.actor,
            record_ids=[record_id],
            before={record_id: None},
        )
    commands._finish_mutation(store, [record_id], events)
    lines = [
        f"opened {record_id}",
        echo_value("kind", DISCUSSION_KIND),
        echo_value("title", title),
        *_receipt(store, record_id, "turn references:")[1:],
    ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "discuss open",
            "id": record_id,
            "record_id": record_id,
            "turn_ref": ref_id,
            "stamp": stamp.store_fields() | {"why": stamp.why or None},
            "record": after[record_id],
        },
    )


def _note(ctx: Context, store: Store, args: Namespace) -> Output:
    if args.id is None:
        raise UsageError("note names the discussion: give its id, and --file FILE")
    if not getattr(args, "file", None):
        raise UsageError(
            "--file holds what was put to the person (the options and the recommendation), "
            "from a file or '-' for stdin (law 5)"
        )
    require_discussion(store, args.id)
    text = read_prose(args.file, what="--file")
    if not text.strip():
        raise UsageError("--file must not be empty: it is stored as the discussion's note")
    stamp = current_stamp(ctx, store, getattr(args, "at", None))
    with store.transaction():
        before = {args.id: export.record_document(store, args.id)}
        created = now()
        note_id, note_seq = store.new_child_id(args.id, "note")
        store.conn.execute(
            "INSERT INTO notes (id, record_id, seq, text, created_at, superseded_by,"
            " superseded_at) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
            (note_id, args.id, note_seq, text, created),
        )
        ref_id = store.add_turn_ref(
            args.id,
            role=ROLE_NOTE,
            author=ctx.actor,
            note_id=note_id,
            created_at=created,
            **stamp.store_fields(),
        )
        _touch_record(store, args.id)
        after, events = commands._commit_and_export(
            store, command="discuss note", actor=ctx.actor, record_ids=[args.id], before=before
        )
    commands._finish_mutation(store, [args.id], events)
    lines = [
        f"noted on {args.id}: {note_id}",
        *echo_block("note", text),
        *(f"  {line}" for line in turn_ref_lines([export._turn_ref(store.turn_refs(args.id)[-1])])),
    ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "discuss note",
            "id": args.id,
            "record_id": args.id,
            "note_id": note_id,
            "turn_ref": ref_id,
            "record": after[args.id],
        },
    )


def _close(ctx: Context, store: Store, args: Namespace) -> Output:
    if args.id is None:
        raise UsageError('close names the discussion: give its id, --dropped and --why "<reason>"')
    if not getattr(args, "dropped", False):
        raise UsageError(
            "a discussion closes when a decision, proposal, plan or task is spawned from it; "
            'close is for one that produced nothing, and says so with --dropped --why "<reason>"'
        )
    why = getattr(args, "why", None)
    if not (why or "").strip():
        raise UsageError("--dropped records that nothing was produced; the reason goes in --why")
    why = single_line(why, what="--why")
    require_discussion(store, args.id)
    if not discussion_state(_refs(store, args.id))["open"]:
        raise UsageError(f"{args.id} is already closed")
    stamp = current_stamp(ctx, store, getattr(args, "at", None))
    with store.transaction():
        before = {args.id: export.record_document(store, args.id)}
        ref_id = close_on(ctx, store, args.id, stamp, why=why, created_at=now())
        after, events = commands._commit_and_export(
            store, command="discuss close", actor=ctx.actor, record_ids=[args.id], before=before
        )
    commands._finish_mutation(store, [args.id], events)
    return Output(
        _receipt(store, args.id, f"closed {args.id} with nothing produced"),
        {
            "ok": True,
            "command": "discuss close",
            "id": args.id,
            "record_id": args.id,
            "turn_ref": ref_id,
            "record": after[args.id],
        },
    )


# ------------------------------------------------------------------ the Stop check (KYO-662)

#: The cues, as the check names them in its message and under ``--json``.
CUE_PERSON = "the person's message opens a choice"
CUE_REPLY = "your reply puts a choice to the person"
CUE_ASKED = "the turn used AskUserQuestion"

#: Text the detector does not read: code, URLs and what the harness injected.
_NOT_PROSE = re.compile(
    r"```.*?(?:```|\Z)|`[^`\n]*`|https?://\S+"
    r"|<(system-reminder|pasted-content|pasted_content)\b[^>]*>.*?</\1>",
    re.S,
)

#: Phrases in the person's words that open a choice (proposal section 5.1, Tier 2).
_PERSON_PHRASES = re.compile(
    r"\b(?:what do you think|what would you|how about|what about|what if|should (?:we|i|it)"
    r"|shall we|could we|would it|options?|i'?m leaning|i am leaning|leaning toward"
    r"|let'?s discuss|let'?s talk|thoughts|i wonder|wondering|not sure|unsure"
    r"|alternatives?|trade-?offs?|pros and cons|or (?:should|would|do|is|are) "
    r"|i'?d (?:rather|prefer|like)|i would (?:rather|prefer|like)|prefer|rather than"
    r"|instead of|maybe we|is there a (?:way|better)|which (?:one|is|would|do|should|of)"
    r"|do you (?:think|recommend|suggest)|recommend|your (?:opinion|view|take)|decide|decision)\b",
    re.I,
)

#: Phrases in the agent's final message that put a choice to the person. Offers and
#: reports of waiting ("if you'd like", "waiting on you", "needs your") were measured and
#: left out: they added turn ends outside any discussion and no opening of one. "do you
#: want", "would you like", "want me to", "shall i", "should i" and "say the word" were
#: offers of the same kind and were left out with KYO-859.
_REPLY_PHRASES = re.compile(
    r"\b(?:i recommend|my recommendation|recommendation:|i(?:'d| would) (?:recommend"
    r"|suggest|go with|lean|pick|choose)|i suggest|which (?:do|would|should) you|which one"
    r"|your call|up to you"
    r"|your decision|for you to decide|yours to decide|you decide|let me know (?:which|if"
    r"|whether|how|what)|tell me (?:which|if|whether|how|what)|say which"
    r"|your choice|options?:)\b",
    re.I,
)
#: A labelled option at the start of a line or list item: ``D1``, ``R12``, ``Option B``,
#: ``(a)``. Two different labels make a set of options.
_LABEL = re.compile(
    r"(?m)^[ \t]*(?:[-*+] |\d+[.)] |\|[ \t]*)?(?:\*\*|__)?"
    r"(\(?[A-Z]{1,2}\d{1,2}\)?|[Oo]ption [A-Z0-9]+|\(?[a-e]\))(?:\*\*|__)?[ \t]*[:.)\-—|]"
)


def _prose(text: str) -> str:
    return _NOT_PROSE.sub(" ", text)


def person_cue(words: str) -> bool:
    """Whether the person's words contain a phrase that opens a choice. A question mark
    alone is not one (KYO-859)."""
    prose = _prose(words)
    return bool(_PERSON_PHRASES.search(prose))


def reply_cue(reply: str) -> bool:
    """Whether the agent's final message puts a choice to the person: a recommendation or a
    request for their choice, or two or more labelled options. A closing question is not one:
    it is as often a go-ahead question ("Shall I build one now?") as a choice (KYO-876)."""
    prose = _prose(reply)
    if _REPLY_PHRASES.search(prose):
        return True
    return len({label.strip("()").lower() for label in _LABEL.findall(prose)}) >= 2


def detect(person: tuple[str, ...] | list[str], reply: str | None, asked: bool) -> list[str]:
    """The cues that fire for one turn, in the order the message names them."""
    cues: list[str] = []
    if any(person_cue(words) for words in person):
        cues.append(CUE_PERSON)
    if reply and reply_cue(reply):
        cues.append(CUE_REPLY)
    if asked:
        cues.append(CUE_ASKED)
    return cues


def _record_ids(prefix: str, text: str) -> set[str]:
    return set(re.findall(rf"\b{re.escape(prefix)}-\d+\b", text))


def open_discussions(store: Store, session: str) -> list[dict]:
    """The discussions opened in ``session`` that no reference has closed and that are not
    void, each with the record ids it is about: those its title, description and notes
    name and those it is linked with."""
    ladder = _load_status_ladder(store)
    prefix = store.config.project_prefix
    out: list[dict] = []
    for row in store.conn.execute(
        "SELECT r.id, r.title, r.description, r.status FROM records r"
        " WHERE r.kind = ? AND EXISTS (SELECT 1 FROM turn_refs t WHERE t.record_id = r.id"
        " AND t.role = ? AND t.session = ?) AND NOT EXISTS (SELECT 1 FROM turn_refs c"
        " WHERE c.record_id = r.id AND c.role = ?) ORDER BY r.seq",
        (DISCUSSION_KIND, ROLE_OPENED, session, ROLE_CLOSED),
    ):
        if ladder.is_terminal(row["status"]):
            continue
        about = _record_ids(prefix, f"{row['title']}\n{row['description'] or ''}")
        for note in store.conn.execute("SELECT text FROM notes WHERE record_id = ?", (row["id"],)):
            about |= _record_ids(prefix, note["text"] or "")
        for link in store.conn.execute(
            "SELECT src_id, dst_id FROM links WHERE src_id = ? OR dst_id = ?",
            (row["id"], row["id"]),
        ):
            about |= {link["src_id"], link["dst_id"]}
        about.discard(row["id"])
        out.append({"id": row["id"], "title": row["title"], "about": about})
    return out


def _index_line(ctx: Context, session: str, prompt_id: str | None) -> dict | None:
    if prompt_id is None:
        return None
    for line in prompt_lines(ctx.root, session):
        if line.get("prompt_id") == prompt_id:
            return line
    return None


def check_turn(ctx: Context, store: Store, payload: dict) -> dict:
    """Decide one Stop. Returns ``{"fire": bool, "why": str, ...}`` and, when it fires,
    ``message``. Reads only; raises what the store, the index or the transcript raise."""
    if payload.get("hook_event_name") != "Stop":
        return {"fire": False, "why": "not a Stop event"}
    if payload.get("stop_hook_active"):
        return {"fire": False, "why": "this stop already follows a Stop hook's feedback"}
    if not transcripts_opted_in(store.config):
        return {"fire": False, "why": "the project has not opted in to reading transcripts"}
    if ctx.layout is not None and ctx.layout.store_via_main_checkout is not None:
        return {"fire": False, "why": "a worktree's session is not the orchestrator's"}
    session = payload.get("session_id")
    raw_path = payload.get("transcript_path")
    if not isinstance(session, str) or not session or not isinstance(raw_path, str):
        return {"fire": False, "why": "no session_id or transcript_path in the hook payload"}
    wanted = payload.get("prompt_id")
    turn = transcript.read_turn(
        Path(raw_path).expanduser(), wanted if isinstance(wanted, str) else None
    )
    if turn is None:
        return {"fire": False, "why": "the transcript does not hold this turn"}
    last = payload.get("last_assistant_message")
    reply = last if isinstance(last, str) and last.strip() else turn.reply
    cues = detect(turn.person, reply, turn.asked)
    if not cues:
        return {"fire": False, "why": "no cue fired", "prompt_id": turn.prompt_id}
    result: dict = {"fire": False, "cues": cues, "prompt_id": turn.prompt_id}
    line = _index_line(ctx, session, turn.prompt_id)
    if line is not None and isinstance(line.get("skip"), dict):
        return result | {"why": "the turn is skipped with a recorded reason"}
    if turn.prompt_id is not None and _attached(store, session, turn.prompt_id):
        return result | {"why": "a discussion names this turn"}
    discussions = open_discussions(store, session)
    named = set().union(
        *(_record_ids(store.config.project_prefix, _prose(words)) for words in turn.person)
    )
    covered = set().union(*(d["about"] | {d["id"]} for d in discussions))
    if discussions and (CUE_PERSON not in cues or not named or named & covered):
        return result | {"why": f"{discussions[-1]['id']} is open in this session"}
    if line is None:
        return result | {"why": "the turn index holds no line for this turn"}
    if discussions:
        state = (
            f"it names {', '.join(sorted(named))}, which no discussion open in this session "
            f"is about ({', '.join(d['id'] for d in discussions)} open)"
        )
    else:
        state = "no discussion is open in this session"
    key = line.get("prompt_id") or line.get("turn")
    recorded = mark_candidate(ctx.root, session, key, cues)
    return result | {"fire": True, "why": state, "candidate": recorded}


def _attached(store: Store, session: str, prompt_id: str) -> bool:
    return (
        store.conn.execute(
            "SELECT 1 FROM turn_refs WHERE session = ? AND prompt_id = ?", (session, prompt_id)
        ).fetchone()
        is not None
    )


def prompt_line(line: dict) -> str:
    """The one line of context the next prompt carries for a candidate turn."""
    prompt_id = line["prompt_id"]
    cues = "; ".join(line["candidate"].get("cues") or [])
    return (
        f"Kyosei: the turn at {line.get('at') or 'an unknown time'} (prompt {prompt_id[:8]}) may "
        f"have put a choice to the person ({cues}). If it did, run `kyo discuss open --at-prompt "
        f'{prompt_id} --title "<topic>"`; if not, run `kyo discuss skip --at-prompt {prompt_id} '
        '--why "<one line>"`. Say nothing to the person about a skip. This is named once.'
    )


def next_prompt(ctx: Context, store: Store, payload: dict) -> dict:
    """UserPromptSubmit: the line naming the session's newest unanswered candidate, marked
    asked, or ``{"line": None, ...}`` with the reason there is none."""
    if not transcripts_opted_in(store.config):
        return {"line": None, "why": "the project has not opted in to reading transcripts"}
    if ctx.layout is not None and ctx.layout.store_via_main_checkout is not None:
        return {"line": None, "why": "a worktree's session is not the orchestrator's"}
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return {"line": None, "why": "no session_id in the hook payload"}
    found = next_candidate(
        ctx.root, session, lambda prompt_id: _attached(store, session, prompt_id)
    )
    if found is None:
        return {"line": None, "why": "no candidate turn is waiting"}
    return {
        "line": prompt_line(found),
        "why": "a candidate turn is named",
        "prompt_id": found["prompt_id"],
    }


def _check(ctx: Context, args: Namespace) -> Output:
    if not getattr(args, "hook", False):
        raise UsageError(
            "check reads Claude Code's hook payload on stdin: run it as `kyo discuss check "
            "--hook`, which is how the packaged Stop and UserPromptSubmit hooks run it"
        )
    lines: list[str] = []
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("hook_event_name") == "UserPromptSubmit":
            data = next_prompt(ctx, ctx.store(), payload)
            if data["line"] is not None:
                lines = [data["line"]]
            data = {
                "fire": data["line"] is not None,
                **{k: v for k, v in data.items() if k != "line"},
            }
        else:
            data = check_turn(ctx, ctx.store(), payload)
    except Exception as exc:  # noqa: BLE001 - a hook that fails must say nothing
        data = {"fire": False, "why": f"could not check: {exc}", "problem": str(exc)}
    return Output(lines, {"ok": True, "command": "discuss check", **data})


def _skip(ctx: Context, store: Store, args: Namespace) -> Output:
    if args.id is not None:
        raise UsageError("skip takes no id: it marks a turn, named with --at-prompt")
    why = getattr(args, "why", None)
    if not (why or "").strip():
        raise UsageError('skip records why the turn is not a discussion: give --why "<one line>"')
    why = single_line(why, what="--why")
    session = _session_of(ctx)
    if session is None:
        raise UsageError(
            f"skip marks a turn of this Claude Code session, and ${ENV_SESSION} is unset"
        )
    if ctx.layout is not None and ctx.layout.store_via_main_checkout is not None:
        raise UsageError(
            "skip writes the turn index, which a worktree's kyo never does; the Stop check "
            "does not ask in a worktree, so there is nothing to skip here"
        )
    if not transcripts_opted_in(store.config):
        raise UsageError(
            "skip writes its reason on the turn's line in the turn index, and this project "
            f"keeps none (`{READ_TRANSCRIPTS_KEY}:` in kyosei/config.yml); the Stop check "
            "does not ask in such a project, so there is nothing to skip here"
        )
    _refresh_index(ctx, session)
    lines = sorted(
        (line for line in prompt_lines(ctx.root, session) if isinstance(line.get("at"), str)),
        key=lambda line: line["at"],
    )
    at = getattr(args, "at", None)
    prompts = [line for line in lines if isinstance(line.get("prompt_id"), str)]
    if at is not None:
        line = _match_at(lines, at, session)
    elif prompts:
        line = prompts[-1]
    else:
        raise UsageError(f"the turn index holds no prompt of session {session} to skip")
    key = line.get("prompt_id") or line.get("turn")
    written = mark_skipped(ctx.root, session, key, why)
    if written is None:
        raise KyoseiError(f"the turn index no longer holds {key} of session {session}")
    return Output(
        [f"skipped prompt {key} at {written['at']}", echo_value("why", why)],
        {
            "ok": True,
            "command": "discuss skip",
            "session": session,
            "prompt_id": written.get("prompt_id"),
            "turn": written.get("turn"),
            "skip": written["skip"],
        },
    )


def cmd_discuss(ctx: Context, args: Namespace) -> Output:
    """``kyo discuss open | note | close | skip | check --hook``."""
    action = args.action
    if action == "check":
        return _check(ctx, args)
    if getattr(args, "hook", False):
        raise UsageError("--hook belongs to `kyo discuss check`")
    store = ctx.store()
    if action == "skip":
        return _skip(ctx, store, args)
    if action == "open":
        return _open(ctx, store, args)
    if action == "note":
        return _note(ctx, store, args)
    return _close(ctx, store, args)
