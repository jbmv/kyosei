# The Kyosei desktop app

This directory holds the Electron app that Kyosei's maintainer uses every day to read
and drive Kyosei projects. It works and is in daily use, but it has not been polished,
and it is published as it is. It is offered for building from source or adapting; anyone
who would rather write their own viewer can do that too. Kyosei does not need the app.
Every read and write the app makes goes through `kyo serve`, which comes with the CLI, so
any other client can use the same interface.

The app is developed and used on macOS. The packaging and signing code is macOS-only,
and the app has not been run on any other platform. It is published under the same
license as the rest of the repository, Apache-2.0.

## How it is used

This is the arrangement the app was built for, and the one the rest of this document
assumes.

- **One `kyo serve` per machine that holds projects.** The app is a client of the
  server on its own machine (`127.0.0.1:47370`) and, through Tailscale Serve, of other
  machines' servers. A machine with no projects of its own, such as a laptop, runs the
  app against another machine's server and starts no server itself.
- **The orchestrator runs in tmux.** `kyo controller start` starts a Claude Code session
  for the project in a detached tmux session. The terminal tab attaches to that session,
  directly on the same machine or over ssh to another one. The claude tab shows the same
  session through claude.ai Remote Control, which is where permission prompts are
  usually answered.
- **The sidebar lists are the board.** Ideas, records, the gates awaiting a person,
  shift notes, docs and dispatch activity each have a list, and every list follows the
  store as the orchestrator changes it. A row opens as a tab, and a
  `kyosei://<project>/<id>` link in the orchestrator's messages opens the record it
  names.
- **The scratchpad is the person's own notes.** A selection can be saved as a new idea
  file, and the orchestrator turns ideas into records. The app never creates a record.

## Building and running from source

You need macOS, Node.js with npm (24.x is what the app is developed with), and `git`.
To see real projects you also need the Kyosei CLI and a running `kyo serve`. tmux is
optional and only the terminal tab uses it. Run every command below from the
repository root.

```
npm --prefix apps/desktop ci
node apps/desktop/node_modules/electron/install.js
npm --prefix apps/desktop run start
```

The second command downloads the Electron binary. `npm ci` does not do this, because
the `electron` package declares no install script. `run start` builds the app and opens
it.

**Which `kyo` the app runs.** Started from inside a Kyosei checkout, the app runs that
checkout's CLI through `.venv/bin/python -m kyosei`, so an edit under `src/kyosei/`
takes effect without a rebuild. Run `make venv` once to create that environment.
Otherwise the app runs the `kyo` on your login shell's PATH (`uv tool install kyosei`).
The status bar shows which one is in use.

**The server.** The app starts no server itself. Start one with `kyo serve`, or install
it as a login service with `kyo serve --install --apply`, and register each project it
should serve with `kyo projects add <path>`. If no server answers, the window says so
and checks again every few seconds.

**A standalone app.**

```
npm --prefix apps/desktop run pack
```

This writes `apps/desktop/release/Kyosei-darwin-<arch>/Kyosei.app`. If the login keychain
holds no Developer ID Application identity, the bundle is signed ad hoc, and the pack
says so in one line. An ad-hoc bundle opens on the Mac that built it, and a copy moved
to another Mac needs its quarantine attribute removed first
(`xattr -dr com.apple.quarantine Kyosei.app`). On a machine that holds an identity, the
pack signs with it unless `KYOSEI_SIGN_IDENTITY=-` is set, which forces an ad-hoc pack.
Notarization happens only when it is requested explicitly. The environment variables
for signing and notarization are described in
[`docs/reference/topics/serve-and-desktop.md`](../../docs/reference/topics/serve-and-desktop.md).
Move the `.app` out of the repository before you run it. A bundle that is still inside
the checkout finds the checkout's CLI by walking up from itself, and uses that instead
of the `kyo` on your PATH (see "Packaging" below).

The rest of this document is the app's design record. It describes what each part does
and why, and it cites the `KYO-n` records in Kyosei's own tracker where each decision
was made.

## The design in brief

**The renderer is v3 (KYO-227, KYO-232), reworked after use (KYO-245).** One window
shell: a left nav rail, a collapsible sidebar whose content the rail selects, and one
or two columns of tabs. No modes, no pane workspace, no bottom dock, no graph, no
bubbles, no properties or outline panes — v2's renderer was deleted rather than
adapted, and the stylesheet was rewritten rather than trimmed. Everything openable is a
TAB: the scratchpad, an idea, a record, a brief, a doc, the terminal, the claude
session, the activity view, and the home page a new tab opens on. "Move to a new
window" opens a whole shell window on the same project holding that tab (KYO-246); the
claude.ai window is the one exception, and belongs to no project window.

What the records below built, and where it stands after v3 stage 2:

- the **live seams** (KYO-111): how the app invokes `kyo`, how it reads the store, how
  it reports the store's currency, and the terminal's shell. Unchanged, all of it.
- the **terminal as a window onto the orchestrator's session** (KYO-115, KYO-229,
  KYO-293, KYO-474): opening the tab lands in the project's orchestrator session on
  `kyo controller`'s own tmux server (`tmux -L kyosei`) — on this machine through a pty
  whose process is the tmux client itself, on a dev box through a pty running `ssh -t
  <host> zsh -lc 'kyo controller attach <prefix>'`. It creates no session; with none
  running it says so and offers Start orchestrator. A bare login shell is one click
  away on this machine, on a second pty of its own. It is a TAB now, opened from the
  rail.
- the **claude.ai pane** (KYO-186, KYO-190, KYO-191): a `WebContentsView` on a
  persistent partition. A TAB now, on the same terms.
- **packaging** (KYO-176): `npm run pack`, a bundle that carries no Python (KYO-450).
  See "Packaging" below.
- the **v3 read model** (KYO-231) and **`kyosei://` deep links** (KYO-230): what the
  tabs render, and how a link reaches one. See their sections below.
- the **constant window shell** (KYO-110), **records mode** (KYO-112, KYO-113) and
  **ideas mode** (KYO-114): their renderers are GONE. Their sections below are kept as
  the design record of what was built and why it was replaced — the reasoning in them
  about the store, the seams and the read-only boundary still holds; the surfaces they
  describe do not exist. Stage 3 (KYO-233..KYO-236) fills the placeholder tabs.

**The app still writes exactly two things of yours, and never your store**: the session
scratchpad and files under `ideas/`. In stage 2 even those are read-only in the app —
the compose tab is a placeholder and KYO-234 makes it an editor. See "The one write
path" below.

**The app never creates a record (KYO-439).** Turning an idea into a record means
deciding its kind, title, criteria and touches, and that is the orchestrator's act, not
the app's: the app has no basis on which to write a record. What the app offers is the scratchpad's "Promote to
idea" (KYO-252, below), which writes an idea FILE; the orchestrator promotes from there.
`kyo.ts`'s `runKyo`, the one subprocess the app starts for the CLI, refuses `kyo
promote` by name (`assertAppMayRun`), and `tests/seams.spec.ts` fails if that refusal
is removed.

## Checks

Every check runs from the **repository root**, with `npm --prefix`:

| what | command |
|---|---|
| lint + format check | `npm --prefix apps/desktop run lint` |
| typecheck | `npm --prefix apps/desktop run typecheck` |
| build | `npm --prefix apps/desktop run build` |
| the test suites (builds first) | `npm --prefix apps/desktop run smoke` |
| all three | `npm --prefix apps/desktop run check` |

First run needs `npm --prefix apps/desktop ci`.

One check is opt-in and skipped by a normal run: the pty quit soak (KYO-124), which
measures a crash *rate* rather than asserting a pass, by process exit signal over many
launches. `.ips` crash reports are useless as a count here — macOS throttles them, as
KYO-119's record establishes.

```
KYOSEI_PTY_SOAK=25 npx playwright test -g "two ptys in play"   # from apps/desktop
KYOSEI_PTY_SOAK_PTYS=4                                    # ptys per launch (default 2)
```

It is opt-in because it deliberately crashes Electron when the defect is present, and
because it takes as long as the launch count says it will.

Two kinds of suite run under the one Playwright runner:

- `tests/smoke.spec.ts` launches the real Electron app and asserts the window.
  `tests/tmux-owner.spec.ts` and `tests/docs.spec.ts` do too, each for one subject
  whose launches need an environment the smoke file's do not — see "The app owns the
  session, and never ends it" for why the tmux one carries no `KYOSEI_TMUX`.
- `tests/seams.spec.ts` launches nothing. The resolver, the argv guard, the
  read-only database read, the currency mapping, the login-shell capture and the
  earlier build's CLI cleanup are all in Electron-free modules on purpose, so they are driven directly and
  fail with a line number rather than a screenshot. `tests/claude-pane.spec.ts` is the
  same shape for the Remote Control resolution.

Both build their fixtures with the real `kyo`, in temp directories **whose paths
contain a space** — this repository's own checkout does (KYO-89), and a fixture in
a spaceless directory would let a quoting defect pass here and fail where the
project actually lives.

These are the app's *own* checks. `make test` at the repo root is the Python suite
and knows nothing about this directory — that separation is deliberate (KYO-110).

To see the app: `npm --prefix apps/desktop run start`. To build one that runs
without a checkout: `npm --prefix apps/desktop run pack` — see "Packaging" below.

### Which environment this suite was written for (KYO-183)

`npm --prefix apps/desktop run check` used to say nothing about where it ran, and
there are two places that behave differently:

- **a fresh dispatch worktree**, where everything gitignored is absent — this
  project's own `kyosei/kyosei.db` included — and where usually nothing else is
  running;
- **a full clone that has been worked in**, where that database exists and is large,
  and where the packaged app, a second Electron project and other workers' suites may
  be running at the same time.

It says so now. `the run says which checkout it ran in, and no check is allowed to
depend on it` (`tests/smoke.spec.ts`) prints the checkout the app would fall back to
and whether it carries a built store, so a run written to a file carries its own
environment. Which of the two you are in is the *only* difference between them that
this suite can see.

**No assertion here may depend on which one it is, and that is a rule rather than an
accident.** No check may depend on a file being absent merely because it is
gitignored, or on this project's own `ideas/` being small. Every launch in every spec
points the app at a `mkdtemp` fixture through `KYOSEI_PROJECT_ROOT`, and a check that
needs a particular store state builds that state itself: `a project whose database is
missing says so instead of showing a count` deletes the fixture's own database rather
than borrowing a worktree's absence.

The rule was paid for. An earlier draft of `a human changes the project from inside
the app, with no variable and no restart` asserted `data-store="unavailable"` for the
app's starting project, "whose database is gitignored and therefore absent here".
True in a worktree; false in the clone, where the file is there and the app reads it.
It passed in every dispatch worktree and failed in the full clone where the app is
actually used. That assertion is gone, and the second half of the check named above is what
stops it coming back: `launchUnpinned` is the one route by which any check here can be
about the checkout at all, and the scan holds every window between an unpinned launch
and that check opening a project of its own to asserting nothing about the store.

**So what a green in a fresh worktree does and does not cover.** It covers every
assertion both suites make — they are all made against fixtures, and none of them is
weaker in one place than the other. It does not cover *timing*. Several checks here
are load-dependent races by construction, and KYO-146's `a file opened while the
machine is busy is never written at all` provokes load on purpose and refuses to pass
without it. A quiet worktree is the easy environment for all of them, so a green there
is evidence that the suite's claims hold and weak evidence about the suite's stability
on a machine somebody is working on.

Nothing in a single run can catch a check whose result depends on timing. Repeated
runs can, and this is the recipe — it is what produced the measurements in the table
below, and it is the thing to run in a full clone with the app open before believing
a repair:

```
for i in 1 2 3 4 5; do
  npm --prefix apps/desktop run check > /tmp/desktop-$i.log 2>&1
  echo "run $i: exit=$?"
done
grep -h '✘' /tmp/desktop-*.log | sort | uniq -c
```

Never pipe a suite through `tail`: `tail`'s exit status is not the suite's, and an
interrupted run leaves no log to read.

#### The difference is also a duration, and a check can build the other side (KYO-218)

The paragraphs above read the difference between the two environments as one of
*contents*: a file is there or it is not, and an assertion about what it holds is what
goes wrong. KYO-218 was the same difference reaching a check through *duration*
instead, and no scan of this suite's source can see that shape.

`a switch detaches from the previous project's tmux session without killing it` passed
in every dispatch worktree and failed three times out of three in a worked-in
clone, reporting `data-terminal-session="kyosei-kyosei"` — the project the app had just
been switched *away* from — beside a `data-terminal-cwd` naming the project it had been
switched *to*. One pane, two projects. Nothing in the check mentions the checkout. What
made it environment-dependent is that the fallback launch project in the clone has a
store the CLI can open, so `kyo worktrees --json` about it does real work and takes long
enough for the switch to land while the pane is still opening; in a worktree the same
command refuses immediately and the pane is open before the switch arrives. The defect
was in the app either way (`askedAboutOneProject`, `src/main/terminal.ts`); only its
visibility was environmental.

So the guard has a third half, and it is not another scan. **A check that needs the
launch project to have a particular shape supplies its own checkout rather than
asserting about the machine's.** `fakeCheckout` in `tests/smoke.spec.ts` builds the
three things that make a directory the one the app decides it was loaded from —
`src/kyosei/__main__.py`, a `.venv/bin/python`, and an `apps/desktop` with a
`package.json` — around a CLI script that delays exactly one question about exactly
that project, and `launchUnpinned`'s second argument points the launch at it. `a switch
away from a launch project whose home is still being asked lands in the new project's
session` is the check built on it, and it constructs that clone's condition on any
machine, a fresh worktree included.

Two things about that check are deliberate and easy to undo on the next edit. The delay
is the *condition under test*, not a timeout and not a retry — nothing there waits
longer for a failure to become a pass. And it asserts, before it switches, that the pane
has not opened yet: without that, a machine fast enough to open the pane inside the
delay would turn it into a check that cannot fail. If that assertion ever fires, widen
`SLOW_HOME_MS` rather than deleting it.

The two clauses added to `the run says which checkout it ran in` keep the seam and its
one caller from disappearing quietly. Both are written as regexes, like the scan above,
because that test reads its own source: a plain substring needle is satisfied by the
copy of itself inside the assertion that names it, and the check can then never fail.

### The known-intermittent checks (KYO-183, KYO-205)

**This table is the list, and it is what a brief should cite.** A failure in this
suite that is not named here is not a known flake: report it rather than re-running
until it passes. A brief that says "one known intermittent failure exists" is what
KYO-205 was filed about — a worker told there is one either blames itself for the
second or learns to wave any single failure through.

Every row is **cause found and removed**, not suppressed and not retried
(`playwright.config.ts` sets `retries: 0`, and no check here re-runs itself). No fix
widens a timeout: KYO-183's record establishes that the failing runs exhaust a
30-second wait where the passing ones take 8–21 seconds, so a longer wait would turn
them green without repairing anything — and KYO-217's third row is the same shape one
level down, a wait that was pointed at the wrong page and would therefore never have
ended however long it was given.

KYO-238's first row is the one place something is retried, and it is not a check:
`removeTree` retries a `rmdir` **syscall**, in teardown, against a directory another
process is writing into. The distinction the paragraph above is making still holds
there. A retried check re-asks a question whose answer might change for reasons the
check cannot see, and turns green on the second answer without anyone learning which
one was true. A retried `rmdir` re-asks a question whose only possible answers are
"the directory is empty now" and "it is not yet", and the first is the outcome being
asked for. Nothing about the app is being re-examined, and nothing is swallowed:
after the last attempt the error is thrown exactly as before.

**The table has one row that is not closed, and being closed is not the same as "the
suite has no intermittents".** It is the list of the ones that have been *measured*:
run in isolation on an idle machine and again under deliberate load, with the counts
recorded. A failure you see that is not here is still a failure to report. The one
open row is the last — `the title bar's identity line`, whose check and whose subject
were both deleted before anyone measured it, so there is nothing left to run. It is
open because saying so is truer than leaving a reader to infer that a row in a table
of fixed things has been fixed.

**Where the checks named below live.** The first three, and the first two of
KYO-238's, were in `tests/smoke.spec.ts`, which KYO-232 deleted along with the v2
renderer — for the last two, shortly after KYO-238 was filed about them. A row
whose check is gone says so, and says where the *mechanism* went, because a deleted
check is not a fixed defect: `removeFixture` outlived the check that caught it and is
called by every spec file in this directory.

