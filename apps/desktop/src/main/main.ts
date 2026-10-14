// The Electron main process: window lifecycle, the registry of open main windows,
// and -- since KYO-111 -- the three live seams, all of which live on this side of
// the boundary because the renderer is sandboxed and has none of the capabilities
// they need.
//
// The seams themselves are elsewhere and deliberately Electron-free, so they are
// testable without launching an app: `kyo.ts` (the resolver and the one
// invocation), `store.ts` (the read-only database read), `facts.ts` (locating the
// store and asking for the currency), `terminal.ts` (the pty and its session).
// This file holds only the wiring and the per-window lifetimes.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  type IpcMainInvokeEvent,
  ipcMain,
  Menu,
  type MenuItemConstructorOptions,
  nativeTheme,
  screen,
  shell,
  WebContentsView,
} from "electron";
import {
  type ActivityView,
  type AppState,
  type AwaitingHuman,
  asPopoutContext,
  type BaseCatalogue,
  type BaseResult,
  type BriefCatalogue,
  type BriefDocument,
  type BriefDocumentView,
  CHANNEL,
  type ClaudePane,
  type Completions,
  type ControllerCommandOutcome,
  type ControllerState,
  type CurrencyFacts,
  type DocDocument,
  type DocTree,
  type FolderListing,
  type FolderMade,
  type IdeaDocument,
  type IdeaEdit,
  type IdeaTree,
  isPopoutPane,
  isTreeGrouping,
  isTreeScope,
  type LatestShiftNote,
  type LayoutSnapshot,
  type LayoutState,
  type LinkDelivery,
  type LinkOrigin,
  type LiveFacts,
  type MainWindowInfo,
  type NewProjectInput,
  type NewProjectOutcome,
  type OpenSource,
  type PaneBounds,
  type PopoutContext,
  type PopoutPane,
  type ProjectChoice,
  type ProjectEntry,
  type ProjectFacts,
  type ProjectServerEntry,
  type ProjectSource,
  parseWindowShow,
  type RecentForgetInput,
  type RecordDetail,
  type RecordDocument,
  type RecordsList,
  type RecordTree,
  type RemoteConnectInput,
  type RemoteFacts,
  type RemoteOpenInput,
  type RemoteProbeFacts,
  type SaveResult,
  type Scratchpad,
  type ScratchpadSave,
  type ServeFacts,
  type ShellCommand,
  type ShiftNotesCatalogue,
  type SshHostOutcome,
  STRIP_BAND_PX,
  type StoreChange,
  type TabDragEndOutcome,
  type TabDragPoint,
  type TabDragStart,
  type TabDragStartOutcome,
  type TabHandoff,
  type TabSnapshot,
  type TerminalAttachOutcome,
  type TerminalInput,
  type TerminalOffer,
  type TerminalOpen,
  type TerminalResize,
  type TerminalSessionsView,
  type TerminalSize,
  type WindowShow,
  type WriteResult,
} from "../shared/protocol";
import {
  answerCache,
  CONTROLLER_COMMAND_HOME,
  controllerListingOn,
  controllerStatusOn,
  type ListingAnswer,
  listedLive,
  localCommandHost,
  pillsFromListing,
  remoteStatusCache,
  startControllerOn,
} from "./controller";
import { checkKyo, describeResolution, type KyoResolution, resolveKyo } from "./kyo";
import {
  clientFor,
  localServer,
  PROBE_TIMEOUT_MS,
  parseServerAddress,
  type ServeClient,
  type ServedEntry,
  sameServer,
  serveFactsFrom,
} from "./kyoserve";
import { machineName } from "./procfacts";
import { addLocalProject, offeredEntries } from "./projects";
import {
  forgetRemote,
  probeRemote,
  type RemoteConnection,
  readRemotes,
  rememberedRemote,
  rememberRemote,
  remoteEntryFacts,
  remoteFacts,
  remoteLabel,
} from "./remote";
import {
  type KnownProject,
  type PaneTarget,
  type ProjectLocation,
  type ProjectState,
  type RememberedProject,
  type ServedProject,
  type StoreService,
  storeService,
} from "./service";
import { shellEnvLogPath } from "./shellenv";
import {
  type CommandHost,
  childPath,
  controllerAttachArgv,
  createProject,
  folderNameProblem,
  listDirectory,
  makeFolder,
  readSshHosts,
  runProcess,
  serverEntries,
  sshCommandHost,
  sshHostFor,
  sshHostProblem,
  writeSshHost,
} from "./ssh";
import { dropTarget, openedFrom, spareFits, stripAt } from "./tabdrag";
import {
  askedAboutOneProject,
  attachedSession,
  attachSink,
  BARE_PANE_NOTICE,
  clientGoneNotice,
  controllerJoinOverSsh,
  controllerSocketPath,
  controllerTmuxEnv,
  endScreen,
  endSessionNamed,
  endSessions,
  loginEnvironment,
  logShellEnvironmentIn,
  noticeFor,
  openSession,
  type PaneAttachState,
  type PtySession,
  paneInfo,
  planPaneAttach,
  planPaneBare,
  ptyEnvironment,
  ptyTty,
  removeRetiredCli,
  repaintTmuxClient,
  resolveTmux,
  retiredCliLinkDirs,
  type SshPane,
  saidByClient,
  screenOf,
  sessionById,
  sessionPills,
  showBare,
  startClient,
  startSshClient,
  tmuxAvailable,
  USER_DATA_ENV,
} from "./terminal";
import { aliveSessions, attachedClients, detachClient } from "./tmuxctl";

const RENDERER_HTML = join(__dirname, "..", "renderer", "index.html");
const PRELOAD = join(__dirname, "preload.js");
/**
 * The claude view's preload, which is a different file from the shell's (KYO-321).
 *
 * Two constants and two bundles, because the two scripts have opposite boundaries:
 * `preload.js` hands `window.kyosei` to a renderer this app wrote, and
 * `claude-preload.js` hands a remote page nothing at all.
 */
const CLAUDE_PRELOAD = join(__dirname, "claude-preload.js");

// ------------------------------------------------ which project (KYO-175)
//
// Before this record the app could only ever look at one project, and there was no
// in-app way to change it: `projectRoot()` read `KYOSEI_PROJECT_ROOT` or fell back
// to the checkout. What is added here is a third place a root may come from -- a
// human choosing one in the app -- and nothing else about the resolution moves.
//
// Two properties of the shape below are load-bearing and neither is incidental:
//
//   IT IS READ ON EVERY CALL. `projectRoot()` re-resolves per invocation, exactly as
//   it re-read the environment per invocation before. That is what makes a
//   schema-mismatch state re-checkable in place rather than a restart (KYO-79
//   decision 5, KYO-175.c3): the human goes to the other project's controller, the
//   migration happens there, they come back and ask again, in the same window.
//   Hoisting this into a constant is the one change that would quietly kill that
//   criterion while every check still passed.
//
//   THE ENVIRONMENT STILL WINS. `resolveProjectRoot` puts `KYOSEI_PROJECT_ROOT`
//   ahead of the in-app choice (KYO-175.c8), and `adoptProject` below REFUSES a
//   choice made while it is set rather than accepting one it would then rank below.
//   Two mechanisms, deliberately: the precedence keeps a remembered choice from an
//   earlier session from outranking an explicit instruction, and the refusal keeps a
//   human from making a choice that silently does nothing.

/**
 * The in-app choice for this session, or null until one is made.
 *
 * Seeded from the remembered state on first read rather than at load, because
 * `app.getPath("userData")` is not meaningful before the app is ready and this
 * module is evaluated well before that.
 */
let chosenProject: RememberedProject | null = null;

/** The remembered state, read once and thereafter maintained here. */
let projectState: ProjectState | null = null;

function rememberedState(): ProjectState {
  projectState ??= storeService.readProjectState(app.getPath("userData"));
  return projectState;
}

// ------------------------------------------ this machine's project registry (KYO-379)
//
// `$KYOSEI_HOME/projects.json`: the projects this machine holds, which this machine's
// kyo serve serves. The CLI is its one writer (KYO-455), so the app reads it as
// every viewer does -- `/v1/projects` of this machine's server -- and keeps the last
// answer here, for the labels the switcher and the link refusals draw without a
// request each. Re-read whenever a window's facts are read, so it follows `kyo projects
// add|remove` run anywhere.

/** This machine's kyo serve (KYO-459): `127.0.0.1:47370`, or `KYOSEI_SERVE_URL`. */
function localClient(): ServeClient {
  return clientFor(localServer(), true);
}

/** What this machine's kyo serve last listed; null until it has answered once. */
let localServed: ServedEntry[] | null = null;

/** Ask this machine's kyo serve for its projects now, and keep the answer. */
async function refreshLocalServed(): Promise<ServedEntry[] | null> {
  const listed = await localClient().projects(PROBE_TIMEOUT_MS);
  if (listed.ok) localServed = listed.projects;
  return localServed;
}

/**
 * A directory on this machine was opened: make sure this machine's kyo serve serves it,
 * by `kyo projects add` when its registry does not list it yet -- the CLI's one writer,
 * run here (KYO-452). The root to open is the one the registry lists, which the CLI
 * resolves; a refusal is the CLI's own sentence.
 */
async function servedRootFor(
  candidate: string,
): Promise<{ root: string; problem: null } | { root: null; problem: string }> {
  const client = localClient();
  try {
    await client.nameFor(candidate);
    return { root: candidate, problem: null };
  } catch (error) {
    const version = await client.version();
    if (!version.ok) return { root: null, problem: version.problem };
    void error;
  }
  const added = await addLocalProject(kyo(), candidate);
  if (!added.ok) return { root: null, problem: added.problem ?? `${candidate} was not added` };
  await refreshLocalServed();
  return { root: added.root, problem: null };
}

/**
 * The in-app choice: this session's, or the one the last session remembered -- with the
 * kyo serve it was read through (KYO-465), never the bare path.
 */
function chosenOne(): RememberedProject | null {
  return chosenProject ?? rememberedState().chosen;
}

/** The chosen project's root, as `ProjectFacts.chosenRoot` states it. */
function chosenRoot(): string | null {
  return chosenOne()?.root ?? null;
}

/**
 * A project was opened in this app (KYO-465): it heads the recents, keyed by the kyo
 * serve it is read through, and is what the next launch reopens when no window is
 * remembered. A failure to write costs the memory, never the project just opened.
 */
function rememberOpened(project: RememberedProject): void {
  projectState = storeService.withRecent(rememberedState(), project);
  storeService.writeProjectState(app.getPath("userData"), projectState);
}

/** The identity a window is on: its root, and the server it reads through. */
function rememberedOf(entry: ShellWindow): RememberedProject {
  return {
    root: entry.projectRoot,
    connection: entry.remote === null ? null : { host: entry.remote.host, port: entry.remote.port },
  };
}

/**
 * Write down the project every window a reader has is showing (KYO-465.c1), for the
 * next launch to reopen each through the server it came from. A transient window -- a
 * tab torn off, whose tabs go home when it closes (KYO-246) -- is not one of them.
 *
 * Not while quitting: the windows close one by one AFTER `before-quit`, and each close
 * would otherwise take its window off the list the next launch reads. Nor for the last
 * window's own close: on this app that close IS the quit (`window-all-closed`), so what
 * it showed is kept. And not while `KYOSEI_PROJECT_ROOT` pins the app: those windows
 * are the launcher's instruction, not the reader's session.
 */
function rememberWindows(): void {
  if (quitting) return;
  if (storeService.environmentRootOf(process.env.KYOSEI_PROJECT_ROOT) !== null) return;
  const open = shellWindows()
    .filter((entry) => !entry.transient && !entry.win.isDestroyed())
    .map(rememberedOf);
  if (open.length === 0) return;
  projectState = storeService.withWindows(rememberedState(), open);
  storeService.writeProjectState(app.getPath("userData"), projectState);
}

/**
 * Which project a NEW window opens on (KYO-249: the project is a property of the
 * window, and this is only the default for one that names none).
 *
 * Three candidates in a fixed order (`resolveProjectRoot`, which holds the reasoning
 * and is a pure function so a check can exercise the order without an app):
 * `KYOSEI_PROJECT_ROOT`, then the project chosen in the app, then the checkout the
 * app itself was loaded from -- "this repository", KYO-79 decision 3.
 */
function defaultProject(
  resolution: KyoResolution,
): ReturnType<typeof storeService.resolveProjectRoot> {
  return storeService.resolveProjectRoot({
    environment: process.env.KYOSEI_PROJECT_ROOT,
    chosen: chosenOne(),
    checkout: resolution.cliRoot ?? app.getAppPath(),
  });
}

/** How a window came to be on its project, in the switcher's own three words. */
function sourceFor(resolution: KyoResolution, root: string): ProjectSource {
  if (storeService.environmentRootOf(process.env.KYOSEI_PROJECT_ROOT) !== null)
    return "environment";
  if (root === (resolution.cliRoot ?? app.getAppPath())) return "checkout";
  return "chosen";
}

// ------------------------------------------ a window's project, addressed (KYO-370)
//
// A project is (server, name) (KYO-459, KYO-463). A window carries its project's root
// and the server it is read through -- `remote` null for this machine's own kyo serve --
// and the same root can name a different project on another machine (both machines
// may have a `~/src/kyosei`), so nothing keyed by root alone can tell the two
// apart. A `ProjectScope` is what every per-project thing here keys on instead: the
// served project it reads through, the root, and one string for the maps.

interface ProjectScope {
  /** `root` for this machine's project; `host:port:root` for another machine's. */
  key: string;
  root: string;
  service: StoreService;
  remote: RemoteConnection | null;
  /** The project, on the kyo serve that serves it: every read and write goes here. */
  project: ServedProject;
}

function scopeFor(root: string, remote: RemoteConnection | null): ProjectScope {
  return {
    key: remote === null ? root : `${remote.host}:${remote.port}:${root}`,
    root,
    service: storeService,
    remote,
    project:
      remote === null
        ? localClient().project(root)
        : clientFor({ host: remote.host, port: remote.port }).project(root),
  };
}

/** The scope a shell window is on; for no window, the one a command would go to. */
function scopeOf(entry: ShellWindow | undefined): ProjectScope {
  const target = entry ?? commandTarget();
  if (target !== undefined) return scopeFor(target.projectRoot, target.remote);
  const fallback = defaultProject(kyo());
  return scopeFor(fallback.root, fallback.connection);
}

function scopeOfWindow(windowId: number): ProjectScope {
  const entry = windows.get(windowId);
  return scopeOf(entry?.kind === "shell" ? entry : undefined);
}

function scopeKeyOf(entry: ShellWindow): string {
  return scopeFor(entry.projectRoot, entry.remote).key;
}

// ---------------------------------- two builds, one userData directory (KYO-189)
//
// `app.getPath("userData")` comes from `app.getName()`, which takes the package's
// `name` unless `productName` is set. `apps/desktop/package.json` sets
// `name: "@kyosei/desktop"` and NO `productName`, so a dev `electron .` and the
// packaged bundle both land in `~/Library/Application Support/@kyosei/desktop` --
// even though the packaged app's `CFBundleName` is `Kyosei` and its identifier is
// `dev.kyosei.desktop`.
//
// KYO-189 asked for that to be DECIDED rather than inherited, and the decision is:
// **they go on sharing it.** In full, with the consequences, in `README.md` under
// "Two builds, one userData directory"; in short, and this is the part that belongs
// beside the code:
//
//   - Setting `productName` would separate the two directories, and with them the
//     recents list and the caches. Everything a user has already accumulated stays
//     behind in the old directory, visible to whichever build kept the old name and
//     invisible to the other, with nothing saying so.
//   - It would not fix the damaging half. Separating THESE two builds leaves any two
//     checkouts -- two worktrees of this repository, say -- resolving to the same
//     userData and the same shim, which is the same collision with none of the
//     compensation.
//   - What fixed it then was the shim saying who wrote it (`ShimOwner`). KYO-450
//     removed the shims themselves with the bundled runtime, so the collision this
//     paragraph was about cannot happen any more: no build writes a `kyo` that
//     another build's panes run. The directory is still shared, and still decided.
//
// `tests/seams.spec.ts` fails if `productName` appears in the manifest, so this
// decision cannot be reversed by a one-line edit with nobody noticing.

// A THIRD launcher, and the one exception to the sharing (KYO-275). The desktop suite
// starts this app dozens of times per run. Chromium's `--user-data-dir` switch does
// move `app.getPath("userData")`, and the specs that passed it were isolated -- but
// two of the twelve launch sites (`links.spec.ts`, `tmux-owner.spec.ts`) never passed
// it, nothing checked, and every one of those launches went to the same directory as
// the packaged build in daily use, regenerated the live shim, and pointed it at this
// checkout's CLI. That switched the open panes of two consumer projects to a
// schema-20 CLI against schema-18 stores, and KYO-189's "quit the other build" had
// nothing to quit -- the writer was a test that had already exited. So the fixture
// now states a directory on EVERY launch environment through a seam of this app's
// own, read here because `setPath("userData")` only takes effect before `ready`, and
// this module's top level is the last moment that is true. An explicit
// `--user-data-dir` still wins: a switch on one launch is more specific than a
// variable in the environment it inherited.
{
  const declared = process.env[USER_DATA_ENV];
  if (
    declared !== undefined &&
    declared.trim() !== "" &&
    !app.commandLine.hasSwitch("user-data-dir")
  ) {
    app.setPath("userData", declared);
    // `sessionData` follows `userData` only when nothing has read it yet; stated
    // rather than assumed, so caches and storage move with the shim and the log.
    app.setPath("sessionData", declared);
  }
}

/**
 * The resolver runs once per process. Its answer is a property of where this app
 * is installed, not of any window, and re-running it per call would let two
 * invocations in one session disagree about which CLI they used.
 */
let resolution: KyoResolution | null = null;

function kyo(): KyoResolution {
  // KYO-449.c1: a resolution that found NO kyo is not a fact about where this app is
  // installed -- it is the "no kyo" state, whose remedy is to install one and click
  // Check again. Nothing has been run through it, so asking again cannot make two
  // invocations disagree; a resolution that found one is kept for the process.
  if (resolution !== null && resolution.source.program === null) resolution = null;
  if (resolution === null) {
    // KYO-450: a packed app has no runtime of its own, so outside a checkout the
    // answer is the `kyo` on the LOGIN shell's PATH -- the capture every pane already
    // uses, never this process's own PATH (KYO-229).
    const environment = loginEnvironment();
    resolution = resolveKyo({
      startDir: app.getAppPath(),
      path: environment.env === null ? null : (environment.env.PATH ?? ""),
      pathProblem: environment.env === null ? environment.why : null,
    });
  }
  return resolution;
}

interface ShellWindow {
  kind: "shell";
  win: BrowserWindow;
  /**
   * The terminal session this window's pane shows (KYO-124), once it has asked for
   * one. A name the window *refers to*, never a thing it owns: the shell behind it
   * outlives this window, and this field going away with the window ends nothing.
   */
  sessionId: string | null;
  /**
   * The layout this window opens on, or null to open on the desk's remembered one.
   * A window opened to hold a tab moved out of another (KYO-246) is seeded with it.
   */
  seed: LayoutSnapshot | null;
  /**
   * True for a window whose layout is never written to disk (KYO-246): one opened by
   * a pop-out or a drop. What it holds when it closes is handed back to `origin`.
   */
  transient: boolean;
  /** The window a transient one was opened from, for the hand-back at close. */
  origin: number | null;
  /**
   * The project this window is bound to (KYO-249). Every read and write this
   * window's renderer asks for runs here, and the switcher moves THIS window only.
   */
  projectRoot: string;
  projectSource: ProjectSource;
  /**
   * The dev box this window's project lives on, or null for a project on this
   * machine (KYO-370). When set, `projectRoot` is a path on THAT machine and every
   * store read and write this window asks for goes over the wire to it.
   */
  remote: RemoteConnection | null;
  /**
   * The last layout this window reported, kept here for every window and written to
   * disk only for a persistent one. It is what a closing transient window hands back,
   * and what Close Window looks at before asking.
   */
  lastLayout: LayoutSnapshot | null;
  /**
   * Set on a SPARE (KYO-261): a shell booted hidden for window `spareFor`, waiting to
   * become the window a tab of that one is torn off into. Null for every window a
   * reader has. A spare is left out of `shellWindows`, so nothing that lists or targets
   * windows ever sees it.
   */
  spareFor: number | null;
  /** Its renderer has finished booting (`CHANNEL.shellBooted`); only a booted spare is used. */
  booted: boolean;
}

/**
 * A window holding one pane and nothing else. After KYO-246 only the claude.ai view
 * is ever shown this way: a popped-out claude window is the ACCOUNT's window rather
 * than any project window's -- it carries the session switcher, it is where the
 * reader hops between every open project -- so it has no owner, is not taken back
 * into a column when it closes, and there is at most one of it.
 */
interface PaneWindow {
  kind: "pane";
  win: BrowserWindow;
  pane: PopoutPane;
  /** Which tab this window is showing (KYO-232): `{kind, arg}`, and nothing else. */
  context: PopoutContext;
  /** Unused since KYO-246; kept null so `sessionIdFor` has one shape to answer. */
  sessionId: string | null;
}

/** Insertion-ordered: the open-windows cluster lists windows as they opened. */
const windows = new Map<number, ShellWindow | PaneWindow>();

/** The shell windows a reader has: every one except the hidden spares (KYO-261). */
function shellWindows(): ShellWindow[] {
  return [...windows.values()].filter(
    (w): w is ShellWindow => w.kind === "shell" && w.spareFor === null,
  );
}

/** The spare kept for one window, booted or still booting, if it has one. */
function spareOf(ownerId: number): ShellWindow | undefined {
  for (const entry of windows.values()) {
    if (entry.kind === "shell" && entry.spareFor === ownerId && !entry.win.isDestroyed()) {
      return entry;
    }
  }
  return undefined;
}

function openWindowList(): MainWindowInfo[] {
  return shellWindows().map((w) => ({ id: w.win.id }));
}

function stateFor(id: number): AppState {
  const entry = windows.get(id);
  const list = openWindowList();
  if (entry?.kind === "pane") {
    return {
      self: { kind: "pane", id, pane: entry.pane, context: entry.context },
      windows: list,
    };
  }
  return {
    self: {
      kind: "shell",
      id,
      seed: entry?.kind === "shell" ? entry.seed : null,
      transient: entry?.kind === "shell" ? entry.transient : false,
      spare: entry?.kind === "shell" ? entry.spareFor !== null : false,
      projectRoot: entry?.kind === "shell" ? entry.projectRoot : defaultProject(kyo()).root,
    },
    windows: list,
  };
}
function broadcast(): void {
  for (const entry of windows.values()) {
    if (entry.win.isDestroyed()) continue;
    entry.win.webContents.send(CHANNEL.state, stateFor(entry.win.id));
  }
}

function baseWebPreferences() {
  return {
    preload: PRELOAD,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    // A window this app puts behind another -- or, under `--kyosei-window-show`,
    // never brings forward at all -- still has a live pty in it. Chromium's default
    // is to throttle a backgrounded renderer's timers to roughly one tick a second,
    // which for the terminal pane means output arriving in one-second lurches while
    // the human reads something else. Off, for every window, because that is the
    // behaviour this app wants regardless of who is looking at it.
    backgroundThrottling: false,
  };
}

// -------------------------------------------- how windows arrive on screen (KYO-122)
//
// Read once per process, from this process's own argv. Once because it is a
// property of how the app was launched and not of any window, and from argv because
// each launch then states its own answer -- which is what lets one check in a suite
// ask for a visible focused window while every other launch in the same run stays
// quiet. See `WINDOW_SHOWS` in `shared/protocol.ts` for what the three words mean.

const windowShow: WindowShow = parseWindowShow(process.argv);

/**
 * Whether shell windows keep a booted, hidden spare to tear tabs off into (KYO-261).
 * On for a launch that shows its windows; off for the suite's hidden and inactive
 * launches, where a spare per window would double the windows of a memory-tight run
 * for nothing a check there reads -- unless `KYOSEI_PREWARM=on` asks, which is how the
 * check about spares turns it on. `KYOSEI_PREWARM=off` turns it off anywhere.
 */
const prewarm =
  process.env.KYOSEI_PREWARM === "on" ||
  (process.env.KYOSEI_PREWARM !== "off" && windowShow === "focus");

