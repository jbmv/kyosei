// The pty-free half of the terminal pane's machinery: what a pane's environment is,
// which tmux it would use and what it says when there is none, and the one funnel
// every tmux argv this app builds goes through (KYO-388).
//
// WHY THIS IS A FILE OF ITS OWN. Everything here used to live in `terminal.ts`, which
// imports `@lydell/node-pty` at its top. That was harmless while only the app's own
// main process read it. It stopped being harmless when this machine's server became a
// headless entry run under `ELECTRON_RUN_AS_NODE` (KYO-378): the server's service
// holds the controller launcher (`controller.ts`, KYO-383), the launcher needs the
// pane's environment, `resolveTmux` and the socket, and importing them from
// `terminal.ts` put the pty -- a native addon only the app's terminals load -- into
// the headless bundle's module graph. `server-main.spec.ts` checks that graph
// (KYO-378.c5) and failed on it.
//
// A lazy `require` of the pty would not have satisfied that check, and should not:
// esbuild's metafile lists a lazy require as an import too, and a module graph that
// only avoids the addon on the paths somebody happened to take is not a graph without
// it. So the split is by what the code needs, not by who calls it: nothing below
// imports a pty, and nothing below may -- `server-main.spec.ts` fails the day one
// arrives through any importer of this file.
//
// KYO-459 deleted that headless server and its spec (`kyo serve` is the server now);
// the split stands, because `controller.ts` still has no business loading a pty.
//
// `terminal.ts` re-exports every name that moved, so its own importers (`main.ts`
// and the checks) read what they always read. `controller.ts` and `tmuxctl.ts` import
// from here directly, because they are the ones the headless server reaches. This is
// the move KYO-384 had planned for the socket and `tmuxArgs`, done now because the
// headless server needed it.

import { accessSync, constants, realpathSync } from "node:fs";
import { delimiter, join } from "node:path";
import { shellQuote } from "./kyo";
import {
  appendShellEnvLog,
  captured,
  captureLoginEnvironment,
  type ShellEnvironment,
  terminalEnvironment,
  worthAskingAgain,
} from "./shellenv";

// ------------------------------------------ the login shell's environment (KYO-229)
//
// Asked once per process and cached, because it is a property of the machine and the
// user rather than of any pane, and because it costs a shell start-up. Lazy rather
// than at load: `main.ts` must be able to put a window on screen without waiting for
// anybody's `.zshrc`, so the first thing that needs a real PATH pays for it -- which
// is the first pane, or the first question about tmux.
//
// A project switch does NOT clear it. The login shell's environment is the user's,
// not the project's, and re-asking on a switch would spend a shell start-up to get
// the same answer back.
//
// WHAT A FAILURE IS CACHED FOR, WHICH IS NOTHING (KYO-266). "Once per process"
// included the failures, and that is the defect: one launch's capture did not come
// back, so `ptyEnvironment()` fell back to `process.env` -- `launchd`'s
// four directories from a Finder launch -- and `findTmux` searched it and found
// nothing. Every pane of that app instance was then a bare shell on a PATH with no
// `tmux`, no `npm`, no `claude`, for as long as the app ran, and the only way out was
// a relaunch. The capture itself takes 0.7-1.0s on this machine and normally
// succeeds; what turned one bad second into a whole session was the memo.
//
// So the memo keeps a SUCCESS and re-asks a failure, on `worthAskingAgain`'s rule --
// which `noTmuxReason` reads too, so the sentence a bare pane prints and the thing
// the next pane actually does cannot come apart. The cost of the retry is bounded and
// stated: one login-shell start-up per pane opened while the shell is still failing,
// paid by the pane that asks, and nothing retries in a loop -- the only thing that
// re-asks is a human opening another pane.

/**
 * Memoise a capture, keeping a success for good and re-asking a failure.
 *
 * A factory rather than a bare module-level `let`, so the rule is a value a check can
 * drive with a stub shell instead of a module state a check would have to reset. The
 * memo below is the one the app uses; the same function under test is the same rule.
 */
export function retryingCapture(capture: () => ShellEnvironment): () => ShellEnvironment {
  let cached: ShellEnvironment | null = null;
  return () => {
    if (cached === null || worthAskingAgain(cached)) cached = capture();
    return cached;
  };
}

