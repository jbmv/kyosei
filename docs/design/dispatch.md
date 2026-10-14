# The dispatch loop: the incidents behind the rules

`kyo dispatch` lives in `src/kyosei/cmd_dispatch.py` (worker model, the refusals, brief
creation, measurement warnings). The rest of the orchestrator-to-worker loop lives by subject:
`dispatch_close.py` (`kyo dispatch close`), `cmd_worktrees.py` (`kyo worktrees`),
`stop_target.py` (which dispatch a stopping subagent is), `stop_audit.py` (the stop-audit
ledger), `worker_tokens.py` (what a worker spent, and when a dispatch ended),
`dispatch_docs.py` (docs obligations a report raises, numbered documents a brief names),
`touch_drift.py` (touch drift, declaration provenance, `kyo touch-drift`) and
`report_ingest.py` (`kyo report`, `kyo report-schema`, `kyo split`). They were one module,
`cmd_dispatch.py`, until KYO-435 split it.

The code states the rule each function enforces and why a reader would otherwise break it. This
file keeps the history those rules came from: what was measured, what broke, and what the
earlier code did. One heading per record, in record order, so a comment in the code can point at
`docs/design/dispatch.md#kyo-<n>`. The laws themselves are in `docs/design/charter.md`.

These passages were moved out of the docstrings and comments by KYO-435. They record what was
true when each record was worked, and they are not kept current: the numbers are measurements of
one store on one date.

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-27

From `cmd_dispatch._MEASUREMENT_RE`: a test-suite figure in task prose (62 tests, ~20s) was copied into two frozen briefs; both workers independently found 114 tests / ~100s. The brief freezes the instant it is generated, so the warning has exactly one useful moment to fire.

## KYO-70

Why `kyo worktrees` exists (`cmd_worktrees.cmd_worktrees`):

Nothing removed a dispatch worktree before this. The orchestrator deleted each
wave's by hand, and a dispatch that died and was never resumed left a directory
AND a branch behind with no command that noticed — so across sessions they
accumulated silently in a directory the tool was never given (KYO-70).

From `cmd_worktrees.cmd_worktrees`: a worker on this project died mid-run with 89 uncommitted files, and its worktree directory was the only surviving copy of its output -- why uncommitted work is never pruned.

## KYO-71

Why touch drift reports and never refuses (`touch_drift.touch_drift`):

The third variant of one bug class, and the first that no amount of reading
declarations could catch. Touch-disjointness failed *undeclared* (KYO-48: an
empty claim intersected nothing, so "I have not said" gave the same favourable
answer as "I have said, and we are independent"), then *declared but unconsulted*
(KYO-65: ``--also-covers`` records' touches were never read). This is *declared
but incomplete*: the list is read, and it is wrong.

It happens constantly and defensibly. The touches table is a **prediction**,
written by an orchestrator who has not done the work yet: KYO-52's prose warning
could only go at a shared funnel, KYO-54's criteria were about code another
record had moved, KYO-57 corrected docstrings in eight files its move had
falsified. Nearly every dispatch in one session edited files its record did not
name, every one for a good reason, every one flagged honestly.

So this **reports**; nothing here refuses. Refusing would have blocked five of
that session's dispatches, all of which were right. The honest goal, stated on
the record, is not that the prediction was right — a worker cannot declare a file
it has not yet discovered it needs — but that the RECORD ends up true.

## KYO-85

Why a three-dot diff is never trusted to say "no drift" (`touch_drift.DRIFT_NOT_CHECKED`):

KYO-85 is what happens when that outcome exists and a path into it is missed.
``git diff <against>...<branch>`` is a three-dot diff, so once the branch is merged
the merge base *is* the branch tip, the diff is empty **by construction**, and the
empty list rendered as ``none`` — "checked, and you are fine" — for a dispatch whose
own ingested report recorded three drifted files. A consumer ran it four times
across four dispatches and it never once helped; a check that reports no drift when
there is drift is worse than no check. The documented workflow routes a
orchestrator to run this *after* the merge, which is precisely when it had stopped
working, so the honest window was the one nobody was sent to.

## KYO-90

Why a report can amend an earlier one (`report_ingest.cmd_report`):

**KYO-90: a resumed worker's corrected report.** The workflow this loop sits
inside resumes workers — report, merge, find something wrong with the merged
tree, send the same worker back to the same branch — and until KYO-90 the
corrected report had nowhere to go, so the store's account stayed the pre-fix
one. A report may now carry ``"amends": "<report-id>"``.

## KYO-128

Why a `SubagentStop` hook has to resolve which worktree it is about (`stop_target.py`):

`kyo check-clean` and `kyo check-branch` are correct commands run in the wrong
place. A `SubagentStop` hook inherits the ORCHESTRATOR session's cwd — the project
root — never the worktree the stopping subagent was dispatched into, and neither
command was told otherwise. So both audited a tree the worker never touched, and
failed in both directions: a false positive on every dispatch that finished while
the orchestrator had edits in flight, and — the one that matters — a false negative
on a worker that really did stop with uncommitted work in its own worktree, which
is precisely the failure the hook exists to catch.

Two fixes look obvious and are not. Both were built in a consumer project:

  * `--root <worktree>` in the hook template cannot work. `.claude/settings.json`
    is ONE static string shared by every dispatch; it cannot carry a per-dispatch
    path.
  * "ask the store which dispatch is running" cannot work either. FIVE dispatches
    were observed in state `running` at one moment. The store knows every
    running dispatch and its directory; it cannot know which of them a given stop
    belongs to, and auditing all five is worse than the bug — a sibling mid-work
    almost always has uncommitted files, so every stop would block on somebody
    else's work in progress.

The missing ingredient is in neither place: it is the hook payload, which carries
`agent_id`. That plus the stopping subagent's own transcript — whose first user
message is the prompt it was spawned with — names exactly one running dispatch.

What was checked before taking on the coupling to the harness's transcript layout:

Nothing kyosei already owns can answer this. Checked before taking the coupling:
the payload's `cwd` is the orchestrator's; the transcript record's own `cwd` and
`gitBranch` fields are the orchestrator's too (observed: `cwd` = the project root
and `gitBranch` = `main` on a worker whose worktree and branch were neither);
the `agent-<id>.meta.json` sidecar carries `agentType`/`description`/`model` and
no directory; and the store has no column that could hold an `agent_id`, because
`kyo dispatch` runs before the agent exists and Kyosei does not spawn workers
(KYO-61). The prompt text is the only carrier, and the transcript is the only
copy of it.

