# Setup decisions

This is the one list both onboarding documents are built from: the human guide explains
each decision, and the agent setup prompt asks it. The prompt ships inside the wheel
(`kyo onboard` prints it), and `tests/test_kyo_onboard.py` fails when a decision id below is
missing from it, or carried with neither a question nor a skip with its reason, so a
decision added here must be added there too.

Terms used throughout:

- **Operator**: the human. Writes ideas, makes the decisions below, approves what needs a
  person's eyes.
- **Orchestrator**: the long-running agent session that owns the store. It files, dispatches,
  verifies and closes records, and edits configuration when the operator asks. Before KYO-519
  the CLI and the skill said *controller*; `kyo controller` is still accepted as an alias, and
  since KYO-524 the packaged standing rules say `audience: orchestrator` (a project ledger that
  still says `audience: controller` is read the same).
- **Worker**: a subagent the orchestrator dispatches into its own worktree with a frozen brief.

Each decision lists the default, why it is the default, what changing it later costs, who
decides, and how a setup agent can find the answer before asking.

---

## A. Before `kyo init`

### A1. Is the folder a git repository with at least one commit?

- **Default:** the setup agent runs `git init` and makes an initial commit first, if the
  operator agrees.
- **Why:** `kyo init` records the code repository's root commit as its identity, so a store
  can never be attached to the wrong repository later. With no commit, nothing is recorded and
  any later adoption is reported as unverified. Dispatch worktrees need git too.
- **Change later:** after the first commit, the next `kyo doctor` or session-start brief
  records the identity automatically (KYO-559).
- **Decides:** the operator, though there is rarely a reason to say no.
- **Detect:** `git rev-parse HEAD`.
- **Closed by KYO-507:** in a folder that is not a repository, `kyo init` runs `git init`
  and commits only the files it writes (`--no-git-init` opts out). In a repository with no
  commit it commits nothing, and the identity is filled in automatically after the first
  commit (KYO-559).

### A2. Which agent harness?

- **Default:** Claude Code. The skill, the hooks and the worker agent types all target it.
- **Why:** the guardrails are Claude Code hooks (SessionStart, Stop, SubagentStop,
  PreToolUse, UserPromptSubmit). Another harness can run `kyo` commands, but nothing would
  enforce the rules.
- **Decides:** the operator. The setup prompt should say plainly what is lost on another
  harness rather than pretend it works the same.

### A3. Is there existing tracking to bring across?

- **Default:** no automatic import. The orchestrator reads what exists (a TODO file, issues,
  notes) and files records for the work that is still live. The rest stays where it is,
  linked from the first shift note.
- **Why:** an imported backlog arrives without criteria or touches, which are what make a
  record useful. Filing only the live work keeps the board honest from day one.
- **Decides:** the operator says what counts as live.
- **Detect:** look for `TODO*`, `ROADMAP*`, `NOTES*`, `docs/`, and open issues if `gh` works.

---

## B. At `kyo init` (cheap now, costly later)

### B1. Where does the store live?

- **Default:** a store of its own at `~/kyosei/<project>/`, in its own git repository. The
  code repository carries only a `.kyosei` pointer.
- **Alternative:** `--in-repo` puts `kyosei/` and `ideas/` beside the code, committed with it.
  `--store DIR` picks another path, and `$KYOSEI_HOME` moves the `~/kyosei` part for every
  project.
- **Why the default:** the store holds verbatim text: ideas, record descriptions, evidence
  (logs, stack traces, whatever was in the terminal), shift notes and frozen briefs. Pushing
  the code repository would publish all of it. A separate store keeps it private by default.
- **When to pick in-repo:** records are not sensitive and you want them reviewed in the same
  pull request as the code.
- **Change later:** moving an in-repo store out is documented, but the history it already
  pushed stays published. `kyo redact` cannot reach a pushed commit.
