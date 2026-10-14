# The dispatch loop

Dispatching work to workers, ingesting their reports, and the sweep, next and briefing commands around that loop.

## The dispatch loop

governs: src/kyosei/cmd_dispatch.py, src/kyosei/cmd_worktrees.py, src/kyosei/dispatch_close.py, src/kyosei/dispatch_docs.py, src/kyosei/report_ingest.py, src/kyosei/stop_audit.py, src/kyosei/stop_target.py, src/kyosei/touch_drift.py, src/kyosei/worker_tokens.py, templates/brief-template.md

A **dispatch** is its own dimension from record status — dispatching a record
never moves it. `kyo dispatch <record>` refuses (exit 2, naming the colliding
dispatch and the shared paths) when the record's touches overlap a *running*
dispatch's touches (a `dir/` directory touch counts as every path under it, a
`+path` new-file touch compares as the path it names so two dispatches that both
declare it collide, and a plain `path` claims the whole file, so it collides with
every `path#symbol` in it; a `path#+` additive claim is disjoint from every other
additive claim and from every `#symbol` claim on the same file, and collides only
with a claim on the plain path, KYO-300);
`--force` overrides, and the override is recorded on the new dispatch.
Separately, `kyo dispatch` warns (never refuses) when one of the record's touches is a
directory that contains a touch of another open record, naming the overlap and suggesting
`+<dir>/<file>` for files the work creates and named files for ones it edits (KYO-539): a
claim on all of `tests/` can hold a whole wave.

It also refuses a record that has nothing left to build — criteria present and every
one settled — with `contract dispatch-record-finished`, naming the status move that
class needs (KYO-295). That class is `kyo next`'s (see "Sweep, next, and the session
briefing"), and the refusal is raised off the same function the listing filters with,
so the surface an orchestrator reads and the surface it acts on cannot disagree about
whether a record is finished. `--force` dispatches it anyway and the output says what
it overrode.

It refuses a record that an incoming `blocks` link holds back — some `A blocks <record>`
whose `A` is not settled on the status ladder (a gated `A` is unsettled) — with
`contract dispatch-record-blocked`, naming each blocker, its status and the link, and
`--force` (KYO-570). That is `kyo next`'s blocked class, refused off the same function
(`cmd_next.unsettled_blockers`). `--force` dispatches it anyway; the echo and `--json`'s
`blocked_overridden` name the blockers it overrode.

Under the split layout the code repository's `.kyosei` may stay untracked. A worktree
cut from HEAD then carries no link of its own, and `kyo` there reads the main checkout's
link at the same relative position, with the worktree as the code root (KYO-580; see
"Where the store lives"), so a `--branch` or `--worktree` dispatch needs no committed link.

**One exception, and only where both sides were precise** (KYO-147.c5): when both
records name the *same* file at `#symbol`s that do not overlap, that is a **shared
file, not a collision**. It is not refused; `kyo dispatch` states the file and each
side's symbols, in its output, in `--json` (`touch_narrowings`, never folded into
`collisions_overridden` — nothing was forced past) and in the frozen brief, so the
worker knows another worker is in that file. Either side declaring the bare path
instead gets the whole-file collision back. Measured need: two dispatches editing
different functions in `doctor.py` and different paragraphs in `README.md` were
refused against each other, and `--force` — recording an override of a collision that
was never there — was the only way through.

**A second exception, stated as a warning** (KYO-395): a `+path` new-file touch of the
dispatch being created that lies under a `dir/` touch of a running one, where the file
does not exist yet, is **let through with a WARNING** rather than refused. The file cannot
be in the other dispatch's frozen plan — but a `dir/` touch claims a tree its work creates
or owns, so that worker is free to create files there, this same name included, and the
warning says so, naming both dispatches and the file: in the output, in `--json`
(`touch_narrowings`, entries with `"why": "new-file-under-directory"`) and in the frozen
brief, which `kyo show <dispatch-id>` reads back. It is still refused when the file
already exists, when the other dispatch names that same path, and in the other
direction (a candidate claiming `dir/` while a running dispatch creates a file in it).

**A symbol claim is checked against the work, not only against other claims**
(KYO-300). `kyo touch-drift` reports a branch that edited a file the record claims
by `#symbol` *outside* every symbol it named — a finding the file-level list cannot
carry, since the path is in the claim — and names the symbol touch to add; and it
reports a branch that changed or removed existing lines of a file claimed only
additively (`path#+`). Both rest on the stated textual approximation of a symbol's
region, placed in the branch tip's copy of the file the diff's line numbers belong
to, never in the orchestrator's checkout (KYO-406); and both say `not-run` rather than
"clean" whenever the diff could not be had (a merged branch, no branch, no git), or
name the paths whose diff or tip copy could not be read. The correction the command prints now
carries the record's existing `#symbol`s instead of flattening them to bare paths.

**The correction applies where the workflow says to apply it** (KYO-307). A drift
correction is applied *after* the merge, because a file the branch created exists only
on that branch until then — and after the merge a `+path` new-file touch names a file
that is now present, which touch validation refuses ("that path is already there").
So a `+path` line whose file now exists is emitted as the plain path it has become, by
`kyo touch-drift` and `kyo edit --touches-from-drift` alike, and both say which lines
they converted; while the file is still only on the branch the `+` form is kept
exactly as it was. The conversion is in the correction and not in validation, so the
printed lines still paste into `kyo edit --touches-file` and a hand-written `+path`
for a file that is already there is refused as before. A `dir/` directory touch needs
none of this — it is accepted whether or not the tree exists. The far side of the same
rule (KYO-436): a claimed file that no longer exists — the branch deleted it, or another
merge removed it from the trunk — is left out of the correction, and both commands name
each dropped line and which of the two happened. A plain path must exist to be claimed,
and work that removed a file has reached that claim rather than left it unfinished. A
third case is dropped the same way (KYO-543): a file the branch itself created, merged,
and then deleted on the trunk — a notes fragment `kyo docs assemble` consumed — which the
branch tip still has; whether the tip is an ancestor of HEAD, not whether it has the file,
tells it from unlanded work, and it is named as this branch's own file deleted after the
merge. A file the branch created and the merge has not landed yet is still kept, and
since KYO-543.c2 it is kept as `+path` rather than the plain path, so `kyo accept` run
*before* the merge stores the correction instead of meeting the plain-path refusal; after
the merge the KYO-307 conversion above turns it back into the plain path. Only a branch
git can still read proves that: with no branch to ask the line stays plain and is listed
as waiting on the merge.

