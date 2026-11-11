// The project switcher (KYO-761): what the dropdown lists, which sessions the top bar links,
// and where the composer's unsent prompts are kept for each project. It also holds what the
// composer decides when a prompt passes from one session to the next (KYO-763). It holds no
// state and touches no element, so that it can be tested without a page.

/** One project of the server's registry, as `GET /v1/projects` lists it. */
export interface ProjectRow { name: string; can_open: boolean; problem: string | null }

/** A session a process is running on, from the project's own list. */
export interface LiveSession { session_id: string; title: string | null; has_pane: boolean }

/** What the app says of the server's projects, sent again whenever any of it changes. */
export interface ProjectsFrame {
  /** The window's project, or the empty string while none is chosen. */
  project: string;
  /** The server's projects in its own order, or null before the list has been read. */
  projects: ProjectRow[] | null;
  /** Why the last read of the list failed, or null. */
  problem: string | null;
  /** Each project's live sessions; a project whose sessions could not be read is absent. */
  live: Record<string, LiveSession[]>;
}

/** One row of the dropdown, and of the list shown in place of the chat. */
export interface Choice {
  name: string;
  /** Whether it is the window's project. */
  current: boolean;
  /** Whether choosing it switches the window; a project that cannot be opened is listed
   *  with its problem and takes no press. */
  openable: boolean;
  problem: string | null;
}

/** The dropdown's rows: every project the server serves, in the server's order. */
export function choices(frame: ProjectsFrame | null): Choice[] {
  return (frame?.projects ?? []).map((row) => ({
    name: row.name,
    current: row.name === frame!.project,
    openable: row.can_open,
    problem: row.can_open
      ? null
      : row.problem || "The server gave no reason why this project cannot be opened.",
  }));
}

/**
 * The session a project's link follows: the first live one the server shows a pane for,
 * which is the session the window opens by itself when it turns to the project. The server's
 * list does not say which session is the orchestrator's, and a session with no pane is one
 * started outside the server's terminals, so it is not linked.
 */
export function orchestrator(rows: LiveSession[] | undefined): LiveSession | null {
  return rows?.find((row) => row.has_pane) ?? null;
}

/** One link of the top bar. */
export interface SessionLink {
  project: string;
  session: string;
  title: string | null;
  /** Whether the project is the window's own, which the bar marks. */
  current: boolean;
}

/** A link for each project with a live orchestrator session, in the server's order. */
export function sessionLinks(frame: ProjectsFrame | null): SessionLink[] {
  return (frame?.projects ?? []).flatMap((row) => {
    const session = row.can_open ? orchestrator(frame!.live[row.name]) : null;
    return session
      ? [{
        project: row.name, session: session.session_id, title: session.title,
        current: row.name === frame!.project,
      }]
      : [];
  });
}

/** What the dropdown and the list say above their rows, or nothing. */
export function listNote(frame: ProjectsFrame | null): string {
  if (!frame || frame.projects === null) {
    return frame?.problem
      ? `The server's projects could not be read: ${frame.problem}`
      : "Reading the server's projects.";
  }
  // The rows are the last list that was read, which a later failure does not take away.
  if (frame.problem) return `The server's projects could not be read again: ${frame.problem}`;
  if (!frame.projects.length) {
    return "The server lists no project. `kyo init` in a project's folder registers it.";
  }
  return "";
}

/**
 * The row a typed letter moves to: the next openable project after `from` whose name begins
 * with `typed`, wrapping round, or null.
 */
export function typedChoice(rows: Choice[], typed: string, from: string | null): string | null {
  const wanted = typed.toLowerCase();
  const open = rows.filter((row) => row.openable);
  const start = open.findIndex((row) => row.name === from) + 1;
  const order = [...open.slice(start), ...open.slice(0, start)];
  return order.find((row) => row.name.toLowerCase().startsWith(wanted))?.name ?? null;
}

// ----------------------------------------------------------- the composer's prompts, kept

/** The part of `localStorage` the kept prompts need. */
export interface Keep {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Where the window kept its prompts before it could show more than one project. */
export const LEGACY_KEY = "composer-current";

/**
 * The key one project's unsent prompts are kept under. `scope` names the server and the
 * project, so two servers' projects of one name do not share their prompts.
 */
export function keptKey(scope: string): string {
  return `${LEGACY_KEY}:${scope}`;
}

/** Keeps a project's unsent prompts, or forgets them when there are none. */
export function keepBoxes(keep: Keep, scope: string, boxes: unknown[]): void {
  try {
    if (boxes.length) keep.setItem(keptKey(scope), JSON.stringify(boxes));
    else keep.removeItem(keptKey(scope));
  } catch {}
}

/**
 * A project's unsent prompts as the window last kept them. The server keeps a draft for
 * each session, and one for the project while none of its sessions is live (KYO-763); its
 * copy replaces these when it arrives. These are what is shown until then, and all there is
 * for a device with no token.
 * Prompts kept before the switcher existed belong to the first project that is shown, when
 * `adoptLegacy` is set, and are moved to it.
 */
export function keptBoxes<T>(keep: Keep, scope: string, adoptLegacy: boolean): T[] {
  const read = (key: string): T[] | null => {
    try {
      const value = keep.getItem(key);
      const boxes = value === null ? null : JSON.parse(value);
      return Array.isArray(boxes) ? (boxes as T[]) : null;
    } catch {
      return null;
    }
  };
  const own = read(keptKey(scope));
  if (!adoptLegacy) return own ?? [];
  const legacy = read(LEGACY_KEY);
  try {
    keep.removeItem(LEGACY_KEY);
  } catch {}
  if (own?.length || !legacy?.length) return own ?? [];
  keepBoxes(keep, scope, legacy);
  return legacy;
}

// ------------------------------------------- a prompt carried from one session to the next

/** The key that says a project's kept prompts have met the server's draft on this device. */
export function metKey(scope: string): string {
  return `composer-met:${scope}`;
}

/**
 * The prompts to show when the server's draft arrives, and whether they differ from it and
 * so are to be saved. The server's draft replaces what the window kept. The exception is the
 * first draft a project is given on this device: prompts written before the server kept a
 * draft between sessions (KYO-763) exist only in this window's storage, so those the
 * server's draft does not hold, by their text, are kept after it that once.
 */
export function meetDraft<T extends { text: string }>(
  keep: Keep, scope: string, server: T[], local: T[],
): { boxes: T[]; merged: boolean } {
  let met = true;
  try {
    met = keep.getItem(metKey(scope)) !== null;
    keep.setItem(metKey(scope), "1");
  } catch {}
  if (met) return { boxes: server, merged: false };
  const held = new Set(server.map((box) => box.text));
  const own = local.filter((box) => !held.has(box.text));
  return { boxes: [...server, ...own], merged: own.length > 0 };
}

/**
 * Whether an anchor's place can be opened from the chat on screen: it can when the anchor
 * was made in the session that is followed. A prompt kept between sessions carries its
 * anchors into the next one, where they are still sent as their words (the server reads them
 * from the session they name) and have no place in the chat to go to. An anchor with no
 * session recorded is taken as the followed session's.
 */
export function anchorOpens(session: string, followed: string | null | undefined): boolean {
  return !session || session === followed;
}

/** What a chip says of an anchor that cannot be opened; `live` is whether its session runs. */
export function anchorGoneNote(live: boolean): string {
  return live
    ? "Made in another session. It is sent with its words, and cannot be opened from this chat."
    : "Made in a session that has ended. It is sent with its words, and can no longer be opened.";
}
