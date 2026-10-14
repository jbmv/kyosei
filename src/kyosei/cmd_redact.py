"""``redact`` — removing stored prose from an append-only store.

Its own module because it is the one command that rewrites history: it reaches
into records, notes, claims, evidence and shift notes alike, rewrites the
export *and* the event ledger, and leaves a marker where the text was. That
cross-cutting reach is exactly why it should not sit inside any one subject's
file.
"""

from __future__ import annotations

from argparse import Namespace

from . import events_ledger, export, ids, ledger_repair, ledger_scrub, ledgers
from .clock import now
from .commands import (
    Context,
    Output,
    _reason,
    _touch_record,
)
from .contracts import (
    REDACTABLE,
    REDACTION_MARKER,
)
from .errors import KyoseiError, NotFound, UsageError
from .render import (
    echo_block,
    result_value,
)
from .store import Store


def _redaction_target(target_id: str) -> str:
    """Which redactable kind an id names, by its own shape (law 6: ids are typed)."""
    if ids.SHIFT_NOTE_RE.match(target_id):
        return "shift-note"
    kind = ids.child_kind(target_id)
    if kind in REDACTABLE:
        return kind
    raise UsageError(
        f"{target_id!r} does not name anything redactable. `kyo redact` takes an evidence "
        "artifact (RECORD.eN, written by `kyo attach`), a claim (RECORD.mN, by "
        "`kyo claim`), a note (RECORD.nN, by `kyo edit --note-file`) or a shift note "
        "(PREFIX-SN, by `kyo note --shift`) — the four places this store holds verbatim "
        "prose — or a record's own title, description or criterion (RECORD.title, "
        "RECORD.description, RECORD.cN; KYO-426). Ids appear in `kyo show <record>` and, "
        "for shift notes, in `kyo brief` "
        "(KYO-131: each shape names its writer, because a reader who cannot find the "
        "write path concludes the object does not exist)"
    )


