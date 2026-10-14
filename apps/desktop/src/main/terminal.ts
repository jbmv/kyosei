// The terminal pane's shell: a real pty, rooted at the project.
//
// `@lydell/node-pty` rather than `node-pty` itself, and the reason is checkable
// rather than a preference. Both are Node-API addons, so both load into Electron
// with no ABI rebuild -- that is not what separates them. What separates them is
// how the binary arrives:
//
//   node-pty@1.1.0 publishes its prebuilds *inside* the package tarball with mode
//   0644, including the macOS `spawn-helper` the pty execs. Nothing in its install
//   scripts chmods it, so a plain `npm ci` leaves a helper the kernel refuses and
//   every spawn fails with `posix_spawnp failed` -- verified on a clean install
//   before the dependency was chosen. Its Linux story is worse: no prebuild ships
//   at all, so Linux compiles through node-gyp in an install script, and npm's
//   allow-scripts gate blocks dependency install scripts here by default.
//
//   @lydell/node-pty ships one optional package per platform/arch (all six,
//   Linux included), with the helper already mode 0755, and has no install script
//   at all. `npm ci` produces a working pty with nothing to approve.
//
// Its cost, stated: the only published version is a 1.2.0 beta. That is the trade
// -- a beta tag against a dependency that does not work after a clean install.
//
// Packaging cost either way: the addon is a real `.node` file, so a packaged build
// must keep it outside the asar archive (`asarUnpack`) and ship the platform
// package for the architecture it targets. Nothing here does packaging (KYO-111's
// boundary); this note is the whole of what that record inherits.

import { existsSync, lstatSync, readlinkSync, realpathSync, rmSync, unlinkSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import type { IPty } from "@lydell/node-pty";
import { spawn as spawnPty } from "@lydell/node-pty";
import type {
  AttachAction,
  PaneSurface,
  TerminalAttachOutcome,
  TerminalMode,
  TerminalOpen,
  TerminalSize,
} from "../shared/protocol";
import { shellQuote } from "./kyo";
import { findOnPath, type ShellEnvironment, terminalEnvironment } from "./shellenv";
import {
  controllerSocketPath,
  controllerSocketSpelled,
  controllerTmuxEnv,
  ENDS_AT_QUIT,
  type LaunchFacts,
  launchFacts,
  loginEnvironment,
  ptyEnvironment,
  resolveTmux,
  type TmuxClient,
  type TmuxSession,
  tmuxArgs,
} from "./tmuxenv";

// The pty-free half of this file moved to `tmuxenv.ts` (KYO-388), so that the headless
// server can reach the pane environment, tmux resolution and the socket funnel without
// loading node-pty. Every name that moved is re-exported here, so this file's own
// importers read what they always read.
export {
  CLIENT_LIST_FORMAT,
  CONTROLLER_SOCKET_ENV,
  CONTROLLER_SOCKET_NAME,
  controllerSocketPath,
  controllerSocketSpelled,
  controllerTmuxEnv,
  ENDS_AT_QUIT,
  findTmux,
  installTmuxInstruction,
  type LaunchFacts,
  type LaunchOrigin,
  launchFacts,
  launchOrigin,
  loginEnvironment,
  logShellEnvironmentIn,
  noTmuxReason,
  parseClients,
  parseSessions,
  ptyEnvironment,
  resolveTmux,
  retryingCapture,
  SESSION_LIST_FORMAT,
  setEnvInstruction,
  TMUX_ENV,
  TMUX_PATH_PLACEHOLDER,
  type TmuxClient,
  type TmuxSession,
  tmuxArgs,
  tmuxDefaultTmpdir,
  unsetEnvInstruction,
  WILL_ASK_AGAIN,
} from "./tmuxenv";

const DEFAULT_SHELL = process.platform === "win32" ? "powershell.exe" : "/bin/bash";

/**
 * The environment variable a pane of an EARLIER build of this app carried: the id of
 * the `kyo` shim it was opened with (KYO-189). KYO-450 removed the shims, so nothing
 * sets it any more -- but a pane of the old app, or a tmux session it prepared, still
 * does, and an app launched from one inherits it. `preparePane` drops it so it never
 * reaches a session again, and the desktop suite scrubs it from its own launches.
 */
export const RETIRED_SHIM_ID_ENV = "KYOSEI_SHIM_ID";

/**
 * The environment variable that moves the app's `userData` directory (KYO-275).
 *
 * KYO-189 decided that a dev build and the packaged build SHARE one directory, and
 * that decision stands. What it did not reckon with is a third launcher: the desktop
 * suite. Every app the suite starts went to the same directory, regenerated the live
 * shim on launch, and pointed it at whatever CLI that checkout resolves -- so a
 * test run switched two consumer projects' open panes to a CLI two schema
 * versions ahead of their stores, and KYO-189's own remedy ("quit the other build")
 * had nothing to quit, because the writer was a test that had already exited.
 *
 * `main.ts` reads this before Electron is ready and calls `app.setPath("userData")`
 * with it; the fixture sets it on every launch environment it builds. It is a
 * consumer's own seam on the precedent `KYOSEI_HOME`, `KYOSEI_TMUX` and
 * `KYOSEI_LOGIN_SHELL` set, not a test-only branch: a person who wants a second,
 * separate install of this app on one machine can point it here too. Chromium's
 * `--user-data-dir` switch also moves the directory, and wins over this when both are
 * given; the leak ran because two of the suite's twelve launch sites never passed it
 * and nothing checked -- an environment every launch inherits is what closes that.
 */
export const USER_DATA_ENV = "KYOSEI_USER_DATA";

// ------------------ an earlier build's command line tools, removed (KYO-450, KYO-315)
//
// Until KYO-450 this app shipped its own CPython and wrote `kyo` and `kyosei` shims for
// it: `<userData>/bin` for its own panes (KYO-111, KYO-189) and `<userData>/cli/<build>`
// for "Install Command Line Tools…", which symlinked them onto the user's PATH
// (KYO-315). KYO-443 removed the runtime, so those shims now exec an interpreter
// that is gone with the old app -- and a link left in `~/.local/bin` pointing at one
// would answer `kyo` in every terminal with a failure, and would make
// `uv tool install kyosei` refuse to put the real one there.
//
// So the first launch of a build without the runtime removes them -- and ONLY them.
// The ownership rule is KYO-315's: a link is this app's when it points into this app's
// own `<userData>/cli/`, which only this app ever wrote. Anything else at those names --
// a `uv tool install`'s file, a link somewhere else, another app's userData -- is left
// exactly as it was. The directories searched are the three KYO-315 could have linked
// into, and nothing more. Idempotent: once nothing points into `<userData>/cli/`, a
// launch finds nothing and says nothing, so it "says once that it did".

/** The CLI's two names: the ones KYO-315 linked, and so the ones this removes. */
export const CLI_NAMES = ["kyo", "kyosei"] as const;

/** The two shim directories an earlier build kept under `userData` (KYO-111, KYO-315). */
export const RETIRED_SHIM_DIRS = ["bin", "cli"] as const;

/**
 * Where KYO-315's Install Command Line Tools could have put a link, best first -- the
 * same three it chose between. Read with the login shell's HOME, never guessed.
 */
export function retiredCliLinkDirs(home: string): string[] {
  return [join(home, ".local", "bin"), join(home, "bin"), "/usr/local/bin"];
}

/** Is `target` inside `root`? Lexical, so a dangling link is still answerable. */
function inside(root: string, target: string): boolean {
  const rel = relative(resolve(root), resolve(target));
  return rel !== "" && !rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel);
}

