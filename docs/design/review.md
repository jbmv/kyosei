# The review loop: history behind `sweep`, `flag`, `next`, `brief` and `note`

The review loop is what the orchestrator runs between dispatches: what went stale
(`kyo sweep`, `src/kyosei/cmd_sweep.py`), what is flagged (`kyo flag`,
`src/kyosei/cmd_flags.py`), what is ready (`kyo next`, `src/kyosei/cmd_next.py`) and
what happened this shift (`kyo brief` / `kyo note`, `src/kyosei/cmd_brief.py`). Those
four modules were one, `cmd_review.py`, until KYO-431 split it by subject.

The code states the rule each function enforces. This document holds what the code
used to carry beside the rule: the incident that produced it, the numbers measured at
the time, and the alternatives that were weighed and turned down. A comment in the
code ends in `see docs/design/review.md#kyo-<n>` when its reasoning continues here.
One heading per record, in record order.

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-8

status: amended
amended-by: KYO-137

The review loop's first build (milestone 6). `kyo sweep`'s three flag classes are the
three KYO-8.c1–c3 name: touch-changed, touch-missing and near-duplicate-criteria. The
charter's "Sweep" section names several more (claims aged past N days, doc-`governs:`
staleness, moved-but-inverse / moved-but-not-merged); none had an acceptance criterion
on KYO-8's record, so none was built, and the dispatch's report flagged that as a
scoping call rather than guessing at them.

`kyo flag resolve` is this build's own design for an acknowledge/resolve path on a
sweep flag — KYO-8's task brief said only "flag it".

## KYO-44

The brief's per-kind spread used to read `decision total:3 open:3`: the same
permanent-`open` lie the listings told about knowledge records, aggregated. KYO-44.c2
made the spread count each record by its standing rather than its raw status.

## KYO-63

Under the split layout (the `kyo init` default) the store that records sweep's base
sha and the code repository that sha belongs to are two repositories whose histories
advance independently, so a later clone of the code may not contain the recorded sha.

Three responses were weighed for a sweep that cannot resolve its own base:

- **Refuse the whole command.** Rejected: it discards the two flag classes that never
  needed the base, and records nothing — and sweep runs from the session-stop hook,
  where a hard failure trains people to remove the hook.
- **Fall back to a full scan.** Rejected: "every declared touch changed" is a claim the
  tool cannot support, and KYO-33 already records what routine false positives do to
  an advisory check.
- **Flag nothing for that class, and say so durably.** Chosen. What is not defensible
  is silence, and silence was the natural direction: a sweep that creates no flags
  because it could not diff is byte-identical to a clean project.

So the run's `coverage` is carried on the `sweep_runs` row, in `--json`, in the sweep's
text, in the audit event and in `kyo brief`'s staleness-coverage line. The `--json`
payload was meant to matter as much as the text: KYO-45's amendment asked the v2 board
and the docs-mode staleness banner to consume it, because a binding that can only paint
"stale"/"not stale" has nowhere to put "the check could not run". Neither was built to
read it: what reads `coverage` today is `kyo sweep`'s own text and `--json`
(`render.sweep_text`) and `kyo brief`'s staleness-coverage line (`cmd_brief`,
`render.brief_text`). The desktop app's docs banner got its third state from its own
vocabulary instead -- `docs_staleness`'s `cannot-tell`, decided per document, not from
the sweep. `never-swept` is the same hole one step earlier — a store nobody has swept has
the same empty flag list.

## KYO-69

status: amended
amended-by: KYO-418

Sweep runs on every Stop hook. Before KYO-69 it filed a new `touch-changed` flag every
run for every open record whose declared touches the diff touched, so the flag count
grew with the number of sweeps rather than the number of problems: 75 unresolved flags
in this repository on 2026-08-26, all of one type, nine on one record, each true and
all of them the same finding. `kyo brief` is where they landed, which made it KYO-33's
"a purge list with routine false positives is one people stop reading" at the scale of
the one document an orchestrator reads first.

The fix has three parts:

