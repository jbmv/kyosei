// The tab drag's one geometric question: which shell window's TAB STRIP is under a
// screen point (KYO-246, KYO-259.c3). Electron-free, like `sessions.ts`, so every case
// can be checked without launching anything.
//
// The strip is not the window's whole top row. The row is the rail's traffic-light
// spacer, the sidebar's head and then the columns' strips (KYO-253), and a tab released
// over the sidebar head -- "IDEAS" and its collapse button -- was being received as if
// it had landed on a strip, though the sidebar head is no part of the tab bar. So a
// window's strip starts where its columns do, which the main process reads off the
// layout the renderer last saved.

import type { LayoutSnapshot } from "../shared/protocol";
import { type RememberedProject, sameProject } from "./facts";
import type { ServerAddress } from "./kyoserve";

/** The rail's width in CSS pixels: `--rail-w` in `renderer/shell.css`. */
export const RAIL_PX = 76;

/**
 * Where a shell window's columns begin, in that window's own pixels: past the rail, and
 * past the sidebar while a list is showing. The sidebar is `box-sizing: border-box`, so
 * its saved width already includes its border.
 */
export function columnsLeft(layout: LayoutSnapshot | null | undefined): number {
  const sidebar = layout?.sidebar;
  return RAIL_PX + (sidebar !== undefined && sidebar.item !== null ? sidebar.width : 0);
}

export interface StripCandidate<T> {
  value: T;
  /** The window's content bounds, in screen pixels. */
  bounds: { x: number; y: number; width: number; height: number };
  visible: boolean;
  focused: boolean;
  layout: LayoutSnapshot | null | undefined;
}

/**
 * The candidate whose strip band is under `(x, y)`, preferring a focused one where
 * windows overlap, else the last listed. `band` is how far below the content top the
 * strip reaches; a point a few pixels above it still counts, where the frame's edge is.
 */
export function stripAt<T>(
  candidates: StripCandidate<T>[],
  x: number,
  y: number,
  band: number,
): T | undefined {
  const under = candidates.filter((candidate) => {
    if (!candidate.visible) return false;
    const b = candidate.bounds;
    return (
      x >= b.x + columnsLeft(candidate.layout) &&
      x < b.x + b.width &&
      y >= b.y - 8 &&
      y < b.y + band
    );
  });
  return (under.find((candidate) => candidate.focused) ?? under.at(-1))?.value;
}

// ------------------------------------------ the project a torn-off tab opens on (KYO-469)
//
// A tab torn off, or popped out, opens a NEW shell window, and a window's spare is booted
// ahead of one. Each of them was built from the source window's root alone, so a tab
// taken out of a window on a dev box's project opened on THIS machine's project at the
// same path -- the class KYO-463 fixed for links. A project is (server, root), the
// identity `RememberedProject` names (`facts.ts`, KYO-465); these carry both halves.

/** The two fields of a shell window that name its project, as `main.ts` keeps them. */
export interface WindowProject {
  projectRoot: string;
  /** The kyo serve it is read through; null for this machine's own. */
  remote: ServerAddress | null;
}

/** A window's project as the one identity type: its root and its server. */
export function projectOfWindow(window: WindowProject): RememberedProject {
  return {
    root: window.projectRoot,
    connection:
      window.remote === null ? null : { host: window.remote.host, port: window.remote.port },
  };
}

/**
 * What a window opened out of `from` -- a tear-off, a pop-out, its spare -- opens on:
 * the same root through the same server. Spread into `createShellWindow`'s options, so
 * neither half can be passed without the other.
 */
export function openedFrom(from: WindowProject): WindowProject {
  return { projectRoot: from.projectRoot, remote: from.remote };
}

/**
 * A spare may become a window of `owner`'s only while it is on owner's project -- the
 * same root on the same server. A spare booted on the same path through another server
 * is a window on another project and is replaced, never used.
 */
export function spareFits(spare: WindowProject, owner: WindowProject): boolean {
  return sameProject(projectOfWindow(spare), projectOfWindow(owner));
}

// ------------------------------------------- the strip a dragged tab may land on (KYO-470)
//
// A tab dropped onto ANOTHER window's strip was handed to that window whatever project it
// was on, so a tab torn out of a window on machine A's project and let go over a window
// on machine B's project at the same path opened against machine B's. A torn-off tab is
// already in a window on its own project (`openedFrom`, KYO-469), so a strip on any other
// project is not a target: the tab stays where it is, exactly as one let go over empty
// desktop does.

/** What a strip under the pointer must say about itself to be judged as a drop target. */
export interface DropCandidate extends WindowProject {
  /** The tmux session the window's terminal is attached to; null while it has none. */
  sessionId: string | null;
}

/**
 * The window a tab of `kind`, carried by the window `dragged`, lands in when let go over
 * `over`'s strip -- or null, when it stays in the window carrying it. Not a target: a
 * window on another project (another root, or the same root through another server),
 * and, for a terminal, a window that already has one. An unknown carrier lands nowhere,
 * since its project cannot be compared.
 */
export function dropTarget<T extends DropCandidate>(
  dragged: WindowProject | undefined,
  kind: string,
  over: T | null | undefined,
): T | null {
  if (over === null || over === undefined || dragged === undefined) return null;
  if (!sameProject(projectOfWindow(dragged), projectOfWindow(over))) return null;
  if (kind === "terminal" && over.sessionId !== null) return null;
  return over;
}
