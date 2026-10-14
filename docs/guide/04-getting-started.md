# Getting started

After this page you will be able to set Kyosei up in a new or existing project, knowing
what each setup question means and which answers are hard to change later.

## The short version

1. Install the CLI once per machine: `uv tool install kyosei`.
2. Open Claude Code in your project and ask it to set up Kyosei. The agent runs
   `kyo onboard`, which prints its setup instructions, and follows them.
3. Answer its questions. It asks them in two rounds, each with a default and a one-line
   reason, so you answer twice rather than once per question.
4. With your consent, the agent runs `kyo harness install claude-code`, which registers
   Kyosei's Claude Code plugin on the machine and enables it in the project. The plugin
   ships inside the package you installed and is not offered by any hosted marketplace, so
   this command is how Claude Code finds it. The copies described below are the
   alternative.
5. Read the readback it ends with: a fixed list of what was set up, which you can check.

The agent runs every command itself. It needs you for two things only: your consent to
set up the plugin (or, on the copies, to write `.claude/settings.json`), and the choices
that are yours. The rest of this page explains those choices so you can answer them with
confidence.

## Before `kyo init`

**Is the folder a git repository with a commit?**
([A1](setup-decisions.md#a1-is-the-folder-a-git-repository-with-at-least-one-commit))
Kyosei records the code repository's first commit as its identity, so a store can never be
attached to the wrong repository later, and workers need git to get their own worktrees.

**Which agent harness?**
([A2](setup-decisions.md#a2-which-agent-harness)) Kyosei's rules are enforced by
Claude Code hooks. On another harness the commands work but nothing enforces the rules.

**Is there tracking to bring across?**
([A3](setup-decisions.md#a3-is-there-existing-tracking-to-bring-across)) There is no
automatic import. The orchestrator reads your TODO file, notes or issues, and files records
for the work that is still live; the rest stays where it is. An imported backlog would
arrive without criteria or touches, which are what make a record useful.

## What `kyo init` does

**Bare `kyo init` in a folder with no store writes nothing.** It exits nonzero and names
three ways to start: `kyo onboard` (recommended), `kyo init --defaults` with the store path
and prefix it would use, and `kyo init --in-repo`. It prints the same text on a terminal
and off one, so an agent told to "run kyo init" sees the same choice you would. Where the
store lives is the one decision that is hard to undo, so `kyo init` leaves it to you.
`kyo init --defaults` takes every default, and is what a script or CI passes.
`kyo init --explain` prints what each layout keeps in or out of the code repository, and
writes nothing. Running `kyo init` again where a store already exists behaves as before.

With a layout chosen, `kyo init` creates the store, registers the project in
`~/kyosei/projects.json`, and prints what it did. It ends with the items
`kyo onboard --check` reads as missing or behind, each with its fix, then `kyo onboard`.
It also gives the project's CLAUDE.md a short marked section saying this project uses
Kyosei and setup is incomplete, which names `kyo onboard --check` as the next step. A later
session reads it even if nothing else was installed. On the plugin, `kyo harness install
claude-code` removes it once it has enabled the plugin; on the copies, `kyo install-hooks
--copies` (or `kyo install-skill --copies`) replaces it with the usual section. What else `kyo init` does depends on the folder.

**An empty folder, with no git repository yet.** `kyo init` runs `git init` and commits the
files it writes in the folder: a `.gitignore` entry, the `.kyosei` pointer to the store,
and the CLAUDE.md section. Then it records that first commit as the project's identity.
`--no-git-init` skips all of this.

**An existing project with commits.** `kyo init` records the repository's first commit as
the identity, and writes the `.kyosei` pointer, the `.gitignore` entry and the CLAUDE.md
section without committing them. The `.kyosei` pointer may stay untracked: a worker's
worktree holds only committed files, and `kyo` in a worktree that lacks the pointer reads
the main checkout's instead (KYO-580). Commit it if you prefer every clone to carry it.

**A repository with no commit yet.** It may hold staged work of yours, so `kyo init` commits
nothing and records no identity. Make your first commit; the next `kyo doctor` or
session-start brief records the identity, with one line saying so (KYO-559). No
`--adopt` is needed: that flag is for a store whose recorded identity differs.

## Where the store lives

([B1](setup-decisions.md#b1-where-does-the-store-live)) This is the decision that is hard
to undo, so decide it before you have many records.

**Split (the default).** The store is its own git repository at `~/kyosei/<project>/`. The
code repository carries only the `.kyosei` pointer. The reason is privacy: the store holds
verbatim text (your ideas, record descriptions, logs and stack traces captured as
evidence, shift notes, the full brief of every dispatch). Pushing the code would publish
all of it if it lived there. Kept apart, it is private unless you choose otherwise.
`--store DIR` puts one project's store somewhere else, and `$KYOSEI_HOME` moves `~/kyosei`
for every project.

**In the repository (`--in-repo`).** `kyosei/` and `ideas/` sit beside the code and are
committed with it. Pick this when the records are not sensitive and you want them reviewed
in the same pull request as the code. A hook refuses a `git push` that would publish the
store until someone accepts it once. Moving an in-repo store out later is possible, but
history already pushed stays published, and `kyo redact` cannot reach a pushed commit.

**A remote for the store?**
([B2](setup-decisions.md#b2-should-the-store-repository-have-a-remote)) None by
default, so a split store lives on one disk. A private remote is a backup. Any remote is a
publication.

**The record id prefix.** ([B3](setup-decisions.md#b3-what-is-the-record-id-prefix))
Derived from the folder name unless you choose one. Choose with care: ids end up in commit
messages and code comments for the life of the project, and the prefix cannot change once
records cite it.

**The trunk branch.** ([B4](setup-decisions.md#b4-which-branch-is-trunk)) Kyosei
asks git: `origin/HEAD`, then `init.defaultBranch`, then `main`. Set `trunk:` in
`kyosei/config.yml` only if the project merges somewhere else, such as `develop`.

## Wiring it into Claude Code

**The plugin.** The order is: install `kyo`, run `kyo harness install claude-code` once
on the machine, then run the same command in each project; setup runs it in the project for
you, and a first run there does the machine's part as well. The first run points
`~/kyosei/harness/claude-code` at the plugin inside the installed package and registers
that path with Claude Code; later upgrades of `kyo` reach the next session through it. Run
in a Kyosei project, it also enables the plugin for you in that project only (local scope,
in `.claude/settings.local.json`), adding that file to the project's `.gitignore` first if
git does not already ignore it. A project that still has the copied hooks described below
is left without the plugin, because every hook would then run twice; the command names
`kyo harness migrate` as the step that replaces the copies. Without `claude` on the PATH it
prints the `claude` commands to run instead. [Install and upgrading](../reference/topics/install.md)
has the detail.

**The copies.** Before the plugin, `kyo install-hooks` and `kyo install-skill` copied the
hooks and the skill into the project's `.claude/` directory, and they remain a supported mode
for an operator without plugins or a cloud session that cannot load one. In a project that
already has the copies, both commands work as before. In any other project they write nothing
unless given `--copies`, and name `kyo harness install claude-code` instead. To move a
project from the copies to the plugin, run `kyo harness migrate` there: it enables the
plugin, removes each copy that is still unedited along with the CLAUDE.md section, and names
whatever it left in place. It commits nothing.

- **The hooks and skill** ([C1](setup-decisions.md#c1-install-the-hooks-and-skill-the-plugin-or-the-copies)):
  `kyo harness install claude-code` (the plugin, the default) or `kyo install-hooks --copies`,
  with your consent. [Sessions and hooks](03-sessions-and-hooks.md) lists what they do.
- **The skill and the procedures**
  ([C2](setup-decisions.md#c2-install-the-skill-and-procedures)): always installed.
  The orchestrator's operating guide, the `kyosei` skill, is part of the plugin; on the
  copies, `kyo install-hooks --copies` (or `kyo install-skill --copies` without hooks)
  writes it into `.claude/skills/kyosei/`. `kyo init` writes the standing rules to
  `kyosei/procedures.md`.
- **A section in CLAUDE.md**
  ([C3](setup-decisions.md#c3-point-the-projects-claudemd-at-kyosei)):
  the skill loads when the session-start brief names a Kyosei project, so the plugin
  writes nothing into CLAUDE.md, and `kyo harness install claude-code` removes the
  setup-incomplete section `kyo init` wrote once it has enabled the plugin. On the copies, `kyo install-skill` writes a short marked section saying the
  project uses Kyosei, for a session where no session-start hook runs. Nothing outside the
  markers is touched.

**After an upgrade.** On the plugin, the hooks and the skill come from the installed
`kyo`, so upgrading it updates them, and the one command left to run in each project is
`kyo install-procedures`, since the standing rules are a copy on every route. On the copies,
rerun `kyo install-hooks`, `kyo install-skill` and `kyo install-procedures`, since copies do
not update themselves. `kyo version` names the commands for the project it runs in.

The second round of questions, after `kyo init`, is about how work is dispatched and what
the project contains. Those are the next two pages.

## The readback, and checking an old install

Setup ends with a readback: the store's path and layout, its remote, the prefix, what is
installed, the dispatch settings, the declared scopes and documents, the decisions you
declined or deferred, the machine layout, and the result of `kyo doctor`. It ends by telling you the next step: write your first idea
as its own file in `ideas/`, or ask the orchestrator to file your first record.

The same checklist works as an audit. `kyo onboard --check` reports, for a project set up
earlier, which items are missing, which installed copies are behind the build you are
running, and which decisions were never made. A decision you declined or deferred (no
store remote, no worktree setup until there is code) is written under `onboarding:` in
`kyosei/config.yml` during setup, and the check reports it as declined or deferred, with
your reason and no fix, so the next agent does not ask you again. It changes nothing; the
orchestrator offers each fix. `kyo onboard --check --all` runs it across every project on the machine.

Next: [Dispatching work](05-dispatching.md).
