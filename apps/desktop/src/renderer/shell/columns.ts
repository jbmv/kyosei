// Columns of tabs: the whole of the v3 workspace (KYO-227, KYO-232.c2, KYO-246).
//
// One or two columns fill the window beside the sidebar. One of them is ACTIVE --
// its tab strip is highlighted -- and a click on a list row opens there. A row dragged
// onto a column opens there instead; a tab dragged between columns moves; a tab
// dragged OUT of the window goes to whichever window is under the pointer, or to a
// new one (KYO-246); a right-click offers what a click cannot express; a split
// control toggles the second column; each strip has a "+" that opens the home page.
//
// TWO COLUMNS, NOT THREE. KYO-227 said "one to three"; in use, two proved enough, with
// the split control either on or off. A third column on a laptop screen was three
// strips too narrow to read, and
// what a reader who wants more actually wants is a second WINDOW -- which a tab can
// now be dragged into.
//
// Two decisions in here are worth stating because the obvious alternative is worse.
//
// A TAB'S BODY ELEMENT IS CREATED ONCE AND MOVED, NEVER REBUILT. Re-rendering a tab
// on every layout change would throw away whatever it holds that the shell does not
// know about -- an xterm.js screen with scrollback in it, a scroll position, a
// half-typed search. So `render` is called once per instance, into an element this
// module then re-parents. `kind.ts` still permits a second `render`, because a tab
// moved into a WINDOW of its own is a new instance in a new document, and that is a
// different thing from a move between columns.
//
// DRAGGING IS POINTER EVENTS, NOT HTML5 DRAG AND DROP. The native mechanism goes
// through the OS drag manager, which means the app cannot draw its own drop feedback
// reliably and a check driving it is driving AppKit rather than this app. Pointer
// events are ordinary input: the ghost, the drop targets and the insert position are
// all this module's, and a check moves the mouse. Chromium keeps delivering them to
// this document while the button is held, even once the pointer has left the window
// -- which is what lets a drop outside be seen at all.

import type {
  OpenColumn,
  TabDragEndOutcome,
  TabDragPoint,
  TabDragStartOutcome,
} from "../../shared/protocol";
import { el } from "../dom";
import { icon } from "../icons";
import type { TabContext, TabInstance } from "../tabs/kind";
import { tabKind } from "../tabs/registry";
import { renderHome } from "./home";

/** The most columns the workspace will hold (KYO-246: two, toggled). */
export const MAX_COLUMNS = 2;

/** How far the pointer must travel before a press becomes a drag. */
const DRAG_THRESHOLD_PX = 4;

/** How far above its strip, or beside the columns, a dragged tab goes before it tears off. */
const TEAR_OFF_PX = 12;

/**
 * How many entries a tab's history keeps alive (KYO-247). Each kept entry is a
 * rendered body, hidden -- a record page, a doc -- so back is instant and keeps its
 * scroll position; past this many the oldest is let go.
 */
const HISTORY_MAX = 12;

export interface TabRef {
  kind: string;
  arg: string;
}

/** Where an open request lands: the shell's words, plus an explicit column index. */
export type OpenWhere = OpenColumn | { column: number };

export interface OpenRequest extends TabRef {
  where?: OpenWhere;
  /** Default true. A restore opens a whole layout without stealing the focus. */
  activate?: boolean;
  /**
   * Open a SECOND tab on the same thing rather than raising the one that is open.
   *
   * What the row menu's "Open in a new tab" means, and the only thing that
   * distinguishes it from a plain click (KYO-227: "right-click on a row or a link
   * offers open-in-new-tab and open-in-other-column"). Two tabs on one record is a
   * state a reader asks for -- two places in a long record, side by side -- and the
   * two then diverge as they are driven, which is what two of anything is for.
   *
   * Ignored for a singleton kind: there is one pty and one claude view, so a second
   * tab on either would be two screens fighting over one object.
   */
  force?: boolean;
}

/** One place a tab has been (KYO-247): its kind and argument, and what was drawn. */
interface Entry {
  kind: string;
  arg: string;
  /** Null for an entry restored from a saved layout and not yet walked back to. */
  instance: TabInstance | null;
  body: HTMLElement | null;
}

/**
 * One open tab: a small browser (KYO-247). `entries` is where it has been and `at`
 * where it is; `kind`, `arg`, `instance` and `body` mirror `entries[at]` so the rest
 * of this module reads a tab as it always did.
 */
interface LiveTab {
  id: string;
  kind: string;
  arg: string;
  instance: TabInstance;
  body: HTMLElement;
  button: HTMLElement;
  entries: Entry[];
  at: number;
}

interface LiveColumn {
  tabs: LiveTab[];
  activeId: string | null;
  widthPercent: number;
  root: HTMLElement;
  strip: HTMLElement;
  /** The scrolling part of the strip: the tab buttons and nothing else. */
  tabsHost: HTMLElement;
  bodies: HTMLElement;
  /**
   * The strip's fixed controls, made once per column and kept: back and forward
   * (repainted on every selection, because they are the active tab's), the "+", and
   * the split toggle.
   */
  controls: { back: HTMLElement; forward: HTMLElement; plus: HTMLElement; split: HTMLElement };
  /** The tab whose button was last scrolled into view, so a repaint does not re-scroll. */
  scrolledTo: string | null;
}

export interface ColumnsDeps {
  /** Build the context a tab kind is opened with; `id` is the tab's own, for `closeSelf`. */
  context(ref: TabRef, id: string): TabContext;
  /** The layout changed in a way worth remembering (KYO-232.c3). */
  onLayoutChanged(): void;
  /**
   * The reader asked for THIS tab to become a window of its own.
   *
   * `tabId` and not only `ref`, because a record can be open in both columns and the
   * two are indistinguishable by reference: the shell closes the tab that left by id,
   * so the copy the reader did not point at stays open (KYO-319).
   */
  onPopOut(ref: TabRef, tabId: string): void;
  /**
   * A tab left its strip (KYO-246): the main process opens a window around it under
   * the pointer -- or, for a window's only tab, moves the window itself -- and then
   * follows every `onDragMove` until `onDragEnd`.
   */
  onTearOff(
    ref: TabRef,
    point: TabDragPoint,
    wholeWindow: boolean,
    grab: { x: number; y: number },
  ): Promise<TabDragStartOutcome>;
  onDragMove(point: TabDragPoint): void;
  onDragEnd(point: TabDragPoint): Promise<TabDragEndOutcome>;
}

export interface TabState extends TabRef {
  /** Every entry in the tab's history, oldest first; `kind`/`arg` are `history[at]`. */
  history?: TabRef[];
  at?: number;
}

/** How a close came about, where it changes what the column does afterwards. */
export interface CloseOptions {
  /** The tab was handed to another window; this is a departure, not a tidy-up. */
  gaveAway?: boolean;
}

/** One open tab as the sidebar sees it (KYO-278). */
export interface OpenRef {
  kind: string;
  arg: string;
  /** The active tab of the active column: where a plain click would land. */
  active: boolean;
}

export interface ColumnsSnapshot {
  columns: { widthPercent: number; tabs: TabState[]; activeTab: number }[];
  activeColumn: number;
}