/** Every spelling of `dir` a link could have been written with: as given, and resolved. */
function spellings(dir: string): string[] {
  const all = [dir];
  try {
    const real = realpathSync(dir);
    if (real !== dir) all.push(real);
  } catch {
    // Already gone, or never there: the lexical spelling is the only one there is.
  }
  return all;
}

export interface RetiredCliCleanup {
  /** The links removed: each pointed into this app's own `<userData>/cli/`. */
  removedLinks: string[];
  /** The shim directories removed from under `userData`. */
  removedDirs: string[];
  /** What could not be removed, and why -- a link or directory that refused. */
  problems: string[];
  /**
   * What the reader is told, once, or null when there was nothing of theirs to tell
   * them about: no link on their PATH was removed and nothing failed. The shim
   * directories alone are this app's own files and not the reader's concern.
   */
  message: string | null;
}

/**
 * Remove an earlier build's command line tools: the links into this app's own
 * `<userData>/cli/`, then both shim directories under `userData`.
 *
 * Pure but for the filesystem, and both inputs are arguments, which is what lets a
 * check hand it a temporary HOME and a temporary userData and nothing on the real
 * machine is ever named.
 */
export function removeRetiredCli(ask: {
  userData: string;
  linkDirs: readonly string[];
}): RetiredCliCleanup {
  const owned = spellings(join(ask.userData, "cli"));
  const removedLinks: string[] = [];
  const removedDirs: string[] = [];
  const problems: string[] = [];
  for (const dir of ask.linkDirs) {
    for (const name of CLI_NAMES) {
      const path = join(dir, name);
      let target: string;
      try {
        if (!lstatSync(path).isSymbolicLink()) continue;
        target = readlinkSync(path);
      } catch {
        continue; // nothing there, or not ours to read
      }
      const resolvedTarget = isAbsolute(target) ? target : join(dirname(path), target);
      if (!owned.some((root) => inside(root, resolvedTarget))) continue;
      try {
        unlinkSync(path);
        removedLinks.push(path);
      } catch (error) {
        problems.push(`${path}: ${(error as Error).message}`);
      }
    }
  }
  for (const name of RETIRED_SHIM_DIRS) {
    const dir = join(ask.userData, name);
    if (!existsSync(dir)) continue;
    try {
      rmSync(dir, { recursive: true, force: true });
      removedDirs.push(dir);
    } catch (error) {
      problems.push(`${dir}: ${(error as Error).message}`);
    }
  }
  return {
    removedLinks,
    removedDirs,
    problems,
    message: retiredCliMessage(removedLinks, problems),
  };
}

/** The one thing the reader is told about it, or null when nothing of theirs moved. */
export function retiredCliMessage(
  removedLinks: readonly string[],
  problems: readonly string[],
): string | null {
  if (removedLinks.length === 0 && problems.length === 0) return null;
  const removed =
    removedLinks.length === 0
      ? ""
      : `Removed ${removedLinks.join(" and ")}: ${removedLinks.length === 1 ? "a link" : "links"} ` +
        "an earlier version of this app installed (Install Command Line Tools) into its own " +
        "copy of kyo, which no longer exists. Nothing else on your PATH was touched. ";
  const failed =
    problems.length === 0
      ? ""
      : `Could not remove ${problems.join("; ")} -- delete ${problems.length === 1 ? "it" : "them"} ` +
        "by hand. ";
  return (
    "Kyosei no longer ships its own kyo. " +
    removed +
    failed +
    "The app, its terminal panes and your Claude Code hooks now all run the kyo on your " +
    "login shell's PATH. If `kyo version` in a new terminal says command not found, " +
    "install it: `uv tool install kyosei`."
  );
}

// --------------------------------------------------------------- tmux (KYO-115)
//
// The whole mechanism behind "view and respond from the app" is tmux, and nothing
// else: no MCP, no IDE plugin, no second Claude process. Since KYO-474 the session the
// pane shows is the CONTROLLER's: `kyo controller start` runs claude in a detached
// session named after the project's prefix on the dedicated `tmux -L kyosei` server
// (KYO-458), and the pane is one more client of it -- on this machine directly, and on
// a dev box through the person's own SSH (`kyo controller attach`). tmux mirrors the
// live TUI to every attached client, so answering a permission prompt here answers it
// there.

/**
 * The tmux subcommands this app may never issue (KYO-229.c2).
 *
 * The pane VIEWS a controller session and nothing more: `kyo controller` starts and
 * stops it, and **nothing in this app ends it.** A controller session is meant to
 * outlive every launch of this app, so a quit, a closed tab or a dropped connection
 * detaches a client and no code path anywhere takes the session down with it.
 *
 * Stated as a list rather than left as an absence because an absence is not checkable:
 * `seams.spec.ts` scans every source file for each of these words and fails on any of
 * them, so the day somebody adds a "close session" button the check goes red rather
 * than a user losing a session to it. `endSessions` kills this app's own
 * PTYs -- which in tmux mode are CLIENTS -- and that is a detach, not an end.
 */
export const TMUX_SUBCOMMANDS_REFUSED = [
  "kill-session",
  "kill-server",
  "kill-window",
  "kill-pane",
] as const;

/**
 * What a human types in any other terminal on the machine that holds the session to
 * join it: the CLI's own verb, which knows the socket and the exact target (KYO-474).
 */
export function controllerJoinCommand(session: string): string {
  return `kyo controller attach ${shellQuote(session)}`;
}

/** And from another machine: the same words through `ssh -t`, as the tab runs them. */
export function controllerJoinOverSsh(host: string, session: string): string {
  return `ssh -t ${shellQuote(host)} ${shellQuote(`zsh -lc ${shellQuote(controllerJoinCommand(session))}`)}`;
}

export interface TmuxPlan {
  /** The tmux binary, or null when there is none on PATH. */
  binary: string | null;
  /** The project's controller session name -- its prefix -- or null when unknown. */
  session: string | null;
  /** Why there is no tmux session to attach to. Null when there is one. */
  reason: string | null;
}

/**
 * Will this pane need the project's prefix? -- which is to say, is there a tmux for it
 * to attach with. Asked before the prefix is looked up, so a machine with no tmux pays
 * for no question whose answer nothing reads.
 */
export function tmuxAvailable(env: Record<string, string | undefined> = ptyEnvironment()): boolean {
  return resolveTmux(env).binary !== null;
}

/** Decide, from facts only, whether this pane can attach to the controller session. */
export function planTmux(
  projectSession: string | null,
  sessionProblem: string | null,
  env: Record<string, string | undefined> = ptyEnvironment(),
  facts: LaunchFacts = launchFacts(),
  environment: ShellEnvironment = loginEnvironment(),
): TmuxPlan {
  const { binary, reason } = resolveTmux(env, facts, environment);
  if (binary === null) return { binary: null, session: null, reason };
  if (projectSession === null) {
    return {
      binary,
      session: null,
      reason:
        "tmux is installed, but this project's prefix could not be read, so there is no " +
        "orchestrator session name to attach to" +
        (sessionProblem === null ? "" : `: ${sessionProblem}`),
    };
  }
  return { binary, session: projectSession, reason: null };
}

