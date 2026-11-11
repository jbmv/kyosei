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

The renderer is one window shell: a left rail, a collapsible sidebar whose content the
rail selects, and one or two columns of tabs. Everything openable is a tab: the
scratchpad or an idea file (the `compose` kind), a record, the views (records, awaiting
you, a brief, a shift note, one document), the terminal, the claude session and the home
page a new tab opens on. "Move to a new window" opens a whole shell window on the same
project holding that tab (KYO-246); the claude.ai window is the one exception, and
belongs to no project window. Records mode, ideas mode and the constant shell of the
earlier renderer were deleted by KYO-232 rather than adapted.

The sections below describe each part as built:

- the **live seams** (KYO-111, KYO-459): how the app invokes `kyo`, how it reaches every
  project through `kyo serve`, how it reports the store's currency, and the terminal's
  shell.
- the **terminal as a window onto the orchestrator's session** (KYO-115, KYO-229,
  KYO-293, KYO-474): opening the tab lands in the project's orchestrator session on
  `kyo controller`'s own tmux server (`tmux -L kyosei`) — on this machine through a pty
  whose process is the tmux client itself, on a dev box through a pty running `ssh -t
  <host> zsh -lc 'kyo controller attach <prefix>'`. It creates no session; with none
  running it says so and offers Start orchestrator. A bare login shell is one click
  away on this machine, on a second pty of its own.
- the **claude.ai pane** (KYO-186, KYO-190, KYO-191): a `WebContentsView` on a
  persistent partition.
- **packaging** (KYO-176): `npm run pack`, a bundle that carries no Python (KYO-450).
  See "Packaging" below.
- the **read model** (KYO-231, KYO-454) and **`kyosei://` deep links** (KYO-230): what
  the tabs render, and how a link reaches one. See their sections below.
- the **compose tab** (KYO-234): the editor for the scratchpad and for idea files, and
  the app's only writes.

**The app writes exactly two things of yours, and never your store**: the session
scratchpad and files under `ideas/`. Both are written by asking the project's `kyo
serve` (KYO-459). See "The compose tab" below.

**The app never creates a record (KYO-439).** Turning an idea into a record means
deciding its kind, title, criteria and touches, and that is the orchestrator's act, not
the app's: the app has no basis on which to write a record. What the app offers is the
scratchpad's "Promote to idea" (KYO-252, below), which writes an idea FILE; the
orchestrator promotes from there. `kyo.ts`'s `runKyo`, the one subprocess the app starts
for the CLI, refuses `kyo promote` by name (`assertAppMayRun`), and
`tests/seams.spec.ts` fails if that refusal is removed.

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

The specs in `tests/` run under one Playwright runner (`playwright.config.ts`: one
worker, `retries: 0`) and are of two kinds:

- Most launch the real Electron app and drive it (`shell.spec.ts`, `compose.spec.ts`,
  `record.spec.ts`, `views.spec.ts`, `links.spec.ts`, `activity.spec.ts`,
  `controller.spec.ts`, `claude-pane.spec.ts`, `docs.spec.ts`, `project-switch.spec.ts`,
  `remote.spec.ts`, `remote-terminal.spec.ts`, `serve-down.spec.ts`, `tmux-owner.spec.ts`,
  `user-data.spec.ts` and others). `tmux-owner.spec.ts` carries no `KYOSEI_TMUX`; see "The
  app owns the session, and never ends it".
- `seams.spec.ts` launches nothing. The resolver, the argv guard, the currency mapping,
  the login-shell capture, the earlier build's CLI cleanup and the ports of the CLI's
  rules are in Electron-free modules on purpose, so they are driven directly and fail
  with a line number rather than a screenshot. `kyoserve.spec.ts`,
  `controller-trust.spec.ts`, `remotes-file.spec.ts`, `styles.spec.ts`, `tabdrag.spec.ts`
  and `fixture-teardown.spec.ts` have the same shape, and `claude-pane.spec.ts` mixes both.

Both kinds build their fixtures with the real `kyo`, in temp directories **whose paths
contain a space** — this repository's own checkout does (KYO-89), and a fixture in a
spaceless directory would let a quoting defect pass here and fail where the project
actually lives (`tests/fixture.ts`).

These are the app's *own* checks. `make test` at the repo root is the Python suite and
knows nothing about this directory — that separation is deliberate (KYO-110).

To see the app: `npm --prefix apps/desktop run start`. To build one that runs without a
checkout: `npm --prefix apps/desktop run pack` — see "Packaging" below.

### Which environment this suite was written for (KYO-183)

There are two places the suite runs that behave differently:

- **a fresh dispatch worktree**, where everything gitignored is absent — this project's
  own `kyosei/kyosei.db` included — and where usually nothing else is running;
- **a full clone that has been worked in**, where that database exists and is large, and
  where the packaged app, a second Electron project and other workers' suites may be
  running at the same time.

**No assertion may depend on which one it is.** No check may depend on a file being absent
merely because it is gitignored, or on this project's own `ideas/` being small. Every
launch in every spec points the app at a `mkdtemp` fixture through `KYOSEI_PROJECT_ROOT`,
and a check that needs a particular store state builds that state itself. The rule was
paid for: a check that asserted the starting project's database was absent passed in
every worktree and failed in the clone where the app is used.

A check that needs the launch project to have a particular shape supplies its own
checkout rather than asserting about the machine's (KYO-218): the difference between the
two environments also reaches a check as a *duration*, since a `kyo` command about a
real store takes longer than one about a refusing worktree, and a scan of the source
cannot see that. `launchUnpinned` in `tests/project-switch.spec.ts` is the one route by
which a check launches the app without pinning a project.

**What a green in a fresh worktree covers.** Every assertion both kinds of spec make is
made against fixtures, so none is weaker in one place than the other. It does not cover
*timing*: several checks are load-dependent races by construction, and a quiet worktree
is the easy environment for all of them. A single run cannot catch a check whose result
depends on timing; repeated runs can, and this is the recipe:

```
for i in 1 2 3 4 5; do
  npm --prefix apps/desktop run check > /tmp/desktop-$i.log 2>&1
  echo "run $i: exit=$?"
