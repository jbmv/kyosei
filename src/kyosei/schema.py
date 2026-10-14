"""SQLite DDL, and the law-2 consumer registry.

Law 2 — *no stored field without a consumer*. ``COLUMN_CONSUMERS`` names, for every
column in the schema, the code path(s) that read it. ``kyo doctor`` compares this
static map against ``PRAGMA table_info`` in both directions, so:

* adding a column without registering a consumer fails ``doctor``;
* dropping a column while leaving its consumer entry behind also fails ``doctor``.

That is the whole point: the checker ships in the same commit as the field.

KYO-3: this module also carries the schema-version discipline the DDL itself needs.
``SCHEMA_VERSION`` and ``DDL`` must move together — ``tests/test_schema_compat.py``
pins a normalised hash of ``DDL`` to the declared ``SCHEMA_VERSION`` so a schema
change that forgets to bump the version fails the suite (KYO-3.c4), the way the
milestone-3 ``touches`` table addition did not.
"""

from __future__ import annotations

import hashlib
import os as _os
import re as _re

from .sweep import SWEEP_COVERAGE_UNRECORDED

#: Bumped to 5 for milestone 8 (KYO-13.c4 / KYO-12: a dispatch may formally cover
#: additional records via ``dispatches.covers_json``, so its report can carry
#: criteria verdicts for those records too). Any DDL change MUST bump this number
#: in the same commit — see ``ddl_fingerprint`` below and
#: ``tests/test_schema_compat.py``, which fails the suite otherwise (KYO-3.c4).
#: Bumped to 8 for KYO-32 (``dispatches.branch``: a dispatch declares the branch
#: its commits must land on, and ``kyo report`` checks reachability against it).
#: Bumped to 9 for KYO-52 — the first bump that changes no DDL at all. What changed
#: is what a stored value *means*: ``dispatches.worktree`` and ``touches.path`` are
#: written relative to the project root from version 9 on, where 8 and earlier
#: stored the resolved absolute path. A format change under an unchanged column name
#: is precisely what a version number exists to make visible; see
#: :data:`PRE_RELATIVE_PATHS_SCHEMA_VERSION` for what ``kyo rebuild`` does with a
#: document written under the old meaning.
#: Bumped to 10 for KYO-63 (``sweep_runs.code_repo_id`` / ``coverage`` /
#: ``coverage_detail``): a sweep's base sha belongs to the CODE repository while the
#: bookkeeping lives in the STORE repository, and under the split layout those are two
#: histories. A run now records which history it swept and whether the staleness
#: question was answered at all, so a restore beside a code repo that does not contain
#: the base is detectable instead of merely quiet.
#: Bumped to 11 for KYO-61 (``dispatches.worker_model``): a dispatch records which model
#: executed it, resolved from ``kyosei/config.yml`` -> the kind's policy in ``kinds.yml`` ->
#: ``kyo dispatch --model``. Kyosei does not spawn workers and must not start, so this field
#: is ``enforced-by: aspirational`` — stated in the frozen brief and recorded on the
#: dispatch, never verified. NULL means *no model was ever declared*, which is a different
#: fact from any particular model and must never render as one.
#: Bumped to 12 for KYO-94 (``brief_runs.session_start``): the store had no session concept
#: at all, so "the shift note authored before this session began" was not a question it could
#: answer — while the shift-log watermark meant a handoff note was surfaced in exactly one
#: brief and never again by any command. A brief run now records whether it was the one a
#: controller with fresh context opened on (``kyo brief --session-start``, which the packaged
#: ``SessionStart`` hook runs), which is the only fact anything in this system knows about
#: where a session begins. NULL means *this run predates the field* — it is not "no", and it
#: must never anchor a session, because a run that was never asked cannot have answered.
#: Bumped to 13 for KYO-100 (``records.scope``): scope becomes a schema dimension, the
#: first of four planned per-project registries to gain one. A record may declare which
#: part of the system its work belongs to, validated at write time against
#: ``kyosei/scopes.yml`` (:mod:`kyosei.registry`). NULL means *no scope was declared* — the
#: state of every record written before this column and the honest state of a record whose
#: work spans the project. It must never be filled in by derivation: deriving scope from
#: ``touches`` is a suggester and validator, never the source, and a record with no
#: touches (or a document) has nothing to derive from in the first place.
#: Bumped to 14 for KYO-101 (``records.status``): the second bump that changes no DDL at
#: all, and the same kind of change version 9 was — what a stored value *means*. A status
#: was a member of a hardcoded six-tuple in ``contracts``; from 14 on it is a name
#: declared in the per-project registry ``kyosei/statuses.yml``, mapping to an archetype
#: role, and every transition contract binds to the **role** rather than to the name
#: (``complete-requires-settled-criteria``, not ``done-requires-settled-criteria``).
#:
#: There is deliberately no entry in :data:`EXPORT_FIELD_DEFAULTS` for 14, and that is
#: not an omission: no export field is added, removed or renamed, so there is no absent
#: key for a default to fill in. Every document written under 13 or earlier carries a
#: ``status`` whose value is one of v1's six names, and the packaged default registry
#: declares exactly those six names with exactly v1's behaviour — so such a document
#: loads unchanged and the store it rebuilds into behaves identically. What a rebuild
#: cannot do is notice that the project has since RENAMED a status out from under those
#: documents; that is a live dangling reference rather than a format question, and
#: ``doctor_records.check_statuses`` is what makes it loud (naming the record and the registry),
#: exactly as ``check_scope_registry`` does one dimension over.
#: Bumped to 15 for KYO-102 (``gates.tags_json``): the third of the four planned config
#: dimensions to become a registry, and the one whose whole purpose is a query. A gate's
#: classification was carried entirely by its free-text ``detail`` payload, so a query
#: such as "everything waiting on a human to review a UI element" was not
#: something the store could be asked. A gate may now carry tags from
#: ``kyosei/gate-tags.yml`` (:mod:`kyosei.registry`), refused at write time if the
#: registry does not declare them. NULL means *no tags were declared*, which is the state
#: of every gate written before this column and the honest state of a gate whose payload
#: says everything there is to say. The payload is not replaced: ``detail`` keeps its
#: meaning, and ``GATE_TYPES_REQUIRING_DO`` still requires it for the three gate types
#: nobody could clear without it.
#: Bumped to 16 for KYO-137 (``doc_refs`` and ``docs_obligations``): the docs governance
#: layer. Two tables rather than one, because it has two halves with different authors.
#: ``doc_refs`` is a DECLARATION, written by a human on a record — the back path from a
#: record to a document, deliberately not a second ``governs:`` because ``governs``
#: asserts authority and a record mentioning a document has none. ``docs_obligations`` is
#: a QUESTION, raised by the tool at report time and settled only by a controller's
#: asserted verdict with a method — never by a timestamp, which
#: is why the row carries ``verdict``/``method``/``author`` and not a ``cleared_at``
#: alone. One version bump because neither half is usable without the other.
#: Bumped to 17 for KYO-181 (every LEDGER line carries a ``schema_version``): the third
#: bump that changes no DDL at all, and the same kind of change 9 and 14 were — a format
#: change no column shape can express. Until this version the four append-only ledgers
#: (``_events.jsonl``, ``_sweeps.jsonl``, ``_briefs.jsonl``, ``_shift_notes.jsonl``)
#: carried no version anywhere, so a bump that touched ONLY a ledger — which 10 (KYO-63)
#: and 12 (KYO-94) both were — left nothing in the export for KYO-179's guard to read: not
#: one record document need have changed, and ``kyo rebuild`` dropped the later build's
#: extra ledger keys in silence. From 17 on the stamp goes on the line, with the line, and
#: is never rewritten — the form chosen because it is bound to the bytes it versions and
#: because a file-header stamp would have to be rewritten on the version's first append,
#: costing the append-only merge-cleanly property the event ledger was chosen for
#: (charter, Layer 2). See :func:`kyosei.export.stamp_ledger_row` for the full trade.
#:
#: There is deliberately no new entry in the ledger defaults tables below, and that is not
#: an omission: no ledger FIELD is added, removed or renamed, so there is no absent key for
#: a default to fill in. The pre-10 and pre-12 tolerances are untouched and keep working on
#: a line that carries no stamp at all, because they were always keyed on the absence of
#: the field rather than on a version number (KYO-181.c4).
#: Bumped to 18 for KYO-172 (``idx_events_command``): the first bump whose whole content is
#: an INDEX. Nothing about what is stored changes — no table, no column, no export field —
#: and that is why the entry below in :data:`EXPORT_FIELD_DEFAULTS` is deliberately absent
#: for the same reason 14 and 17 have none: there is no key an older document is missing, so
#: there is nothing for a default to fill in, and every export written at 17 or earlier loads
#: byte-for-byte as it did. What DOES change is the DDL, which is why the version has to
#: move: ``ddl_fingerprint`` covers indexes as well as tables, and a store built before this
#: version simply does not have the index. There is no migration path in this system and
#: there deliberately is not one — ``store.open_store`` refuses a db behind the build and
#: names ``kyo rebuild --force``, which reconstructs from the git-canonical export under this
#: DDL and therefore builds the index. So the bump is not bookkeeping here: it is the only
#: thing that gets the index onto an existing store at all.
#: Bumped to 19 for KYO-268.c1 (an event snapshot names evidence, it does not carry it):
#: the fourth bump that changes no DDL at all, and the same kind of change 9, 14 and 17
#: were — a format change no column shape can express. An event's ``before``/``after`` is
#: a whole record document, and a record document embeds every evidence artifact's body
#: verbatim, so every mutation of a record copied every artifact on it into the ledger
#: twice. From 19 on a snapshot carries each artifact as ``{id, sha256}`` and nothing else
#: (``events_ledger.EVENT_SNAPSHOT_EVIDENCE_KEYS``); the body stays where it is stored once, on
#: the record's own export document. Measured on Kyosei's own store: 55.0 MB of a
#: 224.1 MB ledger was evidence bodies across 3,736 snapshots.
#:
#: There is deliberately NO entry in :data:`EXPORT_FIELD_DEFAULTS` or in any of the
#: per-child defaults tables below, for the reason 14, 17 and 18 have none — and it is
#: worth stating because this bump points the other way from the ones those tables exist
#: for. Those defaults answer "an older document is MISSING a key this build reads"; here
#: an older ledger line CARRIES keys this build no longer writes, and it carries them
#: harmlessly: ``rebuild`` loads an event's snapshots as opaque JSON, so a line with the
#: bodies in it loads byte-for-byte as it always did and stays readable forever. The one
#: reader that looks inside a snapshot's evidence list is ``kyo redact``'s ledger scrub,
#: and it is keyed on whether the ``text`` key is present rather than on a version number
#: (``ledger_scrub._scrub_events``) — because the two formats are mixed inside one append-only
#: ledger by construction, which is the same argument ``SWEEP_RUN_FIELD_DEFAULTS`` makes
#: for keying on an absent field instead of a stamp.
#: Bumped to 20 for KYO-268.c2 (the events ledger is a directory of bounded shards): the
#: fifth no-DDL bump, and the first one that changes WHICH FILES the export is made of
#: rather than what is inside one. ``_events.jsonl`` was a single tracked file that every
#: commit re-stored whole — 224.1 MB here, 331.1 MB in the largest consumer — so git's
#: periodic repack delta-searched dozens of copies of it against each other and took the
#: machine with it (22 GB resident, 27.6 GB of swap). From 20 on the
#: ledger is ``kyosei/export/_events/NNNNNN.jsonl``, appended to the newest shard until
#: it reaches ``events_ledger.EVENTS_SHARD_MAX_BYTES``, so a mutation rewrites at most that much.
#: ``events_ledger.read_events_in`` reads a monolithic ``_events.jsonl`` first and then every
#: shard in order, which is what makes an un-migrated export load unchanged; `kyo export
#: --migrate-events` is the one run that converts one layout to the other, and
#: ``doctor_dispatch.check_events_ledger_layout`` is what names it.
#:
#: No defaults entry here either, and this time because no LINE changes at all: sharding
#: moves bytes between files and rewrites none of them. A line written at 19 into
#: ``_events.jsonl`` is the same line, still stamped 19, once the migration has packed it
#: into a shard — which is what makes the migration lossless and what lets the version
#: guard go on reading each line's own stamp (``rebuild.read_ledger_rows``).
#: Bumped to 21 for KYO-274.c1 (an event snapshot names a frozen brief and an ingested
#: report, it does not carry them): the sixth no-DDL bump, and the same change 19 was,
#: applied to the two bodies 19 left behind. Evidence was the first body measured, not
#: the only one: a record document also embeds each dispatch's frozen ``brief`` and each
#: report's ingested ``body``, so every mutation of a dispatched record copied both into
#: the ledger twice. Measured on Kyosei's own store and filed as KYO-274: 68.3 MB of
#: briefs and 49.1 MB of reports in a 224.1 MB ledger,
#: against the 55.0 MB of evidence 19 removed. From 21 on a snapshot's ``dispatches[]``
#: carries ``brief_sha256`` in place of ``brief`` and its ``reports[]`` carries
#: ``body_sha256`` in place of ``body`` (``events_ledger.EVENT_SNAPSHOT_BODIES``, read by
#: ``events_ledger.event_snapshot`` and by nothing else, so the three treatments cannot
#: diverge); both bodies stay where they are stored once, on the record's own export
#: document. Unlike an evidence artifact — reduced to ``{id, sha256}`` because it is
#: immutable — a dispatch and a report keep every other field, because a dispatch row
#: MUTATES (``running`` -> ``reported``, ``report_id``, ``closed_at``) and the current
#: export document shows only where it ended up: dropping those would delete the ledger's
#: record of the transition, which is the question the ledger exists to answer.
#:
#: No entry in :data:`EXPORT_FIELD_DEFAULTS` or in any per-child defaults table, for
#: exactly the reason 19 has none, and it is the same direction: an older ledger line
#: CARRIES keys this build no longer writes, harmlessly, because ``rebuild`` loads an
#: event's snapshots as opaque JSON. The readers that look INSIDE a snapshot are keyed on
#: content rather than on a version number and so read both formats — ``kyo redact``'s
#: id-based ledger scrub never looks at ``dispatches``/``reports`` at all
#: (``ledger_scrub._scrub_events``), its embedded-copy scrub matches TEXT in the file and
#: therefore still finds a brief in an old line and nothing in a new one
#: (``ledger_scrub.scrub_text_from_ledgers``), and ``store.description_dated_at`` and
#: ``changelog._completion_dates`` read scalar fields this change does not touch.
#: Bumped to 22 for KYO-345 (a dispatch may end as ``withdrawn``): the seventh no-DDL
#: bump, and the same kind of change 9 and 14 were — what a stored value MEANS, under
#: an unchanged column name. Two values move together, which is why it is one bump.
#: ``dispatches.state`` gains a fourth member, ``withdrawn``
#: (``contracts.DISPATCH_STATES``), for a dispatch frozen by mistake and never handed
#: to a worker — until this version the only exit from ``running`` without a report was
#: ``terminated-no-report``, which means "died mid-work" and which the session brief
#: tells the next controller to flag, so the store's account of a controller's typo was
#: a death that never happened. And ``dispatches.terminated_why`` widens from "why this
#: dispatch was terminated" to "why this dispatch ended with no report", because one
#: column holds the reason for both states and the WORD a reader sees is derived from
#: the state (``contracts.DISPATCH_CLOSE_REASON_LABELS``).
#:
#: THE BUMP IS THE POINT HERE, more than in most of the entries above, and it is worth
#: being plain about which direction it protects. There is deliberately no entry in
#: :data:`EXPORT_FIELD_DEFAULTS` or in :data:`DISPATCH_FIELD_DEFAULTS`, for the reason
#: 14, 17, 18, 19, 20 and 21 have none: no export field is added, removed or renamed,
#: so there is no absent key for a default to fill in, and every document written at 21
#: or earlier loads byte-for-byte as it did — a ``state`` of ``running`` / ``reported``
#: / ``terminated-no-report``, and a ``terminated_why`` written under the narrower
#: meaning, are all still exactly what they were. The direction that needed guarding is
#: the other one: a document written at 22 can carry ``"state": "withdrawn"``, and a
#: build at 21 has no such member in ``contracts.DISPATCH_STATES``. Without this bump
#: that document is missing nothing, so ``rebuild``'s version guard (KYO-179) cannot
#: see it, and the older build reconstructs a store holding a dispatch state it does
#: not know — green until ``doctor_dispatch.check_dispatch_state_consistency`` calls it an
#: unknown state, which is a loud failure arriving one boundary too late and pointing
#: at the store rather than at the build. With the bump the older build refuses the
#: export by name and says to upgrade, which is the whole job of the number.
#: KYO-375: dispatches gains tick_audit_json -- a not-a-worker tick
#: (STOP_TARGET_NO_TRANSCRIPT) no longer writes a row to the git-canonical events
#: ledger; it increments this counter (count, first_seen, last_seen) on each
#: candidate dispatch's own row instead, in place, so the ledger gains nothing per
#: tick. A real DDL change, so a real new hash.
#: Bumped to 24 for KYO-398 (``brief_runs.build_commit`` / ``build_commit_time`` /
#: ``build_source``): each brief run records which build of `kyo` produced it. Every
#: project on a machine runs the one installed `kyo` (the uv tool, KYO-335), so an
#: upgrade changes the tool under every project at once and none of
#: them caused it; the brief already PRINTED the build (KYO-226.c2) but stored it
#: nowhere, so nothing could compare it with the last session's. A session-start brief
#: now compares its own build with the one recorded by the project's previous
#: session-start brief and says first when they differ, and the per-prompt hook
#: compares the running build with this session's opening brief. A real DDL change.
#: NULL in all three is *this run predates the field*, filled in by
#: :data:`BRIEF_RUN_FIELD_DEFAULTS` for a prior-format ledger line; a run written from
#: 24 on always carries ``build_source`` (``unknown`` when the build could not say),
#: so the two ways of not knowing are told apart and both render as "cannot tell".
#: Bumped to 25 for KYO-418, which carries four changes from one code review that
#: each needed a stored-format change. (1) An event line carries ``after``
#: whole and ``changed`` — the prior value of each top-level key that differs — in place
#: of a whole ``before`` (``events.changed_json``; ``events_ledger.event_diff``); a one-field
#: change no longer stores the record twice. Old lines are never rewritten, so the
#: readers key on content (``events_ledger.event_prior``) and :data:`EVENT_FIELD_DEFAULTS` /
#: :data:`EVENT_RETIRED_FIELDS` say what either format's missing key means. (2)
#: ``brief_runs.session_id``: the session a session-start brief opened, so
#: ``kyo check-build --hook`` compares against its own session's opening build; NULL
#: for every older row (:data:`BRIEF_RUN_FIELD_DEFAULTS`). (3)
#: ``dispatches.tick_audit_json`` gains an ``unresolved`` counter beside the tick count:
#: a non-worker SubagentStop that matches no dispatch is counted there, on each RUNNING
#: candidate, instead of writing an events-ledger row, and ticks are counted against
#: running candidates only. An older counter simply has no ``unresolved`` key, which
#: reads as zero. (4) What a no-op ``kyo sweep`` writes: nothing (no ``sweep_runs``
#: row, no event), which changes what the export's ledgers MEAN rather than their shape.
#: A real DDL change, so a real new hash.
#: Bumped to 26 for KYO-498 (from KYO-483): ``records.since_note``, the id of the shift
#: note that first recorded a carried item, so the brief's age for an item a project
#: already carried counts from that note rather than from the day it was filed. Only the
#: id is stored: the time is always read from that note's tool-stamped ``created_at``
#: (Law 16), so the store holds no second copy of a time that could disagree with the
#: first. No foreign key, because `kyo rebuild` loads records and the shift-note ledger
#: in separate passes; the write-time check in ``kyo new`` / ``kyo edit`` is the guard,
#: and a shift note is never deleted, only redacted with its ``created_at`` kept. A
#: record document from an earlier export declares no note (:data:`EXPORT_FIELD_DEFAULTS`
#: [26]). A real DDL change, so a real new hash.
#: Bumped to 27 for KYO-554 (standing docs obligations): ``docs_obligations.dispatch_id``
#: becomes nullable. Until this version every obligation was raised by `kyo report` for a
#: dispatch's branch; from 27 on a record whose kind sets
#: ``policy.raises_docs_obligations`` (the default ``decision``) raises one when it is
#: filed or edited, on each ssot section its text names by id and each of its doc-refs,
#: and such an obligation has no dispatch -- NULL is that fact, never "unknown". A real DDL
#: change, so a real new hash.
#:
#: There is deliberately no entry in :data:`EXPORT_FIELD_DEFAULTS` or a per-obligation
#: defaults table, for the reason 14, 17 and 22 have none: no export field is added,
#: removed or renamed, so a document written at 26 or earlier is missing nothing and loads
#: byte-for-byte as it did (every obligation in it carries a dispatch id). The direction
#: the bump guards is the other one, the one 22 guarded: a document written at 27 can carry
#: an obligation whose ``dispatch_id`` is null, which a build at 26 would refuse half-way
#: through a rebuild on its NOT NULL column. With the bump that build refuses the export
#: by name and says to upgrade instead.
SCHEMA_VERSION = 27