// Where the outcome of each ask is written so a Finder launch keeps it (KYO-266.c4).
// Null until `main.ts` says -- only it can ask Electron for `userData` -- and a null
// costs the record, never the pane.
let captureLogDir: string | null = null;
let captureAttempts = 0;

/** Keep every capture outcome under `dir`. Called once, by `main.ts`, at startup. */
export function logShellEnvironmentIn(dir: string | null): void {
  captureLogDir = dir;
}

const askLoginShell = retryingCapture((): ShellEnvironment => {
  const environment = captureLoginEnvironment();
  captureAttempts += 1;
  if (captureLogDir !== null) appendShellEnvLog(captureLogDir, environment, captureAttempts);
  return environment;
});

/**
 * The environment a terminal on this machine would have. Captured once, if it can be.
 */
export function loginEnvironment(): ShellEnvironment {
  return askLoginShell();
}

/**
 * The environment every pty in this app is spawned with, before the per-pane edits.
 *
 * One definition, two callers (law 3): the pty itself, and the tmux lookup that has to
 * search the same PATH the pty will have. Before KYO-229 those were both
 * `process.env`, which from a Finder launch is `launchd`'s four-directory minimum --
 * so the app searched a PATH no terminal on the machine has ever had, found no tmux,
 * and told the reader tmux might not be installed.
 */
export function ptyEnvironment(): Record<string, string> {
  return terminalEnvironment(process.env, loginEnvironment());
}

// ------------------------------------------------------------ KYOSEI_TMUX (KYO-115)

/**
 * The variable that names a tmux binary, or turns the whole mechanism off.
 *
 * Not a test hook: a macOS app launched from the Finder inherits a minimal PATH
 * that has never heard of `/opt/homebrew/bin`, so "the app cannot find the tmux
 * that is plainly installed" is the ordinary case for a packaged build rather than
 * an exotic one, and `off` is how someone who wants a private shell in this pane
 * says so. The checks then drive the same seam the consumer does -- as
 * `KYOSEI_PROJECT_ROOT` and `KYOSEI_HOME` already are -- rather than a branch that
 * exists only when a test is watching.
 */
export const TMUX_ENV = "KYOSEI_TMUX";

const TMUX_OFF = new Set(["off", "0", "no", "none", "false"]);

// ------------------------------------------------ how this app was launched (KYO-185)
//
// The variable above is the remedy for the case it was created for, and until this
// record the message that names it named the wrong INSTRUMENT for half its readers.
// `export KYOSEI_TMUX=...` is a real remedy in a terminal: the reader is in a shell,
// and that shell is this process's parent, so the export reaches the next launch.
// From the Finder there is no shell in the loop at all -- the app was started by
// `launchd` -- and the sentence names an action its reader cannot take, in exactly
// the situation the variable exists for.
//
// Which of the two it is, is a FACT this process holds rather than a question for
// the reader (c2), and the fact is not a heuristic: it is whether this process has a
// terminal attached to it. That is the same thing as "is there a shell in the loop
// whose environment this app inherited", which is the same thing as "can an `export`
// reach it" -- one question, asked once, answered by the operating system.
//
//   `npm start` from a terminal          stdio is that terminal; `export` works.
//   `open -a Kyosei` from a terminal     launchd starts it; the reader's shell is
//                                        NOT its parent, so `export` reaches
//                                        nothing, and the launchctl form is right.
//   double-click in the Finder           the same, and the ordinary case.
//
// No PATH is synthesised anywhere below (c4), and nothing here guesses where tmux
// might be installed: what the launch origin changes is which sentence is printed
// and which of "not installed" and "not visible from this launch" the message is
// entitled to claim.

/** Where this app was started from, as far as it changes what a reader can do. */
export type LaunchOrigin = "terminal" | "gui";

export interface LaunchFacts {
  /** Is a terminal attached to any of this process's three standard streams? */
  tty: boolean;
  /** `process.platform`. Only macOS has `launchctl setenv`. */
  platform: string;
}

/**
 * Read the launch facts off the running process.
 *
 * Split from the decision below so that the decision is a pure function a check can
 * drive both ways without launching two applications.
 */
export function launchFacts(): LaunchFacts {
  return {
    tty: Boolean(process.stdin.isTTY || process.stdout.isTTY || process.stderr.isTTY),
    platform: process.platform,
  };
}

export function launchOrigin(facts: LaunchFacts = launchFacts()): LaunchOrigin {
  return facts.tty ? "terminal" : "gui";
}

