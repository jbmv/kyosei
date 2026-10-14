# kyosei/procedures.md — standing rules

This file is the project's standing-rules ledger. It is not read whole by anyone: brief
generation (`kyo dispatch`) injects only `audience: worker` rules into the frozen dispatch
brief, and the session brief (`kyo brief`) injects only `audience: orchestrator` rules.
`audience: human` rules are shown in `kyo brief`'s human-facing sections, never injected
into a worker's context. This is Law 15 — audience is a first-class property
of every rule, because the reading budget is the binding constraint on documentation: a
worker that loads a project's whole instructions file reads mostly text it cannot act on.
A rule that lives where its audience does not look is not a rule.

## Rule format

One rule per `###` section, in this shape:

```markdown
### <short imperative title>

- **audience**: worker | orchestrator | human, or several, comma-separated: worker, orchestrator
- **enforced-by**: <the mechanism that fails when this rule is broken> | aspirational

<one to three sentences: what to do, and — briefly — the measured failure that justifies it.
Cite the incident or design law behind it if there is one. This prose is the rule's *reason*,
not the rule itself; the reason is allowed to be long-winded, because a brief carries only the
title, the one-line `enforced-by` and the reason's first sentence, never the whole of it.>
```

**One rule may name several audiences**, comma-separated, and it then reaches each of them:
`- **audience**: worker, orchestrator` puts the same rule in every dispatch brief and in the
session brief, so a restriction both must follow (what must never enter the store, say) is
written once rather than twice.