// --------------- the pane opens in the session, through a pty of its own (KYO-293)
//
// TWO RECORDS, AND THE SECOND REVERSES HALF OF THE FIRST. KYO-283 made the pane a bare
// login shell that attached on request, because a user could not get a plain shell out
// of it (KYO-265). Both halves of that shape failed in use.
//
// Opening bare was a risk: claude started in a bare shell is a process this app owns,
// so quitting the app ends it and the session is lost. KYO-293 opens the pane in the
// project's tmux session instead.
//
// Attaching on request was a defect and not a preference: with a long command running
// in the bare shell, the pane could not attach at all. KYO-283 attached by WRITING
// `attachCommandLine(...)` into the pane's one pty -- the bare shell's -- so a shell
// busy in a foreground command never saw it: a pty's input goes to whatever holds the
// terminal, and that was `sleep`, not the shell. No amount of care about which
// command is typed fixes a design that types.
//
// So nothing types into anybody's shell any more. Four consequences run through
// everything below.
//
//   THE CLIENT IS ITS OWN PTY. `spawnClient` gives node-pty the tmux BINARY as the
//   file and `tmuxArgs(socket, ["attach-session", ...])` as the argv. The process in
//   that pty IS the client -- there is no shell in between to be busy, and no command
//   line to be interrupted. The bare shell keeps its own pty, its own screen and
//   whatever is running in it, untouched by every attach.
//
//   THE APP KNOWS ITS OWN CLIENT BY CONSTRUCTION. It spawned it, so `ptyTty` names the
//   tty tmux will report for it, and `detach-client -t <tty>` is exact. KYO-283 had a
//   whole branch for "attached to something and cannot name my own client", which
//   ended in handing the reader a command to type; no pane the app attached can reach
//   that state now, and the branch is gone rather than left behind (KYO-293.c5).
//
//   THE SOCKET IS NAMED, ALWAYS. The pane's tmux and the app's tmux must be the same
//   server, and "the same server" is decided by `TMUX_TMPDIR` -- an environment
//   variable, which is exactly the thing that differed between the app and the suite,
//   and a difference in it once cost a user their live sessions (KYO-254). A
//   command that names `-S <path>` cannot be redirected by an inherited variable, so
//   every tmux command this feature issues goes through `tmuxArgs` and names it
//   (KYO-293.c5, checked by a scan in `seams.spec.ts`). `tmuxArgs` and `controllerSocketPath`
//   live in `tmuxenv.ts`, which loads no pty, so the headless server can use them (KYO-388).
//
//   WHAT THE PANE IS ATTACHED TO IS READ, NOT REMEMBERED. `list-clients` names the
//   session each client tty is in, and the pane's tty is the client pty's. So a reader
//   who detaches by hand (their own prefix key, which this app does not know) is seen
//   by the next poll, and the highlighted pill cannot drift from the truth.

/**
 * The session this pane's client is in, read off the server rather than remembered.
 *
 * Matched on the pty's own slave tty, which is what tmux reports as `client_tty` for
 * a client started in it. Compared by basename as well as by path because a tty can
 * be named `/dev/ttys004` on one side and `ttys004` on the other, and an attachment
 * the app cannot see is one the reader would see highlighted nowhere.
 */
export function attachedSession(clients: readonly TmuxClient[], tty: string | null): string | null {
  if (tty === null || tty === "") return null;
  const mine = basename(tty);
  for (const client of clients) {
    if (client.tty === tty || basename(client.tty) === mine) return client.session;
  }
  return null;
}

/**
 * The pty's slave tty, which is the name tmux knows this pane's client by.
 *
 * `ptsName` is a real accessor on node-pty's unix terminal and is absent from the
 * published typings, so it is read defensively. Since KYO-293 the pty this is asked
 * about is one whose PROCESS IS THE TMUX CLIENT -- spawned here, with no shell in
 * between -- so the answer is this app's own client's tty by construction, and the
 * `detach-client -t <tty>` built from it cannot be aimed at anybody else's terminal.
 * A null is still handled (an exotic platform, a pty disposed under us): the client is
 * then ended by signalling its pty, which detaches it just the same.
 */
export function ptyTty(pty: Pick<IPty, "pid">): string | null {
  const named = (pty as unknown as { ptsName?: unknown }).ptsName;
  return typeof named === "string" && named !== "" ? named : null;
}

/** One pill above the pane's screen. */
export interface SessionPill {
  name: string;
  /** This window's project's own session: first in the row, and set apart. */
  project: boolean;
  /** Alive on the server right now. A pill that is not live can only be started. */
  live: boolean;
  /** This pane's client is in it. */
  current: boolean;
  /** How many clients are in it, this pane's included. */
  clients: number;
}

/**
 * The row of pills: every session alive on the app's server, the project's first.
 *
 * `live` is exactly the set `list-sessions` answered with, so a session that ends
 * drops off on the next read (KYO-283.c2). The one pill that can be there without
 * being live is the PROJECT's, and it is there for a capability that would otherwise
 * have been lost with the bare-by-default open: KYO-115.c1 -- the app starts the
 * per-project session when it is absent. It is marked `live: false` and says so,
 * rather than being counted among the sessions the server reported.
 */
export function sessionPills(
  alive: readonly TmuxSession[],
  projectSession: string | null,
  attachedTo: string | null,
): SessionPill[] {
  const pills: SessionPill[] = [];
  const seen = new Set<string>();
  if (projectSession !== null) {
    const live = alive.find((session) => session.name === projectSession);
    seen.add(projectSession);
    pills.push({
      name: projectSession,
      project: true,
      live: live !== undefined,
      current: attachedTo === projectSession,
      clients: live?.clients ?? 0,
    });
  }
  for (const session of alive) {
    if (seen.has(session.name)) continue;
    seen.add(session.name);
    pills.push({
      name: session.name,
      project: false,
      live: true,
      current: attachedTo === session.name,
      clients: session.clients,
    });
  }
  return pills;
}

/** What the decision is made from: the pane, as the main process can see it. */
export interface PaneAttachState {
  /** The tmux binary, or null when this machine has none the app can see. */
  binary: string | null;
  /** The socket every command names, or null when there is no tmux. */
  socket: string | null;
  /**
   * The session this pane's tmux client is in, read from `list-clients`, or null when
   * this pane has no client -- which is the same thing as "the bare shell is showing".
   */
  attachedTo: string | null;
  /** This pane's client tty, when there is a client and the app can name it. */
  clientTty: string | null;
}

export interface AttachPlan {
  action: AttachAction;
  /** Which of the pane's two screens this plan would leave showing. */
  surface: PaneSurface;
  /** The sentence the strip says. Never empty, in any branch (law 6). */
  reason: string;
}

/**
 * The argv that attaches ONE pane to ONE session: the whole of a client pty's command.
 *
 * `tmuxArgs` puts the socket in front of it, which is the only reason this is a
 * function rather than an array literal at the spawn site -- `seams.spec.ts` scans every
 * source file for a tmux subcommand written outside that funnel and fails on it, and the
 * needle it scans with is the quoted word below (KYO-293.c5).
 *
 * What is NOT here is a shell. KYO-283's `attachCommandLine` produced a quoted command
 * LINE, because the thing that ran it was the pane's own bare shell; this produces an
 * argv, because the thing that runs it is node-pty, directly. That difference is the
 * whole record: a shell can be busy, an argv cannot.
 */
