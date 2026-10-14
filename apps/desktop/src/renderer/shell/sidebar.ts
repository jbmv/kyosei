// The collapsible sidebar: the lists the rail selects among (KYO-227, KYO-232.c2;
// sorted, filtered and organised by KYO-250).
//
// "The sidebar holds lists (ideas tree, records list with search and saved bases,
// briefs list, docs list); it is never a popover." Never a popover is the load-bearing
// half: it is a region of the layout, it pushes the columns rather than floating over
// them, and the rail icon that selected it collapses it when clicked again. A reader
// dragging a row out of it is dragging out of a thing that stays put.
//
// Every row is the SAME shape -- `data-row-kind` and `data-row-arg` -- so one set of
// handlers serves five lists: a click shows in the active tab, a drag opens in the
// column it is dropped on, a right-click offers what a click cannot say.
//
// EVERY LIST HAS A SORT AND A FILTER (KYO-250): an up/down arrow and a funnel, each
// opening a box that sorts by name, created or modified, or filters by kind, scope,
// type and the like. So each list keeps
// a `ListState` -- the sort field and direction, the filter values kept, the folders
// folded shut -- applies it in the renderer over the rows it loaded, and hands it to
// the shell to remember with the layout. The ideas list is a TREE: folders that fold,
// with new folder, rename, move (by drag or by menu) and delete, every write going
// through the main process's guard.

import {
  type ActivityRow,
  type ActivityView,
  BOARD_FILTER_ID,
  BOARD_FILTER_LABEL,
  BOARD_FILTER_ORDER,
  type BoardClass,
  boardFilterLabel,
  boardFilterUnavailable,
  boardFilterValue,
  type IdeaFileEntry,
  type KyoseiBridge,
  type ListState,
  listingStatus,
  type StoreChange,
  storeReloadDue,
} from "../../shared/protocol";
import { el } from "../dom";
import { type IconName, icon } from "../icons";
import {
  elapsedFor,
  finishedWhen,
  kyoseiUrlFor,
  tokensLabel,
  tokensTip,
  whenTip,
} from "./activity-format";
import { type Columns, type MenuItem, type OpenRef, showMenu, type TabRef } from "./columns";
import type { SidebarList } from "./rail";
import { saidBy } from "./remote-dialog";

/** The narrowest and widest the sidebar may be dragged. */
const MIN_WIDTH = 180;
const MAX_WIDTH = 520;
export const DEFAULT_SIDEBAR_WIDTH = 280;

export interface Sidebar {
  root: HTMLElement;
  /** Show a list, or collapse the sidebar entirely with `null`. */
  show(list: SidebarList | null): void;
  showing(): SidebarList | null;
  width(): number;
  setWidth(width: number): void;
  /** Re-read whichever list is showing -- after a project switch, say. */
  refresh(): void;
  /**
   * Somebody else wrote the store, or `ideas/` (KYO-310): the showing list follows.
   *
   * Not `refresh()`. That rebuilds the list from its head down -- the right thing
   * when the PROJECT changed, and the wrong thing here: the reader is looking at
   * this list, and a rebuild takes away their scroll position, the search they
   * typed and the spinner-free view they were reading. This re-reads the rows
   * underneath them instead.
   */
  followStore(change: StoreChange): void;
  /** Re-mark every row against what is open now (KYO-278); called on layout changes. */
  markOpen(): void;
  /** Every list's sort, filters and folded folders, for the layout (KYO-250). */
  listStates(): Record<string, ListState>;
  setListStates(states: Record<string, ListState>): void;
}

export interface SidebarDeps {
  bridge: KyoseiBridge;
  columns: Columns;
  onLayoutChanged(): void;
  /** The row's menu asked for a window of its own. */
  onOpenInWindow(ref: TabRef): void;
  /** The head's collapse button: the same act as clicking the selected rail item. */
  onCollapse(): void;
}

/** A sort field a list offers: its label, and how to read it off a row. */
interface SortField<T> {
  id: string;
  label: string;
  key(row: T): string;
}

/** A filter a list offers: its label, and the one value a row has for it. */
interface FilterField<T> {
  id: string;
  label: string;
  value(row: T): string;
  /** A fixed order for the values, when there is one; else sorted as found. */
  order?: string[];
  /**
   * Draw every value in `order`, even one no loaded row has (KYO-290.c1).
   *
   * Off by default, and that default is right for the groups built out of whatever
   * the rows happen to hold -- a Kind chip for a kind this store never uses is noise.
   * It is wrong for a group whose values are a fixed vocabulary the reader came
   * looking for: "dispatchable" missing from the box reads as the filter being absent,
   * when what it means is that nothing is dispatchable -- which the emptied list says
   * far better than a vanished chip.
   */
  offerAllValues?: boolean;
  /** A label for a value, when the value is not its own label. */
  labelOf?(value: string): string;
  /**
   * Why this filter cannot answer right now, or null when it can (KYO-290.c2).
   *
   * A filter whose source failed does not narrow: `arrange` skips it and the box
   * prints this in place of its chips. The alternative -- drawing the chips over a
   * source that returned nothing -- turns "we could not ask" into "the answer is
   * none", which is the one thing a filter must never do silently.
   */
  unavailable?(): string | null;
}

/**
 * How long a list may sit on its spinner before the sidebar says so (KYO-306.c2).
 *
 * A load that never answers used to leave the "…" up forever: nothing on the screen
 * said which list was waiting, or on what, and a check that was waiting for a row got
 * a sixty-second silence and a DOM that looked like a list with no rows in it. This is
 * long enough that a slow answer still arrives first -- a list is filled by a `kyo`
 * subprocess, measured at one to two seconds idle -- and short enough that a load
 * which is never going to answer says so while somebody is still looking at it.
 */
const SLOW_LOAD_MS = 15_000;

/**
 * What the showing list is holding, for the reader who finds it empty (KYO-306.c2).
 *
 * `null` when there are rows. Otherwise the sentence the sidebar prints in their
 * place -- and it names WHICH emptiness this is, because "the store has no records",
 * "the search matched nothing" and "the filters matched nothing" are three different
 * facts that an empty pane states as one. Pure, and exported, so the wording is
 * checkable without a window.
 */
export function emptyRecordsLine(counts: {
  loaded: number;
  shown: number;
  query: string;
  filtering: boolean;
}): string | null {
  if (counts.shown > 0) return null;
  const query = counts.query.trim();
  // The search is answered in the MAIN process -- `readRecordsList` filters before it
  // replies -- so a search that matches nothing arrives here as `loaded: 0` and is
  // indistinguishable from a store with no records by the counts alone. The query is
  // therefore read FIRST, or every failed search would report the store as empty.
  if (query !== "" && counts.filtering) {
    return `No record matches "${query}" and the filters that are on.`;
  }
  if (query !== "") return `No record matches "${query}".`;
  if (counts.filtering) return "No record matches the filters that are on.";
  // Nothing was narrowing this, so the answer itself was empty: the state KYO-306 is
  // about, which used to be drawn as a heading reading "Records (0)" and no sentence.
  if (counts.loaded === 0) return "This store answered with no records.";
  // Not reachable from the narrowings above -- rows loaded, nothing narrowing, nothing
  // shown -- and stated rather than left blank, because an empty pane with no sentence
  // in it is the whole defect this line exists to close.
  return "The records list drew no rows, and nothing was narrowing it.";
}

function defaultState(): ListState {
  return { sort: { field: "name", descending: false }, filters: {}, collapsed: [] };
}

function asState(raw: unknown): ListState {
  const state = defaultState();
  if (raw === null || typeof raw !== "object") return state;
  const value = raw as Partial<ListState>;
  if (value.sort && typeof value.sort.field === "string") {
    state.sort = { field: value.sort.field, descending: value.sort.descending === true };
  }
  if (value.filters && typeof value.filters === "object") {
    for (const [key, kept] of Object.entries(value.filters)) {
      if (Array.isArray(kept)) state.filters[key] = kept.filter((v) => typeof v === "string");
    }
  }
  if (Array.isArray(value.collapsed)) {
    state.collapsed = value.collapsed.filter((v) => typeof v === "string");
  }
  return state;
}

