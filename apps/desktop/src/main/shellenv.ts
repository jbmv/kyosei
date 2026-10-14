// The environment a terminal on this machine would have, asked of the login shell
// once (KYO-229).
//
// WHAT THIS SUPERSEDES, AND WHAT IT DOES NOT.
//
// KYO-185.c4 says no PATH is synthesised on the user's behalf, and that decision
// stands untouched: nothing in this file, or anywhere else in this app, contains the
// string `/opt/homebrew/bin` or any other guess about where a binary lives. What
// changes is that the app no longer has to guess, because it can ASK. A macOS app
// launched from the Finder inherits `PATH=/usr/bin:/bin:/usr/sbin:/sbin` from
// `launchd` (measured, KYO-176), and every tool a user has installed --
// `/opt/homebrew/bin/tmux`, the `npm` a `.zshrc` puts on the path -- is invisible
// from there. Running the user's own login shell and reading the environment it
// produces is not a list somebody maintains by hand; it is the same answer that shell
// gives its own terminals, obtained from the shell rather than from this app's
// opinion of what a Mac usually looks like.
//
// The distinction KYO-185 drew between "tmux is not installed" and "this launch
// cannot see it" is therefore SHARPER here, not gone: with a capture in hand the app
// has searched the reader's real PATH and may say so; with no capture it is back in
// KYO-185's position and says which of the two it cannot tell. `tmuxenv.ts` renders
// both sentences, and `noTmuxReason` takes this file's outcome as its argument.
//
// Electron-free on purpose, like `kyo.ts`, `store.ts`, `facts.ts` and `sessions.ts`:
// everything about the world arrives as an argument, so every branch below is
// exercisable from a plain Node check without launching an app.
//
// WHY THIS FILE MAY START A PROCESS. `seams.spec.ts` holds a guard -- one file, and
// one only, may import `node:child_process` -- whose subject is that there is exactly
// one route from this app to the CLI. That guard is now an ALLOWLIST with a reason
// per entry rather than a single name, and this is the second entry: it runs the
// user's own `$SHELL` and nothing else, constructs no interpreter path, and never
// names `kyo`. The check asserts both halves, so the widening cannot quietly become a
// second way to invoke the CLI.