/**
 * Put a window on screen, in whichever of the three ways this launch asked for.
 *
 * Every window in this file goes through here, so "no window this launch opens takes
 * focus" is a property of one switch rather than of remembering to write
 * `showInactive` at each call site.
 */
function revealWindow(win: BrowserWindow, inactive = false): void {
  if (win.isDestroyed()) return;
  if (inactive && windowShow !== "hidden") {
    win.showInactive();
    return;
  }
  switch (windowShow) {
    case "hidden":
      // Nothing. Playwright drives the renderer over CDP, which needs no window on
      // screen; a human who launches this way gets an app they cannot see, which is
      // why `focus` and not this is the default. What makes it SAFE rather than
      // merely quiet is `prepareApp` below, which turns off every form of
      // occluded-renderer throttling by name -- otherwise the checks would run
      // against a renderer whose timers and frames the compositor had slowed down.
      return;
    case "inactive":
      win.showInactive();
      return;
    default:
      win.show();
  }
}

/**
 * Everything that has to happen BEFORE the first window exists.
 *
 * Two jobs, both of which are too late once a window has been created.
 *
 * `app.dock.hide()` makes the process a macOS accessory: no dock icon, and -- the
 * part that matters -- the app never activates itself, so it never pulls itself onto
 * whatever Space the operator is working in. `showInactive` does not buy this. An
 * inactive window is still an on-screen window; on macOS it is still ordered front,
 * and it still paints across a full-screen app sitting in its own Space, which is
 * what the suite was observed doing to a user's screen. macOS only --
 * `app.dock` is undefined everywhere else, hence the optional chain.
 *
 * The Chromium switches are what make `hidden` safe to use. A window that is never
 * shown is occluded as far as the compositor is concerned, and Chromium's response to
 * an occluded renderer is to throttle it: timers to about one tick a second, the
 * renderer process deprioritised, `requestAnimationFrame` starved. That would put the
 * checks in a rendering regime the product never runs in, which is exactly how a
 * check comes to pass vacuously. These three turn each of those off by name, and
 * `backgroundThrottling: false` in `baseWebPreferences` turns off the per-window half.
 * They must be appended before `ready`, because Chromium reads its command line once.
 *
 * Applied for every policy, not only the quiet ones: an app whose terminal pane
 * stutters when the window goes behind another is wrong for a human too.
 */
function prepareApp(): void {
  // One appearance, stated (KYO-122.c6). This app's stylesheet is a single light
  // design -- there is no dark palette anywhere in it -- so a window that followed
  // the OS theme would get a dark frame and dark form controls around a light page.
  // Only the pop-out windows showed it, opening in dark mode beside a light main
  // window: the shell hides its own frame (`hiddenInset`), and the pop-outs have a
  // native one.
  nativeTheme.themeSource = "light";
  app.commandLine.appendSwitch("disable-background-timer-throttling");
  app.commandLine.appendSwitch("disable-renderer-backgrounding");
  app.commandLine.appendSwitch("disable-backgrounding-occluded-windows");
  if (windowShow !== "focus") app.dock?.hide();
}

// Called at load, not from `whenReady`: `appendSwitch` is only read before Chromium
// starts, and `dock.hide()` has to precede the first window rather than merely the
// first `show()`.
prepareApp();

/** A layout holding one tab in one column: what a moved tab's new window opens on. */
function seedFor(tab: TabSnapshot): LayoutSnapshot {
  return {
    columns: [{ widthPercent: 100, tabs: [tab], activeTab: 0 }],
    activeColumn: 0,
    sidebar: { item: null, width: 280 },
  };
}

/** What a spare boots on: one empty column, so it is transient and remembers nothing. */
const SPARE_SEED: LayoutSnapshot = {
  columns: [{ widthPercent: 100, tabs: [], activeTab: -1 }],
  activeColumn: 0,
  sidebar: { item: null, width: 280 },
};

/**
 * Keep one spare for a window while it can tear a tab off into a NEW window -- two or
 * more tabs, since a window's only tab moves the whole window -- and none otherwise
 * (KYO-261): once the tabs are closed, the pre-warmed window closes too. Asked on every
 * layout the window reports, which is every change to its tabs.
 * A spare booted on a project the window has since left is replaced.
 */
function maintainSpare(entry: ShellWindow): void {
  if (!prewarm || entry.spareFor !== null || entry.win.isDestroyed()) return;
  const tabs = (entry.lastLayout?.columns ?? []).reduce(
    (sum, column) => sum + column.tabs.length,
    0,
  );
  const spare = spareOf(entry.win.id);
  if (spare !== undefined && (tabs < 2 || !spareFits(spare, entry))) {
    // `destroy`, not `close`: a spare has nothing to ask about, and a closing one would
    // still be found by `spareOf` until its `closed` fired.
    spare.win.destroy();
  }
  if (tabs < 2 || spareOf(entry.win.id) !== undefined) return;
  createShellWindow({
    spareFor: entry.win.id,
    seed: SPARE_SEED,
    ...openedFrom(entry),
    inactive: true,
  });
}

interface ShellWindowOptions {
  seed?: LayoutSnapshot;
  origin?: number;
  /** Where to open, in screen coordinates: under the pointer that dropped a tab. */
  at?: { x: number; y: number };
  /** A terminal session moving in with the seed (KYO-124: the session moves). */
  sessionId?: string | null;
  /**
   * Show without taking focus: a window opened mid-drag must not steal the pointer
   * stream from the window the drag began in.
   */
  inactive?: boolean;
  /** The dev box that project lives on (KYO-370); a fresh window opens locally. */
  remote?: RemoteConnection | null;
  /** The project to open on (KYO-249); a fresh window opens on the default. */
  projectRoot?: string;
  /** Boot hidden as the spare of this window (KYO-261), never shown until used. */
  spareFor?: number;
}

function createShellWindow(options: ShellWindowOptions = {}): BrowserWindow {
  const transient = options.seed !== undefined;
  const win = new BrowserWindow({
    width: transient ? 1100 : 1440,
    height: transient ? 760 : 900,
    minWidth: 720,
    minHeight: 480,
    ...(options.at === undefined
      ? {}
      : { x: Math.round(options.at.x - 200), y: Math.round(options.at.y - 20) }),
    backgroundColor: "#f7f8fa",
    show: false,
    // The artboard draws the traffic lights inside the app's own title bar; on
    // macOS that is what `hiddenInset` gives us. Other platforms ignore it and
    // keep their native frame above our title bar, which is fine for a scaffold.
    titleBarStyle: "hiddenInset",
    // Centred in the strip row (`--strip-h`, 38px), over the rail's spacer.
    trafficLightPosition: { x: 14, y: 13 },
    webPreferences: baseWebPreferences(),
  });
  // No project named: the default, WITH the server it is read through (KYO-465) -- a
  // chosen project on a dev box opens there, never as this machine's path of that name.
  const fallback = options.projectRoot === undefined ? defaultProject(kyo()) : null;
  const projectRoot = options.projectRoot ?? fallback?.root ?? defaultProject(kyo()).root;
  const remote = fallback === null ? (options.remote ?? null) : fallback.connection;
  windows.set(win.id, {
    kind: "shell",
    win,
    sessionId: options.sessionId ?? null,
    seed: options.seed ?? null,
    transient,
    origin: options.origin ?? null,
    lastLayout: options.seed ?? null,
    projectRoot,
    projectSource: remote !== null ? "remote" : sourceFor(kyo(), projectRoot),
    remote,
    spareFor: options.spareFor ?? null,
    booted: false,
  });
  rememberWindows();
  win.once("ready-to-show", () => {
    // A spare stays hidden; the tear-off that uses it shows it (KYO-261).
    const entry = windows.get(win.id);
    if (entry?.kind === "shell" && entry.spareFor !== null) return;
    revealWindow(win, options.inactive === true);
  });
  win.on("focus", () => {
    lastFocusedShell = win.id;
  });
  // The native window's own blur, forwarded (KYO-257.c1): the renderer's DOM `blur`
  // never fires when focus was in the claude view rather than the shell's page, so a
  // menu stayed open over another app.
  win.on("blur", () => {
    if (!win.isDestroyed()) win.webContents.send(CHANNEL.windowBlur);
  });
  win.on("closed", () => {
    destroyClaude(win.id);
    const entry = windows.get(win.id);
    windows.delete(win.id);
    // The store watch has no tick to notice a window went (KYO-310), so the going
    // says so. Done before the hand-back: that opens nothing, and a sweep that ran
    // after it would still see the same set of windows.
    sweepStoreWatches();
    if (entry?.kind === "shell") handBackTabs(entry);
    // KYO-465.c1: the window is gone from what the next launch reopens.
    if (entry?.kind === "shell" && entry.spareFor === null) rememberWindows();
    if (entry?.kind === "shell" && entry.spareFor === null) {
      // Its spare goes with it (KYO-261) -- and once no window a reader has is left, so
      // does every other spare: a hidden window must never be what keeps the app from
      // quitting when the last real one closes.
      spareOf(win.id)?.win.destroy();
      const readerWindowsLeft = [...windows.values()].some(
        (other) => other.kind === "pane" || other.spareFor === null,
      );
      if (!readerWindowsLeft) {
        for (const other of [...windows.values()]) {
          if (!other.win.isDestroyed()) other.win.destroy();
        }
      }
    }
    broadcast();
  });
  void win.loadFile(RENDERER_HTML);
  broadcast();
  return win;
}

/**
 * A transient window closed: its tabs go back to the window it was opened from
 * (KYO-246).
 *
 * The terminal's session goes with its tab, when the origin has no session of its
 * own by now; otherwise the session simply outlives the window, as every session
 * does (KYO-124), and the origin's own terminal stays what it was.
 */
function handBackTabs(closed: ShellWindow): void {
  if (!closed.transient || closed.origin === null) return;
  const origin = windows.get(closed.origin);
  if (origin?.kind !== "shell" || origin.win.isDestroyed()) return;
  const tabs = (closed.lastLayout?.columns ?? []).flatMap((column) => column.tabs);
  if (tabs.some((tab) => tab.kind === "terminal") && origin.sessionId === null) {
    origin.sessionId = closed.sessionId;
  }
  for (const tab of tabs) {
    const handoff: TabHandoff = { tab, at: null };
    origin.win.webContents.send(CHANNEL.tabReceive, handoff);
  }
}

/** The one claude.ai window, when it is open. */
function claudePaneWindow(): PaneWindow | undefined {
  for (const entry of windows.values()) {
    if (entry.kind === "pane" && entry.pane === "claude" && !entry.win.isDestroyed()) return entry;
  }
  return undefined;
}

function createPaneWindow(pane: PopoutPane, context: PopoutContext): BrowserWindow {
  const win = new BrowserWindow({
    // A claude.ai pane popped out is a chat column, so it opens tall and narrow
    // rather than at the editor's shape.
    width: pane === "claude" ? 520 : 720,
    height: 720,
    // The SHELL's background, not white (KYO-122.c6). A popped-out pane is the same
    // app in another window and must look like it; this is the colour Chromium
    // paints before the renderer's first frame, so a different one here is a flash of
    // the wrong app at every pop-out.
    backgroundColor: "#f7f8fa",
    show: false,
    webPreferences: baseWebPreferences(),
  });
  windows.set(win.id, { kind: "pane", win, pane, context, sessionId: null });
  win.once("ready-to-show", () => revealWindow(win));
  win.on("closed", () => {
    destroyClaude(win.id);
    windows.delete(win.id);
    broadcast();
  });
  void win.loadFile(RENDERER_HTML);
  broadcast();
  return win;
}

function senderWindowId(event: IpcMainInvokeEvent): number | undefined {
  return BrowserWindow.fromWebContents(event.sender)?.id;
}

ipcMain.handle(CHANNEL.hello, (event): AppState | null => {
  const id = senderWindowId(event);
  return id === undefined ? null : stateFor(id);
});

/**
 * Another window, on a project (KYO-249). Validated the way the switcher validates a
 * candidate -- a directory with no project at or above it is refused with the CLI's
 * own sentence -- and the pin refuses too, because a window on another project
 * beside one pinned by `KYOSEI_PROJECT_ROOT` would be the pin silently not pinning.
 */
async function openWindowOn(
  candidate: string | null,
  from?: ShellWindow,
  remote: RemoteConnection | null = null,
): Promise<ProjectChoice> {
  const resolved = kyo();
  if (remote !== null && candidate !== null) {
    // A window on a dev box's project (KYO-374.c1): its kyo serve is asked the way the
    // switcher asks it, then the window opens on that scope, not on a local directory
    // that happens to share the path.
    const probe = await probeRemote(remote);
    if (!probe.ok)
      return refusedChoice(remoteLabel(remote), probe.problem, await windowFacts(from));
    const facts = await projectFacts(candidate, undefined, "remote", remote);
    await rememberDevBox(remote, probe, { root: candidate, project: facts.project });
    rememberOpened({ root: candidate, connection: { host: remote.host, port: remote.port } });
    createShellWindow({ projectRoot: candidate, remote });
    return { project: facts, refused: null, cancelled: false, changed: true };
  }
  if (candidate === null) {
    // A fresh window from a window on a dev box's project stays on that dev box
    // (KYO-374.c1); from anything else, the default a new window opens on.
    const win =
      from !== undefined && from.remote !== null
        ? createShellWindow({ projectRoot: from.projectRoot, remote: from.remote })
        : createShellWindow();
    const entry = windows.get(win.id);
    return {
      project: await windowFacts(entry?.kind === "shell" ? entry : undefined),
      refused: null,
      cancelled: false,
      changed: true,
    };
  }
  const pinned = storeService.environmentRootOf(process.env.KYOSEI_PROJECT_ROOT);
  if (pinned !== null && pinned !== candidate) {
    return refusedChoice(
      candidate,
      `KYOSEI_PROJECT_ROOT is set to ${pinned}, and it outranks any project chosen ` +
        "in the app. Unset it and relaunch to open a window on another project.",
      await projectFacts(pinned),
    );
  }
  // Served by this machine's kyo serve, or made so by `kyo projects add` (KYO-459).
  const served = await servedRootFor(candidate);
  if (served.root === null) {
    return refusedChoice(
      candidate,
      served.problem,
      await projectFacts(defaultProject(resolved).root),
    );
  }
  const root = served.root;
  const probe = await storeService.probeProject(scopeFor(root, null).project);
  if (!probe.located) {
    return refusedChoice(
      candidate,
      probe.problem ?? `no Kyosei project is configured at or above ${candidate}`,
      await projectFacts(defaultProject(resolved).root),
    );
  }
  rememberOpened({ root, connection: null });
  createShellWindow({ projectRoot: root, remote: null });
  return { project: await projectFacts(root), refused: null, cancelled: false, changed: true };
}

ipcMain.handle(
  CHANNEL.openWindow,
  async (event, root: unknown, rawRemote: unknown): Promise<ProjectChoice> => {
    const from = shellEntryOf(event);
    const candidate = typeof root === "string" && root.trim() !== "" ? root : null;
    const remote = (rawRemote ?? null) as Partial<RemoteOpenInput> | null;
    if (remote === null) return openWindowOn(candidate, from);
    const which = {
      host: typeof remote.host === "string" ? remote.host : "",
      port: typeof remote.port === "number" ? remote.port : 0,
      root: candidate ?? (typeof remote.root === "string" ? remote.root : ""),
    };
    if (which.host === "" || which.root === "") {
      return refusedChoice(
        `${which.host}:${which.port}`,
        "a window on another machine's project names its server and its project",
        await windowFacts(from),
      );
    }
    return openWindowOn(which.root, from, { host: which.host, port: which.port });
  },
);

ipcMain.handle(CHANNEL.windowChoose, async (event): Promise<ProjectChoice> => {
  const picked = await pickProjectDirectory(event);
  if (picked === null) {
    return {
      project: await projectFacts(projectCwd(event).cwd),
      refused: null,
      cancelled: true,
      changed: false,
    };
  }
  return openWindowOn(picked);
});

ipcMain.handle(CHANNEL.focusWindow, (_event, id: unknown): void => {
  if (typeof id !== "number") return;
  const entry = windows.get(id);
  if (entry?.kind === "shell" && !entry.win.isDestroyed()) entry.win.focus();
});

/**
 * Move a tab out of a shell window (KYO-246).
 *
 * Two different acts under one verb, and the pane word says which. `claude` opens
 * THE claude.ai window -- the account's, owned by nobody, one at most -- or focuses
 * it. Everything else opens a whole new shell window on the same project with that
 * one tab in it: rail, sidebar, columns, the lot, so a second monitor gets a second
 * workspace with the nav rail and the whole interface, not a bare pane. A terminal
 * takes its session with it.
 */
ipcMain.handle(CHANNEL.popOut, (event, pane: unknown, context: unknown): void => {
  const id = senderWindowId(event);
  if (id === undefined) return;
  // KYO-186 replaced a two-name test with the union's own guard. The test and the
  // union had to be edited together to add a pane, and only one of them would fail
  // loudly if somebody forgot -- a pane whose button silently did nothing.
  if (!isPopoutPane(pane)) return;
  const entry = windows.get(id);
  if (entry?.kind !== "shell") return;
  const asked = asPopoutContext(context);
  if (pane === "claude") {
    const open = claudePaneWindow();
    if (open !== undefined) {
      open.win.focus();
      return;
    }
    createPaneWindow(pane, asked);
    return;
  }
  const tab = asked.tab;
  if (tab === undefined) return;
  let sessionId: string | null = null;
  if (tab.kind === "terminal") {
    sessionId = entry.sessionId;
    entry.sessionId = null;
    releaseViewer(id);
  }
  createShellWindow({ seed: seedFor(tab), origin: id, sessionId, ...openedFrom(entry) });
});

// ------------------------------------------------------ the tab drag (KYO-246)
//
// The way Chrome does it: the moment a tab leaves its strip a window opens around it
// under the pointer and follows the pointer; carried over another window's strip it
// dims and that strip shows where it would land; released there it attaches, released
// anywhere else it stays a window.
//
// The pointer stream stays with the renderer the drag began in -- macOS delivers
// mouse-dragged and mouse-up to the window that got the mouse-down -- so the main
// process is told each point rather than polling the cursor, and the new window is
// shown INACTIVE so it cannot take that stream away.

/**
 * Where the one tab of a freshly torn-off window sits, in that window's own pixels:
 * past the rail (`--rail-w`, 76) and the strip's padding and two nav buttons, and
 * below the strip's own top padding -- the strip IS the top row since KYO-253, there
 * is no title bar above it. Stated here because the main process positions the
 * window before its renderer has laid anything out.
 */
const TORN_TAB_LEFT = 76 + 4 + 22 + 1 + 22 + 1;
const TORN_TAB_TOP = 7;

interface TabDragSession {
  tab: TabSnapshot;
  /** The window following the pointer: a new one, or the sender when it was the tab's only. */
  win: BrowserWindow;
  /** Where the pointer sits inside that window, so it does not jump on the first move. */
  offset: { x: number; y: number };
  wholeWindow: boolean;
  /** The strip a drop would land in right now, told so it can show the insertion. */
  hover: ShellWindow | null;
}

let tabDrag: TabDragSession | null = null;

/**
 * The shell window whose STRIP is under a screen point, other than one. The strip, not
 * the top row: `tabdrag.ts` starts it where the window's columns start, past the rail
 * and the sidebar head (KYO-259.c3).
 */
function stripUnder(x: number, y: number, except: number): ShellWindow | undefined {
  return stripAt(
    shellWindows()
      .filter((entry) => entry.win.id !== except && !entry.win.isDestroyed())
      .map((entry) => ({
        value: entry,
        bounds: entry.win.getContentBounds(),
        visible: entry.win.isVisible(),
        focused: entry.win.isFocused(),
        layout: entry.lastLayout,
      })),
    x,
    y,
    STRIP_BAND_PX,
  );
}

function tellHover(target: ShellWindow | null, at: TabDragPoint | null): void {
  if (target === null || target.win.isDestroyed()) return;
  target.win.webContents.send(CHANNEL.tabDragHover, at);
}

ipcMain.handle(CHANNEL.tabDragStart, (event, raw: unknown): TabDragStartOutcome => {
  const refused = (refusal: string): TabDragStartOutcome => ({ started: false, refusal });
  const id = senderWindowId(event);
  if (id === undefined) return refused("the dragging window is unknown to the main process");
  const sender = windows.get(id);
  if (sender?.kind !== "shell") return refused("only a shell window can tear a tab off");
  const start = raw as Partial<TabDragStart> | null;
  const tab = start?.tab;
  if (
    tab === undefined ||
    tab === null ||
    typeof tab.kind !== "string" ||
    typeof start?.screenX !== "number" ||
    typeof start?.screenY !== "number"
  ) {
    return refused("a tear-off names a tab and a screen point");
  }
  if (tabDrag !== null) return refused("a tab is already being dragged");
  const snapshot: TabSnapshot = { kind: tab.kind, arg: typeof tab.arg === "string" ? tab.arg : "" };
  if (start.wholeWindow === true) {
    const bounds = sender.win.getBounds();
    tabDrag = {
      tab: snapshot,
      win: sender.win,
      offset: { x: start.screenX - bounds.x, y: start.screenY - bounds.y },
      wholeWindow: true,
      hover: null,
    };
    return { started: true, refusal: null };
  }
  let sessionId: string | null = null;
  if (snapshot.kind === "terminal") {
    sessionId = sender.sessionId;
    sender.sessionId = null;
    releaseViewer(id);
  }
  // The pointer lands on the new window's one tab at the spot it took hold of the
  // old one: the tab starts after the rail and the two nav buttons, below the title
  // bar (`TORN_TAB_LEFT`, `TORN_TAB_TOP`), plus where on the tab the grab was.
  const grab = start.grab;
  const offset = {
    x: TORN_TAB_LEFT + (typeof grab?.x === "number" ? grab.x : 40),
    y: TORN_TAB_TOP + (typeof grab?.y === "number" ? grab.y : 16),
  };
  const spare = spareOf(id);
  let opened: BrowserWindow;
  if (spare?.booted && spareFits(spare, sender)) {
    // A booted spare becomes the window (KYO-261): it stops being a spare, takes the
    // tab the way a drop hands one over, and is shown at once -- its shell booted long
    // ago, which is the ~750ms KYO-260.c3 measured the new window spending on screen.
    opened = spare.win;
    spare.spareFor = null;
    spare.origin = id;
    spare.sessionId = sessionId;
    spare.seed = seedFor(snapshot);
    spare.lastLayout = seedFor(snapshot);
    opened.setPosition(Math.round(start.screenX - offset.x), Math.round(start.screenY - offset.y));
    const handoff: TabHandoff = { tab: snapshot, at: null };
    opened.webContents.send(CHANNEL.tabReceive, handoff);
    revealWindow(opened, true);
    if (!sender.win.isDestroyed()) sender.win.webContents.send(CHANNEL.tabDragShown);
    broadcast();
  } else {
    opened = createShellWindow({
      seed: seedFor(snapshot),
      origin: id,
      at: { x: start.screenX - offset.x + 200, y: start.screenY - offset.y + 20 },
      sessionId,
      inactive: true,
      ...openedFrom(sender),
    });
    opened.setPosition(Math.round(start.screenX - offset.x), Math.round(start.screenY - offset.y));
    // Tell the dragging renderer when this window can be seen, so its ghost stays under
    // the pointer until then rather than leaving a gap (KYO-260).
    opened.once("ready-to-show", () => {
      if (!sender.win.isDestroyed()) sender.win.webContents.send(CHANNEL.tabDragShown);
    });
  }
  tabDrag = { tab: snapshot, win: opened, offset, wholeWindow: false, hover: null };
  return { started: true, refusal: null };
});

ipcMain.on(CHANNEL.tabDragMove, (event, raw: unknown) => {
  const point = raw as Partial<TabDragPoint> | null;
  if (
    tabDrag === null ||
    typeof point?.screenX !== "number" ||
    typeof point?.screenY !== "number"
  ) {
    return;
  }
  const id = senderWindowId(event as IpcMainInvokeEvent);
  const drag = tabDrag;
  if (drag.win.isDestroyed()) {
    tabDrag = null;
    return;
  }
  drag.win.setPosition(
    Math.round(point.screenX - drag.offset.x),
    Math.round(point.screenY - drag.offset.y),
  );
  // A strip on another project, or a terminal over a window that already has one, is
  // not a target (`dropTarget`, KYO-470): it shows no insertion.
  const carrier = windows.get(drag.win.id);
  const target = dropTarget(
    carrier?.kind === "shell" ? carrier : undefined,
    drag.tab.kind,
    stripUnder(point.screenX, point.screenY, drag.win.id),
  );
  if (target !== drag.hover) {
    tellHover(drag.hover, null);
    drag.hover = target;
    // The dragged window steps back while a strip is showing where it would land.
    drag.win.setOpacity(target === null ? 1 : 0.45);
  }
  if (target !== null) tellHover(target, { screenX: point.screenX, screenY: point.screenY });
  void id;
});

