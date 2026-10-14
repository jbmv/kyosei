// The claude tab's controller row: is this project's controller session running, and
// Start one -- by running `kyo controller`, on the machine that holds the project
// (KYO-460, replacing KYO-383's own tmux launcher, per the decision KYO-452).
//
// THE CLI IS THE LAUNCHER. `kyo controller start <root>` (KYO-458) creates the detached
// tmux session on its own server (`tmux -L kyosei`), runs the machine's
// `controller_command` through a login shell in the project directory, names it after
// the prefix, and refuses what it cannot do with a sentence naming the remedy.
// `kyo controller list --json` answers "is it running". This file runs those two and
// turns their JSON into the tab's `ControllerStatus`; it starts no tmux of its own and
// keeps no command of its own -- the command is `controller_command` in that machine's
// `$KYOSEI_HOME/settings.yml` (KYO-462), which only that machine's CLI reads.
//
// WHICH MACHINE. A project on this machine: the app's own resolved `kyo`, through
// `runKyo`, the one invocation (`ssh.ts`'s local host). A project on a dev box: `ssh <host> zsh -lc 'kyo
// controller ...'` with that server entry's SSH host (`main.ts` picks it). Both go
// through `ssh.ts`'s one argv builder and its injectable runner, and a failure is what
// the far end printed on stderr, verbatim.
//
// A SESSION STARTED BY HAND (KYO-390). `kyo controller list` sees the sessions on its
// own tmux server; a controller someone started in a terminal, or in the app's own
// pane, is not there. On this machine the session file still knows it -- the hook
// records the process -- so when the list says nothing runs, a live `sessions.json`
// entry in this project is the answer, and Start is not offered beside it.
//
// Electron-free and spawn-free: every command goes through `ssh.ts` -- a `Runner` for
// SSH, `runKyo` here.

import { trustWhy } from "../shared/controller-trust";
import type { ControllerStatus } from "../shared/protocol";
import type { KyoResolution } from "./kyo";
import { machineName } from "./procfacts";
import { liveSessionAmong, type SessionEntry } from "./sessions";
import {
  type CommandHost,
  failureText,
  lastJsonObject,
  type Runner,
  runKyoOn,
  runProcess,
  startControllerSession,
  waitingForTrust,
} from "./ssh";

/** Where the command Start runs is set, named in every sentence that mentions it. */
export const CONTROLLER_COMMAND_HOME = "controller_command in $KYOSEI_HOME/settings.yml";

/**
 * How long a dev box's answer stands before the tab's next poll asks again. The tab
 * polls every `CONTROLLER_POLL_MS`; over SSH each ask is a connection, so a dev box is
 * asked at most this often, and always again right after a Start.
 */
export const REMOTE_STATUS_TTL_MS = 15_000;

/** This machine, with the app's own `kyo`. */
export function localCommandHost(resolution: KyoResolution): CommandHost {
  return { kind: "local", machine: machineName(), resolution };
}

/** One row of `kyo controller list --json`'s `projects`. */
interface ListedProject {
  project: string | null;
  prefix: string | null;
  root: string;
  session: string | null;
  claudeAlive: boolean;
  pid: number | null;
  startedWith: string | null;
  /** The folder Claude Code's trust prompt asks about, while it is on screen (KYO-567). */
  trustFolder: string | null;
}

/** What `kyo controller list --json` said, the parts this file reads. */
export interface ControllerListing {
  socket: string;
  serverRunning: boolean;
  projects: ListedProject[];
}

/** The listing in a `kyo controller list --json` envelope; null when it is not one. */
export function parseControllerList(
  data: Record<string, unknown> | null,
): ControllerListing | null {
  if (data === null || data.ok !== true || !Array.isArray(data.projects)) return null;
  const text = (value: unknown): string | null => (typeof value === "string" ? value : null);
  const projects: ListedProject[] = [];
  for (const raw of data.projects as unknown[]) {
    if (raw === null || typeof raw !== "object") continue;
    const row = raw as Record<string, unknown>;
    if (typeof row.root !== "string") continue;
    projects.push({
      project: text(row.project),
      prefix: text(row.prefix),
      root: row.root,
      session: text(row.session),
      claudeAlive: row.claude_alive === true,
      pid: typeof row.pid === "number" ? row.pid : null,
      startedWith: text(row.started_with),
      trustFolder: waitingForTrust(row),
    });
  }
  return {
    socket: text(data.socket) ?? "tmux -L kyosei",
    serverRunning: data.server_running === true,
    projects,
  };
}

function samePath(a: string, b: string): boolean {
  const trim = (path: string) => (path.length > 1 ? path.replace(/\/+$/, "") : path);
  return trim(a) === trim(b);
}