#: KYO-101: the last export format whose ``records.status`` values were members of a
#: fixed vocabulary rather than names from ``kyosei/statuses.yml``. Nothing rewrites a
#: value on the strength of it — a status name is loaded verbatim, for the same reason
#: :func:`stored_path_from_export` loads a legacy absolute path verbatim: what is already
#: committed to the git-canonical export is the maintainer's to migrate, not a rebuild's.
#: It is declared so the version at which the meaning changed is a fact in the code
#: rather than one only this comment knows.
PRE_STATUS_REGISTRY_SCHEMA_VERSION = 13

DDL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Law 4: the single source of ids. Counters only ever go up; nothing is ever
-- returned to the pool, so an archived or wont-done id can never be reissued.
CREATE TABLE allocations (
    scope      TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL
);

CREATE TABLE records (
    id             TEXT PRIMARY KEY,
    seq            INTEGER NOT NULL UNIQUE,
    kind           TEXT NOT NULL,
    title          TEXT NOT NULL,
    status         TEXT NOT NULL,
    plan_id        TEXT REFERENCES records(id),
    -- KYO-100: which part of the system this record's work belongs to, named
    -- from the per-project registry in kyosei/scopes.yml and validated against it at
    -- write time. Optional: NULL is "no scope was declared", which is what every
    -- record written before this column says and what a record whose work spans the
    -- project says too. Nothing derives it -- see SCHEMA_VERSION's note and
    -- kyosei.registry.suggest_from_paths, which suggests and stores nothing.
    scope          TEXT,
    description    TEXT NOT NULL DEFAULT '',
    -- KYO-498: the shift note (PREFIX-Sn) that first recorded this carried item, or
    -- NULL. Only the id: the item's age reads that note's created_at. No REFERENCES,
    -- because rebuild loads records before the shift-note ledger; kyo new/edit check it.
    since_note     TEXT,
    wont_do_reason TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE criteria (
    id         TEXT PRIMARY KEY,
    record_id  TEXT NOT NULL REFERENCES records(id),
    seq        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    state      TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (record_id, seq)
);

-- Append-only. There is no UPDATE of note text and no DELETE anywhere in the
-- codebase; revision happens by appending a new note and supersede-marking the old.
CREATE TABLE notes (
    id            TEXT PRIMARY KEY,
    record_id     TEXT NOT NULL REFERENCES records(id),
    seq           INTEGER NOT NULL,
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    superseded_by TEXT REFERENCES notes(id),
    superseded_at TEXT,
    -- Milestone 9 (KYO-25): see evidence_artifacts. Verbatim prose is verbatim
    -- prose wherever it is stored, and a note is one of the places a controller
    -- pastes a line it just read.
    redacted_at   TEXT,
    redacted_why  TEXT,
    UNIQUE (record_id, seq)
);

CREATE TABLE gates (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id),
    seq         INTEGER NOT NULL,
    gate_type   TEXT NOT NULL,
    detail      TEXT,
    -- KYO-102: what KIND of waiting this is, named from the per-project
    -- registry in kyosei/gate-tags.yml and validated against it at write time. A JSON
    -- array of tag names in the order they were given, or NULL for a gate carrying
    -- none -- which is every gate written before this column, and the honest state of
    -- a gate whose `detail` payload says all there is to say. `detail` above is
    -- unchanged and still required for GATE_TYPES_REQUIRING_DO: the tag says what kind
    -- of thing is being waited on, the payload says which one.
    tags_json   TEXT,
    created_at  TEXT NOT NULL,
    cleared_at  TEXT,
    cleared_why TEXT,
    UNIQUE (record_id, seq)
);

-- Milestone 3: files/symbols a promoted record's work is expected to touch, authored
-- at `kyo promote` time and validated against the repo *then* (charter, Layer 1:
-- "staleness is guarded at the boundary, not in the pool"). `kyo sweep` (milestone 6)
-- is their eventual full consumer -- see schema.COLUMN_CONSUMERS["touches"].
CREATE TABLE touches (
    id         TEXT PRIMARY KEY,
    record_id  TEXT NOT NULL REFERENCES records(id),
    seq        INTEGER NOT NULL,
    path       TEXT NOT NULL,
    symbol     TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (record_id, seq)
);

-- Milestone 4 (law 9): raw captures stored verbatim -- request dumps, console
-- excerpts, pasted human observations. Law 5's echo-back happens at write time;
-- `sha256` is the same digest, stored so `kyo show` never has to recompute it.
-- `supports_id` is a soft pointer (claim id, criterion id, or record id) -- not a
-- foreign key, because it may legitimately name any of three tables; `kyo doctor`'s
-- `check_orphans` verifies it resolves.
CREATE TABLE evidence_artifacts (
    id           TEXT PRIMARY KEY,
    record_id    TEXT NOT NULL REFERENCES records(id),
    seq          INTEGER NOT NULL,
    text         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    supports_id  TEXT,
    author       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    -- Milestone 9 (KYO-22): `kyo redact` sets both together, or neither. There is
    -- deliberately no `original_sha256` column: a sha256 of a short, low-entropy
    -- identifier (a patient or consumer id) is brute-forceable in seconds, so
    -- keeping the digest in a git-canonical export would publish the very value
    -- the redaction exists to remove. The digest goes with the text.
    redacted_at  TEXT,
    redacted_why TEXT,
    UNIQUE (record_id, seq)
);

