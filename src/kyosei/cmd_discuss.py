"""``kyo discuss``: a discussion opened when it starts, at a turn the index stamped (KYO-658).

Decision KYO-654 adopted DC2 and DC3 of the decision-capture proposal (KYO-651, sections
5.1 Tiers 1 and 3, in the store repository). A DISCUSSION is a record of the ``discussion``
kind (:data:`kyosei.kinds.DISCUSSION_KIND`): knowledge archetype, not counted, swept. Its
life is its turn references (``turn_refs``, ``KYO-n.sN``), each naming one of the person's
turns as the turn index (:mod:`kyosei.cmd_turns`) recorded it:

* ``kyo discuss open --title "<topic>"`` files one with an ``opened`` reference to the
  latest confirmed person turn of ``$CLAUDE_CODE_SESSION_ID``. ``--at <prompt>`` names an
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
none, ``--opened-late`` opens one in the same transaction, at the turn ``--at`` names, and
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
from .contracts import TURN_REF_ABSENT, TURN_REF_INDEXED
from .credits import ENV_SESSION, enforced
from .errors import ContractViolation, KyoseiError, NotFound, UsageError
from .harness import claude_code_transcript as transcript
from .kinds import DISCUSSION_KIND
from .prose import read_prose, single_line
from .render import echo_block, echo_value, lag_seconds, turn_ref_lines
from .store import Store

CONTRACT_DECISION_REQUIRES_DISCUSSION = "decision-requires-discussion"

ROLE_OPENED = "opened"
ROLE_NOTE = "note"
ROLE_CLOSED = "closed"

#: How many of the session's confirmed turns a refusal over ``--at`` lists.
LISTED_TURNS = 5

#: The shortest ``--at`` prefix accepted, so a one-character typo matches nothing.
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
            f"--at names a turn of the turn index, and there is none to name ({stamp.why}); "
            "drop --at, and the opening is recorded with index_state absent"
        )
    return stamp


def _match_at(lines: list[dict], at: str, session: str) -> dict:
    exact = [line for line in lines if at in (line.get("prompt_id"), line.get("turn"))]
    if len(exact) == 1:
        return exact[0]
    found = (
        [
            line
            for line in lines
            if any(isinstance(v, str) and v.startswith(at) for v in (line.get("prompt_id"), line.get("turn")))
        ]
        if len(at) >= MIN_AT_PREFIX
        else []
    )
    if len(found) == 1:
        return found[0]
    what = "matches several" if found else "matches none"
    raise UsageError(
        "\n".join(
            [
                f"--at {at!r} {what} of the person's confirmed turns in session {session}; "
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
        done["closed_ref"] = close_on(
            ctx, store, discussion_id, self.current, produced_id=record_id, created_at=stamp
        )
        return done

    def record_ids(self) -> list[str]:
        """The existing discussion this filing changes, for the events it writes."""
        return [self.discussion_id] if self.discussion_id and not self.late else []


def add_filing_flags(parser) -> None:
    """``--discussion``, ``--opened-late`` and ``--at`` on `kyo new` and `kyo spawn`."""
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
        "good. With a turn index, --at names the person's turn it began at",
    )
    parser.add_argument(
        "--at",
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
    given = getattr(args, "discussion", None)
    late = bool(getattr(args, "opened_late", False))
    at = getattr(args, "at", None)
    if not kind_def.requires_discussion:
        flags = [f for f, v in (("--discussion", given), ("--opened-late", late), ("--at", at)) if v]
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
        raise UsageError("--at goes with --opened-late here: it names where the late discussion began")
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
                    f"add --at <prompt>, a prompt id from the index. The latest of the "
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
            "--at <prompt> where the session has a turn index), which opens one now and marks "
            "it late for good",
        ],
    )


def filing_lines(store: Store, done: dict | None) -> list[str]:
    """What a filing echoes about its discussion."""
    if not done:
        return []
    discussion_id = done["discussion_id"]
    refs = _refs(store, discussion_id)
    out = [f"discussion: {discussion_id}" + (" (opened late, at this filing)" if done["late"] else "")]
    if done["late"]:
        opened = discussion_state(refs)["opened"]
        out += [f"  {line}" for line in turn_ref_lines([opened])]
    if done.get("link_id"):
        out.append(f"  link: {done['link_id']}  produced-by {discussion_id}")
    if done.get("closed_ref"):
        out.append(f"  closed: {done['closed_ref']} (spawning is not a status change)")
    return out


# ------------------------------------------------------------------ the command


def _receipt(store: Store, record_id: str, head: str) -> list[str]:
    refs = _refs(store, record_id)
    return [head, *(f"  {line}" for line in turn_ref_lines(refs))]


def _open(ctx: Context, store: Store, args: Namespace) -> Output:
    if args.id is not None:
        raise UsageError("`kyo discuss open` takes no id; it files a new discussion")
    if not getattr(args, "title", None):
        raise UsageError('`kyo discuss open` needs --title "<topic>"')
    title = single_line(args.title, what="--title")
    stamp = current_stamp(ctx, store, getattr(args, "at", None))
    with store.transaction():
        created = now()
        record_id, ref_id = file_discussion(ctx, store, title, stamp, late=False, created_at=created)
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
        raise UsageError("`kyo discuss note <discussion-id> --file FILE`")
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
            "note_id": note_id,
            "turn_ref": ref_id,
            "record": after[args.id],
        },
    )


def _close(ctx: Context, store: Store, args: Namespace) -> Output:
    if args.id is None:
        raise UsageError('`kyo discuss close <discussion-id> --dropped --why "<reason>"`')
    if not getattr(args, "dropped", False):
        raise UsageError(
            "a discussion closes by spawning what it produced (`kyo spawn <id> --kind "
            "decision`, proposal, plan or task); `kyo discuss close` is for one that produced "
            'nothing, and says so with --dropped --why "<reason>"'
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
            "turn_ref": ref_id,
            "record": after[args.id],
        },
    )


def cmd_discuss(ctx: Context, args: Namespace) -> Output:
    """``kyo discuss open | note | close``."""
    store = ctx.store()
    action = args.action
    if action == "open":
        return _open(ctx, store, args)
    if action == "note":
        return _note(ctx, store, args)
    return _close(ctx, store, args)
