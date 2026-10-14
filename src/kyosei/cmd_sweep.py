"""``kyo sweep`` — the staleness pass over every active record's touches.

The command half: it reads the store, asks :mod:`kyosei.sweep` (the engine, which works
on plain dicts) what went stale, and writes the flags and the sweep run.
"""

from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass, field

from . import commands, events_ledger, export, kinds, ledgers
from . import sweep as sweep_module
from .clock import now
from .commands import (
    _UNMET_CRITERION_STATES,
    Context,
    Output,
    _active_statuses,
    _load_registry,
    _touch_record,
)
from .contracts import (
    FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM,
    FLAG_TYPE_NEAR_DUPLICATE_CRITERIA,
    FLAG_TYPE_TOUCH_CHANGED,
    FLAG_TYPE_TOUCH_MISSING,
    touch_target,
)
from .git_queries import (
    _git_commit_exists,
    _git_diff_added_paths,
    _git_diff_hunks,
    _git_diff_paths,
    _git_file_at,
    _git_head_or_none,
    _git_history_identity,
)
from .render import sweep_text
from .store import Store


@dataclass(frozen=True)
class _SweptRecord:
    id: str
    touches: list[str]
    criteria: list[tuple[str, str, str]]  # (id, text, state)
    #: KYO-300.c1: ``declared path -> the symbols this record named on it``, with
    #: ``None`` for a plain-path touch. Carried beside ``touches`` rather than folded
    #: into it because the file-level question is answered first and unchanged, and the
    #: symbols only refine an answer it already gave — same shape and same reason as
    #: ``commands._touch_symbols``, which the disjointness check has read since KYO-147.
    symbols: dict[str, list[str | None]] = field(default_factory=dict)


def _swept_active_records(store: Store, registry: kinds.KindRegistry) -> list[_SweptRecord]:
    """Every record `kyo sweep`'s three flag classes may touch: archetype
    ``work``, ``policy.swept`` true — the field's promised full consumer
    (schema.py's ``touches`` COLUMN_CONSUMERS entry) — and status still active.
    """
    active = set(_active_statuses(store))
    out: list[_SweptRecord] = []
    for row in store.conn.execute("SELECT id, kind, status FROM records ORDER BY seq"):
        if row["status"] not in active:
            continue
        kind_def = registry.get(row["kind"])
        if (
            kind_def is None
            or kind_def.archetype != "work"
            or not kind_def.policy.get("swept", False)
        ):
            continue
        touch_rows = list(store.touches(row["id"]))
        touches = [t["path"] for t in touch_rows]
        symbols: dict[str, list[str | None]] = {}
        for touch in touch_rows:
            symbols.setdefault(touch["path"], []).append(touch["symbol"] or None)
        criteria = [(c["id"], c["text"], c["state"]) for c in store.criteria(row["id"])]
        out.append(
            _SweptRecord(
                id=row["id"], touches=touches, criteria=criteria, symbols=symbols
            )
        )
    return out


def _existing_unresolved_details(store: Store, record_id: str, flag_type: str) -> set[str]:
    return {
        row["detail"]
        for row in store.conn.execute(
            "SELECT detail FROM flags WHERE record_id = ? AND flag_type = ?"
            " AND resolved_at IS NULL",
            (record_id, flag_type),
        )
    }


def _resolved_details(store: Store, record_id: str, flag_type: str) -> set[str]:
    """The details of every RESOLVED flag of one type on one record (KYO-529)."""
    return {
        row["detail"]
        for row in store.conn.execute(
            "SELECT detail FROM flags WHERE record_id = ? AND flag_type = ?"
            " AND resolved_at IS NOT NULL",
            (record_id, flag_type),
        )
    }


def _unresolved_flags_of_type(store: Store, record_id: str, flag_type: str) -> list[dict]:
    """Every unresolved flag of one type on one record, oldest first (KYO-69).

    Oldest first because supersession always widens the EARLIEST open flag: the target
    has to be stable across runs, or a controller who noted a flag id yesterday finds it
    resolved and a different one carrying the same finding today.
    """
    return [
        dict(row)
        for row in store.conn.execute(
            "SELECT * FROM flags WHERE record_id = ? AND flag_type = ?"
            " AND resolved_at IS NULL ORDER BY seq",
            (record_id, flag_type),
        )
    ]


