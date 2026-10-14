"""``kyo next`` — what is ready, what is finished but not moved, what waits on a person.

Also the predicates other commands share with it: :func:`settled_not_moved_entry`
(``kyo dispatch`` refuses a finished record on it) and :func:`ungated_unobserved`
(``kyo verify``, ``kyo gate``, ``kyo accept`` and ``kyo doctor`` read it).
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import commands, kinds
from .commands import Context, Output, _load_registry, _load_status_ladder
from .contracts import (
    DISPATCH_STATE_REPORTED,
    GATE_TYPES,
    ROLE_VERIFIED,
    SETTLED_CRITERION_STATES,
    TOUCH_COLLIDES,
    TOUCH_SCOPE_UNKNOWN,
    StatusLadder,
    scope_unknown_detail,
)
from .render import next_text
from .store import Store


@dataclass(frozen=True)
class _Readiness:
    """What one pass over the board found, split by what each class actually needs.

    Three lists rather than three separate queries, because `kyo next` and `kyo
    brief` must not be able to disagree about a record (KYO-87.c3). They do not
    each compute readiness — they call :func:`_ready_records` once and render the
    same three lists, so a record's class is decided in one place and read in two.
    """

    ready: list[dict]
    scope_unknown: list[dict]
    settled_not_moved: list[dict]
    #: KYO-570: records held back by an incoming ``blocks`` edge whose source is not
    #: settled. Each row carries ``blockers`` (id, status, title per unsettled source).
    blocked: list[dict]


def _reported_dispatches_by_record(store: Store) -> dict[str, list[str]]:
    """record id -> the ids of the dispatches that formally reported work on it.

    KYO-87.c2: a record is covered by a dispatch either as its primary record or
    through ``covers_json`` (``--also-covers``, KYO-13.c4), and both are formal
    declarations that this dispatch's report speaks for that record. Reading only
    the primary would leave every covered record — the ones a controller is most
    likely to forget to move, because they were never the subject of the dispatch
    — in exactly the class this record exists to empty.

    ``reported`` only. ``terminated-no-report`` is deliberately excluded: it is its
    own loud state precisely because nothing was reported (contracts:
    DISPATCH_STATE_TERMINATED_NO_REPORT), and treating it as evidence that work
    finished would be the "success without evidence" shape law 6 rules out.
    ``withdrawn`` (KYO-345) is excluded by that same sentence and even more plainly:
    a dispatch nobody was ever handed has not merely lost its report, it never had
    anyone to write one. Note the SQL asks for ``reported`` rather than for "not
    running" — a positive test, which is why adding a fourth state changed no
    behaviour here and why adding a fifth will not either.
    """
    out: dict[str, list[str]] = {}
    for row in store.conn.execute(
        "SELECT id, record_id, covers_json FROM dispatches WHERE state = ? ORDER BY seq",
        (DISPATCH_STATE_REPORTED,),
    ):
        for record_id in (row["record_id"], *commands.dispatch_covers(row)):
            out.setdefault(str(record_id), []).append(row["id"])
    return out


def _settles_class(ladder: StatusLadder, status: str) -> str:
    """The status a finished-but-not-moved record must reach for the class to let go.

    KYO-173. The class is *derived* — a dispatch that speaks for the record reported,
    and every criterion is settled — and the derivation never asks what status the
    record is in now. So the remedy it prints has to, or it names a move the record has
    already made: `kyo move X verified` on a record at `verified` succeeds, prints
    ``verified -> verified``, fires no contract, and leaves the row exactly where it
    was — a permanent obstruction, since the skill makes emptying the class a
    precondition for dispatching.

    Keyed on roles rather than names (KYO-101): a project may rename every rung, and
    the remedy must still name that project's own word. A ladder that declares no
    verified rung at all falls through to the completion status, which is then the only
    move left.
    """
    if ladder.role(status) == ROLE_VERIFIED:
        return ladder.complete
    verified = ladder.with_role(ROLE_VERIFIED)
    return verified[0] if verified else ladder.complete


def settled_not_moved_entry(
    ladder: StatusLadder,
    row: Mapping,
    criteria: Sequence[Mapping],
    reported_by: Sequence[str],
) -> dict | None:
    """The finished-but-not-moved row for one record, or ``None`` if it is not in
    that class. The whole of the class's definition, in one place.

    KYO-295: one question, asked once, read by every surface that acts on it. `kyo
    next`/`kyo brief` list the class off this predicate and `kyo dispatch` refuses off
    it, rather than each re-deriving "is this finished?" from criteria states and
    getting to disagree (docs/design/review.md#kyo-295).

    Pure, and takes what it judges rather than a store: :func:`_ready_records` is
    already walking every record with the criteria and the reporter list in hand,
    and re-reading them here would put the per-record cost back on the walk. The
    price is that both callers must read the same three things, which is why the
    parameters are named for exactly what the class is derived from.

    The two boundary rules are KYO-87's and KYO-289's, unchanged and stated here
    because this is now the only place they are written:

    * criteria must be PRESENT and all settled -- "no unsettled criteria" must not
      be satisfiable by the empty set, so a record with no criteria is never in this
      class however finished it looks;
    * whether a dispatch reported is a fact BESIDE the class, never a condition of
      it (KYO-289): work settled in-session or by the maintainer by hand has no
      dispatch to report, and the criteria are the record's own statement of done.

    A record already at a settled status is not in the class either -- it has been
    moved, which is the whole remedy -- and that test is inside this function rather
    than at the callers on purpose: it is the condition under which the class LETS
    GO, and a caller that forgot it would refuse a dispatch for a record whose status
    says the class is finished with it.
    """
    if ladder.is_settled(row["status"]):
        return None
    unsettled = [c for c in criteria if c["state"] not in SETTLED_CRITERION_STATES]
    if not criteria or unsettled:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "title": row["title"],
        "reported_by": list(reported_by),
        # Named, not guessed — and named against the ladder rather than against a
        # word (KYO-173). The remedy has to be the move that actually *clears this
        # class*, and which move that is depends on where the record already stands:
        # a record still short of the verified rung needs that rung, but one already
        # carrying `verified` has only the completion move left, and naming
        # `verified` there is a no-op that prints "moved" and leaves the record in
        # the list. Read off roles, never off names, for KYO-101's reason: a project
        # may call these rungs anything.
        "needs": f"kyo move {row['id']} {_settles_class(ladder, row['status'])}",
    }


#: KYO-570: the one edge type that holds a record back. ``src blocks dst`` is a
#: start-order dependency (KYO-21's advice: record it before dispatching), so ``dst``
#: is not offered by `kyo next` or the brief, and `kyo dispatch` refuses it without
#: ``--force``, until every such ``src`` is settled on this project's status ladder.
#: Only INCOMING edges count, and no other edge type does.
BLOCKING_EDGE = "blocks"


def unsettled_blockers(store: Store, ladder: StatusLadder, record_id: str) -> list[dict]:
    """Every record that ``blocks`` ``record_id`` and is not settled, in link order
    (KYO-570).

    One function for `kyo next`, the brief and `kyo dispatch`, for KYO-87.c3's reason:
    the surface that withholds a record and the surface that hands it out must not be
    able to disagree. "Settled" is :meth:`StatusLadder.is_settled` — the ladder's
    complete/retired roles — never the literal names ``done``/``wont-do`` (KYO-101).
    A gated blocker is unsettled: a gate does not move a status.
    """
    rows = store.conn.execute(
        "SELECT l.id AS link_id, r.id, r.status, r.title FROM links l "
        "JOIN records r ON r.id = l.src_id "
        "WHERE l.dst_id = ? AND l.edge_type = ? ORDER BY l.seq",
        (record_id, BLOCKING_EDGE),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "status": row["status"],
            "title": row["title"],
            "link_id": row["link_id"],
        }
        for row in rows
        if not ladder.is_settled(row["status"])
    ]


def _ready_records(store: Store, registry: kinds.KindRegistry) -> _Readiness:
    """KYO-8.c5 / this dispatch's task brief: "kyo next consumes: kind policy
    dispatchable, gates, criteria presence, touch-disjointness vs running
    dispatches." A record already carrying a running dispatch of its own is
    not "ready" — it is already being worked.

    Returns ``(ready, scope_unknown)``. KYO-48: the second list holds the records
    that pass every other readiness test and whose disjointness *could not be
    computed*, because a record in the comparison declares no touches. They are
    not ready — an undeclared scope is not an independent one — and they are not
    dropped either: silently omitting them would answer one law-6 failure with
    another, and a genuinely independent record (the docs-only chore during a
    code dispatch) has to stay reachable. The caller renders them under their own
    heading with their own count, and `kyo dispatch` refuses them off the same
    call (:func:`commands.touch_standing`), so neither surface can call them safe
    while the other calls them unsafe.

    KYO-65: a running dispatch is compared as the union of its primary record's
    touches and every record it formally covers (``--also-covers``), re-read here
    on every call. That is what makes a record whose real editor is a *covered*
    record stop being offered as ready — including the covered record itself,
    which now collides with the dispatch doing its work instead of being listed
    as available. Candidates carry no ``also_covers`` of their own: a record with
    no dispatch yet has not declared that it covers anything.

    KYO-87: readiness is not a question about ``status`` alone. The third list is
    finished work nobody moved: a record with no unsettled criterion is not
    dispatchable whatever its status says, and what it needs is named — the status
    move, which stays the controller's act (a move fires transition contracts
    and nothing here fires them for anybody). See docs/design/review.md#kyo-87.

    The boundary is kept sharp on purpose. A record whose dispatch reported and
    whose criteria are NOT all settled stays in ``ready``: a worker reporting
    ``unmet-unbuilt`` verdicts is a record that genuinely needs dispatching again.
    Those rows carry ``prior_dispatch`` instead, and both surfaces print it.

    KYO-289: the class does not ask whether a dispatch reported. What makes it true
    is the criteria — the record's own statement of what "done" means, settled — and
    a dispatch is only the road the evidence may have arrived by; work settled
    in-session or by hand has none (docs/design/review.md#kyo-289). So ``reported``
    is a fact about the class, not a condition of it: ``reported_by`` is
    the list of dispatches that spoke for the record, which for a record nothing
    was dispatched against is empty (a list, never absent — law 6: the empty case
    is rendered, not elided), and the surfaces say "every criterion is settled"
    with the reporters named only when there are any.

    Two things that did NOT change, because they are the same boundary read from
    the other side: a record with criteria still unsettled stays in ``ready``
    whatever its dispatch history (carrying ``prior_dispatch`` when one reported),
    and a record with NO criteria is never in this class, because "no unsettled
    criteria" must not be satisfiable by an empty set (KYO-87's own guard).
    """
    running = store.running_dispatches()
    already_running_records = {row["record_id"] for row in running}
    reported_by = _reported_dispatches_by_record(store)

    ready: list[dict] = []
    scope_unknown: list[dict] = []
    settled_not_moved: list[dict] = []
    blocked: list[dict] = []
    # KYO-101: "settled" is the ladder's `complete`/`retired` roles, read off this
    # project's registry, not the two literal names v1 could assume.
    ladder = _load_status_ladder(store)
    for row in store.conn.execute("SELECT id, kind, status, title FROM records ORDER BY seq"):
        if ladder.is_settled(row["status"]) or row["id"] in already_running_records:
            continue
        kind_def = registry.get(row["kind"])
        if (
            kind_def is None
            or kind_def.archetype != "work"
            or not kind_def.policy.get("dispatchable", False)
        ):
            continue
        if store.conn.execute(
            "SELECT 1 FROM gates WHERE record_id = ? AND cleared_at IS NULL LIMIT 1",
            (row["id"],),
        ).fetchone():
            continue
        criteria = store.criteria(row["id"])
        if kind_def.requires_criteria and not criteria:
            continue
        item = {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "title": row["title"],
        }
        reported = reported_by.get(row["id"], [])
        unsettled = [c for c in criteria if c["state"] not in SETTLED_CRITERION_STATES]
        # KYO-87.c1/c2. Derived from the criteria, never from anyone having
        # remembered to set `status = built`: that status is what
        # `_awaiting_verification` keys on, and it is exactly the thing a
        # controller forgets. Checked before disjointness because a finished
        # record is not a candidate whose scope is worth comparing — it is not
        # being handed to anybody.
        #
        # KYO-295 moved the condition itself into `settled_not_moved_entry`, which
        # `kyo dispatch` now refuses off as well: this list and that refusal are one
        # question asked once, so the surface that withholds a record and the surface
        # that hands it out cannot disagree about whether it is finished.
        finished = settled_not_moved_entry(ladder, row, criteria, reported)
        if finished is not None:
            settled_not_moved.append(finished)
            continue
        if reported:
            # The other half of the boundary: worked, reported, and something is
            # still open. Genuinely dispatchable — and a controller about to hand it
            # out is owed the fact that somebody already did, which is the same
            # law-6 debt the brief ran up when it dropped `status`.
            item["prior_dispatch"] = (
                f"{', '.join(reported)} reported; "
                f"{len(unsettled)} of {len(criteria)} criteria unsettled"
            )
        # KYO-570: an unsettled `blocks` source holds this record back. Checked after
        # gates (a gated record is already withheld, and is not listed twice) and after
        # the finished class (a record with nothing left to build needs a status move,
        # not a wait), and before disjointness, because a record that may not start
        # yet is not a candidate whose scope is worth comparing.
        blockers = unsettled_blockers(store, ladder, row["id"])
        if blockers:
            item["blockers"] = blockers
            blocked.append(item)
            continue
        verdict, _collisions, unknown = commands.touch_standing(store, row["id"], running=running)
        if verdict == TOUCH_COLLIDES:
            continue
        if verdict == TOUCH_SCOPE_UNKNOWN:
            item["reason"] = "; ".join(scope_unknown_detail(u) for u in unknown)
            scope_unknown.append(item)
        else:
            ready.append(item)
    return _Readiness(
        ready=ready,
        scope_unknown=scope_unknown,
        settled_not_moved=settled_not_moved,
        blocked=blocked,
    )


# ------------------------------------------------------------------------- KYO-267
#
# A criterion recorded `unmet-unobserved` because a person has not looked yet is a
# wait, and only an open `awaiting:*` gate makes a wait FINDABLE: the desktop's
# Awaiting list, the brief's "gated work" section and `kyo list --gate-tag` all read
# gates and nothing else. An unobserved criterion with no gate appears on no surface a
# person opens. See docs/design/review.md#kyo-267.
#
# ONE PREDICATE, THREE SURFACES. `kyo verify`'s follow-up line, the brief/`kyo next`
# section and `kyo doctor`'s notice all call :func:`ungated_unobserved`, for the
# reason `commands.touch_standing` gives for living where it does: two surfaces
# disagreeing about what is safe is worse than either being wrong alone. A controller
# who is told at verify time that a record needs a gate, and then reads a brief that
# does not list it, learns to believe neither.
#
# The state is exact, and each clause is load-bearing:
#
#   * at least one criterion in `unmet-unobserved` — the wait itself;
#   * NO running dispatch (primary or `--also-covers`) — while a worker is out there,
#     `unmet-unobserved` is the honest state of a criterion that worker is about to
#     observe, and gating it would be premature;
#   * NO open gate of any `awaiting:*` type — the wait is already findable, which is
#     the whole remedy. A `holding-for` gate deliberately does NOT count, because the
#     Awaiting list does not read it either;
#   * a dispatchable `archetype: work` kind whose status is not settled — the same
#     boundary `_ready_records` draws, so the class names records the board is
#     actually still carrying.

#: KYO-267: the gate types that make a wait findable. Derived from
#: :data:`contracts.GATE_TYPES` rather than listed, so a fifth ``awaiting:*`` type
#: joins this set on the day it is declared and not on the day somebody remembers.
AWAITING_GATE_TYPES: frozenset[str] = frozenset(
    gate_type for gate_type in GATE_TYPES if gate_type.startswith("awaiting:")
)

#: KYO-267: the one the remedy names. An unobserved criterion is waiting for somebody
#: to *run or watch something* — ``awaiting:human-run`` is that gate, and it is one of
#: the types whose ``--do`` payload is required
#: (:data:`contracts.GATE_TYPES_REQUIRING_DO`), so the instruction cannot be lost.
UNOBSERVED_GATE_TYPE = "awaiting:human-run"

#: KYO-267: the criterion state this class is about. One name, checked against
#: :data:`contracts.CRITERION_STATES` by ``tests/test_gates.py`` so a rename cannot
#: leave this reading a state nothing can hold.
UNOBSERVED_CRITERION_STATE = "unmet-unobserved"

#: KYO-349: what goes in ``--do`` when nothing has minted the instruction yet. A
#: criterion's stored *reason* is WHY a person has to look ("the banner is only
#: visible with the app in front of them"), never WHAT to look AT or run — pasting
#: the reason as an instruction produces a gate whose payload answers the wrong
#: question (docs/design/review.md#kyo-349). This placeholder names what belongs in `--do`
#: instead, and is never itself a valid payload: `kyo gate` refuses it unedited
#: (:func:`cmd_links.cmd_gate`), naming this same fact back.
UNGATED_UNOBSERVED_DO_PLACEHOLDER = "<what to look at, and the exact command to run>"


def ungated_unobserved_remedy(record_id: str, criteria: list[dict]) -> str:
    """The ``kyo gate`` line that makes this wait findable — never run, only printed,
    and its ``--do`` is a placeholder for the controller to replace (KYO-267, KYO-349).

    Writing the gate stays the controller's act: a gate carries an instruction to a
    person, and a tool that minted one out of a criterion's reason would be putting
    words in somebody's mouth. A criterion's stored *reason* answers "why does
    somebody have to look", not "what should they look at or run", so it never goes
    in ``--do`` (docs/design/review.md#kyo-349). ``--do`` here is
    :data:`UNGATED_UNOBSERVED_DO_PLACEHOLDER` instead, a value ``kyo gate`` refuses
    to accept unedited, so pasting this line without changing it fails loudly rather
    than storing the reason as if it were an instruction.

    ``criteria`` is accepted and not read: every surface that already has the
    criteria in hand (:func:`ungated_unobserved`'s own ``criteria`` key) prints their
    ids and reasons beside this line, not inside it — that is what "beside" means
    here, and it is why this function's signature still takes the list, for the
    surfaces that pass it straight through.
    """
    return f'kyo gate {record_id} {UNOBSERVED_GATE_TYPE} --do "{UNGATED_UNOBSERVED_DO_PLACEHOLDER}"'


def ungated_unobserved(
    store: Store,
    record_id: str,
    *,
    registry: kinds.KindRegistry | None = None,
    ladder: StatusLadder | None = None,
    running: list | None = None,
) -> dict | None:
    """``record_id``'s entry in the ungated-unobserved class, or ``None`` (KYO-267).

    The one definition of the state; the block comment above says what each clause is
    for. The optional arguments exist so the board-wide walk loads the registry, the
    status ladder and the running dispatches once instead of once per record — passing
    none of them answers for a single record correctly, which is what `kyo verify` does.
    """
    registry = _load_registry(store) if registry is None else registry
    ladder = _load_status_ladder(store) if ladder is None else ladder
    row = store.conn.execute(
        "SELECT id, kind, status, title FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    if row is None:
        return None
    kind_def = registry.get(row["kind"])
    if (
        kind_def is None
        or kind_def.archetype != "work"
        or not kind_def.policy.get("dispatchable", False)
        or ladder.is_settled(row["status"])
    ):
        return None
    unobserved = [
        {"id": c["id"], "state": c["state"], "reason": c["reason"], "text": c["text"]}
        for c in store.criteria(row["id"])
        if c["state"] == UNOBSERVED_CRITERION_STATE
    ]
    if not unobserved:
        return None
    running = store.running_dispatches() if running is None else running
    for dispatch in running:
        if row["id"] in (dispatch["record_id"], *commands.dispatch_covers(dispatch)):
            return None
    if any(
        gate["cleared_at"] is None and gate["gate_type"] in AWAITING_GATE_TYPES
        for gate in store.gates(row["id"])
    ):
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "title": row["title"],
        "criteria": unobserved,
        "remedy": ungated_unobserved_remedy(row["id"], unobserved),
    }


def ungated_unobserved_records(
    store: Store, registry: kinds.KindRegistry | None = None
) -> list[dict]:
    """Every record in the ungated-unobserved class, in record order (KYO-267).

    A walk over :func:`ungated_unobserved` and nothing else, so `kyo brief`, `kyo next`
    and `kyo doctor` cannot disagree with each other or with `kyo verify` about which
    records are in the class.
    """
    registry = _load_registry(store) if registry is None else registry
    ladder = _load_status_ladder(store)
    running = store.running_dispatches()
    out: list[dict] = []
    for row in store.conn.execute("SELECT id FROM records ORDER BY seq"):
        item = ungated_unobserved(
            store, row["id"], registry=registry, ladder=ladder, running=running
        )
        if item is not None:
            out.append(item)
    return out


def cmd_next(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    registry = _load_registry(store)
    board = _ready_records(store, registry)
    # KYO-267: computed here and in `cmd_brief` from the SAME function, on KYO-87.c3's
    # reasoning — a class one surface names and the other hides is the two surfaces
    # disagreeing about the same row, which is the defect and not a rendering choice.
    unobserved = ungated_unobserved_records(store, registry)
    return Output(
        [
            next_text(
                board.ready,
                board.scope_unknown,
                board.settled_not_moved,
                unobserved,
                blocked=board.blocked,
            )
        ],
        {
            "ok": True,
            "command": "next",
            "ready": board.ready,
            "scope_unknown": board.scope_unknown,
            # KYO-570.c5: each row's `blockers` carries the unsettled blocker ids.
            "blocked": board.blocked,
            "ungated_unobserved": unobserved,
            # KYO-87.c3: the class `kyo brief` names, named here too and off the
            # same computation. A record `kyo next` silently dropped and the brief
            # explained would be the two surfaces disagreeing about the same row —
            # which is the defect, not a rendering difference.
            "settled_not_moved": board.settled_not_moved,
        },
    )
