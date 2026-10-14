# `kyo` command reference

**This file is generated from the argparse tree in `src/kyosei/cli.py`. Do not edit it by hand — regenerate it with `kyo reference --output <wherever you keep it>`.** Kyosei's own copy is `docs/reference/cli.md` (in Kyosei's own repository), regenerated with `kyo reference --output docs/reference/cli.md` and kept honest by `tests/test_kyo138_command_reference.py` (in Kyosei's own repository), which fails if that copy and the parser disagree. Nothing checks yours, so regenerate it after any upgrade (KYO-138.c4, KYO-156).

Kyosei (共生) — structured store for ideas, work, and evidence.

## Options accepted everywhere

Each of these is accepted both before the command (`kyo --json show ID`) and after it (`kyo show --json ID`). They are listed once here and not repeated under each command below; every command section names them by reference.

- `-h`, `--help` — optional. show this help message and exit
- `--root ROOT` — optional. project root (default: search upward)
- `--actor ACTOR` — optional. who is making this change; falls back to $KYOSEI_ACTOR, then 'orchestrator' (rows written before KYO-519 say 'controller', so a query for the orchestrator matches both)
- `--json` — optional. machine-readable output
- `--full` — optional. with --json: embed every record document a command returns whole (`record`, `records`, and the same inside `kyo accept`'s steps[].data). Without it --json replaces each by its id, kind, status and updated_at plus the fields this invocation changed (`changed_fields`, and the new value of each short scalar one) — ids and changes, not documents. `kyo show --json` is the read and always prints the whole record; `kyo show --fields` narrows it
- `--slim` — optional. with --json: accepted and changes nothing — the slim form is what --json prints by default since KYO-427; kept so scripts that pass it keep working
- `--compact-json` — optional. with --json: print the envelope on one line, error envelopes included, even on a terminal (the default already does so when stdout is not a terminal)
- `--pretty` — optional. with --json: print the envelope indented, error envelopes included, even when stdout is not a terminal (the default indents only on a terminal)

## Command index

| command | needs a project | writes to the store | subject |
| --- | --- | --- | --- |
| [`kyo accept`](#kyo-accept) | yes | yes | apply an ingested report you have read — its criteria verdicts, its docs verdicts and its touch-drift correction — as one act (KYO-331) |
| [`kyo activity`](#kyo-activity) | yes | no | the dispatch activity rows: every running dispatch and the newest finished ones, with record, verdict counts, worker tokens, end time and what git says about each worktree; read-only (KYO-454) |
| [`kyo attach`](#kyo-attach) | yes | yes | attach an evidence artifact, stored verbatim (law 5, 9) |
| [`kyo base`](#kyo-base) | yes | no | saved view definitions (kyosei/bases.yml) over records, ideas/ or docs/: list them, or run one read-only |
| [`kyo brief`](#kyo-brief) | yes | yes | the session briefing, derived from the store (M6) |
| [`kyo briefs`](#kyo-briefs) | yes | no | list the session briefs the store kept, newest first, or show one with the shift notes and ledger entries between it and the brief before; read-only, unlike `kyo brief` (KYO-454) |
| [`kyo changelog`](#kyo-changelog) | no | no | print what changed in Kyosei: one line per completed record, grouped by scope, newest first; defaults to what is newer than the build you are running; no project needed (KYO-226) |
| [`kyo check-branch`](#kyo-check-branch) | optional | no | merge guard: fail if a branch diffs kyosei/ or ideas/ (M8) |
| [`kyo check-build`](#kyo-check-build) | optional | no | has the `kyo` under this session changed since its opening brief? Compares the running build with the one this session's session-start brief recorded; read-only (KYO-398) |
| [`kyo check-clean`](#kyo-check-clean) | optional | no | worker stop guard: fail if the worktree holds uncommitted work outside the directories `check-branch` forbids a branch to touch |
| [`kyo check-command`](#kyo-check-command) | optional | no | worker command guard: refuse a command this project declares orchestrator-only (`controller_only_commands`), or one whose declared timeout is over its ceiling (`worker_command_timeout_ceiling_seconds`), inside a running dispatch's worktree (KYO-298, KYO-312) |
| [`kyo check-push`](#kyo-check-push) | optional | no | publication guard: fail if pushing this repository would publish an unacknowledged Kyosei store (KYO-60) |
| [`kyo claim`](#kyo-claim) | yes | yes | record a claim about a record (law 9) |
| [`kyo close-wave`](#kyo-close-wave) | yes | yes | close a merged wave: move each record to completion, run the declared generators, commit store and generated files together, then print (or, with --run-after, run) the declared after-steps — refused before any write unless every record can finish and the declared suite passed at this code (KYO-536) |
| [`kyo confirm`](#kyo-confirm) | yes | yes | the maintainer looked and approves: verify the criterion met (human-observed) with their words as evidence, clear the answered human gate, and walk the record to completion (KYO-494) |
| [`kyo dispatch`](#kyo-dispatch) | yes | yes | dispatch a record with a frozen brief, or `dispatch close` a dispatch (M5) |
| [`kyo docs`](#kyo-docs) | yes | yes | Layer 3 docs-SOT skeleton validator (M8) |
| [`kyo doctor`](#kyo-doctor) | yes | no | run the tool's own invariant checks |
| [`kyo edit`](#kyo-edit) | yes | yes | change a record |
| [`kyo export`](#kyo-export) | yes | no | rewrite drifted export documents from the db (KYO-6) |
| [`kyo flag`](#kyo-flag) | yes | yes | list, acknowledge/resolve a sweep flag (M6) |
| [`kyo gate`](#kyo-gate) | yes | yes | add a gate; orthogonal to status |
| [`kyo gates`](#kyo-gates) | yes | no | list every open gate across every record, with the record it is on, and each record's open-gate count; read-only (KYO-454) |
| [`kyo harness`](#kyo-harness) | optional | no | install Kyosei into an agent harness: `install claude-code` links $KYOSEI_HOME/harness/claude-code to this build's packaged plugin, registers it with `claude plugin marketplace add`, and inside a Kyosei project enables it there at local scope (KYO-594) |
| [`kyo ideas`](#kyo-ideas) | yes | no | the ideas layer (never enforced) |
| [`kyo init`](#kyo-init) | no | no | scaffold ideas/ and kyosei/ in a project; in a new project, setup starts at `kyo onboard` |
| [`kyo install-agents`](#kyo-install-agents) | yes | no | write the worker agent types the operator chose into .claude/agents/, from the packaged templates, with the operator's model and effort (setup decision C4, KYO-607) |
| [`kyo install-hooks`](#kyo-install-hooks) | yes | no | merge Claude Code hook templates into .claude/settings.json (M8) |
| [`kyo install-procedures`](#kyo-install-procedures) | yes | no | add the standing rules this build ships that kyosei/procedures.md does not have yet, leaving every rule already there untouched unless --adopt names it (KYO-212, KYO-151.c2) |
| [`kyo install-skill`](#kyo-install-skill) | yes | no | write the packaged /kyosei orchestrator workflow, and the reference beside it, into .claude/skills/ (KYO-328) |
| [`kyo link`](#kyo-link) | yes | yes | create a typed edge; cycles refused |
| [`kyo list`](#kyo-list) | yes | no | list records, filtered and summarised |
| [`kyo map`](#kyo-map) | yes | no | render the kind registry as a graph |
| [`kyo move`](#kyo-move) | yes | yes | change status; contracts are enforced here |
| [`kyo new`](#kyo-new) | yes | yes | create a record |
| [`kyo next`](#kyo-next) | yes | no | ready work: dispatchable, ungated, disjoint from running dispatches (M6) |
| [`kyo note`](#kyo-note) | yes | yes | append to the continuously-written shift log (M6); for a note ON a record see `kyo edit --note-file` |
| [`kyo onboard`](#kyo-onboard) | no | no | guided project setup, where a new project starts: prints this build's setup steps for an agent to follow in a new or existing project, or with --check reads a project back against the same checklist and writes nothing (KYO-514) |
| [`kyo orchestrator`](#kyo-orchestrator) | no | no | start, stop, attach to or list detached claude Remote Control sessions, one per project, on this machine's `tmux -L kyosei` server -- safe to run over SSH; --install the LaunchAgent that keeps that server running from login (KYO-458, KYO-474) (also accepted as `kyo controller`) |
| [`kyo pre-tool-use`](#kyo-pre-tool-use) | optional | no | both PreToolUse guards (`kyo check-push --pre-tool-use` and `kyo check-command --pre-tool-use`) against ONE read of the payload on stdin, in one interpreter: exits 2 if either refuses, with both refusals when both do, and is silent otherwise. Opt-in: `kyo install-hooks` still wires the two commands (KYO-422) |
| [`kyo prior-art`](#kyo-prior-art) | yes | yes | search recx's index for existing work matching a record's title and description, and store the results as evidence under a derived claim, superseding the record's previous one; needs the extra kyosei[recx] (KYO-585) |
| [`kyo prior-art-setting`](#kyo-prior-art-setting) | yes | no | turn this project's prior-art search on or off: writes recx.prior_art.state in kyosei/config.yml, keeping its trigger, limit and index_url; `on` needs the extra kyosei[recx] (KYO-586) |
| [`kyo projects`](#kyo-projects) | no | no | list, add or remove the projects in this machine's registry ($KYOSEI_HOME/projects.json), which kyo serve and kyo orchestrator answer for; the registry's one writer (KYO-455) |
| [`kyo promote`](#kyo-promote) | yes | yes | promote an ideas/ file into a record; the ceremony boundary (law 13) |
| [`kyo rebuild`](#kyo-rebuild) | yes | no | reconstruct kyosei.db from kyosei/export/ |
| [`kyo redact`](#kyo-redact) | yes | yes | remove verbatim prose (evidence, claim, note, shift note, or a record's own title, description or criterion) from the store and the git-canonical ledgers, recording that it happened (KYO-426) |
| [`kyo redo`](#kyo-redo) | yes | yes | replace the --do payload on an existing gate, by its own id; not a clearing (KYO-148) |
| [`kyo reference`](#kyo-reference) | no | no | print the command-and-option reference — all of it, or one command's section — generated from this parser; no project needed (KYO-138, KYO-221) |
| [`kyo report`](#kyo-report) | yes | yes | ingest a dispatch's structured report (M5) |
| [`kyo report-schema`](#kyo-report-schema) | no | no | print the report schema `kyo report` validates against — from the installed package, no project needed (KYO-149) |
| [`kyo restate`](#kyo-restate) | yes | yes | void a criterion and add its replacement atomically, back-linked (KYO-58) |
| [`kyo retag`](#kyo-retag) | yes | yes | replace the tags on an existing gate, by its own id; not a clearing (KYO-130) |
| [`kyo rules`](#kyo-rules) | optional | no | print the standing rules in full — one by title, or the list; the text a dispatch brief carries one line of (KYO-327); `kyo rules add` writes a project rule into kyosei/procedures.md (KYO-565) |
| [`kyo serve`](#kyo-serve) | no | no | serve this machine's projects to any viewer over loopback HTTP: the read commands' own --json, each store opened read-only (KYO-453) |
| [`kyo sessions`](#kyo-sessions) | optional | no | the Claude Code sessions this project has, and each one's claude.ai Remote Control page (KYO-186) |
| [`kyo shift-notes`](#kyo-shift-notes) | yes | no | list the shift log newest first, each note marked with the session-start brief it handed off to; read-only (KYO-454) |
| [`kyo show`](#kyo-show) | yes | no | print a record, a criterion, an evidence artifact, a flag, a dispatch or a shift note, by the id that names it |
| [`kyo spawn`](#kyo-spawn) | yes | yes | create a record of another kind from an existing one; the kind-to-kind edge the registry declares (KYO-50) |
| [`kyo split`](#kyo-split) | yes | yes | split unmet criteria to a new successor record, back-linked both ways (M5) |
| [`kyo sweep`](#kyo-sweep) | yes | yes | flag stale touches and near-duplicate criteria; never edits (M6) |
| [`kyo touch-drift`](#kyo-touch-drift) | yes | no | compare a dispatch's declared touches against what its branch actually changed (KYO-71) — reports, never refuses |
| [`kyo ungate`](#kyo-ungate) | yes | yes | clear a gate by its own id; several ids, or every open gate with a tag, in one transaction (KYO-492) |
| [`kyo unlink`](#kyo-unlink) | yes | yes | retract a typed edge by its own id; the edge and the reason stay in the events ledger |
| [`kyo verify`](#kyo-verify) | yes | yes | record a criterion verification (laws 3, 9, 10) |
| [`kyo version`](#kyo-version) | no | no | print which build this copy is — package version, the commit it was built from and that commit's date; no project needed (KYO-226) |
| [`kyo void`](#kyo-void) | yes | yes | retire a knowledge-archetype record; its id is never reused |
| [`kyo where`](#kyo-where) | optional | no | locate this project and say whether this build can open its store (KYO-184) |
| [`kyo wont-do`](#kyo-wont-do) | yes | yes | retire a work-archetype record; its id is never reused |
| [`kyo worktrees`](#kyo-worktrees) | yes | no | list dispatch worktrees, and remove the ones whose dispatch is finished (KYO-70) |

## `kyo accept`

apply an ingested report you have read — its criteria verdicts, its docs verdicts and its touch-drift correction — as one act (KYO-331)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `dispatch_id`, `report_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo accept [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--reviewed] [--close] [--except CRITERION_ID [CRITERION_ID ...]] DISPATCH_ID`

### Positional arguments

- `DISPATCH_ID` — **required**. the dispatch whose ingested report to apply; `kyo report <id> --file <report.json>` is what ingests one, and this applies what that stored

### Options

- `--reviewed` — optional. say that you have read this report's premise_false[] and disagreements[]; without it, a report carrying either is refused and both are printed. Never defaulted: reading them is the orchestrator's act, and the tool cannot do it
- `--close` — optional. afterwards, move each covered record whose criteria are all settled to its completion status, through the ladder's intermediate rungs, and say which records were left and why
- `--except CRITERION_ID [CRITERION_ID ...]` — optional. apply every verdict this report carries except these criteria's — passed to `kyo verify --from-report` as the complement, as its own --only. The excepted verdicts stay unapplied and readable in the report; --close leaves a record that holds one of them unmoved, naming it as the reason (KYO-343)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference accept`. `<name>` stands for a value of that run.

- "unsettled" — they stay readable in the record, and nothing here settles them (KYO-331)
- "none blocks completion" — nothing here blocks its completion status any longer (KYO-137)
- "corrects this record; the guard reads the next record's table" — This makes <record_id> TRUE; it does not improve the disjointness guard, which reads the next record's table and not this one's — measured on one consumer project, where feeding drift back after every merge left four of five following declarations still insufficient (KYO-147.c4)
- "The stored scope is unchanged." — The stored scope is unchanged — derivation never overrides a declaration. (KYO-100.c3)
- "not gated; gate each yourself:" — no open `awaiting:*` gate and no running dispatch. This command wrote no gate — a gate carries an instruction to a person. Gate each yourself: (KYO-331.c4)
- "records not moved" — every covered record is left where this act found it (KYO-331.c5)

## `kyo activity`

the dispatch activity rows: every running dispatch and the newest finished ones, with record, verdict counts, worker tokens, end time and what git says about each worktree; read-only (KYO-454)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo activity [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--finished N] [--against REF]`

### Options

- `--finished N` — optional. how many finished dispatches to show beside every running one (default: 12)
- `--against REF` — optional. the branch a worktree's HEAD is asked to be contained in (default: main)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo attach`

attach an evidence artifact, stored verbatim (law 5, 9)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `evidence_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo attach [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] --file FILE [--supports ID] RECORD_ID`

### Positional arguments

- `RECORD_ID` — **required**.

### Options

- `--file FILE` — **required**. captured text from a file, or '-' for stdin
- `--supports ID` — optional. the claim, criterion, or record id this supports

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo base`

saved view definitions (kyosei/bases.yml) over records, ideas/ or docs/: list them, or run one read-only

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo base [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--group-by FIELD] {list,run} [NAME]`

### Positional arguments

- `{list,run}` — **required**.
- `[NAME]` — optional. for `run`: the saved base to run. Refused with the file's declared names if it is not one of them, never answered with an empty view

### Options

- `--group-by FIELD` — optional. for `run`: arrange the rows by FIELD instead of the base's declared `group-by`. The one binding this command overrides, because grouping rearranges rows a run already produced while source, filters and columns decide which rows exist — those stay in the reviewable file (KYO-120). Validated by the rule the stored key gets: an unknown or multi-valued field is refused naming the field set, and `applied.group-by` in --json reports the field actually used

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo brief`

the session briefing, derived from the store (M6)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Usage: `kyo brief [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--compact] [--session-start]`

### Options

- `--compact` — optional. condensed rendering for hook injection
- `--session-start` — optional. record this brief as the start of a session (what the SessionStart hook runs); anchors the handoff note every later brief in the session re-surfaces

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo briefs`

list the session briefs the store kept, newest first, or show one with the shift notes and ledger entries between it and the brief before; read-only, unlike `kyo brief` (KYO-454)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo briefs [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--latest] [BRIEF_ID]`

### Positional arguments

- `[BRIEF_ID]` — optional. one brief run's id (PREFIX-Bn): print it, the previous brief's id, and what lies between the two

### Options

- `--latest` — optional. the newest brief run, as BRIEF_ID would print it

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo changelog`

print what changed in Kyosei: one line per completed record, grouped by scope, newest first; defaults to what is newer than the build you are running; no project needed (KYO-226)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo changelog [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--since DATE-OR-COMMIT] [--all] [--output FILE]`

### Options

- `--since DATE-OR-COMMIT` — optional. list only records completed on or after this point — a YYYY-MM-DD date (UTC), a UTC timestamp such as a build's commit_time from `kyo version --json` (strictly after it), or a commit this checkout can resolve (an installed build has no checkout, so give it a date or a timestamp). Omit it and the window is the build you are running, which `kyo version` prints; every record listed is in the build printing it
- `--all` — optional. every completed record ever, ignoring the default window; refused together with --since, which asks for a different one
- `--output FILE` — optional. write the changelog to this file as markdown instead of printing it; Kyosei's own committed copy is docs/reference/changelog.md (in Kyosei's own repository), where a test fails if it falls behind the store — nothing checks yours

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo check-branch`

merge guard: fail if a branch diffs kyosei/ or ideas/ (M8)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo check-branch [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--against REF] [--subagent-stop] [--quiet] [branch]`

### Positional arguments

- `[BRANCH]` — optional. branch to check (default: the current branch)

### Options

- `--against REF` — optional. base ref to diff against (default: this repository's trunk -- the `trunk:` config key, else origin/HEAD, init.defaultBranch, main, master; `main` written here means the same)
- `--subagent-stop` — optional. read Claude Code's SubagentStop payload on stdin and check the worktree the stopping worker was dispatched into, rather than the directory the hook inherited its cwd from; how the packaged hook invokes this (KYO-128)
- `--quiet` — optional. when the check passes, print one line naming the tree checked and the result instead of the full explanation; a failing check prints in full, with the same exit code, and --json is unchanged (KYO-420)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo check-build`

has the `kyo` under this session changed since its opening brief? Compares the running build with the one this session's session-start brief recorded; read-only (KYO-398)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo check-build [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--hook]`

### Options

- `--hook` — optional. print one line only when the builds differ, and nothing otherwise, always exiting 0; how the packaged UserPromptSubmit hook invokes this

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo check-clean`

worker stop guard: fail if the worktree holds uncommitted work outside the directories `check-branch` forbids a branch to touch

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo check-clean [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--subagent-stop] [--quiet]`

### Options

- `--subagent-stop` — optional. read Claude Code's SubagentStop payload on stdin and check the worktree the stopping worker was dispatched into, rather than the directory the hook inherited its cwd from; how the packaged hook invokes this (KYO-128)
- `--quiet` — optional. when the check passes, print one line naming the tree checked and the result instead of the full explanation; a failing check prints in full, with the same exit code, and --json is unchanged (KYO-420)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo check-command`

worker command guard: refuse a command this project declares orchestrator-only (`controller_only_commands`), or one whose declared timeout is over its ceiling (`worker_command_timeout_ceiling_seconds`), inside a running dispatch's worktree (KYO-298, KYO-312)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo check-command [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--pre-tool-use] [--timeout-ms MS] [COMMAND]`

### Positional arguments

- `[COMMAND]` — optional. the command line to check, as a worker would type it (default: read it from the PreToolUse payload with --pre-tool-use)

### Options

- `--pre-tool-use` — optional. read Claude Code's PreToolUse payload on stdin and take the command, the directory it will run in and the timeout it declares from it; how the packaged hook invokes this
- `--timeout-ms MS` — optional. state by hand the timeout the Bash call declares, in milliseconds, as the PreToolUse payload states it for itself — so the ceiling check (`worker_command_timeout_ceiling_seconds`, KYO-312) is answerable by hand. Omitted means the call declares none, which is never refused

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo check-push`

publication guard: fail if pushing this repository would publish an unacknowledged Kyosei store (KYO-60)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo check-push [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--pre-tool-use] [--advisory] [--acknowledge]`

### Options

- `--pre-tool-use` — optional. read Claude Code's PreToolUse payload on stdin and check only when it describes a command that publishes; how the packaged hook invokes this
- `--advisory` — optional. warn and exit 0 instead of blocking — what the git pre-push hook runs, because a human pushing their own repository owns that decision
- `--acknowledge` — optional. accept, once and for all, that pushing publishes this store: records who and when in kyosei/config.yml, after which this command is silent

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo claim`

record a claim about a record (law 9)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `claim_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo claim [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] --level LEVEL --method TEXT --file FILE [--supersedes CLAIM_ID] RECORD_ID`

### Positional arguments

- `RECORD_ID` — **required**.

### Options

- `--level LEVEL` — **required**. asserted | derived | verified
- `--method TEXT` — **required**. one-line method or instrument
- `--file FILE` — **required**. the claim's statement from a file, or '-' for stdin (law 5)
- `--supersedes CLAIM_ID` — optional. the prior claim this one supersedes

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo close-wave`

close a merged wave: move each record to completion, run the declared generators, commit store and generated files together, then print (or, with --run-after, run) the declared after-steps — refused before any write unless every record can finish and the declared suite passed at this code (KYO-536)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Usage: `kyo close-wave [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--suite-passed COMMAND] [--dry-run] [--run-after] [RECORD_ID ...]`

### Positional arguments

- `[RECORD_ID ...]` — optional. the records the wave finished; each is walked up the status ladder by `kyo move`. A record already at completion is left where it is

### Options

- `--suite-passed COMMAND` — optional. record that this whole-suite command just passed at HEAD -- chain it on the suite's own exit status: `make test && kyo close-wave --suite-passed "make test"`. Must be a declared `close_wave: suite:` or `orchestrator_only_commands` entry; refused while anything outside kyosei/ and ideas/ is uncommitted. Kept in the checkout's git directory, never tracked
- `--dry-run` — optional. print the whole plan -- what it would refuse, move, run, commit -- and write nothing
- `--run-after` — optional. also run the `close_wave: after:` steps (reinstall, restart, prune), in order, stopping at the first that fails; without it they are printed only

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo confirm`

the maintainer looked and approves: verify the criterion met (human-observed) with their words as evidence, clear the answered human gate, and walk the record to completion (KYO-494)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo confirm [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--quote TEXT] [--quote-file FILE] [--criterion CRITERION_ID [CRITERION_ID ...]] [--gate GATE_ID] [--no-close] RECORD_ID`

### Positional arguments

- `RECORD_ID` — **required**.

### Options

- `--quote TEXT` — optional. the maintainer's words, when they fit on one line
- `--quote-file FILE` — optional. the maintainer's words from a file, or '-' for stdin; stored verbatim
- `--criterion CRITERION_ID [CRITERION_ID ...]` — optional. the criteria the words answer; overrides resolution from the gate's payload or the record's sole unmet-unobserved criterion
- `--gate GATE_ID` — optional. the awaiting:human-* gate the words answer; needed only when more than one is open
- `--no-close` — optional. record the words and clear the gate, but leave the record's status alone

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference confirm`. `<name>` stands for a value of that run.

- "only holding-for does" — clearing it reports that something happened, not that something was decided (only holding-for does — KYO-59) (KYO-59)

## `kyo dispatch`

dispatch a record with a frozen brief, or `dispatch close` a dispatch (M5)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `kyo dispatch --json` carries `dispatch_id`, `record_id`; `kyo dispatch close --json` carries `dispatch_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo dispatch [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--force] [--worktree DIR] [--branch NAME] [--no-create-worktree] [--base REF] [--model NAME] [--brief-out FILE] [--print-brief] [--task-file FILE] [--also-covers RECORD_ID [RECORD_ID ...]] [--terminated-no-report | --withdrawn] [--why TEXT] [--why-file FILE] RECORD_ID|close [DISPATCH_ID]`

### Positional arguments

- `RECORD_ID|close` — **required**. a record id to dispatch, or the literal 'close'
- `[DISPATCH_ID]` — optional. with 'close': the dispatch id to close

### Options

- `--force` — optional. dispatch over a touch-disjointness or worktree collision with a running dispatch
- `--worktree DIR` — optional. where this dispatch's work will happen (default: this project root). Recorded on the dispatch, stated in the frozen brief, and refused if another running dispatch already declares it
- `--branch NAME` — optional. the branch this dispatch's commits must land on (KYO-32; default: none declared). Recorded on the dispatch, stated in the frozen brief, and enforced by `kyo report`, which refuses a report whose commits[] are not reachable from it. Without --worktree, the dispatch's worktree is derived under this project's Kyosei home and CREATED there with `git worktree add` in the code repository (KYO-485): `-b NAME` when the branch does not exist, the branch checked out when it does
- `--no-create-worktree` — optional. with --branch: record and derive the worktree path but do not run `git worktree add`; the command is printed for you to run instead (the behaviour before KYO-485)
- `--base REF` — optional. where this dispatch's work starts (KYO-485): the commit REF names becomes the dispatch's recorded base sha and is stated in the brief, and a created worktree's new branch is cut there. For a re-dispatch deliberately cut from a predecessor's unmerged branch. Default: the code repository's HEAD
- `--model NAME` — optional. the model this dispatch's worker runs on (KYO-61), overriding the kind's policy.worker_model and the project's worker_model in kyosei/config.yml. Recorded on the dispatch and stated in the frozen brief; NOT enforced — Kyosei does not spawn workers, so nothing can make a harness honour it
- `--brief-out FILE` — optional. also write the frozen brief to this file, at the moment this invocation freezes it — not a way to re-read a brief that already exists (KYO-177): `kyo show <dispatch-id>` does that, read-only, for any dispatch already on record
- `--print-brief` — optional. print the whole frozen brief to the terminal too (the old default), in place of the length/digest line naming `kyo show <dispatch-id>` (KYO-326); --brief-out and --json are unaffected either way
- `--task-file FILE` — optional. orchestrator-written task-specific prose for the brief, from a file or '-'
- `--also-covers RECORD_ID [RECORD_ID ...]` — optional. additional record id(s) this dispatch formally covers (KYO-12 fix): their criteria are appended to the frozen brief, and `kyo report` will accept criteria_verdicts naming any of them, not just the primary record. May be repeated: `--also-covers A --also-covers B` is `--also-covers A B`, and an id named twice is covered once
- `--terminated-no-report` — optional. with 'close': mark closure with no ingested report because the work STOPPED — a worker was spawned and died, crashed or was cut off (law 8). Requires --why, and stays loud in `kyo brief` until the record is answered
- `--withdrawn` — optional. with 'close': mark closure with no ingested report because the dispatch was never handed to a worker at all — a brief frozen by mistake, which a frozen brief cannot have amended out of it (KYO-345). Requires --why. Not a death: it is listed nowhere in `kyo brief`, and `kyo show <record-id>` prints it in the record's dispatch history under its own word
- `--why TEXT` — optional. one-line reason for --terminated-no-report or --withdrawn
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference dispatch`. `<name>` stands for a value of that run.

- "`worktree_setup`, printed, not run" — declared by this project as `worktree_setup`; printed, not run (KYO-143)
- "not enforced" — recorded and stated in the brief, never enforced — Kyosei does not spawn workers (KYO-61)
- "disjointness: computed from DECLARED touches" — disjointness was computed against DECLARED touches — a prediction on both sides, written before the work, and reported drift says predictions are routinely wrong (KYO-147.c1)
- "never checked against work" — never checked against work — it has not reported, and no earlier dispatch on this claim has either, so nothing has ever compared this table to a diff (KYO-147.c1)
- "file(s) historically dragged in and not declared" — file(s) that this record's declared files have historically dragged in and this record does not declare (from <reports> reported dispatch(es); reported, never refused) (KYO-147.c2)
- "or --print-brief" — pass --print-brief to print it here instead (KYO-326)

## `kyo docs`

Layer 3 docs-SOT skeleton validator (M8)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `kyo docs settle --json` carries `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo docs [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--record ID] [--all] [--verdict VERDICT] [--method TEXT] [--against REF] [--branch REF] [--suppressed] [--from-report DISPATCH_ID] [--check] [--dry-run] [--only OBLIGATION_ID [OBLIGATION_ID ...]] [--why TEXT] [--why-file FILE] {assemble,check,index,obligations,preview,settle,suggest} [ID ...]`

### Positional arguments

- `{assemble,check,index,obligations,preview,settle,suggest}` — **required**. check: every governs: target must exist. obligations: the document sections this project's dispatches put a question to. preview: the sections THIS worktree's branch would raise, read-only and store-free, for a worker to read before writing docs_verdicts[] (KYO-199). settle: record the verdict that clears one (KYO-137). assemble: append each dispatch's doc fragment to its numbered document with the next number and name the project's follow-up commands — the orchestrator's act on the merged tree (KYO-299). suggest: draft the declarations this repository looks like it wants — a numbered: block for a document whose headings are numbered, an ssot: block for documents whose headings carry requirement ids (KYO-556), an exclusion for a generated file inside a set's globs — each with its evidence and the exact YAML to paste, written nowhere and enforced by nothing (KYO-303). index: write the generated section index of each document set declaring `section_status.index` in documents.yml — per document its section counts by status and code pointers, its non-current sections with their links and the head of each chain, and the sections no code points at (KYO-502)
- `[ID ...]` — optional. settle: the docs obligation(s) to record a verdict against. Naming more than one settles them in a single act carrying one verdict, one method and one reason — for the case where several sections are one editorial act (KYO-159). Every member is named explicitly and nothing is derived from a document, a governed file or a dispatch: `kyo docs obligations` prints the line to copy. The one set the tool forms is the caller's own naming of a record: `--record <id> --all` (KYO-528)

### Options

- `--record ID` — optional. obligations: only this record's. settle --all: the record whose open obligations are settled
- `--all` — optional. obligations: include the ones already settled, not just the open ones. settle: with --record <id>, every open obligation of that record as one act (KYO-528), in place of naming each id
- `--verdict VERDICT` — optional. settle: updated | not-affected — whether the document was changed to match the work, or read and found to need no change. ONE verdict for the whole set: 'I changed all of these' and 'I read all of these and none needed changing' are each one defensible statement, and some-of-each is not, so there is deliberately no way to spell a per-member verdict
- `--method TEXT` — optional. settle: how the verdict was reached (law 9). Required: a verdict without its instrument is a claim whose weight cannot be judged later
- `--against REF` — optional. preview: the base to diff this branch against (default: main) — whatever your worktree was branched from
- `--branch REF` — optional. preview: the branch to read (default: HEAD, the one checked out here)
- `--suppressed` — optional. preview: list the sections a declared `#symbol` stopped from raising on this diff — what a whole-file target would have raised and this one did not (KYO-352), and the code pointers raised at whole-file granularity because their file has no region finder (KYO-500). The count is printed either way, and --json always carries the list; this is how a project that has just narrowed many targets audits what went quiet
- `--from-report DISPATCH_ID` — optional. settle: apply that dispatch's current report's own docs_verdicts[] — each entry's verdict, method and reason read from the report rather than retyped (KYO-199.c4); takes no ID, --verdict, --method or --why of its own
- `--check` — optional. index: write nothing, and exit 1 naming each index whose committed file differs from what `kyo docs index` would write now
- `--dry-run` — optional. assemble: print exactly the plan a real run would apply — which fragment becomes which number, and the follow-up commands — and write nothing
- `--only OBLIGATION_ID [OBLIGATION_ID ...]` — optional. with --from-report: apply only these obligations' verdicts and leave the rest of the report's unapplied
- `--why TEXT` — optional. one-line reason settle: why, in the orchestrator's own words — required when settling more than one obligation at once, where it is the evidence the review covered every member
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference docs`. `<name>` stands for a value of that run.

- "narrows to a symbol's own lines, for `.py`, `.md`, `.ts`, `.tsx`, `.js`, `.mjs`, `.json`, `.yml`, `.yaml`, `.css` targets only" — raises a section only when the diff touches that symbol's own lines, where the section is about a symbol rather than the file. That narrowing holds for `.py`, `.md`, `.ts`, `.tsx`, `.js`, `.mjs`, `.json`, `.yml`, `.yaml`, `.css` targets and is offered for no others: a `#symbol` on any other suffix is accepted and still raises like the whole file (KYO-199.c2, KYO-351)
- "none blocks completion" — nothing here blocks its completion status any longer (KYO-137)

## `kyo doctor`

run the tool's own invariant checks

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo doctor [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--hook]`

### Options

- `--hook` — optional. print ONE line to stderr instead of the whole report, and write the report to doctor-report.txt beside kyosei.db; how the packaged Stop / SessionEnd hook invokes this (KYO-362). The same checks run and the exit status is unchanged — silent, and the report file removed, when nothing fails

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo edit`

change a record

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo edit [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--title TEXT] [--description-file FILE] [--note-file FILE] [--supersede-note NOTE_ID] [--add-criteria-file FILE] [--touches-file FILE] [--touches-from-drift DISPATCH_ID] [--doc-refs-file FILE] [--criterion CRITERION_ID] [--state STATE] [--text-file FILE] [--plan ID] [--no-plan] [--scope SCOPE] [--no-scope] [--since-note NOTE-ID] [--kind KIND] [--why TEXT] [--why-file FILE] id`

### Positional arguments

- `ID` — **required**.

### Options

- `--title TEXT` — optional. replace the one-line title
- `--description-file FILE` — optional. replace the description from a file or '-'
- `--note-file FILE` — optional. append a note from a file or '-'
- `--supersede-note NOTE_ID` — optional. mark this note superseded by the note being appended
- `--add-criteria-file FILE` — optional. add criteria, one per line, from a file or '-'
- `--touches-file FILE` — optional. REPLACE this record's touches with the file's lines ('path', 'path#symbol', 'path#+' for additions to an existing file and nothing already in it, 'dir/' for a tree this work creates or owns, or '+path' for one file it creates), validated against the repo now; an empty file clears them. The prior list stays readable in the events ledger
- `--touches-from-drift DISPATCH_ID` — optional. REPLACE this record's touches with the correction `kyo touch-drift DISPATCH_ID` computed — the declared paths plus the files the branch actually changed (KYO-147.c4). It makes the RECORD true; it does NOT improve the disjointness guard, which reads the NEXT record's table and not this one's
- `--doc-refs-file FILE` — optional. REPLACE this record's doc-refs -- the documents its work affects -- with the file's lines ('path' or 'path#section'), validated against the repo now; an empty file clears them. The back path to a document's `governs:`, for a document about this record's subject that names none of its files (KYO-137)
- `--criterion CRITERION_ID` — optional. criterion to void (with --state and --why) or correct (with --text-file)
- `--state STATE` — optional. criterion state: met, unmet-unbuilt, unmet-unobserved, void-premise-gone, void-withdrawn
- `--text-file FILE` — optional. correct --criterion's wording from a file or '-'. Allowed only while nothing has attested to it (no verification recorded, still unmet-unbuilt); once a verdict exists the refusal points at `kyo restate`, which keeps the old text and its verdicts intact
- `--plan ID` — optional. join a plan record
- `--no-plan` — optional. leave the current plan
- `--scope SCOPE` — optional. set the part of the system this work belongs to, from kyosei/scopes.yml
- `--no-scope` — optional. clear this record's declared scope
- `--since-note NOTE-ID` — optional. a carried kind only: the shift note (PREFIX-Sn) that first recorded this item, no later than the record's filing; its age counts from that note's time (KYO-498)
- `--kind KIND` — optional. change this record's kind to another kind in kyosei/kinds.yml with the same archetype (work or knowledge) -- the remedy for a record whose kind kinds.yml no longer defines (KYO-509)
- `--why TEXT` — optional. one-line reason for a criterion state that requires one
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference edit`. `<name>` stands for a value of that run.

- "corrects this record; the guard reads the next record's table" — This makes <record_id> TRUE; it does not improve the disjointness guard, which reads the next record's table and not this one's — measured on one consumer project, where feeding drift back after every merge left four of five following declarations still insufficient (KYO-147.c4)
- "The stored scope is unchanged." — The stored scope is unchanged — derivation never overrides a declaration. (KYO-100.c3)

## `kyo export`

rewrite drifted export documents from the db (KYO-6)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo export [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--refresh] [--force] [--migrate-events]`

### Options

- `--refresh` — optional. rewrite every record's export document from the database, repairing drift a format-version bump left behind; required (kyo export alone is a usage error)
- `--force` — optional. refresh even where kyosei/export/ is ahead of the database, overwriting the newer side with the older one (KYO-81); the mirror of kyo rebuild --force
- `--migrate-events` — optional. rewrite a monolithic kyosei/export/_events.jsonl as bounded shards under kyosei/export/_events/ and remove the old file, in one run (KYO-268); every line moves verbatim, and an export already sharded is a no-op that says so

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo flag`

list, acknowledge/resolve a sweep flag (M6)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `kyo flag resolve --json` carries `flag_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo flag [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--apply] [--all] [--settled] [--why TEXT] [--why-file FILE] {list,resolve,collapse} [FLAG_ID] [FLAG_ID ...]`

### Positional arguments

- `{list,resolve,collapse}` — **required**. list: every open flag, one line each (--all includes resolved ones). resolve: acknowledge and clear one flag, or with --settled, every open staleness flag under a record already at a completion or retired status (KYO-346; dry run without --apply). collapse: merge each record's duplicate touch-changed flags into its earliest one and close the rest as superseded (KYO-69; dry run without --apply)
- `[FLAG_ID]` — optional.
- `[FLAG_ID ...]` — optional. resolve only: further flags to resolve in the same act, with the one --why; an unknown or already-resolved id refuses the whole call before anything is written

### Options

- `--apply` — optional. collapse/resolve --settled only: actually write it (default is a dry run)
- `--all` — optional. list only: include flags that have already been resolved
- `--settled` — optional. resolve only: every open staleness flag under a record already at a completion or retired status, as one act (KYO-346; dry run without --apply, requires --why)
- `--why TEXT` — optional. one-line reason for resolving it (required with --settled)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo gate`

add a gate; orthogonal to status

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `gate_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo gate [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--do TEXT] [--do-file FILE] [--tag TAG] id GATE`

### Positional arguments

- `ID` — **required**.
- `GATE` — **required**. awaiting:event | awaiting:human-run | awaiting:human-review | holding-for

### Options

- `--do TEXT` — optional. the exact condition, or the exact thing to run, when it fits on one line
- `--do-file FILE` — optional. read the payload from a file, or '-' for stdin — for a procedure with a command and several links that cannot be written on one line
- `--tag TAG` — optional. classify what this gate is waiting on, from kyosei/gate-tags.yml; repeat for several. Refused if the registry does not declare it, and beside --do rather than instead of it: the tag is what makes the gate findable (`kyo list --gate-tag`), the payload is what makes it clearable

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference gate`. `<name>` stands for a value of that run.

- "orthogonal" — gates are orthogonal to status (milestone 1; charter, "Status and gates are orthogonal dimensions")

## `kyo gates`

list every open gate across every record, with the record it is on, and each record's open-gate count; read-only (KYO-454)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo gates [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--type TYPE]`

### Options

- `--type TYPE` — optional. only gates of this type (repeatable): awaiting:event, awaiting:human-run, awaiting:human-review, holding-for

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo harness`

install Kyosei into an agent harness: `install claude-code` links $KYOSEI_HOME/harness/claude-code to this build's packaged plugin, registers it with `claude plugin marketplace add`, and inside a Kyosei project enables it there at local scope (KYO-594)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo harness [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] {install,migrate} [{claude-code}]`

### Positional arguments

- `{install,migrate}` — **required**. `install` the harness integration; `migrate` a project from the copies `kyo install-skill` and `kyo install-hooks` wrote to the plugin: it enables the plugin here and removes the unedited copies, naming what it leaves (KYO-595)
- `[{claude-code}]` — optional. the harness to install into (`migrate` assumes claude-code, the only one)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo ideas`

the ideas layer (never enforced)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo ideas [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--apply] {review,migrate-stamps}`

### Positional arguments

- `{review,migrate-stamps}` — **required**. review: unpromoted ideas by age + broken mentions; migrate-stamps: move bottom-of-file promotion stamps into top-of-file YAML frontmatter

### Options

- `--apply` — optional. migrate-stamps: actually rewrite the files. Without it nothing under ideas/ is written — this is the only command that edits the human's own directory, so it shows the list first

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo init`

scaffold ideas/ and kyosei/ in a project; in a new project, setup starts at `kyo onboard`

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo init [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--prefix PREFIX] [--defaults] [--explain] [--in-repo] [--store DIR] [--adopt] [--no-git-init] [directory]`

### Positional arguments

- `[DIRECTORY]` — optional. project root (default: here)

### Options

- `--prefix PREFIX` — optional. project id prefix, e.g. KYO (default: from dir name)
- `--defaults` — optional. take the default for every choice no other flag makes: the store in its own git repository at ~/kyosei/<project>/ and the prefix from the directory name. What a script or CI passes; `kyo onboard` is the guided route (KYO-558)
- `--explain` — optional. write nothing, and print what the store layout means here: where the store lives, which prose stays out of (or goes into) the code repository, and where dispatch worktrees go. For the project's own layout when it has a store, else for the layout the other flags choose, or both layouts when none is chosen
- `--in-repo` — optional. put the store INSIDE the code repository (kyosei/ and ideas/ committed alongside the code) instead of the default, which is a store of its own at ~/kyosei/<project>/ in its own git repository. The right choice when records are not sensitive and you want them reviewed in the same pull request as the code; it also means ideas/, record descriptions, shift notes, evidence stored verbatim and frozen briefs all become committed prose in this repository
- `--store DIR` — optional. put the split store at this path instead of ~/kyosei/<project>/ ($KYOSEI_HOME overrides the ~/kyosei part)
- `--adopt` — optional. link the existing store at the chosen path even when kyo init cannot confirm it belongs to this code repository: its recorded code repository (root commit) differs, or cannot be compared. Without it, such a store is refused, so a repository never silently adopts another project's store that merely shares its directory name
- `--no-git-init` — optional. in a directory that is in no git repository, do NOT run `git init` and commit the files kyo init wrote there; the store then records no code-repository identity until you make the repository and commit, after which the next `kyo doctor` or session-start brief records it

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo install-agents`

write the worker agent types the operator chose into .claude/agents/, from the packaged templates, with the operator's model and effort (setup decision C4, KYO-607)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo install-agents [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--type TEMPLATE=MODEL[:EFFORT]] [--force] [--print]`

### Options

- `--type TEMPLATE=MODEL[:EFFORT]` — optional. one agent type to write (repeatable): TEMPLATE is worker (build tasks whose design is settled; effort medium unless named) or designer (proposals, measurements, judgement calls; effort high unless named), written as .claude/agents/kyosei-TEMPLATE.md; EFFORT is one of low, medium, high, xhigh, max
- `--force` — optional. replace an existing file that differs from what would be written
- `--print` — optional. show what would be written without writing it

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo install-hooks`

merge Claude Code hook templates into .claude/settings.json (M8)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo install-hooks [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--print] [--copies]`

### Options

- `--print` — optional. show what would be written without writing it
- `--copies` — optional. write the tracked copies in a project not already on them; without it, a project with neither copy or on the plugin is told to use `kyo harness install claude-code`, the default (KYO-595)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo install-procedures`

add the standing rules this build ships that kyosei/procedures.md does not have yet, leaving every rule already there untouched unless --adopt names it (KYO-212, KYO-151.c2)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo install-procedures [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--print] [--adopt RULE_TITLE] [--adopt-preamble]`

### Options

- `--print` — optional. show which rules would be added without writing them
- `--adopt RULE_TITLE` — optional. rewrite this rule's text in the store copy from the packaged default (repeatable); refuses a rule the packaged default does not ship or one already identical (KYO-151.c2)
- `--adopt-preamble` — optional. rewrite the preamble (everything above the first rule) from the packaged default the same way; refuses if the two already agree

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo install-skill`

write the packaged /kyosei orchestrator workflow, and the reference beside it, into .claude/skills/ (KYO-328)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo install-skill [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--print] [--copies]`

### Options

- `--print` — optional. show what would be written without writing it
- `--copies` — optional. write the tracked copies in a project not already on them; without it, a project with neither copy or on the plugin is told to use `kyo harness install claude-code`, the default (KYO-595)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo link`

create a typed edge; cycles refused

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `link_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo link [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] FROM_ID EDGE TO_ID`

### Positional arguments

- `FROM_ID` — **required**.
- `EDGE` — **required**. blocks | duplicates | supersedes | same-question-as | needs-same-change-as | produced-by
- `TO_ID` — **required**.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo list`

list records, filtered and summarised

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo list [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--kind KIND] [--scope SCOPE] [--gate-tag TAG] [--status STATUS] [--plan ID] [--unsettled] [--limit N]`

### Options

- `--kind KIND` — optional. only records of this kind
- `--scope SCOPE` — optional. only records declaring this scope; refused if kyosei/scopes.yml does not declare it, rather than answered with an empty listing
- `--gate-tag TAG` — optional. only records carrying an OPEN gate tagged this, from kyosei/gate-tags.yml — a query such as 'everything waiting on a human to review a UI element'. Refused if the registry does not declare the tag, rather than answered with an empty listing. Orthogonal to --status: a record can be working and gated
- `--status STATUS` — optional. a status declared in kyosei/statuses.yml (default ladder: open | working | built | verified | done | wont-do), or current | void for knowledge-archetype records. Repeat it to ask for any of several (`--status open --status working`)
- `--plan ID` — optional. only records belonging to this plan — membership is stored on the member, so this is the only way to walk the relation the other way
- `--unsettled` — optional. only records not yet settled: leave out every record whose stored status has the `complete` or `retired` role in kyosei/statuses.yml. Combines with --status (both must hold)
- `--limit N` — optional. print at most N rows, in the listing's usual order; the summary line says how many matched and that the listing was limited

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo map`

render the kind registry as a graph

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo map [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty]`

This command takes no arguments of its own.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo move`

change status; contracts are enforced here

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo move [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--why TEXT] [--why-file FILE] [--force] id STATUS`

### Positional arguments

- `ID` — **required**.
- `STATUS` — **required**. a status declared in kyosei/statuses.yml; the default ladder is open | working | built | verified | done | wont-do

### Options

- `--why TEXT` — optional. one-line reason (required to enter wont-do)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin
- `--force` — optional. close a plan over unsettled (not complete or retired) members, recording that it was closed over them; or enter a completion or retired status with a gate still open, leaving it open (KYO-490)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo new`

create a record

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`, `evidence_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo new [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--title TEXT] [--plan ID] [--scope SCOPE] [--since-note NOTE-ID] [--description-file FILE] [--criteria-file FILE] [--touches-file FILE] [--evidence-file FILE] [--from FILE] [--check] [--force] [--no-prior-art] kind`

### Positional arguments

- `KIND` — **required**. record kind, validated against kyosei/kinds.yml

### Options

- `--title TEXT` — optional. one-line title Optional exactly when --from supplies the title instead (KYO-330).
- `--plan ID` — optional. plan record this belongs to
- `--scope SCOPE` — optional. the part of the system this work belongs to, from kyosei/scopes.yml; refused if the registry does not declare it
- `--since-note NOTE-ID` — optional. a carried kind only: the shift note (PREFIX-Sn) that first recorded this item, so its age counts from that note's time; refused for an unknown note or a non-carried kind (KYO-498)
- `--description-file FILE` — optional. description from a file, or '-' for stdin (law 5: prose never as an argument)
- `--criteria-file FILE` — optional. one criterion per line, from a file or '-'; an empty file means zero criteria
- `--touches-file FILE` — optional. one touch per line ('path', 'path#symbol', 'path#+' for additions to an existing file and nothing already in it, 'dir/' for a tree this work creates or owns, or '+path' for one file it creates), relative to the project root, validated against the repo now; optional here (unlike `kyo promote`) because this is the zero-ceremony intake path
- `--evidence-file FILE` — optional. evidence stored with the record in the same act, verbatim with its sha256 as `kyo attach` stores it, from a file or '-'; required by a kind whose policy sets requires_evidence_links (the default `decision`: the operator's words -- who, when, where, then the words verbatim) and allowed beside --from (KYO-521)
- `--from FILE` — optional. file the whole record from one markdown file instead of --title/--plan/--scope/--description-file/--criteria-file/--touches-file: front matter for title/scope/plan/touches, a '## Criteria' section (one criterion per list item), everything else the description; refused together with any of those flags (KYO-330)
- `--check` — optional. with --from, validate the file (kind, scope, touches against the repo) and file nothing (KYO-330)
- `--force` — optional. file the record although one with an identical title and criteria was filed in this project within the last hour (KYO-538)
- `--no-prior-art` — optional. skip the prior-art search kyosei/config.yml's recx.prior_art would run for this record (KYO-585); `kyo prior-art <id>` runs it later

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference new`. `<name>` stands for a value of that run.

- "The stored scope is unchanged." — The stored scope is unchanged — derivation never overrides a declaration. (KYO-100.c3)

## `kyo next`

ready work: dispatchable, ungated, disjoint from running dispatches (M6)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo next [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty]`

This command takes no arguments of its own.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo note`

append to the continuously-written shift log (M6); for a note ON a record see `kyo edit --note-file`

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `shift_note_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo note [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--shift] [--file FILE]`

### Options

- `--shift` — optional. the only note kind v1 ships
- `--file FILE` — optional. the shift note's text from a file, or '-' for stdin (law 5)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo onboard`

guided project setup, where a new project starts: prints this build's setup steps for an agent to follow in a new or existing project, or with --check reads a project back against the same checklist and writes nothing (KYO-514)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo onboard [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--check] [--all]`

### Options

- `--check` — optional. report every readback item of the project here (or --root) as present, missing or behind this build, each with the command that fixes it; reads files and git only, never opens the database, writes nothing
- `--all` — optional. with --check: check every project in $KYOSEI_HOME/projects.json, one block per project (implies --check)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo orchestrator`

start, stop, attach to or list detached claude Remote Control sessions, one per project, on this machine's `tmux -L kyosei` server -- safe to run over SSH; --install the LaunchAgent that keeps that server running from login (KYO-458, KYO-474) (also accepted as `kyo controller`)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo orchestrator [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--install] [--uninstall] [--apply] [{start,stop,attach,list}] [PROJECT]`

### Positional arguments

- `[{start,stop,attach,list}]` — optional. start PROJECT's session (orchestrator_command from $KYOSEI_HOME/settings.yml, default claude, through a login shell, in the project directory, detached), stop it, attach this terminal to it (`tmux -L kyosei attach -t =PREFIX`; over SSH, `ssh -t HOST "zsh -lc 'kyo orchestrator attach PREFIX'"`), or list every registered project's session, whether claude is alive in it and the command it was started with
- `[PROJECT]` — optional. for start, stop and attach: a project in the machine's registry, by name or prefix, or the path of a project directory

### Options

- `--install` — optional. print the per-user LaunchAgent (gui/<uid>) that keeps `tmux -L kyosei` running from login; with --apply, write it and load it
- `--uninstall` — optional. print what removing that LaunchAgent does; with --apply, boot it out (ending its server and every session in it) and remove it
- `--apply` — optional. with --install or --uninstall: do it rather than print it

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo pre-tool-use`

both PreToolUse guards (`kyo check-push --pre-tool-use` and `kyo check-command --pre-tool-use`) against ONE read of the payload on stdin, in one interpreter: exits 2 if either refuses, with both refusals when both do, and is silent otherwise. Opt-in: `kyo install-hooks` still wires the two commands (KYO-422)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo pre-tool-use [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty]`

This command takes no arguments of its own.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo prior-art`

search recx's index for existing work matching a record's title and description, and store the results as evidence under a derived claim, superseding the record's previous one; needs the extra kyosei[recx] (KYO-585)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo prior-art [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] RECORD_ID`

### Positional arguments

- `RECORD_ID` — **required**.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo prior-art-setting`

turn this project's prior-art search on or off: writes recx.prior_art.state in kyosei/config.yml, keeping its trigger, limit and index_url; `on` needs the extra kyosei[recx] (KYO-586)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo prior-art-setting [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] {on,off}`

### Positional arguments

- `{on,off}` — **required**. on: search recx when a record the trigger names is created (by default a proposal, by kyo new, promote or spawn); off: record that this project declined

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo projects`

list, add or remove the projects in this machine's registry ($KYOSEI_HOME/projects.json), which kyo serve and kyo orchestrator answer for; the registry's one writer (KYO-455)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo projects [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] {list,add,remove} [PATH|NAME]`

### Positional arguments

- `{list,add,remove}` — **required**. list every registered project with the name kyo serve uses for it, add the initialised project at PATH, or remove one entry by name or root path
- `[PATH|NAME]` — optional. for add: the project's directory; for remove: its registered name or root

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo promote`

promote an ideas/ file into a record; the ceremony boundary (law 13)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo promote [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--section HEADING] --kind KIND [--title TEXT] [--criteria-file FILE] [--touches-file FILE] [--scope SCOPE] [--keep] [--from FILE] [--check] [--force] [--no-prior-art] IDEA_FILE`

### Positional arguments

- `IDEA_FILE` — **required**. path to a file under ideas/

### Options

- `--section HEADING` — optional. promote one section of a multi-idea file instead of the whole file, named by its markdown heading exactly as the file writes it; the rest of the file survives
- `--kind KIND` — **required**. record kind; its accepts_from must include idea
- `--title TEXT` — optional. one-line title (default: the idea's first '# heading')
- `--criteria-file FILE` — optional. one criterion per line, from a file or '-'; required when the kind's policy.requires_criteria is true
- `--touches-file FILE` — optional. one touch per line ('path', 'path#symbol', 'path#+' for additions to an existing file and nothing already in it, 'dir/' for a tree this work creates or owns, or '+path' for one file it creates), relative to the project root; required for archetype: work kinds
- `--scope SCOPE` — optional. the part of the system this work belongs to, from kyosei/scopes.yml. Law 13's third piece of promotion-time structure, beside criteria and touches
- `--keep` — optional. keep the idea file (or, with --section, the promoted section) instead of removing it -- the pre-KYO-228 behaviour: a stub is left in its place, pointing at the record
- `--from FILE` — optional. take title/scope/plan/criteria/touches from one markdown file instead of --title/--scope/--criteria-file/--touches-file -- the same file shape `kyo new --from` accepts; front matter for title/scope/plan/touches, a '## Criteria' section (one criterion per list item); everything else is the description, but only when it differs (whitespace aside) from the idea being promoted, which remains the description otherwise; refused together with any of --title/--scope/--criteria-file/--touches-file (KYO-330/KYO-340)
- `--check` — optional. with --from, validate the file (kind, scope, touches against the repo) and file nothing (KYO-330)
- `--force` — optional. file the record although one with an identical title and criteria was filed in this project within the last hour (KYO-538)
- `--no-prior-art` — optional. skip the prior-art search kyosei/config.yml's recx.prior_art would run for this record (KYO-585); `kyo prior-art <id>` runs it later

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference promote`. `<name>` stands for a value of that run.

- "The stored scope is unchanged." — The stored scope is unchanged — derivation never overrides a declaration. (KYO-100.c3)

## `kyo rebuild`

reconstruct kyosei.db from kyosei/export/

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo rebuild [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--force]`

### Options

- `--force` — optional. overwrite an existing kyosei.db

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo redact`

remove verbatim prose (evidence, claim, note, shift note, or a record's own title, description or criterion) from the store and the git-canonical ledgers, recording that it happened (KYO-426)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `target_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo redact [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--why TEXT] [--why-file FILE] ID`

### Positional arguments

- `ID` — **required**. an evidence artifact (RECORD.eN, written by `kyo attach`), claim (RECORD.mN, by `kyo claim`), note (RECORD.nN, by `kyo edit --note-file`), or shift note (PREFIX-SN, by `kyo note --shift`); or a record's own title (RECORD.title), description (RECORD.description) or criterion (RECORD.cN), every version of it up to now (KYO-426)

### Options

- `--why TEXT` — optional. one-line reason for redacting it (required)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo redo`

replace the --do payload on an existing gate, by its own id; not a clearing (KYO-148)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `gate_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo redo [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] (--do TEXT | --do-file FILE) GATE_ID`

### Positional arguments

- `GATE_ID` — **required**.

### Options

- `--do TEXT` — optional. the corrected payload, when it fits on one line. REPLACES the gate's payload rather than appending to it, and both texts are echoed
- `--do-file FILE` — optional. read the corrected payload from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo reference`

print the command-and-option reference — all of it, or one command's section — generated from this parser; no project needed (KYO-138, KYO-221)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo reference [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--output FILE] [COMMAND]`

### Positional arguments

- `[COMMAND]` — optional. print only this command's section of the reference; omit it for the whole document. An unknown name is refused with the list of known ones

### Options

- `--output FILE` — optional. write the reference to this file instead of stdout; Kyosei's own committed copy is docs/reference/cli.md (in Kyosei's own repository), where a test fails if it falls behind the parser — nothing checks yours (KYO-156)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo report`

ingest a dispatch's structured report (M5)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `dispatch_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo report [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] --file FILE [--check] DISPATCH_ID`

### Positional arguments

- `DISPATCH_ID` — **required**.

### Options

- `--file FILE` — **required**. the report JSON, validated against the schema `kyo report-schema` prints
- `--check` — optional. run every check the ingest runs before writing, print any problem, write nothing; exits non-zero when the report would be refused (KYO-477)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference report`. `<name>` stands for a value of that run.

- "verdicts above are NOT applied; apply them via" — note: verdicts above are NOT applied to any criterion; applying them is a orchestrator act via (KYO-7.c4)
- "each needs a verdict before completion" — this record cannot reach its completion status until each carries a verdict, and a document counts as updated when you say so, not when its timestamp moves (KYO-137.c2)
- "narrows to a symbol's own lines, for `.py`, `.md`, `.ts`, `.tsx`, `.js`, `.mjs`, `.json`, `.yml`, `.yaml`, `.css` targets only" — raises a section only when the diff touches that symbol's own lines, where the section is about a symbol rather than the file. That narrowing holds for `.py`, `.md`, `.ts`, `.tsx`, `.js`, `.mjs`, `.json`, `.yml`, `.yaml`, `.css` targets and is offered for no others: a `#symbol` on any other suffix is accepted and still raises like the whole file (KYO-199.c2, KYO-351)
- "the diff of <branch>" — read from the diff of <branch> (`git diff <against>...<branch>`) (KYO-211.c1)
- "reported, not refused" — This is REPORTED, not refused — a touches table is a prediction and is routinely, defensibly wrong. What it costs is a later dispatch colliding with a file nothing knew was claimed. (KYO-71)

## `kyo report-schema`

print the report schema `kyo report` validates against — from the installed package, no project needed (KYO-149)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo report-schema [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty]`

This command takes no arguments of its own.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo restate`

void a criterion and add its replacement atomically, back-linked (KYO-58)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`, `criterion_id`, `replacement_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo restate [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] --text-file FILE CRITERION_ID`

### Positional arguments

- `CRITERION_ID` — **required**.

### Options

- `--text-file FILE` — **required**. the replacement wording, from a file or '-'. The old criterion becomes void-withdrawn and reads as 'superseded by' the new one everywhere; its verdict history stays attached to the text it was recorded against, and the replacement starts unverified

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo retag`

replace the tags on an existing gate, by its own id; not a clearing (KYO-130)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `gate_id`, `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo retag [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] (--tag TAG | --no-tags) GATE_ID`

### Positional arguments

- `GATE_ID` — **required**.

### Options

- `--tag TAG` — optional. the tags the gate should carry from now on, from kyosei/gate-tags.yml; repeat for several. REPLACES the gate's tags rather than adding to them, and both sets are echoed. Refused if the registry does not declare one, in the same words `kyo gate` refuses it
- `--no-tags` — optional. leave the gate carrying no tags at all

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo rules`

print the standing rules in full — one by title, or the list; the text a dispatch brief carries one line of (KYO-327); `kyo rules add` writes a project rule into kyosei/procedures.md (KYO-565)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo rules [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--title TEXT] [--audience TOKEN[,TOKEN...]] [--enforced-by TEXT] [--reason-file FILE] [TITLE]`

### Positional arguments

- `[TITLE]` — optional. print the rule whose title contains this text, in full; omit to list every rule with its audience. The word `add`, exactly, is `kyo rules add` (KYO-565): to print a rule whose title contains 'add', spell it any other way, e.g. `kyo rules Add`

### Options

- `--title TEXT` — optional. with `add`: the new rule's short imperative title
- `--audience TOKEN[,TOKEN...]` — optional. with `add`: who the rule reaches -- worker, orchestrator, human (controller is orchestrator's earlier name), comma-separated
- `--enforced-by TEXT` — optional. with `add`: one line naming what holds the rule up, or `aspirational`
- `--reason-file FILE` — optional. with `add`: the rule's reasoning, one to three sentences, from a file or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo serve`

serve this machine's projects to any viewer over loopback HTTP: the read commands' own --json, each store opened read-only (KYO-453)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo serve [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--port N] [--bind ADDR] [--allow-host NAME] [--allow-origin ORIGIN] [--install] [--uninstall] [--status] [--restart] [--apply]`

### Options

- `--port N` — optional. the port to listen on (default 47370; 0 picks a free one)
- `--bind ADDR` — optional. the loopback address to listen on (default 127.0.0.1, or ::1). Any other address is refused, with no override: the server has no password and no TLS, and another machine reaches it through Tailscale Serve
- `--allow-host NAME` — optional. also answer requests whose Host is NAME (repeatable), such as the <machine>.<tailnet>.ts.net name Tailscale Serve forwards; every other non-loopback Host is refused as possible DNS rebinding
- `--allow-origin ORIGIN` — optional. answer a browser page from ORIGIN (repeatable, e.g. http://localhost:5173); a request carrying any other Origin is refused
- `--install` — optional. print the per-user agent that runs this kyo serve from login (launchd dev.kyosei.serve on macOS, the systemd user unit kyosei-serve.service on Linux), with this command's --port, --bind, --allow-host and --allow-origin; with --apply, write it and start it (KYO-457)
- `--uninstall` — optional. print what removing that agent does; with --apply, stop it and remove it
- `--status` — optional. report the installed agent, what answers on the port, and whether that server is stale (started as a build no longer installed)
- `--restart` — optional. restart the installed agent (launchctl kickstart -k, systemctl --user restart)
- `--apply` — optional. with --install or --uninstall: do it rather than print it

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo sessions`

the Claude Code sessions this project has, and each one's claude.ai Remote Control page (KYO-186)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo sessions [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--hook]`

### Options

- `--hook` — optional. read Claude Code's SessionStart / Stop / SessionEnd payload on stdin and record this session in the store's local sessions.json; how the packaged hook invokes this. Silent on success, and always exit 0 — a session that could not be recorded must still start, turn and end

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo shift-notes`

list the shift log newest first, each note marked with the session-start brief it handed off to; read-only (KYO-454)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo shift-notes [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--latest]`

### Options

- `--latest` — optional. only the newest shift note, including one written after the last brief

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo show`

print a record, a criterion, an evidence artifact, a flag, a dispatch or a shift note, by the id that names it

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo show [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--expand-obligations] [--fields FIELDS] id`

### Positional arguments

- `ID` — **required**. the id of the object to print — PREFIX-N — a record; PREFIX-N.cN — a criterion, with its verdict history; PREFIX-N.eN — an evidence artifact; PREFIX-N.fN — a sweep flag; PREFIX-N.dN — a dispatch, printing its frozen brief back; PREFIX-N.rN — a stored report, its amendment chain and its body; PREFIX-SN — a shift note

### Options

- `--expand-obligations` — optional. list every docs obligation in full instead of collapsing the ones that share a settlement and a detail; --json carries them all either way
- `--fields FIELDS` — optional. with --json: print only these top-level keys of the document (comma-separated, e.g. status,title), plus the envelope's ok and command. An unknown key is refused naming the keys this object has

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo spawn`

create a record of another kind from an existing one; the kind-to-kind edge the registry declares (KYO-50)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`, `link_id`, `evidence_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo spawn [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] --kind KIND [--title TEXT] [--description-file FILE] [--criteria-file FILE] [--touches-file FILE] [--scope SCOPE] [--from FILE] [--evidence-file FILE] [--check] [--force] [--no-prior-art] SOURCE_ID`

### Positional arguments

- `SOURCE_ID` — **required**. the record this one comes from

### Options

- `--kind KIND` — **required**. record kind to create; its accepts_from must include the SOURCE record's kind
- `--title TEXT` — optional. one-line title. Required, not defaulted from the source: what a decision decides is not what the proposal proposed, and a copied title would read as the same record twice. Optional exactly when --from supplies the title instead (KYO-330).
- `--description-file FILE` — optional. description from a file, or '-' for stdin. The source's own text is referenced, never copied (it stays live and would drift); this is where anything the new record should actually say goes
- `--criteria-file FILE` — optional. one criterion per line, from a file or '-'; required when the target kind's policy.requires_criteria is true
- `--touches-file FILE` — optional. one touch per line ('path', 'path#symbol', 'path#+' for additions to an existing file and nothing already in it, 'dir/' for a tree this work creates or owns, or '+path' for one file it creates), relative to the project root; required for archetype: work kinds, exactly as `kyo promote` requires it
- `--scope SCOPE` — optional. the part of the system this work belongs to, from kyosei/scopes.yml. Never defaulted from the source: a spawn crosses a kind boundary
- `--from FILE` — optional. file the new record from one markdown file instead of --title/--description-file/--criteria-file/--touches-file: front matter for title/scope/touches, a '## Criteria' section (one criterion per list item), everything else the description; refused together with any of those flags (KYO-330)
- `--evidence-file FILE` — optional. evidence stored with the new record in the same act, from a file or '-'; required when the target kind's policy sets requires_evidence_links and the SOURCE is a carried kind (a reminder becoming a decision: the operator's words -- who, when, where, then the words verbatim); not asked of a proposal or investigation source (KYO-522)
- `--check` — optional. with --from, validate the file (kind, scope, touches against the repo) and file nothing (KYO-330)
- `--force` — optional. file the record although one with an identical title and criteria was filed in this project within the last hour (KYO-538)
- `--no-prior-art` — optional. skip the prior-art search kyosei/config.yml's recx.prior_art would run for this record (KYO-585); `kyo prior-art <id>` runs it later

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference spawn`. `<name>` stands for a value of that run.

- "The stored scope is unchanged." — The stored scope is unchanged — derivation never overrides a declaration. (KYO-100.c3)

## `kyo split`

split unmet criteria to a new successor record, back-linked both ways (M5)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`, `successor_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo split [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] --to-successor CRITERION_ID [CRITERION_ID ...] RECORD_ID`

### Positional arguments

- `RECORD_ID` — **required**.

### Options

- `--to-successor CRITERION_ID [CRITERION_ID ...]` — **required**. unmet criterion id(s) to carry to the successor; a met or void one is refused

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo sweep`

flag stale touches and near-duplicate criteria; never edits (M6)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Usage: `kyo sweep [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--hook]`

### Options

- `--hook` — optional. how the packaged Stop hook invokes this: exactly the bare command inside a Kyosei project, and silent with exit 0 where none resolves (KYO-591)

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo touch-drift`

compare a dispatch's declared touches against what its branch actually changed (KYO-71) — reports, never refuses

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo touch-drift [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--against REF] DISPATCH_ID`

### Positional arguments

- `DISPATCH_ID` — **required**.

### Options

- `--against REF` — optional. the ref to diff from (default: main), same three-dot diff `kyo check-branch` uses

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference touch-drift`. `<name>` stands for a value of that run.

- "reported, not refused" — This is REPORTED, not refused — a touches table is a prediction and is routinely, defensibly wrong. What it costs is a later dispatch colliding with a file nothing knew was claimed. (KYO-71)

## `kyo ungate`

clear a gate by its own id; several ids, or every open gate with a tag, in one transaction (KYO-492)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Usage: `kyo ungate [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--tag TAG] [--apply] [--why TEXT] [--why-file FILE] [GATE_ID ...]`

### Positional arguments

- `[GATE_ID ...]` — optional. one or more gate ids; with several, every one is cleared or none is

### Options

- `--tag TAG` — optional. select every OPEN gate carrying this tag (from kyosei/gate-tags.yml) instead of naming ids; lists them and writes nothing without --apply
- `--apply` — optional. with --tag: clear every gate the selection lists, in one transaction
- `--why TEXT` — optional. one-line reason for clearing it
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

### Output notes

Text output prints the quoted short form in place of the full text after it, which is what it printed there before KYO-489. When it printed any, its last line is: explained in `kyo reference ungate`. `<name>` stands for a value of that run.

- "only holding-for does" — clearing it reports that something happened, not that something was decided (only holding-for does — KYO-59) (KYO-59)

## `kyo unlink`

retract a typed edge by its own id; the edge and the reason stay in the events ledger

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `link_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo unlink [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--why TEXT] [--why-file FILE] LINK_ID`

### Positional arguments

- `LINK_ID` — **required**.

### Options

- `--why TEXT` — optional. one-line reason for retracting it (required)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo verify`

record a criterion verification (laws 3, 9, 10)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo verify [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--verdict VERDICT] [--method METHOD] [--evidence-file FILE] [--from-report DISPATCH_ID] [--only CRITERION_ID [CRITERION_ID ...]] [--again] [--why TEXT] [--why-file FILE] [CRITERION_ID]`

### Positional arguments

- `[CRITERION_ID]` — optional.

### Options

- `--verdict VERDICT` — optional. met | unmet-unbuilt | unmet-unobserved
- `--method METHOD` — optional. machine-checked | human-observed | reasoned-about
- `--evidence-file FILE` — optional. captured evidence from a file, or '-' for stdin
- `--from-report DISPATCH_ID` — optional. apply that dispatch's current report's own criteria_verdicts[] — verdict, method, evidence and reason each read from the report rather than retyped (KYO-198); takes no CRITERION_ID of its own
- `--only CRITERION_ID [CRITERION_ID ...]` — optional. with --from-report: apply only these criteria's verdicts and leave the rest of the report's unapplied
- `--again` — optional. with --from-report: apply a verdict even though the criterion's latest verification already came from this report (KYO-415), or was recorded after this report was ingested (KYO-511); without it such an entry is listed as already applied, or as a newer verdict kept, and skipped
- `--why TEXT` — optional. one-line reason (required for unmet-unobserved)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo version`

print which build this copy is — package version, the commit it was built from and that commit's date; no project needed (KYO-226)

Needs a Kyosei project: **no** — it never searches for one.

Writes to the store: **no** — this command only reads.

Usage: `kyo version [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty]`

This command takes no arguments of its own.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo void`

retire a knowledge-archetype record; its id is never reused

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo void [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--why TEXT] [--why-file FILE] [--force] id`

### Positional arguments

- `ID` — **required**.

### Options

- `--why TEXT` — optional. one-line reason (required)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin
- `--force` — optional. retire the record with a gate still open, leaving it open

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo where`

locate this project and say whether this build can open its store (KYO-184)

Needs a Kyosei project: **optional** — it runs, and says so, where there is no project.

Writes to the store: **no** — this command only reads.

Usage: `kyo where [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty]`

This command takes no arguments of its own.

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo wont-do`

retire a work-archetype record; its id is never reused

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **yes** — an events row and a fresh export.

Top-level ids in `--json`: `record_id`. (KYO-194: an id the payload names an object for is repeated at the payload root, so a script reads it from there rather than from inside the object.)

Usage: `kyo wont-do [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--why TEXT] [--why-file FILE] [--force] id`

### Positional arguments

- `ID` — **required**.

### Options

- `--why TEXT` — optional. one-line reason (required)
- `--why-file FILE` — optional. read the reason from a file, or '-' for stdin
- `--force` — optional. retire the record with a gate still open, leaving it open

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.

## `kyo worktrees`

list dispatch worktrees, and remove the ones whose dispatch is finished (KYO-70)

Needs a Kyosei project: **yes** — it searches upward for one and fails if there is none.

Writes to the store: **no** — this command only reads.

Usage: `kyo worktrees [-h] [--root ROOT] [--actor ACTOR] [--json] [--full] [--slim] [--compact-json] [--pretty] [--prune] [--include-unmerged] [--against REF] [--expand-stops]`

### Options

- `--prune` — optional. actually remove them (law 11: listing is the dry run). Refuses any worktree holding uncommitted work — that directory may be the only surviving copy of a dead worker's output — leaves one whose branch is not merged into the base (KYO-207), and never touches a running dispatch's, or any branch
- `--include-unmerged` — optional. with --prune: remove a finished worktree even though its branch is not contained in the base (or git could not tell). A dispatch that has reported is not a dispatch anybody has merged, and the rehearsal of that branch runs in that directory (KYO-207)
- `--against REF` — optional. the base a worktree's branch must be contained in to count as merged (default: main) — name your trunk here if it is called something else
- `--expand-stops` — optional. name the unaudited stop events on finished-and-gone rows too, instead of printing their count, and print the stop-audit census (per-dispatch counts by reason, then each unmatched stop with its candidates) that the default output folds into one line (KYO-423); --json carries them either way

Also accepts the options every command accepts: `--help`, `--root`, `--actor`, `--json`, `--full`, `--slim`, `--compact-json`, `--pretty`.
