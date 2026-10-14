# KYOSEI (共生) — implementation charter for v1

**Status.** v1 is built: milestones 1–8 were built, merged and verified, with this repository
tracking its own build as records KYO-1…KYO-13. This document is the **design record**: its laws
and reasoning stay authoritative for design questions, but operational instructions live in
docs/reference/topics/operating.md ("Operating a Kyosei project"), the /kyosei skill, and each
project's kyosei/procedures.md.

Where a law here and a `procedures.md` rule cover the same ground, each is the source for a
different half of it: the law is the source for what the rule is *for*, and the rule's
`enforced-by` line is the source for how much of it is enforced today — that line is checked
against the shipped tool and a law is not. So a law may state its intent absolutely; it may not
state coverage its rule's `enforced-by` line does not claim (KYO-96).

**Origin.** This file began as the founding charter for building **Kyosei** (共生, "symbiosis") —
CLI `kyosei`, short alias `kyo` — a custom idea-tracker / task-board / documentation-governance
tool. Kyosei was designed in August 2026 from an operational log kept while running Backlog.md on
an earlier project; each law below cites the failure in that log that motivated it.

---

## What this is

Two systems, deliberately separate, one tool over both:

1. **A flexible idea-delivery system** that tracks raw thoughts through promotion into structured,
   dispatchable, verifiable work — with provenance on every claim.
2. **A rigid documentation schema** that is the single source of truth for a project's *current*
   state — never a diary. Git commits (with reasoning in extended commit messages) are the history.

The human's surfaces are Scratchpad, Ideas, and Bugs (intake). Claude promotes, dispatches,
verifies, moves, and maintains everything else. The human *views* the board; agents *drive* it.

---

## Design laws (non-negotiable, each traces to a measured failure)

1. **Mechanism over mandate.** Every rule that matters gets a check that fails; a rule without
   machinery is stored and rendered as *aspirational*, never stated in the imperative alongside
   enforced rules. (Every doc mandate in the predecessor's log decayed; every scripted check held.)
2. **No stored field without a consumer.** If no code path reads a field, it is decoration and
   will silently rot (the `governs:` field mapped 543 symbols and nothing ever read it). Ship the
   checker in the same commit as the field.
3. **Derive, don't duplicate.** If two fields must agree, the tool derives one from the other or
   the validator fails the build. Never both, never optional. (The `amended_by`/`status` footgun
   was predicted, then fired anyway.)
4. **Supersede, never delete. IDs are never reused, never positional.** Records, criteria, and
   notes keep stable IDs forever — structured (`KYO-12.c3`), never positional; a criterion's ID
   embeds its record and never moves records. Removing a criterion marks it void — either
   `void-premise-gone` or `void-withdrawn` (dropped on purpose, premise intact) — with a reason
   and date; the two are distinct so the first cannot be used as a lie. (`--remove-ac`
   renumbering silently rewrote every citation; an archived ID got reissued and edits landed on
   a ghost.)
5. **Multi-line prose enters via stdin or file, never as a shell argument. Every write echoes back
   what was stored.** The write path must make corruption visible: report the stored text (or a
   diff of intent vs. result) after every mutation. (Shell interpolation silently deleted words
   mid-sentence three times in one session and reported success.) One-line fields — a title, a
   short reason, a gate's run-instruction — may ride argv (the set is read off the shipped parser,
   never listed in prose), and there the guarantee is partial and says so: the CLI refuses an
   embedded newline, and names the residue a substitution leaves when its command printed
   *nothing* — but a substitution whose command printed output is invisible to it, an open hole
   kept named rather than closed by assertion. Everything longer is file/stdin only, the path the
   shell never touches. `procedures.md` holds the enumerated mechanics and the tests that keep
   them from drifting.
6. **Fail loudly.** An edit naming a nonexistent ID errors. An empty list means empty.
   Dependency writes refuse cycles at write time. "Success" must mean the thing happened.
7. **Transition contracts are enforced at transition time**, naming the unmet item — not by a
   validator someone runs later. (Two cards entered a contract-bearing column with unmet contracts
   in one session; the CLI accepted both.)
8. **Terminated ≠ done.** A dispatch without a report artifact is its own loud state that blocks
   cleanup. Worker output must be durable continuously, not at a clean-exit event. (A worker died
   on an API 529 with 1,019 lines of finished work unstaged; the stop hook never fired.)
9. **Provenance on every claim.** Verification level (`asserted` / `derived` / `verified`) plus
   method, instrument, author, and date. Claims keep a *history of verdicts* — a claim can be
   true, then false, then true again, and the contest is the valuable part. Inherited claims
   carry provenance forward. (A wrong number inherited a confident voice across three hops; a
   measurement without its instrument became a trap.)
10. **Criteria are states, not booleans.** `met / unmet-unbuilt / unmet-unobserved /
    void-premise-gone / void-withdrawn`, the last three requiring a reason. Verification events
    record who, when, how, and against what evidence, and a criterion keeps the *history* of those
    verdicts rather than a single current answer. Prefer re-runnable queries to frozen numbers.
    Two further parts of this law are **aspirational, not built** (law 1 — named here rather than
    stated in the imperative beside the enforced parts): a criterion over a declared set
    (5 platforms, 26 chains) should carry **per-member status**, and there should be a **lint on
    criterion text** that names a mechanism (file, script, table) where an observable outcome
    belongs. Neither exists — `criteria` has no member-set columns and `kyo sweep` has no such
    flag class — so a criterion quantifying over a set is today one state for the whole set, and
    mechanism-shaped criterion text is caught only by whoever reads it. The intent stands; the
    coverage does not yet.
11. **Dry-run by default, explicit `--apply`,** for anything that mutates data outside the store.
    (A dry run revealed a 20× blast radius on a wrong premise.)
12. **The viewer writes prose, never state.** The structured store is read-only from every viewer,
    forever. The ideas layer is writable from anywhere. (A web UI drag corrupted 18 unrelated
    cards' ordinals.)
13. **Structure appears at promotion, not at intake.** Ideas need zero ceremony; promotion is the
    explicit act that authors criteria, touches, and scope — the moment human/orchestrator judgment
    is worth spending.
14. **Keep the core small and self-checking.** Bespoke tooling fails silently in the direction of
    looking fine. `kyo doctor` runs the tool's own invariant checks; the test suite is the
    quality floor; every release runs it.
15. **Audience is a first-class property of every rule.** The reading budget is the binding
    constraint on documentation (in one predecessor project, every worker loaded 2,280 lines, 71%
    of which it could not act on). Rules in `procedures.md` carry `audience: worker | orchestrator |
    human` (`controller` is the orchestrator's token before KYO-519, read permanently beside
    it); brief generation includes only worker-audience rules; the session brief carries
    orchestrator rules. A rule that lives where its audience does not look is not a rule.
16. **Prose never restates a derivable fact — it points at the authority.** Every hand-typed
    restatement in the predecessor project drifted: a file's line count (wrong by 5× for 8 days,
    copied into three briefs), a milestone↔plan pairing table, a config value the doc claimed was
    `true` while the config said `false`, the anchor hour, the current time. Counts and pairings
    are generated or cited by pointer; all timestamps are stamped by the tool itself (UTC,
    zone-labeled on render) — an agent never types a time or a count from memory.

---

## Architecture

```
<project>/
  ideas/                  # Layer 1: mutable prose. Markdown + [[wikilinks]]. Obsidian-compatible.
    *.md                  # one idea per file, freely rewritten/forked/merged/deleted
  kyosei/
    kyosei.db              # Layer 2: SQLite, the authoritative structured store
    export/               # deterministic per-record JSON export, committed to git (reviewable diffs)
    kinds.yml             # the kind registry (see below) — validated on every load
    procedures.md         # standing rules, each tagged audience: worker|orchestrator|human (law 15);
                          # worker rules are injected into briefs, orchestrator rules into the session brief
  docs/                   # Layer 3: single-source-of-truth docs (current state only; git = history)
    PRD.md, PSD.md        # per scope level (project / app / component), per the design sketch
```

### Layer 1 — Ideas (mutable, human-owned)

governs: src/kyosei/ideas.py

Plain markdown. No schema, no required fields, no staleness tracking, never swept, never counted
in queue totals. The whole point is that recording a thought costs nothing and rewriting it costs
nothing. Git history is the only archaeology. The directory is a valid Obsidian vault by
construction (wikilinks + optional frontmatter) — the human may edit here with any editor.