| check | what was seen | cause, and what was done about it |
|---|---|---|
| `a file opened while the machine is busy is never written at all` | roughly two failures in five full runs in a worked-in clone, reliably green in a dispatch worktree (0 in 5 measured here), always `locator.click: Timeout 30000ms exceeded` waiting for `[data-open-idea="opened under load.md"]` | It raced the app's **startup** read of the ideas tree, not the mode's. `DEFAULT_SHELL_MODE` is `ideas`, so `ideas.start()` — and the one `loadTree()` a session makes — is already in flight when `body[data-ready="true"]` appears, and `data-ready` is set after `getFacts()`, a *different* `kyo` subprocess. Whichever of the two resolved first decided whether the victim file, written the instant the shell reported ready, was in the tree that got rendered. The check now writes the victim **before the app is launched** and relaunches onto it, so the startup read cannot miss it. |
| `the scratchpad's entries keep their own place, separately from the ideas tree` | one failure in five full runs measured here, and one in three by KYO-203.d1's worker; `expect(received).toBeGreaterThanOrEqual(expected)` on a measured scrollbar overflow that came back `0` | The check measured a pane the app rebuilds on its own — `renderLeft` runs on every `loadTree()` and every scratchpad save that returns — through `locator.evaluate`, which resolves the element in one round trip and runs the callback in the next. A rebuild landing between the two leaves the callback holding a **detached** element, whose `scrollHeight` and `clientHeight` are both `0`; that subtracts to an overflow of `0`, which reads exactly like "this list cannot scroll". Every scroll measurement in the file now goes through `scrollGeometry`, which looks the element up *inside* the page in the same task as the measurement. `a pane measurement survives the rebuild it is racing` asserts both halves: that a replaced element really does report `0`, and that `scrollGeometry` does not. |
| `closing a popped-out claude window re-fetches the docked pane, same URL and all` | two failures in five full runs (KYO-183.d1), `page.waitForSelector: Timeout 30000ms exceeded` waiting for `body[data-ready="true"]` in the window the pop-out opened. Measured in isolation for KYO-217, `npx playwright test tests/smoke.spec.ts -g "closing a popped-out claude window re-fetches" --repeat-each=10`: **0 failures in 10** with the machine idle (one-minute load 3.9–9.3 during the run), **5 failures in 10** under deliberate load (8 CPU spinners plus a `make test`; one-minute load 8.1 rising to 50 across the run), every one of them that same 30-second `body[data-ready="true"]` wait. The failures are bimodal — the passes took 3.0–11.7s, no run took 15–25s — which is what says it is a wrong answer rather than a slow one. | It was waiting on **the wrong page**. Playwright reports a `WebContentsView`'s contents as a window of its own, so with the claude pane open this app has a claude.ai page in `app.windows()`, and `app.waitForEvent("window")` returns whichever of the two registers first: the popped window's renderer starting, or that remote navigation committing. Measured idle: the caption names the resolved session at +0.25s and the claude.ai page arrives as a window at +1.45s, so the pop-out click falls inside the gap and only the machine decides the order. Losing handed the check a page that can never have `body[data-ready]`. The suite already had `poppedPaneWindow` for exactly this — written for the three older claude pop-out checks — and KYO-190's check simply did not use it; it does now, and picks its window by ORIGIN (`file:` is this app's own renderer, `https:` is the view's page) instead of by arrival. `the claude pane's own page is reported as a window too, so a pop-out is chosen by origin` holds the filter in place: it asserts that the pane's page really is reported as a window, and that the pop-out wait refuses it. |
| `a store older than the build shows decision 5's instruction, and nothing that writes` — **the check is gone**, the mechanism was not | one failure (KYO-238), on a memory-starved full run after KYO-229.d1: `ENOTEMPTY, Directory not empty` out of `removeFixture`'s `rmSync(fixture.base, {recursive: true})`. Passed 3 of 3 alone immediately afterwards. Reproduced deliberately for KYO-254.d1, node v24.18.0, 40 teardowns per arm against a process creating files inside the tree: **26 failures in 40** with the writer running for the whole window, **16 in 40** with it stopping after 0.4s, every one `ENOTEMPTY`. So it is not load-specific — load only widens the window. | A recursive `rmSync` is a **walk**: readdir, unlink each name, `rmdir`. A file created between the readdir and the `rmdir` makes the `rmdir` fail with `ENOTEMPTY`, and plenty still writes into a fixture at the moment it is removed — a `kyo` subprocess the app spawned that nobody awaits, SQLite's `-wal` and `-shm` beside a database being closed, KYO-229's git poll, the pane's shell whose cwd is the fixture root. `closeReadyApp` awaits the Electron and *cannot* await the Electron's children. `removeTree` in `tests/fixture.ts` is the one definition with `{maxRetries: 10, retryDelay: 100}` on it, and `removeFixture` and the login shell's `remove()` both go through it: **0 failures in 40 in both arms**. `tests/fixture-teardown.spec.ts` holds it in place against real writing processes — one check requires ten consecutive wins (a single win proves nothing at a bare rate near one in three), and a second requires that at least one of twenty *bare* removes still loses, so the first cannot pass vacuously on a machine that has stopped racing. Measured with the retries emptied: 3 of 3 repeats red. |
| `after a switch the terminal pane is in the new project, not the previous one` — **the check is gone**; the mechanism is KYO-254's | one failure (KYO-238), same run: `error connecting to .../kyosei switch sockets .../tmux-502/default (No such file or directory)`. Passed 3 of 3 alone immediately afterwards. | **Cause found, by the path in the message.** `kyosei switch sockets ` was a `mkdtemp` prefix in exactly one place in this repository's history — the sibling check `a switch detaches from the previous project's tmux session without killing it`, in the same deleted file — and that check removed the directory in its own teardown. The failing check set no `TMUX_TMPDIR` at all, so the one it used came from `process.env`: **a socket directory this suite created, escaping the run that created it, and reaching a later one after it had been deleted.** That is KYO-254 in the inbound direction, and `suiteEnv` closes it: no launch environment in this directory carries an inherited `TMUX`, `TMUX_PANE` or `TMUX_TMPDIR` any more, and `the pane's server is the orchestrator's, whatever TMUX_TMPDIR the app inherited` in `tests/tmux-owner.spec.ts` fails if that changes. **The outbound leg closed with KYO-474**: the app no longer starts or seeds any tmux server -- it attaches to the orchestrator's, which `kyo controller` starts -- so a suite-launched app has no server of its own to leave a fixture's environment in. |
| `a typed [[id]] is Mod-clickable and opens the record through followLink` — in `tests/compose.spec.ts` | Reported independently by KYO-283.d1 (on the UI branch) and KYO-266.d1 (on `main`), both saying the same thing: it fails when the whole file runs and passes alone. Measured: **3 failures in 3** with `npx playwright test tests/compose.spec.ts --repeat-each=3`, and **0 in 3** with the same repeat on the check alone (`-g "Mod-clickable"`). Every failure the identical 10-second `toHaveCount` wait for `[data-tab-kind="record"][data-tab-arg="FIX-1"]`, resolving 0 elements on all 23 polls. **It is not load and it is not intermittent**: three of three in the file, none of three alone, on a machine where the passing runs took 6.7–13.0s. | **Order inside the file, and not the pixel it clicked.** The scratchpad is ONE file for the whole spec — `KYOSEI_HOME` is the fixture's and `beforeAll` makes one fixture — so this check opened on whatever the check before it left there, and `` `[[` offers real records, `#` offers real tags, and `[[<id>.` offers only that record's real children `` leaves `[[FIX-1.`: a half-typed child-id trigger with no closing brackets, the last intermediate state that got 600ms of quiet (`SAVE_DEBOUNCE_MS`) before `afterEach` closed the app. Printed from the running check rather than reasoned about: on entry `"[[FIX-1."`, and after typing `"[[FIX-1.see [[FIX-1]] for context"`. **Both halves then read the wrong thing.** `text.indexOf(recordId)` found the id in the *leftover* at offset 2, so `coordsAtPos` aimed the click at that token; and `linkAt`'s `/\[\[([^\]\n]+)\]\]/` matched from the leftover's `[[` all the way to the TYPED link's `]]`, capturing `FIX-1.see [[FIX-1` — so a click anywhere in *either* token resolved to an id no record has, and `followLink` opened nothing. Which is why the click point was never the thing to fix: the editor followed the link it was asked for, and the check asked for the wrong one. The check now clears the editor and asserts the document is exactly the line it typed, so the carry-over cannot come back unannounced; the click point is the midpoint between `coordsAtPos` for the two ends of the id rather than two pixels off one end; and that point is put back through `posAtCoords` — the same call the editor's own `click` handler makes — so a point that could not have followed the link is named on the spot instead of clicked and waited on for ten seconds. No timeout was widened. After: **0 failures in 27** (the whole file, `--repeat-each=3`) with this repo's own `make test` running beside it, one-minute load 15.5–18.7; and **0 in 27** again under deliberate load, 8 CPU spinners on 8 logical cores, one-minute load 6.2 rising to 38.2. A third arm — the spinners stacked on top of an already-running `make test`, one-minute load reaching **162.9** — is the one place anything went red, and it is the shape the paragraphs below already account for rather than this check's: two 60-second `afterEach` timeouts inside `closeReadyApp` with no assertion reached, one of them this check and one of them `a whole idea file promotes to a chosen kind`, in a run where Playwright's own 60-second worker teardown also timed out. |
| `the title bar's identity line is the store's own, read from its database` — **the check is gone, and so is what it checked** | one failure reported by KYO-229.d1's worker, `database is locked`; **0 of 5** on repeat, by that worker, immediately afterwards | Nothing measured, and nothing measurable: the v2 title bar and its identity line went with the v2 renderer in KYO-232, so there is no check to run under load and no code path to look at. Recorded here rather than dropped, because "the check was deleted" is a different thing from "the defect was fixed" and a reader deserves to be told which one this is. What is known is only what the original report says: one failure, `database is locked`, and five clean repeats. If a `database is locked` ever surfaces again in this suite, this row is the prior observation to cite — it is not evidence that there is no such defect. |
| `a sidebar list renders no rows after launch` — seen three times, as `rowsFor(page, "records")` in `a context menu closes on Escape` and as the Records and Notes rows in `every tab's CSS is installed by the one helper` (both `tests/shell.spec.ts`) and `a criterion id embedded in a ledger command is a link` (`tests/views.spec.ts`) | Three failures in one day, each a different check and a different list — Records twice, Notes once — each a 30-second wait for a `[data-region="sidebar-row"]` that never came, each passing alone within seconds afterwards. The first two happened while other desktop checks were running and were read as contention; **the third had nothing else on the machine**, which is what ruled that out. Roughly one launch in 250. Reproduced here with a launch harness built from `shell.spec.ts`'s own pattern (one shared records fixture, a fresh display signature per launch, the Records and Notes lists opened on every launch): **2 empty lists in 400 launches**, both the Records list, both showing the bare text `database is locked` where its rows belong. | **`node:sqlite` defaults its busy timeout to zero, and this app's reads are not alone at the database.** Every `kyo` subprocess the app spawns opens the store's WAL database and closes it — `where`, `base`, `next` and the status bar's currency read, several per sidebar load — and closing the last connection to a WAL database checkpoints it and removes `-wal`/`-shm` under the exclusive lock. A read-only open landing in that window got `SQLITE_BUSY` on its first and only attempt; `readRecordsList` and `readShiftNotes` turned it into `ok: false`, and the sidebar drew that bare SQLite sentence with no rows under it — which is why both lists could fail the same way and why neither could be slow instead of wrong. Isolated by arm against a WAL store two processes were opening and closing the way `src/kyosei/store.py`'s `connect` does, 8 seconds per arm: **0 failures in 26,220 read-only opens** when the writer only opens and closes, **74 in 79,875** when it also runs a SELECT, **298 in 83,010** when it also runs `PRAGMA journal_mode = WAL` on connect, as `kyo` does. The remedy is the busy timeout SQLite has for exactly this: `openReadOnly` in `src/main/store.ts` is now the ONE place this app opens the store — the missing timeout survived because three call sites each built their own connection and only one was ever looked at — and it opens with `READ_BUSY_TIMEOUT_MS`. Same harness, timeout set: **0 failures in 17,656 opens**, and **0 empty lists in 400 launches** through the app. **No wait was widened**, and the distinction is the one this table already draws for `removeTree`'s retried `rmdir`: a busy timeout's only two answers are "the lock is free now" and "it is not yet", the first is the outcome being asked for, and after it expires `SQLITE_BUSY` is raised exactly as before. Two seconds rather than more because `DatabaseSync` is synchronous and a read that waits blocks the main process — stated in the constant's own comment. `a read waits for the store's lock instead of failing on the first try` (`tests/seams.spec.ts`) holds it: a child process takes the lock and gives it back, an open built the old way is REQUIRED to lose on it so the check cannot pass vacuously, and the production path is required to wait and answer. Measured with the timeout removed: red. What made all three failures unreadable is closed separately, because a fixed cause is not an observable one: the sidebar names the list in front of the tool's sentence (`the records list could not be read: database is locked`), states an empty result instead of drawing a heading that reads `Records (0)` and nothing else, replaces a spinner that has not answered in 15 seconds with a line saying so, and stamps `data-load` on itself — `loading`, `rows:<n>`, `stated`, `silent-empty` — from one `MutationObserver`, so a kept page snapshot says what the list was holding. `a sidebar list never just goes blank` in `tests/views.spec.ts` asserts the stamp and the sentence. Since KYO-459 the app opens no SQLite at all -- every list is a `kyo serve` read -- so this cause cannot recur in it; the lock-wait check went with `store.ts`'s reads. |

**How much the first two rows (`a file opened while the machine is busy`, `the scratchpad's entries keep their own place`) are worth.** Five consecutive full runs before the
fixes and five after, in a dispatch worktree with three other workers on the machine:
one failure of the scratchpad check before, none of either check after. Five green
runs is not proof at a two-in-five rate — the record says as much — which is why the
claim being made here is that the *mechanism* is gone rather than that the runs came
back clean, and why each row says what the mechanism was and what would bring it
back.

**How much the third row (`closing a popped-out claude window re-fetches`) is worth.** The same command that produced 5 failures in 10
under load produced **0 in 10 after the fix, under a heavier load than the one that
broke it** (one-minute load 27–72 across that run, against 8–50 for the failing one).
That is a stronger claim than the runs alone would carry, because the mechanism is
not statistical: with the window chosen by origin there is no longer an event to lose,
and the check that holds the filter in place goes red in about a second when it is
deleted.

**A claude-pane check that is NOT in this table, and why.** KYO-184.d1 reported that
`the claude pane carries a reload, and using it re-resolves the pane` failed once in
three full runs, at the assertion that a pane nobody has told anything still shows the
OLD session — the pane had already moved to the new one. KYO-217 measured it the same
way as the row above, `-g "the claude pane carries a reload" --repeat-each=10`: **0
failures in 10 idle** and **0 of that kind in 10 under load**. The one loaded failure
was a bare `Test timeout of 60000ms exceeded` with no assertion reached, at a
one-minute load average around 65 — the whole 60-second budget spent, which is the
machine and not this check. So the reported signature did not reproduce and there is
nothing measured to put in a row. It is written down here rather than dropped: an
observation that did not reproduce is a different thing from one nobody looked at.

**The start-up budget under load, measured — and CPU load is not the variable
(KYO-238).** Four of this suite's occurrences are the
same shape and none is in the table: a wait budget exhausted, the check passing alone
straight afterwards, no wrong answer anywhere. The fourth was four checks on a merged
tree at one-minute load average 68 (`gate-payload.spec.ts:172`, `shell.spec.ts:1821`,
`:1871`, `:2101`), every one a 30-second `page.waitForSelector` or the 60-second test
timeout followed by KYO-118's *first window never reported ready*, all four passing in
1.4 minutes alone. The record proposed a hypothesis with it: *the fixture's start-up
budget is fixed while the machine's headroom is not.*

Measured for KYO-254.d1, ten launches per arm timed from
`_electron.launch()` to `body[data-ready="true"]` with no cap, so the distribution is
visible rather than censored:

| arm | one-minute load | start-up |
|---|---|---|
| idle | 6.5–8.4 | 1.17–1.70s, median **1.27s** |
| twelve CPU spinners | 9.6 rising to **67.6** | 1.79–2.36s, median **1.91s**, none over 30s |

So the hypothesis is **not supported as stated**. At the very load average the fourth
occurrence recorded, start-up keeps about fifteen times the 30-second budget in hand,
and the loaded distribution is a 50% stretch rather than a cliff. CPU contention is not
what exhausts these waits, and widening the budget would answer nothing — it would only
move the number that gets exceeded.

What every failing run had and this arm did not is **memory pressure and disk
contention**: one was a run on which background jobs had already been killed for low
free memory, and another had two Python suites in other worktrees and a device simulator
starting beside it. That is the variable to instrument next, and it is named here so the
next person measures the right one. A later full run is a fifth data point of the same
shape: one failure,
`shell.spec.ts:1284` with `locator.click: Timeout 30000ms exceeded`, at one-minute load
around 34 — 3 of 3 in isolation immediately afterwards, at 2.1–2.8s each.

### The suite opens no windows (KYO-122)

`npm --prefix apps/desktop run smoke` launches the real Electron app dozens of
times. Until KYO-122 each of those launches put a real window on screen and took
the keyboard, so the machine running the suite was unusable for the length of the
run — you could not read the terminal the suite was printing into. It no longer
does: **the suite's windows are never shown at all.** They are created, loaded, laid
out and driven over CDP, and nothing appears on your screen or in your dock.

Focus was the obvious reading of "the machine is unusable while it runs" and it was
the wrong one — you could type into another app throughout a run. What the windows
were doing was *painting*: ordered front, across a full-screen app in its own macOS
Space. An inactive window still does that, so `showInactive()` does not buy the
relief it looks like it buys.

The switch is a launch argument, `--kyosei-window-show=<focus|inactive|hidden>`,
parsed by `parseWindowShow` in `src/shared/protocol.ts` and applied in
`src/main/main.ts`:

| value | what a window does |
|---|---|
| `focus` | appears and takes focus (`show()`). **The default** — this is what `npm run start` gets, and what you get if you pass nothing or misspell the value. |
| `inactive` | appears without activating the app (`showInactive()`): on screen and compositing, but your keyboard stays where it was. Still paints over what you are looking at, which is why it is not what the suite uses. |
| `hidden` | never shown at all. **What the suite passes.** |

On macOS the app also drops its dock icon (`app.dock.hide()`) for anything but
`focus`, **before the first window is created**. That makes the process an accessory:
it never activates itself, so it never pulls itself onto the Space you are working in.

**What makes `hidden` safe, rather than merely quiet.** Chromium treats a never-shown
window as occluded and throttles it — timers to about 1Hz, the renderer
deprioritised, frames starved — and a suite running in a regime the product never
runs in is a suite that can pass vacuously (KYO-120 found exactly that failure mode
in this file). So the throttling is turned off by name rather than routed around:
`prepareApp` in `src/main/main.ts` appends `disable-background-timer-throttling`,
`disable-renderer-backgrounding` and `disable-backgrounding-occluded-windows` before
`ready`, and every window carries `backgroundThrottling: false`.

That is measured, not assumed. `an unshown window is not a throttled one` in
`smoke.spec.ts` counts animation frames and chained zero-delay timers *inside* a live
unshown window. Under those switches it reports `document.visibilityState ===
"visible"` and delivers frames at full rate — so there is no observable difference for
page code at all. Remove any one switch and that check fails.

The suite's own answer is `SUITE_WINDOW_SHOW` in `tests/fixture.ts`, and it is
`hidden`. **A check that genuinely needs another policy opts in for itself** —
`launchArgs("focus")` or `launchArgs("inactive")` in `smoke.spec.ts`, or
`windowShowArg(…)` appended to any other launch's arguments, with the reason at the
call site. There is deliberately no environment variable and no way to turn the
whole suite loud: quiet is what the suite *is*, and anything else is a per-launch
exception. No check needs one today; the parameter exists so the first one that does
costs one argument instead of everybody's afternoon.

To watch the suite drive a real window, pass `launchArgs("inactive")` or
`launchArgs("focus")` temporarily; to run the app yourself, `npm run start`. Both
`parseWindowShow` (in `tests/seams.spec.ts`) and the suite's actual
quietness (in `tests/smoke.spec.ts`, asserted against a live Electron's
`isFocused()` and `app.dock.isVisible()`, and against `isVisible()` for whichever
policy the suite currently declares) are checked, so pointing the suite back at
`focus` fails a test rather than merely being annoying.

## The shell (KYO-232)

Three regions and nothing else: the **rail**, the **sidebar**, and the **columns**.

**The rail** is nine things in a narrow column — scratchpad, ideas, records, briefs,
docs, activity, terminal, claude, and the project switcher at the foot. Five of them
are the shell's own and select a sidebar LIST; the rest are contributed by tab kinds
that declare a `rail` entry in their own directory, so the rail is not a second
registry of what a tab may be. Two carry a live badge: records shows how many gates
wait on a person, activity how many dispatches are running. Both numbers come from the
read model and are pushed by the main process when they move — a badge read once at
window start would go on saying `3` through a session in which the orchestrator cleared
every gate.

