// The contract between the Electron main process and every renderer.
//
// KYO-110 carried only window state across this boundary. KYO-111 adds the three
// live seams: the facts the app reads out of the store's database, the currency
// direction it asks `kyo` for, and the pty behind the terminal pane. Every one of
// them is main-process work -- the renderer is sandboxed and has no filesystem,
// no subprocess and no database of its own, so the shape below is the whole of
// what it can learn about the world.

// -------------------------------------------- how windows appear at all (KYO-122)
//
// Not a renderer contract like the rest of this file: a launch-time switch, parsed
// in the main process, and it lives here because the checks have to name the same
// three words the app parses. A copy of the list in `tests/fixture.ts` would be a
// second registry of what the switch may say.
//
// The problem it answers is the suite, not the app. `playwright test` launches this
// app dozens of times, each launch showing a real OS window that takes keyboard
// focus, and while it runs the operator's own machine is unusable -- they cannot
// read the terminal the suite is printing into. Playwright's Electron support
// attaches to the renderer over CDP rather than to anything on screen, so none of
// that showing is needed for the checks to drive the app.

/**
 * The three ways a window may arrive on screen.
 *
 * `focus` is what a human wants and is the default when nothing says otherwise: the
 * window appears and takes focus, which is what `BrowserWindow.show()` does.
 *
 * `inactive` shows the window without activating the app (`showInactive()`). The
 * window is on screen and compositing -- so anything that needs a frame, an
 * animation or a real layout still gets one -- but the operator's keyboard stays
 * where they left it.
 *
 * `hidden` never shows the window at all, and it is what the checks use. Viable
 * because CDP does not need a visible window, and it is the only one of the three
 * that keeps the suite off the operator's screen entirely: `showInactive` still
 * orders a window front on macOS and still paints across a full-screen app in its
 * own Space, interrupting whatever the person at the machine is doing.
 *
 * The cost it would otherwise carry is throttling -- Chromium treats a never-shown
 * window as occluded and slows its timers, its frames and its renderer process --
 * and `prepareApp` in `main/main.ts` turns each of those off by name rather than
 * routing around them. Measured under those switches, a never-shown window reports
 * `document.visibilityState === "visible"` and delivers animation frames at full
 * rate: there is no observable difference for page code at all, which is a stronger
 * result than "nothing here happens to read it".
 */
export const WINDOW_SHOWS = ["focus", "inactive", "hidden"] as const;
export type WindowShow = (typeof WINDOW_SHOWS)[number];

/** The switch, spelled once. Passed as `--kyosei-window-show=<one of the above>`. */
export const WINDOW_SHOW_SWITCH = "--kyosei-window-show";

export function isWindowShow(value: unknown): value is WindowShow {
  return typeof value === "string" && (WINDOW_SHOWS as readonly string[]).includes(value);
}

/**
 * Read the switch out of a process's argv.
 *
 * Deliberately total: an unknown or malformed value falls back to `focus` rather
 * than refusing to start. A typo in a launch argument should cost a noisy window,
 * which the operator will notice immediately, and never a window that never appears
 * -- which looks exactly like a broken install.
 *
 * The last occurrence wins, so a caller can append an override after a shared list
 * of arguments instead of having to rebuild the list.
 */
export function parseWindowShow(argv: readonly string[]): WindowShow {
  let found: WindowShow = "focus";
  for (const arg of argv) {
    if (!arg.startsWith(`${WINDOW_SHOW_SWITCH}=`)) continue;
    const value = arg.slice(WINDOW_SHOW_SWITCH.length + 1);
    if (isWindowShow(value)) found = value;
  }
  return found;
}

/**
 * The panes that carry a pop-out affordance.
 *
 * KYO-232 replaced v2's `editor` with `tab`, and the rename is the whole v3 change of
 * meaning: pop-out is no longer "detach this named pane" but "move THIS TAB to its own
 * window" (KYO-227). What is still a pane kind, and must be, is the scarce
 * main-process object behind a tab -- a pty for `terminal`, a `WebContentsView` for
 * `claude` -- because `main.ts` keys the session hand-off and the redock off it. Every
 * other tab kind is `tab`, drawn entirely in the renderer, and several may be popped
 * out at once: what `main.ts` refuses a second window for is the same TAB, not the
 * same pane kind (`poppedOutOf`).
 */
export const POPOUT_PANES = ["tab", "terminal", "claude"] as const;
export type PopoutPane = (typeof POPOUT_PANES)[number];

/**
 * The one test for "is this a pane that pops out", derived from the union itself.
 *
 * There were TWO before KYO-186, both hand-written and both hardcoding
 * `"editor"`/`"terminal"`: one gating the IPC handler in `main.ts`, one gating the
 * click in `renderer.ts`. Adding a pane to the union therefore did nothing until
 * both were found -- and the renderer's is the dangerous half, because a click it
 * refuses produces no error anywhere. The button was in the DOM, the handler ran,
 * the guard said no, and nothing happened or was logged. Measured here rather than
 * reasoned about: the pop-out check failed with no new window and no message.
 *
 * So it is derived from `POPOUT_PANES` and there is exactly one of it. A pane added
 * to that array is poppable in both processes by construction.
 */
export function isPopoutPane(value: unknown): value is PopoutPane {
  return typeof value === "string" && (POPOUT_PANES as readonly string[]).includes(value);
}

// ----------------------------------------------------- the claude.ai pane (KYO-186)

/** Every way `resolvePaneTarget` can decide where the pane goes. `main/sessions.ts`. */
export const CLAUDE_RESOLUTIONS = [
  "this-project-session",
  "running-session",
  "ended-session",
  "no-store",
  "not-written",
  "file-unusable",
  "no-linked-session",
] as const;
export type ClaudeResolution = (typeof CLAUDE_RESOLUTIONS)[number];

/**
 * Where the claude.ai pane is pointed, and why.
 *
 * `why` is carried on every state, deep-linked or not, for the reason law 6 gives
 * and KYO-128 restates: a verdict that does not name its subject cannot be acted
 * on. A user whose pane opened on the account index instead of their session
 * needs to know whether the file was missing, unreadable, or simply named no
 * running session -- three different problems with three different remedies.
 */
export interface ClaudePane {
  url: string;
  outcome: ClaudeResolution;
  why: string;
  storeRoot: string | null;
  deepLinked: boolean;
  /** Null unless the view itself refused to load -- a network error, say. */
  problem: string | null;
}

/** Where the renderer wants the view drawn, in CSS pixels of its own window. */
export interface PaneBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

// ------------------------------------------ the controller launcher (KYO-383)

/**
 * How often the claude tab asks whether the controller is still running, while it is
 * showing. KYO-383.c4's "within one poll" is this interval: a controller that ends
 * with `/exit` takes its tmux session with it, and the tab says so on the next ask.
 */
export const CONTROLLER_POLL_MS = 2_000;

/**
 * Whether this project's controller session is running, on the machine that holds
 * the project, and what Start would run there (`main/controller.ts`).
 *
 * `running` is `kyo controller list`'s answer (KYO-460) -- the session exists on its tmux
 * server and claude is alive in it -- or, on this machine, a live session the project's
 * hook recorded outside it (KYO-390), whose entry is then `recorded`. `command` names
 * where the command is set (`controller_command`, the CLI's) and `program` is null: the
 * app no longer builds the command line itself.
 */
export interface ControllerStatus {
  /** The project root the session runs in, on `machine`. */
  root: string;
  /** The host name of the machine that answered: where the session is or would be. */
  machine: string;
  /** The tmux session's name -- the project's prefix -- or null when it has none. */
  session: string | null;
  running: boolean;
  /** The program's pid, when it is running. */
  pid: number | null;
  /** The session's own directory, as tmux reports it, when there is a session. */
  cwd: string | null;
  /** What tmux says the session was started with, when there is a session. */
  startedWith: string | null;
  /** The configured command, read on `machine` from its own preference. */
  command: string;
  /** True when no preference is set and `command` is the default. */
  commandIsDefault: boolean;
  /** `command` with the session's name appended: exactly what Start runs. */
  program: string | null;
  /** What `sessions.json` says about a session in `root`, or null when it names none. */
  recorded: { sessionId: string; state: string } | null;
  /** One sentence: what is true, and on which machine. */
  why: string;
  /** Why Start cannot run here, or why the last start did not stay up; else null. */
  problem: string | null;
  canStart: boolean;
  /**
   * The folder Claude Code's workspace-trust prompt is asking about, while that prompt
   * is on screen in the running session (`kyo controller` reports `waiting-for-trust`,
   * KYO-567); null otherwise. The answer is the person's, in the session's terminal --
   * the app never answers it (KYO-568). See `controller-trust.ts`.
   */
  trustFolder: string | null;
}

/** The status, and whether THIS window may change the command (it is on that machine). */
export interface ControllerState {
  status: ControllerStatus;
  editable: boolean;
}

/** What changing the command did. The state is re-read either way. */
export interface ControllerCommandOutcome {
  ok: boolean;
  problem: string | null;
  state: ControllerState;
}

/** What a renderer is: the constant shell, or a single popped-out pane. */
export type ViewKind = "shell" | "pane";

/**
 * What was open in a pane at the moment it was popped out (KYO-122.c6).
 *
 * Before this, a record open in the Editor and popped out arrived as "Nothing open":
 * the popped window built a fresh mode object with a fresh empty selection, so it
 * was not showing the wrong thing -- it had never been told there was a thing.
 *
 * Carried on the pop-out request and handed back in the pane window's own state, so
 * a pane opens on what the reader was looking at. Every field is optional and every
 * one is a plain string: this is a starting point for the new window, not a live
 * mirror of the old one. Two panes on one record then diverge as the reader drives
 * them, which is what two windows are for.
 */
export interface PopoutContext {
  /**
   * The tab that was moved into this window: its kind and its argument (KYO-232).
   *
   * The whole of what a popped-out window needs, and deliberately no more. A tab is
   * identified by `{kind, arg}` everywhere in the v3 shell -- in a saved layout, in an
   * `openTab` call, in the singleton rule -- so a window that is handed those two
   * strings opens the SAME tab, built by the same registered kind, with nothing about
   * it changed by having moved (KYO-227). v2 carried three optional fields naming what
   * each mode's editor had open; they are gone with the modes.
   */
  tab?: { kind: string; arg: string };
}

/** Narrow an untrusted payload to a context, dropping anything unrecognised. */
export function asPopoutContext(value: unknown): PopoutContext {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  const raw = value as Record<string, unknown>;
  const out: PopoutContext = {};
  const tab = raw.tab;
  if (tab !== null && typeof tab === "object" && !Array.isArray(tab)) {
    const fields = tab as Record<string, unknown>;
    if (typeof fields.kind === "string" && fields.kind !== "") {
      out.tab = { kind: fields.kind, arg: typeof fields.arg === "string" ? fields.arg : "" };
    }
  }
  return out;
}

/** One open main window, as the window cluster shows it. */
export interface MainWindowInfo {
  id: number;
}

/**
 * The menu commands the main process routes to the focused window (KYO-246).
 *
 * The application menu lives in the main process -- accelerators are its and only
 * its -- and every one of these is an act on the WORKSPACE, which lives in the
 * renderer. So the menu sends a word and the shell does the thing: `new-tab` opens the
 * home page in the last active column, `close-tab` closes the active tab, `toggle-
 * split` adds or removes the second column, `back`/`forward` walk the active tab's
 * history (KYO-247), and `next-tab`/`prev-tab` cycle the active column's strip.
 */
export const SHELL_COMMANDS = [
  "new-tab",
  "close-tab",
  "toggle-split",
  "back",
  "forward",
  "next-tab",
  "prev-tab",
  // The things that left the rail (KYO-248), reachable from the menu as well as the
  // home page: each opens that tab in the active column.
  "open-scratchpad",
  "open-terminal",
  "open-claude",
  "open-activity",
  "open-brief",
  /** New Window: the renderer shows the project chooser (KYO-249). */
  "new-window",
  /** New Project…: the renderer shows the server and directory picker (KYO-460). */
  "new-project",
] as const;
export type ShellCommand = (typeof SHELL_COMMANDS)[number];

export function isShellCommand(value: unknown): value is ShellCommand {
  return typeof value === "string" && (SHELL_COMMANDS as readonly string[]).includes(value);
}

/**
 * A tab torn off its strip (KYO-246, the way Chrome does it).
 *
 * The renderer sees the pointer leave the strip and says so at once; the main process
 * opens a window around the tab under the pointer and moves it with every
 * `tabDragMove` until `tabDragEnd`. `wholeWindow` is the case where the tab was the
 * window's only one: nothing is opened, the window itself follows the pointer, and
 * dropping it on another window's strip empties and closes it.
 */
export interface TabDragStart {
  tab: TabSnapshot;
  screenX: number;
  screenY: number;
  wholeWindow: boolean;
  /** Where on the tab the reader took hold, so the new window puts the tab under the pointer there. */
  grab: { x: number; y: number };
}

export interface TabDragStartOutcome {
  /** True when a window is now following the pointer. */
  started: boolean;
  /** Why not, when not -- a terminal on a window that already has one, say. */
  refusal: string | null;
}

export interface TabDragPoint {
  screenX: number;
  screenY: number;
}

export interface TabDragEndOutcome {
  /** The window whose strip took the tab, or null when it stayed in its own window. */
  attachedTo: number | null;
}

/**
 * The band in which a dragged window snaps to a strip: the strip itself, which is the
 * window's top row since KYO-253, plus a margin below it. Stated once, in screen
 * pixels below the window's top, because the main process cannot see the renderer's
 * layout.
 */
export const STRIP_BAND_PX = 60;

/**
 * A tab handed to this window by the main process (KYO-246): a drop from another
 * window, or the tabs of a popped-out window coming back when it closes.
 *
 * The screen point is where the reader let go, so the receiving shell can put the tab
 * in the column under the pointer; a hand-back carries none and lands in the active
 * column.
 */
export interface TabHandoff {
  tab: TabSnapshot;
  at: { screenX: number; screenY: number } | null;
}

/** Everything a renderer needs to draw itself and the window cluster. */
export interface AppState {
  /** The window this renderer belongs to. */
  self:
    | {
        kind: "shell";
        id: number;
        /**
         * The layout this window opens on, or null to open on what the attached
         * screens remember. A window opened to hold a tab moved out of another
         * (KYO-246) is seeded with that one tab.
         */
        seed: LayoutSnapshot | null;
        /**
         * True for a window whose layout is not written to disk: one opened by a
         * pop-out or a drop, which hands its tabs back to the window it came from
         * when it closes rather than remembering them as the desk's arrangement.
         */
        transient: boolean;
        /** The project this window is bound to (KYO-249): one per window. */
        projectRoot: string;
        /**
         * True while this window is a hidden spare booted for another (KYO-261), waiting
         * to become the window a tab is torn off into; false once a tear-off uses it.
         */
        spare: boolean;
      }
    | { kind: "pane"; id: number; pane: PopoutPane; context: PopoutContext };
  /** Every open main window, in the order they were opened. */
  windows: MainWindowInfo[];
}

// ------------------------------------------------------------------ the seams

/**
 * The two sources a `kyo` invocation can come from (KYO-79 decision 3): this
 * checkout's working tree, or the `kyo` on the login shell's PATH. KYO-450 retired
 * the third, a runtime bundled inside a packed build (KYO-443).
 */
export const KYO_SOURCES = ["working-tree", "login-path"] as const;
export type KyoSourceName = (typeof KYO_SOURCES)[number];

/** One candidate source, as the resolver reports it and the UI states it. */
export interface KyoSourceReport {
  name: KyoSourceName;
  /** Short human label: "working tree" / "kyo on your login PATH". */
  label: string;
  available: boolean;
  /**
   * The resolved command line when available, or the refusal naming what is
   * missing when not. Always populated: a source that cannot run says why.
   */
  detail: string;
}

/** Which source the app is actually invoking `kyo` through, and what else it saw. */
export interface KyoSourceFacts {
  chosen: KyoSourceName | null;
  /** The chosen source's report, or the refusal of the last candidate tried. */
  detail: string;
  label: string;
  candidates: KyoSourceReport[];
}

/**
 * The store identity the title bar shows and the schema version the status bar
 * shows. `ok: false` carries the reason instead -- never a fabricated number.
 */
export interface StoreFacts {
  ok: boolean;
  /** The project's record-id prefix, e.g. "KYO". */
  project: string | null;
  /** The code repository's current git branch, as `kyo` reports it. */
  branch: string | null;
  /** Rows in the `records` table. */
  records: number | null;
  /** The `schema_version` stamped in the database's own `meta` table. */
  schemaVersion: number | null;
  /** `in-repo` | `split` | `unconfigured`, as the CLI resolved it. */
  layout: string | null;
  storeRoot: string | null;
  dbPath: string | null;
  problem: string | null;
}

/**
 * The four states `kyosei.currency` can report, plus two honest non-answers.
 *
 * The four are a verdict *about the store*, produced by the CLI. The last two are
 * verdicts about this app's attempt to ask, and are deliberately not folded into
 * `cannot-tell`: "I could not run kyo" is not the same claim as "kyo compared the
 * two sides and could not tell which moved", and showing the second when the first
 * happened would be the app asserting a store state it never observed.
 */
export const CURRENCY_STATES = [
  "current",
  "database-ahead",
  "export-ahead",
  "cannot-tell",
  "unrecognised",
  "unavailable",
] as const;
export type CurrencyState = (typeof CURRENCY_STATES)[number];

/** The four states the status bar can render, in the order the CLI names them. */
export const STORE_CURRENCY_STATES: readonly CurrencyState[] = [
  "current",
  "database-ahead",
  "export-ahead",
  "cannot-tell",
];

export interface CurrencyFacts {
  state: CurrencyState;
  /** The CLI's own `direction` string, verbatim, or null if it never answered. */
  direction: string | null;
  /** The CLI's own one-line sentence (`db_export_currency.says`). */
  says: string;
  /** The CLI's own remedy (`db_export_currency.remedy`); empty when in step. */
  remedy: string;
  /** The command this came from, for the status bar's tooltip. */
  from: string;
  problem: string | null;
}

