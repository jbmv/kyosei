# The export and its ledgers: the incidents behind the rules

The code in `src/kyosei/export.py`, `events_ledger.py`, `ledgers.py`, `ledger_scrub.py` and
`ledger_repair.py` states the rule each function enforces and why a reader would otherwise break
it. This file keeps the history those rules came from: what was measured, what broke, and what
the earlier code did. One heading per record, so a comment in the code can point at
`docs/design/export.md#kyo-<n>`. The design itself is in `docs/design/charter.md`, Layer 2.

These passages were moved out of the module docstrings and comments by KYO-429. They record
what was true when each record was worked, and they are not kept current: the numbers are
measurements of one store on one date.

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-6

`write_exports` regenerates only the records a mutation touched, so a format-version bump left
every other record's export at the format that last wrote it, and `kyo doctor`'s
`check_exports` flagged all of them. Before `refresh_exports` existed, the only repair path was a
dummy mutation per record, done by hand on KYO-1 and KYO-2 -- "noisy and wrong" in KYO-6's own
filing.

## KYO-28

`scrub_embedded_copies` was found because, in real use of `kyo redact`, a redacted value was
found to land outside the store as well -- and, it turned out, inside it too: brief generation
renders claim statements into the frozen brief, so redacting a claim left the statement verbatim
in every dispatch that had cited it, in the export, in git.

`MIN_GLOBAL_SCRUB_LENGTH`: the consumer that filed this had a candidate value *two digits long*;
replacing "42" everywhere would corrupt the store far worse than leaving it.

## KYO-66

"Verbatim" used to mean *byte-identical*, via SQLite's `instr`/`replace`. That is the narrowest
possible reading of the question and the wrong one: `reports.body_json` holds a blob
JSON-escaped, so a blob with a newline in it never matched at all, and the old fixed-string scrub
missed every multi-line blob. The match moved into Python and became wrap- and escape-tolerant,
with the exact form still tried first so nothing that was scrubbed before stopped being scrubbed.

## KYO-179

Until `document_schema_version` existed, `schema_version` in an export document was written by
every export and used as a version signal by nothing. `kyo rebuild` inferred the source format
only from absent fields, which cannot notice a document from a newer build.

## KYO-181

The ledgers had no version anywhere, so a schema bump that touched only a ledger -- which two of
this store's own bumps were, KYO-63 and KYO-94 -- produced an export in which not one record
document changed and nothing carried a stamp KYO-179's guard could read. `rebuild` read the rows
by name and dropped the later build's extra keys in silence. The per-line form of the stamp, and
why not a header line, is argued on `export.stamp_ledger_row`.

## KYO-188

Why `_allocations.json` carries no `schema_version`: the co-write argument is on
`ledgers.LEDGER_READERS`. The cost side, as it stood when the decision was made: every key in the
mapping is a scope name minted by the code (`record`, `event`, `criterion:KYO-1`), so a flat
`"schema_version"` key would put a metadata entry into a namespace of data entries and hand every
consumer that reads it as `dict[str, int]` -- `doctor.check_exports`,
`currency.db_export_currency`, and the desktop app's test fixture, which reads it as
`Record<string, number>` -- one entry that is not a counter; and a nested form would be a shape
change breaking all three at once. Paying that to duplicate a signal is the wrong trade.

## KYO-268