-- Milestone 4 (laws 3, 9, 10): one row per `kyo verify` call. A criterion's `state`
-- is DERIVED from its latest verification's `verdict` (by `seq`) -- `kyo verify`
-- writes both in the same transaction, and `kyo doctor`'s
-- `check_criterion_verification_agreement` fails the build if they ever disagree
-- (KYO-4.c5). `kyo edit --criterion --state` may still flip the two void states
-- directly (see contracts.EDIT_DIRECT_CRITERION_STATES) -- those are premise
-- judgments, not verified facts, and already require --why.
CREATE TABLE verifications (
    id           TEXT PRIMARY KEY,
    criterion_id TEXT NOT NULL REFERENCES criteria(id),
    record_id    TEXT NOT NULL REFERENCES records(id),
    seq          INTEGER NOT NULL,
    verdict      TEXT NOT NULL,
    method       TEXT NOT NULL,
    reason       TEXT,
    author       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    evidence_id  TEXT REFERENCES evidence_artifacts(id),
    UNIQUE (record_id, seq)
);

-- Milestone 4 (law 9): claims are append-only, mirroring `notes`'s supersede
-- pattern -- a reconsidered claim supersedes the prior one rather than being
-- edited in place, so "true, then false, then true again" stays a readable
-- history rather than an overwrite (charter, law 9; KYO-4.c2).
CREATE TABLE claims (
    id            TEXT PRIMARY KEY,
    record_id     TEXT NOT NULL REFERENCES records(id),
    seq           INTEGER NOT NULL,
    statement     TEXT NOT NULL,
    level         TEXT NOT NULL,
    method        TEXT NOT NULL,
    author        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    supersedes    TEXT REFERENCES claims(id),
    superseded_by TEXT REFERENCES claims(id),
    superseded_at TEXT,
    -- Milestone 9 (KYO-25): a claim's `statement` is verbatim prose too.
    redacted_at   TEXT,
    redacted_why  TEXT,
    UNIQUE (record_id, seq)
);

CREATE TABLE links (
    id         TEXT PRIMARY KEY,
    seq        INTEGER NOT NULL UNIQUE,
    src_id     TEXT NOT NULL REFERENCES records(id),
    edge_type  TEXT NOT NULL,
    dst_id     TEXT NOT NULL REFERENCES records(id),
    created_at TEXT NOT NULL,
    UNIQUE (src_id, edge_type, dst_id)
);

