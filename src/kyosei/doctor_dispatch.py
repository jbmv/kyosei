"""Doctor checks on the events ledger, dispatches, stops, reports and the back-links a
split or restate writes. Registered in :data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import json
import re

from . import currency, events_ledger, ledger_repair
from .contracts import (
    DISPATCH_STATE_REPORTED,
    DISPATCH_STATE_RUNNING,
    DISPATCH_STATES,
    DISPATCH_STATES_WITHOUT_A_REPORT,
    RESTATE_REASON_RE,
    report_amends,
)
from .store import Store

#: KYO-268.c3: the one sentence naming the migration, so this check and `kyo export`'s
#: own usage error cannot come to name two different commands (law 3).
EVENTS_MIGRATION_REMEDY = (
    "Run `kyo export --migrate-events` to rewrite it as bounded shards under "
    "kyosei/export/_events/ and remove the old file"
)


def check_events_ledger_layout(store: Store) -> list[str]:
    """KYO-268.c3: an export still carrying the monolithic ``_events.jsonl``.

    **A NOTICE, and the bar is met the way ``ungated_unobserved_criteria``'s is.** The
    old layout is read correctly — ``events_ledger.events_files_in`` reads the monolithic file
    first and then every shard, which is what "accept either layout during the
    transition" means — so nothing here is broken and no command is refused. What the old
    layout costs is paid to git rather than to the tool: every commit that touches the
    store re-stores that whole file as a new blob, and git's periodic repack then
    delta-searches its versions against each other (docs/design/doctor.md#kyo-268).
    Failing on it would paint every hook run red over a state that is correct until
    somebody chooses to migrate; saying nothing leaves a consumer to diagnose a huge
    ``.git`` from scratch.

    The finding names the file, its measured size, and the command that clears it — and
    ``tests/test_export.py`` proves the remedy clears it, which is this project's
    standing rule for any message that names one.
    """
    path = events_ledger.events_path(store)
    if not path.is_file():
        return []
    megabytes = path.stat().st_size / (1024 * 1024)
    return [
        f"{path} is the pre-KYO-268 monolithic events ledger ({megabytes:.1f} MB). It "
        "still reads correctly and nothing is refused, but git re-stores the whole file "
        "as a new object on every commit that touches the store, and a later repack "
        "delta-searches those copies against each other, which on a large ledger can "
        f"exhaust the machine's memory. {EVENTS_MIGRATION_REMEDY}."
    ]


def check_events(store: Store) -> list[str]:
    """Every record must appear in the audit log at least once."""
    logged = {
        row["record_id"]
        for row in store.conn.execute("SELECT DISTINCT record_id FROM events")
        if row["record_id"]
    }
    return [
        f"{record_id} has no events row; its history is unaccounted for"
        for record_id in store.all_record_ids()
        if record_id not in logged
    ]


LEDGER_REPAIR_REMEDY = (
    "Run `kyo export --refresh`: it re-appends every missing row from the database, in "
    "seq order and in the form the original append would have written, and reports how "
    "many it appended"
)


def check_ledger_gaps(store: Store) -> list[str]:
    """KYO-404: every row in ``events`` / ``sweep_runs`` / ``brief_runs`` /
    ``shift_notes`` is in its ledger, and every ledger line has its row.

    The ledgers are appended after COMMIT, so an interrupted command can leave a row
    in the database that no ledger carries. ``kyo rebuild`` reads only the ledgers, so
    that row disappears on the next rebuild — silently, since nothing else compares
    the two: ``check_events`` asks only that each record has *some* event, and
    ``check_export_roundtrip`` compares documents and allocations. The currency check
    does not see it either once any later command has rewritten the counters.

    Compares id sets only (``ledger_repair.ledger_gaps``), never bodies, so it is affordable
    from ``doctor --hook``. The two directions have different remedies: a row only in
    the database is re-appended by ``kyo export --refresh``; a line only in the ledger
    is the export being ahead of the database, which a rebuild loads and a refresh
    never removes.
    """
    findings: list[str] = []
    try:
        gaps = ledger_repair.ledger_gaps(store)
    except ValueError as exc:  # json.JSONDecodeError: a ledger line that is not JSON
        # A corrupt ledger is the round-trip check's to name, line and all; this check
        # only says it could not compare, rather than crashing the whole doctor run.
        return [
            "could not compare the ledgers with the database: a ledger line is not "
            f"JSON ({exc})"
        ]
    for gap in gaps:
        where = ledger_repair.ledger_location(gap.ledger)
        if gap.only_in_db:
            findings.append(
                f"{len(gap.only_in_db)} row(s) in the database's `{gap.table}` table are "
                f"missing from {where} ({currency.some_of(list(gap.only_in_db))}). The "
                "ledger is what `kyo rebuild` reads, so the next rebuild would drop "
                f"them. {LEDGER_REPAIR_REMEDY}"
            )
        if gap.only_in_ledger:
            findings.append(
                f"{len(gap.only_in_ledger)} line(s) in {where} have no row in the "
                f"database's `{gap.table}` table "
                f"({currency.some_of(list(gap.only_in_ledger))}) — the export holds "
                "history the database does not. Run "
                f"`{currency.REBUILD_REMEDY}` to load it from kyosei/export/, after "
                "reading the [db_export_currency] finding, if any, for what else that "
                "replaces; `kyo export --refresh` never removes a ledger line"
            )
    return findings


# ---------------------------------------------------------------------------- KYO-7


def check_dispatch_state_consistency(store: Store) -> list[str]:
    """KYO-7 / law 8: ``state`` is never a bare label — ``reported`` implies a real
    report row, a report-less terminal state implies a recorded ``why``, and
    ``running`` must not already carry a report (the states never overlap in what
    they imply).

    KYO-345 widens the middle clause from ``terminated-no-report`` alone to every
    member of :data:`~kyosei.contracts.DISPATCH_STATES_WITHOUT_A_REPORT`, which is
    now that state and ``withdrawn``. Quantified over the set rather than written
    twice on purpose: the invariant is "a dispatch that ended with no report says
    why", and a state added to that set without a line here would be one a controller
    could reach with no account of it at all — the bare label this check exists to
    refuse, arriving by omission.
    """
    failures: list[str] = []
    for row in store.conn.execute(
        "SELECT id, state, report_id, terminated_why FROM dispatches ORDER BY id"
    ):
        if row["state"] not in DISPATCH_STATES:
            failures.append(f"dispatch {row['id']} has unknown state {row['state']!r}")
            continue
        if row["state"] == DISPATCH_STATE_REPORTED and not row["report_id"]:
            failures.append(
                f"dispatch {row['id']} is reported but has no report_id (law 8: a dispatch "
                "must never render as reported without an actual report)"
            )
        if row["state"] in DISPATCH_STATES_WITHOUT_A_REPORT:
            if not (row["terminated_why"] or "").strip():
                failures.append(
                    f"dispatch {row['id']} is {row['state']} but has no recorded why (law 8)"
                )
            if row["report_id"]:
                failures.append(
                    f"dispatch {row['id']} is {row['state']} but carries report_id "
                    f"{row['report_id']!r} (law 3: state must agree with the data it is "
                    "derived from — a dispatch with a report is reported)"
                )
        if row["state"] == DISPATCH_STATE_RUNNING and row["report_id"]:
            failures.append(
                f"dispatch {row['id']} is running but already has report_id {row['report_id']!r} "
                "(law 3: state must agree with the data it is derived from)"
            )
    return failures


def check_unresolved_stops(store: Store) -> list[str]:
    """KYO-134: a stop that audited nothing, put where a controller already looks.

    KYO-128 gave the ``SubagentStop`` guards three named outcomes, and the third —
    ``unresolved``, meaning declared worktrees WERE in play and this stop matched
    none of them — correctly declines to answer and correctly does not block. What
    it could not do was reach anybody: on exit 0 no output is fed back to any agent,
    so the warning lived in the controller's transcript and died with it. `kyo
    doctor` runs from this project's ``Stop`` and ``SessionEnd`` hooks, so a
    controller reads it without being told to (KYO-134.c3).

    WHAT BOUNDS THIS, and it is the design decision in the check rather than an
    omission. A recorded unresolved stop is permanent — that is what "durable"
    means — but it is only ACTIONABLE while one of the dispatches it names is still
    running, because the remedy is "look at that worktree before you merge its
    branch". Once every candidate has reported or been terminated, the controller
    has necessarily handled the branch, and a doctor that kept failing on it would
    be a check nobody can ever clear, which is how a failing doctor stops being
    read at all. The row stays in the ledger and stays visible in `kyo worktrees`;
    what stops is the failure.

    WHAT TIER IT REPORTS IN: a notice — declared by :data:`kyosei.doctor.NOTICE_CHECKS`
    — so the finding prints in full and exits 0, agreeing with its own text ("this is
    not a block and never was"). Nothing about the finding is quietened: same sentence,
    same count, same implicated worktrees, printed on every run
    (docs/design/doctor.md#kyo-214).

    This check therefore does NOT satisfy KYO-134.c5 on its own and is not asked to:
    a passing doctor prints ``ok  unresolved_stops``, which says this check ran and
    found nothing ACTIONABLE — not that the ledger is empty, and not that every stop
    was audited. The three-way answer ("none recorded" is not "none happened") is
    :meth:`stop_audit.StopAuditLedger.sentence`, rendered in full by `kyo
    worktrees` and carried in this check's own JSON below.
    """
    from . import stop_audit

    ledger = stop_audit.stop_audits(store)
    if ledger.unreadable is not None:
        return [ledger.sentence()]
    running = {row["id"] for row in store.running_dispatches()}
    # KYO-157: ONE finding, not one per row — a line per stop makes `kyo doctor`
    # unreadable and drowns every other finding (docs/design/doctor.md#kyo-157). What a
    # controller needs is that some stop went unaudited and WHICH TREES are implicated;
    # the per-row detail lives in `kyo worktrees` and in the ledger, which KYO-134 made
    # durable precisely so this surface would not have to carry it.
    implicated: set[str] = set()
    count = 0
    earliest: str | None = None
    for audit in ledger.unresolved:
        live = [candidate for candidate in audit.candidates if candidate in running]
        if not live:
            continue
        count += 1
        implicated.update(live)
        if earliest is None or audit.at < earliest:
            earliest = audit.at
    # KYO-418.c6: an unresolved stop since then is a count on each running candidate's
    # own counter rather than a row, so it is folded in here -- the finding must not
    # shrink because the write side stopped growing the ledger (the same fold
    # `census` makes for KYO-375's ticks). Counted once per running candidate.
    for dispatch_id, counter in ledger.unresolved_counters.items():
        if dispatch_id not in running or not counter.get("count"):
            continue
        count += int(counter["count"])
        implicated.add(dispatch_id)
        first_seen = counter.get("first_seen")
        if isinstance(first_seen, str) and (earliest is None or first_seen < earliest):
            earliest = first_seen
    if not count:
        return []
    # KYO-163.c2, the DECISION that record asked to be written down here: the rows
    # counted above are dominated by Claude Code's periodic `SubagentStop` ticks, not by
    # workers stopping, and they are still counted -- as rows in the ledger before
    # KYO-418 and as the per-dispatch counters folded in above since. Nothing here
    # reclassifies a row, because the only evidence that an agent is a tick is the
    # absence of a trace, and classifying on that would silently stop auditing a real
    # worker whose sidecar the harness failed to write (KYO-163.c3).
    #
    # What is added is the population's measured signature, so the count reads as what
    # it is. The COUNT does NOT shrink: if a real worker's stop went unaudited it is
    # still in it and a controller still has trees to check
    # (`tests/test_kyo163_phantom_stops.py` pins that). KYO-157 owns how much of this
    # surface a reader wades through; this clause is about the number being
    # interpretable. The history is at docs/design/doctor.md#kyo-163.
    pattern = ledger.tick_profile().sentence()
    return [
        f"{count} stop(s) since {earliest}: NOTHING WAS CHECKED about the tree each one "
        f"stopped in, because the stopping agent could not be matched to a running "
        f"dispatch. The worktree(s) that could have been the subject: "
        f"{', '.join(sorted(implicated))}. Check them yourself before merging "
        "their branches (`kyo worktrees` lists them, `kyo show` the events for the "
        "individual stops); this is not a block and never was, and it clears on its own "
        "once those dispatches are no longer running (KYO-134, KYO-157)"
        + (f". {pattern}" if pattern else "")
    ]


def stop_audit_census(store: Store) -> str:
    """KYO-373.c3: the stop ledger's denominator, in one line. Never a finding.

    ``unresolved_stops`` counts the unmatched worker stops on running dispatches and is
    silent otherwise, which leaves a reader of `kyo doctor` unable to tell "3 unmatched"
    out of 30 from 3 out of thousands (docs/design/doctor.md#kyo-373). So the three
    counts ride beside that check's line on every run. Deliberately NOT a check: it has
    no failing state, and a "check" that can only pass is a line that says ``ok``
    about nothing. :data:`CHECK_CENSUS` is where it is attached.
    """
    from . import stop_audit

    return stop_audit.stop_audits(store).census_sentence()


#: KYO-373.c3: a one-line census printed beside a check's own line, by check name.
#: Rendered by `kyo doctor` on every run whether the check passed or not, and carried
#: in ``--json`` under that check's ``census``. Not a finding: nothing here changes a
#: tier, an ``ok`` or an exit code.
CHECK_CENSUS = {
    "unresolved_stops": stop_audit_census,
}


def census_lines(store: Store) -> dict[str, str]:
    """Every :data:`CHECK_CENSUS` line, by check name. Never raises.

    A census that could not be computed says so in its own line rather than taking
    `kyo doctor` down with it — the same stance every stop-ledger reader takes.
    """
    out: dict[str, str] = {}
    for name, census in CHECK_CENSUS.items():
        try:
            out[name] = census(store)
        except Exception as exc:  # noqa: BLE001 - a census must not break the doctor
            out[name] = f"census could not be computed ({exc})"
    return out


def check_report_dispatch_agreement(store: Store) -> list[str]:
    """KYO-7.c3: a report and the dispatch it names point back at each other, and
    agree on which record they belong to.

    KYO-90 narrows the back-link half rather than dropping it: a dispatch may now
    carry more than one report (an amendment supersedes the earlier one), so
    "``dispatch.report_id`` is this report" is no longer true of every row. What
    is still true, and checked here, is that ``report_id`` names a report *of this
    dispatch*. The rest of the relation — that every report which is not the
    current one was superseded by exactly one that is — belongs to
    :func:`check_report_amendment_chain`, which is where an orphaned second report
    is caught.
    """
    failures: list[str] = []
    dispatches = {
        row["id"]: row
        for row in store.conn.execute("SELECT id, record_id, report_id FROM dispatches")
    }
    reports_by_dispatch: dict[str, set[str]] = {}
    for row in store.conn.execute(
        "SELECT id, record_id, dispatch_id FROM reports ORDER BY id"
    ):
        reports_by_dispatch.setdefault(row["dispatch_id"], set()).add(row["id"])
        dispatch = dispatches.get(row["dispatch_id"])
        if dispatch is None:
            failures.append(f"report {row['id']} points at missing dispatch {row['dispatch_id']}")
            continue
        if dispatch["record_id"] != row["record_id"]:
            failures.append(
                f"report {row['id']} is filed under record {row['record_id']} but its dispatch "
                f"{row['dispatch_id']} belongs to {dispatch['record_id']}"
            )
    for dispatch_id, dispatch in sorted(dispatches.items()):
        report_id = dispatch["report_id"]
        if report_id and report_id not in reports_by_dispatch.get(dispatch_id, set()):
            failures.append(
                f"dispatch {dispatch_id} names {report_id} as its current report, but no report "
                f"of that dispatch has that id — back-link disagreement"
            )
    return failures


def check_report_amendment_chain(store: Store) -> list[str]:
    """KYO-90.c1/c3: a dispatch's reports form one chain ending at the current one.

    An amended report supersedes the report its ``amends`` names, and the whole
    relation is stored in one place — that key, inside the report's own
    ``body_json`` — with the reverse direction derived from it. This is the check
    that proves the pair stays consistent, the same role
    :func:`check_restate_backlink_symmetry` plays for a restated criterion.

    Four ways it can be wrong, each of which would leave a reader unable to tell
    which report is the account of what happened:

    * a report that is not its dispatch's current one and that nothing amends —
      an orphan, which reads as a second, competing account;
    * an ``amends`` naming a report of some other dispatch, or no report at all;
    * two reports amending the same one, which forks the chain;
    * the current report being itself amended, which means ``report_id`` is
      pointing at a superseded account.
    """
    failures: list[str] = []
    current_by_dispatch = {
        row["id"]: row["report_id"]
        for row in store.conn.execute("SELECT id, report_id FROM dispatches")
    }
    rows = list(
        store.conn.execute("SELECT id, dispatch_id, body_json FROM reports ORDER BY id")
    )
    amends_of: dict[str, str | None] = {}
    by_dispatch: dict[str, list[str]] = {}
    all_report_ids = {row["id"] for row in rows}
    dispatch_of = {row["id"]: row["dispatch_id"] for row in rows}
    for row in rows:
        try:
            body = json.loads(row["body_json"])
        except (TypeError, ValueError):  # pragma: no cover - validated at ingest
            body = None
        amends_of[row["id"]] = report_amends(body)
        by_dispatch.setdefault(row["dispatch_id"], []).append(row["id"])

    amended: dict[str, list[str]] = {}
    for report_id, target in sorted(amends_of.items()):
        if target is None:
            continue
        if target not in all_report_ids:
            failures.append(
                f"report {report_id} declares it amends {target}, which is not a report in "
                "this store"
            )
            continue
        if dispatch_of[target] != dispatch_of[report_id]:
            failures.append(
                f"report {report_id} (dispatch {dispatch_of[report_id]}) declares it amends "
                f"{target}, which belongs to dispatch {dispatch_of[target]} — an amendment "
                "corrects the account of one attempt and never crosses dispatches"
            )
            continue
        amended.setdefault(target, []).append(report_id)

    for target, amenders in sorted(amended.items()):
        if len(amenders) > 1:
            failures.append(
                f"reports {', '.join(sorted(amenders))} all claim to amend {target}; a report "
                "is superseded by exactly one"
            )

    for dispatch_id, report_ids in sorted(by_dispatch.items()):
        current = current_by_dispatch.get(dispatch_id)
        for report_id in sorted(report_ids):
            if report_id == current:
                if amended.get(report_id):
                    failures.append(
                        f"dispatch {dispatch_id} names {report_id} as its current report, but "
                        f"{', '.join(sorted(amended[report_id]))} claims to supersede it"
                    )
            elif not amended.get(report_id):
                failures.append(
                    f"report {report_id} is not dispatch {dispatch_id}'s current report "
                    f"({current!r}) and nothing amends it — a second account with no stated "
                    "relation to the first is exactly what `kyo report` refuses to create"
                )
    return failures


#: The auto-generated reason `report_ingest.cmd_split` writes onto a criterion it splits
#: away — see the module docstring there. Parsed here, not duplicated, so the two
#: can never independently drift (law 3).
_SPLIT_REASON_RE = re.compile(r"^split off to successor (\S+) as (\S+)$")


def check_split_backlink_symmetry(store: Store) -> list[str]:
    """KYO-7.c6: every ``void-withdrawn`` criterion that names a split successor
    actually has one — a real criterion on a real record, linked back by a
    ``produced-by`` edge from the successor to the original."""
    failures: list[str] = []
    for row in store.conn.execute(
        "SELECT id, record_id, reason FROM criteria WHERE state = 'void-withdrawn' ORDER BY id"
    ):
        match = _SPLIT_REASON_RE.match((row["reason"] or "").strip())
        if not match:
            continue
        successor_id, successor_criterion_id = match.group(1), match.group(2)
        original_id = row["record_id"]

        successor_criterion = store.conn.execute(
            "SELECT record_id FROM criteria WHERE id = ?", (successor_criterion_id,)
        ).fetchone()
        if successor_criterion is None:
            failures.append(
                f"criterion {row['id']} claims it was split to {successor_criterion_id}, "
                "which does not exist"
            )
            continue
        if successor_criterion["record_id"] != successor_id:
            failures.append(
                f"criterion {row['id']} claims split to {successor_criterion_id} on "
                f"{successor_id}, but {successor_criterion_id} actually belongs to "
                f"{successor_criterion['record_id']}"
            )

        link = store.conn.execute(
            "SELECT 1 FROM links WHERE src_id = ? AND edge_type = 'produced-by' AND dst_id = ?",
            (successor_id, original_id),
        ).fetchone()
        if link is None:
            failures.append(
                f"criterion {row['id']} claims split to successor {successor_id}, but no "
                f"'produced-by' link from {successor_id} to {original_id} exists (back-link "
                "asymmetry)"
            )
    return failures


def check_restate_backlink_symmetry(store: Store) -> list[str]:
    """KYO-58.c2: every restated criterion names a real replacement, on its own
    record, newer than itself — and no two criteria name the same replacement.

    The pair with ``check_split_backlink_symmetry`` one function up is deliberate:
    `kyo restate` reuses `kyo split`'s convention (the relation lives in the voided
    criterion's canonical ``reason``, written by :func:`contracts.restate_reason` and
    parsed by :data:`contracts.RESTATE_REASON_RE`), so it gets split's kind of
    validator too — a mechanism whose consistency nothing checks is a convention,
    not a relation.

    The uniqueness clause is what makes the *reverse* direction safe to derive. The
    forward direction is stored ("c1 was replaced by c7"); the reverse ("c7 replaces
    c1") is a lookup, and a lookup that can return two answers is not a back-link.
    Two criteria naming one replacement means someone wrote the reason by hand,
    because `kyo restate` allocates a fresh criterion every time.
    """
    failures: list[str] = []
    claimed: dict[str, str] = {}
    for row in store.conn.execute(
        "SELECT id, record_id, seq, reason FROM criteria WHERE state = 'void-withdrawn'"
        " ORDER BY id"
    ):
        match = RESTATE_REASON_RE.match((row["reason"] or "").strip())
        if not match:
            continue
        replacement_id = match.group(1)
        first = claimed.setdefault(replacement_id, row["id"])
        if first != row["id"]:
            failures.append(
                f"criteria {first} and {row['id']} both claim to have been restated as "
                f"{replacement_id}; a replacement replaces exactly one criterion, and the "
                "'replaces' direction is derived from this pointer, so two claimants make "
                "it unanswerable"
            )
        replacement = store.conn.execute(
            "SELECT record_id, seq FROM criteria WHERE id = ?", (replacement_id,)
        ).fetchone()
        if replacement is None:
            failures.append(
                f"criterion {row['id']} claims it was restated as {replacement_id}, "
                "which does not exist"
            )
            continue
        if replacement["record_id"] != row["record_id"]:
            failures.append(
                f"criterion {row['id']} claims it was restated as {replacement_id}, but "
                f"{replacement_id} belongs to {replacement['record_id']}, not "
                f"{row['record_id']} — a restatement stays on its own record (`kyo split` "
                "is the verb that moves a criterion to another one)"
            )
        elif replacement["seq"] <= row["seq"]:
            failures.append(
                f"criterion {row['id']} claims it was restated as {replacement_id}, which "
                f"is older than it (seq {replacement['seq']} <= {row['seq']}) — a "
                "replacement is authored after the thing it replaces"
            )
    return failures


def check_dispatch_covers_exist(store: Store) -> list[str]:
    """KYO-13.c4 / KYO-12: every record id named in a dispatch's ``covers_json``
    must exist and must not be the dispatch's own ``record_id`` (a dispatch always
    covers its own record implicitly; naming it again in ``covers`` is not itself
    wrong, but doubling up is worth a doctor note rather than silently accepted,
    since `kyo report`'s allowed-records check would otherwise mask a copy/paste
    mistake as a no-op)."""
    failures: list[str] = []
    known = set(store.all_record_ids())
    for row in store.conn.execute(
        "SELECT id, record_id, covers_json FROM dispatches WHERE covers_json IS NOT NULL"
    ):
        try:
            covers = json.loads(row["covers_json"])
        except json.JSONDecodeError:
            failures.append(f"dispatch {row['id']} has non-JSON covers_json {row['covers_json']!r}")
            continue
        if not isinstance(covers, list):
            failures.append(f"dispatch {row['id']} covers_json is not a JSON array: {covers!r}")
            continue
        for covered in covers:
            if covered not in known:
                failures.append(
                    f"dispatch {row['id']} names covered record {covered!r}, which does not exist"
                )
            elif covered == row["record_id"]:
                failures.append(
                    f"dispatch {row['id']} lists its own record {covered!r} in covers_json "
                    "(a dispatch always covers its own record implicitly; redundant)"
                )
    return failures