export interface Columns {
  root: HTMLElement;
  open(request: OpenRequest): void;
  /**
   * Every tab open in this window, with whether it is the active column's active tab
   * (KYO-278) -- what the sidebar marks its rows against. Read, never derived: the
   * sidebar does not keep its own idea of what is open.
   */
  openRefs(): OpenRef[];
  /**
   * Close EVERY tab on this reference, wherever it is -- both copies when the same
   * record is open in two columns (KYO-319).
   *
   * This is the meaning a project switch wants and the only caller that wants it: what
   * makes a `record` or `compose` tab unfit to outlive the switch is its CONTENT, which
   * every copy of it shares. Anything acting on the tab a reader pointed at wants
   * `closeById` instead -- the two used to be one function, which took whichever copy
   * came first and so closed the wrong one (KYO-319).
   */
  closeEveryMatching(ref: TabRef): void;
  /**
   * Close ONE tab -- the one whose id this is, and no other tab that happens to show
   * the same thing. `gaveAway` says the tab went to another window, so an emptied
   * column gives its space up (KYO-249) rather than staying as a close leaves it
   * (KYO-277).
   */
  closeById(id: string, options?: CloseOptions): void;
  /** Close the active column's active tab -- Cmd+W (KYO-246). */
  closeActive(): void;
  /** Open the home page as a new tab in the active column -- Cmd+T (KYO-246). */
  newTab(): void;
  /** Add the second column, or fold it back into the first (KYO-246). */
  toggleSplit(): void;
  /** Select the next (+1) or previous (-1) tab in the active column. */
  cycle(delta: number): void;
  /**
   * Show something else in an open tab, keeping where it was in the tab's history
   * (KYO-247). False when that tab cannot navigate -- it is gone, or it or the target
   * is a singleton kind -- in which case the caller opens a tab instead.
   */
  navigate(tabId: string, ref: TabRef): boolean;
  /** Walk the active column's active tab back (-1) or forward (+1). */
  go(delta: number): void;
  /**
   * What a single click on a list row does (KYO-247): raise the tab already showing
   * it if there is one, else show it in the active column's active tab, keeping that
   * tab's history, else open a tab for it.
   */
  show(ref: TabRef): void;
  /**
   * Take a tab handed over by another window (KYO-246), into the column under the
   * screen point when one is given, else the active column.
   */
  receive(ref: TabRef, at: TabDragPoint | null): void;
  /** A window being dragged is over this strip at that point, or has left (null). */
  hover(at: TabDragPoint | null): void;
  /** The window this window's drag tore off can be seen now: drop the ghost (KYO-260). */
  tornShown(): void;
  /** Is a tab with this identity open anywhere in this window? */
  has(ref: TabRef): boolean;
  /** A tab's title changed: redraw the strips. What `TabContext.setTitle` reaches. */
  retitle(): void;
  snapshot(): ColumnsSnapshot;
  restore(snapshot: ColumnsSnapshot): void;
  /**
   * Start dragging something that is not a tab -- a sidebar row -- onto a column, or
   * onto something of the caller's own (`elsewhere`: a folder in the sidebar,
   * KYO-250), which is asked first on release and can claim the drop.
   */
  dragRow(
    event: PointerEvent,
    ref: TabRef,
    label: string,
    elsewhere?: { hover(x: number, y: number): void; drop(x: number, y: number): boolean },
  ): void;
  dispose(): void;
}

let tabSeq = 0;

/** The next open tab's id. Unique within this renderer; never persisted. */
function nextTabId(): string {
  tabSeq += 1;
  return `tab-${tabSeq}`;
}