-- Milestone 5 (KYO-7): a dispatch is a first-class object, its own dimension from
-- record status (charter, "Core schema": dispatches carry their own
-- `state`; moving the record is the controller's separate act). `state` is one of
-- contracts.DISPATCH_STATES: running -> reported (on `kyo report` ingest),
-- running -> terminated-no-report (only via `kyo dispatch close --terminated-
-- no-report --why`, law 8: terminated is its own loud state, never rendered as
-- done), or running -> withdrawn (only via `kyo dispatch close --withdrawn
-- --why`, KYO-345 at schema_version 22: a brief frozen by mistake and never
-- handed to a worker did not die mid-work, and saying it did is a failure the
-- store invented). `terminated_why` holds the controller's reason for EITHER of
-- the two report-less states from 22 on -- one column, one fact, and the word it
-- prints under derived from `state` (contracts.DISPATCH_CLOSE_REASON_LABELS).
-- `brief` is frozen at dispatch time -- never regenerated, never edited.
-- `closed_at` is orthogonal to `state` (KYO-7.c5: state alone must never read
-- like "done"; `close` records that a controller is finished looking at this
-- dispatch, whichever of the two terminal states it reached).
-- `touch_override_json` is NULL unless `--force` was used to dispatch over a
-- touch collision, in which case it names exactly what was overridden.
-- `covers_json` (KYO-13.c4 / KYO-12) is NULL for a dispatch that formally covers
-- only its own `record_id`, or a JSON array of additional record ids this
-- dispatch's brief was ALSO generated to cover -- set at dispatch creation
-- (`kyo dispatch <id> --also-covers ID [ID ...]`), frozen exactly like the brief
-- itself. `kyo report` accepts `criteria_verdicts` for any criterion belonging to
-- `record_id` or one of these -- the fix for KYO-12 ("a report cannot carry
-- verdicts for co-dispatched records"), made formal rather than informal: a
-- dispatch either declares what it covers, or it refuses to report on it.
CREATE TABLE dispatches (
    id                  TEXT PRIMARY KEY,
    record_id           TEXT NOT NULL REFERENCES records(id),
    seq                 INTEGER NOT NULL,
    state               TEXT NOT NULL,
    brief               TEXT NOT NULL,
    worked_from_sha     TEXT NOT NULL,
    -- Milestone 9 (KYO-22): where this dispatch's work happens, declared by the
    -- controller at `kyo dispatch` time (NOT sniffed from the controller's own
    -- cwd, which is usually the main checkout while the worker is elsewhere).
    -- NULL on any dispatch created before this column existed; the collision
    -- check treats NULL as "unknown" and never refuses on it.
    worktree            TEXT,
    -- KYO-32: which branch this dispatch's commits must land on, declared by the
    -- controller at `kyo dispatch --branch` and stated in the frozen brief in the
    -- same machine-filled slot style as `worktree`. NULL when the controller
    -- declared nothing, which reads as "unknown", never "any branch is fine":
    -- `kyo report` checks commit reachability only when this is set, and says so
    -- either way. A worker that invents its own branch is the failure this exists
    -- for -- `git merge` of the empty branch the controller expected reports
    -- "Already up to date" and exits 0, so the controller's own success signal is
    -- the thing that lies.
    branch              TEXT,
    -- KYO-61: which model executed this dispatch, resolved at `kyo dispatch` time
    -- from kyosei/config.yml's `worker_model`, overridden by the kind's
    -- policy.worker_model, overridden by `kyo dispatch --model`. NULL when nothing
    -- declared one anywhere, which reads as "no model was chosen", never as "the
    -- default": a dispatch object exists to say what the conditions of the work
    -- were, and inventing a value here would answer that question with a guess.
    --
    -- Deliberately NOT enforcement. Kyosei does not spawn workers -- the
    -- controller's harness does -- so no `kyo` command can make a harness honour
    -- this, and no `kyo` command claims to. `kyosei/procedures.md` states the
    -- level verbatim as `enforced-by: aspirational`, the same standing
    -- `write_scope` already has. It is also deliberately not checked against a
    -- worker's self-report: a worker reporting the condition its own dispatch
    -- declared is a second, weaker source of truth for one fact (KYO-32's lesson),
    -- and law 3 says the two then get to disagree.
    worker_model        TEXT,
    touch_override_json TEXT,
    covers_json         TEXT,
    report_id           TEXT REFERENCES reports(id),
    terminated_why      TEXT,
    closed_at           TEXT,
    -- KYO-375: a not-a-worker tick (SubagentStop's ~32s timer, KYO-163) that names
    -- this dispatch as a candidate no longer writes a row to the events ledger --
    -- it increments this JSON object in place instead: {"count", "first_seen",
    -- "last_seen"}. NULL means no tick has ever named this dispatch since the
    -- column existed (which is every dispatch's initial state; a dispatch is
    -- created with no candidate stops behind it yet). `kyo doctor`'s census and
    -- `kyo worktrees`'s per-dispatch lines read this alongside the legacy rows
    -- `STOP_TARGET_NO_TRANSCRIPT` rows written before this shipped. Deliberately
    -- NOT bumped by `_touch_record`/`_commit_and_export`'s usual mutation path --
    -- see `stop_audit.record_tick_audit` -- so a tick changes only this row and
    -- its own record's export, never `records.updated_at`.
    tick_audit_json     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (record_id, seq)
);

-- Milestone 5 (KYO-7.c3): a structured worker report, ingested by `kyo report`
-- and never retyped by the controller. `body_json` carries the full validated
-- document (per report.schema.json, which `kyo report-schema` prints) verbatim
-- -- criteria_verdicts, premise_false, disagreements, corrected_numbers,
-- commits, and the optional summary/touched_files -- so every field the
-- schema defines is stored
-- structurally and round-trips exactly, with no lossy re-flattening into
-- separate columns. `kyo report`'s own cross-checks (dispatch_id/record_id
-- agreement) run before this row is written; nothing here re-derives them.
CREATE TABLE reports (
    id              TEXT PRIMARY KEY,
    record_id       TEXT NOT NULL REFERENCES records(id),
    dispatch_id     TEXT NOT NULL REFERENCES dispatches(id),
    seq             INTEGER NOT NULL,
    worked_from_sha TEXT NOT NULL,
    body_json       TEXT NOT NULL,
    author          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (record_id, seq)
);

-- Law 6 / the shift-continuity design: "what changed since I last looked" is a
-- query over this table, not a git diff.
CREATE TABLE events (
    id          TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL UNIQUE,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    command     TEXT NOT NULL,
    record_id   TEXT,
    before_json TEXT,
    after_json  TEXT,
    -- KYO-418 (schema_version 25): the prior value of each top-level key the
    -- mutation changed (`events_ledger.event_diff`), written IN PLACE OF `before_json` --
    -- which a row written from 25 on leaves NULL. A row loaded from an older ledger
    -- line has `before_json` and no `changed_json`; the readers key on which one a row
    -- holds (`events_ledger.event_prior`), never on a version, because one ledger holds both.
    changed_json TEXT
);

-- Milestone 6 (KYO-8): `kyo sweep` (and the creation-time check, KYO-8.c4)
-- flags, never edits (charter: "flags, never auto-edits"). A flag is a child of
-- the record it primarily concerns (id shape RECORD.f7, mirrors gates/touches).
-- `other_record_id`/`other_criterion_id` are set only for the cross-record flag
-- type (`near-duplicate-criteria`); NULL otherwise. `sweep_run_id` is a soft
-- pointer (not a foreign key: sweep_runs is written in the same transaction as
-- the flags it produces, so the row exists by the time this one does, but
-- nothing else in the schema requires that ordering) naming which sweep found
-- it, or NULL for a flag this build does not attribute to a run. Resolution is
-- this build's own design for "an acknowledge/resolve path" (KYO-8 task
-- brief): `kyo flag resolve <id> [--why]` sets `resolved_at`/`resolved_by`/
-- `resolved_why` together, recording that a human judged it -- never that the
-- underlying condition was fixed by the tool, which only ever flags.
CREATE TABLE flags (
    id                 TEXT PRIMARY KEY,
    record_id          TEXT NOT NULL REFERENCES records(id),
    seq                INTEGER NOT NULL,
    flag_type          TEXT NOT NULL,
    detail             TEXT NOT NULL,
    other_record_id    TEXT REFERENCES records(id),
    other_criterion_id TEXT,
    sweep_run_id       TEXT,
    created_at         TEXT NOT NULL,
    resolved_at        TEXT,
    resolved_by        TEXT,
    resolved_why       TEXT,
    UNIQUE (record_id, seq)
);

-- Milestone 6 (KYO-8.c1): one row per `kyo sweep` run -- `base_sha` is the git
-- HEAD `kyo sweep` diffed *to* this time (NULL when run outside a git repo, in
-- which case the touch-changed flag class is skipped for that run rather than
-- crashing). The NEXT sweep reads the latest row here and diffs from its
-- `base_sha` to the new HEAD, so "since the recorded previous sweep" is this
-- table, never a hardcoded window or a diff since project start every time.
--
-- KYO-63: `base_sha` is a sha in the CODE repository, and under the split layout
-- (KYO-54, the default since) this table lives in a DIFFERENT repository whose
-- history advances independently. Nothing guarantees a later clone of the code
-- contains that sha, so the row also records `code_repo_id` -- the identity of the
-- history it diffed (root commit sha(s); NULL when it could not be established,
-- e.g. a shallow clone) -- and `coverage`/`coverage_detail`: whether the staleness
-- question was actually ANSWERED by this run, and if not, why not
-- (`sweep.SWEEP_COVERAGE_OUTCOMES`). Without those, a sweep that could not resolve
-- its own base creates no touch-changed flags and is indistinguishable from a
-- project with nothing stale -- the silent failure law 6 forbids.
CREATE TABLE sweep_runs (
    id              TEXT PRIMARY KEY,
    seq             INTEGER NOT NULL UNIQUE,
    at              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    base_sha        TEXT,
    code_repo_id    TEXT,
    coverage        TEXT NOT NULL,
    coverage_detail TEXT,
    flags_created   INTEGER NOT NULL
);

-- Milestone 6 (KYO-8.c6): one row per `kyo brief` run. "What changed since the
-- previous brief" is a query over `events` bounded by the previous row's
-- `last_event_seq` (design constraint: derived from the events table, never
-- from git) -- never re-derived from a file or a timestamp guess.
-- `last_shift_note_seq` is the same cursor idea applied to `shift_notes`
-- (KYO-8.c7: "the next kyo brief surfaces" the log written since the last one).
--
-- KYO-94: `session_start` is the store's ONLY session concept, and it exists
-- because that cursor above was a one-shot delivery. A shift note appeared in
-- exactly one brief and every later brief stamped the watermark past it, so the
-- handoff note a controller wrote for the next session was consumed by whichever
-- brief happened to run next -- including one run thirty seconds later by the
-- same session. "The most recent shift note authored before this session began"
-- needs a session boundary to compute, and a brief run is not a session: this
-- column is how the one thing that DOES know -- the `SessionStart` hook, which
-- runs `kyo brief --compact --session-start` -- tells the store.
--
--   1     this brief is where a controller with fresh context started reading
--   0     asked and answered: an ordinary mid-session brief
--   NULL  this row predates the column and was never asked
--
-- NULL is not 0 (law 6: unknown must not read as either answer) -- it never
-- anchors a session, so a store rebuilt from an older export falls back to
-- "the most recent shift note, whatever the watermark says" and SAYS that is
-- what it did, rather than silently treating ancient briefs as session starts.
CREATE TABLE brief_runs (
    id                   TEXT PRIMARY KEY,
    seq                  INTEGER NOT NULL UNIQUE,
    at                   TEXT NOT NULL,
    actor                TEXT NOT NULL,
    last_event_seq       INTEGER NOT NULL,
    last_shift_note_seq  INTEGER NOT NULL,
    session_start        INTEGER,
    -- KYO-398: the build of `kyo` that produced this brief -- its commit, that
    -- commit's UTC instant, and where the identity came from (git / stamp / unknown).
    -- NULL in all three: the row predates the columns and was never asked.
    build_commit         TEXT,
    build_commit_time    TEXT,
    build_source         TEXT,
    -- KYO-418 (schema_version 25): the Claude Code session a
    -- session-start brief opened, read from the SessionStart hook's payload.
    -- `kyo check-build --hook` compares against THIS session's opening brief, not
    -- the newest one from any session. NULL: not a hook-run session-start brief, or
    -- a row written before the column existed.
    session_id           TEXT
);

-- Milestone 6 (KYO-8.c7): `kyo note --shift` appends here continuously, during
-- the shift, never authored retrospectively at closeout (charter, "Shift
-- continuity"). Not tied to any one record: shift notes are project-wide
-- judgment ("what isn't on any record yet"), so unlike `notes` there is no
-- `record_id` and no supersede machinery -- a shift log is read in order, not
-- amended.
CREATE TABLE shift_notes (
    id           TEXT PRIMARY KEY,
    seq          INTEGER NOT NULL UNIQUE,
    text         TEXT NOT NULL,
    author       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    -- Milestone 9 (KYO-25). The consumer that filed this named the shift log
    -- specifically: it is "precisely where a controller quotes a log line while
    -- thinking out loud", which makes it the least edge-case case of the four.
    redacted_at  TEXT,
    redacted_why TEXT
);

-- KYO-137.c1: the BACK path. A document declares the code it
-- governs; a record declares the documents it affects, and the relation is called
-- `doc-refs` rather than a second `governs` on purpose: `governs` asserts
-- authority, and a record mentioning a document has none, so a different relation
-- gets a different name.
--
-- Shaped like `touches` (id/record_id/seq/path/symbol/created_at, UNIQUE per
-- record) because it IS the same kind of thing pointed the other way: a
-- root-relative path with an optional `#section` narrowing, validated against the
-- repository at write time by the same rule (`cmd_records._validate_doc_refs`,
-- which is `_validate_touches`' rule via one shared resolver). `symbol` holds the
-- section heading for a `path#section` declaration and NULL for a bare path, which
-- means "the whole document" -- the same NULL `touches.symbol` already carries.
--
-- It exists for the residual the report-time JOIN provably misses, and only that:
-- a document ABOUT a record's subject that cites none of its files. The consumer
-- report this comes from is exactly that case -- a README paragraph about a
-- record's subject, sharing no path with it, which no `governs:` target could have
-- caught. Where the join already answers, the join answers; see
-- `docs_obligations.obligations_for_changed_files` on which wins a tie and why.
CREATE TABLE doc_refs (
    id         TEXT PRIMARY KEY,
    record_id  TEXT NOT NULL REFERENCES records(id),
    seq        INTEGER NOT NULL,
    path       TEXT NOT NULL,
    symbol     TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (record_id, seq)
);

-- KYO-137.c2-c5: one document SECTION a dispatch's work put a question to. Raised
-- by `kyo report` at the moment the work is fresh in memory -- a document is updated
-- at report time, while the change is fresh -- never by a sweep, which is the next
-- session.
--
-- `section` is why this is not a per-document table (KYO-137.c3): "a README
-- with one stale paragraph is not a stale README", and one row per file makes the
-- signal mush. `cause` is one of `docs_obligations.OBLIGATION_CAUSES` -- which relation
-- raised it (`governs`, `doc-refs`, or since KYO-500 a code `pointer`) -- and `detail`
-- carries the evidence (`DocsObligation.says`) so the controller answering it does not
-- have to re-derive why it was asked. Free text, validated nowhere on export or rebuild,
-- which is why adding `pointer` moved no schema version.
--
-- The four settle columns are asserted freshness, and they are the
-- reason this is a table rather than a query. A document counts as updated when a
-- CONTROLLER RECORDS A VERDICT WITH A METHOD -- the same shape `verifications`
-- carries for a criterion (law 9: provenance on every claim) -- and explicitly NOT
-- when its mtime or its last commit date moves. `governs_staleness()`'s timestamp
-- answer remains what RAISES the question and is never what settles it; a typo fix
-- would clear a timestamp flag falsely, and a document edited in the same commit for
-- unrelated reasons would read as fresh. The accepted cost:
-- the docs answer stops being derivable from git alone. `verdict` is one of
-- `contracts.OBLIGATION_VERDICTS`; all four columns are NULL together on an open
-- obligation and set together when one is settled, so "open" is `verdict IS NULL`
-- and there is no state where a row claims to be settled by nobody.
--
-- KYO-554: `dispatch_id` is NULL on a STANDING obligation -- one a record raised when it
-- was filed or edited (its kind sets `policy.raises_docs_obligations`), on a section its
-- text names by ssot id or its doc-refs declare. A record has no branch, so nothing but a
-- verdict settles it: `kyo docs settle`, or `kyo void` on the record (`not-affected`,
-- carrying the void's reason). `kyo brief` and `kyo doctor` list the open ones.
CREATE TABLE docs_obligations (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id),
    seq         INTEGER NOT NULL,
    dispatch_id TEXT REFERENCES dispatches(id),
    doc_path    TEXT NOT NULL,
    section     TEXT NOT NULL,
    cause       TEXT NOT NULL,
    detail      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    verdict     TEXT,
    method      TEXT,
    author      TEXT,
    settled_at  TEXT,
    settled_why TEXT,
    UNIQUE (record_id, seq)
);

CREATE INDEX idx_criteria_record       ON criteria(record_id);
CREATE INDEX idx_notes_record          ON notes(record_id);
CREATE INDEX idx_gates_record          ON gates(record_id);
CREATE INDEX idx_touches_record        ON touches(record_id);
CREATE INDEX idx_evidence_record       ON evidence_artifacts(record_id);
CREATE INDEX idx_verifications_record  ON verifications(record_id);
CREATE INDEX idx_verifications_crit    ON verifications(criterion_id);
CREATE INDEX idx_claims_record         ON claims(record_id);
CREATE INDEX idx_links_src             ON links(src_id);
CREATE INDEX idx_links_dst             ON links(dst_id);
CREATE INDEX idx_events_record         ON events(record_id);
-- KYO-172: the stop-audit read (`stop_audit.stop_audits`) selects by `command` and
-- orders by `seq`, and with no index on `command` every caller scanned the whole ledger
-- to reach the ~1/3 of it that is stop rows. Three callers pay it -- `kyo doctor` (which
-- the Stop hook runs at every session end), `kyo worktrees`, and `check-clean
-- --subagent-stop`, which since KYO-169 runs once per running subagent per ~32 seconds.
-- The last one is machine-paced and scales with concurrency, and the scan was proportional
-- to the WHOLE ledger rather than to the rows it wanted -- a ledger that only ever grows,
-- and grows fastest exactly when the tick is most active.
--
-- `(command, seq)` and not `(command)` alone: the query's ORDER BY is `seq`, so the
-- two-column form serves the filter and the ordering together. Measured on a clone of this
-- repo's own 216 MB / 5,242-row ledger, the one-column form left `USE TEMP B-TREE FOR
-- ORDER BY` in the plan at 26,578 VDBE steps against this form's 12,408, for an index one
-- page wider. The record asked for an index on `command`; this is that index, with the
-- sort key it is always read with appended.
CREATE INDEX idx_events_command        ON events(command, seq);
CREATE INDEX idx_dispatches_record     ON dispatches(record_id);
CREATE INDEX idx_dispatches_state      ON dispatches(state);
CREATE INDEX idx_reports_record        ON reports(record_id);
CREATE INDEX idx_reports_dispatch      ON reports(dispatch_id);
CREATE INDEX idx_flags_record          ON flags(record_id);
CREATE INDEX idx_flags_other_record    ON flags(other_record_id);
CREATE INDEX idx_flags_resolved        ON flags(resolved_at);
CREATE INDEX idx_doc_refs_record       ON doc_refs(record_id);
CREATE INDEX idx_docs_obl_record       ON docs_obligations(record_id);
CREATE INDEX idx_docs_obl_dispatch     ON docs_obligations(dispatch_id);
CREATE INDEX idx_docs_obl_verdict      ON docs_obligations(verdict);
"""


def ddl_fingerprint() -> str:
    """A short, whitespace/comment-insensitive digest of :data:`DDL`.

    KYO-3.c4: pinned to ``SCHEMA_VERSION`` by ``tests/test_schema_compat.py`` — any
    schema change that does not also bump the version fails that test. Comments and
    incidental whitespace are stripped first so reformatting the DDL's prose (with
    no structural change) does not itself trip the pin.
    """
    without_comments = _re.sub(r"--[^\n]*", "", DDL)
    normalised = " ".join(without_comments.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

#: table -> column -> the code paths that read the column.
#: Every entry must name at least one reader; ``doctor`` enforces that.
#:
#: KYO-62 — the format, now that it is checked rather than merely written. A
#: consumer is ``module.symbol`` or ``module.Class.method``, where *module* is a
#: module of this package; a bare ``module.name`` may also name a method of any
#: class that module defines, which is how ``store.allocate`` and ``store.get_meta``
#: (methods on ``Store``) are written. ``doctor_consumers.check_column_consumers`` resolves
#: every string here against the package's own source and fails on any that names
#: nothing. Wildcards are not legal: an entry that cannot fail is not a consumer
#: declaration. Private names (``commands._commit_and_export``) are legal when the
#: private function is genuinely the reader — naming a public wrapper that merely
#: calls it would be less true, and this check is the thing that now notices when
#: such a name moves.
COLUMN_CONSUMERS: dict[str, dict[str, tuple[str, ...]]] = {
    "meta": {
        "key": ("store.get_meta", "doctor_install.check_schema_version"),
        "value": ("store.get_meta", "doctor_install.check_schema_version"),
    },
    "allocations": {
        "scope": (
            "store.allocate",
            "doctor_exports.check_allocations",
            "ledgers.write_allocations",
            "rebuild.rebuild_from_export",
        ),
        "next_value": (
            "store.allocate",
            "doctor_exports.check_allocations",
            "ledgers.write_allocations",
            "rebuild.rebuild_from_export",
        ),
    },
    "records": {
        # KYO-62: was `commands.*`, the registry's one and only wildcard. It read
        # as "every command touches a record id" — true, and unfalsifiable: nothing
        # can resolve it, so it could never fail, which makes it worth exactly as
        # much as declaring nothing. Wildcards are no longer legal (see
        # `doctor_consumers.unresolvable_consumer`, which rejects them by shape). These four
        # are what actually read the column, as opposed to every caller that passes
        # an id along.
        "id": (
            "store.record",
            "store.all_record_ids",
            "export.record_document",
            "doctor_exports.check_orphans",
        ),
        "seq": ("doctor_exports.check_allocations", "store.allocate"),
        "kind": ("contracts.check_transition", "export.record_document", "render.show_text"),
        "title": ("export.record_document", "render.show_text"),
        # KYO-101: `contracts.check_transition` still reads it, and now reads it
        # through the project's ladder; `doctor_records.check_statuses` is the enforcing
        # consumer — it fails a store whose record sits in a status the registry no
        # longer declares, which is the state a write-time check cannot see (the
        # registry is edited after the write).
        "status": (
            "contracts.check_transition",
            "export.record_document",
            "render.show_text",
            "doctor_records.check_statuses",
        ),
        "plan_id": (
            "export.record_document",
            "render.show_text",
            "doctor_records.check_plan_membership",
        ),
        # KYO-100, law 2: the field ships with its readers in the same commit, and
        # `doctor_registry.check_scope_registry` is the enforcing one — it fails a store whose
        # record names a scope the registry no longer declares, which is the state a
        # write-time-only check cannot see (the registry is edited after the write).
        "scope": (
            "export.record_document",
            "render.show_text",
            "cmd_views.cmd_list",
            "doctor_registry.check_scope_registry",
        ),
        # KYO-498: read where a carried item's age is computed -- the brief's carried
        # section and `kyo show`'s carried line -- and carried by the export document.
        "since_note": (
            "export.record_document",
            "cmd_brief._carried_records",
            "cmd_views._carried_standing",
        ),
        "description": ("export.record_document", "render.show_text"),
        "wont_do_reason": (
            "export.record_document",
            "render.show_text",
            "doctor_records.check_statuses",
        ),
        "created_at": ("export.record_document", "render.show_text"),
        "updated_at": ("export.record_document", "render.show_text"),
    },
    # KYO-58: `state` and `reason` gained a second, derived reading. A criterion
    # voided by `kyo restate` carries a canonical reason naming its replacement
    # (contracts.restate_reason), and every surface that renders a criterion reads
    # the pair through contracts.replaced_by rather than deciding for itself what a
    # void criterion means. `created_at` gained its first real consumer at the same
    # time (KYO-59): it is what `kyo ungate` compares against a gate's clearing.
    "criteria": {
        "id": ("contracts.check_transition", "export.record_document", "cmd_records.cmd_edit"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": (
            "export.record_document",
            "doctor_exports.check_allocations",
            "doctor_dispatch.check_restate_backlink_symmetry",
        ),
        "text": (
            "contracts.check_transition",
            "export.record_document",
            "render.show_text",
            "cmd_records._correct_criterion_text",
        ),
        "state": (
            "contracts.check_transition",
            "export.record_document",
            "render.show_text",
            "contracts.replaced_by",
            "cmd_links._criteria_older_than",
        ),
        "reason": (
            "export.record_document",
            "render.show_text",
            "doctor_records.check_criterion_states",
            "contracts.replaced_by",
            "doctor_dispatch.check_restate_backlink_symmetry",
        ),
        "created_at": ("export.record_document", "cmd_links._criteria_older_than"),
        "updated_at": ("export.record_document",),
    },
    "notes": {
        "id": ("export.record_document", "cmd_records.cmd_edit"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "text": ("export.record_document", "render.show_text"),
        "created_at": ("export.record_document",),
        "superseded_by": ("export.record_document", "render.show_text", "cmd_records.cmd_edit"),
        "superseded_at": ("export.record_document",),
        "redacted_at": (
            "export.record_document",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
        "redacted_why": (
            "export.record_document",
            "render.show_text",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
    },
    "gates": {
        "id": ("export.record_document", "cmd_links.cmd_ungate"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "gate_type": ("export.record_document", "render.show_text"),
        # KYO-148 adds the command that CORRECTS this field without clearing the gate,
        # and the board-wide surface that was already reading it. `render.brief_text`
        # was a consumer before and went unlisted; it is named now because the payload
        # may hold several lines, which makes how each surface renders it a fact worth
        # being able to reach from the column.
        "detail": (
            "export.record_document",
            "render.show_text",
            "render.brief_text",
            "cmd_links.cmd_redo",
        ),
        # KYO-102, law 2: the field ships with the query it exists for, in the same
        # commit. `cmd_views.cmd_list` is `kyo list --gate-tag` — the surface that
        # makes "everything waiting on a human to review a UI element" answerable —
        # and `doctor_registry.check_gate_tags` is the half a write-time check cannot see, a
        # tag that has since left kyosei/gate-tags.yml.
        "tags_json": (
            "export.record_document",
            "render.show_text",
            "cmd_views.cmd_list",
            "doctor_registry.check_gate_tags",
        ),
        "created_at": ("export.record_document",),
        "cleared_at": ("export.record_document", "render.show_text", "cmd_links.cmd_ungate"),
        "cleared_why": ("export.record_document", "render.show_text"),
    },
    # Milestone 3: `kyo promote` writes these; export/render show them. Milestone 6
    # (KYO-8) gives `path`/`symbol` their promised FULL consumer: `kyo sweep`'s
    # touch-changed (KYO-8.c1) and touch-missing (KYO-8.c2) flag classes, and
    # `kyo next`'s touch-disjointness check reads them the same way `kyo dispatch`
    # already does.
    "touches": {
        "id": ("export.record_document", "cmd_records.cmd_promote"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "path": (
            "export.record_document",
            "render.show_text",
            "cmd_records.cmd_promote",
            "sweep.changed_touch_paths",
            "sweep.missing_touch_paths",
            "cmd_next.cmd_next",
            # KYO-52: stored relative to the project root, and read back through
            # the version rule that tells a pre-9 (absolute) value from a 9+ one.
            "rebuild.stored_path_from_export",
        ),
        "symbol": (
            "export.record_document",
            "render.show_text",
            "cmd_records.cmd_promote",
            # KYO-147.c5: the disjointness check narrows an overlap where both sides
            # named disjoint symbols in the same file.
            "commands._narrow_by_symbol",
            # KYO-300: and the two readers that ask what a symbol claims about the file
            # WHILE the record is open — `kyo sweep`'s touch-changed, narrowed to the
            # symbols whose region a change actually reached, and `kyo touch-drift`'s
            # two findings inside a file the claim already covers.
            "sweep.narrow_changed_by_symbol",
            "touch_drift._symbol_drift",
        ),
        "created_at": ("export.record_document",),
    },
    # KYO-137.c1: the back path. Every column has a reader in the
    # same commit as the column (law 2) — the export, the `kyo show` rendering, and the
    # report-time join that turns a declaration into an obligation.
    "doc_refs": {
        "id": ("export.record_document", "cmd_records.cmd_edit"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "path": (
            "export.record_document",
            "render.show_text",
            "cmd_records.cmd_edit",
            "dispatch_docs._docs_obligations_for_dispatch",
        ),
        "symbol": (
            "export.record_document",
            "render.show_text",
            "cmd_records.cmd_edit",
            "dispatch_docs._docs_obligations_for_dispatch",
        ),
        "created_at": ("export.record_document",),
    },
    # KYO-137.c2-c5. `verdict`/`method`/`author`/`settled_at`/`settled_why` are
    # asserted freshness, and their consumer is the settle command that
    # writes them and the transition contract that reads `verdict IS NULL` to decide
    # whether a record may reach `done`. There is deliberately no consumer that compares
    # a timestamp to decide the same thing; that is the whole content of the decision.
    "docs_obligations": {
        "id": ("export.record_document", "render.show_text", "cmd_views._cmd_docs_settle"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        # KYO-554: NULL marks a standing obligation, which the brief and doctor list.
        "dispatch_id": (
            "export.record_document",
            "render.show_text",
            "store.open_standing_obligations",
            "doctor_records.check_open_standing_obligations",
        ),
        "doc_path": (
            "export.record_document",
            "render.show_text",
            "commands._open_docs_obligation_views",
            "cmd_views._cmd_docs_obligations",
        ),
        "section": (
            "export.record_document",
            "render.show_text",
            "commands._open_docs_obligation_views",
            "cmd_views._cmd_docs_obligations",
        ),
        "cause": ("export.record_document", "render.show_text"),
        "detail": ("export.record_document", "render.show_text"),
        "created_at": ("export.record_document",),
        "verdict": (
            "export.record_document",
            "render.show_text",
            "store.open_docs_obligations",
            "cmd_views._cmd_docs_settle",
        ),
        "method": ("export.record_document", "render.show_text", "cmd_views._cmd_docs_settle"),
        "author": ("export.record_document", "render.show_text", "cmd_views._cmd_docs_settle"),
        "settled_at": (
            "export.record_document",
            "render.show_text",
            "cmd_views._cmd_docs_settle",
        ),
        "settled_why": (
            "export.record_document",
            "render.show_text",
            "cmd_views._cmd_docs_settle",
        ),
    },
    # Milestone 4 (law 9, KYO-4.c4): raw captures, verbatim, linked to what they
    # support. `supports_id` is a soft pointer (claim/criterion/record id), checked
    # for existence by `doctor_exports.check_orphans` rather than a foreign key, since it
    # can legitimately name rows in three different tables.
    "evidence_artifacts": {
        "id": ("export.record_document", "cmd_evidence.cmd_attach", "cmd_evidence.cmd_verify"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "text": ("export.record_document", "render.show_text", "cmd_redact.cmd_redact"),
        "sha256": ("export.record_document", "render.show_text", "cmd_redact.cmd_redact"),
        "supports_id": (
            "export.record_document",
            "render.show_text",
            "doctor_exports.check_orphans",
        ),
        "author": ("export.record_document", "render.show_text"),
        "created_at": ("export.record_document",),
        "redacted_at": (
            "export.record_document",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
        "redacted_why": (
            "export.record_document",
            "render.show_text",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
    },
    # Milestone 4 (laws 3, 9, 10, KYO-4.c3/c5): a criterion's `state` is derived from
    # its latest verification's `verdict`; `doctor_records.check_criterion_verification_agreement`
    # fails the build the moment the two disagree (law 3: derive, or the validator fails).
    "verifications": {
        "id": ("export.record_document", "cmd_evidence.cmd_verify"),
        "criterion_id": (
            "export.record_document",
            "doctor_exports.check_orphans",
            "doctor_records.check_criterion_verification_agreement",
        ),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "verdict": (
            "export.record_document",
            "render.show_text",
            "doctor_records.check_criterion_verification_agreement",
        ),
        "method": ("export.record_document", "render.show_text"),
        "reason": (
            "export.record_document",
            "render.show_text",
            "doctor_records.check_criterion_states",
        ),
        "author": ("export.record_document", "render.show_text"),
        "created_at": ("export.record_document",),
        "evidence_id": (
            "export.record_document",
            "render.show_text",
            "doctor_exports.check_orphans",
        ),
    },
    # Milestone 4 (law 9, KYO-4.c1/c2): claims append-only; a reconsidered claim
    # supersedes the prior one (mirrors `notes`).
    "claims": {
        "id": ("export.record_document", "cmd_evidence.cmd_claim"),
        "record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "statement": ("export.record_document", "render.show_text"),
        "level": ("export.record_document", "render.show_text"),
        "method": ("export.record_document", "render.show_text"),
        "author": ("export.record_document", "render.show_text"),
        "created_at": ("export.record_document",),
        "supersedes": (
            "export.record_document",
            "render.show_text",
            "doctor_exports.check_orphans",
        ),
        "superseded_by": ("export.record_document", "render.show_text", "cmd_evidence.cmd_claim"),
        "superseded_at": ("export.record_document",),
        "redacted_at": (
            "export.record_document",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
        "redacted_why": (
            "export.record_document",
            "render.show_text",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
    },
    "links": {
        "id": ("export.record_document",),
        "seq": ("doctor_exports.check_allocations", "export.record_document"),
        "src_id": (
            "export.record_document",
            "links.cycle_if_added",
            "doctor_exports.check_orphans",
        ),
        "edge_type": ("export.record_document", "links.cycle_if_added"),
        "dst_id": (
            "export.record_document",
            "links.cycle_if_added",
            "doctor_exports.check_orphans",
        ),
        "created_at": ("export.record_document",),
    },
    # Milestone 5 (KYO-7): dispatch objects. `report_id`/`terminated_why`/
    # `closed_at` are the fields doctor_dispatch.check_dispatch_state_consistency reads to
    # enforce "reported implies a report row; terminated-no-report implies a why".
    "dispatches": {
        "id": ("export.record_document", "cmd_dispatch.cmd_dispatch", "report_ingest.cmd_report"),
        "record_id": (
            "export.record_document",
            "doctor_exports.check_orphans",
            "cmd_dispatch.cmd_dispatch",
        ),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "state": (
            "export.record_document",
            "render.show_text",
            "cmd_dispatch.cmd_dispatch",
            "report_ingest.cmd_report",
            "doctor_dispatch.check_dispatch_state_consistency",
        ),
        "brief": ("export.record_document", "cmd_dispatch.cmd_dispatch"),
        "worked_from_sha": (
            "export.record_document",
            "cmd_dispatch.cmd_dispatch",
            "report_ingest.cmd_report",
        ),
        # KYO-52: root-relative from schema_version 9 on. `cmd_dispatch` writes it
        # through `contracts.store_path` and compares running dispatches through
        # `contracts.resolve_stored_path`; `rebuild` reads an older, absolute value
        # back verbatim through `schema.stored_path_from_export`.
        "worktree": (
            "export.record_document",
            "cmd_dispatch.cmd_dispatch",
            "brief.generate_brief",
            "rebuild.stored_path_from_export",
        ),
        # KYO-32, law 2: the field ships with its readers in the same commit.
        # `report_ingest.cmd_report` is the enforcing one — it fails the report when a
        # reported commit is not reachable from this branch.
        "branch": (
            "export.record_document",
            "cmd_dispatch.cmd_dispatch",
            "report_ingest.cmd_report",
            "brief.generate_brief",
        ),
        # KYO-61, law 2: the field ships with its readers in the same commit, and
        # they are the only two it can honestly have. `cmd_dispatch.resolve_worker_model`
        # is what decides the value (config -> kind policy -> --model);
        # `brief.generate_brief` is what states it to the worker, which — Kyosei
        # not being what spawns workers — is the whole of what this field does.
        "worker_model": (
            "export.record_document",
            "cmd_dispatch.resolve_worker_model",
            "brief.generate_brief",
            "render.show_text",
        ),
        "touch_override_json": ("export.record_document", "cmd_dispatch.cmd_dispatch"),
        "covers_json": (
            "export.record_document",
            "cmd_dispatch.cmd_dispatch",
            "report_ingest.cmd_report",
            "doctor_dispatch.check_dispatch_covers_exist",
            "brief.generate_brief",
            "render.show_text",
        ),
        "report_id": (
            "export.record_document",
            "report_ingest.cmd_report",
            "doctor_dispatch.check_dispatch_state_consistency",
            "doctor_dispatch.check_report_dispatch_agreement",
        ),
        "terminated_why": (
            "export.record_document",
            "dispatch_close.cmd_dispatch_close",
            "doctor_dispatch.check_dispatch_state_consistency",
        ),
        "closed_at": (
            "export.record_document",
            "render.show_text",
            "dispatch_close.cmd_dispatch_close",
        ),
        # KYO-375, law 2: the counter a not-a-worker tick increments in place
        # instead of writing an events-ledger row. `record_tick_audit` is the sole
        # writer; `stop_audits` is what reads every dispatch's counter back into
        # `StopAuditLedger.tick_counters`, which `census`/`by_dispatch` (`kyo
        # doctor`'s census line, `kyo worktrees`'s per-dispatch lines) and
        # `worker_tokens_from`'s sentence (`kyo show <dispatch>`) all read from.
        "tick_audit_json": (
            "export.record_document",
            "stop_audit.record_tick_audit",
            "stop_audit.stop_audits",
        ),
        "created_at": ("export.record_document",),
        "updated_at": ("export.record_document",),
    },
    # Milestone 5 (KYO-7.c3): structured worker reports.
    "reports": {
        "id": ("export.record_document", "report_ingest.cmd_report"),
        "record_id": (
            "export.record_document",
            "doctor_exports.check_orphans",
            "doctor_dispatch.check_report_dispatch_agreement",
        ),
        "dispatch_id": (
            "export.record_document",
            "report_ingest.cmd_report",
            "doctor_dispatch.check_report_dispatch_agreement",
        ),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "worked_from_sha": ("export.record_document", "report_ingest.cmd_report"),
        # KYO-90: `amends` lives inside this blob (no new column, no schema bump),
        # so the readers of the amendment relation are consumers of this field.
        "body_json": (
            "export.record_document",
            "report_ingest.cmd_report",
            "render.show_text",
            "render.report_standings",
            "doctor_dispatch.check_report_amendment_chain",
        ),
        "author": ("export.record_document",),
        "created_at": ("export.record_document",),
    },
    "events": {
        "id": ("store.record_event", "events_ledger.append_events", "rebuild.rebuild_from_export"),
        "seq": (
            "doctor_exports.check_allocations",
            "store.allocate",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
        "at": ("store.record_event", "events_ledger.append_events", "rebuild.rebuild_from_export"),
        "actor": (
            "store.record_event",
            "doctor_dispatch.check_events",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
        "command": (
            "store.record_event",
            "doctor_dispatch.check_events",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
        "record_id": (
            "store.record_event",
            "doctor_dispatch.check_events",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
        "before_json": (
            "store.record_event",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
        "after_json": (
            "store.record_event",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
        # KYO-418: the diff a row written from schema_version 25 carries in place of a
        # whole `before_json`. Read through `events_ledger.event_prior`, and by the one SQL
        # reader of a prior value, `store.description_edited_at`.
        "changed_json": (
            "store.record_event",
            "store.description_edited_at",
            "events_ledger.event_prior",
            "events_ledger.append_events",
            "rebuild.rebuild_from_export",
        ),
    },
    # Milestone 6 (KYO-8.c1/c2/c3): sweep's own flags, and the creation-time
    # duplicate check's persisted trail (see cmd_records.cmd_new/cmd_promote, which
    # warn without storing -- the *next* `kyo sweep` is what turns the same
    # near-duplicate into a durable flags row).
    "flags": {
        "id": (
            "export.record_document",
            "commands.insert_flag",
            "cmd_flags.cmd_flag",
        ),
        "record_id": (
            "export.record_document",
            "doctor_exports.check_orphans",
            "commands.insert_flag",
        ),
        "seq": ("export.record_document", "doctor_exports.check_allocations"),
        "flag_type": (
            "export.record_document",
            "render.show_text",
            "cmd_brief.cmd_brief",
            "doctor_records.check_flag_types",
        ),
        "detail": ("export.record_document", "render.show_text", "cmd_brief.cmd_brief"),
        "other_record_id": ("export.record_document", "doctor_exports.check_orphans"),
        "other_criterion_id": ("export.record_document", "doctor_exports.check_orphans"),
        "sweep_run_id": ("export.record_document",),
        "created_at": ("export.record_document", "render.show_text"),
        "resolved_at": (
            "export.record_document",
            "render.show_text",
            "cmd_flags.cmd_flag",
            "cmd_brief.cmd_brief",
            "doctor_records.check_flag_resolution_consistency",
        ),
        "resolved_by": ("export.record_document", "cmd_flags.cmd_flag"),
        "resolved_why": ("export.record_document", "render.show_text", "cmd_flags.cmd_flag"),
    },
    # Milestone 6 (KYO-8.c1): the sweep-moment bookkeeping `kyo sweep` reads back
    # on its next run to know what to diff since.
    "sweep_runs": {
        "id": ("ledgers.append_sweep_run", "rebuild.rebuild_from_export"),
        "seq": ("doctor_exports.check_allocations", "ledgers.append_sweep_run"),
        "at": ("ledgers.append_sweep_run", "cmd_sweep.cmd_sweep"),
        "actor": ("ledgers.append_sweep_run",),
        "base_sha": ("cmd_sweep.cmd_sweep", "ledgers.append_sweep_run"),
        # KYO-63: read by the NEXT sweep to decide whether the base sha it recorded
        # belongs to the history in front of it, and rendered by both surfaces that
        # report staleness so an unanswered check can never read as a clean one.
        "code_repo_id": ("cmd_sweep.cmd_sweep", "ledgers.append_sweep_run"),
        "coverage": (
            "cmd_sweep.cmd_sweep",
            "cmd_brief.cmd_brief",
            "render.sweep_text",
            "render.brief_text",
        ),
        "coverage_detail": (
            "cmd_sweep.cmd_sweep",
            "cmd_brief.cmd_brief",
            "render.sweep_text",
            "render.brief_text",
        ),
        "flags_created": ("cmd_sweep.cmd_sweep", "ledgers.append_sweep_run"),
    },
    # Milestone 6 (KYO-8.c6): the brief-moment bookkeeping `kyo brief` reads back
    # on its next run to derive "what changed since the previous brief" from the
    # events table (never git).
    "brief_runs": {
        "id": ("ledgers.append_brief_run", "rebuild.rebuild_from_export"),
        "seq": ("doctor_exports.check_allocations", "ledgers.append_brief_run"),
        "at": ("ledgers.append_brief_run", "cmd_brief.cmd_brief"),
        "actor": ("ledgers.append_brief_run",),
        "last_event_seq": ("cmd_brief.cmd_brief", "ledgers.append_brief_run"),
        "last_shift_note_seq": ("cmd_brief.cmd_brief", "ledgers.append_brief_run"),
        # KYO-94: read by `cmd_brief._handoff_note`, which anchors "before this
        # session began" on the latest brief run that declared itself one.
        "session_start": (
            "cmd_brief.cmd_brief",
            "cmd_brief._handoff_note",
            "ledgers.append_brief_run",
            "rebuild.rebuild_from_export",
        ),
        # KYO-398: read by `cmd_brief._build_change` (the session-start notice) and
        # by `cmd_check_build.opening_brief_build` (the per-prompt check).
        "build_commit": (
            "cmd_brief._build_change",
            "cmd_check_build.opening_brief_build",
            "ledgers.append_brief_run",
            "rebuild.rebuild_from_export",
        ),
        "build_commit_time": (
            "cmd_brief._build_change",
            "cmd_check_build.opening_brief_build",
            "ledgers.append_brief_run",
            "rebuild.rebuild_from_export",
        ),
        "build_source": (
            "cmd_brief._build_change",
            "cmd_check_build.opening_brief_build",
            "ledgers.append_brief_run",
            "rebuild.rebuild_from_export",
        ),
        # KYO-418: which session's opening brief this is, read by
        # `cmd_check_build.opening_brief_build` so the per-prompt check compares against its
        # own session's opening build rather than another session's.
        "session_id": (
            "cmd_brief.cmd_brief",
            "cmd_check_build.opening_brief_build",
            "ledgers.append_brief_run",
            "rebuild.rebuild_from_export",
        ),
    },
    # Milestone 6 (KYO-8.c7): `kyo note --shift`'s continuously-written shift log.
    "shift_notes": {
        "id": ("ledgers.append_shift_note", "rebuild.rebuild_from_export"),
        "seq": ("doctor_exports.check_allocations", "ledgers.append_shift_note"),
        "text": ("ledgers.append_shift_note", "cmd_brief.cmd_brief", "render.show_text"),
        "author": ("ledgers.append_shift_note", "cmd_brief.cmd_brief"),
        "created_at": ("ledgers.append_shift_note", "cmd_brief.cmd_brief"),
        "redacted_at": (
            "ledgers.append_shift_note",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
        "redacted_why": (
            "ledgers.append_shift_note",
            "render.show_text",
            "cmd_redact.cmd_redact",
            "doctor_records.check_redactions",
        ),
    },
}

#: Keys ``kyosei/config.yml`` may carry, and what reads each one. Same law, same
#: check — a config key nobody reads is decoration and will rot.
CONFIG_CONSUMERS: dict[str, tuple[str, ...]] = {
    "schema_version": ("store.open_store", "doctor_install.check_schema_version"),
    "project_prefix": ("store.allocate", "doctor_exports.check_id_prefixes"),
    "export_dir": ("export.export_dir",),
    # KYO-60. Which layout was chosen *at `kyo init`*, as a historical fact — never
    # the answer to "where is the store now", which stays derived from the
    # filesystem by ``config.resolve_layout`` (law 3: the live answer has exactly
    # one source). What it buys is a distinction nothing else can make: a project
    # whose config carries no such key predates KYO-54 entirely, which is a third
    # state alongside the two choices and the one every project made before this
    # release. The push guard treats that state as in-repo and says which of the
    # two reasons it is looking at (KYO-60.c6).
    "layout_at_init": ("cmd_publish.publication_state",),
    # KYO-60. Who accepted that pushing this repository publishes its store, and
    # when — the record that makes the warning fire once instead of every time
    # (KYO-60.c3). A warning on every push is the KYO-33 failure: routine noise
    # trains people to stop reading it.
    "publication_acknowledged": ("cmd_publish.publication_state",),
    # KYO-61. Which model this project's dispatched workers run on by default — a
    # *project* property, not a session one: a project's tolerance for cost, and
    # its sense of which work needs a stronger model, does not change between
    # sessions, and a controller re-deciding it from scratch each session is the
    # same failure as any other unrecorded standing decision. A kind may override
    # it (``kinds.yml`` ``policy.worker_model``) and one dispatch may override both
    # (``kyo dispatch --model``). Absent in every project written before this key,
    # which is why it is optional — and absence means "nothing declared", never
    # "whatever the controller happens to be running on".
    "worker_model": ("cmd_dispatch.resolve_worker_model",),
    # KYO-143. What this project's fresh dispatch worktrees need before its own
    # tests can run there. `git worktree add` checks out tracked files and nothing
    # else, so everything gitignored is absent the moment a worker arrives — and
    # what fixes that (a venv, an npm install, a seeded database, nothing at all)
    # is a fact only the consuming project holds. Declared here so the brief can
    # state it, never sniffed from the tree and never guessed. Both consumers below
    # only *render* it: Kyosei prints these commands and runs none of them, because
    # a store command that ran a project's package manager on its own initiative
    # would be assuming exactly what this key exists to have the project declare.
    # Absent in every project written before this key, and the absence is itself
    # rendered — a worktree with nothing declared gets a brief that says what is
    # missing rather than one that silently lacks it (KYO-143.c4).
    "worktree_setup": ("brief.generate_brief", "cmd_dispatch._cmd_dispatch_create"),
    # KYO-298. Which commands this project considers the WHOLE SUITE — the ones the
    # controller runs once per wave and a dispatched worker must not run inside its
    # worktree while its dispatch is running. Declared here for the same reason
    # ``worktree_setup`` is: only the project knows whether its suite is `make test`,
    # `npm run check` or nothing at all, and a tool that guessed would be blocking
    # commands it invented. Absent (or empty) in every project that has not opted in,
    # and that absence means NOTHING IS REFUSED — the guard below is silent, and
    # `kyo doctor` has nothing to notice. Both readers are listed because they ask
    # two different questions of the one declaration: the guard asks "is the worker
    # about to run one of these, here, now", and doctor asks "does this project's own
    # `worktree_setup` tell a worker to run one" — the exact contradiction that once
    # made the prose rule lose.
    "controller_only_commands": (
        "cmd_check_command.controller_only_matches",
        "doctor_install.check_orchestrator_only_setup",
    ),
    # KYO-519 (from KYO-513): the same key under its post-rename spelling, read by the
    # same two consumers through `config._read_orchestrator_only_commands`. The old
    # spelling above stays registered permanently: a build older than KYO-519 refuses
    # this one, so no command writes it and the operator renames by hand.
    "orchestrator_only_commands": (
        "cmd_check_command.controller_only_matches",
        "doctor_install.check_orchestrator_only_setup",
    ),
    # KYO-312. The longest timeout a dispatched worker's Bash call may DECLARE inside
    # its own worktree, in seconds. Registered beside ``controller_only_commands``
    # because it is read on the same tool call by the same guard, and kept SEPARATE from
    # it because the two are different claims a project may make independently: "the
    # whole suite is the controller's" and "a wait longer than this costs more than it
    # buys". A project may declare either, both or neither.
    #
    # Declared rather than defaulted, and this key's version of that argument is the
    # sharpest of the three: ``worktree_setup`` and ``controller_only_commands`` are
    # facts only the project holds, and this one is a fact about the project's HARNESS —
    # how long a dispatched worker's prompt cache lives, which is what makes a long
    # foreground command cost a full context re-write rather than nothing at all. Claude
    # Code gives a subagent five minutes unless its operator chose otherwise; an operator
    # who chose an hour pays nothing for a twenty-minute wait, and a ceiling guessed at
    # them would be a blocking hook refusing commands that cost them nothing (KYO-33).
    # Absent means no refusal, which is every project that has not opted in.
    #
    # Two readers, asking two halves of one question: the loader that decides whether
    # this project has a ceiling at all, and the refusal that renders it — the ceiling is
    # quoted back at the worker beside the timeout it exceeded, because a refusal that
    # named no number could not be argued with.
    "worker_command_timeout_ceiling_seconds": (
        "cmd_check_command.declared_command_policy",
        "cmd_check_command.timeout_refusal_lines",
    ),
    # KYO-303. Whether `kyo doctor` may print its one NOTICE about `kyo docs suggest`
    # having something to say. Absent means yes, which is every project written before
    # this key; `docs_suggest_notice: false` means no, and that is the whole of it.
    #
    # The key exists because a suggestion that cannot be declined is not a suggestion.
    # A project that has read `kyo docs suggest`, considered the draft and decided
    # against it — its generated index really is a document it wants governed, its
    # numbered notes really are one worker's to append — has answered the question, and
    # a notice that returned on every `kyo doctor` run from a Stop hook would be the
    # KYO-33 failure exactly: routine noise trains people to stop reading it. Exactly
    # one reader, and deliberately: no `kyo doctor` PROBLEM depends on a suggestion, so
    # there is nothing else for this key to switch off.
    "docs_suggest_notice": ("doctor_registry.check_docs_suggestions",),
    # KYO-394. Whether `kyo doctor` may print its NOTICE about a kind whose
    # `accepts_from` grants less than the packaged default's. `docs_suggest_notice`'s
    # terms exactly: absent means yes, `false` is a project that narrowed a kind on
    # purpose and has said so once, rather than reading the notice on every Stop hook.
    "kind_grants_notice": ("doctor_registry.check_kind_grants_missing",),
    # KYO-556.c5. Whether `kyo doctor` may print its NOTICE about a knowledge kind whose
    # `policy` lacks a key the packaged default sets (KYO-554's `raises_docs_obligations`
    # on `decision`, the first). `kind_grants_notice`'s terms exactly.
    "kind_policy_notice": ("doctor_registry.check_kind_policy_missing",),
    # KYO-536. A wave's closing sequence, the project's half of it: `suite` (the
    # whole-suite commands whose recorded pass `kyo close-wave` requires at HEAD; absent,
    # any one of `orchestrator_only_commands` will do), `generate` (the commands that
    # regenerate this project's generated documents, run after the moves and before
    # the commit) and `after` (reinstall, restart, prune: printed, and run only with
    # `--run-after`). Parsed by its one reader, so a malformed value refuses that
    # command and never a hook.
    "close_wave": ("cmd_close_wave.read_close_wave_config",),
    # KYO-560. The setup decisions the operator DECLINED or DEFERRED, keyed by
    # their id in docs/guide/setup-decisions.md (`B2: declined`, or `D4: {status:
    # deferred, reason: ...}`). A decision answered "no" or "later" leaves nothing else
    # behind -- no remote, no `worktree_setup` -- so without this `kyo onboard --check`
    # cannot tell "never decided" from "decided no", reports both as missing with a fix,
    # and the next agent asks the operator again: the unrecorded standing decision.
    # Parsed by each reader through `config.parse_onboarding`, on `close_wave`'s
    # terms: a malformed value is refused there, naming the key, and never blinds a hook.
    # KYO-561: and `kyo brief` reads [E4] from it -- declined drops the `kyosei://`
    # citation rule and its worked example from the brief. The brief reads a malformed
    # map as no answer rather than refusing, since it runs as a session-start hook.
    "onboarding": ("cmd_onboard.answered_items", "cmd_brief.record_links_declined"),
    # KYO-410. Which branch this repository's worker branches are cut from, when git's
    # own answers (origin/HEAD, init.defaultBranch, then `main`/`master`) are wrong or
    # absent. `kyo check-branch` measures the KYO-294 cut point from it and falls back
    # to it on an unmatched stop; before this key it spelled `main` literally, so a
    # `master` repository's SubagentStop guard exited 1 -- a non-blocking hook error --
    # and never ran. Optional, and absence means "ask git", never a default name.
    "trunk": ("cmd_check_branch.resolve_trunk",),
    # KYO-416. Which code repository this store was scaffolded for: that repository's
    # root-commit sha(s), sorted and comma-joined — the same identity a sweep records
    # as `code_repo_id` (git_queries._git_history_identity). Read by `kyo init` when it
    # is about to ADOPT an existing store, so a repository is never linked to another
    # project's store because the two directories share a basename (`~/oss/api` and
    # `~/work/api` both default to `~/kyosei/api`).
    "code_repo_id": ("scaffold.init_layout",),
    # KYO-585. `recx.prior_art`: whether this project searches recx's index for
    # prior art when a record is created (`state: on | off`; absent is "not chosen"), which
    # kinds and commands trigger it (`on`, default `proposal: [new, promote, spawn]`), the
    # result `limit` and an `index_url`. Parsed by its one reader on `close_wave`'s terms,
    # so a malformed block refuses `kyo prior-art` and is named by `kyo doctor`, and never
    # blinds a hook; the doctor check also names a kind or command that does not exist.
    "recx": ("prior_art.read_prior_art_config", "prior_art.check_prior_art_config"),
}

#: Config keys a project may legitimately not carry (KYO-60).
#:
#: ``CONFIG_CONSUMERS`` is otherwise checked in both directions on every load — an
#: unknown key is decoration, a missing key is a reader with nothing to read — and
#: that symmetry is what makes law 2 mechanical rather than aspirational. Neither
#: key below can be required without breaking every project that already exists,
#: including this one: ``kyo init`` wrote three keys, and a config written last
#: week has no line for a key invented today. So they are declared optional *here*,
#: once, rather than by scattering ``.get()`` calls through the readers — absence
#: is meaningful for both of them (no layout was ever chosen; nobody has accepted
#: publication), and :func:`kyosei.config.load_config` still refuses any key that
#: is neither declared nor optional.
#:
#: KYO-143 added ``worktree_setup`` on the same terms and for a sharper version of
#: the same reason: it is not merely tolerable for a project to declare nothing
#: there, it is the case the key is designed around. A shipped default would be a
#: guess about a project Kyosei has never seen, so the default is the empty tuple
#: and the brief renders the absence.
#:
#: KYO-158 gave this set a fourth reader and, with it, a sharper job. It is now also
#: the answer to "which keys does a rewrite of ``config.yml`` carry forward" —
#: :func:`kyosei.config.write_config` iterates it instead of the hand-written
#: parameter list it used to carry. That list had fallen a key behind: `kyo rebuild`
#: rewrites ``config.yml`` on every run to keep ``schema_version`` honest (KYO-5.c1),
#: and ``worktree_setup`` had been registered above, taught to the parser and given a
#: dataclass field without being added to the one place that decides what survives a
#: rewrite — so every rebuild silently deleted it. Adding the name would have fixed
#: the instance; deriving from this set is what stops the next optional key from
#: reintroducing it, because a key becomes carried the moment it is declared optional
#: here and there is nowhere else left to remember.
#: KYO-298 adds ``controller_only_commands`` on ``worktree_setup``'s terms and for the
#: same reason, with one difference worth stating: an EMPTY list is legal there too,
#: where ``worktree_setup`` refuses one. Declaring nothing and declaring an empty list
#: are the same decision about this key — this project opts out of the command guard —
#: and the consequence of either is that a blocking hook stays silent, which is the
#: safe direction. See :func:`kyosei.config._parse_controller_only_commands`.
#: KYO-303 adds ``docs_suggest_notice`` on the same terms, and it is the first key here
#: whose absence is not merely tolerated but is the DEFAULT ANSWER rather than "nothing
#: declared": a project that has never heard of `kyo docs suggest` gets the notice, and
#: the key exists only to say no. That is why the parser refuses anything but a boolean
#: instead of normalising — a value that is neither is a project trying to say something
#: this key cannot express, and guessing which way it meant it is the failure law 6 is
#: about.
#: KYO-312 adds ``worker_command_timeout_ceiling_seconds`` on ``controller_only_commands``'s
#: terms. Optional in the strongest of the senses above: there is no default ceiling and
#: there cannot be one, because the number describes the consumer's harness rather than
#: their project, so absence is not "nothing declared yet" but the only honest answer
#: until somebody states it.
OPTIONAL_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "layout_at_init",
        "publication_acknowledged",
        "worker_model",
        "worktree_setup",
        "controller_only_commands",
        # KYO-519: both spellings, permanently -- dropping either from this set would
        # make the next `kyo rebuild` delete the declaration of a project using it.
        "orchestrator_only_commands",
        "worker_command_timeout_ceiling_seconds",
        "docs_suggest_notice",
        "kind_grants_notice",
        "kind_policy_notice",
        "close_wave",
        # KYO-560: absent in every project that recorded no declined or deferred
        # decision, which is every project written before the key.
        "onboarding",
        "trunk",
        # KYO-416: optional because every store scaffolded before it lacks it, and
        # because a code repository with no commits yet has no identity to record.
        # Absence means "cannot be verified", never "matches" (law 6).
        "code_repo_id",
        # KYO-585: absent means the project has not chosen whether to search for prior
        # art, which is a state of its own beside `state: on` and `state: off`.
        "recx",
    }
)

# --------------------------------------------------------------- KYO-3.c2: rebuild defaults

#: `touches` was added to the export format mid-version-1, without a version bump
#: (the exact defect KYO-3 fixes). It predates the version discipline
#: ``EXPORT_FIELD_DEFAULTS`` below enforces going forward, so it is defaulted
#: unconditionally rather than gated on a version number that was never
#: incremented for it — a document missing the key loads as empty touches.
UNVERSIONED_EXPORT_DEFAULTS: dict[str, object] = {"touches": []}

#: Per-version defaults for export fields a given format version did not yet write.
#: ``kyo rebuild`` applies version *N*'s defaults to any document whose own
#: ``schema_version`` is below *N* (KYO-3.c2: "a small, explicit per-version
#: defaults table, not scattered ``.get()`` calls"). Every future schema bump that
#: adds an export field gets one entry here, in the same commit — the bump itself
#: is what ``tests/test_schema_compat.py`` (KYO-3.c4) forces to happen honestly.
EXPORT_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    2: {"claims": [], "evidence_artifacts": []},  # verifications nest under criteria; see below
    3: {"dispatches": [], "reports": []},  # KYO-7: milestone 5, dispatch loop
    4: {"flags": []},  # KYO-8: milestone 6, sweep flags
    #: KYO-100: a record exported before ``records.scope`` existed declared no scope,
    #: which is exactly what ``None`` says. Never derived from the document's touches
    #: at load time: that would manufacture a declaration nobody made, and it would do
    #: it silently, on every record in the store at once (KYO-46: derivation is a
    #: suggester, never the source).
    13: {"scope": None},
    #: KYO-137: a record exported before the docs layer existed declared no doc-refs and
    #: had no obligations raised against it, which is exactly what two empty lists say.
    #: Never derived from the record's `touches` at load time — a doc-ref is a human's
    #: statement that a document is affected, and manufacturing one from a path would
    #: invent that statement silently across the whole store at once, the move KYO-100
    #: refused for `scope` and KYO-102 refused for a gate's tags.
    16: {"doc_refs": [], "docs_obligations": []},
    #: KYO-498: a record exported before ``records.since_note`` existed declared no
    #: first-recorded note, which is exactly what ``None`` says, and its carried age
    #: counts from its own ``created_at`` as it always did. Never derived from the
    #: description's prose at load time, for the reason KYO-100 refused to derive scope.
    26: {"since_note": None},
}

#: Milestone 4 also adds a field *within* each criterion (its verification history),
#: not at the document's top level — recorded separately because it is applied
#: per-criterion, not per-document, by :func:`apply_export_defaults`.
CRITERION_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    2: {"verifications": []},
}

#: Milestone 8 (KYO-13.c4): a field *within* each dispatch, the same "applied
#: per-child, not per-document" shape as ``CRITERION_FIELD_DEFAULTS`` above — a
#: dispatch exported before this field existed had no ``covers`` key at all,
#: which loads as "covers nothing beyond its own record" (the same meaning as an
#: explicit empty list), never an error.
DISPATCH_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    5: {"covers": []},
    #: KYO-22: a dispatch exported before this field existed never declared where
    #: its work happened, which is exactly the state the collision check must read
    #: as "unknown" rather than "the same place as everything else".
    6: {"worktree": None},
    #: KYO-32: a dispatch exported before this field existed never declared which
    #: branch its commits had to land on. NULL is "unknown", so `kyo report`'s
    #: reachability check stays silent on it rather than inventing a branch to
    #: check against (law 6: unknown must not read as either answer).
    8: {"branch": None},
    #: KYO-61: a dispatch exported before this field existed never declared what
    #: executed it — which is true of every dispatch written before it, all of which
    #: ran on whatever model the controller happened to be on, "not by a decision
    #: but by the absence of one". None is that absence, stated. Filling in any
    #: model name here would retroactively invent a choice nobody made.
    11: {"worker_model": None},
    #: KYO-375: a dispatch exported before this field existed never had a tick
    #: counted against it in this shape — every not-a-worker tick before this
    #: shipped wrote a row to the events ledger instead (read back through
    #: `StopAuditLedger.untranscribed`), so `None` here means exactly "no counted
    #: tick", never "unknown".
    23: {"tick_audit": None},
}

#: KYO-22: fields *within* each evidence artifact, same per-child shape. An
#: artifact exported before `kyo redact` existed was never redacted.
EVIDENCE_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    6: {"redacted_at": None, "redacted_why": None},
}

#: KYO-25: `kyo redact` reached evidence only, and a consumer pointed out that a
#: claim's `statement` and a shift note's `text` are verbatim prose by exactly the
#: same argument — the shift log being "precisely where a controller quotes a log
#: line while thinking out loud". Same per-child default shape, one version later.
CLAIM_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    7: {"redacted_at": None, "redacted_why": None},
}

NOTE_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    7: {"redacted_at": None, "redacted_why": None},
}

#: KYO-102: a field *within* each gate, the same per-child shape as
#: :data:`CRITERION_FIELD_DEFAULTS`. A gate exported before ``gates.tags_json`` existed
#: carries no ``tags`` key at all, and the empty list is exactly what was true of it:
#: the registry did not exist, so nobody classified it and nobody declined to. Never
#: derived from the gate's ``do`` payload by keyword — that would manufacture a
#: classification nobody made, silently, across the whole store at once, which is the
#: same thing KYO-100 refused to do to ``scope`` from ``touches``.
GATE_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    15: {"tags": []},
}