- **Supersede, don't stack (c1).** A sweep that re-observes a record's open finding
  widens the earliest open flag (same start sha, new end sha, union of files, sweep
  count + 1). Earliest, so the target is stable across runs: an orchestrator who noted a
  flag id yesterday must not find it resolved and a different one carrying the same
  finding today.
- **Rank, do not suppress (c2).** Sweep still flags a record that was never
  dispatched — a touch list declared long ago and now wrong is a real finding, and an
  unflagged window is gone, not deferred, because the diff runs from the last recorded
  base. The brief ranks instead: "the ground moved under work that was actually done"
  is a staleness warning; "files this record names have been changing while it sat
  open" describes a codebase being worked on. A record is "worked" if a dispatch names
  or covers it, or a report was filed against it.
- **Migrate what accumulated (c3).** `kyo flag collapse` merges each record's
  duplicates into the earliest instead of bulk-resolving them: resolving 75 flags
  wholesale would close findings no human ever read, and the project's whole argument
  for flags is that they are a human's to judge.

The `flags_widened` count went into the sweep's audit event rather than onto the
`sweep_runs` row because the row could not carry it without a schema bump (argued on
the dispatch's report).

## KYO-74

`kyo brief` is a read surface that writes: it allocates a brief-run id and an event and
rewrites the git-canonical `_allocations.json`. It therefore answers both of the
database/export seam's questions, each where that question is already answered.

The write question ("would this lower a git-canonical counter and reissue an id?") is
asked once, on `Store.transaction()` (KYO-75). The read question ("am I presenting an
incomplete board as the whole board?") is `db_is_behind`, carried into the payload by
`currency.as_data(seam)` and printed first, ahead of every count and before `--compact`
drops anything. A refusal was not the answer to the read question: the state where the
database under-reports without a counter moving is the orphaned-export state, whose own
remedy is "do NOT run either command until you know which side you mean to keep" — a
refusal at session start whose remedy is "do nothing" leaves an orchestrator with no
session and no move, where the warning leaves them the board they can see and the fact
that it is not all of it.

## KYO-80

`cmd_brief` used to ask the write question a second time, on the read predicate
`db_is_behind`. The two disagree in both directions, so the second copy did not narrow
the guard: it refused in a state the guard permits, over a sentence asserting a
reissued id in the one state the tool had just established would not reissue anything.
It was deleted rather than re-narrowed — restating the guard on the right predicate
would put back the per-command copy KYO-75.c2 exists to have removed.

## KYO-87

A consumer's "dispatchable now (31)" carried 16 records that were already dispatched,
reported, verified and merged — finished work, sitting in the one list an orchestrator
reads instead of the records, because nobody had moved them out of `open`. Every input
needed to tell them apart was already in the store and nothing compared any of it.
KYO-87 added the finished-but-not-moved class, derived from the store rather than from
anyone remembering to set `status = built`, and shows it on both `kyo next` and
`kyo brief` off one computation, so the two cannot disagree about a record.

The boundary was kept sharp on purpose: a record whose dispatch reported but whose
criteria are not all settled stays in `ready` (a worker reporting `unmet-unbuilt` is a
record that needs dispatching again) and carries `prior_dispatch`. Flattening the two
would have answered a complaint about a list that flattened two states with a list
that flattens two other ones.

`_awaiting_verification` stays keyed on the `needs-verification` role beside it rather
than widening: `built` is a state an orchestrator declared and `settled_not_moved` is one
the store derives. A record in both appears in both, with the same remedy — accepted,
because the alternative is a row that leaves one queue on a condition computed
elsewhere, and a record that can fall out of every section is the failure being fixed.

## KYO-94

`kyo brief` shows shift notes since the previous brief and then stamps the maximum as
the new watermark, so a note appeared in exactly one brief and no command could reach
it again. A handoff written for the next session was consumed by whichever brief ran
next — in the live case, one run thirty seconds later by the same session, checking
that the handoff would work.

Widening the window was not the fix: the brief is a delta because a surface that costs
more to read than it returns gets skipped (law 15), and the store held thirty-five
notes at the time. What was owed was one guarantee — the last note of a previous session
always arrives — and that needed a definition of "session" the tool can compute, which
a brief run is not. Hence `brief_runs.session_start`, written only by
`kyo brief --session-start` (the packaged `SessionStart` hook). Claude Code fires that
hook on resume and on compaction as well as startup, which lands the right way round: a
orchestrator whose context was just compacted is exactly a reader who needs the last note
handed to it again.

## KYO-131

`kyo note` took three refusals over from argparse, which could only say what was
missing. A record id passed to `kyo note` is not a malformed shift note — it is a
orchestrator asking for a record-scoped note, which `kyo edit --note-file` writes.
Answered with argparse's "the following arguments are required: --shift" (identical to
the answer for a plain missing flag, not even acknowledging the id), a consumer
concluded `RECORD.nN` was a redaction target for an object nothing could produce, and
filed it as a defect.

## KYO-137

The description-predates-claim flag class (c7) is the one sweep class that reads no
repository: both halves of the comparison are in the store, so Kyosei can compare them
without reading the repository, as the consumer report that prompted it pointed out. It is
therefore also the one class `coverage` does not affect.

It is a fourth sweep flag class beside the three KYO-8 built (`contracts.SWEEP_FLAG_TYPES`), and
not one of the classes the charter's "Sweep" section had named.

## KYO-213

A consumer's orchestrator upgraded the tool and opened a session; the brief was the
first thing they read and said nothing about it, so the skill they followed all session
was the old one, and `kyo doctor` only said so at the Stop hook, when it could no longer
change what happened. The brief now reports the same asset drift at session start, off
the same comparisons `kyo doctor` calls.

## KYO-220

`kyo flag` could resolve one flag and collapse many; nothing listed them and nothing
printed one. A consumer wanting to know what a flag said parsed `kyo brief --json` and
picked it out by hand — a brief being a rendering of the flags, built and ranked for a
session start, and the wrong instrument for "show me flag f2". `kyo flag list` is the
read path.

## KYO-226

The brief carries the identity of the build that produced it (c2). A session opens on a
board and on the tool that derived it; before this, a consumer's report named a defect
their own build had already fixed.

## KYO-267

A criterion recorded `unmet-unobserved` because a person has not looked yet is a wait,
and nothing connected that wait to the only object that makes a wait findable. The
desktop's Awaiting list is derived from open gates of type `awaiting:human-run` /
`awaiting:human-review` and nothing else (the app's list of awaiting-human gate types);
the brief's "gated work" section and `kyo list --gate-tag` read
gates and nothing else too. So an unobserved criterion with no gate was a wait that
existed in the store and appeared on no surface a person opens. (KYO-267.c4 calls that
CLI query `kyo list --gate`; the flag is `--gate-tag`, and unlike the two gate-reading
surfaces beside it, it answers only for a gate carrying a tag from
`kyosei/gate-tags.yml`.)

It happened twice in one day: a criterion each on KYO-264 and KYO-265 was verified
`unmet-unobserved` with a person named in the reason, and both were left ungated; the ask
survived only in that session's shift note and was raised by hand in the next session.

## KYO-289

The finished-but-not-moved class used to require a `reported` dispatch, which was
KYO-87's own defect reached by the other road: work done in-session or verified by a
person by hand has no dispatch to report, so a record with every criterion settled
and nothing left to build was handed out as dispatchable because nobody had dispatched
it. Measured on this repository's store on 2026-09-18: `kyo next --json` answered
`ready: 28`, of which 23 sat at `verified` with every criterion settled. `reported`
stopped being a condition of the class and became a fact beside it (`reported_by`).

## KYO-295

`kyo next` had withheld the finished-but-not-moved class since KYO-87 while
`kyo dispatch` handed the same records straight out: the withholding was advisory, on
the listing surfaces only, so an orchestrator reading `kyo next` was told a record had
nothing left to build and `kyo dispatch <that record>` froze a brief for it anyway. That
is the shape KYO-48 closed for touch-disjointness (`commands.touch_standing`) and
KYO-87 closed for the two listing surfaces: one question, asked once, read by every
surface that acts on it. The predicate moved into `settled_not_moved_entry`, which both
the listing and the dispatch refusal call.

## KYO-300

Sweep's symbol narrowing memoises its two lookups (file content, diff hunks) per run: a
store where `README.md` is named by 64 records would otherwise read that file and run
that diff 64 times for one answer.

## KYO-314

`terminated-no-report` has no acknowledge action — `kyo dispatch close
--terminated-no-report --why` is the only path into the state and always sets
`closed_at` — and the brief used to keep every such dispatch loud forever, on the theory
that something else would eventually address the record. Measured on this store's
board: five terminated-no-report dispatches, every one an orchestrator error where no
worker was ever spawned, and every one on a record now `done` behind a later dispatch
that reported. "Stays loud forever" bought five paragraphs of settled history that
every session read past, which is how a terminated dispatch that does still need
attention gets read past with them. Since KYO-314 one drops off once its record is
answered (settled, or a later dispatch that speaks for it reported).

## KYO-346

`kyo flag resolve --settled` is the repair for a store that accumulated staleness
flags under records that had already settled before the completion move, `kyo wont-do`
and `kyo void` learned to close their own (c1). It is a flag on `resolve` rather than a
third action because it is still a resolve — of every staleness flag a settled record's
own move should already have closed — not a new way for a flag to leave the unresolved
set.

## KYO-349

The ungated-unobserved remedy line used to quote the criterion's stored reason
straight into `--do`. A reason answers "why does somebody have to look" ("the banner is
only visible with the app in front of them"), not "what should they look at or run", so
the old payload read as an instruction and was one shell-paste away from being stored
as one: the reason, not a remedy, ended up on the record, one step after an orchestrator
trusted the suggestion. `--do` is now a placeholder that `kyo gate` refuses unedited.

## KYO-398

status: amended
amended-by: docs/design/scaffold.md#kyo-450, docs/design/doctor.md#kyo-466

Once every project on a machine ran one bundled `kyo` (KYO-335), a re-pack upgraded every
consumer at once. The brief printed which build it was (KYO-226.c2) and compared it with
nothing, so a session had no way to learn that the code under it had moved since the last
one — and one session reported two fixes missing from a build that carried them, reading a
changelog that said nothing about containment. Each brief run now records its build, and a
session-start brief compares its own with the previous session-start brief's.

## KYO-414

Two overlapping runs of the review loop's writers could each act on a read the other
had already invalidated.

- **Sweep.** Two sweeps from two sessions' Stop hooks both read "no
  open flag" and each filed one, or both widened the same flag from the same count and
  one increment was lost. The plan now records a fingerprint of its reads and re-reads
  it inside the write transaction; changed, the plan is recomputed under the lock. The
  uncontended case keeps git out of the write lock; the contended one pays for it.
- **Brief.** Nothing held a read transaction across the brief's
  statements, so an event committed between the delta read and a separate `MAX(seq)`
  read landed at or below the stored watermark without ever being listed, and every
  later brief started after it. The watermarks are now the highest seq the brief
  actually listed.

## KYO-418

- **c5.** Before this, every widened flag logged its record's whole document twice and
  touched the record: 441 events and 7 MB between 2026-09-17 and 2026-09-25 on this
  repository, for a change to one string, and an `updated_at` that moved without anyone
  working on the record. A widening is now a flag-level event.
- **c8.** KYO-8 had every sweep log itself "even at zero flags, so
  the audit log shows a sweep happened" — and sweep runs on every Stop, so every
  assistant turn appended a `sweep_runs` row, an event and a shard line and rewrote
  `_allocations.json` whole, dirtying the export to record nothing. A run at the base
  the last recorded run already swept, finding nothing new, now records nothing; KYO-8's
  intent is met by the recorded run that saw that base.
- **c9.** Supersession widened from touch-changed alone to touch-missing and
  description-predates-claim, which had been de-duplicated only by exact detail text:
  a missing set growing from A to A,B, or a newer claim arriving, filed a second open
  flag beside the first.

c5 and c9 change KYO-69's c1. Supersession there covered `touch-changed` alone and widened
the record along with the flag; it now covers the two finding types above as well, and a
widening is recorded on the flag, not the record.
