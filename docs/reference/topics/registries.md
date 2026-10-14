# Registries and saved views

The kind, scope, status and gate-tag registries, and the saved views in `kyosei/bases.yml`.

## The kind registry

governs: src/kyosei/kinds.py, src/kyosei/defaults/kinds.yml

Every record has a **kind**, defined in `kyosei/kinds.yml` — bug, task, chore,
investigation, experiment, proposal, plan, reminder (archetype `work`: has criteria, a status
flow, dispatches), or decision, evidence (archetype `knowledge`: claim-bearing, no
status flow — `kyo move`/`kyo wont-do` refuse them, naming `kyo void` instead).
Claims, verifications, and evidence artifacts (`kyo claim` / `kyo verify` /
`kyo attach`) attach to a record of any kind, `work` or `knowledge` alike, and ship
as of this milestone; a dedicated `amend` verb for a `knowledge` record does not yet
exist — `kyo edit --description-file` is the in-place edit and `kyo void <id> --why`
is the disposal (KYO-44). Each kind declares:

- `accepts_from` / `produces` — the promotion/flow edges, validated symmetric in
  both directions at registry-load time;
- `policy` — `requires_criteria`, `dispatchable`, `unattended_ok`, `write_scope`,
  `swept`, `counted` (+ optional `worker_model` and `carried`) for `work` kinds;
  `amendable`, `swept`, `counted` (+ optional `requires_evidence_links` and
  `raises_docs_obligations`) for `knowledge` kinds. `raises_docs_obligations` (KYO-554, on
  the packaged `decision`) makes filing or editing a record raise a standing docs
  obligation on each ssot section it names by id and each of its doc-refs
  ([docs governance](docs-governance.md)).

