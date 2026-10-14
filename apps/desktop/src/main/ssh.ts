// Running ordinary CLI commands on a machine: this one, or another over the user's
// own SSH (KYO-460, per the decision KYO-452).
//
// THE APP CHANGES A MACHINE ONLY THROUGH COMMANDS A PERSON COULD TYPE. New project runs
// `mkdir -p`, `kyo init` and `kyo controller start`; Start controller runs `kyo
// controller start`; the directory picker is fed by a listing command. Nothing here is a
// server route: on another machine every one of them is `ssh <host> zsh -lc '<command>'`
// with the user's own keys and `~/.ssh/config`, so what the app may do there is exactly
// what that person may do from a terminal.
//
// ONE BUILDER (`commandArgv`), checked for its exact argv. A command is an argv, never a
// string: on this machine it is spawned with no shell in between (and `kyo` itself runs
// through `runKyo`, the app's one invocation of its own CLI); over SSH the argv is quoted
// word by word into one command line, and that line is quoted once more as the single
// argument of `zsh -lc`, because ssh joins everything after the host with spaces and
// hands the result to the remote account's shell to split again.
//
// A LOGIN SHELL ON THE OTHER END (KYO-458.m1). A non-interactive `ssh host kyo ...` gets
// sshd's bare PATH, which has no `~/.local/bin`; `zsh -lc` reads the login profile first,
// so `kyo` resolves as it does in the person's own terminal there.
//
// SSH'S OWN WORDS ON FAILURE. Whatever the far end or ssh itself printed on stderr is the
// problem, verbatim (`failureText`) -- "Permission denied (publickey)", "Could not
// resolve hostname", "zsh:1: command not found: kyo" are each the remedy's first clue, and
// a paraphrase would hide which of them it was. `BatchMode=yes` makes a missing key a
// refusal rather than a password prompt nobody can answer: the app has no terminal.
//
// THE RUNNER IS INJECTABLE. Every function that runs anything takes a `Runner`, and the
// app's own is `runProcess`; the ssh program itself is `KYOSEI_SSH` when set, so a check
// hands the launched app a stand-in and no check ever reaches a real host.
//
// Electron-free: `main.ts` wires it to IPC, and the checks import it directly.

import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type {
  NewProjectOutcome,
  ProjectServerEntry,
  RemoteFacts,
  StartedControllerFacts,
  StoreLocation,
} from "../shared/protocol";
import { type KyoResolution, runKyo, shellCommandLine, shellQuote } from "./kyo";

/** Names the ssh program the app runs, in place of the system's. A check's seam. */
export const SSH_PROGRAM_ENV = "KYOSEI_SSH";

/** The system's ssh. */
export const DEFAULT_SSH_PROGRAM = "/usr/bin/ssh";

/**
 * Put in front of every host. No prompts (there is no terminal to answer one), and a
 * bounded wait for a machine that is asleep or off the tailnet.
 */
export const SSH_OPTIONS: readonly string[] = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"];

/** The far end's shell: a login one, for its PATH (KYO-458.m1). */
export const REMOTE_SHELL: readonly string[] = ["zsh", "-lc"];

/** Where the server entries' SSH hosts are kept, in the app's own data directory. */
export const SSH_HOSTS_FILE = "ssh-hosts.json";

/** Printed before a listing, so a login profile's own output is never read as folders. */
export const LISTING_MARK = "__KYOSEI_LISTING_7d2e__";

/** Where a command runs. */
export type CommandHost =
  | {
      kind: "local";
      /** This machine's name, for sentences. */
      machine: string;
      /** The app's own `kyo`: run through `runKyo`, the one invocation (KYO-111.c1). */
      resolution: KyoResolution;
    }
  | {
      kind: "ssh";
      /** The SSH host, as the person's ssh understands it: `devbox`, `me@devbox.ts.net`. */
      host: string;
      machine: string;
      /** The ssh program. */
      program: string;
    };

export interface RunResult {
  code: number;
  stdout: string;
  stderr: string;
}

/** Runs one argv. The app's is `runProcess`; a check passes its own. */
export type Runner = (
  argv: readonly string[],
  options: { timeoutMs: number },
) => Promise<RunResult>;

