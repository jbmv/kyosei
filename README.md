# Kyosei (共生)

Kyosei is a command-line tracker for ideas, work and evidence, built for software that a
person and several AI agents develop together. One CLI, `kyo` (also installed as
`kyosei`), keeps every piece of work as a record with acceptance criteria and the files
it expects to touch. Work goes to an agent as a frozen brief and comes back as a
structured report, and every claim that something is done carries the evidence it was
checked with. *Kyosei* is Japanese for symbiosis.

The CLI can be run by a person or by any agent that runs shell commands. The session
hooks and the orchestrator's skill, which carry the workflow described below, ship as a
Claude Code plugin inside the installed package; Claude Code is currently the only agent
harness Kyosei integrates with.

## Why

When agents do much of the work on a project, it becomes hard to establish afterwards
what was asked for, what was actually done, what was checked and by whom, and which
documents went stale along the way. Chat history and hand-written handoff notes do not
reliably preserve those facts. Kyosei records them as data:

- **Provenance.** Records, criteria, dispatches, reports and verifications live in a
  store. The store's database is rebuilt from a JSON export that is committed to git, so
  every change to the board is a reviewable diff.
- **Generated briefs.** A dispatched agent is told what its record says: the criteria,
  the files in scope, its write permissions and the project's standing rules. The only
  prose written by hand is the task-specific part.
- **Structured reports.** A worker answers each criterion with a verdict and the method
  it used. It must also report any premise in its brief that turned out false and any
  instruction it disagreed with. Nothing in a report changes the board until the
  orchestrator accepts it.
- **Document governance.** A section of a document can name the code it describes. When
  that code changes, Kyosei raises an obligation to update the section or to confirm it
  is still true ([docs governance](docs/reference/topics/docs-governance.md)).
- **Checks by hook.** Claude Code hooks brief each session as it starts and run the
  staleness sweep and invariant checks when it stops.

## Install

Kyosei needs Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). Install `kyo`
once per machine, from a checkout of this repository:

```sh
git clone https://github.com/jbmv/kyosei.git && cd kyosei
uv tool install .      # a snapshot of this source; re-run with --force to upgrade
kyo version            # which build is on your PATH, and how it was installed
```

Once the package is published on PyPI, `uv tool install kyosei` (or
`pipx install kyosei`) replaces the first two lines.

Then, in the project you want to track, run:

```sh
kyo onboard
```

It prints a setup prompt. Give that prompt to the agent working in the project, or paste
it into a Claude Code session there. The agent works out whether the folder is empty, an
existing repository or an existing store, runs `kyo init` and the install commands, and
asks you the decisions that are yours: where the store lives, the record-id prefix, how to
wire Kyosei into Claude Code, which models workers use. `kyo onboard --check` later reads a
project back against the same checklist and changes nothing. Bare `kyo init` in a folder
with no store writes nothing and names the ways to start: `kyo onboard`,
`kyo init --defaults` for scripts and CI, and `kyo init --in-repo`.

The hooks and the skill reach Claude Code as a plugin inside the installed package, which
setup registers and enables with your consent; an upgrade of `kyo` then reaches them with
no further command. Setting up without `kyo onboard`, the alternative to the plugin for
environments without plugin support, and the optional prior-art search are covered in
[getting started](docs/guide/04-getting-started.md) and the
[installation reference](docs/reference/topics/install.md).

## Operating a Kyosei project

There are three roles, and each has its own surface:

```text
 operator (you)            orchestrator (a Claude Code session)        workers (dispatched agents)
 --------------            ------------------------------------        ---------------------------
 jot ideas in ideas/  -->  promote an idea to a record with
 file bugs directly        criteria and declared touches
                           kyo dispatch <id>  ----------------------->  read the frozen brief,
                                                                         work in their own git
                                                                         worktree, commit there
                           kyo report <dispatch> <-------------------   .kyosei-report.json
                           read it, verify, kyo accept,
 approve what needs  <--   merge the branch
 a person's eyes           sweep and doctor run at every stop
```

- **The operator** writes freely into `ideas/`, with no required fields, and decides
  what matters. Any approval that requires a person is left to the operator.
- **The orchestrator** is a long-running session. It turns ideas into records,
  dispatches ready work, ingests and checks reports, and moves records along. The
  `kyosei` skill, which the plugin provides, teaches it the loop.
- **Workers** see only their brief. Each one works in its own worktree and reports back.
  A worker never writes to the store, and a hook refuses a branch that tries.

A session starts with `kyo brief`. It shows what changed since the last session, what is
in flight, what is gated and what is ready to dispatch. `kyo next` lists work that can be
dispatched now without colliding with running work, and `kyo show <id>` prints any record,
dispatch or criterion. The whole workflow, step by step, is in
[docs/reference/topics/operating.md](docs/reference/topics/operating.md).

## Documentation

- **[docs/guide/](docs/guide/)**: the operator's guide. It covers concepts, getting
  started and the setup decisions, with the reasoning behind each choice.
- **[docs/reference/topics/](docs/reference/topics/README.md)**: the full reference,
  grouped by subject: install and upgrading, day-to-day operation, the store and its
  layout, the registries, the dispatch loop, docs governance, serving and the desktop
  app, and the rules and exit codes.
- **[docs/reference/cli.md](docs/reference/cli.md)**: every command and flag, generated
  from the parser. `kyo <command> --help` prints the same.
- **`kyo changelog`**: what each build changed, as packaged with the build you run.
- **[docs/design/charter.md](docs/design/charter.md)**: the design record. It holds the
  laws the store enforces, the architecture, and the reasoning behind both.

## Working on Kyosei itself

[AGENTS.md](AGENTS.md) is the guide for a person or an agent building, testing or
contributing to Kyosei.

```sh
make venv    # provision .venv (Python 3.12 via uv) and run nothing else
make test    # the Python suite
make lint    # ruff
```

The desktop app under `apps/desktop/` is a separate Electron project with its own checks;
see its README. Kyosei's own development is tracked with Kyosei. Source comments, tests
and these documents cite its records as `KYO-n`, the id of a record in that tracker, with
a suffix such as `.c2` or `.d1` for a criterion or dispatch within one;
`kyo changelog --all` lists the completed records with their titles.

## License

Kyosei is released under the [Apache License 2.0](LICENSE).
