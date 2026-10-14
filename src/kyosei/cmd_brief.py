"""``kyo brief`` / ``kyo note`` — what happened this shift, and the note for the next one."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from argparse import Namespace

from . import (
    buildinfo,
    currency,
    doctor_install,
    events_ledger,
    install_hooks,
    install_procedures,
    install_skill,
    kinds,
    ledgers,
    prior_art,
    scaffold,
)
from . import procedures as procedures_module
from . import sweep as sweep_module
from .brief import enforcement_kind
from .clock import elapsed_days, now
from .cmd_next import _ready_records, _reported_dispatches_by_record, ungated_unobserved_records
from .commands import Context, Output, _load_registry, _load_status_ladder, _read_worker_procedures
from .contracts import (
    DISPATCH_STATE_RUNNING,
    DISPATCH_STATE_TERMINATED_NO_REPORT,
    FLAG_TYPE_TOUCH_CHANGED,
    ROLE_NEEDS_VERIFICATION,
    SETTLED_CRITERION_STATES,
    standing,
)
from .errors import KyoseiError, UsageError
from .prose import read_prose
from .render import brief_text, echo_block
from .store import Store


def _records_with_dispatch_history(store: Store) -> set[str]:
    """Every record something has actually been built against (KYO-69.c2).

    Derived, never stored (law 3): a record is "worked" if a dispatch names it, if a
    dispatch formally covers it (``covers_json`` — KYO-13.c4), or if a report was filed
    against it. That is the exact set for which "your declared touches changed" means
    *the ground moved under work that was done*, rather than *the codebase is being
    worked on*, which is a codebase's normal state.
    """
    worked: set[str] = set()
    for row in store.conn.execute("SELECT record_id, covers_json FROM dispatches"):
        worked.add(row["record_id"])
        if row["covers_json"]:
            try:
                covered = json.loads(row["covers_json"])
            except (TypeError, ValueError):  # pragma: no cover - defensive
                covered = []
            if isinstance(covered, list):
                worked.update(str(rid) for rid in covered)
    for row in store.conn.execute("SELECT record_id FROM reports"):
        worked.add(row["record_id"])
    return worked


def _awaiting_verification(store: Store, registry: kinds.KindRegistry) -> list[dict]:
    """Records in a ``needs-verification`` status — code written, nothing checked yet —
    with what each one needs to move on (KYO-18).

    KYO-101: keyed on the *role*, so a project that calls this rung ``in-review`` gets
    the same queue. This is the role's consumer, and the reason ``needs-verification``
    and ``verified`` are two roles rather than one: a record somebody has already
    verified must not reappear in the queue of things waiting to be verified.

    That rung is "finished but not verified live" (the default ladder calls it
    ``built``, ``src/kyosei/defaults/statuses.yml``) — a state that is in no other
    section: not ready work, not gated, not done.

    "What it needs" is derived, not guessed: the criteria not yet in a settled state
    are what stands between the record and ``done``, so they are what a verifier
    should look at. A record whose criteria are all settled needs only the move.

    Keyed on the role, not widened by KYO-87's class: ``built`` is a state a
    controller *declared* and ``_Readiness.settled_not_moved`` one the store
    *derives*, so a record in both appears in both, with the same remedy, on purpose
    (docs/design/review.md#kyo-87).
    """
    awaiting = _load_status_ladder(store).with_role(ROLE_NEEDS_VERIFICATION)
    out: list[dict] = []
    if not awaiting:
        return out
    for row in store.conn.execute(
        "SELECT id, kind, status, title FROM records"
        f" WHERE status IN ({', '.join('?' * len(awaiting))}) ORDER BY seq",
        awaiting,
    ):
        kind_def = registry.get(row["kind"])
        if kind_def is None or kind_def.archetype != "work":
            continue
        unsettled = [
            {"id": c["id"], "state": c["state"], "text": c["text"]}
            for c in store.criteria(row["id"])
            if c["state"] not in SETTLED_CRITERION_STATES
        ]
        out.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "status": row["status"],
                "title": row["title"],
                "unsettled_criteria": unsettled,
            }
        )
    return out


def _counts_by_kind(store: Store, registry: kinds.KindRegistry) -> list[dict]:
    counts: dict[tuple[str, str], int] = {}
    ladder = _load_status_ladder(store)
    for row in store.conn.execute("SELECT kind, status FROM records"):
        kind_def = registry.get(row["kind"])
        # KYO-44.c2: counted by standing, not raw status, so a knowledge record is
        # never a permanent `open` (docs/design/review.md#kyo-44).
        word = (
            standing(row["status"], kind_def.archetype, ladder) if kind_def else row["status"]
        )
        key = (row["kind"], word)
        counts[key] = counts.get(key, 0) + 1
    by_kind: dict[str, dict] = {}
    for (kind, status), n in counts.items():
        kind_def = registry.get(kind)
        entry = by_kind.setdefault(
            kind,
            {
                "kind": kind,
                "counted": kind_def.counted if kind_def is not None else False,
                "by_status": {},
                "total": 0,
            },
        )
        entry["by_status"][status] = n
        entry["total"] += n
    return [by_kind[k] for k in sorted(by_kind)]


def _asset_drift(store: Store) -> list[dict]:
    """Every packaged asset (skill, hooks, procedures ledger) whose copy in this
    project differs from this build's own (KYO-213): `kyo doctor`'s finding, at session
    start, where it can still change what the session follows
    (docs/design/review.md#kyo-213).

    Calls the same comparisons `kyo doctor` calls — :func:`install_skill.skill_drift`,
    :func:`install_hooks.hook_drift`, :func:`install_procedures.procedures_ledger_drift`
    — rather than re-deriving any of them, so brief and doctor cannot disagree about whether a copy
    has drifted (KYO-213.c2). A rule the store copy has reworded on purpose carries no
    remedy here: nothing rewrites it, and `kyo doctor` is where both wordings print
    side by side.

    KYO-309: the remedy field is decided by the same question the checks ask — whether
    this checkout is the one the package is built from. There the packaged copy is this
    repository's OUTPUT, so `kyo install-skill` / `kyo install-procedures` would write
    the older text over the newer source, and a brief naming either would hand the
    controller the destructive command at session start. The details still come from
    the same comparisons `kyo doctor` runs; only the remedy word is decided here.
    """
    source_repo = (
        scaffold.repo_asset_source(store.config.repo_root, install_skill.SKILL_ASSET) is not None
    )
    findings: list[dict] = []
    # KYO-608: a project on the plugin carries no copies, so naming `kyo install-skill` or
    # `kyo install-hooks` there would put back the copies and double every hook. Decided by
    # the function `kyo doctor` gates the same checks on.
    on_plugin = doctor_install.on_the_plugin(store)
    for detail in [] if on_plugin else install_skill.skill_drift(store.config.repo_root):
        findings.append(
            {
                "asset": "skill",
                "remedy": None if source_repo else "kyo install-skill",
                "detail": detail,
            }
        )
    for detail in install_skill.installed_build_lag(store.config.repo_root):
        findings.append({"asset": "build", "remedy": None, "detail": detail})
    for detail in [] if on_plugin else install_hooks.hook_drift(store.config.repo_root):
        findings.append({"asset": "hooks", "remedy": "kyo install-hooks", "detail": detail})
    differences, errors = install_procedures.procedures_ledger_drift(
        store.config.root, store.config.repo_root
    )
    for detail in errors:
        findings.append({"asset": "procedures", "remedy": None, "detail": detail})
    store_bodies = (
        procedures_module.rule_bodies(
            (store.config.root / procedures_module.STORE_LEDGER_PATH).read_text(
                encoding="utf-8"
            )
        )
        if differences and not source_repo
        else {}
    )
    for difference in differences:
        if difference.kind == procedures_module.LEDGER_MISSING:
            findings.append(
                {
                    "asset": "procedures",
                    "remedy": None if source_repo else "kyo install-procedures",
                    "detail": (
                        f"kyosei/procedures.md is missing the rule {difference.title!r}, "
                        + (
                            "which this repository's own "
                            f"{procedures_module.PACKAGED_LEDGER_SOURCE_PATH} ships — "
                            "copy it across; `kyo install-procedures` reads the "
                            "installed build's copy, which this checkout is the source of"
                            if source_repo
                            else "which this build packages — run `kyo install-procedures`"
                        )
                    ),
                }
            )
        elif (
            difference.kind == procedures_module.LEDGER_CHANGED
            and not source_repo
            and procedures_module.is_superseded_wording(
                difference.title, store_bodies.get(difference.title, "")
            )
        ):
            # KYO-518: the same test `kyo doctor`'s procedures_rules_differ applies
            # (KYO-515) -- nobody in the project wrote this text, so it is inherited,
            # and the plain rerun moves it.
            findings.append(
                {
                    "asset": "procedures",
                    "remedy": "kyo install-procedures",
                    "detail": (
                        f"the rule {difference.title!r} is still on a wording an earlier "
                        "build installed, unedited since -- not reworded by this project "
                        "-- run `kyo install-procedures`, which moves it to this build's "
                        "and leaves every rule you reworded alone"
                    ),
                }
            )
        elif difference.kind == procedures_module.LEDGER_CHANGED:
            findings.append(
                {
                    "asset": "procedures",
                    "remedy": None,
                    "detail": (
                        f"the rule {difference.title!r} is worded differently from "
                        + (
                            "this repository's own "
                            f"{procedures_module.PACKAGED_LEDGER_SOURCE_PATH}"
                            if source_repo
                            else "this build's packaged default"
                        )
                        + " — `kyo doctor` prints both wordings"
                    ),
                }
            )
    return findings


def _gated_records(store: Store, *, at: str) -> list[dict]:
    """Every open gate on the board, with the record it holds.

    KYO-496 adds ``opened_at`` (the gate's ``created_at``) and ``age_days``, whole days
    from it to ``at`` — the brief's own stamp — by :func:`clock.elapsed_days`. A wait's
    age was stored and never printed; it is derived here at read time and never stored.

    KYO-102 adds ``tags``: the brief is the surface a controller opens a session on,
    so the classification that makes gates groupable has to reach it. Decoded here
    rather than handed on as JSON text, because everything downstream of this function
    is a renderer and none of them should be parsing a column.
    """
    rows = store.conn.execute(
        "SELECT g.id AS gate_id, g.record_id AS record_id, g.gate_type AS gate_type,"
        " g.detail AS detail, g.tags_json AS tags_json, g.created_at AS opened_at,"
        " r.title AS title, r.status AS status"
        " FROM gates g JOIN records r ON r.id = g.record_id"
        " WHERE g.cleared_at IS NULL ORDER BY g.record_id, g.seq"
    )
    gated = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json")) if item["tags_json"] else []
        item["age_days"] = elapsed_days(item["opened_at"], at)
        gated.append(item)
    return gated


def _standing_obligations(store: Store, *, at: str) -> list[dict]:
    """KYO-554.c7: every open standing docs obligation, oldest first, with its age.

    A standing obligation was raised by a record when it was filed or edited (its kind
    sets ``policy.raises_docs_obligations``), so no dispatch's report will ever list it;
    the brief is where it is seen until somebody settles it. ``age_days`` is whole days
    from the obligation's ``created_at`` to ``at``, the brief's own stamp, by
    :func:`clock.elapsed_days`, as the gated list's ages are.
    """
    return [
        {
            "id": row["id"],
            "record_id": row["record_id"],
            "record_title": row["record_title"],
            "doc_path": row["doc_path"],
            "section": row["section"],
            "cause": row["cause"],
            "created_at": row["created_at"],
            "age_days": elapsed_days(row["created_at"], at),
        }
        for row in store.open_standing_obligations()
    ]


def _carried_records(
    store: Store, registry: kinds.KindRegistry, ladder, *, at: str
) -> dict:
    """KYO-497 (from KYO-483): every unsettled record of a ``policy.carried`` kind.

    A carried item is a standing decision, an open question or a reminder the next shift
    must see; this is the list a closing note used to re-type by hand, with day counts
    that drifted. "Unsettled" is :meth:`StatusLadder.is_settled` — neither the complete
    nor the retired role, the same test `kyo list --unsettled` applies. Oldest first,
    the order in which an item most needs a decision. ``age_days`` is whole days from
    the item's basis to ``at`` (the brief's own stamp), by
    :func:`clock.elapsed_days` — the same helper the gated list's ages use (KYO-496), and
    derived at read time, never stored. Each item's open gates carry their own age: a
    carried record with an open gate is a wait, and stays listed under "gated work" too.

    KYO-498: the basis is the tool-stamped ``created_at`` of the shift note named in
    ``records.since_note`` when one is set -- the note that first recorded an item the
    project carried before it filed the record -- and the record's own ``created_at``
    otherwise. Only the note id is stored, so ``since`` is always read from the note.
    A ``since_note`` naming a note this store does not hold (a hand-edited export) falls
    back to ``created_at`` rather than inventing an age; ``kyo new``/``kyo edit`` refuse it.

    ``kinds`` names the registry's carried kinds, so a registry with none is said rather
    than shown as an empty list it could not have filled.
    """
    carried_kinds = [name for name in registry.names if registry[name].carried]
    items: list[dict] = []
    if carried_kinds:
        marks = ", ".join("?" for _ in carried_kinds)
        rows = store.conn.execute(
            "SELECT r.id AS id, r.kind AS kind, r.title AS title, r.status AS status,"
            " r.since_note AS since_note, COALESCE(n.created_at, r.created_at) AS since"
            " FROM records r LEFT JOIN shift_notes n ON n.id = r.since_note"
            f" WHERE r.kind IN ({marks}) ORDER BY since, r.seq",
            carried_kinds,
        )
        for row in rows:
            if ladder.is_settled(row["status"]):
                continue
            gates = [
                {
                    "gate_id": g["id"],
                    "gate_type": g["gate_type"],
                    "opened_at": g["created_at"],
                    "age_days": elapsed_days(g["created_at"], at),
                }
                for g in store.gates(row["id"])
                if not g["cleared_at"]
            ]
            items.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "status": row["status"],
                    "since": row["since"],
                    "since_note": row["since_note"],
                    "age_days": elapsed_days(row["since"], at),
                    "open_gates": gates,
                }
            )
    return {"kinds": carried_kinds, "items": items}


def _open_dispatches(store: Store) -> tuple[list[dict], list[dict], int]:
    """Running dispatches, and every terminated-no-report dispatch that is still
    UNANSWERED (KYO-8.c6, KYO-314.c1).

    Unlike a gate or a flag, `terminated-no-report` has no separate acknowledge/clear
    action in this build — `kyo dispatch close --terminated-no-report --why` is the
    ONLY path into the state, and it always sets `closed_at` in that same call
    (KYO-7.c5), so filtering on `closed_at` would hide every one of them. Law 8 says
    terminated is its own loud state — but one whose record is already answered no
    longer needs a controller, and keeping it loud buries the ones that do
    (docs/design/review.md#kyo-314).

    So: a terminated-no-report dispatch drops off this list once its record is
    ANSWERED, which is exactly one of two facts the store already carries —

    * the record has reached a settled status (`StatusLadder.is_settled`: the
      `complete` or `retired` role) — somebody finished it, however; or
    * a LATER dispatch speaks for the record and has reached `reported`. "Speaks
      for" is the same relation :func:`_reported_dispatches_by_record` already
      derives for the "finished but not moved" class — the record's own primary
      dispatch, or one that named it with `--also-covers` — reused rather than
      re-derived here. "Later" is checked by the table's own `rowid`, not
      `dispatches.seq`: `seq` is scoped per RECORD (it is what numbers
      `.d1`/`.d2`), so two dispatches on two different records both start at 1
      and are not comparable across the covers relation — `rowid` is SQLite's
      own insertion order, shared by every row in the table regardless of which
      record it belongs to, and unlike `created_at` it cannot tie two writes
      made in the same millisecond. The check exists so a report that predates
      this termination (the record was reported once, then re-dispatched, and
      THAT dispatch is the one that crashed) does not silently answer it.

    Neither test asks whether THIS dispatch reported — it never did, by
    definition — and neither moves anything or writes a new stored field: the
    answer is derived fresh on every read, the same way `settled_not_moved` is.

    A terminated dispatch on a still-open record with no later report is
    unaffected and stays listed exactly as before this function changed; a
    controller who wants an answered one anyway still has `kyo show
    <record-id>`, which prints a record's whole dispatch history unfiltered.

    KYO-345.c2: ``withdrawn`` appears in neither returned list and in no count. It
    is not "answered" — nothing answered it, because nothing ever asked — it is a
    dispatch a controller deliberately ended before any worker saw it, and the brief
    is the surface for what still needs a decision. The query below names the two
    states it wants rather than excluding the ones it does not, so a withdrawal is
    out by construction rather than by a filter someone has to remember to widen.

    Returns ``(running, terminated_no_report, answered_count)`` — the third value
    is how many terminated-no-report dispatches this call found and did NOT
    return, because their record already answered them.
    """
    running: list[dict] = []
    terminated_rows: list[tuple[int, dict]] = []
    for row in store.conn.execute(
        "SELECT rowid AS rowid, id, record_id, state, terminated_why, worked_from_sha,"
        " created_at FROM dispatches WHERE state = ? OR state = ? ORDER BY rowid",
        (DISPATCH_STATE_RUNNING, DISPATCH_STATE_TERMINATED_NO_REPORT),
    ):
        item = dict(row)
        row_id = item.pop("rowid")
        if row["state"] == DISPATCH_STATE_RUNNING:
            running.append(item)
        else:
            terminated_rows.append((row_id, item))

    if not terminated_rows:
        return running, [], 0

    ladder = _load_status_ladder(store)
    reported_by = _reported_dispatches_by_record(store)
    # Only the dispatch ids `_reported_dispatches_by_record` already named as
    # speaking for some record need a `rowid` — one small lookup by id, not a
    # second derivation of "speaks for" (that relation is reused whole, above).
    reporting_ids = {dispatch_id for ids in reported_by.values() for dispatch_id in ids}
    report_rowid: dict[str, int] = {}
    if reporting_ids:
        placeholders = ", ".join("?" * len(reporting_ids))
        report_rowid = {
            r["id"]: r["rowid"]
            for r in store.conn.execute(
                f"SELECT id, rowid AS rowid FROM dispatches WHERE id IN ({placeholders})",
                tuple(reporting_ids),
            )
        }
    record_status = {
        r["id"]: r["status"] for r in store.conn.execute("SELECT id, status FROM records")
    }

    terminated_no_report: list[dict] = []
    answered = 0
    for row_id, item in terminated_rows:
        record_id = item["record_id"]
        status = record_status.get(record_id)
        settled = status is not None and ladder.is_settled(status)
        later_report = any(
            report_rowid.get(dispatch_id, -1) > row_id
            for dispatch_id in reported_by.get(record_id, [])
        )
        if settled or later_report:
            answered += 1
        else:
            terminated_no_report.append(item)
    return running, terminated_no_report, answered


def _staleness_coverage(store: Store) -> dict:
    """KYO-63: what the most recent `kyo sweep` was able to answer about staleness.

    A brief that lists unresolved flags and nothing else cannot distinguish "nothing is
    stale" from "the check could not run" — and under the split layout the second is a
    restore away, because the base sha the store recorded belongs to a repository the
    store does not contain. ``never-swept`` is the same hole one step earlier: a store
    nobody has ever swept has exactly the same empty flag list.
    """
    latest = store.latest_sweep_run()
    if latest is None:
        return {
            "coverage": sweep_module.SWEEP_COVERAGE_NEVER_SWEPT,
            "detail": sweep_module.NEVER_SWEPT_DETAIL,
            "sweep_run_id": None,
            "at": None,
            "base_sha": None,
        }
    return {
        "coverage": latest["coverage"],
        "detail": latest["coverage_detail"],
        "sweep_run_id": latest["id"],
        "at": latest["at"],
        "base_sha": latest["base_sha"],
    }


def _ranked_unresolved_flags(store: Store) -> list[dict]:
    """The brief's flag list, each ``touch-changed`` entry carrying the scope it belongs
    to, ordered worked-record staleness first (KYO-69.c2).

    *Rank, do not suppress*: `kyo sweep` still flags a record that has never been
    dispatched (an unflagged window is gone, not deferred), and the brief separates
    "the ground moved under work that was actually done" from "files this record names
    have been changing while it sat open" (docs/design/review.md#kyo-69).

    ``touch_changed_scope`` is set only on ``touch-changed`` rows; it is ``None`` for
    every other flag class, which is what keeps those classes in their existing position
    rather than sorted into a bucket that does not describe them. KYO-222 adds
    ``touch_changed_range`` beside it on the same terms — the `from..to` the flag's own
    detail already carries, so the brief's text and a `--json` consumer group on one
    value.
    """
    worked = _records_with_dispatch_history(store)
    flags = []
    for row in store.unresolved_flags():
        flag = dict(row)
        flag["touch_changed_scope"] = (
            sweep_module.touch_changed_scope(has_dispatch_history=flag["record_id"] in worked)
            if flag["flag_type"] == FLAG_TYPE_TOUCH_CHANGED
            else None
        )
        # KYO-222.c2: the grouping key, on the flag, so a consumer of `kyo brief --json`
        # regroups from the same value the text groups by rather than re-parsing the
        # detail string and arriving at its own. `None` for a flag with no range.
        flag["touch_changed_range"] = sweep_module.touch_changed_range(flag)
        flags.append(flag)
    return sweep_module.rank_unresolved_flags(flags)


#: KYO-94: how the handoff note below was chosen — a value, not a sentence, because the
#: renderer and ``--json`` both have to say which definition of "this session" produced it.
HANDOFF_BASIS_SESSION = "session-start"
#: No brief run has ever declared itself a session start (nothing has run
#: ``kyo brief --session-start`` — typically the ``SessionStart`` hook is not installed, or
#: the store was rebuilt from an export written before ``brief_runs.session_start`` existed).
#: The guarantee still holds in the weaker form "the most recent shift note, whatever the
#: watermark says", and the brief prints which one it gave you rather than letting the two
#: read alike.
HANDOFF_BASIS_NO_SESSION = "no-session-recorded"


def _handoff_note(store, *, opening_a_session: bool, max_shift_seq: int) -> dict:
    """The shift note this brief must surface whatever the watermark says (KYO-94.c4).

    The shift-note window is a delta since the previous brief (law 15), so on its own a
    note appears in exactly one brief. What is owed beside it is one guarantee: the last
    note of a previous session always arrives. That needs a definition of "session" the
    tool can compute, and a brief run is not one (docs/design/review.md#kyo-94).

    So: **"before this session began" means "at or below the shift-log watermark of the
    most recent brief run that declared itself a session start"** — ``session_start = 1``,
    written only by ``kyo brief --session-start``, which the packaged ``SessionStart`` hook
    runs. A brief that is itself opening a session is its own anchor, and its watermark is
    the log as it stands right now, so it hands back the last note anyone wrote before it.

    Two properties follow, and they are the two the criterion asks for. The answer does not
    move as more briefs run inside a session, because the anchor is a specific row, not
    "the previous brief". And it needs no id from the reader, because the tool picks the
    note. (`kyo show PREFIX-SN` is the other half — the durable path for a note whose id
    you *do* have.)

    A redacted note is surfaced as the redaction — id, author, time and reason — and never
    as prose. `kyo redact` removed the text; a guarantee that quietly walked back to the
    note *before* it would republish, under the banner of "your predecessor's closing
    judgment", something nobody chose to hand over.
    """
    if opening_a_session:
        anchor, watermark, basis = None, max_shift_seq, HANDOFF_BASIS_SESSION
    else:
        anchor = store.latest_session_brief_run()
        if anchor is None:
            watermark, basis = max_shift_seq, HANDOFF_BASIS_NO_SESSION
        else:
            watermark, basis = int(anchor["last_shift_note_seq"]), HANDOFF_BASIS_SESSION
    note = store.latest_shift_note_upto(watermark)
    return {
        "basis": basis,
        "opens_this_session": opening_a_session,
        # Filled in by `cmd_brief` once this run has an id, when the anchor is this run.
        "session_brief_id": None if anchor is None else anchor["id"],
        "session_started_at": None if anchor is None else anchor["at"],
        "note": None if note is None else dict(note),
    }


# ---------------------------------------------------------------------- KYO-398
#
# THE TOOL CHANGES UNDER A PROJECT, AND NOTHING IN THE PROJECT DID IT: every project on
# a machine runs one installed `kyo` (KYO-335). Each brief run records its build; a
# session-start brief compares its own with the one the project's PREVIOUS session-start
# brief recorded and, when they differ, says so before anything else. See
# docs/design/review.md#kyo-398.

#: The four answers, as ``--json`` carries them.
BUILD_CHANGE_CHANGED = "changed"
BUILD_CHANGE_UNCHANGED = "unchanged"
BUILD_CHANGE_CANNOT_TELL = "cannot-tell"
#: No earlier session-start brief exists: a first session has nothing to compare with,
#: and saying "cannot tell" there would be a notice on every new project's first brief
#: that no reader could act on.
BUILD_CHANGE_FIRST = "first-session"

#: How many records the notice lists before it points at the command that lists the
#: rest. A session-start brief is injected whole into a session's context, and an
#: upgrade after a long gap can carry a hundred records.
BUILD_CHANGE_LIST_LIMIT = 25


def _build_side(commit: str | None, commit_time: str | None, source: str | None) -> dict:
    return {
        "commit": commit,
        "commit_short": commit[: buildinfo.SHORT_SHA] if commit else None,
        "commit_time": commit_time,
        "source": source,
    }


def _build_change(store: Store, build: dict, *, opening_a_session: bool) -> dict | None:
    """Did the build under this project change since its last session-start brief?

    ``None`` for a brief that is not opening a session — mid-session briefs are not
    where a session learns this, and the per-prompt hook (``kyo check-build --hook``,
    KYO-398.c6) covers an upgrade mid-session. Called BEFORE this run's own row is
    inserted, so :meth:`Store.latest_session_brief_run` is the previous session's anchor.

    **A working tree is told, in one line.** A copy running out of a checkout reads
    live HEAD, so its build moves with every commit — true, and in Kyosei's own
    repository nearly every session. The notice still fires (it is true, and
    "the code under you moved" is exactly what the reader should know) but names the
    two commits and ``git log`` instead of a record list: the commits are the reader's
    own, and the checkout's history is the better account of them. A stamped build
    installed as a tool — every consumer — gets the full list.

    **Records come from the NEW build's changelog** — the one this brief is running —
    filtered by the two builds' UTC commit times (:func:`kyosei.changelog.between`). An
    entry with no full instant (packaged data written before ``completed_at``) is
    placed by its date, inclusively, and the notice says the list may then name a
    record the old build already had.
    """
    if not opening_a_session:
        return None
    from . import changelog as changelog_module

    previous = store.latest_session_brief_run()
    current = _build_side(build.get("commit"), build.get("commit_time"), build.get("source"))
    data: dict = {
        "state": BUILD_CHANGE_FIRST,
        "previous_brief_id": None if previous is None else previous["id"],
        "previous_brief_at": None if previous is None else previous["at"],
        "previous": None,
        "current": current,
        "reason": None,
        "records": [],
        "records_total": 0,
        "records_exact": True,
        "records_source": None,
        "older": False,
        "since_command": None,
        # KYO-556.c6: filled only when the build changed -- every kind-registry notice
        # `kyo doctor` would print, so an upgrade says what it asks of this store.
        "registry_notices": [],
    }
    if previous is None:
        return data
    before = _build_side(
        previous["build_commit"], previous["build_commit_time"], previous["build_source"]
    )
    data["previous"] = before

    if previous["build_source"] is None:
        data["state"] = BUILD_CHANGE_CANNOT_TELL
        data["reason"] = (
            "that brief was written before briefs recorded their build (schema_version "
            "24), so there is nothing to compare this one with"
        )
        return data
    if not before["commit"]:
        data["state"] = BUILD_CHANGE_CANNOT_TELL
        data["reason"] = "the build that wrote that brief could not say which snapshot it was"
        return data
    if not current["commit"]:
        data["state"] = BUILD_CHANGE_CANNOT_TELL
        data["reason"] = (
            "the build running now cannot say which snapshot it is (`kyo version` says why "
            "and what fixes it)"
        )
        return data
    if before["commit"] == current["commit"]:
        data["state"] = BUILD_CHANGE_UNCHANGED
        return data

    data["state"] = BUILD_CHANGE_CHANGED
    from .doctor_registry import kind_registry_notices

    data["registry_notices"] = kind_registry_notices(store)
    old_time, new_time = before["commit_time"], current["commit_time"]
    if old_time:
        data["since_command"] = f"kyo changelog --since {old_time}"
    if current["source"] == buildinfo.SOURCE_GIT or not (old_time and new_time):
        # A working tree (see above), or a side with no instant to filter by — the
        # renderer names both builds and says which of the two it is.
        return data
    if old_time > new_time:
        data["older"] = True
        return data
    loaded = changelog_module.load()
    found = changelog_module.between(loaded.entries, old_time, new_time)
    data["records_source"] = loaded.source
    data["records_total"] = len(found.entries)
    data["records_exact"] = found.exact
    data["records"] = [e.as_data() for e in found.entries[:BUILD_CHANGE_LIST_LIMIT]]
    return data


def _link_form_example(store: Store) -> str:
    """KYO-356.c2: one worked example of the `kyosei://` link form, built from THIS
    store's own prefix, so the form a controller copies carries the right project
    token rather than the `KYO-263` in the skill's own prose (KYO-263),
    which is a worked example for a different project and is read once, at
    invocation, while this one is read every session `kyo brief` runs.

    Prefers a real record id -- the most recently allocated one, off the same
    ordering `Store.latest_*` already uses elsewhere -- so the example is not
    just the right shape but an id this project's own controller could actually
    click. A store with no records yet still has to print the FORM: `<n>` is the
    placeholder, unmistakable for a real id (`ids.RECORD_RE` requires digits, and
    the angle brackets match the placeholder convention this file already uses --
    see `UNGATED_UNOBSERVED_DO_PLACEHOLDER`).
    """
    row = store.conn.execute("SELECT id FROM records ORDER BY seq DESC LIMIT 1").fetchone()
    record_id = row["id"] if row is not None else f"{store.prefix}-<n>"
    return f"[{record_id}](kyosei://{store.prefix}/{record_id})"


#: KYO-561: the setup decision that asks whether the maintainer reads the orchestrator
#: through the desktop app, the one surface where a `kyosei://` link is clickable.
RECORD_LINKS_DECISION = "E4"


def record_links_declined(store: Store) -> bool:
    """KYO-561: whether ``onboarding:`` in ``kyosei/config.yml`` records setup decision
    [E4] as ``declined`` -- the maintainer does not read the orchestrator through the
    desktop app, so a ``kyosei://`` link is noise around an id there.

    Only ``declined`` drops the links: ``deferred`` and no answer at all leave the brief
    exactly as it was (KYO-561.c3), because a link the maintainer cannot click costs a
    few characters and a bare id the maintainer could have clicked costs a lookup. A map
    this build cannot read is treated as no answer and is not raised here: the brief is a
    session-start hook, and `kyo onboard --check` is the reader that names the fault
    (``parse_onboarding``'s own terms, KYO-560).
    """
    from .config import ONBOARDING_KEY, config_path_for, parse_onboarding, read_raw_config

    root = store.config.root
    try:
        answers = parse_onboarding(read_raw_config(root).get(ONBOARDING_KEY), config_path_for(root))
    except KyoseiError:
        return False
    answer = answers.get(RECORD_LINKS_DECISION)
    return answer is not None and answer.status == "declined"


def _is_citation_rule(title: str) -> bool:
    """KYO-561: the orchestrator rule that tells a session to cite records as
    ``kyosei://`` links -- the instruction [E4] declined withdraws, alongside its worked
    example. Matched on the link scheme in its title, which is what the rule is about."""
    return "kyosei://" in title


#: How long a session-start brief waits for a hook payload on a pipe that stays open (KYO-442).
_HOOK_STDIN_WAIT_SECONDS = 1.0


def _hook_session_id() -> str | None:
    """The ``session_id`` of the hook payload on stdin, or ``None`` (KYO-418).

    Read only by a session-start brief, which the packaged ``SessionStart`` hook runs
    with Claude Code's payload on stdin. A terminal is never read — a hand-run
    ``kyo brief --session-start`` must not wait on the keyboard — and anything that
    is not a JSON object naming a session is ``None``: a brief is never refused over
    what its hook did or did not send.
    """
    import os
    import select
    import sys
    import time

    try:
        if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
            return None
    except (OSError, ValueError, AttributeError):
        return None
    try:
        fd = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        # An in-memory stream (a test's StringIO) has no descriptor and cannot block.
        try:
            return _session_id_of(sys.stdin.read())
        except (OSError, ValueError):
            return None
    # Read only while bytes keep arriving, never to an EOF that may not come (KYO-442): a
    # caller that spawns this with a pipe it never closes would otherwise hang the brief.
    # The hook writes its whole payload at once, so it is complete long before the deadline.
    raw, payload = b"", None
    deadline = time.monotonic() + _HOOK_STDIN_WAIT_SECONDS
    try:
        while (left := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
            try:
                payload = json.loads(raw)
                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    except (OSError, ValueError):
        return None
    return _session_id_from(payload)


def _session_id_of(raw: str) -> str | None:
    try:
        return _session_id_from(json.loads(raw) if raw.strip() else None)
    except json.JSONDecodeError:
        return None


def _session_id_from(payload: object) -> str | None:
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    return session_id if isinstance(session_id, str) and session_id else None


#: KYO-531.c2: the key in a brief event's ``after`` that carries each orchestrator
#: rule's digest, title -> :func:`_rule_digest`.
RULE_DIGESTS_KEY = "orchestrator_rule_digests"
#: KYO-548.c2: the key in a brief event's ``after`` that carries rule TEXT, digest ->
#: normalized body -- only for a digest no earlier brief event has recorded text for,
#: so the ledger pays for a rule's text once per wording rather than once per brief.
RULE_TEXTS_KEY = "orchestrator_rule_texts"
RULE_CHANGED = "changed"
RULE_NEW = "new"


def _rule_digest(normalized_body: str) -> str:
    return hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()[:12]


def _recorded_rule_texts(store: Store) -> dict[str, str]:
    """Every rule text a brief event has recorded, digest -> normalized body
    (KYO-548.c2). Read from the events ledger, which is git-canonical, so it survives
    `kyo rebuild` exactly as the digests do."""
    texts: dict[str, str] = {}
    for row in store.conn.execute(
        "SELECT after_json FROM events WHERE command = 'brief' AND after_json LIKE ?",
        (f'%"{RULE_TEXTS_KEY}"%',),
    ):
        try:
            after = json.loads(row["after_json"] or "null")
        except ValueError:
            continue
        recorded = after.get(RULE_TEXTS_KEY) if isinstance(after, dict) else None
        if isinstance(recorded, dict):
            texts.update(
                (digest, text)
                for digest, text in recorded.items()
                if isinstance(digest, str) and isinstance(text, str)
            )
    return texts


_TOKEN_RE = re.compile(r"\w+|\S")
#: How many distinct substitutions, and how many characters of one edited span, a
#: one-line summary names before it says how many more there are.
_SUMMARY_MAX_PAIRS = 4
_SUMMARY_SPAN_CHARS = 60


def _span(text: str, tokens: list[re.Match], lo: int, hi: int) -> str:
    """Tokens ``lo..hi`` of ``text`` as the text wrote them, quoted and trimmed."""
    if lo >= hi:
        return '""'
    cut = " ".join(text[tokens[lo].start() : tokens[hi - 1].end()].split())
    if len(cut) > _SUMMARY_SPAN_CHARS:
        cut = cut[: _SUMMARY_SPAN_CHARS - 3].rstrip() + "..."
    return f'"{cut}"'


def rule_change_summary(previous: str, current: str) -> str:
    """One line saying what changed between two normalized rule bodies (KYO-548.c1).

    When every difference is a like-for-like word substitution -- the shape of an
    upstream rename, which touches most rules and changes what none of them asks --
    it names the substitutions and how often each occurred:
    ``controller -> orchestrator (x3)``. Otherwise it names the FIRST edit, trimmed
    (``added "..."``, ``removed "..."`` or ``"..." -> "..."``), and how many more
    edits there are: where a rule's wording moved is what tells a returning reader
    whether to read it, and a word count alone would not."""
    old_tokens = list(_TOKEN_RE.finditer(previous))
    new_tokens = list(_TOKEN_RE.finditer(current))
    matcher = difflib.SequenceMatcher(
        a=[t.group() for t in old_tokens], b=[t.group() for t in new_tokens], autojunk=False
    )
    edits = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not edits:
        return "wording changed (whitespace or layout only)"
    if all(tag == "replace" and i2 - i1 == j2 - j1 for tag, i1, i2, j1, j2 in edits):
        pairs: dict[tuple[str, str], int] = {}
        for _tag, i1, i2, j1, j2 in edits:
            for old, new in zip(old_tokens[i1:i2], new_tokens[j1:j2], strict=True):
                if old.group() != new.group():
                    key = (old.group(), new.group())
                    pairs[key] = pairs.get(key, 0) + 1
        named = [
            f"{old} -> {new}" + (f" (x{count})" if count > 1 else "")
            for (old, new), count in list(pairs.items())[:_SUMMARY_MAX_PAIRS]
        ]
        more = len(pairs) - len(named)
        return ", ".join(named) + (f", and {more} more substitution(s)" if more else "")
    tag, i1, i2, j1, j2 = edits[0]
    if tag == "insert":
        first = f"added {_span(current, new_tokens, j1, j2)}"
    elif tag == "delete":
        first = f"removed {_span(previous, old_tokens, i1, i2)}"
    else:
        first = f"{_span(previous, old_tokens, i1, i2)} -> {_span(current, new_tokens, j1, j2)}"
    more = len(edits) - 1
    return first + (f", and {more} more edit(s)" if more else "")


def _orchestrator_rules_with_changes(
    store: Store, procedures_text: str
) -> tuple[list[dict], str | None]:
    """Every orchestrator-audience rule, each marked with whether its text changed
    since the previous brief in this project (KYO-531.c2).

    THE PREVIOUS STATE lives in the events ledger: each brief's own event records a
    digest of every rule it printed (:data:`RULE_DIGESTS_KEY`), and this reads the
    latest brief event that carries one. A brief event written by a build older than
    KYO-531 carries none, so it is skipped rather than read as "every rule is new" --
    the first brief after an upgrade marks nothing and says it had nothing to compare
    against. The digest is of :func:`procedures.rule_bodies`' normalized text, so
    re-wrapping a paragraph is not a change and rewording one is.

    A CHANGED rule is not reprinted (KYO-548): an upstream rename rewords most rules
    at once and reprinting every one defeats KYO-531's one-line form. It carries a
    one-line ``change_summary`` instead (:func:`rule_change_summary`), computed against
    the previous TEXT, which the brief events record once per wording
    (:data:`RULE_TEXTS_KEY`). Where the baseline's text was never recorded (a brief by
    a build older than KYO-548) the summary is null and the rule is marked changed and
    nothing more.

    Returns the rules (``text`` is the section verbatim, what `kyo rules "<title>"`
    prints; ``change`` is ``changed``, ``new`` or null; ``change_summary`` is a string
    only for a changed rule whose previous text is recorded) and the id of the brief
    they were compared against, or null."""
    bodies = procedures_module.rule_bodies(procedures_text)
    sections = procedures_module.rule_sections(procedures_text)
    # The baseline is the latest SESSION-START brief that recorded digests, and only when
    # there is none, the latest brief of any kind. A brief run by hand, by `--json`, or by
    # a viewer through `kyo serve` must not use up the marker before the session that
    # most needs it opens: the same anchor the build-change notice uses (KYO-531).
    previous: dict | None = None
    compared_to: str | None = None
    fallback: tuple[dict, str | None] | None = None
    for row in store.conn.execute(
        "SELECT after_json FROM events WHERE command = 'brief' ORDER BY seq DESC"
    ):
        try:
            after = json.loads(row["after_json"] or "null")
        except ValueError:
            continue
        if not (isinstance(after, dict) and isinstance(after.get(RULE_DIGESTS_KEY), dict)):
            continue
        if after.get("session_start") is True:
            previous, compared_to = after[RULE_DIGESTS_KEY], after.get("brief_run_id")
            break
        if fallback is None:
            fallback = (after[RULE_DIGESTS_KEY], after.get("brief_run_id"))
    if previous is None and fallback is not None:
        previous, compared_to = fallback
    recorded_texts = _recorded_rule_texts(store) if previous is not None else {}
    rules: list[dict] = []
    for r in procedures_module.orchestrator_rules(procedures_text):
        body = bodies.get(r.title, "")
        digest = _rule_digest(body)
        change: str | None = None
        change_summary: str | None = None
        if previous is not None:
            if r.title not in previous:
                change = RULE_NEW
            elif previous[r.title] != digest:
                change = RULE_CHANGED
                previous_text = recorded_texts.get(previous[r.title])
                if previous_text is not None:
                    change_summary = rule_change_summary(previous_text, body)
        rules.append(
            {
                "title": r.title,
                "enforced_by": r.enforced_by,
                "enforcement": enforcement_kind(r.enforced_by),
                "first_line": r.first_line,
                "text": sections.get(r.title, ""),
                "digest": digest,
                "change": change,
                "change_summary": change_summary,
            }
        )
    return rules, compared_to


def _new_rule_texts(store: Store, procedures_text: str, rules: list[dict]) -> dict:
    """``{RULE_TEXTS_KEY: {digest: normalized body}}`` for each printed rule whose
    digest no brief event has recorded text for yet, or ``{}`` when there is none
    (KYO-548.c2) -- so a brief whose rules did not change adds nothing to the ledger."""
    known = _recorded_rule_texts(store)
    bodies = procedures_module.rule_bodies(procedures_text)
    fresh = {
        r["digest"]: bodies.get(r["title"], "")
        for r in rules
        if r["digest"] not in known
    }
    return {RULE_TEXTS_KEY: fresh} if fresh else {}


def cmd_brief(ctx: Context, args: Namespace) -> Output:
    """``kyo brief [--compact] [--session-start]`` (KYO-8.c6) — the session briefing, derived
    entirely from the store: counts by kind, ready work, gated work with
    conditions, running dispatches and every UNANSWERED terminated-no-report
    dispatch (see :func:`_open_dispatches`, KYO-314.c1), unresolved sweep flags,
    the shift log since the previous brief, and what changed since the previous
    brief (a query over ``events`` bounded by the previous brief's own recorded
    cursor — never a git diff; design constraint).

    KYO-63 adds the staleness-coverage line: whether the last sweep actually answered
    the staleness question. Without it the flag list is the only staleness signal a
    controller sees at session start, and an empty one means both "clean" and "the check
    could not run".

    KYO-69 splits the flag list by whose ground actually moved (see
    :func:`_ranked_unresolved_flags`). This is the surface the defect landed on: an
    advisory section that costs more to read than it returns gets skipped, and the one
    flag that mattered is skipped with it."""
    store = ctx.store()
    # KYO-74.c1 / KYO-80. `kyo brief` is a read surface that WRITES — it allocates a
    # brief-run id and an event and then rewrites the git-canonical
    # `_allocations.json` from the database — so it has both of this seam's questions
    # to answer, and it answers each one where that question is already answered:
    #
    #   the write question ("would this lower a git-canonical counter and reissue an
    #   id the export has issued?") is `Currency.would_reissue_an_id`, and it is asked
    #   once, on `Store.transaction()`, which the write below goes through (KYO-75).
    #   Never ask it again here (docs/design/review.md#kyo-80).
    #
    #   the read question ("am I about to present an incomplete board as the whole
    #   board?") is `db_is_behind`, and it is answered the way `kyo list` answers it:
    #   `currency.as_data(seam)` below carries it into the payload, and `render`
    #   prints it first, unconditionally, ahead of every count and before `--compact`
    #   drops anything (law 6). A warning, not a refusal
    #   (docs/design/review.md#kyo-74).
    seam = currency.db_export_currency(store)
    registry = _load_registry(store)

    prior = store.latest_brief_run()
    since_event_seq = int(prior["last_event_seq"]) if prior is not None else 0
    since_shift_seq = int(prior["last_shift_note_seq"]) if prior is not None else 0
    previous_brief_at = prior["at"] if prior is not None else None

    deltas = [
        dict(row)
        for row in store.conn.execute(
            "SELECT id, seq, at, actor, command, record_id FROM events WHERE seq > ?"
            " ORDER BY seq",
            (since_event_seq,),
        )
    ]
    shift_notes = [dict(row) for row in store.shift_notes_since(since_shift_seq)]

    # KYO-414: the new watermarks are the highest seq this brief
    # actually LISTED, never a separate `MAX(seq)` read — nothing holds a read
    # transaction across the statements above, so a separate read can skip a row that
    # was never listed (docs/design/review.md#kyo-414). Nothing listed means the
    # watermark does not move.
    max_event_seq = max((int(d["seq"]) for d in deltas), default=since_event_seq)
    max_shift_seq = max((int(n["seq"]) for n in shift_notes), default=since_shift_seq)

    running, terminated_no_report, terminated_no_report_answered = _open_dispatches(store)
    procedures_text = _read_worker_procedures(store)
    orchestrator_rules, rules_compared_to = _orchestrator_rules_with_changes(
        store, procedures_text
    )
    # KYO-561.c1: an [E4] "no" withdraws the citation rule and its worked example. Dropped
    # before the digests below are recorded, so if the answer is later deleted the rule
    # reads as new to the session that has never been shown it -- which it is.
    links_declined = record_links_declined(store)
    if links_declined:
        orchestrator_rules = [r for r in orchestrator_rules if not _is_citation_rule(r["title"])]

    board = _ready_records(store, registry)
    data: dict = {
        "counts_by_kind": _counts_by_kind(store, registry),
        "ready": board.ready,
        # KYO-48: the brief is what a session-start hook puts in front of a
        # controller, so a class `kyo next` names and the brief hides would put
        # the silence straight back where it started.
        "scope_unknown": board.scope_unknown,
        # KYO-570.c2: records an unsettled `blocks` source holds back, from the same
        # call `kyo next` renders, so the two cannot disagree about a record.
        "blocked": board.blocked,
        # KYO-87: finished work that nobody moved. It was in `ready` above, which
        # is the list this brief offers as what to hand out next.
        "settled_not_moved": board.settled_not_moved,
        "awaiting_verification": _awaiting_verification(store, registry),
        # KYO-267: the class beside it — a criterion waiting on a person, with no gate
        # to make the wait findable and nothing running that would observe it. It was
        # in no section of this document at all, so the only place the ask lived was a
        # shift note.
        "ungated_unobserved": ungated_unobserved_records(store, registry),
        # KYO-496: filled in below, once this run's own stamp exists to age against.
        "gated": [],
        # KYO-497: the same, for the carried items' ages.
        "carried": [],
        # KYO-554: and for the open standing docs obligations' ages.
        "standing_obligations": [],
        "carried_kinds": [],
        "running_dispatches": running,
        "terminated_no_report": terminated_no_report,
        # KYO-314.c1: the count this same call found and did NOT return above,
        # because their record was already answered (settled, or a later dispatch
        # spoke for it and reported) — carried so `--json` can say what the text
        # says, rather than a reader only ever seeing the list get shorter with no
        # way to tell "answered" from "there was never anything here".
        "terminated_no_report_answered_count": terminated_no_report_answered,
        "unresolved_flags": _ranked_unresolved_flags(store),
        # KYO-63: the flag list above is the surface that reads as "nothing is stale",
        # and it reads that way just as convincingly when the staleness check never ran.
        "staleness": _staleness_coverage(store),
        # KYO-74.c1: and every count in this brief reads as the whole board just as
        # convincingly when the database is behind its export. This is the session's
        # first surface — a hook puts it in front of a controller before anything
        # else — so it is the one place a second checkout gets told before it acts.
        "db_export_currency": currency.as_data(seam),
        "shift_notes": shift_notes,
        # KYO-94.c4: the one shift note that arrives whatever the watermark says. The
        # window above is a delta and stays one; this is the standing guarantee beside
        # it, and it is rendered in full even under `--compact`, because `--compact` is
        # what the SessionStart hook runs and a handoff summarised to its id is not a
        # handoff. See :func:`_handoff_note` for what "this session" means.
        "handoff": _handoff_note(
            store,
            opening_a_session=bool(getattr(args, "session_start", False)),
            max_shift_seq=max_shift_seq,
        ),
        "deltas": deltas,
        "previous_brief_at": previous_brief_at,
        # KYO-519 (from KYO-513): renamed from `controller_rules`, with no alias -- the
        # only consumer is render.py in this same build, and a second key would be a
        # copy that can drift (law 3).
        "orchestrator_rules": orchestrator_rules,
        # KYO-531.c2: the earlier brief each rule's `change` was measured against --
        # the latest one whose event recorded rule digests -- or null when none has,
        # in which case no rule is marked and the text says why.
        "orchestrator_rules_compared_to": rules_compared_to,
        # KYO-356.c2: the worked example printed beside the controller-audience
        # rules -- built here, once, from this store's own prefix, rather than
        # re-derived by render.py from data it would otherwise have to carry
        # anyway (the prefix, a sample record).
        # KYO-561.c1: null when [E4] is recorded as declined -- render prints no
        # example then, and the citation rule is gone from the list above.
        "link_example": None if links_declined else _link_form_example(store),
        # KYO-213: read at brief time so the drift reaches a controller before, not
        # after, a session driven by a stale copy.
        "asset_drift": _asset_drift(store),
        # KYO-586.c3: one line while recx is installed and this project has recorded
        # neither on nor off at recx.prior_art.state; null otherwise. Asked with find_spec
        # and no import, so a project without recx pays one path lookup for it.
        "prior_art_unchosen": prior_art.unchosen_line(store.config.root),
        # KYO-226.c2: which build produced this brief, carried in the document and
        # rendered on the first line. A session opens on a board *and* on the identity
        # of the tool that derived it (docs/design/review.md#kyo-226). Exactly what
        # `kyo version` prints,
        # from the same function, so the two surfaces cannot drift apart.
        "build": buildinfo.build_identity().as_data(),
    }
    # KYO-398.c4/c5: computed before this run's row exists, so "the previous
    # session-start brief" is the previous session's and not this one.
    data["build_change"] = _build_change(
        store, data["build"], opening_a_session=data["handoff"]["opens_this_session"]
    )

    session_id = (
        _hook_session_id() if data["handoff"]["opens_this_session"] else None
    )
    data["session_id"] = session_id
    with store.transaction():
        brief_run_id, seq = store.new_brief_run_id()
        stamp = now()
        data["brief_run_id"] = brief_run_id
        data["generated_at"] = stamp
        data["gated"] = _gated_records(store, at=stamp)
        carried = _carried_records(store, registry, _load_status_ladder(store), at=stamp)
        data["carried"] = carried["items"]
        data["carried_kinds"] = carried["kinds"]
        data["standing_obligations"] = _standing_obligations(store, at=stamp)
        if data["handoff"]["opens_this_session"]:
            # This run is its own session anchor, and only now does it have an id to
            # name. Stated rather than left null: a reader has to be able to check
            # which brief the handoff was measured from.
            data["handoff"]["session_brief_id"] = brief_run_id
            data["handoff"]["session_started_at"] = stamp
        store.conn.execute(
            "INSERT INTO brief_runs (id, seq, at, actor, last_event_seq,"
            " last_shift_note_seq, session_start, build_commit, build_commit_time,"
            " build_source, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                brief_run_id,
                seq,
                stamp,
                ctx.actor,
                max_event_seq,
                max_shift_seq,
                # 1/0 and never NULL from this build on: NULL is reserved for rows
                # written before the column existed, which were never asked.
                1 if data["handoff"]["opens_this_session"] else 0,
                # KYO-398.c3: which build wrote this brief. `build_source` is never
                # NULL from this build on — "unknown" is an answer; NULL is "never
                # asked", the state of every row written before schema_version 24.
                data["build"]["commit"],
                data["build"].get("commit_time"),
                data["build"]["source"],
                # KYO-418: the session this brief opened, so the
                # per-prompt `kyo check-build --hook` compares against ITS session's
                # opening build and not whichever session started last.
                session_id,
            ),
        )
        brief_run_row = store.conn.execute(
            "SELECT * FROM brief_runs WHERE id = ?", (brief_run_id,)
        ).fetchone()
        event = store.record_event(
            actor=ctx.actor,
            command="brief",
            record_id=None,
            before=None,
            after={
                "brief_run_id": brief_run_id,
                "since_event_seq": since_event_seq,
                "deltas": len(deltas),
                "unresolved_flags": len(data["unresolved_flags"]),
                # KYO-94: which briefs opened a session is a fact about the shape of the
                # project's history, so it belongs in the ledger a human reads too.
                "session_start": data["handoff"]["opens_this_session"],
                # KYO-531.c2: what the NEXT brief compares against to say which rule
                # changed -- title -> digest of its normalized text. Kept in this
                # event rather than a `brief_runs` column: the event is already this
                # brief's git-canonical record and survives a rebuild unchanged.
                RULE_DIGESTS_KEY: {r["title"]: r["digest"] for r in orchestrator_rules},
                # KYO-548.c2: and the text behind each digest no earlier brief event
                # recorded, so the next brief can say WHAT changed in a rule rather than
                # reprint it. Once per wording, not per brief: measured on this repo's
                # own store, every orchestrator rule's normalized text is ~20 KB of JSON,
                # against ~1.5 KB for a whole brief event today.
                **_new_rule_texts(store, procedures_text, orchestrator_rules),
            },
        )
    events_ledger.append_events(store, [event])
    ledgers.append_brief_run(store, brief_run_row)
    ledgers.write_allocations(store)

    text = brief_text(data, compact=bool(args.compact))
    # KYO-559.c6: a session-start brief fills in a missing code-repository identity the
    # way `kyo doctor` does -- the same function, one line saying so, never an overwrite.
    data["code_repo_id_recorded"] = None
    if data["handoff"]["opens_this_session"] and store.config.code_root is not None:
        from . import scaffold

        recorded = scaffold.fill_in_code_repo_id(store.config.root, store.config.code_root)
        if recorded:
            data["code_repo_id_recorded"] = "; ".join(recorded)
            text = f"{text}\n\n{data['code_repo_id_recorded']}"
    return Output([text], {"ok": True, "command": "brief", **data})


def cmd_note(ctx: Context, args: Namespace) -> Output:
    """``kyo note --shift --file f`` (KYO-8.c7) — appends to the continuously-
    written shift log; the next `kyo brief` surfaces it. ``--shift`` is
    required because it is the only note kind v1 ships (charter, "Shift
    continuity": "written continuously ... never authored retrospectively at
    closeout").

    KYO-131: the refusals live here, not in argparse, which could only say what was
    missing. A record id passed to this command is a controller asking for a
    *record-scoped* note, which a different command writes, so the id is named back to
    them and that write path is named too, instead of the flag being restated
    (docs/design/review.md#kyo-131).
    """
    stray = getattr(args, "record", None)
    if stray:
        raise UsageError(
            f"kyo note writes the shift log and takes no record id, so {stray!r} has no "
            f"place here. For a note ON a record, use `kyo edit {stray} --note-file FILE` "
            f"— it appends a record-scoped note ({stray}.nN, superseded with "
            "`--supersede-note`), which is a different object from a shift note. For a "
            "shift-log entry, drop the id: `kyo note --shift --file FILE`"
        )
    if not args.shift:
        raise UsageError(
            "kyo note requires --shift (the only note kind v1 ships). If you meant a "
            "note ON a record rather than an entry in the shift log, that is "
            "`kyo edit <RECORD> --note-file FILE`"
        )
    if not args.file:
        raise UsageError(
            "kyo note requires --file FILE (or '-' for stdin) — prose enters the store "
            "by file or stdin, never as a flag value (law 5)"
        )
    store = ctx.store()
    text = read_prose(args.file, what="--file")
    if not text.strip():
        raise UsageError("a shift note must not be empty")

    with store.transaction():
        note_id, seq = store.new_shift_note_id()
        stamp = now()
        store.conn.execute(
            "INSERT INTO shift_notes (id, seq, text, author, created_at) VALUES (?, ?, ?, ?, ?)",
            (note_id, seq, text, ctx.actor, stamp),
        )
        note_row = store.conn.execute(
            "SELECT * FROM shift_notes WHERE id = ?", (note_id,)
        ).fetchone()
        event = store.record_event(
            actor=ctx.actor,
            command="note --shift",
            record_id=None,
            before=None,
            after={"shift_note_id": note_id, "text": text},
        )
    events_ledger.append_events(store, [event])
    ledgers.append_shift_note(store, note_row)
    ledgers.write_allocations(store)

    lines = [f"appended shift note {note_id}"]
    lines += echo_block("shift note", text)
    return Output(lines, {"ok": True, "command": "note", "shift_note_id": note_id, "text": text})
