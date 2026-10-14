<!--
templates/brief-template.md — skeleton for the brief `kyo dispatch <id>` generates and
freezes onto a dispatch object (`docs/design/charter.md`, "CLI surface": "Brief generation
(from the record, not hand-assembled)"). This file is not rendered as-is; it documents the
slots that generated brief must fill, and which ones an orchestrator may still hand-write.
Every [MACHINE-FILLED]
slot below must have a real data source named in its comment — a slot with no named source
is a field with no consumer (Law 2) and should not exist.

Slot legend:
  [MACHINE-FILLED]     — generated entirely from the store; an orchestrator never edits this by
                          hand, and hand-editing it defeats the point (it is what makes the
                          brief trustworthy without re-deriving state from files).
  [ORCHESTRATOR-WRITTEN]  — the genuinely task-specific part; the only prose an orchestrator
                          actually composes for this dispatch.
  [FIXED BOILERPLATE]  — identical on every brief, not generated per-dispatch, but also not
                          hand-typed each time — lives once, here, and is copied verbatim.
-->

# Dispatch brief: {{record_id}} — {{record_title}}

<!--
[MACHINE-FILLED] Header block. Source: the record row plus its dispatch object.
worked_from_sha is stamped by the tool at brief-freeze time, per Law 16 — never typed.
-->
- **Kind**: {{kind}} ({{archetype}})
- **Dispatch id**: {{dispatch_id}}
- **Base SHA**: `{{worked_from_sha}}` — the commit this brief was generated at. Your
  worktree may branch from this sha exactly or from a commit just after it; read the sha
  you actually branched from yourself (`git rev-parse HEAD`) and report that as
  `worked_from_sha` — never copy this line (KYO-105).
- **Generated**: {{generated_at_utc}} UTC
- **Worktree**: `{{worktree_or_not_declared}}` — where this dispatch's work happens,
  stated relative to the project root (KYO-52: the brief is frozen into git-canonical
  `_briefs.jsonl`, so an absolute path in it is permanent and carries a username).
  Source: `dispatches.worktree`, declared by the orchestrator at `kyo dispatch --worktree`
  (KYO-22), never sniffed from the orchestrator's own cwd — normally a different checkout
  from the worker's, so reading it would record the wrong place with total confidence.
- **Branch**: `{{branch_or_not_declared}}` — the branch this dispatch's commits must land
  on. Source: `dispatches.branch`, declared by the orchestrator at `kyo dispatch --branch`
  (KYO-32); same slot style as the worktree above, and for the same reason. A worker left
  to *infer* its branch can infer a different one from its orchestrator, and the orchestrator's
  `git merge` of the branch it expected then reports "Already up to date", exits 0, and
  ships nothing. Enforced rather than merely stated: `kyo report` refuses a report whose
  `commits[]` are not reachable from this branch. Both slots render
  "(not declared at dispatch time)" when the orchestrator declared nothing — an absence
  stated, never implied.
- **Worktree tooling**: {{worktree_tooling}} — KYO-143. Source: `config.worktree_setup`
  (`kyosei/config.yml`), the commands this project declares a fresh dispatch worktree
  needs before its own tests can run there. The last of the header's "conditions of this
  work" bullets, and the only one describing the *tree the worker is standing in* rather
  than a declaration about the work.

  A brief that says nothing here is not neutral. `git worktree add` checks out tracked
  files and nothing else, so everything gitignored — a `.venv/`, a `node_modules/`, a
  built binary, a seeded database — is absent the moment a worker arrives, while the
  project's own README, CLAUDE.md and Makefile go on naming paths that are true only in a
  checkout somebody had already built in. KYO-127's dispatch spent the first fifth of its
  budget on that.

  Rendered either way, like the branch and worker-model slots above, and the empty case is
  the DESIGNED case rather than a fallback: a project that declared nothing gets a brief
  stating what a fresh worktree is missing (KYO-143.c4). This slot never asserts that a
  path exists — only that the project did or did not declare how to create one — because
  `kyo dispatch` normally runs in a different checkout from the worker's, and a claim
  about the worker's tree would be a claim about a tree this command has not looked at
  (KYO-143.c3; the defect class KYO-128 shipped about).

  Kyosei renders these commands and RUNS NONE OF THEM. What a worktree needs is a fact
  only the consuming project holds, so the mechanism declares rather than assumes
  (KYO-143.c2); a store command that ran a project's package manager on its own
  initiative would be guessing precisely what the key exists to have the project state.

  The bullet also carries a caveat that is not derived from the config and is rendered on
  every brief naming a worktree: a worktree isolates FILES and inherits the spawning
  process's ENVIRONMENT unchanged, so `VIRTUAL_ENV`/`PATH` can still point into the
  orchestrator's checkout. No declared command can fix that, which is why it is stated
  independently of whether one was declared.

## Record summary

<!--
[MACHINE-FILLED] Source: record.title, record.description, record.status, record.plan
membership (Core schema: records). Rendered verbatim from stored text — never re-typed or
summarized by the orchestrator, because a summary is exactly the kind of restatement that
drifts (Law 16).
-->
{{record_description}}

**Status**: {{status}}{{#if gate}} — gated: `{{gate_type}} {{gate_condition}}`{{/if}}
**Plan membership**: {{plan_id_or_none}}

## Criteria

<!--
[MACHINE-FILLED] Source: criteria table for this record (Core schema: criteria — id, text,
state, reason, optional member-set, optional query). States are Law 10's four values, never
flattened to a boolean. A criterion whose text names a mechanism (a file, a script, a table)
rather than an observable outcome should be flagged here by the generator once that lint
exists (procedures.md's mechanism-vs-outcome rule is currently aspirational) — until then,
read one skeptically if you find one.
-->
| id | text | state | reason (if void/unobserved) |
|---|---|---|---|
| {{criterion_id}} | {{criterion_text}} | {{criterion_state}} | {{criterion_reason}} |

## Touches

<!--
[MACHINE-FILLED] Source: the dispatch's touch CLAIM — the touches table for this record
UNIONED with the touches of every record the dispatch formally covers via `--also-covers`
(Core schema: touches — record_id -> files, symbols, shared resources). KYO-76: this slot
named the primary record's touches alone while `commands._claim_of` computed the union, so a
worker on a two-record dispatch got the criteria of both records and the touches of one — and
the write-scope section below then stated a boundary that excluded files the worker's own
criteria required. The union is passed in from the same value the disjointness check used, so
what you were checked against and what you're allowed to change cannot drift apart. Each row
names the record that put the path in scope, so you can go back to the right record for the
reason it is there.
-->
| declared by | path | symbol |
|---|---|---|
| {{touch_record_id}} ({{touch_record_role}}) | {{touch_path}} | {{touch_symbol}} |

Shared resources: {{touches_shared_resources}}

## Cited claims and evidence

<!--
[MACHINE-FILLED] Source: claims attached to this record and any it inherits from (Core
schema: claims — statement, level, instrument/method, author, date, verdict history).
VERIFICATION LEVEL IS SHOWN, NOT JUST THE CLAIM TEXT — this is the mechanism for "I inherit
confidence from prose" (`docs/design/charter.md`, "User stories"): a claim's level tells you
what you're allowed to believe without re-checking, and what you should re-verify before
relying on it.
-->
| claim | level | instrument/method | author | date |
|---|---|---|---|---|
| {{claim_statement}} | **{{claim_level}}** (asserted / derived / verified) | {{claim_method}} | {{claim_author}} | {{claim_date}} |

## Write scope — your capability boundary, stated mechanically

<!--
[MACHINE-FILLED] Source: kinds.yml, this record's kind -> policy.write_scope. Rendered as an
explicit statement, not inferred: "Tell my workers their capabilities mechanically. A worker
that must infer its write scope either overreaches or under-delivers"
(`docs/design/charter.md`, "User stories").
-->
**`write_scope: {{write_scope}}`**

{{#if write_scope == "none"}}You may read anything. You may not write code, run mutating
queries, or touch production data. Produce findings/prose only.{{/if}}
{{#if write_scope == "queries-only"}}You may run read-only queries against the systems named
in Touches. You may not write code or mutate any data, production or otherwise.{{/if}}
{{#if write_scope == "code"}}You may edit code and commit on your dispatch branch, within the
files/symbols named in Touches. You may not touch production data or deploy.{{/if}}
{{#if write_scope == "prod-data"}}You may mutate the production data named in Touches, and
you MUST run dry-run-by-default: produce and report a dry-run plan first; do not pass
`--apply` (or equivalent) without it being explicitly requested in this brief's
orchestrator-written section below.{{/if}}

## Standing rules (worker-audience)

<!--
[MACHINE-FILLED] Source: kyosei/procedures.md, filtered to audience: worker (Law 15 — brief
generation includes only worker-audience rules; the orchestrator-audience rules never reach
here). Rendered as title + enforced-by only; the justifying prose stays in procedures.md.
-->
{{#each worker_rules}}
- **{{title}}** (enforced-by: {{enforced_by}})
{{/each}}

## Standing stance

<!--
[FIXED BOILERPLATE] — identical on every brief, verbatim from `docs/design/charter.md`,
"CLI surface" ("Brief generation"). Not generated from a template variable because it is
not project- or record-specific; it is the tool's own posture.
-->
**Flag conflicts, do not resolve them. Contradicting this brief must be cheaper than
complying with it.** If a premise above is false, say so in `premise_false[]` in your report.
If you disagree with the approach, say so in `disagreements[]`, with your rationale — and, if
your judgment says the approach is wrong, you may act on your own judgment and report the
disagreement, rather than either silently complying or silently doing your own thing. Report
what you tried and rejected, not just what you shipped.

---

## Task

<!--
[ORCHESTRATOR-WRITTEN] The only section an orchestrator actually composes by hand for this
dispatch: what specifically to do, in this record's context, that the machine-filled sections
above don't already say. Keep this genuinely task-specific — anything that's true of every
dispatch of this kind belongs in kinds.yml's policy or procedures.md, not repeated here by
hand (Law 16: prose never restates a derivable fact).
-->

{{controller_task_prose}}

## Report

When finished (or when you stop for any reason — including finding the premise false or the
approach wrong), write your structured report per the schema `kyo report-schema` prints — the
same document `kyo report` validates against, read straight from the installed package — and
note in your final message that it's ready for `kyo report {{dispatch_id}} --file <path>`.
Commit your work before you stop; an uncommitted worktree at stop time is checked and blocked.

If you are resumed on this dispatch after your report has already been ingested, do not start
a second one and do not leave the correction in prose: update your report file and add
`"amends": "<the report id the orchestrator ingested>"` at its top level. It is ingested as the
current report, the earlier one stays readable and marked superseded, and the checks `kyo
report` runs — every commit reachable from the declared branch, touch drift against
`touched_files[]` — run again over the corrected document.
