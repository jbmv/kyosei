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

A DISCUSSION IS OPTIONAL (KYO-899, decision KYO-898). KYO-658 (DC3) required a decision
to be filed from a discussion, under ``decision-requires-discussion``, and that rule is
retired: ``kyo new`` and ``kyo spawn`` file any kind with no discussion, whatever a store's
``kinds.yml`` says for ``policy.requires_discussion``, which is still accepted there and
read by nothing. A discussion is one somebody opened on purpose. A record spawned from it,
or filed with ``--discussion``, is linked to it and closes it (:func:`filing_discussion`);
``--opened-late`` still opens one in the filing's own transaction, at the turn
``--at-prompt`` names, with its ``opened`` reference marked ``late`` for good. A kind that
takes a credit (``policy.requires_credit``, the shipped ``decision``) spawned from a record
that itself came from a discussion is filed from that discussion as well, which is where
its credit's span comes from.

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

THE STOP CHECK IS RETIRED (KYO-899). KYO-662 and KYO-876 ran ``kyo discuss check --hook`` at
every Stop and every UserPromptSubmit: it looked for cues that a choice had been put to the
person, marked the turn a candidate on its index line and named it at the next prompt, and
the agent then opened a discussion or dismissed it with ``kyo discuss skip``. Decision
KYO-898 dropped it. The packaged hooks no longer run it, and the cue detection, the
candidate marks and doctor's weekly shares are removed. The two verbs stay in the parser
because a project's own copy of the hooks may still call them: ``check --hook`` exits 0,
prints nothing and writes nothing, and ``skip`` exits 0, writes nothing and says that the
check is retired. An index line written by an earlier build may still carry ``candidate``
or ``skip``; nothing reads either.
"""

from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path

from . import commands, export
from .clock import now
from .cmd_sessions import recorded_transcript_path
from .cmd_turns import HUMAN, confirm_from_transcript, read_lines
from .commands import Context, Output, _load_registry, _load_status_ladder, _touch_record
from .config import READ_TRANSCRIPTS_KEY, transcripts_opted_in
from .contracts import (
    TURN_REF_ABSENT,
    TURN_REF_DROPPED,
    TURN_REF_INDEXED,
    TURN_REF_PRODUCED,
)
from .credits import ENV_SESSION
from .errors import ContractViolation, KyoseiError, NotFound, UsageError
from .harness import claude_code_transcript as transcript
from .kinds import DISCUSSION_KIND
from .prose import read_prose, single_line
from .receipt import detail
from .render import echo_block, echo_value, turn_ref_lines
from .store import Store

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
    outcome: str,
    produced_id: str | None = None,
    why: str | None = None,
    created_at: str,
) -> str | None:
    """Write the ``closed`` reference, inside the caller's transaction, unless the
    discussion is already closed. Returns the reference id, or None.

    ``outcome`` is ``produced`` or ``dropped`` (KYO-882) and is stored as given; ``why``
    is the person's reason as they gave it, under either outcome."""
    if not discussion_state(_refs(store, discussion_id))["open"]:
        return None
    ref_id = store.add_turn_ref(
        discussion_id,
        role=ROLE_CLOSED,
        author=ctx.actor,
        outcome=outcome,
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
                ctx,
                store,
                discussion_id,
                self.current,
                outcome=TURN_REF_PRODUCED,
                produced_id=record_id,
                created_at=stamp,
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
        help="the discussion this record came from, which filing it closes unless "
        "--keep-open is given; optional for every kind (KYO-658, KYO-899)",
    )
    parser.add_argument(
        "--opened-late",
        action="store_true",
        help="open a discussion now, in the same act, marked late for good, for a matter "
        "that was talked through with none open. With a turn index, --at-prompt names the "
        "person's turn it began at",
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
    """The discussion a filing of ``kind_def`` names, or None when it names none. No kind
    needs one (KYO-899): ``policy.requires_discussion`` is read by nothing, and the flags
    are accepted on every kind."""
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
    source_is_discussion = (
        spawn_source is not None and store.record(spawn_source)["kind"] == DISCUSSION_KIND
    )
    if source_is_discussion:
        assert spawn_source is not None
        _refuse_closed_source(store, spawn_source)
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
    if source_is_discussion:
        from_source = spawn_source
    elif spawn_source is not None and kind_def.requires_credit:
        # The discussion behind the record this one is spawned from, for the kind whose
        # filing carries a decided credit: that credit's span is read from it.
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
            via_source=source_is_discussion,
            refs=_refs(store, discussion_id),
        )
    if not late:
        return None
    opening = current_stamp(ctx, store, at)
    if opening.indexed and at is None:
        lines = person_turns(ctx, store, opening.session or "")
        raise UsageError(
            "\n".join(
                [
                    "--opened-late opens the discussion at the person's turn it began at, and "
                    f"session {opening.session} has a turn index, so `{command}` names that "
                    "turn: nothing was written. Add --at-prompt <prompt>, a prompt id from "
                    f"the index. The latest of the person's {len(lines)} confirmed turns:",
                    *_turn_listing(lines),
                ]
            )
        )
    current = current_stamp(ctx, store)
    ref = {"role": ROLE_OPENED, "late": True, **opening.store_fields()}
    return FilingDiscussion(current, late=True, opening=opening, refs=[ref])


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