export function buildColumns(deps: ColumnsDeps): Columns {
  const root = el("div", { class: "columns", data: { region: "columns" } });
  const columns: LiveColumn[] = [];
  let active = 0;

  // ------------------------------------------------------------------ the model

  function makeColumn(widthPercent: number): LiveColumn {
    const tabsHost = el("div", { class: "strip-tabs", data: { region: "strip-tabs" } });
    // The strip is the window's title bar (KYO-253): a drag region, with its tabs
    // and buttons opted out by the stylesheet.
    const strip = el("div", { class: "strip", data: { region: "tab-strip", dragRegion: "" } }, [
      tabsHost,
    ]);
    const bodies = el("div", { class: "bodies" });
    const nav = (label: string, delta: number, glyph: "back" | "forward"): HTMLElement => {
      const button = el("button", {
        class: "strip-action strip-nav",
        data: { action: glyph, region: `nav-${glyph}` },
        tip: label,
      });
      button.append(icon(glyph, { size: 14 }));
      button.addEventListener("click", () => {
        active = columns.indexOf(column);
        go(delta);
      });
      return button;
    };
    const plus = el("button", {
      class: "strip-action",
      data: { action: "new-tab", region: "new-tab" },
      tip: "New tab (⌘T)",
    });
    plus.append(icon("plus", { size: 14 }));
    plus.addEventListener("click", () => {
      active = columns.indexOf(column);
      newTab();
    });
    const split = el("button", {
      class: "strip-action",
      data: { action: "split", region: "split", splitOn: "false" },
    });
    split.addEventListener("click", () => toggleSplit());
    const column: LiveColumn = {
      tabs: [],
      activeId: null,
      widthPercent,
      root: el("div", { class: "column", data: { region: "column" } }, [strip, bodies]),
      strip,
      tabsHost,
      bodies,
      controls: {
        back: nav("Back (⌘[)", -1, "back"),
        forward: nav("Forward (⌘])", 1, "forward"),
        plus,
        split,
      },
      scrolledTo: null,
    };
    strip.prepend(column.controls.back, column.controls.forward);
    strip.append(plus, el("span", { class: "strip-gap" }), split);
    column.root.addEventListener("pointerdown", () => {
      // Clicking anywhere in a column makes it the one a list row opens into. The
      // whole column and not only its strip, because "the column I am reading in" is
      // what the active column means to a reader (KYO-227).
      //
      // `paintState` and NOT `render`, and this is the one line in this file that was
      // measured rather than reasoned about. A full render replaces the strip's
      // buttons, and a button removed between a press and its release never receives
      // the `click` -- Chromium dispatches it on the nearest surviving ancestor
      // instead. So pressing the split control of a column that was not active did
      // nothing at all: the press made it active, the render replaced the button, and
      // the click went to the column. Selection is a change of ATTRIBUTES; only a
      // change of structure needs a render.
      const index = columns.indexOf(column);
      if (index !== -1 && index !== active) {
        active = index;
        paintState();
        deps.onLayoutChanged();
      }
    });
    // A mouse wheel over the strip scrolls it sideways: a strip is one row, so the
    // vertical wheel a reader has is the only wheel there is.
    tabsHost.addEventListener(
      "wheel",
      (event) => {
        if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
        tabsHost.scrollLeft += event.deltaY;
        event.preventDefault();
      },
      { passive: false },
    );
    return column;
  }

  function normaliseWidths(): void {
    const total = columns.reduce((sum, column) => sum + column.widthPercent, 0);
    if (total <= 0) {
      for (const column of columns) column.widthPercent = 100 / columns.length;
      return;
    }
    for (const column of columns) column.widthPercent = (column.widthPercent / total) * 100;
  }

  function ensureColumn(): LiveColumn {
    if (columns.length === 0) columns.push(makeColumn(100));
    const found = columns[Math.min(active, columns.length - 1)];
    // `noUncheckedIndexedAccess` is on, and the line above cannot produce undefined --
    // but proving that to the compiler with a non-null assertion is banned by the
    // lint, so the impossible branch says so instead of being asserted away.
    if (found === undefined) throw new Error("columns: the active column vanished");
    return found;
  }

  function addColumn(): LiveColumn {
    if (columns.length >= MAX_COLUMNS) return ensureColumn();
    const column = makeColumn(100 / (columns.length + 1));
    columns.push(column);
    normaliseWidths();
    active = columns.length - 1;
    render();
    deps.onLayoutChanged();
    return column;
  }

  /**
   * Fold the second column into the first (KYO-246: the split is on or off).
   *
   * Its tabs are appended, not dropped: a reader turning the split off is asking
   * for one column, not for less work. The first column's active tab stays active.
   */
  function removeSplit(): void {
    const [first, second] = columns;
    if (first === undefined || second === undefined) return;
    for (const tab of second.tabs) first.tabs.push(tab);
    if (first.activeId === null) first.activeId = second.activeId;
    second.tabs = [];
    columns.splice(1, columns.length - 1);
    second.root.remove();
    first.widthPercent = 100;
    active = 0;
    render();
    deps.onLayoutChanged();
  }

  function toggleSplit(): void {
    if (columns.length >= 2) removeSplit();
    else addColumn();
  }

  /** The column an open request means. */
  function columnFor(where: OpenWhere | undefined): LiveColumn {
    if (typeof where === "object") {
      while (columns.length <= where.column && columns.length < MAX_COLUMNS) {
        columns.push(makeColumn(100 / (columns.length + 1)));
      }
      normaliseWidths();
      return columns[Math.min(where.column, columns.length - 1)] ?? ensureColumn();
    }
    if (where === "new") {
      return columns.length < MAX_COLUMNS ? addColumn() : otherColumn();
    }
    if (where === "other") return otherColumn();
    // Named columns (KYO-282): the right one comes into being if there is only one.
    if (where === "left") return columnFor({ column: 0 });
    if (where === "right") return columnFor({ column: 1 });
    return ensureColumn();
  }

  /**
   * "The other column": the next one round, adding a second if there is only one.
   *
   * A link that says `?column=other` is asking for "beside what I am reading"
   * (KYO-230's `OPEN_COLUMNS`), and in a one-column window the honest answer is to
   * make the beside exist rather than to open on top of what the reader is looking at.
   */
  function otherColumn(): LiveColumn {
    if (columns.length < 2) return addColumn();
    return columns[(active + 1) % columns.length] ?? ensureColumn();
  }

  function findTab(ref: TabRef): { column: LiveColumn; tab: LiveTab } | null {
    for (const column of columns) {
      for (const tab of column.tabs) {
        if (tab.kind === ref.kind && tab.arg === ref.arg) return { column, tab };
      }
    }
    return null;
  }

  function tabById(id: string): { column: LiveColumn; tab: LiveTab } | null {
    for (const column of columns) {
      for (const tab of column.tabs) if (tab.id === id) return { column, tab };
    }
    return null;
  }

  function activeTab(): { column: LiveColumn; tab: LiveTab } | null {
    const column = columns[active];
    if (column === undefined || column.activeId === null) return null;
    const tab = column.tabs.find((candidate) => candidate.id === column.activeId);
    return tab === undefined ? null : { column, tab };
  }

  // ------------------------------------------------------------------ opening

  function open(request: OpenRequest): void {
    const kind = tabKind(request.kind);
    if (kind === undefined) {
      // Law 6: a tab kind this build does not have is said, not silently skipped. It
      // happens for real -- a saved layout holding a kind a later build dropped.
      const column = columnFor(request.where);
      mount(column, unknownKindTab(request), request.activate !== false);
      return;
    }

    const raise = (found: { column: LiveColumn; tab: LiveTab }): void => {
      active = columns.indexOf(found.column);
      found.column.activeId = found.tab.id;
      paintState();
      deps.onLayoutChanged();
    };

    // A singleton kind is one per window WHEREVER it is, and the argument does not
    // enter into it: there is one pty and one `WebContentsView`, so a second tab on
    // either would be two screens fighting over one object.
    if (kind.singleton === true) {
      const open =
        columns
          .flatMap((column) => column.tabs.map((tab) => ({ column, tab })))
          .find((entry) => entry.tab.kind === request.kind) ?? null;
      if (open !== null) {
        raise(open);
        return;
      }
    }

    // Already open: the same tab is the same tab. WHERE that is checked depends on
    // whether the caller named a column, and the difference is the menu entry that
    // would otherwise do nothing: "open in the other column" on a record already open
    // in THIS one means put a second tab over there, not raise the one here. So a
    // request that names a column dedups against that column alone, and a request that
    // does not -- a click on a row, a deep link's `active` -- dedups against the window.
    const where = request.where;
    const named = where !== undefined && where !== "active";
    if (!named && request.force !== true) {
      const found = findTab(request);
      if (found !== null) {
        raise(found);
        return;
      }
    }

    const destination = columnFor(where);
    if (named && request.force !== true) {
      const here = destination.tabs.find(
        (tab) => tab.kind === request.kind && tab.arg === request.arg,
      );
      if (here !== undefined) {
        raise({ column: destination, tab: here });
        return;
      }
    }

    const id = nextTabId();
    const entry = makeEntry({ kind: request.kind, arg: request.arg }, id);
    const tab: LiveTab = {
      id,
      kind: entry.kind,
      arg: entry.arg,
      instance: entry.instance,
      body: entry.body,
      button: el("button"),
      entries: [entry],
      at: 0,
    };
    mount(destination, tab, request.activate !== false);
  }

  /** Build and draw one history entry for a tab. */
  function makeEntry(
    ref: TabRef,
    tabId: string,
  ): Entry & { instance: TabInstance; body: HTMLElement } {
    const kind = tabKind(ref.kind);
    const body = el("div", {
      class: "tab-body",
      data: { region: "tab-body", tabKind: ref.kind, tabArg: ref.arg },
    });
    if (kind === undefined) {
      const placeholder = unknownKindTab({ kind: ref.kind, arg: ref.arg });
      return {
        kind: ref.kind,
        arg: ref.arg,
        instance: placeholder.instance,
        body: placeholder.body,
      };
    }
    const instance = kind.open(deps.context({ kind: ref.kind, arg: ref.arg }, tabId));
    instance.render(body);
    return { kind: ref.kind, arg: ref.arg, instance, body };
  }

  /** Make `entries[at]` the tab's face, drawing it first if it never was. */
  function applyEntry(tab: LiveTab): void {
    const entry = tab.entries[tab.at];
    if (entry === undefined) return;
    if (entry.instance === null || entry.body === null) {
      const drawn = makeEntry(entry, tab.id);
      entry.instance = drawn.instance;
      entry.body = drawn.body;
    }
    tab.kind = entry.kind;
    tab.arg = entry.arg;
    tab.instance = entry.instance;
    tab.body = entry.body;
  }

  /** Let go of everything a tab ever drew. */
  function endTab(tab: LiveTab): void {
    for (const entry of tab.entries) {
      entry.instance?.close();
      entry.body?.remove();
    }
  }

  /** Let go of one entry that is no longer reachable. */
  function dropEntry(entry: Entry): void {
    entry.instance?.close();
    entry.body?.remove();
  }

  // ------------------------------------------------------------ history (KYO-247)

  function navigate(tabId: string, ref: TabRef): boolean {
    const found = tabById(tabId);
    if (found === null) return false;
    const { tab, column } = found;
    // A singleton is one object per window: a terminal cannot be walked away from and
    // back to, and nothing can become the claude view by navigation. The scratchpad
    // is protected on the same terms (KYO-281): it is never navigated away.
    if (isProtected(tab) || isProtected(ref)) {
      return false;
    }
    if (tab.kind === ref.kind && tab.arg === ref.arg) {
      column.activeId = tab.id;
      active = columns.indexOf(column);
      paintState();
      return true;
    }
    for (const gone of tab.entries.splice(tab.at + 1)) dropEntry(gone);
    tab.entries.push(makeEntry(ref, tab.id));
    while (tab.entries.length > HISTORY_MAX) {
      const oldest = tab.entries.shift();
      if (oldest !== undefined) dropEntry(oldest);
    }
    tab.at = tab.entries.length - 1;
    applyEntry(tab);
    column.activeId = tab.id;
    active = columns.indexOf(column);
    render();
    deps.onLayoutChanged();
    return true;
  }

  function canGo(tab: LiveTab, delta: number): boolean {
    const next = tab.at + delta;
    return next >= 0 && next < tab.entries.length;
  }

  function go(delta: number): void {
    const found = activeTab();
    if (found === null || !canGo(found.tab, delta)) return;
    found.tab.at += delta;
    applyEntry(found.tab);
    render();
    deps.onLayoutChanged();
  }

  // ------------------------------------------------ the open policy (KYO-281)
  //
  // Opening a record or idea shows it in the record tab already open and focuses it, or
  // makes one and focuses it, and never replaces the terminal, claude or scratchpad.
  //
  // So there are two sorts of tab. A BROWSING tab -- a record, an idea file, a view,
  // a doc, the home page -- is a small browser (KYO-247) and is what a record or idea
  // opens into. A PROTECTED tab -- the terminal, the claude session, the scratchpad --
  // is a thing in its own right, and a click on a list never navigates it away. The
  // tab a record opens into is the active one when it is browsing, else the most
  // recently used browsing tab in the window (the active column first), else a new one.

  /** A tab a record or idea must never replace. */
  function isProtected(ref: { kind: string; arg: string }): boolean {
    return (
      tabKind(ref.kind)?.singleton === true || (ref.kind === "compose" && ref.arg === "scratchpad")
    );
  }

  /** Tab ids, most recently shown first (KYO-281); kept by `paintState`. */
  let recent: string[] = [];

  function noteRecent(): void {
    const current = activeTab();
    if (current === null) return;
    recent = [current.tab.id, ...recent.filter((id) => id !== current.tab.id)].slice(0, 64);
  }

  /** The browsing tabs of this window, most recently used first, the active column's first. */
  function browsingTabs(): { column: LiveColumn; tab: LiveTab }[] {
    const ordered = recent
      .map((id) => tabById(id))
      .filter((found): found is { column: LiveColumn; tab: LiveTab } => found !== null)
      .filter((found) => !isProtected(found.tab));
    const activeColumn = columns[active];
    return [
      ...ordered.filter((found) => found.column === activeColumn),
      ...ordered.filter((found) => found.column !== activeColumn),
    ];
  }

  function show(ref: TabRef): void {
    const already = findTab(ref);
    if (already !== null) {
      open(ref);
      return;
    }
    if (isProtected(ref)) {
      open(ref);
      return;
    }
    const shown = activeTab();
    if (shown !== null && !isProtected(shown.tab) && navigate(shown.tab.id, ref)) return;
    // The active tab is protected (or cannot navigate): the browsing tab takes it, and
    // is focused -- in another column if that is where it lives.
    for (const found of browsingTabs()) {
      if (navigate(found.tab.id, ref)) return;
    }
    open(ref);
  }

  /** A tab standing in for a kind this build does not have. */
  function unknownKindTab(request: OpenRequest): LiveTab {
    const body = el("div", {
      class: "tab-body",
      data: { region: "tab-body", tabKind: request.kind, tabArg: request.arg, tabUnknown: "true" },
    });
    body.append(
      el("div", { class: "empty" }, [
        el("p", { text: `This build has no tab kind called “${request.kind}”.` }),
        el("p", {
          class: "muted",
          text:
            "A saved layout named it, or a link asked for it. Tab kinds are the " +
            "directories under src/renderer/tabs/; see src/renderer/tabs/kind.ts.",
        }),
      ]),
    );
    const instance = { title: () => request.kind, render: () => undefined, close: () => undefined };
    return {
      id: nextTabId(),
      kind: request.kind,
      arg: request.arg,
      instance,
      body,
      button: el("button"),
      entries: [{ kind: request.kind, arg: request.arg, instance, body }],
      at: 0,
    };
  }

  function mount(column: LiveColumn, tab: LiveTab, activate: boolean): void {
    column.tabs.push(tab);
    if (activate || column.activeId === null) column.activeId = tab.id;
    if (activate) active = columns.indexOf(column);
    render();
    deps.onLayoutChanged();
  }

  function newTab(): void {
    open({ kind: "home", arg: "", force: true });
  }

  // ------------------------------------------------------------------ closing

  function closeTab(tab: LiveTab, column: LiveColumn, options: CloseOptions = {}): void {
    // KYO-277: a column whose last tab is CLOSED stays, as the home page. Until then it
    // folded itself away, which read as the split closing under the reader. The split
    // is a state the reader sets; only the square unsets it.
    // A close that is really a hand-off to another window (`popOut`) says so and gives
    // the space up, as a move does.
    detach(tab, column, { keepEmpty: options.gaveAway !== true });
    endTab(tab);
    render();
    deps.onLayoutChanged();
  }

  /**
   * Take a tab out of its column without ending it -- a move, or a pop-out.
   *
   * A move or a pop-out that empties a column still gives the space up (KYO-249: the
   * tab went to another window, and an empty copy of its column here would be the
   * thing the reader just dragged away from). Only a close keeps the column
   * (`keepEmpty`, KYO-277) -- the reader was tidying tabs, not changing the layout.
   */
  function detach(tab: LiveTab, column: LiveColumn, options: { keepEmpty?: boolean } = {}): void {
    const index = column.tabs.indexOf(tab);
    if (index !== -1) column.tabs.splice(index, 1);
    if (column.activeId === tab.id) {
      const next = column.tabs[Math.min(index, column.tabs.length - 1)];
      column.activeId = next === undefined ? null : next.id;
    }
    if (column.tabs.length === 0 && columns.length > 1 && options.keepEmpty !== true) {
      const at = columns.indexOf(column);
      columns.splice(at, 1);
      column.root.remove();
      normaliseWidths();
      if (active >= columns.length) active = columns.length - 1;
    }
  }

  /**
   * Close every copy, not the first one (KYO-319).
   *
   * `findTab` answers the FIRST match, so this re-asks until there is none rather than
   * walking a list it is mutating. The guard is the tab count at entry: each turn
   * closes one tab, so the loop cannot outlast it, and a `findTab` that somehow kept
   * answering a tab `closeTab` did not remove would stop rather than hang the renderer.
   */
  function closeEveryMatching(ref: TabRef): void {
    let guard = columns.reduce((total, column) => total + column.tabs.length, 0);
    for (; guard > 0; guard -= 1) {
      const found = findTab(ref);
      if (found === null) return;
      closeTab(found.tab, found.column);
    }
  }

  function closeById(id: string, options: CloseOptions = {}): void {
    const found = tabById(id);
    if (found === null) return;
    closeTab(found.tab, found.column, options);
  }

  function closeActive(): void {
    const found = activeTab();
    if (found === null) return;
    closeTab(found.tab, found.column);
  }

  function closeOthers(keep: LiveTab, column: LiveColumn): void {
    for (const tab of [...column.tabs]) {
      if (tab === keep) continue;
      detach(tab, column);
      endTab(tab);
    }
    column.activeId = keep.id;
    render();
    deps.onLayoutChanged();
  }

  function closeToTheRight(from: LiveTab, column: LiveColumn): void {
    const at = column.tabs.indexOf(from);
    for (const tab of column.tabs.slice(at + 1)) {
      detach(tab, column);
      endTab(tab);
    }
    if (!column.tabs.some((tab) => tab.id === column.activeId)) column.activeId = from.id;
    render();
    deps.onLayoutChanged();
  }

  function cycle(delta: number): void {
    const column = columns[active];
    if (column === undefined || column.tabs.length < 2) return;
    const at = column.tabs.findIndex((tab) => tab.id === column.activeId);
    const next = column.tabs[(at + delta + column.tabs.length) % column.tabs.length];
    if (next === undefined) return;
    column.activeId = next.id;
    paintState();
    deps.onLayoutChanged();
  }

  // ------------------------------------------------------------------ rendering

  /**
   * One tab's strip button, made once and updated in place by `render`.
   *
   * Its listeners look the tab's column UP when they fire; they do not close over the
   * column it was made in (KYO-288, KYO-256). A button is made once and a TAB MOVES --
   * `moveTab` carries it to the other column, `removeSplit` folds the second column's
   * tabs into the first, and `detach` can delete the column it came from outright. A
   * captured column therefore goes stale by ordinary use, and everything the button
   * does is addressed to a column: `closeTab` splices the tab out of `column.tabs`
   * (a no-op on the wrong one, so the X did nothing and the tab stayed in the strip
   * with its instance already ended), selection writes `column.activeId` (so the
   * strip never highlighted the tab that was clicked), and `columns.indexOf` of a
   * deleted column is -1 (so `active` pointed at no column at all). Collapsing the
   * split appeared to "fix" it only because it moved the tab back to the column its
   * button still had.
   */
  function makeTabButton(tab: LiveTab): HTMLElement {
    const button = el("button", {
      class: "tab",
      data: { region: "tab", tabId: tab.id, tabKind: tab.kind, tabArg: tab.arg, selected: "false" },
    });
    button.append(el("span", { class: "tab-title", text: tab.instance.title() }));
    const close = el("span", {
      class: "tab-close",
      data: { action: "close-tab" },
      tip: "Close this tab",
    });
    close.append(icon("close", { size: 12 }));
    button.append(close);
    button.addEventListener("pointerdown", (event) => {
      const found = tabById(tab.id);
      if (found === null) return;
      onTabPointerDown(event, tab, found.column);
    });
    button.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      const found = tabById(tab.id);
      if (found === null) return;
      openTabMenu(event, tab, found.column);
    });
    return button;
  }

  function render(): void {
    if (columns.length === 0) columns.push(makeColumn(100));
    if (active >= columns.length) active = columns.length - 1;
    if (active < 0) active = 0;

    for (const [index, column] of columns.entries()) {
      column.root.style.flexBasis = `${column.widthPercent}%`;
      column.root.dataset.active = String(index === active);
      column.root.dataset.columnIndex = String(index);
      column.strip.dataset.active = String(index === active);

      // The strip's buttons are made once per tab and UPDATED, never rebuilt: a
      // rebuild on every title change emptied the scroller, reset its scroll, and
      // re-filled it, which showed as the whole bar shrinking and growing
      // on every click. Only a change in the SET or ORDER of tabs touches the DOM.
      const wanted: HTMLElement[] = [];
      for (const tab of column.tabs) {
        if (tab.button.dataset.region !== "tab") tab.button = makeTabButton(tab);
        tab.button.dataset.tabKind = tab.kind;
        tab.button.dataset.tabArg = tab.arg;
        tab.button.dataset.tip = tab.arg === "" ? tab.kind : `${tab.kind} · ${tab.arg}`;
        const title = tab.button.querySelector(".tab-title");
        const text = tab.instance.title();
        if (title !== null && title.textContent !== text) title.textContent = text;
        wanted.push(tab.button);
      }
      const now = [...column.tabsHost.children];
      const same = now.length === wanted.length && wanted.every((node, i) => now[i] === node);
      if (!same) {
        const scrollLeft = column.tabsHost.scrollLeft;
        column.tabsHost.replaceChildren(...wanted);
        column.tabsHost.scrollLeft = scrollLeft;
      }

      const splitOn = columns.length >= 2;
      const split = column.controls.split;
      if (split.dataset.splitOn !== String(splitOn) || split.childElementCount === 0) {
        split.dataset.splitOn = String(splitOn);
        split.dataset.tip = splitOn ? "Single column (⌘\\)" : "Split into two columns (⌘\\)";
        split.replaceChildren(icon(splitOn ? "unsplit" : "split", { size: 14 }));
      }

      // Bodies: the active one shown, the rest kept and hidden. Hidden rather than
      // removed, so a tab the reader flicks away from and back to is the same screen
      // with the same scroll position -- and so an xterm.js in it keeps its pty view.
      // Every history entry a tab still holds is kept the same way (KYO-247).
      for (const tab of column.tabs) {
        for (const entry of tab.entries) {
          if (entry.body !== null && entry.body.parentElement !== column.bodies) {
            column.bodies.append(entry.body);
          }
        }
      }
      if (column.tabs.length === 0) {
        // An empty column IS the home page (KYO-246): the things a reader would
        // press "+" to be offered are already there, with nothing to press first.
        column.bodies.replaceChildren(
          el("div", { class: "tab-body empty-column", data: { region: "empty-column" } }, [
            renderHome({
              open: (ref, event) => {
                const columnIndex = columns.indexOf(column);
                open({
                  ...ref,
                  where: { column: columnIndex },
                  force: event.metaKey || event.ctrlKey,
                });
              },
            }),
          ]),
        );
      } else {
        for (const child of [...column.bodies.children]) {
          const kept = column.tabs.some((tab) => tab.entries.some((entry) => entry.body === child));
          if (!kept) child.remove();
        }
      }
    }
    // The columns, with a grip between each pair. Written as a whole rather than
    // appended to, so a column removed from the model leaves the DOM here and not at
    // every call site that could remove one.
    const children: HTMLElement[] = [];
    for (const [index, column] of columns.entries()) {
      if (index > 0) children.push(gripFor(index));
      children.push(column.root);
    }
    // Written only when the ORDER actually changed. `replaceChildren` re-parents every
    // node it is given, which detaches each of them for an instant -- and a render runs
    // on every selection, so an unconditional call would put the whole workspace through
    // that several times a second. Measured as a check reading a tab's box at exactly
    // the wrong moment and being told the tab is not visible.
    const now = [...root.children];
    const unchanged = now.length === children.length && children.every((n, i) => now[i] === n);
    if (!unchanged) root.replaceChildren(...children);
    paintState();
  }

  /**
   * The divider between two columns, dragged to give one of them more room.
   *
   * Widths are part of what a layout remembers (KYO-227: "the set of columns, their
   * widths, their tabs and the sidebar state"), and a width nobody can change is a
   * field with no consumer. Only the PAIR either side of the grip moves, so dragging
   * one divider never reshuffles a column the reader was not touching.
   */
  /** The grips, by the index of the column on their right, made once and reused. */
  const grips = new Map<number, HTMLElement>();

  function gripFor(rightIndex: number): HTMLElement {
    const made = grips.get(rightIndex) ?? grip(rightIndex);
    grips.set(rightIndex, made);
    return made;
  }

  function grip(rightIndex: number): HTMLElement {
    return el("div", {
      class: "col-grip",
      data: { region: "column-grip", gripFor: String(rightIndex) },
      on: {
        pointerdown: (event: PointerEvent) => {
          event.preventDefault();
          event.stopPropagation();
          const left = columns[rightIndex - 1];
          const right = columns[rightIndex];
          if (left === undefined || right === undefined) return;
          const startX = event.clientX;
          const total = root.getBoundingClientRect().width;
          const leftAt = left.widthPercent;
          const rightAt = right.widthPercent;
          const move = (moved: PointerEvent): void => {
            if (total <= 0) return;
            const delta = ((moved.clientX - startX) / total) * 100;
            // A column is never dragged below a tenth of the workspace: a strip with
            // no room for a tab is a column the reader cannot get back to.
            const room = Math.max(-leftAt + 10, Math.min(rightAt - 10, delta));
            left.widthPercent = leftAt + room;
            right.widthPercent = rightAt - room;
            left.root.style.flexBasis = `${left.widthPercent}%`;
            right.root.style.flexBasis = `${right.widthPercent}%`;
          };
          const up = (): void => {
            window.removeEventListener("pointermove", move);
            deps.onLayoutChanged();
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up, { once: true });
        },
      },
    });
  }

  /**
   * Which column is active and which tab is showing, as ATTRIBUTES.
   *
   * Split out of `render` because selection changes far more often than structure
   * does, and because a render mid-press loses the click (see the column's own
   * `pointerdown` listener). Nothing here adds, removes or re-parents a node.
   */
  function paintState(): void {
    for (const [index, column] of columns.entries()) {
      column.root.dataset.active = String(index === active);
      column.root.dataset.columnIndex = String(index);
      column.strip.dataset.active = String(index === active);
      // Back and forward are the ACTIVE tab's, so they follow the selection here and
      // not only a rebuild of the strip.
      const shown = column.tabs.find((tab) => tab.id === column.activeId);
      column.controls.back.toggleAttribute("disabled", shown === undefined || !canGo(shown, -1));
      column.controls.forward.toggleAttribute("disabled", shown === undefined || !canGo(shown, 1));
      for (const tab of column.tabs) {
        const selected = tab.id === column.activeId;
        tab.button.dataset.selected = String(selected);
        for (const entry of tab.entries) {
          if (entry.body !== null) entry.body.hidden = !(selected && entry.body === tab.body);
        }
      }
      // A strip wider than its column scrolls (KYO-246), and a tab the reader just
      // SELECTED must be the one in view -- once, on the selection, not on every
      // repaint, so a title arriving later does not nudge the strip again.
      if (shown !== undefined && shown.id !== column.scrolledTo && shown.button.isConnected) {
        column.scrolledTo = shown.id;
        shown.button.scrollIntoView({ inline: "nearest", block: "nearest" });
      }
    }
    root.dataset.columnCount = String(columns.length);
    root.dataset.activeColumn = String(active);
    noteRecent();
  }

  // ------------------------------------------------------------------ dragging

  interface Drag {
    ghost: HTMLElement;
    drop(columnIndex: number, insertAt: number): void;
    started: boolean;
    from: { x: number; y: number };
    /** The tab being dragged, when it is a tab and not a sidebar row. */
    tab?: LiveTab;
    /** The tab has left its strip and a window is following the pointer (KYO-246). */
    torn: boolean;
    /** A drop target of the caller's own, asked before the columns are (KYO-250). */
    elsewhere?: { hover(x: number, y: number): void; drop(x: number, y: number): boolean };
    /** A tear-off is being asked for; moves wait until the answer. */
    tearing: boolean;
  }

  let drag: Drag | null = null;

  function beginDrag(event: PointerEvent, label: string, drop: Drag["drop"], tab?: LiveTab): void {
    const ghost = el("div", { class: "ghost", text: label, data: { region: "drag-ghost" } });
    drag = {
      ghost,
      drop,
      started: false,
      from: { x: event.clientX, y: event.clientY },
      torn: false,
      tearing: false,
    };
    if (tab !== undefined) drag.tab = tab;
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp, { once: true });
  }

  /**
   * Has a dragged tab been pulled OFF the bar -- above or below every strip, sideways
   * out of the columns, or out of the window -- so that it tears off (KYO-246)?
   *
   * Every direction since KYO-259. Dragging DOWN into a column's body used to move a
   * tab to that column; now a drag down tears off too, and only a strip docks. So a
   * strip is the only place a tab lands in this window, and
   * a tab moves between columns by crossing to the other strip, or -- once torn off --
   * by being dropped on it, which the main process already docks.
   */
  function pulledOffTheBar(event: PointerEvent): boolean {
    if (isOutsideWindow(event)) return true;
    let top = Number.POSITIVE_INFINITY;
    let bottom = Number.NEGATIVE_INFINITY;
    for (const column of columns) {
      const strip = column.strip.getBoundingClientRect();
      top = Math.min(top, strip.top);
      bottom = Math.max(bottom, strip.bottom);
    }
    if (event.clientY < top - TEAR_OFF_PX || event.clientY > bottom + TEAR_OFF_PX) return true;
    // Sideways out of the columns -- over the sidebar or the rail -- is off the bar
    // too: nothing there takes a tab, and a drag that way is read as a tear-off.
    const workspace = root.getBoundingClientRect();
    return event.clientX < workspace.left - TEAR_OFF_PX || event.clientX >= workspace.right;
  }

  let moveFrame: number | null = null;

  function onPointerMove(event: PointerEvent): void {
    if (drag === null) return;
    const current = drag;
    const far =
      Math.abs(event.clientX - current.from.x) > DRAG_THRESHOLD_PX ||
      Math.abs(event.clientY - current.from.y) > DRAG_THRESHOLD_PX;
    if (!current.started && !far) return;
    if (!current.started) {
      current.started = true;
      document.body.append(current.ghost);
      document.body.dataset.dragging = "true";
    }
    if (current.torn) {
      // The ghost, while it is still up, keeps following: it stays until the new window
      // can be seen (`tornShown`), KYO-260.
      if (current.ghost.isConnected) {
        current.ghost.style.left = `${event.clientX + 8}px`;
        current.ghost.style.top = `${event.clientY + 8}px`;
      }
      // One move per frame: the window follows the pointer, and a message per
      // pointer event would be several per frame for nothing.
      if (moveFrame !== null) return;
      moveFrame = window.requestAnimationFrame(() => {
        moveFrame = null;
        deps.onDragMove({ screenX: event.screenX, screenY: event.screenY });
      });
      return;
    }
    if (current.tearing) {
      // Still being answered, and creating the window IS the wait (KYO-260: about half a
      // second). The ghost stays under the pointer meanwhile, so the drag does
      // not look stalled; where the reader lets go is judged by the main process from
      // the release point itself, so nothing here needs to be remembered.
      current.ghost.style.left = `${event.clientX + 8}px`;
      current.ghost.style.top = `${event.clientY + 8}px`;
      return;
    }
    const tab = current.tab;
    if (tab !== undefined && pulledOffTheBar(event)) {
      tearOff(current, tab, event);
      return;
    }
    current.ghost.style.left = `${event.clientX + 8}px`;
    current.ghost.style.top = `${event.clientY + 8}px`;
    current.elsewhere?.hover(event.clientX, event.clientY);
    const target = dropTargetAt(event.clientX, event.clientY, tab !== undefined);
    for (const [index, column] of columns.entries()) {
      column.root.dataset.dropTarget = String(target !== null && target.columnIndex === index);
    }
  }

  /**
   * The tab leaves the strip (KYO-246): a window opens around it under the pointer and
   * this one lets go of its copy -- unless it was this window's only tab, in which
   * case the window itself is what follows the pointer and nothing is opened.
   */
  function tearOff(current: Drag, tab: LiveTab, event: PointerEvent): void {
    current.tearing = true;
    for (const column of columns) delete column.root.dataset.dropTarget;
    const total = columns.reduce((sum, column) => sum + column.tabs.length, 0);
    const wholeWindow = total === 1;
    // Where on the tab the reader took hold, so the new window opens with the tab
    // under the pointer at that same spot rather than at its corner.
    const rect = tab.button.getBoundingClientRect();
    const grab = {
      x: Math.max(0, Math.min(rect.width, current.from.x - rect.left)),
      y: Math.max(0, Math.min(rect.height, current.from.y - rect.top)),
    };
    void deps
      .onTearOff(
        { kind: tab.kind, arg: tab.arg },
        { screenX: event.screenX, screenY: event.screenY },
        wholeWindow,
        grab,
      )
      .then((outcome) => {
        if (drag !== current) return;
        current.tearing = false;
        if (!outcome.started) return;
        // The window exists and follows the pointer from here. The ghost stays until it
        // can be SEEN -- `tornShown`, on the main process's word -- because the window
        // needs another 150-190ms to get there and a gap was the pause (KYO-260).
        current.torn = true;
        if (!wholeWindow) closeById(tab.id);
      });
  }

  /** Has the pointer left this window's viewport? */
  function isOutsideWindow(event: PointerEvent): boolean {
    return (
      event.clientX < 0 ||
      event.clientY < 0 ||
      event.clientX >= window.innerWidth ||
      event.clientY >= window.innerHeight
    );
  }

  function onPointerUp(event: PointerEvent): void {
    window.removeEventListener("pointermove", onPointerMove);
    if (moveFrame !== null) {
      window.cancelAnimationFrame(moveFrame);
      moveFrame = null;
    }
    const current = drag;
    drag = null;
    if (current === null) return;
    current.ghost.remove();
    delete document.body.dataset.dragging;
    for (const column of columns) delete column.root.dataset.dropTarget;
    if (!current.started) return;
    if (current.torn || current.tearing) {
      // Released while a window follows the pointer: the main process attaches it to
      // the strip under the pointer, or leaves it where it is. If the tear-off was
      // still being answered, the end lands after it in the same channel's order.
      void deps
        .onDragEnd({ screenX: event.screenX, screenY: event.screenY })
        .catch(() => undefined);
      return;
    }
    if (isOutsideWindow(event)) return;
    if (current.elsewhere?.drop(event.clientX, event.clientY) === true) return;
    const target = dropTargetAt(event.clientX, event.clientY, current.tab !== undefined);
    if (target === null) return;
    current.drop(target.columnIndex, target.insertAt);
  }

  /**
   * Which column is under the pointer, and where in its strip a tab would land.
   *
   * `stripOnly` for a dragged TAB (KYO-259): only a strip takes one. A sidebar row still
   * opens in whichever column it is dropped on, body or strip.
   */
  function dropTargetAt(
    x: number,
    y: number,
    stripOnly = false,
  ): { columnIndex: number; insertAt: number } | null {
    const under = document.elementFromPoint(x, y);
    if (!(under instanceof Element)) return null;
    const columnEl = under.closest<HTMLElement>('[data-region="column"]');
    if (columnEl === null) return null;
    const columnIndex = columns.findIndex((column) => column.root === columnEl);
    if (columnIndex === -1) return null;
    const column = columns[columnIndex];
    if (column === undefined) return null;
    // Over the strip: the insert position is the button the pointer is left of.
    // Over the body: the end of the strip, which is where a reader dropping into the
    // middle of a column means.
    const overStrip = under.closest('[data-region="tab-strip"]') !== null;
    if (!overStrip) return stripOnly ? null : { columnIndex, insertAt: column.tabs.length };
    let insertAt = column.tabs.length;
    for (const [index, tab] of column.tabs.entries()) {
      const rect = tab.button.getBoundingClientRect();
      if (x < rect.left + rect.width / 2) {
        insertAt = index;
        break;
      }
    }
    return { columnIndex, insertAt };
  }

  /** Move a live tab to a column at a position; the same act for drag and menu. */
  function moveTab(tab: LiveTab, columnIndex: number, insertAt: number): void {
    const destination = columns[columnIndex];
    if (destination === undefined) return;
    const from = tabById(tab.id);
    if (from === null) return;
    const sameColumn = from.column === destination;
    const currentIndex = from.column.tabs.indexOf(tab);
    detach(tab, from.column);
    // Removing from the same column shifts everything after it left by one.
    const at = sameColumn && currentIndex < insertAt ? insertAt - 1 : insertAt;
    destination.tabs.splice(Math.max(0, Math.min(at, destination.tabs.length)), 0, tab);
    destination.activeId = tab.id;
    active = columns.indexOf(destination);
    render();
    deps.onLayoutChanged();
  }

  function onTabPointerDown(event: PointerEvent, tab: LiveTab, column: LiveColumn): void {
    if (event.button !== 0) return;
    const target = event.target;
    if (target instanceof Element && target.closest('[data-action="close-tab"]') !== null) {
      event.preventDefault();
      closeTab(tab, column);
      return;
    }
    // Selection happens on press, not on drop: a reader who starts dragging a tab is
    // looking at it by the time it lands. `paintState`, not `render`, for the reason
    // the column's own listener above states -- a rebuilt button never gets its click.
    column.activeId = tab.id;
    active = columns.indexOf(column);
    paintState();
    deps.onLayoutChanged();
    beginDrag(
      event,
      tab.instance.title(),
      (columnIndex, insertAt) => moveTab(tab, columnIndex, insertAt),
      tab,
    );
  }

  function dragRow(
    event: PointerEvent,
    ref: TabRef,
    label: string,
    elsewhere?: { hover(x: number, y: number): void; drop(x: number, y: number): boolean },
  ): void {
    beginDrag(event, label, (columnIndex) => {
      open({ ...ref, where: { column: columnIndex } });
    });
    if (drag !== null && elsewhere !== undefined) drag.elsewhere = elsewhere;
  }

  /**
   * Where a window dragged over this one would land: the column under the pointer's
   * x, and the slot among its tabs. By x alone, because the main process has already
   * decided the pointer is in the strip band -- a pointer a few pixels above the strip,
   * in the title bar, still means this strip.
   */
  function slotAt(screenX: number): { columnIndex: number; insertAt: number } | null {
    const x = screenX - window.screenX;
    for (const [columnIndex, column] of columns.entries()) {
      const rect = column.root.getBoundingClientRect();
      if (x < rect.left || x >= rect.right) continue;
      let insertAt = column.tabs.length;
      for (const [index, tab] of column.tabs.entries()) {
        const box = tab.button.getBoundingClientRect();
        if (x < box.left + box.width / 2) {
          insertAt = index;
          break;
        }
      }
      return { columnIndex, insertAt };
    }
    return null;
  }

  function receive(ref: TabRef, at: TabDragPoint | null): void {
    hover(null);
    const slot = at === null ? null : slotAt(at.screenX);
    if (slot === null) {
      open({ ...ref, force: true });
      return;
    }
    open({ ...ref, where: { column: slot.columnIndex }, force: true });
    const column = columns[slot.columnIndex];
    const tab = column?.tabs.at(-1);
    if (column === undefined || tab === undefined) return;
    if (slot.insertAt < column.tabs.length - 1) moveTab(tab, slot.columnIndex, slot.insertAt);
  }

  /**
   * The torn-off window can be seen (the main process's `ready-to-show`), so the ghost
   * that bridged the gap goes. While the tear-off is still being answered as well as
   * after: whichever arrives first, the window on screen replaces the ghost (KYO-260).
   */
  function tornShown(): void {
    if (drag !== null && (drag.torn || drag.tearing)) drag.ghost.remove();
  }

  const insertMarker = el("div", { class: "strip-insert", data: { region: "strip-insert" } });

  function hover(at: TabDragPoint | null): void {
    const slot = at === null ? null : slotAt(at.screenX);
    if (slot === null) {
      insertMarker.remove();
      return;
    }
    const column = columns[slot.columnIndex];
    if (column === undefined) return;
    const stripRect = column.tabsHost.getBoundingClientRect();
    const next = column.tabs[slot.insertAt];
    const previous = column.tabs[slot.insertAt - 1];
    const x =
      next !== undefined
        ? next.button.getBoundingClientRect().left
        : previous !== undefined
          ? previous.button.getBoundingClientRect().right
          : stripRect.left;
    insertMarker.style.left = `${x - stripRect.left + column.tabsHost.scrollLeft}px`;
    if (insertMarker.parentElement !== column.tabsHost) column.tabsHost.append(insertMarker);
  }

  // ------------------------------------------------------------ the tab menu

  function openTabMenu(event: MouseEvent, tab: LiveTab, column: LiveColumn): void {
    const at = column.tabs.indexOf(tab);
    showMenu(event, [
      {
        label: columns.length < 2 ? "Move to a new column" : "Move to the other column",
        run: () => {
          const destination = otherColumn();
          if (destination === column) return;
          moveTab(tab, columns.indexOf(destination), destination.tabs.length);
        },
      },
      {
        label: "Move to a new window",
        run: () => deps.onPopOut({ kind: tab.kind, arg: tab.arg }, tab.id),
      },
      { separator: true },
      { label: "Close", tip: "⌘W closes the active tab", run: () => closeTab(tab, column) },
      {
        label: "Close others",
        disabled: column.tabs.length < 2,
        run: () => closeOthers(tab, column),
      },
      {
        label: "Close tabs to the right",
        disabled: at >= column.tabs.length - 1,
        run: () => closeToTheRight(tab, column),
      },
    ]);
  }

  render();

  return {
    root,
    open,
    closeEveryMatching,
    closeById,
    closeActive,
    newTab,
    toggleSplit,
    cycle,
    receive,
    hover,
    tornShown,
    has: (ref) => findTab(ref) !== null,
    retitle: () => render(),
    dragRow,
    navigate,
    go,
    show,
    snapshot: () => ({
      columns: columns.map((column) => ({
        widthPercent: Math.round(column.widthPercent * 100) / 100,
        tabs: column.tabs.map((tab) => ({
          kind: tab.kind,
          arg: tab.arg,
          history: tab.entries.map((entry) => ({ kind: entry.kind, arg: entry.arg })),
          at: tab.at,
        })),
        activeTab: column.tabs.findIndex((tab) => tab.id === column.activeId),
      })),
      activeColumn: active,
    }),
    openRefs() {
      const current = activeTab();
      return columns.flatMap((column) =>
        column.tabs.map((tab) => ({
          kind: tab.kind,
          arg: tab.arg,
          active: current !== null && current.tab === tab,
        })),
      );
    },
    restore(snapshot) {
      for (const column of [...columns]) {
        for (const tab of [...column.tabs]) closeTab(tab, column);
      }
      columns.splice(0, columns.length);
      root.replaceChildren();
      active = 0;
      // A layout saved by a build that allowed three columns (KYO-232) folds its
      // extra columns into the last one this build has: the tabs are kept, the
      // column is not.
      const saved = snapshot.columns.map((column, index) => ({
        ...column,
        column: Math.min(index, MAX_COLUMNS - 1),
      }));
      for (const [index, entry] of saved.entries()) {
        if (entry.column === index) columns.push(makeColumn(entry.widthPercent));
        for (const saved of entry.tabs) {
          open({
            kind: saved.kind,
            arg: saved.arg,
            where: { column: entry.column },
            activate: false,
            force: true,
          });
          // Its history comes back as places it can walk to, drawn on arrival: the
          // current entry is the one just opened, the rest are named and not built.
          const tab = columns[entry.column]?.tabs.at(-1);
          const history = saved.history ?? [];
          const at = saved.at ?? -1;
          if (tab !== undefined && history.length > 1 && at >= 0 && at < history.length) {
            const live = tab.entries[0];
            if (live !== undefined) {
              tab.entries = history.map((ref, index) =>
                index === at ? live : { kind: ref.kind, arg: ref.arg, instance: null, body: null },
              );
              tab.at = at;
            }
          }
        }
        const column = columns[entry.column];
        const chosen = column?.tabs[entry.activeTab];
        if (column !== undefined && chosen !== undefined && entry.column === index) {
          column.activeId = chosen.id;
        }
      }
      normaliseWidths();
      active = Math.max(0, Math.min(snapshot.activeColumn, columns.length - 1));
      render();
    },
    dispose() {
      for (const column of columns) {
        for (const tab of column.tabs) endTab(tab);
      }
    },
  };
}

