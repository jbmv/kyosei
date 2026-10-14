"""``claim`` / ``verify`` / ``attach``.

The evidence layer: an assertion about the world (``claim``), a criterion
checked against it (``verify``), and the artifact either rests on (``attach``).
Stored verbatim, law 5 — nothing here paraphrases what it was given.
"""

from __future__ import annotations

import json
from argparse import Namespace

from . import commands, events_ledger, export
from .clock import now
from .commands import (
    Context,
    Output,
    _add_evidence,
    _reason,
    _require_record,
    _require_supports_target,
    _touch_record,
)
from .contracts import (
    CLAIM_LEVELS,
    CONTRACT_VERIFY_FROM_REPORT_NEEDS_A_REPORT,
    CONTRACT_VERIFY_ONLY_NAMES_AN_ABSENT_CRITERION,
    EDIT_DIRECT_CRITERION_STATES,
    ROLE_COMPLETE,
    SETTLED_CRITERION_STATES,
    VERIFICATION_METHODS,
    VERIFY_VERDICTS,
    check_criterion_state,
    replaced_by,
)
from .errors import ContractViolation, KyoseiError, UsageError
from .prose import (
    read_prose,
    single_line,
)
from .render import (
    echo_block,
    echo_value,
)


def cmd_claim(ctx: Context, args: Namespace) -> Output:
    """``kyo claim <record-id> --level ... --method ... --file/-`` (law 9).

    A reconsidered claim never overwrites the prior one: pass ``--supersedes`` to
    chain a new claim onto an old one, which stays readable with its full history
    (KYO-4.c2) — the same append-only pattern ``kyo edit --note-file
    --supersede-note`` already uses for notes.
    """
    store = ctx.store()
    if args.level not in CLAIM_LEVELS:
        raise UsageError(f"{args.level!r} is not a claim level; known: {', '.join(CLAIM_LEVELS)}")
    method = single_line(args.method, what="--method")
    statement = read_prose(args.file, what="--file")
    if not statement.strip():
        raise UsageError("a claim statement must not be empty")

    record_id = args.id
    with store.transaction():
        _require_record(store, record_id)
        before = {record_id: export.record_document(store, record_id)}
        if args.supersedes:
            prior = store.claim(args.supersedes)
            if prior["record_id"] != record_id:
                raise KyoseiError(
                    f"{args.supersedes} belongs to {prior['record_id']}, not {record_id}"
                )
            if prior["superseded_by"]:
                raise KyoseiError(
                    f"{args.supersedes} was already superseded by {prior['superseded_by']}"
                )
        claim_id, seq = store.new_child_id(record_id, "claim")
        stamp = now()
        store.conn.execute(
            "INSERT INTO claims (id, record_id, seq, statement, level, method, author,"
            " created_at, supersedes, superseded_by, superseded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                claim_id,
                record_id,
                seq,
                statement,
                args.level,
                method,
                ctx.actor,
                stamp,
                args.supersedes,
            ),
        )
        if args.supersedes:
            store.conn.execute(
                "UPDATE claims SET superseded_by = ?, superseded_at = ? WHERE id = ?",
                (claim_id, stamp, args.supersedes),
            )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"claim {record_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)

    lines = [
        f"claimed {claim_id} on {record_id}",
        echo_value("level", args.level),
        echo_value("method", method),
    ]
    if args.supersedes:
        lines.append(echo_value("supersedes", args.supersedes))
    lines += echo_block("statement", statement)
    return Output(
        lines,
        {
            "ok": True,
            "command": "claim",
            "claim_id": claim_id,
            "record_id": record_id,
            "record": after[record_id],
        },
    )


# ------------------------------------------------------------------------- KYO-198


def _refuse_from_report_flags_without_from_report(args: Namespace) -> None:
    """``--only`` is meaningless on its own, and silently ignoring it would apply
    every verdict in a report the caller asked to apply part of."""
    if getattr(args, "only", None):
        raise UsageError(
            "--only selects from a report's criteria_verdicts, so it needs "
            "--from-report <dispatch-id>; verifying one criterion by hand already "
            "names the criterion positionally"
        )
    if getattr(args, "again", False):
        raise UsageError(
            "--again re-applies a report's verdicts that were already applied, so it "
            "needs --from-report <dispatch-id>; verifying one criterion by hand always "
            "records a new verification"
        )


