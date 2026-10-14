# The worker-side guards: incident history

The incidents behind the rules in `kyo check-branch`, `check-clean`, `check-command`,
`check-build` and `pre-tool-use` (`src/kyosei/cmd_guard.py` and the `cmd_check_*.py`
modules beside it). The code keeps the rule each function enforces and why a reader
would otherwise break it; this file keeps what happened, what was measured, and what the
code used to do, one heading per record. A comment ending in
`docs/design/guards.md#kyo-NNN` points here.

Moved out of the code by KYO-430. The passages are the code's own words at the time,
lightly re-joined where a sentence had been split around a line of code.

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-17

`check-clean` (`cmd_check_clean.cmd_check_clean`) shipped previously as a raw
`git status --porcelain` in the hook template, which made the hook's two halves
contradict each other in any checkout the orchestrator is also running `kyo` in: the
orchestrator's own writes to `kyosei/export/` dirty the tree, the dirty check's only
available remedy is "commit them", and committing them puts `kyosei/` diffs on the
dispatch branch, which is precisely what `check-branch` fails on — while
`procedures.md` separately forbids a worker to write there at all. Worse, self-feeding:
the `Stop` hook's own `kyo sweep` dirties `kyosei/export/` as part of stopping, so
stopping cleanly created the condition that failed the next stop. One consumer's worker
fired ten times against that loop and ended "needs the orchestrator or a human".

## KYO-128

Which tree a run of the stop guards is about.

**`check-branch` was vacuous, not merely wrong** (`cmd_check_branch.cmd_check_branch`).
Run from the hook's inherited cwd this command was not merely reading the wrong tree —
it was VACUOUS. The orchestrator's checkout sits on `main`, so `branch` defaulted to
`main` and the check diffed `main` against `main`: zero changed files, "clean", every
time, for every dispatch that used a worktree.

**Naming the subject** (`cmd_guard._subject_lines`, KYO-128.c6, and KYO-48's principle
one level along): the defect being fixed is a check that reported a verdict about a
comparison it never made in the tree it appeared to be about. A silent fallback to the
cwd would reproduce that exactly.

**Why an unplaceable stop does not block** (`cmd_check_clean._unresolved_lines`).
Blocking on the orchestrator's tree is a condition the only woken party is powerless to
clear — one consumer spent ~229,000 tokens on a single dispatch that woke three times
against it, and over half a million across one shift.

**A status flag is not a difference** (KYO-128.c4/c5, `cmd_check_clean.cmd_check_clean`).
`git status` reports what the index says; it can flag a path whose blob is identical to
HEAD. Observed by a consumer as `M README.md` with `git diff` and `git diff HEAD` both
empty and the two blobs the same object.

## KYO-134

status: amended
amended-by: docs/design/dispatch.md#kyo-418

**The stop is written down** (`cmd_guard._stop_target`,
`cmd_check_clean._unresolved_lines`). The raw payload is returned alongside the target
because the stored record carries the harness's own `session_id`/`agent_id` — the
handles that let a human line one of those rows up with a session long afterwards.

Before KYO-134 the unresolved-stop finding went nowhere but the orchestrator's transcript,
which nothing obliged them to read and which was gone by the next session, so `audit`
now says where the same finding is kept instead.

## KYO-169

How loud an unplaceable stop is, decided by the population and never by the row.

**Why no per-row rule** (`cmd_check_clean._population_profile`). KYO-163 settled that
"is *this* stop real?" cannot be asked: a tick's `SubagentStop` payload is
shape-identical to a dispatched worker's, the only difference is whether a transcript
and a spawn sidecar exist for that agent id, and classifying on the absence of a trace
is what KYO-154's dispatch was offered and refused — it silently stops auditing the real
worker whose sidecar the harness failed to write, which is the one case the audit exists
for. This store's own ledger holds a live example: a genuine dispatched worker with a full
sidecar and a 526-entry transcript, sitting unresolved among the ticks. Any per-row rule silences it.

**What the read costs**, measured on this project's own store (520 stop-audit rows in a
3,134-row, 119 MB `events` table): ~4 ms warm, ~180 ms on a cold page cache, against a
hook process that costs ~100 ms to start before it does anything.

**What the notification cost** (`cmd_check_clean._unresolved_lines`, `_tick_line`).
Once the finding became durable (KYO-134), printing it loudly was a notification about a
record rather than the record, and what the notification cost was measured:
~8 long lines, rendered by both `SubagentStop` guards, per unplaceable stop, against a
ledger of 520 rows of which 494 were unresolved. The eight-line explanation was written
by KYO-128 and KYO-134 when the transcript WAS the record; KYO-134's own docstring said
the finding "went nowhere but the orchestrator's transcript".