**Staleness is guarded at the boundary, not in the pool:**
- `kyo promote <idea-file>` requires authoring `touches`, and validates every named file/symbol
  against the repo *at that moment*. A stale idea fails loudly exactly when someone tries to act
  on it.
- `kyo sweep` may grep idea prose for file/symbol mentions and *flag* (never enforce) broken ones.
- `kyo ideas review` lists ideas by age and broken mentions for the human's periodic purge.

### Layer 2 — Structured store (SQLite + git-committed export)

governs: src/kyosei/store.py, src/kyosei/export.py, src/kyosei/events_ledger.py, src/kyosei/ledgers.py, src/kyosei/ledger_scrub.py, src/kyosei/ledger_repair.py, src/kyosei/rebuild.py

SQLite is authoritative: real transactions, transition-time contract enforcement, central ID
allocation (no collisions under concurrent dispatch), cycle refusal, real queries. After every
write, the CLI regenerates a deterministic per-record JSON export under `kyosei/export/` so every
change remains a reviewable git diff — the one property of plain-files-in-git that demonstrably
worked and must not be lost. (This is the pattern beads validated: DB-authoritative, git-synced.) The export must be *complete*: alongside
the per-record JSON it carries the append-only event ledger (merges cleanly) and
`_allocations.json` (the ID counters). Only with those two does rebuild-from-export preserve
the audit log and law 4 — without them, a rebuilt db derives counters from `max(seq)`, which
re-enables exactly the reissued-ID incident law 4 forbids. Once
`kyo rebuild` reconstructs a byte-identical store from export and `kyo doctor` verifies the
round-trip in both directions, `kyosei.db` is gitignored in consumer projects; `kyosei/export/`
is the git-canonical serialization.

Every ledger line carries the `schema_version` of the build that wrote it (KYO-181), and
`kyo rebuild` refuses an export whose ledgers were written by a later build before it writes
anything — the same refusal KYO-179 gave for record documents, through one shared message. The form
is a **per-line key rather than a file header**, and the reason is the merge property this paragraph
already names: a header would have to be rewritten the first time the version moves, which is the
only moment it matters, and that rewrite lands on line 1 of a file two branches are both appending
to — the one line a three-way merge cannot resolve. A per-line key is written with its line and
never touched again, so two branches appending different lines still conflict on nothing. It is also
bound to the bytes it versions, which is why a document's own stamp is trusted here and
`kyosei/config.yml`'s is not. `_allocations.json` carries no version at all, and KYO-188 settled
that as a decision rather than leaving it a residue. The reason is not that the per-line form does
not fit a snapshot; it is that **the snapshot cannot be written alone**. Every function that
rewrites it also writes a stamped artifact in the same command — an event or ledger line, or a
record document — because the counters exist to record that an id was handed out, and handing one
out is a mutation. So counters written by a later build never travel without a later stamp beside
them, `kyo rebuild` refuses on that stamp before it reads a counter, and a version of its own would
witness nothing the export does not already witness — while costing a metadata key inside a
namespace whose every other entry is a scope name and a counter, read as exactly that by
`doctor.check_exports`, `currency.db_export_currency` and the desktop app's test fixture. The
co-write is the premise the argument rests on, so it is pinned by a test rather than trusted
(`tests/test_schema_compat.py`).

The event ledger is **a directory of bounded shards**, `kyosei/export/_events/NNNNNN.jsonl`, and
not the single `_events.jsonl` the paragraph above specified (KYO-268). The reason is a cost this
layer's own premise creates and nothing in it bounded: git stores a changed tracked file as a
complete new object rather than as a diff, so an append-only ledger committed beside the code costs
its *entire* size on every commit that touches the store. Measured here on 2026-09-16: a 224.1 MB
ledger, 5,783 loose objects totalling 7.23 GiB against a 219 MiB pack, and a `git maintenance`
repack delta-searching those versions against each other at 22 GB resident with system swap at
27.6 GB of 28.6 GB, enough to make the host unusable. Appends go to
the newest shard until it reaches `events_ledger.EVENTS_SHARD_MAX_BYTES`, so a mutation rewrites at most
that much and every closed shard is an object git already has. **The merge property is
unchanged**, which is what makes this a file-layout change rather than a format one: two branches
still append lines and still conflict on nothing, and they now do it in a file that is bounded.
`events_ledger.read_events_in` reads a monolithic `_events.jsonl` first and then every shard in order, so
an export written before this loads unchanged; `kyo export --migrate-events` converts one in a
single run, moving every line verbatim — same bytes, same stamp — which is the whole of why that
migration is lossless.

**A whole-file export write is a replace, never a truncate** (KYO-407). The export is read while it
is written — session hooks rewrite `_allocations.json` and record documents every few seconds, and
every `kyo list` reads the counters — so a file rewritten in place is, for the length of the write,
a prefix of itself, and a writer killed mid-write leaves that prefix for every later command to
fail on. Every whole-file writer goes through `export.atomic_write_text` (a temporary file in the
same directory, fsynced, then `os.replace`d over the target), appends and rewrites of a ledger hold
one lock (`export.ledger_lock`, which `kyo export --migrate-events` also holds), and every export
reader goes through one loader that names a file which still does not parse — its path, its line,
and the `git checkout` that restores the committed copy. `kyo rebuild --force` reads the whole
export, counters included, before it removes the database it is replacing.

**An event's `before`/`after` names the record's evidence rather than carrying it** (KYO-268): each
artifact appears as `{id, sha256}`, and the body stays where this layer already stores it once, on
the record's own export document. A snapshot is a full record document and a record document
embeds its artifacts verbatim, so every mutation of a record copied every artifact on it into the
ledger, twice — 55 MB of that 224.1 MB, across 3,736 snapshots, with one record's 2.7 MB of
evidence accounting for 22.5 MB of ledger on its own. The sha is what keeps the reference
checkable, and is also what `kyo redact` rewrites: the digest of a redacted artifact is recomputed
over the marker, so a snapshot holding the original digest would be the republication the scrub
exists to prevent.

**Evidence was the first body measured, not the only one** (KYO-274). A record document also
embeds each dispatch's frozen `brief` and each report's ingested `body`, by the same mechanism and
for the same reason, and on the same ledger those cost it 67.7 MiB and 49.2 MiB against evidence's
52.0 MiB — so the rule is about *bodies*, not about evidence. A snapshot names all three and carries
none: an artifact as `{id, sha256}`, a dispatch with `brief_sha256` in place of `brief`, a report
with `body_sha256` in place of `body`. One table (`events_ledger.EVENT_SNAPSHOT_BODIES`) drives the last
two and one function reads both, so a fourth body added to a record document is a line there or it
is a regression nothing catches. The two digests are *computed* rather than read from a column,
because neither table has ever had one; a reader re-derives either from the document it already
holds. A dispatch and a report keep every other field, where an artifact keeps only two, and the
difference is mutability rather than taste: an artifact is immutable, so the document is a complete
substitute for what the snapshot dropped, while a dispatch row moves `running` → `reported` and the
document shows only where it ended up. Reducing it to `{id, sha}` would delete the only readable
record of the transition, which is the question this ledger exists to answer (law 6).

**No existing line is rewritten by either change, and that is the append-only property, not an
omission.** Both formats therefore sit in one ledger forever, so every reader of a snapshot is
keyed on content rather than on a version stamp: `kyo redact`'s id-based scrub keys on whether the
`text` field is present, its embedded-copy scrub matches text in the file and so still finds a
brief in an old line and nothing in a new one, and `rebuild` loads a snapshot as opaque JSON.
Measured on this ledger on 2026-09-16, read-only, on a copy: 230.4 MiB across 8,250 lines in 59
shards, 113.6 MiB with briefs and reports stripped from every existing line, 59.7 MiB with all three
bodies stripped, and a largest line of 5,779,857 bytes falling to 80,927. What to do with that is a separate decision with
a cost to every clone, and this paragraph is the number, not the decision.