- **Decides:** the operator.
- **Detect:** `git remote -v`. A public remote is a strong reason for the default.
- **Since KYO-558:** bare `kyo init` in a folder with no store does not take the default
  silently. It writes nothing, exits nonzero, and names the three routes: `kyo onboard`
  (recommended), `kyo init --defaults` with the store path and prefix it would use, and
  `kyo init --in-repo`. It prints the same text with and without a terminal, because an
  agent runs `kyo` without one. The setup prompt passes `--defaults` or `--in-repo`
  according to this decision, and scripts pass `--defaults`. `kyo init --explain` prints
  what each layout keeps in or out of the code repository, and writes nothing.

### B2. Should the store repository have a remote?

- **Default:** none. `kyo init` creates it locally and commits the scaffold.
- **Why it matters:** with no remote, the store exists on one disk only. A private remote is a
  backup; any remote is a publication.
- **Decides:** the operator.

### B3. What is the record id prefix?

- **Default:** derived from the folder name (`billing-service` gives `BILL-1`, `BILL-2`, ...).
- **Why ask:** ids appear in commit messages, code comments and design docs for the life of
  the project, and a prefix cannot be changed once records cite it.
- **Decides:** the operator. Suggest the derived one.

### B4. Which branch is trunk?

- **Default:** absent from config. Kyosei uses git's `origin/HEAD`, then `init.defaultBranch`,
  then `main`.
- **Set `trunk:`** only when the project merges to something else (for example `develop`).
  Merged-ness checks, touch drift and worktree pruning read it.

---

## C. Wiring Kyosei into the harness

### C1. Install the hooks and skill: the plugin, or the copies?

- **Default:** yes, as Kyosei's Claude Code plugin, with the operator's consent:
  `kyo harness install claude-code` registers the plugin on the machine once and enables it
  in the project at local scope, in `.claude/settings.local.json`, which it adds to
  `.gitignore`. The plugin carries the hooks and
  the skill, and loads them from the installed `kyo`, so an upgrade reaches every project
  with no further command.
- **The alternative, the copies:** `kyo install-hooks --copies` writes the hooks into
  `.claude/settings.json`, and the skill and the CLAUDE.md section of C2 and C3 into the
  repository, where they are tracked. It suits cloud sessions and a Claude Code without
  plugins, and it is the route every project used before the plugin existed. Each rerun brings the
  copies up to date after an upgrade.
- **What the hooks do** (the same under either route):

  | When | Hook runs | Why |
  |---|---|---|
  | Session start | `kyo brief` | Anchors the session: deltas, ready work, gates, the last shift note. |
  | Every prompt | `kyo check-build --hook` | One line when the installed `kyo` changed mid-session. |
  | Before a `git push` | `kyo check-push` | Refuses a push that would publish an in-repo store until acknowledged. |
  | Before a whole-suite command in a worker's worktree | `kyo check-command` | Keeps full suites with the orchestrator (see E3). |
  | A worker stops | `kyo check-clean`, `kyo check-branch` | Refuses a dirty worktree, or a worker that wrote to the store. |
  | The session stops | `kyo sweep`, then `kyo doctor --hook` | Raises staleness flags, then checks invariants. |

- **Without them:** the commands still work, but every rule above becomes something the agent
  has to remember.
- **Decides:** the operator, between the plugin, the copies and neither.
- **Checked by:** `kyo onboard --check` and `kyo doctor`. A project on the plugin reads one
  `harness` item: present; missing (the marketplace is not registered or the plugin is not
  enabled here); behind (the stable path points at another `kyo`, or is broken); copied
  (Claude Code loads a cached copy, or a copy from another path, rather than this build's
  stable path); or doubled (the plugin is enabled and the copies are still in the
  repository). Each but present names its fix. A project on the copies reads the hooks,
  skill and CLAUDE.md-section items instead. Which route a project is on is read from its
  files, never recorded: enabling the plugin in a settings file puts it on the plugin, and
  copied hooks or skill files without that put it on the copies.

### C2. Install the skill and procedures

- **Default:** always. `kyo init` writes the procedures but not the skill, and `kyo install-procedures` updates them. With the
  plugin, the skill is part of it. With the copies, `kyo install-hooks` writes the skill and
  the CLAUDE.md section of C3 as well as the hooks, so the setup agent runs
  `kyo install-hooks --copies` when C1 chose the copies and `kyo install-skill --copies`
  when the operator
  refused hooks altogether, never both (the second would only report every file already
  current). C1 is a decision; C2 and C3 are not. Each rerun updates the copies after an
  upgrade.
