# `kyo doctor`: the incidents behind the checks

`kyo doctor`'s registry is `src/kyosei/doctor.py` (`CHECK_ORDER`, `CHECKS`, `NOTICE_CHECKS`,
the tiers, `HOOK_FORMS`, `run_checks`, `sort_into_tiers`). The checks themselves live by subject
in `doctor_install.py` (schema version, hook config, skill, build lag, procedures, setup
commands), `doctor_consumers.py` (consumer strings and the doctor cache), `doctor_registry.py`
(kinds, grants, scopes, gate tags, docs suggestions), `doctor_exports.py` (currency, exports, the
round-trip and its hook form, allocations, orphans), `doctor_records.py` (criteria, redactions,
statuses, plans, links, flags) and `doctor_dispatch.py` (events ledger, dispatch state, stops,
reports, back-links). They were one module, `doctor.py`, until KYO-433 split it.

The code states the rule each check enforces and why a reader would otherwise break it. This file
keeps the history those rules came from: what was measured, what broke, and what the earlier
code did. One heading per record, in record order, so a comment in the code can point at
`docs/design/doctor.md#kyo-<n>`. The laws themselves are in `docs/design/charter.md`.

These passages were moved out of the docstrings and comments by KYO-433. They record what was
true when each record was worked, and they are not kept current: the numbers are measurements of
one store on one date.