**From schema_version 25 an event records what changed, not the record twice** (KYO-418). Every
mutation stored the whole record document as `before` and again as `after`, so a one-field edit
cost two documents; measured on the last three shards of this ledger on 2026-09-25, 10,609,739
bytes as stored against 2,085,236 (19.7%) with a top-level-key diff. A new line carries `after`
whole and `changed` — the prior value of each top-level key whose value differs, absent and null
being one value, which is the reading every prior reader already had. `changed` holds whole
top-level values, a record's evidence, dispatch and report lists among them, so it goes through
the same body stripping as `after`. The old lines are not rewritten, for the reason above, so the
two formats sit in one ledger and every reader of a prior value asks `events_ledger.event_prior` —
`store.description_edited_at`, the changelog's completion dates, `kyo redact`'s scrubs (which now
walk `changed` as a third side, keyed to the record its `after` names) and `rebuild`, which loads
`before` and `changed` into their own columns. The same record stopped three writes that recorded
nothing. A `kyo sweep` at the base the last recorded run swept, filing and widening nothing, writes
nothing: KYO-8 logged every run "so the audit log shows a sweep happened", and the recorded run at
that base already shows it, while the Stop hook ran it on every assistant turn. A SubagentStop
whose outcome is `unresolved` — a subagent whose prompt names no dispatch, which is what a
orchestrator's Explore and research agents are — is counted in place on each running candidate
dispatch's counter, as KYO-375 did for ticks, and is still counted in `kyo doctor`'s notice while a
candidate runs; `matched` stops, helpers and an unreadable store still write rows. And a tick is
counted against running dispatches only: KYO-201 keeps a finished dispatch's surviving worktree a
candidate for *matching*, which is right, but counting against it measured how long a directory sat
unpruned and rewrote a finished record's export every ~32 seconds. A sweep's widening of a standing
flag is a flag-level event (`changed: {detail}`) that leaves the record's `updated_at` alone, and
touch-missing and description-predates-claim now keep one open flag per record the way
touch-changed has since KYO-69.

**"`kyosei.db` is gitignored" has a consequence inside a linked git worktree, and it is this
layer's to answer** (KYO-342). `git worktree add` checks out tracked files and nothing else, so
every dispatch worktree of an in-repo project carries the whole git-canonical half of this layer —
`config.yml`, `export/` — and none of the database the layer calls authoritative. Every read run
there refused and named `kyo rebuild`, which is a write under `kyosei/`: the one act a dispatched
worker is forbidden, and one that would also rewrite `config.yml` onto the worker's own branch. Two
consumer projects reported every worker of every wave reaching it. The store is now *resolved*
through git's common dir to the same relative path in the main checkout — gated on the worktree
having no database, so nothing changes where one is present — and what a read reports is that
checkout's live database rather than this branch's `export/`, which is the older of the two
whenever they differ. The rejected alternative was to rebuild the branch's own export into the
worktree: it answers with the branch's snapshot and costs the write this record exists to avoid.
**Writes stay refused** from a redirected store, and that half is the load-bearing one. It sits on
`Store.transaction` (every mutation), on `kyo rebuild` and `kyo export --refresh` (the two
file-level writers that open no `Store`), and on the connection itself, which is opened
`query_only`. Without it this layer's read-only promise would rest on a worker's restraint, because
a write from a worktree lands under whatever branch the main checkout has out — outside the
worker's own diff, which is the only thing `kyo check-branch` judges.

### The kind registry — the circles on the sketch, as configuration

governs: src/kyosei/kinds.py, src/kyosei/defaults/kinds.yml

There is **one record type**. Everything else — bug, chore, feature task, investigation,
experiment, decision, proposal, plan, evidence — is a **kind**, defined in `kyosei/kinds.yml`.
A kind declares its flows (the arrows), its policy (the rules for records in that circle), and
its archetype. Users add, drop, and modify kinds per project; the registry is validated on load;
the viewer renders it as a circles-and-arrows kind map — the hand drawing becomes a generated,
always-current diagram of the project's own configuration.

```yaml
# kyosei/kinds.yml — illustrative entries; ship these as defaults
kinds:
  bug:
    archetype: work
    summary: A defect. Criteria are the reproduction.
    accepts_from: [intake]            # filed without a promotion step (KYO-79)
    produces: [task, evidence, investigation]
    policy: { requires_criteria: true, dispatchable: true, unattended_ok: true,
              write_scope: code, swept: true, counted: true }
  investigation:
    archetype: work
    summary: Answer a question without modifying production code.
    accepts_from: [idea, bug]
    produces: [evidence, decision, task, experiment]
    policy: { requires_criteria: true, dispatchable: true, unattended_ok: true,
              write_scope: queries-only,   # consumed by brief generation — workers are TOLD this
              swept: true, counted: true }
  decision:
    archetype: knowledge                 # amendable-in-place, claim-bearing, no status flow
    summary: Shapes architecture. Cites the evidence that supports it.
    accepts_from: [intake, proposal, investigation, reminder]   # intake, reminder: KYO-521
    produces: []
    policy: { amendable: true, requires_evidence_links: true, swept: false, counted: false }
  evidence:
    archetype: knowledge
    summary: Captured observations with method and instrument. Can be marked wrong.
    accepts_from: [investigation, experiment, intake, bug]
    policy: { amendable: true, swept: true, counted: false }
```

- **`archetype: work`** — has criteria, a status flow, dispatches. Append-only notes with
  supersede markers. Work-archetype features are *toggled by policy*, not implied by the
  archetype: `plan` is work-archetype with `dispatchable: false` and `requires_criteria: false`,
  because its completion is its members' status distribution (flagged as a gap during the
  defaults build, and resolved this way rather than by inventing a third archetype).