#: KYO-63: the same per-version defaults idea applied to a **ledger** file rather than a
#: record document — ``kyosei/export/_sweeps.jsonl``. One difference decides its shape:
#: absence of the KEY is the version signal, so the table is applied by ``setdefault``
#: rather than by comparing numbers. The version is still what the entry is keyed on,
#: because the reason for each default is that version's change and a reader has to know
#: which format is being compensated for. (KYO-25 put the one such default it needed
#: inline at the rebuild call site; this is that idea given the explicit table KYO-3.c2
#: asked for.)
#:
#: KYO-181 gave ledger lines a ``schema_version`` of their own from version 17 — which was
#: the original justification for the ``setdefault`` shape, and is deliberately NOT a
#: reason to change it. This table's job is the older direction, and a stamp cannot do it:
#: every line written before 17 carries no stamp AND no ``coverage``, so a version-number
#: comparison would have nothing to compare and would have to invent a reading of the
#: absence anyway. Keyed on the field's absence, it tolerates a stamped line and an
#: unstamped one by the same rule. The stamp is a ceiling (``rebuild.read_ledger_rows``
#: refuses a line from a LATER build); this is the floor, and they do not touch.
#:
#: ``coverage`` defaults to an explicit "unrecorded", never NULL and never "swept": a run
#: from before this bookkeeping existed genuinely cannot say whether its staleness
#: question was answered, and law 6 forbids that reading as either answer.
SWEEP_RUN_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    10: {
        "code_repo_id": None,
        "coverage": SWEEP_COVERAGE_UNRECORDED,
        "coverage_detail": None,
    },
}


