"""``kyo flag`` — list, collapse and settle the flags a sweep or a check raised."""

from __future__ import annotations

from argparse import Namespace

from . import commands, export
from . import sweep as sweep_module
from .clock import now
from .commands import Context, Output, _load_status_ladder, _reason, _touch_record
from .contracts import (
    CONTRACT_FLAG_ALREADY_RESOLVED,
    FLAG_TYPE_TOUCH_CHANGED,
    SETTLED_ROLES,
    STALENESS_FLAG_TYPES,
)
from .errors import ContractViolation, UsageError
from .render import echo_block, flag_collapse_text, flag_line, flag_settle_text
from .store import Store


def _collapse_plan(store: Store) -> list[dict]:
    """KYO-69.c3: what `kyo flag collapse` would do, computed without writing anything.

    One entry per ``(record, flag_type)`` group that holds more than one unresolved,
    parseable flag — i.e. per record whose open finding has been re-filed. The survivor
    is the earliest flag in the group; the rest are the duplicates it absorbs.

    Groups are skipped whole if ANY member's detail does not parse. A group this build
    cannot read is a group it cannot merge losslessly, and merging losslessly is the only
    thing that makes closing the duplicates honest rather than a bulk resolve.

    Pure read: this is what makes the command dry-run-by-default and what makes the
    idempotence test cheap — a second run's plan is simply empty.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in store.conn.execute(
        "SELECT * FROM flags WHERE resolved_at IS NULL AND flag_type = ? ORDER BY record_id, seq",
        (FLAG_TYPE_TOUCH_CHANGED,),
    ):
        groups.setdefault((row["record_id"], row["flag_type"]), []).append(dict(row))

    plan: list[dict] = []
    for (record_id, flag_type), flags in sorted(groups.items()):
        if len(flags) < 2:
            continue
        findings = [sweep_module.parse_touch_changed_detail(f["detail"]) for f in flags]
        if any(f is None for f in findings):
            continue
        merged = sweep_module.merge_touch_changed_findings(
            [f for f in findings if f is not None]
        )
        survivor = flags[0]
        new_detail = sweep_module.format_touch_changed_detail(merged)
        plan.append(
            {
                "record_id": record_id,
                "flag_type": flag_type,
                "survivor_flag_id": survivor["id"],
                "survivor_detail_before": survivor["detail"],
                "survivor_detail_after": new_detail,
                "superseded_flag_ids": [f["id"] for f in flags[1:]],
            }
        )
    return plan


def _collapse_counts(store: Store) -> dict[str, int]:
    row = store.conn.execute(
        "SELECT COUNT(*) AS n FROM flags WHERE resolved_at IS NULL"
    ).fetchone()
    touch = store.conn.execute(
        "SELECT COUNT(*) AS n FROM flags WHERE resolved_at IS NULL AND flag_type = ?",
        (FLAG_TYPE_TOUCH_CHANGED,),
    ).fetchone()
    return {"unresolved": int(row["n"]), "unresolved_touch_changed": int(touch["n"])}


def cmd_flag_collapse(ctx: Context, args: Namespace) -> Output:
    """``kyo flag collapse [--apply]`` (KYO-69.c3) — the migration for the flags that
    accumulated before `kyo sweep` learned to supersede.

    What it does NOT do, deliberately: bulk-resolve. That would close findings no human
    ever read, and flags are a human's to judge (charter: "flags, never auto-edits";
    docs/design/review.md#kyo-69). What it does
    instead is merge each record's duplicates into the earliest one — earliest start sha,
    latest end sha, union of every file any of them named, total sweeps — so the surviving
    flag says everything all of them said, and then closes the now-redundant rows with a
    ``resolved_why`` that names the survivor and says in words that this is a duplicate
    closed by merging, not a judgement (``sweep.superseded_why``).

    Dry-run by default (law 11's habit, applied to the store's own rows): the plan is
    computed by a pure read and printed, and nothing is written without ``--apply``. It
    is idempotent because the plan is derived from the current state — after one apply,
    every group holds exactly one unresolved flag and the plan is empty — which matters
    because the store it runs against is being swept from a session hook while it runs.
    """
    store = ctx.store()
    plan = _collapse_plan(store)
    before_counts = _collapse_counts(store)
    apply = bool(getattr(args, "apply", False))
    would_close = sum(len(item["superseded_flag_ids"]) for item in plan)

    data = {
        "ok": True,
        "command": "flag collapse",
        "applied": apply,
        "plan": plan,
        "records_affected": len({item["record_id"] for item in plan}),
        "flags_superseded": would_close,
        "unresolved_before": before_counts["unresolved"],
        # Derived, never counted twice (law 3): a collapse closes exactly `would_close`
        # rows and creates none. On a dry run that makes this the projection; after an
        # apply it is re-read from the store below, so the two can never quietly disagree.
        "unresolved_after": before_counts["unresolved"] - would_close,
    }
    if not apply or not plan:
        if not apply:
            data["dry_run_note"] = (
                "nothing was written; re-run with --apply to collapse these groups"
            )
        return Output([flag_collapse_text(data)], data)

    record_ids = [item["record_id"] for item in plan]
    stamp = now()
    with store.transaction():
        before = {rid: export.record_document(store, rid) for rid in record_ids}
        for item in plan:
            store.conn.execute(
                "UPDATE flags SET detail = ? WHERE id = ?",
                (item["survivor_detail_after"], item["survivor_flag_id"]),
            )
            why = sweep_module.superseded_why(item["survivor_flag_id"])
            for flag_id in item["superseded_flag_ids"]:
                store.conn.execute(
                    "UPDATE flags SET resolved_at = ?, resolved_by = ?, resolved_why = ?"
                    " WHERE id = ?",
                    (stamp, ctx.actor, why, flag_id),
                )
            _touch_record(store, item["record_id"])
        after, events = commands._commit_and_export(
            store,
            command="flag collapse",
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)
    data["unresolved_after"] = _collapse_counts(store)["unresolved"]
    return Output([flag_collapse_text(data)], data)


def _settled_plan(store: Store) -> list[dict]:
    """KYO-346.c3: every open staleness flag (:data:`contracts.STALENESS_FLAG_TYPES`)
    sitting under a record already at a completion or retired status
    (:data:`contracts.SETTLED_ROLES`) — the debt `kyo flag resolve --settled` repairs,
    for a store that accumulated these before a record's own move learned to close
    its own (KYO-346.c1). Grouped per record and per type: "printing the count per
    record and per type" is what lets an operator spot-check the plan against
    `kyo show` before trusting an ``--apply``, the same caution `_collapse_plan`
    was built on.

    A pure read, exactly like `_collapse_plan` — the plan this migration prints and
    the plan it acts on are the same query, so the two can never quietly disagree.
    """
    ladder = _load_status_ladder(store)
    by_record: dict[str, dict] = {}
    rows = store.conn.execute(
        "SELECT flags.id AS flag_id, flags.record_id AS record_id, "
        "flags.flag_type AS flag_type, records.status AS status "
        "FROM flags JOIN records ON records.id = flags.record_id "
        "WHERE flags.resolved_at IS NULL "
        "ORDER BY flags.record_id, flags.seq"
    ).fetchall()
    for row in rows:
        if row["flag_type"] not in STALENESS_FLAG_TYPES:
            continue
        if ladder.role(row["status"]) not in SETTLED_ROLES:
            continue
        entry = by_record.setdefault(
            row["record_id"],
            {
                "record_id": row["record_id"],
                "status": row["status"],
                "flag_ids": [],
                "by_type": {},
            },
        )
        entry["flag_ids"].append(row["flag_id"])
        entry["by_type"][row["flag_type"]] = entry["by_type"].get(row["flag_type"], 0) + 1
    return [by_record[rid] for rid in sorted(by_record)]


def cmd_flag_settle(ctx: Context, args: Namespace) -> Output:
    """``kyo flag resolve --settled --why <reason>`` (KYO-346.c3) — closes every open
    staleness flag under a record already at a completion or retired status
    (docs/design/review.md#kyo-346).

    One act across the whole store, on `kyo flag collapse`'s own terms: a plan
    computed by a pure read (:func:`_settled_plan`), printed and acted on
    identically, dry-run by default, nothing written without ``--apply``. Unlike
    ``collapse`` (which merges duplicates nobody has to judge) this *does* close
    findings — every one of them under a record that has already finished or been
    retired, which is why it demands ``--why``: an operator repairing accumulated
    debt in bulk, not a human judging one finding, and the reason belongs to the
    operator's act rather than to any one flag.
    """
    store = ctx.store()
    reason = _reason(args)
    if reason is None:
        raise UsageError(
            "`kyo flag resolve --settled` needs a reason: pass --why or --why-file — "
            "this closes flags nobody judged individually, on an operator's say-so"
        )
    plan = _settled_plan(store)
    before_counts = _collapse_counts(store)
    apply = bool(getattr(args, "apply", False))
    flag_ids = [flag_id for item in plan for flag_id in item["flag_ids"]]
    by_type: dict[str, int] = {}
    for item in plan:
        for flag_type, n in item["by_type"].items():
            by_type[flag_type] = by_type.get(flag_type, 0) + n

    data = {
        "ok": True,
        "command": "flag resolve --settled",
        "applied": apply,
        "plan": plan,
        "records_affected": len(plan),
        "flags_closed": len(flag_ids),
        "by_type": by_type,
        "unresolved_before": before_counts["unresolved"],
        # Derived, never counted twice (law 3), exactly as `cmd_flag_collapse` does.
        "unresolved_after": before_counts["unresolved"] - len(flag_ids),
    }
    if not apply or not plan:
        if not apply:
            data["dry_run_note"] = (
                "nothing was written; re-run with --apply to close these flags"
            )
        return Output([flag_settle_text(data)], data)

    record_ids = [item["record_id"] for item in plan]
    stamp = now()
    with store.transaction():
        before = {rid: export.record_document(store, rid) for rid in record_ids}
        for flag_id in flag_ids:
            store.conn.execute(
                "UPDATE flags SET resolved_at = ?, resolved_by = ?, resolved_why = ?"
                " WHERE id = ?",
                (stamp, ctx.actor, reason, flag_id),
            )
        for rid in record_ids:
            _touch_record(store, rid)
        after, events = commands._commit_and_export(
            store,
            command="flag resolve --settled",
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)
    data["unresolved_after"] = _collapse_counts(store)["unresolved"]
    return Output([flag_settle_text(data)], data)


def cmd_flag_list(ctx: Context, args: Namespace) -> Output:
    """``kyo flag list [--all]`` — the read path for flags (KYO-220.c1; the brief is a
    ranked rendering of them, not a way to find one — docs/design/review.md#kyo-220).

    Read-only: no transaction is opened here and no row is written, so running it twice
    changes nothing. Open-only by default and `--all` for the rest, because a listing
    of what is still owed a judgment and a listing of everything that ever was are two
    different questions and the first is the one asked at a terminal.

    Store order — ``(record_id, seq)`` — and deliberately not `kyo brief`'s ranking.
    The brief ranks by :func:`kyosei.sweep.touch_changed_scope`, which is a fact about
    the RECORD (has it ever been dispatched) rather than about the flag, computed by the
    brief from a pass over the board that this command does not make. Re-deriving it
    here would be a second implementation of the brief's judgment, and asserting it
    without deriving it would be worse; what this listing is for is finding a flag by
    its id, which is the thing no command could do.
    """
    store = ctx.store()
    rows = (
        list(store.conn.execute("SELECT * FROM flags ORDER BY record_id, seq"))
        if args.all
        else store.unresolved_flags()
    )
    flags = [{**export._flag(row), "record_id": row["record_id"]} for row in rows]
    open_flags = [flag for flag in flags if flag["resolved_at"] is None]
    scope = "every flag" if args.all else "open flags"
    lines = [f"{scope}: {len(open_flags)} open of {len(flags)}"]
    lines += [flag_line(flag) for flag in flags]
    if not flags:
        # Law 6: "(none)" with no way to get from there to one is a dead end, and an
        # empty flag list has two causes a reader cannot tell apart by looking.
        lines.append(
            "  (none) — `kyo sweep` raises flags; `kyo flag list --all` includes the "
            "ones already resolved"
        )
    else:
        lines.append(f"  one flag in full: `kyo show {flags[0]['id']}`")
    return Output(
        lines,
        {
            "ok": True,
            "command": "flag list",
            "all": bool(args.all),
            "open": len(open_flags),
            "flags": flags,
        },
    )


def cmd_flag(ctx: Context, args: Namespace) -> Output:
    """``kyo flag resolve <flag-id> [--why]`` — the acknowledge/resolve path on a sweep
    flag (docs/design/review.md#kyo-8). Records who resolved it and when; never
    re-runs the condition that produced the flag, and never edits the record the flag
    concerns — sweep flags, resolving only records that a human/controller looked at it.

    KYO-69 adds ``kyo flag collapse`` beside it — see :func:`cmd_flag_collapse`. KYO-346
    adds ``--settled`` to ``resolve`` itself rather than a third action — see
    :func:`cmd_flag_settle` — because it is still a *resolve*, not a new way for a flag
    to leave the unresolved set (docs/design/review.md#kyo-346).
    """
    settled = bool(getattr(args, "settled", False))
    if args.action == "collapse":
        if args.flag_id is not None:
            raise UsageError(
                "`kyo flag collapse` takes no flag id — it collapses every record's "
                "duplicate touch-changed flags at once; run it without an argument"
            )
        if settled:
            raise UsageError("--settled applies to `kyo flag resolve`, not `kyo flag collapse`")
        return cmd_flag_collapse(ctx, args)
    if args.action == "list":
        if args.flag_id is not None:
            raise UsageError(
                "`kyo flag list` takes no flag id — it lists the project's flags; one "
                f"flag in full is `kyo show {args.flag_id}`"
            )
        if settled:
            raise UsageError("--settled applies to `kyo flag resolve`, not `kyo flag list`")
        return cmd_flag_list(ctx, args)
    if args.action != "resolve":
        raise UsageError(
            f"unknown `kyo flag` action {args.action!r}; only 'list', 'resolve' and "
            "'collapse' exist"
        )
    if settled:
        if args.flag_id is not None:
            raise UsageError(
                "`kyo flag resolve --settled` takes no flag id — it resolves every open "
                "staleness flag under a record already at a completion or retired status "
                "at once; run it without a flag id"
            )
        return cmd_flag_settle(ctx, args)
    if args.flag_id is None:
        raise UsageError("`kyo flag resolve` needs a flag id, e.g. `kyo flag resolve KYO-1.f1`")
    store = ctx.store()
    reason = _reason(args)
    # KYO-528.c1: several ids are one act with one --why. Distinct, first-named order.
    flag_ids: list[str] = []
    for named in [args.flag_id, *(getattr(args, "more_flag_ids", None) or [])]:
        if named not in flag_ids:
            flag_ids.append(named)
    with store.transaction():
        # Every id is resolved and checked before a single row is written, so one
        # unknown or already-resolved member refuses the whole call.
        flags = [store.flag(flag_id) for flag_id in flag_ids]
        already = [f for f in flags if f["resolved_at"]]
        if already:
            lines = [
                f"{f['id']} was already resolved at {f['resolved_at']} by "
                f"{f['resolved_by']!r}"
                for f in already
            ]
            if len(flags) > 1:
                lines.append(
                    f"Nothing was resolved: {len(already)} of the {len(flags)} flags named "
                    "were already resolved. Name only the open ones — `kyo flag list` "
                    "lists them"
                )
            raise ContractViolation(CONTRACT_FLAG_ALREADY_RESOLVED, lines)
        record_ids: list[str] = []
        for flag in flags:
            if flag["record_id"] not in record_ids:
                record_ids.append(flag["record_id"])
        before = {rid: export.record_document(store, rid) for rid in record_ids}
        stamp = now()
        for flag_id in flag_ids:
            store.conn.execute(
                "UPDATE flags SET resolved_at = ?, resolved_by = ?, resolved_why = ?"
                " WHERE id = ?",
                (stamp, ctx.actor, reason, flag_id),
            )
        for rid in record_ids:
            _touch_record(store, rid)
        after, events = commands._commit_and_export(
            store,
            command=f"flag resolve {' '.join(flag_ids)}",
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)
    lines = [
        f"resolved {flag['id']} on {flag['record_id']} ({flag['flag_type']})" for flag in flags
    ]
    if len(flags) > 1:
        lines.insert(0, f"resolved {len(flags)} flags in one act:")
    if reason is not None:
        lines += echo_block("why", reason)
    return Output(
        lines,
        {
            "ok": True,
            "command": "flag resolve",
            "flag_id": flag_ids[0],
            "record_id": record_ids[0],
            "record": after[record_ids[0]],
            "flag_ids": flag_ids,
            "resolved": len(flag_ids),
            "records": [after[rid] for rid in record_ids],
        },
    )