import { execFileSync } from "node:child_process";
import {
  accessSync,
  appendFileSync,
  constants,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { delimiter, join } from "node:path";

/** The variable that turns the capture off, or names a shell to capture from. */
export const LOGIN_SHELL_ENV = "KYOSEI_LOGIN_SHELL";

const OFF = new Set(["off", "0", "no", "none", "false"]);

/**
 * How long the login shell is given to answer.
 *
 * Bounded for the same reason every other wait in this app is: a `.zshrc` that starts
 * something slow, or blocks on a network mount, must cost the reader the captured
 * environment and never the app. Three seconds is long enough for a heavy profile on
 * a cold cache and short enough that a pane opening is still a pane opening.
 */
export const LOGIN_SHELL_TIMEOUT_MS = 3_000;

/** The shell asked when `$SHELL` names none. The same default `terminal.ts` uses. */
export const DEFAULT_LOGIN_SHELL = process.platform === "win32" ? "powershell.exe" : "/bin/sh";

/**
 * The markers the captured block sits between.
 *
 * An interactive login shell is entitled to print things -- a motd, a version banner,
 * a `.zshrc` that echoes -- and all of it lands on the same stdout as the environment.
 * So the environment is not "whatever came back": it is the lines strictly between
 * these two markers, and a run that produced neither is an outcome
 * (`shell-said-nothing-we-could-read`) rather than a best-effort parse of noise.
 */
export const ENV_BEGIN = "__KYOSEI_ENV_BEGIN_9c1f__";
export const ENV_END = "__KYOSEI_ENV_END_9c1f__";

/**
 * The script the login shell runs.
 *
 * `/usr/bin/env` by absolute path, because the whole point of this call is that the
 * PATH is not yet known to be useful; `printf` is a shell builtin in every shell this
 * can land in. Nothing here is interpolated from anything -- it is a constant string,
 * so there is no quoting question and no way for a project path to reach a shell.
 */
export const ENV_SCRIPT = `printf '%s\\n' ${ENV_BEGIN}; /usr/bin/env; printf '%s\\n' ${ENV_END}`;

/**
 * The flags the shell is asked with: interactive, login, one command.
 *
 * All three matter and each buys something different. `-l` is what sources the
 * profile files a terminal window sources (`.zprofile`, `.bash_profile`); `-i` is what
 * sources the interactive ones (`.zshrc`, `.bashrc`), which is where Homebrew's
 * shellenv, nvm, pyenv and the rest usually live on a developer's machine; `-c` is how
 * one command is run and the shell then exits. A capture with only `-l` misses
 * everything a `.zshrc` does, which can include `npm`.
 */
export const LOGIN_SHELL_FLAGS = "-ilc";

/** Why the app has, or has not, an environment a terminal would recognise. */
export const SHELL_ENV_OUTCOMES = [
  /** The login shell ran and its environment was read. */
  "captured",
  /** `KYOSEI_LOGIN_SHELL` is off, by request. */
  "turned-off",
  /** Not a platform where a POSIX login shell is the question. */
  "unsupported-platform",
  /** The shell could not be started at all. */
  "shell-would-not-run",
  /** It ran and did not finish inside `LOGIN_SHELL_TIMEOUT_MS`. */
  "timed-out",
  /** It ran, and what came back carried neither marker. */
  "shell-said-nothing-we-could-read",
] as const;

export type ShellEnvOutcome = (typeof SHELL_ENV_OUTCOMES)[number];

export interface ShellEnvironment {
  /** The captured environment, or null on every outcome but `captured`. */
  env: Record<string, string> | null;
  /** The shell that was asked, whether or not it answered. */
  shell: string;
  outcome: ShellEnvOutcome;
  /** Never empty, on any outcome: a reader always gets a sentence (law 6). */
  why: string;
  /** How long the call took, in ms. Stated so the README's cost claim is measured. */
  tookMs: number;
}

/** Did the capture succeed? The one test every consumer of this actually makes. */
export function captured(environment: ShellEnvironment): boolean {
  return environment.outcome === "captured" && environment.env !== null;
}

// ------------------------------------ a failure is an instant, not a verdict (KYO-266)
//
// The outcomes below are the ones where a shell was ACTUALLY TRIED and the trying is
// what failed: a second ask, a moment later, can answer differently. The two that are
// missing are missing for a reason each, and neither is "it probably will not help":
//
//   `turned-off`             decided by `KYOSEI_LOGIN_SHELL` in this process's own
//                            environment, which nothing can change while the process
//                            runs -- so re-asking is guaranteed to return this same
//                            answer, and `why` correctly says relaunch.
//   `unsupported-platform`   decided by the platform. Same shape, more so.
//
// `captured` is absent because it is the success, and a success is cached for the
// process exactly as KYO-229 left it: the login shell's environment is a property of
// the machine and the user, and re-asking spends a shell start-up for the same answer.
//
// ONE PREDICATE, TWO CONSUMERS (law 3). `loginEnvironment()` re-asks when this is
// true, and `noTmuxReason` promises the reader another pane will re-ask when this is
// true. Written twice, the sentence could survive the behaviour changing; written
// once, a pane cannot claim a retry that will not happen.
export const RETRIABLE_OUTCOMES: readonly ShellEnvOutcome[] = [
  "shell-would-not-run",
  "timed-out",
  "shell-said-nothing-we-could-read",
];

/**
 * Could asking again answer differently?
 *
 * The cost of being wrong in each direction is what sets this. Saying yes when the
 * answer will not change costs one login-shell start-up, once, on the next pane.
 * Saying no when it would have changed is the defect KYO-266 was filed about: one
 * transient timeout at launch, and every pane for the rest of the app's life runs on
 * `launchd`'s four-directory PATH with no tmux, no recovery but a relaunch, and one
 * clause in a pane notice as the only trace.
 */
export function worthAskingAgain(environment: ShellEnvironment): boolean {
  return RETRIABLE_OUTCOMES.includes(environment.outcome);
}

/**
 * Which shell to ask.
 *
 * `KYOSEI_LOGIN_SHELL` may name one, which is the same seam `KYOSEI_TMUX` is: a way
 * for a consumer to point the app at a shell it would not have chosen, and the way the
 * checks drive a fixture shell rather than the developer's own. `$SHELL` otherwise,
 * because that is the shell the operating system says this user's terminals get.
 */
export function loginShellFor(env: Record<string, string | undefined> = process.env): string {
  const named = (env[LOGIN_SHELL_ENV] ?? "").trim();
  if (named !== "" && !OFF.has(named.toLowerCase())) return named;
  const shell = (env.SHELL ?? "").trim();
  return shell === "" ? DEFAULT_LOGIN_SHELL : shell;
}

/** Has somebody turned the capture off? */
export function loginShellTurnedOff(
  env: Record<string, string | undefined> = process.env,
): boolean {
  return OFF.has((env[LOGIN_SHELL_ENV] ?? "").trim().toLowerCase());
}

/**
 * Read one `env(1)` listing into a map.
 *
 * `env` prints `NAME=value` a line at a time, and a value containing a newline is
 * printed across several lines with no marker saying so. The rule here is stated
 * rather than assumed: a line that begins with a legal variable name followed by `=`
 * starts a new variable, and any other line is a continuation of the previous one,
 * joined back with the newline `env` printed. That is the only reading that round-trips
 * a multi-line value, and it is deterministic -- there is no heuristic in it and no
 * line is dropped.
 *
 * Exported because it is the whole of the parsing, and a check drives it directly
 * rather than through a subprocess.
 */
export function parseEnvBlock(text: string): Record<string, string> {
  const assignment = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/;
  const out: Record<string, string> = {};
  let current: string | null = null;
  for (const line of text.split("\n")) {
    const match = assignment.exec(line);
    if (match !== null) {
      current = match[1] as string;
      out[current] = match[2] as string;
      continue;
    }
    if (current === null) continue;
    out[current] = `${out[current] ?? ""}\n${line}`;
  }
  return out;
}

/**
 * The lines strictly between the two markers, or null when a marker is missing.
 *
 * One newline is stripped from each end, and only one: `printf '%s\n'` puts the marker
 * on a line of its own, so the slice between them always opens and closes with the
 * newline that terminated a marker line rather than with anything `env` printed. Left
 * in, the trailing one would be read as a continuation line and would append a newline
 * to whichever variable `env` happened to print last -- a corruption that would show up
 * only on the last variable, and differently on every machine.
 */
export function between(text: string): string | null {
  const start = text.indexOf(ENV_BEGIN);
  if (start === -1) return null;
  const from = start + ENV_BEGIN.length;
  const end = text.indexOf(ENV_END, from);
  if (end === -1) return null;
  return text.slice(from, end).replace(/^\n/, "").replace(/\n$/, "");
}

function failure(
  shell: string,
  outcome: ShellEnvOutcome,
  why: string,
  tookMs: number,
): ShellEnvironment {
  return { env: null, shell, outcome, why, tookMs };
}

/**
 * Ask the login shell for its environment. Once per process; see `tmuxenv.ts`.
 *
 * Synchronous (`execFileSync`) rather than a promise, and the reason is the timeout:
 * this is the one call in the app whose failure mode is a shell profile that hangs,
 * and `execFileSync`'s `timeout` kills the child rather than merely abandoning a
 * promise that resolves later into an app that has moved on. The caller decides WHEN
 * it happens -- `tmuxenv.ts` runs it lazily, off the path between the app launching
 * and its first window, so a slow profile costs the first pane and never the window.
 *
 * Total: every failure is one of `SHELL_ENV_OUTCOMES` carrying a sentence, and nothing
 * here throws. A machine whose login shell will not run is a machine the app still has
 * to open a pane on.
 */
export function captureLoginEnvironment(
  processEnv: Record<string, string | undefined> = process.env,
  options: { timeoutMs?: number; platform?: string } = {},
): ShellEnvironment {
  const platform = options.platform ?? process.platform;
  const shell = loginShellFor(processEnv);
  const started = Date.now();
  if (loginShellTurnedOff(processEnv)) {
    return failure(
      shell,
      "turned-off",
      `${LOGIN_SHELL_ENV} is off, so this app did not ask a login shell for its ` +
        "environment and every pane runs with the environment this app was launched " +
        `with. To undo that, unset ${LOGIN_SHELL_ENV} and relaunch.`,
      Date.now() - started,
    );
  }
  if (platform === "win32") {
    return failure(
      shell,
      "unsupported-platform",
      "a POSIX login shell is not how this platform's environment is established, so " +
        "nothing was captured and panes run with the environment this app was launched " +
        "with",
      Date.now() - started,
    );
  }
  let stdout: string;
  try {
    stdout = execFileSync(shell, [LOGIN_SHELL_FLAGS, ENV_SCRIPT], {
      encoding: "utf8",
      timeout: options.timeoutMs ?? LOGIN_SHELL_TIMEOUT_MS,
      // The shell's own stderr is a profile's business, not this app's: a `.zshrc`
      // that warns about something is not a reason to discard the environment it
      // produced, and the markers below are what decide whether the answer is usable.
      stdio: ["ignore", "pipe", "ignore"],
      maxBuffer: 8 * 1024 * 1024,
    });
  } catch (error) {
    const failed = error as { signal?: string | null; code?: unknown; message?: string };
    const tookMs = Date.now() - started;
    // `execFileSync` reports a timeout by killing the child, so the signal is the
    // only thing that distinguishes "it hung" from "it refused" -- and the two need
    // different sentences, because one of them names a profile to look at.
    if (failed.signal === "SIGTERM" || failed.code === "ETIMEDOUT") {
      return failure(
        shell,
        "timed-out",
        `${shell} ${LOGIN_SHELL_FLAGS} did not finish within ${
          options.timeoutMs ?? LOGIN_SHELL_TIMEOUT_MS
        }ms, so its environment was not captured; something in that shell's startup ` +
          "files is slow or waiting for input. Panes run with the environment this app " +
          "was launched with until that is fixed.",
        tookMs,
      );
    }
    return failure(
      shell,
      "shell-would-not-run",
      `${shell} could not be run to ask for its environment (${
        failed.message ?? "no message"
      }), so panes run with the environment this app was launched with. ` +
        `${LOGIN_SHELL_ENV} names a different shell to ask.`,
      tookMs,
    );
  }
  const tookMs = Date.now() - started;
  const block = between(stdout);
  if (block === null) {
    return failure(
      shell,
      "shell-said-nothing-we-could-read",
      `${shell} ran but its output carried neither marker this app writes around the ` +
        "environment, so nothing could be read from it without guessing which of its " +
        "lines were the environment and which were its own startup chatter",
      tookMs,
    );
  }
  return {
    env: parseEnvBlock(block),
    shell,
    outcome: "captured",
    why:
      `asked ${shell} ${LOGIN_SHELL_FLAGS} once at startup; this is the environment a ` +
      "terminal on this machine gets",
    tookMs,
  };
}

/**
 * The environment a pty gets: the login shell's, with this launch's own instructions
 * kept.
 *
 * ONE RULE, and it is the whole of the merge:
 *
 *   PATH comes from the login shell whenever there is one.
 *   Everything else this process already carries wins over the capture.
 *   Everything the capture has and this process does not is added.
 *
 * The asymmetry on PATH is the point of the record. A variable already in this
 * process's environment was put there by whoever launched the app -- a shell, a
 * `launchctl setenv`, a check -- and is an explicit instruction this app must not
 * override. `PATH` from `launchd` is not an instruction; it is an ABSENCE, the
 * four-directory minimum a GUI application gets whether anybody meant it or not, and
 * treating it as an instruction is exactly the defect KYO-229 was filed about. So it
 * is the one variable the capture is allowed to replace.
 *
 * What that buys, in the two launches that exist: from a terminal, the captured PATH
 * and the inherited one are the same shell's, so nothing observable changes; from the
 * Finder, the pane gets the PATH the user's terminals get, and `tmux` and `npm`
 * are on it.
 */
export function terminalEnvironment(
  processEnv: Record<string, string | undefined>,
  environment: ShellEnvironment,
): Record<string, string> {
  const out: Record<string, string> = {};
  const capturedEnv = captured(environment) ? (environment.env as Record<string, string>) : {};
  for (const [key, value] of Object.entries(capturedEnv)) out[key] = value;
  for (const [key, value] of Object.entries(processEnv)) {
    if (value === undefined) continue;
    // PATH is the one exception, and only when there is a captured one to prefer.
    if (key === "PATH" && capturedEnv.PATH !== undefined) continue;
    out[key] = value;
  }
  return out;
}

// ------------------------------------------ the tools a headless server needs (KYO-378)
//
// The app's terminals get PATH from the capture above; this machine's headless server
// (`server-main.ts`) is started by launchd with the same four-directory minimum a
// Finder launch gets, and needs the same answer. It takes PATH by `terminalEnvironment`'s
// rule and then looks for the three tools its callers run -- `kyo` (the CLI a user
// installed, which a hook or a pane runs), `tmux` and `claude` -- on that PATH, the
// way a shell's `command -v` would: the first directory holding an executable file of
// that name. Nothing here adds a directory; a tool that is on no directory of the
// captured PATH is reported missing, not searched for elsewhere.
//
// KYO-459 deleted that headless server (`kyo serve`, the CLI's, is the server now); the
// rule and its check remain, as `command -v`'s answer on a captured PATH.

/** The tools the headless server resolves at start, in the order it reports them. */
export const SERVER_TOOLS = ["kyo", "tmux", "claude"] as const;

export type ServerTool = (typeof SERVER_TOOLS)[number];

/** The first executable file named `name` on `path`, or null when none is. */
export function findOnPath(name: string, path: string | undefined | null): string | null {
  for (const dir of (path ?? "").split(delimiter)) {
    if (dir === "") continue;
    const candidate = join(dir, name);
    try {
      if (!statSync(candidate).isFile()) continue;
      accessSync(candidate, constants.X_OK);
      return candidate;
    } catch {
      // Not here, or not executable: the next directory, as a shell would.
    }
  }
  return null;
}

/** Where each of `SERVER_TOOLS` resolves on `path`; null for one that does not. */
export function resolveTools(path: string | undefined | null): Record<ServerTool, string | null> {
  const found = {} as Record<ServerTool, string | null>;
  for (const tool of SERVER_TOOLS) found[tool] = findOnPath(tool, path);
  return found;
}

// ------------------------------------- what happened, where a launch keeps it (KYO-266)
//
// KYO-229 wrote the outcome to the app's stdout, which is the right place when the app
// was started from a terminal and NOWHERE when it was not: a Finder launch discards
// stdout, and the only launch whose PATH is interesting is exactly that one. The
// diagnosis behind this record could not read what happened and had to infer it from
// a symptom -- the app had launched just after the desktop suite ran, on a starved
// machine, so the capture had probably timed out -- an inference about a fact the app
// had in its hand.
//
// So: one line per ask, appended, under the directory this app already keeps its
// layouts and its remembered project in (`app.getPath("userData")` --
// `~/Library/Application Support/@kyosei/desktop` on macOS, for example). The
// directory is passed in rather than read here, for the same reason `facts.ts` takes
// it: this file stays Electron-free and exercisable from a plain Node check.
//
// WHAT IT MAY CONTAIN, stated because a log under a user's home outlives everything
// else here: the outcome, the shell's path, the duration, and the attempt number --
// the same four facts the stdout line carried, plus the attempt, which is the one the
// retry adds. No variable's VALUE, ever, captured or not: the environment this file
// reads is the thing most likely to hold a token on a developer's machine, and a file
// that never holds one cannot leak one.

/** Where the capture outcomes are kept, under the caller's `userData` directory. */
export const SHELL_ENV_LOG_FILE = "shell-env.log";

/**
 * How big the log may get before the oldest lines go.
 *
 * Bounded because the retry made this unbounded: before KYO-266 a process asked once,
 * so a line per launch was a line per launch. Now a machine whose login shell is
 * genuinely broken writes one per pane, and a file nobody empties is not a diagnostic
 * aid. 64 KiB is some hundreds of lines -- more history than any diagnosis of this
 * shape has ever wanted -- and the trim keeps the NEWEST, because the question is
 * always "what happened at this launch".
 */
export const SHELL_ENV_LOG_MAX_BYTES = 64 * 1024;

/** How many lines survive a trim. */
export const SHELL_ENV_LOG_KEEP_LINES = 200;

export function shellEnvLogPath(userDataDir: string): string {
  return join(userDataDir, SHELL_ENV_LOG_FILE);
}

/**
 * One line of it. JSON, one object per line, so the file is greppable by a human and
 * parseable by whatever reads it next without a format anyone has to remember.
 */
export function shellEnvLogLine(
  environment: ShellEnvironment,
  attempt: number,
  at: Date = new Date(),
): string {
  return `${JSON.stringify({
    at: at.toISOString(),
    outcome: environment.outcome,
    shell: environment.shell,
    tookMs: environment.tookMs,
    attempt,
  })}\n`;
}

/**
 * Append one, and never fail a pane because the log could not be written.
 *
 * Total, like `writeProjectState`: a read-only or full `userData` costs the next
 * diagnosis its history, not this reader their terminal. The problem is returned
 * rather than thrown so a caller that wants to say so can.
 */
export function appendShellEnvLog(
  userDataDir: string,
  environment: ShellEnvironment,
  attempt: number,
  at: Date = new Date(),
): string | null {
  const path = shellEnvLogPath(userDataDir);
  try {
    mkdirSync(userDataDir, { recursive: true });
    trimShellEnvLog(path);
    appendFileSync(path, shellEnvLogLine(environment, attempt, at), "utf8");
    return null;
  } catch (error) {
    return (error as Error).message;
  }
}

/** Drop the oldest lines once the file has outgrown its cap. */
function trimShellEnvLog(path: string): void {
  let size: number;
  try {
    size = statSync(path).size;
  } catch {
    return; // Not written yet: the ordinary first launch.
  }
  if (size <= SHELL_ENV_LOG_MAX_BYTES) return;
  const lines = readFileSync(path, "utf8")
    .split("\n")
    .filter((line) => line !== "");
  writeFileSync(path, `${lines.slice(-SHELL_ENV_LOG_KEEP_LINES).join("\n")}\n`, "utf8");
}