def cmd_redact(ctx: Context, args: Namespace) -> Output:
    """``kyo redact <id> --why`` (KYO-22, widened by KYO-25) — remove verbatim prose
    from the store and from the git-canonical ledgers, recording that it happened.

    Evidence is stored verbatim and `kyosei/export/` is git-canonical, which is the
    whole point of a provenance tool right up until the thing you quoted is an
    identifier. The first consumer project outside this repo put consumer ids into
    git in a day — in a project whose own application encrypts those same values at
    rest and tokenises them in capture — and had no way back.

    Covers every store listed in :data:`REDACTABLE`. Three things it does, one it
    cannot:

    * The item's text becomes :data:`REDACTION_MARKER`. For evidence, ``sha256`` is
      recomputed over the marker and the original digest is **not** kept: a sha256
      of a short identifier is brute-forceable in seconds, so preserving it would
      republish the value the redaction exists to remove.
    * The ledgers are scrubbed of that item's text
      (:func:`ledger_scrub.scrub_redacted_from_ledgers`) — without which this would be
      theatre, since every mutation copies the whole record document into
      ``_events.jsonl``, and a shift note's text is written into its event directly.
    * ``redacted_at``/``redacted_why`` go on the row and a redaction event is
      appended, so the audit trail says something was removed, by whom, when and
      why. That event carries **no** ``before`` document, because a before document
      is exactly the payload being removed.

    KYO-405 closed three ways it could report done while the value was still stored:

    * **Retry.** The row commits first and the ledger writes and scrubs follow COMMIT,
      so an interruption there left a redacted row and an unscrubbed ledger — and the
      rerun was refused as "already redacted". A rerun with the SAME reason is now a
      retry: it re-appends what the ledger is missing, re-exports, and runs every scrub
      again, matching on the text recovered from the copies still left. `kyo doctor`'s
      ``redactions`` check reads the ledger and the events table and names that rerun.
    * **The events table.** The db's ``events`` snapshots are scrubbed inside the
      redaction's transaction by the ledger's rules
      (:func:`ledger_scrub.scrub_redacted_from_events_table`).
    * **Residue.** The same words quoted in a record's own title, description or
      criteria are not a redaction target; every live column still holding them is
      named (:func:`surviving_occurrences`), and the output says INCOMPLETE.

    What it cannot do: un-publish a value already in a commit. This fixes the
    working tree and everything after it; history that already carries the value
    stays until someone rewrites history, which has a real cost to every clone and
    branch and is a human's decision, not this command's.
    """
    store = ctx.store()
    reason = _reason(args)
    if not (reason or "").strip():
        raise UsageError(
            f"kyo redact {args.evidence_id} needs --why (or --why-file): a redaction with "
            "no recorded reason is indistinguishable from prose going missing"
        )
    target_id = args.evidence_id
    field = ledger_scrub.field_target(target_id)
    if field is not None:
        return _redact_field(ctx, store, target_id, *field, reason)
    kind = _redaction_target(target_id)
    table, text_column, record_column = REDACTABLE[kind]

    with store.transaction():
        row = store.conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (target_id,)  # noqa: S608 - table from REDACTABLE
        ).fetchone()
        if row is None:
            raise NotFound(
                f"no {kind} {target_id!r}; ids appear in `kyo show <record>` and, for "
                "shift notes, in `kyo brief`"
            )
        retry = bool(row["redacted_at"])
        if retry and reason.strip() != (row["redacted_why"] or "").strip():
            raise KyoseiError(
                f"{target_id} was already redacted at {row['redacted_at']} "
                f"(because: {row['redacted_why']}). A rerun completes a redaction whose "
                "scrub was interrupted, and takes the reason it was recorded with: "
                f"`kyo redact {target_id} --why-file FILE`, with that reason in FILE. A "
                "different reason would rewrite the audit trail rather than finish the "
                "job (KYO-405)"
            )
        record_id = row[record_column] if record_column else None
        if retry:
            # KYO-405: the row committed and the scrub after it did not. The item's
            # own text is gone from the row, so what the text-keyed passes match on is
            # recovered from the copies the interrupted scrub left behind.
            residue = ledger_scrub.redaction_residue(store, target_id, kind, REDACTION_MARKER)
            originals = set(residue.texts)
            if row[text_column] != REDACTION_MARKER:
                originals.add(row[text_column])
            stamp = row["redacted_at"]
        else:
            originals = {row[text_column]}
            stamp = now()
        originals.discard(REDACTION_MARKER)
        if not retry or row[text_column] != REDACTION_MARKER:
            _mark_redacted(store, kind, table, text_column, target_id, stamp, reason)

        # KYO-28: brief generation renders claim statements (and the record's
        # description and criteria) into the frozen brief, and a worker's report is
        # ingested verbatim — so the text can be embedded in rows the id-based
        # scrub never looks at. Done inside the transaction so a failure here rolls
        # the whole redaction back rather than half-applying it.
        embedded = 0
        global_pass_ran = False
        for original in sorted(originals):
            count, ran = ledger_scrub.scrub_embedded_copies(store, original, REDACTION_MARKER)
            embedded += count
            global_pass_ran = global_pass_ran or ran
        affected = sorted(
            {
                row["record_id"]
                for row in store.conn.execute(
                    "SELECT record_id FROM dispatches UNION SELECT record_id FROM reports"
                )
            }
        ) if embedded else []

        # KYO-405: the database's own event snapshots, by the ledger's rules —
        # inside the transaction, so the db never commits a redaction its own audit
        # table still contradicts.
        event_rows = ledger_scrub.scrub_redacted_from_events_table(
            store, target_id, kind, REDACTION_MARKER, tuple(sorted(originals))
        )

        after = None
        event = None
        if not retry:
            if record_id:
                _touch_record(store, record_id)
                after = export.record_document(store, record_id)
            # `before=None` on purpose: `_commit_and_export`'s usual before-document
            # would carry the very text being removed straight into the ledger.
            event = store.record_event(
                actor=ctx.actor,
                command=ledger_scrub.REDACTION_COMMAND.format(target=target_id, reason=reason),
                record_id=record_id,
                before=None,
                after=after if after is not None else {"redacted": target_id},
            )
        elif record_id:
            after = export.record_document(store, record_id)
        surviving = surviving_occurrences(store, sorted(originals))

    # KYO-404 ordering (KYO-405.c7): the append-only ledger first, then what a refresh
    # can regenerate, so a crash after COMMIT leaves no ledger gap. A retry re-appends
    # whatever the interrupted run never got into the ledger — its own redaction event
    # included — by the same repair `kyo export --refresh` runs, which scrubs every
    # re-appended event against every redacted item on the way in.
    if event is not None:
        events_ledger.append_events(store, [event])
    else:
        ledger_repair.repair_ledgers(store)
    to_export = [rid for rid in [record_id, *affected] if rid]
    if to_export:
        export.write_exports(store, to_export)
    ledgers.write_allocations(store)
    scrubbed = ledger_scrub.scrub_redacted_from_ledgers(store, target_id, kind, REDACTION_MARKER)
    for original in sorted(originals):
        if len(original) >= ledger_scrub.MIN_GLOBAL_SCRUB_LENGTH:
            scrubbed += ledger_scrub.scrub_text_from_ledgers(store, original, REDACTION_MARKER)

    return _redaction_output(
        kind=kind,
        target_id=target_id,
        record_id=record_id,
        retry=retry,
        stamp=stamp,
        reason=reason,
        surviving=surviving,
        scrubbed=scrubbed,
        event_rows=event_rows,
        embedded=embedded,
        originals=originals,
        global_pass_ran=global_pass_ran,
        after=after,
    )