/** The ssh program this process runs: `KYOSEI_SSH`, or the system's. */
export function sshProgram(env: Record<string, string | undefined> = process.env): string {
  const named = env[SSH_PROGRAM_ENV]?.trim();
  return named === undefined || named === "" ? DEFAULT_SSH_PROGRAM : named;
}

/**
 * Why a string cannot be an SSH host, or null when it can.
 *
 * A leading `-` would be read as an option -- `-oProxyCommand=...` is a command -- and
 * the argv also puts `--` in front of the host, so this is the second of two locks.
 */
export function sshHostProblem(host: string): string | null {
  if (host.trim() === "") return "the SSH host is empty";
  if (host !== host.trim() || /\s/.test(host)) return "an SSH host has no spaces in it";
  if (host.startsWith("-")) return "an SSH host cannot begin with '-'";
  // biome-ignore lint/suspicious/noControlCharactersInRegex: refusing them is the point.
  if (/[\x00-\x1f\x7f]/.test(host)) return "an SSH host has no control characters in it";
  return null;
}

/** A command host for another machine, reached over SSH. */
export function sshCommandHost(host: string, program: string = sshProgram()): CommandHost {
  return { kind: "ssh", host, machine: host, program };
}

/** Asks ssh for a terminal at the far end: the terminal tab's client needs one (KYO-474). */
export const SSH_TTY_OPTION = "-t";

/**
 * The exact argv that runs `words` on `host`.
 *
 * Local: the words themselves, spawned with no shell. SSH:
 * `ssh -o BatchMode=yes -o ConnectTimeout=10 -- <host> zsh -lc '<words, quoted>'`, and
 * with `tty`, `ssh -t ...` -- the terminal tab's client, which runs in a pty of its own
 * and needs one at the far end for tmux to draw on. `BatchMode=yes` stays either way:
 * a password prompt in the pane would be one nothing in the app answers for.
 */
export function commandArgv(
  host: CommandHost,
  words: readonly string[],
  options: { tty?: boolean } = {},
): string[] {
  if (host.kind === "local") return [...words];
  return [
    host.program,
    ...(options.tty === true ? [SSH_TTY_OPTION] : []),
    ...SSH_OPTIONS,
    "--",
    host.host,
    ...REMOTE_SHELL,
    shellQuote(shellCommandLine(words)),
  ];
}

/** The words that attach a terminal to a project's controller session (KYO-474). */
export function controllerAttachWords(session: string): string[] {
  return ["kyo", "controller", "attach", session];
}

/**
 * The terminal tab's client for a dev box: `ssh -t <host> zsh -lc 'kyo controller attach
 * <prefix>'`, through the one builder. The far end's `kyo` resolves its own socket and
 * the exact target, so the app states neither; a `kyo` there that predates `attach`
 * refuses with its own "invalid choice", which the pane shows verbatim.
 */
export function controllerAttachArgv(host: CommandHost, session: string): string[] {
  return commandArgv(host, controllerAttachWords(session), { tty: true });
}

/** The app's runner: one process, no shell, bounded, never throwing. */
export const runProcess: Runner = (argv, { timeoutMs }) =>
  new Promise((resolve) => {
    const [file, ...args] = argv;
    if (file === undefined) {
      resolve({ code: 1, stdout: "", stderr: "nothing to run" });
      return;
    }
    execFile(
      file,
      args,
      { encoding: "utf8", timeout: timeoutMs, maxBuffer: 16 * 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error === null) {
          resolve({ code: 0, stdout, stderr });
          return;
        }
        const status: unknown = (error as { code?: unknown }).code;
        if (typeof status === "string") {
          // It never ran: ENOENT and the like. Said as the system said it.
          resolve({ code: 127, stdout, stderr: `could not run ${file}: ${error.message}` });
          return;
        }
        const code = typeof status === "number" && status !== 0 ? status : 1;
        const late = (error as { killed?: boolean }).killed
          ? `${stderr === "" ? "" : "\n"}(${file} did not finish within ${Math.round(timeoutMs / 1000)}s and was stopped)`
          : "";
        resolve({ code, stdout, stderr: `${stderr}${late}` });
      },
    );
  });

/** Run `words` on `host` through `run`. */
export function runOn(
  host: CommandHost,
  words: readonly string[],
  run: Runner,
  timeoutMs: number,
): Promise<RunResult> {
  return run(commandArgv(host, words), { timeoutMs });
}

