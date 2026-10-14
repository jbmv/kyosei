# Scaffold and the install commands: the history behind the rules

The code in `src/kyosei/scaffold.py` and the five `install_*` modules split out of it
(KYO-432) keeps the rule each function enforces and why a reader would otherwise break it.
This file keeps the incidents and measurements those rules came from, moved here verbatim
from the docstrings and comments that used to carry them, one heading per record. A code
comment points here as `docs/design/scaffold.md#kyo-<n>`.

Modules: `scaffold.py` (`kyo init`, the packaged-asset and git helpers the installers
share, the code repository's report ignore line), `install_hooks.py`, `install_skill.py`,
`install_procedures.py`, `install_gitignore.py` (the store repository's ignore lines) and
`install_prose.py` (what `kyo init` discloses).

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-15

From `install_skill.SKILL_ASSET`:

It lived only in this repo, so `kyo init` gave a consumer the *enforcement*
(hooks, contracts, the merge guard) without the *workflow* — a project with
rules and no instructions. A consumer that hand-copied the file was 34 lines
behind within a day, which is the same configuration-drift shape as the hooks: we ship code that updates itself and
copies that never do. `kyo doctor`'s `skill` check is the other half.

## KYO-17

status: amended
amended-by: KYO-316, KYO-450

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-17: hook commands a previous release shipped and this one supersedes.
`install-hooks` merges rather than replaces, so without this a consumer who
upgrades and re-runs it keeps the superseded hook *and* gains its replacement —
and in this case the superseded one is the half that blocked a worker ten times.
Removal is by exact command string: anything a consumer edited themselves no longer matches and is
left alone, which is the conservative side to err on for a file we do not own.

KYO-31: exact-string supersession is also the *only* path by which a change of
**ordering** reaches an existing consumer. Everything else here compares per
``&&``-separated command (:func:`command_elements`), which is order-blind by
construction — `kyo doctor && kyo sweep && kyo html` and
`kyo sweep && kyo html && kyo doctor` have identical element sets, so reordering
the packaged fragment alone would leave every installed copy un-flagged and
un-rewritten. KYO-215 is the same limit from the other side: *removing* a command
makes the packaged line a strict SUBSET of the installed one, which containment
reads as "already present" and leaves entirely alone. Reordering and removal are
both invisible to containment, and exact-string supersession is the only path
either one takes to an existing consumer. Each value is the packaged command that
replaces the key, and is named verbatim in both `kyo doctor`'s report and
`install-hooks`'s change log;
`tests/test_install_hooks.py` fails if one names a command this build does not
actually install, so an entry cannot survive a later change to the fragment it
points at.

## KYO-20

From `install_hooks.hook_drift`:

We ship consumers two kinds of thing: code, which updates itself the moment
the package does, and configuration, which is a *copy* and does not. Nothing
told a consumer their copy had gone stale — so KYO-17's fix to the
`SubagentStop` loop sat un-applied in a project that had already been bitten
by it, while its own orchestrator believed the fix had landed.

## KYO-27

status: amended
amended-by: KYO-316, KYO-362

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-27: the Stop/SessionEnd fragment gained `kyo html`. Without this entry the
element-containment check read the old command as *not* covering the new one
(correctly — it did not run html) and added a second group, so doctor and sweep
would run twice per stop. A consumer's own extension of this same line is what
taught us that; superseding the packaged predecessor is the other half.

KYO-31 and then KYO-215 re-pointed this value. The value is only ever the *name*
of the replacement — what gets installed always comes from the packaged fragment
— so a consumer three releases behind reaches the current line in one run either
way; leaving it would only have made doctor and install-hooks announce a
command this build no longer ships, which is exactly the drift the check exists
to catch.

From `install_hooks.command_elements`:

A hook's `command` is a shell line, and a consumer who wants one more step
extends the line rather than adding an entry: one consumer's `Stop` hook was
`kyo doctor && kyo sweep && kyo html`, a workaround for the generated viewer
going stale (KYO-27 then shipped that line, KYO-31 reordered it, and KYO-215 retired the
`kyo html` in it).
Compared as whole strings that reads as *absent*, so `install-hooks` used to
add a second group and the consumer swept twice per stop — not cosmetic, since
each sweep writes a sweep run and advances the prior-sha bookkeeping
touch-changed staleness depends on.

## KYO-31

status: amended
amended-by: KYO-362

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-31: the ordering fix, and the only mechanism that carried it. That recipe put
`kyo doctor` first, where it failed html_freshness — the generated viewer went
stale the moment any session work wrote an event — and `&&` short-circuited
before the `kyo html` that was the remedy doctor itself named. Every stop
failed identically and nothing self-healed; a consumer found it in the field
and this repo reproduced it. Containment could not see it: the two orderings
have the same elements. Re-pointed by KYO-215 at the line that ships today.

From `install_hooks.command_elements`:

It is a *set*, so it is order-blind, and it is read as a *containment* test, so
it is also removal-blind. Both are real limits rather than oversights.
Order (KYO-31): `kyo doctor && kyo sweep && kyo html` and
`kyo sweep && kyo html && kyo doctor` are indistinguishable here even though the
first was the bug and the second the fix. Removal (KYO-215): a packaged line that
drops a command is a subset of the installed one, so an installed chain still
running the dropped command reads as fully present and is left untouched. Making
this order-aware, or an equality, would re-break the case it exists for — a
consumer who appends a step of their own would be told they are missing what they
are plainly running. Both travel by :data:`SUPERSEDED_HOOK_COMMANDS` instead,
which is exact-string.

## KYO-56

From `scaffold.check_store_location`:

KYO-56 decided one repository *per project* rather than one spanning all of
them, and the reason was concrete: this project's own store already discloses
an unrelated project's filesystem location because a relay quoted a path. A
single store repository makes that structural instead of accidental. Nesting
a new store inside any existing work tree — ``~/kyosei`` itself made a repo,
a dotfiles repo that owns the home directory, another project's store —
produces exactly that shared repository, so it is refused here rather than
discovered later in a diff.

## KYO-70

From `install_prose.worktree_disclosure`:

Same argument as :func:`prose_disclosure`, one artifact along. Nothing in
`kyo init`, the README or the `/kyosei` skill used to warn that dispatching work
would materialise directories at all, so a maintainer discovered ``kyosei-wt-60``
and ``kyosei-wt-69`` sitting beside their unrelated projects — named after a tool
they never asked to put anything there. The moment the tool is being set up is
the moment that sentence costs nothing and is worth something.

## KYO-73

From `scaffold.existing_store_refusal`:

The message this replaces stated one of those and inferred the other two. It told
a fresh clone of a **split** store — a state where the database is simply not
there, ``kyosei.db`` being gitignored by design — that it "already carries an
in-repo store" and pointed it at the README section on moving a store *out* of a
code repository, an operation the consumer had not asked for and could not
perform. That was the second half of a dead end: the missing-database error had
named `kyo init` as its remedy, and `kyo init` refused with this.

## KYO-94

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-94: the SessionStart fragment gained `--session-start`, and containment cannot
carry it — `kyo brief --compact` and `kyo brief --compact --session-start` are two
different `&&`-separated commands, so an installed copy of the old line would simply
be reported as missing the new one and left beside it, wiring two briefs per session
start. The flag is what tells the store a session began; without it the handoff block
falls back to "the most recent shift note" rather than "the last one before your
session", which is a weaker guarantee and is printed as one.

## KYO-128

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-128 re-pointed this value, on KYO-31's precedent: the value is only ever
the NAME of the replacement — what gets installed always comes from the
packaged fragment — so a consumer still carrying the raw git check reaches the
current line in one run either way, and leaving it would only make doctor and
install-hooks announce a command this build no longer ships.

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-128: the SubagentStop fragment gained `--subagent-stop`, and this is exactly
KYO-94's case one command along — containment cannot carry a flag, so an installed
copy of either bare line would be left in place beside the new one. That is worse
here than a duplicate run. The bare form is the defect: it audits whatever cwd the
hook inherited, which is the ORCHESTRATOR's project root and never the stopping
worker's worktree. Leaving it wired means every stop still blocks on the
orchestrator's uncommitted files — the unclearable condition KYO-128 exists to end —
while the new line beside it quietly does the right thing and is drowned out.
Exact-string supersession is the only mechanism that removes it from a consumer's
settings.json.

## KYO-129

From `scaffold.DEFAULT_ASSETS`:

KYO-129: which files are this project's Layer 3 documents, shipped declaring none
— scopes.yml's shape, for a reason none of the others has. This registry has a real
default (root-level *.md plus docs/**/*.md) and that default lives in the CODE,
because the bug it fixes is in projects that will never receive this file: a store
initialised before this release has no documents.yml, and seeding the two entries
here would have delivered the fix to exactly the projects that did not have the
problem. What the file ships is the shape, the glob dialect (pathlib's, not
scopes.yml's fnmatch), and the fact that declaring anything replaces the default
rather than adding to it.

## KYO-151

From `install_procedures.install_procedures_refusal`:

The hand-off KYO-151 describes is a **orchestrator** act, and the ban it must not
become a route around is the one `kyo check-branch` enforces: a dispatch branch may
not diff anything under ``kyosei/``. That guard is a *diff* check run at stop time,
so a worker that ran this command in its worktree would have written the store copy
for real and only then been told. Refusing the write is what makes the ban hold at
the moment it would be broken rather than at the next stop.

## KYO-186

From `install_hooks.HOOK_INSTALL_TARGETS`:

KYO-186 takes that to three. ``sessions.json`` installs under ``SessionStart``,
``Stop`` and ``SessionEnd``, and `kyo sessions --hook` tells them apart by the
payload's own ``hook_event_name`` rather than by three commands — because the
three do one job between them and the differences are one field wide: start
creates the entry, stop refreshes it (the only event that can notice a session
that joined Remote Control mid-flight), end marks it ended. Three commands would
be three places for that one decision to drift.

From `scaffold.DB_SIDECAR_SUFFIXES`:

KYO-186 adds the third line, and it is the one with a *safety* argument behind
it rather than only a tidiness one. `sessions.json` is written by the session
hooks (`kyo sessions --hook`) and carries a claude.ai Remote Control session id
per session; `kyosei/export/` is git-canonical and this project intends to
publish it, so the file must be on the local-state side of that line with
`kyosei.db`, never on the committed side. It is derived and rebuildable in
exactly the same sense: the next session that runs writes its own entry back.
Ignoring it is also what keeps a dispatched worker green -- a worker's own
`SessionStart` writes one in whatever store it resolves, and an untracked file
nobody ignored is a dirty tree at somebody's stop.

## KYO-206

From `scaffold.install_report_gitignore`:

KYO-206.c1, and the half of it that has to reach a project nobody edits by hand. The
brief names one report path; a path that is only *named* still lands in `git status`
as an untracked file, which is how the consumer's workers came to commit theirs. So
the entry is written by the two commands a consumer already runs — ``kyo init`` for
a new project, ``kyo install-hooks`` for one that predates this — rather than left in
a README instruction a maintainer has to notice and act on.

## KYO-215

status: amended
amended-by: KYO-362

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-215: the line KYO-31 shipped, superseded by the removal of `kyo html`. This
entry is the whole of how the retirement reaches a project that already installed
the hooks, and containment is *worse* than blind here — it is actively wrong:
{kyo sweep, kyo doctor} is a strict subset of {kyo sweep, kyo html, kyo doctor},
so `already_present` is True and `merge_hooks` adds nothing at all. Left to
containment the consumer keeps running `kyo html` on every stop against a build
that no longer has the command, and their closeout chain fails at step two
forever, silently, on every session. Exact-string supersession rewrites the line.

From `scaffold.DB_SIDECAR_SUFFIXES`:

KYO-215 removed the second line, `html/`. It ignored `kyo html`'s regenerated
static viewer, and that command is retired -- an ignore rule for output no build
produces is a line every later reader has to work out is dead. A project that
still HAS the directory keeps its own ignore line, which is theirs and untouched;
`kyo doctor`'s `retired_html_directory` notice is what tells them nothing writes
it any more.

## KYO-309

status: amended
amended-by: docs/design/doctor.md#kyo-466

From `install_skill.skill_drift`:

KYO-309: *which* file is the source depends on which side of the package the
project is on, and the check was written for one side only. For a **consumer**
the packaged copy is the source and the project's is the copy, so a difference
means the project is stale and `kyo install-skill` is the remedy — unchanged
below, and every project without :data:`PACKAGED_ASSET_SOURCE_DIR` takes that
branch. For the **repository the package is built from** the direction is
reversed: ``.claude/skills/kyosei/SKILL.md`` is what the next build's packaged
copy will be made of, and the `kyo` running this check may be an installed build
from before the last edit. There, comparing against the packaged copy makes a
finding out of the build's age — one nobody in the session can act on, whose
named remedy would overwrite the newer skill with the older one. So there the
comparison is against the repository's own source, which is a real mirror
(``tests/test_skill_cli_sync.py`` requires the two to be identical, up to the one path
substitution `kyo install-skill` makes since KYO-593), and the
build's age is :func:`installed_build_lag`'s notice instead.

From `install_skill.installed_build_lag`:

That is a real condition and the reader may need it — an orchestrator editing the
skill wants to know the installed build still documents the previous one — and it
is one **nobody can clear from the session**, which is the whole of the notice
tier's bar (KYO-214): the remedy is to re-pack and reinstall the tool, which is a
build, not a repair, and exit 3 would paint every `Stop` hook red until someone
did it. The remedy it must never name is `kyo install-skill` / `kyo
install-procedures`: both write the *older* packaged text over the newer source.

## KYO-316

status: amended
amended-by: KYO-362, KYO-450

From `install_hooks.PATH_GUARD_MESSAGE`:

MEASURED FIRST, per event, against Claude Code 2.1.278 on 2026-09-19 — a scratch
project whose hooks named a command that does not exist, driven by ``claude -p
--output-format stream-json --verbose``. Every event this project installs into
(``SessionStart``, ``PreToolUse``, ``Stop``, ``SubagentStop``, ``SessionEnd``)
reported ``exit_code: 127``, ``outcome: "error"``, and **nothing blocked**: the
Bash tool call ran, the subagent stopped, the session started and ended. A control
run in the same fixture shape, whose ``PreToolUse`` hook exited 2, blocked the tool
call — so the pass above is the 127 bucket, not a hook that never fired. The
documented rule behind it (code.claude.com/docs/en/hooks, "Other exit codes"): any
exit code other than 2 is a non-blocking error, the action proceeds, and the
transcript shows a ``<hook name> hook error`` notice carrying **the first line of
stderr**. What reached a reader before this was that notice carrying
``/bin/sh: kyo: command not found``, which names no remedy, while the push, suite,
worker-branch and cleanliness guards silently did not run.

From `install_hooks.PATH_GUARD_MESSAGE`:

SO THE MESSAGE GOES TO STDERR, ON ONE LINE, AND THE EXIT IS 127, NOT 2 — the same
shape on every event, deliberately:

* stderr rather than stdout, because a hook that exits 0 has its stderr sent to the
  debug log only and Claude never sees it: exiting 0 here would *hide* the message
  a reader gets today. One line, for the reason the paragraph above gives: whichever
  way the harness is rendering stderr this month, one line is the only shape that
  reads well in both.
* 127 rather than 2, decided per event from the measurement above. On
  ``SessionStart`` and ``SessionEnd`` exit 2 is not honoured at all, so there is no
  blocking choice to make. On ``PreToolUse`` it would block **every Bash tool call**
  in the project — the matcher is ``Bash`` and, without `kyo`, the hook cannot tell
  a `git push` from an `ls`, so a guard that fires on one command would start
  refusing all of them, which is the KYO-33 failure `check-push` was designed not to
  repeat. On ``Stop`` and ``SubagentStop`` exit 2 prevents the stop and continues the
  conversation, on every turn, over a condition nobody woken can clear — the
  unclearable block KYO-128 measured at ~229,000 tokens on a single dispatch.
* 127 rather than 1 because that is the code the shell already returned for exactly
  this condition; it keeps the observed behaviour identical and only changes the text.

When `kyo` IS on PATH the guard is one ``command -v`` and the hook runs untouched,
keeping its own exit status — the idiom :data:`PRE_PUSH_SCRIPT` has used since
KYO-60 (``command -v kyo >/dev/null 2>&1 || exit 0``), which is why the git
``pre-push`` hook is the one hook `install-hooks` writes that never had this defect
and is left as it is: it already handles a missing `kyo`, and its silence there is
deliberate (a human pushing their own repository did not ask to be told, on every
push, about a tool they have not installed).

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-316: every command this build shipped BARE, superseded by the same command
behind the PATH guard. Containment cannot carry this one either, and here it is
blind in the direction that matters most: :func:`hook_action` deliberately makes
the guarded line and the bare line compare EQUAL, so `already_present` is True for
every existing consumer and `merge_hooks` would leave the unguarded line exactly
where it is, forever. That equality is what stops a duplicate group and a
false "you are missing this command"; exact-string supersession is what makes the
upgrade actually happen. The two halves are the mechanism together, and dropping
either one silently reverts this record for every project that already installed
hooks — which is every project this matters for, since a project without hooks has
no bare line to fix.

KYO-316 also changed what KYO-27's entry names. A value is still only the name of the
replacement, as KYO-27 says; since this record that replacement is the packaged line behind
the guard (`guarded(...)` in the table), so the line a consumer three releases behind reaches
is the guarded one.

It also ended KYO-17's "named verbatim in both `kyo doctor`'s report and `install-hooks`'s change
log" for a guard-only entry: `_supersession_note` says the command moved behind the PATH guard
instead of printing a ~300-character replacement that differs from the installed line only by
its prefix.

## KYO-318

From `install_hooks.hook_drift`:

KYO-318.c2 establishes that this check had KYO-309's reversal too, one step
removed, and fixes it on the same terms. The suspicion was right and the shape is
exactly :func:`skill_drift`'s: "behind this build" is the right question for a
consumer and the wrong one for the repository the build is MADE from, where
``src/kyosei/defaults/hooks/`` is what the next build will ship and the installed
`kyo` may predate the last edit. There, a hook group deleted from the source and
from ``.claude/settings.json`` was reported as one the project is missing, naming
`kyo install-hooks` — which would have put the deleted group back. So the
comparison is against this repository's own fragments where it has them, and the
build's own age is :func:`installed_build_lag`'s notice instead.

From `install_hooks.hook_drift`:

Two details KYO-316 changed since that suspicion was filed, checked rather than
assumed: every comparison here goes through :func:`command_elements`, which strips
the PATH guard (:func:`hook_action`), so a guarded packaged line and an unguarded
installed one compare equal — the reversal is about which *directory* is read, and
survives that stripping untouched. :data:`SUPERSEDED_HOOK_COMMANDS` is the one
finding here that does NOT come from a fragment: it is an exact-string table
compiled into the running build, so in this repository it is a statement about the
build rather than about the source, and it names `kyo install-hooks` only when that
command is safe to run.

From `scaffold._source_repo_refusal`:

KYO-309 turned the READ side round: in the repository this package is built from, the
checks compare against `src/kyosei/defaults/` and never name a remedy that would
destroy the newer text. It left the remedies themselves alone, and they are still
commands a person can type. `kyo install-skill` run by hand here from an older
installed build still writes that build's older skill over the newer one; the two
other installers have the same hole in their own shapes.

## KYO-322

From `scaffold.DB_SIDECAR_SUFFIXES`:

KYO-322 adds the database's two SQLite sidecars, and they are DERIVED from
:data:`DB_NAME` rather than spelled beside it: three literals that must agree
about one filename is two chances for them not to. The store opens its database
in WAL mode (``PRAGMA journal_mode = WAL``, ``kyosei/store.py``), and SQLite keeps
``kyosei.db-shm`` and ``kyosei.db-wal`` beside it for as long as any connection is
open -- which, with the desktop app following the store, is always. Ignoring
``kyosei.db`` and not those two produced all three of: `git add kyosei` failing
with "unable to stat 'kyosei/kyosei.db-shm'" because the file vanished between the
status and the add; a dirty tree at a worker's stop for a file nobody wrote on
purpose; and the standing risk that somebody commits a WAL file, which can carry
rows the export does not have yet.

From `install_gitignore.install_store_gitignore`:

KYO-322.c1's "an existing project gets the lines by a command it already reruns
after an upgrade". `kyo init` writes the whole file and every project that predates
a new line never hears of it again, which is the *fourth* instance of this
codebase's standing shape: we ship code that updates itself and copies that never
do (KYO-15, KYO-20, KYO-212 are the other three).

## KYO-328

From `install_skill.SKILL_REFERENCE_ASSET`:

Why a FILE beside the skill rather than a `kyo` command that prints it. The
reference is prose this package already ships and `install-skill` already writes a
file into that directory, so a second one reuses the whole drift/refusal machinery
(``skill_drift``, ``install_skill_refusal``, `kyo doctor`'s ``skill`` check) for one
tuple entry. A command would need a parser entry, a handler and a second surface for
`tests/test_skill_cli_sync.py` to police — and, worse, it would print the WHOLE
document into the context this split exists to keep it out of, where a file can be
read one section at a time, with the same tool an orchestrator already uses, in a
worktree with no store and from a build that cannot open one.

## KYO-342

From `install_procedures._git_worktree_facts`:

KYO-342 moved the ``git rev-parse`` calls themselves into
:func:`kyosei.config.git_worktree_facts`: store *resolution* needs the same three
facts, and a second copy of "how do I know I am in a linked worktree" is exactly
the pair law 3 forbids — the two consumers ask the identical question for opposite
purposes, one to refuse a write from a worktree and one to resolve a read out of
it. What stays here is the shape this module's two refusals read (a tuple, main
checkout as a string, because that is what they interpolate) and
:data:`MAIN_BRANCH`, which is this module's choice of which branch to name.

## KYO-362

From `install_hooks.PATH_GUARD_MESSAGE`:

KYO-362 corrects the "first line" half of that measurement, and only that half: a
later observation of a failing ``Stop`` hook showed the notice
carrying **all** of stderr, filling the terminal with a multi-line `kyo doctor`
report. How much is rendered is a property of the harness and has moved between
builds, so nothing here should depend on it. One line is still the shape every
packaged hook message takes — under the older behaviour because the rest is
discarded, under the newer one because the rest is rendered — and the exit-code
reasoning below is untouched, having been measured on the bucket, not on the text.

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-362 re-pointed this one, on KYO-215's precedent and for its reason: the value
is only ever the NAME of the replacement, so a consumer still carrying the bare
pre-KYO-316 chain reaches the current line in one run either way, and leaving it
pointed at a chain this build no longer installs would only make doctor and
install-hooks announce a command that does not exist. The guard-only pairing for
the chain this build DOES install is the entry directly above.

From `install_hooks.SUPERSEDED_HOOK_COMMANDS`:

KYO-362: the Stop/SessionEnd chain gained `--hook` on its last command, and this
is KYO-94's case one flag along — containment cannot carry a flag, because
:func:`command_elements` splits on `&&` and `kyo doctor` and `kyo doctor --hook`
are two different elements. Left to containment a consumer would be told they are
missing `kyo doctor --hook`, get a SECOND group wired beside the one they have,
and then run doctor twice on every stop — once in the form that fills their
terminal with the whole report, which is the defect this record exists to end.
The entry above supersedes the bare pre-KYO-316 spelling of the same chain; this
one supersedes the guarded spelling every project that ran `kyo install-hooks`
after KYO-316 is actually carrying, and without it that is every consumer.

From `scaffold.store_gitignore_text`:

KYO-362 adds the fourth name, and it reaches an existing project by exactly the
route the third did: :func:`install_store_gitignore` tops the file up per pattern
on every `kyo install-procedures`. `kyo doctor --hook` writes
:data:`kyosei.config.DOCTOR_REPORT_NAME` beside the database whenever a check
fails, and an untracked file that appears at one stop and vanishes at the next is
a dirty tree at somebody's stop — the same failure the WAL sidecars produced.

The `--hook` re-point reached KYO-27's and KYO-31's entries too: both now name
`kyo sweep && kyo doctor --hook` behind the guard, and the `kyo sweep && kyo html && kyo doctor`
that KYO-31 called the fix is itself a superseded key (KYO-215's). It also ends KYO-215's
"strict subset" argument for today's line: `kyo doctor --hook` is a different element from
`kyo doctor`, so containment would now add a group rather than nothing, and the entry is what
rewrites the old chain.

## KYO-403

From `install_gitignore.install_worktree_home_gitignore`:

KYO-403.c5. Under the split layout the worktree home is
``~/kyosei/<store>/dispatch worktrees/`` — :func:`kyosei.config.project_home_name`
is the store directory's own name, so one directory holds a project's store and
its worktrees (KYO-70) — and that directory is the store's git working tree. Every
worktree an orchestrator cut there showed up in the store repository's `git status`
as an untracked ``dispatch worktrees/``, one careless ``git add -A`` away from
committing a checkout of the code repository into the store's history.

Ignoring the home, rather than moving it, is the choice KYO-403 made: moving it
re-points every stored ``<worktrees>/<name>`` token (they resolve against the home
at read time) and so needs a fallback for each worktree still running at the old
location, and the one hazard the nesting actually carried — a `kyo` in such a
worktree climbing out of it into the store — is closed at its cause by
:func:`kyosei.config.resolve_layout` stopping at the worktree's git toplevel.

## KYO-407

From `scaffold.store_gitignore_text`:

KYO-407.c8 adds two temporary-file patterns, both derived rather than spelled. A
writer killed between creating its temporary file and renaming it into place leaves
the temporary behind, and nothing ignored it, so the orchestrator's next ``git add
kyosei/`` committed it: ``sessions.json.<pid>.tmp`` from
``cmd_sessions.write_sessions``, which follows ``sessions_name``, and every export
file's ``.<name>.<random>`` + :data:`kyosei.export.ATOMIC_TEMP_SUFFIX` from
:func:`kyosei.export.atomic_write_text`. A pattern without a slash matches at any
depth, so the one line covers ``export/`` and ``export/_events/`` alike.

## KYO-416

From `scaffold.init_store_repository`:

KYO-416.c7: the scaffold commit is Kyosei's, made on the consumer's machine
under the consumer's global git config — so what that config would add to it
is pinned off here. `commit.gpgsign=true` with a GUI pinentry blocked this call
waiting for a human; a global `core.hooksPath` or `init.templateDir` pre-commit
hook ran over the scaffold and could refuse the first commit, leaving exactly
the uncommitted store the note below calls a durability loss. `core.hooksPath`
at the null device runs no hook at all (post-commit included), and
`--no-verify` says the same for the two hooks it covers in case a git reads
the path differently.

## KYO-450

Why the PATH guard's message changed and the old one is still read
(`install_hooks.RETIRED_PATH_GUARD_MESSAGES`):

KYO-316's guard message named "in the Kyosei app, Install command line tools" as the first
remedy. KYO-450 removed that action with the app's bundled runtime (KYO-443): `kyo` is a
uv tool on the login PATH, so the message names `uv tool install kyosei` (or
`pipx install kyosei`). That changes KYO-316's text, not its shape: one line, on stderr, exit
127. A line a consumer installed under the old message is still the same recipe, so
`hook_action` strips a retired prefix as well as today's, and each `SUPERSEDED_HOOK_COMMANDS`
entry is derived again behind every retired prefix -- a guarded key, and a bare key whose
replacement is itself guarded -- and carried to today's line, the way KYO-316 carried the
bare lines to the guard. `_supersession_note` says that upgrade is the guard's current message
rather than restating a ~300-character command. Keys with a different replacement (a
reordering, a new flag) were never shipped bare behind the old guard and are not derived.

Two older sections read differently since. KYO-17's verbatim naming gains this second exception
(the retired-prefix note). KYO-398's premise that a re-pack upgrades every consumer at once
became a reinstall of the one uv tool, which is what `kyo check-build`'s line names now
(docs/design/doctor.md#kyo-466).