// A shell's renderer has finished booting (KYO-261): a spare is usable from here.
ipcMain.on(CHANNEL.shellBooted, (event) => {
  const id = BrowserWindow.fromWebContents(event.sender)?.id;
  const entry = id === undefined ? undefined : windows.get(id);
  if (entry?.kind === "shell") entry.booted = true;
});

ipcMain.handle(CHANNEL.tabDragEnd, (_event, raw: unknown): TabDragEndOutcome => {
  const point = raw as Partial<TabDragPoint> | null;
  const drag = tabDrag;
  tabDrag = null;
  if (drag === null) return { attachedTo: null };
  if (!drag.win.isDestroyed()) drag.win.setOpacity(1);
  tellHover(drag.hover, null);
  // The strip under the RELEASE, not the last one a move reported (KYO-260). A release
  // while the tear-off was still being answered arrives with no move before it -- the
  // renderer holds its moves until the window exists, and creating it is the
  // half-second pause a user sees -- so `hover` was still null and a tab dropped
  // straight back on its own strip stayed a window.
  //
  // And only a strip on the tab's own project (KYO-470): the window carrying it was opened
  // on the source window's project (`openedFrom`), so a strip on another root, or on the
  // same root through another server, leaves it there -- a terminal's session included,
  // which must never move to a window it was not attached to.
  const dragged = windows.get(drag.win.id);
  const target = dropTarget(
    dragged?.kind === "shell" ? dragged : undefined,
    drag.tab.kind,
    typeof point?.screenX === "number" && typeof point?.screenY === "number"
      ? stripUnder(point.screenX, point.screenY, drag.win.id)
      : null,
  );
  if (target === null || target.win.isDestroyed() || drag.win.isDestroyed()) {
    // Left where it was let go: a window of its own, focused as a window the reader
    // just made should be (KYO-260). It was shown inactive so it could not take the
    // pointer stream mid-drag; the drag is over now. Only a launch that shows its
    // windows activates the app -- the suite's never do.
    if (!drag.win.isDestroyed() && windowShow === "focus") {
      app.focus({ steal: true });
      drag.win.focus();
    }
    return { attachedTo: null };
  }
  if (drag.tab.kind === "terminal" && dragged?.kind === "shell") {
    target.sessionId = dragged.sessionId;
    dragged.sessionId = null;
    releaseViewer(drag.win.id);
  }
  const handoff: TabHandoff = {
    tab: drag.tab,
    at: { screenX: point?.screenX ?? 0, screenY: point?.screenY ?? 0 },
  };
  target.win.webContents.send(CHANNEL.tabReceive, handoff);
  target.win.focus();
  // The window that carried the tab has nothing left in it: close it without the
  // hand-back a transient close would otherwise perform (the tab just went elsewhere).
  if (dragged?.kind === "shell") dragged.origin = null;
  setTimeout(() => {
    if (!drag.win.isDestroyed()) drag.win.close();
  }, 0);
  return { attachedTo: target.win.id };
});

// ------------------------------------------------------- the application menu
//
// KYO-246. Electron's default menu gave this app a File menu whose only item was
// Close Window on Cmd+W -- the one shortcut a reader with a tab strip presses most,
// and it closed the whole window without asking. The menu is the app's own now:
// Cmd+W closes the active TAB, Close Window moves to Shift+Cmd+W and asks first when
// the window holds tabs, and the workspace's other acts get the accelerators a
// reader expects. Every workspace item is routed to the focused shell window as a
// `ShellCommand`; the standard Edit and Window items are the platform's own roles.

/**
 * The shell window a menu command means: the focused one, else the one focused
 * most recently. The fallback is for the moment between windows -- a dialog just
 * dismissed, a pop-out just closed -- when nothing is focused and the reader's
 * keystroke still has an obvious target.
 */
let lastFocusedShell: number | null = null;

function commandTarget(): ShellWindow | undefined {
  const focused = BrowserWindow.getFocusedWindow();
  const entry = focused === null ? undefined : windows.get(focused.id);
  if (entry?.kind === "shell" && !entry.win.isDestroyed()) return entry;
  const last = lastFocusedShell === null ? undefined : windows.get(lastFocusedShell);
  if (last?.kind === "shell" && !last.win.isDestroyed()) return last;
  return shellWindows().find((candidate) => !candidate.win.isDestroyed());
}

function sendCommand(command: ShellCommand): void {
  const target = commandTarget();
  if (target === undefined) return;
  target.win.webContents.send(CHANNEL.command, command);
}

/** Close the focused window, asking first when it holds tabs (KYO-246). */
async function closeFocusedWindow(): Promise<void> {
  const target = commandTarget();
  if (target === undefined) return;
  const focused = target.win;
  const entry: ShellWindow | undefined = target;
  const open =
    entry?.kind === "shell"
      ? (entry.lastLayout?.columns ?? []).reduce((sum, column) => sum + column.tabs.length, 0)
      : 0;
  if (open > 0) {
    const answer = await dialog.showMessageBox(focused, {
      type: "question",
      buttons: ["Close Window", "Cancel"],
      defaultId: 1,
      cancelId: 1,
      message: `Close this window and its ${open === 1 ? "tab" : `${open} tabs`}?`,
      detail:
        "A terminal's shell keeps running in tmux and every tab can be reopened; this only closes the window.",
    });
    if (answer.response !== 0) return;
  }
  focused.close();
}

// ------------- an earlier build's command line tools, removed at launch (KYO-450)
//
// KYO-315's Install Command Line Tools and its removal twin are gone with the bundled
// runtime they exposed (KYO-443): `kyo` is the user's own install now. What an
// earlier build left behind -- links on the user's PATH into this app's own
// `<userData>/cli/`, and the shim directories under `userData` -- is removed once, at
// launch, by `terminal.ts`'s `removeRetiredCli`, which owns the rule for what is ours.

/** Remove what an earlier build installed, and say so when a link on the PATH went. */
function removeEarlierBuildsCli(): void {
  const environment = loginEnvironment();
  const home = environment.env?.HOME ?? app.getPath("home");
  let cleanup: ReturnType<typeof removeRetiredCli>;
  try {
    cleanup = removeRetiredCli({
      userData: app.getPath("userData"),
      linkDirs: retiredCliLinkDirs(home),
    });
  } catch (error) {
    console.log(`kyosei: could not clear an earlier build's kyo: ${(error as Error).message}`);
    return;
  }
  const removed = [...cleanup.removedLinks, ...cleanup.removedDirs];
  if (removed.length > 0 || cleanup.problems.length > 0) {
    console.log(
      `kyosei: an earlier build's command line tools: removed ${removed.join(", ") || "nothing"}` +
        (cleanup.problems.length === 0 ? "" : `; could not remove ${cleanup.problems.join("; ")}`),
    );
  }
  if (cleanup.message === null) return;
  // Not awaited: a launch does not wait on a reader's OK. Said once, because the next
  // launch finds nothing of ours left to remove.
  void dialog.showMessageBox({
    type: "info",
    buttons: ["OK"],
    message: "Kyosei now uses the kyo you install",
    detail: cleanup.message,
  });
}