export function clientAttachArgs(socket: string, session: string): string[] {
  // `=` so tmux matches the name exactly, as `kyo controller attach` does: a bare `-t
  // KYO` matches `KYOSEI` as a prefix when there is no `KYO` (KYO-474).
  return tmuxArgs(socket, ["attach-session", "-t", `=${session}`]);
}

/**
 * What a click on a pill does, decided from facts only.
 *
 * Three answers where KYO-283 had five (KYO-293.c5). `detach-then-attach` is gone
 * because re-targeting is now one act on a pty this app owns -- it ends its own client
 * and starts another -- rather than two commands typed into a shell in sequence. And
 * `prefill` is gone because the case it existed for cannot arise: it was for a pane
 * attached to something the app could not name a client for, and a client the app
 * spawned is one it can always name.
 */
export function planPaneAttach(pane: PaneAttachState, target: string): AttachPlan {
  if (pane.binary === null || pane.socket === null) {
    return {
      action: "refuse",
      surface: "bare",
      reason:
        "there is no tmux this app can see, so there is no session to attach to. The " +
        "pills above are read from a tmux server; without a tmux there is no server.",
    };
  }
  if (pane.attachedTo === target) {
    return {
      action: "already",
      surface: "client",
      reason: `this pane is already in ${target}.`,
    };
  }
  if (pane.attachedTo === null) {
    return { action: "attach", surface: "client", reason: `attaching this pane to ${target}.` };
  }
  return {
    action: "attach",
    surface: "client",
    reason:
      `moving this pane from ${pane.attachedTo} to ${target}. ${pane.attachedTo} keeps ` +
      "running, and so does everything in it -- this pane's client detaches, nothing else.",
  };
}

/**
 * What the leftmost control does, decided from the same facts.
 *
 * KYO-293.c3 in one function: show the bare login shell, and detach this pane's tmux
 * client. Two acts rather than one, and they are independent -- the bare shell is a
 * pty of its own, so it is shown whether or not there is a client to detach, and a
 * pane that is already bare says what a bare pane IS rather than reporting an error
 * for a control the reader is allowed to press twice.
 *
 * KYO-283's `refuse` branch for "cannot name my own client" is gone with the rest of
 * that shape: the client is this app's pty, so worst case it is ended by a signal to
 * that pty, which detaches it exactly as `detach-client` would. There is no case in
 * which this control can do nothing.
 */
export function planPaneBare(pane: PaneAttachState): AttachPlan {
  if (pane.attachedTo === null) {
    return { action: "already", surface: "bare", reason: BARE_PANE_NOTICE };
  }
  return {
    action: "bare",
    surface: "bare",
    reason:
      `detaching this pane's client from ${pane.attachedTo}; it and everything in it ` +
      `keep running. ${BARE_PANE_NOTICE}`,
  };
}

/**
 * What the strip says when the pane's client went away and nobody here asked it to.
 *
 * The other half of KYO-293.c3, and the reason it takes an argument rather than being
 * a constant: a client pty that exits is the ONLY signal this app gets, and it means
 * two very different things. The reader detached by hand -- their own prefix key, then
 * `d`, which tmux tells the server and not this app -- or the session ended under
 * them. `alive` is `list-sessions` asked after the exit, which is the one question that
 * separates them, and each answer names what a reader can do next.
 */
export function clientGoneNotice(session: string, alive: boolean): string {
  if (alive) {
    return (
      `you detached this pane from ${session} (its own prefix key, then \`d\`). ${session} ` +
      `and everything in it keep running -- click ${session} above to go back to it. ` +
      `Meanwhile this pane is ${BARE_PANE_NOTICE}`
    );
  }
  return (
    `${session} ended, so this pane's client ended with it -- nothing in this app ends ` +
    "a session, so that was claude exiting, `kyo controller stop`, or something else " +
    `outside this app. This pane is now ${BARE_PANE_NOTICE}`
  );
}

// ------------------------------------ a dev box's session, over SSH (KYO-474.c3)
//
// A window on a dev box's project has no tmux of its own to attach with: the session
// is on the other machine. Its client pty runs `ssh -t <host> zsh -lc 'kyo controller
// attach <prefix>'` (`ssh.ts` builds it), so the far end's CLI resolves its own socket
// and the pane is a client there exactly as a person's terminal would be. The only
// signal the app gets back is the pty ending, with an exit status and whatever was on
// its screen -- which, for a refusal, is ssh's or kyo's own stderr, verbatim.

/** Everything the app needs to reach a dev box's session. */
export interface SshPane {
  /** The SSH host, as the person's ssh understands it. */
  host: string;
  /** The argv that attaches to one session there: `ssh.ts`'s `controllerAttachArgv`. */
  attachArgv: (session: string) => string[];
}

/** How many lines of a failed client's own output the pane repeats in prose. */
export const SAID_LINES = 12;

/**
 * What a client pty printed, as text a reader can read in prose: escape sequences
 * removed, carriage returns folded, blank edges trimmed, the last `SAID_LINES` kept.
 * The words are the program's own -- nothing is paraphrased (KYO-474.c4).
 */
export function saidByClient(output: string, lines: number = SAID_LINES): string {
  const plain = output
    // biome-ignore lint/suspicious/noControlCharactersInRegex: removing them is the point.
    .replaceAll(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, "")
    // biome-ignore lint/suspicious/noControlCharactersInRegex: removing them is the point.
    .replaceAll(/\x1b[\]P^_][^\x07\x1b]*(\x07|\x1b\\)?/g, "")
    // biome-ignore lint/suspicious/noControlCharactersInRegex: removing them is the point.
    .replaceAll(/\x1b[()][0-9A-Za-z]|\x1b[=>78DEHMNOc]/g, "")
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    // biome-ignore lint/suspicious/noControlCharactersInRegex: removing them is the point.
    .replaceAll(/[\x00-\x08\x0b-\x1f\x7f]/g, "");
  const kept = plain
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line, index, all) => line !== "" || (index > 0 && all[index - 1] !== ""));
  while (kept.length > 0 && kept[0] === "") kept.shift();
  while (kept.length > 0 && kept[kept.length - 1] === "") kept.pop();
  return kept.slice(-lines).join("\n");
}

/**
 * Ask something about the project, and answer only if the project stayed still.
 *
 * KYO-218. A pane is opened from TWO facts about one project -- the directory the
 * pty starts in, and the prefix the controller session is named after -- and only
 * the second costs a question of this machine's kyo serve. So the pty's cwd is read after that subprocess
 * has answered and the session name comes out of an answer given before it started,
 * and a project switch landing in that gap produces a pane that describes two
 * projects at once: a shell in the new project's directory, attached to the previous
 * project's tmux session, with nothing on screen saying so. Observed as
 * `data-terminal-cwd` naming the project just opened beside
 * `data-terminal-session="kyosei-kyosei"`, the checkout the app had just been
 * switched away from.
 *
 * The gap is small -- one `kyo worktrees --json` -- which is exactly why it went
 * unseen: whether the switch lands inside it depends on how long that command takes,
 * and that depends on whether the launch project has a store the CLI can open. A
 * developer's main checkout has one and a dispatch worktree does not (`kyosei.db` is
 * gitignored), so the same code was deterministic in one environment and invisible in
 * the other. `main.ts` is where the two facts are read; this is the rule they are read
 * under, here because `main.ts` cannot be exercised without an Electron and this can.
 *
 * What it does NOT do is retry a failure or wait longer for one. `ask` is re-run only
 * when `rootNow` reports a DIFFERENT project than the one the previous ask was about
 * -- the question changed, so the answer is asked again -- and each re-ask requires
 * another switch, which is a human moving the app, not a loop the machine can spin.
 */