**One explanation, not two** (`cmd_check_branch._deferred_lines`). Both `SubagentStop`
guards render the same finding about the same stop, so an unplaceable one had always cost
its explanation twice. `kyo check-branch` already declined to *record* the stop for
exactly this reason — "recording in both would put two rows in a git-canonical ledger for
one event and make every count of them twice the truth" — and the reasoning was simply
never carried across to the prose.

## KYO-201

(`cmd_guard._subject_lines`.) The candidate list was labelled "running dispatches", and
the word had to go. The candidate set became every dispatch that declared a worktree
still on disk, whatever its state, because a worker outlives the ingest of its own
report — so labelling that list "running" would print, in a hook message, the very claim
KYO-201 was filed about.

## KYO-206

(`cmd_check_clean.cmd_check_clean`.) The brief tells a worker to write its structured
report and to commit before it stops; `check-clean` counted any untracked file, so the
only way to satisfy both sentences was to commit the report into the branch — and a
consumer's merges then carried `report.json` into main, conflicted on it add/add, then
modify/delete, until the orchestrator was running `git rm --cached` at every merge.

## KYO-285

status: amended
amended-by: KYO-294, KYO-410

(`cmd_check_branch._resolve_against`.) Raised by the KYO-284 worker: `main` is the
right base only for a dispatch cut from main. This project's own UI branches carry the
orchestrator's store commits beside code, and a dispatch cut from one of them put those
commits in a diff against `main` — so the guard blamed a worker for commits it never made
(the KYO-283 and KYO-284 dispatches both hit this).

## KYO-294

status: amended
amended-by: docs/design/dispatch.md#kyo-485