function installApplicationMenu(): void {
  const workspace = (label: string, accelerator: string, command: ShellCommand) => ({
    id: command,
    label,
    accelerator,
    click: () => sendCommand(command),
  });
  // The app menu is written out on macOS rather than taken as `role: "appMenu"`. Its
  // project server's item went with the app's server (KYO-459: kyo serve is the
  // server, installed by `kyo serve --install --apply`); KYO-315's Install Command
  // Line Tools… went with the bundled runtime (KYO-450). Everything in it is the
  // platform's own role, in the platform's own order.
  const appMenu: MenuItemConstructorOptions[] =
    process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" },
              { type: "separator" },
              { role: "services" },
              { type: "separator" },
              { role: "hide" },
              { role: "hideOthers" },
              { role: "unhide" },
              { type: "separator" },
              { role: "quit" },
            ],
          },
        ]
      : [];
  const template: MenuItemConstructorOptions[] = [
    ...appMenu,
    {
      label: "File",
      submenu: [
        workspace("New Tab", "CmdOrCtrl+T", "new-tab"),
        {
          id: "new-window",
          label: "New Window",
          accelerator: "CmdOrCtrl+N",
          // The chooser is the renderer's menu (KYO-249): recents and the picker.
          // With no shell window to show it in, a window opens on the default.
          click: () => {
            if (commandTarget() === undefined) createShellWindow();
            else sendCommand("new-window");
          },
        },
        {
          id: "new-project",
          label: "New Project…",
          // KYO-460: a directory on this machine or a dev box, then mkdir, kyo init
          // and kyo controller start there -- the renderer's dialog, over SSH for a dev box.
          click: () => sendCommand("new-project"),
        },
        { type: "separator" },
        workspace("Close Tab", "CmdOrCtrl+W", "close-tab"),
        {
          id: "close-window",
          label: "Close Window",
          accelerator: "Shift+CmdOrCtrl+W",
          click: () => void closeFocusedWindow(),
        },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        workspace("Toggle Split", "CmdOrCtrl+\\", "toggle-split"),
        { type: "separator" },
        workspace("Back", "CmdOrCtrl+[", "back"),
        workspace("Forward", "CmdOrCtrl+]", "forward"),
        { type: "separator" },
        workspace("Next Tab", "Ctrl+Tab", "next-tab"),
        workspace("Previous Tab", "Ctrl+Shift+Tab", "prev-tab"),
        { type: "separator" },
        // What left the rail (KYO-248): the home page offers these, and so does the menu.
        workspace("Scratchpad", "Shift+CmdOrCtrl+P", "open-scratchpad"),
        workspace("Terminal", "Ctrl+`", "open-terminal"),
        workspace("Claude", "Shift+CmdOrCtrl+C", "open-claude"),
        workspace("Activity", "Shift+CmdOrCtrl+A", "open-activity"),
        workspace("Latest Brief", "Shift+CmdOrCtrl+B", "open-brief"),
        { type: "separator" },
        { role: "reload" },
        { role: "toggleDevTools" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
// ------------------------------------------------------------- the live seams

ipcMain.handle(CHANNEL.facts, async (event): Promise<LiveFacts> => {
  const resolved = kyo();
  const { cwd: root, scope } = projectCwd(event);
  const kyoFacts = describeResolution(resolved);
  // KYO-459: every read below is asked of the project's kyo serve. `kyo where` once,
  // handed to both consumers of it (KYO-184), and `kyo list` once, handed to the
  // currency and the record count -- one request each, in parallel, and every answer
  // in this call about the same moment of the store.
  //
  // Sharing rather than caching. The envelope is re-asked on every call to this
  // handler and never held between calls, because that per-call re-ask is what makes
  // a schema-mismatch state -- and a kyo serve that was not running -- re-checkable in
  // place rather than by restart (KYO-79 decision 5, KYO-175.c3).
  const project = scope.project;
  const unreachable = (error: unknown) => ({
    code: 1,
    data: null,
    stderr: (error as Error).message,
  });
  // KYO-449.c1: the kyo this app runs on this machine, judged by its own `kyo version
  // --json` -- on every facts read, which is launch, a project switch and Check again.
  const [version, located, listed, kyoCompat] = await Promise.all([
    project.client.version(),
    scope.service.locateProject(project).catch(
      (error: unknown): ProjectLocation => ({
        layout: null,
        storeRoot: null,
        repoRoot: null,
        branch: null,
        problem: (error as Error).message,
        project: null,
        dbPath: null,
        canOpen: false,
        storeProblem: null,
        mismatch: null,
        buildSchemaVersion: null,
      }),
    ),
    project.json(["list"]).catch(unreachable),
    checkKyo(resolved, { shell: loginEnvironment().shell, cwd: app.getPath("userData") }),
  ]);
  const [currency, store, facts] = await Promise.all([
    scope.service.readCurrency(project, listed),
    scope.service.readStoreFacts(project, located, listed),
    projectFacts(root, located, sourceOf(event), scope.remote),
  ]);
  if (scope.remote === null) void refreshLocalServed();
  // The facts read every shell window makes at boot is also what ARMS the watch behind
  // the sidebar's lists (KYO-310) -- the same shape the two badges have, and for the
  // same reason: an app nobody has opened a window on watches nothing.
  armStoreWatch(scope);
  // KYO-459.c2: whether the project's kyo serve answered at all -- the one state a
  // window shows when it did not, with the commands that start it and Check again --
  // and KYO-449.c5/c6: slow rather than down when it took the connection, and its
  // `/v1/version` judged against KYO_ACCEPTS when it answered.
  const serve: ServeFacts = serveFactsFrom(project.client, version);
  return { store, currency, project: facts, kyo: kyoFacts, serve, kyoCompat };
});

ipcMain.handle(CHANNEL.currency, async (event): Promise<CurrencyFacts> => {
  const { project, service } = projectCwd(event);
  return service.readCurrency(project);
});

// ------------------------------------------------ the project switcher (KYO-175)
//
// Two handlers, and the sentence the records block below states about itself holds
// here too: neither of them can mutate a store. The two that adopt a project change
// only which directory `kyo` is RUN in, and the directory they may change it to must
// already hold a Kyosei project -- `probeProject` refuses everything else at the
// moment it is chosen (KYO-175.c6), rather than adopting it and letting every pane
// then fail separately with its own version of the same sentence.
//
// What is deliberately NOT here is any handler that would run `kyo rebuild` against
// the store that was opened. KYO-79 decision 5 decided against it and the reason is
// worth repeating where the temptation would be: `kyo rebuild --force` rewrites
// `kyosei/config.yml`'s `schema_version` as well as the db (KYO-5.c1), so an app
// bundling schema 16 that "fixed" a consumer's schema-15 store would leave that
// project's OWN `kyo` -- which speaks 15 -- refusing to open the project it had just
// repaired. The read-only viewer would have reached through and broken the thing it
// was reading. The app offers the instruction and nothing else.

/** A project as it stands, without asking `kyo` anything about it. */
function unprobedProject(
  resolution: KyoResolution,
  root: string,
  source: ProjectSource = sourceFor(resolution, root),
  remote: RemoteConnection | null = null,
): ProjectFacts {
  return {
    root,
    source,
    environmentRoot: storeService.environmentRootOf(process.env.KYOSEI_PROJECT_ROOT),
    chosenRoot: chosenRoot(),
    checkoutRoot: resolution.cliRoot ?? app.getAppPath(),
    storeRoot: null,
    project: null,
    mismatch: null,
    // This installation's recents, each on its own server (KYO-465), then the rest of
    // this machine's registry (KYO-379), as this machine's kyo serve last listed it.
    recents: offeredEntries(rememberedState(), localServed, readRemotes(app.getPath("userData"))),
    remote: remoteFacts(remote),
    remotes: remoteEntryFacts(readRemotes(app.getPath("userData"))),
  };
}

/**
 * Which project, and whether its store opens -- asked afresh, every call.
 *
 * Nothing here is cached, and that is KYO-175.c3 rather than an oversight: this IS
 * the re-check. A human whose project was at a schema this build does not speak goes
 * to that project's own controller session, has it upgraded and migrated there, comes
 * back and asks again -- and the same window opens the project, with no restart and
 * no reload of the renderer.
 */
async function projectFacts(
  root: string,
  located?: ProjectLocation,
  source: ProjectSource = sourceFor(kyo(), root),
  remote: RemoteConnection | null = null,
): Promise<ProjectFacts> {
  const facts = unprobedProject(kyo(), root, remote === null ? source : "remote", remote);
  // Asked of the project's kyo serve, which may have gone away since the window moved
  // onto it (KYO-387). These facts are what a REFUSAL is returned with, among other
  // things, so they must not throw: the window's own lists say what failed, where the
  // lists are (KYO-378.c4); here the project is just unprobed. `located`, when the
  // caller already asked `kyo where` about this same project (KYO-184).
  try {
    const probe = await storeService.probeProject(scopeFor(root, remote).project, located);
    return {
      ...facts,
      storeRoot: probe.storeRoot,
      project: probe.project,
      mismatch: probe.mismatch,
    };
  } catch {
    return facts;
  }
}

/** The facts for the project a window is on, remote or not. */
async function windowFacts(entry: ShellWindow | undefined): Promise<ProjectFacts> {
  const scope = scopeOf(entry);
  return projectFacts(scope.root, undefined, entry?.projectSource, scope.remote);
}

/** Nothing was adopted, and this is why. */
function refusedChoice(root: string, problem: string, project: ProjectFacts): ProjectChoice {
  return { project, refused: { root, problem }, cancelled: false, changed: false };
}

/**
 * Point the app at another project.
 *
 * Validated BEFORE it is adopted (KYO-175.c6): the app already has a locate path, it
 * is run against the candidate here, and a directory with no Kyosei project at or
 * above it is refused with the CLI's own sentence about what was looked for. A store
 * at a schema this build does not speak is NOT refused -- it is adopted, and its
 * refusal becomes `mismatch` on the facts, which is what puts the human in front of a
 * screen they can re-check rather than in front of a picker that would not open it.
 */
async function adoptProject(candidate: string, windowId: number): Promise<ProjectChoice> {
  const resolved = kyo();
  const entry = windows.get(windowId);
  const here = entry?.kind === "shell" ? entry.projectRoot : defaultProject(resolved).root;
  const pinned = storeService.environmentRootOf(process.env.KYOSEI_PROJECT_ROOT);
  if (pinned !== null) {
    // KYO-175.c8, said rather than silently applied. The precedence order would
    // already ignore this choice; a control that appears to do something and does
    // nothing is worse than one that refuses and says why (law 6).
    return refusedChoice(
      candidate,
      `KYOSEI_PROJECT_ROOT is set to ${pinned}, and it outranks any project chosen ` +
        "in the app. Unset it and relaunch to choose a project here.",
      await windowFacts(entry?.kind === "shell" ? entry : undefined),
    );
  }
  // KYO-459: a project is one this machine's kyo serve serves; a directory it does not
  // list yet is added by the CLI (`kyo projects add`), whose refusal -- a directory with
  // no initialised project -- is shown as it said it.
  const served = await servedRootFor(candidate);
  if (served.root === null) {
    return refusedChoice(
      candidate,
      served.problem,
      await windowFacts(entry?.kind === "shell" ? entry : undefined),
    );
  }
  const root = served.root;
  const probe = await storeService.probeProject(scopeFor(root, null).project);
  if (!probe.located) {
    return refusedChoice(
      candidate,
      probe.problem ?? `no Kyosei project is configured at or above ${candidate}`,
      await windowFacts(entry?.kind === "shell" ? entry : undefined),
    );
  }
  chosenProject = { root, connection: null };
  // A failure to remember costs the recents list, never the switch: the window has
  // already moved by the time this runs.
  rememberOpened(chosenProject);
  // Coming back from a dev box's project is a move even to the same path (KYO-370):
  // the same string named a directory on another machine a moment ago.
  const changed = root !== here || (entry?.kind === "shell" && entry.remote !== null);
  if (changed && entry?.kind === "shell") {
    // The project moves for THIS window and no other (KYO-249).
    entry.projectRoot = root;
    entry.projectSource = "chosen";
    entry.remote = null;
    rememberWindows();
    await projectMoved(entry);
  }
  const facts = await projectFacts(root, undefined, "chosen");
  if (changed && entry?.kind === "shell" && !entry.win.isDestroyed()) {
    // Told in this order: the project first, so the renderer closes what cannot
    // outlive the previous project; then the layout this project remembers, which
    // the renderer restores whole (KYO-249: "layouts are remembered per project").
    entry.win.webContents.send(CHANNEL.projectChanged, facts);
    entry.win.webContents.send(CHANNEL.layoutChanged, layoutStateFor(entry));
  }
  return { project: facts, refused: null, cancelled: false, changed };
}

/**
 * Everything the main process has to let go of when the project moves.
 *
 * The terminal pane is the part that is easy to miss, and KYO-175.c7 is about it.
 * `startSession` captures `cwd: projectRoot(resolved)` when the shell starts, and the
 * controller session name is the project's PREFIX (KYO-474), which every worktree of one
 * project shares. Both are resolved once, at open. So an already-open pane would keep the previous project's directory and stay
 * attached to the previous project's tmux session, with nothing on screen saying so
 * -- a terminal claiming to be in the project the board beside it is showing, and not
 * being.
 *
 * `endSessions` ends this app's own ptys. In tmux mode that pty is a tmux CLIENT, so
 * ending it detaches; the SESSION is a server-side object and survives, which is what
 * the design asks for -- it is the controller's, and somebody may be attached to it
 * from anywhere (`kyo controller attach`). Nothing here kills a
 * tmux session. Each pane then reopens against the new project, which is a fresh pty
 * at the new cwd attached to the new project's session.
 */
async function projectMoved(entry: ShellWindow): Promise<void> {
  // This window's own pty ends (the id stays; the next open under it starts afresh
  // at the new cwd), and this window's claude view re-resolves. Per window (KYO-249):
  // a window on another project is untouched. The per-project home cache is keyed
  // by root, so nothing there needs invalidating.
  //
  // KYO-186.c9: the claude.ai pane is the second consumer of the switcher KYO-175
  // built, and it belongs here for the same reason the terminal does -- both hold
  // something resolved against the PREVIOUS project, and a pane still showing the
  // previous project's controller session beside a board showing the new project
  // is the same defect KYO-175.c7 is about, one pane over.
  await Promise.all([
    entry.sessionId === null ? Promise.resolve() : endSessionNamed(entry.sessionId),
    claudeFollowsProject(entry.win.id),
  ]);
  // ...and the project this window has LEFT keeps its store watch only if another
  // window is still on it (KYO-310). The new project's is armed by the facts read the
  // renderer makes when it is told the project changed.
  sweepStoreWatches();
}

/**
 * Point THIS window at a dev box's project (KYO-370), read through that machine's kyo
 * serve (KYO-459).
 *
 * Asked before it is adopted, on the switcher's own terms (KYO-175.c6): one request to
 * the address, which fails as "nothing answered" or as kyo serve's own refusal (a Host
 * it was not started to answer, say), naming the address, and is reported where the
 * connection was made rather than shown as an empty store (c5). What is adopted is the
 * project the reader picked from the server's list, else the first it serves.
 *
 * Every refusal here is RETURNED, never thrown (KYO-387): the facts it carries are
 * this window's, and when the window is itself on a dev box that has gone away,
 * `projectFacts` says so by being unprobed rather than by throwing through the IPC
 * layer.
 */
async function adoptRemote(
  connection: RemoteConnection,
  windowId: number,
  wanted: string | null = null,
): Promise<ProjectChoice> {
  const entry = windows.get(windowId);
  const shell = entry?.kind === "shell" ? entry : undefined;
  const label = remoteLabel(connection);
  const pinned = storeService.environmentRootOf(process.env.KYOSEI_PROJECT_ROOT);
  if (pinned !== null) {
    return refusedChoice(
      label,
      `KYOSEI_PROJECT_ROOT is set to ${pinned}, and it outranks any project chosen ` +
        "in the app. Unset it and relaunch to connect to a dev box here.",
      await windowFacts(shell),
    );
  }
  const probe = await probeRemote(connection);
  if (!probe.ok) return refusedChoice(label, probe.problem, await windowFacts(shell));
  const root = wanted ?? probe.projects[0]?.root ?? null;
  if (root === null) {
    return refusedChoice(
      label,
      `the kyo serve at ${label} answered, but its registry lists no project: add one there ` +
        "with `kyo projects add <path>` (over SSH), or make one with New Project…",
      await windowFacts(shell),
    );
  }
  if (!probe.projects.some((project) => project.root === root)) {
    return refusedChoice(
      root,
      `the kyo serve at ${label} does not serve ${root}; it serves ` +
        `${probe.projects.map((project) => project.root).join(", ")}`,
      await windowFacts(shell),
    );
  }
  const facts = await projectFacts(root, undefined, "remote", connection);
  await rememberDevBox(connection, probe, { root, project: facts.project });
  // KYO-465: the choice the next launch reopens, and a recent keyed by this server.
  chosenProject = { root, connection: { host: connection.host, port: connection.port } };
  rememberOpened(chosenProject);
  const already =
    shell !== undefined &&
    shell.remote !== null &&
    sameServer(shell.remote, connection) &&
    shell.projectRoot === root;
  if (already) {
    // Already there: nothing moved, so nothing to tear down (KYO-374).
    const same = { ...facts, remotes: remoteEntryFacts(readRemotes(app.getPath("userData"))) };
    return { project: same, refused: null, cancelled: false, changed: false };
  }
  if (shell !== undefined) {
    shell.projectRoot = root;
    shell.projectSource = "remote";
    shell.remote = connection;
    rememberWindows();
    await projectMoved(shell);
    if (!shell.win.isDestroyed()) {
      shell.win.webContents.send(CHANNEL.projectChanged, facts);
      shell.win.webContents.send(CHANNEL.layoutChanged, layoutStateFor(shell));
    }
  }
  // The list the renderer draws was read before this one was remembered.
  const remembered = { ...facts, remotes: remoteEntryFacts(readRemotes(app.getPath("userData"))) };
  return { project: remembered, refused: null, cancelled: false, changed: true };
}

/**
 * The server the connect dialog named (KYO-459): an address -- `https://<machine>.
 * <tailnet>.ts.net`, or `127.0.0.1:<port>` for a kyo serve on this machine -- checked
 * before anything is sent to it. There is no password and no token to go with it: the
 * tailnet's access rule is the boundary.
 */
function connectionForInput(
  input: Partial<RemoteConnectInput>,
): { ok: true; connection: RemoteConnection } | { ok: false; label: string; problem: string } {
  const typed = typeof input.address === "string" ? input.address : "";
  const parsed = parseServerAddress(typed);
  if (parsed.address === null) return { ok: false, label: typed, problem: parsed.problem };
  return { ok: true, connection: parsed.address };
}

ipcMain.handle(CHANNEL.remoteConnect, async (event, raw: unknown): Promise<ProjectChoice> => {
  const id = senderWindowId(event);
  const input = (raw ?? {}) as Partial<RemoteConnectInput>;
  const parsed = connectionForInput(input);
  if (!parsed.ok || id === undefined) {
    return refusedChoice(
      parsed.ok ? remoteLabel(parsed.connection) : parsed.label,
      parsed.ok ? "a window must ask" : parsed.problem,
      await windowFacts(shellEntryOf(event)),
    );
  }
  const connection = parsed.connection;
  const wanted = typeof input.root === "string" && input.root !== "" ? input.root : null;
  if (input.add === true) {
    // KYO-452: a machine's registry changes by `kyo projects add` run THERE -- over
    // the person's SSH -- never through a server. Said, with the command.
    return refusedChoice(
      wanted ?? "",
      `kyo serve adds no project (KYO-452): on ${remoteLabel(connection)}, run ` +
        `\`kyo projects add ${wanted ?? "<path>"}\` (over SSH), or make one with New Project…`,
      await windowFacts(shellEntryOf(event)),
    );
  }
  if (input.newWindow === true) {
    // The New Window chooser's own row (KYO-374): a window on the dev box, this one
    // untouched. The root is the one picked, else the first the server serves.
    const probe = await probeRemote(connection);
    if (!probe.ok) {
      return refusedChoice(
        remoteLabel(connection),
        probe.problem,
        await windowFacts(shellEntryOf(event)),
      );
    }
    const root = wanted ?? probe.projects[0]?.root ?? null;
    if (root === null) {
      return refusedChoice(
        remoteLabel(connection),
        `the kyo serve at ${remoteLabel(connection)} answered, but its registry lists no project`,
        await windowFacts(shellEntryOf(event)),
      );
    }
    return openWindowOn(root, shellEntryOf(event), connection);
  }
  return adoptRemote(connection, id, wanted);
});

ipcMain.handle(CHANNEL.remoteProbe, async (_event, raw: unknown): Promise<RemoteProbeFacts> => {
  const input = (raw ?? {}) as Partial<RemoteConnectInput>;
  const parsed = connectionForInput(input);
  if (!parsed.ok) {
    return {
      ok: false,
      problem: parsed.problem,
      chosen: null,
      recents: [],
    };
  }
  const probe = await probeRemote(parsed.connection);
  if (!probe.ok) {
    return {
      ok: false,
      problem: probe.problem,
      chosen: null,
      recents: [],
    };
  }
  return {
    ok: true,
    problem: null,
    chosen: null,
    recents: probe.projects,
  };
});

/**
 * Remember every project the dev box's kyo serve listed, the opened one first
 * (KYO-385): the switcher then offers each of them as a row that opens without a dialog.
 */
async function rememberDevBox(
  connection: RemoteConnection,
  probe: { projects: ProjectEntry[] },
  opened: { root: string; project: string | null },
): Promise<void> {
  const userDataDir = app.getPath("userData");
  const at = { host: connection.host, port: connection.port };
  for (const entry of probe.projects.filter((entry) => entry.root !== opened.root).reverse()) {
    rememberRemote(userDataDir, { ...at, root: entry.root, project: entry.project });
  }
  rememberRemote(userDataDir, { ...at, root: opened.root, project: opened.project });
}

ipcMain.handle(CHANNEL.remoteOpen, async (event, raw: unknown): Promise<ProjectChoice> => {
  const id = senderWindowId(event);
  const input = (raw ?? {}) as Partial<RemoteOpenInput>;
  const which = {
    host: typeof input.host === "string" ? input.host : "",
    port: typeof input.port === "number" ? input.port : 0,
    root: typeof input.root === "string" ? input.root : "",
  };
  // A recent on a server (KYO-465) opens as the dev box's own remembered rows do.
  const recent = rememberedState().recents.find((entry) =>
    storeService.sameProject(entry, {
      root: which.root,
      connection: { host: which.host, port: which.port },
    }),
  );
  const recentAt = recent?.connection ?? null;
  const held =
    rememberedRemote(app.getPath("userData"), which) ??
    (recentAt === null ? null : { ...recentAt, root: which.root, project: null });
  if (held === null || id === undefined) {
    return refusedChoice(
      `${which.host}:${which.port}`,
      `no remembered connection to ${which.host}:${which.port} for ${which.root || "that project"}: ` +
        "connect to it again by its address",
      await windowFacts(shellEntryOf(event)),
    );
  }
  // The row's own project, not the server's first one (KYO-385).
  return adoptRemote({ host: held.host, port: held.port }, id, held.root);
});

/**
 * Forget one recent, or all of them (KYO-465.c3), from where the switcher lists them.
 * A project on a server goes from the dev box's remembered rows too, since the switcher
 * offers those beside the recents; `null` clears both. Only this installation's memory
 * moves -- no store, no registry and no server is asked anything -- and the answer is
 * this window's facts, whose `recents` no longer offer what was forgotten.
 */
ipcMain.handle(CHANNEL.recentsForget, async (event, raw: unknown): Promise<ProjectFacts> => {
  const userDataDir = app.getPath("userData");
  const input = raw as Partial<RecentForgetInput> | null;
  if (input === null || input === undefined) {
    projectState = storeService.withoutRecent(rememberedState(), null);
    storeService.writeProjectState(userDataDir, projectState);
    forgetRemote(userDataDir, null);
  } else if (typeof input.root === "string" && input.root !== "") {
    const server = input.server;
    const connection =
      server !== null &&
      server !== undefined &&
      typeof server.host === "string" &&
      typeof server.port === "number"
        ? { host: server.host, port: server.port }
        : null;
    projectState = storeService.withoutRecent(rememberedState(), { root: input.root, connection });
    storeService.writeProjectState(userDataDir, projectState);
    if (connection !== null) forgetRemote(userDataDir, { ...connection, root: input.root });
  }
  return windowFacts(shellEntryOf(event));
});

ipcMain.handle(CHANNEL.projectOpen, async (event, root: unknown): Promise<ProjectChoice> => {
  const id = senderWindowId(event);
  if (typeof root !== "string" || root.trim() === "" || id === undefined) {
    return refusedChoice(
      "",
      "a project must be named by its directory",
      await projectFacts(projectCwd(event).cwd),
    );
  }
  return adoptProject(root, id);
});

/** The native directory picker, owned by the asking window; null when dismissed. */
async function pickProjectDirectory(event: IpcMainInvokeEvent): Promise<string | null> {
  const owner = BrowserWindow.fromWebContents(event.sender);
  const options: Electron.OpenDialogOptions = {
    title: "Open a Kyosei project",
    // The directory the human is *in*, so the picker opens somewhere meaningful
    // rather than at the last place the OS happened to remember.
    defaultPath: projectCwd(event).cwd,
    buttonLabel: "Open",
    // `openDirectory` alone: this app opens a project, which is a directory. There
    // is no file here for anybody to pick, and `createDirectory` is deliberately
    // absent -- the app does not make projects, `kyo init` does.
    properties: ["openDirectory"],
  };
  const result =
    owner === null
      ? await dialog.showOpenDialog(options)
      : await dialog.showOpenDialog(owner, options);
  const picked = result.filePaths[0];
  return result.canceled || picked === undefined ? null : picked;
}

ipcMain.handle(CHANNEL.projectChoose, async (event): Promise<ProjectChoice> => {
  const picked = await pickProjectDirectory(event);
  const id = senderWindowId(event);
  if (picked === null || id === undefined) {
    return {
      project: await projectFacts(projectCwd(event).cwd),
      refused: null,
      cancelled: true,
      changed: false,
    };
  }
  return adoptProject(picked, id);
});

// ----------------------------------------------------------- records mode (KYO-112)
//
// Six handlers, all reads. There is deliberately no seventh: no handler below can
// mutate the store -- which is what makes "records mode is read-only to a human" a
// property of the wiring rather than of the buttons the renderer happens to draw.
// KYO-114 adds handlers that DO write, and they are ideas mode's, further down; not
// one of them can name a path under `kyosei/`, so this statement is unchanged.

/**
 * Where the asking window is looking, for any read or write against a project
 * (KYO-249). A shell window answers with its own project; the claude.ai pane
 * window, which is nobody's, and anything unknown answer with the window a command
 * would go to, else the default a new window opens on.
 */
function projectCwd(event?: IpcMainInvokeEvent | Electron.IpcMainEvent): {
  cwd: string;
  /** The service every store read and write goes through (KYO-459: one, for every project). */
  service: StoreService;
  /** The project on the kyo serve that serves it: this machine's, or a dev box's. */
  project: ServedProject;
  scope: ProjectScope;
} {
  const entry = event === undefined ? undefined : shellEntryOf(event);
  const scope = scopeOf(entry);
  return { cwd: scope.root, service: scope.service, project: scope.project, scope };
}

function shellEntryOf(event: IpcMainInvokeEvent | Electron.IpcMainEvent): ShellWindow | undefined {
  const win = BrowserWindow.fromWebContents(event.sender);
  const entry = win === null ? undefined : windows.get(win.id);
  return entry?.kind === "shell" ? entry : undefined;
}

function sourceOf(event: IpcMainInvokeEvent | Electron.IpcMainEvent): ProjectSource {
  const entry = shellEntryOf(event);
  return entry?.projectSource ?? sourceFor(kyo(), projectCwd(event).cwd);
}

ipcMain.handle(
  CHANNEL.recordsTree,
  async (event, grouping: unknown, scope: unknown): Promise<RecordTree> => {
    const { project, service } = projectCwd(event);
    const asked = isTreeGrouping(grouping) ? grouping : "kind";
    return service.readRecordTree(project, asked, isTreeScope(scope) ? scope : "everything");
  },
);

ipcMain.handle(CHANNEL.recordsBases, async (event): Promise<BaseCatalogue> => {
  const { project, service } = projectCwd(event);
  return service.readBases(project);
});

ipcMain.handle(CHANNEL.recordsRunBase, async (event, name: unknown): Promise<BaseResult> => {
  const { project, service } = projectCwd(event);
  return service.runBase(project, typeof name === "string" ? name : "");
});

ipcMain.handle(CHANNEL.recordsShow, async (event, id: unknown): Promise<RecordDetail> => {
  const { project, service } = projectCwd(event);
  return service.readRecord(project, typeof id === "string" ? id : "");
});

ipcMain.handle(CHANNEL.recordsBriefs, async (event): Promise<BriefCatalogue> => {
  const { project, service } = projectCwd(event);
  return service.readBriefs(project);
});

ipcMain.handle(CHANNEL.recordsBrief, async (event, id: unknown): Promise<BriefDocument> => {
  const { project, service } = projectCwd(event);
  return service.readBrief(project, typeof id === "string" ? id : "");
});

// The daily sheet's split view (KYO-122.c12). A read like the six above, and it is
// among them rather than with ideas mode's handlers for the reason that decides
// which side of that line a handler is on: it opens the STORE, and nothing about it
// can write.
ipcMain.handle(CHANNEL.shiftNotes, async (event): Promise<ShiftNotesCatalogue> => {
  const { project, service } = projectCwd(event);
  return service.readShiftNotes(project);
});

ipcMain.handle(CHANNEL.latestShiftNote, async (event): Promise<LatestShiftNote> => {
  const { project, service } = projectCwd(event);
  return service.readLatestShiftNote(project);
});

// ============================================== the v3 read model (KYO-231)
//
// Five handlers, all reads, and the block says the same thing records mode's does: not
// one of them can mutate a store, so "the v3 tabs are read-only to a human" is a
// property of the wiring. What is new is the push -- `activityChanged` -- and the poll
// behind it, which is the one thing in this app that runs on a timer.

ipcMain.handle(CHANNEL.recordDocument, async (event, id: unknown): Promise<RecordDocument> => {
  const { project, service } = projectCwd(event);
  return service.readRecordDocument(project, typeof id === "string" ? id : "");
});

ipcMain.handle(CHANNEL.awaitingHuman, async (event): Promise<AwaitingHuman> => {
  const { scope } = projectCwd(event);
  const view = await readAwaitingNow(scope);
  // The first read is also what arms the watch behind the badge (KYO-232.c6), and it
  // is what the pushes below are compared against -- so the first push a renderer sees
  // is a CHANGE from what it already drew, never a restatement of it.
  armAwaitingPoll(scope);
  const poll = awaitingPolls.get(scope.key);
  if (poll !== undefined) poll.print = awaitingFingerprint(view);
  return view;
});

ipcMain.handle(CHANNEL.recordsList, async (event, raw: unknown): Promise<RecordsList> => {
  const { project, service } = projectCwd(event);
  const options = (raw ?? {}) as { query?: unknown; base?: unknown };
  return service.readRecordsList(project, {
    query: typeof options.query === "string" ? options.query : "",
    base: typeof options.base === "string" ? options.base : undefined,
  });
});

ipcMain.handle(CHANNEL.latestBrief, async (event): Promise<BriefDocumentView> => {
  const { project, service } = projectCwd(event);
  return service.readLatestBrief(project);
});

// ---------------------------------------------------------- the activity poll
//
// The git half of an activity row changes when a WORKER commits, in another process,
// in another directory, and there is no event this app could subscribe to for it. So
// it is polled -- and only while something is running, because a row whose dispatch
// has finished cannot change in a way anybody is waiting on.
//
// Three properties, each of which is the thing that would otherwise go wrong:
//
//   IT STARTS WHEN SOMEBODY IS LOOKING. The first `getActivity` arms it, so an app
//   whose reader never opens the activity tab never runs a `git` in anger.
//   IT STOPS WHEN NOTHING IS RUNNING. Each tick re-reads, and the tick that finds no
//   running dispatch is the last one; the next `getActivity` arms it again.
//   IT DOES NOT OVERLAP ITSELF. A tick still reading when the next is due is the state
//   that turns a five-second poll into an unbounded pile of `git` processes on a
//   machine where one of them is slow.
//   IT PUSHES ONLY A CHANGE. A tick whose answer has the same shape as the last one
//   sends nothing, on the terms the awaiting-human poll below has always used; a
//   dozen rows redrawn every five seconds is work nobody asked for (KYO-264).
//   IT DOES NOT BLOCK. Every git call under it is an asynchronous spawn
//   (`gitfacts.ts`, KYO-264). It used to be synchronous, which held the main process
//   -- every window's menus, clicks and IPC -- for the whole tick, and with a worker
//   running a suite in the worktree being read that was a spinning cursor every ten
//   seconds: the tick overran the interval, the next tick was skipped as in flight,
//   and the freeze recurred on the doubled period.

/** How often the git half is re-read while a dispatch is running. */
const ACTIVITY_POLL_MS = 5_000;

/**
 * One poll per PROJECT (KYO-249): windows on the same project share a tick, and a
 * window on another project has its own. Stopped when the last window on the
 * project is gone, or -- for activity -- when nothing is running there.
 */
interface Poll {
  timer: ReturnType<typeof setInterval>;
  inFlight: boolean;
  /** The last answer's shape, so an unchanged one is not broadcast. */
  print: string | null;
}

const activityPolls = new Map<string, Poll>();

/** Every shell window on a project, by its scope key (a local root, or `host:port:root`). */
function windowsOn(key: string): ShellWindow[] {
  return shellWindows().filter((entry) => scopeKeyOf(entry) === key && !entry.win.isDestroyed());
}

function stopActivityPoll(root: string): void {
  const poll = activityPolls.get(root);
  if (poll === undefined) return;
  clearInterval(poll.timer);
  activityPolls.delete(root);
}

async function readActivityNow(scope: ProjectScope, finished?: number): Promise<ActivityView> {
  return scope.service.readActivity(scope.project, finished === undefined ? {} : { finished });
}

function armActivityPoll(scope: ProjectScope, view: ActivityView): void {
  const root = scope.key;
  if (view.running === 0) {
    stopActivityPoll(root);
    return;
  }
  if (activityPolls.has(root)) return;
  const poll: Poll = { timer: setInterval(tick, ACTIVITY_POLL_MS), inFlight: false, print: null };
  activityPolls.set(root, poll);
  function tick(): void {
    if (poll.inFlight) return;
    if (windowsOn(root).length === 0) {
      stopActivityPoll(root);
      return;
    }
    poll.inFlight = true;
    void readActivityNow(scope)
      .then((next) => {
        const print = storeService.activityFingerprint(next);
        if (print !== poll.print) {
          poll.print = print;
          for (const entry of windowsOn(root))
            entry.win.webContents.send(CHANNEL.activityChanged, next);
        }
        if (next.running === 0) stopActivityPoll(root);
      })
      .catch(() => stopActivityPoll(root))
      .then(() => {
        poll.inFlight = false;
      });
  }
}

ipcMain.handle(CHANNEL.activity, async (event, raw: unknown): Promise<ActivityView> => {
  const options = (raw ?? {}) as { finished?: unknown };
  const { scope } = projectCwd(event);
  const view = await readActivityNow(
    scope,
    typeof options.finished === "number" ? options.finished : undefined,
  );
  armActivityPoll(scope, view);
  // What the renderer is about to draw is what the next push is compared against, so
  // the first push it sees is a change from that, never a restatement of it.
  const poll = activityPolls.get(scope.key);
  if (poll !== undefined) poll.print = storeService.activityFingerprint(view);
  return view;
});

// ------------------------------------------ the awaiting-human badge (KYO-232.c6)
//
// The records rail icon carries the count of open gates waiting on a person, and a
// badge read once at window start would go on saying `3` through a whole session in
// which the controller cleared every one of them. So it is watched here, on the same
// terms as the activity poll above and for the same reason: the fact changes when a
// CONTROLLER runs a command, in another process, against a database this app only
// reads -- there is no event to subscribe to and the renderer has no way to look.
//
// Armed by the first `getAwaitingHuman`, so an app nobody has opened a window on
// never reads the store on a timer; stopped when the last shell window closes. Sent
// only when the answer MOVED -- the count, or the set of gate ids -- because a push
// every five seconds would redraw a badge that had not changed and would make "the
// badge updated" unobservable to anything watching for it.

/** How often the awaiting-human query is re-asked while a shell window is open. */
const AWAITING_POLL_MS = 5_000;

const awaitingPolls = new Map<string, Poll>();

function awaitingFingerprint(view: AwaitingHuman): string {
  return `${view.count}|${view.gates.map((entry) => entry.gate.id).join(",")}`;
}

function stopAwaitingPoll(root: string): void {
  const poll = awaitingPolls.get(root);
  if (poll === undefined) return;
  clearInterval(poll.timer);
  awaitingPolls.delete(root);
}

async function readAwaitingNow(scope: ProjectScope): Promise<AwaitingHuman> {
  return scope.service.readAwaitingHuman(scope.project);
}

function armAwaitingPoll(scope: ProjectScope): void {
  const root = scope.key;
  if (awaitingPolls.has(root)) return;
  const poll: Poll = { timer: setInterval(tick, AWAITING_POLL_MS), inFlight: false, print: null };
  awaitingPolls.set(root, poll);
  function tick(): void {
    if (poll.inFlight) return;
    if (windowsOn(root).length === 0) {
      stopAwaitingPoll(root);
      return;
    }
    poll.inFlight = true;
    void readAwaitingNow(scope)
      .then((next) => {
        const print = awaitingFingerprint(next);
        if (print === poll.print) return;
        poll.print = print;
        for (const entry of windowsOn(root))
          entry.win.webContents.send(CHANNEL.awaitingChanged, next);
      })
      .catch(() => stopAwaitingPoll(root))
      .then(() => {
        poll.inFlight = false;
      });
  }
}

// ------------------------------------------- the store's own change (KYO-310)
//
// The sidebar's Records, Notes and Ideas lists were snapshots of the moment they were
// opened: `sidebar.refresh()` ran on a rail click and on a project switch and nowhere
// else, so a record filed from the terminal pane beside the app was invisible until
// the reader clicked away and back. This is the other half of the fix -- the half
// that hears the store move.
//
// KYO-459: THE PROJECT'S KYO SERVE SAYS SO. Its events stream (`serve_events.py`)
// pushes `changed` with `store`, `ideas` or `sessions` from a stat fingerprint it takes
// on the machine that holds the project -- `storewatch.ts`'s own rule, moved to the
// server so this machine's projects and a dev box's are heard the same way. So this
// costs nothing while nobody writes, rather than a request every few seconds for the
// rest of the session. A stream that drops and comes back is told as `server`
// (`kyoserve.ts`), and the lists read again the way a first load reads them.
//
// ONE STREAM PER PROJECT, as the polls are (KYO-249): windows on the same project
// share it, a window on another project has its own, and a project no window is on
// has none. Armed by the facts read every shell window makes at boot, and swept when
// a window closes or a project moves.

interface ProjectWatch {
  /** Ends the subscription (`StoreService.subscribe`'s own return). */
  unsubscribe: () => void;
}

const storeWatches = new Map<string, ProjectWatch>();

function stopStoreWatch(root: string): void {
  const held = storeWatches.get(root);
  if (held === undefined) return;
  held.unsubscribe();
  storeWatches.delete(root);
}

function armStoreWatch(scope: ProjectScope): void {
  const root = scope.key;
  if (storeWatches.has(root)) return;
  const unsubscribe = scope.service.subscribe(scope.project, (kind) => {
    for (const entry of windowsOn(root)) {
      // `sessions.json` moved (KYO-382): a controller started, turned or ended in this
      // project, so each claude pane on it asks again where it should be -- and moves
      // only if the answer did. The renderer's lists do not read that file, so it is
      // not told.
      if (kind === "sessions") {
        void claudeFollowsProject(entry.win.id).catch(() => undefined);
        continue;
      }
      entry.win.webContents.send(CHANNEL.storeChanged, { kind } satisfies StoreChange);
    }
  });
  storeWatches.set(root, { unsubscribe });
}

/**
 * Drop the watch on every project no window is open on any more.
 *
 * A watch has no tick to notice this for itself -- that is the whole point of it --
 * so the two moments a window stops being on a project (it closed, or it switched)
 * say so here. Cheap enough to run on both: it is a walk of at most a few windows.
 */
function sweepStoreWatches(): void {
  for (const root of [...storeWatches.keys()]) {
    if (windowsOn(root).length === 0) stopStoreWatch(root);
  }
}

// ======================== layout memory per display configuration (KYO-232.c3)
//
// "Keyed on the attached screens' identities and restored when that configuration is
// seen again" (KYO-227). Three decisions worth stating, because each has an obvious
// alternative that is wrong:
//
//   THE KEY IS THE DISPLAYS, NOT THE WINDOW. A window's own size and position is a
//   thing a human changes constantly and for reasons that have nothing to do with
//   which monitors are plugged in. What is remembered is the arrangement a person
//   works in on each DESK, so the key is `id` plus `bounds` per
//   attached screen, sorted -- so the order two monitors happen to be enumerated in
//   cannot produce two keys for one desk.
//
//   IT LIVES IN THE MAIN PROCESS. `screen` is a main-process module and a sandboxed
//   renderer cannot see a monitor at all. So the key is computed here and the renderer
//   is TOLD it, which also means one file decides it -- and that `localStorage`, which
//   would be keyed per origin rather than per desk, is not involved.
//
//   IT IS ONE FILE UNDER userData, REWRITTEN WHOLE. Layouts are a handful of small
//   objects. A database would be a second store in an app whose whole discipline is
//   that there is one, and it must not be confused with the Kyosei store, which this
//   app never writes to.

/** Where the layouts live, beside the recents list `facts.ts` keeps. */
function layoutsPath(): string {
  return join(app.getPath("userData"), "layouts.json");
}

/**
 * The seam a check simulates two display configurations through (KYO-232.c3).
 *
 * `KYOSEI_DISPLAYS` holds a JSON array of `{id, bounds}`, and when it is set the app
 * keys layouts off that instead of asking `screen`. A consumer's own seam rather than
 * a test-only branch, on the precedent `KYOSEI_TMUX` and `KYOSEI_LOGIN_SHELL` set: it
 * is also how a human pins one layout across a docking station that renumbers its
 * monitors between unplugs. Without it a check of this criterion would need two real
 * monitors and a hand to unplug one, which is not a check anybody can run.
 */
export const DISPLAYS_ENV = "KYOSEI_DISPLAYS";

interface DisplayFact {
  id: number;
  bounds: PaneBounds;
}

function attachedDisplays(): DisplayFact[] {
  const declared = process.env[DISPLAYS_ENV];
  if (declared !== undefined && declared.trim() !== "") {
    try {
      const parsed = JSON.parse(declared) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.map((entry, index) => {
          const raw = (entry ?? {}) as { id?: unknown; bounds?: unknown };
          return { id: typeof raw.id === "number" ? raw.id : index, bounds: asBounds(raw.bounds) };
        });
      }
    } catch {
      // A malformed value falls through to the real screens rather than refusing to
      // start: a typo in an environment variable must not cost a window.
    }
  }
  return screen.getAllDisplays().map((display) => ({
    id: display.id,
    bounds: {
      x: display.bounds.x,
      y: display.bounds.y,
      width: display.bounds.width,
      height: display.bounds.height,
    },
  }));
}

/** The layout key: every attached screen's identity and geometry, sorted. */
function displaySignature(displays: DisplayFact[] = attachedDisplays()): string {
  return displays
    .map((d) => `${d.id}:${d.bounds.x},${d.bounds.y},${d.bounds.width}x${d.bounds.height}`)
    .sort()
    .join("|");
}

function readLayouts(): Record<string, LayoutSnapshot> {
  try {
    const raw = JSON.parse(readFileSync(layoutsPath(), "utf8")) as unknown;
    if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
      return raw as Record<string, LayoutSnapshot>;
    }
  } catch {
    // No file yet, or one this build cannot read. Either way the answer is "this
    // configuration has not been seen before", which is a real answer and not an error.
  }
  return {};
}

/**
 * The key a window's layout is kept under: its PROJECT and the desk (KYO-249 on top
 * of KYO-232.c3). A layout saved before projects were part of the key is read back
 * under the desk alone, so nothing a reader arranged is lost to the change.
 */
function layoutKey(root: string, signature: string): string {
  return `${root}\n${signature}`;
}

function layoutStateFor(entry: ShellWindow | undefined): LayoutState {
  const displays = attachedDisplays();
  const signature = displaySignature(displays);
  const layouts = readLayouts();
  const root = entry?.projectRoot ?? defaultProject(kyo()).root;
  // Keyed on the SCOPE, not the path (KYO-374.c2): a dev box's project and a local
  // one at the same path are two projects, and each remembers its own arrangement.
  const key = entry === undefined ? root : scopeKeyOf(entry);
  return {
    signature,
    root,
    displays,
    layout: layouts[layoutKey(key, signature)] ?? layouts[signature] ?? null,
  };
}

/**
 * Which window's layout is the desk's for its project: the oldest persistent one.
 * Two windows on one project would otherwise take turns overwriting one key.
 */
function persistsLayout(entry: ShellWindow): boolean {
  if (entry.transient) return false;
  const key = scopeKeyOf(entry);
  const first = shellWindows().find(
    (candidate) => !candidate.transient && scopeKeyOf(candidate) === key,
  );
  return first === entry;
}

ipcMain.handle(CHANNEL.layoutLoad, (event): LayoutState => layoutStateFor(shellEntryOf(event)));

/**
 * A save names the project and desk it was made under, and one that no longer matches
 * this window is REFUSED rather than filed under whatever is current (KYO-334).
 *
 * The shell debounces its saves by 250ms and `openProject` moves `entry.projectRoot`
 * before it awaits `projectMoved` and `projectFacts` -- hundreds of milliseconds of
 * subprocess, wide open. A save that left the renderer under project A and arrived
 * here after that move used to be written under project B: the tabs the switch had
 * just closed came back in B, and B's own remembered arrangement was overwritten with
 * A's (KYO-319.r1 measured it, 3 of 3). A screen change moves the other half of the
 * key the same way. Nothing can rescue such a save -- this process cannot tell whether
 * the arrangement described was made before or after the move -- so it is dropped,
 * which costs at most the last 250ms of arranging, and the renderer flushes before the
 * switches it starts itself so that even that is not usually lost.
 */
function layoutSaveFor(entry: ShellWindow, raw: unknown): LayoutSnapshot | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const save = raw as { root?: unknown; signature?: unknown; layout?: unknown };
  if (typeof save.root !== "string" || typeof save.signature !== "string") return null;
  const layout = save.layout;
  if (layout === null || typeof layout !== "object" || Array.isArray(layout)) return null;
  if (save.root !== entry.projectRoot) return null;
  if (save.signature !== displaySignature()) return null;
  return layout as LayoutSnapshot;
}

ipcMain.on(CHANNEL.layoutSave, (event, raw: unknown) => {
  const entry = shellEntryOf(event);
  if (entry === undefined) return;
  const layout = layoutSaveFor(entry, raw);
  if (layout === null) return;
  entry.lastLayout = layout;
  maintainSpare(entry);
  // A transient window (KYO-246) remembers its layout for the hand-back and for
  // Close Window's question, and writes nothing to disk: it is not the desk's
  // arrangement, it is one tab the reader moved aside for a while.
  if (!persistsLayout(entry)) return;
  const layouts = readLayouts();
  layouts[layoutKey(scopeKeyOf(entry), displaySignature())] = layout;
  try {
    mkdirSync(dirname(layoutsPath()), { recursive: true });
    writeFileSync(layoutsPath(), `${JSON.stringify(layouts, null, 2)}\n`, "utf8");
  } catch {
    // A layout that could not be saved costs the next launch its arrangement and
    // nothing else; it must never cost the reader the tab they are in right now.
  }
});

/** The screens changed: tell every shell what THAT configuration remembers. */
function announceDisplays(): void {
  for (const entry of shellWindows()) {
    if (entry.win.isDestroyed()) continue;
    entry.win.webContents.send(CHANNEL.layoutChanged, layoutStateFor(entry));
  }
}

// ------------------------------------------------------------ ideas mode (KYO-114)
//
// Four reads and three writes, and these three are the only handlers in this file
// that change anything on disk. Each one resolves its target inside `ideas.ts`
// against the two roots the human owns and refuses everything else, so the
// boundary is not a property of what these handlers are called: a renderer cannot
// name a path to write, only a path *relative to `ideas/`*, or nothing at all for
// the scratchpad.

ipcMain.handle(CHANNEL.ideasScratchpad, async (event): Promise<Scratchpad> => {
  const { project, service } = projectCwd(event);
  return service.readScratchpad(project);
});

ipcMain.handle(CHANNEL.ideasSaveScratchpad, async (event, save: unknown): Promise<SaveResult> => {
  const { project, service } = projectCwd(event);
  const payload = (save ?? {}) as Partial<ScratchpadSave>;
  // KYO-575: a save names the text AND the version the tab edited it from. One without
  // a version is refused rather than read-then-written: that is the overwrite this
  // record removed, where the check covered only the moment between the two.
  if (
    typeof payload.text !== "string" ||
    typeof payload.base !== "string" ||
    !(typeof payload.version === "string" || payload.version === null)
  ) {
    return refusedSave(
      "the scratchpad was sent a save without its text, base and version; nothing was written",
    );
  }
  return service.writeScratchpad(project, {
    text: payload.text,
    base: payload.base,
    version: payload.version,
  });
});

ipcMain.handle(CHANNEL.ideasTree, async (event): Promise<IdeaTree> => {
  const { project, service } = projectCwd(event);
  return service.readIdeaTree(project);
});

ipcMain.handle(CHANNEL.ideasOpen, async (event, path: unknown): Promise<IdeaDocument> => {
  const { project, service } = projectCwd(event);
  return service.openIdea(project, typeof path === "string" ? path : "");
});

ipcMain.handle(CHANNEL.ideasSave, async (event, edit: unknown): Promise<SaveResult> => {
  const { project, service } = projectCwd(event);
  const payload = (edit ?? {}) as Partial<IdeaEdit>;
  if (
    typeof payload.path !== "string" ||
    typeof payload.body !== "string" ||
    typeof payload.base !== "string" ||
    !(typeof payload.version === "string" || payload.version === null)
  ) {
    return refusedSave(
      "a save must carry a path, a body, and the base and version the tab loaded; nothing was written",
    );
  }
  return service.saveIdea(project, {
    path: payload.path,
    body: payload.body,
    properties: Array.isArray(payload.properties) ? payload.properties : [],
    base: payload.base,
    version: payload.version,
  });
});

/** A save refused before it reached kyo serve: the shape every compose save answers in. */
function refusedSave(problem: string): SaveResult {
  return {
    ok: false,
    path: null,
    problem,
    version: null,
    base: null,
    body: null,
    properties: null,
    merged: null,
  };
}

ipcMain.handle(CHANNEL.ideasCreate, async (event, request: unknown): Promise<WriteResult> => {
  const { project, service } = projectCwd(event);
  const payload = (request ?? {}) as { folder?: unknown; name?: unknown; text?: unknown };
  return service.createIdea(project, {
    folder: typeof payload.folder === "string" ? payload.folder : "",
    name: typeof payload.name === "string" ? payload.name : "",
    text: typeof payload.text === "string" ? payload.text : "",
  });
});

/** The four organising writes (KYO-250); each validates its own payload shape. */
function strField(raw: unknown, key: string): string {
  const value = (raw ?? {}) as Record<string, unknown>;
  return typeof value[key] === "string" ? (value[key] as string) : "";
}

ipcMain.handle(CHANNEL.ideasMkdir, async (event, request: unknown): Promise<WriteResult> => {
  const { project, service } = projectCwd(event);
  return service.mkdirIdeas(project, { folder: strField(request, "folder") });
});

ipcMain.handle(CHANNEL.ideasRename, async (event, request: unknown): Promise<WriteResult> => {
  const { project, service } = projectCwd(event);
  return service.renameIdea(project, {
    path: strField(request, "path"),
    name: strField(request, "name"),
  });
});

ipcMain.handle(CHANNEL.ideasMove, async (event, request: unknown): Promise<WriteResult> => {
  const { project, service } = projectCwd(event);
  return service.moveIdea(project, {
    path: strField(request, "path"),
    folder: strField(request, "folder"),
  });
});

ipcMain.handle(CHANNEL.ideasDelete, async (event, request: unknown): Promise<WriteResult> => {
  const { project, service } = projectCwd(event);
  return service.deleteIdea(project, { path: strField(request, "path") });
});

ipcMain.handle(CHANNEL.copyText, (_event, text: unknown): void => {
  if (typeof text === "string") clipboard.writeText(text);
});

ipcMain.handle(CHANNEL.ideasCompletions, async (event): Promise<Completions> => {
  const { project, service } = projectCwd(event);
  return service.readCompletions(project);
});

// ----------------------------------------------------------- docs mode (KYO-139)
//
// Two handlers and no third. Docs mode is pure view-only past the runtime seam
// (KYO-136 decision 6), so unlike the ideas block above there is no `assertWritable`
// here and nothing for one to guard: a document changes through a controller session
// that a human runs, and this app has no vocabulary in which to ask for the change.

ipcMain.handle(CHANNEL.docsTree, async (event): Promise<DocTree> => {
  const { project, service } = projectCwd(event);
  return service.readDocTree(project);
});

ipcMain.handle(CHANNEL.docsOpen, async (event, path: unknown): Promise<DocDocument> => {
  const { project, service } = projectCwd(event);
  return service.readDoc(project, typeof path === "string" ? path : "");
});

// ------------------------------------------------- the claude.ai pane (KYO-186)
//
// A `WebContentsView`, not an `<iframe>`. claude.ai sends
// `x-frame-options: SAMEORIGIN`, measured, so a frame is refused outright; a
// `WebContentsView` is separate top-level contents and the header does not apply
// to it. That is the whole reason KYO-171's "Terminal -> /rc cannot be built" is
// too broad as a headline: it ruled out INTEGRATING against Remote Control, which
// exposes no API, and rendering the first-party web UI needs no integration
// surface at all.
//
// ONE VIEW PER HOSTING WINDOW, ALL ON ONE PERSISTENT PARTITION.
//
// The partition is what KYO-186.c2 is: `persist:` prefixed, so Chromium keeps the
// cookies on disk under this app's `userData` and a login survives a restart. It
// is shared by every view here, so signing in once in one window signs in
// everywhere -- which is what "asked once and not again" has to mean in an app
// that can have several windows open.
//
// A view PER WINDOW rather than one moved between them, which is where this
// deliberately differs from the terminal pane beside it. A pty is a process with
// one output stream, so two views of it must be two views of ONE session and
// popping out MOVES it. A logged-in web view is not scarce in that way: the
// expensive, stateful thing is the login, and that lives in the partition, not in
// the contents. So each window gets its own page, two windows can sit on two
// different sessions, and popping out is a new page on the same account rather
// than a page taken away from the window it came from.
//
// The view is created lazily -- when a renderer first asks to show one -- because
// a window whose pane is hidden should not be running a Chromium tab nobody is
// looking at, and the Playwright suite launches windows that never ask.

/** Where the login lives. `persist:` is what makes it survive a restart (c2). */
const CLAUDE_PARTITION = "persist:claude-ai";

interface ClaudeView {
  view: WebContentsView;
  /** Attached to its window right now? Hiding detaches without destroying. */
  attached: boolean;
  /** The last URL this view was told to load, so a re-resolve can skip a no-op. */
  loaded: string | null;
}

const claudeViews = new Map<number, ClaudeView>();

/** The last resolution, per window, so a renderer reconnecting can be told it. */
const claudeStates = new Map<number, ClaudePane>();

function paneFrom(target: PaneTarget, problem: string | null = null): ClaudePane {
  return {
    url: target.url,
    outcome: target.outcome,
    why: target.why,
    storeRoot: target.storeRoot,
    deepLinked: target.deepLinked,
    problem,
  };
}

/**
 * Resolve where the pane should point, for the project the app is looking at NOW.
 *
 * Asked afresh every time, and never cached -- `projectFacts()` re-probes the store
 * root on every call for KYO-175.c3's reason, and KYO-186.c9 is the same property
 * one consumer further out: the pane follows a project switch because this question
 * is asked again after the switch, not because anything is invalidated.
 */
/**
 * Where the claude pane points for a window's project. Through the window's own
 * scope (KYO-370.c3): on a dev box's project both the probe and `resolvePaneTarget`
 * run THERE, against the dev box's `sessions.json`, and reach exactly the outcome
 * `sessions.ts` reaches on that machine for itself.
 */
async function resolveClaudeTarget(scope: ProjectScope): Promise<PaneTarget> {
  const { service, root, project } = scope;
  // Both reads through the project's kyo serve (KYO-459): where the store is, and
  // `kyo sessions --json` -- on a dev box's project, that machine's `sessions.json`,
  // reaching exactly the outcome `sessions.ts` reaches there for itself.
  const [probe, read] = await Promise.all([
    service.probeProject(project).catch(() => ({ storeRoot: null })),
    service.readSessionsOf(project),
  ]);
  return service.resolvePaneTarget(probe.storeRoot, root, probe.storeRoot === null ? null : read);
}

function asBounds(raw: unknown): PaneBounds {
  const value = (raw ?? {}) as Partial<Record<keyof PaneBounds, unknown>>;
  const round = (input: unknown, fallback: number): number =>
    typeof input === "number" && Number.isFinite(input) ? Math.round(input) : fallback;
  return {
    x: round(value.x, 0),
    y: round(value.y, 0),
    // A zero-sized view is one Chromium never paints, and a pane that asked before
    // its layout settled would stay blank forever with nothing saying why.
    width: Math.max(1, round(value.width, 1)),
    height: Math.max(1, round(value.height, 1)),
  };
}

function claudeViewFor(win: BrowserWindow): ClaudeView {
  const existing = claudeViews.get(win.id);
  if (existing !== undefined) return existing;
  const view = new WebContentsView({
    webPreferences: {
      partition: CLAUDE_PARTITION,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // ITS OWN PRELOAD, AND NOT THE SHELL'S (KYO-321, and read that decision before
      // touching this). This view renders a first-party site, not this app's renderer,
      // and it must have none of `window.kyosei`: the bridge in `preload.ts` can write
      // idea files and open projects, and handing that to a remote page -- however
      // first-party -- would make every capability on it reachable from whatever that
      // page executes. `claude-preload.js` is the opposite kind of script: it exposes
      // nothing to the page, mutates nothing in it, reads one clicked element's text
      // and sends it here on one one-way channel (`CHANNEL.claudeTextClick`, taken
      // below). Sandbox stays ON -- a sandboxed preload may use `ipcRenderer` and the
      // DOM, which is all of what that file needs.
      preload: CLAUDE_PRELOAD,
      backgroundThrottling: false,
    },
  });
  const entry: ClaudeView = { view, attached: false, loaded: null };
  claudeViews.set(win.id, entry);

  // Anything that would open a new window -- a target=_blank, a link to somewhere
  // else -- goes to the real browser instead. A pane is one page; a popup inside a
  // frameless view the app does not chrome would be a window with no way back.
  //
  // KYO-230 puts one exception in front of that, and only one: a `kyosei://` link in
  // the controller's own message is taken HERE. Handing it to `shell.openExternal`
  // would "work" -- this app registered the scheme, so the OS would route it back --
  // and it is wrong three ways: the round trip out to `launchd` is visible, it can
  // raise another copy of the app, and it throws away the one thing only this call
  // site knows, which is that the click happened in this window's claude view.
  view.webContents.setWindowOpenHandler(({ url }) => {
    const decision = storeService.decideClaudeLink(url, knownProjects(scopeOfWindow(win.id)));
    if (decision.action === "open-in-app") {
      deliverLink(decision.delivery, win.id);
      return { action: "deny" };
    }
    void shell.openExternal(url);
    return { action: "deny" };
  });
  // The other half of the same decision, and it is a different Chromium path: a plain
  // click that would navigate the view itself never reaches the window-open handler.
  // A `kyosei://` navigation is prevented and taken; everything else -- every
  // claude.ai page the reader walks to -- is left alone, because this view renders a
  // first-party site and this app has no business deciding where else it may go.
  view.webContents.on("will-navigate", (event, url) => {
    const decision = storeService.decideClaudeLink(url, knownProjects(scopeOfWindow(win.id)));
    if (decision.action !== "open-in-app") return;
    event.preventDefault();
    deliverLink(decision.delivery, win.id);
  });
  // The third path, and in claude.ai the only one that ever fires (KYO-263): a click
  // on what the site leaves of a `kyosei:` markdown link, which is an anchor with the
  // words and no href at all. Neither hook above sees it -- there is no
  // navigation -- and the right-click menu this record shipped first never appeared,
  // because claude.ai raises its own context menu over ours. So the click is read in
  // the page, by `claude-preload.js` attached above, and arrives on `claudeTextClick`
  // where every other link in this app arrives: `followLink`, then `deliverLink`.
  //
  // The cursor is the other half of the gesture, and it is CSS rather than a DOM
  // write: `insertCSS` styles the page from outside, so nothing of ours is in the
  // page's own document (KYO-321's boundary). Re-applied on every `dom-ready` because
  // a claude.ai navigation is a new document and takes the stylesheet with it.
  view.webContents.on("dom-ready", () => {
    // Every href-less anchor, not only the ones whose text resolves: CSS cannot ask
    // the link grammar, and the alternative -- reading the page to decide -- is the
    // DOM walk this preload deliberately does not do. A click on one that names no
    // record is dropped below, in silence, exactly as it behaved before.
    void view.webContents.insertCSS("a:not([href]) { cursor: pointer; }").catch(() => {
      // A page that went away between `dom-ready` and this call; the next one restyles.
    });
  });
  view.webContents.on("did-fail-load", (_event, code, description, failed) => {
    // `-3` is ERR_ABORTED, which every ordinary in-page navigation produces; only a
    // real failure reaches the pane, and it reaches it as a sentence rather than as
    // a blank rectangle.
    if (code === -3) return;
    const state = claudeStates.get(win.id);
    if (state === undefined) return;
    const next: ClaudePane = { ...state, problem: `${failed}: ${description} (${code})` };
    claudeStates.set(win.id, next);
    if (!win.isDestroyed()) win.webContents.send(CHANNEL.claudeState, next);
  });
  return entry;
}

function detachClaude(windowId: number): void {
  const entry = claudeViews.get(windowId);
  if (entry === undefined || !entry.attached) return;
  const win = windows.get(windowId)?.win;
  if (win !== undefined && !win.isDestroyed()) win.contentView.removeChildView(entry.view);
  entry.attached = false;
}

function destroyClaude(windowId: number): void {
  const entry = claudeViews.get(windowId);
  if (entry === undefined) return;
  detachClaude(windowId);
  // The window is gone and so must its page be: a `WebContentsView` outliving its
  // host is a live Chromium renderer nothing will ever close, which is enough on
  // its own to stop Electron exiting -- the same class of leak `endSessions` exists
  // for on the pty side.
  entry.view.webContents.close();
  claudeViews.delete(windowId);
  claudeStates.delete(windowId);
}

async function showClaude(win: BrowserWindow, bounds: PaneBounds): Promise<ClaudePane> {
  const entry = claudeViewFor(win);
  if (!entry.attached && !win.isDestroyed()) {
    win.contentView.addChildView(entry.view);
    entry.attached = true;
  }
  entry.view.setBounds(bounds);
  const target = await resolveClaudeTarget(scopeOfWindow(win.id));
  const state = paneFrom(target);
  claudeStates.set(win.id, state);
  if (entry.loaded !== target.url) {
    entry.loaded = target.url;
    void entry.view.webContents.loadURL(target.url).catch(() => {
      // `did-fail-load` above is what reports this; the rejection here would be a
      // second, unhandled copy of the same event.
    });
  }
  return state;
}

/**
 * Re-resolve every open claude pane against the project the app now has open.
 *
 * KYO-186.c9, and the reason the feature exists: when the app switches to another
 * project, the pane follows to THAT project's controller session, in the same
 * window, with no restart. It works because the
 * session file lives inside the store and `resolveClaudeTarget` re-probes which
 * store that is -- so switching projects re-points the read, and this navigates.
 *
 * Called from `projectMoved`, beside `endSessions`, because the two are the same
 * obligation: everything the main process has to let go of when the project moves.
 *
 * And from the store watch, when the project's `sessions.json` moved (KYO-382): the
 * same question asked for the other reason its answer changes -- a controller that
 * started after the pane was shown. The rule below is what makes that safe to ask on
 * every hook write: a `Stop` at the end of each turn rewrites the file and moves
 * nothing.
 */
async function claudeFollowsProject(onlyWindowId: number): Promise<void> {
  if (claudeViews.size === 0) return;
  for (const [windowId, entry] of claudeViews) {
    if (windowId !== onlyWindowId) continue;
    const win = windows.get(windowId)?.win;
    if (win === undefined || win.isDestroyed()) continue;
    const target = await resolveClaudeTarget(scopeOfWindow(windowId));
    const state = paneFrom(target);
    claudeStates.set(windowId, state);
    win.webContents.send(CHANNEL.claudeState, state);
    // Navigated even when the URL is unchanged is wrong -- it would throw away a
    // conversation the reader is in the middle of for no reason -- so the pane
    // moves only when the answer actually moved.
    if (entry.loaded === target.url) continue;
    entry.loaded = target.url;
    void entry.view.webContents.loadURL(target.url).catch(() => undefined);
  }
}

ipcMain.handle(CHANNEL.claudeShow, async (event, bounds: unknown): Promise<ClaudePane | null> => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win === null || win.isDestroyed()) return null;
  return showClaude(win, asBounds(bounds));
});