#: KYO-94: the same ledger-shaped per-version table for ``kyosei/export/_briefs.jsonl``.
#: A brief run written before this column existed was never asked whether it opened a
#: session, so it loads as ``None`` — the "unrecorded" third value the DDL comment above
#: describes, never ``0``. Defaulting it to ``0`` would be a lie of the cheap kind (it
#: would read as "we asked, and it did not"); defaulting it to ``1`` would turn every
#: historical brief into a session anchor and pin the handoff to whatever note preceded
#: the most recent one of them.
BRIEF_RUN_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    12: {"session_start": None},
    # KYO-398: a run written before the build was recorded was never asked which build
    # it was, so all three load as NULL — "cannot tell", never a build, and never the
    # same build as anything.
    24: {"build_commit": None, "build_commit_time": None, "build_source": None},
    # KYO-418: a run written before the session was recorded belongs to no session a
    # payload can name, so `kyo check-build --hook` never compares against it.
    25: {"session_id": None},
}


#: KYO-418: the per-version defaults for an events-ledger LINE — the same shape as the
#: two tables above, for the one ledger whose rows are snapshots. A line written before
#: schema_version 25 carries no ``changed``; its whole prior document is in ``before``.
EVENT_FIELD_DEFAULTS: dict[int, dict[str, object]] = {
    25: {"changed": None},
}