**Reminders (KYO-497, KYO-521).** A decision waiting on the operator, or a reminder tied
to a date or an event, that the next shift must see is a record of the default `reminder`
kind (named `carry` until KYO-521): `archetype: work`, no criteria, never dispatched, not
counted, not swept, filed with `kyo new reminder --title "..."`. It is not for a defect
(a `bug`), a settled rule (a `decision` filed with the operator's words) or buildable work (a
`task`). Its policy carries `carried: true`, an optional boolean (default false) that marks
the kinds `kyo brief` lists in its **reminders** section — every record of such a kind whose
status has neither the `complete` nor the `retired` role, oldest first, with its age in
whole days since it was first recorded (see "Sweep, next, and the session briefing"). A decision
waiting on the operator is a reminder with an open gate (`kyo gate <id> awaiting:human-review
--do "..."`); there is no separate class field. On a carried kind, entering a
`complete`-role status needs `--why` (`carried-settle-requires-reason`; the reason is kept
as a record note), and `kyo move <id> done --why "..."` or `kyo wont-do <id> --why "..."`
clears every open gate on the record in the same transaction, with the settle's reason as
each gate's `cleared_why`, and says which gates it cleared — that is how a reminder
meets `settled-requires-no-open-gates` rather than bypassing it; every other kind is still
refused settling over an open gate. To file a follow-up, `kyo spawn <id> --kind task` (or
`bug`, `investigation`, `proposal`, `decision`) links it `produced-by` the item, and settling the item
is a separate `kyo move`. `kyo show <id>` prints `carried: since <time> (filed), <n>d`.
An item the project carried before it was filed keeps its true age: `kyo new reminder
--since-note PREFIX-Sn` (or `kyo edit <id> --since-note PREFIX-Sn` afterwards) names the
shift note that first recorded it, the age then counts from that note's tool-stamped time,
and `kyo show` names the note in place of `filed` (KYO-498; `kyo shift-notes` lists the
ids). Only the note id is stored. It is refused for a note this store does not hold, for a
note written after the record was filed, and on a kind without `carried: true`.
**Upgrade `kyo` before adding `carried: true` to an existing registry**: an older `kyo`
refuses a registry carrying a policy key it does not know ("policy carries unknown
key(s)"), so the edit made first breaks every command, hooks included. An existing
project's registry is its own copy — no command installs the new default kind into it;
copy the `reminder` entry from the packaged `kinds.yml`, and add `reminder` to the
`accepts_from` of each kind it `produces`. A registry that still names the kind `carry`
renames it by the steps in [install.md](install.md), "Upgrading from schema 25
to 26" (KYO-521).

**A decision is filed directly, with its evidence (KYO-521).** A decision the operator
made in conversation is filed where it was made: `decision` accepts `intake`, and `kyo new
decision --title "..." --evidence-file words.md` stores the file as an evidence artifact
on the new record in the same transaction -- verbatim, with its sha256, the row `kyo
attach` writes and `kyo confirm` writes for the maintainer's words. Write it the way a
`kyo confirm` quote is written: who, when, where, then the words verbatim. Without
`--evidence-file`, `kyo new` refuses (`filing-requires-evidence`, exit 2) any kind whose
policy sets `requires_evidence_links`, which is that policy key's consumer; the refusal
names the flag. `--evidence-file` takes a file or `-` for stdin, works beside `--from`
(it is not one of the six fields `--from` replaces), and is allowed on any kind. The
proposal route remains for a decision that needs design work first -- `kyo spawn
<proposal-id> --kind decision` once the proposal is approved -- and a reminder that was
waiting on the operator becomes one with `kyo spawn <reminder-id> --kind decision
--evidence-file words.md`.

**Which spawn routes need evidence (KYO-522).** A reminder becomes a decision exactly when
the operator answers in conversation, so `kyo spawn` from a source whose kind is `carried`
(the `reminder`) to a kind that sets `requires_evidence_links` is refused
(`filing-requires-evidence`, exit 2, naming `--evidence-file`) unless the flag attaches the
words in the same act -- the same helper and the same refusal as `kyo new`. A spawn from a
proposal or an investigation asks for none: the approved proposal or finished investigation
already holds the provenance, and the `produced-by` link names it. `--evidence-file` is
accepted on any spawn and stored when given.

`kyo new <kind>` refuses an unknown kind, listing every known one — and refuses a
*known* kind whose `accepts_from` does not carry `intake`, because `kyo new` is the
intake edge (KYO-43). In the shipped registry every kind is filed directly — `bug`,
`chore`, `decision`, `evidence`, `experiment`, `investigation`, `plan`, `proposal`,
`reminder`, `task` — because filing directly costs no structure: `kyo new` takes
`--criteria-file` and `--touches-file` exactly as `kyo promote` does (KYO-228). Write to
`ideas/` when a thought genuinely is not being filed this session, not as a required
first step before every record. `decision` was the one kind still refused until
KYO-521, and is filed directly only with `--evidence-file` (above). A kind a project
leaves without `intake` arrives via `kyo promote` or `kyo spawn`, which enforce the
matching rules on their own edges (`idea` / a source kind in `accepts_from`). There is
deliberately no `--force`: a project that wants a kind filed
directly adds `intake` to that kind's `accepts_from`, which is one reviewable line in
a versioned file rather than an invisible flag in a script.

All three creation verbs now refuse a registry-flow violation the same way, with
exit 2 (KYO-68): `kyo new` when the kind does not accept `intake`, `kyo promote`
when it does not accept `idea`, and `kyo spawn` when the source's kind is not in
the target's `accepts_from`. The contract *names* stay distinct so a script can
tell which edge was refused; only the exit code is shared, because "the creation
verbs refused my kind" is one situation and used to be two codes.

`kyo spawn <source-id> --kind <target>` is the third and last of those edges: the
one where `accepts_from` names **another kind**. It refuses (exit 2) unless the
source record's kind is in the target's `accepts_from`, creates the target, and
links it back to the source with `produced-by` — the same edge and direction
`kyo split` writes. Before it, that source had no command at all, so every
`bug -> task` / `proposal -> decision` edge in the registry was a transition
`kyo map` would draw and `kyo doctor` would check the symmetry of while nothing
could walk it (KYO-50). Three things it deliberately does not do: it does not copy
the source's text (the source stays live and the two would drift — the new record
carries a provenance stub, and `--description-file` is where its own prose goes),
it does not change the source's status (spawning is not a claim about the work, and
half the registry's kind-to-kind edges start at a `knowledge` kind that has no
status flow at all), and it does not stop at one — an investigation may produce five
evidence records, and a repeat is met with the same near-duplicate warning `kyo new`
gives, never a refusal. A target whose `policy.requires_criteria` is true needs
`--criteria-file`, and an `archetype: work` target needs `--touches-file`, exactly as
`kyo promote` requires them: spawning is a judgment about what to do next, which is
when structure is worth authoring (law 13).