ipcMain.on(CHANNEL.claudeBounds, (event, bounds: unknown) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win === null) return;
  const entry = claudeViews.get(win.id);
  if (entry === undefined || !entry.attached) return;
  entry.view.setBounds(asBounds(bounds));
});

ipcMain.handle(CHANNEL.claudeHide, (event): void => {
  const id = senderWindowId(event);
  // Detached, never destroyed: hiding the pane must not throw away the page the
  // reader was on, exactly as hiding the terminal strip does not kill its pty.
  if (id !== undefined) detachClaude(id);
});

// Stepping aside for an overlay. A view added with `addChildView` is composited above
// the window's own contents, so the shell's menus and prompts -- elements of that
// contents -- are drawn under it wherever they overlap the claude column, and a click
// on them reaches claude.ai. The renderer shows this still in the pane, detaches the
// view with `claudeHide`, and puts it back with `claudeUncover` when the overlay closes.
//
// JPEG rather than PNG: this is encoded on every menu open, at the display's scale,
// and the still is on screen for as long as a menu is.
ipcMain.handle(CHANNEL.claudeSnapshot, async (event): Promise<string | null> => {
  const id = senderWindowId(event);
  const entry = id === undefined ? undefined : claudeViews.get(id);
  if (entry === undefined || !entry.attached) return null;
  const image = await entry.view.webContents.capturePage().catch(() => null);
  if (image === null || image.isEmpty()) return null;
  return `data:image/jpeg;base64,${image.toJPEG(85).toString("base64")}`;
});

