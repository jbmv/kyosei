# Kinds, scopes and documents

After this page you will know what the kinds of record are for, when a project needs
scopes, how Kyosei keeps documentation in step with the code, and which of these defaults
you might change.

## Kinds

([D6](setup-decisions.md#d6-kinds)) Every record has a kind. A kind is not a label: it
declares what a record must carry, what it may lead to, whether it counts as work on the
board, and how it is dispatched. That is what stops a question from being closed as if it
were a build, or a design from being treated as approved because someone wrote it down.

The defaults, in `kyosei/kinds.yml`, are ten kinds in two groups.

**Work kinds** have a status and move toward done.

| Kind | For | Dispatched? |
|---|---|---|
| `bug` | a defect; its criteria are the reproduction | yes, to change code |
| `task` | a unit of work with clear acceptance criteria; the default | yes, to change code |
| `chore` | mechanical, low-risk upkeep such as a dependency bump | yes, to change code |
| `investigation` | answering a question without changing production code | yes, to query only |
| `experiment` | testing a hypothesis on a copy, a staging run or a dry run, before a decision commits to it | yes, with someone watching; it may change non-production data |
| `proposal` | a design argued before it binds; needs your approval (`kyo confirm`) | yes, to write prose only |
| `plan` | a group of records, with no criteria of its own | no; its members are |
| `reminder` | a decision waiting on you, or a reminder tied to a date or an event; the brief shows its age. Not for a defect (a bug), a settled rule (a decision, filed with your words) or buildable work (a task) | no; file a task or a decision from it |

**Knowledge kinds** are current or void, never done.

| Kind | For |
|---|---|
| `decision` | a choice that shapes the architecture, citing its evidence. One you made in conversation is filed directly with your words attached as evidence (`kyo new decision --evidence-file`); one that needs design work first comes from an approved proposal, and one can also come from a finished investigation or a reminder. |
| `evidence` | a recorded fact other records cite. |

**How they relate.** Kinds connect along fixed edges, and the edges are the planning
process:

- an investigation can produce a decision, a task, a bug, an experiment or evidence;
- a proposal you approve can produce a decision, a plan or an experiment;
- a plan groups the tasks and bugs that carry it out, and reports how far along they are;
- a reminder is settled, or turned into a task, bug, investigation, proposal or decision.

`kyo spawn` follows those edges and refuses any other. A decision filed directly instead has
your words behind it: `kyo new decision` refuses without `--evidence-file`.

**Changing them.** Adding a kind, or changing what one accepts, is easy. Removing a kind
that records still use is not refused, but those records stop counting and `kyo doctor`
reports each one at every session stop. Move them first with `kyo edit <id> --kind <kind>`.
Most projects keep the ten; the only setup question is whether the project has a recurring
kind of work they do not fit.

## Statuses and gates

([D9](setup-decisions.md#d9-statuses-and-gates)) Work moves `open`, `working`,
`built`, `verified`, `done`, or ends at `wont-do`. Moves are checked: a record cannot reach
`done` with an unmet criterion. A **gate** parks a record on a condition without changing
its status: waiting for an event, for a person to run something, for a person to review
something, or a plain hold. You will rarely change these. The common addition is a `deploy`
gate, covered in [Dispatching work](05-dispatching.md).

## Scopes, for a repository with several components

([D7](setup-decisions.md#d7-scopes-for-a-repository-with-several-components)) If one
repository holds several components (an app, a server, a shared library), declare them as
scopes in `kyosei/scopes.yml`. A record's scope says which component it belongs to;
`kyo list --scope <name>` filters the board by it, and the changelog groups by it.
`kyo new` suggests a scope from a record's touches. None are declared by default, and
`--scope` is refused until some are. A repository with one component needs none.

## Documents

([D8](setup-decisions.md#d8-documents)) Documentation drifts for the same reason plans do:
the code moves and nobody tells the prose. Kyosei can keep the two connected, for the
documents you declare in `kyosei/documents.yml`. None are declared by default;
`kyo docs suggest` drafts a declaration from the repository and writes nothing.

What declaring a document enables:

- **Governed sections.** A `governs:` line under a heading names the code that section
  describes. When a worker's branch changes that code, Kyosei raises an obligation: the
  section is updated, or someone records that it needed no change. The orchestrator settles
  each one before the record closes, and `kyo docs check` fails when a `governs:` line names
  a file that no longer exists.
- **Numbered sections**, for documents that grow by appending (`## 204. Title`), such as an
  engineering log. Workers write fragments; the orchestrator assigns the numbers when it
  merges, so parallel workers never claim the same one.
- **Amend links.** An earlier section is not rewritten; it is marked amended or superseded
  by a later one, and Kyosei checks that those links resolve.

**The choice for each document** is between editing in place, for a living reference that
should always say what is true now, and appending numbered sections that amend earlier
ones, for a record of what was decided and when. Most projects want both, for different
files. You choose which documents count and which style each uses; the setup agent walks
you through `kyo docs suggest`'s draft.

**`ssot:` or `numbered:`.** Declare a set `numbered:` when its documents are a log: each
entry is written once, later ones amend earlier ones, and the order is the history.
Declare it `ssot:` when its documents are the current statement of requirements, written
up front and edited in place as they change, and other documents cite each requirement by
an id (`API-12`) rather than by a heading or a path. An `ssot:` set gives every
requirement one owning heading, checks that every id cited in its prose resolves to it,
refuses a citation of a retired requirement that does not name its replacement, and keeps
a retired requirement to a short tombstone, so the history goes to the record or the
commit and not into the document. A set cannot be both. A current-state set whose
sections carry no ids (a reference, a guide) can still declare `ssot: {}`, leaving out
`id_heading`, `id_pattern` and `prefixes` together: it gets the tombstone rule and none of
the id rules.

**`history_lint: warn` or `block`.** Every `ssot:` set also lints the prose a branch adds
for history: a sentence such as `this used to run nightly` belongs in a decision record or
the commit message, not in a document that states what is true now. Leave it at `warn`
(the default) while a set is new or its documents still carry history: a worker's stop
prints each phrase it introduced and is never blocked. Choose `block` once the set's
documents have been cleaned and you want them kept that way: then `used to` and
`previously` block a worker's stop unless a commit carries a `Kyosei-History-OK` trailer,
which you review in `kyo report`. Choose `off` for a set whose documents quote history on
purpose, such as a changelog. See
[Docs governance](../reference/topics/docs-governance.md) for the block's keys and
refusals.

## What must never enter the store

([D11](setup-decisions.md#d11-what-must-never-enter-the-store)) Kyosei stores evidence
verbatim and filters nothing, because a trimmed log is a less trustworthy log. If the
project handles customer data, credentials or anything regulated, add a project rule to
`kyosei/procedures.md`, so every brief carries the restriction. `kyo redact` removes text
after the fact.

## Prior-art search

([D12](setup-decisions.md#d12-prior-art-search)) With the optional extra
`kyosei[recx]` installed, Kyosei can search recx's index of published repositories for
existing work when a proposal is created, and store the results on the record as evidence
written by strangers. It is off until the project chooses: `kyo prior-art-setting on` or
`off` records the choice, and `kyo prior-art <record-id>` searches for any record on
demand. See [Operating](../reference/topics/operating.md) for the trigger and how to change it.

Next: [Machine layouts](07-machine-layouts.md).