#: KYO-418: the key a line written FROM schema_version 25 no longer carries, and what
#: its absence means. The other direction from :data:`EVENT_FIELD_DEFAULTS`, and
#: needed for the same reason: ``rebuild`` loads both formats from one ledger. An absent
#: ``before`` is no whole prior document — the line holds a ``changed`` map instead, or
#: (a creation) had no prior document at all, which older lines wrote as ``null``.
EVENT_RETIRED_FIELDS: dict[str, object] = {"before": None}


def apply_event_defaults(row: dict) -> dict:
    """Fill in the events-ledger keys a line of either format may lack (KYO-418).

    Mutates and returns ``row``. Absence of a key is the format signal, as for
    :func:`apply_brief_run_defaults`, and in both directions here: an old line lacks
    ``changed`` and a new one lacks ``before``.
    """
    for version in sorted(EVENT_FIELD_DEFAULTS):
        for field, default in EVENT_FIELD_DEFAULTS[version].items():
            row.setdefault(field, default)
    for field, default in EVENT_RETIRED_FIELDS.items():
        row.setdefault(field, default)
    return row


def apply_brief_run_defaults(row: dict) -> dict:
    """Fill in the ``_briefs.jsonl`` fields an earlier export format did not write.

    Mutates and returns ``row``. Same shape and same reasoning as
    :func:`apply_sweep_run_defaults`: absence of the key is the version signal, which
    stayed true after KYO-181 gave ledger lines a stamp — see
    :data:`SWEEP_RUN_FIELD_DEFAULTS` for why the stamp is a ceiling and this is the floor.
    """
    for version in sorted(BRIEF_RUN_FIELD_DEFAULTS):
        for field, default in BRIEF_RUN_FIELD_DEFAULTS[version].items():
            row.setdefault(field, default)
    return row