`kyo map [--json]` renders the registry as a graph — kinds as nodes, `intake`/`idea` as pseudo-sources,
edges from `accepts_from`. `kyo list [--kind K] [--status S] [--json]` lists records
and summarises the queue split by `policy.counted`. With no filter it lists every record,
done and void included — that default is deliberate and stays; an agent that
wants the short listing asks for it with `kyo list --unsettled` (`--limit N` to cap it). `kyo doctor` validates the
registry and that every record's kind is still defined in it.

**Removing a kind that records still use (KYO-509).** Deleting a kind from `kinds.yml`
does not touch its records: they still list and show, they drop out of `kyo list`'s
counted total, `kyo new <kind>` is refused, and `kyo doctor` reports each one under
`kind_registry` at every session stop until it is fixed. Re-kind them **before** removing
the kind: `kyo edit <id> --kind <kind>` changes a record's kind to another kind the
registry defines with the same archetype, and its receipt names both (`kind: chore ->
task`). It is an ordinary edit, recorded in the events ledger and the record's export,
so `kyo rebuild` reproduces it. It refuses a kind `kinds.yml` does not define, a kind of
the other archetype (a `work` record cannot become a `knowledge` one, or the reverse),
a plan that still has members (detach them with `kyo edit <member> --no-plan` first),
and a record carrying a `--since-note` re-kinded to a kind without `carried: true`. A
kind already removed is not a dead end: the `kind_registry` problem names
`kyo edit <id> --kind <kind>` as its remedy (restoring the kind to `kinds.yml` is the
other), and the edit still checks the archetype, read from the packaged default
`kinds.yml` when the removed kind was a default one.

## The scope registry

governs: src/kyosei/registry.py, src/kyosei/defaults/scopes.yml

A record may also declare a **scope** — which part of the system its work belongs to
(`server`, `web-app`, `app-desktop`) — named from `kyosei/scopes.yml` and validated
against it at write time by `kyo new`, `kyo promote`, `kyo spawn` and `kyo edit`.
Naming a scope the registry does not declare is refused, with the file's path and the
scopes it does hold; as with kinds there is deliberately no `--force`, because adding
the scope is one reviewable line in a versioned file.

```yaml
# kyosei/scopes.yml
scopes:
  app-desktop:
    summary: The desktop app -- its shell, its panes, its bundled runtime.
    paths:                          # optional
      - apps/desktop/*
```

An entry needs a name (a lowercase slug) and a `summary`. `paths` is optional and has
exactly one consumer: after a write, the creation verbs compare the record's `touches`
against these globs and *suggest* a scope, or point out that the declared one and the
touches disagree. **That suggestion is never the source.** It is printed labeled
`derived (suggestion only, nothing stored)`, it stores nothing, and no other code path
— `kyo rebuild` included — derives a scope from anything. Records without touches, and
documents, have nothing to derive from at all.

Scope is optional everywhere. A store created before this release has no
`kyosei/scopes.yml`, which reads as "this project declares no scopes" rather than as an
error, and every record in it has no scope, which reads as "none was declared" rather
than as a scope to be guessed at.

`kyo show` renders the scope (and renders its absence), `kyo list` shows it in the
listing and `kyo list --scope S` filters by it — refusing an undeclared `S` rather than
answering with an empty listing, which would read as "nothing is in that scope".
`kyo doctor`'s `scope_registry` check validates the file and catches a record whose
scope has since been renamed or removed from it, which is the half a write-time check
cannot see.

The file shape — one file per dimension under `kyosei/`, named for the dimension's
plural, top-level key the plural, entries carrying a name and a summary — is shared by
every registry dimension and lives in `kyosei/registry.py`, which is what `kinds.yml`'s
successors are built from.

## The status registry, and why contracts key off roles