# ------------------------------------------------------------------------- KYO-415


def _verification_ids(document: dict | None) -> set[str]:
    """Every verification id a record document (an event snapshot) carries."""
    if not isinstance(document, dict):
        return set()
    return {
        verification["id"]
        for criterion in document.get("criteria") or []
        if isinstance(criterion, dict)
        for verification in criterion.get("verifications") or []
        if isinstance(verification, dict) and verification.get("id")
    }


def _verifications_from_report(store, dispatch_id: str, report_id: str) -> set[str]:
    """The ids of the verifications an earlier ``kyo verify --from-report`` wrote while
    ``report_id`` was the dispatch's current report (KYO-415.c4).

    Derived from rows already stored, so no column and no schema bump (KYO-415.c9).
    Every application of a report writes one events row per record it touched, with
    ``command = "verify --from-report <dispatch>"`` and the record document before and
    after; a verification id in ``after`` and not in ``before`` is one that application
    wrote. Only events at or after the report's own ingestion count: a dispatch's
    reports are superseded rather than overwritten (KYO-90), and ``verify --from-report``
    always applies the CURRENT one, so an application stamped before this report
    existed applied an earlier report, and its verdicts are not this one's. The events
    table is rebuilt from the export ledger, so this answer survives ``kyo rebuild``.
    """
    ingested_at = store.report(report_id)["created_at"]
    written: set[str] = set()
    for row in store.conn.execute(
        "SELECT before_json, after_json, changed_json FROM events WHERE command = ?"
        " AND at >= ? ORDER BY seq",
        (f"verify --from-report {dispatch_id}", ingested_at),
    ):
        # KYO-418: the prior document from either row format.
        event = events_ledger.event_from_columns(
            row["before_json"], row["after_json"], row["changed_json"]
        )
        written |= _verification_ids(event["after"]) - _verification_ids(
            events_ledger.event_before_document(event)
        )
    return written


#: The criterion states a completed record should not be holding: everything a
#: verification can record that does not settle the criterion.
UNMET_CRITERION_STATES: frozenset[str] = VERIFY_VERDICTS - SETTLED_CRITERION_STATES


def completed_with_unmet(store, record_id: str, ladder=None) -> dict | None:
    """KYO-415.c7/c8: ``record_id`` sits at the ladder's complete-role status while
    holding a criterion in an unmet state — or ``None`` when it does not.

    One predicate for both surfaces that report it, so ``kyo verify``'s line and
    ``kyo doctor``'s notice cannot disagree about which records are in the class. The
    verdict that put the record here is a legitimate fact (a regression found after
    the record was closed), so nothing refuses it; what this names is the contradiction
    with the ladder's own meaning of complete, and the two ways out of it: carry the
    unmet criteria to a successor with ``kyo split`` (the remedy the completion
    contract already names), or reopen the record.

    ``ladder`` is the project's status ladder when the caller already loaded it (the
    doctor check asks once per record, and reading ``statuses.yml`` once per record is
    a cost with no answer in it).
    """
    ladder = ladder or commands._load_status_ladder(store)
    record = store.record(record_id)
    if ladder.role(record["status"]) != ROLE_COMPLETE:
        return None
    unmet = [
        {"id": row["id"], "state": row["state"]}
        for row in store.conn.execute(
            "SELECT id, state FROM criteria WHERE record_id = ? ORDER BY seq", (record_id,)
        )
        if row["state"] in UNMET_CRITERION_STATES
    ]
    if not unmet:
        return None
    ids = " ".join(c["id"] for c in unmet)
    return {
        "record_id": record_id,
        "status": record["status"],
        "criteria": unmet,
        "split_remedy": f"kyo split {record_id} --to-successor {ids}",
        "reopen_remedy": f"kyo move {record_id} {ladder.initial}",
    }