## KYO-133

Why close-only flags beside a record id are refused (`dispatch_close._CLOSE_ONLY_FLAGS`):

A close-only flag (`--terminated-no-report`, `--withdrawn`, `--why`, `--why-file`) passed
beside a record id used to be silently accepted and silently ignored — the
command dispatched a brand-new record anyway, which is the maximally
unhelpful outcome for a mistyped closure: the orchestrator's intent was to END
a dispatch, the effect was an extra one, running, with a frozen brief and a
dispatch id that now exists forever. It happened twice in this store before a
fix existed (KYO-120.d2, read-only-intent; KYO-128.d2, this flag). Named once
so the check below and any future reader of the pair see the same
attribute/flag names (law 3) rather than two independently retyped lists.
KYO-345 adds ``--withdrawn`` here in the same commit that adds the flag: a
close-only flag that this table does not know is one the pre-freeze check waves
through, and a mistyped withdrawal would then freeze a SECOND brief — which is
the exact outcome KYO-133 exists to prevent, on the exact command KYO-345 exists
to make usable.

## KYO-134

status: amended
amended-by: KYO-375, KYO-418

`stop_audit.py`'s storage for stop resolutions (`STOP_AUDIT_COMMAND`, `record_stop_audit`):

KYO-128 left one thing open and said so. On exit 0 neither stdout nor stderr is
fed back to any agent, so an `unresolved` warning — "NOTHING WAS CHECKED about
the stopping worker's tree" — lands only in the hook's own output in the
ORCHESTRATOR's transcript. It is addressed to the one party who can act on it and
nothing obliges them to read it, and by the next session it is gone. That is the
same shape as the defect KYO-128 fixed, one step milder: a check whose result
does not reach the party who can act on it.

This is a storage question before it is a display one, because `kyo check-clean`
wrote nothing at all. Three things about the storage chosen, each of which was
the alternative to something worse:

WHY THE `events` LEDGER AND NOT A NEW TABLE. A purpose-built `stop_audits` table
beside `sweep_runs`/`brief_runs` would be the tidier home and was the first
design. It needs `schema.py` (DDL, the schema_version bump, and a registered
consumer for every new column), `export.py`, `rebuild.py`, `store.py` and
`cli.py` — none of which this dispatch may touch. The `events` ledger already
is what it needs to be: append-only, git-canonical, rebuilt from export, and
already carrying rows with `record_id` NULL that are not about any one record
(`kyo brief` writes one on every run). So no schema change, and no version bump.
The tidier home remains a stated preference.

WHY NOT EVERY STOP. One row per SubagentStop would mean a row for every research
subagent in every session, in a file that is committed. A stop resolved to
`no-declared-worktree-in-play` is a stop where no dispatch had a worktree that
could HAVE gone unaudited — there is nothing to record, and recording it would
put ordinary session churn into a git-canonical ledger to say so. What is
recorded is every stop where declared worktrees WERE in play, `matched` ones
included, which is what makes the difference below expressible at all.

WHY `matched` ROWS ARE WRITTEN TOO, which is the whole of c5. "No unresolved
stops" and "nothing ever recorded a stop" are different facts and an empty list
renders them identically — `governs_staleness`'s `cannot-tell` and KYO-129's
`no-documents` are this project's two previous encounters with the same lesson.
A `matched` row is the evidence that the recorder ran at all. With none of them,
the honest sentence is "this store has never recorded a stop audit", not "every
stop was checked".

The write is BEST-EFFORT and never raises. These guards are PROJECT_OPTIONAL and
run inside a hook: a store that will not open, a database behind its export
(`Store._refuse_if_behind_export`), or a `database is locked` from the
orchestrator writing at the same moment must not turn a stop into a crash — and,
under KYO-134.c4, must not turn one into a block either. A failed write is
reported in the guard's own output as a failed write, which is the one thing it
must not do silently.