// ------------------------------------------------- which project (KYO-175)
//
// Before this record the app resolved its project once, from `KYOSEI_PROJECT_ROOT`
// or the checkout it was loaded from, and there was no in-app way to change it. The
// types below are the whole of what a renderer learns about that choice and the
// whole of what it may do to it: read the current project, adopt another, and ask
// again whether the current one opens. None of them can write to a store.

/**
 * Where the app's current project came from, highest precedence first.
 *
 * The ORDER is the contract, not the list. `KYOSEI_PROJECT_ROOT` outranks every
 * in-app choice (KYO-175.c8): it is what the checks drive to open a fixture store,
 * and the record is explicit that this is "the same capability rather than a
 * test-only back door" -- a persisted last-opened project that outranked it would
 * break the checks and would do it by making the app ignore an explicit instruction.
 * `chosen` is the in-app choice, remembered across launches; `checkout` is KYO-79
 * decision 3's default, the repository the app itself was loaded from.
 */
export const PROJECT_SOURCES = ["environment", "chosen", "checkout", "remote"] as const;
export type ProjectSource = (typeof PROJECT_SOURCES)[number];

/**
 * How a store's schema stands against the one this build speaks.
 *
 * Mapped from `errors.SchemaMismatch`'s `direction` (KYO-178) by one lookup table in
 * `facts.ts`, exactly as `db_export_currency`'s direction is. Both parties are named
 * in each word for the reason the CLI names both in its own: `facts.ts` already maps
 * a `direction` field about the db/export seam (KYO-74), whose values include
 * `db-ahead`, and one consumer maps both. `store-ahead-of-app` and
 * `app-ahead-of-store` cannot be confused with it or with each other.
 *
 * The distinction is not cosmetic and it is KYO-175.c4: the two directions have
 * OPPOSITE remedies and one of them is destructive. A store ahead of the build is
 * intact and must not be reconstructed -- `kyo rebuild --force` would rewrite it
 * under the older schema and drop whatever the later one added (KYO-168).
 */
export const SCHEMA_STANDINGS = [
  "store-ahead-of-app",
  "app-ahead-of-store",
  "config-drift",
  "unrecognised",
] as const;
export type SchemaStanding = (typeof SCHEMA_STANDINGS)[number];

/**
 * A store this build cannot open, as `errors.SchemaMismatch`'s payload states it.
 *
 * Every field here is read out of the `schema_mismatch` key of the CLI's own `--json`
 * error envelope. Nothing is parsed out of `says`: that is the point of KYO-178, and
 * it is what lets the CLI improve its wording without moving any code in this app.
 * `says` is rendered and never matched.
 */
export interface SchemaMismatchFacts {
  standing: SchemaStanding;
  /** The CLI's own `direction` string, verbatim, or null if it named none. */
  direction: string | null;
  /** `"build"`, `"db"`, or null where neither party is the older one. */
  olderSide: string | null;
  buildSchemaVersion: number | null;
  dbSchemaVersion: number | null;
  configSchemaVersion: number | null;
  configPath: string | null;
  dbPath: string | null;
  /** The CLI's refusal, verbatim. Shown to the reader; never branched on. */
  says: string;
  /** The command that produced it, for the surface to cite. */
  from: string;
  /** Set only for `unrecognised`: a direction this build does not know. */
  problem: string | null;
}

/** One project the app remembers being pointed at. */
export interface ProjectEntry {
  root: string;
  /** The record-id prefix from that store's `config.yml`, when it could be read. */
  project: string | null;
}

/**
 * One row the switcher offers (KYO-465): a project, the kyo serve it is served from,
 * and whether it is one of this installation's recents -- which the reader can remove
 * -- or a project only this machine's registry lists, which the CLI edits
 * (`kyo projects remove`, KYO-455) and the app does not.
 */
export interface OfferedProject extends ProjectEntry {
  /** The kyo serve it is read through; null for this machine's own. */
  server: RemoteFacts | null;
  /** In this installation's recents, so "remove" applies to it. */
  recent: boolean;
}

/** Which recent to forget (KYO-465.c3): a project on a server, by root. */
export interface RecentForgetInput {
  root: string;
  /** The kyo serve it is read through; null for this machine's own. */
  server: RemoteFacts | null;
}

/**
 * The dev box a remote project lives on (KYO-370): the address and nothing else.
 * The token stays in the main process's own data directory and never reaches a
 * renderer.
 */
export interface RemoteFacts {
  host: string;
  port: number;
}

// ------------------------------------------------ New project, over SSH (KYO-460)
//
// The app changes a machine only by running ordinary CLI commands there (KYO-452):
// this machine's own, or another's over the person's SSH (`main/ssh.ts`). A server
// entry is a dev box the app has connected to; each carries the SSH host its commands
// go to, which is the entry's own host name until someone sets another.

/** A machine New project can make a project on. */
export interface ProjectServerEntry {
  /** The dev box's address as the app connects to it; null for this machine. */
  server: RemoteFacts | null;
  /** What the picker calls it. */
  label: string;
  /** Where its commands go over SSH; null for this machine. */
  sshHost: string | null;
}

/** One folder on a machine, as the directory picker shows it. */
export interface FolderListing {
  ok: boolean;
  /** The folder's absolute path on that machine, when it could be listed. */
  path: string | null;
  /** The folders in it, sorted as a picker sorts them. Files are not listed. */
  folders: string[];
  /** Which machine answered, by name or SSH host. */
  machine: string;
  /** What the listing command printed on stderr, verbatim, when it failed. */
  problem: string | null;
}

/** Make one folder, or say why not (the command's stderr, verbatim). */
export interface FolderMade {
  ok: boolean;
  problem: string | null;
}

/**
 * Where New project puts the store (KYO-573, setup decision B1): a store of its own in
 * the home folder (`kyo init --defaults`), or inside the code repository
 * (`kyo init --in-repo`).
 */
export type StoreLocation = "split" | "in-repo";

/** The choice New project makes when none is given. */
export const DEFAULT_STORE_LOCATION: StoreLocation = "split";

/**
 * Each store location's label in the New project dialog, worded as KYO-573 settled
 * it -- including the split label's closing parenthesis, which has no opening one.
 * Not to be tidied.
 */
export const STORE_LOCATION_LABELS: Readonly<Record<StoreLocation, string>> = {
  split:
    "this keeps the kyosei store in your home folder, a separate git repository is " +
    "initialized there for the store only, and nothing from kyosei is pushed to your " +
    "remote code repository)",
  "in-repo":
    "store the kyosei store inside the code repo (this may expose your ideas, records, " +
    "your quoted reasoning, and anything else you do in kyosei to the public)",
};

/** What New project asks for: where, where its store lives, and whether to start a controller there. */
export interface NewProjectInput {
  server: RemoteFacts | null;
  root: string;
  storeLocation: StoreLocation;
  startController: boolean;
}

/** What `kyo controller start --json` reported about the session it started. */
export interface StartedControllerFacts {
  session: string;
  pid: number | null;
  cwd: string | null;
  startedWith: string | null;
  /** The command that attaches to it, on that machine. */
  attach: string | null;
  /** The name claude.ai lists it under, when the command gives one. */
  remoteControlName: string | null;
  /** The folder Claude Code is asking whether to trust, when start saw its prompt (KYO-567). */
  trustFolder: string | null;
}

/** What New project did, step by step: enough to say it, never the raw output. */
export interface NewProjectOutcome {
  ok: boolean;
  /** The project's directory, on `machine`. */
  root: string;
  machine: string;
  /** The project's name and prefix, as `kyo init` reported them. */
  name: string | null;
  prefix: string | null;
  /** Whether `kyo init` registered it in that machine's registry (KYO-455). */
  registered: boolean;
  /** The controller session, when one was asked for and started. */
  controller: StartedControllerFacts | null;
  /** The step that failed, and what it printed on stderr, verbatim. */
  failedStep: "mkdir" | "init" | "controller" | null;
  problem: string | null;
}

/** Setting a server entry's SSH host: the host it has now, and why not when refused. */
export interface SshHostOutcome {
  ok: boolean;
  problem: string | null;
  sshHost: string;
}

/** One dev box connection the app remembers, and the project it opened there. */
export interface RemoteEntryFacts extends RemoteFacts {
  /** The project root ON THE DEV BOX. */
  root: string;
  /** That store's record-id prefix, when the dev box could read it. */
  project: string | null;
}

/**
 * What the connect dialog sends (KYO-459): the server's address as typed --
 * `https://<machine>.<tailnet>.ts.net`, or `127.0.0.1:<port>` -- and which of its
 * projects. No password and no token: the tailnet's access rule is the boundary.
 */
export interface RemoteConnectInput {
  address: string;
  /** Which of the server's projects to open (KYO-374): one of the roots it listed. */
  root?: string;
  /** Open a NEW window on it (the New Window chooser's row) rather than moving this one. */
  newWindow?: boolean;
  /**
   * A path typed for the server rather than one it listed: refused by name, since kyo
   * serve adds no project (KYO-452) -- `kyo projects add` runs there, over SSH.
   */
  add?: boolean;
}

/**
 * This machine's project registry as it stands (KYO-379): `$KYOSEI_HOME/projects.json`,
 * the projects this machine holds and serves. Answered by the `readRegistry` member,
 * so a dev box answers it from its own registry.
 */
export interface RegistryView {
  ok: boolean;
  /** The registry file, or null when `kyo` could not say where it is. */
  path: string | null;
  /** Every project it lists, in its order, with the prefix each store's config names. */
  projects: ProjectEntry[];
  /** Why it could not be read -- an unparseable file is refused by name; null when `ok`. */
  problem: string | null;
}

/** What adding a project by its path answered (KYO-379.c3, the `addProject` member). */
export interface AddProjectResult {
  ok: boolean;
  /** The root added, resolved on the machine that holds it; the typed text when refused. */
  root: string;
  /** Its store's record-id prefix, when added. */
  project: string | null;
  /** The registry it was added to, when that could be located. */
  path: string | null;
  /** The refusal -- for a directory with no project, the CLI's own sentence. */
  problem: string | null;
}

/** What a dev box's kyo serve answers to a probe (KYO-374): reachable or not, and its projects. */
export interface RemoteProbeFacts {
  ok: boolean;
  /** The refusal, naming the address and what was refused; null when `ok`. */
  problem: string | null;
  /** Unused since KYO-459 (a server has no "open project"); always null. */
  chosen: string | null;
  /** Every project the server serves (`/v1/projects`), with the prefix it read. */
  recents: ProjectEntry[];
}

/** A remembered connection, named by what the switcher shows. */
export interface RemoteOpenInput extends RemoteFacts {
  root: string;
}

/** Which project the app is reading, why that one, and whether its store opens. */
export interface ProjectFacts {
  /** The directory every `kyo` invocation runs in. */
  root: string;
  source: ProjectSource;
  /** `KYOSEI_PROJECT_ROOT`, when it is set -- the value that outranks the rest. */
  environmentRoot: string | null;
  /** The in-app choice, when one has been made (this session or a previous one). */
  chosenRoot: string | null;
  /** The checkout the app was loaded from: the default, KYO-79 decision 3. */
  checkoutRoot: string;
  /** The store root the CLI located under `root`, or null when there is none. */
  storeRoot: string | null;
  /** That store's record-id prefix, when it could be read. */
  project: string | null;
  /**
   * Why this build cannot open that store, or null when it opens.
   *
   * Caught in ONE place -- `probeProject` in `facts.ts` -- rather than per pane, on
   * the precedent the `facts` handler already sets for a missing runtime: say it
   * once, in all three places, rather than letting each read fail separately with
   * its own version of the same sentence.
   */
  mismatch: SchemaMismatchFacts | null;
  /**
   * What the switcher offers (KYO-379.c1): this installation's recents, newest first --
   * each keyed and labelled by the kyo serve it is read through (KYO-465.c2) -- then
   * every other project this machine's registry (`$KYOSEI_HOME/projects.json`) lists,
   * in the registry's order.
   */
  recents: OfferedProject[];
  /**
   * The dev box this window's project lives on, or null for a project on this
   * machine (KYO-370). When set, `root` is a path on THAT machine, every read and
   * write this window makes runs there, and there is no terminal here.
   */
  remote: RemoteFacts | null;
  /** Dev boxes connected to before, newest first, for the switcher (KYO-370). */
  remotes: RemoteEntryFacts[];
}

/** What the renderer learns from asking the app to open a different project. */
export interface ProjectChoice {
  /** What the app is looking at now -- unchanged when the candidate was refused. */
  project: ProjectFacts;
  /**
   * The candidate that was refused, and why (KYO-175.c6).
   *
   * A directory with no Kyosei project at or above it is refused at the moment it is
   * chosen, carrying the CLI's own sentence about what was looked for, rather than
   * being adopted and then rendered as a broken store. A store at another SCHEMA is
   * not refused: it is adopted, and its refusal becomes `project.mismatch`, which is
   * what makes the mismatch state re-checkable in place (KYO-175.c3).
   */
  refused: { root: string; problem: string } | null;
  /** The human dismissed the directory picker; nothing was asked of anything. */
  cancelled: boolean;
  /** The app's project actually moved, so every window has work to redo. */
  changed: boolean;
}

// ------------------------------------------- is this a kyo the app reads (KYO-449)
//
// KYO-443: the app declares the kyo releases it reads (`KYO_ACCEPTS`, beside the
// resolver in `main/kyo.ts`) and judges a kyo's `version --json` against it. Two kyos
// are judged, each for what the app does with it: the kyo serve a window reads through
// (`/v1/version`, KYO-452 -- the one that gates the records views), and the kyo on
// the login PATH the app itself runs on this machine (`kyo projects add`, the terminal
// panes, `kyo controller`). Each lands in one of these states, whose words are
// KYO-440 c2's, built once in `main/kyo.ts` (`kyoCompatFrom`).

export const KYO_COMPAT_STATES = [
  /** It answered as Kyosei's kyo, at a release inside `KYO_ACCEPTS`. */
  "accepted",
  /** There is no kyo on the login PATH (for a server: nothing answered, `ServeFacts.running`). */
  "no-kyo",
  /** It parses as Kyosei's, below the range. */
  "too-old",
  /** It parses as Kyosei's, at or above the range's upper bound. */
  "too-new",
  /** It did not answer `version --json` (or `/v1/version`) the way Kyosei's does. */
  "not-kyosei",
] as const;
export type KyoCompatState = (typeof KYO_COMPAT_STATES)[number];

/**
 * One judged kyo, and the words its state shows: `title`, then `says`, then either the
 * one `command` to run or the CLI's own `route` sentence for its install kind, then
 * `after`. An `accepted` kyo carries a one-line `title` and empty words otherwise.
 */
export interface KyoCompatFacts {
  state: KyoCompatState;
  /** What was judged, as the words name it: a path on this machine, or a server's address. */
  where: string;
  /** The release it reported, when it reported one. */
  version: string | null;
  /** The range this app reads (`KYO_ACCEPTS`). */
  accepts: string;
  title: string;
  says: string;
  command: string | null;
  route: string | null;
  after: string;
}

/**
 * The kyo serve a window's project is read through (KYO-459), asked afresh with every
 * facts read: is it answering, and when it is not, the one sentence that says so.
 */
export interface ServeFacts {
  /** Its address, as a person types it: `http://127.0.0.1:47370`, `https://<name>`. */
  address: string;
  /** This machine's own kyo serve (the one `kyo serve --install --apply` starts). */
  local: boolean;
  /**
   * Nothing REFUSED `/v1/version` there. A server that took the connection and did not
   * answer in time is running and slow (`slow`), never "not running" (KYO-449.c6).
   */
  running: boolean;
  /** It took the connection and did not answer `/v1/version` in time (KYO-449.c6). */
  slow: boolean;
  /** Why not, when it did not answer: for this machine's, naming both ways to start it. */
  problem: string | null;
  /** The commands that start this machine's kyo serve, for the state to show. */
  commands: string[];
  /**
   * Its `/v1/version` judged against `KYO_ACCEPTS` (KYO-449.c5), naming this address;
   * null when nothing answered it (not running, or slow), so there was nothing to judge.
   */
  compat: KyoCompatFacts | null;
  /**
   * Its `/v1/version` said `stale: true` (KYO-550): the kyo installed on disk there is
   * no longer the build the running server started as. False when it said otherwise, or
   * when nothing answered it. A stale server still serves; this is a notice, not a state.
   */
  stale: boolean;
  /** Its `stale_detail`, naming both builds, when it was stale and said why. */
  staleDetail: string | null;
}

/** What restarts a kyo serve that is running an older build than the one installed. */
export const SERVE_RESTART_COMMAND = "kyo serve --restart";

/**
 * The status-bar line for a stale kyo serve (KYO-550.c1), or null when it is not stale.
 * Never an overlay (KYO-550.c2): a stale server still serves, so the scratchpad and the
 * records views stay as they are, and this one line says what to run. Electron-free, so
 * a check feeds it both `/v1/version` payloads (`tests/seams.spec.ts`).
 */
export function serveStaleNotice(serve: ServeFacts): string | null {
  if (!serve.running || serve.slow || !serve.stale) return null;
  const detail =
    serve.staleDetail ??
    `the kyo serve at ${serve.address} is running an older build than the one installed`;
  return `${detail} -- run \`${SERVE_RESTART_COMMAND}\`${serve.local ? "" : ` on the machine at ${serve.address}`}`;
}

/**
 * Whether a change pushed to the window means its kyo serve must be asked again NOW
 * (KYO-471): the events stream closed or re-opened (`server`, `kyoserve.ts`'s own
 * kind). Not while the re-probe loop (KYO-449.c8) is already asking -- that loop is
 * what clears the state, and a second one is never started beside it.
 */