def completed_with_unmet_line(found: dict) -> str:
    """The one sentence ``kyo verify`` and ``kyo doctor`` both print for
    :func:`completed_with_unmet`'s finding."""
    criteria = ", ".join(f"{c['id']} ({c['state']})" for c in found["criteria"])
    return (
        f"{found['record_id']} is {found['status']} but holds unmet {criteria} — the "
        f"verdict is recorded, and the record's status still says its criteria are met. "
        f"Carry the unmet criteria to a successor with `{found['split_remedy']}`, or "
        f"reopen the record with `{found['reopen_remedy']}`"
    )


def _refuse_flags_that_from_report_supplies(args: Namespace) -> None:
    """A verdict typed on the command line beside ``--from-report`` is the retyping
    this whole path removes, and honouring one would silently overwrite what a
    worker actually reported. Refused rather than merged."""
    supplied = [
        flag
        for flag, value in (
            ("CRITERION_ID", args.criterion_id),
            ("--verdict", args.verdict),
            ("--method", args.method),
            ("--evidence-file", args.evidence_file),
            ("--why", getattr(args, "why", None)),
            ("--why-file", getattr(args, "why_file", None)),
        )
        if value
    ]
    if supplied:
        raise UsageError(
            f"--from-report reads the verdict, method, evidence and reason from the "
            f"report, so it takes none of {', '.join(supplied)}. Applying a subset is "
            "--only <criterion-id> ...; recording a verdict of your own instead is "
            "`kyo verify <criterion-id> --verdict <verdict> --method <method>`, without "
            "--from-report"
        )


def _report_verdicts(store, dispatch_id: str) -> tuple[str, list[dict]]:
    """The current report's ``criteria_verdicts[]`` for one dispatch.

    Reports are superseded, never overwritten (KYO-90), and the dispatch row already
    names which one is current — so this reads ``dispatches.report_id`` rather than
    the newest row in ``reports``, and an amended report is what gets applied without
    this function knowing amendment exists.
    """
    dispatch = store.dispatch(dispatch_id)
    report_id = dispatch["report_id"]
    if not report_id:
        raise ContractViolation(
            CONTRACT_VERIFY_FROM_REPORT_NEEDS_A_REPORT,
            [
                f"dispatch {dispatch_id} carries no ingested report, so there are no "
                f"criteria_verdicts to apply (its state is {dispatch['state']!r})",
                f"ingest the worker's report first: `kyo report {dispatch_id} --file "
                "<report-path>`",
            ],
        )
    body = json.loads(store.report(report_id)["body_json"])
    return report_id, list(body["criteria_verdicts"])


def _selected_verdicts(
    dispatch_id: str, report_id: str, verdicts: list[dict], only: list[str] | None
) -> list[dict]:
    if not only:
        return verdicts
    present = {entry["criterion_id"] for entry in verdicts}
    absent = [criterion_id for criterion_id in only if criterion_id not in present]
    if absent:
        raise ContractViolation(
            CONTRACT_VERIFY_ONLY_NAMES_AN_ABSENT_CRITERION,
            [
                f"--only names {criterion_id}, which report {report_id} carries no "
                f"verdict for (it carries: {', '.join(sorted(present)) or '(none)'})"
                for criterion_id in absent
            ]
            + [
                f"nothing was applied. Drop {', '.join(absent)} from --only, or drop "
                f"--only to apply every verdict {report_id} carries"
            ],
        )
    wanted = set(only)
    return [entry for entry in verdicts if entry["criterion_id"] in wanted]


def gate_prompt_lines(gate_prompts: list[dict]) -> list[str]:
    """What ``kyo verify --from-report`` prints for each wait nothing makes findable.

    One function so that ``kyo accept``, which composes this command and prints the
    same remedy in its own summary, can name exactly these lines to leave out of its
    text and print the remedy once (KYO-488). Its ``--json`` keeps them: a step's
    ``lines`` there is the composed command's output, verbatim.
    """
    lines: list[str] = []
    for waiting in gate_prompts:
        lines.append(
            "nothing makes this wait findable: no open `awaiting:*` gate and no running "
            "dispatch, so it is in the desktop's Awaiting list nowhere and in `kyo "
            "brief`'s gated work nowhere. Gate it yourself — this command wrote no gate:"
        )
        lines.append(f"  {waiting['remedy']}")
    return lines