/**
 * How to put `KYOSEI_TMUX` where this app will see it, spelled for this launch.
 *
 * The macOS GUI form is `launchctl setenv`, which sets a variable for subsequently
 * launched GUI applications -- so it is followed by a relaunch, and the message says
 * so rather than leaving the reader to wonder why nothing changed. Everywhere else
 * with no terminal attached (a Linux `.desktop` entry, say) there is no single
 * command to name, and inventing one would be worse than saying what has to be true:
 * the variable must be in the environment the app is launched from.
 */
export function setEnvInstruction(value: string, facts: LaunchFacts = launchFacts()): string {
  if (launchOrigin(facts) === "terminal") {
    return `export ${TMUX_ENV}=${shellQuote(value)} in that shell and reopen this pane`;
  }
  if (facts.platform === "darwin") {
    return (
      `run \`launchctl setenv ${TMUX_ENV} ${shellQuote(value)}\` in any terminal and ` +
      "relaunch this app -- that sets it for GUI applications launched afterwards, " +
      "which is what this one is"
    );
  }
  return (
    `set ${TMUX_ENV}=${shellQuote(value)} in the environment this app is launched from ` +
    "(the desktop entry or session environment that starts it) and relaunch it"
  );
}

/** And how to take it away again, spelled for this launch. */
export function unsetEnvInstruction(facts: LaunchFacts = launchFacts()): string {
  if (launchOrigin(facts) === "terminal") return `unset ${TMUX_ENV} in that shell`;
  if (facts.platform === "darwin") {
    return `run \`launchctl unsetenv ${TMUX_ENV}\` and relaunch this app`;
  }
  return `remove ${TMUX_ENV} from the environment this app is launched from and relaunch it`;
}

/** The placeholder the instructions above use when there is no path to name yet. */
export const TMUX_PATH_PLACEHOLDER = "/path/to/tmux";

/**
 * Find a tmux binary without running one.
 *
 * A PATH scan and nothing else: `tmux -V` would be a subprocess between the human
 * opening the pane and the pane having a shell in it, and the record is explicit
 * that detection may not block startup. The cost of not executing it is that a
 * `tmux` on PATH which cannot actually run is discovered at spawn time instead --
 * where it surfaces as the pane's own failure with tmux's own message, which is a
 * better place for it than a startup probe that swallows the reason.
 */
export function findTmux(env: Record<string, string | undefined> = process.env): string | null {
  if (process.platform === "win32") return null;
  for (const dir of (env.PATH ?? "").split(delimiter)) {
    if (dir === "") continue;
    const candidate = join(dir, "tmux");
    try {
      accessSync(candidate, constants.X_OK);
      return candidate;
    } catch {
      // Not here, or not executable by us; keep walking PATH in its own order.
    }
  }
  return null;
}