export async function askedAboutOneProject<T>(
  rootNow: () => string,
  ask: () => Promise<T>,
): Promise<{ root: string; answer: T }> {
  for (;;) {
    const root = rootNow();
    const answer = await ask();
    if (rootNow() === root) return { root, answer };
  }
}

/**
 * Where the pane's tmux work is done, and why none of it is typed (KYO-283, KYO-293).
 *
 * Two shapes have been tried and both are recorded here, because each one's defect is
 * the next one's reason.
 *
 *   KYO-115's `sessionScript` ran in the pane's own shell -- `new-session -d`,
 *   `set-environment`, then an `exec attach-session` -- because `kyo.ts` was this app's
 *   only seam allowed to start a process. It `exec`'d the client over the shell, so a
 *   detach left an empty pty rather than a shell to return to, and it named
 *   no socket, so every command in it reached whatever server `TMUX_TMPDIR` happened to
 *   name (KYO-254).
 *
 *   KYO-283 kept the shell and stopped `exec`ing: the attach became an ordinary
 *   foreground command WRITTEN into the pane's pty. That fixed the detach and named the
 *   socket, and introduced the defect described above -- a shell already busy in
 *   a foreground command does not read its pty, so the command went into `sleep`'s
 *   stdin and the pane never attached.
 *
 * Nothing is typed now. The attach is a PTY: node-pty spawns the tmux binary with
 * `clientAttachArgs` (or, for a dev box, ssh with `kyo controller attach`), so the client
 * is a process this app started and can end, in a pty of its own that no shell shares.
 * And since KYO-474 nothing is prepared or created either: the session is the
 * controller's, started by `kyo controller start`, and the pane only views it.
 */

export interface TerminalSpec {
  /** The working directory of both of the pane's ptys: the project root. */
  cwd: string;
  /**
   * The project's controller session name -- its prefix, as the kyo serve that serves
   * it lists it -- or null when it could not be read. Without it there is no session
   * to attach to, only a shell.
   */
  projectSession?: string | null;
  /** Why the prefix is unknown, when it is. */
  sessionProblem?: string | null;
  /**
   * Set for a window on a dev box's project (KYO-474.c3): the session is there, and the
   * client is ssh. No tmux is looked for here and no bare shell is offered -- the
   * project's directory is on the other machine.
   */
  ssh?: SshPane | null;
  /**
   * How this app was launched, for the sentences that name an environment variable
   * (KYO-185). Defaulted from the process, and an argument so a check can drive both.
   */
  launch?: LaunchFacts;
  /**
   * The login shell's environment (KYO-229). Defaulted to the process-wide capture,
   * and an argument for the same reason `launch` is one: a check drives every outcome
   * without launching an application per outcome.
   */
  environment?: ShellEnvironment;
}

/**
 * Everything a pane needs before it has decided which of its two ptys to start.
 *
 * KYO-293 split this out of `startTerminal`, which used to compute it and spawn a shell
 * in one act. The pane now spawns a tmux client, or a login shell, or both over its
 * life, and all three want the same environment and the same tmux plan -- computed once, here, so that the shell the reader drops back to is on
 * exactly the environment the client was started with.
 */
export interface PaneSetup {
  /** The environment every pty of this pane is spawned with. */
  env: Record<string, string>;
  /** The reader's own login shell, for the bare surface. */
  shell: string;
  /** Whether there is a tmux, which session this project's is, and why not when not. */
  plan: TmuxPlan;
  /**
   * The controller server's socket every command names (`controllerSocketPath`), or
   * null when there is no tmux here -- or the session is on a dev box.
   */
  socket: string | null;
  /** That server as the CLI spells it for a reader (`tmux -L kyosei`). */
  spelled: string;
  /** The dev box this pane reaches over SSH, or null for this machine. */
  ssh: SshPane | null;
  /** Where `kyo` resolves on this pane's PATH -- the login shell's -- or null. */
  kyoOnPath: string | null;
}

// -------------------------------------------------- one owner for a pty (KYO-124)
//
// A session is a shell, and a window is a VIEW of one. Nothing below knows what a
// `webContents` is, and that is the whole of the ownership decision: a pty's
// lifetime is not a property of any renderer, any window, or any pane, so a window
// closing cannot end a shell and popping a pane out cannot start a second one.
//
// The two halves the record asks for both fall out of that:
//
//   The session outlives the window because `endSessions` is the ONLY thing that
//   ends one, and only the app quitting calls it. A view going away detaches a sink
//   and nothing else.
//
//   The SIGABRT goes because `endSessions` waits for node-pty to DELIVER each exit
//   before the app is allowed to finish quitting. `pty.kill()` does not end a
//   session, it queues one: node-pty's native side reaps the child on a thread of
//   its own and calls back into JS through a napi threadsafe function. Left to run
//   during `node::FreeEnvironment`'s final `uv_run`, that call cannot succeed --
//   the environment it needs is being destroyed -- napi's failure path throws a
//   JavaScript exception, no JS scope exists to receive it, and the C++ exception
//   escapes to `std::terminate`. Waiting for the exit to arrive is what drains that
//   call while the environment is still alive.
//
// It is NOT fixed by catching the throw. There is no `try` here that could catch
// it, and there is deliberately no `uncaughtException` handler anywhere in this app
// (`smoke.spec.ts` scans for one): swallowing it would turn an announced crash into
// a terminal that silently stopped being a terminal, on a surface whose whole
// promise is that the session is still running.

/**
 * How much of a session's output is kept for a view that arrives late.
 *
 * A pane popped into its own window is the same shell in a fresh xterm.js, which
 * has none of the screen the reader was looking at a moment ago. This is what it is
 * handed on arrival. Bounded, because it is a live process's whole output and a
 * `yes` left running would otherwise be a leak; and a tail rather than a summary,
 * because the last screen is the part a reader is looking at.
 */
export const REPLAY_BYTES = 128 * 1024;

/** A view of a pane: somewhere to send bytes, and somewhere to say what happened. */
export interface SessionSink {
  data(surface: PaneSurface, chunk: string): void;
  exit(surface: PaneSurface, info: { exitCode: number }): void;
  /** The tmux client went away and nobody here asked it to (KYO-293.c3). */
  fellBack(outcome: TerminalAttachOutcome): void;
}

/** One of a pane's two ptys, with the screen it has painted so far. */
export interface PaneScreen {
  readonly pty: IPty;
  /** The tail of everything this pty has written -- its own, never the other's. */
  replay: string;
  /** The size it was last told about; a hidden screen keeps it until it is shown. */
  size: TerminalSize;
  /** Resolves once node-pty has delivered this pty's exit into a live JS scope. */
  readonly ended: Promise<void>;
  exited: boolean;
}