def _verify_from_report(ctx: Context, args: Namespace) -> Output:
    """KYO-198.c1/c2/c3. Apply a dispatch report's own verdicts as one controller act.

    One transaction for the whole batch, and the same write path a single
    ``kyo verify`` uses for each entry — the verification row, the derived criterion
    state, and an evidence artifact for the entry's ``evidence`` string — attributed
    to ``ctx.actor``, because the controller is who is applying these, not the worker
    who reported them.

    **Two entries are reported rather than applied**, and both are printed by name so
    the count of what landed is never inferred from the count of what was in the file
    (law 6):

    * a ``void-premise-gone`` or ``void-withdrawn`` verdict. The report schema allows
      five verdicts and ``kyo verify`` records three, deliberately: KYO-4.c3 makes the
      void pair premise judgments rather than verified facts, so they go through
      ``kyo edit --criterion <id> --state <void-...> --why <reason>``, which is a
      different act with a different author. Routing them through here would make
      ``--from-report`` a way around a refusal that exists on purpose, so it names
      that command instead of performing it.
    * a criterion restated since the report was written (KYO-58). Restating mints a
      NEW criterion id and voids the old one; the report names the old one. Recording
      a verdict against superseded text is the evidence re-attachment KYO-58 exists to
      prevent, so the entry is skipped and the replacement is named. The rest of the
      batch still applies: refusing the whole run would send the controller back to
      the per-criterion retyping this command removes, for one stale id.

    **Two more are skipped rather than applied unless ``--again`` is passed**, each
    named beside ``not applied`` with the verification that decided it:

    * ``already_applied`` (KYO-415.c4): the criterion's latest verification is one an
      earlier application of THIS report wrote, so applying it again would record the
      same verdict and the same evidence twice.
    * ``newer_verdict_kept`` (KYO-511): the criterion's latest verification came from
      somewhere else and was recorded at or after this report's ingestion — a
      controller's own ``kyo verify`` after reading the report, most often. The report
      is then the older word, and applying it would silently overwrite a judgment made
      with more in view than the worker had (a consumer project's APP-58: a worktree-only
      failure reported ``unmet-unobserved``, verified ``met`` after the merge, then
      re-flipped by ``kyo accept``). The comparison is the report row's ``created_at``
      — the one instant the store itself stamped for the report (law 16); a worker's
      writing time is not stored, and a verification id carries order only within one
      record. A verdict recorded BEFORE the ingest is older than the report and is
      applied over, as it always was.
    """
    store = ctx.store()
    _refuse_flags_that_from_report_supplies(args)
    dispatch_id = args.from_report
    report_id, verdicts = _report_verdicts(store, dispatch_id)
    selected = _selected_verdicts(dispatch_id, report_id, verdicts, args.only)

    again = bool(getattr(args, "again", False))

    applied: list[dict] = []
    not_applied: list[dict] = []
    already_applied: list[dict] = []
    newer_verdict_kept: list[dict] = []
    lines: list[str] = []
    with store.transaction():
        dispatch = store.dispatch(dispatch_id)
        from_this_report = (
            set() if again else _verifications_from_report(store, dispatch_id, report_id)
        )
        ingested_at = store.report(report_id)["created_at"]
        pending: list[tuple[dict, dict]] = []
        record_ids: list[str] = []
        for entry in selected:
            criterion = store.criterion(entry["criterion_id"])
            successor = replaced_by(criterion["state"], criterion["reason"])
            if successor:
                not_applied.append(
                    {
                        "criterion_id": entry["criterion_id"],
                        "verdict": entry["verdict"],
                        "why": (
                            f"restated as {successor} since the report was written "
                            f"(KYO-58); a verdict recorded against superseded text is "
                            f"the re-attachment that restatement exists to prevent. "
                            f"Verify {successor} against the new wording"
                        ),
                    }
                )
                continue
            if entry["verdict"] not in VERIFY_VERDICTS:
                not_applied.append(
                    {
                        "criterion_id": entry["criterion_id"],
                        "verdict": entry["verdict"],
                        "why": (
                            f"{entry['verdict']} is a premise judgment, not a verified "
                            f"fact — `kyo verify` records "
                            f"{', '.join(sorted(VERIFY_VERDICTS))} only (KYO-4.c3). "
                            f"Apply it with `kyo edit {criterion['record_id']} "
                            f"--criterion {entry['criterion_id']} --state "
                            f"{entry['verdict']} --why <reason>`"
                        ),
                    }
                )
                continue
            if entry["method"] not in VERIFICATION_METHODS:
                raise UsageError(
                    f"report {report_id} gives {entry['criterion_id']} the method "
                    f"{entry['method']!r}, which is not a verification method; known: "
                    f"{', '.join(VERIFICATION_METHODS)}"
                )
            check_criterion_state(
                entry["criterion_id"], entry["verdict"], entry.get("reason")
            )
            # KYO-415.c4: a rerun (a lost terminal, a retry, `kyo accept` resumed after a
            # refused later step) must not record the same verdict and the same evidence
            # a second time. Skipped when the criterion's LATEST verification came from
            # this report.
            latest = store.latest_verification(entry["criterion_id"])
            if latest is not None and latest["id"] in from_this_report:
                already_applied.append(
                    {
                        "criterion_id": entry["criterion_id"],
                        "verdict": entry["verdict"],
                        "verification_id": latest["id"],
                    }
                )
                continue
            # KYO-511: a verdict recorded since the report was ingested, by anything
            # other than this report, is newer than the report, and re-applying over it
            # is a decision `--again` makes explicitly. `again` empties nothing here, so
            # it is tested directly.
            if (
                not again
                and latest is not None
                and latest["created_at"] >= ingested_at
            ):
                newer_verdict_kept.append(
                    {
                        "criterion_id": entry["criterion_id"],
                        "verdict": entry["verdict"],
                        "latest_verdict": latest["verdict"],
                        "latest_method": latest["method"],
                        "latest_author": latest["author"],
                        "latest_at": latest["created_at"],
                        "verification_id": latest["id"],
                    }
                )
                continue
            pending.append((entry, criterion))
            if criterion["record_id"] not in record_ids:
                record_ids.append(criterion["record_id"])

        before = {
            record_id: export.record_document(store, record_id) for record_id in record_ids
        }
        for entry, criterion in pending:
            record_id = criterion["record_id"]
            evidence_id: str | None = None
            evidence = (entry.get("evidence") or "").strip()
            if evidence:
                evidence_id, _digest = _add_evidence(
                    store,
                    record_id,
                    evidence,
                    supports_id=entry["criterion_id"],
                    author=ctx.actor,
                )
            verification_id, seq = store.new_child_id(record_id, "verification")
            stamp = now()
            store.conn.execute(
                "INSERT INTO verifications (id, criterion_id, record_id, seq, verdict,"
                " method, reason, author, created_at, evidence_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    verification_id,
                    entry["criterion_id"],
                    record_id,
                    seq,
                    entry["verdict"],
                    entry["method"],
                    entry.get("reason"),
                    ctx.actor,
                    stamp,
                    evidence_id,
                ),
            )
            # Law 3, exactly as `_verify_one` does it: the criterion's state is derived
            # from the verification just recorded, in the same transaction.
            store.conn.execute(
                "UPDATE criteria SET state = ?, reason = ?, updated_at = ? WHERE id = ?",
                (entry["verdict"], entry.get("reason"), stamp, entry["criterion_id"]),
            )
            applied.append(
                {
                    "criterion_id": entry["criterion_id"],
                    "record_id": record_id,
                    "verdict": entry["verdict"],
                    "method": entry["method"],
                    "verification_id": verification_id,
                    "evidence_id": evidence_id,
                    "was": criterion["state"],
                }
            )
        for record_id in record_ids:
            _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"verify --from-report {dispatch_id}",
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)

    lines.append(
        f"applied {len(applied)} of {len(selected)} verdict(s) from report {report_id} "
        f"(dispatch {dispatch_id}), as {ctx.actor}"
    )
    for row in applied:
        lines.append(
            f"  {row['criterion_id']}  {row['was']} -> {row['verdict']} "
            f"({row['method']})  verification {row['verification_id']}"
            + (f"  evidence {row['evidence_id']}" if row["evidence_id"] else "")
        )
    if args.only:
        lines.append(
            f"--only: {len(selected)} of the report's {len(verdicts)} verdict(s) selected; "
            "the rest are unapplied and stay readable in the report"
        )
    lines.append(f"not applied ({len(not_applied)}):")
    lines += [f"  - {row['criterion_id']}: {row['why']}" for row in not_applied] or ["  (none)"]
    if already_applied:
        # KYO-415.c4/c5: named entry by entry, beside `not applied`, so a rerun that
        # wrote nothing says why rather than reading as a report with nothing in it.
        lines.append(
            f"already applied ({len(already_applied)}): each criterion's latest "
            f"verification already came from report {report_id}, so nothing was recorded "
            f"again — pass --again (`kyo verify --from-report {dispatch_id} --again`) to "
            "apply them anyway:"
        )
        lines += [
            f"  - {row['criterion_id']}: {row['verdict']}, verification {row['verification_id']}"
            for row in already_applied
        ]
    if newer_verdict_kept:
        # KYO-511: named the same way, so a skip is never read as a report with nothing
        # in it — and so `kyo accept`, which prints this step's lines, says it too.
        lines.append(
            f"newer verdict kept ({len(newer_verdict_kept)}): each criterion's latest "
            f"verdict was recorded after report {report_id} was ingested, so the report's "
            "is the older word and was not applied over it — pass --again "
            f"(`kyo verify --from-report {dispatch_id} --again`) to apply it anyway:"
        )
        lines += [
            f"  - {row['criterion_id']}: report says {row['verdict']}; latest is "
            f"{row['latest_verdict']} ({row['latest_method']}) by {row['latest_author']} "
            f"at {row['latest_at']}, verification {row['verification_id']}"
            for row in newer_verdict_kept
        ]

    # KYO-272. `_verify_one` (KYO-267) prints this same finding, and this is the path
    # most `unmet-unobserved` verdicts actually arrive by: a worker's report, not a
    # controller typing `kyo verify` by hand (KYO-264.c3, KYO-265.c3). Computed after
    # the transaction, over every record an applied verdict touched, so a record whose
    # wait predates this call and one this call just created are found the same way —
    # through the one predicate `kyo brief`, `kyo next` and `kyo doctor` also call. At
    # the moment a report is ingested its dispatch is already `reported`, not
    # `running` (KYO-90), so `ungated_unobserved`'s own running-dispatch check does not
    # suppress the very verdicts this command exists to apply.
    from . import cmd_next  # local: keeps the evidence layer off the review layer's

    gate_prompts = [
        waiting
        for waiting in (
            cmd_next.ungated_unobserved(store, record_id) for record_id in record_ids
        )
        if waiting is not None
    ]
    lines += gate_prompt_lines(gate_prompts)

    # KYO-415.c7: an unmet verdict just landed on a record whose status says complete.
    completed = [
        found
        for found in (
            completed_with_unmet(store, record_id)
            for record_id in dict.fromkeys(
                row["record_id"] for row in applied if row["verdict"] in UNMET_CRITERION_STATES
            )
        )
        if found is not None
    ]
    lines += [completed_with_unmet_line(found) for found in completed]

    return Output(
        lines,
        {
            "ok": True,
            "command": "verify",
            "from_report": dispatch_id,
            "report_id": report_id,
            "record_id": dispatch["record_id"],
            "applied": applied,
            "not_applied": not_applied,
            # KYO-415.c4: skipped because this report already applied them.
            "already_applied": already_applied,
            # KYO-511: skipped because a verdict newer than the report is latest.
            "newer_verdict_kept": newer_verdict_kept,
            "again": again,
            "records": [after[record_id] for record_id in record_ids],
            # KYO-272: the same finding a text reader gets, for a `--json` caller.
            "ungated_unobserved": gate_prompts,
            # KYO-415.c7.
            "completed_with_unmet": completed,
        },
    )