/** The status the tab shows before anything is known: nothing running, Start offered. */
function baseStatus(root: string, machine: string): ControllerStatus {
  return {
    root,
    machine,
    session: null,
    running: false,
    pid: null,
    cwd: null,
    startedWith: null,
    command: CONTROLLER_COMMAND_HOME,
    commandIsDefault: false,
    program: null,
    recorded: null,
    why: "",
    problem: null,
    canStart: true,
    trustFolder: null,
  };
}

/**
 * The tab's status from a listing: the row whose root is this project's.
 *
 * Pure, and the one place the listing's meaning is decided -- checked on its own.
 */
export function statusFromListing(
  listing: ControllerListing,
  root: string,
  machine: string,
): ControllerStatus {
  const base = baseStatus(root, machine);
  const row = listing.projects.find((entry) => samePath(entry.root, root));
  if (row === undefined) {
    return {
      ...base,
      why:
        `no orchestrator session for ${root} on ${machine}: it is not in that machine's ` +
        "project registry, so kyo controller list cannot see one (Start names it by its directory)",
    };
  }
  const named = { ...base, session: row.prefix, startedWith: row.startedWith };
  if (row.claudeAlive) {
    const running = {
      ...named,
      running: true,
      pid: row.pid,
      cwd: row.root,
      canStart: false,
      why: `orchestrator session ${row.session ?? row.prefix} is running on ${machine} (${listing.socket}${row.pid === null ? "" : `, pid ${row.pid}`})`,
    };
    if (row.trustFolder === null) return running;
    return {
      ...running,
      trustFolder: row.trustFolder,
      why: trustWhy(row.session ?? row.prefix ?? root, machine, row.trustFolder),
    };
  }
  if (row.session !== null) {
    return {
      ...named,
      why: `orchestrator session ${row.session} on ${machine} is there, and its program has exited`,
    };
  }
  return {
    ...named,
    why:
      `no orchestrator session for ${row.prefix ?? root} on ${machine}` +
      (listing.serverRunning ? "" : ` (no ${listing.socket} server is running there)`),
  };
}

/** What `kyo controller list --json` answered on a host, or why it did not. */
export interface ListingAnswer {
  listing: ControllerListing | null;
  /** ssh's or kyo's own stderr, verbatim, when there is no listing. */
  problem: string | null;
}

/** Ask `kyo controller list --json` on `host`: the controller row's and the terminal's. */
export async function controllerListingOn(host: CommandHost, run: Runner): Promise<ListingAnswer> {
  const result = await runKyoOn(host, ["controller", "list", "--json"], run, 30_000);
  const listing = result.code === 0 ? parseControllerList(lastJsonObject(result.stdout)) : null;
  if (listing !== null) return { listing, problem: null };
  return {
    listing: null,
    problem:
      result.code === 0
        ? "kyo controller list printed no listing (is that machine's kyo older than kyo controller?)"
        : failureText(result, host),
  };
}

/** One pill of a dev box's terminal: a session its `kyo controller list` reported. */
export interface ListedPill {
  name: string;
  project: boolean;
  live: boolean;
}

/**
 * The terminal's pills for a dev box, from its listing (KYO-474.c3): every registered
 * project's session there, this window's project's first -- and its pill even when it
 * has no session, marked not live, so the row always says which one is this project's.
 */
export function pillsFromListing(
  listing: ControllerListing,
  root: string,
  projectSession: string | null,
): ListedPill[] {
  const pills: ListedPill[] = [];
  const own = listing.projects.find((row) => samePath(row.root, root));
  const ownName = own?.session ?? own?.prefix ?? projectSession;
  if (ownName !== null) pills.push({ name: ownName, project: true, live: own?.session != null });
  for (const row of listing.projects) {
    if (row.session === null || row.session === ownName) continue;
    pills.push({ name: row.session, project: false, live: true });
  }
  return pills;
}

/** Is `session` a live session in this listing? */
export function listedLive(listing: ControllerListing, session: string): boolean {
  return listing.projects.some((row) => row.session === session);
}

/**
 * An answer asked at most once per `ttlMs` per key and never twice at once, with a
 * failed answer kept for no time at all: the next ask asks again.
 */
export function answerCache<T>(
  failed: (answer: T) => boolean,
  ttlMs: number = REMOTE_STATUS_TTL_MS,
  now = () => Date.now(),
) {
  const held = new Map<string, { at: number; answer: Promise<T> }>();
  return {
    get(key: string, ask: () => Promise<T>): Promise<T> {
      const entry = held.get(key);
      if (entry !== undefined && now() - entry.at < ttlMs) return entry.answer;
      const answer = ask();
      held.set(key, { at: now(), answer });
      void answer.then((value) => {
        if (failed(value) && held.get(key)?.answer === answer) held.delete(key);
      });
      return answer;
    },
    /** Keep this answer as the latest, or forget the key so the next ask is fresh. */
    put(key: string, value: T | null): void {
      if (value === null) held.delete(key);
      else held.set(key, { at: now(), answer: Promise.resolve(value) });
    },
  };
}

