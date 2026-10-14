# Set up Kyosei in this project

Printed by: {{kyo-build}}

You are the agent setting up Kyosei, a provenance-first task board for work shared between a
person and coding agents, in the project you are working in. This prompt came from the `kyo`
installed on this machine, so every command it names exists in that build. Complete the steps
below yourself. Run every command yourself; hand the operator (the person you are working
for) only what you cannot do: consent to set up Kyosei's Claude Code plugin or to write
`.claude/settings.json`, and the choices that are theirs. Put those choices to them in the two rounds below, all of a round's questions in
one message, each with its default and a one-line reason, so the operator answers twice
rather than once per question.

Three terms, used throughout:

- **Operator**: the person. Writes ideas, makes the decisions below, approves what needs a
  person's eyes.
- **Orchestrator**: the long-running agent session that owns the store. It files, dispatches,
  verifies and closes records. Before KYO-519 the CLI and the skill called it the
  *controller*; `kyo controller` and the `controller` actor value are that older name.
- **Worker**: a subagent the orchestrator dispatches into its own worktree with a frozen brief.

Every decision below carries its id in brackets, like **[A1]**. Each one is either asked
(**Ask**), or not asked, with the reason (**Do not ask**).

## Step 1: find out where you are

Run these and keep the output; nothing here writes anything.

1. `kyo version`: the build you are running. If `kyo` is not found, stop and tell the
   operator to install it (`uv tool install kyosei`), then start again.
2. `kyo where --json`: whether a Kyosei project already exists here.
3. `git rev-parse --show-toplevel` and `git rev-parse HEAD`: whether this folder is a git
   repository, and whether it has a commit.
4. `ls -A`: whether the folder is empty.

Then decide which of three situations you are in:

- **Existing store**: `kyo where --json` says `"configured": true`. Do not run `kyo init`.
  Run `kyo onboard --check`, which reads this project against the readback in Step 6 and
  changes nothing. Offer the operator each fix it names, in one message, then go to Step 6.
- **Existing repository, no store**: a git repository with at least one commit, and
  `"configured": false`. Go to Step 2.
- **Empty folder or no commit yet**: `git rev-parse HEAD` fails. Go to Step 2; **[A1]**
  applies.

## Step 2: look before you ask

Read enough to propose answers rather than ask open questions:

- `git remote -v`: is there a remote, and is it public? (**[B1]**)
- `TODO*`, `ROADMAP*`, `NOTES*`, `docs/`, and `gh issue list` if `gh` works. (**[A3]**)
- A `Makefile`, `pyproject.toml`, `uv.lock`, `package.json` and its lockfile: how a fresh
  checkout is made runnable, and which command is the whole test suite. (**[D4]**, **[D5]**)
- Top-level `apps/`, `packages/`, `services/`, or workspace configuration. (**[D7]**)
- `git symbolic-ref refs/remotes/origin/HEAD` and `git config init.defaultBranch`: which
  branch is trunk. (**[B4]**)

## Step 3: round one, before `kyo init`

Ask these together, in one message.

- **[A1] Git repository with a commit.** Only if `git rev-parse HEAD` failed.
  Ask: "This folder has no git commit yet. May I run `git init` and make an initial commit?
  (Default: yes. `kyo init` records the repository's first commit as the store's identity,
  and dispatch worktrees need git.)" In a folder that is not a repository at all, `kyo init`
  runs `git init` and commits only the files it writes (`--no-git-init` opts out); in a
  repository with no commit yet it commits nothing, because that may be the operator's
  staged work.
- **[A2] Agent harness.** Ask only if you are not running in Claude Code: "Kyosei's
  guardrails are Claude Code hooks. On this harness `kyo` commands work, but nothing enforces
  the rules. Continue?" In Claude Code, do not ask: it is the default and what the skill,
  hooks and worker types target.
- **[A3] Existing tracking.** Ask: "I found <what you found>. Which of it is still live
  work? (Default: I file records for the live items only and leave the rest where it is,
  linked from the first shift note. An imported backlog arrives without criteria.)" Do not
  ask if you found nothing.