def cmd_sweep(ctx: Context, args: Namespace) -> Output:
    """``kyo sweep`` (KYO-8.c1/c2/c3) — flags, never edits (charter: "flags,
    never auto-edits"). Four flag classes: touch-changed (git diff since the
    last sweep intersects a swept record's declared touches, and this run's own
    moment is recorded for the NEXT diff — KYO-8.c1), touch-missing (a declared
    touch no longer resolves to a real file — KYO-8.c2),
    near-duplicate-criteria (unmet criterion text collides across different
    active records — KYO-8.c3), and description-predates-claim (a record's
    description was last edited before one of its live claims was recorded —
    KYO-137.c7). The charter's other sweep classes are not built here;
    see docs/design/review.md#kyo-8.

    KYO-63 — when it cannot resolve its own recorded base, it **flags nothing for that
    class and says so, durably**, on ``sweep_runs.coverage``/``coverage_detail``, in
    ``--json``, in the text below and in `kyo brief`. The three other flag classes still
    run — they never needed the base sha. Never refuse and never fall back to a full
    scan, and never stay silent: no flags is byte-identical to a clean project. See
    docs/design/review.md#kyo-63.

    KYO-69 — when a record it would flag for ``touch-changed`` already carries an
    unresolved one, it WIDENS that flag (same start sha, new end sha, union of files,
    sweep count + 1) instead of filing another, so the flag count tracks problems rather
    than sweeps. See docs/design/review.md#kyo-69.
    """
    store = ctx.store()
    registry = _load_registry(store)
    swept = _swept_active_records(store, registry)

    # KYO-414: everything this run decides -- the prior run it diffs
    # from, the open flags it would widen, the ones it would not re-file -- is a read of
    # the store, and the write that acts on it opens later. So the plan records a
    # fingerprint of those reads, and inside the write transaction the fingerprint is
    # read again: unchanged, the plan stands; changed, the plan is recomputed there,
    # under the lock, where no other writer can move it. See
    # docs/design/review.md#kyo-414.
    def _fingerprint() -> tuple:
        latest = store.latest_sweep_run()
        return (
            latest["id"] if latest is not None else None,
            tuple(
                (row["id"], row["detail"])
                for row in store.conn.execute(
                    "SELECT id, detail FROM flags WHERE resolved_at IS NULL ORDER BY id"
                )
            ),
        )

    def _plan() -> dict:
        fingerprint = _fingerprint()
        # KYO-54: sweep's staleness diff is over *code*, so both shas and the diff
        # between them come from the code repository — the store's own history is a
        # record of records, and diffing it here would flag every touch on every run
        # under the split layout.
        current_sha = _git_head_or_none(ctx.code_root)
        # KYO-63: and *which* code repository, not only which sha. The bookkeeping lives in
        # the store repo; under the split layout that is a different repository, so the
        # history a recorded base belongs to has to be recorded with it or a later clone
        # cannot tell "nothing changed" from "I have never heard of this commit".
        current_repo_id = _git_history_identity(ctx.code_root) if current_sha is not None else None
        prior_run = store.latest_sweep_run()
        prior_sha = prior_run["base_sha"] if prior_run is not None else None
        prior_repo_id = prior_run["code_repo_id"] if prior_run is not None else None

        verdict = sweep_module.classify_staleness_base(
            current_sha=current_sha,
            current_repo_id=current_repo_id,
            prior_sha=prior_sha,
            prior_repo_id=prior_repo_id,
            base_resolves=(
                _git_commit_exists(ctx.code_root, prior_sha)
                if current_sha is not None and prior_sha is not None
                else False
            ),
        )
        coverage, coverage_detail = verdict.coverage, verdict.detail

        changed_files: set[str] | None = None
        created_files: set[str] | None = None
        # `classify_staleness_base` only returns "swept" with both shas present and the base
        # checked, so the two `is not None` tests are the type system catching up with it.
        if (
            sweep_module.staleness_answered(coverage)
            and current_sha is not None
            and prior_sha is not None
        ):
            changed_files = _git_diff_paths(ctx.code_root, prior_sha, current_sha)
            # KYO-225.c3: which of them were CREATED in this window, so a `+path` touch is
            # not flagged by the commit that fulfils it. `None` (git could not answer)
            # suppresses nothing, which is what this check did before the marker existed.
            created_files = _git_diff_added_paths(ctx.code_root, prior_sha, current_sha)
            if changed_files is None:
                coverage = sweep_module.SWEEP_COVERAGE_DIFF_FAILED
                coverage_detail = sweep_module.diff_failed_detail(prior_sha, current_sha)
            else:
                coverage_detail = sweep_module.swept_detail(
                    prior_sha, current_sha, caveat=coverage_detail
                )

        # record_id -> [(flag_type, detail, other_record_id, other_criterion_id)]
        new_flags: dict[str, list[tuple[str, str, str | None, str | None]]] = {}
        unresolved_cache: dict[tuple[str, str], set[str]] = {}

        def already_flagged(record_id: str, flag_type: str, detail: str) -> bool:
            key = (record_id, flag_type)
            if key not in unresolved_cache:
                unresolved_cache[key] = _existing_unresolved_details(store, record_id, flag_type)
            return detail in unresolved_cache[key]

        def add(
            record_id: str,
            flag_type: str,
            detail: str,
            *,
            other_record_id: str | None = None,
            other_criterion_id: str | None = None,
        ) -> None:
            if already_flagged(record_id, flag_type, detail):
                return
            new_flags.setdefault(record_id, []).append(
                (flag_type, detail, other_record_id, other_criterion_id)
            )
            unresolved_cache[(record_id, flag_type)].add(detail)

        # KYO-69: (flag_id, record_id, new_detail, flag_type, prior_detail) for open
        # findings this run re-observed. They are UPDATED, not re-filed — see the module
        # comment in sweep.py. KYO-418.c9: touch-missing and description-predates-claim
        # supersede too, not only touch-changed (docs/design/review.md#kyo-418).
        widened: list[tuple[str, str, str, str, str]] = []

        def supersede_or_add(record_id: str, flag_type: str, detail: str) -> None:
            """KYO-418.c9: one open flag per (record, flag_type) for a class whose
            finding is re-derived whole each run. The standing flag (the EARLIEST open
            one, as KYO-69 chose for touch-changed) takes the current finding in place
            when it differs; with none standing, a new flag is filed."""
            open_flags = _unresolved_flags_of_type(store, record_id, flag_type)
            if not open_flags:
                add(record_id, flag_type, detail)
                return
            standing = open_flags[0]
            if standing["detail"] != detail and all(
                flag["detail"] != detail for flag in open_flags
            ):
                widened.append((standing["id"], record_id, detail, flag_type, standing["detail"]))

        # KYO-300.c1: the two lookups the symbol narrowing needs, memoised per sweep run,
        # so a file many records name is read and diffed once (docs/design/review.md#kyo-300).
        # Both are closures rather than arguments to
        # `sweep_module` because this is the layer that knows where the project is; the
        # module itself stays free of both the filesystem and git.
        hunk_cache: dict[str, list[sweep_module.DiffHunk] | None] = {}
        content_cache: dict[str, str | None] = {}
        region_cache: dict[tuple[str, str], list[sweep_module.LineRange] | None] = {}

        def _hunks_for(declared: str) -> list[sweep_module.DiffHunk] | None:
            if declared not in hunk_cache:
                assert prior_sha is not None and current_sha is not None
                hunk_cache[declared] = _git_diff_hunks(
                    ctx.code_root, [prior_sha, current_sha], touch_target(declared)
                )
            return hunk_cache[declared]

        def _regions_for(declared: str, symbol: str) -> list[sweep_module.LineRange] | None:
            key = (declared, symbol)
            if key in region_cache:
                return region_cache[key]
            path = touch_target(declared)
            if path not in content_cache:
                # KYO-406.c1: the hunks are `prior_sha..current_sha`, so their new-side line
                # numbers belong to `current_sha`'s copy of the file, and the regions must be
                # placed in that copy. The working tree is the wrong file: an uncommitted
                # edit above the symbol moves its region and a committed change inside it
                # narrows away unflagged, while the sweep base advances past this window.
                assert current_sha is not None
                # KYO-406.c2: unreadable at `current_sha` (deleted there, or git failed) is
                # unplaceable, and unplaceable counts as changed.
                content_cache[path] = _git_file_at(ctx.code_root, current_sha, path)
            content = content_cache[path]
            region_cache[key] = (
                None if content is None else sweep_module.symbol_regions(content, symbol)
            )
            return region_cache[key]

        if changed_files:
            for rec in swept:
                hit = sweep_module.changed_touch_paths(
                    rec.touches, changed_files, created=created_files
                )
                if not hit:
                    continue
                # KYO-300.c1. The file-level answer above is unchanged and is never
                # recomputed here; this only asks, of a path every one of whose touches
                # named a symbol, whether the window's diff reached any of those symbols'
                # regions. A path that narrows away is dropped from `hit` — and a record
                # whose whole `hit` narrows away raises nothing at all, which is the point.
                hit, _narrowed_away = sweep_module.narrow_changed_by_symbol(
                    hit, rec.symbols, regions_for=_regions_for, hunks_for=_hunks_for
                )
                if not hit:
                    continue
                # KYO-69.c1: one record, one open touch-changed finding. A later sweep that
                # sees the same condition widens the flag already standing rather than
                # stacking another identical-in-meaning row on top of it.
                open_flags = _unresolved_flags_of_type(store, rec.id, FLAG_TYPE_TOUCH_CHANGED)
                existing = open_flags[0] if open_flags else None
                finding = (
                    sweep_module.parse_touch_changed_detail(existing["detail"])
                    if existing is not None
                    else None
                )
                if existing is not None and finding is not None:
                    new_detail = sweep_module.format_touch_changed_detail(
                        finding.widened(to_sha=current_sha, paths=hit)
                    )
                    if new_detail != existing["detail"]:
                        widened.append(
                            (
                                existing["id"],
                                rec.id,
                                new_detail,
                                FLAG_TYPE_TOUCH_CHANGED,
                                existing["detail"],
                            )
                        )
                    continue
                # No open flag, or one whose text this build does not recognise. The second
                # case files a new flag on purpose: rewriting a detail we could not parse
                # would destroy a finding nobody has read yet, and two visible flags are
                # recoverable where a silent overwrite is not.
                detail = sweep_module.format_touch_changed_detail(
                    sweep_module.TouchChangedFinding(
                        from_sha=prior_sha, to_sha=current_sha, paths=tuple(hit), sweeps=1
                    )
                )
                add(rec.id, FLAG_TYPE_TOUCH_CHANGED, detail)

        for rec in swept:
            missing = sweep_module.missing_touch_paths(ctx.code_root, rec.touches)
            if missing:
                detail = "declared touch(es) no longer exist in the repo: " + ", ".join(missing)
                supersede_or_add(rec.id, FLAG_TYPE_TOUCH_MISSING, detail)

        criteria_pool = [
            sweep_module.CriterionText(record_id=rec.id, criterion_id=cid, text=text)
            for rec in swept
            for cid, text, state in rec.criteria
            if state in _UNMET_CRITERION_STATES
        ]
        for match in sweep_module.find_near_duplicate_criteria(criteria_pool):
            detail = sweep_module.format_near_duplicate_detail(match)
            # KYO-532: a pair a human resolved stays resolved until either text moves
            # (the detail carries both texts and both ids).
            if sweep_module.judged_already(
                detail,
                _resolved_details(store, match.record_a, FLAG_TYPE_NEAR_DUPLICATE_CRITERIA),
            ):
                continue
            add(
                match.record_a,
                FLAG_TYPE_NEAR_DUPLICATE_CRITERIA,
                detail,
                other_record_id=match.record_b,
                other_criterion_id=match.criterion_b,
            )

        # KYO-137.c7. The one flag class in this command that reads no repository at all:
        # both halves of the comparison are in the store. It is therefore also the one
        # class unaffected by `coverage` above: a sweep that could not resolve its own base
        # sha still answers this question completely (docs/design/review.md#kyo-137).
        claims_pool = [
            sweep_module.RecordClaims(
                record_id=rec.id,
                description_at=store.description_edited_at(rec.id),
                claims=tuple(
                    (row["id"], row["created_at"], row["statement"])
                    for row in store.live_claims(rec.id)
                ),
            )
            for rec in swept
        ]
        for stale in sweep_module.find_descriptions_predating_their_claims(claims_pool):
            detail = sweep_module.format_description_predates_claim_detail(stale)
            # KYO-529: a finding a human resolved stays resolved until it moves. With an
            # open flag standing, supersession below already handles the change.
            if not _unresolved_flags_of_type(
                store, stale.record_id, FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM
            ) and sweep_module.judged_already(
                detail,
                _resolved_details(store, stale.record_id, FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM),
            ):
                continue
            supersede_or_add(stale.record_id, FLAG_TYPE_DESCRIPTION_PREDATES_CLAIM, detail)

        return {
            "fingerprint": fingerprint,
            "current_sha": current_sha,
            "current_repo_id": current_repo_id,
            "prior_sha": prior_sha,
            "coverage": coverage,
            "coverage_detail": coverage_detail,
            "new_flags": new_flags,
            "widened": widened,
        }

    plan = _plan()
    unchanged_since: str | None = None
    with store.transaction():
        if _fingerprint() != plan["fingerprint"]:
            plan = _plan()
        current_sha, current_repo_id = plan["current_sha"], plan["current_repo_id"]
        prior_sha = plan["prior_sha"]
        coverage, coverage_detail = plan["coverage"], plan["coverage_detail"]
        new_flags, widened = plan["new_flags"], plan["widened"]
        total_flags = sum(len(items) for items in new_flags.values())
        # KYO-418.c5: a widening changes one flag's detail, so it is logged as a
        # flag-level event and the record's `updated_at` does not move; only a record
        # that gained a NEW flag gets a record-document event and a touch. Its export is
        # still rewritten (the flag's detail is in the document, which is what a
        # rebuild reads flags from), so both sets are exported.
        touched_record_ids = list(new_flags)
        exported_record_ids = list(
            dict.fromkeys(touched_record_ids + [item[1] for item in widened])
        )
        prior_run = store.latest_sweep_run()
        if (
            prior_run is not None
            and not new_flags
            and not widened
            and (prior_run["base_sha"], prior_run["code_repo_id"], prior_run["coverage"])
            == (current_sha, current_repo_id, coverage)
        ):
            # KYO-418.c8: a run at the base the last recorded run
            # already swept, finding nothing new and widening nothing, records NOTHING:
            # nothing this run could say differs from what that row says, and this
            # command runs on every Stop (docs/design/review.md#kyo-418). Decided inside
            # the write transaction, before an id is allocated, so no counter moves.
            unchanged_since = str(prior_run["id"])
        else:
            sweep_run_id, run_seq = store.new_sweep_run_id()
            stamp = now()
            store.conn.execute(
                "INSERT INTO sweep_runs (id, seq, at, actor, base_sha, code_repo_id,"
                " coverage, coverage_detail, flags_created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sweep_run_id,
                    run_seq,
                    stamp,
                    ctx.actor,
                    current_sha,
                    current_repo_id,
                    coverage,
                    coverage_detail,
                    total_flags,
                ),
            )
            sweep_run_row = store.conn.execute(
                "SELECT * FROM sweep_runs WHERE id = ?", (sweep_run_id,)
            ).fetchone()

            before = {rid: export.record_document(store, rid) for rid in touched_record_ids}
            updated: list[dict] = []
            flag_events: list[dict] = []
            for flag_id, record_id, new_detail, flag_type, prior_detail in widened:
                store.conn.execute(
                    "UPDATE flags SET detail = ? WHERE id = ?", (new_detail, flag_id)
                )
                updated.append(
                    {
                        "flag_id": flag_id,
                        "record_id": record_id,
                        "flag_type": flag_type,
                        "detail": new_detail,
                    }
                )
                # KYO-418.c5: the event is the FLAG's, not the record's -- one detail
                # moved, and that is all it says (`after` plus `changed: {detail}`); the
                # record is neither re-logged whole nor touched
                # (docs/design/review.md#kyo-418).
                flag_events.append(
                    store.record_event(
                        actor=ctx.actor,
                        command=f"sweep {record_id}",
                        record_id=record_id,
                        before={"flag_id": flag_id, "flag_type": flag_type, "detail": prior_detail},
                        after={"flag_id": flag_id, "flag_type": flag_type, "detail": new_detail},
                    )
                )
            created: list[dict] = []
            for record_id, items in new_flags.items():
                for flag_type, detail, other_record_id, other_criterion_id in items:
                    flag_id = commands.insert_flag(
                        store,
                        record_id,
                        flag_type,
                        detail,
                        other_record_id=other_record_id,
                        other_criterion_id=other_criterion_id,
                        sweep_run_id=sweep_run_id,
                    )
                    created.append(
                        {
                            "flag_id": flag_id,
                            "record_id": record_id,
                            "flag_type": flag_type,
                            "detail": detail,
                        }
                    )
                _touch_record(store, record_id)

            # KYO-8: a recorded sweep logs its own event, even at zero flags, so the
            # audit log shows a sweep happened, not just its side effects. KYO-418.c8
            # narrowed "every run" to "every run with something to record" above.
            events = [
                store.record_event(
                    actor=ctx.actor,
                    command="sweep",
                    record_id=None,
                    before=None,
                    after={
                        "sweep_run_id": sweep_run_id,
                        "base_sha": current_sha,
                        # KYO-63: a run that created zero flags because it could not diff
                        # must not read, in the audit log either, like a run that created
                        # zero flags because nothing was stale.
                        "coverage": coverage,
                        "flags_created": total_flags,
                        # KYO-69: a run that widened five standing findings and created none
                        # did work, and `flags_created: 0` alone reads as a quiet run. The
                        # sweep_runs row cannot say this without a schema bump; the audit
                        # log can and does.
                        "flags_widened": len(updated),
                    },
                )
            ]
            events += flag_events
            after: dict[str, dict] = {}
            for rid in touched_record_ids:
                after[rid] = export.record_document(store, rid)
                events.append(
                    store.record_event(
                        actor=ctx.actor,
                        command=f"sweep {rid}",
                        record_id=rid,
                        before=before[rid],
                        after=after[rid],
                    )
                )

    if unchanged_since is not None:
        # KYO-418.c8: nothing was written, so nothing is exported -- `git status
        # --porcelain kyosei/export` stays empty across a no-op Stop.
        data = {
            "sweep_run_id": None,
            "recorded": False,
            "unchanged_since": unchanged_since,
            "base_sha": current_sha,
            "prior_sha": prior_sha,
            "code_repo_id": current_repo_id,
            "coverage": coverage,
            "coverage_detail": coverage_detail,
            "checked": len(swept),
            "flags": [],
            "widened_flags": [],
        }
        return Output([sweep_text(data)], {"ok": True, "command": "sweep", **data})

    # KYO-404: the two append-only rows first — they are history, and a record
    # document or the counter snapshot is re-derivable by `kyo export --refresh`
    # while a row missing from its ledger is lost on the next rebuild.
    events_ledger.append_events(store, events)
    ledgers.append_sweep_run(store, sweep_run_row)
    if exported_record_ids:
        export.write_exports(store, exported_record_ids)
    ledgers.write_allocations(store)

    data = {
        "sweep_run_id": sweep_run_id,
        "recorded": True,
        "unchanged_since": None,
        "base_sha": current_sha,
        "prior_sha": prior_sha,
        # KYO-63: the third state, in the payload a machine reads as well as the text a
        # human reads — a binding that can only paint "stale"/"not stale" has nowhere to
        # put "the check could not run" (docs/design/review.md#kyo-63).
        "code_repo_id": current_repo_id,
        "coverage": coverage,
        "coverage_detail": coverage_detail,
        "checked": len(swept),
        "flags": created,
        # KYO-69.c1: the findings this run re-observed rather than re-filed. Named
        # separately from `flags` so a caller can tell "nothing is stale" from "everything
        # stale was already known", which is precisely the distinction the old
        # one-flag-per-sweep behaviour destroyed by making both look like new findings.
        "widened_flags": updated,
    }
    return Output([sweep_text(data)], {"ok": True, "command": "sweep", **data})
