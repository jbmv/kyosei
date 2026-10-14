"""``link`` / ``unlink`` / ``gate`` / ``retag`` / ``ungate``.

Edges between records, and the gates that hold a record until something outside
the store clears. Both are relationships hung off a record rather than changes
to it, and both echo the relationship they stored (law 5).
"""

from __future__ import annotations

import json
from argparse import Namespace

from . import commands, explanations, export, links
from . import registry as registry_module
from .clock import now
from .commands import (
    _UNMET_CRITERION_STATES,
    Context,
    Output,
    _reason,
    _require_record,
    _touch_record,
)
from .contracts import (
    FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA,
    GATE_TYPES,
    GATE_TYPES_FLAGGING_STALE_CRITERIA,
    GATE_TYPES_REQUIRING_DO,
    LINK_TYPES,
)
from .errors import KyoseiError, NotFound, UsageError
from .prose import fingerprint, flatten, read_prose, single_line
from .render import (
    echo_block,
    echo_value,
    edge_sentence,
)

#: How "this gate carries no tags" is echoed. Spelled, never rendered as an empty
#: string: a blank after "stored tags:" reads as a display bug, while the whole point
#: of `kyo retag --no-tags` is that clearing the classification is a thing a caller can
#: deliberately mean and should see confirmed back (law 5).
NO_TAGS = "(none)"


def _titled_edge(store, src: str, edge_type: str, dst: str) -> list[str]:
    """The edge as a directional sentence, with both records' current titles.

    Titles are read from the store at the moment of the echo (KYO-47): the point
    of the sentence is to be checkable against what the operator meant *now*, so a
    renamed record must render under its new name.
    """
    return edge_sentence(
        src,
        store.record(src)["title"],
        edge_type,
        dst,
        store.record(dst)["title"],
    )


def _consequence(store, src: str, edge_type: str, dst: str) -> str:
    """``src edge_type dst``, said as one sentence of consequence (KYO-350).

    Titles are read fresh, exactly as :func:`_titled_edge` reads them, and for the
    same reason. The sentence exists because the titled block above it is not
    enough on its own: a caller who reads only the LAST line of `kyo link`'s output
    — the failure this record was filed over, piping through `tail -1` — saw a bare
    title, which says WHO but not WHAT. This is meant to be read alone.
    """
    return links.edge_consequence(
        edge_type,
        src,
        store.record(src)["title"],
        dst,
        store.record(dst)["title"],
    )


def cmd_link(ctx: Context, args: Namespace) -> Output:
    store = ctx.store()
    if args.edge_type not in LINK_TYPES:
        raise UsageError(
            f"{args.edge_type!r} is not a link type; known: {', '.join(LINK_TYPES)}"
        )
    src, dst = args.src, args.dst
    with store.transaction():
        _require_record(store, src)
        _require_record(store, dst)
        if src == dst:
            raise KyoseiError(f"refusing self-link: {src} {args.edge_type} {dst}")
        existing = store.conn.execute(
            "SELECT id FROM links WHERE src_id = ? AND edge_type = ? AND dst_id = ?",
            (src, args.edge_type, dst),
        ).fetchone()
        if existing:
            raise KyoseiError(
                f"{src} {args.edge_type} {dst} already exists as {existing['id']}"
            )
        cycle = links.cycle_if_added(store, args.edge_type, src, dst)
        if cycle:
            raise KyoseiError(
                f"refusing cycle in {args.edge_type}: " + " -> ".join(cycle)
            )
        before = {
            src: export.record_document(store, src),
            dst: export.record_document(store, dst),
        }
        link_id, seq = store.new_link_id()
        store.conn.execute(
            "INSERT INTO links (id, seq, src_id, edge_type, dst_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (link_id, seq, src, args.edge_type, dst, now()),
        )
        _touch_record(store, src)
        _touch_record(store, dst)
        after, events = commands._commit_and_export(
            store,
            command=f"link {src} {args.edge_type} {dst}",
            actor=ctx.actor,
            record_ids=[src, dst],
            before=before,
        )
    commands._finish_mutation(store, [src, dst], events)
    # KYO-47.c1: the id line confirms what was stored; the titled block below it
    # says what that means. KYO-350.c2 adds the LAST line: a caller who reads only
    # the tail of this output (as the consumer who filed this record did, piping
    # through `tail -1`) must still land on a sentence of consequence, not a bare
    # title — reversal is the error this whole echo exists to catch.
    consequence = _consequence(store, src, args.edge_type, dst)
    lines = [
        f"created link {link_id}",
        echo_value("edge", f"{src} {args.edge_type} {dst}"),
        *_titled_edge(store, src, args.edge_type, dst),
        consequence,
    ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "link",
            "link_id": link_id,
            "link": {"id": link_id, "src": src, "type": args.edge_type, "dst": dst},
            "records": [after[src], after[dst]],
            "consequence": consequence,
        },
    )


