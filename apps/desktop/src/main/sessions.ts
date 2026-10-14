// Which claude.ai page the Remote Control pane opens on, and how it knows.
//
// KYO-186 stage 2's reading half. Deliberately Electron-free, exactly like
// `store.ts`, `facts.ts` and `kyo.ts`, so the whole resolution -- including every
// way it can fail -- is exercisable from a plain Node check without launching an
// app. `main.ts` supplies the one fact only it holds (which store root, which it
// re-probes per call) and this file decides what follows from it.
//
// WHERE THE ENTRIES COME FROM: `kyo sessions --json`, asked of the project's kyo
// serve (KYO-459). Until then this file read `sessions.json` straight off disk, as
// `store.ts` read `kyosei.db`; a viewer of another machine's project cannot, and one
// transport for every project means this machine's is read the same way. The CLI
// parses the file (its schema, its problems); this file still believes an entry's URL
// only when it is one this build recognises (`isClaudeUrl`), because the pane it
// points is a logged-in view.
//
// THE DEGRADATION IS THE POINT (KYO-186.c4). The file is written from a hook that
// reaches into the harness's undocumented private files, so it can be absent,
// stale, half-written, or shaped differently than this build expects. Every one of
// those is a NAMED outcome that falls through to the next step, never an exception
// and never a guess -- and the last step is a real page, so the pane always has
// somewhere to be. `kyo dispatch` sets that rule for the Python half of this
// coupling; this is the same rule on the app's side of it.

import { hostname } from "node:os";
import { join, sep } from "node:path";
import type { KyoJson, ServedProject } from "./kyoserve";
import { processNameOf } from "./procfacts";
import { KYOSEI_DIR } from "./store";

/** The file the session hooks write, beside `kyosei.db`. `kyosei.config.SESSIONS_NAME`. */
export const SESSIONS_NAME = "sessions.json";

/** The document shape this build reads. `kyosei.cmd_sessions.SESSIONS_SCHEMA`. */
export const SESSIONS_SCHEMA = 1;

/**
 * The page that lists the account's own Remote Control sessions.
 *
 * The last step of the resolution, and NOT a consolation prize: on a machine where
 * Claude Code runs somewhere else -- a dedicated dev box, the project remote-mounted,
 * no IDE on the local machine -- this is what the feature IS. It lists the account's
 * own recents, which covers most of what KYO-170 asked for.
 */
export const CLAUDE_INDEX_URL = "https://claude.ai/code";

export function sessionsPathFor(storeRoot: string): string {
  return join(storeRoot, KYOSEI_DIR, SESSIONS_NAME);
}

/** One entry, as much of it as this build uses. */
export interface SessionEntry {
  sessionId: string;
  state: string;
  lastSeen: string;
  cwd: string | null;
  url: string | null;
  /**
   * The process the session runs as, as its hook found it (KYO-390): what tells a
   * session that is running from one killed with no hook to say so. Null from a
   * `kyo` that predates it, or whose walk could not tell.
   */
  pid: number | null;
  /** That process's name, checked beside the pid so a reused pid does not pass. */
  processName: string | null;
  /** The machine the hook ran on: a pid means nothing anywhere else. */
  host: string | null;
}

// ------------------------------------------------------------ named outcomes
//
// Six, and each is an answer a reader can act on. `no-store` and `not-written` in
// particular are different facts with the same consequence: one is "this app has no
// project open", the other is "this project has never had a session hook run in
// it", and a reader debugging a pane that will not deep-link needs to know which.