done
grep -h '✘' /tmp/desktop-*.log | sort | uniq -c
```

Never pipe a suite through `tail`: `tail`'s exit status is not the suite's, and an
interrupted run leaves no log to read.

### The known-intermittent checks (KYO-183, KYO-205)

**This table is the list, and it is what a brief should cite.** A failure in this suite
that is not named here is not a known flake: report it rather than re-running until it
passes. A brief that says "one known intermittent failure exists" is what KYO-205 was
filed about — a worker told there is one either blames itself for the second or learns to
wave any single failure through.

Every row is **cause found and removed**, not suppressed and not retried. No fix widens a
timeout: the failing runs exhaust a 30-second wait where the passing ones take 8–21
seconds, so a longer wait would turn them green without repairing anything. The one
retry in the suite is not a check: `removeTree` in `tests/fixture.ts` retries a `rmdir`
**syscall** in teardown (`{maxRetries: 10, retryDelay: 100}`), because a recursive
`rmSync` is a walk and a file created between its `readdir` and its `rmdir` makes the
`rmdir` fail with `ENOTEMPTY`. Its only two answers are "the directory is empty now" and
"not yet", and after the last attempt the error is thrown as before. Measured for
KYO-254.d1 over 40 teardowns per arm against a process writing into the tree: 26
failures with the writer running throughout and 16 with it stopping after 0.4 s, and 0
in both arms with the retries. `tests/fixture-teardown.spec.ts` holds this against real
writing processes, and requires that at least one of twenty bare removes still loses, so
the check cannot pass vacuously on a machine that has stopped racing.

The checks that exhibited the other failures, each since repaired or removed, were:

| check | what was seen | cause, and what was done about it |
|---|---|---|
| `closing a popped-out claude window re-fetches the docked pane` (KYO-183.d1, KYO-217) — the check is gone, and the rule it established is kept in `tests/links.spec.ts` | two failures in five full runs, `page.waitForSelector: Timeout 30000ms exceeded` waiting for `body[data-ready="true"]`; in isolation 0 failures in 10 on an idle machine and 5 in 10 under deliberate load | It waited on **the wrong page**. Playwright reports a `WebContentsView`'s contents as a window of its own, so `app.waitForEvent("window")` returns whichever of the popped window's renderer and the claude.ai navigation registers first. A check that needs the claude view chooses its contents by origin (`file:` is this app's renderer, anything else the view's page), as the claude check in `tests/links.spec.ts` does. |
| a terminal pane check that failed on `error connecting to .../tmux-502/default` (KYO-238) — the check is gone, the mechanism is KYO-254's | one failure, passed 3 of 3 alone | A socket directory the suite had created escaped the run that created it through `process.env` and reached a later run after it was deleted. `suiteEnv` in `tests/fixture.ts` carries no inherited `TMUX`, `TMUX_PANE` or `TMUX_TMPDIR`, and `the pane's server is the controller's, whatever TMUX_TMPDIR the app inherited` in `tests/tmux-owner.spec.ts` fails if that changes. The app no longer starts any tmux server of its own (KYO-474). |
| `a typed [[id]] is Mod-clickable and opens the record through followLink` — `tests/compose.spec.ts` (KYO-283.d1, KYO-266.d1) | failed 3 of 3 when the whole file ran, 0 of 3 alone; not load-dependent | **Order inside the file.** The scratchpad is one file for the whole spec, so the check opened on the half-typed `[[FIX-1.` an earlier check left there, and the editor's link matcher captured text from that leftover's `[[` to the typed link's `]]`. The check now clears the editor and asserts the document is exactly the line it typed, and the click point is tested through `posAtCoords`, the call the editor's own handler makes. |
| a sidebar list that rendered no rows after launch (seen three times, about one launch in 250) — closed by KYO-459 | a 30-second wait for a `[data-region="sidebar-row"]` that never came, each showing the bare text `database is locked` | `node:sqlite` defaults its busy timeout to zero and every `kyo` subprocess the app spawned opened and closed the WAL database, so a read-only open could land in the checkpoint and receive `SQLITE_BUSY`. The app opens no SQLite since KYO-459: every list is a `kyo serve` read, so the cause cannot recur in it. What made the failures unreadable was also closed: the sidebar names the list in front of the tool's sentence, states an empty result, replaces a spinner that has not answered in 15 seconds with a line saying so, and stamps `data-load` on itself; `a sidebar list never just goes blank` in `tests/views.spec.ts` asserts the stamp and the sentence. |

**The start-up budget under load, measured — and CPU load is not the variable
(KYO-238).** Four occurrences were the same shape and none is in the table: a wait
budget exhausted, the check passing alone straight afterwards, no wrong answer anywhere.
The hypothesis filed with them was that the fixture's start-up budget is fixed while the
machine's headroom is not. Measured for KYO-254.d1, ten launches per arm timed from
`_electron.launch()` to `body[data-ready="true"]` with no cap:

| arm | one-minute load | start-up |
|---|---|---|
| idle | 6.5–8.4 | 1.17–1.70s, median **1.27s** |
| twelve CPU spinners | 9.6 rising to **67.6** | 1.79–2.36s, median **1.91s**, none over 30s |

The hypothesis is therefore not supported as stated: at the load average the fourth
occurrence recorded, start-up keeps about fifteen times the 30-second budget in hand, and
the loaded distribution is a 50% stretch rather than a cliff. What every failing run had
and this arm did not was **memory pressure and disk contention** — background jobs killed
for low free memory, or two Python suites and a device simulator starting beside it. That
is the variable to instrument next.

### The suite opens no windows (KYO-122)

`npm --prefix apps/desktop run smoke` launches the real Electron app dozens of times.
Until KYO-122 each launch put a real window on screen, ordered front across a full-screen
app in its own macOS Space, so the machine running the suite was unusable for the length
of the run. **The suite's windows are never shown at all.** They are created, loaded,
laid out and driven over CDP, and nothing appears on the screen or in the dock.

The switch is a launch argument, `--kyosei-window-show=<focus|inactive|hidden>`, parsed
by `parseWindowShow` in `src/shared/protocol.ts` and applied in `src/main/main.ts`:

| value | what a window does |
|---|---|
| `focus` | appears and takes focus (`show()`). **The default** — what `npm run start` gets, and what you get if you pass nothing or misspell the value. |
| `inactive` | appears without activating the app (`showInactive()`): on screen and compositing, but the keyboard stays where it was. It still paints over what you are looking at, which is why the suite does not use it. |
| `hidden` | never shown at all. **What the suite passes.** |

On macOS the app also drops its dock icon (`app.dock.hide()`) for anything but `focus`,
before the first window is created. That makes the process an accessory: it never
activates itself, so it never pulls itself onto the Space in use.

Chromium treats a never-shown window as occluded and throttles it, and a suite running in
a regime the product never runs in can pass vacuously. So the throttling is turned off by
name: `prepareApp` in `src/main/main.ts` appends `disable-background-timer-throttling`,
`disable-renderer-backgrounding` and `disable-backgrounding-occluded-windows` before
`ready`, and every window carries `backgroundThrottling: false`. No check measures this
now; the check that did, in the deleted `smoke.spec.ts`, counted animation frames and
timers inside an unshown window.

The suite's own policy is `SUITE_WINDOW_SHOW` in `tests/fixture.ts`, and it is `hidden`.
A check that needs another policy opts in for itself, with `launchArgs("focus")` or
`launchArgs("inactive")` (`shell.spec.ts`, `compose.spec.ts`, `activity.spec.ts`) or
`windowShowArg(…)` appended to a launch's arguments, with the reason at the call site.
There is deliberately no environment variable and no way to turn the whole suite loud.
`parseWindowShow` is checked in `tests/seams.spec.ts`.

## The shell (KYO-232)

<!-- governs:
- apps/desktop/src/renderer/shell/shell.ts
- apps/desktop/src/renderer/shell/rail.ts
- apps/desktop/src/renderer/shell/sidebar.ts
- apps/desktop/src/renderer/shell/columns.ts
- apps/desktop/src/renderer/shell/home.ts
-->

Three regions and nothing else: the **rail**, the **sidebar**, and the **columns**.

**The rail** is six sidebar lists in a narrow column — ideas, records, awaiting you,
notes, docs and activity — and the project switcher at the foot. Each list item selects a
sidebar list; a tab kind may also declare a `rail` entry in its own directory, which is
merged in, so the rail is not a second registry of what a tab may be (no kind declares one
today). Two carry a live badge: awaiting you shows how many gates wait on a person,
activity how many dispatches are running. Both numbers come from the read model and are
pushed by the main process when they move — a badge read once at window start would go on
saying `3` through a session in which the orchestrator cleared every gate.

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
activity poll repaints it. A layout saved with the retired `activity` tab opens
the build's own "no tab kind called activity" placeholder, which is what law 6 asks for
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
`moveIdea`, `deleteIdea` in `main/ideas.ts` -- are requests to the project's `kyo serve`,
which refuses a target outside `ideas/` with a sentence naming the path (see "The compose
tab").

**The rail holds lists only (KYO-248).** Ideas, Records, Awaiting you, Notes, Docs,
then the project switcher; every item selects a sidebar list and nothing on the rail
opens a tab. Awaiting is its own list -- each record with an open gate waiting on a
person, the gate type on the row -- rather than a group pinned atop the records list.
Notes replaces Briefs: every shift note, newest first, with the HANDOFF badged (the
note standing when a session-start brief ran, the one `kyo brief` re-surfaces to the
next session) and the latest brief kept at the top; a click shows that note by id
(`getShiftNotes`, which asks `kyo shift-notes` of the project's `kyo serve`). Scratchpad, terminal, claude and activity are on the home page and in the View
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

<!-- governs:
- apps/desktop/src/renderer/tabs/kind.ts
- apps/desktop/src/renderer/tabs/registry.ts
- apps/desktop/build.mjs
-->

`src/renderer/tabs/<kind>/index.ts` exports one `TabKind`, and that is the whole
registration mechanism: `build.mjs` enumerates the directories into
`src/renderer/tabs/registry.generated.ts` (gitignored, because it is output), and
`src/renderer/tabs/registry.ts` checks each module's `kind` against the directory it
came from and refuses a mismatch by name.

**`src/renderer/tabs/kind.ts` is the interface, and it is documented there rather than
here.** A reader of that one file can write a tab. The four members are
`open`, `title`, `render`, `close`; a tab identifies itself by `{kind, arg}` everywhere
— in a saved layout, in an `openTab` call, in the singleton rule, in a pop-out — and
the shell never reaches into a tab's state.

A generated module rather than an esbuild plugin resolving a virtual import, because
`tsc --noEmit` is a separate check in `npm run check` and knows nothing about esbuild's
resolver: a virtual module would typecheck as missing. `npm run typecheck` regenerates
it first (`pretypecheck`).

The tab kinds built are `claude`, `compose`, `home`, `record`, `terminal` and `views`.
`docs/` holds the document renderer the `views` kind uses and is not itself a kind.

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

- **shell windows** — the full window. They appear as the window chips in the
  status bar; the `+` chip opens another, and clicking a chip focuses that window. A shell window
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

<!-- governs:
- apps/desktop/src/main/kyo.ts
- apps/desktop/src/main/shellenv.ts
-->

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

<!-- governs:
- apps/desktop/src/main/kyoserve.ts
- apps/desktop/src/main/store.ts
- apps/desktop/src/shared/served.ts
-->

The app is a client of `kyo serve` for every project it shows, this machine's included
(KYO-452). `kyoserve.ts` is the one transport: a server
is an address (`127.0.0.1:47370` for this machine, or `KYOSEI_SERVE_URL`; another
machine's Tailscale Serve name over HTTPS), a project is (server, registry name), and a
`ServedProject` is what every store module takes -- `json(args)` for a served command,
the file routes for `ideas/` and the scratchpad, and `subscribe` for the events stream.
The app opens no store database: the status bar's record count is `kyo list`'s total,
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

<!-- governs:
- apps/desktop/src/main/facts.ts
-->

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

`kyo list --json` is the surface because it answers on every project: a `kyo base run`
read would need a base declared in `kyosei/bases.yml`, a file in a store this app must not
write to.

### 4. The terminal is a real shell — `src/main/terminal.ts`

<!-- governs:
- apps/desktop/src/main/terminal.ts
- apps/desktop/src/renderer/tabs/terminal/index.ts
-->

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

<!-- governs:
- apps/desktop/src/main/ssh.ts
-->

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

<!-- governs:
- apps/desktop/src/main/tmuxctl.ts
- apps/desktop/src/main/tmuxenv.ts
-->

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

<!-- governs:
- apps/desktop/src/main/shellenv.ts
-->

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

The one-project rule above is checked by `an answer about a project that moved while it
was being asked is asked again` (`seams.spec.ts`), which drives `askedAboutOneProject`
directly, moving the project mid-await, with no Electron and no timing.

## The claude.ai pane (KYO-186)

<!-- governs:
- apps/desktop/src/renderer/tabs/claude/index.ts
-->

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

<!-- governs:
- apps/desktop/src/main/sessions.ts
-->

**The session writes its own Remote Control information into the store, from a
session hook.** `kyo sessions --hook` runs on `SessionStart`, `Stop` and
`SessionEnd` and maintains `kyosei/sessions.json` beside `kyosei.db`. The app reads
the entries with `kyo sessions --json`, asked of the project's `kyo serve` (KYO-459),
so the CLI parses the file and owns its schema and its problems. Until KYO-459 the app
read the file off disk itself, which a viewer of another machine's project cannot do.

The alternative — having the app scan `~/.claude/projects/<path-slug>/` for the
harness's `bridge-session` records — works and was measured, and is the wrong shape
for three reasons. It would put knowledge of the harness's private file layout in a
second consumer. It cannot tell a *running* session from a finished one, because
nothing holds a transcript open and a running `claude` does not name its session on
its command line. And, the one that decides it, **it cannot cross hosts.**

### The file is written by whichever machine runs Claude Code, and read from the server

`kyosei/sessions.json` is written by **whichever machine actually runs Claude
Code**, from that session's own hooks, and is read through the `kyo serve` of the machine
that holds the project. The two need not be the same machine as the one running this app.
The motivating case is a dedicated dev box: the project lives there, and the work is driven
through web Remote Control. The box's session hook writes the file; its `kyo serve` returns
the entries to the app on the laptop, and the pane lands on the session running on the box.
**A scan of `~/.claude` on the laptop would find nothing.**

The file goes beside `kyosei.db` rather than "in the project directory", and those are the
same place only under the in-repo layout. Under the split layout the store is its own
repository elsewhere, and this app never assumes where that is: `kyo serve` resolves it.

**A project whose entries cannot be read falls back rather than failing.** If the server
reports no store, no session file, or a file this build cannot parse, the pane opens
`https://claude.ai/code` — which lists the account's own sessions. That is not a
consolation prize: on a machine where Claude Code runs somewhere else, it is what the
feature *is*.

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
connected laptop. The pane in the real app is covered by `shell.spec.ts` and
`links.spec.ts`: that it opens beside the terminal and hides without destroying its page, that
the view is on a persistent on-disk partition under this app's profile, that the
caption names the outcome, and that it pops out through the same mechanism the
editor and terminal use.

What is deliberately not checked: anything inside the page. That is claude.ai's, not
this project's, and no check here signs in.

## Commands on a machine: the orchestrator row and New project (KYO-460)

<!-- governs:
- apps/desktop/src/main/controller.ts
- apps/desktop/src/main/ssh.ts
- apps/desktop/src/shared/controller-trust.ts
- apps/desktop/src/renderer/shell/new-project-dialog.ts
-->

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

Checked in `tests/controller.spec.ts`, which never reaches a real host: `KYOSEI_SSH`
names the ssh program, and the checks hand in a stand-in that records its argv and runs
the far end's command under a HOME, ZDOTDIR and PATH of the run's own. `suiteEnv` sets
`KYOSEI_SSH=/usr/bin/false` for every other launch, and `KYOSEI_CONTROLLER_SOCKET` to a
socket nothing listens on, so no suite app reaches ssh or the machine's real
`tmux -L kyosei` server.

## The read model (KYO-231, KYO-454, KYO-459)

<!-- governs:
- apps/desktop/src/main/records.ts
- apps/desktop/src/main/activity.ts
-->

Everything the tabs and lists render is produced in the main process, so that **no tab
derives anything**. Every one of these is a read, asked of the project's `kyo serve`
(`ServedProject.json`), and none can mutate a store. Since KYO-459 the app opens no
database and runs no `git` of its own: the CLI commands introduced for it in KYO-454
return the joined facts, and this app renames them into its own casing.

| query | what it answers | the command it asks |
| --- | --- | --- |
| `getRecordDocument(id)` | every child object of one record, in id order, with the description and links | `kyo show <id> --json` |
| `getAwaitingHuman()` | every open gate waiting on a person, across every record status, plus the count for the rail badge | `kyo gates --type …` |
| `getRecordsList({query, base})` | the records list, searched by id or title, or run through a saved base | `kyo base run <name>` when a base is named, `kyo list --json` otherwise; each row is stamped with its class from `kyo next --json` |
| `getActivity({finished})` | running dispatches and the most recent finished ones | `kyo activity --finished <n> --against <ref>` |
| `getLatestBrief()` | the latest session brief as structured sections, every id intact | `kyo briefs --latest` |

The same module also reads one past brief (`kyo briefs <id>`), the briefs and shift notes
lists (`kyo briefs`, `kyo shift-notes`), the saved bases (`kyo base list`) and the board
classes (`kyo next`).

### Why each one asks what it asks

**The record document is `kyo show --json` and nothing else.** That envelope already
carries every child object *and* the obligation collapse, so reading it reuses
`docs_check.collapse_obligations` rather than porting it: there is no second
implementation in this app to drift from the CLI's (law 3). A test compares the
document's groups to `kyo show --json`'s `docs_obligation_groups`, group for group and id
for id, and asserts that no other file in `src/` groups an obligation of its own.

**Awaiting-human matches on gate type.** `awaiting:human-run` and `awaiting:human-review`
are members of `contracts.GATE_TYPES`, and `tags_json` holds the *other* axis, the
per-project vocabulary in `kyosei/gate-tags.yml`, where the type says what kind of
waiting this is and the tag says which thing is being waited on (KYO-102, KYO-46). The
types are passed to `kyo gates` as `--type` arguments, so the app writes no query.
*Across every record status* is the point of the query rather than a detail of it: a gate
on a `working` record and one on an `open` record are the same claim on a person's
attention.

**The brief is read with `kyo briefs`, never `kyo brief`.** Running `kyo brief` inserts a
row in `brief_runs`, so the command whose subject is briefs is the one this app must not
call, and `KYO_WRITING_COMMANDS` names it with that reasoning. `assertRecordsRead` refuses
any subcommand not on `RECORDS_READ_COMMANDS` at the one place arguments are assembled,
an allowlist, so a command the CLI grows later is refused until someone adds it on
purpose. What the store keeps of a past brief is a marker plus two ledger positions; the
sections are the shift notes and ledger entries standing in that window, ids intact so a
renderer can link each row (the resolver of KYO-230 takes `FIX-S1` and `FIX-E12` without
being told what they are). Nothing regenerates the prose: the store never kept it, and
rendering a freshly computed board under an earlier date would invent a document.

**The records list carries the CLI's classes.** Each row is stamped by `readRecordsList`
from `readBoardClasses` (`kyo next --json`), never derived here, because a browser that
re-derived "dispatchable" from status and criteria would be a second board disagreeing
with the first. The Readiness filter of KYO-290 in the sidebar draws from that stamp.

### Activity

`activity.ts` renames `kyo activity --json` (KYO-454) into this codebase's casing and
derives nothing: the dispatch rows come with their record, the current report's verdict
counts, the record's open gates and classes, the worker's tokens and end time, the
unaudited stops, and what git says about each worktree now, read on the machine that holds
the project. `finished-but-not-moved`, `gated` and `terminated-no-report` are the CLI's
classes, and a row can be in more than one: a running dispatch on a gated record is not a
worker who has gone quiet.

**A missing worktree directory is a fact, never an error.** It is the ordinary end state of
a finished dispatch, so each row's git facts end in a named outcome (`read`,
`no-worktree-declared`, `directory-gone`, `not-a-git-worktree`) carrying a sentence.

The git half is **polled** by the main process, because a worker's `git commit` fires no
event a viewer could subscribe to. The poll (`ACTIVITY_POLL_MS`, 5 s, one per project)
is armed by the first `getActivity`, so an app whose reader never opens the list asks for
nothing; it stops on the tick that finds nothing running; it refuses to overlap itself; and
it pushes only a change in the rows' shape (KYO-264).

`ACTIVITY_FINISHED_DEFAULT` (12) is the number of finished dispatches shown by default. It
was measured, not picked: on 2026-09-11 the store held 139 reported and 5 terminated
dispatches, of which 11 closed in the previous fourteen days, and about a dozen is roughly
the fortnight an orchestrator coming back to a session reads.

`ActivityRow.recordStanding` is the empty string: the role word is a property of the record
document, which carries it for the page that needs it.

## `kyosei://<project>/<id>` — deep links (KYO-230)

<!-- governs:
- apps/desktop/src/main/links.ts
- apps/desktop/src/renderer/links/index.ts
- apps/desktop/src/renderer/tabs/views/ids.ts
-->

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

### What ships here

The main process holds the scheme, the interception, the resolver, and a typed
`OpenRequest` on `CHANNEL.openTarget` carrying the target, the anchor, the anchor's kind,
the project root, the source and the column asked for. The renderer draws it (KYO-233):
the record page's anchors, the highlight and the "you were sent here" strip. `deliverLink`
also writes one line per link to the app's own stdout, which is what a person debugging a
link that went nowhere would look at first.

`tests/links.spec.ts` drives both Chromium paths inside the **real** claude view: it
loads a `data:` page into that view over CDP — no production seam takes a URL — and
clicks an anchor (`will-navigate`) and a `window.open` (`setWindowOpenHandler`). It then
asserts the view never left the page it was on, because handing a `kyosei://` link to
`shell.openExternal` would "work" — this app registered the scheme, so the OS would
route it straight back — and is not this.

### In claude.ai the link is an anchor with no href, so the gesture is a click (KYO-263)

<!-- governs:
- apps/desktop/src/main/claude-preload.ts
-->

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

## The compose tab: the scratchpad and idea files (KYO-234)

<!-- governs:
- apps/desktop/src/shared/ideafile.ts
-->

The `compose` tab kind edits the two things this app writes: the session scratchpad and
the files under a project's `ideas/`. The editor is CodeMirror 6 over the markdown source
(`tabs/compose/editor.ts`), chosen over a `<textarea>` or a rich-text editor because the
text must round-trip to a file `kyo` parses and git diffs, byte for byte.

### The write path

Everything else in this app reads. The writes, and the boundary around them, are:

| what | where | who else can see it |
|---|---|---|
| the session scratchpad | `<per-project home>/scratchpad.md` | nobody — outside `ideas/` and outside the store |
| an idea file | under the project's `ideas/` | `kyo promote`, `kyo sweep`, any ideas Base |
| **anything under `kyosei/`** | **never** | — |

**The boundary is `kyo serve`'s: `src/kyosei/serve_files.py`** (KYO-456). Since KYO-459 the
app writes no project file itself: every write is a request to the project's `kyo serve`,
this machine's or another's, through its file routes (`ideas/`, `ideas-move`,
`ideas-mkdir`, `scratchpad`). The module that decides what may be written is therefore the
same for every viewer. It allows files under a project's `ideas/` and the project's session
scratchpad, each resolved to its real path first (a symlink under `ideas/` aimed at
`kyosei/` is refused, and `tests/seams.spec.ts` plants exactly that), and nothing else.

Two properties hold on the app's side:

- **The bridge has no vocabulary for an absolute path.** `saveScratchpad` takes text and
  no path; `saveIdea`, `createIdea` and the organising calls (`mkdirIdeas`, `renameIdea`,
  `moveIdea`, `deleteIdea`, KYO-250) take a path *relative to `ideas/`*.
- **No write replaces a copy it did not read** (KYO-575). Each tab keeps the text and the
  version (kyo serve's ETag) it loaded or last saved, and every save names them, with
  `If-None-Match: *` for a new file. When the server refuses a stale save, `main` reads
  the current file, merges the tab's edit and the file's change three ways
  (`shared/merge3.ts`, a line-based merge) and writes the merge against the version it was
  merged with. A tab with no unsaved edits follows its file instead: when the events
  stream says it changed, the tab re-reads it and puts it on screen as the app, which arms
  no save.

The app's `kyo` allowlist for this tab is `base` and `worktrees`, both reads, and `promote`
is refused by name (`assertAppMayRun`).

### The scratchpad is deliberately not an idea

It lives at `~/kyosei/<project>/scratchpad.md` (KYO-56's per-project home), so **promotion
and sweep cannot see it by construction** rather than by a rule somebody has to remember.
It is the person's own surface: to-do lists, draft prompts, session notes, none of it
swept, counted or required to have any field. The pane states its full path.

**Where the home is, is asked of `kyo`.** `config.store_home()` and `project_home_name()`
are a real rule (`$KYOSEI_HOME` or `~/kyosei`, then the store root's directory name under
the split layout and the code root's under in-repo), and a TypeScript copy would be a
second implementation of where a project's files live. `kyo worktrees --json` reports
`home` as `<per-project home>/dispatch worktrees`, and its parent is the home; taking a
parent directory is a path operation, not a re-derivation. `worktrees` removes nothing
without `--prune` (law 11), so it is a read. The checks set `KYOSEI_HOME`, which `kyo`
honours, so the scratchpad lands inside the fixture: the seam the tests use is the seam the
consumer uses.

The editor's ribbon is context-dependent. The scratchpad's has one button, enabled when
text is selected, which opens the promote-to-idea dialog (`tabs/compose/idea-dialog.ts`,
KYO-252): the selection becomes a new file under `ideas/`, via a name suggested from the
selection's first line and a folder chosen from the vault's own. It writes a markdown file
and nothing else — no record, no stamp, no ledger entry — and never overwrites an existing
file. The new file's path relative to the project root goes on the clipboard, because the
usual next step is to paste it to the orchestrator. An idea file's own tab has no ribbon
(KYO-439).

### `[[`, `#` and `[[<id>.` offer closed sets

`[[` offers only records that exist in the store, matching id and title (id matches
first). `#` offers only tags that already appear across `ideas/` (frontmatter `tags:` and
body `#tag`s; there is no tag registry, and `kyosei/gate-tags.yml` is a different
vocabulary on a different object). `[[<id>.` offers that record's own child ids
(KYO-234.c5). A dangling reference is not offered, so drift is prevented at the input
rather than flagged by a sweep later. A reader can still type `[[whatever]]` by hand, and
when a trigger matches nothing the popup says so rather than closing.

A typed `[[id]]` is followed with Mod-click (an ordinary click still places the caret).
The editor hands the bracketed text verbatim to `followLink`, which is where the id
grammar lives.

A save is armed only by the person's own edit — a keystroke, a paste, an undo, or a ribbon
button that rewrites text on their behalf — and never by the app putting text on screen
(KYO-150). Text reaches the disk by a debounce after the last keystroke, a save on blur,
and ⌘S, and the header states which of `no changes / unsaved / saving / saved / NOT saved`
is true.

### `ideas.py`, ported once, and not trusted

`shared/ideafile.ts` implements the frontmatter and section readers of `kyosei.ideas` for
the buffer the person is editing and for the vault's tags and promotion stubs. The Base
engine answers for files **on disk**; nothing answers for the text being edited, and
shelling out per keystroke to a process that reads the last saved bytes would answer a
question nobody asked.

It is therefore checked rather than believed: `tests/seams.spec.ts` runs the CLI's own
ideas base over a fixture vault and compares the port against the engine file by file for
properties, sections and stub ids. The stub the app recognises is likewise produced by a
real `kyo promote`, so the day `ideas.promotion_stub` changes shape the check fails.

## Layout

<!-- governs:
- apps/desktop/src/main/preload.ts
- apps/desktop/src/main/remote.ts
- apps/desktop/src/main/projects.ts
- apps/desktop/src/main/service.ts
- apps/desktop/src/main/docs.ts
- apps/desktop/src/main/ideas.ts
- apps/desktop/src/main/procfacts.ts
- apps/desktop/src/main/tabdrag.ts
- apps/desktop/src/renderer/index.html
- apps/desktop/src/renderer/shell.css
- apps/desktop/src/renderer/renderer.ts
- apps/desktop/src/renderer/dom.ts
- apps/desktop/src/renderer/icons.ts
- apps/desktop/src/renderer/overlays.ts
- apps/desktop/src/renderer/links/styles.ts
- apps/desktop/src/renderer/shell/activity-format.ts
- apps/desktop/src/renderer/shell/remote-dialog.ts
- apps/desktop/src/renderer/tabs/home/index.ts
- apps/desktop/src/renderer/tabs/compose/index.ts
- apps/desktop/src/renderer/tabs/compose/editor.ts
- apps/desktop/src/renderer/tabs/compose/complete.ts
- apps/desktop/src/renderer/tabs/compose/idea-dialog.ts
- apps/desktop/src/renderer/tabs/compose/styles.ts
- apps/desktop/src/renderer/tabs/record/index.ts
- apps/desktop/src/renderer/tabs/record/page.ts
- apps/desktop/src/renderer/tabs/record/styles.ts
- apps/desktop/src/renderer/tabs/views/index.ts
- apps/desktop/src/renderer/tabs/views/styles.ts
- apps/desktop/src/renderer/tabs/docs/markdown.ts
- apps/desktop/src/renderer/tabs/docs/mermaid.ts
- apps/desktop/src/renderer/tabs/docs/styles.ts
- apps/desktop/src/shared/merge3.ts
-->

```
src/shared/protocol.ts        the main<->renderer contract (windows, facts, terminal, the read model, layouts)
src/shared/ideafile.ts        `kyosei.ideas`'s frontmatter and section readers, ported and checked
src/shared/merge3.ts          a line-based three-way merge, for a save kyo serve refused as stale (KYO-575)
src/shared/served.ts          where a project is served from, in words
src/shared/controller-trust.ts  what the claude tab says while a session waits on the workspace-trust prompt (KYO-567)
src/main/main.ts              window lifecycle, the window registry, the IPC wiring, layout memory
src/main/preload.ts           the whole renderer capability surface (contextIsolation on)
src/main/claude-preload.ts    the claude view's own preload: one click listener, nothing exposed (KYO-321)
src/main/kyo.ts               THE resolver: two sources, one invocation, quoting, argv guard
src/main/kyoserve.ts          THE transport: a client of kyo serve, a project as (server, name) (KYO-459)
src/main/store.ts             the store layout's file names, and nothing that opens them
src/main/service.ts           the store-facing seam: one implementation for every project (KYO-368, KYO-459)
src/main/projects.ts          this machine's project registry, as the app sees it
src/main/facts.ts             locating the store and the currency verdict
src/main/records.ts           every store read, and the allowlist that keeps them reads
src/main/docs.ts              every read the document views make, and no write
src/main/ideas.ts             the scratchpad and `ideas/`, read and written through kyo serve's file routes
src/main/activity.ts          the activity rows, renamed from `kyo activity --json`
src/main/links.ts             the `kyosei://` grammar, the resolver, and `followLink`
src/main/sessions.ts          which claude.ai page the Remote Control pane opens on
src/main/controller.ts        the orchestrator row: whether the session is running, and Start (KYO-460)
src/main/ssh.ts               running CLI commands on a machine: this one, or another over SSH (KYO-460)
src/main/procfacts.ts         what a process is called, and what the machine is called
src/main/tabdrag.ts           which window's tab strip is under a screen point
src/main/shellenv.ts          the login shell's environment, captured once it worked, and the log of every ask (KYO-229, KYO-266)
src/main/remote.ts            the other machines' kyo serves this app connected to, remembered by address
src/main/terminal.ts          the pty, the tmux session, and removing an earlier build's `kyo` links (KYO-450)
src/main/tmuxenv.ts           its pty-free half: the pane's environment, which tmux, the socket funnel (KYO-388)
src/main/tmuxctl.ts           the one place a tmux command is run and its answer read (KYO-283)
src/renderer/index.html       the one document both window kinds load
src/renderer/renderer.ts      the entry: a shell window, or one tab moved into a window
src/renderer/dom.ts           DOM helpers
src/renderer/icons.ts         the icon set, as path data
src/renderer/overlays.ts      what is drawn over the page: a context menu, a prompt
src/renderer/links/index.ts   the link renderer every text surface uses (KYO-233.c3)
src/renderer/links/styles.ts  the link renderer's CSS
src/renderer/shell/shell.ts   the shell -- layout memory, badges, pop-out, deep links, menu commands
src/renderer/shell/rail.ts    the nav rail, and the tab kinds that contribute to it
src/renderer/shell/sidebar.ts the four lists, and the rows every handler shares
src/renderer/shell/columns.ts columns of tabs: the model, the dragging, the menu, the drop outside
src/renderer/shell/home.ts    the home page: what a new tab or an empty column offers
src/renderer/shell/activity-format.ts     the Activity list's arithmetic and links (KYO-251)
src/renderer/shell/new-project-dialog.ts  New project: a machine, a folder, then `kyo init` (KYO-460)
src/renderer/shell/remote-dialog.ts       Connect to a dev box: an address, then a project
src/renderer/tabs/kind.ts     THE TAB INTERFACE -- read this to write a tab
src/renderer/tabs/registry.ts what the build discovered, checked against its directories
src/renderer/tabs/<kind>/     one directory per tab kind; the build enumerates them
src/renderer/tabs/home/index.ts           the home tab
src/renderer/tabs/claude/index.ts         the claude tab: the rectangle and caption of the Remote Control view
src/renderer/tabs/terminal/index.ts       the terminal tab: xterm.js bound to the main process's pty
src/renderer/tabs/compose/index.ts        the compose tab: the scratchpad and idea files, editable, with autosave
src/renderer/tabs/compose/editor.ts       the CodeMirror editor, its ribbon and its completion popup
src/renderer/tabs/compose/complete.ts     `[[`, `#` and `[[<id>.` completion from the registries
src/renderer/tabs/compose/idea-dialog.ts  the promote-to-idea dialog (KYO-252)
src/renderer/tabs/record/index.ts         the record tab
src/renderer/tabs/record/page.ts          the record page: every child object as a section
src/renderer/tabs/views/index.ts          the views tab: records, awaiting-human, the brief, one document
src/renderer/tabs/views/ids.ts            ids in free text, made into links
src/renderer/tabs/docs/markdown.ts        a document rendered read-only
src/renderer/tabs/docs/mermaid.ts         mermaid diagrams, read-only
src/renderer/tabs/<kind>/styles.ts        that tab's own CSS
src/renderer/shell.css        the v3 stylesheet
build.mjs                     the build and, with `--pack`, the application bundle
tests/fixture.ts              throwaway stores, built by the real CLI, in spacey paths
tests/shell.spec.ts           launches the real app and drives the v3 shell
tests/seams.spec.ts      drives the seams directly, with no app
tests/kyoserve.spec.ts   drives the transport against a real kyo serve on an ephemeral port
tests/remote.spec.ts     the app against two kyo serves: this machine's, and a "dev box"
```

The files under `src/shared/` sit beside the protocol for the same reason: both
processes need them.

## Packaging (KYO-176)

<!-- governs:
- apps/desktop/build.mjs
-->

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

- **No UI framework.** The shell's state is a window list and, per window, columns of
  tabs. A framework would be dependency weight with nothing to pay for it.
- **esbuild** (`build.mjs`) rather than Vite/electron-vite: no dev server, no HMR,
  no plugin graph is wanted here, so a bundler needing no config file is the whole
  requirement. Five bundles plus three copied assets (`build.mjs`).
- **TypeScript** with `tsc --noEmit` as the typecheck — esbuild strips types without
  checking them, so the typecheck is a separate command on purpose.
- **Biome** rather than ESLint: one binary, lint *and* format, no plugin graph to
  assemble for TypeScript.
- **Playwright** (`_electron.launch`) for the checks: it launches the real
  Electron binary and drives the real renderer, and its `waitForEvent("window")`
  is what the multi-window and pop-out assertions need. No browsers are downloaded.
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
(KYO-79) — is not here either; the terminal and the record tabs do not know about each other.
