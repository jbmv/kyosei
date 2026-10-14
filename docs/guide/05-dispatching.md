# Dispatching work

After this page you will know how a piece of work goes from a record to a worker and back,
and you will be able to answer the setup questions about worker models, worktree setup,
test suites and deploys.

## How a dispatch works

1. The orchestrator picks a ready record (`kyo next` lists them) and runs `kyo dispatch`.
   With `--branch` it also creates a git worktree for the worker. Either way it freezes a
   **brief**: the record's criteria, touches and claims, the worker rules from
   `kyosei/procedures.md`, and a task paragraph the orchestrator writes.
2. It starts a worker subagent in that worktree. The worker reads its brief with
   `kyo show <dispatch-id>`, does the work, commits on its branch, and writes a structured
   report: a verdict for each criterion, its commits, and anything in the brief it believes
   is wrong or disagrees with.
3. The orchestrator ingests the report with `kyo report`, reads the disagreements first,
   checks the work, records verifications with `kyo verify`, merges the branch, and moves
   the record with `kyo move`.

A brief is frozen on purpose. If the record changes while the worker runs, the worker's
instructions do not, and the report is judged against what the worker was actually told.

The orchestrator can run several dispatches at once. `kyo dispatch` refuses two whose
touches overlap, so parallel workers do not edit the same code.

## Who owns what during a wave

A *wave* is a batch of dispatches running in parallel. The split of work is the same in
every project:

- **Workers run only the tests that cover their change.** Their result is about their
  branch before anyone else's merge, so a whole-suite run there answers the wrong question,
  and several of them at once fight over the machine.
- **The orchestrator runs the whole suite once**, on the merged tree, after the wave.
- **Workers propose documentation changes on their branch.** The orchestrator merges them
  and, for documents that grow by numbered sections, assigns the numbers, so parallel
  workers never collide on "the next section". When a branch changes code that a declared
  document describes, Kyosei raises an obligation the orchestrator settles before the
  record closes. See [Kinds, scopes and documents](06-kinds-scopes-documents.md).
- **Workers never deploy.** Whether the orchestrator may is your call (below).

## The settings to choose

**Worker model: one tier or two?**
([D1](setup-decisions.md#d1-worker-model-one-tier-or-two)) A record is *well specified*
when its criteria and task leave no design decision open, and the worker only executes.
That work can go to a faster, cheaper model. Anything that needs judgment (choosing an
approach, a product question, interface design, a measurement whose method is open) goes
to a stronger one. Writing vaguer criteria is never the fix for work that needs judgment.
Unset by default. The project default is `worker_model` in `kyosei/config.yml`; a kind can
override it with `policy.worker_model` in `kyosei/kinds.yml`, and one dispatch with
`kyo dispatch --model`. Kyosei records the model and prints it in the brief, but Kyosei
does not start workers: the harness does, and it is what honours the choice.

**Worker agent types.**
([C4](setup-decisions.md#c4-worker-agent-types)) Claude Code subagent
definitions in `.claude/agents/` can fix a worker's model and effort and carry a standing
instruction. With two tiers, two agent types make the split hard to forget. Optional, and
none by default; `kyo onboard` asks, `kyo install-agents` writes the chosen ones from
packaged templates, and `kyo onboard --check` reports one whose model disagrees with
`worker_model`.

**The orchestrator's model.**
([D2](setup-decisions.md#d2-orchestrator-model-and-effort)) Kyosei does not set it.
The orchestrator runs long, reads every report and makes the judgment calls, so give it a
strong model at high effort.

**Long commands: run in the background and poll.**
([D3](setup-decisions.md#d3-long-commands-run-detached-and-poll)) This rule binds workers,
and the reason is cost. A model reuses the start of its conversation from a prompt cache,
which is cheap to read and expensive to write. Claude Code keeps a subagent's cache for
about five minutes, and every request refreshes it. A worker that waits on a ten-minute
command in the foreground makes no request for ten minutes, the cache expires, and its next
turn pays to write its whole context again. So a worker keeps each foreground command under
about four minutes, runs anything longer in the background with its output going to a
file, and checks on it every few minutes. Each check is a cheap cache read that keeps the
cache alive. The orchestrator session usually has a one-hour cache, so the rule does not
bind it the same way; it still runs the long suites in the background, because the harness
can kill a long foreground command. Nothing to decide unless your plan changes the cache
lifetime.

**Fresh-worktree setup.** ([D4](setup-decisions.md#d4-fresh-worktree-setup)) A new
worktree holds only the files git tracks: no virtual environment, no `node_modules`. Worse,
a worker inherits the orchestrator's environment, so a worker that skips setup can run the
orchestrator's tools against the orchestrator's tree and report a pass that is about the
wrong code. List the setup commands in `worktree_setup` in `kyosei/config.yml`; every brief
prints them. Empty by default; the setup agent proposes them from your Makefile or lockfile.

**Commands only the orchestrator runs.**
([D5](setup-decisions.md#d5-commands-only-the-orchestrator-runs)) List the
whole-suite commands (`make test`, `npm run check`) in `controller_only_commands`, and a
hook refuses them inside a worker's worktree, naming what to run instead and how to
override. Empty by default, which turns the check off.

**Who deploys?** ([D10](setup-decisions.md#d10-who-deploys)) Workers never do. For the
orchestrator there are three answers: you deploy; the orchestrator deploys when you ask; or
the orchestrator owns deploys. Write the answer in the project's CLAUDE.md, and if it is the
orchestrator's job, add an orchestrator rule to `kyosei/procedures.md`. If deploys lag
behind merges, the `deploy` gate tag in `kyosei/gate-tags.yml` (commented out by default)
makes "merged but not live" a list you can ask for.

Next: [Kinds, scopes and documents](06-kinds-scopes-documents.md).