## KYO-137

Why docs obligations are raised at report time (`dispatch_docs.py`):

KYO-136 decision 2 puts this at report time and not in `kyo sweep`: a document is updated
while the change is fresh. Sweep is the next session; report time is while the orchestrator
can still act on what it just read. The consumer report this came from is exactly the failure
that produces: the orchestrator had just merged a dispatch and had no prompt pointing at the
document.

## KYO-147

What the disjointness check was worth (`touch_drift.declaration_provenance` and its section):

KYO-147.c1. The refusal side of this check has been honest since KYO-48: an
undeclared scope raises `scope-unknown` rather than passing as "disjoint", because
"I did not compare" must never render as "they do not overlap". The PASSING side
never learned the same lesson. It said nothing at all, and an orchestrator reading
silence reads "no collision" — a verdict about the work, when what was actually
established is a verdict about two predictions.

A consumer measured the gap and filed it as data rather than as a complaint: eight
dispatches, eight drifted touch tables, not one declared set
surviving contact with the work. Twice, workers checked disjointness by hand
against the other running dispatch's declared touches — correctly, and only because
the standing stance told them to flag rather than proceed. The mechanism did not
establish what it appeared to establish; the workers did.

Why `touch_drift.touch_drift_correction` exists:

KYO-147.c4. `kyo report` has printed this list since KYO-71 and printed it as a
runnable ``printf ... | kyo edit --touches-file -`` one-liner since KYO-85.c3, so
"apply it without retyping" was already reachable. What was not reachable was
applying it without leaving the tool: a line to paste into another shell is a step
the consumer measured its own orchestrator skipping, and every skipped one leaves the
store wrong about the past.

## KYO-149

Why `kyo report-schema` exists (`report_ingest.cmd_report_schema`):

KYO-149.c1. Every dispatch brief this tool has ever generated told its worker
to "write your structured report per ``templates/report.schema.json``", and a
worker-audience ``enforced-by`` line cited the same path. ``templates/`` is a
directory of the SOURCE tree; ``pyproject.toml`` has never shipped it. In every
consumer project that has ever existed, that citation named a file the reader
could not open, in the one field whose entire job is to name something the
reader can go and check — law 1's failure, sitting inside the instrument built
to prevent it. The defect was invisible from inside this repository for the
plainest possible reason: this repository *is* the source tree, so the path
resolved here, and "we dogfood it" is exactly the discipline that cannot catch
this class.

## KYO-154

The spawn sidecar, re-read (`stop_target._spawn_sidecar`):

KYO-154. KYO-128 checked this file and wrote it off — "carries
``agentType``/``description``/``model`` and no directory" — which was true of
what it was asked then (*which tree?*) and is not true of what it is asked
here (*whose agent is this?*). Observed on disk now: every sidecar carries
``spawnDepth``, and every one written for an agent that another agent spawned
also carries ``parentAgentId``. That is a fact Kyosei does not have to infer
from prose.

## KYO-163

status: amended
amended-by: KYO-373, KYO-375

What produces a stop-audit row whose agent id exists nowhere (`stop_audit.py`, the block above `STOP_TICK_PERIOD_SECONDS`):

WHAT PRODUCES A STOP-AUDIT ROW WHOSE AGENT ID EXISTS NOWHERE. Named from
observation, not inference (KYO-163.c1), so the next reader does not have to
re-derive it:

  Claude Code delivers `SubagentStop` to this project's hook ON A ~32-SECOND
  TIMER, ONCE PER RUNNING SUBAGENT, and mints a FRESH agent id for every tick.
  It is a periodic per-agent signal, NOT an agent finishing.

The evidence, measured over two sessions of this project's own ledger:

* One session recorded 389 stop-audit rows against 40 agents that were actually
  spawned (27 `Agent` calls by the orchestrator, 13 by its workers) and 39 spawn
  sidecars on disk. 365 rows were `unresolved`, every one carrying a DISTINCT,
  non-null agent id, none of which appears anywhere under ``~/.claude/projects``
  as a filename, an ``agentId``, or a ``parentAgentId``.
* Inter-arrival gaps quantize to MULTIPLES OF 32 SECONDS. Restricted to moments
  when exactly one agent was live, the gap histogram is 32s (x37), 64s (x5),
  92s (x3), 124s (x2), 152s, 184s, 212s — a metronome with skipped ticks.
* Phase-unwrapping a later session at 32s resolves the arrivals into one stable
  stream per running agent. Two workers gave two interleaved streams; when one
  went quiet the survivor ticked at exactly 32.0s; SPAWNING ONE EXTRA HELPER
  ADDED A THIRD STREAM within 32 seconds. That last step is causal, not
  correlational — the stream count follows the agent count.