/** A running session in this project's own directory -- resolution step 1. */
export const RESOLVED_THIS_PROJECT = "this-project-session";
/** A running session the file names, not matched to this project's directory. */
export const RESOLVED_RUNNING = "running-session";
/** No running session has a page; the most recent one that ended does. */
export const RESOLVED_ENDED = "ended-session";
/** No store root to look in: nothing is open, or `kyo` could not be asked. */
export const RESOLVED_NO_STORE = "no-store";
/** The store has no `sessions.json`: no session hook has ever run here. */
export const RESOLVED_NOT_WRITTEN = "not-written";
/** There is a file and this build cannot use it. Never folded into the above. */
export const RESOLVED_UNUSABLE = "file-unusable";
/** A readable file that names no session with a Remote Control page. */
export const RESOLVED_NO_LINK = "no-linked-session";

export const RESOLUTIONS = [
  RESOLVED_THIS_PROJECT,
  RESOLVED_RUNNING,
  RESOLVED_ENDED,
  RESOLVED_NO_STORE,
  RESOLVED_NOT_WRITTEN,
  RESOLVED_UNUSABLE,
  RESOLVED_NO_LINK,
] as const;

export type Resolution = (typeof RESOLUTIONS)[number];

/** Where the pane should be, and how that was decided. */
export interface PaneTarget {
  url: string;
  outcome: Resolution;
  /** Rendered on every resolution, deep-linked or not (law 6). */
  why: string;
  /** The store this was resolved against, so a reader can tell which project. */
  storeRoot: string | null;
  /** True only when the URL is one session's page rather than the index. */
  deepLinked: boolean;
}

/** What one read of the file established. Never throws; see `sessionsFrom`. */
export interface SessionsRead {
  entries: SessionEntry[];
  /** Null when the read was clean, INCLUDING when the file is simply not there. */
  problem: string | null;
  present: boolean;
  /** The file, as the CLI named it (on the server's machine); null when it named none. */
  path: string | null;
}

function asEntry(raw: unknown): SessionEntry | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const data = raw as Record<string, unknown>;
  const sessionId = data.session_id;
  if (typeof sessionId !== "string" || sessionId === "") return null;
  return {
    sessionId,
    state: typeof data.state === "string" ? data.state : "",
    lastSeen: typeof data.last_seen === "string" ? data.last_seen : "",
    cwd: typeof data.cwd === "string" ? data.cwd : null,
    // A URL is only believed if it is one this build can recognise. A file that has
    // been hand-edited, or written by a newer tool, must not be able to point a
    // logged-in view at an arbitrary origin -- see `isClaudeUrl`.
    url: typeof data.url === "string" && isClaudeUrl(data.url) ? data.url : null,
    pid:
      typeof data.pid === "number" && Number.isInteger(data.pid) && data.pid > 1 ? data.pid : null,
    processName: typeof data.process === "string" && data.process !== "" ? data.process : null,
    host: typeof data.host === "string" && data.host !== "" ? data.host : null,
  };
}

/**
 * Does this entry belong to the project at `root`? Its directory is the root or
 * inside it (KYO-390).
 *
 * Not `cwd === root`: the hook records the directory of the event that fired it, so
 * a session that has run one command in a subdirectory reads as some other
 * project's from then on (this project's own controller was once recorded in
 * `apps/desktop/src`). A hook cannot fire from outside every project -- `kyo`
 * finds no store there and records nothing -- so the entries a store holds were
 * all written from inside one of its roots.
 */
export function isInProject(cwd: string | null, root: string): boolean {
  if (cwd === null) return false;
  const trimmed = root.endsWith(sep) && root.length > 1 ? root.slice(0, -1) : root;
  return cwd === trimmed || cwd.startsWith(trimmed + sep);
}

/** What asking after a recorded session's process established. */
export type Liveness = "alive" | "gone" | "unknown";

/**
 * Is the process behind this entry still the session's? Asked of the system, on the
 * machine that runs this, and only answered for an entry recorded on this machine.
 *
 * "unknown", never "gone", for an entry with no process or one from another host:
 * the claim a caller makes from "gone" is that nothing runs, and that is not known.
 */