- **The skill** is the orchestrator's operating guide. **Procedures** (`kyosei/procedures.md`)
  are the rules every dispatch brief quotes, grouped by audience: worker, orchestrator, human.

### C3. Point the project's CLAUDE.md at Kyosei

- **Default:** with the plugin, nothing: the skill loads when the session-start brief names
  a Kyosei project, which the `SessionStart` hook adds to every session, as the packaged
  skill's description says since KYO-593. `kyo harness install claude-code` removes the
  setup-incomplete section `kyo init` wrote (below) once it has enabled the plugin. With the copies, a short section in the project's
  CLAUDE.md (created if there is none): this project uses Kyosei, the orchestrator's guide
  is the `kyosei` skill, and workers never write under the store.
- **Why the copies keep it:** it names the skill to a session where no session-start hook
  runs, such as a project that installed the skill and refused the hooks, and to a reader of
  the repository.
- **Since KYO-508:** `kyo install-skill` writes a marked section and nothing outside its
  markers, and `kyo doctor` reports it missing or stale. There is no separate opt-out: the
  section comes with the skill, so the setup agent tells the operator rather than asking.
- **Since KYO-558:** `kyo init` writes a setup-incomplete version of the same marked
  section when CLAUDE.md has none: this project uses Kyosei, setup is incomplete, and the
  next step is `kyo onboard --check`. A later session is pointed at the missing setup even
  if nothing else was installed. Since KYO-613 the section names both routes: on the
  plugin, `kyo harness install claude-code` removes it once it has enabled the plugin, and
  the setup agent does not delete it by hand; on the copies, `kyo install-hooks --copies`
  or `kyo install-skill --copies` replaces it with the usual section,
  and `kyo doctor` does not report it as drift while no skill is installed.

### C4. Worker agent types

- **Default:** none. Workers run as the harness's general-purpose subagent, and the
  orchestrator passes the model at spawn time.
- **The option:** subagent definitions in `.claude/agents/` fix a worker's model and effort,
  and carry a short standing instruction: read the brief with `kyo show <dispatch-id>`,
  never write under the store, leave the whole suite to the orchestrator. A type fixes a
  model and an effort, which are the operator's choice, so Kyosei's plugin ships none;
  they are the project's own files.
- **The question to ask**, after D1: "Do you want worker agent types? (Default: none.) For
  example, a designer on Opus 5.5 at high effort for proposals, measurements and design
  work, and a worker on a faster model at medium effort for build tasks whose design is
  settled. Which, if any, and on which models?" The models are examples as of 2026-10; the
  setup agent confirms which models the operator's harness offers before suggesting any.
- **How it relates to D1:** it is the same split, expressed in the harness instead of in
  `kyo`. A project that answers D1 with one tier needs none or one type; with two tiers,
  two types make the split hard to forget. Each type's model should agree with
  `worker_model` or a kind's `policy.worker_model`.
- **Where it lives:** `kyo install-agents --type worker=<model>[:<effort>]
  --type designer=<model>[:<effort>]` writes `.claude/agents/kyosei-worker.md` and
  `kyosei-designer.md` from packaged templates, naming only the types chosen (effort
  defaults to medium and high). It refuses to replace an existing file unless given
  `--force`. The
  setup agent also adds a line to the project's CLAUDE.md saying which type the
  orchestrator dispatches which work to; with none, it dispatches to the general-purpose
  subagent.
- **Decides:** the operator. The setup agent asks after D1 and suggests what fits its
  answer.
- **Checked by:** `kyo onboard --check` (KYO-514) reads the files back and reports an agent
  type (one whose definition sends it to `kyo show`) whose `model:` matches neither
  `worker_model` nor any kind's `policy.worker_model`. Nothing checks it at dispatch.

---

## D. Project configuration

### D1. Worker model: one tier or two?

- **Default:** none. `worker_model` is unset after `kyo init`, so the brief names no model
  and the harness uses whatever the subagent definition says.