// ------------------------------------------------------------------ the menu
//
// One menu for the whole shell, in this module because both users of it are here or
// one import away: the tab strip's right-click and the sidebar row's. It is a plain
// element rather than `Menu.popup` from the main process, because a native menu cannot
// be read or clicked by a check driving the renderer over CDP, and because these
// entries act entirely inside this document.

export type MenuItem =
  | {
      label: string;
      /** The longer form, shown on hover -- for a label too short to say the whole thing. */
      tip?: string;
      /** Shown but not clickable: an entry that would do nothing right now says so. */
      disabled?: boolean;
      separator?: false;
      run(): void;
      /**
       * A second action at the row's end (KYO-465.c3: the switcher's "remove from
       * recents"). Clicking it takes the row out and leaves the menu open, so several
       * stale entries go in one visit; the row's own action does not run.
       */
      remove?: { tip: string; run(): void };
    }
  | { separator: true };

let openMenu: HTMLElement | null = null;

export function showMenu(event: { clientX: number; clientY: number }, items: MenuItem[]): void {
  closeMenu();
  // `overlay`: the claude tab steps aside while this is open (`overlays.ts`).
  const menu = el("div", { class: "menu", data: { region: "context-menu", overlay: "menu" } });
  for (const item of items) {
    if (item.separator === true) {
      menu.append(el("div", { class: "menu-separator" }));
      continue;
    }
    const button = el("button", {
      class: "menu-item",
      text: item.label,
      tip: item.tip,
      data: { menuItem: item.label },
      on: {
        click: () => {
          closeMenu();
          item.run();
        },
      },
    });
    button.toggleAttribute("disabled", item.disabled === true);
    const remove = item.remove;
    if (remove === undefined) {
      menu.append(button);
      continue;
    }
    const row = el("div", { class: "menu-row", data: { menuRow: item.label } });
    const forget = el("button", {
      class: "menu-remove",
      text: "×",
      tip: remove.tip,
      attrs: { "aria-label": remove.tip },
      data: { action: "remove-recent" },
      on: {
        click: (event) => {
          event.stopPropagation();
          row.remove();
          remove.run();
        },
      },
    });
    row.append(button, forget);
    menu.append(row);
  }
  document.body.append(menu);
  positionMenu(menu, event.clientX, event.clientY);
  openMenu = menu;
  // Deferred by a turn: the pointer event that opened this menu is still propagating,
  // and a listener added now would otherwise close it before it was drawn. Not `once`:
  // a press inside the menu (on a disabled entry, say) is ignored, and a one-shot
  // listener spent on it left the next press outside with nothing to close the menu.
  setTimeout(() => {
    if (openMenu === menu) window.addEventListener("pointerdown", onWindowPointerDown);
  }, 0);
  // The rest of what closes a macOS menu, since a menu left open after the user has
  // moved on reads as a fault. Leaving the window with the pointer is NOT
  // one of them -- a native menu survives an overshoot -- but Escape is, and so is the
  // app losing focus, which is what clicking another app does.
  window.addEventListener("keydown", onMenuKeyDown);
  window.addEventListener("blur", closeMenu);
}