def cmd_unlink(ctx: Context, args: Namespace) -> Output:
    """``kyo unlink <link-id> --why`` (KYO-21) — retract a typed edge.

    `kyo link` had no inverse, and the first consumer project outside this repo
    filed three wrong edges in one day. One of them was a
    `blocks` edge pointing the wrong way, which is the case that decides the
    design: the *correct* edge could not be added either, because the wrong one
    made it a cycle and law 6 refuses cycles at write time. So a mis-directed edge
    was not merely permanent — it actively blocked its own correction. Cycle
    refusal assumes edges are right on the first try, which the absence of an
    inverse guaranteed they would not be.

    The row is deleted rather than soft-retracted, for the same reason `kyo edit
    --touches-file` replaces rather than soft-deletes (KYO-16): the events ledger
    already carries both endpoints' full documents before and after, and every
    record document lists its links with ids, so the retracted edge and this
    command's `--why` stay readable forever without leaving a row that every
    reader — the cycle check, the link graph, `kyo show` — would then have to
    remember to filter out. The `links` table means "the edges that currently
    hold"; history lives where this project keeps history.

    `--why` is required, on the ungate precedent: retracting a claim someone made
    is exactly the kind of act that is worthless without its reason.
    """
    store = ctx.store()
    reason = _reason(args)
    # The edge is read, and rendered as a titled sentence, *before* anything is
    # deleted (KYO-47.c4): the argument is an opaque `KYO-Lnn` with no human-
    # readable content, and this project has already destroyed a correct edge by
    # passing the id of one link while meaning another. Since a `--why` is required
    # anyway, the refusal is the one moment a caller can be shown what the id they
    # typed actually points at, with nothing removed yet -- and it costs no prompt,
    # which would hang a dispatched agent.
    row = store.link(args.link_id)
    src, dst, edge_type = row["src_id"], row["dst_id"], row["edge_type"]
    sentence = _titled_edge(store, src, edge_type, dst)
    if not (reason or "").strip():
        raise UsageError(
            "\n".join(
                [
                    f"kyo unlink {args.link_id} needs --why (or --why-file): retracting an "
                    "edge someone deliberately filed is not bookkeeping",
                    f"nothing has been removed. {args.link_id} is this edge:",
                    *sentence,
                ]
            )
        )
    with store.transaction():
        # KYO-414: the read above ran before the write lock, so a
        # concurrent `kyo unlink` of the same id can have deleted the row since. The
        # DELETE runs under the lock and must remove exactly one row: without that
        # check the second of two racing unlinks deleted nothing and still logged a
        # retraction for both endpoints and printed "REMOVED this edge" -- two
        # retractions in the ledger for one edge. NotFound rolls the transaction back
        # with nothing written.
        before = {
            src: export.record_document(store, src),
            dst: export.record_document(store, dst),
        }
        deleted = store.conn.execute("DELETE FROM links WHERE id = ?", (args.link_id,))
        if deleted.rowcount != 1:
            raise NotFound(f"no link {args.link_id!r}")
        _touch_record(store, src)
        _touch_record(store, dst)
        after, events = commands._commit_and_export(
            store,
            command=f"unlink {args.link_id} ({src} {edge_type} {dst}) because: {reason}",
            actor=ctx.actor,
            record_ids=[src, dst],
            before=before,
        )
    commands._finish_mutation(store, [src, dst], events)
    # KYO-47.c4: `unlink` used to close with `echo_value("edge", ...)`, printing
    # `stored edge: KYO-50 blocks KYO-51` -- the exact phrasing `kyo link` uses to
    # confirm a *creation*, under which a destruction was read as reassurance and a
    # correct edge stayed destroyed. A retraction says it removed something, in
    # words that cannot be mistaken for storing something.
    lines = [
        f"retracted link {args.link_id}",
        "REMOVED this edge; it no longer holds:",
        *sentence,
        *echo_block("reason", reason),
    ]
    return Output(
        lines,
        {
            "ok": True,
            "command": "unlink",
            "link_id": args.link_id,
            "link": {"id": args.link_id, "src": src, "type": edge_type, "dst": dst},
            "why": reason,
            "records": [after[src], after[dst]],
        },
    )


def _validated_tags(store, names: list[str] | None) -> list[str]:
    """The tags to store for one gate: normalized, then refused against the registry.

    One function rather than a copy in each writer, because KYO-130.c4 asks that a
    tag the project does not declare be refused on `kyo retag` "with the same wording
    it is refused with at gate creation". Sharing the sentence is weaker than sharing
    the path: two call sites of ``registry.require`` can drift apart while both still
    quoting the registry, and the wording would then be equal only until someone
    edited one of them. With a single call site there is no second sentence to drift
    — the refusal is identical because it is the same refusal (law 3).
    """
    tag_registry = registry_module.load_gate_tag_registry(store.config.kyosei_dir)
    tags = registry_module.normalized_tags(names)
    for tag in tags:
        tag_registry.require(tag)
    return tags


