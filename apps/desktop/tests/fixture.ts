import type { Page } from "@playwright/test";
// A throwaway Kyosei store, built by the real CLI, for the checks that need a
// store whose contents they control.
//
// Two things about it are deliberate.
//
// It is built by invoking `kyo` through the app's own resolver, so the fixture
// helper is itself a use of the seam under test: if `resolveKyo` or `runKyo` broke,
// no fixture would exist to run anything else against.
//
// Its path contains a space, always. This repository's own checkout does (KYO-89),
// and a fixture in a spaceless temp directory would let a quoting or argv defect
// pass here and fail in the place the project actually lives.

import { type ChildProcess, execFileSync, spawn } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { type KyoResolution, resolveKyo, runKyo } from "../src/main/kyo";
import { clientFor, SERVE_URL_ENV, type ServedProject } from "../src/main/kyoserve";
import { SSH_PROGRAM_ENV } from "../src/main/ssh";
import { RETIRED_SHIM_ID_ENV, USER_DATA_ENV } from "../src/main/terminal";
import { WINDOW_SHOW_SWITCH, type WindowShow } from "../src/shared/protocol";

/**
 * How every Electron this suite launches arrives on screen (KYO-122).
 *
 * `hidden`: the windows are created, loaded, laid out and driven over CDP, and never
 * appear on screen at all.
 *
 * The problem is VISUAL, and that took a second look to establish. The obvious
 * reading of "the machine is unusable while the suite runs" is stolen focus, and
 * `showInactive()` answers that -- but typing into another app's window kept
 * working throughout a run, so focus was never what was going. What the windows were doing
 * was PAINTING: ordered front, across a full-screen app sitting in its own macOS
 * Space. An inactive window is still an on-screen window and still does all of that,
 * so `showInactive` does not buy the relief it looks like it buys. Not showing at all
 * does, and nothing here needs a window on screen: Playwright's Electron support
 * attaches to the renderer over CDP.
 *
 * What that would otherwise cost is the reason it was not the first answer. Chromium
 * treats a never-shown window as occluded and throttles it -- timers to about one
 * tick a second, the renderer deprioritised, frames starved -- and running the checks
 * in a rendering regime the product never runs in is how a check comes to pass
 * vacuously, which is a failure KYO-120 had just found in this very file. So the
 * throttling is turned off by name rather than avoided: `prepareApp` in
 * `main/main.ts` appends `disable-background-timer-throttling`,
 * `disable-renderer-backgrounding` and `disable-backgrounding-occluded-windows`, and
 * every window carries `backgroundThrottling: false`. That neutralisation is checked
 * rather than trusted -- see "an unshown window is not a throttled one" in
 * `smoke.spec.ts`, which measures frames and timers inside the real thing. Under
 * those switches an unshown window reports `document.visibilityState === "visible"`
 * and delivers frames at full rate, so there is no observable difference for page
 * code to notice.
 *
 * A check that genuinely needs a different policy -- a visible window, or a focused
 * one -- says so ITSELF, by passing its own `WindowShow` to `windowShowArg`: one
 * launch opting in, rather than the whole suite opting in on behalf of one check.
 * There is deliberately no environment variable and no config default: quiet is what
 * the suite IS, and anything else is a per-launch exception with its reason at the
 * call site. No check needs one today.
 */
export const SUITE_WINDOW_SHOW: WindowShow = "hidden";

/** The launch argument for a given policy; append it last, it wins over earlier ones. */
export function windowShowArg(show: WindowShow = SUITE_WINDOW_SHOW): string {
  return `${WINDOW_SHOW_SWITCH}=${show}`;
}

/**
 * The launch arguments every Electron this suite starts must carry (KYO-116).
 *
 * macOS offers to restore an app's windows after it "unexpectedly quit", and the
 * offer is a MODAL dialog owned by the bundle id -- `com.github.Electron` for a dev
 * Electron, read from its own Info.plist rather than assumed. A suite that launches
 * and force-kills that bundle id repeatedly is exactly the input that arms it, and
 * once armed the next launch of the same bundle blocks until a human clicks Don't
 * Reopen. From inside the suite that looks like `firstWindow` never resolving: a
 * hang, not slowness, moving between tests, never an assertion. It cost KYO-115 two
 * dispatches and a wrong diagnosis (KYO-115.r3 corrects it).
 *
 * These two arguments are macOS's own opt-out, taken per launch. Foundation reads a
 * process's argv as the NSArgumentDomain, which outranks every persisted defaults
 * domain and applies to that process alone: nothing is written to the machine, no
 * `defaults write` is needed, and a fresh checkout on a fresh machine is immune
 * without anyone knowing this bug existed. Verified rather than assumed -- with
 * these arguments `systemPreferences.getUserDefault("ApplePersistenceIgnoreState")`
 * reads true inside the launched app and false without them, on a machine carrying
 * no such default (KYO-116.d1).
 *
 * `NSQuitAlwaysKeepsWindows NO` is the belt to that brace. It reads false here with
 * or without the argument, because false is already the macOS default -- but a user
 * who unticks "Close windows when quitting an app" in System Settings flips it true
 * for every app, and this suite should not be at the mercy of that switch.
 */
export const MACOS_NO_RESTORE = [
  "-ApplePersistenceIgnoreState",
  "YES",
  "-NSQuitAlwaysKeepsWindows",
  "NO",
];

// ------------------------------------ the terminal this suite was started IN (KYO-254)
//
// Every launch environment in this suite used to be `process.env` plus overrides, and
// `process.env` is the runner's -- which since KYO-232 is routinely a Claude Code
// session running inside the packaged app's OWN terminal pane. That pane exports the
// five names below, and each of them changes what the app under test does:
//
//   TMUX, TMUX_PANE   tmux reads them as "you are already inside a session". The app
//                     deletes both before it spawns a tmux pane (`startTerminal`), so
//                     these two alone are survivable -- but a check's own `tmux` calls
//                     see them, and `attach-session` refuses outright under them.
//   TMUX_TMPDIR       the one that decides WHICH SERVER. `terminalEnvironment` layers
//                     the process environment OVER the captured login shell's, so a
//                     TMUX_TMPDIR inherited from the host pane beats the private one
//                     `makeLoginShell` exports and the app lands its session on the
//                     host's server. The fixture then lists its own server, finds
//                     nothing, and three tmux-owner checks fail -- while a real
//                     session named `kyosei-a-project` is left on a server the suite
//                     does not own and never kills.
//   KYOSEI_DESKTOP    "a pane of the Kyosei app opened this". A shell profile or a
//                     `kyo` invocation may branch on it, and here it is a lie: the
//                     runner is not a pane of the app under test.
//   KYOSEI_SHIM_ID    the id of the shim the HOST app wrote, when the host is an
//                     earlier build (KYO-189). This build writes no shim (KYO-450)
//                     and drops the variable from its panes, but a runner started in
//                     an old app's pane still carries it, and it names nothing here.
//
// So no spec builds a launch environment out of `process.env` directly: they all start
// from `suiteEnv()`. Stated once here rather than five deletions per spec, because a
// spec added tomorrow inherits the scrub instead of having to remember it.
export const HOST_PANE_ENV: readonly string[] = [
  "TMUX",
  "TMUX_PANE",
  "TMUX_TMPDIR",
  "KYOSEI_DESKTOP",
  RETIRED_SHIM_ID_ENV,
];

