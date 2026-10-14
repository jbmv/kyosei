// The home page: what a new tab, or an empty column, offers to open (KYO-246).
//
// The alternative to a row of buttons above the strip: every new tab page lists what
// can be opened in the tab. So the things
// that used to be rail icons that opened tabs -- the scratchpad, the terminal, the
// claude session, the activity view -- are entries here, and so is anything else a
// kind wants offered.
//
// WHAT IS LISTED IS DECLARED BY THE KINDS, not here. `TabKind.home` (`tabs/kind.ts`)
// is one or more `HomeEntry`s per kind, merged and sorted by `order` -- the same
// property the rail has: a directory that wants to be on the home page says so from
// inside the directory, and this file is not the registry of what the page holds.
//
// One rendering with two mounts: the `home` tab kind draws it as a tab (Cmd+T, the
// strip's "+"), and `columns.ts` draws it inline as the empty column's body, so a
// column with nothing in it is already the page a reader would press "+" to reach.

import { el } from "../dom";
import { icon } from "../icons";
import type { HomeEntry } from "../tabs/kind";
import { tabKinds } from "../tabs/registry";
import type { TabRef } from "./columns";

export interface HomeItem extends HomeEntry {
  ref: TabRef;
}

/** Every home entry any kind declares, in `order`. */
export function homeItems(): HomeItem[] {
  const items: HomeItem[] = [];
  for (const kind of tabKinds()) {
    for (const entry of kind.home ?? []) {
      items.push({ ...entry, ref: { kind: kind.kind, arg: entry.arg ?? "" } });
    }
  }
  return items.sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
}

export interface HomeDeps {
  /** The reader chose an entry: open it where this page lives. */
  open(ref: TabRef, event: MouseEvent): void;
}

/** The page itself. Same markup whether it is a tab body or an empty column. */
export function renderHome(deps: HomeDeps): HTMLElement {
  const grid = el("div", { class: "home-grid", data: { region: "home-grid" } });
  for (const item of homeItems()) {
    const card = el(
      "button",
      {
        class: "home-card",
        data: { region: "home-card", homeKind: item.ref.kind, homeArg: item.ref.arg },
        tip: item.detail,
        attrs: { type: "button" },
        on: { click: (event) => deps.open(item.ref, event) },
      },
      [
        el("span", { class: "home-card-icon" }, [icon(item.icon, { size: 20 })]),
        el("span", { class: "home-card-text" }, [
          el("span", { class: "home-card-label", text: item.label }),
          el("span", { class: "home-card-detail", text: item.detail }),
        ]),
      ],
    );
    grid.append(card);
  }
  return el("div", { class: "home", data: { region: "home" } }, [
    el("div", { class: "home-head" }, [
      el("h1", { class: "home-title", text: "Open something" }),
      el("p", {
        class: "home-hint",
        text: "Or pick an idea, record, note or document from the lists on the left.",
      }),
    ]),
    grid,
  ]);
}