def _payload(args: Namespace) -> str | None:
    """The ``do:`` payload one invocation means, from ``--do`` or ``--do-file``.

    KYO-148.c1. ``--do TEXT`` was the only form: no ``--do-file``, no stdin, and the
    write path refused multi-line prose under law 5 — while ``--why-file``,
    ``--description-file``, ``--criteria-file`` and ``--task-file`` all take a file
    precisely so their content can be readable. The one field whose job is "the exact
    thing to run" was the only one that could not be formatted.

    **Law 5 was never the obstacle**, and that is the whole design here. Law 5 says
    multi-line prose enters by file or stdin, which is exactly what ``--do-file`` is.
    What existed before was the rule enforced with no compliant route available
    anywhere: a refusal that named no way to succeed. The incident is what that cost —
    an operator went to answer a ``ui-review`` gate whose payload held a query fragment
    (``?previewBanner=readonly``) and half a command rather than a URL, because a procedure
    with a command and several links, each with what to look at, had to be written as
    one line. Their words: "I have no way to get to those views without you telling me."

    ``--do`` is kept exactly as it was, for the short payloads it suits (c4): a single
    command must not be forced into a file. What it acquires by having a twin is a
    stricter shell-residue check, and that is *derived* rather than declared — the
    prose-argument check refuses a flag whose subparser also offers a ``--<flag>-file``
    form and merely warns about one that does not, so ``--do`` starts refusing residue
    here with no edit to that machinery at all (``cli.check_prose_arguments``, KYO-91,
    which wrote down "ship a ``--title-file`` one day and ``--title`` starts refusing,
    here, with no edit" and meant it).

    One function rather than a copy in each writer, on ``_validated_tags``'s reasoning
    exactly (KYO-130): `kyo gate` and `kyo redo` both write this field, and c3 asks that
    the second refuse what the first refuses. Sharing the *sentence* between two call
    sites is weaker than sharing the *path* — two copies quoting one rule stay equal
    only until someone edits one of them. With a single call site there is no second
    sentence to drift; the refusals are identical because they are the same refusal
    (law 3).
    """
    do = getattr(args, "do", None)
    do_file = getattr(args, "do_file", None)
    if do and do_file:
        raise UsageError("pass --do or --do-file, not both")
    if do_file:
        text = read_prose(do_file, what="--do-file")
        # Law 6 says an empty file means an empty value and never "argument not given",
        # so this refuses out loud rather than quietly resolving to "no payload". A
        # payload is not a description: it is the instruction someone has to follow, and
        # an empty one is a gate nobody can clear — the exact condition
        # GATE_TYPES_REQUIRING_DO exists against. Saying "no payload" has a way to be
        # said, and the message names it.
        if not text.strip():
            raise UsageError(
                f"--do-file read an empty payload from {do_file}: a gate's payload is the "
                "instruction someone has to follow, and an empty one is a gate nobody can "
                "clear. To gate a record with no payload at all, omit --do and --do-file "
                "entirely (the gate types that require one will say so)"
            )
        return text
    if do:
        return single_line(do, what="--do")
    return None


def cmd_gate(ctx: Context, args: Namespace) -> Output:
    """``kyo gate <id> <type> [--do TEXT] [--tag NAME ...]``.

    KYO-102 adds ``--tag``. The gate *type* has been a closed four-member
    registry since v1; what was unstructured is the payload, and an unstructured
    payload is not a query — KYO-46 verbatim: "everything waiting on a human to review
    a UI element" could not be asked. A tag from ``kyosei/gate-tags.yml`` is that
    question's answer, and an undeclared tag is refused here, at write time, with the
    registry's own sentence (law 6).

    What does NOT change, deliberately: ``--do``. KYO-46 demotes the free text to
    "optional detail rather than the only content", which is about what the payload is
    the only *carrier* of, not about whether the three gate types that need one may go
    without. A tag says a gate is waiting on a deploy; only the payload says which
    deploy, and a gate nobody can clear is what the requirement exists against. So the
    payload is kept beside the tags, on exactly the terms it already had.

    KYO-148 adds ``--do-file`` beside ``--do`` and changes nothing about that argument:
    the payload is still required for the same three gate types, still optional for the
    fourth, and ``--do TEXT`` still takes a one-line payload without a file (c4). What
    changes is that a payload too long for one line now has a way in at all — see
    :func:`_payload`, which both this command and `kyo redo` resolve through.
    """
    store = ctx.store()
    if args.gate_type not in GATE_TYPES:
        raise UsageError(
            f"{args.gate_type!r} is not a gate type; known: {', '.join(GATE_TYPES)}"
        )
    detail = _payload(args)
    # KYO-349.c2: `kyo verify`, `kyo brief`, `kyo next`, `kyo accept` and `kyo doctor`
    # all suggest a `kyo gate ... --do <placeholder>` line with this exact text where
    # the instruction goes — a controller who pastes it unedited has not written an
    # instruction, they have copied the tool's own filler, and storing that as if it
    # were one would be worse than the wait staying ungated. Local import: cmd_next
    # is the review layer, above this one, exactly as it is in cmd_evidence.cmd_verify
    # and doctor_records.check_ungated_unobserved_criteria.
    from . import cmd_next

    if (detail or "").strip() == cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER:
        raise UsageError(
            "--do is still the placeholder kyo verify/brief/next/accept/doctor print "
            f"({cmd_next.UNGATED_UNOBSERVED_DO_PLACEHOLDER!r}) — write what a person "
            "should actually look at or run, e.g. `--do \"watch the badge after a cold "
            'start"` or `--do "run make test and read the failures"`, and nothing is '
            "stored until you do"
        )
    if args.gate_type in GATE_TYPES_REQUIRING_DO and not detail:
        raise UsageError(
            f"gate type {args.gate_type} requires --do: the exact condition or thing to "
            "run must live on the record, not in chat. Pass --do TEXT for a payload that "
            "fits on one line, or --do-file FILE — or `--do-file -` for stdin — for a "
            "procedure that does not (KYO-148)"
        )
    # The refusal is the shared registry one (registry.unknown_name_refusal), not a
    # sentence of this module's own: a filter gets the same words a write gets, and
    # every dimension gets the same words as its siblings (law 3).
    tags = _validated_tags(store, args.tag)
    record_id = args.id
    with store.transaction():
        _require_record(store, record_id)
        before = {record_id: export.record_document(store, record_id)}
        gate_id, seq = store.new_child_id(record_id, "gate")
        store.conn.execute(
            "INSERT INTO gates (id, record_id, seq, gate_type, detail, tags_json,"
            " created_at, cleared_at, cleared_why) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                gate_id,
                record_id,
                seq,
                args.gate_type,
                detail,
                json.dumps(tags) if tags else None,
                now(),
            ),
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"gate {record_id} {args.gate_type}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    lines = [
        f"gated {record_id} with {gate_id}",
        echo_value("gate type", args.gate_type),
    ]
    # Law 5: the tags that were STORED, not the ones that were typed — which is what
    # makes deduplicating a repeated `--tag` honest rather than silent.
    if tags:
        lines.append(echo_value("tags", ", ".join(tags)))
    if detail:
        lines += echo_block("do", detail)
    lines.append(f"status is unchanged ({explanations.say('gates-orthogonal')})")
    return Output(
        lines,
        {
            "ok": True,
            "command": "gate",
            "gate_id": gate_id,
            "record_id": record_id,
            "record": after[record_id],
        },
    )