- **The question to ask:** "Do you want well-specified records dispatched to a faster,
  cheaper model, and anything that needs reasoning to a stronger one?"
- **The rule of thumb behind it:** a record is well specified when its criteria and task
  prose leave no design decision open, and the worker only executes. Anything else (choosing
  an approach, judging a product question, styling an interface, a measurement whose method
  is open) goes to the stronger model. Writing vaguer criteria is never the fix for a record
  that needs judgement.
- **What that means as of 2026-10:** reasoning work to Opus, well-specified work to Sonnet,
  which is fast, cheap and worth using. The largest model is not a sensible default for
  workers. The setup agent checks the current model line-up rather than trusting these
  names.
- **Where it lives:**
  - `worker_model` in `kyosei/config.yml` is the project default;
  - `policy.worker_model` in `kyosei/kinds.yml` overrides it per kind;
  - `kyo dispatch --model` overrides it per dispatch.

  The model is recorded on every dispatch and printed in its brief. Kyosei does not spawn
  workers, so the harness is what actually honours it.
- **Decides:** the operator states a preference. The setup agent explains the trade-off and
  suggests one.

### D2. Orchestrator model and effort

- **Default:** whatever the operator's Claude Code session runs. Kyosei does not set it.
- **Recommendation to state:** the orchestrator runs long, reads every report, and makes the
  judgement calls, so give it a strong model at high effort.

### D3. Long commands: run detached and poll

- **This rule is for workers, not the orchestrator.** A dispatched subagent's prompt cache
  lasts about five minutes. A foreground command that blocks longer lets it expire, and the
  worker's next turn re-writes its whole context at full price. So workers background any
  command that might run longer than about four minutes and check it in short slices.
- **The orchestrator** usually runs with a one-hour cache, so the rule does not bind it in
  the same way. It does run the long suites, though, so it uses the same detached pattern for
  a different reason: the harness can kill a foreground command.
- **Where it lives:** the worker rule "Keep every foreground command under about four
  minutes" in procedures, and `worker_command_timeout_ceiling_seconds` in config (unset by
  default).
- **Decides:** nothing to decide unless the operator's plan changes the cache lifetime.

### D4. Fresh-worktree setup

- **Default:** none. `worktree_setup` is empty.
- **Why it matters most:** a new worktree has tracked files only, so no virtualenv and no
  `node_modules`. Worse, it inherits the orchestrator's `VIRTUAL_ENV`, so a worker that skips
  setup is silently testing the orchestrator's tree. Every brief prints these commands.
- **Detect:** a Makefile target, `pyproject.toml`/`uv.lock`, `package.json` with a lockfile.
- **Decides:** the setup agent proposes and the operator confirms.

### D5. Commands only the orchestrator runs

- **Default:** none. `orchestrator_only_commands` is empty. Its older spelling,
  `controller_only_commands`, is read the same; a `kyo` older than KYO-519 reads only the
  older one and refuses the newer, so a store such a build reads keeps the older spelling.
- **What it does:** lists the whole-suite commands (`make test`, `npm run check`, ...).
  The PreToolUse hook refuses them inside a running worker's worktree, naming what to run
  instead and the override.
- **Why:** workers run the tests that cover their change. The orchestrator runs the whole
  suite once, on the merged tree, after a wave. Several workers running the full suite at
  once contend for the machine.
- **Detect:** test targets in the Makefile and package.json scripts.

### D6. Kinds

- **Default:** ten kinds in `kyosei/kinds.yml`, in two groups.
- **Work kinds** carry a status; all but `plan` and `reminder` can be dispatched:
  - `bug`, `task`, `chore`: building things.
  - `investigation`: answering a question without modifying production code.
  - `experiment`: testing a hypothesis against a copy, a staging run or a dry run before a
    decision commits to it.
  - `proposal`: a design that needs the operator's approval (`kyo confirm`) before it spawns
    work.
  - `plan`: groups records. It has no criteria of its own.
  - `reminder`: a decision waiting on the operator, or a reminder tied to a date or an
    event. The brief shows its age.