/**
 * `kyo <args>` on `host`. Over SSH: `kyo` as the far end's login shell finds it. Here:
 * the app's own resolved `kyo`, through `runKyo` -- this file is not a second route to
 * this machine's CLI -- whose refusal to run (no runnable source) is the stderr.
 */
export async function runKyoOn(
  host: CommandHost,
  args: readonly string[],
  run: Runner,
  timeoutMs: number,
): Promise<RunResult> {
  if (host.kind === "ssh") return runOn(host, ["kyo", ...args], run, timeoutMs);
  try {
    return await runKyo(host.resolution, args, { cwd: homedir(), timeoutMs });
  } catch (error) {
    return { code: 127, stdout: "", stderr: (error as Error).message };
  }
}

/**
 * What a failed command said, verbatim: its stderr, trimmed of surrounding blank lines.
 * Only when it said nothing is a sentence made up, and that one names the exit status.
 */
export function failureText(result: RunResult, host: CommandHost): string {
  const said = result.stderr.replace(/^\s*\n/, "").trimEnd();
  if (said !== "") return said;
  return host.kind === "ssh"
    ? `ssh to ${host.host} exited with status ${result.code} and printed nothing on stderr`
    : `the command exited with status ${result.code} and printed nothing on stderr`;
}

/**
 * The JSON document a `kyo --json` command printed: its last line that parses as an
 * object. A login profile that prints something first cannot hide it.
 */
export function lastJsonObject(stdout: string): Record<string, unknown> | null {
  const lines = stdout.split("\n").map((line) => line.trim());
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index] ?? "";
    if (!line.startsWith("{")) continue;
    try {
      const parsed: unknown = JSON.parse(line);
      if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Not this line.
    }
  }
  return null;
}

// ------------------------------------------------------------ the directory picker

/** One folder's contents, as the picker shows them: its absolute path and its folders. */
export interface Listing {
  path: string;
  folders: string[];
}

/**
 * The listing command: go there (home when `path` is null), print the mark, the
 * absolute path, then one entry per line with a `/` after each directory.
 *
 * `sh -c` with the path as `$1`, so the path is data even inside the script, and the
 * same argv works here and over SSH. `command ls` steps past any alias a profile made.
 */
export function listingWords(path: string | null): string[] {
  return [
    "/bin/sh",
    "-c",
    `cd -- "\${1:-$HOME}" && printf '%s\\n' ${LISTING_MARK} && pwd -P && command ls -1p`,
    "sh",
    path ?? "",
  ];
}

/** The folders in a listing's output, sorted as a picker sorts them; null if unreadable. */
export function parseListing(stdout: string): Listing | null {
  const lines = stdout.split("\n");
  const mark = lines.indexOf(LISTING_MARK);
  if (mark < 0) return null;
  const path = lines[mark + 1];
  if (path === undefined || !path.startsWith("/")) return null;
  const folders = lines
    .slice(mark + 2)
    .filter((line) => line.endsWith("/") && line !== "/")
    .map((line) => line.slice(0, -1))
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }));
  return { path, folders };
}

/** `name` inside `parent`. */
export function childPath(parent: string, name: string): string {
  return parent === "/" ? `/${name}` : `${parent.replace(/\/+$/, "")}/${name}`;
}

/** The breadcrumbs for an absolute path: `/`, then each folder down to it. */
export function breadcrumbs(path: string): { label: string; path: string }[] {
  const crumbs = [{ label: "/", path: "/" }];
  let at = "";
  for (const part of path.split("/").filter((segment) => segment !== "")) {
    at = `${at}/${part}`;
    crumbs.push({ label: part, path: at });
  }
  return crumbs;
}

/** Why a name cannot be a new folder's, or null when it can. */
export function folderNameProblem(name: string): string | null {
  if (name.trim() === "") return "the folder needs a name";
  if (name.includes("/")) return "a folder name cannot contain '/'";
  if (name === "." || name === "..") return `"${name}" is not a folder name`;
  // biome-ignore lint/suspicious/noControlCharactersInRegex: refusing them is the point.
  if (/[\x00-\x1f\x7f]/.test(name)) return "a folder name has no control characters in it";
  return null;
}

