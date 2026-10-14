# The operator's guide

This guide is for the person who runs a project with Kyosei: the **operator**. It explains
what Kyosei is, the ideas it is built on, and the choices you make when you set it up. It
is short on purpose. Each page says at the top what you will be able to do after reading
it, and explains why before how.

If you are an agent setting Kyosei up in a project, run `kyo onboard` instead: it prints
the setup prompt, which asks the operator the same questions this guide explains.

## Reading order

1. [What Kyosei is, and why](01-what-kyosei-is.md): the parts you need and the parts you
   don't, where your ideas go, and who does what.
2. [Records](02-records.md): what a record holds, and how criteria, touches and claims keep
   work from drifting.
3. [Sessions and hooks](03-sessions-and-hooks.md): the session brief, shift notes, the
   events ledger, and the hooks that run the rules for you.
4. [Getting started](04-getting-started.md): `kyo onboard`, `kyo init` in an empty folder
   or an existing project, and where the store lives.
5. [Dispatching work](05-dispatching.md): worker models, the long-command rule, worktree
   setup, and who runs tests, writes docs and deploys.
6. [Kinds, scopes and documents](06-kinds-scopes-documents.md): the kinds of record, scopes
   for a repository with several components, and documents Kyosei keeps honest.
7. [Machine layouts](07-machine-layouts.md): the CLI on its own, one machine with the app,
   or a dev box you reach from a laptop.

[Setup decisions](setup-decisions.md) is the reference list behind pages 4 to 7. Every
decision there has an id such as **D1**, and the pages above link to it by that id.

## Three words

- **Operator**: you, the person. You write ideas, make the setup decisions, and approve
  what needs a person's eyes.
- **Orchestrator**: the long-running agent session that owns the store. It files records,
  dispatches workers, verifies their work and closes records.
- **Worker**: a subagent the orchestrator sends to do one piece of work, in its own git
  worktree, with a fixed set of instructions called a brief.

Before KYO-519 the CLI, the skill and the standing rules called the orchestrator the
**controller**, and some places still do: `kyo controller` is kept as an alias of
`kyo orchestrator`, the config key `controller_only_commands` and the standing rules'
`audience: controller` are read alongside their new spellings, and stored history keeps the
old word. It is the same thing. Since KYO-524 the packaged standing rules say
`audience: orchestrator` under `## Orchestrator-audience rules`; a project's own ledger
keeps whichever spelling it has until it adopts the new wording, and both are read the same.
This guide says orchestrator throughout.

## Where to look next

- [The project README](../../README.md): installing, upgrading, and the full operating
  reference.
- [Topic references](../reference/topics/): one page per mechanism, in depth.
- [The command reference](../reference/cli.md): every `kyo` command and flag, generated
  from the CLI itself.
- `kyo <command> --help`: the same, from the build you have installed.