- **Knowledge kinds** are current or void, never done:
  - `decision`: shapes architecture and cites its evidence. Filed directly only with the
    operator's words as evidence (`kyo new decision --evidence-file`); one that needs
    design work first is spawned from an approved proposal.
  - `evidence`: a recorded fact that other records cite.
- **Why kinds exist:** each one declares what it accepts, what it can produce, whether it
  counts toward the board, and its own policy (worker model, carried). That is what stops a
  question from being closed as if it were a build.
- **Change later:** easy to add a kind or change what one accepts. Removing a kind that
  records still use is not refused, and has these effects:
  - its records still list and show;
  - they silently stop counting toward the board;
  - `kyo new` refuses the kind;
  - `kyo doctor` reports each record as a problem at every session stop until the kind
    comes back or the records are retired.

  So re-kind the records first with `kyo edit <id> --kind <kind>` (KYO-509), or retire
  them. Nothing warns at the moment `kinds.yml` is edited; the `kind_registry` problem names
  the re-kind as its remedy afterwards.
- **Decides:** usually leave the defaults. Ask only whether the project has a recurring kind
  of work the ten don't fit.

### D7. Scopes (for a repository with several components)

- **Default:** none declared, so `--scope` is refused until `kyosei/scopes.yml` names some.
- **Why set them:** in a monorepo, scope is how you filter the board (`kyo list --scope`),
  group the changelog, and see that two records belong to different components. `kyo new`
  already suggests a scope from a record's touches.
- **Detect:** top-level `apps/`, `packages/`, `services/`, or workspace config.
- **Decides:** the operator names them, and the setup agent proposes from the layout.

### D8. Documents

- **Default:** none declared. `kyosei/documents.yml` is empty, and `kyo docs suggest` drafts
  one from the repository without writing anything.
- **What declaring documents enables:**
  - `governs:` lists that tie a doc section to the code it describes. A branch that changes
    that code raises a docs obligation the orchestrator settles before the record closes.
  - **Numbered sets** for append-only notes (`## 204. Title`). Workers write fragments and the
    orchestrator assembles them after merging, so parallel workers never collide on "the next
    number".
  - **Amend links** (`amended_by`, `superseded_by`) with `section_status`, which checks that
    those links resolve and are cited back.
  - **SSOT sets** (`ssot:`) for documents that state what is true now and are edited in
    place. A replaced section stays as a short superseded tombstone, and the history goes to
    a decision record or the commit.
- **Four styles, one per set.** Sort each document into the style that fits it:

  | style | fits | declare |
  |---|---|---|
  | SSOT with ids | specs and requirements that other documents cite (`API-12`) | `ssot:` with `id_heading`, `id_pattern`, `prefixes`, and a `section_status` that includes `superseded` |
  | SSOT without ids | current-state reference, findings | `ssot: {}` and the same `section_status` |
  | numbered | append-only logs, engineering notes | `numbered:` |
  | no set | plans, ADRs, dated snapshots, generated files | nothing |

  A set cannot be both SSOT and numbered. With ids, `kyo docs check` refuses an id cited in
  prose that no heading owns, an id owned twice or in a document its prefix does not map to,
  and a citation of a superseded requirement that does not name its replacement. Without ids,
  only the tombstone rule applies.
- **Decision obligations.** In an SSOT set with ids, a record of a kind whose policy sets
  `raises_docs_obligations: true` (the packaged `decision` does) raises a standing docs
  obligation on each section its title or description names by id, and on each `doc-refs`
  entry. `kyo brief` lists the open ones until someone updates the section or settles it
  `not-affected`. A store whose `kinds.yml` predates the key is told by `kyo doctor` and by
  the session-start brief after an upgrade, with the line to add.
- **History lint.** Every SSOT set lints the prose a branch adds for history
  (`this used to run nightly`), which belongs in a decision record or the commit message.
  `warn` (the default) prints each phrase at a worker's stop and never blocks: choose it
  while a set is new or its documents still carry history. `block` refuses `used to` and
  `previously` at the stop unless a commit carries a `Kyosei-History-OK` trailer: choose
  it once the documents are clean. `off` suits a set that quotes history on purpose, such as a changelog.