export function sessionLiveness(entry: SessionEntry, thisHost: string = hostname()): Liveness {
  if (entry.pid === null || entry.processName === null || entry.host !== thisHost) {
    return "unknown";
  }
  try {
    process.kill(entry.pid, 0);
  } catch (error) {
    // EPERM: there is a process with that pid, owned by someone else -- not ours,
    // but also not proof that ours is gone; the name below decides.
    if ((error as NodeJS.ErrnoException).code !== "EPERM") return "gone";
  }
  return processNameOf(entry.pid) === entry.processName ? "alive" : "gone";
}

/**
 * Is this a claude.ai page this pane may navigate to?
 *
 * Checked on the way OUT of the file rather than trusted, and this is a real
 * boundary rather than belt-and-braces: the view that renders it carries a
 * persistent logged-in session, and the file it comes from is written by a hook
 * reading an undocumented format on possibly another machine. A URL that is not
 * https on claude.ai is dropped and the resolution falls through, which costs the
 * deep link and nothing else.
 */
export function isClaudeUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "claude.ai" || url.hostname === "www.claude.ai")
    );
  } catch {
    return false;
  }
}

/**
 * What `kyo sessions --json` said, treating every failure as "nothing recorded, and
 * here is why".
 *
 * Total by design, on `readProjectState`'s argument in `facts.ts`: a corrupt or
 * half-written file must cost the pane its deep link, never the app. What it must
 * never do is invent an entry.
 */
export function sessionsFrom(result: KyoJson): SessionsRead {
  const data = result.data;
  const path = typeof data?.path === "string" && data.path !== "" ? data.path : null;
  if (data === null || result.code !== 0 || data.ok === false) {
    const said =
      typeof data?.error === "string" && data.error !== ""
        ? data.error
        : result.stderr.trim() || `\`kyo sessions --json\` exited ${result.code}`;
    return { entries: [], problem: said, present: false, path };
  }
  const problem = typeof data.problem === "string" && data.problem !== "" ? data.problem : null;
  const entries = (Array.isArray(data.sessions) ? data.sessions : [])
    .map(asEntry)
    .filter((entry): entry is SessionEntry => entry !== null);
  return { entries, problem, present: data.present === true, path };
}

/** The project's sessions, asked of its kyo serve; a server that cannot answer is a problem. */
export async function readSessionsOf(project: ServedProject): Promise<SessionsRead> {
  try {
    return sessionsFrom(await project.json(["sessions"]));
  } catch (error) {
    return { entries: [], problem: (error as Error).message, present: false, path: null };
  }
}

function newest(entries: SessionEntry[]): SessionEntry {
  // Sorted here rather than trusted from the file: the writer writes it newest
  // first, and a hand-edited or foreign-ordered file must not make this reader and
  // the CLI's own `kyo sessions` disagree about which session is the current one.
  return entries.reduce((best, entry) => (entry.lastSeen > best.lastSeen ? entry : best));
}

/**
 * Which claude.ai page this project's pane should open on.
 *
 * KYO-186's resolution order, each step degrading to the next rather than failing:
 *
 *   1. the Remote Control session of the controller running in THIS WINDOW'S
 *      TERMINAL PANE -- resolved as the running session whose recorded `cwd` is
 *      the directory that pane is open in, which is the project root `main.ts`
 *      starts every shell in. That is the closest thing to the literal question
 *      that is knowable: KYO-186 established that nothing holds a transcript open
 *      and a running `claude` does not name its session on its command line, so
 *      the app cannot ask its own pty which session is in it. The session says so
 *      itself instead, in the file, by writing the directory it is running in.
 *   2. failing that, the most recently active session the file names for this
 *      project at all -- which covers Claude Code running on another machine
 *      against the same store, where there is no local pane to match.
 *   3. failing that, `https://claude.ai/code`.
 *
 * Pure, and it must stay pure: `main.ts` calls it on every project change, and c9
 * -- the pane following a switch without a restart -- is exactly the property that
 * a cached answer would quietly kill while every other check still passed.
 */