/**
 * The sessions this project's hook recorded, as the caller reads them -- through the
 * project's kyo serve (`kyo sessions --json`, KYO-459), never off the disk here.
 */
export type SessionsReader = () => Promise<readonly SessionEntry[]>;

/** A live session this project's hook recorded on this machine, outside `kyo controller`. */
async function startedByHand(
  sessions: SessionsReader | null,
  root: string,
): Promise<SessionEntry | null> {
  if (sessions === null) return null;
  try {
    return liveSessionAmong(await sessions(), root);
  } catch {
    return null;
  }
}

/**
 * Is this project's controller running on `host` -- asked of `kyo controller list`.
 *
 * On this machine, a live session the project's hook recorded outside `kyo
 * controller` is the answer when the list has none (KYO-390).
 */
export async function controllerStatusOn(
  host: CommandHost,
  root: string,
  run: Runner,
  sessions: SessionsReader | null = null,
): Promise<ControllerStatus> {
  const base = baseStatus(root, host.machine);
  const { listing, problem } = await controllerListingOn(host, run);
  if (listing === null) {
    return {
      ...base,
      why: `could not ask kyo controller list on ${host.machine}`,
      problem,
    };
  }
  const status = statusFromListing(listing, root, host.machine);
  if (status.running || host.kind !== "local") return status;
  const outside = await startedByHand(sessions, root);
  if (outside === null) return status;
  return {
    ...status,
    running: true,
    canStart: false,
    pid: outside.pid,
    cwd: outside.cwd,
    recorded: { sessionId: outside.sessionId, state: outside.state },
    why:
      `orchestrator session ${outside.sessionId} is running on ${host.machine}, ` +
      `started outside kyo controller (process ${outside.pid}, ${outside.processName})`,
  };
}

/**
 * Start this project's controller on `host` with `kyo controller start <root>`, and
 * report the session it started -- or, when it refused, its own words.
 */
export async function startControllerOn(
  host: CommandHost,
  root: string,
  run: Runner,
  sessions: SessionsReader | null = null,
): Promise<ControllerStatus> {
  const before = await controllerStatusOn(host, root, run, sessions);
  if (before.running) return before;
  const { started, problem } = await startControllerSession(host, root, run);
  if (started === null) {
    return {
      ...before,
      why: `kyo controller start did not start a session on ${host.machine}`,
      problem,
    };
  }
  const listed =
    started.remoteControlName === null
      ? ""
      : `; claude.ai lists it as ${started.remoteControlName}`;
  const attach = started.attach === null ? "" : `; attach there with ${started.attach}`;
  return {
    ...before,
    session: started.session,
    running: true,
    canStart: false,
    pid: started.pid,
    cwd: started.cwd,
    startedWith: started.startedWith,
    problem: null,
    trustFolder: started.trustFolder,
    why:
      started.trustFolder === null
        ? `started orchestrator session ${started.session} on ${host.machine}` +
          `${started.pid === null ? "" : ` (pid ${started.pid})`}${listed}${attach}`
        : trustWhy(started.session, host.machine, started.trustFolder),
  };
}

/**
 * A dev box's status, asked at most once per `ttlMs` and never twice at once: the tab
 * polls every two seconds, and over SSH each ask is a connection.
 */
export function remoteStatusCache(ttlMs: number = REMOTE_STATUS_TTL_MS, now = () => Date.now()) {
  // A failure is not kept: the next poll asks again.
  return answerCache<ControllerStatus>((answer) => answer.problem !== null, ttlMs, now);
}

// ------------------------------------------------------------ the two members
//
// Three parameters each, as before (KYO-383.c6): the first is named `userDataDir`,
// which is what puts a member on the server's `USER_DATA_FIRST` list; the third is the
// `kyo` resolution, which a server replaces with its own (`RESOLUTION_THIRD`). Only
// the root is the caller's, and what runs is that machine's `kyo controller` with the
// root as its one argument -- no command reaches it from a caller.

/** `StoreService.controllerStatus`: this machine's answer, for a project on it. */
export function controllerStatus(
  userDataDir: string,
  root: string,
  resolution: KyoResolution,
  sessions: SessionsReader | null = null,
): Promise<ControllerStatus> {
  void userDataDir;
  return controllerStatusOn(localCommandHost(resolution), root, runProcess, sessions);
}

/** `StoreService.startController`: `kyo controller start` for a project on this machine. */
export function startController(
  userDataDir: string,
  root: string,
  resolution: KyoResolution,
  sessions: SessionsReader | null = null,
): Promise<ControllerStatus> {
  void userDataDir;
  return startControllerOn(localCommandHost(resolution), root, runProcess, sessions);
}