- **Two setup moments, two questions:**
  - **Existing documents.** Run `kyo docs suggest` and sort what it lists into the four
    styles. It drafts `numbered:` from numbered headings and `ssot:` (ids, prefixes and
    pattern) from headings like `## API-12. Title`, with its evidence beside each draft.
  - **Documents written before the code.** When the specs do not exist yet, ask before the
    first one is written whether requirements get ids. If they do, fix the scheme first:
    `<DOC>-<n>` ids, one prefix per owning document, ids never reused or renumbered, and an
    `id_pattern` regex that matches them (`'[A-Z]{1,4}-\d+'`). Then declare the `ssot:` set
    before writing, so the first spec is checked from its first line.
- **Who writes docs:** workers propose doc changes on their branch. The orchestrator merges
  them and assembles numbered sections, so sections never collide.
- **Decides:** the operator chooses which documents are authoritative, which style each set
  uses, the id scheme, and warn or block for the history lint. The setup agent runs
  `kyo docs suggest` and walks through its output.
- **Detect:** `kyo docs suggest`; a `docs/` or `specs/` tree with no code beside it means the
  documents are being written first.

### D9. Statuses and gates

- **Default ladder:** `open`, `working`, `built`, `verified`, `done`, plus `wont-do`. `built`
  exists because "written but not checked" is the most common state worth seeing.
- **Gates** park a record on a condition: an event, a person running something, a person
  reviewing something, or a hold.
- **Change:** rarely needed. The one common addition is below.

### D10. Who deploys?

- **Default:** workers never deploy (a worker rule). Kyosei says nothing about whether the
  orchestrator may.
- **Choices:** the operator deploys; the orchestrator deploys when asked; or the orchestrator
  owns deploys outright. State it in the project's CLAUDE.md, and add an orchestrator rule to
  procedures if it is the orchestrator's.
- **If deploys lag merges:** enable the commented-out `deploy` gate tag in
  `kyosei/gate-tags.yml`. "Merged, not live" then becomes `kyo list --gate-tag deploy`, and
  you clear the gates after each deploy.

### D11. What must never enter the store?

- **Default:** nothing is filtered. Evidence is stored verbatim by design.
- **Ask:** does this project handle customer data, credentials, or anything regulated? If so,
  write it as a project rule in procedures so every brief carries it: `kyo rules add`
  appends one under `## Project rules` in `kyosei/procedures.md`, which
  `kyo install-procedures` keeps as written. One rule may name several audiences,
  comma-separated (`worker, orchestrator`), so a restriction both follow is written once.
  `kyo redact` is the remedy after the fact.

### D12. Prior-art search

- **Default:** off until the project chooses. The search needs the optional extra
  `kyosei[recx]` (`uv tool install 'kyosei[recx]'`); without it there is nothing to decide,
  and the question is not asked.
- **The question to ask:** "Should Kyosei search recx's index of published repositories for
  existing work whenever a proposal is created?"
- **What it does:** with the answer on, a proposal created by `kyo new`, `kyo promote` or
  `kyo spawn` is searched for once, after it is written, and the results are stored as an
  evidence artifact under a derived claim. Nothing is searched on an edit, a move or a
  status change. `kyo prior-art <record-id>` searches for any record on demand, whatever
  the answer.
- **Where it lives:** `recx.prior_art` in `kyosei/config.yml`. `kyo prior-art-setting on`
  and `kyo prior-art-setting off` write its `state`, so a no is recorded as `off` rather
  than under `onboarding:`. The trigger is `recx.prior_art.on` (by default
  `proposal: [new, promote, spawn]`), edited by hand.
- **Why it is asked at all:** installing the extra may not change the build, so nothing
  else would tell an existing project that the choice exists. While the extra is installed
  and neither answer is recorded, the session-start brief prints one line naming
  `kyo prior-art-setting`.
- **Decides:** the operator. The results are text written by the authors of the
  repositories found; agents treat them as data and never follow or run them.

---

## E. The machine: where Kyosei runs and what else it starts

Only the CLI is required. Everything in this section is optional, and the macOS build is
the one that has been measured. Linux has `kyo serve --install` (a systemd user unit);
Windows has neither login service.