export interface PtySession {
  readonly id: string;
  /** What every view of this pane is told about it; one pane, one answer. */
  info: TerminalOpen;
  /** Everything both ptys were built from, so either can be started later. */
  readonly setup: PaneSetup;
  /** The directory both ptys start in: the project root. */
  readonly cwd: string;
  /** The pty whose process IS this pane's tmux client; null when there is none. */
  client: PaneScreen | null;
  /** The session that client is in, as this app asked for it. */
  clientSession: string | null;
  /** The reader's own login shell; null until the bare control is first used. */
  bare: PaneScreen | null;
  /** Which of the two the pane is showing. */
  showing: PaneSurface;
  /**
   * The last size any screen of this pane was told about.
   *
   * What a surface started later is spawned at: the bare shell first shown after a
   * client went away has no size of its own yet, and 80x24 in a pane the reader has
   * made twice that would be a screen with a margin of dead space down one side.
   */
  lastSize: TerminalSize;
  /**
   * True while THIS app is ending the client on purpose -- the bare control, a
   * re-target, a quit. The client pty's exit is the only signal either way, so
   * without this flag an app-requested detach would be reported to the reader as
   * "you detached" (KYO-293.c3).
   */
  droppingClient: boolean;
  /** True once the whole pane is being torn down; no surface is restarted then. */
  closing: boolean;
  readonly sinks: Set<SessionSink>;
  /**
   * What the main process does when the client pty exits unrequested: ask the server
   * which of the two things happened, and tell the sinks. Installed by `main.ts`,
   * because it needs `tmuxctl.ts`, and left null in a check that only wants the ptys.
   * `ended` carries the client's exit status and what it last printed: for an ssh
   * client that never attached, that is ssh's or kyo's own refusal (KYO-474.c4).
   */
  onClientGone: ((session: string, ended: { exitCode: number; output: string }) => void) | null;
}

const sessions = new Map<string, PtySession>();

export function liveSessions(): PtySession[] {
  return [...sessions.values()];
}

export function sessionById(id: string): PtySession | undefined {
  return sessions.get(id);
}

/**
 * Open the pane for `id`, or hand back the one already open under it.
 *
 * Idempotent on the id, which is what makes "pop the pane out" a move rather than a
 * second shell: the popped window asks for the id its owner's pane was already
 * showing and gets that pane, both ptys and all.
 *
 * No pty is started here (KYO-293). Which surface a pane opens on depends on whether
 * this project's session could be attached, which is an answer only `tmuxctl.ts` can
 * get and only asynchronously -- so the pane is made first and `main.ts` starts a
 * surface on it, through `startClient` or `showBare`, before it answers the renderer.
 */
export function openSession(id: string, spec: TerminalSpec): PtySession {
  const running = sessions.get(id);
  if (running !== undefined) return running;
  const setup = preparePane(spec);
  const session: PtySession = {
    id,
    setup,
    cwd: spec.cwd,
    info: paneInfo(id, spec.cwd, setup, "bare", null),
    client: null,
    clientSession: null,
    bare: null,
    showing: "bare",
    lastSize: { cols: 80, rows: 24 },
    droppingClient: false,
    closing: false,
    sinks: new Set(),
    onClientGone: null,
  };
  sessions.set(id, session);
  return session;
}

export function screenOf(session: PtySession, surface: PaneSurface): PaneScreen | null {
  return surface === "client" ? session.client : session.bare;
}

/**
 * Wire one pty into the pane as one of its two surfaces.
 *
 * The data and exit callbacks are tagged with the surface, which is the whole of what
 * makes two screens possible: a chunk from the bare shell reaches the bare xterm while
 * the session is on show, so the command the reader left running keeps painting into a
 * screen with its own scrollback (KYO-293.c2).
 */
function addScreen(
  session: PtySession,
  surface: PaneSurface,
  pty: IPty,
  size: TerminalSize,
): PaneScreen {
  let settle: () => void = () => {};
  const ended = new Promise<void>((resolve) => {
    settle = resolve;
  });
  const screen: PaneScreen = { pty, replay: "", size, ended, exited: false };
  if (surface === "client") session.client = screen;
  else session.bare = screen;
  pty.onData((chunk: string) => {
    screen.replay = `${screen.replay}${chunk}`.slice(-REPLAY_BYTES);
    for (const sink of session.sinks) sink.data(surface, chunk);
  });
  pty.onExit(({ exitCode }: { exitCode: number }) => {
    // This is the callback the crash was about: it arrives from node-pty's own
    // thread through a threadsafe function, and `endSessions` below is what makes
    // sure it arrives HERE rather than into a half-destroyed environment.
    screen.exited = true;
    settle();
    for (const sink of session.sinks) sink.exit(surface, { exitCode });
    if (surface === "client") {
      const wasIn = session.clientSession;
      session.client = null;
      session.clientSession = null;
      // A detach THIS app asked for is narrated by whoever asked for it; this branch
      // is the other one -- the reader's own prefix-d, the session ending, or (over
      // SSH) the connection or the attach itself failing. A dev box's pane has no bare
      // shell to fall back to, so it keeps its client screen, which holds the words.
      if (!session.closing && !session.droppingClient && wasIn !== null) {
        if (session.setup.ssh === null) session.showing = "bare";
        session.onClientGone?.(wasIn, { exitCode, output: screen.replay });
      }
      return;
    }
    session.bare = null;
    // The bare shell exiting is the reader ending it (`exit`, Ctrl-D). The pane is
    // gone only if there is nothing else behind it.
    if (session.client === null) sessions.delete(session.id);
  });
  return screen;
}

/**
 * Show this pane to one more viewer, starting with what each of its screens missed.
 *
 * Both replays, each tagged, and both before the sink joins the set: the main process
 * is single-threaded and nothing is awaited here, so a chunk arriving from either pty
 * cannot be delivered twice or out of order. Both rather than only the one on show,
 * because the reader may switch to the other in the next second and a screen that
 * started empty would have lost everything printed before this window existed.
 */
export function attachSink(session: PtySession, sink: SessionSink): () => void {
  if (session.bare !== null && session.bare.replay !== "") sink.data("bare", session.bare.replay);
  if (session.client !== null && session.client.replay !== "") {
    sink.data("client", session.client.replay);
  }
  session.sinks.add(sink);
  return () => {
    session.sinks.delete(sink);
  };
}

/** How long a shell is given to answer SIGHUP before it is answered with SIGKILL. */
export const HANGUP_GRACE_MS = 400;

/** And how long the SIGKILL is given to be reported back before the app gives up. */
export const KILL_GRACE_MS = 600;

function afterMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Has this screen's exit been delivered -- within `ms`, or at all? */
async function delivered(screen: PaneScreen, ms: number): Promise<boolean> {
  await Promise.race([screen.ended, afterMs(ms)]);
  return screen.exited;
}

/**
 * End one pty and wait for node-pty to say so.
 *
 * SIGHUP first, because that is what a closing terminal sends and what a tmux
 * client, a shell and every well-behaved job under them expect. SIGKILL only if the
 * hangup went unanswered -- a shell with `trap "" HUP` set, which is a real thing a
 * reader can do to their own pane -- because the alternative is an app that cannot
 * be quit. Both waits are bounded for the same reason: a quit that can be held
 * forever is a worse failure than the crash being avoided (KYO-119's judgement,
 * applied to the other half of the same teardown).
 *
 * On the CLIENT surface a hangup is a detach and nothing more: the process being
 * signalled is a tmux client, and a client that goes away leaves the session, its
 * windows and everything running in them exactly where they were (KYO-293.c4).
 */