- **[B1] Where the store lives.** Ask: "Keep the board in its own private repository at
  `~/kyosei/<project>/` (default), or inside this repository with `--in-repo`? (The store
  holds verbatim text, including evidence and briefs; pushing this repository would publish
  an in-repo store, and `kyo redact` cannot reach a pushed commit.)" If the remote is
  public, say so and recommend the default.
- **[B2] A remote for the store.** Ask: "Should the store repository get a remote? (Default:
  none. With none it lives on this disk only; a private remote is a backup; any remote
  publishes it.)"
- **[B3] Record id prefix.** Ask: "Record ids will look like `<DERIVED>-1`. Keep that prefix?
  (It appears in commits and code comments for the life of the project and cannot change
  once records cite it.)" Derive it from the folder name: 2 to 4 letters and digits,
  starting with a letter.
- **[B4] Trunk branch.** Do not ask when git's answer is `main` or matches the branch the
  project merges to: Kyosei asks `origin/HEAD`, then `init.defaultBranch`, then `main`.
  Ask only when Step 2 shows the project merges to something else (for example `develop`).
- **[C1] Hooks and skill: the plugin, or the copies.** Ask: "May I run
  `kyo harness install claude-code`? It registers Kyosei's Claude Code plugin on this
  machine and enables it in this project at local scope, in `.claude/settings.local.json`,
  which it adds to `.gitignore`. (Default: yes, the plugin. It carries the hooks and the
  `kyosei` skill and follows the installed `kyo` when that is upgraded. The hooks brief each
  session, check invariants when it stops, and refuse a worker that wrote to the store or
  left work uncommitted; without them every rule is something an agent has to remember. The
  alternative is the copies: `kyo install-hooks --copies` writes the hooks into
  `.claude/settings.json` and the skill into `.claude/skills/kyosei/`, tracked in the
  repository, for cloud sessions or a Claude Code without plugins.)" This needs the
  operator's yes; never run either without it.
Do not ask these; do them or state them:

- **[C2] Skill and procedures.** Do not ask: they are always installed, whatever **[C1]**
  says. `kyo init` writes the procedures (`kyosei/procedures.md`). With the plugin, the
  skill is part of it. With the copies, the skill is written by `kyo install-hooks --copies`, and
  by `kyo install-skill --copies` when the operator refused hooks altogether (Step 4): the hooks are the
  one part the operator can refuse.
