// ============================================================================
// THE LINK RENDERER -- one module, every surface that renders text (KYO-233.c3).
// ============================================================================
//
// `[[KYO-12.g1]]` written in a record's description, a brief's prose, a doc, an
// idea or the scratchpad is a LINK, and a `kyosei://` URL written anywhere is the
// same link spelled in full. This module is the only place in the renderer that
// turns either of them into something clickable, and the reason it is shared is
// that KYO-233 and KYO-235 both render prose: the record page, and the briefs and
// docs tabs. Two copies of this would be two answers to "what is a link".
//
// ------------------------------------------------------------------ what it exports
//
//   linkedText(text, surface)        a DocumentFragment: text with links in it
//   linkedProse(text, surface, ...)  that fragment inside a `.prose` block
//   idLink(id, surface, ...)         one element for an id you already have
//   projectName(bridge)              the project a bare `[[id]]` belongs to
//   urlFor(project, id, column?)     `kyosei://<project>/<id>[?column=]`
//   LINK_PATTERN                     what counts as a link in rendered text
//   onOpenRequest / latestOpenRequest    the delivery feed (see below)
//
// ------------------------------------------------------------- what it must not do
//
// IT NEVER PARSES AN ID. `main/links.ts` owns the id grammar (KYO-230) and is the
// only thing in this app that knows what `.g1` means. What this module matches is
// BRACKET syntax -- `[[` ... `]]` -- and the URL SCHEME, neither of which is an id
// shape; what is between the brackets is handed to `followLink` verbatim and the
// main process decides whether it is an id at all, refusing with prose if it is
// not (law 6: a `kyosei://` link never silently does nothing).
//
// IT NEVER NAMES THE PROJECT ITSELF. A bare `[[id]]` has to become a full URL to be
// followed, and the `<project>` in it comes from `getFacts().project.project` --
// the store's own prefix, read over the bridge -- never from slicing the id at its
// dash. `onProjectChanged` clears the cache, because the app can be pointed at
// another project while a page is open (KYO-175).
//
// ------------------------------------------------------- the menu, and why `column`
//
// Right-click offers "Open in a new tab" and the left/right column (KYO-227, KYO-282),
// and both go through the SAME `followLink` call as a plain click, differing only
// by the `?column=` query parameter the main process already reads
// (`parseKyoseiUrl`, `OPEN_COLUMNS`). So a link's three behaviours are one code
// path with one parameter, and the renderer still has no way to open a tab from a
// URL on its own.
//
// The menu itself is the shell's (`shell/columns.ts`'s `showMenu`), which is an
// import and not an edit: that module says it holds "one menu for the whole shell",
// and a second menu implementation here would be a second look, a second dismissal
// rule, and a second thing for a check to find. The import closes a cycle
// (columns -> tabs/registry -> tabs/record -> links -> columns); it is safe because
// nothing here calls into it while modules are still evaluating -- `showMenu` is
// reached from a right-click, long after the bundle has finished loading.

import type { KyoseiBridge, OpenColumn, OpenRequest } from "../../shared/protocol";
import { el } from "../dom";
import { showMenu } from "../shell/columns";
import { ensureLinkStyles } from "./styles";

/** The scheme `main/links.ts` owns. Spelled once here so nothing else spells it. */
export const URL_SCHEME = "kyosei";

/**
 * What counts as a link in rendered text.
 *
 * Two alternatives and neither is an id pattern:
 *
 *   `[[ ... ]]`   bracket syntax, no newline and no `]` inside, so an unclosed
 *                 `[[` at the end of a paragraph does not swallow the rest of the
 *                 document looking for its partner.
 *   `kyosei://…`  the scheme, up to the first character that cannot be in a URL as
 *                 a human writes one in prose. Trailing `.`, `,`, `)` and `]` are
 *                 trimmed after the match (see `trimTrailing`) because a URL at the
 *                 end of a sentence is followed by punctuation that is not part of
 *                 it.
 */
export const LINK_PATTERN = new RegExp(`\\[\\[([^\\]\\n]+)\\]\\]|${URL_SCHEME}://[^\\s<>"']+`, "g");

/** Punctuation a sentence puts after a URL that is not part of the URL. */
function trimTrailing(url: string): string {
  return url.replace(/[.,;:!?)\]}]+$/, "");
}