* The rate therefore tracks CONCURRENCY, not completions, and is anti-correlated
  with orchestrator activity (r = -0.43 against the orchestrator's own tool calls):
  the rows arrive fastest while the orchestrator sits idle waiting on workers.

WHAT THESE ARE NOT, ruled out by observation rather than left open. The obvious
benign explanation is that they are ordinary agents whose transcripts Kyosei
looks for in the wrong place — a helper spawned by a WORKER, filed somewhere
other than the orchestrator session directory :func:`_dispatch_prompt` searches.
That would make "no readable transcript" a fact about the search path rather than
about the agent, which is a much better defect to have. It was tested directly
and it is false:

* The harness files EVERY nested agent under the orchestrator session's own
  ``subagents/`` directory, whatever its depth. There is no per-agent directory
  anywhere under ``~/.claude/projects``: a session directory contains exactly
  ``subagents/`` and ``tool-results/`` and nothing else.
* Checked against a helper spawned deliberately for the purpose, at
  ``spawnDepth: 2`` with ``parentAgentId`` naming a dispatched worker. Its
  sidecar and transcript were written to the searched path, its stop RESOLVED,
  and it produced EXACTLY ONE row. In the same ~4-minute window 17 further rows
  arrived carrying ids with no trace anywhere.
* KYO-154 is the standing proof of the same thing: it classifies a helper by
  reading ``parentAgentId`` out of a sidecar at that exact path, and it works.

So the search path is right, a real agent at any depth resolves through it, and
these rows are not misfiled agents. Stated here because the next reader will have
the same good idea, and it costs less to find it already crossed off than to
re-run the experiment.

WHY KYOSEI CANNOT FILTER THESE AT THE WRITE SIDE, which is KYO-163.c2's decision
and the reason it went the way it did. The `SubagentStop` payload carries
``session_id``, ``transcript_path``, ``cwd``, ``hook_event_name``,
``stop_hook_active`` and ``agent_id`` — and a tick's payload is SHAPE-IDENTICAL
to a real worker's. The only thing that differs is whether a transcript and a
spawn sidecar exist for that agent id, and classifying on the ABSENCE of a trace
is precisely what KYO-154's dispatch was offered and refused: it would silently
stop auditing any real worker whose sidecar the harness failed to write, which is
KYO-128's dangerous half returning by a new route. The audit exists for the case
where the harness's own bookkeeping failed.

So: EVERY ROW IS STILL WRITTEN, no stop is reclassified, and no outcome changes
(KYO-163.c3). What changes is what gets REPORTED — the population is measured and
named, so the count an orchestrator reads is interpretable instead of alarming. The
measurement lives in :meth:`StopAuditLedger.tick_profile` and is rendered by
`kyo doctor`, so a recurrence is recognised rather than re-derived (KYO-163.c4).

This is Claude Code's behaviour, not Kyosei's. Kyosei's job is to tolerate it
without lying about it.

## KYO-196

The "branches are left alone" line (`cmd_worktrees.cmd_worktrees`):

KYO-196: "branches are left alone on purpose" used to name every branch any
dispatch in the store had ever declared a worktree for — accumulating forever,
since pruning a worktree does not touch the dispatch record. It now names only
the branches of the worktrees *this run* removed.

## KYO-199

`dispatch_docs._docs_obligation_lines` replaced a flat list:

What went away with it: the "N of the document(s) above hold more than one" tail. It
was a second rendering of exactly this grouping that pointed at a *third* surface
(`kyo docs obligations`) to see the line. One rendering, on the surface where the
orchestrator is already reading, and no cross-reference to run.

## KYO-201

Why `stop_target._declared_worktree_rows` reads every state and narrows by existence:

`_declared_worktree_rows` returns ``(rows, unreadable)``, and the second element is KYO-325's whole fix.
This used to answer a bare list, so the two ways of getting an empty one — the
store said there are none, and the store could not be asked — arrived at the
caller identical. See :data:`STOP_TARGET_STORE_UNREADABLE` for the two hours
that cost on this project's own store.

KYO-201.c1, and the widening is the fix. This read `store.running_dispatches()`,
which sounds right and is not: a dispatch leaves that set the instant its report
is INGESTED, and the report file is the deliverable, not the worker's last
breath. A worker that wrote its report, was ingested, and then stopped was
therefore matched against a candidate set its own dispatch had already left —
so a prompt naming its own dispatch plus one still-running one resolved,
confidently, to the other one, and `kyo check-clean` reported another worker's
nine uncommitted files as "the stopping worker's own worktree" (measured
2026-09-05, KYO-201's record). State is now not consulted at all.

THE ONE NARROWING, and it is about the answer rather than about liveness: the
directory has to be there. What this function feeds is a guard that will run
`git status` in the path it returns. A pruned worktree cannot be the tree a
stopping worker worked in, cannot hold uncommitted work, and cannot be audited;
naming it would be a confident answer about nothing. It also keeps
:data:`STOP_TARGET_NO_WORKTREES` meaningful, which the unnarrowed widening would
not: measured in this project's own store at the time of writing, 120 dispatches
declared a worktree and exactly 1 of those directories still exists, because
`kyo worktrees --prune` removes them as dispatches finish. Without the existence
test every research subagent's stop in a project with any history at all would
report "declared worktrees are in play", the cwd would stop being guarded, and
the ledger would take a row for each. `kyo worktrees` computes the same fact the
same way (`WORKTREE_STATE_GONE`).

## KYO-206

Why the worker's report file is never named as drift (`touch_drift._DRIFT_IGNORED_PATHS`):

The worker's report is the dispatch's OUTPUT, not a file its work edited — `kyo report`
ingests it and the store keeps it. Naming it here produced a second defect a consumer
reported: the suggested `kyo edit --touches-file` line included the report file, and
once the orchestrator had dropped that file from the tree the edit failed validation
("no such file"), so the correction this command hands over was unrunnable. Excluded
even though `check-clean` and the `.gitignore` entry should keep it off a branch
anyway — a branch that predates either still exists, and drift reads what it is
given, including a report's own `touched_files[]`.

## KYO-207

Why a reported dispatch's worktree is not pruned until its HEAD is merged (`cmd_worktrees.cmd_worktrees`):

A consumer reported KYO-207, and the mistake it corrects is a definition:
"finished" meant the dispatch state was ``reported``, which is true the instant the
worker stops and says nothing about whether anybody has merged or rehearsed what it
wrote. A dispatch reported; a prune aimed at another dispatch took its directory with it;
nothing was lost, because the worker had committed and the branch survives by design,
but the orchestrator had to recreate the worktree to run the rehearsal from that code.
The skill sold ``--prune`` as safe because "branches are left alone" — true, and not
the property that was needed: the DIRECTORY is what a rehearsal runs in.

## KYO-211

Why the docs-obligation join computes its own changed paths (`dispatch_docs.DocsChangedPaths`, `_docs_obligations_for_dispatch`):

KYO-211. ``_docs_obligations_for_dispatch`` used to be handed a changed-file list
computed from the branch's three-dot diff and nothing else, which is empty **by
construction** once the branch is merged — so an orchestrator who merged before
ingesting (this project's own standing rule tells them to, and a dispatch that
changed the report schema *forces* it, because the CLI on main cannot validate the
report until the branch lands) got ``docs obligations: none`` on a diff that
examined nothing. Touch drift in the same command already knew this case and said
so; the obligations join did not.

## KYO-284

Where a dispatch's token count comes from (`worker_tokens.py`):

KYO-284 shows a worker's token use in the Activity view, for running dispatches and for
finished ones.

WHERE THE NUMBER COMES FROM, AND WHY IT IS NOT A SECOND SEARCH. Kyosei does not
spawn workers and has no billing surface; the only record of what a worker cost
is the harness's own transcript, which carries a `usage` object on every
assistant message. Finding "the transcript for dispatch X" from scratch would be
a SECOND guess at the harness's private layout beside the one KYO-128 took on
deliberately and hedged with a named outcome at every step — two guesses that
could disagree about which file is whose, and the second one built with none of
the first one's care. So nothing here searches. `resolve_stop_target` already
matches a stopping agent's transcript to a dispatch, by the dispatch id Kyosei
itself minted, and KYO-134 already writes every one of those resolutions to the
git-canonical events ledger. This reads that ledger back, opens the file the
resolution matched, and adds up its `usage` objects. The matching is KYO-154's,
used from the other end.

WHAT THAT BUYS AND WHAT IT COSTS. It buys a number that is right or absent:
every dispatch either has a `matched` stop row naming a transcript, or it has one
of the named outcomes below, and there is no third state where a plausible number
is shown for the wrong worker. It costs coverage of the past: no row written
before KYO-284 carries a transcript path, so every dispatch that finished before
this shipped reads `no-transcript-recorded` forever. That is the honest answer
and not a defect to route around — the alternative is the search this refuses.

## KYO-286

Why `worker_tokens.dispatch_end` exists:

`dispatches.closed_at` is set by `kyo dispatch close` and nothing else, and a
dispatch that simply reported is usually never closed — one of this store's
dispatches stayed `reported` and "(not closed)" for days. The desktop's activity row fell
back to "now minus created" for such a row, so a finished dispatch showed a run
time that kept growing and a "when it ran" that kept moving.

## KYO-292

Why `WorkerTokens.latest_turn_tokens` is the last message's size and not a sum:

This is the figure claude.ai's agent card shows, measured twice against
one worker's transcript on 2026-09-18. With the card reading 141.4k tokens, the
last message read 139,686 then 141,270; at the
end the harness reported 189,568 where the last message read 187,726 and every
cumulative sum was already past it (190,141 for input + cache creation + output
counted once per message). A running sum cannot be the card's figure, because the
card's figure is smaller than one.

Why `worker_tokens.sum_transcript_usage` groups rows by message id and keeps the last:

ONE ROW IS NOT ONE MESSAGE (KYO-292.c4). The harness writes a transcript row per
content BLOCK, and every row of one assistant message repeats that message's
`id` and its whole `usage` object — so a three-block message appears three times
and adding the rows up counts it three times. Measured on the same worker's live
transcript on 2026-09-18: 179 usage-bearing rows against 106 distinct message
ids. So the rows are grouped by `message.id` and the LAST row of each message is
the one that counts, which is not a coin toss either: the first row's
`output_tokens` is a streaming-start figure, and on that same file the first rows
summed to 1,501 output against the last rows' 4,752.

Why `worker_tokens.worker_tokens_index` budgets by `created_at`:

The token budget is spent on the most recently created dispatches, read off
``created_at`` — never on the tail of ``dispatch_ids``, which is the defect
KYO-292 is about. This listing's order is ``dispatches.seq``, and that is the
order rows were inserted, not the order they were created: a rebuild inserts
from the export directory, so `KYO-100.d1` lands before `KYO-99.d1` and every
`.d2` lands after every `.d1`. On this project's own store that put the four
newest dispatches nowhere near the tail and spent all 24 sums on dispatches from
weeks earlier, so the rows the app was rendering all read "tokens unknown".

## KYO-295

Why `kyo dispatch` refuses a finished-but-not-moved record (`cmd_dispatch._refuse_a_finished_record`):

The defect is the asymmetry, not the class. `kyo next` has withheld this class
since KYO-87 and widened it in KYO-289 (every criterion settled, whoever settled
them); `kyo dispatch` read none of it, so an orchestrator was shown a record under
"finished, not moved -- run `kyo move ...`" on one surface and could freeze a
brief for the same record on the other. Measured on this store on 2026-09-18, the
widened class held 23 records. A brief frozen for one of them sends a worker to
build something whose own criteria already say it is built.

## KYO-298

The measurement behind the context re-write thresholds (`worker_tokens.CONTEXT_REWRITE_MIN_TOKENS`, `CONTEXT_REWRITE_SHARE`):

MEASURED, on this store's 2026-09-18 wave of five dispatched workers: exactly two
messages across all five transcripts cleared both thresholds below, at 172,733 and
325,752 `cache_creation_input_tokens`. Both were the message immediately after a
full-suite run; the three workers that ran test files only produced none. 28% of
all the prompt cache that wave's workers wrote was those two messages.

Why `WorkerTokens.context_rewrites` is carried on the dispatch:

WHY THIS IS ON THE DISPATCH rather than in an orchestrator's script: KYO-298 is a
policy ("the whole suite is the orchestrator's") whose only honest justification
is a measurement, and a policy justified by a measurement nobody can re-take is
a policy that outlives its evidence. Carried per dispatch, in every project, it
is the number that says whether the guard is working — or whether the two
expensive dispatches of 2026-09-18 were a bad day rather than a pattern.

## KYO-307

Why `touch_drift._land_new_file_touches` converts `+path` lines:

The bug this closes is the one-step correction failing exactly where the workflow
says to run it. `+path` (KYO-225) says "this work creates that file", and
:func:`cmd_records._new_file_touch_problem` refuses the form for a file that is
already there — deliberately, because the two statements differ and the record has
to carry the true one. The skill says to apply the drift correction AFTER the
merge, because a created file exists only on the branch until then. So for the
commonest kind of dispatch — one that adds a test file — the correction re-emitted
`+tests/test_x.py`, the merge had just made that file exist, and
`kyo edit --touches-from-drift` was refused. Measured on this store on 2026-09-18
against KYO-294, KYO-300 and KYO-303, all three in one session.

## KYO-325

status: amended
amended-by: KYO-375

Why `STOP_TARGET_STORE_UNREADABLE` exists (`stop_target.py`):

KYO-325: the STORE could not be read, so which dispatches declared a worktree is
not known and this stop cannot be placed against anything. A fifth outcome rather
than the fourth, because the thing it replaces was
:data:`STOP_TARGET_NO_WORKTREES` — a POSITIVE claim ("the project root IS every
dispatch's worktree here, so this cwd is the right tree to read") that
:attr:`StopTarget.resolved` treats as an answer and
:func:`record_stop_audit` deliberately writes no row for. Measured on this
project's own store on 2026-09-19: `kyosei/config.yml` gained a key
(`worker_command_timeout_ceiling_seconds`, KYO-312's opt-in) that the `kyo` the
hooks actually run — an older installed build than the source tree — refused as "keys no code path reads", so `ctx.store()` raised
inside :func:`_declared_worktree_rows`, whose `except Exception: return []`
turned "could not read the store" into "nothing declared a worktree". For the two
hours that lasted the ledger recorded NOTHING — not the three running workers'
own stops, not one tick — `check-clean` and `check-branch` reported "clean" about
the ORCHESTRATOR's checkout while three workers' trees went unexamined, and
`kyo show` reads "worker tokens: unknown" for those dispatches. Law 6:
"read it and there was nothing" and "could not read it" are different facts, and
this is what it costs to collapse them.

Why `_declared_worktree_rows` carries the store error out:

KYO-325. The exception text is carried out rather than swallowed: on the
day this was measured it read "kyosei/config.yml carries keys no code path
reads: worker_command_timeout_ceiling_seconds", which names the whole
defect and the remedy (the installed `kyo` is behind the source tree)
in one line — and nobody saw it for two hours because it was discarded here.

Why `worker_tokens.worker_tokens_from` takes `ran_between`:

KYO-325 adds ``ran_between``: the dispatch's own ``(created_at, updated_at)``,
which is when its worker could possibly have stopped. It is optional and it
changes exactly one sentence, the one that was wrong. Without it, a dispatch with
no matched row reads "N stop(s) named this dispatch as a candidate and none of
them could be matched to it" — which sounds like the worker's stop was seen and
the match failed, and on this project's own store it was false three times in one
day. Those N rows were ~32s ticks (KYO-163) recorded HOURS after the worker had
handed back, naming the dispatch only because its worktree directory still exists;
the worker's own stop was never recorded at all. With the window, the two cases
separate, and the second names the cause instead of implying the first.

## KYO-345

Why `kyo dispatch close --withdrawn` exists (`dispatch_close.cmd_dispatch_close`):

KYO-345 is the second of those report-less states, and the reason it exists is
that the first one was being used as a lie. ``--terminated-no-report`` means the
work STOPPED — a worker was spawned and died — and the session brief tells the
next orchestrator to flag every one. A dispatch frozen with the wrong model and
never handed to anybody is not that, and a frozen brief is immutable by design
(amending it is the shape this record refused), so before this there was no exit
from ``running`` that did not enter a death into the store. ``--withdrawn`` is
that exit: the same refusals, the same required reason, and a state that is
terminal and quiet rather than terminal and loud.

## KYO-348

Why `cmd_dispatch._measurement_warnings` reads the criteria:

A figure stated in the dispatched record's criteria (or an `--also-covers` record's)
is not warned about. The defect this closes is that the check could see the
task prose and nothing else, so it read a DESIGN CONSTANT as a remembered
measurement: task prose gave a 15-second grace period for a record whose own
criterion says ``15s``, and its author was asked whether it had measured a number it had
specified. The two cases are genuinely different — a number copied from a doc has
a source that can be stale, a number the record's criteria state IS the source —
and the criteria are the only place in reach that can tell them apart.

## KYO-373

Why `STOP_TARGET_NO_TRANSCRIPT` exists (`stop_target.py`):

KYO-373: the stopping agent has NO transcript anywhere the resolution looks — no
``subagents/agent-<id>.jsonl``, no spawn sidecar beside it, no sidechain row for it
in the session transcript — while the harness HAS written transcripts for the other
agents of the same session. An agent this orchestrator dispatched always has one (both
workers of the session KYO-373 measured had a transcript and a sidecar, and matched
on the first try), so this is not a dispatched worker: it is KYO-154's positive "not
a worker" on a second ground, and the guards treat it the same way — exit 0 — except
that there is no tree at all to read, so NO directory is checked. Measured on the
session that filed it: 286 of these stops against 2 matched worker stops in one
afternoon, every one previously counted as an unmatched worker stop against every
running dispatch and "checked" against the orchestrator's own cwd, which is the tree
the guard exists not to check. KYO-163 is what they are (the ~32s per-subagent
``SubagentStop`` tick with a fresh id each time) and why classifying them was
refused then; see :func:`_untranscribed_agent` for the narrowing that answers it.

## KYO-375

status: amended
amended-by: KYO-418

Why a tick is counted in place (`stop_audit.record_tick_audit`):

Measured at 6,401 tick rows of ~7 KB each, 46.9 MB of a 255 MB events ledger, a
tick now writes NO row there. What survives is
a count plus first/last-seen timestamps, one per candidate dispatch, kept on
the dispatch's own row (``dispatches.tick_audit_json`` — the object
:func:`stop_audits` already reads a dispatch's state, worktree and branch from,
not a new file and not the events ledger) and incremented IN PLACE: this
overwrites a small, bounded value every tick rather than appending an
ever-growing one. The candidate dispatch's own record is re-exported so the
count survives a `kyo rebuild` — the same durability :func:`record_stop_audit`
buys a matched or unmatched row, bought here by a rewrite instead of an append.

This changes one sentence of KYO-325's: the "~32s ticks" it found naming a dispatch were
ledger rows then, and a tick writes no row now, so a dispatch's candidate rows no longer
include ticks.

It also changes what two older sections say is written. KYO-134 recorded every stop where a
declared worktree was in play as an events row, and KYO-163 kept every tick's row on purpose
("every row is still written"); a tick -- since KYO-373, a stop with no transcript -- is now a
count on the dispatch's row instead, and only the outcomes `stop_audit.COUNTED_IN_PLACE` does
not name are still rows.

## KYO-402

How `kyo worktrees --prune` removes a directory (`cmd_worktrees.cmd_worktrees`):

KYO-174: removing a directory with ``shutil.rmtree`` alone left git's OWN worktree
admin state stale, and the fix was a bare ``git worktree prune`` after the rmtree.
KYO-402 replaced both: removal is ``git worktree remove <path>``, one path at a
time (:func:`kyosei.git_queries.worktree_remove`), which clears that path's admin
entry in the same act and leaves every other stale entry for git. The KYO-174.c1
reasoning that rmtree was needed because an explicit ``--worktree`` (KYO-52) need
not be registered is exactly backwards: an unregistered recorded directory is a
separate clone, a subdirectory of the checkout or a store root, and it is now
``WORKTREE_STATE_NOT_REGISTERED`` — never removed. A directory some earlier build's
prune left stale is reported by ``_worktree_rows`` as
``WORKTREE_STATE_STALE_GIT_ENTRY`` rather than folded into plain "gone".

## KYO-403

Why a split project's `.kyosei` link was once required to be committed before dispatch (the
refusal KYO-580 removed):

A worktree cut from a base that lacks the committed `.kyosei` link has no link of its own:
before KYO-403 bounded the upward walk, `kyo`
there climbed into the store directory the worktree is nested in and answered as
an in-repo project on the store's own `main` — the worker wrote the live store and
both stop guards inspected the wrong repository. The bounded walk found nothing
instead, which was honest and still left the worker unable to read its brief, so
KYO-403 refused the dispatch where the defect was made.

KYO-580 replaced the refusal with resolution (`config._split_link_through_main_checkout`).
A repository may keep `.kyosei` untracked because the link names a local path; when the
bounded walk in a linked worktree finds nothing, `kyo` reads the link at the same relative
position in the main checkout, with the worktree as the code root, so the worker's branch,
tree and guards stay its own. A link in the worktree itself still wins, because the walk
finds it first.

## KYO-418

Why an unresolved stop is counted in place (`stop_audit.record_unresolved_audit`):

6,402 of this repository's 13,547 events (6.27 MB) were ``unresolved`` stop rows on
2026-09-25, still arriving at hundreds a shard: every Explore, research and
workflow agent an orchestrator spawns while a worker runs stops with a prompt that
names no dispatch, and each wrote a git-canonical row and dirtied the store. The
count is what anything read from them -- ``kyo doctor``'s ``unresolved_stops``
notice, ``kyo worktrees``' per-dispatch reasons, the census -- so it is kept IN
PLACE on each running candidate's counter (:data:`UNRESOLVED_COUNTER_KEY`), with
the stop's reason code, and survives ``kyo rebuild`` through that record's export.

This narrows two earlier sections. KYO-134 wrote every unresolved stop as an events row
carrying the harness's `session_id`/`agent_id`, the handles that line a row up with a session
afterwards; an unresolved stop now keeps only its count, reason code and first/last-seen times,
so those handles are gone for it, while a `matched` stop is still a row. KYO-375 counted a tick
on every candidate dispatch; KYO-418.c7 counts it on RUNNING candidates only
(`stop_audit._running_candidates`), so a tick no longer rewrites a reported or done dispatch's
export.

## KYO-485

status: amended
amended-by: KYO-487

Why `kyo dispatch --branch` creates the worktree itself (`cmd_dispatch._plan_the_worktree`,
`_WorktreeCreation`):

Before KYO-485 a `--branch` dispatch derived its worktree path and printed the
`git worktree add` command for the orchestrator to run, on KYO-70's stance that creating a
worktree is a code-repository act a store command must not perform. That stance had no
decision behind it, KYO-402 already had `kyo worktrees --prune` run `git worktree remove`, and
an orchestrator pasting the printed command on every dispatch is where hand-made collisions came
from (proposal KYO-484). So the dispatch now runs `git worktree add` in the code repository,
as the last act before the dispatch row is written. Every refusal is drawn first --
`_plan_the_worktree` refuses a path that exists and is not this branch's worktree, and an
existing branch whose tip is not the `--base` -- so a refused dispatch creates nothing, and
`_WorktreeCreation.__exit__` removes the worktree (and the branch, when it cut one) if anything
after it fails, so no directory is left that no row names. A path already registered with the
branch checked out is reused. `--no-create-worktree` keeps the printed command.

A new branch is cut at the sha the brief stamps as its base: the code repository's HEAD, or
the commit `--base <ref>` names. That changes KYO-294 (docs/design/guards.md#kyo-294): a
worktree one or more store-only commits past its recorded base is no longer the ordinary case
for a `--branch` dispatch, because the worker's branch starts at the base itself.
`_branch_cut_point` still covers a worktree made by hand (`--worktree`,
`--no-create-worktree`).

What a dispatch onto a branch that already exists records as its base changed with KYO-487:
see [KYO-487](#kyo-487).

## KYO-487

Why a dispatch onto an existing branch records that branch's tip as its base
(`cmd_dispatch` beside `_plan_the_worktree`, `brief.generate_brief`'s Base SHA line):

KYO-485 made `kyo dispatch X --branch B` check out `B` as it stands when it already exists --
the `.d2` re-dispatch onto a predecessor's branch, a consumer's case -- but with no
`--base` it still recorded the code repository's HEAD as the base sha (`worked_from_sha`). The
worktree starts at `B`'s tip, so the worker's report names that tip as the sha it worked from,
and when `B` is not a descendant of HEAD the report drew the "not a known descendant" warning
that consumer had asked to silence; the only way round it was to pass `--base B` by hand (found
by KYO-485's worker). So when the branch exists, no `--base` is given and the
worktree is checked out rather than cut, the dispatch records the branch's tip
(`local_branch_sha`) as its base, and the frozen brief states the sha as "the tip of the
existing branch", not "HEAD in this repo". With `--base`, KYO-485's plan already refuses a
tip that differs, so the two agree. A dispatch whose branch does not yet exist is unchanged:
it records HEAD, or the commit `--base` names, and its branch is cut there.
