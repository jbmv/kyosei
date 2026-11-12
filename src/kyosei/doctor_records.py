"""Doctor checks on records and what hangs off them: criterion states and verdicts,
redactions, statuses, plan membership, links, flags. Registered in
:data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import json
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path

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
            (residue.shift_note_lines, "line(s) in the note's own ledger"),
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


# ----------------------------------------------------------------------------- KYO-646
#
# P12 of decision KYO-640 (KYO-638 section 4.6): nothing written before credits existed is
# rewritten, so `kyo doctor` says how many rows lack one (a NOTICE) and refuses the same
# kind of row written after enforcement began (a PROBLEM). The tier is a property of a check
# (:data:`kyosei.doctor.NOTICE_CHECKS`), so the two tiers are two checks.

#: The schema_version from which a row that needs a credit carries one. The cut-off is read
#: from the ledger line of the event that wrote the row, which every line stamps (KYO-181),
#: and is not configured.
CREDITS_SINCE_SCHEMA = 28

_HUMAN_GATE_TYPES = ("awaiting:human-review", "awaiting:human-run")

#: The end of an events-ledger line: the keys are written sorted, so the stamp and the
#: sequence number are the last two, after any body however large. Reading them from the
#: tail means a shard of 4 MB costs one pass and no JSON parse.
_LINE_TAIL = re.compile(rb'(?:"schema_version": (\d+), )?"seq": (\d+)\}\s*$')

#: Where a shard's runs are kept, in the doctor cache (``doctor_consumers``).
_STAMP_CACHE_NAME = "ledger-stamps.json"


def _shard_runs(path: Path) -> list[list[int]]:
    """``[[first_seq, last_seq, schema_version], ...]`` for one ledger file, in line order.

    A line with no stamp (written before KYO-181) is version 0. A line whose tail does not
    read as above is parsed whole; one that does not parse at all is left out, which
    ``check_ledger_gaps`` and the round trip name by line.
    """
    runs: list[list[int]] = []
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            match = _LINE_TAIL.search(raw[-96:])
            if match:
                version = int(match.group(1) or 0)
                seq = int(match.group(2))
            else:
                try:
                    row = json.loads(raw)
                    version, seq = int(row.get("schema_version") or 0), int(row["seq"])
                except (ValueError, KeyError, TypeError):
                    continue
            if runs and runs[-1][2] == version and runs[-1][1] < seq:
                runs[-1][1] = seq
            else:
                runs.append([seq, seq, version])
    return runs


def ledger_stamp_runs(store: Store) -> list[list[int]]:
    """Every events-ledger line's ``schema_version``, as seq ranges.

    Each file is scanned once and its runs kept in the doctor cache under the file's name,
    size and mtime, so the next run reads only a file that changed (in practice the newest
    shard): the ledger of this repository is 295 MB, which a Stop hook cannot parse each
    turn.
    """
    from . import events_ledger
    from .doctor_consumers import doctor_cache_dir, read_cache, write_cache

    cache_path = doctor_cache_dir(store) / _STAMP_CACHE_NAME
    cached = (read_cache(cache_path) or {}).get("files", {})
    fresh: dict[str, dict] = {}
    runs: list[list[int]] = []
    for path in events_ledger.events_files(store):
        stat = path.stat()
        key = f"{path.parent.name}/{path.name}"
        entry = cached.get(key)
        if entry and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime_ns:
            file_runs = entry["runs"]
        else:
            file_runs = _shard_runs(path)
        fresh[key] = {"size": stat.st_size, "mtime": stat.st_mtime_ns, "runs": file_runs}
        runs.extend(file_runs)
    if fresh != cached:
        write_cache(cache_path, {"files": fresh})
    return runs


def _credit_kinds(store: Store) -> set[str]:
    """The kinds whose records name the person who decided them.

    ``policy.requires_credit`` where a kind declares it, and the kind ``decision`` where
    nothing does (KYO-640: set on decision). A kind registry that does not load falls back
    to ``decision``; ``kind_registry`` reports the file.
    """
    from . import kinds as kinds_module

    try:
        registry = kinds_module.load_kind_registry(store.config.kyosei_dir)
    except KyoseiError:
        return {"decision"}
    return {
        name
        for name, kind in registry.kinds.items()
        if kind.policy.get("requires_credit", name == "decision")
    }


def uncredited_rows(store: Store) -> list[dict]:
    """Every human-observed verdict, human-gate clearing and decision record that no credit
    names, each with the ``schema_version`` its writing event's ledger line is stamped with.

    A row is ``{"record": id, "type": "verdict"|"gate"|"decision", "id": row id,
    "schema": int, "withdrawn": bool, "written_at": its timestamp}``. A gate cleared as
    ``withdrawn`` says plainly that nobody answered and is not counted, and neither is a
    verdict a later agent-observed verdict on the same criterion restates: that is the
    remedy the legacy notice names (KYO-667). The writing
    event is the record's first event at or after the row's own timestamp (an event is
    stamped just after its row); a row with no such event, or whose line is not in the
    ledger, counts as written before credits, because the ledger-gap check is what names
    that and exit 3 here would name the wrong thing.
    """
    # KYO-670: only a CURRENT credit covers a row. A withdrawn credit covers nothing, and a
    # superseded one is covered by whatever superseded it or by nothing, so a row whose
    # credit was withdrawn (or superseded by one later withdrawn) counts again.
    credited = (
        "NOT EXISTS (SELECT 1 FROM credit_subjects cs JOIN credits c ON c.id = cs.credit_id "
        "WHERE cs.subject_kind = ? AND cs.subject_id = {}.id "
        "AND c.superseded_by IS NULL AND c.withdrawn_at IS NULL)"
    )
    found: list[tuple[str, str, str, str]] = []  # (type, record, id, written_at)
    for row in store.conn.execute(
        "SELECT v.id, v.record_id, v.created_at FROM verifications v WHERE v.method = "
        "'human-observed' AND " + credited.format("v") + " AND NOT EXISTS (SELECT 1 FROM "
        "verifications later WHERE later.criterion_id = v.criterion_id AND later.seq > v.seq "
        "AND later.method = 'agent-observed') ORDER BY v.record_id, v.seq",
        ("verification",),
    ):
        found.append(("verdict", row["record_id"], row["id"], row["created_at"]))
    marks = ", ".join("?" * len(_HUMAN_GATE_TYPES))
    for row in store.conn.execute(
        f"SELECT g.id, g.record_id, g.cleared_at FROM gates g WHERE g.gate_type IN ({marks}) "
        "AND g.cleared_at IS NOT NULL AND COALESCE(g.cleared_as, '') != 'withdrawn' AND "
        + credited.format("g")
        + " ORDER BY g.record_id, g.seq",
        (*_HUMAN_GATE_TYPES, "gate"),
    ):
        found.append(("gate", row["record_id"], row["id"], row["cleared_at"]))
    kinds = sorted(_credit_kinds(store))
    if kinds:
        kind_marks = ", ".join("?" * len(kinds))
        for row in store.conn.execute(
            f"SELECT r.id, r.created_at FROM records r WHERE r.kind IN ({kind_marks}) AND "
            + credited.format("r")
            + " ORDER BY r.seq",
            (*kinds, "record"),
        ):
            found.append(("decision", row["id"], row["id"], row["created_at"]))
    if not found:
        return []
    runs = ledger_stamp_runs(store)
    events: dict[str, list[tuple[str, int]]] = {}
    for event in store.conn.execute(
        "SELECT record_id, at, seq FROM events WHERE record_id IS NOT NULL ORDER BY seq"
    ):
        events.setdefault(event["record_id"], []).append((event["at"], event["seq"]))

    def schema_of(record: str, written_at: str) -> int:
        for at, seq in events.get(record, ()):
            if at >= written_at:
                for low, high, version in runs:
                    if low <= seq <= high:
                        return version
                return 0
        return 0

    withdrawn = {
        row["subject_id"]
        for row in store.conn.execute(
            "SELECT cs.subject_id FROM credit_subjects cs JOIN credits c ON c.id = cs.credit_id"
            " WHERE c.withdrawn_at IS NOT NULL"
        )
    }
    return [
        {
            "record": record,
            "type": kind,
            "id": row_id,
            "schema": schema_of(record, written_at),
            "withdrawn": row_id in withdrawn,
            "written_at": written_at,
        }
        for kind, record, row_id, written_at in found
    ]


def _legacy(row: dict) -> bool:
    """Whether an uncredited row is the legacy notice's: written before credits existed,
    or uncredited by `kyo credit --withdraw` (KYO-670), a command and so no bypass."""
    return row["schema"] < CREDITS_SINCE_SCHEMA or row["withdrawn"]


def _declares_someone(text: str | None, path: Path) -> bool:
    """Whether a config.yml's text declares a person, by the rule the store itself loads
    ``people:`` with. Text that does not parse declares nobody."""
    import yaml

    from .config import PEOPLE_KEY, parse_people

    if text is None:
        return False
    try:
        raw = yaml.safe_load(text)
        return isinstance(raw, dict) and bool(parse_people(raw.get(PEOPLE_KEY), path))
    except (yaml.YAMLError, KyoseiError):
        return False


def _row_stamp(seconds: float) -> str:
    """An instant in the fixed-width format of a row's timestamp, so the two compare as
    strings (:data:`kyosei.clock.TIMESTAMP_FORMAT`, to the millisecond)."""
    stamp = datetime.fromtimestamp(seconds, UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def first_person_declared_at(store: Store) -> str:
    """KYO-684: when this project's ``people:`` first declared someone, as a row timestamp.

    The store records no such time, so it is read from the history of ``config.yml``: the
    author date of the oldest commit whose copy declares a person. The author date, because
    a rebase or a history rewrite moves the committer date and leaves this one alone.

    Where no commit holds a declaration, the declaration is an edit not yet committed, or
    the store has no history to ask; the file's modification time then bounds it from
    above, and is the moment of the edit unless the file was edited again since. A later
    edit can only move a bypass into the notice, never a pre-declaration row into the
    problem. The caller has already found ``people:`` non-empty, so the file exists.
    """
    from .git_queries import _git_file_at, run_git

    path = store.config.config_path
    try:
        log = run_git(path.parent, "log", "--reverse", "--format=%H %at", "--", path.name)
    except OSError:
        log = None
    if log is not None and log.returncode == 0:
        for line in log.stdout.splitlines():
            sha, _, author_time = line.partition(" ")
            if _declares_someone(_git_file_at(path.parent, sha, f"./{path.name}"), path):
                return _row_stamp(int(author_time))
    return _row_stamp(path.stat().st_mtime)


def _since_credits_by_declaration(store: Store) -> tuple[list[dict], list[dict], str]:
    """Rows uncredited since credits existed, as (written before the first person was
    declared, written at or after it, that instant).

    Git is asked only when such a row exists, so a store with none never pays for it.
    """
    rows = [r for r in uncredited_rows(store) if not _legacy(r)]
    if not rows:
        return [], [], ""
    declared_at = first_person_declared_at(store)
    return (
        [r for r in rows if r["written_at"] < declared_at],
        [r for r in rows if r["written_at"] >= declared_at],
        declared_at,
    )


def _open_first(store: Store, records: set[str]) -> list[str]:
    """``records`` with the unsettled ones first, each group in id order by sequence."""
    from . import commands

    try:
        ladder = commands._load_status_ladder(store)
    except KyoseiError:
        ladder = None
    status = {
        row["id"]: row["status"]
        for row in store.conn.execute("SELECT id, status FROM records ORDER BY seq")
    }
    order = {record: position for position, record in enumerate(status)}

    def settled(record: str) -> bool:
        return ladder is not None and ladder.is_settled(status.get(record, ""))

    return sorted(records, key=lambda r: (settled(r), order.get(r, len(order))))


def check_credit_coverage_legacy(store: Store) -> list[str]:
    """KYO-646.c1: rows that need a credit and were written before schema 28 have none.

    A NOTICE, and it argues the two things :data:`kyosei.doctor.NOTICE_CHECKS` asks of a
    check whose finding names a remedy. The condition is EXPECTED: every verdict, gate
    clearing and decision made before credits existed is in it, nothing is rewritten
    (KYO-640), and it falls as the maintainer approves reconstructed credits in batches.
    And failing would cost more than the finding is worth: exit 3 from the Stop hook over
    history no command can change at once would paint every stop red until the last batch.
    The same kind of row written at schema 28 or later is :func:`check_credit_coverage`'s.
    A project declaring nobody has :func:`check_credit_people_undeclared`'s notice instead.
    """
    if not store.config.people:
        return []
    rows = [r for r in uncredited_rows(store) if _legacy(r)]
    if not rows:
        return []
    count = {
        kind: sum(1 for r in rows if r["type"] == kind) for kind in ("verdict", "gate", "decision")
    }
    by_record: dict[str, dict[str, int]] = {}
    for r in rows:
        tally = by_record.setdefault(r["record"], {})
        tally[r["type"]] = tally.get(r["type"], 0) + 1
    words = {"verdict": "verdict", "gate": "gate", "decision": "decision"}
    listing = [
        f"{record} ("
        + ", ".join(
            f"{n} {words[kind]}{'s' if n != 1 else ''}" for kind, n in by_record[record].items()
        )
        + ")"
        for record in _open_first(store, set(by_record))
    ]
    from .currency import some_of

    return [
        f"{count['verdict']} human-observed verdicts, {count['gate']} human-gate clearings and "
        f"{count['decision']} decision records were written before credits existed (before "
        f"schema_version {CREDITS_SINCE_SCHEMA}) and credit no person; `kyo credit` adds a "
        "reconstructed credit, and a verdict that was an agent's own observation is restated "
        "with `kyo verify --method agent-observed` (KYO-646)",
        "by record, open records first: " + some_of(listing, limit=6),
        *(
            [
                f"{len(withdrawn)} of them had their credit withdrawn with `kyo credit "
                f"--withdraw` ({some_of(withdrawn)}) and are counted again as uncredited "
                "(KYO-670)"
            ]
            if (withdrawn := [r["id"] for r in rows if r["withdrawn"]])
            else []
        ),
    ]


def check_credit_coverage(store: Store) -> list[str]:
    """KYO-646.c2: a row that needs a credit, written at schema 28 or later, has none.

    A PROBLEM: enforcement (``kyo verify``, ``kyo confirm``, ``kyo ungate``, ``kyo new`` of
    a kind that requires a credit) refuses to write such a row, so one in the store written
    after the project first declared someone means the enforcement was bypassed, for
    example by a ledger merged from an older ``kyo``. A row written before that is
    :func:`check_credit_coverage_before_people`'s, and a project still declaring nobody is
    not enforced at all (KYO-656), so this asks nothing of it.
    """
    if not store.config.people:
        return []
    _, rows, declared_at = _since_credits_by_declaration(store)
    if not rows:
        return []
    from .currency import some_of

    return [
        f"{len(rows)} human-observed verdict(s), human-gate clearing(s) or decision record(s) "
        f"written at schema_version {CREDITS_SINCE_SCHEMA} or later, after `people:` first "
        f"declared someone ({declared_at[:10]}), credit no person "
        f"({some_of([r['id'] for r in rows])}): the commands refuse that while someone is "
        "declared, so the enforcement was bypassed, for example by a ledger merged from an "
        "older `kyo`. `kyo credit` names the person who observed or decided it (KYO-646)"
    ]


def check_credit_coverage_before_people(store: Store) -> list[str]:
    """KYO-684.c1: a row that needs a credit was written before anyone was declared.

    A NOTICE, and it argues the two things :data:`kyosei.doctor.NOTICE_CHECKS` asks. The
    condition is EXPECTED: until a project declares someone nothing is enforced (KYO-656),
    so these rows were correct when written, and every one of them appears the moment the
    first person is declared. And failing would cost more than the finding is worth: exit
    3 from the Stop hook for adopting credits, over rows no command had refused, would
    make declaring a person the act that breaks the project's stops.
    """
    if not store.config.people:
        return []
    rows, _, declared_at = _since_credits_by_declaration(store)
    if not rows:
        return []
    from .currency import some_of

    return [
        f"{len(rows)} human-observed verdict(s), human-gate clearing(s) or decision record(s) "
        f"were written before `people:` first declared someone ({declared_at[:10]}), when "
        "credits were not enforced here, and credit no person "
        f"({some_of([r['id'] for r in rows])}): `kyo credit` adds a reconstructed credit, and "
        "a verdict that was an agent's own observation is restated with "
        "`kyo verify --method agent-observed` (KYO-684)"
    ]


def check_credit_people_undeclared(store: Store) -> list[str]:
    """KYO-656.c3 (KYO-654, DC12): this project declares no person, so credits are off.

    A NOTICE, because nothing is wrong: enforcement applies only where ``people:`` declares
    someone, so that upgrading kyo refuses nothing before the operator has chosen. It
    replaces the two coverage checks, which would otherwise count every row in such a
    project, and setup decision D13 recorded under ``onboarding:`` silences it, because the
    operator has then chosen.
    """
    from .config import ONBOARDING_KEY, config_path_for, parse_onboarding, read_raw_config

    if store.config.people:
        return []
    root = store.config.root
    try:
        answers = parse_onboarding(read_raw_config(root).get(ONBOARDING_KEY), config_path_for(root))
    except KyoseiError:
        answers = {}
    if "D13" in answers:
        return []
    return [
        "no person is declared under `people:` in kyosei/config.yml, so credits are not "
        "enforced here: a human-observed verdict, `kyo confirm` and a person-gate clearing are "
        "recorded without naming anyone. Declare one under `people:` to turn credits on, or "
        "record `D13: declined` under `onboarding:`; `kyo onboard` asks this as setup decision "
        "D13, and D14 asks whether credits may read transcripts (`read_transcripts:`) (KYO-656)"
    ]


def check_credit_transcripts_appeared(store: Store) -> list[str]:
    """KYO-646: credits recorded as ``transcript-absent`` whose transcript is now here.

    A NOTICE: the credit is valid as written, the check can simply be made now, and a
    transcript reaching this machine later (a restored backup, a synced home directory) is
    not a fault. ``kyo credit --recheck <id>`` redoes the check (KYO-653) and supersedes the
    credit, which then stops being named: only a current credit is looked at.
    """
    from . import cmd_sessions
    from .config import transcripts_opted_in
    from .currency import some_of
    from .harness import claude_code_transcript as transcript

    if not transcripts_opted_in(store.config):
        return []
    config = transcript.config_dir()
    root = store.config.kyosei_dir.parent
    appeared: list[str] = []
    for row in store.conn.execute(
        "SELECT id, source_session FROM credits WHERE transcript_check = 'transcript-absent' "
        "AND superseded_by IS NULL AND withdrawn_at IS NULL AND source_kind = 'claude-code' "
        "AND source_session IS NOT NULL ORDER BY record_id, seq"
    ):
        try:
            recorded = cmd_sessions.recorded_transcript_path(root, row["source_session"])
            found = transcript.candidate_transcripts(
                row["source_session"], config=config, recorded=recorded
            )
        except (ValueError, OSError):
            continue
        if found:
            appeared.append(row["id"])
    if not appeared:
        return []
    return [
        f"{len(appeared)} credit(s) marked transcript-absent now have their transcript on "
        f"this machine ({some_of(appeared)}): `kyo credit --recheck <id>` checks the quote "
        "against it (KYO-646)"
    ]


# ------------------------------------------------------------------ discussions (KYO-662)

#: How many of the latest weeks :func:`check_discussion_shares` prints.
DISCUSSION_SHARE_WEEKS = 4

#: How a decision's discussion was opened, in the order the shares are printed.
OPENED_LIVE = "live"
OPENED_LATE = "late"
OPENED_UNINDEXED = "unindexed"


def _iso_week(stamp: object) -> str | None:
    """``2026-W46`` for a stored UTC stamp; None when it does not parse."""
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    year, week, _ = moment.astimezone(UTC).isocalendar()
    return f"{year}-W{week:02d}"


def _discussion_kinds(store: Store) -> set[str]:
    """The kinds filed from a discussion: ``policy.requires_discussion``, and ``decision``
    when the kind registry does not load (``kind_registry`` reports the file)."""
    from . import kinds as kinds_module

    try:
        registry = kinds_module.load_kind_registry(store.config.kyosei_dir)
    except KyoseiError:
        return {"decision"}
    return {name for name, kind in registry.kinds.items() if kind.requires_discussion}


def discussion_shares(store: Store) -> dict[str, dict[str, int]]:
    """Per ISO week (UTC) of filing, how the discussion behind each decision was opened.

    ``live``: opened at an indexed turn before the decision was filed. ``late``: opened by
    the filing itself (``--opened-late``) at an indexed turn. ``unindexed``: its opening
    names no turn of the turn index, live or late, so its start is known only from the
    time the opening was written. ``none`` counts decisions filed from no discussion, which
    the shares leave out. ``skips`` counts the turns of that week the discussion check named
    and the agent dismissed with ``kyo discuss skip``.
    """
    from . import cmd_discuss, cmd_turns

    weeks: dict[str, dict[str, int]] = {}

    def bump(week: str | None, key: str) -> None:
        if week is not None:
            row = weeks.setdefault(
                week, {OPENED_LIVE: 0, OPENED_LATE: 0, OPENED_UNINDEXED: 0, "none": 0, "skips": 0}
            )
            row[key] += 1

    kinds = sorted(_discussion_kinds(store))
    if kinds:
        marks = ", ".join("?" * len(kinds))
        for row in store.conn.execute(
            f"SELECT id, created_at FROM records WHERE kind IN ({marks}) ORDER BY seq",  # noqa: S608
            kinds,
        ):
            discussion = cmd_discuss.discussion_of(store, row["id"])
            if discussion is None:
                bump(_iso_week(row["created_at"]), "none")
                continue
            opened = next(
                (ref for ref in store.turn_refs(discussion) if ref["role"] == "opened"), None
            )
            if opened is None or opened["index_state"] != contracts.TURN_REF_INDEXED:
                how = OPENED_UNINDEXED
            elif opened["late"]:
                how = OPENED_LATE
            else:
                how = OPENED_LIVE
            bump(_iso_week(row["created_at"]), how)
    for line in cmd_turns.read_lines(store.config.kyosei_dir.parent):
        skip = line.get("skip")
        if isinstance(skip, dict):
            bump(_iso_week(skip.get("at")), "skips")
    return weeks


def check_discussion_shares(store: Store) -> list[str]:
    """KYO-662: per week, the share of decisions whose discussion was opened live, opened
    late or never indexed, and the number of Stop-check prompts dismissed with a skip.

    A NOTICE, one line per week for the latest :data:`DISCUSSION_SHARE_WEEKS` weeks that
    have a decision filed from a discussion or a skip. Nothing here is a fault of the
    store: the shares say whether the Stop check (``kyo discuss check --hook``) is turning
    late openings into live ones, and a week of skips with no live opening says its
    detector is being dismissed. Silent in a store where no decision has a discussion and
    no turn was skipped.
    """
    weeks = discussion_shares(store)
    shown = [
        week
        for week in sorted(weeks)
        if weeks[week]["skips"]
        or weeks[week][OPENED_LIVE] + weeks[week][OPENED_LATE] + weeks[week][OPENED_UNINDEXED]
    ][-DISCUSSION_SHARE_WEEKS:]
    out: list[str] = []
    for week in shown:
        row = weeks[week]
        total = row[OPENED_LIVE] + row[OPENED_LATE] + row[OPENED_UNINDEXED]
        if total:
            shares = ", ".join(
                f"{how} {row[how]} ({round(100 * row[how] / total)}%)"
                for how in (OPENED_LIVE, OPENED_LATE, OPENED_UNINDEXED)
            )
            text = f"week {week}: {total} decision(s) filed from a discussion, opened {shares}"
        else:
            text = f"week {week}: no decision filed from a discussion"
        if row["none"]:
            text += f"; {row['none']} more filed from none"
        text += f"; {row['skips']} Stop-check prompt(s) skipped (KYO-662)"
        out.append(text)
    return out


def check_sessions_unindexed(store: Store) -> list[str]:
    """KYO-662: a session that recorded Stop events while the turn index holds no line for it.

    The Stop hook that records a session in ``sessions.json`` and the hooks that write the
    turn index (``kyo turns --hook`` on UserPromptSubmit and Stop) are installed together,
    so a session with the first and none of the second ran without the index: an older
    hook set, a hook that failed every time, or a transcript the hook could not read. Every
    discussion opened in such a session is ``index absent``, and nothing else says why.

    A PROBLEM, bounded so that it can be cleared and does not fire on a race. It is silent
    in a project that has not opted in to reading transcripts, which keeps no index. It
    needs two recorded Stops, since at a session's first Stop this check can run before
    the turn hook beside it has written. It names only a session whose last Stop is later
    than every line in the index: once any session's prompt is indexed after it, the hooks
    are known to work again, and the earlier session's missing lines are counted by the
    ``unindexed`` share of :func:`check_discussion_shares` rather than reported forever.
    """
    from . import cmd_sessions, cmd_turns
    from .config import transcripts_opted_in

    if not transcripts_opted_in(store.config):
        return []
    root = store.config.kyosei_dir.parent
    lines = cmd_turns.read_lines(root)
    indexed = {line.get("session") for line in lines}
    newest = max((line["at"] for line in lines if isinstance(line.get("at"), str)), default="")
    missing: list[str] = []
    for entry in cmd_sessions.read_sessions(cmd_sessions.sessions_path(root)).entries:
        stops, stopped_at = entry.get("stops"), entry.get("stopped_at")
        if (
            isinstance(stops, int)
            and stops >= 2
            and isinstance(stopped_at, str)
            and stopped_at > newest
            and entry["session_id"] not in indexed
        ):
            missing.append(f"{entry['session_id']} ({stops} stops, the last at {stopped_at})")
    if not missing:
        return []
    return [
        f"session {found} is recorded in sessions.json and has no line in the turn index "
        "(kyosei/turns.jsonl), so its turns were not stamped and a discussion opened in it "
        "is `index absent`: the hooks that write the index did not run. Run `kyo "
        "install-hooks` (or enable the Kyosei plugin, `kyo harness`) and start a new "
        "session; this clears when a later prompt is indexed (KYO-662)"
        for found in missing
    ]