def cmd_retag(ctx: Context, args: Namespace) -> Output:
    """``kyo retag <gate-id> (--tag NAME ... | --no-tags)``.

    KYO-130. A gate's tags were writable only at creation, so a gate that predates the
    project's adoption of ``kyosei/gate-tags.yml`` could not be classified — which is
    every gate in every store that existed before the dimension did. The only available
    route was `ungate` then `gate`, and it costs a new gate id, an ``ungate`` event on a
    gate that never cleared, and (for ``holding-for``) a KYO-59
    ``gate-cleared-stale-criteria`` flag, which is a correct response to an incorrect
    premise. The reporting consumer paid that nine times to add a classification and
    reported it: "it is nine false clearings in a git-canonical ledger to add a
    classification, and a consumer with a hundred gates would not sensibly do it."

    Why tags may be corrected in place at all, when criterion text may not: a tag is a
    CLASSIFICATION, not a claim about the gate's condition. The store already draws that
    line — a record's ``touches`` is a working scope corrected in place with
    ``kyo edit --touches-file``, while criterion text is immutable from first verdict and
    corrected only through `kyo restate`, because a verdict was recorded against it. A
    tag sits on the ``touches`` side: nobody has ever been told anything by it that a
    correction would falsify. So this writes ``gates.tags_json`` and nothing else — the
    gate's id, its ``--do`` payload, and its ``created_at`` / ``cleared_at`` /
    ``cleared_why`` are not in the UPDATE at all (c2), which is what makes "retagging is
    not a clearing" a property of the statement rather than a promise in a docstring.

    A cleared gate is retaggable too, deliberately. Classifying one is bookkeeping about
    history, not a claim that it is open, and `kyo list --gate-tag` selects on OPEN gates
    only — so the tags of a cleared gate are inert to the query and can be corrected
    without changing any answer. Refusing here would be a rule with no harm behind it.

    Replacement, not accumulation: the tags given become the gate's tags. A correction is
    usually "these, not those", and an ``--add``/``--remove`` pair invites the state where
    the caller cannot say what the gate now carries without reading it back. What makes
    replacement safe is the echo — both the previous set and the stored set are printed
    (law 5), so a caller who meant to add sees what they actually dropped. Saying "no
    tags at all" needs its own word, since an absent ``--tag`` cannot mean both "clear
    them" and "you forgot the flag": that is ``--no-tags``, on the ``--no-plan`` /
    ``--no-scope`` precedent from `kyo edit`, and one of the two is required.
    """
    store = ctx.store()
    gate_id = args.gate_id
    # Refused before the transaction opens and before the gate is read, exactly as
    # `kyo gate` does it: an undeclared tag stores nothing, so there is no state in
    # which a gate was found, half-retagged, and then refused.
    tags = [] if args.no_tags else _validated_tags(store, args.tag)
    with store.transaction():
        gate = store.gate(gate_id)
        record_id = gate["record_id"]
        previous = json.loads(gate["tags_json"]) if gate["tags_json"] else []
        before = {record_id: export.record_document(store, record_id)}
        store.conn.execute(
            "UPDATE gates SET tags_json = ? WHERE id = ?",
            (json.dumps(tags) if tags else None, gate_id),
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            # KYO-130.c3: its own command string, so the change is a named act in
            # `_events.jsonl` rather than an unexplained difference between two
            # exports — and one that reads as neither a gating nor a clearing.
            command=f"retag {gate_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    lines = [
        f"retagged {gate_id} on {record_id} ({gate['gate_type']})",
        echo_value("tags", ", ".join(tags) if tags else NO_TAGS),
    ]
    # Law 5 again, and the reason replacement is safe to offer: what the gate carried
    # before is printed beside what it carries now, so a caller who meant `--tag b` as
    # "and b" can see that `a` is gone.
    lines.append(f"  previous tags: {', '.join(previous) if previous else NO_TAGS}")
    if tags == previous:
        # Law 6: "ran and changed nothing" is a different statement from "did not run",
        # and the caller who expected a change is the one who needs to hear it.
        lines.append("these are the tags it already carried — nothing changed")
    # c2, said out loud rather than only held true, because the whole point of the
    # command is that it is NOT the ungate/gate round trip it replaces.
    lines.append(
        "unchanged: the gate's id, its --do payload, and its open/cleared history "
        "(no gate was cleared and no gate was created)"
    )
    lines.append(
        f"this gate is still open — {gate_id} clears with `kyo ungate {gate_id}`"
        if not gate["cleared_at"]
        else f"this gate was already cleared at {gate['cleared_at']}; "
        "its tags are a classification of history and no query over open gates changes"
    )
    return Output(
        lines,
        {
            "ok": True,
            "command": "retag",
            "gate_id": gate_id,
            "record_id": record_id,
            "tags": tags,
            "previous_tags": previous,
            "record": after[record_id],
        },
    )