**`enforced-by` is not decoration (Law 1).** If a rule has real machinery — a hook, a CLI
write-path guard, a schema-required field, a transition contract — name it. If it does not
yet, write `aspirational` verbatim. An aspirational rule is still worth stating (a rule
someone is expected to follow by discipline alone is real information about what the tool
doesn't yet check), but it must never be typeset as though it were enforced, and a rule
should graduate out of `aspirational` the moment a mechanism ships for it — that's the
signal `kyo doctor`'s own test suite should pick up next.

**What reaches a brief is a trim, and the two briefs trim differently.** A worker's dispatch
brief carries each worker-audience rule's title, its one-line `enforced-by` and the first
sentence of its reasoning. The orchestrator's session brief carries each orchestrator-audience
rule's title and only the kind of enforcement, `(mechanism)` or `(aspirational)`, and prints a
rule in full only when it changed since the previous session-start brief. The rest of the
justifying prose stays in this file, and `kyo rules "<title>"` prints one rule verbatim. Keep
the imperative title short enough to survive that trim.

---

## Worker-audience rules

### Prose enters the store only via stdin or a file; the one-line text flags are the exception

- **audience**: worker
- **enforced-by**: mechanism, partial — multi-line prose has no flag-value form at all: body
  text (`edit`, `claim`, `verify`, `note`, `report`, ...) enters only through a `--*-file`
  flag or stdin. The exception is the one-line text flags, which do take prose as a shell
  argument — `--do`, `--enforced-by`, `--method`, `--quote`, `--title`, `--why` — and each refuses a newline
  and is checked for the residue a shell substitution leaves when its command printed nothing: two
  or more whitespace characters in a row, or whitespace at either end (KYO-91), plus the
  weaker odd-backtick-or-quote signal (KYO-26). The response is derived from the parser, not
  listed: a flag with a `--<flag>-file` twin is refused and the message names that twin; a
  flag without one is warned about and stored as given, with the gap made visible. A
  substitution whose command printed *output* leaves no residue and is invisible to all of
  it — an open hole, not a closed one. What is mechanical is that none of this can drift
  unannounced: `tests/test_procedures.py` (in Kyosei's own repository — not a file in your
  project, and not one you can open from here) checks the flag list, and each flag's
  treatment, against what the tool actually does (KYO-86, KYO-93).

Shell interpolation can silently delete words mid-sentence and still report success
(Law 5). Never construct a `kyo` command
whose prose argument was built by string-interpolating a shell variable, and when a reason
runs longer than a phrase, write it to a file and pass `--why-file` — the path the shell
does not touch.

### Commit before your final message; do not park on a stop signal with a dirty worktree

- **audience**: worker
- **enforced-by**: `SubagentStop` hook, `kyo check-clean` — run it yourself to see exactly
  what it checks and what it excludes; `kyo check-clean --help` names the rest

A worker that finishes with unstaged or uncommitted work and then stops is one crash away
from losing it — `git log main..HEAD` reads empty in that state, which looks identical to
"nothing was done." Commit continuously as you complete units of work, not once at the end.
Note the hook's limit: it fires on a clean stop, not on a crash (Law 8) — durability is your
job throughout the dispatch, not a final step. What it will *not* ask of you: `kyo check-clean`
excludes `kyosei/` and `ideas/`, the same two directories the merge guard forbids your branch
to diff, so a store dirtied by the orchestrator's own `kyo` writes is never yours to commit and
never blocks your stop.

### Flag conflicts with the brief; do not silently resolve them yourself

- **audience**: worker
- **enforced-by**: mechanism, partial — the report schema requires the `disagreements[]` and
  `premise_false[]` arrays on every report (present, possibly empty), and `kyo report-schema`
  prints that schema: the same document `kyo report` validates against, from the installed
  package, with no source tree and no project needed. Go and read it. Nothing yet forces you
  to populate the arrays honestly, which is why this is also a discipline rule

If a premise in your brief is false, or you disagree with the approach you were told to take,
say so structurally — do not quietly do what you think is right instead, and do not quietly
comply with something you believe is wrong. The standing stance: **flag conflicts, do not
resolve them; contradicting this brief must be cheaper than complying with it.** This has
repeatedly been the single highest-value output of a dispatch, not a side effect of it.

### Stay inside the dispatch's `write_scope`

- **audience**: worker
- **enforced-by**: aspirational in v1 — `write_scope` is stated explicitly in the brief and
  reviewed by the orchestrator against your report; nothing blocks an out-of-scope write at the
  moment you make it

`write_scope` (`none | queries-only | code | prod-data`) is a mechanical capability
statement, not a suggestion. A `queries-only` dispatch means read, never write — not "write
somewhere that seemed safe." If the work genuinely requires a capability the brief didn't
grant, that is a `disagreements[]` entry, not a decision to make unilaterally.

### Never write to the structured store directly

- **audience**: worker
- **enforced-by**: mechanism, partial — two guarantees, held two different ways, and which
  one you have depends on where this project's store lives. *By construction, under the
  split layout* (the default): the store is its own git repository, so a
  worker's worktree holds no copy of `kyosei/kyosei.db` — nor of `kyosei/export/`, nor of
  this file — and a branch there cannot express a diff under `kyosei/` at all. Nothing
  checks that, because nothing has to. *By a command, under the in-repo layout*: the store
  is committed beside the code, so the diff is expressible, and what fails it is `kyo
  check-branch` (`src/kyosei/cmd_guard.py`), which the `SubagentStop` hook invokes at every
  worker stop — exit 2 on a branch that diffs anything under `kyosei/` or `ideas/` relative
  to its merge-base with `main`. There your checkout lacks the database only because it is
  gitignored, which is the weaker of the two guarantees and worth knowing you lean on it.
  Neither guarantee covers a write made outside git: this is a diff check at stop time, not
  a write lock.

You cannot move a record, check a criterion, or edit `kinds.yml`/`procedures.md` from a
dispatch. Report your findings; `kyo report` and the orchestrator's review are the only path
those findings take into the store. This is what makes a worker's own "I'm done" reviewed
rather than self-certified. Until KYO-97 the `enforced-by` line above credited `kyo doctor`
with the branch check: that command opens the store and validates its contents, imports no
git query and has never looked at a branch, and the guard that does this shipped separately
as `kyo check-branch` (KYO-13.c5) without the attribution ever moving across.

### Never state a timestamp, line count, or other tool-derivable fact from memory

- **audience**: worker, orchestrator
- **enforced-by**: aspirational — no lint yet catches a hand-typed fact in prose; the fact
  itself is always available by generated output or a direct check

Run `kyo show --json`, `kyo brief`, `wc -l`, or `date` at the moment you need the fact, and
cite what it returned. **Orchestrators most of all** (KYO-27): this rule was `audience: worker`
alone, so the one person who writes a brief's task-specific prose — and therefore writes more
tool-derivable facts than any worker does — never saw it, and propagated a stale test count into
two frozen briefs that the workers had to correct from inside. A file's size stated once and
never re-measured can grow severalfold while brief after brief repeats the stale number
verbatim (Law 16). The tool stamps its own timestamps for exactly this reason — an agent
never types a time or a count from memory.

### Write a long run to a file and read the file; never pipe a suite through `tail`

- **audience**: worker, orchestrator
- **enforced-by**: aspirational — Nothing catches a pipe typed by hand: a `| tail` in a shell
  command is invisible to every check Kyosei has, and that is stated here rather than implied
  away (KYO-193.c2). A project can make the safe form the easy one with a logged target, as
  Kyosei's own `make test LOG=<file>` is: it writes the whole run to the file and prints its
  last lines with the command's own exit status, and `tests/test_kyo193_logged_runs.py` (in
  kyosei's own repository) fails if that status stops propagating

Run `cmd > file 2>&1; echo "exit=$?"` and then read the file — or a project target that does
exactly that. Two things a pipe through `tail` does instead, and both are silent: the exit
status you see is `tail`'s, which succeeds whenever it could read its input, so a failing suite
reports success; and `tail` buffers until its input closes, so a run interrupted by a session
turn ending or by the harness leaves a zero-byte log and no evidence it ran. Both have
happened: measurements lost to empty logs, and "exit 0" quoted from piped runs in commit
messages until an independent red result exposed it (KYO-193). No single pipeline flag fixes both halves: `set -o pipefail`
restores the status and not the buffering, `tee` the buffering and not the status, `stdbuf`
only for tools that honour it. Writing the file is the whole of the fix, so it is the rule.

### Never deploy, and never mutate a production system outside the declared `write_scope`

- **audience**: worker
- **enforced-by**: mechanism, partial — only kinds with `write_scope: prod-data` may touch
  production at all, and even then Law 11's dry-run-by-default requires an explicit `--apply`
  before anything is written

Deciding a change is fit to ship, and shipping it, is a judgment made with the whole project
in view — which a dispatched worker does not have. A list of forbidden hosts or deploy keys
goes stale when the tooling changes; the shape that survives is that the kind's policy is
the actual gate.

### Run the tests that cover your change, not the whole suite

- **audience**: worker
- **enforced-by**: mechanism, partial — `kyo dispatch` counts the other dispatches
  already created at the instant your brief is frozen (the same `store.running_dispatches()`
  set its touch-disjointness check compares you against) and states that number in the
  brief as a FLOOR, naming each one, so a worker under contention is told a fact about
  *this* dispatch rather than only this standing sentence. A floor and not a total, and
  the brief says so: an orchestrator dispatching a wave creates the dispatches one after
  another and spawns their workers afterwards, so the count is sampled in the first phase
  and read in the third and can only have risen (KYO-145). It follows that the rule never
  self-cancels — no count this brief can carry is evidence that nothing is contending with
  you. `tests/test_procedures.py` (in Kyosei's own repository — not a file in your project,
  and not one you can open from here) fails if that count stops reaching the brief, and
  fails if the brief regains wording that asserts what was true only at freeze time. What
  is aspirational is the rest: nothing observes which tests you actually ran, and nothing
  can, because only your project knows which command covers what.

Run the tests that cover what you changed, and leave the whole suite to your orchestrator.
Your green is on your own branch, before every other worker's merge — two green branches
can merge to red, and no worker can see that — so the run whose result actually predicts
what ships is the one on the merged tree, which only an orchestrator can perform. That makes
the full-suite run simultaneously the most expensive option available to you and the one
whose green answers a question nobody asked, and it is true of every project regardless of
what its suite costs. Where a suite holds an exclusive resource — a port, a display, a
database, a device — it is worse than useless under concurrent dispatch: an interface
suite run twice at once can fail over keyboard focus, so a concurrency problem arrives
looking like a code problem. This rule names who runs what and why; which
command covers your change is something only your project knows, so it stays yours to
choose.

### Batch independent calls into one turn; read a test run's failures only

- **audience**: worker
- **enforced-by**: aspirational — nothing counts how many tool calls a turn batched
  together versus how many separate turns they were split across, or how much of a
  file-redirected test run got read back into context afterward; both would show up in
  the same per-message transcript already read for a worker's token totals
  (`sum_transcript_usage`) — a by-message tool-call histogram for the first, an
  oversized `tool_result` following a bare test invocation for the second — but nothing
  reads either signal today.

Issue independent reads, searches, and status commands together in one turn instead of one
per turn, and run any test quietly, with its output sent to a file, reading back only its
failures — a turn re-reads your whole context, and printed output is re-read on every turn
after it is printed.

### Keep every foreground command under about four minutes

- **audience**: worker
- **enforced-by**: aspirational — nothing times a worker's commands, and the cache that
  makes this matter belongs to the harness and not to Kyosei, so no `kyo` command can see
  it expire. What is checkable afterwards is the transcript: a `usage` object whose
  `cache_creation_input_tokens` is nearly the whole context, right after a long tool call,
  is one of these, and KYO-297 counted them that way

Keep any single foreground command under about four minutes, and before you hand back, end or stop every background task you started -- monitors included, since stopping the run one watches does not stop the watcher.
A dispatched worker's prompt cache is short-lived — Claude Code gives a subagent five
minutes unless its operator chose otherwise — and it is refreshed by every request you
make. A foreground command that outlives it is a stretch in which nothing is requested:
the cache expires, and your next turn re-writes your *whole* context at the cache-write
price instead of reading it at a tenth; workers that sat through a full-suite run have
re-written everything they had, while workers that ran only test files re-wrote nothing
(KYO-297). The ordinary answer to a long command is that it was not yours to run —
see the rule above. Where one genuinely is — a measurement you have to wait for — run it
detached with its output to a file and **wait in slices**: a bounded `sleep` of three
minutes or so, look, and sleep again. Never one wait loop with a long timeout. You have no
way to be idle except a foreground command, so "waiting once" for a ten-minute run IS a
ten-minute foreground command. The arithmetic, at a 250k context: each look is a cache
*read*, about 25k token-equivalents, and it keeps the cache alive; one expiry is a full
cache *write*, about 313k. Slices are roughly ten times cheaper than one wait for any run
up to about fifty minutes. Looking once, at about the time the run should be done, is the
same single wait under another name, and costs the same (KYO-312).

And finish what you detached. A background process still running when you hand back does
not stay quiet: when it ends, the harness wakes you to say so, and the first request a
finished worker makes after being woken re-writes its whole context, however recently it
last spoke: workers that never ran a suite have re-written everything they had in a
one-line answer to a notice about a run they had already stopped waiting for (KYO-305).
`kyo show <dispatch-id>` counts these (`context_rewrites`). So before your final
message: wait for it, or kill it, and say which.

What wakes you is the *notice*, not the process, so stop the task and not only the run:
a monitor left outstanding at hand-back announces its own expiry later and wakes you even
when nothing of yours is running, and a worker that stopped its detached runs but not the
monitors watching them is woken by the monitors (KYO-337). Every background command and every
monitor prints its task id when it starts, so keep the ids and stop each one through your
harness's task-stop before your final message; a watcher you are waiting on yourself is
better replaced by a bounded foreground look, which ends when you read it.

### Spawn helpers read-only if at all, and own everything they do

- **audience**: worker
- **enforced-by**: aspirational, with one mechanical edge — nothing counts your helpers,
  caps them, sees what model they run on, or stops one writing to the worktree you share
  with it. What *is* mechanical is only that a helper can no longer be mistaken for you:
  `kyo check-clean --subagent-stop` reads the harness's own spawn sidecar, and a stop whose
  agent was spawned by a worker resolves to the named outcome
  `helper-of-a-dispatched-worker` instead of being audited against its parent's tree
  (KYO-154, `src/kyosei/cmd_guard.py`). That closes the one way a helper could *harm* the
  dispatch — being blocked, at its own stop, on its parent's uncommitted work, which
  nobody woken could clear. It does not make anything below enforced.

You may spawn helper subagents, and one read-only helper that surveys a file set and
reports back is often the cheapest way to answer a question your brief needs answered.
Three things stay yours regardless, and they are the reason for the "read-only" and the
"own". **Your worktree is shared, not copied**: a helper writing into it edits the same
files you are editing, with no lock and no ordering, and the first sign of it is work that
silently disappears. **A helper cannot report**: it has no dispatch, no `premise_false[]`
and no `disagreements[]`, so anything it found reaches your orchestrator only if you carry it
into your own report, in your own words, with your own confidence attached — a helper's
conclusion you pass on unchecked is your claim, not its. **A helper's cost and model are
undeclared**: `kyo dispatch --model` records what *you* run on and says nothing about what
you spawn, so a dispatch that fans out is one whose cost and whose provenance both stop
being bounded by the record of it. The first helper this project ever observed behaved
correctly on all three counts — read-only, committed nothing, and declined to read a
request to retransmit its findings as authorization to do more (KYO-154). This rule exists
so that stays a convention someone chose rather than an accident of who went first.

### Read each governed section your branch raises, and propose its verdict in `docs_verdicts[]`

- **audience**: worker
- **enforced-by**: mechanism, partial — `kyo report` matches every `docs_verdicts[]` entry
  to the obligation your diff raised, by (document path, section heading), prints the match
  beside that obligation, and prints any entry that matches nothing as **unmatched**; an
  unmatched entry is never applied, and `kyo docs settle --from-report <dispatch-id>` names
  it rather than settling it. So a verdict against a section you invented, or misspelled,
  or that your branch never touched, cannot pass silently. What nothing can check is
  whether you *read* the section: an entry you guessed matches exactly as one you earned,
  which is why this is also a discipline rule.

Most docs obligations settle `not-affected`: in measured consumer stores they outnumber
`updated` by between two and nine to one, and a single dispatch can raise sixty at once.
Most readings end in "nothing to do", and at sixty sections nobody reads them — the verdicts get made from the file diff
instead, by the orchestrator, days later, with the diff no longer in anyone's head. You are
the party who already has both the diff and the document open, so the reading costs you
almost nothing and costs your orchestrator most of a session.

`kyo docs preview --against <the base your worktree was branched from>` lists exactly the
documents and sections your branch would raise, grouped per document and read-only — it
opens no store, mints no ids and writes nothing, so it is safe to run from a dispatch
worktree. Read those sections, and put one entry per section in your report's
`docs_verdicts[]`: `doc_path`, `section` (the heading verbatim), `verdict` (`updated` if
you changed the document to match your work, `not-affected` if you read it and it needs no
change), `method` (how you decided) and a one-line `reason`.

**The standing stance is unchanged: your orchestrator settles, you propose.** Nothing you
write settles an obligation — `kyo docs settle` is an orchestrator act with an orchestrator's
author on it, and `--from-report` applies your entries under your orchestrator's name after
they have read them. An entry is a proposal with your reasoning attached, which is the
same thing `criteria_verdicts[]` already is and is applied the same way.

---

## Orchestrator-audience rules

### Review a report's `premise_false[]` and `disagreements[]` before its `criteria_verdicts`

- **audience**: orchestrator
- **enforced-by**: aspirational — `kyo report` ingests the whole structured file regardless of
  what order you read it in; nothing surfaces these arrays first

These are the dispatch loop's highest-value output, not a footnote: the loop's product is
corrections, not deliverables. Corrected numbers, and rejected approaches more convincing
than the brief that asked for them, appear in no deliverable, and arrive in prose that has
to be read closely to survive transcription.

### Never claim absence from a phrase search; search one distinctive word and read the context

- **audience**: orchestrator
- **enforced-by**: aspirational — nothing can check which search a human or an orchestrator
  reached for, and nothing ever will. The tool's own half of this defect *is* mechanical and
  is fixed: every prose match the tool performs goes through
  `kyosei.prose.phrase_pattern` (KYO-66), which is wrap-tolerant and tested. This rule is
  the half that lives in a person's hands

A phrase search returning nothing is evidence that the phrase does not appear *without an
intervening line break*, never evidence that it is absent, and in a repository whose prose
is hard-wrapped the second claim is nearly always the one being made. Search a distinctive
single **word** and read the lines around it: a word cannot span a line break, so `founding`
finds in one command what `founding charter` can never find. For a programmatic match or
replace, build the pattern by splitting the phrase on whitespace and joining the escaped
words with `\s+` — that is `kyosei.prose.phrase_pattern`, and calling it is cheaper than
rediscovering it. The wrapping is deliberate and stays: a paragraph on one long line makes a
one-word edit rewrite the whole line, which destroys the reviewable diff `kyosei/export/` is
git-canonical for. What makes this hard to diagnose rather than merely easy to make is that
the rendered form and the stored form disagree about where the breaks are — `kyo brief`
injects a rule's prose whitespace-collapsed onto one line while this file stores it wrapped,
so the text on screen looks exactly like the text the search should have matched. Such an
absence claim has been written into a record as a settled conclusion before a dispatched
worker found the phrase and filed it back as a false premise (KYO-66).

### Re-verify a record's premises immediately before every dispatch

- **audience**: orchestrator
- **enforced-by**: mechanism, partial — `kyo ungate` raises the `gate-cleared-stale-criteria`
  flag when a `holding-for` gate clears on a record whose still-unmet criteria predate the
  clearing (KYO-59), and the flag persists in `kyo brief` until it is resolved. That covers the
  staleness a gate clearing creates, at the moment it is created. It does NOT cover a brief
  frozen against premises that went stale for any other reason: nothing yet blocks dispatching
  one

A premise that held when a record was created can die from work done under a different
record entirely, with nothing that notices: a premise two records share can be killed by
unrelated work, and neither record mentions it. Re-checking at dispatch time, not creation time, is
the cheapest point at which this is still catchable.

### Declare the worker model in the store, not in your head

- **audience**: orchestrator
- **enforced-by**: aspirational — Kyosei does not spawn workers and must not start; your
  harness does, so no `kyo` command can make it honour this value. What the tool does is
  resolve it (`kyo dispatch --model` over the kind's `policy.worker_model` over
  `kyosei/config.yml`'s `worker_model`), record it on the dispatch, and state it in the frozen
  brief — the same standing `write_scope` has, and stated in the brief for the same reason

Which model a worker runs on is a project property, not a session one: a project's tolerance
for cost, and its sense of which work needs a stronger model, does not change between
sessions, and an orchestrator re-deciding it from scratch each session is the same unrecorded
standing decision this file exists to end. It is also a condition of the work in exactly the
sense the base sha, the worktree and the branch are — a dispatch that cannot say what executed
it is incomplete in the same way one without a base sha would be — and its absence was
invisible only because it had never varied (KYO-61: dispatches ran on the orchestrator's
own model, not by a decision but by the absence of one).

Declare it in `kyosei/config.yml`, override it per kind where the *brief asks for* something
different — argument and judgment, versus a mechanical fix its criteria already fully specify
— and per dispatch only when this one is exceptional. Do not ask a worker to report the model
back: the worker would be restating the condition its own dispatch declared, which is a
second, weaker source of truth for one fact (KYO-32's lesson), and law 3 says the two then get
to disagree.

### Run the full suite yourself, once, on the merged tree

- **audience**: orchestrator
- **enforced-by**: aspirational — Kyosei never runs a project's tests and does not know
  what its suite is or what invokes it, so no `kyo` command can tell whether this
  happened. The worker-facing half of this pair does have a mechanism (`kyo dispatch`
  states the live-dispatch count in every frozen brief); this half is discipline, and the
  cheapest moment to spend it is the merge you are already performing

Run the suite once, yourself, on the merged tree — no number of workers' greens
substitutes for it. Once per WAVE, not once per merge: merging four records and running the
suite four times buys only the ability to name which merge went red without bisecting, and
that is isolation you pay for on every wave and use on almost none. Merge the wave, run once,
and bisect only if it is red. Run it through the project's own scripted entry point rather
than an ad-hoc invocation of the test runner — a suite that needs a build step will silently
test the previous build otherwise, and an orchestrator running more of the suite is an orchestrator
with more chances to run it wrongly. Every worker's run happened on its own branch, before every other worker's merge,
and two green branches can merge to red; the merged tree is a thing only you ever hold, so
collecting N pre-merge greens costs N runs and still leaves the only question that matters
unasked. Deferring the suite is therefore not a relaxation of the standard but a relocation
of it to the one place where the answer is about what ships. The one measurement behind
this pair is about injection and compliance, not speed: in a consumer project that adopted
it, both rules reached the frozen briefs and the workers honoured them.

Run the wave's suites with no worker running. A suite that holds an exclusive resource
fights a worker's checks for it exactly as two workers fight each other, and the failure
arrives in *your* run looking like a regression from the merge you just made: interface
checks that time out while a worker runs checks of its own pass alone minutes later, and
pass in the full suite once nothing else is running (KYO-297). And keep a full-suite command
out of the prompt you spawn a worker with — provisioning a worktree is one command and
running its suite is another, and a prompt or a `worktree_setup` entry that names the
second as the first tells every worker to break the rule above.

### Group records into one dispatch when a worker would otherwise ingest the same context twice

- **audience**: orchestrator
- **enforced-by**: mechanism, partial — the formal instrument exists and carries real
  machinery: `kyo dispatch --also-covers <id>` makes one dispatch speak for several
  records, the covered records' criteria settle from that dispatch's report, the brief's
  Touches section states the union that was actually compared for disjointness (KYO-76),
  and "finished but not moved" is derived from it. The serial case is likewise forced by a
  mechanism rather than chosen — `kyo dispatch` refuses a second running dispatch whose
  touches overlap. What no mechanism reaches is the judgment: nothing measures
  context-ingest cost, and nothing can tell whether a grouping you made was the right one

Send several records to one worker when the reading they demand is the same reading, and
pick the instrument that matches the mode. The test is whether a worker would otherwise
have to ingest the same context twice — an observable cost — and *not* whether the records
are "related", which is unfalsifiable and would license grouping anything with anything.
Touch overlap is one signal of shared context and the narrower one: two records in the same
subsystem, or turning on the same mechanism, can require identical reading with no file in
common at all. Group by shared reading, never by topic; a topical grouping puts unrelated
criteria under one brief and spends the disjointness guarantee `--also-covers` is otherwise
careful to preserve.

- **Formal** — `kyo dispatch --also-covers <id>`: one dispatch, one brief, one branch,
  formally speaking for several records. Reach for it when the covered records' criteria
  are meant to settle from this dispatch's report.
- **Informal** — you decide one worker should take several conceptually linked records
  because the reading is shared. No store object changes and none needs to; the grouping
  lives in the dispatch's task prose, and each record keeps its own dispatch.
- **Serial** — records whose touches overlap cannot be dispatched concurrently at all,
  because the disjointness check refuses that pair. Your options are one worker taking them
  in sequence, or waiting; the first keeps the board moving. That is a statement about the
  guard, not a claim that serialising is cheaper — there is no measurement of its
  efficiency, and this rule does not vouch for it.

Provenance, kept in the register it arrived in: grouping records for dispatch and deferring
the suite to the orchestrator *seemed*, in use, to make development more token-efficient and
faster. Seemed — there is no measurement behind the combined effect, and a rule that
restated it more confidently than its source would be inventing the evidence it lacks. What
needs no measurement is the shape of board that produces the serial case: hub files are
claimed by several open records at once long before anyone notices.

### Cite every record to the maintainer as a markdown link with a `kyosei://` target

- **audience**: orchestrator
- **enforced-by**: aspirational — `kyo brief` prints a worked example of the form beside
  the orchestrator-audience rules every session it runs (KYO-356.c2), built from this
  project's own prefix so the shape an orchestrator copies is never a different project's id.
  Nothing checks that a citation actually got typeset as a link rather than retyped as a
  bare id

Name every record to your maintainer as a markdown link with a `kyosei://` target --
`[KYO-263](kyosei://KYO/KYO-263)`, the project being the store's own prefix or its
directory name -- in every message of a session, closing summaries and tables included.
This is the only form the desktop app's claude view renders clickable (KYO-263): a bare
id, or a bare `kyosei://` URL with no markdown link wrapped around it, stays plain text with
no element to click. The rule is in this ledger because an instruction read once, when the
skill is invoked, does not hold over a long session: an orchestrator links ids for its first
hours and drifts to bare ids in its closing summaries, and the session brief, which carries
this ledger, is a surface a session re-reads. Not proposed: linking ids inside `kyo` output itself --
that output is read by the orchestrator, not rendered for the maintainer, and a markdown
link in every row would cost brief size (KYO-323) for no reader.

This rule applies only where onboarding's [E4] answer is not no. A maintainer who does not
read the orchestrator through the desktop app has nowhere to click a `kyosei://` link, so
there it is noise around an id (KYO-561). That answer is recorded as `E4: declined` under
`onboarding:` in `kyosei/config.yml`, and `kyo brief` then prints neither this rule nor
its worked example: cite records as plain ids.

---

## Human-audience rules

### Purge stale ideas from `kyo ideas review` periodically

- **audience**: human
- **enforced-by**: aspirational — the listing surfaces age and broken-mention candidates;
  nothing forces action on it, by design (Law 12: the ideas layer is writable from anywhere
  and never swept into a state the tool enforces)

Ideas are zero-ceremony and never counted against the queue, which is the point — but that
means they only leave inventory when a human decides to promote or delete them. `kyo ideas
review` is the review surface; use it, rather than letting the pool grow into something no
review pass can face at once.

---

## Project rules

This project's own rules go here, in the format above. `kyo rules add` appends one, and
creates this heading in a ledger that has none. `kyo install-procedures` keeps this section
as written: it inserts the rules a newer build ships elsewhere in the file, and never
rewrites or removes a rule only this project has.