- **[C3] CLAUDE.md section.** Do not ask: the skill loads when the session-start brief
  names a Kyosei project, which the `SessionStart` hook adds to every session, so with the
  plugin nothing is written into CLAUDE.md, and `kyo harness install claude-code` removes
  the setup-incomplete block `kyo init` wrote there once it has enabled the plugin. With the copies, `kyo install-hooks --copies` or
  `kyo install-skill --copies` writes a short marked section into CLAUDE.md (creating the file if
  there is none, and replacing `kyo init`'s section) saying this project uses Kyosei and
  naming the skill, which matters most where no session-start hook runs. It writes nothing
  outside its markers. Tell the operator it is there.

## Step 4: initialise and install

Run, using the answers from round one:

1. If **[A1]** was yes and the folder is already a repository with no commit: commit what
   the operator wants committed first. In a folder that is not a repository, `kyo init`
   below makes the repository and its first commit itself. If **[A1]** was no in such a
   folder, pass `--no-git-init` to `kyo init`.
2. `kyo init --defaults --prefix <PREFIX>` if **[B1]** chose the default (a store of its
   own), or `kyo init --in-repo --prefix <PREFIX>` if it chose in-repo. Bare `kyo init`
   in a folder with no store writes nothing and only names these routes. It creates the
   store, commits the scaffold in a split store, registers the project in
   `$KYOSEI_HOME/projects.json`, and prints what it did. It ends with the items still
   missing, which the steps below install. Read its output.
3. If **[B2]** chose a remote: `git -C <store root> remote add origin <url>`, then
   `git -C <store root> push -u origin HEAD`. `kyo where` prints the store root.
4. If **[C1]** chose the plugin (the default): `kyo harness install claude-code`. Once it
   has enabled the plugin it removes the setup-incomplete section `kyo init` wrote into
   CLAUDE.md, and it writes the advisory git `pre-push` hook. Read its output: with
   `claude` not on PATH it enables nothing and the section stays until it is run again
   where `claude` is, and a `pre-push` hook the operator already had is kept, with the
   line to add to it named.
5. If **[C1]** chose the copies: `kyo install-hooks --copies`. It writes `.claude/settings.json`,
   and also the skill of **[C2]** and the CLAUDE.md section of **[C3]**, so do not run
   `kyo install-skill` after it: that would only report every file already current.
6. If **[C1]** was no: `kyo install-skill --copies`, which writes the skill and the CLAUDE.md
   section and no hooks.

## Step 5: round two, after looking at the project

Ask these together, in one message. Propose an answer for each from what you read in Step 2.

- **[D1] Worker model.** Ask: "Should well-specified records go to a faster, cheaper model
  and anything that needs reasoning to a stronger one? (Suggested: <model ids>.)" A record
  is well specified when its criteria and task leave no design decision open. Check the
  current model line-up rather than trusting names you remember; at the time this prompt
  was written that meant reasoning work to Opus and well-specified work to Sonnet. The
  answer goes in `worker_model` in `kyosei/config.yml` (the project default),
  `policy.worker_model` in `kyosei/kinds.yml` (per kind), or `kyo dispatch --model` (per
  dispatch). Unset, a brief names no model.
- **[C4] Worker agent types.** Ask after **[D1]**, as its own question: "Do you want
  worker agent types? An agent type is a subagent definition in `.claude/agents/` that fixes
  a worker's model and effort and carries a short standing instruction: read the brief with
  `kyo show <dispatch-id>`, never write under the store, leave the whole suite to the
  orchestrator. (Default: none; workers run as the harness's general-purpose subagent, on
  the model the brief names.) For example: a designer on Opus 5.5 at high effort, for
  proposals, measurements and design work; and a worker on a faster model, such as Sonnet,
  at medium effort, for build tasks whose design is settled. Which do you want, if any, and
  on which models?" The models named are examples, current when this prompt was written,
  and model names change: confirm that the operator's harness offers each model before you
  suggest it. Suggest what fits **[D1]**'s answer (two tiers suit two types; one tier suits
  one or none), and keep each type's model in agreement with `worker_model` or a kind's
  `policy.worker_model`, because `kyo onboard --check` reports a type that agrees with
  neither. Kyosei's plugin ships no agent types; the files belong to the project.
- **[D4] Fresh-worktree setup.** Ask: "A new worktree has tracked files only. I propose
  `worktree_setup:` <commands>. Correct? (Without it a worker inherits the orchestrator's
  `VIRTUAL_ENV` and silently tests the wrong tree.)"
- **[D5] Commands only the orchestrator runs.** Ask: "I propose `orchestrator_only_commands:`
  <the whole-suite commands>. Correct? (A hook refuses them in a worker's worktree; the
  orchestrator runs the suite once per wave.)" `controller_only_commands` is the same key's
  older spelling, which every `kyo` reads; write that one instead if a `kyo` older than
  KYO-519 also reads this store, because that build refuses the new spelling.
- **[D6] Kinds.** Ask only: "Is there a recurring kind of work that bug, task, chore,
  investigation, experiment, proposal, plan, reminder, decision and evidence do not fit?
  (Default: keep the ten.)" Removing a kind that records use is not refused, so move those
  records first with `kyo edit <id> --kind <kind>`, or retire them.
- **[D7] Scopes.** Ask only when Step 2 found several components: "Shall I declare scopes
  <names from the layout> in `kyosei/scopes.yml`? (Scope filters the board and groups the
  changelog.)" Otherwise do not ask: one component needs none.