**The sidebar** holds the lists and is never a popover: it is a region of the layout,
it pushes the columns rather than floating over them, and clicking the rail icon that
selected it collapses it. Every row carries `data-row-kind` and `data-row-arg`, so one
set of handlers serves four lists.

**The columns** are one or two (KYO-246; KYO-227 said three, and in use two proved
enough). One is ACTIVE, with a highlighted tab strip, and a click on
a list row opens there. A row dragged onto a column opens there instead; a tab dragged
down into a column moves there; a right-click on a row offers open-in-a-new-tab and
open-in-the-other-column, and on a tab offers move to the other column, move to a new
window, close, close others and close to the right; the split control TOGGLES the
second column, folding its tabs back into the first when turned off; closing a column's
last tab removes the column unless it is the only one, and an empty column draws the
home page.

**Tear-off, the way Chrome does it.** A tab pulled UP off its strip, or out of the
window, tears off the moment it leaves: the main process opens a shell window around it
under the pointer (`showInactive`, so the pointer stream stays with the window the drag
began in) and moves that window with every pointer move the origin renderer reports
(`tabDragStart` / `tabDragMove` / `tabDragEnd`). Carried over another window's strip
band it dims and that strip shows an insertion marker where it would land; released
there it attaches at the marker and the emptied window closes; released anywhere else
it stays a window. A window's only tab dragged off its strip moves the whole window
rather than opening another. Up and out only, because dragging DOWN into a column is
how a tab moves between columns here, and Chrome has no columns.

**Every tab is a small browser (KYO-247).** A tab holds a HISTORY of what it has
shown: a link followed inside a page navigates that tab rather than opening another
(the origin tab rides the `kyosei://` delivery as `LinkOrigin`, and `shell.ts`
navigates it when the link asked for no column and no new tab), the home tab becomes
the thing chosen on it, and back and forward -- at the left of each strip, and Cmd+[
and Cmd+] -- walk the history. Entries walked away from are kept drawn and hidden, up
to `HISTORY_MAX`, so back is instant with its scroll position; a layout saves each
tab's history and restores it as names, drawn on arrival. Singleton kinds (the
terminal, the claude view) never navigate and are never navigated to.

**The click model (KYO-247).** A single click on a list
row shows it in the tab the reader is in, the way a link does in a browser: the tab
keeps its history, so back returns to what it showed, and a row already open in some
tab raises that tab instead. Cmd-click opens a tab of its own in the same column
without leaving the current one; a row's menu offers a new tab, the other column, or
a new window. A first cut had a VS Code-style italic preview tab that a single click
reused; it proved unintuitive, because everything opened in a preview tab that kept no
history, and it is gone.

**The strip scrolls.** Tabs shrink toward a floor width and then the row overflows
sideways, with no scrollbar drawn: the wheel moves it, and selecting a tab scrolls it
into view. Every open tab is reachable from its strip, which the clipped v3 strip did
not guarantee past five or six tabs. Each strip ends in a "+" that opens the home page
as a tab in that column.

**One project per window (KYO-249).** The project is a property of the WINDOW, not
of the process: `ShellWindow.projectRoot` is what every read and write that window's
renderer asks for runs against (`projectCwd(event)` resolves it from the sender), the
switcher moves that one window in place, and New Window -- the menu, Cmd+N, the title
bar's "+" -- opens the project chooser (recents, this project, the native picker) and
opens a window ON the choice. Consequences, each of which used to be a process-wide
single: the activity and awaiting polls run one per project and push to the windows
on it; the per-project home is cached by root; a switch ends that window's own pty
and re-resolves that window's own claude view; layouts are keyed by project AND desk
(a layout saved before the change is read back under the desk alone), and a switch
sends the window the target project's own layout to restore; a `kyosei://` link opens
in a window already on its project -- the window it came from when that one is, the
focused one on it, any one on it -- or opens a new window on it. `KYOSEI_PROJECT_ROOT`
still pins: with it set, a window on another project is refused by name. The
popped-out claude.ai window belongs to no project window (KYO-246) and asks the window
a command would go to when it needs a project at all.

**The scratchpad promotes to an idea, not a record (KYO-252).** Only the orchestrator
promotes to a record. The scratchpad's ribbon
is one button, enabled when text is selected; it opens `tabs/compose/idea-dialog.ts`:
a name suggested from the selection's first line, a folder chosen from the vault's own
(`getIdeaTree().folders`), and a checkbox, on by default, to remove the promoted text
from the scratchpad. Save writes through `createIdea` -- the same guarded main-process
path the ideas list's "New idea here…" uses -- then puts the new file's path relative
to the project root on the clipboard (`copyText`, a main-process seam over Electron's
`clipboard`) and says so under the ribbon, because the usual next step is to paste it
into the orchestrator's chat. The text leaves the buffer only if it still
reads exactly as it did when promoted.

*History (KYO-234, removed by KYO-439):* an idea file's own tab once carried "Promote
selection" and "Promote file", which opened `tabs/compose/promote.ts` -- a kind picker
read off `kyo map --json`, title, criteria and touches -- and ran `kyo promote` through
main's `runPromote`, staging a selection under `ideas/.compose-staging/`. All of it is
gone: the dialog, both buttons, `runPromote` and `readKindRegistry`, their bridge,
channel and served-route entries, and the staging folder. An idea tab's ribbon is
empty.

**One stylesheet convention, and the title bar is the strip row (KYO-253).** Every
class name the renderer writes has a rule: `tests/styles.spec.ts` reads the renderer's
sources the way a bundler would, collects every `class:` literal (both arms of a
conditional, `classList.add`, the CodeMirror class hooks) and fails naming the first
one no `shell.css` or `styles.ts` selector mentions -- KYO-240.d1 had measured 56 of
171 with no rule, and the count had grown to 68 of 228 by the time anyone looked
again. The rules that were missing are now where the convention in `tabs/kind.ts`
says they go: the document renderer's in `tabs/docs/styles.ts` (installed by
`renderDocument` itself, since `docs/` is not a tab kind), the editor chrome, the
completion popup and the promote-to-idea dialog in `tabs/compose/styles.ts`, the document
banner in `tabs/views/styles.ts`. The same spec refuses a literal colour or a pixel
font size in any rule: the tokens live on `:root` in `shell.css` and nowhere else, a
seven-step type scale (`--fs-2xs` … `--fs-2xl`) and one palette with a soft and a
border shade for each tone. And there is no title bar element any more. The rail's
traffic-light spacer, the sidebar's head and each column's strip are the window's top
row and its drag region (`[data-drag-region]`, with the tabs and buttons opted out),
so a torn-off tab opens with its tab at the top edge the way Chrome's does; the app
name, the store line and the window chips moved to the status bar, and the link strip
sits just above it. `TORN_TAB_LEFT`/`TORN_TAB_TOP` in `main.ts` and `STRIP_BAND_PX`
in the protocol are the numbers that moved with it.

**Activity is a list (KYO-251).** The activity TAB is retired, and Activity is the sixth
rail list, with the
running-dispatch count as its badge. Running dispatches first, a divider, then the
finished ones newest first, each at most two lines: the dispatch id (a link), the
worker model and the elapsed or total time; then the report id (a link) with its
verdict tally, or the terminated-no-report mark with the reason on hover, the lines
added and removed past the base (`git diff --numstat`, asked in the worktree while it
exists and in the code repository by branch once it is gone), and the gated and
not-moved marks. A running dispatch adds a third line saying only what the store can
observe -- the latest commit's subject, whether the report file has appeared, whether
the worktree is gone -- and says nothing when it cannot. Beneath each dispatch,
indented, the record it was for and every record it also covered (`covers_json`),
each a row that opens the record. The list is live while it shows: the per-project
activity poll repaints it. A layout saved with the old `activity` tab opens the
build's own "no tab kind called activity" placeholder, which is what law 6 asks for
rather than a broken table.

**Every list sorts and filters, and ideas is a folder tree (KYO-250).** Each sidebar
list carries a sort button and a filter button beside its search; each opens a box
under the tools row. Sort is by name, created or modified (records: id, title,
created, modified), ascending or descending; filters are mostly the values the loaded
rows actually carry -- records by kind, status and scope; ideas by folder and by
promoted or not; docs by staleness and folder -- and the filter button carries a dot
while any is on. Sorting and filtering happen in the renderer over the rows the list
loaded (`arrange` in `shell/sidebar.ts`); the stamps they sort on are the store's own
`created_at`/`updated_at`, the file's `birthtime`/`mtime` for ideas, and the file's
`mtime` for docs. Every list's state -- sort, filters, folded folders -- is saved with
the layout (`LayoutSnapshot.sidebar.lists`) and restored with it.

**A showing list follows the store (KYO-310).** Two records the orchestrator had filed
from the CLI minutes earlier (KYO-308, KYO-309) did not appear in the app, though both
existed. A list used to be a
SNAPSHOT of the moment it was opened -- `sidebar.refresh()` ran on a rail click and on
a project switch and nowhere else -- so everything filed, moved or closed in the
terminal pane beside the app was invisible until the reader clicked away and back.
Records, Notes and Ideas now follow it. Since KYO-459 the change is the project's **kyo
serve's events stream** (`src/kyosei/serve_events.py`): the server takes the store's
own fingerprint where the store is -- the database's mtime and size and `export/`'s
mtime for `store`, the newest mtime and entry count under `ideas/` for `ideas`, and
`sessions.json` for `sessions` -- once a second, and pushes `changed` when it moves, so a
read-only `kyo show` (which moves `kyosei/`'s mtime through SQLite's journal churn) is
not a change, and an orchestrator reading beside the app must cost this process nothing. The renderer's side
is one pure function, `storeReloadDue` in `shared/protocol.ts`: a list that is not
showing re-reads nothing, a list re-reads only its own world (`LIST_FOLLOWS`: activity
has a live push of its own, docs is a fact about the working tree), and a read already
out is not stacked on. A follow is not a `refresh()`: it replaces the rows underneath
the reader and leaves the head alone, so the scroll position, the typed search, the
sort, the filters, the folded folders and the open marks are all still there, and no
spinner flashes over a list somebody is reading. `data-store-reloads` on the sidebar
counts the re-reads, which is how "a list that is not showing costs nothing" is checked
in the running app rather than asserted.

**Records also filter by Readiness, and "dispatchable" there is `kyo next`'s own word
(KYO-290).** Filtering for open work returned records that were built but unverified
with criteria still outstanding, when the question was what could be handed to a worker
now. So the records list has a fourth group whose values
are the classes `kyo next --json` computes -- dispatchable (`ready`), finished but not
moved (KYO-87's class), blocked (KYO-570's list: an unsettled `blocks` source holds the
record back, so it is never offered as dispatchable; KYO-571), scope unknown, and not on
the board -- stamped onto each row in
the main process by `readRecordsList` from `readBoardClasses`, never re-derived here. It
is the one group drawn from a fixed vocabulary rather than from the rows, because
"dispatchable" missing from the box would read as the filter being absent rather than as
nothing being dispatchable. When `kyo next` cannot be read every row falls to `none`, so
the group prints why in place of its chips and stops narrowing altogether: an empty list
under a chip the reader pressed would be answering a question nobody could ask. The
group's vocabulary and that sentence live in `shared/protocol.ts` beside `BOARD_CLASSES`
so a check can reach them with no window (`tests/seams.spec.ts`).

The ideas list is a tree: the root's files, then each folder with its count, folded or
not by a click. A folder's menu offers a new idea or folder inside it, rename, move and
delete; a file's menu offers rename, move to any other folder, and delete; a row
dragged onto a folder header moves there; the toolbar's folder button makes a new one
at the root. Names and confirmations come through the app's own prompt (a native
dialog cannot be driven by a check). The four writes -- `mkdirIdeas`, `renameIdea`,
`moveIdea`, `deleteIdea` in `main/ideas.ts` -- resolve their target through the same
`assertWritable` guard `saveIdea` and `createIdea` use, so anything outside `ideas/`
is refused with a sentence naming the path.

**The rail holds lists only (KYO-248).** Ideas, Records, Awaiting you, Notes, Docs,
then the project switcher; every item selects a sidebar list and nothing on the rail
opens a tab. Awaiting is its own list -- each record with an open gate waiting on a
person, the gate type on the row -- rather than a group pinned atop the records list.
Notes replaces Briefs: every shift note, newest first, with the HANDOFF badged (the
note standing when a session-start brief ran, the one `kyo brief` re-surfaces to the
next session) and the latest brief kept at the top; a click shows that note by id
(`getShiftNotes`, read straight out of the ledger, because no read-only command lists
them). Scratchpad, terminal, claude and activity are on the home page and in the View
menu (`open-*` shell commands).

**The home page** (`src/renderer/shell/home.ts`) is what a new tab, an empty column and
Cmd+T show: every entry the tab kinds declare through `TabKind.home`, sorted by order.
The things that used to be rail icons opening tabs — scratchpad, terminal, claude,
activity — are entries here; KYO-248 takes them off the rail.

**The application menu** is the app's own (`installApplicationMenu` in
`src/main/main.ts`). Electron's default gave the app a File menu whose only item was
Close Window on Cmd+W, which closed the whole window without asking. Now Cmd+T is New
Tab, Cmd+N is New Window, Cmd+W is Close Tab, Close Window is Shift+Cmd+W and asks
first when the window holds tabs, Cmd+\ toggles the split, and Ctrl+Tab cycles. Each
workspace item is a `ShellCommand` the main process sends to the focused shell window
(`CHANNEL.command`), and the shell acts on it — accelerators are the main process's,
the workspace is the renderer's, and neither pretends to own the other.

Two decisions in `src/renderer/shell/columns.ts` have a worse obvious alternative:

- **A tab's body element is created once and moved, never rebuilt.** Re-rendering on
  every layout change would throw away what the shell does not know about — an
  xterm.js screen with its scrollback, a scroll position, a half-typed search.
- **Dragging is pointer events, not HTML5 drag and drop.** The native mechanism goes
  through the OS drag manager: the app cannot draw its own drop feedback reliably, and
  a check driving it is driving AppKit rather than this app.

A third is smaller and was measured rather than reasoned about: selecting a column or a
tab repaints ATTRIBUTES (`paintState`) and never rebuilds the strip. A button removed
between a press and its release never receives the `click` — Chromium dispatches it on
the nearest surviving ancestor — so a full render on press made the split control of an
inactive column do nothing at all.

Every shell region carries a `data-region` attribute. That is what `tests/shell.spec.ts`
asserts against, and it is the machine-readable form of "these regions persist".

## The tab registry, discovered by directory (KYO-232.c5)

`src/renderer/tabs/<kind>/index.ts` exports one `TabKind`, and that is the whole
registration mechanism: `build.mjs` enumerates the directories into
`src/renderer/tabs/registry.generated.ts` (gitignored, because it is output), and
`src/renderer/tabs/registry.ts` checks each module's `kind` against the directory it
came from and refuses a mismatch by name.

**`src/renderer/tabs/kind.ts` is the interface, and it is documented there rather than
here.** A stage 3 worker reads that one file and can write a tab. The four members are
`open`, `title`, `render`, `close`; a tab identifies itself by `{kind, arg}` everywhere
— in a saved layout, in an `openTab` call, in the singleton rule, in a pop-out — and
the shell never reaches into a tab's state.

A generated module rather than an esbuild plugin resolving a virtual import, because
`tsc --noEmit` is a separate check in `npm run check` and knows nothing about esbuild's
resolver: a virtual module would typecheck as missing. `npm run typecheck` regenerates
it first (`pretypecheck`).

Stage 2 ships placeholders for `record`, `compose`, `views` and `activity`, each
rendering the read model's own data raw — one directory per stage 3 dispatch, so each
of those replaces one directory and edits no file anybody else is in.

## Layout memory, per display configuration (KYO-232.c3)

The set of columns, their widths, their tabs and the sidebar's state are saved against
the ATTACHED SCREENS — each display's `id` and `bounds`, sorted and joined — and
restored when that configuration is seen again. So a laptop's own screen, a single large
monitor and a dual-monitor desk each restore their own arrangement, and any of them can be
changed at any time.

The key is computed in the main process (`displaySignature` in `src/main/main.ts`),
because `screen` is a main-process module and a sandboxed renderer cannot see a monitor
at all; the renderer is told the key it is saving under and writes it to
`body[data-display-signature]`. The layouts live in one file, `layouts.json` under the
app's `userData` directory — never in any project.

`KYOSEI_DISPLAYS` is the seam: a JSON array of `{id, bounds}` that stands in for
`screen.getAllDisplays()`. It is how the checks simulate two configurations without two
monitors, and how a human pins one layout across a docking station that renumbers its
monitors between unplugs.

**A save names the key it was made under, and one that no longer matches is refused**
(KYO-334). The shell debounces its saves by 250ms, and both halves of the key — the
project (KYO-249) and the desk — can move inside that window: `openProject` sets the
window's project and then awaits a `kyo` subprocess before it tells the renderer, and a
screen event moves the signature under a window that has just been rearranged. A save
that left the renderer before such a move and arrived after it used to be filed under
whatever was current, so the tabs a project switch had just closed came back in the new
project, and the new project's own arrangement was overwritten with the old one's
(measured 3 of 3, KYO-319.r1). So `saveLayout` carries `{root, signature, layout}`; the
handler in `main.ts` drops a save whose root or signature is no longer the window's; and
the renderer (`shell.ts`) does three things around the same 250ms. It FLUSHES the
pending save before a switch this window starts, so the reader's last arrangement of the
project they are leaving is kept rather than lost. It DROPS one when `projectChanged`
arrives, and saves nothing at all until the `layoutChanged` that follows says which
project the arrangement on screen belongs to. And it IGNORES a `layoutChanged` push
whose project and signature the window already has: such a push carries the layouts
file, which is at best equal to the workspace and at worst 250ms behind it, and applying
it would close every tab and rebuild the saved ones — putting a tab just closed back on
screen, and then saving it there.

## Windows

The main process (`src/main/main.ts`) holds the only shared state: which windows exist,
and which tab each pane window is showing. Two window kinds:

- **shell windows** — the full window. They appear in the title bar's window cluster;
  the `+` chip opens another, and clicking a chip focuses that window. A shell window
  opened by "Move to a new window" or by a tab dropped outside every window is
  **transient** (KYO-246): it opens on a layout seeded with that one tab, never writes
  its layout to disk, and hands whatever it holds back to the window it came from when
  it closes — for the terminal with the same pty, because the session id moved with the
  tab (KYO-124) and moves back with it.
- **pane windows** — the claude.ai view, alone. After KYO-246 that is the only pane
  kind ever opened: the popped-out claude window is the account's, not any project's
  (it carries the session switcher, and is where the reader hops between every open
  project), so it has no owner, is never taken back into a column, and there is at most
  one of it — a second request focuses it.