export function serveReprobeDue(change: StoreChange, reprobing: boolean): boolean {
  return change.kind === "server" && !reprobing;
}

/**
 * How often a window asks again while its kyo serve is not running or slow to answer
 * (KYO-449.c8): often enough that starting it clears the state without a click.
 */
export const SERVE_REPROBE_MS = 3_000;

/**
 * Ask again every `everyMs` while the answer says the server is still down, and stop at
 * the first answer that says it is not (KYO-449.c8). One probe in flight at a time: the
 * next is scheduled only after the last one answered, so a slow server is never asked
 * twice at once. Electron-free, and timer-injected so a check can drive it without
 * waiting; the window hands it `window.setTimeout`.
 */
export function reprobeWhileDown<T>(options: {
  probe: () => Promise<T>;
  stillDown: (answer: T) => boolean;
  onAnswer: (answer: T) => void;
  everyMs?: number;
  schedule?: (run: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
}): { stop: () => void; readonly running: boolean } {
  const everyMs = options.everyMs ?? SERVE_REPROBE_MS;
  const schedule =
    options.schedule ?? ((run: () => void, ms: number): unknown => setTimeout(run, ms));
  const cancel =
    options.cancel ??
    ((handle: unknown): void => clearTimeout(handle as ReturnType<typeof setTimeout>));
  let stopped = false;
  let handle: unknown = null;
  const next = (): void => {
    if (stopped) return;
    handle = schedule(() => {
      handle = null;
      if (stopped) return;
      options
        .probe()
        .then((answer) => {
          if (stopped) return;
          const down = options.stillDown(answer);
          if (!down) stopped = true;
          options.onAnswer(answer);
          if (down) next();
        })
        .catch(() => next());
    }, everyMs);
  };
  next();
  return {
    stop: () => {
      stopped = true;
      if (handle !== null) cancel(handle);
      handle = null;
    },
    get running() {
      return !stopped;
    },
  };
}

/** Everything the live seams supply, in one round trip at window start. */
export interface LiveFacts {
  store: StoreFacts;
  currency: CurrencyFacts;
  kyo: KyoSourceFacts;
  /** Which project all three of the above are about (KYO-175). */
  project: ProjectFacts;
  /** The kyo serve all of them were read through (KYO-459). */
  serve: ServeFacts;
  /**
   * The kyo this app runs on this machine (`kyo` above), judged against `KYO_ACCEPTS`
   * by its own `kyo version --json` (KYO-449.c1): the one `kyo projects add` and the
   * terminal panes run, whichever server the window reads through.
   */
  kyoCompat: KyoCompatFacts;
}

/**
 * How the terminal pane got its shell (KYO-115).
 *
 * `tmux` is the pane attached to the shared per-project session -- the same one
 * PyCharm's terminal attaches to, which is the whole of "view and respond from the
 * app". `bare` is KYO-111's shell, alone in this window. Two words rather than a
 * boolean because the pane has to *say* which of them it is, and "not tmux" is not
 * a sentence a human can act on.
 */
export const TERMINAL_MODES = ["tmux", "bare"] as const;
export type TerminalMode = (typeof TERMINAL_MODES)[number];

// ------------------------------------------- one pane, two ptys (KYO-293)
//
// Until this record the pane WAS one pty, and an attach was a command TYPED into it.
// That failed whenever the bare terminal was running a long command: the attach could
// not be typed in and entered. A shell busy in a foreground command has its stdin
// taken by that command, so the keystrokes landed in `sleep`'s input instead.
//
// So the pane owns up to two ptys, and neither is ever typed into on the other's
// behalf:
//
//   `client`   the process IS tmux -- node-pty spawns the tmux binary directly, with
//              `attach-session` in its argv and no shell in between. There is nothing
//              to type into, so nothing can be busy; and the app knows this client's
//              tty because it created it, which is what makes `detach-client -t <tty>`
//              exact rather than a guess.
//   `bare`     the reader's own login shell, spawned the first time the leftmost
//              control is used and kept for the life of the pane -- the same shell,
//              with its screen and its scrollback, every later time it is shown.
//
// Both stream to the renderer at all times, each tagged with its surface, so the one
// that is hidden keeps painting into its own screen. Switching between them shows a
// screen that is already up to date rather than replaying one.
export const PANE_SURFACES = ["client", "bare"] as const;
export type PaneSurface = (typeof PANE_SURFACES)[number];

/** One chunk from one of a pane's two ptys. */
export interface TerminalChunk {
  surface: PaneSurface;
  data: string;
}

/** One of a pane's two ptys ended. */
export interface TerminalExitInfo {
  surface: PaneSurface;
  exitCode: number;
}

/** Keystrokes, addressed to the screen the reader typed into. */
export interface TerminalInput {
  surface: PaneSurface;
  data: string;
}

/** A resize, addressed the same way: the hidden surface keeps the size it had. */
export interface TerminalResize extends TerminalSize {
  surface: PaneSurface;
}

/**
 * What the pane offers when it could not attach, or its connection ended (KYO-474.c4).
 *
 * `start` where the project has no controller session: the claude tab's Start
 * controller, the same KYO-460 flow (`startController`), after which the pane attaches.
 * `reconnect` where there is one to go back to, or where ssh itself failed.
 */
export const TERMINAL_OFFERS = ["start", "reconnect"] as const;
export type TerminalOfferAction = (typeof TERMINAL_OFFERS)[number];

export interface TerminalOffer {
  action: TerminalOfferAction;
  /** The session a Reconnect or a Start then attaches to: the project's prefix. */
  session: string;
  /**
   * What ssh, kyo or tmux said, verbatim -- its stderr, or the last lines the client
   * printed before it ended -- or null when it said nothing. Never paraphrased.
   */
  said: string | null;
}

/** What a renderer learns when it asks for a shell behind its terminal pane. */
export interface TerminalOpen {
  ok: boolean;
  /**
   * Set when this window is on a dev box's project and there is no SSH host to reach it
   * with (KYO-370.c4, narrowed by KYO-474): the tab says where to set one, where the
   * pane would be. A dev box WITH an SSH host gets a real pane, whose client is ssh.
   */
  remote?: RemoteFacts;
  /**
   * Where the session this pane shows lives: `local` (this machine's controller server)
   * or `ssh` (a dev box's, through the person's own SSH). Absent on a refusal.
   */
  where?: "local" | "ssh";
  /** The SSH host a dev box pane's client runs `ssh -t` to; null on this machine. */
  sshHost?: string | null;
  /** What the pane offers since it is not attached, or null (KYO-474.c4). */
  offer?: TerminalOffer | null;
  /** The pty's working directory -- the project root. */
  cwd: string | null;
  /** The shell that was spawned. */
  shell: string | null;
  /**
   * Where `kyo` resolves inside that shell: the first one on the pane's PATH, which is
   * the login shell's (KYO-450 -- the app puts no shim in front of it). Null when there
   * is none there.
   */
  kyoOnPath: string | null;
  problem: string | null;
  /** Attached to the shared session, or the bare fallback. */
  mode: TerminalMode;
  /** The tmux session this pane is attached to; null in `bare` mode. */
  session: string | null;
  /**
   * Which of the pane's two ptys is on screen (KYO-293).
   *
   * `client` wherever this project's session could be attached -- which, since this
   * record, is the ordinary open -- and `bare` on a machine with no tmux, a project
   * with no session name, or a server that refused. `mode` above says the same thing
   * in the reader's vocabulary; this one says it in the pane's.
   */
  showing: PaneSurface;
  /**
   * Which pty this view is showing (KYO-124), or null when there is none to show.
   *
   * The main process's own name for the shell, and the reason it is on the wire at
   * all: two windows showing ONE session and two windows each holding a session of
   * their own are indistinguishable from outside otherwise, and that difference is
   * the whole of what popping a pane out is supposed to mean. The renderer writes it
   * to `data-terminal-pty` and does nothing else with it -- it is a name, not a
   * handle: a renderer cannot name a session it was not given, and being told this
   * one grants it nothing it did not already have over its own pane.
   *
   * Not the same fact as `session` above, which is the *tmux* session's name and is
   * null wherever there is no tmux. This one is present wherever a shell is.
   */
  sessionId: string | null;
  /** The line to type in any other terminal to join it; null in `bare` mode. */
  join: string | null;
  /**
   * This window's project's own tmux session name (KYO-283), or null when there is
   * no tmux or no per-project identity to name one after.
   *
   * A NAME, not a state: since KYO-283 the pane opens attached to nothing, and this
   * is the session its pill stands for -- first in the row and set apart, whether it
   * is running yet or not.
   */
  projectSession: string | null;
  /**
   * The tmux socket every command in this feature names (KYO-283.c5), or null when
   * there is no tmux. On the wire so the pane can show the reader the exact command
   * it would run, and so a check can read it off the DOM.
   */
  socket: string | null;
  /**
   * The pane's status line, composed in the main process because every fact in it
   * is a main-process fact. Never empty: a pane sharing the user's live
   * Claude session and a pane that is a private shell look identical until one of
   * them says which it is (law 6).
   */
  notice: string;
  /**
   * The pill row as the main process saw it at the instant the pane opened (KYO-293).
   *
   * On the wire so the row is drawn on the FIRST paint rather than one poll later:
   * c1 asks that the project's pill be highlighted from the first paint, and a row
   * that appeared a tick after the screen would be a pane that looked bare for a
   * moment while being in a session. Null when there was no shell to open at all.
   */
  sessions: TerminalSessionsView | null;
}

export interface TerminalSize {
  cols: number;
  rows: number;
}

// --------------------------------- the terminal's session pills (KYO-283, KYO-293)
//
// KYO-283 asks for a row of pills above the pane, one per live tmux session, with the
// project's own session first and the one the pane is in highlighted. A click on a pill
// attaches the pane's client to that session directly.
//
// KYO-283 also asked that the tab always open a blank terminal, with a fallback that
// prefilled the bare prompt where a direct attach was not possible. Both
// halves of that are gone (KYO-293): the tab opens IN the project's session, and the
// fallback has no case left, because the attach no longer needs a prompt to put
// anything on. `ATTACH_ACTIONS` below is the vocabulary that remains.
//
// Every field below is a MAIN-PROCESS fact: which sessions are alive and which one this
// pane's client is in. The renderer draws them and decides nothing -- it cannot see a
// tmux server, and a pill composed in the renderer would be a guess with a click on it.

/** One pill above the pane's screen. */
export interface SessionPill {
  name: string;
  /** This window's project's own session: first in the row, and set apart. */
  project: boolean;
  /** Alive on the server right now; a pill that is not live can only be started. */
  live: boolean;
  /** This pane's client is in it -- the highlighted one. */
  current: boolean;
  /** How many clients are in it, this pane's included. */
  clients: number;
}

/** What the pill row is drawn from, re-read on the cadence below. */
export interface TerminalSessionsView {
  pills: SessionPill[];
  /** The session this pane's client is in, read from `list-clients`. */
  attached: string | null;
  /** The socket every command names, or null when there is no tmux. */
  socket: string | null;
  /** Why there is no list, when there is none. Null when the list is the answer. */
  problem: string | null;
}

/**
 * What a click on a pill or on the bare control did, in the seam's own words.
 *
 * Three of KYO-283's five are gone with the typing (KYO-293.c5). `detach-then-attach`
 * and `prefill` were both about a pane whose ONE pty was a shell: the first detached
 * that shell's client before typing the next command into it, and the second gave up
 * and left the command on the input line for a reader who could not be typed at. A
 * client that is its own pty is started and stopped by this app, so there is one
 * route in and no case in which a command has to be handed over.
 *
 * `attach` put the pane's client in that session, `bare` showed the login shell and
 * detached the client, `already` was a click on what the pane is showing, and
 * `refuse` is a machine with no tmux or a session that has gone. `ended` (KYO-474) is a
 * dev box pane whose ssh client ended -- the connection dropped, the attach was
 * refused there, or the reader detached -- which has no bare shell to fall back to.
 */
export const ATTACH_ACTIONS = ["attach", "bare", "already", "refuse", "ended"] as const;
export type AttachAction = (typeof ATTACH_ACTIONS)[number];

export interface TerminalAttachOutcome {
  action: AttachAction;
  /** The session asked for, or null for the bare control. */
  session: string | null;
  /** Which of the pane's two screens is showing now (KYO-293). */
  surface: PaneSurface;
  /** What the strip says about it. Never empty (law 6). */
  notice: string;
  /** What the pane is now, as far as this act made it so. */
  mode: TerminalMode;
  /** What the pane offers next, when it is not attached (KYO-474.c4). */
  offer?: TerminalOffer | null;
}

/**
 * How often the pill row is re-read.
 *
 * The cadence this app already polls with (`ACTIVITY_POLL_MS`, `AWAITING_POLL_MS`),
 * stated here rather than picked again: a session list that refreshed faster than the
 * board beside it would be a second rhythm in one window for no reason anybody asked
 * for. The pane also re-reads on open and after every attach or detach, which is
 * where a change the reader CAUSED shows up without waiting for a tick.
 */
export const TERMINAL_POLL_MS = 5_000;

// ------------------------------------------------------------- records mode
//
// KYO-112. Everything below describes surfaces that paint *store facts*, so every one
// of them carries a `CurrencyFacts` of its own: KYO-74's obligation is that a surface
// showing store contents can say which side of the db/export seam moved, and a payload
// that carried rows but no verdict would be exactly the surface that reads as the whole
// truth while the database is behind its export.
//
// KYO-232 deleted the v2 renderer and, with it, every shape here that only that
// renderer read: the four view pills, the bubble bindings, the typed-edge graph's
// filter and edge type, and the two modes' tool lists. A shared type with no consumer
// on either side of the boundary is not a contract, it is a leftover. What remains is
// what the MAIN process still answers with -- `kyo base run`'s envelopes, the record
// detail, the briefs -- plus the grouping vocabulary `main/records.ts` builds its argv
// from.

/**
 * The three groupings a saved base or a tree read can be asked for.
 *
 * They are the words a reader ASKS with, which is the CLI's vocabulary too -- `kyo list
 * --status open` is spelled with this word. Which engine field each one is answered
 * from is `TREE_GROUPING_FIELDS` below, and for one of the three those are deliberately
 * not the same string.
 */
export const TREE_GROUPINGS = ["kind", "status", "scope"] as const;
export type TreeGrouping = (typeof TREE_GROUPINGS)[number];

export function isTreeGrouping(value: unknown): value is TreeGrouping {
  return typeof value === "string" && (TREE_GROUPINGS as readonly string[]).includes(value);
}

/**
 * The Base-engine field each grouping is actually answered from (KYO-121).
 *
 * Two of the three are the identity. The third is the whole record: **the status
 * grouping groups by `standing`, never by the stored `status` column.**
 *
 * `query.py` projects both on every row, deliberately and with the reason beside it
 * (KYO-44.c2): the stored `status`, and `standing` -- "what that status *means* for this
 * record's archetype. Beside the stored value, never instead of it". A knowledge record
 * (a decision, a piece of evidence) still stores the initial work status in the shared
 * column and *reads* as `current`; `standing` is where that reading lives.
 *
 * Grouping the raw column therefore files every decision in this project under `open`,
 * and that breaks a stated law rather than a label: `kyo show` and `kyo list` both print
 * the role word precisely so that **an `open` in any listing is a real, dispatchable
 * open** (KYO-46, KYO-101). This mapping is not a second vocabulary -- it is the CLI's
 * own: KYO-99 made `kyo list --status` run against the computed word for this reason.
 */
export const TREE_GROUPING_FIELDS: Record<TreeGrouping, string> = {
  kind: "kind",
  status: "standing",
  scope: "scope",
};

/**
 * The status word a LISTING shows and filters by for one row (KYO-365).
 *
 * The same rule as the grouping above, for a surface that has rows instead of a
 * `--group-by`: `standing` when the row carries it, so a decision reads `current` and
 * never `open`. The stored status is the answer only when `standing` is absent -- the
 * direct-read fallback, where the kind registry that derives the word is out of reach --
 * because a filter with no value for a row would hide the row, which is worse than the
 * raw word.
 */
export function listingStatus(row: { status: string; standing: string | null }): string {
  return row.standing === null || row.standing === "" ? row.status : row.standing;
}

/** The engine field for a grouping: what goes after `--group-by`. */
export function groupingField(grouping: TreeGrouping): string {
  return TREE_GROUPING_FIELDS[grouping];
}

/**
 * The two questions the folder tree can be asked (KYO-122.c9).
 *
 * The tree needs at least an everything / open-work switch and a search box, and
 * these two words are used because they are the names of two SAVED BASES. The pill
 * switches which base the tree runs, so the
 * definition of "open work" is the store's own -- `kyosei/bases.yml` -- and not a
 * status rule this app made up beside it (law 3). A store that declares no such base
 * gets the CLI's refusal in the pane, which is the honest answer to "what is open
 * work here" when the project has not said.
 */
export const TREE_SCOPES = ["everything", "open-work"] as const;
export type TreeScope = (typeof TREE_SCOPES)[number];

export function isTreeScope(value: unknown): value is TreeScope {
  return typeof value === "string" && (TREE_SCOPES as readonly string[]).includes(value);
}

/** One saved base, as `kyo base list --json` declares it. */
export interface BaseDefinition {
  name: string;
  summary: string;
  source: string;
  columns: string[];
  sort: string[];
  groupBy: string | null;
  filters: Record<string, string>;
}

/** The bases saved in `kyosei/bases.yml`, or the CLI's refusal to read them. */
export interface BaseCatalogue {
  ok: boolean;
  path: string | null;
  exists: boolean;
  bases: BaseDefinition[];
  /** The CLI's own message, verbatim. Never a paraphrase, never an empty list. */
  refusal: string | null;
  command: string;
}

/** One group the engine computed, or the app's port of it computed identically. */
export interface BaseGroup {
  value: string | null;
  count: number;
  ids: string[];
}

export interface BaseCounts {
  counted: number;
  notCounted: number;
  total: number;
}

/** The result of running one saved base through the CLI's own engine. */
export interface BaseResult {
  ok: boolean;
  name: string;
  summary: string;
  /** The projection the base declared, in its own order. */
  columns: string[];
  rows: Record<string, unknown>[];
  groups: BaseGroup[] | null;
  counts: BaseCounts | null;
  currency: CurrencyFacts;
  /** The CLI's refusal, verbatim, when `ok` is false. */
  refusal: string | null;
  command: string;
}

/**
 * Which class the board puts a record in, as `kyo next --json` computes it.
 *
 * `settled-not-moved` is KYO-87's class and is deliberately its own value rather
 * than a flavour of `ready`: the whole point of that record is that finished work
 * counted as dispatchable, and a UI that collapsed the two would reintroduce it.
 * `blocked` is KYO-570's list, read in by KYO-571: a record an unsettled `blocks`
 * source holds back, which the CLI no longer lists as ready -- without its own class
 * here it fell to `none`.
 */
export const BOARD_CLASSES = [
  "ready",
  "settled-not-moved",
  "blocked",
  "scope-unknown",
  "none",
] as const;
export type BoardClass = (typeof BOARD_CLASSES)[number];

// ------------------------------------------- the readiness filter (KYO-290)
//
// The open-work filter also shows records that are built but not verified, or that
// carry any outstanding criterion; a reader looking for dispatchable work alone had no
// filter for it. So the records browser's Filter box gets a group whose values ARE the
// board classes, and the word a reader looks for, "dispatchable", is the label on
// `ready`.
//
// The whole group lives here rather than in the sidebar for two reasons. It is
// Electron-free, so the value and its label can be checked without launching a window
// (KYO-290.c1); and every name below is a RELABELLING of a class the CLI already
// decided, never a second derivation of it -- keeping them beside `BOARD_CLASSES` is
// what makes a class added there show up as a missing label here, at compile time.

/** The filter group's id, as `ListState.filters` keys it and the DOM marks it. */
export const BOARD_FILTER_ID = "readiness";

/**
 * The group's heading.
 *
 * "Readiness", not "board class": the heading should be a word a user would look
 * for, and `kyo next`'s internal noun is not it.
 */
export const BOARD_FILTER_LABEL = "Readiness";

/**
 * The chips' order: what the reader came for, first.
 *
 * Every class is offered, not just `ready`. The ones a reader did not come for
 * are the answers to "then why isn't it in here?" -- a record that is finished but
 * never moved, one an unsettled blocker holds back (KYO-571), one whose scope the
 * board could not read, and one the board does not list at all -- and each is a click away only because they share this
 * group. One value picked is the filter they asked for; that is the common case and
 * costs nothing extra.
 */
export const BOARD_FILTER_ORDER: readonly BoardClass[] = [
  "ready",
  "settled-not-moved",
  "blocked",
  "scope-unknown",
  "none",
];

/**
 * The reader's word for each class. `Record<BoardClass, string>` on purpose: a class
 * added to `BOARD_CLASSES` without a label here fails the typecheck.
 *
 * `none` reads "not on the board" and not "not ready": a `done` record is in it, and
 * so is a record with a dispatch already running, and calling either one unready would
 * be this app inventing a verdict `kyo next` never gave.
 */
export const BOARD_FILTER_LABELS: Record<BoardClass, string> = {
  ready: "dispatchable",
  "settled-not-moved": "finished, not moved",
  blocked: "blocked",
  "scope-unknown": "scope unknown",
  none: "not on the board",
};

/** The value a row has for this filter: the class the CLI gave it, not a re-derivation. */
export function boardFilterValue(row: { boardClass: BoardClass }): BoardClass {
  return row.boardClass;
}

/** The chip's text for a value; an unknown value is drawn as itself rather than hidden. */
export function boardFilterLabel(value: string): string {
  return BOARD_FILTER_LABELS[value as BoardClass] ?? value;
}

/**
 * What the group says when `kyo next` could not be read (KYO-290.c2).
 *
 * With the board unread every row falls to `none`, so a group that still drew its
 * chips would answer "dispatchable" with an empty list -- which reads as "there is no
 * dispatchable work" and is a different claim entirely. The group prints this instead
 * and offers nothing to pick.
 */
export function boardFilterUnavailable(problem: string): string {
  return `${BOARD_FILTER_LABEL} is unknown: \`kyo next\` could not be read, so nothing here can say what is dispatchable — ${problem}`;
}

// ============================ a showing list follows the store (KYO-310)
//
// Two records filed from the terminal pane beside the app (KYO-308, KYO-309) did not
// appear in the sidebar, though both existed. It showed a SNAPSHOT: the sidebar re-read a
// list only when that list was shown or when the project changed, so everything a
// controller filed, moved or closed in the pane beside it was invisible until the reader
// clicked away and back.
//
// The shape is Activity's and Awaiting's: the main process listens, the renderer is
// pushed to. What is new is that what it hears is the STORE'S OWN CHANGE rather than a
// timer that re-runs a read -- since KYO-459, the project's kyo serve's events stream
// (`serve_events.py`), which takes the store's stat fingerprint where the store is
// and pushes `changed` when it moves. An idle store costs nothing at all.
//
// The two kinds are two different worlds with two different writers, and each list
// reads one of them: `kyo` writes the database and `kyosei/export/`, and a person (or
// an agent) writes files under `ideas/`. Telling them apart is what keeps the ideas
// list still while a controller files records, and the records list still while
// somebody edits an idea.
//
// A third kind, `sessions` (KYO-382), is not a sidebar world at all: it is the
// session hooks' `sessions.json` beside the database, whose one consumer is the claude
// pane. It travels the same stream, on this machine's kyo serve and a dev box's alike,
// so a remote window hears it with no second channel, and `main.ts` routes
// it to the pane rather than to the renderer. No list follows it (`LIST_FOLLOWS`
// names none), so a renderer that is ever handed one reloads nothing.
//
// A fourth, `server` (KYO-378.c4), is not a world either: it says the kyo serve behind
// a window's project went away or came back -- this machine's, or a dev box's. It is
// made on the client side, by `kyoserve.ts` when the events stream drops and when it
// re-opens, and kyo serve never sends it. The sidebar answers
// it by showing its list again the way a first load does, not by the quiet re-read
// the other kinds get: a quiet re-read that fails leaves the old rows up and says
// nothing (by design, KYO-310), while a first load that fails says why where the list
// is -- the same sentence, naming the server's address, that any refused call to that
// server is shown with. When the server is back, the same showing draws the list again.

/**
 * Which world moved: the two behind the sidebar's lists (KYO-310), the pane's
 * (KYO-382), or -- `server` -- the server behind them went away or came back (KYO-378).
 */
export type StoreChangeKind = "store" | "ideas" | "sessions" | "scratchpad" | "server";

/**
 * Should an open compose tab re-read its file? (KYO-575.c3) Pure, so it is checked
 * without a window.
 *
 * A tab follows the change kind its file is under -- `scratchpad`, or `ideas` for an
 * idea file -- and `server` too, since a stream that dropped and came back may have
 * missed one. A tab with unsaved edits, or a save out, does NOT reload: its text is the
 * human's, and the save it owes merges with whatever changed (`shared/merge3.ts`).
 */
export function composeReloadDue(input: {
  changed: StoreChangeKind;
  isIdea: boolean;
  dirty: boolean;
  saving: boolean;
}): boolean {
  if (input.dirty || input.saving) return false;
  if (input.changed === "server") return true;
  return input.changed === (input.isIdea ? "ideas" : "scratchpad");
}

/** What `storeChanged` carries: which world moved, and nothing derived from it. */
export interface StoreChange {
  kind: StoreChangeKind;
}

/**
 * Which change each sidebar list follows, or `null` for a list that follows neither.
 *
 * `activity` is null because it already has a live push of its own
 * (`onActivityChanged`), and a second reload path over the top of it would re-run
 * `git` for rows that poll had just delivered. `docs` is null because the documents a
 * project declares are a fact about the working tree rather than about the store's
 * rows, and that list rebuilds its own search box when it reloads -- a reader typing
 * in it while a controller files a record would lose what they had typed, which is a
 * worse defect than the one this record is about.
 */
export const LIST_FOLLOWS: Readonly<Record<string, StoreChangeKind | null>> = {
  records: "store",
  notes: "store",
  awaiting: "store",
  ideas: "ideas",
  docs: null,
  activity: null,
};

/**
 * Is a reload due? The whole decision, in one pure function (KYO-310.c1).
 *
 * Three facts and no window: which list is showing (`null` when the sidebar is
 * collapsed), which world just moved, and whether a reload this list started is still
 * out. A LIST THAT IS NOT SHOWING COSTS NOTHING is this function's first line rather
 * than a property of where the subscription happens to be installed -- so it can be
 * checked without launching Electron, and so a list added later cannot quietly start
 * re-reading a store nobody is looking at.
 */
export function storeReloadDue(input: {
  showing: string | null;
  changed: StoreChangeKind;
  inFlight: boolean;
}): boolean {
  if (input.showing === null) return false;
  // A reload already out: the store moved again while the last read was still
  // running. Dropping this one is right -- the read in flight was started AFTER the
  // change it is answering, so it returns the newer rows anyway, and stacking reads
  // is how one `kyo new` becomes four `kyo` subprocesses.
  if (input.inFlight) return false;
  return (LIST_FOLLOWS[input.showing] ?? null) === input.changed;
}

/** One record as the folder tree draws it. */
export interface TreeRecord {
  id: string;
  kind: string;
  status: string;
  standing: string;
  scope: string | null;
  title: string;
  gatesOpen: number;
  gateTags: string[];
  /**
   * The distinct types of this record's OPEN gates, in gate-creation order
   * (KYO-203.c1) -- beside `gateTags` for the reason `query.py` states: a reader
   * needs to tell `awaiting:event` from `awaiting:human-review` without opening
   * the record.
   */
  gateTypes: string[];
  boardClass: BoardClass;
}

/**
 * Every record in the store, grouped the way the ribbon asked for.
 *
 * `groups` is always the array the CLI's engine returned. This carried a
 * `groupedByEngine` flag until KYO-120, because `kyo base run` had no `--group-by`
 * and two of the ribbon's three groupings had to be computed in the app; the flag
 * existed so that a surface could not imply a provenance it did not have. It is gone
 * because the condition it reported is: `command` carries the `--group-by` the engine
 * was asked for, which is the provenance, and there is no longer a second answer for
 * a field to distinguish it from.
 */
export interface RecordTree {
  ok: boolean;
  grouping: TreeGrouping;
  /** Which of the two saved bases answered this tree (KYO-122.c9). */
  scope: TreeScope;
  groups: BaseGroup[];
  records: TreeRecord[];
  currency: CurrencyFacts;
  refusal: string | null;
  command: string;
}

export interface GateView {
  id: string;
  type: string;
  detail: string | null;
  tags: string[];
  open: boolean;
  createdAt: string;
  clearedAt: string | null;
  clearedWhy: string | null;
}

export interface ClaimView {
  id: string;
  level: string;
  method: string;
  author: string;
  statement: string;
  createdAt: string;
  supersedes: string | null;
  supersededBy: string | null;
}

export interface VerificationView {
  id: string;
  verdict: string;
  method: string;
  author: string;
  createdAt: string;
  reason: string | null;
}

export interface CriterionView {
  id: string;
  text: string;
  state: string;
  reason: string | null;
  verifications: VerificationView[];
}

export interface ReportView {
  id: string;
  seq: number;
  author: string;
  createdAt: string;
  workedFromSha: string;
  /** The dispatch's `report_id` names exactly one; the rest are superseded. */
  current: boolean;
  body: Record<string, unknown>;
}

export interface DispatchView {
  id: string;
  seq: number;
  state: string;
  branch: string | null;
  worktree: string | null;
  workerModel: string | null;
  workedFromSha: string;
  createdAt: string;
  closedAt: string | null;
  terminatedWhy: string | null;
  /**
   * The OTHER records this dispatch formally covers (`--also-covers`, KYO-296).
   *
   * The far side of `CoveringDispatchView` below, and the two are deliberately not
   * one type: this is a list of record ids hanging off THIS record's own dispatch,
   * and that is a whole dispatch belonging to another record reaching over. Read
   * straight out of `kyo show --json`'s per-dispatch `covers` -- the CLI's own
   * `covers_json` -- so the line the terminal prints as `also formally covers` and
   * the line this app draws cannot disagree about who a dispatch spoke for.
   *
   * `[]` for the overwhelming majority of dispatches, and for a CLI older than this
   * build that carries no such key: the page then draws no line at all.
   */
  covers: string[];
  /** KYO-90: a dispatch can carry several reports; this is the one that counts. */
  currentReport: ReportView | null;
  superseded: ReportView[];
}

/**
 * A dispatch on ANOTHER record that formally covers this one (KYO-291).
 *
 * Deliberately not a `DispatchView`: this dispatch is not this record's, it is not
 * counted among its `dispatches`, and none of its work hangs here. What a reader of
 * the covered record needs is the way back -- which dispatch spoke for this record,
 * what state it is in, the record it was actually dispatched against, and the report
 * ingested into it, which is the document whose verdicts settle this record's
 * criteria.
 *
 * Every field is read straight out of `kyo show --json`'s `covered_by_dispatches`.
 * Nothing here is derived: the CLI walks `covers_json` backwards and this carries the
 * answer, so the terminal and the app cannot disagree about who covered what.
 */
export interface CoveringDispatchView {
  id: string;
  state: string;
  /** Where the dispatch, its brief and its report actually live. */
  primaryRecordId: string;
  /** `null` until a report is ingested -- a running dispatch has none yet. */
  reportId: string | null;
}

export interface TouchView {
  id: string;
  path: string;
  symbol: string | null;
}

export interface LinkView {
  type: string;
  direction: string;
  other: string;
}

/** One record, read-only, as the editor renders it. */
export interface RecordDetail {
  ok: boolean;
  id: string;
  kind: string;
  title: string;
  status: string;
  standing: string;
  scope: string | null;
  plan: string | null;
  createdAt: string;
  updatedAt: string;
  boardClass: BoardClass;
  /**
   * The CLI's own remedy for this record's board class, verbatim, or null.
   *
   * Populated for `settled-not-moved`, where `kyo next --json` names the move that
   * clears it. Carried rather than composed here: KYO-87's whole complaint was two
   * surfaces describing one situation differently.
   */
  needs: string | null;
  description: string;
  gates: GateView[];
  claims: ClaimView[];
  criteria: CriterionView[];
  touches: TouchView[];
  links: LinkView[];
  dispatches: DispatchView[];
  /** KYO-291: the dispatches on other records that formally cover this one. */
  coveredBy: CoveringDispatchView[];
  currency: CurrencyFacts;
  refusal: string | null;
  command: string;
}

/** One row of the store's `brief_runs` table -- a session brief that happened. */
export interface BriefRun {
  id: string;
  seq: number;
  at: string;
  actor: string;
  sessionStart: boolean;
  lastEventSeq: number;
  lastShiftNoteSeq: number;
}

export interface BriefCatalogue {
  ok: boolean;
  /** Newest first, which is the order the calendar tool lists them in. */
  briefs: BriefRun[];
  currency: CurrencyFacts;
  problem: string | null;
}

export interface ShiftNoteView {
  id: string;
  seq: number;
  author: string;
  createdAt: string;
  text: string;
  /** A redacted note kept its row and lost its prose; say which, never both. */
  redactedAt: string | null;
  redactedWhy: string | null;
}

export interface EventView {
  id: string;
  seq: number;
  at: string;
  actor: string;
  command: string;
  recordId: string | null;
}

/**
 * One session brief, reconstructed from what the store actually holds.
 *
 * Deliberately *not* `kyo brief`'s output. `kyo brief` inserts a row in
 * `brief_runs` every time it runs -- it is a write command wearing a read
 * command's name -- and records mode has no write path
 * at all. What the store keeps of a past brief is the run itself and the ledger it
 * closed over, so that is what this carries, and nothing here reconstructs prose
 * the store never kept.
 */
/**
 * The newest shift note in the store, for the daily sheet's split view (KYO-122.c12).
 *
 * The daily sheet is where a person wants their own plan and the record side by side:
 * the controller's note for the next session beside their plan for it, so they can add
 * emergent issues and prepare from the note. So this is the controller's last
 * handoff, beside the human's own plan, and read-only: the note is the controller's
 * and this app has no write path to the store at all.
 *
 * `note: null` with `ok: true` is a real answer -- a store nobody has written a shift
 * note in yet -- and is deliberately not the same as `ok: false`, which is the store
 * failing to answer. The split view says something different for each.
 */
export interface LatestShiftNote {
  ok: boolean;
  note: ShiftNoteView | null;
  problem: string | null;
}

/**
 * One shift note as the Notes list draws it (KYO-248): the note, and whether it was
 * the HANDOFF -- the last note standing when a session-start brief ran, which is the
 * note `kyo brief` re-surfaces to the next session as "what your predecessor left
 * you" (KYO-94). `handoffFor` names that brief.
 */
export interface ShiftNoteRow extends ShiftNoteView {
  handoffFor: string | null;
}

/** Every shift note in the store, newest first, read straight out of the ledger. */
export interface ShiftNotesCatalogue {
  ok: boolean;
  notes: ShiftNoteRow[];
  problem: string | null;
}

export interface BriefDocument {
  ok: boolean;
  run: BriefRun | null;
  previousBriefId: string | null;
  /** The shift notes standing at this brief, verbatim from the store. */
  shiftNotes: ShiftNoteView[];
  /** The ledger entries this brief closed over, since the one before it. */
  events: EventView[];
  problem: string | null;
}

// ---------------------------------------------------------------- ideas mode
//
// KYO-114, and the first part of this protocol that describes a WRITE.
//
// Everything above is a read. Ideas mode is the app's first writable editor, and
// the boundary is drawn here rather than in the buttons a renderer happens to
// offer: the two surfaces below are the human's own -- the session scratchpad and
// the files under `ideas/` -- and there is no shape in this file that can name
// anything under `kyosei/`. Promotion, records and everything past that seam stay
// with the CLI (KYO-39), so no payload here carries a record id the app wrote.

/** Ideas mode's tool windows: the scratchpad, and `ideas/` as a file tree. */
export const IDEA_TOOLS = ["calendar", "folder"] as const;
export type IdeaTool = (typeof IDEA_TOOLS)[number];

/** The right rail: the open file's frontmatter, and its headings. */
export const IDEA_RIGHT_TOOLS = ["properties", "outline"] as const;
export type IdeaRightTool = (typeof IDEA_RIGHT_TOOLS)[number];

/**
 * The session scratchpad -- the rail's calendar tool.
 *
 * Deliberately NOT an idea file. It lives at `<per-project home>/scratchpad.md`
 * (KYO-56's `~/kyosei/<project>/`), outside `ideas/` and outside the store, so
 * `kyo promote` and `kyo sweep` cannot see it by construction rather than by a rule
 * somebody has to remember. `home` and `path` are carried so the pane can state
 * where it is writing: a surface that edits a file the reader cannot locate is a
 * surface they cannot check.
 */
export interface Scratchpad {
  ok: boolean;
  /** The file, absolute. Null only when the home could not be derived. */
  path: string | null;
  /** The per-project directory it lives in. */
  home: string | null;
  text: string;
  /**
   * The version `text` is (kyo serve's ETag), or null when no file existed yet. The tab
   * that read it keeps it and saves against it (KYO-575): a version is the TAB's, never
   * one shared by every editor in a process.
   */
  version: string | null;
  /** True when no file existed yet and this is the empty document offered instead. */
  fresh: boolean;
  problem: string | null;
}

/**
 * A scratchpad save: the editor's text, and the version it was edited from -- the text
 * and version this tab loaded or last saved (KYO-575). The write replaces THAT version;
 * if the file changed since, main merges three ways (`shared/merge3.ts`) and writes the
 * merge against the version there now. Never a version re-read at save time.
 */
export interface ScratchpadSave {
  text: string;
  base: string;
  version: string | null;
}

/**
 * What came of a save from a compose tab (KYO-575): a `WriteResult`, plus the version
 * the file is at now and what it holds -- which is the tab's text, or, after a refused
 * save was merged, the merge. The tab adopts both as its new base.
 */
export interface SaveResult extends WriteResult {
  /** The version (ETag) the write left the file at: what this tab's next save names. */
  version: string | null;
  /** The whole file as written: the next save's merge base. */
  base: string | null;
  /** What the editor shows of it: the scratchpad's whole text, an idea file's body. */
  body: string | null;
  /** An idea file's frontmatter as written (null for the scratchpad). */
  properties: IdeaProperty[] | null;
  /**
   * Not null when the file had changed since the tab's version and the save was merged:
   * `conflicts` overlaps were kept between marker lines, 0 for a clean merge.
   */
  merged: { conflicts: number } | null;
}

/**
 * What came of a write.
 *
 * Every write in this mode answers with one of these, and no renderer treats a
 * write as having happened until one arrives. An editor that showed the human's
 * text as saved because it sent it is an editor that loses work silently.
 */
export interface WriteResult {
  ok: boolean;
  /** The file actually written, absolute -- the claim the reader can check. */
  path: string | null;
  problem: string | null;
}

/** One file under `ideas/`, as the folder tool lists it. */
export interface IdeaFileEntry {
  /** Path relative to `ideas/`, `/`-separated. The identity the app opens by. */
  path: string;
  /** The file's own name, extension included. */
  name: string;
  /** The folder it sits in, relative to `ideas/`; the empty string at the top. */
  folder: string;
  /** Markdown opens in the editor; anything else is listed and not opened. */
  openable: boolean;
  /** The file's own stamps, ISO, for the list's sort (KYO-250); null when unreadable. */
  createdAt: string | null;
  modifiedAt: string | null;
  /** Record ids `kyo promote` stamped into this file's frontmatter, if any. */
  promoted: string[];
  /** The tags this file carries. */
  tags: string[];
}

/**
 * `ideas/` as a file view.
 *
 * `folders` carries every directory, including the ones holding no markdown, so an
 * empty folder renders as an empty folder rather than vanishing -- and so the Idea
 * button's destination list is the folders that exist rather than the folders that
 * happen to have something in them.
 */
export interface IdeaTree {
  ok: boolean;
  /** The `ideas/` directory itself, absolute. */
  root: string | null;
  /** Every folder, relative and `/`-separated, sorted. The root is the empty string. */
  folders: string[];
  files: IdeaFileEntry[];
  /** Where these rows came from, stated rather than left to be inferred. */
  from: string;
  refusal: string | null;
}

/** One heading in an idea file, as the outline tool draws it. */
export interface IdeaSection {
  /** The heading text, without its `#`s. */
  heading: string;
  level: number;
  /** Its line index in the body, so the outline can reach it in the editor. */
  line: number;
  /**
   * The record this section became, when it is a promoted stub.
   *
   * A stub, not a lock: the section is still ordinary text
   * in an ordinary file and the human may still edit it. This says what happened to
   * it, which is the whole of what the app knows -- the record itself is past the
   * seam.
   */
  promotedTo: string | null;
}

/**
 * One frontmatter property, as the right rail renders and edits it.
 *
 * `list` is kept because a list in the file must stay a list when written back: a
 * property the human never touched must survive their edit of a different one, and
 * silently rewriting `promoted:` from a list to a scalar would corrupt a stamp the
 * CLI wrote.
 */
export interface IdeaProperty {
  key: string;
  values: string[];
  list: boolean;
  /**
   * True for the stamps `kyo promote` writes (`promoted`, `promoted_at`).
   *
   * Shown and never editable here. They are the store's own record of what
   * happened, and a properties panel that let a human retype them would be an app
   * surface asserting a promotion that never occurred -- exactly the write past the
   * seam this mode does not have (KYO-39).
   */
  readOnly: boolean;
}

/** One idea file, opened. */
export interface IdeaDocument {
  ok: boolean;
  /** Relative to `ideas/`, as the tree named it. */
  path: string;
  absolutePath: string | null;
  /**
   * Everything after the frontmatter -- the only part the editor pane shows.
   *
   * The frontmatter is hidden there and lives in the right rail's properties tool
   * instead (KYO-114). It remains ordinary frontmatter in the `.md` file: nothing
   * here moves it anywhere, and a file this app has never opened is unchanged by
   * the fact that it renders this way.
   */
  body: string;
  properties: IdeaProperty[];
  sections: IdeaSection[];
  /**
   * The file's own stamps, ISO, for the editor's metadata line (KYO-257); null when the
   * stat failed. `createdAt` is the file system's birth time: a file that arrived by
   * clone or copy carries when it landed on this disk, not when it was written.
   */
  createdAt: string | null;
  modifiedAt: string | null;
  /** The whole file as read, frontmatter and all: the tab's merge base (KYO-575). */
  base: string;
  /** The version `base` is (kyo serve's ETag): what this tab's save names. */
  version: string | null;
  problem: string | null;
}

/**
 * What the editor writes back: the two halves it holds, recomposed by main against the
 * file the tab loaded (`base`, at `version`) -- not a copy re-read at save time, which
 * would make the version check cover only the milliseconds between that read and the
 * write (KYO-575).
 */
export interface IdeaEdit {
  path: string;
  body: string;
  properties: IdeaProperty[];
  base: string;
  version: string | null;
}

/** One `[[` completion: a record that exists in the store, right now. */
export interface RecordCompletion {
  id: string;
  title: string;
  kind: string;
  status: string;
}

/**
 * What `[[` and `#` may offer (KYO-55).
 *
 * The point of carrying these as a closed set rather than letting the editor
 * free-type is that a dangling reference is then not typeable by accident: drift is
 * prevented at the input rather than flagged by a sweep afterwards. `records` comes
 * over the read seams records mode already uses; `tags` is whatever exists across
 * `ideas/` today, because there is no tag registry in v1 -- which the UI says in as
 * many words rather than implying one.
 */
export interface Completions {
  ok: boolean;
  records: RecordCompletion[];
  tags: string[];
  /** The command the records came out of, for the surface to state. */
  recordsFrom: string;
  /** How the tag set was arrived at, for the same reason. */
  tagsFrom: string;
  /** The store's currency: these are store facts on a surface (KYO-74). */
  currency: CurrencyFacts;
  problem: string | null;
}

// ============================================================ docs mode (KYO-139)
//
// PURE VIEW-ONLY, past the runtime seam -- KYO-136 decision 6, and the reason this
// section declares no counterpart to `IdeaEdit` and no `save*` method below. Docs are
// updated from ideas by a controller session that reads and modifies them: the write
// path exists and it runs through a human's controller session, not through this app.
// So there is no second writer to a docs file, which is the problem ideas mode had to
// solve, and there is nothing here to solve it with -- deliberately. A `saveDoc` added
// to `KyoseiBridge` would be the whole decision reversed in one line.

/**
 * How current a document is with the code it governs -- the CLI's four states, plus
 * the one this app can be in about them.
 *
 * The four are `docs_check.STALENESS_STATES` and they are carried apart, `cannot-tell`
 * ESPECIALLY: `defaults/bases.yml` states why in as many words -- "a banner that
 * painted that case current would report a clean docs tree on no evidence". Three of
 * the four are not-current for different reasons a reader would act on differently, so
 * a renderer that collapsed any pair of them would be discarding the distinction this
 * field exists to carry.
 *
 * `unrecognised` is this app's, on the exact precedent of `CurrencyFacts["state"]`: a
 * state word from a CLI newer than this build is shown as unrecognised rather than
 * bucketed into whichever known state looks closest -- and the closest-looking one is
 * always `current`, which is the failure above arriving by a different route.
 */
export type DocStaleness = "current" | "stale" | "ungoverned" | "cannot-tell" | "unrecognised";

/** The four the CLI produces, in the order a legend lists them. */
export const DOC_STALENESS_STATES: readonly DocStaleness[] = [
  "current",
  "stale",
  "cannot-tell",
  "ungoverned",
];

/** Whether a state is a decided answer, mirroring `docs_check.staleness_answered`. */
export function stalenessAnswered(state: DocStaleness): boolean {
  return state === "current" || state === "stale";
}

/** One heading in a document, with the Layer 3 metadata its body declares. */
export interface DocSection {
  /** The heading text without its `#`s; `(preamble)` for the content before the first. */
  heading: string;
  /** The number of `#` characters, or 0 for the preamble. */
  level: number;
  /** The paths this section claims to speak for. */
  governs: string[];
  /** Which of this section's own `governs:` targets no longer resolve. */
  governsBroken: string[];
  status: string | null;
  amendedBy: string[];
}

/** One of this project's Layer 3 documents, as the docs query source rows it. */
export interface DocEntry {
  /** Root-relative and `/`-separated. The identity the app opens by. */
  path: string;
  /** The file's own name, extension included. */
  name: string;
  /** The folder it sits in, relative to the root; the empty string at the top. */
  folder: string;
  /** The file's first heading, or null for a file that has none. */
  title: string | null;
  sectionCount: number;
  /** Every `governs:` target in the file, in declaration order. */
  governs: string[];
  /** Which of them no longer resolve -- `kyo docs check`'s own verdict, not a guess. */
  governsBroken: string[];
  amended: boolean;
  staleness: DocStaleness;
  /** The file's own modification stamp, ISO, for the list's sort (KYO-250). */
  modifiedAt: string | null;
  /** Why that state, in the CLI's own words. Filled in every state, `current` included. */
  stalenessSays: string;
  sections: DocSection[];
  /** Open docs obligations naming this document (KYO-137). */
  obligations: DocObligation[];
}

/**
 * A document section a dispatch put a question to and nobody has answered (KYO-137).
 *
 * Read-only here on the same terms as everything else in this mode: `kyo docs settle`
 * takes a controller's asserted verdict and a method, and neither is a thing a viewer
 * can supply. Shown because it is the thing a reader opens this mode to check.
 */
export interface DocObligation {
  id: string;
  recordId: string;
  dispatchId: string | null;
  /** The section within the document, as the obligation named it. */
  section: string;
  cause: string;
  detail: string;
}

/**
 * Which of three things is true of this project's documents.
 *
 * `no-documents` is KYO-129's state and it is carried all the way to the renderer
 * rather than being inferred from an empty list, because an empty list is also what
 * `unavailable` would produce and the two mean opposite things (KYO-139.c6). Law 6:
 * an absence states itself, and "this project declares no documents" is a different
 * absence from "the read failed".
 */
export type DocsState = "documents" | "no-documents" | "unavailable";

/** A `governs:` target that does not resolve, as `kyo docs check` reports it. */
export interface GovernsProblem {
  docPath: string;
  section: string;
  target: string;
  reason: string;
}

/** This project's documents as a tree, with the governance layer over them. */
export interface DocTree {
  ok: boolean;
  state: DocsState;
  /** The code root the documents live under, absolute. */
  root: string | null;
  documents: DocEntry[];
  /** Every folder holding a document, relative and sorted. The root is the empty string. */
  folders: string[];
  /** The globs that were scanned -- what `documents.yml` declares, or the default. */
  scannedPaths: string[];
  /** Whether the project declared them, or this is the build's default (KYO-129). */
  pathsDeclared: boolean;
  /** The CLI's own sentence about what it scanned and found. */
  says: string;
  /** The command these rows came out of, stated rather than left to be inferred. */
  from: string;
  /** Every unresolved `governs:` target across the tree. */
  problems: GovernsProblem[];
  refusal: string | null;
  /** The store's currency: these are store facts on a surface (KYO-74). */
  currency: CurrencyFacts;
}

/** One document, opened. Text only -- there is no edit to send back. */
export interface DocDocument {
  ok: boolean;
  /** Root-relative, as the tree named it. */
  path: string;
  absolutePath: string | null;
  /** The whole file. A docs file has no frontmatter fence to strip by construction. */
  text: string;
  problem: string | null;
}

// ============================================================ the v3 read model (KYO-231)
//
// Everything the v3 tabs render, produced in the main process so that no tab
// re-derives anything. Four queries and one rule they all obey: a shape here is
// either the CLI's own envelope re-typed, or a projection of one table -- never a
// computation this app performs on rows it could have asked for.
//
// Where each one gets its facts, and why it is not the other way round:
//
//   the record document   `kyo show <id> --json`. It already carries every child
//                         object AND the obligation collapse (KYO-219), so reading it
//                         is REUSE of `docs_check.collapse_obligations` rather than a
//                         port of it -- there is no second implementation to drift.
//   awaiting-human        direct SQLite. No read-only command answers "every open
//                         gate of these types across every record status", and the
//                         one that comes closest (`kyo base run`) needs a base
//                         declared in a store this app must not write to.
//   activity              direct SQLite for the store half, `gitfacts.ts` for the git
//                         half. `kyo worktrees --json` was the alternative and is
//                         measured wrong for a poll: see `gitfacts.ts`.
//   the brief             direct SQLite, because `kyo brief` WRITES a `brief_runs`
//                         row on every run (`KYO_WRITING_COMMANDS`) -- the one command
//                         whose subject is briefs is the one this app must not call.

/** One note on a record, with its supersession. */
export interface NoteView {
  id: string;
  seq: number;
  text: string;
  createdAt: string;
  supersededBy: string | null;
  supersededAt: string | null;
  /** Set when the prose was redacted out of the store and the ledgers. */
  redactedAt: string | null;
  redactedWhy: string | null;
}

/** One evidence artifact, stored verbatim, with its redaction marker. */
export interface EvidenceView {
  id: string;
  seq: number;
  sha256: string;
  author: string;
  createdAt: string;
  /** Which criterion it supports, when it supports one. */
  supportsId: string | null;
  text: string;
  redactedAt: string | null;
  redactedWhy: string | null;
  /**
   * KYO-587: the lines `kyo show <evidence-id>` prints before a recx search artifact
   * (Kyosei's fixed line, then recx's notice), read from `kyo show <record> --json`'s
   * `untrusted_notice`. `null` for every other artifact, which renders as it always has.
   * The CLI holds the one recognition rule; the app only draws its answer.
   */
  untrustedNotice: string[] | null;
  /** The recx artifact's text with control characters escaped, as `kyo show` prints it. */
  untrustedText: string | null;
}

/** One sweep flag, and whether anybody has answered it. */
export interface FlagView {
  id: string;
  seq: number;
  flagType: string;
  detail: string;
  createdAt: string;
  resolvedAt: string | null;
  resolvedWhy: string | null;
  otherRecordId: string | null;
  otherCriterionId: string | null;
}

/** One `doc-refs` entry: a document this record points at. */
export interface DocRefView {
  id: string;
  seq: number;
  docPath: string;
  section: string | null;
  note: string | null;
}

/** A gate with the claims that stand beside it (KYO-88). */
export interface GateDocument extends GateView {
  /** The allocation counter in its own id -- which is id order (law 4). */
  seq: number;
  /** The claims made about this record while this gate was open, newest first. */
  claims: ClaimView[];
}

/** A plan's member, as the plan's own page lists it. */
export interface MemberView {
  id: string;
  kind: string;
  status: string;
  standing: string;
  title: string;
}

/**
 * The obligation collapse, exactly as `kyo show --json` carries it.
 *
 * Re-typed and NOT re-derived: `docs_check.collapse_obligations` computes this, the
 * CLI puts it in the envelope under `docs_obligation_groups`, and this app reads it.
 * KYO-219.c3 built that function so the desktop viewer could read the same grouping
 * rather than deriving a second one, and this is the consumer it was built for.
 */
export interface ObligationDetail {
  detail: string;
  count: number;
  obligationIds: string[];
  docPaths: string[];
  sections: string[];
}

export interface ObligationGroup {
  state: string;
  method: string | null;
  author: string | null;
  settledAt: string | null;
  settledWhy: string | null;
  count: number;
  obligationIds: string[];
  causes: string[];
  dispatchIds: string[];
  details: ObligationDetail[];
}

export interface ObligationCollapse {
  total: number;
  open: number;
  countsByState: { state: string; count: number }[];
  groups: ObligationGroup[];
}

/** Every child object of one record, in id order, with the description and links. */
export interface RecordDocument {
  ok: boolean;
  id: string;
  kind: string;
  title: string;
  /** The stored status column. */
  status: string;
  /**
   * The role word the CLI prints, which for a knowledge-kind record is `current` or
   * `void` rather than a work status (KYO-121, KYO-231.c1). Carried beside `status`
   * rather than instead of it, because the two genuinely differ -- a knowledge record
   * filed `open` reads `current` -- and a page showing only one of them is either
   * hiding what the store holds or printing a word its kind has no business having.
   */
  standing: string;
  scope: string | null;
  plan: string | null;
  createdAt: string;
  updatedAt: string;
  description: string;
  wontDoReason: string | null;
  criteria: CriterionView[];
  gates: GateDocument[];
  claims: ClaimView[];
  notes: NoteView[];
  evidence: EvidenceView[];
  flags: FlagView[];
  dispatches: DispatchView[];
  /**
   * KYO-291: the dispatches on OTHER records that formally cover this one.
   *
   * Beside `dispatches` and never inside it, for the reason the CLI keeps the two
   * counts apart: "who was sent to work on this record" and "who spoke for it" are
   * different questions, and a page that merged them would report one dispatch as two.
   * Empty for the overwhelming majority of records, and the page draws no section at
   * all when it is -- see `renderDocument`.
   */
  coveredBy: CoveringDispatchView[];
  touches: TouchView[];
  links: LinkView[];
  docRefs: DocRefView[];
  obligations: ObligationCollapse;
  /** A plan's members; empty for everything else. */
  members: MemberView[];
  /** This is a surface painting store facts, so it owes the reader the verdict (KYO-74). */
  currency: CurrencyFacts;
  refusal: string | null;
  command: string;
}

/** One open gate that is waiting on a person, with everything it is waiting with. */
export interface AwaitingGate {
  recordId: string;
  recordTitle: string;
  recordKind: string;
  recordStatus: string;
  gate: GateDocument;
}

/**
 * Every open gate waiting on a human, across every record status.
 *
 * "Across every record status" is the point and is why this is not a base run: a gate
 * on a `working` record and a gate on an `open` one are the same claim on the human's
 * attention, and a view that only looked at open work would hide half of them.
 */
export interface AwaitingHuman {
  ok: boolean;
  gates: AwaitingGate[];
  /** What the rail badge shows. `gates.length`, carried so nothing counts twice. */
  count: number;
  /** The gate types this query is about, so a reader can see what it looked for. */
  types: string[];
  problem: string | null;
}

/** One row of the records list: enough to draw it, and no more. */
export interface RecordsListRow {
  id: string;
  kind: string;
  title: string;
  status: string;
  scope: string | null;
  /** The store's own stamps, for the list's sort (KYO-250); empty when a base omits them. */
  createdAt: string;
  updatedAt: string;
  /** The role word, when the row came through the engine; null for a direct read. */
  standing: string | null;
  /**
   * The class `kyo next --json` puts this record in (KYO-290).
   *
   * Stamped in the main process from `readBoardClasses`, so the records browser's
   * readiness filter reads a class the CLI decided rather than deriving a second
   * answer from status, criteria and gates. `none` when the board does not list it --
   * and also, unavoidably, when the board could not be read at all, which is what
   * `boardProblem` is for.
   */
  boardClass: BoardClass;
}

export interface RecordsList {
  ok: boolean;
  rows: RecordsListRow[];
  /** The search that produced them, echoed so a surface can say what it filtered by. */
  query: string;
  /** The saved base that produced them, or null for every record in the store. */
  base: string | null;
  /** The saved bases `kyosei/bases.yml` declares, for the picker. */
  bases: BaseDefinition[];
  currency: CurrencyFacts;
  problem: string | null;
  /**
   * Why `kyo next` could not be read, or null when it was (KYO-290.c2).
   *
   * Its own field beside `problem`: the list itself is fine in this state -- every
   * row is there, searched and sorted -- and only the readiness filter is blind, so
   * failing the whole list would be a worse answer than saying which part is missing.
   */
  boardProblem: string | null;
}

/** Why git could not answer about a worktree, or that it did. */
export const GIT_FACTS_OUTCOMES = [
  "read",
  "no-worktree-declared",
  "directory-gone",
  "not-a-git-worktree",
] as const;
export type GitFactsOutcome = (typeof GIT_FACTS_OUTCOMES)[number];

/** What git says about one dispatch's worktree. A missing directory is a fact. */
export interface GitFacts {
  worktree: string | null;
  present: boolean;
  /** Null when git could not be asked, which is not the same as "clean". */
  dirty: boolean | null;
  dirtyPaths: string[];
  branch: string | null;
  /** Null when the base sha is not in this repository -- never 0, which means "none". */
  commitsPastBase: number | null;
  latestCommitSha: string | null;
  latestCommitMessage: string | null;
  /**
   * Lines added and removed past the base, from `git diff --numstat` (KYO-251); null
   * when there is no base in the repository to diff against, or no worktree to ask.
   */
  linesAdded: number | null;
  linesRemoved: number | null;
  /** True, false, or null for "there is no branch here to compare against". */
  mergedInto: boolean | null;
  reportFilePresent: boolean;
  outcome: GitFactsOutcome;
  why: string;
}

/**
 * What one dispatch's worker cost, exactly as `kyo` answered (KYO-284).
 *
 * THE APP DERIVES NOTHING HERE, which is a design decision and the whole of
 * KYO-284.c4. The only thing that knows which transcript belongs
 * to which dispatch is the CLI's stop-audit resolution (KYO-128/154), and a second
 * answer to that question computed in this process would be a second guess at the
 * harness's private file layout. So the summing lives in `kyo` and this is its
 * answer, renamed into this codebase's casing and not otherwise touched.
 *
 * `known` is the field to branch on, never a zero: `outcome` is the resolution's
 * own word (`matched` when the counts are real, `unresolved` /
 * `no-transcript-recorded` / `not-summed-this-run` / `ledger-unreadable`
 * otherwise), and `why` is the sentence to show a reader who asks.
 */
export interface WorkerTokens {
  outcome: string;
  known: boolean;
  why: string;
  /** The model the TRANSCRIPT names, which is not `ActivityRow.workerModel` -- that
   * is the model the controller declared, and nothing enforces the two agree. */
  model: string | null;
  transcripts: number;
  messages: number;
  inputTokens: number;
  cacheCreationInputTokens: number;
  cacheReadInputTokens: number;
  outputTokens: number;
  /** The size of the worker's LATEST turn (KYO-292.c2): all four counts of the
   * last assistant message in the most recent transcript matched to this dispatch,
   * cache reads included. Not one of the sums above -- it is how big the
   * conversation had become by the last thing the worker said, and it is the figure
   * claude.ai's agent card shows, measured twice against a live transcript. `kyo`'s
   * number, so one place decides what "tokens" means on a row -- the one that reads
   * the transcript. It runs about a percent under the card, because the last
   * message's `output_tokens` is recorded mid-stream; the row's tip says so. */
  latestTurnTokens: number;
  /** All four counts, cache reads included. `kyo`'s sum, not one made here. */
  totalTokens: number;
}

/**
 * When one dispatch finished, exactly as `kyo` answered (KYO-286).
 *
 * `dispatches.closed_at` is set by `kyo dispatch close` and nothing else, and a
 * dispatch that simply reported is usually never closed -- so a finished row had no
 * end time at all and the list fell back to "now minus created", which grew every
 * time it was painted. The end is the CLI's to decide, on the same terms as
 * `WorkerTokens`: only `kyo` holds the stop-audit ledger that knows when a worker
 * actually stopped, and `source` names which of its three answers this one is
 * (`worker-stop`, `report-ingest`, `closed-at`).
 *
 * `at` is null while the dispatch is still running -- its clock is genuinely still
 * going -- and null when nothing in the store recorded an end. `why` is a sentence
 * on both branches, so the row's tip carries the CLI's account rather than one
 * written here.
 */
export interface DispatchEnd {
  at: string | null;
  source: string | null;
  why: string;
}

/** The classes the brief derives, as the brief derives them. */
export const ACTIVITY_CLASSES = [
  "running",
  "finished-but-not-moved",
  "gated",
  "terminated-no-report",
] as const;
export type ActivityClass = (typeof ACTIVITY_CLASSES)[number];

/** One dispatch, as the activity tab shows it: the store's half and git's. */
export interface ActivityRow {
  dispatchId: string;
  recordId: string;
  recordTitle: string;
  recordKind: string;
  recordStatus: string;
  recordStanding: string;
  state: string;
  workerModel: string | null;
  workedFromSha: string;
  branch: string | null;
  createdAt: string;
  closedAt: string | null;
  terminatedWhy: string | null;
  /** The report the dispatch's own `report_id` names, or null (KYO-90). */
  reportId: string | null;
  /** How many verdicts that report carried, by verdict word. */
  verdictCounts: { verdict: string; count: number }[];
  /** Stops recorded against this dispatch that nothing could audit (KYO-163). */
  unauditedStops: number;
  /** What this dispatch's worker cost, per `kyo` (KYO-284). Null when `kyo
   * worktrees --json` could not be read at all, or carried no row for this
   * dispatch -- which is a different fact from `known: false`, and the row says
   * "tokens unknown" for both rather than inventing a number for either. */
  workerTokens: WorkerTokens | null;
  /** When this dispatch finished and where that moment came from, per `kyo`
   * (KYO-286). Null on the same terms as `workerTokens`: `kyo worktrees --json`
   * could not be read, or lists no row for this dispatch -- which is what a
   * dispatch that declared no worktree of its own looks like. The row then falls
   * back to `closedAt`, which is the behaviour this field exists to improve on
   * rather than replace. */
  finished: DispatchEnd | null;
  /** Open gates on the record this dispatch is for. */
  openGates: number;
  /** The records this dispatch also formally covered, with their titles (KYO-251). */
  covers: { id: string; title: string }[];
  git: GitFacts;
  /** Every class this row is in. A row can be in more than one. */
  classes: ActivityClass[];
}

export interface ActivityView {
  ok: boolean;
  rows: ActivityRow[];
  /** How many of `rows` are running -- what the poll is for. */
  running: number;
  /** How many finished dispatches were asked for beyond the running ones. */
  finishedShown: number;
  /** When the git half of these rows was read. */
  readAt: string;
  problem: string | null;
}

/** One section of the latest brief, with every id it names intact. */
export interface BriefSection {
  /** A stable key the renderer can switch on. */
  key: string;
  title: string;
  /** The rows, each carrying its own id so the renderer can link it. */
  items: BriefItem[];
}

export interface BriefItem {
  id: string;
  /** What this row is: a shift note, a ledger event, a dispatch. */
  kind: string;
  /** The record it is about, when it is about one. */
  recordId: string | null;
  at: string;
  actor: string;
  text: string;
}

export interface BriefDocumentView {
  ok: boolean;
  /** The brief run itself, or null when the store holds none. */
  run: BriefRun | null;
  previousBriefId: string | null;
  sections: BriefSection[];
  currency: CurrencyFacts;
  problem: string | null;
}

// ------------------------------------------------- `kyosei://` deep links (KYO-230)
//
// The GRAMMAR lives in `main/links.ts` -- the id shapes, the URL parsing, the resolver
// -- and the shapes it produces live here, because they cross the boundary. A renderer
// never parses an id: it is handed a target that has already been resolved, so the
// question "what kind of id is this" has exactly one answer in this codebase and it is
// in the main process.

/** What a resolved link opens: a page the app has, not a row in a table. */
export const TARGET_KINDS = ["record", "shift-note", "brief"] as const;
export type TargetKind = (typeof TARGET_KINDS)[number];

/**
 * Which column an open request lands in.
 *
 * `active` is the column the reader last worked in and is the default for every link.
 * The other two exist because a link can ask -- `?column=other` in the URL -- and the
 * v3 shell is columns of tabs (KYO-227), so "open this beside what I am reading" is a
 * thing a controller's link will want to say. The renderer decides what each word
 * means for its own layout; the main process only carries the ask.
 */
// KYO-282: "left" and "right" name a column outright; "other" stays for the links and
// layouts that already say it, and still means the column that is not the active one.
export const OPEN_COLUMNS = ["active", "other", "new", "left", "right"] as const;
export type OpenColumn = (typeof OPEN_COLUMNS)[number];

export function isOpenColumn(value: unknown): value is OpenColumn {
  return typeof value === "string" && (OPEN_COLUMNS as readonly string[]).includes(value);
}

/** Every way a link can arrive. Named, because a reader may want to know which. */
export const OPEN_SOURCES = ["launch-argument", "open-url", "claude-view", "in-app"] as const;
export type OpenSource = (typeof OPEN_SOURCES)[number];

/** A resolved link: which page, and what on it to highlight. */
export interface ResolvedTarget {
  kind: TargetKind;
  /** The id of that page's subject: a record id, a shift note id, a brief id. */
  id: string;
  /** The child id to scroll to and highlight, or null when the link names a page. */
  anchor: string | null;
  /** What that child IS, so the renderer finds its section without re-parsing the id. */
  anchorKind: string | null;
  /** The id as it was written, for the "you were sent here" strip. */
  asWritten: string;
}

/** The typed request a renderer receives: open this, highlight that, over there. */
export interface OpenRequest {
  target: ResolvedTarget;
  column: OpenColumn;
  /** The project root the target lives in -- the one the app resolved the link to. */
  projectRoot: string;
  /**
   * The kyo serve that project is read through: null for this machine's own
   * (KYO-463). A project is (server, name), so a link is delivered to a window on this
   * connection and root, never to one on another server that shares the root.
   */
  connection: RemoteFacts | null;
  /**
   * When more than one connection serves the link's project, which one it was opened
   * through and why (KYO-463.c3); null when there was no choice to make.
   */
  choice: string | null;
  /** The link as it arrived, so the strip can quote it. */
  url: string;
  source: OpenSource;
  /** The tab it was followed from, when it was followed from one (KYO-247). */
  origin?: LinkOrigin;
}

/** Why a link was not followed. Every one of these carries prose a human can act on. */
export const LINK_REFUSALS = [
  "not-our-scheme",
  "malformed",
  "unknown-project",
  "not-openable",
  "unknown-shape",
] as const;
export type LinkRefusal = (typeof LINK_REFUSALS)[number];

/**
 * What the main process sends a renderer when a link arrives.
 *
 * A refusal crosses the boundary too, and that is the decision worth stating: a
 * `kyosei://` link was unambiguously meant for this app, so "nothing happened" is the
 * one response it may not get (law 6). The renderer shows the sentence.
 */
export type LinkDelivery =
  | { ok: true; request: OpenRequest; refusal: null }
  | { ok: false; request: null; outcome: LinkRefusal; refusal: string };

// ====================================== layout memory per display set (KYO-232.c3)
//
// A laptop's own screen, a single external monitor and a pair of ultrawides each
// restore their own layout (KYO-227). So the thing a layout is saved AGAINST is the set
// of attached screens, and the key below is derived from their identities -- not from a
// window size, which a human changes for reasons that have nothing to do with which
// monitors are plugged in, and not from a monitor count, which cannot tell one external
// monitor from another.
//
// The key is computed in the MAIN process (`displaySignature` in `main/main.ts`),
// because `screen` is a main-process module and the renderer has no way to ask. The
// renderer is told the key it is saving under, so a surface can state it.

/** One tab in a saved layout: its identity, and nothing about its internal state. */
export interface TabSnapshot {
  kind: string;
  arg: string;
  /**
   * Where the tab has been (KYO-247), oldest first; `kind`/`arg` are `history[at]`.
   * Absent in a layout saved before tabs had a history.
   */
  history?: TabSnapshot[];
  at?: number;
}

/**
 * Which tab a link was followed FROM, and how (KYO-247).
 *
 * Carried on the request and echoed on the delivery, because the main process owns
 * the grammar and the round trip, but only the renderer knows that a link clicked
 * inside a tab means "show it here": the tab navigates and keeps its history, unless
 * the click asked for a new tab (Cmd-click, or the link's own menu).
 */
export interface LinkOrigin {
  tabId: string;
  newTab: boolean;
}

/** One column of tabs, as it was left. */
export interface ColumnSnapshot {
  /** Share of the columns area, in percent; the shell normalises these to 100. */
  widthPercent: number;
  tabs: TabSnapshot[];
  /** Index into `tabs` of the one that was showing, or -1 for an empty column. */
  activeTab: number;
}

/** A whole window layout: what KYO-232.c3 names -- columns, widths, tabs, sidebar. */
export interface LayoutSnapshot {
  columns: ColumnSnapshot[];
  /** Index into `columns` of the active one -- where a click opens. */
  activeColumn: number;
  sidebar: {
    /** Which rail item's list is showing, or null when the sidebar is collapsed. */
    item: string | null;
    /** The sidebar's width in CSS pixels. */
    width: number;
    /**
     * Each list's own sort, filters and collapsed folders (KYO-250), by list name --
     * remembered with the layout so a reader's arrangement of a list survives a
     * restart the way the columns do. Absent in a layout saved before.
     */
    lists?: Record<string, ListState>;
  };
}

/** How one sidebar list is sorted and filtered (KYO-250). */
export interface ListState {
  sort: { field: string; descending: boolean };
  /** Filter name -> the values kept; an absent or empty entry filters nothing. */
  filters: Record<string, string[]>;
  /** Folders folded shut, for the lists that are trees. */
  collapsed: string[];
}

/** What the renderer is told when it asks which layout applies now. */
export interface LayoutState {
  /** The key the layout is stored under: the attached displays' identities. */
  signature: string;
  /**
   * The other half of that key: the project this layout belongs to (KYO-249 made
   * layouts per project; KYO-334 tells the renderer which one). A window that knows
   * which project and which desk its arrangement is FOR can say so when it saves,
   * instead of leaving the main process to infer it from where the window has got to
   * by the time the save lands -- which, across a project switch, is the NEXT project.
   */
  root: string;
  /** Every display in the current configuration, for a surface that wants to say. */
  displays: { id: number; bounds: PaneBounds }[];
  /** The layout saved for this configuration, or null if it has not been seen. */
  layout: LayoutSnapshot | null;
}

/**
 * One save of a window's arrangement, stamped with what it is an arrangement OF.
 *
 * KYO-334: the shell debounces its saves by 250ms, and both the project switch and a
 * screen change move the key a save would be filed under. A save still inside that
 * window used to be written wherever the window had got to -- the new project's key,
 * the new desk's key -- so tabs the switch deliberately closed came back, and the new
 * project's remembered arrangement was overwritten with the old one's. A save now
 * carries its own key, and the main process REFUSES one that no longer matches the
 * window rather than filing it under whatever is current.
 */
export interface LayoutSave {
  /** The project this arrangement was made in. */
  root: string;
  /** The display configuration it was made on. */
  signature: string;
  /** The arrangement itself. */
  layout: LayoutSnapshot;
}

export const CHANNEL = {
  /** renderer -> main, invoke: "who am I, and what is open?" */
  hello: "kyosei:hello",
  /** renderer -> main, invoke: open another main window, on a named project or the default. */
  openWindow: "kyosei:open-window",
  /** renderer -> main, invoke: the native picker, then a window on the pick (KYO-249). */
  windowChoose: "kyosei:window-choose",
  /** renderer -> main, invoke: focus an already-open main window. */
  focusWindow: "kyosei:focus-window",
  /** renderer -> main, invoke: pop this pane out into its own window. */
  popOut: "kyosei:pop-out",
  /** main -> renderer, send: a menu command for the focused window's workspace. */
  command: "kyosei:command",
  /** renderer -> main, invoke: a tab left its strip; open a window around it. */
  tabDragStart: "kyosei:tab-drag-start",
  /** renderer -> main, send: the pointer moved; move the window following it. */
  tabDragMove: "kyosei:tab-drag-move",
  /** renderer -> main, invoke: the pointer was released; attach or leave be. */
  tabDragEnd: "kyosei:tab-drag-end",
  /** main -> renderer, send: a dragged window is over your strip (or has left it). */
  tabDragHover: "kyosei:tab-drag-hover",
  /** main -> renderer, send: take this tab into your workspace. */
  tabReceive: "kyosei:tab-receive",

  // The claude.ai pane (KYO-186). A `WebContentsView` and not an `<iframe>`:
  // claude.ai sends `x-frame-options: SAMEORIGIN`, so a frame is refused, while a
  // `WebContentsView` is separate top-level contents that the header does not apply
  // to. That view lives in the MAIN process -- the renderer cannot make one, and
  // deliberately: it carries a persistent logged-in session, and the sandboxed
  // renderer has no capability to reach it beyond the three messages below.
  /** renderer -> main, invoke: show the claude.ai view at these bounds. */
  claudeShow: "kyosei:claude-show",
  /** renderer -> main, send: the pane moved or resized; follow it. */
  claudeBounds: "kyosei:claude-bounds",
  /** renderer -> main, invoke: take the view off this window (hidden, or popped). */
  claudeHide: "kyosei:claude-hide",
  /** renderer -> main, invoke: resolve again and navigate there. */
  claudeReload: "kyosei:claude-reload",
  /** renderer -> main, invoke: a still of the view as it is now, to show while it steps aside. */
  claudeSnapshot: "kyosei:claude-snapshot",
  /** renderer -> main, invoke: put a stepped-aside view back, at these bounds, without re-resolving. */
  claudeUncover: "kyosei:claude-uncover",
  /**
   * renderer -> main, invoke: is this project's controller session running (KYO-383):
   * `kyo controller list` on the machine that holds the project, over SSH for a dev
   * box's (KYO-460).
   */
  controllerStatus: "kyosei:controller-status",
  /**
   * renderer -> main, invoke: start this project's controller session with `kyo
   * controller start <root>` there (KYO-460). Carries nothing: the command is that
   * machine's `controller_command`, never the caller's (KYO-383.c6).
   */
  controllerStart: "kyosei:controller-start",
  /**
   * renderer -> main, invoke: change the command Start runs. Refused since KYO-460: the
   * command is `controller_command` in that machine's `$KYOSEI_HOME/settings.yml`, the
   * CLI's own setting, and the app keeps none. Kept so the refusal says where it is.
   */
  controllerCommand: "kyosei:controller-command",
  /**
   * claude view's preload -> main, send: the reader clicked an href-less anchor, and
   * this is its text (KYO-321, KYO-263.c5).
   *
   * The one channel that carries anything out of the claude.ai page, and the only one
   * this app's OTHER preload does not have: `preload.ts` exposes the shell's bridge to
   * a renderer it wrote, while `claude-preload.ts` exposes nothing at all to the page
   * it runs in and sends this. One way, no reply, and the payload is a string a remote
   * page chose -- `decideClaudeClick` in `links.ts` is where it stops being trusted,
   * starting with whether the sender is a claude view of this app.
   */
  claudeTextClick: "kyosei:claude-text-click",
  /**
   * main -> renderer, send: this native window stopped being the key window. The DOM's
   * own `blur` fires only when THIS page had focus, and it often does not -- focus can
   * sit in the claude view -- so a menu that closes on "the app lost focus" hears it
   * from here (KYO-257.c1).
   */
  windowBlur: "kyosei:window-blur",
  /**
   * renderer -> main, send: make this window the key window of the active app. A menu
   * opened by a right-click on a window whose app was not active leaves that window
   * unfocused -- macOS does not activate an app for a right-click -- so it would have
   * no blur to lose when the reader clicks away into another app (KYO-257.c1). Not
   * `focusWindow`, which is the window chips' "focus THAT window by id".
   */
  claimFocus: "kyosei:claim-focus",
  /**
   * main -> renderer, send: the window a tear-off opened is ready to be seen (its
   * `ready-to-show`). Until then the renderer keeps the drag ghost under the pointer:
   * the window takes about 150-190ms to get there after the main process has already
   * answered (KYO-260.c3), and a ghost that vanished first showed as a visible pause.
   */
  tabDragShown: "kyosei:tab-drag-shown",
  /**
   * renderer -> main, send: this shell has finished booting (its `data-ready`). A spare
   * (KYO-261) is only handed a torn-off tab once it has said so, so the window a reader
   * gets is ready rather than one that is still fetching its facts on screen.
   */
  shellBooted: "kyosei:shell-booted",
  /** main -> renderer, send: where the pane is pointed now, and why. */
  claudeState: "kyosei:claude-state",
  /** main -> renderer, send: the state changed. */
  state: "kyosei:state",

  /**
   * main -> renderer, send: a `kyosei://` link arrived; open this, or say why not.
   *
   * One direction only, and that is the boundary (KYO-230). Links are RESOLVED in the
   * main process -- it is where the scheme registration, the claude view's hooks and
   * the launch argv all are, and where the only copy of the id grammar lives -- so a
   * renderer receives a target it did not have to parse. There is deliberately no
   * counterpart that takes a URL from a renderer: KYO-233 adds clickable `[[id]]`
   * links inside the app, and those are a renderer opening its own tab, not a page
   * asking the main process to follow a URL on its behalf.
   */
  openTarget: "kyosei:open-target",

  /**
   * renderer -> main, send: this `kyosei://` URL was clicked inside the app.
   *
   * The counterpart KYO-230 deliberately did not build, added here for the one case
   * that cannot be served without it and no wider (KYO-232.c7). The terminal tab's
   * output is TEXT: a `kyosei://` URL printed by `kyo brief` is a run of characters
   * in a pty stream, and turning it into an open tab needs the id grammar, which lives
   * in `main/links.ts` and lives there once. A renderer that parsed it would be the
   * second copy of that grammar; `[[id]]` links inside a rendered record are the case
   * KYO-230's comment is about and still need none of this, because their id is
   * already resolved.
   *
   * What it grants is nothing new. `followLink` refuses every scheme but `kyosei`, and
   * what a resolved one does is open a tab -- which the renderer draws itself anyway.
   * The delivery comes back over `openTarget` above, so a link clicked in the terminal
   * and a link clicked in the claude view arrive by the same path, as the same shape.
   * `OPEN_SOURCES` has carried `in-app` since KYO-230 for exactly this.
   */
  followLink: "kyosei:follow-link",

  /**
   * renderer -> main, invoke: the store facts, the currency, the kyo source and --
   * since KYO-175 -- which project all three are about, and whether its store opens.
   *
   * This is also the RE-CHECK (KYO-175.c3). The main process resolves the project
   * root and probes it afresh on every call, nothing about the answer being cached,
   * so a human who has just had the other project's controller migrate its store asks
   * again through this same channel and the same window opens it. There is
   * deliberately no separate "recheck" channel: a second name for one call is a
   * second thing that could drift from it, and a re-check that did not also refresh
   * the status bar beside it would leave the window half-true.
   */
  facts: "kyosei:facts",
  /** renderer -> main, invoke: re-ask `kyo` for the currency direction alone. */
  currency: "kyosei:currency",

  // The project switcher (KYO-175). Two requests and one announcement, none of which
  // writes: the two that adopt a project change only which directory `kyo` is RUN in,
  // and the directory they may change it to must already hold a Kyosei project.
  /** renderer -> main, invoke: choose a project directory, then adopt it. */
  projectChoose: "kyosei:project-choose",
  /** renderer -> main, invoke: adopt a named project directory (the recents list). */
  projectOpen: "kyosei:project-open",
  /** renderer -> main, invoke: connect to a dev box and adopt its open project (KYO-370). */
  remoteConnect: "kyosei:remote-connect",
  /** renderer -> main, invoke: re-open a remembered dev box connection (KYO-370). */
  remoteOpen: "kyosei:remote-open",
  /** renderer -> main, invoke: forget one recent project, or all of them (KYO-465.c3). */
  recentsForget: "kyosei:recents-forget",
  /** renderer -> main, invoke: is a dev box's kyo serve reachable, and which projects does it serve (KYO-374). */
  remoteProbe: "kyosei:remote-probe",
  /** renderer -> main, invoke: the machines New project can make a project on (KYO-460). */
  newProjectServers: "kyosei:new-project-servers",
  /** renderer -> main, invoke: list one folder on one of them, for the directory picker. */
  newProjectList: "kyosei:new-project-list",
  /** renderer -> main, invoke: make one folder there (the picker's New Folder). */
  newProjectMkdir: "kyosei:new-project-mkdir",
  /** renderer -> main, invoke: mkdir -p, kyo init and (optionally) kyo controller start there. */
  newProjectCreate: "kyosei:new-project-create",
  /** renderer -> main, invoke: set the SSH host a server entry's commands go to. */
  sshHostSet: "kyosei:ssh-host-set",
  /** main -> renderer, send: the project moved; every window has work to redo. */
  projectChanged: "kyosei:project-changed",

  /** renderer -> main, invoke: every record in the store, grouped as asked. */
  recordsTree: "kyosei:records-tree",
  /** renderer -> main, invoke: the bases saved in `kyosei/bases.yml`. */
  recordsBases: "kyosei:records-bases",
  /** renderer -> main, invoke: run one saved base through the CLI's engine. */
  recordsRunBase: "kyosei:records-run-base",
  /** renderer -> main, invoke: one record, read-only. */
  recordsShow: "kyosei:records-show",
  /** renderer -> main, invoke: the session briefs, newest first. */
  recordsBriefs: "kyosei:records-briefs",
  /** renderer -> main, invoke: one session brief, as the store kept it. */
  recordsBrief: "kyosei:records-brief",
  /** renderer -> main, invoke: the newest shift note in the store (KYO-122.c12). */
  latestShiftNote: "kyosei:latest-shift-note",
  /** renderer -> main, invoke: every shift note, newest first, handoffs marked (KYO-248). */
  shiftNotes: "kyosei:shift-notes",

  // The v3 read model (KYO-231). Four reads, and the shape of this block is the same
  // statement records mode's is: not one of them can write, so "the v3 tabs are
  // read-only to a human" is a property of what is here rather than of the buttons a
  // tab happens to draw.
  /** renderer -> main, invoke: every child object of one record, in id order. */
  recordDocument: "kyosei:record-document",
  /** renderer -> main, invoke: every open gate waiting on a human, and the count. */
  awaitingHuman: "kyosei:awaiting-human",
  /** renderer -> main, invoke: the records list, searched or run through a base. */
  recordsList: "kyosei:records-list",
  /** renderer -> main, invoke: running dispatches and the most recent finished ones. */
  activity: "kyosei:activity",
  /** renderer -> main, invoke: the latest session brief, as sections. */
  latestBrief: "kyosei:latest-brief",
  /**
   * main -> renderer, send: the activity rows were re-read.
   *
   * A push rather than a poll the renderer runs, because the two halves of a row
   * change on different clocks: the store half changes when a controller does
   * something, and the git half changes whenever a worker commits. The main process
   * holds both and pushes when either moves, so a tab does not have to guess an
   * interval or re-ask for rows nothing changed about.
   */
  activityChanged: "kyosei:activity-changed",
  /**
   * main -> renderer, send: the awaiting-human count moved (KYO-232.c6).
   *
   * The records rail icon carries that count as a badge, and a badge that were only
   * read at window start would go on saying `3` for the rest of a session in which the
   * controller cleared every gate. Pushed rather than polled in the renderer for the
   * reason `activityChanged` is: the fact changes when a CONTROLLER does something, in
   * another process, and the main process is the side that can watch for it. It is sent
   * only when the count or the gate ids actually move, so a quiet store is quiet.
   */
  awaitingChanged: "kyosei:awaiting-changed",
  /**
   * main -> renderer, send: the store (or `ideas/`) was written by somebody else.
   *
   * Not a view, and deliberately not one: the payload is which WORLD moved, and the
   * renderer decides whether the list it is showing reads that world (`LIST_FOLLOWS`,
   * `storeReloadDue`). A push carrying rows would have to be the rows of every list
   * at once -- the main process does not know which one is showing, and computing
   * five answers to deliver one is the poll this record replaces, moved a process
   * out. Sent only when the store's own fingerprint moved, so a controller running
   * read-only `kyo` commands in the pane beside the app pushes nothing (KYO-310).
   */
  storeChanged: "kyosei:store-changed",

  // Layout memory per display configuration (KYO-232.c3). One read and one write, and
  // the write is the only thing in this app that persists anything about a window.
  /** renderer -> main, invoke: the layout saved for the screens attached now. */
  layoutLoad: "kyosei:layout-load",
  /** renderer -> main, send: save this layout against the screens attached now. */
  layoutSave: "kyosei:layout-save",
  /** main -> renderer, send: the screens changed; here is that configuration's layout. */
  layoutChanged: "kyosei:layout-changed",

  /** renderer -> main, invoke: the session scratchpad, from the per-project home. */
  ideasScratchpad: "kyosei:ideas-scratchpad",
  /** renderer -> main, invoke: write the session scratchpad. */
  ideasSaveScratchpad: "kyosei:ideas-save-scratchpad",
  /** renderer -> main, invoke: `ideas/` as a file tree. */
  ideasTree: "kyosei:ideas-tree",
  /** renderer -> main, invoke: one idea file, split into body and properties. */
  ideasOpen: "kyosei:ideas-open",
  /** renderer -> main, invoke: write one idea file back. */
  ideasSave: "kyosei:ideas-save",
  /** renderer -> main, invoke: create a new idea file from selected text. */
  ideasCreate: "kyosei:ideas-create",
  /** renderer -> main, invoke: the four organising writes under ideas/ (KYO-250). */
  ideasMkdir: "kyosei:ideas-mkdir",
  ideasRename: "kyosei:ideas-rename",
  ideasMove: "kyosei:ideas-move",
  ideasDelete: "kyosei:ideas-delete",
  /** renderer -> main, invoke: put text on the system clipboard (KYO-252). */
  copyText: "kyosei:copy-text",
  /** renderer -> main, invoke: what `[[` and `#` may offer. */
  ideasCompletions: "kyosei:ideas-completions",

  /** renderer -> main, invoke: this project's documents, with the governance layer. */
  docsTree: "kyosei:docs-tree",
  /** renderer -> main, invoke: one document's text. There is no counterpart that writes. */
  docsOpen: "kyosei:docs-open",

  /** renderer -> main, invoke: open a pty for this window's terminal pane. */
  termOpen: "kyosei:term-open",
  /** renderer -> main, send: keystrokes, for the screen they were typed into. */
  termInput: "kyosei:term-input",
  /** renderer -> main, send: one of the pane's two screens was resized. */
  termResize: "kyosei:term-resize",
  /** main -> renderer, send: bytes from one of the pane's two ptys. */
  termData: "kyosei:term-data",
  /** main -> renderer, send: one of the pane's two ptys exited. */
  termExit: "kyosei:term-exit",
  /**
   * renderer -> main, invoke: this screen is the one on show now, at this size.
   *
   * KYO-293. Two things fall out of a surface being shown and neither can be done by
   * the renderer: the pty behind it must be told the size it was hidden at is stale,
   * and a tmux client must be made to repaint -- tmux draws on SIGWINCH, and a screen
   * that comes back at the size it left at generates none (KYO-258's blank pane).
   */
  termShow: "kyosei:term-show",
  /**
   * main -> renderer, send: the pane's tmux client ended and the pane is bare again.
   *
   * KYO-293.c3's other half. The reader's own prefix-d and the session ending are
   * both invisible to this app until the client pty exits, and the two need different
   * sentences -- so the main process asks the server which happened and says so.
   */
  termFellBack: "kyosei:term-fell-back",

  // The session pills (KYO-283). Three members, and the shape is the boundary: the
  // renderer asks for the row, asks to be put in a NAMED session, and asks to be
  // dropped back to its own shell. It cannot name a tmux command, a socket or a
  // client, so nothing it can say reaches a server the app did not choose.
  /** renderer -> main, invoke: the sessions alive on this app's socket, as pills. */
  termSessions: "kyosei:term-sessions",
  /** renderer -> main, invoke: put this pane in that session, however is possible. */
  termAttach: "kyosei:term-attach",
  /** renderer -> main, invoke: detach this pane's client; back to the bare shell. */
  termDetach: "kyosei:term-detach",
} as const;

/**
 * Which `CHANNEL` entries answer a question about the STORE -- this project's
 * records, briefs, facts, git facts, docs, links, activity, sessions, ideas,
 * scratchpad and compose's promotion; anything that would have to travel over the
 * wire once this app's main process can run on another machine (KYO-367) -- and
 * which answer a question about THIS WINDOW instead, in `WINDOW_LOCAL_CHANNELS`
 * below: its tabs, its pop-outs, the claude pane's bounds and visibility, its
 * focus, its layout, its terminal panes. Terminal channels are window-local for
 * this plan: a remote project shows no terminal.
 *
 * A push paired with a request stays in the request's set (`activityChanged` with
 * `activity`, `openTarget` with `followLink`), and `storeChanged` -- which has no
 * request at all -- is store-facing because what it announces is: `StoreService`
 * (KYO-368's own interface, `main/service.ts`) carries it as the one subscription.
 * Some store-facing channels back a WINDOW-local one's own resolution rather than
 * answering the renderer directly (`claudeShow` reads store facts to decide where
 * the pane points before doing anything to the window); that channel still belongs
 * in `WINDOW_LOCAL_CHANNELS`, because the question `CHANNEL` member asks -- what a
 * renderer gets in reply -- is about the window, not the store.
 *
 * Authored once, here, beside `CHANNEL` itself, so KYO-367's later members -- a
 * server exposing exactly the store-facing set, a client substituting for it --
 * read this partition rather than re-deriving it. `tests/seams.spec.ts` fails if a
 * `CHANNEL` entry is in neither set, or in both (KYO-368.c1).
 */
export const STORE_FACING_CHANNELS: readonly (keyof typeof CHANNEL)[] = [
  "facts",
  "currency",
  "recordsTree",
  "recordsBases",
  "recordsRunBase",
  "recordsShow",
  "recordsBriefs",
  "recordsBrief",
  "latestShiftNote",
  "shiftNotes",
  "recordDocument",
  "awaitingHuman",
  "recordsList",
  "activity",
  "latestBrief",
  "activityChanged",
  "awaitingChanged",
  "storeChanged",
  "ideasScratchpad",
  "ideasSaveScratchpad",
  "ideasTree",
  "ideasOpen",
  "ideasSave",
  "ideasCreate",
  "ideasMkdir",
  "ideasRename",
  "ideasMove",
  "ideasDelete",
  "ideasCompletions",
  "docsTree",
  "docsOpen",
  "followLink",
  "openTarget",
  "claudeTextClick",
  "controllerStatus",
  "controllerStart",
];

/** The window-local half of the same partition. See `STORE_FACING_CHANNELS`. */
export const WINDOW_LOCAL_CHANNELS: readonly (keyof typeof CHANNEL)[] = [
  "hello",
  "openWindow",
  "windowChoose",
  "focusWindow",
  "popOut",
  "command",
  "tabDragStart",
  "tabDragMove",
  "tabDragEnd",
  "tabDragHover",
  "tabReceive",
  "claudeShow",
  "claudeBounds",
  "claudeHide",
  "claudeReload",
  "claudeSnapshot",
  "claudeUncover",
  "controllerCommand",
  "windowBlur",
  "claimFocus",
  "tabDragShown",
  "shellBooted",
  "claudeState",
  "state",
  "projectChoose",
  "projectOpen",
  "remoteConnect",
  "remoteOpen",
  // KYO-465: this installation's own memory of what it opened -- never any store's.
  "recentsForget",
  "remoteProbe",
  // KYO-460: commands this app runs on a machine, here or over SSH -- never served.
  "newProjectServers",
  "newProjectList",
  "newProjectMkdir",
  "newProjectCreate",
  "sshHostSet",
  "projectChanged",
  "layoutLoad",
  "layoutSave",
  "layoutChanged",
  "copyText",
  "termOpen",
  "termInput",
  "termResize",
  "termData",
  "termExit",
  "termShow",
  "termFellBack",
  "termSessions",
  "termAttach",
  "termDetach",
];

// ------------------------------------------ the store service on the wire (KYO-369)
//
/** The surface `preload.ts` exposes on `window.kyosei`. */
export interface KyoseiBridge {
  getState(): Promise<AppState>;
  /**
   * Open another shell window (KYO-249): on `root` when one is named, validated the
   * way the switcher validates, else on the project a fresh window opens on.
   */
  openWindow(root?: string, remote?: RemoteOpenInput): Promise<ProjectChoice>;
  /** The native picker, then a window on what was picked (KYO-249). */
  chooseProjectWindow(): Promise<ProjectChoice>;
  focusWindow(id: number): Promise<void>;
  /** Move a tab into a window of its own; the context names which tab (KYO-232). */
  popOut(pane: PopoutPane, context?: PopoutContext): Promise<void>;
  onState(listener: (state: AppState) => void): () => void;
  /** A menu command arrived for this window (KYO-246). */
  onCommand(listener: (command: ShellCommand) => void): () => void;
  /** A tab left its strip: open a window around it that follows the pointer. */
  startTabDrag(start: TabDragStart): Promise<TabDragStartOutcome>;
  /** The pointer moved while a window follows it. */
  moveTabDrag(point: TabDragPoint): void;
  /** The pointer was released: attach to the strip under it, or leave the window. */
  endTabDrag(point: TabDragPoint): Promise<TabDragEndOutcome>;
  /** A window being dragged is over this window's strip, or has just left it. */
  onTabDragHover(listener: (at: TabDragPoint | null) => void): () => void;
  /** Another window handed this one a tab (KYO-246). */
  onTabReceived(listener: (handoff: TabHandoff) => void): () => void;

  // Layout memory per display configuration (KYO-232.c3).
  /** The layout saved against the screens attached right now, or null for none. */
  getLayout(): Promise<LayoutState>;
  /**
   * Save this arrangement under the project and screens it was MADE on, named by the
   * caller (KYO-334) -- not under whichever the window has moved to since.
   */
  saveLayout(save: LayoutSave): void;
  /** The attached screens changed: here is what THAT configuration remembers. */
  onLayoutChanged(listener: (state: LayoutState) => void): () => void;

  /**
   * A `kyosei://` link arrived: open its target, or show why it was not followed.
   *
   * The only member of this interface concerning deep links, and its shape is the
   * decision (KYO-230): it is a SUBSCRIPTION and not a request. A renderer cannot ask
   * the main process to follow a URL, because a renderer that could would be a page
   * able to name any link and have the app act on it; it is told about links the main
   * process already resolved, from the three places they arrive.
   */
  onOpenTarget(listener: (delivery: LinkDelivery) => void): () => void;

  /**
   * A `kyosei://` URL was clicked inside the app: resolve it and deliver it.
   *
   * KYO-232.c7, and the narrowest counterpart to `onOpenTarget` that serves it. There
   * is still no method here that navigates anything: this hands the main process a
   * string, the main process decides -- refusing every other scheme, every unknown
   * project and every unknown id shape -- and what comes back comes back over
   * `onOpenTarget`, exactly as a link clicked in the claude view does.
   */
  followLink(url: string, origin?: LinkOrigin): void;

  // The claude.ai pane (KYO-186). Note what is NOT here: there is no method that
  // takes a URL. The renderer can say where to draw the view and can ask for it to
  // be resolved again; it has no vocabulary in which to name a page for a
  // logged-in session to navigate to. That is the boundary as the renderer can
  // observe it, not a description of one.
  showClaude(bounds: PaneBounds): Promise<ClaudePane>;
  setClaudeBounds(bounds: PaneBounds): void;
  hideClaude(): Promise<void>;
  reloadClaude(): Promise<ClaudePane>;
  /**
   * Stepping aside for an overlay (`renderer/overlays.ts`). The view is composited above
   * this document, so a menu over the claude column would be under it: the tab takes a
   * still (a data: URL, or null when there is nothing painted), hides the view, and
   * uncovers it when the overlay closes -- attached at the bounds given, not re-resolved,
   * because closing a menu is not a reason to navigate the reader's conversation.
   */
  snapshotClaude(): Promise<string | null>;
  uncoverClaude(bounds: PaneBounds): Promise<ClaudePane | null>;
  /** The native window lost focus, wherever inside it focus was (`CHANNEL.windowBlur`). */
  onWindowBlur(listener: () => void): () => void;
  /** Take focus for the calling window, activating the app (`CHANNEL.claimFocus`). */
  claimFocus(): void;
  /** The torn-off window can be seen now (`CHANNEL.tabDragShown`). */
  onTabDragShown(listener: () => void): () => void;
  /** This shell finished booting (`CHANNEL.shellBooted`). */
  shellBooted(): void;
  onClaudeState(listener: (state: ClaudePane) => void): () => void;
  /**
   * The controller launcher (KYO-383). Status and Start take nothing that names a
   * program -- the command is read on the machine that runs it -- and changing the
   * command is refused unless this window is on that machine.
   */
  controllerStatus(): Promise<ControllerState>;
  startController(): Promise<ControllerState>;
  setControllerCommand(command: string): Promise<ControllerCommandOutcome>;

  getFacts(): Promise<LiveFacts>;
  getCurrency(): Promise<CurrencyFacts>;

  // The project switcher (KYO-175). `openProject` is the only method on this object
  // that takes an absolute path, and it is worth saying why that is not a hole in
  // the write boundary the ideas methods below establish: it names a directory to
  // RUN `kyo` in, it is refused unless a Kyosei project is already configured at or
  // above it, and nothing it reaches can mutate a store. Switching which store is
  // read is not a write path, and KYO-79 decision 4 is untouched by any of it.
  //
  // There is no `getProject` here, and its absence is deliberate: `getFacts` already
  // carries the project and re-probes it on every call, so a second method answering
  // the same question would be a second thing to keep in step with the first.
  /** Put a native directory picker up, then adopt what was chosen. */
  chooseProject(): Promise<ProjectChoice>;
  /** Adopt a project directory by name -- the recents list, and the checks. */
  openProject(root: string): Promise<ProjectChoice>;
  /**
   * Connect to a dev box (KYO-370): probe the address with the token, then move
   * THIS window onto the project that app has open. A server that cannot be reached
   * or refuses the token comes back as `refused`, naming host, port and the refusal.
   */
  connectRemote(input: RemoteConnectInput): Promise<ProjectChoice>;
  /** Re-open a remembered dev box connection. */
  openRemote(input: RemoteOpenInput): Promise<ProjectChoice>;
  /**
   * Forget one recent project -- on this machine or on a server -- or every one with
   * `null` (KYO-465.c3). Answers this window's facts, whose `recents` no longer offer it.
   */
  forgetRecent(which: RecentForgetInput | null): Promise<ProjectFacts>;
  /** Ask a dev box whether it is there and which projects it has, adopting nothing (KYO-374). */
  probeRemote(input: RemoteConnectInput): Promise<RemoteProbeFacts>;
  /** The project moved: rebuild what this window is showing of the old one. */
  onProjectChanged(listener: (project: ProjectFacts) => void): () => void;
  /** New project (KYO-460): this machine, then every dev box connected to before. */
  newProjectServers(): Promise<ProjectServerEntry[]>;
  /** List a folder on that machine (its home when `path` is null), for the picker. */
  listFolder(server: RemoteFacts | null, path: string | null): Promise<FolderListing>;
  /** The picker's New Folder: `mkdir` one folder there. */
  makeFolder(server: RemoteFacts | null, path: string): Promise<FolderMade>;
  /** Make the project: `mkdir -p`, `kyo init`, and `kyo controller start` when asked. */
  createProject(input: NewProjectInput): Promise<NewProjectOutcome>;
  /** Set the SSH host a dev box's commands go to; empty goes back to its host name. */
  setSshHost(server: RemoteFacts, sshHost: string): Promise<SshHostOutcome>;

  // Records mode. Every one of these is a read; there is no counterpart that
  // writes, which is the whole of KYO-72's "records mode is read-only to a human"
  // as the renderer can observe it -- the renderer cannot reach `kyo` any other way.
  getRecordTree(grouping: TreeGrouping, scope?: TreeScope): Promise<RecordTree>;
  getBases(): Promise<BaseCatalogue>;
  runBase(name: string): Promise<BaseResult>;
  showRecord(id: string): Promise<RecordDetail>;
  getBriefs(): Promise<BriefCatalogue>;
  getBrief(id: string): Promise<BriefDocument>;
  /** The newest shift note the store holds, or none (KYO-122.c12). */
  getLatestShiftNote(): Promise<LatestShiftNote>;
  getShiftNotes(): Promise<ShiftNotesCatalogue>;

  // The v3 read model (KYO-231). Four reads and one subscription, and -- as with
  // every block above it -- no counterpart that writes. A v3 tab renders what these
  // return and derives nothing: the obligation collapse is the CLI's own, the
  // activity classes are the brief's, and the git facts are git's.
  /** Every child object of one record, in id order, obligations collapsed. */
  getRecordDocument(id: string): Promise<RecordDocument>;
  /** Every open gate waiting on a human, across every record status, and the count. */
  getAwaitingHuman(): Promise<AwaitingHuman>;
  /** The records list: searched by id or title, or run through a saved base. */
  getRecordsList(options?: { query?: string; base?: string }): Promise<RecordsList>;
  /** Running dispatches and the most recent finished ones, store half and git half. */
  getActivity(options?: { finished?: number }): Promise<ActivityView>;
  /** The latest session brief as structured sections, every id preserved. */
  getLatestBrief(): Promise<BriefDocumentView>;
  /** The activity rows were re-read: here they are. */
  onActivityChanged(listener: (view: ActivityView) => void): () => void;
  /** The awaiting-human count moved: here is the new answer (KYO-232.c6). */
  onAwaitingChanged(listener: (view: AwaitingHuman) => void): () => void;
  /**
   * Somebody else wrote the store, `ideas/` or the scratchpad: whichever list follows it
   * re-reads (KYO-310), and so does an open compose tab with no unsaved edits (KYO-575).
   */
  onStoreChanged(listener: (change: StoreChange) => void): () => void;

  // Ideas mode (KYO-114). Four reads and three writes, and the three writes are
  // the whole of what this app can change about the world: the scratchpad file,
  // an existing file under `ideas/`, and a new one. Nothing here names a path --
  // the renderer sends a path relative to `ideas/` or nothing at all, and main
  // resolves it against the one directory it will write to. So "the app never
  // writes `kyosei/`" is a property of this list plus `assertWritable`, not of the
  // buttons a pane draws.
  getScratchpad(): Promise<Scratchpad>;
  saveScratchpad(save: ScratchpadSave): Promise<SaveResult>;
  getIdeaTree(): Promise<IdeaTree>;
  openIdea(path: string): Promise<IdeaDocument>;
  saveIdea(edit: IdeaEdit): Promise<SaveResult>;
  createIdea(request: { folder: string; name: string; text: string }): Promise<WriteResult>;
  /**
   * Organising `ideas/` (KYO-250): a folder made, a file or folder renamed in place,
   * moved to another folder, or deleted. Every one runs in the main process and is
   * refused, naming the path, for anything that resolves outside `ideas/`.
   */
  mkdirIdeas(request: { folder: string }): Promise<WriteResult>;
  renameIdea(request: { path: string; name: string }): Promise<WriteResult>;
  moveIdea(request: { path: string; folder: string }): Promise<WriteResult>;
  deleteIdea(request: { path: string }): Promise<WriteResult>;
  /** Put text on the clipboard: the promoted idea's path, for the controller chat (KYO-252). */
  copyText(text: string): Promise<void>;
  getCompletions(): Promise<Completions>;

  // Docs mode (KYO-139). Two reads and NO write, and the absence is the decision:
  // KYO-136 decision 6 makes this mode pure view-only, past the runtime seam. The
  // renderer cannot reach `kyo` or the filesystem except through this object, so
  // "docs mode cannot change a document" is a property of there being no third
  // method here -- not a property of which buttons a pane happens to draw.
  getDocTree(): Promise<DocTree>;
  openDoc(path: string): Promise<DocDocument>;

  openTerminal(size: TerminalSize): Promise<TerminalOpen>;
  sendTerminalInput(input: TerminalInput): void;
  resizeTerminal(resize: TerminalResize): void;
  /** This screen is showing now, at this size (KYO-293): refit it and repaint it. */
  showTerminalSurface(resize: TerminalResize): Promise<void>;
  onTerminalData(listener: (chunk: TerminalChunk) => void): () => void;
  onTerminalExit(listener: (info: TerminalExitInfo) => void): () => void;
  /** The pane's tmux client went away on its own; here is what happened (KYO-293). */
  onTerminalFellBack(listener: (outcome: TerminalAttachOutcome) => void): () => void;
  /** The pill row: the sessions alive on this app's socket (KYO-283.c2). */
  terminalSessions(): Promise<TerminalSessionsView>;
  /**
   * Put this pane in that session: attach when it is alive, create it and attach when
   * it is not (KYO-293.c1). The size is the session screen's, so the client pty starts
   * at the size the reader is looking at rather than at 80x24 and a repaint.
   */
  attachTerminal(session: string, size: TerminalSize): Promise<TerminalAttachOutcome>;
  /**
   * Show this pane's bare login shell and detach its tmux client (KYO-293.c3). The
   * session and everything in it keep running; the shell is the same one, with its
   * screen and scrollback, every time.
   */
  detachTerminal(size: TerminalSize): Promise<TerminalAttachOutcome>;
}