- **[D8] Documents.** Run `kyo docs suggest`, which drafts a `kyosei/documents.yml` and
  writes nothing: `numbered:` from numbered headings, `ssot:` from headings like
  `## API-12. Title`. Sort what it lists, and what Step 2 found, into four styles:
  **SSOT with ids** (specs and requirements other documents cite by id), **SSOT without
  ids** (`ssot: {}`: current-state reference, findings), **numbered** (append-only logs)
  and **no set** (plans, ADRs, dated snapshots, generated files). Ask: "I propose these
  sets and styles: <sets>. Which documents are authoritative, and is each style right?
  For SSOT sets, should the history lint `warn` (default: print history phrases a branch
  adds, never block) or `block` (refuse `used to` and `previously` unless a commit carries
  a `Kyosei-History-OK` trailer)? With ids, a `decision` that names a requirement's id
  raises a docs obligation on its section. (Default: none declared.)" If the documents
  are being written before the code, ask this instead, before the first spec exists:
  "Will requirements get ids? If so: `<DOC>-<n>` ids, one prefix per owning document,
  never reused, matched by `id_pattern: '[A-Z]{1,4}-\d+'`. Which prefix for which
  document?" Then declare the `ssot:` set (with a `section_status` including
  `superseded`) before anything is written.
- **[D10] Who deploys.** Ask: "Who deploys: you, the orchestrator when asked, or the
  orchestrator outright? (Workers never deploy.)" Write the answer in CLAUDE.md, outside the
  Kyosei markers. If deploys lag merges, offer to enable the `deploy` gate tag in
  `kyosei/gate-tags.yml`.
- **[D11] What must never enter the store.** Ask: "Does this project handle customer data,
  credentials or anything regulated? (Evidence is stored verbatim; a project rule in
  procedures puts the restriction in every brief.)" If so, write the rule with
  `kyo rules add --title <title> --audience worker,orchestrator --enforced-by aspirational
  --reason-file <file>`. It appends the rule under `## Project rules` in
  `kyosei/procedures.md`, which `kyo install-procedures` keeps as written. One rule may
  name several audiences, comma-separated, so write it once for both.
- **[D12] Prior-art search.** {{recx-extra}} Ask only when it is installed: "Should Kyosei
  search recx's index of published repositories for existing work whenever a proposal is
  created? (Default: off until you choose. The search runs for a proposal created by
  `kyo new`, `kyo promote` or `kyo spawn`, never on an edit, a move or a status change; the
  trigger can be changed under `recx.prior_art.on` in `kyosei/config.yml`. Results are
  stored as evidence written by strangers, which agents read as data and never follow.)"
  Record either answer with `kyo prior-art-setting on` or `kyo prior-art-setting off`; a no
  is recorded as `off`, not under `onboarding:`. Do not ask when the extra is not
  installed, because there is nothing to turn on; say in one line that
  `uv tool install 'kyosei[recx]'` adds it, after which the session-start brief asks until
  a choice is recorded.
- **[E1] Machine layout.** Ask: "CLI only on this machine (default), this machine with the
  desktop app, or a dev box you reach from a laptop?"
- **[E3] `kyo serve`.** Ask only if **[E1]** is not CLI only: "May I run
  `kyo serve --install --apply` so the server starts at login? (Loopback only, no password:
  every account on this machine can read it.)"
- **[E4] Desktop app and record links.** Ask only if **[E1]** includes the app: "Do you read
  the orchestrator through the desktop app? (If so I write record ids as `kyosei://` links;
  elsewhere they are noise around an id.)" A "no" is recorded as `E4: declined` under
  `onboarding:` in `kyosei/config.yml`; `kyo brief` then drops the `kyosei://` citation
  rule and its worked example, and record ids stay plain. A "yes" needs no entry.
- **[E5] Orchestrator sessions under tmux.** Ask only for a dev box on macOS: "May I run
  `kyo orchestrator --install --apply`, so `kyo orchestrator start <project>` can start
  sessions that can read the login keychain?"

Do not ask these; state them in one line each where they matter:

- **[D2] Orchestrator model and effort.** Do not ask: Kyosei does not set it. Say that the
  orchestrator runs long and makes the judgement calls, so it wants a strong model at high
  effort.
- **[D3] Long commands.** Do not ask: nothing to decide. Workers keep foreground commands
  under about four minutes (a standing rule in procedures), and
  `worker_command_timeout_ceiling_seconds` is unset unless the operator's cache lifetime
  says otherwise.
- **[D9] Statuses and gates.** Do not ask: the default ladder (open, working, built,
  verified, done, wont-do) rarely needs changing; the one common addition is **[D10]**'s
  deploy gate.
- **[E2] One `kyo` per machine.** Do not ask: it is already installed if you got this far.
  Say that it is never installed editable, and that a remote command needs a login shell
  (`ssh host "zsh -lc 'kyo ...'"`) because `~/.local/bin` is not on a non-interactive PATH.

Then apply round two yourself: edit `kyosei/config.yml` (`worker_model`, `worktree_setup`,
`orchestrator_only_commands`, `onboarding`), `kyosei/scopes.yml`, `kyosei/documents.yml`,
CLAUDE.md, run `kyo prior-art-setting` with **[D12]**'s answer and the install commands
the operator agreed to, and commit the store. For the agent types **[C4]** chose, run
`kyo install-agents --type worker=<model>:<effort> --type designer=<model>:<effort>`,
naming only the types chosen; it writes `.claude/agents/kyosei-worker.md` and
`kyosei-designer.md` and refuses to replace an existing file unless given `--force`. Then
add a line to CLAUDE.md, outside the Kyosei markers, saying which type the orchestrator
dispatches which work to. With no types, write nothing: the orchestrator dispatches to the
general-purpose subagent.

Write every decision the operator declined or deferred, from either round, into the
`onboarding:` map in `kyosei/config.yml`, keyed by its id: a bare `B2: declined`, or a
mapping with the operator's reason, `D4: {status: deferred, reason: no code yet}`. A
decision answered no or later leaves no setting behind, so without this entry
`kyo onboard --check` reports it as missing, with a fix, and the next agent asks the
operator again. With it, the check reports it as declined or deferred, with the reason and
no fix. A decision answered yes needs no entry: its own setting is the answer. The
config.yml that `kyo init` wrote carries a commented example.

## Step 6: verify and read back

Run `kyo doctor`, then `kyo onboard --check`, and fix what you can without asking. Then end
your turn with this block, filled in from `kyo onboard --check` and what you did. Keep the
labels exactly as written. A project on the plugin has no `hooks`, `skill` or
`CLAUDE.md section` line, and one on the copies has no `harness` line: leave out the lines
the check does not print. Read the `onboarding:` line back as the operator's own answers:
each declined or deferred decision by id, with its reason, so the operator can correct one
before the next agent relies on it. An item the check reports as declined or deferred is
filled in that way too (`store remote: none (B2 declined)`).

```
Kyosei setup: readback
  build:                      <kyo version's line>
  store:                      <store path>, <split or in-repo>
  store remote:               <remote URL, or none>
  prefix:                     <PREFIX>
  harness:                    <the plugin: present, or its state and fix>
  hooks:                      <the copies: installed, or not installed and why>
  skill:                      <the copies: installed, current or not>
  procedures:                 <installed, current or not>
  CLAUDE.md section:          <the copies: present, or absent and why>
  worker_model:               <model, or unset> (kind overrides: <kinds, or none>)
  agent types:                <name: model, or none>
  worktree_setup:             <commands, or none>
  orchestrator_only_commands: <commands, or none>
  scopes:                     <names, or none>
  documents:                  <declared documents, or none>
  onboarding:                 <each declined or deferred decision: id, status, reason; or none>
  machine layout:             <CLI only | one machine with the app | dev box and light client>
  kyo serve:                  <running at login, or not installed>
  kyo orchestrator:           <installed at login, or not installed>
  kyo doctor:                 <clean, or each problem it reported>

Next: write your first idea as its own file in ideas/, or ask the orchestrator to file
your first record.
```