`POPOUT_PANES` is still three words: `terminal` and `claude` name the scarce
main-process object behind a tab (a pty, a `WebContentsView`). `terminal` and `tab` now
both open a shell window, and the word only decides whether a session moves with it.

Both kinds load the same `index.html`; the main process tells each renderer on first
contact which it is (`CHANNEL.hello`).

## The live seams (KYO-111)

Three things connect this window to the project it is looking at. Each one is a
single named place, and the reason each is single is a law rather than taste.

### 1. One resolver, two sources — `src/main/kyo.ts`

Every `kyo` this app runs goes through `resolveKyo()`. It knows exactly two
sources and prefers them in a stated order:

| source | what it is | where it comes from |
|---|---|---|
| `working-tree` | this repository's checkout, run as `.venv/bin/python -m kyosei` | the first directory at or above the app that holds `src/kyosei/__main__.py` |
| `login-path` | the `kyo` the user installed (`uv tool install kyosei`) | the first executable `kyo` on the **login shell's** PATH (`shellenv.ts`), and nowhere else |

In a checkout the working tree wins, and that is a decision (KYO-79): a
change under `src/kyosei/` is live the next time the app invokes anything, because
nothing is built, copied or installed in between. **The status bar states which
source is in use** (`kyo: working tree`), with the exact command line in its
tooltip — it is not left to be inferred.

**The app ships no Python of its own** (KYO-450, KYO-443). Until KYO-450 the second
source was a CPython runtime a packed build carried under `<resources>/kyo-runtime/`
(KYO-79, KYO-176); KYO-443 superseded that decision, and the runtime, its
packing and its PATH shims are gone. A packed app has no checkout above it, so it runs
the `kyo` on the login PATH — the one Terminal.app and every hook run. The PATH is the
login shell's own, captured once (KYO-229), never `process.env`: from a Finder launch
that is `launchd`'s four-directory minimum. Nothing beyond it is probed, because a
`kyo` the hooks do not run is not one this app may run either. With no `kyo` there the
source refuses, naming `uv tool install kyosei`; when *no* source is runnable, the app
says so once and names **both** refusals. Whether the `kyo` found is one this app
reads — its version against the range the app accepts — is KYO-449's check, not the
resolver's.

Call sites never build an interpreter path — `tests/seams.spec.ts` scans `src/` and
fails if `node:child_process` or `.venv` appears in any file but `kyo.ts`.

Two rules live at that seam because that is the only place they can be enforced:

- **Paths are quoted.** `runKyo` uses `execFile` with an argv array and **no
  shell**, so nothing is ever re-split and a project path with spaces cannot be
  corrupted. Where a real shell does read what we wrote — the first shell of a tmux
  session this app creates — every word goes through `shellQuote`.
- **Prose goes through `--*-file`.** `assertNoProseArguments` refuses any argument
  containing a newline, naming the flag form that works; `proseFileArgs()` is that
  form, and returns an argv pair rather than a string, so there is no version of it
  that can produce a shell fragment.

### 2. Every read and write goes through `kyo serve` — `src/main/kyoserve.ts` (KYO-459)