/**
 * What a link needs from the surface drawing it.
 *
 * A `TabContext` satisfies this structurally, which is the point: a tab passes
 * itself. Nothing here can open a tab -- `followLink` hands a string to the main
 * process and the answer comes back over `onOpenTarget` -- so a link rendered into
 * a doc has exactly the capability a link clicked in the claude view has.
 */
export interface LinkSurface {
  bridge: KyoseiBridge;
  /** `newTab` is the Cmd-click (KYO-247): open beside, rather than here. */
  followLink(url: string, options?: { newTab?: boolean }): void;
}

// ------------------------------------------------------------------ the project

let projectCache: Promise<string | null> | null = null;

/**
 * Which project a bare `[[id]]` on this screen belongs to: the open one.
 *
 * Cached, because a page of prose can hold fifty links and each one would otherwise
 * be a subprocess round trip. Cleared when the app changes project.
 */
export function projectName(bridge: KyoseiBridge): Promise<string | null> {
  projectCache ??= bridge
    .getFacts()
    .then((facts) => facts.project.project)
    .catch(() => null);
  return projectCache;
}

/** `kyosei://<project>/<id>`, with the column ask when there is one. */
export function urlFor(project: string, id: string, column?: OpenColumn): string {
  const url = new URL(`${URL_SCHEME}://${encodeURIComponent(project)}/${encodeURIComponent(id)}`);
  if (column !== undefined && column !== "active") url.searchParams.set("column", column);
  return url.toString();
}

/**
 * The same ask applied to a URL the text already spelled out in full.
 *
 * `active` sets nothing: it is the default the main process reads for a link that
 * carries no `column` at all, so adding it would make a plain click's URL differ
 * from the link as it was written for no difference in what happens.
 */
function withColumn(url: string, column: OpenColumn): string {
  if (column === "active") return url;
  try {
    const parsed = new URL(url);
    parsed.searchParams.set("column", column);
    return parsed.toString();
  } catch {
    // Not a URL this renderer can re-spell. Hand the original over unchanged and let
    // the main process refuse it by name, which is what it does with every other
    // malformed link (`parseKyoseiUrl`'s `malformed`).
    return url;
  }
}

// ------------------------------------------------------------------ one link

export interface LinkOptions {
  /** Extra classes on the element, for a surface that styles its own links. */
  class?: string;
  /** What the element reads as, when it should not read as the id itself. */
  label?: string;
}

/**
 * One clickable link.
 *
 * A `<span role="link">` and not an `<a href>`: an anchor with a `kyosei://` href
 * would put a navigation in front of Chromium, and this app's window must never
 * navigate away from its own document. The element carries what it is for a check
 * and for a human reading the DOM: `data-link-written` is what the text said,
 * `data-link-url` is what will be followed -- filled in when the project answers,
 * so a check can wait for it rather than guess the prefix.
 */
export function idLink(
  written: string,
  surface: LinkSurface,
  options: LinkOptions = {},
): HTMLElement {
  ensureLinkStyles();
  const spelledOut = written.toLowerCase().startsWith(`${URL_SCHEME}:`);
  const node = el("span", {
    class: `link${options.class === undefined ? "" : ` ${options.class}`}`,
    text: options.label ?? written,
    tip: spelledOut ? written : `Open ${written}`,
    data: { region: "link", linkWritten: written },
    attrs: { role: "link", tabindex: "0" },
  });

  /** The URL for this link, at the column asked for. Null when nothing can be built. */
  const resolve = async (column: OpenColumn): Promise<string | null> => {
    if (spelledOut) return withColumn(written, column);
    const project = await projectName(surface.bridge);
    if (project === null) return null;
    return urlFor(project, written, column);
  };

  // Written onto the element as soon as it is known, so the DOM says what a click
  // would do rather than only doing it.
  void resolve("active").then((url) => {
    if (url !== null) node.dataset.linkUrl = url;
  });

  const follow = (column: OpenColumn, newTab = false): void => {
    void resolve(column).then((url) => {
      if (url === null) {
        node.dataset.linkProblem = "this app has no project open, so it cannot follow a link";
        return;
      }
      surface.followLink(url, { newTab });
    });
  };

  node.addEventListener("click", (event) => follow("active", event.metaKey || event.ctrlKey));
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      follow("active");
    }
  });
  node.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    showMenu(event, [
      { label: "Open in a new tab", run: () => follow("new") },
      // Named columns (KYO-282), the same two entries a sidebar row offers.
      { label: "Open in the left column", run: () => follow("left") },
      { label: "Open in the right column", run: () => follow("right") },
    ]);
  });
  return node;
}