function onMenuKeyDown(event: KeyboardEvent): void {
  if (event.key !== "Escape") return;
  event.preventDefault();
  closeMenu();
}

/**
 * Place the menu at the click point, then pull it back onto the screen if it would
 * spill past the right or bottom edge.
 *
 * The rail's project switcher sits flush against the window's bottom edge, so a menu
 * anchored on that click (rather than on the button's own rect) overflows the window
 * by construction for any menu with more than a couple of rows -- the last items,
 * "Open other project…" among them, render below `innerHeight` with nothing to
 * scroll them into view. Measured after the append, not guessed: the menu has no
 * fixed size, so its real `getBoundingClientRect()` is the only honest input.
 */
function positionMenu(menu: HTMLElement, x: number, y: number): void {
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  const rect = menu.getBoundingClientRect();
  const margin = 4;
  if (rect.right > window.innerWidth) {
    menu.style.left = `${Math.max(margin, window.innerWidth - rect.width - margin)}px`;
  }
  if (rect.bottom > window.innerHeight) {
    menu.style.top = `${Math.max(margin, window.innerHeight - rect.height - margin)}px`;
  }
}

function onWindowPointerDown(event: PointerEvent): void {
  const target = event.target;
  if (target instanceof Element && target.closest('[data-region="context-menu"]') !== null) return;
  closeMenu();
}

export function closeMenu(): void {
  openMenu?.remove();
  openMenu = null;
  window.removeEventListener("pointerdown", onWindowPointerDown);
  window.removeEventListener("keydown", onMenuKeyDown);
  window.removeEventListener("blur", closeMenu);
}