def apply_sweep_run_defaults(row: dict) -> dict:
    """Fill in the ``_sweeps.jsonl`` fields an earlier export format did not write.

    Mutates and returns ``row``. Used only by ``kyo rebuild``; a row written by this
    build always carries every key.
    """
    for version in sorted(SWEEP_RUN_FIELD_DEFAULTS):
        for field, default in SWEEP_RUN_FIELD_DEFAULTS[version].items():
            row.setdefault(field, default)
    return row


#: KYO-52: the last export format that wrote **absolute** paths into path-typed
#: columns (``dispatches.worktree``, ``touches.path``). From version 9 on they are
#: root-relative — see :func:`kyosei.contracts.store_path`.
#:
#: This is a per-version rebuild rule of a different shape from the tables above:
#: the field is not *missing* from the older format, it *means something else*
#: there, so there is no default to fill in. :func:`stored_path_from_export` is the
#: rule, and ``kyo rebuild`` applies it at every path-typed load site.
#:
#: Note what the rule may NOT do: relativise an old value. That would rewrite the
#: git-canonical export the next time its record was touched — a silent migration of
#: committed history, which is the maintainer's decision and explicitly outside this
#: record's scope. It loads the value verbatim and counts it instead.
PRE_RELATIVE_PATHS_SCHEMA_VERSION = 8


def stored_path_from_export(value: str | None, *, where: str, legacy: list[str]) -> str | None:
    """The value to load into a path-typed column from an export document.

    Always returns the value **verbatim**, and that is the rule, not an omission:
    a document at :data:`PRE_RELATIVE_PATHS_SCHEMA_VERSION` or earlier holds an
    absolute path, and rewriting it here would quietly migrate git-canonical history
    the next time that record was exported. What is already committed is the
    maintainer's decision, not a rebuild's — removing it is a git history rewrite
    (KYO-52 scopes itself to *new* paths). Consumers read both forms through
    :func:`kyosei.contracts.resolve_stored_path`, so nothing downstream needs to know
    which format a row came from.

    What the rule does produce is a count. Every absolute value it loads is appended
    to ``legacy``, so ``kyo rebuild`` can say how much machine-specific path data the
    export still carries — the question this record was found while answering
    ("what would leak if this repo were pushed public") — without touching a byte of
    it. An absolute path is inert data that still resolves, which is exactly why it
    survives silently unless something counts it out loud (law 6).
    """
    if value is not None and _os.path.isabs(value):
        # Deliberately keyed on the value's own shape, not on the document's
        # `schema_version`: once a legacy row is re-exported (any mutation of its
        # record rewrites the whole document at the current version) the stamp says 9
        # while the value is still the absolute one loaded verbatim. The value is the
        # thing that is true about itself.
        legacy.append(
            f"{where}: {value} (absolute; path-typed columns went relative at "
            f"schema_version {PRE_RELATIVE_PATHS_SCHEMA_VERSION + 1}, and values written "
            "before that are carried forward unchanged)"
        )
    return value


def apply_export_defaults(doc: dict) -> dict:
    """Fill in fields absent from an export document written by an earlier format.

    Mutates and returns ``doc``. Used only by ``kyo rebuild`` (KYO-3.c2) when
    loading a document from ``kyosei/export/`` — a live, freshly-derived document
    (``export.record_document``) is always complete and never needs this.
    """
    for field, default in UNVERSIONED_EXPORT_DEFAULTS.items():
        doc.setdefault(field, default)
    doc_version = int(doc.get("schema_version", 1))
    for version in sorted(EXPORT_FIELD_DEFAULTS):
        if doc_version < version:
            for field, default in EXPORT_FIELD_DEFAULTS[version].items():
                doc.setdefault(field, default)
    for criterion in doc.get("criteria", []):
        for version in sorted(CRITERION_FIELD_DEFAULTS):
            if doc_version < version:
                for field, default in CRITERION_FIELD_DEFAULTS[version].items():
                    criterion.setdefault(field, default)
    for dispatch in doc.get("dispatches", []):
        for version in sorted(DISPATCH_FIELD_DEFAULTS):
            if doc_version < version:
                for field, default in DISPATCH_FIELD_DEFAULTS[version].items():
                    dispatch.setdefault(field, default)
    for artifact in doc.get("evidence_artifacts", []):
        for version in sorted(EVIDENCE_FIELD_DEFAULTS):
            if doc_version < version:
                for field, default in EVIDENCE_FIELD_DEFAULTS[version].items():
                    artifact.setdefault(field, default)
    for claim in doc.get("claims", []):
        for version in sorted(CLAIM_FIELD_DEFAULTS):
            if doc_version < version:
                for field, default in CLAIM_FIELD_DEFAULTS[version].items():
                    claim.setdefault(field, default)
    for note in doc.get("notes", []):
        for version in sorted(NOTE_FIELD_DEFAULTS):
            if doc_version < version:
                for field, default in NOTE_FIELD_DEFAULTS[version].items():
                    note.setdefault(field, default)
    for gate in doc.get("gates", []):
        for version in sorted(GATE_FIELD_DEFAULTS):
            if doc_version < version:
                for field, default in GATE_FIELD_DEFAULTS[version].items():
                    gate.setdefault(field, default)
    return doc