export async function endScreen(
  screen: PaneScreen,
  hangupMs: number = HANGUP_GRACE_MS,
  killMs: number = KILL_GRACE_MS,
): Promise<void> {
  // node-pty's own `kill` swallows an ESRCH for a shell that has already gone; this
  // catch is for a pty object disposed under us, and neither is the exception the
  // record refuses to swallow -- that one is thrown in C++ during teardown, where no
  // `try` in JavaScript can reach it.
  try {
    screen.pty.kill();
  } catch {
    // Already gone; the wait below settles on the exit that is already in flight.
  }
  if (await delivered(screen, hangupMs)) return;
  try {
    screen.pty.kill("SIGKILL");
  } catch {
    // Same.
  }
  await delivered(screen, killMs);
}

/** End both of a pane's ptys, and do not resolve until both exits are delivered. */
async function endSession(session: PtySession, hangupMs: number, killMs: number): Promise<void> {
  session.closing = true;
  const screens = [session.client, session.bare].filter((one): one is PaneScreen => one !== null);
  await Promise.all(screens.map((screen) => endScreen(screen, hangupMs, killMs)));
  sessions.delete(session.id);
}

/**
 * End one pane by id -- a window's own, when that window moves to another project
 * (KYO-249): the ptys at the old project's cwd go, the id stays, and the next
 * `openSession` under it starts afresh at the new cwd. The client pty is a tmux
 * CLIENT, so ending it detaches; the session on the server is untouched, as ever.
 */
export async function endSessionNamed(
  id: string,
  hangupMs: number = HANGUP_GRACE_MS,
  killMs: number = KILL_GRACE_MS,
): Promise<void> {
  const session = sessionById(id);
  if (session === undefined) return;
  await endSession(session, hangupMs, killMs);
}

/**
 * End every pane, and do not resolve until their exits have been delivered.
 *
 * The app's `before-quit` holds the quit on this. What it buys is stated where the
 * mechanism is, at the top of this section: the exit callbacks node-pty queues on
 * its own thread run here, in a live environment, instead of during
 * `node::FreeEnvironment`'s last `uv_run`, where the call into JS cannot succeed and
 * the resulting C++ exception aborts the process.
 *
 * KYO-293.c4: this is also the whole of what quitting does to tmux. Each pane's client
 * is a pty whose process is a tmux client; SIGHUP detaches it. Nothing here, and
 * nothing anywhere in this app, names a subcommand that could end a session --
 * `TMUX_SUBCOMMANDS_REFUSED` lists them and `seams.spec.ts` scans every source file.
 */