**What that check is worth, said out loud** (KYO-147.c1). Disjointness is computed
from DECLARED touches on both sides — predictions written before the work — and a
passing check used to say nothing at all, so silence read as "no collision". It now
states what it compared: per other running dispatch, how many paths it declared, that
it has not reported (structurally: ingesting a report moves a dispatch out of
`running`, so no set in the comparison has ever been checked against a diff), and how
stale its table is — `never-checked`, `reconciled`, or `unreconciled`, the last naming
the files an earlier report on that claim proved it is missing. With nothing else
running it says that too: nothing was compared, so the dispatch is unconstrained by
the check rather than cleared by it.

**And what the declared files usually drag in** (KYO-147.c2/c3). The brief's Touches
section also lists files that dispatches touching each declared file have historically
also touched and this record does not name — derived from the `touched_files[]` on
reports the store already holds, at generation time, refusing nothing. It is aimed at
the class of file no touches table predicts, because the coupling lives in the
*dependent* file: a generated mirror, a positionally-keyed index, a byte-identical
twin. Below its thresholds, or on a store with too little history, it prints **nothing
at all** — never a weak signal with a percentage attached to make it look like one.

**Where the declared symbols are, and where this project's tests have been**
(KYO-327.c2). Two answers the store could already give and did not, both aimed at the
first stretch of turns of a dispatch, which goes to finding what the record already
names. The Touches table states, per `path#symbol` touch, the **line that symbol was
found at when the brief was frozen** — through `sweep.symbol_regions`, the same reader
that judges touch drift, so the brief points at the lines the tool itself will judge
the work against — with a note that it is a starting point at the base sha and not a
current fact, since the worker's own edits move it. Beside it, a block naming the
**test files that past reported dispatches on these same paths also changed**, off the
same `touched_files[]` history the co-occurrence block reads (one query, so the two
blocks cannot describe two different histories). It is labelled history and explicitly
not coverage: it is where this project has been putting the tests for these paths, not
a claim that any of them exercises this change, and a path with no such history
contributes nothing.

**And the standing rules are one line each, not their essays** (KYO-327.c1). Every
worker-audience rule from `kyosei/procedures.md` still reaches every brief — that is
law 15 and it is tested — but as its title, a one-line `enforced-by` (the level
verbatim, plus the commands and test files the bullet cites) and the ledger's own
first sentence of reasoning. Nothing is paraphrased, so no rule's meaning can change,
and the argument behind each rule is one read-only command away: `kyo rules "<title>"`
prints one rule in full and `kyo rules` lists them. On one project's ledger the
section went from about 9.8 KB to 4.2 KB. The standing **stance** is not a rule and is
carried verbatim, as it always was.

The frozen brief is generated
entirely from the record — never hand-typed — per `templates/brief-template.md`'s
machine-filled slots, plus one orchestrator-written task section (`--task-file`).
`--brief-out FILE` also writes it to disk for handing to a worker, at the moment
this invocation freezes it — it is not a way to re-read a brief that already
exists, and running it against a record that already has a dispatch freezes a
second one (KYO-177). `kyo show <dispatch-id>` is the read-back path: it prints
a dispatch's frozen brief by the dispatch's own id, read-only, so running it
twice changes nothing.

A second dispatch of the same record is refused when it would land in the
worktree a running dispatch of that record already holds — the derived
`<worktrees>/<branch>` path makes that the default for a retry — with the running
dispatch named; pass `--worktree` for a tree of its own, or `--force` (KYO-411).

With `--branch` and no `--worktree`, `kyo dispatch` creates the derived worktree
itself (KYO-485): `git worktree add` in the code repository — never the store
repository — cutting a new branch at the base sha, or checking the branch out when
it already exists. It runs after every refusal and immediately before the dispatch
row is written, so a refused dispatch creates nothing, a failed `git worktree add`
is a Kyosei error carrying git's message with no dispatch recorded, and a failure
after the worktree exists removes it again. A path that already holds a worktree of
this repository on that branch is reused; any other directory there is refused,
naming `--worktree` and a different `--branch`. `--base <ref>` records the commit
the ref names as the dispatch's base sha (the brief states it) and cuts the branch
there, so a re-dispatch from a predecessor's unmerged branch reports a
`worked_from_sha` that matches. A branch that already exists needs no `--base`
(KYO-487): its worktree checks it out as it stands, so the dispatch records that
branch's tip as its base sha and the brief says so, rather than the code
repository's HEAD. `--no-create-worktree` records and prints instead.
`worktree_setup` is still printed, never run.
`--brief-out` is checked before anything is written, so a missing directory or an
unwritable file refuses with no dispatch created; a write that still fails after
the dispatch is recorded says which dispatch exists and not to re-run.

`kyo dispatch` itself no longer prints the whole frozen brief to the terminal by
default (KYO-326): it prints everything else it always did — warnings, the
drag-in summary, the disjointness lines, the worktree line — and, in
the brief's place, a length/digest line naming `kyo show <dispatch-id>` as where
to read it. The full brief used to come back on every call (about 35 KB for a
typical brief) and stay in an orchestrator's context to be re-read every
turn, unread. `--print-brief` restores the old full terminal print; `--brief-out`
and `--json` were never affected either way.

**What a worker leaves outside its worktree is named at its stop** (KYO-535). Once the
dispatch row exists, `kyo dispatch` writes a baseline into the code repository's git
common directory (`kyosei-dispatch-baselines/<dispatch-id>.json`, `outside_baseline` in
`--json`): the main checkout's untracked files outside `kyosei/` and `ideas/`, the
directories directly under `$KYOSEI_HOME`, and the roots its `projects.json` lists. A
stop the `SubagentStop` resolution places as that dispatch's worker is compared against
it by `kyo check-clean --subagent-stop`, and each new path is named with the command that
removes it (`rm`, `rm -r`, `KYOSEI_HOME=... kyo projects remove ...`), with `contract
worker-wrote-outside-its-worktree` (exit 2). It cannot tell who made a path, so the
stop that follows a blocked one (`stop_hook_active`) reports the same list without
blocking: a path the worker did not make, or cannot remove, costs it one wake. Every
other blocking finding of `kyo check-clean` and `kyo check-branch` does the same since
KYO-592: the first stop blocks with the full remedy, and a repeat stop reports the finding,
exits 0 and records the unblocked stop in the stop audit, which `kyo report` and
`kyo show <dispatch-id>` list under the dispatch. A
dispatch with no baseline says so rather than passing silently. Each `kyo dispatch`
removes the baselines of this store's dispatches that are no longer running. From
inside a running dispatch's worktree, `kyo init` and `kyo projects add` refuse the real
home (see "Where the store lives").

`kyo show <dispatch-id>` also prints **what that dispatch's worker cost**
(KYO-284): input, cache-creation, cache-read and output tokens, and the model
the transcript names — which is not the same fact as the `worker model` the
orchestrator declared beside it. The number is summed from the worker transcript
that the `SubagentStop` resolution (KYO-128/154) matched to this dispatch and
recorded in the events ledger (KYO-134); nothing here goes looking for a
transcript of its own, because a second guess at the harness's private file
layout would be a second answer to whose file is whose. A dispatch with no
matched, recorded transcript reads `unknown` with the resolution's own named
outcome as the reason, never a zero. `kyo worktrees --json` carries the same
answer on every row, which is where the desktop app's activity list reads it.