ipcMain.handle(
  CHANNEL.claudeUncover,
  async (event, bounds: unknown): Promise<ClaudePane | null> => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win === null || win.isDestroyed()) return null;
    const entry = claudeViews.get(win.id);
    // Never shown before the overlay opened -- the tab became visible under a menu --
    // so this is its first show, and a first show resolves.
    if (entry === undefined) return showClaude(win, asBounds(bounds));
    if (!entry.attached) {
      win.contentView.addChildView(entry.view);
      entry.attached = true;
    }
    entry.view.setBounds(asBounds(bounds));
    return claudeStates.get(win.id) ?? null;
  },
);

// The claude view's one inbound channel (KYO-321, KYO-263.c5): the reader clicked an
// href-less anchor in claude.ai and `claude-preload.js` reported its text.
//
// REGISTERED ONCE, for every claude view this app has, because `ipcMain` is per
// process and not per contents -- so the question "may this message be believed" is
// asked here, of the sender, rather than assumed by where the listener was attached.
// `decideClaudeClick` answers it, and answers it launch-free: a sender that is not one
// of this app's claude views, text that is not a string, and text naming no record are
// three named drops in `links.ts` with checks on each.
//
// A DROP IS A LOG LINE AND NOTHING ELSE -- no dialog, no strip, no menu. A click in
// that view lands on ordinary prose far more often than on a record id, and the reader
// who clicked something that is not a link is owed the nothing they already got. The
// line is there for whoever is debugging a gesture that stopped working, beside every
// other link decision this file narrates on stdout.
ipcMain.on(CHANNEL.claudeTextClick, (event, text: unknown) => {
  const live = [...claudeViews.entries()].filter(
    ([, entry]) => !entry.view.webContents.isDestroyed(),
  );
  // Which window's view sent this, when it was one of them at all. The window decides
  // which project the text is read against, exactly as the two navigation hooks above
  // do: a window looking at another project must not resolve a bare id against this one.
  const windowId = live.find(([, entry]) => entry.view.webContents === event.sender)?.[0];
  const decision = storeService.decideClaudeClick(
    {
      senderId: event.sender.id,
      claudeViewIds: live.map(([, entry]) => entry.view.webContents.id),
      text,
    },
    knownProjects(windowId === undefined ? scopeOf(undefined) : scopeOfWindow(windowId)),
  );
  if (decision.action === "drop") {
    console.log(`kyosei: claude click dropped: ${decision.why}`);
    return;
  }
  const { reference } = decision;
  console.log(
    `kyosei: claude click: ${JSON.stringify(reference.asWritten)} (${reference.form}) ` +
      `names ${reference.target.id}`,
  );
  // The same path every other link in this app takes, so a record opened by a click in
  // claude.ai lands in the same tab, column, window-on-its-project and "you were sent
  // here" strip a clicked link gets. Nothing about opening a record is decided here.
  followAndDeliver(reference.url, "claude-view", windowId);
});

// A menu opened on a window whose app was not active (KYO-257.c1). macOS does not
// activate an app for a right-click, so without this the window never becomes key, the
// `blur` forwarded above never fires, and the menu stays up after the reader clicks
// into another app. `steal`: the reader is acting in this window right now.
ipcMain.on(CHANNEL.claimFocus, (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win === null || win.isDestroyed()) return;
  // Only a launch that shows its windows normally activates the app: the suite's
  // launches are `hidden` or `inactive` precisely so that nothing they open takes focus.
  if (windowShow !== "focus") return;
  app.focus({ steal: true });
  win.focus();
});

/**
 * Resolve again and re-navigate one window's claude view.
 *
 * One definition with two callers (law 3): the reload the human asks for
 * (`CHANNEL.claudeReload`, KYO-191) and the reload a redock forces (KYO-190). They
 * are the same act -- ask where the pane should point now, and put it there -- and
 * writing that twice is how the two would come to disagree about what "reload"
 * means the first time resolution changes.
 *
 * Returns the new state without sending it: an invoke returns it to the caller,
 * while the redock path has no invoke in flight and pushes it instead.
 */
async function refreshClaude(windowId: number): Promise<ClaudePane | null> {
  const win = windows.get(windowId)?.win;
  if (win === undefined || win.isDestroyed()) return null;
  const target = await resolveClaudeTarget(scopeOfWindow(windowId));
  const state = paneFrom(target);
  claudeStates.set(windowId, state);
  const entry = claudeViews.get(windowId);
  if (entry !== undefined) {
    entry.loaded = target.url;
    void entry.view.webContents.loadURL(target.url).catch(() => undefined);
  }
  return state;
}

// KYO-190's redock -- re-navigating the OWNER's docked view when its popped-out
// claude window closed -- is gone with the owner (KYO-246): the claude.ai window is
// the account's now and belongs to no project window. A docked tab resolves afresh
// every time it is shown (`showClaude`), which is the same reload on the same trigger
// the reader actually cares about: coming back to the tab.

ipcMain.handle(CHANNEL.claudeReload, async (event): Promise<ClaudePane | null> => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win === null || win.isDestroyed()) return null;
  return refreshClaude(win.id);
});

// ------------------------------------------------ the controller row (KYO-383, KYO-460)
//
// The claude tab's status line and its Start button, answered by `kyo controller` on
// the machine that holds the project (KYO-460): a local window asks its own service,
// which runs this machine's `kyo`, and a window on a dev box's project runs `kyo
// controller list|start` THERE over the person's SSH, with the server entry's SSH host
// (`ssh.ts`). The dev box's answer is asked at most every `REMOTE_STATUS_TTL_MS`: the
// tab polls every two seconds, and each ask is an SSH connection.
//
// The command Start runs is `controller_command` in that machine's settings.yml, the
// CLI's own (KYO-462), so there is nothing here to change: `editable` is false for
// every window, and the one channel that used to write the app's own preference now
// says where the setting lives.

const devBoxStatus = remoteStatusCache();

/** A dev box's commands: over SSH, to the SSH host its server entry carries. */
function devBoxHost(remote: { host: string; port: number }): CommandHost {
  return sshCommandHost(sshHostFor(app.getPath("userData"), remote.host, remote.port));
}

async function controllerStateOf(
  event: IpcMainInvokeEvent,
  start: boolean,
): Promise<ControllerState> {
  const { scope } = projectCwd(event);
  if (scope.remote !== null) {
    const host = devBoxHost(scope.remote);
    const key = `${host.machine}\0${scope.root}`;
    if (start) {
      const status = await startControllerOn(host, scope.root, runProcess);
      devBoxStatus.put(key, status.problem === null ? status : null);
      return { status, editable: false };
    }
    const status = await devBoxStatus.get(key, () =>
      controllerStatusOn(host, scope.root, runProcess),
    );
    return { status, editable: false };
  }
  const userData = app.getPath("userData");
  // A session started by hand, outside `kyo controller`, is read from the sessions the
  // project's kyo serve reports (KYO-459), never from the store's files directly.
  const sessions = async () => (await scope.service.readSessionsOf(scope.project)).entries;
  const status = start
    ? await scope.service.startController(userData, scope.root, kyo(), sessions)
    : await scope.service.controllerStatus(userData, scope.root, kyo(), sessions);
  return { status, editable: false };
}

ipcMain.handle(CHANNEL.controllerStatus, (event) => controllerStateOf(event, false));

ipcMain.handle(CHANNEL.controllerStart, (event) => controllerStateOf(event, true));

ipcMain.handle(CHANNEL.controllerCommand, async (event): Promise<ControllerCommandOutcome> => {
  const state = await controllerStateOf(event, false);
  return {
    ok: false,
    problem:
      `the command Start runs is ${CONTROLLER_COMMAND_HOME} on ${state.status.machine}, ` +
      "the CLI's own setting (kyo controller reads it there); the app keeps none",
    state,
  };
});

// ------------------------------------------------ New project, over SSH (KYO-460)
//
// This machine, or a dev box connected to before -- nothing else: a server named here
// that the app has no entry for is refused, so the renderer cannot aim a command at an
// arbitrary host. Every command is `ssh.ts`'s: a listing for the picker, `mkdir` for
// New Folder, and `mkdir -p`, `kyo init`, `kyo controller start` for the project.

/** The machine a New project request names, or why it names none the app knows. */
function newProjectHost(server: unknown): { host: CommandHost | null; problem: string | null } {
  if (server === null) return { host: localCommandHost(kyo()), problem: null };
  const named = server as { host?: unknown; port?: unknown } | undefined;
  const known = readRemotes(app.getPath("userData")).find(
    (entry) => entry.host === named?.host && entry.port === named?.port,
  );
  if (known === undefined) {
    return {
      host: null,
      problem: "that server is not one this app has connected to; connect to it first",
    };
  }
  return { host: devBoxHost(known), problem: null };
}

ipcMain.handle(CHANNEL.newProjectServers, (): ProjectServerEntry[] => {
  const userData = app.getPath("userData");
  return serverEntries(readRemotes(userData), machineName(), readSshHosts(userData));
});

ipcMain.handle(
  CHANNEL.newProjectList,
  async (_event, server: unknown, path: unknown): Promise<FolderListing> => {
    const { host, problem } = newProjectHost(server);
    if (host === null) return { ok: false, path: null, folders: [], machine: "", problem };
    const where = typeof path === "string" && path.startsWith("/") ? path : null;
    const listed = await listDirectory(host, where, runProcess);
    return {
      ok: listed.listing !== null,
      path: listed.listing?.path ?? null,
      folders: listed.listing?.folders ?? [],
      machine: host.machine,
      problem: listed.problem,
    };
  },
);

ipcMain.handle(
  CHANNEL.newProjectMkdir,
  async (_event, server: unknown, path: unknown): Promise<FolderMade> => {
    const { host, problem } = newProjectHost(server);
    if (host === null) return { ok: false, problem };
    if (typeof path !== "string" || !path.startsWith("/")) {
      return { ok: false, problem: "the new folder needs an absolute path" };
    }
    const cut = path.lastIndexOf("/");
    const name = path.slice(cut + 1);
    const refused = folderNameProblem(name);
    if (refused !== null) return { ok: false, problem: refused };
    return makeFolder(host, childPath(path.slice(0, cut) || "/", name), runProcess);
  },
);

ipcMain.handle(
  CHANNEL.newProjectCreate,
  async (_event, raw: unknown): Promise<NewProjectOutcome> => {
    const input = (raw ?? {}) as Partial<NewProjectInput>;
    const root = typeof input.root === "string" ? input.root : "";
    const { host, problem } = newProjectHost(input.server ?? null);
    if (host === null) {
      return {
        ok: false,
        root,
        machine: "",
        name: null,
        prefix: null,
        registered: false,
        controller: null,
        failedStep: "mkdir",
        problem,
      };
    }
    const outcome = await createProject(
      host,
      root,
      {
        startController: input.startController === true,
        // Anything but the in-repo choice is the split store, the default (KYO-573).
        storeLocation: input.storeLocation === "in-repo" ? "in-repo" : "split",
      },
      runProcess,
    );
    // A dev box's controller row asks again rather than showing the answer from before.
    if (host.kind === "ssh") devBoxStatus.put(`${host.machine}\0${outcome.root}`, null);
    return outcome;
  },
);

ipcMain.handle(CHANNEL.sshHostSet, (_event, server: unknown, sshHost: unknown): SshHostOutcome => {
  const named = server as { host?: unknown; port?: unknown } | null;
  const userData = app.getPath("userData");
  const known = readRemotes(userData).find(
    (entry) => entry.host === named?.host && entry.port === named?.port,
  );
  if (known === undefined || typeof sshHost !== "string") {
    return {
      ok: false,
      problem: "that server is not one this app has connected to; connect to it first",
      sshHost: "",
    };
  }
  return writeSshHost(userData, known.host, known.port, sshHost);
});

// The account's own session list, named here so a check can assert the fallback
// without importing the resolver's constant twice.
export const CLAUDE_FALLBACK_URL = storeService.claudeIndexUrl;

// ------------------------------------------------ `kyosei://` deep links (KYO-230)
//
// The scheme, the three ways a link arrives, and the one place they meet. The grammar
// -- the id shapes, the URL parsing, the resolver -- is `links.ts` and is deliberately
// Electron-free, so what is here is only the wiring and the two facts only this file
// holds: which projects this app knows, and which window to tell.
//
// THE SCHEME IS REGISTERED TWICE AND FOR TWO DIFFERENT AUDIENCES. A packaged build
// carries `CFBundleURLTypes` in its `Info.plist`, written by `build.mjs --pack`, which
// is how `launchd` knows to start it for a `kyosei://` link clicked in Chrome. A
// development `electron .` has no bundle of its own -- it IS the Electron bundle --
// so `setAsDefaultProtocolClient` is how it claims the scheme at run time. Neither
// covers the other: the first only exists after a pack, the second only lasts as long
// as that install of Electron.
//
// WHAT IS DELIBERATELY ABSENT: `requestSingleInstanceLock`. The usual shape for a
// URL-handling app is one instance that everything else funnels into, and this app is
// not that -- it opens several shell windows on purpose, its checks launch it dozens
// of times, and on macOS a running app receives `open-url` directly without any of it.
// A lock here would make the second launch of the suite quit on startup.

/**
 * The projects a link may name, newest-first with the open one in front.
 *
 * A link that names a project this app does not know is REFUSED rather than opened
 * against whatever happens to be open (c1): a reader put in front of the right id in
 * the wrong project has no way to tell, and the ids look identical. The refusal names
 * this list, which is the remedy -- open that project, follow the link again.
 *
 * Each project is known WITH ITS CONNECTION (KYO-463): the asking window's own first,
 * then every open window's, then this machine's recents, then every project on a
 * server this app has connected to. A root means nothing without the server it is a
 * path on, so two entries with the same root and prefix on two servers are two
 * projects, and `links.ts` prefers the asking window's server between them.
 */
function knownProjects(current: ProjectScope): KnownProject[] {
  const state = rememberedState();
  const seen = new Set<string>();
  const projects: KnownProject[] = [];
  const remembered = readRemotes(app.getPath("userData"));
  const prefixOn = (root: string, remote: RemoteFacts | null): string | null => {
    if (remote === null) {
      // As this machine's kyo serve last listed it: a request per remembered project
      // to label a link is a cost nobody asked for, and a project it does not serve
      // reads as null.
      return localServed?.find((entry) => entry.root === root)?.prefix ?? null;
    }
    return (
      remembered.find((entry) => entry.root === root && sameServer(entry, remote))?.project ?? null
    );
  };
  const add = (root: string, remote: RemoteFacts | null): void => {
    const key = remote === null ? root : `${remote.host}:${remote.port}:${root}`;
    if (seen.has(key)) return;
    seen.add(key);
    projects.push({
      prefix: prefixOn(root, remote),
      root,
      connection: remote === null ? null : { host: remote.host, port: remote.port },
      current:
        key ===
        (current.remote === null
          ? current.root
          : `${current.remote.host}:${current.remote.port}:${current.root}`),
    });
  };
  add(current.root, current.remote);
  for (const entry of shellWindows()) add(entry.projectRoot, entry.remote);
  for (const entry of state.recents) add(entry.root, entry.connection);
  for (const entry of remembered) add(entry.root, { host: entry.host, port: entry.port });
  return projects;
}

/**
 * Hand a resolved link -- or its refusal -- to a window, and say so on stdout.
 *
 * ONE SHELL WINDOW, chosen rather than broadcast: a link opens a tab, and a link that
 * opened the same tab in every window would be four tabs for one click. The window the
 * link came from when there is one (a click inside that window's claude view), the
 * focused shell otherwise, and the first shell when nothing is focused.
 *
 * The `console.log` is the placeholder half this record ships on purpose. KYO-233 is
 * the renderer that draws the target and its "you were sent here" strip; until it
 * exists the v2 renderer subscribes to nothing, and a link that resolved correctly
 * would otherwise be indistinguishable from one that vanished. The line names the
 * target and the source, so the main-process half is observable on its own -- which is
 * also what the checks read.
 */
function deliverLink(
  delivery: LinkDelivery,
  preferWindowId?: number,
  origin: LinkOrigin | null = null,
): void {
  if (delivery.ok && origin !== null) delivery.request.origin = origin;
  if (delivery.ok) {
    const { target, source, column } = delivery.request;
    console.log(
      `kyosei: open ${target.kind} ${target.id}` +
        `${target.anchor === null ? "" : ` at ${target.anchorKind} ${target.anchor}`}` +
        ` in the ${column} column, from ${source}`,
    );
  } else {
    console.log(`kyosei: link not followed (${delivery.outcome}): ${delivery.refusal}`);
  }
  const preferred = preferWindowId === undefined ? undefined : windows.get(preferWindowId);
  const preferredShell = preferred?.kind === "shell" ? preferred : undefined;
  if (!delivery.ok) {
    // A refusal goes to whoever asked, else whoever is looking.
    const target =
      preferredShell ?? shellWindows().find((entry) => entry.win.isFocused()) ?? shellWindows()[0];
    if (target === undefined || target.win.isDestroyed()) return;
    target.win.webContents.send(CHANNEL.openTarget, delivery);
    return;
  }
  // A link opens in a window ON ITS PROJECT (KYO-249) -- its project on ITS SERVER
  // (KYO-463): the window it came from when that window is on the project, else the
  // focused window on it, else any window on it -- else a new window is opened on it,
  // on that same connection, and the link delivered once that window can take it (the
  // same wait a launch link takes, `deliverLaunchLink`). A window on another server
  // that shares the root is not on the project.
  const root = delivery.request.projectRoot;
  const connection = delivery.request.connection;
  if (delivery.request.choice !== null) console.log(`kyosei: ${delivery.request.choice}`);
  const remote = connection === null ? null : { host: connection.host, port: connection.port };
  const key = scopeFor(root, remote).key;
  const on = windowsOn(key);
  const target =
    (preferredShell !== undefined && scopeKeyOf(preferredShell) === key
      ? preferredShell
      : undefined) ??
    on.find((entry) => entry.win.isFocused()) ??
    on[0];
  if (target !== undefined) {
    target.win.webContents.send(CHANNEL.openTarget, delivery);
    target.win.focus();
    return;
  }
  const opened = createShellWindow({ projectRoot: root, remote });
  opened.once("ready-to-show", () => {
    setImmediate(() => {
      if (!opened.isDestroyed()) opened.webContents.send(CHANNEL.openTarget, delivery);
    });
  });
}

/** Follow one link string from one of the three places links arrive. */
function followAndDeliver(
  raw: string,
  source: OpenSource,
  preferWindowId?: number,
  origin: LinkOrigin | null = null,
): void {
  const preferred = preferWindowId === undefined ? undefined : windows.get(preferWindowId);
  // The asking window's scope -- its root AND its server -- or, for a link from outside
  // the app, the scope a command would go to; `links.ts` says which connection it
  // chose when the project is served by more than one (KYO-463.c3).
  const current = scopeOf(preferred?.kind === "shell" ? preferred : undefined);
  deliverLink(storeService.followLink(raw, knownProjects(current), source), preferWindowId, origin);
}

/** The origin a renderer sent with a link, narrowed; anything else is no origin. */
function asLinkOrigin(value: unknown): LinkOrigin | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.tabId !== "string" || raw.tabId === "") return null;
  return { tabId: raw.tabId, newTab: raw.newTab === true };
}

// A link clicked anywhere else on the Mac, while this app is running -- or the one
// that started it, which macOS also delivers here once `ready` has fired.
app.on("open-url", (event, url) => {
  event.preventDefault();
  followAndDeliver(url, "open-url");
});

// A `kyosei://` URL clicked inside the app -- today, one printed into the terminal
// tab's output and turned into a hyperlink by `Terminal.registerLinkProvider`
// (KYO-232.c7). It reaches `followLink` exactly as the other three sources do, so an
// unknown project, an unknown id shape and a foreign scheme are refused here by the
// same code and with the same sentence, and the renderer's part is a string.
ipcMain.on(CHANNEL.followLink, (event, url: unknown, origin: unknown) => {
  if (typeof url !== "string") return;
  followAndDeliver(
    url,
    "in-app",
    senderWindowId(event as IpcMainInvokeEvent),
    asLinkOrigin(origin),
  );
});

/**
 * The link a launch carried on its own argv, delivered once a window can show it.
 *
 * macOS uses `open-url` for both the cold and the warm case, so this is Windows,
 * Linux, and `electron . kyosei://...` during development. Deferred to the first
 * window's paint rather than run at `ready`: there is nothing to send it to before
 * then, and `deliverLink` picks a window.
 */
function deliverLaunchLink(win: BrowserWindow): void {
  const raw = storeService.urlFromArgv(process.argv);
  if (raw === null) return;
  win.once("ready-to-show", () => {
    setImmediate(() => followAndDeliver(raw, "launch-argument", win.id));
  });
}

// ------------------------------------------------------------- the pty panes

// One shell per WINDOW LINEAGE, not per renderer (KYO-124).
//
// Before this record a pty was keyed by `webContents.id`, and everything wrong with
// the pane followed from that one choice: the shell died when its renderer did, so a
// window closing ended a session; and a popped-out terminal, being a new renderer,
// opened a shell of ITS own, so the pane and the popped window were two shells
// rather than one moved. `terminal.ts` now holds sessions under free-standing ids
// and this file only says which window is looking at which id.
//
// Free-standing ids rather than "the owning shell window" -- the shape KYO-122.r1
// proposed -- because opening, popping out, closing or hiding the pane should not end
// the terminal session where that can be avoided. With the session owned by
// the shell window, closing that window while the popped-out terminal is still open
// would have to either end the shell the reader is still looking at or leave a
// session owned by a window that no longer exists. With an id owned by neither, the
// question does not arise: the popped window holds the id, and the shell keeps
// running as long as anything is looking at it.
//
// What ends a session is therefore exactly one thing: the reader ending it. The
// shell exiting (`exit`, Ctrl-D, or the job being killed) removes it, and quitting
// the app ends them all through `endSessions`. A window closing detaches a view.

/** Which session a live view is showing, so its keystrokes reach the right shell. */
const viewers = new Map<number, { sessionId: string; detach: () => void }>();

/** Renderers that already carry the teardown listener `showSession` installs. */
const torndown = new Set<number>();

let sessionSeq = 0;

/**
 * The session id a window's terminal pane should attach to, allocating one for a
 * shell window that has not asked before.
 *
 * A pane window answers with the id captured when it was popped, which is what keeps
 * it showing the same shell after its owner has gone. A shell window answers with
 * its own, which is what makes popping back -- and reopening the pane -- a
 * reattachment rather than a new shell.
 */