export async function endSessions(
  hangupMs: number = HANGUP_GRACE_MS,
  killMs: number = KILL_GRACE_MS,
): Promise<void> {
  await Promise.all(liveSessions().map((session) => endSession(session, hangupMs, killMs)));
  // Two turns of the loop after the last exit, so anything node-pty queued behind it
  // -- releasing and finalising the threadsafe function it just used -- also runs
  // while there is still an environment to run it in.
  for (let turn = 0; turn < 2; turn += 1) {
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
}

/**
 * What a pane showing the bare login shell says about itself (KYO-283.c1, KYO-293.c3).
 *
 * The bare shell is no longer where a pane OPENS -- KYO-293 puts the pane in the
 * project's session, because claude started in a bare shell ends when the app quits and
 * the session is lost with it. It is where the leftmost control
 * takes a reader who wants a shell of their own, and the sentence is unchanged in what
 * it has to say: this is your own shell, as a new terminal window would give it to you,
 * in no session. `ENDS_AT_QUIT` is the point rather than a footnote now: this is the
 * one surface in the pane whose contents a Cmd-Q really does end.
 */
export const BARE_PANE_NOTICE =
  "a bare login shell -- your own $SHELL, on your login shell's environment and PATH, " +
  "attached to no tmux session, exactly as a new terminal window would open. " +
  `${ENDS_AT_QUIT} Click a session above to attach this pane to it; nothing here ever ` +
  "ends a session.";

/**
 * What the pane's status line says. Never empty, in any branch: a pane that is a
 * private shell and a pane that is the session PyCharm is attached to look
 * identical until one of them says which it is (law 6).
 */
export function noticeFor(
  mode: TerminalMode,
  session: string | null,
  reason: string | null,
): string {
  return mode === "bare"
    ? (reason ?? BARE_PANE_NOTICE)
    : // The controller's session, which `kyo controller` started and this app only
      // views (KYO-474): the sentence says so, and names the CLI verb that joins it.
      `orchestrator session ${session} -- this app never ends it: closing this tab, the ` +
        "window or quitting the app only detaches this view, and claude keeps running; " +
        "reopening the tab attaches again, scrollback and all. Run " +
        `\`${controllerJoinCommand(session ?? "")}\` in any other terminal on that machine ` +
        "and both sides drive the same session.";
}

/**
 * Everything a pane's ptys are built from, computed once and spawning nothing.
 *
 * Split out of KYO-283's `startTerminal`, which computed this and immediately spawned a
 * login shell -- the one shape it had. A pane now has two, started at different moments
 * and possibly both, and they must be built from ONE environment: the bare shell a
 * reader drops back to has to be the same shell, on the same PATH and the same
 * `KYOSEI_*`, as the session they left, or the leftmost control would be a quiet
 * project switch.
 *
 * `TMUX` and `TMUX_PANE` are deleted unconditionally. They name the session the APP was
 * launched inside -- `npm start` from a developer's own tmux -- and a pty that
 * carries them tells every tmux command in it that it is already inside a session:
 * `attach-session` refuses outright ("sessions should be nested with care"), which
 * would make the client pty exit at once, and everything else silently targets that
 * server instead of the one the pills are read from.
 */
export function preparePane(spec: TerminalSpec): PaneSetup {
  // KYO-229: the environment a terminal on this machine would have, not the one this
  // process happens to carry. From a Finder launch those are different in the one way
  // that matters -- PATH -- and every symptom the record was filed about follows from
  // it: no tmux found, `command not found: npm` inside the pane, a controller session
  // started in a shell this app owns and kills at quit.
  const environment = spec.environment ?? loginEnvironment();
  const env = terminalEnvironment(process.env, environment);
  const shell = env.SHELL || DEFAULT_SHELL;
  // KYO-450: PATH is the login shell's, with nothing in front of it. The app used to
  // put a directory of its own `kyo` shims there (KYO-111); with the bundled runtime
  // gone, `kyo` in a pane is the `kyo` in Terminal.app, which is the point (KYO-443).
  env.TERM = "xterm-256color";
  env.KYOSEI_DESKTOP = "1";
  // An app launched from a pane of an earlier build inherits that pane's shim id; it
  // names a shim that no longer exists, so it does not travel into this pane's sessions.
  delete env[RETIRED_SHIM_ID_ENV];
  delete env.TMUX;
  delete env.TMUX_PANE;
  const ssh = spec.ssh ?? null;

  // The launch facts reach `planTmux` because the no-tmux sentence names a variable,
  // and how a reader sets one depends on how this app was launched (KYO-185).
  const launch = spec.launch ?? launchFacts();
  // A dev box's pane looks for no tmux here: its client is ssh, and the tmux it reaches
  // is the one `kyo controller attach` finds on the other machine.
  const plan: TmuxPlan =
    ssh !== null
      ? { binary: null, session: spec.projectSession ?? null, reason: spec.sessionProblem ?? null }
      : planTmux(
          spec.projectSession ?? null,
          spec.sessionProblem ?? null,
          env,
          launch,
          environment,
        );

  return {
    env,
    shell,
    plan,
    socket: plan.binary === null ? null : controllerSocketPath(env),
    spelled: controllerSocketSpelled(env),
    ssh,
    kyoOnPath: findOnPath("kyo", env.PATH),
  };
}

/** What every view of this pane is told about it, for one surface and one session. */
export function paneInfo(
  id: string | null,
  cwd: string,
  setup: PaneSetup,
  showing: PaneSurface,
  session: string | null,
  sessions: TerminalOpen["sessions"] = null,
): TerminalOpen {
  const mode: TerminalMode = showing === "client" && session !== null ? "tmux" : "bare";
  return {
    ok: true,
    cwd,
    shell: setup.shell,
    kyoOnPath: setup.kyoOnPath,
    problem: null,
    mode,
    session: mode === "tmux" ? session : null,
    sessionId: id,
    showing,
    join:
      setup.plan.session === null
        ? null
        : setup.ssh === null
          ? controllerJoinCommand(setup.plan.session)
          : controllerJoinOverSsh(setup.ssh.host, setup.plan.session),
    // The two facts the pill row is built from.
    projectSession: setup.plan.session,
    socket: setup.socket,
    // Where the session lives, and the host a dev box's client runs ssh to (KYO-474).
    where: setup.ssh === null ? "local" : "ssh",
    sshHost: setup.ssh?.host ?? null,
    offer: null,
    notice: noticeFor(
      mode,
      mode === "tmux" ? session : setup.plan.session,
      // A bare pane that is bare because tmux is missing says WHY; a bare pane the
      // reader asked for says what a bare pane is.
      mode === "bare" ? setup.plan.reason : null,
    ),
    sessions,
  };
}

/** The reader's own login shell, in a pty of its own. Spawned at most once per pane. */
export function spawnBarePty(setup: PaneSetup, cwd: string, size: TerminalSize): IPty {
  return spawnPty(setup.shell, [], {
    name: "xterm-256color",
    cols: Math.max(2, size.cols),
    rows: Math.max(1, size.rows),
    cwd,
    env: setup.env,
  });
}

/**
 * The pane's tmux client, in a pty of its own -- the whole of KYO-293.c2.
 *
 * The file is the tmux BINARY and the argv is `clientAttachArgs`, so the process in
 * this pty is the client itself. No shell is spawned, nothing is written into anything,
 * and there is therefore nothing that can be busy: the attach happens because a process
 * started, not because a line of text was accepted by a reader.
 *
 * It also makes the client's tty this app's own by construction (`ptyTty`), which is
 * what `detach-client -t <tty>` needs and what KYO-283 had no reliable way to get.
 */
export function spawnClientPty(
  setup: PaneSetup,
  binary: string,
  socket: string,
  session: string,
  cwd: string,
  size: TerminalSize,
): IPty {
  return spawnPty(binary, clientAttachArgs(socket, session), {
    name: "xterm-256color",
    cols: Math.max(2, size.cols),
    rows: Math.max(1, size.rows),
    cwd,
    // The CLI's scrubbed environment (`DROPPED_ENV`): a `TMUX` here would make tmux
    // refuse to nest, and the attach is to a server named by its socket either way.
    env: controllerTmuxEnv(setup.env),
  });
}

/**
 * Start (or replace) this pane's tmux client and show it.
 *
 * The caller has already made sure the session exists and has been prepared; this is
 * the pty half, and the only place a client is created. An existing client must be
 * ended by the caller first -- `main.ts` does that with `detach-client` so the detach
 * is tmux's own act rather than a signal -- and `droppingClient` is what keeps that
 * teardown from being reported to the reader as something they did.
 */
export function startClient(
  session: PtySession,
  binary: string,
  socket: string,
  target: string,
  size: TerminalSize,
): PaneScreen {
  const pty = spawnClientPty(session.setup, binary, socket, target, session.cwd, size);
  const screen = addScreen(session, "client", pty, size);
  session.clientSession = target;
  session.showing = "client";
  session.info = paneInfo(session.id, session.cwd, session.setup, "client", target);
  return screen;
}

/**
 * Start (or replace) a dev box pane's client: ssh, running `kyo controller attach` there.
 *
 * The same pty machinery as `startClient`, and the same guarantee: the process in the
 * pty IS the client, nothing is typed into anything, and ending it -- a closed tab, a
 * quit, a re-target -- is a hangup to ssh, which the far end's tmux reads as a client
 * going away. Nothing ends the session there (KYO-474.c4). Started in the home
 * directory, because the project's directory is on the other machine.
 */
export function startSshClient(
  session: PtySession,
  target: string,
  size: TerminalSize,
): PaneScreen | null {
  const ssh = session.setup.ssh;
  if (ssh === null) return null;
  const [file, ...args] = ssh.attachArgv(target);
  if (file === undefined) return null;
  const pty = spawnPty(file, args, {
    name: "xterm-256color",
    cols: Math.max(2, size.cols),
    rows: Math.max(1, size.rows),
    cwd: homedir(),
    env: controllerTmuxEnv(session.setup.env),
  });
  const screen = addScreen(session, "client", pty, size);
  session.clientSession = target;
  session.showing = "client";
  session.info = paneInfo(session.id, session.cwd, session.setup, "client", target);
  return screen;
}

/** How long a repainting tmux client is held one row short (KYO-258). */
export const REPAINT_STEP_MS = 60;

/**
 * Make a tmux client draw its whole screen again, for a screen that was just rebuilt.
 *
 * The replay is the raw tail of what the client wrote, which for a full-screen tmux is
 * cursor moves over a screen the new xterm never had. tmux repaints on SIGWINCH, and
 * the kernel sends that only when the size actually changes -- a reopened tab asks for
 * the size the pty already has -- so the size steps down a row and back (KYO-258).
 * Another client attached to the session sees the same one-row flicker.
 *
 * The step back restores the size the screen holds WHEN IT FIRES, never the one this
 * was called with (KYO-364). A pane that opens measures its screen, asks for this, and
 * only then lays out the session strip above the screen, which costs it two rows; the
 * renderer reports the smaller size inside the step's delay, and a step back to the
 * size captured at the start then left the pty two rows taller than the xterm drawing
 * it -- tmux placed the cursor, and everything typed, two rows below the prompt until
 * something else resized the window.
 */
export function repaintTmuxClient(
  client: PaneScreen | null,
  size: TerminalSize,
  later: (run: () => void, ms: number) => void = (run, ms) => void setTimeout(run, ms),
): void {
  if (client === null || client.exited || size.rows < 3) return;
  try {
    client.pty.resize(size.cols, size.rows - 1);
  } catch {
    return;
  }
  later(() => {
    try {
      client.pty.resize(client.size.cols, client.size.rows);
    } catch {
      // The client exited in between; there is nothing left to repaint.
    }
  }, REPAINT_STEP_MS);
}

/** Show the pane's bare login shell, spawning it the first time it is asked for. */
export function showBare(session: PtySession, size: TerminalSize): PaneScreen {
  const already = session.bare;
  const screen =
    already !== null && !already.exited
      ? already
      : addScreen(session, "bare", spawnBarePty(session.setup, session.cwd, size), size);
  session.showing = "bare";
  session.info = paneInfo(session.id, session.cwd, session.setup, "bare", null);
  return screen;
}