/**
 * The runner's environment with the host terminal's own variables removed, plus
 * `extra`, with the undefined entries dropped.
 *
 * This is the base every launch environment in this suite is built from. It is NOT a
 * clean environment -- PATH, HOME and the rest are still the runner's, deliberately,
 * because the suite is meant to run on the machine it is run on. What it removes is
 * only the set in `HOST_PANE_ENV`: the variables that describe the terminal the suite
 * was STARTED from, and that would otherwise describe the app the suite LAUNCHES.
 */
export function suiteEnv(extra: Record<string, string> = {}): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value === undefined || HOST_PANE_ENV.includes(key)) continue;
    env[key] = value;
  }
  return {
    ...env,
    [USER_DATA_ENV]: suiteUserData(),
    // KYO-459: no app this suite launches reaches the real kyo serve on 47370. Every
    // launch is pointed at the kyo serve the last fixture started (`makeFixture`), and
    // a launch with no fixture at an address nothing listens on -- never at whatever
    // the runner's own environment names.
    [SERVE_URL_ENV]: process.env[SUITE_SERVE_ENV] ?? NOTHING_LISTENS,
    // KYO-460: no app this suite launches reaches a real host. A window on a dev box's
    // project runs `kyo controller` there over ssh, so ssh is a program that fails; a
    // check of the SSH path hands in its stand-in through `extra`. And no local
    // `kyo controller` asks the machine's real `tmux -L kyosei` server: its socket is
    // one nothing listens on.
    [SSH_PROGRAM_ENV]: "/usr/bin/false",
    KYOSEI_CONTROLLER_SOCKET: join(tmpdir(), "kyosei-suite-no-controller.sock"),
    ...extra,
  };
}

// ------------------------------------------------ a kyo serve of the check's own (KYO-459)
//
// The app reads and writes every project through a `kyo serve` (KYO-459), so every
// check that shows a project starts one: the working tree's own CLI, `kyo serve --port
// 0` on loopback, under the fixture's `KYOSEI_HOME` -- whose `projects.json` lists the
// fixture's project, because `kyo init` registered it there. Never the real server on
// 47370: the port is the system's pick, read back from the line the server prints
// when it binds, and the app is pointed at it through `KYOSEI_SERVE_URL`.

/**
 * Where the suite's own kyo serve is, for `suiteEnv` to hand every launch: set by
 * `makeFixture` on this process, like `KYOSEI_HOME`. Its own name rather than
 * `KYOSEI_SERVE_URL` itself, so a value in the runner's environment is never passed on.
 */
export const SUITE_SERVE_ENV = "KYOSEI_SUITE_SERVE_URL";

/** An address nothing listens on (port 2: privileged, and not on fetch's "bad port" list). */
export const NOTHING_LISTENS = "http://127.0.0.1:2";

/** One running `kyo serve`, started by a check. */
export interface KyoServeFixture {
  child: ChildProcess;
  /** Where it listens: 127.0.0.1 and the port the system picked. */
  address: { host: string; port: number };
  /** `http://127.0.0.1:<port>` -- the value `KYOSEI_SERVE_URL` takes. */
  url: string;
  /** The `KYOSEI_HOME` it serves. */
  home: string;
  /** Everything it wrote to stderr so far (one line per request). */
  stderr(): string;
}

const runningServes = new Set<ChildProcess>();
process.on("exit", () => {
  for (const child of runningServes) {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
  }
});