def _redact_field(
    ctx: Context, store: Store, target_id: str, kind: str, record_id: str, reason: str
) -> Output:
    """``kyo redact RECORD.title | RECORD.description | RECORD.cN`` (KYO-426).

    The words a redaction removes are often quoted in the record's own fields too, and
    KYO-405 could only NAME those. This removes them. Every version of the field that any
    snapshot up to this redaction holds becomes :data:`REDACTION_MARKER` — the id-keyed
    rule, over the database's ``events`` table and the ledger alike — and each of those
    versions is also matched as whole-blob text wherever it was copied (briefs, reports,
    the ledger's other lines), under the same length guard the prose stores use. Every
    version, not only the current one: the text is as often in the value an earlier
    ``kyo edit`` replaced as in the one standing now, and "edit first, redact second" was
    this record's workaround precisely because the edit left the old value behind.

    A criterion's text is immutable from its first verdict (KYO-58); a redaction does not
    restate it, it replaces it with the marker, and the criterion keeps its id, state and
    verdicts.

    There is no ``redacted_at`` column on a record or a criterion: the redaction event
    carries the reason, and :func:`ledger_scrub.field_redactions` reads it back for
    ``kyo doctor``. A rerun under the reason already recorded, on a field that already
    holds the marker, is a retry — it records no second event and runs every scrub again
    up to the first one. Any other run records its own event, whose scope covers
    everything before it.
    """
    table, column = ledger_scrub.FIELD_REDACTABLE[kind]
    with store.transaction():
        if kind == "criterion":
            row = store.conn.execute(
                "SELECT text AS value FROM criteria WHERE id = ?", (target_id,)
            ).fetchone()
        else:
            row = store.conn.execute(
                f"SELECT {column} AS value FROM records WHERE id = ?",  # noqa: S608
                (record_id,),
            ).fetchone()
        if row is None:
            raise NotFound(f"no {kind} {target_id!r} in this store to redact")
        current = row["value"]
        previous = next(
            (item for item in ledger_scrub.field_redactions(store) if item.target_id == target_id),
            None,
        )
        retry = (
            current == REDACTION_MARKER
            and previous is not None
            and previous.why.strip() == reason.strip()
        )
        upto = previous.seq if retry and previous is not None else None
        # Every version a snapshot still holds — before any event of this run exists, so
        # `upto=None` on a first run and the recorded bound on a retry.
        residue = ledger_scrub.field_redaction_residue(
            store, target_id, kind, record_id, upto, REDACTION_MARKER
        )
        originals = set(residue.texts)
        if isinstance(current, str) and current and current != REDACTION_MARKER:
            originals.add(current)
        if not originals and current != REDACTION_MARKER:
            raise UsageError(
                f"{record_id} has no {kind} text to redact at {target_id}, now or in any "
                "earlier version this store holds"
            )
        if current != REDACTION_MARKER and current:
            store.conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE id = ?",  # noqa: S608
                (REDACTION_MARKER, target_id if kind == "criterion" else record_id),
            )

        embedded = 0
        global_pass_ran = False
        for original in sorted(originals):
            count, ran = ledger_scrub.scrub_embedded_copies(store, original, REDACTION_MARKER)
            embedded += count
            global_pass_ran = global_pass_ran or ran
        affected = sorted(
            {
                item["record_id"]
                for item in store.conn.execute(
                    "SELECT record_id FROM dispatches UNION SELECT record_id FROM reports"
                )
            }
        ) if embedded else []

        event = None
        if retry and previous is not None:
            stamp = str(
                store.conn.execute(
                    "SELECT at FROM events WHERE seq = ?", (previous.seq,)
                ).fetchone()["at"]
            )
            upto_seq = previous.seq
        else:
            _touch_record(store, record_id)
            # `before=None` for the reason the item path gives: a before document would
            # carry the very text being removed straight into the ledger.
            event = store.record_event(
                actor=ctx.actor,
                command=ledger_scrub.REDACTION_COMMAND.format(target=target_id, reason=reason),
                record_id=record_id,
                before=None,
                after=export.record_document(store, record_id),
            )
            stamp = event["at"]
            upto_seq = event["seq"]
        event_rows = ledger_scrub.scrub_field_from_events_table(
            store, target_id, kind, record_id, upto_seq, REDACTION_MARKER,
            tuple(sorted(originals)),
        )
        after = export.record_document(store, record_id)
        surviving = surviving_occurrences(store, sorted(originals))

    # The KYO-404 ordering the item path keeps: ledger first, then what a refresh can
    # regenerate. A retry repairs whatever the interrupted run never appended.
    if event is not None and not retry:
        events_ledger.append_events(store, [event])
    if retry:
        ledger_repair.repair_ledgers(store)
    export.write_exports(store, [record_id, *[rid for rid in affected if rid != record_id]])
    ledgers.write_allocations(store)
    scrubbed = ledger_scrub.scrub_field_from_ledgers(
        store, target_id, kind, record_id, upto_seq, REDACTION_MARKER
    )
    for original in sorted(originals):
        if len(original) >= ledger_scrub.MIN_GLOBAL_SCRUB_LENGTH:
            scrubbed += ledger_scrub.scrub_text_from_ledgers(store, original, REDACTION_MARKER)

    return _redaction_output(
        kind=kind,
        target_id=target_id,
        record_id=record_id,
        retry=retry,
        stamp=stamp,
        reason=reason,
        surviving=surviving,
        scrubbed=scrubbed,
        event_rows=event_rows,
        embedded=embedded,
        originals=originals,
        global_pass_ran=global_pass_ran,
        after=after,
    )


