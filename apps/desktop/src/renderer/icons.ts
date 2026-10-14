// The shell's icon set: one 24-box stroke path per name, drawn at the rail's weight.
//
// Kept as path data in one file rather than as files on disk because the renderer's
// CSP allows no remote origins and an `<img>` per icon would be a build step for
// nothing. A tab kind names one of these in its `rail.icon` (`tabs/kind.ts`), so this
// list is part of the tab interface's vocabulary -- adding an icon here is the one
// shared edit a new tab kind can need, and it is additive.

const PATHS = {
  /** The scratchpad: a pencil. */
  pencil: '<path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"></path>',
  /** Ideas: the human's own folder. */
  folder:
    '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>',
  /** Records: a list. */
  list: '<path d="M4 6h16M4 12h16M4 18h10"></path>',
  /** Briefs: the session's own sheet. */
  calendar:
    '<rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M16 2v4M8 2v4M3 10h18"></path>',
  /** Docs: a page. */
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"></path><path d="M14 3v5h5M9 13h6M9 17h4"></path>',
  /** Activity: a running trace. */
  pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"></path>',
  terminal: '<path d="M4 17l6-5-6-5M12 19h8"></path>',
  /** The claude session: a conversation. */
  chat: '<path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6a8 8 0 0 1 8-9h2a8 8 0 0 1 8 8z"></path>',
  /** The project switcher, at the foot of the rail. */
  project:
    '<rect x="3" y="3" width="18" height="18" rx="3"></rect><path d="M8 12h8M13 9l3 3-3 3"></path>',
  /** Split: add a column. */
  split: '<rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M12 4v16"></path>',
  /** Unsplit: back to one column (KYO-246). */
  unsplit: '<rect x="3" y="4" width="18" height="16" rx="2"></rect>',
  /** Hide the sidebar: a panel with its left side folding away (KYO-257). */
  "sidebar-left":
    '<rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M9 4v16M16 10l-2 2 2 2"></path>',
  /** The sidebar's sort and filter boxes, a new folder, a folded folder (KYO-250). */
  sort: '<path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"></path>',
  funnel: '<path d="M3 5h18l-7 8v6l-4 2v-8z"></path>',
  "folder-plus":
    '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><path d="M12 10v6M9 13h6"></path>',
  "chevron-down": '<path d="M6 9l6 6 6-6"></path>',
  /** Back and forward through a tab's history (KYO-247). */
  back: '<path d="M15 6l-6 6 6 6"></path>',
  forward: '<path d="M9 6l6 6-6 6"></path>',
  /** Close a tab, or dismiss a menu. */
  close: '<path d="M6 6l12 12M18 6L6 18"></path>',
  /** Move this tab to its own window. */
  popout:
    '<path d="M14 4h6v6M20 4l-8 8M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"></path>',
  /** The sidebar's search field. */
  search: '<circle cx="11" cy="11" r="7"></circle><path d="M20 20l-4-4"></path>',
  /** A window in the window cluster. */
  window: '<rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M3 9h18"></path>',
  plus: '<path d="M12 5v14M5 12h14"></path>',
  /** Anything a kind names that this build does not have: a visible unknown. */
  unknown: '<circle cx="12" cy="12" r="8"></circle><path d="M12 8v5M12 16h.01"></path>',
} as const;

export type IconName = keyof typeof PATHS;

export function icon(name: IconName, options: { size?: number; width?: number } = {}): SVGElement {
  const size = options.size ?? 18;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", String(options.width ?? 1.7));
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  // Law 6 in one line: a kind that names an icon this build does not have draws the
  // `unknown` glyph rather than an empty box, so the rail says something is wrong.
  svg.innerHTML = PATHS[name] ?? PATHS.unknown;
  return svg;
}