### E1. Which layout? *(ask this before E2 to E5)*

| Layout | What runs where | Needs |
|---|---|---|
| **CLI only** | One machine: repositories, stores, `kyo`, Claude Code in your own terminal. | Nothing below. |
| **One machine with the app** | The same, plus `kyo serve` at login and the desktop app reading through it. | E3, E4. |
| **Dev box and light client** | The dev box holds every repository and store, and runs `kyo`, `kyo serve`, and the orchestrator sessions under tmux. The laptop runs the desktop app (or a browser, or `ssh`), plus claude.ai's Remote Control to talk to a session. | E2 to E5, and a private network between the two (Tailscale). |

The store stays with whatever writes it. In the dev-box layout the laptop holds no store
and runs no orchestrator; it only views and talks to them.

### E2. One `kyo` per machine, and PATH

- **Install:** `uv tool install kyosei`. It puts `kyo` (and `kyosei`) in `~/.local/bin`.
  Every hook in every project, every terminal and the desktop app run that one executable.
  Upgrading it upgrades every project on the machine at once.
- **Never install it editable** (`-e`): every project would then track the working tree of
  a checkout, uncommitted edits included.
- **`~/kyosei/`** (`$KYOSEI_HOME`) is the machine's Kyosei home:
  - `projects.json`, the projects this machine knows, which `kyo serve` and
    `kyo orchestrator` read;
  - `settings.yml`, machine settings such as `controller_command`;
  - the split stores, one directory per project;
  - each store's `dispatch worktrees/`.
- **PATH is the usual trap.** `~/.local/bin` is on PATH in a login shell. It is not on PATH
  in a non-interactive `ssh host kyo ...`, nor in anything launchd starts without help.
  That is why:
  - remote commands run as `ssh host "zsh -lc 'kyo ...'"`;
  - orchestrator sessions start as `zsh -lc 'exec <controller_command>'`;
  - the `kyo serve` login agent records the installing shell's PATH.

### E3. `kyo serve`

- **What it is:** an HTTP server on loopback (port 47370) that serves every project in
  `projects.json` to any viewer. It serves what the CLI's `--json` reads, and allows
  exactly two writes: files under a project's `ideas/` and its session scratchpad. It
  starts no process and writes no record.
- **Locally:** `kyo serve` in a terminal for a trial, or `kyo serve --install --apply` to
  run it at login (a LaunchAgent on macOS, a systemd user unit on Linux).
- **Restart it after every upgrade** (`kyo serve --restart`). A running server keeps
  serving the build it started as, and `kyo serve --status` says when it is stale.
- **Loopback only, with no password.** Every account on the machine can reach it. On a
  shared machine, that is everyone who logs in.
- **From another machine:** Tailscale Serve proxies the tailnet to it with a certificate
  Tailscale provisions. Run `kyo serve --install --allow-host <machine>.<tailnet>.ts.net
  --apply`, then `tailscale serve --bg 47370`. Restrict access with a tailnet rule.
  **Never `tailscale funnel` this port**: that publishes every project to the internet.

### E4. The desktop app (macOS)

- **What it is:** one viewer of `kyo serve`, and dispensable; anyone can write another. It
  ships no `kyo` and no server of its own, and says so when `kyo serve` is not answering.
- **Locally:** it reads this machine's projects at `http://127.0.0.1:47370`. It adds a
  terminal pane per project and a claude tab listing that project's sessions.
- **As a light client to a dev box:** in the project switcher, **Connect to a dev box…**.
  Give it the dev box's tailnet address and an SSH host. It reads and writes through that
  server; New Project… and Start orchestrator run their commands over SSH. A remote project
  has no terminal pane, because terminals are local.
- **Record links (ask):** a `kyosei://` link opens the record in the app. It is clickable
  only on macOS with the app installed, which registers the scheme. Elsewhere it is noise
  around an id. So ask: if the operator reads the orchestrator through the app, links; if
  not, plain ids. Record a "no" as `E4: declined` under `onboarding:` in
  `kyosei/config.yml`: `kyo brief` then stops printing the `kyosei://` citation rule and
  its worked example. With no entry, the brief keeps both.