Three things about that sum are worth knowing before you read one (KYO-292).
**One assistant message is counted once, not once per content block**: the
harness writes a transcript row per block and repeats the message's whole
`usage` on each of them, so the rows are grouped by `message.id` and the last
row of each message is the one that counts — the first row's `output_tokens` is
a streaming-start figure. **The headline is `latest_turn_tokens`** — all four
counts of the LAST assistant message, cache reads included, which is how big the
conversation had become by the last thing the worker said. That, and not any
running total, is what claude.ai's agent card shows: measured on one finished
worker, the card read 189,568 where the last message read 187,726 and every
cumulative sum was already past it. It runs about a percent under the card,
because the transcript records that last message's output tokens mid-stream. The
four summed counts and the all-in total are carried beside it, as what the
worker used altogether. And **`kyo worktrees --json`
sums under a budget**, because it is on the desktop's five-second poll: the
budget is spent on the most recently *created* dispatches plus every running
one, and a row outside it says `not-summed-this-run` and names
`kyo show <dispatch-id>`, which is never capped.

**Beside the spend, one behavioural number (KYO-298): `context_rewrites` and
`context_rewrite_tokens`.** A worker that sits through a long foreground command
comes back to an expired prompt cache, and the harness re-writes its entire
context as cache creation on the worker's very next message. That message is the
whole cost of the wait and it is the only durable evidence the wait happened, so
it is counted: an assistant message after the first whose cache creation was
both over 50,000 tokens and at least 80% of that message's whole context, once
per message id. Those thresholds found exactly two such messages across the five
transcripts of one measured wave — 172,733 and 325,752 tokens, each straight
after a full-suite run — and none in the three workers that ran test files only.
Both fields are additive: every field these surfaces carried before means the
same thing beside them. They are the measurement behind
["The whole suite is the orchestrator's"](operating.md#the-whole-suite-is-the-orchestrators-controller_only_commands-kyo-298),
and the reason that policy can be re-checked per dispatch rather than believed.

Each row of `kyo worktrees --json` also carries **when that dispatch finished
and where that moment came from** (KYO-286): `finished_at`, plus
`finished_at_source` — one of `worker-stop` (the latest stop the audit
resolution matched to it, which is the closest thing recorded to its worker
stopping), `report-ingest` or `closed-at` — and `finished_at_why` saying it in a
sentence. A close or an ingest is an orchestrator's act and can land hours after
the worker stopped, so neither outranks the stop. `finished_at` is null on a
running dispatch, whose clock is genuinely still going, and null when none of
the three can date it, rather than standing in for the unknown with the current
time. `dispatches.closed_at` is written by `kyo dispatch close` and by nothing
else, so a dispatch that simply reported has none — which is why a consumer
measuring a run time from `closed_at` alone measures one that keeps growing.

`--terminated-no-report`, `--withdrawn`, `--why` and `--why-file` are meaningful
only with
`kyo dispatch close <dispatch-id>` (below); passed alongside a record id instead,
they used to be silently accepted and silently ignored while the command
dispatched a brand-new record anyway — the maximally unhelpful outcome for a
mistyped closure. It is now a usage error naming the correct form and, when the
record already has a running dispatch, that dispatch's own id (KYO-133).

`kyo dispatch <record> --also-covers ID [ID ...]` (KYO-13.c4) lets one dispatch
formally cover more than one record — each covered record's own criteria are
appended to the frozen brief under its own heading, and the covered ids are
frozen onto the dispatch (`dispatches.covers_json`) alongside it. Without
`--also-covers`, a dispatch covers exactly its own record, as before.

`kyo report <dispatch-id> --file .kyosei-report.json` validates the file against the report
schema `kyo report-schema` prints (required keys, verdict/method enums, the
void/unobserved-needs-a-reason rule, no unknown keys — implemented against the
stdlib, no `jsonschema` dependency), refuses loudly on a mismatched
`dispatch_id`/`record_id`, and stores every array (`criteria_verdicts`,
`premise_false`, `disagreements`, `corrected_numbers`, `commits`, and the
optional `docs_verdicts`) structurally.
`kyo report <dispatch-id> --file .kyosei-report.json --check` (KYO-477) is the
worker's dry run: every check the ingest runs before it writes — the schema, the
ids, the dispatch's state, `criteria_verdicts` scoping, the branch check over
`commits[]` — through the same functions, with nothing written. It exits non-zero
with the ingest's own message when the report would be refused, and it runs from
a dispatch worktree, whose store is the main checkout's opened read-only. Every
brief tells the worker to run it and shows a minimal filled report, which the
suite validates against the schema.
`criteria_verdicts` may name a criterion belonging to the dispatch's own
record or to any record it formally covers via `--also-covers` — anything
else is refused (KYO-12: a report cannot informally bundle a record its
dispatch never declared covering). Ingestion moves the dispatch
`running -> reported`; it never calls `kyo verify` — applying a verdict to a
criterion stays an orchestrator act, always. Since KYO-198 that act can be one
command: `kyo verify --from-report <dispatch-id> [--only <criterion-id>...]`
applies the report's own `criteria_verdicts[]`, method and evidence included,
and prints every entry it did *not* apply (a `void-*` verdict, which stays a
premise judgment made by hand, or a criterion restated since the report was
written) with the command that would.

A report may also carry **`docs_verdicts[]`** (KYO-199): one entry per governed
document section the branch's diff raises an obligation against, keyed by
document path and section heading — the obligation ids do not exist until this
ingest mints them. `kyo report` matches each entry to the obligation it raised,
prints the match beside that obligation, and names any entry that matched none.
In a document declared `numbered:` in `documents.yml`, an entry whose heading
differs from the document's after the number is matched by its section number
(`[205. …]` → section 205) — the one key the worker and an append-only document
share — and is named as "matched by number" wherever it is printed, so the
differing heading is read before the verdict is trusted (KYO-393). A number
another entry already answered is a duplicate, never applied.
Applying them is the same shape as the criteria: `kyo docs settle --from-report
<dispatch-id> [--only <obligation-id>...]`, an orchestrator act with the
orchestrator's name on every row. The worker sees exactly what will be raised, in
its own worktree and without a store, by running `kyo docs preview --against
main`. In a project that governs its RECX.yaml, `kyo report` also warns, without
refusing, for each `module <id>` section the entries answer that no
`Recx-Checked` trailer on the branch tip names (KYO-614; see "RECX.yaml as a
governed document" in docs-governance.md).

**Accepting a report: `kyo accept <dispatch-id>`** (KYO-331). Applying one
report was seven commands and about ten reports a session, and a step was easy
to drop — a doc-ref obligation on KYO-263 and a drift correction on KYO-314 each
needed a second pass. `kyo accept` runs three of them as one act: `kyo verify
--from-report`, then `kyo docs settle --from-report`, then `kyo edit
--touches-from-drift` on the dispatch's own record. It is a composition and not
a second implementation — each step is reached through the same parser and the
same handler the typed command uses, the argv is printed beside the step's own
output, and a refusal any of them makes is made here in its words. So it applies
nothing they would not: a `void-*` verdict, a criterion restated since the
report was written, an obligation that already carries a verdict and an entry
matching no obligation are each reported and left, exactly as the separate
commands leave them. When any docs verdict matched no obligation, the **last
line** `kyo accept` prints says how many and names `kyo docs settle
--from-report <dispatch-id>`, which lists them again and applies nothing twice;
and `--close`, left short over open docs obligations, names the unmatched
verdicts on those same documents beside the refusal (KYO-393).

Two things it will not do on its own. It **refuses while the report carries
`premise_false[]` or `disagreements[]` entries**, printing every field of every
one of them, until `--reviewed` says you have read them — reading a worker's
account before applying it is the one judgment in the sequence, and the flag is
never defaulted. And it **writes no gate**: for each covered record left with an
unobserved criterion that no open `awaiting:*` gate and no running dispatch
makes findable, it prints the exact `kyo gate` line (the same one `kyo verify`,
`kyo brief`, `kyo next` and `kyo doctor` print) and leaves the writing to you,
because a gate carries an instruction to a person.

`--close` then moves each covered record whose criteria are all settled to its
completion status, through the ladder's intermediate rungs — `open → working →
built → verified → done` for the default registry, under whatever names
[your own status registry](registries.md#the-status-registry-and-why-contracts-key-off-roles)
declares — and says which records it left and why, quoting the contract that
refused each one. It is opt-in for the reason `kyo report` never applies a
verdict: deciding a record is finished is a judgment made with the whole board
in view.

The steps commit one at a time (SQLite does not nest a transaction, and each
composed command opens its own), so a refusal in a later step leaves the earlier
ones applied — and a second run is safe, which is what makes that acceptable:
`verify --from-report` validates its whole batch before writing any of it and
skips every entry whose criterion's latest verification already came from this
report, listing each as *already applied* (KYO-415), and every entry whose
criterion's latest verdict was recorded after the report was ingested — your own
`kyo verify` after reading it — listing each as a *newer verdict kept*, so a
rerun never puts the report's older verdict back over yours (KYO-511); `--again`
applies both kinds anyway. `docs settle --from-report` never overwrites a verdict, the drift correction
replaces the same list with itself, and `kyo move` onto a status a record
already holds is a no-op that says so. One refusal is expected often enough to
be caught rather than raised: after the branch is merged, a report carrying no
`touched_files[]` leaves nothing to compare, so `kyo edit --touches-from-drift`
refuses with `touch-drift-apply-needs-a-finding` — that is a fact about the
dispatch, so it is printed as a step marked REFUSED and the rest of the act
still runs.

**A partial acceptance still exits 0, so read `complete`** (KYO-415). A refused
step, or a covered record `--close` left short of completion, does not change the
exit status. What says so is `complete` in `--json` — false when any step was
refused or, under `--close`, any covered record was left open (a step skipped for
nothing to compare does not count, nor does a record already at completion) — and
an `INCOMPLETE` line under the headline naming the refused steps and the records
left open. A script that keys on the exit status alone reads a partial acceptance
as a whole one; key on `complete` instead.

**Confirming a person's look: `kyo confirm`** (KYO-494). Closing a human gate
was three commands every time — `kyo verify` the criterion `human-observed` with
the maintainer's words as evidence, `kyo ungate`, `kyo move` (46 human-gate
clearings measured across two stores, 39 followed by a move within fifteen
minutes) — and approving a proposal is the same act with no gate in it.
`kyo confirm <record-id> --quote-file q.txt` (or `--quote TEXT` for one line,
`--quote-file -` for stdin) is that act, composed the way `kyo accept` is: each
write is `kyo verify`, `kyo attach`, `kyo ungate` or `kyo move`, reached through
the same parser and handler, printed with its argv, refusing in its own words.
It records `met` with method `human-observed` and nothing else; there is no
`--verdict` or `--method` flag. A "no" is `kyo verify <cid> --verdict
unmet-unbuilt`, a measurement the orchestrator took is `machine-checked` and stays
`kyo verify`, and a gate that is not waiting on a person is cleared with
`kyo ungate`.

Everything is resolved before anything is written. The criteria the words answer
come from the first rule that applies: `--criterion <cid>...` names them
exactly (each this record's own and not void; an `unmet-unbuilt` one is allowed
when named, which is how a proposal's decision criterion is confirmed); else
every full-form id (`KYO-12.c4`, never a bare `c4`) in the in-scope human
gate's payload that belongs to this record, another record's id being ignored
and said so; else the record's sole `unmet-unobserved` criterion; else nothing
is verified and, with a human gate in scope, the words are attached to the
record as evidence (`kyo attach <record-id> --file … --supports <record-id>`).
**Only `awaiting:human-review` and `awaiting:human-run` gates clear**:
`awaiting:event` waits for something to happen rather than for a look, and
clearing `holding-for` raises `gate-cleared-stale-criteria`, which wants a
deliberate `kyo ungate`. The in-scope gate is the record's one open human gate,
or the one `--gate <gate-id>` names when several are open, and it clears only
once every criterion its payload names is settled.

Each refusal writes nothing, exits non-zero under its own contract name and
prints the ready `kyo confirm` line(s) that resolve it:
`confirm-ambiguous-gate` (several open human gates and no `--gate`),
`confirm-ambiguous-criteria` (several `unmet-unobserved` criteria and nothing
naming any — one approval applied to observations nobody named is a rubber
stamp), `confirm-nothing-to-confirm` (no human gate, no unobserved criterion),
`confirm-record-retired`, `confirm-dispatch-running` (a look taken while a
worker is changing the thing is a look at a build about to change; the same line
runs once the report is ingested), `confirm-criterion-not-confirmable` (another
record's, void or missing), `confirm-gate-not-confirmable` (missing, another
record's, already cleared, or not a human gate) and
`confirm-gate-names-a-missing-criterion` (correct the payload with `kyo redo`).

**The words are stored verbatim in evidence, and not in the gate's clearing
reason.** The quote is read once, its length and sha256 echoed on the first
line, and each step is handed a temporary file holding exactly those bytes.
Nothing is prefixed — a frame the tool wrote would be the tool's words stored as
evidence — so write the file in this shape yourself: who said it, when,
where, then the words verbatim. Several criteria each get their own evidence
artifact sharing one sha256. The gate's `cleared_why` is a one-line pointer to
that evidence ("confirmed by kyo confirm: KYO-12.c4 met (human-observed),
evidence <evidence id>, sha256:…"), because a clearing reason is a place
`kyo redact` can only name, not rewrite, and evidence is where it can scrub.

**The completion move is the default.** Unless `--no-close`, confirm walks the
record up the ladder's rungs to its completion status — the walk `kyo accept
--close` makes — and an unsettled criterion or any open gate of any type left on
the record stops it, the reason printed. It is the default here, and opt-in on
accept, because the maintainer's approval is the judgment `--close` waits for.
Pass `--no-close` for a decision gate on work that is not finished: the words
are recorded and the gate cleared, and the status is left alone. As with accept,
the steps commit one at a time, a partial result exits 0 with `complete: false`
in `--json` and an `INCOMPLETE` line, and a second run with the same words
applies nothing twice.

**A resumed worker's corrected report** (KYO-90). The workflow this loop sits
inside resumes workers: a report is ingested, the branch is merged, the merged
tree turns out to be wrong, and the same worker goes back to the same branch. A
second report that declares nothing is still refused (`dispatch-already-terminal`
— a different account landing silently over the first would make the
frozen-brief-and-report pairing worthless), but a report carrying
`"amends": "<report-id>"` at its top level is ingested as a **new, current**
report, and the one it names becomes **superseded**: same id, same ingest time,
every word intact, and `kyo show` says which of the two is the account of what
happened. The declaration is a field in the report rather than a flag on the
command because the party who knows the first report was wrong is the worker,
who cannot type the orchestrator's flags — and because it names *which* account is
being replaced, so an `amends` pointing at anything but the dispatch's current
report is refused (`report-amends-wrong-report`) rather than reconciled. The
point is not only that the corrected report lands: every consumer re-runs over
it — schema validation, the branch check that still refuses a commit not
reachable from the declared branch, and touch drift over `touched_files[]`.
Nothing is reopened: `state` stays `reported`, `closed_at` stays whatever it
was, and `kyo doctor`'s `report_amendment_chain` check keeps a dispatch's
reports one chain ending at the current one.

`kyo dispatch close <id>` refuses (exit 2) to close a dispatch with no ingested
report unless one of the two report-less states is named explicitly, rather than
letting a dispatch that produced nothing look like anything finished. A
dispatch's `state` is always exactly one of `running`, `reported`,
`terminated-no-report`, `withdrawn` — never a value that reads like "done".

`--terminated-no-report --why ...` records law 8's loud, distinct terminal
state: a worker was spawned and the work STOPPED. `--withdrawn --why ...`
(KYO-345) records the other thing that ends a dispatch with no report — it was
never handed to a worker at all, which is what a brief frozen with the wrong
model, branch or record is. A frozen brief is immutable by design, so before
this state the only exit from such a mistake was to file a death that had not
happened, on a dispatch every later brief then told an orchestrator to flag. The
two are the same in what they require (a `--why`, which is the whole account of
what went wrong) and in what they release (the worktree and the touch claims);
they differ in volume. A terminated dispatch stays in every `kyo brief` until
its record is answered; a withdrawn one is listed in no brief at all, and
`kyo show <record-id>` prints it in the record's dispatch history under its own
word. `kyo doctor` holds both to the same invariant: a state that means "no
report" implies a recorded why, and implies no `report_id`.

`kyo split <record> --to-successor <criterion-id>...` refuses a met or already-void
criterion; for each named (necessarily unmet) criterion it creates a new
criterion on a fresh successor record (same kind, same text, same state, new id),
copies the original's touches onto the successor, links the successor back to
the original (`produced-by`), and marks the originals `void-withdrawn` with an
auto reason naming the successor — supersede, never delete (law 4).

## Closing a wave

governs: src/kyosei/cmd_close_wave.py

`kyo close-wave <record>...` (KYO-536) is the sequence an orchestrator runs after a wave's
branches are merged, in the order that keeps the generated documents true: it moves each
record to its completion status through the ladder's rungs (through `kyo move` itself, so
each rung meets the same transition contracts), runs the project's declared generators,
commits the store's export and every file the generators changed in one commit, and then
prints the declared after-steps. Before the moves, the changelog would be regenerated
from records not yet closed — the order is the point.

Which commands are the suite, the generators and the after-steps is the project's to
declare, under `close_wave:` in `kyosei/config.yml`:

```yaml
close_wave:
  suite:                # each must have a recorded pass at this code
    - make test
  generate:             # run in order from the code root, after the moves
    - kyo changelog --all --output CHANGELOG.md
    - run: kyo reference --output docs/reference/cli.md
      note: the parser's reference, held to the parser by a test
  after:                # printed every time; run only with --run-after
    - make install-tool
    - kyo serve --restart
    - kyo worktrees --prune
```

Each `generate`/`after` entry is a command line or `{run, note}`, as in `worktree_setup`.
Without `suite`, the requirement falls back to `orchestrator_only_commands`: any one of
them passing at this code satisfies it, and the output names that rule. A malformed
`close_wave:` refuses `kyo close-wave` and nothing else.

**It runs no suite.** It refuses unless the declared suite PASSED at this code, and that
fact is recorded by chaining on the suite's own exit status:
`make test && kyo close-wave --suite-passed "make test"` (the command may carry arguments,
`make test LOG=/tmp/t.log`, and is matched the way `kyo check-command` matches it). The
pass is written to `kyosei-suite-passes.json` in the checkout's git directory — never
tracked — with the HEAD it passed at and a fingerprint of HEAD's tree outside `kyosei/`
and `ideas/`, so a store-only commit leaves it standing and any code change makes it
stale. Recording is refused while anything outside those two directories is uncommitted:
a suite that ran on uncommitted code vouches for no commit. The `&&` is the whole
mechanism; Kyosei never sees the suite's exit status, so a pass typed without running
anything is recorded like any other.

**Refused before any write**, every blocker at once, under `contract close-wave-not-ready`:
a record whose criteria, open docs obligations or open gates would refuse its completion
move (`record-not-closable`, in the move's own words), a suite with no pass at this code
(`suite-not-passed`, naming the line that records one), and uncommitted changes outside
`kyosei/` and `ideas/` (`code-not-committed`). `--dry-run` prints the whole plan —
blockers, moves, generators, the commit and the after-steps — and writes nothing, the
pass `--suite-passed` would record included. `--run-after` runs the after-steps in order
and stops at the first that fails. A generator or after-step that fails stops the
sequence there (exit 1, `INCOMPLETE`, naming what already happened); rerunning is safe,
because a record already at completion is left where it is and the commit stages only
what changed. Under the split layout the store's export is committed in the store's own
repository and the generated files in the code repository, with the same message.

**A record completed outside a wave** (KYO-577) would otherwise leave the generated
documents behind the store until the next close-wave, and a suite that checks them fails
first, so `make test && kyo close-wave ...` stops at the suite. Three things keep the
documented sequence whole:

- `kyo confirm`, when it moves a record to completion, runs the `close_wave: generate:`
  lines (in order, through the shell, from the code root) and commits their output with
  the store's export, message `store: <id> done; generated documents regenerated (kyo
  confirm)`. A project declaring no generate steps gets no command and no commit. The
  steps run only when nothing outside `kyosei/` and `ideas/` is uncommitted; when they
  cannot run, or one fails, the confirmation stays applied, nothing is committed, the
  output says `INCOMPLETE` (exit status unchanged) and names the line that finishes it.
  A typed `kyo move` into or out of the completion status, and `kyo accept --close` once
  for the records it closed, do the same (KYO-603); the move's exit status and output are
  otherwise unchanged, and its `--json` gains `refresh` only when generate steps are
  declared. The moves `kyo close-wave`, `kyo confirm` and `kyo accept` compose do not
  regenerate individually; each of those commands regenerates once for the whole act.
- That line is `kyo close-wave <record>` for a record already at completion: a run in
  which every named record is already complete moves nothing and closes nothing, so it
  needs no suite pass. It regenerates and commits, and says why no suite was required.
  Every `suite-not-passed` refusal names it, with the most recently completed record, for
  the routes that did not regenerate (any of the above run over uncommitted work, or an
  edit that restates or rescopes a completed record).
- A recorded pass is carried across a commit close-wave, confirm, move or accept made
  from a clean tree. That commit's paths outside the store are generator output only, and close-wave
  regenerates them after its moves without a suite run anyway, so those paths, across
  that commit, are exempt; a pass that was already stale before it stays stale. The
  carried pass names the commit and its paths when close-wave reads it.

## Sweep, next, and the session briefing

governs: src/kyosei/sweep.py, src/kyosei/brief.py

`kyo sweep` flags — it never edits a record (charter: "flags, never
auto-edits"). Only records whose kind is `archetype: work` and carries
`policy.swept: true` are ever considered (the field's promised full consumer).
Four flag classes: three matching KYO-8.c1/c2/c3 exactly, and a fourth,
`description-predates-claim`, added by KYO-137.c7
(`docs/design/charter.md`'s "Sweep" section names several more — claims aged
past N days, doc-`governs:` staleness, moved-but-inverse/moved-but-not-merged
— none of which has an acceptance criterion on this milestone's record, so
none is built here):

- **touch-changed** — a `sweep_runs` row records the git sha each sweep ran
  at, *and which code history that sha belongs to*; the *next* sweep diffs
  from that sha to the current `HEAD` and flags any record whose declared
  touches the changed files include — a directory touch (`apps/desktop/`) when
  anything *under* it changed, and a new-file touch (`+src/x.py`) when that file
  changed and the change was not the file's own creation, which is the record's
  declared output arriving rather than the ground moving under it (KYO-225).
  A record whose every touch on a changed file names a `#symbol` is flagged only
  when the change reached one of those symbols' regions, and the flag names the
  symbol rather than the file (KYO-300); a plain-path touch is flagged exactly as
  before, and a symbol the textual approximation cannot place counts as changed.
  The regions are placed in the file as of the sweep's current sha, not the
  working tree, whose uncommitted edits would shift them off the committed hunks;
  a file that cannot be read at that sha counts as changed (KYO-406).
  When it cannot — no git repo, no
  earlier base, a base sha this clone does not contain, or a different code
  repository entirely — the class produces nothing, and the run says so
  rather than letting the empty result speak for it (see "Staleness
  coverage" below).
- **touch-missing** — a declared touch that no longer resolves to a real file
  under the project root. A directory touch is never flagged this way: its whole
  meaning is that the tree may not exist yet, so the reading "the file you
  declared is gone" does not apply to it (KYO-209). Nor is a new-file touch, for
  the same reason one scale smaller (KYO-225). A record carries at most one open
  touch-missing flag: a later sweep that finds a different missing set restates the
  standing flag in place, as touch-changed has widened since KYO-69, and so does
  `description-predates-claim` when a newer claim arrives (KYO-418).
- **near-duplicate-criteria** — unmet criterion text that collides, at a high
  `difflib.SequenceMatcher` ratio over normalised (case-folded,
  punctuation-stripped) text, with another record's unmet criterion text.
  Tuned for precision over recall: short texts (under 16 normalised
  characters) are never compared, and the threshold is 0.90 — "a false
  'duplicate!' warning trains people to ignore it."
- **description-predates-claim** (KYO-137.c7) — a record whose description has
  not been edited since a live claim on it was recorded. Advisory and store-only:
  a prompt, never a refusal, since a newer claim may confirm the description word
  for word. A flag resolved with `kyo flag resolve` stays resolved while neither
  half moves: the next sweep re-raises it only when a newer claim is filed, or a
  claim is filed after a description edit (KYO-529). Which flag types honour a
  resolution: `description-predates-claim` and `near-duplicate-criteria` do (a
  resolved near-duplicate pair stays resolved until either criterion's text is
  edited or restated, KYO-532); `touch-missing` does not, because a missing file is
  a standing fact that stays a real problem until the touches are corrected, so a
  resolved finding that persists is raised again.

`kyo new`/`kyo promote` run the same near-duplicate comparison at creation
time, against the existing corpus of still-active records, and print a
warning without ever refusing to create the record (KYO-8.c4) — the *next*
`kyo sweep` is what turns the same overlap into a durable, resolvable flag,
once the new record actually exists to attach one to.

### Staleness coverage — "no flags" is not the same as "nothing is stale"

governs: src/kyosei/cmd_{sweep,flags,next,brief}.py

Sweep's base sha is a commit in the **code** repository, while the row holding it
lives in the **store** repository. Under the split layout (the `kyo init` default)
those are two repositories whose histories advance independently, and nothing
guarantees that a later clone of the code contains the recorded sha: it may be
shallow, a fork, an unmerged branch, or a history that was rewritten. A sweep that
cannot diff creates no `touch-changed` flags — which is byte-for-byte what a
project with nothing stale looks like (KYO-63).

So every recorded run records **`coverage`**: whether the staleness question was answered,
and if not, why not. It is `swept` only when the diff actually ran; otherwise
`baseline` (first run with a base to record), `no-git`, `base-unresolvable` (the
recorded sha is not a commit this code repository has), `foreign-history` (this is
not the repository the last run swept — root-commit identity is recorded per run
and compared), `diff-failed`, or, read from a store rather than a run,
`never-swept` and `unrecorded` (a row written before schema_version 10).

A run with nothing new to say is not recorded at all (KYO-418): at the same base sha,
code history and coverage as the last recorded run, filing and widening no flag, it
writes no `sweep_runs` row, no event and no export file, and says `not recorded — same
base as <that run>`. The Stop hook sweeps after every assistant turn, and
recording each of those dirtied the export to say what the last recorded row already
says; that row is still what the next sweep diffs from and what `kyo brief` reads.

When it cannot resolve its own base, `kyo sweep` **flags nothing for that class and
says so** — in its output, in `--json`, in `kyo brief`'s "staleness coverage"
section, and durably on the row. It does not refuse (that would discard the two
flag classes which never needed a base, and record nothing at all, in a command
run from a session-stop hook) and it does not fall back to a full scan (that
asserts every declared touch changed, which the tool does not know, and routine
false positives are how an advisory check gets ignored). The run re-baselines at
the `HEAD` it can see, so the *next* sweep answers staleness again; the window it
could not read stays unread rather than being counted as clean. Any surface
rendering staleness therefore has three states to paint, not two — `stale`, `not
stale`, and `could not tell`.

**A fourth flag class, not raised by sweep.** `kyo ungate` flags the record when a
`holding-for` gate clears on a record carrying criteria older than the clearing
(`gate-cleared-stale-criteria`). A gate's meaning is "this record is waiting on
something that will change what it should be", so clearing one always implies
something just changed; the test is `criterion.created_at` against the gate's
resolution, with no text analysis in it, and only still-unmet criteria are named
(a settled one directs nobody). Only `holding-for` raises it: the other three gate
types clear when something *happened* or someone *looked*, and a flag that fires on
every clearing becomes the routine false positive nobody reads.

`kyo flag resolve <flag-id> [--why]` is this build's own acknowledge/resolve
design for a flag: it records who resolved it and when, and never re-runs or
silently clears the condition that produced it. An already-resolved flag
refuses a second resolve (exit 2); sweeping again after a resolve, with the
condition still true, creates a *new* flag rather than reopening the old one.

`touch-changed` is the one flag class that *supersedes* rather than accumulates
(KYO-69). Sweep runs on every session-stop hook, and before this it filed a new
flag every run for every open record whose declared touches the diff touched — so
the count grew with the number of sweeps, not the number of problems (75 unresolved
flags in one store, all of one type, nine on one record, each true and
all of them the same finding). A sweep that re-observes a record's open
`touch-changed` finding now widens the standing flag instead: same start sha, new
end sha, union of files, sweep count + 1. `kyo flag collapse` is the one-off
migration for a store that accumulated flags before that — it merges each record's
duplicates into its earliest flag and closes the rest with a `resolved_why` that
names the survivor, so the surviving flag says everything all of them said. It is a
dry run without `--apply`, and idempotent: after one apply the plan is empty.

**A flag outlives the record it was raised under only until that record settles**
(KYO-346). Moving a record to its kind's completion status, `kyo wont-do`, and
`kyo void` each close, in the same transaction as the move, that record's own open
`touch-changed`, `touch-missing` and `description-predates-claim` flags — a
completed or retired record has no open work left for one of those to ask about —
recording a `resolved_why` that names the move and the actor. `kyo accept --close`
inherits this for free: it walks a record to completion through `kyo move`.
`near-duplicate-criteria` and `gate-cleared-stale-criteria` are left open
regardless of the record's status: the first is a fact about the criteria's text
and the second a judgement about an unobserved criterion after a gate cleared, and
neither is answered by the record having moved. `kyo flag resolve --settled --why
<reason> [--apply]` is the one-act repair for a store that accumulated the first
three under already-settled records before this existed — dry run by default, on
`kyo flag collapse`'s own terms, printing the count closed per record and per type.

`kyo brief` splits the unresolved `touch-changed` flags by whether the record has
any dispatch history, and prints both counts even at zero. Firing for a record that
has never been dispatched is deliberate — a touch list declared long ago can simply
be wrong, and a sweep that declines to look never gets that window back, because the
next diff starts from the base this run recorded — but the two mean different things
and are ranked accordingly: "the ground moved under work that was actually done"
above "files this record names have been changing while it sat open".

`kyo next` lists every record that is dispatchable (`policy.dispatchable`),
ungated, has criteria if its kind requires them, is not already carrying a
running dispatch, and whose touches are disjoint from every other *running*
dispatch's touches — the exact readiness test `kyo dispatch` itself applies,
run in bulk.

One class is withheld from that list and rendered beside it under `finished but
not moved`: a record that has criteria, every one of them **settled** (`met` or
void), has nothing left to build, whoever built it, and offering it as
dispatchable is how one project's "dispatchable now (31)" came to hold 16
finished records (KYO-87). The class is derived from the criteria alone. It
does not ask whether a dispatch reported — until KYO-289 it did, which excluded
exactly the work nobody dispatched (built in-session, or verified by the
maintainer by hand) and left 23 of one store's 28 "ready" records sitting
in the list an orchestrator reads *instead of* the records; a dispatch that
formally spoke for the record is named on the row (`reported_by`, an empty list
when there is none) rather than deciding the class. Each row names the move that
clears it — the verified rung, or the completion move for a record already
standing at verified (KYO-173) — and no surface performs it, because a status
change fires transition contracts and stays the orchestrator's act.
`kyo dispatch` refuses such a record with `contract dispatch-record-finished`,
naming that same move, off the same function the listing filters with, so the
surface an orchestrator reads and the surface it acts on cannot disagree about
whether a record is finished (KYO-295); `--force` dispatches it anyway and the
echo says what it overrode. Two boundaries hold in the other direction: a record
with any criterion still unsettled stays ready whatever its dispatch history
(carrying `prior_dispatch` when one reported), and a record with no criteria at
all is never in the class, because an empty set must not be able to assert that
work is done.

A second class is withheld under `blocked by an unsettled record (NOT ready)` (the
brief's `blocked, NOT dispatchable now`): a record with an incoming `blocks` link whose
source is not settled on the status ladder — settled means the ladder's complete or
retired roles, never the literal `done`/`wont-do`, and a gated source is unsettled
(KYO-570). Each row names its blockers and their statuses, the count prints at zero, and
`kyo next --json` carries the group under `blocked`, each row's `blockers` holding the
ids. Only incoming `blocks` edges count; no other link type affects readiness. A record
that is also gated is withheld by its gate and not listed again here, and a finished
record goes to `finished but not moved` first. When every blocker settles the record is
ready again; `kyo dispatch` refuses a blocked record off the same function (see "The
dispatch loop").

Disjointness has three outcomes, not two. A record that declares **no** touches
is not disjoint from anything; it is a record about which nothing can be
computed, because set intersection cannot tell "I have not said what I touch"
apart from "I have said, and it is independent" — the empty set intersects
nothing. So while any dispatch is running, such a record is listed separately
under its own count, as `record(s) with unknown scope (NOT ready)`, naming the
running dispatch it could not be compared against; and `kyo dispatch` refuses it
with `contract dispatch-scope-unknown`, off the same computation, so the two
surfaces cannot disagree about what is safe. Declaring the scope (`kyo edit <id>
--touches-file FILE`) clears it; `kyo dispatch --force` dispatches anyway and
records the unchecked comparison on the dispatch, next to a forced touch
collision. With nothing running there is nothing to be disjoint from, so an
unscoped record is simply ready — zero-ceremony intake is untaxed in the quiet
case.

Overlap is measured at the file, with one refinement: two records naming the same
file at `#symbol`s that do not overlap are **disjoint**, and both surfaces read that
the same way, off the same computation (KYO-147.c5). The refinement applies only
where *both* sides were precise — a plain path claims the whole file and collides
with every symbol in it, and a `dir/` touch claims its whole tree — so it narrows
where the declarations are specific and changes nothing where they are not. `kyo
dispatch` states each narrowed overlap rather than passing silently; see "The
dispatch loop". The one other overlap both surfaces let through is a candidate's `+path`
new file under a running dispatch's `dir/` (KYO-395), stated as a warning.

`kyo brief [--compact]` renders the session briefing entirely from the store:
record counts by kind, `kyo next`'s ready list, gated records with their
condition, the reminders, running dispatches, and every UNANSWERED terminated-no-report
dispatch — law 8's loud state has no separate acknowledge step, since
`kyo dispatch close --terminated-no-report` already sets `closed_at` in the
same call that sets the state, so a terminated-no-report dispatch has nothing
to clear it by hand. What drops it off the list instead is derived (KYO-314):
its record reaching a settled status, or a LATER dispatch speaking for the
record (its own, or one naming it with `--also-covers`) and reaching
`reported` — the same "speaks for the record" relation "finished but not
moved" already reads. The brief states how many it found answered this way
and did not list; `kyo show <record-id>` still prints a record's whole
dispatch history, including an answered one. Then the last sweep's staleness
coverage (KYO-63 —
printed immediately above the flag list, because an empty flag list means both
"nothing is stale" and "the check could not run"), unresolved sweep flags, the
shift log written
since the previous brief, and every event since the previous brief. The last
two are bounded by a `brief_runs` row's own recorded cursor (`last_event_seq`,
`last_shift_note_seq`) — a design constraint, not an implementation
convenience: "what changed since I last looked" must never be a git diff.
Last, only when something differs, a block naming each packaged asset (the
`/kyosei` skill, the hooks, the procedures ledger, rule-wise) whose copy in
this project has fallen behind this build's, with the remedy command beside
each — the same drift comparisons `kyo doctor` uses, read here at session
start rather than only at the Stop hook (KYO-213).

The **reminders** section (KYO-497; headed `carried` until KYO-521) sits directly after
gated work. It lists every record of a kind whose policy carries `carried: true` (the
default `reminder` kind; see "The kind registry") and whose status has neither the
`complete` nor the `retired` role, oldest first, each as `<id>  <n>d  <title>`, where `n` is whole 24-hour periods since the
record's tool-stamped `created_at`, rounded down — computed by the same helper as the
gated list's `open <n>d`, at read time, and never stored or typed. A reminder with
an open gate ends its line with `[waiting: <gate-id> <type>, open <n>d]`, and is still
listed under gated work as well. `--json` carries `carried[]`, each with `id`, `kind`,
`title`, `status`, `since`, `age_days` and `open_gates[]` (`gate_id`, `gate_type`,
`opened_at`, `age_days`), and `carried_kinds[]`, the registry's carried kinds; when there
are none, the full brief says so in one line rather than printing an empty list. This is
the list a closing note used to re-type by hand with day counts that drifted, so a
closing note no longer needs to carry it. The `--json` keys keep the policy's word,
`carried`; only the heading took the kind's name. Settle an item with `kyo move <id> done --why
"..."`, which also clears its gates.

`--compact` is the form the packaged `SessionStart` hook injects, and it is
shorter in two ways (KYO-419). Every count-section with nothing in it (scope
unknown, finished but not moved, built awaiting verification, unobserved waiting
on a person, reminders, running dispatches, terminated-no-report) is named on one shared
`empty (0 each):` line rather than given a heading and a "(none)" of its own, so
"checked, and empty" is still on the page. The per-section
explanatory parentheticals are dropped, the unobserved section's remedy sentence
included. `kyo brief` without `--compact` prints all of it, and `kyo rules
"<title>"` prints one rule verbatim.

Under both forms each orchestrator-audience rule prints as one line, under the
store's own record-link example: its title and its enforced-by kind in a word
(`mechanism` or `aspirational`), then one line naming `kyo rules "<title>"` for the
rest (KYO-531). Where `onboarding:` in `kyosei/config.yml` records setup decision E4 as
`declined` (the maintainer does not read the orchestrator through the desktop app), the
brief prints neither the record-link example nor the `kyosei://` citation rule; `--json`
carries `link_example: null` (KYO-561). A rule added since the previous brief in this project prints in
full instead, marked `NEW` and naming the brief it was compared against. A rule whose
text changed is marked `CHANGED` the same way but not reprinted (KYO-548): under its
title one line says what changed, and names `kyo rules "<title>"` for the full text.
When the change is word substitutions only, as an upstream rename is, that line names
them (`controller -> orchestrator (x3)`); otherwise it names the first edit, trimmed
(`added "..."`, `removed "..."` or `"..." -> "..."`), and how many more there are.
Each brief's own event records a digest of every rule's
normalized text, so re-wrapping a paragraph is not a change, and the next brief
compares against the latest session-start brief that carries digests (the latest
brief of any kind only when no session-start brief has any). So a brief run by hand,
by `--json` or by a viewer through `kyo serve` mid-session does not use up the
marker: the next session's opening brief still shows the change, as the build notice
does. A brief from an
older build recorded none, so the first brief after an upgrade marks nothing and
says so. The text a summary is measured against is in the same ledger: a brief event
also records the normalized text of each rule whose digest no earlier brief recorded
text for, once per wording rather than once per brief. A baseline written by a build
older than KYO-548 has digests but no text, so a rule changed since it is marked
`CHANGED` with "what changed is not recorded" and no summary. `kyo brief --json`
carries every rule's full text as `text`, and a changed rule's summary as
`change_summary`.

The build notice, the db-behind warning, the
handoff note, asset drift, dispatchable, gated and reminder rows, and the flag counts
print in full under both (a reminder row drops only its status under `--compact`).

`kyo note --shift --file f` appends one entry to a continuously-written shift
log (`shift_notes`), at the moment of the thought — never authored
retrospectively at closeout. The next `kyo brief` surfaces everything written
since the previous one and then advances its own cursor, so a note is shown
exactly once, in the first brief after it was written.