/** List one folder on `host`. */
export async function listDirectory(
  host: CommandHost,
  path: string | null,
  run: Runner,
): Promise<{ listing: Listing | null; problem: string | null }> {
  const result = await runOn(host, listingWords(path), run, 30_000);
  if (result.code !== 0) return { listing: null, problem: failureText(result, host) };
  const listing = parseListing(result.stdout);
  if (listing === null) {
    return {
      listing: null,
      problem: `the listing of ${path ?? "the home folder"} on ${host.machine} could not be read`,
    };
  }
  return { listing, problem: null };
}

/** Make one new folder on `host` (`mkdir`, not `-p`: an existing name is a refusal). */
export async function makeFolder(
  host: CommandHost,
  path: string,
  run: Runner,
): Promise<{ ok: boolean; problem: string | null }> {
  const result = await runOn(host, ["mkdir", "--", path], run, 30_000);
  return result.code === 0
    ? { ok: true, problem: null }
    : { ok: false, problem: failureText(result, host) };
}

// ------------------------------------------------------------ New project

/** The fields of `kyo controller start --json` the app shows; null when it is not that. */
export function parseStarted(data: Record<string, unknown> | null): StartedControllerFacts | null {
  if (data === null || data.ok !== true || typeof data.session !== "string") return null;
  const text = (value: unknown): string | null => (typeof value === "string" ? value : null);
  return {
    session: data.session,
    pid: typeof data.pid === "number" ? data.pid : null,
    cwd: text(data.cwd),
    startedWith: text(data.started_with),
    attach: text(data.attach),
    remoteControlName: text(data.remote_control_name),
    trustFolder: waitingForTrust(data),
  };
}

/**
 * The folder Claude Code's trust prompt is asking about, from a `kyo controller start`
 * or `list` row (KYO-567): `trust_folder`, or the session's `cwd` when an answer says
 * `waiting_for_trust` without one; null when it is not waiting, or a kyo older than
 * KYO-567 said nothing about it.
 */
export function waitingForTrust(data: Record<string, unknown>): string | null {
  if (data.waiting_for_trust !== true) return null;
  if (typeof data.trust_folder === "string" && data.trust_folder !== "") return data.trust_folder;
  if (typeof data.cwd === "string" && data.cwd !== "") return data.cwd;
  return typeof data.root === "string" ? data.root : "";
}

/** Run `kyo controller start --json <root>` on `host`. */
export async function startControllerSession(
  host: CommandHost,
  root: string,
  run: Runner,
): Promise<{ started: StartedControllerFacts | null; problem: string | null }> {
  const result = await runKyoOn(host, ["controller", "start", "--json", root], run, 60_000);
  if (result.code !== 0) return { started: null, problem: failureText(result, host) };
  const started = parseStarted(lastJsonObject(result.stdout));
  if (started === null) {
    return {
      started: null,
      problem: `kyo controller start on ${host.machine} exited 0 but reported no session`,
    };
  }
  return { started, problem: null };
}

/**
 * New project: `mkdir -p`, `kyo init` (which registers it, KYO-455), and -- when asked
 * -- `kyo controller start`, each a separate command so a failure names its step.
 */
export async function createProject(
  host: CommandHost,
  root: string,
  options: { startController: boolean; storeLocation: StoreLocation },
  run: Runner,
): Promise<NewProjectOutcome> {
  const outcome: NewProjectOutcome = {
    ok: false,
    root,
    machine: host.machine,
    name: null,
    prefix: null,
    registered: false,
    controller: null,
    failedStep: null,
    problem: null,
  };
  if (!root.startsWith("/")) {
    return { ...outcome, failedStep: "mkdir", problem: `${root} is not an absolute path` };
  }
  const made = await runOn(host, ["mkdir", "-p", "--", root], run, 30_000);
  if (made.code !== 0) {
    return { ...outcome, failedStep: "mkdir", problem: failureText(made, host) };
  }
  // KYO-558: bare `kyo init` in a folder with no store writes nothing and names the
  // routes, so the dialog's store-location choice (KYO-573) names one: the split store's
  // defaults, or the store inside the code repository.
  const layout = options.storeLocation === "in-repo" ? "--in-repo" : "--defaults";
  const initialised = await runKyoOn(host, ["init", layout, "--json", root], run, 120_000);
  if (initialised.code !== 0) {
    return { ...outcome, failedStep: "init", problem: failureText(initialised, host) };
  }
  const data = lastJsonObject(initialised.stdout);
  const registry =
    data !== null && data.registry !== null && typeof data.registry === "object"
      ? (data.registry as Record<string, unknown>)
      : null;
  const initDone: NewProjectOutcome = {
    ...outcome,
    ok: true,
    root: typeof data?.code_root === "string" ? data.code_root : root,
    name: typeof registry?.name === "string" ? registry.name : null,
    prefix: typeof data?.project_prefix === "string" ? data.project_prefix : null,
    registered: registry?.registered === true,
  };
  if (!options.startController) return initDone;
  const started = await startControllerSession(host, initDone.root, run);
  if (started.started === null) {
    return { ...initDone, ok: false, failedStep: "controller", problem: started.problem };
  }
  return { ...initDone, controller: started.started };
}