### E5. Orchestrator sessions under tmux (`kyo orchestrator`, macOS)

- **What it is:** `kyo orchestrator start <project>` starts a detached tmux session named
  after the project's prefix, running the machine's `controller_command` in the project's
  directory. `list`, `attach` and `stop` do the rest. A session keeps running when you
  disconnect.
- **Why a dedicated tmux server** (`tmux -L kyosei`), started at login by
  `kyo orchestrator --install --apply`: on macOS, Claude Code's credentials are in the login
  keychain. A process started straight from an SSH login cannot read it; a session spawned
  by a server that the desktop login started can. Over SSH, `start` refuses rather than
  start a server in the wrong context.
- **`controller_command`** in `~/kyosei/settings.yml` is a list of arguments, with
  `{prefix}` replaced by the project's prefix. The default is `claude`. To name each session
  in claude.ai and give it Chrome:
  `controller_command: ["claude", "--chrome", "--remote-control", "{prefix}"]`.
- **Attach from another machine:** `ssh -t <machine> "zsh -lc 'kyo orchestrator attach
  <PREFIX>'"`. Plain `tmux ls` over SSH shows nothing, because it reads the default server.
- **Careful:** stopping the `-L kyosei` server, or `kyo orchestrator --uninstall --apply`,
  ends every orchestrator session on the machine.
- **macOS also needs** Remote Login turned on (System Settings → General → Sharing) for
  any of the SSH paths.

---

## F. After setup: the readback

The setup prompt ends with a fixed block the operator can check. It lists:

- the store path and layout, and whether the store has a remote;
- the record id prefix;
- the harness: the plugin's state, or, for a project on the copies, the installed hooks and
  skill and the CLAUDE.md section;
- the procedures;
- `worker_model` (and any agent types), `worktree_setup` and `orchestrator_only_commands`;
- the declared scopes and documents;
- the decisions the operator declined or deferred, from the `onboarding:` map below;
- the machine layout from E1, and which of E3 to E5 are running;
- the result of `kyo doctor`.

The next step it names is: write your first idea as its own file in `ideas/`, or ask the
orchestrator to file your first record.

**Declined and deferred decisions are recorded** (KYO-560). A decision answered yes leaves
its own trace: a remote, a `worktree_setup` list, a hooks file. One answered no or later
leaves nothing, and a readback that cannot tell "never decided" from "decided no" sends the
next agent back to the operator with a question already answered. So the setup agent writes
each such answer into `kyosei/config.yml`, keyed by its id here:

```yaml
onboarding:
  B2: declined
  D4:
    status: deferred
    reason: no code yet, so no install or test command
```

A value is a bare `declined` or `deferred`, or a mapping with `status` and an optional
`reason`; any other shape is refused by name. The config.yml `kyo init` writes carries this
example, commented out. A `kyo` older than the key refuses it, so a store adopts it only
once every `kyo` that reads it has been updated.

---

## G. Auditing an existing install

The readback in F is a checklist. The same checklist, run against a project set up months
ago, is an audit. So it is one command with two uses (KYO-514): `kyo onboard` prints the
prompt that asks the questions for a new project, and `kyo onboard --check` reports, for an
existing one:

- every item in F, present or missing;
- each installed copy (skill, procedures, hooks) current or behind this build, or, for a
  project on the plugin, the plugin's state and its fix (C1);
- the decisions never made: no `worker_model`, no `worktree_setup`, no scopes in a
  repository with several components;
- the decisions declined or deferred under `onboarding:`, each reported as such with its
  reason and no fix, and an answer that a setting now contradicts (a remote added after B2
  was declined) reported as `disagrees`.

It reports and changes nothing; the orchestrator offers each fix. It reads files and asks
git, and never opens the database, so the readback's `kyo doctor` line is the result the
last `kyo doctor` (or `kyo doctor --hook`) recorded beside the database: clean or the failing
checks' names, with its age and the build that ran it. It reads as not run, naming `kyo
doctor`, only when no result is recorded. `kyo onboard --check --all` runs it across the machine's
`projects.json` and answers "which of my projects are missing what".