def cmd_redo(ctx: Context, args: Namespace) -> Output:
    """``kyo redo <gate-id> (--do TEXT | --do-file FILE)``.

    KYO-148.c2/c3. A gate's payload was writable only at creation, and gates are absent
    from `kyo edit` entirely, so correcting a ``do:`` — including one that turned out
    not to be executable — needed `ungate` then `gate`. That is the wrong instrument
    twice over: it writes ``cleared`` into a git-canonical ledger for a condition that
    did not clear, and it mints a new gate id for what is textually one continuing
    obligation. The reporting consumer did it three times and had to say so in the
    ``--why`` each time.

    **Its own verb, on KYO-130's stated rule** — "a command addressing a child object by
    the child's own id is a verb of its own here" (`restate <criterion-id>`, `unlink
    <link-id>`, `ungate <gate-id>`, `retag <gate-id>`). The alternative considered and
    rejected was `kyo edit <record-id> --gate <gate-id> --do-file FILE`, which is what
    the consumer report's own wording suggests ("gates are absent from `kyo edit`"). It
    loses on two counts: it makes the caller retype a record id the gate id already
    contains, and it gives the two corrections available on one object two unrelated
    shapes — `kyo retag KYO-1.g1` beside `kyo edit KYO-1 --gate KYO-1.g1`. Correcting a
    gate's tags and correcting its payload are the same kind of act and should read like
    it (law 3).

    **Why a payload may be corrected in place at all**, which is not quite KYO-130's
    argument and the difference is the point. A tag is a CLASSIFICATION, and KYO-130's
    reason for allowing it was that "nobody has ever been told anything by it that a
    correction would falsify". A payload is not a classification — but neither is it a
    claim about whether the gate HOLDS. It is an instruction to a human, and an
    instruction that turns out to be unfollowable is wrong in the way a working scope is
    wrong, not in the way a criterion is wrong. The store already has a verb for each:
    ``touches`` is corrected in place by `kyo edit --touches-file`, criterion text is
    immutable from its first verdict. An open gate's payload sits on the ``touches``
    side, so this writes ``gates.detail`` and nothing else — the gate's id, its type,
    its tags and its ``created_at``/``cleared_at``/``cleared_why`` are not in the UPDATE
    at all, which is what makes "correcting a payload is not a clearing" a property of
    the statement rather than a promise in a docstring (c2).

    **A CLEARED gate is refused, and here this parts company with `kyo retag`.**
    KYO-130 allows retagging a cleared gate deliberately: classifying one is bookkeeping
    about history and `kyo list --gate-tag` selects on open gates only, so no answer
    changes. The payload is different in the one way that matters — somebody was told to
    do a thing, did it, and recorded ``cleared_why`` against it. Rewriting the
    instruction afterwards would make the ledger say they did something they were never
    asked to do. That is exactly the hazard `_correct_criterion_text` refuses on
    (KYO-58: "a verdict attesting to text that later changed... that evidence silently
    re-attaches to a different claim"), and it is the strongest precedent in the store,
    so this follows it rather than `retag`.

    **Where the previous payload stays readable** (c3): in this command's own echo,
    printed beside the new one, and permanently in the events ledger — every mutation
    writes the record's whole document before and after, so ``kyosei/export/_events.jsonl``
    holds the old ``do:`` verbatim under this command's own name. That is the same place
    a criterion corrected in place leaves its old wording, and it is why this needs no
    new column: the record explicitly rules out a schema change, and the gate object
    already carries the string.
    """
    store = ctx.store()
    gate_id = args.gate_id
    # Resolved before the transaction opens and before the gate is read, exactly as
    # `kyo retag` validates tags: a refused payload writes nothing, so there is no state
    # in which a gate was found, half-rewritten, and then refused.
    detail = _payload(args)
    if not detail:
        raise UsageError(
            "kyo redo needs --do TEXT or --do-file FILE: it exists to replace a gate's "
            "payload, and there is nothing to replace it with"
        )
    with store.transaction():
        gate = store.gate(gate_id)
        record_id = gate["record_id"]
        if gate["cleared_at"]:
            raise KyoseiError(
                f"{gate_id} was cleared at {gate['cleared_at']} and its payload is no "
                "longer correctable: someone was told to do this, did it, and the reason "
                "they cleared it is recorded against these words. Rewriting them now "
                "would make the ledger describe an instruction nobody was given. If the "
                "obligation is still live, `kyo gate` a new one saying what is actually "
                "needed — a cleared gate that asked for the wrong thing is a fact about "
                "what happened, not an error to edit away"
            )
        previous = gate["detail"] or ""
        before = {record_id: export.record_document(store, record_id)}
        store.conn.execute("UPDATE gates SET detail = ? WHERE id = ?", (detail, gate_id))
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            # c3: its own command string, so the correction is a named act in
            # `_events.jsonl` rather than an unexplained difference between two exports —
            # and one that reads as neither a gating nor a clearing.
            command=f"redo {gate_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    lines = [
        f"rewrote the payload on {gate_id} on {record_id} ({gate['gate_type']})",
        *echo_block("do", detail),
    ]
    # Law 5, and c3's "leaves the previous text readable": what the gate said before is
    # printed beside what it says now, measured the same way, so a caller who meant to
    # append can see that they replaced. Spelled out here rather than passed through
    # `echo_block`, whose "stored ..." wording answers a different question — this is
    # what is no longer stored.
    if previous:
        lines.append(
            f"  it previously said ({len(previous)} chars, sha256:{fingerprint(previous)}):"
        )
        lines.extend(f"  | {line}" for line in previous.split("\n"))
    else:
        lines.append("  it previously carried no payload at all")
    if detail == previous:
        # Law 6: "ran and changed nothing" is a different statement from "did not run".
        lines.append("this is the payload it already carried — nothing changed")
    # c2, said out loud rather than only held true, because the whole point of the
    # command is that it is NOT the ungate/gate round trip it replaces.
    lines.append(
        "unchanged: the gate's id, its type, its tags, and its open/cleared history "
        "(no gate was cleared and no gate was created)"
    )
    lines.append(f"this gate is still open — {gate_id} clears with `kyo ungate {gate_id}`")
    return Output(
        lines,
        {
            "ok": True,
            "command": "redo",
            "gate_id": gate_id,
            "record_id": record_id,
            "do": detail,
            "previous_do": gate["detail"],
            "record": after[record_id],
        },
    )


