"""Doctor checks on records and what hangs off them: criterion states and verdicts,
redactions, statuses, plan membership, links, flags. Registered in
:data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import shlex

from . import contracts, ledger_scrub, links
from . import registry as registry_module
from .contracts import (
    ACYCLIC_LINK_TYPES,
    CRITERION_STATES,
    CRITERION_STATES_REQUIRING_REASON,
    EDIT_DIRECT_CRITERION_STATES,
    FLAG_TYPES,
    REDACTABLE,
    REDACTION_MARKER,
)
from .errors import KyoseiError
from .store import Store


def check_criterion_states(store: Store) -> list[str]:
    failures: list[str] = []
    for row in store.conn.execute("SELECT id, state, reason FROM criteria ORDER BY id"):
        if row["state"] not in CRITERION_STATES:
            failures.append(f"criterion {row['id']} has unknown state {row['state']!r}")
        elif row["state"] in CRITERION_STATES_REQUIRING_REASON and not (
            row["reason"] or ""
        ).strip():
            failures.append(f"criterion {row['id']} is {row['state']} with no reason (law 10)")
    return failures


def check_ungated_unobserved_criteria(store: Store) -> list[str]:
    """KYO-267: a criterion waiting on a person, with nothing that makes the wait
    findable — no open ``awaiting:*`` gate and no running dispatch.

    The class, and what each clause is for, is defined once in
    :func:`cmd_next.ungated_unobserved`; this check reads that predicate rather than
    re-deriving it, so `kyo doctor` cannot disagree with the brief section and the
    `kyo verify` follow-up line about which records are in it. The finding is one line
    per record, naming the criterion, its reason, and the exact `kyo gate` line that
    clears it — the same line the other two surfaces print, from the same builder.

    A NOTICE although it names a remedy command, which :data:`kyosei.doctor.NOTICE_CHECKS`'s
    bar otherwise keeps a problem: the condition is *supposed to occur* — recording
    ``unmet-unobserved`` for a person's observation is correct behaviour — and exit 3
    from the ``Stop`` hook over "you have not gated it yet" would make `kyo doctor`
    unreadable for the duration of every human-observed verification. Printed in full,
    on every run, exit 0 (docs/design/doctor.md#kyo-267).
    """
    from . import cmd_next

    try:
        waiting = cmd_next.ungated_unobserved_records(store)
    except KyoseiError:
        # The class is defined partly by the kind registry and the status ladder, so a
        # broken `kinds.yml`/`statuses.yml` makes it unanswerable. Silence here rather
        # than a raise: `check_kind_registry`, `check_scope_registry` and
        # `check_statuses` are the checks that report those files, and a doctor run
        # that ABORTS on one of them reports nothing at all — which is how a broken
        # registry hid every other finding in the same run.
        return []
    findings: list[str] = []
    for item in waiting:
        criteria = ", ".join(
            f"{c['id']} ({(c['reason'] or '').strip() or 'no reason recorded'})"
            for c in item["criteria"]
        )
        findings.append(
            f"{item['id']} ({item['status']}) is waiting on somebody to look — "
            f"{criteria} — and no open `awaiting:*` gate says so, with nothing running "
            f"against the record. Until it is gated the wait is in the desktop's "
            f"Awaiting list nowhere and in `kyo brief`'s gated work nowhere: "
            f"`{item['remedy']}`. Verifying the criterion "
            f"clears this too; either way the wait stops being invisible (KYO-267)"
        )
    return findings


def check_criterion_verification_agreement(store: Store) -> list[str]:
    """KYO-4.c5 (law 3): a criterion's ``state`` must agree with its latest
    verification's ``verdict``. ``kyo verify`` keeps the two in step by construction
    (it writes both in one transaction); this check exists for everything else that
    could desync them — a hand-edited export, a bug, a manufactured disagreement.

    KYO-19: it applies only while the criterion is in a state a verification can
    *reach*. :data:`EDIT_DIRECT_CRITERION_STATES` — the two void states — are
    premise judgments ("this no longer applies", "we chose not to pursue this"),
    which contracts.py already declares are not verified facts and which
    :data:`VERIFY_VERDICTS` is deliberately disjoint from. Demanding that a state
    no verification can record agree with a verification would fail doctor
    permanently after any `kyo split` of a verified criterion, with no sanctioned
    command able to restore agreement (docs/design/doctor.md#kyo-19;
    tests/test_doctor_void_criteria.py).

    Nothing is hidden by this: the verification history is append-only and stays
    readable, voiding still requires a reason (:func:`check_criterion_states`), and
    a desync between two states a verification *can* record still fails.
    """
    failures: list[str] = []
    for row in store.conn.execute("SELECT id, state FROM criteria ORDER BY id"):
        if row["state"] in EDIT_DIRECT_CRITERION_STATES:
            continue
        latest = store.latest_verification(row["id"])
        if latest is not None and latest["verdict"] != row["state"]:
            failures.append(
                f"criterion {row['id']} is {row['state']} but its latest verification "
                f"{latest['id']} recorded verdict {latest['verdict']!r} (law 3: derive, don't "
                "duplicate — the state must come from the verification)"
            )
    return failures


def check_completed_with_unmet_criteria(store: Store) -> list[str]:
    """KYO-415.c8: a record at the ladder's complete-role status holding a criterion in
    an unmet state.

    `kyo verify` records such a verdict on purpose — a regression found after a record was
    closed is a legitimate fact to store — and prints this same line at the moment it does
    (KYO-415.c7). This check is the half that lasts (docs/design/doctor.md#kyo-415). The
    class and the sentence come from :func:`cmd_evidence.completed_with_unmet` and its line
    builder, so the two surfaces cannot disagree about which records are in it.

    A NOTICE, and it argues the two things :data:`kyosei.doctor.NOTICE_CHECKS` asks of a
    remedy-naming check. That the condition is EXPECTED rather than broken: recording the
    regression is correct behaviour, and the interval before the controller splits or
    reopens the record is a legitimate state. And that failing would cost more than the
    finding is worth: `kyo doctor` runs from the ``Stop`` hook, and exit 3 there would paint
    every stop red over a judgment — split or reopen — that is the controller's to make.
    """
    from . import cmd_evidence, commands

    try:
        ladder = commands._load_status_ladder(store)
        found = [
            item
            for item in (
                cmd_evidence.completed_with_unmet(store, row["id"], ladder)
                for row in store.conn.execute(
                    "SELECT id FROM records WHERE status = ? ORDER BY seq", (ladder.complete,)
                )
            )
            if item is not None
        ]
    except KyoseiError:
        # A broken `statuses.yml` makes the complete role unanswerable; `check_statuses`
        # is the check that reports that, and aborting here would hide every other
        # finding in the run (the reasoning `check_ungated_unobserved_criteria` gives).
        return []
    return [cmd_evidence.completed_with_unmet_line(item) + " (KYO-415)" for item in found]


def _redact_rerun(item_id: str, why: str | None) -> str:
    """The exact `kyo redact` rerun that completes an interrupted redaction (KYO-405):
    a retry is accepted only with the reason the row already records."""
    why = why or ""
    if "\n" in why:
        return f"kyo redact {item_id} --why-file <a file holding the recorded reason>"
    return f"kyo redact {item_id} --why {shlex.quote(why)}"


def check_redactions(store: Store) -> list[str]:
    """KYO-22: a redacted evidence artifact is redacted *everywhere*.

    Two halves that must agree, so law 3 says derive one and check the other:
    ``redacted_at``/``redacted_why`` are set together or not at all, and a row
    marked redacted must actually hold the marker rather than its original text —
    a half-applied redaction is worse than none, because it reads as done.
    """
    failures: list[str] = []
    marked_ids = [
        row["id"]
        for table, _text, _record in REDACTABLE.values()
        for row in store.conn.execute(
            f"SELECT id FROM {table} WHERE redacted_at IS NOT NULL"  # noqa: S608
        )
    ]
    # KYO-426: a record's title, description or criterion redacted by id. No row column
    # carries it; its redaction event does, and the residue is read up to that event.
    fields = ledger_scrub.field_redactions(store)
    needles = [*marked_ids, *(item.record_id for item in fields)]
    # One pass over the ledger for every redacted id, not one per id (KYO-405 follow-up):
    # this runs from the Stop hook, and each id alone used to re-read every shard. The
    # same for the database's `events` table (KYO-426.c4): one scan for every id.
    shards = ledger_scrub.shards_naming(store, needles)
    event_rows = ledger_scrub.event_rows_naming(store, needles)
    for kind, (table, text_column, _record_column) in REDACTABLE.items():
        for row in store.conn.execute(
            f"SELECT id, {text_column} AS body, redacted_at, redacted_why"  # noqa: S608
            f" FROM {table} ORDER BY id"
        ):
            marked = bool(row["redacted_at"])
            if marked != bool((row["redacted_why"] or "").strip()):
                failures.append(
                    f"{kind} {row['id']} has redacted_at={row['redacted_at']!r} but "
                    f"redacted_why={row['redacted_why']!r} (law 3: these are set together)"
                )
            if marked and row["body"] != REDACTION_MARKER:
                failures.append(
                    f"{kind} {row['id']} is marked redacted but still holds its text — "
                    "a half-applied redaction reads as done and is not"
                )
            if not marked:
                continue
            # KYO-405: the row commits before the ledger is scrubbed, so a
            # row holding the marker says nothing about the ledger — and the db's own
            # `events` snapshots are a third copy. Keyed on the id by the scrub's own
            # rule, so a finding here is exactly what the rerun would change.
            residue = ledger_scrub.redaction_residue(
                store,
                row["id"],
                kind,
                REDACTION_MARKER,
                paths=shards.get(row["id"], []),
                event_rows=event_rows.get(row["id"], []),
            )
            if residue.total:
                failures.append(
                    f"{kind} {row['id']} is marked redacted but its text or original "
                    f"digest survives in {_residue_places(residue)} — the scrub after the "
                    "redaction's commit did not finish. Rerun it with the reason it was "
                    f"recorded with: `{_redact_rerun(row['id'], row['redacted_why'])}` "
                    "completes the scrub"
                )
    for item in fields:
        residue = ledger_scrub.field_redaction_residue(
            store,
            item.target_id,
            item.kind,
            item.record_id,
            item.seq,
            REDACTION_MARKER,
            paths=shards.get(item.record_id, []),
            event_rows=event_rows.get(item.record_id, []),
        )
        if residue.total:
            failures.append(
                f"{item.kind} {item.target_id} was redacted but its text survives in "
                f"{_residue_places(residue)} — the scrub after the redaction's commit did "
                "not finish. Rerun it with the reason it was recorded with: "
                f"`{_redact_rerun(item.target_id, item.why)}` completes the scrub"
            )
    return failures


def _residue_places(residue: ledger_scrub.RedactionResidue) -> str:
    """Where a redaction's residue is, as the finding names it."""
    return ", ".join(
        f"{count} {where}"
        for count, where in (
            (residue.ledger_lines, "line(s) in kyosei/export/_events/"),
            (residue.shift_note_lines, "line(s) in the shift-note ledger"),
            (residue.event_rows, "row(s) of the database's `events` table"),
        )
        if count
    )


def check_statuses(store: Store) -> list[str]:
    """Every stored status is one this project declares, and the retirement reason
    agrees with the status carrying it.

    KYO-101 turned the first half from a tuple membership test into a registry one,
    and that is what makes this check earn its keep rather than duplicate the
    write-time contract. `kyo move` refuses a status the registry does not declare —
    but ``kyosei/statuses.yml`` is an ordinary editable file, so a status can be
    renamed or dropped the day after the records sitting in it were written, and
    nothing at write time is watching then. This is the same shape as
    :func:`check_scope_registry` one dimension over, and the refusal is the registry's
    own sentence rather than a second wording of it (law 3).

    Nothing is rewritten here. A rename that stranded records is a decision for
    whoever made it — either move the records or restore the name — and a check that
    silently migrated them would be the "success that means nothing happened" law 6
    exists against.
    """
    failures: list[str] = []
    try:
        ladder = contracts.load_status_ladder(store.config.kyosei_dir)
    except KyoseiError as exc:
        # A ladder the contracts cannot bind to is a finding, not a traceback: every
        # other command already refuses on it, and `kyo doctor` is where a broken
        # registry is supposed to be reported rather than tripped over.
        return [str(exc)]
    retired = ladder.retired
    for row in store.conn.execute("SELECT id, status, wont_do_reason FROM records ORDER BY seq"):
        if row["status"] not in ladder:
            failures.append(
                registry_module.unknown_name_refusal(ladder.registry, row["status"])
                + f" — {row['id']} is stored in it"
            )
            continue
        if ladder.role(row["status"]) == contracts.ROLE_RETIRED and not (
            row["wont_do_reason"] or ""
        ).strip():
            failures.append(f"{row['id']} is {row['status']} with no recorded reason")
        if ladder.role(row["status"]) != contracts.ROLE_RETIRED and (
            row["wont_do_reason"] or ""
        ).strip():
            failures.append(
                f"{row['id']} is {row['status']} but carries a retirement reason "
                f"(only {retired!r} may, law 3: two fields that must agree, disagreeing)"
            )
    return failures


def check_plan_membership(store: Store) -> list[str]:
    failures: list[str] = []
    plans = {
        row["id"]: row["plan_id"]
        for row in store.conn.execute("SELECT id, plan_id FROM records")
    }
    for record_id, plan_id in sorted(plans.items()):
        if plan_id is None:
            continue
        if plan_id not in plans:
            failures.append(f"{record_id} belongs to missing plan {plan_id}")
            continue
        seen = {record_id}
        cursor = plan_id
        while cursor is not None:
            if cursor in seen:
                failures.append(f"{record_id} is in a plan-membership cycle through {cursor}")
                break
            seen.add(cursor)
            cursor = plans.get(cursor)
    return failures


def check_links(store: Store) -> list[str]:
    failures: list[str] = []
    for edge_type in sorted(ACYCLIC_LINK_TYPES):
        cycle = links.find_cycle(store, edge_type)
        if cycle:
            failures.append(f"cycle in {edge_type}: " + " -> ".join(cycle))
    for row in store.conn.execute("SELECT id, src_id, dst_id FROM links WHERE src_id = dst_id"):
        failures.append(f"link {row['id']} is a self-link on {row['src_id']}")
    return failures


# ------------------------------------------------------------------------ M6: KYO-8


def check_flag_types(store: Store) -> list[str]:
    """Every flag names a known flag_type (contracts.FLAG_TYPES)."""
    return [
        f"flag {row['id']} has unknown flag_type {row['flag_type']!r}"
        for row in store.conn.execute("SELECT id, flag_type FROM flags ORDER BY id")
        if row["flag_type"] not in FLAG_TYPES
    ]


def check_flag_resolution_consistency(store: Store) -> list[str]:
    """KYO-8: ``resolved_at``/``resolved_by`` are set together or not at all —
    `kyo flag resolve` is the only writer of either, and always sets both in the
    same statement; this check exists for everything else that could desync
    them (a hand-edited export, a bug), mirroring
    ``check_criterion_verification_agreement``'s role for criteria."""
    failures: list[str] = []
    for row in store.conn.execute(
        "SELECT id, resolved_at, resolved_by FROM flags ORDER BY id"
    ):
        has_at = bool((row["resolved_at"] or "").strip())
        has_by = bool((row["resolved_by"] or "").strip())
        if has_at != has_by:
            failures.append(
                f"flag {row['id']} has resolved_at={row['resolved_at']!r} but "
                f"resolved_by={row['resolved_by']!r} (law 3: these must be set together)"
            )
    return failures


def check_open_standing_obligations(store: Store) -> list[str]:
    """KYO-554.c9: how many standing docs obligations are open, as one NOTICE line.

    A standing obligation is raised by a record (a decision naming a section) rather
    than by a dispatch's report, so nothing on a branch will ever surface it again; `kyo
    brief` lists each one and this says the count at every session end. A NOTICE: an open
    obligation is the expected state between filing a decision and editing the section
    it names, and failing the ``Stop`` hook over that interval would make every decision
    a hook error until the document caught up (docs/reference/topics/docs-governance.md).
    """
    rows = store.open_standing_obligations()
    if not rows:
        return []
    records = sorted({row["record_id"] for row in rows})
    return [
        f"{len(rows)} standing docs obligation(s) open on {len(records)} record(s) "
        f"({', '.join(records)}): each names a section a record says should change. "
        "`kyo brief` lists them; settle each with `kyo docs settle <id> --verdict "
        "updated|not-affected --method <how>` once the section says what was decided "
        "(KYO-554)"
    ]