**Why a directory of shards.** git stores a changed tracked file as a whole new blob, so a 224 MB
append-only ledger cost 224 MB of new object on every store commit, and the periodic
`git maintenance` repack then delta-searched dozens of those copies against each other (22 GB
resident and 27.6 GB of swap, exhausting the host's memory; measured 2026-09-16).

**Why 4 MiB shards.** 4 MiB put this store's whole post-KYO-268 ledger -- 7,721 events, with the
evidence bodies removed by c1 -- in a handful of shards, and capped the per-commit cost at 4 MiB
against the 224 MB it was. It is comfortably under git's own `core.bigFileThreshold` default of
512 MiB and under the 32 MiB set as the KYO-268 stopgap, so a
shard is still delta-compressed against its own earlier versions -- the behaviour you want for a
file that grows by appending, and exactly what a 224 MB blob made ruinous. A line-count limit was
rejected because the repack that exhausted the host's memory was delta-compressing 224 MB blobs:
the cost is in bytes.

**Why evidence bodies left the snapshots (c1).** Measured on this project's own store on
2026-09-16, before the change: 55.0 MB of a 224.1 MB `_events.jsonl` was evidence bodies repeated
across 3,736 snapshots, and one record carrying 2.7 MB of evidence accounted for 22.5 MB of the
ledger on its own -- a single `move ... verified` event was 5.5 MB.

**Why the migration streams (c3).** The ledger `kyo export --migrate-events` exists for was
226 MB here and 331 MB in a larger consumer store. Written first as a list of every line, and
measured on this repository's own ledger, the peak resident set was 239 MB against 125 MB for a streaming pass over the same bytes. The
migration of this repository's 226.3 MB ledger took 2.3 seconds.

## KYO-274

Evidence was never the only body in a record document, only the first one measured. Measured on
this store's own `_events.jsonl` on 2026-09-16, by the KYO-268 worker and filed as KYO-274:
68.3 MB of frozen briefs and 49.1 MB of ingested reports in a 224.1 MB ledger -- together more
than twice the 55.0 MB of evidence KYO-268.c1 removed. Same mechanism, same argument, so the same
stripper (`events_ledger.EVENT_SNAPSHOT_BODIES`).

## KYO-404

Before `ledger_gaps`, nothing compared the ledgers with the database: `doctor.check_events` asked
only that every record had *some* events row, and `check_export_roundtrip` compared documents and
allocations. A row committed to `kyosei.db` whose ledger append was interrupted was lost at the
next rebuild with nothing to say it had existed. The tail-only id match in `ledger_repair` exists
because this project's own events ledger was 264 MB on 2026-09-25 and `kyo doctor` runs from the
session hooks.

## KYO-405

`scrub_redacted_from_events_table`: the ledger scrub rewrote the ledger's lines and left the
table those lines were written from untouched -- `events.before_json`/`after_json` kept the shift
note's text and the evidence's original sha256, so every copy of `kyosei.db` (backups included)
and every reader of the events table still carried the value the redaction reported gone.

`shards_naming`: `redaction_residue` read the whole ledger
per id, and `kyo doctor` asked it of every redacted row on every `Stop`, which on this
repository's 266 MB ledger was 0.85 s of a hook run.

## KYO-407

`atomic_write_text`: a code review measured 5,051 of 10,000 concurrent reads of an
allocations-shaped file seeing invalid JSON under a truncating write. `ATOMIC_TEMP_SUFFIX` is a
gitignore pattern because a leftover temporary is otherwise committed, as `sessions.json`'s own
temporary had already shown.

`migrate_events_to_shards` (c5/c6): its docstring used to promise that re-running an interrupted
migration was safe, and it was not. The old entry step began with `rmtree(superseded)`, which
silently lost the events a kill between the two renames had left only in `_events.superseded/`.

## KYO-418

Until schema_version 25 every mutation wrote the whole record document twice, as `before` and
`after`, so a one-field edit stored the record twice over. Measured on the last three shards of
this repository's ledger on 2026-09-25: 10,609,739 bytes as stored against 2,085,236 (19.7%) with
a top-level-key diff.

## KYO-426

`kyo redact` reached the four prose stores in `contracts.REDACTABLE` and nothing else, so the same
words quoted in a record's title, description or criteria could be NAMED after a redaction
(KYO-405's residue report) but never removed -- and a `kyo edit` of that field afterwards copied
the old value into the edit's before-snapshot, where a rerun could no longer match it.

`event_rows_naming` (c4): each residue call scanned the whole `events` table with its own `instr`
query, about 0.18 s per redacted id on this repository on 2026-09-26, in every `Stop` hook. A
substring test per id in one scan would still cost a pass over every snapshot per id (measured on
a copy of this repository's db, 282 MB of snapshots: 0.35 s for 3 ids, 2.8 s for 30); extracting
each row's ids once and intersecting took 0.21 s for 30.
