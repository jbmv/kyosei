// ============================================================================
// THE TAB INTERFACE -- read this file and you can write a tab (KYO-232.c5).
// ============================================================================
//
// Everything openable in the v3 shell is a TAB: the scratchpad, an idea, a record,
// a brief, a doc, the terminal, the claude session, the activity view. A tab kind
// is a DIRECTORY under `src/renderer/tabs/`, and that is the whole registration
// mechanism -- there is no list of kinds to add yourself to and no shared file to
// edit.
//
// ---------------------------------------------------------------- how to add one
//
//   1. Make `src/renderer/tabs/<kind>/index.ts`.
//   2. `export default` a `TabKind` whose `kind` is EXACTLY the directory name.
//   3. That is all. `node build.mjs` enumerates the directories and writes
//      `registry.generated.ts`; `registry.ts` reads it, checks each module's `kind`
//      against the directory it came from, and refuses a mismatch by name.
//
// A stage 3 record that replaces a placeholder tab therefore touches ONE directory
// and no file anybody else is in. That is the property this interface exists for,
// and `tests/tabs.spec.ts` proves it by creating a fixture directory, rebuilding,
// and asserting the kind appears with nothing else changed.
//
// ------------------------------------------------------------- the four members
//
// `open`, `title`, `render`, `close` -- the fixed interface the record names. They
// are split across two objects rather than four methods on one, because three of
// them are about ONE OPEN TAB and the first is about the KIND:
//
//   TabKind.open(context) -> TabInstance     make a tab
//   TabInstance.title()                      what its strip button says
//   TabInstance.render(mount)                paint into this element
//   TabInstance.close()                      let go of everything
//
// A `TabInstance` holds its own state in its own closure. The shell never reaches
// into it and never serialises it: what the shell persists is `{kind, arg}`, so a
// tab restored from a saved layout is a NEW instance opened with the same argument.
// Anything a tab wants to survive a restart belongs in `arg` or in the store.
//
// --------------------------------------------------------------- the rules
//
//   RENDER MAY BE CALLED MORE THAN ONCE. Moving a tab between columns, or into a
//   window of its own, re-mounts it: the shell calls `render` with the new element.
//   A tab that builds its DOM in `open` and appends it in `render` is fine; a tab
//   that assumes `render` happens once is not.
//
//   CLOSE IS THE ONLY TEARDOWN. Every subscription a tab takes out over the bridge
//   (`onActivityChanged`, `onProjectChanged`, ...) is released here. `close` is
//   called when the reader closes the tab and when the window goes away; it is NOT
//   called for a move, because a move is the same tab somewhere else.
//
//   A TAB NEVER PARSES AN ID AND NEVER FOLLOWS A URL ITSELF. `context.openTab`
//   opens another tab; `context.followLink` hands a `kyosei://` URL to the main
//   process, which owns the grammar (KYO-230). A tab that split an id on "." would
//   be the second copy of a rule that lives in `main/links.ts`.
//
//   A TAB ASKS THE BRIDGE FOR FACTS AND DERIVES NONE. `context.bridge` is the whole
//   of its capability surface; the read model (KYO-231) already returns shapes a tab
//   can paint.
//
//   A TAB CARRIES ITS OWN CSS, AND INSTALLS IT WITH `installStyles`. See below: it is
//   the fifth thing you need to write a tab, and the one the four stage 3 tabs each
//   answered differently.

import type { KyoseiBridge, OpenColumn } from "../../shared/protocol";
import type { IconName } from "../icons";

// ============================================================================
// A TAB'S OWN CSS (KYO-240.c3)
// ============================================================================
//
// THE CONVENTION: a tab directory that needs rules writes them in a `styles.ts` of its
// own and installs them with `installStyles`, once, under a name. It does NOT edit
// `shell.css`, and it does NOT set style properties on the elements it builds.
//
// Why not `shell.css`. The registry's whole promise, stated at the top of this file, is
// that a tab kind is a DIRECTORY and adding one touches no file anybody else is in. A
// stylesheet every kind has to edit takes that back: it makes one shared file the place
// four parallel worktrees collide, and `build.mjs` copies exactly two renderer assets by
// name, so a second `.css` file is an edit to a shared file too. Rules that live beside
// the markup they style move, and are deleted, with the directory.
//
// Why not element styles. Stage 3 built four tabs at once and settled this four ways:
// the record page injected a sheet, compose and activity set `style` objects on
// elements, views declared class names and styled none of them -- so `.id-link` in a
// brief rendered as a default grey button in the middle of a sentence, and the comment
// above it said "styled". An element style cannot express a hover, a focus ring, a
// media query or a child selector, it is invisible to anyone reading the sheet, and it
// costs a property write per element per render.
//
// The one exception, and it is narrow: a value COMPUTED per element, which no static
// rule can express. `compose/editor.ts` indents a wrapped list item by the width of its
// own bullet (`padding-left: ${columns}ch`), a different number on every line. That is
// a measurement, not a look, and it stays on the element.
//
// What a sheet may assume: the shell's own custom properties -- `--accent`, `--border`,
// `--panel`, `--bad`, ... -- declared on `:root` in `shell.css`. A tab styles LIKE this
// app; it does not bring a palette of its own.
//
// What is checked: `tests/shell.spec.ts` enumerates `style[data-styles]` in the live
// document and asserts each tab's sheet is installed through this helper, and that no
// other module in the renderer makes a `<style>` element of its own.