- **`archetype: knowledge`** — has claims with verdict histories and `amended_by` machinery
  (the mechanism the predecessor's log praises in ADRs and could never apply to cards).
- **`write_scope`** (`none | queries-only | code | prod-data`) is not decoration: brief generation
  embeds it as a mechanical constraint, and `prod-data` work inherits dry-run-by-default.
- **`accepts_from` / `produces`** are the promotion/flow edges — validated at write time, on each
  of the three sources an `accepts_from` entry can name, by the one command that owns that source:
  `intake` by `kyo new` (KYO-43), `idea` by `kyo promote`, another kind by `kyo spawn` (KYO-50 —
  the `--from <record>` half of the `kyo new` sketch below, given its own verb because it
  validates a different edge and takes the *source record's* id as its subject). Until KYO-50 the
  kind-to-kind edge had no command at all, so those edges were drawn by `kyo map` and
  symmetry-checked by `kyo doctor` while staying untraversable: fields that governed a picture.
- **Policy fields make real the property table from the predecessor's log**: has criteria? dispatchable
  unattended? swept? counted against the queue? decays?
- **`carried`** (KYO-497) is an optional work-policy boolean, false when absent, and the default
  registry sets it on one shipped kind, `reminder` (named `carry` until KYO-521): a decision
  waiting on the operator, or a reminder tied to a date or an event, that the next shift must
  see (`accepts_from: [intake]`, `produces: [task, bug, investigation, proposal, decision]`, no
  criteria, not dispatchable, not swept, not counted). It is not where a defect, a settled rule
  or buildable work goes: those are a `bug`, a `decision` filed with the operator's words, and a
  `task`, and a consumer that filed them as carried items (46 open, of which about 25 were
  settled rules and 8 defects) had a backlog the kind made visible but did not cause. Its three
  consumers: `kyo brief` lists every unsettled record of a carried kind with its age (see "Shift
  continuity"); entering a `complete`-role status on one needs `--why`
  (`carried-settle-requires-reason`), kept as a record note; and settling one — `kyo move <id>
  done --why` or `kyo wont-do <id> --why` — clears its open gates in the same transaction, the
  settle's reason as each `cleared_why`, before KYO-490's `settled-requires-no-open-gates` runs,
  so the contract still holds and finds the waits answered. A wait is a carried record with an
  open gate; there is no class field, because nothing would read one. It is a key rather than a
  test for the kind's name because projects rename kinds, and KYO-521 renamed the shipped one
  without touching a line that reads the key. An older `kyo` refuses a policy key it does not
  know, so a project upgrades `kyo` before it adds `carried: true` to its registry.
- **Where a decision comes from** (KYO-521). A decision reaches the store by one of two routes,
  and which one is the decision's own shape, not a preference. **Made in conversation** — the
  operator says what they decided — it is filed directly, `kyo new decision --evidence-file
  words.md`, with the operator's words as its evidence (who, when, where, then the words
  verbatim), stored in the same act the way `kyo attach` and `kyo confirm` store them.
  **Needing design work first** — alternatives to weigh, evidence to gather, an argument to
  approve — it goes through a `proposal`, and is spawned from the approved proposal (or from an
  investigation that settled the question). A reminder that was waiting on the operator becomes
  a decision by `kyo spawn` once they answer, with their words attached the same way
  (`--evidence-file`, KYO-522). Until KYO-521 only the second route existed: the
  shipped `decision` refused `intake`, so a decision made in conversation had no record to go
  to, and one consumer had the `decision` kind and zero decision records while its decisions sat
  in its carried list. Opening `intake` keeps the archetype's promise because
  `requires_evidence_links`, which until then had no consumer, now has one: `kyo new` refuses a
  kind that sets it unless `--evidence-file` attaches the evidence in the same act
  (`filing-requires-evidence`), so a decision filed directly is never an unsupported assertion.
  `kyo spawn` asks for none; its `produced-by` link names the record the decision came from.

### Status and gates are orthogonal dimensions

governs: src/kyosei/contracts.py, src/kyosei/defaults/statuses.yml, src/kyosei/defaults/gate-tags.yml

Statuses (work archetype): `open → working → built → verified → done`, plus terminal **`wont-do`**
(retires the ID permanently; distinct from done; for dead premises and supersession).
`built` exists because "finished but not verified live" was the most common state the old board
could not express. Transitions may move backward and may skip forward, but every entry's
contract holds regardless of path — and entering `done` on a `requires_criteria` kind demands
at least one `met` criterion with none unmet: "all criteria met" is vacuously true at zero
criteria, and a record whose criteria are all void has established nothing and belongs in `wont-do`. Entering `verified` demands a recorded
verdict, of any value, on every live criterion (`verified-requires-a-verdict`, KYO-546): the
role means "a verdict is recorded", and a consumer's record reached it with none after the
`kyo accept` that would have recorded them had been refused.

`plan` sets `requires_criteria: false` (its completion is a status distribution over its
members, not a criteria set — see "Core schema" below), so neither of those contracts ever
fires for it. `kyo move <plan> <a completion status>` used to succeed over a plan whose
members were still open, printing only the plan's own transition; `complete-requires-
settled-plan-members` (KYO-308) closes that gap, refusing by name — settle each member, or
detach it with `kyo edit --no-plan` — unless `--force` is passed, which moves the plan and
records that it was closed over open members.

**Gates** are a separate dimension — a record can be `working` *and* gated. Gate types:
- `awaiting:event <condition>` — parked on an external event (deploy cycle, CI run)
- `awaiting:human-run <the exact thing to run>` — "run this and paste what happens"; the
  instruction lives ON the record, not in chat
- `awaiting:human-review` — "read this and judge it"
- `holding-for:<observation>` — ready, but deliberately held while an in-flight measurement
  completes (the state that previously lived only in the orchestrator's head)

`awaiting:human-run` and `awaiting:human-review` are different asks and are never flattened.

**Settling is the exception (KYO-490).** Gates stay orthogonal to every move except one
into a settling status — completion or retirement — where an open gate means the record is
not finished: it is still waiting on something, and a settled record carrying the gate
says both at once while every brief goes on listing it (KYO-380 reached that state by
`kyo wont-do`). So `settled-requires-no-open-gates` refuses `kyo move` into either role,
`kyo wont-do`, `kyo void`, and `kyo accept --close`'s walk, naming each gate and the two
ways out: clear it with `kyo ungate <gate-id> --why` once what it waits on has happened,
or pass `--force` to settle with the gate left open, which the command reports.

### Core schema (SQLite)

governs: src/kyosei/schema.py

- **records**: id (project-prefixed, monotonic, never reused), kind, title, status, plan
  membership, created/updated, body sections (description, notes). **There is no separate
  milestone entity**: a plan is itself a record (kind `plan`) that owns its member records — the
  Backlog.md milestone↔plan back-pointer table and its drift cease to exist. Plan completion is
  reported as a status *distribution* (open / built / verified / done / wont-do), never a single
  Done ratio — "how much is closed" and "how much is built" are different questions and the old
  single number conflated them. A record of a `carried` kind may also name `since_note`: the id
  of the shift note that first recorded the item, so an item a project carried before it filed
  the record keeps its true age (KYO-498). Only the id is stored; the age reads that note's
  tool-stamped `created_at` (law 16), and there is no foreign key because rebuild loads records
  before the shift-note ledger, so `kyo new`/`kyo edit` check it at write time.
- **criteria**: stable structured id, record_id, text, state, reason (required for
  void/unobserved states). Specified alongside those and **not built** — the same aspirational
  half law 10 names: an optional member-set with per-member states, and an optional query (the
  re-runnable form of the check). The shipped table carries neither, so nothing may be written
  or read against them.
- **verifications**: criterion_id, verdict, method (`machine-checked | human-observed |
  reasoned-about`), author, date, evidence_id.
- **claims**: statement, level (`asserted|derived|verified`), instrument/method, author, date,
  verdict history (append-only), supersedes pointer, attached to any record.
- **evidence_artifacts**: raw captures (request dumps, console excerpts, query outputs, pasted
  human observations) stored verbatim, linked to the claims/criteria they support. The refutation
  of a dead approach is evidence too — a first-class **closed door**, searchable, so the next
  person with the same idea finds the measurement that killed it.
- **touches**: record_id → files, symbols, shared resources (e.g. `dev-postgres`,
  `browser-required`). Consumed by: dispatch disjointness checks, sweep, brief generation.
- **doc_refs**: record_id -> the Layer 3 documents this record's work affects (`path`,
  optional `#section`). The back path to a document's `governs:`, named `doc-refs` rather than a
  second `governs` because a record mentioning a document asserts no authority over it (KYO-136
  decision 1). Consumed by: the report-time docs join, `kyo show`.
- **docs_obligations**: one document SECTION a dispatch's work put a question to — record_id,
  dispatch_id, doc_path, section, cause, plus the verdict/method/author that settle it. Raised by
  `kyo report`, settled only by `kyo docs settle`, and blocking on the way to the `complete` role.
  A *standing* obligation (KYO-554) has a null dispatch_id: a record raised it when it was filed
  or edited, and `kyo void` on that record settles it `not-affected`.
  A settled row is an assertion with provenance (law 9), which is what makes it different from a
  timestamp; an open one is `verdict IS NULL`, so there is no state where a row claims to have been
  settled by nobody.
- **links** (typed edges): `blocks`, `duplicates`, `supersedes`, `same-question-as`,
  `needs-same-change-as`, `depends-on-decision <record#claim>`, `produced-by`. Cycle-checked
  at write time.
- **dispatches**: first-class object — record_id, generated brief (frozen copy), amendments
  (each re-brief is recorded), worker identity, state (`running | reported |
  terminated-no-report | withdrawn`), report_id, worked_from_sha. The fourth state is
  KYO-345's, and it is law 8 read precisely rather than loosened: a dispatch with no report
  artifact is still its own state that blocks cleanup, but a brief frozen by mistake and never
  handed to a worker did not die mid-work, and recording it as a death is a failure the store
  invented. `terminated-no-report` keeps meaning the work *stopped* — which is what makes
  flagging one in a brief worth doing — while `withdrawn` is terminal, carries the same
  required reason, and is loud nowhere.
- **reports**: structured worker output — per-criterion verdicts with method + evidence,
  `premise_false[]` (a premise in the brief was wrong — first-class, reviewed first),
  `disagreements[]` (worker contradicted the brief/orchestrator, with rationale),
  `corrected_numbers[]`, commits produced. Ingested by `kyo report`, not retyped by the
  orchestrator. The orchestrator annotates and promotes; it is not the courier.
- **events**: append-only audit log of every mutation — actor (human / orchestrator / worker /
  hook), command, `after`, and the prior value of each key that changed (`changed`, from
  schema_version 25; a whole `before` on older lines, KYO-418). "What changed since I last
  looked" is a query, not a git diff.

---

## CLI surface (v1)

governs: src/kyosei/cli.py

One command, JSON output everywhere (`--json`), exit codes with fixed meanings (0 ok; 1 runtime
error / unknown ID; 2 contract violation; 3 doctor failure; 64 usage — argparse's own 2 is
overridden so 2 means exactly one thing), prose via stdin/file (one-line fields per law 5).

```
kyo init                          # scaffold dirs, db, default kinds.yml, hooks templates
kyo new <kind> --title ... [--from <idea|record>] [--evidence-file f]   # evidence stored in the
                                  #   same act; required where policy.requires_evidence_links (KYO-521)
kyo show <id> [--json]
kyo edit <id> --notes-file f.md   # or stdin; echoes stored result; supersede-marks prior notes
kyo promote <idea-file> --kind <k>   # validates flows + touches against repo; authors criteria
kyo move <id> <status>            # enforces transition contracts AT the transition, names unmet items
kyo gate <id> awaiting:human-run --do "run the migration on staging, then reload the dashboard"
kyo ungate <id> [--why]
kyo wont-do <id> --why            # terminal; ID retired forever
kyo split <id> --criteria c3,c7   # met criteria close with evidence; rest leave with history; back-pointers both ways
kyo link <id> <edge-type> <id>    # refuses cycles
kyo claim <id> --level derived --method "raw SQL: ..." --file claim.md
kyo verify <criterion-id> --verdict met --method human-observed --evidence-file paste.txt
kyo attach <id> --file dump.har --supports <claim-id>
kyo sessions [--hook] [--json]    # session hooks record this project's Claude Code sessions,
                                  #   and where each is on claude.ai, into gitignored local state
kyo dispatch <id>                 # checks premises (shows claims + levels), checks touch-disjointness
                                    # vs running dispatches, generates brief, freezes it
kyo report <dispatch-id> --file .kyosei-report.json   # structured ingest; absence of report =
                                  #   loud state. KYO-206: one named path, gitignored, so a
                                  #   worker commits its work without committing its report;
                                  #   `--check` runs the same checks and writes nothing (KYO-477)
kyo note --shift                  # append to the current shift log (stdin/file; continuous, never at closeout)
kyo next                          # ready work: dispatchable, ungated, disjoint from running work
                                    # (undeclared touches = its own counted class, never "disjoint")
kyo brief [--compact]             # session briefing: counts (actionable kinds only), ready list,
                                    # gated list w/ conditions, stale flags, dispatches missing reports,
                                    # what changed since last brief
kyo sweep                         # see below
kyo ideas review                  # ideas by age + broken mentions, for human purge
kyo map                           # render kind registry as the circles-and-arrows diagram
kyo html                          # regenerate the static viewer  [RETIRED 2026-09-07, KYO-215]
kyo doctor                        # the tool's own invariant checks
```

`kyo html` is the one line of this surface that no longer exists. It is left in the listing
rather than deleted, per this file's own status note — the v1 specification is the record, and
the difference between what was specified and what stands is the thing worth keeping. The
decision is dated and reasoned under **v1 scope and build order**, milestone 7.

**Brief generation** (from the record, not hand-assembled): criteria with current states, touches,
cited docs and claims *with their verification levels*, `procedures.md` standing constraints, the
kind's `write_scope` as an explicit capability statement, base SHA — plus the standing stance that
earned its keep: **"flag conflicts, do not resolve them; contradicting this brief must be cheaper
than complying with it."** The orchestrator writes only the genuinely task-specific part.

**Sweep** (flags, never auto-edits): git diff since last sweep × all `touches` → possibly-stale
records; records in `open` whose declared artifacts already exist in the repo → moved-but-inverse
flag; records in shipping states whose commits are absent from main → moved-but-not-merged flag;
claims older than N days with level `asserted` → re-verify prompts; `governs:`-bearing doc
sections whose symbols changed → doc-stale flags; near-duplicate criterion text across records →
same-question flags. Run `kyo sweep` on record *creation* too, against the existing corpus —
creation is when overlap is cheapest to catch.

---

## Shift continuity — the handoff ritual is retired, not the intent it carried

governs: src/kyosei/brief.py, src/kyosei/cmd_dispatch.py

The predecessor project ended every orchestrator shift with a closeout and a hand-written
handoff document whose readers were told to check its date and trust the board over it when it
was old — an admission that handoffs decay, and operational experience showed they also
*propagate*: two of the worst wrong-claim incidents entered through handoff prose. The design
replaces both halves with mechanisms:

- **The state half of a handoff is derived, not written.** The store is transactional and
  continuously current; the events log answers "what changed since the last session" as a query.
  `kyo brief` opens the next shift with: deltas since last brief, in-flight dispatches (and any
  `terminated-no-report`), gated work with conditions, stale flags, and ready work. A crash at
  any moment loses nothing that was ever recorded — there is no end-of-shift moment where state
  gets written down.
- **What a shift carries forward is records, not restated prose** (KYO-497). Standing decisions,
  open questions and reminders were re-typed into every closing note with hand-kept day counts
  that drifted (one consumer project had about 46 such items, half of each closing note). Each is a
  record of a `policy.carried` kind (the default `reminder`), and `kyo brief` lists every unsettled
  one, oldest first, with its age in whole days computed from the record's tool-stamped
  `created_at` — never typed, never stored — and any open gate with its own age. Settling one is
  one command with a reason, which clears its gates. The shift log keeps only what no record can
  hold.
- **The intent half survives as a shift log, written continuously.** "What I suspect", "pick up X
  before Y and here's why", "what isn't on any record yet" is genuine judgment no store can
  derive. `kyo note --shift` appends to the current shift's log *during* the shift, at the
  moment of the thought — never authored retrospectively at closeout, so a crash loses at most
  the last unrecorded thought. Assertions in shift notes are claims and carry provenance levels
  like everything else; the next brief opens with the previous shift log, dated.
- **Closeout is a hook, not a ritual.** `SessionEnd`/`Stop` hooks run `kyo doctor` + `kyo
  sweep` + report-completeness checks. Nothing about ending a shift is remembered.
- **Worktree cleanup refuses to destroy work.** `kyo dispatch close` fails loudly if the
  worktree holds uncommitted changes or the dispatch has no ingested report — the check the old
  "Ending a shift" checklist performed from memory, with `--force` silently discarding. KYO-207
  adds the second reading of "work": `kyo worktrees --prune` also leaves a finished dispatch's
  worktree whose **branch is not contained in the base**, because a dispatch reports the instant
  its worker stops and that says nothing about whether anybody has merged or rehearsed what it
  wrote — the directory is what a rehearsal runs in. `--include-unmerged` removes it anyway.
  KYO-402 narrows both readings to what git itself knows: the containment question is asked
  of the worktree's **HEAD**, not the declared branch (a worker that committed on a detached
  HEAD never moves its branch), and a prune removes only directories `git worktree list`
  names as linked worktrees of the code repository, each with `git worktree remove` — a
  recorded path that is not one (a separate clone, a subdirectory of the checkout, a store
  root) is reported and never removed, whatever the flags.

**Worktrees and the store**: the authoritative `kyosei/kyosei.db` is the main checkout's. Workers
never write to `kyosei/` (no write path by design), and `kyo doctor` plus a merge guard verify
that no worker branch diffs `kyosei/` — cross-branch reads in an earlier setup made every
worktree a phantom duplicate of the whole board. What the guard judges is the
commits the **branch added**, past the point it was cut from the trunk, and not everything that
differs from the sha its brief recorded: `kyo dispatch` stamps that sha and the orchestrator *then*
commits the store, so the ordinary worktree starts one or more store-only commits ahead of it and
would otherwise be refused at its own stop for a diff the orchestrator wrote (KYO-294). A store
commit the worker makes itself is refused exactly as before — it is a descendant of the cut point
either way — and the worker-facing brief states the same rule in the same words, so no worker is
told a state is normal by one surface and blocked for it by the other.

## Layer 3 — Documentation SOT

governs: src/kyosei/docs_check.py, src/kyosei/docs_numbered.py, src/kyosei/docs_obligations.py, src/kyosei/docs_parse.py, src/kyosei/docs_regions.py, src/kyosei/docs_staleness.py, src/kyosei/docs_suggest.py, src/kyosei/docs_targets.py

- `docs/` states only what is **currently true**. History and reasoning live in extended git
  commit messages. Never a diary.
- Sections carry machine-readable metadata: `governs:` (code symbols/files) and status
  (`current | amended-by <sec>`). What this section is *for* is unchanged and absolute: the
  validator consumes every field (law 2), so a doc cannot claim to govern code that has moved and
  cannot claim to be current while pointing at its own amendment (law 3: derive or fail).
- **Both directions, and the back path is not called `governs`** (KYO-136 decision 1). A document
  declares the code it governs; a record declares the documents its work affects, under a relation
  named **`doc-refs`** (`kyo edit --doc-refs-file`, `path` or `path#section`, validated against the
  repository exactly as `touches` is). The separate word is the point: `governs` asserts authority,
  and a record mentioning a document has none. `doc-refs` exists for the residual the report-time
  join provably misses — a document *about* a record's subject that cites none of its files — and
  the join answers everything else.
- **How much of that is enforced today**, stated in the register law 1 demands rather than in the
  imperative:
  - *Enforced.* A `governs:` target that no longer exists fails the build — one target per
    comma-separated entry, resolved exactly like a record's `touches` (`src/kyosei/docs_check.py`,
    surfaced by `kyo docs check`). So does a `governs:` that names no target at all (KYO-412): a
    claim the parser could not read is reported, never passed as a section governing nothing.
  - *Enforced.* The `amended-by`/`status` cross-check (KYO-499), in two tiers. **In every
    document of every project**, the pair agrees: an `amended-by` (either spelling; `[]` names
    nothing) needs status `amended` or `superseded`, a `superseded-by` needs a dead status, and
    status `amended` or `superseded` needs its link — refused as `section-link-without-status` or
    `section-status-without-link`, naming the section and the remedy. **In a document set that
    declares `section_status:`** in `documents.yml` (`statuses`, required; `pointer_obligations`,
    default true; `index`, optional, with `index_lists`, `index_depth` and `index_fields` saying
    what the generated index lists (KYO-512, KYO-520) — any other key refused by name when the file is read), four
    more: a status outside the set's `statuses` (`section-status-unknown`); a link that names no
    heading (`section-link-unresolved` — a bare key resolves in the same document only, another
    document is named `path.md#anchor`) or names its own section (`section-link-to-itself`); a
    linked section whose prose never mentions the linking one (`section-link-not-cited-back` —
    the forward direction is derived from that prose, so no `amends:` key exists to disagree
    with); and a `src/` pointer to a superseded section whose comment block does not name the
    successor (`pointer-to-superseded-section`). `kyo docs check` runs all of them and fails on
    any; `kyo check-branch --subagent-stop` runs them on the documents and `src/` files a
    worker's branch changed and blocks with the same refusals. What none of them can catch is a
    section nobody marked: they keep marks honest, they do not make them.
  - *Read.* Since KYO-109 both fields are **read**: `kyosei.docs_parse.section_metadata` returns a
    section's `governs:`, `status:` and `amended-by:`, and a Base over the `docs` source selects on
    them (`kyo base run <name>`). That closed the law-2 half of the gap — `status:` stopped being a
    field with no consumer — and the cross-check above closed the validation half.
  - *Read, not checked.* Whether a document is still current with the code it governs. The `docs`
    source answers it per file (`staleness`, one of `current | stale | ungoverned | cannot-tell`,
    with the evidence in `staleness_says`), using git dates only — a document git cannot date reads
    `cannot-tell` rather than `current`, because on a fresh clone an mtime comparison would report
    the whole tree current. Nothing fails a build on it; the `touches × governs` banner KYO-45's
    amendment describes is the rendering this data exists for, and it is not built either.
  - *Enforced, at report time.* A **docs obligation** (KYO-137, from the seven decisions on
    KYO-136). `kyo report` intersects what the dispatch's branch actually changed (KYO-71) with
    every `governs:` section's targets, and raises one obligation **per section** — a README with
    one stale paragraph is not a stale README. An obligation is settled only by
    `kyo docs settle <id> --verdict updated|not-affected --method <how>`: a document counts as
    updated when an orchestrator asserts it with a method, never when its timestamp moves, so the docs
    answer is deliberately no longer derivable from git alone. **Who does the reading moved once**
    (KYO-199): the *worker* proposes a verdict per section in its report's `docs_verdicts[]`,
    having seen the same join read-only from its own worktree (`kyo docs preview`), and the
    orchestrator applies the proposals with `kyo docs settle --from-report <dispatch-id>`. The
    settling act, its author and its refusals are unchanged — what moved is where the reading
    happens, to the party who still has the diff and the document open. Two consumer stores
    measured 2026-09-05 say why: `not-affected` outnumbered `updated` roughly nine to one in one
    and two to one in the other, and one dispatch there raised sixty sections, which is a reading
    nobody performs. While one is open, the record cannot
    reach the `complete` role (`complete-requires-docs-obligations-settled`) — it can still be
    retired, because a record that ships no code makes no document stale. Nothing under `ideas/`
    raises one, and neither `cannot-tell` nor `ungoverned` ever does: a project that declared
    nothing is not thereby stale (KYO-33 measured what routine false positives cost). **The
    relation is also read from the code's side** (KYO-500): in a set whose
    `section_status.pointer_obligations` is on, a `<doc path>#<anchor>` pointer in a changed `src/`
    file raises an obligation on the section it names, cause `pointer`, when the branch changed
    the pointer's region — the innermost `def`/`class`, or else the module-level statement the
    comment precedes. A region whose text is the same on both sides raised nothing; a pointer
    no region finder can place (Python is the one language read) raises on any change to its
    file and is reported as raised at file granularity. It settles, and blocks `complete`, as
    every other obligation does. **A decision raises one too, and it stands** (KYO-554): in an
    `ssot:` set the decision record is the history and the section is the current state, so a
    record whose kind sets `policy.raises_docs_obligations` (the default `decision`) raises a
    *standing* obligation, with no dispatch, when it is filed (`kyo new`, `kyo spawn`, `kyo
    promote`) or its title, description or doc-refs are edited — one per section that a bare id
    in its title or description resolves to (cause `decision`), and one per `doc-refs` entry. An
    id with a declared prefix and no owner raises nothing and the write names it; a section the
    record already held one on, open or settled, is not raised again. A record has no branch, so
    `kyo brief` lists every open standing obligation under its own heading with its age, `kyo
    show` lists the record's, and `kyo doctor` counts them as a notice. They settle as every
    other obligation does, by `kyo docs settle`, and `kyo void` on the record settles its open
    ones `not-affected` with the void's reason. A record filed before a build that knew the
    policy holds none until it is next written. **A changed file whose diff moved no code token raises
    nothing** (KYO-530), through either relation: the language's own lexer reads both sides,
    and only comments, blank lines and docstrings differ (Python and YAML are read; any other
    file, or a side that will not lex, raises as before). `kyo docs preview` lists what this
    quieted (docs/design/docs.md#kyo-530).
  - *Enforced, on the branch and on the merge.* **Doc fragments** (KYO-299), for the one shape of
    document Layer 3 could not protect at all: append-only, numbered sections, an index generated
    from it. Everything above is about whether a document is *current*; this is about whether two
    workers can *extend* one in a single wave, and they cannot — they collide on the number, on
    the tail of the file and on every generated index, however their touches were declared,
    because a touch narrows a claim and does not move a byte. A document set declares itself
    `numbered:` (a fragments directory, a heading template carrying `{number}`, and the project's
    own follow-up commands); each worker writes each new section to its own file under that
    directory (one section per fragment, KYO-486), with `{{next}}` where the number goes; `kyo check-branch` refuses a branch
    that adds a numbered heading to the document itself and names the fragment path instead; and
    `kyo docs assemble` appends every fragment on the merged tree with the next number, refusing
    the whole run rather than assembling half of one. **Editing an existing section is untouched
    by all of it** — an ordinary edit, serialized by a `path#section heading` touch. Two
    boundaries are deliberate and are the boundary `worktree_setup` already sits at: Kyosei does
    not own the numbering format (the project's template does) and does not generate the index —
    it NAMES the project's command and, as everywhere else, runs none of it.
  - *Enforced, at assembly.* **A fragment's amendments** (KYO-493), the Layer 3 pair above
    written by the one party that knows the new number. A set opts in with
    `numbered.amends: {field, status}` — `field` one of `amended-by` / `amended_by`, `status`
    a non-empty value outside `DEAD_STATUSES`, both required, refused by name when
    `documents.yml` is read — and a fragment names the sections it amends on its header,
    `<!-- kyosei-fragment amends: 178, 186 -->`. `kyo docs assemble` writes the new number onto
    each section's back-link and the declared status onto its `status:` line in the same
    all-or-nothing plan, so the pair is derived together rather than kept in agreement by hand
    (law 3). A dead section gains the back-link and keeps its status. The refusals, each voiding
    the run: `fragment-header-unreadable`, `amends-undeclared`, `amends-unknown-section`,
    `amends-unassembled-section`, `amends-ambiguous-section`, `amends-no-status-line`,
    `amends-unreadable-back-link`; `kyo check-branch` runs all but `amends-unassembled-section`
    on the worker's branch. This writes the pair; the cross-check above is what holds every
    section of every document, written this way or by hand, to agree.
  - *Enforced, on the branch and in `kyo docs check`.* **Ids with one owner** (KYO-552, slice 1
    of KYO-551), for a set of current-state documents whose requirements carry ids. A set declares
    `ssot:` (`id_heading`, a template carrying `{id}` and `{title}`; `id_pattern`; `prefixes`, each
    id prefix mapped to the one document that owns it; `tombstone_max_lines`, default 3), never
    beside `numbered:` and only beside a `section_status:` whose statuses include `superseded`.
    The three id keys are optional together (KYO-553): a current-state set whose sections carry
    no ids declares none of them (`ssot: {}`), owns no ids, has nothing in it read as one, and is
    checked for `ssot-tombstone-body` only; a block declaring some but not all is refused.
    The owner of an id is the section whose heading carries it, derived and never declared
    (law 3). Only a token whose prefix is declared is an id, so `UTF-8` and a record id are not.
    Ids resolve across every ssot document, in `superseded-by:`/`amended-by:` (the cross-check
    above runs through that resolution) and as bare ids in prose. The refusals:
    `ssot-prefix-undeclared`, `ssot-id-wrong-document`, `ssot-id-duplicate`, `ssot-id-unresolved`,
    `ssot-cites-tombstone` (a cite of a superseded id whose paragraph does not name the head of its
    chain) and `ssot-tombstone-body`. They run inside the section-status check, so
    `kyo check-branch` holds a worker's changed documents to them. An id cited from a document the
    branch did not change is reported by `kyo docs check` only, so a branch that deletes an
    owning heading is not blocked at its stop for the cites it leaves elsewhere. **The history
    lint** (KYO-555, slice 5): `ssot.history_lint: off | warn | block` (default `warn`, with or
    without ids) runs seven measured patterns over what a branch newly introduces into the set's
    documents (per file and pattern, more matches on added lines than on removed ones, outside
    code), prints each at `kyo check-branch` and `kyo docs preview`, and in a `block` set blocks
    the stop on a block-grade one (`used-to`, `previously`) unless a `Kyosei-History-OK` trailer
    names the file and pattern; `kyo report` lists each trailer with the disagreements.
  - *Enforced, at report time.* **A recx manifest as a document** (KYO-600, from the KYO-598
    decision). A set declaring `format: recx-manifest`
    matches exactly `RECX.yaml` at the code root and reads it with PyYAML, never through recx: a
    `(preamble)` section for the top-level fields, a `module <id>` section per module and an
    `evidence <id>` section per evidence entry. A module's `path` is its governed target, matched
    as a prefix of each changed path and read from both sides of the diff, so a deletion and a
    move raise as an edit does; optional target lists in `documents.yml` (`project_governs`,
    `evidence_governs`, and `module_governs`, which replaces a module's path) keep anything only
    Kyosei reads out of the public manifest. The obligations are ordinary ones, and a
    comment-only change under a module's path raises none, by KYO-530's rule (KYO-606). A file it cannot
    read safely (invalid YAML; an anchor, alias or merge key; no `modules` list of mappings with
    string `id` and `path`) is one ungoverned `(preamble)` section, and `kyo docs check` says why.
  - *Suggested, and enforced nowhere.* `kyo docs suggest` (KYO-303) — the only thing in this
    module that drafts rather than checks, and the register it sits in is the point. It reads
    `documents.yml` and the repository and prints the declarations a project *might* want: a
    `numbered:` block for a document whose headings follow a numbered pattern under a set that has
    not declared one (the template inferred from the headings themselves) and whose git history
    shows it growing at its end — at least two commits each adding a section after the last, none
    editing an earlier one; without that evidence a numbered file gets only a question and no YAML,
    because headings alone describe a plan edited in place as well as a log (KYO-562) — an exclusion for a file
    that says it is generated inside a set's globs, the same for a document with a generated
    index beside it — which is the only place `after:` can be read rather than guessed — and an
    `ssot:` block for documents whose headings carry ids (`## API-12. Title`), its prefixes and
    pattern read off those headings and offered only when `kyo docs check` passes with it pasted
    (KYO-556). Each
    carries the evidence it was drawn from and the exact YAML to paste, and it writes nothing.
    The posture is `kyo new`'s about scope since KYO-100 (`derived (suggestion only, nothing
    stored) ... derivation never overrides a declaration`) and it is load-bearing here: the
    project's declaration is the authority, no `kyo doctor` PROBLEM reads a suggestion, the one
    NOTICE that mentions them names the command and the count and reproduces none of them, and
    `docs_suggest_notice: false` switches even that off — a suggestion that cannot be declined is
    not a suggestion. Every block is validated by the registry's own reader before it is offered;
    a draft the next command would refuse is worse than no draft. Filed because the `numbered:`
    block above reached nobody who did not already know it existed.
  - *Advisory, and store-only.* `description-predates-claim` (KYO-137.c7): a record whose
    description has not been edited since a live claim on it was recorded. The one staleness case
    with no document in it, where the store holds both halves and needs no repository to compare
    them. A prompt from `kyo sweep`, never a refusal — a newer claim may confirm the description
    word for word.
- Docstrings may **cite** another module's behavior but must not **copy** it — prose inside code
  asserting things about other code decays exactly like a card.
- v1 was specified to ship the schema and a skeleton validator covering both "targets exist" and
  "pairs agree"; only the first half shipped with it, and the second shipped with KYO-499. That
  history is recorded here rather than edited away, per this file's own status note. The full
  rule set grows only alongside checks (law 1).

---

## Claude Code integration (the enforcement surface)

governs: src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md, src/kyosei/defaults/hooks/session-start.json, src/kyosei/defaults/hooks/pre-tool-use.json, src/kyosei/defaults/hooks/subagent-stop.json

Built-ins verified 2026-08: ultraplan is deprecated; ultracode is a per-session effort/orchestration
mode with no persistence; native task tools are session-only. So `kyosei` owns cross-session state,
and integrates via:

- **A skill** (`/kyosei`) teaching the workflow — low context cost, loaded on demand. No MCP server
  in v1; the CLI *is* the API (JSON out, files/stdin in). MCP can wrap the CLI later if a non-CLI
  client needs it.
  Since KYO-593 the skill ships as part of a Claude Code plugin inside the package
  (`src/kyosei/harness/claude-code/`), whose `hooks/hooks.json` is generated from the same hook fragments listed below;
  `kyo install-skill` copies the same two skill files. Registering the plugin is KYO-594.
- **Hooks** (installed by `kyo init` as templates into consumer projects):
  - `SessionStart` → `kyo brief --compact` injected (the token-efficient briefing; no re-derivation)
  - `PreToolUse` on Bash → two checks that stop a command before it runs, each silent unless it
    fires: `kyo check-push --pre-tool-use` stops a `git push` that would publish an in-repo store
    (KYO-60), and `kyo check-command --pre-tool-use` refuses a command the project declared
    orchestrator-only when it would run inside a running dispatch's worktree (KYO-298). The
    `git commit`-on-`kyo doctor` gate this line described until 2026-09-18 never shipped: the
    packaged PreToolUse fragment was first added by KYO-60 and has never carried one.
  - `SubagentStop` → report-artifact presence check (and note: a stop hook is NOT the durability
    mechanism — law 8 — it is a second net). KYO-206 gives that artifact one named path,
    `.kyosei-report.json`, which `kyo init`/`kyo install-hooks` gitignore and the stop's own
    uncommitted-work check excludes — so "commit before you stop" stops meaning "commit your
    report". These guards run *after* the worker's hand-back reaches the orchestrator, and the
    harness signals their verdict separately from it, so a hand-back is not final until they
    pass: a worker they block goes back and commits, rebases or amends, and an orchestrator who
    merged on the message alone has merged shas that no longer exist (KYO-294). The skill says
    so where an orchestrator reads before merging.
  - `UserPromptSubmit` → `kyo check-build --hook`: one line when the `kyo` on PATH is not the
    build this session's opening brief recorded, silent otherwise, read-only (KYO-398). Since
    KYO-335 one installed `kyo` serves every project on a machine (the uv tool since KYO-443
    and KYO-450), so an upgrade (`uv tool upgrade kyosei`, or a reinstall from source)
    changes the tool under running sessions; the session-start brief names a change between
    sessions, and this names one within a session.
  - Every packaged hook exits 0 with no output where no Kyosei project resolves, because a
    plugin's hooks fire in every session (KYO-591): `kyo`'s own project resolution decides when
    it is on PATH, and the missing-`kyo` guard's marker walk decides when it is not. A blocking
    stop guard blocks the first stop only; a repeat stop (`stop_hook_active`) reports and records
    the finding without blocking again (KYO-592).
- **CLAUDE.md in consumer projects** carries awareness ("this project uses Kyosei; the skill is
  /kyosei"), not rules — rules live in hooks and transition contracts, because prose mandates decay.

---

## User stories

### Human

- A reviewer gets a concrete, *runnable-now* task: the `awaiting:human-run` gate carries the exact
  thing to run; a gated-on-deploy task never appears in their queue.
- The maintainer records a thought in `ideas/` in ten seconds with zero required fields, and purges
  stale ones from a single review listing.
- The maintainer views the project at any altitude — kind map, board by status×kind, record detail
  (metadata pane + rendered-markdown pane, PyCharm-style), link graph — while agents make every
  state change.
- The maintainer reads a record's *current understanding* at the top, not an archaeology of
  superseded notes; the contested history is one click deeper, never lost.
- Docs are current by construction: a stale `governs:` target fails the build, so "is this doc
  true" is answered by CI, not by trust.
- The maintainer customizes the tool to the project by editing `kinds.yml` — adding a circle,
  drawing its arrows, setting its rules — and the viewer's map immediately reflects it.

### Claude (written by the agent that will operate this, in its own voice)

- **I have no continuity of intention across sessions.** A convention I agreed to last week
  reaches the next session only if something in the repo forces it to. Rank every mechanism by
  how much discipline it demands at the moment of reading; zero is the only reliable number.
  Gates and transition contracts bind me; prose requests compete with everything else in context.
- **I will believe your success messages.** I have no skepticism budget for my own tooling. A
  write path that can report success while storing something else will poison my downstream work
  invisibly — echo back what was stored, and fail loudly, because I act on what the tool tells me.
- **I inherit confidence from prose.** Text written in a confident register reads as true to me,
  and I will propagate it into briefs and cards in the same register. Verification levels on
  claims are how the store tells me what I'm allowed to believe — show them to me at dispatch
  time, when re-checking is cheapest.
- **My context is the scarcest resource in the system.** The session brief must be complete enough
  that I never re-derive board state from files, and compact enough that it doesn't crowd out the
  work. Give me deltas ("what changed since last brief"), summaries with drill-down IDs, and
  JSON I can query — never force a full-file read to answer a status question.
- **Transcription is where I lose fidelity.** When worker findings pass through me by hand, careful
  distinctions flatten and `NOT VERIFIED` gets buried in prose. Structured report ingestion keeps
  the reading (which is where corrections are caught) and drops the retyping (which is where they
  are lost).
- **Never make me choose between honesty and progress.** When the only states are "checked" or
  "blocked", the board rots in whichever direction satisfies the invariant. Criterion states,
  per-member status, `built`, `wont-do`, and `holding-for` exist so the true state is always
  expressible — an expressible truth is one I will record.
- **I cite identifiers in durable places** — commits, briefs, other records — within minutes of
  learning them. Anything positional or reusable will be wrong in my own writing almost
  immediately. Stable opaque IDs are not a nicety; they are what keeps my own output from lying.
- **Tell my workers their capabilities mechanically.** A worker that must infer its write scope
  either overreaches or under-delivers, and both surface as prose I have to catch. `write_scope`
  in the brief, and "flag conflicts, don't resolve" as a standing stance, make a worker's
  disagreement cheap to express — and worker disagreement is the highest-value output of dispatch.
- **I may die mid-operation.** Every store mutation is a transaction; every dispatch's work must
  be durable continuously; a crashed session must leave no half-writes and no silent locks —
  "died without a report" is a state, not an absence.

---

## v1 scope and build order

Each milestone ships with its tests, and every field ships with its consumer (law 2).

1. **Core store**: schema (records, criteria, events, links), ID allocation, `init / new / show /
   edit / move / link / gate / wont-do`, transition contracts, JSON export, `doctor`.
2. **Kind registry**: loader + validation, default kinds, policy enforcement (requires_criteria,
   counted, archetype behaviors), `map` data.
3. **Ideas layer + promotion**: `promote` with flow validation and touches-vs-repo guard,
   `ideas review`.
4. **Provenance**: claims, verifications, evidence artifacts, verdict histories, `claim / verify /
   attach`, supersede machinery for notes.
5. **Dispatch loop**: dispatch objects, brief generation, structured report ingestion,
   `terminated-no-report` detection, touch-disjointness check, `split`.
6. **Sweep + briefing**: `sweep` (all flag classes above), `next`, `brief`, creation-time
   duplicate check.
7. **Static viewer** (`kyo html`): board view (status×kind), record view (two-pane:
   metadata | rendered markdown), kind map (circles-and-arrows from the registry), link graph
   (vendored MIT force-graph or sigma.js — Obsidian's own code is closed-source and cannot be
   borrowed; its *file conventions* are open and Layer 1 stays vault-compatible), docs view.
   Read-only, no server, regenerated on demand.

   **Decision, 2026-09-07 (KYO-215): milestone 7 is retired. The desktop app is the viewer.**
   The prompt was the `html_freshness` doctor check failing a Stop hook by one event, again.
   What this milestone was for — a board a human can look at, per-record pages,
   the kind map, the link graph — is what the Electron app deferred to v2+ above now does,
   live against the store rather than regenerated from it, and the app never read the generated
   site. What is left behind is a surface that answers the same questions with a copy that goes
   stale the moment anything is written: `kyo html` had to be re-run to stay true, so the Stop
   hook ran it on every stop, so every session ended with a tree of regenerated HTML churn that
   no commit carried — and the check that told you the site was behind lost a race with the
   session-event write on most stops anyway. A viewer with no notion of being behind reads as
   authoritative, which is a failure operational experience had already recorded; the answer that scales is a
   viewer with no copy to be behind.

   **A project without the desktop app is not left without a board.** `kyo show <id>` is the
   record view, `kyo list` (with `--status`, `--kind`, `--plan`, and the saved Bases behind
   `kyo base`) is the board, `kyo map` is the kind map, and `kyo brief` is the session read of
   what is ready, gated, stale and unreported. Every one of them reads the store directly, so
   none of them can be behind it — which is the property the static site could not have.
   `kyo doctor`'s `retired_html_directory` notice tells a project that still has the old pages,
   once, that nothing writes them any more. The link graph is the one view with no CLI
   equivalent, and it is the desktop app's or nothing.
8. **Integration**: `/kyosei` skill, hook templates, docs-SOT skeleton validator, `install` flow
   for consumer projects.

Deferred to v2+: local live server; full docs validator rule set; external event watchers
("wake when the cycle reaches done" — design the gate schema so a watcher can clear gates later);
Electron app (native shell, point-at-a-project-directory, ideas editor pane — the state-read-only
rule is permanent regardless of shell); optional Obsidian-vault mirror export of the structured
store; criterion-as-query auto-evaluation.

## Tech choices

- **Python 3.12+**, stdlib-first: `sqlite3` (no ORM), `argparse`, `json`; `pyyaml` and a markdown
  renderer as the only notable deps. Rationale: broad tooling support, cross-platform, no runtime
  services.
- **pytest** as the floor from milestone 1; **ruff** for lint. `kyo doctor` is part of CI.
- Viewer: server-side-rendered static HTML + vendored JS (graph lib only). No CDN, no network
  at runtime.
- The tool lives in this repo and installs as a CLI (`pipx` / `uv tool install`) with two console
  entry points — `kyosei` and the blessed short alias `kyo` (identical); each consumer
  project gets `ideas/`, `kyosei/`, `docs/` via `kyo init`.

## References

The operational log and design sketches the laws were drawn from are not published; each law
states the failure it answers.

- beads (github.com/steveyegge/beads) — prior art for DB-authoritative + git-synced storage and
  ready-work detection; consciously not adopted because it models tasks, not claims/evidence/docs.
- Backlog.md — prior art and cautionary tale; its manifesto's human-readability principles are
  worth keeping even as its constraints are shed.