// ------------------------------------------------------------------ text with links

/**
 * Rendered text, with every `[[id]]` and `kyosei://` occurrence clickable.
 *
 * Returns a fragment of text nodes and link elements, so the caller decides what
 * box it goes in -- a `pre-wrap` prose block, a table cell, a heading. The text
 * between links is left as TEXT NODES and never as markup: nothing here interprets
 * anything else in the string, so a record's description cannot smuggle HTML into
 * this window.
 */
export function linkedText(text: string, surface: LinkSurface): DocumentFragment {
  const fragment = document.createDocumentFragment();
  let at = 0;
  // A fresh regex per call: `LINK_PATTERN` is a `/g` regex, whose `lastIndex` is
  // state, and this function is called once per field on a page of dozens.
  const pattern = new RegExp(LINK_PATTERN.source, "g");
  for (let match = pattern.exec(text); match !== null; match = pattern.exec(text)) {
    const whole = match[0];
    const inner = match[1];
    // A spelled-out URL may have taken the sentence's punctuation with it; give it
    // back to the text rather than to the link.
    const written = inner ?? trimTrailing(whole);
    const consumed = inner === undefined ? written.length : whole.length;
    if (match.index > at) fragment.append(text.slice(at, match.index));
    fragment.append(idLink(written, surface));
    at = match.index + consumed;
    pattern.lastIndex = at;
  }
  if (at < text.length) fragment.append(text.slice(at));
  return fragment;
}

export interface ProseOptions {
  class?: string;
  data?: Record<string, string | number>;
}

/** `linkedText` in a `pre-wrap` block: how every multi-line field is rendered. */
export function linkedProse(
  text: string,
  surface: LinkSurface,
  options: ProseOptions = {},
): HTMLElement {
  const node = el("div", {
    class: `prose${options.class === undefined ? "" : ` ${options.class}`}`,
    data: options.data,
  });
  node.append(linkedText(text, surface));
  return node;
}

// ============================================================ the delivery feed
//
// `bridge.onOpenTarget` is how a followed link comes back (KYO-230), and the shell
// subscribes to it to open the tab. A TAB also needs it -- KYO-233.c2's "scrolls to
// and highlights the anchored child" is a thing only the record page can do -- and
// that is what this feed is for.
//
// Why it is a module-level subscription rather than one taken out per tab: the
// delivery that OPENS a record tab is dispatched to listeners that were registered
// before it fired, and the tab's own `open()` runs inside that dispatch (the shell
// calls `columns.open` from its handler). `ipcRenderer` is a Node `EventEmitter`,
// which snapshots its listener array before calling any of them, so a listener the
// tab adds while being opened would never hear the delivery that opened it. This
// module is imported through the tab registry, so it subscribes when the bundle
// loads -- before any delivery exists -- and holds the latest one for a tab that is
// painting because of it.
//
// It is also why `latest()` exists beside the subscription: whichever of the shell
// and this module is called first, the record page reads `latestOpenRequest()` when
// its document lands, which is a microtask later, by which time both have run.

/** The most recent successful delivery, with a sequence so a tab can tell it apart. */
export interface SeenRequest {
  seq: number;
  request: OpenRequest;
}

let seen: SeenRequest | null = null;
let seq = 0;
const listeners = new Set<(seen: SeenRequest) => void>();

function startFeed(): void {
  const bridge: KyoseiBridge | undefined =
    typeof window === "undefined" ? undefined : window.kyosei;
  if (bridge === undefined) return;
  bridge.onOpenTarget((delivery) => {
    if (!delivery.ok) return;
    seq += 1;
    seen = { seq, request: delivery.request };
    for (const listener of [...listeners]) listener(seen);
  });
  bridge.onProjectChanged(() => {
    projectCache = null;
  });
}

startFeed();

/** The most recent link delivery this window received, or null for none yet. */
export function latestOpenRequest(): SeenRequest | null {
  return seen;
}

/** Hear about link deliveries from now on. Returns the unsubscribe. */
export function onOpenRequest(listener: (seen: SeenRequest) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
