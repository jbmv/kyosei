# What Kyosei is, and why

After this page you will know which parts of Kyosei you need, where your own thoughts go
and where the agents' work goes, and who is responsible for what.

## The problem it solves

When you build software with coding agents, the agents do not remember. Each session
starts cold. A plan agreed yesterday lives in a chat transcript nobody will reread. A task
marked "done" may mean "written" or may mean "checked". Two agents working in parallel can
edit the same file without either knowing. Over a few weeks the project's real state and
everyone's picture of it drift apart.

Kyosei is a store for that state. It keeps three things for a project:

- **ideas**: what you, the operator, have thought of, in plain text with no structure;
- **records**: the work, written down with what "done" means, which files it touches, and
  what has actually been checked;
- **evidence**: what was observed, kept verbatim and linked to the record it supports.

Every change is logged with who made it and when. That is what "provenance-first" means:
any fact on the board can be traced to where it came from.

## What you need, and what is optional

**The CLI is required.** It is called `kyo` (also `kyosei`), it is installed once per
machine with `uv tool install kyosei`, and it is the only thing that writes the store.
Everything else reads through it.

**The server and the app are optional.**

- `kyo serve` is a small HTTP server on your own machine that lets a viewer read your
  projects. It writes no records.
- The desktop app (macOS) is one such viewer: a board, record pages and a terminal per
  project. Without it, `kyo brief`, `kyo list` and `kyo show` answer the same questions in
  the terminal.

Kyosei is built for Claude Code, whose hooks enforce its rules. Another agent harness can
run `kyo` commands, but nothing would enforce the rules there.
[Machine layouts](07-machine-layouts.md) covers when the optional parts are worth it.

## The seam: ideas are yours, records are the orchestrator's

A Kyosei project has two areas, and the line between them is deliberate.

**`ideas/` is yours.** Write one file per idea, named however you like. There
is no format, no required field and no command to run. Nothing there is counted, checked
or flagged as stale. An idea can sit half-formed for as long as you like. A passing
thought can go in the desktop app's Scratchpad instead, one file at
`~/kyosei/<project>/scratchpad.md` that nothing lists; a thought you want to find again
belongs in its own file under `ideas/`.

**`kyosei/` is the orchestrator's.** It holds the records, their history, the project's
configuration and its standing rules. You do not edit it by hand. When you want a record
filed, a rule changed or a setting moved, you ask the orchestrator and it runs the `kyo`
command that does it, which keeps the change in the log.

The orchestrator turns an idea into a record (it *promotes* it) at the moment someone can
say what done looks like. Until then, structure would be invented, and invented criteria
are worse than none. `kyo ideas review` lists the ideas still waiting, oldest first.

## Who does what

| | Operator | Orchestrator | Worker |
|---|---|---|---|
| Ideas | writes them | promotes them into records | never sees them |
| Records | asks for them, approves proposals | files, edits, verifies, closes | reports on one; never writes the store |
| Configuration, rules, documents setup | decides | edits, when asked | never |
| Code | reviews what needs a person | merges worker branches | writes it, on its own branch |
| Tests | | runs the whole suite once per wave, on the merged tree | runs only the tests that cover its change |
| Documentation | decides which documents count | merges doc changes, assembles numbered sections | proposes doc changes on its branch |
| Deploys | decides who deploys | deploys only if the project says so | never |

Most of the worker column is enforced, not hoped for. A hook refuses a worker that wrote to
the store or stopped with uncommitted work, and another can refuse a whole-suite command
inside a worker's worktree. [Sessions and hooks](03-sessions-and-hooks.md) has the list.
The deploy row is the one Kyosei leaves to you: some projects have the operator deploy, some
let the orchestrator deploy when asked, and some give it the job outright.
[Dispatching work](05-dispatching.md) covers that choice.

## Where the store lives

By default a project's store is its own git repository at `~/kyosei/<project>/`, separate
from the code, because records hold verbatim text you may not want published with your
code. It can instead live inside the code repository. [Getting started](04-getting-started.md)
explains the choice.

Next: [Records](02-records.md).