The app is a client of `kyo serve` for every project it shows, this machine's included
(KYO-452). `kyoserve.ts` is the one transport: a server
is an address (`127.0.0.1:47370` for this machine, or `KYOSEI_SERVE_URL`; another
machine's Tailscale Serve name over HTTPS), a project is (server, registry name), and a
`ServedProject` is what every store module takes -- `json(args)` for a served command,
the file routes for `ideas/` and the scratchpad, and `subscribe` for the events stream.
The app opens no store database: the title bar's record count is `kyo list`'s total,
the schema is the one `kyo where` opened the store under, and briefs, shift notes,
gates and activity are `kyo briefs`, `kyo shift-notes`, `kyo gates` and `kyo activity`
(KYO-454). `store.ts` keeps only the layout's file names. One reader of the schema, the
CLI (law 3).

**Where the store is** is `kyo where --json`, asked of the server; nothing here walks
directories. When the store cannot be opened -- a fresh clone with no `kyosei.db`, a
schema this build does not speak -- the identity line says the CLI's sentence and never
`0 records`: "nothing there" and "I cannot see it" are different claims.

**When nothing answers at this machine's kyo serve**, the window shows one state:
*kyo serve is not running*, naming `kyo serve --install --apply` and `kyo serve`, with
**Check again** (`tests/serve-down.spec.ts`). The app starts no server itself. While
that state shows, the window asks again every `SERVE_REPROBE_MS` (3 s) by itself and
clears it once the server answers (KYO-449.c8). A server that took the connection and
has not answered inside `PROBE_TIMEOUT_MS` (20 s) is *slow to answer*, a note in the
status bar, never the not-running state (c6/c7): `/v1/version` runs under kyo serve's
one command lock, so a loaded server answers it late. The state also offers **Connect to
a server…** (KYO-473): the connect dialog, opening the chosen project in a window of its
own, so a machine that runs no kyo serve -- a laptop whose projects all live on a dev
box -- is still a client of another's. The status bar names where every window's project
is served from, `KYO @ this machine` or `KYO @ <host>:<port>`, through the recents' own
label helper (`src/shared/served.ts`).

**Is it a kyo this app reads** (KYO-449, KYO-443). `KYO_ACCEPTS` in `main/kyo.ts` is
the release range the app reads; `tests/seams.spec.ts` fails when it does not admit
`pyproject.toml`'s own version, so bump both in one commit. A window's server is
judged by its `/v1/version`: too old, too new, or not Kyosei's is an overlay worded as
KYO-440 specifies, naming the server's address. The kyo this app runs itself on this machine
(`kyo projects add`, the terminal panes; the checkout's when unpacked) is judged by its
own `kyo version --json` on every facts read -- launch, project switch, Check again --
and a kyo it does not read is a status-bar chip that opens the same states.

### 3. Currency in the status bar

The read path inherits the db-vs-export gap (KYO-74), so the status bar renders the
direction, and can show all four states:

| CLI `direction` | status bar |
|---|---|
| `in-step` | store current |
| `db-ahead` | database ahead |
| `export-ahead` | export ahead |
| `cannot-tell` | currency: cannot tell |

The direction is produced by `kyosei.currency` and carried in `kyo list --json`'s
`db_export_currency` envelope; this app maps the CLI's word to a label and puts the
CLI's own sentence and remedy in the tooltip. There is **no comparison in
JavaScript** — a check scans `src/` for `_allocations.json`, `FROM allocations` and
`next_value` and fails if any appears. A direction this build does not recognise
becomes `unrecognised`, and a CLI that could not be run becomes `unavailable`;
neither is folded into `cannot-tell`, which is the CLI's own verdict about the
store and not a place to put the app's failures.

`kyo list --json` is the surface because it exists here today: this store saves no
Bases and has no `kyosei/bases.yml`, so a `kyo base run --json` poll would need a
config file written into a store this app must not write to.

### 4. The terminal is a real shell — `src/main/terminal.ts`

Ptys (`@lydell/node-pty`) with their working directory at the project root, drawn by
xterm.js, on the login shell's environment (KYO-229). Typing `kyo` there reaches the
`kyo` on the login PATH — the same one Terminal.app runs — because the app puts
nothing in front of that `PATH` (KYO-450). Until KYO-450 it wrote `kyo` and `kyosei`
shims into `<userData>/bin` for its bundled runtime and prepended that directory; with
the runtime gone, a pane running one build while Terminal.app ran another is no
longer a state this app can produce.

A popped-out terminal gets its own pty; ptys are keyed by renderer and killed when
the window goes.

**A pane is up to two ptys (KYO-293).** One whose process *is* a tmux client
(`attach-session`, spawned directly with no shell in between) and one bare login
shell, spawned the first time the leftmost control is used. Each has an xterm of its
own, shown one at a time, so the screen that is hidden keeps everything on it — and
neither is ever typed into on the other's behalf. Why that matters is "The attach is
a pty, not a typed command" below.

### An earlier build's command line tools, removed at launch (KYO-450)

KYO-315 gave the app **Kyosei → Install Command Line Tools…** and **Remove Command
Line Tools**: they symlinked `kyo` and `kyosei` into the first directory on the login
PATH that was writable without a password (`~/.local/bin`, then `~/bin`, then
`/usr/local/bin`), pointing at per-build shims in `<userData>/cli/<shim owner id>/`
that exec'd the bundled runtime. KYO-450 removed both menu items with the runtime:
`kyo` is the user's own install now.

What an earlier build left behind would break without them: the shims exec an
interpreter that went away with the old app, so a link still on PATH answers `kyo` in
every terminal with a failure, and `uv tool install kyosei` refuses to overwrite an
executable it did not write. So **every launch removes them, and only them**
(`removeRetiredCli` in `terminal.ts`):

- **a link is this app's when it points into this app's own `<userData>/cli/`** —
  KYO-315's ownership rule, since only this app ever wrote there. It looks at the
  `kyo`/`kyosei` names in the three directories KYO-315 could have linked into, and
  nowhere else. A regular file (a uv tool's), a link anywhere else, and a link into
  *another* userData (a second install pointed elsewhere by `KYOSEI_USER_DATA`) are
  left exactly as they were;
- then it removes its own `<userData>/bin/` and `<userData>/cli/` shim directories, so
  a tmux session an older build prepared, whose `PATH` still names `<userData>/bin`,
  falls through to the login PATH's `kyo` instead of a dead shim;
- **it says once that it did**: a dialog naming the links it removed and
  `uv tool install kyosei`, shown only when a link on the reader's PATH went. The next
  launch finds nothing of the app's and says nothing. Removing only the shim
  directories is the app's own business and goes to stdout.

A dev build (`npm start`) shares `userData` with the packed app (KYO-189, below), so
launching the new dev build performs the same migration for the packed app's links —
which is the intended order: the old app's links are exactly the ones that would
dangle. `seams.spec.ts` drives the rule over a temporary HOME and userData, and
`user-data.spec.ts` plants the shim directories in the suite's own userData and
checks a real launch takes them away and writes nothing back. Nothing in either check
names a real `~/.local/bin`.

The sibling record is the hook half (KYO-316): what a hook says when `kyo` is not on
PATH at all — since KYO-450, `uv tool install kyosei` and no app action.

## The terminal pane is the orchestrator's session (KYO-115, KYO-474)

The app is built for running Claude Code as an orchestrator and seeing and answering it
from the app, above all its permission prompts. **tmux is the whole mechanism, and MCP is
not involved.** The Claude process never moves: `kyo controller start` runs it in a
detached session on the dedicated `tmux -L kyosei` server (KYO-458), and this pane is
one more client of that session, as `kyo controller attach` in any terminal is. tmux
mirrors the live screen to every attached client, and keystrokes from any of them reach
the same claude. Nothing crosses a "terminal boundary" except tmux's own
client-server protocol.

**The server is the orchestrator's, never the default (KYO-474).** Until KYO-474 the pane
used the machine's default server and made a `kyosei-<project home>` session of its own
there — so on a machine whose orchestrator ran under `kyo controller` the tab never
showed it at all. The pane's
socket is now the one `kyo controller` resolves (`orchestratorSocketPath` in
`src/main/tmuxenv.ts`): `$KYOSEI_CONTROLLER_SOCKET` when set, else `-L kyosei` with
`TMUX_TMPDIR` dropped, which tmux resolves under the real `/tmp`; tmux commands and
clients run with `TMUX`, `TMUX_PANE` and `TMUX_TMPDIR` scrubbed, as the CLI's are.
`tests/tmux-owner.spec.ts` checks that the app and the CLI name the same socket —
their constants, `kyo controller list --json`'s own spelling, and tmux's own `-L` rule
measured on a private name. The app never creates, lists or ends anything on the
default server, and a session a person made there is left alone.

### The session name is the prefix

The session `kyo controller start` makes is named after the project's prefix (`KYO`),
and the pane reads the prefix from the kyo serve that serves the project — this
machine's or the dev box's — so there is nothing to derive and no `kyo` subprocess.
Every dispatch worktree of one project shares it. The pane states the name, and the
line to type in any other terminal on that machine:

```
kyo controller attach <prefix>
```

**One pane, one project (KYO-218).** A pane is opened from two facts about a project —
the directory its pty starts in, and the prefix its session is named after — and only
the second costs a question of a kyo serve. So the two are read a subprocess apart,
and a human switching project inside that gap once produced a pane running in the new
project's directory while attached to the previous project's session, with nothing on
screen saying so. `askedAboutOneProject` (`src/main/terminal.ts`) is the rule the pair
is now read under: the answer is used only if the project has not moved since the
question was asked, and otherwise the question is asked again — of the new project.
That is not a retry. A failed answer is never re-asked; only an answer about a project
nobody is looking at any more is, and each re-ask takes another human switching
projects to provoke.

### Attach-to-existing is the only flow, and the tab opens in it

The session of interest exists before the tab opens, because that is where the
orchestrator is already running — so **opening the terminal tab lands in this project's
orchestrator session** (KYO-293.c1, KYO-474.c2). When there is none, the pane says so,
offers **Start orchestrator** — the claude tab's own KYO-460 flow, `kyo controller start`
— and attaches once it runs; it creates no session itself (it used to, on the default
server, before KYO-474). One code path does the attach (`attachPane` in
`src/main/main.ts`), so "opened the tab" and "clicked the project's pill" cannot drift
apart.

That default was reversed once and put back. KYO-283 made the pane open **bare**,
because there was then no way from the app to a plain shell at all. That was withdrawn
on review: a `claude` started in a bare terminal ends when the app quits, and the
session is lost with it, so the pane opens in the project's session. The bare shell stays — it is what the leftmost control
shows — and it is no longer where a pane starts.

KYO-474 removed the creating half, and with it the preparing half: the app used to
`new-session -d` and then `set-environment` its own `KYOSEI_*` into the session before
attaching (KYO-115.c3, KYO-265). The session is the orchestrator's now, and writing into
its environment would be writing into somebody else's; `seams.spec.ts` fails on any
`new-session`, `set-environment` or `show-environment` in the app's sources.

### A dev box's session, over the person's own SSH (KYO-474.c3)

A window on a dev box's project has no tmux to attach with here: the session is on the
other machine. Its client pty runs `ssh -t <host> zsh -lc 'kyo controller attach
<prefix>'`, built by `ssh.ts`'s one argv builder (`orchestratorAttachArgv`, with `-t` and
`BatchMode=yes`) to the SSH host Start orchestrator uses (`sshHostFor`), so the far end's
CLI resolves its own socket and target. Its pills are that dev box's `kyo controller
list --json` over the same SSH, asked at most once per `REMOTE_STATUS_TTL_MS` and afresh
after any attach or end. The only signal back is the ssh pty ending, with a status and
its last screen: the pane repeats what ssh or kyo said verbatim (`saidByClient`) and
offers Reconnect, or Start orchestrator where the listing shows no session. There is no
bare shell there — the project's directory is on the other machine — so the leftmost
control only detaches. No terminal is served through kyo serve: its loopback,
read-only stance (KYO-448) stands. "No terminal here" remains only for a dev box
whose SSH host ssh could not be handed. `tests/remote-terminal.spec.ts` drives all of
it against an ssh stand-in.

### The attach is a pty, not a typed command (KYO-293.c2)

KYO-115 ran a POSIX script in the pane's own shell (`sessionScript`) which `exec`'d
the client, so a detach left an empty pty with no shell to return to. KYO-283 stopped
`exec`ing and made the attach an ordinary foreground command **written into** the pane's
pty, and that had a cost: with a long-running command in the bare shell, the attach
could not be typed, so the session could not be opened.

A pty's input goes to whatever holds the terminal, and that was `sleep`, not the
shell. No care about *which* command is typed fixes a design that types.

So the client is **its own pty**: node-pty is given the tmux binary as the file and
`clientAttachArgs(socket, session)` as the argv. There is no shell in that pty to be
busy and no command line to be interrupted; the bare shell keeps its own pty, its own
screen and its own running job, untouched by every attach. Two further things fall
out of it, and both are things KYO-283 could not have:

- **The app knows its own client's tty by construction** (`ptyTty`), because it
  spawned the process. `detach-client -t <tty>` is therefore exact rather than a
  guess, and KYO-283's whole "attached somewhere and cannot name my own client"
  branch — which ended in handing the reader a command to type — is *gone* rather
  than left unreachable.
- **A client that goes away is a signal.** The reader's own prefix-d and the session
  ending are both invisible to this app until the client process exits; when it does,
  the pane falls back to the bare shell and asks the server which happened
  (`clientGoneNotice`), because the two need different sentences.

`kyo.ts` is no longer the only file that may start a process: `tmuxctl.ts` is the
second, added by KYO-283 with its reason, and `seams.spec.ts`'s allowlist names it.
What has not been weakened is the tmux rule — **every argv this feature builds goes
through `tmuxArgs(socket, …)`**, and the same spec fails on a tmux subcommand written
outside that funnel (KYO-254, KYO-293.c5). The funnel lives in `tmuxenv.ts`, beside the
pane's environment and tmux resolution, because none of it needs the pty and the headless
server's orchestrator launcher needed all of it (KYO-388; since KYO-460 the orchestrator row
runs `kyo controller` instead and starts no tmux of its own).

### `kyo` inside the shared session

A tmux session's environment is fixed by whoever created it. Until KYO-450 that mattered
for `kyo`: the app prepended its shim directory to a session's `PATH` (in the first
shell of a session it created, and by `set-environment` on one PyCharm had made), and a
shell already running there kept the `PATH` it started with — so the pane told the
reader to `export PATH=<shim dir>:$PATH` by hand. With the shims gone there is nothing
to prepend: `kyo` in every shell of the session, whichever side started it, is the
login PATH's, the same build the app runs. The session's `PATH` is left exactly as its
creator made it.

**The session's `KYOSEI_*` was the app's (KYO-265; retired by KYO-474, which stopped
the app creating or preparing sessions).** tmux gives a new session the
*server's* global environment — the environment of whichever process started the
server, refreshed from the attaching client only for the names in
`update-environment` — so the pty environment the app builds reaches the tmux client
and stops there. A consumer met the consequence: a suite-launched app had started the
machine's default server from a fixture, its global environment carried the fixture's
`KYOSEI_HOME` under `/var/folders`, and every pane the real app opened afterwards ran
a `kyo` that put dispatch worktrees there and called the real ones "already-gone",
while nothing in the app had set the variable or could see it. So the app states every
`KYOSEI_*` in the pane's intended environment and removes, by name, every other
`KYOSEI_*` the server or the session carries: in the first shell of a session it
creates by `unset` before it execs (`sessionShellCommand`), and for every later window
by `set-environment -r` (`sessionPrepareCalls`, issued from `tmuxctl.ts` with the
socket named, before the pane attaches). Only that
namespace — the CLI's variables and the app's own, the ones whose value decides where
`kyo` writes; `PATH` and the rest of PyCharm's session are left alone. The same limit applies: a shell already running keeps what it had.

### Without tmux, the pane says what it is

No tmux on `PATH` and the pane opens on KYO-111's bare shell instead of in a session —
and the status line above the screen says so, because a pane that is a private shell
and a pane sharing a live Claude session look identical until one of
them says which it is (law 6). The same fallback covers a project whose per-project
home could not be read (there is then no name to share a session under) and a tmux
that refused to start one: the pane is a shell carrying tmux's own words, rather than
no shell at all. Detection is a `PATH` scan and runs no subprocess: `tmux -V` would be
a process between the human opening the pane and the pane having a shell in it.

`KYOSEI_TMUX` names a tmux binary, or turns the mechanism off (`off`, `0`, `no`,
`none`, `false`). Not a test hook: a macOS app launched from the Finder inherits a
minimal `PATH` that has never heard of `/opt/homebrew/bin`, so "cannot find the tmux
that is plainly installed" is the ordinary case for a packaged build. A binary named
there that cannot be executed is refused *by name* rather than falling through to
the `PATH` scan — someone who named a path said which tmux they meant. The checks
drive this same variable, as they drive `KYOSEI_HOME` and `KYOSEI_PROJECT_ROOT`.

That paragraph stopped being a prediction with KYO-176: the packaged build launched
from the Finder inherits `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, measured, and `tmux`
is at `/opt/homebrew/bin/tmux`. The pane is a private shell there and says so.

### The app asks the login shell for its environment, once (KYO-229)

And then it stopped being the right behaviour. A Finder-launched app that says "I
cannot see your tmux" is a true sentence and a useless one: a Finder launch showed it
beside `.zshrc: command not found: npm`, both from the same
four-directory `PATH`, in an app whose whole purpose is to host the orchestrator session.

So the app **asks**. Once at startup — after the first window has painted, so nothing
waits on anybody's `.zshrc` — it runs

```
$SHELL -ilc 'printf ... ; /usr/bin/env ; printf ...'
```

and reads the environment back out from between its own two markers
(`src/main/shellenv.ts`). That environment is what every pty is spawned with and what
the tmux lookup searches.

**This is not KYO-185.c4 being reversed.** That criterion says no `PATH` is synthesised
on the user's behalf, and nothing here synthesises one: there is no list of likely
directories anywhere in this app, and `seams.spec.ts` still fails if any file assigns
`/opt/homebrew/bin` or `/usr/local/bin` into a `PATH`. Asking a shell what its own
environment is, is the opposite of guessing — it is the same answer that shell gives
its own terminals, obtained from the shell.

What it *does* change is what the app is entitled to **claim**, which is the half of
KYO-185 that matters. With a capture in hand the `PATH` searched is the reader's own,
so the message says "no tmux on `/bin/zsh`'s own PATH" and drops the hedge. With no
capture — the shell would not run, it hung, `KYOSEI_LOGIN_SHELL` is off — the app is
back in KYO-185's position exactly, says which of the two things it cannot tell apart,
and names why there was no capture. Both wordings are checked, each with its world
stated rather than inherited from whoever is running the suite.

| what | where |
| --- | --- |
| the flags, and why all three | `LOGIN_SHELL_FLAGS` — `-l` sources `.zprofile`, `-i` sources `.zshrc`, `-c` runs one command. A capture with only `-l` misses everything `.zshrc` does, which can include `npm`. |
| the merge rule | `terminalEnvironment`: **`PATH` comes from the login shell; every other variable this process already carries wins; anything the capture has and this process lacks is added.** A variable already in this app's environment was put there by whoever launched it and is an instruction. `launchd`'s `PATH` is not an instruction, it is an absence — which is why it is the one variable the capture replaces. |
| the seam | `KYOSEI_LOGIN_SHELL` names a shell to ask, or turns the capture off (`off`, `0`, `no`, `none`, `false`). The consumer's own switch, and how the checks supply a login shell whose exports they know. |
| every way it can fail | `SHELL_ENV_OUTCOMES` — `captured`, `turned-off`, `unsupported-platform`, `shell-would-not-run`, `timed-out`, `shell-said-nothing-we-could-read`. Each carries a sentence; none throws; the capture is bounded at `LOGIN_SHELL_TIMEOUT_MS`. |

`shellenv.ts` is the second file in this app allowed to import `node:child_process`,
and the guard that used to name one file now names two with a reason each
(`MAY_START_A_PROCESS` in `seams.spec.ts`). What that guard was protecting is
unchanged and still checked separately: `.venv` — this project's own interpreter —
appears in `kyo.ts` and nowhere else, so there is still exactly one route from this app
to the CLI.

### "Once" means once it worked (KYO-266)

The capture above was memoised once per process, **including its failures**, and that
is one transient second costing a whole app session. In the observed case an instance
asked while the machine was short of memory (KYO-238) and got nothing back, so `ptyEnvironment()` fell back to `launchd`'s four directories, the tmux
lookup searched them and found nothing, and every pane that instance opened afterwards
was a bare shell with no `tmux`, no `npm` and no `claude` on its `PATH`. The three
per-project sessions were up and healthy the whole time. Editing `.zshrc` did
not help; relaunching the app did.

So a **success** is still cached for the process — the login shell's environment is a
property of the machine and the user, and re-asking spends a shell start-up for the
same answer — and a **failure** is not:

| | |
| --- | --- |
| what re-asks | `RETRIABLE_OUTCOMES` — `shell-would-not-run`, `timed-out`, `shell-said-nothing-we-could-read`: the outcomes where a shell was actually tried, so a second ask can answer differently. `turned-off` and `unsupported-platform` are decided by this process's own environment and by the platform, cannot change while it runs, and their own sentences already name the change that helps. |
| when | on the next call — the next pane, or the next question about tmux. Nothing loops and nothing retries in the background: the only thing that re-asks is a human opening another pane. |
| what it costs | one login-shell start-up per pane opened while the shell is still failing, paid by the pane that asks, bounded by `LOGIN_SHELL_TIMEOUT_MS`. |
| what the pane says | `WILL_ASK_AGAIN`, appended to the no-tmux sentence — "it asks again the next time a pane opens … open another pane here — you do not have to relaunch the app". One predicate (`worthAskingAgain`) decides both the retry and the sentence, so a pane cannot promise a retry that will not happen. |

And the outcome is no longer written only to stdout, which a Finder launch discards —
the launch whose `PATH` is the interesting one was the launch that kept no record, and
this record's own diagnosis had to infer the timeout from the clock. Every ask appends
one JSON line to **`<userData>/shell-env.log`**
(`~/Library/Application Support/@kyosei/desktop/shell-env.log`): the time, the outcome,
the shell asked, how long it took, and which attempt it was. No variable's *value*,
ever — the environment is the likeliest place on a developer's machine for a token to
be. The file is capped at `SHELL_ENV_LOG_MAX_BYTES` and trimmed to the newest
`SHELL_ENV_LOG_KEEP_LINES`, because the retry turned a line per launch into a line per
pane on a machine whose shell stays broken.

### The app owns the session, and never ends it (KYO-229)

The intended use is to start `claude` in tmux, so that it outlives every app, and to
drive it through the claude tab. That makes one property load-bearing:
**nothing in this app ends a tmux session.** Quitting kills this app's own ptys, and in
tmux mode a pty is a *client*, so the quit detaches and the session — with whatever is
running in it — stays up. Relaunching attaches to the same session, scrollback and all.

Stated as a mechanism rather than left as an absence, because an absence is not
checkable: `TMUX_SUBCOMMANDS_REFUSED` in `src/main/terminal.ts` names `kill-session`,
`kill-server`, `kill-window` and `kill-pane`, and `seams.spec.ts` scans every source
file for them. The day somebody adds a "close session" control, that check goes red
rather than someone losing an orchestrator session to it.

The pane says so too. Under tmux the status line states that the app owns the session
and never ends it; without tmux it states the opposite in as many words — *this shell
ends when this app quits, and anything running in it ends with it* — and names
`brew install tmux` as the one change that fixes it. Those two sentences are the whole
difference between the two panes, and a reader deciding where to start an orchestrator
session cannot see it any other way.

**tmux is optional, never integral.** No other feature of this app depends on it: with
none installed the terminal tab is an app-owned shell and everything else is unchanged.
**Bundling a tmux inside the app was considered and deliberately deferred** to a
follow-on record (KYO-229.c3) — nothing here ships one, and the app's whole answer to a
machine without tmux is the sentence above.

`tests/tmux-owner.spec.ts` is where this is checked, and it is the one spec file whose
launches deliberately carry **no `KYOSEI_TMUX` at all**: c1 asks that the pane attach
without any such setting, so the only thing that can show it is a tmux the app found on
the login shell's own `PATH`. It supplies that shell itself (`makeLoginShell` in
`tests/fixture.ts`), which is also how the marker variable and the stub `npm` get
somewhere the app can find them. Everything tmux does for real — a session surviving
its client, a process surviving a relaunch — runs wherever a tmux binary is on `PATH`
and skips with its reason where there is none, on the pattern KYO-115 set. Every
session it starts is on a **private server** (`TMUX_TMPDIR`), so it can never see,
resize or kill one belonging to the human at the machine.

### The message names the instrument its reader has (KYO-185)

For a while it named the wrong one for half its readers. "Set `KYOSEI_TMUX`" is a real
remedy in a terminal — the reader is in a shell, that shell is this process's parent,
and an `export` reaches the next launch. From the Finder there is no shell in the loop
at all: the app was started by `launchd`, and the sentence named an action its reader
could not take, in exactly the situation the variable was created for.

Which of the two it is, is a **fact this process holds**, not a question for the
reader, and it is not a heuristic: it is whether a terminal is attached to any of the
process's three standard streams. That is the same question as "is there a shell in
the loop whose environment this app inherited", which is the same question as "can an
`export` reach it" — one question, answered by the operating system
(`launchOrigin` in `src/main/tmuxenv.ts`).

| how it was started | what the message names |
| --- | --- |
| `npm start` from a terminal | `export KYOSEI_TMUX=… in that shell and reopen this pane` |
| `open -a Kyosei`, or a double-click | ``run `launchctl setenv KYOSEI_TMUX …` in any terminal and relaunch this app`` |
| anywhere else with no terminal attached | set it in the environment the app is launched from — there is no one command to name, and inventing one would be worse than saying what has to be true |

`open -a` from a terminal lands in the second row and that is correct rather than a
near miss: `launchd` starts the app, so the reader's shell is *not* its parent and an
`export` there would reach nothing.

The two messages also differ in what the app is entitled to CLAIM. From a shell, the
`PATH` scanned is the reader's own, so "tmux is not installed, or it is not on the
`PATH` you launched from" is a strong statement. From the Finder the `PATH` is the
minimal one above, so a tmux that *is* installed is invisible — and the message says
that it cannot tell those two apart, rather than asserting the one it cannot know.

Still no synthesised `PATH`, and that is the same decision KYO-176.d1 made: those two
directory names appear in a sentence addressed to a person and nowhere in anything the
app searches or spawns with. Guessing on the user's behalf would make the app's
behaviour depend on a list somebody maintains by hand, and would hide precisely the
difference the message states.

### The status line is DOM, not text in the screen

tmux repaints the whole screen the moment a client attaches, so anything written
into the terminal buffer would be erased by the session it was describing. The line
sits above the screen instead, and every word in it is composed in the main process:
the renderer cannot see a `PATH`, a tmux binary or a session, so a sentence composed
there would be a guess about a world it has no access to.

### Where the rails went (KYO-122)

Nine icons in two columns. Sorted by what each was actually doing:

| was | is | why |
|---|---|---|
| records mode's `bubbles` / `graph` | nothing — deleted | Redundant outright: they focused a centre-pane view that the centre pane's own pill row already sets, in the pane it changes. Two controls for one setting, and the mode carried an `onViewChange` callback purely so the rail could not go stale against the pills. |
| records mode's `folder` / `calendar` | pills in the **left pane's own header**, above the listings | A real job — which tool window fills the left pane — moved to the pane it changes. |
| ideas mode's `calendar` / `folder` | the same | The rail's only non-redundant entries. |
| the right rail's `properties` / `list` | pills in the **right pane's own header** | Two view modes, and now they sit on the pane whose mode they are. |
| the terminal toggle | a button in the **status bar** | The strip it toggles is anchored to the bottom of the window, and so is this. |
| docs mode's four icons | nothing — deleted | They selected nothing: docs mode is still KYO-110's placeholder and those clicks were ignored. Law 6 — a button that silently does nothing states the opposite of an absence stating itself. |

### The terminal toggle hides the pane; nothing kills the session

The status bar's terminal button shows and hides the pane, PyCharm-style, and the
splitter goes with it. It sets `hidden` — the DOM, the xterm.js instance and the pty
all stay exactly as they were, so showing it again reveals the session mid-sentence
rather than restarting it. The record allows detaching and reattaching the tmux
client on toggle; this does not even detach. The `ResizeObserver` stops measuring a
hidden pane, because fitting to a zero-height box would tell the pty its screen is
one column wide — and in a session shared with another client, that resizes
*everyone*.

Killing a pty is a detach, never an end: closing the window, quitting the app and
popping the pane out all leave the session, and whatever is running in it,
untouched.

### What is checked, and what a machine cannot check

`seams.spec.ts` covers the session naming and its slugging, the `PATH`-scan detection
and its override, the pane's status line and the bare fallback, the pill row's order
and marking, the socket rule (`tmuxSocketPath`, `tmuxArgs`, and the scan that fails on
a tmux subcommand written outside the funnel), the argv a client pty is spawned with
(`clientAttachArgs`), what a pill click and the bare control decide
(`planPaneAttach`, `planPaneBare`), what a client that went away on its own says
(`clientGoneNotice`), and the argvs that create and prepare a session
(`newSessionArgs`, `sessionPrepareCalls`) — all Electron-free, most of it needing no
tmux at all.

What only tmux can establish is in `tmux-owner.spec.ts`, against a **private server**
(`TMUX_TMPDIR` under the run's own directory, so it can never see, resize or kill a
session belonging to a human at that machine): that opening the tab really lands in
the project's session and creates it when absent, that a pill click attaches **while
the bare shell is held in a `sleep`** and that command keeps running with its screen
intact, that the leftmost control detaches a client and leaves a job started before
the app running, that a detach performed from outside falls the pane back to its own
shell with the right sentence, and that quitting leaves the session and everything in
it alive with the screen painted on the next launch. It runs wherever a tmux binary is
on `PATH` and **skips with its reason where there is none**.

The one-project rule above has two checks, at the two altitudes it needs. `an answer
about a project that moved while it was being asked is asked again` (`seams.spec.ts`)
drives `askedAboutOneProject` directly, moving the project mid-await, with no Electron
and no timing; `a switch away from a launch project whose home is still being asked
lands in the new project's session` (`smoke.spec.ts`) builds the whole condition in a
real app — its own launch checkout, its own slow CLI — and asserts the pane's cwd and
its session name name one project. Both go red when the loop in `askedAboutOneProject`
is replaced by a single ask.

## The claude.ai pane (KYO-186)

A pane beside the terminal — not instead of it — showing claude.ai, opened on **this
project's own Remote Control session** when the store names one. The footer's
`claude` button shows and hides it; it starts closed, unlike the terminal, because
opening it runs a Chromium tab and may put a sign-in page in front of somebody who
did not ask for one.

### A `WebContentsView`, not an `<iframe>`

claude.ai sends `x-frame-options: SAMEORIGIN`, so a frame is refused outright. A
`WebContentsView` is separate top-level contents and the header does not apply to
it. It is positioned by the main process over a rectangle the renderer reports, so
the pane's body has no children and must have none — anything drawn there would sit
underneath the view.

This is also why KYO-171's "Terminal → /rc cannot be built" is right about what it
measured and too broad as a headline. It ruled out *integrating against* Remote
Control, which exposes no API, SDK or embedding surface. Rendering the first-party
web UI needs no integration surface at all.

### Signing in once (`persist:claude-ai`)

The view runs on a persistent session partition, so its cookies are written to
`<userData>/Partitions/claude-ai` and the login survives a restart. Every view in
the app shares that one partition: signing in once in one window signs in
everywhere, and popping the pane out is a second page on the same account rather
than the first page taken away from the window it came from. (That is where this
pane deliberately differs from the terminal beside it. A pty is one process with
one output stream, so popping it out has to *move* it. A web view's scarce, stateful
part is the login, and that lives in the partition, not in any one page.)

The view never gets the **shell's** preload: `window.kyosei` can write idea files and
open projects, and handing that to a remote page — however first-party — would make
every capability on it reachable from whatever that page runs. It has a preload of its
own instead, `claude-preload.ts`, which is the opposite kind of script: it exposes
nothing to the page at all and does one thing, described under "In claude.ai the link is
an anchor with no href" below and decided in KYO-321. Anything that would open a new
window goes to the real browser instead.

### Which session, and why the store rather than a scan

**The session writes its own Remote Control information into the store, from a
session hook.** `kyo sessions --hook` runs on `SessionStart`, `Stop` and
`SessionEnd` and maintains `kyosei/sessions.json` beside `kyosei.db`; this app reads
that file directly, the same way it reads the database directly.

The alternative — having the app scan `~/.claude/projects/<path-slug>/` for the
harness's `bridge-session` records — works and was measured, and is the wrong shape
for three reasons. It would put knowledge of the harness's private file layout in a
second consumer. It cannot tell a *running* session from a finished one, because
nothing holds a transcript open and a running `claude` does not name its session on
its command line. And, the one that decides it, **it cannot cross hosts.**

### The file is written by whichever machine runs Claude Code, and read from the store

This is the arrangement the feature exists for, so it is worth stating plainly.

`kyosei/sessions.json` is written by **whichever machine actually runs Claude
Code**, from that session's own hooks, and is read by whichever machine runs this
app, from the project's store. The two need not be the same machine. The motivating
case is a dedicated dev box: the project lives there, it is remote-mounted on a
laptop with no IDE, and the work is driven through web Remote Control. The box's
session hook writes the file; the app on the laptop opens the mounted project, finds
the store through `kyo`, reads the file across the mount, and the pane lands on the
session running on the box. **A scan of `~/.claude` would look at the laptop and
find nothing.**

It goes beside `kyosei.db` rather than "in the project directory", and those are the
same place only under the in-repo layout. Under the split layout the store is its
own repository elsewhere, and this app never assumes where that is — it asks `kyo`,
and reads `sessions.json` from whatever store root comes back. So one path is
correct under both layouts, and the rule is simply: **whatever arrangement lets this
app read a project's records lets it read that project's session file.**

**A project this app cannot see on disk falls back rather than failing.** If the
store is unreachable, has no session file, or has one this build cannot parse, the
pane opens `https://claude.ai/code` — which lists the account's own sessions. That
is not a consolation prize: on a machine where Claude Code runs somewhere else, it is
what the feature *is*, and it is most of what KYO-170's "bubbles" wanted.

### Resolution order, and what it says out loud

1. the Remote Control session running in **this project's own directory** — the one
   the terminal pane beside it is open in;
2. failing that, the most recently active session the store's file names at all —
   which is the cross-host case, where there is no local pane to match;
3. failing that, `https://claude.ai/code`.

Step 1 is resolved by matching the `cwd` each session *records for itself*, not by
asking the pty what is running in it. That is deliberate and is the same fact that
ruled out the scan: a running `claude` does not name its session on its command
line, so the app cannot ask. The session says so itself, in the file.

Every step degrades to a **named outcome** rather than a guess —
`this-project-session`, `running-session`, `ended-session`, `no-store`,
`not-written`, `file-unusable`, `no-linked-session` — and the pane's caption renders
the outcome and its reason on every resolution, deep-linked or not. `bridge-session`
is an *undocumented* record in a file format that can change without notice, so a
format change must turn the deep link off loudly rather than point the pane
confidently at the wrong session. `file-unusable` is never folded into
`not-written`: they have the same consequence and completely different causes, and
a user whose pane will not deep-link needs to know which one they have.

A URL that is not `https` on `claude.ai` is dropped and the resolution falls
through. The view carries a persistent logged-in session and the file is written by
a hook reading an undocumented format, possibly on another machine; a page this
build does not recognise costs the deep link and nothing else.

### It follows a project switch, with no restart

The pane is the second consumer of the switcher KYO-175 built. Switching projects
re-points the app at another store, the session file lives *inside* that store, and
`projectMoved` re-resolves and navigates every open pane — beside `endSessions`,
because the two are the same obligation. A pane still showing the previous project's
orchestrator session beside a board showing the new project is the defect KYO-175.c7
is about, one pane over. Nothing is cached: a cached answer is the single change
that would break this while every other check still passed.

### It follows a session that starts after it was shown (KYO-382)

A common sequence is to open the app, start `claude` in the terminal, and then open
the claude tab — which used to show *yesterday's* session, because the pane
resolved its target when it was shown and nothing asked again when the new session's
`SessionStart` hook wrote `sessions.json` a moment later. The project's kyo serve's
events stream (KYO-310, since KYO-459 the server's) carries a third kind, `sessions`,
with a fingerprint of its own, so a session write moves no sidebar list and
a record filed moves no pane. `main.ts` routes it to `claudeFollowsProject` for every
window on that project instead of to the renderer, and the pane moves **only when the
resolved URL changed**: the `Stop` hook rewrites the file at the end of every turn, and
reloading claude.ai under a reader mid-conversation for that would be worse than the
bug. A window on a dev box's project hears the same kind on that dev box's kyo serve's
events stream, the same way. A stream that drops -- the server restarted, a laptop that
slept -- is re-opened by `kyoserve.ts`, and told to the lists as `server`, so they read
again the way a first load reads them.

### What is checked

`claude-pane.spec.ts` covers every resolution outcome without a launch,
including all four failure paths, against a file **written by the real
`kyo sessions --hook`** — a hand-written fixture would let the reader and the writer
drift apart while both stayed green — and, for KYO-382, launches the app once to
assert that a `SessionStart` fired while the pane is open moves it (its pushed state
and the view's `loadURL`) and that a `Stop` which leaves the answer alone does not,
plus an Electron-free check that a dev box's server pushes the `sessions` change to a
connected laptop. `smoke.spec.ts` covers the pane in the real
app: that it opens beside the terminal and hides without destroying its page, that
the view is on a persistent on-disk partition under this app's profile, that the
caption names the outcome, and that it pops out through the same mechanism the
editor and terminal use.

What is deliberately not checked: anything inside the page. That is claude.ai's, not
this project's, and no check here signs in.

## Commands on a machine: the orchestrator row and New project (KYO-460)

The app changes a machine only by running ordinary CLI commands there, locally or over
the person's own SSH (KYO-452). `src/main/ssh.ts` is the one place those commands are
built and run; `controller.ts` and `main.ts`'s New project handlers call it.

- **One argv builder.** `commandArgv` turns a command's words into the exact argv: here,
  the words themselves, spawned with no shell (and `kyo` itself through `runKyo`, the one
  invocation); on a dev box, `ssh -o BatchMode=yes -o ConnectTimeout=10 -- <host> zsh -lc
  '<words, quoted>'`. A login shell, because a non-interactive `ssh host kyo` has no
  `~/.local/bin` on PATH (KYO-458.m1); `BatchMode`, because the app has no terminal to
  answer a password prompt; `--`, because a host is data and never an option.
- **SSH's stderr, verbatim.** Every failure shows what ssh or the far end printed —
  `Permission denied (publickey)`, `command not found: kyo` — never a paraphrase.
- **Each server entry carries its SSH host.** A dev box the app has connected to
  (`remotes.json`) runs its commands at its own host name unless an SSH host is set for
  it (`ssh-hosts.json` in the app's data directory) — from the connect dialog's *SSH
  host* field, or New project's. macOS needs Remote Login enabled on the dev box.
- **The orchestrator row** in the claude tab runs `kyo controller list --json` for its
  status and `kyo controller start --json <root>` for Start, on the machine that holds
  the project; a dev box's status is asked at most every 15 seconds (each ask is an SSH
  connection). On this machine a live session the project's hook recorded outside
  `kyo controller` still reads as the orchestrator (KYO-390). The app keeps no command of
  its own any more: Start runs `controller_command` from that machine's
  `$KYOSEI_HOME/settings.yml` (KYO-462), and the old `orchestrator.json` preference is
  no longer read.
- **Waiting for trust** (KYO-567). When `kyo controller start` or `list` reports the
  session `waiting-for-trust` — Claude Code's "trust this folder?" prompt is on its
  screen, so it has not registered with Remote Control — the row says so, naming the
  folder, and offers **Open terminal**, which opens the terminal tab on the session. The
  app never answers the prompt and never pre-trusts a folder (KYO-568). The wording and
  the offer are decided in `src/shared/controller-trust.ts`, checked without a window in
  `tests/controller-trust.spec.ts`.
- **New Project…** (File menu, and the rail's project menu) picks a machine — this one or
  a connected dev box — then a folder in a conventional picker: breadcrumbs, a folder
  list, double-click to descend, New Folder, Choose. The list comes from a listing
  command run there (`cd`, a marker line, `pwd -P`, `ls -1p`); only folder names reach the
  dialog. Choose runs `mkdir -p`, `kyo init` (which registers the project, KYO-455) and,
  when the box is ticked, `kyo controller start`, then says what was made and offers to
  open it in a new window.

Checked in `tests/orchestrator.spec.ts`, which never reaches a real host: `KYOSEI_SSH`
names the ssh program, and the checks hand in a stand-in that records its argv and runs
the far end's command under a HOME, ZDOTDIR and PATH of the run's own. `suiteEnv` sets
`KYOSEI_SSH=/usr/bin/false` for every other launch, and `KYOSEI_CONTROLLER_SOCKET` to a
socket nothing listens on, so no suite app reaches ssh or the machine's real
`tmux -L kyosei` server.

## The v3 read model (KYO-231)

Everything the v3 tabs render, produced in the main process so that **no tab derives
anything**. Four queries and a push, all reads, none of which can mutate a store.

| query | what it answers | where its facts come from |
| --- | --- | --- |
| `getRecordDocument(id)` | every child object of one record, in id order, with the description and links | `kyo show <id> --json` |
| `getAwaitingHuman()` | every open gate waiting on a person, across every record status, plus the count for the rail badge | direct SQLite |
| `getRecordsList({query, base})` | the records list, searched by id or title, or run through a saved base | `kyo base run` when a base is named, direct SQLite otherwise |
| `getActivity({finished})` | running dispatches and the most recent finished ones | direct SQLite for the store half, `gitfacts.ts` for git's, `kyo worktrees --json` for the unaudited-stop count |
| `getLatestBrief()` | the latest session brief as structured sections, every id intact | direct SQLite |

### Why each one asks what it asks

**The record document is `kyo show --json` and nothing else.** That envelope already
carries every child object *and* the obligation collapse, so reading it is **reuse** of
`docs_check.collapse_obligations` rather than a port of it — there is no second
implementation in this app to drift from the CLI's. A direct SQLite read would have
been a dozen SELECTs plus TypeScript re-derivations of the collapse and of the role
word, which is two rules with two implementations (law 3).

That is checked by name and against the record the criterion names: `the obligation
collapse is the CLI's own, on this store's KYO-215` compares the document's groups to
`kyo show KYO-215 --json`'s `docs_obligation_groups`, group for group and id for id,
and then asserts that no other file in `src/` groups an obligation of its own. It skips
with its reason in a checkout with no built `kyosei/kyosei.db` — the ordinary state of
a dispatch worktree, where it is gitignored.

**Awaiting-human is a direct read, and it matches on gate TYPE.** KYO-231's own
description calls `awaiting:human-run` and `awaiting:human-review` "tags"; the store
disagrees, and the correction matters. Both are members of `contracts.GATE_TYPES`, and
`tags_json` holds the *other* axis — the per-project vocabulary in
`kyosei/gate-tags.yml` (`ui-review`, `deploy-risk`), where the type says what KIND of
waiting this is and the tag says which thing is being waited on (KYO-102 / KYO-46).
Matching on tags would have returned nothing.

*Across every record status* is the point of the query rather than a detail of it: a
gate on a `working` record and a gate on an `open` one are the same claim on a person's
attention, and a view that only looked at open work would hide half of them. No
read-only command asks this — `kyo base run` with a gate filter (KYO-203) comes closest
and needs a base declared in `kyosei/bases.yml`, a file in a store this app must not
write to.

**The brief is read out of the store because `kyo brief` writes.** Every run of it
INSERTs a row in `brief_runs`, so the one command whose subject is briefs is the one
this app must never call (`KYO_WRITING_COMMANDS`). What the store keeps about a past
brief is a marker plus two ledger positions; the sections are the shift notes and the
ledger entries standing in that window, ids intact so a renderer can link each row
(KYO-230's resolver takes `FIX-S1` and `FIX-E12` without being told what they are).
Nothing regenerates the prose — the store never kept it, and rendering a freshly
computed board under a date three days ago would be inventing a document and dating it.

### The git half, and the poll

`src/main/gitfacts.ts` reads one worktree at a time: presence, dirtiness, branch,
commits past the dispatch's own base sha, the last commit's sha and subject, whether
that branch is contained in `main`, and whether a `.kyosei-report.json` is sitting
there. **A missing directory is a fact, never an error** — it is the ordinary end state
of a finished dispatch, because `kyo worktrees` removes them — so every path ends in a
named outcome (`read`, `no-worktree-declared`, `directory-gone`, `not-a-git-worktree`)
carrying a sentence, and nothing throws.

The git half is **polled**, and it has to be: a worker's `git commit` happens in
another process in another directory and fires no event this app could subscribe to.
The poll is armed by the first `getActivity`, so an app whose reader never opens the
activity tab never runs a `git` in anger; it stops on the tick that finds nothing
running; and it refuses to overlap itself, because a tick still reading when the next
is due is what turns a five-second interval into an unbounded pile of `git` processes.

`gitfacts.ts` is the third file allowed `node:child_process`. Routing these through
`kyo` was the alternative and does not answer: `kyo worktrees --json` reports presence,
branch and merged-state for every worktree the store has ever had plus a full stop
audit, and reports neither dirtiness, nor commits past base, nor the last commit
message. It *is* used for the one number it owns — the unaudited-stop count, whose
attribution is a real rule that reads the harness's own spawn sidecar (KYO-154) and
whose meaning is stated at length in that command's output (KYO-163). Measured at 0.24s
against this store's 144 dispatches, and it only reads: `--prune` is what removes
anything, because listing is the dry run (law 11).

### The classes are the brief's

`finished-but-not-moved`, `gated` and `terminated-no-report` are read off the store's
own columns exactly as `cmd_review` derives them — a dispatch that reported while its
record stayed `open` (KYO-87), a record with an open gate, a dispatch that ended with no
report. Nothing here invents a fourth or softens one, and a row can be in more than one:
a running dispatch on a gated record is not a worker who has gone quiet.

`ACTIVITY_FINISHED_DEFAULT` is **measured, not picked**: on 2026-09-11 this store held
139 reported and 5 terminated dispatches, of which 11 closed in the previous fourteen
days. A dozen is roughly the last fortnight of this project's work, which is the window
an orchestrator coming back to a session reads; 144 rows is not a view of anything.

### What is not delivered, and what is

`ActivityRow.recordStanding` is the empty string: the role word is not a column in the
`records` table, and a direct read will not invent one. The record document carries it
for the page that needs it. Everything else the record's own description lists is
there — worktree path and presence and dirtiness, branch and commits past base and
latest commit message and merged-into-main state, report-file presence, ingest state,
verdict counts, the unaudited stop count, and the derived classes.

## `kyosei://<project>/<id>` — deep links (KYO-230)

A link in the orchestrator's message that opens the record and highlights the gate,
claim, criterion, note, evidence, flag or dispatch it names. Three surfaces, one URL,
and none of them knows how to read an id:

| where a link is clicked | how it reaches the app |
| --- | --- |
| the **claude tab** — a link in the orchestrator's own message | the view's `setWindowOpenHandler` and its `will-navigate`, both in `main.ts`, both deciding through `decideClaudeLink` |
| **anywhere else on the Mac** — a claude.ai tab in Chrome, a terminal, another app | `app.on("open-url")`, and on platforms that put it on argv, `urlFromArgv` at launch |
| **inside the app** — a `[[KYO-226.g1]]` in an editor or a rendered record | KYO-233. Not here: that is a renderer opening its own tab, and the bridge deliberately has no method that takes a URL |

`src/main/links.ts` is the whole of the grammar and is Electron-free, so every shape
and every refusal is driven directly in `tests/links.spec.ts` without launching
anything.

### One place that knows an id has a shape

`ID_SHAPES` is a table: the pattern, the page it opens, and an example. The matcher
walks it, and the refusal for an unknown shape is *generated from it* — so the list a
reader is shown cannot go stale against the list the matcher uses. Child ids resolve to
their **owning record** with themselves as the anchor and their kind spelled out
(`criterion`, `gate`, `claim`, …), which is what saves the renderer from ever parsing
an id.

Three outcomes, not two, and the third is the one worth stating. `KYO-L7` (a typed
link), `KYO-E41` (a ledger event) and `KYO-W3` (a sweep run) are **real ids with no
page**: a link names an edge between two records and an event names a ledger row, so
there is no single page either could open. They are refused as `not-openable` and never
folded into "I do not know what that is" — the two have completely different causes and
a reader needs to know which they got. (The criterion's own list of shapes names twelve;
`kyosei.ids` mints seventeen. The resolver knows all seventeen.)

The child letters are a **copy** of `kyosei.ids.CHILD_KIND_BY_LETTER` — there is no
importing a Python dict — so `the child letters this app knows are the CLI's own,
letter for letter` reads that file out of the checkout and compares them. KYO-137 is
why: it added two child kinds and had to remember to widen a regex elsewhere, which is
exactly the drift a second copy invites.

### The scheme is registered twice, for two different audiences

`build.mjs --pack` passes `protocols:` to `@electron/packager`, which becomes
`CFBundleURLTypes` in the bundle's `Info.plist` — that is what makes `launchd` start
**this** app for a link clicked in Chrome. A development `electron .` has no plist of
its own, so it claims the scheme at run time with `setAsDefaultProtocolClient`. Neither
covers the other, and `verifyBundle` reads the built plist back rather than trusting
the option, on the same argument as everything else it re-reads.

**There is deliberately no `requestSingleInstanceLock`.** The usual shape for a
URL-handling app is one instance everything funnels into, and this app is not that: it
opens several shell windows on purpose and its checks launch it dozens of times. On
macOS a running app receives `open-url` directly without any of it.

### A link for a project this app does not know is refused

Matched on the store's own prefix *or* the project directory's name, either case,
against the open project and the recents — with the open one first, so a prefix two
projects share resolves to the one the reader is looking at. A link that names neither
is **refused, naming the projects it does know**, and never opened against whatever
happens to be open: a reader put in front of the right id in the wrong project has no
way to tell, because the ids look identical.

A refusal crosses the bridge too (`LinkDelivery`), and that is the decision: a
`kyosei://` link was unambiguously meant for this app, so "nothing happened" is the one
response it may not get (law 6).

### What ships here, and what is a placeholder

The main-process half is complete: the scheme, the interception, the resolver, and a
typed `OpenRequest` on `CHANNEL.openTarget` carrying the target, the anchor, the anchor's
kind, the project root, the source and the column asked for. The renderer half — the
record page's anchors, the highlight, the "you were sent here" strip — is KYO-233.
Until it exists, `deliverLink` narrates each link on the app's own stdout, which is
what a human debugging a link that went nowhere would look at first and what the checks
read.

`tests/links.spec.ts` drives both Chromium paths inside the **real** claude view: it
loads a `data:` page into that view over CDP — no production seam takes a URL — and
clicks an anchor (`will-navigate`) and a `window.open` (`setWindowOpenHandler`). It then
asserts the view never left the page it was on, because handing a `kyosei://` link to
`shell.openExternal` would "work" — this app registered the scheme, so the OS would
route it straight back — and is not this.

### In claude.ai the link is an anchor with no href, so the gesture is a click (KYO-263)

**A `kyosei://` link written into a claude.ai message did nothing when you clicked it,
and that was the site's doing, not this app's.** Its message renderer keeps the link's
words and **strips a non-http(s) `href`**, so what the reader is looking at is styled
text with no target: neither `will-navigate` nor `setWindowOpenHandler` ever fires, and
there is nothing to intercept. Copied from the live page (KYO-263), with all three
forms in one message:

```html
<ol dir="ltr" start="1"><li>A markdown link: <a target="_blank" rel="noreferrer">KYO-263</a></li><li>A bare URL: kyosei://KYO/KYO-263</li><li>A bare id: KYO-263</li></ol>
```

**Only the first is clickable, and that is the whole shape of the feature.** A markdown
link survives as an ANCHOR — one with no href, which is an element that does nothing in
the page when it is clicked, which is exactly what makes the click free to take. A bare
URL and a bare id are plain text: no element, nothing to click, and making them
clickable would mean walking and rewriting claude.ai's DOM. So the gesture is **a single
left click on a linked record id**, and the orchestrator's side of it is writing
`[KYO-263](kyosei://KYO/KYO-263)` — which the `/kyosei` skill says, and which still
opens the app when it is clicked anywhere else.

**What reads the click is a preload of this view's own, and its boundary is a decision
rather than a habit (KYO-321).** `src/main/claude-preload.ts`, bundled to its own output
by `build.mjs` and attached to the claude view alone: isolated world, `sandbox: true`,
`contextIsolation: true`; it exposes **nothing** to the page (no `contextBridge`,
nothing on `window`), mutates **no** DOM, reads nothing but the clicked element's text,
and sends that one string on one one-way channel (`CHANNEL.claudeTextClick`). It listens
in the capture phase for a left click with no modifier, takes `closest("a:not([href])")`
— so an anchor **with** an href is left entirely alone, because it is a real link — and
never calls `preventDefault`: the click goes on doing in the page whatever it did
before. The pointer cursor comes from `insertCSS` on `dom-ready`, which styles the page
from outside rather than putting anything of ours inside it.

**The main process treats what arrives as what it is: a string a remote page chose.**
`decideClaudeClick` in `links.ts` refuses it unless `event.sender` is one of this app's
own claude views (`ipcMain` is per process, so the channel is reachable from every
renderer), refuses a payload that is not a string, re-applies the length cap
(`MAX_CLICKED_TEXT` — a cap inside a renderer is one a renderer could be made to skip),
and resolves the text with the same grammar every other link uses. A `kyosei://` URL as
the anchor's text is read as the URL it is, `?column=` and all; a **bare** id is read
against the project its own prefix names, never against whichever project happens to be
open, because the right id in the wrong project is the one mistake a reader cannot see.
Shapes with no page (`KYO-L7`, `KYO-E41`, `KYO-W3`) resolve to nothing. Anything that
does not resolve is **dropped in silence** — one line on stdout, no dialog — because a
click in that view lands on ordinary prose far more often than on a record id. What
does resolve goes to `followLink` and `deliverLink`, so it lands in the same tab,
column, window-on-its-project and "you were sent here" strip as any other link.

`tests/links.spec.ts` clicks the element above, verbatim, inside the **real** claude
view, and asserts from the page's own world that `window` carries nothing of ours;
`tests/seams.spec.ts` asserts the boundary against the preload's source, so widening it
is a red check rather than a review someone has to notice.

**The right-click menu this record shipped first is gone.** It was built on
`context-menu` params (a selection, `Open KYO-261 in Kyosei`) and it never appeared: claude.ai raises its own context menu over the page,
so a native Electron menu can never reach a reader there (KYO-263.c3). The measurement
that went with it is still true and still worth knowing — over unselected text Electron
44's `context-menu` params carry *nothing*, so no word-under-the-pointer gesture is
available to any app — but it is no longer a route to anything here.

### Not here

Enumerating Remote Control sessions across machines. KYO-171 established there is no
external API for it and nothing here changes that: the store file works because the
project directory is shared, not because sessions became enumerable.

## Records mode (KYO-112, KYO-113) — the v2 renderer, deleted by KYO-232

**This surface no longer exists.** The three modes went with the v2 renderer; what
replaced this one is the records list in the sidebar, the `record` tab, and the `views`
tab (KYO-227). The section is kept because the reasoning in it is still the design
record — why the tree delegates grouping to the engine, why the status pill groups by
`standing`, why every read is a read — and KYO-233 and KYO-235 build against it.

The first mode whose panes hold the store rather than a sketch of it. Each pane
carries its own selector — the mode pills stay the only mode switcher. The left
pane's header offers its two tool windows (`Folder · Calendar`); the centre pane has
four static view pills, `Editor · Base · Bubbles · Graph`, and all four are surfaces;
the right pane's header offers its two (`Properties · Outline`).

Choosing a centre view leaves the left pane exactly as it was — the folder tree stays
put, because it is this mode's click-target list and a rendering that replaced it
would take the names away exactly when a reader wants to compare a dot against one.
One of each is current at a time, and since KYO-122 that is automatic rather than
arranged: the two settings are marked in the two panes that own them, where the one
rail that displayed both had to encode a `role` to stop one un-marking the other.

**Nothing in this mode writes.** That is a property of the wiring, not of the buttons
drawn: `main.ts` exposes six records handlers and all six are reads, so the renderer
has no method that could mutate the store. Underneath them, `assertRecordsRead`
refuses any `kyo` subcommand not on `RECORDS_READ_COMMANDS` (`base`, `show`, `next`)
at the one place arguments are assembled — an allowlist, so a command the CLI grows
tomorrow is refused until somebody adds it on purpose.

### `kyo brief` writes, so records mode never calls it

Worth its own heading, because it is the trap. `kyo brief` reads like a report and is
offered to orchestrators as the surface to read *instead of* the records — and every
run of it INSERTs a row into `brief_runs`. It is therefore the obvious command to
build a session-brief calendar on and the one command a read-only mode most needs to
avoid. It is named in `KYO_WRITING_COMMANDS` with that reasoning, and a check runs it
against a fixture and proves the row appears, so the day it stops writing this
decision becomes reviewable again.

Two consequences:

- The board classes come from `kyo next --json`, which shares `_ready_records` with
  `cmd_brief` — the same computation, including KYO-87's `settled_not_moved` and
  KYO-570's `blocked` — and
  writes nothing.
- Past briefs are `kyo briefs <id> --json` (KYO-454), asked of kyo serve; until KYO-459
  they were read out of `brief_runs`, `shift_notes` and `events` by SQL. What the editor shows is what the store *kept* of a brief: the run,
  the ledger position it closed over, and the shift notes standing at the time,
  verbatim. Not the prose it printed, which was never stored — rendering a
  freshly-computed board under a three-day-old date would be inventing a document.

### The Base engine answers the query

The folder tree and the Base view are two renderings of one query (KYO-45), and both
run `kyo base run <name> --json`. The app selects no rows and applies no filter rule
of its own. On top of a base the reader refines — search, `column:value` filter,
click-to-sort — computed in the renderer over the engine's own rows, labelled as
theirs, and saved nowhere: writing a refinement back into `bases.yml` is past the
seam and is a later record. A base the CLI refuses renders the refusal **verbatim**,
beside the currency, never as an empty table; the refusal names the declared bases
and the entry that would make the query legal, and a paraphrase would throw that away.

**The one rule implemented twice, and why.** `kyo base run` takes no `--group-by`
override, so a ribbon offering kind/status/scope over one saved base cannot ask the
engine for two of its three answers. `groupRows` is a port of `bases.grouping()` that
keeps every one of its decisions — group order follows row order, a list-valued field
is refused rather than bucketed, a null value is its own group and not the empty
string. It is not trusted: `tests/seams.spec.ts` declares a fixture base per grouping
and compares the port against the engine's own output for all three. The folder tool
also *states* which produced its groups (`data-grouped-by="engine"|"app"`), because a
surface implying the engine had answered would be claiming a provenance it lacks. The
fix is a flag on the CLI; see this record's report.

### Bubbles and the graph: two more renderings of the one Base (KYO-113)

Neither runs a query. Both take the `BaseResult` the centre pane already holds and
draw it, which is KYO-45 in as many words — "the renderings differ only in how they
draw the answer" — and is why choosing a base moves all four views at once.

**Bubbles.** Three bindings over the clusters: `group`, `color`, `size`.

- **Color defaults to `match group`** — one neutral hue per cluster, so color says
  exactly what position already says. That is the point: it makes choosing
  kind/status/scope the act of *adding* a dimension. The alternative (default to a
  field, warn when it duplicates the grouping) designs the redundancy in and then
  apologises for it.
- **Size is three buckets and never continuous.** Dot area is nonlinear in the
  radius — twice the radius is four times the ink — so no reader recovers a number
  from a continuous scale. The split is across the *distinct values present*, in
  thirds: a range split puts everything in "small" the moment one record has forty
  open gates, and a rank split puts two thirds of a hundred identical zeroes into
  "medium" and "large". `size: uniform` exists so the human can switch size off and
  see whether it was telling them anything, and a field every row agrees on says so
  in the legend rather than being drawn as a field of equal dots.
- **Grouping is the folder tree's grouping**, literally: `groupRows` lives in
  `src/shared/grouping.ts` and both processes import it. A check fails if that
  function is ever defined anywhere else.

**The graph.** Typed edges with an explicit edge-type filter, and the drawn types are
always **stated** in words rather than left to be inferred from the picture. Filtered
to `blocks` it is a dependency graph — the drawn subgraph is acyclic, so the nodes are
layered and a blocker sits above what it blocks; set to `all` it has a cycle in it, no
direction to read, and becomes the hairball the filtering exists to avoid. Which one
you are looking at is on the screen either way.

Three things it refuses to do quietly:

- The edges come off **the same base run**. `query._attach_links` puts a `links` array
  on every row for exactly this consumer, so there is no per-node query — its own
  comment says a graph that re-queried would be the second query path KYO-108.c4
  exists to prevent.
- The filter's options are the types the **drawn rows carry**. This app keeps no list
  of link types: `contracts.LINK_TYPES` is the CLI's registry and a copy here would be
  a second answer to what an edge may be.
- An edge whose other end is **outside the query** is counted and stated, never
  dropped. Concluding "this record blocks nothing" from a missing line would be wrong,
  and a narrower base (`open-work`) produces exactly that case.

Whether the drawn subgraph is acyclic is computed from the subgraph, not from a copy
of `contracts.ACYCLIC_LINK_TYPES` — same law, one dimension over.

Hovering a dot or a node shows a preview of the row the query answered with; clicking
opens that record in the Editor view. The pills are **static, not transient**: the
bindings and the edge filter live on the mode, so the round trip through the Editor
and back returns the reader to the picture they left.

### The record editor

Read-only, and gates lead the properties. That ordering is a claim about
simultaneity, not taste: a record can be working AND blocked, and a panel showing
`status: open` with the gates elsewhere lets a reader see one without the other.
Under each **open** gate go all of the record's claims, one line each — a
transcription of `render.claims_beside_gate`, decisions included: never truncated
(the claim a cap hides is the one the rendering exists to surface), never matched
(nothing here decides a claim answers a gate; that judgment silently unblocks work
when wrong), and a record with no claims says so rather than rendering an empty block
that reads as "compared and found nothing".

A dispatch renders the report its own `report_id` names, with any superseded ones
named and counted (KYO-90) — picking the newest instead would be this app deciding
which account of a dispatch is authoritative when the store already has.

Finished-but-not-moved (KYO-87) is its own class, carrying the CLI's own remedy
verbatim, and is never folded into `ready` or read as `done`.

### Currency, on every surface

Every records payload carries a `CurrencyFacts` and every surface paints it. A table
of rows is exactly as convincing when the database is behind its export, so the
status bar answering on their behalf is not enough (KYO-74). `currencyFrom` maps one
`db_export_currency` envelope for all of them, so four renderings cannot disagree
about what the CLI said; where a command carries no envelope (`kyo show`), the
verdict is asked for separately rather than omitted.

## Ideas mode (KYO-114) — the v2 renderer, deleted by KYO-232

**This surface no longer exists.** What replaced it is the ideas list in the sidebar
and the `compose` tab, which in stage 2 is a read-only placeholder; KYO-234 makes it an
editor again. The section is kept because "The one write path" below is still the law,
and because the frontmatter and autocomplete reasoning is what KYO-234 builds against.

The mode the human writes in. Its left pane offers two tools in its own header — the
**scratchpad** (calendar slot) and **`ideas/` as a file tree** (folder slot) — and its
right pane offers the same two as records mode: the open file's frontmatter, and its
headings.

### The one write path

Everything else in this app reads. Ideas mode writes, and the boundary is
mechanical rather than described:

| what | where | who else can see it |
|---|---|---|
| the session scratchpad | `<per-project home>/scratchpad.md` | nobody — outside `ideas/` and outside the store |
| an idea file | under the project's `ideas/` | `kyo promote`, `kyo sweep`, any ideas Base |
| **anything under `kyosei/`** | **never** | — |

**The boundary is `kyo serve`'s: `src/kyosei/serve_files.py`** (KYO-456). Since KYO-459 the app writes no project file itself --
every write is a request to the project's kyo serve, this machine's or another's -- so
the one module that decides what may be written is that one, for every viewer. It
allows files under a project's `ideas/` and the project's session scratchpad, each
resolved to its real path first (a symlink under `ideas/` aimed at `kyosei/` is refused,
and `tests/seams.spec.ts` plants exactly that), and nothing else. The app's old
TypeScript copy of the rule, `assertWritable`, is gone with the app's own write path.

Two things still hold on the app's side:

- **The bridge has no vocabulary for it.** `saveScratchpad` takes text and no path;
  `saveIdea` and `createIdea` take a path *relative to `ideas/`*. No method on
  `window.kyosei` accepts an absolute path, so the renderer cannot ask.
- **No write replaces a copy it did not read.** Each goes with the ETag it read (or
  `If-None-Match: *` for a new file), so a file another viewer changed in between is
  refused with kyo serve's sentence rather than overwritten.

Ideas mode's own `kyo` allowlist is two commands: `base` and `worktrees`. Both are
reads. `worktrees` earns its place below; `promote` is refused by name, and it is
the one this mode is nearest to. Since KYO-439 no route in the app runs it at all:
`runKyo` refuses it (`assertAppMayRun`).

### The daily sheet's split view (KYO-122)

The scratchpad is where a person plans the next session, and the orchestrator's shift
note is the other half of that plan, so the two are worth reading side by side.

A **split view** pill sits beside the pop-out button, on the scratchpad only, and
splits the editor in half: the writer's own sheet on the left, the orchestrator's most
recent shift note on the right, read-only. Read-only is a property of the wiring, not
of the styling — there is no handler in this app that writes a shift note.

The note comes from `readLatestShiftNote`, which reads the newest row of
`shift_notes` directly rather than out of a brief's window. That distinction is the
point: `readBrief` returns the notes standing *at* a brief, so the handoff from the
session that has just ended — which is exactly the one this view is for — appears in
none of them. Three answers, because they are three facts: the note, a store holding
none yet, and a store that could not be read. It is asked for on the first split and
not before.

### The scratchpad is deliberately not an idea

It lives at `~/kyosei/<project>/scratchpad.md` — KYO-56's per-project home — so
**promotion and sweep cannot see it by construction** rather than by a rule
somebody has to remember. It is the human's own surface: to-do lists, draft
prompts, session notes, none of it swept, counted or required to have any field.
The pane states its full path, because it is the one file this app edits outside
the project directory and a human should be able to open it in any other editor.

Its ribbon is context-dependent (KYO-S56) — these three buttons belong to the
scratchpad, not to the editor, and an idea file has no ribbon at all:

1. **Session notes** — a `## Session Notes — <today>` heading at the **very top**,
   pushing everything below down, so the newest entry is the first thing in the
   file. The calendar tool lists those headings, which is what makes the pane's
   calendar slot a calendar.
2. **Clear** — only through an explicit confirmation modal, which says how many
   lines are about to go. There is no undo, and nothing else holds a copy.
3. **Idea** — the current selection becomes a new file under `ideas/`, via a modal
   asking a name and a destination folder. It writes a markdown file and nothing
   else: no record, no stamp, no ledger entry. Promotion is `kyo promote`'s, and a
   human's, later. An existing file is never overwritten.

**Where the home is, is asked of `kyo`** — the same weak point `facts.ts`
documents for the store locator, one directory over. `config.store_home()` +
`project_home_name()` is a real rule (`$KYOSEI_HOME` or `~/kyosei`, then the store
root's directory name under the split layout and the code root's under in-repo),
and a TypeScript copy would be a second implementation of where a project's files
live. So the app asks: `kyo worktrees --json` reports `home` as
`<per-project home>/dispatch worktrees`, and its parent is the home. Taking a
parent directory is a path operation, not a re-derivation — none of the three
things that could drift is decided here. `worktrees` removes nothing without
`--prune` (law 11), so it is a read.

That also gives the checks their seam: they set `KYOSEI_HOME`, `kyo` honours it,
and the scratchpad lands inside the fixture. **The seam the tests use is the seam
the consumer uses** — there is no app-only override and no test-only branch.

### The tree is the vault; the engine decorates it

Membership comes from a directory scan, always, and that is deliberate: a folder
holding nothing, an image, a `.base` file, and a file some base's filter excluded
are all things an Obsidian-like file view must show, so **no base's filter can make
a file disappear from the tree**. Every base whose `source` is `ideas` is then run
and its rows *decorate* what the scan found — the promotion stamp above all. The
tool states which happened. `scratchpad.md` is skipped at any depth, because
`ideas.idea_files` skips it: a consumer's leftover in-vault scratchpad is not an
idea, and listing it as one would disagree with every `kyo` command.

### Frontmatter is hidden in the editor and lives in the right pane

Two halves of one decision. The editor pane shows the body; the properties tool
renders and edits the frontmatter, and it remains **ordinary frontmatter in the
`.md` file** — nothing moves it anywhere, and Obsidian's own properties pane shows
the same block.

`promoted` and `promoted_at` render and never edit. They are `kyo promote`'s record
of what happened, and a text box over them would be this app offering to assert a
promotion that never occurred. The row says so rather than being greyed out with no
reason.

Saving **splices**; it does not re-render a file from a parse. A key the properties
tool does not model — a comment, a nested mapping, a quoting style — survives an
edit to the key beside it byte for byte, and a check proves it by editing one key of
a file that has all three.

A promoted section renders as **a stub, not a lock**: the two-line blockquote
`kyo promote` wrote is ordinary editable text, and the outline marks which heading
it belongs to.

### `[[` and `#` are closed sets (KYO-55)

`[[` offers only records that exist in the store, matching **id and title** (id
matches first). `#` offers only tags that already appear across `ideas/`. **A
dangling reference is not typeable by accident** — drift is prevented at the input
rather than flagged by a sweep a session later, when the writer has stopped thinking
about it.

A reader can still type `[[whatever]]` by hand; this is a text editor and pretending
otherwise would be a lock. What is guaranteed is that nothing the app *offers* can
produce a reference to something that is not there — and when a trigger matches
nothing, the popup **says so** rather than closing, so the reader learns the
reference does not exist while still typing it.

There is **no tag registry** in v1. `kyosei/gate-tags.yml` is the registry for gate
tags, a different vocabulary on a different object, and borrowing it here would offer
a set unrelated to what is written in these files. The tag set is the vault's own —
frontmatter `tags:` plus body `#tag`s — and the UI says that in as many words.

Records come over the read seams records mode already built (`readRecordTree`). This
mode adds no second route to a record.

### `ideas.py`, ported once, and not trusted

`shared/ideafile.ts` is the second rule in this app implemented twice, after
`grouping.ts`, and it is there for a forced reason: the Base engine answers
beautifully for files **on disk**, and nothing answers for the buffer the human is
**editing** — the properties tool has to know that a `- item` line just made `tags` a
list, and the outline has to show a heading typed a second ago. Shelling out per
keystroke to a process that reads the last saved bytes would answer a question nobody
asked.

So it is checked rather than believed: `tests/seams.spec.ts` runs the CLI's own ideas
base over a fixture vault and compares the port against the engine **file by file**
for properties, sections and stub ids. The stub the app recognises is likewise
produced by a real `kyo promote` rather than compared against a copied template, so
the day `ideas.promotion_stub` changes shape, this fails here.

### No editor dependency

A `<textarea>`. Held to the same standard as the node-pty note set: CodeMirror or
ProseMirror would be several hundred kilobytes and a plugin graph, configured back
down into "a plain markdown box with a completion popup". What a code editor buys —
highlighting, folding, multiple cursors — is not in this record; what this record
needs is a selection the Idea button can read, a caret offset the popup can anchor
to, and text that round-trips to a file byte for byte, and a textarea gives all
three. The one thing it does not give is where the caret is *on screen*, which is
measured from the element's own font rather than a hard-coded metric.

### The pane is live from the moment it is on screen

Two defects the smoke suite caught, both the same shape, both worth knowing about
before touching this code: an editor that took focus when its first read landed stole
keystrokes from the terminal, and a startup read that finished late overwrote text a
human had already typed. **Anything that lands asynchronously must not assume the
editor is still empty, and must not assume nobody is typing somewhere else.**

Text reaches the disk by three routes — a debounce after the last keystroke, a save
on blur, and ⌘S — and the header states which of `no changes / unsaved / saving /
saved / NOT saved` is true. A failed write is never silent and never reads as saved.

## Layout

```
src/shared/protocol.ts        the main<->renderer contract (windows, facts, terminal, the read model, layouts)
src/shared/ideafile.ts        `kyosei.ideas`'s frontmatter and section readers, ported and checked
src/main/main.ts              window lifecycle, the window registry, the IPC wiring, layout memory
src/main/preload.ts           the whole renderer capability surface (contextIsolation on)
src/main/claude-preload.ts    the claude view's own preload: one click listener, nothing exposed (KYO-321)
src/main/kyo.ts               THE resolver: two sources, one invocation, quoting, argv guard
src/main/kyoserve.ts          THE transport: a client of kyo serve, a project as (server, name) (KYO-459)
src/main/store.ts             the store layout's file names, and nothing that opens them
src/main/facts.ts             locating the store and the currency verdict
src/main/records.ts           every store read, and the allowlist that keeps them reads
src/main/ideas.ts             the scratchpad and `ideas/`, read and written through kyo serve's file routes
src/main/activity.ts          the activity rows, renamed from `kyo activity --json`
src/main/links.ts             the `kyosei://` grammar, the resolver, and `followLink`
src/main/shellenv.ts          the login shell's environment, captured once it worked, and the log of every ask (KYO-229, KYO-266)
src/main/remote.ts            the other machines' kyo serves this app connected to, remembered by address
src/main/terminal.ts          the pty, the tmux session, and removing an earlier build's `kyo` links (KYO-450)
src/main/tmuxenv.ts           its pty-free half: the pane's environment, which tmux, the socket funnel (KYO-388)
src/renderer/renderer.ts      the entry: a shell window, or one tab moved into a window
src/renderer/shell/shell.ts   the shell -- layout memory, badges, pop-out, deep links, menu commands
src/renderer/shell/rail.ts    the nav rail, and the tab kinds that contribute to it
src/renderer/shell/sidebar.ts the four lists, and the rows every handler shares
src/renderer/shell/columns.ts columns of tabs: the model, the dragging, the menu, the drop outside
src/renderer/shell/home.ts    the home page: what a new tab or an empty column offers
src/renderer/tabs/kind.ts     THE TAB INTERFACE -- read this to write a tab
src/renderer/tabs/registry.ts what the build discovered, checked against its directories
src/renderer/tabs/<kind>/     one directory per tab kind; the build enumerates them
src/renderer/shell.css        the v3 stylesheet
tests/fixture.ts              throwaway stores, built by the real CLI, in spacey paths
tests/shell.spec.ts           launches the real app and drives the v3 shell
tests/seams.spec.ts      drives the seams directly, with no app
tests/kyoserve.spec.ts   drives the transport against a real kyo serve on an ephemeral port
tests/remote.spec.ts     the app against two kyo serves: this machine's, and a "dev box"
```

The v2 renderer's records mode, deleted by KYO-232, was a directory rather than a file,
one module per surface:

```
src/renderer/records/fragments.ts  currency line, refusal block, empty state, chips
src/renderer/records/tools.ts      the folder tree and the session-brief calendar
src/renderer/records/base.ts       the Base table and the reader's refinement
src/renderer/records/bubbles.ts    the clusters, and the three bindings over them
src/renderer/records/graph.ts      the typed edges, the type filter, the layout
src/renderer/records/record.ts     one record read-only: gates, properties, document
src/renderer/records/brief.ts      one past session brief, as the store kept it
src/renderer/records/mode.ts       the three panes and the state behind them
```

Its ideas mode, deleted with it, had the same shape, and reused records mode's
`fragments.ts` rather than growing a second currency line and a second empty state:

```
src/renderer/ideas/complete.ts    the `[[` / `#` triggers, matching and insertion
src/renderer/ideas/editor.ts      the textarea, the ribbon, the completion popup
src/renderer/ideas/modals.ts      the clear confirmation and the new-idea form
src/renderer/ideas/panels.ts      the editable properties block, the nested outline
src/renderer/ideas/tools.ts       the scratchpad tool and `ideas/` as a file tree
src/renderer/ideas/mode.ts        the three panes, the open document, the saving
```

`src/shared/grouping.ts` and `src/shared/ideafile.ts` sit beside the protocol for the
same reason: both processes need them. Grouping, because the
folder tree groups in main where the rows are read, and the bubble rendering groups
the Base result the renderer already holds.

## Packaging (KYO-176)

```
npm --prefix apps/desktop run pack     # → apps/desktop/release/Kyosei-darwin-arm64/Kyosei.app
```

`release/` is gitignored. The pack builds for the **host platform and architecture
only**: KYO-176's boundary is a build that runs on this machine, and cross-building
is part of the distribution question nobody has decided.

What `build.mjs --pack` does, in order: the four esbuild bundles (KYO-459 removed a
fifth, the app's own headless server: `kyo serve` is the server); then
`@electron/packager`; then it re-reads the finished `.app` against the contract and
**fails the pack** if any clause is unmet.

**The bundle carries no Python** (KYO-450, KYO-443). Until KYO-450 the pack also ran
`uv python install` into a staging directory, installed this tree's `kyosei` into it,
copied it into `Contents/Resources/kyo-runtime` with `extraResource`, compiled its
bytecode before sealing, signed its Mach-O files, and checked that the runtime and the
tree agreed on `schema_version` (KYO-79, KYO-176, KYO-386). KYO-443 superseded
that decision: the app runs the `kyo` the user installed, and whether that `kyo` reads a
store is the CLI's to say. Every one of those steps is gone, and `verifyBundle` now
asserts the opposite of what it used to — `assertNoPythonRuntime` walks
`Contents/Resources` and fails the pack on a `kyo-runtime` directory, a `python*`
executable or a `site-packages` anywhere in it, so the runtime cannot come back through
a packaging option or a dependency that vendors one. The bundle is about 310MB, almost
all of it Electron.

**`@electron/packager` rather than `electron-builder`**, for the same reason esbuild is
here rather than Vite: everything electron-builder adds — installer targets,
publishing, auto-update, its own signing and notarization path — is work KYO-176
explicitly did not want (signing came later, as packager's own `osxSign` option; see
"Signing and notarization"), and `@electron/packager` is a function call from the
script that already exists (38 packages, no config file).

`@lydell/node-pty`'s prebuilds are `asar.unpack`ed: the `.node` is dlopened and
`spawn-helper` is `exec`ed, and neither works from inside an archive. The pack checks
that the helper survived at mode 0755 — the failure that disqualified `node-pty` itself
(see "Tooling, and why").

### Signing and notarization (KYO-386)

[`docs/reference/topics/serve-and-desktop.md`](../../docs/reference/topics/serve-and-desktop.md)
says how to ask for them; this is what the pack does and why.

**`verifyBundle` asks each nested Mach-O for itself.** `codesign --verify --deep`
descends into nested bundles and frameworks; a Mach-O under `Contents/Resources` is
neither, so to the outer seal it is a resource, hashed like a text file, and its own
signature is never looked at. Notarization does look. So every Mach-O under
`app.asar.unpacked` must show this team, the hardened runtime and a secure timestamp,
or the pack fails before anything is sent to Apple. (Until KYO-450 the bundled
runtime's Mach-O files were asked the same, its bytecode was compiled before sealing so
the first `kyo` run could not break the seal, and the seal was asked for a second time
after that `kyo` had run; with no runtime, none of that has anything to act on.)

**Entitlements, measured rather than copied from a template.** The main binary and
the plain helper get `com.apple.security.cs.allow-jit` and nothing else
(`entitlements.mac.plist`): with it removed, `ELECTRON_RUN_AS_NODE=1` on the signed
binary dies at V8's first code-range reservation (`Failed to reserve virtual memory
for CodeRange`), and `allow-unsigned-executable-memory` was not needed by the Node
mode or the window. The Renderer, GPU and Plugin helpers keep osx-sign's defaults.
`AMFI` parses entitlements with its own XML reader, which refuses comments, which is
why that file has none and this paragraph exists.

**The RunAsNode fuse stays on.** The pack flips no fuse, and `assertRunsAsNode` runs
the finished binary as Node in an empty environment on every pack, which is how
KYO-378's headless server starts it.

### Run it from outside the checkout, or you are not running what it ships

`resolveKyo` prefers the working tree, and that is a decision (KYO-79), not an accident: an
edit under `src/kyosei/` must be live in the app with no rebuild. A packed `.app`
sitting in `apps/desktop/release/` is *inside this checkout*, so it finds the
working tree by walking up and uses it — the `kyo` on your PATH is never asked.

Copy the `.app` somewhere outside the repository to run it as a user would: on the
`kyo` your login PATH has. That is also KYO-176's acceptance
condition, that the app runs when no checkout is open, and the app then
has no project until one is chosen in the switcher (KYO-175), because there is no
checkout under it to fall back to.

### What the Finder hands it

Launched by double-clicking, this build inherits `PATH=/usr/bin:/bin:/usr/sbin:/sbin`
— measured from the running process, and exactly what the `KYOSEI_TMUX` section
above describes. Nothing about the app's `kyo` breaks: it is resolved on the login
shell's PATH, captured once at launch (KYO-229), and never on this minimal one.

What the minimal `PATH` does reach is the terminal pane. With Homebrew on
Apple silicon `tmux` lives in `/opt/homebrew/bin`, which is not in that list, so the pane is a
shell of its own and says so, naming the remedy **this** launch can perform:
`launchctl setenv KYOSEI_TMUX /opt/homebrew/bin/tmux`, then relaunch. See "The message
names the instrument its reader has" above for how the two launches are told apart.
The app does **not** synthesise a `PATH` of its own to paper over this, deliberately:
guessing at `/opt/homebrew/bin` would be the app answering a question it was not asked
(law 6).

### Two facts worth knowing before you go looking for them

- **The `.app` shares its `userData` with `npm start`.** Electron takes the app name
  from `package.json`'s `name` inside the asar, not from the packager's `name`, so
  both write to `~/Library/Application Support/@kyosei/desktop` — the same remembered
  project and recents, which is why a fresh packaged launch opens the project you
  last chose in development. That sharing is now a decision rather than an accident;
  see "Two builds, one userData directory" below.
- **It is signed only when the keychain holds an identity** (see "Signing and
  notarization"). An ad-hoc bundle runs on the Mac that built it because a locally built
  bundle is not quarantined. The `.app` is about 310MB, nearly all Electron, and
  `release/` holds only the `.app` once a pack finishes.

### Two builds, one userData directory (KYO-189)

Electron derives `app.getPath("userData")` from `app.getName()`, which takes the
package's `name` unless `productName` is set. `package.json` sets
`name: "@kyosei/desktop"` and no `productName`, so a dev `electron .` and the packaged
bundle both resolve to `~/Library/Application Support/@kyosei/desktop` — even though
the packaged app's `CFBundleName` is `Kyosei` and its identifier is
`dev.kyosei.desktop`. There is no `~/Library/Application Support/Kyosei`; the name that
would have separated them never reaches Electron.

Two pieces of state lived there and both were shared: the remembered project
(`project.json`) and, the damaging one until KYO-450, the `kyo` shim under `bin/` that
every terminal pane put in front of `PATH`. The shim's own header said it was
regenerated every launch, so **whichever build launched most recently owned every
build's panes**. KYO-450 removed the shims with the bundled runtime — a pane's `kyo` is
the login PATH's now — so the collision below is history; it is kept because the
sharing decision it produced still stands. The
failure path runs entirely through mechanisms working as designed: a packaged build
kept open on a project at an older `schema_version`, a newer checkout build launched
beside it, and the packaged app's pane now holds a `kyo` from the newer build — whose
schema refusal correctly names `kyo rebuild --force`, which correctly upgrades the
store, after which the packaged app can no longer open the project at all.

**The decision is that they go on sharing it**, and it is recorded here and checked in
`tests/seams.spec.ts` (which fails if `productName` appears in the manifest), so
reversing it is a decision somebody makes rather than a one-line edit nobody notices.
Three reasons, in order of weight:

- **Separating them would not fix the damaging half.** `productName` separates *these
  two* builds. Two checkouts of this repository — two dispatch worktrees, say — still
  resolve to the same `userData` and the same shim, which is the same collision with
  none of the compensation.
- **It would split the recents and the caches**, and everything a user already
  accumulated would stay behind in the old directory: visible to whichever build kept
  the old name, invisible to the other, with nothing saying so. That is a behaviour
  change for anyone relying on the current sharing, and the record was explicit that it
  should not be a side effect of a manifest edit.
- **The smaller change covers every case.** A shim that records its writer, and a pane
  that reads it back, makes the collision visible without moving any directory.

So the shim carried its build's identity, in two places for the two moments it mattered
(both removed with the shims, KYO-450):

| moment | mechanism | what a reader sees |
| --- | --- | --- |
| at launch | `writeKyoShims` reads `bin/.kyo-shim.json` before overwriting it | the pane's status line says another build had this directory, and names it |
| at every run | the shim script compares its own stamp against `KYOSEI_SHIM_ID` in the pane's environment | one line on stderr, above whatever the command printed, naming the build whose CLI this actually is |

The second is the one the record is about, and nothing else can do its job: both builds
running at once means the file changes under a pane that is sitting idle, and nothing in
this app is watching it. It **warns and then runs** rather than refusing — the reader
may well have meant to run the other build's CLI, and a pane whose `kyo` stopped working
is a worse failure than one that works and says whose it is. What it buys is that the
sentence lands *above* a schema refusal, and therefore above the `kyo rebuild --force`
that refusal names.

`KYOSEI_PROJECT_ROOT` still solves the remembered-project half by pinning a build to one
project (`main.ts` returns the pinned refusal before `writeProjectState`, so a pinned
build records no choice). It does not touch the shim, which is why the shim needed its
own answer.

#### A third launcher: the test suite (KYO-275)

The decision above counted two builds. A third launcher turned out to have been sharing
the directory all along: every app the desktop suite starts resolved to the same
directory, regenerated the live shim on launch, and pointed it at this checkout's CLI.
A merged-tree run switched the open panes of two consumer projects to a CLI two
`schema_version`s ahead of their stores; KYO-189's warning fired exactly as designed,
and its remedy — "quit the other build" — named a test that had exited hours earlier.
The specs that passed `--user-data-dir` were isolated by it; two of the twelve
launch sites (`links.spec.ts` and `tmux-owner.spec.ts`) never passed it, and nothing
checked.

Two things changed, and the sharing decision is not one of them:

- **`KYOSEI_USER_DATA` moves the directory.** Read in `main.ts` before Electron is
  ready (the last moment `app.setPath("userData")` takes effect), on the precedent
  `KYOSEI_HOME`, `KYOSEI_TMUX` and `KYOSEI_LOGIN_SHELL` set: a consumer's seam, not a
  test-only branch. `tests/fixture.ts`'s `suiteEnv()` sets it on every launch
  environment to one temp directory per runner process, so a spec cannot forget it; an
  explicit `--user-data-dir` on a launch still wins, since a switch on one launch is
  more specific than the environment it inherited. `tests/user-data.spec.ts` snapshots
  the real directory's state around a launch and fails if any byte moves — including
  an earlier build's shims there, which a suite launch's KYO-450 cleanup must never
  remove from the real directory.
- **The shim knows whether its writer is still running.** Its record carries the
  writer's pid, and the script asks `kill -0` before it warns. A live writer is a
  contested directory and keeps KYO-189's sentence. A dead one — a test run, a crashed
  build — is a stale file, and the sentence says so, tells the reader not to act on the
  refusal beneath it, and names the one act that regenerates the shim: relaunch the
  app the pane belongs to, then reopen the pane.

## Tooling, and why

- **No UI framework.** The shell's whole state is a mode and a window list. A
  framework would be dependency weight with nothing to pay for it.
- **esbuild** (`build.mjs`) rather than Vite/electron-vite: no dev server, no HMR,
  no plugin graph is wanted here, so a bundler needing no config file is the whole
  requirement. Four bundles (main, the shell's preload, the claude view's preload,
  renderer) plus three copied assets.
- **TypeScript** with `tsc --noEmit` as the typecheck — esbuild strips types without
  checking them, so the typecheck is a separate command on purpose.
- **Biome** rather than ESLint: one binary, lint *and* format, no plugin graph to
  assemble for TypeScript.
- **Playwright** (`_electron.launch`) for the smoke test: it launches the real
  Electron binary and drives the real renderer, and its `waitForEvent("window")`
  is what the multi-window and pop-out assertions need. No browsers are downloaded.
- **`node:sqlite`** for the read path: a stable built-in of the Node inside Electron
  44, so no dependency and no native rebuild.
- **`@lydell/node-pty`** rather than `node-pty` itself. Both are Node-API addons, so
  neither needs an Electron ABI rebuild; what separates them is how the binary
  arrives. `node-pty@1.1.0` ships its prebuilds inside its own tarball with the
  macOS `spawn-helper` at mode 0644 and no install script that fixes it, so after a
  plain `npm ci` every spawn fails with `posix_spawnp failed` (verified here before
  the choice was made) — and it ships no Linux prebuild at all, so Linux compiles
  through node-gyp in an install script, which npm's allow-scripts gate blocks by
  default. `@lydell/node-pty` publishes one optional package per platform/arch
  (all six), with the helper already 0755 and no install script anywhere. Its cost,
  stated: the only published version is a `1.2.0` beta.
- **xterm.js** for the terminal screen: a real shell emits real control sequences,
  and a textarea with ANSI stripped would look right until the first `git log`.

## Not here

Auto-update belongs to a later record, and so does anything about giving this build
to another person — see "Packaging" above for what is deliberately absent from it.

Terminal *context* — the pane knowing which record its neighbours are showing
(KYO-79) — is not here either. This is the shell, not the wiring to the modes.