/**
 * Is any of this list's filters actually narrowing it right now?
 *
 * One definition, two consumers: the dot on the filter button, which answers "is this
 * list shorter than it looks", and the sentence an emptied list prints in place of its
 * rows (KYO-306). Written once because two copies of it would eventually disagree, and
 * a list whose dot says "filtered" while its sentence says "this store answered with
 * no records" is worse than either alone. `arrange` skips a filter that cannot answer
 * (KYO-290.c2), so this has to skip it too or it would claim a narrowing that is not
 * happening.
 */
function narrowingNow<T>(state: ListState, filters: FilterField<T>[]): boolean {
  return filters.some(
    (filter) =>
      (filter.unavailable?.() ?? null) === null && (state.filters[filter.id] ?? []).length > 0,
  );
}

/** Sort and filter one list's rows by its state; the query is the list's own. */
function arrange<T>(
  rows: T[],
  state: ListState,
  sorts: SortField<T>[],
  filters: FilterField<T>[],
): T[] {
  let kept = rows;
  for (const filter of filters) {
    // A filter that cannot answer narrows nothing (KYO-290.c2). The kept values stay
    // in `state` -- the reader picked them and the choice survives a restart -- but a
    // board that could not be read must not empty the list on their behalf.
    if ((filter.unavailable?.() ?? null) !== null) continue;
    const wanted = state.filters[filter.id];
    if (wanted === undefined || wanted.length === 0) continue;
    const set = new Set(wanted);
    kept = kept.filter((row) => set.has(filter.value(row)));
  }
  const sort = sorts.find((candidate) => candidate.id === state.sort.field) ?? sorts[0];
  if (sort === undefined) return kept;
  const sorted = [...kept].sort((a, b) =>
    sort.key(a).localeCompare(sort.key(b), undefined, { numeric: true, sensitivity: "base" }),
  );
  return state.sort.descending ? sorted.reverse() : sorted;
}