/** Start `kyo serve --port 0` for `home`; resolves once it has bound its port. */
export async function startKyoServe(
  home: string,
  extra: Record<string, string> = {},
  port = 0,
): Promise<KyoServeFixture> {
  const resolution = testResolution();
  const { program, baseArgs, unavailable } = resolution.source;
  if (program === null) throw new Error(`no kyo to serve with: ${unavailable ?? "no source"}`);
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  const child = spawn(program, [...baseArgs, "serve", "--port", String(port)], {
    cwd: tmpdir(),
    env: { ...env, KYOSEI_HOME: home, ...extra },
    stdio: ["ignore", "ignore", "pipe"],
  });
  runningServes.add(child);
  child.once("exit", () => runningServes.delete(child));
  let stderr = "";
  child.stderr?.on("data", (chunk: Buffer) => {
    stderr += chunk.toString("utf8");
  });
  const until = Date.now() + 30_000;
  for (;;) {
    const bound = /serving http:\/\/127\.0\.0\.1:(\d+)\/v1\//.exec(stderr);
    if (bound !== null) {
      const port = Number(bound[1]);
      return {
        child,
        address: { host: "127.0.0.1", port },
        url: `http://127.0.0.1:${port}`,
        home,
        stderr: () => stderr,
      };
    }
    if (child.exitCode !== null || Date.now() > until) {
      child.kill("SIGTERM");
      throw new Error(`kyo serve did not start for ${home}: ${stderr || "(no output)"}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

/** SIGTERM a check's `kyo serve`, and wait for it to go. */
export async function stopKyoServe(serve: KyoServeFixture): Promise<void> {
  if (serve.child.exitCode !== null || serve.child.signalCode !== null) return;
  const gone = new Promise<void>((resolve) => serve.child.once("exit", () => resolve()));
  serve.child.kill("SIGTERM");
  await gone;
}

// ------------------------------------ the app under test gets its own userData (KYO-275)
//
// KYO-189 decided that every build of this app shares one `userData` directory, and
// with it the `kyo` shim every pane runs. The suite is a launcher that decision never
// counted: each app it starts regenerated the user's LIVE shim and pointed it at
// this checkout's CLI, so a single test run switched two consumer projects' open panes
// to a CLI two schema versions ahead of their stores. The specs that passed
// `--user-data-dir` were isolated; two of the twelve (`links.spec.ts` and
// `tmux-owner.spec.ts`) never did, and nothing checked, which is why the leak ran for as long as the suite has existed with
// nothing failing. A spec that still passes the switch keeps its own directory -- the
// app lets an explicit switch win over this variable.
//
// One directory per runner process, created on first use and removed at exit, handed
// to every launch through `suiteEnv()` so a spec cannot forget it. Per process rather
// than per fixture because the shim, the shell-env log and the layouts file are
// per-app state, not per-project state, and because a spec that launches with no
// fixture at all must still not touch the real directory.
let suiteUserDataDir: string | null = null;

/** The `userData` directory every app this process launches is given. */
export function suiteUserData(): string {
  if (suiteUserDataDir === null) {
    suiteUserDataDir = realpathSync(mkdtempSync(join(tmpdir(), "kyosei desktop userData ")));
    const dir = suiteUserDataDir;
    process.on("exit", () => {
      try {
        removeTree(dir);
      } catch {
        // A directory that would not go at exit is a leak of a temp directory, not a
        // failure of any check; the OS reclaims it.
      }
    });
  }
  return suiteUserDataDir;
}

// ---------------------------------- removing a directory something is writing to (KYO-238)
//
// A recursive `rmSync` is a WALK: readdir, unlink each name, rmdir the directory. A
// file created between the readdir and the rmdir makes the rmdir fail with
// `ENOTEMPTY`, and everything in this suite still writes into a fixture at the moment
// it is being removed -- a `kyo` subprocess the app spawned and nobody waits for,
// SQLite's `-wal` and `-shm` beside a database being closed, the git poll KYO-229
// added, the pty's own shell whose cwd is the fixture root. `closeReadyApp` awaits the
// Electron; it does not await the Electron's children, and it cannot: they are not its
// children by the time it returns.
//
// So the race is real, it is not going away, and what removes it is retrying the walk
// rather than sequencing processes nothing here owns. Measured on this machine, node
// v24.18.0, 40 teardowns each against a writer creating files inside the tree
// (KYO-254.d1's `rmtest` harness, 2026-09-16):
//
//   {recursive, force}, writer for the whole window   26 failures in 40, all ENOTEMPTY
//   {recursive, force}, writer stops after 0.4s       16 failures in 40, all ENOTEMPTY
//   the same plus {maxRetries: 10, retryDelay: 100}    0 failures in 40, both arms
//
// This is NOT a widened timeout in the sense the known-intermittent table forbids.
// What is being waited for is not a slow answer that may or may not arrive: it is a
// directory that is empty for an instant between two writes, and a single attempt only
// ever sampled one instant. Nothing is swallowed either -- after the last attempt the
// error is thrown exactly as before, naming the directory that would not go.
const TEARDOWN_RETRIES = { maxRetries: 10, retryDelay: 100 } as const;

/**
 * Remove a directory tree this suite owns, retrying the `ENOTEMPTY` above.
 *
 * One definition, every teardown in this file, and exported so a spec's own teardown
 * gets the same treatment rather than reinventing the bare `rmSync` that loses.
 */
export function removeTree(path: string): void {
  rmSync(path, { recursive: true, force: true, ...TEARDOWN_RETRIES });
}

/**
 * The apps this suite is allowed to stop: the ones whose first window has rendered.
 *
 * KYO-118. Quitting an Electron 44.0.0 while it is still starting up segfaults it,
 * intermittently, on macOS ARM64. `app.close()` is `app.quit()` over CDP, which on
 * macOS reaches `-[NSWindow performClose:]` -- and that is a button click, so the
 * crashing stack carries `-[NSButtonCell performClick:]` and `-[NSWindow __close]`
 * above frames inside Electron Framework that then dereference 0x228. All 26 reports
 * standing in `~/Library/Logs/DiagnosticReports` when this was filed carry that one
 * signature, every one of them 0.22s-0.38s after its own process launched.
 *
 * Measured here, not inferred. Under this app, 20 launches closed the instant
 * `_electron.launch()` resolved produced 3 crashes; 20 launches closed only after
 * `body[data-ready="true"]` produced 0. The same early-close arm against Electron
 * 43.4.1 produced 0 in 20 -- the defect is a regression in 44.0.0, and 44.0.0 is the
 * latest stable Electron there is, so there is no upgrade to take. This barrier is
 * the fix available to us: never ask an app to quit before it has finished starting.
 *
 * It is a `WeakSet` and a refusal rather than a convention because a convention is
 * what already failed. The suite's early closes were not written as early closes --
 * they are `beforeEach` launching an app that the test's first line then relaunches
 * away from, which reads as nothing at all.
 */
const readyToClose = new WeakSet<object>();

/** Record that an app's first window has rendered, so it may be closed. */
export function markReadyToClose(app: object): void {
  readyToClose.add(app);
}

/**
 * Close an Electron this suite launched, refusing one that never reported ready.
 *
 * Structurally typed on `close()` rather than on `ElectronApplication` so the
 * refusal itself is checkable without launching an app to be refused -- a test that
 * proved this guard by early-closing a real Electron would be re-arming the very
 * crash the guard exists to prevent.
 */
export async function closeReadyApp(app: { close(): Promise<void> }): Promise<void> {
  if (!readyToClose.has(app)) {
    throw new Error(
      "KYO-118: refusing to close an Electron whose first window never reported ready. " +
        "Quitting Electron 44.0.0 mid-startup segfaults it (EXC_BAD_ACCESS at 0x228). " +
        "Await the window (readyWindow/firstShell) and call markReadyToClose before closing.",
    );
  }
  await app.close();
}

// ============================== a login shell whose environment a check owns (KYO-229)
//
// The app asks the user's login shell for its environment once at startup and spawns
// every pty with it, which is the whole of KYO-229.c1. A check of that cannot use the
// developer's own `$SHELL`: what it would then assert is whatever that person's
// `.zshrc` happens to export, which is different on every desk and unassertable on
// all of them.
//
// So the check supplies a login shell of its own, through `KYOSEI_LOGIN_SHELL` -- the
// app's own seam for naming one, the same kind of seam `KYOSEI_TMUX` and `KYOSEI_HOME`
// already are, and not a test-only branch. What that shell exports is what the check
// then looks for in the pane: a marker variable, a PATH holding a stub `npm`, and
// (for the tmux checks) a `TMUX_TMPDIR` pointing at a private server.
//
// It is a REAL LOGIN SHELL rather than a stub of one: it is executed with the app's
// own `-ilc` and answers the app's own `printf ... env ... printf` script through
// `/bin/sh`, so what is exercised is the parsing and the merge, not a mock of them.
// Run with no arguments it is an interactive shell, because that is what the pty runs.

export interface LoginShellFixture {
  /** The directory holding everything below; remove it to clean up. */
  dir: string;
  /** The wrapper: pass as `KYOSEI_LOGIN_SHELL`. */
  shell: string;
  /** On the PATH this shell exports, holding a stub `npm` and maybe a tmux. */
  binDir: string;
  /** The value of `KYOSEI_FIXTURE_MARKER` in the environment it exports. */
  marker: string;
  /** The private tmux server directory it exports as `TMUX_TMPDIR`. */
  sockets: string;
  remove(): void;
}

export interface LoginShellOptions {
  /**
   * A tmux to put on the exported PATH, or null for a PATH with none on it.
   *
   * Linked into `binDir` rather than named through `KYOSEI_TMUX`, because that is the
   * point: c1 asks that the pane attach to tmux **without any `KYOSEI_TMUX` setting**,
   * which can only be shown by a tmux the app found on the login shell's own PATH.
   */
  tmux?: string | null;
}

/**
 * A login shell that exports a known environment, and the environment it exports.
 *
 * The PATH it sets is `binDir` plus `/usr/bin:/bin` and nothing else -- deliberately
 * NOT the developer's own -- so "tmux is on this PATH" and "tmux is not" are both
 * states the check produces rather than states it inherits from the machine.
 */
export function makeLoginShell(options: LoginShellOptions = {}): LoginShellFixture {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "kyosei login shell ")));
  const binDir = join(dir, "a bin dir");
  mkdirSync(binDir, { recursive: true });
  // The socket directory is SHORT and lives beside the fixture rather than inside it,
  // and that is not tidiness. A tmux server socket is a unix domain socket, whose path
  // must fit in `sockaddr_un.sun_path` -- 104 bytes on macOS -- and tmux appends
  // `/tmux-<uid>/default` to whatever `TMUX_TMPDIR` names. macOS's own `os.tmpdir()`
  // is already about fifty characters of `/var/folders/...`, so a socket under this
  // fixture's deliberately-spacey directory name overruns it and tmux fails with
  // `File name too long` -- which the app then renders as "tmux is installed but the
  // session could not be opened", a true sentence about the wrong problem. Every OTHER
  // path this fixture hands the app still carries a space (KYO-89).
  const sockets = realpathSync(mkdtempSync(join(tmpdir(), "kyo")));
  const marker = `fixture-marker-${Math.random().toString(36).slice(2, 10)}`;

  // A stub `npm`, because "`npm` resolves inside the pane" is half of c1 and the
  // symptom KYO-229 was filed for (`command not found: npm`). A stub
  // rather than the real npm for the reason the whole fixture exists: what is being
  // asserted is that the PANE'S PATH is the login shell's, and a real npm on a
  // developer's machine would be found either way.
  const npm = join(binDir, "npm");
  writeFileSync(npm, "#!/bin/sh\nprintf 'fixture npm\\n'\n", "utf8");
  chmodSync(npm, 0o755);

  if (options.tmux != null) {
    // A wrapper rather than a symlink: tmux resolves its own server socket from
    // `TMUX_TMPDIR`, which this shell exports, and a wrapper keeps the link between
    // "the tmux the app found" and "the server this check talks to" visible here.
    const tmux = join(binDir, "tmux");
    writeFileSync(tmux, `#!/bin/sh\nexec ${shellQuoteForFixture(options.tmux)} "$@"\n`, "utf8");
    chmodSync(tmux, 0o755);
  }

  const shell = join(dir, "a login shell");
  writeFileSync(
    shell,
    [
      "#!/bin/sh",
      "# A login shell this check owns (KYO-229). See tests/fixture.ts.",
      `PATH=${shellQuoteForFixture(binDir)}:/usr/bin:/bin; export PATH`,
      `KYOSEI_FIXTURE_MARKER=${shellQuoteForFixture(marker)}; export KYOSEI_FIXTURE_MARKER`,
      `TMUX_TMPDIR=${shellQuoteForFixture(sockets)}; export TMUX_TMPDIR`,
      "# `-ilc <script>` is exactly how the app asks; anything else is the pty asking",
      "# for an interactive shell, which is what a pane runs.",
      'case "$1" in',
      '  -ilc) shift; exec /bin/sh -c "$1" ;;',
      "esac",
      'exec /bin/sh "$@"',
      "",
    ].join("\n"),
    "utf8",
  );
  chmodSync(shell, 0o755);

  return {
    dir,
    shell,
    binDir,
    marker,
    sockets,
    remove: () => {
      // `removeTree` and not a bare `rmSync` for the reason above it (KYO-238): this
      // directory holds the login shell a pty may still be exec'ing, and the socket
      // directory a tmux server may still be writing into.
      removeTree(dir);
      removeTree(sockets);
    },
  };
}

/**
 * POSIX quoting for the one script this file writes.
 *
 * A local copy rather than `kyo.ts`'s `shellQuote`, and only because the fixture's own
 * temp paths contain spaces by construction -- importing the app's quoting into the
 * thing that builds the app's inputs would make a quoting defect invisible in exactly
 * the direction this repository cares about (KYO-89).
 */
function shellQuoteForFixture(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export interface Fixture {
  /** The project root: also the store root, since these are `--in-repo` stores. */
  root: string;
  /** The temp directory holding it; remove this to clean up. */
  base: string;
  /**
   * The `KYOSEI_HOME` this fixture runs under -- the seam that keeps KYO-114's
   * scratchpad out of the developer's real `~/kyosei/`.
   *
   * This is the CLI's own variable and not an app-only override, which is the point:
   * the app derives the per-project home by asking `kyo worktrees --json`, `kyo`
   * honours `KYOSEI_HOME` when it answers, so setting it here moves the scratchpad
   * through the same seam a consumer would use. There is no test-only branch in the
   * app for the checks to exercise instead of the real path.
   */
  home: string;
  resolution: KyoResolution;
  /** The kyo serve this fixture started over `home`, which serves its project (KYO-459). */
  serve: KyoServeFixture;
  /** The fixture's project on that server: what the store modules take. */
  project: ServedProject;
}

/** Where the app will put this fixture's scratchpad: `<home>/<project dir>/scratchpad.md`. */
export function scratchpadPathFor(fixture: Fixture): string {
  return join(fixture.home, basename(fixture.root), "scratchpad.md");
}

/**
 * The resolver, as the app builds it in this checkout: the working tree answers. The
 * login PATH is given as empty, so an installed `kyo` on the runner's PATH is never the
 * one a fixture ran (KYO-450: that is the only other source there is).
 */
export function testResolution(): KyoResolution {
  return resolveKyo({ startDir: __dirname, path: "" });
}

/** Run `kyo`, throwing with its own stderr/stdout on a nonzero exit. */
export async function kyo(
  resolution: KyoResolution,
  args: string[],
  cwd: string,
): Promise<{ code: number; stdout: string; stderr: string }> {
  const result = await runKyo(resolution, args, { cwd, timeoutMs: 60_000 });
  if (result.code !== 0) {
    throw new Error(
      `kyo ${args.join(" ")} exited ${result.code}: ${result.stderr || result.stdout}`,
    );
  }
  return result;
}

export interface FixtureOptions {
  records?: number;
  /**
   * The project directory's name, which is also the name `kyo serve` serves it under
   * (KYO-459): two projects one server serves need two names. `a project` by default.
   */
  dir?: string;
  /**
   * Whether the project is a git repository. Real ones are; the one check that
   * turns this off is the one about a locator that needs git and a fallback that
   * does not.
   */
  git?: boolean;
}

/**
 * Create a store with `records` records in it.
 *
 * `bug` and not `task`: this store's kind registry makes a task promotable-only,
 * and the fixture has no reason to argue with it.
 */
export async function makeFixture(options: FixtureOptions | number = {}): Promise<Fixture> {
  const {
    records = 2,
    git = true,
    dir = "a project",
  } = typeof options === "number" ? { records: options } : options;
  // `realpathSync`: on macOS `os.tmpdir()` is `/var/...`, a symlink to
  // `/private/var/...`, and the CLI resolves the paths it reports. Without this an
  // assertion comparing the two would fail over a symlink rather than over
  // anything this record is about.
  const base = realpathSync(mkdtempSync(join(tmpdir(), "kyosei desktop ")));
  const root = join(base, dir);
  const resolution = testResolution();
  // `KYOSEI_HOME` keeps `kyo init`'s worktree home inside the fixture rather than
  // in the developer's real ~/kyosei -- and, since KYO-114, keeps the app's session
  // scratchpad there too: the app asks `kyo` where the per-project home is, and this
  // is what `kyo` answers with. Set on `process.env` because that is what the CLI
  // invocations below inherit, and returned so the Electron launches can set it
  // explicitly rather than relying on having inherited it.
  const home = join(base, "kyosei home");
  process.env.KYOSEI_HOME = home;
  await kyo(resolution, ["init", "--in-repo", "--prefix", "FIX", root], base);
  if (git) {
    const run = (...args: string[]) => execFileSync("git", args, { cwd: root, stdio: "ignore" });
    run("init", "-b", "main");
    run("config", "user.email", "checks@example.invalid");
    run("config", "user.name", "kyosei checks");
    run("add", "-A");
    run("commit", "-m", "fixture");
  }
  for (let index = 1; index <= records; index += 1) {
    await kyo(resolution, ["new", "bug", "--title", `Fixture record ${index}`], root);
  }
  // KYO-459: the app reads every project through a kyo serve, so the fixture starts one
  // over its home -- whose registry lists this project, since `kyo init` added it -- and
  // points every launch after this at it, as `KYOSEI_HOME` above points every `kyo`.
  const serve = await startKyoServe(home);
  process.env[SUITE_SERVE_ENV] = serve.url;
  liveFixtures.push({ url: serve.url, home });
  const project = clientFor(serve.address, true).project(root);
  return { root, base, home, resolution, serve, project };
}

/**
 * The fixtures still alive, oldest first. A check that makes a second fixture points
 * every launch at it; removing that fixture must point them BACK at the one still
 * alive, not at nothing -- otherwise every check after it launches against a stopped
 * kyo serve and opens under the "kyo serve is not running" overlay (shell.spec's
 * popped-out check once made the next check fail in every full run this way).
 */
const liveFixtures: { url: string; home: string }[] = [];

/**
 * Point every launch (and every `kyo` this process runs) at `fixture`'s kyo serve and
 * store home: what `makeFixture` does for the fixture it has just made, for a check that
 * makes several and serves them from one (KYO-459).
 */
export function useServe(fixture: Fixture): void {
  process.env[SUITE_SERVE_ENV] = fixture.serve.url;
  process.env.KYOSEI_HOME = fixture.home;
}

/**
 * Add `other`'s project to `fixture`'s registry (`kyo projects add`, the CLI's own
 * writer), so the one kyo serve over `fixture`'s home serves both. The two must be
 * made with different `dir`s: a registry refuses two projects with one name.
 */
export async function serveAlso(fixture: Fixture, other: Fixture): Promise<void> {
  const held = process.env.KYOSEI_HOME;
  process.env.KYOSEI_HOME = fixture.home;
  try {
    await kyo(fixture.resolution, ["projects", "add", other.root], fixture.base);
  } finally {
    if (held === undefined) delete process.env.KYOSEI_HOME;
    else process.env.KYOSEI_HOME = held;
  }
}

/** Remove a fixture, and stop its kyo serve, retrying the walk a writer can lose (KYO-238). */
export function removeFixture(fixture: Fixture): void {
  if (fixture.serve.child.exitCode === null && fixture.serve.child.signalCode === null) {
    fixture.serve.child.kill("SIGTERM");
  }
  const at = liveFixtures.findIndex((live) => live.url === fixture.serve.url);
  if (at >= 0) liveFixtures.splice(at, 1);
  if (process.env[SUITE_SERVE_ENV] === fixture.serve.url) {
    const previous = liveFixtures[liveFixtures.length - 1];
    if (previous === undefined) {
      delete process.env[SUITE_SERVE_ENV];
    } else {
      process.env[SUITE_SERVE_ENV] = previous.url;
      process.env.KYOSEI_HOME = previous.home;
    }
  }
  removeTree(fixture.base);
}

function allocationsPath(fixture: Fixture): string {
  return join(fixture.root, "kyosei", "export", "_allocations.json");
}

export function readAllocations(fixture: Fixture): Record<string, number> {
  return JSON.parse(readFileSync(allocationsPath(fixture), "utf8")) as Record<string, number>;
}

/**
 * Move one id counter in `kyosei/export/_allocations.json`.
 *
 * This is how the four currency states are produced: `kyosei.currency` decides the
 * direction by comparing that snapshot against the `allocations` table, so raising
 * the on-disk counter is "the export recorded something the database did not" and
 * lowering it is the reverse. Nothing here computes a direction -- the CLI is
 * asked, and the point of the fixture is that it is asked over a store whose two
 * sides are known to disagree in a known way.
 */
export function setAllocation(fixture: Fixture, scope: string, value: number): void {
  const path = allocationsPath(fixture);
  const data = JSON.parse(readFileSync(path, "utf8")) as Record<string, number>;
  data[scope] = value;
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

/** Move a record's export document aside: counters agree, record sets do not. */
export function hideExportedRecord(fixture: Fixture, id: string): void {
  const path = join(fixture.root, "kyosei", "export", `${id}.json`);
  renameSync(path, `${path}.hidden`);
}

/**
 * Stamp a schema_version into the store's `meta` table (KYO-184).
 *
 * The one way to produce a real schema refusal in a check: `kyosei.store.open_store`
 * compares the db's stamp against the build's, and a number ABOVE the build's is the
 * direction whose remedy must never be followed (KYO-168) and whose payload the app
 * has to be able to read (KYO-178). Written straight into the file, on
 * `setAllocation`'s own reasoning one seam over: nothing here decides anything, it
 * puts the store into a state whose verdict the CLI is then asked for.
 *
 * Returns what the stamp was, so a caller can say by how much it moved.
 */
export function setSchemaVersion(fixture: Fixture, value: number): number {
  const path = join(fixture.root, "kyosei", "kyosei.db");
  const db = new DatabaseSync(path);
  try {
    const row = db.prepare("SELECT value FROM meta WHERE key = 'schema_version'").get() as
      | { value?: unknown }
      | undefined;
    const before = Number(row?.value ?? 0);
    db.prepare("UPDATE meta SET value = ? WHERE key = 'schema_version'").run(String(value));
    return before;
  } finally {
    db.close();
  }
}

/**
 * Query the store's database out of band, so an assertion about what the app read
 * is checked against the file rather than against the app's own reader.
 */
export function sqliteScalar(dbPath: string, sql: string): string {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  try {
    const row = db.prepare(sql).get() as Record<string, unknown> | undefined;
    const first = Object.values(row ?? {})[0];
    return String(first ?? "");
  } finally {
    db.close();
  }
}

// ================================================ a store records mode can read
//
// The plain fixture above is two or three records with nothing on them, which is
// everything KYO-111's seams needed. Records mode needs a store with *shape*: more
// than one kind, more than one status, a scope on some records and none on others,
// an open gate with a claim beside it, and a saved base per grouping.
//
// The bases are the load-bearing part: one per ribbon grouping, each DECLARING that
// grouping, so the oracle over `shared/grouping.ts` has a real engine answer to
// compare the port against for kind, scope and BOTH status fields -- the stored
// column and the role word. Both, because KYO-121 is the difference between them:
// this store holds a knowledge record filed `open` that reads `current` (FIX-2), so
// `by-status` and `by-standing` return provably different answers over the same rows
// and a check can say which one the ribbon actually asked for. KYO-120 gave
// `kyo base run` a `--group-by`, so these are no longer the only way to obtain those
// answers -- they are kept as the INDEPENDENT way. An oracle that asked for its
// expected value through the same flag the tree now uses would still pass if the flag
// and the port drifted together. Writing them into a throwaway fixture is not editing
// a project's store config: this store is created and destroyed by the check using it.

/** The bases every records-mode check runs against, one per ribbon grouping. */
export const FIXTURE_BASES = `bases:
  everything:
    summary: Every record in the store, grouped by kind.
    source: records
    columns: [id, kind, scope, status, standing, gates_open, title]
    sort: [seq]
    group-by: kind
  by-status:
    summary: Every record in the store, grouped by the RAW stored status column.
    source: records
    columns: [id, kind, scope, status, standing, title]
    sort: [seq]
    group-by: status
  by-standing:
    summary: Every record in the store, grouped by the role word the CLI prints.
    source: records
    columns: [id, kind, scope, status, standing, title]
    sort: [seq]
    group-by: standing
  by-scope:
    summary: Every record in the store, grouped by scope.
    source: records
    columns: [id, kind, scope, status, title]
    sort: [seq]
    group-by: scope
  open-work:
    summary: Everything open, oldest first.
    source: records
    filters:
      status: open
    columns: [id, kind, title]
    sort: [seq]
  unpromoted:
    summary: Idea files nothing has been promoted out of yet.
    source: ideas
    filters:
      stamped: "false"
    columns: [id, title, section_count]
`;

const FIXTURE_SCOPES = `scopes:
  alpha:
    summary: The first half of the fixture's system.
  beta:
    summary: The second half of the fixture's system.
`;

/** Overwrite one of the store's registry files. */
export function writeStoreFile(fixture: Fixture, name: string, body: string): void {
  writeFileSync(join(fixture.root, "kyosei", name), body, "utf8");
}

/** Write a file of prose and return its path, for a `--*-file` flag (law 5). */
function proseFile(fixture: Fixture, name: string, body: string): string {
  const path = join(fixture.base, name);
  writeFileSync(path, body, "utf8");
  return path;
}

export interface RecordsFixture extends Fixture {
  /** The bases this store declares, in the order the CLI lists them. */
  bases: string[];
}

// ============================================ KYO-203: a store for the gate filter

const GATE_FILTER_TAGS = `gate-tags:
  ui-review:
    summary: A human has to look at a UI element.
  deploy-risk:
    summary: Something risky is about to deploy.
`;

const GATE_FILTER_BASES = `bases:
  everything:
    summary: Every record in the store, unfiltered.
    source: records
    columns: [id, kind, scope, status, standing, gates_open, gate_tags, gate_types, title]
    sort: [seq]
    group-by: kind
`;

export interface GateFilterFixture extends Fixture {
  /** Carries one open \`awaiting:event\` gate tagged \`ui-review\`. */
  eventGated: string;
  /** Carries one open \`awaiting:human-review\` gate tagged \`deploy-risk\`. */
  reviewGated: string;
  /** No gate at all -- the "no open gate" entry's one member. */
  ungated: string;
}

/**
 * A small store built for KYO-203's own desktop test: two gate TYPES, two gate
 * TAGS, and a record with no open gate at all -- the shape c5 asks for.
 *
 * Deliberately its OWN store and not an addition to `makeRecordsFixture`'s: that
 * fixture's FIX-1 is the one record in ITS store with an open gate ("nothing else
 * in this store has any", asserted in `smoke.spec.ts`), and seeding a second and
 * third gated record into it would falsify that assertion for a reason that has
 * nothing to do with what it is about.
 */
export async function makeGateFilterFixture(): Promise<GateFilterFixture> {
  const fixture = await makeFixture({ records: 0 });
  writeStoreFile(fixture, "gate-tags.yml", GATE_FILTER_TAGS);
  writeStoreFile(fixture, "bases.yml", GATE_FILTER_BASES);
  const run = (args: string[]) => kyo(fixture.resolution, args, fixture.root);
  const idOf = (stdout: string): string =>
    (JSON.parse(stdout) as { record: { id: string } }).record.id;

  const eventGated = idOf(
    (await run(["new", "bug", "--title", "Held on a CI run", "--json"])).stdout,
  );
  await run([
    "gate",
    eventGated,
    "awaiting:event",
    "--do",
    "the nightly CI run goes green",
    "--tag",
    "ui-review",
  ]);

  const reviewGated = idOf(
    (await run(["new", "bug", "--title", "Held on a human look", "--json"])).stdout,
  );
  await run(["gate", reviewGated, "awaiting:human-review", "--tag", "deploy-risk"]);

  const ungated = idOf(
    (await run(["new", "bug", "--title", "Nothing is holding this one", "--json"])).stdout,
  );

  return { ...fixture, eventGated, reviewGated, ungated };
}

// ================================================ a vault ideas mode can read (KYO-114)
//
// Every file below exists to make one assertion possible and the comment says which.
// The set is chosen to cover the four things the app's port of `ideas.py` has to get
// right, so that comparing the port against the engine over this vault is a real
// comparison and not a tautology over one simple file:
//
//   frontmatter of all three value shapes (block list, flow list, one-line scalar,
//   and a key declared with no value at all);
//   headings at several levels, INCLUDING a `#` inside a fenced code block and a
//   `#tag` with no space -- neither of which is a heading, and both of which a naive
//   scan reads as one;
//   a promotion stub, in the exact bytes `ideas.promotion_stub` writes;
//   a file in a subfolder, and a non-markdown file, so the tree has both.

/** Every idea file the fixture vault holds, as `path -> contents`. */
export const FIXTURE_IDEAS: Record<string, string> = {
  // Frontmatter in three shapes, plus a fenced block whose `#` is not a heading and
  // a `#tag` that is not one either. Unstamped, so the `unpromoted` base returns it.
  "a first idea.md": `---
tags:
  - vault
  - deep/nested
author: someone
empty:
flow: [one, two]
---

# A first idea

Some prose with a #loose tag in it.

## A subsection

\`\`\`sh
# this is a shell comment, not a heading
echo hi
\`\`\`

### A deeper heading
`,
  // A file with NO frontmatter at all -- law 13's zero ceremony, and the case where
  // `split_frontmatter` must return no block rather than an empty one.
  "bare.md": `# Bare

No frontmatter here at all.
`,
  // A section-level promotion stub, written in `ideas.promotion_stub`'s exact bytes
  // (middle dots and all). This is what the tree's badge and the outline's mark are
  // asserted against, and what `stubRecordIds` must find.
  "in a folder/promoted already.md": `---
promoted:
  - FIX-1
promoted_at:
  - "2026-08-27T16:30:08.798Z"
tags: single-scalar-tag
---

# Promoted already

> **Promoted -> FIX-1** · 2026-08-27 · #promoted #FIX-1
> Text now lives in the record.

## Still mine

This section was never promoted.
`,
  // An opening `---` that is never closed: a horizontal rule or an unfinished edit,
  // and NOT frontmatter. The one case where "looks like a property block" is wrong.
  "in a folder/unclosed.md": `---
not: frontmatter

# Unclosed
`,
};

/** A file in the vault that is not markdown -- listed by the tree, never opened. */
export const FIXTURE_IDEAS_OTHER = "in a folder/a picture.png";

/**
 * Write the vault, and the leftover in-vault scratchpad the CLI has always skipped.
 *
 * `ideas/scratchpad.md` is deliberately among them: `ideas.idea_files` excludes a
 * file of that name at any depth, so a consumer's vault really can hold one, and the
 * app's tree must skip it for the same reason rather than listing it as an idea.
 */
export function seedIdeas(fixture: Fixture): void {
  const ideas = join(fixture.root, "ideas");
  for (const [path, body] of Object.entries(FIXTURE_IDEAS)) {
    const target = join(ideas, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, body, "utf8");
  }
  writeFileSync(join(ideas, FIXTURE_IDEAS_OTHER), "not markdown", "utf8");
  writeFileSync(
    join(ideas, "scratchpad.md"),
    "# an in-vault scratchpad the CLI has always skipped\n",
    "utf8",
  );
  // An empty folder, so the tree can prove a folder holding nothing still appears --
  // and so the new-idea modal's destination list is the folders that exist rather
  // than the folders with something in them.
  mkdirSync(join(ideas, "an empty folder"), { recursive: true });
}

/**
 * A store with the shape records mode renders.
 *
 * Every record here exists to make one assertion possible, and the comments say
 * which. Nothing is filler: a fixture row nobody asserts against is a row that can
 * rot without any check noticing.
 */
export async function makeRecordsFixture(): Promise<RecordsFixture> {
  const fixture = await makeFixture({ records: 0 });
  const run = (args: string[]) => kyo(fixture.resolution, args, fixture.root);

  writeStoreFile(fixture, "scopes.yml", FIXTURE_SCOPES);
  writeStoreFile(fixture, "bases.yml", FIXTURE_BASES);

  const criteria = proseFile(
    fixture,
    "criteria.txt",
    "the reproduction reproduces\nthe fix holds under load\n",
  );
  const touches = proseFile(fixture, "touches.txt", "kyosei/config.yml\n");

  // FIX-1: a bug with a scope, an OPEN gate and a claim -- the pair KYO-88 is about,
  // and the record the gate badge and the claims-beside-gates rendering are checked on.
  await run([
    "new",
    "bug",
    "--title",
    "A defect with a gate",
    "--scope",
    "alpha",
    "--criteria-file",
    criteria,
  ]);
  await run(["gate", "FIX-1", "holding-for", "--do", "the upstream release ships"]);
  await run([
    "claim",
    "FIX-1",
    "--level",
    "verified",
    "--method",
    "read the upstream changelog",
    "--file",
    proseFile(
      fixture,
      "claim.txt",
      "The upstream release shipped on the 3rd, so this gate may already be answered.\nA second line, so the one-line rendering has something to elide.\n",
    ),
  ]);

  // FIX-2: a knowledge-archetype record, so "kind" has a third value and the tree is
  // not grouping a store where every row is the same. It is also the store's LONGEST
  // record, deliberately: KYO-122.c4 is about opening a record too tall for the pane,
  // and a store whose every record fits cannot express the difference between a pane
  // that scrolls and a pane that grows until it pushes the terminal off the window.
  await run(["new", "evidence", "--title", "Some evidence with no scope"]);
  await run([
    "edit",
    "FIX-2",
    "--description-file",
    proseFile(
      fixture,
      "long.md",
      `# A record longer than any pane\n\n${Array.from(
        { length: 120 },
        (_, index) =>
          `Paragraph ${index + 1}. This record exists to be too tall for the editor pane, ` +
          "so that a check can tell a pane that scrolls from a pane that grows.",
      ).join("\n\n")}\n`,
    ),
  ]);

  // FIX-3: a bug with NO scope -- the null group, which the engine keeps separate
  // from the empty string and which the app's port must keep separate too.
  await run(["new", "bug", "--title", "A defect with no scope", "--criteria-file", criteria]);

  // FIX-4: a task in the other scope, moved off `open`, so "status" has two values.
  await run([
    "spawn",
    "FIX-1",
    "--kind",
    "task",
    "--title",
    "A task spawned from the defect",
    "--scope",
    "beta",
    "--criteria-file",
    criteria,
    "--touches-file",
    touches,
  ]);
  await run(["move", "FIX-4", "working"]);

  // FIX-3 is then taken all the way to KYO-87's state: dispatched, reported, every
  // criterion verified met -- and still `open`, because nobody moved it. That is
  // "finished but not moved", it is the class the brief used to count as
  // dispatchable, and without a record actually in it the check that the app keeps
  // it apart from `ready` would be asserting over an empty set.
  const head = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: fixture.root,
    encoding: "utf8",
  }).trim();
  await run(["dispatch", "FIX-3"]);
  await run(["verify", "FIX-3.c1", "--verdict", "met", "--method", "machine-checked"]);
  await run(["verify", "FIX-3.c2", "--verdict", "met", "--method", "machine-checked"]);
  await run([
    "report",
    "FIX-3.d1",
    "--file",
    proseFile(
      fixture,
      "report.json",
      `${JSON.stringify(
        {
          dispatch_id: "FIX-3.d1",
          record_id: "FIX-3",
          worked_from_sha: head,
          summary: "A fixture report, so this record has a reported dispatch behind it.",
          commits: [],
          touched_files: [],
          premise_false: [],
          disagreements: [],
          corrected_numbers: [],
          criteria_verdicts: [
            { criterion_id: "FIX-3.c1", verdict: "met", method: "machine-checked" },
            { criterion_id: "FIX-3.c2", verdict: "met", method: "machine-checked" },
          ],
        },
        null,
        2,
      )}\n`,
    ),
  ]);

  // Typed edges, so the graph rendering has a graph (KYO-113). `spawn` already left
  // one behind -- FIX-4 produced-by FIX-1 -- and each of these adds exactly one thing
  // a check needs:
  //
  //   a `blocks` CHAIN, so filtering to blocks produces a layered dependency graph
  //   with real depth (FIX-3 -> FIX-1 -> FIX-4) rather than one isolated pair;
  //   a MUTUAL pair of a second type, so `all` contains a cycle the layering must
  //   refuse -- `duplicates` is legitimately mutual, which is why the CLI's cycle
  //   check exempts it, and it is the honest way to produce the hairball case;
  //   an edge whose other end is OUTSIDE a narrower base: `open-work` does not
  //   return FIX-4, so FIX-1's blocks edge leads out of that query and the graph
  //   has to say so rather than drawing FIX-1 as unblocking.
  await run(["link", "FIX-1", "blocks", "FIX-4"]);
  await run(["link", "FIX-3", "blocks", "FIX-1"]);
  await run(["link", "FIX-2", "duplicates", "FIX-3"]);
  await run(["link", "FIX-3", "duplicates", "FIX-2"]);

  // FIX-5: the store's one READY record. Since KYO-570 an unsettled `blocks` source
  // holds its target back, so the chain above makes FIX-4 `blocked` (FIX-1 is open) --
  // and the chain stays, because the link-graph checks read it. This record is what
  // `kyo next` offers instead (KYO-571): a task with touches, no gate, no link, and
  // filed with `new` rather than `spawn` so it adds no edge to the graph.
  await run([
    "new",
    "task",
    "--title",
    "A task nothing holds back",
    "--scope",
    "beta",
    "--criteria-file",
    criteria,
    "--touches-file",
    touches,
  ]);

  // Two session briefs with a shift note between them, so the calendar tool has a
  // list to render and the second brief has a non-empty window to reconstruct.
  await run(["brief", "--session-start"]);
  await run([
    "note",
    "--shift",
    "--file",
    proseFile(fixture, "note.txt", "A shift note the calendar tool should surface verbatim.\n"),
  ]);
  await run(["brief", "--session-start"]);

  // The vault, written before the bases are asked for: the `unpromoted` base above
  // is over the ideas source, and a base run against an empty `ideas/` would give the
  // checks nothing to compare the app's port against.
  seedIdeas(fixture);

  return {
    ...fixture,
    bases: ["by-scope", "by-standing", "by-status", "everything", "open-work", "unpromoted"],
  };
}

/**
 * Open a tab kind the way a reader does since KYO-248 took the tab-opening icons off
 * the rail: a new tab (the strip's "+"), then the home page's card for it. A card the
 * home tab can BECOME (a record, the scratchpad) navigates that tab; a singleton (the
 * terminal, the claude view) opens beside it and the home tab closes itself.
 */
export async function openKind(page: Page, kind: string, arg = ""): Promise<void> {
  await page.locator('[data-action="new-tab"]').first().click();
  await page
    .locator(
      `[data-region="tab-body"][data-tab-kind="home"]:not([hidden]) [data-home-kind="${kind}"][data-home-arg="${arg}"]`,
    )
    .click();
}