governs: src/kyosei/contracts.py, src/kyosei/defaults/statuses.yml

The work archetype's ladder is `kyosei/statuses.yml`: this project says what its
statuses are **called**, and each one declares the **archetype role** it plays. Every
transition contract binds to the role.

```yaml
# kyosei/statuses.yml
statuses:
  open:
    summary: Filed, with nothing started on it yet.
    role: initial
  working:
    summary: Someone or something is working on this right now.
    role: active
  built:
    summary: Written, but nothing has been checked.
    role: needs-verification
  verified:
    summary: A verdict has been recorded; only the move to done remains.
    role: verified
  done:
    summary: Finished -- every criterion settled, at least one of them met.
    role: complete
  wont-do:
    summary: Retired without being done. Requires a reason; nothing leaves it.
    role: retired
```

**The names are yours; the roles are not.** Rename `done` to `shipped` and
`complete-requires-settled-criteria` still refuses an unqualified move into it, under
the new name — because that contract was never a fact about the word `done`, only
about the status where work is completed. The six roles are a closed set
(`kyosei/contracts.py`, `STATUS_ROLES`); a project that could add a seventh could add a
contract nothing enforces.

What each role costs, stated so a rename is made with the contracts in view:

| role | means | what entering it requires |
| --- | --- | --- |
| `initial` | filed; nothing has started | — (this is where `kyo new`/`promote`/`spawn` file a record) |
| `active` | work is underway | at least one criterion, on a `requires_criteria` kind (`active-requires-criteria`) |
| `needs-verification` | built, no verdict yet | at least one criterion, on a `requires_criteria` kind (`verification-requires-criteria`) — a move may skip `active`, and this is the rung `kyo brief` reports as awaiting verification, so a record with nothing to verify is refused here |
| `verified` | a verdict is recorded | at least one criterion, and a recorded verdict on every live one (any verdict, `unmet-unbuilt` included), on a `requires_criteria` kind (`verification-requires-criteria`, `verified-requires-a-verdict`) — the refusal names `kyo verify <cid>` and, when an ingested report holds an unapplied verdict, `kyo accept <dispatch-id>` (KYO-546) |
| `complete` | finished, with the criteria to say so | every criterion settled *and* at least one `met` (`complete-requires-settled-criteria`, `complete-requires-a-met-criterion`), and no open gate (`settled-requires-no-open-gates`) |
| `retired` | settled without completing | `--why`, and no open gate; nothing may leave it afterwards (`retired-requires-reason`, `settled-requires-no-open-gates`, `terminal-status-is-terminal`) |

Exactly one status may carry `initial`, one `complete` and one `retired`: each is a
status the tool has to be able to *name* rather than merely test for — where a record is
filed, what `kyo wont-do` moves to, what "finished" means to the ready-work queue — and
"which of your three completions did you mean" has no answer. The other three roles may
be carried by any number of statuses, including none. **The file's order is the ladder**,
so there is no `position:` field to keep in step with it.

`complete` and `retired` are two roles rather than one terminal because their contracts
differ in both directions: completion demands a met criterion, retirement demands a
reason, and a completed record may be reopened while a retired id is gone for good.

A `plan` carries a third, archetype-specific `complete` requirement:
`complete-requires-settled-plan-members` (KYO-308) refuses `kyo move <plan> <a completion
status>` by name when any member is not itself settled (its own status is `complete` or
`retired`), naming the remedies — settle each member, or detach it with
`kyo edit <MEMBER-ID> --no-plan` — and `--force` overrides it, recording that the plan was
closed over open members. `plan`'s `requires_criteria: false` means the two criteria
contracts above never fire for it, so this is the only safeguard standing between "printed
as done" and "is done" for that kind.

Every record carries one more settling requirement, on both settling roles:
`settled-requires-no-open-gates` (KYO-490) refuses `kyo move` into a `complete` or `retired`
status, `kyo wont-do` and `kyo void` while the record has an open gate, naming each gate's
id, type and first payload line. Clear it with `kyo ungate <gate-id> --why`, or pass `--force`
to settle anyway with the gate left open (the command lists the gates it left).
`kyo accept --close` reads the same refusal and leaves a gated record where it is, in
`left_open`, counted toward `complete: false`. Gates stay orthogonal to every other move.
A record of a `policy.carried` kind (KYO-497, "The kind registry"; the default
`reminder`) adds one more: entering
a `complete`-role status needs `--why` (`carried-settle-requires-reason`), and its settle
clears the record's open gates with that reason before this contract runs, so it finds none.