export function resolvePaneTarget(
  storeRoot: string | null,
  projectRoot: string | null,
  read: SessionsRead | null,
): PaneTarget {
  const index = (outcome: Resolution, why: string): PaneTarget => ({
    url: CLAUDE_INDEX_URL,
    outcome,
    why,
    storeRoot,
    deepLinked: false,
  });
  if (storeRoot === null || read === null) {
    return index(
      RESOLVED_NO_STORE,
      "no Kyosei store is open, so there is no session file to read — showing the " +
        "account's own session list",
    );
  }
  if (read.problem !== null) return index(RESOLVED_UNUSABLE, read.problem);
  if (!read.present) {
    return index(
      RESOLVED_NOT_WRITTEN,
      `no ${SESSIONS_NAME} in ${storeRoot} yet — no Claude Code session has run in ` +
        "this project since the session hooks were installed, so there is nothing to " +
        "deep-link to. `kyo install-hooks` wires them",
    );
  }
  const linked = read.entries.filter((entry) => entry.url !== null);
  const running = linked.filter((entry) => entry.state === "running");
  const here =
    projectRoot === null ? [] : running.filter((entry) => isInProject(entry.cwd, projectRoot));
  if (here.length > 0) {
    const best = newest(here);
    return {
      url: best.url as string,
      outcome: RESOLVED_THIS_PROJECT,
      why: `the Claude Code session running in ${projectRoot} (${best.sessionId}, last seen ${best.lastSeen})`,
      storeRoot,
      deepLinked: true,
    };
  }
  if (running.length > 0) {
    const best = newest(running);
    return {
      url: best.url as string,
      outcome: RESOLVED_RUNNING,
      why:
        `the most recently active running session this store names (${best.sessionId}, ` +
        `last seen ${best.lastSeen}${best.cwd === null ? "" : `, in ${best.cwd}`})`,
      storeRoot,
      deepLinked: true,
    };
  }
  if (linked.length > 0) {
    const best = newest(linked);
    return {
      url: best.url as string,
      outcome: RESOLVED_ENDED,
      why: `no session is running here; the most recent one that ended (${best.sessionId}, last seen ${best.lastSeen})`,
      storeRoot,
      deepLinked: true,
    };
  }
  return index(
    RESOLVED_NO_LINK,
    `${read.path ?? sessionsPathFor(storeRoot)} names ${read.entries.length} session(s), none of ` +
      "them on Remote Control — showing the account's own session list",
  );
}

/**
 * What the file says about the session recorded in THIS directory, newest first, or
 * null when it names none (KYO-383).
 *
 * A record, and only a record: the controller launcher reports it beside what tmux
 * says is alive, never instead of it. The file says `running` for a session whose
 * process died without its `SessionEnd` hook firing -- a crash, a closed terminal, a
 * machine that slept through it -- so "is a controller running here" is a liveness
 * question `controller.ts` asks tmux, and this is what the file CLAIMED, carried so
 * the tab can say when the two disagree.
 */
export function sessionRecordedAmong(
  entries: readonly SessionEntry[],
  projectRoot: string,
): SessionEntry | null {
  const here = entries.filter((entry) => isInProject(entry.cwd, projectRoot));
  return here.length === 0 ? null : newest(here);
}

/**
 * The newest session in this project whose process is alive on this machine, or
 * null (KYO-390). What the controller launcher asks before it says nothing runs, and
 * before it starts anything: a session started by hand, in a terminal or the app's
 * own pane, is this project's controller as much as one the launcher started.
 */
export function liveSessionAmong(
  entries: readonly SessionEntry[],
  projectRoot: string,
  thisHost: string = hostname(),
): SessionEntry | null {
  const alive = entries.filter(
    (entry) =>
      entry.state === "running" &&
      isInProject(entry.cwd, projectRoot) &&
      sessionLiveness(entry, thisHost) === "alive",
  );
  return alive.length === 0 ? null : newest(alive);
}