const installedSheets = new Set<string>();

/**
 * Put one named stylesheet in the document, once.
 *
 * Idempotent by name, because `open` runs per TAB and a reader with three records open
 * has opened the record kind three times -- the second call is a no-op, not a second
 * copy of the same rules. The name is written to `data-styles` so a check, and anyone
 * with the inspector open, can see which sheets a window is actually carrying.
 */
export function installStyles(name: string, css: string): void {
  if (installedSheets.has(name)) return;
  installedSheets.add(name);
  const style = document.createElement("style");
  style.setAttribute("data-styles", name);
  style.textContent = css;
  document.head.append(style);
}

/**
 * What the rail badge on a kind's icon counts.
 *
 * A closed set, because the rail is what draws it and the rail must know where the
 * number comes from: a tab kind naming a source here gets the count kept live for
 * it (KYO-232.c6) instead of each kind inventing its own poll.
 */
export const BADGE_SOURCES = ["awaiting-human", "running-dispatches"] as const;
export type BadgeSource = (typeof BADGE_SOURCES)[number];

/**
 * A kind's own entry in the left nav rail, when it has one.
 *
 * Declared BY THE KIND rather than listed by the rail, which is the same property
 * the registry has one level up: a directory that wants an icon gets one without
 * anybody editing the rail. The shell's own rail items -- the ones that select a
 * sidebar list rather than opening a tab -- are declared in `shell/rail.ts`, and the
 * two sets are merged and sorted by `order`.
 */
export interface RailEntry {
  /** Sort key across every rail item, the shell's own included. Lower is higher up. */
  order: number;
  label: string;
  icon: IconName;
  /** The argument the rail click opens this kind with. Defaults to the empty string. */
  arg?: string;
  /** A live count drawn on the icon, or absent for no badge. */
  badge?: BadgeSource;
}

/**
 * One entry on the home page (KYO-246): what a new tab, or an empty column, offers.
 *
 * Declared BY THE KIND, on the same terms as `RailEntry`, and a kind may declare
 * several -- `views` offers records, awaiting-you and the latest brief, which are
 * three doors into one kind. `shell/home.ts` merges every kind's entries and sorts
 * them by `order`.
 */
export interface HomeEntry {
  order: number;
  label: string;
  /** One line under the label saying what opens. */
  detail: string;
  icon: IconName;
  /** The argument the entry opens this kind with. Defaults to the empty string. */
  arg?: string;
}

/** What the shell hands a tab when it opens one. */
export interface TabContext {
  /**
   * The tab's subject, as a string the KIND defines: a record id, an idea path, a
   * view name. The empty string means "this kind's one thing" (the activity view,
   * the terminal). Together with `kind` it is the tab's identity for layout memory
   * and for the singleton rule, so two tabs with the same kind and arg are the same
   * tab.
   */
  arg: string;
  bridge: KyoseiBridge;
  /**
   * Open another tab -- a link inside this one. `where` defaults to the active
   * column; `force` opens a second tab on a thing already open rather than raising it.
   */
  openTab(request: { kind: string; arg: string; where?: OpenColumn; force?: boolean }): void;
  /**
   * Close this tab. What a page that has done its one job reaches for -- the home
   * tab, once the reader has chosen from it. A no-op in a window with no columns.
   */
  closeSelf(): void;
  /**
   * Show something else in THIS tab, keeping where it was in the tab's history
   * (KYO-247) -- a link followed inside a page, the home page's choice. Falls back
   * to opening a tab when this one cannot navigate (a singleton, or a pane window).
   */
  navigate(ref: { kind: string; arg: string }): void;
  /**
   * Hand a `kyosei://` URL to the main process to resolve and deliver.
   *
   * The renderer never parses it: `main/links.ts` holds the id grammar and the
   * delivery arrives back over `onOpenTarget`, which is the same path a link clicked
   * in the claude view takes (KYO-230's `LinkDelivery`).
   */
  followLink(url: string, options?: { newTab?: boolean }): void;
  /** The tab's title changed -- redraw its strip button. Safe to call at any time. */
  setTitle(title: string): void;
}

/** One open tab. */
export interface TabInstance {
  /** What the tab strip shows. Called after every `setTitle`. */
  title(): string;
  /**
   * Paint into `mount`, which is empty and owned by this tab until it is closed.
   *
   * May be called again with a different element when the tab moves between columns
   * or into a window of its own.
   */
  render(mount: HTMLElement): void;
  /** Release everything: subscriptions, observers, timers. */
  close(): void;
}

/** One tab kind: a directory under `src/renderer/tabs/`. */
export interface TabKind {
  /** EXACTLY the directory name. `registry.ts` refuses anything else. */
  kind: string;
  /** The fallback tab title, for an instance that names none. */
  label: string;
  /**
   * At most one of these open per window, whatever the argument.
   *
   * True for the terminal and the claude session, and the reason is not tidiness:
   * both are views of ONE scarce main-process object -- a pty and a
   * `WebContentsView` -- so a second tab would be a second view of the same thing
   * fighting the first over its bounds and its bytes.
   */
  singleton?: boolean;
  /** This kind's rail icon, when it has one. */
  rail?: RailEntry;
  /** This kind's entries on the home page, when it has any (KYO-246). */
  home?: HomeEntry[];
  open(context: TabContext): TabInstance;
}
