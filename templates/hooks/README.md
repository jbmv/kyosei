# Kyosei's Claude Code hooks

`kyo install-hooks` (KYO-13.c1) merges — never overwrites — these hooks into a consumer
project's `.claude/settings.json`. They are the enforcement surface named in the Kyosei
charter (`docs/design/charter.md`, "Claude Code integration"): hooks are one of the two
places rules actually bind (the
other is transition contracts inside `kyo` itself) — everything else is prose, and prose decays
(Law 1: mechanism over mandate).

**The hooks themselves are the JSON fragments in `src/kyosei/defaults/hooks/`**, which ship
in the wheel and are the only copy `kyo install-hooks` reads (the same pattern as
`kyosei/kinds.yml`/`kyosei/procedures.md`'s defaults). This document explains what each one
does and why; the last section lists the fragments, and `tests/test_hook_templates_sync.py`
fails if a packaged fragment is missing from that list.

Every command the hooks below name — `kyo brief`, `kyo doctor`, `kyo sweep`, (KYO-13.c5)
`kyo check-branch`, (KYO-17) `kyo check-clean`, (KYO-60) `kyo check-push`, (KYO-298)
`kyo check-command`, (KYO-186) `kyo sessions` and (KYO-398) `kyo check-build` — is a
command of the installed CLI.

## Keeping them current

Hooks are the one thing Kyosei ships you that does *not* update with the package: `kyo` is code
and updates itself, `.claude/settings.json` is a copy of a template and never does. `kyo doctor`'s
`hook_config` check closes that gap — it reports a hook this build supersedes, and a packaged
command your settings do not run, naming `kyo install-hooks` as the remedy in both cases. It says
nothing when no settings file exists, since hooks are a convenience rather than a requirement.

Extending a hook is safe and is not reported: comparison is per `&&`-separated command, so
`kyo sweep --hook && kyo doctor --hook && something-of-your-own` counts as running every packaged
command.
`install-hooks` reads the same comparison, so it will not add a duplicate group alongside your
extended one. The missing-`kyo` guard every packaged command now begins with (its own section
below) is stripped before that comparison — it is a precondition, not a step of the recipe — so a
line reads the same way whether or not it carries one, and an extension of yours still counts.

**That per-command containment comparison covers neither ordering nor removal, and deliberately
does not.** It compares *sets* of `&&`-separated commands, so two things are invisible to it.
*Ordering*: `kyo doctor && kyo sweep && kyo html` and `kyo sweep && kyo html && kyo doctor` have
identical sets — even though KYO-31 is exactly the difference between the two (doctor first fails
`html_freshness`, `&&` short-circuits, and the `kyo html` that doctor's own message named as the
remedy never runs). *Removal*: a chain you installed that still runs a command this build has
since **retired** is a strict superset of the packaged one, so containment reads it as entirely
current while it runs a command `kyo` no longer has. KYO-215 is the live instance — `kyo html`
was retired, and every project carrying `kyo sweep && kyo html && kyo doctor` would otherwise
have failed at step two on every stop with nothing saying why. Making the comparison order-aware,
or an equality, would re-break the case it exists for: a consumer who appended a step of their
own would be told they are missing what they are plainly running.

**Both are propagated by exact-string supersession instead** — `SUPERSEDED_HOOK_COMMANDS` in
`src/kyosei/scaffold.py`, the same mechanism described under `SubagentStop` below. The old line is
listed there verbatim; `kyo doctor` reports it as superseded, and `kyo install-hooks` removes it
and installs the current line in its place. The consequence worth remembering is the flip side
of that mechanism's conservatism: a hook line you reordered or edited yourself no longer matches
the exact string, so it is left alone and nothing will tell you its order is wrong. If you
customised one of these lines, check its order against the fragment it came from.

## When `kyo` is not on this shell's PATH (KYO-316)

Every packaged command begins with the same guard:

```sh
command -v kyo >/dev/null 2>&1 || { d="${CLAUDE_PROJECT_DIR:-$PWD}"; c="$(git -C "$d" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; { [ -n "$c" ] && { [ -e "${c%/*}/kyosei/config.yml" ] || [ -e "${c%/*}/.kyosei" ]; }; } || while [ ! -e "$d/kyosei/config.yml" ] && [ ! -e "$d/.kyosei" ]; do case "$d" in */*) d="${d%/*}" ;; *) exit 0 ;; esac; done; echo 'Kyosei CLI (kyo) is not on PATH, so this hook did not run. Install it: uv tool install kyosei (or pipx install kyosei), then start a new session' >&2; exit 127; }; kyo sweep --hook && kyo doctor --hook
```

It exists because a hook's whole interface is its command string, and a session whose PATH has no
`kyo` — a teammate who cloned the repository without installing Kyosei, or a machine whose
desktop app is its only Kyosei — used to get `/bin/sh: kyo: command not found` from every one of
these hooks, which names nothing anyone can act on, while the push, suite, worker-branch and
cleanliness guards silently did not run. The guard replaces that one line with a line that says
what is wrong and the command that fixes it. Since KYO-450 that is `uv tool install kyosei` alone:
the desktop app runs the `kyo` you install and ships none of its own, so its old "Install command
line tools" action is gone, and a line installed under that older message is superseded by
today's the next time `kyo install-hooks` runs. When `kyo` *is* present it is one `command -v` and
changes nothing: the hook runs exactly as before and keeps its own exit status.

**Since KYO-591 the guard speaks only inside a Kyosei project.** A plugin's hooks fire in every
session where it is enabled, so a missing `kyo` in a directory that is not a Kyosei project is
not a fault. Before printing, the guard looks for `kyosei/config.yml` or a `.kyosei` store link
at or above `$CLAUDE_PROJECT_DIR` (the hook's working directory when that is unset), and beside
the main checkout that `git rev-parse --git-common-dir` names, which is where a dispatch worktree
of a split project finds its store link. It prints its line and exits 127 only when it finds one,
and otherwise exits 0 with no output. When `kyo` is on PATH the walk does not run: each hook
mode (`--hook`, `--session-start`, `--pre-tool-use`, `--subagent-stop`) asks `kyo`'s own project
resolution, and exits 0 silently where none resolves. `kyo check-push --pre-tool-use` is the one
exception in kind: a push from outside any project can still name a Kyosei project with
`git -C`, so it still refuses such a push, and is silent about everything else.

**It does not block, on any event, and that was measured rather than assumed.** Against Claude
Code 2.1.278, a scratch project whose hooks named a command that does not exist (driven by
`claude -p --output-format stream-json --verbose`): every event these hooks install into reported `exit_code: 127`, `outcome: "error"`, and nothing was prevented — the Bash
tool call ran, the subagent stopped, the session started and ended. All anyone saw was a
`<hook name> hook error` notice carrying **the first line of stderr**. A control run whose
`PreToolUse` hook exited 2 *did* block the tool call, so that pass is the exit-code bucket and
not a hook that never fired. The documented rule behind it is Claude Code's own: any exit code
other than 2 is a non-blocking error and the action proceeds
([hooks reference](https://code.claude.com/docs/en/hooks), "Other exit codes").

So the guard exits **127, never 2**, and the message goes to **stderr, on one line**:

- `exit 2` on `PreToolUse` blocks the tool call — and the matcher is `Bash`. Without `kyo` the
  hook cannot tell a command that publishes from an `ls`, so it would refuse *every* Bash call in
  the project rather than the few these guards are about.
- `exit 2` on `Stop` and `SubagentStop` prevents the stop and continues the conversation, every
  turn, over a condition nobody in the session can clear.
- `exit 2` on `SessionStart` and `SessionEnd` is not honoured at all, so there was no choice to
  make there.
- stdout is the wrong channel and `exit 0` hides the message: a hook that exits 0 has its stderr
  sent to the debug log alone. One line, because only the first is rendered.
- 127 rather than 1 because that is the code the shell already returned for exactly this
  condition — the behaviour is unchanged and only the text is new.

**An existing project picks this up by re-running `kyo install-hooks`.** Every command this tool
shipped without the guard is listed in `SUPERSEDED_HOOK_COMMANDS`, so `kyo doctor` reports the
unguarded line and one `install-hooks` replaces it. It has to work that way: the guard is
stripped before the containment comparison, so nothing is ever reported as "missing" and nothing
would be added beside your line — which is what keeps you from ending up running each check
twice, and is also why exact-string supersession is the only path this change travels.

The git `pre-push` hook below is the one hook `kyo install-hooks` writes that never had this
defect, and it is deliberately unchanged: it has guarded on `command -v kyo` since KYO-60 and
exits 0 in silence, because a human pushing their own repository did not ask to be told, on every
push, about a tool they have not installed.

## Installing: `kyo install-hooks`

```sh
kyo install-hooks           # merge these hooks into .claude/settings.json, idempotently
kyo install-hooks --print   # show what would be written, without writing it
```

Re-running `kyo install-hooks` is a no-op once the hooks are present: it merges into (never
replaces) the `hooks` section, and within each event, into (never replaces) that event's own
hook list — a hook group already present (same event, same commands) is not added again. Any
other content already in `.claude/settings.json` (other hooks, permissions, anything else) is
left exactly as it was.

## The seven hooks, and what each one is a mechanism for

### `SessionStart` → `kyo brief --compact --session-start`

Injects the token-efficient session briefing at the start of every session — deltas since the
last brief, in-flight dispatches (and any `terminated-no-report`), gated work with its
conditions, stale flags, ready work (`docs/design/charter.md`, "Shift continuity"). This
replaces a hand-written handoff document: the state half of a handoff is derived, not written, so there is no end-of-shift moment where state gets committed
to prose and can go stale before the next session reads it. `--compact` matters — the point is a
briefing that never forces a full-file read to answer a status question
(`docs/design/charter.md`, "User stories": "My context is the scarcest resource in the
system").

### `Stop` / `SessionEnd` → `kyo sweep --hook && kyo doctor --hook`

Runs the flag-only staleness sweep (git diff since last sweep × touches, moved-but-inverse /
moved-but-not-merged flags, aging asserted claims, doc-stale `governs:` sections, near-duplicate
criteria — `docs/design/charter.md`, "Sweep"), and then runs the tool's own invariant checks at
the end of every session.

**`--hook` is about the PRINTING (KYO-362), and about not redoing work (KYO-408).** The same
checks run, in the same order, and the exit status is the one bare `kyo doctor` would have given
over the same export. The one check whose hook form differs is `export_roundtrip`: bare `kyo doctor`
rebuilds the whole store from export every time, and the hook form re-verifies only what changed
since the last passing round-trip (`cached`, `appends-verified`, `documents-rebuilt`, or `full`
when it cannot vouch for the change -- named under `hook_forms` in `--json`; its cache is
`kyosei/.doctor-cache/`, which ignores itself). What else changes is that a
failure writes its whole report to `kyosei/doctor-report.txt` (gitignored, beside `kyosei.db`,
removed again by the first run that passes) and puts ONE line on stderr: how many problems, which
checks they came from, and where the report is. Run `kyo doctor` yourself to read those findings
in the terminal. The defect it fixes was observed in a consumer project: a round-trip check
formatted two store-sized objects into a single finding, the harness rendered all of the failing
hook's stderr, and every stop filled the whole screen. This is "closeout is a hook, not a ritual"
(`docs/design/charter.md`, "Shift continuity") — nothing about ending a session is remembered
by a person. Pair it with a shift note
(`kyo note --shift`) written continuously during the session, not authored retrospectively here —
by the time this hook fires it is too late to capture "what I suspect" as anything but a guess at
what you meant earlier.

**The order is the mechanism, not a preference (KYO-31).** This shipped as
`kyo doctor && kyo sweep && kyo html`, and any session work at all made `kyosei/html/` stale, so
`kyo doctor` failed `html_freshness` first, `&&` short-circuited, and the `kyo html` that doctor's
own message named as the remedy never ran. Every subsequent stop failed identically; it never
self-healed. Healing steps run before the verification step, or the verification prevents the
healing — a check that reports a remedy must not sit ahead of that remedy in the same `&&` chain.
Running doctor last is also better on its own merits: its verdict then covers everything the hook
just did, rather than everything it is about to undo. `kyo sweep` is the only step here that
mutates the store (one sweep run); `kyo doctor` writes nothing.

KYO-215 then retired `kyo html` out of the middle of this chain — the desktop app is the viewer
and a project without it reads with `kyo show`, `kyo list`, `kyo map` and `kyo brief`, so there
is no generated site left to go stale. The rule about order is what survives the command that
taught it.

### `SubagentStop` → `kyo check-clean --subagent-stop`, then `kyo check-branch --subagent-stop --against main`

Two checks, in order, either of which blocks the stop (not advisory).

**`--subagent-stop` is what tells them which tree to read, and it is load-bearing (KYO-128).**
A `SubagentStop` hook inherits the **orchestrator session's** cwd — the project root — never the
worktree the stopping subagent was dispatched into. Without this flag both commands resolve
upward from that cwd and audit a tree the worker never touched. That failed in both directions:
a false positive on every dispatch finishing while the orchestrator had edits in flight, and — the
one that matters — a false negative on a worker that really did stop with uncommitted work in
its own worktree, which is precisely the failure this hook exists to catch. It was accidentally
correct only for an *undeclared* dispatch, whose worktree IS the project root, i.e. for exactly
the dispatches that ignored the worktree guidance. See "How the tree is chosen" below.

1. **`kyo check-clean --subagent-stop` (KYO-17, KYO-128).** Fails if the dispatch's worktree holds uncommitted or
   unstaged changes **outside** the directories check 2 below forbids a branch to diff. This is
   the mechanism behind procedures.md's worker-audience rule "commit before your final message"
   — instruction alone measurably failed (three of seven workers in one session ended with
   1,019 combined lines unstaged, `git log main..HEAD` reading empty, despite every brief
   saying commit first).

   **The exclusion is the point, and it is derived rather than restated.** This shipped as a
   raw `git status --porcelain` until KYO-17, which made the two checks in this hook contradict
   each other in any checkout the orchestrator is also running `kyo` in: the orchestrator's own
   writes to `kyosei/export/` dirty the tree, the only remedy this check offered was to commit
   them, and committing them puts `kyosei/` diffs on the dispatch branch — which is exactly
   what check 2 fails on. The worker is separately forbidden to write there at all, so it had
   no legal move. Self-feeding, too: the `Stop` hook's own `kyo sweep` dirties `kyosei/export/`
   as part of stopping, so stopping cleanly created the condition that failed the next stop.
   One consumer's worker fired ten times against that loop and ended "needs the orchestrator or a
   human", burning roughly a third of the dispatch's token budget. `kyo check-clean` reads its
   exclusions from `contracts.GUARDED_BRANCH_DIRS` — the same constant check 2 reads its
   prohibitions from — so the two halves are consistent by construction rather than by two
   people remembering (law 3).

   **Upgrading from the raw-git version:** `kyo install-hooks` removes the superseded hook by
   exact command string and installs this one in its place (`scaffold.SUPERSEDED_HOOK_COMMANDS`).
   A hook you edited yourself no longer matches that string and is left alone — check the result
   if you customised it.

   **This check is a second net, not the durability mechanism (Law 8).** It only fires on a
   clean stop. A worker that dies mid-operation — the recorded case is an API 529
   mid-sentence, before any commit — never reaches `SubagentStop` at all, and 1,019 lines
   of finished work sat unstaged and unrecovered by any hook. The actual fix for that case is
   committing continuously as work completes, which is a worker-side discipline this hook
   reinforces at the margin, not one it can enforce for a process that never gets to run
   again. Treat "died without a report artifact" as its own loud, blocking state (`kyo
   dispatch close` per `docs/design/charter.md`, "CLI surface") — that check belongs to the
   orchestrator session picking the pieces back up, not to this hook.

   **A `git status` flag is not the same fact as a difference (KYO-128).** `git status
   --porcelain` can flag a path whose blob is identical to `HEAD` — from a stale index
   stat-cache entry, or from a commit landing between the hook's own two observations. Observed
   in the field as `M README.md` with `git diff` and `git diff HEAD` both empty and the worktree
   and `HEAD` blobs the same object. Every flagged **tracked** path is therefore re-tested with
   `git diff --quiet HEAD -- <path>`, and only a real content difference counts as work. An
   **untracked** path has no `HEAD` blob to compare against and always counts — which is the
   case this guard most exists for, since a worker that died before its first commit leaves
   output that is untracked by definition.

2. **`kyo check-branch --subagent-stop --against main` (KYO-13.c5, KYO-128, KYO-285).** Fails if the current
   branch diffs anything under `kyosei/` or `ideas/` relative to `main` — or, when this stop
   resolves to a specific running dispatch, relative to THAT dispatch's own recorded base sha
   instead (see "Which base, not main" below). This is the in-repo half of what
   `src/kyosei/defaults/procedures.md`, "Never write to the structured store directly"
   promises a worker, in the ledger's current words: "`kyo check-branch`
   (`src/kyosei/cmd_guard.py`), which the `SubagentStop` hook invokes at every worker stop",
   giving "exit 2 on a branch that diffs anything under `kyosei/` or `ideas/` relative to its
   merge-base with `main`". A worker's worktree never legitimately writes to either directory
   (no `kyosei.db` to write to; the ideas layer is the human's own surface) — a diff there is
   either a mistake or a scope violation, and this check catches it before the stop completes
   rather than at merge review.

   **Those two quotations are pinned to the ledger, not proof-read (KYO-103).** Until KYO-97
   the rule credited `kyo doctor` with the branch check — a command that opens the store,
   invokes git nowhere, and has never looked at a branch — and this file and the packaged
   `subagent-stop.json` went on quoting that sentence, false attribution included, after the
   rule itself was corrected. `tests/test_hook_templates_sync.py` now reads the rule and fails
   if either side moves alone. **It covers exactly those two sites and nothing else**: a
   quotation of the ledger anywhere else in this repo is still nobody's job to keep current.
   This is a pin on the quotations that actually went stale, not a general quote-currency
   verifier.

   **Run from the inherited cwd this check was not merely wrong, it was vacuous (KYO-128).**
   The orchestrator's checkout sits on `main`, so `branch` defaulted to `main` and the command
   diffed `main` against `main`: zero changed files, "clean", every time. Every `check-branch`
   result this hook has ever recorded for a dispatch with a declared worktree was that.

   **Which base, not main (KYO-285).** A dispatch cut from a non-`main` branch — this project's
   own UI branches carry the orchestrator's own store commits beside code — puts THAT branch's
   commits in a diff against `main`, and the check blamed a worker for commits it never made
   (raised by the KYO-284.d1 worker; KYO-283.d1 hit the same thing the same day). The fix reuses
   the resolution `--subagent-stop` already performs to pick which worktree to read: when this
   stop resolves to a specific running dispatch, the base is that dispatch's own recorded
   `worked_from_sha` — the sha `kyo dispatch` stamped at the moment it was created — via
   `git merge-base <branch> <worked_from_sha>` rather than the sha alone, so a branch that
   descends directly from it (the ordinary case) compares against exactly that sha, and one that
   doesn't still compares against their true common ancestor rather than nothing. `main` is
   retargeted only when it is the value in play, never overridden by name: the command line above
   is unchanged by this record on purpose, and a DIFFERENT `--against <ref>` named explicitly
   still wins outright, matched dispatch or not — only the fallback spelling `main` is eligible to
   be sharpened. Falls back to `main` exactly as before whenever this stop cannot be matched to a
   running dispatch, whenever the matched dispatch recorded no base sha, or whenever that sha and
   the branch share no merge-base this repository can compute.

#### How the tree is chosen

Two fixes look obvious and neither works. A consumer built both and reported back:

- **`--root <path>` in this file cannot work.** `.claude/settings.json` holds *one static
  string* shared by every dispatch; it cannot carry a per-dispatch path.
- **Asking the store "which dispatch is running" cannot work.** They observed **five**
  dispatches in state `running` at one moment. The store knows every running dispatch and its
  directory; it cannot know which of them a given stop belongs to. Auditing all five is worse
  than the bug, because a sibling mid-work almost always has uncommitted files, so every stop
  would block on somebody else's work in progress.

The ingredient that does work is the hook payload, which Claude Code delivers on stdin and which
carries `agent_id`. `--subagent-stop` reads it, finds that subagent's own spawn prompt in its
transcript, and looks in it for the **dispatch id** — a token Kyosei itself minted and writes
into every brief — matched against the running dispatches the store already knows about. Exactly
one match resolves the worktree.

That reach into Claude Code's own transcript directory is the only such coupling in this
codebase, and it is deliberate: the payload's `cwd` is the orchestrator's, the transcript
record's `cwd` and `gitBranch` fields are the orchestrator's too, the agent's `meta.json` sidecar
carries no directory, and the store cannot hold an `agent_id` because `kyo dispatch` runs before
the agent exists (Kyosei does not spawn workers, KYO-61). The prompt text is the only carrier.

**Three outcomes, never two.** Every run states which tree it read and why, passing or failing
(KYO-128.c6 — a verdict that does not name its subject is the defect being fixed):

| outcome | what it means | does it block? |
|---|---|---|
| `matched` | the stopping agent resolved to exactly one running dispatch's worktree | yes — this is the worker's own tree |
| `no-declared-worktree-in-play` | no running dispatch declared a worktree outside the project root, so the hook's cwd **is** the worker's tree | yes — the old accidental correctness, now deliberate |
| `unresolved` | declared worktrees are in play and this stop matched none of them | **no** |

**Why `unresolved` does not block.** Blocking there is the measured failure this record ends. The
paths the check would cite belong to the orchestrator's branch, which the woken worker is forbidden
to commit and forbidden to write — so it is a condition the only party woken is *powerless to
clear*. One consumer measured ~229,000 subagent tokens on a single dispatch that woke three times
after it had already reported, each time re-deriving that the modified `kyosei/export/` paths were
not its own, and over half a million tokens across one shift. The workers behaved correctly every
time; they simply cannot satisfy a condition that is not about them. Law 6 is met by naming the
outcome rather than by picking a verdict: "could not tell" is reported as itself, loudly, instead
of being rendered as either "clean" or "dirty". `check-branch` already takes this line for a
repository with no store configured — exit 0 while saying out loud that it is guarding nothing,
rather than blocking every stop in a project that never asked for one (KYO-54.c2). The remedy is
addressed to the orchestrator, who is the party that can act on it.

### `SessionStart` / `Stop` / `SessionEnd` → `kyo sessions --hook`

**KYO-186.** Records this session in the store's local `kyosei/sessions.json` — which Claude
Code sessions this project has, and which claude.ai Remote Control page each one is on — so the
desktop app's claude.ai pane can open on *this project's* orchestrator session rather than on
whatever the browser last had.

One command under three events, because each does a job the other two cannot.

- **`SessionStart`** creates the entry. A session that was on Remote Control when it began
  already carries a `bridge-session` record in its transcript's opening lines — measured on this
  project's own thirteen transcripts, where the first copy stood at line 2 to 5 in every one of
  the eleven that had any, re-emitted roughly every twenty-five lines thereafter — so this is
  where an entry usually gets its URL.
- **`Stop`** is here for exactly one reason, and it is not liveness. A session that connects to
  Remote Control *after* it began has no record in the head of its transcript and gains one only
  at the end; `Stop` is the only event that comes back and looks again. That is why the reader
  reads *both ends* of the transcript, and why this refresh is load-bearing rather than
  decorative.
- **`SessionEnd`** marks the entry ended. This is the whole reason the design beats the
  alternative KYO-186 rejected — having the app scan `~/.claude` for the same records — because
  nothing holds a transcript open and a running `claude` does not name its session on its
  command line, so a scan cannot tell a running session from a finished one. A file that only
  ever grows is a file that says everything is running.

**The file is local state, never export.** It sits beside `kyosei.db` under `kyosei/`, is
gitignored alongside it (`kyo init` writes that ignore), and is rebuilt by the next session that
runs. Nothing this hook writes reaches `kyosei/export/`, which is git-canonical. The account and
organization identifiers that sit beside the session id in the harness's own record are not read
and not stored; only the id the URL needs is.

`bridge-session` is an **undocumented** internal record in a file format that can change without
notice, so every step of the read degrades to a *named* outcome rather than a guess — the rule
`kyo dispatch`'s own reach into those files already sets (`src/kyosei/cmd_dispatch.py`) — and a
format change turns the deep link off loudly instead of pointing the pane confidently at the
wrong session. `kyo sessions` with no flag prints the file and the URL a pane would open, which
is where you look when it does.

Silent on success, and always exit 0: `SessionStart`'s stdout is injected into the session's
context, where the compact brief is the only thing that belongs; `Stop` fires every turn; and a
session that could not be *recorded* must still start, turn and end.

### `PreToolUse` (matcher `Bash`) → `kyo check-push --pre-tool-use`

**KYO-60.** Blocks an agent's `git push` from a repository that carries this project's store
and whose publication nobody has accepted yet. Exit 2, like the two checks above: the tool
call never runs, and the message reaches the agent.

The hazard it exists for is not exotic. Under the in-repo layout, the first push publishes
every idea file, record description, note, shift note, evidence artifact stored verbatim and
frozen brief the project has ever held — plus `_events.jsonl`, which has already copied prose
out of all of them. That choice is made once, at `kyo init`, often by someone who has not yet
written anything private into the store; the consequence arrives months later at a push, by
which time the decision is forgotten and the store holds a great deal more than it did. `kyo
redact` cannot help afterwards — it explicitly cannot reach committed history — so there is no
after-the-fact remedy to fall back on, which is why this blocks rather than warns.

**The message is measured, not described.** It states the counts at that moment — idea files,
records, evidence artifacts, shift notes, frozen briefs, events — from the same surface table
`kyo init`'s disclosure is generated from (`scaffold.COMMITTED_PROSE_SURFACES`), so the two can
never describe different things.

**It fires once.** `kyo check-push --acknowledge` records who and when in `kyosei/config.yml`
under `publication_acknowledged`, and this is silent afterwards. A warning on every push is
noise, and noise gets ignored — the exact failure KYO-33 was filed for. The acknowledgement is
per *project* (committed with the store), not per clone: the hazard is a property of the
project, and re-blocking every agent in every fresh clone forever is precisely the noise being
avoided. It names the actor, so a colleague who inherits it can see whose call it was.

**Matching is narrow, and the misses are the accepted cost.** `git push`, `git push -u origin
main` and a `gh pr create` that pushes implicitly are not one pattern, and no pattern catches a
`make release` that pushes three layers down. On a *blocking* hook, matching broadly is the
dangerous direction — a blocking hook that fires on commands publishing nothing gets trained
away. So: an unambiguous `git` invocation whose subcommand is `push` (leading global options
and all), plus `gh pr create` and `gh repo create`, and nothing else. Everything else exits 0
without a word, which matters because this hook sees *every* Bash call the agent makes.

**Silent under the split layout**, which is `kyo init`'s default since KYO-56 — the code
repository holds no store to publish. Not silent for a project that chose split and still
tracks the old store copy in the code repository: that is the case that needs this most.

### `PreToolUse` (matcher `Bash`) → `kyo check-command --pre-tool-use`

**KYO-298.** Refuses a command this project has declared to be *the whole suite* when a
dispatched worker is about to run it inside its own worktree and that dispatch is still
running. Exit 2, same mechanism as the check above, shipped in the same fragment beside it —
as a second matcher group rather than a second entry in the first one, so that a consumer who
already has `check-push` installed gains exactly this and not a duplicate of both.

**Nothing is refused until the project declares something.** Which command line is this
project's suite is a fact only this project holds — `make test`, `npm run check`, `cargo test
--all`, or nothing at all — so it is declared in `kyosei/config.yml` as
`controller_only_commands` and never sniffed, on exactly the reasoning `worktree_setup` is
declared. A project that declares none gets no refusals, opens no database, and pays a few
milliseconds of YAML per Bash call. Declaring the key is the whole of opting in; deleting it is
the whole of opting out.

**Why it exists, measured.** The standing rule "run the tests that cover your change, not the
whole suite" was in `kyosei/procedures.md` and in every brief of one wave of five
workers. Two of them ran the whole suite anyway — because another line of the *same brief*, this project's
own `worktree_setup`, told them to. Each of those runs was ten to seventeen minutes of a worker
waiting, each was followed by a full-context re-write (172,733 and 325,752 tokens, 28% of all
the prompt cache that wave's workers wrote) because the harness's cache expired while they sat
there, and both of the day's desktop failures were those runs contending with the orchestrator's
own suite for the display. A rule that loses to a contradiction inside its own brief is not
enforced by restating it more firmly.

**What "inside a running dispatch's worktree" means, and both routes it takes.** A worker's
Bash cwd usually starts as the *orchestrator's* checkout, so the payload's `cwd` alone would have
cleared every real case: what a worker types is `cd "<worktree>" && make test`. So the segments
are walked in order with a running cwd that moves on each `cd`, and the declared worktrees come
from the dispatches whose state is `running` — a dispatch that has reported has left that set,
so the same command in its worktree afterwards is allowed with no second check.

**Matching is on the invoked command, never a substring.** Tokens, from position zero after any
`FOO=1` env prefix, compared as a prefix: `make test LOG=/tmp/t.log` matches `make test`, while
`make test-data` and `echo "make test"` do not. What it deliberately does **not** follow is
written down rather than discovered later — `pushd`, `cd -`, a `cd` through a variable or a
substitution, subshells, `git -C`/`make -C`, and a suite invoked from inside a script the
project ships. Each of those leaves the directory *unknown*, and an unknown directory never
produces a refusal: a blocking hook guessing where a command will run is how a hook gets
disabled.

**The override is one word, and it is meant to be used.** `KYOSEI_SUITE_OK=1 make test` runs.
The refusal says so in the same breath as it refuses, and asks the worker to report having used
it in its report's `disagreements[]`. A prefix rather than an exported variable on purpose: the
hook is a fresh process that never sees the shell's `export`, and — more to the point — the
prefix is in the command line the transcript records, which is what makes the override visible
to whoever reads it afterwards.

**Its companion measurement.** `kyo show <dispatch-id>` and `kyo worktrees --json` count
`context_rewrites` per dispatch — assistant messages that re-wrote nearly their whole context —
so whether this policy is working is read off the store per dispatch rather than from a
orchestrator's ad-hoc script.

`kyo doctor` prints a **notice** (never a failure) for each `worktree_setup` entry that is
itself a declared orchestrator-only command: the contradiction above, caught where it can be
fixed.

### `UserPromptSubmit` → `kyo check-build --hook`

KYO-398. Every project on a machine runs its one installed `kyo` (KYO-335; the uv tool since
KYO-443 D4 and KYO-450), so an upgrade — `uv tool upgrade kyosei`, or a re-snapshot from a
checkout — replaces the tool under every project at once, while their sessions keep running, and
without any of them having asked. The session-start brief catches the change between
sessions: it records its build, compares it with the build the project's previous session-start
brief recorded, and opens with a `BUILD CHANGED` notice naming both builds and the records the new
build carries that the old one did not. This hook catches the upgrade that lands *mid*-session,
which no brief sees.

On each prompt it compares the build of the `kyo` on PATH with the build this session's opening
brief recorded, and prints one line — both builds and the `kyo changelog --since` that lists what
changed — only when they differ. It prints nothing and exits 0 when they match, when the checkout
has no project, and when there is no opening brief to compare with (`UserPromptSubmit`'s stdout is
added to the session's context, so a line on every unchanged prompt would be paid for on every
turn). It runs on every prompt, so it opens the database read-only for one query and reads the
build stamp; it never rebuilds, exports or writes.

### The other half: a git `pre-push` hook, for the human

`kyo install-hooks` also writes `.git/hooks/pre-push`, which runs `kyo check-push --advisory`.
Same message, **exit 0** — it warns and never blocks, and `git push --no-verify` skips it.

The asymmetry is the design, not an oversight. An agent asking to push has no standing to
publish the maintainer's private thinking, so the harness hook blocks. A human pushing their
own repository owns that decision, so this one only tells them what it is.

**`.git/hooks` is not versioned, and this is its real limitation.** A fresh clone of the
repository does not have this hook; nothing carries it there, and nothing will notice it is
missing. Run `kyo install-hooks` in a new clone to get it back. `kyo doctor` deliberately does
not report its absence — that condition is true in every fresh clone forever, and a check that
fires forever is the noise this whole record exists to avoid. The blocking half above is the
one that travels, because `.claude/settings.json` is committed.

An existing `pre-push` hook that is not ours is never overwritten: `kyo install-hooks` reports
it and leaves it alone. Add `kyo check-push --advisory || true` to it yourself if you want the
warning.

## The packaged fragments

All in `src/kyosei/defaults/hooks/`:

- `session-start.json` — `SessionStart` → `kyo brief --compact --session-start`
- `stop.json` — `Stop` and `SessionEnd` → `kyo sweep --hook`, then `kyo doctor --hook`
- `subagent-stop.json` — `SubagentStop` → `kyo check-clean --subagent-stop`, then `kyo check-branch --subagent-stop --against main`
- `sessions.json` — `SessionStart`, `Stop` and `SessionEnd` → `kyo sessions --hook`
- `pre-tool-use.json` — `PreToolUse` (matcher `Bash`) → `kyo check-push --pre-tool-use`; `PreToolUse` again, in a second group of the same file → `kyo check-command --pre-tool-use`
- `prompt-submit.json` — `UserPromptSubmit` → `kyo check-build --hook`

Each file is a standalone JSON fragment shaped like one event's value under `.claude/
settings.json`'s `"hooks"` key. Its `comment` fields are for a reader of the fragment and are
stripped before anything reaches a consumer's settings. `kyo install-hooks` (see above) is the
supported way to apply them; hand-merging one of these files into `.claude/settings.json` yourself works too (the
shape is exactly what the command itself writes), the same "merge the `hooks` arrays, never
replace the file wholesale" rule either way.