def _redaction_output(
    *,
    kind: str,
    target_id: str,
    record_id: str | None,
    retry: bool,
    stamp: str,
    reason: str,
    surviving: list[dict],
    scrubbed: int,
    event_rows: int,
    embedded: int,
    originals: set[str],
    global_pass_ran: bool,
    after: dict | None,
) -> Output:
    """What a redaction says it did — one wording for an item and a field (KYO-426)."""
    where = f" on {record_id}" if record_id else ""
    headline = (
        f"retried the redaction of {kind} {target_id}{where} (redacted at {stamp})"
        if retry
        else f"redacted {kind} {target_id}{where}"
    )
    return Output(
        [
            headline,
            *_surviving_lines(surviving),
            result_value("scrubbed from prior ledger lines", str(scrubbed)),
            result_value("scrubbed from the database's events table", str(event_rows)),
            result_value("embedded copies removed (briefs, reports)", str(embedded)),
            *echo_block("reason", reason),
            "",
            *(
                [
                    "NOTE: this is a retry, and no copy of the original text survives "
                    "anywhere to match against, so only the id-keyed scrub ran; the "
                    "passes that match the text itself had nothing to run with.",
                    "",
                ]
                if retry and not originals
                else []
                if global_pass_ran
                else [
                    f"NOTE: this text is shorter than {ledger_scrub.MIN_GLOBAL_SCRUB_LENGTH} "
                    "characters, so the pass that removes copies embedded in dispatch "
                    "briefs and worker reports was SKIPPED — replacing a short string "
                    "everywhere would corrupt unrelated text. Check `kyo show` on any "
                    "record whose brief or report may have quoted it.",
                    "",
                ]
            ),
            "This fixed the working tree, not history: any commit that already "
            "contains this text still does. Rewriting that is a separate, deliberate "
            "decision with a cost to every clone and branch.",
        ],
        {
            "ok": True,
            "command": "redact",
            "target_id": target_id,
            "target_kind": kind,
            "record_id": record_id,
            "why": reason,
            "retry": retry,
            "complete": not surviving,
            "surviving_occurrences": surviving,
            "ledger_lines_scrubbed": scrubbed,
            "event_rows_scrubbed": event_rows,
            "embedded_copies_removed": embedded,
            "global_scrub_ran": global_pass_ran,
            "record": after,
        },
    )


