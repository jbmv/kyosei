// The left nav rail (KYO-227, KYO-232.c2 and .c6; reworked by KYO-248).
//
// Six LISTS in a 48px column -- ideas, records, awaiting, notes, docs, activity --
// and the project switcher at the foot. Nothing on the rail opens a tab any more: the things
// that used to (scratchpad, activity, terminal, claude) live on the home page and in
// the View menu, so the rail reads as one kind of thing rather than a mix of list
// items and tab-opening items. Awaiting carries a live badge -- how many gates wait on a
// person -- from the read model (KYO-231), pushed by the main process when it moves.
//
// THE RAIL IS STILL NOT ONE LIST. A tab kind may declare a `rail` entry in its own
// directory (`tabs/kind.ts`) and it is merged in and sorted by `order`; no kind does
// today, and a project that adds one is adding a tab-opening icon back on purpose.

import { el } from "../dom";
import { type IconName, icon } from "../icons";
import type { BadgeSource } from "../tabs/kind";
import { tabKinds } from "../tabs/registry";
import type { TabRef } from "./columns";

/** The sidebar lists the shell itself draws. */
export const SIDEBAR_LISTS = ["ideas", "records", "awaiting", "notes", "docs", "activity"] as const;
export type SidebarList = (typeof SIDEBAR_LISTS)[number];

export interface RailItem {
  id: string;
  order: number;
  label: string;
  icon: IconName;
  badge?: BadgeSource;
  /** Clicking opens this tab straight away -- there is no list to show. */
  opens?: TabRef;
  /** Clicking selects this sidebar list; clicking again collapses the sidebar. */
  list?: SidebarList;
}

/** The shell's own rail items: the sidebar lists, in their fixed order. */
const SHELL_ITEMS: RailItem[] = [
  { id: "ideas", order: 20, label: "Ideas", icon: "folder", list: "ideas" },
  { id: "records", order: 30, label: "Records", icon: "list", list: "records" },
  {
    id: "awaiting",
    order: 35,
    label: "Awaiting you",
    icon: "list",
    list: "awaiting",
    badge: "awaiting-human",
  },
  { id: "notes", order: 40, label: "Notes", icon: "calendar", list: "notes" },
  { id: "docs", order: 50, label: "Docs", icon: "doc", list: "docs" },
  // Activity is a LIST too (KYO-251): the sidebar shows dispatch information. The
  // running-dispatch count that was the activity tab's badge lives here.
  {
    id: "activity",
    order: 60,
    label: "Activity",
    icon: "pulse",
    list: "activity",
    badge: "running-dispatches",
  },
];

/** Every rail item: the shell's, plus one per tab kind that declares an entry. */
export function railItems(): RailItem[] {
  const contributed: RailItem[] = [];
  for (const kind of tabKinds()) {
    const entry = kind.rail;
    if (entry === undefined) continue;
    const item: RailItem = {
      id: kind.kind,
      order: entry.order,
      label: entry.label,
      icon: entry.icon,
      opens: { kind: kind.kind, arg: entry.arg ?? "" },
    };
    if (entry.badge !== undefined) item.badge = entry.badge;
    contributed.push(item);
  }
  return [...SHELL_ITEMS, ...contributed].sort(
    (a, b) => a.order - b.order || a.id.localeCompare(b.id),
  );
}

export interface RailDeps {
  onSelect(item: RailItem): void;
  /** The event is the click that opened it -- `showMenu` anchors on its coordinates. */
  onSwitchProject(event: MouseEvent): void;
}

export interface Rail {
  root: HTMLElement;
  /** Which list is showing, or null when the sidebar is collapsed. */
  setSelected(listId: string | null): void;
  setBadge(source: BadgeSource, count: number): void;
  /**
   * The project switcher's caption: the project the app is reading, and -- for a
   * dev box's project (KYO-374.c3) -- the host it lives on, shown whole on a line of
   * its own rather than truncated after the prefix.
   */
  setProject(label: string, tip: string, host?: string | null): void;
  /** What the switcher says right now. */
  projectLabel(): string;
}

export function buildRail(deps: RailDeps): Rail {
  const items = railItems();
  const buttons = new Map<string, HTMLElement>();
  const badges = new Map<string, HTMLElement>();
  const top = el("div", { class: "rail-top" });

  for (const item of items) {
    const badge = el("span", {
      class: "badge",
      data: { region: "rail-badge", badgeFor: item.id, count: "0" },
      text: "",
    });
    badge.hidden = true;
    const button = el("button", {
      class: "rail-item",
      data: { region: "rail-item", railItem: item.id, selected: "false" },
      tip: item.label,
      on: { click: () => deps.onSelect(item) },
    });
    button.append(icon(item.icon), badge, el("span", { class: "rail-label", text: item.label }));
    buttons.set(item.id, button);
    if (item.badge !== undefined) badges.set(item.badge, badge);
    top.append(button);
  }

  const project = el("button", {
    class: "rail-item rail-project",
    data: { region: "project-switcher" },
    tip: "Switch projects",
    on: { click: (event) => deps.onSwitchProject(event) },
  });
  const projectName = el("span", { class: "rail-label", data: { region: "project-name" } });
  const projectHost = el("span", { class: "rail-host", data: { region: "project-host" } });
  projectHost.hidden = true;
  project.append(icon("project"), projectName, projectHost);

  const root = el("nav", { class: "rail", data: { region: "rail" } }, [
    // Where macOS draws the traffic lights; the window's drag handle on this side.
    el("div", { class: "rail-lights", data: { region: "traffic-lights", dragRegion: "" } }),
    top,
    el("div", { class: "rail-foot" }, [project]),
  ]);

  return {
    root,
    setSelected(listId) {
      for (const [id, button] of buttons) {
        button.dataset.selected = String(id === listId);
      }
    },
    setBadge(source, count) {
      const badge = badges.get(source);
      if (badge === undefined) return;
      badge.textContent = String(count);
      badge.dataset.count = String(count);
      // A badge showing `0` is noise the reader has to read to dismiss; an absent one
      // is the same fact with nothing to read (law 6's spirit -- the absence states
      // itself, and the count is still on the attribute for anything asking).
      badge.hidden = count === 0;
    },
    setProject(label, tip, host = null) {
      projectName.textContent = label;
      projectHost.textContent = host ?? "";
      projectHost.hidden = host === null || host === "";
      project.setAttribute("data-tip", tip);
      project.dataset.project = label;
    },
    projectLabel: () => projectName.textContent ?? "",
  };
}