A store created before this release has no `kyosei/statuses.yml` and gets the ladder
above — the same file `kyo init` writes, so the default and the fallback cannot drift
apart. `kyo doctor`'s `statuses` check validates the file and catches a record sitting
in a status that has since left the registry; nothing is migrated on your behalf, because
a rename that stranded records is a decision for whoever made it.

## The gate-tag registry

governs: src/kyosei/registry.py, src/kyosei/defaults/gate-tags.yml

A **gate** holds a record until something outside the store clears it, and it is
orthogonal to status: a record can be `working` *and* blocked, which is a state a
`gated` status would make unsayable. There is deliberately no such status, and no role
for one.

A gate's *type* is one of four (`awaiting:event`, `awaiting:human-run`,
`awaiting:human-review`, `holding-for`). Its *payload* — `--do` — is free text, and free
text is not searchable, so "everything waiting on a human to review a UI element" was
not a question the store could be asked. `kyosei/gate-tags.yml` is that question's other
half:

```yaml
# kyosei/gate-tags.yml
gate-tags:
  ui-review:
    summary: A person has to look at a rendered interface and judge it.
  deploy:
    summary: Waiting on a release to reach an environment where this can be seen.
```

An entry needs a name (a lowercase slug) and a `summary`, and nothing else — a tag does
not declare which gate types it may appear on.

```
kyo gate KYO-31 awaiting:human-review --tag ui-review
kyo gate KYO-31 awaiting:event --do "the 2026-09 release reaches staging" --tag deploy
kyo list --gate-tag ui-review
```

**The tag is beside the payload, never instead of it.** `--do` is still required for the
three gate types nobody could clear without one: a tag says a gate is waiting on a
deploy, and only the payload says *which* deploy. `--tag` is repeatable, refused if the
registry does not declare the tag (with the file's path and the tags it does hold), and
a tag typed twice is stored once — the echo shows what was stored.

`kyo list --gate-tag T` selects records carrying an **open** gate tagged `T`, and shows
the tags in the listing; an undeclared `T` is refused rather than answered with an empty
listing, which would read as "nothing is waiting on that". `kyo show`, `kyo brief` (in
full and `--compact`) render a gate's tags beside its type and payload.
`kyo doctor`'s `gate_tags` check validates the file and catches a gate carrying a tag
that has since left the registry — at which point the gate has silently dropped out of
the only query it was tagged for.

**A gate that carries the wrong tags, or the wrong payload, is corrected in place.**
`kyo retag <gate-id> --tag T` replaces the gate's tags (`--no-tags` leaves it carrying
none) and `kyo redo <gate-id> --do "<corrected>"` (or `--do-file`) replaces an open
gate's payload; each writes that one field and echoes the previous value beside the
stored one. Neither is a clearing: the gate keeps its id, its type, the other field, and
its `created_at`/`cleared_at` history. That is the point of having them, because the
`kyo ungate` + `kyo gate` round trip they replace writes a *clearing* into a git-canonical
ledger for a condition that never cleared, mints a new id for one continuing obligation,
and on a `holding-for` gate raises `gate-cleared-stale-criteria` against criteria nothing
unblocked, a cost that adds up when classifications (KYO-130) or payloads (KYO-148) are
corrected often. The two part company on a *cleared* gate: `kyo retag`
allows it, since `kyo list --gate-tag` selects on open gates and no answer changes, while
`kyo redo` refuses it — somebody was told to do that thing, did it, and recorded
`cleared_why` against those words.