(`cmd_check_branch._branch_cut_point`.) KYO-285 answered "which base?" with the
dispatch's own `worked_from_sha`, and that is right whenever the worktree was cut at the
sha the brief recorded. It was wrong in what turned out to be the ordinary case, because
of KYO-10's sequencing: `kyo dispatch` stamps HEAD as the base and the orchestrator *then*
commits the store (the dispatch records themselves), so `git worktree add` cuts the
worker's branch one or more store-only commits PAST the recorded base. Diffing against
the recorded base then handed the worker a `kyosei/export/` diff it never wrote, and the
stop hook blocked it for a state its own brief calls normal (KYO-294's report, and the
whole of that wave's four worktrees the same day).

## KYO-298

(`cmd_check_command`, the section header.) The rule "run the tests that cover your
change, not the whole suite" was in `kyosei/procedures.md` and every brief of one wave
carried it. Two of five workers ran the whole suite anyway, because another
line of the SAME brief — this project's own `worktree_setup` — told them to. What it
cost was measured: those two runs were ten and seventeen minutes of a worker waiting,
and each was followed by a full-context re-write of 172,733 and 325,752 tokens (28% of
all the prompt cache that wave's workers wrote) because the harness's cache expired while
they sat there.

## KYO-335

**Every hook, not only `SubagentStop`** (`cmd_check_build`, the section header). KYO-325
gave the stop guard a fifth outcome — `store-unreadable` — so that a hook whose
`ctx.store()` raised says NOTHING WAS CHECKED instead of reporting confidently on the
orchestrator's checkout. That fixed the guard that was *lying*. It did not reach the six
other things this project installs into a hook, and those six were just as blind for the
same 120.5 minutes: no session-start brief, no session ledger row, no
sweep, no doctor, no push guard, no command guard. What each of them printed was
`kyo: /…/kyosei/config.yml carries keys no code path reads: …` — true, accurate, and
saying nothing whatever about the fact that the project's entire guard layer had
stopped.

**An unreadable config is not an empty one** (`cmd_check_command.CommandPolicy`,
`declared_command_policy`). Until `CommandPolicy.unreadable` existed,
`cmd_check_command` printed the same sentence for "declared nothing" and "cannot read
what it declared" — "this project declares no `controller_only_commands` … so nothing is
refused here", which during that window was a confident false claim made by every Bash
call for two hours, about a project that declares both keys (law 6). Refusing nothing was
exactly right — that half was never the bug.

## KYO-373

(`cmd_guard._untranscribed_output`, KYO-373.c2.) KYO-154's helper is the precedent for
exiting 0 on a positive "not a worker", but not for what is read first: a helper shares a
real tree, its parent's, and the guard still reports what is outstanding there. An agent
the harness kept no transcript for has no tree at all, and the only directory the guard
could fall back to is the orchestrator's own cwd — the tree KYO-128 exists not to check,
which is what every one of these stops was doing before KYO-373.

## KYO-409

(`cmd_check_command`, above the `make` option tables.) The segmenting, tokenising,
env-prefix and cd-following that used to live in the command guard moved to
`kyosei.shell_words`, shared with `kyo check-push`: two private copies had missed the
same shapes, among them the `nohup <command> > log 2>&1 &` form the command guard's OWN
timeout refusal recommends (KYO-312), which walked past the suite guard.

## KYO-410

**The trunk is a spelling** (`cmd_check_branch.TRUNK_SPELLING`, `_resolve_against`).
Before KYO-410, a repository whose trunk is `master` got "'main' is not a known git ref"
and exited 1 on every unmatched stop — which `SubagentStop` treats as a non-blocking
error, so the store-write guard silently did not run at all — and a matched stop there
lost its cut point and fell back to the recorded sha, which is KYO-294 again.

**The status listing is pinned** (`cmd_check_clean.cmd_check_clean`).
`status.showUntrackedFiles=no` — common in large repositories, set globally or per repo
— made the `git status` call list no untracked file at all, so a worker that wrote new
modules and never committed them stopped "clean" and its worktree was later pruned with
the work in it: the dead-worker case KYO-128 calls the one that matters most.

This narrows KYO-285 as well: the `main` it fell back to when no dispatch matched is now the
resolved trunk (`resolve_trunk`), a spelling rather than a branch name.

## KYO-418

(`cmd_check_build.opening_brief_build`.) Before the session was
stored on the brief run, the newest session-start brief from ANY session answered, so
two concurrent sessions each compared against whichever one had started last.

## KYO-422

(`cmd_check_command`, the section header above `cmd_pre_tool_use`.) The two
`PreToolUse` hook groups cost ~70 ms of interpreter start each, on every Bash call an
agent makes, measured in a code review.

## KYO-535

In one wave one worker left eight scratch files (`run.txt`, `status.txt` and others) in
the main checkout's root (KYO-519), another ran `kyo init` with no temporary
`$KYOSEI_HOME`, creating `~/kyosei/p1` and registering it in the real `projects.json`
(KYO-521), and a
third's scratch copy was registered the same way (KYO-505). `check-clean` and
`check-branch` read only the worker's own worktree, so every one of those stops passed.

**A baseline, not a timestamp** (`cmd_check_clean.take_baseline`,
`cmd_check_clean.outside_the_worktree`). A `projects.json` entry carries no time of its
own, and a file's birth time is not portable (Linux's Python exposes none), so "appeared
after the dispatch began" is answerable only against what was there when it began. It is
kept in the git common directory and not the store: it is one machine's absolute paths,
which the git-canonical export must not carry (KYO-52), and git's common directory is
found by the same `git rev-parse` from the main checkout and from every linked worktree.

**Blocks once, bounded by `stop_hook_active`.** This is the KYO-128 tension, decided
rather than inherited: the comparison cannot say who made a path, so the orchestrator's
own new file, or another worker's, is named on this worker's stop too. KYO-128 measured
what blocking a party powerless to clear a condition costs when every stop repeats it.
Here the first matched stop blocks (the worker is the only party holding the context to
say which paths are its own, and an exit-0 hook line reaches no model), and the stop
that follows a block (`stop_hook_active`) reports the same list without blocking, so a
path the worker cannot clear costs one wake. A dirty-worktree refusal carries the same
list, since the worker's next stop may already be a repeat. The orchestrator's own
untracked files in the main checkout mid-wave are the known false positive; store writes
under `kyosei/` and `ideas/` are excluded for KYO-17's reason, and new dispatch worktrees
land two levels under the home, below what is compared.

**Prevention where the worker can be seen** (`config.refuse_real_home_from_a_dispatch_worktree`).
A worker's Bash cwd is reset to the orchestrator's checkout between calls, so the cwd
alone misses the KYO-521 shape; the location the `kyosei` package was imported from is
asked too, which catches a worker running its worktree's `.venv/bin/python -m kyosei`.
A worker running the INSTALLED `kyo` from the main checkout is indistinguishable from
its orchestrator, and is the stop check's to catch. "Real home" is decided from facts
about the home — unset, `~/kyosei`, the worktree inside it, the project registered in
it — because an orchestrator that exports `$KYOSEI_HOME` hands the same value to every
worker it spawns.