def cmd_verify(ctx: Context, args: Namespace) -> Output:
    """``kyo verify <criterion-id> --verdict ... --method ... [--evidence-file f]``,
    or ``kyo verify --from-report <dispatch-id> [--only <criterion-id>...]``.

    KYO-4.c3: records a verification event; the criterion's ``state`` is then
    DERIVED from it (law 3), never set as a bare flip. ``--verdict`` only accepts
    :data:`kyosei.contracts.VERIFY_VERDICTS` — the two void states are premise
    judgments, not verified facts, and remain ``kyo edit --criterion --state``'s job.

    **KYO-198: ``--from-report``.** ``kyo report`` prints a worker's verdicts and then
    says, correctly, that nothing was applied (KYO-7.c4) — and the controller then
    extracted each criterion's evidence from the report JSON by hand and ran one
    ``kyo verify --evidence-file`` per criterion, for every report. Every field that
    command asks for is already in the stored report, in the
    shape it asks for it, and the controller-audience rule "read, don't retype" says
    the courier step is where distinctions flatten. So the courier step goes and the
    deliberate one stays: nothing is applied until the controller runs this command,
    and ``--only`` applies the verdicts they have checked and leaves the rest.
    """
    if getattr(args, "from_report", None):
        return _verify_from_report(ctx, args)
    return _verify_one(ctx, args)