def _criteria_older_than(store, record_id: str, moment: str) -> list[dict]:
    """The record's still-live criteria authored before ``moment`` (KYO-59.c1).

    The whole test, with no heuristics in it: ``criterion.created_at`` against the
    gate's resolution time. No text analysis, no similarity scoring, nothing that
    can be wrong in an interesting way.

    Two narrowings, both deliberate. Settled criteria are excluded — a ``met``
    criterion's verdict is already recorded against the world as it was, and a void
    one directs nobody; only a criterion still capable of sending a worker somewhere
    can send one to the wrong place, which is the harm. And the comparison is
    ``<``, not "all of them": at the instant a gate clears these are the same set,
    but writing the real comparison is what makes the flag's claim true rather than
    merely correct today.
    """
    return [
        dict(row)
        for row in store.conn.execute(
            "SELECT id, text, state, created_at FROM criteria WHERE record_id = ?"
            " AND created_at < ? ORDER BY seq",
            (record_id, moment),
        )
        if row["state"] in _UNMET_CRITERION_STATES
    ]


def _stale_criteria_flag_detail(gate, moment: str, criteria: list[dict]) -> str:
    """The KYO-59 flag's text. The payload is flattened, and that is not cosmetic.

    KYO-148: this is the only place a gate's payload is copied into a *stored* field
    rather than printed. It goes inside a parenthetical, and the result is written to
    ``flags.detail``, which is permanent, has no correction command, and is rendered by
    four one-line surfaces (`kyo brief`, `kyo sweep`, `kyo flag`, and `ungate`'s own
    echo) — and `cmd_next` dedupes unresolved flags by exact string equality on it.
    A newline reaching here would corrupt all of that unfixably, so the payload is
    collapsed to one line by ``prose.flatten``, which is this project's single answer to
    "where it wrapped is not information" (KYO-66). The gate's own record still holds the
    payload exactly as written; only this quotation of it is flattened.
    """
    named = ", ".join(f"{c['id']} ({c['state']})" for c in criteria)
    do = f": {flatten(gate['detail'])}" if gate["detail"] else ""
    return (
        f"{gate['id']} ({gate['gate_type']}{do}) cleared at {moment}; "
        f"{len(criteria)} criterion/criteria were authored before that and may describe "
        f"the world it was waiting on — {named}. Review them: restate the ones the "
        "decision changed (`kyo restate <criterion-id> --text-file FILE`), then resolve "
        "this flag"
    )