**Clearing several gates is one act.** `kyo ungate G1 G2 ... --why TEXT` clears every
named gate in one transaction and prints a line per gate; if any id is unknown or
already cleared, nothing is cleared and the refusal names each bad id with the reason a
single `kyo ungate` would have given. `kyo ungate --tag T --why TEXT` lists every *open*
gate carrying `T` and writes nothing; `--apply` clears them all in one transaction (law
11, as `kyo flag collapse` has it). An undeclared `T` is refused naming
`kyosei/gate-tags.yml`. Each clearing is a single `kyo ungate`'s — the same
`cleared_why`, the same events, the same `gate-cleared-stale-criteria` flag on a
`holding-for` gate — repeated. A bare gate id with no `--tag` is the command it always
was.

**Merged is not live: the deploy gate.** A project whose merges reach users only at its
next deploy keeps that difference without a new stored field (KYO-479). Declare a
`deploy` tag (the packaged `gate-tags.yml` carries it commented out), and when a record's
work merges, gate it:

```
kyo gate KYO-31 awaiting:event --tag deploy --do "live after the next deploy"
kyo list --gate-tag deploy                                    # merged, not live
kyo ungate KYO-31.g2 KYO-32.g1 --why "deployed 2026-10-03"    # this deploy's subset
kyo ungate --tag deploy --why "deployed 2026-10-03"           # or everything: list...
kyo ungate --tag deploy --why "deployed 2026-10-03" --apply   # ...then clear
```

The brief's gated work section shows the same set. A record holding an open gate cannot
settle, so a record reaching done means its work is live, not merely merged.

Tags are optional everywhere. `kyo init` ships the file declaring none, because what a
project waits on is a claim about *that* project; a store created before this release
has no `kyosei/gate-tags.yml` at all, which reads as "this project declares no gate
tags", and every gate in it carries none.

## Saved views: `kyosei/bases.yml` and `kyo base`

governs: src/kyosei/bases.py, src/kyosei/defaults/bases.yml

A **Base** is a saved query with an arrangement: a source, filters, the columns a
rendering shows, a sort, and an optional `group-by`. It exists because the board, the
status view, a bubble overview and a typed-edge graph are not four features — they are
four *renderings* of one saved question, and writing the question down once is what lets
you re-bind how it is drawn instead of rewriting it.

```yaml
# kyosei/bases.yml
bases:
  open-work:
    summary: Everything open, newest first — the queue.
    source: records
    filters:
      status: open
    columns: [id, kind, standing, gates_open, title]
    sort: [-seq]
    group-by: kind
```

```
kyo base list                   # what this project has saved, and the file they live in
kyo base run open-work          # run one, read-only
kyo base run open-work --json   # rows, applied columns/sort, grouping, per-row detail,
                                # and the db/export currency direction
```

### The three sources

governs: src/kyosei/query.py

A base's `source:` is `records`, `ideas` or `docs`. All three run through one engine and
come back as **one document**: the same keys (`rows`, `applied`, `groups`, `summary`,
`db_export_currency`), the same grouping computation, the same currency block — so a
rendering built for one draws all three. What differs is the *field set*, because a
record has an id and a file has a path.

| source | what a row is | its fields | its filters |
|---|---|---|---|
| `records` | one record | `id`, `seq`, `kind`, `scope`, `status`, `standing`, `gates_open`, `gate_tags`, `gate_types`, `title`, `counted`, `created_at`, `updated_at` | `kind`, `scope`, `status`, `plan`, `gate-tag` — exactly `kyo list`'s filters; its row-count flags `--unsettled` and `--limit` are not Base filters, and `status:` takes one value where `kyo list --status` may repeat (KYO-420) |
| `ideas` | one file under `ideas/` | `id` (its path), `title`, `stamped`, `promoted`, `promoted_at`, `section_count`, `promoted_section_count`, `properties` | `path`, `contains`, any field, `properties.<key>` |
| `docs` | one file under `docs/` | `id` (its path), `title`, `section_count`, `governs`, `governs_broken`, `amended`, `staleness`, `staleness_says`, `properties` | the same four |

Rows carry per-row detail beside their columns: a record's `links` (every typed edge
touching it, both directions), and a file's `sections` — each heading, plus the record a
promoted idea section's stub points at, or a document section's `governs:`, `status:`
and `amended-by:` metadata.