export function buildSidebar(deps: SidebarDeps): Sidebar {
  const title = el("div", { class: "sidebar-title", data: { region: "sidebar-title" } });
  const body = el("div", { class: "sidebar-body", data: { region: "sidebar-body" } });
  const grip = el("div", { class: "sidebar-grip", data: { region: "sidebar-grip" } });
  // A collapse control at the head's right, for a reader who does not know that a
  // second click on the selected rail item collapses the sidebar. It is that second click.
  const collapse = el("button", {
    class: "tool-button",
    data: { region: "sidebar-collapse" },
    tip: "Hide the sidebar",
    attrs: { type: "button" },
    on: { click: () => deps.onCollapse() },
  });
  collapse.append(icon("sidebar-left", { size: 15 }));
  const root = el("aside", { class: "sidebar", data: { region: "sidebar", open: "false" } }, [
    el("div", { class: "sidebar-head", data: { dragRegion: "" } }, [
      title,
      el("span", { class: "grow" }),
      collapse,
    ]),
    body,
    grip,
  ]);
  let current: SidebarList | null = null;
  let width = DEFAULT_SIDEBAR_WIDTH;
  const states = new Map<string, ListState>();

  // ------------------------------------- following the store (KYO-310)
  //
  // The showing list's own re-read, set by whichever render function is in force and
  // cleared by `show`. It is not that list's `render()`: a render REBUILDS the list
  // (head, search box, tool boxes, rows) and a re-read replaces only the rows, which
  // is what keeps the reader's scroll, their typed search and their focus where they
  // were while a controller files records in the pane beside them. A list with no
  // such function -- one still loading, or one that has none -- simply does not
  // follow, which is the honest behaviour rather than a rebuild in disguise.
  let reloadCurrent: (() => Promise<void>) | null = null;
  /** A store-driven re-read is out; another change arriving now is dropped. */
  let reloadInFlight = false;
  /**
   * How many store-driven re-reads this sidebar has done (KYO-310.c1).
   *
   * Stamped on the sidebar's root so "a list that is not showing costs nothing" is
   * observable from outside: a check can file a record with the sidebar collapsed and
   * read a zero here, which is a claim about work NOT done and has no other evidence.
   */
  let storeReloads = 0;

  function stateOf(list: string): ListState {
    let state = states.get(list);
    if (state === undefined) {
      state = defaultState();
      states.set(list, state);
    }
    return state;
  }

  // ------------------------------------------------------------------ rows

  function row(
    ref: TabRef,
    label: string,
    detail: string,
    badge?: string,
    extraMenu: MenuItem[] = [],
  ): HTMLElement {
    const node = el("div", {
      class: "row",
      data: { region: "sidebar-row", rowKind: ref.kind, rowArg: ref.arg },
      tip: detail === "" ? label : `${label} — ${detail}`,
    });
    markRow(node, deps.columns.openRefs());
    node.append(el("span", { class: "row-label", text: label }));
    if (detail !== "") node.append(el("span", { class: "row-detail", text: detail }));
    if (badge !== undefined) node.append(el("span", { class: "row-badge", text: badge }));
    // The click model (KYO-247): a single click shows
    // the row in the tab the reader is in, the way a link does in a browser -- the
    // tab keeps its history, so back returns to what it showed. Cmd-click opens a tab
    // of its own in the same column without leaving this one; the menu offers the
    // other column and a new window.
    node.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey) {
        deps.columns.open({ ...ref, force: true, activate: false });
        return;
      }
      deps.columns.show(ref);
    });
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      deps.columns.dragRow(event, ref, label, {
        // A row dropped on one of THIS list's folders (KYO-250) moves the file there;
        // the drag is the same drag that opens a row in a column when it lands on one.
        hover: (x, y) => markFolderUnder(x, y),
        drop: (x, y) => {
          const folder = folderUnder(x, y);
          markFolderUnder(-1, -1);
          if (folder === null || !ref.arg.startsWith("idea:")) return false;
          void moveTo(ref.arg.slice("idea:".length), folder);
          return true;
        },
      });
    });
    node.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      showMenu(event, [
        { label: "Open in a new tab", run: () => deps.columns.open({ ...ref, force: true }) },
        // Named columns, not "the other" one (KYO-282): with the active tab weakly
        // marked, "the other column" did not say where a tab would land. The right
        // column comes into being if there is only one.
        {
          label: "Open in the left column",
          run: () => deps.columns.open({ ...ref, where: "left" }),
        },
        {
          label: "Open in the right column",
          run: () => deps.columns.open({ ...ref, where: "right" }),
        },
        { label: "Open in a new window", run: () => deps.onOpenInWindow(ref) },
        ...extraMenu,
      ]);
    });
    return node;
  }

  function group(heading: string, rows: HTMLElement[]): HTMLElement {
    return el("div", { class: "group", data: { group: heading } }, [
      el("div", { class: "group-head", text: `${heading} (${rows.length})` }),
      ...rows,
    ]);
  }

  function problem(text: string): HTMLElement {
    return el("div", { class: "sidebar-problem", data: { region: "sidebar-problem" }, text });
  }

  function loading(): HTMLElement {
    return el("div", { class: "sidebar-loading", data: { region: "sidebar-loading" }, text: "…" });
  }

  // ------------------------------------------------- what the list is holding (KYO-306)
  //
  // A sidebar list that draws no rows was, until this, indistinguishable in the DOM
  // from one that had not been asked yet -- which is exactly the state three checks
  // once died in, each waiting thirty seconds for a row and each leaving a
  // page snapshot that said nothing about the list. So the sidebar stamps what its
  // body actually holds, and a kept snapshot carries it.
  //
  // Written from ONE observer rather than at each paint site on purpose: a list added
  // tomorrow gets the stamp without its author having to know this exists, and a paint
  // that forgot to call a helper is the failure mode this is here to prevent. `root`
  // is not inside `body`, so writing the attribute cannot re-trigger the observer.
  function stampLoad(): void {
    if (current === null) {
      delete root.dataset.load;
      return;
    }
    const rows = body.querySelectorAll('[data-region="sidebar-row"]').length;
    if (body.querySelector('[data-region="sidebar-loading"]') !== null) {
      root.dataset.load = "loading";
      return;
    }
    if (rows > 0) {
      root.dataset.load = `rows:${rows}`;
      return;
    }
    root.dataset.load =
      body.querySelector('[data-region="sidebar-problem"]') !== null ? "stated" : "silent-empty";
  }
  new MutationObserver(stampLoad).observe(body, { childList: true, subtree: true });

  /**
   * The search control a list puts at its top, made HERE so two lists cannot drift.
   *
   * "The same control on the same terms" (KYO-240.c4) is a claim about one widget in
   * two places, not about two widgets that look alike: same class, same icon, same
   * `type="search"` (so the reader gets the platform's own clear button), same
   * live-on-input behaviour. What differs between the lists is only the placeholder,
   * because "id or title" and "name or folder" are what each one actually matches --
   * a search box that names the wrong field is worse than one that names none.
   */
  function searchBox(region: string, placeholder: string): HTMLInputElement {
    return el("input", {
      class: "search-input",
      data: { region },
      attrs: { type: "search", placeholder },
    });
  }

  // ------------------------------------------------- the sort and filter boxes

  /**
   * The tools row and the box under it (KYO-250): the search, then a sort button
   * and a filter button, each of which opens its box below the row. The filter
   * button carries a dot while any filter is on, because a list quietly showing
   * half its rows is the failure the dot exists to prevent.
   *
   * `repaint` is for the caller whose rows arrive after the row is built: the dot and
   * the box are drawn from the rows and from what each filter can answer, and both are
   * unknown until the load returns. Without it a list restored with a filter already
   * set painted the dot from the state alone and then never looked again -- which is
   * exactly the lie the dot exists to prevent, the moment one of its filters turns out
   * to be blind (KYO-290.c2).
   */
  function tools<T>(
    list: string,
    search: HTMLElement,
    sorts: SortField<T>[],
    filters: FilterField<T>[],
    rowsNow: () => T[],
    redraw: () => void,
    extra: HTMLElement[] = [],
  ): { head: HTMLElement; box: HTMLElement; repaint: () => void } {
    const state = stateOf(list);
    const box = el("div", { class: "sidebar-box", data: { region: "sidebar-box" } });
    box.hidden = true;
    let open: "sort" | "filter" | null = null;

    const sortButton = el("button", {
      class: "tool-button",
      data: { region: "sort-button", active: "false" },
      tip: "Sort",
      attrs: { type: "button" },
    });
    sortButton.append(icon("sort", { size: 14 }));
    const filterButton = el("button", {
      class: "tool-button",
      data: { region: "filter-button", active: "false", filtering: "false" },
      tip: "Filter",
      attrs: { type: "button" },
    });
    filterButton.append(icon("funnel", { size: 14 }));

    // The dot answers "is this list shorter than it looks", so it counts only filters
    // that are actually narrowing: a filter this list does not declare never reached
    // `arrange`, and one that cannot answer is skipped there too (KYO-290.c2).
    const filtering = (): boolean => narrowingNow(state, filters);

    const paintButtons = (): void => {
      sortButton.dataset.active = String(open === "sort");
      filterButton.dataset.active = String(open === "filter");
      filterButton.dataset.filtering = String(filtering());
      const field = sorts.find((s) => s.id === state.sort.field)?.label ?? "name";
      sortButton.dataset.tip = `Sort: ${field}, ${state.sort.descending ? "descending" : "ascending"}`;
    };

    const changed = (): void => {
      paintButtons();
      redraw();
      deps.onLayoutChanged();
    };

    const choice = (
      text: string,
      data: Record<string, string>,
      on: boolean,
      run: () => void,
    ): HTMLElement =>
      el("button", {
        class: "box-choice",
        text,
        data: { ...data, on: String(on) },
        attrs: { type: "button" },
        on: { click: run },
      });

    const drawBox = (): void => {
      box.hidden = open === null;
      box.dataset.showing = open ?? "";
      box.replaceChildren();
      if (open === "sort") {
        // One dropdown and an up/down direction toggle beside it, rather than a row of
        // pills.
        const field = el("select", {
          class: "base-picker sort-field",
          data: { region: "sort-field" },
          on: {
            change: () => {
              state.sort.field = field.value;
              changed();
            },
          },
        });
        for (const sort of sorts) {
          const option = el("option", { text: sort.label, attrs: { value: sort.id } });
          option.selected = state.sort.field === sort.id;
          field.append(option);
        }
        const descending = state.sort.descending;
        const direction = el("button", {
          class: "sort-direction",
          text: descending ? "↓" : "↑",
          tip: descending ? "Descending (click for ascending)" : "Ascending (click for descending)",
          data: { region: "sort-direction", direction: descending ? "descending" : "ascending" },
          attrs: { type: "button" },
          on: {
            click: () => {
              state.sort.descending = !state.sort.descending;
              drawBox();
              changed();
            },
          },
        });
        box.append(
          el("div", { class: "box-title", text: "Sort by" }),
          el("div", { class: "box-row sort-row" }, [
            el("span", { class: "sort-field-wrap" }, [field]),
            direction,
          ]),
        );
        return;
      }
      if (open === "filter") {
        // Clear filters first, above the groups: under the last group it read as one more
        // chip of that group.
        const clear = choice("Clear filters", { region: "filter-clear" }, false, () => {
          state.filters = {};
          drawBox();
          changed();
        });
        clear.classList.add("box-clear");
        box.append(el("div", { class: "box-row" }, [clear]));
        const rows = rowsNow();
        for (const filter of filters) {
          // The group whose source failed says so where its chips would be, and offers
          // nothing to pick: an empty list under a chip the reader pressed would read
          // as an answer (KYO-290.c2).
          const unavailable = filter.unavailable?.() ?? null;
          if (unavailable !== null) {
            box.append(
              el("div", { class: "box-title", text: filter.label }),
              el("div", {
                class: "box-note",
                data: { region: "filter-unavailable", filter: filter.id },
                text: unavailable,
              }),
            );
            continue;
          }
          const values = new Set(rows.map((r) => filter.value(r)));
          const fixed = filter.order ?? [];
          const ordered = [
            ...fixed.filter((v) => filter.offerAllValues === true || values.has(v)),
            ...[...values].filter((v) => !fixed.includes(v)).sort(),
          ];
          const kept = new Set(state.filters[filter.id] ?? []);
          const line = el("div", {
            class: "box-row",
            data: { region: "filter-group", filter: filter.id },
          });
          for (const value of ordered) {
            const label = filter.labelOf ? filter.labelOf(value) : value === "" ? "(none)" : value;
            line.append(
              choice(label, { filterValue: value }, kept.has(value), () => {
                if (kept.has(value)) kept.delete(value);
                else kept.add(value);
                state.filters[filter.id] = [...kept];
                drawBox();
                changed();
              }),
            );
          }
          box.append(el("div", { class: "box-title", text: filter.label }), line);
        }
      }
    };

    sortButton.addEventListener("click", () => {
      open = open === "sort" ? null : "sort";
      paintButtons();
      drawBox();
    });
    filterButton.addEventListener("click", () => {
      open = open === "filter" ? null : "filter";
      paintButtons();
      drawBox();
    });
    paintButtons();

    const head = el("div", { class: "sidebar-tools" }, [
      el("span", { class: "search-icon" }, [icon("search", { size: 13 })]),
      search,
      ...extra,
      sortButton,
      filterButton,
    ]);
    return {
      head,
      box,
      repaint: () => {
        paintButtons();
        drawBox();
      },
    };
  }

  // ------------------------------------------------------------ prompts

  /**
   * A question with a text answer, drawn by this app (KYO-250): a native dialog
   * cannot be read or answered by a check, and `window.prompt` is not available in a
   * sandboxed renderer. Resolves null on cancel.
   */
  function askText(question: string, initial: string): Promise<string | null> {
    return new Promise((resolve) => {
      const input = el("input", {
        class: "prompt-input",
        data: { region: "prompt-input" },
        attrs: { type: "text", value: initial },
      });
      input.value = initial;
      const finish = (answer: string | null): void => {
        overlay.remove();
        resolve(answer);
      };
      const overlay = el(
        "div",
        { class: "prompt-overlay", data: { region: "prompt", overlay: "prompt" } },
        [
          el("div", { class: "prompt-dialog" }, [
            el("div", { class: "prompt-question", text: question }),
            input,
            el("div", { class: "prompt-actions" }, [
              el("button", {
                class: "prompt-button",
                text: "Cancel",
                data: { region: "prompt-cancel" },
                attrs: { type: "button" },
                on: { click: () => finish(null) },
              }),
              el("button", {
                class: "prompt-button prompt-ok",
                text: "OK",
                data: { region: "prompt-ok" },
                attrs: { type: "button" },
                on: { click: () => finish(input.value) },
              }),
            ]),
          ]),
        ],
      );
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") finish(input.value);
        if (event.key === "Escape") finish(null);
      });
      document.body.append(overlay);
      input.focus();
      input.select();
    });
  }

  function askConfirm(question: string): Promise<boolean> {
    return new Promise((resolve) => {
      const finish = (answer: boolean): void => {
        overlay.remove();
        resolve(answer);
      };
      const overlay = el(
        "div",
        { class: "prompt-overlay", data: { region: "prompt", overlay: "prompt" } },
        [
          el("div", { class: "prompt-dialog" }, [
            el("div", { class: "prompt-question", text: question }),
            el("div", { class: "prompt-actions" }, [
              el("button", {
                class: "prompt-button",
                text: "Cancel",
                data: { region: "prompt-cancel" },
                attrs: { type: "button" },
                on: { click: () => finish(false) },
              }),
              el("button", {
                class: "prompt-button prompt-ok",
                text: "Delete",
                data: { region: "prompt-ok" },
                attrs: { type: "button" },
                on: { click: () => finish(true) },
              }),
            ]),
          ]),
        ],
      );
      document.body.append(overlay);
      overlay.querySelector<HTMLElement>('[data-region="prompt-ok"]')?.focus();
    });
  }

  /** A write's refusal, shown where the list is, verbatim. */
  function reportWrite(result: { ok: boolean; problem: string | null }): void {
    if (result.ok) return;
    const note = problem(result.problem ?? "the write was refused");
    note.dataset.region = "sidebar-write-problem";
    body.prepend(note);
    setTimeout(() => note.remove(), 8000);
  }

  // ------------------------------------------------------------- the ideas tree

  /** The folder header under a screen point, for a row being dragged over it. */
  function folderUnder(x: number, y: number): string | null {
    const under = document.elementFromPoint(x, y);
    if (!(under instanceof Element)) return null;
    const header = under.closest<HTMLElement>('[data-region="sidebar-folder"]');
    if (header === null || !root.contains(header)) return null;
    return header.dataset.folder ?? null;
  }

  function markFolderUnder(x: number, y: number): void {
    const folder = folderUnder(x, y);
    for (const header of root.querySelectorAll<HTMLElement>('[data-region="sidebar-folder"]')) {
      header.dataset.dropTarget = String(header.dataset.folder === folder && folder !== null);
    }
  }

  async function moveTo(path: string, folder: string): Promise<void> {
    reportWrite(await deps.bridge.moveIdea({ path, folder }));
    refresh();
  }

  // Every renderer below checks `current` after its read: the reader may have chosen
  // another list while the bridge was out, and a read that lands late must not paint
  // over the list they moved to (seen once as the docs rows under the "Awaiting you"
  // title, KYO-253).
  async function renderIdeas(): Promise<void> {
    // A vault that could not be read follows by rebuilding (the branch below is a
    // sentence, not a list); a vault that could is re-read in place, further down.
    reloadCurrent = renderIdeas;
    const tree = await deps.bridge.getIdeaTree();
    if (current !== "ideas") return;
    if (!tree.ok) {
      body.replaceChildren(problem(tree.refusal ?? "ideas/ could not be read"));
      return;
    }
    const state = stateOf("ideas");
    const search = searchBox("ideas-search", "Search name or folder");
    const results = el("div", { class: "results" });

    // Every openable file, once. `getIdeaTree` returns the WHOLE vault and the rows
    // carry everything the filter reads, so this list filters in the renderer rather
    // than re-asking the main process per keystroke: `ideas/` is a directory listing
    // this app already has in its hand.
    //
    // `let` rather than `const` since KYO-310: a file written under `ideas/` by
    // somebody else replaces these two under the reader, without rebuilding the
    // search box they are typing in or the folder rows they have folded.
    let files = tree.files.filter((file) => file.openable);
    let folders = tree.folders;
    const sorts: SortField<IdeaFileEntry>[] = [
      { id: "name", label: "Name", key: (f) => f.name },
      { id: "created", label: "Created", key: (f) => f.createdAt ?? "" },
      { id: "modified", label: "Modified", key: (f) => f.modifiedAt ?? "" },
    ];
    const filters: FilterField<IdeaFileEntry>[] = [
      {
        id: "folder",
        label: "Folder",
        value: (f) => f.folder,
        labelOf: (v) => (v === "" ? "(root)" : v),
      },
      {
        id: "promoted",
        label: "Promotion",
        value: (f) => (f.promoted.length > 0 ? "promoted" : "unpromoted"),
        order: ["unpromoted", "promoted"],
      },
    ];

    const newFolder = el("button", {
      class: "tool-button",
      data: { region: "ideas-new-folder" },
      tip: "New folder",
      attrs: { type: "button" },
    });
    newFolder.append(icon("folder-plus", { size: 14 }));
    newFolder.addEventListener("click", () => void createFolder(""));

    async function createFolder(parent: string): Promise<void> {
      const question = parent === "" ? "New folder in ideas/" : `New folder in ${parent}/`;
      const name = await askText(question, "");
      if (name === null || name.trim() === "") return;
      reportWrite(
        await deps.bridge.mkdirIdeas({ folder: parent === "" ? name : `${parent}/${name}` }),
      );
      refresh();
    }
    async function renameEntry(path: string, isFolder: boolean): Promise<void> {
      const base = path.split("/").at(-1) ?? path;
      const name = await askText(`Rename ${isFolder ? "folder" : "idea"} ${base}`, base);
      if (name === null || name.trim() === "" || name === base) return;
      reportWrite(await deps.bridge.renameIdea({ path, name }));
      refresh();
    }
    async function deleteEntry(path: string, isFolder: boolean): Promise<void> {
      const ok = await askConfirm(
        isFolder
          ? `Delete the folder ${path} and everything in it? This cannot be undone.`
          : `Delete ${path}? This cannot be undone.`,
      );
      if (!ok) return;
      reportWrite(await deps.bridge.deleteIdea({ path }));
      refresh();
    }
    async function createIdeaIn(folder: string): Promise<void> {
      const question = folder === "" ? "New idea in ideas/" : `New idea in ${folder}/`;
      const name = await askText(question, "");
      if (name === null || name.trim() === "") return;
      const result = await deps.bridge.createIdea({ folder, name, text: "" });
      reportWrite(result);
      refresh();
      if (result.ok) {
        const file = name.toLowerCase().endsWith(".md") ? name : `${name}.md`;
        const path = folder === "" ? file : `${folder}/${file}`;
        deps.columns.show({ kind: "compose", arg: `idea:${path}` });
      }
    }
    const moveItems = (path: string): MenuItem[] => {
      const here = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
      return folders
        .filter((folder) => folder !== here && folder !== path && !folder.startsWith(`${path}/`))
        .map((folder) => ({
          label: folder === "" ? "Move to ideas/ (root)" : `Move to ${folder}/`,
          run: () => void moveTo(path, folder),
        }));
    };

    const { head, box, repaint } = tools("ideas", search, sorts, filters, () => files, draw, [
      newFolder,
    ]);
    body.replaceChildren(head, box, results);

    function fileRow(file: IdeaFileEntry): HTMLElement {
      return row(
        { kind: "compose", arg: `idea:${file.path}` },
        file.name,
        "",
        file.promoted.length > 0 ? file.promoted.join(" ") : undefined,
        [
          { separator: true },
          { label: "Rename…", run: () => void renameEntry(file.path, false) },
          ...moveItems(file.path),
          { label: "Delete…", run: () => void deleteEntry(file.path, false) },
        ],
      );
    }

    function folderRow(folder: string, count: number, depth: number): HTMLElement {
      const collapsed = state.collapsed.includes(folder);
      const name = folder.split("/").at(-1) ?? folder;
      const header = el("div", {
        class: "folder-row",
        data: {
          region: "sidebar-folder",
          folder,
          collapsed: String(collapsed),
          depth: String(depth),
        },
        tip: `${folder}/ · ${count} ${count === 1 ? "file" : "files"}`,
      });
      header.append(
        el("span", { class: "folder-chevron" }, [
          icon(collapsed ? "forward" : "chevron-down", { size: 12 }),
        ]),
        el("span", { class: "folder-icon" }, [icon("folder", { size: 14 })]),
        el("span", { class: "folder-name", text: name }),
        el("span", { class: "folder-count", text: String(count) }),
      );
      header.addEventListener("click", () => {
        state.collapsed = collapsed
          ? state.collapsed.filter((f) => f !== folder)
          : [...state.collapsed, folder];
        draw();
        deps.onLayoutChanged();
      });
      header.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        showMenu(event, [
          { label: "New idea here…", run: () => void createIdeaIn(folder) },
          { label: "New folder here…", run: () => void createFolder(folder) },
          { separator: true },
          { label: "Rename…", run: () => void renameEntry(folder, true) },
          ...moveItems(folder),
          { label: "Delete…", run: () => void deleteEntry(folder, true) },
        ]);
      });
      return header;
    }

    function draw(): void {
      const query = search.value.trim().toLowerCase();
      const shown = arrange(
        query === ""
          ? files
          : files.filter((file) => `${file.path}\n${file.name}`.toLowerCase().includes(query)),
        state,
        sorts,
        filters,
      );
      if (shown.length === 0 && (query !== "" || files.length === 0)) {
        // Two different absences, said differently (Law 6): a vault with nothing in it
        // is not a search that matched nothing, and the remedy is different for each.
        results.replaceChildren(
          problem(
            files.length === 0
              ? "No idea files yet. ideas/ is the human's folder."
              : `No idea file matches “${search.value.trim()}”.`,
          ),
        );
        return;
      }
      // The tree (KYO-250): the root's own files, then each folder folded or not.
      // Folders are the vault's, from the scan -- an empty one still shows, because a
      // reader who just made it wants to see it.
      const byFolder = new Map<string, IdeaFileEntry[]>();
      for (const file of shown) {
        const list = byFolder.get(file.folder) ?? [];
        list.push(file);
        byFolder.set(file.folder, list);
      }
      const named = folders.filter((folder) => folder !== "");
      const filtering =
        query !== "" || Object.values(state.filters).some((kept) => kept.length > 0);
      const nodesOf = (parent: string, depth: number): HTMLElement[] => {
        const out: HTMLElement[] = [];
        const direct = named.filter((folder) => {
          if (parent === "") return !folder.includes("/");
          return folder.startsWith(`${parent}/`) && !folder.slice(parent.length + 1).includes("/");
        });
        for (const folder of direct) {
          const own = byFolder.get(folder) ?? [];
          const below = named.filter((f) => f.startsWith(`${folder}/`));
          const total = own.length + below.reduce((n, f) => n + (byFolder.get(f)?.length ?? 0), 0);
          if (filtering && total === 0) continue;
          out.push(folderRow(folder, total, depth));
          if (state.collapsed.includes(folder)) continue;
          const nest = el("div", {
            class: "folder-children",
            data: { region: "folder-children", folder },
          });
          // Folders before files at every level.
          for (const node of nodesOf(folder, depth + 1)) nest.append(node);
          for (const file of own) nest.append(fileRow(file));
          out.push(nest);
        }
        return out;
      };
      // The root's folders, a divider, then the root's own files.
      const folderNodes = nodesOf("", 0);
      const rootFiles = (byFolder.get("") ?? []).map(fileRow);
      results.replaceChildren(
        el("div", { class: "group", data: { group: "Ideas" } }, [
          el("div", { class: "group-head", text: `Ideas (${shown.length})` }),
          ...folderNodes,
          folderNodes.length > 0 && rootFiles.length > 0
            ? el("div", { class: "sidebar-divider", data: { region: "ideas-divider" } })
            : null,
          ...rootFiles,
        ]),
      );
    }

    search.addEventListener("input", draw);
    draw();
    // From here the list follows `ideas/` (KYO-310): a file written there by an
    // editor, an agent or `kyo promote` replaces the rows under the reader. The tools
    // and the search box are not rebuilt, so what they typed and folded stays.
    reloadCurrent = async (): Promise<void> => {
      const next = await deps.bridge.getIdeaTree();
      if (current !== "ideas" || !next.ok) return;
      files = next.files.filter((file) => file.openable);
      folders = next.folders;
      // The filter box is an answer about the rows that just arrived -- a folder made
      // outside the app is a value this list can now filter by, and a box left as it
      // was would offer the previous vault's folders.
      repaint();
      draw();
    };
  }

  // ------------------------------------------------------------ the records list

  async function renderRecords(): Promise<void> {
    const state = stateOf("records");
    const search = searchBox("records-search", "Search id or title");
    // No base dropdown beside the search (KYO-259): what it did is in the filter box
    // now -- kind, status and scope are the filter box's groups.
    const results = el("div", { class: "results" });
    interface Row {
      id: string;
      kind: string;
      title: string;
      status: string;
      standing: string | null;
      scope: string | null;
      createdAt: string;
      updatedAt: string;
      boardClass: BoardClass;
    }
    let loaded: Row[] = [];
    /** Why `kyo next` could not be read last time this list loaded (KYO-290.c2). */
    let boardProblem: string | null = null;
    const sorts: SortField<Row>[] = [
      { id: "name", label: "Id", key: (r) => r.id },
      { id: "title", label: "Title", key: (r) => r.title },
      { id: "created", label: "Created", key: (r) => r.createdAt },
      { id: "modified", label: "Modified", key: (r) => r.updatedAt },
    ];
    const filters: FilterField<Row>[] = [
      { id: "kind", label: "Kind", value: (r) => r.kind },
      // The role word, never the stored column: a decision stores `open` and reads
      // `current`, and an `open` in a listing has to mean dispatchable work (KYO-365).
      { id: "status", label: "Status", value: listingStatus },
      {
        id: "scope",
        label: "Scope",
        value: (r) => r.scope ?? "",
        labelOf: (v) => (v === "" ? "(no scope)" : v),
      },
      // Readiness (KYO-290): a filter for dispatchable work only, and
      // dispatchable is `kyo next`'s word -- so the value function reads the class the
      // main process stamped on the row and derives nothing. Every part of the group
      // that is not this list's business -- its id, its heading, the order, the
      // labels, the sentence for an unreadable board -- lives in `shared/protocol.ts`
      // beside `BOARD_CLASSES`, where a check can reach it without a window.
      {
        id: BOARD_FILTER_ID,
        label: BOARD_FILTER_LABEL,
        value: boardFilterValue,
        order: [...BOARD_FILTER_ORDER],
        labelOf: boardFilterLabel,
        offerAllValues: true,
        unavailable: () => (boardProblem === null ? null : boardFilterUnavailable(boardProblem)),
      },
    ];
    const { head, box, repaint } = tools("records", search, sorts, filters, () => loaded, draw);
    body.replaceChildren(head, box, results);

    function draw(): void {
      const shown = arrange(loaded, state, sorts, filters);
      // An empty list SAYS it is empty (KYO-306.c2). `group` alone renders a heading
      // reading "Records (0)" and nothing else, which is what the reader -- and the
      // check waiting for a row -- was given when a load came back with nothing: a
      // pane that looks exactly like one that was never asked.
      const line = emptyRecordsLine({
        loaded: loaded.length,
        shown: shown.length,
        query: search.value,
        filtering: narrowingNow(state, filters),
      });
      results.replaceChildren(
        group(
          "Records",
          shown.map((record) =>
            row({ kind: "record", arg: record.id }, record.id, record.title, record.kind),
          ),
        ),
        ...(line === null ? [] : [problem(line)]),
      );
    }

    /**
     * Re-ask for the rows. `quiet` is the store-driven re-read (KYO-310): no spinner
     * goes up in place of a list the reader is already reading.
     */
    const run = async (options: { quiet?: boolean } = {}): Promise<void> => {
      if (options.quiet !== true) results.replaceChildren(loading());
      const list = await deps.bridge.getRecordsList({ query: search.value });
      // The reader may have moved to another list while the bridge was out -- the
      // convention every renderer below states, and now also the one a re-read that
      // was started for a change and landed late has to keep.
      if (current !== "records") return;
      if (!list.ok) {
        // The list's name in front of the tool's own sentence: `database is locked`
        // arriving alone (KYO-306's cause) reads as a stray line of debris rather
        // than as this list's answer, and says nothing about which list failed.
        results.replaceChildren(
          problem(
            list.problem === null || list.problem === ""
              ? "the records list could not be read"
              : `the records list could not be read: ${list.problem}`,
          ),
        );
        return;
      }
      loaded = list.rows;
      boardProblem = list.boardProblem;
      // The dot and the box are both answers about the rows that just arrived -- and
      // about whether the board could be read at all, which is not knowable until now.
      repaint();
      // "Id" ascending keeps the store's own allocation order, since KYO-2 sorts
      // before KYO-10 under a numeric compare.
      draw();
    };

    search.addEventListener("input", () => void run());
    await run();
    // This list follows the store from here on (KYO-310): the search the reader typed
    // is re-asked with, because `search` is the same box they are still typing in.
    reloadCurrent = () => run({ quiet: true });
  }

  // ------------------------------------------------------------- awaiting, notes

  /**
   * Awaiting you (KYO-248): every record with an open gate waiting on a person, the
   * gate type on each row. Its own list rather than a group pinned atop the records
   * list, and a list rather than a full page: a list of records with the gate is
   * enough. Since KYO-257.c8 it is the only place for this query -- the full page
   * left the home page -- so each row carries the gate's instruction on hover.
   */
  async function renderAwaiting(): Promise<void> {
    // The whole of this list is its rows -- no search box, no tool boxes -- so its
    // re-read IS its render (KYO-310), and the scroll is restored by `followStore`.
    reloadCurrent = renderAwaiting;
    const view = await deps.bridge.getAwaitingHuman();
    if (current !== "awaiting") return;
    if (!view.ok) {
      body.replaceChildren(problem(view.problem ?? "the awaiting-human query failed"));
      return;
    }
    if (view.gates.length === 0) {
      body.replaceChildren(problem("Nothing is waiting on a person right now."));
      return;
    }
    body.replaceChildren(
      group(
        "Awaiting you",
        view.gates.map((entry) => {
          const node = row(
            { kind: "record", arg: entry.recordId },
            entry.recordId,
            entry.recordTitle,
            entry.gate.type,
          );
          // The gate's instruction on hover -- the one thing the home page's full
          // Awaiting view added over this list, which is why that entry could go
          // (KYO-257.c8): what to review, or the exact thing to run.
          if (entry.gate.detail !== null && entry.gate.detail !== "") {
            node.dataset.tip = `${entry.recordId} ${entry.recordTitle} — ${entry.gate.detail}`;
          }
          return node;
        }),
      ),
    );
  }

  /**
   * Notes (KYO-248), formerly Briefs: what a reader wants here is the shift notes,
   * the handoffs above all, rather than the briefs. So: every shift
   * note, newest first, the handoffs badged, and the latest brief kept reachable at
   * the top for the reader who wants the run it belongs to.
   */
  async function renderNotes(): Promise<void> {
    // As with Awaiting: rows and nothing else, so the render is the re-read (KYO-310).
    // A shift note written from the terminal pane is exactly the case -- the closing
    // note of a session lands while the reader is looking at this list.
    reloadCurrent = renderNotes;
    const catalogue = await deps.bridge.getShiftNotes();
    if (current !== "notes") return;
    if (!catalogue.ok) {
      // Named, for the reason `renderRecords` states: this list reads the same
      // database, and `database is locked` reached the screen from here too.
      body.replaceChildren(
        problem(
          catalogue.problem === null || catalogue.problem === ""
            ? "the shift notes could not be read"
            : `the shift notes could not be read: ${catalogue.problem}`,
        ),
      );
      return;
    }
    const rows = catalogue.notes.map((note) => {
      const first = note.text.split("\n").find((line) => line.trim() !== "") ?? "";
      const node = row(
        { kind: "views", arg: `shift-note:${note.id}` },
        note.id,
        note.redactedAt === null ? first : "[redacted]",
        note.handoffFor === null ? undefined : "handoff",
      );
      node.dataset.noteAt = note.createdAt;
      if (note.handoffFor !== null) node.dataset.handoffFor = note.handoffFor;
      node.dataset.tip = `${note.createdAt} · ${note.author}${note.handoffFor === null ? "" : ` · the handoff ${note.handoffFor} opened on`}`;
      return node;
    });
    body.replaceChildren(
      group("Latest", [row({ kind: "views", arg: "brief" }, "Latest brief", "", undefined)]),
      rows.length === 0 ? problem("This store holds no shift notes yet.") : group("Notes", rows),
    );
  }

  // ---------------------------------------------------------------- the docs list

  async function renderDocs(): Promise<void> {
    const tree = await deps.bridge.getDocTree();
    if (current !== "docs") return;
    if (!tree.ok) {
      body.replaceChildren(problem(tree.refusal ?? "the documents could not be read"));
      return;
    }
    if (tree.state === "no-documents") {
      // KYO-139.c6: "this project declares no documents" is a different absence from
      // "the read failed", and the sidebar says which.
      body.replaceChildren(problem(tree.says));
      return;
    }
    const state = stateOf("docs");
    const search = searchBox("docs-search", "Search title or path");
    const results = el("div", { class: "results" });
    type Doc = (typeof tree.documents)[number];
    const sorts: SortField<Doc>[] = [
      { id: "name", label: "Name", key: (d) => d.title ?? d.name },
      { id: "modified", label: "Modified", key: (d) => d.modifiedAt ?? "" },
    ];
    const filters: FilterField<Doc>[] = [
      {
        id: "staleness",
        label: "Staleness",
        value: (d) => d.staleness,
        order: ["stale", "cannot-tell", "current", "ungoverned"],
      },
      {
        id: "folder",
        label: "Folder",
        value: (d) => d.folder,
        labelOf: (v) => (v === "" ? "(root)" : v),
      },
    ];
    const { head, box } = tools("docs", search, sorts, filters, () => tree.documents, draw);
    body.replaceChildren(head, box, results);

    function draw(): void {
      const query = search.value.trim().toLowerCase();
      const shown = arrange(
        query === ""
          ? tree.documents
          : tree.documents.filter((d) =>
              `${d.path}\n${d.title ?? ""}\n${d.name}`.toLowerCase().includes(query),
            ),
        state,
        sorts,
        filters,
      );
      results.replaceChildren(
        group(
          "Documents",
          shown.map((doc) =>
            row(
              { kind: "views", arg: `doc:${doc.path}` },
              doc.title ?? doc.name,
              doc.folder,
              doc.staleness,
            ),
          ),
        ),
      );
    }
    search.addEventListener("input", draw);
    draw();
  }

  // ------------------------------------------------------------ activity (KYO-251)
  //
  // Each dispatch is at most two lines -- its id, model, time and line counts, with
  // links to the dispatch and its report -- then the records it worked on, indented.
  // Running dispatches come first, then a divider, then finished ones, and a running
  // dispatch gets a third line saying what it is doing. Every class a row is in is the
  // read model's own (`row.classes`); nothing is derived here.

  /** The activity list is live while it shows: the poll's pushes repaint it. */
  let activityLive: (() => void) | null = null;

  function idLink(id: string, label?: string): HTMLElement {
    return el("button", {
      class: "id-link activity-link",
      text: label ?? id,
      data: { linkId: id },
      attrs: { type: "button" },
      on: {
        click: (event) => {
          event.stopPropagation();
          deps.bridge.followLink(kyoseiUrlFor(id));
        },
      },
    });
  }

  function dispatchBlock(row: ActivityRow): HTMLElement {
    const running = row.state === "running";
    const terminated = row.classes.includes("terminated-no-report");
    const lines =
      row.git.linesAdded === null || row.git.linesRemoved === null
        ? null
        : `+${row.git.linesAdded} −${row.git.linesRemoved}`;
    const block = el("div", {
      class: "dispatch",
      data: {
        region: "activity-row",
        dispatchId: row.dispatchId,
        dispatchState: row.state,
        classes: row.classes.join(" "),
      },
      tip: `Open ${row.dispatchId}`,
    });
    // The whole block opens the dispatch, as its id link does: the id alone was not an
    // obvious target, so a click anywhere on the block's empty space opens it. What has a
    // click of its own inside keeps it: the id links stop their own event, and a record
    // row beneath opens that record rather than the dispatch.
    block.addEventListener("click", (event) => {
      const target = event.target;
      if (target instanceof Element && target.closest('button, [data-region="sidebar-row"]')) {
        return;
      }
      deps.bridge.followLink(kyoseiUrlFor(row.dispatchId));
    });
    // Line one: the dispatch, the model, what it has cost, the time.
    //
    // KYO-284 settled all three. The token figure is
    // `kyo`'s and is only RENDERED here (c4) -- "tokens unknown" whenever the CLI
    // could not match a transcript, with its own reason on the tip, because a
    // number that might be somebody else's worker is worse than no number. The
    // running row lost its " so far": the clock is running, which the row already
    // says by being in the Running group. And a finished row older than a day shows
    // the date it ran instead of how long it took, with the run time moved to line
    // two beside the diff counts.
    const first = el("div", { class: "dispatch-line" }, [
      idLink(row.dispatchId),
      el("span", { class: "dispatch-model", text: row.workerModel ?? "no model declared" }),
      el("span", {
        class: "dispatch-tokens",
        data: { region: "dispatch-tokens" },
        text: tokensLabel(row.workerTokens),
        tip: tokensTip(row.workerTokens),
      }),
      el("span", {
        class: "dispatch-time",
        data: { region: "dispatch-elapsed" },
        text: running ? elapsedFor(row) : finishedWhen(row),
        // KYO-286: the tip names the end time and which of kyo's three sources it
        // is, because a run time that used to grow is a figure a reader has to be
        // able to check rather than take.
        tip: whenTip(row),
      }),
    ]);
    // Line two: what came of it -- the report, the diff, the marks.
    const second = el("div", { class: "dispatch-line dispatch-second" });
    if (row.reportId !== null) {
      second.append(
        idLink(row.reportId),
        el("span", {
          class: "dispatch-verdicts",
          text: row.verdictCounts.map((v) => `${v.verdict} ${v.count}`).join(", "),
        }),
      );
    } else if (terminated) {
      second.append(
        el("span", {
          class: "row-badge dispatch-mark",
          data: { marker: "terminated-no-report" },
          text: "terminated, no report",
          tip: row.terminatedWhy ?? "",
        }),
      );
    } else {
      second.append(el("span", { class: "muted", text: "no report" }));
    }
    // The run time sits below the date the dispatch ran, left of the lines added and
    // deleted (KYO-284.c2). Finished rows only: a running row's line one IS its
    // run time, and printing it twice would read as two different clocks.
    if (!running) {
      second.append(
        el("span", {
          class: "dispatch-time dispatch-runtime",
          data: { region: "dispatch-runtime" },
          text: `ran ${elapsedFor(row)}`,
          tip: whenTip(row),
        }),
      );
    }
    if (lines !== null) {
      second.append(
        el("span", { class: "dispatch-lines", data: { region: "dispatch-lines" }, text: lines }),
      );
    }
    if (row.openGates > 0) {
      second.append(
        el("span", {
          class: "row-badge",
          data: { marker: "gated" },
          text: `${row.openGates} open ${row.openGates === 1 ? "gate" : "gates"}`,
        }),
      );
    }
    if (row.classes.includes("finished-but-not-moved")) {
      second.append(
        el("span", {
          class: "row-badge",
          data: { marker: "finished-but-not-moved" },
          text: "not moved",
        }),
      );
    }
    block.append(first, second);
    // Line three, running only, and only what the store can observe: the latest
    // commit's subject and whether the report file has appeared. Nothing invented.
    if (running) {
      const seen: string[] = [];
      if (row.git.latestCommitMessage !== null && (row.git.commitsPastBase ?? 0) > 0) {
        seen.push(`latest commit: ${row.git.latestCommitMessage}`);
      } else if (row.git.outcome === "read") {
        seen.push("no commits past base yet");
      }
      if (row.git.outcome === "read")
        seen.push(row.git.reportFilePresent ? "report file present" : "no report file yet");
      if (row.git.outcome === "directory-gone") seen.push("worktree is gone");
      if (seen.length > 0) {
        block.append(
          el("div", {
            class: "dispatch-line dispatch-doing",
            data: { region: "dispatch-doing" },
            text: seen.join(" · "),
          }),
        );
      }
    }
    // The records it worked on, indented: its own, and the ones it also covered.
    const records = el("div", { class: "dispatch-records", data: { region: "dispatch-records" } });
    for (const entry of [{ id: row.recordId, title: row.recordTitle }, ...row.covers]) {
      records.append(row_(entry.id, entry.title));
    }
    block.append(records);
    return block;
  }

  function row_(id: string, title: string): HTMLElement {
    return row({ kind: "record", arg: id }, id, title);
  }

  function paintActivity(view: ActivityView): void {
    if (!view.ok) {
      body.replaceChildren(problem(view.problem ?? "the activity could not be read"));
      return;
    }
    const running = view.rows.filter((row) => row.state === "running");
    const finished = view.rows.filter((row) => row.state !== "running");
    const runningGroup = el("div", { class: "group", data: { group: "running" } }, [
      el("div", { class: "group-head", text: `Running (${running.length})` }),
      ...(running.length === 0
        ? [el("div", { class: "sidebar-problem", text: "Nothing is running." })]
        : running.map(dispatchBlock)),
    ]);
    const finishedGroup = el("div", { class: "group", data: { group: "finished" } }, [
      el("div", { class: "group-head", text: `Finished (${finished.length} shown)` }),
      ...(finished.length === 0
        ? [el("div", { class: "sidebar-problem", text: "No finished dispatch yet." })]
        : finished.map(dispatchBlock)),
    ]);
    body.replaceChildren(
      runningGroup,
      el("div", { class: "sidebar-divider", data: { region: "activity-divider" } }),
      finishedGroup,
      el("div", { class: "sidebar-problem", text: `git read at ${view.readAt}` }),
    );
  }

  async function renderActivity(): Promise<void> {
    activityLive?.();
    activityLive = deps.bridge.onActivityChanged((view) => {
      if (current === "activity") paintActivity(view);
    });
    const view = await deps.bridge.getActivity();
    if (current === "activity") paintActivity(view);
  }

  const LISTS: Record<SidebarList, { title: string; render(): Promise<void> }> = {
    ideas: { title: "Ideas", render: renderIdeas },
    records: { title: "Records", render: renderRecords },
    awaiting: { title: "Awaiting you", render: renderAwaiting },
    notes: { title: "Notes", render: renderNotes },
    docs: { title: "Docs", render: renderDocs },
    activity: { title: "Activity", render: renderActivity },
  };

  function show(list: SidebarList | null): void {
    if (list !== "activity" && activityLive !== null) {
      activityLive();
      activityLive = null;
    }
    current = list;
    // Whatever the previous list left behind cannot re-read this one (KYO-310); the
    // render below installs this list's own, once it has something to re-read.
    reloadCurrent = null;
    root.dataset.open = String(list !== null);
    root.dataset.list = list ?? "";
    root.style.width = list === null ? "0px" : `${width}px`;
    if (list === null) {
      body.replaceChildren();
      title.textContent = "";
      // Stamped here as well as from the observer: replacing nothing with nothing
      // records no mutation, so a sidebar collapsed twice would keep the last list's
      // stamp and say a list is showing that is not.
      stampLoad();
      return;
    }
    title.textContent = LISTS[list].title;
    body.replaceChildren(loading());
    stampLoad();
    // The spinner is not an answer (KYO-306.c2). A load that has not come back is
    // still awaited -- a late answer paints over this -- but the reader, and any
    // check whose snapshot outlives the run, is told which list is waiting and for
    // how long, instead of being shown a list that merely looks empty.
    const asked = list;
    const slow = window.setTimeout(() => {
      if (current !== asked) return;
      const spinner = body.querySelector('[data-region="sidebar-loading"]');
      if (spinner === null) return;
      spinner.replaceWith(
        problem(
          `the ${LISTS[asked].title} list has not answered in ${Math.round(SLOW_LOAD_MS / 1000)}s; ` +
            "it is still being read",
        ),
      );
    }, SLOW_LOAD_MS);
    // The first load counts as a read in flight (KYO-310): a change arriving while a
    // list is still being drawn for the first time needs no second read, because the
    // read already out was started after that change and will carry it.
    reloadInFlight = true;
    void LISTS[list]
      .render()
      .catch((error: unknown) => {
        // A list whose read threw shows what the main process said, alone: a dev box
        // that went away is its refusal's own sentence -- host, port and what refused
        // (KYO-370.c5) -- never behind Electron's "Error invoking remote method…" frame
        // or the refusal's class name (KYO-389).
        body.replaceChildren(problem(saidBy(error)));
      })
      .finally(() => {
        window.clearTimeout(slow);
        reloadInFlight = false;
      });
  }

  function refresh(): void {
    if (current !== null) show(current);
  }

  /**
   * The store moved: re-read the showing list under the reader (KYO-310.c1).
   *
   * The decision is `storeReloadDue`'s, in `shared/protocol.ts`, so that "a list that
   * is not showing costs nothing" is checkable without a window. What is left here is
   * the doing of it, and the two things the reader must not lose:
   *
   *   THE SCROLL POSITION. `.sidebar-body` is the scrolling element; the rows inside
   *   it are replaced, so the browser would otherwise put the reader back at the top.
   *   Read before and written after, rather than restored by a frame callback, so a
   *   list that grew above the reader's position does not jump first and settle after.
   *   THE SPINNER. A re-read is QUIET: no `loading()` goes up. A "…" flashing in place
   *   of a list somebody is reading, every time a controller files a record, is the
   *   opposite of following the store.
   *
   * The sort, the filters and the folded folders survive because they live in
   * `states` rather than in the DOM, the search text survives because the box is not
   * rebuilt, and the open marks survive because `row()` marks every row it makes from
   * the columns' own answer.
   */
  function followStore(change: StoreChange): void {
    // The server behind this window went away or came back (KYO-378.c4): the list is
    // shown again as a first load shows it, so a read that fails says so here, naming
    // where the server was, and one that succeeds draws the rows again.
    if (change.kind === "server") {
      if (current !== null) show(current);
      return;
    }
    if (!storeReloadDue({ showing: current, changed: change.kind, inFlight: reloadInFlight })) {
      return;
    }
    const reload = reloadCurrent;
    if (reload === null) return;
    const asked = current;
    const top = body.scrollTop;
    reloadInFlight = true;
    void reload()
      .catch((error: unknown) => {
        // A failed re-read leaves what the reader is looking at alone. The rows on
        // screen are the last answer the store gave, which is a better thing to be
        // shown than an error where a list used to be -- and the next change, or the
        // next time this list is shown, asks again.
        console.error("kyosei: the sidebar could not follow the store", error);
      })
      .finally(() => {
        reloadInFlight = false;
        if (current !== asked) return;
        body.scrollTop = top;
        storeReloads += 1;
        root.dataset.storeReloads = String(storeReloads);
      });
  }

  // ------------------------------------------------------- open marks (KYO-278)
  //
  // A record, idea or awaiting row whose tab is open is highlighted in the sidebar, so
  // the reader can see that it is open. A row is marked open
  // when a tab with its kind and arg is open anywhere in this window, and marked
  // active when that tab is the active column's active one -- the place a plain click
  // lands. Marked at creation from the columns' own answer, and re-walked whenever the
  // layout changes, so the list never keeps a second idea of what is open.

  function markRow(node: HTMLElement, refs: OpenRef[]): void {
    const kind = node.dataset.rowKind ?? "";
    const arg = node.dataset.rowArg ?? "";
    const match = refs.filter((ref) => ref.kind === kind && ref.arg === arg);
    if (match.length === 0) {
      delete node.dataset.open;
      delete node.dataset.openActive;
      return;
    }
    node.dataset.open = "true";
    node.dataset.openActive = String(match.some((ref) => ref.active));
  }

  function markOpen(): void {
    const refs = deps.columns.openRefs();
    for (const node of body.querySelectorAll<HTMLElement>('[data-region="sidebar-row"]')) {
      markRow(node, refs);
    }
  }

  // ------------------------------------------------------------------ the grip

  grip.addEventListener("pointerdown", (event) => {
    if (current === null) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (moved: PointerEvent): void => {
      width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + (moved.clientX - startX)));
      root.style.width = `${width}px`;
    };
    const up = (): void => {
      window.removeEventListener("pointermove", move);
      deps.onLayoutChanged();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  });

  return {
    root,
    show,
    showing: () => current,
    width: () => width,
    setWidth(next) {
      width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, next));
      if (current !== null) root.style.width = `${width}px`;
    },
    refresh,
    followStore,
    markOpen,
    listStates: () =>
      Object.fromEntries([...states.entries()].map(([k, v]) => [k, structuredClone(v)])),
    setListStates(next) {
      states.clear();
      for (const [list, raw] of Object.entries(next ?? {})) states.set(list, asState(raw));
    },
  };
}

/** Icons this file names, so the shell's set is known to carry them. */
export const SIDEBAR_ICONS: IconName[] = [
  "sort",
  "funnel",
  "folder",
  "folder-plus",
  "chevron-down",
  "sidebar-left",
];