def cmd_ungate(ctx: Context, args: Namespace) -> Output:
    """``kyo ungate <gate-id>... [--why]``, or ``kyo ungate --tag T [--apply]``.

    KYO-59: clearing a ``holding-for`` gate also flags the record when it carries
    criteria older than the clearing. A gate's whole meaning is "this record is
    waiting on something that will change what it should be", so clearing one
    always implies something just changed — and every criterion written before that
    moment was authored under the assumption it has just resolved.

    The case that produced it: KYO-30 sat gated on "the v2 promoted-table design
    decides what a promotion stamp is". When that design was decided and the gate cleared,
    both of KYO-30's criteria were stale in independent ways — one described a
    single-value stamp where the decision chose a list, one named "the four existing
    bottom-stamped ideas" when there were 32. Both were caught only because the
    controller happened to read them before dispatching.

    "It is done at dispatch" was the alternative and is not the answer: that rule
    exists in ``kyosei/procedures.md``, marked ``enforced-by: aspirational``, which
    by this project's law 1 is what a mandate is — what you write when the mechanism
    is not built. Dispatch is also the wrong moment, one step too late: by then the
    controller has selected the record and written the task prose against the stale
    text. The staleness is created here, when the gate clears, and here is where it
    is cheapest to notice.

    Flagging, never editing — the charter's rule for every flag class. The flag
    names the criteria and the gate, reaches `kyo brief` like any other unresolved
    flag (so it survives the session boundary the KYO-30 case actually spanned), and
    is cleared by `kyo flag resolve` once a human has looked.

    KYO-492: several gate ids, or ``--tag T`` (dry run unless ``--apply``), clear in
    one transaction through :func:`_clear_gate` — the single path, run once per gate —
    so ``cleared_why``, the events, the KYO-59 flag and the refusals are a single
    `kyo ungate`'s, repeated. One bare id with no ``--tag`` is today's command exactly.
    """
    tag = getattr(args, "tag", None)
    apply = bool(getattr(args, "apply", False))
    gate_ids = list(dict.fromkeys(args.gate_id or []))
    if tag is not None and gate_ids:
        raise UsageError(
            "name gates by id or select them with --tag, not both: --tag already "
            "means every open gate carrying it"
        )
    if apply and tag is None:
        raise UsageError(
            "--apply confirms a --tag selection; gates named by id are cleared without it"
        )
    if tag is None and not gate_ids:
        raise UsageError("name at least one gate id, or select open gates with --tag TAG")
    store = ctx.store()
    reason = _reason(args)
    if tag is not None:
        return _ungate_by_tag(ctx, store, tag, reason, apply)
    if len(gate_ids) == 1:
        return _ungate_one(ctx, store, gate_ids[0], reason)
    return _ungate_many(ctx, store, gate_ids, reason, command=f"ungate {' '.join(gate_ids)}")


def _already_cleared(gate_id: str, cleared_at: str) -> str:
    """The refusal for clearing a cleared gate, in one place so a bulk refusal names a
    bad id in exactly the words a single `kyo ungate` would have used (KYO-492.c1)."""
    return f"{gate_id} was already cleared at {cleared_at}"


def _clear_gate(store, gate_id: str, reason: str | None, cleared_at: str) -> dict:
    """Clear one gate inside the caller's transaction; the caller exports and commits.

    The one write path for a clearing, whether one gate was named or many (KYO-492):
    the UPDATE, the KYO-59 stale-criteria flag, and the record's touch."""
    gate = store.gate(gate_id)
    if gate["cleared_at"]:
        raise KyoseiError(_already_cleared(gate_id, gate["cleared_at"]))
    record_id = gate["record_id"]
    store.conn.execute(
        "UPDATE gates SET cleared_at = ?, cleared_why = ? WHERE id = ?",
        (cleared_at, reason, gate_id),
    )
    flagged: dict | None = None
    if gate["gate_type"] in GATE_TYPES_FLAGGING_STALE_CRITERIA:
        stale = _criteria_older_than(store, record_id, cleared_at)
        if stale:
            detail = _stale_criteria_flag_detail(gate, cleared_at, stale)
            flag_id = commands.insert_flag(
                store, record_id, FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA, detail
            )
            flagged = {
                "flag_id": flag_id,
                "flag_type": FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA,
                "detail": detail,
                "criteria": [c["id"] for c in stale],
            }
    _touch_record(store, record_id)
    return {
        "gate_id": gate_id,
        "record_id": record_id,
        "gate_type": gate["gate_type"],
        "flag": flagged,
    }