/** Is this file there and executable by us? */
function isExecutable(path: string): boolean {
  try {
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Which tmux this pane will use, and -- when none -- the sentence the pane says.
 *
 * A binary named in `KYOSEI_TMUX` that cannot be executed does *not* fall through
 * to the PATH scan: someone who named a path has said which tmux they mean, and
 * quietly using a different one would be the app answering a question it was not
 * asked (law 6). It refuses by name instead.
 *
 * KYO-185: every sentence below that names the variable spells the instruction for
 * THIS launch (`setEnvInstruction` / `unsetEnvInstruction`). The facts are a second
 * defaulted argument rather than something read at the point of use, so a check can
 * drive both launches without launching two applications.
 */
export function resolveTmux(
  env: Record<string, string | undefined> = ptyEnvironment(),
  facts: LaunchFacts = launchFacts(),
  environment: ShellEnvironment = loginEnvironment(),
): {
  binary: string | null;
  reason: string | null;
} {
  const named = (env[TMUX_ENV] ?? "").trim();
  if (named !== "") {
    if (TMUX_OFF.has(named.toLowerCase())) {
      return {
        binary: null,
        reason: `${TMUX_ENV}=${named}, so this pane is a plain shell of its own by request: what you type here is not visible in any other terminal, and nothing running elsewhere is visible here. ${ENDS_AT_QUIT} To undo that, ${unsetEnvInstruction(facts)}.`,
      };
    }
    if (isExecutable(named)) return { binary: named, reason: null };
    return {
      binary: null,
      reason: `${TMUX_ENV} names ${named}, which is not an executable file, so there is no shared session -- this pane is a shell of its own. ${ENDS_AT_QUIT} To search PATH instead, ${unsetEnvInstruction(facts)}.`,
    };
  }
  const found = findTmux(env);
  if (found !== null) return { binary: found, reason: null };
  return { binary: null, reason: noTmuxReason(facts, environment) };
}

/**
 * What every app-owned shell owes its reader, in one sentence (KYO-229.c3).
 *
 * The two panes look identical -- a shell is a shell -- and the difference between
 * them is what happens at quit: a tmux client detaches and leaves the controller
 * session running, and a shell this app owns is killed by `endSessions` on the way
 * out, taking whatever was running in it. A reader who starts `claude` in the wrong
 * one of those loses it to a Cmd-Q, and nothing on screen would have said so.
 */
export const ENDS_AT_QUIT =
  "This shell ends when this app quits, and anything running in it ends with it.";

/**
 * The remedy for a capture that merely failed, named where the reader is (KYO-266.c3).
 *
 * A user who met this pane read it as a tmux problem and then as a `.zshrc`
 * problem, because those are the two things the sentence talked about. What it never
 * said is the one thing that was true: the ask had failed, it was about to be made
 * again, and another pane was the whole of the fix. Until KYO-266 that was also a lie
 * waiting to be told -- the failure was cached, so no later pane asked anything -- and
 * the sentence and the retry ship together for that reason.
 */
export const WILL_ASK_AGAIN =
  "This app asked once and did not get an answer; it asks again the next time a pane " +
  "opens, so if that shell was only slow or the machine was busy, open another pane " +
  "here -- you do not have to relaunch the app.";

/**
 * The one change that makes a session outlive the app, named where a reader is.
 *
 * macOS only, because `brew` is: on any other platform the app names the package
 * rather than a command it has no business predicting. Bundling a tmux inside the
 * app was CONSIDERED and deliberately deferred to a follow-on record (KYO-229.c3);
 * nothing here ships one, and this sentence is the whole of the app's answer to a
 * machine without tmux.
 */
export function installTmuxInstruction(facts: LaunchFacts = launchFacts()): string {
  return facts.platform === "darwin"
    ? "`brew install tmux` is the one change that makes a session here outlive the app"
    : "installing tmux is the one change that makes a session here outlive the app";
}

/**
 * The no-tmux sentence, in the two forms its two audiences can act on (KYO-185).
 *
 * The remedies are the same two either way -- install tmux, or point the app at the
 * one that is installed -- and only the second's INSTRUMENT differs. What also
 * differs, and is the part a reader most needs, is what the app is entitled to claim
 * about why nothing was found:
 *
 *   from a terminal   the PATH scanned is the reader's own shell's PATH, so "it is
 *                     not on the PATH you launched me from" is a strong statement
 *                     and the two possibilities are near enough to state together.
 *   from the Finder   the PATH is the minimal one `launchd` hands a GUI application,
 *                     which has never included `/opt/homebrew/bin` or
 *                     `/usr/local/bin`. A tmux that IS installed is invisible here,
 *                     and saying "tmux is not installed" would be a claim this app
 *                     cannot make from this launch. So it says which of the two it
 *                     cannot tell, and why (c4).
 *
 * Nothing here synthesises a PATH: those two directories are NAMED in a sentence
 * addressed to a person, and no code below or above this line adds either to
 * anything the app searches or spawns with. That was KYO-176.d1's decision and it
 * stands -- guessing on the user's behalf is what makes the app's behaviour depend
 * on a hand-maintained list and hides exactly the difference this message states.
 */
export function noTmuxReason(
  facts: LaunchFacts = launchFacts(),
  environment: ShellEnvironment = loginEnvironment(),
): string {
  // KYO-266.c3: when the capture failed in a way another ask could fix, the remedy
  // the reader needs is one pane away, and nothing on screen said so -- a user's
  // first remedy for exactly this was to hand-edit `.zshrc`, and the one
  // that worked was a relaunch. Guarded by `worthAskingAgain` rather than by "the
  // capture failed", because `turned-off` and `unsupported-platform` will answer the
  // same way forever and their own `why` already names the change that helps.
  const askAgain = worthAskingAgain(environment) ? ` ${WILL_ASK_AGAIN}` : "";
  const shared =
    "so this pane is a shell of its own: what you type here is not visible in " +
    "any other terminal and what runs there is not visible here.";
  const point = `${setEnvInstruction(TMUX_PATH_PLACEHOLDER, facts)}`;
  const install = installTmuxInstruction(facts);
  if (captured(environment)) {
    // KYO-229: the PATH searched is the one `environment.shell` gives its own
    // terminals, whichever way this app was launched. That is a stronger claim than
    // either of KYO-185's two, and it is the claim this branch is entitled to make --
    // "not on your login shell's PATH" is what was actually measured, and the reader
    // can check it by typing `which tmux` in any terminal they own.
    return (
      `no tmux on ${environment.shell}'s own PATH, ${shared} ${ENDS_AT_QUIT} This app asked ` +
      `${environment.shell} for its environment at startup, so the PATH searched is the one ` +
      "your terminals get rather than the one this app was launched with -- a tmux that " +
      `is installed would have been found. ${install}; ${point} if it is installed ` +
      "somewhere that shell does not look (KYO-115, KYO-229)."
    );
  }
  // No capture, so KYO-185's distinction stands exactly as that record left it: the
  // app has searched an environment it cannot claim is the reader's, and says which
  // of the two things it cannot tell apart rather than asserting the one it cannot
  // know. `environment.why` names why there was no capture, which is the new half.
  if (launchOrigin(facts) === "terminal") {
    return (
      `no tmux on PATH, ${shared} ${ENDS_AT_QUIT} This app was launched from a shell and ` +
      `could not ask a login shell for its environment (${environment.why}), so the PATH ` +
      "searched is the launching shell's own: either tmux is not installed, or it is not " +
      `on the PATH you launched from. ${install}, or ${point}, to share a single session ` +
      `between them (KYO-115, KYO-185).${askAgain}`
    );
  }
  const minimal =
    facts.platform === "darwin"
      ? "the minimal PATH `launchd` gives a GUI application"
      : "the environment this app was launched from rather than any shell's";
  return (
    `no tmux on this app's PATH, ${shared} ${ENDS_AT_QUIT} This app was not launched from a ` +
    `shell and could not ask one for its environment (${environment.why}), so the PATH ` +
    `searched is ${minimal} -- a tmux that IS installed can be invisible from here, which ` +
    "is a different thing from tmux not being installed, and nothing this app can tell " +
    `apart without being told. To point it at one, ${point}. If it is genuinely not ` +
    `installed, ${install} (KYO-115, KYO-185, KYO-229).${askAgain}`
  );
}

// --------------------------------------- the socket funnel (KYO-283.c5, KYO-293.c5)
//
// Why the socket is named on every command, and why that is a funnel rather than a
// habit, is argued where the pane's client is spawned: `terminal.ts`, under "THE
// SOCKET IS NAMED, ALWAYS". The functions that hold it live here so the headless
// server's controller launcher can use them without loading the pty (KYO-388).

// --------------------------------- the controller's server, not the default (KYO-474)
//
// Until KYO-474 the pane used the machine's DEFAULT server (`$TMUX_TMPDIR/tmux-<uid>/
// default`) and made a `kyosei-<project>` session there. Since KYO-458 the controller
// sessions live on a dedicated server, `tmux -L kyosei`, kept running from login by a
// LaunchAgent -- so the tab never showed the running controller at all. The pane now
// attaches to THAT server, spelled exactly as `kyo controller` spells it
// (`src/kyosei/cmd_controller.py`): `-S $KYOSEI_CONTROLLER_SOCKET` when it is set, and
// otherwise `-L kyosei` with `TMUX_TMPDIR` dropped, which tmux resolves to
// `<realpath of /tmp>/tmux-<uid>/kyosei`. It never creates, lists or ends anything on
// the default server, and a session a person made there is theirs.

/** The CLI's name for the controller server (`SOCKET_NAME` in `cmd_controller.py`). */
export const CONTROLLER_SOCKET_NAME = "kyosei";

/** An explicit socket path in place of `-L kyosei` (`SOCKET_ENV` in `cmd_controller.py`). */
export const CONTROLLER_SOCKET_ENV = "KYOSEI_CONTROLLER_SOCKET";

/**
 * Where tmux keeps a `-L` socket when `TMUX_TMPDIR` is unset: `/tmp`, resolved the way
 * tmux resolves it (on macOS `/tmp` is a link to `/private/tmp`, and tmux reports the
 * resolved path as `#{socket_path}`).
 */
export function tmuxDefaultTmpdir(): string {
  try {
    return realpathSync("/tmp");
  } catch {
    return "/tmp";
  }
}

/**
 * The controller server's socket, as a path: the one `kyo controller` reaches.
 *
 * `TMUX_TMPDIR` is deliberately NOT read: the CLI drops it before every tmux call
 * (`DROPPED_ENV`), so its `-L kyosei` is always the one under `/tmp` -- the same one
 * the LaunchAgent's server, which has no `TMUX_TMPDIR`, listens on. `name` exists so a
 * check can measure tmux's own rule on a private `-L` name instead of the real one.
 */
export function controllerSocketPath(
  env: Record<string, string | undefined> = ptyEnvironment(),
  uid: number = typeof process.getuid === "function" ? process.getuid() : 0,
  name: string = CONTROLLER_SOCKET_NAME,
): string {
  const explicit = (env[CONTROLLER_SOCKET_ENV] ?? "").trim();
  if (explicit !== "") return explicit;
  return join(tmuxDefaultTmpdir(), `tmux-${uid}`, name);
}

/**
 * How the CLI would spell that server for a reader (`Tmux.spelled`): `tmux -L kyosei`,
 * or `tmux -S <path>` under `KYOSEI_CONTROLLER_SOCKET`.
 */
export function controllerSocketSpelled(env: Record<string, string | undefined>): string {
  const explicit = (env[CONTROLLER_SOCKET_ENV] ?? "").trim();
  return explicit === "" ? `tmux -L ${CONTROLLER_SOCKET_NAME}` : `tmux -S ${shellQuote(explicit)}`;
}

/**
 * The environment every tmux command and client of the pane runs in: the pane's own,
 * with the three variables the CLI drops (`DROPPED_ENV`) dropped here too. With `TMUX`
 * set, `attach-session` refuses ("sessions should be nested with care"); `TMUX_TMPDIR`
 * cannot move a `-S` socket, and is dropped anyway so nothing downstream reads it.
 */
export function controllerTmuxEnv(env: Record<string, string>): Record<string, string> {
  const scrubbed = { ...env };
  delete scrubbed.TMUX;
  delete scrubbed.TMUX_PANE;
  delete scrubbed.TMUX_TMPDIR;
  return scrubbed;
}

/**
 * Every tmux argv this feature builds, with the socket in front of it.
 *
 * One function so there is one place to check: `seams.spec.ts` scans the sources for
 * a tmux argv that does not come through here, and fails on it (KYO-283.c5). A bare
 * server command is what once took a user's live sessions down, and
 * an absence is not checkable -- a funnel is.
 */
export function tmuxArgs(socket: string, args: readonly string[]): string[] {
  return ["-S", socket, ...args];
}

/** What `list-sessions` is asked for: the name, and how many clients are in it. */
export const SESSION_LIST_FORMAT = "#{session_name}\t#{session_attached}";

/** One session alive on the server. */
export interface TmuxSession {
  name: string;
  /** How many clients are attached to it -- this app's pane may be one of them. */
  clients: number;
}

/** The sessions in `list-sessions -F SESSION_LIST_FORMAT`'s output, in its order. */
export function parseSessions(text: string): TmuxSession[] {
  const found: TmuxSession[] = [];
  for (const line of text.split("\n")) {
    if (line === "") continue;
    const tab = line.indexOf("\t");
    const name = tab === -1 ? line : line.slice(0, tab);
    if (name === "") continue;
    const clients = Number.parseInt(tab === -1 ? "0" : line.slice(tab + 1), 10);
    found.push({ name, clients: Number.isFinite(clients) ? clients : 0 });
  }
  return found;
}

/** What `list-clients` is asked for: which tty is in which session. */
export const CLIENT_LIST_FORMAT = "#{client_tty}\t#{session_name}";

export interface TmuxClient {
  tty: string;
  session: string;
}

export function parseClients(text: string): TmuxClient[] {
  const found: TmuxClient[] = [];
  for (const line of text.split("\n")) {
    if (line === "") continue;
    const tab = line.indexOf("\t");
    if (tab === -1) continue;
    const tty = line.slice(0, tab);
    const session = line.slice(tab + 1);
    if (tty !== "" && session !== "") found.push({ tty, session });
  }
  return found;
}