Whether the rule a section explains still holds is kept current: a section a later record
changed carries `status:` and `amended-by:` or `superseded-by:` under its heading, and
`kyo docs check` holds the status and the links to agree (docs/design/docs.md#kyo-499).

## KYO-19

`check_criterion_verification_agreement` used to demand that every criterion's state agree with
its latest verification, including the two void states no verification can record. That made a
legitimate command produce an unrepairable store: `kyo split` voids the criteria it carries to
the successor, so splitting any criterion that had ever been verified failed doctor permanently
-- `kyo verify` cannot record a void verdict by design, so no sanctioned command could restore
agreement. Reported by a consumer project and reproduced here from a clean store in four
commands; see `tests/test_doctor_void_criteria.py`.

## KYO-20

`check_hook_config` is the only check that reads a file the tool does not own. Kyosei ships
consumers two kinds of thing: code, which updates the moment the package does, and
configuration, which is a copy that never does. When KYO-17 fixed the `SubagentStop` loop that
had fired one consumer's worker ten times, the fix reached their `kyo` instantly and their hooks
not at all -- and nothing anywhere said so.

## KYO-23

`_first_difference` takes its two strings by keyword because `check_export_roundtrip` once
passed them the wrong way round, and reported the rebuilt content as "on disk" and the file's
real content as "derived". The substance was right and the direction was backwards, which is
the worst shape a diagnostic can take: a consumer chased it into the file to find out which half
to believe (reported by a consumer project after the v6 migration).

## KYO-27

`check_procedures` answers the bug that produced it, one step upstream: "never state a
tool-derivable fact from memory" reached only workers because its audience named only workers,
and nothing anywhere noticed that the person most able to break it could not see it.

## KYO-62

`COLUMN_CONSUMERS` and `CONFIG_CONSUMERS` were checked in one direction only: their table and
column NAMES against the live sqlite schema. Nothing looked at the right-hand side, so a consumer
string could name a function deleted three milestones ago and the check that exists to make
law 2 answerable would pass, with the tool's authority behind an answer that was false. Measured
when this was found: of 360 strings in `COLUMN_CONSUMERS`, 88 resolved to nothing (20 distinct
names, six of them already broken before the refactor that made the rest visible), and one more
was a wildcard that could not be evaluated at all.

The first measurement resolved module-level attributes only, so `store.allocate` and
`store.get_meta` -- real methods on `Store` -- counted as broken. That is why a name is resolved
against class members as well.

Findings are grouped by reference because a single deleted function printed once per site
(`render.show` was declared by eighteen columns) buried the other nineteen findings under it.

For `CONFIG_CONSUMERS` all five strings resolved when the resolver was added; the point was that
the sixth could not rot unnoticed.

## KYO-74

`check_exports` used to state two sentences unconditionally: "the database is authoritative --
exports are generated, never hand-edited" and "(stale export)". Both are still exactly right
where they were written: a store whose export is behind its database, or whose export document
was hand-edited, and neither was deleted. Until KYO-56 made the split layout the default only
that side could ever move. After it, a consumer who acted on either sentence in the pulled case
regenerated the export from a database that was behind it and destroyed the pulled work -- and
the first sentence's own wording is what recommended that. They are now stated only in the cases
where they are true.

## KYO-157

`unresolved_stops` shipped emitting a line per unresolved stop and reached 342 rows within
hours, which made `kyo doctor` -- the surface that reports every other invariant -- unreadable,
and drowned a real `export_roundtrip` failure that appeared once and was nearly missed. It has
reported one finding since.

## KYO-163

status: amended
amended-by: docs/design/dispatch.md#kyo-373, docs/design/dispatch.md#kyo-375

The rows `unresolved_stops` counts are dominated by a signal that is not a worker stopping at
all: Claude Code delivers `SubagentStop` on a ~32-second timer per running subagent, with a fresh
agent id every tick (measured; see the KYO-163 block in `cmd_dispatch.py`). One session of this
project's own ledger recorded 365 such rows against 40 agents ever spawned.

The choice was between not WRITING those rows and not REPORTING them, and it went to reporting.
Not writing would mean deciding, at the moment of the stop, that this particular agent is not a
worker -- and the only thing available to decide it on is the absence of a transcript and a spawn
sidecar, because the hook payload is otherwise shape-identical for a tick and for a real worker.
Classifying on the absence of a trace is exactly what KYO-154's dispatch was offered and refused:
it would silently stop auditing a real worker whose sidecar the harness failed to write, which is
the case the audit exists for (KYO-163.c3). So every row is still written, every row keeps its
outcome, and the finding adds the population's measured signature so the count reads as what it
is.

KYO-163 wrote "and does not stop failing" in that comment, meaning the count must not be filtered
down to "probably real" rows. That is still true of the count, and is what
`tests/test_kyo163_phantom_stops.py`'s
`test_the_doctor_finding_does_not_shrink_when_the_tick_is_named` pins. The exit code was a
separate question, and KYO-214 answered it the other way.

## KYO-212

`kyosei/procedures.md` was the worst of the copies Kyosei ships, because nothing even reported
its drift: `kyo init` copied the packaged default once and no command ever read it again, so
KYO-199's worker-audience rule reached no project that already had a store, and no surface said
so. Before the ledger checks existed, the only other instances of the shape were `hook_config`
and `skill`.

## KYO-213

The comparison `_ledger_comparison` makes used to live in `doctor.py`. KYO-213 moved it into
`install_procedures.procedures_ledger_drift`, which `kyo brief`'s asset-drift block reads too,
and left the doctor helper as a pass-through so the two ledger checks kept their call shape.

## KYO-214

Before KYO-214 every finding failed `kyo doctor` with exit 3. `unresolved_stops` said of itself
"this is not a block and never was" (since KYO-157), and the tool contradicted it: `kyo doctor`
runs last in this project's `Stop` and `SessionEnd` hook chain, Claude Code renders a hook
exiting nonzero as an error block, and an operator saw that paragraph rendered as a hook error
on every stop for the whole duration of every dispatch, in every project running the hook.

KYO-214 made the tier a property of the check. It weighed three candidates for the notice tier
and moved one. `skill` and `hook_config` stayed problems because each names a remedy command,
which is precisely the case where the reader can act; the recommendation for each is on
KYO-214's report (KYO-214.c3), and they want deciding together with KYO-213's session-start
surface. The third candidate, `html_freshness`, no longer exists (KYO-215).

## KYO-215

`retired_html_directory` replaces `html_freshness` (KYO-27), which asked whether the generated
site was behind the store. `kyo html` is gone -- the desktop app is the viewer, and a project
without it reads with `kyo show`, `kyo list`, `kyo map` and `kyo brief` -- so the site cannot be
behind, it can only be abandoned, and a directory of pages that stopped updating on an unknown
date is exactly the "reads as authoritative" failure `html_freshness` was filed about.

## KYO-216

A format-version bump used to produce one finding per export document: measured live, 198 of
them from `exports` and 198 more from `export_roundtrip` for a single bump.
`_schema_version_stamp_finding` reports the set
once.

## KYO-267

`ungated_unobserved_criteria` is the first notice that names a remedy command, which the bar on
`NOTICE_CHECKS` otherwise says is the case that stays a problem. KYO-267.c3 asks in as many words
for a warning that does not fail. The argument for the exception: the condition is supposed to
occur -- recording `unmet-unobserved` for a person's observation is correct behaviour -- and an
exit 3 on "you have not gated it yet" would paint every `Stop` hook red for the duration of every
human-observed verification, which is the KYO-214 failure exactly. Any later remedy-naming notice
has to argue the same two things: that the condition is expected rather than broken, and that
failing on it would cost the reader more than the finding is worth.

## KYO-604

`hook_config` and `skill` stayed problems under KYO-214 because each names a remedy. Measured
2026-10-12 after KYO-591's build was installed: a consumer that had not rerun `kyo
install-hooks` got 9 `hook_config` findings, every one a hook that an earlier build packaged and
that still ran, and every `Stop` hook exited 3. Any release that changes a packaged hook's or
the skill's text does the same to every project on the copies, which is the KYO-214 failure
again.

The tier is a property of a check name, so the split is by finding into a second check each:
`hook_config_superseded` and `skill_superseded`, both notices, both read from the same
comparison as their problem-tier twin (`install_hooks.hook_drift_by_tier`,
`install_skill.skill_drift_by_tier`) so the two cannot disagree. They make KYO-267's two
arguments: an earlier build's unedited text is the expected state after any upgrade, and failing
on it costs a hook-error block on every stop. A hook is superseded only when its command is
exactly a `SUPERSEDED_HOOK_COMMANDS` key; a packaged command missing only because a superseded
line runs its earlier form (the earlier `Stop` chain lacks `kyo sweep --hook`) is not reported a
second time. Nothing recorded earlier builds' skill text, so `kyo install-skill` now ends each
file it writes with the digest of that text. The digest is in the file rather than beside it,
because a sidecar is one more file a consumer must commit and a record outside the repository
is absent on every other machine. A copy that differs and has no digest, or no longer matches
it, is still a problem.

## KYO-268

What the monolithic `_events.jsonl` cost on this project's own store, measured 2026-09-16: 224.1
MB per copy, 5,783 loose objects totalling 7.23 GiB, `git pack-objects` at 22 GB resident
against 27.6 GB of swap, and the machine unusable while it ran, with nothing in the tool naming
the cause. Saying nothing would leave a consumer to meet a 20 GB `.git` and diagnose it from scratch.
The design of the shards is in `docs/design/export.md#kyo-268`.

## KYO-298

This project's own `worktree_setup` once said `make test`, the standing rule in every
brief said "run the tests that cover your change, not the whole suite", and two of five workers
followed the config -- correctly, because a brief that tells a worker to run a command is a
stronger instruction than a rule telling it not to. `kyo check-command` refuses that at the
moment it happens; `orchestrator_only_setup` says so at the moment a maintainer reads their board,
which is when the config can be changed.

KYO-519 renamed the check (it was `controller_only_setup`) and gave it a second notice: the key
declared under its old spelling, `controller_only_commands`. That notice meets the same bar. The
old spelling is read permanently, so nothing is broken; and its remedy, renaming the key by hand,
is safe only once no `kyo` older than KYO-519 reads the store, because an older build refuses the
new spelling and every hook goes blind (KYO-335). So the notice names that precondition and the
`path_build_lag` check that confirms it, and no command makes the edit.

## KYO-303

`docs_suggestions` is the notice whose argument is that it must not be a problem at all, rather
than that failing would cost too much. A suggestion is a draft of a declaration the project owns;
making `kyo doctor` fail over one would be the tool asserting that a project's own
`documents.yml` is wrong on the strength of a heuristic about heading shapes, which is what the
record says never to do. It is also the first notice a project can switch off
(`docs_suggest_notice: false`), because a suggestion that cannot be declined is not a
suggestion.

## KYO-309

status: amended
amended-by: KYO-466

`skill`, `procedures_rules_missing` and `procedures_rules_differ` all assumed the packaged copy
is the source and the project's is the copy -- true for every consumer, and backwards in the
repository the package is built from. There the old `skill` finding was half true and half
unactionable: the installed build's packaged skill and this checkout's skill did differ, but
calling that the project's drift, failing on it, and naming `kyo install-skill` would have
overwritten the newer skill with the older one. Nothing in the session could clear it -- the
remedy is a re-pack and a reinstall -- and this repository's `Stop` hook went red after every
skill edit until the build was reinstalled. KYO-309 split the true half
into `installed_build_lag`, a notice that is structurally silent in every consumer project.

## KYO-335

The defect behind `path_build_lag`, measured on this store on 2026-09-19 and written up on
KYO-325: the orchestrator wrote to `kyosei/config.yml` (or bumped `schema_version`) from one build,
every hook in the project ran a different build, and the moment the second one refused the store
every guard in the project exited without checking anything. It lasted 120.5 minutes and nothing
in the project said a word -- the `SubagentStop` guards reported on the orchestrator's checkout
while three workers' trees went unexamined, and the window closed only when an unrelated
reinstall brought the two builds back into agreement.

## KYO-362

`MAX_FINDING_CHARS` is a number rather than a principle because the principle -- "do not put a
whole collection in a message" -- is what two checks were already violating when it was written.
One of them, measured in a consumer, was the allocations finding: it used to be
`on disk {whole dict} vs rebuilt {whole dict}`, and in a large store that is tens of kilobytes of
terminal to say that one counter is off by one. It ran from the `Stop` hook, so the operator read
both maps in full on every stop. The round-trip
check's own line was `f"on disk {on_disk_allocations} vs rebuilt {rebuilt_allocations}"`.

## KYO-373

The stop-ledger census rides beside `unresolved_stops` because that check counts only unmatched
worker stops on running dispatches, which left a reader unable to tell 3 unmatched out of 30 from
3 out of 6,514 -- and the second is what this project's own ledger was, with 6,277 of the rest
being agents that were never workers.

## KYO-394

The measured case: a consumer store scaffolded before KYO-228 kept a
`kinds.yml` without `intake` on its work kinds, `kyo new task` refused, and nothing had said so
beforehand -- `kyo install-procedures` merges the ledger and no command merges `kinds.yml`, so
the copy `kyo init` made is the copy the project keeps. The packaged default itself withheld
`intake` from `task` deliberately until KYO-228, which is why a narrowed kind is a legitimate
registry and the check is a notice.

## KYO-408

Before the doctor cache, `kyo sweep && kyo doctor --hook` -- which runs after every assistant
turn -- re-parsed the package's own source and rebuilt the WHOLE store from export into a
temporary database on every run: 2.6 s, ~850 MB resident and a ~300-590 MB temporary directory
per stop on this repository's own store (measured 2026-09-25), linear in the size of the events
ledger. The module index's in-process memo replaced an `lru_cache`, which did not see a module
edited during a long-lived process.

The ledger id shape the incremental tier relies on (`LEDGER_ID_RE`) held for every line of all
four of this repository's ledgers when it was written: 14,842 lines, 2026-09-25.

## KYO-414

`check_allocations` used to compare a hand-written copy of the scope list rebuild seeds from. That
copy had already dropped `doc_refs` (`x`) and `docs_obligations` (`o`), so a lowered counter in
either was never reported.

## KYO-415

Before `completed_with_unmet_criteria`, `kyo show` read `done` beside `unmet-unbuilt`, and
`doctor`, `next` and `brief` all said nothing. `kyo verify` prints the same line at the moment it
records such a verdict (KYO-415.c7); the check is the half that lasts.

## KYO-466

Why `installed_build_lag` names reinstalling kyo, not a re-pack
(`doctor_install.check_installed_build_lag`, `install_skill.installed_build_lag`):

KYO-309 gave this notice the remedy "a re-pack and a reinstall", because the `kyo` the hooks
ran then was the desktop app's bundled runtime. KYO-450 retired that runtime: the machine runs
one `kyo`, a uv tool, and the app runs the same copy, so the notice's remedy became
`buildinfo.upgrade_route` for the running build -- `make install-tool` for an unreleased
snapshot of a checkout, `uv tool upgrade kyosei` for a release. KYO-466 removed the re-pack
wording that was left: in the skill reference, in the schema-mismatch refusals' sentence for a
reader who did not install the build (`contracts.installed_build_clause`, which asks
`upgrade_route` instead of restating a route), and in every printed notice that still told a
user to re-pack the app to upgrade kyo, `kyo check-build`'s "`kyo` on PATH is now" line among
them. KYO-309's direction rule is unchanged: never `kyo install-skill`, which would write the
installed build's older skill over this checkout's newer source.

That `kyo check-build` line is KYO-398's build-change notice, whose premise -- one `kyo` per
machine, so one upgrade moves every consumer -- still holds; only the upgrade is now
`uv tool upgrade` or `make install-tool` rather than a re-pack.