def _ungate_one(ctx: Context, store, gate_id: str, reason: str | None) -> Output:
    with store.transaction():
        record_id = store.gate(gate_id)["record_id"]
        before = {record_id: export.record_document(store, record_id)}
        cleared = _clear_gate(store, gate_id, reason, now())
        after, events = commands._commit_and_export(
            store,
            command=f"ungate {gate_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    flagged = cleared["flag"]
    gate_type = cleared["gate_type"]
    lines = [f"cleared {gate_id} on {record_id} ({gate_type})"]
    if reason is not None:
        lines += echo_block("why", reason)
    if flagged is not None:
        lines.append(f"flagged {flagged['flag_id']}  {flagged['flag_type']}")
        lines.append(f"  {flagged['detail']}")
    elif gate_type in GATE_TYPES_FLAGGING_STALE_CRITERIA:
        # Law 6: the check ran and found nothing is a different statement from the
        # check not being part of this gate type at all, and both are said out loud.
        lines.append(
            "no criteria predate this clearing — nothing to review "
            f"({FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA} not raised)"
        )
    else:
        lines.append(
            f"{gate_type} does not raise {FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA} "
            f"({explanations.say('ungate-no-stale-flag')})"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "ungate",
            "gate_id": gate_id,
            "record_id": record_id,
            "flag": flagged,
            "record": after[record_id],
        },
    )


def _ungate_many(
    ctx: Context, store, gate_ids: list[str], reason: str | None, *, command: str
) -> Output:
    """Clear every gate in ``gate_ids`` in one transaction, or none of them.

    Every id is checked before anything is written, so a refusal names each bad id
    with the reason a single `kyo ungate` would have given for it — not just the first
    one a loop happened to reach (KYO-492.c1)."""
    with store.transaction():
        bad: list[str] = []
        for gate_id in gate_ids:
            try:
                gate = store.gate(gate_id)
            except NotFound as missing:
                bad.append(f"{gate_id}: {missing}")
                continue
            if gate["cleared_at"]:
                bad.append(f"{gate_id}: {_already_cleared(gate_id, gate['cleared_at'])}")
        if bad:
            raise KyoseiError(
                f"nothing was cleared: {len(bad)} of {len(gate_ids)} gate(s) cannot be "
                "cleared, and a bulk ungate clears all of them or none — "
                + "; ".join(bad)
            )
        record_ids = list(dict.fromkeys(store.gate(g)["record_id"] for g in gate_ids))
        before = {rid: export.record_document(store, rid) for rid in record_ids}
        cleared_at = now()
        cleared = [_clear_gate(store, g, reason, cleared_at) for g in gate_ids]
        after, events = commands._commit_and_export(
            store,
            command=command,
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)
    lines: list[str] = []
    for item in cleared:
        lines.append(f"cleared {item['gate_id']} on {item['record_id']} ({item['gate_type']})")
        if item["flag"] is not None:
            lines.append(f"  flagged {item['flag']['flag_id']}  {item['flag']['flag_type']}")
            lines.append(f"    {item['flag']['detail']}")
    flags = sum(1 for item in cleared if item["flag"] is not None)
    lines.append(
        f"{len(cleared)} gate(s) cleared in one transaction; {flags} raised "
        f"{FLAG_TYPE_GATE_CLEARED_STALE_CRITERIA}"
    )
    if reason is not None:
        lines += echo_block("why", reason)
    return Output(
        lines,
        {
            "ok": True,
            "command": "ungate",
            "applied": True,
            "gate_ids": [item["gate_id"] for item in cleared],
            "cleared": cleared,
            "why": reason,
            "records": [after[rid] for rid in record_ids],
        },
    )


def _ungate_by_tag(ctx: Context, store, tag: str, reason: str | None, apply: bool) -> Output:
    """``kyo ungate --tag T [--apply]`` (KYO-492.c2): every OPEN gate carrying ``T``.

    Dry run by default — law 11's habit, as `kyo flag collapse` has it: the selection is
    a pure read and nothing is written without ``--apply``. The tag is refused against
    ``kyosei/gate-tags.yml`` first, exactly as `kyo list --gate-tag` refuses it, because
    an empty selection for a typo would read as "nothing is waiting on that"."""
    registry_module.load_gate_tag_registry(store.config.kyosei_dir).require(tag)
    selected = []
    for row in store.conn.execute(
        "SELECT id, record_id, gate_type, detail, tags_json FROM gates "
        "WHERE cleared_at IS NULL ORDER BY record_id, seq"
    ):
        if tag in (json.loads(row["tags_json"]) if row["tags_json"] else []):
            selected.append(
                {
                    "gate_id": row["id"],
                    "record_id": row["record_id"],
                    "gate_type": row["gate_type"],
                    "detail": row["detail"],
                }
            )
    if apply and selected:
        return _ungate_many(
            ctx,
            store,
            [item["gate_id"] for item in selected],
            reason,
            command=f"ungate --tag {tag} --apply",
        )
    lines = [
        f"{item['gate_id']} on {item['record_id']} ({item['gate_type']})"
        + (f": {flatten(item['detail'])}" if item["detail"] else "")
        for item in selected
    ]
    if not selected:
        lines.append(f"no open gate carries the tag {tag!r} — nothing to clear")
    elif not apply:
        lines.append(
            f"{len(selected)} open gate(s) tagged {tag!r}; nothing was written — re-run "
            "with --apply to clear them all in one transaction"
        )
    data = {
        "ok": True,
        "command": "ungate",
        "applied": False,
        "tag": tag,
        "gates": selected,
        "why": reason,
    }
    if not apply:
        data["dry_run_note"] = "nothing was written; re-run with --apply to clear these gates"
    return Output(lines, data)