def _ref_lines(refs: list[dict], *, keep: int = 0) -> list[str]:
    """``turn_ref_lines`` of ``refs``, indented. KYO-880: a reference's lines restate
    what `kyo show` prints of the discussion, so a receipt leaves them out, except the
    last ``keep`` references, and any reference the index stamped late or not at all,
    which is a warning about the turn it names."""
    out: list[str] = []
    for position, ref in enumerate(refs):
        flagged = bool(ref.get("late")) or ref.get("index_state") != "indexed"
        kept = flagged or position >= len(refs) - keep
        for line in turn_ref_lines([ref]):
            out.append(f"  {line}" if kept else detail(f"  {line}"))
    return out


def _receipt(store: Store, record_id: str, head: str, *, keep: int = 0) -> list[str]:
    return [head, *_ref_lines(_refs(store, record_id), keep=keep)]


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
        detail(echo_value("kind", DISCUSSION_KIND)),
        echo_value("title", title),
        *_receipt(store, record_id, "turn references:")[1:],
    ]
    return Output(
        lines,
        receipt=True,
        data={
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
        *_ref_lines([export._turn_ref(store.turn_refs(args.id)[-1])]),
    ]
    return Output(
        lines,
        receipt=True,
        data={
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
        raise UsageError(
            "close names the discussion: give its id, and --produced RECORD_ID [RECORD_ID ...] "
            'or --dropped --why "<reason>"'
        )
    dropped = bool(getattr(args, "dropped", False))
    produced = getattr(args, "produced", None)
    if dropped and produced is not None:
        raise UsageError(
            "--dropped says nothing was produced and --produced says what was: give one"
        )
    if not dropped and produced is None:
        raise UsageError(
            "a discussion closes when a decision, proposal, plan or task is spawned from it; "
            "close is for one that led elsewhere, and says what with --produced RECORD_ID "
            '[RECORD_ID ...] (--why for what is not a record), or that produced nothing, '
            'with --dropped --why "<reason>"'
        )
    why = getattr(args, "why", None)
    if dropped:
        if not (why or "").strip():
            raise UsageError(
                "--dropped records that nothing was produced; the reason goes in --why"
            )
    elif why is not None and not why.strip():
        raise UsageError("--why is empty: give what the discussion produced, or leave it out")
    why = single_line(why, what="--why") if why else None
    produced = list(dict.fromkeys(produced or []))
    if not dropped and not produced and not why:
        raise UsageError(
            "--produced names the records the discussion led to; give one or more, or --why "
            "for what is not a record, such as an idea file"
        )
    require_discussion(store, args.id)
    if not discussion_state(_refs(store, args.id))["open"]:
        raise UsageError(f"{args.id} is already closed")
    for record_id in produced:
        if record_id == args.id:
            raise UsageError(f"{args.id} cannot have produced itself")
        store.record(record_id)  # an unknown id refuses here, before anything is written
    stamp = current_stamp(ctx, store, getattr(args, "at", None))
    touched = [args.id, *produced]
    with store.transaction():
        before = {rid: export.record_document(store, rid) for rid in touched}
        created = now()
        links = [_link(store, rid, args.id, created) for rid in produced]
        outcome = TURN_REF_DROPPED if dropped else TURN_REF_PRODUCED
        ref_id = close_on(
            ctx,
            store,
            args.id,
            stamp,
            outcome=outcome,
            produced_id=produced[0] if produced else None,
            why=why,
            created_at=created,
        )
        after, events = commands._commit_and_export(
            store,
            command="discuss close",
            actor=ctx.actor,
            record_ids=touched,
            before=before,
        )
    commands._finish_mutation(store, touched, events)
    if dropped:
        head = f"closed {args.id} with nothing produced"
    elif produced:
        head = f"closed {args.id}, produced {', '.join(produced)}"
    else:
        head = f"closed {args.id}, produced {why}"
    # KYO-880: the closing reference stays, with what was produced and each link; the
    # discussion's earlier turns are `kyo show`'s.
    lines = _receipt(store, args.id, head, keep=1)
    lines += [
        f"  link: {link}  {rid} produced-by {args.id}"
        for rid, link in zip(produced, links, strict=True)
        if link
    ]
    return Output(
        lines,
        receipt=True,
        data={
            "ok": True,
            "command": "discuss close",
            "id": args.id,
            "record_id": args.id,
            "turn_ref": ref_id,
            "outcome": outcome,
            "produced": produced,
            "record": after[args.id],
        },
    )


# ------------------------------------------------------- the retired Stop check (KYO-899)

#: What ``kyo discuss skip`` prints. One line, so a hook copy that runs it adds one line.
SKIP_RETIRED = (
    "discuss skip: the discussion check is retired (KYO-899), so no turn waits to be "
    "skipped; nothing was written"
)


def _check() -> Output:
    """``kyo discuss check [--hook]``: retired. It reads nothing, not even the payload on
    stdin, prints nothing and writes nothing, so a project's older hook copy that still
    runs it at Stop or at UserPromptSubmit adds nothing to the session."""
    return Output([], {"ok": True, "command": "discuss check", "fire": False, "retired": True})


def _skip() -> Output:
    """``kyo discuss skip``: retired with the check. Whatever flags it was given, it writes
    nothing to the turn index or the store and says so in one line."""
    return Output([SKIP_RETIRED], {"ok": True, "command": "discuss skip", "retired": True})


def cmd_discuss(ctx: Context, args: Namespace) -> Output:
    """``kyo discuss open | note | close``, and the retired ``skip`` and ``check --hook``."""
    action = args.action
    if action == "check":
        return _check()
    if action == "skip":
        return _skip()
    if getattr(args, "hook", False):
        raise UsageError("--hook belongs to `kyo discuss check`")
    store = ctx.store()
    if action == "open":
        return _open(ctx, store, args)
    if action == "note":
        return _note(ctx, store, args)
    return _close(ctx, store, args)