def _mark_redacted(
    store: Store, kind: str, table: str, text_column: str, target_id: str, stamp: str, reason: str
) -> None:
    """Put the marker (and, for evidence, the marker's digest) on the item's own row."""
    if kind == "evidence":
        store.conn.execute(
            "UPDATE evidence_artifacts SET text = ?, sha256 = ?, redacted_at = ?,"
            " redacted_why = ? WHERE id = ?",
            (
                REDACTION_MARKER,
                export._digest(REDACTION_MARKER),
                stamp,
                reason,
                target_id,
            ),
        )
    else:
        store.conn.execute(
            f"UPDATE {table} SET {text_column} = ?, redacted_at = ?, redacted_why = ?"  # noqa: S608
            " WHERE id = ?",
            (REDACTION_MARKER, stamp, reason, target_id),
        )


#: KYO-405: the live columns searched, after a redaction, for the text it removed —
#: ``(table, column, record-id column or None)``. :data:`REDACTABLE` names the stores
#: `kyo redact` rewrites; this names the places the same words can ALSO be, which it does
#: not rewrite (a record's own title, description and criteria are the common case), plus
#: the embedded and event columns it does scrub, so a copy the whole-blob pass skipped or
#: could not match is named rather than assumed gone. Only reporting — nothing here is
#: rewritten.
RESIDUE_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("records", "title", "id"),
    ("records", "description", "id"),
    ("records", "wont_do_reason", "id"),
    ("criteria", "text", "record_id"),
    ("criteria", "reason", "record_id"),
    ("notes", "text", "record_id"),
    ("claims", "statement", "record_id"),
    ("evidence_artifacts", "text", "record_id"),
    ("shift_notes", "text", None),
    ("gates", "detail", "record_id"),
    ("gates", "cleared_why", "record_id"),
    ("verifications", "reason", "record_id"),
    ("flags", "detail", "record_id"),
    ("flags", "resolved_why", "record_id"),
    ("dispatches", "brief", "record_id"),
    ("dispatches", "terminated_why", "record_id"),
    ("reports", "body_json", "record_id"),
    ("docs_obligations", "settled_why", "record_id"),
    ("events", "command", "record_id"),
    ("events", "before_json", "record_id"),
    ("events", "after_json", "record_id"),
    ("events", "changed_json", "record_id"),
)


def surviving_occurrences(store: Store, originals: list[str]) -> list[dict]:
    """Every live column that still holds one of ``originals``, as
    ``{table, column, ids, record_ids}`` — one entry per column (KYO-405).

    Matched by the same wrap- and escape-tolerant definition the scrub uses, at any
    length: a short value is too risky to REPLACE everywhere, but naming where it still
    is costs nothing, and a redaction that says nothing about a copy it left is the
    "success that means nothing happened" law 6 exists against.
    """
    found: list[dict] = []
    if not originals:
        return found
    for table, column, record_column in RESIDUE_COLUMNS:
        record_select = record_column or "NULL"
        hits: list[tuple[str, str | None]] = []
        for row in store.conn.execute(
            f"SELECT id, {record_select} AS record_id, {column} AS value"  # noqa: S608
            f" FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
        ):
            value = row["value"]
            if not isinstance(value, str) or value == REDACTION_MARKER:
                continue
            if any(ledger_scrub._scrub_every_form(value, text, "")[1] for text in originals):
                hits.append((row["id"], row["record_id"]))
        if hits:
            found.append(
                {
                    "table": table,
                    "column": column,
                    "ids": [item for item, _record in hits],
                    "record_ids": sorted({record for _item, record in hits if record}),
                }
            )
    return found


def _surviving_lines(surviving: list[dict]) -> list[str]:
    """The loud half of :func:`surviving_occurrences`: said second, above every count."""
    if not surviving:
        return []
    lines = [
        "INCOMPLETE — the redacted text is NOT gone from the store. It still appears in "
        f"{len(surviving)} live field(s) `kyo redact` does not rewrite:"
    ]
    for place in surviving:
        on = ", ".join(place["record_ids"]) or ", ".join(place["ids"])
        count = len(place["ids"])
        rows = f" ({count} rows)" if count > 1 else ""
        lines.append(f"  {place['table']}.{place['column']} on {on}{rows}")
    lines.append(
        "A record's own fields are outside what `kyo redact` rewrites, and changing one "
        "now with `kyo edit` copies its old value into that edit's event, where a "
        "redaction that has already run cannot match it."
    )
    return lines