**Filtering a file source.** `path:` is a glob over the file's path. `contains:` is a
phrase in the file's **body**, whitespace-normalised on both sides, so a phrase that
hard-wrapped in the file still matches — this repo's prose is hard-wrapped deliberately,
and a filter that fails to find a note you know exists teaches you not to trust it. Any
other key is equality against a field, and a multi-valued field matches if *any* of its
values does (`governs: src/kyosei/brief.py`, `promoted: KYO-30`). `properties.<key>` is
equality against a frontmatter property, where the key is whatever the file states —
`author`, `area`, anything. The same dotted name works as a column, a sort key and a
`group-by`.

```yaml
  unpromoted:
    summary: Ideas nobody has turned into a record yet.
    source: ideas
    filters:
      stamped: "false"
    columns: [id, title, section_count]

  stale-docs:
    summary: Documents the code they govern has moved out from under.
    source: docs
    filters:
      staleness: stale
    columns: [id, title, governs, staleness_says]
```

**Querying the ideas pool never validates it.** The pool has zero required ceremony, so
a file with no frontmatter is a row whose `properties` is `{}`, and a property no file
declares is an empty column rather than an error. The one thing that cannot be settled
when the file is read is therefore settled at the row that proves it: grouping by a
property whose value is a list is refused naming the file, because a row with two values
belongs to two groups.

**A document says whether it is still current with what it governs.** `staleness` is one
of `current`, `stale`, `ungoverned` (the file declares no `governs:` target at all) or
`cannot-tell`, with the evidence in `staleness_says` and the unresolved targets in
`governs_broken` — decided by `kyo docs check`'s own rule, so the two cannot disagree.
Only git is consulted: a file git cannot date reads `cannot-tell`, not `current`, because
on a fresh clone every file's mtime is the checkout time and an mtime comparison would
report a whole docs tree current. This is the data the `touches × governs` banner needs;
the banner itself is a rendering and is not built yet.

**A Base cannot ask a question this project's registries do not license.** The file is
validated on load, and a `kind`, `status`, `scope` or `gate-tag` value the corresponding
registry does not declare is refused in that registry's own words, naming the file that
would make it valid — the same sentence a write gets. A `columns`, `sort` or `group-by`
name that is not a field is refused naming the field set. Every problem in the file
arrives in one message.

**A records base's filters are exactly `kyo list`'s filters, because they are the same
filters.** `kyo base run` is not a second query path: both surfaces call one engine
(`src/kyosei/query.py`), which owns the SQL, the standing-aware status filter, the
open-gate pass and the counted/not-counted split. The filter set is a single dataclass
that `kyo list`'s flags and a Base's `filters:` keys are both derived from, and a test
fails if one surface ever gains a filter the other lacks.

`sort` takes field names most-significant-first and a leading `-` reverses one; the
default is `seq` for records (the order `kyo list` prints) and `id` for the two file
sources (path order). A field a base declares as a list — `gate_tags`, `promoted`,
`governs` — can be shown and filtered on but never grouped by, since a row with two
values belongs to two groups.

The counted/not-counted split in `summary` is each source's own: for records it is
`kinds.yml`'s `policy.counted`; for ideas it is the files not yet promoted (a stamped one
points at a record the records source already counts); for docs it is the files that
declare a `governs:` target.

Every `--json` run states the db/export currency direction (`in-step`, `db-ahead`,
`export-ahead`, `cannot-tell`) so a rendering has somewhere to paint *could not tell*
instead of defaulting to clean.

`kyo init` ships `bases.yml` declaring none, with the shape and two commented examples.
The reason it is empty is sharper than for `scopes.yml`: a seeded base would have to name
a status or a kind out of this project's own registries, so a project that renamed its
ladder would hold a `bases.yml` that fails to load — taking every base it had written
itself down with it. A store created before this release has no `kyosei/bases.yml` at
all, which reads as "this project has saved no views".

Bases are **config, not store data**: no record field is added, no schema version moves,
and nothing in `kyo` writes the file — it is edited and reviewed the way `kinds.yml` is.
`color-by` and `size-by` are deliberately not in the format yet: nothing in v1 reads
them, and a stored field with no consumer is what this project's second law forbids.