// ------------------------------------------------------------ each server's SSH host

/** The key a server entry is kept under: its address, as the app connects to it. */
export function serverKey(host: string, port: number): string {
  return `${host}:${port}`;
}

function sshHostsPath(userDataDir: string): string {
  return join(userDataDir, SSH_HOSTS_FILE);
}

/** Every SSH host set by hand, by server key. A missing or broken file is none. */
export function readSshHosts(userDataDir: string): Record<string, string> {
  try {
    const raw: unknown = JSON.parse(readFileSync(sshHostsPath(userDataDir), "utf8"));
    const hosts =
      raw !== null && typeof raw === "object" ? (raw as { hosts?: unknown }).hosts : null;
    if (hosts === null || typeof hosts !== "object" || Array.isArray(hosts)) return {};
    const kept: Record<string, string> = {};
    for (const [key, value] of Object.entries(hosts as Record<string, unknown>)) {
      if (typeof value === "string" && sshHostProblem(value) === null) kept[key] = value;
    }
    return kept;
  } catch {
    return {};
  }
}

/** A server entry's SSH host: the one set for it, or its own host name. */
export function sshHostFor(userDataDir: string, host: string, port: number): string {
  return readSshHosts(userDataDir)[serverKey(host, port)] ?? host;
}

/**
 * The machines New project offers: this one, then each dev box connected to before,
 * once per address, in the order given (newest first), each with its SSH host.
 */
export function serverEntries(
  remotes: readonly RemoteFacts[],
  machine: string,
  sshHosts: Record<string, string>,
): ProjectServerEntry[] {
  const entries: ProjectServerEntry[] = [
    { server: null, label: `This machine (${machine})`, sshHost: null },
  ];
  const seen = new Set<string>();
  for (const remote of remotes) {
    const key = serverKey(remote.host, remote.port);
    if (seen.has(key)) continue;
    seen.add(key);
    entries.push({
      server: { host: remote.host, port: remote.port },
      label: remote.host,
      sshHost: sshHosts[key] ?? remote.host,
    });
  }
  return entries;
}

/** Set a server entry's SSH host. Empty, or the host name itself, goes back to the default. */
export function writeSshHost(
  userDataDir: string,
  host: string,
  port: number,
  sshHost: string,
): { ok: boolean; problem: string | null; sshHost: string } {
  const wanted = sshHost.trim();
  const problem = wanted === "" ? null : sshHostProblem(wanted);
  if (problem !== null) return { ok: false, problem, sshHost: sshHostFor(userDataDir, host, port) };
  const hosts = readSshHosts(userDataDir);
  const key = serverKey(host, port);
  if (wanted === "" || wanted === host) delete hosts[key];
  else hosts[key] = wanted;
  try {
    mkdirSync(userDataDir, { recursive: true });
    const path = sshHostsPath(userDataDir);
    const staged = `${path}.${process.pid}.tmp`;
    writeFileSync(staged, `${JSON.stringify({ hosts }, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    renameSync(staged, path);
  } catch (error) {
    return {
      ok: false,
      problem: `could not keep the SSH host: ${(error as Error).message}`,
      sshHost: sshHostFor(userDataDir, host, port),
    };
  }
  return { ok: true, problem: null, sshHost: hosts[key] ?? host };
}
