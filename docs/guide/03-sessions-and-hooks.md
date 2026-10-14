# Sessions and hooks

After this page you will know how an orchestrator session starts already knowing the
project's state, how judgment carries from one session to the next, where the history of
every change is kept, and which rules the hooks enforce so nobody has to remember them.

## Why sessions need help

An agent session ends, and the next one starts with an empty context. The usual remedy is a
handoff document written at the end of the day, which is written in a hurry, from memory,
by the session least able to judge what mattered. Kyosei replaces it with three things: a
brief derived from the store, notes written as the work happens, and a ledger of every
change.

## The brief

`kyo brief` prints the state of the project: counts by kind, work that is ready to
dispatch, records that are finished but not yet moved, gated work and what each gate waits
for, running dispatches, staleness flags, reminders with their age,
and everything that changed since the last brief.

It is computed from the store every time, so it cannot be out of date. With the hooks
installed, it runs at the start of every session and the orchestrator begins already
oriented, rather than by reading files and guessing.

## Shift notes

Some things the store cannot derive: what the orchestrator suspects, what it would try
next, why it chose one approach over another. Those go in **shift notes**, written with
`kyo note --shift` at the moment the thought occurs, not saved up for the end.

The next session's brief prints the shift notes written since the previous brief, and the
last note written before that session began, in full. That last note is the handoff. Notes
written as you go are more honest than a summary written at the end, and a session that
ends abruptly loses nothing.

A decision waiting on you, or a reminder tied to a date or an event, is better as a
`reminder` record. The brief lists each one with its age until it is settled. A defect
is a bug, a settled rule is a decision filed with your words, and buildable work is a task.

## The events ledger

Every change to the store (a record filed, a criterion verified, a status moved, a
dispatch closed) is appended to an events ledger under `kyosei/export/_events/`, with who
made it, when, and what it changed. The ledger is append-only and committed to git, beside
one JSON file per record.

This is where provenance comes from. Any fact on the board can be traced to the change that
put it there. The working database, `kyosei/kyosei.db`, is not committed: it is rebuilt from
the exported files with `kyo rebuild`, so git always holds the authoritative copy, and every
change to it is a diff you can review.

## Standing rules

`kyosei/procedures.md` is the project's rule book, grouped by audience: workers, the
orchestrator, and the operator. Every dispatch brief quotes the worker rules. Each rule
states how it is enforced: by a mechanism (a command or hook that refuses), partly, or not
at all (it relies on the agent). `kyo rules` lists them, and `kyo rules "<title>"` prints
one in full.

The rules are yours. `kyo init` copies the defaults in, you can edit or add to them by
asking the orchestrator, and `kyo install-procedures` adds new default rules after an
upgrade without overwriting yours.

## The hooks

Kyosei's Claude Code plugin carries these hooks, and on the copies `kyo install-hooks` adds
them to the project's `.claude/settings.json` instead
([C1](setup-decisions.md#c1-install-the-hooks-and-skill-the-plugin-or-the-copies)). Claude
Code runs them at the moments below; each turns a rule into something that happens on its
own. Outside a Kyosei project every one of them exits silently.

| When | What runs | What it does |
|---|---|---|
| A session starts | `kyo brief --compact --session-start` | Prints the brief into the new session. |
| A session starts | `kyo sessions --hook` | Records which Claude Code sessions belong to this project. |
| You send a prompt | `kyo check-build --hook` | Says so, in one line, when the installed `kyo` changed mid-session. |
| An agent runs a shell command | `kyo check-push --pre-tool-use` | Refuses a `git push` that would publish a store kept inside the code repository, until someone accepts that. |
| An agent runs a shell command | `kyo check-command --pre-tool-use` | Refuses a whole-suite command inside a worker's worktree, if the project lists such commands. |
| A worker stops | `kyo check-clean --subagent-stop` | Refuses to let a worker stop with uncommitted work. |
| A worker stops | `kyo check-branch --subagent-stop --against main` | Refuses a worker branch that changed anything under `kyosei/` or `ideas/`. `main` here means the project's trunk, whatever it is called. |
| The session stops | `kyo sweep`, then `kyo doctor --hook` | Raises staleness flags, then checks the store's invariants and reports any problem in one line. |

The two worker checks block a worker's first attempt to stop and name the remedy. If the
worker stops again without fixing it, they report the finding without blocking, and the
unblocked stop is recorded where `kyo show <dispatch-id>` and `kyo report` show it, so a
worker that cannot act is not held indefinitely.

`kyo sweep` flags; it never edits a record. A flag says, for example, that files a record
touches have changed since the record was written. It appears in every brief until the
orchestrator resolves it or the record is settled.

Without the hooks every command still works, but each rule above becomes something an
agent must remember to do. Installing them needs your consent because they change the
project's Claude Code configuration: the plugin is enabled in `.claude/settings.local.json`,
and the copies are written to `.claude/settings.json`. This is decision
[C1](setup-decisions.md#c1-install-the-hooks-and-skill-the-plugin-or-the-copies).

Next: [Getting started](04-getting-started.md).