def _verify_one(ctx: Context, args: Namespace) -> Output:
    """One criterion, from flags. The shape ``kyo verify`` has always had."""
    store = ctx.store()
    _refuse_from_report_flags_without_from_report(args)
    missing = [
        flag
        for flag, value in (
            ("CRITERION_ID", args.criterion_id),
            ("--verdict", args.verdict),
            ("--method", args.method),
        )
        if not value
    ]
    if missing:
        raise UsageError(
            f"kyo verify needs {', '.join(missing)}. Verifying one criterion takes a "
            "criterion id, --verdict and --method; applying a dispatch report's own "
            "verdicts instead takes `kyo verify --from-report <dispatch-id>` and none "
            "of the three"
        )
    if args.verdict not in VERIFY_VERDICTS:
        raise UsageError(
            f"{args.verdict!r} is not a verdict `kyo verify` records; known: "
            f"{', '.join(sorted(VERIFY_VERDICTS))} "
            f"({', '.join(sorted(EDIT_DIRECT_CRITERION_STATES))} go through "
            "`kyo edit --criterion --state ... --why` instead)"
        )
    if args.method not in VERIFICATION_METHODS:
        raise UsageError(
            f"{args.method!r} is not a verification method; known: "
            f"{', '.join(VERIFICATION_METHODS)}"
        )
    reason = _reason(args)

    with store.transaction():
        criterion = store.criterion(args.criterion_id)
        record_id = criterion["record_id"]
        check_criterion_state(args.criterion_id, args.verdict, reason)
        before = {record_id: export.record_document(store, record_id)}

        evidence_id: str | None = None
        if args.evidence_file:
            text = read_prose(args.evidence_file, what="--evidence-file")
            if not text.strip():
                raise UsageError("--evidence-file must not be empty")
            evidence_id, _digest = _add_evidence(
                store, record_id, text, supports_id=args.criterion_id, author=ctx.actor
            )

        verification_id, seq = store.new_child_id(record_id, "verification")
        stamp = now()
        store.conn.execute(
            "INSERT INTO verifications (id, criterion_id, record_id, seq, verdict, method,"
            " reason, author, created_at, evidence_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verification_id,
                args.criterion_id,
                record_id,
                seq,
                args.verdict,
                args.method,
                reason,
                ctx.actor,
                stamp,
                evidence_id,
            ),
        )
        # Law 3: derive the criterion's state from the verification just recorded,
        # in the same transaction — `kyo doctor`'s check_criterion_verification_
        # agreement is the second, enforcing check that these can never desync.
        store.conn.execute(
            "UPDATE criteria SET state = ?, reason = ?, updated_at = ? WHERE id = ?",
            (args.verdict, reason, stamp, args.criterion_id),
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"verify {args.criterion_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)

    lines = [
        f"verified {args.criterion_id}",
        echo_value("criterion state", f"{criterion['state']} -> {args.verdict}"),
        echo_value("method", args.method),
    ]
    if evidence_id:
        lines.append(echo_value("evidence", evidence_id))
    if reason is not None:
        lines += echo_block("reason", reason)

    # KYO-267. The verdict is stored; the *wait* it just recorded is findable by
    # nobody. Computed after the transaction, so the criterion's new state is the one
    # being read (law 3), and read through the same predicate `kyo brief`, `kyo next`
    # and `kyo doctor` use — a controller told here that a record needs a gate and then
    # handed a brief that does not list it learns to believe neither surface.
    #
    # It suggests and never acts. `kyo gate` writes an instruction addressed to a
    # person, and minting one out of a criterion's reason would be the tool putting
    # words in somebody's mouth. No gates row is written here, and `tests/test_gates.py`
    # proves that as well as proving the line appears.
    from . import cmd_next  # local: keeps the evidence layer off the review layer's

    waiting = (
        cmd_next.ungated_unobserved(store, record_id)
        if args.verdict == cmd_next.UNOBSERVED_CRITERION_STATE
        else None
    )
    if waiting is not None:
        lines.append(
            "nothing makes this wait findable: no open `awaiting:*` gate and no running "
            "dispatch, so it is in the desktop's Awaiting list nowhere and in `kyo "
            "brief`'s gated work nowhere. Gate it yourself — this command wrote no gate:"
        )
        lines.append(f"  {waiting['remedy']}")
    # KYO-415.c7: recorded, and the contradiction with the record's status named.
    completed = (
        completed_with_unmet(store, record_id)
        if args.verdict in UNMET_CRITERION_STATES
        else None
    )
    if completed is not None:
        lines.append(completed_with_unmet_line(completed))
    return Output(
        lines,
        {
            "ok": True,
            "command": "verify",
            "verification_id": verification_id,
            "record_id": record_id,
            # KYO-267: the same finding a text reader gets, for a `--json` caller who
            # would otherwise be handed the verdict and none of its consequence.
            "ungated_unobserved": waiting,
            # KYO-415.c7: the same finding a text reader gets.
            "completed_with_unmet": completed,
            "record": after[record_id],
        },
    )


def cmd_attach(ctx: Context, args: Namespace) -> Output:
    """``kyo attach <record-id> --file <capture> [--supports <claim-or-criterion-id>]``.

    KYO-4.c4: stores the text verbatim with its sha256 (law 5's echo-back), linked
    to whatever it supports.
    """
    store = ctx.store()
    text = read_prose(args.file, what="--file")
    if not text.strip():
        raise UsageError("an attached capture must not be empty")

    record_id = args.id
    with store.transaction():
        _require_record(store, record_id)
        before = {record_id: export.record_document(store, record_id)}
        if args.supports:
            _require_supports_target(store, args.supports)
        evidence_id, digest = _add_evidence(
            store, record_id, text, supports_id=args.supports, author=ctx.actor
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"attach {record_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)

    lines = [f"attached {evidence_id} to {record_id}"]
    if args.supports:
        lines.append(echo_value("supports", args.supports))
    lines += echo_block("evidence", text)
    lines.append(echo_value("sha256", digest))
    return Output(
        lines,
        {
            "ok": True,
            "command": "attach",
            "evidence_id": evidence_id,
            "record_id": record_id,
            "record": after[record_id],
        },
    )