function sessionIdFor(windowId: number): string | null {
  const entry = windows.get(windowId);
  if (entry === undefined) return null;
  if (entry.kind === "pane") return entry.pane === "terminal" ? entry.sessionId : null;
  if (entry.sessionId === null) {
    sessionSeq += 1;
    entry.sessionId = `pty-${sessionSeq}`;
  }
  return entry.sessionId;
}

/**
 * Point one renderer at a session: it gets what the session has already printed,
 * and everything printed from now on.
 *
 * The renderer's own view is all that goes away when its window does. `destroyed`
 * detaches the sink and nothing else -- there is deliberately no path from a window
 * closing to a shell ending, because that path is the defect this record is about.
 */
function showSession(contents: Electron.WebContents, session: PtySession): boolean {
  if (contents.isDestroyed()) return false;
  const already = viewers.get(contents.id);
  // Every `termOpen` comes from a NEW, empty screen -- the renderer builds one per
  // attach -- so a renderer already watching this session is a reopened tab, and it is
  // re-attached so the replay reaches the screen it has now. Returning early here left
  // the reopened tab blank until the shell next printed (KYO-258).
  const reopened = already?.sessionId === session.id;
  already?.detach();
  const detach = attachSink(session, {
    data: (surface, chunk: string) => {
      if (!contents.isDestroyed()) contents.send(CHANNEL.termData, { surface, data: chunk });
    },
    exit: (surface, info: { exitCode: number }) => {
      // One of the pane's two ptys ended. The viewer goes only when the PANE does --
      // when `terminal.ts` has dropped it from the registry -- because an id whose pane
      // has gone can be given to a NEW one later (a tab reopened after `exit` at its
      // prompt), and a stale viewer pointing at that id would send the new pane
      // keystrokes from a screen showing the dead one.
      //
      // NOT "is anything still running behind it" (KYO-293): between a client exiting
      // and the bare shell being shown there is an instant with neither -- every detach
      // and every re-target passes through it -- and evicting the viewer there left a
      // pane that PAINTED (the sink is the pane's, not the viewer map's) and swallowed
      // every keystroke, because `sessionOf` reads the viewer map. Measured as a bare
      // shell that showed its prompt and then ignored the reader entirely.
      if (sessionById(session.id) === undefined) viewers.delete(contents.id);
      if (!contents.isDestroyed()) contents.send(CHANNEL.termExit, { surface, ...info });
    },
    fellBack: (outcome: TerminalAttachOutcome) => {
      if (!contents.isDestroyed()) contents.send(CHANNEL.termFellBack, outcome);
    },
  });
  viewers.set(contents.id, { sessionId: session.id, detach });
  // Once per renderer, not once per attach: a second listener here would be a second
  // teardown for one window, and the detach it holds would be a stale one.
  //
  // Kept in a set of its own rather than inferred from `already === undefined`, which
  // is what it was before KYO-175. That test was true exactly once per renderer only
  // while a viewer entry, once made, was never removed except by the window going
  // away -- and a project switch removes it: `endSessions` delivers each exit, whose
  // handler drops the viewer, and the same renderer then attaches again to the shell
  // started against the new project. Under the old test that second attach looked
  // like a first one and added a second teardown listener per switch.
  if (torndown.has(contents.id)) return reopened;
  torndown.add(contents.id);
  contents.once("destroyed", () => {
    viewers.get(contents.id)?.detach();
    viewers.delete(contents.id);
    torndown.delete(contents.id);
  });
  return reopened;
}

/** KYO-258's repaint, for this session's tmux client; the step itself is `terminal.ts`'s. */
function repaintTmux(session: PtySession, size: TerminalSize): void {
  repaintTmuxClient(session.client, size);
}

/**
 * A window's terminal session moved to another window (KYO-246): stop feeding the
 * old window's renderer. Without this the bytes keep arriving on a channel whose tab
 * is gone, queue in that preload's early buffer, and are drained into the NEXT
 * terminal the window opens -- a fresh shell whose first screen is another
 * session's output.
 */
function releaseViewer(windowId: number): void {
  const win = windows.get(windowId)?.win;
  if (win === undefined || win.isDestroyed()) return;
  const contents = win.webContents;
  viewers.get(contents.id)?.detach();
  viewers.delete(contents.id);
}

/** The session behind one renderer's pane, for the two channels that write to it. */
function sessionOf(contentsId: number): PtySession | undefined {
  const viewer = viewers.get(contentsId);
  return viewer === undefined ? undefined : sessionById(viewer.sessionId);
}

function asSize(value: unknown): TerminalSize {
  const size = (value ?? {}) as { cols?: unknown; rows?: unknown };
  const cols = typeof size.cols === "number" && size.cols > 1 ? Math.floor(size.cols) : 80;
  const rows = typeof size.rows === "number" && size.rows > 0 ? Math.floor(size.rows) : 24;
  return { cols, rows };
}

/**
 * A project's controller session name -- its prefix -- asked once per project.
 *
 * The session `kyo controller start` makes is named after the prefix (KYO-458), so the
 * pane needs nothing else to know which session is this project's. Asked of the kyo
 * serve that serves the project -- this machine's, or the dev box's -- which lists every
 * registered project with its prefix: no `kyo` subprocess, and no SSH connection for a
 * dev box. A success is kept per project (per scope key); a failure is asked again by
 * the next pane, since a kyo serve that was down a moment ago may be up now.
 */
interface PrefixAnswer {
  prefix: string | null;
  problem: string | null;
}

const projectPrefixes = new Map<string, Promise<PrefixAnswer>>();

/** No prefix was asked for, because nothing would have read it. */
const NO_PREFIX: PrefixAnswer = { prefix: null, problem: null };

function projectPrefixOnce(scope: ProjectScope): Promise<PrefixAnswer> {
  let pending = projectPrefixes.get(scope.key);
  if (pending === undefined) {
    pending = (async (): Promise<PrefixAnswer> => {
      const client =
        scope.remote === null
          ? localClient()
          : clientFor({ host: scope.remote.host, port: scope.remote.port });
      const listed = await client.projects();
      if (!listed.ok) return { prefix: null, problem: listed.problem };
      const entry = listed.projects.find((one) => one.root === scope.root);
      if (entry?.prefix != null && entry.prefix !== "")
        return { prefix: entry.prefix, problem: null };
      return {
        prefix: null,
        problem:
          entry === undefined
            ? `the kyo serve that serves it does not list ${scope.root}`
            : `its registry entry names no prefix${entry.problem === null ? "" : ` (${entry.problem})`}`,
      };
    })().catch((error: unknown) => ({ prefix: null, problem: (error as Error).message }));
    projectPrefixes.set(scope.key, pending);
    const asked = pending;
    void asked.then((answer) => {
      if (answer.prefix === null && projectPrefixes.get(scope.key) === asked) {
        projectPrefixes.delete(scope.key);
      }
    });
  }
  return pending;
}

/**
 * How a dev box window's pane reaches the controller session there (KYO-474.c3): the
 * SSH host Start controller uses (`sshHostFor`), and `ssh.ts`'s one argv builder with
 * `-t`. Null when that host is not one ssh could be handed -- the one case in which the
 * tab says "No terminal here" and where to set one.
 */
function sshPaneFor(remote: { host: string; port: number }): SshPane | null {
  const sshHost = sshHostFor(app.getPath("userData"), remote.host, remote.port);
  if (sshHostProblem(sshHost) !== null) return null;
  return {
    host: sshHost,
    attachArgv: (session: string) => controllerAttachArgv(sshCommandHost(sshHost), session),
  };
}

/**
 * A dev box's `kyo controller list --json`, asked at most once per interval per SSH
 * host: the pill row polls every `TERMINAL_POLL_MS`, and over SSH each ask is a
 * connection. The same interval the claude tab's controller row keeps
 * (`REMOTE_STATUS_TTL_MS`); a failure is never kept, and an attach or a client ending
 * forgets the host's answer so the next question is fresh.
 */
const devBoxListings = answerCache<ListingAnswer>((answer) => answer.listing === null);

function devBoxListing(sshHost: string, fresh = false): Promise<ListingAnswer> {
  const host = sshCommandHost(sshHost);
  if (fresh) devBoxListings.put(host.machine, null);
  return devBoxListings.get(host.machine, () => controllerListingOn(host, runProcess));
}

/** A pane's own refusal, in the shape the pane can render (law 6). */
function noShell(problem: string): TerminalOpen {
  return {
    ok: false,
    cwd: null,
    shell: null,
    kyoOnPath: null,
    problem,
    mode: "bare",
    session: null,
    sessionId: null,
    showing: "bare",
    join: null,
    projectSession: null,
    socket: null,
    notice: problem,
    sessions: null,
  };
}

/**
 * Sessions still being started.
 *
 * KYO-115 made this handler asynchronous -- it asks for the project's identity and
 * tmux for the session before there is a pty -- and an async handler keyed by "is
 * there a session yet" would start a second shell for a pane that asked twice before
 * the first answer landed. The in-flight promise is the key instead, and since KYO-124
 * it is keyed by SESSION id: two windows opening the same session at once -- a pop-out
 * raced against its own owner -- must wait on one shell, which is the same defect one
 * step out.
 */
const opening = new Map<string, Promise<PtySession | null>>();

function startSession(
  id: string,
  size: TerminalSize,
  scopeNow: () => ProjectScope,
): Promise<PtySession | null> {
  const running = sessionById(id);
  if (running !== undefined) return Promise.resolve(running);
  const inFlight = opening.get(id);
  if (inFlight !== undefined) return inFlight;
  const started = (async (): Promise<PtySession | null> => {
    // KYO-218: through `askedAboutOneProject`, because the pty's cwd and the session
    // name must be two facts about the SAME project, and the question is long enough
    // for a human to switch project inside it. `terminal.ts` holds the rule.
    //
    // The prefix is asked only when something will use it: a dev box's pane always, a
    // local one only when there is a tmux to attach with.
    const { answer } = await askedAboutOneProject(
      () => scopeNow().key,
      async () => {
        const scope = scopeNow();
        const needed = scope.remote !== null || tmuxAvailable();
        return { scope, prefix: needed ? await projectPrefixOnce(scope) : NO_PREFIX };
      },
    );
    const { scope, prefix } = answer;
    // A shell started after `before-quit` has swept is a process nothing will ever
    // end: the sweep has already run and no later one is coming, and that leak is
    // enough to stop Electron exiting.
    if (quitting) return null;
    const ssh = scope.remote === null ? null : sshPaneFor(scope.remote);
    const session = openSession(id, {
      cwd: scope.root,
      projectSession: prefix.prefix,
      sessionProblem: prefix.problem,
      ssh,
    });
    session.lastSize = size;
    // The client pty exiting when nobody here asked it to is the reader's own
    // prefix-d, the session ending, or (over SSH) the connection or the attach failing,
    // and only the far side can say which (KYO-293.c3, KYO-474.c4). Installed here
    // rather than in `terminal.ts` because that file may not run a tmux command.
    session.onClientGone = (was, ended) => void clientGone(session, was, ended);
    const target = session.setup.plan.session;
    if (ssh !== null) {
      // A dev box's pane: its only surface is the ssh client. When there is nothing to
      // attach to, the pane says why in prose and offers the next act; there is no bare
      // shell here, because the project's directory is on the other machine.
      session.showing = "client";
      if (target === null) {
        session.info = {
          ...paneInfo(session.id, session.cwd, session.setup, "client", null),
          notice:
            `could not read this project's prefix from ${remoteLabel(scope.remote as RemoteConnection)}, ` +
            `so there is no orchestrator session name to attach to: ${prefix.problem ?? "no answer"}`,
        };
        return session;
      }
      const outcome = await attachPane(session, target, size);
      if (outcome.action !== "attach") {
        session.info = {
          ...paneInfo(session.id, session.cwd, session.setup, "client", null),
          notice: outcome.notice,
          offer: outcome.offer ?? null,
        };
      }
      return session;
    }
    // THE PANE OPENS IN THE PROJECT'S CONTROLLER SESSION (KYO-293.c1, KYO-474.c2). One
    // attach, and it is the same one a pill click takes -- `attachPane` -- so "opened the
    // tab" and "clicked the project's pill" cannot drift apart. When there is no session
    // the pane says so and offers Start controller; it creates nothing itself, and the
    // bare shell beneath carries the sentence.
    if (target !== null && session.setup.plan.binary !== null) {
      const outcome = await attachPane(session, target, size);
      if (outcome.action === "attach") return session;
      showBare(session, size);
      session.info = {
        ...session.info,
        notice: `${outcome.notice} ${BARE_PANE_NOTICE}`,
        offer: outcome.offer ?? null,
      };
      return session;
    }
    showBare(session, size);
    return session;
  })();
  opening.set(id, started);
  void started.catch(() => undefined).then(() => opening.delete(id));
  return started;
}

/** Where to set a dev box's SSH host, named in the one refusal that needs it. */
const SSH_HOST_HOME =
  "set its SSH host in the rail's Connect to a dev box… dialog (the SSH host field), " +
  "or in New Project… beside that dev box";

ipcMain.handle(CHANNEL.termOpen, async (event, raw: unknown): Promise<TerminalOpen> => {
  const contents = event.sender;
  const windowId = BrowserWindow.fromWebContents(contents)?.id;
  const id = windowId === undefined ? null : sessionIdFor(windowId);
  if (id === null) return noShell("this window has no terminal pane to put a shell behind");
  const owner = windowId === undefined ? undefined : windows.get(windowId);
  if (owner?.kind === "shell" && owner.remote !== null && sshPaneFor(owner.remote) === null) {
    // KYO-370.c4, narrowed by KYO-474: a dev box's pane is a client over the person's
    // own SSH, and without an SSH host there is nothing to run ssh to. Said where the
    // pane would be, with where to set one, never an empty screen.
    const where = remoteLabel(owner.remote);
    const sshHost = sshHostFor(app.getPath("userData"), owner.remote.host, owner.remote.port);
    return {
      ...noShell(
        `this window is on ${where}'s project, and its terminal attaches to the orchestrator ` +
          `session there over SSH -- but ${JSON.stringify(sshHost)} is not an SSH host ` +
          `(${sshHostProblem(sshHost)}). To reach it, ${SSH_HOST_HOME}.`,
      ),
      remote: { host: owner.remote.host, port: owner.remote.port },
    };
  }
  try {
    const size = asSize(raw);
    const session = await startSession(id, size, () =>
      windowId === undefined ? scopeOf(undefined) : scopeOfWindow(windowId),
    );
    if (session === null) return noShell("the app was quitting before this shell could start");
    // The window can go away while the prefix is still being asked. Unlike before
    // KYO-124 that is no longer a leak -- the session is reachable by its id and swept
    // at quit -- but there is nobody to show it to, so this stops here rather than
    // sending a first screen at a destroyed renderer.
    if (contents.isDestroyed()) return noShell("the window closed before its shell could start");
    if (showSession(contents, session)) repaintTmux(session, size);
    // The pill row travels with the first answer, so the project's pill is highlighted
    // on the FIRST paint rather than one poll later (KYO-293.c1).
    return { ...session.info, sessions: await sessionsView(session, windowId) };
  } catch (error) {
    return noShell(`could not start a shell: ${(error as Error).message}`);
  }
});

ipcMain.on(CHANNEL.termInput, (event, raw: unknown) => {
  const input = raw as Partial<TerminalInput>;
  if (typeof input?.data !== "string") return;
  const session = sessionOf(event.sender.id);
  if (session === undefined) return;
  // Addressed to the screen the reader typed into, never to "the pane": the bare shell
  // and the tmux client are two ptys, and a keystroke sent to the wrong one is the
  // defect this record is about, one level down (KYO-293.c2).
  const screen = screenOf(session, input.surface === "client" ? "client" : "bare");
  if (screen === null || screen.exited) return;
  screen.pty.write(input.data);
});

ipcMain.on(CHANNEL.termResize, (event, raw: unknown) => {
  const session = sessionOf(event.sender.id);
  if (session === undefined) return;
  const resize = raw as Partial<TerminalResize>;
  const size = asSize(raw);
  const surface = resize?.surface === "client" ? "client" : "bare";
  resizeSurface(session, surface, size);
});

/**
 * Tell one of a pane's ptys its screen's size, and remember it.
 *
 * The hidden surface is deliberately NOT resized: it has no layout, so the renderer
 * cannot measure it, and a zero sent to a tmux client resizes every other client of
 * that session down with it. What keeps it from coming back stale is `termShow`, which
 * the renderer sends when a surface goes on screen and which lands here first.
 */
function resizeSurface(session: PtySession, surface: "client" | "bare", size: TerminalSize): void {
  session.lastSize = size;
  const screen = screenOf(session, surface);
  if (screen === null || screen.exited) return;
  screen.size = size;
  try {
    screen.pty.resize(size.cols, size.rows);
  } catch {
    // A pty whose process exited between the resize and here; nothing to resize.
  }
}

/**
 * This surface is the one on screen now (KYO-293).
 *
 * Two things the renderer cannot do for itself. The pty is told the size it was hidden
 * at is stale -- the reader may have resized the window while the other screen was up.
 * And a tmux client is made to REPAINT: tmux draws on SIGWINCH, and a client coming
 * back at the size it left at generates none, which is KYO-258's blank pane with the
 * bare shell's screen now able to cause it on every switch rather than only on reopen.
 */
ipcMain.handle(CHANNEL.termShow, (event, raw: unknown): void => {
  const session = sessionOf(event.sender.id);
  if (session === undefined) return;
  const shown = raw as Partial<TerminalResize>;
  const surface = shown?.surface === "client" ? "client" : "bare";
  const size = asSize(raw);
  session.showing = surface;
  resizeSurface(session, surface, size);
  if (surface === "client") repaintTmux(session, size);
});

// ------------------------------------------------- the session pills (KYO-283)
//
// Three handlers, and between them the whole of what the renderer may ask about tmux:
// what is alive, put me in THAT one, and put me back in my own shell. It cannot name a
// socket, a client or a tmux subcommand -- those are composed here, from facts only
// this process holds, which is the same boundary `docsOpen` is drawn on. A renderer that could name a tmux command could name one that ends a session.
//
// Every one of them reads the pane's state FRESH rather than remembering it: whether
// this pane's client is in a session is a question only the server can answer, and a
// reader who detached with their own prefix key told the server and not this app.
//
// The server is the CONTROLLER's (KYO-474): `controllerSocketPath`, the socket `kyo
// controller` resolves, in the environment it scrubs. A dev box's pane asks its
// `kyo controller list --json` over SSH instead, and its pills are that listing's.

/** The environment a pane's tmux commands run in: its own, scrubbed as the CLI's is. */
function paneEnv(session: PtySession | undefined): Record<string, string | undefined> {
  return controllerTmuxEnv(session === undefined ? ptyEnvironment() : session.setup.env);
}

/**
 * What the pane looks like to tmux right now: the binary, the socket, the client.
 *
 * `attachedTo` is still READ rather than remembered -- `list-clients` names the session
 * each client tty is in, and this pane's tty is its client pty's -- with one addition
 * KYO-293 makes both possible and necessary. The client pty is spawned by this app, and
 * tmux takes a moment to register the client after the process starts; a read taken in
 * that window would say "attached to nothing" about a pane that is attaching, and the
 * pill row drawn from it would flicker unhighlighted on every open (c1 asks for the
 * highlight from the first paint). So a live client pty's own target answers for it
 * until the server has caught up. That is not a memory that can go stale: a reader who
 * detaches with their own prefix key ends the client PROCESS, which ends this pty,
 * which clears `clientSession` in the same breath.
 */
async function paneNow(session: PtySession | undefined): Promise<PaneAttachState> {
  const env = paneEnv(session);
  const binary = session?.setup.plan.binary ?? resolveTmux(env).binary;
  const socket =
    session === undefined
      ? binary === null
        ? null
        : controllerSocketPath(env)
      : session.setup.socket;
  const client = session?.client ?? null;
  const tty = client === null || client.exited ? null : ptyTty(client.pty);
  const fromServer =
    binary === null || socket === null || tty === null
      ? null
      : attachedSession(await attachedClients(binary, socket, env), tty);
  const attached =
    fromServer ?? (client !== null && !client.exited ? (session?.clientSession ?? null) : null);
  return { binary, socket, attachedTo: attached, clientTty: tty };
}

/** The project's own session name for a window, for the pill that is set apart. */
async function projectSessionFor(windowId: number | undefined): Promise<string | null> {
  if (!tmuxAvailable()) return null;
  const answer = await projectPrefixOnce(
    windowId === undefined ? scopeOf(undefined) : scopeOfWindow(windowId),
  );
  return answer.prefix;
}

/** A dev box pane's pill row: its `kyo controller list`, over SSH (KYO-474.c3). */
async function devBoxSessionsView(
  session: PtySession,
  ssh: SshPane,
): Promise<TerminalSessionsView> {
  const attached = session.client !== null && !session.client.exited ? session.clientSession : null;
  const answer = await devBoxListing(ssh.host);
  if (answer.listing === null) {
    return {
      pills: [],
      attached,
      socket: null,
      problem: `could not ask ${ssh.host} which orchestrator sessions it holds: ${answer.problem ?? ""}`,
    };
  }
  const pills = pillsFromListing(answer.listing, session.cwd, session.setup.plan.session).map(
    (pill) => ({ ...pill, current: pill.name === attached, clients: 0 }),
  );
  return { pills, attached, socket: null, problem: null };
}

/** The pill row, composed from the server's answer and this pane's own client. */
async function sessionsView(
  session: PtySession | undefined,
  windowId: number | undefined,
): Promise<TerminalSessionsView> {
  if (session?.setup.ssh != null) return await devBoxSessionsView(session, session.setup.ssh);
  const owner = windowId === undefined ? undefined : windows.get(windowId);
  if (session === undefined && owner?.kind === "shell" && owner.remote !== null) {
    return { pills: [], attached: null, socket: null, problem: null };
  }
  const pane = await paneNow(session);
  if (pane.binary === null || pane.socket === null) {
    return {
      pills: [],
      attached: null,
      socket: null,
      problem: "there is no tmux this app can see, so there is no server to list sessions on.",
    };
  }
  const project = session?.setup.plan.session ?? (await projectSessionFor(windowId));
  const { sessions, problem } = await aliveSessions(pane.binary, pane.socket, paneEnv(session));
  return {
    pills: sessionPills(sessions, project, pane.attachedTo),
    attached: pane.attachedTo,
    socket: pane.socket,
    problem,
  };
}

ipcMain.handle(CHANNEL.termSessions, async (event): Promise<TerminalSessionsView> => {
  const windowId = BrowserWindow.fromWebContents(event.sender)?.id;
  return await sessionsView(sessionOf(event.sender.id), windowId);
});

/** One refusal, in the shape the strip renders. */
function refuseAttach(
  target: string | null,
  notice: string,
  mode: "tmux" | "bare",
  offer: TerminalOffer | null = null,
  surface: "client" | "bare" = "bare",
): TerminalAttachOutcome {
  return { action: "refuse", session: target, surface, notice, mode, offer };
}

/**
 * End this pane's tmux client, and do it as tmux's own act where that is possible.
 *
 * `detach-client -t <tty>` and not `-s <session>`: the second detaches EVERY client of
 * that session, which on the shared server this whole design rests on is the
 * user's own terminal and whatever else has joined. The tty is this pane's client
 * pty's, which this app knows because it spawned the client -- KYO-283 had to hedge
 * about clients it could not name, and KYO-293 removed the hedge by removing the case.
 *
 * `endScreen` follows it and is not a second way of doing the same thing: it is what
 * WAITS for the client process to go, so the next client is spawned into a pane whose
 * previous one has actually exited. It also covers a `detach-client` that failed or a
 * tty that could not be read -- SIGHUP to a tmux client detaches it, which is why
 * quitting the app has always left sessions running (KYO-293.c4). A dev box's client
 * is ssh, which has no tty on this machine's tmux: the hangup alone ends it, and the
 * far end's tmux sees a client go away (KYO-474.c4).
 *
 * `droppingClient` is the difference between this and the reader's own prefix-d. Both
 * end in the same pty exit; only this one was asked for here, and only the other one
 * owes the reader a sentence (`clientGone`).
 */
async function dropClient(session: PtySession): Promise<void> {
  const client = session.client;
  if (client === null || client.exited) return;
  const { binary, socket } =
    session.setup.plan.binary === null
      ? { binary: null, socket: null }
      : { binary: session.setup.plan.binary, socket: session.setup.socket };
  session.droppingClient = true;
  try {
    const tty = ptyTty(client.pty);
    if (binary !== null && socket !== null && tty !== null) {
      await detachClient(binary, socket, paneEnv(session), tty);
    }
    await endScreen(client);
  } finally {
    session.droppingClient = false;
  }
}

/**
 * Put this pane in a named session on the controller's server (KYO-293.c1, KYO-474.c2).
 *
 * ONE code path, and both callers matter: the tab opening and a pill click run exactly
 * this, so "the pane opens in the project's session" and "clicking the project's pill"
 * cannot come to mean two different things.
 *
 * It creates nothing. The session is the controller's, made by `kyo controller start`;
 * when it is not there the pane says so and offers Start controller -- the claude tab's
 * KYO-460 flow -- rather than making a session of its own, and it never touches the
 * default tmux server, whose sessions are left exactly as they are. Nor does it prepare
 * the session's environment any more: the app views the controller's session, and
 * stating its own `KYOSEI_*` there would be writing into somebody else's (KYO-265's
 * `set-environment` calls were for sessions this app created).
 *
 * Then the client: a pty whose process IS tmux -- or, for a dev box, ssh running `kyo
 * controller attach` there. Nothing is written into any shell, so a bare shell busy in
 * a foreground command is not consulted and not disturbed (KYO-293.c2).
 */
async function attachPane(
  session: PtySession,
  target: string,
  size: TerminalSize,
): Promise<TerminalAttachOutcome> {
  if (session.setup.ssh !== null)
    return await attachOverSsh(session, session.setup.ssh, target, size);
  const pane = await paneNow(session);
  const plan = planPaneAttach(pane, target);
  if (plan.action !== "attach") {
    return {
      action: plan.action,
      session: target,
      surface: plan.surface,
      notice: plan.reason,
      mode: pane.attachedTo === null ? "bare" : "tmux",
      offer: null,
    };
  }
  const binary = pane.binary as string;
  const socket = pane.socket as string;
  const modeNow = pane.attachedTo === null ? "bare" : "tmux";
  const where = session.setup.spelled;
  const { sessions: alive, problem } = await aliveSessions(binary, socket, paneEnv(session));
  if (!alive.some((one) => one.name === target)) {
    if (problem !== null) {
      return refuseAttach(
        target,
        `could not ask ${where} which sessions it holds, so this pane is not attached.`,
        modeNow,
        { action: "reconnect", session: target, said: problem },
      );
    }
    // Any name but the project's is one the server reported a moment ago and has
    // since lost; there is nothing to offer for it (law 6).
    if (target !== session.setup.plan.session) {
      return refuseAttach(
        target,
        `${target} is no longer running on ${where}, so there is nothing to attach to.`,
        modeNow,
      );
    }
    return refuseAttach(
      target,
      `no orchestrator session ${target} is running on ${where} on this machine` +
        (alive.length === 0 ? " (that server holds no sessions, or is not running)" : "") +
        ". This app makes no session of its own: Start orchestrator runs `kyo controller " +
        "start` for this project, and this pane attaches to it.",
      modeNow,
      { action: "start", session: target, said: null },
    );
  }
  await dropClient(session);
  startClient(session, binary, socket, target, size);
  return {
    action: "attach",
    session: target,
    surface: "client",
    // What the act did, and then what the pane now IS. The second sentence is
    // `noticeFor`'s tmux line: that this app never ends the session, and the line to
    // type in any other terminal to join it.
    notice: `${plan.reason} ${noticeFor("tmux", target, null)}`,
    mode: "tmux",
    offer: null,
  };
}

/**
 * The same, for a dev box (KYO-474.c3): ask its `kyo controller list` over SSH whether
 * the session is there, then start ssh running `kyo controller attach` in the pane's
 * client pty. A listing ssh could not get is said in ssh's own words, with Reconnect;
 * a project with no session there is said with Start controller, which runs `kyo
 * controller start` there over the same SSH (the claude tab's KYO-460 flow).
 */
async function attachOverSsh(
  session: PtySession,
  ssh: SshPane,
  target: string,
  size: TerminalSize,
): Promise<TerminalAttachOutcome> {
  const live = session.client !== null && !session.client.exited;
  if (live && session.clientSession === target) {
    return {
      action: "already",
      session: target,
      surface: "client",
      notice: `this pane is already in ${target} on ${ssh.host}.`,
      mode: "tmux",
      offer: null,
    };
  }
  const answer = await devBoxListing(ssh.host);
  const modeNow = live ? "tmux" : "bare";
  if (answer.listing === null) {
    return refuseAttach(
      target,
      `could not reach ${ssh.host} to ask which orchestrator sessions it holds (\`kyo controller ` +
        "list` over ssh), so this pane is not attached. What ssh said is below.",
      modeNow,
      { action: "reconnect", session: target, said: answer.problem },
      "client",
    );
  }
  if (!listedLive(answer.listing, target)) {
    if (target !== session.setup.plan.session) {
      return refuseAttach(
        target,
        `${target} is no longer running on ${ssh.host}, so there is nothing to attach to.`,
        modeNow,
        null,
        "client",
      );
    }
    return refuseAttach(
      target,
      `no orchestrator session ${target} is running on ${ssh.host}. Start orchestrator runs ` +
        `\`kyo controller start\` there over ssh, and this pane attaches to it.`,
      modeNow,
      { action: "start", session: target, said: null },
      "client",
    );
  }
  const from = live ? session.clientSession : null;
  await dropClient(session);
  startSshClient(session, target, size);
  const join = controllerJoinOverSsh(ssh.host, target);
  return {
    action: "attach",
    session: target,
    surface: "client",
    notice:
      (from === null
        ? `attaching this pane to ${target} on ${ssh.host}: \`${join}\`. `
        : `moving this pane from ${from} to ${target} on ${ssh.host}; ${from} keeps running. `) +
      noticeFor("tmux", target, null),
    mode: "tmux",
    offer: null,
  };
}

/**
 * The pane's client went away and this app did not ask it to (KYO-293.c3, KYO-474.c4).
 *
 * On this machine, two things it can be, and they need different sentences: the reader
 * detached with their own prefix key (the session is still there, one click goes back
 * to it) or the session ended under them. `list-sessions` is the one question that
 * separates them, asked AFTER the client has gone so the answer is about the session.
 */
async function clientGone(
  session: PtySession,
  was: string,
  ended: { exitCode: number; output: string },
): Promise<void> {
  if (session.setup.ssh !== null) {
    await devBoxClientGone(session, session.setup.ssh, was, ended);
    return;
  }
  const { binary } = session.setup.plan;
  const socket = session.setup.socket;
  let alive = false;
  if (binary !== null && socket !== null) {
    const { sessions } = await aliveSessions(binary, socket, paneEnv(session));
    alive = sessions.some((one) => one.name === was);
  }
  if (session.closing) return;
  showBare(session, session.lastSize);
  const outcome: TerminalAttachOutcome = {
    action: "bare",
    session: was,
    surface: "bare",
    notice: clientGoneNotice(was, alive),
    mode: "bare",
    offer: alive ? null : { action: "start", session: was, said: null },
  };
  session.info = { ...session.info, notice: outcome.notice, offer: outcome.offer ?? null };
  for (const sink of session.sinks) sink.fellBack(outcome);
}

/**
 * A dev box pane's ssh client ended unasked: a dropped connection, an attach the far
 * end refused (no session, no server, a `kyo` that predates `attach`), or the reader's
 * own detach. The dev box's listing is asked afresh to tell them apart, and what the
 * client last printed -- ssh's or kyo's own stderr -- is repeated verbatim (c4).
 */
async function devBoxClientGone(
  session: PtySession,
  ssh: SshPane,
  was: string,
  ended: { exitCode: number; output: string },
): Promise<void> {
  const answer = await devBoxListing(ssh.host, true);
  if (session.closing) return;
  const said = saidByClient(ended.output);
  const join = controllerJoinOverSsh(ssh.host, was);
  const status = `\`${join}\` exited with status ${ended.exitCode}`;
  let notice: string;
  let offer: TerminalOffer | null;
  if (answer.listing === null) {
    notice =
      `the connection to ${ssh.host} ended (${status}), and ${ssh.host} cannot be reached ` +
      `now to ask whether ${was} is still running. If it is, it keeps running: nothing ` +
      "here ends a session.";
    offer = {
      action: "reconnect",
      session: was,
      said: [said, answer.problem ?? ""].filter((part) => part !== "").join("\n") || null,
    };
  } else if (!listedLive(answer.listing, was)) {
    notice =
      ended.exitCode === 0
        ? `${was} ended on ${ssh.host}, so this pane's connection ended with it.`
        : `no orchestrator session ${was} is running on ${ssh.host} (${status}).`;
    offer =
      was === session.setup.plan.session
        ? { action: "start", session: was, said: said === "" ? null : said }
        : null;
  } else if (ended.exitCode === 0) {
    notice =
      `this pane detached from ${was} on ${ssh.host}; ${was} and everything in it keep ` +
      "running there.";
    offer = { action: "reconnect", session: was, said: null };
  } else {
    notice =
      `the connection to ${was} on ${ssh.host} ended (${status}). ${was} keeps running ` +
      "there -- a connection that ends only detaches this view.";
    offer = { action: "reconnect", session: was, said: said === "" ? null : said };
  }
  session.showing = "client";
  session.info = {
    ...paneInfo(session.id, session.cwd, session.setup, "client", null),
    notice,
    offer,
  };
  const outcome: TerminalAttachOutcome = {
    action: "ended",
    session: was,
    surface: "client",
    notice,
    mode: "bare",
    offer,
  };
  for (const sink of session.sinks) sink.fellBack(outcome);
}

ipcMain.handle(CHANNEL.termAttach, async (event, raw: unknown): Promise<TerminalAttachOutcome> => {
  const asked = raw as { session?: unknown; cols?: unknown; rows?: unknown };
  const target = typeof asked?.session === "string" ? asked.session : "";
  const session = sessionOf(event.sender.id);
  if (session === undefined) {
    return refuseAttach(
      target === "" ? null : target,
      "this window's terminal pane has no pty behind it. Reopen the terminal tab.",
      "bare",
    );
  }
  if (target === "") return refuseAttach(null, "no session was named.", session.info.mode);
  // A Reconnect or a Start that follows a refusal asks afresh: the answer that refused
  // may be the one held (KYO-474.c4).
  if (session.setup.ssh !== null)
    devBoxListings.put(sshCommandHost(session.setup.ssh.host).machine, null);
  const outcome = await attachPane(session, target, asSize(raw));
  if (outcome.action === "attach") return outcome;
  session.info = { ...session.info, notice: outcome.notice, offer: outcome.offer ?? null };
  return outcome;
});

/**
 * The leftmost control: show the bare login shell, and detach this pane's client.
 *
 * KYO-293.c3. Two acts and they are independent, which is the change from KYO-283 --
 * there the bare shell WAS the pane's only pty and "detach" was the whole of it, so a
 * detach that could not be performed left the reader with nothing. Here the shell is a
 * pty of its own: it is shown either way, spawned the first time and the same shell
 * with its screen and scrollback every later time.
 *
 * A dev box's pane has no bare shell (its project is on the other machine), so there
 * the control only detaches: ssh is hung up, the far end's tmux sees a client go, and
 * the pane offers Reconnect (KYO-474.c4).
 */
ipcMain.handle(CHANNEL.termDetach, async (event, raw: unknown): Promise<TerminalAttachOutcome> => {
  const session = sessionOf(event.sender.id);
  if (session === undefined) {
    return refuseAttach(null, "this window's terminal pane has no pty behind it.", "bare");
  }
  const size = asSize(raw);
  const ssh = session.setup.ssh;
  if (ssh !== null) {
    const was = session.client !== null && !session.client.exited ? session.clientSession : null;
    await dropClient(session);
    const target = was ?? session.setup.plan.session;
    const notice =
      was === null
        ? "this pane is not attached to anything."
        : `this pane detached from ${was} on ${ssh.host}; ${was} and everything in it keep running there.`;
    const offer: TerminalOffer | null =
      target === null ? null : { action: "reconnect", session: target, said: null };
    session.info = {
      ...paneInfo(session.id, session.cwd, session.setup, "client", null),
      notice,
      offer,
    };
    return { action: "ended", session: was, surface: "client", notice, mode: "bare", offer };
  }
  const pane = await paneNow(session);
  const plan = planPaneBare(pane);
  if (plan.action === "already") {
    showBare(session, size);
    return { action: "already", session: null, surface: "bare", notice: plan.reason, mode: "bare" };
  }
  const left = pane.attachedTo;
  await dropClient(session);
  showBare(session, size);
  return { action: "bare", session: left, surface: "bare", notice: plan.reason, mode: "bare" };
});

/**
 * Set once the app is going away, so a shell still being started is not started.
 *
 * `before-quit` ends the sessions that exist; this is what keeps one from appearing
 * after that, which no later sweep would run for.
 */
let quitting = false;

/** Set once every session's exit has been delivered, so the quit may complete. */
let sessionsEnded = false;

// --------------------------------------------- quitting while a window is starting
//
// KYO-119. Electron 44.0.0 segfaults when it is asked to quit while one of its
// windows has not finished rendering. `app.quit()` reaches `-[NSWindow
// performClose:]`, which macOS implements as a click on the window's close button,
// and the Electron frames under `-[NSWindow __close]` then dereference 0x228. Every
// crash report standing in ~/Library/Logs/DiagnosticReports when this was filed
// carried that one signature, each of them 0.22s-0.38s after its own process
// launched (KYO-118, and KYO-118.r1 for the mechanism) -- which is the width of this
// app's own startup. So a user who hits Cmd-Q in the app's first quarter-second
// crashes it, and by KYO-116 that genuine crash is exactly what arms macOS's
// "unexpectedly quit while reopening windows" modal for their next launch.
//
// A quit that arrives then is therefore HELD rather than performed: the app
// remembers it, lets the window finish, and then quits by the ordinary path once
// nothing is mid-render. That path is the one KYO-118 measured as safe -- 0 crashes
// in 40 quits of a rendered app against 3 in 40 of a starting one -- so this moves
// the user's quit onto the measured-good path rather than inventing a new one.
//
// The barrier covers "no window yet" and not only "window not yet painted", because
// the earliest moment is the former: at the instant Playwright's `_electron.launch()`
// resolves, `app.isReady()` is already true while `BrowserWindow.getAllWindows()` is
// still empty. A quit taken there would run to completion *into* `whenReady`'s
// window creation, which is the same collision from the other side.
//
// Three things it deliberately is not:
//
//   It is not a wait for the app to be "loaded". The barrier is `ready-to-show`, the
//   window's own first paint -- not the store, the seams, or the renderer's
//   `data-ready`. A quit must never be hostage to a slow seam or a missing project.
//
//   It is not unbounded. A window that never paints at all -- a broken install whose
//   renderer never loads -- would otherwise leave the app impossible to quit, which
//   is a worse failure than the crash being avoided. After `BARRIER_MS` the barrier
//   gives up; `liftBarrier` says what it then does with the window it gave up on.
//
//   It is not a change to quitting after startup. With every window painted,
//   `startupPending()` is false and `before-quit` runs the same two statements it
//   ran before this existed. The only quits that observe any of this are the ones
//   that would otherwise land in the crash.

/** How long a quit may be held for a window that has not painted. */
const BARRIER_MS = 3_000;

/** Windows that exist but have not yet painted; quitting through one segfaults. */
const unrendered = new Set<BrowserWindow>();

/** False until `whenReady` has made the first window: the earliest unsafe moment. */
let firstWindowCreated = false;

/** A quit that arrived while something was still starting, kept for later. */
let quitHeld = false;

/** Set when the bounded wait expires: from then on a quit is never held again. */
let barrierLifted = false;

let barrierTimer: ReturnType<typeof setTimeout> | null = null;

function startupPending(): boolean {
  if (barrierLifted) return false;
  return !firstWindowCreated || unrendered.size > 0;
}

/** Perform a held quit, once nothing is mid-render. */
function releaseHeldQuit(): void {
  if (!quitHeld || startupPending()) return;
  quitHeld = false;
  // The bounded wait was for this quit. Cancelling it matters even though the app
  // is on its way out: `liftBarrier` sets `barrierLifted` for good, so a timer left
  // running would disarm the barrier for every window opened after it fired.
  if (barrierTimer !== null) {
    clearTimeout(barrierTimer);
    barrierTimer = null;
  }
  app.quit();
}

/**
 * The bounded wait expired: quit anyway, and do it without touching the crash.
 *
 * A window that has not painted in `BARRIER_MS` is not going to -- a renderer that
 * never loaded, on a broken install -- and holding the quit any longer would leave
 * the app impossible to quit, which is a worse failure than the one being avoided.
 * Such a window is taken out of the way with `destroy()`, which closes it outright
 * rather than through the `-[NSWindow performClose:]` path the segfault lives on:
 * measured at 12 clean exits in 12, against 26 segfaults in 40 for a quit dragged
 * through a window in that state. Each destroyed window then leaves `unrendered` by
 * its own `closed` event, and the held quit is performed by the ordinary release.
 */
function liftBarrier(): void {
  barrierTimer = null;
  for (const win of [...unrendered]) {
    if (win.isDestroyed()) unrendered.delete(win);
    else win.destroy();
  }
  barrierLifted = true;
  releaseHeldQuit();
}

app.on("browser-window-created", (_event, win) => {
  firstWindowCreated = true;
  unrendered.add(win);
  // A window leaves the set by painting or by going away; either way it is no
  // longer something a quit could be dragged through. The release is deferred by
  // one turn so that the other `ready-to-show` listener -- `revealWindow` above --
  // has run first: the window finishes appearing, and only then does the app quit.
  const settled = (): void => {
    if (!unrendered.delete(win)) return;
    setImmediate(releaseHeldQuit);
  };
  win.once("ready-to-show", settled);
  win.once("closed", settled);
});

// ------------------------------------------- quitting with a shell still running
//
// KYO-124, and the second reason this app holds a quit rather than performing it.
//
// `pty.kill()` does not end a shell, it asks the kernel to; node-pty reaps the child
// on a thread of its own and calls back into JavaScript through a napi threadsafe
// function. Before this record `before-quit` killed each pty and returned, which
// left that call to be drained by `node::FreeEnvironment`'s final `uv_run` -- where
// the environment it needs is being destroyed, napi's failure path throws a JS
// exception, no JS scope exists to receive it, and the C++ exception escapes to
// `std::terminate`. The kill was part of the race rather than a cure for it, and
// two ptys made it likelier because there were two threadsafe functions to drain.
//
// So the quit waits for the exits instead. `endSessions` signals each shell, waits
// for node-pty to deliver its exit HERE, and only then lets the app finish quitting;
// nothing is caught and nothing is swallowed, because the throw being avoided
// happens in C++ during teardown where no `try` in this process could reach it --
// and a swallowed one would be a terminal that silently stopped being a terminal.
//
// Bounded, like the startup barrier above and for the same reason: a shell that
// ignores SIGHUP (`trap "" HUP` is a thing a reader can do to their own pane) would
// otherwise leave the app impossible to quit. `terminal.ts` escalates to SIGKILL
// after the hangup grace and gives up after the kill grace.

/** How long the whole sweep may hold a quit, however many sessions are in play. */
const SESSIONS_END_MS = 4_000;

app.on("before-quit", (event) => {
  if (startupPending()) {
    event.preventDefault();
    if (!quitHeld) {
      quitHeld = true;
      barrierTimer ??= setTimeout(liftBarrier, BARRIER_MS);
    }
    return;
  }
  if (sessionsEnded) return;
  event.preventDefault();
  if (quitting) return;
  quitting = true;
  void Promise.race([
    endSessions(),
    new Promise<void>((resolve) => setTimeout(resolve, SESSIONS_END_MS)),
  ]).then(() => {
    sessionsEnded = true;
    app.quit();
  });
});

// ------------------------------------ the login shell's environment, once (KYO-229)
//
// "At startup" and "before the first window" are different instants, and this app
// wants the first without the second. `captureLoginEnvironment` runs a real
// `$SHELL -ilc`, which on a machine with a heavy `.zshrc` is a tenth of a second and
// on a bad one is the whole bounded timeout -- and it is SYNCHRONOUS, deliberately
// (see `shellenv.ts`: a timeout that kills the child is worth more here than a
// promise that resolves into an app which has moved on). Blocking the main process
// for that long while a window is mid-render is exactly the state KYO-119's segfault
// lives in, and it would hold the startup barrier open for no reason.
//
// So it is kicked off once the first window has painted, on the next turn of the loop:
// early enough that the first pane never waits for it, late enough that nothing is
// starting up. Everything downstream still calls `loginEnvironment()` -- a pane that
// somehow gets there first pays for the capture itself rather than finding it missing,
// and one that gets there after a SUCCESSFUL capture pays nothing. KYO-266: what this
// paragraph used to call idempotent is now idempotent on a success only. A capture
// that failed is re-asked by the next caller, so this warm-up is the first ask rather
// than the only one, and its stdout line reports the first ask alone -- the log the
// line names has the rest.
function captureEnvironmentAfterFirstPaint(win: BrowserWindow): void {
  win.once("ready-to-show", () => {
    setImmediate(() => {
      const environment = loginEnvironment();
      // Written to the app's own stdout rather than kept private: when a pane says it
      // cannot find tmux, the next question is always "what PATH did you search", and
      // this is the answer. It names no variable's VALUE -- only the shell, the
      // outcome and the cost -- so a screen-shared log carries no secret.
      //
      // KYO-266: and stdout is not enough. A Finder launch has none, which is exactly
      // the launch whose PATH is worth asking about, so the same four facts are
      // appended to `shell-env.log` under `userData` by `logShellEnvironmentIn`
      // above -- named here so a reader of this line knows where the others are.
      console.log(
        `kyosei: login shell environment ${environment.outcome} from ${environment.shell} ` +
          `in ${environment.tookMs}ms (also ${shellEnvLogPath(app.getPath("userData"))})`,
      );
    });
  });
}

app.whenReady().then(async () => {
  // KYO-230: a development `electron .` has no bundle of its own to carry
  // `CFBundleURLTypes`, so it claims the scheme at run time instead. Harmless in a
  // packaged build, which already has it from the plist, and the packed half is what
  // `build.mjs --pack` writes and `verifyBundle` reads back.
  app.setAsDefaultProtocolClient(storeService.urlScheme);
  // KYO-266.c4: where the login-shell capture's outcome is kept. Said before any
  // window exists, so a pane that asks before the paint-time capture still has its
  // ask recorded -- `userData` is only meaningful after `ready`, which is here.
  logShellEnvironmentIn(app.getPath("userData"));
  // KYO-450: an earlier build's `kyo` links and shims point at a runtime this build
  // does not ship; remove the ones that are this app's own, and say so once.
  removeEarlierBuildsCli();
  // KYO-232.c3: a monitor plugged in or unplugged is a different desk, and each desk
  // restores its own layout. Subscribed once, here, because `screen` is only usable
  // after `ready` -- and all three events are taken, because a display whose
  // resolution changed is as much a different configuration as one that went away.
  screen.on("display-added", announceDisplays);
  screen.on("display-removed", announceDisplays);
  screen.on("display-metrics-changed", announceDisplays);
  installApplicationMenu();
  // KYO-459: every project is read through a kyo serve; this machine's is asked for its
  // projects once at launch, for the labels the switcher draws. The first window does
  // not wait for it, and a server that is not running is the window's to say.
  void refreshLocalServed();
  console.log(
    `kyosei: this machine's projects are read through kyo serve at ${localClient().label}`,
  );
  // KYO-465.c1: every window the last session showed, each through the kyo serve it was
  // read through; with nothing remembered, one window on the default.
  const reopened = storeService.windowsAtLaunch(rememberedState(), process.env.KYOSEI_PROJECT_ROOT);
  const opened = reopened.map((project) =>
    createShellWindow({ projectRoot: project.root, remote: project.connection }),
  );
  const first = opened[0] ?? createShellWindow();
  captureEnvironmentAfterFirstPaint(first);
  deliverLaunchLink(first);
  app.on("activate", () => {
    if (shellWindows().length === 0) createShellWindow();
  });
});

app.on("window-all-closed", () => {
  // The smoke test drives this app headfully and closes every window; keeping the
  // process alive on macOS would hang it, so quit everywhere.
  app.quit();
});
